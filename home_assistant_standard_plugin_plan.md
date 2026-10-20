# MQTT Bridge 标准插件化方案

## 一句话核心结论

这套 `/haos/mqtt-bridge` 现在本质上是“独立 Python 守护进程 + HA 自动化 + 直接改 HA 文件”的混合方案。
如果要做成 **Home Assistant 标准插件方式**，推荐拆成：

1. **Custom Integration（HACS 安装）**：负责 HA 内的设备模型、服务、场景、配置流、状态同步
2. **Add-on（可选，但强烈建议）**：负责公网 MQTT 长连接、凭证刷新、消息桥接守护进程

最稳的标准形态不是“只有一个插件”，而是 **Integration + Add-on** 两层。

---

## 先把概念说清楚

### HACS 适合什么

HACS 主要分发这些内容：

- Custom Integration
- Lovelace Card
- Theme

HACS **不是** Add-on 的标准分发方式。

### Add-on 适合什么

Add-on 适合：

- 长时间运行的后台进程
- 独立依赖环境
- 外网连接、凭证、桥接守护

你现在的 `bridge.py` 明显更像一个 Add-on，而不是单纯的 HACS 集成。

---

## 现状拆解

当前实现分成三块：

### 1. `bridge.py`

作用：

- 调 Java HTTP 接口拿公网 MQTT 凭证
- 连接阿里云 MQTT
- 连接本地 Mosquitto
- 双向转发消息
- 定时心跳
- 读取 HA registry 补充设备信息

当前问题：

- 通过 `docker exec homeassistant cat /config/.storage/...` 读 HA 内部文件，不是标准 HA 接口
- 自己维护线程、日志、生命周期，和 HA 本体解耦过深
- 配置通过环境变量注入，缺少标准配置流

### 2. `automations.yaml`

作用：

- 监听本地 MQTT 指令
- 调 HA service
- 拼回执消息
- 设备列表、场景列表、场景激活/创建/更新/删除

当前问题：

- 业务逻辑散落在自动化里，难测试、难版本化
- 任何功能扩展都要改 YAML，维护成本高

### 3. `scene_manager.py`

作用：

- 直接操作 `scenes.yaml` / `.storage`
- 实现场景 CRUD 和启停

当前问题：

- 直接写 HA 配置和 `.storage`，侵入性强
- 不属于标准的集成能力边界

---

## 目标架构

推荐落成下面这个结构：

```text
repo/
├── custom_components/
│   └── mqtt_bridge/
│       ├── __init__.py
│       ├── manifest.json
│       ├── config_flow.py
│       ├── const.py
│       ├── coordinator.py
│       ├── services.yaml
│       ├── mqtt_router.py
│       ├── device_registry.py
│       ├── scene_store.py
│       ├── scene.py
│       ├── websocket_api.py
│       └── translations/
├── addon/
│   └── mqtt_bridge_gateway/
│       ├── config.yaml
│       ├── Dockerfile
│       ├── run.sh
│       └── app/
│           └── bridge_daemon.py
├── hacs.json
└── README.md
```

当前工作区里，第一版 custom integration 源码已经放在：

```text
/Users/chengzhiguang/workspace/huaweiqi/haos/mqtt-bridge/custom_components/mqtt_bridge
```

而 HACS 仓库标记文件还在：

```text
/Users/chengzhiguang/workspace/huaweiqi/haos/mqtt-bridge/hacs.json
```

---

## 推荐职责划分

## A. Custom Integration：负责 HA 内业务

Domain 建议：`mqtt_bridge`

Integration 负责：

- 配置流
- 保存桥接配置
- 设备/实体信息获取
- 指令路由
- HA service 注册
- 场景数据管理
- 对外统一发布状态和通知
- 提供 WebSocket / REST API（可选）

### 应注册的 HA services

建议至少提供这些服务：

- `mqtt_bridge.get_devices`
- `mqtt_bridge.get_device`
- `mqtt_bridge.call_service`
- `mqtt_bridge.list_scenes`
- `mqtt_bridge.activate_scene`
- `mqtt_bridge.create_scene`
- `mqtt_bridge.update_scene`
- `mqtt_bridge.delete_scene`
- `mqtt_bridge.enable_scene`
- `mqtt_bridge.disable_scene`
- `mqtt_bridge.publish_status`
- `mqtt_bridge.publish_notify`

这样可以替代现在 `automations.yaml` 里大部分逻辑。

### 场景应该怎么做

不要再直接改 `scenes.yaml` 和 `.storage`。

更标准的做法是：

- 用 integration 自己的 storage 保存场景定义
- 自己实现 `scene` 平台实体
- 激活时调用实体内部逻辑或转发 HA service

也就是把“桥接场景”变成你自己的标准实体，而不是去写 HA 核心文件。

### 设备信息应该怎么拿

不要再 `docker exec` 读 registry 文件。

在 integration 内可以直接使用 HA 提供的 registry API：

- `entity_registry`
- `device_registry`
- `area_registry`

这才是标准方式。

---

## B. Add-on：负责公网 MQTT 守护进程

Add-on 负责：

- 调外部 Java API 获取 MQTT token
- 连接公网 MQTT Broker
- 维持长连接
- 定时刷新凭证
- 心跳上报
- 收到下行指令后转发给 HA
- 接收 HA 的上行状态/通知后发往公网 MQTT

### Add-on 和 Integration 怎么通信

优先级建议：

1. **首选：HA WebSocket / REST API**
2. 次选：Supervisor Ingress / 本地 HTTP
3. 兼容保留：本地 MQTT

如果你要“标准插件化”，建议逐步从“本地 MQTT + 自动化”迁到：

- Add-on 收公网消息
- 通过 HA API 调 `mqtt_bridge.*` services
- Integration 处理 HA 内逻辑
- Integration 再把执行结果交给 Add-on 上报

### 为什么不建议继续把本地 MQTT 当主干

本地 MQTT 不是不能用，而是：

- 命令路由会分散在自动化和脚本里
- 回执链条不透明
- 场景和设备信息很难统一建模

保留本地 MQTT 作为兼容层可以，但不建议继续作为核心业务面。

---

## 最推荐的两阶段落地路线

## 第一阶段：先做成一个完整的 HACS Custom Integration

目标：先把 `automations.yaml + scene_manager.py` 收进 integration。

保留现有 `bridge.py` 守护进程，但改成只做纯桥接：

- 外网 MQTT 下行 -> 投递给 HA
- HA 执行结果 -> 外网 MQTT 上报

这一阶段先不碰公网连接方式，先把 HA 内逻辑标准化。

### 第一阶段改造结果

旧：

- `bridge.py`
- `automations.yaml`
- `scene_manager.py`

新：

- `bridge.py` 只做外网桥接
- `custom_components/mqtt_bridge/` 承担设备/指令/场景逻辑

这是投入产出比最高的一步。

## 第二阶段：再把 `bridge.py` 收成 Add-on

目标：把 systemd 独立脚本改成标准 Add-on。

旧：

- `install.sh`
- `systemd service`
- `/opt/mqtt-bridge/bridge.env`

新：

- `addon/mqtt_bridge_gateway/config.yaml`
- `Dockerfile`
- `run.sh`
- Add-on options schema

这样后续安装方式就变成：

- HA 内通过 HACS 安装 `mqtt_bridge` integration
- HA Add-on Store 安装 `mqtt_bridge_gateway` add-on

这才是标准 Home Assistant 风格。

---

## 指令链路建议

建议统一消息模型：

```json
{
  "traceId": "uuid",
  "cmd": "get_devices",
  "type": "command",
  "gatewayId": "D69512C45225",
  "deviceId": "light.living_room",
  "time": 1760000000,
  "data": {}
}
```

统一返回：

```json
{
  "traceId": "uuid",
  "cmd": "get_devices",
  "type": "notify",
  "gatewayId": "D69512C45225",
  "deviceId": "D69512C45225",
  "time": 1760000001,
  "data": {
    "status": "ok",
    "devices": []
  }
}
```

建议在 integration 内统一做：

- 校验
- 路由
- 错误码封装
- 响应格式

这样 Add-on 和公网平台只管传输，不管业务。

---

## 旧实现到新实现的映射

### `get_devices / get_device`

旧方式：

- MQTT 自动化拼 Jinja
- 再由 `bridge.py` 补 registry 信息

新方式：

- integration service 直接查 HA registry
- 返回标准 JSON

### `service_call`

旧方式：

- MQTT 自动化转 `homeassistant.turn_on` 等 service

新方式：

- integration service `call_service`
- 统一校验 `domain/service/entity_id/service_data`

### `scene CRUD`

旧方式：

- `scene_manager.py` 改 YAML 和 `.storage`

新方式：

- integration 自己维护 storage
- 暴露 `scene` 实体
- 提供标准 services

### 心跳/在线状态

旧方式：

- `bridge.py` 定时发 status

新方式：

- Add-on 保留
- 但状态结构由 integration 定义

---

## 为什么这套更标准

因为它满足了这几个 Home Assistant 生态里的基本原则：

1. **HA 内逻辑放在 integration**
2. **长期后台进程放在 add-on**
3. **配置通过 config flow / add-on options 管理**
4. **不要直接改 HA 核心 storage 文件**
5. **优先用 HA 官方 registry / service / websocket API**

---

## 建议的 MVP

如果现在就开始做，先不要一次性全改完。

### MVP 第 1 步

先做 `custom_components/mqtt_bridge`，只实现：

- config flow
- `get_devices`
- `call_service`
- `list_scenes`
- `activate_scene`

### MVP 第 2 步

再把场景 CRUD 收进 integration storage：

- `create_scene`
- `update_scene`
- `delete_scene`
- `enable_scene`
- `disable_scene`

### MVP 第 3 步

最后再做 Add-on 化：

- 用容器替代 systemd
- 用 add-on 配置替代 env 文件

---

## 仓库分发建议

如果你希望“标准插件安装”体验，建议拆成两个仓库，或者一个 monorepo 两个入口：

### 仓库 A：HACS 集成

- 仓库名：`ha-mqtt-bridge-integration`
- 内容：`custom_components/mqtt_bridge`
- 安装方式：HACS 自定义仓库

### 仓库 B：Add-on 仓库

- 仓库名：`ha-mqtt-bridge-addon`
- 内容：`addon/mqtt_bridge_gateway`
- 安装方式：Add-on 仓库

如果你希望统一维护，也可以 monorepo，但要在 README 里明确：

- HACS 装哪个目录
- Add-on 仓库指向哪个目录

---

## 对你现在这套代码的直接建议

### 建议保留

- `bridge.py` 的公网 MQTT 凭证获取逻辑
- `bridge.py` 的凭证刷新、心跳、双向连接能力
- Topic 规范

### 建议优先迁走

- `automations.yaml`
- `scene_manager.py`
- `docker exec cat /config/.storage/...`

### 推荐新边界

- Add-on：只做“连接”和“转发”
- Integration：只做“模型”和“执行”

---

## 最终建议

如果目标是“标准 Home Assistant 插件”，不要把现在的目录直接硬套成 HACS 插件。  
正确答案是：

**先把 HA 内业务做成 Custom Integration，再把桥接守护做成 Add-on。**

这是最稳定、最可维护、最像 HA 原生生态的实现。

---

## 下一步建议

下一步最值得直接开工的是：

1. 先生成 `custom_components/mqtt_bridge/` 骨架
2. 先把 `get_devices` 和 `call_service` 从 `automations.yaml` 迁进去
3. 再决定是否马上把 `bridge.py` 改成 Add-on

如果继续做实现，推荐优先顺序是：

1. `manifest.json`
2. `config_flow.py`
3. `services.yaml`
4. `__init__.py` 中注册 services
5. `device_registry.py`
6. `scene_store.py`
