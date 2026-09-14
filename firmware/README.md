# BORING MIST 固件源码

本目录公开当前 BORING MIST / Matrix12 Power V2 的可编译应用固件源码，供非商业学习和功能层 DIY。**开始修改前必须阅读 [DIY 修改边界](DIY-GUIDE.md)。设备的 USB、认证、升级、启动、分区、电源和存储底层坚决不要改。**

此源码快照以 `20260913.01` 样品固件的功能为参考，并包含截至 2026-09-14 的默认 DIY 构建与无 Git 源码 ZIP 支持。源码生成的是自定义构建，不是官方签名包，也不承诺与官方镜像逐字节一致。具体来源和验收范围见 [SOURCE.md](SOURCE.md)。

仅修改按键映射、提示词、灯光配置时，优先使用控制台已有功能，不必重新编译固件。完整 DIY 安装后再恢复官方、确认实际功能正常的实机流程尚未验收，不应将编译成功理解为可放心安装任何改动。

## 编译环境

- ESP-IDF **6.0.2**，目标 ESP32-S3；先按 [Espressif 官方安装说明](https://docs.espressif.com/projects/esp-idf/en/v6.0.2/esp32s3/get-started/index.html) 安装并激活该版本。
- 激活环境后应能运行 `idf.py --version`；需要 POSIX shell、Python 3、Git（源码 ZIP 可没有 `.git`）和 `rsync`。
- 本次实际编译环境为 Apple Silicon macOS。Windows 可准备 WSL 中的 ESP-IDF 开发环境，但本次没有 Windows / WSL 实测结果。
- 编译依赖由组件管理器下载，版本见 `dependencies.lock`、`main/idf_component.yml` 和 `components/board/idf_component.yml`。不需要生产私钥、设备证书文件或服务器凭据。
- 保持仓库的 `firmware/` 与 `protocol/` 为同级目录；不要只下载单个 `firmware/` 文件夹。

## 生成自己的维护包

在仓库根目录执行：

```sh
cd firmware
sh tools/build.sh --target matrix12-power-v2-codex-usb --custom-name mykeys
```

`mykeys` 使用 1–16 位英文字母、数字、下划线或连字符。不传名称时使用 `local`。构建自动生成 `custom-` 编号并写入镜像；无 Git 源码 ZIP 的编号带 `-nogit`。**不要伪造官方构建编号、修改硬件标识或添加官方签名。**

生成的维护 ZIP 位于 `artifacts/v0.3.0-alpha.1/matrix12-power-v2-codex-usb/custom-mykeys/` 下的本次构建目录。ZIP 只包含应用镜像和 `firmware-manifest.json`；构建只生成文件，不安装到设备。

**只能使用控制台的“导入自定义固件”入口选择此 ZIP。不要安装编译目录中的其他文件，不要单独选择 `.bin`，也不要把整个源码 ZIP 当作固件包。** 构建工具和分区配置必须保持原样，不使用通用设备写入工具或完整存储擦除命令。

## 控制台与恢复

自定义导入需要具备该入口的控制台；本文的“查看官方版本 → 下载 → 恢复官方固件”操作对应 **0.1.29** 的已实现界面。本仓库现有社区控制台源码仍为 0.1.24，公开旧安装包仍为 0.1.13，本次没有替换它们，不能据此认定旧安装包具备新恢复流程。准备安装前，请先取得具备这些功能的官方控制台和匹配硬件的官方签名恢复包；没有准备好就不要安装自定义固件。

恢复前提、操作和限制统一见 [DIY 修改边界与恢复说明](DIY-GUIDE.md)。本次开源交付只验证源码编译和打包，不提供“刷坏都能恢复”的保证。

## 开发测试

已有 Python 测试不接触设备：

```sh
python3 -m unittest discover -s tools/tests -p 'test_*.py'
```

测试覆盖构建来源编号、无 Git 构建入口和维护包元数据，不代表所有设备功能或实际升级已验收。公开文件范围见 [SOURCE.md](SOURCE.md)，许可见 [仓库许可说明](../LICENSING.md)。
