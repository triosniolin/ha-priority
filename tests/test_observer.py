"""Out-of-band changes, restart survival, and availability."""

from __future__ import annotations

from datetime import timedelta

from freezegun.api import FrozenDateTimeFactory
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from homeassistant.const import STATE_UNAVAILABLE

from custom_components.priority.array import Slot
from custom_components.priority.const import (
    DOMAIN,
    PRI_AUTO,
    PRI_MANUAL,
    PRI_DEFAULT,
)
from homeassistant.util import dt as dt_util

from . import mocks

LIGHT = "light.one"
SWITCH = "switch.one"


def _light():
    return mocks.ENTITIES["light"][0]


async def _wall_switch(hass, entity_id: str, state: str) -> None:
    """Simulate a change with no Home Assistant service call behind it."""
    attrs = dict(hass.states.get(entity_id).attributes)
    hass.states.async_set(entity_id, state, attrs)
    await hass.async_block_till_done()


# ----------------------------------------------------------------------
# Out-of-band detection
# ----------------------------------------------------------------------


async def test_wall_switch_lands_in_the_default_slot(priority_entry, demo_hass) -> None:
    await _wall_switch(demo_hass, SWITCH, "on")

    array = priority_entry.runtime_data.async_peek_array(SWITCH)
    assert array is not None
    slot = array.get(PRI_DEFAULT)
    assert slot.service == "turn_on"
    assert slot.written_by == "out_of_band"


async def test_wall_switch_is_not_snapped_back(priority_entry, demo_hass) -> None:
    """We record reality; we never argue with somebody at a light switch."""
    await _wall_switch(demo_hass, SWITCH, "on")
    assert demo_hass.states.get(SWITCH).state == "on"

    await _wall_switch(demo_hass, SWITCH, "off")
    assert demo_hass.states.get(SWITCH).state == "off"


async def test_automation_still_beats_a_wall_switch(
    priority_entry, demo_hass
) -> None:
    """Recorded at Default, so an explicit PRI 4 write still wins afterwards."""
    await _wall_switch(demo_hass, SWITCH, "on")

    await demo_hass.services.async_call(
        "switch",
        "turn_off",
        {"entity_id": SWITCH, "priority": PRI_AUTO},
        blocking=True,
    )
    await demo_hass.async_block_till_done()
    assert demo_hass.states.get(SWITCH).state == "off"


async def test_our_own_dispatch_is_not_recorded_as_out_of_band(
    priority_entry, demo_hass
) -> None:
    """The context map has to stop us mistaking our own writes for a human."""
    await demo_hass.services.async_call(
        "light",
        "turn_on",
        {"entity_id": LIGHT, "priority": PRI_MANUAL},
        blocking=True,
    )
    await demo_hass.async_block_till_done()

    array = priority_entry.runtime_data.async_peek_array(LIGHT)
    assert array.get(PRI_DEFAULT) is None
    assert array.effective_priority() == PRI_MANUAL


async def test_out_of_band_does_not_override_a_held_slot(
    priority_entry, demo_hass
) -> None:
    """A wall switch is recorded, but it does not take control from PRI 3."""
    await demo_hass.services.async_call(
        "switch",
        "turn_off",
        {"entity_id": SWITCH, "priority": PRI_MANUAL},
        blocking=True,
    )
    await demo_hass.async_block_till_done()

    await _wall_switch(demo_hass, SWITCH, "on")

    array = priority_entry.runtime_data.async_peek_array(SWITCH)
    assert array.get(PRI_DEFAULT).service == "turn_on"
    assert array.effective_priority() == PRI_MANUAL


async def test_attribute_only_change_is_ignored(priority_entry, demo_hass) -> None:
    """A device reporting a new brightness is not somebody commanding it."""
    state = demo_hass.states.get(SWITCH)
    demo_hass.states.async_set(SWITCH, state.state, {"extra": 1})
    await demo_hass.async_block_till_done()

    array = priority_entry.runtime_data.async_peek_array(SWITCH)
    assert array is None or array.get(PRI_DEFAULT) is None


# ----------------------------------------------------------------------
# Availability
# ----------------------------------------------------------------------


async def test_returning_from_unavailable_does_nothing_at_default(
    priority_entry, demo_hass
) -> None:
    """The no-op guarantee, applied to flapping devices.

    At the Default level the array only records ordinary last-wins traffic.
    Re-sending it when a device rejoins would be a command nobody issued, and
    on a marginal radio link a flapping entity would produce a stream of them.
    Stock Home Assistant does nothing here, so neither do we.
    """
    await demo_hass.services.async_call(
        "light", "turn_on", {"entity_id": LIGHT, "brightness": 55}, blocking=True
    )
    await demo_hass.async_block_till_done()

    demo_hass.states.async_set(LIGHT, STATE_UNAVAILABLE)
    await demo_hass.async_block_till_done()

    before = len(_light().turn_on_calls)
    demo_hass.states.async_set(LIGHT, "off")
    await demo_hass.async_block_till_done()

    assert len(_light().turn_on_calls) == before
    assert demo_hass.states.get(LIGHT).state == "off"


async def test_returning_from_unavailable_redrives_a_held_override(
    priority_entry, demo_hass
) -> None:
    """A real override does survive the device going away and coming back."""
    await demo_hass.services.async_call(
        "light",
        "turn_on",
        {"entity_id": LIGHT, "priority": PRI_MANUAL, "brightness": 123},
        blocking=True,
    )
    await demo_hass.async_block_till_done()

    demo_hass.states.async_set(LIGHT, STATE_UNAVAILABLE)
    await demo_hass.async_block_till_done()

    before = len(_light().turn_on_calls)
    demo_hass.states.async_set(LIGHT, "off")
    await demo_hass.async_block_till_done()

    assert len(_light().turn_on_calls) - before == 1
    assert demo_hass.states.get(LIGHT).attributes["brightness"] == 123


async def test_unavailable_transition_is_not_recorded_as_manual(
    priority_entry, demo_hass
) -> None:
    """Going unavailable must not be written into the array as a command."""
    await _wall_switch(demo_hass, SWITCH, "on")
    demo_hass.states.async_set(SWITCH, STATE_UNAVAILABLE)
    await demo_hass.async_block_till_done()

    array = priority_entry.runtime_data.async_peek_array(SWITCH)
    assert array.get(PRI_DEFAULT).service == "turn_on"


# ----------------------------------------------------------------------
# Restart survival
# ----------------------------------------------------------------------


async def test_high_slots_survive_a_restart(demo_hass, entry_options) -> None:
    """Every override level restores; only slot 5 is deliberately re-derived.

    Level 4 used to be dropped here, on the reasoning that an automation
    re-asserts on its own triggers. An automation that fires on an edge (a peak
    window opening, a threshold crossing) has no trigger to re-fire after a
    reboot, so dropping its slot handed control back to whatever it was
    deliberately overriding. Nothing reaches level 4 without a caller asking
    for it by name, so it is as much a statement of intent as level 3.
    """
    entry = MockConfigEntry(domain=DOMAIN, options=entry_options, unique_id=DOMAIN)
    entry.add_to_hass(demo_hass)
    assert await demo_hass.config_entries.async_setup(entry.entry_id)
    await demo_hass.async_block_till_done()

    manager = entry.runtime_data
    manager.async_write_slot(
        LIGHT,
        PRI_MANUAL,
        Slot("light", "turn_on", {"brightness": 77}, dt_util.utcnow()),
    )
    manager.async_write_slot(
        LIGHT, PRI_AUTO, Slot("light", "turn_off", {}, dt_util.utcnow())
    )
    await manager.async_save()

    assert await demo_hass.config_entries.async_unload(entry.entry_id)
    await demo_hass.async_block_till_done()

    assert await demo_hass.config_entries.async_setup(entry.entry_id)
    await demo_hass.async_block_till_done()

    restored = entry.runtime_data.async_peek_array(LIGHT)
    assert restored.get(PRI_MANUAL).data == {"brightness": 77}
    assert restored.get(PRI_AUTO).service == "turn_off"
    assert restored.get(PRI_DEFAULT) is None


async def test_restored_level_4_still_outranks_default_traffic(
    demo_hass, entry_options
) -> None:
    """The point of persisting level 4: it keeps suppressing after a reboot.

    A restored slot is not re-driven at startup (nothing is), so the proof that
    it survived meaningfully is that the next Default-level command is recorded
    and not dispatched, exactly as it would have been before the restart.
    """
    entry = MockConfigEntry(domain=DOMAIN, options=entry_options, unique_id=DOMAIN)
    entry.add_to_hass(demo_hass)
    assert await demo_hass.config_entries.async_setup(entry.entry_id)
    await demo_hass.async_block_till_done()

    await demo_hass.services.async_call(
        "light",
        "turn_on",
        {"entity_id": LIGHT, "brightness": 200, "priority": PRI_AUTO},
        blocking=True,
    )
    await entry.runtime_data.async_save()

    assert await demo_hass.config_entries.async_unload(entry.entry_id)
    await demo_hass.async_block_till_done()
    assert await demo_hass.config_entries.async_setup(entry.entry_id)
    await demo_hass.async_block_till_done()

    manager = entry.runtime_data
    assert manager.async_peek_array(LIGHT).effective_priority() == PRI_AUTO

    # An ordinary command, the kind a person or an unqualified automation
    # issues. It must lose to the restored hold rather than sail through.
    before = demo_hass.states.get(LIGHT).state
    await demo_hass.services.async_call(
        "light", "turn_off", {"entity_id": LIGHT}, blocking=True
    )
    await demo_hass.async_block_till_done()

    assert demo_hass.states.get(LIGHT).state == before
    array = manager.async_peek_array(LIGHT)
    assert array.get(PRI_DEFAULT).service == "turn_off"
    assert array.effective_priority() == PRI_AUTO


async def test_lapsed_lease_is_dropped_on_restart(
    demo_hass, entry_options, freezer: FrozenDateTimeFactory
) -> None:
    """An override whose lease ran out while HA was down must not come back."""
    entry = MockConfigEntry(domain=DOMAIN, options=entry_options, unique_id=DOMAIN)
    entry.add_to_hass(demo_hass)
    assert await demo_hass.config_entries.async_setup(entry.entry_id)
    await demo_hass.async_block_till_done()

    manager = entry.runtime_data
    manager.async_write_slot(
        LIGHT,
        PRI_MANUAL,
        Slot(
            "light",
            "turn_on",
            {},
            dt_util.utcnow(),
            expires_at=dt_util.utcnow() + timedelta(seconds=300),
        ),
    )
    await manager.async_save()

    assert await demo_hass.config_entries.async_unload(entry.entry_id)
    await demo_hass.async_block_till_done()

    freezer.tick(timedelta(seconds=600))

    assert await demo_hass.config_entries.async_setup(entry.entry_id)
    await demo_hass.async_block_till_done()

    restored = entry.runtime_data.async_peek_array(LIGHT)
    assert restored is None or restored.get(PRI_MANUAL) is None


async def test_live_lease_is_rearmed_after_restart(
    demo_hass, entry_options, freezer: FrozenDateTimeFactory
) -> None:
    """A lease with time left must keep counting down across a restart."""
    entry = MockConfigEntry(domain=DOMAIN, options=entry_options, unique_id=DOMAIN)
    entry.add_to_hass(demo_hass)
    assert await demo_hass.config_entries.async_setup(entry.entry_id)
    await demo_hass.async_block_till_done()

    entry.runtime_data.async_write_slot(
        LIGHT,
        PRI_MANUAL,
        Slot(
            "light",
            "turn_on",
            {},
            dt_util.utcnow(),
            expires_at=dt_util.utcnow() + timedelta(seconds=600),
        ),
    )
    await entry.runtime_data.async_save()

    assert await demo_hass.config_entries.async_unload(entry.entry_id)
    await demo_hass.async_block_till_done()
    assert await demo_hass.config_entries.async_setup(entry.entry_id)
    await demo_hass.async_block_till_done()

    manager = entry.runtime_data
    assert manager.async_peek_array(LIGHT).get(PRI_MANUAL) is not None

    freezer.tick(timedelta(seconds=601))
    async_fire_time_changed(demo_hass)
    await demo_hass.async_block_till_done()

    assert manager.async_peek_array(LIGHT).get(PRI_MANUAL) is None
