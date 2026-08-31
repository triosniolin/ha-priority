"""The Home Assistant internals this integration depends on.

Everything here is core *internals*, not public API. If Home Assistant renames
or reshapes any of it, the integration breaks, and the visible symptom is every
supported entity in the house going unresponsive.

These assertions exist so that failure is one obvious line naming exactly what
moved, rather than eighty confusing errors in the behavioural suite. Run against
whichever core version CI is testing; a red build here is the signal to look at
the upstream changelog before anyone upgrades.
"""

from __future__ import annotations

import inspect

import voluptuous as vol

from homeassistant.const import (
    ENTITY_MATCH_ALL,
    ENTITY_MATCH_NONE,
    EVENT_LOGBOOK_ENTRY,
    EVENT_SERVICE_REGISTERED,
    EVENT_SERVICE_REMOVED,
)
from homeassistant.core import HomeAssistant, Service, ServiceCall
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import target as target_helpers
from homeassistant.helpers.event import async_track_point_in_utc_time
from homeassistant.helpers.service import (
    async_get_all_descriptions,
    async_set_service_schema,
    remove_entity_service_fields,
)


async def test_service_registry_is_still_introspectable(hass) -> None:
    """We read the live registry to capture handlers before wrapping them.

    Checked against a real instance, not the class: `services` is assigned in
    __init__, so `hasattr(HomeAssistant, "services")` is False and would make
    this test lie.
    """
    from homeassistant.core import ServiceRegistry

    assert isinstance(hass.services, ServiceRegistry)
    assert hasattr(ServiceRegistry, "async_services_internal")
    assert hasattr(ServiceRegistry, "async_register")
    assert hasattr(ServiceRegistry, "async_remove")

    registry = hass.services.async_services_internal()
    assert isinstance(registry, dict), "the live registry must stay a plain dict"


def test_service_object_still_exposes_job_schema_and_response() -> None:
    """We re-register a wrapper carrying the original's schema and job."""
    for attr in ("job", "schema", "supports_response"):
        assert attr in Service.__slots__, f"Service lost .{attr}"

    from homeassistant.core import HassJob

    assert hasattr(HassJob, "target"), "HassJob.target is how we call the original"


def test_entity_service_fields_is_a_single_dict() -> None:
    """One dict governs every entity service; it is where a core patch would add
    the priority field, and what remove_entity_service_fields strips."""
    assert isinstance(cv.ENTITY_SERVICE_FIELDS, dict)
    keys = {str(k) for k in cv.ENTITY_SERVICE_FIELDS}
    for expected in ("entity_id", "device_id", "area_id", "floor_id", "label_id"):
        assert any(expected in k for k in keys), f"{expected} missing"


def test_remove_entity_service_fields_strips_only_those_fields() -> None:
    """The assumption behind a real bug.

    Anything we add to a call and fail to strip ourselves is handed to the
    entity method as a keyword argument, because core removes only its own
    targeting fields. Domains taking **kwargs swallow it; ones with a strict
    signature (fan.set_percentage) raise TypeError.
    """
    call = ServiceCall(
        None,  # type: ignore[arg-type]
        "light",
        "turn_on",
        {"entity_id": ["light.a"], "brightness": 5, "priority_ttl": 60},
    )
    remaining = remove_entity_service_fields(call)
    assert "entity_id" not in remaining, "core no longer strips entity_id"
    assert remaining.get("brightness") == 5
    assert "priority_ttl" in remaining, (
        "core started stripping unknown keys; our own stripping may now be "
        "redundant, but verify before removing it"
    )


def test_target_resolution_helpers_exist() -> None:
    """How a call's targets become concrete entity ids."""
    assert hasattr(target_helpers, "TargetSelection")
    assert hasattr(target_helpers, "async_extract_referenced_entity_ids")
    sig = inspect.signature(target_helpers.async_extract_referenced_entity_ids)
    assert "hass" in sig.parameters
    fields = target_helpers.SelectedEntities.__dataclass_fields__
    assert "referenced" in fields and "indirectly_referenced" in fields


def test_service_description_rewriting_still_works() -> None:
    """How the priority fields reach the automation editor."""
    assert callable(async_get_all_descriptions)
    assert callable(async_set_service_schema)
    src = inspect.getsource(async_set_service_schema)
    assert "ALL_SERVICE_DESCRIPTIONS_CACHE" in src, (
        "async_set_service_schema no longer invalidates the all-descriptions "
        "cache, so the frontend will not pick up our added fields"
    )


def test_sentinels_and_events_unchanged() -> None:
    assert ENTITY_MATCH_ALL == "all"
    assert ENTITY_MATCH_NONE == "none"
    assert EVENT_SERVICE_REGISTERED == "service_registered"
    assert EVENT_SERVICE_REMOVED == "service_removed"
    assert EVENT_LOGBOOK_ENTRY == "logbook_entry"


def test_frontend_and_timer_helpers_exist() -> None:
    """Card registration and lease expiry."""
    from homeassistant.components.frontend import add_extra_js_url, remove_extra_js_url
    from homeassistant.components.http import StaticPathConfig

    assert callable(add_extra_js_url)
    assert callable(remove_extra_js_url)
    assert StaticPathConfig is not None
    assert callable(async_track_point_in_utc_time)


def test_entity_service_schemas_still_accept_extra_validation() -> None:
    """We wrap the original schema rather than rebuilding it."""
    schema = cv.make_entity_service_schema({vol.Optional("brightness"): int})
    out = schema({"entity_id": ["light.a"], "brightness": 5})
    assert out["brightness"] == 5
    try:
        schema({"entity_id": ["light.a"], "nonsense": 1})
    except vol.Invalid:
        pass
    else:
        raise AssertionError("entity service schemas stopped rejecting extra keys")


def test_base_components_match_the_shim_requirements_list() -> None:
    """The generated test requirements cover every component core imports.

    `shim_requirements` copies this list instead of calling the function,
    because calling it imports the modules whose requirements it is trying to
    work out. This is the check that keeps the copy honest.
    """
    from homeassistant.helpers.service import _base_components

    from .shim_requirements import BASE_COMPONENTS

    assert set(_base_components()) == set(BASE_COMPONENTS)
