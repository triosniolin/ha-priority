"""Priority command arbitration for Home Assistant.

Gives every command a level of authority, so a manual action and an automation
can disagree without one silently clobbering the other. Modelled on the BACnet
priority array (ASHRAE 135), trimmed from sixteen levels to five:

    1  Manual Emergency
    2  Automatic Emergency
    3  Manual
    4  Automatic
    5  Default             <- everything, unless the caller says otherwise

The lowest-numbered occupied slot drives the device. Clearing a slot hands
control back to the next one down, re-issuing that command as it stands right
now rather than restoring a stale snapshot - which is the failure mode every
hand-rolled capture-and-restore automation eventually hits.

Everything defaults to level 5, and writes at the same level simply replace
each other. So a house that never mentions priority behaves exactly like stock
Home Assistant: last command wins, and a person can always countermand an
automation through the UI.

That default matters more than it looks. Splitting the defaults - automations
at 4, people at 5 - is the faithful BACnet reading, but it is wrong here: Home
Assistant automations fire and forget, with no relinquish idiom, so slot 4
would fill up and never drain, and every entity an automation had ever touched
would go deaf to the app. Arbitration has to be something you opt into per
command, not something that accumulates behind your back.

Opt-in therefore holds at two levels: no config entry means nothing is wrapped
at all, and with one loaded nothing changes until a call actually asks for a
priority above 5.
"""

from __future__ import annotations

import logging
import pathlib

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    ATTR_DOMAIN,
    ATTR_SERVICE,
    EVENT_SERVICE_REGISTERED,
    EVENT_SERVICE_REMOVED,
    Platform,
)
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers import entity_registry as er

from .const import ARBITRATED_SERVICES
from .descriptions import async_patch_descriptions, async_restore_descriptions
from .observer import async_start_observer
from .service_wrapper import (
    async_unwrap_all,
    async_unwrap_service,
    async_wrap_all,
    async_wrap_service,
)
from .services import async_register_services, async_unregister_services
from .store import PriorityManager

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR]

_CARD_URL = "/priority_static/priority-card.js"
_FRONTEND_REGISTERED = "priority_frontend_registered"

type PriorityConfigEntry = ConfigEntry[PriorityManager]


async def async_setup_entry(hass: HomeAssistant, entry: PriorityConfigEntry) -> bool:
    """Set up priority arbitration from a config entry."""
    manager = PriorityManager(hass, dict(entry.options))
    await manager.async_load()
    entry.runtime_data = manager

    # Services registered by integrations that set up after us are wrapped when
    # the registry announces them. Without this, anything loading later than
    # this entry would escape arbitration entirely.
    @callback
    def _on_service_registered(event: Event) -> None:
        domain = event.data[ATTR_DOMAIN]
        service = event.data[ATTR_SERVICE]
        if service in ARBITRATED_SERVICES.get(domain, frozenset()) and (
            domain in manager.async_managed_domains()
        ):
            async_wrap_service(hass, manager, domain, service)

    @callback
    def _on_service_removed(event: Event) -> None:
        domain = event.data[ATTR_DOMAIN]
        service = event.data[ATTR_SERVICE]
        manager.async_forget_original(domain, service)

    @callback
    def _on_registry_updated(event: Event) -> None:
        manager.async_invalidate_managed_cache()

    entry.async_on_unload(
        hass.bus.async_listen(EVENT_SERVICE_REGISTERED, _on_service_registered)
    )
    entry.async_on_unload(
        hass.bus.async_listen(EVENT_SERVICE_REMOVED, _on_service_removed)
    )
    entry.async_on_unload(
        hass.bus.async_listen(er.EVENT_ENTITY_REGISTRY_UPDATED, _on_registry_updated)
    )

    async_wrap_all(hass, manager)
    async_register_services(hass, manager)

    # Make the fields real in the frontend. Without this the feature is
    # YAML-only: the schema accepts priority, but no form ever offers it.
    await async_patch_descriptions(hass, manager)
    await _async_register_frontend(hass)
    entry.async_on_unload(async_start_observer(hass, manager))
    entry.async_on_unload(entry.add_update_listener(_async_update_options))
    entry.async_on_unload(manager.async_shutdown_timers)

    manager.async_rearm_timers()

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    _LOGGER.info(
        "Priority arbitration active: scope=%s, wrapped %s services",
        manager.scope,
        len(manager.async_originals()),
    )
    return True


async def _async_register_frontend(hass: HomeAssistant) -> None:
    """Serve the override card and load it into the frontend automatically.

    Registering the JS as an extra frontend module rather than a Lovelace
    resource means there is no manual "add resource" step - the card is simply
    available in the card picker once the integration is set up.
    """
    if hass.data.get(_FRONTEND_REGISTERED):
        return
    # Arbitration must not depend on the frontend being present. A headless
    # instance, or a test rig without the compiled frontend package, still gets
    # a fully working integration - it just has no card.
    if "frontend" not in hass.config.components or not hasattr(hass, "http"):
        _LOGGER.debug("Priority: no frontend available, skipping card registration")
        return
    try:
        from homeassistant.components.frontend import add_extra_js_url
        from homeassistant.components.http import StaticPathConfig

        await hass.http.async_register_static_paths(
            [
                StaticPathConfig(
                    _CARD_URL,
                    str(
                        pathlib.Path(__file__).parent / "frontend" / "priority-card.js"
                    ),
                    True,
                )
            ]
        )
        add_extra_js_url(hass, _CARD_URL)
        hass.data[_FRONTEND_REGISTERED] = True
        _LOGGER.info("Priority registered its dashboard card at %s", _CARD_URL)
    except Exception:
        _LOGGER.exception("Priority could not register its dashboard card")


async def _async_update_options(
    hass: HomeAssistant, entry: PriorityConfigEntry
) -> None:
    """Re-apply options, wrapping or unwrapping services as scope changes."""
    manager = entry.runtime_data
    manager.async_update_options(dict(entry.options))

    wanted = manager.async_managed_domains()
    for domain, service in manager.async_originals():
        if domain not in wanted:
            async_unwrap_service(hass, manager, domain, service)
    async_wrap_all(hass, manager)


async def async_unload_entry(hass: HomeAssistant, entry: PriorityConfigEntry) -> bool:
    """Unload the entry and put every original service handler back."""
    manager = entry.runtime_data
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if not unloaded:
        return False
    async_restore_descriptions(hass, manager)
    async_unwrap_all(hass, manager)
    async_unregister_services(hass)
    await manager.async_save()
    return True
