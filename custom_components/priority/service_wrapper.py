"""Interception of domain services so priority arbitration can run.

Home Assistant has no middleware hook for service calls, but the service
registry is a plain dict that can be re-registered into. For each arbitrated
service we capture the :class:`homeassistant.core.Service` that is already
registered, then register a wrapper of our own over the same name. The wrapper
holds the arbitration decision; the captured original is the only thing that
ever touches a device.

Two properties matter and are load-bearing:

* The wrapper's schema is the original schema plus one optional ``priority``
  field. Every other field keeps its original validation, so nothing an
  integration expects can be lost.
* Unmanaged targets are forwarded to the original handler untouched, in the
  same call, so a service call that spans managed and unmanaged entities
  behaves exactly as it did before.
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

# Carries the pre-validation payload alongside the validated one. Some domains
# rewrite their data during validation - `light` folds every colour and
# brightness field into a single `params` dict - so the validated form cannot be
# fed back through the same schema a second time. Slots therefore store what the
# caller actually asked for, and validation happens once per dispatch.
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
    """Normalise a TTL to seconds, treating 0 and None as "no lease"."""
    if value is None:
        return None
    if isinstance(value, timedelta):
        seconds = value.total_seconds()
    else:
        seconds = float(value)
    return seconds or None


def _extend_schema(schema: Any) -> Any:
    """Return the original schema with an optional priority field added.

    Entity service schemas are built by ``cv.make_entity_service_schema`` as a
    ``vol.Schema`` wrapping a ``vol.All``. Rather than trying to unpick that
    structure, validate priority separately and hand the rest to the original
    schema untouched. That keeps every integration's own validation intact,
    including custom validators and ``extra=`` behaviour, and keeps validation
    errors surfacing from the service registry where callers expect them.
    """
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
    """Resolve a call's target selection to concrete entity ids.

    ``entity_id: all`` is resolved rather than waved through. An earlier version
    bailed out here on the grounds that arbitrating `all` meant "writing every
    array in the house", which was simply wrong: core resolves the sentinel
    inside ``entity_service_call`` against *that platform's* entities, so
    ``light.turn_off`` with `all` reaches lights and nothing else. The bail-out
    meant any hold - including a safety interlock at priority 2 - could be
    driven straight through with no error and no trace. Confirmed live: a
    post-restart `light.turn_off` / `entity_id: all` sweep defeated a Manual
    hold while the array went on reporting it was in control.

    It was also inconsistent. `homeassistant.turn_off` expands to per-domain
    calls carrying a concrete entity list, so the same user intent was
    arbitrated or bypassed depending only on which service the caller reached
    for.

    The full domain is returned, **not** ``async_managed_entities()``: the
    caller splits managed from unmanaged and forwards the remainder, and
    pre-filtering here would silently drop excluded entities from the sweep
    altogether.

    Resolution failures are deliberately **not** caught. Passing a call through
    when we cannot tell what it targets is precisely the silent-override-defeat
    this method exists to prevent; a visible error is recoverable, a quietly
    ignored hold is not. With pre-validated call data this path is effectively
    unreachable.
    """
    entity_id = call.data.get(ATTR_ENTITY_ID)

    if entity_id == ENTITY_MATCH_NONE:
        # `none` means target nothing - the opposite of `all`, and previously
        # conflated with it.
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
    """Build the wrapper coroutine for one domain service."""

    async def _wrapped(call: ServiceCall) -> ServiceResponse:
        original = manager.async_get_original(domain, service)
        if original is None:
            raise RuntimeError(f"priority lost the original handler for {domain}.{service}")

        raw: dict[str, Any] = dict(call.data.get(_RAW_KEY) or call.data)

        async def _passthrough(entity_ids: list[str] | None = None) -> ServiceResponse:
            """Run the original handler, optionally narrowed to some entities."""
            if entity_ids is None:
                # Nothing to re-target, so reuse the already-validated payload.
                #
                # Both our added fields must come off. Core's
                # remove_entity_service_fields only strips
                # cv.ENTITY_SERVICE_FIELDS, so anything left here is handed to
                # the entity method as a keyword argument. Domains whose
                # handlers take **kwargs (cover, climate) swallow it silently;
                # ones with a strict signature do not - fan.set_percentage
                # raises TypeError on an otherwise ordinary call.
                data = {
                    key: value
                    for key, value in call.data.items()
                    if key not in (ATTR_PRIORITY, ATTR_PRIORITY_TTL, _RAW_KEY)
                }
            else:
                # Re-targeting means re-validating, because some domains rewrite
                # their payload during validation.
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

        # A response-returning service cannot be meaningfully arbitrated: the
        # caller wants data back, so suppressing the call would produce a wrong
        # answer rather than a deferred one.
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

        # Group by the service each entity resolves to, because `toggle` can
        # resolve differently per entity depending on its current state.
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
    """Replace one registered service with an arbitrating wrapper."""
    if manager.suspended:
        return False
    registry = hass.services.async_services_internal()
    existing = registry.get(domain, {}).get(service)
    if existing is None:
        return False
    if getattr(existing.job.target, _WRAPPER_MARKER, False):
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
    """Put the original handler back."""
    original = manager.async_forget_original(domain, service)
    if original is None:
        return
    registry = hass.services.async_services_internal()
    current = registry.get(domain, {}).get(service)
    if current is not None and not getattr(current.job.target, _WRAPPER_MARKER, False):
        # Something re-registered over our wrapper; leave that alone.
        return

    # Registering fires EVENT_SERVICE_REGISTERED synchronously, and our own
    # listener would wrap the handler straight back again.
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
    """Wrap every arbitrated service that is currently registered."""
    for domain in manager.async_managed_domains():
        for service in ARBITRATED_SERVICES.get(domain, frozenset()):
            async_wrap_service(hass, manager, domain, service)


@callback
def async_unwrap_all(hass: HomeAssistant, manager: PriorityManager) -> None:
    """Restore every service we wrapped."""
    was_suspended = manager.suspended
    manager.async_suspend(True)
    try:
        for domain, service in manager.async_originals():
            async_unwrap_service(hass, manager, domain, service)
    finally:
        manager.async_suspend(was_suspended)
