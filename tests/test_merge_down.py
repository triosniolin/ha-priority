"""Per-attribute merging, and staying outside an Adaptive Lighting style proxy (issue #5)."""

from __future__ import annotations

from datetime import timedelta

import pytest
from homeassistant.core import ServiceCall
from homeassistant.util import dt as dt_util
from homeassistant.util.read_only_dict import ReadOnlyDict
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.priority.array import Slot
from custom_components.priority.const import (
    CONF_MERGE_DOWN,
    CONF_MERGE_ENABLED,
    CONF_SCOPE,
    DOMAIN,
    PRI_AUTO,
    PRI_DEFAULT,
    PRI_MANUAL,
    PRI_SCHEDULED,
    SCOPE_ALL,
)
from custom_components.priority.merge import is_off, merged_data

from . import mocks

LIGHT = "light.one"
WARM = (30.0, 60.0)
COOL = (220.0, 40.0)


def _slot(service="turn_on", merge_down=None, domain="light", **data) -> Slot:
    return Slot(
        domain=domain,
        service=service,
        data=data,
        written_at=dt_util.utcnow(),
        merge_down=merge_down,
    )


def _light():
    return mocks.ENTITIES["light"][0]


async def _call(hass, service="turn_on", **data) -> None:
    await hass.services.async_call(
        "light", service, {"entity_id": LIGHT, **data}, blocking=True
    )
    await hass.async_block_till_done()


async def _setup(hass, **options) -> MockConfigEntry:
    """Merging enabled unless a test says otherwise; the default is the separate option."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        options={CONF_SCOPE: SCOPE_ALL, CONF_MERGE_ENABLED: True, **options},
        unique_id=DOMAIN,
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


# ---- merged_data ----


def test_off_by_default() -> None:
    assert merged_data(_slot(), [_slot(hs_color=WARM)], False) is None


def test_the_option_fills_unset_groups() -> None:
    assert merged_data(_slot(), [_slot(hs_color=WARM, brightness=90)], True) == {
        "hs_color": WARM,
        "brightness": 90,
    }


def test_the_call_overrides_the_option() -> None:
    below = [_slot(hs_color=WARM)]
    assert merged_data(_slot(merge_down=True), below, False) == {"hs_color": WARM}
    assert merged_data(_slot(merge_down=False), below, True) is None


def test_issue_example_groups_claim_their_synonyms() -> None:
    winner = _slot(rgb_color=(255, 0, 0))
    below = [
        _slot(brightness=50),
        _slot(brightness_pct=50, color_temp_kelvin=5000),
    ]
    assert merged_data(winner, below, True) == {
        "rgb_color": (255, 0, 0),
        "brightness": 50,
    }


def test_off_slots_contribute_nothing() -> None:
    below = [
        _slot("turn_off"),
        _slot(brightness=0, hs_color=COOL),
        _slot(brightness_pct="0"),
        _slot(white=0),
        _slot(hs_color=WARM),
    ]
    assert merged_data(_slot(), below, True) == {"hs_color": WARM}


def test_zero_brightness_is_off() -> None:
    assert is_off(_slot(brightness=0))
    assert is_off(_slot(brightness_pct=0.0))
    assert not is_off(_slot(brightness=1))
    assert not is_off(_slot(white=True))


def test_an_off_winner_does_not_merge() -> None:
    assert merged_data(_slot("turn_off"), [_slot(hs_color=WARM)], True) is None
    assert merged_data(_slot(brightness=0), [_slot(hs_color=WARM)], True) is None


def test_an_unvetted_attribute_stops_the_walk() -> None:
    below = [_slot(brightness=40), _slot(effect="colorloop", hs_color=COOL), _slot(hs_color=WARM)]
    assert merged_data(_slot(), below, True) == {"brightness": 40}
    assert merged_data(_slot(effect="colorloop"), [_slot(hs_color=WARM)], True) is None


def test_a_relative_step_stops_the_walk() -> None:
    below = [_slot(brightness_step_pct=-100), _slot(hs_color=WARM)]
    assert merged_data(_slot(), below, True) is None


def test_explicit_false_below_contributes_then_stops() -> None:
    below = [_slot(brightness=40, merge_down=False), _slot(hs_color=WARM)]
    assert merged_data(_slot(), below, True) == {"brightness": 40}


def test_white_needs_both_groups_free() -> None:
    assert merged_data(_slot(hs_color=COOL), [_slot(white=120)], True) is None
    assert merged_data(_slot(), [_slot(white=120)], True) == {"white": 120}


def test_a_service_without_a_table_never_merges() -> None:
    winner = _slot("set_cover_position", domain="cover", position=40)
    below = [_slot("set_cover_position", domain="cover", position=90, tilt_position=10)]
    assert merged_data(winner, below, True) is None


def test_only_the_same_service_contributes() -> None:
    winner = _slot("set_temperature", domain="climate", temperature=72)
    below = [
        _slot("set_hvac_mode", domain="climate", hvac_mode="cool"),
        _slot("set_temperature", domain="climate", temperature=65, hvac_mode="heat"),
    ]
    assert merged_data(winner, below, True) == {"temperature": 72, "hvac_mode": "heat"}


def test_each_domain_has_its_own_off() -> None:
    climate = _slot("set_temperature", domain="climate", temperature=72)
    shed = [_slot("set_temperature", domain="climate", temperature=60, hvac_mode="off")]
    assert merged_data(climate, shed, True) is None

    fan = _slot(domain="fan")
    assert merged_data(fan, [_slot(domain="fan", percentage=0)], True) is None
    assert merged_data(fan, [_slot(domain="fan", preset_mode="sleep")], True) == {
        "preset_mode": "sleep"
    }
    assert merged_data(_slot(domain="fan", percentage=30), [_slot(domain="fan", preset_mode="sleep")], True) is None


def test_slot_storage_round_trip() -> None:
    assert Slot.from_dict(_slot(merge_down=True).as_dict()).merge_down is True
    plain = _slot().as_dict()
    assert "merge_down" not in plain
    assert Slot.from_dict(plain).merge_down is None


# ---- through Home Assistant ----


async def test_a_bare_override_keeps_lower_colour_changes(demo_hass) -> None:
    await _setup(demo_hass, **{CONF_MERGE_DOWN: True})
    await _call(demo_hass, priority=PRI_MANUAL)
    await _call(demo_hass, hs_color=WARM, brightness=90)

    state = demo_hass.states.get(LIGHT)
    assert state.state == "on"
    assert tuple(state.attributes["hs_color"]) == WARM
    assert state.attributes["brightness"] == 90

    # A lower turn_off still cannot turn it off.
    await _call(demo_hass, "turn_off", priority=PRI_AUTO)
    assert demo_hass.states.get(LIGHT).state == "on"


async def test_without_the_option_the_override_takes_the_whole_light(demo_hass) -> None:
    await _setup(demo_hass)
    await _call(demo_hass, priority=PRI_MANUAL)
    await _call(demo_hass, hs_color=WARM)
    assert demo_hass.states.get(LIGHT).attributes["hs_color"] is None


async def test_per_call_flag_and_what_the_override_names_wins(demo_hass) -> None:
    await _setup(demo_hass)
    await _call(demo_hass, priority=PRI_MANUAL, merge_down=True, brightness=20)
    await _call(demo_hass, priority=PRI_SCHEDULED, hs_color=WARM, brightness=200)

    state = demo_hass.states.get(LIGHT)
    assert tuple(state.attributes["hs_color"]) == WARM
    assert state.attributes["brightness"] == 20
    assert "merge_down" not in _light().turn_on_calls[-1]


async def test_a_lower_write_that_changes_nothing_is_not_sent(demo_hass) -> None:
    await _setup(demo_hass, **{CONF_MERGE_DOWN: True})
    await _call(demo_hass, priority=PRI_MANUAL, hs_color=COOL, brightness=20)
    before = len(_light().turn_on_calls)
    await _call(demo_hass, hs_color=WARM, brightness=200)
    assert len(_light().turn_on_calls) == before


async def test_releasing_a_contributing_level_redrives(demo_hass) -> None:
    await _setup(demo_hass, **{CONF_MERGE_DOWN: True})
    await _call(demo_hass, priority=PRI_MANUAL)
    await _call(demo_hass, hs_color=COOL)
    await _call(demo_hass, priority=PRI_SCHEDULED, hs_color=WARM)
    assert tuple(demo_hass.states.get(LIGHT).attributes["hs_color"]) == WARM

    await demo_hass.services.async_call(
        DOMAIN, "relinquish", {"entity_id": LIGHT, "priority": PRI_SCHEDULED}, blocking=True
    )
    await demo_hass.async_block_till_done()
    assert tuple(demo_hass.states.get(LIGHT).attributes["hs_color"]) == COOL


async def test_a_contributing_lease_expiring_redrives(demo_hass, freezer) -> None:
    await _setup(demo_hass, **{CONF_MERGE_DOWN: True})
    await _call(demo_hass, priority=PRI_MANUAL)
    await _call(demo_hass, hs_color=COOL)
    await _call(demo_hass, priority=PRI_SCHEDULED, priority_ttl=60, hs_color=WARM)
    assert tuple(demo_hass.states.get(LIGHT).attributes["hs_color"]) == WARM

    freezer.tick(timedelta(seconds=61))
    async_fire_time_changed(demo_hass)
    await demo_hass.async_block_till_done()
    assert tuple(demo_hass.states.get(LIGHT).attributes["hs_color"]) == COOL


async def test_set_service_takes_the_flag(demo_hass) -> None:
    await _setup(demo_hass)
    await demo_hass.services.async_call(
        DOMAIN,
        "set",
        {"entity_id": LIGHT, "priority": PRI_MANUAL, "service": "turn_on", "merge_down": True},
        blocking=True,
    )
    await _call(demo_hass, hs_color=WARM)
    assert tuple(demo_hass.states.get(LIGHT).attributes["hs_color"]) == WARM


async def test_every_domain_takes_the_field(demo_hass) -> None:
    await _setup(demo_hass)
    await demo_hass.services.async_call(
        "switch", "turn_on", {"entity_id": "switch.one", "merge_down": True}, blocking=True
    )
    await demo_hass.services.async_call(
        "fan", "set_percentage", {"entity_id": "fan.one", "percentage": 40, "merge_down": True},
        blocking=True,
    )
    assert demo_hass.states.get("switch.one").state == "on"
    assert demo_hass.states.get("fan.one").attributes["percentage"] == 40


async def test_a_thermostat_hold_keeps_the_mode_set_below(demo_hass) -> None:
    await _setup(demo_hass, **{CONF_MERGE_DOWN: True})
    climate = "climate.one"

    async def _set(priority=None, **data):
        payload = {"entity_id": climate, **data}
        if priority is not None:
            payload["priority"] = priority
        await demo_hass.services.async_call("climate", "set_temperature", payload, blocking=True)
        await demo_hass.async_block_till_done()

    await _set(PRI_MANUAL, temperature=72)
    await _set(temperature=65, hvac_mode="heat")
    state = demo_hass.states.get(climate)
    assert state.state == "heat"
    assert state.attributes["temperature"] == 72

    # A lower shed that would turn it off contributes nothing.
    await _set(PRI_AUTO, temperature=60, hvac_mode="off")
    state = demo_hass.states.get(climate)
    assert state.state == "heat"
    assert state.attributes["temperature"] == 72


async def test_a_merge_the_schema_refuses_sends_the_winner_alone(demo_hass) -> None:
    await _setup(demo_hass, **{CONF_MERGE_DOWN: True})
    await _call(demo_hass, priority=PRI_MANUAL, hs_color=COOL)
    entry = demo_hass.config_entries.async_entries(DOMAIN)[0]
    array = entry.runtime_data.async_peek_array(LIGHT)
    # Written directly: no service call would accept it, which is the point.
    array.write(PRI_DEFAULT, _slot(brightness="bright"))
    command = entry.runtime_data.async_command(LIGHT)
    assert "brightness" not in command.data


# ---- an Adaptive Lighting style proxy ----


def _install_adapter(hass) -> dict:
    """Register a proxy that, like Adaptive Lighting, edits the validated `params` of a turn_on
    for a light that is off, then calls whatever it captured directly."""
    registry = hass.services.async_services_internal()
    below = registry["light"]["turn_on"]
    handle = {"below": below, "seen": 0}

    async def _proxy(call: ServiceCall) -> None:
        handle["seen"] += 1
        state = hass.states.get(LIGHT)
        if state is not None and state.state == "off":
            data = dict(call.data)
            params = dict(data.get("params") or {})
            params.setdefault("hs_color", WARM)
            data["params"] = params
            call.data = ReadOnlyDict(data)
        await below.job.target(call)

    hass.services.async_register("light", "turn_on", _proxy, below.schema)
    handle["proxy"] = _proxy
    return handle


def _held_data(hass, priority: int) -> dict:
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    return entry.runtime_data.async_peek_array(LIGHT).get(priority).data


@pytest.mark.parametrize("adapter_first", [True, False])
async def test_the_adapter_works_in_either_load_order(demo_hass, adapter_first) -> None:
    if adapter_first:
        handle = _install_adapter(demo_hass)
        await _setup(demo_hass, **{CONF_MERGE_DOWN: True})
    else:
        await _setup(demo_hass, **{CONF_MERGE_DOWN: True})
        handle = _install_adapter(demo_hass)
        await demo_hass.async_block_till_done()

    await _call(demo_hass, priority=PRI_MANUAL)

    # The adaptation reaches the light, and the slot keeps only what the caller asked for.
    assert tuple(demo_hass.states.get(LIGHT).attributes["hs_color"]) == WARM
    assert _held_data(demo_hass, PRI_MANUAL) == {}
    assert handle["seen"] == 1

    # Arbitration still holds with the adapter in the chain.
    await _call(demo_hass, "turn_off", priority=PRI_AUTO)
    assert demo_hass.states.get(LIGHT).state == "on"
    await _call(demo_hass, hs_color=COOL)
    assert tuple(demo_hass.states.get(LIGHT).attributes["hs_color"]) == COOL


async def test_an_adapter_that_removes_itself_hands_back_to_us(demo_hass) -> None:
    await _setup(demo_hass)
    handle = _install_adapter(demo_hass)
    await demo_hass.async_block_till_done()

    below = handle["below"]
    demo_hass.services.async_register("light", "turn_on", below.job.target, below.schema)
    await demo_hass.async_block_till_done()

    await _call(demo_hass, priority=PRI_MANUAL)
    before = len(_light().turn_on_calls)
    await _call(demo_hass, priority=PRI_DEFAULT, hs_color=WARM)
    assert len(_light().turn_on_calls) == before
    await demo_hass.services.async_call(
        DOMAIN, "relinquish", {"entity_id": LIGHT, "priority": PRI_MANUAL}, blocking=True
    )
    await demo_hass.async_block_till_done()
    assert len(_light().turn_on_calls) == before + 1
    assert handle["seen"] == 0


async def test_unloading_leaves_the_adapter_working(demo_hass) -> None:
    entry = await _setup(demo_hass)
    handle = _install_adapter(demo_hass)
    await demo_hass.async_block_till_done()
    assert await demo_hass.config_entries.async_unload(entry.entry_id)
    await demo_hass.async_block_till_done()

    current = demo_hass.services.async_services_internal()["light"]["turn_on"]
    assert current.job.target is handle["proxy"]
    await _call(demo_hass)
    assert tuple(demo_hass.states.get(LIGHT).attributes["hs_color"]) == WARM


async def test_group_members_inherit_the_flag(demo_hass) -> None:
    from homeassistant.setup import async_setup_component

    assert await async_setup_component(demo_hass, "group", {})
    group = MockConfigEntry(
        domain="group",
        options={
            "group_type": "light",
            "name": "Room",
            "entities": [LIGHT, "light.two"],
            "hide_members": False,
            "all": False,
        },
        title="Room",
    )
    group.add_to_hass(demo_hass)
    assert await demo_hass.config_entries.async_setup(group.entry_id)
    await demo_hass.async_block_till_done()
    await _setup(demo_hass)

    await demo_hass.services.async_call(
        "light",
        "turn_on",
        {"entity_id": "light.room", "priority": PRI_MANUAL, "merge_down": True},
        blocking=True,
    )
    await demo_hass.async_block_till_done()
    entry = demo_hass.config_entries.async_entries(DOMAIN)[0]
    assert entry.runtime_data.async_peek_array(LIGHT).get(PRI_MANUAL).merge_down is True

    await _call(demo_hass, hs_color=WARM)
    assert tuple(demo_hass.states.get(LIGHT).attributes["hs_color"]) == WARM


async def test_disabled_is_a_kill_switch(demo_hass) -> None:
    from homeassistant.helpers.service import async_get_all_descriptions

    await _setup(demo_hass, **{CONF_MERGE_ENABLED: False, CONF_MERGE_DOWN: True})
    await _call(demo_hass, priority=PRI_MANUAL, merge_down=True)
    await _call(demo_hass, hs_color=WARM)
    assert demo_hass.states.get(LIGHT).attributes["hs_color"] is None

    attrs = demo_hass.states.get("sensor.active_overrides").attributes
    assert attrs["merge_services"] == {}
    fields = (await async_get_all_descriptions(demo_hass))["light"]["turn_on"]["fields"]
    assert "priority" in fields
    assert "merge_down" not in fields


async def test_the_sensor_tells_the_cards_what_can_merge(demo_hass) -> None:
    entry = await _setup(demo_hass)
    attrs = demo_hass.states.get("sensor.active_overrides").attributes
    assert attrs["merge_down"] is False
    assert attrs["merge_services"]["light"] == ["turn_on"]
    assert "switch" not in attrs["merge_services"]

    demo_hass.config_entries.async_update_entry(
        entry, options={**entry.options, CONF_MERGE_DOWN: True}
    )
    await demo_hass.async_block_till_done()
    assert demo_hass.states.get("sensor.active_overrides").attributes["merge_down"] is True

    demo_hass.config_entries.async_update_entry(
        entry, options={**entry.options, CONF_MERGE_ENABLED: False}
    )
    await demo_hass.async_block_till_done()
    assert demo_hass.states.get("sensor.active_overrides").attributes["merge_services"] == {}
