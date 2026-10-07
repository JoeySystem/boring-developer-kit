# BORING MIST 官方固件恢复工具

适用于 M 系列 Mac 与 WMP-S3-MATRIX12-POWER-V2 设备。内置 20261007.01 修复固件，保留个人配置和设备身份。

[下载安装包与源码](https://github.com/JoeySystem/boring-developer-kit/releases/tag/mist-recovery-20261007.01) · [AI 恢复提示词](AI-RECOVERY-PROMPT.md) · [故障排查](TROUBLESHOOTING.md)

## 使用

1. 打开 DMG，把“BORING MIST 恢复工具”拖入“应用程序”。退出 Boring Console 和其他占用设备的程序。
2. 用支持数据传输的 USB 线直连设备和 Mac。设备屏幕不亮或只有灯闪时，也可让工具检测。
3. 打开恢复工具，浏览器会显示仅在本机运行的恢复页面。点击“恢复官方固件”，保持连接，等待重启和版本核对。
4. 看到恢复成功且官方来源登记完成后，点击“退出工具”，再打开官方 Boring Console 0.2.1 或更新版。当前建议通过 USB 使用。
5. 如果失败，点击“保存诊断信息”，把文件发给售后。日志不自动上传。

工具需要设备仍能响应 USB 固件通信。不亮屏不代表一定不能恢复，但完全没有 USB 接口或协议无法响应时，工具无法越过硬件/启动故障强行烧录。无需拆机，也没有让用户按 BOOT 的操作。

macOS 应用目前为本地 ad-hoc 签名，未做 Apple Developer ID 公证；这与固件的 BORING 官方发布签名是两回事。首次打开若系统提示无法验证开发者，可在确认文件来自 BORING 售后的前提下，按 macOS“隐私与安全性”的“仍要打开”流程操作。公司管理策略禁止运行时请联系售后，不要关闭系统安全保护。

## 官方识别

工具先验证内置固件的官方 Ed25519 签名。仅当 USB 刷写完成、同一设备重启并回读精确构建号后，才把这份有效签名发布记录加入当前 macOS 用户的 Console 官方记录。保留已有记录；不修改 Console 程序、信任公钥、设备证书或 eFuse。

这份记录只在执行工具的这台 Mac/当前账户登记。换电脑使用时，可在另一台 Mac 执行同样的恢复流程，或导入 Release 单独提供的官方签名 ZIP。固件签名不表示蓝牙问题已修复；当前售后范围是 USB 恢复。

## 源码运行与修改

需要 Python 3.13。执行：

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python authority_update_app.py
```

重建 DMG：`./build-macos.sh`。构建需要 macOS Command Line Tools；内置依赖，不要求客户安装 Python。产物架构与构建 Mac 相同；本次交付 arm64。

可把整个目录交给 Codex 或其他 AI，入口见 AGENTS.md。修改检测、错误提示、串口连接或恢复流程后，先运行 `python -m unittest discover -s tests`，再用自己的样机验证。不要把仅检测到端口或发送完成当作恢复成功。

固件工程与修复补丁在交付目录的 `firmware-source.zip`。固件工具不包含发布私钥、生产根密钥、客户数据或本机配置。修改固件会使原签名失效；自改固件不能继续冒充官方构建，需自行按自定义固件流程使用。

## 本次固件内容

- 首次电量读取失败时，避免把原本已开机的设备错误记为关机。
- 开机自检灯光从实际开始显示时计时，避免初始化耗时跳过后续灯光。
- 构建号：20261007.01-gae3b06fb-dirty。保留真实工程状态；并非全量 stable 发布。

实机结果见[公开验证记录](https://github.com/JoeySystem/boring-developer-kit/blob/main/docs/verification-mist-recovery-20261007.md)。

源码沿用 PolyForm Noncommercial 1.0.0 非商业许可，见 LICENSE 和 NOTICE；第三方组件保留各自许可，见 THIRD-PARTY-NOTICES.md。
