# BORING 本地扩展 1.0

BORING 本地扩展是由 BORING 控制台管理、由私有 Runner 执行的 Python 扩展包。
它不是固件 MOD，也不是让第三方代码直接控制串口的驱动。用户可以在控制台复制
“AI 开发提示词”，去 Codex 或其他 AI 中完成开发，再把目录或 ZIP 导回控制台。

## 扩展包合同

扩展包根目录必须包含 `boring-extension.json`，并包含 manifest 指定的 Python
入口。最小示例：

```json
{
  "manifest_version": 1,
  "id": "com.example.my-boring-extension",
  "name": "My BORING Extension",
  "version": "1.0.0",
  "api_version": {"major": 1, "minor": 0},
  "entrypoint": "main.py",
  "observer_events": ["prompt.triggered"],
  "actions": [
    {"id": "run_task", "name": "Run task"}
  ]
}
```

`id` 使用稳定、唯一的反向域名形式。`entrypoint` 必须是包内 `.py` 相对路径。
V1 入口只依赖 Python 标准库和 `boring_console_sdk`。控制台拒绝无效 manifest、
重复 ID、缺失入口以及路径逃逸 ZIP；导入后先处于停用状态，不会自动执行代码。

## 导入和生命周期

在“扩展”页选择“导入扩展目录”或“导入 ZIP”。控制台把合法包复制到自己的受管
目录，然后允许用户启用、停用、重启和移除。启用时通过随应用交付的
`boring-extension-runner` 启动，不经 shell，也不回退到用户系统 Python。

扩展只有在进程运行并完成 API handshake 后才显示为就绪。停用、移除、崩溃或
控制台退出会撤销 action 资格并收口进程。运行输出和错误显示在扩展页。

## 本地 API 与 SDK

本地 API 1.0 是只对本机已登记扩展开放的 UTF-8 JSON Lines 通道。首条请求必须是
handshake。同一扩展 ID 只允许一个已认证 client；同时处理 Observer 和 Action 时，
应在这个 client 上交替消费事件与调用。SDK 封装以下能力：

- `get_context()`：读取连接状态、设备身份、兼容性、能力、当前状态、活动 Profile、
  配置摘要、本地草稿摘要和提示词监听状态；
- `subscribe_events()`：只订阅 manifest `observer_events` 已声明的事件；订阅成功后
  观察实时 `prompt.triggered`，不重放历史；
- `next_action()` / `respond_action()`：处理用户显式绑定的实体 action；
- `propose_mapping()`：向控制台提交一个按键映射提案。

公开对象都是 JSON 值，不包含 Qt 对象、串口名、gateway、原始命令执行器、配置写入
确认或固件维护入口。

扩展收到的事件使用控制台内部事件总线、本地自动化和本地 API 共用的
`schema_version: 1` 信封。当前唯一正式设备事件及各字段语义见
[`device-events.md`](device-events.md)。

## Observer 与 Action

Observer 只收到事件副本，不能阻止默认 Unicode 粘贴。需要接管实体事件时，扩展先
在 manifest 声明 action，再由用户在扩展页把具体设备的提示词槽位绑定到该 action。

事件优先级固定为：

```text
内置本地自动化 → 已绑定且就绪的扩展 action → Unicode 粘贴回退
```

扩展必须在控制台给定时限内返回 `accepted` 或 `rejected`。未运行、未绑定、拒绝、
超时或响应无效时，控制台只回退一次 Unicode 粘贴。扩展已经接受后再报告 `failed`
只记录失败，不会重复粘贴或重复执行。

## 配置提案

V1 只支持 `set_mapping` 提案。提案必须携带目标 serial、基础 generation、基础
digest、Profile ID、control ID 和符合当前设备 Schema 的 action。控制台先校验并
显示来源、现值、建议值和差异；用户“批准到本地草稿”后仍未写设备。

最终写入继续使用控制台既有流程：

```text
本地草稿 → VALIDATE_CONFIG → 用户确认 → SET_CONFIG
→ GET_STATUS 轮询 → GET_CONFIG 读回
```

设备断开、generation/digest 改变、目标控件消失或已有未保存草稿时，提案进入
blocked/stale，不会自动换设备、覆盖或合并。

## 官方示例

- `examples/extensions/observe_prompt`：观察并打印 `prompt.triggered`；
- `examples/extensions/claim_prompt`：声明 `use_prompt` action，接受并完成实体调用；
- `examples/extensions/propose_mapping`：为 `key.12` 提交映射提案，等待用户审阅。

源码验收可直接在扩展页导入这些目录。实体事件验收必须连接真实样机，再逐项确认
observer 不影响粘贴、action 接管、停用回退，以及提案批准后仍需用户确认写入。

## 开发与交付边界

- 不打开 USB CDC、HID 或其他设备端口；
- 不发送原始协议、配置写入或固件维护命令；
- 不通过 shell 或 `sys.executable` 启动第二套 Python；
- 不假设用户电脑预装 Python；
- V1 不包含插件商店、账号、云端分发或网络代码下载。

SDK 开发说明见 [SDK 开发说明](../sdk/python/README.md)。双平台正式包必须同时交付控制台、私有
Runner、SDK 运行内容及构建时从 `protocol/` 复制的权威协议资产。
