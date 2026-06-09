#!/usr/bin/env bash
# build_windows.sh
# 在 Windows 上打包字幕翻译器为 exe
# 用法: bash build_windows.sh

set -e

echo "========================================"
echo "  字幕翻译器 - Windows 打包脚本"
echo "========================================"

# 检查依赖
command -v python3 >/dev/null 2>&1 || { echo "❌ 需要安装 Python 3.10+"; exit 1; }

# 安装打包依赖
pip install -r requirements.txt
pip install pyinstaller requests

# 清理旧构建
rm -rf build dist __pycache__ *.spec

# 运行 PyInstaller
python -m PyInstaller build.spec

# 检查结果
if [ -f "dist/SubtitleTranslator.exe" ]; then
    echo "========================================"
    echo "✅ 打包成功!"
    echo "   输出: dist/SubtitleTranslator.exe"
    echo "   大小: $(du -h dist/SubtitleTranslator.exe | cut -f1)"
    echo "========================================"
else
    echo "❌ 打包失败"
    exit 1
fi
