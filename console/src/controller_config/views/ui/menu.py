"""The Console pop-up menu (Prompt 页 定稿 v1 `.menu`, design decision).

A dark rounded card with a soft shadow; each item is a 34 px row that gets a
faint rounded box under the pointer; a submenu shows a thin "›" on the right;
a dangerous item is orange and has a thin line above it; an item that cannot
be used is greyed. Qt's own menu is square, has a solid triangle for
submenus and can colour no single item, so this one paints itself and lets
QMenu keep the mouse, keyboard and submenu behaviour.

    menu = Menu(button)
    menu.addAction("恢复成设备里的版本")
    examples = menu.addMenu("用示例填入")          # also a Menu
    delete = menu.add_danger_action("从设备删除")  # line above, orange text
    button.clicked.connect(lambda: menu.popup_under(button))

Values: style/tokens.py, "pop-up menu".
"""
from __future__ import annotations

from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, Qt
from PySide6.QtGui import QAction, QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QMenu, QWidget

from controller_config.i18n import translate_ui_text as tr
from controller_config.style import tokens

DANGER_PROPERTY = "menuDanger"
SHADOW_STEPS = 10          # rings drawn to fake the blur


class Menu(QMenu):
    """See the module docstring."""

    def __init__(self, parent: QWidget | None = None, title: str = "") -> None:
        super().__init__(tr(title), parent)
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.FramelessWindowHint
                            | Qt.WindowType.NoDropShadowWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setSeparatorsCollapsible(True)
        # Item sizes and the insets come from style/sheets.py through the main window.
        self.setProperty("boringMenu", True)
        self.setContentsMargins(tokens.MENU_SHADOW_LEFT, tokens.MENU_SHADOW_TOP,
                                tokens.MENU_SHADOW_RIGHT, tokens.MENU_SHADOW_BOTTOM)
        self.setMinimumWidth(tokens.MENU_WIDTH + tokens.MENU_SHADOW_LEFT + tokens.MENU_SHADOW_RIGHT)

    # -- building --------------------------------------------------------------

    def addMenu(self, title, *args) -> QMenu:  # noqa: N802 - Qt API
        """A submenu in the same style (a title string makes a Menu)."""
        if isinstance(title, str) and not args:
            submenu = Menu(self, title)
            self.addAction(submenu.menuAction())
            return submenu
        return super().addMenu(title, *args)

    def add_danger_action(self, text: str) -> QAction:
        """An orange item with a thin line above it ("从设备删除")."""
        self.addSeparator()
        action = self.addAction(text)
        action.setProperty(DANGER_PROPERTY, True)
        return action

    # -- showing ---------------------------------------------------------------

    def card_rect(self) -> QRect:
        """The visible card inside the window (the rest is shadow)."""
        return self.rect().adjusted(tokens.MENU_SHADOW_LEFT, tokens.MENU_SHADOW_TOP,
                                    -tokens.MENU_SHADOW_RIGHT, -tokens.MENU_SHADOW_BOTTOM)

    def popup_under(self, anchor: QWidget) -> None:
        """Open below `anchor`, the card's right edge on the anchor's right edge."""
        self.ensurePolished()
        size = self.sizeHint()
        corner = anchor.mapToGlobal(QPoint(anchor.width(), anchor.height() + tokens.MENU_GAP))
        self.popup(QPoint(corner.x() - size.width() + tokens.MENU_SHADOW_RIGHT,
                          corner.y() - tokens.MENU_SHADOW_TOP))

    # -- painting --------------------------------------------------------------

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt virtual method
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        card = QRectF(self.card_rect())
        radius = tokens.MENU_RADIUS
        self._paint_shadow(painter, card, radius)
        painter.setPen(QPen(QColor(*tokens.MENU_EDGE), 1))
        painter.setBrush(QColor(tokens.MENU_SURFACE))
        painter.drawRoundedRect(card.adjusted(0.5, 0.5, -0.5, -0.5), radius, radius)

        active = self.activeAction()
        for action in self.actions():
            if not action.isVisible():
                continue
            rect = QRectF(self.actionGeometry(action))
            if rect.isEmpty():
                continue
            if action.isSeparator():
                self._paint_rule(painter, rect)
                continue
            if action is active and action.isEnabled():
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(tokens.MENU_ITEM_HOVER))
                painter.drawRoundedRect(rect, tokens.MENU_ITEM_RADIUS, tokens.MENU_ITEM_RADIUS)
            ink = QColor(self.item_color(action))
            painter.setPen(ink)
            painter.setFont(self.font())
            text = rect.adjusted(tokens.MENU_ITEM_PADDING, 0, -tokens.MENU_ITEM_PADDING, 0)
            painter.drawText(text, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                             action.text().split("\t")[0].replace("&&", "\0").replace("&", "").replace("\0", "&"))
            if action.menu() is not None:
                self._paint_arrow(painter, rect, ink)
        painter.end()

    @staticmethod
    def item_color(action: QAction) -> str:
        """The text colour an item is painted with (also used by tests)."""
        if not action.isEnabled():
            return tokens.MENU_TEXT_DISABLED
        if action.property(DANGER_PROPERTY):
            return tokens.MENU_TEXT_DANGER
        return tokens.MENU_TEXT

    @staticmethod
    def _paint_shadow(painter: QPainter, card: QRectF, radius: float) -> None:
        """Rings growing outwards, fading out: a cheap stand-in for a 40 px blur."""
        colour = QColor(*tokens.MENU_SHADOW)
        peak = colour.alphaF()
        reach = tokens.MENU_SHADOW_BLUR / 2
        shifted = card.translated(0, tokens.MENU_SHADOW_OFFSET)
        painter.setPen(Qt.PenStyle.NoPen)
        for step in range(SHADOW_STEPS, 0, -1):
            grow = reach * step / SHADOW_STEPS
            colour.setAlphaF(peak / SHADOW_STEPS * (1 - (step - 1) / SHADOW_STEPS) * 1.6)
            painter.setBrush(colour)
            painter.drawRoundedRect(shifted.adjusted(-grow, -grow, grow, grow), radius + grow, radius + grow)

    def _paint_rule(self, painter: QPainter, rect: QRectF) -> None:
        card = QRectF(self.card_rect())
        y = rect.center().y()
        painter.setPen(QPen(QColor(*tokens.MENU_RULE), 1))
        painter.drawLine(QPointF(card.left() + tokens.MENU_RULE_INSET, y),
                         QPointF(card.right() - tokens.MENU_RULE_INSET, y))

    @staticmethod
    def _paint_arrow(painter: QPainter, rect: QRectF, ink: QColor) -> None:
        """Thin "›": M9 6 l6 6 -6 6 in a 24 box drawn at MENU_ARROW, stroke 2 of 24."""
        size = tokens.MENU_ARROW
        scale = size / 24
        box = QRectF(rect.right() - tokens.MENU_ITEM_PADDING - size, rect.center().y() - size / 2, size, size)
        path = QPainterPath(QPointF(box.left() + 9 * scale, box.top() + 6 * scale))
        path.lineTo(box.left() + 15 * scale, box.top() + 12 * scale)
        path.lineTo(box.left() + 9 * scale, box.top() + 18 * scale)
        pen = QPen(ink, 2 * scale, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(path)
