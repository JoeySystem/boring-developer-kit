# BORING MIST 固件源码

本目录公开当前 BORING MIST / Matrix12 Power V2 的可编译应用固件源码，供非商业学习和功能层 DIY。**开始修改前必须阅读 [DIY 修改边界](DIY-GUIDE.md)。设备的 USB、认证、升级、启动、分区、电源和存储底层坚决不要改。**

此源码快照对应 `20261123.01-ga7323e48 / sample-verified` 官方签名固件（样机权威包）的应用源码，包含蓝牙多主机与配对保留、第 8 键说话/发送、低电保护、启动保护与修复状态、外部内存存放配置和摇杆校准等功能。装配专用代码及生产签发资料不在公开范围内。源码编译得到的是自定义构建，不是官方签名包，也不承诺与官方镜像逐字节一致。具体来源和验收范围见 [SOURCE.md](SOURCE.md)。需要安装已签名原版时，使用单独的[官方固件维护 ZIP](../docs/official-firmware-20261123.01.md)。

仅修改按键映射、提示词、灯光配置时，优先使用控制台已有功能，不必重新编译固件。完整 DIY 安装后再恢复官方、确认实际功能正常的实机流程尚未验收，不应将编译成功理解为可放心安装任何改动。

## 编译环境

- ESP-IDF **6.0.2**，目标 ESP32-S3；先按 [Espressif 官方安装说明](https://docs.espressif.com/projects/esp-idf/en/v6.0.2/esp32s3/get-started/index.html) 安装并激活该版本。
- 激活环境后应能运行 `idf.py --version`；需要 POSIX shell、Python 3、Git（源码 ZIP 可没有 `.git`）和 `rsync`。
- 本次实际编译环境为 Apple Silicon macOS。Windows 可准备 WSL 中的 ESP-IDF 开发环境，但本次没有 Windows / WSL 实测结果。
- 编译依赖由组件管理器下载，版本见 `dependencies.lock`、`main/idf_component.yml` 和 `components/board/idf_component.yml`。不需要生产私钥、设备证书文件或服务器凭据。
- 保持仓库的 `firmware/` 与 `protocol/` 为同级目录；不要只下载单个 `firmware/` 文件夹。
- **Power V2 蓝牙目标（`matrix12-power-v2-codex-usb` / `-codex-ble`）必须用打过配对保留补丁的 ESP-IDF 6.0.2 编译**，否则构建脚本会停下。一次性设置见 [BLE-BOND-PATCH.md](BLE-BOND-PATCH.md)。

## 生成自己的维护包

包目录结构、必要字段、导入入口和安装条件见 **[DIY 固件包格式](PACKAGE-FORMAT.md)**。

在仓库根目录执行：

```sh
cd firmware
sh tools/build.sh --target matrix12-power-v2-codex-usb --custom-name mykeys
```

`mykeys` 使用 1–16 位英文字母、数字、下划线或连字符。不传名称时使用 `local`。构建自动生成 `custom-` 编号并写入镜像；无 Git 源码 ZIP 的编号带 `-nogit`。**不要伪造官方构建编号、修改硬件标识或添加官方签名。**

生成的维护 ZIP 位于 `artifacts/v0.3.0-alpha.1/matrix12-power-v2-codex-usb/custom-mykeys/` 下的本次构建目录。ZIP 只包含应用镜像和 `firmware-manifest.json`；构建只生成文件，不安装到设备。

**只能使用控制台的“导入自定义固件”入口选择此 ZIP。不要安装编译目录中的其他文件，不要单独选择 `.bin`，也不要把整个源码 ZIP 当作固件包。** 构建工具和分区配置必须保持原样，不使用通用设备写入工具或完整存储擦除命令。

## 控制台与恢复

自定义导入需要具备该入口的控制台：官方安装版 0.2.x / 0.3.0 和本仓库的 0.3.0 社区源码都有“导入自定义固件”和“导入官方固件”。社区源码没有预置官方更新地址或官方发布记录，恢复官方需要手动导入官方签名 ZIP，或使用[固件更新与校准工具](../recovery-tool/README.md)。准备安装前，请先取得具备这些功能的控制台和匹配硬件的官方签名恢复包；没有准备好就不要安装自定义固件。

恢复前提、操作和限制统一见 [DIY 修改边界与恢复说明](DIY-GUIDE.md)。本次开源交付只验证源码编译和打包，不提供“刷坏都能恢复”的保证。

## 开发测试

已有 Python 测试不接触设备：

```sh
python3 -m unittest discover -s tools/tests -p 'test_*.py'
```

测试在电脑上编译运行固件里的模型代码（蓝牙策略、启动保护、存储恢复、摇杆、电量、按键手势等），并检查构建来源编号和维护包元数据；需要本机有 C 编译器。测试通过不代表设备功能或实际升级已验收。公开文件范围见 [SOURCE.md](SOURCE.md)，许可见 [仓库许可说明](../LICENSING.md)。
