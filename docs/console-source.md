# Console 0.3.0 社区源码

本次 `v0.1.0-preview.11` 把控制台公开源码从 0.2.1 同步到已正式发布的 **0.3.0**（官方安装包见 [console-v0.3.0](https://github.com/JoeySystem/boring-developer-kit/releases/tag/console-v0.3.0)）。公开提交保存社区适配结果，没有导入私人 Git 历史。

0.3.0 是界面重做的一版：Mode、Prompt、Settings 三个页签；灯光、固件更新、摇杆校准改为方块墙；蓝牙连接分三步显示并复用本机保存的配置；设置页“遇到问题”可以复制带诊断信息的提示词给电脑上的 AI 助手；Claude / Codex 状态灯分灯、方案拖动排序、减弱动态效果等。也包含 0.2.3 的蓝牙读配置超时修复。

社区版和官方版的差别：

- 使用 BORING Console Community 独立设置、系统字体和公开的二维设备示意；不含官方图标与图形 Logo、点阵字库、3D 设备模型、3D 连接动画、新手引导动画和屏幕素材。代码中的品牌名称不意味着品牌素材授权。
- **不含豆包联动**：官方版会读取豆包桌面版的本地数据、并通过系统辅助功能 / UI Automation 打开对话，把豆包对话显示到状态灯上。这部分涉及第三方应用的窗口和数据，暂不公开；社区版保留一个占位模块，豆包在设置页里始终显示为未运行。设备端协议（`doubao_status.py`）照常保留。
- 不内置官方恢复固件 ZIP；固件页的“恢复官方固件”会改为让你选择官方签名 ZIP（见[固件 Release](https://github.com/JoeySystem/boring-developer-kit/releases/tag/firmware-20261123.01)）。
- 打包脚本只能生成社区版；应用构建来源为 custom，软件与固件在线源留空，不能自动替换官方安装版。

保留设备认证、配置冲突与写入确认、固件签名验证。不包含生产私钥、设备私密数据、部署配置、生产签发工具或内部交付记录。

协议资料同步到 0.3.0 对应的版本（含桌面 Agent 状态灯、升级后确认等），SDK 仍为 1.1.0 / API 1.1。固件源码同步到 20261123.01。官方安装包与社区源码分别交付；源码 wheel 不是 DMG/EXE。

从 [console/README.md](../console/README.md) 开始；结果与限制见 [本次验证](verification-sync-20261124.md)。许可保持 [PolyForm Noncommercial 1.0.0](../LICENSING.md)，按 [Best Effort](../SUPPORT.md) 维护。
