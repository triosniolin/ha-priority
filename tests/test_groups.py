"""Group entities forward to their members with a fresh service call (issue #2).

That inner call re-enters the wrapper without a priority field, so without
inheritance a group command at Manual reached its members at Default: held
members ignored it, and unheld members took it at the wrong level.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import Context
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.priority.const import DOMAIN, PRI_AUTO, PRI_DEFAULT, PRI_MANUAL

from . import mocks

GROUP = "light.room"
ONE = "light.one"
TWO = "light.two"


def _light(entity_id: str):
    index = {ONE: 0, TWO: 1}[entity_id]
    return mocks.ENTITIES["light"][index]


def _commands(entity_id: str) -> int:
    light = _light(entity_id)
    return len(light.turn_on_calls) + len(light.turn_off_calls)


@pytest.fixture
async def room_group(demo_hass) -> str:
    """A core `group` light over both mock lights, set up before priority."""
    assert await async_setup_component(demo_hass, "group", {})
    entry = MockConfigEntry(
        domain="group",
        options={
            "group_type": "light",
            "name": "Room",
            "entities": [ONE, TWO],
            "hide_members": False,
            "all": False,
        },
        title="Room",
    )
    entry.add_to_hass(demo_hass)
    assert await demo_hass.config_entries.async_setup(entry.entry_id)
    await demo_hass.async_block_till_done()
    assert demo_hass.states.get(GROUP) is not None
    return GROUP


async def _call(hass, service, entity_id, priority=None, context=None, **data):
    payload = {"entity_id": entity_id, **data}
    if priority is not None:
        payload["priority"] = priority
    await hass.services.async_call(
        "light", service, payload, blocking=True, context=context
    )
    await hass.async_block_till_done()


async def _get(hass, entity_id) -> dict:
    response = await hass.services.async_call(
        DOMAIN, "get", {"entity_id": entity_id}, blocking=True, return_response=True
    )
    return response["arrays"][entity_id]


async def test_group_command_reaches_members_at_its_priority(
    room_group, priority_entry, demo_hass
) -> None:
    """The reported case: a Manual group off must beat a member's own Manual on."""
    await _call(demo_hass, "turn_on", ONE, PRI_MANUAL)
    await _call(demo_hass, "turn_on", TWO)

    await _call(demo_hass, "turn_off", GROUP, PRI_MANUAL)

    assert demo_hass.states.get(ONE).state == "off"
    assert demo_hass.states.get(TWO).state == "off"
    for member in (ONE, TWO):
        slot = (await _get(demo_hass, member))["slots"][str(PRI_MANUAL)]
        assert slot is not None and slot["service"] == "turn_off"


async def test_default_group_command_still_respects_member_holds(
    room_group, priority_entry, demo_hass
) -> None:
    """Inheritance must not strengthen anything: Default stays Default."""
    await _call(demo_hass, "turn_on", ONE, PRI_MANUAL)
    await _call(demo_hass, "turn_on", TWO)

    await _call(demo_hass, "turn_off", GROUP)

    assert demo_hass.states.get(ONE).state == "on"
    assert demo_hass.states.get(TWO).state == "off"
    assert (await _get(demo_hass, TWO))["slots"][str(PRI_MANUAL)] is None


async def test_members_inherit_the_group_lease(
    room_group, priority_entry, demo_hass, freezer: FrozenDateTimeFactory
) -> None:
    """A member must not keep a permanent hold from a group command that had a lease."""
    await _call(demo_hass, "turn_on", ONE, PRI_AUTO)
    await _call(demo_hass, "turn_off", GROUP, PRI_MANUAL, priority_ttl=600)
    assert demo_hass.states.get(ONE).state == "off"

    freezer.tick(timedelta(seconds=601))
    async_fire_time_changed(demo_hass)
    await demo_hass.async_block_till_done()

    assert (await _get(demo_hass, ONE))["slots"][str(PRI_MANUAL)] is None
    assert demo_hass.states.get(ONE).state == "on"


async def test_a_reused_context_does_not_inherit(
    room_group, priority_entry, demo_hass
) -> None:
    """A script reuses one context for every step; only calls made inside a dispatch inherit."""
    context = Context()
    await _call(demo_hass, "turn_on", ONE, PRI_MANUAL, context=context)
    await _call(demo_hass, "turn_on", TWO, context=context)

    array = await _get(demo_hass, TWO)
    assert array["slots"][str(PRI_MANUAL)] is None
    assert array["effective_priority"] == PRI_DEFAULT


async def test_relinquishing_the_group_releases_its_members(
    room_group, priority_entry, demo_hass
) -> None:
    """Otherwise members stay held at Manual with nothing on the group to show it."""
    await _call(demo_hass, "turn_on", GROUP)
    await _call(demo_hass, "turn_off", GROUP, PRI_MANUAL)
    before = {member: _commands(member) for member in (ONE, TWO)}

    await demo_hass.services.async_call(
        DOMAIN, "relinquish", {"entity_id": GROUP, "priority": PRI_MANUAL}, blocking=True
    )
    await demo_hass.async_block_till_done()

    for member in (ONE, TWO):
        assert (await _get(demo_hass, member))["slots"][str(PRI_MANUAL)] is None
        assert demo_hass.states.get(member).state == "on"
        assert _commands(member) - before[member] == 1, "one command, no flicker"


async def test_relinquish_all_on_the_group_releases_its_members(
    room_group, priority_entry, demo_hass
) -> None:
    await _call(demo_hass, "turn_on", GROUP)
    await _call(demo_hass, "turn_off", GROUP, PRI_MANUAL)

    await demo_hass.services.async_call(
        DOMAIN, "relinquish_all", {"entity_id": GROUP}, blocking=True
    )
    await demo_hass.async_block_till_done()

    for member in (ONE, TWO):
        assert (await _get(demo_hass, member))["effective_priority"] == PRI_DEFAULT
        assert demo_hass.states.get(member).state == "on"
