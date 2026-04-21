#!/usr/bin/env python3
"""Sync the allowlisted repo files into a Hugging Face Space git repo."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = REPO_ROOT / ".hf-space-sync.json"


def run_git(args: list[str], cwd: Path, capture_output: bool = False) -> str:
    """Run a git command and raise on failure."""
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        text=True,
        capture_output=capture_output,
    )
    return result.stdout.strip() if capture_output else ""


def load_config(path: Path) -> dict[str, Any]:
    """Load the Space sync configuration."""
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def render_front_matter(values: dict[str, Any]) -> str:
    """Render a minimal YAML front matter block."""
    lines = ["---"]
    for key, value in values.items():
        if isinstance(value, bool):
            rendered = "true" if value else "false"
        elif isinstance(value, (int, float)):
            rendered = str(value)
        else:
            text = str(value)
            rendered = f"\"{text}\"" if ":" in text else text
        lines.append(f"{key}: {rendered}")
    lines.append("---")
    lines.append("")
    return "\n".join(lines)


def resolve_included_files(repo_root: Path, patterns: list[str]) -> list[Path]:
    """Resolve include glob patterns into a stable, unique file list."""
    files: set[Path] = set()
    missing_patterns: list[str] = []

    for pattern in patterns:
        matches = [path for path in repo_root.glob(pattern) if path.is_file()]
        if not matches:
            missing_patterns.append(pattern)
            continue
        files.update(path.resolve() for path in matches)

    if missing_patterns:
        joined = ", ".join(missing_patterns)
        raise FileNotFoundError(f"No files matched include pattern(s): {joined}")

    return sorted(files, key=lambda path: path.relative_to(repo_root).as_posix())


def build_readme_content(source: Path, generated_cfg: dict[str, Any]) -> str:
    """Apply README generation rules on top of the source file."""
    content = source.read_text(encoding="utf-8")
    front_matter = generated_cfg.get("prepend_front_matter")
    if not front_matter:
        return content

    prefix = render_front_matter(front_matter)
    if content.startswith("---\n"):
        return content
    return prefix + content


def copy_allowlisted_files(
    repo_root: Path,
    target_root: Path,
    files: list[Path],
    generated: dict[str, Any],
) -> None:
    """Copy the allowlisted files into the target repo."""
    for source in files:
        relative = source.relative_to(repo_root)
        destination = target_root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)

        generated_cfg = generated.get(relative.as_posix())
        if generated_cfg and relative.name.lower() == "readme.md":
            destination.write_text(build_readme_content(source, generated_cfg), encoding="utf-8")
        else:
            shutil.copy2(source, destination)


def clear_target_tree(target_root: Path) -> None:
    """Remove all files from the target repo except the git metadata."""
    for child in target_root.iterdir():
        if child.name == ".git":
            continue
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()


def current_commit_short_sha(repo_root: Path) -> str:
    """Return the current git commit short SHA, or a fallback label."""
    try:
        return run_git(["rev-parse", "--short", "HEAD"], cwd=repo_root, capture_output=True)
    except subprocess.CalledProcessError:
        return "working-tree"


def build_remote_url(space_repo: str, username: str, token: str) -> str:
    """Construct the authenticated HF Space git URL."""
    return f"https://{username}:{token}@huggingface.co/spaces/{space_repo}"


def sync_space(
    repo_root: Path,
    config_path: Path,
    token: str,
    username: str,
    commit_message: str | None,
    dry_run: bool,
) -> None:
    """Clone the HF Space repo, replace its allowlisted files, and push."""
    config = load_config(config_path)
    space_repo = str(config["space_repo"])
    include_patterns = [str(pattern) for pattern in config.get("include", [])]
    generated = dict(config.get("generated", {}))

    if not include_patterns:
        raise ValueError("Config must define at least one include pattern")

    files = resolve_included_files(repo_root, include_patterns)
    source_sha = current_commit_short_sha(repo_root)
    message = commit_message or f"Sync Space files from {source_sha}"
    remote_url = build_remote_url(space_repo=space_repo, username=username, token=token)

    with tempfile.TemporaryDirectory(prefix="hf-space-sync-") as tmp_dir_str:
        tmp_dir = Path(tmp_dir_str)
        run_git(["clone", remote_url, str(tmp_dir)], cwd=repo_root)
        clear_target_tree(tmp_dir)
        copy_allowlisted_files(repo_root=repo_root, target_root=tmp_dir, files=files, generated=generated)

        status = run_git(["status", "--porcelain"], cwd=tmp_dir, capture_output=True)
        if not status:
            print(f"No Hugging Face Space changes to push for {space_repo}.")
            return

        print(f"Prepared {len(files)} file(s) for Space sync to {space_repo}.")
        if dry_run:
            print("Dry run enabled; skipping commit and push.")
            print(status)
            return

        run_git(["config", "user.name", "github-actions[bot]"], cwd=tmp_dir)
        run_git(["config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com"], cwd=tmp_dir)
        run_git(["add", "--all"], cwd=tmp_dir)
        run_git(["commit", "-m", message], cwd=tmp_dir)
        run_git(["push", "origin", "HEAD:main"], cwd=tmp_dir)
        print(f"Pushed Space update to https://huggingface.co/spaces/{space_repo}")


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(description="Sync allowlisted files to a Hugging Face Space repo")
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG_PATH,
        help=f"Path to the Space sync config (default: {DEFAULT_CONFIG_PATH})",
    )
    parser.add_argument(
        "--token",
        default=(os.environ.get("HF_TOKEN") or os.environ.get("HUGGINGFACE_HUB_TOKEN") or ""),
        help="Hugging Face write token. Defaults to HF_TOKEN or HUGGINGFACE_HUB_TOKEN.",
    )
    parser.add_argument(
        "--username",
        default=(os.environ.get("HF_USERNAME") or "hf-token"),
        help="Username to embed in the authenticated git URL (default: HF_USERNAME or hf-token).",
    )
    parser.add_argument(
        "--commit-message",
        default=None,
        help="Optional custom commit message for the Space repo.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Prepare the target tree and print the pending git status without pushing.",
    )
    return parser.parse_args()


def main() -> int:
    """CLI entry point."""
    args = parse_args()
    if not args.token:
        print("Missing Hugging Face token. Set HF_TOKEN or pass --token.", file=sys.stderr)
        return 1

    try:
        sync_space(
            repo_root=REPO_ROOT,
            config_path=args.config.resolve(),
            token=args.token,
            username=args.username,
            commit_message=args.commit_message,
            dry_run=args.dry_run,
        )
    except Exception as exc:
        print(f"Space sync failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
