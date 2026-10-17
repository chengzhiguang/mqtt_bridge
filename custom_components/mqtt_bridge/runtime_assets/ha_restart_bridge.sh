#!/bin/sh
# 在 HAOS 上重启 mqtt-bridge 守护进程（tmux 常驻）
set -eu

RUN_DIR="/config/mqtt_bridge_runtime"
SESSION="mqtt-bridge"
ENV_FILE="$RUN_DIR/bridge.env"
LOG_FILE="$RUN_DIR/bridge.log"
PY="$RUN_DIR/venv/bin/python"

if [ ! -x "$PY" ]; then
  echo "找不到 python: $PY"
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

echo "停止旧进程..."
tmux kill-session -t "$SESSION" 2>/dev/null || true
pkill -f "$RUN_DIR/bridge.py" 2>/dev/null || true
sleep 1

echo "启动新进程..."
START_CMD="set -a; . $ENV_FILE; set +a; exec $PY $RUN_DIR/bridge.py >> $LOG_FILE 2>&1"
tmux new-session -d -s "$SESSION" "bash -lc '$START_CMD'"

sleep 4

echo
echo "── tmux 会话 ──"
tmux ls 2>/dev/null || echo "(无)"
echo
echo "── 进程 ──"
pgrep -f "$RUN_DIR/bridge.py" || echo "(无)"
echo
echo "── 最新日志 ──"
tail -n 20 "$LOG_FILE" 2>/dev/null || echo "(暂无)"
