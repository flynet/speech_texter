"""Regenerate derived assets and notification sounds.

``assets/app.ico`` is maintained as a source file: replace it to change the
application icon used by the executable, the installer, shortcuts, and the
tray. This script only refreshes the PNG preview and the notification sounds.

Run it after changing the icon or the sound design::

    .venv\\Scripts\\python.exe scripts/generate_assets.py
"""

from __future__ import annotations

import math
import wave
from pathlib import Path

import numpy as np
from PySide6.QtGui import QImageReader

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "assets"
SOUNDS = ASSETS / "sounds"


def sync_icon_preview() -> None:
    source = ASSETS / "app.ico"
    reader = QImageReader(str(source))
    image = reader.read()
    if image.isNull():
        raise RuntimeError(f"Could not read {source}")
    target = ASSETS / "icon-256.png"
    if not image.save(str(target), "PNG"):
        raise RuntimeError(f"Could not write {target}")
    print(f"Wrote {target}")


def tone_sequence(notes: list[tuple[float, float]], amplitude: float = 0.18) -> np.ndarray:
    sample_rate = 16_000
    fade = int(0.008 * sample_rate)
    chunks: list[np.ndarray] = []
    for frequency, duration in notes:
        count = int(duration * sample_rate)
        t = np.arange(count, dtype=np.float64) / sample_rate
        envelope = np.ones(count, dtype=np.float64)
        if fade > 0 and count > 2 * fade:
            ramp = np.linspace(0.0, 1.0, fade)
            envelope[:fade] = ramp
            envelope[-fade:] = ramp[::-1]
        wave_data = np.sin(2.0 * math.pi * frequency * t) * envelope * amplitude
        chunks.append(wave_data)
    return np.concatenate(chunks)


def write_wav(path: Path, samples: np.ndarray) -> None:
    pcm = np.clip(samples, -1.0, 1.0)
    pcm = (pcm * 32767.0).astype("<i2")
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(16_000)
        handle.writeframes(pcm.tobytes())


def main() -> int:
    ASSETS.mkdir(parents=True, exist_ok=True)
    SOUNDS.mkdir(parents=True, exist_ok=True)

    sync_icon_preview()

    start = tone_sequence([(880.0, 0.055), (1318.5, 0.065)])
    end = tone_sequence([(1174.7, 0.055), (784.0, 0.075)])
    write_wav(SOUNDS / "start.wav", start)
    write_wav(SOUNDS / "end.wav", end)
    print(f"Wrote {SOUNDS / 'start.wav'}")
    print(f"Wrote {SOUNDS / 'end.wav'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
