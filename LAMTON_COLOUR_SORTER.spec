# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['LAMTON_LINE_NEW_ESP32.py'],
    pathex=[],
    binaries=[],
    datas=[('app_icon.ico', '.'), ('app_icon.png', '.'), ('logo.png', '.'), ('hsv.png', '.'), ('background.png', '.'), ('bg2.png', '.'), ('recipes.json', '.'), ('camera_config.json', '.'), ('ejector_config.json', '.'), ('light_config.json', '.'), ('bg_config.json', '.'), ('system_info.json', '.'), ('system_config.json', '.'), ('wiper_config.json', '.'), ('tcm_license.dat', '.'), ('manufacturer_data_v3.db', '.'), ('mvsdk.py', '.'), ('best.pt', '.')],
    hiddenimports=[],
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
    [],
    exclude_binaries=True,
    name='LAMTON_COLOUR_SORTER',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['app_icon.ico'],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='LAMTON_COLOUR_SORTER',
)
