"""Priority command arbitration for Home Assistant.

A BACnet-style priority array (ASHRAE 135) trimmed to five levels:

    1  Manual Emergency
    2  Automatic Emergency
    3  Manual
    4  Automatic
    5  Default             <- everything, unless the caller says otherwise

The lowest-numbered occupied slot drives the device, and clearing one re-issues
the next as it stands now rather than restoring a snapshot. Same-level writes
replace each other, so until a call names a level the house behaves exactly
like stock Home Assistant.
"""

from __future__ import annotations

import hashlib
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
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from .const import ARBITRATED_SERVICES
from .descriptions import async_patch_descriptions, async_restore_descriptions
from .observer import async_start_observer
from .reconcile import async_schedule_startup_reconcile
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
# A static path cannot be unregistered, so it outlives the module URL across reloads.
_STATIC_REGISTERED = "priority_static_registered"


def _card_path() -> pathlib.Path:
    return pathlib.Path(__file__).parent / "frontend" / "priority-card.js"


def _card_fingerprint() -> str:
    """Content hash for the card URL; the static path is cached for 31 days. Blocking I/O."""
    try:
        return hashlib.sha256(_card_path().read_bytes()).hexdigest()[:12]
    except OSError:
        return "0"

type PriorityConfigEntry = ConfigEntry[PriorityManager]


async def async_setup_entry(hass: HomeAssistant, entry: PriorityConfigEntry) -> bool:
    manager = PriorityManager(hass, dict(entry.options))
    await manager.async_load()
    entry.runtime_data = manager

    # Integrations that load after us would otherwise escape arbitration.
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
        # A label or area change can bring in the first entity of a domain.
        async_wrap_all(hass, manager)

    entry.async_on_unload(
        hass.bus.async_listen(EVENT_SERVICE_REGISTERED, _on_service_registered)
    )
    entry.async_on_unload(
        hass.bus.async_listen(EVENT_SERVICE_REMOVED, _on_service_removed)
    )
    entry.async_on_unload(
        hass.bus.async_listen(er.EVENT_ENTITY_REGISTRY_UPDATED, _on_registry_updated)
    )
    entry.async_on_unload(
        hass.bus.async_listen(dr.EVENT_DEVICE_REGISTRY_UPDATED, _on_registry_updated)
    )

    async_wrap_all(hass, manager)
    async_register_services(hass, manager)

    # Without this the fields are YAML-only; no form offers them.
    await async_patch_descriptions(hass, manager)
    await _async_register_frontend(hass)
    entry.async_on_unload(async_start_observer(hass, manager))
    entry.async_on_unload(async_schedule_startup_reconcile(hass, manager))
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
    """Serve the cards as an extra frontend module, so no Lovelace resource step is needed."""
    if hass.data.get(_FRONTEND_REGISTERED):
        return
    # Arbitration must work headless; hass.http exists but is None without the http component.
    if "frontend" not in hass.config.components or getattr(hass, "http", None) is None:
        _LOGGER.debug("Priority: no frontend available, skipping card registration")
        return
    try:
        from homeassistant.components.frontend import add_extra_js_url
        from homeassistant.components.http import StaticPathConfig

        # Registering the same path twice raises, which would lose the card after a reload.
        if not hass.data.get(_STATIC_REGISTERED):
            await hass.http.async_register_static_paths(
                [StaticPathConfig(_CARD_URL, str(_card_path()), True)]
            )
            hass.data[_STATIC_REGISTERED] = True
        fingerprint = await hass.async_add_executor_job(_card_fingerprint)
        card_url = f"{_CARD_URL}?v={fingerprint}"
        add_extra_js_url(hass, card_url)
        # Unregistering needs the exact string that was added.
        hass.data[_FRONTEND_REGISTERED] = card_url
        _LOGGER.info("Priority registered its dashboard card at %s", card_url)
    except Exception:
        _LOGGER.exception("Priority could not register its dashboard card")


@callback
def _async_unregister_frontend(hass: HomeAssistant) -> None:
    """Drop the module URL on unload; the static path cannot be unregistered."""
    card_url = hass.data.pop(_FRONTEND_REGISTERED, None)
    if not card_url:
        return
    try:
        from homeassistant.components.frontend import remove_extra_js_url

        remove_extra_js_url(hass, card_url)
    except Exception:
        _LOGGER.debug("Priority could not unregister its card", exc_info=True)


async def _async_update_options(
    hass: HomeAssistant, entry: PriorityConfigEntry
) -> None:
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
    _async_unregister_frontend(hass)
    await manager.async_save()
    return True
