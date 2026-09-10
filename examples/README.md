# 三个官方扩展示例

示例均使用 SDK 1.0.0 / 本地 API 1.0。可以从 Releases 下载各自 ZIP，也可将本目录 `extensions/` 下的单个示例目录导入兼容控制台。

| 示例 | 行为 | 是否需要动作绑定 |
|---|---|---|
| [observe_prompt](extensions/observe_prompt/README.md) | 打印当前设备和收到的提示词事件，保留默认粘贴 | 不需要扩展 action 绑定，但设备必须有实际提示词触发 |
| [claim_prompt](extensions/claim_prompt/README.md) | 接受绑定调用，打印调用并报告完成 | 需要将设备槽位绑定到 `Use prompt` |
| [propose_mapping](extensions/propose_mapping/README.md) | 启动时提出 `key.12 → Enter` 建议，保持在线等待审阅 | 无动作绑定；后续审阅和写入需用户确认 |

示例不是热更新目录。修改源码后需替换或重新导入控制台受管副本。创建自己的扩展时修改唯一 ID，保留 LICENSE / NOTICE，按非商业许可范围使用。
