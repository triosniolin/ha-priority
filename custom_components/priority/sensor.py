"""A single diagnostic sensor summarising active overrides.

Scope defaults to every supported entity, so one diagnostic entity per managed
entity would mean hundreds of near-empty entities. Instead this reports only
the entities where something above Default currently holds control - which
is exactly the set worth looking at on a dashboard.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import MIN_PRIORITY, PRI_DEFAULT, PRIORITY_NAMES

if TYPE_CHECKING:
    from . import PriorityConfigEntry
    from .array import PriorityArray


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PriorityConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the diagnostic sensor."""
    async_add_entities([PriorityOverrideSensor(entry)])


class PriorityOverrideSensor(SensorEntity):
    """Counts entities currently held by something above Default."""

    _attr_has_entity_name = True
    _attr_name = "Active overrides"
    _attr_icon = "mdi:priority-high"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_should_poll = False

    def __init__(self, entry: PriorityConfigEntry) -> None:
        """Initialise the sensor."""
        self._entry = entry
        self._manager = entry.runtime_data
        self._attr_unique_id = f"{entry.entry_id}_active_overrides"

    async def async_added_to_hass(self) -> None:
        """Subscribe to array changes."""

        @callback
        def _changed(_entity_id: str) -> None:
            self.async_write_ha_state()

        self.async_on_remove(self._manager.async_add_listener(_changed))

    @property
    def native_value(self) -> int:
        """Number of entities under an override."""
        return len(self._overrides())

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """The overrides themselves, for templates and dashboards."""
        overrides = self._overrides()
        return {
            "overrides": overrides,
            "entity_id": sorted(overrides),
            "tracked_arrays": len(self._manager.async_all_arrays()),
        }

    def _overrides(self) -> dict[str, Any]:
        """Entities whose effective priority outranks Default.

        Reads the manager's incrementally-maintained override set rather than
        scanning every array: at house-wide scope that set is a handful of
        entries while the arrays number in the hundreds.
        """
        result: dict[str, Any] = {}
        for entity_id in sorted(self._manager.async_overridden()):
            array = self._manager.async_peek_array(entity_id)
            if array is None:
                continue
            winner = array.effective()
            if winner is None or winner[0] >= PRI_DEFAULT:
                continue
            priority, slot = winner
            result[entity_id] = {
                # Winner, kept flat so an older cached card still renders.
                "priority": priority,
                "priority_name": PRIORITY_NAMES[priority],
                "service": f"{slot.domain}.{slot.service}",
                "written_at": slot.written_at.isoformat(),
                "written_by": slot.written_by,
                # The card counts down against this, so it has to be here.
                "expires_at": (
                    None
                    if slot.expires_at is None
                    else slot.expires_at.isoformat()
                ),
                "friendly_name": (
                    state.name
                    if (state := self.hass.states.get(entity_id)) is not None
                    else entity_id
                ),
                # Every held level, not just the winner. Without this the card
                # cannot show what is queued behind what is driving, which
                # reads as an override having silently gone missing. Bounded:
                # this only covers overridden entities, four slots at most.
                "levels": self._levels(array),
            }
        return result

    def _levels(self, array: PriorityArray) -> dict[str, Any]:
        """Every occupied override level on one array, lowest first."""
        levels: dict[str, Any] = {}
        for priority in range(MIN_PRIORITY, PRI_DEFAULT):
            slot = array.get(priority)
            # Skip a lapsed slot whose timer has not fired yet, for the same
            # reason effective() does: it is not holding anything.
            if slot is None or slot.is_expired():
                continue
            levels[str(priority)] = {
                "priority_name": PRIORITY_NAMES[priority],
                "service": f"{slot.domain}.{slot.service}",
                "written_at": slot.written_at.isoformat(),
                "written_by": slot.written_by,
                "expires_at": (
                    None
                    if slot.expires_at is None
                    else slot.expires_at.isoformat()
                ),
            }
        return levels
