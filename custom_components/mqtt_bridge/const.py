"""Constants for the MQTT Bridge integration."""

DOMAIN = "mqtt_bridge"
PLATFORMS: list[str] = ["scene", "button", "sensor"]

CONF_GATEWAY_DEVICE_ID = "gateway_device_id"
CONF_NAME = "name"

SERVICE_GET_DEVICES = "get_devices"
SERVICE_GET_DEVICE = "get_device"
SERVICE_CALL_SERVICE = "call_service"
SERVICE_REFRESH_CLOUD_INFO = "refresh_cloud_info"
SERVICE_HANDLE_COMMAND = "handle_command"
SERVICE_GET_RUNTIME_LOGS = "get_runtime_logs"
SERVICE_REINITIALIZE_RUNTIME = "reinitialize_runtime"
SERVICE_LIST_SCENES = "list_scenes"
SERVICE_ACTIVATE_SCENE = "activate_scene"
SERVICE_CREATE_SCENE = "create_scene"
SERVICE_UPDATE_SCENE = "update_scene"
SERVICE_DELETE_SCENE = "delete_scene"
SERVICE_ENABLE_SCENE = "enable_scene"
SERVICE_DISABLE_SCENE = "disable_scene"

DEFAULT_NAME = "MQTT Bridge"
STORAGE_VERSION = 1
STORAGE_KEY = f"{DOMAIN}_managed_scenes"

DEFAULT_EXCLUDE_DOMAINS = {
    "automation",
    "button",
    "calendar",
    "conversation",
    "counter",
    "date",
    "datetime",
    "event",
    "geo_location",
    "group",
    "image",
    "image_processing",
    "input_button",
    "input_datetime",
    "input_number",
    "input_select",
    "input_text",
    "notify",
    "number",
    "person",
    "radio_frequency",
    "remote",
    "scene",
    "script",
    "select",
    "siren",
    "sun",
    "tags",
    "text",
    "time",
    "timer",
    "todo",
    "tts",
    "update",
    "valve",
    "wake_word",
    "weather",
    "zone",
}

DEFAULT_EXCLUDE_ENTITY_PREFIXES = (
    "binary_sensor.time_",
    "sensor.backup_",
    "sensor.calendar_",
    "sensor.date_",
    "sensor.hacs_",
    "sensor.proximity_",
    "sensor.sun_",
    "sensor.time_",
)
