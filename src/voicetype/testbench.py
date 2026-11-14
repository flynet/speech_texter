"""Short recognition test used by the Settings window.

The test deliberately runs on its own recorder and transcription worker so the
user can try the model, device, and microphone that are currently selected in
Settings without saving them and without disturbing normal dictation.
"""

from __future__ import annotations

import logging
import time

import numpy as np
from PySide6.QtCore import QObject, QThread, QTimer, Signal

from . import hardware, models, translation
from .recorder import Recorder
from .transcriber import TranscriptionWorker

log = logging.getLogger(__name__)

TEST_MAX_SECONDS = 30
MIN_TEST_SECONDS = 0.5


class RecognitionTester(QObject):
    """Record a sample, transcribe it with an unsaved profile, report the text."""

    configure_requested = Signal(str, str, str, str, object, bool, bool, str)
    transcribe_requested = Signal(object)
    unload_requested = Signal()
    quit_requested = Signal()

    state_changed = Signal(str)
    status_changed = Signal(str)
    level_changed = Signal(float)
    elapsed_changed = Signal(float)
    result_ready = Signal(str, str)
    error = Signal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._state = "idle"
        self._selection: hardware.Selection | None = None
        self._model_key: str | None = None
        self._device = "cpu"
        self._language = "ru"
        self._translate = False
        self._target_language = "en"
        self._prompt = ""
        self._blacklist: list[str] = []
        self._microphone = "System default"
        self._model_ready = False
        self._pending_audio: np.ndarray | None = None
        self._run_started = 0.0
        self._unload_when_done = False
        self._unload_pending = False
        self._closed = False

        self._worker = TranscriptionWorker()
        self._thread = QThread(self)
        self._worker.moveToThread(self._thread)
        self._thread.start()

        self.configure_requested.connect(self._worker.configure)
        self.transcribe_requested.connect(self._worker.transcribe)
        self.unload_requested.connect(self._worker.unload)
        self.quit_requested.connect(self._worker.quit_thread)
        self._worker.model_loading.connect(self._on_model_loading)
        self._worker.model_ready.connect(self._on_model_ready)
        self._worker.model_error.connect(self._on_model_error)
        self._worker.transcription_ready.connect(self._on_transcription_ready)
        self._worker.transcription_error.connect(self._on_transcription_error)
        self._worker.fallback.connect(self._on_fallback)

        self._recorder = Recorder(self)
        self._recorder.rms_changed.connect(self._on_level)
        self._recorder.limit_reached.connect(self._on_limit_reached)
        self._recorder.error.connect(self._on_recorder_error)

        self._timer = QTimer(self)
        self._timer.setInterval(100)
        self._timer.timeout.connect(self._on_tick)

    @property
    def state(self) -> str:
        return self._state

    @property
    def selection_reason(self) -> str:
        return self._selection.reason if self._selection is not None else ""

    def start(
        self,
        model_pref: str,
        device_pref: str,
        language: str,
        prompt: str,
        microphone: str | None,
        blacklist: list[str],
        translate: bool = False,
        target_language: str = "en",
    ) -> bool:
        if self._closed or self._state in {"recording", "processing"}:
            return False

        if model_pref != "auto" and not models.is_available(model_pref):
            self._report_error("The selected model is not downloaded. Download it first.")
            return False

        if microphone:
            names = [name for _index, name in Recorder.input_devices()]
            if microphone not in names:
                self._report_error(
                    "The selected microphone is no longer connected. Press Refresh."
                )
                return False

        try:
            selection = hardware.resolve_selection(model_pref, device_pref, bool(translate))
        except Exception as exc:
            log.exception("Could not resolve the test profile")
            self._report_error(f"Could not choose a model or device: {exc}")
            return False

        if not self._recorder.start(microphone, TEST_MAX_SECONDS):
            self._report_error(
                "Could not start the test microphone. Check the selected device."
            )
            return False

        self._selection = selection
        self._model_key = selection.model
        self._device = selection.device
        self._language = language
        self._translate = bool(translate)
        self._target_language = target_language
        self._prompt = prompt
        self._blacklist = [str(item) for item in blacklist]
        self._microphone = microphone or "System default"
        self._model_ready = False
        self._pending_audio = None
        self._unload_when_done = False
        self._unload_pending = False
        self._run_started = time.monotonic()

        self._set_state("recording")
        self._set_status("Recording… speak now")
        self.elapsed_changed.emit(0.0)
        self._timer.start()
        self.configure_requested.emit(
            self._model_key,
            self._device,
            self._language,
            self._prompt,
            self._blacklist,
            device_pref == "cuda",
            self._translate,
            self._target_language,
        )
        return True

    def stop(self) -> None:
        if self._state != "recording":
            return
        self._timer.stop()
        audio, duration = self._recorder.stop()
        self._set_state("processing")
        if duration < MIN_TEST_SECONDS:
            self._pending_audio = None
            self._report_error(
                "The test recording was too short. Speak for at least a second."
            )
            return
        self._pending_audio = audio
        if self._model_ready:
            self._transcribe_pending()
        else:
            self._set_status("Loading the model…")

    def unload(self) -> None:
        if self._closed:
            return
        self._unload_pending = True
        self._model_ready = False
        self.unload_requested.emit()

    def on_hide(self) -> None:
        """Release the test model when Settings is hidden."""
        if self._closed:
            return
        if self._state == "recording":
            self._timer.stop()
            self._recorder.stop()
            self._pending_audio = None
            self._set_state("idle")
            self._set_status("Ready.")
        if self._state == "processing":
            # The worker is already transcribing; unload as soon as it finishes.
            self._unload_when_done = True
            return
        self.unload()

    def shutdown(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._timer.stop()
        if self._recorder.is_recording:
            self._recorder.stop()
        self._pending_audio = None
        self.unload_requested.emit()
        self.quit_requested.emit()
        if not self._thread.wait(3000):
            log.warning("The recognition test thread did not stop in time")
            self._thread.terminate()
            self._thread.wait(1000)

    # ---------------------------------------------------------------- internals

    def _set_state(self, state: str) -> None:
        self._state = state
        self.state_changed.emit(state)

    def _set_status(self, message: str) -> None:
        self.status_changed.emit(message)

    def _report_error(self, message: str) -> None:
        log.warning("Recognition test: %s", message)
        self._set_state("error")
        self._set_status(message)
        self.error.emit(message)

    def _on_tick(self) -> None:
        if self._state != "recording":
            return
        elapsed = time.monotonic() - self._run_started
        self.elapsed_changed.emit(elapsed)
        self._set_status(f"Recording… {elapsed:.1f} s — speak now")

    def _on_model_loading(self, model_key: str, device: str) -> None:
        if self._state == "recording":
            self._set_status("Recording… loading the model in the background")
        elif self._state == "processing":
            self._set_status("Loading the model…")

    def _on_model_ready(self, model_key: str, device: str) -> None:
        if self._unload_pending:
            return
        self._model_ready = True
        self._device = device
        if self._state == "processing" and self._pending_audio is not None:
            self._transcribe_pending()

    def _on_model_error(self, message: str) -> None:
        self._model_ready = False
        if self._state == "recording":
            self._timer.stop()
            self._recorder.stop()
        self._pending_audio = None
        self._report_error(f"Could not load the test model: {message}")

    def _transcribe_pending(self) -> None:
        audio = self._pending_audio
        self._pending_audio = None
        if audio is None or audio.size == 0:
            self._report_error("No audio was recorded. Try again.")
            return
        self._set_status("Recognizing…")
        self.transcribe_requested.emit(audio)

    def _on_transcription_ready(
        self, text: str, duration: float, elapsed: float, rtf: float
    ) -> None:
        if self._closed:
            return
        self._timer.stop()
        if text:
            self._set_status("Test complete.")
        else:
            self._set_status(
                "No speech was recognized. Try again or check the microphone."
            )
        self.result_ready.emit(text, self._result_meta(elapsed, rtf))
        self._set_state("done")
        if self._unload_when_done:
            self._unload_when_done = False
            self.unload()

    def _on_transcription_error(self, message: str) -> None:
        self._pending_audio = None
        self._report_error(f"Recognition failed: {message}")
        if self._unload_when_done:
            self._unload_when_done = False
            self.unload()

    def _on_fallback(self, reason: str) -> None:
        log.warning("Test CUDA fallback: %s", reason)
        self._set_status("CUDA is unavailable; the test is using the CPU.")

    def _on_limit_reached(self) -> None:
        if self._state == "recording":
            self._set_status("Maximum test length reached; recognizing…")
            self.stop()

    def _on_recorder_error(self, message: str) -> None:
        if self._state != "recording":
            return
        self._timer.stop()
        self._recorder.stop()
        self._pending_audio = None
        self._report_error(message)

    def _on_level(self, rms: float) -> None:
        if self._state == "recording":
            self.level_changed.emit(min(1.0, max(0.0, rms * 10.0)))

    def _result_meta(self, elapsed: float, rtf: float) -> str:
        display = self._model_key or (
            self._selection.model if self._selection is not None else ""
        )
        info = models.MODELS.get(display)
        if info is not None:
            display = info.display_name
        meta = (
            f"{display} · {self._device.upper()} · {self._microphone} · "
            f"{elapsed:.1f} s · RTF {rtf:.2f}"
        )
        if self._translate:
            info = translation.target_info(self._target_language)
            name = info.display_name if info is not None else self._target_language
            meta += f" · translated to {name}"
        return meta
