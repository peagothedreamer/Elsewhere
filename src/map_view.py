"""地图面板：QtWebEngine 嵌本地 Leaflet，瓦片只用 OSM。"""
from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import QObject, QUrl, Signal, Slot
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineCore import QWebEngineSettings
from PySide6.QtWebEngineWidgets import QWebEngineView

from inject import DEFAULT_LOCATION, Coordinate

MAP_HTML = Path(__file__).resolve().parent / "web" / "map.html"
USER_AGENT = "Elsewhere/0.1 (iOS location testing tool)"


class _Bridge(QObject):
    picked = Signal(float, float)
    ready = Signal()

    @Slot(float, float)
    def pick(self, lat: float, lon: float) -> None:
        self.picked.emit(lat, lon)

    @Slot()
    def on_ready(self) -> None:
        self.ready.emit()


class MapView(QWebEngineView):
    picked = Signal(object)  # Coordinate
    ready = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._ready = False
        self._pending: list[str] = []
        self._bridge = _Bridge(self)
        self._bridge.picked.connect(self._on_pick)
        self._bridge.ready.connect(self._on_ready)
        self._channel = QWebChannel(self.page())
        self._channel.registerObject("bridge", self._bridge)
        self.page().setWebChannel(self._channel)
        self.page().profile().setHttpUserAgent(USER_AGENT)
        # file:// 页面既要能读本地 leaflet.js，又要能拉远程瓦片。
        settings = self.settings()
        settings.setAttribute(
            QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True
        )
        settings.setAttribute(
            QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True
        )
        self.load(QUrl.fromLocalFile(str(MAP_HTML)))

    def _js(self, script: str) -> None:
        # 页面就绪前先排队，避免 runJavaScript 丢在加载过程中。
        if self._ready:
            self.page().runJavaScript(script)
        else:
            self._pending.append(script)

    def _on_ready(self) -> None:
        self._ready = True
        lat, lon = DEFAULT_LOCATION
        self.page().runJavaScript(f"init({lat}, {lon}, 15)")
        pending, self._pending = self._pending, []
        for script in pending:
            self.page().runJavaScript(script)
        self.ready.emit()

    def _on_pick(self, lat: float, lon: float) -> None:
        self.picked.emit(Coordinate(round(lat, 6), round(lon, 6)))

    def set_route(self, points: list[Coordinate], closed: bool = True) -> None:
        data = [[p.latitude, p.longitude] for p in points]
        self._js(f"setRoute({json.dumps(data)}, {'true' if closed else 'false'})")

    def set_picked(self, coord: Coordinate) -> None:
        self._js(f"setPicked({coord.latitude}, {coord.longitude})")

    def set_current(self, coord: Coordinate | None) -> None:
        if coord is None:
            self._js("clearCurrent()")
        else:
            self._js(f"setCurrent({coord.latitude}, {coord.longitude})")

    def fly_to(self, coord: Coordinate, zoom: int = 16) -> None:
        self._js(f"flyTo({coord.latitude}, {coord.longitude}, {zoom})")
