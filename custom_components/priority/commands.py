"""Translate between an entity's state and the command that would produce it."""

from __future__ import annotations

from typing import Any

from homeassistant.components.cover import CoverEntityFeature
from homeassistant.components.valve import ValveEntityFeature
from homeassistant.const import ATTR_SUPPORTED_FEATURES, STATE_OFF, STATE_ON
from homeassistant.core import State

_OPENING, _CLOSING, _OPEN, _CLOSED = "opening", "closing", "open", "closed"

_MOVING_STOP: dict[str, tuple[str, int]] = {
    "cover": ("stop_cover", CoverEntityFeature.STOP),
    "valve": ("stop_valve", ValveEntityFeature.STOP),
}


def resolve_toggle(domain: str, state: State | None) -> str:
    """Pick the concrete service core's own toggle would run against this state.

    A slot holds a command to replay later, and replaying a toggle would flip the
    device instead of restoring it.
    """
    value = state.state if state is not None else None

    if domain in _MOVING_STOP:
        stop, feature = _MOVING_STOP[domain]
        features = state.attributes.get(ATTR_SUPPORTED_FEATURES, 0) if state else 0
        if value in (_OPENING, _CLOSING) and features & feature:
            return stop
        # Core's valve toggle keeps going while closing; its cover toggle reverses.
        opens = value == _CLOSED or (domain == "cover" and value == _CLOSING)
        return f"open_{domain}" if opens or value is None else f"close_{domain}"

    if domain == "climate":
        is_on = value is not None and value != STATE_OFF
    elif domain == "media_player":
        is_on = value is not None and value not in (STATE_OFF, "standby")
    else:
        is_on = value == STATE_ON
    return "turn_off" if is_on else "turn_on"


def command_for_state(domain: str, state: str) -> tuple[str, dict[str, Any]] | None:
    """The command an out-of-band change to ``state`` amounts to, if any."""
    if domain in ("cover", "valve"):
        return {_OPEN: (f"open_{domain}", {}), _CLOSED: (f"close_{domain}", {})}.get(
            state
        )
    if domain == "lock":
        return {"locked": ("lock", {}), "unlocked": ("unlock", {})}.get(state)
    # set_hvac_mode needs no feature flag, unlike climate turn_on/turn_off.
    if domain == "climate":
        return "set_hvac_mode", {"hvac_mode": state}
    if domain == "water_heater":
        if state == STATE_OFF:
            return "turn_off", {}
        return "set_operation_mode", {"operation_mode": state}
    if domain == "media_player":
        return ("turn_off", {}) if state in (STATE_OFF, "standby") else ("turn_on", {})
    return {STATE_ON: ("turn_on", {}), STATE_OFF: ("turn_off", {})}.get(state)
