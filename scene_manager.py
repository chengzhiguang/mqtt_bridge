#!/usr/bin/env python3
"""
HA 场景管理脚本
操作 .storage/scenes.yaml，支持 CRUD 和启用/停用

用法:
  python3 scene_manager.py list
  python3 scene_manager.py create '<json>'
  python3 scene_manager.py create --file <path>
  python3 scene_manager.py update <scene_id> '<json>'
  python3 scene_manager.py update <scene_id> --file <path>
  python3 scene_manager.py delete <scene_id>
  python3 scene_manager.py enable <scene_id>
  python3 scene_manager.py disable <scene_id>

场景 JSON 格式:
{
  "id": "movie_mode",
  "name": "电影模式",
  "icon": "mdi:movie",
  "entities": {
    "light.living_room": {"state": "on", "brightness": 80},
    "light.ceiling": {"state": "off"}
  }
}
"""
import json
import sys
import os

HA_CONFIG_DIR = os.environ.get("HA_CONFIG_DIR", "/config")
SCENES_FILE = os.path.join(HA_CONFIG_DIR, "scenes.yaml")
DISABLED_FILE = os.path.join(HA_CONFIG_DIR, ".storage", "disabled_scenes.yaml")
CUSTOMIZE_FILE = os.path.join(HA_CONFIG_DIR, "customize.yaml")
ENTITY_REGISTRY_FILE = os.path.join(HA_CONFIG_DIR, ".storage", "core.entity_registry")
RESTORE_STATE_FILE = os.path.join(HA_CONFIG_DIR, ".storage", "core.restore_state")

MQTT_HOST = os.environ.get("MQTT_HOST", "127.0.0.1")
MQTT_PORT = int(os.environ.get("MQTT_PORT", "1883"))
MQTT_USER = os.environ.get("MQTT_USER", "ha")
MQTT_PASS = os.environ.get("MQTT_PASS", "")
MQTT_TOPIC_STATUS = os.environ.get("MQTT_TOPIC_STATUS", "iot/device/status")


def get_mac_address():
    """获取本机 MAC 地址（大写，无冒号）"""
    try:
        import uuid
        mac = uuid.getnode()
        return format(mac, "012X")
    except Exception:
        return "UNKNOWN"


def load_mqtt_config():
    """从 HA 的 core.config_entries 读取 MQTT 配置"""
    global MQTT_HOST, MQTT_PORT, MQTT_USER, MQTT_PASS, MQTT_TOPIC_STATUS
    config_entries = os.path.join(HA_CONFIG_DIR, ".storage", "core.config_entries")
    if not os.path.exists(config_entries):
        return
    try:
        with open(config_entries, "r", encoding="utf-8") as f:
            data = json.load(f)
        for entry in data.get("data", {}).get("entries", []):
            if entry.get("domain") == "mqtt":
                mqtt_data = entry.get("data", {})
                MQTT_HOST = mqtt_data.get("broker", MQTT_HOST)
                MQTT_PORT = int(mqtt_data.get("port", MQTT_PORT))
                MQTT_USER = mqtt_data.get("username", MQTT_USER)
                MQTT_PASS = mqtt_data.get("password", MQTT_PASS)
                break
    except (json.JSONDecodeError, KeyError, ValueError):
        pass
    # 构造带 MAC 的 status topic
    if MQTT_TOPIC_STATUS == "iot/device/status":
        mac = get_mac_address()
        MQTT_TOPIC_STATUS = f"iot/device/{mac}/status"


def mqtt_publish(payload):
    """通过 paho-mqtt 发布消息到 MQTT"""
    try:
        import paho.mqtt.client as mqtt
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        if MQTT_USER:
            client.username_pw_set(MQTT_USER, MQTT_PASS)
        client.connect(MQTT_HOST, MQTT_PORT, 60)
        client.loop_start()
        result = client.publish(MQTT_TOPIC_STATUS, payload, qos=2)
        result.wait_for_publish(timeout=10)
        client.loop_stop()
        client.disconnect()
    except Exception as e:
        print(f"ERROR: MQTT 发布失败: {e}", file=sys.stderr)


def load_yaml(path):
    """加载 YAML/JSON 文件"""
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()
    if not content.strip():
        return []
    # 先尝试 JSON（disabled_scenes.yaml 用 JSON 格式）
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        pass
    # 再尝试 YAML
    try:
        import yaml
        return yaml.safe_load(content) or []
    except ImportError:
        print("ERROR: 需要安装 PyYAML: pip3 install pyyaml", file=sys.stderr)
        sys.exit(1)


def save_yaml(path, data):
    """保存 YAML/JSON 文件（disabled_scenes.yaml 用 JSON 格式便于 HA sensor 读取）"""
    if path == DISABLED_FILE:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    else:
        try:
            import yaml
            with open(path, "w", encoding="utf-8") as f:
                yaml.dump(data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
        except ImportError:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)


def find_scene(scenes, scene_id):
    """根据 id 查找场景"""
    for i, s in enumerate(scenes):
        if s.get("id") == scene_id or s.get("entity_id") == scene_id:
            return i
    return -1


def update_customize(scene_id, friendly_name):
    """更新 customize.yaml 中的 friendly_name"""
    customize = {}
    if os.path.exists(CUSTOMIZE_FILE):
        try:
            import yaml
            with open(CUSTOMIZE_FILE, "r", encoding="utf-8") as f:
                customize = yaml.safe_load(f) or {}
        except ImportError:
            with open(CUSTOMIZE_FILE, "r", encoding="utf-8") as f:
                customize = json.load(f) if f.read().strip() else {}
    entity_key = f"scene.{scene_id}"
    if entity_key not in customize:
        customize[entity_key] = {}
    customize[entity_key]["friendly_name"] = friendly_name
    try:
        import yaml
        with open(CUSTOMIZE_FILE, "w", encoding="utf-8") as f:
            yaml.dump(customize, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
    except ImportError:
        with open(CUSTOMIZE_FILE, "w", encoding="utf-8") as f:
            json.dump(customize, f, ensure_ascii=False, indent=2)


def remove_customize(scene_id):
    """从 customize.yaml 中删除场景配置"""
    if not os.path.exists(CUSTOMIZE_FILE):
        return
    try:
        import yaml
        with open(CUSTOMIZE_FILE, "r", encoding="utf-8") as f:
            customize = yaml.safe_load(f) or {}
    except ImportError:
        with open(CUSTOMIZE_FILE, "r", encoding="utf-8") as f:
            customize = json.load(f) if f.read().strip() else {}
    entity_key = f"scene.{scene_id}"
    if entity_key in customize:
        del customize[entity_key]
        try:
            import yaml
            with open(CUSTOMIZE_FILE, "w", encoding="utf-8") as f:
                yaml.dump(customize, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
        except ImportError:
            with open(CUSTOMIZE_FILE, "w", encoding="utf-8") as f:
                json.dump(customize, f, ensure_ascii=False, indent=2)


def remove_from_registry(scene_id):
    """从 core.entity_registry 和 core.restore_state 中删除场景实体"""
    entity_id = f"scene.{scene_id}"

    # 清理 entity_registry
    if os.path.exists(ENTITY_REGISTRY_FILE):
        try:
            with open(ENTITY_REGISTRY_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            entities = data.get("data", {}).get("entities", [])
            original_len = len(entities)
            data["data"]["entities"] = [
                e for e in entities if e.get("entity_id") != entity_id
            ]
            if len(data["data"]["entities"]) != original_len:
                with open(ENTITY_REGISTRY_FILE, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
        except (json.JSONDecodeError, KeyError):
            pass

    # 清理 restore_state
    if os.path.exists(RESTORE_STATE_FILE):
        try:
            with open(RESTORE_STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            states = data.get("data", [])
            original_len = len(states)
            data["data"] = [
                s for s in states if s.get("entity_id") != entity_id
            ]
            if len(data["data"]) != original_len:
                with open(RESTORE_STATE_FILE, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
        except (json.JSONDecodeError, KeyError):
            pass


def cmd_list(cmd_id=""):
    """列出所有场景（启用 + 停用）"""
    load_mqtt_config()
    scenes = load_yaml(SCENES_FILE)
    disabled = load_yaml(DISABLED_FILE)
    # 加载 customize 获取友好显示名
    customize = {}
    if os.path.exists(CUSTOMIZE_FILE):
        try:
            import yaml
            with open(CUSTOMIZE_FILE, "r", encoding="utf-8") as f:
                customize = yaml.safe_load(f) or {}
        except ImportError:
            with open(CUSTOMIZE_FILE, "r", encoding="utf-8") as f:
                customize = json.load(f) if f.read().strip() else {}

    def get_display_name(s):
        entity_key = f"scene.{s.get('id', '')}"
        return customize.get(entity_key, {}).get("friendly_name", s.get("name", s.get("id", "")))

    import time
    result = {
        "cmdId": cmd_id,
        "cmd": "list_scenes",
        "type": "notify",
        "time": int(time.time()),
        "data": {
            "enabled": [{"id": s.get("id"), "name": get_display_name(s), "entity_id": f"scene.{s.get('id')}", "icon": s.get("icon"), "entities": list(s.get("entities", {}).keys())} for s in scenes],
            "disabled": [{"id": s.get("id"), "name": get_display_name(s), "entity_id": f"scene.{s.get('id')}", "icon": s.get("icon"), "entities": list(s.get("entities", {}).keys())} for s in disabled]
        }
    }
    payload = json.dumps(result, ensure_ascii=False)
    mqtt_publish(payload)
    print(json.dumps({"status": "ok", "message": "场景列表已发布"}))


def cmd_create(scene_json):
    """创建场景"""
    scene = json.loads(scene_json)
    if "id" not in scene:
        print("ERROR: 缺少 id 字段", file=sys.stderr)
        sys.exit(1)
    if "entities" not in scene:
        print("ERROR: 缺少 entities 字段", file=sys.stderr)
        sys.exit(1)

    scenes = load_yaml(SCENES_FILE)
    if find_scene(scenes, scene["id"]) >= 0:
        print(json.dumps({"status": "error", "message": f"场景 {scene['id']} 已存在"}))
        sys.exit(1)

    # 确保有 id 字段（HA 需要）
    # name 设为 id（英文），这样 entity_id 就是 scene.<id>
    # 友好显示名通过 customize.yaml 设置
    scene_entry = {
        "id": scene["id"],
        "name": scene["id"],
        "entities": scene["entities"]
    }
    if "icon" in scene:
        scene_entry["icon"] = scene["icon"]

    scenes.append(scene_entry)
    save_yaml(SCENES_FILE, scenes)
    # 设置友好显示名
    friendly_name = scene.get("name", scene["id"])
    update_customize(scene["id"], friendly_name)
    print(json.dumps({"status": "ok", "scene_id": scene["id"], "message": f"场景 {scene['id']} 创建成功"}))


def cmd_update(scene_id, scene_json):
    """更新场景"""
    update_data = json.loads(scene_json)
    scenes = load_yaml(SCENES_FILE)
    idx = find_scene(scenes, scene_id)
    if idx < 0:
        # 检查是否在停用列表中
        disabled = load_yaml(DISABLED_FILE)
        if find_scene(disabled, scene_id) >= 0:
            print(json.dumps({"status": "error", "message": f"场景 {scene_id} 已停用，请先启用再编辑"}))
        else:
            print(json.dumps({"status": "error", "message": f"场景 {scene_id} 不存在"}))
        sys.exit(1)

    # 更新字段
    if "name" in update_data:
        # name 保持为 id，友好显示名通过 customize 更新
        update_customize(scene_id, update_data["name"])
    if "icon" in update_data:
        scenes[idx]["icon"] = update_data["icon"]
    if "entities" in update_data:
        scenes[idx]["entities"] = update_data["entities"]

    save_yaml(SCENES_FILE, scenes)
    print(json.dumps({"status": "ok", "scene_id": scene_id, "message": f"场景 {scene_id} 更新成功"}))


def cmd_delete(scene_id):
    """删除场景"""
    scenes = load_yaml(SCENES_FILE)
    idx = find_scene(scenes, scene_id)
    if idx >= 0:
        scenes.pop(idx)
        save_yaml(SCENES_FILE, scenes)
        remove_customize(scene_id)
        remove_from_registry(scene_id)
        print(json.dumps({"status": "ok", "scene_id": scene_id, "message": f"场景 {scene_id} 已删除"}))
        return

    # 检查停用列表
    disabled = load_yaml(DISABLED_FILE)
    idx = find_scene(disabled, scene_id)
    if idx >= 0:
        disabled.pop(idx)
        save_yaml(DISABLED_FILE, disabled)
        remove_customize(scene_id)
        remove_from_registry(scene_id)
        print(json.dumps({"status": "ok", "scene_id": scene_id, "message": f"场景 {scene_id} 已删除（从停用列表）"}))
        return

    print(json.dumps({"status": "error", "message": f"场景 {scene_id} 不存在"}))
    sys.exit(1)


def cmd_enable(scene_id):
    """启用场景（从 disabled 移到 scenes）"""
    disabled = load_yaml(DISABLED_FILE)
    idx = find_scene(disabled, scene_id)
    if idx < 0:
        print(json.dumps({"status": "error", "message": f"场景 {scene_id} 不在停用列表中"}))
        sys.exit(1)

    scene = disabled.pop(idx)
    save_yaml(DISABLED_FILE, disabled)

    scenes = load_yaml(SCENES_FILE)
    if find_scene(scenes, scene_id) < 0:
        scenes.append(scene)
        save_yaml(SCENES_FILE, scenes)

    print(json.dumps({"status": "ok", "scene_id": scene_id, "message": f"场景 {scene_id} 已启用"}))


def cmd_disable(scene_id):
    """停用场景（从 scenes 移到 disabled）"""
    scenes = load_yaml(SCENES_FILE)
    idx = find_scene(scenes, scene_id)
    if idx < 0:
        print(json.dumps({"status": "error", "message": f"场景 {scene_id} 不存在或已停用"}))
        sys.exit(1)

    scene = scenes.pop(idx)
    save_yaml(SCENES_FILE, scenes)

    disabled = load_yaml(DISABLED_FILE)
    if find_scene(disabled, scene_id) < 0:
        disabled.append(scene)
        save_yaml(DISABLED_FILE, disabled)

    print(json.dumps({"status": "ok", "scene_id": scene_id, "message": f"场景 {scene_id} 已停用"}))


def parse_json_arg(args, start_idx=2):
    """解析 JSON 参数，支持直接传 JSON 字符串、--file <path> 或 --b64 <base64>"""
    if len(args) <= start_idx:
        print("ERROR: 缺少 JSON 参数", file=sys.stderr)
        sys.exit(1)
    if args[start_idx] == "--file":
        if len(args) <= start_idx + 1:
            print("ERROR: 缺少文件路径", file=sys.stderr)
            sys.exit(1)
        with open(args[start_idx + 1], "r", encoding="utf-8") as f:
            return f.read()
    if args[start_idx] == "--b64":
        if len(args) <= start_idx + 1:
            print("ERROR: 缺少 base64 数据", file=sys.stderr)
            sys.exit(1)
        import base64
        return base64.b64decode(args[start_idx + 1]).decode("utf-8")
    return args[start_idx]


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    command = sys.argv[1]

    if command == "list":
        cmd_id = sys.argv[2] if len(sys.argv) > 2 else ""
        cmd_list(cmd_id)
    elif command == "create":
        scene_json = parse_json_arg(sys.argv, 2)
        cmd_create(scene_json)
    elif command == "update":
        if len(sys.argv) < 3:
            print("ERROR: 缺少 scene_id", file=sys.stderr)
            sys.exit(1)
        scene_id = sys.argv[2]
        scene_json = parse_json_arg(sys.argv, 3)
        cmd_update(scene_id, scene_json)
    elif command == "delete":
        if len(sys.argv) < 3:
            print("ERROR: 缺少 scene_id", file=sys.stderr)
            sys.exit(1)
        cmd_delete(sys.argv[2])
    elif command == "enable":
        if len(sys.argv) < 3:
            print("ERROR: 缺少 scene_id", file=sys.stderr)
            sys.exit(1)
        cmd_enable(sys.argv[2])
    elif command == "disable":
        if len(sys.argv) < 3:
            print("ERROR: 缺少 scene_id", file=sys.stderr)
            sys.exit(1)
        cmd_disable(sys.argv[2])
    else:
        print(f"ERROR: 未知命令 {command}", file=sys.stderr)
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
