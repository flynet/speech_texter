from __future__ import annotations

import logging
from dataclasses import dataclass

import psutil

from . import cuda
from . import models

log = logging.getLogger(__name__)

_cuda_checked = False
_cuda_ok = False


@dataclass(frozen=True)
class GpuInfo:
    index: int
    name: str
    total_mb: int
    free_mb: int
    driver: str


@dataclass(frozen=True)
class Selection:
    device: str
    model: str
    reason: str
    gpu_name: str | None = None
    free_vram_mb: int | None = None


class NoModelsAvailable(RuntimeError):
    pass


def detect_nvidia_gpus() -> list[GpuInfo]:
    try:
        import pynvml
    except Exception:
        return []
    gpus: list[GpuInfo] = []
    try:
        pynvml.nvmlInit()
        driver = _decode(pynvml.nvmlSystemGetDriverVersion())
        for index in range(pynvml.nvmlDeviceGetCount()):
            handle = pynvml.nvmlDeviceGetHandleByIndex(index)
            name = _decode(pynvml.nvmlDeviceGetName(handle))
            memory = pynvml.nvmlDeviceGetMemoryInfo(handle)
            gpus.append(
                GpuInfo(
                    index=index,
                    name=name,
                    total_mb=int(memory.total // (1024 * 1024)),
                    free_mb=int(memory.free // (1024 * 1024)),
                    driver=driver,
                )
            )
    except Exception:
        log.debug("NVML is not available", exc_info=True)
        return []
    finally:
        try:
            pynvml.nvmlShutdown()
        except Exception:
            pass
    return gpus


def _decode(value: object) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def physical_cores() -> int:
    return int(psutil.cpu_count(logical=False) or psutil.cpu_count() or 4)


def reset_cuda_cache() -> None:
    global _cuda_checked, _cuda_ok
    _cuda_checked = False
    _cuda_ok = False


def cuda_libraries_available() -> bool:
    """Check that the DLLs CTranslate2 needs for CUDA can actually be loaded.

    ``ctranslate2.get_cuda_device_count()`` only asks the driver, so it returns
    a positive number even when cuBLAS/cuDNN are missing. Loading the DLLs first
    avoids selecting CUDA on machines that would fail on the first model load.
    """
    if cuda.cuda_pack_installed():
        cuda.activate_cuda_libs()
    try:
        import ctypes

        for name in cuda.REQUIRED_DLLS:
            ctypes.WinDLL(name)
        return True
    except OSError as exc:
        log.info("CUDA libraries are not loadable: %s", exc)
        return False


def cuda_available() -> bool:
    global _cuda_checked, _cuda_ok
    if _cuda_checked:
        return _cuda_ok
    _cuda_checked = True
    if not cuda_libraries_available():
        _cuda_ok = False
    else:
        try:
            import ctranslate2

            _cuda_ok = ctranslate2.get_cuda_device_count() > 0
        except Exception:
            log.warning("CTranslate2 could not report CUDA devices", exc_info=True)
            _cuda_ok = False
    log.info("CUDA available: %s", _cuda_ok)
    return _cuda_ok


def _best_available(available: list[str]) -> str:
    for key in models.MODEL_ORDER:
        if key in available:
            return key
    raise NoModelsAvailable("No speech models are installed")


def resolve_selection(model_pref: str, device_pref: str) -> Selection:
    available = models.available_models()
    if not available:
        raise NoModelsAvailable(
            "No speech models are installed. Download one in Settings."
        )

    gpus = detect_nvidia_gpus()
    cuda_ok = bool(gpus) and cuda_available()
    free_vram = gpus[0].free_mb if gpus else None

    if device_pref == "cpu":
        device = "cpu"
        reason = "CPU selected in settings"
    elif device_pref == "cuda":
        device = "cuda" if cuda_ok else "cpu"
        reason = "CUDA selected in settings" if cuda_ok else "CUDA unavailable, using CPU"
    else:
        if not gpus:
            device = "cpu"
            reason = "No NVIDIA GPU detected"
        elif not cuda_ok:
            device = "cpu"
            reason = "NVIDIA GPU detected, but CUDA is not available"
        elif free_vram is not None and free_vram < 800:
            device = "cpu"
            reason = "Less than 800 MB of free VRAM, using CPU"
        else:
            device = "cuda"
            reason = "NVIDIA GPU with CUDA detected"

    if model_pref != "auto":
        if model_pref in available:
            model = model_pref
            reason += f"; model {model_pref} selected in settings"
        else:
            model = _best_available(available)
            reason += f"; model {model_pref} is not downloaded, using {model}"
    elif device == "cuda":
        if free_vram is not None and free_vram >= 1400 and "large-v3-turbo" in available:
            model = "large-v3-turbo"
        elif "small" in available:
            model = "small"
        elif "medium" in available:
            model = "medium"
        elif "base" in available:
            model = "base"
        else:
            model = _best_available(available)
        reason += f"; free VRAM: {free_vram} MB"
    else:
        if physical_cores() >= 6 and "small" in available:
            model = "small"
        elif "base" in available:
            model = "base"
        elif "small" in available:
            model = "small"
        else:
            model = _best_available(available)
        reason += f"; physical cores: {physical_cores()}"

    gpu_name = gpus[0].name if gpus else None
    return Selection(
        device=device,
        model=model,
        reason=reason,
        gpu_name=gpu_name,
        free_vram_mb=free_vram,
    )


def compute_type(device: str) -> str:
    return "int8_float16" if device == "cuda" else "int8"
