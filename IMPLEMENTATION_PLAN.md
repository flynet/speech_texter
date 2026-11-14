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
| License | **MIT** in `LICENSE`. Non-commercial models (CC-BY-NC, for example NLLB) are not used |
| Uninstall | User config and models are kept |
| Autostart | `HKCU\Software\Microsoft\Windows\CurrentVersion\Run` |
| Translation | Off by default. Phase 1 uses the Whisper `translate` task, which turns speech into **English** only. Other target languages need local translation packs, downloaded on demand, never bundled |
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
  mt-packs.yml         # tag mt-packs-v* -> mt-packs-v1 release
voicetype.spec         # PyInstaller --onedir
requirements.txt
requirements-build.txt
requirements-packs.txt
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

### Stage 7 — Translation

Phase 1 (done):

- `config.py`: `translation_enabled` (off) and `target_language` (`en` default),
  `TRANSLATION_TARGETS` as a literal kept in sync with `translation.TARGETS` by a
  test, `CONFIG_VERSION = 2`, clamped in `normalize()`.
- `transcriber.py`: `configure(..., translate, target_language)`;
  `translation_plan` picks the stage, so `task="translate"` runs for an English
  target and for a pivoted target, and plain transcription runs for English
  speech and for the `none` plan.
- `ui/settings.py`: **Translation** group between the model and audio groups —
  checkbox + separate target selector; the **Language** selector stays independent.
- `models.TRANSLATION_MODEL_ORDER = ("medium", "small", "base")` and
  `hardware.resolve_selection(..., translate)`: with translation on, the fast
  distilled model is skipped. Measured on `tests/audio/ru_60s.wav` (CUDA,
  `int8_float16`), `large-v3-turbo` with `task="translate"` returned Russian
  (`Что тут еще нужно говорить? Я ебу.`), while `base`, `small`, and `medium`
  returned English (`What else do you need to say? I will eat.`).
- Test bench passes translation through, the profile line shows `translate to DE`
  and the result meta names the target (`translated to German`); the tray status
  shows `translate → DE`.

Phase 2 (done, see `docs/research-translation.md`):

- `sentencepiece==0.2.3` (Apache-2.0) in `requirements.txt` and
  `collect_all("sentencepiece")` in `voicetype.spec`.
- `translation.py`: `TARGET_LANGUAGES` registry (10 packs + English),
  `REQUIRED_PACK_FILES` (`model.bin`, `config.json`, `source.spm`, `target.spm`),
  SHA-256-verified on-demand download from release `mt-packs-v1`, safe zip
  extraction, cached `PackTranslator` (spm + `ctranslate2.Translator`, CPU int8,
  chunks of ≤40 words, decode cap `min(4 × source, 128)` tokens), idle unload,
  `forget_translator`, `delete_pack`.
- Packs are converted from `Helsinki-NLP/opus-mt-en-*` by
  `scripts/build_mt_packs.py` (`int8`, 60–68 MB each) with `NOTICE.txt` and
  `SOURCE.json`, published by `.github/workflows/mt-packs.yml` as `mt-packs-v1`.
  Pre-converted `gaudi` packs are not used: the project runs the converter
  itself, because Marian needs `model_type=marian` and `add_source_eos` that the
  published packs do not carry, and without them the decoder loops.
- `translation_plan(source, target)` returns `whisper`, `mt`, `pivot`, or `none`;
  `TranscriptionWorker` runs the pack after Whisper for `mt` and `pivot`, so
  Russian speech reaches German through English, and English speech reaches
  Russian without the Whisper translate task.
- `ui/settings.py`: pack rows with size, **Download**, progress, cancel,
  **Remove**; `app.py` runs downloads and removals off the UI thread, and the
  test bench reports the target language in the result meta.
- License: the project is MIT and no CC-BY-NC model is used. `opus-mt-en-de` is
  CC-BY-4.0, the rest Apache-2.0; NLLB stays out of the plan.

Measured on the development machine (CPU only, 4 threads, `int8`), 20 dictation
phrases, 175 words, 3 passes, `scripts/bench_translation.py`: cold pack load
0.28–0.35 s, 0.05–0.07 s per phrase (5.9–8.4 ms per word), 91–105 MB per loaded
pack, reference coverage 0.62–0.88, all reference numbers preserved, no empty
output. A/B on 60 s of real Russian speech with `small` on CPU: `transcribe`
6.46 s, `translate` 9.45 s (46% slower, invented numbers: spoken
"60 секунд" became `10 seconds`), pack `en-de` 0.68 s, faithful to its English
input. The same recording for 30 s with `en-es`: `transcribe` 3.29 s,
`translate` 2.88 s, pack 0.37 s, and every digit of `1,2,3,4,5` and `60`
survives the pack stage.

Acceptance: Russian speech with translation on produces English text; with it
off, the original language is returned; both selectors act independently; while
translation is on, the tray shows a full multilingual model, not
`large-v3-turbo`; a target that needs a pack reports it before Whisper loads;
**Remove** frees the pack files; a target equal to the spoken language keeps the
recognized text.

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
8. Whisper translation is weaker supervision than transcription: names, numbers,
   and dates can be paraphrased or invented. Measured A/B on 60 s of real Russian
   speech (`small`, CPU): translate is 46% slower than transcribe and invented
   numbers (spoken `60 секунд` became `10 seconds`, `Разве 3-4-5 вышел` became
   `1,2,3,4,5`). Documented in the README; the pack stage keeps the numbers of
   the text it is given.
9. Non-English pairs are sparse in the OPUS-MT catalog, so most of them need the
   English pivot: two stages, extra latency, quality loss. Measured pack CPU cost
   is 0.05–0.07 s per phrase, under a second per dictation run, and a pack is
   60–68 MB in `int8`, not the ~150 MB of the published HF conversions.
10. Distilled Whisper models keep the source language in `translate` mode, so
    translation runs `medium`, `small`, or `base`. Short clips of 2–3 seconds
    translate poorly and can return empty text; CUDA translate time for 20 s of
    audio is 0.4–1.1 s. CPU translate measured: 9.45 s for 60 s of speech,
    against 6.46 s for transcribe. When the pack returns no text, VoiceType keeps
    the recognized text.
11. Reference coverage is 0.62–0.66 for Russian, Ukrainian, and Czech against
    0.82–0.88 for German, French, Dutch, and Swedish, because the pack picks
    inflected word forms the reference does not use. The number is a fidelity
    indicator, not a quality score.
12. The 0.3.0 installer shipped a broken SentencePiece: `_sentencepiece` failed to
    load (`DLL load failed while importing _sentencepiece`) and VoiceType 0.3.0 did
    not start. A repaired wheel ships its runtime DLLs in `<package>.libs` with
    hash names (`msvcp140-<hash>.dll`) and patches the package to add that
    directory to the DLL search path. PyInstaller recognizes those DLLs as the
    Microsoft C++ runtime, normalizes the name, and moves the copy to the bundle
    root, so the hash name the extension asks for is gone. Packages with their own
    PyInstaller hook (numpy, av) keep the directory; sentencepiece has no hook, so
    the DLLs are copied as `datas`, which copies them verbatim, and
    `scripts/check_bundle_libs.py` fails the release build when a hash-named DLL is
    missing from the bundle. Adding the DLLs to `binaries` looked fine in the local
    build and failed on the runner, which is why the bundle check runs in CI. The
    tokenizer is imported when a pack loads, so a broken tokenizer is a pack error
    at translation time, not a startup crash. The re-published installer was
    verified after install: the bundle has every hash-named DLL, the installed
    tokenizer loads a downloaded pack (`开会时间是早上九点` for en → zh), and
    the installed app starts with CUDA and loads the model. A re-published tag does
    not update anyone: the broken 0.3.0 that shipped first reports the same
    `__version__` as the fixed one, so the update check finds nothing to do, and
    users who installed it within the ~90 minutes it was published have an app that
    starts, dictates, and fails on every translation pack. The fix therefore ships
    as **v0.3.1**, which the update check on a broken 0.3.0 finds. The 0.3.1
    installer was verified after install: its published SHA-256 matched,
    `check_bundle_libs.py` passes on the installed bundle, the installed tokenizer
    translates (`开会时间是早上九点`), the app starts as `Starting VoiceType 0.3.1`
    on CUDA, and the update check offers 0.3.1 to a machine on 0.3.0 and 0.2.1 and
    nothing to 0.3.1.
13. Live UI acceptance found that pressing **Cancel** on a download row produced a
    failure notice (`Translation pack download failed / Download cancelled.`).
    Cancelling is a user choice, not an error. Model, pack, CUDA, and update
    downloads now share `config.CANCELLED_MESSAGE`: a cancelled download resets
    the row, logs at info, and emits no failure notice, while real failures keep
    their `... failed` title. The CUDA tray line is restored when a download is
    cancelled with no Settings window open, which is the CUDA prompt path.

## 6. Test audio

Files used locally (16 kHz, mono, PCM WAV):

- `tests/audio/ru_60s.wav` — 60 s trim of a 61 s Russian recording
- `tests/audio/ru_3min.wav` — the same recording repeated to 180 s

The source recording is informal spontaneous Russian speech. Audio files are
ignored by git. `scripts/bench.py` accepts any WAV/audio file via `--audio`.

## 7. Measured results

Development machine: Intel Core i5-12500H (12 physical cores), NVIDIA GeForce
RTX 4060 Laptop 8 GB, Windows 11, Python 3.12.

Benchmark audio: 60.00 s of Russian speech, warmup 1, repeat 2, VAD enabled.
`RTF` is wall time divided by audio duration.

| Model | Device | Compute | Model load | RTF | Peak used VRAM |
| --- | --- | --- | --- | --- | --- |
| `small` | CPU | `int8` | 0.87 s | 0.077 | n/a |
| `small` | CUDA | `int8_float16` | 1.07 s | 0.038 | ~2.0 GB total |
| `medium` | CUDA | `int8_float16` | 2.70 s | 0.072 | ~2.6 GB total |
| `large-v3-turbo` | CUDA | `int8_float16` | 2.79 s | 0.027 | ~2.6 GB total |

3-minute recording (the 61 s sample repeated to 180 s), one measured run:

| Model | Device | Time | RTF |
| --- | --- | --- | --- |
| `small` | CPU | 14.03 s | 0.078 |
| `small` | CUDA | 6.97 s | 0.039 |
| `large-v3-turbo` | CUDA | 4.16 s | 0.023 |

Notes:

- CUDA is roughly 2× faster than CPU on this machine for `small` on 60 s audio.
- `large-v3-turbo` produced the most accurate transcript of the spontaneous
  speech sample; `small` made noticeably more word errors.
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
