"""Sensor entities for MQTT Bridge runtime visibility."""

from __future__ import annotations

from datetime import timedelta
import logging
from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity, DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .const import CONF_GATEWAY_DEVICE_ID, DOMAIN
from .runtime import RUNTIME_LOG_PATH, RUNTIME_STATUS_PATH, read_runtime_logs, read_runtime_status

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up MQTT Bridge runtime sensors."""
    coordinator = MqttBridgeRuntimeCoordinator(hass, entry.entry_id)
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
            MqttBridgeConnectedDevicesSensor(entry, coordinator),
            MqttBridgeLastDeviceActivitySensor(entry, coordinator),
        ]
    )


class MqttBridgeRuntimeCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Poll bridge runtime status and recent logs."""

    def __init__(self, hass: HomeAssistant, bridge_entry_id: str) -> None:
        super().__init__(
            hass,
            logger=_LOGGER,
            name="mqtt_bridge_runtime",
            update_interval=timedelta(seconds=5),
        )
        self._bridge_entry_id = bridge_entry_id

    async def _async_update_data(self) -> dict[str, Any]:
        status = await self.hass.async_add_executor_job(read_runtime_status)
        logs = await self.hass.async_add_executor_job(read_runtime_logs, 20)
        devices = _build_device_overview(self.hass, self._bridge_entry_id)
        return {"status": status, "logs": logs, "devices": devices}


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
        gateway_device_id = str(
            entry.options.get(
                CONF_GATEWAY_DEVICE_ID,
                entry.data.get(CONF_GATEWAY_DEVICE_ID, ""),
            )
        ).strip()
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


class MqttBridgeConnectedDevicesSensor(MqttBridgeBaseEntity):
    """Expose a summary of bridged Home Assistant devices."""

    def __init__(self, entry: ConfigEntry, coordinator: MqttBridgeRuntimeCoordinator) -> None:
        super().__init__(
            entry,
            coordinator,
            key="connected_devices",
            name="设备在线概览",
            icon="mdi:devices",
        )

    @property
    def native_value(self) -> str:
        overview = self.coordinator.data.get("devices", {})
        total = int(overview.get("total_devices", 0) or 0)
        online = int(overview.get("online_devices", 0) or 0)
        return f"{online}/{total} 在线" if total else "暂无设备"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        overview = self.coordinator.data.get("devices", {})
        return {
            "total_devices": overview.get("total_devices", 0),
            "online_devices": overview.get("online_devices", 0),
            "offline_devices": overview.get("offline_devices", 0),
            "last_device_activity_at": overview.get("latest_activity_at"),
            "last_device_activity_name": overview.get("latest_activity_name", ""),
            "devices": overview.get("devices", []),
        }


class MqttBridgeLastDeviceActivitySensor(MqttBridgeBaseEntity):
    """Expose the latest observed HA device activity summary."""

    def __init__(self, entry: ConfigEntry, coordinator: MqttBridgeRuntimeCoordinator) -> None:
        super().__init__(
            entry,
            coordinator,
            key="last_device_activity",
            name="最后设备活动",
            icon="mdi:timeline-clock-outline",
        )

    @property
    def native_value(self) -> str:
        overview = self.coordinator.data.get("devices", {})
        latest_name = str(overview.get("latest_activity_name") or "").strip()
        latest_at = str(overview.get("latest_activity_at") or "").strip()
        if not latest_name and not latest_at:
            return "暂无"
        if latest_name and latest_at:
            return f"{latest_name} {latest_at}"
        return latest_name or latest_at

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        overview = self.coordinator.data.get("devices", {})
        return {
            "device_id": overview.get("latest_activity_device_id", ""),
            "device_name": overview.get("latest_activity_name", ""),
            "entity_id": overview.get("latest_activity_entity_id", ""),
            "last_state_update_at": overview.get("latest_activity_at"),
            "devices": overview.get("devices", []),
        }


def _build_device_overview(hass: HomeAssistant, bridge_entry_id: str) -> dict[str, Any]:
    """Build a device-level online/activity snapshot for the integration page."""
    entity_registry = er.async_get(hass)
    device_registry = dr.async_get(hass)
    devices: dict[str, dict[str, Any]] = {}
    latest_activity_ts = None
    latest_activity: dict[str, Any] | None = None

    for entity_entry in entity_registry.entities.values():
        if getattr(entity_entry, "config_entry_id", None) == bridge_entry_id:
            continue
        if entity_entry.disabled_by is not None:
            continue
        if not entity_entry.device_id:
            continue

        state = hass.states.get(entity_entry.entity_id)
        if state is None:
            continue

        device_entry = device_registry.async_get(entity_entry.device_id)
        device_name = ""
        manufacturer = ""
        model = ""
        area_id = ""
        if device_entry is not None:
            device_name = (
                device_entry.name_by_user
                or device_entry.name
                or device_entry.serial_number
                or entity_entry.device_id
            )
            manufacturer = device_entry.manufacturer or ""
            model = device_entry.model or ""
            area_id = device_entry.area_id or ""
        else:
            device_name = entity_entry.original_name or entity_entry.entity_id

        item = devices.setdefault(
            entity_entry.device_id,
            {
                "device_id": entity_entry.device_id,
                "name": device_name,
                "manufacturer": manufacturer,
                "model": model,
                "area_id": area_id,
                "entity_count": 0,
                "online_entity_count": 0,
                "online": False,
                "last_state_update_at": "",
                "last_state_entity_id": "",
                "entities": [],
                "_last_state_dt": None,
            },
        )
        item["entity_count"] += 1
        item["entities"].append(entity_entry.entity_id)

        is_online = state.state not in (STATE_UNAVAILABLE, STATE_UNKNOWN)
        if is_online:
            item["online_entity_count"] += 1
            item["online"] = True

        last_updated = getattr(state, "last_updated", None) or getattr(state, "last_changed", None)
        if last_updated is not None:
            local_time = dt_util.as_local(last_updated)
            local_value = local_time.strftime("%Y-%m-%d %H:%M:%S")
            if item["_last_state_dt"] is None or local_time > item["_last_state_dt"]:
                item["_last_state_dt"] = local_time
                item["last_state_update_at"] = local_value
                item["last_state_entity_id"] = entity_entry.entity_id

            if latest_activity_ts is None or local_time > latest_activity_ts:
                latest_activity_ts = local_time
                latest_activity = {
                    "latest_activity_at": local_value,
                    "latest_activity_name": item["name"],
                    "latest_activity_device_id": item["device_id"],
                    "latest_activity_entity_id": entity_entry.entity_id,
                }

    device_list = sorted(
        devices.values(),
        key=lambda item: (
            not bool(item.get("online")),
            str(item.get("name") or item.get("device_id") or ""),
        ),
    )
    for item in device_list:
        item.pop("_last_state_dt", None)
    total = len(device_list)
    online = sum(1 for item in device_list if item.get("online"))
    offline = total - online
    latest_activity = latest_activity or {
        "latest_activity_at": "",
        "latest_activity_name": "",
        "latest_activity_device_id": "",
        "latest_activity_entity_id": "",
    }
    return {
        "total_devices": total,
        "online_devices": online,
        "offline_devices": offline,
        "devices": device_list,
        **latest_activity,
    }
