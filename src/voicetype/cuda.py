from __future__ import annotations

import ctypes
import hashlib
import logging
import os
import shutil
import tempfile
import threading
import zipfile
from pathlib import Path
from typing import Callable

import requests

from . import __version__
from . import config as cfg

log = logging.getLogger(__name__)

PACK_TAG = "cuda-pack-v1"
PACK_NAME = "voicetype-cuda-pack.zip"
SUMS_NAME = "SHA256SUMS.txt"
PACK_URL = f"https://github.com/flynet/speech_texter/releases/download/{PACK_TAG}"
CUDA_PACK_APPROX_MB = 650

REQUIRED_DLLS = (
    "cublas64_12.dll",
    "cublasLt64_12.dll",
    "cudnn64_9.dll",
    "cudnn_ops64_9.dll",
    "cudnn_cnn64_9.dll",
    "cudnn_graph64_9.dll",
)

_dll_handle: object | None = None


class CudaDownloadCancelled(Exception):
    """Raised when the user cancels the CUDA pack download."""


ProgressCallback = Callable[[int, int], None]


def cuda_pack_installed() -> bool:
    directory = cfg.cuda_dir()
    return all((directory / name).is_file() for name in REQUIRED_DLLS)


def cuda_pack_version() -> str | None:
    version_file = cfg.cuda_dir() / "version.txt"
    try:
        return version_file.read_text(encoding="utf-8").strip()
    except OSError:
        return None


def activate_cuda_libs() -> bool:
    """Make the downloaded CUDA DLLs visible to CTranslate2."""
    global _dll_handle
    if _dll_handle is not None:
        return True
    directory = cfg.cuda_dir()
    if not directory.is_dir():
        return False
    if hasattr(os, "add_dll_directory"):
        try:
            _dll_handle = os.add_dll_directory(str(directory))
        except OSError:
            log.exception("Could not add the CUDA DLL directory")
            return False
    os.environ["PATH"] = str(directory) + os.pathsep + os.environ.get("PATH", "")
    log.info("Activated CUDA libraries from %s", directory)
    return True


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().lower()


def _download(
    url: str,
    target: Path,
    progress: ProgressCallback | None,
    cancel: threading.Event | None,
) -> None:
    with requests.get(
        url,
        stream=True,
        headers={"User-Agent": f"VoiceType/{__version__}"},
        timeout=(20, 120),
    ) as response:
        response.raise_for_status()
        total = int(response.headers.get("Content-Length") or 0)
        done = 0
        with target.open("wb") as handle:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if cancel is not None and cancel.is_set():
                    raise CudaDownloadCancelled()
                if not chunk:
                    continue
                handle.write(chunk)
                done += len(chunk)
                if progress is not None:
                    progress(done, total)


def _expected_hash(sums_text: str, filename: str) -> str:
    for line in sums_text.splitlines():
        parts = line.strip().split()
        if len(parts) >= 2 and parts[-1].lstrip("*") == filename:
            return parts[0].lower()
    raise RuntimeError(f"{filename} is missing from {SUMS_NAME}")


def download_and_install_pack(
    progress: ProgressCallback | None = None,
    cancel: threading.Event | None = None,
) -> Path:
    """Download, verify and install the CUDA acceleration pack."""
    work = Path(tempfile.mkdtemp(prefix="voicetype-cuda-"))
    zip_path = work / PACK_NAME
    try:
        log.info("Downloading CUDA pack from %s", PACK_URL)
        _download(f"{PACK_URL}/{PACK_NAME}", zip_path, progress, cancel)
        sums_response = requests.get(
            f"{PACK_URL}/{SUMS_NAME}",
            headers={"User-Agent": f"VoiceType/{__version__}"},
            timeout=20,
        )
        sums_response.raise_for_status()
        expected = _expected_hash(sums_response.text, PACK_NAME)
        actual = _sha256(zip_path)
        if actual != expected:
            raise RuntimeError(
                f"CUDA pack checksum mismatch: expected {expected}, got {actual}"
            )

        destination = cfg.cuda_dir()
        destination.mkdir(parents=True, exist_ok=True)
        # Remove old DLLs only after the new archive has been verified.
        for old in destination.glob("*.dll"):
            try:
                old.unlink()
            except OSError:
                pass

        installed = 0
        with zipfile.ZipFile(zip_path) as archive:
            for info in archive.infolist():
                if info.is_dir() or not info.filename.lower().endswith(".dll"):
                    continue
                name = Path(info.filename).name
                with archive.open(info) as source, (destination / name).open("wb") as target:
                    shutil.copyfileobj(source, target)
                installed += 1
        (destination / "version.txt").write_text(PACK_TAG + "\n", encoding="utf-8")
        log.info("Installed %d CUDA DLLs into %s", installed, destination)
        activate_cuda_libs()
        return destination
    finally:
        shutil.rmtree(work, ignore_errors=True)
