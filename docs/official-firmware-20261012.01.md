# BORING MIST 官方签名固件 20261012.01

[下载 `Firmware-BORING-20261012.01.zip`](https://github.com/JoeySystem/boring-developer-kit/releases/download/firmware-20261012.01/Firmware-BORING-20261012.01.zip)。这是 Power V2 设备的**官方签名应用固件维护包**，适用于控制台中的官方固件导入；不是源码 ZIP、通用 ESP32 烧录包或 USB 售后恢复镜像。

- 适用硬件：`WMP-S3-MATRIX12-POWER-V2`；Build ID：`20261012.01-g5398b776`；产品版本：`0.3.0-alpha.1`。
- 渠道和验证状态：`sample / sample-verified`，已完成留样机更新、读回及主要摇杆输入验收，未标记为正式量产稳定版。
- 本版包含逐台摇杆校准、读数失效后方向释放、Normal 单次拨动单方向，并保留 Codex 连续画圈、启动状态修复和语音按住行为。

使用支持官方固件导入的 BORING Console，通过 USB 连接匹配硬件，选择**整个 ZIP**，并在安装后读回上述 Build ID。不要解压后单独刷入 `.bin`，不要选择“导入自定义固件”，不要使用整片擦除流程。GitHub 仓库源码自行编译得到的是自定义构建，和此签名包分别交付。

此包原先已发布到 BORING 的 `sample` 更新渠道；本次 GitHub Release 提供同一官方 ZIP 的下载入口。公开源码从该包标注的干净提交选取应用运行部分，排除装配专用代码、生产签发工具、私钥、凭据与设备数据。来源说明见[固件源码范围](../firmware/SOURCE.md)。
