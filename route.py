"""路线：几何计算、预设持久化、后台播放执行器。

路线恒为闭环：末点与首点相连（保存时自动连接）。
- 跳点传送：逐点瞬移，点间按冷却等待。
- 多点路线：沿闭环折线按速度平滑移动（约 10 Hz 刷新）。
"""
from __future__ import annotations

import json
import math
import queue
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from inject import Coordinate, Device, InjectionError

EARTH_RADIUS_M = 6371008.8
MODE_TELEPORT = "teleport"
MODE_SMOOTH = "smooth"
DEFAULT_SPEED = 3.0          # m/s
DEFAULT_COOLDOWN = 1.0       # s
SMOOTH_TICK_S = 0.1
RECONNECT_RETRY_S = 2.0      # 断连后重试注入的间隔
# 预设随应用目录存放，迁移时一并带走。
PRESETS_PATH = Path(__file__).resolve().parent / "routes.json"


def distance_m(a: Coordinate, b: Coordinate) -> float:
    """两点球面距离（米，haversine）。"""
    lat1, lon1 = math.radians(a.latitude), math.radians(a.longitude)
    lat2, lon2 = math.radians(b.latitude), math.radians(b.longitude)
    dlat, dlon = lat2 - lat1, lon2 - lon1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(h))


@dataclass
class Route:
    points: list[Coordinate] = field(default_factory=list)
    mode: str = MODE_TELEPORT
    speed: float = DEFAULT_SPEED        # 平滑模式 m/s
    cooldown: float = DEFAULT_COOLDOWN  # 跳点模式 秒
    loops: int = 1                      # 0 = 无限循环
    closed: bool = True                 # 恒为闭环

    def segments(self) -> list[tuple[Coordinate, Coordinate, float]]:
        """闭环折线的各段（含末点→首点）。"""
        n = len(self.points)
        if n < 2:
            return []
        segs = []
        for i in range(n):
            a, b = self.points[i], self.points[(i + 1) % n]
            d = distance_m(a, b)
            if d > 0:
                segs.append((a, b, d))
        return segs

    def total_length(self) -> float:
        return sum(d for _, _, d in self.segments())

    def position_at(self, arc: float) -> Coordinate:
        """闭合折线上弧长 arc 处的坐标（按总长取模）。"""
        segs = self.segments()
        total = sum(d for _, _, d in segs)
        if total <= 0:
            return self.points[0]
        arc %= total
        for a, b, d in segs:
            if arc <= d:
                t = arc / d
                return Coordinate(
                    round(a.latitude + (b.latitude - a.latitude) * t, 6),
                    round(a.longitude + (b.longitude - a.longitude) * t, 6),
                )
            arc -= d
        return self.points[0]

    def to_dict(self) -> dict:
        return {
            "points": [{"lat": p.latitude, "lon": p.longitude} for p in self.points],
            "mode": self.mode,
            "speed": self.speed,
            "cooldown": self.cooldown,
            "loops": self.loops,
            "closed": self.closed,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Route":
        return cls(
            points=[Coordinate(float(p["lat"]), float(p["lon"])) for p in d["points"]],
            mode=d.get("mode", MODE_TELEPORT),
            speed=float(d.get("speed", DEFAULT_SPEED)),
            cooldown=float(d.get("cooldown", DEFAULT_COOLDOWN)),
            loops=int(d.get("loops", 1)),
            closed=bool(d.get("closed", True)),
        )


def load_presets() -> dict[str, Route]:
    if not PRESETS_PATH.exists():
        return {}
    try:
        data = json.loads(PRESETS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    out: dict[str, Route] = {}
    for name, raw in (data.get("presets") or {}).items():
        try:
            out[name] = Route.from_dict(raw)
        except (KeyError, TypeError, ValueError):
            continue
    return out


def save_presets(presets: dict[str, Route]) -> None:
    data = {"version": 1, "presets": {name: r.to_dict() for name, r in presets.items()}}
    tmp = PRESETS_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(PRESETS_PATH)  # 原子替换，避免写一半损坏


class RouteRunner:
    """在后台线程播放路线；进度经 queue 交给 UI（UI 线程不得直接操作控件）。"""

    def __init__(self, device: Device, route: Route, events: "queue.Queue") -> None:
        self.device = device
        self.route = route
        self.events = events
        self._stop = threading.Event()
        self._pause = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._disconnected = False

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def paused(self) -> bool:
        return self._pause.is_set()

    def start(self) -> None:
        if self.running:
            return
        self._stop.clear()
        self._pause.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def pause(self) -> None:
        self._pause.set()

    def resume(self) -> None:
        self._pause.clear()

    def stop(self) -> None:
        self._stop.set()
        self._pause.clear()
        if self._thread is not None:
            self._thread.join(timeout=5)
        self._thread = None

    # --- 内部 ---
    def _write(self, coord: Coordinate) -> tuple[bool, bool]:
        """注入坐标；断连时每 RECONNECT_RETRY_S 秒重试，直到成功或停止。

        返回 (是否继续, 是否经历重试)。重试期间调用线程被占用，路线进度随之冻结。
        """
        retried = False
        while not self._stop.is_set():
            try:
                self.device.set_location(coord)
            except InjectionError as exc:
                retried = True
                if not self._disconnected:
                    self._disconnected = True
                    self.events.put(("disconnected", str(exc)))
                self._stop.wait(RECONNECT_RETRY_S)
                continue
            if self._disconnected:
                self._disconnected = False
                self.events.put(("connected", None))
            return True, retried
        return False, retried

    def _wait(self, seconds: float) -> bool:
        """可中断等待；暂停期间冻结计时。返回 False 表示已停止。"""
        remaining = seconds
        last = time.monotonic()
        while True:
            if self._stop.is_set():
                return False
            if self._pause.is_set():
                time.sleep(0.03)
                last = time.monotonic()
                continue
            if remaining <= 0:
                return True
            now = time.monotonic()
            remaining -= now - last
            last = now
            if remaining > 0:
                time.sleep(min(0.03, remaining))
            else:
                return True

    def _run(self) -> None:
        try:
            if self.route.mode == MODE_SMOOTH:
                self._run_smooth()
            else:
                self._run_teleport()
        except Exception as exc:  # 线程内异常不能上抛
            self.events.put(("error", str(exc)))
        finally:
            self.events.put(("finished", None))

    def _run_teleport(self) -> None:
        points = self.route.points
        n = len(points)
        if n == 0:
            raise ValueError("路线没有点")
        total = self.route.loops * n if self.route.loops > 0 else None
        k = 0
        while not self._stop.is_set():
            if total is not None and k >= total:
                return
            if not self._wait(self.route.cooldown if k > 0 else 0.0):
                return
            point = points[k % n]
            if not self._write(point)[0]:
                return
            self.events.put(("progress", (k // n, k % n, point)))
            k += 1

    def _run_smooth(self) -> None:
        points = self.route.points
        if len(points) < 2:
            raise ValueError("平滑移动至少需要 2 个点")
        total = self.route.total_length()
        if total <= 0:
            raise ValueError("路线总长度为 0")
        if not self._wait(0.0):
            return
        if not self._write(points[0])[0]:
            return
        self.events.put(("progress", (0, 0, points[0])))
        arc = 0.0
        last = time.monotonic()
        while not self._stop.is_set():
            if self._pause.is_set():
                if not self._wait(0.0):
                    return
                last = time.monotonic()
                continue
            now = time.monotonic()
            arc += self.route.speed * (now - last)
            last = now
            if self.route.loops > 0 and arc >= self.route.loops * total:
                return
            pos = self.route.position_at(arc)
            ok, retried = self._write(pos)
            if not ok:
                return
            if retried:
                last = time.monotonic()  # 断连期间不推进弧长
            self.events.put(("progress", (int(arc // total), -1, pos)))
            time.sleep(SMOOTH_TICK_S)
