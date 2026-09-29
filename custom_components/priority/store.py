"""Runtime state: arrays, the managed set, and the one path that drives devices.

Dispatch invokes the captured pre-wrap ``Service`` job rather than
``hass.services.async_call``, so recursion is structurally impossible and one
relinquish produces exactly one dispatch.
"""

from __future__ import annotations

import logging
import time
from collections import OrderedDict
from collections.abc import Callable, Iterable
from datetime import timedelta
from typing import Any

import voluptuous as vol
from homeassistant.const import ATTR_ENTITY_ID, EVENT_LOGBOOK_ENTRY, SERVICE_TOGGLE
from homeassistant.core import Context, HomeAssistant, Service, ServiceCall, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_track_point_in_utc_time
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .array import PriorityArray, Slot
from .commands import resolve_toggle
from .const import (
    ARBITRATED_SERVICES,
    ATTR_PRIORITY,
    ATTR_PRIORITY_TTL,
    CONF_DEFAULT_AUTOMATION_PRIORITY,
    CONF_DEFAULT_USER_PRIORITY,
    CONF_EXCLUDED_ENTITIES,
    CONF_MANAGED_AREAS,
    CONF_MANAGED_ENTITIES,
    CONF_MANAGED_LABELS,
    CONF_SCOPE,
    CONF_TRACK_OUT_OF_BAND,
    CONTEXT_MAP_MAX_ENTRIES,
    CONTEXT_TTL_SECONDS,
    DEFAULT_AUTOMATION_PRIORITY,
    DEFAULT_SCOPE,
    DEFAULT_TRACK_OUT_OF_BAND,
    DEFAULT_USER_PRIORITY,
    DOMAIN,
    MAX_PRIORITY,
    MIN_PRIORITY,
    PRI_DEFAULT,
    PRIORITY_NAMES,
    SCOPE_ALL,
    STORAGE_KEY,
    STORAGE_VERSION,
)

_LOGGER = logging.getLogger(__name__)

SAVE_DELAY = 10

# Targeting and control fields, not commanded values; never stored in a slot.
_NON_COMMAND_FIELDS = frozenset(
    {
        ATTR_ENTITY_ID,
        ATTR_PRIORITY,
        ATTR_PRIORITY_TTL,
        "device_id",
        "area_id",
        "floor_id",
        "label_id",
        "metadata",
    }
)


class PriorityManager:
    """Owns every priority array and the single path that drives devices."""

    def __init__(self, hass: HomeAssistant, options: dict[str, Any]) -> None:
        self.hass = hass
        self._options = dict(options)
        self._arrays: dict[str, PriorityArray] = {}
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, STORAGE_KEY
        )
        self._originals: dict[tuple[str, str], Service] = {}
        # Pre-patch frontend descriptions, put back on unload.
        self._descriptions: dict[tuple[str, str], dict[str, Any]] = {}
        # context id -> (priority, monotonic stamp); bounded and TTL'd.
        self._our_contexts: OrderedDict[str, tuple[int, float]] = OrderedDict()
        self._listeners: list[Callable[[str], None]] = []
        # Maintained incrementally so the hot path never scans every array.
        self._override_set: frozenset[str] = frozenset()
        # Cached because auth lookups are coroutines and the write path is a @callback.
        self._user_names: dict[str, str] = {}
        self._user_refresh_pending = False
        self._managed_cache: frozenset[str] | None = None
        self._timers: dict[tuple[str, int], Callable[[], None]] = {}
        # async_register fires EVENT_SERVICE_REGISTERED synchronously, so unwrapping re-wraps.
        self._suspended = False

    @property
    def suspended(self) -> bool:
        return self._suspended

    @callback
    def async_suspend(self, suspended: bool) -> None:
        self._suspended = suspended

    # ---- Options and the managed set ----

    @property
    def options(self) -> dict[str, Any]:
        return self._options

    @callback
    def async_update_options(self, options: dict[str, Any]) -> None:
        self._options = dict(options)
        self._managed_cache = None

    @property
    def track_out_of_band(self) -> bool:
        return self._options.get(
            CONF_TRACK_OUT_OF_BAND, DEFAULT_TRACK_OUT_OF_BAND
        )

    @callback
    def async_invalidate_managed_cache(self) -> None:
        self._managed_cache = None

    @property
    def scope(self) -> str:
        return self._options.get(CONF_SCOPE, DEFAULT_SCOPE)

    @callback
    def _excluded(self) -> frozenset[str]:
        return frozenset(self._options.get(CONF_EXCLUDED_ENTITIES) or [])

    @callback
    def async_is_managed(self, entity_id: str) -> bool:
        """Whether this entity is under arbitration.

        Runs for every target of every arbitrated call, so ``all`` scope never
        touches a registry.
        """
        if entity_id.split(".", 1)[0] not in ARBITRATED_SERVICES:
            return False
        if entity_id in self._excluded():
            return False
        if self.scope == SCOPE_ALL:
            return True
        return entity_id in self._selected_entities()

    @callback
    def _selected_entities(self) -> frozenset[str]:
        if self._managed_cache is not None:
            return self._managed_cache

        managed: set[str] = set(self._options.get(CONF_MANAGED_ENTITIES) or [])
        label_ids = set(self._options.get(CONF_MANAGED_LABELS) or [])
        area_ids = set(self._options.get(CONF_MANAGED_AREAS) or [])

        if label_ids or area_ids:
            ent_reg = er.async_get(self.hass)
            dev_reg = dr.async_get(self.hass)
            for entry in ent_reg.entities.values():
                if entry.disabled_by is not None:
                    continue
                area_id = entry.area_id
                # An entity that follows its device's area stores None here.
                if area_id is None and entry.device_id is not None:
                    device = dev_reg.async_get(entry.device_id)
                    area_id = device.area_id if device is not None else None
                if (label_ids and (entry.labels & label_ids)) or (
                    area_ids and area_id in area_ids
                ):
                    managed.add(entry.entity_id)

        self._managed_cache = frozenset(
            entity_id
            for entity_id in managed
            if entity_id.split(".", 1)[0] in ARBITRATED_SERVICES
        )
        return self._managed_cache

    @callback
    def async_managed_entities(self) -> frozenset[str]:
        """Every managed entity; enumerates, so never call it on the hot path."""
        if self.scope == SCOPE_ALL:
            excluded = self._excluded()
            return frozenset(
                state.entity_id
                for state in self.hass.states.async_all(
                    list(ARBITRATED_SERVICES)
                )
                if state.entity_id not in excluded
            )
        return self._selected_entities()

    @callback
    def async_managed_domains(self) -> frozenset[str]:
        """Domains whose services should be wrapped."""
        if self.scope == SCOPE_ALL:
            return frozenset(ARBITRATED_SERVICES)
        return frozenset(
            entity_id.split(".", 1)[0] for entity_id in self._selected_entities()
        )

    # ---- Arrays ----

    @callback
    def async_get_array(self, entity_id: str) -> PriorityArray:
        if (array := self._arrays.get(entity_id)) is None:
            array = PriorityArray(entity_id=entity_id)
            self._arrays[entity_id] = array
        return array

    @callback
    def async_peek_array(self, entity_id: str) -> PriorityArray | None:
        return self._arrays.get(entity_id)

    @callback
    def async_all_arrays(self) -> dict[str, PriorityArray]:
        return dict(self._arrays)

    # ---- Change notification ----

    @callback
    def async_add_listener(self, listener: Callable[[str], None]) -> Callable[[], None]:
        """Subscribe to array changes. Returns an unsubscribe callable."""
        self._listeners.append(listener)

        def _remove() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return _remove

    @callback
    def async_overridden(self) -> frozenset[str]:
        """Entities currently held above Default."""
        return self._override_set

    @callback
    def async_notify(self, entity_id: str) -> None:
        """Record an array change, fanning out only when an override starts or ends.

        Every arbitrated command lands here; notifying on Default traffic would
        cost a sensor write and a recorder row per light toggle in the house.
        """
        array = self._arrays.get(entity_id)
        held = array.effective_priority() if array is not None else None
        overridden = held is not None and held < PRI_DEFAULT
        was_overridden = entity_id in self._override_set

        if overridden:
            self._override_set = self._override_set | {entity_id}
        elif was_overridden:
            self._override_set = self._override_set - {entity_id}

        if not (overridden or was_overridden):
            return

        for listener in list(self._listeners):
            listener(entity_id)
        self._store.async_delay_save(self._data_to_save, SAVE_DELAY)

    # ---- Context attribution ----

    @callback
    def async_remember_context(self, context: Context, priority: int) -> None:
        now = time.monotonic()
        self._our_contexts[context.id] = (priority, now)
        self._our_contexts.move_to_end(context.id)
        self._prune_contexts(now)

    @callback
    def async_is_our_context(self, context: Context) -> bool:
        """Whether a state change can be attributed to a dispatch of ours."""
        now = time.monotonic()
        self._prune_contexts(now)
        if context.id in self._our_contexts:
            return True
        return bool(context.parent_id) and context.parent_id in self._our_contexts

    def _prune_contexts(self, now: float) -> None:
        while self._our_contexts:
            oldest_id = next(iter(self._our_contexts))
            _, stamp = self._our_contexts[oldest_id]
            if (
                now - stamp > CONTEXT_TTL_SECONDS
                or len(self._our_contexts) > CONTEXT_MAP_MAX_ENTRIES
            ):
                del self._our_contexts[oldest_id]
                continue
            break

    @callback
    def async_default_priority(self, context: Context) -> int:
        """Priority for a call that named none: a ``user_id`` means a person.

        Both defaults are configurable because the heuristic misreads some
        callers, such as a REST script the user considers manual.
        """
        if context.user_id:
            return int(
                self._options.get(CONF_DEFAULT_USER_PRIORITY, DEFAULT_USER_PRIORITY)
            )
        return int(
            self._options.get(
                CONF_DEFAULT_AUTOMATION_PRIORITY, DEFAULT_AUTOMATION_PRIORITY
            )
        )

    async def async_refresh_user_names(self) -> None:
        self._user_refresh_pending = False
        try:
            users = await self.hass.auth.async_get_users()
        except Exception:  # noqa: BLE001 - attribution must never break a write
            _LOGGER.debug("Priority: could not load users for attribution")
            return
        self._user_names = {
            user.id: user.name for user in users if user.name
        }

    @callback
    def _async_schedule_user_refresh(self) -> None:
        if self._user_refresh_pending:
            return
        self._user_refresh_pending = True
        self.hass.async_create_task(self.async_refresh_user_names())

    @callback
    def async_attribute(self, context: Context) -> str | None:
        """Best-effort human-readable attribution for a slot write."""
        if context.user_id:
            if (name := self._user_names.get(context.user_id)) is not None:
                return f"user:{name}"
            # Unknown user: keep the raw id traceable and refresh for next time.
            self._async_schedule_user_refresh()
            return f"user:{context.user_id}"
        # Automations and scripts stamp their own state with the context they act under.
        wanted = {context.id}
        if context.parent_id:
            wanted.add(context.parent_id)
        for domain in ("automation", "script"):
            for state in self.hass.states.async_all(domain):
                if state.context.id in wanted:
                    return state.entity_id
        return None

    # ---- Original service handlers ----

    @callback
    def async_store_original(
        self, domain: str, service: str, original: Service
    ) -> None:
        self._originals[(domain, service)] = original

    @callback
    def async_get_original(self, domain: str, service: str) -> Service | None:
        return self._originals.get((domain, service))

    @callback
    def async_forget_original(self, domain: str, service: str) -> Service | None:
        return self._originals.pop((domain, service), None)

    @callback
    def async_originals(self) -> dict[tuple[str, str], Service]:
        return dict(self._originals)

    # ---- Original frontend descriptions ----

    @callback
    def async_store_description(
        self, domain: str, service: str, description: dict[str, Any]
    ) -> None:
        self._descriptions[(domain, service)] = description

    @callback
    def async_descriptions(self) -> dict[tuple[str, str], dict[str, Any]]:
        return dict(self._descriptions)

    # ---- Logbook ----

    @callback
    def async_logbook(
        self, entity_id: str, message: str, context: Context | None = None
    ) -> None:
        """Write an entry into the affected entity's own logbook.

        Fires ``logbook_entry`` directly instead of importing logbook, so it is
        not a dependency; if logbook is not loaded the event goes unheard.
        """
        self.hass.bus.async_fire(
            EVENT_LOGBOOK_ENTRY,
            {
                "name": "Priority",
                "message": message,
                "domain": DOMAIN,
                "entity_id": entity_id,
            },
            context=context,
        )

    # ---- Slot construction ----

    @callback
    def async_resolve_service(
        self, domain: str, service: str, entity_id: str
    ) -> str:
        """Resolve ``toggle`` against current state, once, at the moment of the call."""
        if service != SERVICE_TOGGLE:
            return service
        return resolve_toggle(domain, self.hass.states.get(entity_id))

    @callback
    def async_validate_command(
        self, domain: str, service: str, entity_id: str, data: dict[str, Any]
    ) -> None:
        """Raise if this payload could never be dispatched.

        A slot wins arbitration whether or not its dispatch succeeds, so an
        undispatchable one would hold the device with nothing driving it.
        """
        original = self.async_get_original(domain, service)
        schema = original.schema if original is not None else None
        if schema is None:
            return
        try:
            schema({**data, ATTR_ENTITY_ID: [entity_id]})
        except vol.Invalid as err:
            raise ServiceValidationError(
                f"{domain}.{service} would not accept this data for "
                f"{entity_id}: {err}"
            ) from err

    @callback
    def async_make_slot(
        self,
        domain: str,
        service: str,
        data: dict[str, Any],
        context: Context,
        ttl: float | None = None,
    ) -> Slot:
        expires_at = None
        if ttl:
            expires_at = dt_util.utcnow() + timedelta(seconds=float(ttl))
        return Slot(
            domain=domain,
            service=service,
            data={
                key: value
                for key, value in data.items()
                if key not in _NON_COMMAND_FIELDS
            },
            written_at=dt_util.utcnow(),
            written_by=self.async_attribute(context),
            expires_at=expires_at,
        )

    # ---- Expiry timers ----

    @callback
    def async_write_slot(
        self, entity_id: str, priority: int, slot: Slot
    ) -> None:
        """Write a slot and (re)arm its expiry timer."""
        array = self.async_get_array(entity_id)
        array.write(priority, slot)
        self.async_arm_timer(entity_id, priority)

    @callback
    def async_cancel_timer(self, entity_id: str, priority: int) -> None:
        if (cancel := self._timers.pop((entity_id, priority), None)) is not None:
            cancel()

    @callback
    def async_arm_timer(self, entity_id: str, priority: int) -> None:
        """Schedule a slot's expiry, replacing any pending one."""
        self.async_cancel_timer(entity_id, priority)

        array = self.async_peek_array(entity_id)
        if array is None or (slot := array.get(priority)) is None:
            return
        if slot.expires_at is None:
            return

        @callback
        def _expired(_now) -> None:
            self._timers.pop((entity_id, priority), None)
            current = self.async_peek_array(entity_id)
            if current is None or current.get(priority) is not slot:
                # Rewritten since the timer was armed; that write armed its own.
                return
            was_in_control = current.lowest_occupied() == priority
            current.clear(priority)
            self.async_notify(entity_id)
            _LOGGER.debug(
                "Priority %s on %s expired after its lease", priority, entity_id
            )
            fell_to = current.effective_priority()
            self.async_logbook(
                entity_id,
                f"{PRIORITY_NAMES[priority]} override expired"
                + (
                    f", returned to {PRIORITY_NAMES[fell_to]}"
                    if fell_to is not None
                    else ", no longer under priority control"
                ),
            )
            if was_in_control:
                self.hass.async_create_task(
                    self.async_drive_effective(entity_id),
                    f"priority expiry redrive {entity_id}",
                )

        self._timers[(entity_id, priority)] = async_track_point_in_utc_time(
            self.hass, _expired, slot.expires_at
        )

    @callback
    def async_rearm_timers(self) -> None:
        """Re-arm every expiry after a restart, dropping ones already lapsed."""
        now = dt_util.utcnow()
        for entity_id, array in list(self._arrays.items()):
            if array.purge_expired(now):
                self.async_notify(entity_id)
            for priority in range(MIN_PRIORITY, MAX_PRIORITY + 1):
                if array.get(priority) is not None:
                    self.async_arm_timer(entity_id, priority)

    @callback
    def async_shutdown_timers(self) -> None:
        for cancel in self._timers.values():
            cancel()
        self._timers.clear()

    # ---- Dispatch: the only place a device is actually driven ----

    async def async_dispatch(
        self,
        domain: str,
        service: str,
        entity_ids: Iterable[str],
        data: dict[str, Any],
        priority: int,
        context: Context | None = None,
    ) -> None:
        """Drive entities through the pre-wrap handler, never re-entering our wrapper."""
        targets = list(entity_ids)
        if not targets:
            return

        original = self.async_get_original(domain, service)
        if original is None:
            # Not wrapped, so the plain call path cannot recurse into us.
            await self.hass.services.async_call(
                domain,
                service,
                {**data, ATTR_ENTITY_ID: targets},
                blocking=True,
                context=context,
            )
            return

        call_context = context or Context()
        self.async_remember_context(call_context, priority)

        payload: dict[str, Any] = {**data, ATTR_ENTITY_ID: targets}
        if original.schema is not None:
            try:
                payload = original.schema(payload)
            except vol.Invalid:
                _LOGGER.exception(
                    "Priority dispatch built an invalid payload for %s.%s on %s; "
                    "dropping the slot at priority %s rather than letting it hold "
                    "control of a device it cannot drive",
                    domain,
                    service,
                    targets,
                    priority,
                )
                # Backstop for async_validate_command: give control back.
                for entity_id in targets:
                    array = self.async_peek_array(entity_id)
                    if array is None or array.get(priority) is None:
                        continue
                    array.clear(priority)
                    self.async_cancel_timer(entity_id, priority)
                    self.async_notify(entity_id)
                return

        service_call = ServiceCall(
            self.hass, domain, service, payload, call_context, False
        )

        _LOGGER.debug(
            "Priority %s dispatching %s.%s to %s", priority, domain, service, targets
        )

        task = self.hass.async_run_hass_job(original.job, service_call)
        if task is not None:
            await task

    async def async_drive_effective(
        self, entity_id: str, context: Context | None = None
    ) -> None:
        """Re-issue the winning command; an empty array leaves the device alone."""
        array = self.async_peek_array(entity_id)
        if array is None or (winner := array.effective()) is None:
            return
        priority, slot = winner
        await self.async_dispatch(
            slot.domain, slot.service, [entity_id], slot.data, priority, context
        )

    # ---- Persistence ----

    async def async_load(self) -> None:
        # Attribution needs user names even when nothing is persisted.
        await self.async_refresh_user_names()
        if (raw := await self._store.async_load()) is None:
            return
        for entity_id, stored in (raw.get("arrays") or {}).items():
            array = PriorityArray.from_storage(entity_id, stored)
            if not array.is_empty():
                self._arrays[entity_id] = array

        # Restoring bypasses async_notify, so rebuild the index here.
        self._override_set = frozenset(
            entity_id
            for entity_id, array in self._arrays.items()
            if (held := array.effective_priority()) is not None
            and held < PRI_DEFAULT
        )
        _LOGGER.debug(
            "Restored %s priority arrays, %s of them overridden",
            len(self._arrays),
            len(self._override_set),
        )

    @callback
    def _data_to_save(self) -> dict[str, Any]:
        arrays: dict[str, Any] = {}
        for entity_id, array in self._arrays.items():
            stored = array.to_storage()
            if stored["slots"]:
                arrays[entity_id] = stored
        return {"arrays": arrays}

    async def async_save(self) -> None:
        await self._store.async_save(self._data_to_save())

    # No startup seed of slot 5: relinquish_all would re-drive a command nobody issued.
