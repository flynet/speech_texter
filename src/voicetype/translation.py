from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import tempfile
import threading
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import ctranslate2
import requests
import sentencepiece as spm

from . import __version__
from . import config as cfg

log = logging.getLogger(__name__)

# Whisper translates speech into English only, so every other target language
# runs through English first: speech -> English text -> target text.
PIVOT = "en"

USER_AGENT = f"VoiceType/{__version__}"

# Translation packs are converted OPUS-MT models in the CTranslate2 format.
# CI builds them once (scripts/build_mt_packs.py) and publishes them as assets of
# this release tag, so they are downloaded on demand and never bundled.
PACKS_TAG = "mt-packs-v1"
PACKS_URL = f"https://github.com/flynet/speech_texter/releases/download/{PACKS_TAG}"
SUMS_NAME = "SHA256SUMS.txt"

# Files a CTranslate2 translation pack needs to run fully offline. The
# SentencePiece files are the tokenizer: without them the pack cannot be used.
REQUIRED_PACK_FILES = ("model.bin", "config.json", "source.spm", "target.spm")

# Marian needs two options that the HF converter does not record: a model type
# and the sentence-final token the source text was trained with. Packs built by
# scripts/build_mt_packs.py already carry them, and packs downloaded from an
# older build are fixed on load, otherwise the decoder loops on repeated words.
MARIAN_CONFIG = {"model_type": "marian", "add_source_eos": True}

# OPUS-MT models are quality-tested on short sentences, and very long chunks
# lose attention, so text is translated piece by piece.
MAX_CHUNK_WORDS = 40


class PackCancelled(Exception):
    """Raised when the user cancels a translation pack download."""


ProgressCallback = Callable[[int, int], None]


@dataclass(frozen=True)
class TargetLanguage:
    code: str
    display_name: str
    # None means no pack is needed: English text comes from Whisper itself.
    asset: str | None = None
    source_repo: str = ""
    license: str = ""
    size_mb: int = 0
    # Language token a multilingual group model needs before the translation.
    # No shipped pack uses it: Helsinki has no single-pair model for English to
    # Polish or Turkish, and the group models (en-sla, en-trk, en-mul) do not
    # carry those language tokens in their vocabulary, so they cannot be
    # restricted to one target language.
    target_prefix: str | None = None


# Targets are ordered English first, then by the size of the user base, matching
# the order the Settings combo shows them in. The sizes are the measured sizes
# of the int8 packs scripts/build_mt_packs.py builds, so the download in
# Settings shows real numbers.
TARGET_LANGUAGES: tuple[TargetLanguage, ...] = (
    TargetLanguage(code="en", display_name="English"),
    TargetLanguage(
        code="ru",
        display_name="Russian",
        asset="voicetype-mt-en-ru.zip",
        source_repo="Helsinki-NLP/opus-mt-en-ru",
        license="Apache-2.0",
        size_mb=84,
    ),
    TargetLanguage(
        code="de",
        display_name="German",
        asset="voicetype-mt-en-de.zip",
        source_repo="Helsinki-NLP/opus-mt-en-de",
        license="CC-BY-4.0",
        size_mb=60,
    ),
    TargetLanguage(
        code="fr",
        display_name="French",
        asset="voicetype-mt-en-fr.zip",
        source_repo="Helsinki-NLP/opus-mt-en-fr",
        license="Apache-2.0",
        size_mb=60,
    ),
    TargetLanguage(
        code="es",
        display_name="Spanish",
        asset="voicetype-mt-en-es.zip",
        source_repo="Helsinki-NLP/opus-mt-en-es",
        license="Apache-2.0",
        size_mb=66,
    ),
    TargetLanguage(
        code="it",
        display_name="Italian",
        asset="voicetype-mt-en-it.zip",
        source_repo="Helsinki-NLP/opus-mt-en-it",
        license="Apache-2.0",
        size_mb=67,
    ),
    TargetLanguage(
        code="nl",
        display_name="Dutch",
        asset="voicetype-mt-en-nl.zip",
        source_repo="Helsinki-NLP/opus-mt-en-nl",
        license="Apache-2.0",
        size_mb=66,
    ),
    TargetLanguage(
        code="uk",
        display_name="Ukrainian",
        asset="voicetype-mt-en-uk.zip",
        source_repo="Helsinki-NLP/opus-mt-en-uk",
        license="Apache-2.0",
        size_mb=65,
    ),
    TargetLanguage(
        code="cs",
        display_name="Czech",
        asset="voicetype-mt-en-cs.zip",
        source_repo="Helsinki-NLP/opus-mt-en-cs",
        license="Apache-2.0",
        size_mb=66,
    ),
    TargetLanguage(
        code="sv",
        display_name="Swedish",
        asset="voicetype-mt-en-sv.zip",
        source_repo="Helsinki-NLP/opus-mt-en-sv",
        license="Apache-2.0",
        size_mb=64,
    ),
    TargetLanguage(
        code="zh",
        display_name="Chinese",
        asset="voicetype-mt-en-zh.zip",
        source_repo="Helsinki-NLP/opus-mt-en-zh",
        license="Apache-2.0",
        size_mb=68,
    ),
)

TARGETS = tuple(t.code for t in TARGET_LANGUAGES)

PACKS: dict[str, TargetLanguage] = {t.code: t for t in TARGET_LANGUAGES if t.asset}


def target_info(code: str) -> TargetLanguage | None:
    for info in TARGET_LANGUAGES:
        if info.code == code:
            return info
    return None


def translation_plan(source: str | None, target: str) -> str:
    """What must happen for one run: "whisper", "mt", "pivot", or "none".

    "whisper": the Whisper translate task produces English text directly.
    "mt": the text is already in English, a local pack translates it.
    "pivot": speech is translated to English by Whisper, then by a pack.
    "none": the recognized text is already the target language, keep it.
    """
    if target == PIVOT:
        return "whisper"
    if source == target:
        return "none"
    if source == PIVOT:
        return "mt"
    return "pivot"


def pack_dir(target: str) -> Path:
    return cfg.local_data_dir() / "translation" / f"en-{target}"


def pack_status(target: str) -> str:
    """Return "none" for English, "installed" for a ready pack, "missing" else."""
    if target == PIVOT:
        return "none"
    if target not in PACKS:
        return "missing"
    directory = pack_dir(target)
    if all((directory / name).is_file() for name in REQUIRED_PACK_FILES):
        return "installed"
    return "missing"


def pack_available(target: str) -> bool:
    return pack_status(target) == "installed"


def pack_size_bytes(target: str) -> int:
    """Size of the downloaded pack on disk, for Settings display."""
    info = target_info(target)
    if info is not None and info.size_mb:
        return info.size_mb * 1024 * 1024
    return 0


def _ensure_marian_config(directory: Path) -> None:
    """Write the Marian options into a pack config once, so old packs self-heal.

    Packs built by scripts/build_mt_packs.py already carry these keys, so this
    is a no-op for them: reading a 200-byte config and comparing two values is
    cheaper than tracking versions.
    """
    path = directory / "config.json"
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        log.warning("Cannot read the pack config %s: %s", path, exc)
        return
    if all(config.get(key) == value for key, value in MARIAN_CONFIG.items()):
        return
    config.update(MARIAN_CONFIG)
    path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    log.info("Patched the pack config at %s for Marian", path)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _expected_hash(sums_text: str, filename: str) -> str:
    for line in sums_text.splitlines():
        parts = line.strip().split()
        if len(parts) >= 2 and parts[-1].lstrip("*") == filename:
            # GNU sha256sum writes "hash  name" for text and "hash *name" for
            # binary files; the app builds text zips, so both forms are accepted.
            return parts[0].lstrip("*").lower()
    raise RuntimeError(f"{filename} is missing from {SUMS_NAME}")


def _download(url: str, target: Path, progress, cancel) -> None:
    response = requests.get(
        url,
        headers={"User-Agent": USER_AGENT},
        stream=True,
        timeout=30,
    )
    response.raise_for_status()
    total = int(response.headers.get("Content-Length") or 0)
    done = 0
    with target.open("wb") as handle:
        for chunk in response.iter_content(chunk_size=1024 * 1024):
            if cancel is not None and cancel.is_set():
                raise PackCancelled()
            if not chunk:
                continue
            handle.write(chunk)
            done += len(chunk)
            if progress is not None:
                progress(done, total)


def _safe_names(archive: zipfile.ZipFile) -> list[str]:
    names = []
    for info in archive.infolist():
        name = info.filename.replace("\\", "/")
        if info.is_dir():
            continue
        pure = Path(name)
        if pure.is_absolute() or ".." in pure.parts:
            raise RuntimeError(f"Unsafe path in the translation pack: {name}")
        names.append(name)
    return names


def download_pack(
    target: str,
    progress: ProgressCallback | None = None,
    cancel: threading.Event | None = None,
) -> Path:
    """Download, verify and install one translation pack for a target language."""
    info = target_info(target)
    if info is None or info.asset is None:
        raise RuntimeError(f"{target} needs no translation pack")
    work = Path(tempfile.mkdtemp(prefix="voicetype-mt-"))
    zip_path = work / info.asset
    try:
        log.info("Downloading translation pack %s from %s", target, PACKS_URL)
        _download(f"{PACKS_URL}/{info.asset}", zip_path, progress, cancel)
        sums_response = requests.get(
            f"{PACKS_URL}/{SUMS_NAME}",
            headers={"User-Agent": USER_AGENT},
            timeout=20,
        )
        sums_response.raise_for_status()
        expected = _expected_hash(sums_response.text, info.asset)
        actual = _sha256(zip_path)
        if actual != expected:
            raise RuntimeError(
                f"Translation pack checksum mismatch: expected {expected}, got {actual}"
            )

        destination = pack_dir(target)
        destination.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(zip_path) as archive:
            names = _safe_names(archive)
            # Extract only after the archive is known to be safe.
            for name in names:
                with archive.open(name) as source, (destination / name).open("wb") as out:
                    shutil.copyfileobj(source, out)

        missing = [name for name in REQUIRED_PACK_FILES if not (destination / name).is_file()]
        if missing:
            raise RuntimeError(
                "Translation pack is incomplete: " + ", ".join(missing)
            )
        log.info("Translation pack %s is ready at %s", target, destination)
        return destination
    finally:
        shutil.rmtree(work, ignore_errors=True)


def delete_pack(target: str) -> None:
    directory = pack_dir(target)
    forget_translator(target)
    if directory.is_dir():
        shutil.rmtree(directory, ignore_errors=True)


# --------------------------------------------------------------- engine cache

_lock = threading.Lock()
_translators: dict[str, "PackTranslator"] = {}


def _chunk_words(text: str) -> list[str]:
    """Split text into short chunks, preferring sentence boundaries."""
    sentences = [part for part in re.split(r"(?<=[.!?])\s+", text) if part.strip()]
    chunks: list[str] = []
    for sentence in sentences:
        words = sentence.split()
        if len(words) <= MAX_CHUNK_WORDS:
            chunks.append(sentence.strip())
            continue
        for index in range(0, len(words), MAX_CHUNK_WORDS):
            chunks.append(" ".join(words[index : index + MAX_CHUNK_WORDS]))
    return chunks


class PackTranslator:
    """One OPUS-MT pack in CTranslate2 format, loaded lazily on CPU."""

    def __init__(self, info: TargetLanguage, directory: Path) -> None:
        self.info = info
        self._directory = directory
        self._source = spm.SentencePieceProcessor()
        self._source.load(str(directory / "source.spm"))
        self._target = spm.SentencePieceProcessor()
        self._target.load(str(directory / "target.spm"))
        _ensure_marian_config(directory)
        threads = max(1, min(4, os.cpu_count() or 1))
        self._translator = ctranslate2.Translator(
            str(directory),
            device="cpu",
            compute_type="int8",
            intra_threads=threads,
        )
        log.info(
            "Loaded translation pack %s (%s) from %s",
            info.source_repo,
            info.license,
            directory,
        )

    def translate(self, text: str) -> str:
        if not text.strip():
            return text
        parts = []
        for chunk in _chunk_words(text):
            tokens = self._source.encode(chunk, out_type=str)
            if not tokens:
                continue
            prefix = [self.info.target_prefix] if self.info.target_prefix else None
            # OPUS-MT models are trained on short sentences, so the decode is
            # capped at twice the source length: on a long chunk the model is
            # out of its training range, and a cap keeps a runaway decoder from
            # spinning at 100% CPU for a minute.
            result = self._translator.translate_batch(
                [tokens],
                target_prefix=prefix,
                beam_size=2,
                max_decoding_length=min(4 * len(tokens), 128),
            )
            hypothesis = result[0].hypotheses[0]
            parts.append(self._target.decode(hypothesis))
        return " ".join(parts).strip()

    def unload(self) -> None:
        self._translator.unload_model(to_cpu=True)

    def reload(self) -> None:
        self._translator.load_model()


def get_translator(target: str) -> PackTranslator:
    """Return a ready translator for the target, loading the pack on first use."""
    with _lock:
        cached = _translators.get(target)
        if cached is not None:
            cached.reload()
            return cached
        info = target_info(target)
        if info is None or info.asset is None:
            raise RuntimeError(f"{target} needs no translation pack")
        directory = pack_dir(target)
        if pack_status(target) != "installed":
            raise RuntimeError(
                f"The {info.display_name} translation pack is not downloaded. "
                "Download it in Settings."
            )
        translator = PackTranslator(info, directory)
        _translators[target] = translator
        return translator


def translate_text(target: str, text: str) -> str:
    """Translate English text into the target language with the installed pack.

    Returns the translated text, or an empty string when the pack produced no
    output for this text; the caller keeps the English text in that case.
    """
    if not text.strip():
        return ""
    return get_translator(target).translate(text)


def unload_translators(target: str | None = None) -> None:
    """Move packs to host memory, mirroring the idle unload of Whisper models."""
    with _lock:
        if target is None:
            for translator in _translators.values():
                translator.unload()
            return
        translator = _translators.get(target)
        if translator is not None:
            translator.unload()


def forget_translator(target: str) -> None:
    """Drop a cached translator so the next use loads the pack from disk.

    Deleting a pack must do this too: a cached translator keeps the deleted
    directory open, and a later download replaces the files under it.
    """
    with _lock:
        translator = _translators.pop(target, None)
        if translator is not None:
            translator.unload()
