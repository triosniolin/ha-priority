"""The priority array itself.

A commandable entity gets one array of five slots. Slot index 0 is PRI 1
(Manual Emergency), index 4 is PRI 5 (Default). The highest-priority
non-empty slot is the *effective* command: the one that should currently be
driving the device.

Unlike BACnet, a slot holds a whole service call rather than a single value,
because Home Assistant entities are multi-property: `light.turn_on` carries
brightness, colour, transition and effect together. Storing the call keeps the
design domain-agnostic - climate, cover and fan work without per-domain value
mapping - at the cost of not being able to blend attributes across priorities.
The storage format keeps a per-slot `data` dict so per-attribute arrays can be
layered on later without a breaking migration.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Self

from homeassistant.util import dt as dt_util

from .const import (
    MAX_PRIORITY,
    MIN_PRIORITY,
    NUM_SLOTS,
    PERSISTED_PRIORITIES,
    PRIORITY_NAMES,
)


@dataclass(slots=True)
class Slot:
    """A single commanded value in the array."""

    domain: str
    """Service domain, e.g. "light"."""

    service: str
    """Service name, already resolved away from `toggle`, e.g. "turn_on"."""

    data: dict[str, Any]
    """Call payload with entity/target and priority fields already stripped."""

    written_at: datetime
    """When this slot was last written."""

    written_by: str | None = None
    """Best-effort attribution: a user id, or an automation/script entity id."""

    expires_at: datetime | None = None
    """When this slot self-clears. None means it holds until relinquished.

    An override with no end is a trap: "turn the lights on at Manual Emergency"
    is easy to issue and easy to forget, and until somebody relinquishes it
    every automation below is dead. A TTL makes the override a loan rather than
    a seizure.
    """

    def is_expired(self, now: datetime | None = None) -> bool:
        """Whether this slot's lease has run out."""
        if self.expires_at is None:
            return False
        return (now or dt_util.utcnow()) >= self.expires_at

    def as_dict(self) -> dict[str, Any]:
        """Serialise for storage and for the diagnostic attribute."""
        return {
            "domain": self.domain,
            "service": self.service,
            "data": self.data,
            "written_at": self.written_at.isoformat(),
            "written_by": self.written_by,
            "expires_at": (
                None if self.expires_at is None else self.expires_at.isoformat()
            ),
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Self | None:
        """Rebuild from storage, tolerating anything malformed."""
        try:
            written_at = dt_util.parse_datetime(raw["written_at"])
            if written_at is None:
                return None
            expires_raw = raw.get("expires_at")
            expires_at = (
                dt_util.parse_datetime(expires_raw) if expires_raw else None
            )
            return cls(
                domain=raw["domain"],
                service=raw["service"],
                data=dict(raw["data"]),
                written_at=written_at,
                written_by=raw.get("written_by"),
                expires_at=expires_at,
            )
        except (KeyError, TypeError, ValueError):
            return None


def _slot_index(priority: int) -> int:
    """Map a priority level to its array index."""
    if not MIN_PRIORITY <= priority <= MAX_PRIORITY:
        raise ValueError(
            f"priority must be between {MIN_PRIORITY} and {MAX_PRIORITY}, got {priority}"
        )
    return priority - MIN_PRIORITY


@dataclass(slots=True)
class PriorityArray:
    """The five command slots for one entity."""

    entity_id: str
    slots: list[Slot | None] = field(
        default_factory=lambda: [None] * NUM_SLOTS
    )

    def get(self, priority: int) -> Slot | None:
        """Return the slot at a priority, or None if it is empty."""
        return self.slots[_slot_index(priority)]

    def write(self, priority: int, slot: Slot) -> None:
        """Write a command into a slot."""
        self.slots[_slot_index(priority)] = slot

    def clear(self, priority: int) -> bool:
        """Empty a slot. Returns True if it held anything."""
        index = _slot_index(priority)
        had_value = self.slots[index] is not None
        self.slots[index] = None
        return had_value

    def effective(self, now: datetime | None = None) -> tuple[int, Slot] | None:
        """Return the winning (priority, slot), or None if the array is empty.

        Expired slots are skipped rather than trusted. A timer normally clears
        them, but a missed or delayed timer must never leave a lapsed override
        in control of a device.
        """
        for index, slot in enumerate(self.slots):
            if slot is not None and not slot.is_expired(now):
                return index + MIN_PRIORITY, slot
        return None

    def lowest_occupied(self) -> int | None:
        """Lowest-numbered occupied priority, ignoring expiry.

        Needed when a lease lapses: at that moment the slot is already expired,
        so :meth:`effective` would skip it and the caller would wrongly conclude
        it had never been in control - and skip the hand-back dispatch.
        """
        for index, slot in enumerate(self.slots):
            if slot is not None:
                return index + MIN_PRIORITY
        return None

    def purge_expired(self, now: datetime | None = None) -> list[int]:
        """Drop every lapsed slot. Returns the priorities that were cleared."""
        cleared: list[int] = []
        for index, slot in enumerate(self.slots):
            if slot is not None and slot.is_expired(now):
                self.slots[index] = None
                cleared.append(index + MIN_PRIORITY)
        return cleared

    def effective_priority(self) -> int | None:
        """Return the priority currently driving the entity, if any."""
        winner = self.effective()
        return None if winner is None else winner[0]

    def wins(self, priority: int) -> bool:
        """Whether a write at this priority would take control of the entity.

        A write at the level that already holds control still wins - that is how
        an automation updates its own command without relinquishing first.
        """
        current = self.effective_priority()
        return current is None or priority <= current

    def is_empty(self) -> bool:
        """Whether every slot is empty."""
        return all(slot is None for slot in self.slots)

    def as_dict(self) -> dict[str, Any]:
        """Full snapshot, for the diagnostic attribute and the `get` service."""
        winner = self.effective()
        return {
            "entity_id": self.entity_id,
            "effective_priority": None if winner is None else winner[0],
            "effective_priority_name": (
                None if winner is None else PRIORITY_NAMES[winner[0]]
            ),
            "effective_command": None if winner is None else winner[1].as_dict(),
            "slots": {
                str(index + MIN_PRIORITY): (None if slot is None else slot.as_dict())
                for index, slot in enumerate(self.slots)
            },
        }

    def to_storage(self) -> dict[str, Any]:
        """Serialise only the slots that should survive a restart.

        Slot 5 tracks physical reality, which may have moved while Home
        Assistant was down. It is therefore not restored at all - and, note,
        not re-derived either: see the comment in store.py explaining why
        seeding it from live state is a command nobody issued. Slot 4 is not persisted either: an automation that wants
        its command to survive a restart will re-assert it on its own triggers,
        and restoring a stale automation command would fight that.
        """
        return {
            "slots": {
                str(priority): slot.as_dict()
                for priority in PERSISTED_PRIORITIES
                if (slot := self.get(priority)) is not None
            }
        }

    @classmethod
    def from_storage(cls, entity_id: str, raw: dict[str, Any]) -> Self:
        """Rebuild from storage, dropping anything that no longer parses."""
        array = cls(entity_id=entity_id)
        for key, value in (raw.get("slots") or {}).items():
            try:
                priority = int(key)
            except (TypeError, ValueError):
                continue
            if priority not in PERSISTED_PRIORITIES:
                continue
            if (slot := Slot.from_dict(value)) is not None:
                array.write(priority, slot)
        return array
