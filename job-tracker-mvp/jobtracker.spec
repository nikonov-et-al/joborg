from PyInstaller.utils.hooks import collect_data_files, collect_submodules


a = Analysis(
    ["jobtracker/desktop.py"],
    pathex=["."],
    binaries=[],
    datas=collect_data_files("jobtracker"),
    hiddenimports=["webview", *collect_submodules("uvicorn")],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="JobTracker",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
)
