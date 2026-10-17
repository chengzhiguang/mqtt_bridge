# MQTT Bridge 初始化清单

这份清单面向第一次安装或迁移到新 HAOS 的场景。

## 一、安装前确认

- Mac 本机可以访问目标 HAOS
- Java 后台已启动，`MQTT_TOKEN_API` 可访问
- HAOS 已正常启动并可以打开 Web 页面
- `Terminal & SSH` 已安装
- 已准备好 HA 的 `refresh_token` 和 `client_id`

## 二、执行安装

在 Mac 本地执行：

```bash
cd "/Users/chengzhiguang/workspace/huaweiqi/haos/mqtt-bridge-standard"
VM_HOST=192.168.64.16 VM_PASS='你的密码' bash install.sh
```

安装完成后，确认远端目录存在：

- `/config/custom_components/mqtt_bridge`
- `/config/mqtt_bridge_runtime/bridge.py`
- `/config/mqtt_bridge_runtime/bridge.env`
- `/config/mqtt_bridge_runtime/ha_restart_bridge.sh`

如果 `bridge.env` 里的必填项已经填好，`install.sh` 会继续自动做 3 个检查：

- bridge 进程是否已启动
- 阿里云 MQTT 是否已连接
- 首个心跳是否已经上报，并显示 `last_ha_alive`

## 三、HA 页面初始化

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

## 四、bridge.env 必填项

编辑：

```bash
vi /config/mqtt_bridge_runtime/bridge.env
```

至少确认这些字段已经填好：

- `MQTT_TOKEN_API`
- `COMMAND_DISPATCH_MODE=ha_service`
- `HA_BASE_URL`
- `HA_ACCESS_TOKEN`
- `HA_TOKEN_URL`
- `HA_COMMAND_SERVICE=mqtt_bridge.handle_command`

## 五、启动与验证

启动 bridge：

```bash
sh /config/mqtt_bridge_runtime/ha_restart_bridge.sh
```

终端验证：

- `tail -f /config/mqtt_bridge_runtime/bridge.log`
- `cat /config/mqtt_bridge_runtime/runtime_status.json`

关键日志应包含：

- `获取凭证成功`
- `阿里云 MQTT 连接成功`
- `阿里云已订阅`
- `心跳已发送`
- `心跳已发送 ha_alive=True`

## 六、页面验收

进入 `MQTT Bridge` 设备页，确认能看到：

- `初始化运行时`
- `完整重初始化`
- `刷新云 MQTT 信息`
- `运行状态`
- `最后错误`
- `最近日志`
- `公网 MQTT 信息`
- `本地 MQTT 信息`

其中重点看：

- `运行状态 = connected`
- `公网 MQTT 信息` 能看到 `endpoint / username / topic`
- `本地 MQTT 信息` 在 `ha_service` 模式下显示“当前未启用”是正常的

如果是通过 HACS 安装的集成，建议先点一次 `初始化运行时`，让集成自动生成 `/config/mqtt_bridge_runtime` 下的运行时文件，再继续启动或重初始化。

## 七、联调检查

后端下发一条 `call_service` 后，应至少看到：

- 一条 `executing`
- 一条最终 `ok` 或 `error`

如果失败，优先检查：

- `最近日志`
- `/config/mqtt_bridge_runtime/bridge.log`
- `runtime_status.json`

## 八、一句话验收标准

满足下面 4 条，就算初始化完成：

1. HA 页面能添加并打开 `MQTT Bridge`
2. `运行状态 = connected`
3. `bridge.log` 持续有心跳
4. 后端下发指令能收到执行中和最终结果
