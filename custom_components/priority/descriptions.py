"""Make the priority fields visible in the Home Assistant frontend.

Wrapping a service extends the schema it *accepts*, but the frontend does not
render forms from schemas - it renders them from service *descriptions*, which
core builds from each integration's `services.yaml`. Those files are not ours to
edit, so without this module `priority` works only from hand-written YAML, which
makes the whole feature a toy.

`async_set_service_schema` re-registers a description at runtime and pops the
all-descriptions cache, so the frontend picks up the change. Patching every
wrapped service therefore puts `priority` and `priority_ttl` into every place
the frontend renders a service call: Developer Tools, the automation editor, the
script editor, and button-card tap actions.

The core patch would not need any of this - a field added to
`cv.ENTITY_SERVICE_FIELDS` would be described once, centrally. This module is
the custom-integration workaround for exactly that gap.
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
    PRIORITY_NAMES,
)
from .store import PriorityManager

_LOGGER = logging.getLogger(__name__)


def _priority_fields() -> dict[str, Any]:
    """The two fields to graft onto every arbitrated service description."""
    return {
        ATTR_PRIORITY: {
            "name": "Priority",
            "description": (
                "Authority level for this command. Lower numbers win. Leave "
                "unset for Default, which behaves exactly as it always has: "
                "the last command wins."
            ),
            "required": False,
            "example": 3,
            "selector": {
                "select": {
                    "options": [
                        {
                            "value": str(priority),
                            "label": f"{priority} - {PRIORITY_NAMES[priority]}",
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
                "it. Not valid at Default."
            ),
            "required": False,
            "example": "00:30:00",
            "selector": {"duration": {}},
        },
    }


async def async_patch_descriptions(
    hass: HomeAssistant, manager: PriorityManager, *, retry: bool = True
) -> None:
    """Add the priority fields to every wrapped service's description.

    ``async_get_all_descriptions`` imports every integration that has registered
    a service in order to read its ``services.yaml``. A single integration that
    cannot be imported therefore takes the whole pass down with it - and the
    visible symptom is not an error, it is priority quietly never appearing in
    any form. So a failure here retries once Home Assistant has finished
    starting, and complains loudly if it still cannot manage it.
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

    patched = 0
    for domain, service in manager.async_originals():
        existing = (all_descriptions.get(domain) or {}).get(service)
        if existing is None:
            continue

        manager.async_store_description(domain, service, copy.deepcopy(existing))

        updated = copy.deepcopy(existing)
        updated.setdefault("fields", {})
        updated["fields"].update(_priority_fields())
        async_set_service_schema(hass, domain, service, updated)
        patched += 1

    _LOGGER.info(
        "Priority added its fields to %s service descriptions", patched
    )


@callback
def async_restore_descriptions(
    hass: HomeAssistant, manager: PriorityManager
) -> None:
    """Put the original descriptions back on unload."""
    for (domain, service), description in manager.async_descriptions().items():
        async_set_service_schema(hass, domain, service, description)
