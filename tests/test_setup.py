"""Setup, teardown, and the disabled-path guarantee."""

from __future__ import annotations

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.priority.const import DOMAIN
from custom_components.priority.service_wrapper import _WRAPPER_MARKER


def _handler(hass, domain, service):
    return hass.services.async_services_internal()[domain][service].job.target


def _is_wrapped(hass, domain, service) -> bool:
    return getattr(_handler(hass, domain, service), _WRAPPER_MARKER, False)


async def test_demo_entities_exist(demo_hass) -> None:
    """The fixture really did give us something to command."""
    assert demo_hass.states.async_entity_ids("light")
    assert demo_hass.states.async_entity_ids("switch")
    assert demo_hass.states.async_entity_ids("climate")
    assert demo_hass.states.async_entity_ids("cover")


async def test_services_are_untouched_before_setup(demo_hass) -> None:
    """The disabled path: no config entry means stock Home Assistant."""
    assert not _is_wrapped(demo_hass, "light", "turn_on")
    assert not demo_hass.services.has_service(DOMAIN, "relinquish")


async def test_setup_wraps_and_unload_restores(demo_hass, entry_options) -> None:
    """Wrapping is reversible: unload must put the original handlers back."""
    original = _handler(demo_hass, "light", "turn_on")

    entry = MockConfigEntry(domain=DOMAIN, options=entry_options, unique_id=DOMAIN)
    entry.add_to_hass(demo_hass)
    assert await demo_hass.config_entries.async_setup(entry.entry_id)
    await demo_hass.async_block_till_done()

    assert _is_wrapped(demo_hass, "light", "turn_on")
    assert _is_wrapped(demo_hass, "climate", "set_temperature")
    assert demo_hass.services.has_service(DOMAIN, "relinquish")

    assert await demo_hass.config_entries.async_unload(entry.entry_id)
    await demo_hass.async_block_till_done()

    assert not _is_wrapped(demo_hass, "light", "turn_on")
    assert _handler(demo_hass, "light", "turn_on") is original
    assert not demo_hass.services.has_service(DOMAIN, "relinquish")


async def test_wrapper_is_not_double_wrapped(priority_entry, demo_hass) -> None:
    """Reloading must not stack wrappers on top of each other."""
    from custom_components.priority.service_wrapper import async_wrap_service

    manager = priority_entry.runtime_data
    first = _handler(demo_hass, "light", "turn_on")
    assert not async_wrap_service(demo_hass, manager, "light", "turn_on")
    assert _handler(demo_hass, "light", "turn_on") is first


async def test_original_schema_still_validates(priority_entry, demo_hass) -> None:
    """Wrapping must not weaken an integration's own validation."""
    import voluptuous as vol
    import pytest

    entity_id = demo_hass.states.async_entity_ids("light")[0]
    with pytest.raises(vol.Invalid):
        await demo_hass.services.async_call(
            "light",
            "turn_on",
            {"entity_id": entity_id, "brightness": "not-a-number"},
            blocking=True,
        )


async def test_priority_field_is_accepted_and_bounded(priority_entry, demo_hass) -> None:
    """The new field validates, and out-of-range values are rejected."""
    import voluptuous as vol
    import pytest

    entity_id = demo_hass.states.async_entity_ids("light")[0]
    await demo_hass.services.async_call(
        "light", "turn_on", {"entity_id": entity_id, "priority": 3}, blocking=True
    )
    with pytest.raises(vol.Invalid):
        await demo_hass.services.async_call(
            "light", "turn_on", {"entity_id": entity_id, "priority": 9}, blocking=True
        )
