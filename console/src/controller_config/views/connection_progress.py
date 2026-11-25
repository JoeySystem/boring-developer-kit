"""Visible connection feedback; observes transport state without driving it.

While the Console looks for the device it plays the Blender-rendered device
animation (design decision, 乙 键灯跑马, the designer 11-23 "黑色配色的可以定稿"): the
six lit keys chase in orange on a loop. Once the device is found and the
Console is reading it, the "found" clip plays once — the camera settles to the
top view, the lit keys come on one after another, the round screen shows the
firmware's BORING page — and holds its last frame. The main window waits for
that ending (``settled``) before it fades to the Mode page. Both clips are pre-rendered animated WebP files under
assets/connect_3d (scripts in design-assets/blender-connect). If the WebP
image plugin is missing the page falls back to the live 3D model.
"""
from importlib.resources import as_file, files

from pathlib import Path

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QMovie, QPainter
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from controller_config.accessibility import system_reduces_motion
from controller_config.i18n import set_translatable_text
from controller_config.models import AppState

_CLIPS = files("controller_config.assets").joinpath("connect_3d")
SEARCH_CLIP = "search-loop.webp"
FOUND_CLIP = "found.webp"
# Small versions for the two dialogs that show the device (design ticket 第 2.2 条).
# Not rendered yet: until a file with this name is in assets/connect_3d the dialogs
# keep their live 3D model. Drop the file in and the dialog plays it (loop) instead.
BLE_HELP_CLIP = "ble-help.webp"
AI_SETUP_CLIP = "ai-setup.webp"


def _clip_path(name: str) -> str:
    with as_file(_CLIPS.joinpath(name)) as path:
        return str(path)


def clip_exists(name: str) -> bool:
    with as_file(_CLIPS.joinpath(name)) as path:
        return Path(path).is_file()


class ClipStage(QWidget):
    """Draws the current frame of a QMovie scaled to the widget, centred.

    ``cover`` fills the widget and crops (the full-page connection view);
    ``contain`` shows the whole frame (small dialog versions).
    """

    settled = Signal()  # a play-once clip reached its last frame and holds it

    def __init__(self, parent=None, *, fit: str = "cover"):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._fit = max if fit == "cover" else min
        self._movie: QMovie | None = None
        self._movies: dict[str, QMovie] = {}
        self._once = False
        self.current = ""
        self.is_settled = False

    def load(self, name: str) -> bool:
        movie = QMovie(_clip_path(name), parent=self)
        # Decode frame by frame: caching every 1152x768 frame costs about 600 MB for
        # the two clips; decoding on the fly stays near 100 MB and still jumps to frames.
        movie.setCacheMode(QMovie.CacheMode.CacheNone)
        if not movie.isValid():
            return False
        movie.frameChanged.connect(lambda _frame, m=movie: self._frame(m))
        self._movies[name] = movie
        return True

    def play(self, name: str, *, loop: bool, still: bool = False) -> None:
        movie = self._movies.get(name)
        if movie is None:
            return
        if self._movie is not None and self._movie is not movie:
            self._movie.stop()
        if movie is self._movie and not loop and not still and not self.is_settled \
                and movie.state() is QMovie.MovieState.Running:
            return  # already playing this ending: never start it over
        movie.stop()
        self._movie, self.current = movie, name
        self._once, self.is_settled = not loop, False
        if still:
            # Reduced motion: no loop, no camera move; show the clip's last frame.
            movie.jumpToFrame(max(0, movie.frameCount() - 1))
            self.update()
            return
        if not loop:
            movie.finished.connect(self._hold_last, Qt.ConnectionType.UniqueConnection)
        movie.start()

    def _hold_last(self) -> None:
        movie = self.sender()
        if isinstance(movie, QMovie) and movie is self._movie:
            movie.jumpToFrame(max(0, movie.frameCount() - 1))
            self._settle()

    def _frame(self, movie: QMovie) -> None:
        if movie is self._movie:
            if self._once and movie.currentFrameNumber() >= movie.frameCount() - 1:
                movie.setPaused(True)        # hold the settled top view, keys lit, screen on
                self._settle()
            self.update()

    def _settle(self) -> None:
        self.update()
        if self._once and not self.is_settled:
            self.is_settled = True
            self.settled.emit()

    def stop(self) -> None:
        if self._movie is not None:
            self._movie.stop()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt virtual method
        if self._movie is None:
            return
        frame = self._movie.currentPixmap()
        if frame.isNull():
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        scale = self._fit(self.width() / frame.width(), self.height() / frame.height())
        w, h = frame.width() * scale, frame.height() * scale
        painter.drawPixmap(QRectF((self.width() - w) / 2, (self.height() - h) / 2, w, h), frame,
                           QRectF(frame.rect()))
        painter.end()


def dialog_clip(name: str, side: int, parent=None) -> QWidget | None:
    """The small pre-rendered device for a dialog, or None while it is not rendered."""
    if not clip_exists(name):
        return None
    stage = ClipStage(parent, fit="contain")
    if not stage.load(name):
        stage.deleteLater()
        return None
    stage.setObjectName("dialogDeviceClip")
    stage.setFixedSize(side, side)
    stage.play(name, loop=True, still=system_reduces_motion())
    return stage


class ConnectionProgress(QWidget):
    # The found clip's ending finished (or there is none to wait for after finish_found).
    settled = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("connectionProgress")
        self.setFocusPolicy(Qt.NoFocus)
        self._reduce_motion = system_reduces_motion()
        self.stage = ClipStage(self)
        self.canvas = None
        self._clips = self.stage.load(SEARCH_CLIP) and self.stage.load(FOUND_CLIP)
        self.stage.settled.connect(self.settled)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 12, 24, 28)
        layout.setSpacing(10)
        layout.addStretch(1)
        if not self._clips:
            # No WebP plugin: keep the live 3D model that was here before.
            from controller_config.views.device_silhouette import DeviceModelCanvas
            self.canvas = DeviceModelCanvas(None, {}, None, self)
            self.canvas.set_connection_preview(True)
            layout.addWidget(self.canvas, 0, Qt.AlignHCenter)
        # The big orange "连接中" title is gone from the design (the clip and the line
        # below say it); the label stays hidden because the main window still writes
        # "已连接" into it when the connection completes.
        self.title = QLabel(objectName="connectionProgressTitle")
        self.title.hide()
        self.hint = QLabel(objectName="connectionProgressHint")
        self.hint.setAlignment(Qt.AlignCenter)
        self.hint.setWordWrap(True)
        layout.addWidget(self.hint)
        self._state = None

    def observe(self, state):
        set_translatable_text(self.title, "连接中")
        set_translatable_text(self.hint, "正在寻找设备" if state is AppState.SCANNING
                              else "连接完成后自动进入")
        if state is self._state:
            return
        self._state = state
        if not self._clips:
            return
        if state is AppState.SCANNING:
            if self._reduce_motion:
                self.stage.play(SEARCH_CLIP, loop=True, still=True)
            else:
                self.stage.play(SEARCH_CLIP, loop=True)
        else:
            # Found: the device is there and being read; settle and hold.
            self.stage.play(FOUND_CLIP, loop=False, still=self._reduce_motion)

    def finish_found(self) -> bool:
        """The device is connected: let the found clip end before the Mode page.

        Returns True when ``settled`` will follow (the ending is still to play),
        False when there is nothing to wait for: live 3D fallback, reduced motion,
        hidden, or the ending already played and is holding its last frame.
        """
        if not self._clips or self._reduce_motion or not self.isVisible():
            return False
        if self.stage.current == FOUND_CLIP and self.stage.is_settled:
            return False
        # Connected straight from scanning: play the ending now. Already playing: keep it.
        self.stage.play(FOUND_CLIP, loop=False)
        self._state = AppState.CONNECTING
        return True

    def hideEvent(self, event) -> None:  # noqa: N802 - Qt virtual method
        super().hideEvent(event)
        self.stage.stop()
        self._state = None

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.stage.setGeometry(self.rect())
        self.stage.lower()
        if self.canvas is not None:
            side = max(140, min(460, self.width() - 48, self.height() - 138))
            if self.canvas.width() != side:
                self.canvas.setFixedSize(side, side)
