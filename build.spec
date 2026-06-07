# -*- mode: python ; coding: utf-8 -*-
import sys
from PyInstaller.utils.hooks import collect_submodules, collect_data_files

# markdown + pymdownx 的子模块和数据文件需要完整收集（动态加载，PyInstaller 无法自动检测）
_md_hidden = collect_submodules('markdown')
_pymdownx_hidden = collect_submodules('pymdownx')
_md_datas = collect_data_files('markdown')
_pymdownx_datas = collect_data_files('pymdownx')

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[('src', 'src'), ('icon.ico', '.')] + _md_datas + _pymdownx_datas,
    hiddenimports=[
        'pypdf',
        'fitz',
        'fitz._fitz',
    ] + _md_hidden + _pymdownx_hidden,
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
