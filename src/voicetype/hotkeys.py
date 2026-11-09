from __future__ import annotations

import logging
import sys
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

# Virtual-key codes used by the low-level Windows event filter. The filter runs
# before pynput converts an event, so it needs its own code-to-name mapping.
_VK_NAMES = {
    0x08: "backspace",
    0x09: "tab",
    0x0D: "enter",
    0x10: "shift",
    0x11: "ctrl",
    0x12: "alt",
    # The low-level hook reports the left/right variants for physical
    # modifier keys, so they must map to the same canonical names.
    0xA0: "shift",
    0xA1: "shift",
    0xA2: "ctrl",
    0xA3: "ctrl",
    0xA4: "alt",
    0xA5: "alt",
    0x13: "pause",
    0x14: "capslock",
    0x1B: "esc",
    0x20: "space",
    0x21: "pageup",
    0x22: "pagedown",
    0x23: "end",
    0x24: "home",
    0x25: "left",
    0x26: "up",
    0x27: "right",
    0x28: "down",
    0x2C: "printscreen",
    0x2D: "insert",
    0x2E: "delete",
    0x5D: "menu",
    0x5B: "win",
    0x5C: "win",
    0x90: "numlock",
    0x91: "scrolllock",
    0xAD: "mediavolumemute",
    0xAE: "mediavolumedown",
    0xAF: "mediavolumeup",
    0xB0: "mediaplaypause",
    0xB1: "mediaprevious",
    0xB2: "medianext",
    0xBA: ";",
    0xBB: "=",
    0xBC: ",",
    0xBD: "-",
    0xBE: ".",
    0xBF: "/",
    0xC0: "`",
    0xDB: "[",
    0xDC: "\\",
    0xDD: "]",
    0xDE: "'",
}

_WM_KEYDOWN = 0x0100
_WM_KEYUP = 0x0101
_WM_SYSKEYDOWN = 0x0104
_WM_SYSKEYUP = 0x0105
_LLKHF_INJECTED = 0x10
_LLKHF_LOWER_IL_INJECTED = 0x02


def canonical_vk(vk: int) -> str | None:
    """Map a Windows virtual-key code to a canonical key name."""
    if vk in _VK_NAMES:
        return _VK_NAMES[vk]
    if 0x70 <= vk <= 0x87:
        return f"f{vk - 0x6F}"
    if 0x41 <= vk <= 0x5A:
        return chr(vk).lower()
    if 0x30 <= vk <= 0x39:
        return chr(vk)
    return None


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
    tokens = [
        part.strip().lower()
        for part in combo.replace(" ", "").split("+")
        if part.strip()
    ]
    if not tokens:
        raise ValueError("Hotkey is empty")
    return frozenset(_canonical_token(token) for token in tokens)


def _is_standalone_key(key: str) -> bool:
    if key == "space":
        return True
    if key.startswith("f") and key[1:].isdigit():
        return 1 <= int(key[1:]) <= 24
    return False


def validate_hotkey(combo: str) -> frozenset[str]:
    keys = parse_hotkey(combo)
    if "win" in keys:
        raise ValueError("The Windows key cannot be used in a global hotkey.")
    trigger = keys - MODIFIERS
    if not trigger:
        raise ValueError("Add a non-modifier key to the hotkey.")
    if not keys & MODIFIERS:
        if not all(_is_standalone_key(key) for key in trigger):
            raise ValueError(
                "Add a modifier, or use F1-F24 / Space as a single-key hotkey."
            )
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
        self._trigger_keys = self._combo - MODIFIERS
        self._modifier_keys = self._combo & MODIFIERS
        self._listener: keyboard.Listener | None = None
        self._backend: keyboard.Listener | None = None
        self._lock = threading.Lock()
        self._down: set[str] = set()
        self._combo_down = False
        self._paused = False
        # On Windows the low-level event filter sees and can swallow keys
        # before the focused application receives them. That is what keeps a
        # Space or F5 hotkey from leaking into a text field.
        self._use_filter = sys.platform == "win32"

    def start(self) -> None:
        if self._listener is not None:
            return
        try:
            if self._use_filter:
                # pynput exposes the Windows low-level hook filter as
                # ``win32_event_filter``; plain ``event_filter`` is silently
                # ignored, which leaves the listener without any callback.
                listener = keyboard.Listener(
                    win32_event_filter=self._event_filter
                )
                self._backend = listener
            else:
                listener = keyboard.Listener(
                    on_press=self._on_press, on_release=self._on_release
                )
            listener.daemon = True
            listener.start()
            self._listener = listener
            if self._use_filter:
                if getattr(listener, "_event_filter", None) is None:
                    log.error(
                        "The Windows key filter was not attached; "
                        "hotkeys will not work"
                    )
                else:
                    log.info("Windows low-level key filter attached")
            log.info(
                "Global hotkey listener started (%s)",
                format_hotkey(self._combo),
            )
        except Exception as exc:
            log.exception("Could not start the global hotkey listener")
            self._listener = None
            self._backend = None
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
        self._backend = None

    def set_combo(self, combo: str) -> None:
        keys = validate_hotkey(combo)
        with self._lock:
            self._combo = keys
            self._trigger_keys = keys - MODIFIERS
            self._modifier_keys = keys & MODIFIERS
            self._down.clear()
            self._combo_down = False
        log.info("Hotkey changed to %s", format_hotkey(keys))

    def set_paused(self, paused: bool) -> None:
        with self._lock:
            self._paused = paused
            self._down.clear()
            self._combo_down = False

    def _event_filter(self, msg: int, data: object) -> bool:
        if self._filter_event(msg, data):
            return True
        backend = self._backend
        if backend is not None:
            # Raises pynput's SuppressException, which makes the low-level
            # Windows hook return a non-zero value and keeps the key away from
            # the focused application.
            backend.suppress_event()
        return False

    def _filter_event(self, msg: int, data: object) -> bool:
        """Update hotkey state and report whether the event may pass.

        Returns ``True`` to let the event reach the focused application and
        ``False`` when the trigger key must be swallowed. Modifier keys are
        always let through so that unrelated shortcuts keep working.
        """
        if msg not in (_WM_KEYDOWN, _WM_KEYUP, _WM_SYSKEYDOWN, _WM_SYSKEYUP):
            return True
        try:
            flags = int(getattr(data, "flags", 0))
            vk = int(getattr(data, "vkCode", 0))
        except (TypeError, ValueError):
            return True
        if flags & (_LLKHF_INJECTED | _LLKHF_LOWER_IL_INJECTED):
            return True
        canonical = canonical_vk(vk)
        if canonical is None or canonical not in self._combo:
            return True
        is_down = msg in (_WM_KEYDOWN, _WM_SYSKEYDOWN)
        trigger = canonical in self._trigger_keys
        pressed = released = False
        with self._lock:
            if self._paused:
                return True
            if trigger:
                if is_down:
                    if not self._modifier_keys.issubset(self._down):
                        return True
                    if canonical in self._down:
                        # Auto-repeat while the hotkey is held: keep it hidden.
                        return False
                    self._down.add(canonical)
                    if not self._combo_down and self._combo.issubset(self._down):
                        self._combo_down = True
                        pressed = True
                else:
                    if canonical not in self._down:
                        return True
                    self._down.discard(canonical)
                    if not self._combo.issubset(self._down):
                        self._combo_down = False
                        released = True
            elif is_down:
                self._down.add(canonical)
            else:
                self._down.discard(canonical)
                if self._combo_down and not self._combo.issubset(self._down):
                    self._combo_down = False
                    released = True
        if pressed:
            self._emit(self.pressed)
        if released:
            self._emit(self.released)
        return not trigger

    def _emit(self, signal: Signal) -> None:
        try:
            signal.emit()
        except Exception:
            # The Qt object was deleted while the listener was still running.
            # Never let a Qt error kill the low-level hook thread.
            log.debug("Could not emit hotkey signal", exc_info=True)

    def _handle_press_locked(self, canonical: str) -> bool:
        if self._paused:
            return False
        self._down.add(canonical)
        if self._combo_down or not self._combo.issubset(self._down):
            return False
        self._combo_down = True
        return True

    def _handle_release_locked(self, canonical: str) -> bool:
        self._down.discard(canonical)
        if not self._combo_down or self._combo.issubset(self._down):
            return False
        self._combo_down = False
        return True

    def _on_press(self, key: object) -> None:
        canonical = canonical_key(key)
        if canonical is None or canonical not in self._combo:
            return
        with self._lock:
            pressed = self._handle_press_locked(canonical)
        if pressed:
            self._emit(self.pressed)

    def _on_release(self, key: object) -> None:
        canonical = canonical_key(key)
        if canonical is None or canonical not in self._combo:
            return
        with self._lock:
            released = self._handle_release_locked(canonical)
        if released:
            self._emit(self.released)
