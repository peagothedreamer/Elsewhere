"""Windows: 自动下载并安装 Apple Mobile Device Support (AMDS)。

pymobiledevice3 在 Windows 上通过 127.0.0.1:27015 的 usbmuxd 发现设备，
该端口由 AMDS 提供。iTunes / 「Apple 设备」都只是 AMDS 的安装器；这里
直接装 Apple 官方 CDN 上的独立 MSI（winget Apple.AppleMobileDeviceSupport 同源）。
"""
from __future__ import annotations

import ctypes
import hashlib
import socket
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

# 版本 19.4.0.10；URL/哈希来自 winget 的 Apple.AppleMobileDeviceSupport 清单。
# ponytail: 苹果更新版本后需同步改这两行，失败时会报“校验失败”而不是装错包。
AMDS_URL = (
    "https://swcdn.apple.com/content/downloads/20/49/047-76422/"
    "qcw2a7028lr8yp4rdkrfyzkvlhi9q1gg2g/AppleMobileDeviceSupport64.msi"
)
AMDS_SHA256 = "b60533fb54e7bd81ffc52d99678a9cce04e58cb54a76281bd7b1309f20d360e9"
AMDS_SERVICE = "Apple Mobile Device Service"
USBMUX = ("127.0.0.1", 27015)
USER_AGENT = "Elsewhere/0.1 (iOS location testing tool)"

SEE_MASK_NOCLOSEPROCESS = 0x00000040
ERROR_CANCELLED = 1223


def amds_installed() -> bool:
    """系统里是否注册了 Apple Mobile Device Service。"""
    if sys.platform != "win32":
        return False
    import winreg

    try:
        key = winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SYSTEM\CurrentControlSet\Services\Apple Mobile Device Service",
        )
        winreg.CloseKey(key)
        return True
    except OSError:
        return False


def usbmux_ready(timeout: float = 0.5) -> bool:
    """AMDS 是否已在监听 usbmuxd 端口。"""
    try:
        with socket.create_connection(USBMUX, timeout=timeout):
            return True
    except OSError:
        return False


def _download(dest: Path, progress) -> None:
    """流式下载并校验 SHA-256；progress(done_bytes, total_bytes)。"""
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    digest = hashlib.sha256()
    request = urllib.request.Request(AMDS_URL, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=30) as response, open(part, "wb") as out:
            total = int(response.headers.get("Content-Length") or 0)
            done = 0
            while chunk := response.read(1 << 20):
                out.write(chunk)
                digest.update(chunk)
                done += len(chunk)
                progress(done, total)
        if digest.hexdigest() != AMDS_SHA256:
            raise RuntimeError("驱动安装包校验失败，请稍后重试")
        part.replace(dest)
    finally:
        part.unlink(missing_ok=True)


def _run_elevated(file: str, params: str, timeout: float = 900.0) -> int:
    """以管理员权限启动并等待退出；用户取消 UAC 时抛 RuntimeError。"""
    from ctypes import wintypes

    class SHELLEXECUTEINFOW(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("fMask", wintypes.ULONG),
            ("hwnd", wintypes.HWND),
            ("lpVerb", wintypes.LPCWSTR),
            ("lpFile", wintypes.LPCWSTR),
            ("lpParameters", wintypes.LPCWSTR),
            ("lpDirectory", wintypes.LPCWSTR),
            ("nShow", ctypes.c_int),
            ("hInstApp", wintypes.HINSTANCE),
            ("lpIDList", ctypes.c_void_p),
            ("lpClass", wintypes.LPCWSTR),
            ("hkeyClass", wintypes.HKEY),
            ("dwHotKey", wintypes.DWORD),
            ("hIcon", wintypes.HANDLE),
            ("hProcess", wintypes.HANDLE),
        ]

    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    shell32.ShellExecuteExW.argtypes = [ctypes.POINTER(SHELLEXECUTEINFOW)]
    shell32.ShellExecuteExW.restype = wintypes.BOOL
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    info = SHELLEXECUTEINFOW()
    info.cbSize = ctypes.sizeof(info)
    info.fMask = SEE_MASK_NOCLOSEPROCESS
    info.lpVerb = "runas"
    info.lpFile = file
    info.lpParameters = params
    info.nShow = 0  # SW_HIDE
    if not shell32.ShellExecuteExW(ctypes.byref(info)):
        if ctypes.get_last_error() == ERROR_CANCELLED:
            raise RuntimeError("已取消管理员授权，驱动未安装")
        raise ctypes.WinError(ctypes.get_last_error())
    if not info.hProcess:
        return 0
    try:
        kernel32.WaitForSingleObject(info.hProcess, int(timeout * 1000))
        code = wintypes.DWORD()
        kernel32.GetExitCodeProcess(info.hProcess, ctypes.byref(code))
        return int(code.value)
    finally:
        kernel32.CloseHandle(info.hProcess)


def install_apple_driver(progress) -> None:
    """下载并静默安装 AMDS，然后启动服务。阻塞，需在工作线程调用。"""
    if sys.platform != "win32":
        raise RuntimeError("仅在 Windows 上需要安装 Apple 驱动")

    msi = Path(tempfile.gettempdir()) / "Elsewhere" / "AppleMobileDeviceSupport64.msi"
    if not (msi.is_file() and hashlib.sha256(msi.read_bytes()).hexdigest() == AMDS_SHA256):
        _download(msi, progress)

    log = msi.with_suffix(".log")
    code = _run_elevated(
        "msiexec.exe",
        f'/i "{msi}" /quiet /norestart /l*v "{log}"',
    )
    # 0=成功 3010/1641=成功但需重启 1638=已有更新版本
    if code not in (0, 3010, 1641, 1638) or not amds_installed():
        raise RuntimeError(f"驱动安装失败（退出码 {code}），日志：{log}")

    if not usbmux_ready(2.0):
        try:
            _run_elevated("cmd.exe", f'/c sc start "{AMDS_SERVICE}"')
        except Exception:
            pass
        for _ in range(20):
            if usbmux_ready(0.5):
                break
            time.sleep(0.5)
        else:
            raise RuntimeError("驱动已安装，但服务未能启动；请重启电脑后重试")
