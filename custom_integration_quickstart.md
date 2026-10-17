# MQTT Bridge 标准版快速初始化

这份文档只讲最短路径，目标是让你在一台新的 HAOS 上尽快跑起来。

## 1. 安装到 HAOS

在 Mac 本地执行：

```bash
cd "/Users/chengzhiguang/workspace/huaweiqi/haos/mqtt-bridge-standard"
VM_HOST=192.168.64.16 VM_PASS='你的密码' bash install.sh
```

安装完成后，HAOS 上会出现：

```text
/config/custom_components/mqtt_bridge
/config/mqtt_bridge_runtime/bridge.py
/config/mqtt_bridge_runtime/bridge.env
/config/mqtt_bridge_runtime/bridge.env.example
/config/mqtt_bridge_runtime/ha_restart_bridge.sh
```

## 2. 在 HA 页面添加集成

进入：

```text
设置 -> 设备与服务 -> 添加集成
```

搜索：

```text
MQTT Bridge
```

建议填写：

- 名称：`MQTT Bridge`
- 设备 ID：例如 `D69512C45225`
- 后台基础地址：例如 `http://192.168.64.1:8082`
- HA 基础地址：例如 `http://192.168.64.16`
- HA 长期访问令牌：在 HA 个人资料页最下方生成

## 3. 初始化 bridge.env

SSH 到 HAOS：

```bash
ssh root@192.168.64.16
vi /config/mqtt_bridge_runtime/bridge.env
```

推荐最小配置：

```ini
MQTT_TOKEN_API=http://192.168.64.1:8082/api/device/mqtt/token
HOME_MAC=D69512C45225
COMMAND_DISPATCH_MODE=ha_service
HA_BASE_URL=http://192.168.64.16
HA_ACCESS_TOKEN=这里填 HA 长期访问令牌
HA_TOKEN_URL=http://192.168.64.16/auth/token
HA_COMMAND_SERVICE=mqtt_bridge.handle_command
LOCAL_MQTT_HOST=core-mosquitto.local.hass.io
LOCAL_MQTT_PORT=1883
LOCAL_MQTT_USER=mqtt_bridge
LOCAL_MQTT_PASS=MqttBridge@2026
```

说明：
- 推荐使用 `ha_service`
- 这样 bridge 不再依赖本地 MQTT 执行命令
- 本地 MQTT 只保留为兼容信息，不是主执行通道

## 4. 启动 bridge

```bash
sh /config/mqtt_bridge_runtime/ha_restart_bridge.sh
```

## 5. 页面验证

进入 `MQTT Bridge` 设备页，确认能看到：

- `完整重初始化`
- `刷新云 MQTT 信息`
- `运行状态`
- `最后错误`
- `最近日志`
- `公网 MQTT 信息`
- `本地 MQTT 信息`

其中：
- `运行状态 = connected` 代表 bridge 已连通
- `公网 MQTT 信息` 里能看到地址、账号、topic
- `本地 MQTT 信息` 在 `ha_service` 模式下显示“当前未启用”是正常的
- 如果你是 HACS 安装的版本，先点一次 `初始化运行时` 再看后续状态会更顺

## 6. 终端验证

看日志：

```bash
tail -f /config/mqtt_bridge_runtime/bridge.log
```

看状态：

```bash
cat /config/mqtt_bridge_runtime/runtime_status.json
```

你应该至少能看到：

- `阿里云 MQTT 连接成功`
- `阿里云已订阅`
- `心跳已发送`

## 7. 常用操作

完整重初始化：

```text
HA 页面 -> MQTT Bridge -> 完整重初始化
```

查看最近日志：

```text
HA 页面 -> MQTT Bridge -> 最近日志
```

刷新云 MQTT 信息：

```text
HA 页面 -> MQTT Bridge -> 刷新云 MQTT 信息
```

初始化运行时：

```text
HA 页面 -> MQTT Bridge -> 初始化运行时
```

## 8. 一句话判断是否成功

只要这三个条件同时成立，就说明标准版已经跑起来了：

1. HA 页面能看到 `MQTT Bridge` 设备
2. `运行状态 = connected`
3. `/config/mqtt_bridge_runtime/bridge.log` 持续有心跳日志
