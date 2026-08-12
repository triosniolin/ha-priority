"""The one-shot re-drive of emergency holds after a restart.

While running, the array is never re-asserted against reality (observer.py).
A restart is the gap in that rule: a change during downtime was observed by
nobody, so nothing recorded it and nobody decided it. These cover that the
gap is closed for levels 1-2 and deliberately left open for 3 and 4.
"""

from __future__ import annotations

from datetime import timedelta

from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from homeassistant.const import (
    EVENT_HOMEASSISTANT_STARTED,
    STATE_OFF,
    STATE_ON,
    STATE_UNAVAILABLE,
)
from homeassistant.core import CoreState

from custom_components.priority.array import Slot
from custom_components.priority.const import (
    DOMAIN,
    PRI_AUTO,
    PRI_AUTO_EMERGENCY,
    PRI_MANUAL,
    PRI_MANUAL_EMERGENCY,
    RECONCILE_DELAY_SECONDS,
)
from homeassistant.util import dt as dt_util

LIGHT = "light.one"


async def _boot(hass, entry_options, holds) -> MockConfigEntry:
    """Write slots, then genuinely restart: save, unload, set up again.

    `holds` is {priority: service}. Going through storage rather than writing
    into the fresh manager is the point - a restored hold arrives without ever
    passing through the write path, which is exactly what made the override
    index go stale.
    """
    entry = MockConfigEntry(domain=DOMAIN, options=entry_options, unique_id=DOMAIN)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    for priority, service in holds.items():
        entry.runtime_data.async_write_slot(
            LIGHT, priority, Slot("light", service, {}, dt_util.utcnow())
        )
    await entry.runtime_data.async_save()

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    hass.set_state(CoreState.not_running)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def _finish_starting(hass) -> None:
    """Fire the started event and let the settle delay elapse."""
    hass.set_state(CoreState.running)
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
    await hass.async_block_till_done()
    async_fire_time_changed(
        hass, dt_util.utcnow() + timedelta(seconds=RECONCILE_DELAY_SECONDS + 1)
    )
    await hass.async_block_till_done()


async def test_emergency_hold_is_re_driven_after_a_restart(
    demo_hass, entry_options
) -> None:
    """The whole point: a device that drifted while HA was down is corrected."""
    entry = await _boot(demo_hass, entry_options, {PRI_AUTO_EMERGENCY: "turn_on"})

    # Reality disagrees with the restored hold, the way it would if a relay had
    # power-cycled to its default while Home Assistant was down. Set directly:
    # no service call, no context of ours, nobody observed it happen.
    demo_hass.states.async_set(LIGHT, STATE_OFF)
    await demo_hass.async_block_till_done()

    await _finish_starting(demo_hass)

    assert demo_hass.states.get(LIGHT).state == STATE_ON


async def test_manual_emergency_is_re_driven_too(demo_hass, entry_options) -> None:
    entry = await _boot(demo_hass, entry_options, {PRI_MANUAL_EMERGENCY: "turn_on"})
    demo_hass.states.async_set(LIGHT, STATE_OFF)
    await demo_hass.async_block_till_done()

    await _finish_starting(demo_hass)

    assert demo_hass.states.get(LIGHT).state == STATE_ON


async def test_manual_hold_is_left_alone(demo_hass, entry_options) -> None:
    """Level 3 says "do not let automations interfere", not "this must be true".

    Re-commanding a device on every boot is too surprising to do on somebody's
    behalf at that level, so the drift is left standing.
    """
    await _boot(demo_hass, entry_options, {PRI_MANUAL: "turn_on"})
    demo_hass.states.async_set(LIGHT, STATE_OFF)
    await demo_hass.async_block_till_done()

    await _finish_starting(demo_hass)

    assert demo_hass.states.get(LIGHT).state == STATE_OFF


async def test_automatic_hold_is_left_alone(demo_hass, entry_options) -> None:
    """Level 4 persists across a restart but is still not re-asserted."""
    await _boot(demo_hass, entry_options, {PRI_AUTO: "turn_on"})
    demo_hass.states.async_set(LIGHT, STATE_OFF)
    await demo_hass.async_block_till_done()

    await _finish_starting(demo_hass)

    assert demo_hass.states.get(LIGHT).state == STATE_OFF


async def test_unavailable_entity_is_skipped(demo_hass, entry_options) -> None:
    """Driving an unreachable device would just fail.

    The observer re-drives on return-from-unavailable for anything held at 1-4,
    so this case is covered there rather than here, and must not be commanded
    while it is still down.
    """
    await _boot(demo_hass, entry_options, {PRI_AUTO_EMERGENCY: "turn_on"})
    demo_hass.states.async_set(LIGHT, STATE_UNAVAILABLE)
    await demo_hass.async_block_till_done()

    await _finish_starting(demo_hass)

    assert demo_hass.states.get(LIGHT).state == STATE_UNAVAILABLE


async def test_nothing_happens_when_ha_was_already_running(
    demo_hass, entry_options
) -> None:
    """Adding or reloading the integration by hand is not a restart.

    There has been no downtime, so there is nothing to reconcile, and firing a
    command because somebody opened the options flow would be alarming.
    """
    demo_hass.set_state(CoreState.running)
    entry = MockConfigEntry(domain=DOMAIN, options=entry_options, unique_id=DOMAIN)
    entry.add_to_hass(demo_hass)
    assert await demo_hass.config_entries.async_setup(entry.entry_id)
    await demo_hass.async_block_till_done()

    entry.runtime_data.async_write_slot(
        LIGHT,
        PRI_AUTO_EMERGENCY,
        Slot("light", "turn_on", {}, dt_util.utcnow()),
    )
    demo_hass.states.async_set(LIGHT, STATE_OFF)
    await demo_hass.async_block_till_done()

    await _finish_starting(demo_hass)

    assert demo_hass.states.get(LIGHT).state == STATE_OFF


async def test_reconcile_does_not_fire_after_unload(demo_hass, entry_options) -> None:
    """The pending timer must not outlive the config entry."""
    entry = await _boot(demo_hass, entry_options, {PRI_AUTO_EMERGENCY: "turn_on"})
    demo_hass.states.async_set(LIGHT, STATE_OFF)
    await demo_hass.async_block_till_done()

    assert await demo_hass.config_entries.async_unload(entry.entry_id)
    await demo_hass.async_block_till_done()

    await _finish_starting(demo_hass)

    assert demo_hass.states.get(LIGHT).state == STATE_OFF
