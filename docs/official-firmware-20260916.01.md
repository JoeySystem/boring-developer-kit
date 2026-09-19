# BORING MIST 官方固件维护包 20260916.01

[下载 `Boring-20260916.01.zip`](https://github.com/JoeySystem/boring-developer-kit/releases/download/v0.1.0-preview.9/Boring-20260916.01.zip)。这是**已签名的官方应用固件维护包**，供现有 BORING MIST 设备通过控制台更新；不是源码 ZIP、控制台安装包或通用 ESP32 烧录包。

- 适用硬件：`WMP-S3-MATRIX12-POWER-V2`；Build ID：`20260916.01-gbc6d2699-dirty`。
- 渠道及验证状态：`sample / sample-verified`。首批样机完成普通 USB 更新、回连、版本读回及快速输入响应检查；未标记为 RC 或正式量产发布。
- 已保存的待机设置保持不变；新设备默认 15 分钟待机，保存的屏幕亮度按实际百分比生效。另启用 80–160 MHz 动态调频，并在蓝牙未连接时于快速广播后降低广播频率；实际省电幅度尚未量化。

使用最新版**官方 BORING Console**，用 USB 连接设备，打开“设置 → 固件更新 → 更多固件选项 → 选择官方固件文件”，选择整个 ZIP。确认硬件标识和版本后安装，保持 USB 与供电稳定，等待设备重连并读回上述 Build ID。仓库单独提供的 0.1.13 旧安装包不能据此认定具备此维护入口；如当前控制台没有该入口，应先取得新版官方控制台。

ZIP 内仅有签名 `firmware-manifest.json`、应用 `.bin` 和中文使用说明。不要解压后单独刷 `.bin`，不要选择“导入自定义固件”，也不要用整片擦除或 BOOT 拆机流程。若控制台拒绝包或设备无法建立正常 USB 更新会话，应停止并联系官方支持。

同一 Release 的 `BORING-Developer-Kit-v0.1.0-preview.9.zip` 是**可修改源码**；用它编译得到 `custom-` 自定义包，不会带官方签名。源码对应功能，但原官方包标记 `git_dirty=true`，不能声称由公开提交逐字节重建。源码许可与 DIY 边界见 [许可](../LICENSING.md)、[固件来源](../firmware/SOURCE.md)及[修改与恢复说明](../firmware/DIY-GUIDE.md)。
