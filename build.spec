# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller 打包配置
用法: pyinstaller build.spec
"""

import os
import sys
import requests
import zipfile
import shutil
from pathlib import Path

BLOCK_CATALOGS = []
APP_NAME = "SubtitleTranslator"

# ── 1. 下载 Windows ffmpeg ──────────────────────────────────
FFMPEG_DIR = os.path.join(os.path.dirname(__file__), "ffmpeg_win")
FFMPEG_ZIP = os.path.join(os.path.dirname(__file__), "ffmpeg.zip")

if not os.path.exists(os.path.join(FFMPEG_DIR, "bin", "ffmpeg.exe")):
    print("📥 下载 ffmpeg for Windows...")
    url = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
    os.makedirs(FFMPEG_DIR, exist_ok=True)
    
    # 下载
    r = requests.get(url, stream=True)
    r.raise_for_status()
    with open(FFMPEG_ZIP, "wb") as f:
        for chunk in r.iter_content(chunk_size=8192):
            f.write(chunk)
    
    # 解压
    with zipfile.ZipFile(FFMPEG_ZIP, "r") as z:
        z.extractall(FFMPEG_DIR)
    
    # 移动 ffmpeg.exe 到根目录
    for root, dirs, files in os.walk(FFMPEG_DIR):
        for f in files:
            if f == "ffmpeg.exe":
                src = os.path.join(root, f)
                dst = os.path.join(FFMPEG_DIR, "ffmpeg.exe")
                shutil.move(src, dst)
                break
    
    # 清理
    if os.path.exists(FFMPEG_ZIP):
        os.remove(FFMPEG_ZIP)
    print("✅ ffmpeg 下载完成")


# ── 2. PyInstaller 配置 ─────────────────────────────────────
a = Analysis(
    ['launcher.py'],
    pathex=[os.path.dirname(__file__)],
    binaries=[
        # 只包含 ffmpeg.exe（Windows 下不需要 ffprobe 即可工作）
        (os.path.join(FFMPEG_DIR, 'ffmpeg.exe'), '.'),
    ],
    datas=[
        # 前端静态文件
        ('app/static', 'app/static'),
        # data 目录（空目录，运行时创建）
        ('data', 'data'),
    ],
    hiddenimports=[
        'uvicorn',
        'uvicorn.logging',
        'uvicorn.loops',
        'uvicorn.loops.auto',
        'uvicorn.protocols',
        'uvicorn.protocols.http',
        'uvicorn.protocols.http.auto',
        'uvicorn.server',
        'aiofiles',
        'pysrt',
        'openai',
        'fastapi',
        'starlette',
        'multipart',
    ],
    hookspath=[],
    hooksconfig={},
    excludes=[
        'tkinter',
        'matplotlib',
        'PIL',
        'Pillow',
        'numpy',
        'pandas',
        'scipy',
        'torch',
        'tensorflow',
        'cuda',
        'notebook',
        'jupyter',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=None,
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name=APP_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,          # 显示控制台窗口（方便看日志）
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='icon.ico' if os.path.exists('icon.ico') else None,
)
