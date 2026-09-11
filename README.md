# BORING Developer Kit

让用户通过扩展和设备协议，定义自己的 BORING 使用方式。

**当前版本：`v0.1.0-preview.2`，SDK / 协议开发者预发布。** 已提供 Python SDK 1.0.0、三个扩展示例、WMP1 协议与配置 Schema、API 文档、开发包 ZIP 和源码测试。

[开始使用](START-HERE.md) · [下载预发布](https://github.com/JoeySystem/boring-developer-kit/releases/tag/v0.1.0-preview.2) · [兼容与验证边界](docs/versions.md) · [许可](LICENSING.md)

## 本次可用的内容

| 内容 | 入口 |
|---|---|
| Python SDK 源码及安装说明 | [sdk/python](sdk/python/README.md) |
| 三个扩展及预期结果 | [示例说明](examples/README.md) |
| SDK 方法、返回值和错误处理 | [API 参考](docs/api.md) |
| 扩展 manifest、动作和提案 | [扩展接口](docs/extensions.md) |
| 设备事件字段 | [设备事件](docs/device-events.md) |
| USB / BLE WMP1 协议、配置 Schema 和公开测试向量 | [protocol](protocol/README.md) |
| 平台与配套控制台限制 | [兼容说明](docs/versions.md) |
| 修改、导入和重新打包自己的扩展 | [开发流程](docs/development-guide.md) |

## 运行前先确认宿主

SDK 是官方控制台本地 API 的客户端，**不是独立硬件驱动**。运行扩展需要具有扩展 Runner / API 1.0 的 BORING Console，以及控制台接受的设备会话。

本次对齐控制台 0.1.6 与固件 20260910.07，SDK/API 版本保持不变。控制台普通连接与固件更新已有内部安装版验收；三个扩展示例仍未完成安装包到实体触发的联合验收，本仓库不附控制台或固件二进制。安装 SDK 后仍需兼容的控制台 Runner 才能运行扩展。请先阅读 [版本与下载](docs/versions.md)。没有合适宿主时仍可安装 SDK、阅读代码并运行本包的离线测试。

首期围绕提示词槽位事件、绑定动作和映射提案，不开放全部原始输入、屏幕绘制或固件内部行为。见 [开放范围](docs/scope.md)。

## 下载与维护

Releases 提供完整开发包、单独的三个扩展 ZIP 和 SDK wheel。GitHub 的 **Code → Download ZIP** 是仓库源码快照；完整开发包还含可直接导入的扩展 ZIP，二者不要混淆。

原创内容采用 **PolyForm Noncommercial 1.0.0**，商业复用另行书面授权。保留 NOTICE 与第三方许可，具体范围见 [LICENSING](LICENSING.md)。

按 [Best Effort](SUPPORT.md) 维护，不承诺固定响应、合并或发布周期。问题和需求请使用 [Issue 入口](https://github.com/JoeySystem/boring-developer-kit/issues/new/choose)，贡献方式见 [CONTRIBUTING](CONTRIBUTING.md)，版本变化见 [CHANGELOG](CHANGELOG.md)。
