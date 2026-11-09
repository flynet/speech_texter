from __future__ import annotations

import ctypes
import logging
import time
from ctypes import wintypes

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtGui import QClipboard, QGuiApplication

log = logging.getLogger(__name__)

INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
VK_CONTROL = 0x11
VK_MENU = 0x12
VK_SHIFT = 0x10
VK_LWIN = 0x5B
VK_RWIN = 0x5C
VK_V = 0x56
MODIFIER_VKS = (VK_CONTROL, VK_MENU, VK_SHIFT, VK_LWIN, VK_RWIN)

ULONG_PTR = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    ]


class _INPUT_UNION(ctypes.Union):
    _fields_ = [
        ("ki", KEYBDINPUT),
        ("mi", MOUSEINPUT),
        ("hi", HARDWAREINPUT),
    ]


class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [
        ("type", wintypes.DWORD),
        ("u", _INPUT_UNION),
    ]


def _key_event(vk: int, key_up: bool = False) -> INPUT:
    flags = KEYEVENTF_KEYUP if key_up else 0
    return INPUT(
        type=INPUT_KEYBOARD,
        u=_INPUT_UNION(ki=KEYBDINPUT(wVk=vk, wScan=0, dwFlags=flags, time=0, dwExtraInfo=0)),
    )


class Inserter(QObject):
    finished = Signal()
    error = Signal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._clipboard = QGuiApplication.clipboard()
        self._timer = QTimer(self)
        self._timer.setInterval(10)
        self._timer.timeout.connect(self._poll_modifiers)
        self._pending_text: str | None = None
        self._saved_text = ""
        self._deadline = 0.0
        self._active = False

    def insert(self, text: str) -> None:
        if self._active:
            self.error.emit("Another insertion is already in progress.")
            return
        if not text:
            QTimer.singleShot(0, self.finished.emit)
            return
        self._active = True
        self._pending_text = text
        self._saved_text = self._clipboard.text()
        self._clipboard.setText(text, QClipboard.Clipboard)
        self._deadline = time.monotonic() + 1.0
        self._timer.start()

    def _poll_modifiers(self) -> None:
        if self._modifiers_down() and time.monotonic() < self._deadline:
            return
        self._timer.stop()
        if not self._send_ctrl_v():
            self._restore_clipboard()
            self._active = False
            self.error.emit("Could not send Ctrl+V to the active window.")
            return
        QTimer.singleShot(250, self._restore_and_finish)

    @staticmethod
    def _modifiers_down() -> bool:
        try:
            user32 = ctypes.WinDLL("user32", use_last_error=True)
            user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
            user32.GetAsyncKeyState.restype = ctypes.c_short
            return any(user32.GetAsyncKeyState(vk) & 0x8000 for vk in MODIFIER_VKS)
        except Exception:
            return False

    @staticmethod
    def _send_ctrl_v() -> bool:
        try:
            user32 = ctypes.WinDLL("user32", use_last_error=True)
            user32.SendInput.argtypes = [
                wintypes.UINT,
                ctypes.POINTER(INPUT),
                ctypes.c_int,
            ]
            user32.SendInput.restype = wintypes.UINT
            events = (
                _key_event(VK_CONTROL),
                _key_event(VK_V),
                _key_event(VK_V, key_up=True),
                _key_event(VK_CONTROL, key_up=True),
            )
            array = (INPUT * len(events))(*events)
            sent = user32.SendInput(len(events), array, ctypes.sizeof(INPUT))
            if sent != len(events):
                log.error("SendInput sent %s of %s events", sent, len(events))
                return False
            return True
        except Exception:
            log.exception("SendInput failed")
            return False

    def _restore_clipboard(self) -> None:
        if self._pending_text is None:
            return
        try:
            if self._clipboard.text() == self._pending_text:
                self._clipboard.setText(self._saved_text, QClipboard.Clipboard)
        except Exception:
            log.debug("Could not restore the clipboard", exc_info=True)
        self._pending_text = None

    def _restore_and_finish(self) -> None:
        self._restore_clipboard()
        self._active = False
        self.finished.emit()
