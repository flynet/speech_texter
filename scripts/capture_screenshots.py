"""Capture documentation screenshots from the real VoiceType widgets.

The script uses a small stub application instead of a real VoiceType instance,
so it does not touch the user's configuration, microphone list, or hardware
state. It renders deterministic demo data and writes PNG files to
``docs/screenshots``.

Run from the repository root::

    .venv\\Scripts\\python.exe scripts\\capture_screenshots.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import pynput  # noqa: F401  (must be imported before PySide6)

from PySide6.QtCore import QObject, QPoint, Qt, Signal
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QGroupBox, QScrollArea

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from voicetype import cuda, hardware, models  # noqa: E402
from voicetype.config import AppConfig  # noqa: E402
from voicetype.recorder import Recorder  # noqa: E402
from voicetype.ui import theme  # noqa: E402
from voicetype.ui.overlay import OverlayWindow  # noqa: E402
from voicetype.ui.settings import SettingsWindow  # noqa: E402
from voicetype.ui.tray import TrayIcon  # noqa: E402

OUTPUT = ROOT / "docs" / "screenshots"
DEMO_MICROPHONE = "Microphone (USB Audio Device)"


class DemoApplication(QObject):
    """Minimal stand-in for VoiceTypeApplication used by SettingsWindow."""

    model_download_progress = Signal(str, int, int)
    model_download_finished = Signal(str, bool, str)
    cuda_progress = Signal(int, int)
    cuda_finished = Signal(bool, str)

    def __init__(self) -> None:
        super().__init__()
        self.config = AppConfig(
            hotkey="ctrl+alt+space",
            hotkey_mode="hold",
            language="ru",
            model="auto",
            device="auto",
            microphone=DEMO_MICROPHONE,
            sounds=True,
            max_record_seconds=600,
            idle_unload_minutes=0,
            overlay_position="bottom-center",
            autostart=False,
        )
        self.config.normalize()

    @property
    def active_model_download(self) -> None:
        return None

    @property
    def cuda_download_active(self) -> bool:
        return False

    def set_hotkey_capture(self, active: bool) -> None:
        pass

    def request_model_download(self, key: str) -> None:
        pass

    def cancel_model_download(self, key: str | None = None) -> None:
        pass

    def download_cuda_pack(self) -> None:
        pass

    def cancel_cuda_download(self) -> None:
        pass

    def check_updates(self, manual: bool = True) -> None:
        pass

    def apply_settings(self, values: dict) -> bool:
        return True


def _prepare_demo_environment() -> None:
    """Replace hardware and microphone lookups with neutral demo values."""
    models.is_available = lambda key: key == "small"
    models.model_status = lambda key: "bundled" if key == "small" else "missing"
    hardware.detect_nvidia_gpus = lambda: []
    hardware.cuda_available = lambda: False
    cuda.cuda_pack_installed = lambda: False
    Recorder.input_devices = staticmethod(
        lambda: [(0, DEMO_MICROPHONE), (1, "Microphone Array (Built-in)")]
    )


def _decorate_test_group(window: SettingsWindow) -> None:
    """Fill the test group with a completed demo result for the screenshot."""
    window._test_status.setText("Test complete.")
    window._test_text.setPlainText(
        "Проверка модели и микрофона: речь распознана полностью."
    )
    window._test_meta.setText(
        "Small (base.en) · CPU · Microphone (USB Audio Device) · 3.4 s · RTF 0.48"
    )
    window._test_copy.setEnabled(True)
    window._test_clear.setEnabled(True)


def _settings_screenshots(app: QApplication) -> None:
    window = SettingsWindow(DemoApplication())
    window.resize(660, 860)
    window.show()
    QTest.qWait(400)

    window.grab().save(str(OUTPUT / "settings-dictation.png"), "PNG")

    scroll = window.findChild(QScrollArea)
    if scroll is not None:
        bar = scroll.verticalScrollBar()
        audio = next(
            (group for group in window.findChildren(QGroupBox) if group.title() == "Audio"),
            None,
        )
        _decorate_test_group(window)
        bar.setValue(audio.y() - 12 if audio is not None else bar.maximum())
        QTest.qWait(250)
        window.grab().save(str(OUTPUT / "settings-audio-system.png"), "PNG")

    window.shutdown()
    window.close()
    QTest.qWait(100)


def _tray_screenshot() -> None:
    tray = TrayIcon()
    tray.set_language("ru")
    tray.set_autostart(False)
    menu = tray._menu
    menu.adjustSize()
    menu.popup(QPoint(-4000, -4000))
    QTest.qWait(250)
    menu.grab().save(str(OUTPUT / "tray-menu.png"), "PNG")
    menu.close()
    QTest.qWait(100)


def _overlay_screenshot() -> None:
    overlay = OverlayWindow()
    overlay.set_theme(True)
    overlay.show_state("recording")
    QTest.qWait(250)
    for level in (0.12, 0.30, 0.55, 0.80, 0.62, 0.44, 0.72, 0.95, 0.70, 0.42,
                  0.28, 0.50, 0.75, 0.58, 0.36, 0.22, 0.48, 0.82, 0.90, 0.66,
                  0.40, 0.26, 0.18, 0.10):
        overlay.set_level(level)
        QTest.qWait(15)
    panel = overlay.grab()

    canvas = QImage(360, 140, QImage.Format_ARGB32)
    canvas.fill(QColor("#15161A"))
    painter = QPainter(canvas)
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.drawPixmap((canvas.width() - panel.width()) // 2,
                       (canvas.height() - panel.height()) // 2,
                       panel)
    painter.end()
    canvas.save(str(OUTPUT / "recording-overlay.png"), "PNG")
    overlay.close()


def main() -> int:
    _prepare_demo_environment()
    app = QApplication([])
    theme.apply_theme(app, dark=True)
    OUTPUT.mkdir(parents=True, exist_ok=True)

    _settings_screenshots(app)
    _tray_screenshot()
    _overlay_screenshot()

    for path in sorted(OUTPUT.glob("*.png")):
        print(f"Wrote {path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
