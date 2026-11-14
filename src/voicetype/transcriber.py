from __future__ import annotations

import gc
import logging
import time

import numpy as np
from PySide6.QtCore import QObject, Signal, Slot

from . import hardware
from . import models
from . import translation
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
        self._translate: bool = False
        self._target_language: str = "en"
        self._prompt: str = ""
        self._blacklist: list[str] = []
        self._cuda_failure: str | None = None

    @Slot(str, str, str, str, object, bool, bool, str)
    def configure(
        self,
        model_key: str,
        device: str,
        language: str,
        prompt: str,
        blacklist: object,
        retry_cuda: bool = False,
        translate: bool = False,
        target_language: str = "en",
    ) -> None:
        self._language = language
        self._translate = bool(translate)
        self._target_language = target_language
        self._prompt = prompt
        if isinstance(blacklist, list):
            self._blacklist = [str(item) for item in blacklist]
        if retry_cuda:
            # The user explicitly requested CUDA again. A previous failure may
            # have been transient (for example, not enough free VRAM).
            self._cuda_failure = None
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

    def _plan(self) -> str:
        """What this run needs: "whisper", "mt", "pivot", or "none".

        "whisper" and "none" need no pack at all, "mt" and "pivot" run the
        local OPUS-MT pack after Whisper.
        """
        if not self._translate:
            return "none"
        return translation.translation_plan(self._language, self._target_language)

    @Slot(object)
    def transcribe(self, audio: object) -> None:
        if not isinstance(audio, np.ndarray) or audio.size == 0:
            self.transcription_error.emit("No audio was recorded.")
            return
        # faster-whisper requires a strictly 1D float32 array. The recorder
        # already returns mono, so flattening here is lossless and protects
        # against regressions such as an (N, 1) array.
        audio = np.asarray(audio, dtype=np.float32).reshape(-1)
        plan = self._plan()
        # Every target except English needs a local pack, so check it before
        # loading a model: the user sees the reason without waiting for Whisper.
        if plan in {"mt", "pivot"} and not translation.pack_available(
            self._target_language
        ):
            info = translation.target_info(self._target_language)
            name = info.display_name if info is not None else self._target_language
            self.transcription_error.emit(
                f"The {name} translation pack is not downloaded. Download "
                "it in Settings, or choose English as the target."
            )
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
        # Whisper turns speech into English for the "whisper" and "pivot" plans;
        # "mt" starts from English text and "none" keeps the spoken language.
        whisper_english = plan in {"whisper", "pivot"}
        try:
            text = self._transcribe_once(audio, whisper_english)
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
                text = self._transcribe_once(audio, whisper_english)
            except Exception as exc2:
                log.exception("CPU transcription failed after CUDA fallback")
                self.transcription_error.emit(str(exc2))
                return
        text = clean_text(text, self._blacklist)
        if plan in {"mt", "pivot"}:
            try:
                text = self._translate_with_pack(text, plan)
            except Exception as exc:
                log.exception("Translation pack failed")
                self.transcription_error.emit(str(exc))
                return
        elapsed = time.perf_counter() - started
        rtf = elapsed / duration if duration > 0 else 0.0
        log.info(
            "Transcribed %.2f s of audio in %.2f s (RTF %.3f) on %s",
            duration,
            elapsed,
            rtf,
            self._device,
        )
        self.transcription_ready.emit(text, duration, elapsed, rtf)

    def _translate_with_pack(self, text: str, plan: str) -> str:
        """Run the local OPUS-MT pack over the English text, on the CPU."""
        if not text:
            return text
        info = translation.target_info(self._target_language)
        name = info.display_name if info is not None else self._target_language
        started = time.perf_counter()
        translated = translation.translate_text(self._target_language, text)
        log.info(
            "Translated %d words into %s (%s) in %.2f s",
            len(text.split()),
            name,
            plan,
            time.perf_counter() - started,
        )
        if not translated:
            # Short clips can leave a Marian pack with no usable output. Keeping
            # the recognized text is better than inserting nothing at all: the
            # user sees what Whisper heard and can translate it again later.
            log.warning(
                "The %s translation pack returned no text; keeping the "
                "recognized text",
                name,
            )
            return text
        return translated

    def _transcribe_once(self, audio: np.ndarray, whisper_english: bool) -> str:
        if self._model is None:
            raise RuntimeError("The speech model is not loaded.")
        language = None if self._language == "auto" else self._language
        # Whisper translates speech into English only, so the English stage is
        # the task flag. Any other target language comes from a translation pack.
        segments, info = self._model.transcribe(
            audio,
            language=language,
            task="translate" if whisper_english else "transcribe",
            beam_size=5 if self._device == "cuda" else 1,
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 500},
            condition_on_previous_text=False,
            initial_prompt=self._prompt.strip() or None,
            multilingual=self._language == "auto",
        )
        text = " ".join(segment.text.strip() for segment in segments).strip()
        if whisper_english:
            detected = getattr(info, "language", None)
            log.info(
                "Translated speech from %s into English",
                detected if detected is not None else "unknown",
            )
        return text

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

    @Slot()
    def quit_thread(self) -> None:
        # QThread.quit() must run inside the worker thread, so it is invoked
        # through this queued slot instead of being called from the GUI thread.
        thread = self.thread()
        if thread is not None:
            thread.quit()
