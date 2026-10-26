"""方向 C（航空航图）的色板与 QSS。

只做视觉：颜色、字号、边框、间距。行为留在 main.py。
"""
from __future__ import annotations

PAPER = "#F1EBDC"
PAPER2 = "#F4EFE2"
INK = "#1D2B36"
INK2 = "#46555F"
MUTED = "#7C7868"
FAINT = "#B7AD96"
LINE = "#C9BFA8"
LINE2 = "#A89C82"
MAGENTA = "#A32C5E"
DANGER = "#9C3A2A"
OK = "#2E6B4C"

# 连接状态标记色（在深色顶栏上使用浅一档）
TONE_IDLE = "#A7A18F"
TONE_OK = "#7FB599"
TONE_BUSY = "#C9A44C"
TONE_ERROR = "#D06A56"

# 字体栈：Windows 字体在前，macOS 上按顺序回退到系统自带字体。
UI_FONTS = ("Bahnschrift", "Helvetica Neue", "DengXian", "PingFang SC", "Microsoft YaHei UI")
BODY_FONTS = ("DengXian", "PingFang SC")
SERIF_FONTS = ("Cambria", "Georgia")


QSS = """
QWidget {
    color: #1D2B36;
    font-family: "Bahnschrift", "Helvetica Neue", "DengXian", "PingFang SC", "Microsoft YaHei UI";
    font-size: 13px;
}
QMainWindow, QWidget#Central {
    background: #F1EBDC;
}

/* ---------- flight strip ---------- */
QWidget#TopBar {
    background: #1D2B36;
    border-bottom: 1px solid #0F1A22;
}
QLabel#StatusText {
    color: #F1EBDC;
    font-size: 12px;
}
QPushButton#Credit {
    background: transparent;
    border: none;
    color: #A7A18F;
    font-size: 11px;
    letter-spacing: 2px;
    padding: 0 2px;
}
QPushButton#Credit:hover {
    color: #F1EBDC;
}
QComboBox#DeviceCombo {
    background: transparent;
    border: 1px solid #4A565E;
    color: #F1EBDC;
    padding: 0 26px 0 10px;
    min-height: 30px;
    max-height: 30px;
    font-size: 13px;
}
QComboBox#DeviceCombo::drop-down {
    width: 26px;
    border: none;
}
QComboBox#DeviceCombo::down-arrow {
    image: none;
    width: 0;
    height: 0;
}
QPushButton#TopGhost {
    background: transparent;
    border: 1px solid #46525A;
    color: #A7A18F;
    padding: 0 10px;
    min-height: 30px;
    max-height: 30px;
    font-size: 12px;
}
QPushButton#TopGhost:hover {
    color: #F1EBDC;
    border-color: #6E7A82;
}

/* ---------- rail / tabs ---------- */
QWidget#Rail {
    background: #F1EBDC;
    border-right: 1px solid #A89C82;
}
QTabWidget#Tabs::pane {
    border: 0;
    background: #F1EBDC;
}
QTabBar {
    background: #F1EBDC;
    border-bottom: 1px solid #C9BFA8;
}
QTabBar::tab {
    background: transparent;
    color: #7C7868;
    padding: 0 14px;
    height: 35px;
    border-bottom: 2px solid transparent;
    font-size: 13px;
}
QTabBar::tab:selected {
    color: #1D2B36;
    border-bottom: 2px solid #A32C5E;
}
QTabBar::tab:hover {
    color: #46555F;
}
QWidget#Page {
    background: #F1EBDC;
}

/* ---------- fields ---------- */
QLabel#FieldLabel {
    color: #7C7868;
    font-size: 12px;
}
QLineEdit#Coord {
    background: transparent;
    border: none;
    border-bottom: 1px solid #A89C82;
    color: #1D2B36;
    font-family: "Cambria", "Georgia";
    font-size: 20px;
    padding: 0 2px;
    min-height: 38px;
}
QLineEdit#CoordSmall {
    background: transparent;
    border: none;
    border-bottom: 1px solid #A89C82;
    color: #1D2B36;
    font-family: "Cambria", "Georgia";
    font-size: 16px;
    padding: 0 2px;
    min-height: 32px;
}
QLineEdit#Coord:focus, QLineEdit#CoordSmall:focus {
    border-bottom: 1px solid #A32C5E;
}

/* ---------- primary / buttons ---------- */
QPushButton#Primary {
    background: #1D2B36;
    border: 1px solid #1D2B36;
    color: #F1EBDC;
    font-size: 13px;
    font-weight: 600;
    min-height: 38px;
    padding: 0 10px;
}
QPushButton#Primary:hover {
    background: #132029;
    border-color: #132029;
}
QPushButton#Primary:disabled {
    background: #B7AD96;
    border-color: #B7AD96;
    color: #EFE8D8;
}
QPushButton#AddPoint {
    background: transparent;
    border: 1px solid #1D2B36;
    color: #1D2B36;
    min-height: 32px;
    max-height: 32px;
    padding: 0 10px;
    font-size: 12px;
}
QPushButton#AddPoint:hover {
    background: #1D2B36;
    color: #F1EBDC;
}
QPushButton#TextCmd {
    background: transparent;
    border: none;
    color: #46555F;
    padding: 0;
    font-size: 12px;
    text-align: left;
}
QPushButton#TextCmd:hover {
    color: #1D2B36;
}
QPushButton#TextDanger {
    background: transparent;
    border: none;
    color: #9C3A2A;
    padding: 0;
    font-size: 12px;
    text-align: left;
}
QPushButton#TextDanger:hover {
    color: #7E2B1E;
}
QPushButton#DefaultButton {
    background: #F4EFE2;
    border: 1px solid #A89C82;
    color: #46555F;
    font-size: 12px;
    padding: 0 10px;
    min-height: 30px;
    max-height: 30px;
}
QPushButton#DefaultButton:hover {
    background: #EADFCB;
    border-color: #46555F;
    color: #1D2B36;
}
QPushButton#DangerStamp {
    background: #F7ECE7;
    border: 1px solid #C08A7E;
    color: #9C3A2A;
    font-size: 12px;
    padding: 0 10px;
    min-height: 30px;
    max-height: 30px;
}
QPushButton#DangerStamp:hover {
    background: #F2DFD7;
    border-color: #9C3A2A;
}
QWidget#CancelZone {
    border-top: 1px solid #D8CFBC;
}
QFrame#Leader {
    border: none;
    border-top: 1px dotted #A89C82;
    max-height: 1px;
}

/* ---------- route table ---------- */
QListWidget#Waypoints {
    background: transparent;
    border: 0;
    outline: 0;
}
QListWidget#Waypoints::item {
    background: transparent;
    border: 0;
}
QLabel#TotalLabel {
    color: #7C7868;
    font-size: 12px;
}
QLabel#TotalValue {
    color: #1D2B36;
    font-family: "Cambria", "Georgia";
    font-size: 18px;
    font-weight: 600;
}
QWidget#SpecRow {
    border-bottom: 1px solid #D8CFBC;
}
QLabel#SpecLabel {
    color: #46555F;
    font-size: 12px;
}
QComboBox#SpecCombo {
    background: transparent;
    border: none;
    color: #1D2B36;
    font-family: "Cambria", "Georgia";
    font-size: 15px;
    padding: 0 18px 0 0;
    min-height: 26px;
}
QComboBox#SpecCombo::drop-down {
    width: 16px;
    border: none;
}
QComboBox#SpecCombo::down-arrow {
    image: none;
    width: 0;
    height: 0;
}
QDoubleSpinBox#SpecSpin, QSpinBox#SpecSpin {
    background: transparent;
    border: none;
    color: #1D2B36;
    font-family: "Cambria", "Georgia";
    font-size: 15px;
    padding: 0 15px 0 0;
    min-height: 26px;
    max-height: 26px;
}
QDoubleSpinBox#SpecSpin::up-button, QSpinBox#SpecSpin::up-button {
    subcontrol-origin: border;
    subcontrol-position: top right;
    width: 13px;
    border-left: 1px solid #C9BFA8;
    background: transparent;
}
QDoubleSpinBox#SpecSpin::down-button, QSpinBox#SpecSpin::down-button {
    subcontrol-origin: border;
    subcontrol-position: bottom right;
    width: 13px;
    border-left: 1px solid #C9BFA8;
    background: transparent;
}
QLabel#SpecLabel:disabled, QDoubleSpinBox#SpecSpin:disabled, QSpinBox#SpecSpin:disabled {
    color: #B7AD96;
}
QFrame#Leader:disabled {
    border-top-color: #D8CFBC;
}
QCheckBox#LoopCheck {
    color: #7C7868;
    font-size: 12px;
    spacing: 5px;
}
QCheckBox#LoopCheck::indicator {
    width: 14px;
    height: 14px;
    border: 1px solid #A89C82;
    background: transparent;
}
QCheckBox#LoopCheck::indicator:checked {
    background: #A32C5E;
    border-color: #A32C5E;
}
QPushButton#Quick {
    background: transparent;
    border: 1px solid #A89C82;
    color: #7C7868;
    font-size: 11px;
    padding: 0;
}
QPushButton#Quick:hover {
    color: #1D2B36;
    border-color: #46555F;
}
QWidget#RunRow {
    border-top: 1px solid #C9BFA8;
}
QPushButton#Stamp {
    background: transparent;
    border: 1px solid #46555F;
    color: #46555F;
    font-size: 12px;
    min-height: 34px;
    padding: 0 4px;
}
QPushButton#Stamp:hover {
    background: #E9E0CE;
}
QPushButton#Stamp:disabled {
    color: #B7AD96;
    border-color: #D8CFBC;
}
QPushButton#StopStamp {
    background: transparent;
    border: 1px solid #46555F;
    color: #46555F;
    font-size: 12px;
    min-height: 34px;
    padding: 0 4px;
}
QPushButton#StopStamp:hover {
    background: #E9E0CE;
}
QPushButton#StopStamp:disabled {
    color: #B7AD96;
    border-color: #D8CFBC;
}
QComboBox#PresetCombo {
    background: transparent;
    border: 1px solid #A89C82;
    color: #1D2B36;
    font-size: 12px;
    min-height: 30px;
    max-height: 30px;
    padding: 0 22px 0 8px;
}
QComboBox#PresetCombo::drop-down {
    width: 20px;
    border: none;
}
QComboBox#PresetCombo::down-arrow {
    image: none;
    width: 0;
    height: 0;
}
QPushButton#PresetCmd {
    background: transparent;
    border: 1px solid #A89C82;
    color: #46555F;
    font-size: 11px;
    min-height: 30px;
    max-height: 30px;
    padding: 0 3px;
}
QPushButton#PresetCmd:hover {
    border-color: #46555F;
    color: #1D2B36;
}
QPushButton#PresetDanger {
    background: transparent;
    border: 1px solid #C08A7E;
    color: #9C3A2A;
    font-size: 11px;
    min-height: 30px;
    max-height: 30px;
    padding: 0 3px;
}
QPushButton#PresetDanger:hover {
    border-color: #9C3A2A;
}
QLabel#RouteStatus, QLabel#Hint {
    color: #7C7868;
    font-size: 11px;
}

/* ---------- log ---------- */
QTextEdit#Log {
    background: transparent;
    border: 0;
    border-top: 1px solid #D8CFBC;
    color: #7C7868;
    font-size: 12px;
    padding: 7px 14px;
    selection-background-color: #E7DCC6;
    selection-color: #1D2B36;
}

/* ---------- map / search ---------- */
QWidget#MapPanel {
    background: #E9E5D6;
}
QLineEdit#MapSearch {
    background: #F4EFE2;
    border: 1px solid #1D2B36;
    color: #1D2B36;
    font-size: 13px;
    padding: 0 11px;
    min-height: 38px;
    max-height: 38px;
}
QLineEdit#MapSearch:focus {
    border: 1px solid #A32C5E;
}
QLabel#SearchStatus {
    background: #F4EFE2;
    border-left: 1px solid #1D2B36;
    border-right: 1px solid #1D2B36;
    border-bottom: 1px solid #1D2B36;
    color: #7C7868;
    font-size: 11px;
    padding: 4px 9px;
}
QListWidget#SearchResults {
    background: #F4EFE2;
    border: 1px solid #1D2B36;
    border-top: 0;
    color: #46555F;
    font-size: 12px;
    outline: 0;
}
QListWidget#SearchResults::item {
    padding: 0 11px;
    border-bottom: 1px solid #E2D8C3;
}
QListWidget#SearchResults::item:hover {
    background: #EADFCB;
    color: #A32C5E;
}
QListWidget#SearchResults::item:selected {
    background: #E7DCC6;
    color: #A32C5E;
}
/* ---------- combo popups / scrollbars ---------- */
QComboBox QAbstractItemView {
    background: #F4EFE2;
    border: 1px solid #1D2B36;
    color: #1D2B36;
    font-size: 13px;
    selection-background-color: #E7DCC6;
    selection-color: #1D2B36;
    outline: 0;
}
QScrollBar:vertical {
    background: transparent;
    width: 8px;
    margin: 0;
}
QScrollBar::handle:vertical {
    background: #C9BFA8;
    min-height: 22px;
    border-radius: 4px;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0;
}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
    background: transparent;
}
QToolTip {
    background: #F4EFE2;
    color: #1D2B36;
    border: 1px solid #1D2B36;
    padding: 4px 7px;
    font-size: 12px;
}
"""
