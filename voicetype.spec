# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller build specification for VoiceType (onedir, windowed)."""

from pathlib import Path
import re

from PyInstaller.utils.hooks import collect_all
from PyInstaller.utils.win32.versioninfo import (
    FixedFileInfo,
    StringFileInfo,
    StringStruct,
    StringTable,
    VarFileInfo,
    VarStruct,
    VSVersionInfo,
)

ROOT = Path(SPECPATH) if "SPECPATH" in globals() else Path.cwd()
SRC = ROOT / "src"
ASSETS = ROOT / "assets"
BUILD = ROOT / "build"
BUILD.mkdir(parents=True, exist_ok=True)

# Custom runtime hooks run before PyInstaller's own hooks. The first one must
# import pynput before pyi_rth_pyside6 imports PySide6 (see the file docstring).
RUNTIME_HOOKS = [str(ROOT / "build_hooks" / "rthook_early.py")]

VERSION = "0.1.3"
match = re.search(
    r'__version__\s*=\s*"([^"]+)"',
    (SRC / "voicetype" / "__init__.py").read_text(encoding="utf-8"),
)
if match:
    VERSION = match.group(1)


def make_version_file(version: str) -> str:
    parts = [int(item) for item in re.findall(r"\d+", version)][:4]
    while len(parts) < 4:
        parts.append(0)
    info = VSVersionInfo(
        ffi=FixedFileInfo(
            filevers=tuple(parts),
            prodvers=tuple(parts),
            mask=0x3F,
            flags=0x0,
            OS=0x40004,
            fileType=0x1,
            subtype=0x0,
            date=(0, 0),
        ),
        kids=[
            StringFileInfo(
                [
                    StringTable(
                        "040904B0",
                        [
                            StringStruct("CompanyName", "flynet"),
                            StringStruct("FileDescription", "VoiceType"),
                            StringStruct("FileVersion", version),
                            StringStruct("InternalName", "VoiceType"),
                            StringStruct("LegalCopyright", "Copyright (c) flynet"),
                            StringStruct("OriginalFilename", "VoiceType.exe"),
                            StringStruct("ProductName", "VoiceType"),
                            StringStruct("ProductVersion", version),
                        ],
                    )
                ]
            ),
            VarFileInfo([VarStruct("Translation", [1033, 1200])]),
        ],
    )
    path = BUILD / "version_info.txt"
    path.write_text(str(info), encoding="utf-8")
    return str(path)


datas = [(str(ASSETS), "assets")]
binaries = []
hiddenimports = [
    "pynvml",
    "psutil",
    "requests",
    "certifi",
    "charset_normalizer",
    "six",
]

for package in (
    "ctranslate2",
    "faster_whisper",
    "onnxruntime",
    "av",
    "sounddevice",
    "tokenizers",
    "pynput",
    "huggingface_hub",
):
    package_datas, package_binaries, package_hiddenimports = collect_all(package)
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_hiddenimports

a = Analysis(
    [str(SRC / "voicetype" / "__main__.py")],
    pathex=[str(SRC)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=RUNTIME_HOOKS,
    excludes=[
        "tkinter",
        "_tkinter",
        "matplotlib",
        "pandas",
        "scipy",
        "IPython",
        "pytest",
        "sphinx",
        "notebook",
        "PySide6.QtWebEngineCore",
        "PySide6.QtWebEngineWidgets",
        "PySide6.QtWebEngineQuick",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="VoiceType",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ASSETS / "app.ico"),
    version=make_version_file(VERSION),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="VoiceType",
)
