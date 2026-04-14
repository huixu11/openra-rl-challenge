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

import base64
import random
import struct
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
DEFENSE_STRUCTURE_TYPES = {"pbox", "hbox", "gun", "ftur", "tsla", "agun", "sam", "gap", "mslo"}
NAVAL_STRUCTURE_TYPES = {"spen", "syrd"}
ENEMY_FACING_STRUCTURE_TYPES = {"pbox", "hbox", "gun", "ftur", "tsla", "agun", "sam"}
NO_BUILDABLE_AREA_TYPES = NAVAL_STRUCTURE_TYPES | {"silo", "kenn"}
BUILDING_VARIANT_CHOICES: dict[str, tuple[str, ...]] = {
    "barracks": ("tent", "barr"),
    "afld": ("afld", "afld.ukraine"),
}
BUILDING_CANONICAL_TYPES: dict[str, str] = {"afld.ukraine": "afld"}
BUILDING_DIMENSIONS: dict[str, tuple[int, int]] = {
    "fact": (3, 4),
    "powr": (2, 3),
    "apwr": (3, 3),
    "proc": (3, 4),
    "weap": (3, 4),
    "barr": (2, 3),
    "tent": (2, 3),
    "dome": (2, 3),
    "atek": (2, 3),
    "hpad": (2, 3),
    "afld": (3, 2),
    "afld.ukraine": (3, 2),
    "fix": (3, 3),
    "stek": (3, 3),
    "spen": (3, 3),
    "syrd": (3, 3),
    "sam": (2, 1),
    "mslo": (2, 1),
    "silo": (2, 1),
    "kenn": (2, 2),
    "pbox": (2, 1),
    "hbox": (2, 1),
    "gun": (2, 2),
    "ftur": (2, 2),
    "tsla": (2, 2),
    "agun": (2, 2),
    "gap": (3, 3),
}
BUILDING_TOPLEFT_OFFSETS: dict[str, tuple[int, int]] = {
    "fact": (1, 1),
    "powr": (1, 1),
    "apwr": (1, 1),
    "proc": (1, 1),
    "weap": (1, 1),
    "barr": (1, 1),
    "tent": (1, 1),
    "dome": (1, 1),
    "atek": (1, 1),
    "hpad": (1, 1),
    "afld": (1, 1),
    "afld.ukraine": (1, 1),
    "fix": (1, 1),
    "stek": (1, 1),
    "spen": (1, 1),
    "syrd": (1, 1),
    "sam": (1, 0),
    "mslo": (1, 0),
    "silo": (1, 0),
    "kenn": (1, 1),
    "pbox": (1, 0),
    "hbox": (1, 0),
    "gun": (1, 1),
    "ftur": (1, 1),
    "tsla": (1, 1),
    "agun": (1, 1),
    "gap": (1, 1),
}

BUILDING_COSTS: dict[str, int] = {
    "powr": 300, "apwr": 500, "proc": 1400, "weap": 2000,
    "barr": 500, "tent": 500, "kenn": 200,
    "dome": 1500, "hpad": 500, "afld": 500, "afld.ukraine": 500,
    "fix": 1200, "atek": 1500, "stek": 1500, "silo": 150,
    "pbox": 600, "hbox": 750, "gun": 800, "ftur": 600,
    "tsla": 1200, "agun": 800, "sam": 700,
    "gap": 800, "mslo": 2500, "spen": 800, "syrd": 1000,
}

BUILDING_LIMITS: dict[str, int] = {
    "barr": 7, "tent": 7, "dome": 1, "weap": 4, "hpad": 4,
    "afld": 4, "atek": 1, "stek": 1, "fix": 1, "kenn": 1,
    "mslo": 1, "spen": 1, "syrd": 1,
}

BUILDING_FRACTIONS: dict[str, int] = {
    "powr": 1, "proc": 1, "tent": 3, "barr": 3, "kenn": 1,
    "dome": 1, "weap": 4, "hpad": 1, "spen": 1, "syrd": 1, "afld": 1,
    "pbox": 9, "gun": 9, "ftur": 10, "tsla": 5, "gap": 2,
    "fix": 1, "agun": 5, "sam": 1, "atek": 1, "stek": 1,
    "mslo": 1,
}

BUILDING_DELAYS: dict[str, int] = {
    "dome": 6000, "fix": 3000, "pbox": 1500, "gun": 2000,
    "ftur": 1500, "tsla": 2800, "kenn": 7000, "atek": 9000,
    "stek": 9000, "spen": 6000, "syrd": 6000,
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
STRUCTURE_PRODUCTION_ACTIVE_DELAY = 25
STRUCTURE_PRODUCTION_INACTIVE_DELAY = 125
STRUCTURE_PRODUCTION_RANDOM_BONUS_DELAY = 10
STRUCTURE_PRODUCTION_RESUME_DELAY = 1500
PLACEMENT_ATTEMPT_INTERVAL = 25
PLACEMENT_CONFIRMATION_DELAY = 300
MAX_FAILED_PLACEMENT_ATTEMPTS = 8
BASE_BUILD_MIN_RADIUS = 2
BASE_BUILD_MAX_RADIUS = 20
DEFENSE_BUILD_MIN_RADIUS = 5
DEFENSE_BUILD_MAX_RADIUS = 20
CHECK_FOR_WATER_RADIUS = 8
NAVAL_WATER_SCAN_STRIDE = 1
NAVAL_WATER_SCAN_RADIUS = 12
NAVAL_MIN_OPEN_WATER_WINDOWS = 1
NAVAL_MIN_WATER_SCORE = 20
NAVAL_EARLY_BUILD_WATER_SCORE = 22
NAVAL_EARLY_BUILD_CREDIT_BUFFER = 250
NAVAL_BUILD_MAX_RADIUS = 24
NAVAL_GATE_CACHE_TICKS = 151
RESOURCE_MAP_UPDATE_INTERVAL = 151
RESOURCE_PATCH_LINK_RADIUS = 3
RESOURCE_PATCH_MIN_CELLS = 2
RESOURCE_PATCH_SEARCH_MARGIN = 8
RESOURCE_PATCH_THREAT_RADIUS = 12
RESOURCE_PATCH_REFINERY_DISLIKE_RADIUS = 14
MAX_REFINERIES_PER_PATCH = 2
NAVAL_CANDIDATE_MIN_COUNT = 1
RESOURCE_PATCH_MEMORY_MATCH_RADIUS = 6
RESOURCE_PATCH_MAX_CAPACITY = 6

ATTACK_FORCE_INTERVAL = 75
RUSH_INTERVAL = 600
RUSH_TICKS = 4000
ASSIGN_ROLES_INTERVAL = 50
HARVESTER_SCAN_INTERVAL = 50
UNIT_FEEDBACK_TIME = 30
STALE_TARGET_REACHED_RADIUS = 8
STALE_TARGET_REDIRECT_LIMIT = 3
PRODUCTION_MIN_CASH_REQUIREMENT = 500
QUEUE_PRODUCTION_DELAYS: dict[str, int] = {
    "Infantry": 12,
    "Vehicle": 18,
    "Plane": 30,
    "Ship": 36,
    "Aircraft": 22,
}
UNIT_PRODUCTION_DELAYS: dict[str, int] = {
    "dog": 600,
    "harv": 800,
    "mcv": 2200,
    "4tnk": 180,
    "ttnk": 240,
    "stnk": 260,
    "ca": 260,
    "ss": 220,
    "msub": 220,
    "mig": 180,
    "yak": 160,
    "heli": 140,
    "mh60": 140,
}
QUEUE_IDLE_BASE_CAPS: dict[str, int] = {
    "Infantry": 14,
    "Vehicle": 9,
    "Plane": 4,
    "Ship": 4,
    "Aircraft": 3,
}
IDLE_BASE_UNIT_RADIUS = 15
AIRFIELD_PLANE_CAPACITY = 4
HELIPAD_AIRCRAFT_CAPACITY = 1
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
HARVESTER_THREAT_RADIUS = 10
HARVESTER_RETREAT_COOLDOWN = 120
LOW_EFFECT_HARVESTER_SCAN_INTERVAL = 433
RESOURCE_CELLS_PER_HARVESTER = 4
HARVESTER_PATCH_ASSIGN_RADIUS = 12
HARVESTER_REASSIGN_COOLDOWN = 650
HARVESTER_REASSIGN_REFINERY_RADIUS = 14
HARVESTER_LOW_EFFECT_TIMEOUT = 500
HARVESTER_NO_RESOURCE_COOLDOWN = 300
HARVESTER_PROGRESS_MOVE_THRESHOLD = 2
HARVESTER_LOCAL_RESOURCE_MIN = 0.5
POST_CONTACT_WINDOW = 2400
RECOVERY_DURATION = 2600
RECOVERY_TRIGGER_PEAK = 24
RECOVERY_DROP_RATIO = 0.6
RECOVERY_MIN_COMBAT = 16
RECOVERY_EXIT_COMBAT = 24
RECOVERY_HARVESTER_CAP = 2
RECOVERY_CLEAR_CONTACT_GAP = 450
RECOVERY_REFINERY_REBUILD_CREDITS = 2000
HOME_GUARD_MIN_RESERVE = 6
HOME_GUARD_MAX_RESERVE = 10
RUSH_SQUAD_MIN_SIZE = 6
AIR_SQUAD_MIN_SIZE = 2
NAVAL_SQUAD_MIN_SIZE = 2
PROTECTION_SQUAD_MIN_SIZE = 4
SQUAD_RETREAT_HOLD_TICKS = 180
SQUAD_RECOVER_HOLD_TICKS = 240
RUSH_COMBAT_TYPES = {"e1", "e3", "apc", "jeep", "1tnk", "2tnk", "3tnk", "arty", "v2rl"}
FORCE_COMMIT_UNIT_THRESHOLD = 30
FORCE_COMMIT_REGROUPS = 3
FORCE_COMMIT_COOLDOWN = 400


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
        self._next_build_check_tick = 0
        self._placement_fail_counts: dict[str, int] = {}
        self._placement_pending: dict[str, tuple[str, int, int]] = {}
        self._placement_backoff_until: dict[str, int] = {}
        self._placement_backoff_snapshot: dict[str, tuple[int, int]] = {}
        self._next_placement_attempt_tick: dict[str, int] = {}
        self._naval_retry_buildable_count = -1

        # Rally
        self._rally_set: set[int] = set()

        # Squads
        self._attack_squad: list[int] = []
        self._protection_squad: list[int] = []
        self._rush_squad: list[int] = []
        self._air_squad: list[int] = []
        self._naval_squad: list[int] = []
        self._temporary_defenders: set[int] = set()
        self._last_attack_tick = 0
        self._last_rush_tick = 0
        self._last_assign_tick = 0
        self._last_protection_tick = -9999
        self._assault_threshold = self._roll_assault_threshold()
        self._squad_states: dict[str, str] = {
            "assault": "assemble",
            "protection": "assemble",
            "rush": "assemble",
            "air": "assemble",
            "naval": "assemble",
        }
        self._squad_state_until: dict[str, int] = {}
        self._squad_regroup_count: dict[str, int] = {}
        self._squad_last_commit_tick: dict[str, int] = {}
        self._enemy_base_pos: Optional[Tuple[int, int]] = None
        self._stale_attack_target: Optional[Tuple[int, int]] = None
        self._stale_attack_redirects = 0

        # Repair / power
        self._repair_issued: set[int] = set()
        self._last_repair_tick = -9999
        self._powered_down: dict[int, int] = {}
        self._last_power_toggle_tick = -9999

        # Economy / production
        self._last_harvester_scan_tick = -9999
        self._last_harvester_reassign_tick = -9999
        self._last_unit_tick = -9999
        self._current_queue_index = -1
        self._unit_requests: list[str] = []
        self._queue_delay_until: dict[str, int] = {}
        self._unit_delay_until: dict[str, int] = {}
        self._last_mcv_scan_tick = -9999
        self._last_mcv_build_tick = -9999
        self._mcv_targets: dict[int, tuple[int, int]] = {}
        self._harvester_retreat_until: dict[int, int] = {}
        self._harvester_reassign_until: dict[int, int] = {}
        self._harvester_patch_targets: dict[int, tuple[int, int]] = {}
        self._harvester_last_cells: dict[int, tuple[int, int]] = {}
        self._harvester_last_progress_tick: dict[int, int] = {}
        self._harvester_no_resource_until: dict[int, int] = {}
        self._combat_peak = 0
        self._last_contact_tick = -9999
        self._recovery_until_tick = -9999

        # Map
        self._cached_map_size: Optional[Tuple[int, int]] = None
        self._candidate_targets: list[Tuple[int, int]] = []
        self._target_index = 0
        self._spatial_raw: bytes = b""
        self._spatial_channels = 0
        self._last_spatial_update_tick = -9999
        self._resource_patches: list[dict[str, float | int]] = []
        self._resource_patch_memory: dict[tuple[int, int], dict[str, float | int]] = {}
        self._last_naval_gate_tick = -9999
        self._cached_naval_gate_ok = False

    def decide(self, obs: OpenRAObservation) -> OpenRAAction:
        commands: List[CommandModel] = []
        self._update_map_size(obs)
        self._update_spatial_analysis(obs)
        self._update_phase(obs)
        self._cleanup_dead(obs)
        self._update_post_contact_state(obs)

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
        active_queues: set[str] = set()
        counts = self._building_counts(obs)
        for prod in obs.production:
            if self._is_structure_queue(prod.queue_type) and prod.progress >= 0.99:
                queue_type = prod.queue_type
                active_queues.add(queue_type)
                pending = self._placement_pending.get(queue_type)
                current_count = counts.get(self._canonical_building_type(prod.item), 0)
                if pending is not None:
                    pending_item, pending_count, pending_tick = pending
                    if pending_item == prod.item:
                        if current_count > pending_count:
                            self._placement_fail_counts[queue_type] = 0
                            self._placement_pending.pop(queue_type, None)
                            continue
                        if obs.tick < pending_tick + PLACEMENT_CONFIRMATION_DELAY:
                            continue
                        self._placement_fail_counts[queue_type] = self._placement_fail_counts.get(queue_type, 0) + 1
                        self._placement_pending.pop(queue_type, None)
                    elif pending_item != prod.item:
                        self._placement_pending.pop(queue_type, None)
                        self._placement_fail_counts.pop(queue_type, None)

                if self._queue_backoff_active(queue_type, obs):
                    if obs.tick >= self._next_placement_attempt_tick.get(queue_type, -9999):
                        commands.append(CommandModel(action=ActionType.CANCEL_PRODUCTION, item_type=prod.item))
                        self._next_placement_attempt_tick[queue_type] = obs.tick + PLACEMENT_ATTEMPT_INTERVAL
                    continue

                if self._placement_fail_counts.get(queue_type, 0) >= MAX_FAILED_PLACEMENT_ATTEMPTS:
                    commands.append(CommandModel(action=ActionType.CANCEL_PRODUCTION, item_type=prod.item))
                    self._placement_backoff_until[queue_type] = obs.tick + STRUCTURE_PRODUCTION_RESUME_DELAY
                    self._placement_backoff_snapshot[queue_type] = (
                        len(obs.buildings),
                        sum(1 for b in obs.buildings if b.type == "fact"),
                    )
                    self._placement_fail_counts[queue_type] = 0
                    self._placement_pending.pop(queue_type, None)
                    self._next_placement_attempt_tick[queue_type] = obs.tick + PLACEMENT_ATTEMPT_INTERVAL
                    if self._canonical_building_type(prod.item) in NAVAL_STRUCTURE_TYPES:
                        self._naval_retry_buildable_count = self._buildable_area_structure_count(obs)
                    self._rewind_build_order_after_cancel(obs, prod.item)
                    self._log(f"Canceling {prod.item} after repeated placement failures; backing off {queue_type} queue")
                    continue

                if obs.tick < self._next_placement_attempt_tick.get(queue_type, -9999):
                    continue

                location = self._placement_offset(obs, cy, prod.item)
                if location is None:
                    self._placement_fail_counts[queue_type] = self._placement_fail_counts.get(queue_type, 0) + 1
                    self._next_placement_attempt_tick[queue_type] = obs.tick + PLACEMENT_ATTEMPT_INTERVAL
                    continue
                x, y = location
                commands.append(CommandModel(
                    action=ActionType.PLACE_BUILDING,
                    item_type=prod.item,
                    target_x=x, target_y=y,
                ))
                self._placement_count += 1
                self._placement_pending[queue_type] = (prod.item, current_count, obs.tick)
                self._next_placement_attempt_tick[queue_type] = obs.tick + PLACEMENT_ATTEMPT_INTERVAL

        for queue_type in list(self._placement_pending):
            if queue_type not in active_queues:
                self._placement_pending.pop(queue_type, None)
                self._placement_fail_counts.pop(queue_type, None)
        return commands

    def _placement_offset(
        self,
        obs: OpenRAObservation,
        cy: BuildingInfoModel,
        item_type: str,
    ) -> Optional[Tuple[int, int]]:
        center = self._placement_base_center(obs)
        if center is None:
            center = self._building_top_left(cy)
        center = self._placement_anchor(obs, item_type, center)
        cx, cy_y = center
        queue_type = self._structure_queue_type(item_type)
        retry_index = self._placement_fail_counts.get(queue_type, 0)

        min_radius = DEFENSE_BUILD_MIN_RADIUS if item_type in ENEMY_FACING_STRUCTURE_TYPES else BASE_BUILD_MIN_RADIUS
        max_radius = DEFENSE_BUILD_MAX_RADIUS if item_type in ENEMY_FACING_STRUCTURE_TYPES else BASE_BUILD_MAX_RADIUS
        if item_type not in ENEMY_FACING_STRUCTURE_TYPES:
            max_radius += min(retry_index * 4, 20)
        candidates = self._placement_candidates(obs, item_type, cx, cy_y, min_radius, max_radius)

        if not candidates:
            return None

        if item_type == "proc":
            plan = self._best_refinery_plan(obs)
            target = plan["target"] if plan is not None else center
            refineries = [b for b in obs.buildings if b.type == "proc"]
            candidates.sort(
                key=lambda p: (
                    self._resource_amount_at(*p) > 0.0,
                    -self._local_resource_score(p[0], p[1], 4),
                    self._cell_distance(p[0], p[1], target[0], target[1]),
                    -self._nearest_distance_to_buildings(p[0], p[1], refineries),
                    self._cell_distance(p[0], p[1], cx, cy_y),
                )
            )
            idx = (self._placement_count + retry_index * 5) % min(len(candidates), 16)
            return candidates[idx]

        if item_type in NAVAL_STRUCTURE_TYPES:
            candidates.sort(
                key=lambda p: (
                    -self._local_water_score(p[0], p[1], 2),
                    self._cell_distance(p[0], p[1], cx, cy_y),
                )
            )
            idx = (self._placement_count + retry_index * 5) % min(len(candidates), 16)
            return candidates[idx]

        if item_type in ENEMY_FACING_STRUCTURE_TYPES and self._enemy_base_pos is not None:
            tx, ty = self._enemy_base_pos
            candidates.sort(key=lambda p: ((p[0] - tx) ** 2 + (p[1] - ty) ** 2, (p[0] - cx) ** 2 + (p[1] - cy_y) ** 2))
            idx = (self._placement_count + retry_index * 5) % min(len(candidates), 16)
            return candidates[idx]

        idx = (self._placement_count + retry_index * 7) % len(candidates)
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
        if obs.tick < self._next_build_check_tick:
            return commands

        structure_in_queue = any(self._is_structure_queue(p.queue_type) for p in obs.production)
        if structure_in_queue:
            return commands
        credits = self._available_credits(obs)
        if credits < PRODUCTION_MIN_CASH_REQUIREMENT:
            self._schedule_next_build_check(obs, active=False)
            return commands

        # Phase 1: follow the fixed build order
        if self._build_index < len(BUILD_ORDER):
            item = self._resolve_build_item(obs, BUILD_ORDER[self._build_index])
            if item is None:
                self._schedule_next_build_check(obs, active=False)
                return commands
            if not self._structure_queue_available(obs, item):
                self._schedule_next_build_check(obs, active=False)
                return commands
            if self._already_have(obs, item, self._build_index):
                self._build_index += 1
                return commands
            if self._can_produce(obs, item):
                cost = self._build_cost(item)
                if credits >= cost:
                    self._log(
                        f"Building {item} [{self._build_index+1}/{len(BUILD_ORDER)}] "
                        f"({self._credits_str(obs)})"
                    )
                    commands.append(CommandModel(action=ActionType.BUILD, item_type=item))
                    self._last_build_tick = obs.tick
                    self._schedule_next_build_check(obs, active=True)
                    self._build_index += 1
                else:
                    self._schedule_next_build_check(obs, active=False)
            else:
                self._schedule_next_build_check(obs, active=False)
            return commands

        # Phase 2: dynamic base building driven by the normal AI priorities.
        item = self._choose_recovery_building(obs) if self._in_recovery_mode(obs) else self._choose_dynamic_building(obs)
        if item and self._structure_queue_available(obs, item) and self._can_produce(obs, item):
            cost = self._build_cost(item)
            if credits >= cost:
                self._log(f"Building {item} (dynamic, {self._credits_str(obs)})")
                commands.append(CommandModel(action=ActionType.BUILD, item_type=item))
                self._last_build_tick = obs.tick
                self._schedule_next_build_check(obs, active=True)
            else:
                self._schedule_next_build_check(obs, active=False)
        else:
            self._schedule_next_build_check(obs, active=False)

        return commands

    def _resolve_build_item(self, obs: OpenRAObservation, placeholder: str) -> Optional[str]:
        variants = BUILDING_VARIANT_CHOICES.get(placeholder)
        if variants is not None:
            for btype in variants:
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

        naval_item = self._preferred_early_naval_building(obs, credits)
        if naval_item is not None:
            return naval_item

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
            credits > NEW_PRODUCTION_CASH_THRESHOLD
            and random.randrange(100) < NEW_PRODUCTION_CHANCE
            and self._can_safely_build_naval_structure(obs)
        ):
            naval_production = self._best_naval_production_building(obs)
            if naval_production:
                return naval_production

        if (
            obs.economy.resource_capacity > 0
            and obs.economy.ore >= obs.economy.resource_capacity * SILO_BUILD_THRESHOLD
            and self._can_produce(obs, "silo")
            and self._structure_queue_available(obs, "silo")
        ):
            return "silo"

        total_buildings = max(1, len(obs.buildings))
        candidates = list(BUILDING_FRACTIONS.keys())
        random.shuffle(candidates)
        for item in candidates:
            if BUILDING_DELAYS.get(item, 0) > obs.tick:
                continue
            resolved_item = self._resolve_build_item(obs, item)
            if resolved_item is None or not self._can_produce(obs, resolved_item):
                continue
            if not self._structure_queue_available(obs, resolved_item):
                continue
            canonical_item = self._canonical_building_type(resolved_item)
            count = bldg_counts.get(canonical_item, 0)
            limit = BUILDING_LIMITS.get(canonical_item)
            if limit is not None and count >= limit:
                continue
            if count * 100 > BUILDING_FRACTIONS[item] * total_buildings:
                continue
            return resolved_item

        return None

    def _choose_recovery_building(self, obs: OpenRAObservation) -> Optional[str]:
        bldg_counts = self._building_counts(obs)
        credits = self._available_credits(obs)
        power_balance = obs.economy.power_provided - obs.economy.power_drained
        minimum_excess_power = self._minimum_excess_power_target(obs)
        power_item = self._best_power_building(obs)

        if power_balance < minimum_excess_power and power_item:
            return power_item

        refinery_count = bldg_counts.get("proc", 0)
        if (
            refinery_count == 0
            and not self._base_under_pressure(obs)
            and credits >= RECOVERY_REFINERY_REBUILD_CREDITS
            and self._can_produce(obs, "proc")
            and self._structure_queue_available(obs, "proc")
        ):
            return "proc"

        if not any(b.type in WAR_FACTORY_TYPES for b in obs.buildings):
            if self._can_produce(obs, "weap") and self._structure_queue_available(obs, "weap"):
                return "weap"

        if not any(b.type in BARRACKS_TYPES for b in obs.buildings):
            barracks = self._resolve_build_item(obs, "barracks")
            if barracks and self._can_produce(obs, barracks) and self._structure_queue_available(obs, barracks):
                return barracks

        if self._base_under_pressure(obs):
            defense_count = sum(bldg_counts.get(item, 0) for item in ("ftur", "gun", "pbox"))
            defense_cap = 1 if self._combat_unit_count(obs) < RECOVERY_EXIT_COMBAT else 2
            if defense_count >= defense_cap:
                return power_item if power_balance < 0 and power_item else None
            for item in ("ftur", "gun", "pbox"):
                if not self._can_produce(obs, item):
                    continue
                if not self._structure_queue_available(obs, item):
                    continue
                limit = BUILDING_LIMITS.get(item)
                if limit is not None and bldg_counts.get(item, 0) >= limit:
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
        reserved_for_build = max(self._pending_build_cost(obs), self._priority_structure_reservation(obs))
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
            if self._queue_delay_active(obs, queue_type):
                continue
            unit = self._pick_unit(obs, allowed)
            if unit:
                commands.append(CommandModel(action=ActionType.TRAIN, item_type=unit))
                self._mark_unit_trained(obs, unit, queue_type)
                break

        return commands

    def _pick_unit(self, obs: OpenRAObservation, allowed: set[str]) -> Optional[str]:
        unit_counts: dict[str, int] = {}
        total_units = 0
        for u in obs.units:
            unit_counts[u.type] = unit_counts.get(u.type, 0) + 1
            if u.type in UNITS_TO_BUILD:
                total_units += 1
        for p in obs.production:
            unit_counts[p.item] = unit_counts.get(p.item, 0) + 1
            if p.item in UNITS_TO_BUILD:
                total_units += 1
        for item in self._unit_requests:
            unit_counts[item] = unit_counts.get(item, 0) + 1
            if item in UNITS_TO_BUILD:
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
        reassignment_commands, redirected_harvesters = self._reassign_low_effect_harvesters(obs)
        commands.extend(reassignment_commands)
        for u in obs.units:
            if u.type != "harv":
                continue
            self._update_harvester_progress(obs, u)

            no_resource_target = self._harvester_patch_targets.get(u.actor_id)
            if (
                no_resource_target is not None
                and self._local_resource_score(no_resource_target[0], no_resource_target[1], 2) <= HARVESTER_LOCAL_RESOURCE_MIN
            ):
                self._harvester_no_resource_until[u.actor_id] = obs.tick + HARVESTER_NO_RESOURCE_COOLDOWN
                self._harvester_patch_targets.pop(u.actor_id, None)

            if u.actor_id in redirected_harvesters:
                continue

            threat = self._nearest_enemy_to_unit(obs, u, HARVESTER_THREAT_RADIUS)
            if threat is not None:
                self._last_contact_tick = obs.tick
                current_target = self._harvester_patch_targets.get(u.actor_id)
                if current_target is not None:
                    threatened_state = self._nearest_patch_state(
                        self._resource_patch_states(obs),
                        current_target[0],
                        current_target[1],
                        HARVESTER_PATCH_ASSIGN_RADIUS,
                        allow_fallback=True,
                    )
                    if threatened_state is not None and int(threatened_state["threat"]) > 0:
                        self._harvester_patch_targets.pop(u.actor_id, None)
                if obs.tick >= self._harvester_retreat_until.get(u.actor_id, -9999):
                    fallback = self._pick_harvester_retreat_point(obs, u)
                    if fallback is not None:
                        commands.append(CommandModel(
                            action=ActionType.MOVE,
                            actor_id=u.actor_id,
                            target_x=fallback[0],
                            target_y=fallback[1],
                        ))
                        self._harvester_retreat_until[u.actor_id] = obs.tick + HARVESTER_RETREAT_COOLDOWN
                        self._harvester_last_progress_tick[u.actor_id] = obs.tick
                continue

            if self._is_low_effect_harvester(obs, u):
                fallback_target = self._fallback_harvest_target(obs, u)
                if fallback_target is not None:
                    commands.append(CommandModel(
                        action=ActionType.HARVEST,
                        actor_id=u.actor_id,
                        target_x=fallback_target[0],
                        target_y=fallback_target[1],
                    ))
                    self._harvester_patch_targets[u.actor_id] = fallback_target
                    self._harvester_reassign_until[u.actor_id] = obs.tick + HARVESTER_REASSIGN_COOLDOWN
                    self._harvester_last_progress_tick[u.actor_id] = obs.tick
                    redirected_harvesters.add(u.actor_id)
                    continue
                self._harvester_no_resource_until[u.actor_id] = obs.tick + HARVESTER_NO_RESOURCE_COOLDOWN

            if u.is_idle:
                target = self._harvester_patch_targets.get(u.actor_id)
                if target is not None and self._local_resource_score(target[0], target[1], 2) > 0:
                    commands.append(
                        CommandModel(
                            action=ActionType.HARVEST,
                            actor_id=u.actor_id,
                            target_x=target[0],
                            target_y=target[1],
                        )
                    )
                    self._harvester_last_progress_tick[u.actor_id] = obs.tick
                else:
                    self._harvester_patch_targets.pop(u.actor_id, None)
                    commands.append(CommandModel(action=ActionType.HARVEST, actor_id=u.actor_id))
                    self._harvester_last_progress_tick[u.actor_id] = obs.tick

        self._ensure_harvester_requests(obs)
        return commands

    # ── Expansion ────────────────────────────────────────────────

    def _manage_expansion(self, obs: OpenRAObservation) -> List[CommandModel]:
        commands = []
        if self.phase == "deploy_mcv" or self._in_recovery_mode(obs):
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
            if item_type == "harv":
                current = self._current_unit_count(obs, "harv")
                pending = sum(1 for p in obs.production if p.item == "harv")
                queued = self._requested_production_count("harv")
                if current + pending + queued - 1 >= self._harvester_target(obs):
                    del self._unit_requests[idx]
                    continue
                if self._should_delay_harvester_request(obs, current):
                    idx += 1
                    continue
            queue_type = self._queue_type_for_unit(item_type)
            if queue_type is None:
                del self._unit_requests[idx]
                continue
            if item_type not in {"harv", "mcv"} and self._queue_delay_active(obs, queue_type):
                idx += 1
                continue
            if item_type not in {"harv", "mcv"} and self._unit_delay_active(obs, item_type):
                idx += 1
                continue
            if any(p.queue_type == queue_type for p in obs.production):
                idx += 1
                continue
            if not self._can_produce(obs, item_type):
                idx += 1
                continue
            if not self._production_support_available(obs, item_type):
                idx += 1
                continue
            if self._unit_at_limit(obs, item_type):
                del self._unit_requests[idx]
                continue

            del self._unit_requests[idx]
            self._mark_unit_trained(obs, item_type, queue_type)
            self._log(f"Training {item_type} (requested)")
            return CommandModel(action=ActionType.TRAIN, item_type=item_type)

        return None

    # ── Squads ────────────────────────────────────────────────────

    def _manage_squads(self, obs: OpenRAObservation) -> List[CommandModel]:
        commands = []
        if self.phase in ("deploy_mcv", "build_base"):
            return commands

        self._temporary_defenders = set()

        if obs.tick - self._last_assign_tick >= ASSIGN_ROLES_INTERVAL:
            self._last_assign_tick = obs.tick
            self._assign_squad_roles(obs)

        commands.extend(self._handle_defense(obs))

        if obs.tick - self._last_attack_tick >= ATTACK_FORCE_INTERVAL:
            self._last_attack_tick = obs.tick
            commands.extend(self._handle_attack(obs))

        return commands

    def _assign_squad_roles(self, obs: OpenRAObservation):
        combat_units = [
            u
            for u in obs.units
            if u.type not in EXCLUDE_FROM_SQUADS and u.type in COMBAT_TYPES
        ]
        air_ids = [u.actor_id for u in combat_units if u.type in AIRCRAFT_TYPES]
        naval_ids = [u.actor_id for u in combat_units if u.type in SHIP_TYPES]
        ground_units = [u for u in combat_units if u.type not in AIRCRAFT_TYPES | SHIP_TYPES]

        base_center = self._base_center(obs)
        if base_center is not None:
            ground_units.sort(
                key=lambda u: self._cell_distance(u.cell_x, u.cell_y, base_center[0], base_center[1])
            )
        else:
            ground_units.sort(key=lambda u: u.actor_id)

        protection_count = 0
        if ground_units:
            if len(ground_units) < self._assault_threshold:
                protection_count = min(max(2, len(ground_units) // 4), max(0, len(ground_units) // 2))
            else:
                protection_count = min(HOME_GUARD_MAX_RESERVE, max(PROTECTION_SQUAD_MIN_SIZE, len(ground_units) // 5))
            if self._base_under_pressure(obs):
                protection_count = min(HOME_GUARD_MAX_RESERVE, max(PROTECTION_SQUAD_MIN_SIZE, protection_count + 2))
            keep_for_field = max(4, self._assault_threshold // 3)
            protection_count = min(protection_count, max(0, len(ground_units) - keep_for_field))
            if self._base_under_pressure(obs) and protection_count == 0:
                protection_count = min(len(ground_units), PROTECTION_SQUAD_MIN_SIZE)

        protection_ids = [u.actor_id for u in ground_units[:protection_count]]
        remaining_ground = [u for u in ground_units if u.actor_id not in set(protection_ids)]

        rush_ids: list[int] = []
        rush_candidates = [u for u in remaining_ground if u.type in RUSH_COMBAT_TYPES]
        rush_window_open = obs.tick < RUSH_TICKS or obs.tick - self._last_rush_tick >= RUSH_INTERVAL
        if rush_window_open and rush_candidates:
            rush_count = min(
                len(rush_candidates),
                max(RUSH_SQUAD_MIN_SIZE, len(rush_candidates) // 2),
            )
            keep_for_assault = max(4, self._assault_threshold // 3)
            rush_count = min(rush_count, max(0, len(remaining_ground) - keep_for_assault))
            rush_ids = [u.actor_id for u in rush_candidates[:rush_count]]

        rush_id_set = set(rush_ids)
        self._protection_squad = protection_ids
        self._rush_squad = rush_ids
        self._attack_squad = [u.actor_id for u in remaining_ground if u.actor_id not in rush_id_set]
        self._air_squad = air_ids
        self._naval_squad = naval_ids

    def _squad_units(self, obs: OpenRAObservation, squad_ids: list[int]) -> list[UnitInfoModel]:
        alive = {u.actor_id: u for u in obs.units}
        return [alive[uid] for uid in squad_ids if uid in alive]

    def _set_squad_state(self, squad_name: str, state: str, until: Optional[int] = None):
        self._squad_states[squad_name] = state
        if until is None:
            self._squad_state_until.pop(squad_name, None)
        else:
            self._squad_state_until[squad_name] = until

    def _current_squad_state(self, obs: OpenRAObservation, squad_name: str) -> str:
        state = self._squad_states.get(squad_name, "assemble")
        hold_until = self._squad_state_until.get(squad_name, -9999)
        if state in {"retreat", "recover"} and hold_until > obs.tick:
            return state
        if state in {"retreat", "recover"} and hold_until <= obs.tick:
            self._set_squad_state(squad_name, "assemble")
            return "assemble"
        return state

    def _assemble_squad_commands(
        self,
        obs: OpenRAObservation,
        squad_name: str,
        squad_units: list[UnitInfoModel],
    ) -> List[CommandModel]:
        if not squad_units or squad_name == "naval":
            return []

        anchor = self._base_center(obs)
        if anchor is None:
            leader = self._select_squad_leader(squad_units)
            anchor = (leader.cell_x, leader.cell_y)

        commands: list[CommandModel] = []
        redirected = 0
        for unit in squad_units:
            if self._cell_distance(unit.cell_x, unit.cell_y, anchor[0], anchor[1]) <= REGROUP_RADIUS:
                continue
            commands.append(CommandModel(
                action=ActionType.ATTACK_MOVE,
                actor_id=unit.actor_id,
                target_x=anchor[0],
                target_y=anchor[1],
            ))
            redirected += 1
        if redirected:
            self._log(f"Assembling {squad_name} squad ({redirected}/{len(squad_units)})")
        return commands

    def _emergency_defense_units(self, obs: OpenRAObservation, needed: int) -> list[UnitInfoModel]:
        if needed <= 0:
            return []

        alive = {u.actor_id: u for u in obs.units}
        reserve_ids = set(self._protection_squad)
        candidates = [
            alive[uid]
            for uid in self._rush_squad + self._attack_squad
            if uid in alive and uid not in reserve_ids and alive[uid].can_attack
        ]
        base_center = self._base_center(obs)
        if base_center is not None:
            candidates.sort(
                key=lambda u: self._cell_distance(u.cell_x, u.cell_y, base_center[0], base_center[1])
            )
        return candidates[:needed]

    def _handle_field_squad(
        self,
        obs: OpenRAObservation,
        squad_name: str,
        squad_units: list[UnitInfoModel],
        minimum_commitment: int,
        rush: bool,
    ) -> List[CommandModel]:
        commands: list[CommandModel] = []
        if not squad_units:
            self._set_squad_state(squad_name, "assemble")
            return commands

        if squad_name not in self._squad_regroup_count:
            self._squad_regroup_count[squad_name] = 0
        if squad_name not in self._squad_last_commit_tick:
            self._squad_last_commit_tick[squad_name] = -9999

        state = self._current_squad_state(obs, squad_name)
        leader = self._select_squad_leader(squad_units)
        local_enemy_units = self._visible_enemy_units_near(obs, leader.cell_x, leader.cell_y, LOCAL_FIGHT_RADIUS)
        local_enemy_buildings = self._visible_enemy_buildings_near(obs, leader.cell_x, leader.cell_y, LOCAL_FIGHT_RADIUS)

        if local_enemy_units or local_enemy_buildings:
            self._last_contact_tick = obs.tick
            self._reset_stale_attack_target()
            if not self._should_take_local_fight(
                squad_units,
                local_enemy_units,
                local_enemy_buildings,
                rush=rush or squad_name in {"air", "naval"},
                cautious=state == "recover",
                squad_name=squad_name,
            ):
                self._set_squad_state(squad_name, "retreat", obs.tick + SQUAD_RETREAT_HOLD_TICKS)
                retreat_commands = self._retreat_squad_commands(obs, squad_units, leader)
                if retreat_commands:
                    return retreat_commands

            priority_target = self._pick_priority_target(
                obs,
                leader.cell_x,
                leader.cell_y,
                local_only=True,
                squad_name=squad_name,
            )
            if priority_target is not None:
                self._set_squad_state(squad_name, "commit")
                if rush:
                    self._last_rush_tick = obs.tick
                focus_commands = self._focus_fire_commands(squad_units, priority_target)
                if focus_commands:
                    return focus_commands

        if state == "retreat":
            retreat_commands = self._retreat_squad_commands(obs, squad_units, leader)
            if retreat_commands:
                return retreat_commands
            self._set_squad_state(squad_name, "recover", obs.tick + SQUAD_RECOVER_HOLD_TICKS)
            self._squad_regroup_count[squad_name] = 0

        if state == "recover" and not (local_enemy_units or local_enemy_buildings):
            return self._assemble_squad_commands(obs, squad_name, squad_units)

        if self._base_under_pressure(obs) and squad_name in {"assault", "rush"} and not (local_enemy_units or local_enemy_buildings):
            self._set_squad_state(squad_name, "recover", obs.tick + SQUAD_RECOVER_HOLD_TICKS)
            return self._assemble_squad_commands(obs, squad_name, squad_units)

        if self._in_recovery_mode(obs) and squad_name in {"assault", "rush"}:
            self._set_squad_state(squad_name, "recover", obs.tick + SQUAD_RECOVER_HOLD_TICKS)
            assemble_commands = self._assemble_squad_commands(obs, squad_name, squad_units)
            return assemble_commands

        if len(squad_units) < minimum_commitment:
            self._set_squad_state(squad_name, "assemble")
            if squad_name == "assault":
                self._assault_threshold = self._roll_assault_threshold()
            self._squad_regroup_count[squad_name] = 0
            return self._assemble_squad_commands(obs, squad_name, squad_units)

        if squad_name == "naval" and not (
            local_enemy_units or local_enemy_buildings or obs.visible_enemies or obs.visible_enemy_buildings
        ):
            self._set_squad_state(squad_name, "assemble")
            return commands

        force_commit = False
        if squad_name in {"assault", "rush"}:
            regroup_attempts = self._squad_regroup_count.get(squad_name, 0)
            last_commit_tick = self._squad_last_commit_tick.get(squad_name, -9999)
            if (
                regroup_attempts >= FORCE_COMMIT_REGROUPS
                and len(squad_units) >= FORCE_COMMIT_UNIT_THRESHOLD
                and not local_enemy_units
                and not local_enemy_buildings
                and obs.tick - last_commit_tick >= FORCE_COMMIT_COOLDOWN
                and not self._base_under_pressure(obs)
            ):
                force_commit = True

        regroup_commands = self._regroup_squad_commands(squad_units, leader)
        if regroup_commands:
            self._set_squad_state(squad_name, "regroup")
            self._squad_regroup_count[squad_name] = self._squad_regroup_count.get(squad_name, 0) + 1
            return regroup_commands

        self._set_squad_state(squad_name, "commit")
        tx, ty = self._find_attack_target(obs, leader.cell_x, leader.cell_y, squad_name=squad_name)
        if squad_name in {"assault", "rush"}:
            self._track_stale_attack_target(obs, leader, tx, ty)
        if force_commit and squad_name == "assault":
            attackers = squad_units
        else:
            attackers = self._attack_wave_units(obs, squad_units) if squad_name == "assault" else squad_units
        for unit in attackers:
            commands.append(CommandModel(
                action=ActionType.ATTACK_MOVE,
                actor_id=unit.actor_id,
                target_x=tx,
                target_y=ty,
            ))
        if commands and rush:
            self._last_rush_tick = obs.tick
        if commands:
            self._squad_regroup_count[squad_name] = 0
            self._squad_last_commit_tick[squad_name] = obs.tick
        return commands

    def _handle_defense(self, obs: OpenRAObservation) -> List[CommandModel]:
        commands = []
        protection_units = self._squad_units(obs, self._protection_squad)
        if not protection_units:
            self._set_squad_state("protection", "assemble")
            return commands

        threat = next(iter(self._base_threat_enemies(obs)), None)
        if not threat:
            current_state = self._current_squad_state(obs, "protection")
            if current_state in {"commit", "retreat"}:
                self._set_squad_state("protection", "recover", obs.tick + SQUAD_RECOVER_HOLD_TICKS)
            return self._assemble_squad_commands(obs, "protection", protection_units)

        if obs.tick - self._last_protection_tick < PROTECTION_RESPONSE_COOLDOWN:
            return commands

        self._last_protection_tick = obs.tick
        self._last_contact_tick = obs.tick
        self._set_squad_state("protection", "commit")

        defenders = list(protection_units)
        enemy_pressure = len(self._base_threat_enemies(obs))
        defenders.extend(self._emergency_defense_units(obs, max(0, enemy_pressure - len(defenders))))

        seen: set[int] = set()
        unique_defenders: list[UnitInfoModel] = []
        for defender in defenders:
            if defender.actor_id in seen:
                continue
            seen.add(defender.actor_id)
            unique_defenders.append(defender)

        self._temporary_defenders = {defender.actor_id for defender in unique_defenders}

        priority_target = self._pick_priority_target(
            obs,
            threat.cell_x,
            threat.cell_y,
            local_only=True,
            squad_name="protection",
        )
        if priority_target is not None:
            return self._focus_fire_commands(unique_defenders, priority_target)

        for defender in unique_defenders:
            if not defender.can_attack:
                continue
            commands.append(CommandModel(
                action=ActionType.ATTACK_MOVE,
                actor_id=defender.actor_id,
                target_x=threat.cell_x, target_y=threat.cell_y,
            ))
        return commands

    def _handle_attack(self, obs: OpenRAObservation) -> List[CommandModel]:
        commands = []
        commands.extend(
            self._handle_field_squad(
                obs,
                "assault",
                [u for u in self._squad_units(obs, self._attack_squad) if u.actor_id not in self._temporary_defenders],
                self._assault_threshold,
                rush=False,
            )
        )
        commands.extend(
            self._handle_field_squad(
                obs,
                "rush",
                [u for u in self._squad_units(obs, self._rush_squad) if u.actor_id not in self._temporary_defenders],
                RUSH_SQUAD_MIN_SIZE,
                rush=True,
            )
        )
        commands.extend(
            self._handle_field_squad(
                obs,
                "air",
                [u for u in self._squad_units(obs, self._air_squad) if u.actor_id not in self._temporary_defenders],
                AIR_SQUAD_MIN_SIZE,
                rush=False,
            )
        )
        commands.extend(
            self._handle_field_squad(
                obs,
                "naval",
                [u for u in self._squad_units(obs, self._naval_squad) if u.actor_id not in self._temporary_defenders],
                NAVAL_SQUAD_MIN_SIZE,
                rush=False,
            )
        )
        return commands

    def _find_attack_target(
        self,
        obs: OpenRAObservation,
        leader_x: Optional[int],
        leader_y: Optional[int],
        squad_name: str = "assault",
    ) -> Tuple[int, int]:
        priority = self._pick_priority_target(obs, None, None, local_only=False, squad_name=squad_name)
        if priority is not None:
            _, tx, ty, _, kind = priority
            if kind == "building":
                self._enemy_base_pos = (tx, ty)
            elif self._enemy_base_pos is None:
                self._enemy_base_pos = (tx, ty)
            self._reset_stale_attack_target()
            return tx, ty
        if self._enemy_base_pos and self._should_clear_enemy_base_target(obs, leader_x, leader_y):
            self._log(f"Clearing stale enemy base target {self._enemy_base_pos}")
            self._enemy_base_pos = None
            self._reset_stale_attack_target()
        if self._enemy_base_pos:
            return self._enemy_base_pos
        if not self._candidate_targets:
            self._candidate_targets = self._search_grid(obs)
        t = self._candidate_targets[self._target_index % len(self._candidate_targets)]
        self._target_index = (self._target_index + 1) % len(self._candidate_targets)
        return t

    def _track_stale_attack_target(
        self,
        obs: OpenRAObservation,
        leader: UnitInfoModel,
        tx: int,
        ty: int,
    ) -> None:
        if obs.visible_enemies or obs.visible_enemy_buildings:
            self._reset_stale_attack_target()
            return

        target = (tx, ty)
        reached_target = self._cell_distance(leader.cell_x, leader.cell_y, tx, ty) <= STALE_TARGET_REACHED_RADIUS
        if self._stale_attack_target == target:
            if reached_target:
                self._stale_attack_redirects += 1
        else:
            self._stale_attack_target = target
            self._stale_attack_redirects = 1 if reached_target else 0

    def _should_clear_enemy_base_target(
        self,
        obs: OpenRAObservation,
        leader_x: Optional[int],
        leader_y: Optional[int],
    ) -> bool:
        if self._enemy_base_pos is None:
            return False
        if obs.visible_enemies or obs.visible_enemy_buildings:
            return False

        tx, ty = self._enemy_base_pos
        if (
            leader_x is not None
            and leader_y is not None
            and self._cell_distance(leader_x, leader_y, tx, ty) <= STALE_TARGET_REACHED_RADIUS
        ):
            return True

        return (
            self._stale_attack_target == self._enemy_base_pos
            and self._stale_attack_redirects >= STALE_TARGET_REDIRECT_LIMIT
        )

    def _reset_stale_attack_target(self) -> None:
        self._stale_attack_target = None
        self._stale_attack_redirects = 0

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
        self._protection_squad = [uid for uid in self._protection_squad if uid in alive]
        self._rush_squad = [uid for uid in self._rush_squad if uid in alive]
        self._air_squad = [uid for uid in self._air_squad if uid in alive]
        self._naval_squad = [uid for uid in self._naval_squad if uid in alive]
        self._temporary_defenders &= alive
        self._squad_regroup_count = {k: v for k, v in self._squad_regroup_count.items() if k in self._squad_states}
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
        self._harvester_retreat_until = {
            actor_id: tick
            for actor_id, tick in self._harvester_retreat_until.items()
            if actor_id in alive
        }
        self._harvester_reassign_until = {
            actor_id: tick
            for actor_id, tick in self._harvester_reassign_until.items()
            if actor_id in alive
        }
        self._harvester_patch_targets = {
            actor_id: target
            for actor_id, target in self._harvester_patch_targets.items()
            if actor_id in alive
        }
        self._harvester_last_cells = {
            actor_id: cell
            for actor_id, cell in self._harvester_last_cells.items()
            if actor_id in alive
        }
        self._harvester_last_progress_tick = {
            actor_id: tick
            for actor_id, tick in self._harvester_last_progress_tick.items()
            if actor_id in alive
        }
        self._harvester_no_resource_until = {
            actor_id: tick
            for actor_id, tick in self._harvester_no_resource_until.items()
            if actor_id in alive
        }

    def _update_post_contact_state(self, obs: OpenRAObservation):
        combat_count = self._combat_unit_count(obs)
        was_recovering = self._in_recovery_mode(obs)
        self._combat_peak = max(self._combat_peak, combat_count)

        if self._base_under_pressure(obs):
            self._last_contact_tick = obs.tick

        had_recent_contact = obs.tick - self._last_contact_tick <= POST_CONTACT_WINDOW
        collapse_threshold = max(RECOVERY_MIN_COMBAT, int(self._combat_peak * RECOVERY_DROP_RATIO))
        if had_recent_contact and self._combat_peak >= RECOVERY_TRIGGER_PEAK and combat_count <= collapse_threshold:
            self._recovery_until_tick = max(self._recovery_until_tick, obs.tick + RECOVERY_DURATION)

        if (
            was_recovering
            and combat_count >= max(RECOVERY_EXIT_COMBAT, int(self._combat_peak * 0.75))
            and obs.tick - self._last_contact_tick >= RECOVERY_CLEAR_CONTACT_GAP
            and not self._base_under_pressure(obs)
        ):
            self._recovery_until_tick = obs.tick
            self._combat_peak = combat_count
        elif not had_recent_contact and combat_count < RECOVERY_TRIGGER_PEAK:
            self._combat_peak = max(combat_count, self._combat_peak - 1)

        is_recovering = self._in_recovery_mode(obs)
        if not was_recovering and is_recovering:
            self._log(f"Recovery mode -> rebuild ({combat_count} combat units, peak {self._combat_peak})")
        elif was_recovering and not is_recovering:
            self._log(f"Recovery mode -> cleared ({combat_count} combat units)")

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

    def _update_spatial_analysis(self, obs: OpenRAObservation):
        if not obs.spatial_map or obs.spatial_channels <= 0:
            return
        if (
            self._spatial_raw
            and obs.tick >= self._last_spatial_update_tick
            and obs.tick - self._last_spatial_update_tick < RESOURCE_MAP_UPDATE_INTERVAL
        ):
            return
        try:
            raw = base64.b64decode(obs.spatial_map)
        except Exception:
            return

        w, h = self._get_map_size()
        channels = obs.spatial_channels
        if w <= 0 or h <= 0 or channels <= 0:
            return

        self._spatial_raw = raw
        self._spatial_channels = channels
        self._last_spatial_update_tick = obs.tick

        resource_cells: list[tuple[int, int, float]] = []
        for y in range(h):
            for x in range(w):
                base_idx = (y * w + x) * channels
                try:
                    resource = struct.unpack_from("f", raw, (base_idx + 2) * 4)[0]
                except struct.error:
                    continue
                if resource > 0:
                    resource_cells.append((x, y, resource))

        self._resource_patches = self._cluster_resource_patches(resource_cells)
        self._sync_resource_patch_memory(obs.tick)

    def _cluster_resource_patches(
        self,
        resource_cells: list[tuple[int, int, float]],
    ) -> list[dict[str, float | int]]:
        if not resource_cells:
            return []

        density_by_cell = {(x, y): density for x, y, density in resource_cells}
        unvisited = set(density_by_cell.keys())
        patches: list[dict[str, float | int]] = []
        while unvisited:
            start = unvisited.pop()
            queue = [start]
            cluster = [(start[0], start[1], density_by_cell[start])]
            while queue:
                cx, cy = queue.pop()
                for dx in range(-RESOURCE_PATCH_LINK_RADIUS, RESOURCE_PATCH_LINK_RADIUS + 1):
                    for dy in range(-RESOURCE_PATCH_LINK_RADIUS, RESOURCE_PATCH_LINK_RADIUS + 1):
                        nx, ny = cx + dx, cy + dy
                        if (nx, ny) not in unvisited:
                            continue
                        unvisited.remove((nx, ny))
                        queue.append((nx, ny))
                        cluster.append((nx, ny, density_by_cell[(nx, ny)]))

            if len(cluster) < RESOURCE_PATCH_MIN_CELLS:
                continue

            center_x = sum(c[0] for c in cluster) // len(cluster)
            center_y = sum(c[1] for c in cluster) // len(cluster)
            total_density = sum(c[2] for c in cluster)
            resource_center = min(
                cluster,
                key=lambda c: ((c[0] - center_x) ** 2 + (c[1] - center_y) ** 2, -c[2]),
            )
            patches.append(
                {
                    "center_x": center_x,
                    "center_y": center_y,
                    "resource_center_x": resource_center[0],
                    "resource_center_y": resource_center[1],
                    "cells": len(cluster),
                    "total_density": round(total_density, 1),
                }
            )

        patches.sort(key=lambda p: (int(p["cells"]), float(p["total_density"])), reverse=True)
        return patches

    def _sync_resource_patch_memory(self, tick: int):
        previous = dict(self._resource_patch_memory)
        refreshed: dict[tuple[int, int], dict[str, float | int]] = {}

        for patch in self._resource_patches:
            target = self._patch_target(patch)
            match_key: Optional[tuple[int, int]] = None
            best_dist = RESOURCE_PATCH_MEMORY_MATCH_RADIUS + 1
            for key in previous:
                dist = self._cell_distance(target[0], target[1], key[0], key[1])
                if dist <= RESOURCE_PATCH_MEMORY_MATCH_RADIUS and dist < best_dist:
                    match_key = key
                    best_dist = dist

            memory = previous.pop(match_key) if match_key is not None else {}
            current_density = float(patch["total_density"])
            previous_density = float(memory.get("last_density", current_density))
            peak_density = max(
                current_density,
                previous_density,
                float(memory.get("peak_density", current_density)),
            )

            density_drop_ratio = 0.0
            if previous_density > 1e-6 and current_density < previous_density:
                density_drop_ratio = (previous_density - current_density) / previous_density

            depletion_ratio = 0.0
            if peak_density > 1e-6 and current_density < peak_density:
                depletion_ratio = (peak_density - current_density) / peak_density

            depletion_trend = float(memory.get("depletion_trend", 0.0)) * 0.7 + density_drop_ratio * 0.3
            refreshed[target] = {
                "last_density": current_density,
                "peak_density": peak_density,
                "depletion_ratio": max(0.0, min(1.0, depletion_ratio)),
                "depletion_trend": max(0.0, min(1.0, depletion_trend)),
                "last_tick": tick,
            }

        self._resource_patch_memory = refreshed

    def _patch_memory(self, patch: dict[str, float | int]) -> dict[str, float | int]:
        return self._resource_patch_memory.get(self._patch_target(patch), {})

    def _nearest_anchor_distance(
        self,
        x: int,
        y: int,
        anchors: list[tuple[int, int]],
    ) -> int:
        if not anchors:
            return 0
        return min(self._cell_distance(x, y, ax, ay) for ax, ay in anchors)

    def _resource_patch_capacity(
        self,
        total_density: float,
        cells: int,
        refinery_count: int,
        depletion_ratio: float,
        threat: int,
    ) -> int:
        capacity = max(1, cells // RESOURCE_CELLS_PER_HARVESTER)
        if total_density >= cells * 2.0:
            capacity += 1
        if total_density >= cells * 3.5:
            capacity += 1
        if refinery_count > 0:
            capacity += 1

        capacity = min(RESOURCE_PATCH_MAX_CAPACITY, capacity)
        floor = 1 if refinery_count > 0 else 0
        if depletion_ratio >= 0.55:
            capacity = max(floor, capacity - 1)
        if threat > 0:
            capacity = max(0, capacity - min(threat, 2))
        return capacity

    def _spatial_value(self, x: int, y: int, channel: int, default: float = 0.0) -> float:
        w, h = self._get_map_size()
        if (
            not self._spatial_raw
            or self._spatial_channels <= channel
            or x < 0
            or y < 0
            or x >= w
            or y >= h
        ):
            return default
        base_idx = (y * w + x) * self._spatial_channels
        try:
            return struct.unpack_from("f", self._spatial_raw, (base_idx + channel) * 4)[0]
        except struct.error:
            return default

    def _resource_amount_at(self, x: int, y: int) -> float:
        return self._spatial_value(x, y, 2, 0.0)

    def _terrain_index_at(self, x: int, y: int) -> int:
        return int(self._spatial_value(x, y, 0, 0.0))

    def _is_passable_cell(self, x: int, y: int) -> bool:
        if not self._spatial_raw:
            return True
        return self._spatial_value(x, y, 3, 1.0) > 0.5

    def _is_water_candidate_cell(self, x: int, y: int) -> bool:
        if not self._spatial_raw:
            return False
        # Prefer terrain-index water (common case), but fall back to a human-visible cue:
        # large contiguous impassable regions (water) in the passability channel.
        if self._terrain_index_at(x, y) in {7, 8}:
            return True
        passability = self._spatial_value(x, y, 3, 1.0)
        if passability > 0.05:
            return False
        # Reject isolated impassables (cliffs/rocks) by requiring most neighbors
        # to also be strongly impassable.
        imp = 0
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                if self._spatial_value(x + dx, y + dy, 3, 1.0) <= 0.05:
                    imp += 1
        return imp >= 8

    def _is_open_water_cell(self, x: int, y: int) -> bool:
        w, h = self._get_map_size()
        if x <= 0 or y <= 0 or x >= w - 1 or y >= h - 1:
            return False
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                if not self._is_water_candidate_cell(x + dx, y + dy):
                    return False
        return True

    def _local_resource_score(self, x: int, y: int, radius: int) -> float:
        total = 0.0
        for dx in range(-radius, radius + 1):
            for dy in range(-radius, radius + 1):
                total += self._resource_amount_at(x + dx, y + dy)
        return total

    def _local_water_score(self, x: int, y: int, radius: int) -> int:
        total = 0
        for dx in range(-radius, radius + 1):
            for dy in range(-radius, radius + 1):
                if self._is_water_candidate_cell(x + dx, y + dy):
                    total += 1
        return total

    # ── Helpers ───────────────────────────────────────────────────

    def _find_building(self, obs: OpenRAObservation, btype: str) -> Optional[BuildingInfoModel]:
        return next((b for b in obs.buildings if b.type == btype), None)

    def _is_structure_queue(self, queue_type: str) -> bool:
        return queue_type in STRUCTURE_QUEUE_TYPES

    def _available_credits(self, obs: OpenRAObservation) -> int:
        # OpenRA splits spendable funds between liquid cash and stored ore/resources.
        return obs.economy.cash + obs.economy.ore

    def _combat_unit_count(self, obs: OpenRAObservation) -> int:
        return sum(1 for u in obs.units if u.type in COMBAT_TYPES)

    def _in_recovery_mode(self, obs: OpenRAObservation) -> bool:
        return obs.tick < self._recovery_until_tick

    def _base_center(self, obs: OpenRAObservation) -> Optional[Tuple[int, int]]:
        cy = self._find_building(obs, "fact")
        if cy is not None:
            return (
                cy.cell_x if cy.cell_x > 0 else cy.pos_x // 1024,
                cy.cell_y if cy.cell_y > 0 else cy.pos_y // 1024,
            )
        if obs.buildings:
            b = obs.buildings[0]
            return (
                b.cell_x if b.cell_x > 0 else b.pos_x // 1024,
                b.cell_y if b.cell_y > 0 else b.pos_y // 1024,
            )
        return None

    def _placement_base_center(self, obs: OpenRAObservation) -> Optional[Tuple[int, int]]:
        cy = self._find_building(obs, "fact")
        if cy is not None:
            return self._building_top_left(cy)
        if obs.buildings:
            return self._building_top_left(obs.buildings[0])
        return None

    def _protected_points(self, obs: OpenRAObservation) -> list[tuple[int, int, int]]:
        protected_points: list[tuple[int, int, int]] = []
        for b in obs.buildings:
            if b.type in PROTECTION_TYPES:
                bx = b.cell_x if b.cell_x > 0 else b.pos_x // 1024
                by = b.cell_y if b.cell_y > 0 else b.pos_y // 1024
                protected_points.append((bx, by, PROTECTION_SCAN_RADIUS))
        for u in obs.units:
            if u.type in {"harv", "mcv"}:
                protected_points.append((u.cell_x, u.cell_y, PROTECT_UNIT_SCAN_RADIUS))
        return protected_points

    def _base_threat_enemies(self, obs: OpenRAObservation) -> list[UnitInfoModel]:
        protected_points = self._protected_points(obs)
        if not protected_points:
            return []
        return [
            e for e in obs.visible_enemies
            if any(self._cell_distance(e.cell_x, e.cell_y, px, py) <= radius for px, py, radius in protected_points)
        ]

    def _base_under_pressure(self, obs: OpenRAObservation) -> bool:
        return bool(self._base_threat_enemies(obs))

    def _nearest_enemy_to_unit(
        self,
        obs: OpenRAObservation,
        unit: UnitInfoModel,
        radius: int,
    ) -> Optional[UnitInfoModel]:
        nearby = self._visible_enemy_units_near(obs, unit.cell_x, unit.cell_y, radius)
        if not nearby:
            return None
        return min(nearby, key=lambda e: self._cell_distance(unit.cell_x, unit.cell_y, e.cell_x, e.cell_y))

    def _pick_harvester_retreat_point(
        self,
        obs: OpenRAObservation,
        harvester: UnitInfoModel,
    ) -> Optional[Tuple[int, int]]:
        refineries = [b for b in obs.buildings if b.type == "proc"]
        threatened_patch = self._nearest_patch_state(
            self._resource_patch_states(obs),
            harvester.cell_x,
            harvester.cell_y,
            HARVESTER_PATCH_ASSIGN_RADIUS,
            allow_fallback=True,
        )
        avoid_target: Optional[tuple[int, int]] = None
        if threatened_patch is not None and int(threatened_patch["threat"]) > 0:
            avoid_target = threatened_patch["target"]  # type: ignore[index]

        if refineries:
            scored_refineries = sorted(
                refineries,
                key=lambda b: (
                    1
                    if avoid_target is not None and self._cell_distance(
                        b.cell_x if b.cell_x > 0 else b.pos_x // 1024,
                        b.cell_y if b.cell_y > 0 else b.pos_y // 1024,
                        avoid_target[0],
                        avoid_target[1],
                    ) <= HARVESTER_PATCH_ASSIGN_RADIUS
                    else 0,
                    self._cell_distance(
                        harvester.cell_x,
                        harvester.cell_y,
                        b.cell_x if b.cell_x > 0 else b.pos_x // 1024,
                        b.cell_y if b.cell_y > 0 else b.pos_y // 1024,
                    ),
                ),
            )
            best = scored_refineries[0]
            return (
                best.cell_x if best.cell_x > 0 else best.pos_x // 1024,
                best.cell_y if best.cell_y > 0 else best.pos_y // 1024,
            )
        return self._base_center(obs)

    def _update_harvester_progress(self, obs: OpenRAObservation, harvester: UnitInfoModel):
        actor_id = harvester.actor_id
        current_cell = (harvester.cell_x, harvester.cell_y)
        previous_cell = self._harvester_last_cells.get(actor_id)
        patch_target = self._harvester_patch_targets.get(actor_id)

        if previous_cell is None:
            self._harvester_last_progress_tick[actor_id] = obs.tick
        else:
            moved = self._cell_distance(current_cell[0], current_cell[1], previous_cell[0], previous_cell[1])
            if moved >= HARVESTER_PROGRESS_MOVE_THRESHOLD:
                self._harvester_last_progress_tick[actor_id] = obs.tick

        if patch_target is not None and self._cell_distance(current_cell[0], current_cell[1], patch_target[0], patch_target[1]) <= 2:
            self._harvester_last_progress_tick[actor_id] = obs.tick

        self._harvester_last_cells[actor_id] = current_cell

    def _is_low_effect_harvester(self, obs: OpenRAObservation, harvester: UnitInfoModel) -> bool:
        if harvester.is_idle:
            return False
        if obs.tick < self._harvester_retreat_until.get(harvester.actor_id, -9999):
            return False
        if obs.tick < self._harvester_no_resource_until.get(harvester.actor_id, -9999):
            return False

        last_progress = self._harvester_last_progress_tick.get(harvester.actor_id, obs.tick)
        if obs.tick - last_progress < HARVESTER_LOW_EFFECT_TIMEOUT:
            return False

        patch_target = self._harvester_patch_targets.get(harvester.actor_id)
        if patch_target is not None and self._local_resource_score(patch_target[0], patch_target[1], 2) <= HARVESTER_LOCAL_RESOURCE_MIN:
            return True

        activity = harvester.current_activity.lower()
        if "harvest" in activity or "move" in activity or "dock" in activity:
            return True
        return False

    def _fallback_harvest_target(
        self,
        obs: OpenRAObservation,
        harvester: UnitInfoModel,
    ) -> Optional[tuple[int, int]]:
        patch_states = self._resource_patch_states(obs)
        candidates = [
            state
            for state in patch_states
            if int(state["threat"]) == 0
            and int(state["capacity"]) > 0
            and float(state["depletion_ratio"]) < 0.9
        ]
        if not candidates:
            return None

        best = max(
            candidates,
            key=lambda state: (
                int(state["score"]),
                -self._cell_distance(
                    harvester.cell_x,
                    harvester.cell_y,
                    state["target"][0],  # type: ignore[index]
                    state["target"][1],  # type: ignore[index]
                ),
            ),
        )
        return best["target"]  # type: ignore[return-value]

    def _pending_build_cost(self, obs: OpenRAObservation) -> int:
        if self._build_index >= len(BUILD_ORDER):
            return 0
        item = self._resolve_build_item(obs, BUILD_ORDER[self._build_index])
        if item is None or self._already_have(obs, item, self._build_index):
            return 0
        return self._build_cost(item)

    def _building_counts(self, obs: OpenRAObservation) -> dict[str, int]:
        counts: dict[str, int] = {}
        for b in obs.buildings:
            btype = self._canonical_building_type(b.type)
            counts[btype] = counts.get(btype, 0) + 1
        return counts

    def _canonical_building_type(self, item_type: str) -> str:
        return BUILDING_CANONICAL_TYPES.get(item_type, item_type)

    def _build_cost(self, item_type: str) -> int:
        canonical = self._canonical_building_type(item_type)
        return BUILDING_COSTS.get(item_type, BUILDING_COSTS.get(canonical, 500))

    def _building_dimensions(self, item_type: str) -> tuple[int, int]:
        canonical = self._canonical_building_type(item_type)
        return BUILDING_DIMENSIONS.get(item_type, BUILDING_DIMENSIONS.get(canonical, (2, 2)))

    def _building_top_left(self, building: BuildingInfoModel) -> tuple[int, int]:
        canonical = self._canonical_building_type(building.type)
        offset_x, offset_y = BUILDING_TOPLEFT_OFFSETS.get(
            building.type,
            BUILDING_TOPLEFT_OFFSETS.get(canonical, (0, 0)),
        )
        return (
            (building.cell_x if building.cell_x > 0 else building.pos_x // 1024) - offset_x,
            (building.cell_y if building.cell_y > 0 else building.pos_y // 1024) - offset_y,
        )

    def _occupied_building_cells(self, obs: OpenRAObservation) -> set[tuple[int, int]]:
        occupied: set[tuple[int, int]] = set()
        for building in obs.buildings:
            bx, by = self._building_top_left(building)
            width, height = self._building_dimensions(building.type)
            for dx in range(width):
                for dy in range(height):
                    occupied.add((bx + dx, by + dy))
        return occupied

    def _buildable_area_cells(self, obs: OpenRAObservation) -> set[tuple[int, int]]:
        cells: set[tuple[int, int]] = set()
        for building in obs.buildings:
            if self._canonical_building_type(building.type) in NO_BUILDABLE_AREA_TYPES:
                continue
            bx, by = self._building_top_left(building)
            width, height = self._building_dimensions(building.type)
            for dx in range(width):
                for dy in range(height):
                    cells.add((bx + dx, by + dy))
        return cells

    def _buildable_area_structure_count(self, obs: OpenRAObservation) -> int:
        return sum(
            1
            for building in obs.buildings
            if self._canonical_building_type(building.type) not in NO_BUILDABLE_AREA_TYPES
        )

    def _footprint_close_enough_to_base(
        self,
        top_left_x: int,
        top_left_y: int,
        width: int,
        height: int,
        base_cells: set[tuple[int, int]],
        radius: int,
    ) -> bool:
        if not base_cells:
            return False
        max_x = top_left_x + width - 1
        max_y = top_left_y + height - 1
        for bx, by in base_cells:
            dx = 0 if top_left_x <= bx <= max_x else min(abs(bx - top_left_x), abs(bx - max_x))
            dy = 0 if top_left_y <= by <= max_y else min(abs(by - top_left_y), abs(by - max_y))
            if max(dx, dy) <= radius:
                return True
        return False

    def _candidate_fits_building_footprint(
        self,
        obs: OpenRAObservation,
        item_type: str,
        top_left_x: int,
        top_left_y: int,
        occupied: Optional[set[tuple[int, int]]] = None,
        base_cells: Optional[set[tuple[int, int]]] = None,
    ) -> bool:
        width, height = self._building_dimensions(item_type)
        w, h = self._get_map_size()
        if top_left_x < 0 or top_left_y < 0 or top_left_x + width > w or top_left_y + height > h:
            return False

        occupied = occupied or self._occupied_building_cells(obs)
        is_naval = self._canonical_building_type(item_type) in NAVAL_STRUCTURE_TYPES
        if base_cells is None:
            base_cells = self._buildable_area_cells(obs)
        if is_naval and not self._footprint_close_enough_to_base(
            top_left_x, top_left_y, width, height, base_cells, CHECK_FOR_WATER_RADIUS
        ):
            return False
        for dx in range(width):
            for dy in range(height):
                cell = (top_left_x + dx, top_left_y + dy)
                if cell in occupied:
                    return False
                if is_naval:
                    if not self._is_water_candidate_cell(*cell):
                        return False
                else:
                    if not self._is_passable_cell(*cell):
                        return False
                    if self._resource_amount_at(*cell) > 0.0:
                        return False
        return True

    def _schedule_next_build_check(self, obs: OpenRAObservation, active: bool):
        delay = STRUCTURE_PRODUCTION_ACTIVE_DELAY if active else STRUCTURE_PRODUCTION_INACTIVE_DELAY
        random_bonus = random.randrange(STRUCTURE_PRODUCTION_RANDOM_BONUS_DELAY) if STRUCTURE_PRODUCTION_RANDOM_BONUS_DELAY > 0 else 0
        self._next_build_check_tick = obs.tick + delay + random_bonus

    def _structure_queue_type(self, item_type: str) -> str:
        canonical = self._canonical_building_type(item_type)
        return "Defense" if canonical in DEFENSE_STRUCTURE_TYPES else "Building"

    def _clear_queue_backoff(self, queue_type: str):
        self._placement_backoff_until.pop(queue_type, None)
        self._placement_backoff_snapshot.pop(queue_type, None)

    def _queue_backoff_active(self, queue_type: str, obs: OpenRAObservation) -> bool:
        until = self._placement_backoff_until.get(queue_type, -9999)
        if until <= obs.tick:
            self._clear_queue_backoff(queue_type)
            return False

        snapshot = self._placement_backoff_snapshot.get(queue_type)
        if snapshot is not None:
            prev_buildings, prev_conyards = snapshot
            current_conyards = sum(1 for b in obs.buildings if b.type == "fact")
            if len(obs.buildings) < prev_buildings or current_conyards > prev_conyards:
                self._clear_queue_backoff(queue_type)
                return False
        return True

    def _structure_queue_available(self, obs: OpenRAObservation, item_type: str) -> bool:
        canonical = self._canonical_building_type(item_type)
        if canonical in NAVAL_STRUCTURE_TYPES and not self._can_safely_build_naval_structure(obs):
            return False
        return not self._queue_backoff_active(self._structure_queue_type(canonical), obs)

    def _priority_structure_reservation(self, obs: OpenRAObservation) -> int:
        if self._build_index < len(BUILD_ORDER):
            return self._pending_build_cost(obs)

        power_balance = obs.economy.power_provided - obs.economy.power_drained
        minimum_excess_power = self._minimum_excess_power_target(obs)
        power_item = self._best_power_building(obs)
        if power_balance < minimum_excess_power and power_item and self._structure_queue_available(obs, power_item):
            return self._build_cost(power_item)

        if not self._has_adequate_refinery_count(obs) and self._can_produce(obs, "proc") and self._structure_queue_available(obs, "proc"):
            return self._build_cost("proc")

        if self._in_recovery_mode(obs):
            bldg_counts = self._building_counts(obs)
            if (
                bldg_counts.get("proc", 0) == 0
                and not self._base_under_pressure(obs)
                and self._can_produce(obs, "proc")
                and self._structure_queue_available(obs, "proc")
            ):
                return self._build_cost("proc")

        return 0

    def _rewind_build_order_after_cancel(self, obs: OpenRAObservation, item_type: str):
        canonical = self._canonical_building_type(item_type)
        for idx, placeholder in enumerate(BUILD_ORDER):
            resolved = self._resolve_build_item(obs, placeholder)
            if resolved is None:
                continue
            if self._canonical_building_type(resolved) != canonical:
                continue
            existing = self._building_counts(obs).get(canonical, 0)
            required = sum(
                1
                for p in BUILD_ORDER[: idx + 1]
                if (rp := self._resolve_build_item(obs, p)) is not None and self._canonical_building_type(rp) == canonical
            )
            if existing < required:
                self._build_index = min(self._build_index, idx)
                return

    def _placement_anchor(
        self,
        obs: OpenRAObservation,
        item_type: str,
        fallback: Tuple[int, int],
    ) -> Tuple[int, int]:
        if item_type == "proc":
            plan = self._best_refinery_plan(obs)
            if plan is not None:
                return plan["anchor"]
        return fallback

    def _placement_candidates(
        self,
        obs: OpenRAObservation,
        item_type: str,
        cx: int,
        cy: int,
        min_radius: int,
        max_radius: int,
    ) -> list[tuple[int, int]]:
        if item_type in NAVAL_STRUCTURE_TYPES:
            naval_candidates = self._naval_build_candidates(obs, item_type)
            if naval_candidates:
                return naval_candidates

        occupied = self._occupied_building_cells(obs)
        buildable_area = self._buildable_area_cells(obs)

        candidates: list[tuple[int, int]] = []
        w, h = self._get_map_size()
        for radius in range(min_radius, max_radius + 1):
            for dx in range(-radius, radius + 1):
                for dy in range(-radius, radius + 1):
                    if max(abs(dx), abs(dy)) != radius:
                        continue
                    x = cx + dx
                    y = cy + dy
                    if x < 0 or y < 0 or x >= w or y >= h:
                        continue
                    candidates.append((x, y))

        fitting = [
            c for c in candidates
            if self._candidate_fits_building_footprint(obs, item_type, c[0], c[1], occupied, buildable_area)
        ]
        if fitting:
            candidates = fitting

        return candidates

    def _resource_patch_threat(self, obs: OpenRAObservation, patch: dict[str, float | int]) -> int:
        px = int(patch.get("resource_center_x", patch["center_x"]))
        py = int(patch.get("resource_center_y", patch["center_y"]))
        enemies = 0
        for enemy in obs.visible_enemies:
            if self._cell_distance(enemy.cell_x, enemy.cell_y, px, py) <= RESOURCE_PATCH_THREAT_RADIUS:
                enemies += 1
        for building in obs.visible_enemy_buildings:
            if self._cell_distance(building.cell_x, building.cell_y, px, py) <= RESOURCE_PATCH_THREAT_RADIUS:
                enemies += 2
        return enemies

    def _patch_target(self, patch: dict[str, float | int]) -> tuple[int, int]:
        return (
            int(patch.get("resource_center_x", patch["center_x"])),
            int(patch.get("resource_center_y", patch["center_y"])),
        )

    def _nearest_patch_state(
        self,
        patch_states: list[dict[str, object]],
        x: int,
        y: int,
        radius: int,
        allow_fallback: bool = False,
    ) -> Optional[dict[str, object]]:
        best: Optional[dict[str, object]] = None
        best_dist = radius + 1
        for state in patch_states:
            tx, ty = state["target"]  # type: ignore[index]
            dist = self._cell_distance(x, y, tx, ty)
            if dist <= radius and dist < best_dist:
                best = state
                best_dist = dist
        if best is None and allow_fallback and patch_states:
            best = min(
                patch_states,
                key=lambda state: self._cell_distance(x, y, state["target"][0], state["target"][1]),  # type: ignore[index]
            )
        return best

    def _resource_patch_states(self, obs: OpenRAObservation) -> list[dict[str, object]]:
        patch_states: list[dict[str, object]] = []
        refineries = [b for b in obs.buildings if b.type == "proc"]
        conyards = [b for b in obs.buildings if b.type == "fact"]
        base_center = self._base_center(obs)
        anchors = [
            (
                conyard.cell_x if conyard.cell_x > 0 else conyard.pos_x // 1024,
                conyard.cell_y if conyard.cell_y > 0 else conyard.pos_y // 1024,
            )
            for conyard in conyards
        ]
        if not anchors and base_center is not None:
            anchors = [base_center]

        for idx, patch in enumerate(self._resource_patches):
            target = self._patch_target(patch)
            memory = self._patch_memory(patch)
            nearest_refinery_distance = self._nearest_distance_to_buildings(target[0], target[1], refineries)
            nearby_refineries = sum(
                1
                for refinery in refineries
                if self._cell_distance(
                    target[0],
                    target[1],
                    refinery.cell_x if refinery.cell_x > 0 else refinery.pos_x // 1024,
                    refinery.cell_y if refinery.cell_y > 0 else refinery.pos_y // 1024,
                )
                <= RESOURCE_PATCH_REFINERY_DISLIKE_RADIUS
            )
            anchor_distance = self._nearest_anchor_distance(target[0], target[1], anchors)
            base_distance = (
                self._cell_distance(target[0], target[1], base_center[0], base_center[1])
                if base_center is not None
                else anchor_distance
            )
            patch_states.append(
                {
                    "id": idx,
                    "patch": patch,
                    "target": target,
                    "harvesters": [],
                    "harvester_count": 0,
                    "refinery_count": 0,
                    "nearby_refineries": nearby_refineries,
                    "nearest_refinery_distance": nearest_refinery_distance,
                    "anchor_distance": anchor_distance,
                    "base_distance": base_distance,
                    "threat": self._resource_patch_threat(obs, patch),
                    "depletion_ratio": float(memory.get("depletion_ratio", 0.0)),
                    "depletion_trend": float(memory.get("depletion_trend", 0.0)),
                    "travel_cost": 0,
                    "capacity": 0,
                    "saturation": 0.0,
                    "lack": 0,
                    "density_score": 0,
                    "score": 0,
                    "refinery_score": 0,
                    "expansion_score": 0,
                }
            )

        if not patch_states:
            return patch_states

        for refinery in refineries:
            rx = refinery.cell_x if refinery.cell_x > 0 else refinery.pos_x // 1024
            ry = refinery.cell_y if refinery.cell_y > 0 else refinery.pos_y // 1024
            state = self._nearest_patch_state(
                patch_states,
                rx,
                ry,
                HARVESTER_REASSIGN_REFINERY_RADIUS,
                allow_fallback=True,
            )
            if state is not None:
                state["refinery_count"] = int(state["refinery_count"]) + 1
                state["nearest_refinery_distance"] = min(
                    int(state["nearest_refinery_distance"]),
                    self._cell_distance(rx, ry, state["target"][0], state["target"][1]),  # type: ignore[index]
                )

        for harvester in [u for u in obs.units if u.type == "harv"]:
            state = self._nearest_patch_state(
                patch_states,
                harvester.cell_x,
                harvester.cell_y,
                HARVESTER_PATCH_ASSIGN_RADIUS,
                allow_fallback=True,
            )
            if state is None:
                forced_target = self._harvester_patch_targets.get(harvester.actor_id)
                if forced_target is not None:
                    state = self._nearest_patch_state(
                        patch_states,
                        forced_target[0],
                        forced_target[1],
                        HARVESTER_PATCH_ASSIGN_RADIUS * 2,
                        allow_fallback=True,
                    )
            if state is not None:
                state["harvesters"].append(harvester)  # type: ignore[index]

        for state in patch_states:
            patch = state["patch"]  # type: ignore[assignment]
            harvester_count = len(state["harvesters"])  # type: ignore[arg-type]
            cells = int(patch["cells"])  # type: ignore[index]
            total_density = float(patch["total_density"])  # type: ignore[index]
            threat = int(state["threat"])
            refinery_count = int(state["refinery_count"])
            nearby_refineries = int(state["nearby_refineries"])
            nearest_refinery_distance = min(
                int(state["nearest_refinery_distance"]),
                RESOURCE_PATCH_REFINERY_DISLIKE_RADIUS * 3,
            )
            anchor_distance = int(state["anchor_distance"])
            base_distance = int(state["base_distance"])
            depletion_ratio = float(state["depletion_ratio"])
            depletion_trend = float(state["depletion_trend"])

            capacity = self._resource_patch_capacity(
                total_density,
                cells,
                refinery_count,
                depletion_ratio,
                threat,
            )
            if capacity > harvester_count:
                lack = capacity - harvester_count
            elif capacity < harvester_count:
                lack = -(harvester_count - capacity)
            else:
                lack = 0

            if refinery_count <= 0 and lack > 0:
                lack = min(lack, 1)
            if threat > 0 and lack > 0:
                lack = 0

            saturation = harvester_count / max(1, capacity) if capacity > 0 else float(harvester_count)
            travel_cost = nearest_refinery_distance if refineries else anchor_distance + 8
            if refinery_count <= 0 and refineries:
                travel_cost = min(travel_cost + 4, anchor_distance + 10)

            density_score = int(total_density * 8) + cells * 24
            depletion_penalty = int(depletion_ratio * 400) + int(depletion_trend * 320)
            support_bonus = refinery_count * 350
            if refineries:
                support_bonus += max(
                    0,
                    RESOURCE_PATCH_REFINERY_DISLIKE_RADIUS
                    - min(nearest_refinery_distance, RESOURCE_PATCH_REFINERY_DISLIKE_RADIUS),
                ) * 14

            score = density_score + support_bonus
            score -= threat * 240
            score -= travel_cost * 12
            score -= depletion_penalty
            if harvester_count == 0 and refinery_count > 0 and threat == 0:
                score += 90
            if saturation > 1.0:
                score -= int((saturation - 1.0) * 300)

            refinery_score = density_score
            refinery_score -= anchor_distance * 16
            refinery_score -= nearby_refineries * 600
            refinery_score -= threat * 220
            refinery_score -= depletion_penalty
            if nearby_refineries == 0:
                refinery_score += 250
            if refineries:
                refinery_score += min(nearest_refinery_distance, RESOURCE_PATCH_REFINERY_DISLIKE_RADIUS) * 20
            if threat == 0 and saturation >= 0.75:
                refinery_score += 80

            expansion_score = density_score
            expansion_score -= base_distance * 6
            expansion_score -= threat * 240
            expansion_score -= depletion_penalty
            if nearby_refineries == 0:
                expansion_score += 120
            if anchor_distance > MCV_FRIENDLY_CONYARD_DISLIKE_RANGE:
                expansion_score += 100
            if nearest_refinery_distance > MCV_FRIENDLY_REFINERY_DISLIKE_RANGE:
                expansion_score += 80
            if saturation >= 1.0 and threat == 0:
                expansion_score += 60

            state["harvester_count"] = harvester_count
            state["capacity"] = capacity
            state["saturation"] = saturation
            state["travel_cost"] = travel_cost
            state["lack"] = lack
            state["density_score"] = density_score
            state["score"] = score
            state["refinery_score"] = refinery_score
            state["expansion_score"] = expansion_score

        return patch_states

    def _can_reassign_harvester(self, obs: OpenRAObservation, harvester: UnitInfoModel) -> bool:
        if obs.tick < self._harvester_retreat_until.get(harvester.actor_id, -9999):
            return False
        if obs.tick < self._harvester_reassign_until.get(harvester.actor_id, -9999):
            return False
        if obs.tick < self._harvester_no_resource_until.get(harvester.actor_id, -9999):
            return False
        if self._nearest_enemy_to_unit(obs, harvester, HARVESTER_THREAT_RADIUS) is not None:
            return False

        activity = harvester.current_activity.lower()
        if "dock" in activity:
            return False
        return True

    def _reassign_low_effect_harvesters(
        self,
        obs: OpenRAObservation,
    ) -> tuple[list[CommandModel], set[int]]:
        if obs.tick - self._last_harvester_reassign_tick < LOW_EFFECT_HARVESTER_SCAN_INTERVAL:
            return [], set()
        self._last_harvester_reassign_tick = obs.tick

        if self._base_under_pressure(obs) or len(self._resource_patches) < 2:
            return [], set()

        patch_states = self._resource_patch_states(obs)
        donors = [state for state in patch_states if int(state["lack"]) < 0]
        receivers = [
            state
            for state in patch_states
            if int(state["lack"]) > 0 and int(state["threat"]) == 0
        ]

        if not donors or not receivers:
            fallback_donors = [
                state
                for state in patch_states
                if int(state["harvester_count"]) > 1 and int(state["threat"]) == 0 and float(state["saturation"]) >= 1.0
            ]
            if not receivers or not fallback_donors:
                return [], set()

            best_receiver = max(receivers, key=lambda state: int(state["score"]))
            donors = [
                state
                for state in fallback_donors
                if int(best_receiver["score"]) > int(state["score"]) + 600
            ]
            if not donors:
                return [], set()
            for donor in donors:
                donor["lack"] = min(int(donor["lack"]), -1)

        donors.sort(key=lambda state: int(state["lack"]))
        receivers.sort(
            key=lambda state: (
                int(state["score"]),
                int(state["lack"]),
                -int(state["travel_cost"]),
            ),
            reverse=True,
        )

        commands: list[CommandModel] = []
        redirected: set[int] = set()

        for receiver in receivers:
            need = int(receiver["lack"])
            if int(receiver["refinery_count"]) <= 0:
                need = min(need, 1)
            if need <= 0:
                continue

            tx, ty = receiver["target"]  # type: ignore[index]
            for donor in donors:
                if need <= 0 or int(donor["lack"]) >= 0:
                    continue

                harvesters = sorted(
                    donor["harvesters"],  # type: ignore[index]
                    key=lambda u: self._cell_distance(u.cell_x, u.cell_y, tx, ty),
                )
                for harvester in harvesters:
                    if need <= 0 or int(donor["lack"]) >= 0:
                        break
                    if harvester.actor_id in redirected:
                        continue
                    if not self._can_reassign_harvester(obs, harvester):
                        continue
                    if self._harvester_patch_targets.get(harvester.actor_id) == (tx, ty):
                        continue

                    commands.append(
                        CommandModel(
                            action=ActionType.HARVEST,
                            actor_id=harvester.actor_id,
                            target_x=tx,
                            target_y=ty,
                        )
                    )
                    redirected.add(harvester.actor_id)
                    donor["lack"] = int(donor["lack"]) + 1
                    need -= 1
                    self._harvester_patch_targets[harvester.actor_id] = (tx, ty)
                    self._harvester_reassign_until[harvester.actor_id] = obs.tick + HARVESTER_REASSIGN_COOLDOWN
                    self._harvester_last_progress_tick[harvester.actor_id] = obs.tick
                    self._log(
                        f"Redirecting harv #{harvester.actor_id} -> patch ({tx},{ty}) "
                        f"from overloaded patch {donor['target']}"
                    )

        return commands, redirected

    def _best_refinery_plan(
        self,
        obs: OpenRAObservation,
    ) -> Optional[dict[str, Tuple[int, int]]]:
        patch_states = self._resource_patch_states(obs)
        if not patch_states:
            return None

        conyards = [b for b in obs.buildings if b.type == "fact"]
        if not conyards:
            return None

        best: Optional[tuple[int, Tuple[int, int], Tuple[int, int]]] = None
        for conyard in conyards:
            anchor = (
                conyard.cell_x if conyard.cell_x > 0 else conyard.pos_x // 1024,
                conyard.cell_y if conyard.cell_y > 0 else conyard.pos_y // 1024,
            )
            for state in patch_states:
                target = state["target"]  # type: ignore[index]
                dist = self._cell_distance(anchor[0], anchor[1], target[0], target[1])
                if dist > BASE_BUILD_MAX_RADIUS + RESOURCE_PATCH_SEARCH_MARGIN:
                    continue

                if int(state["nearby_refineries"]) >= MAX_REFINERIES_PER_PATCH:
                    continue

                score = int(state["refinery_score"]) - dist * 8
                if int(state["threat"]) == 0 and float(state["depletion_ratio"]) < 0.6:
                    score += 60

                if best is None or score > best[0]:
                    best = (score, anchor, target)

        if best is None:
            conyard = conyards[0]
            anchor = (
                conyard.cell_x if conyard.cell_x > 0 else conyard.pos_x // 1024,
                conyard.cell_y if conyard.cell_y > 0 else conyard.pos_y // 1024,
            )
            state = min(
                patch_states,
                key=lambda s: self._cell_distance(anchor[0], anchor[1], s["target"][0], s["target"][1]),  # type: ignore[index]
            )
            return {"anchor": anchor, "target": state["target"]}  # type: ignore[return-value]

        return {"anchor": best[1], "target": best[2]}

    def _best_expansion_patch_target(self, obs: OpenRAObservation) -> Optional[Tuple[int, int]]:
        patch_states = self._resource_patch_states(obs)
        if not patch_states:
            return None

        conyards = [b for b in obs.buildings if b.type == "fact"]
        refineries = [b for b in obs.buildings if b.type == "proc"]
        best: Optional[tuple[int, Tuple[int, int]]] = None
        for state in patch_states:
            target = state["target"]  # type: ignore[index]
            if conyards and self._nearest_distance_to_buildings(target[0], target[1], conyards) < MCV_FRIENDLY_CONYARD_DISLIKE_RANGE:
                continue
            if refineries and self._nearest_distance_to_buildings(target[0], target[1], refineries) < MCV_FRIENDLY_REFINERY_DISLIKE_RANGE:
                continue

            if int(state["threat"]) > 0:
                continue

            score = int(state["expansion_score"])
            if float(state["depletion_ratio"]) >= 0.8:
                score -= 200
            if int(state["harvester_count"]) == 0 and int(state["capacity"]) >= 2:
                score += 80

            if best is None or score > best[0]:
                best = (score, target)

        return None if best is None or best[0] <= 0 else best[1]

    def _naval_build_candidates(
        self,
        obs: OpenRAObservation,
        item_type: str,
        occupied: Optional[set[tuple[int, int]]] = None,
    ) -> list[tuple[int, int]]:
        center = self._placement_base_center(obs) or (0, 0)
        w, h = self._get_map_size()
        if not self._spatial_raw:
            return []

        occupied = occupied or self._occupied_building_cells(obs)
        buildable_area = self._buildable_area_cells(obs)
        width, height = self._building_dimensions(item_type)
        candidates: set[tuple[int, int]] = set()
        origins = [
            self._building_top_left(building)
            for building in obs.buildings
            if self._canonical_building_type(building.type) not in NO_BUILDABLE_AREA_TYPES
        ]
        for ox, oy in origins:
            for dx in range(-NAVAL_WATER_SCAN_RADIUS, NAVAL_WATER_SCAN_RADIUS + 1, NAVAL_WATER_SCAN_STRIDE):
                for dy in range(-NAVAL_WATER_SCAN_RADIUS, NAVAL_WATER_SCAN_RADIUS + 1, NAVAL_WATER_SCAN_STRIDE):
                    if dx * dx + dy * dy > NAVAL_WATER_SCAN_RADIUS * NAVAL_WATER_SCAN_RADIUS:
                        continue
                    x = ox + dx
                    y = oy + dy
                    if x < 0 or y < 0 or x + width > w or y + height > h:
                        continue
                    candidate_center = (x + width // 2, y + height // 2)
                    center_radius = max(abs(candidate_center[0] - center[0]), abs(candidate_center[1] - center[1]))
                    if center_radius < BASE_BUILD_MIN_RADIUS or center_radius > NAVAL_BUILD_MAX_RADIUS:
                        continue
                    if not self._candidate_fits_building_footprint(
                        obs,
                        item_type,
                        x,
                        y,
                        occupied=occupied,
                        base_cells=buildable_area,
                    ):
                        continue
                    candidates.add((x, y))
        ordered = list(candidates)
        ordered.sort(
            key=lambda p: (
                -self._naval_anchor_score(item_type, p[0], p[1]),
                self._cell_distance(p[0], p[1], center[0], center[1]),
            )
        )
        return ordered

    def _naval_anchor_score(self, item_type: str, top_left_x: int, top_left_y: int) -> int:
        width, height = self._building_dimensions(item_type)
        center_x = top_left_x + width // 2
        center_y = top_left_y + height // 2
        return self._local_water_score(center_x, center_y, 2)

    def _naval_gate_open_water_windows(self, obs: OpenRAObservation) -> int:
        if not self._spatial_raw:
            return 0
        # Count only footprint-valid naval anchors with enough surrounding water.
        candidates = self._naval_build_candidates(obs, "spen")
        if not candidates:
            return 0
        viable = [
            candidate
            for candidate in candidates
            if self._naval_anchor_score("spen", candidate[0], candidate[1]) >= NAVAL_MIN_WATER_SCORE
        ]
        return min(len(viable), NAVAL_MIN_OPEN_WATER_WINDOWS)

    def _best_naval_anchor(self, obs: OpenRAObservation) -> Optional[Tuple[int, int]]:
        candidates = [
            candidate
            for candidate in self._naval_build_candidates(obs, "spen")
            if self._naval_anchor_score("spen", candidate[0], candidate[1]) >= NAVAL_MIN_WATER_SCORE
        ]
        if len(candidates) < NAVAL_CANDIDATE_MIN_COUNT:
            return None
        return candidates[0]

    def _can_safely_build_naval_structure(self, obs: OpenRAObservation) -> bool:
        if obs.tick - self._last_naval_gate_tick <= NAVAL_GATE_CACHE_TICKS:
            return self._cached_naval_gate_ok

        if any(self._canonical_building_type(b.type) in NAVAL_STRUCTURE_TYPES for b in obs.buildings):
            self._naval_retry_buildable_count = -1
            self._cached_naval_gate_ok = True
            self._last_naval_gate_tick = obs.tick
            return True
        elif self._naval_retry_buildable_count >= 0:
            if self._buildable_area_structure_count(obs) <= self._naval_retry_buildable_count:
                self._cached_naval_gate_ok = False
                self._last_naval_gate_tick = obs.tick
                return False
            self._naval_retry_buildable_count = -1

        # Human-like: only enable naval when there is a strong, footprint-valid
        # naval anchor near our current buildable area.
        if self._naval_gate_open_water_windows(obs) < NAVAL_MIN_OPEN_WATER_WINDOWS:
            self._cached_naval_gate_ok = False
            self._last_naval_gate_tick = obs.tick
            return False

        ok = self._best_naval_anchor(obs) is not None
        self._cached_naval_gate_ok = ok
        self._last_naval_gate_tick = obs.tick
        return ok

    def _minimum_excess_power_target(self, obs: OpenRAObservation) -> int:
        bonus = EXCESS_POWER_INCREMENT * (len(obs.buildings) // max(1, EXCESS_POWER_INCREASE_THRESHOLD))
        return max(MINIMUM_EXCESS_POWER, min(MAXIMUM_EXCESS_POWER, MINIMUM_EXCESS_POWER + bonus))

    def _has_any_production_building(self, obs: OpenRAObservation) -> bool:
        return any(b.type in BARRACKS_TYPES | WAR_FACTORY_TYPES for b in obs.buildings)

    def _optimal_refinery_count(self, obs: OpenRAObservation) -> int:
        target = INITIAL_MIN_REFINERY_COUNT
        if self._has_any_production_building(obs):
            target += ADDITIONAL_MIN_REFINERY_COUNT

        patch_states = self._resource_patch_states(obs)
        strong_patches = sum(
            1
            for state in patch_states
            if int(state["threat"]) == 0
            and float(state["depletion_ratio"]) < 0.75
            and int(state["refinery_score"]) > 0
        )
        return max(target, min(3, strong_patches))

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
            if self._can_produce(obs, item) and self._structure_queue_available(obs, item):
                return item
        return None

    def _best_production_building(self, obs: OpenRAObservation) -> Optional[str]:
        candidates = []
        counts = self._building_counts(obs)
        for item in ("weap", "barr", "tent"):
            if not self._can_produce(obs, item):
                continue
            if not self._structure_queue_available(obs, item):
                continue
            limit = BUILDING_LIMITS.get(item)
            if limit is not None and counts.get(item, 0) >= limit:
                continue
            candidates.append(item)
        if not candidates:
            return None
        return random.choice(candidates)

    def _preferred_early_naval_building(self, obs: OpenRAObservation, credits: int) -> Optional[str]:
        if self._in_recovery_mode(obs):
            return None
        if any(self._canonical_building_type(b.type) in NAVAL_STRUCTURE_TYPES for b in obs.buildings):
            return None
        if not any(b.type == "proc" for b in obs.buildings):
            return None
        if not any(b.type in WAR_FACTORY_TYPES for b in obs.buildings):
            return None
        if not self._can_safely_build_naval_structure(obs):
            return None

        anchor = self._best_naval_anchor(obs)
        if anchor is None or self._naval_anchor_score("spen", anchor[0], anchor[1]) < NAVAL_EARLY_BUILD_WATER_SCORE:
            return None
        naval_item = self._best_naval_production_building(obs)
        if naval_item is None:
            return None
        if credits < self._build_cost(naval_item) + NAVAL_EARLY_BUILD_CREDIT_BUFFER:
            return None
        return naval_item

    def _best_naval_production_building(self, obs: OpenRAObservation) -> Optional[str]:
        candidates = []
        counts = self._building_counts(obs)
        for item in NAVAL_STRUCTURE_TYPES:
            if not self._can_produce(obs, item):
                continue
            if not self._structure_queue_available(obs, item):
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
        current += sum(1 for p in obs.production if p.item == item_type)
        current += self._requested_production_count(item_type)
        return current >= limit

    def _current_unit_count(self, obs: OpenRAObservation, item_type: str) -> int:
        return sum(1 for u in obs.units if u.type == item_type)

    def _queue_delay_active(self, obs: OpenRAObservation, queue_type: str) -> bool:
        return obs.tick < self._queue_delay_until.get(queue_type, -9999)

    def _unit_delay_active(self, obs: OpenRAObservation, item_type: str) -> bool:
        return obs.tick < self._unit_delay_until.get(item_type, -9999)

    def _mark_unit_trained(self, obs: OpenRAObservation, item_type: str, queue_type: str):
        queue_delay = QUEUE_PRODUCTION_DELAYS.get(queue_type, 0)
        if queue_delay > 0:
            self._queue_delay_until[queue_type] = max(
                self._queue_delay_until.get(queue_type, -9999),
                obs.tick + queue_delay,
            )

        unit_delay = UNIT_PRODUCTION_DELAYS.get(item_type, 0)
        if unit_delay > 0:
            self._unit_delay_until[item_type] = max(
                self._unit_delay_until.get(item_type, -9999),
                obs.tick + unit_delay,
            )

    def _idle_base_unit_count(self, obs: OpenRAObservation, queue_type: Optional[str] = None) -> int:
        base_center = self._base_center(obs)
        if base_center is None:
            return 0

        count = 0
        for unit in obs.units:
            if unit.type in {"harv", "mcv"}:
                continue
            if not getattr(unit, "is_idle", False):
                continue
            if self._cell_distance(unit.cell_x, unit.cell_y, base_center[0], base_center[1]) > IDLE_BASE_UNIT_RADIUS:
                continue
            if queue_type is not None and self._queue_type_for_unit(unit.type) != queue_type:
                continue
            count += 1
        return count

    def _production_support_available(
        self,
        obs: OpenRAObservation,
        item_type: str,
        unit_counts: Optional[dict[str, int]] = None,
    ) -> bool:
        if item_type in SHIP_TYPES:
            return any(self._canonical_building_type(b.type) in NAVAL_STRUCTURE_TYPES for b in obs.buildings)

        if unit_counts is None:
            unit_counts = {}
            for unit in obs.units:
                unit_counts[unit.type] = unit_counts.get(unit.type, 0) + 1
            for prod in obs.production:
                unit_counts[prod.item] = unit_counts.get(prod.item, 0) + 1
            for requested in self._unit_requests:
                unit_counts[requested] = unit_counts.get(requested, 0) + 1

        if item_type in PLANE_TYPES:
            airfields = sum(1 for b in obs.buildings if self._canonical_building_type(b.type) == "afld")
            if airfields <= 0:
                return False
            plane_count = sum(unit_counts.get(t, 0) for t in PLANE_TYPES)
            return plane_count < airfields * AIRFIELD_PLANE_CAPACITY

        if item_type in AIRCRAFT_TYPES - PLANE_TYPES:
            helipads = sum(1 for b in obs.buildings if self._canonical_building_type(b.type) == "hpad")
            if helipads <= 0:
                return False
            aircraft_count = sum(unit_counts.get(t, 0) for t in AIRCRAFT_TYPES - PLANE_TYPES)
            return aircraft_count < helipads * HELIPAD_AIRCRAFT_CAPACITY

        return True

    def _economy_ready_for_tech(self, obs: OpenRAObservation) -> bool:
        refinery_count = sum(1 for b in obs.buildings if b.type == "proc")
        if refinery_count < 2:
            return False
        return self._harvester_target(obs) >= max(2, refinery_count)

    def _harvester_target(self, obs: OpenRAObservation) -> int:
        refinery_count = sum(1 for b in obs.buildings if b.type == "proc")
        if refinery_count <= 0:
            return 0

        target = max(INITIAL_HARVESTERS, refinery_count)
        patch_states = self._resource_patch_states(obs)
        if patch_states:
            safe_capacity = sum(
                min(
                    int(state["capacity"]),
                    1 if int(state["refinery_count"]) <= 0 else int(state["capacity"]),
                )
                for state in patch_states
                if int(state["threat"]) == 0
                and float(state["depletion_ratio"]) < 0.9
                and int(state["score"]) > -300
            )
            if safe_capacity > 0:
                desired_cap = INITIAL_HARVESTERS + max(0, refinery_count - 1)
                target = max(refinery_count, min(safe_capacity, desired_cap))

        target = min(target, UNIT_LIMITS.get("harv", target))
        if self._in_recovery_mode(obs):
            recovery_cap = 0 if refinery_count == 0 else 2 if refinery_count == 1 else RECOVERY_HARVESTER_CAP
            target = min(target, recovery_cap)
        return target

    def _should_delay_harvester_request(self, obs: OpenRAObservation, current_harvesters: int) -> bool:
        if not self._in_recovery_mode(obs):
            return False
        return current_harvesters >= self._harvester_target(obs) and self._combat_unit_count(obs) < RECOVERY_EXIT_COMBAT

    def _desired_unit_share(
        self,
        obs: OpenRAObservation,
        item_type: str,
        unit_counts: dict[str, int],
    ) -> int:
        share = UNITS_TO_BUILD.get(item_type, 0)
        if share <= 0:
            return 0

        queue_type = self._queue_type_for_unit(item_type)
        if queue_type is not None and self._unit_delay_active(obs, item_type):
            return 0
        if not self._production_support_available(obs, item_type, unit_counts):
            return 0
        if item_type in AIRCRAFT_TYPES and self._available_credits(obs) < 3000:
            return 0
        if item_type == "harv":
            return share if unit_counts.get("harv", 0) < self._harvester_target(obs) else 0
        if item_type == "dog" and (obs.tick > 2500 or any(b.type in WAR_FACTORY_TYPES for b in obs.buildings)):
            return 0

        has_weap = any(b.type in WAR_FACTORY_TYPES for b in obs.buildings)
        base_under_pressure = self._base_under_pressure(obs)
        economy_ready_for_tech = self._economy_ready_for_tech(obs)
        refinery_count = sum(1 for b in obs.buildings if b.type == "proc")
        infantry_count = sum(unit_counts.get(t, 0) for t in INFANTRY_TYPES if t != "dog")
        vehicle_count = sum(unit_counts.get(t, 0) for t in VEHICLE_TYPES if t not in {"harv", "mcv"})

        if queue_type is not None and not base_under_pressure:
            idle_cap = QUEUE_IDLE_BASE_CAPS.get(queue_type)
            if idle_cap is not None and self._idle_base_unit_count(obs, queue_type) >= idle_cap:
                if item_type in {"dog", "e1", "e2", "e3", "jeep", "apc", "ftrk", "pt"}:
                    return 0
                if queue_type in {"Plane", "Aircraft", "Ship"}:
                    return 0
                share = int(share * 0.55)

        if item_type in AIRCRAFT_TYPES | SHIP_TYPES and not economy_ready_for_tech:
            return 0
        if item_type in {"4tnk", "ttnk", "stnk"} and (not economy_ready_for_tech or refinery_count < 2):
            share = int(share * 0.55)
        if base_under_pressure and item_type in AIRCRAFT_TYPES | SHIP_TYPES:
            return 0

        if has_weap:
            if item_type in INFANTRY_TYPES:
                share = max(0, int(share * 0.85))
                if infantry_count >= 36 and vehicle_count < max(10, infantry_count // 3):
                    return 0
            elif item_type in {"1tnk", "2tnk", "3tnk", "4tnk", "ttnk", "stnk", "arty", "v2rl"}:
                share = int(share * 1.2)
            elif item_type in {"apc", "jeep", "ftrk"}:
                share = int(share * 1.1)

        if base_under_pressure:
            if item_type in {"e1", "e3", "apc", "jeep", "ftrk", "1tnk", "2tnk", "arty", "v2rl"}:
                share = int(share * 1.15)
            elif item_type in {"4tnk", "ttnk", "stnk"}:
                share = int(share * 0.75)

        if self._in_recovery_mode(obs):
            if item_type in AIRCRAFT_TYPES:
                return 0
            if item_type in {"e1", "e3", "apc", "jeep", "ftrk", "1tnk", "2tnk", "arty", "v2rl"}:
                share = int(share * 1.25)
            elif item_type in {"4tnk", "ttnk", "stnk"}:
                share = int(share * 0.7)

        return share

    def _ensure_mcv_requests(self, obs: OpenRAObservation):
        if self._in_recovery_mode(obs):
            return
        if self._build_index < len(BUILD_ORDER):
            return
        if not any(b.type in WAR_FACTORY_TYPES for b in obs.buildings):
            return
        if self._available_credits(obs) < BUILD_ADDITIONAL_MCV_CASH_AMOUNT:
            return

        patch_states = self._resource_patch_states(obs)
        viable_expansions = [
            state
            for state in patch_states
            if int(state["threat"]) == 0
            and float(state["depletion_ratio"]) < 0.8
            and int(state["expansion_score"]) > 0
        ]
        if not viable_expansions:
            return

        conyards = sum(1 for b in obs.buildings if b.type == "fact")
        mcvs = sum(1 for u in obs.units if u.type == "mcv")
        pending = sum(1 for p in obs.production if p.item == "mcv") + self._requested_production_count("mcv")
        best_expansion = max(viable_expansions, key=lambda state: int(state["expansion_score"]))
        refinery_count = sum(1 for b in obs.buildings if b.type == "proc")
        if conyards + mcvs + pending < MINIMUM_CONSTRUCTION_YARD_COUNT:
            if int(best_expansion["expansion_score"]) >= 250 and refinery_count >= max(2, min(3, int(best_expansion["capacity"]))):
                self._request_unit_production("mcv")

    def _pick_expansion_target(self, obs: OpenRAObservation) -> Optional[Tuple[int, int]]:
        patch_target = self._best_expansion_patch_target(obs)
        if patch_target is not None:
            return patch_target

        patch_states = self._resource_patch_states(obs)
        fallback_patch_states = [
            state
            for state in patch_states
            if int(state["threat"]) == 0
            and float(state["depletion_ratio"]) < 0.9
        ]
        if fallback_patch_states:
            best_state = max(
                fallback_patch_states,
                key=lambda state: (
                    int(state["expansion_score"]),
                    int(state["capacity"]),
                    -int(state["base_distance"]),
                ),
            )
            if int(best_state["expansion_score"]) > -200:
                return best_state["target"]  # type: ignore[return-value]

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

    def _attack_wave_units(
        self,
        obs: OpenRAObservation,
        squad_units: list[UnitInfoModel],
    ) -> list[UnitInfoModel]:
        base_center = self._base_center(obs)
        if base_center is None:
            return squad_units

        dedicated_defenders = len(self._protection_squad) + len(self._temporary_defenders)
        if self._base_under_pressure(obs):
            if dedicated_defenders >= PROTECTION_SQUAD_MIN_SIZE:
                return squad_units
            reserve = min(len(squad_units), max(2, PROTECTION_SQUAD_MIN_SIZE - dedicated_defenders))
            if reserve <= 0:
                return squad_units

            bx, by = base_center
            nearest_to_base = sorted(
                squad_units,
                key=lambda u: self._cell_distance(u.cell_x, u.cell_y, bx, by),
            )
            reserve_ids = {u.actor_id for u in nearest_to_base[:reserve]}
            attackers = [u for u in squad_units if u.actor_id not in reserve_ids]
            return attackers or squad_units

        reserve = max(HOME_GUARD_MIN_RESERVE, len(squad_units) // 5)
        reserve = min(HOME_GUARD_MAX_RESERVE, reserve)
        reserve = max(0, reserve - min(dedicated_defenders, HOME_GUARD_MIN_RESERVE))
        reserve = min(reserve, max(0, len(squad_units) - max(10, self._assault_threshold // 2)))
        if reserve <= 0:
            return squad_units

        bx, by = base_center
        nearest_to_base = sorted(
            squad_units,
            key=lambda u: self._cell_distance(u.cell_x, u.cell_y, bx, by),
        )
        reserve_ids = {u.actor_id for u in nearest_to_base[:reserve]}
        attackers = [u for u in squad_units if u.actor_id not in reserve_ids]
        return attackers or squad_units

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
        cautious: bool = False,
        squad_name: str = "assault",
    ) -> bool:
        own_units = [u for u in squad_units if u.can_attack]
        if not own_units:
            return False

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

        required_ratio = 1.05
        min_hp = 0.48
        if squad_name == "protection":
            required_ratio = 0.92
            min_hp = 0.4
        elif squad_name == "rush":
            required_ratio = 1.0
            min_hp = 0.55
        elif squad_name in {"air", "naval"}:
            required_ratio = 1.08
            min_hp = 0.5

        if rush:
            required_ratio = min(required_ratio, 1.04)

        if cautious:
            required_ratio += 0.12
            min_hp = max(min_hp, 0.5)

        if own_avg_hp < RETREAT_HEALTH_THRESHOLD:
            required_ratio += 0.18
        elif own_avg_hp >= enemy_avg_hp:
            required_ratio -= 0.08

        if speed_ratio < 0.9 and squad_name not in {"air", "rush"}:
            required_ratio += 0.05

        if squad_name == "air" and any(b.type in {"sam", "agun", "tsla"} for b in enemy_buildings):
            required_ratio += 0.15
        if squad_name == "naval" and enemy_buildings:
            required_ratio += 0.08
        if squad_name != "protection" and any(b.type in {"tsla", "gun", "ftur", "agun"} for b in enemy_buildings):
            required_ratio += 0.08

        if not enemy_units and not any(b.type in DEFENSE_STRUCTURE_TYPES | {"agun", "sam"} for b in enemy_buildings):
            required_ratio -= 0.1

        required_ratio = max(0.82, required_ratio)
        return own_avg_hp >= min_hp and power_ratio >= required_ratio

    def _pick_priority_target(
        self,
        obs: OpenRAObservation,
        x: Optional[int],
        y: Optional[int],
        local_only: bool,
        squad_name: str = "assault",
    ) -> Optional[Tuple[int, int, int, str, str]]:
        best: Optional[Tuple[float, Tuple[int, int, int, str, str]]] = None
        local_radius = LOCAL_FIGHT_RADIUS + (4 if squad_name in {"air", "naval"} else 0)

        for b in obs.visible_enemy_buildings:
            if local_only and x is not None and y is not None and self._cell_distance(x, y, b.cell_x, b.cell_y) > local_radius:
                continue
            priority = TARGET_BUILDING_PRIORITY.get(b.type, 40)
            if squad_name == "protection":
                priority += 8 if b.type in DEFENSE_STRUCTURE_TYPES else -12
            elif squad_name == "rush":
                if b.type in {"proc", "weap", "fact", "powr", "apwr"}:
                    priority += 12
            elif squad_name == "air":
                if b.type in {"proc", "weap", "fact", "powr", "apwr", "hpad", "afld", "afld.ukraine"}:
                    priority += 14
                if b.type in {"sam", "agun", "tsla"}:
                    priority -= 25
            elif squad_name == "naval":
                if b.type in NAVAL_STRUCTURE_TYPES | {"proc", "weap"}:
                    priority += 10
                if b.type in {"sam", "agun", "tsla"}:
                    priority -= 10
            dist = self._cell_distance(x, y, b.cell_x, b.cell_y) if x is not None and y is not None else 0
            score = priority * 1000 - dist * 20 + (1.0 - b.hp_percent) * 120
            candidate = (b.actor_id, b.cell_x, b.cell_y, b.type, "building")
            if best is None or score > best[0]:
                best = (score, candidate)

        for e in obs.visible_enemies:
            if local_only and x is not None and y is not None and self._cell_distance(x, y, e.cell_x, e.cell_y) > local_radius:
                continue
            if "husk" in e.type:
                continue
            if not e.can_attack and e.type not in {"harv", "mcv"} and squad_name not in {"rush", "air"}:
                continue
            priority = TARGET_UNIT_PRIORITY.get(e.type, 30 if e.can_attack else 10)
            if squad_name == "protection":
                if e.can_attack:
                    priority += 15
                if e.type in {"harv", "mcv"}:
                    priority -= 20
            elif squad_name == "rush":
                if e.type in {"harv", "mcv", "arty", "v2rl"}:
                    priority += 10
            elif squad_name == "air":
                if e.type in {"harv", "mcv", "arty", "v2rl", "ftrk"}:
                    priority += 14
            elif squad_name == "naval":
                if e.type in SHIP_TYPES:
                    priority += 18
                elif e.type in AIRCRAFT_TYPES:
                    priority -= 10
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
        if not obs.buildings:
            return self._base_center(obs)

        type_bonus = {
            "fact": 420,
            "weap": 280,
            "proc": 260,
            "ftur": 220,
            "gun": 200,
            "tsla": 220,
            "pbox": 140,
            "hbox": 140,
            "powr": 90,
            "apwr": 110,
        }
        best_score: Optional[int] = None
        best_pos: Optional[Tuple[int, int]] = None
        for building in obs.buildings:
            bx = building.cell_x if building.cell_x > 0 else building.pos_x // 1024
            by = building.cell_y if building.cell_y > 0 else building.pos_y // 1024
            leader_dist = self._cell_distance(leader.cell_x, leader.cell_y, bx, by)
            enemy_clearance = min(
                [self._cell_distance(enemy.cell_x, enemy.cell_y, bx, by) for enemy in obs.visible_enemies] + [LOCAL_FIGHT_RADIUS + 8]
            )
            static_clearance = min(
                [self._cell_distance(enemy.cell_x, enemy.cell_y, bx, by) for enemy in obs.visible_enemy_buildings] + [LOCAL_FIGHT_RADIUS + 8]
            )
            canonical = self._canonical_building_type(building.type)
            score = type_bonus.get(canonical, 80)
            score += min(enemy_clearance, LOCAL_FIGHT_RADIUS + 8) * 18
            score += min(static_clearance, LOCAL_FIGHT_RADIUS + 8) * 10
            score -= leader_dist * 8
            if canonical in DEFENSE_STRUCTURE_TYPES:
                score += 40
            if best_score is None or score > best_score:
                best_score = score
                best_pos = (bx, by)

        return best_pos or self._base_center(obs)

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
