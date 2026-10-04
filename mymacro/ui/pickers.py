"""Two pickers that let the user point at the screen instead of typing numbers.

RegionPicker  - freezes the screen, drag a rectangle, get [left, top, w, h]
CoordPicker   - park the mouse on the target, press F2, get (x, y)

Both are QDialogs driven by `exec()`. That matters: they are opened from
inside another dialog that is itself running `exec()`, and Qt only lets the
innermost modal dialog receive input. A plain QWidget overlay would be shown
but silently refuse every click.

Hiding the calling dialog to get it out of the screenshot is not an option
either. `QDialog::setVisible(false)` exits the modal event loop, so `exec()`
would return Rejected the moment the overlay opened and the edit would be
thrown away. `invisible_while()` sets the window opacity to zero instead,
which keeps the loop alive and still keeps the window out of the capture.
"""

from __future__ import annotations

from contextlib import contextmanager

import cv2
import numpy as np
from PySide6.QtCore import QEventLoop, QPoint, QRect, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from .. import vision
from ..hotkeys import SingleKeyCapture
from ..input_backend import position as cursor_position

# Long enough for the compositor to apply the opacity change before the
# screenshot is taken.
HIDE_SETTLE_MS = 250


def pump_events(milliseconds: int) -> None:
    """Keep the UI responsive for a while without blocking the event loop."""
    loop = QEventLoop()
    QTimer.singleShot(milliseconds, loop.quit)
    loop.exec()


@contextmanager
def invisible_while(widgets: list[QWidget]):
    """Make windows invisible to a screen capture without hiding them.

    Hiding is what we actually want, but it cancels any `exec()` these windows
    are running, and hiding a parent hides its dialogs too.
    """
    saved = [(widget, widget.windowOpacity()) for widget in widgets]
    for widget, _ in saved:
        widget.setWindowOpacity(0.0)
    pump_events(HIDE_SETTLE_MS)
    try:
        yield
    finally:
        for widget, opacity in saved:
            widget.setWindowOpacity(opacity)


class RegionPicker(QDialog):
    """Full-virtual-desktop overlay showing a frozen screenshot.

    Drag to select, Esc cancels. The selection comes back in absolute screen
    coordinates, which is what both mss and SetCursorPos use.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setSizeGripEnabled(False)

        self.region: list[int] | None = None

        box = vision.virtual_screen()
        self._origin = (box["left"], box["top"])

        frame = vision.grab(None)  # BGR
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        self._buffer = np.ascontiguousarray(rgb)  # keep alive for QImage
        height, width, _ = self._buffer.shape
        image = QImage(self._buffer.data, width, height, 3 * width, QImage.Format.Format_RGB888)
        self._pixmap = QPixmap.fromImage(image)

        self.setGeometry(box["left"], box["top"], box["width"], box["height"])
        self._start: QPoint | None = None
        self._end: QPoint | None = None

    def pick(self) -> list[int] | None:
        """Show the overlay and return the chosen region, or None."""
        self.exec()
        return self.region

    # --- painting ---------------------------------------------------------
    def paintEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        painter = QPainter(self)
        painter.drawPixmap(0, 0, self._pixmap)
        painter.fillRect(self.rect(), QColor(0, 0, 0, 110))

        rect = self._current_rect()
        if rect is not None and rect.width() > 0 and rect.height() > 0:
            painter.drawPixmap(rect, self._pixmap, rect)
            painter.setPen(QPen(QColor(60, 160, 255), 2))
            painter.drawRect(rect)
            painter.setPen(QPen(QColor(255, 255, 255)))
            painter.drawText(
                rect.left() + 4, max(14, rect.top() - 6),
                f"{rect.width()} x {rect.height()}",
            )
        else:
            painter.setPen(QPen(QColor(255, 255, 255)))
            painter.drawText(
                self.rect(),
                Qt.AlignmentFlag.AlignCenter,
                "찾을 이미지 영역을 드래그하세요  (Esc: 취소)",
            )

    def _current_rect(self) -> QRect | None:
        if self._start is None or self._end is None:
            return None
        return QRect(self._start, self._end).normalized()

    # --- interaction ------------------------------------------------------
    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._start = event.position().toPoint()
            self._end = self._start
            self.update()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._start is not None:
            self._end = event.position().toPoint()
            self.update()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton:
            return
        rect = self._current_rect()
        if rect is None or rect.width() < 3 or rect.height() < 3:
            self.reject()
            return
        self.region = [
            rect.left() + self._origin[0],
            rect.top() + self._origin[1],
            rect.width(),
            rect.height(),
        ]
        self.accept()


class CoordPicker(QDialog):
    """Shows the live mouse position; F2 records it, Esc cancels.

    Nothing covers the screen, so you can aim at a target inside another app.
    """

    _captured = Signal()
    _cancelled = Signal()

    def __init__(self, parent: QWidget | None = None, trigger: str = "f2") -> None:
        super().__init__(parent)
        self.setWindowTitle("좌표 찍기")
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowStaysOnTopHint)
        self.result_point: tuple[int, int] | None = None
        self.trigger = trigger

        layout = QVBoxLayout(self)
        layout.addWidget(
            QLabel(
                f"마우스를 원하는 위치에 두고 <b>{trigger.upper()}</b> 키를 누르세요.<br>"
                "Esc 를 누르면 취소합니다."
            )
        )
        self._position_label = QLabel("현재 좌표: -")
        self._position_label.setStyleSheet("font-size: 16px; font-weight: bold;")
        layout.addWidget(self._position_label)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._refresh_position)
        self._timer.start(60)

        # The pynput callbacks fire on a listener thread; bounce through a
        # signal so the dialog is only touched on the Qt thread.
        self._captured.connect(self._on_captured)
        self._cancelled.connect(self.reject)
        self._capture = SingleKeyCapture(
            trigger,
            on_trigger=self._captured.emit,
            on_cancel=self._cancelled.emit,
        )
        self._capture.start()

    def _refresh_position(self) -> None:
        x, y = cursor_position()
        self._position_label.setText(f"현재 좌표: ({int(x)}, {int(y)})")

    def _on_captured(self) -> None:
        self.result_point = cursor_position()
        self.accept()

    def done(self, code: int) -> None:  # noqa: D102
        self._timer.stop()
        self._capture.stop()
        super().done(code)
