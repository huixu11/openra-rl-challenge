"""Python reimplementation of OpenRA's Normal AI (ModularBot@NormalAI).

Mirrors the C# modular bot architecture with these managers:
  - Economy        (HarvesterBotModule@normal-turtle)
  - Base building   (BaseBuilderBotModule@normal)
  - Unit production (UnitBuilderBotModule@normal)
  - Squads          (SquadManagerBotModule@normal)
  - Repairs         (BuildingRepairBotModule)
  - Power           (PowerDownBotModule)

All unit weights are taken directly from mods/ra/rules/ai.yaml.
"""

import random
from typing import List, Optional, Tuple

from openra_env.models import (
    ActionType,
    BuildingInfoModel,
    CommandModel,
    OpenRAAction,
    OpenRAObservation,
    UnitInfoModel,
)

# ---------------------------------------------------------------------------
# Constants from ai.yaml (@normal)
# ---------------------------------------------------------------------------

UNITS_TO_BUILD: dict[str, int] = {
    "e1": 65, "e2": 15, "e3": 30, "e4": 15, "e7": 1, "dog": 15,
    "shok": 15, "harv": 15, "apc": 30, "jeep": 20, "arty": 15,
    "v2rl": 40, "ftrk": 30, "1tnk": 40, "2tnk": 50, "3tnk": 50,
    "4tnk": 25, "ttnk": 25, "stnk": 5, "heli": 30, "mh60": 30,
    "mig": 30, "yak": 30, "ss": 10, "msub": 10, "dd": 10,
    "ca": 10, "pt": 10,
}

UNIT_LIMITS: dict[str, int] = {"dog": 4, "harv": 8, "jeep": 4, "ftrk": 4}

INFANTRY_TYPES = {"e1", "e2", "e3", "e4", "e7", "shok", "dog"}
VEHICLE_TYPES = {"harv", "apc", "jeep", "arty", "v2rl", "ftrk",
                 "1tnk", "2tnk", "3tnk", "4tnk", "ttnk", "stnk", "mcv"}
AIRCRAFT_TYPES = {"heli", "mh60", "mig", "yak", "hind"}
PLANE_TYPES = {"mig", "yak"}
SHIP_TYPES = {"ss", "msub", "dd", "ca", "pt"}

COMBAT_TYPES = (
    {"e1", "e2", "e3", "e4", "e7", "shok"} |
    {"apc", "jeep", "arty", "v2rl", "ftrk", "1tnk", "2tnk", "3tnk", "4tnk", "ttnk", "stnk"} |
    SHIP_TYPES |
    AIRCRAFT_TYPES
)

SQUAD_SIZE = 28
SQUAD_SIZE_RANDOM_BONUS = 10
EXCLUDE_FROM_SQUADS = {"harv", "mcv", "dog", "badr.bomber", "u2"}
BARRACKS_TYPES = {"tent", "barr"}
WAR_FACTORY_TYPES = {"weap"}
POWER_DOWN_TYPES = {"dome", "tsla", "mslo", "agun", "sam"}
PROTECTION_TYPES = {
    "harv", "mcv", "mslo", "gap", "spen", "syrd", "iron", "pdox", "tsla", "agun",
    "dome", "pbox", "hbox", "gun", "ftur", "sam", "atek", "weap", "fact", "proc",
    "silo", "hpad", "afld", "afld.ukraine", "powr", "apwr", "stek", "barr", "kenn",
    "tent", "fix", "fpwr", "tenf", "syrf", "spef", "weaf", "domf", "fixf", "fapw",
    "atef", "pdof", "mslf", "facf",
}

UNIT_QUEUE_ORDER: tuple[tuple[str, set[str]], ...] = (
    ("Vehicle", VEHICLE_TYPES - {"mcv"}),
    ("Infantry", INFANTRY_TYPES),
    ("Plane", PLANE_TYPES),
    ("Ship", SHIP_TYPES),
    ("Aircraft", AIRCRAFT_TYPES - PLANE_TYPES),
)

STRUCTURE_QUEUE_TYPES = {"Building", "Defense"}
ENEMY_FACING_STRUCTURE_TYPES = {"pbox", "hbox", "gun", "ftur", "tsla", "agun", "sam"}

BUILDING_COSTS: dict[str, int] = {
    "powr": 300, "apwr": 500, "proc": 2000, "weap": 2000,
    "barr": 500, "tent": 500, "kenn": 500,
    "dome": 1000, "hpad": 1500, "afld": 1500,
    "fix": 1200, "atek": 1500, "stek": 1500, "silo": 500,
    "pbox": 400, "hbox": 600, "gun": 600, "ftur": 600,
    "tsla": 1500, "agun": 800, "sam": 750,
}

BUILDING_LIMITS: dict[str, int] = {
    "barr": 7, "tent": 7, "dome": 1, "weap": 4, "hpad": 4,
    "afld": 4, "atek": 1, "stek": 1, "fix": 1, "kenn": 1,
}

BUILDING_FRACTIONS: dict[str, int] = {
    "powr": 1, "proc": 1, "tent": 3, "barr": 3, "kenn": 1,
    "dome": 1, "weap": 4, "hpad": 1, "afld": 1,
    "pbox": 9, "gun": 9, "ftur": 10, "tsla": 5,
    "fix": 1, "agun": 5, "sam": 1, "atek": 1, "stek": 1,
}

BUILDING_DELAYS: dict[str, int] = {
    "dome": 6000, "fix": 3000, "pbox": 1500, "gun": 2000,
    "ftur": 1500, "tsla": 2800, "kenn": 7000, "atek": 9000,
    "stek": 9000,
}

UNIT_COMBAT_POWER: dict[str, int] = {
    "e1": 25, "e2": 20, "e3": 45, "e4": 35, "e7": 30, "shok": 55,
    "dog": 5, "jeep": 45, "apc": 55, "arty": 110, "v2rl": 120,
    "ftrk": 70, "1tnk": 90, "2tnk": 120, "3tnk": 145, "4tnk": 135,
    "ttnk": 130, "stnk": 120, "heli": 95, "mh60": 90, "mig": 100,
    "yak": 95, "hind": 95, "ss": 90, "msub": 90, "dd": 100,
    "ca": 120, "pt": 60,
}

BUILDING_THREAT_POWER: dict[str, int] = {
    "pbox": 40, "hbox": 45, "gun": 90, "ftur": 110, "tsla": 160,
    "agun": 120, "sam": 70, "fact": 55, "weap": 60, "proc": 55,
    "dome": 40, "atek": 45, "stek": 45, "fix": 40,
}

TARGET_BUILDING_PRIORITY: dict[str, int] = {
    "fact": 100, "weap": 95, "proc": 90, "dome": 82, "fix": 80,
    "atek": 78, "stek": 78, "afld": 75, "afld.ukraine": 75, "hpad": 72,
    "barr": 68, "tent": 68, "powr": 62, "apwr": 62, "silo": 58,
    "tsla": 56, "agun": 54, "ftur": 52, "gun": 50, "sam": 48,
    "pbox": 45, "hbox": 42,
}

TARGET_UNIT_PRIORITY: dict[str, int] = {
    "mcv": 100, "harv": 95, "v2rl": 90, "arty": 88, "ftrk": 84,
    "4tnk": 82, "3tnk": 80, "2tnk": 76, "1tnk": 72, "ttnk": 78,
    "stnk": 78, "apc": 65, "jeep": 62, "shok": 58, "e4": 52,
    "e3": 50, "e2": 45, "e1": 40, "dog": 10,
}

# Initial build order — same sequence the C# AI follows in practice.
# Uses "barracks" as a placeholder resolved to tent or barr at runtime.
BUILD_ORDER = ["powr", "barracks", "proc", "weap", "powr"]

ATTACK_FORCE_INTERVAL = 75
RUSH_INTERVAL = 600
RUSH_TICKS = 4000
ASSIGN_ROLES_INTERVAL = 50
HARVESTER_SCAN_INTERVAL = 50
UNIT_FEEDBACK_TIME = 30
PRODUCTION_MIN_CASH_REQUIREMENT = 500
INITIAL_HARVESTERS = 4
MINIMUM_EXCESS_POWER = 0
MAXIMUM_EXCESS_POWER = 200
EXCESS_POWER_INCREMENT = 40
EXCESS_POWER_INCREASE_THRESHOLD = 4
INITIAL_MIN_REFINERY_COUNT = 0
ADDITIONAL_MIN_REFINERY_COUNT = 2
NEW_PRODUCTION_CASH_THRESHOLD = 8000
NEW_PRODUCTION_CHANCE = 50
SILO_BUILD_THRESHOLD = 0.8
PROTECT_UNIT_SCAN_RADIUS = 15
PROTECTION_SCAN_RADIUS = 12
PROTECTION_RESPONSE_COOLDOWN = 30
REPAIR_ALL_BUILDINGS_COOLDOWN = 107
POWER_TOGGLE_INTERVAL = 150
ATTACK_SCAN_RADIUS = 12
REGROUP_RADIUS = 14
LOCAL_FIGHT_RADIUS = 12
RETREAT_HEALTH_THRESHOLD = 0.42
MINIMUM_CONSTRUCTION_YARD_COUNT = 2
BUILD_ADDITIONAL_MCV_CASH_AMOUNT = 5000
SCAN_FOR_NEW_MCV_INTERVAL = 20
BUILD_MCV_INTERVAL = 101
MCV_MIN_DEPLOY_RADIUS = 2
MCV_TARGET_REACHED_RADIUS = 2
MCV_TRY_MAINTAIN_RANGE = 8
MCV_FRIENDLY_CONYARD_DISLIKE_RANGE = 14
MCV_FRIENDLY_REFINERY_DISLIKE_RANGE = 14


class NormalAIBot:
    """Python reimplementation of OpenRA's Normal AI."""

    def __init__(self, verbose: bool = False):
        self.verbose = verbose
        self.phase = "deploy_mcv"

        # Base building
        self._build_index = 0
        self._placement_count = 0
        self._deploy_issued = False
        self._last_build_tick: int = -9999

        # Rally
        self._rally_set: set[int] = set()

        # Squads
        self._attack_squad: list[int] = []
        self._last_attack_tick = 0
        self._last_rush_tick = 0
        self._last_assign_tick = 0
        self._last_protection_tick = -9999
        self._assault_threshold = self._roll_assault_threshold()
        self._enemy_base_pos: Optional[Tuple[int, int]] = None

        # Repair / power
        self._repair_issued: set[int] = set()
        self._last_repair_tick = -9999
        self._powered_down: dict[int, int] = {}
        self._last_power_toggle_tick = -9999

        # Economy / production
        self._last_harvester_scan_tick = -9999
        self._last_unit_tick = -9999
        self._current_queue_index = -1
        self._unit_requests: list[str] = []
        self._last_mcv_scan_tick = -9999
        self._last_mcv_build_tick = -9999
        self._mcv_targets: dict[int, tuple[int, int]] = {}

        # Map
        self._cached_map_size: Optional[Tuple[int, int]] = None
        self._candidate_targets: list[Tuple[int, int]] = []
        self._target_index = 0

    def decide(self, obs: OpenRAObservation) -> OpenRAAction:
        commands: List[CommandModel] = []
        self._update_map_size(obs)
        self._update_phase(obs)
        self._cleanup_dead(obs)

        commands.extend(self._handle_placement(obs))

        if self.phase == "deploy_mcv":
            cmd = self._handle_deploy(obs)
            if cmd:
                commands.append(cmd)

        commands.extend(self._handle_rally_points(obs))
        commands.extend(self._manage_power(obs))
        commands.extend(self._manage_repairs(obs))
        commands.extend(self._manage_economy(obs))
        commands.extend(self._manage_expansion(obs))
        commands.extend(self._manage_base_building(obs))
        commands.extend(self._manage_unit_production(obs))
        commands.extend(self._manage_squads(obs))

        if not commands:
            commands.append(CommandModel(action=ActionType.NO_OP))

        return OpenRAAction(commands=commands)

    # ── Phase ─────────────────────────────────────────────────────

    def _update_phase(self, obs: OpenRAObservation):
        has_cy = any(b.type == "fact" for b in obs.buildings)
        if self.phase == "deploy_mcv" and has_cy:
            self.phase = "build_base"
            self._log("Phase -> build_base")
        elif self.phase == "build_base":
            has_barracks = any(b.type in BARRACKS_TYPES for b in obs.buildings)
            if has_barracks:
                self.phase = "produce"
                self._log("Phase -> produce")
        elif self.phase == "produce":
            combat = [u for u in obs.units if u.type in COMBAT_TYPES]
            has_weap = any(b.type in WAR_FACTORY_TYPES for b in obs.buildings)
            if has_weap or len(combat) >= self._assault_threshold or obs.tick >= RUSH_TICKS:
                self.phase = "active"
                self._log(f"Phase -> active ({len(combat)} combat units)")

    # ── Deploy MCV ────────────────────────────────────────────────

    def _handle_deploy(self, obs: OpenRAObservation) -> Optional[CommandModel]:
        if self._deploy_issued:
            return None
        mcv = next((u for u in obs.units if u.type == "mcv"), None)
        if mcv:
            self._deploy_issued = True
            self._log(f"Deploying MCV #{mcv.actor_id}")
            return CommandModel(action=ActionType.DEPLOY, actor_id=mcv.actor_id)
        return None

    # ── Building placement ────────────────────────────────────────

    def _handle_placement(self, obs: OpenRAObservation) -> List[CommandModel]:
        commands = []
        cy = self._find_building(obs, "fact")
        if not cy:
            return commands
        for prod in obs.production:
            if self._is_structure_queue(prod.queue_type) and prod.progress >= 0.99:
                x, y = self._placement_offset(obs, cy, prod.item)
                commands.append(CommandModel(
                    action=ActionType.PLACE_BUILDING,
                    item_type=prod.item,
                    target_x=x, target_y=y,
                ))
                self._placement_count += 1
        return commands

    def _placement_offset(
        self,
        obs: OpenRAObservation,
        cy: BuildingInfoModel,
        item_type: str,
    ) -> Tuple[int, int]:
        cx = cy.cell_x if cy.cell_x > 0 else cy.pos_x // 1024
        cy_y = cy.cell_y if cy.cell_y > 0 else cy.pos_y // 1024
        candidates: list[tuple[int, int]] = []
        for radius in range(2, 7):
            for dx in range(-radius, radius + 1):
                for dy in range(-radius, radius + 1):
                    if max(abs(dx), abs(dy)) != radius:
                        continue
                    candidates.append((cx + dx, cy_y + dy))

        if not candidates:
            return cx + 3, cy_y

        if item_type in ENEMY_FACING_STRUCTURE_TYPES and self._enemy_base_pos is not None:
            tx, ty = self._enemy_base_pos
            candidates.sort(key=lambda p: ((p[0] - tx) ** 2 + (p[1] - ty) ** 2, (p[0] - cx) ** 2 + (p[1] - cy_y) ** 2))
            idx = self._placement_count % min(len(candidates), 8)
            return candidates[idx]

        idx = self._placement_count % len(candidates)
        return candidates[idx]

    # ── Rally points ──────────────────────────────────────────────

    def _handle_rally_points(self, obs: OpenRAObservation) -> List[CommandModel]:
        commands = []
        cy = self._find_building(obs, "fact")
        if not cy:
            return commands
        rally_x = cy.cell_x if cy.cell_x > 0 else cy.pos_x // 1024
        rally_y = cy.cell_y if cy.cell_y > 0 else cy.pos_y // 1024
        for b in obs.buildings:
            if b.type in ("tent", "barr", "weap") and b.actor_id not in self._rally_set:
                commands.append(CommandModel(
                    action=ActionType.SET_RALLY_POINT,
                    actor_id=b.actor_id,
                    target_x=rally_x, target_y=rally_y,
                ))
                self._rally_set.add(b.actor_id)
        return commands

    # ── Base Building (fixed order then dynamic) ──────────────────

    def _manage_base_building(self, obs: OpenRAObservation) -> List[CommandModel]:
        commands = []
        if self.phase == "deploy_mcv":
            return commands

        structure_in_queue = any(self._is_structure_queue(p.queue_type) for p in obs.production)
        if structure_in_queue:
            return commands
        if obs.tick - self._last_build_tick < 10:
            return commands
        credits = self._available_credits(obs)
        if credits < PRODUCTION_MIN_CASH_REQUIREMENT:
            return commands

        # Phase 1: follow the fixed build order
        if self._build_index < len(BUILD_ORDER):
            item = self._resolve_build_item(obs, BUILD_ORDER[self._build_index])
            if item is None:
                return commands
            if self._already_have(obs, item, self._build_index):
                self._build_index += 1
                return commands
            if self._can_produce(obs, item):
                cost = BUILDING_COSTS.get(item, 500)
                if credits >= cost:
                    self._log(
                        f"Building {item} [{self._build_index+1}/{len(BUILD_ORDER)}] "
                        f"({self._credits_str(obs)})"
                    )
                    commands.append(CommandModel(action=ActionType.BUILD, item_type=item))
                    self._last_build_tick = obs.tick
                    self._build_index += 1
            return commands

        # Phase 2: dynamic base building driven by the normal AI priorities.
        item = self._choose_dynamic_building(obs)
        if item and self._can_produce(obs, item):
            cost = BUILDING_COSTS.get(item, 500)
            if credits >= cost:
                self._log(f"Building {item} (dynamic, {self._credits_str(obs)})")
                commands.append(CommandModel(action=ActionType.BUILD, item_type=item))
                self._last_build_tick = obs.tick

        return commands

    def _resolve_build_item(self, obs: OpenRAObservation, placeholder: str) -> Optional[str]:
        if placeholder == "barracks":
            for btype in BARRACKS_TYPES:
                if self._can_produce(obs, btype):
                    return btype
            return None
        return placeholder

    def _already_have(self, obs: OpenRAObservation, item: str, idx: int) -> bool:
        count = sum(1 for b in obs.buildings if b.type == item)
        target = sum(1 for i, p in enumerate(BUILD_ORDER[:idx+1])
                     if self._resolve_build_item(obs, p) == item)
        return count >= target

    def _choose_dynamic_building(self, obs: OpenRAObservation) -> Optional[str]:
        bldg_counts = self._building_counts(obs)
        credits = self._available_credits(obs)
        power_balance = obs.economy.power_provided - obs.economy.power_drained
        minimum_excess_power = self._minimum_excess_power_target(obs)
        power_item = self._best_power_building(obs)

        if power_balance < minimum_excess_power and power_item:
            return power_item

        if not self._has_adequate_refinery_count(obs):
            if self._can_produce(obs, "proc"):
                return "proc"
            return power_item

        if (
            credits > NEW_PRODUCTION_CASH_THRESHOLD
            and random.randrange(100) < NEW_PRODUCTION_CHANCE
        ):
            production = self._best_production_building(obs)
            if production:
                return production

        if (
            obs.economy.resource_capacity > 0
            and obs.economy.ore >= obs.economy.resource_capacity * SILO_BUILD_THRESHOLD
            and self._can_produce(obs, "silo")
        ):
            return "silo"

        total_buildings = max(1, len(obs.buildings))
        candidates = list(BUILDING_FRACTIONS.keys())
        random.shuffle(candidates)
        for item in candidates:
            if BUILDING_DELAYS.get(item, 0) > obs.tick:
                continue
            if not self._can_produce(obs, item):
                continue
            count = bldg_counts.get(item, 0)
            limit = BUILDING_LIMITS.get(item)
            if limit is not None and count >= limit:
                continue
            if count * 100 > BUILDING_FRACTIONS[item] * total_buildings:
                continue
            return item

        return None

    # ── Unit Production ───────────────────────────────────────────

    def _manage_unit_production(self, obs: OpenRAObservation) -> List[CommandModel]:
        commands = []
        if self.phase == "deploy_mcv":
            return commands
        if self._last_build_tick == obs.tick or obs.tick - self._last_unit_tick < UNIT_FEEDBACK_TIME:
            return commands
        credits = self._available_credits(obs)
        if credits < PRODUCTION_MIN_CASH_REQUIREMENT:
            return commands
        reserved_for_build = self._pending_build_cost(obs)
        if reserved_for_build and credits < reserved_for_build:
            return commands
        self._last_unit_tick = obs.tick

        requested = self._queue_requested_unit(obs)
        if requested:
            commands.append(requested)
            return commands

        for _ in range(len(UNIT_QUEUE_ORDER)):
            self._current_queue_index = (self._current_queue_index + 1) % len(UNIT_QUEUE_ORDER)
            queue_type, allowed = UNIT_QUEUE_ORDER[self._current_queue_index]
            if any(p.queue_type == queue_type for p in obs.production):
                continue
            unit = self._pick_unit(obs, allowed)
            if unit:
                commands.append(CommandModel(action=ActionType.TRAIN, item_type=unit))
                break

        return commands

    def _pick_unit(self, obs: OpenRAObservation, allowed: set[str]) -> Optional[str]:
        unit_counts: dict[str, int] = {}
        total_units = 0
        for u in obs.units:
            unit_counts[u.type] = unit_counts.get(u.type, 0) + 1
            if u.type in UNITS_TO_BUILD:
                total_units += 1

        desired: Optional[str] = None
        desired_error = float("inf")
        candidates = list(allowed)
        random.shuffle(candidates)
        for utype in candidates:
            if utype not in allowed:
                continue
            if not self._can_produce(obs, utype):
                continue
            if self._unit_at_limit(obs, utype):
                continue

            share = self._desired_unit_share(obs, utype, unit_counts)
            if share <= 0:
                continue

            count = unit_counts.get(utype, 0)
            error = (count * 100 / total_units - share) if total_units > 0 else -1
            if error < 0:
                return utype

            if error < desired_error:
                desired_error = error
                desired = utype

        return desired

    # ── Economy ──────────────────────────────────────────────────

    def _manage_economy(self, obs: OpenRAObservation) -> List[CommandModel]:
        commands = []
        if self.phase == "deploy_mcv":
            return commands
        if obs.tick - self._last_harvester_scan_tick < HARVESTER_SCAN_INTERVAL:
            return commands

        self._last_harvester_scan_tick = obs.tick
        for u in obs.units:
            if u.type == "harv" and u.is_idle:
                commands.append(CommandModel(action=ActionType.HARVEST, actor_id=u.actor_id))

        self._ensure_harvester_requests(obs)
        return commands

    # ── Expansion ────────────────────────────────────────────────

    def _manage_expansion(self, obs: OpenRAObservation) -> List[CommandModel]:
        commands = []
        if self.phase == "deploy_mcv":
            return commands

        if obs.tick - self._last_mcv_build_tick >= BUILD_MCV_INTERVAL:
            self._last_mcv_build_tick = obs.tick
            self._ensure_mcv_requests(obs)

        if obs.tick - self._last_mcv_scan_tick < SCAN_FOR_NEW_MCV_INTERVAL:
            return commands

        self._last_mcv_scan_tick = obs.tick
        conyards = [b for b in obs.buildings if b.type == "fact"]
        if len(conyards) >= MINIMUM_CONSTRUCTION_YARD_COUNT:
            return commands

        for mcv in [u for u in obs.units if u.type == "mcv"]:
            if not mcv.is_idle:
                continue

            nearest_conyard = self._nearest_distance_to_buildings(mcv.cell_x, mcv.cell_y, conyards)
            if nearest_conyard >= MCV_TRY_MAINTAIN_RANGE:
                commands.append(CommandModel(action=ActionType.DEPLOY, actor_id=mcv.actor_id))
                self._mcv_targets.pop(mcv.actor_id, None)
                continue

            target = self._mcv_targets.get(mcv.actor_id)
            if target is None or self._cell_distance(mcv.cell_x, mcv.cell_y, *target) <= MCV_TARGET_REACHED_RADIUS:
                target = self._pick_expansion_target(obs)
                if target is None:
                    continue
                self._mcv_targets[mcv.actor_id] = target

            commands.append(CommandModel(
                action=ActionType.MOVE,
                actor_id=mcv.actor_id,
                target_x=target[0],
                target_y=target[1],
            ))
        return commands

    def _ensure_harvester_requests(self, obs: OpenRAObservation):
        target = self._harvester_target(obs)
        current = sum(1 for u in obs.units if u.type == "harv")
        current += sum(1 for p in obs.production if p.item == "harv")
        current += self._requested_production_count("harv")

        if current < target:
            self._request_unit_production("harv")

    def _request_unit_production(self, item_type: str):
        if self._requested_production_count(item_type) == 0:
            self._unit_requests.append(item_type)
            self._log(f"Requesting {item_type} production")

    def _requested_production_count(self, item_type: str) -> int:
        return sum(1 for item in self._unit_requests if item == item_type)

    def _queue_requested_unit(self, obs: OpenRAObservation) -> Optional[CommandModel]:
        idx = 0
        while idx < len(self._unit_requests):
            item_type = self._unit_requests[idx]
            queue_type = self._queue_type_for_unit(item_type)
            if queue_type is None:
                del self._unit_requests[idx]
                continue
            if any(p.queue_type == queue_type for p in obs.production):
                idx += 1
                continue
            if not self._can_produce(obs, item_type):
                idx += 1
                continue
            if self._unit_at_limit(obs, item_type):
                del self._unit_requests[idx]
                continue

            del self._unit_requests[idx]
            self._log(f"Training {item_type} (requested)")
            return CommandModel(action=ActionType.TRAIN, item_type=item_type)

        return None

    # ── Squads ────────────────────────────────────────────────────

    def _manage_squads(self, obs: OpenRAObservation) -> List[CommandModel]:
        commands = []
        if self.phase in ("deploy_mcv", "build_base"):
            return commands

        # Assign new units to squad
        if obs.tick - self._last_assign_tick >= ASSIGN_ROLES_INTERVAL:
            self._last_assign_tick = obs.tick
            assigned = set(self._attack_squad)
            for u in obs.units:
                if u.type in EXCLUDE_FROM_SQUADS or u.type not in COMBAT_TYPES:
                    continue
                if u.actor_id not in assigned:
                    self._attack_squad.append(u.actor_id)

        # Base defense
        commands.extend(self._handle_defense(obs))

        # Attack
        if obs.tick - self._last_attack_tick >= ATTACK_FORCE_INTERVAL:
            self._last_attack_tick = obs.tick
            commands.extend(self._handle_attack(obs))

        return commands

    def _handle_defense(self, obs: OpenRAObservation) -> List[CommandModel]:
        commands = []
        if not obs.visible_enemies or obs.tick - self._last_protection_tick < PROTECTION_RESPONSE_COOLDOWN:
            return commands

        protected_points: list[tuple[int, int, int]] = []
        for b in obs.buildings:
            if b.type in PROTECTION_TYPES:
                bx = b.cell_x if b.cell_x > 0 else b.pos_x // 1024
                by = b.cell_y if b.cell_y > 0 else b.pos_y // 1024
                protected_points.append((bx, by, PROTECTION_SCAN_RADIUS))
        for u in obs.units:
            if u.type in {"harv", "mcv"}:
                protected_points.append((u.cell_x, u.cell_y, PROTECT_UNIT_SCAN_RADIUS))

        threat = None
        for e in obs.visible_enemies:
            if any(abs(e.cell_x - px) + abs(e.cell_y - py) <= radius for px, py, radius in protected_points):
                threat = e
                break
        if not threat:
            return commands

        self._last_protection_tick = obs.tick
        alive = {u.actor_id: u for u in obs.units}
        for uid in self._attack_squad:
            u = alive.get(uid)
            if not u or not u.can_attack:
                continue
            commands.append(CommandModel(
                action=ActionType.ATTACK_MOVE, actor_id=uid,
                target_x=threat.cell_x, target_y=threat.cell_y,
            ))
        return commands

    def _handle_attack(self, obs: OpenRAObservation) -> List[CommandModel]:
        commands = []
        alive = {u.actor_id: u for u in obs.units}
        squad_units = [alive[uid] for uid in self._attack_squad if uid in alive]
        minimum_commitment = max(8, self._assault_threshold // 2)
        if len(squad_units) < minimum_commitment:
            self._assault_threshold = self._roll_assault_threshold()
            return commands

        leader = self._select_squad_leader(squad_units)
        local_enemy_units = self._visible_enemy_units_near(obs, leader.cell_x, leader.cell_y, LOCAL_FIGHT_RADIUS)
        local_enemy_buildings = self._visible_enemy_buildings_near(obs, leader.cell_x, leader.cell_y, LOCAL_FIGHT_RADIUS)
        full_redirect = (
            obs.tick - self._last_rush_tick >= RUSH_INTERVAL
            or bool(local_enemy_units)
            or bool(local_enemy_buildings)
        )

        if local_enemy_units or local_enemy_buildings:
            if not self._should_take_local_fight(squad_units, local_enemy_units, local_enemy_buildings, rush=full_redirect):
                retreat_commands = self._retreat_squad_commands(obs, squad_units, leader)
                if retreat_commands:
                    self._last_rush_tick = obs.tick
                    self._log(
                        f"Retreating {len(retreat_commands)} units "
                        f"(local fight unfavorable: own={len(squad_units)} enemy={len(local_enemy_units) + len(local_enemy_buildings)})"
                    )
                    return retreat_commands

            priority_target = self._pick_priority_target(obs, leader.cell_x, leader.cell_y, local_only=True)
            if priority_target is not None:
                focus_commands = self._focus_fire_commands(squad_units, priority_target)
                if focus_commands:
                    self._last_rush_tick = obs.tick
                    self._log(
                        f"Focus attack {priority_target[3]} #{priority_target[0]} "
                        f"with {len(focus_commands)} units"
                    )
                    return focus_commands

        if len(squad_units) < self._assault_threshold:
            return commands

        regroup_commands = self._regroup_squad_commands(squad_units, leader)
        if regroup_commands:
            return regroup_commands

        tx, ty = self._find_attack_target(obs)
        redirected = 0
        for u in squad_units:
            if full_redirect or u.is_idle:
                commands.append(CommandModel(
                    action=ActionType.ATTACK_MOVE, actor_id=u.actor_id,
                    target_x=tx, target_y=ty,
                ))
                redirected += 1
        if redirected:
            if full_redirect:
                self._last_rush_tick = obs.tick
            self._log(f"Attack-move {redirected}/{len(squad_units)} units -> ({tx},{ty})")
        return commands

    def _find_attack_target(self, obs: OpenRAObservation) -> Tuple[int, int]:
        priority = self._pick_priority_target(obs, None, None, local_only=False)
        if priority is not None:
            _, tx, ty, _, kind = priority
            if kind == "building":
                self._enemy_base_pos = (tx, ty)
            elif self._enemy_base_pos is None:
                self._enemy_base_pos = (tx, ty)
            return tx, ty
        if self._enemy_base_pos:
            return self._enemy_base_pos
        if not self._candidate_targets:
            self._candidate_targets = self._search_grid(obs)
        t = self._candidate_targets[self._target_index % len(self._candidate_targets)]
        self._target_index = (self._target_index + 1) % len(self._candidate_targets)
        return t

    def _search_grid(self, obs: OpenRAObservation) -> list[Tuple[int, int]]:
        w, h = self._get_map_size()
        cy = self._find_building(obs, "fact")
        if not cy:
            return [(w // 2, h // 2)]
        bx = cy.cell_x if cy.cell_x > 0 else cy.pos_x // 1024
        by = cy.cell_y if cy.cell_y > 0 else cy.pos_y // 1024
        n = 3
        cw, ch = max(1, w // n), max(1, h // n)
        centers = [(cw * gx + cw // 2, ch * gy + ch // 2)
                    for gx in range(n) for gy in range(n)]
        min_d2 = (min(w, h) // n) ** 2
        far = [p for p in centers if (p[0]-bx)**2 + (p[1]-by)**2 > min_d2]
        if not far:
            far = [(w // 2, h // 2)]
        far.sort(key=lambda p: (p[0]-bx)**2 + (p[1]-by)**2, reverse=True)
        return far

    # ── Repairs ───────────────────────────────────────────────────

    def _manage_repairs(self, obs: OpenRAObservation) -> List[CommandModel]:
        commands = []
        if obs.tick - self._last_repair_tick < REPAIR_ALL_BUILDINGS_COOLDOWN:
            return commands
        self._last_repair_tick = obs.tick
        for b in obs.buildings:
            if b.hp_percent >= 0.98:
                self._repair_issued.discard(b.actor_id)
            if (b.hp_percent < 0.75 and not b.is_repairing
                    and b.actor_id not in self._repair_issued
                    and self._available_credits(obs) >= 500):
                commands.append(CommandModel(action=ActionType.REPAIR, actor_id=b.actor_id))
                self._repair_issued.add(b.actor_id)
        return commands

    # ── Power ─────────────────────────────────────────────────────

    def _manage_power(self, obs: OpenRAObservation) -> List[CommandModel]:
        commands = []
        if obs.tick - self._last_power_toggle_tick < POWER_TOGGLE_INTERVAL:
            return commands
        self._last_power_toggle_tick = obs.tick
        bal = obs.economy.power_provided - obs.economy.power_drained
        buildings_by_id = {b.actor_id: b for b in obs.buildings}
        self._powered_down = {
            actor_id: expected_change
            for actor_id, expected_change in self._powered_down.items()
            if actor_id in buildings_by_id and not buildings_by_id[actor_id].is_powered
        }

        if bal > 0 and self._powered_down:
            for actor_id, expected_change in sorted(
                self._powered_down.items(),
                key=lambda item: item[1],
                reverse=True,
            ):
                if bal - expected_change < 0:
                    continue
                commands.append(CommandModel(action=ActionType.POWER_DOWN, actor_id=actor_id))
                bal -= expected_change
            for cmd in commands:
                self._powered_down.pop(cmd.actor_id, None)
            return commands

        if bal < 0:
            candidates: list[tuple[int, int]] = []
            for b in obs.buildings:
                if b.type not in POWER_DOWN_TYPES or not b.is_powered or b.actor_id in self._powered_down:
                    continue
                expected_change = max(0, -b.power_amount)
                if expected_change <= 0:
                    continue
                candidates.append((expected_change, b.actor_id))

            for expected_change, actor_id in sorted(candidates):
                commands.append(CommandModel(action=ActionType.POWER_DOWN, actor_id=actor_id))
                self._powered_down[actor_id] = expected_change
                bal += expected_change
                if bal >= 0:
                    break
        return commands

    # ── Cleanup ───────────────────────────────────────────────────

    def _cleanup_dead(self, obs: OpenRAObservation):
        alive = {u.actor_id for u in obs.units}
        self._attack_squad = [uid for uid in self._attack_squad if uid in alive]
        self._mcv_targets = {
            actor_id: target
            for actor_id, target in self._mcv_targets.items()
            if actor_id in alive
        }
        alive_b = {b.actor_id for b in obs.buildings}
        self._repair_issued &= alive_b
        self._rally_set &= alive_b
        self._powered_down = {
            actor_id: expected_change
            for actor_id, expected_change in self._powered_down.items()
            if actor_id in alive_b
        }

    # ── Map ───────────────────────────────────────────────────────

    def _update_map_size(self, obs: OpenRAObservation):
        w, h = obs.map_info.width, obs.map_info.height
        if w > 0 and h > 0:
            if self._cached_map_size is None:
                self._cached_map_size = (w, h)
            else:
                cw, ch = self._cached_map_size
                if w < cw or h < ch:
                    self._cached_map_size = (w, h)
                    self._candidate_targets = []

    def _get_map_size(self) -> Tuple[int, int]:
        return self._cached_map_size or (128, 128)

    # ── Helpers ───────────────────────────────────────────────────

    def _find_building(self, obs: OpenRAObservation, btype: str) -> Optional[BuildingInfoModel]:
        return next((b for b in obs.buildings if b.type == btype), None)

    def _is_structure_queue(self, queue_type: str) -> bool:
        return queue_type in STRUCTURE_QUEUE_TYPES

    def _available_credits(self, obs: OpenRAObservation) -> int:
        # OpenRA splits spendable funds between liquid cash and stored ore/resources.
        return obs.economy.cash + obs.economy.ore

    def _pending_build_cost(self, obs: OpenRAObservation) -> int:
        if self._build_index >= len(BUILD_ORDER):
            return 0
        item = self._resolve_build_item(obs, BUILD_ORDER[self._build_index])
        if item is None or self._already_have(obs, item, self._build_index):
            return 0
        return BUILDING_COSTS.get(item, 500)

    def _building_counts(self, obs: OpenRAObservation) -> dict[str, int]:
        counts: dict[str, int] = {}
        for b in obs.buildings:
            counts[b.type] = counts.get(b.type, 0) + 1
        return counts

    def _minimum_excess_power_target(self, obs: OpenRAObservation) -> int:
        bonus = EXCESS_POWER_INCREMENT * (len(obs.buildings) // max(1, EXCESS_POWER_INCREASE_THRESHOLD))
        return max(MINIMUM_EXCESS_POWER, min(MAXIMUM_EXCESS_POWER, MINIMUM_EXCESS_POWER + bonus))

    def _has_any_production_building(self, obs: OpenRAObservation) -> bool:
        return any(b.type in BARRACKS_TYPES | WAR_FACTORY_TYPES for b in obs.buildings)

    def _optimal_refinery_count(self, obs: OpenRAObservation) -> int:
        if self._has_any_production_building(obs):
            return INITIAL_MIN_REFINERY_COUNT + ADDITIONAL_MIN_REFINERY_COUNT
        return INITIAL_MIN_REFINERY_COUNT

    def _has_adequate_refinery_count(self, obs: OpenRAObservation) -> bool:
        refinery_count = sum(1 for b in obs.buildings if b.type == "proc")
        has_power = any(b.type in {"powr", "apwr"} for b in obs.buildings)
        has_conyard = any(b.type == "fact" for b in obs.buildings)
        return (
            refinery_count >= self._optimal_refinery_count(obs)
            or not has_power
            or not has_conyard
        )

    def _best_power_building(self, obs: OpenRAObservation) -> Optional[str]:
        for item in ("apwr", "powr"):
            if self._can_produce(obs, item):
                return item
        return None

    def _best_production_building(self, obs: OpenRAObservation) -> Optional[str]:
        candidates = []
        counts = self._building_counts(obs)
        for item in ("weap", "barr", "tent"):
            if not self._can_produce(obs, item):
                continue
            limit = BUILDING_LIMITS.get(item)
            if limit is not None and counts.get(item, 0) >= limit:
                continue
            candidates.append(item)
        if not candidates:
            return None
        return random.choice(candidates)

    def _unit_at_limit(self, obs: OpenRAObservation, item_type: str) -> bool:
        limit = UNIT_LIMITS.get(item_type)
        if limit is None:
            return False
        current = sum(1 for u in obs.units if u.type == item_type)
        return current >= limit

    def _harvester_target(self, obs: OpenRAObservation) -> int:
        refinery_count = sum(1 for b in obs.buildings if b.type == "proc")
        target = max(INITIAL_HARVESTERS, refinery_count)
        return min(target, UNIT_LIMITS.get("harv", target))

    def _desired_unit_share(
        self,
        obs: OpenRAObservation,
        item_type: str,
        unit_counts: dict[str, int],
    ) -> int:
        share = UNITS_TO_BUILD.get(item_type, 0)
        if share <= 0:
            return 0

        if item_type in SHIP_TYPES:
            return 0
        if item_type in AIRCRAFT_TYPES and self._available_credits(obs) < 3000:
            return 0
        if item_type == "harv":
            return share if unit_counts.get("harv", 0) < self._harvester_target(obs) else 0
        if item_type == "dog" and (obs.tick > 2500 or any(b.type in WAR_FACTORY_TYPES for b in obs.buildings)):
            return 0

        has_weap = any(b.type in WAR_FACTORY_TYPES for b in obs.buildings)
        infantry_count = sum(unit_counts.get(t, 0) for t in INFANTRY_TYPES if t != "dog")
        vehicle_count = sum(unit_counts.get(t, 0) for t in VEHICLE_TYPES if t not in {"harv", "mcv"})

        if has_weap:
            if item_type in INFANTRY_TYPES:
                share = max(0, int(share * 0.85))
                if infantry_count >= 36 and vehicle_count < max(10, infantry_count // 3):
                    return 0
            elif item_type in {"1tnk", "2tnk", "3tnk", "4tnk", "ttnk", "stnk", "arty", "v2rl"}:
                share = int(share * 1.2)
            elif item_type in {"apc", "jeep", "ftrk"}:
                share = int(share * 1.1)

        return share

    def _ensure_mcv_requests(self, obs: OpenRAObservation):
        if self._build_index < len(BUILD_ORDER):
            return
        if not any(b.type in WAR_FACTORY_TYPES for b in obs.buildings):
            return
        if self._available_credits(obs) < BUILD_ADDITIONAL_MCV_CASH_AMOUNT:
            return

        conyards = sum(1 for b in obs.buildings if b.type == "fact")
        mcvs = sum(1 for u in obs.units if u.type == "mcv")
        pending = sum(1 for p in obs.production if p.item == "mcv") + self._requested_production_count("mcv")
        if conyards + mcvs + pending < MINIMUM_CONSTRUCTION_YARD_COUNT:
            self._request_unit_production("mcv")

    def _pick_expansion_target(self, obs: OpenRAObservation) -> Optional[Tuple[int, int]]:
        conyards = [b for b in obs.buildings if b.type == "fact"]
        refineries = [b for b in obs.buildings if b.type == "proc"]
        candidates = self._search_grid(obs)
        for target in candidates:
            if self._nearest_distance_to_buildings(target[0], target[1], conyards) < MCV_FRIENDLY_CONYARD_DISLIKE_RANGE:
                continue
            if refineries and self._nearest_distance_to_buildings(target[0], target[1], refineries) < MCV_FRIENDLY_REFINERY_DISLIKE_RANGE:
                continue
            return target
        return candidates[0] if candidates else None

    def _nearest_distance_to_buildings(self, x: int, y: int, buildings: list[BuildingInfoModel]) -> int:
        if not buildings:
            return 10**9
        return min(
            self._cell_distance(
                x,
                y,
                b.cell_x if b.cell_x > 0 else b.pos_x // 1024,
                b.cell_y if b.cell_y > 0 else b.pos_y // 1024,
            )
            for b in buildings
        )

    def _cell_distance(self, ax: int, ay: int, bx: int, by: int) -> int:
        return abs(ax - bx) + abs(ay - by)

    def _queue_type_for_unit(self, item_type: str) -> Optional[str]:
        if item_type == "mcv":
            return "Vehicle"
        for queue_type, allowed in UNIT_QUEUE_ORDER:
            if item_type in allowed:
                return queue_type
        return None

    def _roll_assault_threshold(self) -> int:
        return SQUAD_SIZE + random.randrange(SQUAD_SIZE_RANDOM_BONUS)

    def _select_squad_leader(self, squad_units: list[UnitInfoModel]) -> UnitInfoModel:
        avg_x = sum(u.cell_x for u in squad_units) / len(squad_units)
        avg_y = sum(u.cell_y for u in squad_units) / len(squad_units)
        return min(squad_units, key=lambda u: (u.cell_x - avg_x) ** 2 + (u.cell_y - avg_y) ** 2)

    def _regroup_squad_commands(
        self,
        squad_units: list[UnitInfoModel],
        leader: UnitInfoModel,
    ) -> List[CommandModel]:
        close_units = [
            u for u in squad_units
            if self._cell_distance(u.cell_x, u.cell_y, leader.cell_x, leader.cell_y) <= REGROUP_RADIUS
        ]
        if len(close_units) >= max(2, int(len(squad_units) * 0.4)):
            return []

        commands = [CommandModel(action=ActionType.STOP, actor_id=leader.actor_id)]
        redirected = 0
        for u in squad_units:
            if u.actor_id == leader.actor_id:
                continue
            if self._cell_distance(u.cell_x, u.cell_y, leader.cell_x, leader.cell_y) > REGROUP_RADIUS:
                commands.append(CommandModel(
                    action=ActionType.ATTACK_MOVE,
                    actor_id=u.actor_id,
                    target_x=leader.cell_x,
                    target_y=leader.cell_y,
                ))
                redirected += 1

        if redirected:
            self._log(f"Regrouping {redirected}/{len(squad_units)} units around leader")
            return commands
        return []

    def _visible_enemy_units_near(
        self,
        obs: OpenRAObservation,
        x: int,
        y: int,
        radius: int,
    ) -> list[UnitInfoModel]:
        return [
            e for e in obs.visible_enemies
            if self._cell_distance(x, y, e.cell_x, e.cell_y) <= radius
        ]

    def _visible_enemy_buildings_near(
        self,
        obs: OpenRAObservation,
        x: int,
        y: int,
        radius: int,
    ) -> list[BuildingInfoModel]:
        return [
            b for b in obs.visible_enemy_buildings
            if self._cell_distance(x, y, b.cell_x, b.cell_y) <= radius
        ]

    def _estimate_combat_power(self, actor) -> float:
        base = UNIT_COMBAT_POWER.get(actor.type, 0)
        if base == 0:
            base = BUILDING_THREAT_POWER.get(actor.type, 0)
        if base == 0:
            return 0.0

        hp = getattr(actor, "hp_percent", 1.0)
        speed = getattr(actor, "speed", 0)
        attack_range = getattr(actor, "attack_range", 0)
        return base * max(0.2, hp) * (1.0 + min(speed / 200.0, 0.25) + min(attack_range / 12000.0, 0.25))

    def _should_take_local_fight(
        self,
        squad_units: list[UnitInfoModel],
        enemy_units: list[UnitInfoModel],
        enemy_buildings: list[BuildingInfoModel],
        rush: bool,
    ) -> bool:
        own_units = [u for u in squad_units if u.can_attack]
        own_power = sum(self._estimate_combat_power(u) for u in own_units)
        enemy_power = (
            sum(self._estimate_combat_power(u) for u in enemy_units)
            + sum(self._estimate_combat_power(b) * 0.7 for b in enemy_buildings)
        )

        own_avg_hp = sum(u.hp_percent for u in own_units) / max(1, len(own_units))
        enemy_avg_hp = (
            sum(u.hp_percent for u in enemy_units) + sum(b.hp_percent for b in enemy_buildings)
        ) / max(1, len(enemy_units) + len(enemy_buildings))
        own_avg_speed = sum(max(1, getattr(u, "speed", 1)) for u in own_units) / max(1, len(own_units))
        enemy_avg_speed = sum(max(1, getattr(u, "speed", 1)) for u in enemy_units) / max(1, len(enemy_units))

        if enemy_power <= 1:
            return True

        power_ratio = own_power / enemy_power
        speed_ratio = own_avg_speed / max(1.0, enemy_avg_speed)

        if rush:
            return power_ratio >= 1.05 and own_avg_hp >= 0.55
        if own_avg_hp < RETREAT_HEALTH_THRESHOLD:
            return power_ratio >= 1.25 and speed_ratio <= 1.0
        if own_avg_hp >= enemy_avg_hp:
            return power_ratio >= 0.9
        if speed_ratio < 0.9:
            return power_ratio >= 1.0
        return power_ratio >= 1.1

    def _pick_priority_target(
        self,
        obs: OpenRAObservation,
        x: Optional[int],
        y: Optional[int],
        local_only: bool,
    ) -> Optional[Tuple[int, int, int, str, str]]:
        best: Optional[Tuple[float, Tuple[int, int, int, str, str]]] = None

        for b in obs.visible_enemy_buildings:
            if local_only and x is not None and y is not None and self._cell_distance(x, y, b.cell_x, b.cell_y) > LOCAL_FIGHT_RADIUS:
                continue
            priority = TARGET_BUILDING_PRIORITY.get(b.type, 40)
            dist = self._cell_distance(x, y, b.cell_x, b.cell_y) if x is not None and y is not None else 0
            score = priority * 1000 - dist * 20 + (1.0 - b.hp_percent) * 120
            candidate = (b.actor_id, b.cell_x, b.cell_y, b.type, "building")
            if best is None or score > best[0]:
                best = (score, candidate)

        for e in obs.visible_enemies:
            if local_only and x is not None and y is not None and self._cell_distance(x, y, e.cell_x, e.cell_y) > LOCAL_FIGHT_RADIUS:
                continue
            if "husk" in e.type:
                continue
            if not e.can_attack and e.type not in {"harv", "mcv"}:
                continue
            priority = TARGET_UNIT_PRIORITY.get(e.type, 30 if e.can_attack else 10)
            dist = self._cell_distance(x, y, e.cell_x, e.cell_y) if x is not None and y is not None else 0
            score = priority * 1000 - dist * 25 + (1.0 - e.hp_percent) * 150
            candidate = (e.actor_id, e.cell_x, e.cell_y, e.type, "unit")
            if best is None or score > best[0]:
                best = (score, candidate)

        return best[1] if best is not None else None

    def _focus_fire_commands(
        self,
        squad_units: list[UnitInfoModel],
        target: Tuple[int, int, int, str, str],
    ) -> List[CommandModel]:
        target_actor_id, tx, ty, _, _ = target
        commands = []
        for u in squad_units:
            if not u.can_attack:
                continue
            commands.append(CommandModel(
                action=ActionType.ATTACK,
                actor_id=u.actor_id,
                target_actor_id=target_actor_id,
                target_x=tx,
                target_y=ty,
            ))
        return commands

    def _retreat_squad_commands(
        self,
        obs: OpenRAObservation,
        squad_units: list[UnitInfoModel],
        leader: UnitInfoModel,
    ) -> List[CommandModel]:
        fallback = self._pick_retreat_point(obs, leader)
        if fallback is None:
            return []
        tx, ty = fallback
        return [
            CommandModel(
                action=ActionType.MOVE,
                actor_id=u.actor_id,
                target_x=tx,
                target_y=ty,
            )
            for u in squad_units
        ]

    def _pick_retreat_point(
        self,
        obs: OpenRAObservation,
        leader: UnitInfoModel,
    ) -> Optional[Tuple[int, int]]:
        if obs.buildings:
            best = min(
                obs.buildings,
                key=lambda b: self._cell_distance(
                    leader.cell_x,
                    leader.cell_y,
                    b.cell_x if b.cell_x > 0 else b.pos_x // 1024,
                    b.cell_y if b.cell_y > 0 else b.pos_y // 1024,
                ),
            )
            return (
                best.cell_x if best.cell_x > 0 else best.pos_x // 1024,
                best.cell_y if best.cell_y > 0 else best.pos_y // 1024,
            )
        return None

    def _credits_str(self, obs: OpenRAObservation) -> str:
        return (
            f"${obs.economy.cash} cash + ${obs.economy.ore} ore"
            f" = ${self._available_credits(obs)}"
        )

    def _can_produce(self, obs: OpenRAObservation, item_type: str) -> bool:
        if item_type in obs.available_production:
            return True
        for b in obs.buildings:
            if item_type in b.can_produce:
                return True
        return False

    def _log(self, msg: str):
        if self.verbose:
            print(f"  [NormalAI] {msg}")
