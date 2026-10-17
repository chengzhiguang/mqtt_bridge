#!/usr/bin/env bash
#
# HAOS 一键安装脚本
# 作用：
# 1. 部署 custom_components/mqtt_bridge
# 2. 部署 bridge.py / ha_restart_bridge.sh
# 3. 初始化 /config/mqtt_bridge_runtime/venv
# 4. 自动准备 bridge.env（缺失时从 example 生成）
# 5. 重启 HA Core，并在配置完整时启动 bridge.py

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VM_HOST="${VM_HOST:-}"
VM_USER="${VM_USER:-root}"
VM_PASS="${VM_PASS:-}"

REMOTE_CONFIG_DIR="/config"
REMOTE_COMPONENT_DIR="$REMOTE_CONFIG_DIR/custom_components"
REMOTE_DOMAIN_DIR="$REMOTE_COMPONENT_DIR/mqtt_bridge"
REMOTE_RUN_DIR="$REMOTE_CONFIG_DIR/mqtt_bridge_runtime"
REMOTE_ENV_FILE="$REMOTE_RUN_DIR/bridge.env"
REMOTE_ENV_EXAMPLE="$REMOTE_RUN_DIR/bridge.env.example"
REMOTE_RESTART_SCRIPT="$REMOTE_RUN_DIR/ha_restart_bridge.sh"

if ! command -v sshpass >/dev/null 2>&1; then
  echo "缺少 sshpass，请先安装：brew install sshpass"
  exit 1
fi

if [ -z "$VM_HOST" ]; then
  echo "请传入 VM_HOST，例如：VM_HOST=192.168.64.16 VM_PASS='你的密码' bash install.sh"
  exit 1
fi

if [ -z "$VM_PASS" ]; then
  echo "请传入 VM_PASS，例如：VM_HOST=192.168.64.16 VM_PASS='你的密码' bash install.sh"
  exit 1
fi

ssh_exec() {
  sshpass -p "$VM_PASS" ssh \
    -o StrictHostKeyChecking=no \
    -o UserKnownHostsFile=/dev/null \
    "$VM_USER@$VM_HOST" "$@"
}

scp_copy() {
  sshpass -p "$VM_PASS" scp \
    -o StrictHostKeyChecking=no \
    -o UserKnownHostsFile=/dev/null \
    "$@"
}

print_remote_status_summary() {
  ssh_exec "python3 - '$REMOTE_RUN_DIR' <<'PY'
import json
import os
import sys

run_dir = sys.argv[1]
status_path = os.path.join(run_dir, 'runtime_status.json')
if not os.path.exists(status_path):
    print('runtime_status.json: missing')
    sys.exit(0)

with open(status_path, encoding='utf-8') as f:
    data = json.load(f)

summary = {
    'status': data.get('status'),
    'aliyun_connected': data.get('aliyun_connected'),
    'last_heartbeat_at': data.get('last_heartbeat_at'),
    'last_ha_alive': data.get('last_ha_alive'),
    'last_error': data.get('last_error'),
    'last_exception': data.get('last_exception'),
}
print(json.dumps(summary, ensure_ascii=False))
PY"
}

wait_for_remote_status() {
  local phase="$1"
  local start_ts="$2"
  local timeout="$3"
  ssh_exec "python3 - '$REMOTE_RUN_DIR' '$phase' '$start_ts' '$timeout' <<'PY'
import json
import os
import sys
import time

run_dir = sys.argv[1]
phase = sys.argv[2]
start_ts = int(sys.argv[3])
timeout = int(sys.argv[4])
status_path = os.path.join(run_dir, 'runtime_status.json')
deadline = time.time() + timeout
last_data = None

while time.time() < deadline:
    if os.path.exists(status_path):
        try:
            with open(status_path, encoding='utf-8') as f:
                last_data = json.load(f)
        except Exception:
            last_data = None
    if last_data:
        if phase == 'aliyun' and bool(last_data.get('aliyun_connected')):
            print(json.dumps({
                'phase': phase,
                'status': last_data.get('status'),
                'aliyun_connected': last_data.get('aliyun_connected'),
                'last_heartbeat_at': last_data.get('last_heartbeat_at'),
                'last_ha_alive': last_data.get('last_ha_alive'),
            }, ensure_ascii=False))
            sys.exit(0)
        heartbeat_at = int(last_data.get('last_heartbeat_at') or 0)
        if phase == 'heartbeat' and heartbeat_at >= start_ts:
            print(json.dumps({
                'phase': phase,
                'status': last_data.get('status'),
                'aliyun_connected': last_data.get('aliyun_connected'),
                'last_heartbeat_at': heartbeat_at,
                'last_ha_alive': last_data.get('last_ha_alive'),
            }, ensure_ascii=False))
            sys.exit(0)
    time.sleep(2)

print(json.dumps({
    'phase': phase,
    'status': (last_data or {}).get('status'),
    'aliyun_connected': (last_data or {}).get('aliyun_connected'),
    'last_heartbeat_at': (last_data or {}).get('last_heartbeat_at'),
    'last_ha_alive': (last_data or {}).get('last_ha_alive'),
    'last_error': (last_data or {}).get('last_error'),
    'last_exception': (last_data or {}).get('last_exception'),
}, ensure_ascii=False))
sys.exit(1)
PY"
}

print_remote_diagnostics() {
  echo ">>> 最近运行状态"
  print_remote_status_summary || true
  echo ">>> 最近日志"
  ssh_exec "tail -n 30 '$REMOTE_RUN_DIR/bridge.log' 2>/dev/null || true"
}

echo ">>> 连接 HAOS: $VM_HOST"
ssh_exec "echo ok" >/dev/null

echo ">>> 准备远端目录"
ssh_exec "mkdir -p '$REMOTE_COMPONENT_DIR' '$REMOTE_RUN_DIR' '$REMOTE_RUN_DIR/control'"

echo ">>> 部署自定义集成"
ssh_exec "rm -rf '$REMOTE_DOMAIN_DIR'"
scp_copy -r "$SCRIPT_DIR/custom_components/mqtt_bridge" "$VM_USER@$VM_HOST:$REMOTE_COMPONENT_DIR/"

echo ">>> 部署 bridge 运行时"
scp_copy "$SCRIPT_DIR/bridge.py" "$VM_USER@$VM_HOST:$REMOTE_RUN_DIR/bridge.py"
scp_copy "$SCRIPT_DIR/ha_restart_bridge.sh" "$VM_USER@$VM_HOST:$REMOTE_RESTART_SCRIPT"
scp_copy "$SCRIPT_DIR/bridge.env.example" "$VM_USER@$VM_HOST:$REMOTE_ENV_EXAMPLE"
ssh_exec "chmod +x '$REMOTE_RESTART_SCRIPT'"

echo ">>> 初始化 Python 运行环境"
ssh_exec "
  set -eu
  if [ ! -x '$REMOTE_RUN_DIR/venv/bin/python' ]; then
    python3 -m venv '$REMOTE_RUN_DIR/venv'
  fi
  '$REMOTE_RUN_DIR/venv/bin/pip' install --upgrade pip >/dev/null
  '$REMOTE_RUN_DIR/venv/bin/pip' install paho-mqtt >/dev/null
"

echo ">>> 准备 bridge.env"
ssh_exec "
  set -eu
  if [ ! -f '$REMOTE_ENV_FILE' ]; then
    cp '$REMOTE_ENV_EXAMPLE' '$REMOTE_ENV_FILE'
  fi
"

echo ">>> 重启 Home Assistant Core"
ssh_exec "ha core restart >/dev/null 2>&1 || true"

echo ">>> 检查 bridge.env 是否已完成初始化"
ENV_STATUS="$(
  ssh_exec "
    set -eu
    ha_access_token=\"\$(grep '^HA_ACCESS_TOKEN=' '$REMOTE_ENV_FILE' | head -n1 | cut -d= -f2-)\"
    ha_refresh_token=\"\$(grep '^HA_REFRESH_TOKEN=' '$REMOTE_ENV_FILE' | head -n1 | cut -d= -f2-)\"
    ha_client_id=\"\$(grep '^HA_CLIENT_ID=' '$REMOTE_ENV_FILE' | head -n1 | cut -d= -f2-)\"
    if [ -z \"\$ha_access_token\" ] && { [ -z \"\$ha_refresh_token\" ] || [ -z \"\$ha_client_id\" ]; }; then
      echo missing
    elif grep -q '^HA_BASE_URL=$' '$REMOTE_ENV_FILE'; then
      echo missing
    elif grep -q '^MQTT_TOKEN_API=$' '$REMOTE_ENV_FILE'; then
      echo missing
    else
      echo ready
    fi
  "
)"

if [ "$ENV_STATUS" = "ready" ]; then
  echo ">>> 启动 bridge 运行时"
  INSTALL_STARTED_AT="$(ssh_exec "date +%s")"
  ssh_exec "sh '$REMOTE_RESTART_SCRIPT'"

  echo ">>> 检查 bridge 进程"
  ssh_exec "pgrep -f '$REMOTE_RUN_DIR/bridge.py' >/dev/null"

  echo ">>> 等待阿里云 MQTT 连接"
  if ! wait_for_remote_status "aliyun" "$INSTALL_STARTED_AT" 60; then
    echo "安装后自动检查失败：阿里云 MQTT 未在预期时间内连上。"
    print_remote_diagnostics
    exit 1
  fi

  echo ">>> 等待首个心跳上报"
  if ! wait_for_remote_status "heartbeat" "$INSTALL_STARTED_AT" 45; then
    echo "安装后自动检查失败：首个心跳未在预期时间内出现。"
    print_remote_diagnostics
    exit 1
  fi

  echo ">>> 安装后自动检查摘要"
  print_remote_status_summary
else
  echo ">>> bridge.env 仍有必填项未填写，先跳过启动"
fi

echo
echo "╔══════════════════════════════════════════════════╗"
echo "║              MQTT Bridge Standard               ║"
echo "║                 安装已完成                      ║"
echo "╚══════════════════════════════════════════════════╝"
echo
echo "远端集成目录: $REMOTE_DOMAIN_DIR"
echo "远端运行目录: $REMOTE_RUN_DIR"
echo "配置文件示例: $REMOTE_ENV_EXAMPLE"
echo "实际配置文件: $REMOTE_ENV_FILE"
echo

if [ "$ENV_STATUS" = "ready" ]; then
  echo "bridge.py 已启动。"
else
  cat <<EOF
bridge.py 尚未启动，因为 bridge.env 还没填完。

下一步：
1. SSH 到 HAOS
   ssh $VM_USER@$VM_HOST
2. 编辑配置
   vi $REMOTE_ENV_FILE
3. 至少补齐这些字段：
   - MQTT_TOKEN_API
   - HA_BASE_URL
   - HA_ACCESS_TOKEN
     或者
   - HA_REFRESH_TOKEN
   - HA_CLIENT_ID
4. 启动 bridge
   sh $REMOTE_RESTART_SCRIPT
EOF
fi
