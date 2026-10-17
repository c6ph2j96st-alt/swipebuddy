# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for SwipeBuddy (onedir, windowed). Build with:
# Entry is src/swipebuddy/__main__.py (absolute imports only).
#   pyinstaller build.spec --noconfirm --clean
from PyInstaller.utils.hooks import (
    collect_data_files,
    collect_dynamic_libs,
    collect_submodules,
)

block_cipher = None

datas = [("assets/icon.png", "assets")]
binaries = []
hiddenimports = []

# Playwright: the node driver (node.exe + package/) is required; browsers are NOT bundled
# (they live in %LOCALAPPDATA%\ms-playwright and are never inside the package dir).
datas += collect_data_files("playwright", include_py_files=False)
hiddenimports += collect_submodules("playwright")

# onnxruntime: CPU runtime DLLs
binaries += collect_dynamic_libs("onnxruntime")
hiddenimports += ["onnxruntime", "onnxruntime.capi._pybind_state"]

# App submodules (imported lazily in places)
hiddenimports += collect_submodules("swipebuddy")

hiddenimports += ["PIL.Image", "httpx", "numpy"]

excludes = [
    # heavy / unused ML + plotting + misc
    "torch", "torchvision", "tensorflow", "ultralytics", "cv2", "matplotlib",
    "tkinter", "IPython", "pytest", "scipy", "pandas",
    # unused Qt modules
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick",
    "PySide6.QtWebChannel", "PySide6.QtWebSockets", "PySide6.QtWebView",
    "PySide6.Qt3DCore", "PySide6.Qt3DRender", "PySide6.Qt3DInput", "PySide6.Qt3DLogic",
    "PySide6.Qt3DAnimation", "PySide6.Qt3DExtras",
    "PySide6.QtMultimedia", "PySide6.QtMultimediaWidgets", "PySide6.QtSpatialAudio",
    "PySide6.QtQuick3D", "PySide6.QtCharts", "PySide6.QtDataVisualization",
    "PySide6.QtPdf", "PySide6.QtPdfWidgets",
    "PySide6.QtQuick", "PySide6.QtQml", "PySide6.QtQuickWidgets", "PySide6.QtQuickControls2",
    "PySide6.QtDesigner", "PySide6.QtHelp", "PySide6.QtTest", "PySide6.QtSql",
    "PySide6.QtBluetooth", "PySide6.QtNfc", "PySide6.QtSensors", "PySide6.QtSerialPort",
    "PySide6.QtPositioning", "PySide6.QtRemoteObjects", "PySide6.QtScxml",
    "PySide6.QtTextToSpeech", "PySide6.QtGraphs", "PySide6.QtLocation",
]

a = Analysis(
    ["src/swipebuddy/__main__.py"],
    pathex=["src"],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="SwipeBuddy",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    icon="assets/icon.ico",
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="SwipeBuddy",
)
