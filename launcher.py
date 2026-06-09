#!/usr/bin/env python3
"""
字幕翻译器 - Windows 启动器
打包后用户双击 exe 即可启动服务并自动打开浏览器
"""
import os
import sys
import webbrowser
import threading
import time
import uvicorn

# 获取 exe 所在目录（打包后是临时解压目录）
if getattr(sys, 'frozen', False):
    BASE_DIR = sys._MEIPASS
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

os.chdir(BASE_DIR)
sys.path.insert(0, os.path.join(BASE_DIR, 'app'))

# 确保 data 目录存在
DATA_DIR = os.path.join(os.path.dirname(BASE_DIR) if getattr(sys, 'frozen', False) else BASE_DIR, 'data')
os.makedirs(DATA_DIR, exist_ok=True)
os.environ['DATA_DIR'] = DATA_DIR
os.environ['VIDEO_MOUNT'] = os.path.abspath(os.path.join(DATA_DIR, '..'))  # 视频目录默认为上级


def open_browser():
    """延迟打开浏览器"""
    time.sleep(2)
    webbrowser.open('http://127.0.0.1:7860')


if __name__ == '__main__':
    print("=" * 50)
    print("  字幕翻译器 Subtitle Translator")
    print("  正在启动服务...")
    print("=" * 50)
    print(f"  数据目录: {DATA_DIR}")
    print(f"  访问地址: http://127.0.0.1:7860")
    print("=" * 50)

    threading.Thread(target=open_browser, daemon=True).start()

    # 启动 FastAPI 服务
    from app.main import app
    uvicorn.run(app, host="127.0.0.1", port=7860, log_level="info")
