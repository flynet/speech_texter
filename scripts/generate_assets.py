"""Generate VoiceType icons and notification sounds.

The generated files are committed to the repository so that PyInstaller and
Inno Setup do not need to run this script. Run it manually after changing the
icon or the sound design::

    .venv\\Scripts\\python.exe scripts/generate_assets.py
"""

from __future__ import annotations

import io
import math
import struct
import wave
from pathlib import Path

import numpy as np
from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QRectF, Qt
from PySide6.QtGui import (
    QBrush,
    QColor,
    QImage,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
)

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "assets"
SOUNDS = ASSETS / "sounds"
ICON_SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)


def _lerp_color(start: QColor, end: QColor, t: float) -> QColor:
    return QColor(
        int(start.red() + (end.red() - start.red()) * t),
        int(start.green() + (end.green() - start.green()) * t),
        int(start.blue() + (end.blue() - start.blue()) * t),
        int(start.alpha() + (end.alpha() - start.alpha()) * t),
    )


def draw_icon(size: int) -> QImage:
    scale = size / 256.0
    image = QImage(size, size, QImage.Format_ARGB32)
    image.fill(Qt.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.Antialiasing, True)

    # Rounded square background with a subtle diagonal gradient.
    gradient = QLinearGradient(0, 0, size, size)
    gradient.setColorAt(0.0, QColor("#4F8EF7"))
    gradient.setColorAt(1.0, QColor("#1B2C5C"))
    path = QPainterPath()
    path.addRoundedRect(
        QRectF(6 * scale, 6 * scale, 244 * scale, 244 * scale),
        58 * scale,
        58 * scale,
    )
    painter.fillPath(path, QBrush(gradient))

    # Soft highlight in the upper-left corner.
    highlight = QLinearGradient(0, 0, size * 0.75, size * 0.75)
    highlight.setColorAt(0.0, QColor(255, 255, 255, 70))
    highlight.setColorAt(1.0, QColor(255, 255, 255, 0))
    painter.fillPath(path, QBrush(highlight))

    # Microphone body.
    body = QColor("#FFFFFF")
    painter.setPen(Qt.NoPen)
    painter.setBrush(body)
    painter.drawRoundedRect(
        QRectF(96 * scale, 58 * scale, 64 * scale, 104 * scale),
        32 * scale,
        32 * scale,
    )

    # Microphone cradle.
    pen = QPen(body, max(2.0, 13 * scale))
    pen.setCapStyle(Qt.RoundCap)
    painter.setPen(pen)
    painter.setBrush(Qt.NoBrush)
    painter.drawArc(
        QRectF(70 * scale, 96 * scale, 116 * scale, 112 * scale),
        180 * 16,
        180 * 16,
    )

    # Stand and base.
    painter.drawLine(128 * scale, 206 * scale, 128 * scale, 222 * scale)
    painter.drawLine(96 * scale, 226 * scale, 160 * scale, 226 * scale)

    # Accent dot indicates the active recording state.
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor("#4CC38A"))
    painter.drawEllipse(QRectF(180 * scale, 180 * scale, 44 * scale, 44 * scale))

    painter.end()
    return image


def png_bytes(image: QImage) -> bytes:
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.WriteOnly)
    if not image.save(buffer, "PNG"):
        raise RuntimeError("Could not encode a PNG image")
    buffer.close()
    return bytes(data)


def write_ico(path: Path, images: list[QImage]) -> None:
    frames = [(image.width(), png_bytes(image)) for image in images]
    header = struct.pack("<HHH", 0, 1, len(frames))
    entries = bytearray()
    payload = bytearray()
    offset = 6 + 16 * len(frames)
    for size, data in frames:
        dimension = 0 if size >= 256 else size
        entries += struct.pack(
            "<BBBBHHII",
            dimension,
            dimension,
            0,
            0,
            1,
            32,
            len(data),
            offset,
        )
        payload += data
        offset += len(data)
    path.write_bytes(header + bytes(entries) + bytes(payload))


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

    images = [draw_icon(size) for size in ICON_SIZES]
    write_ico(ASSETS / "app.ico", images)
    images[-1].save(str(ASSETS / "icon-256.png"), "PNG")
    print(f"Wrote {ASSETS / 'app.ico'}")
    print(f"Wrote {ASSETS / 'icon-256.png'}")

    start = tone_sequence([(880.0, 0.055), (1318.5, 0.065)])
    end = tone_sequence([(1174.7, 0.055), (784.0, 0.075)])
    write_wav(SOUNDS / "start.wav", start)
    write_wav(SOUNDS / "end.wav", end)
    print(f"Wrote {SOUNDS / 'start.wav'}")
    print(f"Wrote {SOUNDS / 'end.wav'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
