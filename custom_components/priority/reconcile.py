"""One-shot re-drive of emergency holds after a restart.

While Home Assistant is running, this integration never re-asserts the array
against reality: an out-of-band change is recorded at Default and left alone
(see observer.py). That rule exists so the house does not argue with somebody
standing at a wall switch, and so a marginal Zigbee link cannot become a
command loop.

A restart is the one case that rule does not cover honestly. A change during
downtime was never observed by anything - there is no slot 5 recording it and
nobody decided it. A power blip that returned a relay to its default is not a
person expressing intent, and the array comes back believing it is in control
of a device it may no longer match.

So exactly once per start, and only for entities held at an *emergency* level,
the winning command is re-issued. Levels 3 and 4 are deliberately excluded: an
override that says "do not let ordinary automations interfere" is not the same
claim as "this must be true", and quietly re-commanding a device on every boot
is too surprising a thing to do on somebody's behalf at that level. Levels 1
and 2 are the ones where being wrong is expensive enough to justify it.

The command is re-issued unconditionally rather than only when the device
appears to have drifted. Deciding "it already agrees" would need a per-domain
map of service to expected state, which is a third hand-maintained mirror
(const.py already has two) and which fails in the dangerous direction: a wrong
match verdict silently skips an emergency hold. Re-driving a device that was
already correct costs one idempotent command, for a handful of entities, once.
"""

from __future__ import annotations

import logging

from homeassistant.const import (
    EVENT_HOMEASSISTANT_STARTED,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.core import CALLBACK_TYPE, Event, HomeAssistant, callback
from homeassistant.helpers.event import async_call_later

from .const import PRI_AUTO_EMERGENCY, RECONCILE_DELAY_SECONDS
from .store import PriorityManager

_LOGGER = logging.getLogger(__name__)

_IGNORED_STATES = frozenset({STATE_UNAVAILABLE, STATE_UNKNOWN})


@callback
def async_schedule_startup_reconcile(
    hass: HomeAssistant, manager: PriorityManager
) -> CALLBACK_TYPE:
    """Arrange a single reconciliation pass after this start. Returns a cancel.

    Does nothing at all when Home Assistant is already running, which is the
    case when the integration is added or reloaded by hand. Reconciliation is
    about downtime, and there has not been any.
    """
    if hass.is_running:
        _LOGGER.debug("Priority: not a restart, skipping startup reconciliation")
        return lambda: None

    cancel_delay: CALLBACK_TYPE | None = None
    fired = False

    async def _reconcile(_now) -> None:
        nonlocal cancel_delay
        cancel_delay = None
        await async_reconcile_emergency_holds(hass, manager)

    @callback
    def _on_started(_event: Event) -> None:
        # Started means every platform has finished setting up, but a device
        # can still be mid-handshake. Waiting lets those entities report before
        # we look at them; anything still unavailable is skipped here and picked
        # up by the observer's return-from-unavailable path instead.
        nonlocal cancel_delay, fired
        fired = True
        cancel_delay = async_call_later(hass, RECONCILE_DELAY_SECONDS, _reconcile)

    cancel_listen = hass.bus.async_listen_once(
        EVENT_HOMEASSISTANT_STARTED, _on_started
    )

    @callback
    def _cancel() -> None:
        # async_listen_once removes itself once it has fired, and calling the
        # returned canceller afterwards logs an "unknown job listener" error.
        if not fired:
            cancel_listen()
        if cancel_delay is not None:
            cancel_delay()

    return _cancel


async def async_reconcile_emergency_holds(
    hass: HomeAssistant, manager: PriorityManager
) -> int:
    """Re-drive every entity held at level 1 or 2. Returns how many were driven."""
    driven = 0
    for entity_id in sorted(manager.async_overridden()):
        array = manager.async_peek_array(entity_id)
        if array is None:
            continue
        held = array.effective_priority()
        if held is None or held > PRI_AUTO_EMERGENCY:
            continue

        state = hass.states.get(entity_id)
        if state is None or state.state in _IGNORED_STATES:
            # Not reachable yet. Re-driving now would just fail; the observer
            # re-drives on return-from-unavailable for anything held at 1-4.
            _LOGGER.debug(
                "Priority: %s held at %s but not available, leaving it to the "
                "observer",
                entity_id,
                held,
            )
            continue

        await manager.async_drive_effective(entity_id)
        driven += 1

    if driven:
        _LOGGER.info(
            "Priority re-drove %s emergency override(s) after restart", driven
        )
    return driven
