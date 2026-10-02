"""Config flow for priority command arbitration."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.data_entry_flow import section
from homeassistant.helpers import selector

from .const import (
    ARBITRATED_SERVICES,
    CONF_DEFAULT_AUTOMATION_PRIORITY,
    CONF_DEFAULT_USER_PRIORITY,
    CONF_EXCLUDED_ENTITIES,
    CONF_MANAGED_AREAS,
    CONF_MANAGED_ENTITIES,
    CONF_MANAGED_LABELS,
    CONF_PRIORITY_NAMES,
    CONF_SCOPE,
    CONF_TRACK_OUT_OF_BAND,
    CONFIG_MINOR_VERSION,
    DEFAULT_AUTOMATION_PRIORITY,
    DEFAULT_SCOPE,
    DEFAULT_TRACK_OUT_OF_BAND,
    DEFAULT_USER_PRIORITY,
    DOMAIN,
    MAX_PRIORITY,
    MIN_PRIORITY,
    PRI_DEFAULT,
    PRIORITY_NAMES,
    SCOPE_ALL,
    SCOPE_SELECTED,
    priority_names,
)


def _level_key(priority: int) -> str:
    return f"level_{priority}"


def _priority_selector(names: dict[int, str]) -> selector.SelectSelector:
    return selector.SelectSelector(
        selector.SelectSelectorConfig(
            options=[
                selector.SelectOptionDict(
                    value=str(priority), label=f"{priority} - {names[priority]}"
                )
                for priority in range(MIN_PRIORITY, MAX_PRIORITY + 1)
            ],
            mode=selector.SelectSelectorMode.DROPDOWN,
        )
    )


_ENTITY_SELECTOR = selector.EntitySelector(
    selector.EntitySelectorConfig(domain=sorted(ARBITRATED_SERVICES), multiple=True)
)


def _options_schema(current: dict[str, Any]) -> vol.Schema:
    names = priority_names(current)
    priority_selector = _priority_selector(names)
    return vol.Schema(
        {
            vol.Required(
                CONF_SCOPE, default=current.get(CONF_SCOPE, DEFAULT_SCOPE)
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[
                        selector.SelectOptionDict(
                            value=SCOPE_ALL, label="All supported entities"
                        ),
                        selector.SelectOptionDict(
                            value=SCOPE_SELECTED, label="Only entities I choose"
                        ),
                    ],
                    mode=selector.SelectSelectorMode.LIST,
                )
            ),
            vol.Optional(
                CONF_EXCLUDED_ENTITIES,
                default=current.get(CONF_EXCLUDED_ENTITIES, []),
            ): _ENTITY_SELECTOR,
            vol.Optional(
                CONF_MANAGED_ENTITIES,
                default=current.get(CONF_MANAGED_ENTITIES, []),
            ): _ENTITY_SELECTOR,
            vol.Optional(
                CONF_MANAGED_AREAS, default=current.get(CONF_MANAGED_AREAS, [])
            ): selector.AreaSelector(selector.AreaSelectorConfig(multiple=True)),
            vol.Optional(
                CONF_MANAGED_LABELS, default=current.get(CONF_MANAGED_LABELS, [])
            ): selector.LabelSelector(selector.LabelSelectorConfig(multiple=True)),
            vol.Required(
                CONF_DEFAULT_USER_PRIORITY,
                default=str(
                    current.get(CONF_DEFAULT_USER_PRIORITY, DEFAULT_USER_PRIORITY)
                ),
            ): priority_selector,
            vol.Required(
                CONF_DEFAULT_AUTOMATION_PRIORITY,
                default=str(
                    current.get(
                        CONF_DEFAULT_AUTOMATION_PRIORITY, DEFAULT_AUTOMATION_PRIORITY
                    )
                ),
            ): priority_selector,
            vol.Required(
                CONF_TRACK_OUT_OF_BAND,
                default=current.get(
                    CONF_TRACK_OUT_OF_BAND, DEFAULT_TRACK_OUT_OF_BAND
                ),
            ): selector.BooleanSelector(),
            vol.Required(CONF_PRIORITY_NAMES): section(
                vol.Schema(
                    {
                        # Suggested, not default: a cleared field is omitted, and a default
                        # would put the old name straight back instead of the shipped one.
                        vol.Optional(
                            _level_key(priority),
                            description={"suggested_value": names[priority]},
                        ): selector.TextSelector()
                        for priority in range(MIN_PRIORITY, MAX_PRIORITY + 1)
                    }
                ),
                {"collapsed": True},
            ),
        }
    )


def _coerce(user_input: dict[str, Any]) -> dict[str, Any]:
    """Selector strings back to ints; Default and shipped names are left unstored.

    Leaving them out keeps a rollback to 0.1.x safe: it reads its own default instead of a level 8
    it has no slot for.
    """
    options = dict(user_input)
    for key in (CONF_DEFAULT_USER_PRIORITY, CONF_DEFAULT_AUTOMATION_PRIORITY):
        if key in options:
            options[key] = int(options[key])
            if options[key] == PRI_DEFAULT:
                del options[key]

    raw_names = options.pop(CONF_PRIORITY_NAMES, None) or {}
    custom: dict[str, str] = {}
    for priority, shipped in PRIORITY_NAMES.items():
        name = str(raw_names.get(_level_key(priority)) or "").strip()
        if name and name != shipped:
            custom[str(priority)] = name
    if custom:
        options[CONF_PRIORITY_NAMES] = custom
    return options


def _duplicate_names(options: dict[str, Any]) -> bool:
    names = [name.casefold() for name in priority_names(options).values()]
    return len(set(names)) != len(names)


class PriorityConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the initial setup."""

    VERSION = 1
    MINOR_VERSION = CONFIG_MINOR_VERSION

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Scope defaults to everything: installing is the decision, not a per-entity chore."""
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()

        errors: dict[str, str] = {}
        current: dict[str, Any] = {}
        if user_input is not None:
            current = _coerce(user_input)
            if not _duplicate_names(current):
                return self.async_create_entry(
                    title="Priority Command Arbitration", data={}, options=current
                )
            errors["base"] = "duplicate_names"

        return self.async_show_form(
            step_id="user", data_schema=_options_schema(current), errors=errors
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> PriorityOptionsFlow:
        """Return the options flow."""
        return PriorityOptionsFlow()


class PriorityOptionsFlow(OptionsFlow):
    """Handle reconfiguration."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        current = dict(self.config_entry.options)
        if user_input is not None:
            current = _coerce(user_input)
            if not _duplicate_names(current):
                return self.async_create_entry(data=current)
            errors["base"] = "duplicate_names"

        return self.async_show_form(
            step_id="init", data_schema=_options_schema(current), errors=errors
        )
