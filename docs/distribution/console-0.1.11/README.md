# BORING Console 0.1.11 第三方组件说明

本资料随官方安装交付包提供，适用于本次 Apple Silicon Mac 测试版。原始 DMG 未改写；这里汇集实际构建环境中的许可文件及上游资料。源码开发包、官方应用和第三方组件的授权范围分别说明。

## Python 与运行组件

安装包包含 CPython 3.12.0、PySide6 / shiboken6 / Qt 6.11.1、PyObjC 11.1，以及下列构建环境中的依赖。Nuitka 4.2 用于编译应用及 Runner，附其运行时许可。Python 的许可文件也保留其随附组件说明。

| Python 组件 | 构建环境版本 |
|---|---|
| cryptography | 46.0.7 |
| jsonschema | 4.26.0 |
| jsonschema-specifications | 2025.9.1 |
| referencing | 0.37.0 |
| rpds-py | 2026.6.3 |
| attrs | 26.1.0 |
| psutil | 7.2.2 |
| pyobjc-framework-Cocoa | 11.1 |
| pyobjc-framework-CoreBluetooth | 11.1 |
| cffi | 2.1.1 |
| pycparser | 3.0 |
| typing-extensions | 4.16.0 |

## Qt / PySide6

Copyright The Qt Company Ltd. and other contributors. Qt 和 PySide6 保留自身 LGPL / GPL 等适用许可；本资料附 LGPLv3 和 GPLv3 全文，BORING 的非商业源码条款不替代这些许可。

Qt 框架以动态库形式随程序分发。用户对 LGPL 组件的修改、替换，以及为调试这些修改所需的反向工程，按 LGPL 许可保留。自行替换框架后的程序与官方交付包不同，可能需要重新签名才能在 macOS 上运行。

对应版本的上游源代码：

- [Qt 6.11.1 完整源码](https://download.qt.io/archive/qt/6.11/6.11.1/single/)
- [Qt for Python / PySide6 6.11.1 源码](https://github.com/pyside/pyside-setup/tree/v6.11.1)
- [Qt 模块及第三方归属说明](https://doc.qt.io/qt-6/licenses.html)
- [Qt PDF / PDFium 第三方归属说明](https://doc.qt.io/qt-6/qtpdf-licensing.html)

包内 Qt PDF 模块附带的 PDFium 及其第三方组件仍适用各自许可，详见上述 Qt PDF 归属说明；这些组件不因打包而成为 BORING 原创代码。

## 其他组件与来源

`licenses/` 中复制的 Python 组件许可来自本次构建所用虚拟环境的发行元数据；不复制凭据、设备配置或整份开发环境。OpenSSL 3.0.11 和 PyObjC 11.1 的许可来自对应上游版本，完整来源如下：

- `licenses/Qt-6.11.1-LGPL-3.0.txt`：https://raw.githubusercontent.com/qt/qtbase/v6.11.1/LICENSES/LGPL-3.0-only.txt
- `licenses/Qt-6.11.1-GPL-3.0.txt`：https://raw.githubusercontent.com/qt/qtbase/v6.11.1/LICENSES/GPL-3.0-only.txt
- `licenses/PySide6-6.11.1-LGPL-3.0.txt`：https://raw.githubusercontent.com/pyside/pyside-setup/v6.11.1/LICENSES/LGPL-3.0-only.txt
- `licenses/OpenSSL-3.0.11-LICENSE.txt`：https://raw.githubusercontent.com/openssl/openssl/openssl-3.0.11/LICENSE.txt
- `licenses/PyObjC-11.1-LICENSE.txt`：https://raw.githubusercontent.com/ronaldoussoren/pyobjc/v11.1/pyobjc-core/License.txt

第三方版权和许可保留；官方名称、图标与字库用于识别和运行 BORING 产品，不授权单独提取并用于其他品牌。安装包正常使用与本仓库源码修改许可的区别见 [使用许可](../../../LICENSING.md)。
