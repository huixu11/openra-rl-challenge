"""Python reimplementation of OpenRA's Normal AI (ModularBot@NormalAI).

Mirrors the C# modular bot architecture with these managers:
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

COMBAT_TYPES = (
    {"e1", "e2", "e3", "e4", "e7", "shok"} |
    {"apc", "jeep", "arty", "v2rl", "ftrk", "1tnk", "2tnk", "3tnk", "4tnk", "ttnk", "stnk"} |
    AIRCRAFT_TYPES
)

SQUAD_SIZE = 20
EXCLUDE_FROM_SQUADS = {"harv", "mcv", "dog", "badr.bomber", "u2"}
BARRACKS_TYPES = {"tent", "barr"}
WAR_FACTORY_TYPES = {"weap"}
POWER_DOWN_TYPES = {"dome", "tsla", "mslo", "agun", "sam"}

BUILDING_COSTS: dict[str, int] = {
    "powr": 300, "apwr": 500, "proc": 2000, "weap": 2000,
    "barr": 500, "tent": 500, "kenn": 500,
    "dome": 1000, "hpad": 1500, "afld": 1500,
    "fix": 1200, "atek": 1500, "stek": 1500,
    "pbox": 400, "hbox": 600, "gun": 600, "ftur": 600,
    "tsla": 1500, "agun": 800, "sam": 750,
}

# Initial build order — same sequence the C# AI follows in practice.
# Uses "barracks" as a placeholder resolved to tent or barr at runtime.
BUILD_ORDER = ["powr", "barracks", "proc", "weap", "powr"]

ATTACK_FORCE_INTERVAL = 600
RUSH_TICKS = 4000


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
        self._last_assign_tick = 0
        self._enemy_base_pos: Optional[Tuple[int, int]] = None

        # Repair / power
        self._repair_issued: set[int] = set()
        self._powered_down: set[int] = set()

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
            if len(combat) >= 6 or obs.tick >= RUSH_TICKS:
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
            if prod.queue_type == "Building" and prod.progress >= 0.99:
                x, y = self._placement_offset(cy)
                commands.append(CommandModel(
                    action=ActionType.PLACE_BUILDING,
                    item_type=prod.item,
                    target_x=x, target_y=y,
                ))
                self._placement_count += 1
        return commands

    def _placement_offset(self, cy: BuildingInfoModel) -> Tuple[int, int]:
        cx = cy.cell_x if cy.cell_x > 0 else cy.pos_x // 1024
        cy_y = cy.cell_y if cy.cell_y > 0 else cy.pos_y // 1024
        offsets = [
            (3, 0), (-3, 0), (0, 3), (0, -3),
            (3, 3), (-3, 3), (3, -3), (-3, -3),
            (6, 0), (-6, 0), (0, 6), (0, -6),
            (2, 0), (-2, 0), (0, 2), (0, -2),
            (4, 0), (-4, 0), (0, 4), (0, -4),
        ]
        idx = self._placement_count % len(offsets)
        dx, dy = offsets[idx]
        return cx + dx, cy_y + dy

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

        building_in_queue = any(p.queue_type == "Building" for p in obs.production)
        if building_in_queue:
            return commands
        if obs.tick - self._last_build_tick < 10:
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
                if obs.economy.cash >= cost:
                    self._log(f"Building {item} [{self._build_index+1}/{len(BUILD_ORDER)}] (${obs.economy.cash})")
                    commands.append(CommandModel(action=ActionType.BUILD, item_type=item))
                    self._last_build_tick = obs.tick
                    self._build_index += 1
            return commands

        # Phase 2: dynamic expansion when rich
        if obs.economy.cash < 600:
            return commands
        for item in self._dynamic_priorities(obs):
            if self._can_produce(obs, item):
                cost = BUILDING_COSTS.get(item, 500)
                if obs.economy.cash >= cost:
                    self._log(f"Building {item} (dynamic, ${obs.economy.cash})")
                    commands.append(CommandModel(action=ActionType.BUILD, item_type=item))
                    self._last_build_tick = obs.tick
                    break

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

    def _dynamic_priorities(self, obs: OpenRAObservation) -> list[str]:
        bldg_counts: dict[str, int] = {}
        for b in obs.buildings:
            bldg_counts[b.type] = bldg_counts.get(b.type, 0) + 1
        result = []
        power_balance = obs.economy.power_provided - obs.economy.power_drained
        if power_balance < 40:
            result.extend(["apwr", "powr"])
        if bldg_counts.get("proc", 0) < 2:
            result.append("proc")
        result.extend(["gun", "ftur", "pbox", "sam"])
        result.extend(["dome", "hpad", "afld"])
        return result

    # ── Unit Production ───────────────────────────────────────────

    def _manage_unit_production(self, obs: OpenRAObservation) -> List[CommandModel]:
        commands = []
        if self.phase == "deploy_mcv":
            return commands
        if obs.economy.cash < 100:
            return commands

        # Infantry queue
        inf_producing = any(
            p.queue_type == "Infantry" and p.progress < 0.99
            for p in obs.production
        )
        if not inf_producing and any(b.type in BARRACKS_TYPES for b in obs.buildings):
            unit = self._pick_unit(obs, INFANTRY_TYPES)
            if unit:
                commands.append(CommandModel(action=ActionType.TRAIN, item_type=unit))

        # Vehicle queue
        veh_producing = any(
            p.queue_type == "Vehicle" and p.progress < 0.99
            for p in obs.production
        )
        if not veh_producing and any(b.type in WAR_FACTORY_TYPES for b in obs.buildings):
            unit = self._pick_unit(obs, VEHICLE_TYPES - {"mcv"})
            if unit:
                commands.append(CommandModel(action=ActionType.TRAIN, item_type=unit))

        return commands

    def _pick_unit(self, obs: OpenRAObservation, allowed: set[str]) -> Optional[str]:
        candidates, weights = [], []
        unit_counts: dict[str, int] = {}
        for u in obs.units:
            unit_counts[u.type] = unit_counts.get(u.type, 0) + 1
        for utype, w in UNITS_TO_BUILD.items():
            if utype not in allowed:
                continue
            if not self._can_produce(obs, utype):
                continue
            limit = UNIT_LIMITS.get(utype)
            if limit is not None and unit_counts.get(utype, 0) >= limit:
                continue
            candidates.append(utype)
            weights.append(w)
        if not candidates:
            return None
        return random.choices(candidates, weights=weights, k=1)[0]

    # ── Squads ────────────────────────────────────────────────────

    def _manage_squads(self, obs: OpenRAObservation) -> List[CommandModel]:
        commands = []
        if self.phase in ("deploy_mcv", "build_base"):
            return commands

        # Assign new units to squad
        if obs.tick - self._last_assign_tick >= 100:
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
        if not obs.visible_enemies:
            return commands
        cy = self._find_building(obs, "fact")
        if not cy:
            return commands
        bx = cy.cell_x if cy.cell_x > 0 else cy.pos_x // 1024
        by = cy.cell_y if cy.cell_y > 0 else cy.pos_y // 1024
        threat = None
        for e in obs.visible_enemies:
            if abs(e.cell_x - bx) + abs(e.cell_y - by) < 15:
                threat = e
                break
        if not threat:
            return commands
        alive = {u.actor_id: u for u in obs.units}
        for uid in self._attack_squad:
            u = alive.get(uid)
            if not u:
                continue
            if abs(u.cell_x - bx) + abs(u.cell_y - by) < 20:
                commands.append(CommandModel(
                    action=ActionType.ATTACK_MOVE, actor_id=uid,
                    target_x=threat.cell_x, target_y=threat.cell_y,
                ))
        return commands

    def _handle_attack(self, obs: OpenRAObservation) -> List[CommandModel]:
        commands = []
        alive = {u.actor_id: u for u in obs.units}
        squad = [uid for uid in self._attack_squad if uid in alive]
        if len(squad) < 2:
            return commands
        if len(squad) < SQUAD_SIZE and self.phase != "active":
            return commands

        tx, ty = self._find_attack_target(obs)
        redirected = 0
        for uid in squad:
            u = alive[uid]
            if u.is_idle:
                commands.append(CommandModel(
                    action=ActionType.ATTACK_MOVE, actor_id=uid,
                    target_x=tx, target_y=ty,
                ))
                redirected += 1
        if redirected:
            self._log(f"Attack-move {redirected}/{len(squad)} idle -> ({tx},{ty})")
        return commands

    def _find_attack_target(self, obs: OpenRAObservation) -> Tuple[int, int]:
        if obs.visible_enemy_buildings:
            prods = [b for b in obs.visible_enemy_buildings
                     if b.type in ("fact", "tent", "barr", "weap", "proc")]
            t = prods[0] if prods else obs.visible_enemy_buildings[0]
            self._enemy_base_pos = (t.cell_x, t.cell_y)
            return t.cell_x, t.cell_y
        if obs.visible_enemies:
            e = obs.visible_enemies[0]
            if self._enemy_base_pos is None:
                self._enemy_base_pos = (e.cell_x, e.cell_y)
            return e.cell_x, e.cell_y
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
        for b in obs.buildings:
            if (b.hp_percent < 0.75 and not b.is_repairing
                    and b.actor_id not in self._repair_issued
                    and obs.economy.cash >= 500):
                commands.append(CommandModel(action=ActionType.REPAIR, actor_id=b.actor_id))
                self._repair_issued.add(b.actor_id)
        return commands

    # ── Power ─────────────────────────────────────────────────────

    def _manage_power(self, obs: OpenRAObservation) -> List[CommandModel]:
        commands = []
        bal = obs.economy.power_provided - obs.economy.power_drained
        if bal < 0:
            for b in obs.buildings:
                if b.type in POWER_DOWN_TYPES and b.is_powered and b.actor_id not in self._powered_down:
                    commands.append(CommandModel(action=ActionType.POWER_DOWN, actor_id=b.actor_id))
                    self._powered_down.add(b.actor_id)
                    return commands
        return commands

    # ── Cleanup ───────────────────────────────────────────────────

    def _cleanup_dead(self, obs: OpenRAObservation):
        alive = {u.actor_id for u in obs.units}
        self._attack_squad = [uid for uid in self._attack_squad if uid in alive]
        alive_b = {b.actor_id for b in obs.buildings}
        self._repair_issued &= alive_b
        self._rally_set &= alive_b
        self._powered_down &= alive_b

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
