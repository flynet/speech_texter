from __future__ import annotations

import logging

from PySide6.QtCore import QEvent, QTimer, Qt, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from .. import __version__, cuda, hardware, models
from ..config import AppConfig
from ..hotkeys import (
    canonical_vk,
    format_hotkey,
    parse_hotkey,
    validate_hotkey,
)
from ..recorder import Recorder
from ..testbench import RecognitionTester

log = logging.getLogger(__name__)


class HotkeyEdit(QLineEdit):
    capture_started = Signal()
    capture_finished = Signal()
    captured = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setReadOnly(True)
        self.setPlaceholderText("Click and press a key or combination")
        self._capturing = False
        self._modifiers: set[str] = set()
        self._old_text = ""

    def start_capture(self) -> None:
        if self._capturing:
            return
        self._old_text = self.text()
        self._capturing = True
        self._modifiers = set()
        self.setText("Press keys...")
        self.setFocus(Qt.OtherFocusReason)
        self.capture_started.emit()

    def cancel_capture(self) -> None:
        if not self._capturing:
            return
        self._capturing = False
        self.setText(self._old_text)
        self.capture_finished.emit()

    def is_capturing(self) -> bool:
        return self._capturing

    def finish_capture(self) -> None:
        """End capture and keep the key text that is currently displayed.

        Clicking Apply while the field is still capturing used to trigger a
        focus-out cancel, which silently reverted the visible combination to
        the previous value. Keeping the text lets Apply validate it and show a
        clear message when the combination is incomplete.
        """
        if not self._capturing:
            return
        self._capturing = False
        if self.text() == "Press keys...":
            self.setText(self._old_text)
        self.capture_finished.emit()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        self.start_capture()
        event.accept()

    def focusOutEvent(self, event) -> None:  # noqa: N802
        if getattr(self, "_capturing", False):
            self.finish_capture()
        super().focusOutEvent(event)

    def event(self, event) -> bool:  # noqa: N802
        if (
            getattr(self, "_capturing", False)
            and event.type() == QEvent.KeyPress
            and event.key() in (Qt.Key_Tab, Qt.Key_Backtab)
        ):
            self.keyPressEvent(event)
            return True
        return super().event(event)

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if not getattr(self, "_capturing", False):
            return super().keyPressEvent(event)
        key = event.key()
        if key == Qt.Key_Escape:
            self.cancel_capture()
            return
        modifier = {
            Qt.Key_Control: "ctrl",
            Qt.Key_Shift: "shift",
            Qt.Key_Alt: "alt",
            Qt.Key_Meta: "win",
        }.get(key)
        if modifier is not None:
            if modifier == "win":
                QMessageBox.warning(
                    self,
                    "Hotkey",
                    "The Windows key cannot be used in a global hotkey.",
                )
                self.cancel_capture()
                return
            self._modifiers.add(modifier)
            self.setText(
                "+".join(
                    part for part in ("Ctrl", "Alt", "Shift")
                    if part.lower() in self._modifiers
                )
            )
            return
        name = self._key_name(event)
        if name is None:
            return
        keys = set(self._modifiers)
        keys.add(name)
        try:
            validate_hotkey("+".join(keys))
        except ValueError as exc:
            QMessageBox.warning(self, "Hotkey", str(exc))
            self.cancel_capture()
            return
        combo = format_hotkey(keys)
        self.setText(combo)
        self._capturing = False
        log.info("Hotkey captured: %s", combo)
        self.capture_finished.emit()
        self.captured.emit(combo)
        self.clearFocus()

    @staticmethod
    def _key_name(event) -> str | None:
        # Prefer the Windows virtual-key code. It is layout independent, so a
        # Russian layout still records the physical key ("q") instead of the
        # Cyrillic character that key produces ("й"). The global listener
        # matches virtual-key codes, so both sides must use the same names.
        native_vk = int(getattr(event, "nativeVirtualKey", lambda: 0)() or 0)
        if native_vk:
            name = canonical_vk(native_vk)
            if name is not None:
                return name
        key = event.key()
        special = {
            Qt.Key_Space: "space",
            Qt.Key_Return: "enter",
            Qt.Key_Enter: "enter",
            Qt.Key_Tab: "tab",
            Qt.Key_Backtab: "tab",
            Qt.Key_Backspace: "backspace",
            Qt.Key_Delete: "delete",
            Qt.Key_Insert: "insert",
            Qt.Key_Home: "home",
            Qt.Key_End: "end",
            Qt.Key_PageUp: "pageup",
            Qt.Key_PageDown: "pagedown",
            Qt.Key_Up: "up",
            Qt.Key_Down: "down",
            Qt.Key_Left: "left",
            Qt.Key_Right: "right",
            Qt.Key_Pause: "pause",
            Qt.Key_Print: "printscreen",
            Qt.Key_CapsLock: "capslock",
            Qt.Key_NumLock: "numlock",
            Qt.Key_ScrollLock: "scrolllock",
            Qt.Key_Menu: "menu",
        }
        if key in special:
            return special[key]
        if Qt.Key_F1 <= key <= Qt.Key_F24:
            return f"f{key - Qt.Key_F1 + 1}"
        if Qt.Key_A <= key <= Qt.Key_Z:
            return chr(key).lower()
        if Qt.Key_0 <= key <= Qt.Key_9:
            return chr(key)
        text = event.text()
        if text and len(text) == 1 and text.isprintable() and not text.isspace():
            return text.lower()
        return None


class ModelRow(QWidget):
    download_requested = Signal(str)
    cancel_requested = Signal(str)

    def __init__(self, info: models.ModelInfo, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.key = info.key
        self._info = info
        self._downloading = False
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 2, 0, 2)
        layout.setSpacing(8)

        self._name = QLabel(info.display_name)
        self._name.setMinimumWidth(116)
        self._name.setToolTip(info.description)
        self._status = QLabel("")
        self._status.setObjectName("muted")
        self._status.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self._progress = QProgressBar()
        self._progress.setFixedWidth(120)
        self._progress.setVisible(False)
        self._button = QPushButton("Download")
        self._button.setFixedWidth(90)
        self._button.clicked.connect(self._on_button)

        layout.addWidget(self._name)
        layout.addWidget(self._status, 1)
        layout.addWidget(self._progress)
        layout.addWidget(self._button)
        self.update_status(models.model_status(self.key))

    def update_status(self, status: str) -> None:
        self._downloading = False
        self._progress.setVisible(False)
        if status in {"bundled", "downloaded"}:
            self._status.setText("Bundled" if status == "bundled" else "Downloaded")
            self._button.setText("Installed")
            self._button.setEnabled(False)
        else:
            self._status.setText("Not downloaded")
            self._button.setText("Download")
            self._button.setEnabled(True)

    def set_downloading(self, active: bool) -> None:
        self._downloading = active
        self._progress.setVisible(active)
        self._button.setText("Cancel" if active else "Download")
        self._button.setEnabled(True)
        if active:
            self._status.setText("Downloading...")
            self._progress.setRange(0, 0)

    def set_progress(self, done: int, total: int) -> None:
        if total > 0:
            self._progress.setRange(0, 100)
            self._progress.setValue(min(100, int(done * 100 / total)))
            self._progress.setFormat(
                f"{done / (1024 * 1024):.0f} / {total / (1024 * 1024):.0f} MB"
            )
        else:
            self._progress.setRange(0, 0)
            self._progress.setFormat(f"{done / (1024 * 1024):.0f} MB")

    def _on_button(self) -> None:
        if self._downloading:
            self.cancel_requested.emit(self.key)
        else:
            self.download_requested.emit(self.key)


class SettingsWindow(QDialog):
    def __init__(self, app, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._app = app
        self._config: AppConfig = app.config
        self._cuda_downloading = False
        self.setWindowTitle("VoiceType Settings")
        self.setMinimumSize(600, 620)
        self.resize(640, 820)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 14, 14, 14)
        outer.setSpacing(10)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(2, 2, 8, 2)
        layout.setSpacing(10)

        header = QLabel("VoiceType")
        header.setObjectName("title")
        layout.addWidget(header)
        subtitle = QLabel("Local push-to-talk speech to text")
        subtitle.setObjectName("muted")
        layout.addWidget(subtitle)

        layout.addWidget(self._build_dictation_group())
        layout.addWidget(self._build_model_group())
        layout.addWidget(self._build_audio_group())
        layout.addWidget(self._build_test_group())
        layout.addWidget(self._build_appearance_group())
        layout.addWidget(self._build_system_group())
        layout.addWidget(self._build_about_group())
        layout.addStretch(1)

        scroll.setWidget(content)
        outer.addWidget(scroll, 1)

        buttons = QHBoxLayout()
        self._status_label = QLabel("")
        self._status_label.setObjectName("muted")
        buttons.addWidget(self._status_label, 1)
        self._close_button = QPushButton("Close")
        self._close_button.clicked.connect(self.hide)
        self._apply_button = QPushButton("Apply")
        self._apply_button.setObjectName("primaryButton")
        self._apply_button.clicked.connect(self.apply)
        buttons.addWidget(self._close_button)
        buttons.addWidget(self._apply_button)
        outer.addLayout(buttons)

        self._app.model_download_progress.connect(self._on_model_progress)
        self._app.model_download_finished.connect(self._on_model_finished)
        self._app.cuda_progress.connect(self._on_cuda_progress)
        self._app.cuda_finished.connect(self._on_cuda_finished)

        self._tester = RecognitionTester(self)
        self._tester.state_changed.connect(self._on_test_state)
        self._tester.status_changed.connect(self._on_test_status)
        self._tester.level_changed.connect(self._on_test_level)
        self._tester.elapsed_changed.connect(self._on_test_elapsed)
        self._tester.result_ready.connect(self._on_test_result)
        self._tester.error.connect(self._on_test_error)
        self._test_controls = [
            self._model_combo,
            self._device_combo,
            self._language_combo,
            self._mic_combo,
            self._mic_refresh_button,
            self._prompt_edit,
        ]

        self.refresh()

    def _build_dictation_group(self) -> QGroupBox:
        group = QGroupBox("Dictation")
        form = QFormLayout(group)
        form.setLabelAlignment(Qt.AlignLeft)
        form.setFormAlignment(Qt.AlignLeft | Qt.AlignTop)
        form.setHorizontalSpacing(18)
        form.setVerticalSpacing(8)

        self._hotkey_edit = HotkeyEdit()
        self._hotkey_edit.capture_started.connect(lambda: self._app.set_hotkey_capture(True))
        self._hotkey_edit.capture_finished.connect(lambda: self._app.set_hotkey_capture(False))
        form.addRow("Hotkey", self._hotkey_edit)

        hint = QLabel(
            "Use a modifier combination (for example Ctrl+Alt+Space), or a single "
            "F1-F24 / Space key. Single-key hotkeys are captured globally and are "
            "not typed into the focused window."
        )
        hint.setWordWrap(True)
        hint.setObjectName("muted")
        form.addRow(hint)

        self._mode_combo = QComboBox()
        self._mode_combo.addItem("Push-to-talk (hold)", "hold")
        self._mode_combo.addItem("Toggle (press to start / stop)", "toggle")
        form.addRow("Mode", self._mode_combo)

        self._language_combo = QComboBox()
        self._language_combo.addItem("Russian", "ru")
        self._language_combo.addItem("English", "en")
        self._language_combo.addItem("Auto (mixed speech)", "auto")
        form.addRow("Language", self._language_combo)

        self._max_record_spin = QSpinBox()
        self._max_record_spin.setRange(1, 60)
        self._max_record_spin.setSuffix(" min")
        form.addRow("Maximum recording", self._max_record_spin)

        self._keep_clipboard_check = QCheckBox(
            "Keep the recognized text in the clipboard"
        )
        form.addRow("", self._keep_clipboard_check)

        clipboard_hint = QLabel(
            "If the text was not inserted automatically, click the target field "
            "and press Ctrl+V. Disable this to restore the previous clipboard "
            "content after each insertion."
        )
        clipboard_hint.setWordWrap(True)
        clipboard_hint.setObjectName("muted")
        form.addRow(clipboard_hint)
        return group

    def _build_model_group(self) -> QGroupBox:
        group = QGroupBox("Speech model")
        layout = QVBoxLayout(group)
        form = QFormLayout()
        form.setHorizontalSpacing(18)
        form.setVerticalSpacing(8)

        self._device_combo = QComboBox()
        self._device_combo.addItem("Auto", "auto")
        self._device_combo.addItem("CUDA (NVIDIA)", "cuda")
        self._device_combo.addItem("CPU", "cpu")
        form.addRow("Device", self._device_combo)

        self._model_combo = QComboBox()
        form.addRow("Model", self._model_combo)

        self._idle_spin = QSpinBox()
        self._idle_spin.setRange(0, 120)
        self._idle_spin.setSuffix(" min")
        self._idle_spin.setSpecialValueText("Never")
        form.addRow("Unload model when idle", self._idle_spin)
        layout.addLayout(form)

        installed_label = QLabel("Models")
        installed_label.setObjectName("muted")
        layout.addWidget(installed_label)
        self._model_rows: dict[str, ModelRow] = {}
        for key in models.MODEL_ORDER:
            row = ModelRow(models.MODELS[key])
            row.download_requested.connect(self._app.request_model_download)
            row.cancel_requested.connect(self._app.cancel_model_download)
            self._model_rows[key] = row
            layout.addWidget(row)

        prompt_label = QLabel("Initial prompt / vocabulary")
        prompt_label.setObjectName("muted")
        layout.addWidget(prompt_label)
        self._prompt_edit = QPlainTextEdit()
        self._prompt_edit.setPlaceholderText(
            "Optional. Add names, terms and abbreviations to improve recognition."
        )
        self._prompt_edit.setFixedHeight(72)
        layout.addWidget(self._prompt_edit)
        return group

    def _build_audio_group(self) -> QGroupBox:
        group = QGroupBox("Audio")
        form = QFormLayout(group)
        form.setHorizontalSpacing(18)
        form.setVerticalSpacing(8)

        mic_row = QWidget()
        mic_layout = QHBoxLayout(mic_row)
        mic_layout.setContentsMargins(0, 0, 0, 0)
        mic_layout.setSpacing(6)
        self._mic_combo = QComboBox()
        mic_layout.addWidget(self._mic_combo, 1)
        self._mic_refresh_button = QPushButton("Refresh")
        self._mic_refresh_button.setFixedWidth(80)
        self._mic_refresh_button.clicked.connect(self._refresh_mics)
        mic_layout.addWidget(self._mic_refresh_button)
        form.addRow("Microphone", mic_row)

        self._sounds_check = QCheckBox("Play a sound when recording starts and stops")
        form.addRow("", self._sounds_check)
        return group

    def _build_test_group(self) -> QGroupBox:
        group = QGroupBox("Test model and microphone")
        layout = QVBoxLayout(group)
        layout.setSpacing(8)

        hint = QLabel(
            "Press Start, say a short phrase, then press Stop to see the "
            "recognized text. The test uses the values selected above without "
            "saving them, so you can compare models freely."
        )
        hint.setWordWrap(True)
        hint.setObjectName("muted")
        layout.addWidget(hint)

        self._test_profile = QLabel("")
        self._test_profile.setObjectName("muted")
        self._test_profile.setWordWrap(True)
        layout.addWidget(self._test_profile)

        controls = QHBoxLayout()
        self._test_button = QPushButton("Start test")
        self._test_button.setMinimumWidth(150)
        self._test_button.clicked.connect(self._on_test_button)
        controls.addWidget(self._test_button)
        self._test_status = QLabel("Ready.")
        self._test_status.setObjectName("muted")
        self._test_status.setWordWrap(True)
        controls.addWidget(self._test_status, 1)
        layout.addLayout(controls)

        self._test_level = QProgressBar()
        self._test_level.setRange(0, 100)
        self._test_level.setTextVisible(False)
        self._test_level.setFixedHeight(10)
        self._test_level.setVisible(False)
        layout.addWidget(self._test_level)

        result_label = QLabel("Recognized text")
        result_label.setObjectName("muted")
        layout.addWidget(result_label)

        self._test_text = QPlainTextEdit()
        self._test_text.setReadOnly(True)
        self._test_text.setPlaceholderText("Your recognized text will appear here.")
        self._test_text.setFixedHeight(92)
        layout.addWidget(self._test_text)

        footer = QHBoxLayout()
        self._test_meta = QLabel("Not tested yet.")
        self._test_meta.setObjectName("muted")
        self._test_meta.setWordWrap(True)
        footer.addWidget(self._test_meta, 1)
        self._test_copy = QPushButton("Copy")
        self._test_copy.setFixedWidth(70)
        self._test_copy.setEnabled(False)
        self._test_copy.clicked.connect(self._copy_test_text)
        self._test_clear = QPushButton("Clear")
        self._test_clear.setFixedWidth(70)
        self._test_clear.setEnabled(False)
        self._test_clear.clicked.connect(self._clear_test_result)
        footer.addWidget(self._test_copy)
        footer.addWidget(self._test_clear)
        layout.addLayout(footer)

        self._model_combo.currentIndexChanged.connect(self._update_test_profile)
        self._device_combo.currentIndexChanged.connect(self._update_test_profile)
        self._mic_combo.currentIndexChanged.connect(self._update_test_profile)
        return group

    def _build_appearance_group(self) -> QGroupBox:
        group = QGroupBox("Appearance")
        form = QFormLayout(group)
        form.setHorizontalSpacing(18)
        self._overlay_combo = QComboBox()
        self._overlay_combo.addItem("Bottom center", "bottom-center")
        self._overlay_combo.addItem("Bottom left", "bottom-left")
        self._overlay_combo.addItem("Bottom right", "bottom-right")
        self._overlay_combo.addItem("Top center", "top-center")
        form.addRow("Overlay position", self._overlay_combo)
        return group

    def _build_system_group(self) -> QGroupBox:
        group = QGroupBox("System")
        layout = QVBoxLayout(group)
        self._autostart_check = QCheckBox("Start VoiceType with Windows")
        layout.addWidget(self._autostart_check)

        gpu_row = QHBoxLayout()
        self._cuda_status = QLabel("")
        self._cuda_status.setObjectName("muted")
        self._cuda_status.setWordWrap(True)
        self._cuda_button = QPushButton("Download CUDA pack")
        self._cuda_button.clicked.connect(self._on_cuda_button)
        gpu_row.addWidget(self._cuda_status, 1)
        gpu_row.addWidget(self._cuda_button)
        layout.addLayout(gpu_row)
        self._cuda_progress = QProgressBar()
        self._cuda_progress.setVisible(False)
        layout.addWidget(self._cuda_progress)
        return group

    def _build_about_group(self) -> QGroupBox:
        group = QGroupBox("About")
        layout = QVBoxLayout(group)
        row = QHBoxLayout()
        version = QLabel(f"VoiceType {__version__}")
        version.setObjectName("muted")
        row.addWidget(version, 1)
        self._update_button = QPushButton("Check for updates")
        self._update_button.clicked.connect(lambda: self._app.check_updates(manual=True))
        row.addWidget(self._update_button)
        layout.addLayout(row)
        self._update_status = QLabel("")
        self._update_status.setObjectName("muted")
        self._update_status.setWordWrap(True)
        layout.addWidget(self._update_status)
        return group

    def _update_test_profile(self) -> None:
        model_key = self._model_combo.currentData()
        if not model_key or model_key == "auto":
            model_name = "Auto (best available)"
        else:
            info = models.MODELS.get(model_key)
            model_name = info.display_name if info is not None else str(model_key)
            if not models.is_available(model_key):
                model_name += " (not downloaded)"
        device = {"auto": "Auto", "cuda": "CUDA", "cpu": "CPU"}.get(
            str(self._device_combo.currentData()), "Auto"
        )
        microphone = self._mic_combo.currentText() or "System default"
        self._test_profile.setText(
            f"Will test: {model_name} · {device} · {microphone}"
        )

    def _on_test_button(self) -> None:
        if self._tester.state == "recording":
            self._tester.stop()
            return
        if self._tester.state == "processing":
            return
        if self._app.is_busy:
            self._set_test_status(
                "Wait for the current recording or model load to finish.",
                error=True,
            )
            return
        model_key = self._model_combo.currentData()
        if model_key != "auto" and not models.is_available(model_key):
            self._set_test_status(
                "The selected model is not downloaded. Download it first.",
                error=True,
            )
            return
        self._test_text.clear()
        self._test_meta.setText("Working…")
        self._test_copy.setEnabled(False)
        self._test_clear.setEnabled(False)
        self._app.set_hotkey_capture(True)
        started = self._tester.start(
            str(model_key),
            str(self._device_combo.currentData()),
            str(self._language_combo.currentData()),
            self._prompt_edit.toPlainText().strip(),
            self._mic_combo.currentData() or None,
            list(self._app.config.hallucination_phrases),
        )
        if not started:
            self._app.set_hotkey_capture(False)

    def _on_test_state(self, state: str) -> None:
        active = state in {"recording", "processing"}
        self._apply_button.setEnabled(not active)
        for widget in self._test_controls:
            widget.setEnabled(not active)
        if state == "recording":
            self._test_button.setText("Stop and recognize")
            self._test_button.setEnabled(True)
            self._test_level.setVisible(True)
            self._app.set_hotkey_capture(True)
        elif state == "processing":
            self._test_button.setText("Recognizing…")
            self._test_button.setEnabled(False)
            self._test_level.setVisible(False)
        elif state == "done":
            self._test_button.setText("Test again")
            self._test_button.setEnabled(True)
            self._test_level.setVisible(False)
            self._app.set_hotkey_capture(False)
        elif state == "error":
            self._test_button.setText("Try again")
            self._test_button.setEnabled(True)
            self._test_level.setVisible(False)
            self._app.set_hotkey_capture(False)
        else:
            self._test_button.setText("Start test")
            self._test_button.setEnabled(True)
            self._test_level.setVisible(False)
            self._app.set_hotkey_capture(False)

    def _on_test_status(self, message: str) -> None:
        self._set_test_status(message, error=self._tester.state == "error")

    def _on_test_error(self, message: str) -> None:
        self._set_test_status(message, error=True)
        if not self._test_text.toPlainText():
            self._test_meta.setText("Not tested yet.")
        self._app.set_hotkey_capture(False)

    def _on_test_level(self, level: float) -> None:
        self._test_level.setValue(int(max(0.0, min(1.0, level)) * 100))

    def _on_test_elapsed(self, elapsed: float) -> None:
        if self._tester.state == "recording":
            self._set_test_status(f"Recording… {elapsed:.1f} s — speak now")

    def _on_test_result(self, text: str, meta: str) -> None:
        self._test_text.setPlainText(text)
        self._test_meta.setText(meta)
        self._test_meta.setToolTip(self._tester.selection_reason)
        self._test_copy.setEnabled(bool(text))
        self._test_clear.setEnabled(True)

    def _set_test_status(self, message: str, error: bool = False) -> None:
        self._test_status.setText(message)
        self._test_status.setObjectName("error" if error else "muted")
        self._test_status.style().unpolish(self._test_status)
        self._test_status.style().polish(self._test_status)

    def _copy_test_text(self) -> None:
        text = self._test_text.toPlainText()
        if not text:
            return
        QGuiApplication.clipboard().setText(text)
        self._set_test_status("Copied to the clipboard.")

    def _clear_test_result(self) -> None:
        if self._tester.state in {"recording", "processing"}:
            return
        self._test_text.clear()
        self._test_meta.setText("Not tested yet.")
        self._test_meta.setToolTip("")
        self._test_copy.setEnabled(False)
        self._test_clear.setEnabled(False)
        self._set_test_status("Ready.")

    def refresh(self) -> None:
        self._config = self._app.config
        self._cuda_downloading = self._app.cuda_download_active
        config = self._config
        if not self._hotkey_edit.is_capturing():
            try:
                display = format_hotkey(parse_hotkey(config.hotkey))
            except ValueError:
                display = config.hotkey
            self._hotkey_edit.setText(display)
        self._set_combo_data(self._mode_combo, config.hotkey_mode)
        self._set_combo_data(self._language_combo, config.language)
        self._max_record_spin.setValue(max(1, config.max_record_seconds // 60))
        self._keep_clipboard_check.setChecked(config.keep_text_in_clipboard)
        self._set_combo_data(self._device_combo, config.device)
        self._refresh_model_combo()
        self._prompt_edit.setPlainText(config.initial_prompt)
        self._idle_spin.setValue(config.idle_unload_minutes)
        self._refresh_mics()
        self._sounds_check.setChecked(config.sounds)
        self._set_combo_data(self._overlay_combo, config.overlay_position)
        self._autostart_check.setChecked(config.autostart)
        for key, row in self._model_rows.items():
            row.update_status(models.model_status(key))
        active_download = self._app.active_model_download
        if active_download is not None:
            row = self._model_rows.get(active_download)
            if row is not None:
                row.set_downloading(True)
        self._refresh_cuda()
        self._update_test_profile()

    def _set_combo_data(self, combo: QComboBox, value) -> None:
        index = combo.findData(value)
        if index >= 0:
            combo.setCurrentIndex(index)

    def _refresh_model_combo(self) -> None:
        self._model_combo.blockSignals(True)
        self._model_combo.clear()
        self._model_combo.addItem("Auto (recommended)", "auto")
        for key in models.MODEL_ORDER:
            info = models.MODELS[key]
            suffix = "" if models.is_available(key) else " (not downloaded)"
            self._model_combo.addItem(info.display_name + suffix, key)
        self._set_combo_data(self._model_combo, self._config.model)
        self._model_combo.blockSignals(False)
        self._update_test_profile()

    def _refresh_mics(self) -> None:
        current = self._config.microphone or ""
        self._mic_combo.blockSignals(True)
        self._mic_combo.clear()
        self._mic_combo.addItem("System default", "")
        for _index, name in Recorder.input_devices():
            self._mic_combo.addItem(name, name)
        self._set_combo_data(self._mic_combo, current)
        self._mic_combo.blockSignals(False)
        self._update_test_profile()

    def _refresh_cuda(self) -> None:
        gpus = hardware.detect_nvidia_gpus()
        if self._cuda_downloading:
            self._cuda_status.setText("Downloading the CUDA acceleration pack...")
            self._cuda_button.setText("Cancel download")
            self._cuda_button.setEnabled(True)
            self._cuda_progress.setVisible(True)
            return
        self._cuda_progress.setVisible(False)
        if not gpus:
            self._cuda_status.setText("No NVIDIA GPU detected. CPU mode will be used.")
            self._cuda_button.setVisible(False)
            return
        self._cuda_button.setVisible(True)
        if cuda.cuda_pack_installed():
            version = cuda.cuda_pack_version() or cuda.PACK_TAG
            self._cuda_status.setText(f"CUDA pack installed ({version}).")
            self._cuda_button.setVisible(False)
        elif hardware.cuda_available():
            self._cuda_status.setText(
                "CUDA libraries were found on this system. GPU acceleration is ready."
            )
            self._cuda_button.setVisible(False)
        else:
            gpu = gpus[0]
            self._cuda_status.setText(
                f"NVIDIA GPU detected: {gpu.name} ({gpu.free_mb} MB free). "
                f"Download the CUDA pack (~{cuda.CUDA_PACK_APPROX_MB} MB) to accelerate recognition."
            )
            self._cuda_button.setText("Download CUDA pack")
            self._cuda_button.setEnabled(True)

    def _on_cuda_button(self) -> None:
        if self._cuda_downloading:
            self._app.cancel_cuda_download()
        else:
            self._app.download_cuda_pack()

    def apply(self) -> None:
        if self._tester.state in {"recording", "processing"}:
            return
        if self._hotkey_edit.is_capturing():
            self._hotkey_edit.finish_capture()
        raw_hotkey = self._hotkey_edit.text()
        if not raw_hotkey or raw_hotkey == "Press keys...":
            QMessageBox.warning(self, "Settings", "Please set a hotkey.")
            return
        try:
            keys = validate_hotkey(raw_hotkey)
        except ValueError as exc:
            log.warning("Hotkey rejected: %r (%s)", raw_hotkey, exc)
            QMessageBox.warning(self, "Settings", str(exc))
            return
        log.info("Applying settings with hotkey %s", format_hotkey(keys))
        model_key = str(self._model_combo.currentData())
        if model_key != "auto" and not models.is_available(model_key):
            QMessageBox.warning(
                self,
                "Settings",
                "The selected model is not downloaded yet. Download it first or choose Auto.",
            )
            return
        # Free the test model before the main worker loads the new profile.
        self._tester.unload()
        values = {
            "hotkey": format_hotkey(keys),
            "hotkey_mode": self._mode_combo.currentData(),
            "language": self._language_combo.currentData(),
            "model": model_key,
            "device": self._device_combo.currentData(),
            "microphone": self._mic_combo.currentData() or None,
            "autostart": self._autostart_check.isChecked(),
            "sounds": self._sounds_check.isChecked(),
            "max_record_seconds": self._max_record_spin.value() * 60,
            "keep_text_in_clipboard": self._keep_clipboard_check.isChecked(),
            "idle_unload_minutes": self._idle_spin.value(),
            "initial_prompt": self._prompt_edit.toPlainText().strip(),
            "overlay_position": self._overlay_combo.currentData(),
        }
        if self._app.apply_settings(values):
            self.notify_saved()
        else:
            log.warning("Settings were not applied: %s", values)
            self._status_label.setText("Settings were not saved.")
            QTimer.singleShot(3000, lambda: self._status_label.setText(""))

    def notify_saved(self) -> None:
        self._status_label.setText("Settings saved.")
        QTimer.singleShot(3000, lambda: self._status_label.setText(""))

    def set_update_status(self, text: str) -> None:
        self._update_status.setText(text)

    def set_model_downloading(self, key: str, active: bool) -> None:
        row = self._model_rows.get(key)
        if row is not None:
            row.set_downloading(active)

    def set_cuda_downloading(self, active: bool) -> None:
        self._cuda_downloading = active
        self._refresh_cuda()

    def _on_model_progress(self, key: str, done: int, total: int) -> None:
        row = self._model_rows.get(key)
        if row is not None:
            row.set_progress(done, total)

    def _on_model_finished(self, key: str, ok: bool, message: str) -> None:
        row = self._model_rows.get(key)
        if row is not None:
            row.update_status(models.model_status(key))
        self._refresh_model_combo()
        if not ok and message:
            self._status_label.setText(message)

    def _on_cuda_progress(self, done: int, total: int) -> None:
        if total > 0:
            self._cuda_progress.setRange(0, 100)
            self._cuda_progress.setValue(min(100, int(done * 100 / total)))
            self._cuda_progress.setFormat(
                f"{done / (1024 * 1024):.0f} / {total / (1024 * 1024):.0f} MB"
            )
        else:
            self._cuda_progress.setRange(0, 0)
            self._cuda_progress.setFormat(f"{done / (1024 * 1024):.0f} MB")

    def _on_cuda_finished(self, ok: bool, message: str) -> None:
        self._cuda_downloading = False
        self._cuda_progress.setVisible(False)
        self._refresh_cuda()
        if message:
            self._status_label.setText(message)

    def showEvent(self, event) -> None:  # noqa: N802
        self.refresh()
        super().showEvent(event)

    def hideEvent(self, event) -> None:  # noqa: N802
        tester = getattr(self, "_tester", None)
        if tester is not None:
            tester.on_hide()
        super().hideEvent(event)

    def shutdown(self) -> None:
        tester = getattr(self, "_tester", None)
        if tester is not None:
            tester.shutdown()

    def closeEvent(self, event) -> None:  # noqa: N802
        if self._hotkey_edit._capturing:
            self._hotkey_edit.cancel_capture()
        event.ignore()
        self.hide()
