# Normal AI Gap Backlog

Last updated: 2026-04-12

## Purpose

This document lists what the Python `scripts/normal_ai_bot.py` still lacks
compared with the real OpenRA bot implementation in `../openra-rl/openra`.

It is intended as a future implementation backlog, with each gap marked as:

- `feasible now`: can be implemented with the current RL bridge
- `partial`: can be approximated, but not fully matched
- `blocked by API`: the RL bridge does not expose enough data or actions

This document now prioritizes a practical goal:

- make the Python bot much stronger under the current bridge
- specifically, push it toward reliably defeating the OpenRA `EasyAI`
- do not treat full C# `NormalAI` parity as the immediate success criterion

## Optimization Principles

The OpenRA `NormalAI` in `../openra-rl/openra` should be treated as the
optimized baseline from the original development team.

When choosing what to implement next, prefer this order of reasoning:

1. Follow the real OpenRA bot behavior when the current RL bridge exposes enough
   information to do so.
2. When exact parity is not possible, build the closest stable approximation
   rather than inventing unrelated heuristics.
3. Only prefer bridge-specific tuning over source behavior when the OpenRA
   design clearly does not transfer under the exposed observations/actions.

In short:

- source-aligned first
- bridge-aware second
- ad hoc heuristics last

## Source Of Truth

Primary reference files:

- `../openra-rl/openra/mods/ra/rules/ai.yaml`
- `../openra-rl/openra/OpenRA.Mods.Common/Traits/Player/ModularBot.cs`
- `../openra-rl/openra/OpenRA.Mods.Common/Traits/BotModules/HarvesterBotModule.cs`
- `../openra-rl/openra/OpenRA.Mods.Common/Traits/BotModules/BaseBuilderBotModule.cs`
- `../openra-rl/openra/OpenRA.Mods.Common/Traits/BotModules/BotModuleLogic/BaseBuilderQueueManager.cs`
- `../openra-rl/openra/OpenRA.Mods.Common/Traits/BotModules/UnitBuilderBotModule.cs`
- `../openra-rl/openra/OpenRA.Mods.Common/Traits/BotModules/SquadManagerBotModule.cs`
- `../openra-rl/openra/OpenRA.Mods.Common/Traits/BotModules/BuildingRepairBotModule.cs`
- `../openra-rl/openra/OpenRA.Mods.Common/Traits/BotModules/PowerDownBotManager.cs`
- `../openra-rl/openra/OpenRA.Mods.Common/Traits/BotModules/McvExpansionManagerBotModule.cs`
- `../openra-rl/openra/OpenRA.Mods.Common/Traits/BotModules/ResourceMapBotModule.cs`

## Current Status Summary

The Python bot now has:

- total-credit accounting (`cash + ore`)
- basic harvester replacement requests
- idle harvester `HARVEST`
- OpenRA-like unit production timing and requested build handling
- more OpenRA-like building fractions, delays, and refinery/power priorities
- periodic repair behavior
- remembered power toggle behavior
- staged attack thresholds closer to `SquadManagerBotModule@normal`
- a coarse MCV expansion/request path
- correct handling for both `Building` and `Defense` structure queues
- placement of completed defense-queue structures
- a partial combat-quality pass:
  - bridge-tuned assault threshold
  - local target prioritization
  - limited regrouping
  - simple local attack/flee gating
  - a first post-contact recovery / stabilization loop
  - basic harvester-preservation behavior under visible threat

Observed directionally positive result:

- a recent `3` minute smoke test survived to time limit instead of dying early
- final state from that smoke test was strong enough to show the bot is now
  materially stronger than its earlier versions
- a later validation run confirmed that the late-game `silo` spam bug was
  caused by treating only `Building` as a structure queue, and that this has
  now been fixed
- a later combat pass improved visible target choice and squad behavior, but
  still did not make the bot reliably survive or win against stronger early
  pressure
- a later stabilization pass materially improved survival and trading after the
  first major fight, but still did not reach reliable `EasyAI` wins
- a later economy-preservation pass brought the implementation closer to the
  OpenRA harvester/recovery intent, but validation still showed meaningful
  variance between runs

The Python bot still does not have full parity with OpenRA's `NormalAI`, but
full parity is not required for the current target of building a much stronger
bot that can beat easier opponents.

## Practical Goal

Current target:

- make the Python bot strong enough to consistently threaten or defeat the
  OpenRA `EasyAI` in `../openra-rl/openra`

This changes how the backlog should be prioritized:

- features that increase combat effectiveness, stability, and economy under the
  current bridge are higher priority than bridge-level parity work
- RL bridge changes are useful later, but they are not required to keep making
  the bot substantially stronger

## Do We Need RL Bridge Changes?

For the current goal, not necessarily.

The current RL bridge is enough to continue improving:

- economy stability
- building/placement heuristics
- unit composition
- squad timing and force commitment
- attack/flee heuristics
- basic expansion

RL bridge changes become more important when the goal shifts from "make the bot
much better" to "match the real OpenRA C# bot closely."

## Gap List

### 1. ResourceMapBotModule

Source:
- `../openra-rl/openra/OpenRA.Mods.Common/Traits/BotModules/ResourceMapBotModule.cs`

What OpenRA has:
- a resource/threat grid over the map
- valuable resource region scoring
- nearby threat summaries
- refinery density checks by region
- inputs for harvester reassignment and MCV expansion

Python status:
- no equivalent resource map

Why it matters:
- this is a central dependency for smart refinery placement, harvester
  reassignment, and MCV expansion

Status:
- `blocked by API`

Reason:
- current observations do not expose resource cell locations, resource creator
  positions, or the derived resource-map grid used by OpenRA

Priority for current goal:
- `medium`, not `highest`

Why:
- this blocks full parity, but it does not block large improvements against
  easier opponents

### 2. HarvesterBotModule: low-effect reassignment and flee-to-dock

Source:
- `../openra-rl/openra/OpenRA.Mods.Common/Traits/BotModules/HarvesterBotModule.cs`

What OpenRA has beyond current Python bot:
- low-effect harvester detection
- reassignment to better resource regions
- enemy-avoidance path cost while searching for ore
- response to harvester attacks by docking/falling back
- no-resource cooldown logic

Python status:
- currently only:
  - idle harvester `HARVEST`
  - harvester replacement requests toward target count
  - simplified target-count logic for more than one harvester
  - basic visible-threat retreat toward refineries/base

Status:
- `partial`

What is still feasible:
- add threat-based retreat heuristics using visible enemies
- add cooldowns to avoid spamming the same harvester
- tune harvester targets so the bot does not overspend on harvesters late
- rebuild refinery/economy state more reliably after major combat losses

What is blocked:
- exact resource-aware reassignment
- exact dock behavior if the RL bridge does not expose a dedicated dock action

Priority for current goal:
- `high`

Why:
- long-lasting games still depend heavily on keeping the ore loop alive after
  the first major battle

### 3. BaseBuilder placement search

Source:
- `../openra-rl/openra/OpenRA.Mods.Common/Traits/BotModules/BotModuleLogic/BaseBuilderQueueManager.cs`

What OpenRA has:
- legal build search in annuli (`2..20` and defense `5..20`)
- refinery placement near resource regions
- defense placement toward enemy base
- failed-placement backoff and retry windows
- variant-aware placement

Python status:
- now correctly places completed items from both `Building` and `Defense`
  queues, which fixed the worst late-game build-churn issue
- still uses a simplified placement approach around the conyard
- still does not have the real failure-aware placement search from OpenRA

Status:
- `partial`

What is feasible now:
- use richer annulus search patterns and retry tracking
- bias defense placement toward the known enemy base
- add better retry/backoff when placement choices repeatedly fail
- improve placement quality for defenses, tech, and utility structures

What is blocked:
- exact legality-aware search without buildability queries
- proper refinery placement near resource fields without resource map data

Priority for current goal:
- `very high`

Why:
- placement quality still matters a lot, but the worst structure-queue churn
  bug has already been fixed

### 4. UnitBuilderBotModule full parity

Source:
- `../openra-rl/openra/OpenRA.Mods.Common/Traits/BotModules/UnitBuilderBotModule.cs`

What OpenRA has beyond current Python bot:
- queue rotation across all unit categories
- requested production handling
- army-share-deficit selection
- idle-base-unit cap integration
- unit delays
- aircraft rearm/reload building checks

Python status:
- partially ported:
  - queue rotation
  - requested production
  - share-deficit unit selection
  - min-cash gate
  - a modest ground-army bias for current bridge play
  - suppression of low-value late dogs / ships / extra harvesters once the
    target eco count is met

Still missing:
- idle-base-unit cap
- unit delays
- aircraft reload building constraints
- true queue/buildability parity with C#

Additional practical issue:
- the current bot may still drift into an imbalanced composition for the
  "beat EasyAI" goal, for example too much infantry or too many harvesters in
  some runs, or too little staying power after the first major engagement

Status:
- `partial`

Priority for current goal:
- `high`

### 5. SquadManagerBotModule state machine

Source:
- `../openra-rl/openra/OpenRA.Mods.Common/Traits/BotModules/SquadManagerBotModule.cs`
- `../openra-rl/openra/OpenRA.Mods.Common/Traits/BotModules/Squads/*`

What OpenRA has:
- separate squad types:
  - assault
  - rush
  - protection
  - air
  - naval
- leader/straggler regroup logic
- attack-or-flee heuristics
- local retargeting and danger scans
- rush logic with randomized squad thresholds

Python status:
- one simplified attack squad plus simple protection redirect
- now also has:
  - local visible-target prioritization
  - basic focus fire
  - limited regrouping around a squad leader
  - a simple local attack/flee gate
  - temporary recovery mode after major post-contact army loss
  - reduced recovery-time eco/expansion greed
  - a small reserve kept home for remote attacks
- much closer timers/thresholds than before, but still not a real OpenRA
  squad-state implementation

Status:
- `partial`

What is feasible now:
- more faithful thresholds and timers
- attack-force staging before assault
- separate buckets for ground/air/naval units
- simpler strength checks before committing
- regroup / pull-straggler behavior
- stop overcommitting once local strength turns bad
- better target prioritization against production, economy, and exposed units
- better post-contact stabilization after the first engagement
- better handoff between base defense and field assault
- better logic for when recovery mode should clear versus keep rebuilding
- less wasteful emergency-defense spending while rebuilding

What remains difficult:
- exact state-machine parity
- fuzzy combat-strength calculation parity

Priority for current goal:
- `very high`

Why:
- beating `EasyAI` depends much more on attack quality and force commitment than
  on exact bot parity features like support powers
- but the remaining gains now likely come from combining combat follow-through
  with stronger economic recovery, not from raw aggression alone

### 6. PowerDownBotModule full toggle behavior

Source:
- `../openra-rl/openra/OpenRA.Mods.Common/Traits/BotModules/PowerDownBotManager.cs`

What OpenRA has:
- 150-tick interval
- toggles selected structures off when in low power
- remembers toggled structures
- toggles them back on when power recovers

Python status:
- partially implemented approximation exists

Status:
- `partial`

Priority for current goal:
- `medium`

### 7. BuildingRepairBotModule periodic scan behavior

Source:
- `../openra-rl/openra/OpenRA.Mods.Common/Traits/BotModules/BuildingRepairBotModule.cs`

What OpenRA has:
- periodic repair-all scan every `107` ticks
- reactive repair when a building crosses a damage threshold after attack

Python status:
- partially implemented approximation exists

Status:
- `partial`

Priority for current goal:
- `medium`

### 8. McvExpansionManagerBotModule

Source:
- `../openra-rl/openra/OpenRA.Mods.Common/Traits/BotModules/McvExpansionManagerBotModule.cs`

What OpenRA has:
- MCV production requests to maintain conyard count
- idle MCV scanning
- deployment search around resource regions
- expansion mode switching
- moving/undeploying old conyards

Python status:
- a coarse approximation now exists:
  - extra `mcv` requests
  - simple move-and-deploy behavior

Status:
- `partial`

What is feasible now:
- request a second MCV when rich and under target conyard count
- move idle MCV toward coarse expansion targets
- deploy when sufficiently far from the current base
- tune expansion timing so it helps instead of starving frontline production

What is blocked:
- smart resource-region expansion
- real mode switching based on resource map

Priority for current goal:
- `medium`

Why:
- a second base is useful, but a bad expansion is worse than a strong main-base
  army when trying to beat `EasyAI`

### 9. SupportPowerBotModule

Source:
- `../openra-rl/openra/mods/ra/rules/ai.yaml`
- `../openra-rl/openra/OpenRA.Mods.Common/Traits/BotModules/SupportPowerBotModule.cs`

What OpenRA has:
- spy plane
- paratroopers
- parabombs
- nuke logic

Python status:
- no equivalent

Status:
- `blocked by API`

Reason:
- RL bridge actions do not expose support-power activation commands

Priority for current goal:
- `low`

Why:
- support powers would help, but they are not the first thing needed to beat
  `EasyAI`

### 10. ModularBot order throttling

Source:
- `../openra-rl/openra/OpenRA.Mods.Common/Traits/Player/ModularBot.cs`

What OpenRA has:
- queues many orders internally
- only issues about `ceil(pending_orders / 5)` each tick

Python status:
- emits commands immediately from all managers each step

Status:
- `partial`

Why it matters:
- prevents command bursts and changes how multiple modules interact

Priority for current goal:
- `medium`

Why:
- improving order pacing could help stability, but it is probably not the next
  biggest strength gain

## Recommended Next Implementation Order

For the goal of defeating `EasyAI`, the next work should focus on the highest
impact improvements available under the current bridge:

1. Fix build churn and placement/backoff behavior.
   - stop repeated silo spam
   - add cooldowns after failed or low-value dynamic build decisions
   - improve placement target selection for defenses and utility structures

2. Continue the combined combat-plus-economy stabilization pass.
   - improve post-contact stabilization after the first clash
   - avoid collapsing the home base while the main army is committed
   - make retreat/re-engage logic less binary
   - keep focus fire on live, high-value targets only

3. Strengthen long-game economy in an OpenRA-aligned way.
   - preserve harvesters under visible threat
   - rebuild refinery/economy state reliably after major losses
   - cap overproduction of harvesters
   - make sure expansion and dynamic building do not starve combat production

4. Improve coarse expansion only after the main-base bot is already strong.
   - second MCV timing
   - safer deployment heuristics

5. Treat RL bridge changes as optional phase 2.
   - resource map exposure
   - support powers
   - richer attack-event/state signals

## Practical Definition Of "Done Enough"

Within the current RL API, the Python bot should be considered "good enough"
when it has:

- stable economy with multiple harvesters
- a second conyard/MCV expansion path
- production and building choices close to OpenRA defaults
- no obviously broken power or repair behavior
- delayed/staged assaults closer to real NormalAI
- no obvious late-game build spam or placement churn
- can consistently perform competitively against `EasyAI`

Full parity with the C# bot is not possible without additional observation and
action support from the RL bridge.
