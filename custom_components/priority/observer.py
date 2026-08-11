"""Out-of-band change detection.

A wall switch, a vendor app, a Zigbee group binding or a physical relay can
change a device with no Home Assistant service call behind it. Without this the
array would quietly lie: it would claim a command is in force while the device
sits somewhere else entirely.

The rule chosen here is that an unexplained change is treated as a Manual Low
write. Touching a switch on the wall then means exactly what commanding at
priority 5 means - it takes effect, and any automation is free to override it.

Deliberately absent: any form of snap-back. When the array disagrees with
reality we record reality; we never re-assert the array against somebody
standing at a light switch. Enforcing the array would turn a marginal Zigbee
link into a command loop, and would make the house argue with its occupants.
"""

from __future__ import annotations

import logging

from homeassistant.const import (
    EVENT_STATE_CHANGED,
    STATE_ON,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.util import dt as dt_util

from .array import Slot
from .const import ARBITRATED_SERVICES, PRI_DEFAULT
from .store import PriorityManager

_LOGGER = logging.getLogger(__name__)

_IGNORED_STATES = frozenset({STATE_UNAVAILABLE, STATE_UNKNOWN})


@callback
def async_start_observer(hass: HomeAssistant, manager: PriorityManager):
    """Watch for state changes we cannot account for. Returns an unsubscribe."""

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

        # An entity coming back from unavailable is a transport event, not a
        # command, so it is never recorded as one.
        #
        # It may be worth re-driving, but only when a real override is being
        # held. At the Default level the array is just a record of ordinary
        # last-wins traffic, and re-sending that would be a command nobody
        # issued - stock Home Assistant does nothing when a device returns, and
        # so must we. On a marginal Zigbee link an entity can flap repeatedly;
        # re-driving on every recovery would turn that into a stream of
        # commands, and for a lock or a valve that is a genuinely bad idea.
        if old_state is not None and old_state.state in _IGNORED_STATES:
            if new_state.state not in _IGNORED_STATES:
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

        # Attribute changes without an on/off transition are usually the device
        # reporting, not somebody commanding it.
        if old_state is not None and old_state.state == new_state.state:
            return

        if manager.async_is_our_context(new_state.context):
            return

        service = "turn_on" if new_state.state == STATE_ON else "turn_off"
        if service not in ARBITRATED_SERVICES.get(domain, frozenset()):
            return

        array = manager.async_get_array(entity_id)
        array.write(
            PRI_DEFAULT,
            Slot(
                domain=domain,
                service=service,
                data={},
                written_at=dt_util.utcnow(),
                written_by="out_of_band",
            ),
        )
        manager.async_notify(entity_id)
        _LOGGER.debug(
            "Priority recorded out-of-band %s on %s into slot %s",
            new_state.state,
            entity_id,
            PRI_DEFAULT,
        )

    return hass.bus.async_listen(EVENT_STATE_CHANGED, _handle)
