# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller build specification for VoiceType (onedir, windowed)."""

from pathlib import Path
import re
import sysconfig

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

SITE_PACKAGES = Path(sysconfig.get_paths()["purelib"])


def collect_libs(package: str) -> list[tuple[str, str]]:
    """Keep every DLL a repaired wheel ships in `<package>.libs`.

    Delvewheel renames the bundled Microsoft C++ runtime DLLs to hash names and
    patches the package to add `<package>.libs` to the DLL search path.
    PyInstaller recognizes those DLLs as the Microsoft C++ runtime, normalizes
    the name to `msvcp140.dll`, and puts the copy in the bundle root, so the hash
    name the extension asks for is gone and the extension fails to load with
    `DLL load failed while importing _sentencepiece`. Copy the DLLs as data, so
    they are copied verbatim, and skip a package whose own hook collects the
    directory (numpy, av, pandas do).
    """
    lib_dir = SITE_PACKAGES / f"{package}.libs"
    if not lib_dir.is_dir():
        return []
    return [(str(path), lib_dir.name) for path in sorted(lib_dir.glob("*.dll"))]


for package in (
    "ctranslate2",
    "faster_whisper",
    "onnxruntime",
    "av",
    "sounddevice",
    "tokenizers",
    "pynput",
    "huggingface_hub",
    # Translation packs need the SentencePiece tokenizer shipped as a module.
    "sentencepiece",
):
    package_datas, package_binaries, package_hiddenimports = collect_all(package)
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_hiddenimports
    lib_dir = SITE_PACKAGES / f"{package}.libs"
    if any(dest == lib_dir.name for _src, dest in package_binaries + package_datas):
        continue
    datas += collect_libs(package)

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
