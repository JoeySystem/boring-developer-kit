# 第三方依赖

源码开发包不内置 Python、Qt 或其他第三方运行库二进制。另行发布的官方 0.1.11 安装交付包包含程序运行所需组件，并附 [许可文本与来源说明](docs/distribution/console-0.1.11/README.md)。SDK wheel 包含 BORING SDK 源码及其许可；社区控制台 wheel 按其构建配置收集应用代码和资源，运行依赖通过 Python 包管理器另行安装。

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
