#!/usr/bin/env python3
"""Collect expert demonstrations by running a bot against OpenRA's built-in AI.

Supports two Python bots:
  - scripted: PeriodicAttackBot (simple build-order + periodic grid-search attack)
  - normal:   NormalAIBot (reimplements OpenRA's ModularBot@NormalAI in Python)

Usage:
    # Start the OpenRA-RL server first:
    docker run -p 8000:8000 openra-rl

    # Collect with the default scripted bot:
    python scripts/collect_bot_data.py --episodes 10

    # Collect with the normal AI bot (mimics OpenRA's built-in normal AI):
    python scripts/collect_bot_data.py --episodes 10 --bot normal

    # Quick test (2 minutes per episode):
    python scripts/collect_bot_data.py --episodes 2 --max-minutes 2 --bot normal

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
import shutil
import sys
import time
from pathlib import Path

# openra_env is installed via: pip install openra-rl
# scripted_bot.py is vendored in this scripts/ directory
sys.path.insert(0, str(Path(__file__).resolve().parent))

from openra_env.client import OpenRAEnv
from openra_env.models import OpenRAAction, OpenRAObservation, CommandModel, ActionType
from scripted_bot import ScriptedBot
from normal_ai_bot import NormalAIBot

GAME_STATE_POLL_EVERY_STEPS = 25


class PeriodicAttackBot(ScriptedBot):
    """ScriptedBot with grid-search targeting that works on any map layout.

    Instead of assuming rotational symmetry, divides the map into a grid
    and systematically searches cells far from our base for the enemy.
    Cycles to the next grid cell whenever the army fails to find enemies.
    """

    REATTACK_INTERVAL = 600   # re-issue attack order every ~600 ticks (~18s)
    CYCLE_AFTER_REATTACKS = 2  # switch target after this many re-attacks with no enemy contact

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._last_attack_tick: int = 0
        self._candidate_targets: list[tuple[int, int]] = []
        self._target_index: int = 0
        self._no_contact_reattacks: int = 0
        self._cached_map_size: tuple[int, int] | None = None
        self._enemy_base_pos: tuple[int, int] | None = None

    def _handle_rally_points(self, obs):
        """Override: set rally on both Allied ('tent') and Soviet ('barr') barracks."""
        from openra_env.models import CommandModel, ActionType
        commands = []
        cy = self._find_building(obs, "fact")
        if not cy:
            return commands
        for b in obs.buildings:
            if b.type in ("tent", "barr", "weap") and b.actor_id not in self._rally_set:
                rally_x = cy.cell_x if cy.cell_x > 0 else cy.pos_x // 1024
                rally_y = cy.cell_y if cy.cell_y > 0 else cy.pos_y // 1024
                commands.append(CommandModel(
                    action=ActionType.SET_RALLY_POINT,
                    actor_id=b.actor_id,
                    target_x=rally_x,
                    target_y=rally_y,
                ))
                self._rally_set.add(b.actor_id)
        return commands

    def _get_map_size(self, obs: OpenRAObservation) -> tuple[int, int]:
        """Return map dimensions, preferring the in-game reported size.

        The reset observation may report padded dimensions (e.g. 128x128)
        while gameplay observations report the actual playable area (e.g.
        112x54). Always update the cache when a smaller (more accurate)
        value is observed.
        """
        w, h = obs.map_info.width, obs.map_info.height
        if w > 0 and h > 0:
            if self._cached_map_size is None:
                self._cached_map_size = (w, h)
            else:
                cw, ch = self._cached_map_size
                if w < cw or h < ch:
                    self._cached_map_size = (w, h)
                    self._candidate_targets = []
        if self._cached_map_size is not None:
            return self._cached_map_size
        return (128, 128)

    def _compute_candidate_spawns(self, obs: OpenRAObservation) -> list[tuple[int, int]]:
        """Generate search targets by dividing the map into a grid.

        Produces a 4x4 grid of cell centers, excludes the cell containing
        our base, and sorts by distance (farthest first) so we check the
        most likely enemy positions before nearby ones.
        """
        cy_bldg = self._find_building(obs, "fact")
        w, h = self._get_map_size(obs)

        if not cy_bldg:
            return [(w // 2, h // 2)]

        bx = cy_bldg.cell_x if cy_bldg.cell_x > 0 else cy_bldg.pos_x // 1024
        by = cy_bldg.cell_y if cy_bldg.cell_y > 0 else cy_bldg.pos_y // 1024

        grid_n = 3
        cell_w, cell_h = w // grid_n, h // grid_n
        grid_centers = []
        for gx in range(grid_n):
            for gy in range(grid_n):
                cx = cell_w * gx + cell_w // 2
                cy = cell_h * gy + cell_h // 2
                cx = max(0, min(w - 1, cx))
                cy = max(0, min(h - 1, cy))
                grid_centers.append((cx, cy))

        min_dist_sq = (min(w, h) // grid_n) ** 2
        candidates = [
            p for p in grid_centers
            if (p[0] - bx) ** 2 + (p[1] - by) ** 2 > min_dist_sq
        ]

        if not candidates:
            candidates = [(w // 2, h // 2)]

        candidates.sort(
            key=lambda p: (p[0] - bx) ** 2 + (p[1] - by) ** 2,
            reverse=True,
        )
        return candidates

    def _find_attack_target(self, obs: OpenRAObservation):
        """Priority: visible enemy buildings > enemy units > remembered base > grid search."""
        if obs.visible_enemy_buildings:
            prod_buildings = [
                b for b in obs.visible_enemy_buildings
                if b.type in ("fact", "tent", "weap", "hpad", "afld")
            ]
            target = prod_buildings[0] if prod_buildings else obs.visible_enemy_buildings[0]
            self._enemy_base_pos = (target.cell_x, target.cell_y)
            return target.cell_x, target.cell_y

        if obs.visible_enemies:
            enemy = obs.visible_enemies[0]
            if self._enemy_base_pos is None:
                self._enemy_base_pos = (enemy.cell_x, enemy.cell_y)
            return enemy.cell_x, enemy.cell_y

        if self._enemy_base_pos is not None:
            return self._enemy_base_pos

        if not self._candidate_targets:
            self._candidate_targets = self._compute_candidate_spawns(obs)
            self._target_index = 0

        return self._candidate_targets[self._target_index % len(self._candidate_targets)]

    def _handle_combat(self, obs: OpenRAObservation):
        from openra_env.models import CommandModel, ActionType
        commands = []
        if self.phase != "attack":
            return commands

        commands.extend(self._handle_unload(obs))

        fighters = [
            u for u in obs.units
            if u.type in self.COMBAT_UNIT_TYPES
            and u.actor_id not in self._guards_assigned
        ]

        if len(fighters) < 2:
            return commands

        # Reset counter when we actually see enemies (we're at the right place)
        if obs.visible_enemies or obs.visible_enemy_buildings:
            self._no_contact_reattacks = 0

        ticks_since_attack = obs.tick - self._last_attack_tick
        if ticks_since_attack < self.REATTACK_INTERVAL:
            return commands

        # Each time we re-attack without enemy contact, increment counter.
        # After CYCLE_AFTER_REATTACKS misses, switch to next candidate.
        if not obs.visible_enemies and not obs.visible_enemy_buildings:
            self._no_contact_reattacks += 1
            if (self._no_contact_reattacks >= self.CYCLE_AFTER_REATTACKS
                    and self._candidate_targets):
                self._target_index = (self._target_index + 1) % len(self._candidate_targets)
                self._no_contact_reattacks = 0
                self._log(
                    f"[cycle] No enemy contact after {self.CYCLE_AFTER_REATTACKS} "
                    f"re-attacks, switching to target #{self._target_index}: "
                    f"{self._candidate_targets[self._target_index]}"
                )

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


def available_credits(obs: OpenRAObservation) -> int:
    """OpenRA spendable money = liquid cash + stored ore/resources."""
    return obs.economy.cash + obs.economy.ore


def copy_replay_artifact(replay_info: dict, output_dir: Path, episode_id: int) -> dict:
    """Copy the replay locally when the server returned a readable file path."""
    if not replay_info:
        return {}

    replay_path = replay_info.get("path", "")
    if not replay_path:
        return replay_info

    source = Path(replay_path)
    if not source.is_file():
        return replay_info

    destination = output_dir / f"episode_{episode_id:03d}{source.suffix}"
    if source.resolve() != destination.resolve():
        shutil.copy2(source, destination)

    enriched = dict(replay_info)
    enriched["local_copy"] = str(destination)
    return enriched


def write_json_array_stream(path: Path, items: list[dict]) -> None:
    """Write a JSON array incrementally to avoid huge peak memory/slow dumps.

    This matters for long episodes where the trajectory can be >1GB.
    """
    with open(path, "w") as f:
        f.write("[")
        for i, item in enumerate(items):
            if i:
                f.write(",")
            json.dump(item, f, separators=(",", ":"))
            if i and i % 2000 == 0:
                f.flush()
        f.write("]")


def infer_outcome(
    final_obs: OpenRAObservation,
    eliminated_since_step: int | None,
    elapsed_s: float,
    max_minutes: float,
) -> str:
    """Classify why an episode stopped when the env did not emit a final result."""
    if final_obs.done and final_obs.result:
        return final_obs.result
    if eliminated_since_step is not None or (not final_obs.units and not final_obs.buildings):
        return "eliminated"
    if elapsed_s >= max_minutes * 60.0:
        return f"time_limit({max_minutes:.0f}min)"
    return "step_limit"


async def poll_terminal_game_state(env: OpenRAEnv) -> dict:
    """Query the server-side game-state tool and normalize terminal fields."""
    try:
        state = await asyncio.wait_for(env.call_tool("get_game_state"), timeout=5.0)
    except Exception:
        return {}

    if not isinstance(state, dict):
        return {}

    phase = str(state.get("phase", "") or "")
    winner = str(state.get("winner", "") or "")
    self_slot = str(state.get("self_slot", "") or "")
    enemy_slot = str(state.get("enemy_slot", "") or "")
    result = str(state.get("result", "") or "")

    if phase != "game_over":
        return {}

    if not result:
        if winner and self_slot and winner == self_slot:
            result = "win"
        elif winner and enemy_slot and winner == enemy_slot:
            result = "lose"
        elif winner:
            result = "draw"
        else:
            result = "draw"

    return {
        "phase": phase,
        "winner": winner,
        "self_slot": self_slot,
        "enemy_slot": enemy_slot,
        "result": result,
    }


async def collect_episode(
    env_url: str,
    episode_id: int,
    max_steps: int = 20000,
    max_minutes: float = 15.0,
    map_name: str = "singles.oramap",
    verbose: bool = False,
    bot_type: str = "scripted",
) -> dict:
    """Play one full game and record the trajectory.

    Stops when any of these conditions is met (in order):
      1. Game ends (win / lose)
      2. Wall-clock time exceeds max_minutes
      3. Step count reaches max_steps (safety cap)

    Returns:
        List of {observation, action, reward} dicts — one per game step.
    """
    if bot_type == "normal":
        bot = NormalAIBot(verbose=verbose)
    else:
        bot = PeriodicAttackBot(verbose=False)
    trajectory = []
    replay_info = {}
    error = ""
    max_seconds = max_minutes * 60.0
    episode_start = time.time()
    step = 0
    eliminated_since_step = None
    result = None
    obs = None
    terminal_game_state: dict = {}

    async with OpenRAEnv(base_url=env_url, message_timeout_s=300.0) as env:
        if verbose:
            print(f"  Episode {episode_id}: Resetting environment (map={map_name})...")

        try:
            result = await env.reset(map_name=map_name)
            obs = result.observation
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            if verbose:
                print(f"  Episode {episode_id}: RESET ERROR | {error}")
            try:
                replay_info = await env.call_tool("get_replay_path")
            except Exception:
                replay_info = {}
            return {"trajectory": trajectory, "replay": replay_info, "error": error}

        # Let the bot see the reset observation so _get_map_size can initialize
        # (the correct playable-area dimensions will be refined on later steps)

        if verbose:
            print(
                f"  Episode {episode_id}: Game started on "
                f"{obs.map_info.map_name} ({obs.map_info.width}x{obs.map_info.height})"
            )

        step = 0
        eliminated_since_step = None
        try:
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

                # Stop early if we've lost everything (0 units + 0 buildings)
                obs_now = result.observation
                if not obs_now.units and not obs_now.buildings and step > 100:
                    if eliminated_since_step is None:
                        eliminated_since_step = step
                    elif step - eliminated_since_step >= 200:
                        if verbose:
                            print(
                                f"  Episode {episode_id}: Eliminated "
                                f"(0 units/buildings for 200 steps), stopping."
                            )
                        break
                else:
                    eliminated_since_step = None

                try:
                    # Expert decides
                    obs_before = result.observation
                    action = bot.decide(obs_before)

                    # Execute and get reward for THIS action
                    result = await env.step(action)
                    step += 1

                    # Record (s, a, r, done) where r is the reward from taking a in s
                    trajectory.append({
                        "step": step - 1,
                        "observation": serialize_obs(obs_before),
                        "action": serialize_action(action),
                        "reward": result.reward or 0.0,
                        "done": result.done,
                    })

                    if not result.done and step % GAME_STATE_POLL_EVERY_STEPS == 0:
                        terminal_game_state = await poll_terminal_game_state(env)
                        if terminal_game_state:
                            if verbose:
                                print(
                                    f"  Episode {episode_id}: Game over via bridge state "
                                    f"(winner={terminal_game_state['winner'] or '?'}, "
                                    f"result={terminal_game_state['result']}), stopping."
                                )
                            break
                except Exception as exc:
                    error = f"{type(exc).__name__}: {exc}"
                    if verbose:
                        print(f"  Episode {episode_id}: STEP ERROR | {error}")
                    break

                if verbose and step % 200 == 0:
                    eco = result.observation.economy
                    n_units = len(result.observation.units)
                    n_buildings = len(result.observation.buildings)
                    elapsed_min = (time.time() - episode_start) / 60.0
                    credits = available_credits(result.observation)
                    attack_stats = bot.get_attack_stats(result.observation) if hasattr(bot, "get_attack_stats") else None
                    squad_stats = bot.get_squad_stats() if hasattr(bot, "get_squad_stats") else None
                    attack_info = ""
                    if attack_stats is not None:
                        attack_info = (
                            f" | Targeted:{attack_stats['unique_unit_targets']}u/{attack_stats['unique_building_targets']}b"
                            f" | Kills:{attack_stats['units_killed']}u/{attack_stats['buildings_killed']}b"
                        )
                    print(
                        f"  Episode {episode_id}: Step {step:4d} | "
                        f"Tick {result.observation.tick:5d} | "
                        f"Cash:${eco.cash:5d} Ore:{eco.ore:5d} Tot:${credits:5d} | "
                        f"Units:{n_units} Bldgs:{n_buildings} | "
                        f"{bot.phase} | {elapsed_min:.1f}min"
                        f"{attack_info}"
                    )
                    if squad_stats is not None:
                        states = squad_stats.get("states", {})
                        print(
                            "    "
                            f"Squads | idle:{squad_stats['idle_ground']} "
                            f"atk:{squad_stats['attack_squad']} "
                            f"rush:{squad_stats['rush_squad']} "
                            f"prot:{squad_stats['protection_squad']} "
                            f"thr:{squad_stats['assault_threshold']} "
                            f"state:{states}"
                        )

        except KeyboardInterrupt:
            error = "KeyboardInterrupt"
        finally:
            # If we stopped due to time/step limits, surrender to force a game-over so a replay is written.
            if result is not None and not result.done and not terminal_game_state:
                try:
                    await asyncio.wait_for(env.call_tool("surrender"), timeout=30.0)
                except Exception:
                    pass

        try:
            replay_info = await asyncio.wait_for(env.call_tool("get_replay_path"), timeout=10.0)
        except Exception as replay_exc:
            replay_info = {"error": str(replay_exc)}

        # Record the final observation (no action taken, so reward=0)
        final_obs = result.observation if result is not None else obs
        if final_obs is not None:
            if terminal_game_state:
                final_obs.done = True
                final_obs.result = terminal_game_state["result"]
            trajectory.append({
                "step": step,
                "observation": serialize_obs(final_obs),
                "action": {"commands": [{"action": "no_op"}]},
                "reward": 0.0,
                "done": True,
            })

        if verbose and final_obs is not None:
            mil = final_obs.military
            elapsed_total_s = time.time() - episode_start
            outcome = infer_outcome(final_obs, eliminated_since_step, elapsed_total_s, max_minutes)
            elapsed_total = elapsed_total_s / 60.0
            attack_stats = bot.get_attack_stats(final_obs) if hasattr(bot, "get_attack_stats") else None
            attack_info = ""
            if attack_stats is not None:
                attack_info = (
                    f" | Targeted: {attack_stats['unique_unit_targets']}u/{attack_stats['unique_building_targets']}b"
                    f" | Orders: {attack_stats['attack_commands']} ATTACK, {attack_stats['attack_move_commands']} AMOVE"
                )
            print(
                f"  Episode {episode_id}: DONE — {outcome.upper()} | "
                f"{step} steps | Tick {final_obs.tick} | "
                f"Real time: {elapsed_total:.1f}min | "
                f"Kills: {mil.units_killed}u/{mil.buildings_killed}b | "
                f"Lost: {mil.units_lost}u/{mil.buildings_lost}b"
                f"{attack_info}"
            )

    return {"trajectory": trajectory, "replay": replay_info, "error": error}


async def collect_all(
    env_url: str,
    num_episodes: int,
    max_steps: int,
    max_minutes: float,
    map_name: str,
    output_dir: Path,
    verbose: bool,
    bot_type: str = "scripted",
    save_json: bool = False,
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
            episode_data = await collect_episode(
                env_url=env_url,
                episode_id=i,
                max_steps=max_steps,
                max_minutes=max_minutes,
                map_name=map_name,
                verbose=verbose,
                bot_type=bot_type,
            )
        except Exception as e:
            print(f"  Episode {i} FAILED: {e}")
            continue

        elapsed = time.time() - t0
        trajectory = episode_data.get("trajectory", [])
        replay_info = copy_replay_artifact(episode_data.get("replay", {}), output_dir, i)
        episode_error = episode_data.get("error", "")

        filename = output_dir / f"episode_{i:03d}.json"
        if save_json:
            # Save trajectory (streaming write helps for huge episodes)
            write_json_array_stream(filename, trajectory)

        # Compute summary
        final = trajectory[-1]["observation"] if trajectory else {}
        mil = final.get("military", {})
        final_units = len(final.get("units", []))
        final_buildings = len(final.get("buildings", []))
        summary_result = final.get("result")
        if not summary_result:
            if final_units == 0 and final_buildings == 0:
                summary_result = "eliminated"
            elif elapsed >= max_minutes * 60.0:
                summary_result = f"time_limit({max_minutes:.0f}min)"
            else:
                summary_result = "step_limit"
        summary = {
            "episode": i,
            "steps": len(trajectory),
            "ticks": final.get("tick", 0),
            "result": summary_result,
            "kills_cost": mil.get("kills_cost", 0),
            "deaths_cost": mil.get("deaths_cost", 0),
            "explored_percent": final.get("explored_percent", 0),
            "final_buildings": final_buildings,
            "final_units": final_units,
            "elapsed_s": round(elapsed, 1),
            "file": str(filename) if save_json else "",
            "replay_path": replay_info.get("path", ""),
            "replay_local_copy": replay_info.get("local_copy", ""),
            "error": episode_error,
        }
        summaries.append(summary)

        if save_json:
            file_size_mb = filename.stat().st_size / (1024 * 1024)
            print(
                f"  Saved {filename.name} ({file_size_mb:.1f} MB, "
                f"{len(trajectory)} steps, {elapsed:.0f}s)"
            )
        else:
            print(f"  Episode {i} finished ({len(trajectory)} steps, {elapsed:.0f}s)")
        if replay_info.get("local_copy"):
            print(f"  Replay copied to {Path(replay_info['local_copy']).name}")
        elif replay_info.get("path"):
            print(f"  Replay available at {replay_info['path']}")
        if episode_error:
            print(f"  Episode {i} completed with error: {episode_error}")

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
        "--bot",
        choices=["scripted", "normal"],
        default="scripted",
        help=(
            "Which Python bot to use (default: scripted). "
            "'normal' uses NormalAIBot that mimics OpenRA's built-in normal AI."
        ),
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print per-step progress",
    )
    parser.add_argument(
        "--save-json",
        action="store_true",
        help=(
            "Also save per-episode trajectory JSON (can be very large). "
            "Default is replay-only."
        ),
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
                bot_type=args.bot,
                save_json=args.save_json,
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
