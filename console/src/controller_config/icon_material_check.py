"""Cancellable image decoding; the worker never opens a window or a device."""
from __future__ import annotations

import json
import sys

from PySide6.QtCore import QCoreApplication, QObject, QProcess, QTimer, Signal
from PySide6.QtGui import QImage

from controller_config.screen_icon import IconImageError, MAX_IMAGE_PIXELS, load_icon_image


def worker_arguments(path: str) -> tuple[str, ...]:
    if getattr(sys, "frozen", False) or "__compiled__" in globals():
        return sys.executable, "--check-icon-material", path
    return sys.executable, "-m", "controller_config.app", "--check-icon-material", path


def run_worker(path: str) -> int:
    # Raw pixels avoid decoding the same file again on the GUI thread. The
    # supported 16-million-pixel limit also bounds this local pipe to 64 MB.
    app = QCoreApplication.instance() or QCoreApplication([])
    try:
        image = load_icon_image(path)
    except IconImageError as exc:
        sys.stdout.buffer.write(json.dumps({"error": exc.reason}).encode() + b"\n")
    except OSError:
        sys.stdout.buffer.write(b'{"error":"read"}\n')
    else:
        header = {"width": image.width(), "height": image.height()}
        sys.stdout.buffer.write(json.dumps(header).encode() + b"\n")
        sys.stdout.buffer.write(image.constBits())
    return 0


class IconMaterialCheck(QObject):
    accepted = Signal(str, QImage)
    rejected = Signal(str)
    busy_changed = Signal(bool)

    def __init__(self, parent: QObject | None = None, *, timeout_ms: int = 15000) -> None:
        super().__init__(parent)
        self._process: QProcess | None = None
        self._path = ""
        self._output = bytearray()
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(timeout_ms)
        self._timer.timeout.connect(lambda: self._fail("timeout"))

    @property
    def busy(self) -> bool:
        return self._process is not None

    def start(self, path: str) -> None:
        self.cancel()
        self._path = path
        # App ownership lets cancellation kill asynchronously; deleting a page
        # must not invoke a running QProcess destructor that waits for exit.
        process = QProcess(QCoreApplication.instance())
        self._process = process
        process.setProcessChannelMode(QProcess.SeparateChannels)
        process.readyReadStandardOutput.connect(self._read)
        process.readyReadStandardError.connect(process.readAllStandardError)
        process.finished.connect(self._finished)
        process.finished.connect(process.deleteLater)
        process.errorOccurred.connect(self._error)
        self.destroyed.connect(process.kill)
        QCoreApplication.instance().aboutToQuit.connect(process.kill)
        args = worker_arguments(path)
        self.busy_changed.emit(True)
        self._timer.start()
        process.start(args[0], list(args[1:]))

    def cancel(self) -> None:
        process, self._process = self._process, None
        self._timer.stop()
        self._output.clear()
        if process is not None:
            process.kill()
            self.busy_changed.emit(False)

    def _fail(self, reason: str) -> None:
        self.cancel()
        self.rejected.emit(reason)

    def _read(self) -> None:
        if self.sender() != self._process or self._process is None:
            return
        self._output.extend(bytes(self._process.readAllStandardOutput()))
        if len(self._output) > MAX_IMAGE_PIXELS * 4 + 1024:
            self._fail("damaged")

    def _error(self, error: QProcess.ProcessError) -> None:
        if self.sender() == self._process and error == QProcess.FailedToStart:
            process = self._process
            self._fail("worker")
            process.deleteLater()

    def _finished(self, code: int, status: QProcess.ExitStatus) -> None:
        if self.sender() != self._process or self._process is None:
            return
        self._read()
        if self._process is None:
            return
        output = self._output
        path = self._path
        self._process = None
        self._output = bytearray()
        self._timer.stop()
        self.busy_changed.emit(False)
        if code != 0 or status != QProcess.NormalExit:
            self.rejected.emit("worker")
            return
        try:
            separator = output.index(b"\n")
            result = json.loads(output[:separator])
            pixels = memoryview(output)[separator + 1:]
            if "error" in result:
                self.rejected.emit(result["error"])
                return
            width, height = result["width"], result["height"]
            if not (width > 0 and height > 0 and width * height <= MAX_IMAGE_PIXELS
                    and len(pixels) == width * height * 4):
                raise ValueError("Incomplete image output")
            image = QImage(pixels, width, height, width * 4, QImage.Format_ARGB32).copy()
        except (ValueError, KeyError, TypeError):
            self.rejected.emit("worker")
            return
        self.accepted.emit(path, image)
