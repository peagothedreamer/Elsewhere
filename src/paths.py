"""用户数据目录：源码运行和安装版统一放 %APPDATA%\\Elsewhere。

程序目录可能不可写（Program Files），预设放用户目录；
首次运行时把旧版放在源码目录的文件迁移过去。
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

APP_DIR = Path(os.environ.get("APPDATA") or Path.home()) / "Elsewhere"


def user_file(name: str, legacy: Path | None = None) -> Path:
    APP_DIR.mkdir(parents=True, exist_ok=True)
    target = APP_DIR / name
    if legacy is not None and not target.exists() and legacy.exists():
        try:
            shutil.copy2(legacy, target)
        except OSError:
            pass
    return target
