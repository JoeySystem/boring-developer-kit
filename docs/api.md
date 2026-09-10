# Python SDK 1.0.0 API

包名 `boring-console-sdk`，导入名 `boring_console_sdk`。SDK 是阻塞式的本地 API 客户端，连接地址和已登记扩展 ID 由控制台 Runner 提供。

```python
from boring_console_sdk import BoringConsoleClient

with BoringConsoleClient.from_environment() as console:
    context = console.get_context()
    print(context)
```

`from_environment()` 构造客户端；`with` 进入时才连接和握手，退出时关闭。不要自行伪造环境变量或把 socket 地址硬编码到扩展。

## 公开方法

| 方法 | 返回值与行为 |
|---|---|
| `BoringConsoleClient.from_environment(timeout_ms=2000)` | 从 Runner 环境构造客户端；默认请求超时 2000 ms |
| `connect()` / `close()` | 建立握手 / 关闭连接；返回 `None` |
| `get_context()` | 当前 JSON 上下文字典；关键字段见下文 |
| `subscribe_events(events=("prompt.triggered",))` | 返回订阅成功的事件名 tuple；只能订阅 manifest 已声明事件 |
| `next_event(timeout_ms=None)` | 完整事件字典，等候超时返回 `None`；`None` 超时参数表示使用客户端默认值 |
| `events()` | 持续获取事件的迭代器；保持循环直到断开或异常 |
| `next_action(timeout_ms=None)` | 调用字典，等候超时返回 `None` |
| `respond_action(invocation_id, status, message="")` | 报告 `accepted`、`rejected`、`completed` 或 `failed`，返回 `None` |
| `propose_mapping(proposal)` | 返回包含 `proposal_id`、`status`、`message` 的提案结果；不直接写设备 |
| `is_connected` | 只读连接状态；不代表设备或动作已经完成 |
| `extension_id` | 当前扩展 ID |

同一扩展 ID 只使用一个客户端。需要同时观察事件和处理动作时，用同一个客户端交替调用短超时的 `next_event()` 和 `next_action()`；不要用阻塞的 `events()` 循环让动作处理一直得不到执行。

## 上下文和事件

`get_context()` 包含 `schema_version`、`revision`、`device_serial`、`connection_state`、`identity`、`compatibility`、`capabilities`、`status`、`active_profile`、`config_summary`、`draft`、`prompt_listener`。未连接时设备相关字段可能为空，先判断再使用。

`config_summary` 中的 generation/digest 和活动 Profile 用于构造提案，不能把它们缓存后当成永久有效的设备状态。字段变化后应重新读取上下文。

事件的完整字段及 UTF-8 正文语义见 [device-events.md](device-events.md)。当前唯一公开设备事件是 `prompt.triggered`。

## 动作处理

`next_action()` 返回 `schema_version`、`invocation_id`、`extension_id`、`action_id`、`device_serial`、`context_revision` 和嵌套 `event`。

收到调用后，先决定接受或拒绝。接受的动作在实际完成后报告 `completed`，出错报告 `failed`。不要一接到调用就把未执行的事情报告为完成。已接受后失败不会再次粘贴或重放动作。

[claim_prompt 示例](../examples/extensions/claim_prompt/main.py)只打印调用，因此可以随即报告完成；添加耗时逻辑时必须让状态与真实执行结果对应。控制台的动作接受时限不同于 SDK 的请求超时，不能通过增大 SDK timeout 延长控制台等待。

## 映射提案

提案包含 `schema_version=1`、唯一 `proposal_id`、当前 `extension_id`、目标 `device_serial`、`base_generation`、`base_digest`、`profile_id`、`control_id` 和符合 [配置 Schema](../protocol/config-schema.json) 的 `action`。

[propose_mapping 示例](../examples/extensions/propose_mapping/main.py)从当前上下文构造提案。它建议 `key.12` 使用 HID usage 40（Enter），只是建议；用户批准到草稿之后仍需在控制台确认写入。

结果状态可能为 `pending`、`blocked`、`stale`、`approved` 或 `rejected`。应显示服务端返回的 message；不能把请求成功或 `pending` 当成设备已经完成改键。

## 错误与超时

- `BoringConsoleError`：SDK 异常基类。
- `ConnectionError`：Runner 环境缺失、连接失败、断连或请求超时。
- `RequestError`：请求被拒绝或收到无效响应；`.code` 为机器可读错误码，异常字符串为说明。
- `ValueError`：非法参数，例如负的读取超时或不支持的动作状态。

等待事件或动作超时返回 `None`；请求超时会抛异常，这两种情况不同。SDK 不自动重新连接、不重放历史动作，也不提供串口、原始命令、固件安装或配置写入接口。
