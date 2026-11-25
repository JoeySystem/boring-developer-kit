"""The one dialog shell every Console dialog uses (the designer, B direction v0.4 final).

A dark rounded card without a system title bar; the main window dims behind
it. Buttons sit in one row on the right, sized to their text, primary last.
Closing lives bottom-left: a round button with an orange cross, or the
"稍后设置" text button when the dialog offers one, or nothing in a cancel /
confirm dialog. Read-only dialogs have no "关闭" button at the bottom.
Danger confirmations carry no orange: "取消" is the cream button in the
primary spot (Enter cancels), the dangerous action is an outline button.

Spec: Console视觉稿/弹窗统一样式-B方向-v0.4-合成版-20261118-图/4-合成版-规矩和数值.png.
"""
from __future__ import annotations

from PySide6.QtCore import QEvent, QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QDialog, QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea, QSizePolicy,
    QStackedWidget, QVBoxLayout, QWidget,
)

from controller_config.i18n import translate_ui_text as tr
from controller_config.style import tokens
from controller_config.views.ui.button import Button

# Width tiers: each dialog takes the smallest one its content fits.
SMALL, MEDIUM, LARGE, XLARGE = 360, 480, 600, 760
# Tallest a dialog gets: a step smaller than the smallest window (960×640), so a dialog
# never covers it whole (design decision 第 4 件; 窗口尺寸研究 第 7 节第 7 条).
MAX_HEIGHT = 560
# 设置常用 AI keeps 640 for now: its step 3 needs 34 more than 560 (left to the firmware owner).
TALL_MAX_HEIGHT = 640
PADDING = 24
TITLE_GAP = 14
BUTTON_ROW_GAP = 24
BUTTON_GAP = 8
CLOSE_SIZE = 40
QWIDGETSIZE_MAX = (1 << 24) - 1



def dialog_button(text: str, kind: str = "secondary", *, object_name: str = "") -> Button:
    """Kept for banner.py, which adds its own small size; new code uses Button."""
    return Button(text, kind, object_name=object_name)


class RoundCloseButton(QPushButton):
    """Bottom-left close: dark round button, orange cross, thin orange ring."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("dialogRoundClose")
        self.setFixedSize(CLOSE_SIZE, CLOSE_SIZE)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAccessibleName(tr("关闭"))
        self.setToolTip(tr("关闭"))
        self.setAutoDefault(False)

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt virtual method
        return QSize(CLOSE_SIZE, CLOSE_SIZE)

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt virtual method
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        body = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        ring = QColor(tokens.ACCENT)
        ring.setAlphaF(0.6 if self.isEnabled() else 0.2)
        fill = QColor(tokens.SURFACE_3 if self.underMouse() and self.isEnabled() else tokens.SURFACE_2)
        painter.setPen(QPen(ring, 1))
        painter.setBrush(fill)
        painter.drawEllipse(body)
        cross = QColor(tokens.ACCENT if self.isEnabled() else tokens.TEXT_TERTIARY)
        painter.setPen(QPen(cross, 1.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        centre, half = body.center(), 6.0  # a 12 px cross
        painter.drawLine(centre + QPointF(-half, -half), centre + QPointF(half, half))
        painter.drawLine(centre + QPointF(-half, half), centre + QPointF(half, -half))
        painter.end()


class _Scrim(QWidget):
    """45% black over the main window while a dialog is open."""

    def __init__(self, window: QWidget) -> None:
        super().__init__(window)
        self.setObjectName("dialogScrim")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        self.setGeometry(window.rect())
        window.installEventFilter(self)

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 - Qt virtual method
        if watched is self.parent() and event.type() == QEvent.Type.Resize:
            self.setGeometry(self.parent().rect())
        return False

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt virtual method
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        window = self.window()
        radius = 0 if window.isFullScreen() else tokens.RADIUS_CARD
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()), radius, radius)
        painter.fillPath(path, QColor(*tokens.DIALOG_SCRIM))
        painter.end()


class Dialog(QDialog):
    """Shell for every Console dialog.

    title:   written once, top-left (18, semibold).
    step:    optional "名 · 1 / 3" line above the title for step-by-step dialogs.
    width:   SMALL, MEDIUM, LARGE or XLARGE; the smallest one the content fits.
    close:   "round" (default), "later" (with later_text, e.g. "稍后设置"), or "none".
    full_height: dialogs whose content is swapped in after opening (a guide
             embedded later) take MAX_HEIGHT at once, so the window does not
             jump; their own content scrolls inside the body.
    use_steps(stack): a step-by-step dialog whose steps are the pages of one
             QStackedWidget takes the height of its tallest step (B v0.4
             "高取最高的那一步"), so it does not jump between steps and is no
             taller than it needs to be; a step taller than MAX_HEIGHT scrolls.
    Add content with body_layout(), buttons with add_button(); the primary
    button goes last. Height follows the content up to MAX_HEIGHT, then scrolls.
    Shown as a child widget inside another dialog (embedded), it draws no card,
    no scrim and keeps no padding: the host dialog owns those.
    """

    max_height = MAX_HEIGHT   # a dialog that must stay taller sets its own

    def __init__(self, parent: QWidget | None, title: str = "", *, step: str = "",
                 width: int = MEDIUM, close: str = "round", later_text: str = "稍后设置",
                 full_height: bool = False, object_name: str = "") -> None:
        super().__init__(parent)
        if close not in {"round", "later", "none"}:
            raise ValueError(f"Unknown close style: {close}")
        self.setObjectName(object_name or "boringDialog")
        self.setProperty("boringDialog", True)
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setModal(True)
        # Styled by style/sheets.py (COMPONENT_TEMPLATE) through the main window.
        # Without a parent window (startup errors, tests) it would get no style
        # at all and paint dark text on the dark card, so it carries the sheet.
        if parent is None:
            from controller_config.style.sheets import application_stylesheet
            self.setStyleSheet(application_stylesheet())
        self.setWindowTitle(tr(title))
        self._width = width
        self._full_height = full_height
        self._steps: QStackedWidget | None = None
        self._scrim: _Scrim | None = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(PADDING, PADDING, PADDING, PADDING)
        outer.setSpacing(0)
        self._step_label: QLabel | None = None
        if step:
            self._step_label = QLabel(tr(step), objectName="boringDialogStep")
            outer.addWidget(self._step_label)
            outer.addSpacing(4)
        self.title_label = QLabel(tr(title), objectName="boringDialogTitle")
        self.title_label.setWordWrap(True)
        self.title_label.setVisible(bool(title))
        outer.addWidget(self.title_label)
        if title:
            outer.addSpacing(TITLE_GAP)

        self._shell_scroll = QScrollArea()
        self._shell_scroll.setWidgetResizable(True)
        self._shell_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._shell_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._shell_scroll.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Ignored)
        self._shell_body = QWidget(objectName="boringDialogBody")
        self._shell_body_layout = QVBoxLayout(self._shell_body)
        self._shell_body_layout.setContentsMargins(0, 0, 0, 0)
        self._shell_body_layout.setSpacing(16)
        self._shell_scroll.setWidget(self._shell_body)
        outer.addWidget(self._shell_scroll, 1)

        outer.addSpacing(BUTTON_ROW_GAP)
        self._shell_buttons = QHBoxLayout()
        self._shell_buttons.setContentsMargins(0, 0, 0, 0)
        self._shell_buttons.setSpacing(BUTTON_GAP)
        self.close_button: QPushButton | None = None
        if close == "round":
            self.close_button = RoundCloseButton()
            self.close_button.clicked.connect(self.reject)
            self._shell_buttons.addWidget(self.close_button)
        elif close == "later":
            self.close_button = Button(later_text, "secondary", object_name="dialogLater")
            self.close_button.clicked.connect(self.reject)
            self._shell_buttons.addWidget(self.close_button)
        self._shell_buttons.addStretch(1)
        outer.addLayout(self._shell_buttons)

    # -- content -------------------------------------------------------------

    def body_layout(self) -> QVBoxLayout:
        return self._shell_body_layout

    def button_row(self) -> QHBoxLayout:
        """The bottom row: close, a stretch, then the buttons in order.

        For extras that belong between them, such as a step dialog's dots.
        """
        return self._shell_buttons

    def use_steps(self, stack: QStackedWidget) -> None:
        """Size the window for the tallest page of `stack`; see the class docstring."""
        self._steps = stack
        stack.currentChanged.connect(lambda _index: self._size_steps())
        self._size_steps()

    def _size_steps(self) -> None:
        """Only the current step counts towards the body's height.

        A short step then shows no scroll bar inside the tallest step's window.
        """
        if self._steps is None:
            return
        current = self._steps.currentWidget()
        for index in range(self._steps.count()):
            page = self._steps.widget(index)
            policy = page.sizePolicy()
            vertical = QSizePolicy.Policy.Preferred if page is current else QSizePolicy.Policy.Ignored
            if policy.verticalPolicy() != vertical:
                policy.setVerticalPolicy(vertical)
                page.setSizePolicy(policy)
        self._steps.updateGeometry()
        self._shell_body.updateGeometry()

    def _steps_extra_height(self, width: int) -> int:
        """How much taller the tallest step is than the one showing now."""
        if self._steps is None or self._steps.count() == 0:
            return 0

        def natural(page: QWidget) -> int:
            # A page in its own scroll area: measure what it scrolls, not the area
            # (QScrollArea keeps the first size it was asked for).
            inner = page.widget() if isinstance(page, QScrollArea) and page.widget() is not None else page
            height = inner.heightForWidth(width) if inner.hasHeightForWidth() else inner.sizeHint().height()
            return max(height, inner.minimumSizeHint().height())

        heights = [natural(self._steps.widget(index)) for index in range(self._steps.count())]
        return max(0, max(heights) - heights[self._steps.currentIndex()])

    def set_step(self, text: str) -> None:
        """Update the "名 · 1 / 3" line; the dialog must be built with a step."""
        if self._step_label is None:
            raise ValueError("This dialog was built without a step line")
        self._step_label.setText(text)

    def add_text(self, text: str, *, secondary: bool = False) -> QLabel:
        label = QLabel(tr(text))
        label.setWordWrap(True)
        label.setTextFormat(Qt.TextFormat.PlainText)
        if secondary:
            label.setProperty("tone", "secondary")
        self._shell_body_layout.addWidget(label)
        return label

    def add_button(self, text: str, kind: str = "secondary", *, role: str = "",
                   object_name: str = "") -> Button:
        """Add a bottom-right button; call in order, primary last.

        role "accept" closes with accept(), "reject" with reject(); empty leaves
        the click to the caller.
        """
        button = Button(text, kind, object_name=object_name)
        if role == "accept":
            button.clicked.connect(self.accept)
        elif role == "reject":
            button.clicked.connect(self.reject)
        self._shell_buttons.addWidget(button)
        return button

    # -- geometry and window ---------------------------------------------------

    def fit(self) -> None:
        """Width from the tier, height from the content, capped at `max_height`."""
        inner = self._width - 2 * PADDING
        self._size_steps()
        self._shell_body.setFixedWidth(inner)
        layout = self._shell_body.layout()
        layout.activate()
        body_height = (self._shell_body.heightForWidth(inner) if self._shell_body.hasHeightForWidth()
                       else layout.sizeHint().height())
        body_height = max(body_height, layout.minimumSize().height())
        body_height += self._steps_extra_height(self._steps.width() if self._steps is not None
                                                and self._steps.width() > 0 else inner)
        # The scroll area gets exactly the body's height, or what is left under
        # the cap; only then does a scroll bar appear.
        room = self.max_height - self._chrome_height()
        if self._full_height:
            body_height = room
        self._shell_scroll.setFixedHeight(max(0, min(body_height, room)))
        # Measured at the full width; from here the body follows the scroll
        # area, so a scroll bar takes its room instead of covering the content.
        self._shell_scroll.setFixedWidth(inner)
        self._shell_body.setMinimumWidth(0)
        self._shell_body.setMaximumWidth(QWIDGETSIZE_MAX)
        self.layout().activate()
        self.setFixedSize(self._width, min(self.max_height, self.layout().sizeHint().height()))

    def _chrome_height(self) -> int:
        """Everything except the body: padding, step, title and the button row."""
        height = 2 * PADDING + BUTTON_ROW_GAP
        if self._step_label is not None:
            height += self._step_label.sizeHint().height() + 4
        if self.title_label.isVisibleTo(self) or self.title_label.text():
            height += self.title_label.heightForWidth(self._width - 2 * PADDING) + TITLE_GAP
        row = max((self._shell_buttons.itemAt(i).widget().sizeHint().height()
                   for i in range(self._shell_buttons.count()) if self._shell_buttons.itemAt(i).widget()),
                  default=0)
        return height + max(row, CLOSE_SIZE if self.close_button is not None else 40)

    def embed(self) -> None:
        """Show this dialog as a step inside another dialog's body."""
        self.setWindowFlags(Qt.WindowType.Widget)
        self.setModal(False)
        self.layout().setContentsMargins(0, 0, 0, 0)

    def showEvent(self, event) -> None:  # noqa: N802 - Qt virtual method
        if not self.isWindow():
            super().showEvent(event)
            return
        self.fit()
        window = self.parentWidget().window() if self.parentWidget() else None
        if window is not None and window.isVisible():
            if self._scrim is None:
                self._scrim = _Scrim(window)
            self._scrim.show()
            self._scrim.raise_()
            centre = window.frameGeometry().center()
            self.move(centre.x() - self.width() // 2, centre.y() - self.height() // 2)
        super().showEvent(event)

    def done(self, result: int) -> None:  # noqa: D401 - Qt virtual method
        if self._scrim is not None:
            self._scrim.hide()
            self._scrim.deleteLater()
            self._scrim = None
        super().done(result)

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt virtual method
        if not self.isWindow():
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        body = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        painter.setPen(QPen(QColor(*tokens.DIALOG_EDGE), 1))
        painter.setBrush(QColor(tokens.DIALOG_SURFACE))
        painter.drawRoundedRect(body, tokens.RADIUS_CARD, tokens.RADIUS_CARD)
        painter.end()


class _WarningMark(QWidget):
    """Deep-orange rounded triangle used in danger confirmations."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(20, 20)

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt virtual method
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        colour = QColor(tokens.SIGNAL)
        painter.setPen(QPen(colour, 1.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                            Qt.PenJoinStyle.RoundJoin))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        path = QPainterPath()
        path.moveTo(10, 3)
        path.lineTo(18, 17)
        path.lineTo(2, 17)
        path.closeSubpath()
        painter.drawPath(path)
        painter.drawLine(QPointF(10, 8.5), QPointF(10, 12))
        painter.drawPoint(QPointF(10, 14.5))
        painter.end()


def confirm(parent: QWidget | None, title: str, text: str, confirm_text: str, *,
            danger: bool = False, cancel_text: str = "取消", width: int = SMALL,
            object_name: str = "boringConfirm") -> bool:
    """Ask once; True only when the user picks confirm_text.

    danger=True follows the danger rule: no orange, "取消" is the cream button in
    the primary spot and the default (Enter cancels), the action is an outline
    button, a deep-orange triangle sits left of the text. Confirm dialogs have
    no round close; Esc cancels.
    """
    dialog = Dialog(parent, title, width=width, close="none", object_name=object_name)
    row = QHBoxLayout()
    row.setSpacing(10)
    if danger:
        row.addWidget(_WarningMark(), 0, Qt.AlignmentFlag.AlignTop)
    label = QLabel(tr(text))
    label.setWordWrap(True)
    label.setTextFormat(Qt.TextFormat.PlainText)
    row.addWidget(label, 1)
    holder = QWidget()
    holder.setLayout(row)
    dialog.body_layout().addWidget(holder)
    if danger:
        dialog.add_button(confirm_text, "secondary", role="accept", object_name="confirmAction")
        cancel = dialog.add_button(cancel_text, "cream", role="reject", object_name="confirmCancel")
        cancel.setDefault(True)
        cancel.setFocus()
    else:
        dialog.add_button(cancel_text, "secondary", role="reject", object_name="confirmCancel")
        accept = dialog.add_button(confirm_text, "primary", role="accept", object_name="confirmAction")
        accept.setDefault(True)
    return dialog.exec() == QDialog.DialogCode.Accepted
