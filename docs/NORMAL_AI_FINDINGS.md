# Normal AI Findings

Last updated: 2026-04-12

## Purpose

This note captures the current understanding of the Python `NormalAIBot`
implementation in `scripts/normal_ai_bot.py`, the real OpenRA source of truth
in the sibling repo, the bugs already found, and the fixes that have already
been applied. The goal is to make it easy to resume the port later.

## OpenRA Source Of Truth

The actual OpenRA bot implementation is available in the sibling repo:

- `../openra-rl/openra/mods/ra/rules/ai.yaml`
- `../openra-rl/openra/OpenRA.Mods.Common/Traits/Player/ModularBot.cs`
- `../openra-rl/openra/OpenRA.Mods.Common/Traits/BotModules/HarvesterBotModule.cs`
- `../openra-rl/openra/OpenRA.Mods.Common/Traits/BotModules/BaseBuilderBotModule.cs`
- `../openra-rl/openra/OpenRA.Mods.Common/Traits/BotModules/UnitBuilderBotModule.cs`
- `../openra-rl/openra/OpenRA.Mods.Common/Traits/BotModules/SquadManagerBotModule.cs`

Important observations from those files:

- `ai.yaml` wires `ModularBot@NormalAI` to a set of modular bot traits.
- `HarvesterBotModule.cs` contains real economy behavior that the Python bot
  does not yet implement, including:
  - idle-harvester scanning
  - low-effect / no-resource handling
  - harvester replacement
  - `InitialHarvesters`
- In `mods/ra/rules/ai.yaml`, `HarvesterBotModule@normal` uses:
  - `HarvesterTypes: harv`
  - `RefineryTypes: proc`
  - `InitialHarvesters: 4`

## Findings From Collected Data

Using:

```bash
python scripts/collect_bot_data.py --episodes 10 --max-minutes 15 --bot normal --verbose
```

the following issues were observed:

1. The bot appeared "stuck at low money", but the real issue was more specific:
   the Python code was checking only `obs.economy.cash`, while the environment
   also stores mined resources in `obs.economy.ore`.

2. Episode data showed the bot could still be mining while looking broke.
   Example pattern from recorded episodes:
   - `cash` dropped near `0` or `40`
   - `ore` continued to rise
   - total spendable resources were therefore higher than the bot believed

3. In the original run, episodes often peaked at only one harvester and ended
   with zero harvesters. This confirms the Python bot still lacks the real
   harvester-management loop from OpenRA.

4. The collector summary mislabeled eliminated games as `timeout` because it
   defaulted missing final `result` values to `timeout`.

5. After the barracks came online, infantry production could spend newly mined
   income fast enough to delay or starve the pending war factory (`weap`) in
   the fixed build order.

## Fixes Already Applied

### In `scripts/normal_ai_bot.py`

The following fixes have already been made:

- Added `_available_credits(obs)`:
  - total spendable credits = `obs.economy.cash + obs.economy.ore`

- Updated affordability checks to use total credits instead of cash-only:
  - base building
  - dynamic building
  - repair gating
  - basic unit-production gating

- Added `_pending_build_cost(obs)` and a reservation rule so unit production
  does not consume the credits needed for the next structure in the fixed build
  order.

This was enough to fix the immediate "economy build stalls before war factory"
problem.

### Additional Porting Pass Applied

After comparing against the OpenRA source in `../openra-rl/openra`, the Python
`NormalAIBot` was updated further to align with the original module logic:

- Added a minimal economy manager inspired by `HarvesterBotModule.cs`:
  - scans every `50` ticks
  - issues `HARVEST` to idle harvesters
  - requests replacement/new harvesters toward `max(InitialHarvesters, refinery_count)`
  - uses `InitialHarvesters = 4`

- Added a requested-unit queue so economy-driven requests (for example,
  harvesters) are handled ahead of normal production, similar to
  `UnitBuilderBotModule.cs`.

- Reworked unit production toward OpenRA's logic:
  - feedback interval `30` ticks
  - minimum production credits `500`
  - rotates queue categories (`Vehicle`, `Infantry`, `Plane`, `Ship`, `Aircraft`)
  - selects units by army-share deficit instead of plain weighted random choice

- Reworked dynamic base building to better follow `BaseBuilderBotModule@normal`:
  - excess power target grows with building count
  - refinery adequacy check now follows the normal-AI `0 + 2` rule
  - rich-economy extra-production rule uses the `8000` threshold
  - silo check triggers near `80%` resource storage
  - dynamic structure choice now uses a simplified version of the normal AI
    building fractions, limits, and delays

- Improved defense triggering to protect a wider set of important assets, not
  just the construction yard area.

### Later Feasible-Module Pass Applied

Another pass then implemented the remaining pieces that are practical with the
current RL bridge:

- Squad/attack behavior moved closer to `SquadManagerBotModule@normal`:
  - `SquadSize = 40`
  - randomized assault threshold (`40 + rand(0..29)`)
  - `AssignRolesInterval = 50`
  - `AttackForceInterval = 75`
  - periodic full re-attack/rush-style redirects

- Power behavior moved closer to `PowerDownBotModule`:
  - `150` tick toggle interval
  - tracks buildings toggled off by the bot
  - attempts to toggle them back on when excess power recovers

- Repair behavior moved closer to `BuildingRepairBotModule`:
  - periodic repair scan using a cooldown of `107` ticks

- Added a simplified `McvExpansionManagerBotModule` approximation:
  - requests an extra `mcv` when the bot is rich enough and under the target
    construction-yard count
  - moves idle MCVs toward coarse expansion targets
  - deploys an idle MCV when it has moved far enough from the current base

- Added a dedicated backlog note:
  - `docs/NORMAL_AI_GAP_BACKLOG.md`
  - this separates future work into `feasible now`, `partial`, and
    `blocked by API`

### In `scripts/collect_bot_data.py`

The collector was updated to:

- print `Cash`, `Ore`, and total credits in verbose logs
- infer outcomes more accurately:
  - `eliminated`
  - `time_limit(...)`
  - `step_limit`

This avoids confusing summaries where clearly eliminated games show up as
`timeout`.

## Validation Performed

Validation that has already been run:

- `python -m compileall scripts/normal_ai_bot.py scripts/collect_bot_data.py`
- short smoke tests with:

```bash
python scripts/collect_bot_data.py --episodes 1 --max-minutes 2 --bot normal --verbose --output-dir data/episodes_smoketest2
```

Observed result after the credit + reservation fix:

- the bot now saves up and successfully queues:
  - `powr -> barr/tent -> proc -> weap -> powr`
- the collector summary now reports `eliminated` instead of incorrectly using
  `timeout`

Observed result after the later OpenRA-aligned porting pass:

- short smoke test:

```bash
python scripts/collect_bot_data.py --episodes 1 --max-minutes 2 --bot normal --verbose --output-dir data/episodes_smoketest_openra_port
```

- the bot requested and trained multiple harvesters
- `harvester_count` reached `4` in the smoke test
- the bot still remains combat-weaker than the real C# `NormalAI`, but economy
  and production now behave substantially closer to the source logic

Observed result after the later feasible-module pass:

```bash
python scripts/collect_bot_data.py --episodes 1 --max-minutes 3 --bot normal --verbose --output-dir data/episodes_smoketest_gap_impl
```

- episode survived to the `3` minute time limit
- final summary from the smoke test:
  - result: `time_limit(3min)`
  - final units: `52`
  - final buildings: `8`
  - final composition heavily favored infantry plus multiple harvesters
- this is a large improvement over the earlier elimination-heavy behavior

### Structure Queue / Placement Gap Fixed

Later validation uncovered a concrete structure-queue bug:

- the Python bot only treated `Building` as a structure queue
- OpenRA can also produce structures through the `Defense` queue
- `silo` in particular was using `Defense`, which meant:
  - completed silos were never placed
  - defense-queue items did not block new structure requests
  - the bot spammed repeated silo builds and accumulated a huge fake backlog

The fix applied in `scripts/normal_ai_bot.py`:

- treat both `Building` and `Defense` as structure queues
- place completed items from both queues
- block new structure requests while either queue is active

Validation result after the fix:

```bash
python scripts/collect_bot_data.py --episodes 1 --max-minutes 3 --bot normal --verbose --output-dir data/episodes_smoketest_gap_impl2
```

- `max_silo_queue_entries = 0`
- no repeated late-game silo spam
- final buildings included real placed tech/defense structures like `ftur`,
  `sam`, `dome`, `stek`, and `apwr`

### Combat Quality Pass Applied

Another follow-up pass targeted the highest-value remaining gap under the
current bridge: combat quality.

The changes applied in `scripts/normal_ai_bot.py` were:

- add a simple local strength estimator inspired by OpenRA's
  `AttackOrFleeFuzzy`, using visible-unit/building power, health, range, and
  speed as a coarse proxy
- add local fight gating so squads can refuse obviously bad visible fights
  instead of always blindly reissuing `ATTACK_MOVE`
- add a squad leader selection plus limited regroup behavior while approaching
  a target
- add target prioritization for visible enemy units/buildings, with focus fire
  on higher-value targets like tanks, harvesters, production, and refineries
- explicitly ignore dead-value focus targets such as `*.husk`
- tune production toward a more practical ground army for the current bridge:
  - harvesters only remain in the mix until the refinery target is satisfied
  - dogs are suppressed after the early phase
  - ships are skipped on the land-focused current setup
  - ground vehicles get a modest boost once the war factory is online
- reduce the practical assault threshold from the earlier pure-OpenRA
  `40 + rand(0..29)` approximation to a bridge-tuned `28 + rand(0..9)` so the
  bot commits before being rolled over by easier AIs

Validation run for the combat pass:

```bash
python scripts/collect_bot_data.py --episodes 1 --max-minutes 3 --bot normal --verbose --output-dir data/episodes_smoketest_combat5
```

Observed result:

- the bot now forms and commits a real attack force again
- verbose logs show explicit focus-fire decisions against live targets such as
  `1tnk`, `2tnk`, `harv`, and `fact`
- the previous bad behavior of tunneling on `mcv.husk` targets was removed
- regrouping now happens only on approach, not as often during direct contact
- the run still ended in `eliminated` rather than surviving to time limit

Interpretation:

- this pass improved tactical behavior and made the combat code materially
  closer to the intended backlog item
- it did not yet solve the broader "beat `EasyAI` reliably" goal
- the remaining weakness now appears to be a mix of post-engagement army
  preservation, defense/build spending while the frontline collapses, and still
  imperfect force commitment

### Post-Contact Stabilization Pass Applied

The next follow-up pass targeted the specific collapse pattern that remained
after the first combat pass:

- the bot could win or trade the first clash partially
- then it would keep spending into the wrong things while its army count fell
- and finally it would collapse at home instead of rebuilding a stable force

The stabilization changes added in `scripts/normal_ai_bot.py` were:

- track recent combat contact and enter a temporary recovery mode after large
  post-fight army drops
- pause MCV expansion while recovering
- reduce harvester targets while recovering so frontline rebuild is not starved
- suppress most non-essential dynamic building during recovery
- keep a small home-guard reserve for remote attacks instead of emptying the
  entire base every time
- trigger recovery immediately when a local fight decides to retreat
- cap emergency recovery-defense spending so the bot does not endlessly stack
  new turrets while the army is still weak

Validation result:

```bash
python scripts/collect_bot_data.py --episodes 1 --max-minutes 3 --bot normal --verbose --output-dir data/episodes_smoketest_stabilize2
```

- recovery mode now visibly triggers in logs after post-fight drops
- the bot no longer immediately falls into the earlier harvester-plus-tech
  collapse pattern
- the validated run survived much longer and reached:
  - `time`: about `2.9` minutes before elimination
  - `kills`: `112u / 8b`
  - `losses`: `119u / 15b`
- this is still not "beats `EasyAI` reliably", but it is a material
  stabilization improvement over the earlier collapse-heavy behavior

One later smoke test hit a server-side execution error near the end of the run,
so the reliable validation reference for this pass should remain the
`episodes_smoketest_stabilize2` run above rather than the later failed run.

### Economy Preservation Pass Applied

Because long games were still collapsing after partial battlefield recovery, the
next pass focused on following OpenRA's economy priorities more closely where
the bridge allows it:

- add visible-threat harvester retreat toward refineries/base as a coarse
  approximation of the real harvester `Dock`/avoidance behavior
- slow recovery-mode clearing so the bot does not flip back to aggression too
  early after one partial rebuild
- allow refinery rebuild during recovery when the base is stable and credits are
  available
- keep recovery-time harvester targets conservative so combat rebuild remains
  funded

Reference source for this direction:

- `../openra-rl/openra/OpenRA.Mods.Common/Traits/BotModules/HarvesterBotModule.cs`
- `../openra-rl/openra/OpenRA.Mods.Common/Traits/BotModules/UnitBuilderBotModule.cs`

Validation:

```bash
python scripts/collect_bot_data.py --episodes 1 --max-minutes 3 --bot normal --verbose --output-dir data/episodes_smoketest_tune_econ2
```

Observed result:

- the bot still shows significant run-to-run variance
- however, the OpenRA-aligned economy/recovery pass remained directionally
  useful in the validated reference run:
  - survived to about `2.6` minutes
  - `89u / 8b` kills
  - no early harvester-spam collapse after first contact
- a separate run (`episodes_smoketest_tune_econ1`) performed materially worse,
  so this pass should be treated as a useful but still noisy tuning step, not a
  solved economy problem

## Remaining Gaps Vs Real OpenRA Bot

The Python bot is still only a partial port. The largest missing pieces are:

1. No real harvester manager yet.
   What is now implemented:
   - periodic idle-harvester `HARVEST`
   - replacement requests
   - target count toward `max(4, refinery_count)`

   What is still missing:
   - resource-map-aware reassignment
   - low-effect harvester relocation
   - enemy-avoidance path cost logic
   - dock/flee-on-attack behavior

2. No real `ResourceMapBotModule` equivalent yet.
   The Python bot does not currently reason about resource fields the way the
   C# implementation does.

3. Unit production is still simplified.
   The current logic uses a weighted pick, but it is still not a full port of
   the real `UnitBuilderBotModule`.

4. Squad/defense behavior remains approximate rather than a direct port of
   OpenRA's real state-machine-based squad logic.
   What is now implemented beyond the earlier pass:
   - local target prioritization
   - limited regroup behavior
   - basic local strength checks / attack-or-flee gating
   - post-contact recovery mode
   - remote-attack home-guard reserve

   What is still missing:
   - more reliable post-fight retreat / re-engage timing
   - separate assault/protection/rush squad handling
   - stronger preservation of the home base while an assault is out on the map

5. MCV expansion is only a coarse approximation.
   It can now request and deploy extra MCVs, but it still lacks the real
   resource-region logic from OpenRA.

## Suggested Next Steps

Recommended implementation order:

1. Continue the new combat pass so it survives after first contact instead of
   trading one army and then collapsing.
2. Revisit production/build spending while fighting, so dynamic building and
   recovery logic do not starve the frontline.
3. Keep improving harvester behavior beyond simple replacement requests.
4. After those are more stable, revisit more accurate squad-state logic.

## Quick Resume Summary

If resuming later, remember:

- The immediate money bug was not "no mining". It was "cash-only accounting".
- The next important strength gap is no longer the silo queue bug; it is
  combat follow-through after the initial engagement.
- Harvester management is still incomplete, but combat stability and production
  spending now matter at least as much as another build-order tweak.
- The sibling OpenRA repo should be treated as the source of truth for further
  ports.
