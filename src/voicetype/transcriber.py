from __future__ import annotations

import gc
import logging
import time

import numpy as np
from PySide6.QtCore import QObject, Signal, Slot

from . import hardware
from . import models
from .config import AppConfig

log = logging.getLogger(__name__)


def clean_text(text: str, blacklist: list[str]) -> str:
    text = " ".join(text.split()).strip()
    if not text:
        return ""
    if not any(char.isalnum() for char in text):
        return ""

    def normalized(value: str) -> str:
        return value.strip().strip(" .,!?:;…\"'«»").lower()

    candidate = normalized(text)
    for phrase in blacklist:
        if candidate == normalized(phrase):
            log.info("Dropped a known Whisper hallucination: %r", text)
            return ""
    return text


class TranscriptionWorker(QObject):
    model_loading = Signal(str, str)
    model_ready = Signal(str, str)
    model_unloaded = Signal()
    model_error = Signal(str)
    transcription_ready = Signal(str, float, float, float)
    transcription_error = Signal(str)
    fallback = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self._model = None
        self._model_key: str | None = None
        self._device: str | None = None
        self._configured_model: str | None = None
        self._configured_device: str = "cpu"
        self._language: str = "ru"
        self._prompt: str = ""
        self._blacklist: list[str] = []
        self._cuda_failure: str | None = None

    @Slot(str, str, str, str, object)
    def configure(
        self,
        model_key: str,
        device: str,
        language: str,
        prompt: str,
        blacklist: object,
    ) -> None:
        self._language = language
        self._prompt = prompt
        if isinstance(blacklist, list):
            self._blacklist = [str(item) for item in blacklist]
        self._configured_model = model_key
        self._configured_device = device
        if (
            self._model is not None
            and self._model_key == model_key
            and self._device == self._effective_device(device)
        ):
            # Language, prompt and blacklist are already updated above. Emit
            # model_ready so the application can leave the "loading" state.
            self.model_ready.emit(model_key, self._device)
            return
        if self._model is not None:
            self.unload()
        try:
            self._load(model_key, device)
        except Exception as exc:
            log.exception("Could not load the speech model")
            self.model_error.emit(str(exc))

    def _effective_device(self, requested: str) -> str:
        if requested == "cuda" and self._cuda_failure:
            return "cpu"
        return requested

    def _load(self, model_key: str, requested_device: str) -> None:
        path = models.model_path(model_key)
        if path is None:
            raise RuntimeError(f"Model {model_key} is not available locally")
        device = self._effective_device(requested_device)
        self.model_loading.emit(model_key, device)
        from faster_whisper import WhisperModel

        compute_type = hardware.compute_type(device)
        log.info(
            "Loading model %s on %s (%s), cpu_threads=%s",
            model_key,
            device,
            compute_type,
            hardware.physical_cores(),
        )
        try:
            model = WhisperModel(
                str(path),
                device=device,
                compute_type=compute_type,
                cpu_threads=hardware.physical_cores(),
            )
        except Exception as exc:
            if device != "cuda":
                raise
            reason = f"CUDA model load failed: {exc}"
            log.exception(reason)
            self._cuda_failure = reason
            self.fallback.emit(reason)
            self.model_loading.emit(model_key, "cpu")
            model = WhisperModel(
                str(path),
                device="cpu",
                compute_type="int8",
                cpu_threads=hardware.physical_cores(),
            )
            device = "cpu"
        self._model = model
        self._model_key = model_key
        self._device = device
        self.model_ready.emit(model_key, device)

    @Slot(object)
    def transcribe(self, audio: object) -> None:
        if not isinstance(audio, np.ndarray) or audio.size == 0:
            self.transcription_error.emit("No audio was recorded.")
            return
        if self._model is None:
            try:
                if self._configured_model is None:
                    raise RuntimeError("The speech model is not configured.")
                self._load(self._configured_model, self._configured_device)
            except Exception as exc:
                log.exception("Could not load the model for transcription")
                self.transcription_error.emit(str(exc))
                return

        duration = float(len(audio)) / 16_000.0
        started = time.perf_counter()
        try:
            text = self._transcribe_once(audio)
        except Exception as exc:
            if self._device != "cuda":
                log.exception("Transcription failed")
                self.transcription_error.emit(str(exc))
                return
            reason = f"CUDA transcription failed: {exc}"
            log.exception(reason)
            self._cuda_failure = reason
            self.fallback.emit(reason)
            if self._configured_model is None:
                self.transcription_error.emit(reason)
                return
            try:
                self.unload()
                self._load(self._configured_model, "cpu")
                text = self._transcribe_once(audio)
            except Exception as exc2:
                log.exception("CPU transcription failed after CUDA fallback")
                self.transcription_error.emit(str(exc2))
                return
        elapsed = time.perf_counter() - started
        rtf = elapsed / duration if duration > 0 else 0.0
        text = clean_text(text, self._blacklist)
        log.info(
            "Transcribed %.2f s of audio in %.2f s (RTF %.3f) on %s",
            duration,
            elapsed,
            rtf,
            self._device,
        )
        self.transcription_ready.emit(text, duration, elapsed, rtf)

    def _transcribe_once(self, audio: np.ndarray) -> str:
        if self._model is None:
            raise RuntimeError("The speech model is not loaded.")
        language = None if self._language == "auto" else self._language
        segments, _info = self._model.transcribe(
            audio,
            language=language,
            task="transcribe",
            beam_size=5 if self._device == "cuda" else 1,
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 500},
            condition_on_previous_text=False,
            initial_prompt=self._prompt.strip() or None,
            multilingual=self._language == "auto",
        )
        return " ".join(segment.text.strip() for segment in segments).strip()

    @Slot()
    def unload(self) -> None:
        if self._model is None:
            return
        self._model = None
        self._model_key = None
        self._device = None
        gc.collect()
        log.info("Speech model unloaded")
        self.model_unloaded.emit()

    def reset_cuda_failure(self) -> None:
        self._cuda_failure = None
