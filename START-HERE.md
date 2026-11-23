# 从这里开始

## 设备需要恢复或重新校准

先看 [工具使用说明](recovery-tool/README.md)，下载 Mac 或 Windows 版固件更新与校准工具。注意它会清空三个蓝牙槽位并重新校准摇杆。工具打不开或更新失败时，用 [AI 提示词](recovery-tool/AI-RECOVERY-PROMPT.md) 配合源码定位。USB 完全不响应时请联系售后。

## 只想使用设备

下载 [官方 macOS 或 Windows 安装包](docs/install-console.md)。不需要编译源码。

## 修改控制台

下载并解压 [preview.11 源码开发包](https://github.com/JoeySystem/boring-developer-kit/releases/tag/v0.1.0-preview.11)，按 [console/README.md](console/README.md) 安装 Python 依赖，运行 `python -m controller_config --demo ready`。请保留开发包完整目录，特别是 `protocol/` 和 `sdk/`。

社区源码对应 Console 0.2.3，使用独立设置、系统字体和简化设备示意，不附官方品牌动画或产品模型。源码运行不需要生产凭据，不加入官方软件更新渠道。

## 编写扩展

从 `observe_prompt`、`claim_prompt`、`propose_mapping`、`save_host_task` 四个示例开始。SDK 1.1.0 / API 1.1，支持原有 1.0 提示词流程和新增电脑端任务事件。见 [接口](docs/extensions.md) 和 [SDK](sdk/python/README.md)。

## 固件

`firmware/` 对应官方签名固件 20261118.01 的应用源码。编译 Power V2 蓝牙目标前要先给 ESP-IDF 6.0.2 打[配对保留补丁](firmware/BLE-BOND-PATCH.md)。源码 ZIP 不能当作维护包导入。请先阅读 [DIY 边界](firmware/DIY-GUIDE.md)。

原创部分使用 [非商业许可](LICENSING.md)。验证与已知限制见 [本次记录](docs/verification-sync-20261123.md)。
