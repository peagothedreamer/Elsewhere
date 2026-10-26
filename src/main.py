from __future__ import annotations

import queue
import sys
import threading
from pathlib import Path

from PySide6.QtCore import QRect, QRectF, QSize, Qt, QTimer, QUrl
from PySide6.QtGui import (
    QColor,
    QDesktopServices,
    QFont,
    QIcon,
    QPainter,
    QPalette,
    QPen,
    QPixmap,
    QTextCursor,
    QTextDocument,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QStyle,
    QStyledItemDelegate,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

import theme_c
from geocode import Geocoder
from inject import (
    DEFAULT_LOCATION,
    Coordinate,
    Device,
    DeviceLockedError,
    DeviceMonitor,
    InjectionError,
    MockDevice,
    RealDevice,
    list_devices,
    parse_coordinate,
)
from map_view import MapView
from route import (
    DEFAULT_COOLDOWN,
    DEFAULT_SPEED,
    MODE_SMOOTH,
    MODE_TELEPORT,
    Route,
    RouteRunner,
    distance_m,
    load_presets,
    save_presets,
)
from setup_page import SetupDialog


def _font(families: tuple[str, ...], size: int) -> QFont:
    """按字体栈建字体：首个可用字体生效，缺字由系统回退。"""
    font = QFont()
    font.setFamilies(list(families))
    font.setPointSize(size)
    return font


class ChevronCombo(QComboBox):
    """无边框下拉框，用自绘小折线替代原生箭头。"""

    def __init__(self, parent=None, chevron: str = theme_c.INK) -> None:
        super().__init__(parent)
        self._chevron = QColor(chevron)

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setPen(QPen(self._chevron, 1.2))
        x = self.width() - 13
        y = self.height() // 2
        p.drawLine(x - 3, y - 2, x, y + 1)
        p.drawLine(x, y + 1, x + 3, y - 2)
        p.end()


class WaypointDelegate(QStyledItemDelegate):
    """航点行：编号节点、虚线航路、两列坐标、距离。"""

    ROW_HEIGHT = 36

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.coord_font = _font(theme_c.SERIF_FONTS, 11)
        self.node_font = _font(theme_c.SERIF_FONTS, 8)
        self.dist_font = _font(theme_c.BODY_FONTS, 9)

    def sizeHint(self, option, index) -> QSize:
        return QSize(0, self.ROW_HEIGHT)

    def paint(self, painter: QPainter, option, index) -> None:
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = option.rect
        enabled = bool(option.state & QStyle.StateFlag.State_Enabled)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        model = index.model()
        total = model.rowCount() if model is not None else 0
        row = index.row()
        text_color = QColor(theme_c.INK if enabled else theme_c.FAINT)
        soft_color = QColor(theme_c.MUTED if enabled else theme_c.LINE)

        if selected:
            painter.fillRect(
                QRect(rect.left() + 24, rect.top(), max(0, rect.width() - 30), rect.height()),
                QColor("#F6E9EC"),
            )

        cx = rect.left() + 10
        cy = rect.top() + rect.height() // 2
        spine = QColor(theme_c.LINE2 if enabled else "#D8CFBC")
        painter.setPen(QPen(spine, 1, Qt.PenStyle.DashLine))
        if row > 0:
            painter.drawLine(cx, rect.top(), cx, cy)
        if row < total - 1:
            painter.drawLine(cx, cy, cx, rect.bottom())

        radius = 7
        node = QRectF(cx - radius, cy - radius, radius * 2, radius * 2)
        if selected:
            painter.setBrush(QColor(theme_c.MAGENTA))
            painter.setPen(QPen(QColor(theme_c.MAGENTA), 1))
        else:
            painter.setBrush(QColor(theme_c.PAPER))
            painter.setPen(QPen(text_color, 1))
        painter.drawEllipse(node)
        painter.setFont(self.node_font)
        painter.setPen(QColor(theme_c.PAPER if selected else text_color))
        painter.drawText(node, Qt.AlignmentFlag.AlignCenter, str(row + 1))

        if row < total - 1:
            painter.setPen(QPen(QColor("#E2D8C3"), 1, Qt.PenStyle.SolidLine))
            painter.drawLine(rect.left() + 24, rect.bottom() - 1, rect.right() - 6, rect.bottom() - 1)

        data = index.data(Qt.ItemDataRole.UserRole) or {}
        lat = str(data.get("lat", ""))
        lon = str(data.get("lon", ""))
        dist = str(data.get("dist", ""))
        right = rect.right() - 6
        dist_w = 48
        lon_right = right - dist_w
        content_left = rect.left() + 24
        col_w = max(1, (lon_right - content_left) // 2)
        lat_right = content_left + col_w

        painter.setFont(self.coord_font)
        painter.setPen(text_color)
        painter.drawText(
            QRect(content_left, rect.top(), col_w, rect.height()),
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
            lat,
        )
        painter.drawText(
            QRect(lat_right, rect.top(), col_w, rect.height()),
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
            lon,
        )
        painter.setFont(self.dist_font)
        painter.setPen(soft_color)
        painter.drawText(
            QRect(lon_right, rect.top(), dist_w, rect.height()),
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
            dist,
        )
        painter.restore()


class WaypointList(QListWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("Waypoints")
        self.setItemDelegate(WaypointDelegate(self))
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setUniformItemSizes(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setSpacing(0)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Elsewhere — 虚拟定位")
        self.resize(1100, 720)

        self._presets = load_presets()
        self._route_points: list[Coordinate] = []
        self._manual_idx = 0
        self._runner: RouteRunner | None = None
        self._events: "queue.Queue" = queue.Queue()
        self._busy = False
        self._disconnected = False
        self._monitor: DeviceMonitor | None = None
        self._device_state = "absent"
        self._guide_open = False
        self._poll_timer = QTimer(self)
        self._poll_timer.timeout.connect(self._poll_events)

        self._build_ui()
        self._refresh_presets()
        self._refresh_route_view()
        self.refresh_devices(guide=True)
        self._poll_timer.start(100)

    # ---------------- UI 构建 ----------------
    def _build_ui(self) -> None:
        central = QWidget()
        central.setObjectName("Central")
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        topbar = QWidget()
        topbar.setObjectName("TopBar")
        topbar.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        topbar.setFixedHeight(42)
        bar = QHBoxLayout(topbar)
        bar.setContentsMargins(14, 0, 14, 0)
        bar.setSpacing(8)

        self.device_combo = ChevronCombo(chevron=theme_c.PAPER)
        self.device_combo.setObjectName("DeviceCombo")
        self.device_combo.setFixedWidth(330)
        self.device_combo.currentIndexChanged.connect(self._on_device_changed)
        self.refresh_btn = QPushButton("刷新设备")
        self.refresh_btn.setObjectName("TopGhost")
        self.refresh_btn.clicked.connect(lambda: self.refresh_devices(guide=True))
        self.help_btn = QPushButton("使用说明")
        self.help_btn.setObjectName("TopGhost")
        self.help_btn.clicked.connect(
            lambda: QDesktopServices.openUrl(
                QUrl("https://github.com/peagothedreamer/Elsewhere")
            )
        )
        bar.addWidget(self.device_combo)
        bar.addWidget(self.refresh_btn)
        bar.addWidget(self.help_btn)
        bar.addStretch(1)

        credit = QPushButton("peago")
        credit.setObjectName("Credit")
        credit.setFlat(True)
        credit.setCursor(Qt.CursorShape.PointingHandCursor)
        credit.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl("https://peagothedreamer.github.io/"))
        )
        bar.addWidget(credit)

        status_box = QWidget()
        status_lay = QHBoxLayout(status_box)
        status_lay.setContentsMargins(0, 0, 0, 0)
        status_lay.setSpacing(8)
        self.status_mark = QLabel("◆")
        self.status_mark.setObjectName("StatusMark")
        self.status_mark.setFixedWidth(10)
        self.status_mark.setStyleSheet(
            f"color:{theme_c.TONE_IDLE}; font-size:9px;"
        )
        self.device_status = QLabel("未连接")
        self.device_status.setObjectName("StatusText")
        status_lay.addWidget(self.status_mark)
        status_lay.addWidget(self.device_status)
        bar.addWidget(status_box)
        root.addWidget(topbar)

        main = QWidget()
        main.setObjectName("Main")
        main_lay = QHBoxLayout(main)
        main_lay.setContentsMargins(0, 0, 0, 0)
        main_lay.setSpacing(0)

        rail = QWidget()
        rail.setObjectName("Rail")
        rail.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        rail.setFixedWidth(400)
        rail_lay = QVBoxLayout(rail)
        rail_lay.setContentsMargins(0, 0, 0, 0)
        rail_lay.setSpacing(0)

        self._log_doc = QTextDocument(self)
        self._log_doc.setDefaultFont(_font(theme_c.BODY_FONTS, 9))

        self.tabs = QTabWidget()
        self.tabs.setObjectName("Tabs")
        self.tabs.setDocumentMode(True)
        self.tabs.tabBar().setExpanding(False)
        self.tabs.tabBar().setDrawBase(False)
        self.tabs.tabBar().setFixedHeight(36)
        self.tabs.addTab(self._build_single_tab(), "单点")
        self.tabs.addTab(self._build_route_tab(), "路线")
        self.tabs.currentChanged.connect(self._on_tab_changed)
        self._log_doc.contentsChanged.connect(self._scroll_active_log)
        rail_lay.addWidget(self.tabs, 1)

        main_lay.addWidget(rail)
        main_lay.addWidget(self._build_map_panel(), 1)
        root.addWidget(main, 1)
        self.setCentralWidget(central)

    def _build_map_panel(self) -> QWidget:
        panel = QWidget()
        panel.setObjectName("MapPanel")
        panel.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.map = MapView()
        self.map.picked.connect(self._on_map_pick)
        layout.addWidget(self.map)

        self.search_panel = QWidget(panel)
        self.search_panel.setObjectName("SearchPanel")
        self.search_panel.setFixedWidth(360)
        sp = QVBoxLayout(self.search_panel)
        sp.setContentsMargins(0, 0, 0, 0)
        sp.setSpacing(0)

        search_row = QHBoxLayout()
        search_row.setContentsMargins(0, 0, 0, 0)
        search_row.setSpacing(4)
        self.search_edit = QLineEdit()
        self.search_edit.setObjectName("MapSearch")
        self.search_edit.setPlaceholderText("搜索地点，回车")
        self.search_edit.returnPressed.connect(self._search_place)
        pal = self.search_edit.palette()
        pal.setColor(QPalette.ColorRole.PlaceholderText, QColor(theme_c.MUTED))
        self.search_edit.setPalette(pal)
        search_row.addWidget(self.search_edit, 1)
        sp.addLayout(search_row)

        self.search_status = QLabel("")
        self.search_status.setObjectName("SearchStatus")
        self.search_status.setWordWrap(True)
        self.search_status.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
        )
        self.search_status.setVisible(False)
        sp.addWidget(self.search_status)

        self.search_results = QListWidget()
        self.search_results.setObjectName("SearchResults")
        self.search_results.setMaximumHeight(124)
        self.search_results.setVisible(False)
        self.search_results.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.search_results.itemClicked.connect(self._use_search_result)
        sp.addWidget(self.search_results)

        self.search_panel.move(12, 12)
        self.search_panel.adjustSize()
        self.search_panel.raise_()

        self.geocoder = Geocoder(self)
        self.geocoder.results.connect(self._on_search_results)
        self.geocoder.error.connect(self._on_search_error)
        return panel

    def _sync_search_panel(self) -> None:
        self.search_panel.adjustSize()
        self.search_panel.raise_()

    def _leader(self) -> QFrame:
        line = QFrame()
        line.setObjectName("Leader")
        line.setFixedHeight(1)
        line.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        return line

    def _label(self, text: str, name: str) -> QLabel:
        lab = QLabel(text)
        lab.setObjectName(name)
        return lab

    def _coord_field(self, caption: str, value: str, small: bool = False):
        box = QWidget()
        lay = QVBoxLayout(box)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        lay.addWidget(self._label(caption, "FieldLabel"))
        edit = QLineEdit(value)
        edit.setObjectName("CoordSmall" if small else "Coord")
        edit.setAlignment(Qt.AlignmentFlag.AlignRight)
        edit.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        lay.addWidget(edit)
        return box, edit

    def _spec_row(self, caption: str, value: QWidget):
        row = QWidget()
        row.setObjectName("SpecRow")
        row.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        lay = QHBoxLayout(row)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        label = self._label(caption, "SpecLabel")
        label.setFixedWidth(78)
        lay.addWidget(label)
        lay.addWidget(self._leader(), 1, Qt.AlignmentFlag.AlignVCenter)
        lay.addWidget(value, 0, Qt.AlignmentFlag.AlignVCenter)
        row.setFixedHeight(29)
        return row, label

    def _magenta_icon(self) -> QIcon:
        pix = QPixmap(4, 4)
        pix.fill(QColor(theme_c.MAGENTA))
        return QIcon(pix)

    def _set_status(self, text: str, tone: str = "idle") -> None:
        self.device_status.setText(text)
        color = {
            "ok": theme_c.TONE_OK,
            "busy": theme_c.TONE_BUSY,
            "error": theme_c.TONE_ERROR,
        }.get(tone, theme_c.TONE_IDLE)
        self.status_mark.setStyleSheet(f"color:{color}; font-size:9px;")

    def _on_tab_changed(self, index: int) -> None:
        if index == 0:
            view = self.log_single
        elif index == 1:
            view = self.log_route
        else:
            return
        view.moveCursor(QTextCursor.MoveOperation.End)
        view.ensureCursorVisible()

    def _scroll_active_log(self) -> None:
        index = self.tabs.currentIndex()
        if index == 0:
            view = self.log_single
        elif index == 1:
            view = self.log_route
        else:
            return
        view.moveCursor(QTextCursor.MoveOperation.End)
        view.ensureCursorVisible()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if hasattr(self, "search_panel"):
            self.search_panel.raise_()
        if hasattr(self, "tabs"):
            self._on_tab_changed(self.tabs.currentIndex())

    # ---------------- 地图 / 搜索 ----------------
    def _search_place(self) -> None:
        text = self.search_edit.text().strip()
        if not text:
            return
        self.search_status.setText("搜索中…")
        self.search_status.setVisible(True)
        self.search_results.setVisible(False)
        self._sync_search_panel()
        self.geocoder.search(text)

    def _on_search_results(self, places) -> None:
        self.search_results.clear()
        for place in places:
            item = QListWidgetItem(place.label)
            item.setData(Qt.ItemDataRole.UserRole, place.coordinate)
            item.setSizeHint(QSize(0, 34))
            self.search_results.addItem(item)
        self.search_results.setVisible(True)
        self.search_status.setText(f"{len(places)} 个结果")
        self.search_status.setVisible(True)
        self._sync_search_panel()

    def _on_search_error(self, message: str) -> None:
        self.search_status.setText(message)
        self.search_status.setVisible(True)
        self._sync_search_panel()
        self.log.append(message)

    def _use_search_result(self, item: QListWidgetItem) -> None:
        coord = item.data(Qt.ItemDataRole.UserRole)
        if coord is not None:
            self._apply_coordinate(coord, fly=True)

    def _on_map_pick(self, coord: Coordinate) -> None:
        self.map.set_picked(coord)
        self._apply_coordinate(coord, fly=False)

    def _apply_coordinate(self, coord: Coordinate, fly: bool) -> None:
        """把选中的坐标填入当前 tab 的经纬度输入框（不直接注入）。"""
        if fly:
            self.map.set_picked(coord)
            self.map.fly_to(coord)
        lat_text, lon_text = f"{coord.latitude:.6f}", f"{coord.longitude:.6f}"
        if self.tabs.currentIndex() == 0:
            self.lat_edit.setText(lat_text)
            self.lon_edit.setText(lon_text)
        elif self.tabs.currentIndex() == 1:
            self.route_lat.setText(lat_text)
            self.route_lon.setText(lon_text)

    def _build_single_tab(self) -> QWidget:
        page = QWidget()
        page.setObjectName("Page")
        page.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        root = QVBoxLayout(page)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(8)

        fix = QWidget()
        fix_lay = QHBoxLayout(fix)
        fix_lay.setContentsMargins(0, 0, 0, 0)
        fix_lay.setSpacing(18)
        lat_box, self.lat_edit = self._coord_field(
            "纬度", f"{DEFAULT_LOCATION[0]:.6f}"
        )
        lon_box, self.lon_edit = self._coord_field(
            "经度", f"{DEFAULT_LOCATION[1]:.6f}"
        )
        fix_lay.addWidget(lat_box, 1)
        fix_lay.addWidget(lon_box, 1)
        root.addWidget(fix)

        self.set_btn = QPushButton("设为模拟位置")
        self.set_btn.setObjectName("Primary")
        self.set_btn.setFixedHeight(38)
        self.set_btn.setIcon(self._magenta_icon())
        self.set_btn.setIconSize(QSize(4, 4))
        self.set_btn.clicked.connect(self.set_location)
        root.addWidget(self.set_btn)

        cancel = QWidget()
        cancel.setObjectName("CancelZone")
        cancel.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        cancel_lay = QHBoxLayout(cancel)
        cancel_lay.setContentsMargins(0, 10, 0, 0)
        cancel_lay.setSpacing(8)
        self.default_btn = QPushButton("默认位置（浙大紫金港）")
        self.default_btn.setObjectName("DefaultButton")
        self.default_btn.clicked.connect(self.use_default)
        self.clear_btn = QPushButton("恢复真实位置")
        self.clear_btn.setObjectName("DangerStamp")
        self.clear_btn.setFixedHeight(30)
        self.clear_btn.clicked.connect(self.clear_location)
        cancel_lay.addWidget(self.default_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        cancel_lay.addWidget(self._leader(), 1, Qt.AlignmentFlag.AlignVCenter)
        cancel_lay.addWidget(self.clear_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        root.addWidget(cancel)

        self.log_single = QTextEdit()
        self.log_single.setObjectName("Log")
        self.log_single.setReadOnly(True)
        self.log_single.setFrameShape(QFrame.Shape.NoFrame)
        self.log_single.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        self.log_single.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.log_single.setDocument(self._log_doc)
        pal = self.log_single.palette()
        pal.setColor(QPalette.ColorRole.Text, QColor(theme_c.MUTED))
        self.log_single.setPalette(pal)
        root.addWidget(self.log_single, 1)
        return page

    def _build_route_tab(self) -> QWidget:
        page = QWidget()
        page.setObjectName("Page")
        page.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        root = QVBoxLayout(page)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(6)

        add = QWidget()
        add_lay = QHBoxLayout(add)
        add_lay.setContentsMargins(0, 0, 0, 0)
        add_lay.setSpacing(8)
        lat_box, self.route_lat = self._coord_field(
            "纬度", f"{DEFAULT_LOCATION[0]:.6f}", small=True
        )
        lon_box, self.route_lon = self._coord_field(
            "经度", f"{DEFAULT_LOCATION[1]:.6f}", small=True
        )
        self.add_point_btn = QPushButton("添加点")
        self.add_point_btn.setObjectName("AddPoint")
        self.add_point_btn.setFixedHeight(32)
        self.add_point_btn.clicked.connect(self.add_route_point)
        add_lay.addWidget(lat_box, 1)
        add_lay.addWidget(lon_box, 1)
        add_lay.addWidget(
            self.add_point_btn, 0, Qt.AlignmentFlag.AlignBottom
        )
        root.addWidget(add)

        self.point_list = WaypointList()
        self.point_list.setMinimumHeight(80)
        root.addWidget(self.point_list, 1)

        order_row = QHBoxLayout()
        order_row.setContentsMargins(0, 0, 0, 0)
        order_row.setSpacing(14)
        self.delete_point_btn = QPushButton("删除")
        self.delete_point_btn.setObjectName("TextDanger")
        self.delete_point_btn.clicked.connect(self.delete_route_point)
        self.up_btn = QPushButton("上移")
        self.up_btn.setObjectName("TextCmd")
        self.up_btn.clicked.connect(lambda: self._move_point(-1))
        self.down_btn = QPushButton("下移")
        self.down_btn.setObjectName("TextCmd")
        self.down_btn.clicked.connect(lambda: self._move_point(1))
        for button in (self.delete_point_btn, self.up_btn, self.down_btn):
            order_row.addWidget(button)
        order_row.addStretch(1)
        root.addLayout(order_row)

        total = QWidget()
        total_lay = QHBoxLayout(total)
        total_lay.setContentsMargins(0, 0, 0, 0)
        total_lay.setSpacing(0)
        total_lay.addWidget(self._label("总距离", "TotalLabel"))
        total_lay.addWidget(self._leader(), 1, Qt.AlignmentFlag.AlignVCenter)
        self.distance_label = QLabel("0 m")
        self.distance_label.setObjectName("TotalValue")
        self.distance_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        total_lay.addWidget(self.distance_label)
        root.addWidget(total)

        self.closure_hint = QLabel("点击「开始」或「保存」时，路线自动闭合成环")
        self.closure_hint.setObjectName("Hint")
        root.addWidget(self.closure_hint)

        spec = QWidget()
        spec_lay = QVBoxLayout(spec)
        spec_lay.setContentsMargins(0, 0, 0, 0)
        spec_lay.setSpacing(0)

        self.mode_combo = ChevronCombo()
        self.mode_combo.setObjectName("SpecCombo")
        self.mode_combo.addItem("跳点传送（瞬移）", MODE_TELEPORT)
        self.mode_combo.addItem("多点路线（平滑）", MODE_SMOOTH)
        self.mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        self.mode_combo.setFixedWidth(168)
        mode_row, _ = self._spec_row("模式", self.mode_combo)
        spec_lay.addWidget(mode_row)

        self.cooldown_spin = QDoubleSpinBox()
        self.cooldown_spin.setObjectName("SpecSpin")
        self.cooldown_spin.setRange(0.0, 3600.0)
        self.cooldown_spin.setDecimals(1)
        self.cooldown_spin.setValue(DEFAULT_COOLDOWN)
        self.cooldown_spin.setFixedWidth(80)
        self._align_spin(self.cooldown_spin)
        self.cooldown_row, self.cooldown_label = self._spec_row(
            "冷却（秒）", self.cooldown_spin
        )
        spec_lay.addWidget(self.cooldown_row)

        self.speed_spin = QDoubleSpinBox()
        self.speed_spin.setObjectName("SpecSpin")
        self.speed_spin.setRange(0.1, 300.0)
        self.speed_spin.setDecimals(1)
        self.speed_spin.setValue(DEFAULT_SPEED)
        self.speed_spin.setFixedWidth(80)
        self._align_spin(self.speed_spin)
        self.speed_row, self.speed_label = self._spec_row(
            "速度（m/s）", self.speed_spin
        )
        spec_lay.addWidget(self.speed_row)

        loops_value = QWidget()
        loops_lay = QHBoxLayout(loops_value)
        loops_lay.setContentsMargins(0, 0, 0, 0)
        loops_lay.setSpacing(6)
        self.loops_spin = QSpinBox()
        self.loops_spin.setObjectName("SpecSpin")
        self.loops_spin.setRange(1, 9999)
        self.loops_spin.setValue(1)
        self.loops_spin.setSuffix(" 次")
        self.loops_spin.setFixedWidth(66)
        self._align_spin(self.loops_spin)
        self.infinite_check = QCheckBox("无限")
        self.infinite_check.setObjectName("LoopCheck")
        self.infinite_check.toggled.connect(
            lambda on: self.loops_spin.setDisabled(on)
        )
        loops_lay.addWidget(self.loops_spin)
        loops_lay.addWidget(self.infinite_check)
        self.quick_btns = []
        for n in (6, 10):
            b = QPushButton(str(n))
            b.setObjectName("Quick")
            b.setFixedSize(22, 22)
            b.clicked.connect(lambda _=False, n=n: self._set_loops_quick(n))
            self.quick_btns.append(b)
            loops_lay.addWidget(b)
        loops_row, self.loops_label = self._spec_row("循环", loops_value)
        spec_lay.addWidget(loops_row)
        root.addWidget(spec)

        run = QWidget()
        run.setObjectName("RunRow")
        run.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        run_lay = QHBoxLayout(run)
        run_lay.setContentsMargins(0, 8, 0, 0)
        run_lay.setSpacing(5)
        self.start_btn = QPushButton("开始")
        self.start_btn.setObjectName("Primary")
        self.start_btn.setFixedHeight(38)
        self.start_btn.setIcon(self._magenta_icon())
        self.start_btn.setIconSize(QSize(4, 4))
        self.start_btn.clicked.connect(self.start_route)
        self.pause_btn = QPushButton("暂停")
        self.pause_btn.setObjectName("Stamp")
        self.pause_btn.clicked.connect(self.toggle_pause)
        self.pause_btn.setDisabled(True)
        self.stop_btn = QPushButton("停止")
        self.stop_btn.setObjectName("StopStamp")
        self.stop_btn.clicked.connect(self.stop_route)
        self.stop_btn.setDisabled(True)
        self.next_btn = QPushButton("下一个")
        self.next_btn.setObjectName("Stamp")
        self.next_btn.clicked.connect(self.manual_next)
        run_lay.addWidget(self.start_btn, 3)
        run_lay.addWidget(self.pause_btn, 2)
        run_lay.addWidget(self.stop_btn, 2)
        run_lay.addWidget(self.next_btn, 2)
        root.addWidget(run)

        presets = QWidget()
        presets_lay = QHBoxLayout(presets)
        presets_lay.setContentsMargins(0, 0, 0, 0)
        presets_lay.setSpacing(4)
        self.preset_combo = ChevronCombo()
        self.preset_combo.setObjectName("PresetCombo")
        presets_lay.addWidget(self.preset_combo, 1)
        self.save_preset_btn = QPushButton("保存")
        self.save_preset_btn.setObjectName("PresetCmd")
        self.save_preset_btn.clicked.connect(self.save_preset)
        self.load_preset_btn = QPushButton("加载")
        self.load_preset_btn.setObjectName("PresetCmd")
        self.load_preset_btn.clicked.connect(self.load_preset)
        self.delete_preset_btn = QPushButton("删除")
        self.delete_preset_btn.setObjectName("PresetDanger")
        self.delete_preset_btn.clicked.connect(self.delete_preset)
        self.rename_preset_btn = QPushButton("重命名")
        self.rename_preset_btn.setObjectName("PresetCmd")
        self.rename_preset_btn.clicked.connect(self.rename_preset)
        for button in (
            self.save_preset_btn,
            self.load_preset_btn,
            self.rename_preset_btn,
            self.delete_preset_btn,
        ):
            presets_lay.addWidget(button)
        root.addWidget(presets)

        self.route_status = QLabel("")
        self.route_status.setObjectName("RouteStatus")
        self.route_status.setFixedHeight(14)
        root.addWidget(self.route_status)

        self.log_route = QTextEdit()
        self.log_route.setObjectName("Log")
        self.log_route.setReadOnly(True)
        self.log_route.setFrameShape(QFrame.Shape.NoFrame)
        self.log_route.setFixedHeight(64)
        self.log_route.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        self.log_route.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.log_route.setDocument(self._log_doc)
        pal = self.log_route.palette()
        pal.setColor(QPalette.ColorRole.Text, QColor(theme_c.MUTED))
        self.log_route.setPalette(pal)
        root.addWidget(self.log_route)
        self.log = self.log_route

        self.mode_combo.setCurrentIndex(1)  # 默认多点（平滑）模式
        self._on_mode_changed()
        return page

    @staticmethod
    def _align_spin(spin) -> None:
        line_edit = spin.findChild(QLineEdit)
        if line_edit is not None:
            line_edit.setAlignment(Qt.AlignmentFlag.AlignRight)

    # ---------------- 设备 ----------------
    def refresh_devices(self, guide: bool = False) -> None:
        self.device_combo.clear()
        try:
            devices = list_devices(use_mock=False)
        except Exception as exc:
            devices = []
            self.log.append(f"枚举真实设备失败：{exc}")
        if not devices:
            devices = [MockDevice()]
            self.log.append("未检测到真实设备，已回退到 Mock 设备（离线调试）。")
        for d in devices:
            self.device_combo.addItem(f"{d.name}  [{d.serial}]", d)
        state = self._update_status()
        if guide and state in ("absent", "trust", "locked", "devmode"):
            # 等窗口显示完再弹，避免对话框先于主窗口出现。
            QTimer.singleShot(0, self._open_guide)

    def _open_guide(self) -> None:
        if self._guide_open or self._device_state == "ready":
            return
        self._guide_open = True
        try:
            SetupDialog(self._device_state, self).exec()
        finally:
            self._guide_open = False
        self.refresh_devices()

    def _current_device(self) -> Device | None:
        return self.device_combo.currentData()

    def _on_device_changed(self) -> None:
        if self._runner is not None:
            self.stop_route()
        self._restart_monitor()
        self._update_status()

    def _update_status(self) -> str:
        """刷新顶部状态，返回设备状态：ready / devmode / trust / absent / error。"""
        dev = self._current_device()
        if dev is None:
            self._device_state = "absent"
            self._set_status("未连接", "idle")
            return "absent"
        if isinstance(dev, MockDevice):
            self._device_state = "absent"
            self._set_status("Mock 设备（离线调试）", "busy")
            return "absent"
        try:
            dm = dev.is_developer_mode_enabled()
        except DeviceLockedError as exc:
            self._device_state = "locked"
            self._set_status(str(exc), "busy")
            return "locked"
        except InjectionError as exc:
            self._device_state = "trust"
            self._set_status(str(exc), "error")
            return "trust"
        except Exception as exc:
            self._device_state = "error"
            self._set_status(f"状态获取失败：{exc}", "error")
            return "error"
        if dm:
            self._device_state = "ready"
            self._set_status("已连接，开发者模式已开启", "ok")
            return "ready"
        self._device_state = "devmode"
        self._set_status("已连接，开发者模式未开启 —— 无法注入", "error")
        return "devmode"

    # ---------------- 设备 I/O（后台线程） ----------------
    def _spawn(self, label: str, fn, data=None) -> bool:
        """在后台线程执行一次设备操作，结果经 events 回 UI。忙时忽略。"""
        if self._busy:
            return False
        self._busy = True

        def worker() -> None:
            try:
                fn()
                self._events.put(("job_done", (label, data)))
            except Exception as exc:
                self._events.put(("job_error", (label, str(exc))))

        threading.Thread(target=worker, daemon=True).start()
        return True

    def _on_job_done(self, label: str, data) -> None:
        self._busy = False
        if label == "set":
            self._reset_status()
            self.map.set_current(data)
            self.log.append(f"已设为模拟位置：{data}")
        elif label == "clear":
            self._reset_status()
            self.map.set_current(None)
            self.log.append("已恢复真实位置")
        elif label == "next":
            idx, point = data
            self._manual_idx = idx + 1
            self.point_list.setCurrentRow(idx)
            self.map.set_current(point)
            self.log.append(f"跳到第 {idx + 1} 点：{point}")
        elif label == "recover":
            self._disconnected = False
            self._reset_status()
            self.log.append("设备已重连")
            dev = self._current_device()
            last = dev.current_location if dev is not None else None
            if last is not None:
                self.map.set_current(last)

    def _on_job_error(self, label: str, message: str) -> None:
        self._busy = False
        if label == "recover":
            self._set_status("重连失败，重试中…", "error")
            self.log.append(f"重连失败：{message}")
            return
        self._reset_status()
        QMessageBox.critical(self, "失败", message)

    def _reset_status(self) -> None:
        dev = self._current_device()
        if dev is None:
            self._set_status("未连接", "idle")
        elif isinstance(dev, MockDevice):
            self._set_status("Mock 设备（离线调试）", "busy")
        else:
            self._set_status("已连接", "ok")

    def _restart_monitor(self) -> None:
        if self._monitor is not None:
            self._monitor.stop()
            self._monitor = None
        self._disconnected = False
        dev = self._current_device()
        if isinstance(dev, RealDevice):
            self._monitor = DeviceMonitor(dev.serial, self._events)
            self._monitor.start()

    def _on_presence(self, present: bool) -> None:
        if not isinstance(self._current_device(), RealDevice):
            return
        if present:
            if not self._disconnected:
                return
            playing = (
                self._runner is not None
                and self._runner.running
                and not self._runner.paused
            )
            if not playing:
                self._try_recover()
            return  # 播放中由 runner 的写入重试恢复，成功后发 connected
        if self._disconnected:
            return
        self._disconnected = True
        self._set_status("已断开，重连中…", "error")
        self.log.append("设备已断开")

    def _try_recover(self) -> None:
        dev = self._current_device()
        if not isinstance(dev, RealDevice):
            return
        self._set_status("正在重连…", "busy")
        self._spawn("recover", lambda: self._recover_device(dev))

    @staticmethod
    def _recover_device(dev: RealDevice) -> None:
        dev.reset()
        last = dev.current_location
        if last is not None:
            dev.set_location(last)

    # ---------------- 单点 ----------------
    def set_location(self) -> None:
        dev = self._current_device()
        if dev is None:
            return
        try:
            coord = parse_coordinate(self.lat_edit.text(), self.lon_edit.text())
        except ValueError as exc:
            QMessageBox.warning(self, "输入错误", str(exc))
            return
        if self._spawn("set", lambda: dev.set_location(coord), coord):
            self._set_status("正在注入…", "busy")

    def clear_location(self) -> None:
        dev = self._current_device()
        if dev is None:
            return
        if self._spawn("clear", dev.clear_location):
            self._set_status("正在恢复真实位置…", "busy")

    def use_default(self) -> None:
        lat, lon = DEFAULT_LOCATION
        coord = Coordinate(lat, lon)
        self.lat_edit.setText(f"{lat:.6f}")
        self.lon_edit.setText(f"{lon:.6f}")
        self.map.set_picked(coord)
        self.map.fly_to(coord)
        self.log.append(f"已填入默认位置：{lat:.6f}, {lon:.6f}")

    # ---------------- 路线：编辑 ----------------
    def add_route_point(self) -> None:
        try:
            coord = parse_coordinate(self.route_lat.text(), self.route_lon.text())
        except ValueError as exc:
            QMessageBox.warning(self, "输入错误", str(exc))
            return
        self._route_points.append(coord)
        self._manual_idx = 0
        self._refresh_route_view()

    def delete_route_point(self) -> None:
        row = self.point_list.currentRow()
        if 0 <= row < len(self._route_points):
            del self._route_points[row]
            self._manual_idx = 0
            self._refresh_route_view()

    def _move_point(self, delta: int) -> None:
        row = self.point_list.currentRow()
        new = row + delta
        if 0 <= row < len(self._route_points) and 0 <= new < len(self._route_points):
            self._route_points[row], self._route_points[new] = (
                self._route_points[new],
                self._route_points[row],
            )
            self._refresh_route_view()
            self.point_list.setCurrentRow(new)

    def _refresh_route_view(self) -> None:
        self.point_list.clear()
        pts = self._route_points
        for i, p in enumerate(pts):
            dist = "—" if i == 0 else self._format_m(distance_m(pts[i - 1], p))
            text = f"{i + 1}. {p.latitude:.6f}, {p.longitude:.6f}"
            if i > 0:
                text += f"    {dist}"
            item = QListWidgetItem()
            item.setData(
                Qt.ItemDataRole.UserRole,
                {
                    "lat": f"{p.latitude:.6f}",
                    "lon": f"{p.longitude:.6f}",
                    "dist": dist,
                },
            )
            item.setData(Qt.ItemDataRole.DisplayRole, text)
            item.setToolTip(text)
            self.point_list.addItem(item)
        if len(pts) >= 2:
            total = self._current_route().total_length()
            self.distance_label.setText(self._format_m(total))
        else:
            self.distance_label.setText("0 m")
        self.map.set_route(pts, closed=False)  # 编辑时显示开口折线，保存/开始时才闭合

    @staticmethod
    def _format_m(meters: float) -> str:
        return f"{meters:,.0f}".replace(",", " ") + " m"

    def _set_loops_quick(self, n: int) -> None:
        self.infinite_check.setChecked(False)
        self.loops_spin.setValue(n)

    def _on_mode_changed(self) -> None:
        smooth = self.mode_combo.currentData() == MODE_SMOOTH
        self.cooldown_row.setEnabled(not smooth)
        self.speed_row.setEnabled(smooth)
        self.next_btn.setEnabled(
            not smooth and not (self._runner is not None and self._runner.running)
        )

    def _current_route(self) -> Route:
        loops = 0 if self.infinite_check.isChecked() else self.loops_spin.value()
        return Route(
            points=list(self._route_points),
            mode=self.mode_combo.currentData(),
            speed=self.speed_spin.value(),
            cooldown=self.cooldown_spin.value(),
            loops=loops,
            closed=True,
        )

    def _set_route_editing(self, enabled: bool) -> None:
        for w in (
            self.route_lat, self.route_lon, self.add_point_btn, self.point_list,
            self.delete_point_btn, self.up_btn, self.down_btn, self.mode_combo,
            self.cooldown_spin, self.speed_spin, self.loops_spin,
            self.infinite_check, *self.quick_btns, self.preset_combo,
            self.save_preset_btn, self.load_preset_btn,
            self.delete_preset_btn, self.rename_preset_btn,
        ):
            w.setEnabled(enabled)

    # ---------------- 路线：播放 ----------------
    def start_route(self) -> None:
        if self._runner is not None and self._runner.running:
            return
        dev = self._current_device()
        if dev is None:
            return
        route = self._current_route()
        if route.mode == MODE_SMOOTH and len(route.points) < 2:
            QMessageBox.warning(self, "无法开始", "平滑移动至少需要 2 个点")
            return
        if not route.points:
            QMessageBox.warning(self, "无法开始", "请先添加路线点")
            return
        self.map.set_route(route.points, closed=True)
        self._runner = RouteRunner(dev, route, self._events)
        self._runner.start()
        self.start_btn.setDisabled(True)
        self.pause_btn.setDisabled(False)
        self.pause_btn.setText("暂停")
        self.stop_btn.setDisabled(False)
        self.next_btn.setDisabled(True)
        self._set_route_editing(False)
        self.log.append("路线开始")

    def toggle_pause(self) -> None:
        if self._runner is None:
            return
        if self._runner.paused:
            self._runner.resume()
            self.pause_btn.setText("暂停")
            self.route_status.setText("运行中")
        else:
            self._runner.pause()
            self.pause_btn.setText("继续")
            self.route_status.setText("已暂停")

    def stop_route(self) -> None:
        if self._runner is not None:
            self._runner.stop()
        self._finish_route()

    def _finish_route(self) -> None:
        if self._runner is not None:
            self._runner.stop()
            self._runner = None
        self.start_btn.setDisabled(False)
        self.pause_btn.setDisabled(True)
        self.pause_btn.setText("暂停")
        self.stop_btn.setDisabled(True)
        self._set_route_editing(True)
        self._on_mode_changed()
        self.map.set_route(self._route_points, closed=False)
        self.route_status.setText("已停止")

    def _poll_events(self) -> None:
        finished = False
        while True:
            try:
                kind, payload = self._events.get_nowait()
            except queue.Empty:
                break
            if kind == "progress":
                if self._runner is None:  # 停止后残留的进度事件
                    continue
                loop_idx, point_idx, coord = payload
                if 0 <= point_idx < self.point_list.count():
                    self.point_list.setCurrentRow(point_idx)
                self.route_status.setText(f"运行中 · 循环 {loop_idx + 1} · {coord}")
                self.map.set_current(coord)
            elif kind == "disconnected":
                self._disconnected = True
                self._set_status("已断开，重连中…", "error")
                self.log.append("设备已断开，路线已冻结，等待重连")
            elif kind == "connected":
                self._disconnected = False
                self._reset_status()
                self.log.append("设备已重连，路线继续")
            elif kind == "present":
                self._on_presence(bool(payload))
            elif kind == "job_done":
                self._on_job_done(*payload)
            elif kind == "job_error":
                self._on_job_error(*payload)
            elif kind == "error":
                self.log.append(f"路线错误：{payload}")
                QMessageBox.critical(self, "路线错误", str(payload))
            elif kind == "finished":
                if self._runner is not None:
                    finished = True
        if finished:
            self._finish_route()
            self.log.append("路线结束")

    def manual_next(self) -> None:
        dev = self._current_device()
        if dev is None or not self._route_points:
            return
        if self.mode_combo.currentData() != MODE_TELEPORT:
            return
        idx = self._manual_idx % len(self._route_points)
        point = self._route_points[idx]
        self._spawn("next", lambda: dev.set_location(point), (idx, point))

    # ---------------- 预设 ----------------
    def _refresh_presets(self) -> None:
        self.preset_combo.clear()
        for name in self._presets:
            self.preset_combo.addItem(name)

    def save_preset(self) -> None:
        if not self._route_points:
            QMessageBox.warning(self, "无法保存", "路线为空")
            return
        name, ok = QInputDialog.getText(self, "保存预设", "名称：")
        name = name.strip()
        if not ok or not name:
            return
        if name in self._presets and QMessageBox.question(
            self, "覆盖", f"预设「{name}」已存在，覆盖？"
        ) != QMessageBox.Yes:
            return
        self._presets[name] = self._current_route()
        try:
            save_presets(self._presets)
        except OSError as exc:
            QMessageBox.critical(self, "保存失败", str(exc))
            return
        self._refresh_presets()
        self.preset_combo.setCurrentText(name)
        self.map.set_route(self._route_points, closed=True)
        self.log.append(f"已保存预设：{name}")

    def load_preset(self) -> None:
        name = self.preset_combo.currentText()
        route = self._presets.get(name)
        if route is None:
            return
        self._route_points = list(route.points)
        self.mode_combo.setCurrentIndex(max(0, self.mode_combo.findData(route.mode)))
        self.speed_spin.setValue(route.speed)
        self.cooldown_spin.setValue(route.cooldown)
        if route.loops == 0:
            self.infinite_check.setChecked(True)
        else:
            self.infinite_check.setChecked(False)
            self.loops_spin.setValue(route.loops)
        self._manual_idx = 0
        self._refresh_route_view()
        self.log.append(f"已加载预设：{name}")

    def delete_preset(self) -> None:
        name = self.preset_combo.currentText()
        if name not in self._presets:
            return
        if QMessageBox.question(self, "删除", f"删除预设「{name}」？") != QMessageBox.Yes:
            return
        del self._presets[name]
        save_presets(self._presets)
        self._refresh_presets()
        self.log.append(f"已删除预设：{name}")

    def rename_preset(self) -> None:
        name = self.preset_combo.currentText()
        if name not in self._presets:
            return
        new, ok = QInputDialog.getText(self, "重命名", "新名称：", text=name)
        new = new.strip()
        if not ok or not new or new == name:
            return
        if new in self._presets:
            QMessageBox.warning(self, "重命名", "名称已存在")
            return
        self._presets[new] = self._presets.pop(name)
        save_presets(self._presets)
        self._refresh_presets()
        self.preset_combo.setCurrentText(new)
        self.log.append(f"已重命名为：{new}")

    # ---------------- 关闭 ----------------
    def closeEvent(self, event) -> None:
        if self._runner is not None:
            self._runner.stop()
            self._runner = None
        if self._monitor is not None:
            self._monitor.stop()
            self._monitor = None
        self._poll_timer.stop()
        for i in range(self.device_combo.count()):
            dev = self.device_combo.itemData(i)
            if dev is not None:
                try:
                    dev.close()
                except Exception:
                    pass
        super().closeEvent(event)


def main() -> int:
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setFont(_font(theme_c.UI_FONTS, 9))
    app.setStyleSheet(theme_c.QSS)
    icon_name = "app.icns" if sys.platform == "darwin" else "app.ico"
    if getattr(sys, "_MEIPASS", None):
        icon_path = Path(sys._MEIPASS) / icon_name
    else:
        icon_path = Path(__file__).resolve().parent.parent / "assets" / icon_name
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))
    win = MainWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
