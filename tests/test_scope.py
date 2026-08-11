"""Scope: what "on" applies to, and what stays untouched."""

from __future__ import annotations

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.priority.const import (
    CONF_EXCLUDED_ENTITIES,
    CONF_MANAGED_ENTITIES,
    CONF_SCOPE,
    DOMAIN,
    PRI_AUTO,
    PRI_MANUAL,
    SCOPE_ALL,
    SCOPE_SELECTED,
)

LIGHT = "light.one"
OTHER = "light.two"


async def _entry(hass, options) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, options=options, unique_id=DOMAIN)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def _block(hass, entity_id: str) -> None:
    """Hold an entity at Manual, then try to move it at Automatic."""
    await hass.services.async_call(
        "light",
        "turn_on",
        {"entity_id": entity_id, "priority": PRI_MANUAL},
        blocking=True,
    )
    await hass.services.async_call(
        "light",
        "turn_off",
        {"entity_id": entity_id, "priority": PRI_AUTO},
        blocking=True,
    )
    await hass.async_block_till_done()


async def test_scope_all_covers_everything(demo_hass) -> None:
    await _entry(demo_hass, {CONF_SCOPE: SCOPE_ALL})
    await _block(demo_hass, LIGHT)
    assert demo_hass.states.get(LIGHT).state == "on"


async def test_excluded_entity_is_a_pure_passthrough(demo_hass) -> None:
    """The escape hatch has to be complete: no array, no arbitration."""
    entry = await _entry(
        demo_hass, {CONF_SCOPE: SCOPE_ALL, CONF_EXCLUDED_ENTITIES: [LIGHT]}
    )

    await _block(demo_hass, LIGHT)
    assert demo_hass.states.get(LIGHT).state == "off", "must not be arbitrated"
    assert entry.runtime_data.async_peek_array(LIGHT) is None

    await _block(demo_hass, OTHER)
    assert demo_hass.states.get(OTHER).state == "on", "others still arbitrated"


async def test_selected_scope_only_covers_the_selection(demo_hass) -> None:
    await _entry(
        demo_hass,
        {CONF_SCOPE: SCOPE_SELECTED, CONF_MANAGED_ENTITIES: [LIGHT]},
    )

    await _block(demo_hass, LIGHT)
    assert demo_hass.states.get(LIGHT).state == "on"

    await _block(demo_hass, OTHER)
    assert demo_hass.states.get(OTHER).state == "off"


async def test_mixed_call_splits_managed_and_unmanaged(demo_hass) -> None:
    """One call spanning both must behave correctly for each half."""
    await _entry(
        demo_hass, {CONF_SCOPE: SCOPE_ALL, CONF_EXCLUDED_ENTITIES: [OTHER]}
    )

    await demo_hass.services.async_call(
        "light",
        "turn_on",
        {"entity_id": LIGHT, "priority": PRI_MANUAL},
        blocking=True,
    )
    await demo_hass.async_block_till_done()

    await demo_hass.services.async_call(
        "light",
        "turn_off",
        {"entity_id": [LIGHT, OTHER], "priority": PRI_AUTO},
        blocking=True,
    )
    await demo_hass.async_block_till_done()

    assert demo_hass.states.get(LIGHT).state == "on", "arbitrated, PRI 3 holds"
    assert demo_hass.states.get(OTHER).state == "off", "excluded, command applied"


async def test_unsupported_domain_is_never_arbitrated(demo_hass) -> None:
    """Only domains with a command model are touched."""
    entry = await _entry(demo_hass, {CONF_SCOPE: SCOPE_ALL})
    manager = entry.runtime_data
    assert not manager.async_is_managed("sensor.anything")
    assert not manager.async_is_managed("binary_sensor.anything")
    assert manager.async_is_managed(LIGHT)


async def test_default_traffic_does_not_churn_the_diagnostic_sensor(
    demo_hass,
) -> None:
    """Ordinary commands must not push a state write per call.

    Every arbitrated command reaches the notify path. At house-wide scope,
    firing listeners unconditionally would write the diagnostic sensor - and a
    recorder row - for every light toggle in the house, to report a count that
    had not changed.
    """
    entry = await _entry(demo_hass, {CONF_SCOPE: SCOPE_ALL})
    seen: list[str] = []
    entry.runtime_data.async_add_listener(seen.append)

    for _ in range(5):
        await demo_hass.services.async_call(
            "light", "turn_on", {"entity_id": LIGHT}, blocking=True
        )
        await demo_hass.services.async_call(
            "light", "turn_off", {"entity_id": LIGHT}, blocking=True
        )
    await demo_hass.async_block_till_done()

    assert seen == [], "Default-level traffic must be silent"

    # A real override is still reported, and so is releasing it.
    await demo_hass.services.async_call(
        "light",
        "turn_on",
        {"entity_id": LIGHT, "priority": PRI_MANUAL},
        blocking=True,
    )
    await demo_hass.async_block_till_done()
    assert seen == [LIGHT]
    assert entry.runtime_data.async_overridden() == frozenset({LIGHT})

    await demo_hass.services.async_call(
        DOMAIN, "relinquish_all", {"entity_id": LIGHT}, blocking=True
    )
    await demo_hass.async_block_till_done()
    assert entry.runtime_data.async_overridden() == frozenset()
    assert len(seen) == 2


async def test_options_update_reapplies_scope(demo_hass) -> None:
    """Changing the exclude list must take effect without a restart."""
    entry = await _entry(demo_hass, {CONF_SCOPE: SCOPE_ALL})
    await _block(demo_hass, LIGHT)
    assert demo_hass.states.get(LIGHT).state == "on"

    demo_hass.config_entries.async_update_entry(
        entry, options={CONF_SCOPE: SCOPE_ALL, CONF_EXCLUDED_ENTITIES: [OTHER]}
    )
    await demo_hass.async_block_till_done()

    await _block(demo_hass, OTHER)
    assert demo_hass.states.get(OTHER).state == "off"
