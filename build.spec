# -*- mode: python ; coding: utf-8 -*-
import sys

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[('src', 'src'), ('icon.ico', '.')],
    hiddenimports=[
        'pypdf',
        'fitz',
        'fitz._fitz',
        'markdown',
        'pymdownx',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'nltk',
        'scipy',
        'skimage',
        'faster_whisper',
        'torch',
        'tensorflow',
        'cv2',
        'opencv-python',
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='Book-Distiller',
    icon='icon.ico',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
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
    name='Book-Distiller',
)

# macOS: 生成 .app bundle
if sys.platform == 'darwin':
    app = BUNDLE(
        coll,
        name='Book-Distiller.app',
        icon='icon.ico',
        bundle_identifier='com.bookdistiller.app',
        info_plist={
            'NSHighResolutionCapable': True,
            'CFBundleShortVersionString': '2.0.0',
        },
    )
