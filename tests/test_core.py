"""核心逻辑自检：坐标解析、mock 设备、路线几何、两种播放执行器、更新检查。

不启动 Qt 界面，不访问网络。
"""
import json
import pathlib
import queue
import sys
import tempfile
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))

import paths
from inject import Coordinate, Device, InjectionError, MockDevice, parse_coordinate
from route import (
    MODE_SMOOTH,
    MODE_TELEPORT,
    Route,
    RouteRunner,
    distance_m,
    load_presets,
    save_presets,
)


class RecordingDevice(Device):
    def __init__(self) -> None:
        self.recorded: list[Coordinate] = []

    @property
    def serial(self) -> str:
        return "REC"

    @property
    def name(self) -> str:
        return "Recording"

    def is_developer_mode_enabled(self) -> bool:
        return True

    def set_location(self, coord: Coordinate) -> None:
        self.recorded.append(coord)

    def clear_location(self) -> None:
        pass


def _wait_finished(events: "queue.Queue", timeout: float = 5.0) -> list[str]:
    errors: list[str] = []
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            kind, payload = events.get(timeout=0.05)
        except queue.Empty:
            continue
        if kind == "error":
            errors.append(str(payload))
        if kind == "finished":
            return errors
    raise AssertionError("播放器未在超时内结束")


def test_parse_coordinate() -> None:
    c = parse_coordinate("30.306066", "120.081277")
    assert c == Coordinate(30.306066, 120.081277)
    assert parse_coordinate(" 30.306066 ", "120.081277") == c
    assert str(c) == "30.306066, 120.081277"

    bad = [("abc", "120"), ("30", "xyz"), ("91", "120"), ("-91", "0"),
           ("30", "181"), ("0", "-180.5")]
    for lat, lon in bad:
        try:
            parse_coordinate(lat, lon)
        except ValueError:
            pass
        else:
            raise AssertionError(f"应当拒绝 ({lat}, {lon})")


def test_mock_device() -> None:
    d = MockDevice(developer_mode=True)
    assert d.is_developer_mode_enabled()
    assert d.current_location is None
    c = Coordinate(30.0, 120.0)
    d.set_location(c)
    assert d.current_location == c
    d.clear_location()
    assert d.current_location is None


def test_distance() -> None:
    # 纬度 1° ≈ 111.2 km
    a = Coordinate(30.0, 120.0)
    b = Coordinate(31.0, 120.0)
    assert 110_000 < distance_m(a, b) < 112_000, distance_m(a, b)
    assert distance_m(a, a) == 0


def test_route_geometry() -> None:
    p0, p1, p2 = Coordinate(30.0, 120.0), Coordinate(30.001, 120.0), Coordinate(30.001, 120.001)
    route = Route(points=[p0, p1, p2], mode=MODE_TELEPORT)
    segs = route.segments()
    assert len(segs) == 3, "闭环应有 3 段（含末→首）"
    total = route.total_length()
    assert total == sum(d for _, _, d in segs)
    assert total > distance_m(p0, p1) + distance_m(p1, p2)

    # 弧长取模：0 与 总长 都落在起点
    assert route.position_at(0) == p0
    assert route.position_at(total) == p0
    # 第一段中点在第一段两端之间
    mid = route.position_at(distance_m(p0, p1) / 2)
    assert p0.latitude <= mid.latitude <= p1.latitude


def test_presets_roundtrip() -> None:
    import route as route_mod

    old = route_mod.PRESETS_PATH
    try:
        route_mod.PRESETS_PATH = pathlib.Path(tempfile.mkdtemp()) / "routes.json"
        r = Route(
            points=[Coordinate(30.0, 120.0), Coordinate(30.001, 120.0)],
            mode=MODE_SMOOTH, speed=2.5, cooldown=0.5, loops=3,
        )
        save_presets({"测试路线": r})
        loaded = load_presets()
        assert "测试路线" in loaded
        got = loaded["测试路线"]
        assert got.points == r.points
        assert (got.mode, got.speed, got.cooldown, got.loops) == (
            r.mode, r.speed, r.cooldown, r.loops
        )
        # 损坏文件不应崩溃
        route_mod.PRESETS_PATH.write_text("{not json", encoding="utf-8")
        assert load_presets() == {}
    finally:
        route_mod.PRESETS_PATH = old


def test_teleport_runner() -> None:
    p0, p1, p2 = Coordinate(30.0, 120.0), Coordinate(30.001, 120.0), Coordinate(30.002, 120.0)
    dev = RecordingDevice()
    events: "queue.Queue" = queue.Queue()
    runner = RouteRunner(dev, Route(points=[p0, p1, p2], mode=MODE_TELEPORT,
                                    cooldown=0.0, loops=2), events)
    runner.start()
    errors = _wait_finished(events)
    assert not errors, errors
    assert dev.recorded == [p0, p1, p2, p0, p1, p2], dev.recorded


def test_smooth_runner() -> None:
    p0, p1 = Coordinate(30.0, 120.0), Coordinate(30.0001, 120.0)  # ~11 m 一段
    dev = RecordingDevice()
    events: "queue.Queue" = queue.Queue()
    runner = RouteRunner(dev, Route(points=[p0, p1], mode=MODE_SMOOTH,
                                    speed=100.0, loops=1), events)
    runner.start()
    errors = _wait_finished(events)
    assert not errors, errors
    assert len(dev.recorded) >= 1
    assert dev.recorded[0] == p0


def test_runner_survives_disconnect() -> None:
    """断连期间写入失败应重试而不是结束播放，恢复后继续。"""
    import route as route_mod

    class FlakyDevice(RecordingDevice):
        def __init__(self) -> None:
            super().__init__()
            self.failures = 2

        def set_location(self, coord: Coordinate) -> None:
            if self.failures:
                self.failures -= 1
                raise InjectionError("设备已断开")
            super().set_location(coord)

    old = route_mod.RECONNECT_RETRY_S
    route_mod.RECONNECT_RETRY_S = 0.05
    try:
        p0, p1 = Coordinate(30.0, 120.0), Coordinate(30.001, 120.0)
        dev = FlakyDevice()
        events: "queue.Queue" = queue.Queue()
        runner = RouteRunner(dev, Route(points=[p0, p1], mode=MODE_TELEPORT,
                                        cooldown=0.0, loops=1), events)
        runner.start()
        seen: list[str] = []
        end = time.monotonic() + 5.0
        while time.monotonic() < end:
            try:
                kind, payload = events.get(timeout=0.05)
            except queue.Empty:
                continue
            seen.append(kind)
            assert kind != "error", payload
            if kind == "finished":
                break
        else:
            raise AssertionError("播放器未在超时内结束")
    finally:
        route_mod.RECONNECT_RETRY_S = old

    assert dev.recorded == [p0, p1], dev.recorded
    assert "disconnected" in seen and "connected" in seen, seen
    assert seen.index("disconnected") < seen.index("connected")


def test_search_parse() -> None:
    import json

    from geocode import parse_places, search_url

    url = search_url("西湖 杭州")
    assert "nominatim.openstreetmap.org" in url
    assert "format=jsonv2" in url and "limit=5" in url

    raw = json.dumps([
        {"display_name": "西湖, 杭州市, 浙江省", "lat": "30.240000", "lon": "120.140000"},
        {"display_name": "坏数据"},
    ]).encode()
    places = parse_places(raw)
    assert len(places) == 1
    assert places[0].label == "西湖, 杭州市, 浙江省"
    assert places[0].coordinate == Coordinate(30.24, 120.14)
    assert parse_places(b"{not json") == []


def test_user_file_migration() -> None:
    """旧版放在源码目录的预设，首次运行复制到用户数据目录；已有文件不覆盖。"""
    old_dir = paths.APP_DIR
    with tempfile.TemporaryDirectory() as td:
        paths.APP_DIR = pathlib.Path(td) / "appdata"
        legacy = pathlib.Path(td) / "routes.json"
        legacy.write_text("legacy", encoding="utf-8")
        try:
            target = paths.user_file("routes.json", legacy=legacy)
            assert target.read_text(encoding="utf-8") == "legacy"

            target.write_text("new", encoding="utf-8")
            paths.user_file("routes.json", legacy=legacy)
            assert target.read_text(encoding="utf-8") == "new"
        finally:
            paths.APP_DIR = old_dir


def test_version_compare() -> None:
    from updater import is_newer

    assert is_newer("v0.4.0", "0.3.0")
    assert is_newer("0.3.1", "0.3")
    assert not is_newer("0.3.0", "0.3.0")
    assert not is_newer("v0.3.0", "0.10.0")


def test_release_assets() -> None:
    from updater import parse_release, pick_asset

    release = parse_release(json.dumps({
        "tag_name": "v0.4.0",
        "assets": [
            {"name": "Elsewhere-0.4.0-portable.zip", "browser_download_url": "u1"},
            {"name": "Elsewhere-0.4.0-arm64.dmg", "browser_download_url": "u2"},
        ],
    }).encode())
    assert release is not None and release.version == "0.4.0"
    assert pick_asset(release.assets, "win32")["browser_download_url"] == "u1"
    assert pick_asset(release.assets, "darwin")["browser_download_url"] == "u2"
    assert pick_asset(release.assets, "linux") is None
    assert parse_release(b"{not json") is None


def test_platform_paths() -> None:
    """macOS 用户目录是 ~/Library/Application Support/Elsewhere，不是 ~/Elsewhere。"""
    old = paths.sys.platform
    try:
        paths.sys.platform = "darwin"
        mac = paths._app_dir()
        assert mac.name == "Elsewhere"
        assert mac.parent.name == "Application Support"
        assert mac.parent.parent.name == "Library"
        paths.sys.platform = "win32"
        assert paths._app_dir().name == "Elsewhere"
    finally:
        paths.sys.platform = old


if __name__ == "__main__":
    test_parse_coordinate()
    test_mock_device()
    test_distance()
    test_route_geometry()
    test_presets_roundtrip()
    test_teleport_runner()
    test_smooth_runner()
    test_runner_survives_disconnect()
    test_search_parse()
    test_user_file_migration()
    test_version_compare()
    test_release_assets()
    test_platform_paths()
    print("OK")
