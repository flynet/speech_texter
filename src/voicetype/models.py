from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import requests

from . import __version__
from . import config as cfg

log = logging.getLogger(__name__)

USER_AGENT = f"VoiceType/{__version__}"

HF_API_URL = "https://huggingface.co/api/models/{repo}?blobs=true&revision={revision}"
HF_RESOLVE_URL = "https://huggingface.co/{repo}/resolve/{revision}/{filename}"

# Files faster-whisper needs to load a model without network access. Without
# tokenizer.json, faster-whisper silently falls back to downloading a tokenizer
# from openai/whisper-tiny on first use, which breaks offline operation.
REQUIRED_MODEL_FILES = ("model.bin", "config.json", "tokenizer.json")


@dataclass(frozen=True)
class ModelInfo:
    key: str
    display_name: str
    repo_id: str
    revision: str
    approximate_size_mb: int
    description: str = ""
    fallback_files: tuple[tuple[str, int], ...] = ()


MODELS: dict[str, ModelInfo] = {
    "large-v3-turbo": ModelInfo(
        key="large-v3-turbo",
        display_name="Large v3 Turbo",
        repo_id="deepdml/faster-whisper-large-v3-turbo-ct2",
        revision="4df90f75321148c3a29a9e2351b7ddf8f5b115a8",
        approximate_size_mb=1547,
        description="Best quality, needs about 1.4 GB of free VRAM.",
        fallback_files=(
            ("config.json", 0),
            ("model.bin", 0),
            ("preprocessor_config.json", 0),
            ("tokenizer.json", 0),
            ("vocabulary.json", 0),
        ),
    ),
    "medium": ModelInfo(
        key="medium",
        display_name="Medium",
        repo_id="Systran/faster-whisper-medium",
        revision="08e178d48790749d25932bbc082711ddcfdfbc4f",
        approximate_size_mb=1460,
        description="High quality, slower on CPU.",
        fallback_files=(
            ("config.json", 0),
            ("model.bin", 0),
            ("tokenizer.json", 0),
            ("vocabulary.txt", 0),
        ),
    ),
    "small": ModelInfo(
        key="small",
        display_name="Small",
        repo_id="Systran/faster-whisper-small",
        revision="536b0662742c02347bc0e980a01041f333bce120",
        approximate_size_mb=464,
        description="Bundled default. Good balance for CPU and GPU.",
        fallback_files=(
            ("config.json", 0),
            ("model.bin", 0),
            ("tokenizer.json", 0),
            ("vocabulary.txt", 0),
        ),
    ),
    "base": ModelInfo(
        key="base",
        display_name="Base",
        repo_id="Systran/faster-whisper-base",
        revision="ebe41f70d5b6dfa9166e2c581c45c9c0cfc57b66",
        approximate_size_mb=141,
        description="Smallest download, lower accuracy.",
        fallback_files=(
            ("config.json", 0),
            ("model.bin", 0),
            ("tokenizer.json", 0),
            ("vocabulary.txt", 0),
        ),
    ),
}

MODEL_ORDER = ("large-v3-turbo", "medium", "small", "base")


class DownloadCancelled(Exception):
    """Raised when the user cancels a model download."""


ProgressCallback = Callable[[int, int], None]


def model_dirs(key: str) -> list[Path]:
    return [cfg.bundled_models_dir() / key, cfg.models_dir() / key]


def _is_model_complete(directory: Path) -> bool:
    return all((directory / name).is_file() for name in REQUIRED_MODEL_FILES)


def model_path(key: str, prefer_bundled: bool = True) -> Path | None:
    candidates = model_dirs(key)
    if not prefer_bundled:
        candidates = list(reversed(candidates))
    for path in candidates:
        if _is_model_complete(path):
            return path
    return None


def is_available(key: str) -> bool:
    return model_path(key) is not None


def model_status(key: str) -> str:
    bundled = cfg.bundled_models_dir() / key
    if _is_model_complete(bundled):
        return "bundled"
    local = cfg.models_dir() / key
    if _is_model_complete(local):
        return "downloaded"
    return "missing"


def available_models() -> list[str]:
    return [key for key in MODEL_ORDER if is_available(key)]


def _remote_files(info: ModelInfo) -> tuple[list[tuple[str, int]], int]:
    """Return files and total size from the Hugging Face API."""
    try:
        response = requests.get(
            HF_API_URL.format(repo=info.repo_id, revision=info.revision),
            headers={"User-Agent": USER_AGENT},
            timeout=20,
        )
        response.raise_for_status()
        payload = response.json()
        files: list[tuple[str, int]] = []
        for sibling in payload.get("siblings", []):
            name = sibling.get("rfilename")
            if not name or name in {".gitattributes", "README.md"}:
                continue
            size = int(sibling.get("size") or 0)
            files.append((name, size))
        if files:
            total = sum(size for _, size in files)
            return files, total
    except Exception:
        log.warning("Could not read the file list for %s", info.repo_id, exc_info=True)
    return list(info.fallback_files), 0


def _download_file(
    url: str,
    target: Path,
    expected_size: int,
    downloaded_before: int,
    total: int,
    progress: ProgressCallback | None,
    cancel: threading.Event | None,
) -> int:
    tmp = target.with_name(target.name + ".part")
    done = 0
    try:
        with requests.get(
            url,
            stream=True,
            headers={"User-Agent": USER_AGENT},
            timeout=(20, 120),
        ) as response:
            response.raise_for_status()
            with tmp.open("wb") as handle:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if cancel is not None and cancel.is_set():
                        raise DownloadCancelled()
                    if not chunk:
                        continue
                    handle.write(chunk)
                    done += len(chunk)
                    if progress is not None:
                        progress(downloaded_before + done, total)
        if expected_size and tmp.stat().st_size != expected_size:
            raise RuntimeError(
                f"size mismatch for {target.name}: expected {expected_size}, "
                f"got {tmp.stat().st_size}"
            )
        os.replace(tmp, target)
        return done
    except Exception:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def download_model(
    key: str,
    target_dir: Path | None = None,
    progress: ProgressCallback | None = None,
    cancel: threading.Event | None = None,
) -> Path:
    """Download a CTranslate2 model directory from Hugging Face.

    The download is resumable at file granularity and supports cooperative
    cancellation through ``cancel``.
    """
    if key not in MODELS:
        raise KeyError(f"Unknown model: {key}")
    info = MODELS[key]
    dest = target_dir if target_dir is not None else cfg.models_dir() / key
    dest.mkdir(parents=True, exist_ok=True)

    files, total = _remote_files(info)
    if progress is not None:
        progress(0, total)

    downloaded = 0
    for filename, expected_size in files:
        target = dest / filename
        if target.is_file():
            size = target.stat().st_size
            if not expected_size or size == expected_size:
                downloaded += size
                if progress is not None:
                    progress(downloaded, total)
                continue
        url = HF_RESOLVE_URL.format(
            repo=info.repo_id,
            revision=info.revision,
            filename=filename,
        )
        log.info("Downloading %s/%s", info.repo_id, filename)
        downloaded += _download_file(
            url=url,
            target=target,
            expected_size=expected_size,
            downloaded_before=downloaded,
            total=total,
            progress=progress,
            cancel=cancel,
        )

    if not _is_model_complete(dest):
        raise RuntimeError(f"Model download for {key} is incomplete")

    if progress is not None:
        progress(max(downloaded, total), max(downloaded, total))
    log.info("Model %s is ready at %s", key, dest)
    return dest
