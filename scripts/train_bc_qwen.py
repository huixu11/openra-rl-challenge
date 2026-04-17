#!/usr/bin/env python3
"""Train a Qwen behavioral-cloning model on the compact macro dataset.

This is the macro-policy training path. It consumes the JSONL/JSONL.GZ output
from `scripts/build_macro_dataset.py` instead of the raw step-by-step episode
trajectory JSON used by `scripts/train_raw_bc.py`.

Typical usage:
    python scripts/train_bc_qwen.py \
        --data-path data/macro/macro_dataset.jsonl.gz \
        --model Qwen/Qwen3-4B \
        --output-dir checkpoints/openra-bc-qwen

Recommended workflow:
    1. Collect a small audit set with `collect_bot_data.py --save-json`
    2. Build the compact macro dataset with `build_macro_dataset.py`
    3. Train this macro BC model
"""

from __future__ import annotations

import argparse
import gzip
import json
import random
import sys
from pathlib import Path
from typing import Any


def open_text_reader(path: Path):
    if path.suffix == ".gz":
        return gzip.open(path, "rt", encoding="utf-8-sig")
    return open(path, "r", encoding="utf-8-sig")


def load_macro_rows(
    data_path: Path,
    max_rows: int | None = None,
    max_episodes: int | None = None,
) -> list[dict[str, Any]]:
    """Load macro dataset rows from JSONL / JSONL.GZ."""
    if not data_path.exists():
        print(f"Dataset not found: {data_path}")
        print("Run scripts/build_macro_dataset.py first.")
        sys.exit(1)

    rows: list[dict[str, Any]] = []
    seen_episodes: set[str] = set()
    skipped_missing = 0

    with open_text_reader(data_path) as f:
        for line_no, raw_line in enumerate(f, start=1):
            line = raw_line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                print(f"Invalid JSONL at line {line_no}: {exc}")
                sys.exit(1)

            episode = str(row.get("episode", "") or "")
            if max_episodes is not None and episode and episode not in seen_episodes:
                if len(seen_episodes) >= max_episodes:
                    continue
                seen_episodes.add(episode)
            elif episode:
                seen_episodes.add(episode)

            prompt = str(row.get("prompt", "") or "").strip()
            completion = str(row.get("completion", "") or "").strip()
            if not prompt or not completion:
                skipped_missing += 1
                continue

            record = dict(row)
            record["text"] = f"{prompt}\n{completion}"
            rows.append(record)

            if max_rows is not None and len(rows) >= max_rows:
                break

    if not rows:
        print(f"No usable rows found in {data_path}")
        sys.exit(1)

    print(f"Loaded {len(rows)} rows from {data_path}")
    print(f"  Episodes: {len(seen_episodes)}")
    if skipped_missing:
        print(f"  Skipped rows missing prompt/completion: {skipped_missing}")
    return rows


def split_by_episode(
    rows: list[dict[str, Any]],
    val_ratio: float,
    seed: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split train/eval at the episode level to avoid leakage."""
    if val_ratio <= 0:
        return rows, []

    episode_to_rows: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        episode = str(row.get("episode", "") or "")
        episode_to_rows.setdefault(episode, []).append(row)

    episodes = list(episode_to_rows)
    if len(episodes) < 2:
        return rows, []

    rng = random.Random(seed)
    rng.shuffle(episodes)

    n_eval = max(1, round(len(episodes) * val_ratio))
    if n_eval >= len(episodes):
        n_eval = len(episodes) - 1
    eval_episodes = set(episodes[:n_eval])

    train_rows: list[dict[str, Any]] = []
    eval_rows: list[dict[str, Any]] = []
    for episode, episode_rows in episode_to_rows.items():
        if episode in eval_episodes:
            eval_rows.extend(episode_rows)
        else:
            train_rows.extend(episode_rows)

    return train_rows, eval_rows


def describe_rows(rows: list[dict[str, Any]], label: str) -> None:
    primary_intents: dict[str, int] = {}
    phases: dict[str, int] = {}
    results: dict[str, int] = {}

    for row in rows:
        intent = str(row.get("primary_intent", "") or "unknown")
        phase = str(row.get("phase", "") or "unknown")
        result = str(row.get("episode_result", "") or "unknown")
        primary_intents[intent] = primary_intents.get(intent, 0) + 1
        phases[phase] = phases.get(phase, 0) + 1
        results[result] = results.get(result, 0) + 1

    def top_counts(counts: dict[str, int], limit: int = 8) -> str:
        ordered = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:limit]
        return ", ".join(f"{k}:{v}" for k, v in ordered) if ordered else "none"

    print(f"\n{label}: {len(rows)} rows")
    print(f"  Primary intents: {top_counts(primary_intents)}")
    print(f"  Phases: {top_counts(phases)}")
    print(f"  Episode results: {top_counts(results)}")


def build_peft_config(args):
    from peft import LoraConfig

    target_modules = [module.strip() for module in args.lora_target_modules.split(",") if module.strip()]
    return LoraConfig(
        r=args.lora_r,
        lora_alpha=args.lora_alpha,
        lora_dropout=args.lora_dropout,
        bias="none",
        task_type="CAUSAL_LM",
        target_modules=target_modules,
    )


def load_model_and_tokenizer(args):
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig

    tokenizer = AutoTokenizer.from_pretrained(args.model, trust_remote_code=True)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token
    tokenizer.padding_side = "right"

    quantization_config = None
    if args.load_in_4bit:
        quantization_config = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_use_double_quant=True,
            bnb_4bit_compute_dtype=torch.bfloat16 if torch.cuda.is_available() and torch.cuda.is_bf16_supported() else torch.float16,
        )

    model_kwargs: dict[str, Any] = {
        "trust_remote_code": True,
    }
    if quantization_config is not None:
        model_kwargs["quantization_config"] = quantization_config
        model_kwargs["device_map"] = "auto"
    elif torch.cuda.is_available():
        model_kwargs["torch_dtype"] = "auto"
        model_kwargs["device_map"] = "auto"

    model = AutoModelForCausalLM.from_pretrained(args.model, **model_kwargs)
    return model, tokenizer


def train(args) -> None:
    rows = load_macro_rows(
        data_path=args.data_path,
        max_rows=args.max_rows,
        max_episodes=args.max_episodes,
    )
    train_rows, eval_rows = split_by_episode(rows, val_ratio=args.val_ratio, seed=args.seed)

    if not train_rows:
        print("No training rows after split. Exiting.")
        sys.exit(1)

    describe_rows(train_rows, "Train split")
    if eval_rows:
        describe_rows(eval_rows, "Eval split")
    else:
        print("\nEval split: disabled")

    print("\nExample prompt:")
    print(train_rows[0]["prompt"][:800])
    print("\nExample completion:")
    print(train_rows[0]["completion"][:400])

    if args.prepare_only:
        print("\nPrepare-only mode; not starting training.")
        return

    import torch
    from datasets import Dataset
    from peft import prepare_model_for_kbit_training
    from trl import SFTConfig, SFTTrainer

    train_dataset = Dataset.from_list(train_rows)
    eval_dataset = Dataset.from_list(eval_rows) if eval_rows else None

    print(f"\nLoading model: {args.model}")
    model, tokenizer = load_model_and_tokenizer(args)

    peft_config = build_peft_config(args) if not args.no_lora else None
    if args.load_in_4bit:
        model = prepare_model_for_kbit_training(model)

    use_bf16 = bool(torch.cuda.is_available() and torch.cuda.is_bf16_supported())
    use_fp16 = bool(torch.cuda.is_available() and not use_bf16)

    training_args = SFTConfig(
        output_dir=str(args.output_dir),
        num_train_epochs=args.epochs,
        per_device_train_batch_size=args.batch_size,
        per_device_eval_batch_size=args.eval_batch_size,
        gradient_accumulation_steps=args.grad_accum,
        learning_rate=args.lr,
        max_seq_length=args.max_seq_length,
        logging_steps=args.logging_steps,
        save_steps=args.save_steps,
        eval_steps=args.eval_steps if eval_dataset is not None else None,
        save_total_limit=args.save_total_limit,
        warmup_ratio=args.warmup_ratio,
        lr_scheduler_type=args.lr_scheduler_type,
        dataset_text_field="text",
        report_to="none",
        bf16=use_bf16,
        fp16=use_fp16,
        gradient_checkpointing=args.gradient_checkpointing,
        evaluation_strategy="steps" if eval_dataset is not None else "no",
    )

    print("\nStarting macro BC training...")
    print(f"  Output: {args.output_dir}")
    print(f"  Train rows: {len(train_dataset)}")
    print(f"  Eval rows: {len(eval_dataset) if eval_dataset is not None else 0}")
    print(f"  Epochs: {args.epochs}")
    print(f"  Batch size: {args.batch_size}")
    print(f"  Grad accum: {args.grad_accum}")
    print(f"  Learning rate: {args.lr}")
    print(f"  Max seq length: {args.max_seq_length}")
    print(f"  LoRA: {'off' if args.no_lora else 'on'}")
    if not args.no_lora:
        print(
            "  LoRA targets: "
            f"{args.lora_target_modules} (r={args.lora_r}, alpha={args.lora_alpha}, dropout={args.lora_dropout})"
        )

    trainer = SFTTrainer(
        model=model,
        tokenizer=tokenizer,
        train_dataset=train_dataset,
        eval_dataset=eval_dataset,
        args=training_args,
        peft_config=peft_config,
    )

    trainer.train()

    print(f"\nSaving model to {args.output_dir}")
    trainer.save_model(str(args.output_dir))
    tokenizer.save_pretrained(str(args.output_dir))
    print("Training complete.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Train Qwen on the macro-action dataset for behavior cloning."
    )
    parser.add_argument(
        "--data-path",
        type=Path,
        default=Path("data/macro/macro_dataset.jsonl.gz"),
        help="Path to macro dataset JSONL / JSONL.GZ (default: data/macro/macro_dataset.jsonl.gz)",
    )
    parser.add_argument(
        "--model",
        default="Qwen/Qwen3-4B",
        help="Base model to fine-tune (default: Qwen/Qwen3-4B)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("checkpoints/openra-bc-qwen"),
        help="Output directory for the trained model (default: checkpoints/openra-bc-qwen)",
    )
    parser.add_argument(
        "--max-rows",
        type=int,
        default=None,
        help="Maximum macro rows to load (default: all)",
    )
    parser.add_argument(
        "--max-episodes",
        type=int,
        default=None,
        help="Maximum unique episodes to use (default: all)",
    )
    parser.add_argument(
        "--val-ratio",
        type=float,
        default=0.1,
        help="Episode-level validation split ratio (default: 0.1)",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=7,
        help="Random seed for episode split (default: 7)",
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
        help="Per-device train batch size (default: 2)",
    )
    parser.add_argument(
        "--eval-batch-size",
        type=int,
        default=2,
        help="Per-device eval batch size (default: 2)",
    )
    parser.add_argument(
        "--grad-accum",
        type=int,
        default=4,
        help="Gradient accumulation steps (default: 4)",
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
        help="Maximum sequence length (default: 1024)",
    )
    parser.add_argument(
        "--logging-steps",
        type=int,
        default=10,
        help="Log every N steps (default: 10)",
    )
    parser.add_argument(
        "--save-steps",
        type=int,
        default=500,
        help="Save every N steps (default: 500)",
    )
    parser.add_argument(
        "--eval-steps",
        type=int,
        default=200,
        help="Evaluate every N steps when eval split exists (default: 200)",
    )
    parser.add_argument(
        "--save-total-limit",
        type=int,
        default=2,
        help="Maximum number of checkpoints to keep (default: 2)",
    )
    parser.add_argument(
        "--warmup-ratio",
        type=float,
        default=0.03,
        help="Warmup ratio (default: 0.03)",
    )
    parser.add_argument(
        "--lr-scheduler-type",
        default="cosine",
        help="Learning rate scheduler type (default: cosine)",
    )
    parser.add_argument(
        "--gradient-checkpointing",
        action="store_true",
        help="Enable gradient checkpointing",
    )
    parser.add_argument(
        "--prepare-only",
        action="store_true",
        help="Load and split the dataset, then stop before training",
    )
    parser.add_argument(
        "--load-in-4bit",
        action="store_true",
        help="Load the base model with 4-bit quantization",
    )
    parser.add_argument(
        "--no-lora",
        action="store_true",
        help="Disable LoRA and fine-tune the base model directly",
    )
    parser.add_argument(
        "--lora-r",
        type=int,
        default=16,
        help="LoRA rank (default: 16)",
    )
    parser.add_argument(
        "--lora-alpha",
        type=int,
        default=32,
        help="LoRA alpha (default: 32)",
    )
    parser.add_argument(
        "--lora-dropout",
        type=float,
        default=0.05,
        help="LoRA dropout (default: 0.05)",
    )
    parser.add_argument(
        "--lora-target-modules",
        default="q_proj,k_proj,v_proj,o_proj,gate_proj,up_proj,down_proj",
        help="Comma-separated LoRA target modules (default: q_proj,k_proj,v_proj,o_proj,gate_proj,up_proj,down_proj)",
    )
    args = parser.parse_args()
    train(args)


if __name__ == "__main__":
    main()
