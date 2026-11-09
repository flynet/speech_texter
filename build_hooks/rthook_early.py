"""PyInstaller runtime hook: import pynput before PySide6.

PyInstaller's ``pyi_rth_pyside6`` runtime hook imports ``PySide6.QtCore``.
Importing PySide6 installs the shiboken import hook, which inspects the source
of every imported module. On Python 3.12, ``pynput`` lazily imports
``six.moves`` and shiboken's inspection then fails with::

    AttributeError: '_SixMetaPathImporter' object has no attribute '_path'

Custom runtime hooks run before PyInstaller's own hooks, so importing pynput
here loads ``six.moves`` while no shiboken hook is installed yet. The same
ordering is used in ``voicetype/__main__.py`` for source runs.
"""

try:
    import pynput  # noqa: F401
except Exception:  # pragma: no cover - never block startup
    pass
