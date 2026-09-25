# 四个扩展示例

四个示例配合 SDK 1.1.0：前三个保持 API 1.0 用法，save_host_task 要求 API 1.1。宿主可使用兼容的官方控制台，或从本仓库 [console/](../console/README.md) 运行社区控制台。可以从 Releases 下载各自 ZIP，也可将本目录 `extensions/` 下的单个示例目录导入兼容控制台。

| 示例 | 行为 | 是否需要动作绑定 |
|---|---|---|
| [observe_prompt](extensions/observe_prompt/README.md) | 打印当前设备和收到的提示词事件，保留默认粘贴 | 不需要扩展 action 绑定，但设备必须有实际提示词触发 |
| [claim_prompt](extensions/claim_prompt/README.md) | 接受绑定调用，打印调用并报告完成 | 需要将设备槽位绑定到 `Use prompt` |
| [propose_mapping](extensions/propose_mapping/README.md) | 启动时提出 `key.12 → Enter` 建议，保持在线等待审阅 | 无动作绑定；后续审阅和写入需用户确认 |

| [save_host_task](extensions/save_host_task/README.md) | 将电脑端任务事件保存到临时目录，可响应停止请求 | 将电脑端任务绑定到 `save_event` |

本轮验证范围见 [发布验证](../docs/verification.md)，源码流程、合成事件测试和真实设备触发分别记录。

示例不是热更新目录。修改源码后需替换或重新导入控制台受管副本。创建自己的扩展时修改唯一 ID，保留 LICENSE / NOTICE，按非商业许可范围使用。
