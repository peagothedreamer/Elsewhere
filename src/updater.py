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
import time
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject, QUrl, Signal
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest

APP_VERSION = "1.0.2"  # 发布时由 CI 按 tag 覆盖
REPO = "peagothedreamer/Elsewhere"
# 不走 api.github.com：未认证 API 每个 IP 每小时只有 60 次，共享出口常被耗尽（403）。
# releases/latest 会 302 到 /releases/tag/<tag>，网页请求没有这个限制。
LATEST_URL = f"https://github.com/{REPO}/releases/latest"
USER_AGENT = f"Elsewhere/{APP_VERSION}"

PROGRESS_SCRIPT = r"""param([int]$WaitPid, [string]$Text)
# 只在旧进程退出后露面：更新期间别留一段「什么都没有」的空白。
try { Wait-Process -Id $WaitPid -Timeout 180 -ErrorAction SilentlyContinue } catch {}
Start-Sleep -Milliseconds 300
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
[System.Windows.Forms.Application]::EnableVisualStyles()
$form = New-Object System.Windows.Forms.Form
$form.Text = 'Elsewhere 更新'
$form.ClientSize = New-Object System.Drawing.Size(360, 96)
$form.StartPosition = 'CenterScreen'
$form.FormBorderStyle = 'FixedToolWindow'
$form.ControlBox = $false
$form.TopMost = $true
$form.ShowInTaskbar = $false
$label = New-Object System.Windows.Forms.Label
$label.Text = $Text
$label.Dock = 'Fill'
$label.TextAlign = 'MiddleCenter'
$label.Font = [System.Drawing.SystemFonts]::MessageBoxFont
$form.Controls.Add($label)
$timer = New-Object System.Windows.Forms.Timer
$timer.Interval = 300000
$timer.Add_Tick({ $form.Close() })
$timer.Start()
[System.Windows.Forms.Application]::Run($form)
Remove-Item -Force $PSCommandPath -ErrorAction SilentlyContinue
"""

WINDOWS_SCRIPT = r"""param([int]$WaitPid, [string]$Zip, [string]$AppDir, [string]$Marker, [int]$ProgressPid, [string]$ProgressScript)
$log = Join-Path ([IO.Path]::GetTempPath()) 'Elsewhere-update.log'
function Log([string]$m) {
  try { Add-Content -LiteralPath $log -Value ("[{0}] {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $m) } catch {}
}
# 握手文件：主进程确认脚本真的跑起来了才退出，避免「关了但什么也没发生」。
Set-Content -LiteralPath $Marker -Value $PID -ErrorAction SilentlyContinue
Log "start pid=$WaitPid zip=$Zip app=$AppDir"
$tmp = Join-Path ([IO.Path]::GetTempPath()) ([guid]::NewGuid().ToString('N'))
$ok = $false
try {
  try { Wait-Process -Id $WaitPid -Timeout 180 -ErrorAction SilentlyContinue } catch {}
  Start-Sleep -Milliseconds 800
  # 进程退出后文件锁还要一会儿才释放：等 Elsewhere.exe 可以独占写。
  $exe = Join-Path $AppDir 'Elsewhere.exe'
  for ($i = 0; $i -lt 60; $i++) {
    try { $f = [IO.File]::Open($exe, 'Open', 'ReadWrite', 'None'); $f.Close(); break }
    catch { Start-Sleep -Milliseconds 500 }
  }
  Log 'extract'
  Add-Type -AssemblyName System.IO.Compression.FileSystem
  [IO.Compression.ZipFile]::ExtractToDirectory($Zip, $tmp)
  $src = Join-Path $tmp 'Elsewhere'
  if (-not (Test-Path (Join-Path $src 'Elsewhere.exe'))) { $src = $tmp }
  Log "copy $src -> $AppDir"
  & robocopy $src $AppDir /E /IS /IT /R:10 /W:1 /NFL /NDL /NJH /NJS /NP | Out-Null
  $rc = $LASTEXITCODE
  Log "robocopy rc=$rc"
  $ok = $rc -lt 8
} catch {
  Log ("error: " + $_.Exception.Message)
} finally {
  Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
  Remove-Item -Force $Zip -ErrorAction SilentlyContinue
}
if (-not $ok) {
  try {
    Add-Type -AssemblyName System.Windows.Forms
    [System.Windows.Forms.MessageBox]::Show("更新失败，详见日志：$log", 'Elsewhere') | Out-Null
  } catch {}
}
if ($ProgressPid -gt 0) { Stop-Process -Id $ProgressPid -Force -ErrorAction SilentlyContinue }
Remove-Item -Force $ProgressScript -ErrorAction SilentlyContinue
Start-Process -FilePath (Join-Path $AppDir 'Elsewhere.exe') -WorkingDirectory $AppDir
Remove-Item -Force $PSCommandPath -ErrorAction SilentlyContinue
"""

MACOS_SCRIPT = r"""#!/bin/bash
pid=$1; dmg=$2; app=$3
while kill -0 "$pid" 2>/dev/null; do sleep 0.5; done
osascript -e 'display notification "正在更新，完成后会自动重启" with title "Elsewhere"' >/dev/null 2>&1
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


def _log(message: str) -> None:
    """更新日志固定写 %TEMP%\\Elsewhere-update.log，失败时可以让人翻。"""
    try:
        path = Path(tempfile.gettempdir()) / "Elsewhere-update.log"
        with path.open("a", encoding="utf-8") as stream:
            stream.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] app: {message}\n")
    except OSError:
        pass


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
    temp = Path(tempfile.gettempdir())
    pid = os.getpid()
    script = temp / f"elsewhere-update-{pid}.ps1"
    progress_script = temp / f"elsewhere-progress-{pid}.ps1"
    marker = temp / f"elsewhere-update-{pid}.started"
    script.write_text(WINDOWS_SCRIPT, encoding="utf-8-sig")
    progress_script.write_text(PROGRESS_SCRIPT, encoding="utf-8-sig")
    marker.unlink(missing_ok=True)
    flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    # 进度窗自己等旧进程退出后再出现，避免压在主界面上。
    progress = None
    try:
        progress = subprocess.Popen(
            [
                "powershell", "-NoProfile", "-WindowStyle", "Hidden",
                "-ExecutionPolicy", "Bypass", "-File", str(progress_script),
                "-WaitPid", str(pid),
                "-Text", "正在更新，完成后会自动重启…",
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            creationflags=flags,
        )
    except OSError as exc:
        _log(f"progress window failed: {exc}")
    _log(f"spawn pid={pid} zip={archive} app={Path(sys.executable).resolve().parent}")
    try:
        _spawn(
            [
                "powershell", "-NoProfile", "-WindowStyle", "Hidden",
                "-ExecutionPolicy", "Bypass", "-File", str(script),
                "-WaitPid", str(pid),
                "-Zip", str(archive),
                "-AppDir", str(Path(sys.executable).resolve().parent),
                "-Marker", str(marker),
                "-ProgressPid", str(progress.pid if progress else 0),
                "-ProgressScript", str(progress_script),
            ],
            creationflags=flags,
        )
    except OSError as exc:
        if progress is not None:
            progress.kill()
        _log(f"spawn failed: {exc}")
        raise
    # 脚本被安全软件拦掉时，进程会静默消失；只有收到握手文件才关闭自己。
    deadline = time.time() + 8
    while time.time() < deadline and not marker.exists():
        time.sleep(0.1)
    if not marker.exists():
        if progress is not None:
            progress.kill()
        _log("script did not start")
        raise RuntimeError("更新程序未能启动（可能被安全软件拦截），请手动下载安装包")


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
