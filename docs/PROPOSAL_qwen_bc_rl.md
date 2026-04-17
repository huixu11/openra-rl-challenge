# Proposal: Qwen 3.5 Behavior Cloning and RL for OpenRA-RL

## Objective

Train a strong RTS agent for this repo using the collected demonstration data from `scripts/collect_bot_data.py`, then improve it with reinforcement learning against the OpenRA-RL environment.

The target is not just "produce valid JSON actions". The target is:

- win more often against `easy`, then `normal`
- preserve the strong macro behaviors already present in the scripted and normal bot data
- avoid policy collapse during RL fine-tuning
- keep the system practical to train with the current data and infrastructure

## Recommendation

Use **behavior cloning first**, then **constrained RL**, but do **not** make the final system a pure token-level imitation model over raw low-level commands.

The better plan is:

1. Train Qwen 3.5 on the collected data as a **macro policy**.
2. Keep a deterministic Python controller to translate macro decisions into concrete OpenRA command JSON.
3. Fine-tune the policy with RL using shaped rewards plus match outcome, while regularizing toward the BC policy.

This is better than direct "LLM outputs every command from scratch" for three reasons:

- the collected action space is noisy and high-entropy at the per-unit command level
- long-horizon online RL on raw JSON tokens will be expensive and unstable
- the repo already has strong hand-coded execution logic that should be reused instead of discarded

## Why Plain BC -> RL Is Not Enough

The key problem with raw step-by-step command imitation is that it has limits:

- it flattens observations into text, which is workable but lossy
- it learns from every surviving action equally, even when many steps are low-information
- it predicts raw command arrays, which mixes strategic decisions with execution noise
- RL on top of that representation will spend too much capacity learning syntax and repair behavior instead of strategy

The current reward helper in [rewards/shaped_reward.py](/C:/Users/huixu3/code/openrarl/openra-rl-challenge/rewards/shaped_reward.py) is also evaluation-oriented, not yet a robust training reward on its own.

## Is The Current Macro Dataset Good Enough?

Yes, as the primary training artifact.

The compact macro dataset is good for:

- behavior cloning
- Colab-friendly storage and upload
- filtering and episode-level evaluation

The old full trajectory JSON format was **not** a good long-term primary training format if one episode was around 1 GB.

Why it is inefficient:

- each step stores a full observation snapshot
- consecutive states are highly redundant
- many steps are economically routine or nearly no-op
- raw low-level commands preserve execution noise that the model does not need to imitate exactly

So the answer is:

- **yes for the main BC corpus**
- **no for old full step-by-step JSON as the main corpus**

## Recommended Data Strategy

Use a two-tier dataset instead of storing everything in the same format.

### Tier 1: Compact BC training set

This should be the main supervised dataset.

Instead of storing every step, store only:

- every `N`th step, for example every `10-20` environment steps
- steps where the action is non-trivial
- steps near major events:
  - first scout
  - first barracks / war factory / refinery / tech transition
  - first combat contact
  - attack launch
  - retreat / regroup
  - terminal combat

And store a compact record, not the full raw observation dump.

Recommended fields:

- episode id
- step / tick
- map / faction / opponent / result
- compact state summary
- compact macro action label
- optional raw action for traceability

Preferred formats:

- `jsonl.gz` for simplicity
- `parquet` if you want efficient columnar filtering later

This is the dataset that should feed Qwen BC.

### Tier 2: Replay archive

Keep the `.orarep` replay for every episode even if you do not keep the full JSON.

Purpose:

- later relabeling
- failure review
- qualitative evaluation
- rebuilding compact datasets with improved extractors

Replays are much smaller than full step-by-step JSON and are worth keeping broadly.

## Proposed System

### Policy split

Use two layers:

- **Planner / policy model**: Qwen 3.5 predicts a compact macro action.
- **Executor / controller**: Python code converts the macro action into valid OpenRA commands.

Examples of macro actions:

- `build(barr)`
- `train(e1, count=5)`
- `expand_to_ore_patch(x, y)`
- `attack(enemy_base)`
- `defend(base_center)`
- `tech_up(weap)`
- `scout(unexplored_quadrant=NE)`

This preserves the advantages of an LLM policy while shrinking the action space to something RL can optimize.

### Training stages

#### Stage 0: Data audit and cleanup

Before training, build a dataset audit pass over the collected macro dataset rows.

Goals:

- measure action distribution and no-op rate
- group near-duplicate consecutive samples
- label samples by opponent, map, faction, episode result, and game phase
- identify invalid or contradictory actions
- compute simple quality scores for each trajectory

Expected outcome:

- a filtered dataset for BC
- a "high-value" subset for early RL warm start

#### Stage 1: Behavior cloning

Train Qwen 3.5 on demonstration data, but target **macro decisions**, not raw command dumps.

Recommended choices:

- use LoRA or QLoRA instead of full fine-tuning
- start from a small or medium Qwen 3.5 instruct checkpoint that fits your hardware
- keep prompts structured with labeled sections instead of a single prose paragraph
- train on episode-level train/val splits, not random row splits
- overweight decisive states:
  - first tech transitions
  - scouting decisions
  - first attack launch
  - defense reactions
  - terminal combat windows

Output format should be a tight schema, for example:

```json
{
  "intent": "attack",
  "group": "main_army",
  "target": "visible_enemy_building",
  "target_id": 1234,
  "reason": "enemy_base_exposed"
}
```

The `reason` field is optional for training analysis and can be dropped during deployment if latency matters.

#### Stage 2: Offline policy improvement

Before online RL, add an offline improvement phase.

This is the main upgrade over a naive BC -> PPO plan.

Options:

- weighted BC, where winning episodes and higher-value segments get larger weight
- advantage-weighted imitation using episode return or shaped score
- pairwise ranking / preference training between better and worse actions from similar states

Why this stage matters:

- it is much cheaper than online RL
- it uses the collected data more efficiently
- it improves policy quality before expensive rollouts

#### Stage 3: Online RL

After BC and offline improvement, run online RL in the real environment.

Recommended setup:

- initialize from the BC checkpoint
- use short-to-medium horizon rollouts first
- start against `easy`, then mix `easy` and `normal`
- keep a KL penalty to the BC policy
- keep strict action validation in the controller
- save frequent checkpoints and run head-to-head evaluation after each training block

Suggested reward mix:

- match outcome as the anchor objective
- shaped reward for dense guidance:
  - exploration
  - base progress
  - army strength
  - combat efficiency
  - survival
- penalties for:
  - invalid macro actions
  - repeated contradictory macro actions
  - long idle windows with no scouting, production, or attack

I would not start with unconstrained GRPO over raw token sequences. If you want a language-model-native RL method, use it only after the policy already emits a small, rigid macro schema.

#### Stage 4: Curriculum and robustness

Expand the training curriculum gradually:

1. scripted bot demos only
2. normal bot demos
3. BC evaluation against `easy`
4. RL against `easy`
5. mixed RL against `easy` and `normal`
6. map and faction diversification

Later, add self-play only after the policy is stable against fixed opponents. Self-play too early will make debugging much harder.

## Model and Data Design

### Input representation

Do not rely only on a free-form prompt template.

Use a more stable prompt template with explicit sections:

- economy
- power
- own army summary
- own base summary
- visible enemy summary
- production queues
- alerts
- explored percent
- current strategic phase

If possible, add a compact machine-readable block after the natural-language summary. That gives you better long-term flexibility for ablations.

### Action representation

Prefer macro actions over raw low-level actions.

Use the compact macro dataset as the final training corpus.

That gives you a direct benchmark path:

- macro BC model
- macro BC + offline improvement
- macro BC + RL

### Data quality strategy

Not all collected steps should be treated equally.

Prioritize:

- winning episodes
- episodes with meaningful combat
- states close to important transitions
- states where the scripted or normal bot took non-trivial actions

Downweight:

- repeated idle states
- repeated rally / housekeeping states
- long runs of essentially identical economy upkeep

## How Much Data To Collect

Do not think in terms of "collect as many 1 GB episodes as possible". That is the wrong unit.

The right unit is:

- number of **useful decision points**
- diversity of maps / factions / opponents
- coverage of key strategic transitions

### Practical starting target

For a first serious BC model, I recommend:

- `50-100` total episodes collected
- split across:
  - `25-50` scripted bot episodes
  - `25-50` normal bot episodes

That is enough to build:

- a macro dataset
- a first offline evaluation split

### If the first model works

Then scale to:

- `200-500` total episodes

Only do this after you confirm that:

- validation performance is still improving
- live win rate is still moving
- new episodes are adding behavior diversity rather than repeating the same build order

### Approximate useful sample target

For macro BC, a reasonable first target is:

- `100k-300k` compact training examples

That is usually more useful than storing millions of raw step snapshots.

If you sample every 10-20 steps and keep event-heavy windows, you can reach that scale with a manageable number of episodes and storage.

## Is It Efficient?

In its current full-JSON-per-step form: **no**.

For this repo, 1 GB per episode is too expensive to be the default BC corpus because:

- storage scales badly
- loading and preprocessing become slow
- most bytes are redundant
- training signal per byte is poor

It becomes efficient if you use:

- replay for everything
- compact macro rows for BC

That is the right tradeoff.

## Evaluation Plan

Use evaluation that reflects the actual goal, not just training loss.

Primary metrics:

- win rate vs `easy`
- win rate vs `normal`
- mean game length
- invalid action rate
- surrender / deadlock rate

Secondary metrics:

- first attack timing
- exploration percent by fixed timestamps
- unit and building kill counts
- resource float over time
- army value over time

Keep a fixed benchmark suite:

- same maps
- same seeds where possible
- same opponent settings
- same replay capture process

## Recommended Repo Changes

### Near-term

Add:

- `scripts/audit_dataset.py`
- `scripts/train_bc_qwen.py`
- `scripts/eval_policy.py`
- `docs/EXPERIMENT_PLAN_bc_rl.md`

Update:

- [scripts/train_bc_qwen.py](/C:/Users/huixu3/code/openrarl/openra-rl-challenge/scripts/train_bc_qwen.py)
  - keep it focused on compact macro-policy BC
  - add episode-level validation split support
  - add sample weighting hooks

### Medium-term

Add a controller layer such as:

- `policies/macro_controller.py`
- `policies/qwen_policy.py`
- `policies/action_schema.py`

This will separate:

- what the model decides
- how the environment command JSON is produced

That separation is important for RL stability.

## Risks

Main risks:

- too much no-op and housekeeping data in the demonstrations
- direct token RL learns syntax repair instead of strategy
- reward hacking against shaped metrics
- rollout cost becomes too high if every experiment uses full long matches
- policy overfits to one opponent or faction mix

Mitigations:

- filter and weight the dataset
- use macro actions
- use KL regularization during RL
- evaluate against multiple opponents and maps
- maintain replay-based failure review after each milestone

## Concrete Recommendation

If you want the most practical plan for this repo, I recommend:

1. Collect a macro-action dataset directly with `scripts/collect_bot_data.py`.
2. Fine-tune a Qwen 3.5 model with LoRA on the macro dataset.
3. Add offline weighted imitation or ranking before online RL.
4. Run constrained online RL with shaped reward plus win/loss, anchored to the BC policy.

## Success Criteria

Phase 1 success:

- model produces valid macro actions consistently
- BC model improves offline validation and live evaluation against scripted or heuristic baselines

Phase 2 success:

- RL model exceeds BC win rate against `easy`
- RL improves combat and exploration without collapsing build order quality

Phase 3 success:

- model reaches stable improvement against `normal`
- replay review shows sensible scouting, expansion, and attack timing rather than reward exploits

## Bottom Line

Using Qwen 3.5 for BC and then RL is reasonable, but the strongest version of that idea in this repo is:

**Qwen 3.5 as a macro-policy, Python as the executor, offline improvement before online RL, and KL-constrained RL instead of raw free-form token optimization.**
