"""What the diagnostic sensor publishes for the overrides card."""

from __future__ import annotations

from homeassistant.util import dt as dt_util

from custom_components.priority.array import Slot
from custom_components.priority.const import PRI_AUTO, PRI_MANUAL

LIGHT = "light.one"


def _overrides(hass) -> dict:
    return hass.states.get("sensor.active_overrides").attributes["overrides"]


async def test_every_held_level_is_published(priority_entry, demo_hass) -> None:
    """An entity can be held at more than one level at once.

    The sensor used to publish only `effective()`, so a Manual hold with an
    Automatic hold underneath it looked, on the overrides card, exactly like a
    single Manual hold. The level below had not gone anywhere; it was simply
    never sent to the frontend.
    """
    manager = priority_entry.runtime_data
    now = dt_util.utcnow()
    manager.async_write_slot(
        LIGHT, PRI_MANUAL, Slot("light", "turn_on", {}, now, "user:Ada")
    )
    manager.async_write_slot(
        LIGHT, PRI_AUTO, Slot("light", "turn_off", {}, now, "automation.dusk")
    )
    manager.async_notify(LIGHT)
    await demo_hass.async_block_till_done()

    entry = _overrides(demo_hass)[LIGHT]

    # The winner stays flat, so an older cached card keeps working.
    assert entry["priority"] == PRI_MANUAL

    levels = entry["levels"]
    assert sorted(levels) == [str(PRI_MANUAL), str(PRI_AUTO)]
    assert levels[str(PRI_MANUAL)]["written_by"] == "user:Ada"
    assert levels[str(PRI_AUTO)]["written_by"] == "automation.dusk"
    assert levels[str(PRI_AUTO)]["service"] == "light.turn_off"


async def test_default_is_not_published_as_a_level(
    priority_entry, demo_hass
) -> None:
    """Default is not an override, so it has no business on the card."""
    manager = priority_entry.runtime_data
    now = dt_util.utcnow()
    manager.async_write_slot(
        LIGHT, PRI_MANUAL, Slot("light", "turn_on", {}, now, "user:Ada")
    )
    manager.async_write_slot(
        LIGHT, 5, Slot("light", "turn_off", {}, now, "out_of_band")
    )
    manager.async_notify(LIGHT)
    await demo_hass.async_block_till_done()

    assert list(_overrides(demo_hass)[LIGHT]["levels"]) == [str(PRI_MANUAL)]


async def test_a_lapsed_slot_is_not_published(
    priority_entry, demo_hass
) -> None:
    """A slot whose lease ran out is not holding anything.

    effective() already skips expired slots; the level list has to agree, or
    the card shows a hold that nothing is actually enforcing.
    """
    manager = priority_entry.runtime_data
    now = dt_util.utcnow()
    manager.async_write_slot(
        LIGHT, PRI_MANUAL, Slot("light", "turn_on", {}, now, "user:Ada")
    )
    manager.async_write_slot(
        LIGHT,
        PRI_AUTO,
        Slot(
            "light",
            "turn_off",
            {},
            now,
            "automation.dusk",
            expires_at=now - dt_util.dt.timedelta(seconds=1),
        ),
    )
    manager.async_notify(LIGHT)
    await demo_hass.async_block_till_done()

    assert list(_overrides(demo_hass)[LIGHT]["levels"]) == [str(PRI_MANUAL)]
