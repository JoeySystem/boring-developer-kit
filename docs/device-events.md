# BORING 设备事件合同 v1

BORING 控制台内部事件总线、本地自动化脚本和本地扩展 API 统一使用同一个
UTF-8 JSON 事件信封。v1 只定义已经由权威 USB CDC 协议和当前主机链路真实产生的
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

当前唯一对外设备事件是 `prompt.triggered`：

1. 后台助手约每 100 ms 调用 `GET_PROMPT_EVENT`；
2. 固件返回 `event_id` 和 `prompt_id`；
3. 控制台用 `GET_PROMPT` 读取该槽位当次名称和 UTF-8 正文；
4. 控制台补齐 `prompt_name`、`prompt_body` 和 `body_bytes` 后分发事件。

`body_bytes` 必须等于 `prompt_body` 的 UTF-8 字节长度。`prompt_id` 当前位于
`1..12`。Observer 只能看到事件副本；已绑定 Action、本地自动化和 Unicode 粘贴
仍按控制台既定优先级执行。

## 内部手动试运行

自动化页的“试运行”也使用同一版本化信封，但其 `kind` 是
`automation.manual_test`、`source` 是 `console`、`event_id` 是 `null`。它是控制台
内部测试调用，不是设备事件，不会广播给扩展 Observer。

## v1 边界

v1 尚未定义普通按键、摇杆方向、旋钮旋转、设备模式切换或原始 HID 事件。只有当
权威协议或已实现的主机事件源能够稳定提供对应语义和数据时，才会在后续合同版本中
增加；扩展不得根据 `prompt.triggered` 推断这些未声明事件。
