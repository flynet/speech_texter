"""Build the VoiceType CUDA shim pack for Windows x64.

The pack contains the cuBLAS and cuDNN DLLs required by CTranslate2. It is
published separately from the installer as release ``cuda-pack-v1``.

Usage::

    python scripts/build_cuda_pack.py --output dist-cuda
    python scripts/build_cuda_pack.py --output dist-cuda --keep-wheels
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Keep in sync with src/voicetype/cuda.py (PACK_TAG, PACK_NAME).
PACK_TAG = "cuda-pack-v1"
PACK_NAME = "voicetype-cuda-pack.zip"

CUBLAS_VERSION = "12.9.2.10"
CUDNN_VERSION = "9.9.0.52"

PACKAGES = (
    f"nvidia-cublas-cu12=={CUBLAS_VERSION}",
    f"nvidia-cudnn-cu12=={CUDNN_VERSION}",
)

# CTranslate2 uses cuBLAS for GEMM and cuDNN for the convolutional frontend.
# The remaining cuDNN libraries (RNN/attention, JIT-compiled and precompiled
# engine caches, nvBLAS) are not used by faster-whisper and would add roughly
# 900 MB to the download. Use --full to include every DLL from the wheels.
CURATED_DLLS = (
    "cublas64_12.dll",
    "cublasLt64_12.dll",
    "cudnn64_9.dll",
    "cudnn_ops64_9.dll",
    "cudnn_cnn64_9.dll",
    "cudnn_graph64_9.dll",
    "cudnn_heuristic64_9.dll",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _human(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GB"


def download_wheels(destination: Path) -> list[Path]:
    destination.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable,
        "-m",
        "pip",
        "download",
        "--no-deps",
        "--only-binary=:all:",
        "--dest",
        str(destination),
        *PACKAGES,
    ]
    print("Running:", " ".join(command))
    subprocess.run(command, check=True)
    wheels = sorted(destination.glob("*.whl"))
    if not wheels:
        raise SystemExit("pip did not download any wheels")
    return wheels


def extract_dlls(wheels: list[Path], staging: Path, full: bool = False) -> list[Path]:
    staging.mkdir(parents=True, exist_ok=True)
    seen: dict[str, bytes] = {}
    copied: list[Path] = []
    skipped: list[tuple[str, int]] = []
    for wheel in wheels:
        with zipfile.ZipFile(wheel) as archive:
            for info in archive.infolist():
                name = info.filename
                if info.is_dir() or not name.lower().endswith(".dll"):
                    continue
                parts = name.split("/")
                if "nvidia" not in parts or "bin" not in parts:
                    continue
                dll_name = Path(name).name
                if not full and dll_name not in CURATED_DLLS:
                    skipped.append((dll_name, info.file_size))
                    continue
                target = staging / dll_name
                data = archive.read(info)
                existing = seen.get(target.name)
                if existing is not None:
                    if existing != data:
                        raise SystemExit(
                            f"Conflicting DLL with the same name: {target.name}"
                        )
                    continue
                seen[target.name] = data
                target.write_bytes(data)
                copied.append(target)
    if not copied:
        raise SystemExit("No NVIDIA DLLs were found in the downloaded wheels")
    if not full:
        missing = [name for name in CURATED_DLLS if name not in seen]
        if missing:
            raise SystemExit(f"Expected DLLs are missing from the wheels: {missing}")
        skipped_size = sum(size for _, size in skipped)
        print(
            f"Skipping {len(skipped)} unused DLL(s), "
            f"{_human(skipped_size)} uncompressed"
        )
    return copied


def build_archive(staging: Path, output: Path) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    archive_path = output / PACK_NAME
    with zipfile.ZipFile(
        archive_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6
    ) as archive:
        for dll in sorted(staging.glob("*.dll")):
            archive.write(dll, arcname=dll.name)
        version = (
            f"{PACK_TAG}\n"
            f"nvidia-cublas-cu12=={CUBLAS_VERSION}\n"
            f"nvidia-cudnn-cu12=={CUDNN_VERSION}\n"
        )
        archive.writestr("version.txt", version)
    return archive_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the VoiceType CUDA pack")
    parser.add_argument(
        "--output", default=str(ROOT / "dist-cuda"), help="Output directory"
    )
    parser.add_argument(
        "--keep-wheels",
        action="store_true",
        help="Keep downloaded wheels in <output>/wheels for inspection",
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help="Include every DLL from the NVIDIA wheels (larger but maximally compatible)",
    )
    args = parser.parse_args()

    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    wheels_dir = output / "wheels" if args.keep_wheels else Path(
        tempfile.mkdtemp(prefix="voicetype-cuda-wheels-")
    )
    staging = Path(tempfile.mkdtemp(prefix="voicetype-cuda-stage-"))
    try:
        wheels = download_wheels(wheels_dir)
        print(f"Downloaded {len(wheels)} wheel(s) into {wheels_dir}")
        dlls = extract_dlls(wheels, staging, full=args.full)
        total = sum(dll.stat().st_size for dll in dlls)
        print(f"Extracted {len(dlls)} DLL(s), {_human(total)} uncompressed:")
        for dll in sorted(dlls):
            print(f"  {dll.name:40s} {_human(dll.stat().st_size):>10s}")
        archive = build_archive(staging, output)
        digest = _sha256(archive)
        sums = output / "SHA256SUMS.txt"
        sums.write_text(f"{digest}  {PACK_NAME}\n", encoding="utf-8")
        print(
            f"\nCreated {archive} ({_human(archive.stat().st_size)})\n"
            f"SHA256 {digest}\nWrote {sums}"
        )
        return 0
    finally:
        shutil.rmtree(staging, ignore_errors=True)
        if not args.keep_wheels:
            shutil.rmtree(wheels_dir, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
