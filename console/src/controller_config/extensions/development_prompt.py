from __future__ import annotations

from controller_config.extensions.contracts import ExtensionContext


def build_extension_development_prompt(context: ExtensionContext) -> str:
    """Create the self-contained task users can paste into an external AI."""

    device = context.device_serial or "当前未连接设备"
    hardware = context.identity.get("hardware_id", "未知硬件")
    return f"""请为 BORING 控制台开发一个本地 Python 扩展。

先用普通用户能理解的语言问我最多 3 个问题，只确认：
1. 我希望实体操作最终完成什么；
2. 希望由哪个提示词槽位或实体动作触发；
3. 完成后需要什么可见反馈。
不要询问串口、HID Usage、线程、IPC 或打包细节；这些由你按下面的合同决定。

当前设备上下文：
- device_serial: {device}
- hardware_id: {hardware}
- BORING Extension API: 1.0

交付要求：
- 交付一个目录或 ZIP，根目录必须包含 boring-extension.json 与 manifest 指定的 .py 入口；
- manifest_version=1，声明唯一反向域名 id、name、version、api_version、entrypoint、observer_events 和 actions；
- V1 只使用 Python 标准库与 boring_console_sdk；
- 可使用 get_context() 读取只读上下文；
- observer 可订阅 prompt.triggered，但不能阻止默认动作；
- 需要承接实体动作时，在 manifest 声明 action，并用 next_action()/respond_action() 返回 accepted、rejected、completed 或 failed；
- 需要修改按键映射时，只提交 set_mapping proposal，由用户在控制台审阅并走现有写入确认；
- 禁止打开设备串口、发送原始协议命令、调用固件维护、绕过用户确认写设备；
- 不依赖用户电脑预装 Python，也不要调用 sys.executable；扩展由 BORING 私有 Runner 启动；
- 给出 README，说明功能、所需实体绑定、测试步骤和失败时的回退行为。

完成提问并理解需求后，直接生成完整扩展包文件内容，不要求我决定技术实现细节。
"""
