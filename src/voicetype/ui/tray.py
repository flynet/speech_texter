from __future__ import annotations

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QAction, QActionGroup, QColor, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

STATE_COLORS = {
    "idle": "#4CC38A",
    "loading": "#4F8EF7",
    "recording": "#E05C5C",
    "processing": "#4F8EF7",
    "error": "#E0A64C",
}


def _draw_icon(state: str, size: int) -> QPixmap:
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing, True)
    scale = size / 64.0
    body = QColor("#AEB6C4")
    painter.setPen(Qt.NoPen)
    painter.setBrush(body)
    painter.drawRoundedRect(
        QRectF(24 * scale, 7 * scale, 16 * scale, 29 * scale),
        8 * scale,
        8 * scale,
    )
    pen = QPen(body, max(1.5, 3.0 * scale))
    pen.setCapStyle(Qt.RoundCap)
    painter.setPen(pen)
    painter.setBrush(Qt.NoBrush)
    painter.drawArc(
        QRectF(16 * scale, 23 * scale, 32 * scale, 23 * scale),
        200 * 16,
        140 * 16,
    )
    painter.drawLine(32 * scale, 46 * scale, 32 * scale, 52 * scale)
    painter.drawLine(23 * scale, 54 * scale, 41 * scale, 54 * scale)
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor(STATE_COLORS.get(state, STATE_COLORS["idle"])))
    painter.drawEllipse(QRectF(42 * scale, 42 * scale, 16 * scale, 16 * scale))
    painter.end()
    return pixmap


def state_icon(state: str) -> QIcon:
    icon = QIcon()
    for size in (16, 20, 24, 32, 48, 64):
        icon.addPixmap(_draw_icon(state, size))
    return icon


class TrayIcon(QSystemTrayIcon):
    settings_requested = Signal()
    language_changed = Signal(str)
    check_updates_requested = Signal()
    autostart_toggled = Signal(bool)
    quit_requested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._state = "idle"
        self._status_text = ""
        self.setIcon(state_icon("idle"))
        self.setToolTip("VoiceType")
        self._menu = QMenu()
        self._build_menu()
        self.setContextMenu(self._menu)
        self.activated.connect(self._on_activated)

    def _build_menu(self) -> None:
        settings_action = QAction("Settings...", self._menu)
        settings_action.triggered.connect(self.settings_requested.emit)
        self._menu.addAction(settings_action)

        language_menu = self._menu.addMenu("Language")
        self._language_group = QActionGroup(language_menu)
        self._language_group.setExclusive(True)
        self._language_actions: dict[str, QAction] = {}
        for code, label in (("ru", "Russian"), ("en", "English"), ("auto", "Auto")):
            action = QAction(label, language_menu)
            action.setCheckable(True)
            action.setData(code)
            action.triggered.connect(lambda _checked=False, value=code: self.language_changed.emit(value))
            self._language_group.addAction(action)
            language_menu.addAction(action)
            self._language_actions[code] = action

        self._menu.addSeparator()

        updates_action = QAction("Check for updates...", self._menu)
        updates_action.triggered.connect(self.check_updates_requested.emit)
        self._menu.addAction(updates_action)

        self._autostart_action = QAction("Start with Windows", self._menu)
        self._autostart_action.setCheckable(True)
        self._autostart_action.triggered.connect(self.autostart_toggled.emit)
        self._menu.addAction(self._autostart_action)

        self._menu.addSeparator()

        quit_action = QAction("Quit", self._menu)
        quit_action.triggered.connect(self.quit_requested.emit)
        self._menu.addAction(quit_action)

    def set_state(self, state: str) -> None:
        self._state = state
        self.setIcon(state_icon(state))
        self._update_tooltip()

    def set_status_text(self, text: str) -> None:
        self._status_text = text
        self._update_tooltip()

    def _update_tooltip(self) -> None:
        label = {
            "idle": "Idle",
            "loading": "Loading model",
            "recording": "Recording",
            "processing": "Processing",
            "error": "Error",
        }.get(self._state, self._state.title())
        tooltip = f"VoiceType — {label}"
        if self._status_text:
            tooltip += f"\n{self._status_text}"
        self.setToolTip(tooltip)

    def set_language(self, language: str) -> None:
        action = self._language_actions.get(language)
        if action is None:
            return
        action.blockSignals(True)
        action.setChecked(True)
        action.blockSignals(False)

    def set_autostart(self, enabled: bool) -> None:
        self._autostart_action.blockSignals(True)
        self._autostart_action.setChecked(enabled)
        self._autostart_action.blockSignals(False)

    def notify(self, title: str, message: str) -> None:
        try:
            self.showMessage(title, message, QSystemTrayIcon.Information, 5000)
        except Exception:
            pass

    def _on_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self.settings_requested.emit()
