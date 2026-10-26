# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
import importlib.util

ROOT = Path(SPECPATH).resolve().parent

# pytun_pmd3 在 import 时通过 ctypes 加载 wintun.dll；PyInstaller 不会自动收集它。
_pytun = importlib.util.find_spec('pytun_pmd3')
if _pytun is None or _pytun.origin is None:
    raise SystemExit('pytun_pmd3 is not installed; cannot package tunnel support')
PYTUN = Path(_pytun.origin).parent

# pymobiledevice3 的 CLI / WebInspector / 截图功能依赖；本应用不会加载。
EXCLUDES = [
    'IPython', 'ipykernel', 'ipywidgets', 'jedi', 'matplotlib', 'matplotlib_inline',
    'numpy', 'PIL', 'tkinter', '_tkinter', 'zmq', 'tornado', 'traitlets', 'parso',
]

# 未使用的 Qt addon：DLL 前缀 Qt6Xxx，Python 绑定前缀 QtXxx。
QT_DROP_STEMS = (
    '3D', 'Charts', 'DataVisualization', 'Graphs', 'Multimedia', 'Pdf',
    'Quick3D', 'QuickControls2', 'QuickTemplates2', 'QuickDialogs2',
    'Location', 'Svg', 'Sql', 'Test', 'Designer', 'Help', 'Bluetooth', 'Nfc',
    'Sensors', 'SerialPort', 'SerialBus', 'RemoteObjects', 'Scxml',
    'StateMachine', 'TextToSpeech', 'UiTools', 'WebSockets', 'Xml',
    'HttpServer', 'Coap', 'Mqtt', 'OpcUa', 'Labs', 'SpatialAudio',
    'WebEngineQuick', 'QmlLocalStorage', 'QuickNativeStyle', 'QuickTest',
    'QuickVectorImage',
)


def _drop_qt_binary(name: str) -> bool:
    n = name.replace('\\', '/')
    if '/qml/' in n:
        return True
    base = n.rsplit('/', 1)[-1]
    return any(
        base.startswith('Qt6' + stem) or base.startswith('Qt' + stem)
        for stem in QT_DROP_STEMS
    )


def _drop_qt_data(name: str) -> bool:
    n = name.replace('\\', '/')
    if '/qml/' in n or '/plugins/qmltooling/' in n:
        return True
    if '/resources/' in n and ('devtools' in n or '.debug.' in n):
        return True
    if '/translations/qtwebengine_locales/' in n:
        return n.rsplit('/', 1)[-1] not in ('zh-CN.pak', 'en-US.pak')
    if '/translations/' in n:
        base = n.rsplit('/', 1)[-1]
        return not (base.endswith('_zh_CN.qm') or base.endswith('_en.qm'))
    # 这些插件链接了上面删掉的 Qt6Pdf / Qt6Svg / Qt6SerialPort。
    return n.endswith((
        '/imageformats/qpdf.dll',
        '/imageformats/qsvg.dll',
        '/iconengines/qsvgicon.dll',
        '/position/qtposition_nmea.dll',
    ))


a = Analysis(
    [str(ROOT / 'src' / 'main.py')],
    pathex=[str(ROOT / 'src')],
    binaries=[],
    datas=[
        (str(ROOT / 'src' / 'web'), 'web'),
        (str(ROOT / 'assets' / 'app.ico'), '.'),
        (str(PYTUN / 'wintun' / 'bin' / 'amd64' / 'wintun.dll'),
         'pytun_pmd3/wintun/bin/amd64'),
        (str(PYTUN / 'wintun' / 'LICENSE.txt'), 'pytun_pmd3/wintun'),
    ],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=EXCLUDES,
    noarchive=False,
    optimize=0,
)

a.binaries = [b for b in a.binaries if not _drop_qt_binary(b[0])]
a.datas = [d for d in a.datas if not _drop_qt_data(d[0])]

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='Elsewhere',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    icon=str(ROOT / 'assets' / 'app.ico'),
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='Elsewhere',
)
