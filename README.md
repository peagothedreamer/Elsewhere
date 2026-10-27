# <img src="assets/app.png" width="32" height="32" alt=""> Elsewhere — iPhone 虚拟定位工具

**绝对不是为了校园跑而写的** 桌面端虚拟定位工具。

## 下载安装

**Windows 10/11**
- [下载 Elsewhere-Setup-0.3.1.exe](https://github.com/peagothedreamer/Elsewhere/releases/download/v0.3.1/Elsewhere-Setup-0.3.1.exe) — 安装版（推荐）
- [Elsewhere-0.3.1-portable.zip](https://github.com/peagothedreamer/Elsewhere/releases/download/v0.3.1/Elsewhere-0.3.1-portable.zip) — 免安装，解压后运行 `Elsewhere.exe`

**macOS 14+（Apple Silicon）**
- [下载 Elsewhere-0.3.1-arm64.dmg](https://github.com/peagothedreamer/Elsewhere/releases/download/v0.3.1/Elsewhere-0.3.1-arm64.dmg) — 打开后把 `Elsewhere.app` 拖进「应用程序」

全部版本见 [Releases](https://github.com/peagothedreamer/Elsewhere/releases)。

应用未签名：
- Windows 首次运行可能提示「已保护你的电脑」：点「更多信息」→「仍要运行」。
- macOS 会提示「无法验证开发者」。macOS 14 可右键点 app →「打开」；macOS 15 起需到「系统设置 → 隐私与安全性」→「仍要打开」，或执行：
  ```bash
  xattr -dr com.apple.quarantine /Applications/Elsewhere.app
  ```

## 使用前准备
- Windows 10/11，或 macOS 14+（Apple Silicon）
- iPhone：**iOS 17+**，开启「设置 → 隐私与安全性 → 开发者模式」并重启确认
- 数据线连接并解锁 iPhone；首次连接时在手机上点「信任」
- 地图瓦片与地点搜索需要联网；首次注入需联网下载并挂载开发者镜像（DDI），之后注入与路线播放可离线
- 关闭手机vpn

## 从源码运行
```bash
python -m pip install -r requirements.txt
python src/main.py
```

macOS 上把 `python` 换成 `python3`。需要 Python 3.11+。

用户预设存放位置：
- Windows：`%APPDATA%\Elsewhere\`
- macOS：`~/Library/Application Support/Elsewhere/`

顶部选择设备；未检测到真机时自动回退到 Mock（离线调试）。

### 地图
右侧地图常驻，按当前页签决定选点用途：
- **单点**页签下点地图 / 选搜索结果 → 填入纬度、经度（不会直接注入）。
- **路线**页签下点地图 / 选搜索结果 → 填入选点输入框，再点「添加点」。
- 顶部搜索框回车，用 Nominatim 搜地名，最多 5 条结果点选。

瓦片：
- 使用 OpenStreetMap（FOSSGIS 的 `tile.openstreetmap.de`，无需 Key）。

### 单点
输入经纬度（默认浙大紫金港）→「设为模拟位置」；「恢复真实位置」还原。注入在后台线程执行，界面不会卡住。

### 路线
1. 在地图上点击或在「路线」页输入经纬度，逐个「添加点」；地图上实时显示折线与编号点（编辑时为开口折线）。
2. 选模式（默认多点平滑）：
   - **跳点传送（瞬移）**：逐点瞬移，点间按「冷却」等待。
   - **多点路线（平滑）**：沿闭环按「速度」(m/s) 平滑移动。
3. 设置循环次数。
4. 「开始 / 暂停 / 停止」；跳点模式可点「下一个」手动跳；播放中的位置以绿色圆点显示在地图上。
5. 预设：保存 / 加载 / 删除 / 重命名，存于用户数据目录下的 `routes.json`。

点「开始」或「保存」预设时路线自动首尾闭合，停止后恢复开口显示。

### 断连恢复
每 2 秒检测设备是否仍在。断开时状态栏提示「已断开，重连中…」，路线进度冻结；设备回来后自动重建隧道、重新注入最后坐标，路线从冻结处继续。空闲状态下同样会自动恢复模拟位置。


## 仓库结构
- `src/` — 应用源码与地图资源
- `assets/` — 应用图标
- `tests/` — 自测
- `packaging/` — PyInstaller spec、Inno Setup 脚本、构建依赖

## 实现原理
**定位注入**
- 通过 pymobiledevice3 与 iPhone 通信。iOS 17+ 的模拟定位走 DVT 的 `SimulateLocation` 服务：先经 usbmuxd 建立 RSD 用户态隧道，再在隧道上连接 Instruments 服务，无需越狱。
- 隧道底层的 PyTCP 协议栈是进程级单例，因此进程内只保留一个常驻会话（隧道 + DVT + LocationSimulation），单点注入、路线播放和恢复真实位置共用它。
- pymobiledevice3 的接口是异步的：应用在后台线程维护一个共享 asyncio 事件循环，设备操作投递进去并同步取回结果。注入和路线播放都在工作线程执行，结果经队列回到 UI，界面不会被阻塞。
- 设备可见性由后台线程每 2 秒轮询；断连时路线冻结在当前进度，设备恢复后重建隧道并重新注入最后坐标。

**路线播放**
- 路线按闭环折线与 haversine 距离处理；跳点模式按“冷却”逐点瞬移，平滑模式按设定速度沿弧长插值（约 10 Hz）。
- 播放器运行在独立线程；暂停会冻结计时/弧长，停止可随时中断。

**界面与地图**
- PySide6（Qt）界面；地图是本地 Leaflet 页面，由 QtWebEngine 加载在线 OSM 瓦片。地图点击和搜索结果通过 QWebChannel 回传。
- 地名搜索使用 Nominatim，限速 1 次/秒；路线预设以 JSON 存于用户数据目录。

**设备准备**
- 启动时按“连接 → 信任 → 开发者模式 → 就绪”检测；配对和显示开发者模式选项由 pymobiledevice3 在后台完成。
- Windows 缺少 Apple Mobile Device Service 时，引导页可直接从 Apple 官方 CDN 下载并静默安装 AMDS（需管理员授权），为 pymobiledevice3 提供 usbmuxd。

**限制**
- 仅支持 iOS 17+；地图依赖在线 OSM 瓦片，无离线包。
- 模拟位置不持久化：应用重启后不会自动重新注入。

## 许可证
GPL-3.0，见 [LICENSE](LICENSE)；第三方组件许可见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
