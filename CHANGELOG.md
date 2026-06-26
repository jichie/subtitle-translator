# 更新日志

## [1.2.0] — 2026-06-26

### 🔴 严重 Bug 修复

- **修复订阅文件夹（Watch）翻译功能完全不可用** — 发现以下多个问题并逐一修复：
  - **`track_index=0` 硬编码** — Watch 模式总是提取第 0 条字幕流，不检测语言。很多番剧第 0 条不是英语字幕，导致翻译错误语言或提取失败。现已改为自动检测英语字幕流（与批量翻译逻辑一致）。
  - **无外挂 SRT 支持** — 很多动漫（如 ANi 压制的 MP4）没有内嵌字幕流，但同目录下有外挂 `.srt`/`.ass` 文件。原代码直接报"字幕提取失败"跳过。现已添加外挂字幕自动查找 fallback。
  - **失败视频无限重试** — 没有字幕的视频每 5 分钟重新尝试一次，不断创建失败任务。现添加 `_failed_videos` 集合去重，失败后不再重复尝试。
  - **`map_path` 安全修复后中断 Watch 循环** — 路径穿越防护修复后 `map_path` 可能抛 `ValueError`，直接中断整个 Watch 线程。现添加 try/except 保护。
  - **`translator` 未配置时静默跳过** — API Key 未配置时 Watch 模式静默跳过所有视频，没有任何提示。现添加日志输出。

### 🟢 新增方法

- **`find_subtitle_track()`** — 自动检测视频的英语字幕流索引，找不到时返回 None。
- **`find_external_srt()`** — 查找视频同目录下的外挂字幕文件（`.eng.srt`/`.en.srt`/`.srt`/`.ass`/`.ssa`）。
- **`run_translation()` 外挂 SRT fallback** — 当内嵌字幕提取失败时，自动查找并使用外挂字幕文件。

---

## [1.1.0] — 2026-06-26

### 🔴 严重 Bug 修复

- **修复 `logger` 未定义导致运行时崩溃** — `_send_webhook`、`start_batch` 等路径中使用了未定义的 `logger` 变量，触发 `NameError`。已添加 `import logging` 并创建全局 logger 实例。
- **修复 `icon_36` 路由缺失装饰器** — `/icon_36.png` 端点因缺少 `@app.get()` 装饰器导致 404，前端图标无法加载。

### 🟠 安全修复

- **修复 `/api/download` 路径穿越漏洞** — 端点直接使用客户端传入的 `path` 参数，攻击者可下载服务器任意文件。现已通过 `map_path()` 规范化路径，并校验结果必须在 `VIDEO_ROOT` 目录内。
- **修复 `map_path` 目录穿越** — `/videos/../../etc/passwd` 等路径可逃逸出视频目录。现使用 `os.path.realpath()` 规范化后做前缀检查。
- **Docker 容器以非 root 用户运行** — 创建 `appuser`（UID 1000）运行应用，entrypoint 脚本在启动时修复挂载卷权限后自动降权。
- **Docker 添加健康检查** — 新增 `HEALTHCHECK` 指令，每 30 秒探测服务可用性。

### 🟡 逻辑错误修复

- **修复 `_clean_punct` 误删英文句点** — 原正则 `[。.]` 同时删除了中文句号和英文句点，导致 `3.14` → `314`、`Mr. Smith` → `Mr Smith`。改为只删中文句号，英文句点仅在非数字上下文删除。
- **修复批量翻译和自动 Watch 模式的取消检测不生效** — `make_fn` 未处理 `check_cancelled` 参数，导致取消信号被忽略。现已同步取消检测逻辑。
- **修复 `tasks` 字典无限增长（内存泄漏）** — 已完成/失败/取消的任务从不清理。新增后台线程每 10 分钟清理超过 1 小时的历史任务。
- **修复异步端点阻塞事件循环** — `get_tracks`、`scan_folder`、`browse`、`subtitle_read`、`subtitle_save`、`start_batch` 等端点中使用了 `subprocess.run`、`os.walk`、`pysrt.open` 等阻塞 I/O，但声明为 `async def`，阻塞了 asyncio 事件循环。已改为同步 `def`（FastAPI 自动走线程池）。
- **修复 `subtitle_read` docstring 位置错误** — docstring 被放在代码语句之后，变成被丢弃的字符串表达式。
- **修复 `_guide_cache` 多线程竞态** — 类变量被 `ThreadPoolExecutor` 多个线程并发读写。现添加 `threading.Lock` 保护所有读写操作。
- **修复 `scan_videos` 静默吞异常** — `except Exception: pass` 隐藏了扫描错误。现改为打印错误日志。

### 🟢 优化改进

- **SRT 编码自动 fallback** — `pysrt.open` 原硬编码 `utf-8`，遇到非 UTF-8 字幕文件会报错。现依次尝试 `utf-8` → `utf-8-sig` → `gbk` → `latin-1` → `shift-jis`。
- **收紧 `detect_anime_name` 正则** — Format 5 的 `^\d+(?:\s|\D)` 过于宽泛，会误匹配大多数以数字开头的文件名。现改为一组更严格的模式，仅匹配明确以集数标记开头的文件。
- **`launcher.py` 端口占用友好提示** — 当 7860 端口被占用时，不再直接崩溃，而是输出清晰的错误信息和解决建议。
- **清理未使用的依赖** — 从 `requirements.txt` 中移除未使用的 `aiohttp`。
- **移除函数内冗余 import** — `favicon`、`icon_256_file` 等函数中的重复 `import` 移至文件顶部。

### 📦 部署

- **新增 `entrypoint.sh`** — 容器启动入口脚本，先以 root 修复 `/data` 和 `/videos` 目录权限，再降权为 `appuser` 运行应用。解决 Docker 挂载卷权限问题（`Permission denied: /data/history.json`）。
- **Dockerfile 重构** — 调整 `USER` 指令位置，配合 entrypoint 实现安全的权限修复流程。

---

## [1.0.0] — 初始版本

- 基于 FastAPI + OpenAI API 的字幕翻译工具
- 支持视频内嵌字幕提取、AI 翻译、翻译指南生成
- 支持手动翻译、批量翻译、文件夹自动 Watch
- 内置字幕编辑器，支持翻译纠错和指南微调
- Webhook 通知（企业微信/钉钉）
- Docker 部署 / Windows 打包
