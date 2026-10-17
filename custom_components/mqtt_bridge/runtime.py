"""Shared runtime helpers for MQTT Bridge entities and services."""

from __future__ import annotations

import json
import shlex
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any

RUNTIME_DIR = Path("/config/mqtt_bridge_runtime")
RUNTIME_LOG_PATH = RUNTIME_DIR / "bridge.log"
RUNTIME_STATUS_PATH = RUNTIME_DIR / "runtime_status.json"
RUNTIME_CONTROL_DIR = RUNTIME_DIR / "control"
RUNTIME_ENV_PATH = RUNTIME_DIR / "bridge.env"
RUNTIME_ENV_EXAMPLE_PATH = RUNTIME_DIR / "bridge.env.example"
RUNTIME_RESTART_SCRIPT_PATH = RUNTIME_DIR / "ha_restart_bridge.sh"
RUNTIME_PID_PATH = RUNTIME_DIR / "bridge.pid"
RUNTIME_ASSET_DIR = Path(__file__).resolve().parent / "runtime_assets"


def _asset_path(name: str) -> Path:
    """Return the path to a bundled runtime asset."""
    return RUNTIME_ASSET_DIR / name


def _read_env_map(path: Path) -> dict[str, str]:
    """Read a simple KEY=VALUE env file into a dict."""
    if not path.exists():
        return {}

    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def _write_env_map(path: Path, values: dict[str, str]) -> None:
    """Write a simple env mapping to disk."""
    rows = [f"{key}={value}" for key, value in values.items()]
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")


def _normalize_base_url(value: str) -> str:
    """Normalize a base URL by trimming whitespace and trailing slash."""
    return str(value or "").strip().rstrip("/")


def _build_managed_env(config: dict[str, Any]) -> dict[str, str]:
    """Build managed runtime env values from integration config."""
    device_id = str(
        config.get("device_id") or config.get("gateway_device_id") or ""
    ).strip().upper().replace(":", "")
    backend_base_url = _normalize_base_url(
        str(config.get("backend_base_url") or "")
    )
    token_api_url = str(config.get("mqtt_token_api") or "").strip()
    if not token_api_url and backend_base_url:
        token_api_url = f"{backend_base_url}/api/device/mqtt/token"

    ha_base_url = _normalize_base_url(str(config.get("ha_base_url") or ""))
    access_token = str(config.get("ha_access_token") or "").strip()

    managed = {
        "MQTT_TOKEN_API": token_api_url,
        "HOME_MAC": device_id,
        "COMMAND_DISPATCH_MODE": "ha_service",
        "HA_BASE_URL": ha_base_url,
        "HA_TOKEN_URL": f"{ha_base_url}/auth/token" if ha_base_url else "",
        "HA_COMMAND_SERVICE": "mqtt_bridge.handle_command",
    }
    if access_token:
        managed["HA_ACCESS_TOKEN"] = access_token
    return managed


def _runtime_missing_fields(env_values: dict[str, str]) -> list[str]:
    """Return runtime prerequisites that are still missing."""
    missing: list[str] = []
    if not env_values.get("HOME_MAC"):
        missing.append("device_id")
    if not env_values.get("MQTT_TOKEN_API"):
        missing.append("backend_base_url")
    if not env_values.get("HA_BASE_URL"):
        missing.append("ha_base_url")
    has_access_token = bool(env_values.get("HA_ACCESS_TOKEN"))
    has_refresh_pair = bool(env_values.get("HA_REFRESH_TOKEN")) and bool(
        env_values.get("HA_CLIENT_ID")
    )
    if not has_access_token and not has_refresh_pair:
        missing.append("ha_access_token")
    return missing


def _render_restart_script() -> str:
    """Render a portable restart script for the runtime bridge process."""
    py_exec = shlex.quote(sys.executable)
    return f"""#!/bin/sh
set -eu

RUN_DIR="{RUNTIME_DIR}"
ENV_FILE="$RUN_DIR/bridge.env"
LOG_FILE="$RUN_DIR/bridge.log"
PID_FILE="$RUN_DIR/bridge.pid"
PY={py_exec}

if [ ! -x "$PY" ]; then
  PY="$(command -v python3 || command -v python || true)"
fi
if [ -z "$PY" ]; then
  echo "找不到 Python 可执行文件"
  exit 1
fi
if [ ! -f "$RUN_DIR/bridge.py" ]; then
  echo "找不到 bridge.py: $RUN_DIR/bridge.py"
  exit 1
fi
if [ ! -f "$ENV_FILE" ]; then
  echo "找不到 bridge.env: $ENV_FILE"
  exit 1
fi

if [ -f "$PID_FILE" ]; then
  PID="$(cat "$PID_FILE" 2>/dev/null || true)"
  if [ -n "$PID" ]; then
    kill "$PID" 2>/dev/null || true
  fi
  rm -f "$PID_FILE"
fi
pkill -f "$RUN_DIR/bridge.py" 2>/dev/null || true

set -a
. "$ENV_FILE"
set +a

nohup "$PY" "$RUN_DIR/bridge.py" >> "$LOG_FILE" 2>&1 &
PID="$!"
echo "$PID" > "$PID_FILE"

sleep 2
echo "PID=$PID"
tail -n 20 "$LOG_FILE" 2>/dev/null || true
"""


def initialize_runtime_environment(
    config: dict[str, Any],
    *,
    start_immediately: bool = False,
) -> dict[str, Any]:
    """Create runtime files under /config and optionally start the bridge."""
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    RUNTIME_CONTROL_DIR.mkdir(parents=True, exist_ok=True)

    bridge_asset = _asset_path("bridge.py")
    env_asset = _asset_path("bridge.env.example")

    if bridge_asset.exists():
        (RUNTIME_DIR / "bridge.py").write_text(
            bridge_asset.read_text(encoding="utf-8"),
            encoding="utf-8",
        )

    base_env = _read_env_map(env_asset)
    existing_env = _read_env_map(RUNTIME_ENV_PATH)
    merged_env = dict(base_env)
    merged_env.update(existing_env)
    merged_env.update(_build_managed_env(config))
    _write_env_map(RUNTIME_ENV_PATH, merged_env)
    _write_env_map(RUNTIME_ENV_EXAMPLE_PATH, base_env)

    RUNTIME_RESTART_SCRIPT_PATH.write_text(
        _render_restart_script(),
        encoding="utf-8",
    )
    RUNTIME_RESTART_SCRIPT_PATH.chmod(
        RUNTIME_RESTART_SCRIPT_PATH.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
    )

    missing = _runtime_missing_fields(merged_env)
    result: dict[str, Any] = {
        "runtime_dir": str(RUNTIME_DIR),
        "bridge_path": str(RUNTIME_DIR / "bridge.py"),
        "env_path": str(RUNTIME_ENV_PATH),
        "env_example_path": str(RUNTIME_ENV_EXAMPLE_PATH),
        "restart_script_path": str(RUNTIME_RESTART_SCRIPT_PATH),
        "missing_fields": missing,
        "start_requested": start_immediately,
    }

    if start_immediately and not missing:
        completed = subprocess.run(
            ["sh", str(RUNTIME_RESTART_SCRIPT_PATH)],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        result["start_result"] = {
            "returncode": completed.returncode,
            "stdout": completed.stdout.strip(),
            "stderr": completed.stderr.strip(),
        }
    return result


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
