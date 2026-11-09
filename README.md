# VoiceType

VoiceType is a local push-to-talk speech-to-text app for Windows. Press a global
hotkey, speak, release, and the recognized text is inserted into the input field
that currently has focus. Audio never leaves the machine: recognition runs
locally with [faster-whisper](https://github.com/SYSTRAN/faster-whisper) and
CTranslate2.

## Features

- Global hotkey (push-to-talk by default, toggle mode available)
- Local Russian, English, or automatic language recognition
- Automatic GPU detection with a safe CPU fallback
- Tray icon, no main window, and a small always-on-top recording overlay
- Model manager: `base`, `small`, `medium`, and `large-v3-turbo`
- Optional CUDA acceleration pack downloaded separately from GitHub Releases
- Optional autostart, notification sounds, initial prompt, idle model unload
- Built-in updater verified with SHA-256
- Single-instance application, per-user installer

## Requirements

- Windows 10 21H1 or newer, Windows 11 recommended (64-bit)
- 4 GB RAM minimum, 8 GB recommended
- Any x64 CPU; an NVIDIA GPU is optional
- Microphone

GPU mode uses NVIDIA CUDA through CTranslate2. Without an NVIDIA GPU (or without
the optional CUDA pack) VoiceType runs on the CPU automatically.

## Install

1. Download `VoiceType-Setup-<version>.exe` from
   [Releases](https://github.com/flynet/speech_texter/releases/latest).
2. Verify the download with `SHA256SUMS.txt` if desired.
3. Run the installer. The default installation is per-user and does not require
   administrator rights.
4. VoiceType starts in the system tray. The bundled `small` model is ready
   immediately.

The installer is not code-signed, so Windows SmartScreen may show a warning.
Choose **More info → Run anyway** after verifying the checksum.

## Getting started

1. Open **Settings** from the tray icon.
2. Click the hotkey field and press the desired combination (default:
   `Ctrl+Alt+Space`).
3. Choose the language and, optionally, a microphone.
4. Click **Apply**.
5. Focus any text field (Notepad, a browser, a chat app) and hold the hotkey.
   Speak while holding it, then release. The recognized text is pasted into the
   focused input.

The first GPU transcription may take a few seconds while the model is loaded.
The overlay shows `Processing` while VoiceType is busy; new recordings are
ignored until the current one is inserted.

### Modes

- **Push-to-talk (hold)** — recording runs while the hotkey is held. Releasing
  the hotkey stops recording and starts recognition.
- **Toggle** — press once to start, press again to stop.

Recordings shorter than 0.3 seconds are ignored. The maximum recording length is
configurable (10 minutes by default).

## Settings

| Setting | Description |
| --- | --- |
| Hotkey | Global key combination with at least one modifier and one regular key |
| Mode | Push-to-talk or toggle |
| Language | Russian, English, or Auto |
| Maximum recording | Automatic stop after the selected duration |
| Device | Auto, CUDA, or CPU |
| Model | Auto, Base, Small, Medium, Large v3 Turbo |
| Unload model when idle | Frees RAM/VRAM after a period of inactivity |
| Initial prompt / vocabulary | Names and terms that improve recognition |
| Microphone | Input device; the system default is used by default |
| Sounds | Short start/stop tones |
| Overlay position | Bottom center (default), bottom left, bottom right, top center |
| Start with Windows | Adds an `HKCU\...\Run` entry |
| CUDA pack | Download/remove GPU acceleration libraries |

### Models

| Model | Approximate size | Notes |
| --- | --- | --- |
| `base` | 141 MB | Fastest, lowest accuracy |
| `small` | 464 MB | Bundled default, good CPU/GPU balance |
| `medium` | 1.46 GB | Better accuracy, slower |
| `large-v3-turbo` | 1.55 GB | Best quality, needs a capable GPU |

Model files are downloaded directly from Hugging Face with pinned revisions.
Downloads can be cancelled and are resumed at file granularity.

## GPU acceleration

VoiceType detects NVIDIA GPUs automatically. The installer does **not** include
CUDA libraries; instead, the app can download an optional CUDA pack
(`cuda-pack-v1`) from GitHub Releases on first run or from Settings.

- Default path: `%LOCALAPPDATA%\VoiceType\cuda`
- The archive is verified against `SHA256SUMS.txt` before extraction
- Only the required cuBLAS and cuDNN DLLs are installed
- If CUDA initialization or transcription fails, VoiceType falls back to the
  CPU and reports the reason in a tray notification

The development machine (RTX 4060 Laptop, 8 GB) is used for real measurements.
Use the benchmark script below to measure your own hardware.

## Data locations

| Path | Contents |
| --- | --- |
| `%APPDATA%\VoiceType\config.json` | Settings |
| `%LOCALAPPDATA%\VoiceType\models` | Downloaded models |
| `%LOCALAPPDATA%\VoiceType\cuda` | Optional CUDA DLLs |
| `%LOCALAPPDATA%\VoiceType\logs\app.log` | Rotating log file |

Uninstalling keeps settings and downloaded models. Remove the two directories
above manually for a full cleanup.

## Building from source

### Prerequisites

- Python 3.12 (64-bit)
- [Inno Setup 6](https://jrsoftware.org/isinfo.php) for the installer
- Git

### Setup

```powershell
git clone https://github.com/flynet/speech_texter.git
cd speech_texter
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt -r requirements-build.txt
```

Run from source:

```powershell
$env:PYTHONPATH = "src"
.\.venv\Scripts\python.exe -m voicetype
```

### Build the application

```powershell
.\.venv\Scripts\python.exe scripts\generate_assets.py
.\.venv\Scripts\python.exe scripts\fetch_models.py --models small
.\.venv\Scripts\pyinstaller.exe --noconfirm --clean voicetype.spec
New-Item -ItemType Directory -Force dist\VoiceType\models | Out-Null
Copy-Item -Recurse -Force models\small dist\VoiceType\models\small
```

`models` must be copied next to `dist\VoiceType\VoiceType.exe`, not into
`dist\VoiceType\_internal`.

### Build the installer

```powershell
iscc /DMyAppVersion=0.1.0 installer\voicetype.iss
```

The result is written to `dist-installer\VoiceType-Setup-0.1.0.exe`.

### Build the CUDA pack

```powershell
.\.venv\Scripts\python.exe scripts\build_cuda_pack.py --output dist-cuda
```

This downloads the pinned `nvidia-cublas-cu12` and `nvidia-cudnn-cu12` wheels,
extracts the Windows DLLs, creates `voicetype-cuda-pack.zip`, and writes
`SHA256SUMS.txt`. The **CUDA pack** GitHub Actions workflow (tag
`cuda-pack-v*` or manual dispatch) publishes the result as release
`cuda-pack-v1`.

## Benchmarks

`scripts/bench.py` measures wall time, real-time factor (RTF), and peak VRAM for
any audio file supported by PyAV.

```powershell
.\.venv\Scripts\python.exe scripts\bench.py --audio tests\audio\ru_60s.wav
.\.venv\Scripts\python.exe scripts\bench.py --audio tests\audio\ru_60s.wav `
    --model small --device cpu --repeat 3 --output bench-results.json
```

Recommended test audio: 16 kHz, mono, PCM WAV, 60 seconds and 3 minutes of
Russian speech. See `tests/audio/README.md`.

RTF below 1.0 means transcription is faster than real time. Results depend on the
model, device, CPU, and GPU.

Measured on the development machine (Intel Core i5-12500H, NVIDIA GeForce RTX
4060 Laptop 8 GB, Windows 11) with 60 seconds of spontaneous Russian speech,
warmup 1, repeat 2, VAD enabled:

| Model | Device | RTF | Model load | Peak used VRAM |
| --- | --- | --- | --- | --- |
| `small` | CPU (`int8`) | 0.077 | 0.87 s | n/a |
| `small` | CUDA (`int8_float16`) | 0.038 | 1.07 s | ~2.0 GB total |
| `medium` | CUDA (`int8_float16`) | 0.072 | 2.70 s | ~2.6 GB total |
| `large-v3-turbo` | CUDA (`int8_float16`) | 0.027 | 2.79 s | ~2.6 GB total |

For a 3-minute recording (the 61 s sample repeated to 180 s):

| Model | Device | Time | RTF |
| --- | --- | --- | --- |
| `small` | CPU (`int8`) | 14.03 s | 0.078 |
| `small` | CUDA (`int8_float16`) | 6.97 s | 0.039 |
| `large-v3-turbo` | CUDA (`int8_float16`) | 4.16 s | 0.023 |

The curated CUDA pack is 642.6 MB compressed (919.2 MB uncompressed) and was
verified with `small`, `medium`, and `large-v3-turbo`. The PyInstaller application
directory is 336.4 MB without models.

## Manual acceptance checklist

- [ ] First run with no internet shows a clear error and opens Settings
- [ ] Hotkey works in Notepad and in a browser text field
- [ ] Text is inserted into the focused field; focus is not stolen by VoiceType
- [ ] A press shorter than 0.3 s is ignored
- [ ] A 3-minute recording transcribes correctly and does not block the UI
- [ ] New recordings are ignored while the overlay shows `Processing`
- [ ] CPU mode works on a machine without an NVIDIA GPU
- [ ] CUDA mode falls back to CPU if the CUDA pack is missing or broken
- [ ] Clipboard content is restored after insertion
- [ ] Autostart toggles survive a reboot / sign-out
- [ ] Single instance: launching a second copy shows a message
- [ ] Update check finds the latest release and verifies SHA-256
- [ ] Uninstall keeps `%APPDATA%\VoiceType` and `%LOCALAPPDATA%\VoiceType`

## Troubleshooting

- **No text is inserted.** Some elevated applications (for example Task Manager)
  reject synthetic input from a non-elevated app. Run VoiceType at the same
  privilege level as the target application.
- **The hotkey does nothing in a specific app.** Global hotkeys are captured at
  the system level; if another app registers the same combination, choose a
  different one.
- **Clipboard is not restored.** VoiceType saves and restores plain text only.
  Non-text clipboard formats (images, files, rich text) are not preserved.
- **Microphone not found.** Reconnect the device, open Settings, and press
  **Refresh**.
- **CUDA errors.** Check `%LOCALAPPDATA%\VoiceType\logs\app.log`. VoiceType
  should fall back to CPU automatically; you can also set **Device** to **CPU**.
- **Antivirus warnings.** PyInstaller executables are sometimes flagged. Verify
  the SHA-256 checksum and add an exclusion only if you trust the release.

Logs are written to `%LOCALAPPDATA%\VoiceType\logs\app.log` (rotated, up to four
files of 1 MB).

## Privacy

VoiceType does not upload audio or recognized text. Network access is used only
for:

- downloading models from Hugging Face;
- downloading the optional CUDA pack from GitHub Releases;
- checking for updates on GitHub.

## Limitations

- Windows only (global hotkeys, clipboard integration, and installer are
  Windows-specific).
- Text insertion uses the clipboard and a synthetic `Ctrl+V`, so the target
  application must support pasting.
- Plain-text clipboard contents are restored; other formats are not.
- The application is not code-signed.
