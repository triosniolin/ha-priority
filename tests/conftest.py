"""Shared fixtures for the priority arbitration tests."""

from __future__ import annotations

import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    MockModule,
    MockPlatform,
    mock_integration,
    mock_platform,
)

from homeassistant.setup import async_setup_component
from homeassistant.util.unit_system import US_CUSTOMARY_SYSTEM

from custom_components.priority.const import (
    CONF_SCOPE,
    CONF_TRACK_OUT_OF_BAND,
    DOMAIN,
    SCOPE_ALL,
)

from . import mocks

TEST_DOMAINS = ("light", "switch", "climate", "cover")


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Let Home Assistant load custom_components in tests."""
    return


@pytest.fixture
def entry_options() -> dict:
    """Options for the config entry under test."""
    return {CONF_SCOPE: SCOPE_ALL, CONF_TRACK_OUT_OF_BAND: True}


@pytest.fixture
async def demo_hass(hass):
    """A hass with commandable mock entities in four domains."""
    # The mock thermostat reports in Fahrenheit; match the instance to it so
    # setpoints in tests are not silently converted from Celsius.
    hass.config.units = US_CUSTOMARY_SYSTEM

    assert await async_setup_component(hass, "homeassistant", {})

    mock_integration(hass, MockModule("mockdev"), built_in=False)

    for domain in TEST_DOMAINS:

        def _make_setup(dom: str):
            async def _async_setup_platform(
                hass, config, async_add_entities, discovery_info=None
            ):
                async_add_entities(mocks.build(dom))

            return _async_setup_platform

        mock_platform(
            hass,
            f"mockdev.{domain}",
            MockPlatform(async_setup_platform=_make_setup(domain)),
        )

    for domain in TEST_DOMAINS:
        assert await async_setup_component(
            hass, domain, {domain: {"platform": "mockdev"}}
        )
    await hass.async_block_till_done()
    return hass


@pytest.fixture
def light_one(demo_hass) -> str:
    """Entity id of the first mock light."""
    return "light.one"


@pytest.fixture
async def priority_entry(demo_hass, entry_options) -> MockConfigEntry:
    """A loaded priority config entry."""
    entry = MockConfigEntry(domain=DOMAIN, options=entry_options, unique_id=DOMAIN)
    entry.add_to_hass(demo_hass)
    assert await demo_hass.config_entries.async_setup(entry.entry_id)
    await demo_hass.async_block_till_done()
    return entry
