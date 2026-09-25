from __future__ import annotations

from controller_config.extensions.contracts import ExtensionContext
from controller_config.i18n import translate_ui_text


def build_extension_development_prompt(context: ExtensionContext) -> str:
    """Create the self-contained task users can paste into an external AI."""

    device = context.device_serial or translate_ui_text("当前未连接设备")
    hardware = context.identity.get("hardware_id", translate_ui_text("未知硬件"))
    template = """请为 BORING 控制台开发一个本地 Python 扩展。

先用普通用户能理解的语言问我最多 3 个问题，只确认：
1. 我希望实体操作最终完成什么；
2. 需要处理什么输入、把结果保存到哪里；
3. 完成后需要什么可见反馈。
不要询问串口、HID Usage、线程、IPC 或打包细节；这些由你按下面的合同决定。

当前设备上下文：
- device_serial: {device}
- hardware_id: {hardware}
- BORING Extension API: 1.1（仍兼容已有 1.0 扩展）

交付要求：
- 交付一个目录或 ZIP，根目录必须包含 boring-extension.json 与 manifest 指定的 .py 入口；
- manifest_version=1，声明唯一反向域名 id、name、version、api_version、entrypoint、observer_events 和 actions；
- 新任务的 api_version 必须为 {{"major":1,"minor":1}}；不需要观察事件时 observer_events=[]；
- V1 只使用 Python 标准库与 boring_console_sdk；
- 可使用 get_context() 读取只读上下文；
- observer 可订阅 prompt.triggered，但不能阻止默认动作；
- 需要承接实体动作时，在 manifest 声明 action，并用 next_action()/respond_action() 返回 accepted、rejected、completed 或 failed；
- 使用 from boring_console_sdk import BoringConsoleClient，再用 BoringConsoleClient.from_environment() 建立连接；
- 用户在 Console 选择包提供的具名动作、设备按键并一次应用，首次实体试用成功后启用；不要让用户填写占位提示词；
- 首期独立电脑任务仅支持受支持固件的 USB、普通模式、Matrix12 第 8–12 键；不替换 CODEX/CC 官方控件；
- next_action() 返回调用对象；顶层 action_id 是声明的动作名，invocation_id 用于回复；event.kind=host_action.triggered、source=usb.host_action；
- event.payload 只有 action_id（1–255）和 task_token（32位小写hex UUID）；这里的 action_id 是设备任务编号，不是扩展动作名，也没有提示词正文；
- 必须及时返回 accepted（接管窗口约 700ms），真正完成后才返回 completed，失败返回 failed 并说明已完成部分和如何修复；
- 长任务在步骤间用 next_cancellation(timeout_ms=0) 检查取消，停止后报告 failed；默认最长 60 秒，可在高级设置调整；
- Console 先请求协作取消，无终态时会停用整个扩展。不要声称能回滚文件或已打开应用；自行启动的外部工具需自己负责停止；
- 需要修改按键映射时，只提交 set_mapping proposal，由用户在控制台审阅并走现有写入确认；
- 禁止打开设备串口、发送原始协议命令、调用固件维护、绕过用户确认写设备；
- 不依赖用户电脑预装 Python，也不要调用 sys.executable；扩展由 BORING 私有 Runner 启动；
- Console 不自动安装第三方依赖；优先只用标准库和随 Runner 交付的 SDK，不编造 SDK 的桌面自动化能力；
- 给出 README，说明功能、运行条件、输入输出位置、绑定和真实产物核对步骤。完成/失败后不自动重试或粘贴提示词。

完成提问并理解需求后，直接生成完整扩展包文件内容，不要求我决定技术实现细节。
"""
    return translate_ui_text(template).format(device=device, hardware=hardware)
