"""Eight levels, user-editable names, and the move of Default from 5 to 8."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import Context
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.service import async_get_all_descriptions
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.priority.const import (
    ATTR_PRIORITY,
    CONF_DEFAULT_AUTOMATION_PRIORITY,
    CONF_DEFAULT_USER_PRIORITY,
    CONF_PRIORITY_NAMES,
    CONF_SCOPE,
    CONFIG_MINOR_VERSION,
    DOMAIN,
    PRI_AUTO,
    PRI_AUTO_EMERGENCY,
    PRI_DEFAULT,
    PRI_OCCUPANCY,
    PRI_PEAK_DEMAND_LIMIT,
    PRI_SCHEDULED,
    PRIORITY_NAMES,
    SCOPE_ALL,
)

LIGHT = "light.one"


async def _setup(hass, options, minor_version=CONFIG_MINOR_VERSION) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN, options=options, unique_id=DOMAIN, minor_version=minor_version
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def _held(hass, entity_id: str) -> int | None:
    resp = await hass.services.async_call(
        DOMAIN, "get", {"entity_id": entity_id}, blocking=True, return_response=True
    )
    return resp["arrays"][entity_id]["effective_priority"]


def _sensor(hass):
    return next(
        s for s in hass.states.async_all("sensor") if "priority_levels" in s.attributes
    )


# ---- Migration ----


async def test_a_stored_default_of_5_is_dropped_not_rewritten(demo_hass) -> None:
    """Dropping it, rather than storing 8, is what keeps a rollback to 0.1.x working."""
    entry = await _setup(
        demo_hass,
        {
            CONF_SCOPE: SCOPE_ALL,
            CONF_DEFAULT_USER_PRIORITY: 5,
            CONF_DEFAULT_AUTOMATION_PRIORITY: PRI_AUTO,
        },
        minor_version=1,
    )

    assert entry.state is ConfigEntryState.LOADED
    assert entry.minor_version == CONFIG_MINOR_VERSION
    assert CONF_DEFAULT_USER_PRIORITY not in entry.options
    assert entry.options[CONF_DEFAULT_AUTOMATION_PRIORITY] == PRI_AUTO

    manager = entry.runtime_data
    assert manager.async_default_priority(Context(user_id="someone")) == PRI_DEFAULT
    assert manager.async_default_priority(Context()) == PRI_AUTO


async def test_a_future_major_version_is_refused(demo_hass) -> None:
    entry = MockConfigEntry(domain=DOMAIN, options={}, unique_id=DOMAIN, version=2)
    entry.add_to_hass(demo_hass)
    await demo_hass.config_entries.async_setup(entry.entry_id)
    await demo_hass.async_block_till_done()
    assert entry.state is ConfigEntryState.MIGRATION_ERROR


# ---- The new levels ----


async def test_new_levels_sit_between_automatic_and_default(demo_hass) -> None:
    await _setup(demo_hass, {CONF_SCOPE: SCOPE_ALL})

    async def call(service: str, priority: int | None = None) -> None:
        data = {"entity_id": LIGHT}
        if priority is not None:
            data[ATTR_PRIORITY] = priority
        await demo_hass.services.async_call("light", service, data, blocking=True)

    await call("turn_on", PRI_SCHEDULED)
    await call("turn_off", PRI_PEAK_DEMAND_LIMIT)
    assert demo_hass.states.get(LIGHT).state == "off"

    await call("turn_on", PRI_SCHEDULED)
    assert demo_hass.states.get(LIGHT).state == "off", "Scheduled outranked Peak Demand Limit"

    await call("turn_on")
    assert demo_hass.states.get(LIGHT).state == "off", "Default outranked Peak Demand Limit"

    await call("turn_on", PRI_OCCUPANCY)
    assert demo_hass.states.get(LIGHT).state == "on"

    await call("turn_off", PRI_AUTO)
    assert demo_hass.states.get(LIGHT).state == "off"
    assert await _held(demo_hass, LIGHT) == PRI_AUTO


# ---- Renaming ----


async def test_renames_reach_every_surface(demo_hass) -> None:
    entry = await _setup(demo_hass, {CONF_SCOPE: SCOPE_ALL})

    demo_hass.config_entries.async_update_entry(
        entry,
        options={
            **entry.options,
            CONF_PRIORITY_NAMES: {str(PRI_AUTO_EMERGENCY): "Burst pipe"},
        },
    )
    await demo_hass.async_block_till_done()

    levels = _sensor(demo_hass).attributes["priority_levels"]
    assert levels[str(PRI_AUTO_EMERGENCY)] == "Burst pipe"
    assert levels[str(PRI_DEFAULT)] == PRIORITY_NAMES[PRI_DEFAULT]

    descriptions = await async_get_all_descriptions(demo_hass)
    options = descriptions["light"]["turn_on"]["fields"][ATTR_PRIORITY]["selector"][
        "select"
    ]["options"]
    assert {"value": "2", "label": "2 - Burst pipe"} in options
    assert len(options) == PRI_DEFAULT, "re-patched from the original, not stacked"

    await demo_hass.services.async_call(
        "light",
        "turn_on",
        {"entity_id": LIGHT, ATTR_PRIORITY: PRI_AUTO_EMERGENCY},
        blocking=True,
    )
    resp = await demo_hass.services.async_call(
        DOMAIN, "get", {"entity_id": LIGHT}, blocking=True, return_response=True
    )
    assert resp["arrays"][LIGHT]["effective_priority_name"] == "Burst pipe"


async def test_options_flow_stores_only_what_differs(demo_hass) -> None:
    entry = await _setup(demo_hass, {CONF_SCOPE: SCOPE_ALL})

    result = await demo_hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM

    names = {f"level_{p}": name for p, name in PRIORITY_NAMES.items()}
    names["level_2"] = "  Burst pipe  "
    names["level_5"] = ""
    result = await demo_hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_SCOPE: SCOPE_ALL,
            CONF_DEFAULT_USER_PRIORITY: str(PRI_DEFAULT),
            CONF_DEFAULT_AUTOMATION_PRIORITY: str(PRI_SCHEDULED),
            "track_out_of_band": True,
            CONF_PRIORITY_NAMES: names,
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_PRIORITY_NAMES] == {"2": "Burst pipe"}
    assert CONF_DEFAULT_USER_PRIORITY not in entry.options
    assert entry.options[CONF_DEFAULT_AUTOMATION_PRIORITY] == PRI_SCHEDULED


async def test_clearing_a_name_restores_the_shipped_one(demo_hass) -> None:
    """The frontend omits a cleared field rather than sending an empty string."""
    entry = await _setup(
        demo_hass,
        {CONF_SCOPE: SCOPE_ALL, CONF_PRIORITY_NAMES: {str(PRI_OCCUPANCY): "Motion"}},
    )

    result = await demo_hass.config_entries.options.async_init(entry.entry_id)
    names = {f"level_{p}": name for p, name in PRIORITY_NAMES.items()}
    del names[f"level_{PRI_OCCUPANCY}"]
    result = await demo_hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_SCOPE: SCOPE_ALL,
            CONF_DEFAULT_USER_PRIORITY: str(PRI_DEFAULT),
            CONF_DEFAULT_AUTOMATION_PRIORITY: str(PRI_DEFAULT),
            "track_out_of_band": True,
            CONF_PRIORITY_NAMES: names,
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert CONF_PRIORITY_NAMES not in entry.options
    assert entry.runtime_data.priority_names[PRI_OCCUPANCY] == "Occupancy"


async def test_the_form_shows_each_level_current_name(demo_hass) -> None:
    entry = await _setup(
        demo_hass,
        {CONF_SCOPE: SCOPE_ALL, CONF_PRIORITY_NAMES: {str(PRI_OCCUPANCY): "Motion"}},
    )
    result = await demo_hass.config_entries.options.async_init(entry.entry_id)
    section = result["data_schema"].schema[CONF_PRIORITY_NAMES].schema.schema
    suggested = {
        str(key): key.description["suggested_value"] for key in section
    }
    assert suggested[f"level_{PRI_OCCUPANCY}"] == "Motion"
    assert suggested[f"level_{PRI_DEFAULT}"] == PRIORITY_NAMES[PRI_DEFAULT]


async def test_options_flow_refuses_two_levels_with_one_name(demo_hass) -> None:
    entry = await _setup(demo_hass, {CONF_SCOPE: SCOPE_ALL})

    result = await demo_hass.config_entries.options.async_init(entry.entry_id)
    names = {f"level_{p}": name for p, name in PRIORITY_NAMES.items()}
    names["level_7"] = "default"
    result = await demo_hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_SCOPE: SCOPE_ALL,
            CONF_DEFAULT_USER_PRIORITY: str(PRI_DEFAULT),
            CONF_DEFAULT_AUTOMATION_PRIORITY: str(PRI_DEFAULT),
            "track_out_of_band": True,
            CONF_PRIORITY_NAMES: names,
        },
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "duplicate_names"}
    assert CONF_PRIORITY_NAMES not in entry.options
