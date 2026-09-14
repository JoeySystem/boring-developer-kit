# 第三方依赖

源码开发包不内置 Python、Qt 或其他第三方运行库二进制。另行发布的官方 0.1.13 安装交付包包含程序运行所需组件，并附 [许可文本与来源说明](docs/distribution/console-0.1.13/README.md)。SDK wheel 包含 BORING SDK 源码及其许可；社区控制台 wheel 按其构建配置收集应用代码和资源，运行依赖通过 Python 包管理器另行安装。

| 依赖 | 用途 | 上游许可入口 |
|---|---|---|
| PySide6 6.11.1 / Qt for Python | 控制台界面、USB 通信、本地 API 与 SDK 进程通信 | [Qt 官方许可说明](https://doc.qt.io/qtforpython-6/commercial/index.html)；适用许可按实际组件与版本区分 |
| cryptography | 控制台设备认证、公开认证向量测试 | [上游许可证](https://github.com/pyca/cryptography/blob/main/LICENSE) |
| jsonschema | 控制台配置和扩展格式验证、公开样例测试 | [上游许可证](https://github.com/python-jsonschema/jsonschema/blob/main/COPYING) |
| psutil | 控制台主机进程信息 | [上游许可证](https://github.com/giampaolo/psutil/blob/master/LICENSE) |
| PyObjC core / Cocoa / CoreBluetooth | macOS 原生应用与蓝牙接口 | [上游许可证](https://github.com/ronaldoussoren/pyobjc/blob/main/LICENSE.txt) |
| pytest / pytest-qt | 开发测试 | [pytest](https://github.com/pytest-dev/pytest/blob/main/LICENSE) / [pytest-qt](https://github.com/pytest-dev/pytest-qt/blob/master/LICENSE) |
| setuptools | SDK 和控制台源码 wheel 构建 | [上游许可证](https://github.com/pypa/setuptools/blob/main/LICENSE) |

准确依赖版本和平台条件见 [控制台 pyproject.toml](console/pyproject.toml) 与 [SDK pyproject.toml](sdk/python/pyproject.toml)。原生安装包工具所引入的依赖也需要保留其实际版本的许可；源码 wheel 构建与原生安装包交付是不同步骤。

社区构建调用操作系统已有字体，不再分发内部 BORING 5R 字库或官方应用图标。系统字体的存在不授予提取、打包或再分发字体文件的权利。

第三方代码及运行库保留各自许可，不受本仓库的非商业条款重新授权。自行打包、修改或再分发时，须履行实际所用第三方组件的许可要求；本仓库许可只覆盖有权按其授权的 BORING 原创内容。

## Sparkle 更新支持

公开 macOS 更新客户端和构建脚本可对接 Sparkle；本源码包不捆绑框架二进制。保留其 [许可文本](console/src/controller_config/assets/Sparkle-LICENSE.md)，实际分发框架时需随包保留许可。

## 固件构建依赖

`firmware/` 不捆绑以下组件源码或工具链二进制；ESP-IDF 组件管理器按 `dependencies.lock` 获取。上游代码保持原有许可，不改为本仓库非商业许可。自行分发构建产物时需随实际依赖保留相应许可。

- ESP-IDF 6.0.2：[Espressif 源码与许可](https://github.com/espressif/esp-idf/tree/v6.0.2)。
- cJSON 1.7.19：[上游](https://github.com/DaveGamble/cJSON)。
- esp_tinyusb 2.0.0、TinyUSB 0.21.0~1：[Espressif 组件](https://components.espressif.com/components/espressif/esp_tinyusb/versions/2.0.0)、[TinyUSB](https://github.com/hathach/tinyusb)。
- esp_secure_cert_mgr 2.9.3：[上游](https://github.com/espressif/esp_secure_cert_mgr)。
- led_strip 3.0.3：[组件来源](https://components.espressif.com/components/espressif/led_strip/versions/3.0.3)。
- waveshare/esp_lcd_st7735 2.0.0：[组件来源](https://components.espressif.com/components/waveshare/esp_lcd_st7735/versions/2.0.0)。
