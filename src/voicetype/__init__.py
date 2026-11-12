"""VoiceType — local push-to-talk speech-to-text for Windows."""

# PySide6 installs a shiboken import hook that breaks when pynput lazily imports
# ``six.moves`` on Python 3.12 (inspect.getsource fails on the six meta path
# importer). Importing pynput before any PySide6 module avoids the conflict.
try:  # pragma: no cover - import order workaround
    import pynput  # noqa: F401
except Exception:
    pass

__version__ = "0.2.0"
APP_NAME = "VoiceType"
APP_ID = "VoiceType.SingleInstance"
GITHUB_REPO = "flynet/speech_texter"


def patch_shiboken_import_hook() -> None:
    """Make shiboken's import hook tolerate lazy ``six`` modules.

    PySide6's ``shibokensupport`` inspects the source of every imported module
    to detect whether it uses PySide6. Lazy modules created by ``six.moves``
    do not expose a source file, so ``inspect.getsource`` raises
    ``AttributeError`` on Python 3.12. Treat such modules as non-PySide6
    modules instead of crashing the application.
    """
    try:
        from shibokensupport import feature
    except Exception:
        return
    original = getattr(feature, "_mod_uses_pyside", None)
    if original is None or getattr(original, "_voicetype_patched", False):
        return

    def _safe_mod_uses_pyside(module):
        try:
            return original(module)
        except Exception:
            return False

    _safe_mod_uses_pyside._voicetype_patched = True
    feature._mod_uses_pyside = _safe_mod_uses_pyside
