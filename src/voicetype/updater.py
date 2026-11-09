from __future__ import annotations

import hashlib
import logging
import re
import subprocess
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path

import requests
from PySide6.QtCore import QCoreApplication, QObject, Signal

from . import GITHUB_REPO, __version__

log = logging.getLogger(__name__)

API_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases"
HEADERS = {
    "Accept": "application/vnd.github+json",
    "User-Agent": f"VoiceType/{__version__}",
}


class UpdateCancelled(Exception):
    pass


@dataclass(frozen=True)
class UpdateInfo:
    version: str
    tag: str
    name: str
    notes: str
    html_url: str
    setup_url: str
    setup_name: str
    sums_url: str | None


def version_key(value: str) -> tuple[tuple[int, int, int], int, str]:
    match = re.match(
        r"^v?(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?(?:\+.*)?$",
        value.strip(),
    )
    if not match:
        return (0, 0, 0), 0, value.strip()
    numbers = (int(match.group(1)), int(match.group(2)), int(match.group(3)))
    pre = match.group(4)
    return numbers, 0 if pre else 1, pre or ""


def is_newer(latest: str, current: str) -> bool:
    return version_key(latest) > version_key(current)


_RELEASE_TAG = re.compile(r"^v?\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+.*)?$")


def select_latest_release(releases: object) -> dict | None:
    """Pick the highest normal application release from the GitHub list.

    The repository also publishes ``cuda-pack-v1``. GitHub's
    ``/releases/latest`` endpoint would return it once it is the newest release,
    so the list is filtered by tag shape and the highest semantic version.
    """
    if not isinstance(releases, list):
        return None
    best: dict | None = None
    for release in releases:
        if not isinstance(release, dict):
            continue
        if release.get("draft") or release.get("prerelease"):
            continue
        tag = str(release.get("tag_name") or "")
        if not _RELEASE_TAG.match(tag):
            continue
        if best is None or version_key(tag) > version_key(
            str(best.get("tag_name") or "")
        ):
            best = release
    return best


def check_for_update(current_version: str) -> UpdateInfo | None:
    response = requests.get(
        API_URL,
        headers=HEADERS,
        params={"per_page": 30},
        timeout=20,
    )
    if response.status_code == 404:
        return None
    response.raise_for_status()
    payload = select_latest_release(response.json())
    if payload is None:
        return None
    tag = str(payload.get("tag_name") or "")
    if not tag or not is_newer(tag, current_version):
        return None
    version = tag.lstrip("v")
    assets = payload.get("assets") or []
    setup = None
    sums = None
    for asset in assets:
        name = str(asset.get("name") or "")
        url = str(asset.get("browser_download_url") or "")
        if name == f"VoiceType-Setup-{version}.exe":
            setup = (name, url)
        elif name.startswith("VoiceType-Setup-") and name.endswith(".exe") and setup is None:
            setup = (name, url)
        if name == "SHA256SUMS.txt":
            sums = url
    if setup is None:
        raise RuntimeError("The latest release does not contain a VoiceType installer.")
    return UpdateInfo(
        version=version,
        tag=tag,
        name=str(payload.get("name") or tag),
        notes=str(payload.get("body") or ""),
        html_url=str(payload.get("html_url") or ""),
        setup_url=setup[1],
        setup_name=setup[0],
        sums_url=sums,
    )


def _parse_sums(text: str, filename: str) -> str | None:
    for line in text.splitlines():
        parts = line.strip().split()
        if len(parts) >= 2 and parts[-1].lstrip("*") == filename:
            return parts[0].lower()
    return None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().lower()


class UpdateChecker(QObject):
    check_finished = Signal(object)
    check_failed = Signal(str)
    download_progress = Signal(int, int)
    download_finished = Signal(str)
    download_failed = Signal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._checking = False
        self._downloading = False

    def check_async(self, current_version: str = __version__) -> None:
        if self._checking:
            return
        self._checking = True
        threading.Thread(
            target=self._check_worker,
            args=(current_version,),
            daemon=True,
            name="VoiceTypeUpdateCheck",
        ).start()

    def _check_worker(self, current_version: str) -> None:
        try:
            info = check_for_update(current_version)
        except Exception as exc:
            log.exception("Update check failed")
            self._checking = False
            self.check_failed.emit(str(exc))
            return
        self._checking = False
        self.check_finished.emit(info)

    def download_async(self, info: UpdateInfo, cancel: threading.Event) -> None:
        if self._downloading:
            return
        self._downloading = True
        threading.Thread(
            target=self._download_worker,
            args=(info, cancel),
            daemon=True,
            name="VoiceTypeUpdateDownload",
        ).start()

    def _download_worker(self, info: UpdateInfo, cancel: threading.Event) -> None:
        destination = Path(tempfile.gettempdir()) / "VoiceType" / info.setup_name
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            if not info.sums_url:
                raise RuntimeError("The release does not provide SHA256SUMS.txt.")
            with requests.get(
                info.setup_url,
                stream=True,
                headers={"User-Agent": HEADERS["User-Agent"]},
                timeout=(20, 120),
            ) as response:
                response.raise_for_status()
                total = int(response.headers.get("Content-Length") or 0)
                done = 0
                with destination.open("wb") as handle:
                    for chunk in response.iter_content(chunk_size=1024 * 1024):
                        if cancel.is_set():
                            raise UpdateCancelled()
                        if not chunk:
                            continue
                        handle.write(chunk)
                        done += len(chunk)
                        self.download_progress.emit(done, total)
            sums_response = requests.get(
                info.sums_url,
                headers={"User-Agent": HEADERS["User-Agent"]},
                timeout=20,
            )
            sums_response.raise_for_status()
            expected = _parse_sums(sums_response.text, info.setup_name)
            if not expected:
                raise RuntimeError(f"{info.setup_name} is missing from SHA256SUMS.txt.")
            actual = _sha256(destination)
            if actual != expected:
                raise RuntimeError(
                    f"Installer checksum mismatch: expected {expected}, got {actual}."
                )
        except UpdateCancelled:
            destination.unlink(missing_ok=True)
            self._downloading = False
            self.download_failed.emit("Update download cancelled.")
            return
        except Exception as exc:
            log.exception("Update download failed")
            destination.unlink(missing_ok=True)
            self._downloading = False
            self.download_failed.emit(str(exc))
            return
        self._downloading = False
        self.download_finished.emit(str(destination))

    @staticmethod
    def install(setup_path: str) -> None:
        log.info("Starting the installer at %s", setup_path)
        subprocess.Popen(
            [
                setup_path,
                "/SILENT",
                "/CLOSEAPPLICATIONS",
                "/RESTARTAPPLICATIONS",
            ],
            close_fds=True,
        )
        QCoreApplication.quit()
