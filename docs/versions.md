# 兼容版本与下载

## 当前安装包

官方 **0.1.13 · Apple Silicon Mac 测试版** 已单独提供。[下载、安装和限制](install-console.md)。已完成实际安装、USB 认证及响应式工作区验收；没有新增 Windows/Intel Mac 包，也未将完整扩展实体触发或固件更新验收标记为通过。

## 源码开发包

| 项目 | 版本 / 范围 |
|---|---|
| 开发包 | `v0.1.0-preview.4`，非商业源码开放预发布 |
| 社区控制台源码 | 基于 Console `0.1.13`，公开适配版 BORING Console Community |
| 来源提交 | `8a243971a10a3bd6cdf985f2e800e61e072dc0b7` |
| Python SDK / 本地扩展 API | `1.0.0` / `1.0` |
| 扩展 manifest / 设备事件 schema | `1` |
| WMP1 线协议 / 配置 Schema | `1.0` / `1` |
| Python | 声明支持 `>=3.12,<3.14`；实际验证环境见发布验证 |
| PySide6 | `6.11.1` |
| 固件参考 | Power V2 / `0.3.0-alpha.1` / `20260910.07-g7989ccbf-dirty` / `sample-verified` |

## 源码、宿主与固件

- 本轮公开控制台与官方 0.1.13 同源，保留系统字体、独立应用标识及配置目录等公开分发适配。来源与差异见 [控制台源码说明](console-source.md)。
- 可使用本仓库源码运行社区控制台，也可使用兼容官方宿主。SDK wheel 单独安装不会启动宿主或 Runner。
- 源码开发包不附 DMG / EXE 或固件镜像；官方 0.1.13 安装交付包单独下载，`.07` 仍通过官方维护方式获取。`sample-verified` 表示已有范围内的留样机验证，不是全部硬件和场景的无条件稳定承诺。
- 使用扩展实体触发需要设备支持提示词存储与 USB 提示词事件，槽位有内容且已有实体映射，并能建立控制台接受的设备会话。
- SDK 本地 API 与设备 USB / BLE 传输是不同层。BLE 配置协议存在不等于扩展已经通过 BLE 实体触发验收。

## 验证范围

本轮社区控制台源码测试、独立构建、启动与扩展示例验证以 [preview.4 发布验证记录](verification.md) 为准。正在验证或没有覆盖的组合保留明确限制，不沿用旧轮次测试数量作为本轮结果。

历史 0.1.6 官方安装版已在内部 Apple Silicon Mac / Power V2 完成普通 USB / BLE 连接、USB `.16 → .07` 升级、自动回连、配置保留及窗口／菜单验收。这是官方安装版历史结果，不能替代本次社区源码适配或三个扩展示例的验收。

Windows、Linux、Python 3.13、干净电脑安装、BLE 扩展实体触发，以及全部历史固件组合不作为本次默认已验收范围。具体新增结果仅以发布验证记录为准。

## 下载

[本次 Release](https://github.com/JoeySystem/boring-developer-kit/releases/tag/v0.1.0-preview.4) 提供完整开发包、SDK wheel 和三个独立扩展 ZIP。完整开发包包含 `console/` 源码；SDK wheel 不包含控制台或 Qt 运行库。GitHub 自动生成的 Source code ZIP 是仓库源码，不是可导入扩展，也不是控制台安装包。

安装与构建入口见 [开始使用](../START-HERE.md)。固件需要更新时使用官方维护流程；开发扩展不需要重新预置设备身份或写 eFuse。
