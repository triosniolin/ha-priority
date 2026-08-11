"""The behaviour the whole project exists for."""

from __future__ import annotations

import pytest

from homeassistant.core import Context

from custom_components.priority.const import (
    DOMAIN,
    PRI_AUTO,
    PRI_AUTO_EMERGENCY,
    PRI_MANUAL,
    PRI_DEFAULT,
)

from . import mocks

LIGHT = "light.one"


def _light(entity_id: str = LIGHT):
    """The mock entity object behind an entity id."""
    index = {"light.one": 0, "light.two": 1}[entity_id]
    return mocks.ENTITIES["light"][index]


async def _call(hass, service, priority=None, entity_id=LIGHT, **data):
    payload = {"entity_id": entity_id, **data}
    if priority is not None:
        payload["priority"] = priority
    await hass.services.async_call("light", service, payload, blocking=True)
    await hass.async_block_till_done()


# ----------------------------------------------------------------------
# Core arbitration
# ----------------------------------------------------------------------


async def test_explicit_default_is_overridden_by_an_automation(
    priority_entry, demo_hass
) -> None:
    """An explicit PRI 5 write leaves automations at PRI 4 free to override."""
    await _call(demo_hass, "turn_on", PRI_DEFAULT)
    assert demo_hass.states.get(LIGHT).state == "on"

    await _call(demo_hass, "turn_off", PRI_AUTO)
    assert demo_hass.states.get(LIGHT).state == "off"


async def test_manual_blocks_an_automation(priority_entry, demo_hass) -> None:
    """PRI 3 means ordinary automations do not get through."""
    await _call(demo_hass, "turn_on", PRI_MANUAL)
    assert demo_hass.states.get(LIGHT).state == "on"

    before = len(_light().turn_off_calls)
    await _call(demo_hass, "turn_off", PRI_AUTO)

    assert demo_hass.states.get(LIGHT).state == "on"
    assert len(_light().turn_off_calls) == before, "device must not be touched"


async def test_blocked_call_is_still_recorded(priority_entry, demo_hass) -> None:
    """A losing command is remembered, so it can take over on relinquish."""
    await _call(demo_hass, "turn_on", PRI_MANUAL)
    await _call(demo_hass, "turn_off", PRI_AUTO)

    array = priority_entry.runtime_data.async_peek_array(LIGHT)
    assert array.effective_priority() == PRI_MANUAL
    assert array.get(PRI_AUTO).service == "turn_off"


async def test_emergency_beats_manual(priority_entry, demo_hass) -> None:
    """PRI 2 is exactly the escape hatch PRI 3 is supposed to allow."""
    await _call(demo_hass, "turn_off", PRI_MANUAL)
    await _call(demo_hass, "turn_on", PRI_AUTO_EMERGENCY, brightness=255)

    assert demo_hass.states.get(LIGHT).state == "on"
    assert demo_hass.states.get(LIGHT).attributes["brightness"] == 255


async def test_same_priority_rewrites_in_place(priority_entry, demo_hass) -> None:
    """An automation can update its own command without relinquishing."""
    await _call(demo_hass, "turn_on", PRI_AUTO, brightness=100)
    await _call(demo_hass, "turn_on", PRI_AUTO, brightness=200)
    assert demo_hass.states.get(LIGHT).attributes["brightness"] == 200


# ----------------------------------------------------------------------
# Relinquish and replay
# ----------------------------------------------------------------------


async def test_relinquish_replays_the_next_slot(priority_entry, demo_hass) -> None:
    await _call(demo_hass, "turn_off", PRI_AUTO)
    await _call(demo_hass, "turn_on", PRI_AUTO_EMERGENCY)
    assert demo_hass.states.get(LIGHT).state == "on"

    await demo_hass.services.async_call(
        DOMAIN,
        "relinquish",
        {"entity_id": LIGHT, "priority": PRI_AUTO_EMERGENCY},
        blocking=True,
    )
    await demo_hass.async_block_till_done()

    assert demo_hass.states.get(LIGHT).state == "off"


async def test_relinquish_uses_the_current_lower_command_not_a_snapshot(
    priority_entry, demo_hass
) -> None:
    """The failure mode every capture-and-restore automation eventually hits.

    The lower-priority command is mutated *while* the higher one holds control.
    On relinquish the device must land on the new value, not the one that was
    in force when the override started.
    """
    await _call(demo_hass, "turn_on", PRI_AUTO, brightness=50)
    await _call(demo_hass, "turn_on", PRI_AUTO_EMERGENCY, brightness=255)
    assert demo_hass.states.get(LIGHT).attributes["brightness"] == 255

    # The automation moves on while it is being overridden.
    await _call(demo_hass, "turn_on", PRI_AUTO, brightness=90)
    assert demo_hass.states.get(LIGHT).attributes["brightness"] == 255

    await demo_hass.services.async_call(
        DOMAIN,
        "relinquish",
        {"entity_id": LIGHT, "priority": PRI_AUTO_EMERGENCY},
        blocking=True,
    )
    await demo_hass.async_block_till_done()

    assert demo_hass.states.get(LIGHT).attributes["brightness"] == 90


async def test_relinquish_restores_every_attribute(priority_entry, demo_hass) -> None:
    """A slot holds the whole call, so colour comes back with brightness."""
    await _call(
        demo_hass, "turn_on", PRI_AUTO, brightness=80, hs_color=[240, 100]
    )
    await _call(demo_hass, "turn_on", PRI_AUTO_EMERGENCY, brightness=255)

    await demo_hass.services.async_call(
        DOMAIN,
        "relinquish",
        {"entity_id": LIGHT, "priority": PRI_AUTO_EMERGENCY},
        blocking=True,
    )
    await demo_hass.async_block_till_done()

    state = demo_hass.states.get(LIGHT)
    assert state.attributes["brightness"] == 80
    assert state.attributes["hs_color"] == (240, 100)


async def test_relinquish_produces_exactly_one_dispatch(
    priority_entry, demo_hass
) -> None:
    """The recursion guard: replay must not re-enter the wrapper."""
    await _call(demo_hass, "turn_on", PRI_AUTO, brightness=60)
    await _call(demo_hass, "turn_off", PRI_AUTO_EMERGENCY)

    before = len(_light().turn_on_calls)
    await demo_hass.services.async_call(
        DOMAIN,
        "relinquish",
        {"entity_id": LIGHT, "priority": PRI_AUTO_EMERGENCY},
        blocking=True,
    )
    await demo_hass.async_block_till_done()

    assert len(_light().turn_on_calls) - before == 1


async def test_relinquish_of_a_losing_slot_does_not_dispatch(
    priority_entry, demo_hass
) -> None:
    """Clearing a slot that was not in control must not disturb the device."""
    await _call(demo_hass, "turn_on", PRI_AUTO_EMERGENCY)
    await _call(demo_hass, "turn_off", PRI_AUTO)

    before = len(_light().turn_on_calls) + len(_light().turn_off_calls)
    await demo_hass.services.async_call(
        DOMAIN, "relinquish", {"entity_id": LIGHT, "priority": PRI_AUTO}, blocking=True
    )
    await demo_hass.async_block_till_done()

    assert len(_light().turn_on_calls) + len(_light().turn_off_calls) == before
    assert demo_hass.states.get(LIGHT).state == "on"


async def test_relinquish_empty_array_leaves_device_alone(
    priority_entry, demo_hass
) -> None:
    """Relinquishing everything means stop arbitrating, not turn off."""
    await _call(demo_hass, "turn_on", PRI_AUTO_EMERGENCY)
    await demo_hass.services.async_call(
        DOMAIN, "relinquish_all", {"entity_id": LIGHT}, blocking=True
    )
    await demo_hass.async_block_till_done()

    assert demo_hass.states.get(LIGHT).state == "on"


# ----------------------------------------------------------------------
# Defaults, toggle, and other domains
# ----------------------------------------------------------------------


async def test_everything_defaults_to_the_same_level(
    priority_entry, demo_hass, hass_admin_user
) -> None:
    """People and automations both land in slot 5 unless told otherwise."""
    await demo_hass.services.async_call(
        "light",
        "turn_on",
        {"entity_id": LIGHT},
        blocking=True,
        context=Context(user_id=hass_admin_user.id),
    )
    await demo_hass.async_block_till_done()
    assert priority_entry.runtime_data.async_peek_array(LIGHT).get(PRI_DEFAULT)

    await demo_hass.services.async_call(
        "light", "turn_off", {"entity_id": "light.two"}, blocking=True
    )
    await demo_hass.async_block_till_done()
    array_two = priority_entry.runtime_data.async_peek_array("light.two")
    assert array_two.get(PRI_DEFAULT) is not None
    assert array_two.get(PRI_AUTO) is None


async def test_default_traffic_is_last_wins(
    priority_entry, demo_hass, hass_admin_user
) -> None:
    """The no-op guarantee.

    With nothing mentioning priority, an automation and a person must be able
    to countermand each other freely, in either order, exactly as they do
    without this integration installed. This is what makes it safe to enable
    house-wide: arbitration is opt-in per command, not something that
    accumulates behind your back.
    """
    # Automation turns it on.
    await demo_hass.services.async_call(
        "light", "turn_on", {"entity_id": LIGHT}, blocking=True
    )
    await demo_hass.async_block_till_done()
    assert demo_hass.states.get(LIGHT).state == "on"

    # Person turns it off in the app. This must work.
    await demo_hass.services.async_call(
        "light",
        "turn_off",
        {"entity_id": LIGHT},
        blocking=True,
        context=Context(user_id=hass_admin_user.id),
    )
    await demo_hass.async_block_till_done()
    assert demo_hass.states.get(LIGHT).state == "off"

    # And the automation can take it back again.
    await demo_hass.services.async_call(
        "light", "turn_on", {"entity_id": LIGHT}, blocking=True
    )
    await demo_hass.async_block_till_done()
    assert demo_hass.states.get(LIGHT).state == "on"


async def test_wall_switch_still_works_after_an_automation(
    priority_entry, demo_hass
) -> None:
    """The lockout regression, from the other direction.

    An automation commanding an entity must not make it deaf to the wall
    switch. Both write slot 5, so the last one wins.
    """
    await demo_hass.services.async_call(
        "switch", "turn_on", {"entity_id": "switch.one"}, blocking=True
    )
    await demo_hass.async_block_till_done()

    await demo_hass.services.async_call(
        "switch", "turn_off", {"entity_id": "switch.one"}, blocking=True
    )
    await demo_hass.async_block_till_done()
    assert demo_hass.states.get("switch.one").state == "off"


async def test_toggle_is_resolved_before_storage(priority_entry, demo_hass) -> None:
    """`toggle` has no stable meaning in an array, so it is resolved on write."""
    await _call(demo_hass, "turn_off", PRI_AUTO)
    await _call(demo_hass, "toggle", PRI_MANUAL)

    array = priority_entry.runtime_data.async_peek_array(LIGHT)
    assert array.get(PRI_MANUAL).service == "turn_on"
    assert demo_hass.states.get(LIGHT).state == "on"


async def test_climate_setpoint_arbitrates(priority_entry, demo_hass) -> None:
    """Non-toggle setpoint services work without per-domain code."""
    await demo_hass.services.async_call(
        "climate",
        "set_temperature",
        {"entity_id": "climate.one", "temperature": 68, "priority": PRI_MANUAL},
        blocking=True,
    )
    await demo_hass.async_block_till_done()
    assert demo_hass.states.get("climate.one").attributes["temperature"] == 68

    await demo_hass.services.async_call(
        "climate",
        "set_temperature",
        {"entity_id": "climate.one", "temperature": 78, "priority": PRI_AUTO},
        blocking=True,
    )
    await demo_hass.async_block_till_done()
    assert demo_hass.states.get("climate.one").attributes["temperature"] == 68


async def test_cover_position_arbitrates(priority_entry, demo_hass) -> None:
    await demo_hass.services.async_call(
        "cover",
        "set_cover_position",
        {"entity_id": "cover.one", "position": 30, "priority": PRI_MANUAL},
        blocking=True,
    )
    await demo_hass.async_block_till_done()
    assert demo_hass.states.get("cover.one").attributes["current_position"] == 30

    await demo_hass.services.async_call(
        "cover",
        "set_cover_position",
        {"entity_id": "cover.one", "position": 90, "priority": PRI_AUTO},
        blocking=True,
    )
    await demo_hass.async_block_till_done()
    assert demo_hass.states.get("cover.one").attributes["current_position"] == 30


async def test_multi_target_call_arbitrates_each_entity(
    priority_entry, demo_hass
) -> None:
    """One call, two entities, independent arrays."""
    await _call(demo_hass, "turn_on", PRI_MANUAL, entity_id=LIGHT)
    await _call(
        demo_hass, "turn_off", PRI_AUTO, entity_id=["light.one", "light.two"]
    )

    assert demo_hass.states.get("light.one").state == "on"
    assert demo_hass.states.get("light.two").state == "off"


async def test_get_service_returns_the_array(priority_entry, demo_hass) -> None:
    await _call(demo_hass, "turn_on", PRI_AUTO_EMERGENCY, brightness=200)
    response = await demo_hass.services.async_call(
        DOMAIN,
        "get",
        {"entity_id": LIGHT},
        blocking=True,
        return_response=True,
    )
    array = response["arrays"][LIGHT]
    assert array["effective_priority"] == PRI_AUTO_EMERGENCY
    assert array["effective_priority_name"] == "Automatic Emergency"
    assert array["effective_command"]["data"]["brightness"] == 200
