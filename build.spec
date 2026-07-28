# -*- mode: python ; coding: utf-8 -*-
import sys
from PyInstaller.utils.hooks import collect_all, collect_submodules, collect_data_files

# PyMuPDF (fitz) 带原生库与字体/资源，用 collect_all 确保完整打包
fitz_datas, fitz_binaries, fitz_hidden = collect_all('fitz')

# markdown / pymdownx 通过 entry point 动态加载扩展，PyInstaller 容易漏掉，
# 显式收集子模块 + 数据文件，保证渲染扩展（tables、tasklist、magiclink 等）可用
_md_hidden = collect_submodules('markdown')
_pymdownx_hidden = collect_submodules('pymdownx')
_md_datas = collect_data_files('markdown')
_pymdownx_datas = collect_data_files('pymdownx')

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=fitz_binaries,
    datas=[('src', 'src'), ('icon.ico', '.')] + fitz_datas + _md_datas + _pymdownx_datas,
    hiddenimports=[
        'fitz',
        'pypdf',
        'markdown',
        'pymdownx.tasklist',
        'pymdownx.magiclink',
    ] + fitz_hidden + _md_hidden + _pymdownx_hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Video-Distiller 残留依赖，Book-Distiller 不再使用
        'nltk',
        'faster_whisper',
        'whisper',
        'torch',
        'torchaudio',
        'skimage',
        'scipy',
        'cv2',
        'opencv-python',
        'matplotlib',
        'tensorflow',
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
