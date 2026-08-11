"""Findings from an outside review, 2026-08-11.

Each of these was verified to fail against the code as it stood before the fix.
"""

from __future__ import annotations

import pytest
import voluptuous as vol
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.exceptions import ServiceValidationError

from custom_components.priority.const import (
    CONF_EXCLUDED_ENTITIES,
    CONF_SCOPE,
    DOMAIN,
    MAX_PRIORITY,
    PRI_MANUAL,
    PRIORITY_NAMES,
    SCOPE_ALL,
)

LIGHT = "light.one"
OTHER = "light.two"


async def _entry(hass, options=None) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id=DOMAIN, options=options or {CONF_SCOPE: SCOPE_ALL}
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


# ----------------------------------------------------------------------
# priority_ttl leaked into the original handler on the all-unmanaged path
# ----------------------------------------------------------------------


async def test_ttl_does_not_reach_the_entity_method(demo_hass) -> None:
    """A stray kwarg crashes any handler with a strict signature.

    Core's remove_entity_service_fields only strips cv.ENTITY_SERVICE_FIELDS,
    so anything we add and fail to remove is passed to the entity method as a
    keyword argument. cover and climate take **kwargs and swallow it silently;
    fan.set_percentage takes `percentage: int` and raises TypeError on an
    otherwise ordinary call.
    """
    await _entry(demo_hass, {CONF_SCOPE: SCOPE_ALL, CONF_EXCLUDED_ENTITIES: ["fan.one"]})

    await demo_hass.services.async_call(
        "fan",
        "set_percentage",
        {"entity_id": "fan.one", "percentage": 40, "priority_ttl": 60},
        blocking=True,
    )
    await demo_hass.async_block_till_done()

    assert demo_hass.states.get("fan.one").attributes["percentage"] == 40


async def test_ttl_does_not_reach_the_entity_method_when_managed(demo_hass) -> None:
    """The arbitrated path must be just as clean."""
    await _entry(demo_hass)

    await demo_hass.services.async_call(
        "fan",
        "set_percentage",
        {"entity_id": "fan.one", "percentage": 55, "priority": PRI_MANUAL,
         "priority_ttl": 60},
        blocking=True,
    )
    await demo_hass.async_block_till_done()

    assert demo_hass.states.get("fan.one").attributes["percentage"] == 55


# ----------------------------------------------------------------------
# An undispatchable slot used to hold control forever, silently
# ----------------------------------------------------------------------


async def test_priority_set_rejects_a_payload_the_domain_cannot_accept(
    demo_hass,
) -> None:
    """The black hole: the slot won arbitration but could never be driven.

    Everything below it stayed suppressed while nothing drove the device, and
    the only trace was a log line.
    """
    entry = await _entry(demo_hass)

    with pytest.raises(ServiceValidationError):
        await demo_hass.services.async_call(
            DOMAIN,
            "set",
            {
                "entity_id": LIGHT,
                "priority": PRI_MANUAL,
                "service": "turn_on",
                "data": {"brightnesss": 200},  # typo
            },
            blocking=True,
        )
    await demo_hass.async_block_till_done()

    array = entry.runtime_data.async_peek_array(LIGHT)
    assert array is None or array.get(PRI_MANUAL) is None, "no slot may be written"


async def test_priority_set_accepts_a_valid_payload(demo_hass) -> None:
    """The validation must not reject legitimate commands."""
    entry = await _entry(demo_hass)

    await demo_hass.services.async_call(
        DOMAIN,
        "set",
        {
            "entity_id": LIGHT,
            "priority": PRI_MANUAL,
            "service": "turn_on",
            "data": {"brightness": 200},
        },
        blocking=True,
    )
    await demo_hass.async_block_till_done()

    assert entry.runtime_data.async_peek_array(LIGHT).get(PRI_MANUAL) is not None
    assert demo_hass.states.get(LIGHT).attributes["brightness"] == 200


# ----------------------------------------------------------------------
# priority.set applied partially on error
# ----------------------------------------------------------------------


async def test_priority_set_is_all_or_nothing(demo_hass) -> None:
    """Raising part-way through left earlier entities holding new slots."""
    entry = await _entry(
        demo_hass, {CONF_SCOPE: SCOPE_ALL, CONF_EXCLUDED_ENTITIES: [OTHER]}
    )

    with pytest.raises(ServiceValidationError):
        await demo_hass.services.async_call(
            DOMAIN,
            "set",
            {
                "entity_id": [LIGHT, OTHER],  # OTHER is excluded, so this fails
                "priority": PRI_MANUAL,
                "service": "turn_on",
                "data": {},
            },
            blocking=True,
        )
    await demo_hass.async_block_till_done()

    first = entry.runtime_data.async_peek_array(LIGHT)
    assert first is None or first.get(PRI_MANUAL) is None, (
        "the entity processed before the failure must not keep a slot"
    )


# ----------------------------------------------------------------------
# Level naming left over from the rename
# ----------------------------------------------------------------------


async def test_errors_name_a_level_that_actually_exists(demo_hass) -> None:
    """The error used to say "Manual Low", which appears nowhere in the UI."""
    await _entry(demo_hass)

    with pytest.raises(ServiceValidationError) as err:
        await demo_hass.services.async_call(
            "light",
            "turn_on",
            {"entity_id": LIGHT, "priority": MAX_PRIORITY, "priority_ttl": 60},
            blocking=True,
        )

    assert PRIORITY_NAMES[MAX_PRIORITY] in str(err.value)
    assert "Manual Low" not in str(err.value)
