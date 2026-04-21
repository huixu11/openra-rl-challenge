#!/usr/bin/env python3
"""Collect compact macro-policy demonstrations against OpenRA's built-in AI.

Supports two Python bots:
  - scripted: PeriodicAttackBot (simple build-order + periodic grid-search attack)
  - normal:   NormalAIBot (reimplements OpenRA's ModularBot@NormalAI in Python)

Usage:
    # Start the OpenRA-RL server first:
    docker run -p 8000:8000 openra-rl

    # Collect a macro dataset with the default scripted bot:
    python scripts/collect_bot_data.py --episodes 10

    # Collect with the normal AI bot (mimics OpenRA's built-in normal AI):
    python scripts/collect_bot_data.py --episodes 10 --bot normal

    # Quick test (2 minutes per episode):
    python scripts/collect_bot_data.py --episodes 2 --max-minutes 2 --bot normal

Output:
    data/macro/macro_dataset.jsonl.gz  - compact macro-policy dataset
    data/episodes/collection_summary.json
    data/episodes/episode_001.orarep
    ...

Notes:
    - The server runs at ~23 steps/second, so 15 min is about 20,000 steps.
    - The ScriptedBot builds a full base by ~1400 steps, then marches to the enemy.
      You need 10+ minutes to see actual combat on a 128x128 map.
    - Episodes stop early if the game ends (win/lose) before the time limit.
    - The collector writes compact macro rows directly; it does not save raw
      per-step trajectory JSON.
"""

import argparse
import asyncio
import json
import shutil
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any
from urllib import parse as urlparse
from urllib import request as urlrequest

# openra_env is installed via: pip install openra-rl
# scripted_bot.py is vendored in this scripts/ directory
sys.path.insert(0, str(Path(__file__).resolve().parent))

from openra_env.client import OpenRAEnv
from openra_env.models import OpenRAAction, OpenRAObservation, CommandModel, ActionType
from scripted_bot import ScriptedBot
from normal_ai_bot import NormalAIBot


def get_nested(d: dict[str, Any], key: str, default: Any = None) -> Any:
    value = d.get(key, default)
    return value if value is not None else default


def count_types(items: list[dict[str, Any]], key: str = "type", top_k: int = 12) -> dict[str, int]:
    counts = Counter(str(item.get(key, "")) for item in items if item.get(key))
    ordered = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:top_k]
    return {name: count for name, count in ordered}


def render_counts(counts: dict[str, int]) -> str:
    if not counts:
        return "none"
    return ", ".join(f"{count}x {name}" for name, count in counts.items())


def infer_phase(obs: dict[str, Any]) -> str:
    if obs.get("done"):
        return "terminal"

    buildings = {b.get("type") for b in get_nested(obs, "buildings", []) if b.get("type")}
    tick = int(obs.get("tick", 0) or 0)
    visible_enemy_units = len(get_nested(obs, "visible_enemies", []))
    visible_enemy_buildings = len(get_nested(obs, "visible_enemy_buildings", []))
    military = get_nested(obs, "military", {})
    combat_activity = sum(
        int(get_nested(military, field, 0) or 0)
        for field in ("units_killed", "buildings_killed", "units_lost", "buildings_lost")
    )

    if visible_enemy_units or visible_enemy_buildings:
        return "combat"
    if not buildings:
        return "eliminated"
    if tick < 3000 or "fact" not in buildings:
        return "opening"
    if not ({"tent", "barr"} & buildings) or "proc" not in buildings:
        return "opening"
    if tick < 12000 and combat_activity == 0:
        return "build_up"
    if tick >= 24000:
        return "late_game"
    return "mid_game"


def summarize_observation(obs: dict[str, Any], top_k: int) -> dict[str, Any]:
    eco = get_nested(obs, "economy", {})
    units = get_nested(obs, "units", [])
    buildings = get_nested(obs, "buildings", [])
    enemies = get_nested(obs, "visible_enemies", [])
    enemy_buildings = get_nested(obs, "visible_enemy_buildings", [])
    production = get_nested(obs, "production", [])
    available = get_nested(obs, "available_production", [])
    military = get_nested(obs, "military", {})
    map_info = get_nested(obs, "map_info", {})

    idle_units = sum(1 for unit in units if unit.get("is_idle"))
    power_balance = int(get_nested(eco, "power_provided", 0) or 0) - int(get_nested(eco, "power_drained", 0) or 0)

    return {
        "tick": int(obs.get("tick", 0) or 0),
        "phase": infer_phase(obs),
        "done": bool(obs.get("done", False)),
        "result": str(obs.get("result", "") or ""),
        "map": {
            "name": str(get_nested(map_info, "map_name", "") or ""),
            "width": int(get_nested(map_info, "width", 0) or 0),
            "height": int(get_nested(map_info, "height", 0) or 0),
        },
        "economy": {
            "cash": int(get_nested(eco, "cash", 0) or 0),
            "ore": int(get_nested(eco, "ore", 0) or 0),
            "credits": int(get_nested(eco, "cash", 0) or 0) + int(get_nested(eco, "ore", 0) or 0),
            "power_balance": power_balance,
            "harvesters": int(get_nested(eco, "harvester_count", 0) or 0),
        },
        "own": {
            "units": len(units),
            "idle_units": idle_units,
            "unit_types": count_types(units, top_k=top_k),
            "buildings": len(buildings),
            "building_types": count_types(buildings, top_k=top_k),
        },
        "enemy": {
            "visible_units": len(enemies),
            "unit_types": count_types(enemies, top_k=top_k),
            "visible_buildings": len(enemy_buildings),
            "building_types": count_types(enemy_buildings, top_k=top_k),
        },
        "production": [
            {
                "item": str(p.get("item", "") or ""),
                "progress": round(float(p.get("progress", 0.0) or 0.0), 3),
                "remaining_ticks": int(p.get("remaining_ticks", 0) or 0),
            }
            for p in production[:8]
            if p.get("item")
        ],
        "available_production": [str(item) for item in available[:20]],
        "military": {
            key: int(get_nested(military, key, 0) or 0)
            for key in (
                "units_killed",
                "buildings_killed",
                "units_lost",
                "buildings_lost",
                "kills_cost",
                "deaths_cost",
            )
        },
        "explored_percent": float(obs.get("explored_percent", 0.0) or 0.0),
    }


def summarize_command(cmd: dict[str, Any]) -> dict[str, Any]:
    action = str(cmd.get("action", "no_op") or "no_op").lower()
    macro: dict[str, Any] = {"intent": action, "count": 1}

    item_type = cmd.get("item_type")
    if item_type:
        macro["item_type"] = str(item_type)

    target_actor_id = int(cmd.get("target_actor_id", 0) or 0)
    if target_actor_id:
        macro["target_actor_id"] = target_actor_id

    target_x = cmd.get("target_x")
    target_y = cmd.get("target_y")
    if target_x is not None or target_y is not None:
        tx = int(target_x or 0)
        ty = int(target_y or 0)
        if tx or ty:
            macro["target"] = {"x": tx, "y": ty}

    queued = bool(cmd.get("queued", False))
    if queued:
        macro["queued"] = True

    for extra_key in ("stance", "stance_type"):
        extra_value = cmd.get(extra_key)
        if extra_value not in (None, "", 0):
            macro[extra_key] = extra_value

    return macro


def macro_signature(macro: dict[str, Any]) -> str:
    payload = {k: v for k, v in macro.items() if k != "count"}
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def merge_macros(macros: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    index_by_sig: dict[str, int] = {}

    for macro in macros:
        sig = macro_signature(macro)
        existing_idx = index_by_sig.get(sig)
        if existing_idx is None:
            index_by_sig[sig] = len(merged)
            merged.append(dict(macro))
        else:
            merged[existing_idx]["count"] += int(macro.get("count", 1) or 1)

    build_idx = next((i for i, item in enumerate(merged) if item.get("intent") == "build"), None)
    place_idx = next((i for i, item in enumerate(merged) if item.get("intent") == "place_building"), None)
    if build_idx is not None and place_idx is not None:
        build_macro = merged[build_idx]
        place_macro = merged[place_idx]
        construct_macro: dict[str, Any] = {
            "intent": "construct",
            "count": min(int(build_macro.get("count", 1)), int(place_macro.get("count", 1))),
        }
        if "item_type" in build_macro:
            construct_macro["item_type"] = build_macro["item_type"]
        if "target" in place_macro:
            construct_macro["target"] = place_macro["target"]

        rebuilt: list[dict[str, Any]] = [construct_macro]
        for idx, macro in enumerate(merged):
            if idx not in (build_idx, place_idx):
                rebuilt.append(macro)
        merged = rebuilt

    return merged


def extract_macro_actions(action: dict[str, Any]) -> list[dict[str, Any]]:
    commands = get_nested(action, "commands", [])
    if not commands:
        return [{"intent": "no_op", "count": 1}]
    merged = merge_macros([summarize_command(cmd) for cmd in commands])
    return merged or [{"intent": "no_op", "count": 1}]


def primary_intent(macros: list[dict[str, Any]]) -> str:
    intents = [str(m.get("intent", "no_op")) for m in macros]
    if not intents:
        return "no_op"
    if len(set(intents)) == 1:
        return intents[0]
    if any(intent in {"attack", "attack_move"} for intent in intents):
        return "combat_mixed"
    if any(intent in {"construct", "build", "place_building"} for intent in intents):
        return "base_mixed"
    if any(intent == "train" for intent in intents):
        return "production_mixed"
    return "mixed"


def observation_signature(obs: dict[str, Any]) -> tuple[Any, ...]:
    military = get_nested(obs, "military", {})
    return (
        len(get_nested(obs, "units", [])),
        len(get_nested(obs, "buildings", [])),
        len(get_nested(obs, "visible_enemies", [])),
        len(get_nested(obs, "visible_enemy_buildings", [])),
        len(get_nested(obs, "production", [])),
        int(get_nested(military, "units_killed", 0) or 0),
        int(get_nested(military, "buildings_killed", 0) or 0),
        int(get_nested(military, "units_lost", 0) or 0),
        int(get_nested(military, "buildings_lost", 0) or 0),
    )


def is_wait_only(macros: list[dict[str, Any]]) -> bool:
    return len(macros) == 1 and macros[0].get("intent") == "no_op"


def render_prompt(summary: dict[str, Any]) -> str:
    economy = summary["economy"]
    own = summary["own"]
    enemy = summary["enemy"]
    military = summary["military"]

    lines = [
        "You are planning the next macro action for an OpenRA Red Alert bot.",
        "Return a JSON array of compact macro actions.",
        "",
        f"[tick] {summary['tick']}",
        f"[phase] {summary['phase']}",
        (
            "[economy] "
            f"cash={economy['cash']} ore={economy['ore']} total={economy['credits']} "
            f"power={economy['power_balance']:+d} harvesters={economy['harvesters']}"
        ),
        (
            "[own] "
            f"units={own['units']} idle={own['idle_units']} "
            f"buildings={own['buildings']}"
        ),
        f"[own_units] {render_counts(own['unit_types'])}",
        f"[own_buildings] {render_counts(own['building_types'])}",
        (
            "[enemy] "
            f"visible_units={enemy['visible_units']} "
            f"visible_buildings={enemy['visible_buildings']}"
        ),
        f"[enemy_units] {render_counts(enemy['unit_types'])}",
        f"[enemy_buildings] {render_counts(enemy['building_types'])}",
        (
            "[combat] "
            f"killed={military['units_killed']}u/{military['buildings_killed']}b "
            f"lost={military['units_lost']}u/{military['buildings_lost']}b "
            f"kills_cost={military['kills_cost']} deaths_cost={military['deaths_cost']}"
        ),
        (
            "[production] "
            + (
                ", ".join(
                    f"{item['item']}@{item['progress']:.0%}(~{item['remaining_ticks']}t)"
                    for item in summary["production"]
                )
                if summary["production"]
                else "idle"
            )
        ),
        (
            "[available] "
            + (", ".join(summary["available_production"]) if summary["available_production"] else "none")
        ),
        f"[explored_percent] {summary['explored_percent']:.1f}",
    ]
    return "\n".join(lines)


def should_keep_step(
    step_idx: int,
    step_data: dict[str, Any],
    obs: dict[str, Any],
    macros: list[dict[str, Any]],
    prev_sig: tuple[Any, ...] | None,
    sample_every: int,
    keep_state_changes: bool,
) -> list[str]:
    reasons: list[str] = []

    if step_idx == 0:
        reasons.append("episode_start")
    if step_data.get("done"):
        reasons.append("terminal")
    if sample_every > 0 and step_idx % sample_every == 0:
        reasons.append("periodic")
    if not is_wait_only(macros):
        reasons.append("non_noop")
    if get_nested(obs, "visible_enemies", []) or get_nested(obs, "visible_enemy_buildings", []):
        reasons.append("enemy_visible")
    if abs(float(step_data.get("reward", 0.0) or 0.0)) > 1e-9:
        reasons.append("nonzero_reward")

    if keep_state_changes:
        current_sig = observation_signature(obs)
        if prev_sig is not None and current_sig != prev_sig:
            reasons.append("state_change")

    return reasons


def build_row(
    episode_name: str,
    episode_result: str,
    step_data: dict[str, Any],
    state_summary: dict[str, Any],
    macros: list[dict[str, Any]],
    reasons: list[str],
) -> dict[str, Any]:
    return {
        "id": f"{episode_name}:{int(step_data.get('step', 0) or 0)}",
        "episode": episode_name,
        "step": int(step_data.get("step", 0) or 0),
        "tick": state_summary["tick"],
        "phase": state_summary["phase"],
        "episode_result": episode_result,
        "reward": float(step_data.get("reward", 0.0) or 0.0),
        "done": bool(step_data.get("done", False)),
        "selection_reason": reasons,
        "primary_intent": primary_intent(macros),
        "state": state_summary,
        "macro_actions": macros,
        "prompt": render_prompt(state_summary),
        "completion": json.dumps(macros, separators=(",", ":")),
    }

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


def build_artifact_url(env_url: str, route: str, query: dict[str, Any] | None = None) -> str:
    """Build an HTTP URL for the repo's artifact helper endpoints."""
    base = env_url.rstrip("/")
    route = route if route.startswith("/") else f"/{route}"
    if not query:
        return f"{base}{route}"
    encoded = urlparse.urlencode({k: v for k, v in query.items() if v is not None})
    return f"{base}{route}?{encoded}" if encoded else f"{base}{route}"


def download_remote_replay(
    env_url: str,
    replay_path: str,
    output_dir: Path,
    episode_id: int,
) -> dict[str, Any]:
    """Download a replay from a remote OpenRA server artifact endpoint."""
    source = Path(replay_path)
    suffix = source.suffix or ".orarep"
    destination = output_dir / f"episode_{episode_id:03d}{suffix}"
    download_url = build_artifact_url(
        env_url,
        "/artifacts/replay",
        {"path": replay_path, "delete_after_download": "false"},
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    with urlrequest.urlopen(download_url, timeout=120) as response, open(destination, "wb") as out_file:
        shutil.copyfileobj(response, out_file)

    return {
        "local_copy": str(destination),
        "download_url": download_url,
    }


def cleanup_remote_artifacts(
    env_url: str,
    replay_paths: list[str] | None = None,
    delete_logs: bool = True,
) -> dict[str, Any]:
    """Delete remote replays/log files from the OpenRA server after download."""
    payload = {
        "replay_paths": replay_paths or [],
        "delete_logs": delete_logs,
    }
    request = urlrequest.Request(
        build_artifact_url(env_url, "/artifacts/cleanup"),
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlrequest.urlopen(request, timeout=30) as response:
        body = response.read().decode("utf-8")
    return json.loads(body) if body else {}


def copy_replay_artifact(replay_info: dict, output_dir: Path, episode_id: int, env_url: str) -> dict:
    """Copy or download the replay, then clean remote artifacts when applicable."""
    if not replay_info:
        try:
            cleanup_result = cleanup_remote_artifacts(env_url=env_url, replay_paths=[], delete_logs=True)
            return {"remote_cleanup": cleanup_result} if cleanup_result else {}
        except Exception as exc:
            return {"cleanup_error": f"{type(exc).__name__}: {exc}"}

    enriched = dict(replay_info)
    replay_path = str(replay_info.get("path", "") or "")
    source = Path(replay_path) if replay_path else None
    replay_downloaded = False

    if replay_path and source is not None and source.is_file():
        destination = output_dir / f"episode_{episode_id:03d}{source.suffix}"
        if source.resolve() != destination.resolve():
            shutil.copy2(source, destination)
        enriched["local_copy"] = str(destination)
        return enriched

    if replay_path:
        try:
            enriched.update(download_remote_replay(env_url, replay_path, output_dir, episode_id))
            replay_downloaded = True
        except Exception as exc:
            enriched["download_error"] = f"{type(exc).__name__}: {exc}"

    try:
        cleanup_result = cleanup_remote_artifacts(
            env_url=env_url,
            replay_paths=[replay_path] if replay_downloaded and replay_path else [],
            delete_logs=True,
        )
        if cleanup_result:
            enriched["remote_cleanup"] = cleanup_result
    except Exception as exc:
        enriched["cleanup_error"] = f"{type(exc).__name__}: {exc}"

    return enriched


def open_dataset_writer(path: Path, append: bool):
    """Open the compact macro dataset writer."""
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = "at" if append else "wt"
    if path.suffix == ".gz":
        import gzip

        return gzip.open(path, mode, encoding="utf-8")
    return open(path, mode, encoding="utf-8")


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


async def collect_episode(
    env_url: str,
    episode_id: int,
    max_steps: int = 20000,
    max_minutes: float = 15.0,
    map_name: str = "singles.oramap",
    verbose: bool = False,
    bot_type: str = "scripted",
    sample_every: int = 12,
    keep_state_changes: bool = False,
    top_k_types: int = 12,
) -> dict:
    """Play one full game and record compact macro-policy rows.

    Stops when any of these conditions is met (in order):
      1. Game ends (win / lose)
      2. Wall-clock time exceeds max_minutes
      3. Step count reaches max_steps (safety cap)

    Returns:
        Dict with compact macro rows plus replay and summary data.
    """
    if bot_type == "normal":
        bot = NormalAIBot(verbose=verbose)
    else:
        bot = PeriodicAttackBot(verbose=False)
    macro_candidates = []
    replay_info = {}
    error = ""
    max_seconds = max_minutes * 60.0
    episode_start = time.time()
    step = 0
    eliminated_since_step = None
    result = None
    obs = None
    prev_sig = None
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
            return {
                "macro_rows": [],
                "replay": replay_info,
                "error": error,
                "final_observation": {},
                "step_count": 0,
                "result": "",
            }

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

                    step_data = {
                        "step": step - 1,
                        "observation": serialize_obs(obs_before),
                        "action": serialize_action(action),
                        "reward": result.reward or 0.0,
                        "done": result.done,
                    }
                    macros = extract_macro_actions(step_data["action"])
                    reasons = should_keep_step(
                        step_idx=step_data["step"],
                        step_data=step_data,
                        obs=step_data["observation"],
                        macros=macros,
                        prev_sig=prev_sig,
                        sample_every=sample_every,
                        keep_state_changes=keep_state_changes,
                    )
                    prev_sig = observation_signature(step_data["observation"])
                    if reasons:
                        macro_candidates.append({
                            "step_data": step_data,
                            "state_summary": summarize_observation(step_data["observation"], top_k=top_k_types),
                            "macros": macros,
                            "reasons": reasons,
                        })

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
            if result is not None and not result.done:
                try:
                    await asyncio.wait_for(env.call_tool("surrender"), timeout=30.0)
                except Exception:
                    pass

        try:
            replay_info = await asyncio.wait_for(env.call_tool("get_replay_path"), timeout=10.0)
        except Exception as replay_exc:
            replay_info = {"error": str(replay_exc)}

        # Record the final observation as a terminal no-op example.
        final_obs = result.observation if result is not None else obs
        if final_obs is not None:
            final_step_data = {
                "step": step,
                "observation": serialize_obs(final_obs),
                "action": {"commands": [{"action": "no_op"}]},
                "reward": 0.0,
                "done": True,
            }
            final_macros = extract_macro_actions(final_step_data["action"])
            final_reasons = should_keep_step(
                step_idx=final_step_data["step"],
                step_data=final_step_data,
                obs=final_step_data["observation"],
                macros=final_macros,
                prev_sig=prev_sig,
                sample_every=sample_every,
                keep_state_changes=keep_state_changes,
            )
            if final_reasons:
                macro_candidates.append({
                    "step_data": final_step_data,
                    "state_summary": summarize_observation(final_step_data["observation"], top_k=top_k_types),
                    "macros": final_macros,
                    "reasons": final_reasons,
                })

        outcome = ""
        macro_rows = []
        final_obs_dict = {}
        if final_obs is not None:
            elapsed_total_s = time.time() - episode_start
            outcome = infer_outcome(final_obs, eliminated_since_step, elapsed_total_s, max_minutes)
            final_obs_dict = serialize_obs(final_obs)
            episode_name = f"episode_{episode_id:03d}"
            macro_rows = [
                build_row(
                    episode_name=episode_name,
                    episode_result=outcome,
                    step_data=item["step_data"],
                    state_summary=item["state_summary"],
                    macros=item["macros"],
                    reasons=item["reasons"],
                )
                for item in macro_candidates
            ]

        if verbose and final_obs is not None:
            mil = final_obs.military
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

    return {
        "macro_rows": macro_rows,
        "replay": replay_info,
        "error": error,
        "final_observation": final_obs_dict,
        "step_count": step,
        "result": outcome,
    }


async def collect_all(
    env_url: str,
    num_episodes: int,
    max_steps: int,
    max_minutes: float,
    map_name: str,
    output_dir: Path,
    dataset_path: Path,
    verbose: bool,
    bot_type: str = "scripted",
    sample_every: int = 12,
    keep_state_changes: bool = False,
    top_k_types: int = 12,
    append_dataset: bool = False,
):
    """Collect multiple episodes sequentially."""
    output_dir.mkdir(parents=True, exist_ok=True)

    summaries = []
    total_rows_written = 0

    with open_dataset_writer(dataset_path, append=append_dataset) as dataset_writer:
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
                    sample_every=sample_every,
                    keep_state_changes=keep_state_changes,
                    top_k_types=top_k_types,
                )
            except Exception as e:
                print(f"  Episode {i} FAILED: {e}")
                continue

            elapsed = time.time() - t0
            replay_info = copy_replay_artifact(
                episode_data.get("replay", {}),
                output_dir,
                i,
                env_url=env_url,
            )
            episode_error = episode_data.get("error", "")
            macro_rows = episode_data.get("macro_rows", [])
            final = episode_data.get("final_observation", {})

            for row in macro_rows:
                dataset_writer.write(json.dumps(row, separators=(",", ":")) + "\n")
            dataset_writer.flush()
            total_rows_written += len(macro_rows)

            mil = final.get("military", {})
            final_units = len(final.get("units", []))
            final_buildings = len(final.get("buildings", []))
            summary_result = episode_data.get("result") or final.get("result")
            if not summary_result:
                if final_units == 0 and final_buildings == 0:
                    summary_result = "eliminated"
                elif elapsed >= max_minutes * 60.0:
                    summary_result = f"time_limit({max_minutes:.0f}min)"
                else:
                    summary_result = "step_limit"
            summary = {
                "episode": i,
                "steps": episode_data.get("step_count", 0),
                "macro_rows": len(macro_rows),
                "ticks": final.get("tick", 0),
                "result": summary_result,
                "kills_cost": mil.get("kills_cost", 0),
                "deaths_cost": mil.get("deaths_cost", 0),
                "explored_percent": final.get("explored_percent", 0),
                "final_buildings": final_buildings,
                "final_units": final_units,
                "elapsed_s": round(elapsed, 1),
                "dataset_path": str(dataset_path),
                "replay_path": replay_info.get("path", ""),
                "replay_local_copy": replay_info.get("local_copy", ""),
                "replay_download_error": replay_info.get("download_error", ""),
                "replay_cleanup_error": replay_info.get("cleanup_error", ""),
                "error": episode_error,
            }
            summaries.append(summary)

            print(f"  Episode {i} finished ({episode_data.get('step_count', 0)} steps, {elapsed:.0f}s)")
            if replay_info.get("local_copy"):
                print(f"  Replay saved to {Path(replay_info['local_copy']).name}")
            elif replay_info.get("path"):
                print(f"  Replay available at {replay_info['path']}")
            if replay_info.get("download_error"):
                print(f"  Replay download failed: {replay_info['download_error']}")
            cleanup_info = replay_info.get("remote_cleanup", {})
            if cleanup_info:
                print(
                    "  Remote cleanup:"
                    f" {len(cleanup_info.get('deleted_replays', []))} replay(s),"
                    f" {len(cleanup_info.get('deleted_logs', []))} log file(s)"
                )
            if replay_info.get("cleanup_error"):
                print(f"  Remote cleanup failed: {replay_info['cleanup_error']}")
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
        description="Collect compact macro-policy demonstrations for behavior cloning"
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
        help="Output directory for replays and collection summary (default: data/episodes)",
    )
    parser.add_argument(
        "--dataset-path",
        type=Path,
        default=Path("data/macro/macro_dataset.jsonl.gz"),
        help="Compact macro dataset path (default: data/macro/macro_dataset.jsonl.gz)",
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
        "--sample-every",
        type=int,
        default=12,
        help="Keep every Nth step as a periodic macro snapshot (default: 12, 0 disables periodic sampling)",
    )
    parser.add_argument(
        "--keep-state-changes",
        action="store_true",
        help="Also keep steps where coarse unit/building/combat counts changed",
    )
    parser.add_argument(
        "--top-k-types",
        type=int,
        default=12,
        help="Maximum number of unit/building types to keep in summaries (default: 12)",
    )
    parser.add_argument(
        "--append-dataset",
        action="store_true",
        help="Append new rows to an existing macro dataset instead of overwriting it",
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
                dataset_path=args.dataset_path,
                verbose=args.verbose,
                bot_type=args.bot,
                sample_every=args.sample_every,
                keep_state_changes=args.keep_state_changes,
                top_k_types=args.top_k_types,
                append_dataset=args.append_dataset,
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
