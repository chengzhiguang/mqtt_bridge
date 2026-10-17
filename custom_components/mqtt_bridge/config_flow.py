"""Config flow for MQTT Bridge."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant import config_entries

from .const import CONF_GATEWAY_DEVICE_ID, CONF_NAME, DEFAULT_NAME, DOMAIN


class MqttBridgeConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for MQTT Bridge."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Handle the initial step."""
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")

        if user_input is not None:
            name = user_input.get(CONF_NAME) or DEFAULT_NAME
            return self.async_create_entry(title=name, data=user_input)

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Optional(CONF_NAME, default=DEFAULT_NAME): str,
                    vol.Optional(CONF_GATEWAY_DEVICE_ID, default=""): str,
                }
            ),
        )
