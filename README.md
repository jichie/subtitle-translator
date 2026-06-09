# AI 字幕翻译器

自动提取视频中的英文字幕，通过 AI 翻译成中文字幕，支持番剧背景知识优化翻译质量。

## 功能特性

- **双模型协作**：V4-Pro 分析番剧生成翻译指南 + V4-Flash 批量翻译
- **番剧背景感知**：自动识别番剧名，生成角色语气/名词译法等指南
- **上下文模式**：翻译时传入前后字幕作为语境，对话更连贯
- **字幕编辑**：翻译完成后展示全部字幕，支持手动编辑和保存
- **指南管理**：自动生成、手动编辑、AI 微调翻译指南
- **批量翻译**：一键翻译文件夹内所有视频，自动跳过已翻译
- **文件夹订阅**：自动检测新集数并翻译
- **Webhook 通知**：支持企业微信/钉钉机器人
- **亮暗主题切换**：支持亮色/暗色主题，自动记忆偏好

## 快速开始

### 1. 配置 API 密钥

编辑 `docker-compose.yml` 中的环境变量，或在首次启动后通过 Web 界面配置。

### 2. 启动服务

```bash
docker compose up -d --build
```

### 3. 访问界面

打开浏览器访问 `http://localhost:7860`

### 4. 使用

1. 点击 ⚙️ 设置 → 填入 API Key 和 Base URL → 保存
2. 在左侧浏览目录或输入视频文件路径
3. 选择视频后点击「翻译」按钮
4. 翻译完成后可在「样例」标签编辑字幕

## 技术栈

- **后端**：Python FastAPI + OpenAI SDK
- **前端**：原生 HTML/CSS/JS，暗色/亮色主题
- **容器**：Docker + ffmpeg
- **AI**：DeepSeek V4-Pro（分析） + V4-Flash（翻译）

## 许可证

MIT License

---

## Windows 桌面版

从 [Releases](https://github.com/jichie/subtitle-translator/releases) 页面下载 `SubtitleTranslator.exe`，双击运行即可。

配置、缓存等运行时数据自动生成在 exe 同级 `data/` 目录下。

### 系统要求

- Windows 10/11 64位
- 4GB+ 内存（推荐 8GB）
- 网络连接（用于 API 调用）
