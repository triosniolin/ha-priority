"""Record changes with no service call behind them (wall switch, vendor app, Zigbee binding).

An unexplained change is a Default-level write. There is deliberately no
snap-back: the array records reality and is never re-asserted against someone
at a switch, which on a marginal link would also become a command loop.
"""

from __future__ import annotations

import logging

from homeassistant.const import (
    EVENT_STATE_CHANGED,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.util import dt as dt_util

from .array import Slot
from .commands import command_for_state
from .const import ARBITRATED_SERVICES, PRI_DEFAULT
from .store import PriorityManager

_LOGGER = logging.getLogger(__name__)

_IGNORED_STATES = frozenset({STATE_UNAVAILABLE, STATE_UNKNOWN})


@callback
def async_start_observer(hass: HomeAssistant, manager: PriorityManager):
    """Returns an unsubscribe."""

    @callback
    def _record(entity_id: str, domain: str, state: str) -> None:
        command = command_for_state(domain, state)
        if command is None:
            return
        service, slot_data = command
        if service not in ARBITRATED_SERVICES.get(domain, frozenset()):
            return
        manager.async_get_array(entity_id).write(
            PRI_DEFAULT,
            Slot(
                domain=domain,
                service=service,
                data=slot_data,
                written_at=dt_util.utcnow(),
                written_by="out_of_band",
            ),
        )
        manager.async_notify(entity_id)
        _LOGGER.debug("Priority recorded %s on %s at Default", state, entity_id)

    @callback
    def _handle(event: Event[EventStateChangedData]) -> None:
        if not manager.track_out_of_band:
            return

        data = event.data
        entity_id = data["entity_id"]
        new_state = data["new_state"]
        old_state = data["old_state"]

        if new_state is None:
            return

        domain = entity_id.split(".", 1)[0]
        if domain not in ARBITRATED_SERVICES:
            return
        if not manager.async_is_managed(entity_id):
            return

        # Returning from unavailable is transport, not a command. Re-drive only a real
        # override: re-sending Default would turn a flapping link into a command stream.
        if old_state is not None and old_state.state in _IGNORED_STATES:
            if new_state.state not in _IGNORED_STATES:
                array = manager.async_peek_array(entity_id)
                # Mesh radios come up via unavailable, so this is their come-online report.
                # Only into an empty slot: a flap must not replace a richer commanded payload.
                if array is None or array.get(PRI_DEFAULT) is None:
                    _record(entity_id, domain, new_state.state)
                    array = manager.async_peek_array(entity_id)
                held = array.effective_priority() if array is not None else None
                if held is not None and held < PRI_DEFAULT:
                    hass.async_create_task(
                        manager.async_drive_effective(entity_id),
                        f"priority redrive {entity_id}",
                    )
            return

        if new_state.state in _IGNORED_STATES:
            return

        # An attribute-only change is the device reporting, not somebody commanding it.
        if old_state is not None and old_state.state == new_state.state:
            return

        if manager.async_is_our_context(new_state.context):
            return

        _record(entity_id, domain, new_state.state)

    return hass.bus.async_listen(EVENT_STATE_CHANGED, _handle)
