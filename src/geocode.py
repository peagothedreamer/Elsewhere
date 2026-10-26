"""地名搜索：Nominatim（免 key）。需自定义 User-Agent，且每秒最多 1 次请求。"""
from __future__ import annotations

import json
from dataclasses import dataclass
from urllib.parse import urlencode

from PySide6.QtCore import QElapsedTimer, QObject, QTimer, QUrl, Signal
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest

from inject import Coordinate

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
USER_AGENT = "Elsewhere/0.1 (iOS location testing tool)"
MIN_INTERVAL_MS = 1000
MAX_RESULTS = 5


@dataclass(frozen=True)
class Place:
    label: str
    coordinate: Coordinate


def search_url(text: str, limit: int = MAX_RESULTS) -> str:
    query = urlencode({
        "q": text.strip(),
        "format": "jsonv2",
        "limit": limit,
        "accept-language": "zh-CN,zh,en",
    })
    return f"{NOMINATIM_URL}?{query}"


def parse_places(raw: bytes) -> list[Place]:
    try:
        items = json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return []
    places: list[Place] = []
    for item in items if isinstance(items, list) else []:
        try:
            places.append(Place(
                label=str(item["display_name"]),
                coordinate=Coordinate(
                    round(float(item["lat"]), 6), round(float(item["lon"]), 6)
                ),
            ))
        except (KeyError, TypeError, ValueError):
            continue  # 单条坏数据不影响其余结果
    return places


class Geocoder(QObject):
    results = Signal(object)  # list[Place]
    error = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._nam = QNetworkAccessManager(self)
        self._clock = QElapsedTimer()
        self._clock.start()

    def search(self, text: str) -> None:
        text = text.strip()
        if not text:
            return
        wait = MIN_INTERVAL_MS - self._clock.elapsed()
        if wait > 0:  # Nominatim 限流：把过快的请求推迟到 1 秒后
            QTimer.singleShot(wait, self, lambda: self._send(text))
        else:
            self._send(text)

    def _send(self, text: str) -> None:
        self._clock.restart()
        request = QNetworkRequest(QUrl(search_url(text)))
        request.setRawHeader(b"User-Agent", USER_AGENT.encode())
        reply = self._nam.get(request)
        reply.finished.connect(lambda: self._on_reply(reply))

    def _on_reply(self, reply: QNetworkReply) -> None:
        try:
            if reply.error() != QNetworkReply.NetworkError.NoError:
                self.error.emit(f"搜索失败：{reply.errorString()}")
                return
            places = parse_places(bytes(reply.readAll().data()))
        finally:
            reply.deleteLater()
        if not places:
            self.error.emit("未找到匹配地点")
            return
        self.results.emit(places)
