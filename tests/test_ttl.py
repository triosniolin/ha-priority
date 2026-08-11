"""Expiring overrides.

An override with no end is easy to issue and easy to forget. A TTL turns
"override at Manual Emergency" into a loan: the slot clears itself and control
falls back to whatever is underneath, with no automation needed to clean up.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
import voluptuous as vol
from freezegun.api import FrozenDateTimeFactory
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from homeassistant.exceptions import ServiceValidationError

from custom_components.priority.const import (
    DOMAIN,
    PRI_AUTO,
    PRI_MANUAL_EMERGENCY,
    PRI_MANUAL,
    PRI_DEFAULT,
)

LIGHT = "light.one"


async def _advance(hass, freezer: FrozenDateTimeFactory, seconds: int) -> None:
    """Move time forward and let scheduled callbacks fire."""
    freezer.tick(timedelta(seconds=seconds))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


async def test_ttl_expires_and_falls_through(
    priority_entry, demo_hass, freezer
) -> None:
    """The headline case: override for 30 minutes, then let the system take over."""
    await demo_hass.services.async_call(
        "light",
        "turn_off",
        {"entity_id": LIGHT, "priority": PRI_AUTO},
        blocking=True,
    )
    await demo_hass.services.async_call(
        "light",
        "turn_on",
        {"entity_id": LIGHT, "priority": PRI_MANUAL_EMERGENCY, "priority_ttl": 1800},
        blocking=True,
    )
    await demo_hass.async_block_till_done()
    assert demo_hass.states.get(LIGHT).state == "on"

    await _advance(demo_hass, freezer, 1799)
    assert demo_hass.states.get(LIGHT).state == "on", "must not expire early"

    await _advance(demo_hass, freezer, 2)
    assert demo_hass.states.get(LIGHT).state == "off"

    array = priority_entry.runtime_data.async_peek_array(LIGHT)
    assert array.get(PRI_MANUAL_EMERGENCY) is None
    assert array.effective_priority() == PRI_AUTO


async def test_ttl_zero_means_hold_indefinitely(
    priority_entry, demo_hass, freezer
) -> None:
    """TTL 0 is the documented "until released" case."""
    await demo_hass.services.async_call(
        "light",
        "turn_on",
        {"entity_id": LIGHT, "priority": PRI_MANUAL, "priority_ttl": 0},
        blocking=True,
    )
    await demo_hass.async_block_till_done()

    array = priority_entry.runtime_data.async_peek_array(LIGHT)
    assert array.get(PRI_MANUAL).expires_at is None

    await _advance(demo_hass, freezer, 86400)
    assert array.get(PRI_MANUAL) is not None


async def test_ttl_with_nothing_underneath_leaves_device_alone(
    priority_entry, demo_hass, freezer
) -> None:
    """Expiry releases control; it does not command the device off."""
    await demo_hass.services.async_call(
        "light",
        "turn_on",
        {"entity_id": LIGHT, "priority": PRI_MANUAL, "priority_ttl": 60},
        blocking=True,
    )
    await demo_hass.async_block_till_done()

    await _advance(demo_hass, freezer, 61)

    assert demo_hass.states.get(LIGHT).state == "on"
    array = priority_entry.runtime_data.async_peek_array(LIGHT)
    assert array.get(PRI_MANUAL) is None


async def test_rewriting_a_slot_resets_its_lease(
    priority_entry, demo_hass, freezer
) -> None:
    """A fresh command at the same level starts a fresh clock."""
    await demo_hass.services.async_call(
        "light",
        "turn_on",
        {"entity_id": LIGHT, "priority": PRI_MANUAL, "priority_ttl": 100},
        blocking=True,
    )
    await demo_hass.async_block_till_done()

    await _advance(demo_hass, freezer, 90)
    await demo_hass.services.async_call(
        "light",
        "turn_on",
        {"entity_id": LIGHT, "priority": PRI_MANUAL, "priority_ttl": 100},
        blocking=True,
    )
    await demo_hass.async_block_till_done()

    await _advance(demo_hass, freezer, 20)
    array = priority_entry.runtime_data.async_peek_array(LIGHT)
    assert array.get(PRI_MANUAL) is not None, "old timer must not fire"

    await _advance(demo_hass, freezer, 90)
    assert array.get(PRI_MANUAL) is None


async def test_relinquish_cancels_the_timer(
    priority_entry, demo_hass, freezer
) -> None:
    """A cancelled lease must not fire later against a re-used slot."""
    await demo_hass.services.async_call(
        "light",
        "turn_on",
        {"entity_id": LIGHT, "priority": PRI_MANUAL, "priority_ttl": 100},
        blocking=True,
    )
    await demo_hass.async_block_till_done()

    await demo_hass.services.async_call(
        DOMAIN,
        "relinquish",
        {"entity_id": LIGHT, "priority": PRI_MANUAL},
        blocking=True,
    )
    await demo_hass.async_block_till_done()

    # Re-take the same slot with no lease at all.
    await demo_hass.services.async_call(
        "light",
        "turn_on",
        {"entity_id": LIGHT, "priority": PRI_MANUAL},
        blocking=True,
    )
    await demo_hass.async_block_till_done()

    await _advance(demo_hass, freezer, 200)
    array = priority_entry.runtime_data.async_peek_array(LIGHT)
    assert array.get(PRI_MANUAL) is not None


async def test_ttl_accepts_a_duration(priority_entry, demo_hass, freezer) -> None:
    """`priority_ttl: "00:30:00"` is the form a YAML automation will use."""
    await demo_hass.services.async_call(
        "light",
        "turn_on",
        {
            "entity_id": LIGHT,
            "priority": PRI_MANUAL,
            "priority_ttl": "00:30:00",
        },
        blocking=True,
    )
    await demo_hass.async_block_till_done()

    await _advance(demo_hass, freezer, 1801)
    array = priority_entry.runtime_data.async_peek_array(LIGHT)
    assert array.get(PRI_MANUAL) is None


async def test_ttl_rejected_at_manual_low(priority_entry, demo_hass) -> None:
    """Priority 5 is the floor - there is nothing to expire back to."""
    with pytest.raises(ServiceValidationError):
        await demo_hass.services.async_call(
            "light",
            "turn_on",
            {"entity_id": LIGHT, "priority": PRI_DEFAULT, "priority_ttl": 60},
            blocking=True,
        )


async def test_negative_ttl_rejected(priority_entry, demo_hass) -> None:
    with pytest.raises(vol.Invalid):
        await demo_hass.services.async_call(
            "light",
            "turn_on",
            {"entity_id": LIGHT, "priority": PRI_MANUAL, "priority_ttl": -5},
            blocking=True,
        )


async def test_expired_slot_never_wins_even_if_a_timer_is_missed(
    priority_entry, demo_hass
) -> None:
    """Defence in depth: resolution ignores lapsed slots regardless of timers."""
    from datetime import datetime, UTC

    from custom_components.priority.array import Slot

    array = priority_entry.runtime_data.async_get_array(LIGHT)
    array.write(
        PRI_AUTO,
        Slot("light", "turn_off", {}, datetime(2020, 1, 1, tzinfo=UTC)),
    )
    array.write(
        PRI_MANUAL_EMERGENCY,
        Slot(
            "light",
            "turn_on",
            {},
            datetime(2020, 1, 1, tzinfo=UTC),
            expires_at=datetime(2020, 1, 2, tzinfo=UTC),
        ),
    )
    assert array.effective_priority() == PRI_AUTO


async def test_lease_returns_to_current_intent_not_a_snapshot(
    priority_entry, demo_hass, freezer
) -> None:
    """The claim in the README that a timer cannot match.

    "On for twenty minutes" done with delay-then-turn-off hardcodes `off` as
    the thing to go back to. A lease does not turn the light off when it
    expires; it stops overriding, and the house resumes whatever it was already
    trying to do. So if something else asked for the light while the override
    was up, the light stays on.
    """
    # Nothing wants the light on.
    await demo_hass.services.async_call(
        "light", "turn_off", {"entity_id": LIGHT, "priority": PRI_AUTO}, blocking=True
    )
    # "On for twenty minutes."
    await demo_hass.services.async_call(
        "light",
        "turn_on",
        {"entity_id": LIGHT, "priority": PRI_MANUAL, "priority_ttl": 1200},
        blocking=True,
    )
    await demo_hass.async_block_till_done()
    assert demo_hass.states.get(LIGHT).state == "on"

    # Ten minutes in, an automation decides it wants the light on anyway.
    await _advance(demo_hass, freezer, 600)
    await demo_hass.services.async_call(
        "light",
        "turn_on",
        {"entity_id": LIGHT, "priority": PRI_AUTO, "brightness": 90},
        blocking=True,
    )
    await demo_hass.async_block_till_done()

    # The lease lapses. A delay-then-turn-off would switch it off here.
    await _advance(demo_hass, freezer, 601)

    assert demo_hass.states.get(LIGHT).state == "on", (
        "the override ended, but the automation still wants it on"
    )
    assert demo_hass.states.get(LIGHT).attributes["brightness"] == 90
    array = priority_entry.runtime_data.async_peek_array(LIGHT)
    assert array.get(PRI_MANUAL) is None
    assert array.effective_priority() == PRI_AUTO


async def test_lease_goes_back_to_off_when_nothing_else_wants_it(
    priority_entry, demo_hass, freezer
) -> None:
    """And the ordinary case from the README: it does go back off."""
    await demo_hass.services.async_call(
        "light", "turn_off", {"entity_id": LIGHT, "priority": PRI_AUTO}, blocking=True
    )
    await demo_hass.services.async_call(
        "light",
        "turn_on",
        {"entity_id": LIGHT, "priority": PRI_MANUAL, "priority_ttl": 1200},
        blocking=True,
    )
    await demo_hass.async_block_till_done()
    assert demo_hass.states.get(LIGHT).state == "on"

    await _advance(demo_hass, freezer, 1201)
    assert demo_hass.states.get(LIGHT).state == "off"
