#!/usr/bin/env bash
# package_fpnos.sh
# 在飞牛 OS 上打包 .fpk 应用
# 在飞牛 NAS 的 SSH 终端中运行

set -e

APP_NAME="subtitle-translator"
WORK_DIR="/tmp/fpk_build"

echo "========================================"
echo "  飞牛 OS 应用打包脚本"
echo "========================================"

# 1. 创建应用骨架
echo "📦 创建应用骨架..."
rm -rf "$WORK_DIR"
mkdir -p "$WORK_DIR"
cd "$WORK_DIR"
fnpack create "$APP_NAME" -t docker

# 2. 替换 docker-compose.yml
echo "📝 写入 docker-compose.yml..."
cat > "$WORK_DIR/$APP_NAME/app/docker/docker-compose.yaml" << 'DOCKEREOF'
services:
  subtitle-translator:
    build: .
    ports:
      - "7860:7860"
    volumes:
      - /vol1/1000/影视:/videos
      - ./data:/data
    environment:
      - DATA_DIR=/data
      - VIDEO_MOUNT=/videos
    restart: unless-stopped
DOCKEREOF

# 3. 下载项目源码
echo "📥 下载项目源码..."
cd "$WORK_DIR/$APP_NAME"
git clone https://github.com/jichie/subtitle-translator.git tmp_src 2>/dev/null || {
    echo "⚠️ 无法直连 GitHub，请手动复制项目文件到: $WORK_DIR/$APP_NAME/app/"
    echo "   把 app/ 目录复制过来即可"
}

if [ -d "tmp_src" ]; then
    cp -r tmp_src/app/* app/
    cp tmp_src/Dockerfile .
    cp tmp_src/requirements.txt .
    cp tmp_src/.gitignore .
    rm -rf tmp_src
    echo "✅ 源码已复制"
fi

# 4. 放入图标
echo "🖼️ 放入图标..."
# 使用 GitHub 上已有的 icon_256.png
curl -sL "https://raw.githubusercontent.com/jichie/subtitle-translator/main/icon_256.png" -o "ICON_256.png" 2>/dev/null || echo "⚠️ 请手动放置 ICON_256.png"
# 用同个文件作为 64x64 图标
cp ICON_256.png ICON.PNG 2>/dev/null || true

# 5. 放入截图
mkdir -p app/ui/images
for i in 1 2 3 4 5; do
    curl -sL "https://raw.githubusercontent.com/jichie/subtitle-translator/main/Preview/0${i}-dark-main.jpg" -o "app/ui/images/screenshot_${i}.jpg" 2>/dev/null || true
done

# 6. 填写 manifest
echo "📋 填写 manifest..."
cat > manifest << 'MANEOF'
appname=subtitle-translator
version=1.0.0
display_name=字幕翻译器
desc=AI驱动的番剧字幕自动翻译工具。自动提取视频英文字幕，通过AI翻译成中文字幕，支持番剧背景知识优化、批量处理、文件夹订阅自动翻译。
arch=x86_64
source=thirdparty
maintainer=Jichie
maintainer_url=https://github.com/jichie/subtitle-translator
os_min_version=0.8.27
service_port=7860
checkport=false
MANEOF

# 7. 打包
echo "🔨 正在打包..."
cd "$WORK_DIR/$APP_NAME"
fnpack build --directory .

# 8. 输出结果
FPK_FILE="${APP_NAME}.fpk"
if [ -f "$FPK_FILE" ]; then
    echo "========================================"
    echo "✅ 打包成功!"
    echo "   文件: $WORK_DIR/$APP_NAME/$FPK_FILE"
    echo "   大小: $(du -h "$FPK_FILE" | cut -f1)"
    echo ""
    echo "📤 下一步："
    echo "   1. 下载 $FPK_FILE 到电脑"
    echo "   2. 登录 developer.fnnas.com"
    echo "   3. 上传 .fpk 文件"
    echo "========================================"
else
    echo "❌ 打包失败"
    exit 1
fi
