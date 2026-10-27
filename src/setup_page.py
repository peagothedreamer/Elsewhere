"""设备准备向导：连接 → 信任 → 开发者模式，逐步检测、自动进入下一步。

检测与配对都在后台线程跑，结果经 queue 回到 UI 线程（与主窗口的 events 模式一致）。
"""
from __future__ import annotations

import queue
import sys
import threading
import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

import theme_c
from apple_driver import amds_installed, install_apple_driver, usbmux_ready
from inject import DeviceLockedError, InjectionError, _run_sync, list_devices

# 状态 → 页面。缺省（error / locked）留在当前页。
PAGES = {"absent": 0, "driver": 0, "trust": 1, "devmode": 2, "ready": 3}

COUNTERS = ["01 / 03", "02 / 03", "03 / 03", "完成"]
ACTIONS = ["重新检测", "重新配对", "重新执行", "开始使用"]

QSS = f"""
QDialog#SetupDialog {{
    background: {theme_c.PAPER};
}}
QLabel#GuideHead {{
    color: {theme_c.INK};
    font-size: 16px;
    font-weight: 600;
}}
QLabel#GuideStep {{
    color: {theme_c.MAGENTA};
    font-family: "Cambria", "Georgia";
    font-size: 12px;
}}
QLabel#GuideTitle {{
    color: {theme_c.INK};
    font-size: 14px;
    font-weight: 600;
}}
QLabel#GuideBody {{
    color: {theme_c.INK2};
    font-size: 12px;
}}
QLabel#GuideCmd {{
    background: {theme_c.PAPER2};
    border: 1px solid {theme_c.LINE};
    color: {theme_c.INK};
    font-family: "Consolas", "Menlo", "Courier New", monospace;
    font-size: 12px;
    padding: 7px 9px;
}}
QLabel#GuideStatus {{
    color: {theme_c.MUTED};
    font-size: 12px;
}}
QFrame#DialogRule {{
    border: none;
    border-top: 1px solid {theme_c.LINE};
    background: transparent;
}}
QPushButton#GuidePrimary {{
    background: {theme_c.INK};
    border: 1px solid {theme_c.INK};
    color: {theme_c.PAPER};
    font-size: 13px;
    font-weight: 600;
    min-height: 36px;
    padding: 0 18px;
}}
QPushButton#GuidePrimary:hover {{
    background: #132029;
}}
QPushButton#GuidePrimary:pressed {{
    background: #0A1116;
    border-color: {theme_c.MAGENTA};
    padding-top: 2px;
}}
QPushButton#GuidePrimary:disabled {{
    background: {theme_c.FAINT};
    border-color: {theme_c.FAINT};
    color: {theme_c.PAPER2};
}}
QPushButton#GuideLater {{
    background: transparent;
    border: none;
    color: {theme_c.INK2};
    font-size: 12px;
    padding: 0;
    text-align: left;
}}
QPushButton#GuideLater:hover {{
    color: {theme_c.INK};
}}
QPushButton#GuideLater:pressed {{
    color: {theme_c.INK};
    background: #EADFCB;
}}
"""


def apple_driver_missing() -> bool:
    """Windows 上是否缺 Apple 设备驱动（AMDS 未注册且没有可用的 usbmux）。"""
    if sys.platform != "win32":
        return False
    return not (amds_installed() or usbmux_ready())


def probe_device() -> tuple[str, str]:
    """返回 (状态, 说明)。状态：absent / driver / trust / devmode / ready / error。"""
    try:
        devices = list_devices(use_mock=False)
    except Exception as exc:
        return "error", str(exc)
    if not devices:
        return ("driver", "") if apple_driver_missing() else ("absent", "")
    dev = devices[0]
    try:
        if dev.is_developer_mode_enabled():
            return "ready", dev.name
        return "devmode", dev.name
    except DeviceLockedError as exc:
        return "locked", str(exc)
    except InjectionError as exc:
        return "trust", str(exc)
    except Exception as exc:
        return "error", str(exc)


def _pair_error(message: str) -> str:
    low = message.lower()
    if "refused" in low or "trust" in low:
        return "手机拒绝了信任。解锁后拔线重插，再点「重新配对」。"
    return message


def pair_device() -> tuple[bool, str]:
    """配对第一台可见设备；手机上会弹出「信任此电脑」。"""
    async def _pair() -> None:
        from pymobiledevice3.lockdown import create_using_usbmux
        from pymobiledevice3.usbmux import list_devices as usbmux_list

        found = await usbmux_list()
        if not found:
            raise RuntimeError("未检测到设备")
        ld = await create_using_usbmux(serial=found[0].serial, autopair=True)
        await ld.close()

    try:
        _run_sync(_pair(), timeout=120)
        return True, ""
    except Exception as exc:
        return False, str(exc)


def reveal_device() -> tuple[bool, str]:
    """让「开发者模式」出现在设置里（不重启、不改状态）。

    注意不能用 `--userspace`：那会走隧道，开发者模式没开时服务起不来。
    """
    async def _reveal() -> None:
        from pymobiledevice3.lockdown import create_using_usbmux
        from pymobiledevice3.services.amfi import AmfiService
        from pymobiledevice3.usbmux import list_devices as usbmux_list

        found = await usbmux_list()
        if not found:
            raise RuntimeError("未检测到设备")
        ld = await create_using_usbmux(serial=found[0].serial, autopair=False)
        try:
            await AmfiService(ld).reveal_developer_mode_option_in_ui()
        finally:
            await ld.close()

    try:
        _run_sync(_reveal(), timeout=30)
        return True, ""
    except Exception as exc:
        return False, str(exc)


class SetupDialog(QDialog):
    def __init__(self, state: str = "absent", parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("SetupDialog")
        self.setWindowTitle("准备 iPhone")
        self.setModal(True)
        self.setFixedWidth(520)
        self.setStyleSheet(QSS)

        self._page = -1
        self._state = state
        self._shown = False
        self._closed = False
        self._probing = False
        self._installing = False
        self._pairing = False
        self._pair_tried = False
        self._revealing = False
        self._reveal_tried = False
        self._reveal_ok = False
        self._last_probe = 0.0
        self._probe_interval = 2.5
        self._q: queue.Queue = queue.Queue()

        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 20, 24, 20)
        outer.setSpacing(12)

        head = QHBoxLayout()
        title = QLabel("准备设备")
        title.setObjectName("GuideHead")
        self.counter = QLabel("")
        self.counter.setObjectName("GuideStep")
        head.addWidget(title)
        head.addStretch(1)
        head.addWidget(self.counter)
        outer.addLayout(head)

        line = QFrame()
        line.setObjectName("DialogRule")
        line.setFixedHeight(1)
        outer.addWidget(line)

        self.stack = QStackedWidget()
        self.stack.setMinimumHeight(190)
        self._build_pages()
        outer.addWidget(self.stack, 1)

        self.status = QLabel("")
        self.status.setObjectName("GuideStatus")
        self.status.setWordWrap(True)
        outer.addWidget(self.status)

        bottom = QHBoxLayout()
        self.later_btn = QPushButton("稍后")
        self.later_btn.setObjectName("GuideLater")
        self.later_btn.clicked.connect(self.reject)
        self.action_btn = QPushButton("")
        self.action_btn.setObjectName("GuidePrimary")
        self.action_btn.clicked.connect(self._on_action)
        bottom.addWidget(self.later_btn)
        bottom.addStretch(1)
        bottom.addWidget(self.action_btn)
        outer.addLayout(bottom)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(500)

        start = {"absent": 0, "driver": 0, "locked": 1, "trust": 1, "devmode": 2, "ready": 3}
        self._set_page(start.get(state, 0))
        self._set_status(*{
            "driver": ("未检测到 Apple 设备驱动，点「自动安装驱动」。", "error"),
            "locked": ("iPhone 已锁定，解锁后自动继续。", "busy"),
            "trust": ("等待手机信任这台电脑…", "busy"),
            "devmode": ("开发者模式未开启，打开后自动继续。", "busy"),
            "ready": ("已就绪", "ok"),
        }.get(state, ("未检测到 iPhone，插好后自动继续。", "muted")))
        for button in self.findChildren(QPushButton):
            button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.adjustSize()

    # ---------------- 页面 ----------------
    def _build_pages(self) -> None:
        if sys.platform == "darwin":
            connect_hint = (
                "数据线连接 iPhone 与电脑，保持解锁。"
                "macOS 自带 usbmuxd，无需安装 iTunes 或任何驱动。"
            )
        else:
            connect_hint = (
                "数据线连接 iPhone 与电脑，保持解锁。缺少驱动时点下方「自动安装驱动」"
                "（下载 Apple Mobile Device Support）；也可以手动安装 iTunes 或「Apple 设备」。"
            )
        specs = [
            (
                "连接电脑",
                connect_hint,
                None,
            ),
            (
                "解锁并信任",
                "解锁 iPhone；若弹出「要信任此电脑吗？」→ 点「信任」→ 输入锁屏密码。"
                "没有弹窗就拔掉重插。",
                None,
            ),
            (
                "开启开发者模式",
                "设置 → 隐私与安全性 → 开发者模式 → 打开 → 重启 iPhone → 确认。",
                None,
            ),
            (
                "设备已就绪",
                "iPhone 已连接，开发者模式已开启。",
                None,
            ),
        ]
        for title, body, cmd in specs:
            self.stack.addWidget(self._page_widget(title, body, cmd))

    @staticmethod
    def _page_widget(title: str, body: str, cmd: str | None) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 14, 0, 0)
        lay.setSpacing(8)
        head = QLabel(title)
        head.setObjectName("GuideTitle")
        text = QLabel(body)
        text.setObjectName("GuideBody")
        text.setWordWrap(True)
        lay.addWidget(head)
        lay.addWidget(text)
        if cmd:
            line = QLabel(cmd)
            line.setObjectName("GuideCmd")
            line.setWordWrap(True)
            line.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            lay.addWidget(line)
        lay.addStretch(1)
        return w

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not self._shown:
            self._shown = True
            if self._page == 1 and self._state == "trust":
                self._maybe_pair()
            elif self._page == 2 and self._state == "devmode":
                self._maybe_reveal()

    def done(self, result: int) -> None:
        self._closed = True
        self._timer.stop()
        super().done(result)

    # ---------------- 状态机 ----------------
    def _set_page(self, index: int) -> None:
        if index == self._page:
            return
        self._page = index
        self.stack.setCurrentIndex(index)
        self.counter.setText(COUNTERS[index])
        self._refresh_action_text()
        if index == 1 and self._shown and self._state == "trust":
            self._maybe_pair()
        elif index == 2 and self._shown and self._state == "devmode":
            self._maybe_reveal()

    def _refresh_action_text(self) -> None:
        if self._installing:
            return
        text = ACTIONS[self._page]
        if self._page == 0 and self._state == "driver":
            text = "自动安装驱动"
        self.action_btn.setText(text)

    def _set_status(self, text: str, tone: str = "muted") -> None:
        color = {
            "ok": theme_c.OK,
            "busy": theme_c.MAGENTA,
            "error": theme_c.DANGER,
        }.get(tone, theme_c.MUTED)
        self.status.setText(text)
        self.status.setStyleSheet(f"color:{color};")

    def _on_action(self) -> None:
        if self._installing:
            return
        if self._page == 3:
            self.accept()
        elif self._page == 0 and self._state == "driver":
            self._install_driver_now()
        elif self._page == 1:
            self._pair_now()
        elif self._page == 2:
            self._reveal_now()
            self._probe_now()
        else:
            self._probe_now()

    # ---------------- 后台检测 ----------------
    def _tick(self) -> None:
        self._drain()
        if self._installing:  # 安装期间别再插一轮枚举
            return
        now = time.monotonic()
        if not self._probing and now - self._last_probe >= self._probe_interval:
            self._probe_now()

    def _drain(self) -> None:
        while True:
            try:
                kind, payload = self._q.get_nowait()
            except queue.Empty:
                return
            if kind == "probe":
                self._probing = False
                self._apply_state(*payload)
                self._refresh_action_text()
            elif kind == "pair":
                self._pairing = False
                self.action_btn.setEnabled(True)
                self._refresh_action_text()
                ok, message = payload
                if ok:
                    self._set_status("已配对，正在检测…", "ok")
                    self._last_probe = 0.0
                else:
                    self._set_status(f"配对失败：{_pair_error(message)}", "error")
            elif kind == "install_progress":
                pct = payload
                self._set_status(
                    f"正在下载驱动… {pct}%" if pct >= 0 else "正在下载驱动…", "busy"
                )
            elif kind == "install_done":
                self._installing = False
                ok, message = payload
                self.action_btn.setEnabled(True)
                if ok:
                    self._state = "absent"  # 防止检测结果回来前再点一次安装
                    self._set_status("驱动已安装，正在检测…", "ok")
                    self._last_probe = 0.0
                    self._probe_now()
                    self._refresh_action_text()
                else:
                    self._set_status(f"安装失败：{message}", "error")
                    self._refresh_action_text()

    def _apply_state(self, state: str, detail: str) -> None:
        self._state = state
        if state == "error":
            self._set_status(detail or "检测失败", "error")
            return
        if state == "driver":
            self._set_page(0)
            self._set_status("未检测到 Apple 设备驱动，点「自动安装驱动」。", "error")
            return
        if state == "locked":
            if self._page < 1:
                self._set_page(1)
            self._reveal_tried = False  # 解锁后重新尝试显示选项
            self._set_status("iPhone 已锁定，解锁后自动继续。", "busy")
            return
        if state == "absent" and self._page > 0:
            self._set_status("等待设备重新连接…", "busy")
            return
        index = PAGES[state]
        self._set_page(index)
        if state == "trust":
            self._maybe_pair()
            self._set_status(
                "请在手机上点「信任」并输入密码" if self._pairing
                else "等待手机信任这台电脑…",
                "busy",
            )
        elif state == "devmode":
            self._maybe_reveal()
            if self._reveal_ok:
                self._set_status("按照步骤打开开发者模式。", "ok")
            else:
                self._set_status("开发者模式未开启，打开后自动继续。", "busy")
        elif state == "ready":
            self._set_status("已就绪", "ok")
        else:
            self._set_status("未检测到 iPhone，插好后自动继续。", "muted")

    def _probe_now(self) -> None:
        if self._probing or self._closed:
            return
        self._probing = True
        self._last_probe = time.monotonic()
        threading.Thread(target=self._probe_worker, daemon=True).start()

    def _probe_worker(self) -> None:
        try:
            result = probe_device()
        except Exception as exc:
            result = ("error", str(exc))
        self._q.put(("probe", result))

    # ---------------- 自动安装 Apple 驱动 ----------------
    def _install_driver_now(self) -> None:
        if self._installing or self._closed:
            return
        self._installing = True
        self.action_btn.setEnabled(False)
        self.action_btn.setText("正在安装…")
        self._set_status("正在下载 Apple 驱动…", "busy")
        threading.Thread(target=self._install_worker, daemon=True).start()

    def _install_worker(self) -> None:
        def progress(done: int, total: int) -> None:
            self._q.put(("install_progress", done * 100 // total if total else -1))

        try:
            install_apple_driver(progress)
            self._q.put(("install_done", (True, "")))
        except Exception as exc:
            self._q.put(("install_done", (False, str(exc))))

    # ---------------- 显示开发者模式选项 ----------------
    def _maybe_reveal(self) -> None:
        if (
            not self._shown or self._state != "devmode"
            or self._reveal_tried or self._revealing
        ):
            return
        self._reveal_tried = True
        self._reveal_now()

    def _reveal_now(self) -> None:
        if self._revealing or self._closed:
            return
        self._revealing = True
        # 统一按“已显示”处理：命令结果不再回传 UI。
        self._reveal_ok = True
        self._set_status("按照步骤打开开发者模式。", "ok")
        threading.Thread(target=self._reveal_worker, daemon=True).start()

    def _reveal_worker(self) -> None:
        reveal_device()
        self._revealing = False

    # ---------------- 配对 ----------------
    def _maybe_pair(self) -> None:
        if not self._shown or self._state != "trust" or self._pair_tried or self._pairing:
            return
        self._pair_tried = True
        self._pair_now()

    def _pair_now(self) -> None:
        if self._pairing or self._closed:
            return
        self._pairing = True
        self.action_btn.setEnabled(False)
        self.action_btn.setText("等待手机确认…")
        self._set_status("请在手机上点「信任」并输入密码", "busy")
        threading.Thread(target=self._pair_worker, daemon=True).start()

    def _pair_worker(self) -> None:
        ok, message = pair_device()
        self._q.put(("pair", (ok, message)))
