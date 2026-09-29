"""Findings from an outside review, 2026-09-29.

Each of these was verified to fail against the code as it stood before the fix.
"""

from __future__ import annotations

import pytest
from homeassistant.core import Context, State
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import label_registry as lr
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.priority.commands import command_for_state, resolve_toggle
from custom_components.priority.const import (
    CONF_MANAGED_AREAS,
    CONF_MANAGED_LABELS,
    CONF_SCOPE,
    DOMAIN,
    PRI_MANUAL,
    SCOPE_SELECTED,
)

CLIMATE = "climate.one"
COVER = "cover.one"
LIGHT = "light.one"


async def _slot(hass, entity_id: str, priority: int) -> dict | None:
    resp = await hass.services.async_call(
        DOMAIN, "get", {"entity_id": entity_id}, blocking=True, return_response=True
    )
    array = resp["arrays"].get(entity_id)
    return array["slots"][str(priority)] if array else None


async def _entry(hass, options) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, options=options, unique_id=DOMAIN)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


# ----------------------------------------------------------------------
# toggle resolved as if every entity were on/off
# ----------------------------------------------------------------------


async def test_cover_toggle_at_a_priority_moves_the_cover(
    demo_hass, priority_entry
) -> None:
    """It resolved to cover.turn_on, raised, and left slot 3 holding it."""
    before = demo_hass.states.get(COVER).state

    await demo_hass.services.async_call(
        "cover", "toggle", {"entity_id": COVER, "priority": PRI_MANUAL}, blocking=True
    )
    await demo_hass.async_block_till_done()

    assert demo_hass.states.get(COVER).state != before
    assert (await _slot(demo_hass, COVER, PRI_MANUAL))["service"].endswith("_cover")


async def test_climate_toggle_turns_off_a_running_thermostat(
    demo_hass, priority_entry
) -> None:
    """ "cool" is not "on", so it resolved to turn_on and changed nothing."""
    await demo_hass.services.async_call(
        "climate",
        "set_hvac_mode",
        {"entity_id": CLIMATE, "hvac_mode": "cool"},
        blocking=True,
    )
    await demo_hass.services.async_call(
        "climate", "toggle", {"entity_id": CLIMATE}, blocking=True
    )
    await demo_hass.async_block_till_done()

    assert demo_hass.states.get(CLIMATE).state == "off"


@pytest.mark.parametrize(
    ("domain", "state", "features", "expected"),
    [
        ("media_player", "playing", 0, "turn_off"),
        ("media_player", "standby", 0, "turn_on"),
        ("climate", "heat", 0, "turn_off"),
        ("climate", "off", 0, "turn_on"),
        ("cover", "closed", 0, "open_cover"),
        ("cover", "open", 0, "close_cover"),
        ("cover", "closing", 0, "open_cover"),
        ("cover", "opening", 8, "stop_cover"),
        ("valve", "closing", 0, "close_valve"),
        ("valve", "opening", 8, "stop_valve"),
        ("light", "on", 0, "turn_off"),
        ("light", "off", 0, "turn_on"),
    ],
)
def test_toggle_resolves_the_way_core_toggles(
    domain, state, features, expected
) -> None:
    attrs = {"supported_features": features}
    assert resolve_toggle(domain, State(f"{domain}.x", state, attrs)) == expected


# ----------------------------------------------------------------------
# Out-of-band changes inferred as if every entity were on/off
# ----------------------------------------------------------------------


async def test_thermostat_changed_out_of_band_is_recorded_as_its_mode(
    demo_hass, priority_entry
) -> None:
    """ "heat" was recorded as turn_off, which a release would then re-drive."""
    demo_hass.states.async_set(CLIMATE, "heat", {}, context=Context())
    await demo_hass.async_block_till_done()

    slot = await _slot(demo_hass, CLIMATE, 5)
    assert slot["service"] == "set_hvac_mode"
    assert slot["data"] == {"hvac_mode": "heat"}


@pytest.mark.parametrize(
    ("domain", "state", "expected"),
    [
        ("media_player", "paused", ("turn_on", {})),
        ("media_player", "off", ("turn_off", {})),
        ("lock", "unlocked", ("unlock", {})),
        ("lock", "jammed", None),
        ("cover", "closed", ("close_cover", {})),
        ("cover", "opening", None),
        ("valve", "open", ("open_valve", {})),
        ("water_heater", "eco", ("set_operation_mode", {"operation_mode": "eco"})),
        ("water_heater", "off", ("turn_off", {})),
        ("light", "on", ("turn_on", {})),
    ],
)
def test_out_of_band_state_maps_to_a_real_command(domain, state, expected) -> None:
    assert command_for_state(domain, state) == expected


# ----------------------------------------------------------------------
# Label and area selection
# ----------------------------------------------------------------------


async def test_label_added_after_setup_brings_its_domain_under_arbitration(
    demo_hass,
) -> None:
    """The registry listener refreshed the cache but never wrapped the domain."""
    label = lr.async_get(demo_hass).async_create("held")
    await _entry(
        demo_hass, {CONF_SCOPE: SCOPE_SELECTED, CONF_MANAGED_LABELS: [label.label_id]}
    )

    er.async_get(demo_hass).async_update_entity(CLIMATE, labels={label.label_id})
    await demo_hass.async_block_till_done()

    await demo_hass.services.async_call(
        "climate",
        "set_hvac_mode",
        {"entity_id": CLIMATE, "hvac_mode": "heat", "priority": PRI_MANUAL},
        blocking=True,
    )
    assert (await _slot(demo_hass, CLIMATE, PRI_MANUAL))["service"] == "set_hvac_mode"


async def test_area_selection_includes_entities_that_follow_their_device(
    demo_hass,
) -> None:
    """Only an entity-level area was checked, and that is None when inherited."""
    area = ar.async_get(demo_hass).async_create("Kitchen")
    owner = MockConfigEntry(domain="mockdev")
    owner.add_to_hass(demo_hass)
    device = dr.async_get(demo_hass).async_get_or_create(
        config_entry_id=owner.entry_id, identifiers={("mockdev", "lamp")}
    )
    dr.async_get(demo_hass).async_update_device(device.id, area_id=area.id)
    er.async_get(demo_hass).async_update_entity(LIGHT, device_id=device.id)

    await _entry(demo_hass, {CONF_SCOPE: SCOPE_SELECTED, CONF_MANAGED_AREAS: [area.id]})

    await demo_hass.services.async_call(
        "light", "turn_on", {"entity_id": LIGHT, "priority": PRI_MANUAL}, blocking=True
    )
    assert (await _slot(demo_hass, LIGHT, PRI_MANUAL))["service"] == "turn_on"


def test_the_integration_is_offered_in_the_add_integration_list() -> None:
    """Core leaves "system" and "entity" custom integrations out of the picker entirely."""
    import json
    from pathlib import Path

    manifest = json.loads(
        (
            Path(__file__).parent.parent
            / "custom_components"
            / "priority"
            / "manifest.json"
        ).read_text()
    )
    assert manifest["integration_type"] not in ("system", "entity")

