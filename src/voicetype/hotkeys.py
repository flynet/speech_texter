from __future__ import annotations

import logging
import threading

from pynput import keyboard
from PySide6.QtCore import QObject, Signal

log = logging.getLogger(__name__)

MODIFIER_ORDER = ("ctrl", "alt", "shift", "win")
MODIFIERS = set(MODIFIER_ORDER)

_ALIASES = {
    "control": "ctrl",
    "ctl": "ctrl",
    "option": "alt",
    "opt": "alt",
    "windows": "win",
    "meta": "win",
    "super": "win",
    "cmd": "win",
    "command": "win",
    "return": "enter",
    "escape": "esc",
    "del": "delete",
    "ins": "insert",
    "pgup": "pageup",
    "pgdn": "pagedown",
    "print": "printscreen",
    "prtsc": "printscreen",
}

_SPECIAL_NAMES = {
    "space": "space",
    "enter": "enter",
    "tab": "tab",
    "esc": "esc",
    "backspace": "backspace",
    "delete": "delete",
    "insert": "insert",
    "home": "home",
    "end": "end",
    "page_up": "pageup",
    "page_down": "pagedown",
    "up": "up",
    "down": "down",
    "left": "left",
    "right": "right",
    "pause": "pause",
    "print_screen": "printscreen",
    "caps_lock": "capslock",
    "num_lock": "numlock",
    "scroll_lock": "scrolllock",
    "menu": "menu",
    "media_play_pause": "mediaplaypause",
    "media_volume_mute": "mediavolumemute",
    "media_volume_down": "mediavolumedown",
    "media_volume_up": "mediavolumeup",
    "media_previous": "mediaprevious",
    "media_next": "medianext",
}

_DISPLAY_NAMES = {
    "ctrl": "Ctrl",
    "alt": "Alt",
    "shift": "Shift",
    "win": "Win",
    "space": "Space",
    "enter": "Enter",
    "tab": "Tab",
    "esc": "Esc",
    "backspace": "Backspace",
    "delete": "Delete",
    "insert": "Insert",
    "home": "Home",
    "end": "End",
    "pageup": "PageUp",
    "pagedown": "PageDown",
    "up": "Up",
    "down": "Down",
    "left": "Left",
    "right": "Right",
    "pause": "Pause",
    "printscreen": "PrintScreen",
    "capslock": "CapsLock",
    "numlock": "NumLock",
    "scrolllock": "ScrollLock",
    "menu": "Menu",
    "mediaplaypause": "MediaPlayPause",
    "mediavolumemute": "MediaVolumeMute",
    "mediavolumedown": "MediaVolumeDown",
    "mediavolumeup": "MediaVolumeUp",
    "mediaprevious": "MediaPrevious",
    "medianext": "MediaNext",
}

_MODIFIER_KEYS: dict[object, str] = {}
for _name, _canonical in (
    ("ctrl", "ctrl"),
    ("ctrl_l", "ctrl"),
    ("ctrl_r", "ctrl"),
    ("alt", "alt"),
    ("alt_l", "alt"),
    ("alt_r", "alt"),
    ("alt_gr", "alt"),
    ("shift", "shift"),
    ("shift_l", "shift"),
    ("shift_r", "shift"),
    ("cmd", "win"),
    ("cmd_l", "win"),
    ("cmd_r", "win"),
):
    _key = getattr(keyboard.Key, _name, None)
    if _key is not None:
        _MODIFIER_KEYS[_key] = _canonical

_SPECIAL_KEYS: dict[object, str] = {}
for _name, _canonical in _SPECIAL_NAMES.items():
    _key = getattr(keyboard.Key, _name, None)
    if _key is not None:
        _SPECIAL_KEYS[_key] = _canonical
for _index in range(1, 25):
    _key = getattr(keyboard.Key, f"f{_index}", None)
    if _key is not None:
        _SPECIAL_KEYS[_key] = f"f{_index}"


def _canonical_token(token: str) -> str:
    token = token.strip().lower()
    token = _ALIASES.get(token, token)
    if token in MODIFIERS:
        return token
    if token in _SPECIAL_NAMES.values() or token in {"space", "enter"}:
        return token
    if token.startswith("f") and token[1:].isdigit():
        number = int(token[1:])
        if 1 <= number <= 24:
            return f"f{number}"
    if len(token) == 1:
        return token
    raise ValueError(f"Unknown key: {token}")


def parse_hotkey(combo: str) -> frozenset[str]:
    tokens = [part.strip().lower() for part in combo.replace(" ", "").split("+") if part.strip()]
    if not tokens:
        raise ValueError("Hotkey is empty")
    return frozenset(_canonical_token(token) for token in tokens)


def validate_hotkey(combo: str) -> frozenset[str]:
    keys = parse_hotkey(combo)
    if "win" in keys:
        raise ValueError("The Windows key cannot be used in a global hotkey.")
    if not keys & MODIFIERS:
        raise ValueError("Add at least one modifier (Ctrl, Alt, or Shift).")
    if not keys - MODIFIERS:
        raise ValueError("Add a non-modifier key to the hotkey.")
    return keys


def format_hotkey(keys: frozenset[str] | set[str]) -> str:
    ordered: list[str] = [name for name in MODIFIER_ORDER if name in keys]
    ordered.extend(sorted(key for key in keys if key not in MODIFIERS))
    parts: list[str] = []
    for key in ordered:
        display = _DISPLAY_NAMES.get(key)
        if display is None:
            display = key.upper() if len(key) == 1 else key.title()
        parts.append(display)
    return "+".join(parts)


def canonical_key(key: object) -> str | None:
    if key in _MODIFIER_KEYS:
        return _MODIFIER_KEYS[key]
    if key in _SPECIAL_KEYS:
        return _SPECIAL_KEYS[key]
    if isinstance(key, keyboard.KeyCode):
        if key.char:
            return key.char.lower()
        if key.vk is not None and 32 <= int(key.vk) <= 126:
            return chr(int(key.vk)).lower()
    return None


class HotkeyListener(QObject):
    pressed = Signal()
    released = Signal()
    error = Signal(str)

    def __init__(self, combo: str, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._combo = validate_hotkey(combo)
        self._listener: keyboard.Listener | None = None
        self._lock = threading.Lock()
        self._down: set[str] = set()
        self._combo_down = False
        self._paused = False

    def start(self) -> None:
        if self._listener is not None:
            return
        try:
            self._listener = keyboard.Listener(
                on_press=self._on_press,
                on_release=self._on_release,
            )
            self._listener.daemon = True
            self._listener.start()
            log.info("Global hotkey listener started")
        except Exception as exc:
            log.exception("Could not start the global hotkey listener")
            self._listener = None
            self.error.emit(f"Could not start the global hotkey listener: {exc}")

    def stop(self) -> None:
        listener = self._listener
        self._listener = None
        if listener is not None:
            try:
                listener.stop()
                listener.join(timeout=1.0)
            except Exception:
                log.debug("Could not stop the hotkey listener", exc_info=True)

    def set_combo(self, combo: str) -> None:
        keys = validate_hotkey(combo)
        with self._lock:
            self._combo = keys
            self._down.clear()
            self._combo_down = False
        log.info("Hotkey changed to %s", format_hotkey(keys))

    def set_paused(self, paused: bool) -> None:
        with self._lock:
            self._paused = paused
            self._down.clear()
            self._combo_down = False

    def _on_press(self, key: object) -> None:
        canonical = canonical_key(key)
        if canonical is None:
            return
        with self._lock:
            if self._paused:
                return
            self._down.add(canonical)
            if self._combo_down or not self._combo.issubset(self._down):
                return
            self._combo_down = True
        self.pressed.emit()

    def _on_release(self, key: object) -> None:
        canonical = canonical_key(key)
        if canonical is None:
            return
        with self._lock:
            self._down.discard(canonical)
            if not self._combo_down or self._combo.issubset(self._down):
                return
            self._combo_down = False
        self.released.emit()
