from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import numpy as np  # noqa: E402

import voicetype  # noqa: E402,F401
from voicetype import models  # noqa: E402
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


if __name__ == "__main__":
    unittest.main()
