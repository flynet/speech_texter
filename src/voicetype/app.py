from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone

from PySide6.QtCore import QObject, QThread, QTimer, Qt, Signal
from PySide6.QtWidgets import QApplication, QMessageBox, QProgressDialog

from . import __version__, autostart, cuda, hardware, models
from .config import AppConfig, assets_dir
from .hotkeys import HotkeyListener
from .inserter import Inserter
from .recorder import Recorder
from .transcriber import TranscriptionWorker
from .ui import theme
from .ui.overlay import OverlayWindow
from .ui.settings import SettingsWindow
from .ui.tray import TrayIcon
from .updater import UpdateChecker, UpdateInfo

log = logging.getLogger(__name__)


class VoiceTypeApplication(QObject):
    configure_worker = Signal(str, str, str, str, object)
    transcribe_requested = Signal(object)
    unload_requested = Signal()

    model_download_progress = Signal(str, int, int)
    model_download_finished = Signal(str, bool, str)
    cuda_progress = Signal(int, int)
    cuda_finished = Signal(bool, str)

    def __init__(self, app: QApplication) -> None:
        super().__init__()
        self.app = app
        self.config = AppConfig.load()
        try:
            self.config.autostart = autostart.is_enabled()
            self.config.save()
        except Exception:
            log.debug("Could not read the autostart state", exc_info=True)

        self._state = "idle"
        self._recording = False
        self._model_loaded = False
        self._last_use = time.monotonic()
        self._selection: hardware.Selection | None = None
        self._active_device: str | None = None
        self._settings: SettingsWindow | None = None
        self._pending_reconfigure = False

        self._model_download_cancel: threading.Event | None = None
        self._model_download_key: str | None = None
        self._cuda_download_cancel: threading.Event | None = None
        self._update_cancel: threading.Event | None = None
        self._update_info: UpdateInfo | None = None
        self._manual_update_check = False
        self._progress_dialog: QProgressDialog | None = None

        self._tray = TrayIcon()
        self._overlay = OverlayWindow()
        self._recorder = Recorder(self)
        self._inserter = Inserter(self)
        try:
            self._hotkeys = HotkeyListener(self.config.hotkey, self)
        except ValueError:
            log.warning("Stored hotkey is invalid, resetting to the default")
            self.config.hotkey = "ctrl+alt+space"
            self.config.save()
            self._hotkeys = HotkeyListener(self.config.hotkey, self)
        self._worker = TranscriptionWorker()
        self._worker_thread = QThread(self)
        self._worker.moveToThread(self._worker_thread)
        self._updater = UpdateChecker(self)

        self._idle_timer = QTimer(self)
        self._idle_timer.setInterval(30_000)
        self._idle_timer.timeout.connect(self._on_idle_tick)

        theme.apply_theme(self.app)
        self._overlay.set_theme(theme.is_dark_theme())
        self._overlay.set_position(self.config.overlay_position)
        self._connect_signals()
        self._tray.set_language(self.config.language)
        self._tray.set_autostart(self.config.autostart)

    def _connect_signals(self) -> None:
        self._hotkeys.pressed.connect(self._on_hotkey_pressed)
        self._hotkeys.released.connect(self._on_hotkey_released)
        self._hotkeys.error.connect(self._set_error)

        self._recorder.rms_changed.connect(self._overlay.set_level)
        self._recorder.limit_reached.connect(self._on_limit_reached)
        self._recorder.error.connect(self._on_recorder_error)

        self.configure_worker.connect(self._worker.configure)
        self.transcribe_requested.connect(self._worker.transcribe)
        self.unload_requested.connect(self._worker.unload)

        self._worker.model_loading.connect(self._on_model_loading)
        self._worker.model_ready.connect(self._on_model_ready)
        self._worker.model_unloaded.connect(self._on_model_unloaded)
        self._worker.model_error.connect(self._on_model_error)
        self._worker.transcription_ready.connect(self._on_transcription_ready)
        self._worker.transcription_error.connect(self._on_transcription_error)
        self._worker.fallback.connect(self._on_fallback)

        self._inserter.finished.connect(self._on_insert_finished)
        self._inserter.error.connect(self._on_insert_error)

        self._tray.settings_requested.connect(self.open_settings)
        self._tray.language_changed.connect(self._on_language_changed)
        self._tray.check_updates_requested.connect(lambda: self.check_updates(manual=True))
        self._tray.autostart_toggled.connect(self._on_tray_autostart)
        self._tray.quit_requested.connect(self.app.quit)

        self.model_download_finished.connect(self._on_model_download_finished)
        self.cuda_finished.connect(self._on_cuda_finished)

        self._updater.check_finished.connect(self._on_update_check_finished)
        self._updater.check_failed.connect(self._on_update_check_failed)
        self._updater.download_progress.connect(self._on_update_progress)
        self._updater.download_finished.connect(self._on_update_downloaded)
        self._updater.download_failed.connect(self._on_update_download_failed)

        self.app.aboutToQuit.connect(self.shutdown)

    def start(self) -> None:
        self._tray.show()
        self._hotkeys.start()
        self._worker_thread.start()
        self._idle_timer.start()
        QTimer.singleShot(0, self._configure_from_config)
        QTimer.singleShot(2000, self._maybe_prompt_cuda)
        QTimer.singleShot(10000, self._maybe_check_updates)
        self._tray.set_status_text("Starting...")

    # ------------------------------------------------------------------ model

    def _configure_from_config(self) -> None:
        try:
            selection = hardware.resolve_selection(self.config.model, self.config.device)
        except hardware.NoModelsAvailable as exc:
            log.error("%s", exc)
            self._state = "error"
            self._tray.set_state("error")
            self._tray.set_status_text(str(exc))
            self._tray.notify("VoiceType", str(exc))
            if self._settings is not None:
                self._settings.refresh()
            QTimer.singleShot(0, self.open_settings)
            return
        self._selection = selection
        log.info("Selected profile: %s", selection.reason)
        self._state = "loading"
        self._tray.set_state("loading")
        self._tray.set_status_text(
            f"{models.MODELS[selection.model].display_name} · "
            f"{selection.device.upper()} · loading"
        )
        self.configure_worker.emit(
            selection.model,
            selection.device,
            self.config.language,
            self.config.initial_prompt,
            self.config.hallucination_phrases,
        )

    def _on_model_loading(self, model_key: str, device: str) -> None:
        self._model_loaded = False
        if self._state in {"recording", "processing"}:
            self._tray.set_state(self._state)
        else:
            self._state = "loading"
            self._tray.set_state("loading")
        display = models.MODELS.get(model_key)
        name = display.display_name if display else model_key
        self._tray.set_status_text(f"{name} · {device.upper()} · loading")

    def _on_model_ready(self, model_key: str, device: str) -> None:
        self._model_loaded = True
        self._active_device = device
        self._last_use = time.monotonic()
        if self._state == "loading":
            self._state = "idle"
            self._tray.set_state("idle")
        elif self._state in {"recording", "processing"}:
            self._tray.set_state(self._state)
        display = models.MODELS.get(model_key)
        name = display.display_name if display else model_key
        if self._state == "idle":
            self._tray.set_status_text(f"{name} · {device.upper()}")
        log.info("Model %s is ready on %s", model_key, device)
        if self.config.model != "auto" and self.config.model != model_key:
            self._tray.notify(
                "VoiceType",
                f"Requested model {self.config.model} is not available; using {model_key}.",
            )

    def _on_model_unloaded(self) -> None:
        self._model_loaded = False
        if self._state == "idle":
            self._tray.set_status_text("Model unloaded (idle)")

    def _on_model_error(self, message: str) -> None:
        self._model_loaded = False
        self._set_error(message)

    def _on_fallback(self, reason: str) -> None:
        log.warning("GPU fallback: %s", reason)
        self._tray.notify("GPU acceleration unavailable", reason)

    # --------------------------------------------------------------- recording

    def _on_hotkey_pressed(self) -> None:
        log.info("Hotkey pressed")
        if self._state in {"loading", "processing", "error"}:
            return
        if self._state == "recording":
            if self.config.hotkey_mode == "toggle":
                self._stop_recording()
            return
        if self._state != "idle":
            return
        self._start_recording()

    def _on_hotkey_released(self) -> None:
        log.info("Hotkey released")
        if self.config.hotkey_mode == "hold" and self._state == "recording":
            self._stop_recording()

    def _start_recording(self) -> None:
        if not self._recorder.start(self.config.microphone, self.config.max_record_seconds):
            return
        self._recording = True
        self._state = "recording"
        self._tray.set_state("recording")
        self._tray.set_status_text("Listening...")
        self._overlay.show_state("recording")
        self._play_sound("start")

    def _stop_recording(self) -> None:
        if not self._recording:
            return
        audio, duration = self._recorder.stop()
        self._recording = False
        self._play_sound("stop")
        if duration < self.config.min_record_seconds:
            log.info("Ignoring a %.3f s recording", duration)
            self._finish_processing()
            return
        self._state = "processing"
        self._tray.set_state("processing")
        self._tray.set_status_text("Processing...")
        self._overlay.show_state("processing")
        self._last_use = time.monotonic()
        self.transcribe_requested.emit(audio)

    def _on_limit_reached(self) -> None:
        if self._state == "recording":
            self._tray.notify("VoiceType", "Maximum recording length reached.")
            self._stop_recording()

    def _on_recorder_error(self, message: str) -> None:
        if self._recording:
            self._recorder.stop()
            self._recording = False
        self._set_error(message)

    # ------------------------------------------------------------ transcription

    def _on_transcription_ready(
        self, text: str, duration: float, elapsed: float, rtf: float
    ) -> None:
        if not text:
            log.info("Empty transcription; nothing to insert")
            self._finish_processing()
            return
        self._inserter.insert(text, keep_text=self.config.keep_text_in_clipboard)

    def _on_transcription_error(self, message: str) -> None:
        self._set_error(message)

    def _on_insert_finished(self) -> None:
        self._finish_processing()

    def _on_insert_error(self, message: str) -> None:
        self._set_error(message)

    def _device_label(self) -> str:
        if self._active_device:
            return self._active_device.upper()
        if self._selection is not None:
            return self._selection.device.upper()
        return ""

    def _finish_processing(self) -> None:
        if self._recording:
            return
        self._state = "idle"
        self._overlay.hide_overlay()
        self._tray.set_state("idle")
        if self._selection is not None and self._model_loaded:
            name = models.MODELS[self._selection.model].display_name
            self._tray.set_status_text(f"{name} · {self._device_label()}")
        else:
            self._tray.set_status_text("")
        self._last_use = time.monotonic()
        if self._pending_reconfigure:
            self._pending_reconfigure = False
            QTimer.singleShot(0, self._configure_from_config)

    def _set_error(self, message: str, notify: bool = True) -> None:
        log.error("%s", message)
        self._state = "error"
        self._tray.set_state("error")
        self._tray.set_status_text(message)
        self._overlay.show_state("error", message)
        if notify:
            self._tray.notify("VoiceType", message)
        QTimer.singleShot(3500, self._recover_from_error)

    def _recover_from_error(self) -> None:
        if self._state != "error":
            return
        self._state = "idle"
        self._overlay.hide_overlay()
        self._tray.set_state("idle")
        if self._selection is not None:
            name = models.MODELS[self._selection.model].display_name
            self._tray.set_status_text(f"{name} · {self._device_label()}")
        else:
            self._tray.set_status_text("")
        if self._pending_reconfigure:
            self._pending_reconfigure = False
            QTimer.singleShot(0, self._configure_from_config)

    # ------------------------------------------------------------------ config

    def apply_settings(self, values: dict) -> bool:
        old = self.config.to_dict()
        new = dict(old)
        for key, value in values.items():
            if key in new:
                new[key] = value
        candidate = AppConfig.from_dict(new)
        if candidate.autostart != self.config.autostart:
            if not autostart.set_enabled(candidate.autostart):
                QMessageBox.warning(
                    self._settings,
                    "Settings",
                    "Could not update the Windows autostart setting.",
                )
                return False
        candidate.save()
        self.config = candidate
        self._tray.set_language(self.config.language)
        self._tray.set_autostart(self.config.autostart)
        self._overlay.set_position(self.config.overlay_position)
        if self.config.hotkey != old["hotkey"]:
            self._hotkeys.set_combo(self.config.hotkey)

        reconfigure_keys = {
            "model",
            "device",
            "language",
            "initial_prompt",
            "hallucination_phrases",
        }
        if any(old.get(key) != new.get(key) for key in reconfigure_keys):
            if self._state in {"recording", "processing"}:
                self._pending_reconfigure = True
            else:
                self._configure_from_config()
        if self._settings is not None:
            self._settings.refresh()
        return True

    def set_hotkey_capture(self, active: bool) -> None:
        if active and self._state == "recording":
            self._stop_recording()
        self._hotkeys.set_paused(active)

    def _on_language_changed(self, language: str) -> None:
        if language not in {"ru", "en", "auto"}:
            return
        self.config.language = language
        self.config.save()
        self._tray.set_language(language)
        if self._settings is not None:
            self._settings.refresh()
        if self._selection is not None:
            self.configure_worker.emit(
                self._selection.model,
                self._selection.device,
                self.config.language,
                self.config.initial_prompt,
                self.config.hallucination_phrases,
            )

    def _on_tray_autostart(self, enabled: bool) -> None:
        if not autostart.set_enabled(enabled):
            self._tray.set_autostart(not enabled)
            self._tray.notify("VoiceType", "Could not update the autostart setting.")
            return
        self.config.autostart = enabled
        self.config.save()
        if self._settings is not None:
            self._settings.refresh()

    # ----------------------------------------------------------------- offline

    def _on_idle_tick(self) -> None:
        minutes = self.config.idle_unload_minutes
        if minutes <= 0 or not self._model_loaded or self._state != "idle":
            return
        if time.monotonic() - self._last_use < minutes * 60:
            return
        log.info("Unloading the model after %s minutes of inactivity", minutes)
        self._model_loaded = False
        self.unload_requested.emit()
        self._tray.set_status_text("Model unloaded (idle)")

    # ---------------------------------------------------------------- downloads

    @property
    def active_model_download(self) -> str | None:
        if self._model_download_cancel is None:
            return None
        return self._model_download_key

    @property
    def cuda_download_active(self) -> bool:
        return self._cuda_download_cancel is not None

    def request_model_download(self, key: str) -> None:
        if key not in models.MODELS or self._model_download_cancel is not None:
            return
        cancel = threading.Event()
        self._model_download_cancel = cancel
        self._model_download_key = key
        if self._settings is not None:
            self._settings.set_model_downloading(key, True)

        def worker() -> None:
            try:
                models.download_model(
                    key,
                    progress=lambda done, total: self.model_download_progress.emit(
                        key, done, total
                    ),
                    cancel=cancel,
                )
            except models.DownloadCancelled:
                self.model_download_finished.emit(key, False, "Download cancelled.")
            except Exception as exc:
                log.exception("Model download failed")
                self.model_download_finished.emit(key, False, str(exc))
            else:
                self.model_download_finished.emit(key, True, "")

        threading.Thread(
            target=worker, daemon=True, name=f"VoiceTypeModelDownload-{key}"
        ).start()

    def cancel_model_download(self, key: str | None = None) -> None:
        if self._model_download_cancel is not None:
            self._model_download_cancel.set()

    def _on_model_download_finished(self, key: str, ok: bool, message: str) -> None:
        self._model_download_cancel = None
        self._model_download_key = None
        if self._settings is not None:
            self._settings.set_model_downloading(key, False)
            self._settings.refresh()
        if ok:
            self._tray.notify(
                "Model downloaded",
                f"{models.MODELS[key].display_name} is ready to use.",
            )
            if self.config.model == "auto" or self.config.model == key:
                if self._state in {"recording", "processing"}:
                    self._pending_reconfigure = True
                else:
                    self._configure_from_config()
        elif message:
            self._tray.notify("Model download failed", message)

    def download_cuda_pack(self) -> None:
        if self._cuda_download_cancel is not None:
            return
        cancel = threading.Event()
        self._cuda_download_cancel = cancel
        if self._settings is not None:
            self._settings.set_cuda_downloading(True)
        else:
            self._tray.set_status_text("Downloading CUDA pack...")

        def worker() -> None:
            try:
                cuda.download_and_install_pack(
                    progress=lambda done, total: self.cuda_progress.emit(done, total),
                    cancel=cancel,
                )
            except cuda.CudaDownloadCancelled:
                self.cuda_finished.emit(False, "CUDA pack download cancelled.")
            except Exception as exc:
                log.exception("CUDA pack download failed")
                self.cuda_finished.emit(False, str(exc))
            else:
                self.cuda_finished.emit(
                    True, "CUDA pack installed. Restart VoiceType to use the GPU."
                )

        threading.Thread(
            target=worker, daemon=True, name="VoiceTypeCudaDownload"
        ).start()

    def cancel_cuda_download(self) -> None:
        if self._cuda_download_cancel is not None:
            self._cuda_download_cancel.set()

    def _on_cuda_finished(self, ok: bool, message: str) -> None:
        self._cuda_download_cancel = None
        hardware.reset_cuda_cache()
        if self._settings is not None:
            self._settings.set_cuda_downloading(False)
        if ok:
            self._tray.notify("CUDA acceleration", message)
        elif message:
            self._tray.notify("CUDA acceleration", message)

    def _maybe_prompt_cuda(self) -> None:
        if self.config.cuda_prompt_shown:
            return
        if not hardware.detect_nvidia_gpus():
            return
        if cuda.cuda_pack_installed() or hardware.cuda_available():
            return
        self.config.cuda_prompt_shown = True
        self.config.save()
        message = QMessageBox()
        message.setWindowTitle("CUDA acceleration")
        message.setIcon(QMessageBox.Question)
        message.setText(
            "An NVIDIA GPU was detected. Download the CUDA acceleration pack "
            f"(~{cuda.CUDA_PACK_APPROX_MB} MB) to make recognition faster?"
        )
        message.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        message.setDefaultButton(QMessageBox.Yes)
        message.setWindowFlag(Qt.WindowStaysOnTopHint, True)
        if message.exec() == QMessageBox.Yes:
            self.download_cuda_pack()

    # ----------------------------------------------------------------- updates

    def check_updates(self, manual: bool = True) -> None:
        self._manual_update_check = manual
        if manual and self._settings is not None:
            self._settings.set_update_status("Checking for updates...")
        self._updater.check_async(__version__)

    def _maybe_check_updates(self) -> None:
        last = self.config.last_update_check
        if last:
            try:
                checked = datetime.fromisoformat(last)
                if checked.tzinfo is None:
                    checked = checked.replace(tzinfo=timezone.utc)
                if (datetime.now(timezone.utc) - checked).total_seconds() < 24 * 3600:
                    return
            except ValueError:
                pass
        self.check_updates(manual=False)

    def _on_update_check_finished(self, info: UpdateInfo | None) -> None:
        self.config.last_update_check = datetime.now(timezone.utc).isoformat()
        self.config.save()
        manual = self._manual_update_check
        if info is None:
            if manual:
                if self._settings is not None:
                    self._settings.set_update_status("You have the latest version.")
                self._tray.notify("VoiceType", "You have the latest version.")
            return
        self._update_info = info
        if manual:
            if self._settings is not None:
                self._settings.set_update_status(f"Version {info.version} is available.")
            text = f"Version {info.version} is available. Download and install it now?"
            if info.notes:
                text += "\n\n" + info.notes[:1200]
            answer = QMessageBox.question(
                self._settings,
                "VoiceType update",
                text,
                QMessageBox.Yes | QMessageBox.No,
            )
            if answer == QMessageBox.Yes:
                self._start_update_download(info)
        else:
            self._tray.notify(
                "Update available",
                f"Version {info.version} is available. Open Settings to install it.",
            )

    def _on_update_check_failed(self, message: str) -> None:
        log.warning("Update check failed: %s", message)
        if self._manual_update_check:
            if self._settings is not None:
                self._settings.set_update_status(f"Update check failed: {message}")
            self._tray.notify("Update check failed", message)

    def _start_update_download(self, info: UpdateInfo) -> None:
        cancel = threading.Event()
        self._update_cancel = cancel
        dialog = QProgressDialog(
            "Downloading the VoiceType update...",
            "Cancel",
            0,
            100,
            self._settings,
        )
        dialog.setWindowTitle("VoiceType update")
        dialog.setWindowModality(Qt.NonModal)
        dialog.setMinimumDuration(0)
        dialog.setAutoClose(False)
        dialog.canceled.connect(cancel.set)
        dialog.show()
        self._progress_dialog = dialog
        self._updater.download_async(info, cancel)

    def _on_update_progress(self, done: int, total: int) -> None:
        dialog = self._progress_dialog
        if dialog is None:
            return
        if total > 0:
            dialog.setRange(0, 100)
            dialog.setValue(min(100, int(done * 100 / total)))
            dialog.setLabelText(
                f"Downloading the VoiceType update... "
                f"{done / (1024 * 1024):.0f} / {total / (1024 * 1024):.0f} MB"
            )
        else:
            dialog.setRange(0, 0)
            dialog.setLabelText(
                f"Downloading the VoiceType update... {done / (1024 * 1024):.0f} MB"
            )

    def _on_update_downloaded(self, path: str) -> None:
        if self._progress_dialog is not None:
            self._progress_dialog.close()
            self._progress_dialog = None
        self._update_cancel = None
        if self._settings is not None:
            self._settings.set_update_status("Update downloaded and verified.")
        answer = QMessageBox.question(
            self._settings,
            "VoiceType update",
            "The update has been downloaded and verified. Install it now?\n\n"
            "VoiceType will close and restart.",
            QMessageBox.Yes | QMessageBox.No,
        )
        if answer == QMessageBox.Yes:
            UpdateChecker.install(path)

    def _on_update_download_failed(self, message: str) -> None:
        if self._progress_dialog is not None:
            self._progress_dialog.close()
            self._progress_dialog = None
        self._update_cancel = None
        if self._settings is not None:
            self._settings.set_update_status(message)
        self._tray.notify("Update failed", message)

    # --------------------------------------------------------------------- ui

    def open_settings(self) -> None:
        if self._settings is None:
            self._settings = SettingsWindow(self)
        else:
            self._settings.refresh()
        self._settings.show()
        self._settings.raise_()
        self._settings.activateWindow()

    # ------------------------------------------------------------------- misc

    def _play_sound(self, name: str) -> None:
        if not self.config.sounds:
            return
        path = assets_dir() / "sounds" / f"{name}.wav"
        if not path.is_file():
            return
        try:
            import winsound

            winsound.PlaySound(
                str(path),
                winsound.SND_FILENAME | winsound.SND_ASYNC,
            )
        except Exception:
            log.debug("Could not play the notification sound", exc_info=True)

    def shutdown(self) -> None:
        log.info("Shutting down VoiceType")
        self._idle_timer.stop()
        self._hotkeys.stop()
        if self._recording:
            self._recorder.stop()
        if self._model_download_cancel is not None:
            self._model_download_cancel.set()
        if self._cuda_download_cancel is not None:
            self._cuda_download_cancel.set()
        if self._update_cancel is not None:
            self._update_cancel.set()
        self._worker_thread.quit()
        if not self._worker_thread.wait(2000):
            log.warning("The transcription thread did not stop in time; terminating it")
            self._worker_thread.terminate()
            self._worker_thread.wait(1000)
        if self._settings is not None:
            self._settings.hide()
        self._tray.hide()
