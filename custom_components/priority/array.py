"""The priority array: one slot per level, lowest-numbered occupied slot drives the device.

A slot holds a whole service call, not a BACnet value, because HA entities are multi-property
(`light.turn_on` carries brightness and colour together). That keeps it domain-agnostic at the
cost of per-attribute blending; the per-slot `data` dict leaves room for that without a migration.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Self

from homeassistant.util import dt as dt_util

from .const import (
    MAX_PRIORITY,
    MIN_PRIORITY,
    NUM_SLOTS,
    PERSISTED_PRIORITIES,
)


@dataclass(slots=True)
class Slot:
    """A single commanded value in the array."""

    domain: str
    # Already resolved away from `toggle`.
    service: str
    # Target and priority fields already stripped.
    data: dict[str, Any]
    written_at: datetime
    # Best-effort: "user:<name>", or an automation/script entity id.
    written_by: str | None = None
    # None holds until relinquished.
    expires_at: datetime | None = None

    def is_expired(self, now: datetime | None = None) -> bool:
        if self.expires_at is None:
            return False
        return (now or dt_util.utcnow()) >= self.expires_at

    def as_dict(self) -> dict[str, Any]:
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
    if not MIN_PRIORITY <= priority <= MAX_PRIORITY:
        raise ValueError(
            f"priority must be between {MIN_PRIORITY} and {MAX_PRIORITY}, got {priority}"
        )
    return priority - MIN_PRIORITY


@dataclass(slots=True)
class PriorityArray:
    """The command slots for one entity."""

    entity_id: str
    slots: list[Slot | None] = field(
        default_factory=lambda: [None] * NUM_SLOTS
    )

    def get(self, priority: int) -> Slot | None:
        return self.slots[_slot_index(priority)]

    def write(self, priority: int, slot: Slot) -> None:
        self.slots[_slot_index(priority)] = slot

    def clear(self, priority: int) -> bool:
        """Empty a slot. Returns True if it held anything."""
        index = _slot_index(priority)
        had_value = self.slots[index] is not None
        self.slots[index] = None
        return had_value

    def effective(self, now: datetime | None = None) -> tuple[int, Slot] | None:
        """Winning (priority, slot); skips expired slots so a late timer cannot hold control."""
        for index, slot in enumerate(self.slots):
            if slot is not None and not slot.is_expired(now):
                return index + MIN_PRIORITY, slot
        return None

    def lowest_occupied(self) -> int | None:
        """Ignores expiry: a firing lease is already expired, so effective() would skip it."""
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
        winner = self.effective()
        return None if winner is None else winner[0]

    def wins(self, priority: int) -> bool:
        """Ties win, so a writer can update its own command without relinquishing first."""
        current = self.effective_priority()
        return current is None or priority <= current

    def is_empty(self) -> bool:
        return all(slot is None for slot in self.slots)

    def as_dict(self, names: Mapping[int, str]) -> dict[str, Any]:
        """Full snapshot for the `get` service."""
        winner = self.effective()
        return {
            "entity_id": self.entity_id,
            "effective_priority": None if winner is None else winner[0],
            "effective_priority_name": (
                None if winner is None else names[winner[0]]
            ),
            "effective_command": None if winner is None else winner[1].as_dict(),
            "slots": {
                str(index + MIN_PRIORITY): (None if slot is None else slot.as_dict())
                for index, slot in enumerate(self.slots)
            },
        }

    def to_storage(self) -> dict[str, Any]:
        """Every override level; Default tracks a device that may have moved while HA was down."""
        return {
            "slots": {
                str(priority): slot.as_dict()
                for priority in PERSISTED_PRIORITIES
                if (slot := self.get(priority)) is not None
            }
        }

    @classmethod
    def from_storage(cls, entity_id: str, raw: dict[str, Any]) -> Self:
        """Drops anything that no longer parses."""
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
