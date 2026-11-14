from __future__ import annotations

import contextlib
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import numpy as np  # noqa: E402

import voicetype  # noqa: E402,F401
from voicetype import hardware, models  # noqa: E402
from voicetype.config import DEFAULT_HALLUCINATIONS, AppConfig  # noqa: E402
from voicetype.hotkeys import (  # noqa: E402
    canonical_vk,
    format_hotkey,
    parse_hotkey,
    validate_hotkey,
)
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

    def test_translation_is_off_by_default(self) -> None:
        config = AppConfig()
        self.assertFalse(config.translation_enabled)
        self.assertEqual(config.target_language, "en")

    def test_translation_values_are_clamped(self) -> None:
        from voicetype import translation
        from voicetype.config import CONFIG_VERSION, TRANSLATION_TARGETS

        self.assertEqual(CONFIG_VERSION, 2)
        # The config list and the pack registry must stay in sync.
        self.assertEqual(TRANSLATION_TARGETS, translation.TARGETS)
        config = AppConfig.from_dict(
            {"translation_enabled": "yes", "target_language": "de"}
        )
        self.assertTrue(bool(config.translation_enabled))
        self.assertEqual(config.target_language, "de")
        # Polish is not a supported target, so the value falls back to English.
        config = AppConfig.from_dict({"target_language": "pl"})
        self.assertEqual(config.target_language, "en")

    def test_translation_survives_a_config_round_trip(self) -> None:
        config = AppConfig(translation_enabled=True, target_language="en")
        restored = AppConfig.from_dict(config.to_dict())
        self.assertTrue(restored.translation_enabled)
        self.assertEqual(restored.target_language, "en")


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
            validate_hotkey("ctrl+alt")
        with self.assertRaises(ValueError):
            validate_hotkey("win+space")
        with self.assertRaises(ValueError):
            validate_hotkey("ctrl+alt+notakey")

    def test_single_key_hotkeys(self) -> None:
        self.assertEqual(parse_hotkey("F5"), frozenset({"f5"}))
        self.assertEqual(format_hotkey(parse_hotkey("f5")), "F5")
        validate_hotkey("f5")
        validate_hotkey("space")
        validate_hotkey("f24")
        with self.assertRaises(ValueError):
            validate_hotkey("a")
        with self.assertRaises(ValueError):
            validate_hotkey("enter")
        with self.assertRaises(ValueError):
            validate_hotkey("f25")

    def test_canonical_vk(self) -> None:
        self.assertEqual(canonical_vk(0x74), "f5")
        self.assertEqual(canonical_vk(0x70), "f1")
        self.assertEqual(canonical_vk(0x87), "f24")
        self.assertEqual(canonical_vk(0x20), "space")
        self.assertEqual(canonical_vk(0x0D), "enter")
        self.assertEqual(canonical_vk(0x41), "a")
        self.assertEqual(canonical_vk(0x35), "5")
        self.assertEqual(canonical_vk(0x11), "ctrl")
        self.assertEqual(canonical_vk(0x12), "alt")
        self.assertEqual(canonical_vk(0x10), "shift")
        self.assertEqual(canonical_vk(0xA2), "ctrl")
        self.assertEqual(canonical_vk(0xA3), "ctrl")
        self.assertEqual(canonical_vk(0xA4), "alt")
        self.assertEqual(canonical_vk(0xA5), "alt")
        self.assertEqual(canonical_vk(0xA0), "shift")
        self.assertEqual(canonical_vk(0xA1), "shift")


class _FakeKeyEvent:
    def __init__(self, vk: int, flags: int = 0) -> None:
        self.vkCode = vk
        self.flags = flags


class _FakeBackend:
    """Stands in for pynput's listener and raises like suppress_event()."""

    def __init__(self) -> None:
        self.calls = 0

    def suppress_event(self) -> None:
        self.calls += 1
        raise RuntimeError("event suppressed")


class _FakeQtKeyEvent:
    def __init__(self, key: int, text: str = "", native_vk: int = 0) -> None:
        self._key = key
        self._text = text
        self._native_vk = native_vk

    def key(self) -> int:
        return self._key

    def text(self) -> str:
        return self._text

    def nativeVirtualKey(self) -> int:  # noqa: N802
        return self._native_vk


class SettingsKeyNameTests(unittest.TestCase):
    def test_cyrillic_layout_maps_to_physical_key(self) -> None:
        from voicetype.ui.settings import HotkeyEdit

        # Russian layout: the physical Q key produces "й".
        self.assertEqual(
            HotkeyEdit._key_name(_FakeQtKeyEvent(0x419, "й", 0x51)), "q"
        )
        # Russian layout: the physical ; key produces "ж".
        self.assertEqual(
            HotkeyEdit._key_name(_FakeQtKeyEvent(0x416, "ж", 0xBA)), ";"
        )

    def test_special_keys_use_native_codes(self) -> None:
        from voicetype.ui.settings import HotkeyEdit

        self.assertEqual(
            HotkeyEdit._key_name(_FakeQtKeyEvent(0x01000034, "", 0x74)), "f5"
        )
        self.assertEqual(
            HotkeyEdit._key_name(_FakeQtKeyEvent(0x20, " ", 0x20)), "space"
        )

    def test_fallback_when_native_code_is_missing(self) -> None:
        from voicetype.ui.settings import HotkeyEdit

        self.assertEqual(
            HotkeyEdit._key_name(_FakeQtKeyEvent(0x01000034, "")), "f5"
        )


class HotkeyCaptureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from PySide6.QtWidgets import QApplication

        cls._app = QApplication.instance() or QApplication([])

    def test_finish_capture_keeps_visible_combo(self) -> None:
        from voicetype.ui.settings import HotkeyEdit

        edit = HotkeyEdit()
        edit.setText("Alt+Q")
        edit.start_capture()
        edit.setText("Ctrl+Alt+F5")
        edit.finish_capture()
        self.assertFalse(edit.is_capturing())
        self.assertEqual(edit.text(), "Ctrl+Alt+F5")

    def test_finish_capture_restores_previous_combo(self) -> None:
        from voicetype.ui.settings import HotkeyEdit

        edit = HotkeyEdit()
        edit.setText("Alt+Q")
        edit.start_capture()
        edit.finish_capture()
        self.assertFalse(edit.is_capturing())
        self.assertEqual(edit.text(), "Alt+Q")


class HotkeyListenerStartTests(unittest.TestCase):
    @unittest.skipUnless(sys.platform == "win32", "Windows-specific hook option")
    def test_windows_listener_uses_win32_event_filter(self) -> None:
        from voicetype import hotkeys

        created: dict = {}

        class FakeListener:
            def __init__(self, **kwargs) -> None:
                created.update(kwargs)
                self._event_filter = kwargs.get("win32_event_filter")

            def start(self) -> None:
                pass

            def stop(self) -> None:
                pass

        original = hotkeys.keyboard.Listener
        hotkeys.keyboard.Listener = FakeListener
        try:
            listener = hotkeys.HotkeyListener("f5")
            listener.start()
        finally:
            hotkeys.keyboard.Listener = original

        self.assertIn("win32_event_filter", created)
        self.assertNotIn("event_filter", created)
        self.assertEqual(created["win32_event_filter"], listener._event_filter)


class TrayIconTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from PySide6.QtWidgets import QApplication

        cls._app = QApplication.instance() or QApplication([])

    def test_state_icon_uses_application_asset(self) -> None:
        from voicetype.config import assets_dir
        from voicetype.ui import tray

        self.assertTrue((assets_dir() / "app.ico").is_file())
        icon = tray.state_icon("idle")
        self.assertFalse(icon.isNull())
        self.assertFalse(icon.pixmap(32, 32).isNull())


class TrayStatusTextTests(unittest.TestCase):
    """The tray line shows the model, the device and the translation mode."""

    def _status(
        self,
        translate: bool,
        target: str,
        active: str | None,
        suffix: str = "",
        model_name: str = "Small",
    ):
        import types

        from voicetype.app import VoiceTypeApplication

        app = SimpleNamespace(
            config=AppConfig(translation_enabled=translate, target_language=target),
            _active_device=active,
            _selection=hardware.Selection("cpu", "small", "Auto on CPU"),
        )
        # Use the real device label logic, not a copy of it.
        app._device_label = types.MethodType(VoiceTypeApplication._device_label, app)
        return VoiceTypeApplication._status_text(app, model_name, suffix)

    def test_translation_mode_is_shown_in_the_tray(self) -> None:
        self.assertEqual(
            self._status(True, "de", "cuda"),
            "Small · CUDA · translate → DE",
        )

    def test_plain_dictation_does_not_mention_translation(self) -> None:
        self.assertEqual(self._status(False, "de", "cuda"), "Small · CUDA")

    def test_the_device_comes_from_the_selection_when_the_model_is_not_loaded(self) -> None:
        self.assertEqual(self._status(True, "ru", None), "Small · CPU · translate → RU")

    def test_a_suffix_is_appended_after_the_mode(self) -> None:
        self.assertEqual(
            self._status(True, "en", "cpu", "Recording…"),
            "Small · CPU · translate → EN · Recording…",
        )

    def test_an_empty_model_name_keeps_the_device_mode_and_suffix(self) -> None:
        self.assertEqual(
            self._status(True, "de", "cpu", "Loading…", ""),
            "CPU · translate → DE · Loading…",
        )


class InserterClipboardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from PySide6.QtWidgets import QApplication

        cls._app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        from PySide6.QtGui import QGuiApplication

        self._clipboard = QGuiApplication.clipboard()
        self._original = self._clipboard.text()

    def tearDown(self) -> None:
        self._clipboard.setText(self._original)

    def _make_inserter(self, keep_text: bool):
        from voicetype.inserter import Inserter

        inserter = Inserter()
        inserter._modifiers_down = staticmethod(lambda: False)
        inserter._send_ctrl_v = staticmethod(lambda: True)
        inserter.insert("recognized text", keep_text=keep_text)
        inserter._timer.stop()
        return inserter

    def test_keeps_text_in_clipboard_when_enabled(self) -> None:
        inserter = self._make_inserter(keep_text=True)
        inserter._restore_and_finish()
        self.assertEqual(self._clipboard.text(), "recognized text")

    def test_restores_previous_clipboard_when_disabled(self) -> None:
        self._clipboard.setText("previous clipboard")
        inserter = self._make_inserter(keep_text=False)
        inserter._restore_and_finish()
        self.assertEqual(self._clipboard.text(), "previous clipboard")

    def test_keeps_text_when_paste_cannot_be_sent(self) -> None:
        from voicetype.inserter import Inserter

        self._clipboard.setText("previous clipboard")
        inserter = Inserter()
        inserter._modifiers_down = staticmethod(lambda: False)
        inserter._send_ctrl_v = staticmethod(lambda: False)
        errors: list[str] = []
        inserter.error.connect(errors.append)
        inserter.insert("recognized text", keep_text=False)
        inserter._poll_modifiers()
        self.assertEqual(self._clipboard.text(), "recognized text")
        self.assertTrue(errors)
        self.assertIn("clipboard", errors[0])


class HotkeyListenerTests(unittest.TestCase):
    """Exercise the Windows low-level filter without installing a hook."""

    def _make(self, combo: str):
        from voicetype import hotkeys

        listener = hotkeys.HotkeyListener(combo)
        events: list[str] = []
        listener.pressed.connect(lambda: events.append("pressed"))
        listener.released.connect(lambda: events.append("released"))
        return hotkeys, listener, events

    def test_space_is_swallowed_including_auto_repeat(self) -> None:
        hotkeys, listener, events = self._make("space")
        down = hotkeys._WM_KEYDOWN
        up = hotkeys._WM_KEYUP
        self.assertFalse(listener._filter_event(down, _FakeKeyEvent(0x20)))
        self.assertFalse(listener._filter_event(down, _FakeKeyEvent(0x20)))
        self.assertEqual(events, ["pressed"])
        self.assertFalse(listener._filter_event(up, _FakeKeyEvent(0x20)))
        self.assertEqual(events, ["pressed", "released"])

    def test_f5_triggers_and_is_swallowed(self) -> None:
        hotkeys, listener, events = self._make("f5")
        self.assertFalse(
            listener._filter_event(hotkeys._WM_KEYDOWN, _FakeKeyEvent(0x74))
        )
        self.assertFalse(
            listener._filter_event(hotkeys._WM_KEYUP, _FakeKeyEvent(0x74))
        )
        self.assertEqual(events, ["pressed", "released"])

    def test_plain_space_passes_when_modifiers_required(self) -> None:
        hotkeys, listener, events = self._make("ctrl+alt+space")
        self.assertTrue(
            listener._filter_event(hotkeys._WM_KEYDOWN, _FakeKeyEvent(0x20))
        )
        self.assertTrue(
            listener._filter_event(hotkeys._WM_KEYUP, _FakeKeyEvent(0x20))
        )
        self.assertEqual(events, [])

    def test_modifiers_are_tracked_and_pass_through(self) -> None:
        hotkeys, listener, events = self._make("ctrl+alt+space")
        self.assertTrue(
            listener._filter_event(hotkeys._WM_KEYDOWN, _FakeKeyEvent(0x11))
        )
        self.assertTrue(
            listener._filter_event(hotkeys._WM_KEYDOWN, _FakeKeyEvent(0x12))
        )
        self.assertFalse(
            listener._filter_event(hotkeys._WM_KEYDOWN, _FakeKeyEvent(0x20))
        )
        self.assertFalse(
            listener._filter_event(hotkeys._WM_KEYDOWN, _FakeKeyEvent(0x20))
        )
        self.assertEqual(events, ["pressed"])
        self.assertFalse(
            listener._filter_event(hotkeys._WM_KEYUP, _FakeKeyEvent(0x20))
        )
        self.assertEqual(events, ["pressed", "released"])

    def test_injected_events_pass_through(self) -> None:
        hotkeys, listener, events = self._make("space")
        injected = hotkeys._LLKHF_INJECTED
        self.assertTrue(
            listener._filter_event(
                hotkeys._WM_KEYDOWN, _FakeKeyEvent(0x20, flags=injected)
            )
        )
        self.assertEqual(events, [])

    def test_wrapper_asks_pynput_to_suppress(self) -> None:
        hotkeys, listener, events = self._make("space")
        backend = _FakeBackend()
        listener._backend = backend
        with self.assertRaises(RuntimeError):
            listener._event_filter(hotkeys._WM_KEYDOWN, _FakeKeyEvent(0x20))
        self.assertEqual(backend.calls, 1)
        self.assertEqual(events, ["pressed"])
        self.assertTrue(
            listener._event_filter(hotkeys._WM_KEYDOWN, _FakeKeyEvent(0x41))
        )


class RecorderTests(unittest.TestCase):
    def test_stop_flattens_multichannel_blocks(self) -> None:
        from voicetype.recorder import Recorder

        recorder = Recorder()
        recorder._recording = True
        recorder._callback(
            np.zeros((100, 1), dtype=np.float32), 100, None, None
        )
        audio, duration = recorder.stop()
        self.assertEqual(audio.shape, (100,))
        self.assertEqual(audio.dtype, np.float32)
        self.assertAlmostEqual(duration, 100 / 16_000)

    def test_stop_accepts_mono_blocks(self) -> None:
        from voicetype.recorder import Recorder

        recorder = Recorder()
        recorder._recording = True
        recorder._callback(np.zeros(50, dtype=np.float32), 50, None, None)
        audio, _ = recorder.stop()
        self.assertEqual(audio.shape, (50,))


class TranscriberTests(unittest.TestCase):
    def test_transcribe_flattens_audio_for_whisper(self) -> None:
        from voicetype.transcriber import TranscriptionWorker

        class _Segment:
            text = " hello "

        class _Model:
            def __init__(self) -> None:
                self.shapes: list[tuple[int, ...]] = []

            def transcribe(self, audio, **kwargs):
                self.shapes.append(audio.shape)
                return [_Segment()], None

        worker = TranscriptionWorker()
        model = _Model()
        worker._model = model
        worker._model_key = "small"
        worker._device = "cpu"
        worker._configured_model = "small"
        worker._configured_device = "cpu"
        results: list[tuple] = []
        worker.transcription_ready.connect(lambda *args: results.append(args))

        worker.transcribe(np.zeros((160, 1), dtype=np.float32))

        self.assertEqual(model.shapes, [(160,)])
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0][0], "hello")


class TranslationTaskTests(unittest.TestCase):
    """Whisper produces English text, the local pack turns it into the target."""

    def _worker(self, calls: list):
        from voicetype.transcriber import TranscriptionWorker

        class _Segment:
            text = " привет мир "

        class _Model:
            def transcribe(self, audio, **kwargs):
                calls.append(kwargs)
                return [_Segment()], SimpleNamespace(language="ru")

        worker = TranscriptionWorker()
        worker._model = _Model()
        worker._model_key = "small"
        worker._device = "cpu"
        worker._configured_model = "small"
        worker._configured_device = "cpu"
        return worker

    def _run(self, language: str, translate: bool, target: str, installed: bool = True):
        """Run one transcription with a fake Whisper and a fake pack."""
        from voicetype import translation

        calls: list = []
        pack_calls: list = []
        results: list = []
        errors: list = []
        worker = self._worker(calls)
        worker.transcription_ready.connect(lambda *args: results.append(args))
        worker.transcription_error.connect(errors.append)

        def fake_pack(target: str, text: str) -> str:
            pack_calls.append((target, text))
            return f"[{target}]"

        with contextlib.ExitStack() as stack:
            stack.enter_context(
                mock.patch.object(translation, "pack_available", lambda code: installed)
            )
            stack.enter_context(
                mock.patch.object(translation, "translate_text", fake_pack)
            )
            worker.configure("small", "cpu", language, "", [], False, translate, target)
            worker.transcribe(np.zeros(160, dtype=np.float32))
        return calls, pack_calls, results, errors

    def test_english_target_asks_whisper_to_translate(self) -> None:
        calls, pack_calls, results, errors = self._run("ru", True, "en")
        self.assertEqual(calls[0]["task"], "translate")
        self.assertEqual(calls[0]["language"], "ru")
        self.assertEqual(results[0][0], "привет мир")
        self.assertEqual(pack_calls, [])
        self.assertEqual(errors, [])

    def test_plain_recognition_stays_transcribe(self) -> None:
        calls, pack_calls, results, _errors = self._run("ru", False, "en")
        self.assertEqual(calls[0]["task"], "transcribe")
        self.assertEqual(pack_calls, [])
        self.assertEqual(results[0][0], "привет мир")

    def test_pivot_target_asks_whisper_for_english_first(self) -> None:
        calls, pack_calls, results, _errors = self._run("ru", True, "de")
        self.assertEqual(calls[0]["task"], "translate")
        self.assertEqual(calls[0]["language"], "ru")
        self.assertEqual(pack_calls, [("de", "привет мир")])
        self.assertEqual(results[0][0], "[de]")

    def test_english_speech_uses_the_pack_after_recognition(self) -> None:
        calls, pack_calls, results, _errors = self._run("en", True, "ru")
        self.assertEqual(calls[0]["task"], "transcribe")
        self.assertEqual(pack_calls, [("ru", "привет мир")])
        self.assertEqual(results[0][0], "[ru]")

    def test_target_matching_the_language_keeps_the_text(self) -> None:
        calls, pack_calls, results, _errors = self._run("ru", True, "ru")
        self.assertEqual(calls[0]["task"], "transcribe")
        self.assertEqual(pack_calls, [])
        self.assertEqual(results[0][0], "привет мир")

    def test_missing_pack_is_reported_before_whisper_runs(self) -> None:
        calls, pack_calls, results, errors = self._run("ru", True, "de", installed=False)
        self.assertEqual(calls, [])
        self.assertEqual(results, [])
        self.assertIn("German translation pack is not downloaded", errors[0])

    def test_a_runaway_pack_keeps_the_recognized_text(self) -> None:
        from voicetype import translation

        calls: list = []
        results: list = []
        worker = self._worker(calls)
        worker.transcription_ready.connect(lambda *args: results.append(args))
        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.object(translation, "pack_available", lambda code: True))
            stack.enter_context(
                mock.patch.object(translation, "translate_text", lambda target, text: "")
            )
            worker.configure("small", "cpu", "ru", "", [], False, True, "de")
            worker.transcribe(np.zeros(160, dtype=np.float32))
        self.assertEqual(results[0][0], "привет мир")

    def test_pack_failure_is_reported_as_a_recognition_error(self) -> None:
        from voicetype import translation

        def boom(target: str, text: str) -> str:
            raise RuntimeError("the pack is broken")

        calls: list = []
        results: list = []
        errors: list = []
        worker = self._worker(calls)
        worker.transcription_ready.connect(lambda *args: results.append(args))
        worker.transcription_error.connect(errors.append)
        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.object(translation, "pack_available", lambda code: True))
            stack.enter_context(mock.patch.object(translation, "translate_text", boom))
            worker.configure("small", "cpu", "ru", "", [], False, True, "de")
            worker.transcribe(np.zeros(160, dtype=np.float32))
        self.assertEqual(results, [])
        self.assertIn("the pack is broken", errors[0])

    def test_auto_language_keeps_multilingual_detection(self) -> None:
        calls, _pack_calls, _results, _errors = self._run("auto", True, "en")
        self.assertIsNone(calls[0]["language"])
        self.assertTrue(calls[0]["multilingual"])
        self.assertEqual(calls[0]["task"], "translate")


class TranslationPlanTests(unittest.TestCase):
    """Every target needs a different path: Whisper, a pack, both, or neither."""

    def _plan(self, source: str, target: str) -> str:
        from voicetype.translation import translation_plan

        return translation_plan(source, target)

    def test_english_target_is_the_whisper_translate_task(self) -> None:
        self.assertEqual(self._plan("ru", "en"), "whisper")
        self.assertEqual(self._plan("auto", "en"), "whisper")

    def test_english_speech_to_a_pack_target(self) -> None:
        self.assertEqual(self._plan("en", "de"), "mt")

    def test_non_english_pairs_go_through_english(self) -> None:
        self.assertEqual(self._plan("ru", "de"), "pivot")
        self.assertEqual(self._plan("auto", "zh"), "pivot")

    def test_matching_language_and_target_need_no_translation(self) -> None:
        self.assertEqual(self._plan("ru", "ru"), "none")


class TranslationPackTests(unittest.TestCase):
    """The pack registry, integrity checks and the cached translator."""

    def _data_dir(self, root: Path):
        from voicetype import config as cfg

        return mock.patch.object(cfg, "local_data_dir", lambda: root)

    def test_english_needs_no_pack(self) -> None:
        from voicetype import translation

        self.assertEqual(translation.pack_status("en"), "none")
        self.assertIsNone(translation.target_info("en").asset)

    def test_every_target_except_english_has_a_pack(self) -> None:
        from voicetype import translation

        # The project is MIT and ships commercial-friendly packs only, so a
        # non-commercial license (CC-BY-NC, NLLB) can never appear here.
        allowed = ("Apache-2.0", "CC-BY-4.0")
        for info in translation.TARGET_LANGUAGES:
            if info.code == "en":
                continue
            self.assertTrue(info.asset.startswith("voicetype-mt-en-"))
            self.assertTrue(info.source_repo.startswith("Helsinki-NLP/opus-mt-en-"))
            self.assertIn(info.license, allowed)
            self.assertGreater(info.size_mb, 0)
            self.assertIn(info.code, translation.PACKS)

    def test_a_partial_pack_is_not_installed(self) -> None:
        from voicetype import translation

        with tempfile.TemporaryDirectory() as tmp:
            with self._data_dir(Path(tmp)):
                self.assertEqual(translation.pack_status("de"), "missing")
                directory = translation.pack_dir("de")
                directory.mkdir(parents=True)
                for name in translation.REQUIRED_PACK_FILES[:-1]:
                    (directory / name).write_bytes(b"x")
                self.assertEqual(translation.pack_status("de"), "missing")
                self.assertFalse(translation.pack_available("de"))

    def test_a_complete_pack_is_installed(self) -> None:
        from voicetype import translation

        with tempfile.TemporaryDirectory() as tmp:
            with self._data_dir(Path(tmp)):
                directory = translation.pack_dir("zh")
                directory.mkdir(parents=True)
                for name in translation.REQUIRED_PACK_FILES:
                    (directory / name).write_bytes(b"x")
                self.assertEqual(translation.pack_status("zh"), "installed")
                self.assertTrue(translation.pack_available("zh"))

    def test_the_pack_size_is_the_built_pack_size(self) -> None:
        from voicetype import translation

        self.assertEqual(translation.pack_size_bytes("en"), 0)
        self.assertEqual(translation.pack_size_bytes("ru"), 67 * 1024 * 1024)

    def test_deleting_a_pack_unloads_it_first(self) -> None:
        from voicetype import translation

        calls: list = []

        class _FakeTranslator:
            def unload(self) -> None:
                calls.append("unloaded")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self._data_dir(root):
                directory = translation.pack_dir("ru")
                directory.mkdir(parents=True)
                translation._translators["ru"] = _FakeTranslator()
                translation.delete_pack("ru")
                self.assertEqual(translation.pack_status("ru"), "missing")
        self.assertEqual(calls, ["unloaded"])
        self.assertFalse(directory.is_dir())
        translation._translators.clear()

    def test_a_missing_pack_refuses_to_load(self) -> None:
        from voicetype import translation

        with tempfile.TemporaryDirectory() as tmp:
            with self._data_dir(Path(tmp)):
                with self.assertRaises(RuntimeError):
                    translation.get_translator("de")

    def test_a_cached_pack_is_reloaded_on_the_next_run(self) -> None:
        from voicetype import translation

        calls: list = []

        class _FakeTranslator:
            def reload(self) -> None:
                calls.append("reload")

            def unload(self) -> None:
                calls.append("unload")

        translation._translators["ru"] = _FakeTranslator()
        try:
            self.assertIs(translation.get_translator("ru"), translation._translators["ru"])
            self.assertEqual(calls, ["reload"])
            translation.unload_translators()
            self.assertEqual(calls, ["reload", "unload"])
        finally:
            translation._translators.clear()

    def test_long_text_is_split_at_sentence_boundaries(self) -> None:
        from voicetype.translation import _chunk_words, MAX_CHUNK_WORDS

        chunks = _chunk_words("One two three. Four five. Six seven eight.")
        self.assertEqual(chunks, ["One two three.", "Four five.", "Six seven eight."])

        long = " ".join(f"word{n}" for n in range(50))
        chunks = _chunk_words(long)
        self.assertEqual(len(chunks), 2)
        self.assertLessEqual(len(chunks[0].split()), MAX_CHUNK_WORDS)
        self.assertEqual(chunks[0].split()[0], "word0")
        self.assertEqual(chunks[1].split()[0], "word40")

    def test_the_expected_hash_is_read_from_the_sums_file(self) -> None:
        from voicetype.translation import _expected_hash

        # GNU sha256sum: "hash  name" for text files, "hash *name" for binary.
        sums = "aaa  voicetype-mt-en-ru.zip\nbbb *voicetype-mt-en-de.zip\n"
        self.assertEqual(_expected_hash(sums, "voicetype-mt-en-ru.zip"), "aaa")
        self.assertEqual(_expected_hash(sums, "voicetype-mt-en-de.zip"), "bbb")
        with self.assertRaises(RuntimeError):
            _expected_hash(sums, "voicetype-mt-en-pl.zip")

    def test_paths_outside_the_pack_are_rejected(self) -> None:
        import io
        import zipfile

        from voicetype.translation import _safe_names

        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("../evil", b"x")
        with zipfile.ZipFile(buffer) as archive:
            with self.assertRaises(RuntimeError):
                _safe_names(archive)

        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            for name in ("model.bin", "config.json"):
                archive.writestr(name, b"x")
        with zipfile.ZipFile(buffer) as archive:
            self.assertEqual(_safe_names(archive), ["model.bin", "config.json"])

    def test_a_pack_config_is_patched_for_marian_once(self) -> None:
        import json

        from voicetype.translation import _ensure_marian_config, MARIAN_CONFIG

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            path.write_text(json.dumps({"vocab_size": 100}), encoding="utf-8")
            _ensure_marian_config(Path(tmp))
            config = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(config["model_type"], "marian")
            self.assertTrue(config["add_source_eos"])
            self.assertEqual(config["vocab_size"], 100)
            text = path.read_text(encoding="utf-8")
            _ensure_marian_config(Path(tmp))
            self.assertEqual(path.read_text(encoding="utf-8"), text)

    def _pack_zip(self, names: tuple[str, ...]) -> bytes:
        import io
        import zipfile

        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            for name in names:
                archive.writestr(name, b"x")
        return buffer.getvalue()

    def _download_pack(self, blob: bytes, sums_text: str, target: str):
        """Run download_pack with a fake download and a fake sums response.

        The caller keeps `self._data_dir(root)` active so the pack lands in the
        temporary directory and the status check sees the same root.
        """

        def fake_download(url: str, path: Path, progress, cancel) -> None:
            path.write_bytes(blob)

        response = SimpleNamespace(
            raise_for_status=lambda: None,
            text=sums_text,
        )
        import requests

        from voicetype import translation

        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.object(translation, "_download", fake_download))
            stack.enter_context(mock.patch.object(requests, "get", lambda url, **kw: response))
            return translation.download_pack(target)

    def test_download_verifies_the_checksum_and_installs_the_pack(self) -> None:
        import hashlib

        from voicetype import translation

        blob = self._pack_zip(translation.REQUIRED_PACK_FILES)
        digest = hashlib.sha256(blob).hexdigest()
        sums = f"{digest}  voicetype-mt-en-de.zip\n"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self._data_dir(root):
                directory = self._download_pack(blob, sums, "de")
                self.assertEqual(translation.pack_status("de"), "installed")
                self.assertEqual(
                    sorted(path.name for path in directory.iterdir()),
                    sorted(translation.REQUIRED_PACK_FILES),
                )

    def test_a_checksum_mismatch_is_not_installed(self) -> None:
        import hashlib

        from voicetype import translation

        blob = self._pack_zip(translation.REQUIRED_PACK_FILES)
        sums = f"{'0' * 64}  voicetype-mt-en-de.zip\n"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self._data_dir(root):
                with self.assertRaises(RuntimeError):
                    self._download_pack(blob, sums, "de")
                self.assertEqual(translation.pack_status("de"), "missing")
                self.assertEqual(list(root.iterdir()), [])

    def test_an_incomplete_pack_is_not_installed(self) -> None:
        import hashlib

        from voicetype import translation

        blob = self._pack_zip(translation.REQUIRED_PACK_FILES[:-1])
        digest = hashlib.sha256(blob).hexdigest()
        sums = f"{digest}  voicetype-mt-en-de.zip\n"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self._data_dir(root):
                with self.assertRaises(RuntimeError):
                    self._download_pack(blob, sums, "de")
                self.assertEqual(translation.pack_status("de"), "missing")


class TranslationModelSelectionTests(unittest.TestCase):
    """Translation needs a full multilingual model, not the distilled turbo one."""

    _ALL = ["large-v3-turbo", "medium", "small", "base"]

    def _select(self, model_pref: str, available: list[str], translate: bool):
        from voicetype import hardware

        with contextlib.ExitStack() as stack:
            stack.enter_context(
                mock.patch.object(models, "available_models", lambda: list(available))
            )
            stack.enter_context(
                mock.patch.object(hardware, "detect_nvidia_gpus", lambda: [])
            )
            stack.enter_context(mock.patch.object(hardware, "cuda_available", lambda: False))
            stack.enter_context(mock.patch.object(hardware, "physical_cores", lambda: 8))
            return hardware.resolve_selection(model_pref, "auto", translate)

    def test_turbo_is_replaced_by_a_translation_model(self) -> None:
        selection = self._select("large-v3-turbo", self._ALL, True)
        self.assertEqual(selection.model, "medium")
        self.assertIn("does not translate", selection.reason)

    def test_auto_prefers_the_best_available_translation_model(self) -> None:
        selection = self._select("auto", ["large-v3-turbo", "small", "base"], True)
        self.assertEqual(selection.model, "small")

    def test_translation_respects_an_explicit_supported_model(self) -> None:
        selection = self._select("base", self._ALL, True)
        self.assertEqual(selection.model, "base")

    def test_plain_recognition_keeps_the_fast_model(self) -> None:
        selection = self._select("large-v3-turbo", self._ALL, False)
        self.assertEqual(selection.model, "large-v3-turbo")

    def test_translation_without_a_multilingual_model_is_reported(self) -> None:
        from voicetype import hardware

        with self.assertRaises(hardware.NoModelsAvailable):
            self._select("auto", ["large-v3-turbo"], True)

    def test_translation_models_are_the_full_multilingual_ones(self) -> None:
        self.assertEqual(models.TRANSLATION_MODEL_ORDER, ("medium", "small", "base"))


class CleanTextTests(unittest.TestCase):
    def test_whitespace(self) -> None:
        self.assertEqual(clean_text("  привет   мир \n", []), "привет мир")

    def test_hallucinations(self) -> None:
        phrases = list(DEFAULT_HALLUCINATIONS)
        self.assertEqual(clean_text("Продолжение следует...", phrases), "")
        self.assertEqual(clean_text("Спасибо за просмотр!", phrases), "")
        self.assertEqual(clean_text("Продолжение следует", phrases), "")

    def test_all_default_hallucinations_are_dropped(self) -> None:
        phrases = list(DEFAULT_HALLUCINATIONS)
        for phrase in phrases:
            with self.subTest(phrase=phrase):
                self.assertEqual(clean_text(phrase, phrases), "")

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

    def test_select_latest_release_skips_pack_tags(self) -> None:
        # Translation packs publish mt-packs-v1, which is not an app release.
        releases = [
            {"tag_name": "mt-packs-v1", "draft": False, "prerelease": False},
            {"tag_name": "cuda-pack-v1", "draft": False, "prerelease": False},
            {"tag_name": "v0.2.1", "draft": False, "prerelease": False},
        ]
        selected = select_latest_release(releases)
        self.assertEqual(selected["tag_name"], "v0.2.1")

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
            self.assertIn("tokenizer.json", dict(info.fallback_files))


class ModelCompletenessTests(unittest.TestCase):
    @staticmethod
    def _write(directory: Path, *names: str) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        for name in names:
            (directory / name).write_bytes(b"test")

    def _patch_dirs(self, local: Path, bundled: Path):
        return (
            mock.patch.object(models.cfg, "models_dir", return_value=local),
            mock.patch.object(models.cfg, "bundled_models_dir", return_value=bundled),
        )

    def test_partial_download_is_not_installed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            local = root / "local"
            bundled = root / "bundled"
            self._write(local / "small", "model.bin", "config.json")
            local_patch, bundled_patch = self._patch_dirs(local, bundled)
            with local_patch, bundled_patch:
                self.assertIsNone(models.model_path("small"))
                self.assertEqual(models.model_status("small"), "missing")
                self.assertFalse(models.is_available("small"))

    def test_complete_download_is_installed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            local = root / "local"
            bundled = root / "bundled"
            self._write(local / "small", *models.REQUIRED_MODEL_FILES)
            local_patch, bundled_patch = self._patch_dirs(local, bundled)
            with local_patch, bundled_patch:
                self.assertEqual(models.model_path("small"), local / "small")
                self.assertEqual(models.model_status("small"), "downloaded")
                self.assertTrue(models.is_available("small"))

    def test_bundled_model_takes_precedence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            local = root / "local"
            bundled = root / "bundled"
            self._write(local / "small", *models.REQUIRED_MODEL_FILES)
            self._write(bundled / "small", *models.REQUIRED_MODEL_FILES)
            local_patch, bundled_patch = self._patch_dirs(local, bundled)
            with local_patch, bundled_patch:
                self.assertEqual(models.model_path("small"), bundled / "small")
                self.assertEqual(models.model_status("small"), "bundled")


class ModelRemoteFilesTests(unittest.TestCase):
    def test_file_list_is_requested_for_pinned_revision(self) -> None:
        info = models.MODELS["small"]
        response = mock.Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "siblings": [{"rfilename": "model.bin", "size": 123}]
        }
        with mock.patch.object(models.requests, "get", return_value=response) as get:
            files, total = models._remote_files(info)
        url = get.call_args.args[0]
        self.assertIn(f"revision={info.revision}", url)
        self.assertEqual(files, [("model.bin", 123)])
        self.assertEqual(total, 123)


class CudaRetryTests(unittest.TestCase):
    def test_explicit_cuda_retry_clears_previous_failure(self) -> None:
        from voicetype.transcriber import TranscriptionWorker

        worker = TranscriptionWorker()
        worker._cuda_failure = "CUDA out of memory"
        self.assertEqual(worker._effective_device("cuda"), "cpu")
        loaded: list[tuple[str, str]] = []
        worker._load = lambda model_key, device: loaded.append((model_key, device))

        worker.configure("small", "cuda", "ru", "", [], True)

        self.assertIsNone(worker._cuda_failure)
        self.assertEqual(loaded, [("small", "cuda")])

    def test_configure_without_retry_keeps_failure(self) -> None:
        from voicetype.transcriber import TranscriptionWorker

        worker = TranscriptionWorker()
        worker._cuda_failure = "CUDA out of memory"
        worker._load = lambda model_key, device: None

        worker.configure("small", "cuda", "ru", "", [], False)

        self.assertEqual(worker._cuda_failure, "CUDA out of memory")


class OverlayFadeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from PySide6.QtWidgets import QApplication

        cls._app = QApplication.instance() or QApplication([])

    def test_cancel_pending_hide(self) -> None:
        from voicetype.ui.overlay import OverlayWindow

        overlay = OverlayWindow()
        overlay._hide_after_fade = True
        overlay._cancel_pending_hide()
        self.assertFalse(overlay._hide_after_fade)
        self.assertAlmostEqual(overlay.windowOpacity(), 1.0)
        overlay.deleteLater()


class RecognitionTestValidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from PySide6.QtWidgets import QApplication

        cls._app = QApplication.instance() or QApplication([])

    def test_missing_microphone_is_rejected(self) -> None:
        from voicetype.testbench import RecognitionTester

        tester = RecognitionTester()
        errors: list[str] = []
        tester.error.connect(errors.append)
        try:
            started = tester.start(
                "small",
                "cpu",
                "ru",
                "",
                "VoiceType microphone that does not exist",
                [],
            )
        finally:
            tester.shutdown()
        self.assertFalse(started)
        self.assertEqual(tester.state, "error")
        self.assertTrue(errors)


class HelpButtonTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from PySide6.QtWidgets import QApplication

        cls._app = QApplication.instance() or QApplication([])

    def test_the_help_button_opens_the_repo_issues_page(self) -> None:
        from PySide6.QtWidgets import QPushButton

        from voicetype import cuda, hardware, models
        from voicetype.recorder import Recorder
        from voicetype.ui import settings

        self.assertEqual(
            settings.ISSUES_URL, "https://github.com/flynet/speech_texter/issues"
        )

        sys.path.insert(0, str(ROOT / "scripts"))
        from capture_screenshots import DemoApplication

        opened: list[str] = []
        fake_desktop = SimpleNamespace(
            openUrl=lambda url: opened.append(url.toString()) or True
        )
        with mock.patch.object(models, "is_available", lambda key: key == "small"), \
            mock.patch.object(
                models,
                "model_status",
                lambda key: "bundled" if key == "small" else "missing",
            ), \
            mock.patch.object(hardware, "detect_nvidia_gpus", lambda: []), \
            mock.patch.object(hardware, "cuda_available", lambda: False), \
            mock.patch.object(cuda, "cuda_pack_installed", lambda: False), \
            mock.patch.object(
                Recorder,
                "input_devices",
                staticmethod(lambda: [(0, "Microphone (USB Audio Device)")]),
            ), \
            mock.patch.object(settings, "QDesktopServices", fake_desktop):
            window = settings.SettingsWindow(DemoApplication())
            buttons = [b for b in window.findChildren(QPushButton) if b.text() == "Help"]
            self.assertEqual(len(buttons), 1)
            button = buttons[0]
            self.assertFalse(button.icon().isNull())
            self.assertIn("GitHub issues", button.toolTip())
            button.click()
            window.shutdown()
            window.deleteLater()
        self.assertEqual(opened, [settings.ISSUES_URL])

    def test_the_link_is_shown_when_the_browser_cannot_be_opened(self) -> None:
        from voicetype.ui import settings

        messages: list[str] = []
        fake_box = SimpleNamespace(
            warning=lambda parent, title, text: messages.append(text)
        )
        fake_desktop = SimpleNamespace(openUrl=lambda url: False)
        with mock.patch.object(settings, "QDesktopServices", fake_desktop), mock.patch.object(
            settings, "QMessageBox", fake_box
        ):
            settings.SettingsWindow._open_issues(object())
        self.assertEqual(len(messages), 1)
        self.assertIn(settings.ISSUES_URL, messages[0])


class SettingsTranslationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from PySide6.QtWidgets import QApplication

        cls._qt_app = QApplication.instance() or QApplication([])

    def _open_window(self):
        from voicetype import cuda, hardware, models
        from voicetype.recorder import Recorder
        from voicetype.ui import settings

        sys.path.insert(0, str(ROOT / "scripts"))
        from capture_screenshots import DemoApplication

        stack = contextlib.ExitStack()
        stack.enter_context(
            mock.patch.object(models, "is_available", lambda key: key == "small")
        )
        stack.enter_context(
            mock.patch.object(
                models,
                "model_status",
                lambda key: "bundled" if key == "small" else "missing",
            )
        )
        stack.enter_context(mock.patch.object(hardware, "detect_nvidia_gpus", lambda: []))
        stack.enter_context(mock.patch.object(hardware, "cuda_available", lambda: False))
        stack.enter_context(mock.patch.object(cuda, "cuda_pack_installed", lambda: False))
        stack.enter_context(
            mock.patch.object(
                Recorder,
                "input_devices",
                staticmethod(lambda: [(0, "Microphone (USB Audio Device)")]),
            )
        )
        app = DemoApplication()
        window = settings.SettingsWindow(app)
        return stack, app, window

    def _close_window(self, stack, window) -> None:
        window.shutdown()
        window.deleteLater()
        stack.close()

    def test_translation_group_is_separate_from_dictation(self) -> None:
        from PySide6.QtWidgets import QGroupBox

        stack, app, window = self._open_window()
        try:
            titles = [group.title() for group in window.findChildren(QGroupBox)]
            self.assertIn("Dictation", titles)
            self.assertIn("Translation", titles)
            self.assertLess(titles.index("Speech model"), titles.index("Translation"))
        finally:
            self._close_window(stack, window)

    def test_input_language_and_translation_target_are_independent(self) -> None:
        from voicetype.config import LANGUAGES, TRANSLATION_TARGETS

        stack, app, window = self._open_window()
        try:
            window._language_combo.setCurrentIndex(
                window._language_combo.findData("ru")
            )
            self.assertEqual(window._language_combo.currentData(), "ru")
            self.assertEqual(window._target_combo.currentData(), "en")
            languages = [
                window._language_combo.itemData(index)
                for index in range(window._language_combo.count())
            ]
            targets = [
                window._target_combo.itemData(index)
                for index in range(window._target_combo.count())
            ]
            self.assertEqual(languages, list(LANGUAGES))
            self.assertEqual(targets, list(TRANSLATION_TARGETS))
            self.assertNotEqual(languages, targets)
        finally:
            self._close_window(stack, window)

    def test_the_target_is_enabled_only_when_translation_is_on(self) -> None:
        stack, app, window = self._open_window()
        try:
            window._translate_check.setChecked(True)
            self.assertTrue(window._target_combo.isEnabled())
            window._translate_check.setChecked(False)
            self.assertFalse(window._target_combo.isEnabled())
        finally:
            self._close_window(stack, window)

    def test_apply_saves_translation_settings(self) -> None:
        captured: list[dict] = []
        stack, app, window = self._open_window()
        try:
            app.apply_settings = lambda values: captured.append(values) or True
            window._language_combo.setCurrentIndex(
                window._language_combo.findData("ru")
            )
            window._translate_check.setChecked(True)
            window.apply()
            self.assertEqual(len(captured), 1)
            self.assertEqual(captured[0]["language"], "ru")
            self.assertTrue(captured[0]["translation_enabled"])
            self.assertEqual(captured[0]["target_language"], "en")
        finally:
            self._close_window(stack, window)


    def test_the_hint_explains_the_translation_model(self) -> None:
        from PySide6.QtWidgets import QGroupBox, QLabel

        stack, app, window = self._open_window()
        try:
            group = next(
                group for group in window.findChildren(QGroupBox)
                if group.title() == "Translation"
            )
            texts = [label.text() for label in group.findChildren(QLabel)]
            self.assertTrue(any("does not translate" in text for text in texts))
        finally:
            self._close_window(stack, window)

    def test_pack_rows_cover_every_target_except_english(self) -> None:
        from voicetype import translation

        stack, app, window = self._open_window()
        try:
            codes = list(window._pack_rows)
            self.assertEqual(
                codes, [info.code for info in translation.TARGET_LANGUAGES if info.asset]
            )
            self.assertNotIn("en", codes)
        finally:
            self._close_window(stack, window)

    def test_the_pack_manager_shows_the_download_state(self) -> None:
        from voicetype import translation

        stack, app, window = self._open_window()
        try:
            with mock.patch.object(
                translation,
                "pack_status",
                lambda code: "installed" if code == "ru" else "missing",
            ):
                window.refresh()
            self.assertEqual(window._pack_rows["ru"]._status.text(), "Installed")
            self.assertFalse(window._pack_rows["ru"]._button.isEnabled())
            self.assertIn("Not downloaded", window._pack_rows["de"]._status.text())
            self.assertTrue(window._pack_rows["de"]._button.isEnabled())
        finally:
            self._close_window(stack, window)

    def test_pack_buttons_ask_the_app_to_download_and_remove(self) -> None:
        from voicetype import translation

        requests: list[tuple[str, str]] = []
        stack, app, window = self._open_window()
        app.request_pack_download = lambda target: requests.append(("download", target))
        app.request_pack_delete = lambda target: requests.append(("remove", target))
        try:
            with mock.patch.object(
                translation,
                "pack_status",
                lambda code: "installed" if code == "ru" else "missing",
            ):
                window.refresh()
            installed = window._pack_rows["ru"]
            missing = window._pack_rows["de"]
            self.assertFalse(installed._remove_button.isHidden())
            self.assertTrue(missing._remove_button.isHidden())
            missing._button.click()
            installed._remove_button.click()
            self.assertEqual(
                requests, [("download", "de"), ("remove", "ru")]
            )
        finally:
            self._close_window(stack, window)


class TestBenchTranslationMetaTests(unittest.TestCase):
    """The test bench reports the language it actually translated to."""

    @classmethod
    def setUpClass(cls) -> None:
        from PySide6.QtWidgets import QApplication

        cls._app = QApplication.instance() or QApplication([])

    def _meta(self, translate: bool, target: str) -> str:
        from voicetype.testbench import RecognitionTester

        tester = RecognitionTester()
        tester._model_key = "small"
        tester._device = "cpu"
        tester._microphone = "Microphone (USB Audio Device)"
        tester._translate = translate
        tester._target_language = target
        try:
            return tester._result_meta(2.0, 0.5)
        finally:
            tester.shutdown()

    def test_the_result_names_the_translation_target(self) -> None:
        self.assertIn("Small · CPU · Microphone (USB Audio Device)", self._meta(True, "de"))
        self.assertIn("translated to German", self._meta(True, "de"))

    def test_plain_recognition_reports_no_translation(self) -> None:
        self.assertNotIn("translated", self._meta(False, "de"))


if __name__ == "__main__":
    unittest.main()
