# 版本与兼容范围

| 内容 | 当前公开版本 |
|---|---|
| 源码开发包 | v0.1.0-preview.10，非商业开发者预发布 |
| 控制台社区源码 | 0.2.1 / BORING Console Community |
| SDK / 本地扩展 API | 1.1.0 / 1.1，兼容原有 API 1.0 用法 |
| 官方安装包 | macOS arm64、Windows x86_64：0.2.1，见 [下载](install-console.md) |
| 固件源码 | 保留 preview.9 / 20260916.01 历史参考，本次未更新 |
| Python / Qt | Python >=3.12,<3.14；PySide6 6.11.1；本次使用 macOS / Python 3.12 |

控制台、SDK、固件与开发包分别编号。新版 SDK 不代表设备支持全部新功能；控制台依据设备报告的能力开放操作。固件源码 ZIP、官方签名维护 ZIP、控制台安装包用途不同，不能互相导入。

社区版提供独立设置、系统字体、简化设备示意和文字引导，不包含官方图标、产品模型或品牌动画，也不订阅官方在线更新。源码 wheel 不是独立安装包，运行需要开发包中的 protocol 资源。

本轮没有新增 Windows 安装、实体按键、BLE 重连、固件刷写或长期压力验收。此前官方安装版的历史结果不能代替社区构建验收。详见 [本次验证](verification-console-0.2.1.md) 和 [历史记录](verification.md)。

[本次 Release](https://github.com/JoeySystem/boring-developer-kit/releases/tag/v0.1.0-preview.10) 提供完整源码开发包、SDK wheel 和四个示例 ZIP。历史官方固件维护包仍在 [preview.9](https://github.com/JoeySystem/boring-developer-kit/releases/tag/v0.1.0-preview.9) 独立提供。
