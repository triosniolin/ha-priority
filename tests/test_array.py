"""Unit tests for the priority array itself - no Home Assistant needed."""

from __future__ import annotations

import pytest

from custom_components.priority.array import PriorityArray, Slot
from custom_components.priority.const import (
    PRI_AUTO,
    PRI_AUTO_EMERGENCY,
    PRI_MANUAL_EMERGENCY,
    PRI_MANUAL,
    PRI_DEFAULT,
)
from homeassistant.util import dt as dt_util


def _slot(service: str = "turn_on", **data) -> Slot:
    return Slot(
        domain="light",
        service=service,
        data=dict(data),
        written_at=dt_util.utcnow(),
    )


def test_empty_array_has_no_winner() -> None:
    array = PriorityArray("light.test")
    assert array.is_empty()
    assert array.effective() is None
    assert array.effective_priority() is None


def test_lowest_number_wins() -> None:
    array = PriorityArray("light.test")
    array.write(PRI_AUTO, _slot("turn_off"))
    array.write(PRI_MANUAL, _slot("turn_on"))
    priority, slot = array.effective()
    assert priority == PRI_MANUAL
    assert slot.service == "turn_on"


def test_wins_allows_equal_priority_rewrite() -> None:
    """An automation must be able to update its own command in place."""
    array = PriorityArray("light.test")
    array.write(PRI_AUTO, _slot())
    assert array.wins(PRI_AUTO)
    assert array.wins(PRI_AUTO_EMERGENCY)
    assert not array.wins(PRI_DEFAULT)


def test_clear_hands_control_down() -> None:
    array = PriorityArray("light.test")
    array.write(PRI_AUTO, _slot("turn_off"))
    array.write(PRI_AUTO_EMERGENCY, _slot("turn_on", brightness=255))
    assert array.effective_priority() == PRI_AUTO_EMERGENCY

    assert array.clear(PRI_AUTO_EMERGENCY)
    assert array.effective_priority() == PRI_AUTO
    assert array.effective()[1].service == "turn_off"


def test_clear_empty_slot_reports_false() -> None:
    array = PriorityArray("light.test")
    assert not array.clear(PRI_MANUAL_EMERGENCY)


def test_invalid_priority_rejected() -> None:
    array = PriorityArray("light.test")
    with pytest.raises(ValueError):
        array.write(0, _slot())
    with pytest.raises(ValueError):
        array.write(6, _slot())


def test_storage_round_trip_persists_only_high_slots() -> None:
    """Slots 4 and 5 are re-derived rather than restored."""
    array = PriorityArray("light.test")
    array.write(PRI_MANUAL, _slot("turn_on", brightness=120))
    array.write(PRI_AUTO, _slot("turn_off"))
    array.write(PRI_DEFAULT, _slot("turn_on"))

    stored = array.to_storage()
    assert set(stored["slots"]) == {str(PRI_MANUAL)}

    restored = PriorityArray.from_storage("light.test", stored)
    assert restored.get(PRI_MANUAL).data == {"brightness": 120}
    assert restored.get(PRI_AUTO) is None
    assert restored.get(PRI_DEFAULT) is None


def test_from_storage_drops_malformed_slots() -> None:
    restored = PriorityArray.from_storage(
        "light.test",
        {"slots": {"3": {"domain": "light"}, "notanint": {}, "1": None}},
    )
    assert restored.is_empty()


def test_as_dict_reports_the_winner() -> None:
    array = PriorityArray("light.test")
    array.write(PRI_AUTO, _slot("turn_off"))
    snapshot = array.as_dict()
    assert snapshot["effective_priority"] == PRI_AUTO
    assert snapshot["effective_priority_name"] == "Automatic"
    assert snapshot["effective_command"]["service"] == "turn_off"
    assert snapshot["slots"]["5"] is None
