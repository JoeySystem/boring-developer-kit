"""Approved Companion teaching content; intentionally independent of device I/O."""

from dataclasses import dataclass

ONBOARDING_COMPLETED_KEY = "ui/onboarding_completed_v1"


@dataclass(frozen=True)
class Step:
    title: tuple[str, str]
    description: tuple[str, str]
    clip: str
    cta: tuple[str, str]
    help: tuple[str, str]
    answer: tuple[str, str]
    page: str = "overview"


ONBOARDING_STEPS = (
    Step(
        ("打开 MIST", "Turn on MIST"),
        (
            "长按旋钮，等屏幕亮起后松手。新设备第一次开机，屏幕会先出现 MAC／WIN，转旋钮选你的电脑系统，按下确认。",
            "Hold the knob until the screen lights up, then release. On a new MIST's first start, the screen shows MAC / WIN first: turn the knob to pick your computer's system, then press to confirm.",
        ),
        "power-on",
        ("屏幕亮了，继续", "Continue"),
        ("屏幕没亮？", "Screen still off?"),
        (
            "先用 USB-C 数据线给 MIST 供电，再长按旋钮。如果仍没有反应，换一根数据线或一个 USB 接口试试。",
            "Connect MIST to power with a USB-C cable, then hold the knob. If the screen stays off, try another cable or USB port.",
        ),
    ),
    Step(
        ("连接这台电脑", "Connect to this computer"),
        (
            "用 USB-C 数据线连接 MIST 和电脑，就可以开始设置。",
            "Connect MIST to your computer with a USB-C data cable to start configuring.",
        ),
        "usb-connect",
        ("已连接，继续", "Continue"),
        ("找不到设备？", "Device not found?"),
        (
            "确认数据线支持数据传输，并直接连接电脑的 USB 接口。也可以通过蓝牙连接控制台并修改配置；固件升级需要 USB。",
            "Use a data cable connected directly to your computer. You can also configure through Bluetooth; firmware updates require USB.",
        ),
    ),
    Step(
        ("打开功能中心", "Open the function menu"),
        (
            "长按右上角的功能键，出现菜单后松手。",
            "Hold the top-right function key until the menu appears, then release.",
        ),
        "function-open",
        ("继续", "Continue"),
        ("找不到功能键？", "Which key is it?"),
        (
            "功能键在与屏幕平行的那一排，设备正常摆放时最右边的一颗。跟随橙色高亮，长按到菜单出现后松手。",
            "The function key is the rightmost key in the row parallel to the screen when MIST faces you. Follow the orange highlight.",
        ),
    ),
    Step(
        ("旋转选择，按下进入", "Turn to choose. Press to enter."),
        (
            "转动旋钮选中「设置」，再按一下旋钮。",
            "Turn the knob to select Settings, then press the knob once.",
        ),
        "select-confirm",
        ("继续", "Continue"),
        ("查看操作帮助", "See how it works"),
        (
            "顺时针和逆时针都可以切换选项。选中想要的项目后，短按旋钮进入。",
            "Turn in either direction to change your selection. Press the knob once to enter the selected item.",
        ),
    ),
    Step(
        ("用摇杆切换设置", "Move between settings"),
        (
            "左右拨动摇杆，切换设置项。松手后，摇杆自动回中。",
            "Move the joystick left or right to change settings. Release it to return to center.",
        ),
        "joystick-left-right",
        ("继续", "Continue"),
        ("查看操作帮助", "See how it works"),
        (
            "轻轻向左或向右拨动即可，不需要一直按住。在设置栏中，上下拨动不会切换设置项。",
            "A gentle left or right movement is enough. You don't need to hold it. Moving up or down does not navigate this settings menu.",
        ),
    ),
    Step(
        ("随时回到首页", "Find your way back"),
        (
            "短按功能键返回上一层，长按回到首页。",
            "Press the function key once to go back one level. Hold it to return home.",
        ),
        "back-home",
        ("继续", "Continue"),
        ("查看操作帮助", "See how it works"),
        (
            "不确定自己在哪一层时，长按与屏幕同排、最右侧的功能键，就可以回到首页。",
            "If you lose your place, hold the rightmost function key in the screen's row to return home.",
        ),
    ),
    Step(
        ("准备好了", "You're ready"),
        (
            "现在可以开始使用 MIST。按键和灯光，之后都能慢慢调整。",
            "Start using MIST. You can customize keys and lighting whenever you're ready.",
        ),
        "ready",
        ("开始使用", "Start using MIST"),
        ("再看一次引导", "Review the guide"),
        (
            "可以从头再看一遍，也可以点击下方的进度点，回到想复习的操作。",
            "Start over or use the progress dots to revisit any step.",
        ),
    ),
)

# 引导蓝牙页正式稿 v0.3 (design decision): only the first pairing is demonstrated; switching
# between paired computers is one sentence. Bluetooth has its own "device not found" answer.
BLUETOOTH_STEP = Step(
    ("用蓝牙连接电脑", "Connect with Bluetooth"),
    (
        "按住功能键，再按住语音键 2 秒，屏幕出现 NEW PC 后，到电脑的蓝牙设置里选 BORING。以后连过几台电脑，按住功能键再短按语音键，就能切到下一台。",
        "Hold the function key, then hold the voice key for 2 seconds. When the screen shows NEW PC, choose BORING in your computer's Bluetooth settings. Once you've paired more than one computer, hold the function key and tap the voice key to switch to the next one.",
    ),
    "bluetooth-pair",
    ("已配对，继续", "Continue"),
    ("找不到设备？", "Device not found?"),
    (
        "电脑的蓝牙列表里没有 BORING？在设备上再按住功能键、按住语音键 2 秒，屏幕出现 NEW PC 后，3 分钟内在电脑上选它。BORING 正连着别的电脑时，新电脑搜不到它，也要先这样按。还是不行，先用数据线连接。",
        "Don't see BORING in your Bluetooth list? On the device, hold the function key and hold the voice key for 2 seconds again. When the screen shows NEW PC, choose BORING on your computer within 3 minutes. While BORING is connected to another computer, new computers can't find it until you do this. Still stuck? Connect with a cable first.",
    ),
)

# Times are authored demo inputs. They do not assert that a physical device responded.
# The screen frames themselves are native firmware renders in timeline.json.
CUES = {
    "power-on": [
        (0, "hand-tap", "按住旋钮约 6 秒", "Hold the knob for about 6 s"),
        (6300, "sun", "屏幕亮起，松开旋钮", "Screen on. Release the knob."),
        (10650, "check", "开机完成", "Ready"),
        (
            11500,
            "arrows-clockwise",
            "第一次开机：转旋钮选电脑系统，按下确认",
            "First start: turn to pick your computer's system, press to confirm",
        ),
    ],
    "function-open": [
        (0, "hand-tap", "按住右上角功能键", "Hold the function key"),
        (1600, "squares-four", "功能中心已打开", "Function menu opened"),
    ],
    "select-confirm": [
        (0, "arrows-clockwise", "转动旋钮，选择设置", "Turn to select Settings"),
        (2400, "hand-tap", "选中设置，按下旋钮", "Press to enter Settings"),
        (2800, "check", "进入设置", "Settings opened"),
    ],
    "knob-press": [
        (0, "hand-tap", "按下旋钮", "Press the knob"),
        (800, "check", "进入设置", "Settings opened"),
    ],
    "knob-clockwise": [
        (
            0,
            "arrow-clockwise",
            "顺时针转动，切换选项",
            "Turn clockwise to change selection",
        )
    ],
    "knob-counterclockwise": [
        (
            0,
            "arrow-counter-clockwise",
            "逆时针转动，切换选项",
            "Turn counterclockwise to change selection",
        )
    ],
    "joystick-left-right": [
        (0, "arrows-horizontal", "向右拨一下，换到下一项", "Move right once for the next item"),
        (850, "check", "松手，摇杆回中", "Release to center"),
        (1700, "arrows-horizontal", "向左拨一下，回到上一项", "Move left once to go back"),
    ],
    "back-home": [
        (0, "hand-tap", "短按功能键，返回上一层", "Tap the function key to go back"),
        (1200, "hand-tap", "长按功能键，回到首页", "Hold the function key to go home"),
        (3500, "check", "已回到首页", "Home screen"),
    ],
    "bluetooth-pair": [
        (0, "hand-tap", "按住功能键，再按住语音键 2 秒", "Hold Function, then hold Voice for 2 s"),
        (2650, "bluetooth", "等待新电脑，最多 3 分钟", "Waiting for a new computer (up to 3 min)"),
        (5200, "bluetooth", "到电脑的蓝牙设置里选 BORING", "Choose BORING in your computer's Bluetooth settings"),
        (6500, "check", "已连接", "Connected"),
    ],
    "usb-connect": [
        (0, "usb", "找到顶部的数据接口", "Find the port at the top"),
        (900, "usb", "沿接口方向插入数据线", "Insert the cable straight into the port"),
        (4300, "check", "插到底即可", "Push the connector fully in"),
    ],
    "ready": [(0, "check", "可以开始使用了", "Ready to use")],
}


def cue_at(clip: str, milliseconds: int, english: bool = False) -> tuple[str, str]:
    cues = CUES[clip]
    cue = next((row for row in reversed(cues) if milliseconds >= row[0]), cues[0])
    return cue[1], cue[3 if english else 2]
