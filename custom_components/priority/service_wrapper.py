"""Wrap arbitrated services by re-registering over them; HA has no service middleware.

The wrapper's schema is the original plus the priority fields, and unmanaged
targets are forwarded to the original in the same call, so nothing a caller or
integration relies on changes.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import timedelta
from typing import Any

import voluptuous as vol
from homeassistant.const import ATTR_ENTITY_ID, ENTITY_MATCH_ALL, ENTITY_MATCH_NONE
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import target as target_helpers

from .const import (
    ARBITRATED_SERVICES,
    ATTR_PRIORITY,
    ATTR_PRIORITY_TTL,
    MAX_PRIORITY,
    MIN_PRIORITY,
    PRIORITY_NAMES,
)
from .store import PriorityManager

_LOGGER = logging.getLogger(__name__)

# Targeting fields, stripped before a payload becomes a slot or is re-targeted.
_TARGET_FIELDS = frozenset(
    {ATTR_ENTITY_ID, ATTR_PRIORITY, ATTR_PRIORITY_TTL, "device_id", "area_id",
     "floor_id", "label_id", "metadata"}
)

# Marks a handler as one of ours, so a reload cannot wrap a wrapper.
_WRAPPER_MARKER = "_priority_wrapper"

# Pre-validation payload: light folds colour into `params`, so validated data can't be revalidated.
_RAW_KEY = "__priority_raw"

PRIORITY_FIELD: dict[Any, Any] = {
    vol.Optional(ATTR_PRIORITY): vol.All(
        vol.Coerce(int), vol.Range(min=MIN_PRIORITY, max=MAX_PRIORITY)
    ),
    vol.Optional(ATTR_PRIORITY_TTL): vol.Any(
        cv.positive_time_period, vol.All(vol.Coerce(float), vol.Range(min=0))
    ),
}


def _ttl_seconds(value: Any) -> float | None:
    """Seconds, with 0 and None both meaning no lease."""
    if value is None:
        return None
    if isinstance(value, timedelta):
        seconds = value.total_seconds()
    else:
        seconds = float(value)
    return seconds or None


def _extend_schema(schema: Any) -> Any:
    """Validate our fields separately; the original schema sees the rest untouched."""
    if schema is None:
        return None

    priority_schema = vol.Schema(PRIORITY_FIELD, extra=vol.ALLOW_EXTRA)

    def _validate(data: Any) -> Any:
        if not isinstance(data, dict):
            return schema(data)

        has_priority = ATTR_PRIORITY in data or ATTR_PRIORITY_TTL in data
        checked = priority_schema(data) if has_priority else data

        rest = {
            key: value
            for key, value in data.items()
            if key not in (ATTR_PRIORITY, ATTR_PRIORITY_TTL, _RAW_KEY)
        }
        validated = schema(rest)
        if not isinstance(validated, dict):
            return validated

        validated = dict(validated)
        validated[_RAW_KEY] = rest
        if ATTR_PRIORITY in checked:
            validated[ATTR_PRIORITY] = int(checked[ATTR_PRIORITY])
        if ATTR_PRIORITY_TTL in checked:
            validated[ATTR_PRIORITY_TTL] = checked[ATTR_PRIORITY_TTL]
        return validated

    return _validate


@callback
def _resolve_targets(hass: HomeAssistant, call: ServiceCall) -> list[str]:
    """Resolve a call's targets to entity ids, including ``all``, which must never bypass a hold.

    Returns the whole domain for ``all``, not the managed set: the caller forwards
    the unmanaged remainder. Resolution errors propagate, because passing an
    unresolvable call through would silently defeat a hold.
    """
    entity_id = call.data.get(ATTR_ENTITY_ID)

    if entity_id == ENTITY_MATCH_NONE:
        return []

    if entity_id == ENTITY_MATCH_ALL:
        return sorted(state.entity_id for state in hass.states.async_all(call.domain))

    selection = target_helpers.TargetSelection(call.data)
    selected = target_helpers.async_extract_referenced_entity_ids(
        hass, selection, True
    )
    return sorted(selected.referenced | selected.indirectly_referenced)


def _build_wrapper(
    hass: HomeAssistant, manager: PriorityManager, domain: str, service: str
):
    captured = manager.async_get_original(domain, service)

    async def _wrapped(call: ServiceCall) -> ServiceResponse:
        original = manager.async_get_original(domain, service)
        # Unwrapped while a foreign proxy still calls us: pass straight through.
        orphaned = original is None
        if orphaned:
            original = captured
        if original is None:
            raise RuntimeError(f"priority lost the original handler for {domain}.{service}")

        raw: dict[str, Any] = dict(call.data.get(_RAW_KEY) or call.data)

        async def _passthrough(entity_ids: list[str] | None = None) -> ServiceResponse:
            if entity_ids is None:
                # Strip our fields: core passes leftovers as kwargs, and fan.set_percentage raises.
                data = {
                    key: value
                    for key, value in call.data.items()
                    if key not in (ATTR_PRIORITY, ATTR_PRIORITY_TTL, _RAW_KEY)
                }
            else:
                # Re-targeting means re-validating the raw payload.
                narrowed = {
                    key: value
                    for key, value in raw.items()
                    if key not in _TARGET_FIELDS
                }
                narrowed[ATTR_ENTITY_ID] = entity_ids
                data = (
                    original.schema(narrowed)
                    if original.schema is not None
                    else narrowed
                )
                data = {
                    key: value
                    for key, value in data.items()
                    if key not in (ATTR_PRIORITY, ATTR_PRIORITY_TTL, _RAW_KEY)
                }
            forwarded = ServiceCall(
                hass, domain, service, data, call.context, call.return_response
            )
            task = hass.async_run_hass_job(original.job, forwarded)
            return await task if task is not None else None

        if orphaned:
            return await _passthrough()

        targets = _resolve_targets(hass, call)

        managed = [
            entity_id for entity_id in targets if manager.async_is_managed(entity_id)
        ]
        managed_set = set(managed)
        unmanaged = [
            entity_id for entity_id in targets if entity_id not in managed_set
        ]

        if not managed:
            return await _passthrough()

        # Suppressing a response-returning call would hand back a wrong answer.
        if call.return_response:
            return await _passthrough()

        priority = call.data.get(ATTR_PRIORITY) or manager.async_default_priority(
            call.context
        )
        ttl = _ttl_seconds(call.data.get(ATTR_PRIORITY_TTL))
        if ttl is not None and priority == MAX_PRIORITY:
            raise ServiceValidationError(
                f"priority_ttl is not valid at priority {MAX_PRIORITY} "
                f"({PRIORITY_NAMES[MAX_PRIORITY]}): it is the lowest level, so "
                "there is nothing for it to expire back to"
            )

        # Grouped by resolved service: toggle can resolve differently per entity.
        winners: dict[str, list[str]] = defaultdict(list)
        for entity_id in managed:
            resolved = manager.async_resolve_service(domain, service, entity_id)
            array = manager.async_get_array(entity_id)
            takes_control = array.wins(priority)
            previous = array.effective_priority()
            manager.async_write_slot(
                entity_id,
                priority,
                manager.async_make_slot(domain, resolved, raw, call.context, ttl),
            )
            manager.async_notify(entity_id)
            if takes_control:
                winners[resolved].append(entity_id)
                if priority < MAX_PRIORITY and previous != priority:
                    lease = (
                        f" for {timedelta(seconds=int(ttl))}" if ttl else ""
                    )
                    manager.async_logbook(
                        entity_id,
                        f"held at {PRIORITY_NAMES[priority]} "
                        f"({domain}.{resolved}){lease}",
                        call.context,
                    )
            else:
                _LOGGER.debug(
                    "Priority %s call to %s.%s on %s recorded but not dispatched; "
                    "priority %s holds control",
                    priority,
                    domain,
                    service,
                    entity_id,
                    array.effective_priority(),
                )

        if unmanaged:
            await _passthrough(unmanaged)

        command_data = {
            key: value for key, value in raw.items() if key not in _TARGET_FIELDS
        }
        for resolved, entity_ids in winners.items():
            await manager.async_dispatch(
                domain, resolved, entity_ids, command_data, priority, call.context
            )
        return None

    setattr(_wrapped, _WRAPPER_MARKER, True)
    return _wrapped


@callback
def async_wrap_service(
    hass: HomeAssistant, manager: PriorityManager, domain: str, service: str
) -> bool:
    if manager.suspended:
        return False
    registry = hass.services.async_services_internal()
    existing = registry.get(domain, {}).get(service)
    if existing is None:
        return False
    if getattr(existing.job.target, _WRAPPER_MARKER, False):
        return False
    # A proxy registered over our wrapper still calls it; re-wrapping would store the
    # proxy as our original and loop (issue #1). Removal forgets the original.
    if manager.async_get_original(domain, service) is not None:
        return False

    manager.async_store_original(domain, service, existing)
    hass.services.async_register(
        domain,
        service,
        _build_wrapper(hass, manager, domain, service),
        schema=_extend_schema(existing.schema),
        supports_response=existing.supports_response,
    )
    _LOGGER.debug("Priority wrapped %s.%s", domain, service)
    return True


@callback
def async_unwrap_service(
    hass: HomeAssistant, manager: PriorityManager, domain: str, service: str
) -> None:
    original = manager.async_forget_original(domain, service)
    if original is None:
        return
    registry = hass.services.async_services_internal()
    current = registry.get(domain, {}).get(service)
    if current is not None and not getattr(current.job.target, _WRAPPER_MARKER, False):
        _LOGGER.warning(
            "Priority could not unwrap %s.%s: another integration registered over it, "
            "so calls still pass through ours, unarbitrated, until Home Assistant restarts",
            domain,
            service,
        )
        return

    # Registering fires EVENT_SERVICE_REGISTERED synchronously; suspend or we re-wrap it.
    was_suspended = manager.suspended
    manager.async_suspend(True)
    try:
        hass.services.async_register(
            domain,
            service,
            original.job.target,
            schema=original.schema,
            supports_response=original.supports_response,
        )
    finally:
        manager.async_suspend(was_suspended)
    _LOGGER.debug("Priority unwrapped %s.%s", domain, service)


@callback
def async_wrap_all(hass: HomeAssistant, manager: PriorityManager) -> None:
    for domain in manager.async_managed_domains():
        for service in ARBITRATED_SERVICES.get(domain, frozenset()):
            async_wrap_service(hass, manager, domain, service)


@callback
def async_unwrap_all(hass: HomeAssistant, manager: PriorityManager) -> None:
    was_suspended = manager.suspended
    manager.async_suspend(True)
    try:
        for domain, service in manager.async_originals():
            async_unwrap_service(hass, manager, domain, service)
    finally:
        manager.async_suspend(was_suspended)
