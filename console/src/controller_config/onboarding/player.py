"""One monotonic timeline for optimized GIF frames and the native screen atlas.

GIF encoders coalesce identical frames. Their frame indices MUST NOT be used as
20 fps screen indices. manifest.json retains each GIF frame's duration.
No browser, video codec, SDK, serial connection or device writes are involved.
"""

from __future__ import annotations

import bisect
import json
from importlib.resources import files
from pathlib import Path

from PySide6.QtCore import QElapsedTimer, QObject, QTimer, Signal
from PySide6.QtGui import QImage, QMovie


def asset_root() -> Path:
    return Path(str(files("controller_config.assets").joinpath("onboarding")))


class GuidePlayer(QObject):
    position_changed = Signal(int)
    playing_changed = Signal(bool)
    error = Signal(str)

    def __init__(
        self, parent: QObject | None = None, *, assets: Path | None = None
    ) -> None:
        super().__init__(parent)
        self.assets = assets or asset_root()
        self.clip = ""
        self.position = 0
        self.duration = 0
        self.playing = False
        self.error_message = ""
        self.movie: QMovie | None = None
        self.model_frame = -1
        self.screen_tile = 0
        self.timeline: dict = {}
        self.manifest: dict = {}
        self.atlas = QImage()
        self._starts: list[int] = []
        self._anchor = 0
        self._clock = QElapsedTimer()
        self._timer = QTimer(self)
        self._timer.setInterval(25)
        self._timer.timeout.connect(self._tick)
        try:
            self.timeline = json.loads((self.assets / "timeline.json").read_text())
            self.manifest = json.loads((self.assets / "manifest.json").read_text())
            self.atlas = QImage(str(self.assets / "atlas.png"))
            if self.atlas.isNull():
                raise ValueError("Screen atlas cannot be decoded")
        except (OSError, ValueError) as exc:
            self.error_message = str(exc)

    def load(self, clip: str, *, autoplay: bool = False) -> bool:
        self.pause()
        self.clip = clip
        self.position = 0
        self.model_frame = -1
        self.duration = 0
        if self.movie is not None:
            self.movie.stop()
            self.movie.deleteLater()
            self.movie = None
        if clip not in self.manifest.get("clips", {}) or clip not in self.timeline.get(
            "clips", {}
        ):
            self.error_message = f"Missing animation manifest: {clip}"
            self.error.emit(self.error_message)
            self.position_changed.emit(0)
            return False
        data = self.manifest["clips"][clip]
        delays = data["frame_durations_ms"]
        total = 0
        self._starts = []
        for delay in delays:
            self._starts.append(total)
            total += delay
        self.duration = total
        self.movie = QMovie(str(self.assets / "clips" / f"{clip}.gif"), parent=self)
        self.movie.setCacheMode(QMovie.CacheMode.CacheAll)
        if not self.movie.isValid() or self.atlas.isNull() or not total:
            self.error_message = f"Cannot decode animation: {clip}"
            self.error.emit(self.error_message)
            self.position_changed.emit(0)
            return False
        self.error_message = ""
        self.seek(0)
        if autoplay:
            self.play()
        return True

    def seek(self, milliseconds: int) -> None:
        if not self.duration or not self.movie or self.error_message:
            return
        self.position = max(0, min(self.duration, int(milliseconds)))
        self._anchor = self.position
        self._clock.restart()
        self._render()

    def _render(self) -> None:
        index = min(
            len(self._starts) - 1, bisect.bisect_right(self._starts, self.position) - 1
        )
        if index != self.model_frame:
            if not self.movie.jumpToFrame(index):
                self.pause()
                self.error_message = f"Cannot decode frame {index}: {self.clip}"
                self.error.emit(self.error_message)
                return
            self.model_frame = index
        frames = self.timeline["clips"][self.clip]
        self.screen_tile = frames[
            min(len(frames) - 1, self.position * self.timeline["fps"] // 1000)
        ]
        self.position_changed.emit(self.position)

    def screen_image(self) -> QImage:
        if self.atlas.isNull() or self.error_message:
            return QImage()
        column = self.screen_tile % self.timeline["columns"]
        row = self.screen_tile // self.timeline["columns"]
        return self.atlas.copy(column * 128, row * 128, 128, 128)

    def play(self) -> None:
        if self.playing or self.error_message or not self.duration:
            return
        if self.position >= self.duration:
            self.seek(0)
        self._anchor = self.position
        self._clock.restart()
        self.playing = True
        self._timer.start()
        self.playing_changed.emit(True)

    def pause(self) -> None:
        self._timer.stop()
        if self.playing:
            self.playing = False
            self.playing_changed.emit(False)

    def replay(self) -> None:
        self.seek(0)
        self.play()

    def _tick(self) -> None:
        if self.playing and self.duration:
            self.position = min(self.duration, self._anchor + self._clock.elapsed())
            self._render()
            if self.position >= self.duration:
                self.pause()

    def close(self) -> None:
        self.pause()
        if self.movie:
            self.movie.stop()
            self.movie.deleteLater()
            self.movie = None
