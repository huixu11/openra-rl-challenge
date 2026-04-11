#!/usr/bin/env python3
"""Imitation learning: behavioral cloning on ScriptedBot demonstrations.

Trains a small LLM to predict the ScriptedBot's actions given game observations,
using TRL's SFTTrainer.

Usage:
    # 1. First collect data:
    python scripts/collect_bot_data.py --episodes 10

    # 2. Train:
    python scripts/train_imitation.py --data-dir data/episodes --model Qwen/Qwen3-4B

    # 3. Quick test with fewer episodes:
    python scripts/train_imitation.py --data-dir data/episodes --max-episodes 3 --epochs 1
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

from datasets import Dataset


# ─── Observation → Prompt ─────────────────────────────────────────────────────


def obs_to_prompt(obs: dict) -> str:
    """Convert a structured game observation into a concise text prompt.

    The prompt captures the key information an agent needs to make decisions:
    economy, units, buildings, enemies, and production status.
    """
    eco = obs.get("economy", {})
    units = obs.get("units", [])
    buildings = obs.get("buildings", [])
    enemies = obs.get("visible_enemies", [])
    enemy_buildings = obs.get("visible_enemy_buildings", [])
    production = obs.get("production", [])
    available = obs.get("available_production", [])

    # Power balance
    power_balance = eco.get("power_provided", 0) - eco.get("power_drained", 0)

    # Summarize units by type
    unit_types: dict[str, int] = {}
    idle_count = 0
    for u in units:
        unit_types[u["type"]] = unit_types.get(u["type"], 0) + 1
        if u.get("is_idle"):
            idle_count += 1

    # Summarize buildings by type
    building_types: dict[str, int] = {}
    for b in buildings:
        building_types[b["type"]] = building_types.get(b["type"], 0) + 1

    # Format unit summary
    unit_str = ", ".join(f"{count}x {utype}" for utype, count in sorted(unit_types.items()))
    building_str = ", ".join(f"{count}x {btype}" for btype, count in sorted(building_types.items()))

    # Production status
    if production:
        prod_str = ", ".join(
            f"{p['item']}@{p['progress']:.0%}" for p in production
        )
    else:
        prod_str = "idle"

    # Enemy info
    enemy_str = ""
    if enemies:
        enemy_types: dict[str, int] = {}
        for e in enemies:
            enemy_types[e["type"]] = enemy_types.get(e["type"], 0) + 1
        enemy_str = ", ".join(f"{c}x {t}" for t, c in sorted(enemy_types.items()))

    # Military stats
    mil = obs.get("military", {})

    prompt = f"""You are commanding a Red Alert army. Analyze the game state and issue commands.

GAME STATE (Tick {obs.get('tick', 0)}):
Economy: ${eco.get('cash', 0)} cash, {eco.get('ore', 0)} ore, power {power_balance:+d}, {eco.get('harvester_count', 0)} harvesters
Units ({len(units)}, {idle_count} idle): {unit_str or 'none'}
Buildings ({len(buildings)}): {building_str or 'none'}
Production: {prod_str}
Available to build: {', '.join(available[:15]) if available else 'none'}
Enemies visible: {len(enemies)} units{f' ({enemy_str})' if enemy_str else ''}, {len(enemy_buildings)} buildings
Combat stats: killed {mil.get('units_killed', 0)}u/{mil.get('buildings_killed', 0)}b, lost {mil.get('units_lost', 0)}u/{mil.get('buildings_lost', 0)}b

Issue your commands as a JSON array:"""

    return prompt


# ─── Action → Completion ──────────────────────────────────────────────────────


def action_to_completion(action: dict) -> str:
    """Convert a structured action dict into a compact JSON text completion.

    Only includes non-default fields to keep completions short.
    """
    commands = action.get("commands", [])
    compact = []
    for cmd in commands:
        entry = {"action": cmd.get("action", "no_op")}
        # Only include non-zero / non-empty optional fields
        if cmd.get("actor_id", 0) != 0:
            entry["actor_id"] = cmd["actor_id"]
        if cmd.get("target_actor_id", 0) != 0:
            entry["target_actor_id"] = cmd["target_actor_id"]
        if cmd.get("target_x", 0) != 0 or cmd.get("target_y", 0) != 0:
            entry["target_x"] = cmd.get("target_x", 0)
            entry["target_y"] = cmd.get("target_y", 0)
        if cmd.get("item_type", ""):
            entry["item_type"] = cmd["item_type"]
        if cmd.get("queued", False):
            entry["queued"] = True
        compact.append(entry)

    return json.dumps(compact)


# ─── Data Loading ─────────────────────────────────────────────────────────────


def load_episodes(data_dir: Path, max_episodes: Optional[int] = None) -> list[dict]:
    """Load trajectory JSON files from the data directory.

    Returns list of {prompt, completion} dicts suitable for SFT.
    """
    episode_files = sorted(data_dir.glob("episode_*.json"))
    if max_episodes:
        episode_files = episode_files[:max_episodes]

    if not episode_files:
        print(f"No episode files found in {data_dir}")
        print("Run collect_bot_data.py first to generate training data.")
        sys.exit(1)

    print(f"Loading {len(episode_files)} episode files from {data_dir}")

    samples = []
    skipped_noop = 0
    total_steps = 0

    for ep_file in episode_files:
        with open(ep_file) as f:
            trajectory = json.load(f)

        for step_data in trajectory:
            if step_data.get("terminal"):
                continue

            obs = step_data["observation"]
            action = step_data["action"]
            total_steps += 1

            # Skip pure NO_OP steps (very common, not informative)
            commands = action.get("commands", [])
            if len(commands) == 1 and commands[0].get("action") == "no_op":
                skipped_noop += 1
                continue

            prompt = obs_to_prompt(obs)
            completion = action_to_completion(action)

            samples.append({
                "text": f"{prompt}\n{completion}",
                "prompt": prompt,
                "completion": completion,
            })

    print(
        f"  Total steps: {total_steps}, "
        f"Skipped NO_OP: {skipped_noop}, "
        f"Training samples: {len(samples)}"
    )

    return samples


# ─── Training ─────────────────────────────────────────────────────────────────


def train(
    data_dir: Path,
    model_name: str,
    output_dir: Path,
    max_episodes: Optional[int],
    epochs: int,
    batch_size: int,
    learning_rate: float,
    max_seq_length: int,
):
    """Run SFT training on collected demonstrations."""
    # Lazy imports so --help works without torch
    from transformers import AutoTokenizer, AutoModelForCausalLM
    from trl import SFTTrainer, SFTConfig

    # Load data
    samples = load_episodes(data_dir, max_episodes)
    if not samples:
        print("No training samples. Exiting.")
        sys.exit(1)

    dataset = Dataset.from_list(samples)
    print(f"\nDataset: {len(dataset)} samples")
    print(f"Example prompt (first 200 chars):\n{samples[0]['prompt'][:200]}...")
    print(f"Example completion:\n{samples[0]['completion'][:200]}...")

    # Load model and tokenizer
    print(f"\nLoading model: {model_name}")
    tokenizer = AutoTokenizer.from_pretrained(model_name, trust_remote_code=True)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    model = AutoModelForCausalLM.from_pretrained(
        model_name,
        trust_remote_code=True,
        torch_dtype="auto",
        device_map="auto",
    )

    # Training config
    training_args = SFTConfig(
        output_dir=str(output_dir),
        num_train_epochs=epochs,
        per_device_train_batch_size=batch_size,
        learning_rate=learning_rate,
        max_seq_length=max_seq_length,
        logging_steps=10,
        save_steps=500,
        save_total_limit=2,
        bf16=True,
        gradient_accumulation_steps=4,
        warmup_ratio=0.03,
        lr_scheduler_type="cosine",
        report_to="none",
        dataset_text_field="text",
    )

    # Train
    print(f"\nStarting SFT training...")
    print(f"  Epochs: {epochs}")
    print(f"  Batch size: {batch_size} (x4 grad accum = {batch_size * 4} effective)")
    print(f"  Learning rate: {learning_rate}")
    print(f"  Max seq length: {max_seq_length}")
    print(f"  Output: {output_dir}")

    trainer = SFTTrainer(
        model=model,
        tokenizer=tokenizer,
        train_dataset=dataset,
        args=training_args,
    )

    trainer.train()

    # Save final model
    print(f"\nSaving model to {output_dir}")
    trainer.save_model(str(output_dir))
    tokenizer.save_pretrained(str(output_dir))

    print("\nTraining complete!")
    print(f"Model saved to: {output_dir}")
    print(f"\nTo test the model, run:")
    print(f"  python -c \"from transformers import pipeline; ...")


# ─── CLI ──────────────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(
        description="Train an LLM to imitate the ScriptedBot via behavioral cloning"
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path("data/episodes"),
        help="Directory with episode JSON files (default: data/episodes)",
    )
    parser.add_argument(
        "--model",
        default="Qwen/Qwen3-4B",
        help="Base model to fine-tune (default: Qwen/Qwen3-4B)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("checkpoints/openra-il"),
        help="Output directory for trained model (default: checkpoints/openra-il)",
    )
    parser.add_argument(
        "--max-episodes",
        type=int,
        default=None,
        help="Maximum episodes to use (default: all)",
    )
    parser.add_argument(
        "--epochs",
        type=int,
        default=3,
        help="Number of training epochs (default: 3)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=2,
        help="Per-device batch size (default: 2)",
    )
    parser.add_argument(
        "--lr",
        type=float,
        default=2e-5,
        help="Learning rate (default: 2e-5)",
    )
    parser.add_argument(
        "--max-seq-length",
        type=int,
        default=1024,
        help="Max sequence length (default: 1024)",
    )

    # Also support just preparing the dataset (no training)
    parser.add_argument(
        "--prepare-only",
        action="store_true",
        help="Only load and prepare data, don't train (useful for debugging)",
    )

    args = parser.parse_args()

    if args.prepare_only:
        samples = load_episodes(args.data_dir, args.max_episodes)
        print(f"\nPrepared {len(samples)} training samples.")
        print("\nSample prompt:")
        print(samples[0]["prompt"])
        print("\nSample completion:")
        print(samples[0]["completion"])
        return

    train(
        data_dir=args.data_dir,
        model_name=args.model,
        output_dir=args.output_dir,
        max_episodes=args.max_episodes,
        epochs=args.epochs,
        batch_size=args.batch_size,
        learning_rate=args.lr,
        max_seq_length=args.max_seq_length,
    )


if __name__ == "__main__":
    main()
