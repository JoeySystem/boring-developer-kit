# BORING 控制台社区源码 0.2.1

随开发包 `v0.1.0-preview.10` 交付；SDK 1.1.0 / API 1.1。此目录是独立的社区适配源码，官方安装包见 [安装说明](../docs/install-console.md)。

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

## 在设备屏幕显示 Codex 剩余额度

macOS 社区版控制台连接到支持 `codex_quota_menu` 能力的 MIST Matrix12 Power V2 固件后，长按功能键，在“专注 / 设置 / 电池 / Codex”中旋转选择 Codex，按旋钮进入额度详情。详情页显示 7 天及可用时的 5 小时剩余百分比；没有新鲜数据时提示打开控制台。数据来自本机 Codex app-server，经当前已认证的 USB 或蓝牙配置连接发送；它是额度百分比，不是 token 余额。控制台退出、断开或十分钟未收到更新后，设备清除或停止显示旧额度。

首页 Codex 额度卡片中的“在设备屏幕显示七天剩余额度”开关仅控制 NORMAL/CODEX 空闲首页的叠加显示；关闭它仍可在功能菜单查看额度。旧固件只支持原来的首页叠加显示，并保持原有开关行为。此功能不会修改按键映射、语音、配置方案或蓝牙设置。

该功能需要固件和控制台的配套构建；已安装的官方控制台与官方固件不会因更新源码而自动获得此功能。公开源码版本与已发布的 Console 0.2.1、20260921.01 固件存在差异；直接用公开源码构建并替换日常设备固件可能重置较新版本的配置和语音功能。请先由维护者合入当前发布源码、验证原配置迁移及语音功能，再发布可安装固件。社区版控制台不可替代较新的官方 Console 0.2.1。

## 公开适配与认证

- 系统字体替换内部 BORING 5R 字库，不附官方图标；窗口标记 Community。`digital_font.py` 和 `Boring5RLabel` 保留调用入口，字体绘制实现不同。
- 根目录 `protocol/` 是唯一协议和演示数据来源，不能只复制 `console/` 就丢掉这些资源。
- 认证、配置确认和更新客户端源代码保留。源码开发态及原生包 production 策略沿用原设计；production 不接受测试根或缺失认证能力的设备。
- 社区构建不能生成生产身份，不包含生产私钥或签发工具。
- `firmware-source.json` 和 `app-update-source.json` 均未配置服务；源码构建不会加入官方应用更新渠道。维护包不在开发包中，官方导入要求有效的发布者签名。旧无签名维护 ZIP 不能直接通过此入口。
- 0.2.1 同步语音应用设置、连接状态、草稿恢复、电脑端任务扩展与配置流程。具体范围见 [本轮变化](../docs/console-source.md)，自定义固件入口限制见 [固件维护边界](../docs/firmware-maintenance.md)。
- 可交互设备示意由 Qt 简单绘制；不附 Blender/STEP 产品模型、官方图标、固件屏幕素材或 Companion 引导素材，文字引导保留。

## 测试与构建

从仓库根目录运行定向回归（macOS 无窗口测试可在命令前设置 `QT_QPA_PLATFORM=offscreen:configfile=console/tests/offscreen.json`）：

```bash
console/.venv/bin/python -m pytest -q console/tests/test_device_auth.py console/tests/test_bootstrap.py console/tests/test_packaged_contract.py console/tests/test_extension_sdk.py console/tests/test_extension_platform.py console/tests/test_extension_proposals.py console/tests/test_digital_font.py tests/test_public_materials.py
```

无窗口环境使用随测试提供的 1920×1200 虚拟屏幕，避免默认 800×800 屏幕限制大窗口布局测试。

本轮实际结果与未验收项见 [验证记录](../docs/verification-console-0.2.1.md)。源码测试、模拟事件、真实设备连接和实体按键触发分别记录。

构建应用 wheel（仍需根目录协议与 SDK；不是独立安装包）：

```bash
console/.venv/bin/python -m pip wheel --no-deps --wheel-dir dist ./console
```

原生脚本要求上面的 `console/.venv` 布局，并安装 `nuitka` 和 `ordered-set`、`zstandard`。macOS 还需 Apple 命令行编译工具；Windows 需本机 C 编译环境，生成 Setup EXE 还需 Inno Setup：

```bash
console/.venv/bin/python -m pip install "Nuitka==4.2.1" ordered-set zstandard
sh console/deploy/build_macos.sh
```

```powershell
console/.venv/Scripts/python.exe -m pip install "Nuitka==4.2.1" ordered-set zstandard
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
