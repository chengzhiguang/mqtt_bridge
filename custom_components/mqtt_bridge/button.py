"""Button entities for MQTT Bridge quick actions."""

from __future__ import annotations

from typing import Any

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    CONF_GATEWAY_DEVICE_ID,
    CONF_MQTT_TOKEN_API,
    DOMAIN,
    SERVICE_INITIALIZE_RUNTIME,
    SERVICE_REINITIALIZE_RUNTIME,
    SERVICE_REFRESH_CLOUD_INFO,
)


def _entry_value(entry: ConfigEntry, key: str, default: str = "") -> str:
    """Read a config value from options first, then entry data."""
    value = entry.options.get(key, entry.data.get(key, default))
    return str(value or "").strip()


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up MQTT Bridge button entities."""
    async_add_entities(
        [
            MqttBridgeActionButton(
                hass,
                entry,
                key="initialize_runtime",
                name="初始化运行时",
                icon="mdi:play-box-edit-outline",
                service=SERVICE_INITIALIZE_RUNTIME,
                service_data={"start_immediately": True},
            ),
            MqttBridgeActionButton(
                hass,
                entry,
                key="reinitialize_runtime",
                name="完整重初始化",
                icon="mdi:restart-alert",
                service=SERVICE_REINITIALIZE_RUNTIME,
                service_data={"reason": "button_press", "reload_entry": True},
            ),
            MqttBridgeActionButton(
                hass,
                entry,
                key="refresh_cloud_info",
                name="刷新云 MQTT 信息",
                icon="mdi:cloud-refresh",
                service=SERVICE_REFRESH_CLOUD_INFO,
                service_data={},
            ),
        ]
    )


class MqttBridgeActionButton(ButtonEntity):
    """A simple button that proxies to an integration service."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        *,
        key: str,
        name: str,
        icon: str,
        service: str,
        service_data: dict[str, Any],
    ) -> None:
        self.hass = hass
        self._entry = entry
        self._service = service
        self._service_data = dict(service_data)
        gateway_device_id = _entry_value(entry, CONF_GATEWAY_DEVICE_ID)
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_name = name
        self._attr_icon = icon
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="HuaweiQi",
            model="MQTT Bridge",
            serial_number=gateway_device_id or None,
        )

    async def async_press(self) -> None:
        """Invoke the mapped service."""
        data = dict(self._service_data)
        if self._service == SERVICE_REFRESH_CLOUD_INFO:
            data.setdefault(
                "token_api_url",
                _entry_value(self._entry, CONF_MQTT_TOKEN_API),
            )
            gateway_device_id = _entry_value(self._entry, CONF_GATEWAY_DEVICE_ID)
            if gateway_device_id:
                data.setdefault("gateway_device_id", gateway_device_id)
        await self.hass.services.async_call(
            DOMAIN,
            self._service,
            service_data=data,
            blocking=True,
            return_response=True,
        )
