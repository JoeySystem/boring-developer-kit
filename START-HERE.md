# 从这里开始

本次是 `v0.1.0-preview.9` 开发者预发布。固件源码按功能对齐 Power V2 `20260916.01 / sample-verified`，并单独提供已签名的[官方维护 ZIP](docs/official-firmware-20260916.01.md)。控制台公开适配源码保持 0.1.46，另有 DIY 修改边界、SDK、示例和协议。可以从源码运行 BORING Console Community，也可在兼容官方宿主中使用扩展。实际验证范围见 [发布验证](docs/verification.md)。

## 先决定是否需要源码

如果只是安装控制台或尝试扩展，先下载 [0.1.13 M 系列 Mac 测试版](https://github.com/JoeySystem/boring-developer-kit/releases/download/console-v0.1.13/BORING-Console-0.1.13-macOS-arm64.zip)，按 [安装说明](docs/install-console.md) 操作即可，不需要先配置 Python。安装交付 ZIP 内是 DMG、说明和许可；它不是固件包。

只有查看或修改源码时，才需要下面的源码开发环境。公开安装版仍是 0.1.13，源码已到 0.1.46；两者的功能和验收范围分别说明。

## 1. 下载和许可

从 [本次 Release](https://github.com/JoeySystem/boring-developer-kit/releases/tag/v0.1.0-preview.9) 下载 `BORING-Developer-Kit-v0.1.0-preview.9.zip` 并解压。阅读 [许可](LICENSING.md) 和 [兼容说明](docs/versions.md)。如只更新官方固件，请下载同页的 `Boring-20260916.01.zip` 并按[固件说明](docs/official-firmware-20260916.01.md)操作，不要下载源码包代替。

完整开发包中的 `examples/packages/` 放有三个独立扩展 ZIP。导入控制台的是其中一个扩展 ZIP，不是整个开发包。仓库源码用户也可直接导入 `examples/extensions/` 下的单个示例目录。

## 2. 选择你的路径

### 学习或修改固件

先阅读 [DIY 修改边界](firmware/DIY-GUIDE.md)，再按 [固件编译说明](firmware/README.md) 构建。不要修改基础模块，不要把源码包直接导入控制台。本次没有更新公开 0.1.13 安装包；安装 DIY 前必须先准备具备自定义导入与官方恢复功能的控制台。

### 修改或运行控制台源码

先按下面的 Python 虚拟环境说明准备依赖，再阅读 [console/README.md](console/README.md) 启动程序、使用模拟设备和运行测试。社区版使用系统字体，不带官方应用图标；它不是官方安装包，也不包含固件。来源和开放边界见 [控制台源码说明](docs/console-source.md)。

### 已有兼容的控制台

1. 打开兼容控制台，连接能被当前控制台接受的设备，确认设备已就绪。
2. 进入扩展管理，导入 `observe_prompt.zip` 或 `observe_prompt` 目录，再启用。
3. 在控制台中确认要触发的提示词已写入设备，并且有实际的实体触发配置；不要把空槽位直接当成可用触发。
4. 触发该槽位，查看扩展日志中的 `prompt.triggered` 和内容。观察示例不阻止默认粘贴。
5. 再试 `claim_prompt`：在控制台将一个已配置槽位绑定到 `Use prompt` 动作，然后实体触发。示例打印调用并报告完成，不执行额外外部操作。
6. 需要改键提案时再读 [propose_mapping 说明](examples/extensions/propose_mapping/README.md)。它建议将 `key.12` 改为 Enter，启动即提出一次建议，并保持在线等待用户审阅，仍需用户批准和确认写入。

本页步骤描述源码已实现的流程。正式安装包和社区源码的具体界面、验证范围可能不同，不能仅凭版本接近就认定全部流程已经通过。

### 准备源码开发环境

建议使用本轮验证目标 Python 3.12，从解压后的根目录创建 `console/.venv` 虚拟环境，以便后续原生打包脚本直接使用。项目声明支持 3.13，但本次未实测。macOS / Linux：

```bash
python3.12 -m venv console/.venv
source console/.venv/bin/activate
python -m pip install -e ./sdk/python -e "./console[test]"
python -m pip install -r requirements-dev.txt
python -m pytest -q tests
```

Windows PowerShell：

```powershell
py -3.12 -m venv console/.venv
.\console\.venv\Scripts\python.exe -m pip install -e ./sdk/python -e "./console[test]"
.\console\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\console\.venv\Scripts\python.exe -m pytest -q tests
```

上述 `tests` 命令运行公开材料测试；控制台测试命令见 [console/README.md](console/README.md)。Windows 命令是环境准备说明，本次未在 Windows 上验收。安装依赖可能需要联网，离线测试本身不连接硬件。仅开发 SDK 时可省略 `./console[test]`。

不要直接运行示例 `main.py` 来替代控制台：SDK 需要 Runner 注入的本地 API 地址和扩展 ID，缺少时会报 `missing runner environment`。

## 3. 改成自己的动作

先复制一个示例，修改显示名称和唯一扩展 ID，再更改 Python 逻辑。控制台导入的是受管副本；修改开发目录后要停用并按版本说明替换或重新导入，重启旧副本不会自动更新源码。

具体方法见 [开发流程](docs/development-guide.md) 和 [API 参考](docs/api.md)。
