#!/usr/bin/env python3
"""
家庭 MQTT 桥接脚本（替代 Mosquitto 静态桥接）

核心能力：
1. 启动时调用 Java HTTP 接口获取阿里云 MQTT 凭证（一机一密 DeviceCredential）
2. 定时刷新凭证，防止过期
3. 双向桥接阿里云 MQTT ↔ Home Assistant，支持两种下行执行方式：
   - mqtt：服务端 → 本地 Mosquitto → HA
   - ha_service：服务端 → HA service `mqtt_bridge.handle_command`
   Topic 规范：
   - 下行：iot/server/{MAC}/command  服务端 → HA 执行
   - 上行：iot/device/{MAC}/notify   HA → 服务端（回执/通知）
   - 上行：iot/device/{MAC}/status   HA → 服务端（状态上报）
"""

import os
import re
import sys
import json
import time
import uuid
import base64
import signal
import socket
import fcntl
import struct
import logging
import queue
import threading
import subprocess
from hashlib import sha1
from hmac import new as hmac_new
from urllib.request import Request, urlopen
from urllib.parse import urlencode, urlparse

import paho.mqtt.client as mqtt


def get_mac_address(iface=None):
    """自动获取本机 MAC 地址（大写、去冒号）
    iface 指定网卡名（如 eth0/enp0s1），不传则走默认路由出口网卡
    """
    try:
        if iface:
            # 读 sysfs
            with open(f"/sys/class/net/{iface}/address") as f:
                raw = f.read().strip()
        else:
            # 找默认路由出口网卡
            with open("/proc/net/route") as f:
                next(f)  # 跳过表头
                for line in f:
                    parts = line.strip().split()
                    if len(parts) > 2 and parts[1] == "00000000":
                        iface = parts[0]
                        break
            if not iface:
                # 兜底：取第一个非 lo 的网卡
                iface = next(
                    (n for n in os.listdir("/sys/class/net") if n != "lo"),
                    None
                )
            if not iface:
                raise RuntimeError("找不到可用网卡")
            with open(f"/sys/class/net/{iface}/address") as f:
                raw = f.read().strip()
        return raw.upper().replace(":", "")
    except Exception as e:
        log = logging.getLogger("mqtt-bridge")
        log.warning("自动获取 MAC 失败: %s", e)
        return None


# ===================== 配置 =====================

# Java 服务端 HTTP 接口（获取阿里云 MQTT 凭证）
HTTP_API_URL = os.environ.get("MQTT_TOKEN_API", "http://192.168.2.11:8082/api/device/mqtt/token")

# 家庭 MAC：优先取环境变量，否则自动从默认路由出口网卡获取
_HOME_MAC_ENV = os.environ.get("HOME_MAC")
if _HOME_MAC_ENV:
    HOME_MAC = _HOME_MAC_ENV.upper().replace(":", "")
else:
    HOME_MAC = get_mac_address()
if not HOME_MAC:
    sys.exit("无法获取本机 MAC 地址，请显式设置 HOME_MAC 环境变量")

# 本地 Mosquitto（默认 127.0.0.1：bridge 和 Mosquitto 跑同一台机器）
LOCAL_HOST = os.environ.get("LOCAL_MQTT_HOST", "127.0.0.1")
LOCAL_PORT = int(os.environ.get("LOCAL_MQTT_PORT", "1883"))
LOCAL_USER = os.environ.get("LOCAL_MQTT_USER", "ha")
LOCAL_PASS = os.environ.get("LOCAL_MQTT_PASS", "HomeAssistant@2026")

# 下行命令分发模式：
# - mqtt: 保持原行为，转发到本地 Mosquitto
# - ha_service: 直接调用 HA 的 mqtt_bridge.handle_command
COMMAND_DISPATCH_MODE = os.environ.get("COMMAND_DISPATCH_MODE", "mqtt").strip().lower()
HA_BASE_URL = os.environ.get("HA_BASE_URL", "http://127.0.0.1:8123").rstrip("/")
HA_ACCESS_TOKEN = os.environ.get("HA_ACCESS_TOKEN", "").strip()
HA_REFRESH_TOKEN = os.environ.get("HA_REFRESH_TOKEN", "").strip()
HA_CLIENT_ID = os.environ.get("HA_CLIENT_ID", "").strip()
HA_TOKEN_URL = os.environ.get("HA_TOKEN_URL", f"{HA_BASE_URL}/auth/token").strip()
HA_COMMAND_SERVICE = os.environ.get("HA_COMMAND_SERVICE", "mqtt_bridge.handle_command").strip()
HA_API_TIMEOUT = int(os.environ.get("HA_API_TIMEOUT", "10"))

# HA 日志镜像：把 bridge 日志同步进 HA 的 system_log，便于直接在 HA Web UI 查看
HA_LOG_MIRROR = os.environ.get("HA_LOG_MIRROR", "true").strip().lower() in ("1", "true", "yes", "on")
HA_LOG_MIRROR_LEVEL = os.environ.get("HA_LOG_MIRROR_LEVEL", "INFO").strip().upper()
HA_LOG_MIRROR_LOGGER = os.environ.get("HA_LOG_MIRROR_LOGGER", "custom_components.mqtt_bridge.bridge").strip()
HA_LOG_MIRROR_SKIP = ("心跳已发送",)

# 凭证刷新间隔（秒）
TOKEN_REFRESH_INTERVAL = int(os.environ.get("TOKEN_REFRESH_INTERVAL", "3600"))
RUNTIME_DIR = os.environ.get("MQTT_BRIDGE_RUNTIME_DIR", "/config/mqtt_bridge_runtime")
CONTROL_DIR = os.path.join(RUNTIME_DIR, "control")
STATUS_PATH = os.path.join(RUNTIME_DIR, "runtime_status.json")
CONTROL_POLL_INTERVAL = int(os.environ.get("MQTT_BRIDGE_CONTROL_POLL_INTERVAL", "2"))

# Topic 模板
TOPIC_COMMAND = f"iot/server/{HOME_MAC}/command"  # 下行（服务端→设备）
TOPIC_NOTIFY  = f"iot/device/{HOME_MAC}/notify"   # 上行（回执）
TOPIC_STATUS  = f"iot/device/{HOME_MAC}/status"   # 上行（状态）

# ===================== 日志 =====================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("mqtt-bridge")

# ===================== HTTP 获取凭证 =====================

def fetch_aliyun_credential():
    """调用 Java HTTP 接口获取阿里云 MQTT 连接信息"""
    try:
        body = json.dumps({
            "deviceId": HOME_MAC,
            "clientType": "home_assistant"
        }).encode()
        req = Request(
            HTTP_API_URL,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        with urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read().decode())
        if not result.get("success"):
            raise Exception(f"API 返回失败: {result}")
        data = result["data"]
        log.info("获取凭证成功: endpoint=%s, clientId=%s", data["endpoint"], data["clientId"])
        update_runtime_status(
            public_mqtt_endpoint=str(data.get("endpoint") or "").strip(),
            public_mqtt_client_id=str(data.get("clientId") or "").strip(),
            public_mqtt_username=str(data.get("username") or "").strip(),
            public_mqtt_tcp_port=int(data.get("tcpPort") or 0),
            public_mqtt_instance_id=str(data.get("instanceId") or "").strip(),
            public_mqtt_region=str(data.get("region") or "").strip(),
            public_mqtt_secure_mode=str(data.get("secureMode") or "").strip(),
        )
        return data
    except Exception as e:
        log.error("获取阿里云 MQTT 凭证失败: %s", e)
        raise

# ===================== 全局客户端 =====================

aliyun_client = None
local_client = None
aliyun_cred = None
running = True
ha_access_token = HA_ACCESS_TOKEN
ha_access_token_expires_at = 0

# ===================== HA Registry 缓存 =====================
# 通过 docker exec homeassistant cat /config/.storage/core.*_registry 读 HA 内部注册表
# 缓存刷新间隔（秒）
HA_REGISTRY_REFRESH = int(os.environ.get("HA_REGISTRY_REFRESH", "60"))
ha_entity_map = {}   # entity_id -> {device_id, unique_id}
ha_device_map = {}   # device_id -> {manufacturer, model, serial, area}
ha_registry_lock = threading.Lock()
runtime_reinit_lock = threading.Lock()
runtime_status_lock = threading.Lock()
runtime_status = {
    "status": "starting",
    "gatewayId": HOME_MAC,
    "dispatch_mode": COMMAND_DISPATCH_MODE,
    "token_api_url": HTTP_API_URL,
    "ha_command_service": HA_COMMAND_SERVICE,
    "runtime_dir": RUNTIME_DIR,
    "runtime_log_path": os.path.join(RUNTIME_DIR, "bridge.log"),
    "public_mqtt_endpoint": "",
    "public_mqtt_client_id": "",
    "public_mqtt_username": "",
    "public_mqtt_tcp_port": 0,
    "public_mqtt_instance_id": "",
    "public_mqtt_region": "",
    "public_mqtt_secure_mode": "",
    "public_mqtt_topic_command": TOPIC_COMMAND,
    "public_mqtt_topic_notify": TOPIC_NOTIFY,
    "public_mqtt_topic_status": TOPIC_STATUS,
    "local_mqtt_host": LOCAL_HOST,
    "local_mqtt_port": LOCAL_PORT,
    "local_mqtt_username": LOCAL_USER,
    "local_mqtt_enabled": COMMAND_DISPATCH_MODE == "mqtt",
    "local_mqtt_topic_command": TOPIC_COMMAND,
    "local_mqtt_topic_notify": TOPIC_NOTIFY,
    "local_mqtt_topic_status": TOPIC_STATUS,
    "started_at": int(time.time()),
    "updated_at": int(time.time()),
    "aliyun_connected": False,
    "local_connected": False,
    "last_error": "",
    "last_error_at": 0,
    "last_exception": "",
    "last_message_at": 0,
    "last_message_trace_id": "",
    "last_message_cmd": "",
    "last_response_topic": "",
    "last_response_status": "",
    "last_heartbeat_at": 0,
    "last_ha_alive": False,
    "last_registry_load_at": 0,
    "registry_entities": 0,
    "registry_devices": 0,
    "last_reinitialize_at": 0,
    "last_reinitialize_reason": "",
}


def _write_runtime_status_locked():
    """Persist the current runtime status snapshot to disk."""
    os.makedirs(RUNTIME_DIR, exist_ok=True)
    tmp_path = f"{STATUS_PATH}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(runtime_status, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, STATUS_PATH)


def update_runtime_status(**fields):
    """Update runtime status and persist it for HA diagnostic entities."""
    with runtime_status_lock:
        runtime_status.update(fields)
        runtime_status["updated_at"] = int(time.time())
        _write_runtime_status_locked()

def _docker_cat(path):
    """优先直接读取挂载文件，必要时再尝试 docker exec。"""
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        pass
    except Exception as e:
        log.warning("直接读取 HA storage 失败: %s", e)

    try:
        result = subprocess.run(
            ["docker", "exec", "homeassistant", "cat", path],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            return result.stdout
    except Exception as e:
        log.debug("docker exec 失败: %s", e)
    return None

def load_ha_registry():
    """从 HA storage 加载 entity/device registry 到内存缓存"""
    global ha_entity_map, ha_device_map
    try:
        # entity registry
        er_raw = _docker_cat("/config/.storage/core.entity_registry")
        if er_raw:
            er = json.loads(er_raw)
            new_entity_map = {}
            for e in er.get("data", {}).get("entities", []):
                eid = e.get("entity_id", "")
                if eid:
                    new_entity_map[eid] = {
                        "device_id": e.get("device_id", "") or "",
                        "unique_id": e.get("unique_id", "") or "",
                    }
            ha_entity_map = new_entity_map

        # device registry
        dr_raw = _docker_cat("/config/.storage/core.device_registry")
        if dr_raw:
            dr = json.loads(dr_raw)
            new_device_map = {}
            for d in dr.get("data", {}).get("devices", []):
                did = d.get("id", "")
                if did:
                    new_device_map[did] = {
                        "manufacturer": d.get("manufacturer", "") or "",
                        "model": d.get("model", "") or "",
                        "serial_number": d.get("serial_number", "") or "",
                        "area_id": d.get("area_id", "") or "",
                    }
            ha_device_map = new_device_map

        log.info("HA registry 加载完成: entities=%d, devices=%d",
                 len(ha_entity_map), len(ha_device_map))
        update_runtime_status(
            last_registry_load_at=int(time.time()),
            registry_entities=len(ha_entity_map),
            registry_devices=len(ha_device_map),
        )
    except Exception as e:
        log.warning("加载 HA registry 失败: %s", e)
        update_runtime_status(
            last_error="加载 HA registry 失败",
            last_error_at=int(time.time()),
            last_exception=str(e),
        )

def ha_registry_refresh_loop():
    """定时刷新 HA registry"""
    while running:
        time.sleep(HA_REGISTRY_REFRESH)
        with ha_registry_lock:
            load_ha_registry()

def enrich_devices_with_registry(payload_obj):
    """对 get_devices 响应补查 device_id / unique_id / manufacturer / model / area"""
    data = payload_obj.get("data")
    if not data:
        return payload_obj
    devices = data.get("devices", [])
    if not devices:
        return payload_obj

    with ha_registry_lock:
        for dev in devices:
            eid = dev.get("entity_id", "")
            ent_info = ha_entity_map.get(eid)
            if ent_info:
                dev["device_id"] = ent_info["device_id"]
                dev["unique_id"] = ent_info["unique_id"]
                did = ent_info["device_id"]
                if did:
                    dev_info = ha_device_map.get(did)
                    if dev_info:
                        dev["manufacturer"] = dev_info["manufacturer"]
                        dev["model"] = dev_info["model"]
                        dev["serial_number"] = dev_info["serial_number"]
                        dev["area"] = dev_info["area_id"]
                else:
                    # entity 没注册到 device（比如 input_boolean 这类 helper）
                    dev["device_id"] = ""
                    dev["unique_id"] = ""
                    dev["manufacturer"] = ""
                    dev["model"] = ""
                    dev["area"] = ""
            else:
                # 不在 registry 里（可能是 Jinja filter 刚过滤出来的、HA 内部临时的）
                dev.setdefault("device_id", "")
                dev.setdefault("unique_id", "")
                dev.setdefault("manufacturer", "")
                dev.setdefault("model", "")
                dev.setdefault("area", "")

        # 更新 count
        data["count"] = len(devices)

    return payload_obj

# ===================== 阿里云 MQTT 回调 =====================

def on_aliyun_connect(client, userdata, flags, rc):
    if rc == 0:
        log.info("阿里云 MQTT 连接成功")
        # 订阅下行 Topic
        client.subscribe(TOPIC_COMMAND, qos=2)
        log.info("阿里云已订阅: %s", TOPIC_COMMAND)
        update_runtime_status(
            status="connected",
            aliyun_connected=True,
            last_error="",
            last_exception="",
        )
    else:
        log.error("阿里云 MQTT 连接失败 rc=%s", rc)
        update_runtime_status(
            status="error",
            aliyun_connected=False,
            last_error=f"阿里云 MQTT 连接失败 rc={rc}",
            last_error_at=int(time.time()),
            last_exception=f"rc={rc}",
        )

def ensure_device_ids(obj, default_device_id=None):
    """确保 payload 里有 gatewayId + deviceId 两个顶层字段
    - gatewayId 永远强制 = HOME_MAC（bridge 自己知道网关标识，外部传了也覆盖）
    - deviceId：从 data 里取目标设备；网关级指令取不到则 fallback 到 HOME_MAC（两值相等）
    """
    if not isinstance(obj, dict):
        return obj
    obj["gatewayId"] = HOME_MAC   # 强制覆盖，防止外部瞎传
    if not obj.get("deviceId"):
        dev_id = None
        data = obj.get("data")
        if isinstance(data, dict):
            dev_id = (data.get("unique_id")
                      or data.get("entity_id")
                      or data.get("device_id"))
        obj["deviceId"] = dev_id or default_device_id or HOME_MAC
    return obj

def on_aliyun_message(client, userdata, msg):
    """阿里云收到下行指令 → 按配置分发到本地 MQTT 或 HA service"""
    try:
        obj = json.loads(msg.payload.decode())
        obj = ensure_device_ids(obj)
        payload = json.dumps(obj, ensure_ascii=False)
        log.info(
            "收到下行消息: topic=%s traceId=%s cmd=%s deviceId=%s",
            msg.topic,
            obj.get("traceId", ""),
            obj.get("cmd", ""),
            obj.get("deviceId", ""),
        )
        update_runtime_status(
            last_message_at=int(time.time()),
            last_message_trace_id=obj.get("traceId", ""),
            last_message_cmd=obj.get("cmd", ""),
        )
    except Exception:
        log.warning("下行消息不是有效 JSON，忽略: %s", msg.payload[:120])
        update_runtime_status(
            last_error="下行消息不是有效 JSON",
            last_error_at=int(time.time()),
            last_exception=str(msg.payload[:120]),
        )
        return

    if COMMAND_DISPATCH_MODE == "ha_service":
        log.info("→[下行] 阿里云→HA service %s: %s", msg.topic, payload[:120])
        if should_publish_processing_response(obj.get("cmd", "")):
            publish_aliyun_response(obj, build_processing_response(obj))
        response = dispatch_command_via_ha_service(obj)
        publish_aliyun_response(obj, response)
        return

    try:
        payload = json.dumps(obj, ensure_ascii=False)
    except Exception:
        payload = msg.payload.decode()
    log.info("→[下行] 阿里云→本地 %s: %s", msg.topic, payload[:120])
    local_client.publish(msg.topic, payload, qos=2)

def on_aliyun_disconnect(client, userdata, rc):
    if rc == 0:
        log.info("阿里云 MQTT 已主动断开")
    else:
        log.warning("阿里云 MQTT 断开 rc=%s, 自动重连中...", rc)
    update_runtime_status(
        status="disconnected",
        aliyun_connected=False,
        last_error=f"阿里云 MQTT 断开 rc={rc}",
        last_error_at=int(time.time()),
        last_exception=f"rc={rc}",
    )

# ===================== 本地 Mosquitto 回调 =====================

def on_local_connect(client, userdata, flags, rc):
    if rc == 0:
        log.info("本地 Mosquitto 连接成功")
        # 订阅 HA 的上行 Topic（回执 + 状态）
        client.subscribe(TOPIC_NOTIFY, qos=2)
        client.subscribe(TOPIC_STATUS, qos=2)
        log.info("本地已订阅: %s, %s", TOPIC_NOTIFY, TOPIC_STATUS)
        update_runtime_status(local_connected=True)
    else:
        log.error("本地 Mosquitto 连接失败 rc=%s", rc)
        update_runtime_status(
            local_connected=False,
            last_error=f"本地 Mosquitto 连接失败 rc={rc}",
            last_error_at=int(time.time()),
            last_exception=f"rc={rc}",
        )

def on_local_message(client, userdata, msg):
    """本地 Mosquitto 收到上行消息 → 转发到阿里云"""
    payload = msg.payload.decode()

    # get_devices / get_device 时补查 HA registry
    try:
        # HA Jinja 生成的 JSON 可能有 trailing comma（最后一个元素后多逗号），清理一下
        cleaned = payload
        for _ in range(5):
            new = re.sub(r',\s*([}\]])', r'\1', cleaned)
            if new == cleaned:
                break
            cleaned = new
        if cleaned != payload:
            log.debug("清理 trailing comma 后 payload 更干净")
            payload = cleaned

        obj = json.loads(payload)
        cmd = obj.get("cmd", "")
        if cmd in ("get_devices", "get_device") and isinstance(obj.get("data"), dict):
            obj = enrich_devices_with_registry(obj)
            log.info("→[上行·补查后] %s: devices=%d", msg.topic, obj.get("data", {}).get("count", 0))
            dev0 = (obj.get("data", {}).get("devices") or [{}])[0]
            if dev0:
                log.info("  dev[0] entity_id=%s device_id=%s unique_id=%s mfr=%s model=%s area=%s",
                         dev0.get("entity_id"), dev0.get("device_id"), dev0.get("unique_id"),
                         dev0.get("manufacturer"), dev0.get("model"), dev0.get("area"))
        # 所有上行消息都补 gatewayId + deviceId
        obj = ensure_device_ids(obj)
        payload = json.dumps(obj, ensure_ascii=False)
    except Exception as e:
        log.warning("补查 registry 失败，原样转发: %s", e)

    log.info("→[上行] 本地→阿里云 %s: %s", msg.topic, payload[:120])
    aliyun_client.publish(msg.topic, payload, qos=2)

def on_local_disconnect(client, userdata, rc):
    log.warning("本地 Mosquitto 断开 rc=%s, 自动重连中...", rc)
    update_runtime_status(
        local_connected=False,
        last_error=f"本地 Mosquitto 断开 rc={rc}",
        last_error_at=int(time.time()),
        last_exception=f"rc={rc}",
    )


def dispatch_command_via_ha_service(payload_obj):
    """直接调用 HA service 处理一条旧版 MQTT 命令。"""
    if "." not in HA_COMMAND_SERVICE:
        raise RuntimeError("HA_COMMAND_SERVICE 必须是 domain.service 形式")

    service_domain, service_name = HA_COMMAND_SERVICE.split(".", 1)
    url = f"{HA_BASE_URL}/api/services/{service_domain}/{service_name}?return_response"
    request_body = json.dumps({"payload": payload_obj}, ensure_ascii=False).encode()
    log.info(
        "开始调用 HA service: traceId=%s service=%s payload=%s",
        payload_obj.get("traceId", ""),
        HA_COMMAND_SERVICE,
        json.dumps(payload_obj, ensure_ascii=False)[:400],
    )
    update_runtime_status(
        last_message_at=int(time.time()),
        last_message_trace_id=payload_obj.get("traceId", ""),
        last_message_cmd=payload_obj.get("cmd", ""),
    )
    headers = {
        "Authorization": f"Bearer {get_ha_access_token()}",
        "Content-Type": "application/json",
    }
    req = Request(url, data=request_body, headers=headers, method="POST")

    try:
        with urlopen(req, timeout=HA_API_TIMEOUT) as resp:
            raw = resp.read().decode()
    except Exception as e:
        log.error("调用 HA service 失败: %s", e)
        update_runtime_status(
            status="error",
            last_error="调用 HA service 失败",
            last_error_at=int(time.time()),
            last_exception=str(e),
            last_response_status="error",
        )
        return build_error_response(payload_obj, str(e))

    try:
        parsed = json.loads(raw) if raw else {}
    except Exception as e:
        log.error("解析 HA service 响应失败: %s raw=%s", e, raw[:300])
        update_runtime_status(
            status="error",
            last_error="解析 HA service 响应失败",
            last_error_at=int(time.time()),
            last_exception=str(e),
            last_response_status="error",
        )
        return build_error_response(payload_obj, f"HA service 返回非 JSON: {e}")

    response_data = extract_service_response(parsed)
    if not isinstance(response_data, dict):
        return build_error_response(payload_obj, "HA service 返回结构异常")
    log.info(
        "HA service 调用完成: traceId=%s result=%s",
        payload_obj.get("traceId", ""),
        json.dumps(response_data, ensure_ascii=False)[:400],
    )
    response_status = ""
    if isinstance(response_data.get("data"), dict):
        response_status = str(response_data["data"].get("status") or "").strip()
    elif isinstance(response_data, dict):
        response_status = str(response_data.get("status") or "").strip()
    update_runtime_status(
        status="connected",
        last_response_status=response_status,
        last_error="" if response_status == "ok" else runtime_status.get("last_error", ""),
    )
    return response_data


def extract_service_response(parsed):
    """尽量兼容不同 HA service API 返回结构。"""
    if isinstance(parsed, dict):
        if isinstance(parsed.get("service_response"), dict):
            return parsed["service_response"]
        if isinstance(parsed.get("data"), dict) and parsed.get("type") == "notify":
            return parsed
        return parsed
    if isinstance(parsed, list) and parsed:
        first = parsed[0]
        if isinstance(first, dict) and isinstance(first.get("service_response"), dict):
            return first["service_response"]
    return {}


def build_error_response(payload_obj, message):
    """构造统一错误响应。"""
    return {
        "traceId": payload_obj.get("traceId", ""),
        "cmd": payload_obj.get("cmd", ""),
        "type": "notify",
        "gatewayId": HOME_MAC,
        "deviceId": payload_obj.get("deviceId") or HOME_MAC,
        "time": int(time.time()),
        "data": {
            "status": "error",
            "message": str(message),
        },
    }


def build_processing_response(payload_obj):
    """构造统一 processing/executing 响应。"""
    data = {
        "status": "executing",
        "message": "指令处理中",
    }

    raw_data = payload_obj.get("data")
    if isinstance(raw_data, dict):
        service = raw_data.get("service")
        if isinstance(service, str) and service.strip():
            data["service"] = service.strip()

        entity_id = raw_data.get("entity_id")
        if isinstance(entity_id, str) and entity_id.strip():
            data["target"] = {"entity_id": entity_id.strip()}

        params = raw_data.get("params")
        if isinstance(params, dict):
            data["service_data"] = params

        for key in ("scene_id", "id", "name"):
            value = raw_data.get(key)
            if isinstance(value, str) and value.strip():
                data[key] = value.strip()

    return {
        "traceId": payload_obj.get("traceId", ""),
        "cmd": payload_obj.get("cmd", ""),
        "type": "status",
        "gatewayId": HOME_MAC,
        "deviceId": payload_obj.get("deviceId") or HOME_MAC,
        "time": int(time.time()),
        "data": data,
    }


def should_publish_processing_response(cmd):
    """Whether a command should emit an immediate executing response."""
    return cmd in {
        "service_call",
        "call_service",
        "create_scene",
        "update_scene",
        "delete_scene",
        "activate_scene",
        "enable_scene",
        "disable_scene",
    }


def publish_aliyun_response(request_obj, response_obj):
    """把 HA service 的响应发布回阿里云。"""
    if not isinstance(response_obj, dict):
        response_obj = build_error_response(request_obj, "返回结果不是对象")

    response_obj = ensure_device_ids(
        response_obj,
        default_device_id=request_obj.get("deviceId") or HOME_MAC,
    )
    topic = response_topic_for_response(request_obj.get("cmd", ""), response_obj)
    payload = json.dumps(response_obj, ensure_ascii=False)
    log.info("→[上行] HA service→阿里云 %s: %s", topic, payload[:120])
    response_data = response_obj.get("data") if isinstance(response_obj, dict) else {}
    update_runtime_status(
        last_response_topic=topic,
        last_response_status=(
            str(response_data.get("status") or "").strip()
            if isinstance(response_data, dict)
            else ""
        ),
    )
    aliyun_client.publish(topic, payload, qos=2)


def response_topic_for_response(cmd, response_obj):
    """根据响应类型和命令选择上行 topic。"""
    if isinstance(response_obj, dict) and response_obj.get("type") == "status":
        return TOPIC_STATUS
    if cmd in ("get_devices", "get_device", "list_scenes"):
        return TOPIC_STATUS
    return TOPIC_NOTIFY


def get_ha_access_token():
    """Return a valid Home Assistant access token."""
    global ha_access_token, ha_access_token_expires_at

    if HA_ACCESS_TOKEN:
        return HA_ACCESS_TOKEN

    if ha_access_token and time.time() < ha_access_token_expires_at - 60:
        return ha_access_token

    if not HA_REFRESH_TOKEN:
        raise RuntimeError(
            "COMMAND_DISPATCH_MODE=ha_service 时必须设置 HA_ACCESS_TOKEN 或 HA_REFRESH_TOKEN"
        )
    if not HA_CLIENT_ID:
        raise RuntimeError("使用 HA_REFRESH_TOKEN 时必须设置 HA_CLIENT_ID")

    form = urlencode(
        {
            "grant_type": "refresh_token",
            "client_id": HA_CLIENT_ID,
            "refresh_token": HA_REFRESH_TOKEN,
        }
    ).encode()
    req = Request(
        HA_TOKEN_URL,
        data=form,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )

    try:
        with urlopen(req, timeout=HA_API_TIMEOUT) as resp:
            token_data = json.loads(resp.read().decode())
    except Exception as e:
        raise RuntimeError(f"刷新 HA access token 失败: {e}") from e

    access_token = str(token_data.get("access_token") or "").strip()
    expires_in = int(token_data.get("expires_in") or 1800)
    if not access_token:
        raise RuntimeError(f"HA token 接口返回异常: {token_data}")

    ha_access_token = access_token
    ha_access_token_expires_at = time.time() + expires_in
    log.info("已刷新 HA access token，expires_in=%ss", expires_in)
    return ha_access_token

# ===================== HA 日志镜像 =====================

class HALogMirrorHandler(logging.Handler):
    """把 bridge 日志异步写入 HA 的 system_log，方便在 HA Web UI 里查看。"""

    LEVEL_MAP = {
        "DEBUG": "debug",
        "INFO": "info",
        "WARNING": "warning",
        "ERROR": "error",
        "CRITICAL": "critical",
    }

    def __init__(self, level=logging.INFO):
        super().__init__(level=level)
        self._queue = queue.Queue(maxsize=1000)
        threading.Thread(target=self._run, daemon=True).start()

    def emit(self, record):
        if getattr(record, "ha_mirror_internal", False):
            return
        text = record.getMessage()
        for keyword in HA_LOG_MIRROR_SKIP:
            if keyword in text:
                return
        try:
            self._queue.put_nowait((record.levelname, text))
        except queue.Full:
            pass

    def _run(self):
        warned = False
        while True:
            levelname, text = self._queue.get()
            try:
                token = get_ha_access_token()
                payload = json.dumps(
                    {
                        "message": text,
                        "level": self.LEVEL_MAP.get(levelname, "info"),
                        "logger": HA_LOG_MIRROR_LOGGER,
                    }
                ).encode()
                req = Request(
                    f"{HA_BASE_URL}/api/services/system_log/write",
                    data=payload,
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Content-Type": "application/json",
                    },
                    method="POST",
                )
                with urlopen(req, timeout=HA_API_TIMEOUT) as resp:
                    resp.read()
                warned = False
            except Exception as e:
                # 镜像失败不影响 bridge 主流程，只提示一次
                if not warned:
                    warned = True
                    log.warning(
                        "HA 日志镜像推送失败: %s",
                        e,
                        extra={"ha_mirror_internal": True},
                    )


def attach_ha_log_mirror():
    """按需开启 HA 日志镜像。"""
    if not HA_LOG_MIRROR:
        return
    if not (HA_ACCESS_TOKEN or HA_REFRESH_TOKEN):
        log.warning("HA 日志镜像未开启：缺少 HA_ACCESS_TOKEN / HA_REFRESH_TOKEN")
        return
    handler = HALogMirrorHandler(level=getattr(logging, HA_LOG_MIRROR_LEVEL, logging.INFO))
    handler.setFormatter(logging.Formatter("%(message)s"))
    log.addHandler(handler)


# ===================== 主流程 =====================

def setup_aliyun(cred):
    global aliyun_client
    port = int(cred.get("tcpPort", 8883))
    use_ssl = (port == 8883)  # 阿里云 MQTT 默认 8883 走 TLS
    client = mqtt.Client(
        client_id=cred["clientId"],  # 必须和 password 签名的 clientId 一致
        clean_session=True,
        protocol=mqtt.MQTTv311,
    )
    client.username_pw_set(cred["username"], cred["password"])
    client.will_set(
        TOPIC_STATUS,
        build_gateway_presence_payload(
            "offline",
            reason="unexpected_disconnect",
            source="mqtt_lwt",
            ha_alive=False,
        ),
        qos=1,
        retain=False,
    )
    if use_ssl:
        client.tls_set()  # 用系统 CA 证书
    client.on_connect = on_aliyun_connect
    client.on_message = on_aliyun_message
    client.on_disconnect = on_aliyun_disconnect
    client.reconnect_delay_set(min_delay=5, max_delay=60)
    log.info("阿里云 MQTT: endpoint=%s port=%d ssl=%s", cred["endpoint"], port, use_ssl)
    client.connect_async(cred["endpoint"], port, keepalive=25)
    return client

def setup_local():
    global local_client
    client = mqtt.Client(
        client_id=f"bridge_{HOME_MAC}_{os.getpid()}",
        clean_session=True,
        protocol=mqtt.MQTTv311,
    )
    client.username_pw_set(LOCAL_USER, LOCAL_PASS)
    client.on_connect = on_local_connect
    client.on_message = on_local_message
    client.on_disconnect = on_local_disconnect
    client.reconnect_delay_set(min_delay=1, max_delay=10)
    client.connect_async(LOCAL_HOST, LOCAL_PORT, keepalive=60)
    return client


def _restart_aliyun_connection(new_cred, reason):
    """Recreate the Aliyun MQTT client with fresh credentials."""
    global aliyun_client, aliyun_cred
    old_client = aliyun_client
    if old_client:
        try:
            old_client.loop_stop()
            old_client.disconnect()
        except Exception as e:
            log.warning("关闭旧阿里云 MQTT 连接失败: %s", e)

    aliyun_client = setup_aliyun(new_cred)
    aliyun_cred = new_cred
    aliyun_client.loop_start()
    log.info(
        "阿里云 MQTT 已重建: reason=%s endpoint=%s clientId=%s",
        reason,
        new_cred.get("endpoint", ""),
        new_cred.get("clientId", ""),
    )
    update_runtime_status(
        status="reconnecting",
        last_reinitialize_reason=reason,
    )


def _restart_local_connection(reason):
    """Recreate the local MQTT client when mqtt dispatch mode is enabled."""
    global local_client
    old_client = local_client
    if old_client:
        try:
            old_client.loop_stop()
            old_client.disconnect()
        except Exception as e:
            log.warning("关闭旧本地 MQTT 连接失败: %s", e)

    local_client = setup_local()
    local_client.loop_start()
    log.info("本地 MQTT 已重建: reason=%s host=%s port=%s", reason, LOCAL_HOST, LOCAL_PORT)
    update_runtime_status(last_reinitialize_reason=reason)


def perform_full_reinitialize(reason="manual"):
    """Re-run the full bridge runtime initialization pipeline."""
    global ha_access_token, ha_access_token_expires_at
    with runtime_reinit_lock:
        log.info("开始完整重初始化: reason=%s", reason)
        update_runtime_status(
            status="reinitializing",
            last_reinitialize_at=int(time.time()),
            last_reinitialize_reason=reason,
        )
        try:
            ha_access_token = HA_ACCESS_TOKEN
            ha_access_token_expires_at = 0
            log.info("步骤 1/4: 重置 HA access token 缓存")

            log.info("步骤 2/4: 重新加载 HA registry")
            with ha_registry_lock:
                load_ha_registry()

            log.info("步骤 3/4: 重新获取公网 MQTT 凭证")
            new_cred = fetch_aliyun_credential()
            _restart_aliyun_connection(new_cred, reason)

            if COMMAND_DISPATCH_MODE == "mqtt":
                log.info("步骤 4/4: 重建本地 MQTT 连接")
                _restart_local_connection(reason)
            else:
                log.info("步骤 4/4: 当前为 ha_service 模式，跳过本地 MQTT 重建")

            log.info("完整重初始化完成: reason=%s", reason)
            update_runtime_status(
                status="connected",
                last_error="",
                last_exception="",
                last_reinitialize_at=int(time.time()),
                last_reinitialize_reason=reason,
            )
        except Exception as e:
            log.exception("完整重初始化失败: reason=%s error=%s", reason, e)
            update_runtime_status(
                status="error",
                last_error="完整重初始化失败",
                last_error_at=int(time.time()),
                last_exception=str(e),
                last_reinitialize_at=int(time.time()),
                last_reinitialize_reason=reason,
            )


def _consume_runtime_command(command_path):
    """Consume one runtime control command file."""
    try:
        with open(command_path, encoding="utf-8") as f:
            command = json.load(f)
    except Exception as e:
        log.warning("读取运行时控制命令失败: path=%s error=%s", command_path, e)
        try:
            os.remove(command_path)
        except OSError:
            pass
        return

    action = str(command.get("action") or "").strip()
    reason = str(command.get("reason") or "manual").strip() or "manual"
    log.info("收到运行时控制命令: action=%s reason=%s path=%s", action, reason, command_path)
    try:
        if action == "full_reinitialize":
            perform_full_reinitialize(reason)
        else:
            log.warning("忽略未知运行时控制命令: action=%s path=%s", action, command_path)
    finally:
        try:
            os.remove(command_path)
        except OSError as e:
            log.warning("删除运行时控制命令失败: path=%s error=%s", command_path, e)


def runtime_control_loop():
    """Poll the shared control directory for runtime commands."""
    os.makedirs(CONTROL_DIR, exist_ok=True)
    while running:
        time.sleep(CONTROL_POLL_INTERVAL)
        try:
            pending = sorted(
                os.path.join(CONTROL_DIR, name)
                for name in os.listdir(CONTROL_DIR)
                if name.endswith(".json")
            )
        except FileNotFoundError:
            os.makedirs(CONTROL_DIR, exist_ok=True)
            pending = []
        except Exception as e:
            log.warning("扫描运行时控制目录失败: %s", e)
            continue

        for command_path in pending:
            _consume_runtime_command(command_path)

def token_refresh_loop():
    """定时刷新阿里云凭证（重新连接）"""
    while running:
        time.sleep(TOKEN_REFRESH_INTERVAL)
        try:
            log.info("定时刷新阿里云 MQTT 凭证...")
            new_cred = fetch_aliyun_credential()
            # 如果凭证变了，重连
            if new_cred["clientId"] != aliyun_cred.get("clientId") or \
               new_cred["password"] != aliyun_cred.get("password"):
                log.info("凭证有变化，重连阿里云 MQTT")
                _restart_aliyun_connection(new_cred, "token_refresh")
            else:
                log.info("凭证无变化，跳过重连")
        except Exception as e:
            log.error("凭证刷新失败，保持旧连接: %s", e)
            update_runtime_status(
                last_error="凭证刷新失败",
                last_error_at=int(time.time()),
                last_exception=str(e),
            )

def check_ha_alive():
    """HTTP 探测 HA 是否活着，避免访问需要认证的 /api/ 触发告警。"""
    ha_base_url = os.environ.get("HA_BASE_URL", "").strip()
    if ha_base_url:
        parsed = urlparse(ha_base_url)
        scheme = parsed.scheme or "http"
        netloc = parsed.netloc or parsed.path
        target_url = f"{scheme}://{netloc}/"
    else:
        ha_host = os.environ.get("HA_HOST", "127.0.0.1")
        ha_port = int(os.environ.get("HA_PORT", "8123"))
        target_url = f"http://{ha_host}:{ha_port}/"
    ha_alive = False
    try:
        req = Request(target_url, method="GET")
        with urlopen(req, timeout=2) as resp:
            ha_alive = resp.status == 200
    except Exception as e:
        # HTTPError 也是 urlopen 抛的，带 .code 属性
        if hasattr(e, "code"):
            ha_alive = e.code == 200
        else:
            log.debug("HA health check 失败: %s", e)
    return ha_alive


def build_gateway_presence_payload(status, *, reason="", source="", ha_alive=False):
    """Build a gateway presence payload for heartbeat and offline notices."""
    return json.dumps(
        {
            "traceId": f"{status[:3]}_" + uuid.uuid4().hex[:12],
            "cmd": "heartbeat",
            "type": "notify",
            "gatewayId": HOME_MAC,
            "deviceId": HOME_MAC,
            "time": int(time.time()),
            "data": {
                "status": status,
                "ha_alive": ha_alive,
                "reason": reason,
                "source": source,
            },
        },
        ensure_ascii=False,
    )


def publish_gateway_offline(client, *, reason, source):
    """Publish an explicit offline notice before a graceful shutdown when possible."""
    if client is None or not client.is_connected():
        return False

    payload = build_gateway_presence_payload(
        "offline",
        reason=reason,
        source=source,
        ha_alive=False,
    )
    try:
        info = client.publish(TOPIC_STATUS, payload, qos=1)
        info.wait_for_publish(timeout=3)
        log.info("已发送离线通知: reason=%s source=%s", reason, source)
        return True
    except Exception as e:
        log.warning("发送离线通知失败，将回退到 MQTT 遗言: %s", e)
        return False

def heartbeat_loop():
    """每 20s 往阿里云 MQTT 发一条心跳消息，顺便上报 HA 存活状态"""
    HEARTBEAT_TOPIC = f"iot/device/{HOME_MAC}/status"
    HEARTBEAT_INTERVAL = 20
    while running:
        time.sleep(HEARTBEAT_INTERVAL)
        if aliyun_client and aliyun_client.is_connected():
            try:
                ha_alive = check_ha_alive()
                payload = build_gateway_presence_payload(
                    "alive",
                    reason="heartbeat",
                    source="runtime",
                    ha_alive=ha_alive,
                )
                aliyun_client.publish(HEARTBEAT_TOPIC, payload, qos=0)
                log.info("心跳已发送 ha_alive=%s", ha_alive)
                update_runtime_status(
                    last_heartbeat_at=int(time.time()),
                    last_ha_alive=ha_alive,
                )
            except Exception as e:
                log.warning("心跳发送失败: %s", e)
                update_runtime_status(
                    last_error="心跳发送失败",
                    last_error_at=int(time.time()),
                    last_exception=str(e),
                )

def signal_handler(sig, frame):
    global running
    log.info("收到停止信号，正在优雅退出...")
    running = False
    update_runtime_status(status="stopped")
    offline_sent = publish_gateway_offline(
        aliyun_client,
        reason=f"signal_{sig}",
        source="graceful_shutdown",
    )
    if aliyun_client:
        aliyun_client.loop_stop()
        if offline_sent:
            aliyun_client.disconnect()
    if local_client:
        local_client.loop_stop()
        local_client.disconnect()
    sys.exit(0)

def main():
    global aliyun_client, local_client, aliyun_cred

    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    attach_ha_log_mirror()
    update_runtime_status(status="starting")

    log.info("=" * 60)
    log.info("家庭 MQTT 桥接启动")
    log.info("  HOME_MAC      = %s", HOME_MAC)
    log.info("  下行 Topic    = %s", TOPIC_COMMAND)
    log.info("  上行 Topic    = %s, %s", TOPIC_NOTIFY, TOPIC_STATUS)
    log.info("  Local MQTT    = %s:%s", LOCAL_HOST, LOCAL_PORT)
    log.info("  Dispatch Mode = %s", COMMAND_DISPATCH_MODE)
    if COMMAND_DISPATCH_MODE == "ha_service":
        log.info("  HA Service    = %s @ %s", HA_COMMAND_SERVICE, HA_BASE_URL)
        if HA_ACCESS_TOKEN:
            log.info("  HA Auth       = access token")
        elif HA_REFRESH_TOKEN:
            log.info("  HA Auth       = refresh token @ %s", HA_TOKEN_URL)
    log.info("  Token API     = %s", HTTP_API_URL)
    log.info("=" * 60)

    if COMMAND_DISPATCH_MODE not in ("mqtt", "ha_service"):
        log.error("不支持的 COMMAND_DISPATCH_MODE: %s", COMMAND_DISPATCH_MODE)
        sys.exit(1)

    # 1. 获取阿里云凭证
    for attempt in range(5):
        try:
            aliyun_cred = fetch_aliyun_credential()
            break
        except Exception as e:
            log.error("获取凭证失败(%d/5): %s", attempt + 1, e)
            time.sleep(10)
    else:
        log.error("获取阿里云 MQTT 凭证彻底失败，退出")
        sys.exit(1)

    # 2. 连接阿里云
    _restart_aliyun_connection(aliyun_cred, "startup")

    # 3. 仅在 mqtt 转发模式下连接本地 Mosquitto
    if COMMAND_DISPATCH_MODE == "mqtt":
        _restart_local_connection("startup")
    else:
        log.info("跳过本地 Mosquitto 连接（ha_service 模式）")

    # 4. 启动凭证刷新线程
    threading.Thread(target=token_refresh_loop, daemon=True).start()

    # 5. 启动心跳线程（防止阿里云 Serverless 空闲超时）
    threading.Thread(target=heartbeat_loop, daemon=True).start()

    # 6. 加载 HA registry + 启动刷新线程
    load_ha_registry()
    threading.Thread(target=ha_registry_refresh_loop, daemon=True).start()
    threading.Thread(target=runtime_control_loop, daemon=True).start()

    # 5. 阻塞主线程
    while running:
        time.sleep(1)

if __name__ == "__main__":
    main()
