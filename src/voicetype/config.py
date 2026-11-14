from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

APP_NAME = "VoiceType"
CONFIG_VERSION = 2

# A download the user stopped is not a failure, and the message says so. Handlers
# compare against this exact string to keep the tray from reporting a cancellation
# as a download failure.
CANCELLED_MESSAGE = "Download cancelled."

DEFAULT_HALLUCINATIONS = [
    "Продолжение следует...",
    "Продолжение следует",
    "Субтитры сделал DimaTorzok",
    "Субтитры создавал DimaTorzok",
    "Субтитры делал DimaTorzok",
    "Спасибо за просмотр!",
    "Спасибо за просмотр",
    "Thank you for watching!",
    "Thank you for watching",
    "Subtitles by",
    "Amara.org",
    "Продолжение в следующей серии",
]

OVERLAY_POSITIONS = ("bottom-center", "bottom-left", "bottom-right", "top-center")
HOTKEY_MODES = ("hold", "toggle")
LANGUAGES = ("ru", "en", "auto")
DEVICES = ("auto", "cuda", "cpu")
# Translation targets. Whisper translates speech into English only, so every
# other language runs through English: speech -> English text -> target text,
# with a local OPUS-MT pack downloaded on demand. The list is a literal so
# config stays importable without ctranslate2, and tests assert it matches
# translation.TARGETS.
TRANSLATION_TARGETS = ("en", "ru", "de", "fr", "es", "it", "nl", "uk", "cs", "sv", "zh")
DEFAULT_TARGET_LANGUAGE = "en"


def app_dir() -> Path:
    """Directory of the executable (frozen) or repository root (source)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def resource_dir() -> Path:
    """Directory where PyInstaller unpacks bundled data files."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", app_dir()))
    return app_dir()


def assets_dir() -> Path:
    return resource_dir() / "assets"


def user_data_dir() -> Path:
    base = os.environ.get("APPDATA")
    return Path(base) / APP_NAME if base else Path.home() / "AppData" / "Roaming" / APP_NAME


def local_data_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    return Path(base) / APP_NAME if base else Path.home() / "AppData" / "Local" / APP_NAME


def config_path() -> Path:
    return user_data_dir() / "config.json"


def bundled_models_dir() -> Path:
    return app_dir() / "models"


def models_dir() -> Path:
    return local_data_dir() / "models"


def logs_dir() -> Path:
    return local_data_dir() / "logs"


def cuda_dir() -> Path:
    return local_data_dir() / "cuda"


@dataclass
class AppConfig:
    version: int = CONFIG_VERSION
    hotkey: str = "ctrl+alt+space"
    hotkey_mode: str = "hold"
    language: str = "ru"
    translation_enabled: bool = False
    target_language: str = DEFAULT_TARGET_LANGUAGE
    model: str = "auto"
    device: str = "auto"
    microphone: str | None = None
    autostart: bool = False
    sounds: bool = True
    max_record_seconds: int = 600
    min_record_seconds: float = 0.3
    idle_unload_minutes: int = 0
    initial_prompt: str = ""
    overlay_position: str = "bottom-center"
    keep_text_in_clipboard: bool = True
    hallucination_phrases: list[str] = field(
        default_factory=lambda: list(DEFAULT_HALLUCINATIONS)
    )
    cuda_prompt_shown: bool = False
    last_update_check: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {f.name: getattr(self, f.name) for f in fields(self)}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AppConfig":
        known = {f.name for f in fields(cls)}
        values = {k: v for k, v in data.items() if k in known}
        cfg = cls(**values)
        cfg.normalize()
        return cfg

    def normalize(self) -> None:
        if self.hotkey_mode not in HOTKEY_MODES:
            self.hotkey_mode = "hold"
        if self.language not in LANGUAGES:
            self.language = "ru"
        if self.device not in DEVICES:
            self.device = "auto"
        self.translation_enabled = bool(self.translation_enabled)
        if self.target_language not in TRANSLATION_TARGETS:
            self.target_language = DEFAULT_TARGET_LANGUAGE
        if self.overlay_position not in OVERLAY_POSITIONS:
            self.overlay_position = "bottom-center"
        self.max_record_seconds = max(5, min(3600, int(self.max_record_seconds)))
        self.min_record_seconds = max(0.0, min(2.0, float(self.min_record_seconds)))
        self.idle_unload_minutes = max(0, min(24 * 60, int(self.idle_unload_minutes)))
        if not isinstance(self.hallucination_phrases, list):
            self.hallucination_phrases = list(DEFAULT_HALLUCINATIONS)
        if not self.hotkey:
            self.hotkey = "ctrl+alt+space"
        if not isinstance(self.initial_prompt, str):
            self.initial_prompt = ""
        self.keep_text_in_clipboard = bool(self.keep_text_in_clipboard)

    @classmethod
    def load(cls) -> "AppConfig":
        path = config_path()
        if not path.is_file():
            cfg = cls()
            cfg.save()
            return cfg
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("config root is not an object")
            return cls.from_dict(data)
        except Exception:
            # Keep a copy of the broken file for diagnostics, then start fresh.
            try:
                path.replace(path.with_suffix(".json.broken"))
            except OSError:
                pass
            cfg = cls()
            cfg.save()
            return cfg

    def save(self) -> None:
        path = config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(self.to_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        os.replace(tmp, path)
