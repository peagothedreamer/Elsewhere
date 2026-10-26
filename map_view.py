"""地图面板：QtWebEngine 嵌本地 Leaflet；瓦片走 Mapbox（有 token 时）或 OSM。"""
from __future__ import annotations

import json
import os
from pathlib import Path

from PySide6.QtCore import QObject, QUrl, Signal, Slot
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineCore import QWebEngineSettings
from PySide6.QtWebEngineWidgets import QWebEngineView

from inject import DEFAULT_LOCATION, Coordinate

MAP_HTML = Path(__file__).resolve().parent / "web" / "map.html"
USER_AGENT = "Elsewhere/0.1 (iOS location testing tool)"
OSM_URL = "https://tile.openstreetmap.de/{z}/{x}/{y}.png"
MAPBOX_URLS = {
    "streets": "https://api.mapbox.com/styles/v1/mapbox/streets-v11/tiles/256/"
               "{z}/{x}/{y}@2x?access_token={token}",
    "satellite": "https://api.mapbox.com/styles/v1/mapbox/satellite-v11/tiles/256/"
                 "{z}/{x}/{y}@2x?access_token={token}",
}
MAPBOX_ATTRIBUTION = {
    "streets": "© Mapbox © OpenStreetMap",
    "satellite": "© Mapbox © Maxar",
}
TOKEN_FILE = Path(__file__).resolve().parent / "mapbox_token.txt"


def mapbox_token() -> str:
    """token 从环境变量 MAPBOX_TOKEN 或同目录 mapbox_token.txt 读取（不写进代码）。"""
    token = os.environ.get("MAPBOX_TOKEN", "").strip()
    if token:
        return token
    try:
        return TOKEN_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


class _Bridge(QObject):
    picked = Signal(float, float)
    ready = Signal()
    tile_failed = Signal()

    @Slot(float, float)
    def pick(self, lat: float, lon: float) -> None:
        self.picked.emit(lat, lon)

    @Slot()
    def on_ready(self) -> None:
        self.ready.emit()

    @Slot()
    def tile_error(self) -> None:
        self.tile_failed.emit()


class MapView(QWebEngineView):
    picked = Signal(object)  # Coordinate
    ready = Signal()
    tile_failed = Signal()  # Mapbox 瓦片失败并已回退 OSM

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.token = mapbox_token()
        self.has_mapbox = bool(self.token)
        self._base_kind = "osm"
        self._fallback_done = False
        self._ready = False
        self._pending: list[str] = []
        self._bridge = _Bridge(self)
        self._bridge.picked.connect(self._on_pick)
        self._bridge.ready.connect(self._on_ready)
        self._bridge.tile_failed.connect(self._on_tile_error)
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
        if self.has_mapbox:
            self.set_base("streets")
        pending, self._pending = self._pending, []
        for script in pending:
            self.page().runJavaScript(script)
        self.ready.emit()

    def _on_pick(self, lat: float, lon: float) -> None:
        self.picked.emit(Coordinate(round(lat, 6), round(lon, 6)))

    def _on_tile_error(self) -> None:
        if self._base_kind == "osm" or self._fallback_done:
            return
        self.set_base("osm")  # 会重置 _fallback_done，故之后重新标记
        self._fallback_done = True
        self.tile_failed.emit()

    # ---------------- 供 UI 调用 ----------------
    def set_base(self, kind: str) -> None:
        """切换底图：streets / satellite（需 token）或 osm。"""
        if kind not in ("streets", "satellite", "osm"):
            return
        if kind != "osm" and not self.token:
            kind = "osm"
        self._base_kind = kind
        self._fallback_done = False
        if kind == "osm":
            url = OSM_URL
            attribution = "© OpenStreetMap contributors"
        else:
            # 只替换 token：URL 里的 {z}/{x}/{y} 是给 Leaflet 的模板，不能过 str.format。
            url = MAPBOX_URLS[kind].replace("{token}", self.token)
            attribution = MAPBOX_ATTRIBUTION[kind]
        self._js(f"setBase({json.dumps(url)}, {json.dumps(attribution)}, 19)")

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
