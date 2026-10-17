"""Sensor entities for MQTT Bridge runtime visibility."""

from __future__ import annotations

from datetime import timedelta
import logging
from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity, DataUpdateCoordinator

from .const import CONF_GATEWAY_DEVICE_ID, DOMAIN
from .runtime import RUNTIME_LOG_PATH, RUNTIME_STATUS_PATH, read_runtime_logs, read_runtime_status

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up MQTT Bridge runtime sensors."""
    coordinator = MqttBridgeRuntimeCoordinator(hass)
    await coordinator.async_config_entry_first_refresh()
    async_add_entities(
        [
            MqttBridgeRuntimeSensor(
                entry,
                coordinator,
                key="runtime_status",
                name="运行状态",
                icon="mdi:lan-connect",
            ),
            MqttBridgeLastErrorSensor(entry, coordinator),
            MqttBridgeRecentLogsSensor(entry, coordinator),
            MqttBridgePublicMqttInfoSensor(entry, coordinator),
            MqttBridgeLocalMqttInfoSensor(entry, coordinator),
        ]
    )


class MqttBridgeRuntimeCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Poll bridge runtime status and recent logs."""

    def __init__(self, hass: HomeAssistant) -> None:
        super().__init__(
            hass,
            logger=_LOGGER,
            name="mqtt_bridge_runtime",
            update_interval=timedelta(seconds=5),
        )

    async def _async_update_data(self) -> dict[str, Any]:
        status = await self.hass.async_add_executor_job(read_runtime_status)
        logs = await self.hass.async_add_executor_job(read_runtime_logs, 20)
        return {"status": status, "logs": logs}


class MqttBridgeBaseEntity(CoordinatorEntity[MqttBridgeRuntimeCoordinator], SensorEntity):
    """Base entity shared by runtime sensors."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        entry: ConfigEntry,
        coordinator: MqttBridgeRuntimeCoordinator,
        *,
        key: str,
        name: str,
        icon: str,
    ) -> None:
        super().__init__(coordinator)
        gateway_device_id = str(entry.data.get(CONF_GATEWAY_DEVICE_ID) or "").strip()
        self._entry = entry
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

    @property
    def runtime_status(self) -> dict[str, Any]:
        """Return the latest runtime status payload."""
        status = self.coordinator.data.get("status", {})
        return status if isinstance(status, dict) else {}


class MqttBridgeRuntimeSensor(MqttBridgeBaseEntity):
    """Show the current bridge runtime state."""

    @property
    def native_value(self) -> str:
        status = self.runtime_status
        state = str(status.get("status") or "").strip()
        if state:
            return state
        if not RUNTIME_STATUS_PATH.exists():
            return STATE_UNAVAILABLE
        return "unknown"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        status = dict(self.runtime_status)
        status.setdefault("runtime_status_path", str(RUNTIME_STATUS_PATH))
        status.setdefault("runtime_log_path", str(RUNTIME_LOG_PATH))
        recent_logs = self.coordinator.data.get("logs", [])
        status["recent_logs"] = recent_logs[-10:] if isinstance(recent_logs, list) else []
        return status


class MqttBridgeLastErrorSensor(MqttBridgeBaseEntity):
    """Expose the last bridge runtime error."""

    def __init__(self, entry: ConfigEntry, coordinator: MqttBridgeRuntimeCoordinator) -> None:
        super().__init__(
            entry,
            coordinator,
            key="last_error",
            name="最后错误",
            icon="mdi:alert-circle-outline",
        )

    @property
    def native_value(self) -> str:
        status = self.runtime_status
        message = str(status.get("last_error") or "").strip()
        return message or "无"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        status = self.runtime_status
        return {
            "last_error_at": status.get("last_error_at"),
            "last_exception": status.get("last_exception", ""),
        }


class MqttBridgeRecentLogsSensor(MqttBridgeBaseEntity):
    """Expose a recent runtime log summary on the integration page."""

    def __init__(self, entry: ConfigEntry, coordinator: MqttBridgeRuntimeCoordinator) -> None:
        super().__init__(
            entry,
            coordinator,
            key="recent_logs",
            name="最近日志",
            icon="mdi:file-document-outline",
        )

    @property
    def native_value(self) -> str:
        logs = self.coordinator.data.get("logs", [])
        if not isinstance(logs, list) or not logs:
            return "暂无日志"
        return str(logs[-1])[:255]

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        logs = self.coordinator.data.get("logs", [])
        return {
            "runtime_log_path": str(RUNTIME_LOG_PATH),
            "line_count": len(logs) if isinstance(logs, list) else 0,
            "recent_logs": logs if isinstance(logs, list) else [],
        }


class MqttBridgePublicMqttInfoSensor(MqttBridgeBaseEntity):
    """Expose public MQTT connection info on the device page."""

    def __init__(self, entry: ConfigEntry, coordinator: MqttBridgeRuntimeCoordinator) -> None:
        super().__init__(
            entry,
            coordinator,
            key="public_mqtt_info",
            name="公网 MQTT 信息",
            icon="mdi:cloud-outline",
        )

    @property
    def native_value(self) -> str:
        status = self.runtime_status
        endpoint = str(status.get("public_mqtt_endpoint") or "").strip()
        if endpoint:
            port = status.get("public_mqtt_tcp_port") or ""
            return f"{endpoint}:{port}" if port else endpoint
        return "未获取"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        status = self.runtime_status
        return {
            "connected": status.get("aliyun_connected", False),
            "endpoint": status.get("public_mqtt_endpoint", ""),
            "tcp_port": status.get("public_mqtt_tcp_port", 0),
            "username": status.get("public_mqtt_username", ""),
            "client_id": status.get("public_mqtt_client_id", ""),
            "instance_id": status.get("public_mqtt_instance_id", ""),
            "region": status.get("public_mqtt_region", ""),
            "secure_mode": status.get("public_mqtt_secure_mode", ""),
            "topic_command": status.get("public_mqtt_topic_command", ""),
            "topic_notify": status.get("public_mqtt_topic_notify", ""),
            "topic_status": status.get("public_mqtt_topic_status", ""),
            "token_api_url": status.get("token_api_url", ""),
        }


class MqttBridgeLocalMqttInfoSensor(MqttBridgeBaseEntity):
    """Expose local MQTT configuration on the device page."""

    def __init__(self, entry: ConfigEntry, coordinator: MqttBridgeRuntimeCoordinator) -> None:
        super().__init__(
            entry,
            coordinator,
            key="local_mqtt_info",
            name="本地 MQTT 信息",
            icon="mdi:server-network",
        )

    @property
    def native_value(self) -> str:
        status = self.runtime_status
        host = str(status.get("local_mqtt_host") or "").strip()
        port = status.get("local_mqtt_port") or ""
        if not status.get("local_mqtt_enabled", False):
            return "当前未启用"
        if host:
            return f"{host}:{port}" if port else host
        return "未配置"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        status = self.runtime_status
        return {
            "enabled": status.get("local_mqtt_enabled", False),
            "connected": status.get("local_connected", False),
            "dispatch_mode": status.get("dispatch_mode", ""),
            "host": status.get("local_mqtt_host", ""),
            "port": status.get("local_mqtt_port", 0),
            "username": status.get("local_mqtt_username", ""),
            "topic_command": status.get("local_mqtt_topic_command", ""),
            "topic_notify": status.get("local_mqtt_topic_notify", ""),
            "topic_status": status.get("local_mqtt_topic_status", ""),
        }
