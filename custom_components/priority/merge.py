"""Per-attribute merging (issue #5): a `merge_down` winner fills its gaps from below.

Only between levels holding the same service, since the result must still be one call. The winner
keeps its own fields; below it, each attribute group comes from the highest live slot that names
it. The walk carries the flag down: a slot without it inherits, an explicit `false` contributes and
then stops. Anything not in the table stops the walk without contributing, so a service with no
table never merges.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from .array import Slot

_LIGHT_COLOR = frozenset({"color"})
_LIGHT_BRIGHTNESS = frozenset({"brightness"})
_SETPOINT = frozenset({"setpoint"})
_MODE = frozenset({"mode"})

_GROUPS: dict[tuple[str, str], dict[str, frozenset[str]]] = {
    ("light", "turn_on"): {
        "brightness": _LIGHT_BRIGHTNESS,
        "brightness_pct": _LIGHT_BRIGHTNESS,
        "color_name": _LIGHT_COLOR,
        "color_temp_kelvin": _LIGHT_COLOR,
        "hs_color": _LIGHT_COLOR,
        "rgb_color": _LIGHT_COLOR,
        "rgbw_color": _LIGHT_COLOR,
        "rgbww_color": _LIGHT_COLOR,
        "xy_color": _LIGHT_COLOR,
        # A colour mode that also sets brightness, so it needs both groups free.
        "white": _LIGHT_COLOR | _LIGHT_BRIGHTNESS,
        "transition": frozenset({"transition"}),
    },
    # A preset and a percentage both set the speed.
    ("fan", "turn_on"): {
        "percentage": frozenset({"speed"}),
        "preset_mode": frozenset({"speed"}),
    },
    ("climate", "set_temperature"): {
        "temperature": _SETPOINT,
        "target_temp_high": _SETPOINT,
        "target_temp_low": _SETPOINT,
        "hvac_mode": _MODE,
    },
    ("water_heater", "set_temperature"): {
        "temperature": _SETPOINT,
        "operation_mode": _MODE,
    },
}

# Published for the cards, which only offer the toggle where it can do something.
MERGE_SERVICES: dict[str, list[str]] = {}
for _domain, _service in _GROUPS:
    MERGE_SERVICES.setdefault(_domain, []).append(_service)

# Values core treats as a turn-off even inside an "on" service; merging one would let a lower
# level switch off a device the winner holds on.
_OFF_VALUES: dict[str, dict[str, Any]] = {
    "light": {"brightness": 0, "brightness_pct": 0, "white": 0},
    "fan": {"percentage": 0},
    "climate": {"hvac_mode": "off"},
    "water_heater": {"operation_mode": "off"},
}


def _means(value: Any, off: Any) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(off, str):
        return value == off
    try:
        return float(value) == off
    except (TypeError, ValueError):
        return False


def is_off(slot: Slot) -> bool:
    if slot.service == "turn_off":
        return True
    off = _OFF_VALUES.get(slot.domain, {})
    return any(key in slot.data and _means(slot.data[key], value) for key, value in off.items())


def merged_data(
    winner: Slot, below: Iterable[Slot], default: bool
) -> dict[str, Any] | None:
    """The winner's data with gaps filled from ``below`` (highest first), or None if unchanged."""
    groups = _GROUPS.get((winner.domain, winner.service))
    if groups is None or is_off(winner):
        return None
    if not (default if winner.merge_down is None else winner.merge_down):
        return None
    if not groups.keys() >= winner.data.keys():
        return None

    data = dict(winner.data)
    claimed: set[str] = set().union(*(groups[key] for key in data))
    for slot in below:
        if slot.service == winner.service and not is_off(slot):
            if not groups.keys() >= slot.data.keys():
                break
            for key, value in slot.data.items():
                if not groups[key] & claimed:
                    data[key] = value
            claimed.update(*(groups[key] for key in slot.data))
        if slot.merge_down is False:
            break
    return data if data != winner.data else None
