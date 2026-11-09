from __future__ import annotations

import ctypes
import logging
import math

from PySide6.QtCore import QEasingCurve, QPoint, QPropertyAnimation, QRectF, QTimer, Qt
from PySide6.QtGui import QColor, QCursor, QFont, QFontMetrics, QGuiApplication, QPainter, QPen
from PySide6.QtWidgets import QWidget

from . import theme

log = logging.getLogger(__name__)

GWL_EXSTYLE = -20
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000


class OverlayWindow(QWidget):
    WIDTH = 220
    HEIGHT = 56

    def __init__(self) -> None:
        super().__init__(
            None,
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool
            | Qt.WindowDoesNotAcceptFocus
            | Qt.WindowTransparentForInput,
        )
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setFocusPolicy(Qt.NoFocus)
        self.setFixedSize(self.WIDTH, self.HEIGHT)
        self.setWindowOpacity(0.0)

        self._state = "hidden"
        self._message = ""
        self._levels = [0.0] * 24
        self._phase = 0.0
        self._position = "bottom-center"
        self._dpr = 1.0
        self._hide_after_fade = False

        self._timer = QTimer(self)
        self._timer.setInterval(33)
        self._timer.timeout.connect(self._tick)

        self._fade = QPropertyAnimation(self, b"windowOpacity", self)
        self._fade.setDuration(130)
        self._fade.setEasingCurve(QEasingCurve.OutCubic)
        self._fade.finished.connect(self._on_fade_finished)

        # Create the native window now so Win32 flags are applied before the first show.
        self.winId()

    def set_theme(self, dark: bool) -> None:
        self._dark = dark
        self.update()

    def set_position(self, position: str) -> None:
        self._position = position
        if self.isVisible():
            self._move_to_position()

    def show_state(self, state: str, message: str = "") -> None:
        if state == "hidden":
            self.hide_overlay()
            return
        self._state = state
        self._message = message
        if not self.isVisible():
            self.setWindowOpacity(0.0)
            self._move_to_position()
            self.show()
            self._apply_no_activate()
            self.raise_()
            self._fade.stop()
            self._hide_after_fade = False
            self._fade.setStartValue(0.0)
            self._fade.setEndValue(1.0)
            self._fade.start()
        self._phase = 0.0
        if state in {"recording", "processing"}:
            self._timer.start()
        else:
            self._timer.stop()
        self.update()

    def hide_overlay(self) -> None:
        if not self.isVisible():
            return
        self._state = "hidden"
        self._timer.stop()
        self._fade.stop()
        self._hide_after_fade = True
        self._fade.setStartValue(self.windowOpacity())
        self._fade.setEndValue(0.0)
        self._fade.start()

    def set_level(self, rms: float) -> None:
        value = min(1.0, max(0.0, rms * 10.0))
        self._levels.pop(0)
        self._levels.append(value)
        if self._state == "recording":
            self.update()

    def _tick(self) -> None:
        if self._state == "recording":
            self._levels = [value * 0.96 for value in self._levels]
        elif self._state == "processing":
            self._phase = (self._phase + 0.30) % (2.0 * math.pi)
        self.update()

    def _on_fade_finished(self) -> None:
        if self._hide_after_fade:
            self._hide_after_fade = False
            self.hide()

    def _move_to_position(self) -> None:
        screen = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        if screen is None:
            return
        area = screen.availableGeometry()
        margin = 48
        if self._position == "bottom-left":
            x = area.left() + 24
            y = area.bottom() - self.HEIGHT - margin
        elif self._position == "bottom-right":
            x = area.right() - self.WIDTH - 24
            y = area.bottom() - self.HEIGHT - margin
        elif self._position == "top-center":
            x = area.center().x() - self.WIDTH // 2
            y = area.top() + 80
        else:
            x = area.center().x() - self.WIDTH // 2
            y = area.bottom() - self.HEIGHT - margin
        self.move(QPoint(int(x), int(y)))

    def _apply_no_activate(self) -> None:
        try:
            hwnd = int(self.winId())
            user32 = ctypes.windll.user32
            user32.GetWindowLongW.argtypes = [ctypes.c_void_p, ctypes.c_int]
            user32.GetWindowLongW.restype = ctypes.c_long
            user32.SetWindowLongW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_long]
            user32.SetWindowLongW.restype = ctypes.c_long
            style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            style |= WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW | WS_EX_TRANSPARENT
            user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style)
        except Exception:
            log.debug("Could not apply no-activate window styles", exc_info=True)

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        QTimer.singleShot(0, self._apply_no_activate)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        colors = theme.colors(getattr(self, "_dark", None))
        panel = QColor(colors["panel"])
        panel.setAlpha(238)
        border = QColor(colors["border"])
        border.setAlpha(220)

        rect = QRectF(0.5, 0.5, self.WIDTH - 1.0, self.HEIGHT - 1.0)
        painter.setPen(QPen(border, 1.0))
        painter.setBrush(panel)
        painter.drawRoundedRect(rect, 18.0, 18.0)

        if self._state == "recording":
            self._paint_wave(painter, QColor(colors["accent"]))
        elif self._state == "processing":
            self._paint_spinner(painter, QColor(colors["accent"]), colors)
        elif self._state == "error":
            self._paint_error(painter, QColor(colors["danger"]), colors)

    def _paint_wave(self, painter: QPainter, color: QColor) -> None:
        painter.setPen(Qt.NoPen)
        painter.setBrush(color)
        bar_width = 4.0
        gap = 4.0
        left = 16.0
        center_y = self.HEIGHT / 2.0
        for index, level in enumerate(self._levels):
            height = 3.0 + max(0.0, min(1.0, level)) * 29.0
            x = left + index * (bar_width + gap)
            painter.drawRoundedRect(
                QRectF(x, center_y - height / 2.0, bar_width, height),
                2.0,
                2.0,
            )

    def _paint_spinner(
        self, painter: QPainter, color: QColor, colors: dict[str, str]
    ) -> None:
        pen = QPen(color, 2.6)
        pen.setCapStyle(Qt.RoundCap)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        painter.drawArc(
            QRectF(24.0, 18.0, 20.0, 20.0),
            int(math.degrees(self._phase) * -16),
            120 * 16,
        )
        painter.setPen(QColor(colors["text"]))
        font = QFont("Segoe UI", 9)
        painter.setFont(font)
        painter.drawText(
            QRectF(54.0, 0.0, self.WIDTH - 66.0, self.HEIGHT),
            Qt.AlignVCenter | Qt.AlignLeft,
            "Processing",
        )

    def _paint_error(
        self, painter: QPainter, color: QColor, colors: dict[str, str]
    ) -> None:
        painter.setPen(Qt.NoPen)
        painter.setBrush(color)
        painter.drawEllipse(QRectF(26.0, 22.0, 12.0, 12.0))
        painter.setPen(QColor(colors["text"]))
        font = QFont("Segoe UI", 9)
        painter.setFont(font)
        metrics = QFontMetrics(font)
        text = metrics.elidedText(
            self._message or "Error", Qt.ElideRight, int(self.WIDTH - 62)
        )
        painter.drawText(
            QRectF(50.0, 0.0, self.WIDTH - 62.0, self.HEIGHT),
            Qt.AlignVCenter | Qt.AlignLeft,
            text,
        )
