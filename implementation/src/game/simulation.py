from __future__ import annotations

import math
from dataclasses import dataclass, field
import re
from typing import List, Optional, Tuple

from game.grid import Grid
from game.store import ResourceStore
from game.types import ComponentTypeStats
from game.upgrades import UpgradeManager, StatCategory


def _fuel_index(name: str) -> Optional[int]:
    match = re.match(r"Fuel(\d+)", name)
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def _component_shop_page(name: str) -> int:
    if name.startswith("Fuel"):
        fuel = _fuel_index(name)
        if fuel is None:
            return 0
        # Fuel 1-6 are base fuels, 7-11 are experimental.
        return 0 if fuel <= 6 else 2
    if name.startswith("Capacitor") or name.startswith("GenericPower"):
        return 0
    if name.startswith(
        (
            "Vent",
            "Coolant",
            "Exchanger",
            "Reflector",
            "Plate",
            "Plating",
            "Inlet",
            "Outlet",
            "GenericHeat",
        )
    ):
        return 1
    if name.startswith(("Clock", "GenericInfinity")):
        return 2
    return 3


@dataclass
class ReactorComponent:
    stats: ComponentTypeStats
    heat: float = 0.0
    durability: float = 0.0
    last_power: float = 0.0
    last_heat: float = 0.0
    depleted: bool = False
    grid_x: int = 0
    grid_y: int = 0
    grid_z: int = 0
    pulse_count: int = 0  # pulses received from neighbors (recalculated on layout change)

    def __post_init__(self):
        if self.durability == 0.0 and self.stats.max_durability > 0:
            self.durability = self.stats.max_durability


@dataclass
class ExplosionEffect:
    """Visual explosion animation spawned when a component is destroyed.

    RE: Explosion MonoBehaviour — 12 frames (Explosion_0..11), self-destructs after last frame.
    Stores grid coordinates; screen position computed at draw time via cell_to_screen()
    so scrolling doesn't break explosion rendering.
    """
    grid_x: int
    grid_y: int
    frame: int = 0
    time: float = 0.0
    fps: float = 16.0  # 12 frames at 16fps ≈ 0.75s


@dataclass
class Simulation:
    """Reactor simulation matching the decoded WASM tick pipeline.

    Tick pipeline (from wasm-decompilation-notes.md):
    1. PrepareMultipliers (upgrade-based global scaling)
    2. DistributePulses (count pulses each cell receives)
    3. DrainDurability (fuel -1/tick, reflectors -neighborPulses/tick)
    4. GeneratePowerAndHeat (fuel cells produce power+heat)
    5. HeatExchange phase 1+2 (cells exchange heat toward equilibrium)
    6. VentReactor (SelfVentRate: cells vent heat to air)
    7. VentHeatToAir (additional vent pass)
    8. ExchangeWithHull (ReactorVentRate: cells <-> reactor hull)
    9. EarnMoney (power -> money conversion)
    """
    store: ResourceStore = field(default_factory=ResourceStore)
    components: List[ReactorComponent] = field(default_factory=list)
    grid: Optional[Grid] = None
    shop_components: List[ComponentTypeStats] = field(default_factory=list)
    selected_component_index: int = -1
    hover_component: Optional[ComponentTypeStats] = None
    hover_placed_component: Optional[ReactorComponent] = None
    shop_page: int = 0
    prestige_level: int = 0
    upgrade_manager: UpgradeManager = field(default_factory=UpgradeManager)

    # View state: "reactor", "upgrades", "prestige", "options", "statistics", "help"
    view_mode: str = "reactor"

    # Reactor state (Reactor class offsets)
    reactor_heat: float = 0.0        # Reactor+0x28 heat store
    stored_power: float = 0.0        # Reactor+0x30 accumulated power (manual sell to convert)
    last_heat_change: float = 0.0    # per-tick delta for UI
    last_power_change: float = 0.0   # per-tick delta for UI
    preview_vent_capacity: float = 0.0    # current vent dissipation capacity (/tick)
    preview_outlet_capacity: float = 0.0  # current outlet transfer capacity (/tick)
    preview_active_dissipation: float = 0.0  # vent capacity for outlet-adjacent vents only

    # Max capacities (RE: unnamed_function_10412/10413)
    # Heat: GetStatCached(1, 0xb) * 1000.0 + sum(component contributions)
    # Power: GetStatCached(1, 0xc) * 100.0 + sum(component contributions)
    # With no upgrades: GetStatCached returns 1.0
    max_reactor_heat: float = 1000.0   # Reactor.cachedMaxHeat (+0x48) = 1.0 * 1000.0
    max_reactor_power: float = 100.0   # Reactor.cachedMaxPower (+0x58) = 1.0 * 100.0

    # Manual vent/sell amounts (RE: GetStatCached(1, 14) / GetStatCached(1, 13))
    # With no upgrades, GetStatCached returns 1.0
    manual_vent_amount: float = 1.0    # stat 14 ManualVent
    manual_sell_mult: float = 1.0      # stat 13 ManualSell

    # Global multipliers (from upgrade system — default 1.0 = no upgrades)
    self_vent_mult: float = 1.0      # Reactor+0x70
    heat_exchange_mult: float = 1.0  # Reactor+0x78
    power_cap_mult: float = 1.0     # *(Reactor+0x80)+0x10
    heat_cap_mult: float = 1.0      # *(Reactor+0x80)+0x08

    # Toggle states (from Controller class, offset +0xA0 and +0x44→+0x2A)
    paused: bool = False
    replace_mode: bool = True  # Gates auto-replacement of depleted fuel (perpetual upgrade)

    # Active explosion animations (cosmetic, updated independently of ticks)
    explosions: List[ExplosionEffect] = field(default_factory=list)

    # Protium depletion counter — RE: Reactor+0x38, persistent count of depleted Protium cells.
    # Each depleted Protium adds +1% power to all surviving Protium cells.
    depleted_protium_count: int = 0

    # Dirty flag for pulse recalculation
    _pulses_dirty: bool = True

    # Tick timing — from unnamed_function_10418 (Simulation.LogicalUpdate):
    #   while (Time.time - lastTick > 1.0 / ticksPerSecond):
    #       executeTick(); lastTick += 1.0 / ticksPerSecond
    # Base ticksPerSecond = multiplier * (additive + 1.0) = 1.0 with no upgrades.
    ticks_per_second: float = 1.0
    _tick_accumulator: float = 0.0

    # Statistics tracking (money/power/heat stats are in ResourceStore)
    total_ticks: int = 0

    # Reset confirmation timer (for Options panel double-click)
    reset_confirm_timer: float = 0.0

    # Prestige button state machine (RE: fn 10493/10490, Controller +0xA2/+0xA4)
    prestige_confirming: bool = False   # Phase B: awaiting second click
    prestige_can_refund: bool = False   # Phase C: post-prestige refund window

    def _stat_mult(self, type_id: int, stat: int) -> float:
        """Return upgrade multiplier for a (component_type_id, stat_category) pair.

        RE: effective_stat = base_stat * GetUpgradeStatBonus(type_id, stat).
        With no upgrades purchased, returns 1.0 (identity).
        """
        return self.upgrade_manager.get_upgrade_stat_bonus(type_id, stat)

    def step(self, dt: float) -> None:
        """Accumulate time and fire ticks at the correct rate."""
        self._tick_accumulator += dt
        tick_interval = 1.0 / self.ticks_per_second if self.ticks_per_second > 0 else 1.0
        while self._tick_accumulator >= tick_interval:
            self._tick_accumulator -= tick_interval
            self._do_tick()

    def _do_tick(self) -> None:
        """Execute one simulation tick following the decoded pipeline."""
        self.reactor_heat = max(0.0, self.reactor_heat)
        self.last_heat_change = 0.0
        self.last_power_change = 0.0
        self.total_ticks += 1

        # Note: NO early return for empty components — auto-vent must always run
        # (the binary's tick pipeline always executes fn 10428/10429 regardless)

        # Step 1: PrepareMultipliers
        self.upgrade_manager.prepare_multipliers(self)
        self.preview_vent_capacity = self.vent_dissipation_capacity_per_tick()
        self.preview_outlet_capacity = self.outlet_transfer_capacity_per_tick()
        self.preview_active_dissipation = self.active_dissipation_capacity_per_tick()

        # Step 2: DistributePulses (recalc when layout changes)
        if self._pulses_dirty:
            self._distribute_pulses()
            self._pulses_dirty = False

        # Step 3: DrainDurability
        self._drain_durability()

        # Step 4: GeneratePowerAndHeat
        power_gen, heat_gen = self._generate_power_and_heat()
        self.last_power_change += power_gen
        self.last_heat_change += heat_gen

        # Step 5: HeatExchange (simplified — exchange between neighbors toward equilibrium)
        self._heat_exchange()

        # Step 5b: ExtremeCoolant radius-2 heat absorption (fn 10424 post-loop)
        self._extreme_coolant_absorb()

        # Step 6: ExchangeWithHull (fn 10438 — Inlets/Outlets use ReactorTransferRate stat 10)
        heat_before_hull = self.reactor_heat
        self._exchange_with_hull()
        hull_exchange = self.reactor_heat - heat_before_hull  # positive = heat moved IN

        # Step 7: VentHeatToAir (fn 10437 — Vents use SelfVentRate stat 6)
        component_vented = self._vent_heat_to_air()

        # Build the net heat accumulator matching the binary (Simulation+0x30):
        # fn 10423 adds heat_gen, fn 10425 adds component_vented, fn 10427 subtracts hull_exchange
        # RE: lines 389444, 389804, 389876
        net_heat_accumulator = heat_gen + component_vented - hull_exchange

        # Step 9: Check explosions (RE: fn 10429 — explosions BEFORE auto-vent)
        self._check_explosions()

        # Step 10: AutoVent (RE: fn 10429 lines 390126-390190)
        # Auto-vent rate = maxHeat * autoVentMult * 0.01 + maxHeat * 0.0001
        # Enhanced decay when overheating AND net heat accumulator <= 0:
        #   rate = max(normalRate, (heat - maxHeat) * 0.05)
        auto_vent = self.auto_vent_rate_per_tick()
        if self.reactor_heat > self.max_reactor_heat and net_heat_accumulator <= 0:
            overheat_decay = (self.reactor_heat - self.max_reactor_heat) * 0.05
            auto_vent = max(auto_vent, overheat_decay)
        if auto_vent > 0 and self.reactor_heat > 0:
            vented = min(self.reactor_heat, auto_vent)
            self.reactor_heat -= vented
            self.last_heat_change -= vented  # RE: line 390147 — vent subtracted from accumulator
            # RE: fn 10428 lines 390162-390183 — auto-vent counts toward lifetime heat dissipated
            self.store.total_heat_dissipated += vented
            self.store.heat_dissipated_this_game += vented

        # Auto-sell: convert power to money each tick
        # RE: unnamed_function_10430 (Reactor.AutoSellPower)
        auto_sell = self.auto_sell_rate_per_tick()
        if auto_sell > 0 and self.stored_power > 0:
            sold = min(self.stored_power, auto_sell)
            self.stored_power -= sold
            self.store.add_money(sold)

            # RE: fn 10430 — ExtremeCapacitor (Capacitor6, TypeOfComponent=13, tier=6)
            # generates per-component heat when auto-sell is active.
            # Formula: autoSellMult * GetStatCached(type, 12) * 0.005 * (sold / autoSellRate)
            # Heat goes to the component, clamped to its heat capacity.
            auto_sell_mult = self.upgrade_manager.get_upgrade_stat_bonus(
                1, StatCategory.AUTO_SELL_RATE) - 1.0
            if auto_sell_mult > 0:
                sell_ratio = sold / auto_sell if auto_sell > 0 else 0.0
                for comp in self.components:
                    if (comp.stats.type_of_component == "Capacitor"
                            and comp.stats.name == "Capacitor6"
                            and not comp.depleted):
                        # stat 12 = ReactorPowerCapacityIncrease with upgrade bonus
                        power_cap_inc = comp.stats.reactor_power_capacity_increase * self._stat_mult(
                            comp.stats.component_type_id, StatCategory.REACTOR_POWER_CAP_INCREASE)
                        heat_added = auto_sell_mult * power_cap_inc * 0.005 * sell_ratio
                        comp.heat += heat_added
                        # Clamp to component's effective heat capacity
                        eff_cap = self.get_effective_heat_capacity(comp)
                        if eff_cap > 0 and comp.heat > eff_cap:
                            comp.heat = eff_cap

        # Clamp power only (heat is NOT clamped — can exceed for explosion mechanic)
        self.reactor_heat = max(0.0, self.reactor_heat)
        self.stored_power = min(self.stored_power, self.max_reactor_power)

        # Update resource store for UI
        self.store.power = self.stored_power
        self.store.heat = self.reactor_heat

    def _distribute_pulses(self) -> None:
        """RE: unnamed_function_10441 — distribute pulses to self and cardinal neighbors.

        For each non-depleted fuel cell:
          pulses = PulsesPerCore * NumberOfCores (= stats.pulses_produced)
          scale = floor(log2(cores) + 1)  (for single-core: 1)
          self.pulseCount += scale * pulses
          each cardinal neighbor.pulseCount += pulses

        Stavrium (type 0x14/20): distributes to entire row + column instead of
        4 cardinal neighbors.  Self still gets scale * pulses.
        """
        if self.grid is None:
            return
        # Zero all pulse counts
        for comp in self.components:
            comp.pulse_count = 0

        for x, y, z, comp in self.grid.iter_cells():
            if comp is None or comp.depleted:
                continue
            if comp.stats.pulses_produced <= 0:
                continue  # not a fuel cell

            pulses = int(comp.stats.pulses_produced)
            cores = comp.stats.number_of_cores
            # Scale factor: floor(log2(cores) + 1) — for single-core cells this is 1
            scale = int(math.log2(cores) + 1) if cores >= 1 else 1

            # Add to self (line 391434)
            comp.pulse_count += scale * pulses

            # RE: fn 10441 — Stavrium (type 0x14/20) distributes to entire row+column
            if comp.stats.component_type_id == 20:
                # Entire row (same y, all x except self)
                for col in range(self.grid.width):
                    if col != x:
                        ncomp = self.grid.get(col, y, z)
                        if ncomp is not None:
                            ncomp.pulse_count += pulses
                # Entire column (same x, all y except self)
                for row in range(self.grid.height):
                    if row != y:
                        ncomp = self.grid.get(x, row, z)
                        if ncomp is not None:
                            ncomp.pulse_count += pulses
            else:
                # Standard: distribute to cardinal neighbors (line 391459-391472)
                for nx, ny, nz in self.grid.neighbors(x, y, z):
                    ncomp = self.grid.get(nx, ny, nz)
                    if ncomp is not None:
                        ncomp.pulse_count += pulses

    def _drain_durability(self) -> None:
        """Fuel cells lose 1 durability/tick. Reflectors lose neighbor pulse sum."""
        if self.grid is None:
            return
        for x, y, z, comp in self.grid.iter_cells():
            if comp is None or comp.depleted:
                continue

            # Fuel cells: lose 1 durability per tick
            if comp.stats.pulses_produced > 0 and comp.stats.max_durability > 0:
                comp.durability -= 1.0
                if comp.durability <= 0:
                    # RE: Protium (type 0x10/16) — increment permanent depletion counter
                    if comp.stats.component_type_id == 16:
                        self.depleted_protium_count += 1
                    # Perpetual upgrade + replace toggle: auto-replace depleted fuel cell
                    if self.replace_mode and self.upgrade_manager.has_replaces_self(comp.stats.component_type_id):
                        dur_mult = self._stat_mult(comp.stats.component_type_id, StatCategory.MAX_DURABILITY)
                        comp.durability = comp.stats.max_durability * dur_mult
                        comp.heat = 0.0
                    else:
                        comp.depleted = True
                    self._pulses_dirty = True

            # Reflectors: lose durability = sum of neighbor PulsesProduced
            if comp.stats.reflects_pulses > 0 and comp.stats.max_durability > 0:
                neighbor_pulse_sum = 0
                for nx, ny, nz in self.grid.neighbors(x, y, z):
                    ncomp = self.grid.get(nx, ny, nz)
                    if ncomp is not None and not ncomp.depleted:
                        neighbor_pulse_sum += int(ncomp.stats.pulses_produced)
                comp.durability -= neighbor_pulse_sum
                if comp.durability <= 0:
                    comp.depleted = True

    def _generate_power_and_heat(self) -> Tuple[float, float]:
        """RE: unnamed_function_10443 — generate power and heat from fuel cells.

        Power formula (fn 10446): pulseCount × EnergyPerPulse × reflectorMult
        Heat formula (fn 10444): (pulseCount² × HeatPerPulse) / (cellWidth × cellHeight)
        Heat goes to absorbing cardinal neighbors (split equally) or reactor hull.

        Special mechanics:
        - Overheat mult (all fuels): power bonus when reactor_heat > 1000
        - Protium (type 16): power × (depleted_count / 100 + 1.0)
        - Monastium (type 17): power × (1.0 - occupied_7x7 * 0.02)
        - Kymium (type 18): cosine pulsation on power and heat
        """
        if self.grid is None:
            return 0.0, 0.0

        total_power = 0.0
        total_heat_to_reactor = 0.0

        # RE: fn 10443 — overheat multiplier (pre-loop, applies to all fuels)
        # With no CellEffectiveness upgrades (bonus=1.0), mult is always 1.0.
        overheat_mult = 1.0
        if self.reactor_heat > 1000.0:
            cell_eff = self.upgrade_manager.get_upgrade_stat_bonus(1, StatCategory.CELL_EFFECTIVENESS)
            overheat_mult = (math.log(self.reactor_heat) / math.log(1000.0)) * (cell_eff - 1.0) * 0.01 + 1.0

        for x, y, z, comp in self.grid.iter_cells():
            if comp is None or comp.depleted:
                continue
            if comp.stats.energy_per_pulse <= 0:
                continue  # not a fuel cell

            pulse_count = comp.pulse_count
            tid = comp.stats.component_type_id

            # --- Base power (fn 10446) ---
            epp = comp.stats.energy_per_pulse * self._stat_mult(tid, StatCategory.ENERGY_PER_PULSE)
            power = float(pulse_count) * epp

            # RE: Protium (type 0x10/16) — permanent depletion bonus
            if tid == 16:
                power *= self.depleted_protium_count / 100.0 + 1.0

            # --- Base heat (fn 10444) ---
            hpp = comp.stats.heat_per_pulse * self._stat_mult(tid, StatCategory.HEAT_PER_PULSE)
            cell_area = comp.stats.cell_width * comp.stats.cell_height
            heat = (float(pulse_count * pulse_count) * hpp) / max(1, cell_area)

            # --- Kymium (type 0x12/18) — cosine pulsation ---
            if tid == 18:
                max_dur = comp.stats.max_durability * self._stat_mult(tid, StatCategory.MAX_DURABILITY)
                if max_dur > 0:
                    phase = (comp.durability / max_dur) * 8.0 * math.pi
                    cos_val = math.cos(phase)
                    power *= (1.0 - cos_val) / 2.0
                    heat *= (1.0 + cos_val) / 2.0

            # --- Monastium (type 0x11/17) — 7×7 density penalty (power only) ---
            if tid == 17:
                occupied = 0
                for dy in range(-3, 4):
                    for dx in range(-3, 4):
                        nx, ny = x + dx, y + dy
                        if 0 <= nx < self.grid.width and 0 <= ny < self.grid.height:
                            if self.grid.get(nx, ny, z) is not None:
                                occupied += 1
                power *= 1.0 - occupied * 0.02

            # --- Cardinal neighbor scan: reflector bonus + absorber count ---
            reflector_mult = 1.0
            absorber_count = 0
            for nx, ny, nz in self.grid.neighbors(x, y, z):
                ncomp = self.grid.get(nx, ny, nz)
                if ncomp is None:
                    continue
                if ncomp.stats.reflects_pulses > 0 and not ncomp.depleted:
                    ref_bonus = self._stat_mult(ncomp.stats.component_type_id, StatCategory.REFLECTOR_EFFECTIVENESS)
                    reflector_mult += 0.1 * ref_bonus
                if ncomp.stats.heat_capacity > 0:
                    absorber_count += 1

            # Apply multipliers to power (reflector, overheat)
            power *= reflector_mult * overheat_mult

            # Store per-component stats for UI
            comp.last_power = power
            comp.last_heat = heat

            total_power += power

            # Heat distribution (lines 391810-391851)
            if absorber_count > 0:
                heat_per_absorber = heat / absorber_count
                for nx, ny, nz in self.grid.neighbors(x, y, z):
                    ncomp = self.grid.get(nx, ny, nz)
                    if ncomp is None:
                        continue
                    if ncomp.stats.heat_capacity > 0:
                        ncomp.heat += heat_per_absorber
            else:
                total_heat_to_reactor += heat

        self.stored_power += total_power
        self.reactor_heat += total_heat_to_reactor
        self.store.total_power_produced += total_power
        self.store.power_produced_this_game += total_power
        return total_power, total_heat_to_reactor

    def _estimate_generation_preview(self) -> Tuple[float, float]:
        """Estimate next-tick generated power/heat without mutating simulation state."""
        if self.grid is None:
            return 0.0, 0.0

        total_power = 0.0
        total_heat_to_reactor = 0.0

        # Mirror overheat multiplier logic from _generate_power_and_heat.
        overheat_mult = 1.0
        if self.reactor_heat > 1000.0:
            cell_eff = self.upgrade_manager.get_upgrade_stat_bonus(1, StatCategory.CELL_EFFECTIVENESS)
            overheat_mult = (math.log(self.reactor_heat) / math.log(1000.0)) * (cell_eff - 1.0) * 0.01 + 1.0

        for x, y, z, comp in self.grid.iter_cells():
            if comp is None or comp.depleted:
                continue
            if comp.stats.energy_per_pulse <= 0:
                continue

            pulse_count = comp.pulse_count
            tid = comp.stats.component_type_id

            epp = comp.stats.energy_per_pulse * self._stat_mult(tid, StatCategory.ENERGY_PER_PULSE)
            power = float(pulse_count) * epp

            if tid == 16:
                power *= self.depleted_protium_count / 100.0 + 1.0

            hpp = comp.stats.heat_per_pulse * self._stat_mult(tid, StatCategory.HEAT_PER_PULSE)
            cell_area = comp.stats.cell_width * comp.stats.cell_height
            heat = (float(pulse_count * pulse_count) * hpp) / max(1, cell_area)

            if tid == 18:
                max_dur = comp.stats.max_durability * self._stat_mult(tid, StatCategory.MAX_DURABILITY)
                if max_dur > 0:
                    phase = (comp.durability / max_dur) * 8.0 * math.pi
                    cos_val = math.cos(phase)
                    power *= (1.0 - cos_val) / 2.0
                    heat *= (1.0 + cos_val) / 2.0

            if tid == 17:
                occupied = 0
                for dy in range(-3, 4):
                    for dx in range(-3, 4):
                        nx, ny = x + dx, y + dy
                        if 0 <= nx < self.grid.width and 0 <= ny < self.grid.height:
                            if self.grid.get(nx, ny, z) is not None:
                                occupied += 1
                power *= 1.0 - occupied * 0.02

            reflector_mult = 1.0
            absorber_count = 0
            for nx, ny, nz in self.grid.neighbors(x, y, z):
                ncomp = self.grid.get(nx, ny, nz)
                if ncomp is None:
                    continue
                if ncomp.stats.reflects_pulses > 0 and not ncomp.depleted:
                    ref_bonus = self._stat_mult(ncomp.stats.component_type_id, StatCategory.REFLECTOR_EFFECTIVENESS)
                    reflector_mult += 0.1 * ref_bonus
                if ncomp.stats.heat_capacity > 0:
                    absorber_count += 1

            power *= reflector_mult * overheat_mult

            # Keep hover/info panel stats fresh even while paused.
            comp.last_power = power
            comp.last_heat = heat

            total_power += power
            if absorber_count == 0:
                total_heat_to_reactor += heat

        return total_power, total_heat_to_reactor

    def _heat_exchange(self) -> None:
        """RE: fn 10424 — Only exchangers drive heat redistribution.

        The binary iterates ALL components but skips those with stat 9
        (AdjacentTransferRate) == 0.  Only exchangers have a non-zero rate.
        For each exchanger, balance heat with each cardinal neighbor toward
        pooled equilibrium, limited by the exchanger's transfer rate.
        Reflectors (CantLoseHeat flag 0xCA) are skipped as exchange partners.
        """
        if self.grid is None:
            return
        for x, y, z, comp in self.grid.iter_cells():
            if comp is None or comp.depleted:
                continue
            if comp.stats.type_of_component != "Exchanger":
                continue
            # Exchanger's transfer rate = base SelfVentRate × upgrade × global mult
            base_rate = comp.stats.self_vent_rate
            if base_rate <= 0:
                continue
            rate = base_rate * self._stat_mult(
                comp.stats.component_type_id, StatCategory.ADJACENT_TRANSFER_RATE
            ) * self.heat_exchange_mult
            comp_cap = self.get_effective_heat_capacity(comp)
            if comp_cap <= 0:
                continue

            for nx, ny, nz in self.grid.neighbors(x, y, z):
                ncomp = self.grid.get(nx, ny, nz)
                if ncomp is None or ncomp.depleted:
                    continue
                # Skip CantLoseHeat components (0xCA=1): Reflectors + ExtremeCoolant
                if ncomp.stats.cant_lose_heat:
                    continue
                ncap = self.get_effective_heat_capacity(ncomp)
                if ncap <= 0:
                    continue
                # Pooled equilibrium: target fill = combined heat / combined cap
                total_heat = comp.heat + ncomp.heat
                total_cap = comp_cap + ncap
                if total_cap <= 0:
                    continue
                target_ratio = max(0.0, min(1.0, total_heat / total_cap))
                target_neighbor = target_ratio * ncap
                delta = target_neighbor - ncomp.heat  # positive = neighbor needs more
                # Clamp transfer by exchanger rate
                transfer = max(-rate, min(rate, delta))
                if abs(transfer) > 0.001:
                    ncomp.heat += transfer
                    comp.heat -= transfer

    def _extreme_coolant_absorb(self) -> None:
        """RE: fn 10424 post-loop — ExtremeCoolant (Coolant6) absorbs 10% heat from radius-2 neighbors.

        Uses Manhattan distance (diamond shape), radius=2.
        Skips neighbors with CantLoseHeat flag (other ExtremeCoolants, Reflectors).
        Absorbed heat is permanently trapped (ExtremeCoolant has CantLoseHeat=1).
        """
        if self.grid is None:
            return
        for comp in self.components:
            if comp.stats.type_of_component != "Coolant" or comp.stats.name != "Coolant6":
                continue
            if comp.depleted:
                continue
            for nx, ny, nz in self.grid.manhattan_neighbors(comp.grid_x, comp.grid_y, 0, radius=2):
                ncomp = self.grid.get(nx, ny, nz)
                if ncomp is None:
                    continue
                if ncomp.stats.heat_capacity <= 0:
                    continue
                if ncomp.stats.cant_lose_heat:
                    continue
                absorbed = ncomp.heat * 0.1
                ncomp.heat -= absorbed
                comp.heat += absorbed

    def _vent_heat_to_air(self) -> float:
        """RE: fn 10437 — Vents dissipate their stored heat to the environment.

        Only components with binary SelfVentRate (stat 6) > 0 participate.
        Exchangers are excluded: their JSON self_vent_rate is actually their
        AdjacentTransferRate (stat 9), used only for neighbor heat exchange.
        Inlets/Outlets use ReactorTransferRate (stat 10) and don't self-vent.
        """
        total_vented = 0.0
        for comp in self.components:
            if comp.stats.self_vent_rate <= 0:
                continue
            # RE: In the binary, only Vents have SelfVentRate > 0.
            # Exchangers/Inlets/Outlets store their rate in different stat slots.
            if comp.stats.type_of_component in ("Exchanger", "Inlet", "Outlet"):
                continue
            vent_bonus = self._stat_mult(comp.stats.component_type_id, StatCategory.SELF_VENT_RATE)
            rate = comp.stats.self_vent_rate * vent_bonus * self.self_vent_mult
            vented = min(comp.heat, rate)
            comp.heat -= vented
            total_vented += vented
            # Clamp small residuals
            if comp.heat < 0.01:
                comp.heat = 0.0
        self.store.total_heat_dissipated += total_vented
        self.store.heat_dissipated_this_game += total_vented
        return total_vented

    def _exchange_with_hull(self) -> None:
        """RE: fn 10438 — Inlets pull neighbor heat into reactor; outlets push reactor heat to neighbors."""
        if self.grid is None:
            return

        # --- Pass 1: Pre-calculate total outlet capacity for distribution ratio ---
        total_outlet_capacity = 0.0
        for comp in self.components:
            if comp.stats.reactor_vent_rate <= 0 or comp.depleted:
                continue
            if comp.stats.type_of_component != "Outlet":
                continue
            rate = comp.stats.reactor_vent_rate * self._stat_mult(
                comp.stats.component_type_id, StatCategory.REACTOR_TRANSFER_RATE
            ) * self.heat_exchange_mult
            # Count neighbors with heat_capacity > 0 (0xC9 HeatAbsorb flag)
            absorber_count = 0
            for nx, ny, nz in self.grid.neighbors(comp.grid_x, comp.grid_y, 0):
                ncomp = self.grid.get(nx, ny, nz)
                if ncomp is not None and ncomp.stats.heat_capacity > 0:
                    absorber_count += 1
            if absorber_count > 0:
                total_outlet_capacity += absorber_count * rate

        # Distribution ratio: how much of reactor heat can outlets move this tick
        if total_outlet_capacity > 0:
            dist_ratio = max(0.0, min(1.0, self.reactor_heat / total_outlet_capacity))
        else:
            dist_ratio = 0.0

        # --- Pass 2: Process inlets and outlets ---
        for comp in self.components:
            if comp.stats.reactor_vent_rate <= 0 or comp.depleted:
                continue
            rate = comp.stats.reactor_vent_rate * self._stat_mult(
                comp.stats.component_type_id, StatCategory.REACTOR_TRANSFER_RATE
            ) * self.heat_exchange_mult

            if comp.stats.type_of_component == "Inlet":
                # Inlet: pull heat from neighbors into reactor hull
                for nx, ny, nz in self.grid.neighbors(comp.grid_x, comp.grid_y, 0):
                    ncomp = self.grid.get(nx, ny, nz)
                    if ncomp is None:
                        continue
                    # Skip CantLoseHeat flag (0xCA): Reflectors + ExtremeCoolant
                    if ncomp.stats.cant_lose_heat:
                        continue
                    transfer = min(rate, ncomp.heat)
                    if transfer > 0:
                        ncomp.heat -= transfer
                        self.reactor_heat += transfer

            elif comp.stats.type_of_component == "Outlet":
                # Outlet: push reactor heat into neighboring heat absorbers
                if total_outlet_capacity <= 0:
                    continue
                absorber_count = 0
                for nx, ny, nz in self.grid.neighbors(comp.grid_x, comp.grid_y, 0):
                    ncomp = self.grid.get(nx, ny, nz)
                    if ncomp is not None and ncomp.stats.heat_capacity > 0:
                        absorber_count += 1
                if absorber_count == 0:
                    continue
                transfer_total = min(dist_ratio * rate * absorber_count, self.reactor_heat)
                transfer_per = transfer_total / absorber_count
                for nx, ny, nz in self.grid.neighbors(comp.grid_x, comp.grid_y, 0):
                    ncomp = self.grid.get(nx, ny, nz)
                    if ncomp is not None and ncomp.stats.heat_capacity > 0:
                        self.reactor_heat -= transfer_per
                        ncomp.heat += transfer_per
        self.reactor_heat = max(0.0, self.reactor_heat)

    def _check_explosions(self) -> None:
        """RE: fn 10429 — heat overflow damage + component/reactor explosions.

        1. When reactor_heat > max_reactor_heat, each component with heat_capacity > 0
           receives (reactor_heat - max_reactor_heat) * 0.05 added to its heat per tick.
        2. When component.heat >= component.stats.heat_capacity, the component explodes.
        3. When reactor_heat >= 2 * max_reactor_heat, ALL components are destroyed (meltdown).
        """
        if self.grid is None:
            return

        max_heat = self.max_reactor_heat
        is_meltdown = (max_heat > 0 and self.reactor_heat >= 2.0 * max_heat)

        # Heat overflow: when reactor heat > capacity, distribute 5% of excess to components
        if self.reactor_heat > max_heat and max_heat > 0:
            overflow = self.reactor_heat - max_heat
            for comp in self.components:
                if comp.stats.heat_capacity > 0 and not comp.depleted:
                    comp.heat += overflow * 0.05

        # Build destruction list
        to_destroy = []
        for comp in self.components:
            if comp.depleted:
                continue
            if comp.stats.heat_capacity > 0:
                eff_cap = comp.stats.heat_capacity * self._stat_mult(
                    comp.stats.component_type_id, StatCategory.HEAT_CAPACITY)
                if is_meltdown or comp.heat >= eff_cap:
                    to_destroy.append(comp)
            else:
                # No heat capacity: only destroyed on meltdown
                if is_meltdown:
                    to_destroy.append(comp)

        # Execute destruction — spawn explosion effect at each destroyed component's position
        for comp in to_destroy:
            if self.grid is not None:
                self.explosions.append(ExplosionEffect(grid_x=comp.grid_x, grid_y=comp.grid_y))
            self.grid.set(comp.grid_x, comp.grid_y, comp.grid_z, None)
            try:
                self.components.remove(comp)
            except ValueError:
                pass

        if to_destroy:
            self._pulses_dirty = True
            self.recompute_max_capacities()

        # Clamp small heat residuals on surviving components
        for comp in self.components:
            if comp.heat < 0.01:
                comp.heat = 0.0

    def update_explosions(self, dt: float) -> None:
        """Advance explosion animations. Runs even when paused (cosmetic only)."""
        for effect in self.explosions:
            effect.time += dt
            effect.frame = int(effect.time * effect.fps)
        # Remove finished animations (12 frames total)
        self.explosions = [e for e in self.explosions if e.frame < 12]

    # ── Public interface ──────────────────────────────────────────

    def can_scrounge(self) -> bool:
        """RE: unnamed_function_10508 line 394595-394611.
        Scrounge requires: reactor has 0 components AND money + power < 10.0.
        unnamed_function_10448 counts non-empty tiles; must return 0."""
        return len(self.components) == 0 and (self.store.money + self.stored_power) < 10.0

    def sell_or_scrounge(self) -> float:
        """RE: unnamed_function_10508 — Sell All Power button handler.
        If money+power < 10: scrounge +1$.
        Else: transfer all stored_power to money, zero reactor power."""
        if self.can_scrounge():
            self.store.add_money(1.0)
            return 1.0
        gained = self.stored_power
        self.stored_power = 0.0
        self.store.power = 0.0
        self.store.add_money(gained)
        return gained

    def vent_heat(self) -> float:
        """RE: unnamed_function_10506 — manual vent button.
        Vents min(reactor_heat, manual_vent_amount) from reactor hull."""
        vented = min(self.reactor_heat, self.manual_vent_amount)
        self.reactor_heat = max(0.0, self.reactor_heat - vented)
        self.store.heat = self.reactor_heat
        return vented

    def auto_vent_rate_per_tick(self) -> float:
        """RE: unnamed_function_10415 — vent rate shown on button label.
        = maxHeat * autoVentUpgradeMult * 0.01 + maxHeat * 0.0001
        With no upgrades (autoVentMult=1.0→bonus-1=0): rate = maxHeat * 0.0001"""
        auto_vent_bonus = self.upgrade_manager.get_upgrade_stat_bonus(1, StatCategory.AUTO_VENT_RATE)
        return self.max_reactor_heat * (auto_vent_bonus - 1.0) * 0.01 + self.max_reactor_heat * 0.0001

    def vent_dissipation_capacity_per_tick(self) -> float:
        """Maximum per-tick heat dissipation to air from vent-like components."""
        total = 0.0
        for comp in self.components:
            if comp.depleted or comp.stats.self_vent_rate <= 0:
                continue
            if comp.stats.type_of_component in ("Exchanger", "Inlet", "Outlet"):
                continue
            vent_bonus = self._stat_mult(comp.stats.component_type_id, StatCategory.SELF_VENT_RATE)
            total += comp.stats.self_vent_rate * vent_bonus * self.self_vent_mult
        return total

    def vent_dissipation_capacity_for(self, comp: ReactorComponent) -> float:
        """Per-tick heat dissipation for a single placed Vent component."""
        if comp.depleted or comp.stats.self_vent_rate <= 0:
            return 0.0
        if comp.stats.type_of_component != "Vent":
            return 0.0
        vent_bonus = self._stat_mult(comp.stats.component_type_id, StatCategory.SELF_VENT_RATE)
        return comp.stats.self_vent_rate * vent_bonus * self.self_vent_mult

    def active_dissipation_capacity_per_tick(self) -> float:
        """Sum vent capacity for vents adjacent to at least one outlet."""
        if self.grid is None:
            return 0.0
        seen_vent_ids: set[int] = set()
        total = 0.0
        for comp in self.components:
            if comp.depleted or comp.stats.type_of_component != "Outlet":
                continue
            for nx, ny, nz in self.grid.neighbors(comp.grid_x, comp.grid_y, 0):
                ncomp = self.grid.get(nx, ny, nz)
                if ncomp is None:
                    continue
                vid = id(ncomp)
                if vid in seen_vent_ids:
                    continue
                cap = self.vent_dissipation_capacity_for(ncomp)
                if cap > 0:
                    seen_vent_ids.add(vid)
                    total += cap
        return total

    def outlet_transfer_capacity_per_tick(self) -> float:
        """Maximum per-tick reactor->component transfer capacity from outlets."""
        total = 0.0
        for comp in self.components:
            total += self.outlet_transfer_capacity_for(comp)
        return total

    def outlet_transfer_capacity_for(self, comp: ReactorComponent) -> float:
        """Per-tick transfer capacity contributed by one outlet component."""
        if self.grid is None:
            return 0.0
        rate = self.outlet_transfer_rate_for(comp)
        if rate <= 0.0:
            return 0.0
        absorber_count = 0
        for nx, ny, nz in self.grid.neighbors(comp.grid_x, comp.grid_y, 0):
            ncomp = self.grid.get(nx, ny, nz)
            if ncomp is not None and ncomp.stats.heat_capacity > 0:
                absorber_count += 1
        return absorber_count * rate

    def outlet_transfer_rate_for(self, comp: ReactorComponent) -> float:
        """Single-output transfer rate for one outlet (before neighbor fan-out)."""
        if comp.depleted or comp.stats.reactor_vent_rate <= 0:
            return 0.0
        if comp.stats.type_of_component != "Outlet":
            return 0.0
        return comp.stats.reactor_vent_rate * self._stat_mult(
            comp.stats.component_type_id, StatCategory.REACTOR_TRANSFER_RATE
        ) * self.heat_exchange_mult

    def max_adjacent_vent_capacity_for(self, comp: ReactorComponent) -> float:
        """Largest per-tick dissipation among this outlet's adjacent vents."""
        if self.grid is None:
            return 0.0
        if comp.depleted or comp.stats.type_of_component != "Outlet":
            return 0.0
        max_cap = 0.0
        for nx, ny, nz in self.grid.neighbors(comp.grid_x, comp.grid_y, 0):
            ncomp = self.grid.get(nx, ny, nz)
            if ncomp is None:
                continue
            vent_cap = self.vent_dissipation_capacity_for(ncomp)
            if vent_cap > max_cap:
                max_cap = vent_cap
        return max_cap

    def is_outlet_bottleneck(self, comp: ReactorComponent) -> bool:
        """True when an adjacent vent can dissipate more than this outlet outputs."""
        outlet_rate = self.outlet_transfer_rate_for(comp)
        if outlet_rate <= 0.0:
            return False
        return self.max_adjacent_vent_capacity_for(comp) > outlet_rate + 1e-6

    def refresh_live_preview(self) -> None:
        """Refresh derived stats/deltas without advancing simulation time.

        This keeps UI values responsive when the reactor is paused or when a panel
        is open and ticks are suppressed.
        """
        self.reactor_heat = max(0.0, self.reactor_heat)
        self.upgrade_manager.prepare_multipliers(self)
        if self._pulses_dirty:
            self._distribute_pulses()
            self._pulses_dirty = False

        power_gen, heat_gen = self._estimate_generation_preview()
        self.last_power_change = power_gen

        projected_heat = self.reactor_heat + heat_gen
        auto_vent = self.auto_vent_rate_per_tick()
        if projected_heat > self.max_reactor_heat and heat_gen <= 0:
            overheat_decay = (projected_heat - self.max_reactor_heat) * 0.05
            auto_vent = max(auto_vent, overheat_decay)
        self.last_heat_change = heat_gen - auto_vent

        self.preview_vent_capacity = self.vent_dissipation_capacity_per_tick()
        self.preview_outlet_capacity = self.outlet_transfer_capacity_per_tick()
        self.preview_active_dissipation = self.active_dissipation_capacity_per_tick()

    def auto_sell_rate_per_tick(self) -> float:
        """RE: unnamed_function_10417 — auto-sell rate shown on sell button.
        = (autoSellBonus - 1) * maxPower * 0.01
        With no upgrades (bonus=1.0): 0"""
        auto_sell_bonus = self.upgrade_manager.get_upgrade_stat_bonus(1, StatCategory.AUTO_SELL_RATE)
        return (auto_sell_bonus - 1.0) * self.max_reactor_power * 0.01

    def calculate_prestige_ep(self) -> int:
        """RE: unnamed_function_10380 — compute EP earned from next prestige.

        Formula: floor(4^(log10(min(totalPower, totalHeat)) - 12))
        Requires min(power, heat) >= 1e12 to earn any EP.
        Returns delta: total_calculated - highwater (already-earned EP).
        """
        value = min(self.store.total_power_produced, self.store.total_heat_dissipated)
        if value < 1e12:
            return 0
        total_ep = int(math.floor(4.0 ** (math.log10(value) - 12.0)))
        return max(0, total_ep - int(self.store.total_exotic_particles))

    def do_prestige(self) -> int:
        """RE: unnamed_function_10481 — execute prestige.

        Awards EP delta, clears grid/components/money/power/heat/per-game stats.
        Lifetime totals (total_power_produced, total_heat_dissipated) persist.
        """
        ep_gain = self.calculate_prestige_ep()

        # Update highwater mark and award EP
        new_total = int(self.store.total_exotic_particles) + ep_gain
        self.store.total_exotic_particles = float(new_total)
        self.store.exotic_particles += float(ep_gain)

        # Clear grid
        if self.grid is not None:
            for x, y, z, comp in list(self.grid.iter_cells()):
                if comp is not None:
                    self.grid.set(x, y, z, None)
        self.components.clear()

        # Zero per-game resources (lifetime totals persist)
        self.store.money = 0.0
        self.store.money_earned_this_game = 0.0
        self.store.power = 0.0
        self.store.power_produced_this_game = 0.0
        self.store.heat = 0.0
        self.store.heat_dissipated_this_game = 0.0
        self.reactor_heat = 0.0
        self.stored_power = 0.0
        self.depleted_protium_count = 0

        # Reset non-prestige upgrades
        for u in self.upgrade_manager.upgrades:
            if not u.is_prestige:
                u.level = 0

        self.selected_component_index = -1
        self.replace_mode = True  # RE: fn 10481 sets Controller+0xA1
        self._pulses_dirty = True
        self.recompute_max_capacities()

        # Prestige state machine: enter Phase C (refund window)
        self.prestige_confirming = False
        self.prestige_can_refund = True
        self.resize_grid_for_subspace()
        return ep_gain

    def refund_prestige_upgrades(self) -> None:
        """RE: fn 10493 canResetPrestigeUpgrade branch — refund all prestige upgrades.

        Binary: CurrentExoticParticles(+0x10) = TotalExoticParticles(+0x48).
        Restores current EP to lifetime total, giving back all spent EP.
        """
        for u in self.upgrade_manager.upgrades:
            if u.is_prestige:
                u.level = 0
        self.store.exotic_particles = self.store.total_exotic_particles
        self.prestige_can_refund = False
        self.recompute_max_capacities()

    def resize_grid_for_subspace(self, base_width: int = 19, base_height: int = 16) -> None:
        """Resize grid based on Subspace Expansion (upgrade 50) level.

        Each level adds +1 width and +1 height to the reactor grid.
        RE: upgrade 50 is prestige, base cost 500 EP, cost_multiplier 1000x, no max level.
        """
        if self.grid is None:
            return
        level = 0
        if self.upgrade_manager.upgrades and len(self.upgrade_manager.upgrades) > 50:
            level = self.upgrade_manager.upgrades[50].level
        new_w = base_width + level
        new_h = base_height + level
        if new_w != self.grid.width or new_h != self.grid.height:
            self.grid.resize(new_w, new_h)
            self._pulses_dirty = True

    def recompute_max_capacities(self) -> None:
        """RE: unnamed_function_10412/10413 — recompute maxHeat and maxPower.
        Base = GetStatCached(1, stat) * 1000.0 + sum of component contributions.
        Upgrade multipliers: power_cap_mult / heat_cap_mult scale the base."""
        heat_base_mult = self.upgrade_manager.get_upgrade_stat_bonus(1, StatCategory.REACTOR_HEAT_CAP_INCREASE)
        power_base_mult = self.upgrade_manager.get_upgrade_stat_bonus(1, StatCategory.REACTOR_POWER_CAP_INCREASE)
        base_heat = heat_base_mult * 1000.0
        base_power = power_base_mult * 100.0
        for comp in self.components:
            # Per-component contribution scaled by upgrade bonus for that component type
            tid = comp.stats.component_type_id
            heat_inc_bonus = self._stat_mult(tid, StatCategory.REACTOR_HEAT_CAP_INCREASE)
            power_inc_bonus = self._stat_mult(tid, StatCategory.REACTOR_POWER_CAP_INCREASE)
            base_heat += comp.stats.reactor_heat_capacity_increase * heat_inc_bonus
            base_power += comp.stats.reactor_power_capacity_increase * power_inc_bonus
        self.max_reactor_heat = base_heat * self.heat_cap_mult
        self.max_reactor_power = base_power * self.power_cap_mult

    def count_components_of_type(self, type_prefix: str) -> int:
        """Count placed components whose type_of_component or name starts with prefix."""
        count = 0
        for comp in self.components:
            if comp.depleted:
                continue
            if comp.stats.type_of_component and comp.stats.type_of_component.startswith(type_prefix):
                count += 1
            elif comp.stats.name.startswith(type_prefix):
                count += 1
        return count

    def sum_component_tiers(self, type_prefix: str) -> int:
        """RE: unnamed_function_10447 — sum Tier field for placed components of a type.

        The binary sums ComponentType.Tier (offset 0x10) for all components
        whose TypeOfComponent (offset 0x14) matches. A tier-1 component
        contributes 1, tier-2 contributes 2, etc.
        """
        total = 0
        for comp in self.components:
            if comp.depleted:
                continue
            if comp.stats.type_of_component and comp.stats.type_of_component.startswith(type_prefix):
                total += comp.stats.tier
            elif comp.stats.name.startswith(type_prefix):
                total += comp.stats.tier
        return total

    def get_component_cost(self, comp: ComponentTypeStats) -> float:
        """Return effective cost of a component after applying discount upgrades."""
        return comp.cost * self.upgrade_manager.get_component_discount()

    def get_effective_max_durability(self, comp: ReactorComponent) -> float:
        """Return upgraded max durability for display (Durability: X / Y)."""
        return comp.stats.max_durability * self._stat_mult(
            comp.stats.component_type_id, StatCategory.MAX_DURABILITY)

    def get_effective_heat_capacity(self, comp: ReactorComponent) -> float:
        """Return upgraded heat capacity for display (Heat: X / Y)."""
        return comp.stats.heat_capacity * self._stat_mult(
            comp.stats.component_type_id, StatCategory.HEAT_CAPACITY)

    @staticmethod
    def can_replace(existing: ReactorComponent, new_type: ComponentTypeStats) -> bool:
        """RE: unnamed_function_10462 — check if existing component can be replaced.

        Rules:
        1. Same ComponentType AND NOT depleted → can NOT replace
        2. Both are fuel cells (have CellData / pulses_produced > 0) → CAN replace
        3. Same TypeOfComponent → CAN replace
        4. Different TypeOfComponent → can NOT replace
        """
        existing_type = existing.stats
        # Same exact component type and not depleted → cannot replace
        if existing_type.name == new_type.name and not existing.depleted and not existing.heat:
            return False
        # Both are fuel cells (have CellData) → can replace
        if existing_type.pulses_produced > 0 and new_type.pulses_produced > 0:
            return True
        # Compare TypeOfComponent
        if existing_type.type_of_component and new_type.type_of_component:
            return existing_type.type_of_component == new_type.type_of_component
        # Fallback: if type_of_component not set, infer from name prefix
        return False

    @staticmethod
    def sell_value(comp: ReactorComponent) -> float:
        """Calculate sell value with quadratic degradation (unnamed_function_10456).

        Formula: cost × (1 - heat/maxHeat)² × (durability/maxDurability)²
        Fuel cells (have pulses_produced > 0) always return $0.
        """
        stats = comp.stats
        value = stats.cost

        # Heat penalty: quadratic degradation based on stored heat
        if stats.heat_capacity > 0.0:
            heat_ratio = 1.0 - min(1.0, comp.heat / stats.heat_capacity)
            value *= heat_ratio * heat_ratio

        # Durability penalty: quadratic degradation based on remaining durability
        if stats.max_durability > 0.0:
            dur_ratio = max(0.0, comp.durability / stats.max_durability)
            value *= dur_ratio * dur_ratio

        # Fuel cells refund $0 (CellData check in original)
        if stats.pulses_produced > 0:
            value = 0.0

        return value

    def reset_game(self) -> None:
        """Reset all game state (grid, money, power, heat, stats, upgrades).

        RE: fn 10330 resets ALL upgrades (including prestige) on hard reset.
        """
        if self.grid is not None:
            for x, y, z, comp in list(self.grid.iter_cells()):
                if comp is not None:
                    self.grid.set(x, y, z, None)
        self.components.clear()
        self.store.money = 0.0
        self.store.total_money = 0.0
        self.store.money_earned_this_game = 0.0
        self.store.power = 0.0
        self.store.total_power_produced = 0.0
        self.store.power_produced_this_game = 0.0
        self.store.heat = 0.0
        self.store.total_heat_dissipated = 0.0
        self.store.heat_dissipated_this_game = 0.0
        self.store.exotic_particles = 0.0
        self.store.total_exotic_particles = 0.0
        self.reactor_heat = 0.0
        self.stored_power = 0.0
        self.depleted_protium_count = 0
        self.total_ticks = 0
        self.selected_component_index = -1
        self.view_mode = "reactor"
        self._pulses_dirty = True
        # Reset ALL upgrades (RE: fn 10330)
        for u in self.upgrade_manager.upgrades:
            u.level = 0
        # Clear prestige state flags
        self.prestige_confirming = False
        self.prestige_can_refund = False
        self.reset_confirm_timer = 0.0
        self.recompute_max_capacities()
        self.resize_grid_for_subspace()

    def place_component(self, x: int, y: int, component: ReactorComponent, z: int = 0, recompute_capacities=True) -> bool:
        if self.grid is None or not self.grid.in_bounds(x, y, z):
            return False
        if self.grid.get(x, y, z) is not None:
            return False
        component.grid_x = x
        component.grid_y = y
        component.grid_z = z
        # Set initial durability using upgraded max (RE: placement uses current upgrade level)
        if component.stats.max_durability > 0:
            dur_mult = self._stat_mult(component.stats.component_type_id, StatCategory.MAX_DURABILITY)
            component.durability = component.stats.max_durability * dur_mult
        self.grid.set(x, y, z, component)
        self.components.append(component)
        self._pulses_dirty = True
        if recompute_capacities:
            self.recompute_max_capacities()
        return True

    def remove_component(self, x: int, y: int, z: int = 0, recompute_capacities=True) -> Optional[ReactorComponent]:
        if self.grid is None or not self.grid.in_bounds(x, y, z):
            return None
        existing = self.grid.get(x, y, z)
        if existing is None:
            return None
        self.grid.set(x, y, z, None)
        try:
            self.components.remove(existing)
        except ValueError:
            pass
        self._pulses_dirty = True
        if recompute_capacities:
            self.recompute_max_capacities()
        return existing

    def selected_component(self) -> Optional[ComponentTypeStats]:
        shop = self.shop_components_for_page()
        if not shop or self.selected_component_index < 0:
            return None
        idx = min(self.selected_component_index, len(shop) - 1)
        return shop[idx]

    def shop_components_for_page(self) -> List[ComponentTypeStats]:
        if not self.shop_components:
            return []
        page = self.shop_page % 4
        if self.shop_page != page:
            self.shop_page = page
        if self.shop_page_locked(page):
            for fallback in range(4):
                if not self.shop_page_locked(fallback):
                    if self.shop_page != fallback:
                        self.shop_page = fallback
                        self.selected_component_index = -1
                    page = fallback
                    break
        components = [comp for comp in self.shop_components if comp.shop_page == page]
        if not components:
            components = [comp for comp in self.shop_components if _component_shop_page(comp.name) == page]
        # RE: fn 10393/10395 — hide components whose required_upgrade hasn't been purchased
        mgr = self.upgrade_manager
        visible = []
        for comp in components:
            if comp.required_upgrade >= 0:
                if not mgr.upgrades or comp.required_upgrade >= len(mgr.upgrades):
                    continue
                if mgr.upgrades[comp.required_upgrade].level == 0:
                    continue
            visible.append(comp)
        return sorted(
            visible,
            key=lambda comp: (comp.shop_row, comp.shop_col, comp.shop_order, comp.name),
        )

    def shop_page_locked(self, page: int) -> bool:
        if page <= 1:
            return False
        if page == 2:
            # Experimental tab unlocked by Research Grant (upgrade 32)
            mgr = self.upgrade_manager
            if mgr.upgrades and len(mgr.upgrades) > 32:
                return mgr.upgrades[32].level == 0
            return True
        return self.prestige_level < 2


def demo_simulation() -> Simulation:
    from game.catalog import load_component_catalog

    sim = Simulation()
    sim.upgrade_manager.load()
    sim.shop_components = load_component_catalog()
    if not sim.shop_components:
        sim.shop_components = [
            ComponentTypeStats(
                name="Fuel1-1",
                sprite_name="Fuel1-1.png",
                energy_per_pulse=1.0,
                heat_per_pulse=1.0,
                pulses_produced=1.0,
                max_durability=15.0,
                heat_capacity=0.0,
                self_vent_rate=0.0,
                reactor_vent_rate=0.0,
            )
        ]
    sim.grid = Grid(width=12, height=8, tile_size=16, origin_x=16, origin_y=96)
    return sim
