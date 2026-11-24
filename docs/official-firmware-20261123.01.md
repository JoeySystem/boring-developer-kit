# BORING MIST 官方签名固件 20261123.01

[下载 `Firmware-BORING-20261123.01.zip`](https://github.com/JoeySystem/boring-developer-kit/releases/download/firmware-20261123.01/Firmware-BORING-20261123.01.zip)。这是 Power V2 的**官方签名应用固件维护包**，在 BORING Console 的“导入官方固件”里使用；不是源码 ZIP，也不是通用 ESP32 烧录包。官方 Console 0.3.0 也可以在固件页直接在线更新到这一版。

- 适用硬件：`WMP-S3-MATRIX12-POWER-V2`；Build ID：`20261123.01-ga7323e48`；产品版本：`0.3.0-alpha.1`；SHA-256（ZIP）：`2f1233e9e5b3d7e8b0c5d65b931c98889d7412f3c6cead3a4bc8cd392d1d31e7`。
- 渠道和验证状态：`sample / sample-verified`，当前样机权威包，已放进官网更新源 sample 渠道，未标记为正式量产稳定版。
- 相比 20261118.01 只多一处修复：功能中心底部标签上移，SETTINGS 不再被圆形屏幕边截成 SETTIN。20261118.01 的变化（双击旋钮切换普通/Codex、双击摇杆不再切模式、满电显示 100%、过滤摇杆异常采样、畸形配置请求不再重启）都保留。更早各版见 [CHANGELOG](../CHANGELOG.md)。
- 同一 Release 的 `BORING-MIST-20261123.01-firmware-package.zip` 是完整固件包，供从源码运行或打包[固件更新与校准工具](../recovery-tool/README.md)使用。

## 怎么装

用支持官方固件导入的 BORING Console（官方 0.2.x / 0.3.0），通过 USB 连接匹配硬件，选“导入官方固件”，选择**整个 ZIP**，安装后读回上述 Build ID。不要解压后单独刷 `.bin`，不要选“导入自定义固件”，不要使用整片擦除流程。

## 验证范围

- Power V2 样机（一台）：从 20261118.01 经 USB 升级，运行分区已确认、无待回滚，配置代数和摘要不变，蓝牙配对保留。
- 手测：功能中心 SETTINGS 完整显示，USB 和蓝牙按键正常。
- 官方包签名经 Console 内置公钥验证通过，篡改版本号、哈希、大小、说明或渠道都会被拒；官网更新源上的文件用 Console 的在线校验代码离线核对通过。

## 已知限制

- 用 Console 导入本 ZIP 升级、以及 Console 实际走一遍“检查在线更新 → 下载 → 安装”都还没有实测。
- 只在一台样机上回归。
- 设备插着数据线、电脑睡眠时可能反复唤醒电脑，醒来后配置通道可能不应答，拔插数据线即可恢复。
- 2026-10-16 之前出厂配置的设备，第 8 键仍是空格，固件不自动迁移；[固件更新与校准工具](../recovery-tool/README.md)会把它改成说话/发送手势。
- 满电判定点只用一台样机标定。配置上限 16384 字节，出厂大小的方案最多 7 套。

公开源码从该包标注的干净提交选取应用运行部分，排除装配专用代码、生产签发工具、私钥、凭据与设备数据。来源说明见[固件源码范围](../firmware/SOURCE.md)。
