"""Graft the priority fields onto every wrapped service's frontend description.

The frontend renders forms from descriptions (each integration's services.yaml), not from the
schema the wrapper extends, so without this priority is YAML-only. A core patch adding the field to
`cv.ENTITY_SERVICE_FIELDS` would make this module unnecessary.
"""

from __future__ import annotations

import copy
import logging
from typing import Any

from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.service import (
    async_get_all_descriptions,
    async_set_service_schema,
)

from .const import (
    ATTR_PRIORITY,
    ATTR_PRIORITY_TTL,
    MAX_PRIORITY,
    MIN_PRIORITY,
)
from .store import PriorityManager

_LOGGER = logging.getLogger(__name__)


def _priority_fields(names: dict[int, str]) -> dict[str, Any]:
    return {
        ATTR_PRIORITY: {
            "name": "Priority",
            "description": (
                "Authority level for this command. Lower numbers win. Leave "
                f"unset for {names[MAX_PRIORITY]}, which behaves exactly as it "
                "always has: the last command wins."
            ),
            "required": False,
            "example": 3,
            "selector": {
                "select": {
                    "options": [
                        {
                            "value": str(priority),
                            "label": f"{priority} - {names[priority]}",
                        }
                        for priority in range(MIN_PRIORITY, MAX_PRIORITY + 1)
                    ],
                    "mode": "dropdown",
                }
            },
        },
        ATTR_PRIORITY_TTL: {
            "name": "Hold for",
            "description": (
                "How long this command keeps its priority before releasing it "
                "automatically. Leave unset to hold until something relinquishes "
                f"it. Not valid at {names[MAX_PRIORITY]}."
            ),
            "required": False,
            "example": "00:30:00",
            "selector": {"duration": {}},
        },
    }


async def async_patch_descriptions(
    hass: HomeAssistant, manager: PriorityManager, *, retry: bool = True
) -> None:
    """Also re-run on a rename, so it patches from the stored original, never its own output.

    ``async_get_all_descriptions`` imports every service-registering integration, so one that will
    not import fails the whole pass silently; hence the retry after startup and the loud log.
    """
    try:
        all_descriptions = await async_get_all_descriptions(hass)
    except Exception:
        if retry and not hass.is_running:
            _LOGGER.debug(
                "Priority could not load service descriptions yet; "
                "retrying once Home Assistant has started"
            )

            async def _retry(_event: Event) -> None:
                await async_patch_descriptions(hass, manager, retry=False)

            hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, _retry)
            return
        _LOGGER.exception(
            "Priority could not load service descriptions, so its fields will "
            "not appear in the UI. Arbitration itself is unaffected and "
            "priority still works from YAML"
        )
        return

    fields = _priority_fields(manager.priority_names)
    stored = manager.async_descriptions()
    patched = 0
    for domain, service in manager.async_originals():
        original = stored.get((domain, service))
        if original is None:
            existing = (all_descriptions.get(domain) or {}).get(service)
            if existing is None:
                continue
            original = copy.deepcopy(existing)
            manager.async_store_description(domain, service, original)

        updated = copy.deepcopy(original)
        updated.setdefault("fields", {})
        updated["fields"].update(copy.deepcopy(fields))
        async_set_service_schema(hass, domain, service, updated)
        patched += 1

    _LOGGER.info(
        "Priority added its fields to %s service descriptions", patched
    )


@callback
def async_restore_descriptions(
    hass: HomeAssistant, manager: PriorityManager
) -> None:
    """On unload."""
    for (domain, service), description in manager.async_descriptions().items():
        async_set_service_schema(hass, domain, service, description)
