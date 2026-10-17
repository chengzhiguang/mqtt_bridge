"""Shared runtime helpers for MQTT Bridge entities and services."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

RUNTIME_DIR = Path("/config/mqtt_bridge_runtime")
RUNTIME_LOG_PATH = RUNTIME_DIR / "bridge.log"
RUNTIME_STATUS_PATH = RUNTIME_DIR / "runtime_status.json"
RUNTIME_CONTROL_DIR = RUNTIME_DIR / "control"


def read_runtime_status() -> dict[str, Any]:
    """Read the persisted bridge runtime status file."""
    if not RUNTIME_STATUS_PATH.exists():
        return {}

    try:
        payload = json.loads(
            RUNTIME_STATUS_PATH.read_text(encoding="utf-8", errors="replace")
        )
    except Exception:
        return {}

    return payload if isinstance(payload, dict) else {}


def read_runtime_logs(lines: int = 20) -> list[str]:
    """Read the last N lines from the bridge runtime log."""
    if not RUNTIME_LOG_PATH.exists():
        return []

    try:
        rows = RUNTIME_LOG_PATH.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        return []

    return rows[-lines:]
