# VoiceType — Implementation Plan

This document fixes the implementation decisions for the VoiceType desktop app.
The source requirements are in `Техплан VoiceType — локальный голосовой ввод для Windows.md`.

## 1. Fixed decisions

| Item | Decision |
| --- | --- |
| Repository | `https://github.com/flynet/speech_texter`, default branch `main` |
| Product name | **VoiceType** (`VoiceType.exe`, `%APPDATA%\VoiceType`, `%LOCALAPPDATA%\VoiceType`) |
| Python | **3.12**, virtual environment in `.venv` |
| CI Python | **3.12** |
| GPU detection | Automatic via `pynvml` + `ctranslate2.get_cuda_device_count()`; no NVIDIA/CUDA → CPU |
| 2 GB VRAM | Target guideline for profiles; real measurements on RTX 4060 Laptop 8 GB |
| First run model | Best already downloaded model; larger models are offered for download |
| Busy state | New recordings are blocked while a transcription is processing |
| Hotkey mode | Push-to-talk by default; toggle mode is a setting |
| Installer | `small` model bundled; size limit may be relaxed to ~800 MB if needed |
| CUDA pack | Separate release `cuda-pack-v1`; `nvidia-cublas-cu12==12.9.2.10`, `nvidia-cudnn-cu12==9.9.0.52` |
| UI / README | English |
| Clipboard | Save and restore **text only**; documented limitation |
| Uninstall | User config and models are kept |
| Autostart | `HKCU\Software\Microsoft\Windows\CurrentVersion\Run` |
| Overlay position | Presets: bottom-center (default), bottom-left, bottom-right, top-center |

## 2. Project layout

```
src/voicetype/
  __init__.py          # version and product constants
  __main__.py          # entry point, single-instance mutex, logging
  app.py               # component wiring, state machine, downloads, updates
  config.py            # paths, config dataclass, JSON load/save
  hardware.py          # GPU/VRAM/CPU detection, profile selection
  cuda.py              # CUDA pack detect/download/install/DLL activation
  models.py            # model registry, local paths, Hugging Face downloads
  transcriber.py       # QThread worker, model loading, fallback, filtering
  recorder.py          # sounddevice stream, RMS, max duration
  hotkeys.py           # pynput global listener, combo parse/format
  inserter.py          # clipboard + SendInput Ctrl+V
  updater.py           # GitHub Releases check/download/install
  autostart.py         # HKCU Run key helper
  ui/
    tray.py            # tray icon, menu, state icons
    overlay.py         # frameless no-focus pill, waveform, spinner
    settings.py        # settings window, model/CUDA managers, hotkey recorder
    theme.py           # dark/light theme and QSS
scripts/
  bench.py             # RTF / VRAM benchmark
  fetch_models.py      # download bundled models for build
  build_cuda_pack.py   # build voicetype-cuda-pack.zip
  generate_assets.py   # generate icons and notification sounds
installer/
  voicetype.iss        # Inno Setup 6
.github/workflows/
  release.yml          # tag v* -> installer + SHA256SUMS
  cuda-pack.yml        # tag cuda-pack-v* -> cuda-pack-v1 release
voicetype.spec         # PyInstaller --onedir
requirements.txt
requirements-build.txt
pyproject.toml
README.md
tests/audio/README.md
```

## 3. Dependencies

Initial pins (exact transitive pins are fixed after the first successful install/build):

- Runtime: `ctranslate2==4.8.5`, `faster-whisper==1.2.1`, `PySide6-Essentials==6.12.0`,
  `sounddevice==0.5.6`, `pynput==1.8.2`, `huggingface-hub==2.4.0`,
  `nvidia-ml-py==13.615.71`, `numpy==2.5.4`, `psutil==7.2.2`, `requests==2.34.2`
- Build: `pyinstaller==6.22.3`
- CUDA pack: `nvidia-cublas-cu12==12.9.2.10`, `nvidia-cudnn-cu12==9.9.0.52`
  (newer cuBLAS releases no longer publish Windows wheels)

Pinned Hugging Face model revisions:

| Key | Repository | Revision |
| --- | --- | --- |
| `base` | `Systran/faster-whisper-base` | `ebe41f70d5b6dfa9166e2c581c45c9c0cfc57b66` |
| `small` | `Systran/faster-whisper-small` | `536b0662742c02347bc0e980a01041f333bce120` |
| `medium` | `Systran/faster-whisper-medium` | `08e178d48790749d25932bbc082711ddcfdfbc4f` |
| `large-v3-turbo` | `deepdml/faster-whisper-large-v3-turbo-ct2` | `4df90f75321148c3a29a9e2351b7ddf8f5b115a8` |

## 4. Stages

### Stage 0 — Bootstrap

- Initialize git, add `.gitignore`, `pyproject.toml`, `requirements*.txt`.
- Create `.venv` with Python 3.12 and install dependencies.
- Install Inno Setup 6 for local packaging.
- Smoke-test imports, GPU detection, microphone enumeration, Qt start.
- Create `tests/audio/` with the expected input file names.

### Stage 1 — Core without UI

- `config.py`: defaults, atomic JSON save, paths, resource paths.
- `hardware.py`: GPU list/free VRAM, physical cores, CUDA availability, profile resolution.
- `models.py`: registry, bundled/local lookup, HF file download with progress/cancel.
- `cuda.py`: pack presence, download, SHA-256, safe extraction, DLL activation.
- `transcriber.py`: worker thread, lazy model load, fallback to CPU, hallucination filter.
- `recorder.py`: 16 kHz mono float32 stream, RMS, max duration, device errors.
- `hotkeys.py`: global listener, canonical combos, hold edge events.
- `inserter.py`: wait for modifier release, clipboard backup/paste/restore.
- `__main__.py` / `app.py`: mutex, logging, console wiring.
- `scripts/bench.py`: RTF, wall time, peak VRAM.

Acceptance: Notepad + browser insertion, 3-minute recording, CPU mode, VRAM measurement,
sub-0.3 s presses ignored.

### Stage 2 — UI

- `ui/theme.py`: system dark/light detection and QSS.
- `ui/tray.py`: menu, language, autostart, updates, state icons.
- `ui/overlay.py`: 220×56 pill, 24 bars, spinner, fade, no-focus Win32 flags.
- `ui/settings.py`: hotkey recorder, language, model/device, mic, sounds, limits, prompt,
  idle unload, overlay position, autostart, model/CUDA download, updates, version.

Acceptance: overlay under 100 ms, focus preserved, settings apply live, single instance.

### Stage 3 — Packaging

- Generate icons and sounds.
- `voicetype.spec`: onedir, no console, excludes, `collect_all` for native packages.
- `installer/voicetype.iss`: fixed AppId, per-user default, AppMutex, close applications,
  desktop/autostart tasks, launch after install.
- Local build; clean Windows test by the user.

### Stage 4 — GPU pack

- Build script and workflow for `cuda-pack-v1`.
- First-run prompt, download with progress, extract to `%LOCALAPPDATA%\VoiceType\cuda`.
- Add DLL directory before importing CTranslate2.
- Benchmark CUDA vs CPU and document RTF.

### Stage 5 — Updates

- GitHub releases list filtered to normal `vX.Y.Z` tags, semver comparison,
  daily + manual checks.
- Download installer with progress, verify SHA-256 from `SHA256SUMS.txt`.
- Run `/SILENT /CLOSEAPPLICATIONS /RESTARTAPPLICATIONS`, quit the app.

### Stage 6 — CI/CD

- `release.yml` on `v*`: Python 3.12, model cache, PyInstaller, Inno Setup, SHA-256,
  GitHub Release with installer and `SHA256SUMS.txt`.
- `cuda-pack.yml` on `cuda-pack-v*` or manual dispatch: publish `cuda-pack-v1`.
- Publish `v0.1.0` and verify the installer on clean Windows 11.

## 5. Known risks and checks

1. `ctranslate2 4.8.5` + CUDA 12.9 + cuDNN 9.9 on Windows.
2. `huggingface-hub 2.4` compatibility with `faster-whisper 1.2.1`
   (fallback: 1.33.x or 0.36.x).
3. `PySide6 6.12` + PyInstaller 6.22 packaging.
4. Microphone stream start latency ≤100 ms; if not achievable, evaluate a pre-opened stream.
5. Installer payload: PyInstaller onedir (336 MB) + bundled `small` model (464 MB)
   ≈ 800 MB before compression; compressed installer is expected to be smaller.
6. Idle RAM ≤600 MB with `small`. Measured working set after CUDA model load:
   ~450 MB (frozen build, `large-v3-turbo`, 20 s after start).
7. Exact 2 GB VRAM behavior cannot be measured on the 8 GB development machine.

## 6. Test audio

Expected files (provide 16 kHz, mono, PCM WAV):

- `tests/audio/ru_60s.wav` — benchmark
- `tests/audio/ru_3min.wav` — long-recording acceptance

`scripts/bench.py` accepts any WAV/audio file via `--audio`.

## 7. Measured results

Development machine: Intel Core i5-12500H (12 physical cores), NVIDIA GeForce
RTX 4060 Laptop 8 GB, Windows 11, Python 3.12.

Benchmark audio: 10.59 s English SAPI TTS (`tests/audio/tts_test.wav`), warmup 1,
repeat 2. `RTF` is wall time divided by audio duration.

| Model | Device | Compute | Model load | RTF | Peak used VRAM |
| --- | --- | --- | --- | --- | --- |
| `small` | CPU | `int8` | 0.90 s | 0.150 | n/a |
| `small` | CUDA | `int8_float16` | 1.11 s | 0.038 | ~1.9 GB total |
| `medium` | CUDA | `int8_float16` | 3.18 s | 0.067 | ~2.5 GB total |
| `large-v3-turbo` | CUDA | `int8_float16` | 3.05 s | 0.034 | ~2.6 GB total |

Notes:

- CUDA is roughly 4× faster than CPU on this machine for `small`.
- CUDA transcription was verified with the curated 7-DLL pack
  (`cublas64_12`, `cublasLt64_12`, `cudnn64_9`, `cudnn_cnn64_9`,
  `cudnn_graph64_9`, `cudnn_heuristic64_9`, `cudnn_ops64_9`).
- Removing `cudnn_heuristic64_9.dll` did not change results on this
  machine; `cublasLt64_12.dll` is required.
- Curated pack: 642.6 MB zip, 919.2 MB uncompressed. The `--full` pack contains
  11 DLLs (1.2 GB zip).
- PyInstaller onedir build without models: 336.4 MB.
- Bundled payload (`small` model included): 800.1 MB. Compressed installer:
  501.4 MB. Silent install and uninstall verified; uninstall keeps
  `%APPDATA%\VoiceType\config.json` and `%LOCALAPPDATA%\VoiceType\cuda`.
- Frozen application verified: starts, loads the best downloaded model on CUDA,
  writes `%LOCALAPPDATA%\VoiceType\logs\app.log`, and stays in the tray.
- Real Russian speech measurements still need `ru_60s.wav` and `ru_3min.wav`.
