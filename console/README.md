# BORING 控制台源码

此目录是基于 Console **0.1.13** 已归档提交 `8a243971a10a3bd6cdf985f2e800e61e072dc0b7` 的社区源码适配，随开发包 `v0.1.0-preview.4` 交付。[官方 0.1.13 安装包](../docs/install-console.md) 另行交付，本目录提供同一 0.1.13 来源的可公开源码，保留素材替换和配置隔离适配。公开源码构建仍使用 BORING Console Community 作为程序标识，以区分官方安装版。

## 安装和运行

需要 Python `>=3.12,<3.14`；本次使用 Python 3.12。首次安装依赖需要网络。从本仓库根目录运行：

```bash
python3.12 -m venv console/.venv
console/.venv/bin/python -m pip install -e ./sdk/python -e './console[test]'
console/.venv/bin/python -m controller_config --demo ready
```

Windows 对应命令：

```powershell
py -3.12 -m venv console/.venv
console/.venv/Scripts/python.exe -m pip install -e ./sdk/python -e './console[test]'
console/.venv/Scripts/python.exe -m controller_config --demo ready
```

模拟设备不会连接硬件。真实连接时去掉 `--demo ready`，先退出正在运行的其他控制台/串口工具，避免同时占用设备。社区版使用独立应用设置、单实例名称、扩展 API 和登录启动项，官方应用的本地草稿不会自动迁移。

运行扩展只需要仓库根目录的 SDK 和 `examples/extensions/`，不需要复制进 `console/`。脚本使用控制台注入的 API 地址，不要硬编码官方宿主的本地服务名。

## 公开适配与认证

- 系统字体替换内部 BORING 5R 字库，不附官方图标；窗口标记 Community。`digital_font.py` 和 `Boring5RLabel` 保留调用入口，字体绘制实现不同。
- 根目录 `protocol/` 是唯一协议和演示数据来源，不能只复制 `console/` 就丢掉这些资源。
- 认证、配置确认和更新客户端源代码保留。源码开发态及原生包 production 策略沿用原设计；production 不接受测试根或缺失认证能力的设备。
- 社区构建不能生成生产身份，不包含生产私钥或签发工具。
- 本次 `firmware-source.json` 未配置在线更新服务；固件维护包沿用官方定向交付方式，不在开发包中。

## 测试与构建

从仓库根目录运行定向回归（macOS 无窗口测试可在命令前设置 `QT_QPA_PLATFORM=offscreen:configfile=console/tests/offscreen.json`）：

```bash
console/.venv/bin/python -m pytest -q console/tests/test_device_auth.py console/tests/test_bootstrap.py console/tests/test_packaged_contract.py console/tests/test_extension_sdk.py console/tests/test_extension_platform.py console/tests/test_extension_proposals.py console/tests/test_digital_font.py tests/test_public_materials.py
```

无窗口环境使用随测试提供的 1920×1200 虚拟屏幕，避免默认 800×800 屏幕限制大窗口布局测试。

本轮实际结果与未验收项见 [验证记录](../docs/verification.md)。源码测试、模拟事件、真实设备连接和实体按键触发分别记录。

构建应用 wheel（仍需根目录协议与 SDK；不是独立安装包）：

```bash
console/.venv/bin/python -m pip wheel --no-deps --wheel-dir dist ./console
```

原生脚本要求上面的 `console/.venv` 布局，并安装 `nuitka` 和 `ordered-set`、`zstandard`。macOS 还需 Apple 命令行编译工具；Windows 需本机 C 编译环境，生成 Setup EXE 还需 Inno Setup：

```bash
console/.venv/bin/python -m pip install nuitka ordered-set zstandard
sh console/deploy/build_macos.sh
```

```powershell
console/.venv/Scripts/python.exe -m pip install nuitka ordered-set zstandard
powershell -ExecutionPolicy Bypass -File console/deploy/build_windows.ps1 -SkipInstaller
```

脚本只清理本工程 `build/macos`、`dist/macos` 或对应 Windows 输出目录。生成 Community 包，保留生产认证策略；不需要官方签名密钥。macOS 使用 ad-hoc 签名，不等同于 Apple 公证。脚本存在不代表本轮完成了该平台安装版验收；本次 Release 不附新 DMG/EXE。

## 主要入口

| 用途 | 路径 |
|---|---|
| 应用启动 | `src/controller_config/app.py` |
| 页面与编辑器 | `src/controller_config/views/` |
| 状态与配置事务 | `src/controller_config/viewmodels/main.py` |
| USB/BLE 传输与认证 | `src/controller_config/transport/`、`src/controller_config/protocol/` |
| 本地扩展运行 | `src/controller_config/extensions/`、`src/controller_config/extension_runner.py` |
| 字体适配 | `src/controller_config/digital_font.py` |
| 协议打包 | `tools/stage_protocol_resources.py` |

原创代码采用根目录 [非商业许可](../LICENSING.md)，第三方依赖保持原许可，维护按 [Best Effort](../SUPPORT.md)。
