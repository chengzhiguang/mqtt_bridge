# MQTT Bridge Standard

这是 `mqtt-bridge` 的标准化整理版目录。

目标很明确：
- 安装简单
- 初始化步骤固定
- 文档入口统一
- 原来的 `haos/mqtt-bridge` 保持不动，继续作为现网基线

## 目录说明

```text
mqtt-bridge-standard/
├── custom_components/mqtt_bridge   # HA 自定义集成
├── bridge.py                       # 公网 MQTT <-> HA 桥接守护进程
├── install.sh                      # HAOS 一键安装脚本
├── bridge.env.example              # bridge 运行配置模板
├── ha_restart_bridge.sh            # HAOS 上重启 bridge 的标准脚本
├── README.md                       # 当前总入口
└── custom_integration_quickstart.md
```

## 推荐架构

当前推荐模式是 `ha_service`：

```text
公网 MQTT
  -> bridge.py
  -> mqtt_bridge.handle_command
  -> Home Assistant service
  -> bridge.py 回公网
```

这个模式下：
- 不依赖本地 MQTT 作为命令执行通道
- Zigbee / Wi-Fi / Matter 设备都统一走 HA 实体与 service
- 页面里可以直接看到日志、运行状态、公网 MQTT 信息、重初始化按钮

## 一键安装

在 Mac 本地执行：

```bash
cd "/Users/chengzhiguang/workspace/huaweiqi/haos/mqtt-bridge-standard"
VM_HOST=192.168.64.16 VM_PASS='你的密码' bash install.sh
```

脚本会自动完成：
1. 复制 `custom_components/mqtt_bridge` 到 HAOS
2. 复制 `bridge.py` 到 `/config/mqtt_bridge_runtime`
3. 初始化 `venv`
4. 安装 `paho-mqtt`
5. 下发 `bridge.env.example`
6. 重启 HA Core
7. 如果 `bridge.env` 已填完，就自动启动 bridge
8. 自动检查 bridge 进程是否已启动
9. 自动检查阿里云 MQTT 是否已连接
10. 自动等待首个心跳，并输出 `last_ha_alive`

## 安装界面

推荐在开源仓库里优先放一张设备页截图，便于别人第一眼理解装好后的结果。

截图建议位置：

- `docs/assets/install-device-page.png`

建议截图内容：

- `MQTT Bridge` 设备页
- `初始化运行时`
- `完整重初始化`
- `刷新云 MQTT 信息`
- `运行状态`
- `最近日志`
- `公网 MQTT 信息`
- `本地 MQTT 信息`

截图资源说明见：

[docs/assets/README.md](file:///Users/chengzhiguang/workspace/huaweiqi/haos/mqtt-bridge-standard/docs/assets/README.md)

## HACS 自定义仓库

如果你想把它作为 HACS 自定义仓库接入，使用当前仓库地址：

- 仓库：`https://github.com/chengzhiguang/mqtt_bridge.git`
- 类型：`Integration`

当前目录已经带了：

- `hacs.json`
- `custom_components/mqtt_bridge/manifest.json`
- 可直接渲染的 `README.md`
- 明确版本号 `0.3.0`
- `CHANGELOG.md`
- `RELEASE_NOTES.md`

## 初始化入口

详细步骤看：

[custom_integration_quickstart.md](file:///Users/chengzhiguang/workspace/huaweiqi/haos/mqtt-bridge-standard/custom_integration_quickstart.md)
[INIT_CHECKLIST.md](file:///Users/chengzhiguang/workspace/huaweiqi/haos/mqtt-bridge-standard/INIT_CHECKLIST.md)

你主要只需要关注两件事：

1. HA 页面里添加 `MQTT Bridge` 集成
2. 在配置表单里填好这 4 个核心字段：
   - `设备 ID`
   - `后台基础地址`
   - `HA 基础地址`
   - `HA 长期访问令牌`
3. 再补齐 `/config/mqtt_bridge_runtime/bridge.env`

## 安装后验证

HA 页面里验证：

1. `设置 -> 设备与服务 -> 添加集成`
2. 搜索 `MQTT Bridge`
3. 添加后进入设备页
4. 你应该能看到：
   - `初始化运行时`
   - `完整重初始化`
   - `刷新云 MQTT 信息`
   - `运行状态`
   - `最后错误`
   - `最近日志`
   - `公网 MQTT 信息`
   - `本地 MQTT 信息`

终端验证：

```bash
ssh root@<HAOS_IP>
tail -f /config/mqtt_bridge_runtime/bridge.log
cat /config/mqtt_bridge_runtime/runtime_status.json
```

如果使用 `install.sh` 且 `bridge.env` 已经填好，安装脚本本身就会直接给出这三项结果：

- bridge 进程是否启动成功
- 阿里云 MQTT 是否已连接
- 首个心跳是否已上报，以及 `last_ha_alive` 当前值

如果使用 HACS 安装集成，建议先进入设备页点击一次 `初始化运行时`，让集成把 `bridge.py`、`bridge.env` 和重启脚本写到 `/config/mqtt_bridge_runtime`。

## 日常运维

重启 bridge：

```bash
sh /config/mqtt_bridge_runtime/ha_restart_bridge.sh
```

看日志：

```bash
tail -f /config/mqtt_bridge_runtime/bridge.log
```

看运行状态：

```bash
cat /config/mqtt_bridge_runtime/runtime_status.json
```

## 当前状态

这个目录已经是新的整理入口，后续的标准化工作都建议在这里继续做，不再直接改原始 `haos/mqtt-bridge`。
# mqtt_bridge
