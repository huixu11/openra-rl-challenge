# Normal AI Gap Backlog

Last updated: 2026-04-13

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
- targeted harvester reassignment toward better coarse resource patches
- OpenRA-like unit production timing and requested build handling
- source-correct building costs for the main `NormalAI` land-building set
- closer-to-source building fractions, limits, delays, and refinery/power priorities
- coarse `spatial_map`-backed resource patch clustering for refinery and expansion targeting
- broader `BaseBuilderBotModule` placement annuli (`2..20`, defenses `5..20`)
- refinery placement biased toward nearby resource patches instead of only the first conyard ring
- broader `NormalAI` land-building coverage, including `gap` and `mslo`
- conservative terrain-index plus open-water-gated naval structure logic for `spen` / `syrd`
- variant-aware handling for `afld` / `afld.ukraine`
- source-like structure-production pacing (`25` / `125` tick checks with random bonus)
- bridge-compatible structure placement backoff / resume using `CANCEL_PRODUCTION`
- stronger reservation for mandatory refinery / power structure spend
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
- a later spatial-map/basebuilder pass moved the first dynamic second-refinery
  build forward to roughly `1.1` minutes in a fresh `3` minute validation on
  `singles.oramap`, instead of appearing only near the end of the time-limit
  window
- a later harvester/resource-map pass issued targeted patch-based `HARVEST`
  commands by tick `12000` in direct validation, confirming that the Python bot
  now reassigns harvesters using coarse resource-patch signals

The Python bot still does not have full parity with OpenRA's `NormalAI`, but
full parity is not required for the current target of building a much stronger
bot that can beat easier opponents.

When `EasyAI` appears to develop a richer or broader base than the Python bot,
that should be treated as a remaining Python-port gap, not as proof that source
OpenRA `EasyAI` is meant to economically out-develop source `NormalAI`.

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
- now parses `spatial_map` into coarse resource patches
- now uses those patches for refinery-target bias and expansion-target bias
- still does not reproduce OpenRA's true per-indice threat/resource bookkeeping

Why it matters:
- this is a central dependency for smart refinery placement, harvester
  reassignment, and MCV expansion

Status:
- `partial`

What is still missing:
- exact OpenRA indice/grid scoring
- nearby-indice threat summaries instead of simple visible-enemy counting
- resource-creator actor inputs
- harvester reassignment driven by patch saturation / low-yield detection

Priority for current goal:
- `high`

Why:
- the bridge now exposes enough map data for a useful approximation, so this is
  no longer blocked and should be used more aggressively in economy logic

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
- now has:
  - idle harvester `HARVEST`
  - harvester replacement requests toward target count
  - simplified target-count logic for more than one harvester
  - basic visible-threat retreat toward refineries/base
  - coarse patch-based targeted harvester reassignment using `spatial_map`
    resource clustering
  - cooldowns to avoid reassign-spam on the same harvester

Status:
- `partial`

What is still feasible:
- add stronger threat-based retreat heuristics using visible enemies
- tune harvester targets so the bot does not overspend on harvesters late
- rebuild refinery/economy state more reliably after major combat losses

What is blocked:
- exact resource-aware reassignment parity with OpenRA's indice grid
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
- buildings-being-produced accounting during build choice
- water-aware gating for naval production structures
- variant-aware placement

Python status:
- now correctly places completed items from both `Building` and `Defense`
  queues, which fixed the worst late-game build-churn issue
- now uses broader annulus targets closer to source defaults
- now biases refinery placement toward coarse resource patches from `spatial_map`
- now has bridge-compatible failed-placement cancel/backoff/resume logic
- now gates naval structures behind terrain-index-validated open-water candidate
  checks inside the current buildable area
- still does not have the real legality-aware placement search from OpenRA
- still does not have exact `CanPlaceBuilding` legality checks
- still does not have exact water/buildability parity for naval placement

Status:
- `partial`

What is feasible now:
- use richer annulus search patterns and retry tracking
- bias defense placement toward the known enemy base
- use coarse resource patches to steer refinery and expansion placement
- keep the widened retry sweep and queue backoff tied to source `BaseBuilder`
  intent under bridge constraints
- improve placement quality for defenses, tech, and utility structures
- keep land-macro structure selection aligned to `BaseBuilderBotModule@normal`

What is blocked:
- exact legality-aware search without buildability queries
- exact naval placement parity without true water/buildability visibility

Priority for current goal:
- `high`

Why:
- source-like macro is materially closer now, but crowded-base legality and
  exact naval placement still matter for consistency

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

1. Push the new coarse resource-map data deeper into economy and expansion.
   - add harvester reassignment toward better patches where feasible
   - use patch saturation to decide when refinery recovery / rebuild should happen
   - keep expansion target choice tied to real resource patches instead of only grid search

2. Continue the combined combat-plus-economy stabilization pass.
   - improve post-contact stabilization after the first clash
   - avoid collapsing the home base while the main army is committed
   - make retreat/re-engage logic less binary
   - keep focus fire on live, high-value targets only

3. Tighten the remaining `BaseBuilderBotModule` consistency gaps.
   - reduce false placement retries in crowded bases
   - refine build-order retry behavior when placement fails repeatedly
   - keep naval placement conservative unless shoreline evidence is strong

4. Strengthen long-game economy in an OpenRA-aligned way.
   - preserve harvesters under visible threat
   - rebuild refinery/economy state reliably after major losses
   - cap overproduction of harvesters
   - make sure expansion and dynamic building do not starve combat production

5. Treat RL bridge changes as optional phase 2.
   - exact buildability / legality exposure
   - explicit water-terrain exposure
   - support powers
   - richer attack-event/state signals

## Practical Definition Of "Done Enough"

Within the current RL API, the Python bot should be considered "good enough"
when it has:

- stable economy with multiple harvesters
- multiple refineries once the base has production online
- coarse resource-map-backed refinery / expansion choices from `spatial_map`
- a second conyard/MCV expansion path
- source-correct macro costs and production/building choices close to OpenRA defaults
- no obviously broken power or repair behavior
- delayed/staged assaults closer to real NormalAI
- no obvious late-game build spam or repeated placement churn
- can consistently perform competitively against `EasyAI`

Full parity with the C# bot is still not possible without exact buildability /
water semantics and some richer bot-state support from the RL bridge.
