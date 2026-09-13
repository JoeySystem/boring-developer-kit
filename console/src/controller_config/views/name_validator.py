from PySide6.QtGui import QValidator


class NameLengthValidator(QValidator):
    """Match JSON Schema/Python code-point length, including non-BMP emoji."""

    def __init__(self, maximum: int, parent=None):
        super().__init__(parent)
        self.maximum = maximum

    def validate(self, text: str, position: int):
        state = QValidator.State.Acceptable if len(text) <= self.maximum else QValidator.State.Invalid
        return state, text, position
