# OpenRA-RL Challenge

https://youtu.be/YaO-KyHiXfo

Training scripts for [**OpenRA-RL**](https://github.com/yxc20089/OpenRA-RL) - an environment that lets AI agents play *Command & Conquer: Red Alert*.

Huggingface env

https://huggingface.co/spaces/huixuv/openra-rl-challenge
## Prerequisites

- Docker (Optional if you want to run OpenRA-RL Env in local)
- Python 3.11+

## Reproduce

### huggingface env
https://huggingface.co/spaces/openra-rl/openra-rl-challenge

```bash
python scripts/collect_bot_data.py --url https://openra-rl-openra-rl-challenge.hf.space --episodes 1  --max-minutes 10 --bot normal --verbose
```
Replay will be saved to local file data/episodes/*.orarep

### local (Optional)
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

#### Replay file (Optional)

To find it in a running container:

```bash
docker exec openra-rl-server find /root/.config/openra/Replays/ra/{DEV_VERSION} -name '*.orarep' -type f
```

To copy one out:

```bash
docker cp openra-rl-server:/root/.config/openra/Replays/ra/{DEV_VERSION}/<file>.orarep .
```

## Watch replay
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
python scripts/collect_bot_data.py \
    --episodes 50 \
    --bot normal \
    --dataset-path data/macro/macro_dataset.jsonl.gz

python scripts/train_bc_qwen.py \
    --data-path data/macro/macro_dataset.jsonl.gz \
    --model Qwen/Qwen3-4B \
    --epochs 3 \
    --output-dir checkpoints/openra-bc-qwen
```

The collector now writes `data/macro/macro_dataset.jsonl.gz` directly. Upload only that file to Colab / Google Drive for training.

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
|   `-- train_bc_qwen.py        # Macro-policy BC trainer for Qwen
|-- rewards/
|   `-- shaped_reward.py        # Evaluation reward function
|-- requirements.txt
`-- README.md
```
## License

GPL-3.0
