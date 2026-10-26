"""用户数据目录：源码运行和安装版统一放用户数据目录。

Windows: %APPDATA%\\Elsewhere
macOS:   ~/Library/Application Support/Elsewhere
其他:    ~/.local/share/Elsewhere

程序目录可能不可写（Program Files / .app），预设放用户目录；
首次运行时把旧版放在源码目录的文件迁移过去。
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path


def _app_dir() -> Path:
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA") or Path.home()) / "Elsewhere"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Elsewhere"
    return Path.home() / ".local" / "share" / "Elsewhere"


APP_DIR = _app_dir()


def user_file(name: str, legacy: Path | None = None) -> Path:
    APP_DIR.mkdir(parents=True, exist_ok=True)
    target = APP_DIR / name
    if legacy is not None and not target.exists() and legacy.exists():
        try:
            shutil.copy2(legacy, target)
        except OSError:
            pass
    return target
