# BORING Developer Kit

让用户通过扩展和设备协议，定义自己的 BORING 使用方式。

**当前阶段：开发者文档预览。SDK、示例、协议文件和配套开发包尚未发布，当前仓库不能用于安装或运行扩展。**

[从这里开始](START-HERE.md) · [开放范围](docs/scope.md) · [版本与下载状态](docs/versions.md) · [常见问题](docs/faq.md)

## 你可以在这里做什么

- 了解扩展如何连接设备触发与电脑上的个人流程；
- 阅读开发流程、示例用途和故障反馈方法；
- 提交具体使用场景或文档改进建议；
- 在后续开发包发布后，阅读源码、修改示例并开发自己的扩展，使用范围以随包许可为准。

首期开发路径是 **官方 BORING Console + Python 扩展**。控制台负责设备连接与扩展运行；开发者负责自己的动作逻辑。原始设备协议将作为进阶资料提供，自写上位机与第三方环境适配不属于首期承诺支持的路径。

首期围绕提示词槽位触发、扩展动作和按键映射提案展开，不代表所有按键、旋钮、屏幕或固件内部行为都可任意接管。详见 [扩展如何工作](docs/extensions-overview.md)。

## 文档导航

| 你想了解 | 阅读位置 |
|---|---|
| 第一次参与，需要准备什么 | [开始使用](START-HERE.md) |
| 哪些部分开放、哪些保留 | [开放范围](docs/scope.md) |
| 观察事件、处理动作和改键提案有什么区别 | [扩展如何工作](docs/extensions-overview.md) |
| 开发包发布后，怎样从示例做出自己的扩展 | [开发流程](docs/development-guide.md) |
| 下载状态、版本对应和平台支持 | [版本与下载](docs/versions.md) |
| 常见问题和问题定位 | [FAQ](docs/faq.md) |
| 提交 Issue 或 PR | [贡献指南](CONTRIBUTING.md) |
| 维护承诺 | [Best Effort 维护政策](SUPPORT.md) |
| 使用与商业授权 | [授权状态](LICENSING.md) |
| 仓库最近更新 | [更新记录](CHANGELOG.md) |

## 下载

当前没有公开开发包。后续下载统一放在 [Releases](https://github.com/JoeySystem/boring-developer-kit/releases)，每次发布注明配套控制台、固件、平台和已知限制。

GitHub 的 **Code → Download ZIP** 下载的是仓库文档快照，不是可导入控制台的扩展包。

## 授权与维护

计划采用非商业源码开放、商业复用另行书面授权的模式；具体许可尚未定稿，当前不授予产品代码的使用权，详见 [授权状态](LICENSING.md)。

本项目按 Best Effort 方式维护，不承诺固定响应或发布周期。优先稳定产品固件、桌面端、量产和更新链路。问题与建议请使用 [Issue 入口](https://github.com/JoeySystem/boring-developer-kit/issues/new/choose)；官方产品正常售后与社区扩展维护分开处理。
