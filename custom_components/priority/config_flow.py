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
from homeassistant.helpers import selector

from .const import (
    ARBITRATED_SERVICES,
    CONF_DEFAULT_AUTOMATION_PRIORITY,
    CONF_DEFAULT_USER_PRIORITY,
    CONF_EXCLUDED_ENTITIES,
    CONF_MANAGED_AREAS,
    CONF_MANAGED_ENTITIES,
    CONF_MANAGED_LABELS,
    CONF_SCOPE,
    CONF_TRACK_OUT_OF_BAND,
    DEFAULT_AUTOMATION_PRIORITY,
    DEFAULT_SCOPE,
    DEFAULT_TRACK_OUT_OF_BAND,
    DEFAULT_USER_PRIORITY,
    DOMAIN,
    MAX_PRIORITY,
    MIN_PRIORITY,
    PRIORITY_NAMES,
    SCOPE_ALL,
    SCOPE_SELECTED,
)

_PRIORITY_OPTIONS = [
    selector.SelectOptionDict(
        value=str(priority), label=f"{priority} - {PRIORITY_NAMES[priority]}"
    )
    for priority in range(MIN_PRIORITY, MAX_PRIORITY + 1)
]

_PRIORITY_SELECTOR = selector.SelectSelector(
    selector.SelectSelectorConfig(
        options=_PRIORITY_OPTIONS, mode=selector.SelectSelectorMode.DROPDOWN
    )
)

_ENTITY_SELECTOR = selector.EntitySelector(
    selector.EntitySelectorConfig(domain=sorted(ARBITRATED_SERVICES), multiple=True)
)


def _options_schema(current: dict[str, Any]) -> vol.Schema:
    """Build the options schema, seeded with the current values."""
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
            ): _PRIORITY_SELECTOR,
            vol.Required(
                CONF_DEFAULT_AUTOMATION_PRIORITY,
                default=str(
                    current.get(
                        CONF_DEFAULT_AUTOMATION_PRIORITY, DEFAULT_AUTOMATION_PRIORITY
                    )
                ),
            ): _PRIORITY_SELECTOR,
            vol.Required(
                CONF_TRACK_OUT_OF_BAND,
                default=current.get(
                    CONF_TRACK_OUT_OF_BAND, DEFAULT_TRACK_OUT_OF_BAND
                ),
            ): selector.BooleanSelector(),
        }
    )


def _coerce(user_input: dict[str, Any]) -> dict[str, Any]:
    """Turn the selector's string priorities back into integers."""
    options = dict(user_input)
    for key in (CONF_DEFAULT_USER_PRIORITY, CONF_DEFAULT_AUTOMATION_PRIORITY):
        if key in options:
            options[key] = int(options[key])
    return options


class PriorityConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the initial setup."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Create the single config entry.

        Defaults to arbitrating everything: turning the integration on is meant
        to be the decision, not a per-entity opt-in chore.
        """
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()

        if user_input is not None:
            return self.async_create_entry(
                title="Priority Command Arbitration",
                data={},
                options=_coerce(user_input),
            )

        return self.async_show_form(
            step_id="user", data_schema=_options_schema({})
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
        """Edit the options."""
        if user_input is not None:
            return self.async_create_entry(data=_coerce(user_input))

        return self.async_show_form(
            step_id="init",
            data_schema=_options_schema(dict(self.config_entry.options)),
        )
