# OpenRA-RL Challenge: Teaching LLMs to Play Red Alert 🎮

[![OpenEnv](https://img.shields.io/badge/OpenEnv-Environment-blue)](https://meta-pytorch.org/OpenEnv/)
[![HuggingFace](https://img.shields.io/badge/🤗-HuggingFace-yellow)](https://huggingface.co/openenv)
[![License: GPL-3.0](https://img.shields.io/badge/License-GPL--3.0-green.svg)](LICENSE)

**Submission for the [OpenEnv Challenge: SOTA Environments to Drive General Intelligence](https://drive.google.com/file/d/1NASall4R84xAhoDdcaMwwJ78Ao3B-EK4/view)**

This repo contains simple training scripts for **OpenRA-RL** — an OpenEnv-compatible environment that lets AI agents play *Command & Conquer: Red Alert* through tool calls.

## 🏗️ Architecture

```
                    ┌─────────────────┐
                    │   LLM Agent     │  ← Trained via imitation learning
                    │  (Qwen/LLaMA)   │
                    └────────┬────────┘
                             │ JSON tool calls
                    ┌────────▼────────┐
                    │  OpenEnv API    │  ← reset() / step() / state()
                    │  (WebSocket)    │
                    └────────┬────────┘
                             │ gRPC
                    ┌────────▼────────┐
                    │   OpenRA Game   │  ← Modified C# engine
                    │  (Red Alert)    │
                    └─────────────────┘
```

## 🚀 Quick Start

### 1. Start the OpenRA-RL Environment

The OpenRA-RL game server runs in Docker. You need to build the image from source first (it's not on Docker Hub).

**Option A: Build Docker image from source** (run from the `OpenRA-RL/` repo directory):
```bash
# Clone OpenRA-RL if you haven't already
cd /path/to/OpenRA-RL

# Build the Docker image (takes ~10 min the first time — compiles the C# game engine)
docker build -t openra-rl .

# Run the server
docker run -p 8000:8000 openra-rl
```

**Option B: Use the `openra-rl` CLI** (recommended — handles Docker automatically):
```bash
# 1. Create and activate a virtual environment
python -m venv .venv

# Windows
.venv\Scripts\activate

# macOS / Linux
# source .venv/bin/activate

# 2. Install the openra-rl package
pip install openra-rl

# 3. Start the game server with a chosen difficulty (default: normal)
openra-rl server start --difficulty normal
```

The `--difficulty` flag sets the OpenRA built-in AI opponent level:

| Difficulty | Description | Recommended for |
|------------|-------------|-----------------|
| `easy` | Passive AI, slow build | Quick sanity tests |
| `normal` | Balanced AI (**default**) | **Data collection** — best trade-off between pressure and episode length |
| `hard` | Aggressive rush AI | Advanced data collection once `normal` episodes are collected |

To switch difficulty, stop the server and restart it:
```bash
openra-rl server stop
openra-rl server start --difficulty hard
```

> **Note:** The server takes 30–60 seconds to start up. Wait until you see the health check pass before running scripts. You can check with:
> ```bash
> curl http://localhost:8000/health
> ```

### 2. Collect Expert Data

Run the ScriptedBot (built-in expert) against OpenRA's AI and record the trajectories:

```bash
# Default: 10 episodes, 15 minutes each, on singles.oramap (128x128)
python scripts/collect_bot_data.py --episodes 10 --verbose

# Specify a different map
python scripts/collect_bot_data.py --episodes 10 --map crossfire.oramap --verbose

# Longer episodes for more combat data
python scripts/collect_bot_data.py --episodes 10 --max-minutes 20 --verbose

# Quick smoke test
python scripts/collect_bot_data.py --episodes 2 --max-minutes 2 --verbose
```

**Default map:** `singles.oramap` (128×128, standard 1v1). The server ships with 66 maps.

**List all available maps:**
```bash
docker exec openra-rl-server find /opt/openra/mods/ra/maps -name "*.oramap"
```

**Extract map preview images** (each `.oramap` is a ZIP containing `map.png`):

```powershell
# Windows PowerShell — extracts all 66 previews into map_previews/
$maps = docker exec openra-rl-server find /opt/openra/mods/ra/maps -name "*.oramap" |
        ForEach-Object { $_.Trim() }
New-Item -ItemType Directory -Force -Path map_previews | Out-Null
foreach ($mapPath in $maps) {
    $name = [System.IO.Path]::GetFileNameWithoutExtension($mapPath)
    docker cp "openra-rl-server:${mapPath}" "map_previews\${name}.zip"
    Expand-Archive -Path "map_previews\${name}.zip" -DestinationPath "map_previews\${name}_tmp" -Force
    Copy-Item "map_previews\${name}_tmp\map.png" "map_previews\${name}.png" -ErrorAction SilentlyContinue
    Remove-Item "map_previews\${name}.zip", "map_previews\${name}_tmp" -Recurse -Force -ErrorAction SilentlyContinue
}
```

```bash
# macOS / Linux — extracts all 66 previews into map_previews/
mkdir -p map_previews
docker exec openra-rl-server find /opt/openra/mods/ra/maps -name "*.oramap" | while read map; do
    name=$(basename "$map" .oramap)
    docker cp "openra-rl-server:$map" /tmp/${name}.zip
    unzip -p /tmp/${name}.zip map.png > map_previews/${name}.png 2>/dev/null
    rm /tmp/${name}.zip
done
```

Then open `map_previews/index.html` in a browser for an interactive preview grid with search.

> **Timing:** The server runs at ~23 steps/second, so each 15-minute episode ≈ 20,000 steps ≈ 35–40 MB of JSON. 10 episodes takes ~2.5 hours total.
>
> **Why 15 minutes?** The ScriptedBot builds a base for the first ~10 minutes, then attacks. You need 10+ minutes to capture actual combat data. Episodes end early if the game finishes (win/lose) before the time limit.

This saves trajectories to `data/episodes/` — each file is a list of `{step, observation, action, reward}` dicts, one per game step.

### 3. Train via Imitation Learning

Fine-tune a small LLM on the collected demonstrations using behavioral cloning:

```bash
# Inspect the data first (no GPU needed):
python scripts/train_imitation.py --data-dir data/episodes --prepare-only

# Train:
python scripts/train_imitation.py \
    --data-dir data/episodes \
    --model Qwen/Qwen3-4B \
    --epochs 3 \
    --output-dir checkpoints/openra-il
```

### 4. Evaluate

Score episodes using the shaped reward function:

```python
from rewards.shaped_reward import EvalReward
import json

reward_fn = EvalReward()

with open("data/episodes/episode_001.json") as f:
    trajectory = json.load(f)

scores = reward_fn.score_trajectory(trajectory)
print(scores)
# {'components': {'exploration': 0.45, 'base_progress': 0.8, ...}, 'total': 0.52}
```

## 📁 Repo Structure

```
openra-rl-challenge/
├── scripts/
│   ├── collect_bot_data.py     # Bot-vs-bot data collection
│   └── train_imitation.py      # Behavioral cloning with TRL SFTTrainer
├── rewards/
│   └── shaped_reward.py        # Evaluation reward (exploration + progress + combat)
├── notebooks/
│   └── openra_rl_demo.ipynb    # End-to-end demo notebook
├── requirements.txt
└── README.md
```

## 🎯 Reward Design

The evaluation reward scores episodes across 6 dimensions:

| Component | Weight | What it measures |
|-----------|--------|-----------------|
| **Exploration** | 0.20 | Fraction of map explored (fog of war cleared) |
| **Base Progress** | 0.20 | Buildings constructed & building type diversity |
| **Army Strength** | 0.15 | Number of units trained |
| **Combat Ratio** | 0.20 | Kill/death cost ratio |
| **Survival** | 0.10 | How long the agent survived |
| **Outcome** | 0.15 | Win (+1.0) / Lose (-0.3) |

> **Note**: This reward is used for **evaluation only**, not training. The imitation learning agent is trained purely on behavioral cloning from the ScriptedBot expert.

## 🔗 Links

- **Environment**: [OpenRA-RL on HuggingFace](https://huggingface.co/openenv)
- **OpenRA-RL Repo**: [GitHub](https://github.com/your-org/OpenRA-RL)
- **OpenEnv Framework**: [meta-pytorch/OpenEnv](https://github.com/meta-pytorch/OpenEnv)
- **Blog Post**: [HuggingFace Blog](https://huggingface.co/blog)

## 📄 License

GPL-3.0 — same as OpenRA.
