from __future__ import annotations

from pathlib import Path
import csv
import json
import re
from typing import Dict, Iterable, List, Optional, Tuple

from game.types import ComponentTypeStats


COMPONENT_PREFIXES = (
    "Fuel",
    "Coolant",
    "Capacitor",
    "Vent",
    "Plate",
    "Reflector",
    "Inlet",
    "Outlet",
    "Exchanger",
    "Clock",
    "GenericHeat",
    "GenericPower",
    "GenericInfinity",
)


FUEL_ELEMENTS = [
    "Uranium",
    "Plutonium",
    "Thorium",
    "Seaborgium",
    "Dolorium",
    "Nefastium",
    "Protium",
    "Monastium",
    "Kymium",
    "Discurrium",
    "Stavrium",
]


def _fuel_index(name: str) -> Optional[int]:
    match = re.match(r"Fuel(\d+)", name)
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None

TIER_PREFIXES = {
    "Basic": 1,
    "Advanced": 2,
    "Super": 3,
    "Wondrous": 4,
    "Ultimate": 5,
    "Extreme": 6,
}

SHOP_PAGE_POWER = 0
SHOP_PAGE_HEAT = 1
SHOP_PAGE_EXPERIMENTAL = 2
SHOP_PAGE_ARCANE = 3

CORE_COLUMNS = {1: 0, 2: 2, 4: 4}
HEAT_ROWS = {
    "Vent": 0,
    "Exchanger": 1,
    "Inlet": 2,
    "Outlet": 3,
    "Coolant": 4,
    "Plate": 5,
}
EXPERIMENTAL_COLUMNS = {
    "Capacitor": 2,
}

# RE: ComponentType+0xCC/+0xD0 (Nullable<int>) — required upgrade index for shop visibility.
# Components with a required upgrade are hidden until that upgrade is purchased (level > 0).
# Fuel 1-6, Vent/Exchanger/Inlet/Outlet/Coolant 1-5, Reflector 1-5, Plate 1-5, Capacitor 1-5: always visible.
_REQUIRED_UPGRADE: dict[str, int] = {
    "Fuel7": 33,   # Protium Research
    "Fuel8": 46,   # Monastium Research
    "Fuel9": 47,   # Kymium Research
    "Fuel10": 48,  # Discurrium Research
    "Fuel11": 49,  # Stavrium Research
    "Capacitor6": 41,  # Experimental Capacitance Research
    "Coolant6": 42,    # Vortex Cooling
}


# RE: TypeOfComponent integer IDs used in upgrade bonus lookups.
# Fuel elements 1-6 → IDs 2-7; elements 7-11 → IDs 16-20.
# Non-fuel: Vent=8, Exchanger=9, Coolant=10, Reflector=11, Plating=12,
# Capacitor=13, Inlet=14, Outlet=15.
_COMPONENT_CATEGORY_IDS = {
    "Vent": 8, "Exchanger": 9, "Coolant": 10, "Reflector": 11,
    "Plate": 12, "Capacitor": 13, "Inlet": 14, "Outlet": 15,
}


def _compute_component_type_id(name: str) -> int:
    """Compute the integer component_type_id from a sprite/component name."""
    fuel = _fuel_index(name)
    if fuel is not None:
        # Fuel 1-6 → IDs 2-7; Fuel 7-11 → IDs 16-20
        if 1 <= fuel <= 6:
            return fuel + 1
        elif 7 <= fuel <= 11:
            return fuel + 9
        return 0
    for prefix, type_id in _COMPONENT_CATEGORY_IDS.items():
        if name.startswith(prefix):
            return type_id
    return 0


def _get_required_upgrade(name: str) -> int:
    """Return the upgrade index required to unlock a component, or -1 if always visible.

    RE: ComponentType+0xCC/+0xD0 — Fuel variants (e.g. Fuel7-1, Fuel7-2, Fuel7-4)
    all share the same required upgrade as their base fuel index.
    """
    for prefix, upgrade_idx in _REQUIRED_UPGRADE.items():
        if name.startswith(prefix):
            return upgrade_idx
    return -1


def _fallback_shop_page(name: str) -> int:
    if name.startswith("Fuel"):
        fuel = _fuel_index(name)
        if fuel is None:
            return SHOP_PAGE_POWER
        return SHOP_PAGE_POWER if fuel <= 6 else SHOP_PAGE_EXPERIMENTAL
    if name.startswith("Capacitor") or name.startswith("GenericPower"):
        return SHOP_PAGE_POWER
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
        return SHOP_PAGE_HEAT
    if name.startswith(("Clock", "GenericInfinity")):
        return SHOP_PAGE_EXPERIMENTAL
    return SHOP_PAGE_ARCANE


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _mono_index_path() -> Path:
    return _repo_root() / "decompilation" / "recovered" / "recovered_mono" / "monobehaviour_index.csv"


def _sprite_exists(name: str) -> bool:
    sprites_dir = _repo_root() / "decompilation" / "recovered" / "recovered_assets" / "sprites"
    if not sprites_dir.exists():
        return True  # Web build: sprites are JS Image objects, not on VFS
    return (sprites_dir / f"{name}.png").exists()


def _is_shop_sprite(name: str) -> bool:
    if name.endswith("-e"):
        return False
    if name.endswith("UP"):
        return False
    if name in ("GenericHeat", "GenericPower"):
        return False
    return True


def _filter_component_entries(entries: Iterable[Tuple[int, str]]) -> List[str]:
    deduped: Dict[str, int] = {}
    for path_id, name in entries:
        if not name or not name.startswith(COMPONENT_PREFIXES):
            continue
        if not _is_shop_sprite(name):
            continue
        if not _sprite_exists(name):
            continue
        if name not in deduped or path_id < deduped[name]:
            deduped[name] = path_id
    ordered = sorted(deduped.items(), key=lambda item: item[1])
    return [name for name, _ in ordered]


def _metadata_types_path() -> Path:
    return _repo_root() / "decompilation" / "recovered" / "recovered_metadata" / "assembly_csharp_types_v2.json"


def _component_costs_path() -> Path:
    return _repo_root() / "decompilation" / "recovered" / "recovered_analysis" / "component_costs.json"


def _component_texts_path() -> Path:
    return _repo_root() / "decompilation" / "recovered" / "recovered_analysis" / "component_texts.json"


def _stringliteral_path() -> Path:
    return _repo_root() / "decompilation" / "recovered" / "il2cppdumper" / "stringliteral.json"


def _stringliteral_values() -> List[str]:
    path = _stringliteral_path()
    if not path.exists():
        # Web build: use pre-extracted subset bundled alongside catalog
        web_path = Path(__file__).resolve().parent / "stringliterals_web.json"
        if web_path.exists():
            try:
                raw = json.loads(web_path.read_text(encoding="utf-8"))
                return [v for v in raw if isinstance(v, str)]
            except (OSError, json.JSONDecodeError):
                return []
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    values: List[str] = []
    if isinstance(raw, list):
        for entry in raw:
            value = entry.get("value") if isinstance(entry, dict) else None
            if isinstance(value, str):
                values.append(value)
    return values


def _find_literal(values: List[str], needle: str) -> Optional[str]:
    for value in values:
        if needle in value:
            return value.strip()
    return None


def _parse_literal_values() -> Dict[str, str]:
    values = _stringliteral_values()
    labels: Dict[str, str] = {}

    def _set(key: str, needle: str) -> None:
        found = _find_literal(values, needle)
        if found:
            labels[key] = found

    _set("cost", "Cost: ")
    _set("cost_unknown", "Cost: ???")
    _set("heat", "Heat: ")
    _set("durability", "Durability: ")
    _set("heat_per_tick", "Heat Per Tick: ")
    _set("power_per_tick", "Power Per Tick: ")
    _set("sells_for", "Sells for: ")
    _set("depleted_prefix", "Depleted ")
    _set("depleted_body", "This cell has run out of fuel and is now inert.")
    return labels


def _component_type_to_stats(comp_type: dict) -> Optional[ComponentTypeStats]:
    if not isinstance(comp_type, dict):
        return None
    raw_name = comp_type.get("Name")
    if not isinstance(raw_name, str) or not raw_name.strip():
        return None
    raw_name = raw_name.strip()
    sprite = comp_type.get("Sprite") or ""
    sprite_value = sprite.strip() if isinstance(sprite, str) else ""

    name = raw_name
    if sprite_value and _is_shop_sprite(sprite_value):
        name = sprite_value
    if not _is_shop_sprite(name):
        return None

    if not _sprite_exists(name):
        return None

    sprite_name = name
    if not sprite_name.endswith(".png"):
        sprite_name = f"{sprite_name}.png"

    display_name = raw_name if raw_name != name else ""
    description = comp_type.get("Description")
    description = description.strip() if isinstance(description, str) else ""

    cost = float(comp_type.get("Cost", 0.0) or 0.0)
    durability = float(comp_type.get("MaxDurability", 0.0) or 0.0)
    heat_capacity = float(comp_type.get("HeatCapacity", 0.0) or 0.0)
    reactor_heat = float(comp_type.get("ReactorHeatCapacityIncrease", 0.0) or 0.0)
    reactor_power = float(comp_type.get("ReactorPowerCapacityIncrease", 0.0) or 0.0)
    # JSON field names are swapped for Capacitor and Plating only:
    #   Capacitors have ReactorHeatCapacityIncrease but actually increase power.
    #   Plating has ReactorPowerCapacityIncrease but actually increases heat.
    # Coolant's ReactorHeatCapacityIncrease is correctly named (it does increase heat).
    if name.startswith("Capacitor") or name.startswith("Plate"):
        reactor_heat, reactor_power = reactor_power, reactor_heat
    reflects = float(comp_type.get("ReflectsPulses", 0.0) or 0.0)
    meta = comp_type.get("_meta", {})
    type_of_component = comp_type.get("type_of_component") or comp_type.get("TypeOfComponent") or ""
    if not type_of_component:
        type_of_component = meta.get("type_of_component", "")
    if isinstance(type_of_component, str):
        type_of_component = type_of_component.strip()
    else:
        type_of_component = ""

    fuel = comp_type.get("CellData") or {}
    heat = comp_type.get("HeatData") or {}

    energy_per_pulse = float(fuel.get("EnergyPerPulse", 0.0) or 0.0)
    heat_per_pulse = float(fuel.get("HeatPerPulse", 0.0) or 0.0)
    pulses_per_core = float(fuel.get("PulsesPerCore", 0.0) or 0.0)
    cores = int(fuel.get("NumberOfCores", 0) or 0)
    if cores < 1:
        cores = 1
    pulses_produced = pulses_per_core * cores
    # Cell dimensions: 1-core=1x1, 2-core=2x1, 4-core=2x2
    cell_width = 1 if cores <= 1 else 2
    cell_height = 1 if cores <= 2 else 2

    self_vent_rate = float(heat.get("SelfVentRate", 0.0) or 0.0)
    reactor_vent_rate = float(heat.get("ReactorVentRate", 0.0) or 0.0)
    neighbor_affects = bool(heat.get("NeighborAffects")) if heat else False
    reflector_bonus = float(heat.get("ReflectorBonus", 0.0) or 0.0)

    return ComponentTypeStats(
        name=name,
        sprite_name=sprite_name,
        energy_per_pulse=energy_per_pulse,
        heat_per_pulse=heat_per_pulse,
        pulses_produced=pulses_produced,
        max_durability=durability,
        heat_capacity=heat_capacity,
        cost=cost,
        display_name=display_name,
        description=description,
        self_vent_rate=self_vent_rate,
        reactor_vent_rate=reactor_vent_rate,
        neighbor_affects=neighbor_affects,
        reactor_heat_capacity_increase=reactor_heat,
        reactor_power_capacity_increase=reactor_power,
        reflects_pulses=reflects,
        reflector_bonus_pct=reflector_bonus,
        type_of_component=type_of_component,
        number_of_cores=cores,
        cell_width=cell_width,
        cell_height=cell_height,
        stats_known=True,
        component_type_id=_compute_component_type_id(name),
        tier=meta.get("tier", 0) if isinstance(meta, dict) else 0,
        required_upgrade=_get_required_upgrade(name),
    )


def _component_text_templates() -> Dict[str, str]:
    values = _stringliteral_values()
    templates: Dict[str, str] = {}
    def _set(key: str, needle: str) -> None:
        found = _find_literal(values, needle)
        if found:
            templates[key] = found
    _set("fuel_base", "power production. Produces {2} power and {3} heat per pulse")
    _set("fuel_large_a", "cells, but only takes up a single tile. Produces {12} power")
    _set("fuel_large_b", "cells, but only takes up a single tile. Produces {14} power")
    _set("vent", "Lowers the heat of itself by {5} per tick")
    _set("exchanger", "Attempts to balance the heat between itself and adjacent components")
    _set("inlet", "takes {9} out of each adjacent component and puts it directly into the reactor")
    _set("outlet", "takes {9} out of the reactor and puts it into that component")
    _set("coolant", "Stores a large amount of heat before melting")
    _set("reflector", "Bounces neutrons back at adjacent fuel cells")
    _set("plating", "Increases the maximum heat of the reactor by {10}")
    _set("capacitor", "Increases the maximum power of the reactor by {11}")
    return templates


def _component_title_templates() -> Dict[str, str]:
    values = _stringliteral_values()
    titles: Dict[str, str] = {}
    def _set(key: str, needle: str) -> None:
        found = _find_literal(values, needle)
        if found:
            titles[key] = found
    _set("vent", "Heat Vent")
    _set("exchanger", "Heat Exchanger")
    _set("inlet", "Heat Inlet")
    _set("outlet", "Heat Outlet")
    _set("coolant", "Coolant Cell")
    _set("reflector", "Neutron Reflector")
    _set("plating", "Reactor Plating")
    _set("capacitor", "Capacitor")
    return titles


def _component_costs_from_json() -> Dict[str, float]:
    path = _component_costs_path()
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    costs: Dict[str, float] = {}
    if isinstance(raw, dict):
        for name, value in raw.items():
            try:
                costs[str(name)] = float(value)
            except (TypeError, ValueError):
                continue
    return costs


def _component_texts_from_json() -> Dict[str, Dict[str, str]]:
    path = _component_texts_path()
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    texts: Dict[str, Dict[str, str]] = {}
    if isinstance(raw, dict):
        for name, value in raw.items():
            if isinstance(value, dict):
                title = value.get("title")
                description = value.get("description")
            else:
                title = None
                description = None
            entry: Dict[str, str] = {}
            if isinstance(title, str) and title.strip():
                entry["title"] = title.strip()
            if isinstance(description, str) and description.strip():
                entry["description"] = description.strip()
            if entry:
                texts[str(name)] = entry
    return texts


def _pretty_component_name(name: str) -> str:
    name = re.sub(r"([a-z])([A-Z])", r"\1 \2", name)
    name = re.sub(r"([A-Za-z])(\d)", r"\1 \2", name)
    return name.replace("Generic ", "Generic ").strip()


def _fuel_display_name(name: str) -> str:
    idx = _fuel_index(name)
    if idx is None or idx < 1 or idx > len(FUEL_ELEMENTS):
        return _pretty_component_name(name)
    element = FUEL_ELEMENTS[idx - 1]
    if "-4" in name:
        return f"Quad {element} Cell"
    elif "-2" in name:
        return f"Double {element} Cell"
    return f"{element} Cell"


def _component_types_fields() -> List[str]:
    path = _metadata_types_path()
    if not path.exists():
        return []
    with path.open() as handle:
        data = json.load(handle)
    types = data.get("types", [])
    for t in types:
        if t.get("name") == "ComponentTypes":
            return [f.get("name") for f in t.get("fields", []) if f.get("name") and f.get("name") != "instance"]
    return []


def _map_component_field(field: str) -> Optional[str]:
    base = field
    variant = "single"
    if field.endswith("Double"):
        base = field[: -len("Double")]
        variant = "double"
    elif field.endswith("Quad"):
        base = field[: -len("Quad")]
        variant = "quad"

    if base in FUEL_ELEMENTS:
        idx = FUEL_ELEMENTS.index(base) + 1
        size = {"single": "1", "double": "2", "quad": "4"}[variant]
        return f"Fuel{idx}-{size}"

    for prefix, tier in TIER_PREFIXES.items():
        if base.startswith(prefix):
            suffix = base[len(prefix) :]
            if suffix == "Plating":
                suffix = "Plate"
            if suffix in ("Vent", "Exchanger", "Inlet", "Outlet", "Coolant", "Reflector", "Capacitor", "Plate"):
                return f"{suffix}{tier}"
    return None


def _component_names_from_metadata() -> List[str]:
    fields = _component_types_fields()
    if not fields:
        return []
    ordered: List[str] = []
    for field in fields:
        mapped = _map_component_field(field)
        if not mapped:
            continue
        if not _is_shop_sprite(mapped):
            continue
        if not _sprite_exists(mapped):
            continue
        ordered.append(mapped)
    # Include any leftover component sprites that match prefixes (e.g., Clock) in name order.
    sprites_dir = _repo_root() / "decompilation" / "recovered" / "recovered_assets" / "sprites"
    if sprites_dir.exists():
        extras = []
        for sprite in sprites_dir.glob("*.png"):
            name = sprite.stem
            if name in ordered:
                continue
            if not name.startswith(COMPONENT_PREFIXES):
                continue
            if not _is_shop_sprite(name):
                continue
            extras.append(name)
        ordered.extend(sorted(extras))
    return ordered


def _parse_fuel_layout(name: str) -> Optional[Tuple[int, int, int]]:
    match = re.match(r"Fuel(\d+)-(\d+)$", name)
    if not match:
        return None
    try:
        element_idx = int(match.group(1))
        cores = int(match.group(2))
    except ValueError:
        return None
    if element_idx <= 6:
        page = SHOP_PAGE_POWER
        row = element_idx - 1
    else:
        # Fuel 7-11 (Protium through Stavrium) go on the Experimental tab
        page = SHOP_PAGE_EXPERIMENTAL
        row = element_idx - 7
    col = CORE_COLUMNS.get(cores, 0)
    return page, row, col


def _parse_tiered_component(name: str) -> Optional[Tuple[str, int]]:
    match = re.match(r"([A-Za-z]+)(\d+)$", name)
    if not match:
        return None
    base = match.group(1)
    if base == "Plating":
        base = "Plate"
    try:
        tier = int(match.group(2))
    except ValueError:
        return None
    return base, tier


def _assign_shop_layout(name: str) -> Optional[Tuple[int, int, int, int]]:
    fuel_layout = _parse_fuel_layout(name)
    if fuel_layout:
        page, row, col = fuel_layout
        return page, row, col, row * 10 + col

    if name in ("Clock", "GenericInfinity"):
        row = 6
        col = 0 if name == "Clock" else 1
        return SHOP_PAGE_ARCANE, row, col, row * 10 + col

    tiered = _parse_tiered_component(name)
    if not tiered:
        return None
    base, tier = tiered

    if base == "Capacitor":
        if tier >= 6:
            # Extreme Capacitor: Experimental tab, row 8 rightmost corner
            row = 7
            col = 4
            return SHOP_PAGE_EXPERIMENTAL, row, col, row * 10 + col
        row = 7
        col = max(0, min(4, tier - 1))
        return SHOP_PAGE_POWER, row, col, row * 10 + col

    if base == "Reflector":
        row = 6
        col = max(0, min(4, tier - 1))
        return SHOP_PAGE_POWER, row, col, row * 10 + col

    if base == "Coolant" and tier >= 6:
        # Extreme Coolant goes on Experimental tab, row 8 middle (requires Vortex Cooling upgrade 42)
        row = 7
        col = 2
        return SHOP_PAGE_EXPERIMENTAL, row, col, row * 10 + col

    if base in HEAT_ROWS:
        row = HEAT_ROWS[base]
        col = max(0, min(4, tier - 1))
        return SHOP_PAGE_HEAT, row, col, row * 10 + col

    return None


def load_component_catalog() -> List[ComponentTypeStats]:
    type_stats = _load_component_types()
    literal_labels = _parse_literal_values()
    if type_stats:
        return _build_catalog_from_types(type_stats, literal_labels)
    component_costs = _component_costs_from_json()
    component_texts = _component_texts_from_json()
    text_templates = _component_text_templates()
    title_templates = _component_title_templates()
    component_names = _component_names_from_metadata()
    if not component_names:
        path = _mono_index_path()
        if not path.exists():
            return []
        entries: List[Tuple[int, str]] = []
        with path.open(newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                name = (row.get("gameobject") or "").strip()
                if not name:
                    continue
                try:
                    path_id = int((row.get("path_id") or "0").strip())
                except ValueError:
                    path_id = 0
                entries.append((path_id, name))
        component_names = _filter_component_entries(entries)
    catalog: List[ComponentTypeStats] = []
    for idx, name in enumerate(component_names):
        layout = _assign_shop_layout(name)
        shop_page = 0
        shop_row = idx // 5
        shop_col = idx % 5
        shop_order = idx
        if layout:
            shop_page, shop_row, shop_col, shop_order = layout
        cost = component_costs.get(name, 0.0)
        text_entry = component_texts.get(name, {})
        display_name = text_entry.get("title")
        description = text_entry.get("description")
        if not display_name or not description:
            base_tier = _parse_tiered_component(name)
            if name.startswith("Fuel"):
                display_name = display_name or _fuel_display_name(name)
                if "-4" in name and text_templates.get("fuel_large_b"):
                    description = description or text_templates["fuel_large_b"]
                elif "-2" in name and text_templates.get("fuel_large_a"):
                    description = description or text_templates["fuel_large_a"]
                else:
                    description = description or text_templates.get("fuel_base", "")
            elif base_tier:
                base, _tier = base_tier
                if base == "Vent":
                    display_name = display_name or title_templates.get("vent", "Heat Vent")
                    description = description or text_templates.get("vent", "")
                elif base == "Exchanger":
                    display_name = display_name or title_templates.get("exchanger", "Heat Exchanger")
                    description = description or text_templates.get("exchanger", "")
                elif base == "Inlet":
                    display_name = display_name or title_templates.get("inlet", "Heat Inlet")
                    description = description or text_templates.get("inlet", "")
                elif base == "Outlet":
                    display_name = display_name or title_templates.get("outlet", "Heat Outlet")
                    description = description or text_templates.get("outlet", "")
                elif base == "Coolant":
                    display_name = display_name or title_templates.get("coolant", "Coolant Cell")
                    description = description or text_templates.get("coolant", "")
                elif base == "Reflector":
                    display_name = display_name or title_templates.get("reflector", "Neutron Reflector")
                    description = description or text_templates.get("reflector", "")
                elif base in ("Plate", "Plating"):
                    display_name = display_name or title_templates.get("plating", "Reactor Plating")
                    description = description or text_templates.get("plating", "")
                elif base == "Capacitor":
                    display_name = display_name or title_templates.get("capacitor", "Capacitor")
                    description = description or text_templates.get("capacitor", "")
        if not display_name:
            display_name = _pretty_component_name(name)
        if description is None:
            description = ""
        catalog.append(
            ComponentTypeStats(
                name=name,
                sprite_name=f"{name}.png",
                energy_per_pulse=1.0,
                heat_per_pulse=1.0,
                pulses_produced=1.0,
                max_durability=100.0,
                heat_capacity=50.0,
                cost=cost,
                display_name=display_name,
                description=description,
                self_vent_rate=0.0,
                reactor_vent_rate=0.0,
                neighbor_affects=False,
                shop_page=shop_page,
                shop_row=shop_row,
                shop_col=shop_col,
                shop_order=shop_order,
                component_type_id=_compute_component_type_id(name),
            )
        )
    return catalog


def _component_types_path() -> Path:
    return _repo_root() / "decompilation" / "recovered" / "recovered_analysis" / "component_types.json"


def _load_component_types() -> List[ComponentTypeStats]:
    # Try local directory first (Pyodide VFS), then repo path
    local_path = Path(__file__).resolve().parent / "component_types.json"
    if local_path.exists():
        path = local_path
    else:
        path = _component_types_path()
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if isinstance(raw, dict):
        items = raw.get("items") or raw.get("components") or raw.get("types")
    else:
        items = raw
    if not isinstance(items, list):
        return []
    catalog: List[ComponentTypeStats] = []
    for entry in items:
        stats = _component_type_to_stats(entry)
        if stats is not None:
            catalog.append(stats)
    return catalog


def _build_catalog_from_types(
    catalog: List[ComponentTypeStats], labels: Dict[str, str]
) -> List[ComponentTypeStats]:
    text_templates = _component_text_templates()
    # Tier name lookup: tier number → prefix (RE: binary string literals)
    _TIER_NAMES = {1: "Basic", 2: "Advanced", 3: "Super", 4: "Wondrous", 5: "Ultimate", 6: "Extreme"}
    # Category suffixes from stringliteral.json (all have leading space in binary)
    _CATEGORY_SUFFIX = {
        "Vent": " Heat Vent", "Exchanger": " Heat Exchanger",
        "Inlet": " Heat Inlet", "Outlet": " Heat Outlet",
        "Coolant": " Coolant Cell", "Reflector": " Neutron Reflector",
        "Plate": " Reactor Plating", "Capacitor": " Capacitor",
    }
    # Map base category to text template key
    _CATEGORY_TEXT_KEY = {
        "Vent": "vent", "Exchanger": "exchanger", "Inlet": "inlet",
        "Outlet": "outlet", "Coolant": "coolant", "Reflector": "reflector",
        "Plate": "plating", "Capacitor": "capacitor",
    }
    for idx, comp in enumerate(catalog):
        layout = _assign_shop_layout(comp.name)
        if layout:
            comp.shop_page, comp.shop_row, comp.shop_col, comp.shop_order = layout
        else:
            comp.shop_page = _fallback_shop_page(comp.name)
            comp.shop_row = idx // 5
            comp.shop_col = idx % 5
            comp.shop_order = idx

        # --- Display name (always computed, not from JSON raw_name) ---
        if comp.name.startswith("Fuel"):
            # Always compute fuel names from element list (binary builds these dynamically)
            comp.display_name = _fuel_display_name(comp.name)
        else:
            base_tier = _parse_tiered_component(comp.name)
            if base_tier:
                base, tier = base_tier
                tier_prefix = _TIER_NAMES.get(tier, "")
                suffix = _CATEGORY_SUFFIX.get(base, "")
                if tier_prefix and suffix:
                    comp.display_name = tier_prefix + suffix
                else:
                    comp.display_name = _pretty_component_name(comp.name)
            elif not comp.display_name:
                comp.display_name = _pretty_component_name(comp.name)

        # --- Description ---
        if not comp.description:
            if comp.name.startswith("Fuel"):
                fuel_idx = _fuel_index(comp.name)
                element = FUEL_ELEMENTS[fuel_idx - 1] if fuel_idx and 1 <= fuel_idx <= len(FUEL_ELEMENTS) else ""
                # RE: fn 10312 — binary prefixes all fuel descriptions with "Tier-" + tier.
                # Tier = fuel_idx (Fuel1=tier 1, ..., Fuel11=tier 11, from ComponentType+0x10).
                tier_prefix = f"Tier-{fuel_idx} " if fuel_idx else ""
                if "-4" in comp.name and text_templates.get("fuel_large_b"):
                    comp.description = f"Acts as four {element.lower()} " + text_templates["fuel_large_b"]
                elif "-2" in comp.name and text_templates.get("fuel_large_a"):
                    comp.description = f"Acts as two {element.lower()} " + text_templates["fuel_large_a"]
                else:
                    comp.description = tier_prefix + text_templates.get("fuel_base", "")
                    # RE: stringliteral.json — element-specific mechanic descriptions for experimental fuels.
                    # The special description is stored at ExperimentalFuelElement+0x10 and appended
                    # to the base description at display time.
                    _EXPERIMENTAL_DESCS = {
                        7: ("After burning up completely, it releases a special form of "
                            "radiation that permanently increases the power output of "
                            "other protium cells by 1% per depleted cell."),
                        8: ("Its base power output drops by 2% for each other component "
                            "in the 7 x 7 area surrounding it."),
                        9: ("It gradually cycles between producing only heat and "
                            "producing only power."),
                        10: "Each cell produces four pulses per tick instead of the usual one.",
                        11: ("All components aligned vertically or horizontally are "
                            "considered adjacent to it."),
                    }
                    extra = _EXPERIMENTAL_DESCS.get(fuel_idx)
                    if extra:
                        comp.description += " " + extra
            else:
                base_tier = _parse_tiered_component(comp.name)
                if base_tier:
                    base, _tier = base_tier
                    # Capacitor6 is actually a reflector (reflects_pulses > 0)
                    if comp.reflects_pulses > 0 and base != "Reflector":
                        text_key = "reflector"
                    else:
                        text_key = _CATEGORY_TEXT_KEY.get(base, "")
                    if text_key:
                        comp.description = text_templates.get(text_key, comp.description)

        # --- Heat capacity fixes from binary analysis ---
        # Coolants: personal heat_capacity = reactor_heat_capacity_increase (fn_10315, param6)
        if comp.name.startswith("Coolant") and comp.heat_capacity == 0.0 and comp.reactor_heat_capacity_increase > 0.0:
            comp.heat_capacity = comp.reactor_heat_capacity_increase

        # Extreme Capacitor (Capacitor6): JSON misidentifies as Reflector.
        # RE: fn 10315 — binary type 13 (Capacitor).  JSON data is swapped with
        # Coolant6 (same rotation as tiers 1-5); corrected by the post-loop swap below.
        # Generates heat during auto-sell (fn 10430).
        if comp.name == "Capacitor6":
            comp.type_of_component = "Capacitor"
            comp.reflects_pulses = 0.0
            # JSON put the value in MaxDurability; it's both personal heat_capacity
            # and reactor_power_capacity_increase (matching Capacitors 1-5 pattern)
            comp.heat_capacity = comp.max_durability
            comp.reactor_power_capacity_increase = comp.max_durability
            comp.max_durability = 0.0
            comp.reactor_heat_capacity_increase = 0.0
            comp.description = (
                "Increases the maximum power capacity of the reactor by {11}. "
                "Produces heat at a rate equal to 50% the amount of power automatically sold by it."
            )
        # Capacitors 1-5: personal heat_capacity = reactor_power_capacity_increase (fn_10315, param6)
        # (In binary, both HeatCap@+0x40 and the power cap value are the same number)
        elif comp.name.startswith("Capacitor") and comp.heat_capacity == 0.0 and comp.reactor_power_capacity_increase > 0.0:
            comp.heat_capacity = comp.reactor_power_capacity_increase

        # --- CantLoseHeat flag (binary +0xCA) ---
        # Reflectors and ExtremeCoolant (Coolant6) are thermally isolated.
        # Must run AFTER Capacitor6 fixup (which clears reflects_pulses to 0).
        if comp.reflects_pulses > 0:
            comp.cant_lose_heat = True
        if comp.name == "Coolant6":
            comp.cant_lose_heat = True

        if comp.name.endswith("-e") and labels.get("depleted_prefix"):
            comp.display_name = f"{labels['depleted_prefix']}{comp.display_name}"

    # --- Coolant/Capacitor/Reflector data rotation fix ---
    # ALL data in the JSON is cyclically rotated for these three categories:
    #   JSON "Coolant" entries contain binary Capacitor data
    #   JSON "Reflector" entries contain binary Coolant data
    #   JSON "Capacitor" entries contain binary Reflector data
    # Fix: rotate back — Coolant←Reflector, Reflector←Capacitor, Capacitor←Coolant
    # Exclude tier 6 (Extreme) from rotation — handled separately
    coolants = sorted([c for c in catalog if c.name.startswith("Coolant") and c.name != "Coolant6"],
                      key=lambda c: c.cost)
    reflectors = sorted([c for c in catalog if c.name.startswith("Reflector")],
                        key=lambda c: c.cost)
    capacitors = sorted([c for c in catalog if c.name.startswith("Capacitor") and c.name != "Capacitor6"],
                        key=lambda c: c.cost)
    # Save current (rotated) values
    coolant_costs = [c.cost for c in coolants]
    reflector_costs = [c.cost for c in reflectors]
    capacitor_costs = [c.cost for c in capacitors]
    coolant_rhci = [c.reactor_heat_capacity_increase for c in coolants]
    reflector_md = [c.max_durability for c in reflectors]
    capacitor_rpci = [c.reactor_power_capacity_increase for c in capacitors]
    # Rotate costs
    for i, c in enumerate(coolants):
        c.cost = reflector_costs[i] if i < len(reflector_costs) else c.cost
    for i, c in enumerate(reflectors):
        if i < len(capacitor_costs):
            c.cost = capacitor_costs[i]
        elif i < len(coolant_costs):
            c.cost = coolant_costs[i]
    for i, c in enumerate(capacitors):
        c.cost = coolant_costs[i] if i < len(coolant_costs) else c.cost
    # Rotate param6 values (stored in different fields per type):
    #   Coolant param6 → reactor_heat_capacity_increase (+ heat_capacity)
    #   Reflector param6 → max_durability
    #   Capacitor param6 → reactor_power_capacity_increase (+ heat_capacity)
    for i, c in enumerate(coolants):
        if i < len(reflector_md):
            c.reactor_heat_capacity_increase = reflector_md[i]
            c.heat_capacity = reflector_md[i]
    for i, c in enumerate(reflectors):
        if i < len(capacitor_rpci):
            c.max_durability = capacitor_rpci[i]
    for i, c in enumerate(capacitors):
        if i < len(coolant_rhci):
            c.reactor_power_capacity_increase = coolant_rhci[i]
            c.heat_capacity = coolant_rhci[i]

    # --- Extreme component data swap (Coolant6 ↔ Capacitor6) ---
    # The JSON data rotation also affects tier 6, but since there's no Reflector6
    # the 3-way cycle collapses to a 2-way swap.
    #   JSON "Coolant6" contains binary Capacitor6 data (cost=104.857T, param6=5.378T)
    #   JSON "Capacitor6" contains binary Coolant6 data (cost=160T, param6=377.82T)
    # Binary truth:
    #   Coolant6  (type 10): cost=160T,     param6=377.82T, CantLoseHeat=1
    #   Capacitor6 (type 13): cost=104.857T, param6=5.378T,  auto-sell heat gen
    coolant6 = next((c for c in catalog if c.name == "Coolant6"), None)
    cap6 = next((c for c in catalog if c.name == "Capacitor6"), None)
    if coolant6 and cap6:
        c6_cost, c6_hc = coolant6.cost, coolant6.heat_capacity
        k6_cost, k6_hc = cap6.cost, cap6.heat_capacity
        # Coolant6 ← Capacitor6's pre-swap values
        coolant6.cost = k6_cost
        coolant6.heat_capacity = k6_hc
        coolant6.reactor_heat_capacity_increase = k6_hc
        coolant6.reactor_power_capacity_increase = 0.0
        # Capacitor6 ← Coolant6's pre-swap values
        cap6.cost = c6_cost
        cap6.heat_capacity = c6_hc
        cap6.reactor_power_capacity_increase = c6_hc

    return catalog
