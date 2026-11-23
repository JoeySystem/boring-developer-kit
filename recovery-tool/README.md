# BORING MIST 固件更新与校准工具

适用于 WMP-S3-MATRIX12-POWER-V2（BORING MIST）。内置官方签名固件 **20261118.01-gf1f54815**，有 Apple 芯片 Mac 和 Windows 64 位两个版本。

[下载 Mac / Windows 版](https://github.com/JoeySystem/boring-developer-kit/releases/tag/mist-recovery-20261118.01) · [故障排查](TROUBLESHOOTING.md) · [交给 AI 的提示词](AI-RECOVERY-PROMPT.md)

## 它会做什么

点“开始更新”后，工具依次自动完成：

1. **写入固件**：通过 USB 写入内置固件，设备重启后读回版本核对。设备已经是这个版本时跳过写入，不重启。
2. **清空蓝牙槽位**：三个蓝牙槽位逐个清除并读回，确认都为空。之后电脑和手机需要重新配对；电脑端的旧配对记录要在系统蓝牙设置里“忽略设备”。
3. **设置第 8 键**：两个出厂 Profile（Codex macOS / Codex Windows）的第 8 键改为“单击说话（右 Option / 右 Alt + 空格）、双击发送（Enter）”，并移除全局语音键覆盖。自建 Profile 和其他按键不动。
4. **校准摇杆**：按窗口提示把摇杆推到上、下、左、右极限再松手，完成后自动保存，再做一次四向验收。原有摇杆校准会被新结果替换。

工具不改设备身份、不恢复出厂设置、不擦整片 Flash，也没有拆机或按 BOOT 的步骤。

## 使用

1. 完全退出 BORING Console、串口助手和其他刷机工具。
2. 用支持数据传输的 USB 线连接一台**正常开机**的 BORING MIST。
3. Mac：解压 ZIP，打开 `BORING-MIST-BLE-Recovery.app`；首次被系统拦截时右键选“打开”。Windows：把整个 ZIP 解压到短路径（例如 `C:\BORING`），打开文件夹里的 `BORING-MIST-BLE-Recovery.exe`，不要把 EXE 单独拖出来。
4. 窗口显示“已识别 1 台 BORING MIST”后点“开始更新”，按提示完成摇杆校准。
5. 看到“本台已完成”即可拔线。ZIP 里另附 `Firmware-BORING-20261118.01.zip`，换电脑使用时可在 BORING Console 的“导入官方固件”里选它，让 Console 显示官方固件。

同一时间只能开一个工具窗口，一次只接一台设备。固件写入期间窗口无法关闭。

## 限制

- 需要设备能正常开机，并能通过 USB 数据通道通信。屏幕不亮、只有灯闪时可以先试，但完全没有 USB 响应的设备无法靠电脑端工具救回，请联系售后。
- 校准期间关闭窗口：工具会先取消未保存的校准再退出，设备保留原配置。
- Mac 版为本地临时签名，未做 Apple 公证；Windows 版未做程序签名，首次运行出现 SmartScreen 提示时选“仍要运行”。这与固件的 BORING 官方发布签名是两回事。
- 20261118.01 是样机权威包（sample-verified），已在 Power V2 样机上通过 Console 导入和旧版升级验证；不代表所有设备、平台和长期运行都已验收。
- 操作记录保存在本机：Mac 为 `~/Library/Application Support/Codex Micro Firmware Provisioner/history`，Windows 为 `%LOCALAPPDATA%\Codex Micro Firmware Provisioner\history`，不会自动上传。

## 源码运行与构建

需要 Python 3.12 或 3.13。工具运行时要一份完整的固件包目录（含 `bootloader/`、`partition_table/`、`ota_data_initial.bin`、应用镜像、`firmware-manifest.json`，以及 `online/` 下带发布签名的 `Firmware-BORING-*.zip`），从 Release 的 `BORING-MIST-20261118.01-firmware-package.zip` 解压得到 `matrix12-power-v2-20261118.01-sample-verified/` 目录。

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-authority-update.txt
.venv/bin/python packaging/macos/prepare_bundle.py --tool-root . --output . \
  --authority /path/to/matrix12-power-v2-20261118.01-sample-verified
.venv/bin/python authority_update_app.py
```

重新打包：Mac 运行 `scripts/build-authority-updater-macos.sh /path/to/固件包目录`；Windows 在 PowerShell 运行 `scripts/build-authority-updater-windows.ps1 -AuthorityPath <固件包目录>`。社区构建使用 `packaging/make_icon.py` 生成的通用图标，官方版图标不在本仓库。

修改检测、提示、串口或更新流程后，先运行 `python -m unittest discover -s tests -t .`，再用自己的样机验证。不要把“检测到端口”或“传输完成”当作更新成功。开发说明见 [AGENTS.md](AGENTS.md)。

工具源码不含发布私钥、生产身份签发工具或客户数据。改过的固件会使官方签名失效，不能再当官方构建使用，请按[自定义固件流程](../firmware/README.md)处理。

源码沿用 PolyForm Noncommercial 1.0.0 非商业许可，见 [LICENSE](LICENSE) 和 [NOTICE](NOTICE)；第三方组件保留各自许可，见 [THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md)。
