#!/usr/bin/env python3
from fastapi import FastAPI
import uvicorn

app = FastAPI()

@app.get("/")
async def root():
    return {"status": "ok"}

@app.get("/health")
async def health():
    return {"status": "healthy"}

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

# #!/usr/bin/env python3
# """FastAPI wrapper that adds replay/log artifact endpoints for Spaces."""

# from __future__ import annotations

# import os
# from pathlib import Path

# from fastapi import BackgroundTasks, HTTPException
# from fastapi.responses import FileResponse
# from pydantic import BaseModel, Field

# from openra_env.server.app import app


# OPENRA_MOD = os.environ.get("OPENRA_MOD", "ra")

# @app.get("/")
# async def root():
#     return {"status": "ok", "service": "openra-rl-space"}


# @app.get("/health")
# async def health():
#     return {"status": "healthy"}
    
# def _is_relative_to(path: Path, root: Path) -> bool:
#     try:
#         path.relative_to(root)
#         return True
#     except ValueError:
#         return False


# def _support_dir() -> Path:
#     openra_path = Path(os.environ.get("OPENRA_PATH", "/opt/openra"))
#     candidates = [openra_path / "Support"]

#     xdg = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config")))
#     candidates.append(xdg / "openra")
#     candidates.append(Path.home() / ".openra")

#     for candidate in candidates:
#         if candidate.exists():
#             return candidate
#     return candidates[1]


# def _replay_root() -> Path:
#     return _support_dir() / "Replays" / OPENRA_MOD


# def _logs_root() -> Path:
#     return _support_dir() / "Logs"


# def _latest_replay() -> Path | None:
#     replay_root = _replay_root()
#     if not replay_root.exists():
#         return None

#     replays = sorted(
#         replay_root.rglob("*.orarep"),
#         key=lambda candidate: candidate.stat().st_mtime,
#         reverse=True,
#     )
#     return replays[0] if replays else None


# def _resolve_allowed_path(raw_path: str) -> Path:
#     candidate = Path(raw_path).resolve(strict=False)
#     allowed_roots = (_replay_root().resolve(strict=False), _logs_root().resolve(strict=False))

#     if not any(_is_relative_to(candidate, root) for root in allowed_roots):
#         raise HTTPException(status_code=400, detail=f"Path is outside allowed artifact roots: {raw_path}")
#     return candidate


# def _delete_file(path: Path) -> None:
#     try:
#         path.unlink(missing_ok=True)
#     except TypeError:
#         if path.exists():
#             path.unlink()


# class ArtifactCleanupRequest(BaseModel):
#     replay_paths: list[str] = Field(default_factory=list)
#     delete_logs: bool = True


# @app.get("/artifacts/replay")
# async def download_replay(path: str | None = None, delete_after_download: bool = False):
#     """Download a replay file by absolute path or fall back to the latest replay."""
#     replay_path = _resolve_allowed_path(path) if path else _latest_replay()
#     if replay_path is None or not replay_path.is_file():
#         raise HTTPException(status_code=404, detail="Replay file not found")

#     background = None
#     if delete_after_download:
#         background = BackgroundTasks()
#         background.add_task(_delete_file, replay_path)

#     return FileResponse(
#         replay_path,
#         filename=replay_path.name,
#         media_type="application/octet-stream",
#         background=background,
#     )


# @app.post("/artifacts/cleanup")
# async def cleanup_artifacts(request: ArtifactCleanupRequest):
#     """Delete downloaded replay files and transient OpenRA logs."""
#     deleted_replays: list[str] = []
#     deleted_logs: list[str] = []
#     missing_paths: list[str] = []
#     errors: list[str] = []

#     for raw_path in request.replay_paths:
#         try:
#             replay_path = _resolve_allowed_path(raw_path)
#         except HTTPException as exc:
#             errors.append(f"{raw_path}: {exc.detail}")
#             continue

#         if replay_path.suffix != ".orarep":
#             errors.append(f"{raw_path}: not a replay file")
#             continue

#         if replay_path.is_file():
#             _delete_file(replay_path)
#             deleted_replays.append(str(replay_path))
#         else:
#             missing_paths.append(str(replay_path))

#     if request.delete_logs:
#         logs_root = _logs_root()
#         if logs_root.exists():
#             for log_path in sorted(candidate for candidate in logs_root.rglob("*") if candidate.is_file()):
#                 _delete_file(log_path)
#                 deleted_logs.append(str(log_path))

#     return {
#         "deleted_replays": deleted_replays,
#         "deleted_logs": deleted_logs,
#         "missing_paths": missing_paths,
#         "errors": errors,
#     }


# def main() -> None:
#     import uvicorn

#     uvicorn.run(
#         app,
#         host="0.0.0.0",
#         port=8000,
#         ws_ping_interval=None,
#         ws_ping_timeout=None,
#     )


# if __name__ == "__main__":
#     main()
