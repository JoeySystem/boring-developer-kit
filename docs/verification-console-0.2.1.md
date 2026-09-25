# Console 0.2.1 社区源码验证

日期：2026-09-25。开发包 `v0.1.0-preview.10`、控制台社区源码 `0.2.1`、SDK `1.1.0` / API `1.1`。来源为 0.2.1 已提交发布源码的公开适配；公开提交与标签保存实际交付内容。

## 已验证

- 认证、bootstrap、协议资源、构建身份、草稿、本地扩展 API/SDK、提案、语音设置、连接提示和恢复的定向回归。主要批次 269 项通过；更新旧 Windows 打包测试和动画断言后，相关两个测试文件 18 项全部通过。扩展示例路径问题已修正并通过复测。
- 签名、打包配置与社区安装标识测试 31 项通过；未执行完整 DMG/EXE 编译。
- 全部控制台测试模块可收集；未宣称全套测试或全部平台通过。内部模型和品牌动画资源测试不随公开副本提供。
- 控制台 wheel 0.2.1 与 SDK wheel 1.1.0 构建成功。
- 开发包独立解压，在新建 Python 3.12 环境中安装依赖和公开工程，实际执行 `controller_config.app.main(["--demo", "ready"])`，成功读取模拟设备并渲染社区示意。加载模块来自解压目录，不依赖私人模块或品牌资源。
- 私钥、部署资料、设备备份、缓存、内部字体/图标/模型/动画不进入公开文件。公开根公钥和合成测试向量保留。

## 复现

按 [运行说明](../console/README.md) 安装依赖，从仓库根目录执行：

```sh
QT_QPA_PLATFORM=offscreen:configfile=console/tests/offscreen.json console/.venv/bin/python -m pytest -q tests/test_public_materials.py console/tests/test_device_auth.py console/tests/test_bootstrap.py console/tests/test_build_identity.py console/tests/test_build_data_isolation.py console/tests/test_packaged_contract.py console/tests/test_extension_sdk.py console/tests/test_extension_contracts.py console/tests/test_extension_local_api.py console/tests/test_extension_platform.py console/tests/test_extension_proposals.py console/tests/test_drafts.py console/tests/test_local_draft_choices.py console/tests/test_session_recovery.py console/tests/test_ai_setup.py console/tests/test_voice_setup_flow.py console/tests/test_connection_progress.py console/tests/test_desktop_update_packaging.py console/tests/test_desktop_update_trial_windows.py console/tests/test_onboarding.py
```

## 边界

本次为社区源码预发布，没有进行新的 Windows/macOS 原生安装、设备写入、实体按键、BLE 重连或固件刷写验收。官方 0.2.1 安装包属于已有独立发布，本次仅更新文档入口。

固件保留 preview.9 / 20260916.01 参考。开发包不带新官方固件，不代表旧固件支持所有新可选功能；控制台按设备声明的能力显示功能。DIY 固件修改与恢复遵循既有固件文档。
