"""Config and options flow for Sensible."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_NAME
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import CONF_TYPE, DEFAULT_NAME, DOMAIN
from .modules import MODULES, module_options


class SensibleConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Two steps: pick a module type, then configure it."""

    VERSION = 1

    def __init__(self) -> None:
        self._type: str | None = None
        self._name: str = DEFAULT_NAME

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        if user_input is not None:
            self._name = user_input[CONF_NAME]
            self._type = user_input[CONF_TYPE]
            return await self.async_step_configure()

        schema = vol.Schema(
            {
                vol.Required(CONF_NAME, default=DEFAULT_NAME): str,
                vol.Required(CONF_TYPE): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=module_options(),
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                ),
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema)

    async def async_step_configure(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        module = MODULES[self._type]
        fields = module.build_schema(self.hass, {})
        # Modules with no settings (e.g. moon phase) skip the configure form.
        if user_input is not None or not fields:
            return self.async_create_entry(
                title=self._name,
                data={
                    CONF_NAME: self._name,
                    CONF_TYPE: self._type,
                    **(user_input or {}),
                },
            )
        return self.async_show_form(
            step_id="configure",
            data_schema=vol.Schema(fields),
            description_placeholders={"module": module.name},
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        return SensibleOptionsFlow(config_entry)


class SensibleOptionsFlow(config_entries.OptionsFlow):
    """Edit the chosen module's settings (type stays fixed)."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self._entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        module = MODULES[self._entry.data[CONF_TYPE]]
        defaults = {**self._entry.data, **self._entry.options}
        schema = vol.Schema(module.build_schema(self.hass, defaults))
        return self.async_show_form(
            step_id="init",
            data_schema=schema,
            description_placeholders={"module": module.name},
        )
