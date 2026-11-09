"""Benchmark faster-whisper models used by VoiceType.

The script reports wall time, real-time factor (RTF) and peak VRAM for one or
more runs over the same audio file.

Examples::

    python scripts/bench.py --audio tests/audio/ru_60s.wav
    python scripts/bench.py --audio tests/audio/ru_3min.wav --model small --device cpu
    python scripts/bench.py --audio tests/audio/ru_60s.wav --model large-v3-turbo \\
        --device cuda --repeat 3 --output bench-results.json
"""

from __future__ import annotations

import argparse
import json
import platform
import statistics
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from voicetype import __version__, hardware, models  # noqa: E402

SAMPLE_RATE = 16_000


def load_audio(path: Path) -> np.ndarray:
    """Decode any PyAV-supported file to 16 kHz mono float32."""
    import av

    chunks: list[np.ndarray] = []
    with av.open(str(path)) as container:
        stream = next((s for s in container.streams if s.type == "audio"), None)
        if stream is None:
            raise SystemExit(f"{path} does not contain an audio stream")
        resampler = av.AudioResampler(format="flt", layout="mono", rate=SAMPLE_RATE)
        for frame in container.decode(stream):
            for out in resampler.resample(frame):
                chunks.append(out.to_ndarray().reshape(-1))
        for out in resampler.resample(None):
            chunks.append(out.to_ndarray().reshape(-1))
    if not chunks:
        raise SystemExit(f"No audio samples decoded from {path}")
    audio = np.concatenate(chunks).astype(np.float32, copy=False)
    peak = float(np.max(np.abs(audio))) if audio.size else 0.0
    if peak > 1.0:
        audio = audio / peak
    return audio


class VramSampler:
    """Sample NVML memory usage in a background thread."""

    def __init__(self, device_index: int = 0, interval: float = 0.05) -> None:
        self._device_index = device_index
        self._interval = interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.baseline_mb: int | None = None
        self.peak_mb: int | None = None
        self.total_mb: int | None = None
        self._pynvml = None

    def __enter__(self) -> "VramSampler":
        try:
            import pynvml

            self._pynvml = pynvml
            pynvml.nvmlInit()
            handle = pynvml.nvmlDeviceGetHandleByIndex(self._device_index)
            info = pynvml.nvmlDeviceGetMemoryInfo(handle)
            self.baseline_mb = int(info.used // (1024 * 1024))
            self.peak_mb = self.baseline_mb
            self.total_mb = int(info.total // (1024 * 1024))
            self._handle = handle
            self._thread = threading.Thread(
                target=self._run, daemon=True, name="VoiceTypeVramSampler"
            )
            self._thread.start()
        except Exception:
            self._pynvml = None
        return self

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                info = self._pynvml.nvmlDeviceGetMemoryInfo(self._handle)
                used = int(info.used // (1024 * 1024))
                if self.peak_mb is None or used > self.peak_mb:
                    self.peak_mb = used
            except Exception:
                pass
            self._stop.wait(self._interval)

    def __exit__(self, *exc_info) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
        if self._pynvml is not None:
            try:
                self._pynvml.nvmlShutdown()
            except Exception:
                pass

    def result(self) -> dict[str, int | None]:
        delta = None
        if self.peak_mb is not None and self.baseline_mb is not None:
            delta = self.peak_mb - self.baseline_mb
        return {
            "baseline_used_mb": self.baseline_mb,
            "peak_used_mb": self.peak_mb,
            "peak_delta_mb": delta,
            "total_mb": self.total_mb,
        }


def resolve_device(requested: str) -> str:
    if requested == "cpu":
        return "cpu"
    if requested == "cuda":
        if not hardware.cuda_available():
            raise SystemExit("CUDA was requested, but CTranslate2 reports no CUDA device")
        return "cuda"
    gpus = hardware.detect_nvidia_gpus()
    return "cuda" if gpus and hardware.cuda_available() else "cpu"


def resolve_model(requested: str) -> str:
    available = models.available_models()
    if not available:
        raise SystemExit(
            "No speech models found. Run scripts/fetch_models.py --models small first."
        )
    if requested == "auto":
        return available[0]
    if requested not in models.MODELS:
        raise SystemExit(f"Unknown model: {requested}")
    if requested not in available:
        raise SystemExit(
            f"Model {requested} is not downloaded. Available: {', '.join(available)}"
        )
    return requested


def main() -> int:
    parser = argparse.ArgumentParser(description="Benchmark VoiceType speech models")
    parser.add_argument("--audio", required=True, help="Audio file (WAV, MP3, FLAC, ...)")
    parser.add_argument(
        "--model",
        default="auto",
        choices=("auto", *models.MODEL_ORDER),
        help="Model key (default: best downloaded)",
    )
    parser.add_argument(
        "--device", default="auto", choices=("auto", "cuda", "cpu"), help="Device"
    )
    parser.add_argument("--language", default="ru", choices=("ru", "en", "auto"))
    parser.add_argument("--prompt", default="", help="Initial prompt / vocabulary")
    parser.add_argument("--repeat", type=int, default=1, help="Measured runs (default 1)")
    parser.add_argument(
        "--warmup", type=int, default=1, help="Warmup runs, not measured (default 1)"
    )
    parser.add_argument("--output", default="", help="Write JSON results to this path")
    args = parser.parse_args()

    audio_path = Path(args.audio)
    if not audio_path.is_file():
        raise SystemExit(f"Audio file not found: {audio_path}")

    audio = load_audio(audio_path)
    duration = len(audio) / SAMPLE_RATE
    if duration < 0.5:
        raise SystemExit("Audio is too short to benchmark")

    model_key = resolve_model(args.model)
    device = resolve_device(args.device)
    model_path = models.model_path(model_key)
    if model_path is None:
        raise SystemExit(f"Model directory for {model_key} was not found")

    compute_type = hardware.compute_type(device)
    print(
        f"VoiceType {__version__} benchmark\n"
        f"  audio: {audio_path} ({duration:.2f} s)\n"
        f"  model: {model_key} ({model_path})\n"
        f"  device: {device} ({compute_type})\n"
        f"  cpu threads: {hardware.physical_cores()}"
    )

    import faster_whisper

    load_started = time.perf_counter()
    model = faster_whisper.WhisperModel(
        str(model_path),
        device=device,
        compute_type=compute_type,
        cpu_threads=hardware.physical_cores(),
    )
    load_seconds = time.perf_counter() - load_started
    print(f"  model load: {load_seconds:.2f} s")

    language = None if args.language == "auto" else args.language

    def transcribe() -> str:
        segments, _info = model.transcribe(
            audio,
            language=language,
            task="transcribe",
            beam_size=5 if device == "cuda" else 1,
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 500},
            condition_on_previous_text=False,
            initial_prompt=args.prompt.strip() or None,
            multilingual=args.language == "auto",
        )
        return " ".join(segment.text.strip() for segment in segments).strip()

    for index in range(max(0, args.warmup)):
        started = time.perf_counter()
        transcribe()
        print(f"  warmup {index + 1}: {time.perf_counter() - started:.2f} s")

    runs: list[dict[str, float | str]] = []
    with VramSampler() as sampler:
        for index in range(max(1, args.repeat)):
            started = time.perf_counter()
            text = transcribe()
            elapsed = time.perf_counter() - started
            rtf = elapsed / duration if duration else 0.0
            runs.append(
                {
                    "elapsed_s": round(elapsed, 4),
                    "rtf": round(rtf, 4),
                    "chars": len(text),
                    "text": text,
                }
            )
            print(
                f"  run {index + 1}: {elapsed:.2f} s, RTF {rtf:.3f}, "
                f"{len(text)} chars"
            )
        vram = sampler.result()

    elapsed_values = [float(run["elapsed_s"]) for run in runs]
    rtf_values = [float(run["rtf"]) for run in runs]
    summary = {
        "elapsed_mean_s": round(statistics.fmean(elapsed_values), 4),
        "elapsed_min_s": round(min(elapsed_values), 4),
        "elapsed_max_s": round(max(elapsed_values), 4),
        "rtf_mean": round(statistics.fmean(rtf_values), 4),
        "rtf_min": round(min(rtf_values), 4),
        "rtf_max": round(max(rtf_values), 4),
        "chars_mean": round(statistics.fmean([r["chars"] for r in runs]), 1),
    }

    result = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "voicetype_version": __version__,
        "python": platform.python_version(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "processor": platform.processor(),
        "audio": str(audio_path.resolve()),
        "audio_duration_s": round(duration, 3),
        "model": model_key,
        "model_path": str(model_path),
        "device": device,
        "compute_type": compute_type,
        "cpu_threads": hardware.physical_cores(),
        "language": args.language,
        "model_load_s": round(load_seconds, 4),
        "runs": runs,
        "summary": summary,
        "vram": vram,
    }

    print(
        f"\nSummary: mean {summary['elapsed_mean_s']:.2f} s, "
        f"RTF {summary['rtf_mean']:.3f}"
    )
    if vram.get("peak_delta_mb") is not None:
        print(
            f"VRAM: peak {vram['peak_used_mb']} MB "
            f"(+{vram['peak_delta_mb']} MB over baseline, "
            f"{vram['total_mb']} MB total)"
        )

    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        print(f"Saved {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
