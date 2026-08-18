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


async def test_card_url_is_content_fingerprinted(demo_hass) -> None:
    """The card URL must change when the card does.

    The static path is served with a month-long max-age, so a browser that has
    the file will not ask for it again. Without a fingerprint in the URL, a
    frontend fix reaches nobody until that expires - which is exactly what an
    updated integration reporting itself healthy looks like from the outside.
    """
    from custom_components.priority import _card_fingerprint, _card_path

    first = await demo_hass.async_add_executor_job(_card_fingerprint)
    assert first != "0", "the card must be readable"
    assert len(first) == 12
    assert first == await demo_hass.async_add_executor_job(_card_fingerprint), (
        "same bytes must give the same URL, or every restart busts every cache"
    )

    path = _card_path()
    original = path.read_bytes()
    try:
        path.write_bytes(original + b"\n// changed\n")
        assert await demo_hass.async_add_executor_job(_card_fingerprint) != first
    finally:
        path.write_bytes(original)


async def test_card_url_registered_is_the_one_removed(
    demo_hass, entry_options, monkeypatch
) -> None:
    """Unregistering has to name the exact URL that was added.

    `remove_extra_js_url` matches on the string. Now that the URL carries a
    fingerprint, removing the bare path would silently do nothing and leave the
    card loading on every page until the next restart.
    """
    from homeassistant.components import frontend

    # The rig has no frontend component, and the registration path deliberately
    # no-ops without one. Stand in for it so the add/remove pairing is actually
    # exercised rather than skipped.
    demo_hass.config.components.add("frontend")

    class _Http:
        def __init__(self) -> None:
            self.paths: list = []

        async def async_register_static_paths(self, configs) -> None:
            self.paths.extend(configs)

    monkeypatch.setattr(demo_hass, "http", _Http(), raising=False)

    added: list[str] = []
    removed: list[str] = []
    monkeypatch.setattr(
        frontend, "add_extra_js_url", lambda hass, url: added.append(url)
    )
    monkeypatch.setattr(
        frontend, "remove_extra_js_url", lambda hass, url: removed.append(url)
    )

    entry = MockConfigEntry(domain=DOMAIN, options=entry_options, unique_id=DOMAIN)
    entry.add_to_hass(demo_hass)
    assert await demo_hass.config_entries.async_setup(entry.entry_id)
    await demo_hass.async_block_till_done()

    assert added, "the card was never registered, so nothing here was tested"
    assert "?v=" in added[0], f"card URL is not cache-busted: {added[0]}"

    assert await demo_hass.config_entries.async_unload(entry.entry_id)
    await demo_hass.async_block_till_done()
    assert removed == added, "the URL removed must be the URL that was added"
