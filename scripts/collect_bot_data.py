#!/usr/bin/env python3
"""Collect expert demonstrations by running the ScriptedBot against OpenRA's built-in AI.

Usage:
    # Start the OpenRA-RL server first:
    docker run -p 8000:8000 openra-rl

    # Collect 10 episodes, each up to 15 minutes (default):
    python scripts/collect_bot_data.py --episodes 10

    # Use explicit time limit:
    python scripts/collect_bot_data.py --episodes 10 --max-minutes 20

    # Quick test (2 minutes per episode):
    python scripts/collect_bot_data.py --episodes 2 --max-minutes 2

Output:
    data/episodes/episode_001.json  — list of {observation, action, reward} dicts
    data/episodes/episode_002.json
    ...

Notes:
    - The server runs at ~23 steps/second, so 15 min ≈ 20,000 steps.
    - The ScriptedBot builds a full base by ~1400 steps, then marches to the enemy.
      You need 10+ minutes to see actual combat on a 128x128 map.
    - Episodes stop early if the game ends (win/lose) before the time limit.
    - explored_percent is not in the raw step() observation (computed by MCP tool only).
"""

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

# Add the OpenRA-RL repo to path so we can import the client and scripted bot
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "OpenRA-RL"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "OpenRA-RL" / "examples"))

from openra_env.client import OpenRAEnv
from openra_env.models import OpenRAAction, OpenRAObservation, CommandModel, ActionType
from scripted_bot import ScriptedBot


class PeriodicAttackBot(ScriptedBot):
    """ScriptedBot with two fixes:

    Fix 1 — Opposite-corner target:
        The original bot falls back to map center (64,64) when no enemies are
        visible. On symmetric 1v1 maps the enemy always starts diagonally
        opposite to our base, so the army marches to the wrong location and
        stops. This override attacks toward the mirror of our CY position
        instead — the actual enemy spawn zone.

    Fix 2 — Periodic re-attack:
        The original only sends attack-move to *idle* units. Once units reach
        their destination and stop, is_idle=False so the bot never re-orders
        them. This override re-issues attack-move to ALL combat units every
        REATTACK_INTERVAL ticks so the army keeps chasing the enemy.
    """

    REATTACK_INTERVAL = 600  # re-issue attack order every ~600 ticks (~18s at 33tps)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._last_attack_tick: int = 0

    def _find_attack_target(self, obs: OpenRAObservation):
        """Priority: visible enemy buildings > enemy units > opposite corner > map center."""
        # Priority 1: visible enemy buildings
        if obs.visible_enemy_buildings:
            prod_buildings = [
                b for b in obs.visible_enemy_buildings
                if b.type in ("fact", "tent", "weap", "hpad", "afld")
            ]
            target = prod_buildings[0] if prod_buildings else obs.visible_enemy_buildings[0]
            return target.cell_x, target.cell_y

        # Priority 2: visible enemy units
        if obs.visible_enemies:
            enemy = obs.visible_enemies[0]
            return enemy.cell_x, enemy.cell_y

        # Priority 3: mirror our CY across the map (opposite corner = enemy spawn on 1v1 maps)
        cy = self._find_building(obs, "fact")
        if cy and obs.map_info.width > 0:
            w, h = obs.map_info.width, obs.map_info.height
            # Use cell coords if available, otherwise derive from sub-cell pos
            cx = cy.cell_x if cy.cell_x > 0 else cy.pos_x // 1024
            cy_y = cy.cell_y if cy.cell_y > 0 else cy.pos_y // 1024
            # Clamp to valid map range
            target_x = max(0, min(w - 1, w - cx - 1))
            target_y = max(0, min(h - 1, h - cy_y - 1))
            return target_x, target_y

        # Fallback: map center
        return obs.map_info.width // 2, obs.map_info.height // 2

    def _handle_combat(self, obs: OpenRAObservation):
        from openra_env.models import CommandModel, ActionType
        commands = []
        if self.phase != "attack":
            return commands

        # Unload APC near enemy (unchanged)
        commands.extend(self._handle_unload(obs))

        ticks_since_attack = obs.tick - self._last_attack_tick
        if ticks_since_attack < self.REATTACK_INTERVAL:
            return commands

        # Send ALL non-guard combat units, not just idle ones
        fighters = [
            u for u in obs.units
            if u.type in self.COMBAT_UNIT_TYPES
            and u.actor_id not in self._guards_assigned
        ]

        if len(fighters) < 2:
            return commands

        target_x, target_y = self._find_attack_target(obs)
        self._last_attack_tick = obs.tick

        for unit in fighters:
            commands.append(CommandModel(
                action=ActionType.ATTACK_MOVE,
                actor_id=unit.actor_id,
                target_x=target_x,
                target_y=target_y,
            ))

        self._log(
            f"[periodic] Attack-move {len(fighters)} units → "
            f"({target_x}, {target_y}) at tick {obs.tick}"
        )
        return commands


def serialize_obs(obs: OpenRAObservation) -> dict:
    """Convert observation to a JSON-serializable dict.

    Drops the spatial_map field (large binary) to keep files manageable.
    """
    d = obs.model_dump()
    # Remove large spatial tensor — not needed for text-based imitation learning
    d.pop("spatial_map", None)
    d.pop("metadata", None)
    return d


def serialize_action(action: OpenRAAction) -> dict:
    """Convert action to a JSON-serializable dict."""
    return action.model_dump()


async def collect_episode(
    env_url: str,
    episode_id: int,
    max_steps: int = 20000,
    max_minutes: float = 15.0,
    map_name: str = "singles.oramap",
    verbose: bool = False,
) -> list[dict]:
    """Play one full game with the ScriptedBot and record the trajectory.

    Stops when any of these conditions is met (in order):
      1. Game ends (win / lose)
      2. Wall-clock time exceeds max_minutes
      3. Step count reaches max_steps (safety cap)

    Returns:
        List of {observation, action, reward} dicts — one per game step.
    """
    bot = PeriodicAttackBot(verbose=False)
    trajectory = []
    max_seconds = max_minutes * 60.0
    episode_start = time.time()

    async with OpenRAEnv(base_url=env_url, message_timeout_s=300.0) as env:
        if verbose:
            print(f"  Episode {episode_id}: Resetting environment (map={map_name})...")

        result = await env.reset(map_name=map_name)
        obs = result.observation

        if verbose:
            print(
                f"  Episode {episode_id}: Game started on "
                f"{obs.map_info.map_name} ({obs.map_info.width}x{obs.map_info.height})"
            )

        step = 0
        while not result.done and step < max_steps:
            # Stop if wall-clock time limit exceeded
            elapsed_so_far = time.time() - episode_start
            if elapsed_so_far >= max_seconds:
                if verbose:
                    print(
                        f"  Episode {episode_id}: Time limit reached "
                        f"({elapsed_so_far/60:.1f} min), stopping."
                    )
                break

            # Expert decides
            action = bot.decide(result.observation)

            # Record the transition
            trajectory.append({
                "step": step,
                "observation": serialize_obs(result.observation),
                "action": serialize_action(action),
                "reward": result.reward or 0.0,
            })

            # Execute
            result = await env.step(action)
            step += 1

            if verbose and step % 200 == 0:
                eco = result.observation.economy
                n_units = len(result.observation.units)
                n_buildings = len(result.observation.buildings)
                elapsed_min = (time.time() - episode_start) / 60.0
                print(
                    f"  Episode {episode_id}: Step {step:4d} | "
                    f"Tick {result.observation.tick:5d} | "
                    f"${eco.cash:5d} | "
                    f"Units:{n_units} Bldgs:{n_buildings} | "
                    f"{bot.phase} | {elapsed_min:.1f}min"
                )

        # Record the final observation
        final_obs = result.observation
        trajectory.append({
            "step": step,
            "observation": serialize_obs(final_obs),
            "action": {"commands": [{"action": "no_op"}]},
            "reward": result.reward or 0.0,
            "terminal": True,
        })

        if verbose:
            mil = final_obs.military
            if final_obs.done:
                outcome = final_obs.result
            elif (time.time() - episode_start) >= max_seconds:
                outcome = f"time_limit({max_minutes:.0f}min)"
            else:
                outcome = "step_limit"
            elapsed_total = (time.time() - episode_start) / 60.0
            print(
                f"  Episode {episode_id}: DONE — {outcome.upper()} | "
                f"{step} steps | Tick {final_obs.tick} | "
                f"Real time: {elapsed_total:.1f}min | "
                f"Kills: {mil.units_killed}u/{mil.buildings_killed}b | "
                f"Lost: {mil.units_lost}u/{mil.buildings_lost}b"
            )

    return trajectory


async def collect_all(
    env_url: str,
    num_episodes: int,
    max_steps: int,
    max_minutes: float,
    map_name: str,
    output_dir: Path,
    verbose: bool,
):
    """Collect multiple episodes sequentially."""
    output_dir.mkdir(parents=True, exist_ok=True)

    summaries = []

    for i in range(1, num_episodes + 1):
        print(f"\n{'='*60}")
        print(f"Collecting episode {i}/{num_episodes}")
        print(f"{'='*60}")

        t0 = time.time()
        try:
            trajectory = await collect_episode(
                env_url=env_url,
                episode_id=i,
                max_steps=max_steps,
                max_minutes=max_minutes,
                map_name=map_name,
                verbose=verbose,
            )
        except Exception as e:
            print(f"  Episode {i} FAILED: {e}")
            continue

        elapsed = time.time() - t0

        # Save trajectory
        filename = output_dir / f"episode_{i:03d}.json"
        with open(filename, "w") as f:
            json.dump(trajectory, f, indent=None)  # compact JSON

        # Compute summary
        final = trajectory[-1]["observation"]
        mil = final.get("military", {})
        summary = {
            "episode": i,
            "steps": len(trajectory),
            "ticks": final.get("tick", 0),
            "result": final.get("result", "timeout"),
            "kills_cost": mil.get("kills_cost", 0),
            "deaths_cost": mil.get("deaths_cost", 0),
            "explored_percent": final.get("explored_percent", 0),
            "final_buildings": len(final.get("buildings", [])),
            "final_units": len(final.get("units", [])),
            "elapsed_s": round(elapsed, 1),
            "file": str(filename),
        }
        summaries.append(summary)

        file_size_mb = filename.stat().st_size / (1024 * 1024)
        print(
            f"  Saved {filename.name} ({file_size_mb:.1f} MB, "
            f"{len(trajectory)} steps, {elapsed:.0f}s)"
        )

    # Save collection summary
    summary_file = output_dir / "collection_summary.json"
    with open(summary_file, "w") as f:
        json.dump(summaries, f, indent=2)

    print(f"\n{'='*60}")
    print(f"Collection complete: {len(summaries)}/{num_episodes} episodes")
    print(f"Summary: {summary_file}")
    print(f"{'='*60}")

    # Print results table
    if summaries:
        print(f"\n{'Episode':>8} {'Result':>10} {'Steps':>6} {'Kills$':>8} {'Deaths$':>8} {'Bldgs':>6} {'Units':>6}")
        print("-" * 62)
        for s in summaries:
            result_str = s['result'] if s['result'] else 'timeout'
            print(
                f"{s['episode']:>8} {result_str:>10} {s['steps']:>6} "
                f"{s['kills_cost']:>8} {s['deaths_cost']:>8} "
                f"{s['final_buildings']:>6} {s['final_units']:>6}"
            )


def main():
    parser = argparse.ArgumentParser(
        description="Collect ScriptedBot expert demonstrations for imitation learning"
    )
    parser.add_argument(
        "--url",
        default="http://localhost:8000",
        help="OpenRA-RL server URL (default: http://localhost:8000)",
    )
    parser.add_argument(
        "--episodes",
        type=int,
        default=10,
        help="Number of episodes to collect (default: 10)",
    )
    parser.add_argument(
        "--map",
        default="singles.oramap",
        help=(
            "Map filename to play on (default: singles.oramap). "
            "Must be one of the .oramap files inside the server container. "
            "List available maps with: "
            "docker exec openra-rl-server find /opt/openra/mods/ra/maps -name '*.oramap'"
        ),
    )
    parser.add_argument(
        "--max-minutes",
        type=float,
        default=15.0,
        help="Max real-time minutes per episode (default: 15). Episodes stop early if game ends.",
    )
    parser.add_argument(
        "--max-steps",
        type=int,
        default=200000,
        help="Hard step cap per episode (default: 200000, effectively unlimited). Use --max-minutes instead.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("data/episodes"),
        help="Output directory for trajectory files (default: data/episodes)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print per-step progress",
    )
    args = parser.parse_args()

    try:
        asyncio.run(
            collect_all(
                env_url=args.url,
                num_episodes=args.episodes,
                max_steps=args.max_steps,
                max_minutes=args.max_minutes,
                map_name=args.map,
                output_dir=args.output_dir,
                verbose=args.verbose,
            )
        )
    except KeyboardInterrupt:
        print("\nInterrupted by user")
        sys.exit(0)
    except ConnectionRefusedError:
        print(f"\nCould not connect to {args.url}")
        print("Is the OpenRA-RL server running?")
        print("  docker run -p 8000:8000 openra-rl")
        sys.exit(1)


if __name__ == "__main__":
    main()
