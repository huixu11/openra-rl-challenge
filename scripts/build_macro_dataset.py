#!/usr/bin/env python3
"""Build a compact macro-action dataset from collected episode trajectories.

This script reads the `episode_*.json` files written by `collect_bot_data.py`
and exports a much smaller JSONL dataset for behavior cloning.

Why not train directly on replay files?
    Replay files are great archives, but they are not already aligned into
    `(observation, action)` supervision rows. A replay tells the engine how the
    match unfolded; the trajectory JSON stores what the RL client observed at a
    specific step and which action JSON it chose from that partial observation.

Typical usage:
    python scripts/build_macro_dataset.py

    python scripts/build_macro_dataset.py \
        --data-dir data/episodes \
        --output-path data/macro/macro_dataset.jsonl.gz \
        --sample-every 12 \
        --include-raw-action
"""

from __future__ import annotations

import argparse
import gzip
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Iterator, TextIO


CHUNK_SIZE = 1 << 20


def iter_json_array(path: Path) -> Iterator[dict[str, Any]]:
    """Stream a JSON array without loading the whole file into memory."""
    opener = gzip.open if path.suffix == ".gz" else open
    decoder = json.JSONDecoder()

    with opener(path, "rt", encoding="utf-8-sig") as f:
        buffer = ""

        # Consume the opening '['
        while True:
            chunk = f.read(CHUNK_SIZE)
            if not chunk:
                raise ValueError(f"{path} is empty or not a JSON array")
            buffer += chunk
            stripped = buffer.lstrip()
            if not stripped:
                buffer = ""
                continue
            if stripped[0] != "[":
                raise ValueError(f"{path} does not start with a JSON array")
            buffer = stripped[1:]
            break

        while True:
            while True:
                buffer = buffer.lstrip()
                if buffer.startswith(","):
                    buffer = buffer[1:]
                    continue
                break

            while not buffer:
                chunk = f.read(CHUNK_SIZE)
                if not chunk:
                    return
                buffer += chunk

            buffer = buffer.lstrip()
            if buffer.startswith("]"):
                return

            while True:
                try:
                    item, idx = decoder.raw_decode(buffer)
                    yield item
                    buffer = buffer[idx:]
                    break
                except json.JSONDecodeError:
                    chunk = f.read(CHUNK_SIZE)
                    if not chunk:
                        raise ValueError(f"{path} ended mid-object")
                    buffer += chunk


def get_episode_files(data_dir: Path, max_episodes: int | None) -> list[Path]:
    files = sorted(data_dir.glob("episode_*.json")) + sorted(data_dir.glob("episode_*.json.gz"))
    if max_episodes is not None:
        files = files[:max_episodes]
    if not files:
        raise FileNotFoundError(
            f"No episode_*.json files found in {data_dir}. "
            "Run collect_bot_data.py with --save-json first."
        )
    return files


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
    """Map a raw command dict to a compact macro-action fragment."""
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
    """Stable signature for merging equivalent macro fragments."""
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


def open_text_writer(path: Path) -> TextIO:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix == ".gz":
        return gzip.open(path, "wt", encoding="utf-8")
    return open(path, "w", encoding="utf-8")


def scan_episode_metadata(path: Path) -> dict[str, Any]:
    last_step = -1
    last_obs: dict[str, Any] = {}
    for idx, row in enumerate(iter_json_array(path)):
        last_step = idx
        last_obs = get_nested(row, "observation", {})

    final_units = len(get_nested(last_obs, "units", []))
    final_buildings = len(get_nested(last_obs, "buildings", []))
    episode_result = str(last_obs.get("result", "") or "")
    if not episode_result:
        if final_units == 0 and final_buildings == 0:
            episode_result = "eliminated"
        elif last_obs.get("done"):
            episode_result = "terminal_unknown"
        else:
            episode_result = "unknown"

    return {
        "num_rows": last_step + 1,
        "episode_result": episode_result,
        "final_tick": int(last_obs.get("tick", 0) or 0),
        "map_name": str(get_nested(get_nested(last_obs, "map_info", {}), "map_name", "") or ""),
    }


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
    include_raw_action: bool,
) -> dict[str, Any]:
    row = {
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
    if include_raw_action:
        row["raw_action"] = get_nested(step_data, "action", {})
    return row


def process_episode(
    path: Path,
    writer: TextIO,
    stats: dict[str, Any],
    sample_every: int,
    keep_state_changes: bool,
    top_k: int,
    include_raw_action: bool,
) -> None:
    meta = scan_episode_metadata(path)
    episode_name = path.stem
    if episode_name.endswith(".json"):
        episode_name = Path(episode_name).stem

    prev_sig: tuple[Any, ...] | None = None

    for step_idx, step_data in enumerate(iter_json_array(path)):
        obs = get_nested(step_data, "observation", {})
        action = get_nested(step_data, "action", {})
        macros = extract_macro_actions(action)
        reasons = should_keep_step(
            step_idx=step_idx,
            step_data=step_data,
            obs=obs,
            macros=macros,
            prev_sig=prev_sig,
            sample_every=sample_every,
            keep_state_changes=keep_state_changes,
        )

        prev_sig = observation_signature(obs)
        stats["steps_seen"] += 1

        if not reasons:
            stats["steps_skipped"] += 1
            continue

        summary = summarize_observation(obs, top_k=top_k)
        row = build_row(
            episode_name=episode_name,
            episode_result=meta["episode_result"],
            step_data=step_data,
            state_summary=summary,
            macros=macros,
            reasons=reasons,
            include_raw_action=include_raw_action,
        )
        writer.write(json.dumps(row, separators=(",", ":")) + "\n")

        stats["rows_written"] += 1
        stats["episodes_processed"] += 0
        stats["primary_intents"][row["primary_intent"]] += 1
        for reason in reasons:
            stats["selection_reasons"][reason] += 1


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build a compact macro-action dataset from episode trajectory JSON files."
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path("data/episodes"),
        help="Directory containing episode_*.json files (default: data/episodes)",
    )
    parser.add_argument(
        "--output-path",
        type=Path,
        default=Path("data/macro/macro_dataset.jsonl.gz"),
        help="Output JSONL or JSONL.GZ path (default: data/macro/macro_dataset.jsonl.gz)",
    )
    parser.add_argument(
        "--max-episodes",
        type=int,
        default=None,
        help="Maximum number of episode files to process (default: all)",
    )
    parser.add_argument(
        "--sample-every",
        type=int,
        default=12,
        help="Keep every Nth step as a periodic snapshot (default: 12, 0 disables periodic sampling)",
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
        "--include-raw-action",
        action="store_true",
        help="Include the original action JSON for traceability",
    )
    args = parser.parse_args()

    try:
        episode_files = get_episode_files(args.data_dir, args.max_episodes)
    except FileNotFoundError as exc:
        print(exc)
        sys.exit(1)

    stats = {
        "episodes_requested": len(episode_files),
        "episodes_processed": 0,
        "steps_seen": 0,
        "steps_skipped": 0,
        "rows_written": 0,
        "primary_intents": Counter(),
        "selection_reasons": Counter(),
        "sample_every": args.sample_every,
        "keep_state_changes": args.keep_state_changes,
    }

    with open_text_writer(args.output_path) as writer:
        for path in episode_files:
            print(f"Processing {path.name} ...")
            process_episode(
                path=path,
                writer=writer,
                stats=stats,
                sample_every=args.sample_every,
                keep_state_changes=args.keep_state_changes,
                top_k=args.top_k_types,
                include_raw_action=args.include_raw_action,
            )
            stats["episodes_processed"] += 1

    stats["primary_intents"] = dict(stats["primary_intents"].most_common())
    stats["selection_reasons"] = dict(stats["selection_reasons"].most_common())
    stats_path = args.output_path.with_suffix(args.output_path.suffix + ".stats.json")
    with open(stats_path, "w", encoding="utf-8") as f:
        json.dump(stats, f, indent=2)

    print(f"\nWrote {stats['rows_written']} rows to {args.output_path}")
    print(f"Stats: {stats_path}")
    print(f"Primary intents: {stats['primary_intents']}")
    print(f"Selection reasons: {stats['selection_reasons']}")


if __name__ == "__main__":
    main()
