"""设备注入层：坐标模型 + 真实设备(pymobiledevice3) / mock 设备，共用同一接口。

iOS 17+ 的定位注入走 DVT（com.apple.instruments server / SimulateLocation），
必须先建立 RSD 隧道（pymobiledevice3 的 no-root userspace 隧道）。该隧道的
PyTCP 栈是进程级单例，故整个进程只保留一个常驻会话并复用（否则第二个隧道必失败）。
"""
from __future__ import annotations

import asyncio
import logging
import queue
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# 默认位置：浙江大学紫金港校区
DEFAULT_LOCATION = (30.308270, 120.078263)

# 全进程共享的后台事件循环：pymobiledevice3 的 DVT/隧道 API 都是异步的。
_loop: asyncio.AbstractEventLoop | None = None
_loop_lock = threading.Lock()


def _bg_loop() -> asyncio.AbstractEventLoop:
    global _loop
    with _loop_lock:
        if _loop is None or _loop.is_closed():
            _loop = asyncio.new_event_loop()
            threading.Thread(target=_loop.run_forever, daemon=True).start()
        return _loop


def _run_sync(coro, timeout: float = 120.0):
    """把协程投到后台 loop 并同步等待结果（阻塞当前线程）。"""
    return asyncio.run_coroutine_threadsafe(coro, _bg_loop()).result(timeout)


class InjectionError(Exception):
    """注入失败（未连接、未开开发者模式、隧道/DDI 失败等）。"""


class DeviceLockedError(InjectionError):
    """设备锁屏：iOS 会拒绝读取开发者模式等状态，解锁后即可恢复。"""


@dataclass(frozen=True)
class Coordinate:
    latitude: float
    longitude: float

    def __str__(self) -> str:
        return f"{self.latitude:.6f}, {self.longitude:.6f}"


def parse_coordinate(lat_text: str, lon_text: str) -> Coordinate:
    """解析经纬度，校验范围并保留 6 位小数（约 0.1 m 级）。非法输入抛 ValueError。"""
    try:
        lat, lon = float(lat_text.strip()), float(lon_text.strip())
    except ValueError as exc:
        raise ValueError("经纬度必须是数字") from exc
    if not -90.0 <= lat <= 90.0:
        raise ValueError(f"纬度 {lat} 超出范围 [-90, 90]")
    if not -180.0 <= lon <= 180.0:
        raise ValueError(f"经度 {lon} 超出范围 [-180, 180]")
    return Coordinate(round(lat, 6), round(lon, 6))


class Device(ABC):
    """注入设备抽象。真实设备与 mock 设备都实现此接口。"""

    @property
    @abstractmethod
    def serial(self) -> str: ...

    @property
    @abstractmethod
    def name(self) -> str: ...

    @abstractmethod
    def is_developer_mode_enabled(self) -> bool:
        """是否已开启开发者模式（定位模拟的前置条件）。"""

    @abstractmethod
    def set_location(self, coord: Coordinate) -> None:
        """把设备定位设为单个坐标。"""

    @abstractmethod
    def clear_location(self) -> None:
        """恢复真实位置。"""

    def close(self) -> None:
        """释放连接资源。默认无操作。"""


class MockDevice(Device):
    """无真机时的假设备，用于离线开发与自测。"""

    def __init__(self, serial: str = "MOCK-0001", name: str = "Mock iPhone",
                 developer_mode: bool = True) -> None:
        self._serial, self._name = serial, name
        self._developer_mode = developer_mode
        self._location: Coordinate | None = None

    @property
    def serial(self) -> str:
        return self._serial

    @property
    def name(self) -> str:
        return self._name

    @property
    def current_location(self) -> Coordinate | None:
        return self._location

    def is_developer_mode_enabled(self) -> bool:
        return self._developer_mode

    def set_location(self, coord: Coordinate) -> None:
        self._location = coord

    def clear_location(self) -> None:
        self._location = None


class RealDevice(Device):
    """通过 pymobiledevice3（iOS 17+ RSD + DVT SimulateLocation）注入。"""

    def __init__(self, serial: str, name: str) -> None:
        self._serial, self._name = serial, name
        # (tunnel, dvt, ls)；建立后跨 set/clear 复用。
        self._session: tuple | None = None
        # 最后成功注入的坐标；断连重连后据此恢复。
        self._location: Coordinate | None = None

    @property
    def serial(self) -> str:
        return self._serial

    @property
    def name(self) -> str:
        return self._name

    @property
    def current_location(self) -> Coordinate | None:
        return self._location

    async def _ensure_session(self):
        if self._session is not None:
            return self._session
        from pymobiledevice3.exceptions import AlreadyMountedError, RSDRequiredError
        from pymobiledevice3.lockdown import create_using_usbmux
        from pymobiledevice3.remote.rsd_tunnel import PreferredRsdTunnel
        from pymobiledevice3.services.dvt.instruments.dvt_provider import DvtProvider
        from pymobiledevice3.services.dvt.instruments.location_simulation import (
            LocationSimulation,
        )
        from pymobiledevice3.services.mobile_image_mounter import (
            MobileImageMounterService,
            auto_mount,
            image_type_for_device,
        )

        # DVT 的定位服务依赖开发者镜像；新机器/重启后 DDI 不会自己挂上。
        # 先走 usbmux/lockdown 挂载（快），iOS 27+ 的 cryptex 镜像只能走 RSD，稍后补。
        # 已挂载时直接跳过：auto_mount 会先下载镜像再检查，离线环境不该因此失败。
        mount_over_rsd = False
        lockdown = await create_using_usbmux(serial=self._serial, autopair=False)
        try:
            mounted = False
            try:
                async with MobileImageMounterService(lockdown=lockdown) as mounter:
                    mounted = await mounter.is_image_mounted(image_type_for_device(lockdown))
            except Exception:
                logger.exception("查询开发者镜像状态失败")
            if mounted:
                logger.info("开发者镜像已在设备上")
            else:
                try:
                    await auto_mount(lockdown)
                    logger.info("开发者镜像已挂载")
                except AlreadyMountedError:
                    logger.info("开发者镜像已在设备上")
                except RSDRequiredError:
                    mount_over_rsd = True
        finally:
            await lockdown.close()

        tunnel = PreferredRsdTunnel(serial=self._serial)
        try:
            rsd = await tunnel.aopen()
            logger.info("RSD 隧道已建立")
            if mount_over_rsd:
                try:
                    await auto_mount(rsd)
                    logger.info("开发者镜像已挂载（RSD）")
                except AlreadyMountedError:
                    logger.info("开发者镜像已在设备上（RSD）")
            dvt = DvtProvider(rsd)
            await dvt.connect()
            ls = LocationSimulation(dvt)
            await ls.connect()
        except BaseException:
            logger.exception("建立注入会话失败")
            await tunnel.aclose()
            raise
        self._session = (tunnel, dvt, ls)
        return self._session

    async def _drop_session(self) -> None:
        session, self._session = self._session, None
        if session is None:
            return
        tunnel, dvt, ls = session
        try:
            await ls.close()
        finally:
            try:
                await dvt.close()
            finally:
                await tunnel.aclose()

    def is_developer_mode_enabled(self) -> bool:
        async def _check() -> bool:
            from pymobiledevice3.lockdown import create_using_usbmux
            ld = await create_using_usbmux(serial=self._serial, autopair=False)
            try:
                return await ld.get_developer_mode_status()
            finally:
                await ld.close()

        try:
            return _run_sync(_check(), timeout=30)
        except Exception as exc:
            from pymobiledevice3.exceptions import (
                GetProhibitedError,
                NotPairedError,
                NotTrustedError,
                PairingError,
            )
            if isinstance(exc, GetProhibitedError):
                # 锁屏时 lockdownd 拒绝读取开发者模式等设备状态。
                raise DeviceLockedError("iPhone 已锁定，请解锁后重试") from exc
            if isinstance(exc, (NotPairedError, NotTrustedError, PairingError)):
                raise InjectionError(
                    "未配对 / 未信任：请在手机上点「信任此电脑」，"
                    "或先运行一次 pymobiledevice3 lockdown pair"
                ) from exc
            raise

    def set_location(self, coord: Coordinate) -> None:
        async def _do() -> None:
            _, _, ls = await self._ensure_session()
            await ls.set(coord.latitude, coord.longitude)

        try:
            # 首次可能要下载并挂载 DDI，给足时间。
            _run_sync(_do(), timeout=300)
        except Exception as exc:
            self.reset()  # 会话可能已随断连失效：关掉它，下次注入重建
            raise InjectionError(f"模拟位置注入失败：{exc}") from exc
        self._location = coord

    def clear_location(self) -> None:
        async def _do() -> None:
            _, _, ls = await self._ensure_session()
            await ls.clear()

        try:
            _run_sync(_do(), timeout=300)
        except Exception as exc:
            self.reset()
            raise InjectionError(f"恢复真实位置失败：{exc}") from exc
        self._location = None

    def reset(self) -> None:
        """丢弃当前隧道会话（断连后可能已死）；下次注入时重建。"""
        try:
            _run_sync(self._drop_session(), timeout=15)
        except Exception:
            self._session = None

    def close(self) -> None:
        try:
            _run_sync(self._drop_session(), timeout=15)
        except Exception:
            self._session = None


def list_devices(use_mock: bool = False) -> list[Device]:
    """枚举已连接设备。use_mock=True 返回一个假设备；真实枚举失败返回空列表。"""
    if use_mock:
        return [MockDevice()]

    async def _enumerate() -> list[tuple[str, str]]:
        from pymobiledevice3.lockdown import create_using_usbmux
        from pymobiledevice3.usbmux import list_devices as usbmux_list

        found: list[tuple[str, str]] = []
        for dev in await usbmux_list():
            name = dev.serial
            try:
                ld = await create_using_usbmux(serial=dev.serial, autopair=False)
                try:
                    name = (await ld.get_value(key="DeviceName")) or dev.serial
                finally:
                    await ld.close()
            except Exception:
                pass  # 拿不到友好名就用序列号
            found.append((dev.serial, name))
        return found

    try:
        infos = _run_sync(_enumerate(), timeout=20)
    except Exception:
        return []
    return [RealDevice(serial, name) for serial, name in infos]


def device_present(serial: str, timeout: float = 30.0) -> bool:
    """设备是否还在 usbmux 上（只看连接可见性，不碰已有的隧道会话）。"""
    async def _check() -> bool:
        from pymobiledevice3.usbmux import list_devices as usbmux_list

        return any(dev.serial == serial for dev in await usbmux_list())

    try:
        return _run_sync(_check(), timeout=timeout)
    except Exception:
        return False


class DeviceMonitor:
    """后台轮询设备可见性，每轮把 ("present", bool) 放入 events。

    # ponytail: 固定 2 秒轮询一个设备；多设备同时监控时再按设备拆分。
    """

    def __init__(self, serial: str, events: "queue.Queue", interval: float = 2.0) -> None:
        self._serial = serial
        self._events = events
        self._interval = interval
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        while not self._stop.is_set():
            self._events.put(("present", device_present(self._serial)))
            self._stop.wait(self._interval)
