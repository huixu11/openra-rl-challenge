#!/bin/sh
set -eu

log() {
    printf '[space-entrypoint] %s\n' "$*"
}

print_dir_status() {
    path="$1"

    if [ -e "$path" ]; then
        writable="no"
        if [ -w "$path" ]; then
            writable="yes"
        fi

        log "path=$path writable=$writable"
        ls -ld "$path" || true
    else
        log "path missing: $path"
    fi
}

log_python_diagnostics() {
    python - <<'PY'
import importlib.util
import os
import sys
from pathlib import Path

print(f"[space-entrypoint] python executable: {sys.executable}")
print(f"[space-entrypoint] python version: {sys.version.splitlines()[0]}")
print(f"[space-entrypoint] python Path.home(): {Path.home()}")
print("[space-entrypoint] python sys.path:")
for entry in sys.path:
    print(f"[space-entrypoint]   {entry}")

for name in ("hf_space_server", "openra_env", "openra_env.server.app"):
    try:
        spec = importlib.util.find_spec(name)
        origin = None if spec is None else getattr(spec, "origin", None)
        print(f"[space-entrypoint] module {name}: {origin}")
    except Exception as exc:
        print(f"[space-entrypoint] module {name}: ERROR {type(exc).__name__}: {exc}")

print(f"[space-entrypoint] env HOME: {os.environ.get('HOME')}")
print(f"[space-entrypoint] env XDG_CONFIG_HOME: {os.environ.get('XDG_CONFIG_HOME')}")
print(f"[space-entrypoint] env OPENRA_PATH: {os.environ.get('OPENRA_PATH')}")
PY
}

log_diagnostics() {
    log "startup diagnostics begin"
    log "pwd=$(pwd)"
    log "id=$(id 2>/dev/null || true)"
    log "umask=$(umask 2>/dev/null || true)"
    log "HOME=${HOME:-}"
    log "XDG_CONFIG_HOME=${XDG_CONFIG_HOME:-}"
    log "PYTHONPATH=${PYTHONPATH:-}"
    log "OPENRA_PATH=${OPENRA_PATH:-}"
    log "OPENRA_INTERNAL_BASE_URL=${OPENRA_INTERNAL_BASE_URL:-}"
    log "DISPLAY=${DISPLAY:-}"
    log "python=$(command -v python || true)"
    log "python_version=$(python --version 2>&1 || true)"

    if [ -n "${HOME:-}" ]; then
        print_dir_status "$HOME"
        print_dir_status "$HOME/app"
    fi

    if [ -n "${XDG_CONFIG_HOME:-}" ]; then
        print_dir_status "$XDG_CONFIG_HOME"
        print_dir_status "$XDG_CONFIG_HOME/openra"
        print_dir_status "$XDG_CONFIG_HOME/openra/Logs"
        print_dir_status "$XDG_CONFIG_HOME/openra/Replays"
        print_dir_status "$XDG_CONFIG_HOME/openra/Content/ra/v2"
    fi

    if [ -n "${OPENRA_PATH:-}" ]; then
        print_dir_status "$OPENRA_PATH"
        print_dir_status "$OPENRA_PATH/bin"
    fi

    if [ -d "$HOME/app" ]; then
        log "app root listing:"
        ls -la "$HOME/app" | sed -n '1,40p' || true
    fi

    if [ -d "${XDG_CONFIG_HOME:-}/openra/Content/ra/v2" ]; then
        mix_count="$(find "${XDG_CONFIG_HOME}/openra/Content/ra/v2" -maxdepth 1 -type f -name '*.mix' | wc -l | tr -d ' ')"
        log "ra content mix_count=$mix_count"
    fi

    if [ -d "${XDG_CONFIG_HOME:-}/openra/Logs" ]; then
        log "existing OpenRA logs:"
        find "${XDG_CONFIG_HOME}/openra/Logs" -maxdepth 1 -type f | sed -n '1,20p' || true
    fi

    log_python_diagnostics
    log "startup diagnostics end"
}

log "Starting Xvfb on display :99..."
Xvfb :99 -screen 0 1024x768x24 -ac +extension GLX +render -noreset &
XVFB_PID=$!

sleep 2
if ! kill -0 "$XVFB_PID" 2>/dev/null; then
    log "ERROR: Xvfb failed to start"
    exit 1
fi

log "Xvfb started (PID: $XVFB_PID)"
export DISPLAY=:99

cleanup() {
    log "Shutting down..."
    kill "$XVFB_PID" 2>/dev/null || true
    wait "$XVFB_PID" 2>/dev/null || true
    exit 0
}

trap cleanup TERM INT

log_diagnostics || true

log "Starting OpenRA-RL environment server..."
exec "$@"
