from __future__ import annotations

import logging
import time

import numpy as np
import sounddevice as sd
from PySide6.QtCore import QObject, Signal

log = logging.getLogger(__name__)

SAMPLE_RATE = 16_000
CHANNELS = 1


class Recorder(QObject):
    rms_changed = Signal(float)
    limit_reached = Signal()
    error = Signal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._stream: sd.InputStream | None = None
        self._blocks: list[np.ndarray] = []
        self._recording = False
        self._stopping = False
        self._frames = 0
        self._max_frames = 0
        self._limit_emitted = False
        self._last_rms_at = 0.0

    @property
    def is_recording(self) -> bool:
        return self._recording

    @staticmethod
    def input_devices() -> list[tuple[int, str]]:
        devices: list[tuple[int, str]] = []
        try:
            for index, device in enumerate(sd.query_devices()):
                if int(device.get("max_input_channels", 0)) > 0:
                    devices.append((index, str(device.get("name", f"Input {index}"))))
        except Exception:
            log.exception("Could not enumerate audio devices")
        return devices

    @staticmethod
    def _resolve_device(name: str | None) -> int | None:
        if not name:
            return None
        for index, device_name in Recorder.input_devices():
            if device_name == name:
                return index
        log.warning("Microphone %r was not found, using the system default", name)
        return None

    def start(self, device_name: str | None, max_seconds: int) -> bool:
        if self._recording:
            return False
        self._blocks.clear()
        self._frames = 0
        self._max_frames = int(max_seconds * SAMPLE_RATE)
        self._limit_emitted = False
        self._stopping = False
        try:
            device = self._resolve_device(device_name)
            self._stream = sd.InputStream(
                samplerate=SAMPLE_RATE,
                channels=CHANNELS,
                dtype="float32",
                device=device,
                callback=self._callback,
                finished_callback=self._finished,
            )
            self._stream.start()
            self._recording = True
            self._last_rms_at = 0.0
            return True
        except Exception as exc:
            log.exception("Could not start recording")
            self._stream = None
            self.error.emit(f"Could not start recording: {exc}")
            return False

    def stop(self) -> tuple[np.ndarray, float]:
        if not self._recording and self._stream is None:
            return np.empty(0, dtype=np.float32), 0.0
        self._recording = False
        self._stopping = True
        stream = self._stream
        self._stream = None
        if stream is not None:
            try:
                stream.stop()
            except Exception:
                log.debug("Could not stop the input stream", exc_info=True)
            try:
                stream.close()
            except Exception:
                log.debug("Could not close the input stream", exc_info=True)
        if not self._blocks:
            audio = np.empty(0, dtype=np.float32)
        else:
            audio = np.concatenate(self._blocks).astype(np.float32, copy=False)
            audio = audio.reshape(-1)
        self._blocks.clear()
        if len(audio) > self._max_frames > 0:
            audio = audio[: self._max_frames]
        duration = len(audio) / SAMPLE_RATE
        return audio, duration

    def _callback(self, indata, frames, time_info, status) -> None:
        if not self._recording:
            return
        if status:
            log.debug("Audio status: %s", status)
        block = indata[:, 0].copy() if indata.ndim > 1 else indata.copy()
        self._blocks.append(block)
        self._frames += frames
        if self._max_frames and self._frames >= self._max_frames and not self._limit_emitted:
            self._limit_emitted = True
            self.limit_reached.emit()
        now = time.monotonic()
        if now - self._last_rms_at >= 1.0 / 30.0:
            self._last_rms_at = now
            rms = float(np.sqrt(np.mean(np.square(block))))
            self.rms_changed.emit(rms)

    def _finished(self) -> None:
        if self._stopping or not self._recording:
            return
        log.warning("The microphone stream stopped unexpectedly")
        self._recording = False
        self.error.emit("The microphone stream stopped unexpectedly.")
