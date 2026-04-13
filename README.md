# OpenRA-RL Challenge

Training scripts for **OpenRA-RL** — an environment that lets AI agents play *Command & Conquer: Red Alert*.

## Why not use the published Docker image?

The published image (`ghcr.io/yxc20089/openra-rl:latest`) has a critical bug: the AI opponent never spawns, so every game is played against nobody and you get zero combat data. This repo includes two key files that fix this:

- **`Dockerfile`** — Builds the game server from source, pinned to commit `2e26b31c0f28b75d140e11d9023b56b17715adea` that includes the AI bot spawn fix. It clones both the OpenRA C# engine and the OpenRA-RL Python server from GitHub automatically — no other repos needed.
- **`scripts/scripted_bot.py`** — Vendored copy of the base `ScriptedBot` class from `OpenRA-RL/examples/`. This removes the need to have the `OpenRA-RL` repo cloned as a sibling directory. `collect_bot_data.py` imports it directly.

See [Bugs Found & Fixed](#bugs-found--fixed) for the full list of 9 bugs fixed.

## Prerequisites

- Docker
- Python 3.11+

## Reproduce

```bash
git clone <this-repo>
cd openra-rl-challenge

# 1. Build the fixed game server image (~5 min first time, cached after)
docker build -t openra-rl:local .

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

# 5. Collect data
python scripts/collect_bot_data.py --episodes 1 --max-minutes 5 --verbose
```

On Windows, set `$env:PYTHONUNBUFFERED="1"` before step 5 to see live output.

Output is saved to `data/episodes/`. You should see kills > 0, confirming the AI opponent is active:

```
Episode 1: DONE — TIME_LIMIT(5MIN) | 18585 steps | Kills: 2u/0b | Lost: 12u/5b
```

For a full collection run:

```bash
python scripts/collect_bot_data.py --episodes 10 --max-minutes 15 --verbose
```

Each 15-minute episode produces ~20,000 steps. 10 episodes takes ~2.5 hours.

### Normal AI vs Normal AI

To collect data where the Python bot mimics OpenRA's built-in normal AI logic:

```bash
python scripts/collect_bot_data.py --episodes 10 --max-minutes 15 --bot normal --verbose
```

This uses `NormalAIBot` — a Python reimplementation of OpenRA's `ModularBot@NormalAI` with weighted unit production, dynamic base building, squad management, and economy logic ported from `ai.yaml`.

### Train (optional)

```bash
python scripts/train_imitation.py \
    --data-dir data/episodes \
    --model Qwen/Qwen3-4B \
    --epochs 3 \
    --output-dir checkpoints/openra-il
```

## Repo Structure

```
openra-rl-challenge/
├── Dockerfile                  # Builds the fixed game server image
├── scripts/
│   ├── collect_bot_data.py     # Data collection (--bot scripted|normal)
│   ├── scripted_bot.py         # Base ScriptedBot (vendored from OpenRA-RL)
│   ├── normal_ai_bot.py        # NormalAIBot — Python port of OpenRA's normal AI
│   └── train_imitation.py      # Behavioral cloning trainer
├── rewards/
│   └── shaped_reward.py        # Evaluation reward function
├── requirements.txt
└── README.md
```

## Bugs Found & Fixed

The published Docker image (`ghcr.io/yxc20089/openra-rl:latest`) has bugs that prevent data collection. The `Dockerfile` and `collect_bot_data.py` in this repo contain all fixes. Using `docker build` from this repo is required.

### Docker image bugs (fixed by the Dockerfile)

| # | Bug | Root Cause | Fix |
|---|-----|-----------|-----|
| 1 | **AI opponent never spawns** (critical) | `OpenRA.Game.dll` is built from an old commit missing `spectate` and `slot_bot` lobby commands in `LoadMap`. The AI player slot is silently dropped. | Dockerfile pins OpenRA to commit `2e26b31c0f28b75d140e11d9023b56b17715adea` which includes fix `8c96a76b4c`. |
| 2 | **Invalid bot type** | `BOT_TYPE=hard` is passed directly to OpenRA, which only accepts `rush`/`normal`/`turtle`/`naval`. Unrecognized types are silently ignored. | Rebuilt image includes `BOT_TYPE_MAP` that translates `hard` -> `normal`. |

### Script bugs (fixed in `collect_bot_data.py`)

| # | Bug | Fix |
|---|-----|-----|
| 3 | **Reward off-by-one** — each entry's reward came from the previous action | Reordered loop: capture obs/action, call `step()`, then record reward |
| 4 | **Soviet barracks (`barr`) missing from rally points** — infantry don't rally | Override `_handle_rally_points` to include `barr` |
| 5 | **Terminal entry duplicated last reward** | Set terminal entry reward to `0.0` |
| 6 | **Summary shows empty string instead of "timeout"** | Changed `get("result", "timeout")` to `get("result") or "timeout"` |
| 7 | **Missing `done` flag in trajectory entries** | Added `"done": result.done` to every entry |
| 8 | **Map dimensions wrong (128x128 vs 112x54)** — targets outside playable area | `_get_map_size` now updates cache when smaller dimensions are observed |
| 9 | **Bot leaves enemy base after first contact** | Added `_enemy_base_pos` to remember and re-attack the discovered location |

## License

GPL-3.0
