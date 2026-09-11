# BORING Developer Kit

让用户通过扩展、设备协议和控制台源码，定义自己的 BORING 使用方式。

**先选择你需要的内容：**

- **直接安装使用：**[BORING 控制台 0.1.11 · M 系列 Mac 测试版](https://github.com/JoeySystem/boring-developer-kit/releases/download/console-v0.1.11/BORING-Console-0.1.11-macOS-arm64.zip) · [安装说明与已知限制](docs/install-console.md)。交付 ZIP 内含 DMG，不含固件。
- **查看或修改源码：**[开发包 v0.1.0-preview.3](https://github.com/JoeySystem/boring-developer-kit/releases/tag/v0.1.0-preview.3)。提供基于 Console 0.1.6 的控制台源码、Python SDK 1.0.0、三个示例、协议与文档，采用非商业许可。

安装包与公开源码独立标注版本，当前源码不是 0.1.11 的完整源码。

[开始使用](START-HERE.md) · [下载源码开发包](https://github.com/JoeySystem/boring-developer-kit/releases/tag/v0.1.0-preview.3) · [兼容与验证边界](docs/versions.md) · [许可](LICENSING.md)

<p align="center">
  <img src="docs/images/boring-mist-product.png" alt="BORING MIST 产品实拍：金属外壳、彩色背光按键、圆形屏幕、旋钮和摇杆" width="420">
  <br>
  <sub>BORING MIST · 产品实拍</sub>
</p>

## 本次可用的内容

| 内容 | 入口 |
|---|---|
| 控制台源码、运行与构建 | [console](console/README.md) · [来源与边界](docs/console-source.md) |
| Python SDK 源码及安装说明 | [sdk/python](sdk/python/README.md) |
| 三个扩展及预期结果 | [示例说明](examples/README.md) |
| SDK 方法、返回值和错误处理 | [API 参考](docs/api.md) |
| 扩展 manifest、动作和提案 | [扩展接口](docs/extensions.md) |
| 设备事件字段 | [设备事件](docs/device-events.md) |
| USB / BLE WMP1 协议、配置 Schema 和公开测试向量 | [protocol](protocol/README.md) |
| 平台与配套控制台限制 | [兼容说明](docs/versions.md) |
| 修改、导入和重新打包自己的扩展 | [开发流程](docs/development-guide.md) |

## 运行前先确认宿主

SDK 是控制台本地 API 的客户端，**不是独立硬件驱动**。运行扩展需要具有扩展 Runner / API 1.0 的 BORING Console，以及控制台接受的设备会话。

本次控制台源码取自已提交的 0.1.6 快照，经过公开分发适配，名称为 **BORING Console Community**；它不代表仍在开发的更新产品版本。可以按 [控制台说明](console/README.md) 从源码运行，也可使用兼容的官方宿主。SDK / API 版本不变，配套固件参考仍是 `.07 / sample-verified`。源码开发包不附安装包或固件；官方 0.1.11 安装包单独提供，见 [安装说明](docs/install-console.md)。历史官方安装版验收不能替代公开源码构建验收。具体结果见 [发布验证](docs/verification.md)。

首期围绕提示词槽位事件、绑定动作和映射提案，不开放全部原始输入、屏幕绘制或固件内部行为。见 [开放范围](docs/scope.md)。

## 下载与维护

Releases 分别提供官方安装交付包和源码开发包；开发包另有三个扩展 ZIP 和 SDK wheel。GitHub 的 **Code → Download ZIP** 是仓库源码快照；完整开发包还含可直接导入的扩展 ZIP，二者不要混淆。

包括本轮控制台代码在内的原创内容采用 **PolyForm Noncommercial 1.0.0**，商业复用另行书面授权。保留 NOTICE 与第三方许可，具体范围见 [LICENSING](LICENSING.md)。

按 [Best Effort](SUPPORT.md) 维护，不承诺固定响应、合并或发布周期。问题和需求请使用 [Issue 入口](https://github.com/JoeySystem/boring-developer-kit/issues/new/choose)，贡献方式见 [CONTRIBUTING](CONTRIBUTING.md)，版本变化见 [CHANGELOG](CHANGELOG.md)。
