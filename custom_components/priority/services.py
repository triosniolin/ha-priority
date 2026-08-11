"""Services this integration offers: relinquish, set, get."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

import voluptuous as vol
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
    callback,
)
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import target as target_helpers

from .const import (
    ARBITRATED_SERVICES,
    ATTR_PRIORITY,
    ATTR_PRIORITY_TTL,
    DOMAIN,
    MAX_PRIORITY,
    MIN_PRIORITY,
    PRI_DEFAULT,
    PRIORITY_NAMES,
    SERVICE_GET,
    SERVICE_RELINQUISH,
    SERVICE_RELINQUISH_ALL,
    SERVICE_SET,
)
from .store import PriorityManager

_LOGGER = logging.getLogger(__name__)

_PRIORITY = vol.All(vol.Coerce(int), vol.Range(min=MIN_PRIORITY, max=MAX_PRIORITY))

_TTL = vol.Any(
    cv.positive_time_period, vol.All(vol.Coerce(float), vol.Range(min=0))
)

RELINQUISH_SCHEMA = cv.make_entity_service_schema(
    {vol.Required(ATTR_PRIORITY): _PRIORITY}
)
RELINQUISH_ALL_SCHEMA = cv.make_entity_service_schema({})
SET_SCHEMA = cv.make_entity_service_schema(
    {
        vol.Required(ATTR_PRIORITY): _PRIORITY,
        vol.Required("service"): cv.string,
        vol.Optional("data", default=dict): dict,
        vol.Optional(ATTR_PRIORITY_TTL): _TTL,
    }
)
GET_SCHEMA = cv.make_entity_service_schema({})


@callback
def _targets(hass: HomeAssistant, call: ServiceCall) -> list[str]:
    """Resolve the entities a call to one of our services refers to."""
    selection = target_helpers.TargetSelection(call.data)
    selected = target_helpers.async_extract_referenced_entity_ids(
        hass, selection, True
    )
    return sorted(selected.referenced | selected.indirectly_referenced)


def async_register_services(hass: HomeAssistant, manager: PriorityManager) -> None:
    """Register the priority domain services."""

    async def _relinquish(call: ServiceCall) -> None:
        """Clear one slot and hand control to whatever wins next."""
        priority = call.data[ATTR_PRIORITY]
        for entity_id in _targets(hass, call):
            array = manager.async_peek_array(entity_id)
            if array is None:
                continue
            was_in_control = array.effective_priority() == priority
            if not array.clear(priority):
                continue
            manager.async_cancel_timer(entity_id, priority)
            manager.async_notify(entity_id)
            if priority < MAX_PRIORITY:
                fell_to = array.effective_priority()
                manager.async_logbook(
                    entity_id,
                    f"{PRIORITY_NAMES[priority]} override released"
                    + (
                        f", returned to {PRIORITY_NAMES[fell_to]}"
                        if fell_to is not None
                        else ", no longer under priority control"
                    ),
                    call.context,
                )
            if was_in_control:
                # Exactly one dispatch: async_drive_effective invokes the
                # captured original handler, never the wrapper.
                await manager.async_drive_effective(entity_id, call.context)

    async def _relinquish_all(call: ServiceCall) -> None:
        """Clear every slot above Manual Low and re-drive what is left."""
        for entity_id in _targets(hass, call):
            array = manager.async_peek_array(entity_id)
            if array is None:
                continue
            changed = False
            for priority in range(MIN_PRIORITY, PRI_DEFAULT):
                if array.clear(priority):
                    manager.async_cancel_timer(entity_id, priority)
                    changed = True
            if not changed:
                continue
            manager.async_notify(entity_id)
            manager.async_logbook(
                entity_id,
                "all overrides released, no longer under priority control",
                call.context,
            )
            await manager.async_drive_effective(entity_id, call.context)

    async def _set(call: ServiceCall) -> None:
        """Write a slot directly, without going through a domain service."""
        priority = call.data[ATTR_PRIORITY]
        service = call.data["service"]
        data: dict[str, Any] = dict(call.data.get("data") or {})
        ttl_raw = call.data.get(ATTR_PRIORITY_TTL)
        ttl = (
            ttl_raw.total_seconds() if isinstance(ttl_raw, timedelta) else ttl_raw
        ) or None
        if ttl is not None and priority == MAX_PRIORITY:
            raise ServiceValidationError(
                "priority_ttl is not valid at priority 5 (Manual Low): it is the "
                "lowest level, so there is nothing for it to expire back to"
            )

        for entity_id in _targets(hass, call):
            if not manager.async_is_managed(entity_id):
                raise ServiceValidationError(
                    f"{entity_id} is not under priority arbitration"
                )
            domain = entity_id.split(".", 1)[0]
            if service not in ARBITRATED_SERVICES.get(domain, frozenset()):
                raise ServiceValidationError(
                    f"{domain}.{service} is not an arbitrated service"
                )
            resolved = manager.async_resolve_service(domain, service, entity_id)
            array = manager.async_get_array(entity_id)
            takes_control = array.wins(priority)
            manager.async_write_slot(
                entity_id,
                priority,
                manager.async_make_slot(domain, resolved, data, call.context, ttl),
            )
            manager.async_notify(entity_id)
            if takes_control:
                await manager.async_dispatch(
                    domain, resolved, [entity_id], data, priority, call.context
                )

    async def _get(call: ServiceCall) -> ServiceResponse:
        """Return the full array for each target, for templates and debugging."""
        result: dict[str, Any] = {}
        for entity_id in _targets(hass, call):
            array = manager.async_peek_array(entity_id)
            result[entity_id] = (
                array.as_dict()
                if array is not None
                else {
                    "entity_id": entity_id,
                    "effective_priority": None,
                    "effective_priority_name": None,
                    "effective_command": None,
                    "slots": {str(p): None for p in range(MIN_PRIORITY, MAX_PRIORITY + 1)},
                }
            )
        return {"arrays": result}

    hass.services.async_register(
        DOMAIN, SERVICE_RELINQUISH, _relinquish, schema=RELINQUISH_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_RELINQUISH_ALL, _relinquish_all, schema=RELINQUISH_ALL_SCHEMA
    )
    hass.services.async_register(DOMAIN, SERVICE_SET, _set, schema=SET_SCHEMA)
    hass.services.async_register(
        DOMAIN,
        SERVICE_GET,
        _get,
        schema=GET_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )


@callback
def async_unregister_services(hass: HomeAssistant) -> None:
    """Remove the priority domain services."""
    for service in (
        SERVICE_RELINQUISH,
        SERVICE_RELINQUISH_ALL,
        SERVICE_SET,
        SERVICE_GET,
    ):
        hass.services.async_remove(DOMAIN, service)
