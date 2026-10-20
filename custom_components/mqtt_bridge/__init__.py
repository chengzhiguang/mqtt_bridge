"""MQTT Bridge custom integration."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import (
    area_registry as ar,
    aiohttp_client,
    config_validation as cv,
    device_registry as dr,
    entity_registry as er,
)

from .const import (
    CONF_BACKEND_BASE_URL,
    CONF_GATEWAY_DEVICE_ID,
    CONF_MQTT_TOKEN_API,
    DEFAULT_EXCLUDE_DOMAINS,
    DEFAULT_EXCLUDE_ENTITY_PREFIXES,
    DOMAIN,
    SERVICE_ACTIVATE_SCENE,
    SERVICE_CALL_SERVICE,
    SERVICE_CREATE_SCENE,
    SERVICE_DELETE_SCENE,
    SERVICE_DISABLE_SCENE,
    SERVICE_ENABLE_SCENE,
    SERVICE_GET_DEVICE,
    SERVICE_GET_DEVICES,
    SERVICE_HANDLE_COMMAND,
    SERVICE_INITIALIZE_RUNTIME,
    SERVICE_GET_RUNTIME_LOGS,
    SERVICE_LIST_SCENES,
    SERVICE_REINITIALIZE_RUNTIME,
    SERVICE_REFRESH_CLOUD_INFO,
    SERVICE_UPDATE_SCENE,
    PLATFORMS,
)
from .runtime import (
    RUNTIME_CONTROL_DIR,
    RUNTIME_LOG_PATH,
    ensure_runtime_started,
    initialize_runtime_environment,
)
from .scene import MqttBridgeSceneManager, get_scene_manager

_LOGGER = logging.getLogger(__name__)

type BridgeConfigEntry = ConfigEntry[dict[str, Any]]

GET_DEVICES_SCHEMA = vol.Schema(
    {
        vol.Optional("domain"): cv.string,
        vol.Optional("exclude_domains"): vol.All(cv.ensure_list, [cv.string]),
        vol.Optional("exclude_entity_prefixes"): vol.All(cv.ensure_list, [cv.string]),
        vol.Optional("include_attributes", default=False): cv.boolean,
    }
)

CALL_SERVICE_SCHEMA = vol.Schema(
    {
        vol.Optional("service"): cv.string,
        vol.Optional("domain"): cv.string,
        vol.Optional("service_name"): cv.string,
        vol.Optional("entity_id"): vol.Any(cv.entity_id, [cv.entity_id]),
        vol.Optional("target"): dict,
        vol.Optional("service_data"): dict,
        vol.Optional("params"): dict,
    },
    extra=vol.ALLOW_EXTRA,
)

REFRESH_CLOUD_INFO_SCHEMA = vol.Schema(
    {
        vol.Optional("token_api_url"): cv.string,
        vol.Optional("gateway_device_id"): cv.string,
        vol.Optional("client_type", default="home_assistant"): cv.string,
        vol.Optional("timeout", default=10): vol.All(
            vol.Coerce(int), vol.Range(min=3, max=60)
        ),
        vol.Optional("include_raw", default=False): cv.boolean,
    }
)

HANDLE_COMMAND_SCHEMA = vol.Schema(
    {
        vol.Optional("payload"): dict,
    },
    extra=vol.ALLOW_EXTRA,
)

INITIALIZE_RUNTIME_SCHEMA = vol.Schema(
    {
        vol.Optional("start_immediately", default=True): cv.boolean,
    }
)

GET_RUNTIME_LOGS_SCHEMA = vol.Schema(
    {
        vol.Optional("lines", default=200): vol.All(
            vol.Coerce(int), vol.Range(min=20, max=1000)
        ),
        vol.Optional("contains", default=""): cv.string,
    }
)

REINITIALIZE_RUNTIME_SCHEMA = vol.Schema(
    {
        vol.Optional("reason", default="manual"): cv.string,
        vol.Optional("reload_entry", default=True): cv.boolean,
    }
)

GET_DEVICE_SCHEMA = vol.Schema(
    {
        vol.Optional("entity_id"): cv.entity_id,
        vol.Optional("device_id"): cv.string,
        vol.Optional("include_attributes", default=False): cv.boolean,
    }
)

LIST_SCENES_SCHEMA = vol.Schema({})

ACTIVATE_SCENE_SCHEMA = vol.Schema(
    {
        vol.Optional("entity_id"): cv.entity_id,
        vol.Optional("scene_id"): cv.string,
    }
)

CREATE_SCENE_SCHEMA = vol.Schema(
    {
        vol.Optional("id"): cv.string,
        vol.Optional("scene_id"): cv.string,
        vol.Optional("name"): cv.string,
        vol.Optional("icon"): cv.string,
        vol.Optional("entities"): dict,
        vol.Optional("enabled", default=True): cv.boolean,
    }
)

UPDATE_SCENE_SCHEMA = vol.Schema(
    {
        vol.Optional("entity_id"): cv.entity_id,
        vol.Optional("scene_id"): cv.string,
        vol.Optional("name"): cv.string,
        vol.Optional("icon"): cv.string,
        vol.Optional("entities"): dict,
    }
)

DELETE_SCENE_SCHEMA = vol.Schema(
    {
        vol.Optional("entity_id"): cv.entity_id,
        vol.Optional("scene_id"): cv.string,
    }
)

ENABLE_SCENE_SCHEMA = DELETE_SCENE_SCHEMA
DISABLE_SCENE_SCHEMA = DELETE_SCENE_SCHEMA

COMMAND_SERVICE_MAP = {
    SERVICE_GET_DEVICES: SERVICE_GET_DEVICES,
    SERVICE_GET_DEVICE: SERVICE_GET_DEVICE,
    SERVICE_CALL_SERVICE: SERVICE_CALL_SERVICE,
    SERVICE_LIST_SCENES: SERVICE_LIST_SCENES,
    SERVICE_ACTIVATE_SCENE: SERVICE_ACTIVATE_SCENE,
    SERVICE_CREATE_SCENE: SERVICE_CREATE_SCENE,
    SERVICE_UPDATE_SCENE: SERVICE_UPDATE_SCENE,
    SERVICE_DELETE_SCENE: SERVICE_DELETE_SCENE,
    SERVICE_ENABLE_SCENE: SERVICE_ENABLE_SCENE,
    SERVICE_DISABLE_SCENE: SERVICE_DISABLE_SCENE,
}


async def async_setup(hass: HomeAssistant, _config: dict[str, Any]) -> bool:
    """Set up the integration from YAML."""
    hass.data.setdefault(DOMAIN, {})
    _async_register_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: BridgeConfigEntry) -> bool:
    """Set up MQTT Bridge from a config entry."""
    hass.data.setdefault(DOMAIN, {})
    scene_manager = MqttBridgeSceneManager(hass, entry.entry_id)
    await scene_manager.async_load()
    entry_config = dict(entry.data)
    entry_config.update(entry.options)
    hass.data[DOMAIN][entry.entry_id] = {
        "config": entry_config,
        "scene_manager": scene_manager,
    }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    _async_register_services(hass)
    startup_result = await asyncio.to_thread(ensure_runtime_started, entry_config)
    missing_fields = startup_result.get("missing_fields", [])
    if missing_fields:
        _LOGGER.warning(
            "MQTT Bridge 运行时未自动启动，仍缺少配置: %s",
            ", ".join(str(item) for item in missing_fields),
        )
    else:
        start_result = startup_result.get("start_result", {})
        returncode = (
            start_result.get("returncode")
            if isinstance(start_result, dict)
            else None
        )
        if startup_result.get("process_running"):
            if startup_result.get("started"):
                _LOGGER.info("MQTT Bridge 运行时已在启动阶段自动拉起")
            else:
                _LOGGER.info("MQTT Bridge 运行时已在运行中，跳过重复启动")
        elif returncode not in (None, 0):
            _LOGGER.error(
                "MQTT Bridge 运行时自动启动失败: stdout=%s stderr=%s",
                start_result.get("stdout", ""),
                start_result.get("stderr", ""),
            )

    return True


def _async_register_services(hass: HomeAssistant) -> None:
    """Register MQTT Bridge services once at startup."""

    if not hass.services.has_service(DOMAIN, SERVICE_GET_DEVICES):
        hass.services.async_register(
            DOMAIN,
            SERVICE_GET_DEVICES,
            _async_handle_get_devices,
            schema=GET_DEVICES_SCHEMA,
            supports_response=SupportsResponse.ONLY,
        )

    if not hass.services.has_service(DOMAIN, SERVICE_GET_DEVICE):
        hass.services.async_register(
            DOMAIN,
            SERVICE_GET_DEVICE,
            _async_handle_get_device,
            schema=GET_DEVICE_SCHEMA,
            supports_response=SupportsResponse.ONLY,
        )

    if not hass.services.has_service(DOMAIN, SERVICE_CALL_SERVICE):
        hass.services.async_register(
            DOMAIN,
            SERVICE_CALL_SERVICE,
            _async_handle_call_service,
            schema=CALL_SERVICE_SCHEMA,
            supports_response=SupportsResponse.ONLY,
        )

    if not hass.services.has_service(DOMAIN, SERVICE_REFRESH_CLOUD_INFO):
        hass.services.async_register(
            DOMAIN,
            SERVICE_REFRESH_CLOUD_INFO,
            _async_handle_refresh_cloud_info,
            schema=REFRESH_CLOUD_INFO_SCHEMA,
            supports_response=SupportsResponse.ONLY,
        )

    if not hass.services.has_service(DOMAIN, SERVICE_HANDLE_COMMAND):
        hass.services.async_register(
            DOMAIN,
            SERVICE_HANDLE_COMMAND,
            _async_handle_command,
            schema=HANDLE_COMMAND_SCHEMA,
            supports_response=SupportsResponse.ONLY,
        )

    if not hass.services.has_service(DOMAIN, SERVICE_GET_RUNTIME_LOGS):
        hass.services.async_register(
            DOMAIN,
            SERVICE_GET_RUNTIME_LOGS,
            _async_handle_get_runtime_logs,
            schema=GET_RUNTIME_LOGS_SCHEMA,
            supports_response=SupportsResponse.ONLY,
        )

    if not hass.services.has_service(DOMAIN, SERVICE_INITIALIZE_RUNTIME):
        hass.services.async_register(
            DOMAIN,
            SERVICE_INITIALIZE_RUNTIME,
            _async_handle_initialize_runtime,
            schema=INITIALIZE_RUNTIME_SCHEMA,
            supports_response=SupportsResponse.ONLY,
        )

    if not hass.services.has_service(DOMAIN, SERVICE_REINITIALIZE_RUNTIME):
        hass.services.async_register(
            DOMAIN,
            SERVICE_REINITIALIZE_RUNTIME,
            _async_handle_reinitialize_runtime,
            schema=REINITIALIZE_RUNTIME_SCHEMA,
            supports_response=SupportsResponse.ONLY,
        )

    if not hass.services.has_service(DOMAIN, SERVICE_LIST_SCENES):
        hass.services.async_register(
            DOMAIN,
            SERVICE_LIST_SCENES,
            _async_handle_list_scenes,
            schema=LIST_SCENES_SCHEMA,
            supports_response=SupportsResponse.ONLY,
        )

    if not hass.services.has_service(DOMAIN, SERVICE_ACTIVATE_SCENE):
        hass.services.async_register(
            DOMAIN,
            SERVICE_ACTIVATE_SCENE,
            _async_handle_activate_scene,
            schema=ACTIVATE_SCENE_SCHEMA,
            supports_response=SupportsResponse.ONLY,
        )

    if not hass.services.has_service(DOMAIN, SERVICE_CREATE_SCENE):
        hass.services.async_register(
            DOMAIN,
            SERVICE_CREATE_SCENE,
            _async_handle_create_scene,
            schema=CREATE_SCENE_SCHEMA,
            supports_response=SupportsResponse.ONLY,
        )

    if not hass.services.has_service(DOMAIN, SERVICE_UPDATE_SCENE):
        hass.services.async_register(
            DOMAIN,
            SERVICE_UPDATE_SCENE,
            _async_handle_update_scene,
            schema=UPDATE_SCENE_SCHEMA,
            supports_response=SupportsResponse.ONLY,
        )

    if not hass.services.has_service(DOMAIN, SERVICE_DELETE_SCENE):
        hass.services.async_register(
            DOMAIN,
            SERVICE_DELETE_SCENE,
            _async_handle_delete_scene,
            schema=DELETE_SCENE_SCHEMA,
            supports_response=SupportsResponse.ONLY,
        )

    if not hass.services.has_service(DOMAIN, SERVICE_ENABLE_SCENE):
        hass.services.async_register(
            DOMAIN,
            SERVICE_ENABLE_SCENE,
            _async_handle_enable_scene,
            schema=ENABLE_SCENE_SCHEMA,
            supports_response=SupportsResponse.ONLY,
        )

    if not hass.services.has_service(DOMAIN, SERVICE_DISABLE_SCENE):
        hass.services.async_register(
            DOMAIN,
            SERVICE_DISABLE_SCENE,
            _async_handle_disable_scene,
            schema=DISABLE_SCENE_SCHEMA,
            supports_response=SupportsResponse.ONLY,
        )


async def async_unload_entry(hass: HomeAssistant, entry: BridgeConfigEntry) -> bool:
    """Unload MQTT Bridge."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if not unload_ok:
        return False

    hass.data[DOMAIN].pop(entry.entry_id, None)

    if not hass.data[DOMAIN]:
        hass.services.async_remove(DOMAIN, SERVICE_GET_DEVICES)
        hass.services.async_remove(DOMAIN, SERVICE_GET_DEVICE)
        hass.services.async_remove(DOMAIN, SERVICE_CALL_SERVICE)
        hass.services.async_remove(DOMAIN, SERVICE_REFRESH_CLOUD_INFO)
        hass.services.async_remove(DOMAIN, SERVICE_HANDLE_COMMAND)
        hass.services.async_remove(DOMAIN, SERVICE_GET_RUNTIME_LOGS)
        hass.services.async_remove(DOMAIN, SERVICE_REINITIALIZE_RUNTIME)
        hass.services.async_remove(DOMAIN, SERVICE_LIST_SCENES)
        hass.services.async_remove(DOMAIN, SERVICE_ACTIVATE_SCENE)
        hass.services.async_remove(DOMAIN, SERVICE_CREATE_SCENE)
        hass.services.async_remove(DOMAIN, SERVICE_UPDATE_SCENE)
        hass.services.async_remove(DOMAIN, SERVICE_DELETE_SCENE)
        hass.services.async_remove(DOMAIN, SERVICE_ENABLE_SCENE)
        hass.services.async_remove(DOMAIN, SERVICE_DISABLE_SCENE)

    return True


async def _async_handle_get_devices(call: ServiceCall) -> ServiceResponse:
    """Return Home Assistant entities in a bridge-friendly structure."""
    requested_domain = (call.data.get("domain") or "").strip()
    exclude_domains = {
        item.strip()
        for item in call.data.get("exclude_domains", DEFAULT_EXCLUDE_DOMAINS)
        if item and item.strip()
    }
    exclude_entity_prefixes = tuple(
        item.strip()
        for item in call.data.get(
            "exclude_entity_prefixes", DEFAULT_EXCLUDE_ENTITY_PREFIXES
        )
        if item and item.strip()
    )
    include_attributes = bool(call.data.get("include_attributes", False))

    entity_registry = er.async_get(call.hass)
    device_registry = dr.async_get(call.hass)
    area_registry = ar.async_get(call.hass)

    devices: list[dict[str, Any]] = []
    for state in call.hass.states.async_all():
        domain = state.domain
        if domain in exclude_domains:
            continue
        if requested_domain and domain != requested_domain:
            continue
        if any(state.entity_id.startswith(prefix) for prefix in exclude_entity_prefixes):
            continue

        devices.append(
            _serialize_state_device(
                state,
                entity_registry=entity_registry,
                device_registry=device_registry,
                area_registry=area_registry,
                include_attributes=include_attributes,
            )
        )

    return {
        "status": "ok",
        "gateway_device_id": _get_gateway_device_id(call.hass),
        "domain": requested_domain,
        "count": len(devices),
        "devices": devices,
    }


async def _async_handle_get_device(call: ServiceCall) -> ServiceResponse:
    """Return a single entity or a full HA device and its entities."""
    entity_id = (call.data.get("entity_id") or "").strip()
    device_id = (call.data.get("device_id") or "").strip()
    include_attributes = bool(call.data.get("include_attributes", False))

    if not entity_id and not device_id:
        raise ServiceValidationError("缺少 entity_id 或 device_id")

    entity_registry = er.async_get(call.hass)
    device_registry = dr.async_get(call.hass)
    area_registry = ar.async_get(call.hass)

    if entity_id:
        state = call.hass.states.get(entity_id)
        if state is None:
            raise ServiceValidationError(f"实体不存在: {entity_id}")

        item = _serialize_state_device(
            state,
            entity_registry=entity_registry,
            device_registry=device_registry,
            area_registry=area_registry,
            include_attributes=include_attributes,
        )
        return {
            "status": "ok",
            "gateway_device_id": _get_gateway_device_id(call.hass),
            "entity_id": entity_id,
            "device_id": item["device_id"],
            "count": 1,
            "device": item,
            "devices": [item],
        }

    device_entry = device_registry.async_get(device_id)
    if device_entry is None:
        raise ServiceValidationError(f"设备不存在: {device_id}")

    device_entities: list[dict[str, Any]] = []
    for registry_entry in entity_registry.entities.values():
        if registry_entry.device_id != device_id:
            continue
        state = call.hass.states.get(registry_entry.entity_id)
        if state is None:
            continue
        device_entities.append(
            _serialize_state_device(
                state,
                entity_registry=entity_registry,
                device_registry=device_registry,
                area_registry=area_registry,
                include_attributes=include_attributes,
            )
        )

    device_entities.sort(key=lambda item: item["entity_id"])
    return {
        "status": "ok",
        "gateway_device_id": _get_gateway_device_id(call.hass),
        "device_id": device_id,
        "count": len(device_entities),
        "device": _serialize_registry_device(device_entry, area_registry),
        "devices": device_entities,
    }


async def _async_handle_call_service(call: ServiceCall) -> ServiceResponse:
    """Execute a Home Assistant service call and return a bridge-friendly result."""
    service_ref = (call.data.get("service") or "").strip()
    if service_ref:
        if "." not in service_ref:
            raise ServiceValidationError(
                "service 必须是 domain.service 形式，或者单独传 domain 和 service_name"
            )
        service_domain, service_name = service_ref.split(".", 1)
    else:
        service_domain = (call.data.get("domain") or "").strip()
        service_name = (call.data.get("service_name") or "").strip()

    if not service_domain or not service_name:
        raise ServiceValidationError("缺少 service 或 domain/service_name")

    target = dict(call.data.get("target") or {})
    entity_id = call.data.get("entity_id")
    if entity_id and "entity_id" not in target:
        target["entity_id"] = entity_id

    service_data = dict(call.data.get("service_data") or call.data.get("params") or {})
    _validate_service_targets(call.hass, target=target, service_data=service_data)

    try:
        await call.hass.services.async_call(
            service_domain,
            service_name,
            service_data=service_data,
            target=target or None,
            blocking=True,
            return_response=False,
        )
    except HomeAssistantError as err:
        raise ServiceValidationError(str(err)) from err

    return {
        "status": "ok",
        "gateway_device_id": _get_gateway_device_id(call.hass),
        "service": f"{service_domain}.{service_name}",
        "target": target,
        "service_data": service_data,
    }


def _validate_service_targets(
    hass: HomeAssistant,
    *,
    target: dict[str, Any],
    service_data: dict[str, Any],
) -> None:
    """Validate entity/device targets before calling a HA service."""
    entity_ids = _coerce_target_ids(
        target.get("entity_id") if "entity_id" in target else service_data.get("entity_id")
    )
    if entity_ids:
        missing_entities: list[str] = []
        unavailable_entities: list[str] = []
        for item in entity_ids:
            state = hass.states.get(item)
            if state is None:
                missing_entities.append(item)
                continue
            if state.state in {STATE_UNAVAILABLE, STATE_UNKNOWN}:
                unavailable_entities.append(item)

        if missing_entities:
            raise ServiceValidationError(f"实体不存在: {', '.join(missing_entities)}")
        if unavailable_entities:
            raise ServiceValidationError(f"实体不可用: {', '.join(unavailable_entities)}")

    device_ids = _coerce_target_ids(
        target.get("device_id") if "device_id" in target else service_data.get("device_id")
    )
    if device_ids:
        device_registry = dr.async_get(hass)
        missing_devices = [
            item for item in device_ids if device_registry.async_get(item) is None
        ]
        if missing_devices:
            raise ServiceValidationError(f"设备不存在: {', '.join(missing_devices)}")


def _coerce_target_ids(value: Any) -> list[str]:
    """Normalize a target id field into a clean string list."""
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, list):
        return [
            str(item).strip()
            for item in value
            if isinstance(item, str) and str(item).strip()
        ]
    return []


async def _async_handle_refresh_cloud_info(call: ServiceCall) -> ServiceResponse:
    """Fetch cloud MQTT credential info from the backend API."""
    token_api_url = str(
        call.data.get("token_api_url") or _get_runtime_token_api_url(call.hass)
    ).strip()
    gateway_device_id = str(
        call.data.get("gateway_device_id") or _get_gateway_device_id(call.hass)
    ).strip()
    client_type = str(call.data.get("client_type") or "home_assistant").strip()
    timeout = int(call.data.get("timeout", 10))
    include_raw = bool(call.data.get("include_raw", False))

    if not token_api_url:
        raise ServiceValidationError("缺少 token_api_url")
    if not gateway_device_id:
        raise ServiceValidationError("缺少 gateway_device_id")

    _LOGGER.info(
        "MQTT Bridge 开始刷新云 MQTT 信息: gateway_device_id=%s url=%s client_type=%s",
        gateway_device_id,
        token_api_url,
        client_type,
    )

    session = aiohttp_client.async_get_clientsession(call.hass)
    payload = {"deviceId": gateway_device_id, "clientType": client_type}

    try:
        async with asyncio.timeout(timeout):
            async with session.post(token_api_url, json=payload) as response:
                response.raise_for_status()
                result = await response.json()
    except TimeoutError as err:
        message = f"调用后台接口超时: {token_api_url}"
        _LOGGER.warning("MQTT Bridge 刷新云 MQTT 信息失败: %s", message)
        raise ServiceValidationError(message) from err
    except Exception as err:
        _LOGGER.warning("MQTT Bridge 刷新云 MQTT 信息失败: %s", err)
        raise ServiceValidationError(f"调用后台接口失败: {err}") from err

    if not isinstance(result, dict):
        raise ServiceValidationError("后台接口返回结构不是对象")
    if not result.get("success"):
        raise ServiceValidationError(f"后台接口返回失败: {result}")

    cloud_info = result.get("data")
    if not isinstance(cloud_info, dict):
        raise ServiceValidationError("后台接口缺少 data 对象")

    response_payload = {
        "status": "ok",
        "gateway_device_id": gateway_device_id,
        "token_api_url": token_api_url,
        "client_type": client_type,
        "endpoint": cloud_info.get("endpoint", ""),
        "client_id": cloud_info.get("clientId", ""),
        "username": cloud_info.get("username", ""),
        "tcp_port": cloud_info.get("tcpPort", ""),
        "instance_id": cloud_info.get("instanceId", ""),
        "region": cloud_info.get("region", ""),
        "secure_mode": cloud_info.get("secureMode", ""),
        "message": "云 MQTT 信息刷新成功",
    }
    if include_raw:
        response_payload["raw"] = {
            **cloud_info,
            "password": _mask_secret(str(cloud_info.get("password", ""))),
        }

    _LOGGER.info(
        "MQTT Bridge 云 MQTT 信息刷新成功: endpoint=%s client_id=%s",
        response_payload["endpoint"],
        response_payload["client_id"],
    )
    return response_payload


async def _async_handle_get_runtime_logs(call: ServiceCall) -> ServiceResponse:
    """Return bridge.py runtime logs from the shared runtime directory."""
    lines = int(call.data.get("lines", 200))
    contains = str(call.data.get("contains") or "").strip()

    if not RUNTIME_LOG_PATH.exists():
        raise ServiceValidationError(f"运行日志不存在: {RUNTIME_LOG_PATH}")

    def _read_runtime_logs() -> list[str]:
        text = RUNTIME_LOG_PATH.read_text(encoding="utf-8", errors="replace")
        rows = text.splitlines()
        if contains:
            rows = [row for row in rows if contains in row]
        return rows[-lines:]

    selected_lines = await asyncio.to_thread(_read_runtime_logs)
    return {
        "status": "ok",
        "gateway_device_id": _get_gateway_device_id(call.hass),
        "path": str(RUNTIME_LOG_PATH),
        "lines": selected_lines,
        "count": len(selected_lines),
        "contains": contains,
        "message": "运行日志读取成功",
    }


async def _async_handle_initialize_runtime(call: ServiceCall) -> ServiceResponse:
    """Create runtime files under /config and optionally start bridge.py."""
    config = _get_active_entry_config(call.hass)
    start_immediately = bool(call.data.get("start_immediately", True))
    result = await asyncio.to_thread(
        initialize_runtime_environment,
        config,
        start_immediately=start_immediately,
    )
    missing_fields = result.get("missing_fields", [])
    start_result = result.get("start_result", {})
    returncode = start_result.get("returncode") if isinstance(start_result, dict) else None

    status = "ok"
    message = "运行时文件已初始化"
    if missing_fields:
        status = "needs_input"
        message = "运行时文件已初始化，但仍缺少启动所需配置"
    elif start_immediately and returncode not in (None, 0):
        status = "error"
        message = "运行时文件已初始化，但启动失败"
    elif start_immediately:
        message = "运行时文件已初始化并已尝试启动"

    return {
        "status": status,
        "gateway_device_id": _get_gateway_device_id(call.hass),
        "runtime_dir": result.get("runtime_dir", ""),
        "bridge_path": result.get("bridge_path", ""),
        "env_path": result.get("env_path", ""),
        "restart_script_path": result.get("restart_script_path", ""),
        "missing_fields": missing_fields,
        "start_requested": start_immediately,
        "start_result": start_result,
        "message": message,
    }


async def _async_handle_reinitialize_runtime(call: ServiceCall) -> ServiceResponse:
    reason = str(call.data.get("reason") or "manual").strip() or "manual"
    reload_entry = bool(call.data.get("reload_entry", True))
    command_path = await asyncio.to_thread(_queue_runtime_command, "full_reinitialize", reason)

    scheduled_reload = False
    if reload_entry:
        for entry in call.hass.config_entries.async_entries(DOMAIN):
            scheduled_reload = True
            call.hass.async_create_task(call.hass.config_entries.async_reload(entry.entry_id))

    _LOGGER.info(
        "MQTT Bridge 已触发完整重初始化: reason=%s reload_entry=%s command=%s",
        reason,
        reload_entry,
        command_path,
    )
    return {
        "status": "ok",
        "gateway_device_id": _get_gateway_device_id(call.hass),
        "reason": reason,
        "reload_entry": reload_entry,
        "reload_scheduled": scheduled_reload,
        "command_file": str(command_path),
        "runtime_log_path": str(RUNTIME_LOG_PATH),
        "message": "完整重初始化已触发",
    }


async def _async_handle_command(call: ServiceCall) -> ServiceResponse:
    """Route a legacy MQTT bridge command payload to the matching service."""
    payload = _extract_command_payload(call.data)
    cmd = (payload.get("cmd") or "").strip()
    if cmd == "service_call":
        cmd = SERVICE_CALL_SERVICE
    gateway_device_id = _get_gateway_device_id(call.hass) or str(
        payload.get("gatewayId") or ""
    ).strip()

    try:
        if not cmd:
            raise ServiceValidationError("缺少 cmd")
        if (payload.get("type") or "command").strip() != "command":
            raise ServiceValidationError("type 必须是 command")
        if cmd not in COMMAND_SERVICE_MAP:
            raise ServiceValidationError(f"不支持的 cmd: {cmd}")

        service_data = _normalize_command_service_data(cmd, payload)
        result = await call.hass.services.async_call(
            DOMAIN,
            COMMAND_SERVICE_MAP[cmd],
            service_data=service_data,
            blocking=True,
            return_response=True,
        )
        return _build_command_envelope(
            payload=payload,
            gateway_device_id=gateway_device_id,
            data=result if isinstance(result, dict) else {"status": "ok"},
        )
    except (HomeAssistantError, ServiceValidationError, ValueError) as err:
        return _build_command_envelope(
            payload=payload,
            gateway_device_id=gateway_device_id,
            data={"status": "error", "message": str(err)},
        )


async def _async_handle_list_scenes(call: ServiceCall) -> ServiceResponse:
    """Return current Home Assistant scenes."""
    enabled_scenes = [
        _serialize_scene_state(state)
        for state in sorted(call.hass.states.async_all("scene"), key=lambda item: item.entity_id)
    ]
    managed_scenes = _get_scene_manager_for_call(call.hass).list_managed_scenes()
    disabled_scenes = managed_scenes["disabled"]

    return {
        "status": "ok",
        "gateway_device_id": _get_gateway_device_id(call.hass),
        "count": len(enabled_scenes) + len(disabled_scenes),
        "count_enabled": len(enabled_scenes),
        "count_disabled": len(disabled_scenes),
        "enabled": enabled_scenes,
        "disabled": disabled_scenes,
    }


async def _async_handle_activate_scene(call: ServiceCall) -> ServiceResponse:
    """Activate an existing Home Assistant scene entity."""
    entity_id = _resolve_scene_entity_id(call.data)
    state = call.hass.states.get(entity_id)
    if state is None or state.domain != "scene":
        raise ServiceValidationError("场景不存在或已停用")

    try:
        await call.hass.services.async_call(
            "scene",
            "turn_on",
            target={"entity_id": entity_id},
            blocking=True,
            return_response=False,
        )
    except HomeAssistantError as err:
        raise ServiceValidationError(str(err)) from err

    return {
        "status": "ok",
        "gateway_device_id": _get_gateway_device_id(call.hass),
        "scene": entity_id,
        "name": state.name,
    }


async def _async_handle_create_scene(call: ServiceCall) -> ServiceResponse:
    """Create a managed MQTT Bridge scene."""
    manager = _get_scene_manager_for_call(call.hass)
    try:
        record = await manager.async_create_scene(call.data)
    except ValueError as err:
        raise ServiceValidationError(str(err)) from err

    return {
        "status": "ok",
        "gateway_device_id": _get_gateway_device_id(call.hass),
        "scene_id": record["id"],
        "scene": manager.serialize_scene_summary(record),
        "message": f"场景 {record['id']} 创建成功",
    }


async def _async_handle_update_scene(call: ServiceCall) -> ServiceResponse:
    """Update a managed MQTT Bridge scene."""
    manager = _get_scene_manager_for_call(call.hass)
    scene_id = _resolve_scene_id(call.data)
    update_data = {
        key: value
        for key, value in call.data.items()
        if key in {"name", "icon", "entities"}
    }
    if not update_data:
        raise ServiceValidationError("缺少可更新的字段")

    try:
        record = await manager.async_update_scene(scene_id, update_data)
    except ValueError as err:
        raise ServiceValidationError(str(err)) from err

    return {
        "status": "ok",
        "gateway_device_id": _get_gateway_device_id(call.hass),
        "scene_id": record["id"],
        "scene": manager.serialize_scene_summary(record),
        "message": f"场景 {record['id']} 更新成功",
    }


async def _async_handle_delete_scene(call: ServiceCall) -> ServiceResponse:
    """Delete a managed MQTT Bridge scene."""
    manager = _get_scene_manager_for_call(call.hass)
    scene_id = _resolve_scene_id(call.data)
    try:
        record = await manager.async_delete_scene(scene_id)
    except ValueError as err:
        raise ServiceValidationError(str(err)) from err

    return {
        "status": "ok",
        "gateway_device_id": _get_gateway_device_id(call.hass),
        "scene_id": record["id"],
        "message": f"场景 {record['id']} 已删除",
    }


async def _async_handle_enable_scene(call: ServiceCall) -> ServiceResponse:
    """Enable a managed MQTT Bridge scene."""
    manager = _get_scene_manager_for_call(call.hass)
    scene_id = _resolve_scene_id(call.data)
    try:
        record = await manager.async_enable_scene(scene_id)
    except ValueError as err:
        raise ServiceValidationError(str(err)) from err

    return {
        "status": "ok",
        "gateway_device_id": _get_gateway_device_id(call.hass),
        "scene_id": record["id"],
        "scene": manager.serialize_scene_summary(record),
        "message": f"场景 {record['id']} 已启用",
    }


async def _async_handle_disable_scene(call: ServiceCall) -> ServiceResponse:
    """Disable a managed MQTT Bridge scene."""
    manager = _get_scene_manager_for_call(call.hass)
    scene_id = _resolve_scene_id(call.data)
    try:
        record = await manager.async_disable_scene(scene_id)
    except ValueError as err:
        raise ServiceValidationError(str(err)) from err

    return {
        "status": "ok",
        "gateway_device_id": _get_gateway_device_id(call.hass),
        "scene_id": record["id"],
        "scene": manager.serialize_scene_summary(record),
        "message": f"场景 {record['id']} 已停用",
    }


def _serialize_state_device(
    state: Any,
    *,
    entity_registry: er.EntityRegistry,
    device_registry: dr.DeviceRegistry,
    area_registry: ar.AreaRegistry,
    include_attributes: bool,
) -> dict[str, Any]:
    """Convert a Home Assistant state to the bridge device payload."""
    entity_entry = entity_registry.async_get(state.entity_id)
    device_entry = (
        device_registry.async_get(entity_entry.device_id)
        if entity_entry and entity_entry.device_id
        else None
    )
    area_id = (
        device_entry.area_id
        if device_entry and device_entry.area_id
        else entity_entry.area_id
        if entity_entry and entity_entry.area_id
        else None
    )
    area = area_registry.async_get_area(area_id) if area_id else None

    item: dict[str, Any] = {
        "entity_id": state.entity_id,
        "state": state.state,
        "domain": state.domain,
        "name": state.name,
        "device_id": entity_entry.device_id if entity_entry else "",
        "unique_id": entity_entry.unique_id if entity_entry else "",
        "manufacturer": device_entry.manufacturer if device_entry else "",
        "model": device_entry.model if device_entry else "",
        "serial_number": device_entry.serial_number if device_entry else "",
        "area_id": area_id or "",
        "area": area.name if area else "",
    }
    if include_attributes:
        item["attributes"] = dict(state.attributes)
    return item


def _serialize_registry_device(
    device_entry: dr.DeviceEntry,
    area_registry: ar.AreaRegistry,
) -> dict[str, Any]:
    """Convert a device registry entry to a bridge-friendly payload."""
    area = area_registry.async_get_area(device_entry.area_id) if device_entry.area_id else None
    return {
        "device_id": device_entry.id,
        "name": device_entry.name_by_user or device_entry.name or "",
        "manufacturer": device_entry.manufacturer or "",
        "model": device_entry.model or "",
        "serial_number": device_entry.serial_number or "",
        "area_id": device_entry.area_id or "",
        "area": area.name if area else "",
    }


def _serialize_scene_state(state: Any) -> dict[str, Any]:
    """Convert a scene state to the bridge scene payload."""
    entities = state.attributes.get("entity_id")
    entity_list = list(entities) if isinstance(entities, list | tuple | set) else []
    return {
        "id": state.entity_id.split(".", 1)[1],
        "entity_id": state.entity_id,
        "name": state.name,
        "icon": state.attributes.get("icon"),
        "state": state.state,
        "entities": entity_list,
    }


def _resolve_scene_entity_id(data: dict[str, Any]) -> str:
    """Resolve a scene service payload to an entity_id."""
    entity_id = (data.get("entity_id") or "").strip()
    scene_id = (data.get("scene_id") or "").strip()

    if entity_id:
        return entity_id
    if scene_id:
        return scene_id if scene_id.startswith("scene.") else f"scene.{scene_id}"
    raise ServiceValidationError("缺少 entity_id 或 scene_id")


def _resolve_scene_id(data: dict[str, Any]) -> str:
    """Resolve a scene service payload to the internal scene id."""
    return _resolve_scene_entity_id(data).split(".", 1)[1]


def _extract_command_payload(data: dict[str, Any]) -> dict[str, Any]:
    """Extract a bridge command payload from a service call."""
    if isinstance(data.get("payload"), dict):
        return dict(data["payload"])
    return dict(data)


def _normalize_command_service_data(
    cmd: str, payload: dict[str, Any]
) -> dict[str, Any]:
    """Convert a bridge command payload into service data."""
    raw_data = payload.get("data") or {}
    if not isinstance(raw_data, dict):
        raise ServiceValidationError("data 必须是对象")

    service_data = dict(raw_data)
    legacy_target = str(payload.get("deviceId") or "").strip()

    if cmd in {SERVICE_GET_DEVICE, SERVICE_CALL_SERVICE}:
        if legacy_target and "entity_id" not in service_data and "device_id" not in service_data:
            if "." in legacy_target:
                service_data["entity_id"] = legacy_target
            else:
                service_data["device_id"] = legacy_target

    if cmd in {
        SERVICE_ACTIVATE_SCENE,
        SERVICE_UPDATE_SCENE,
        SERVICE_DELETE_SCENE,
        SERVICE_ENABLE_SCENE,
        SERVICE_DISABLE_SCENE,
    }:
        if legacy_target and "entity_id" not in service_data and "scene_id" not in service_data:
            if legacy_target.startswith("scene."):
                service_data["entity_id"] = legacy_target
            else:
                service_data["scene_id"] = legacy_target

    if cmd == SERVICE_CREATE_SCENE and legacy_target and "id" not in service_data:
        service_data["id"] = legacy_target.split(".", 1)[1] if legacy_target.startswith("scene.") else legacy_target

    return service_data


def _build_command_envelope(
    *,
    payload: dict[str, Any],
    gateway_device_id: str,
    data: dict[str, Any],
) -> dict[str, Any]:
    """Wrap a service result in the legacy MQTT command response format."""
    return {
        "traceId": payload.get("traceId", ""),
        "cmd": payload.get("cmd", ""),
        "type": "notify",
        "gatewayId": gateway_device_id,
        "deviceId": _infer_command_device_id(payload, data, gateway_device_id),
        "time": int(time.time()),
        "data": data,
    }


def _infer_command_device_id(
    payload: dict[str, Any],
    data: dict[str, Any],
    gateway_device_id: str,
) -> str:
    """Infer the response device id for a command envelope."""
    for key in ("entity_id", "device_id", "scene_id", "scene"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()

    legacy_device_id = str(payload.get("deviceId") or "").strip()
    if legacy_device_id:
        return legacy_device_id
    return gateway_device_id


def _mask_secret(value: str) -> str:
    """Mask a secret value for logs and service responses."""
    if not value:
        return ""
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}{'*' * (len(value) - 8)}{value[-4:]}"


def _queue_runtime_command(action: str, reason: str) -> Path:
    """Write a runtime control command for bridge.py to consume."""
    RUNTIME_CONTROL_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "action": action,
        "reason": reason,
        "requested_at": int(time.time()),
        "source": "home_assistant_service",
    }
    command_path = RUNTIME_CONTROL_DIR / f"{int(time.time() * 1000)}_{action}.json"
    command_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return command_path


def _get_scene_manager_for_call(hass: HomeAssistant) -> MqttBridgeSceneManager:
    """Return the active scene manager for service handlers."""
    for entry_id in hass.data.get(DOMAIN, {}):
        return get_scene_manager(hass, entry_id)
    raise ServiceValidationError("MQTT Bridge 尚未初始化")


def _get_active_entry_config(hass: HomeAssistant) -> dict[str, Any]:
    """Return the merged config/options for the active config entry."""
    for entry in hass.config_entries.async_entries(DOMAIN):
        data = dict(entry.data)
        data.update(entry.options)
        return data
    raise ServiceValidationError("MQTT Bridge 尚未初始化")


def _get_gateway_device_id(hass: HomeAssistant) -> str:
    """Return the configured gateway device id, if any."""
    for entry in hass.config_entries.async_entries(DOMAIN):
        gateway_device_id = str(
            entry.options.get(
                CONF_GATEWAY_DEVICE_ID,
                entry.data.get(CONF_GATEWAY_DEVICE_ID, ""),
            )
        ).strip()
        if gateway_device_id:
            return gateway_device_id
    return ""


def _get_runtime_token_api_url(hass: HomeAssistant) -> str:
    """Return the configured token API URL, if any."""
    for entry in hass.config_entries.async_entries(DOMAIN):
        token_api_url = str(
            entry.options.get(
                CONF_MQTT_TOKEN_API,
                entry.data.get(CONF_MQTT_TOKEN_API, ""),
            )
        ).strip()
        if token_api_url:
            return token_api_url
        backend_base_url = str(
            entry.options.get(
                CONF_BACKEND_BASE_URL,
                entry.data.get(CONF_BACKEND_BASE_URL, ""),
            )
        ).strip().rstrip("/")
        if backend_base_url:
            return f"{backend_base_url}/api/device/mqtt/token"
    return ""
