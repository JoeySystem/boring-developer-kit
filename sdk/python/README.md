# BORING Console Python SDK 1.0

本 SDK 只用于由 BORING 控制台专用 Runner 启动的本地扩展。正式用户不需要安装
Python，也不需要单独安装 SDK；Runner 会随控制台携带兼容版本。

扩展入口中使用：

```python
from boring_console_sdk import BoringConsoleClient

with BoringConsoleClient.from_environment() as console:
    context = console.get_context()
    console.subscribe_events(("prompt.triggered",))
    while console.is_connected:
        event = console.next_event(timeout_ms=100)
        if event is not None:
            print(event)
        invocation = console.next_action(timeout_ms=100)
        if invocation is not None:
            console.respond_action(
                invocation["invocation_id"], "accepted", "accepted"
            )
            # Execute the declared action here, then report its terminal state.
            console.respond_action(
                invocation["invocation_id"], "completed", "completed"
            )
```

每个扩展 ID 只建立一个已认证 client。需要同时处理 Observer 和 Action 时，在这个
client 上用带短超时的 `next_event()` 与 `next_action()` 交替消费；不要分别建立两个
连接。`subscribe_events()` 只能订阅 manifest 的 `observer_events` 已声明的事件。
`next_event()` 返回 BORING 设备事件合同 v1 的完整字典；当前只支持
`prompt.triggered`。字段定义见[设备事件文档](../../docs/device-events.md)。

公开方法包括：

- `get_context()`：读取不可变 JSON 上下文；
- `subscribe_events()` / `next_event()`：观察实时语义事件；
- `next_action()` / `respond_action()`：处理用户明确绑定的扩展 action；
- `propose_mapping()`：提交单个 `set_mapping` 提案，不能直接写设备。

SDK 不提供串口、原始协议、固件安装、配置确认或网络下载接口。

仅开发扩展时可在本目录运行：

```bash
python3.12 -m pip install -e .
```

控制台源码和正式安装包均通过专用 Runner 提供 SDK；扩展不得调用用户系统 Python。

## 构建与许可

在 SDK 目录执行 `python -m pip wheel --no-deps --wheel-dir ../../dist .` 构建 wheel。SDK 自身没有编译步骤，但安装需要 PySide6 6.11.1；支持的 Python 为 3.12 / 3.13，本次实际测试 3.12。

只安装 SDK wheel 不会启动本地 API。需要宿主时，可按 [社区控制台源码说明](../../console/README.md) 安装并运行本仓库的控制台。宿主版本边界见 [兼容说明](../../docs/versions.md)。完整开发包的构建方法见 [开发流程](../../docs/development-guide.md)。

本 SDK 采用 PolyForm Noncommercial 1.0.0，完整条款见随包 LICENSE 和 NOTICE；PySide6 的许可独立适用。
