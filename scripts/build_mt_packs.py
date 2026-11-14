#!/usr/bin/env python3
"""Build VoiceType translation packs.

Every target language except English runs on a local OPUS-MT model converted to
the CTranslate2 format. This script converts one model, patches its config and
publishes a zip that VoiceType downloads on demand, so packs never enter the
installer.

    python scripts/build_mt_packs.py --targets ru,de,fr,es,it,nl,uk,cs,sv,zh
    python scripts/build_mt_packs.py --targets ru --quantization float16

Requires torch, transformers and ctranslate2, which are build-time tools: they
are not shipped in the app, the app only needs ctranslate2 and sentencepiece.
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from voicetype import __version__
from voicetype.translation import TARGET_LANGUAGES, REQUIRED_PACK_FILES

CONVERTER_MODULE = "ctranslate2.converters.transformers"
HF_REPO_URL = "https://huggingface.co/{}"
PACK_FILES = ("source.spm", "target.spm", "vocab.json", "generation_config.json")


def build_info(code: str):
    for info in TARGET_LANGUAGES:
        if info.code == code:
            return info
    raise SystemExit(f"Unknown target language: {code}. Known: {known()}")


def known() -> str:
    return ", ".join(info.code for info in TARGET_LANGUAGES if info.asset)


def convert(info, work: Path, quantization: str) -> None:
    """Run the CTranslate2 converter for one Helsinki OPUS-MT repository."""
    command = [
        sys.executable,
        "-m",
        CONVERTER_MODULE,
        "--model",
        info.source_repo,
        "--output_dir",
        str(work),
        "--quantization",
        quantization,
        "--force",
        # The SentencePiece files are the tokenizer: VoiceType needs them, the
        # converter does not copy them by default.
        "--copy_files",
        *PACK_FILES,
    ]
    print(f"converting {info.source_repo} ({quantization})")
    subprocess.run(command, check=True, capture_output=True, text=True)


def patch_config(work: Path) -> None:
    """Fix the two Marian options the HF converter leaves out.

    Marian models are trained with a sentence-final token in the source text,
    and the HF converter records no model type, so CTranslate2 defaults to a
    plain Transformer layout. Without these two keys the decoder starts the
    sentence at the wrong place and repeats words in a loop.
    """
    path = work / "config.json"
    config = json.loads(path.read_text(encoding="utf-8"))
    config["model_type"] = "marian"
    config["add_source_eos"] = True
    path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")


def notice_text(info, license_name: str, quantization: str) -> str:
    date = datetime.date.today().isoformat()
    return (
        f"VoiceType translation pack en-{info.code} ({info.display_name})\n"
        f"Source model: {info.source_repo} <{HF_REPO_URL.format(info.source_repo)}>\n"
        f"OPUS-MT / Tatoeba-Challenge, trained by the University of Helsinki.\n"
        f"License: {license_name}\n"
        f"Converted to CTranslate2 {quantization} by VoiceType {__version__} "
        f"with ct2-transformers-converter on {date}.\n"
        "Source files copied unchanged: source.spm, target.spm, vocab.json.\n"
        "The model is provided as-is, without any warranty.\n"
    )


def source_meta(info, license_name: str, quantization: str) -> str:
    meta = {
        "tool": "VoiceType",
        "version": __version__,
        "source_repo": info.source_repo,
        "license": license_name,
        "quantization": quantization,
        "converter": "ct2-transformers-converter",
        "ct2_version": _ct2_version(),
        "built": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    return json.dumps(meta, indent=2, sort_keys=True) + "\n"


def _ct2_version() -> str:
    import ctranslate2

    return ctranslate2.__version__


def license_of(info) -> str:
    """Read the license recorded in the repository, not the one hardcoded here."""
    import requests

    response = requests.get(
        f"https://huggingface.co/api/models/{info.source_repo}",
        headers={"User-Agent": f"VoiceType/{__version__}"},
        timeout=30,
    )
    response.raise_for_status()
    license_name = (response.json().get("license") or "").replace("_", "-")
    return license_name or info.license


def pack_zip(info, work: Path, dist: Path, quantization: str) -> Path:
    license_name = license_of(info)
    (work / "NOTICE.txt").write_text(notice_text(info, license_name, quantization), encoding="utf-8")
    (work / "SOURCE.json").write_text(source_meta(info, license_name, quantization), encoding="utf-8")
    missing = [name for name in REQUIRED_PACK_FILES if not (work / name).is_file()]
    if missing:
        raise SystemExit(f"Pack en-{info.code} is incomplete: {', '.join(missing)}")
    dist.mkdir(parents=True, exist_ok=True)
    zip_path = dist / f"voicetype-mt-en-{info.code}.zip"
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for file in sorted(work.iterdir()):
            if file.is_file():
                archive.write(file, file.name)
    size_mb = zip_path.stat().st_size / (1024 * 1024)
    print(f"pack en-{info.code}: {size_mb:.1f} MB -> {zip_path}")
    return zip_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--targets", default=known(), help="comma-separated target codes")
    parser.add_argument("--dist", default=str(ROOT / "dist" / "mt-packs"))
    parser.add_argument("--quantization", default="int8", help="int8, int8_float16 or float16")
    parser.add_argument("--work", default=str(ROOT / "dist" / "mt-packs-work"))
    options = parser.parse_args()
    dist = Path(options.dist)
    work_root = Path(options.work)
    zips = []
    for code in [part.strip() for part in options.targets.split(",") if part.strip()]:
        info = build_info(code)
        work = work_root / f"en-{info.code}"
        if work.exists():
            shutil.rmtree(work)
        work.mkdir(parents=True)
        convert(info, work, options.quantization)
        patch_config(work)
        zips.append(pack_zip(info, work, dist, options.quantization))

    sums = dist / "SHA256SUMS.txt"
    lines = []
    for zip_path in zips:
        digest = hashlib.sha256()
        with zip_path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
        lines.append(f"{digest.hexdigest()}  {zip_path.name}")
    sums.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"checksums -> {sums}")


if __name__ == "__main__":
    main()
