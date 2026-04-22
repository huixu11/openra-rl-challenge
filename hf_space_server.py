#!/usr/bin/env python3
"""FastAPI wrapper for Hugging Face Spaces with lazy OpenRA app loading."""

from __future__ import annotations

import importlib
import os
from pathlib import Path

import uvicorn
from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

app = FastAPI()

OPENRA_MOD = os.environ.get("OPENRA_MOD", "ra")
_openra_module = None
_openra_mounted = False


def _load_openra_module():
    global _openra_module
    if _openra_module is None:
        _openra_module = importlib.import_module("openra_env.server.app")
    return _openra_module


@app.get("/")
async def root():
    return {
        "status": "ok",
        "service": "openra-rl-space",
        "openra_loaded": _openra_module is not None,
        "openra_mounted": _openra_mounted,
    }


@app.get("/health")
async def health():
    return {"status": "healthy"}


@app.get("/openra-status")
async def openra_status():
    return {
        "loaded": _openra_module is not None,
        "mounted": _openra_mounted,
        "module": str(_openra_module) if _openra_module is not None else None,
        "mount_path": "/openra" if _openra_mounted else None,
    }


@app.post("/debug-import")
async def debug_import():
    try:
        mod = _load_openra_module()
        return {"ok": True, "module": str(mod)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/mount-openra")
async def mount_openra():
    global _openra_mounted

    if _openra_mounted:
        return {"ok": True, "mounted": True, "path": "/openra"}

    try:
        mod = _load_openra_module()
        if not hasattr(mod, "get_app"):
            raise RuntimeError("openra_env.server.app has no attribute 'get_app'")

        openra_app = mod.get_app()
        app.mount("/openra", openra_app)
        _openra_mounted = True
        return {"ok": True, "mounted": True, "path": "/openra"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _support_dir() -> Path:
    openra_path = Path(os.environ.get("OPENRA_PATH", "/opt/openra"))
    candidates = [openra_path / "Support"]

    xdg = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config")))
    candidates.append(xdg / "openra")
    candidates.append(Path.home() / ".openra")

    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[1]


def _replay_root() -> Path:
    return _support_dir() / "Replays" / OPENRA_MOD


def _logs_root() -> Path:
    return _support_dir() / "Logs"


def _latest_replay() -> Path | None:
    replay_root = _replay_root()
    if not replay_root.exists():
        return None

    replays = sorted(
        replay_root.rglob("*.orarep"),
        key=lambda candidate: candidate.stat().st_mtime,
        reverse=True,
    )
    return replays[0] if replays else None


def _resolve_allowed_path(raw_path: str) -> Path:
    candidate = Path(raw_path).resolve(strict=False)
    allowed_roots = (
        _replay_root().resolve(strict=False),
        _logs_root().resolve(strict=False),
    )

    if not any(_is_relative_to(candidate, root) for root in allowed_roots):
        raise HTTPException(
            status_code=400,
            detail=f"Path is outside allowed artifact roots: {raw_path}",
        )
    return candidate


def _delete_file(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except TypeError:
        if path.exists():
            path.unlink()


class ArtifactCleanupRequest(BaseModel):
    replay_paths: list[str] = Field(default_factory=list)
    delete_logs: bool = True


@app.get("/artifacts/replay")
async def download_replay(
    path: str | None = None,
    delete_after_download: bool = False,
):
    replay_path = _resolve_allowed_path(path) if path else _latest_replay()
    if replay_path is None or not replay_path.is_file():
        raise HTTPException(status_code=404, detail="Replay file not found")

    background = None
    if delete_after_download:
        background = BackgroundTasks()
        background.add_task(_delete_file, replay_path)

    return FileResponse(
        replay_path,
        filename=replay_path.name,
        media_type="application/octet-stream",
        background=background,
    )


@app.post("/artifacts/cleanup")
async def cleanup_artifacts(request: ArtifactCleanupRequest):
    deleted_replays: list[str] = []
    deleted_logs: list[str] = []
    missing_paths: list[str] = []
    errors: list[str] = []

    for raw_path in request.replay_paths:
        try:
            replay_path = _resolve_allowed_path(raw_path)
        except HTTPException as exc:
            errors.append(f"{raw_path}: {exc.detail}")
            continue

        if replay_path.suffix != ".orarep":
            errors.append(f"{raw_path}: not a replay file")
            continue

        if replay_path.is_file():
            _delete_file(replay_path)
            deleted_replays.append(str(replay_path))
        else:
            missing_paths.append(str(replay_path))

    if request.delete_logs:
        logs_root = _logs_root()
        if logs_root.exists():
            for log_path in sorted(
                candidate for candidate in logs_root.rglob("*") if candidate.is_file()
            ):
                _delete_file(log_path)
                deleted_logs.append(str(log_path))

    return {
        "deleted_replays": deleted_replays,
        "deleted_logs": deleted_logs,
        "missing_paths": missing_paths,
        "errors": errors,
    }


def main() -> None:
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8000,
        ws_ping_interval=None,
        ws_ping_timeout=None,
    )


if __name__ == "__main__":
    main()
