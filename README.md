# OpenRA-RL Challenge

https://youtu.be/YaO-KyHiXfo

Training scripts for **OpenRA-RL** - an environment that lets AI agents play *Command & Conquer: Red Alert*.

## Why not use the published Docker image?

The published image (`ghcr.io/yxc20089/openra-rl:latest`) has a critical bug: the AI opponent never spawns, so every game is played against nobody and you get zero combat data. This repo builds the server from source instead:

- **`Dockerfile`** - Builds the game server from source. It pins OpenRA to commit `8a5d224223e0498e006a7350a9767a87bd45a708` and clones the OpenRA-RL Python server from GitHub `main`. Rebuild with `--no-cache` when you need the latest merged OpenRA-RL changes.
- **`scripts/scripted_bot.py`** - Vendored copy of the base `ScriptedBot` class from `OpenRA-RL/examples/`. This removes the need to have the `OpenRA-RL` repo cloned as a sibling directory. `collect_bot_data.py` imports it directly.

See [Bugs Found & Fixed](#bugs-found--fixed) for the full list of 9 bugs fixed.

## Prerequisites

- Docker
- Python 3.11+

## Reproduce

```bash
git clone <this-repo>
cd openra-rl-challenge

# 1. Build the fixed game server image (~5 min first time, cached after)
docker build --no-cache -t openra-rl:local .

# 2. Start the server
docker run -d -p 8000:8000 --name openra-rl-server -e BOT_TYPE=easy openra-rl:local

# 3. Wait ~30 seconds, then verify it's up
curl http://localhost:8000/health

# 4. Install Python dependencies
python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS/Linux:
# source .venv/bin/activate
pip install openra-rl

# 5. Collect compact macro-policy data
python scripts/collect_bot_data.py --episodes 10 --max-minutes 15 --bot normal --verbose
```

This uses `NormalAIBot` - a Python reimplementation of OpenRA's `ModularBot@NormalAI` with weighted unit production, dynamic base building, squad management, and economy logic ported from `ai.yaml`.

## Replay file

To find it in a running container:

```bash
docker exec openra-rl-server find /root/.config/openra/Replays/ra/{DEV_VERSION} -name '*.orarep' -type f
```

To copy one out:

```bash
docker cp openra-rl-server:/root/.config/openra/Replays/ra/{DEV_VERSION}/<file>.orarep .
```

To build a local OpenRA checkout and open a replay:

```powershell
cd C:\Users\huixu3\code\openrarl\OpenRA-RL\OpenRA
$env:Path = "C:\Program Files\dotnet;$env:Path"
.\make.cmd all

.\launch-game.cmd Game.Mod=ra Launch.Replay="C:\full\path\to\your.orarep"
```

If you want the replay to live in the standard local replay folder:

```powershell
New-Item -ItemType Directory -Force "$env:APPDATA\OpenRA\Replays\ra\{DEV_VERSION}" | Out-Null
Copy-Item "C:\Users\huixu3\code\openrarl\openra-rl-challenge\data\episodes\ra-RL-db15d685bc9d-2026-04-15T001937667Z.orarep" "$env:APPDATA\OpenRA\Replays\ra\{DEV_VERSION}\"
```

## Train (optional)

Macro-policy BC path:

```bash
python scripts/train_bc_qwen.py \
    --data-path data/macro/macro_dataset.jsonl.gz \
    --model Qwen/Qwen3-4B \
    --epochs 3 \
    --output-dir checkpoints/openra-bc-qwen
```

The collector now writes `data/macro/macro_dataset.jsonl.gz` directly. Upload only that file to Colab / Google Drive for training.

If you have older raw trajectory JSON from previous runs, you can still backfill a compact dataset with:

```bash
python scripts/build_macro_dataset.py \
    --data-dir data/episodes \
    --output-path data/macro/macro_dataset.jsonl.gz
```

Recommended starting scale for macro BC:

- `50-100` collected episodes
- at least `50k` compact macro rows for a first useful run
- `100k+` compact macro rows for a more serious run

If you only want the Colab path, the notebook is:

```text
notebooks/colab_train_bc.ipynb
```

## Repo Structure

```text
openra-rl-challenge/
|-- Dockerfile                  # Builds the fixed game server image
|-- scripts/
|   |-- collect_bot_data.py     # Direct macro-policy data collection (--bot scripted|normal)
|   |-- scripted_bot.py         # Base ScriptedBot (vendored from OpenRA-RL)
|   |-- normal_ai_bot.py        # NormalAIBot - Python port of OpenRA's normal AI
|   |-- build_macro_dataset.py  # Backfill compact macro dataset from older trajectory JSON
|   `-- train_bc_qwen.py        # Macro-policy BC trainer for Qwen
|-- rewards/
|   `-- shaped_reward.py        # Evaluation reward function
|-- requirements.txt
`-- README.md
```

## Bugs Found & Fixed

The published Docker image (`ghcr.io/yxc20089/openra-rl:latest`) has bugs that prevent data collection. The `Dockerfile` in this repo rebuilds against fixed OpenRA/OpenRA-RL sources, and `collect_bot_data.py` carries the collector-side fixes. Using `docker build` from this repo is required.

### Docker image bugs (fixed by the Dockerfile)

| # | Bug | Root Cause | Fix |
|---|-----|-----------|-----|
| 1 | **AI opponent never spawns** (critical) | `OpenRA.Game.dll` is built from an old commit missing `spectate` and `slot_bot` lobby commands in `LoadMap`. The AI player slot is silently dropped. | Dockerfile pins OpenRA to commit `8a5d224223e0498e006a7350a9767a87bd45a708`, which includes the required bot-spawn fixes. |
| 2 | **Invalid bot type** | `BOT_TYPE=hard` is passed directly to OpenRA, which only accepts `rush`/`normal`/`turtle`/`naval`. Unrecognized types are silently ignored. | Rebuilt image includes `BOT_TYPE_MAP` that translates `hard` -> `normal`. |

### Script bugs (fixed in `collect_bot_data.py`)

| # | Bug | Fix |
|---|-----|-----|
| 3 | **Reward off-by-one** - each entry's reward came from the previous action | Reordered loop: capture obs/action, call `step()`, then record reward |
| 4 | **Soviet barracks (`barr`) missing from rally points** - infantry don't rally | Override `_handle_rally_points` to include `barr` |
| 5 | **Terminal entry duplicated last reward** | Set terminal entry reward to `0.0` |
| 6 | **Summary shows empty string instead of "timeout"** | Changed `get("result", "timeout")` to `get("result") or "timeout"` |
| 7 | **Missing `done` flag in trajectory entries** | Added `"done": result.done` to every entry |
| 8 | **Map dimensions wrong (128x128 vs 112x54)** - targets outside playable area | `_get_map_size` now updates cache when smaller dimensions are observed |
| 9 | **Bot leaves enemy base after first contact** | Added `_enemy_base_pos` to remember and re-attack the discovered location |

The current collector no longer saves those full trajectory JSON files by default. It writes compact macro-policy rows directly into the training dataset.

## License

GPL-3.0
