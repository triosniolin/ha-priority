"""Another integration registering a proxy over our wrapper, as Adaptive Lighting does (issue #1)."""

from __future__ import annotations

from homeassistant.core import ServiceCall

from custom_components.priority.const import DOMAIN, PRI_AUTO, PRI_MANUAL

LIGHT = "light.one"


def _install_proxy(hass, domain: str, service: str) -> list[ServiceCall]:
    """Register a pass-through proxy over whatever currently handles the service."""
    below = hass.services.async_services_internal()[domain][service]
    seen: list[ServiceCall] = []

    async def _proxy(call: ServiceCall) -> None:
        seen.append(call)
        task = hass.async_run_hass_job(below.job, call)
        if task is not None:
            await task

    hass.services.async_register(domain, service, _proxy, schema=below.schema)
    return seen


async def test_a_proxy_registered_over_the_wrapper_does_not_recurse(
    demo_hass, priority_entry
) -> None:
    """Re-wrapping stored the proxy as our original, so wrapper and proxy called each other."""
    seen = _install_proxy(demo_hass, "light", "turn_on")
    await demo_hass.async_block_till_done()

    await demo_hass.services.async_call(
        "light", "turn_on", {"entity_id": LIGHT}, blocking=True
    )

    assert demo_hass.states.get(LIGHT).state == "on"
    assert len(seen) == 1


async def test_arbitration_still_holds_beneath_the_proxy(
    demo_hass, priority_entry
) -> None:
    _install_proxy(demo_hass, "light", "turn_on")
    _install_proxy(demo_hass, "light", "turn_off")
    await demo_hass.async_block_till_done()

    await demo_hass.services.async_call(
        "light", "turn_on", {"entity_id": LIGHT, "priority": PRI_MANUAL}, blocking=True
    )
    await demo_hass.services.async_call(
        "light", "turn_off", {"entity_id": LIGHT, "priority": PRI_AUTO}, blocking=True
    )
    assert demo_hass.states.get(LIGHT).state == "on"

    await demo_hass.services.async_call(
        DOMAIN, "relinquish", {"entity_id": LIGHT, "priority": PRI_MANUAL}, blocking=True
    )
    await demo_hass.async_block_till_done()
    assert demo_hass.states.get(LIGHT).state == "off"


async def test_a_registry_update_does_not_rewrap_under_the_proxy(
    demo_hass, priority_entry
) -> None:
    """0.1.6 re-runs async_wrap_all on every registry change, a second route to the loop."""
    from homeassistant.helpers import entity_registry as er

    _install_proxy(demo_hass, "light", "turn_on")
    er.async_get(demo_hass).async_update_entity(LIGHT, name="Renamed")
    await demo_hass.async_block_till_done()

    await demo_hass.services.async_call(
        "light", "turn_on", {"entity_id": LIGHT}, blocking=True
    )
    assert demo_hass.states.get(LIGHT).state == "on"


async def test_unloading_under_a_proxy_leaves_a_pass_through(
    demo_hass, priority_entry, caplog
) -> None:
    """The orphaned wrapper raised "lost the original handler" on every call."""
    _install_proxy(demo_hass, "light", "turn_on")
    assert await demo_hass.config_entries.async_unload(priority_entry.entry_id)
    await demo_hass.async_block_till_done()

    await demo_hass.services.async_call(
        "light", "turn_on", {"entity_id": LIGHT}, blocking=True
    )
    assert demo_hass.states.get(LIGHT).state == "on"
    assert "could not unwrap light.turn_on" in caplog.text
