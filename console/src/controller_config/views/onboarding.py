from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from controller_config.i18n import set_translatable_text


ONBOARDING_COMPLETED_KEY = "ui/onboarding_completed_v1"


@dataclass(frozen=True)
class OnboardingStep:
    title: str
    description: str
    action: str
    page: str


ONBOARDING_STEPS = (
    OnboardingStep(
        "连接你的 BORING 设备",
        "首次配置建议使用 USB-C 数据线连接 BORING MIST 并正常开机，无需进入 BOOT。等待右上角显示“已认证”；未连接时也可以阅读本引导。",
        "若未识别，换一根支持数据传输的线，再到设置中重新扫描。",
        "overview",
    ),
    OnboardingStep(
        "设置一个常用按键",
        "打开“按键配置”，点击中间设备图上的按键、旋钮或摇杆。在右侧录制快捷键，或选择动作；录制时按电脑键盘上的目标组合键。",
        "先改一个常用按键，检查设备当前值与本地草稿的区别。",
        "overview",
    ),
    OnboardingStep(
        "让修改真正生效",
        "“保存本地草稿”不会改变设备。要让实体按键使用新设置，请点击“写入设备”或“保存到设备”，确认写入，等待读回成功后再按实体按键验证。",
        "写入过程中保持连接；失败或结果未知时，先重新连接并读取设备，不要把草稿当成已生效。",
        "overview",
    ),
    OnboardingStep(
        "配置四个快捷提示词",
        "打开“快捷提示词”，选择上、右、下、左中的一个槽位，填写名称与正文，点击“写入设备并读回”。使用时先把光标放在目标输入框，并保持控制台助手运行、相关权限可用。",
        "长按 Key12 约 0.8 秒 → 摇杆选择 → 短按旋钮确认。Key3 取消，10 秒无操作退出；粘贴后不会自动按 Enter。",
        "prompts",
    ),
    OnboardingStep(
        "调整外观与反馈",
        "打开“外观与反馈”，调整灯光、震动和屏幕。中间设备图是本地效果示意；开启“在设备上实时预览”才能临时查看实体效果，预览不会自动保存。",
        "满意后保存到设备。自定义图标可单项替换或恢复默认，不需要一起更换。",
        "lighting",
    ),
    OnboardingStep(
        "需要自动化时再用 Playground",
        "Playground 用于配置自动化和扩展。第一次使用可以先跳过；需要时进入页面，按功能说明完成配置和测试，再启用对应操作。",
        "先确认基础按键和提示词可用，再逐步添加自动化。",
        "actions",
    ),
    OnboardingStep(
        "以后从设置找到帮助",
        "“设置”中可重新查看使用引导、切换语言、打开诊断和固件维护。收到官方固件维护 ZIP 包后，可在固件维护中导入，核对信息并确认安装。",
        "升级时保持 USB 连接，等待设备重启及版本读回；不要只导入裸 .bin 文件。",
        "settings",
    ),
)


class OnboardingDialog(QDialog):
    """Short first-run guide that stays separate from device state."""

    page_requested = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent, Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self.setObjectName("onboardingDialog")
        self.setModal(True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setMinimumSize(620, 570)
        self.resize(680, 600)
        self._step_index = 0

        outer = QVBoxLayout(self)
        outer.setContentsMargins(18, 18, 18, 18)
        card = QFrame(objectName="onboardingCard")
        outer.addWidget(card)

        layout = QVBoxLayout(card)
        layout.setContentsMargins(34, 28, 34, 28)
        layout.setSpacing(18)

        header = QHBoxLayout()
        brand = QLabel("BORING CONSOLE", objectName="onboardingBrand")
        brand.setProperty("boringI18nSkip", True)
        header.addWidget(brand)
        header.addStretch(1)
        close = QPushButton("×", objectName="onboardingClose")
        close.setAccessibleName("暂时跳过使用引导")
        close.setToolTip("暂时跳过")
        close.clicked.connect(self.reject)
        header.addWidget(close)
        layout.addLayout(header)

        self._progress = QLabel(objectName="onboardingProgress")
        self._title = QLabel(objectName="onboardingTitle")
        self._title.setWordWrap(True)
        self._description = QLabel(objectName="onboardingDescription")
        self._description.setWordWrap(True)
        self._description.setAlignment(Qt.AlignmentFlag.AlignTop)
        layout.addWidget(self._progress)
        layout.addWidget(self._title)
        layout.addWidget(self._description)

        action_card = QFrame(objectName="onboardingAction")
        action_layout = QHBoxLayout(action_card)
        action_layout.setContentsMargins(18, 16, 18, 16)
        action_layout.setSpacing(14)
        self._step_number = QLabel(objectName="onboardingStepNumber")
        self._action = QLabel(objectName="onboardingActionText")
        self._action.setWordWrap(True)
        action_layout.addWidget(self._step_number)
        action_layout.addWidget(self._action, 1)
        layout.addWidget(action_card)
        self._open_page = QPushButton("打开对应页面", objectName="onboardingOpenPage")
        self._open_page.setProperty("buttonRole", "secondary")
        self._open_page.clicked.connect(self.open_step_page)
        layout.addWidget(self._open_page)
        layout.addStretch(1)

        footer = QHBoxLayout()
        footer.setSpacing(10)
        self._dots = QLabel(objectName="onboardingDots")
        self._dots.setProperty("boringI18nSkip", True)
        footer.addWidget(self._dots)
        footer.addStretch(1)
        self._skip = QPushButton("暂时跳过", objectName="onboardingSkip")
        self._skip.setProperty("buttonRole", "secondary")
        self._skip.clicked.connect(self.reject)
        footer.addWidget(self._skip)
        self._back = QPushButton("上一步", objectName="onboardingBack")
        self._back.setProperty("buttonRole", "secondary")
        self._back.clicked.connect(self.previous_step)
        footer.addWidget(self._back)
        self._next = QPushButton("下一步", objectName="onboardingNext")
        self._next.setProperty("buttonRole", "primary")
        self._next.clicked.connect(self.next_step)
        footer.addWidget(self._next)
        layout.addLayout(footer)

        self._render_step()

    @property
    def step_index(self) -> int:
        return self._step_index

    def open_step_page(self) -> None:
        page = ONBOARDING_STEPS[self._step_index].page
        self.accept()
        self.page_requested.emit(page)

    def next_step(self) -> None:
        if self._step_index == len(ONBOARDING_STEPS) - 1:
            self.accept()
            return
        self._step_index += 1
        self._render_step()

    def previous_step(self) -> None:
        if self._step_index == 0:
            return
        self._step_index -= 1
        self._render_step()

    def _render_step(self) -> None:
        step = ONBOARDING_STEPS[self._step_index]
        set_translatable_text(
            self._progress,
            f"第 {self._step_index + 1} 步，共 {len(ONBOARDING_STEPS)} 步",
        )
        set_translatable_text(self._title, step.title)
        set_translatable_text(self._description, step.description)
        self._step_number.setText(f"{self._step_index + 1:02d}")
        self._step_number.setProperty("boringI18nSkip", True)
        set_translatable_text(self._action, step.action)
        self._dots.setText(
            "  ".join(
                "●" if index == self._step_index else "○"
                for index in range(len(ONBOARDING_STEPS))
            )
        )
        self._back.setVisible(self._step_index > 0)
        set_translatable_text(
            self._next,
            "开始配置" if self._step_index == len(ONBOARDING_STEPS) - 1 else "下一步",
        )
