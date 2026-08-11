"""The user-facing surfaces: service descriptions and logbook entries."""

from __future__ import annotations

from homeassistant.const import EVENT_LOGBOOK_ENTRY
from homeassistant.core import callback
from homeassistant.helpers.service import async_get_all_descriptions
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.priority.const import (
    ATTR_PRIORITY,
    ATTR_PRIORITY_TTL,
    DOMAIN,
    PRI_AUTO,
    PRI_MANUAL,
)

LIGHT = "light.one"


# ----------------------------------------------------------------------
# Service descriptions - what makes the fields appear in the UI
# ----------------------------------------------------------------------


async def test_priority_fields_appear_in_service_descriptions(
    priority_entry, demo_hass
) -> None:
    """Without this, priority is YAML-only and no form ever offers it."""
    descriptions = await async_get_all_descriptions(demo_hass)
    fields = descriptions["light"]["turn_on"]["fields"]

    assert ATTR_PRIORITY in fields
    assert ATTR_PRIORITY_TTL in fields
    assert fields[ATTR_PRIORITY]["selector"]["select"]["mode"] == "dropdown"
    assert fields[ATTR_PRIORITY_TTL]["selector"] == {"duration": {}}

    labels = [
        o["label"] for o in fields[ATTR_PRIORITY]["selector"]["select"]["options"]
    ]
    assert labels == [
        "1 - Manual Emergency",
        "2 - Automatic Emergency",
        "3 - Manual",
        "4 - Automatic",
        "5 - Default",
    ]


async def test_descriptions_cover_every_wrapped_service(
    priority_entry, demo_hass
) -> None:
    """Consistency: if a service is arbitrated, its form must offer priority."""
    descriptions = await async_get_all_descriptions(demo_hass)
    for domain, service in priority_entry.runtime_data.async_originals():
        fields = (descriptions.get(domain, {}).get(service) or {}).get("fields", {})
        assert ATTR_PRIORITY in fields, f"{domain}.{service} missing priority"


async def test_existing_fields_are_preserved(priority_entry, demo_hass) -> None:
    """Patching a description must not eat the integration's own fields."""
    descriptions = await async_get_all_descriptions(demo_hass)
    fields = descriptions["light"]["turn_on"]["fields"]
    assert "brightness" in fields or "brightness_pct" in fields
    assert descriptions["climate"]["set_temperature"]["fields"].get("temperature")


async def test_descriptions_restored_on_unload(demo_hass, entry_options) -> None:
    """Unload must leave the frontend exactly as it found it."""
    before = (await async_get_all_descriptions(demo_hass))["light"]["turn_on"]
    assert ATTR_PRIORITY not in before.get("fields", {})

    entry = MockConfigEntry(domain=DOMAIN, options=entry_options, unique_id=DOMAIN)
    entry.add_to_hass(demo_hass)
    assert await demo_hass.config_entries.async_setup(entry.entry_id)
    await demo_hass.async_block_till_done()
    assert ATTR_PRIORITY in (await async_get_all_descriptions(demo_hass))["light"][
        "turn_on"
    ]["fields"]

    assert await demo_hass.config_entries.async_unload(entry.entry_id)
    await demo_hass.async_block_till_done()

    after = (await async_get_all_descriptions(demo_hass))["light"]["turn_on"]
    assert ATTR_PRIORITY not in after.get("fields", {})
    assert ATTR_PRIORITY_TTL not in after.get("fields", {})


# ----------------------------------------------------------------------
# Logbook - what makes the level visible in the device's own history
# ----------------------------------------------------------------------


async def test_logbook_entries_for_override_lifecycle(
    priority_entry, demo_hass
) -> None:
    """Taking and releasing an override both land in the entity's logbook."""
    entries: list[dict] = []

    @callback
    def _capture(event) -> None:
        entries.append(dict(event.data))

    demo_hass.bus.async_listen(EVENT_LOGBOOK_ENTRY, _capture)

    await demo_hass.services.async_call(
        "light",
        "turn_on",
        {"entity_id": LIGHT, "priority": PRI_MANUAL},
        blocking=True,
    )
    await demo_hass.async_block_till_done()

    assert len(entries) == 1
    assert entries[0]["entity_id"] == LIGHT
    assert "Manual" in entries[0]["message"]

    await demo_hass.services.async_call(
        DOMAIN,
        "relinquish",
        {"entity_id": LIGHT, "priority": PRI_MANUAL},
        blocking=True,
    )
    await demo_hass.async_block_till_done()

    assert len(entries) == 2
    assert "released" in entries[1]["message"]


async def test_default_traffic_writes_no_logbook_entries(
    priority_entry, demo_hass
) -> None:
    """The no-op guarantee, applied to the logbook.

    Every command reaches the wrapper. If ordinary Default-level traffic logged,
    every light toggle in the house would get a duplicate logbook line.
    """
    entries: list[dict] = []
    demo_hass.bus.async_listen(
        EVENT_LOGBOOK_ENTRY, callback(lambda e: entries.append(dict(e.data)))
    )

    for _ in range(4):
        await demo_hass.services.async_call(
            "light", "turn_on", {"entity_id": LIGHT}, blocking=True
        )
        await demo_hass.services.async_call(
            "light", "turn_off", {"entity_id": LIGHT}, blocking=True
        )
    await demo_hass.async_block_till_done()

    assert entries == []


async def test_lease_is_named_in_the_logbook(priority_entry, demo_hass) -> None:
    """An override with a lease should say so where people will read it."""
    entries: list[dict] = []
    demo_hass.bus.async_listen(
        EVENT_LOGBOOK_ENTRY, callback(lambda e: entries.append(dict(e.data)))
    )

    await demo_hass.services.async_call(
        "light",
        "turn_on",
        {"entity_id": LIGHT, "priority": PRI_AUTO, "priority_ttl": 1800},
        blocking=True,
    )
    await demo_hass.async_block_till_done()

    assert len(entries) == 1
    assert "0:30:00" in entries[0]["message"]
