# MQTT Bridge Standard Release Notes

## v0.2.0

这是标准化目录的第一个可用版本。

核心变化：
- 新建 `mqtt-bridge-standard` 目录，与原始 `mqtt-bridge` 并行维护
- 补齐 HAOS 一键安装脚本 `install.sh`
- 补齐运行配置模板 `bridge.env.example`
- 补齐标准重启脚本 `ha_restart_bridge.sh`
- 设备页支持直接查看：
  - 运行状态
  - 最后错误
  - 最近日志
  - 公网 MQTT 信息
  - 本地 MQTT 信息
- 设备页支持直接操作：
  - 完整重初始化
  - 刷新云 MQTT 信息

推荐使用方式：
- 命令分发模式使用 `ha_service`
- 本地 MQTT 仅作为兼容信息，不作为主执行通道

适用场景：
- 新 HAOS 环境初始化
- 需要简单安装和明确初始化步骤
- 需要在 HA 页面直接看到桥接状态和公网 MQTT 信息
