# 从这里开始

本次是 `v0.1.0-preview.1` 开发者预发布。源码、示例和协议现在可以下载；运行示例需要兼容的官方控制台，完整安装包与真实设备组合尚未由本发布验证。

## 1. 下载和许可

从 [本次 Release](https://github.com/JoeySystem/boring-developer-kit/releases/tag/v0.1.0-preview.1) 下载 `BORING-Developer-Kit-v0.1.0-preview.1.zip` 并解压。阅读 [许可](LICENSING.md) 和 [兼容说明](docs/versions.md)。

完整开发包中的 `examples/packages/` 放有三个独立扩展 ZIP。导入控制台的是其中一个扩展 ZIP，不是整个开发包。仓库源码用户也可直接导入 `examples/extensions/` 下的单个示例目录。

## 2. 选择你的路径

### 已有兼容的控制台

1. 打开官方控制台，连接能被当前控制台接受的设备，确认设备已就绪。
2. 进入扩展管理，导入 `observe_prompt.zip` 或 `observe_prompt` 目录，再启用。
3. 在控制台中确认要触发的提示词已写入设备，并且有实际的实体触发配置；不要把空槽位直接当成可用触发。
4. 触发该槽位，查看扩展日志中的 `prompt.triggered` 和内容。观察示例不阻止默认粘贴。
5. 再试 `claim_prompt`：在控制台将一个已配置槽位绑定到 `Use prompt` 动作，然后实体触发。示例打印调用并报告完成，不执行额外外部操作。
6. 需要改键提案时再读 [propose_mapping 说明](examples/extensions/propose_mapping/README.md)。它建议将 `key.12` 改为 Enter，启动即提出一次建议，并保持在线等待用户审阅，仍需用户批准和确认写入。

本页步骤描述源码已经实现的接口流程，具体安装包界面可能不同。当前发布没有提供新的控制台下载，未持有合适宿主的用户请走下面的离线路径。

### 尚无兼容宿主，先阅读和开发

使用 Python 3.12 或 3.13，在解压后的根目录创建虚拟环境。macOS / Linux：

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -e ./sdk/python
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

Windows PowerShell：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ./sdk/python
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest -q
```

Windows 命令是环境准备说明，本次未在 Windows 上验收。离线测试不连接硬件，也不启动完整官方控制台。

不要直接运行示例 `main.py` 来替代控制台：SDK 需要 Runner 注入的本地 API 地址和扩展 ID，缺少时会报 `missing runner environment`。

## 3. 改成自己的动作

先复制一个示例，修改显示名称和唯一扩展 ID，再更改 Python 逻辑。控制台导入的是受管副本；修改开发目录后要停用并按版本说明替换或重新导入，重启旧副本不会自动更新源码。

具体方法见 [开发流程](docs/development-guide.md) 和 [API 参考](docs/api.md)。
