# 第三方依赖

本开发包不内置 Python、PySide6、Qt、cryptography、jsonschema 或 pytest 的二进制。SDK wheel 只包含 BORING SDK 源码及其许可，第三方依赖通过 Python 包管理器单独安装。

| 依赖 | 本次用途 | 上游许可说明 |
|---|---|---|
| PySide6 6.11.1 / Qt for Python | SDK 的本地进程通信 | [Qt 官方许可说明](https://doc.qt.io/qtforpython-6/commercial/index.html)；社区版为 LGPLv3 / GPLv3，另有商业许可，按具体组件适用 |
| jsonschema | 验证协议配置样例，仅开发测试 | [上游许可证](https://github.com/python-jsonschema/jsonschema/blob/main/COPYING) |
| cryptography | 验证公开的认证测试向量，仅开发测试 | [上游许可证](https://github.com/pyca/cryptography/blob/main/LICENSE) |
| pytest | 开发测试 | [上游许可证](https://github.com/pytest-dev/pytest/blob/main/LICENSE) |
| setuptools | 构建 SDK wheel | [上游许可证](https://github.com/pypa/setuptools/blob/main/LICENSE) |

打包、再分发第三方运行库时，需同时履行其实际版本的许可要求。本仓库的 PolyForm 许可只覆盖 BORING 原创交付内容，不改变上游许可证。
