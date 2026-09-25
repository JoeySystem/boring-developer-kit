# BORING 设备事件合同 v1

BORING 控制台内部事件总线、本地自动化脚本和本地扩展 API 统一使用同一个
UTF-8 JSON 事件信封。v1 定义由权威 USB CDC 协议及当前主机实现提供的
语义事件，不暴露串口帧、HID 报告或原始输入采样。

## 事件信封

```json
{
  "schema_version": 1,
  "kind": "prompt.triggered",
  "source": "usb.prompt",
  "device_serial": "CP01-AABBCCDDEEFF",
  "event_id": 7,
  "payload": {
    "prompt_id": 2,
    "prompt_name": "快捷提示词",
    "prompt_body": "你好 BORING",
    "body_bytes": 13
  }
}
```

- `schema_version`：事件合同版本，当前固定为 `1`；
- `kind`：稳定的语义事件名；
- `source`：产生事件的已确认通道，不代表扩展可直接访问该通道；
- `device_serial`：HELLO 已核对的目标设备序列号；
- `event_id`：固件事件队列返回的非负整数 ID；
- `payload`：只包含该事件合同声明的字段。

未知顶层字段、未知事件类型、错误来源或不完整 payload 会被控制台拒绝，不会作为
另一种事件静默转发。

## `prompt.triggered`

旧提示词事件 `prompt.triggered` 保持原有内容与流程：

1. 后台助手约每 100 ms 调用 `GET_PROMPT_EVENT`；
2. 固件返回 `event_id` 和 `prompt_id`；
3. 控制台用 `GET_PROMPT` 读取该槽位当次名称和 UTF-8 正文；
4. 控制台补齐 `prompt_name`、`prompt_body` 和 `body_bytes` 后分发事件。

`body_bytes` 必须等于 `prompt_body` 的 UTF-8 字节长度。`prompt_id` 当前位于
`1..12`。Observer 只能看到事件副本；已绑定 Action、本地自动化和 Unicode 粘贴
仍按控制台既定优先级执行。

## `host_action.triggered`（扩展 API 1.1）

```json
{
  "schema_version": 1,
  "kind": "host_action.triggered",
  "source": "usb.host_action",
  "device_serial": "CP01-AABBCCDDEEFF",
  "event_id": 8,
  "payload": {"action_id": 1, "task_token": "0123456789abcdef0123456789abcdef"}
}
```

`GET_HOST_ACTION_EVENT`（0x1E）轮询返回任务引用及事件序号，建议间隔 100ms。
只有设备声明 `features.host_action_usb=true` 才启用此路径；当前仅 USB、普通模式、
Matrix12 第 8–12 键。`action_id` 是 1–255 的设备任务引用，Console 按设备 serial
查找本机已应用任务，并核对32位小写hex UUID `task_token`。编号复用而token不同
时不得运行旧任务；编辑同一任务保留token，新任务使用新token。它不是提示词 ID，不含名称、正文或 `body_bytes`，也不取提示词正文。

扩展须声明 API 1.1 才能接收此事件；订阅 Observer 时还须在 `observer_events` 声明
`host_action.triggered`。Observer 只观察，不参与具名 Action 的执行权。
动作未配置、被停用或失败时报告问题，不回退 Unicode 粘贴，不自动重放任务。
断线及监听过期会丢弃尚未交付的旧事件。固件 event_id 在一次启动内标识事件，
不可脱离设备会话作为跨设备或跨重启的永久主键。

## 内部手动试运行

自动化页的“试运行”也使用同一版本化信封，但其 `kind` 是
`automation.manual_test`、`source` 是 `console`、`event_id` 是 `null`。它是控制台
内部测试调用，不是设备事件，不会广播给扩展 Observer。

## v1 边界

v1 尚未定义普通按键、摇杆方向、旋钮旋转、设备模式切换或原始 HID 事件。只有当
权威协议或已实现的主机事件源能够稳定提供对应语义和数据时，才会在后续合同版本中
增加；扩展不得根据 `prompt.triggered` 推断这些未声明事件。
