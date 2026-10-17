"""Managed scene support for MQTT Bridge."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from typing import Any

from homeassistant.components.scene import Scene
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.storage import Store

from .const import DOMAIN, STORAGE_KEY, STORAGE_VERSION

type SceneRecord = dict[str, Any]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up MQTT Bridge managed scenes."""
    manager = get_scene_manager(hass, entry.entry_id)
    manager.async_register_add_entities(async_add_entities)
    entry.async_on_unload(manager.async_unregister_add_entities)


def get_scene_manager(hass: HomeAssistant, entry_id: str) -> MqttBridgeSceneManager:
    """Return the managed scene manager for an entry."""
    return hass.data[DOMAIN][entry_id]["scene_manager"]


class MqttBridgeSceneManager:
    """Manage MQTT Bridge scenes stored in Home Assistant storage."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        """Initialize the scene manager."""
        self.hass = hass
        self.entry_id = entry_id
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, f"{STORAGE_KEY}_{entry_id}"
        )
        self._records: dict[str, SceneRecord] = {}
        self._entities: dict[str, MqttBridgeManagedScene] = {}
        self._async_add_entities: AddEntitiesCallback | None = None

    async def async_load(self) -> None:
        """Load scene data from Home Assistant storage."""
        payload = await self._store.async_load()
        scenes = payload.get("scenes", []) if isinstance(payload, dict) else []

        self._records = {}
        for item in scenes:
            if not isinstance(item, dict):
                continue
            record = self._normalize_scene_record(item, allow_missing_id=False)
            self._records[record["id"]] = record

    def async_register_add_entities(
        self, async_add_entities: AddEntitiesCallback
    ) -> None:
        """Register the entity add callback from the scene platform."""
        self._async_add_entities = async_add_entities
        new_entities = [
            self._get_or_create_entity(record["id"])
            for record in self._sorted_records(enabled=True)
            if record["id"] not in self._entities
        ]
        if new_entities:
            async_add_entities(new_entities)

    def async_unregister_add_entities(self) -> None:
        """Clear the entity add callback when the platform unloads."""
        self._async_add_entities = None

    def list_managed_scenes(self) -> dict[str, list[dict[str, Any]]]:
        """Return managed scenes grouped by enablement."""
        enabled = [
            self.serialize_scene_summary(record)
            for record in self._sorted_records(enabled=True)
        ]
        disabled = [
            self.serialize_scene_summary(record)
            for record in self._sorted_records(enabled=False)
        ]
        return {"enabled": enabled, "disabled": disabled}

    def get_scene(self, scene_id: str) -> SceneRecord | None:
        """Return a managed scene by id."""
        return self._records.get(scene_id)

    async def async_create_scene(self, scene_data: Mapping[str, Any]) -> SceneRecord:
        """Create a new managed scene."""
        record = self._normalize_scene_record(scene_data, allow_missing_id=False)
        scene_id = record["id"]
        if scene_id in self._records:
            raise ValueError(f"场景 {scene_id} 已存在")

        self._records[scene_id] = record
        await self._async_save()
        if record["enabled"]:
            self._async_add_scene_entity(scene_id)
        return deepcopy(record)

    async def async_update_scene(
        self, scene_id: str, updates: Mapping[str, Any]
    ) -> SceneRecord:
        """Update an existing managed scene."""
        record = self._records.get(scene_id)
        if record is None:
            raise ValueError(f"场景 {scene_id} 不存在")
        if not record["enabled"]:
            raise ValueError(f"场景 {scene_id} 已停用，请先启用再编辑")

        if "name" in updates:
            record["name"] = self._normalize_name(updates.get("name"), scene_id)
        if "icon" in updates:
            record["icon"] = self._normalize_icon(updates.get("icon"))
        if "entities" in updates:
            record["entities"] = self._normalize_entities(updates.get("entities"))

        self._records[scene_id] = record
        await self._async_save()

        entity = self._entities.get(scene_id)
        if entity is not None:
            entity.async_sync_from_record(record)

        return deepcopy(record)

    async def async_delete_scene(self, scene_id: str) -> SceneRecord:
        """Delete a managed scene."""
        record = self._records.pop(scene_id, None)
        if record is None:
            raise ValueError(f"场景 {scene_id} 不存在")

        await self._async_remove_scene_entity(scene_id)
        await self._async_save()
        return deepcopy(record)

    async def async_enable_scene(self, scene_id: str) -> SceneRecord:
        """Enable a managed scene."""
        record = self._records.get(scene_id)
        if record is None:
            raise ValueError(f"场景 {scene_id} 不存在")
        if record["enabled"]:
            raise ValueError(f"场景 {scene_id} 已启用")

        record["enabled"] = True
        self._records[scene_id] = record
        await self._async_save()
        self._async_add_scene_entity(scene_id)
        return deepcopy(record)

    async def async_disable_scene(self, scene_id: str) -> SceneRecord:
        """Disable a managed scene."""
        record = self._records.get(scene_id)
        if record is None:
            raise ValueError(f"场景 {scene_id} 不存在")
        if not record["enabled"]:
            raise ValueError(f"场景 {scene_id} 不存在或已停用")

        record["enabled"] = False
        self._records[scene_id] = record
        await self._async_remove_scene_entity(scene_id)
        await self._async_save()
        return deepcopy(record)

    def serialize_scene_summary(self, record: SceneRecord) -> dict[str, Any]:
        """Convert a stored scene record to a bridge-friendly payload."""
        return {
            "id": record["id"],
            "name": record["name"],
            "entity_id": f"scene.{record['id']}",
            "icon": record.get("icon"),
            "entities": list(record.get("entities", {}).keys()),
            "enabled": bool(record.get("enabled", True)),
            "managed": True,
        }

    def _normalize_scene_record(
        self, scene_data: Mapping[str, Any], *, allow_missing_id: bool
    ) -> SceneRecord:
        """Normalize scene data for storage."""
        scene_id = self._normalize_scene_id(
            scene_data.get("id") or scene_data.get("scene_id") or scene_data.get("entity_id"),
            allow_missing=allow_missing_id,
        )
        return {
            "id": scene_id,
            "name": self._normalize_name(scene_data.get("name"), scene_id),
            "icon": self._normalize_icon(scene_data.get("icon")),
            "entities": self._normalize_entities(scene_data.get("entities")),
            "enabled": bool(scene_data.get("enabled", True)),
        }

    def _normalize_scene_id(self, raw_scene_id: Any, *, allow_missing: bool) -> str:
        """Normalize scene id or entity_id to the internal scene id."""
        text = str(raw_scene_id or "").strip()
        if text.startswith("scene."):
            text = text.split(".", 1)[1]
        if text:
            return text
        if allow_missing:
            return ""
        raise ValueError("缺少场景 id")

    def _normalize_name(self, raw_name: Any, fallback: str) -> str:
        """Normalize the scene display name."""
        name = str(raw_name or "").strip()
        return name or fallback

    def _normalize_icon(self, raw_icon: Any) -> str | None:
        """Normalize the optional scene icon."""
        icon = str(raw_icon or "").strip()
        return icon or None

    def _normalize_entities(self, raw_entities: Any) -> dict[str, dict[str, Any]]:
        """Normalize scene entities to a dict payload."""
        if not isinstance(raw_entities, Mapping) or not raw_entities:
            raise ValueError("缺少 entities 字段")

        normalized: dict[str, dict[str, Any]] = {}
        for entity_id, entity_data in raw_entities.items():
            key = str(entity_id).strip()
            if not key:
                continue
            if not isinstance(entity_data, Mapping):
                raise ValueError(f"实体 {key} 的场景数据必须是对象")
            normalized[key] = dict(entity_data)

        if not normalized:
            raise ValueError("缺少有效的 entities 配置")
        return normalized

    def _sorted_records(self, *, enabled: bool) -> list[SceneRecord]:
        """Return stored scenes sorted by id."""
        return sorted(
            (
                deepcopy(record)
                for record in self._records.values()
                if bool(record.get("enabled", True)) is enabled
            ),
            key=lambda item: item["id"],
        )

    def _get_or_create_entity(self, scene_id: str) -> MqttBridgeManagedScene:
        """Return an existing entity or create a new one."""
        entity = self._entities.get(scene_id)
        if entity is None:
            entity = MqttBridgeManagedScene(self, scene_id)
            self._entities[scene_id] = entity
        return entity

    def _async_add_scene_entity(self, scene_id: str) -> None:
        """Add a managed scene entity if the scene platform is ready."""
        if self._async_add_entities is None:
            return
        if scene_id in self._entities:
            entity = self._entities[scene_id]
            entity.async_sync_from_record(self._records[scene_id])
            return
        self._async_add_entities([self._get_or_create_entity(scene_id)])

    async def _async_remove_scene_entity(self, scene_id: str) -> None:
        """Remove an entity from Home Assistant if it is loaded."""
        entity = self._entities.pop(scene_id, None)
        if entity is not None:
            await entity.async_remove(force_remove=True)

    async def _async_save(self) -> None:
        """Persist all managed scenes to Home Assistant storage."""
        await self._store.async_save(
            {
                "scenes": [
                    deepcopy(self._records[scene_id])
                    for scene_id in sorted(self._records)
                ]
            }
        )


class MqttBridgeManagedScene(Scene):
    """A persistent MQTT Bridge managed scene entity."""

    _attr_should_poll = False

    def __init__(self, manager: MqttBridgeSceneManager, scene_id: str) -> None:
        """Initialize the managed scene."""
        self._manager = manager
        self._scene_id = scene_id
        self.entity_id = f"scene.{scene_id}"
        self._apply_record(manager.get_scene(scene_id) or {"id": scene_id, "entities": {}})

    async def async_activate(self, **kwargs: Any) -> None:
        """Activate the managed scene through Home Assistant scene.apply."""
        del kwargs
        record = self._manager.get_scene(self._scene_id)
        if record is None:
            return
        await self.hass.services.async_call(
            "scene",
            "apply",
            service_data={"entities": deepcopy(record["entities"])},
            blocking=True,
            return_response=False,
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional state attributes for inspection."""
        record = self._manager.get_scene(self._scene_id) or {}
        return {
            "managed_by": DOMAIN,
            "managed": True,
            "scene_id": self._scene_id,
            "entity_count": len(record.get("entities", {})),
            "entity_id": list(record.get("entities", {}).keys()),
        }

    @callback
    def async_sync_from_record(self, record: SceneRecord) -> None:
        """Update entity attributes from storage and write state."""
        self._apply_record(record)
        self.async_write_ha_state()

    def _apply_record(self, record: Mapping[str, Any]) -> None:
        """Apply a storage record to entity attributes."""
        self._attr_name = str(record.get("name") or self._scene_id)
        self._attr_icon = record.get("icon")
        self._attr_unique_id = f"{DOMAIN}_scene_{self._scene_id}"
