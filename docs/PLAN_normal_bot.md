# Implementation Plan: Normal AI Bot in Python

## Goal

Replace the current `PeriodicAttackBot` (simple build-then-rush) with a `NormalAIBot` that mimics the C# `ModularBot@NormalAI` behavior. This gives us "normal bot vs normal bot" data — symmetric skill levels producing better training signal.

## Architecture

Mirror the C# modular design. Each C# `BotModule` becomes a Python method that runs every tick and returns a list of `CommandModel` actions:

```
NormalAIBot(ScriptedBot)
  ├── _manage_economy()          # HarvesterBotModule
  ├── _manage_base_building()    # BaseBuilderBotModule
  ├── _manage_unit_production()  # UnitBuilderBotModule
  ├── _manage_squads()           # SquadManagerBotModule
  ├── _manage_expansion()        # McvExpansionManagerBotModule
  ├── _manage_repairs()          # BuildingRepairBotModule
  └── _manage_power()            # PowerDownBotModule
```

The `decide()` method calls all managers and merges their commands.

## Modules

### Module 1: Economy Manager (`_manage_economy`)

**Mimics:** `HarvesterBotModule@normal-turtle`

- Track harvester count (target: 4 per refinery, matching `InitialHarvesters: 4`)
- If idle harvesters exist, issue `HARVEST` command to send them to ore
- If harvester count < target, queue `TRAIN harv` from war factory
- Scan every ~25 ticks (matching C# `ScanForIdleHarvestersInterval`)

**Python API used:** `obs.units` (filter `type=="harv"`, check `is_idle`), `HARVEST` action, `TRAIN` action

### Module 2: Base Builder (`_manage_base_building`)

**Mimics:** `BaseBuilderBotModule@normal`

Implement the build order from the YAML config as a priority queue:

| Priority | Building | Condition |
|----------|----------|-----------|
| 1 | `powr` / `apwr` | `power_provided - power_drained < 0` (or `< 200` max excess) |
| 2 | `proc` | refinery count < 2 + expansions |
| 3 | `barr` or `tent` | no barracks yet |
| 4 | `weap` | no war factory yet |
| 5 | `dome` | no radar dome yet |
| 6 | Defense (`gun`, `ftur`, `pbox`, `sam`) | based on `BuildingFractions` weights |
| 7 | Tech (`atek`/`stek`, `fix`, `afld`, `hpad`) | after delays from `BuildingDelays` |
| 8 | `silo` | `resource_capacity` nearing full |

Placement: near Construction Yard (existing `_find_building(obs, "fact")` logic works). Use `BUILD` + `PLACE_BUILDING` actions.

Respect `BuildingLimits` from YAML (e.g. max 7 barracks, 4 war factories).

**Python API used:** `obs.buildings`, `obs.economy` (cash, power), `BUILD`, `PLACE_BUILDING`

### Module 3: Unit Production (`_manage_unit_production`)

**Mimics:** `UnitBuilderBotModule@normal`

Weighted random unit selection from the YAML `UnitsToBuild` table:

| Unit | Weight | Queue |
|------|--------|-------|
| `e1` (Rifle) | 30 | Infantry |
| `e3` (Rocket) | 20 | Infantry |
| `1tnk` (Light tank) | 15 | Vehicle |
| `2tnk` (Medium tank) | 25 | Vehicle |
| `3tnk` (Heavy tank) | 20 | Vehicle |
| `v2rl` (V2 Launcher) | 10 | Vehicle |
| `arty` (Artillery) | 10 | Vehicle |
| `heli`/`hind` | 8 | Aircraft |
| `mig`/`yak` | 8 | Aircraft |

Only produce if `cash > 500` (matching `ProductionMinCashRequirement` default). Check `obs.production` to avoid queuing when already producing. Respect `UnitLimits` (e.g. max dogs, harvesters).

**Python API used:** `obs.production`, `obs.economy.cash`, `obs.available_production`, `TRAIN`

### Module 4: Squad Manager (`_manage_squads`)

**Mimics:** `SquadManagerBotModule@normal`

This is the most complex module.

**Data structures:**
- `_attack_squad: list[int]` — actor IDs of units assigned to attack
- `_defense_squad: list[int]` — actor IDs guarding base

**Logic (every ~75 ticks, matching `AttackForceInterval`):**

1. **Assign roles:** combat units not in any squad get assigned. If < `SquadSize` (40) in attack squad, add them there. Keep 2-3 units as defense near base.
2. **Attack decision:** When attack squad has >= 40 units (+ random 0-30 bonus matching `SquadSizeRandomBonus`), pick a target:
   - Visible enemy buildings (production first) -> `ATTACK_MOVE` to that location
   - Remembered `_enemy_base_pos` if known
   - Otherwise explore via grid search (reuse existing `_compute_candidate_spawns`)
3. **Protection:** If own buildings are under attack (`visible_enemies` near our buildings), redirect some units with `ATTACK_MOVE` to defend
4. **Cleanup:** Remove dead units from squads each tick

**Python API used:** `obs.units`, `obs.visible_enemies`, `obs.visible_enemy_buildings`, `ATTACK_MOVE`, `GUARD`

### Module 5: MCV Expansion (`_manage_expansion`)

**Mimics:** `McvExpansionManagerBotModule@normal`

- Target: `MinimumConstructionYardCount: 2`
- If only 1 CY and cash > 8000 (`NewProductionCashThreshold`), train an MCV from `weap`
- When MCV is built and idle, find an expansion location (far from existing base, near map center or known ore) and `MOVE` + `DEPLOY`

**Python API used:** `obs.units` (filter `type=="mcv"`), `TRAIN`, `MOVE`, `DEPLOY`

### Module 6: Building Repair (`_manage_repairs`)

**Mimics:** `BuildingRepairBotModule`

- Scan `obs.buildings` for any with `hp_percent < 75` and `is_repairing == False`
- Issue `REPAIR` on them
- Cooldown: check every ~100 ticks

**Python API used:** `obs.buildings`, `REPAIR`

### Module 7: Power Management (`_manage_power`)

**Mimics:** `PowerDownBotModule`

- If `power_drained > power_provided`, find non-essential buildings (`dome`, `tsla`, `sam`, `agun`) and issue `POWER_DOWN`
- When power surplus recovers, re-enable them

**Python API used:** `obs.economy`, `obs.buildings`, `POWER_DOWN`

## What We Can't Replicate (API Gaps)

| C# Feature | Why | Workaround |
|-------------|-----|------------|
| Support powers (spy plane, nukes, paratroopers) | No action type exposed | Skip — minimal impact on normal games |
| Engineer capture | No `CAPTURE` action | Skip |
| Fog-of-war shroud data | Only `visible_enemies` exposed | Use `_enemy_base_pos` memory + grid search |
| Precise pathfinding / threat avoidance | No path queries | Simple distance-based decisions |

## Key YAML Constants to Port

These come directly from `ai.yaml` for `@normal`:

```python
SQUAD_SIZE = 40
SQUAD_SIZE_RANDOM_BONUS = 30
INITIAL_HARVESTERS = 4
MIN_EXCESS_POWER = 0
MAX_EXCESS_POWER = 200
NEW_PRODUCTION_CASH_THRESHOLD = 8000
MIN_CONSTRUCTION_YARDS = 2
PRODUCTION_MIN_CASH = 500
RUSH_INTERVAL = 600  # ticks before first attack
```

## Implementation Order

| Phase | Work | Est. lines |
|-------|------|-----------|
| Phase 1 | Skeleton `NormalAIBot` class, `decide()` dispatching to modules | ~50 |
| Phase 2 | `_manage_base_building` with build order + placement | ~120 |
| Phase 3 | `_manage_unit_production` with weighted table | ~60 |
| Phase 4 | `_manage_economy` (harvester management) | ~40 |
| Phase 5 | `_manage_squads` (attack/defense grouping) | ~150 |
| Phase 6 | `_manage_expansion`, `_manage_repairs`, `_manage_power` | ~80 |
| Phase 7 | Integration into `collect_bot_data.py`, testing | ~30 |

**Total estimate: ~530 lines** of new code in a single file `scripts/normal_ai_bot.py`.

## C# Source References

- `OpenRA-RL/OpenRA/OpenRA.Mods.Common/Traits/Player/ModularBot.cs`
- `OpenRA-RL/OpenRA/OpenRA.Mods.Common/Traits/BotModules/BaseBuilderBotModule.cs`
- `OpenRA-RL/OpenRA/OpenRA.Mods.Common/Traits/BotModules/UnitBuilderBotModule.cs`
- `OpenRA-RL/OpenRA/OpenRA.Mods.Common/Traits/BotModules/HarvesterBotModule.cs`
- `OpenRA-RL/OpenRA/OpenRA.Mods.Common/Traits/BotModules/SquadManagerBotModule.cs`
- `OpenRA-RL/OpenRA/OpenRA.Mods.Common/Traits/BotModules/McvExpansionManagerBotModule.cs`
- `OpenRA-RL/OpenRA/mods/ra/rules/ai.yaml`
