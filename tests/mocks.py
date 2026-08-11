"""Minimal commandable entities across four domains.

The `demo` integration is config-entry-only and pulls in camera, stt and
conversation, which drags C extensions into a test run that does not need them.
These mocks are the smallest thing that exercises the real domain service
schemas: multi-attribute (light), plain toggle (switch), non-toggle setpoint
(climate) and positional (cover).
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACMode,
)
from homeassistant.components.cover import CoverEntity, CoverEntityFeature
from homeassistant.components.fan import FanEntity, FanEntityFeature
from homeassistant.components.light import ATTR_BRIGHTNESS, ColorMode, LightEntity
from homeassistant.components.switch import SwitchEntity
from homeassistant.const import UnitOfTemperature


class MockLight(LightEntity):
    """A light that remembers everything it was told."""

    _attr_should_poll = False
    _attr_supported_color_modes = {ColorMode.HS}
    _attr_color_mode = ColorMode.HS

    def __init__(self, name: str) -> None:
        self._attr_name = name
        self._attr_unique_id = f"mock_light_{name}"
        self._attr_is_on = False
        self._attr_brightness = None
        self._attr_hs_color = None
        self.turn_on_calls: list[dict[str, Any]] = []
        self.turn_off_calls: list[dict[str, Any]] = []

    async def async_turn_on(self, **kwargs: Any) -> None:
        self.turn_on_calls.append(dict(kwargs))
        self._attr_is_on = True
        if ATTR_BRIGHTNESS in kwargs:
            self._attr_brightness = kwargs[ATTR_BRIGHTNESS]
        if "hs_color" in kwargs:
            self._attr_hs_color = kwargs["hs_color"]
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        self.turn_off_calls.append(dict(kwargs))
        self._attr_is_on = False
        self.async_write_ha_state()


class MockSwitch(SwitchEntity):
    """A plain on/off switch."""

    _attr_should_poll = False

    def __init__(self, name: str) -> None:
        self._attr_name = name
        self._attr_unique_id = f"mock_switch_{name}"
        self._attr_is_on = False

    async def async_turn_on(self, **kwargs: Any) -> None:
        self._attr_is_on = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        self._attr_is_on = False
        self.async_write_ha_state()


class MockClimate(ClimateEntity):
    """A thermostat, to prove non-toggle setpoint services arbitrate."""

    _attr_should_poll = False
    _attr_temperature_unit = UnitOfTemperature.FAHRENHEIT
    _attr_hvac_modes = [HVACMode.OFF, HVACMode.COOL, HVACMode.HEAT]
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.TURN_ON
        | ClimateEntityFeature.TURN_OFF
    )

    def __init__(self, name: str) -> None:
        self._attr_name = name
        self._attr_unique_id = f"mock_climate_{name}"
        self._attr_hvac_mode = HVACMode.OFF
        self._attr_target_temperature = 70
        self._attr_current_temperature = 72

    async def async_set_temperature(self, **kwargs: Any) -> None:
        if (temp := kwargs.get("temperature")) is not None:
            self._attr_target_temperature = temp
        self.async_write_ha_state()

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        self._attr_hvac_mode = hvac_mode
        self.async_write_ha_state()

    async def async_turn_on(self) -> None:
        self._attr_hvac_mode = HVACMode.COOL
        self.async_write_ha_state()

    async def async_turn_off(self) -> None:
        self._attr_hvac_mode = HVACMode.OFF
        self.async_write_ha_state()


class MockCover(CoverEntity):
    """A positional cover."""

    _attr_should_poll = False
    _attr_supported_features = (
        CoverEntityFeature.OPEN
        | CoverEntityFeature.CLOSE
        | CoverEntityFeature.STOP
        | CoverEntityFeature.SET_POSITION
    )

    def __init__(self, name: str) -> None:
        self._attr_name = name
        self._attr_unique_id = f"mock_cover_{name}"
        self._attr_current_cover_position = 0

    @property
    def is_closed(self) -> bool:
        return self._attr_current_cover_position == 0

    async def async_open_cover(self, **kwargs: Any) -> None:
        self._attr_current_cover_position = 100
        self.async_write_ha_state()

    async def async_close_cover(self, **kwargs: Any) -> None:
        self._attr_current_cover_position = 0
        self.async_write_ha_state()

    async def async_set_cover_position(self, **kwargs: Any) -> None:
        self._attr_current_cover_position = kwargs["position"]
        self.async_write_ha_state()

    async def async_stop_cover(self, **kwargs: Any) -> None:
        self.async_write_ha_state()


class MockFan(FanEntity):
    """A fan. Its `async_set_percentage(self, percentage)` has a strict
    signature, unlike cover/climate which take **kwargs - so it is the domain
    that actually catches a stray key leaking through to the handler."""

    _attr_should_poll = False
    _attr_supported_features = FanEntityFeature.SET_SPEED

    def __init__(self, name: str) -> None:
        self._attr_name = name
        self._attr_unique_id = f"mock_fan_{name}"
        self._attr_percentage = 0

    async def async_set_percentage(self, percentage: int) -> None:
        self._attr_percentage = percentage
        self.async_write_ha_state()


ENTITIES: dict[str, list] = {}


def build(domain: str) -> list:
    """Create (once per test) the entities for a domain."""
    factories = {
        "light": lambda: [MockLight("one"), MockLight("two")],
        "switch": lambda: [MockSwitch("one")],
        "climate": lambda: [MockClimate("one")],
        "cover": lambda: [MockCover("one")],
        "fan": lambda: [MockFan("one")],
    }
    ENTITIES[domain] = factories[domain]()
    return ENTITIES[domain]
