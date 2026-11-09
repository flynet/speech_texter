from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import voicetype  # noqa: E402,F401
from voicetype import models  # noqa: E402
from voicetype.config import AppConfig  # noqa: E402
from voicetype.hotkeys import format_hotkey, parse_hotkey, validate_hotkey  # noqa: E402
from voicetype.transcriber import clean_text  # noqa: E402
from voicetype.updater import is_newer, select_latest_release  # noqa: E402


class ConfigTests(unittest.TestCase):
    def test_normalize_invalid_values(self) -> None:
        config = AppConfig.from_dict(
            {
                "hotkey_mode": "weird",
                "language": "de",
                "device": "tpu",
                "overlay_position": "middle",
                "max_record_seconds": -5,
                "min_record_seconds": 99,
                "idle_unload_minutes": -3,
                "hallucination_phrases": "not-a-list",
                "hotkey": "",
            }
        )
        self.assertEqual(config.hotkey_mode, "hold")
        self.assertEqual(config.language, "ru")
        self.assertEqual(config.device, "auto")
        self.assertEqual(config.overlay_position, "bottom-center")
        self.assertEqual(config.max_record_seconds, 5)
        self.assertEqual(config.min_record_seconds, 2.0)
        self.assertEqual(config.idle_unload_minutes, 0)
        self.assertEqual(config.hotkey, "ctrl+alt+space")
        self.assertIsInstance(config.hallucination_phrases, list)


class HotkeyTests(unittest.TestCase):
    def test_parse_and_format(self) -> None:
        keys = parse_hotkey("CTRL + alt + space")
        self.assertEqual(keys, frozenset({"ctrl", "alt", "space"}))
        self.assertEqual(format_hotkey(keys), "Ctrl+Alt+Space")

    def test_function_key(self) -> None:
        self.assertEqual(format_hotkey(parse_hotkey("shift+f12")), "Shift+F12")

    def test_validation(self) -> None:
        validate_hotkey("ctrl+alt+space")
        with self.assertRaises(ValueError):
            validate_hotkey("space")
        with self.assertRaises(ValueError):
            validate_hotkey("ctrl+alt")
        with self.assertRaises(ValueError):
            validate_hotkey("win+space")
        with self.assertRaises(ValueError):
            validate_hotkey("ctrl+alt+notakey")


class CleanTextTests(unittest.TestCase):
    def test_whitespace(self) -> None:
        self.assertEqual(clean_text("  привет   мир \n", []), "привет мир")

    def test_hallucinations(self) -> None:
        phrases = ["Продолжение следует...", "Спасибо за просмотр!"]
        self.assertEqual(clean_text("Продолжение следует...", phrases), "")
        self.assertEqual(clean_text("Спасибо за просмотр!", phrases), "")
        self.assertEqual(clean_text("Продолжение следует", phrases), "")

    def test_punctuation_only(self) -> None:
        self.assertEqual(clean_text("...", []), "")

    def test_regular_text(self) -> None:
        self.assertEqual(clean_text("  Hello, world!  ", []), "Hello, world!")


class VersionTests(unittest.TestCase):
    def test_newer(self) -> None:
        self.assertTrue(is_newer("v0.2.0", "0.1.0"))
        self.assertFalse(is_newer("v0.1.0", "0.1.0"))
        self.assertFalse(is_newer("v0.1.0-beta.1", "0.1.0"))
        self.assertTrue(is_newer("v0.1.0", "0.1.0-beta.1"))
        self.assertTrue(is_newer("1.0.0", "0.9.9"))

    def test_select_latest_release_skips_cuda_pack(self) -> None:
        releases = [
            {"tag_name": "cuda-pack-v1", "draft": False, "prerelease": False},
            {"tag_name": "v0.1.0", "draft": False, "prerelease": False},
            {"tag_name": "v0.2.0-beta.1", "draft": False, "prerelease": True},
            {"tag_name": "v0.0.1", "draft": True, "prerelease": False},
        ]
        selected = select_latest_release(releases)
        self.assertIsNotNone(selected)
        self.assertEqual(selected["tag_name"], "v0.1.0")

    def test_select_latest_release_highest_version(self) -> None:
        releases = [
            {"tag_name": "v0.1.0", "prerelease": False},
            {"tag_name": "v0.10.0", "prerelease": False},
            {"tag_name": "v0.9.9", "prerelease": False},
        ]
        selected = select_latest_release(releases)
        self.assertEqual(selected["tag_name"], "v0.10.0")
        self.assertIsNone(select_latest_release(None))


class ModelRegistryTests(unittest.TestCase):
    def test_known_models(self) -> None:
        self.assertEqual(
            models.MODEL_ORDER, ("large-v3-turbo", "medium", "small", "base")
        )
        for key in models.MODEL_ORDER:
            info = models.MODELS[key]
            self.assertEqual(info.key, key)
            self.assertEqual(len(info.revision), 40)
            self.assertIn("model.bin", dict(info.fallback_files))
            self.assertIn("config.json", dict(info.fallback_files))


if __name__ == "__main__":
    unittest.main()
