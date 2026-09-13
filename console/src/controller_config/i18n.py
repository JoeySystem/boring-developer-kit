from __future__ import annotations

from PySide6.QtCore import (
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

from .text_catalog import get_text_catalog

SIMPLIFIED_CHINESE = "zh_CN"
ENGLISH = "en_US"
JAPANESE = "ja_JP"
SUPPORTED_LANGUAGES = (SIMPLIFIED_CHINESE, ENGLISH, JAPANESE)
LANGUAGE_SETTING_KEY = "ui/language"
TRANSLATION_CONTEXT = "ControllerConfig"
SKIP_TRANSLATION_PROPERTY = "boringI18nSkip"


def normalize_language(value: object) -> str | None:
    text = str(value or "").replace("-", "_").lower()
    if text.startswith("zh"):
        return SIMPLIFIED_CHINESE
    if text.startswith("en"):
        return ENGLISH
    if text.startswith("ja"):
        return JAPANESE
    return None


def system_language() -> str:
    return normalize_language(QLocale.system().name()) or ENGLISH


def translate_ui_text(source: str) -> str:
    application = QApplication.instance()
    language = (
        normalize_language(application.property("boringUiLanguage"))
        if isinstance(application, QApplication)
        else SIMPLIFIED_CHINESE
    )
    return get_text_catalog().translate(source, language or SIMPLIFIED_CHINESE)


def set_translatable_text(widget: QLabel | QAbstractButton, source: str) -> None:
    translated = translate_ui_text(source)
    widget.setProperty("boringI18nSource_text", source)
    widget.setText(translated)
    widget.setProperty("boringI18nRendered_text", widget.text())


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
        # QObject's default filter returns False. Avoid wrapping the watched
        # object again while Qt is delivering its destruction events.
        return False

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
        self._application.removeTranslator(self._qt_translator)
        self._qt_translator.deleteLater()
        self._qt_translator = QTranslator(self)
        get_text_catalog()  # Validate editable copy before applying the language.
        qt_catalog = {
            SIMPLIFIED_CHINESE: "qtbase_zh_CN",
            JAPANESE: "qtbase_ja",
        }.get(language)
        if qt_catalog:
            translations_path = QLibraryInfo.path(
                QLibraryInfo.LibraryPath.TranslationsPath
            )
            if self._qt_translator.load(qt_catalog, translations_path):
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
        if not current and obj.property(f"boringI18nSource_{field}") is None:
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
        obj.setProperty(rendered_key, getter())

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
