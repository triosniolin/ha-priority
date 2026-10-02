"""One diagnostic sensor for the whole house: per-entity sensors would be hundreds of empties."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import MIN_PRIORITY, PRI_DEFAULT

if TYPE_CHECKING:
    from . import PriorityConfigEntry
    from .array import PriorityArray


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PriorityConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
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
        self._entry = entry
        self._manager = entry.runtime_data
        self._attr_unique_id = f"{entry.entry_id}_active_overrides"

    async def async_added_to_hass(self) -> None:
        @callback
        def _changed(_entity_id: str) -> None:
            self.async_write_ha_state()

        @callback
        def _renamed() -> None:
            self.async_write_ha_state()

        self.async_on_remove(self._manager.async_add_listener(_changed))
        self.async_on_remove(self._manager.async_add_names_listener(_renamed))

    @property
    def native_value(self) -> int:
        return len(self._overrides())

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        overrides = self._overrides()
        return {
            "overrides": overrides,
            "entity_id": sorted(overrides),
            "tracked_arrays": len(self._manager.async_all_arrays()),
            # The cards label every picker from this; the highest key is Default.
            "priority_levels": {
                str(priority): name
                for priority, name in self._manager.priority_names.items()
            },
        }

    def _overrides(self) -> dict[str, Any]:
        """Entities held above Default, from the incremental override set rather than every array."""
        names = self._manager.priority_names
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
                "priority_name": names[priority],
                "service": f"{slot.domain}.{slot.service}",
                "written_at": slot.written_at.isoformat(),
                "written_by": slot.written_by,
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
                # Without the queued levels a hold underneath reads as having vanished.
                "levels": self._levels(array, names),
            }
        return result

    def _levels(self, array: PriorityArray, names: dict[int, str]) -> dict[str, Any]:
        levels: dict[str, Any] = {}
        for priority in range(MIN_PRIORITY, PRI_DEFAULT):
            slot = array.get(priority)
            # A lapsed slot whose timer has not fired is not holding anything, as in effective().
            if slot is None or slot.is_expired():
                continue
            levels[str(priority)] = {
                "priority_name": names[priority],
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
