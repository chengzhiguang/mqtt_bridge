"""Config flow for MQTT Bridge."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback

from .const import (
    CONF_BACKEND_BASE_URL,
    CONF_DEVICE_ID,
    CONF_GATEWAY_DEVICE_ID,
    CONF_HA_ACCESS_TOKEN,
    CONF_HA_BASE_URL,
    CONF_MQTT_TOKEN_API,
    CONF_NAME,
    DEFAULT_NAME,
    DOMAIN,
)


def _normalize_base_url(value: str) -> str:
    """Normalize a base URL for backend or Home Assistant."""
    normalized = str(value or "").strip()
    if not normalized:
        return ""
    if "://" not in normalized:
        normalized = f"http://{normalized}"
    return normalized.rstrip("/")


def _normalize_device_id(value: str) -> str:
    """Normalize a gateway/device id into uppercase MAC-like form."""
    return str(value or "").strip().upper().replace(":", "")


def _default_ha_base_url(hass) -> str:
    """Return the best-effort HA base URL for the current instance."""
    for candidate in (hass.config.internal_url, hass.config.external_url):
        normalized = _normalize_base_url(candidate or "")
        if normalized:
            return normalized
    return ""


def _build_entry_payload(user_input: dict[str, Any]) -> dict[str, Any]:
    """Convert simplified form fields into stored config entry data."""
    device_id = _normalize_device_id(user_input.get(CONF_DEVICE_ID, ""))
    backend_base_url = _normalize_base_url(user_input.get(CONF_BACKEND_BASE_URL, ""))
    ha_base_url = _normalize_base_url(user_input.get(CONF_HA_BASE_URL, ""))
    mqtt_token_api = ""
    if backend_base_url:
        mqtt_token_api = f"{backend_base_url}/api/device/mqtt/token"

    payload = {
        CONF_NAME: str(user_input.get(CONF_NAME) or DEFAULT_NAME).strip() or DEFAULT_NAME,
        CONF_DEVICE_ID: device_id,
        CONF_GATEWAY_DEVICE_ID: device_id,
        CONF_BACKEND_BASE_URL: backend_base_url,
        CONF_MQTT_TOKEN_API: mqtt_token_api,
        CONF_HA_BASE_URL: ha_base_url,
        CONF_HA_ACCESS_TOKEN: str(user_input.get(CONF_HA_ACCESS_TOKEN) or "").strip(),
    }
    return payload


def _build_form_schema(defaults: dict[str, Any]) -> vol.Schema:
    """Build the simplified install form schema."""
    return vol.Schema(
        {
            vol.Optional(CONF_NAME, default=defaults.get(CONF_NAME, DEFAULT_NAME)): str,
            vol.Required(CONF_DEVICE_ID, default=defaults.get(CONF_DEVICE_ID, "")): str,
            vol.Required(
                CONF_BACKEND_BASE_URL,
                default=defaults.get(CONF_BACKEND_BASE_URL, ""),
            ): str,
            vol.Required(
                CONF_HA_BASE_URL,
                default=defaults.get(CONF_HA_BASE_URL, ""),
            ): str,
            vol.Optional(
                CONF_HA_ACCESS_TOKEN,
                default=defaults.get(CONF_HA_ACCESS_TOKEN, ""),
            ): str,
        }
    )


class MqttBridgeConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for MQTT Bridge."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        """Return the options flow for MQTT Bridge."""
        return MqttBridgeOptionsFlow(config_entry)

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Handle the initial step."""
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")

        errors: dict[str, str] = {}
        if user_input is not None:
            data = _build_entry_payload(user_input)
            if not data[CONF_DEVICE_ID]:
                errors[CONF_DEVICE_ID] = "required"
            if not data[CONF_BACKEND_BASE_URL]:
                errors[CONF_BACKEND_BASE_URL] = "required"
            if not data[CONF_HA_BASE_URL]:
                errors[CONF_HA_BASE_URL] = "required"
            if not errors:
                return self.async_create_entry(title=data[CONF_NAME], data=data)

        return self.async_show_form(
            step_id="user",
            data_schema=_build_form_schema(
                {
                    CONF_NAME: DEFAULT_NAME,
                    CONF_HA_BASE_URL: _default_ha_base_url(self.hass),
                }
            ),
            errors=errors,
        )


class MqttBridgeOptionsFlow(config_entries.OptionsFlow):
    """Options flow for MQTT Bridge."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Initialize the options flow."""
        self.config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Manage MQTT Bridge options."""
        errors: dict[str, str] = {}
        if user_input is not None:
            data = _build_entry_payload(user_input)
            if not data[CONF_DEVICE_ID]:
                errors[CONF_DEVICE_ID] = "required"
            if not data[CONF_BACKEND_BASE_URL]:
                errors[CONF_BACKEND_BASE_URL] = "required"
            if not data[CONF_HA_BASE_URL]:
                errors[CONF_HA_BASE_URL] = "required"
            if not errors:
                return self.async_create_entry(title="", data=data)

        current = dict(self.config_entry.data)
        current.update(self.config_entry.options)
        defaults = {
            CONF_NAME: current.get(CONF_NAME, DEFAULT_NAME),
            CONF_DEVICE_ID: current.get(CONF_DEVICE_ID)
            or current.get(CONF_GATEWAY_DEVICE_ID, ""),
            CONF_BACKEND_BASE_URL: current.get(CONF_BACKEND_BASE_URL, ""),
            CONF_HA_BASE_URL: current.get(CONF_HA_BASE_URL, "")
            or _default_ha_base_url(self.hass),
            CONF_HA_ACCESS_TOKEN: current.get(CONF_HA_ACCESS_TOKEN, ""),
        }
        return self.async_show_form(
            step_id="init",
            data_schema=_build_form_schema(defaults),
            errors=errors,
        )
