# Changelog

## 0.3.0

- 配置流改为精简安装表单，只保留设备 ID、后台基础地址、HA 基础地址和 HA 长期访问令牌
- 新增选项页，可在安装后直接从 HA 页面修改上述配置
- “刷新云 MQTT 信息”按钮改为优先使用集成里保存的 `token_api_url`
- `bridge.env.example` 与初始化文档改为优先使用 `HA_ACCESS_TOKEN`
- 把 `bridge.py` 等运行时资产打包进集成目录，并新增 `初始化运行时` 按钮/service

## 0.2.1

- 修复心跳中的 HA 存活探测，优先基于 `HA_BASE_URL` 检查，避免误报 `ha_alive=false`
- 运行状态新增 `last_ha_alive`，便于安装后直接确认 HA 健康状态
- `install.sh` 新增安装后自动检查：bridge 进程、阿里云 MQTT 连接、首个心跳

## 0.2.0

- 新建 `mqtt-bridge-standard` 目录，和原 `mqtt-bridge` 并行维护
- 补齐 HAOS 版一键安装脚本 `install.sh`
- 补齐 `bridge.env.example` 和 `ha_restart_bridge.sh`
- 统一 README 和快速初始化文档入口
- 新增 `INIT_CHECKLIST.md` 初始化清单
- 新增 `docs/assets/README.md` 截图资源说明
- 集成页补齐运行状态、日志、公网 MQTT、本地 MQTT 信息和操作按钮
- 将文档入口和 manifest 指向标准版目录
