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
    if grep -q '^HA_REFRESH_TOKEN=$' '$REMOTE_ENV_FILE'; then
      echo missing
    elif grep -q '^HA_CLIENT_ID=$' '$REMOTE_ENV_FILE'; then
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
  ssh_exec "sh '$REMOTE_RESTART_SCRIPT'"
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
   - HA_REFRESH_TOKEN
   - HA_CLIENT_ID
4. 启动 bridge
   sh $REMOTE_RESTART_SCRIPT
EOF
fi
