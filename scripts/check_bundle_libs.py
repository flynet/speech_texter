"""Compare the delvewheel DLL directories in site-packages with the built bundle.

A repaired wheel ships its DLLs in `<package>.libs` with hash names, and the
package adds that directory to the DLL search path, so the exact file names have
to survive the build. PyInstaller recognizes the Microsoft C++ runtime DLLs and
normalizes the name, which leaves the hash name the extension asks for missing.
The build fails here with the missing files listed, not on a user's machine.
"""

import sys
import sysconfig
from pathlib import Path


def missing(site_packages: Path, bundle: Path) -> list[str]:
    """Report the DLLs in site-packages `<package>.libs` directories that the
    bundle does not have under the same name."""
    problems: list[str] = []
    for lib_dir in sorted(site_packages.glob("*.libs")):
        if not lib_dir.is_dir():
            continue
        names = {path.name for path in lib_dir.glob("*.dll")}
        if not names:
            continue
        target = bundle / lib_dir.name
        if not target.is_dir():
            problems.append(f"{lib_dir.name}: the directory is not in the bundle")
            continue
        bundled = {path.name for path in target.rglob("*.dll")}
        for name in sorted(names - bundled):
            problems.append(f"{lib_dir.name}: {name}")
    return problems


def main() -> int:
    site = Path(sysconfig.get_paths()["purelib"])
    bundle = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("dist/VoiceType/_internal")
    problems = missing(site, bundle)
    if problems:
        print("bundled DLLs are missing:", *problems, sep="\n  ")
        return 1
    print("bundled delvewheel directories match site-packages")
    return 0


if __name__ == "__main__":
    sys.exit(main())
