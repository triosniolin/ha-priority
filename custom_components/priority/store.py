"""Runtime state for priority arbitration.

Holds one :class:`PriorityArray` per managed entity, decides which entities are
managed, remembers which contexts originated from a dispatch of ours, and owns
the one path that actually drives a device.

Dispatch deliberately does **not** go through ``hass.services.async_call``. We
keep a reference to the original :class:`homeassistant.core.Service` that was
registered before we wrapped it, and invoke its job directly. That makes
recursion structurally impossible rather than something a guard flag has to
catch, and it means one relinquish produces exactly one dispatch.
"""

from __future__ import annotations

import logging
import time
from collections import OrderedDict
from collections.abc import Callable, Iterable
from datetime import timedelta
from typing import Any

import voluptuous as vol
from homeassistant.const import ATTR_ENTITY_ID, EVENT_LOGBOOK_ENTRY, STATE_ON
from homeassistant.core import Context, HomeAssistant, Service, ServiceCall, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_track_point_in_utc_time
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .array import PriorityArray, Slot
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
    TOGGLE_SERVICES,
)

_LOGGER = logging.getLogger(__name__)

SAVE_DELAY = 10

# Fields we strip from a call payload before it becomes a slot. These are
# targeting and control fields, not commanded values.
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
        """Initialise the manager."""
        self.hass = hass
        self._options = dict(options)
        self._arrays: dict[str, PriorityArray] = {}
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, STORAGE_KEY
        )
        # Original Service objects, keyed by (domain, service), captured before
        # we replaced them in the registry.
        self._originals: dict[tuple[str, str], Service] = {}
        # Frontend service descriptions as they were before we grafted the
        # priority fields on, so unload can put them back.
        self._descriptions: dict[tuple[str, str], dict[str, Any]] = {}
        # context id -> priority, for out-of-band attribution. Bounded and TTL'd
        # so a long-running instance cannot grow it without limit.
        self._our_contexts: OrderedDict[str, tuple[int, float]] = OrderedDict()
        self._listeners: list[Callable[[str], None]] = []
        # Entities currently held above Default. Kept incrementally so the hot
        # path never has to scan every array to answer "did anything change".
        self._override_set: frozenset[str] = frozenset()
        self._managed_cache: frozenset[str] | None = None
        # (entity_id, priority) -> cancel callable for a pending slot expiry.
        self._timers: dict[tuple[str, int], Callable[[], None]] = {}
        # Re-registering a service fires EVENT_SERVICE_REGISTERED synchronously,
        # which our own listener would otherwise treat as a new service to wrap.
        # During unwrapping that would immediately undo the unwrap.
        self._suspended = False

    @property
    def suspended(self) -> bool:
        """Whether service wrapping is currently suppressed."""
        return self._suspended

    @callback
    def async_suspend(self, suspended: bool) -> None:
        """Suppress or resume service wrapping."""
        self._suspended = suspended

    # ------------------------------------------------------------------
    # Options and the managed set
    # ------------------------------------------------------------------

    @property
    def options(self) -> dict[str, Any]:
        """Current config entry options."""
        return self._options

    @callback
    def async_update_options(self, options: dict[str, Any]) -> None:
        """Apply new options and drop the managed-set cache."""
        self._options = dict(options)
        self._managed_cache = None

    @property
    def track_out_of_band(self) -> bool:
        """Whether unexplained state changes are recorded into the lowest slot."""
        return self._options.get(
            CONF_TRACK_OUT_OF_BAND, DEFAULT_TRACK_OUT_OF_BAND
        )

    @callback
    def async_invalidate_managed_cache(self) -> None:
        """Recompute the managed set on next use (registry changed)."""
        self._managed_cache = None

    @property
    def scope(self) -> str:
        """Whether arbitration covers every entity or an explicit selection."""
        return self._options.get(CONF_SCOPE, DEFAULT_SCOPE)

    @callback
    def _excluded(self) -> frozenset[str]:
        """Entities the user has carved out of arbitration."""
        return frozenset(self._options.get(CONF_EXCLUDED_ENTITIES) or [])

    @callback
    def async_is_managed(self, entity_id: str) -> bool:
        """Whether this entity is under priority arbitration.

        This is the hot predicate - it runs for every target of every
        arbitrated service call - so the ``all`` case is answered from a domain
        lookup and a set membership test, without touching any registry.
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
        """Resolve the explicit selection from entities, labels and areas."""
        if self._managed_cache is not None:
            return self._managed_cache

        managed: set[str] = set(self._options.get(CONF_MANAGED_ENTITIES) or [])
        label_ids = set(self._options.get(CONF_MANAGED_LABELS) or [])
        area_ids = set(self._options.get(CONF_MANAGED_AREAS) or [])

        if label_ids or area_ids:
            ent_reg = er.async_get(self.hass)
            for entry in ent_reg.entities.values():
                if entry.disabled_by is not None:
                    continue
                if (label_ids and (entry.labels & label_ids)) or (
                    area_ids and entry.area_id in area_ids
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
        """Every currently-known managed entity.

        Only for seeding and diagnostics. The hot path uses
        :meth:`async_is_managed` instead, which never has to enumerate.
        """
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
        """Domains that should have their services wrapped.

        Under ``all`` scope this is every arbitrated domain that is actually
        loaded, so a service is never wrapped for a domain the user does not
        have.
        """
        if self.scope == SCOPE_ALL:
            return frozenset(ARBITRATED_SERVICES)
        return frozenset(
            entity_id.split(".", 1)[0] for entity_id in self._selected_entities()
        )

    # ------------------------------------------------------------------
    # Arrays
    # ------------------------------------------------------------------

    @callback
    def async_get_array(self, entity_id: str) -> PriorityArray:
        """Return the array for an entity, creating it if needed."""
        if (array := self._arrays.get(entity_id)) is None:
            array = PriorityArray(entity_id=entity_id)
            self._arrays[entity_id] = array
        return array

    @callback
    def async_peek_array(self, entity_id: str) -> PriorityArray | None:
        """Return the array for an entity without creating one."""
        return self._arrays.get(entity_id)

    @callback
    def async_all_arrays(self) -> dict[str, PriorityArray]:
        """Every array currently held."""
        return dict(self._arrays)

    # ------------------------------------------------------------------
    # Change notification, for the diagnostic entities
    # ------------------------------------------------------------------

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
        """Entities currently held by something above the Default level."""
        return self._override_set

    @callback
    def async_notify(self, entity_id: str) -> None:
        """Record that an array changed, and fan out only if it matters.

        Every arbitrated command lands here, so this is the hottest path in the
        integration. With every entity in scope, notifying unconditionally
        would push a diagnostic state write - and a recorder row - for every
        light toggle in the house, to report a number that had not changed.

        Only overrides are interesting. Ordinary Default-level traffic updates
        the array silently: nothing persists it (slots 4 and 5 are not stored)
        and nothing displays it.
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

    # ------------------------------------------------------------------
    # Context attribution
    # ------------------------------------------------------------------

    @callback
    def async_remember_context(self, context: Context, priority: int) -> None:
        """Record that a context originated from one of our dispatches."""
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
        """Drop expired and surplus context ids, oldest first."""
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
        """Infer a priority for a call that did not specify one.

        A human acting through the UI, the app or voice arrives with a
        ``user_id``; anything else is taken to be an automation or script.
        Both defaults are configurable because this heuristic will be wrong for
        somebody - a REST call from a script the user considers "manual", for
        instance.
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

    @callback
    def async_attribute(self, context: Context) -> str | None:
        """Best-effort human-readable attribution for a slot write."""
        if context.user_id:
            return f"user:{context.user_id}"
        # Automations and scripts stamp their own state with the context they
        # then use for their actions, so a bounded scan of those two domains
        # finds the originator without pulling in the logbook machinery.
        wanted = {context.id}
        if context.parent_id:
            wanted.add(context.parent_id)
        for domain in ("automation", "script"):
            for state in self.hass.states.async_all(domain):
                if state.context.id in wanted:
                    return state.entity_id
        return None

    # ------------------------------------------------------------------
    # Original service handlers
    # ------------------------------------------------------------------

    @callback
    def async_store_original(
        self, domain: str, service: str, original: Service
    ) -> None:
        """Remember the handler that was registered before we wrapped it."""
        self._originals[(domain, service)] = original

    @callback
    def async_get_original(self, domain: str, service: str) -> Service | None:
        """Return the pre-wrap handler for a service."""
        return self._originals.get((domain, service))

    @callback
    def async_forget_original(self, domain: str, service: str) -> Service | None:
        """Drop and return the pre-wrap handler for a service."""
        return self._originals.pop((domain, service), None)

    @callback
    def async_originals(self) -> dict[tuple[str, str], Service]:
        """Every captured pre-wrap handler."""
        return dict(self._originals)

    # ------------------------------------------------------------------
    # Original frontend descriptions
    # ------------------------------------------------------------------

    @callback
    def async_store_description(
        self, domain: str, service: str, description: dict[str, Any]
    ) -> None:
        """Remember a service description as it was before we patched it."""
        self._descriptions[(domain, service)] = description

    @callback
    def async_descriptions(self) -> dict[tuple[str, str], dict[str, Any]]:
        """Every captured pre-patch description."""
        return dict(self._descriptions)

    # ------------------------------------------------------------------
    # Logbook
    # ------------------------------------------------------------------

    @callback
    def async_logbook(
        self, entity_id: str, message: str, context: Context | None = None
    ) -> None:
        """Write an entry into the affected entity's own logbook.

        This is what makes an override visible where people actually look for
        it - the history and logbook tabs of the device's more-info dialog -
        rather than only in a diagnostic sensor they have to know about.

        Fires the documented ``logbook_entry`` event directly rather than
        importing ``logbook.async_log_entry``, which is only a thin wrapper
        around the same event. That keeps logbook off the dependency list
        entirely: if it is not loaded the event simply goes unheard, and there
        is no import to guard or fail.
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

    # ------------------------------------------------------------------
    # Slot construction
    # ------------------------------------------------------------------

    @callback
    def async_resolve_service(
        self, domain: str, service: str, entity_id: str
    ) -> str:
        """Resolve a state-dependent service to a concrete one.

        ``toggle`` has no stable meaning inside an array: replaying it later
        would flip the device rather than restore it. It is resolved against
        current state at the moment of the call, exactly once.
        """
        if (mapping := TOGGLE_SERVICES.get(service)) is None:
            return service
        when_on, when_off = mapping
        state = self.hass.states.get(entity_id)
        return when_on if state is not None and state.state == STATE_ON else when_off

    @callback
    def async_make_slot(
        self,
        domain: str,
        service: str,
        data: dict[str, Any],
        context: Context,
        ttl: float | None = None,
    ) -> Slot:
        """Build a slot from a call payload, with an optional lease."""
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

    # ------------------------------------------------------------------
    # Expiry timers
    # ------------------------------------------------------------------

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
        """Cancel a pending expiry for one slot."""
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
        """Cancel every pending expiry."""
        for cancel in self._timers.values():
            cancel()
        self._timers.clear()

    # ------------------------------------------------------------------
    # Dispatch - the only place a device is actually driven
    # ------------------------------------------------------------------

    async def async_dispatch(
        self,
        domain: str,
        service: str,
        entity_ids: Iterable[str],
        data: dict[str, Any],
        priority: int,
        context: Context | None = None,
    ) -> None:
        """Drive entities by invoking the pre-wrap handler directly.

        Bypassing ``hass.services.async_call`` is what makes recursion
        impossible: our wrapper is never re-entered, so a replay cannot be
        mistaken for a fresh command.
        """
        targets = list(entity_ids)
        if not targets:
            return

        original = self.async_get_original(domain, service)
        if original is None:
            # Nothing wrapped this service, so the plain call path is correct
            # and cannot recurse into us.
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
                    "Priority dispatch built an invalid payload for %s.%s on %s",
                    domain,
                    service,
                    targets,
                )
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
        """Re-issue whatever command currently wins for an entity.

        Called after a relinquish. If the array is now empty the device is left
        exactly as it is - relinquishing everything means "stop arbitrating",
        not "turn off".
        """
        array = self.async_peek_array(entity_id)
        if array is None or (winner := array.effective()) is None:
            return
        priority, slot = winner
        await self.async_dispatch(
            slot.domain, slot.service, [entity_id], slot.data, priority, context
        )

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    async def async_load(self) -> None:
        """Restore persisted slots."""
        if (raw := await self._store.async_load()) is None:
            return
        for entity_id, stored in (raw.get("arrays") or {}).items():
            array = PriorityArray.from_storage(entity_id, stored)
            if not array.is_empty():
                self._arrays[entity_id] = array
        _LOGGER.debug("Restored %s priority arrays", len(self._arrays))

    @callback
    def _data_to_save(self) -> dict[str, Any]:
        """Build the storage payload, skipping arrays with nothing to persist."""
        arrays: dict[str, Any] = {}
        for entity_id, array in self._arrays.items():
            stored = array.to_storage()
            if stored["slots"]:
                arrays[entity_id] = stored
        return {"arrays": arrays}

    async def async_save(self) -> None:
        """Flush pending state to disk now."""
        await self._store.async_save(self._data_to_save())

    # Deliberately absent: seeding slot 5 from live state at startup.
    #
    # It was tempting - an entity switched at the wall while Home Assistant was
    # down otherwise shows an empty array while visibly being on. But a seeded
    # slot is a command nobody issued, and it is not inert: `relinquish_all`
    # would re-drive it, so releasing an override could switch a device to
    # whatever it happened to be doing when Home Assistant last started. A
    # cosmetic gap in the array is much cheaper than a phantom command. Slot 5
    # is written only by a real manual call or an observed out-of-band change.
