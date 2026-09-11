from __future__ import annotations

import re
from importlib.resources import as_file, files

from PySide6.QtCore import (
    QCoreApplication,
    QEvent,
    QLibraryInfo,
    QLocale,
    QObject,
    QSettings,
    QTranslator,
    Signal,
)
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QAbstractButton,
    QApplication,
    QComboBox,
    QGroupBox,
    QLabel,
    QLineEdit,
    QMenu,
    QProgressBar,
    QWidget,
)


SIMPLIFIED_CHINESE = "zh_CN"
ENGLISH = "en_US"
SUPPORTED_LANGUAGES = (SIMPLIFIED_CHINESE, ENGLISH)
LANGUAGE_SETTING_KEY = "ui/language"
TRANSLATION_CONTEXT = "ControllerConfig"
SKIP_TRANSLATION_PROPERTY = "boringI18nSkip"


def normalize_language(value: object) -> str | None:
    text = str(value or "").replace("-", "_").lower()
    if text.startswith("zh"):
        return SIMPLIFIED_CHINESE
    if text.startswith("en"):
        return ENGLISH
    return None


def system_language() -> str:
    return normalize_language(QLocale.system().name()) or ENGLISH


def translate_ui_text(source: str) -> str:
    translated = QCoreApplication.translate(TRANSLATION_CONTEXT, source)
    application = QApplication.instance()
    language = (
        normalize_language(application.property("boringUiLanguage"))
        if isinstance(application, QApplication)
        else SIMPLIFIED_CHINESE
    )
    if translated == source and language == ENGLISH:
        return _translate_dynamic_english(source)
    return translated


def set_translatable_text(widget: QLabel | QAbstractButton, source: str) -> None:
    translated = translate_ui_text(source)
    widget.setProperty("boringI18nSource_text", source)
    widget.setText(translated)
    widget.setProperty("boringI18nRendered_text", translated)


def set_translatable_accessible_name(widget: QWidget, source: str) -> None:
    translated = translate_ui_text(source)
    widget.setProperty("boringI18nSource_accessibleName", source)
    widget.setAccessibleName(translated)
    widget.setProperty("boringI18nRendered_accessibleName", translated)


class LanguageManager(QObject):
    """Own the application language and retranslate the existing Qt widget tree."""

    language_changed = Signal(str)

    def __init__(
        self,
        application: QApplication,
        *,
        settings: QSettings | None = None,
        initial_language: str | None = None,
        use_system_default: bool = False,
        persist: bool = True,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._application = application
        self._settings = settings if settings is not None else QSettings()
        self._persist = persist
        self._translator = QTranslator(self)
        self._qt_translator = QTranslator(self)
        self._language = SIMPLIFIED_CHINESE

        stored = normalize_language(self._settings.value(LANGUAGE_SETTING_KEY))
        requested = normalize_language(initial_language)
        selected = requested or stored
        if selected is None:
            selected = system_language() if use_system_default else SIMPLIFIED_CHINESE
        self._install_language(selected)
        application.installEventFilter(self)

    @property
    def language(self) -> str:
        return self._language

    def set_language(self, language: str) -> bool:
        normalized = normalize_language(language)
        if normalized is None:
            raise ValueError(f"Unsupported UI language: {language}")
        if normalized == self._language:
            return False
        self._install_language(normalized)
        if self._persist:
            self._settings.setValue(LANGUAGE_SETTING_KEY, normalized)
            self._settings.sync()
        for widget in self._application.topLevelWidgets():
            self.retranslate_widget_tree(widget)
        self.language_changed.emit(normalized)
        return True

    def translate(self, source: str) -> str:
        return translate_ui_text(source)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if event.type() == QEvent.Type.Show and isinstance(watched, QWidget):
            self.retranslate_widget_tree(watched)
        return super().eventFilter(watched, event)

    def retranslate_widget_tree(self, root: QWidget) -> None:
        widgets = [root, *root.findChildren(QWidget)]
        actions: list[QAction] = []
        for widget in widgets:
            if widget.property(SKIP_TRANSLATION_PROPERTY) is True:
                continue
            self._translate_widget(widget)
            actions.extend(widget.actions())
            if isinstance(widget, QMenu):
                actions.append(widget.menuAction())
        seen: set[int] = set()
        for action in actions:
            identity = id(action)
            if identity in seen or action.property(SKIP_TRANSLATION_PROPERTY) is True:
                continue
            seen.add(identity)
            self._translate_object_text(action, "text", action.text, action.setText)
            self._translate_object_text(
                action, "toolTip", action.toolTip, action.setToolTip
            )

    def _install_language(self, language: str) -> None:
        self._application.removeTranslator(self._translator)
        self._application.removeTranslator(self._qt_translator)
        self._translator.deleteLater()
        self._qt_translator.deleteLater()
        self._translator = QTranslator(self)
        self._qt_translator = QTranslator(self)
        if language == ENGLISH:
            resource = files("controller_config.translations").joinpath(
                "boring_configurator_en_US.qm"
            )
            with as_file(resource) as path:
                if not self._translator.load(str(path)):
                    raise RuntimeError(f"Unable to load UI translation: {path}")
            self._application.installTranslator(self._translator)
        else:
            translations_path = QLibraryInfo.path(
                QLibraryInfo.LibraryPath.TranslationsPath
            )
            if self._qt_translator.load("qtbase_zh_CN", translations_path):
                self._application.installTranslator(self._qt_translator)
        self._language = language
        self._application.setProperty("boringUiLanguage", language)

    def _translate_widget(self, widget: QWidget) -> None:
        self._translate_object_text(
            widget, "windowTitle", widget.windowTitle, widget.setWindowTitle
        )
        self._translate_object_text(widget, "toolTip", widget.toolTip, widget.setToolTip)
        self._translate_object_text(
            widget, "statusTip", widget.statusTip, widget.setStatusTip
        )
        self._translate_object_text(
            widget,
            "accessibleName",
            widget.accessibleName,
            widget.setAccessibleName,
        )
        self._translate_object_text(
            widget,
            "accessibleDescription",
            widget.accessibleDescription,
            widget.setAccessibleDescription,
        )
        if isinstance(widget, QLabel):
            self._translate_object_text(widget, "text", widget.text, widget.setText)
        elif isinstance(widget, QAbstractButton):
            self._translate_object_text(widget, "text", widget.text, widget.setText)
        elif isinstance(widget, QGroupBox):
            self._translate_object_text(widget, "title", widget.title, widget.setTitle)
        elif isinstance(widget, QLineEdit):
            self._translate_object_text(
                widget,
                "placeholderText",
                widget.placeholderText,
                widget.setPlaceholderText,
            )
        elif isinstance(widget, QProgressBar):
            self._translate_object_text(widget, "format", widget.format, widget.setFormat)
        if isinstance(widget, QComboBox):
            self._translate_object_text(
                widget, "placeholderText", widget.placeholderText, widget.setPlaceholderText
            )
            self._translate_combo_items(widget)

    def _translate_object_text(
        self,
        obj: QObject,
        field: str,
        getter,
        setter,
    ) -> None:
        current = getter()
        if not current:
            return
        if obj.property(f"boringI18nSkip_{field}") is True:
            return
        source_key = f"boringI18nSource_{field}"
        rendered_key = f"boringI18nRendered_{field}"
        source = obj.property(source_key)
        rendered = obj.property(rendered_key)
        if not isinstance(source, str) or current != rendered:
            source = current
            obj.setProperty(source_key, source)
        translated = self.translate(source)
        if translated != current:
            setter(translated)
        obj.setProperty(rendered_key, translated)

    def _translate_combo_items(self, combo: QComboBox) -> None:
        sources = combo.property("boringI18nComboSources")
        rendered = combo.property("boringI18nComboRendered")
        current = [combo.itemText(index) for index in range(combo.count())]
        if (
            not isinstance(sources, list)
            or len(sources) != combo.count()
            or current != rendered
        ):
            sources = current
            combo.setProperty("boringI18nComboSources", sources)
        translated = [self.translate(str(source)) for source in sources]
        for index, text in enumerate(translated):
            if combo.itemText(index) != text:
                combo.setItemText(index, text)
        combo.setProperty("boringI18nComboRendered", translated)


_DYNAMIC_ENGLISH_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"^(\d+)  处未写入改动$"), r"\1  changes not written"),
    (re.compile(r"^更新于 (.+)$"), r"Updated \1"),
    (
        re.compile(r"^第 (\d+) 步，共 (\d+) 步$"),
        r"Step \1 of \2",
    ),
    (
        re.compile(r"^under_key 必须包含 (\d+) 项$"),
        r"under_key must contain \1 items",
    ),
    (
        re.compile(r"^under_key\[(\d+)\] 必须只包含 r、g、b$"),
        r"under_key[\1] must contain only r, g, and b",
    ),
    (
        re.compile(r"^under_key\[(\d+)\]\.([rgb]) 必须是 0\.\.255 的整数$"),
        r"under_key[\1].\2 must be an integer from 0 to 255",
    ),
    (
        re.compile(r"^自动化总数 (\d+)  ·  当前可用 (\d+)  ·  已绑定控件 (\d+)$"),
        r"Total Automations \1  ·  Available \2  ·  Bound Controls \3",
    ),
    (re.compile(r"^已测试 (\d+) / (\d+)$"), r"Tested \1 / \2"),
    (
        re.compile(r"^按键 (\d+)/(\d+)  ·  旋钮 (\d+)/(\d+)  ·  摇杆 (\d+)/(\d+)$"),
        r"Keys \1/\2  ·  Knob \3/\4  ·  Joystick \5/\6",
    ),
    (re.compile(r"^下一步：按下并松开按键 (\d+)。$"), r"Next: press and release key \1."),
    (re.compile(r"^设备没有进入安全诊断：(.+)$"), r"The device did not enter safe diagnostics: \1"),
    (re.compile(r"^下一步：操作 (.+) 一次。$"), r"Next: operate \1 once."),
    (
        re.compile(r"^(\d+) 个已安装 · (\d+) 个运行中$"),
        r"\1 installed · \2 running",
    ),
    (re.compile(r"^提示词槽位 (\d+)$"), r"Prompt Slot \1"),
    (re.compile(r"^(\d+)/4 个方向已配置$"), r"\1/4 directions configured"),
    (
        re.compile(r"^当前方向绑定快捷提示词 (\d+)$"),
        r"This direction is bound to quick prompt \1",
    ),
    (re.compile(r"^粘贴提示词 (\d+)$"), r"Paste prompt \1"),
    (re.compile(r"^当前 (\d+) · 已配对$"), r"Current \1 · Paired"),
    (re.compile(r"^槽位 (\d+) · 等待配对$"), r"Slot \1 · Waiting to pair"),
    (re.compile(r"^还有 (\d+) 项差异…$"), r"\1 more changes…"),
    (re.compile(r"^还有 (\d+) 项更改…$"), r"\1 more changes…"),
    (
        re.compile(r"^配置方案名称最多 (\d+) 个字符。$"),
        r"Profile names can contain at most \1 characters.",
    ),
    (
        re.compile(r"^确定从本地草稿删除“(.+)”吗？删除后仍需保存到设备才会生效。$"),
        r"Delete “\1” from the local draft? Save to the device to apply this change.",
    ),
    (re.compile(r"^(\d+) 条按键序列$"), r"\1 key sequences"),
    (re.compile(r"^运行按键序列 (\d+)$"), r"Run Key Sequence \1"),
    (re.compile(r"^(\d+) 个步骤 · 编码 (\d+) 字节 · 显式延时 (.+)$"), r"\1 steps · \2 encoded bytes · explicit delay \3"),
    (re.compile(r"^(\d+) / (\d+) 字节 · %p%$"), r"\1 / \2 bytes · %p%"),
    (re.compile(r"^当前固件版本：(.+)。$"), r"Current firmware version: \1."),
    (re.compile(r"^固件签名能力：当前构建未要求签名。$"), r"Firmware signing: not required by the current build."),
    (re.compile(r"^(\d{2}) · 空槽位$"), r"\1 · Empty slot"),
    (re.compile(r"^(\d{2}) · 槽位$"), r"\1 · Slot"),
    (re.compile(r"^(\d+)/(\d+) UTF-8 字节$"), r"\1/\2 UTF-8 bytes"),
    (
        re.compile(r"^(\d+)/(\d+) 条 · (\d+)/(\d+) UTF-8 字节$"),
        r"\1/\2 prompts · \3/\4 UTF-8 bytes",
    ),
    (
        re.compile(r"^收到实体事件 (\d+)，正在读取提示词 (\d+)$"),
        r"Received physical event \1; reading prompt \2",
    ),
    (
        re.compile(r"^已处理事件 (\d+) · 提示词 (\d+) · (\d+) UTF-8 字节$"),
        r"Handled event \1 · prompt \2 · \3 UTF-8 bytes",
    ),
    (
        re.compile(r"^事件 (\d+) 已读取提示词 (\d+)，但粘贴失败$"),
        r"Event \1 read prompt \2, but paste failed",
    ),
    (
        re.compile(r"^事件 (\d+) 的提示词读取失败，本次未粘贴$"),
        r"Prompt read failed for event \1; nothing was pasted",
    ),
    (re.compile(r"^已保存本地脚本：(.+)$"), r"Saved local script: \1"),
    (
        re.compile(r"^提示词槽位 (\d+) 已绑定其他本地脚本$"),
        r"Prompt slot \1 is already bound to another local script",
    ),
    (re.compile(r"^已启动：(.+)$"), r"Started: \1"),
    (re.compile(r"^运行完成：(.+)$"), r"Completed: \1"),
    (
        re.compile(r"^运行失败：(.+) · exit (-?\d+)$"),
        r"Failed: \1 · exit \2",
    ),
    (
        re.compile(r"^本地脚本启动失败：(.+)$"),
        r"Local script failed to start: \1",
    ),
    (
        re.compile(r"^事件 (\d+) 已交给本地脚本：(.+)$"),
        r"Event \1 dispatched to local script: \2",
    ),
    (re.compile(r"^脚本文件不存在：(.+)$"), r"Script file not found: \1"),
    (re.compile(r"^已保存自动化：(.+)$"), r"Saved automation: \1"),
    (re.compile(r"^自动化运行完成：(.+)$"), r"Automation completed: \1"),
    (
        re.compile(r"^第 (\d+) 步完成：(.+)$"),
        r"Step \1 completed: \2",
    ),
    (
        re.compile(r"^第 (\d+) 步失败：(.+)$"),
        r"Step \1 failed: \2",
    ),
)


_DYNAMIC_ENGLISH_EXACT = {
    "请先完成当前固件维护或校准，再打开配置页面。": "Finish the current firmware maintenance or calibration before opening a configuration page.",
    "连接设备并读取配置后，即可调整外观与反馈。": "Connect a device and read its configuration before adjusting Appearance & Feedback.",
    "打开对应页面": "Open This Page",
    "连接你的 BORING 设备": "Connect Your BORING Device",
    "首次配置建议使用 USB-C 数据线连接 BORING MIST 并正常开机，无需进入 BOOT。等待右上角显示“已认证”；未连接时也可以阅读本引导。": "For your first setup, connect BORING MIST with a USB-C data cable and power it on normally. BOOT mode is not required. Wait for Verified at the top right. You can read this guide without a device.",
    "若未识别，换一根支持数据传输的线，再到设置中重新扫描。": "If not detected, try another data cable and rescan in Settings.",
    "设置一个常用按键": "Set Up a Control",
    "打开“按键配置”，点击中间设备图上的按键、旋钮或摇杆。在右侧录制快捷键，或选择动作；录制时按电脑键盘上的目标组合键。": "Open Key Configuration and select a key, knob or joystick on the device diagram. Record a shortcut using your computer keyboard, or choose an action in the editor.",
    "先改一个常用按键，检查设备当前值与本地草稿的区别。": "Start with one key and compare its device value with your local draft.",
    "让修改真正生效": "Apply Your Changes",
    "“保存本地草稿”不会改变设备。要让实体按键使用新设置，请点击“写入设备”或“保存到设备”，确认写入，等待读回成功后再按实体按键验证。": "Saving a local draft does not change the device. Choose Write to Device or Save to Device, confirm, and wait for verification before testing the physical control.",
    "写入过程中保持连接；失败或结果未知时，先重新连接并读取设备，不要把草稿当成已生效。": "Keep the device connected. If the result is unknown, reconnect and read it back before assuming changes were applied.",
    "配置四个快捷提示词": "Set Up Quick Prompts",
    "打开“快捷提示词”，选择上、右、下、左中的一个槽位，填写名称与正文，点击“写入设备并读回”。使用时先把光标放在目标输入框，并保持控制台助手运行、相关权限可用。": "Open Quick Prompts, select Up, Right, Down or Left, enter a name and text, then choose Write and Verify. Focus the destination text field and keep the console helper running with the required permissions.",
    "长按 Key12 约 0.8 秒 → 摇杆选择 → 短按旋钮确认。Key3 取消，10 秒无操作退出；粘贴后不会自动按 Enter。": "Hold Key12 for about 0.8 s, select with the joystick, then press the knob to confirm. Key3 cancels; 10 s idle exits. Pasting does not press Enter.",
    "调整外观与反馈": "Appearance & Feedback",
    "打开“外观与反馈”，调整灯光、震动和屏幕。中间设备图是本地效果示意；开启“在设备上实时预览”才能临时查看实体效果，预览不会自动保存。": "Adjust lighting, haptics and display in Appearance & Feedback. The central diagram is a local illustration. Enable live device preview to try supported effects temporarily; previewing does not save them.",
    "满意后保存到设备。自定义图标可单项替换或恢复默认，不需要一起更换。": "Save to the device when satisfied. Custom icons can be replaced or restored individually.",
    "需要自动化时再用 Playground": "Explore Playground Later",
    "Playground 用于配置自动化和扩展。第一次使用可以先跳过；需要时进入页面，按功能说明完成配置和测试，再启用对应操作。": "Playground contains automation and extensions. You can skip it during first setup. When needed, follow the feature instructions to configure and test an action before enabling it.",
    "先确认基础按键和提示词可用，再逐步添加自动化。": "Get your basic keys and prompts working first, then add automation.",
    "以后从设置找到帮助": "Help and Maintenance",
    "“设置”中可重新查看使用引导、切换语言、打开诊断和固件维护。收到官方固件维护 ZIP 包后，可在固件维护中导入，核对信息并确认安装。": "Settings lets you replay this guide, change language, open diagnostics and maintain firmware. Import an official firmware maintenance ZIP in Firmware Maintenance, review its details and confirm installation.",
    "升级时保持 USB 连接，等待设备重启及版本读回；不要只导入裸 .bin 文件。": "Keep USB connected during installation and wait for reboot and version verification. Do not import a standalone .bin file.",
    "NORMAL 首页图标": "NORMAL home image",
    "功能与模式图标": "Function and mode icons",
    "选择一个图标单独修改；未修改的图标继续保留，每个图标都可单独恢复默认。": "Customize one icon at a time. Other icons stay unchanged; each icon can be restored individually.",
    "选择图标": "Select icon",
    "该图标用于系统警告或错误反馈，仅可查看，不能修改。": "This warning or error icon is view-only and cannot be changed.",
    "如首页已设置图片，需先恢复首页默认图标，才能看到此模式标志。": "If a home image is set, restore the default home image to see this mode symbol.",
    "导入静态 PNG / JPG；图片按比例缩小为单色点阵，保留原有位置、文字和状态颜色。": "Import a static PNG / JPG. It is fitted to a monochrome dot grid; position, text and status colors stay unchanged.",
    "建议使用轮廓清晰的简单图形；白色显示、黑色或透明区域隐藏，黑色图形可勾选反色。": "Use a simple, clear shape. White is visible; black or transparent areas are hidden. Invert a dark symbol if needed.",
    "反色（将黑色图形转为亮色）": "Invert (make dark shapes bright)",
    "保存本项图标": "Save selected icon",
    "本项恢复默认": "Reset This Icon",
    "请连接支持单项图标自定义的设备。": "Connect a device supporting individual custom icons.",
    "设备或选中的图标已变化，请重新确认后操作。": "The device or selected icon changed. Check the selection and try again.",
    "只替换当前选中的图标，其他图标和设置保持不变。是否继续？": "Replace only the selected icon? Other icons and settings will stay unchanged.",
    "只恢复当前选中的内置图标，其他自定义图标和设置保持不变。是否继续？": "Restore only the selected built-in icon? Other custom icons and settings will stay unchanged.",
    "NORMAL 小标志": "NORMAL small symbol",
    "CODEX 小标志": "CODEX small symbol",
    "蓝牙连接": "Bluetooth connection",
    "操作系统": "Operating system",
    "NORMAL 模式": "NORMAL mode",
    "CODEX 模式": "CODEX mode",
    "CC 模式": "CC mode",
    "当前固件未支持逐项图标自定义": "This firmware does not support individual custom icons",
    "请选择要修改的图标": "Select an icon to customize",
    "正在读取图标列表": "Reading icon list",
    "正在读取选中图标": "Reading selected icon",
    "此图标为内置系统提示，不能修改": "This built-in system alert cannot be customized",
    "请先读取该图标，确认上次操作结果": "Read this icon first to confirm the previous operation",
    "图标像素尺寸不匹配": "Icon pixel dimensions do not match",
    "正在确认选中图标的设备版本": "Checking selected icon revision",
    "正在保存选中图标": "Saving selected icon",
    "保存已响应，正在读回选中图标": "Save acknowledged; reading back selected icon",
    "此图标已保存，读回一致；其他图标不变": "Selected icon saved and verified; other icons unchanged",
    "此图标已恢复默认；其他图标不变": "Selected icon restored to default; other icons unchanged",
    "已读取选中图标": "Selected icon read",
    "读回与待保存图标不一致，本地图片保留": "Readback differs from the candidate; local image retained",
    "未确认恢复默认，请重新读取": "Restore not confirmed; read the icon again",
    "设备拒绝修改；已读取当前图标，本地图片保留": "Device rejected the change; current icon read, local image retained",
    "操作响应异常，正在读取确认": "Unexpected response; reading to confirm",
    "尚未读取设备图标": "Device icon has not been read",
    "写入结果待重新读取确认": "Read the device again to confirm the write result",
    "当前设备未支持图标写入或连接尚未授权": "Icon writes are unsupported or the connection is not authorized",
    "请先重新读取，确认上次写入结果": "Read the device first to confirm the previous write",
    "正在读取设备图标": "Reading device icon",
    "正在上传图标": "Uploading icon",
    "写入已响应，正在回读确认": "Write acknowledged; reading back to verify",
    "图标已保存，完整回读一致": "Icon saved; full readback matches",
    "已恢复默认图标": "Default icon restored",
    "设备正在使用默认图标": "Device is using its default icon",
    "已读取设备自定义图标": "Custom device icon read",
    "设备当前图标与待上传图片不一致；保留本地图片，可重新上传": "Device icon differs from the upload; local image retained for retry",
    "设备仍为自定义图标，未确认恢复默认": "Device still has a custom icon; reset not confirmed",
    "已取消，设备图标未修改": "Cancelled; device icon unchanged",
    "已取消读取，设备图标未修改": "Read cancelled; device icon unchanged",
    "设备当前图标：内置默认": "Current device icon: built-in default",
    "设备当前图标：自定义（已读回）": "Current device icon: custom (read back)",
    "读取设备图标": "Read device icon",
    "取消上传": "Cancel transfer",
    "将替换当前设备的 NORMAL 首页图标，其他设置不变。是否继续？": "Replace this device's NORMAL home icon? Other settings will not change.",
    "仅恢复内置首页图标，不恢复出厂设置，也不修改按键和提示词。是否继续？": "Restore only the built-in home icon? Key mappings, prompts and other settings will not change.",
    "设备连接已变化": "Device connection changed",
    "设备已断连或重新连接，请重新确认目标设备后操作。": "The device disconnected or reconnected. Check the target device and try again.",
    "圆屏图标正在传输或读回，请先等待完成或取消": "Wait for the icon transfer or readback to finish, or cancel it first",
    "设备维护或写入尚未结束，请稍后操作圆屏图标": "Device maintenance or writing is in progress; try the icon operation later",
    "圆屏图标尚未完成": "Icon transfer is not finished",
    "请等待圆屏图标传输或读回完成；提交之前可以点击取消上传。": "Wait for the icon transfer or readback to finish. You can cancel before commit.",
    "自定义圆屏图标": "Custom screen icon",
    "仅用于 NORMAL 模式的空闲首页；不改变提示词盘、任务状态或系统警告。": "For the NORMAL idle home screen only; prompt selection, task states and system warnings stay unchanged.",
    "设备当前图标：尚未读取": "Current device icon: not read",
    "尚未选择图片": "No image selected",
    "本地预览 · 尚未写入设备": "Local preview · not written to device",
    "导入静态 PNG / JPG，拖动图片调整位置，或使用下方滑杆。最大 10 MiB、1600 万像素。": "Import a static PNG / JPG. Drag to position or use the sliders below. Maximum 10 MiB and 16 megapixels.",
    "导入图片…": "Import image…",
    "缩放": "Zoom",
    "水平位置": "Horizontal position",
    "垂直位置": "Vertical position",
    "丢弃本地图标": "Discard local icon",
    "写入图标": "Write icon",
    "恢复默认图标": "Restore default icon",
    "控制台图标协议接入尚未完成，暂不能写入或恢复默认。": "Console icon protocol integration is pending; writing and restoring are not available yet.",
    "当前固件尚未支持自定义图标，可先导入并预览。": "This firmware does not support custom icons yet. You can import and preview locally.",
    "本地图标只在本次运行中保留，不包含在配置方案或配置导出中。": "Local icons are kept for this session only, separately from profiles and configuration exports.",
    "图片无法导入": "Cannot import image",
    "请选择可读取的静态 PNG / JPG，大小不超过 10 MiB、1600 万像素。原有本地编辑已保留。": "Choose a readable static PNG / JPG up to 10 MiB and 16 megapixels. Your previous local edits have been preserved.",
    "本地图标尚未写入": "Local icons have not been written",
    "退出将丢弃本次运行中各设备的本地图标编辑；原图片文件不受影响。是否丢弃并退出？": "Quitting discards local icon edits for all devices in this session. Original image files are unaffected. Discard and quit?",
    "连接已断开 · 断开前的只读快照": "Disconnected · read-only snapshot from the last connection",
    "预览不等于保存；写入设备仍需明确确认。": "Preview does not save changes; writing to the device still requires confirmation.",
    "按键序列": "Key Sequence",
    "设备按键序列": "Device Key Sequences",
    "管理设备按键序列": "Manage Device Key Sequences",
    "按键序列步骤编辑": "Key Sequence Step Editor",
    "保存在设备内，无需后台助手；支持按下、释放、敲击、ASCII 文本和有界延时。": (
        "Stored on the device and works without the background helper. Supports "
        "press, release, tap, ASCII text, and bounded delays."
    ),
    "按键序列保存在设备中，可在 BORING 控制台退出后继续执行。": (
        "Key sequences are stored on the device and keep working after BORING Console exits."
    ),
    "新建按键序列": "New Key Sequence",
    "当前配置没有按键序列。点击“新建按键序列”创建第一条。": (
        "This configuration has no key sequences. Select New Key Sequence to create the first one."
    ),
    "按键序列名称": "Key Sequence Name",
    "取消按键序列": "Cancel Key Sequence",
    "无法新建按键序列": "Unable to Create Key Sequence",
    "无法保存按键序列": "Unable to Save Key Sequence",
    "按键序列已保存，但草稿未通过校验": (
        "Key sequence saved, but the draft did not pass validation"
    ),
    "无法删除按键序列": "Unable to Delete Key Sequence",
    "创建自动化": "Create Automation",
    "我的自动化": "My Automation",
    "工作流与脚本": "Workflows and Scripts",
    "脚本导入": "Script Import",
    "Codex 工作流": "Codex Workflow",
    "导入 JSON": "Import JSON",
    "导入脚本": "Import Script",
    "粘贴给 Codex 并描述需求": "Paste into Codex and describe your needs",
    "Codex 生成 ZIP 扩展包": "Have Codex generate a ZIP extension package",
    "控制台校验扩展": "The console validates the extension",
    "启用、检查运行结果并绑定控件": "Enable, check run results, and bind a control",
    "按键触发运行": "Press the bound control to run",
    "以上为操作顺序，不代表已完成验证。扩展启用后请确认运行记录，再进行实体触发验收。": (
        "These are instructions, not completed validation steps. After enabling the "
        "extension, check its run log, then verify the physical trigger."
    ),
    "配置变更提案审阅": "Review Configuration Proposals",
    "自动化只包含可检查的数据和 BORING 内置步骤；本地脚本与扩展在“工作流与脚本”中单独管理。": (
        "Automation contains inspectable data and BORING built-in steps only. "
        "Manage local scripts and extensions under Workflows and Scripts."
    ),
    "灯光": "Lighting",
    "点击方向编辑提示词": "Select a direction to edit its prompt",
    "更多操作": "More actions",
    "粘贴说明": "Paste help",
    "写入设备并读回": "Write and Verify",
    "上": "Up",
    "右": "Right",
    "下": "Down",
    "左": "Left",
    "查看设备操作步骤": "Device instructions",
    "震动": "Haptics",
    "屏幕": "Display",
    "外观与反馈": "Appearance & Feedback",
    "外观与反馈 · 灯光、震动、屏幕": "Appearance & Feedback · Lighting, Haptics, Display",
    "只能在外观与反馈页开启实时预览": "Live preview is only available on the Appearance & Feedback page",
    "灯光・震动・圆屏": "Lighting · Haptics · Display",
    "通用": "General",
    "设备": "Device",
    "关于": "About",
    "信任状态": "Trust Status",
    "已认证 [ VERIFIED ]": "Authenticated [ VERIFIED ]",
    "开发设备，未认证 [ DEV ]": "Development device, unauthenticated [ DEV ]",
    "无法确认是 BORING 设备 [ UNTRUSTED ]": "Unable to verify BORING identity [ UNTRUSTED ]",
    "未连接设备 [ NO LINK ]": "No device connected [ NO LINK ]",
    "已断开 [ NO LINK ]": "Disconnected [ NO LINK ]",
    "已同步 [ SYNCED ]": "Synced [ SYNCED ]",
    "7D 外环 · 5H 内环 · 剩余额度": "7D outer ring · 5H inner ring · Remaining quota",
    "BORING MIST 桌面控制台\n\n界面字体优先使用 HarmonyOS Sans SC；未安装时使用系统中文字体。字体按名称引用，不随应用嵌入。\n\n点阵：BORING 5R UI Digital Core v1.0。10R 品牌字库待设计素材补齐后接入。": (
        "BORING MIST Desktop Console\n\nThe interface uses HarmonyOS Sans SC when installed, "
        "otherwise the system Chinese font. Fonts are referenced by name and are not bundled."
        "\n\nDot matrix: BORING 5R UI Digital Core v1.0. The 10R brand font awaits the final design assets."
    ),
    "控件绑定": "Control Bindings",
    "运行记录": "Run History",
    "开发扩展": "Develop Extensions",
    "本地脚本": "Local Scripts",
    "脚本列表": "Script List",
    "新建脚本": "New Script",
    "本地脚本设置": "Local Script Settings",
    "保存本地脚本": "Save Local Script",
    "扩展包": "Extension Packages",
    "扩展": "Extension",
    "把一个实体提示词槽位绑定到 1–5 个执行步骤。任一步骤失败时立即停止，不自动重试，也不会自行按下 Enter。": (
        "Bind one physical prompt slot to 1–5 execution steps. The automation "
        "stops when any step fails, never retries automatically, and never presses Enter."
    ),
    "让一个实体控件替你完成一项或多项电脑操作；需要更多能力时，再导入由 Codex 或其他 AI 帮你开发的扩展。": (
        "Let one physical control complete one or more computer operations. "
        "When you need more, import an extension developed with Codex or another AI."
    ),
    "这里汇总内置自动化、本地脚本和已导入扩展。普通使用时只需要关注名称、可用状态和控件绑定。": (
        "This list combines built-in automation, local scripts, and imported "
        "extensions. Most users only need the name, availability, and control binding."
    ),
    "一个提示词槽位只能绑定一个自动化来源：内置自动化、本地脚本或扩展。这里不会静默替换已有绑定。": (
        "A prompt slot can bind to only one automation source: built-in automation, "
        "a local script, or an extension. Existing bindings are never replaced silently."
    ),
    "内置自动化不够用时：复制开发提示词 → 在 Codex 或其他 AI 中说明需求 → 导入生成的扩展包 → 检查权限 → 测试并绑定。": (
        "When built-in automation is not enough: copy the development prompt → explain "
        "your need in Codex or another AI → import the extension → review permissions → test and bind."
    ),
    "自动化列表": "Automation List",
    "自动化设置": "Automation Settings",
    "使用保存摘录模板": "Use Save Selection Template",
    "实体触发": "Physical Trigger",
    "允许实体控件运行": "Allow Physical Execution",
    "执行步骤": "Execution Steps",
    "添加步骤": "Add Step",
    "上移": "Move Up",
    "下移": "Move Down",
    "移除步骤": "Remove Step",
    "当前步骤": "Current Step",
    "参数": "Parameter",
    "选择文件": "Choose File",
    "选择目录": "Choose Folder",
    "追加间隔": "Append Separator",
    "空一行": "Blank Line",
    "换一行": "New Line",
    "直接连接": "No Separator",
    "保存自动化": "Save Automation",
    "3 秒后测试": "Test in 3 Seconds",
    "导入自动化": "Import Automation",
    "导出自动化": "Export Automation",
    "自动化只包含可检查的数据和 BORING 内置步骤；本地脚本与扩展在“开发扩展”中单独管理。": (
        "Automation contains inspectable data and BORING built-in steps only. "
        "Manage local scripts and extensions separately under Develop Extensions."
    ),
    "获取当前选中文字": "Capture Selected Text",
    "读取剪贴板文字": "Read Clipboard Text",
    "追加到 Markdown / TXT": "Append to Markdown / TXT",
    "打开应用、文件或网址": "Open App, File, or URL",
    "显示完成通知": "Show Completion Notification",
    "向前台应用发送复制快捷键，并把 Unicode 文字交给下一步。": (
        "Send the copy shortcut to the foreground app and pass Unicode text to the next step."
    ),
    "读取当前剪贴板中的 Unicode 文字，不发送键盘快捷键。": (
        "Read Unicode text from the clipboard without sending a shortcut."
    ),
    "把当前文字追加到本机文件，不覆盖已有内容。": (
        "Append the current text to a local file without overwriting existing content."
    ),
    "通过操作系统打开一个本机目标或 HTTP/HTTPS 网址。": (
        "Open a local target or HTTP/HTTPS URL through the operating system."
    ),
    "通过操作系统显示一条简短的完成提示。": (
        "Show a short completion message through the operating system."
    ),
    "目标文件": "Target File",
    "目标": "Target",
    "通知内容": "Notification Message",
    "清空官方与本地记录": "Clear Official and Local History",
    "尚未创建自动化": "No automation created",
    "尚未建立实体绑定": "No physical bindings",
    "尚无运行记录": "No run history",
    "内置自动化不够用时": "When built-in automation is not enough",
    "自动化已保存": "Automation saved",
    "3 秒后开始测试；请立即切回要获取内容的应用。": (
        "The test starts in 3 seconds. Switch back to the app you want to capture from now."
    ),
    "已导入但未启用；请检查本机路径和实体绑定。": (
        "Imported but disabled. Review local paths and the physical binding."
    ),
    "自动化已导出": "Automation exported",
    "导入内容尚未完成本机路径与绑定检查": "Imported paths and bindings still need review",
    "有未保存修改；修改后需要重新测试": "Unsaved changes; test again after saving",
    "已启用 · 实体事件可以运行": "Enabled · physical events can run it",
    "测试通过 · 可以启用实体触发": "Test passed · physical execution can be enabled",
    "尚未测试 · 不会响应实体事件": "Not tested · physical events will not run it",
    "尚未选择目标文件": "No target file selected",
    "尚未选择目标": "No target selected",
    "尚未填写通知": "No notification message",
    "已测试": "Tested",
    "待测试": "Needs Testing",
    "可运行": "Ready",
    "已启用但未就绪": "Enabled but Not Ready",
    "未绑定实体控件": "Not Physically Bound",
    "会响应实体事件": "Responds to Physical Events",
    "当前不会运行": "Will Not Run",
    "内置自动化": "Built-in Automation",
    "BORING 内置自动化": "BORING Built-in Automation",
    "BORING 本地脚本": "BORING Local Script",
    "本地脚本只执行用户在本机明确选择并启用的 Python 文件；不下载网络代码，也不修改设备协议或固件。": (
        "Local scripts run only Python files explicitly selected and enabled on this computer. "
        "They do not download network code or modify the device protocol or firmware."
    ),
    "新建本地脚本默认不允许实体触发；保存后可手动测试。": (
        "New local scripts cannot be triggered physically by default. Save one before running a manual test."
    ),
    "无法保存本地脚本": "Unable to Save Local Script",
    "本地脚本已保存": "Local script saved",
    "无法运行本地脚本": "Unable to Run Local Script",
    "本地脚本名称不能为空": "Local script name cannot be empty",
    "删除本地脚本": "Delete Local Script",
    "无法删除本地脚本": "Unable to Delete Local Script",
    "已删除本地脚本": "Deleted local script",
    "连接设备后，本地脚本会按 USB 序列号分别保存。": (
        "After a device is connected, local scripts are stored separately by USB serial number."
    ),
    "内置自动化加载失败": "Built-in automation failed to load",
    "已删除自动化": "Automation deleted",
    "当前系统不可用": "Unavailable on This System",
    "开始手动测试": "Manual test started",
    "收到实体事件，开始运行": "Physical event received; starting workflow",
    "手动测试通过，可以启用实体触发": (
        "Manual test passed; physical execution can now be enabled"
    ),
    "官方工作流加载失败": "Official workflow failed to load",
    "已删除官方工作流": "Official workflow deleted",
}


def _translate_dynamic_english(source: str) -> str:
    slot_pending = re.fullmatch(r"提示词槽位 (\d+) 尚未写入设备；请先在快捷提示词页填写、写入并读回。", source)
    if slot_pending:
        return f"Prompt slot {slot_pending.group(1)} is not on the device. Fill it in on Quick Prompts, write it, and read it back first."
    slot_mapping = re.fullmatch(r"提示词槽位 (\d+) 不在四向提示词盘中；请先在设备按键配置中设置并写入触发映射。", source)
    if slot_mapping:
        return f"Prompt slot {slot_mapping.group(1)} is outside the four-way palette. Configure and write a physical key mapping first."
    old_slot = re.fullmatch(r"提示词槽位 (\d+) · 需重新配置", source)
    if old_slot:
        return f"Prompt slot {old_slot.group(1)} · setup required"
    if source.startswith("步骤测试通过；"):
        return "Steps passed; " + translate_ui_text(source.removeprefix("步骤测试通过；"))
    if source.startswith("已停止："):
        return "Stopped: " + source.removeprefix("已停止：")
    exact = _DYNAMIC_ENGLISH_EXACT.get(source)
    if exact is not None:
        return exact
    def translate_fragment(value: str) -> str:
        translated = QCoreApplication.translate(TRANSLATION_CONTEXT, value)
        if translated != value:
            return translated
        if "/" in value:
            return "/".join(translate_fragment(part) for part in value.split("/"))
        return _translate_dynamic_english(value)

    if source.startswith("7D 外环 · 5H 内环 · 剩余额度\n"):
        return "\n".join(translate_fragment(line) for line in source.split("\n"))
    device_detail = re.fullmatch(r"(设备|硬件 ID|序列号|端口|固件|信任状态)  (.+)", source)
    if device_detail:
        label, value = device_detail.groups()
        return f"{translate_fragment(label)}  {translate_fragment(value) if label == '信任状态' else value}"

    unavailable = re.fullmatch(r"(.+) · 当前系统不可用", source)
    if unavailable:
        return (
            f"{translate_fragment(unavailable.group(1))} · "
            f"{_DYNAMIC_ENGLISH_EXACT['当前系统不可用']}"
        )

    for prefix in ("• ", "+ "):
        if source.startswith(prefix):
            remainder = source[len(prefix) :]
            translated = translate_fragment(remainder)
            return f"{prefix}{translated}"
    numbered = re.fullmatch(r"(\d{2}  )(.*)", source, re.DOTALL)
    if numbered:
        remainder = numbered.group(2)
        translated = translate_fragment(remainder)
        return f"{numbered.group(1)}{translated}"
    rgb = re.fullmatch(r"(Agent 状态键|白色动作键) (\d+)\n(.+)", source, re.DOTALL)
    if rgb:
        role = QCoreApplication.translate(TRANSLATION_CONTEXT, rgb.group(1))
        return f"{role} {rgb.group(2)}\n{rgb.group(3)}"
    rgb_accessible = re.fullmatch(
        r"(Agent 状态键|白色动作键) (\d+)：(.+)", source, re.DOTALL
    )
    if rgb_accessible:
        role = QCoreApplication.translate(
            TRANSLATION_CONTEXT, rgb_accessible.group(1)
        )
        return f"{role} {rgb_accessible.group(2)}: {rgb_accessible.group(3)}"
    macro_capacity = re.fullmatch(
        r"(\d+)/(\d+) 条按键序列\n(\d+)/(\d+) 字节", source
    )
    if macro_capacity:
        return (
            f"{macro_capacity.group(1)}/{macro_capacity.group(2)} key sequences\n"
            f"{macro_capacity.group(3)}/{macro_capacity.group(4)} bytes"
        )
    direction_selection = re.fullmatch(
        r"([↑→↓←]) (上|右|下|左) · 快捷提示词 ([1-4])", source
    )
    if direction_selection:
        direction = translate_fragment(direction_selection.group(2))
        return (
            f"{direction_selection.group(1)} {direction} · "
            f"Quick Prompt {direction_selection.group(3)}"
        )
    direction_card = re.fullmatch(
        r"([↑→↓←])  (上|右|下|左)\n(.+)\n(.+)", source, re.DOTALL
    )
    if direction_card:
        direction = translate_fragment(direction_card.group(2))
        status = translate_fragment(direction_card.group(4))
        return (
            f"{direction_card.group(1)}  {direction}\n"
            f"{direction_card.group(3)}\n{status}"
        )
    current_action = re.fullmatch(r"当前动作：(.+)", source, re.DOTALL)
    if current_action:
        return f"Current action: {translate_fragment(current_action.group(1))}"
    macro_summary = re.fullmatch(
        r"(\d+) 个步骤 · 编码 (\d+)/(\d+) 字节 · 显式延时 (.+) · (容量内|已超限)",
        source,
    )
    if macro_summary:
        state = QCoreApplication.translate(TRANSLATION_CONTEXT, macro_summary.group(5))
        return (
            f"{macro_summary.group(1)} steps · "
            f"{macro_summary.group(2)}/{macro_summary.group(3)} encoded bytes · "
            f"explicit delay {macro_summary.group(4)} · {state}"
        )
    if source.startswith("数字键盘 "):
        suffix = source.removeprefix("数字键盘 ")
        if suffix == "=（AS/400）":
            suffix = "= (AS/400)"
        return f"Keypad {suffix}"
    for prefix, translated_prefix in (
        ("动作 · ", "Action · "),
        ("按 · ", "Press · "),
        ("转 · ", "Turn · "),
    ):
        if source.startswith(prefix):
            return f"{translated_prefix}{translate_fragment(source.removeprefix(prefix))}"
    bound = re.fullmatch(r"绑定 (.+) · generation (.+)", source)
    if bound:
        return f"Bound to {bound.group(1)} · generation {bound.group(2)}"
    actual_and_name = re.fullmatch(
        r"实际动作：(.+)\n功能名称：(.+)", source, re.DOTALL
    )
    if actual_and_name:
        return (
            f"Actual action: {translate_fragment(actual_and_name.group(1))}\n"
            f"Function name: {actual_and_name.group(2)}"
        )
    function_name = re.fullmatch(r"功能名称：(.+)", source, re.DOTALL)
    if function_name:
        return f"Function name: {function_name.group(1)}"
    physical_key = re.fullmatch(r"(白色按键|透明状态键) (\d+)", source)
    if physical_key:
        label = "White Key" if physical_key.group(1) == "白色按键" else "Transparent Status Key"
        return f"{label} {physical_key.group(2)}"
    axis_values = re.fullmatch(
        r"X  (.+)    死区 (.+)\nY  (.+)    死区 (.+)", source
    )
    if axis_values:
        return (
            f"X  {axis_values.group(1)}    Deadzone {axis_values.group(2)}\n"
            f"Y  {axis_values.group(3)}    Deadzone {axis_values.group(4)}"
        )
    config_status = re.fullmatch(r"配置状态：(.+)。", source)
    if config_status:
        return f"Configuration status: {translate_fragment(config_status.group(1))}."
    for pattern, replacement in (
        (re.compile(r"^硬件身份：(.+)。$"), r"Hardware identity: \1."),
        (re.compile(r"^运行模式：(.+)。$"), r"Operating mode: \1."),
    ):
        if pattern.fullmatch(source):
            return pattern.sub(replacement, source)
    for pattern, replacement in _DYNAMIC_ENGLISH_PATTERNS:
        if pattern.fullmatch(source):
            return pattern.sub(replacement, source)
    return source
