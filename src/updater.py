"""检查 GitHub Releases 并用官方安装包就地更新。

检查/下载走 QtNetwork（与 geocode 一致）；替换文件由一个脱离本进程的脚本完成：
应用退出后解压覆盖当前安装目录（Windows 便携 zip）或替换 .app（macOS dmg），再重新启动。
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject, QUrl, Signal
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest

APP_VERSION = "1.0.1"  # 发布时由 CI 按 tag 覆盖
REPO = "peagothedreamer/Elsewhere"
# 不走 api.github.com：未认证 API 每个 IP 每小时只有 60 次，共享出口常被耗尽（403）。
# releases/latest 会 302 到 /releases/tag/<tag>，网页请求没有这个限制。
LATEST_URL = f"https://github.com/{REPO}/releases/latest"
USER_AGENT = f"Elsewhere/{APP_VERSION}"

WINDOWS_SCRIPT = r"""param([int]$WaitPid, [string]$Zip, [string]$AppDir)
try { Wait-Process -Id $WaitPid -Timeout 120 -ErrorAction SilentlyContinue } catch {}
Start-Sleep -Milliseconds 800
$tmp = Join-Path ([System.IO.Path]::GetTempPath()) ([guid]::NewGuid().ToString("N"))
try {
  Expand-Archive -LiteralPath $Zip -DestinationPath $tmp -Force
  $src = Join-Path $tmp 'Elsewhere'
  if (-not (Test-Path (Join-Path $src 'Elsewhere.exe'))) { $src = $tmp }
  Copy-Item -Path (Join-Path $src '*') -Destination $AppDir -Recurse -Force
} catch {}
Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
Remove-Item -Force $Zip -ErrorAction SilentlyContinue
Remove-Item -Force $PSCommandPath -ErrorAction SilentlyContinue
Start-Process -FilePath (Join-Path $AppDir 'Elsewhere.exe')
"""

MACOS_SCRIPT = r"""#!/bin/bash
pid=$1; dmg=$2; app=$3
while kill -0 "$pid" 2>/dev/null; do sleep 0.5; done
mnt=$(mktemp -d)
if ! hdiutil attach -nobrowse -quiet "$dmg" -mountpoint "$mnt"; then
  rm -rf "$mnt"; open "$app"; exit 1
fi
new="$app.updating"
rm -rf "$new"
if ditto "$mnt/Elsewhere.app" "$new"; then
  hdiutil detach -quiet "$mnt"
  rm -rf "$app" && mv "$new" "$app"
else
  hdiutil detach -quiet "$mnt"
  rm -rf "$new"
fi
rm -rf "$mnt" "$dmg"
open "$app"
rm -f "$0"
"""


@dataclass(frozen=True)
class Release:
    version: str
    assets: tuple[dict, ...]


def _parts(text: str) -> tuple[int, ...]:
    nums = [int(n) for n in re.findall(r"\d+", text)[:3]]
    return tuple(nums + [0] * (3 - len(nums)))


def is_newer(latest: str, current: str) -> bool:
    return _parts(latest) > _parts(current)


def pick_asset(assets, platform: str = sys.platform) -> dict | None:
    suffix = {"win32": "-portable.zip", "darwin": "-arm64.dmg"}.get(platform)
    if suffix is None:
        return None
    for asset in assets:
        name = str(asset.get("name", ""))
        if name.endswith(suffix) and asset.get("browser_download_url"):
            return asset
    return None


def asset_urls(version: str) -> tuple[dict, ...]:
    """安装包命名固定（见 release.yml），可直接由版本号拼出下载地址。"""
    base = f"https://github.com/{REPO}/releases/download/v{version}"
    return tuple(
        {"name": name, "browser_download_url": f"{base}/{name}"}
        for name in (
            f"Elsewhere-{version}-portable.zip",
            f"Elsewhere-{version}-arm64.dmg",
        )
    )


def parse_tag_url(url: str) -> Release | None:
    match = re.search(r"/releases/tag/([^/?#]+)", url or "")
    if match is None:
        return None
    version = match.group(1).lstrip("vV")
    return Release(version, asset_urls(version)) if version else None


class Updater(QObject):
    checked = Signal(object)  # Release
    progress = Signal("qlonglong", "qlonglong")  # received, total
    downloaded = Signal(str)  # 临时文件路径
    error = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._nam = QNetworkAccessManager(self)
        self._out = None
        self._path = ""

    def check(self) -> None:
        reply = self._nam.get(self._request(LATEST_URL))
        reply.finished.connect(lambda: self._on_checked(reply))

    def download(self, asset: dict) -> None:
        suffix = ".dmg" if sys.platform == "darwin" else ".zip"
        fd, self._path = tempfile.mkstemp(prefix="elsewhere-update-", suffix=suffix)
        os.close(fd)
        self._out = open(self._path, "wb")
        reply = self._nam.get(self._request(asset["browser_download_url"]))
        reply.readyRead.connect(lambda: self._write(reply))
        reply.downloadProgress.connect(self.progress)
        reply.finished.connect(lambda: self._on_downloaded(reply))

    @staticmethod
    def _request(url: str) -> QNetworkRequest:
        request = QNetworkRequest(QUrl(url))
        request.setRawHeader(b"User-Agent", USER_AGENT.encode())
        request.setAttribute(
            QNetworkRequest.Attribute.RedirectPolicyAttribute,
            QNetworkRequest.RedirectPolicy.NoLessSafeRedirectPolicy,
        )
        return request

    def _on_checked(self, reply: QNetworkReply) -> None:
        try:
            ok = reply.error() == QNetworkReply.NetworkError.NoError
            # 跟随 302 后 reply.url() 就是 /releases/tag/<tag>。
            url = reply.url().toString()
            message = reply.errorString()
        finally:
            reply.deleteLater()
        release = parse_tag_url(url) if ok else None
        if release is None:
            self.error.emit(f"检查更新失败：{message if not ok else '无法解析最新版本'}")
            return
        self.checked.emit(release)

    def _write(self, reply: QNetworkReply) -> None:
        try:
            if self._out is not None:
                self._out.write(bytes(reply.readAll().data()))
        except OSError:
            reply.abort()

    def _on_downloaded(self, reply: QNetworkReply) -> None:
        out, self._out = self._out, None
        path, self._path = self._path, ""
        try:
            ok = reply.error() == QNetworkReply.NetworkError.NoError
            message = reply.errorString()
            if out is not None:
                out.write(bytes(reply.readAll().data()))
                out.close()
        except OSError as exc:
            ok, message = False, str(exc)
        finally:
            reply.deleteLater()
        if not ok:
            if path:
                Path(path).unlink(missing_ok=True)
            self.error.emit(f"下载失败：{message}")
            return
        self.downloaded.emit(path)


def apply_update(archive: str | Path) -> None:
    """启动后台脚本：等本进程退出后替换程序文件并重新启动。"""
    if not getattr(sys, "frozen", False):
        raise RuntimeError("源码运行时不支持自动更新，请 git pull")
    if sys.platform == "win32":
        _apply_windows(Path(archive))
    elif sys.platform == "darwin":
        _apply_macos(Path(archive))
    else:
        raise RuntimeError("当前平台不支持自动更新")


def _spawn(args: list[str], **kwargs) -> None:
    subprocess.Popen(
        args,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        **kwargs,
    )


def _apply_windows(archive: Path) -> None:
    script = Path(tempfile.gettempdir()) / f"elsewhere-update-{os.getpid()}.ps1"
    script.write_text(WINDOWS_SCRIPT, encoding="utf-8-sig")
    _spawn(
        [
            "powershell", "-NoProfile", "-WindowStyle", "Hidden",
            "-ExecutionPolicy", "Bypass", "-File", str(script),
            "-WaitPid", str(os.getpid()),
            "-Zip", str(archive),
            "-AppDir", str(Path(sys.executable).resolve().parent),
        ],
        creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
    )


def _apply_macos(archive: Path) -> None:
    exe = Path(sys.executable).resolve()
    app = next((p for p in exe.parents if p.suffix == ".app"), None)
    if app is None:
        raise RuntimeError("找不到 .app 应用包")
    if str(app).startswith("/Volumes/"):
        raise RuntimeError("请先把 Elsewhere.app 拖入「应用程序」再更新")
    script = Path(tempfile.gettempdir()) / f"elsewhere-update-{os.getpid()}.sh"
    script.write_text(MACOS_SCRIPT, encoding="utf-8")
    script.chmod(0o755)
    _spawn(
        ["/bin/bash", str(script), str(os.getpid()), str(archive), str(app)],
        start_new_session=True,
    )
