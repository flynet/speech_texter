from __future__ import annotations

# This import must stay before any PySide6 import. See voicetype/__init__.py.
import pynput  # noqa: F401

import ctypes
import logging
import sys
from ctypes import wintypes
from logging.handlers import RotatingFileHandler

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from voicetype import APP_ID, __version__, patch_shiboken_import_hook
from voicetype.config import assets_dir, logs_dir

patch_shiboken_import_hook()

log = logging.getLogger(__name__)
_mutex_handle: int | None = None


def setup_logging() -> None:
    directory = logs_dir()
    directory.mkdir(parents=True, exist_ok=True)
    handlers: list[logging.Handler] = [
        RotatingFileHandler(
            directory / "app.log",
            maxBytes=1_000_000,
            backupCount=3,
            encoding="utf-8",
        )
    ]
    if not getattr(sys, "frozen", False):
        handlers.append(logging.StreamHandler())
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=handlers,
        force=True,
    )


def acquire_single_instance() -> bool:
    global _mutex_handle
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.argtypes = [
            ctypes.c_void_p,
            wintypes.BOOL,
            wintypes.LPCWSTR,
        ]
        kernel32.CreateMutexW.restype = wintypes.HANDLE
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        handle = kernel32.CreateMutexW(None, False, APP_ID)
        error = ctypes.get_last_error()
        if not handle:
            log.error("CreateMutexW failed with error %s", error)
            return True
        if error == 183:  # ERROR_ALREADY_EXISTS
            kernel32.CloseHandle(handle)
            return False
        _mutex_handle = handle
        return True
    except Exception:
        log.exception("Could not create the single-instance mutex")
        return True


def _show_already_running() -> None:
    try:
        ctypes.windll.user32.MessageBoxW(
            None,
            "VoiceType is already running.",
            "VoiceType",
            0x00000040,
        )
    except Exception:
        pass


def _application_icon() -> QIcon:
    path = assets_dir() / "app.ico"
    return QIcon(str(path)) if path.is_file() else QIcon()


def main() -> int:
    setup_logging()
    log.info("Starting VoiceType %s", __version__)
    if not acquire_single_instance():
        log.info("Another VoiceType instance is already running")
        _show_already_running()
        return 0

    app = QApplication(sys.argv)
    app.setApplicationName("VoiceType")
    app.setApplicationDisplayName("VoiceType")
    app.setOrganizationName("flynet")
    app.setQuitOnLastWindowClosed(False)
    icon = _application_icon()
    if not icon.isNull():
        app.setWindowIcon(icon)

    try:
        from voicetype.app import VoiceTypeApplication

        application = VoiceTypeApplication(app)
        application.start()
        return app.exec()
    except Exception:
        log.exception("Fatal application error")
        try:
            ctypes.windll.user32.MessageBoxW(
                None,
                "VoiceType could not start. See the log file for details.",
                "VoiceType",
                0x00000010,
            )
        except Exception:
            pass
        return 1
    finally:
        logging.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
