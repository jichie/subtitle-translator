import asyncio, json, os, re, time, uuid, threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional

import pysrt

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from .translator import Translator, detect_anime_name, map_path, scan_videos
from .translator import VIDEO_ROOT, DATA_DIR

app = FastAPI(title="字幕翻译器")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

executor = ThreadPoolExecutor(max_workers=5)
tasks: dict[str, dict] = {}
translator: Optional[Translator] = None
config: dict = {}
history: list[dict] = []
watch_folders: dict[str, dict] = {}  # path -> {interval, last_scan, enabled}
watch_thread: Optional[threading.Thread] = None

CONFIG_FILE = os.path.join(DATA_DIR, "config.json")
HISTORY_FILE = os.path.join(DATA_DIR, "history.json")
WATCH_FILE = os.path.join(DATA_DIR, "watch_folders.json")


def load_config():
    global config, translator
    try:
        with open(CONFIG_FILE, encoding="utf-8") as f: config = json.load(f)
    except Exception:
        config = {"api_key": "", "base_url": "",
                  "model": "deepseek-v4-flash", "context_model": "deepseek-v4-pro",
                  "batch_size": 12, "use_context": False, "webhook_url": "", "webhook_type": "wecom"}
    if config.get("api_key"):
        translator = Translator(config["api_key"], config["base_url"])


def save_config():
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)


def load_history():
    global history
    try:
        with open(HISTORY_FILE, encoding="utf-8") as f: history = json.load(f)
    except Exception:
        history = []


def save_history():
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history[-200:], f, ensure_ascii=False, indent=2)  # keep last 200


def load_watch():
    global watch_folders
    try:
        with open(WATCH_FILE, encoding="utf-8") as f: watch_folders = json.load(f)
    except Exception:
        watch_folders = {}


def save_watch():
    with open(WATCH_FILE, "w", encoding="utf-8") as f:
        json.dump(watch_folders, f, ensure_ascii=False, indent=2)


def watch_loop():
    """后台线程：定期扫描订阅文件夹"""
    while True:
        time.sleep(300)  # 5分钟
        for fpath, info in list(watch_folders.items()):
            if not info.get("enabled", True):
                continue
            real = map_path(fpath)
            if not os.path.isdir(real):
                continue
            videos = scan_videos(fpath)
            translated = set()
            for v in videos:
                base = Path(v).stem
                if os.path.exists(str(Path(v).parent / f"{base}.chi.srt")):
                    translated.add(v)
            new_videos = [v for v in videos if v not in translated]
            if new_videos and translator:
                for v in new_videos[:3]:  # max 3 at a time
                    anime = detect_anime_name(v)
                    tid = str(uuid.uuid4())[:8]
                    task = {"task_id": tid, "video": v, "anime_name": anime,
                            "state": "pending", "progress": 0, "total": 0,
                            "message": "自动翻译", "logs": [], "guide": None,
                            "output": None, "samples": [], "source": "watch"}
                    tasks[tid] = task

                    def make_fn(t):
                        def fn(**kw):
                            if kw.get("check_cancelled"):
                                return t.get("state") in ("cancelled", "error")
                            if t.get("state") == "cancelled":
                                return True
                            for k, v in kw.items():
                                if k == "log":
                                    t["logs"].append(f"[{t.get('state', '?')}] {v}")
                                elif k in t:
                                    t[k] = v
                            if kw.get("state") == "done":
                                _add_history(t)
                        return fn

                    def run_trans(t):
                        try:
                            translator.run_translation(
                                t["video"], 0, t["anime_name"],
                                config["model"], config["context_model"],
                                config["batch_size"], config.get("use_context", False),
                                make_fn(t))
                        except Exception as e:
                            t["state"] = "error"
                            t["message"] = str(e)
                            t["logs"].append(f"[error] {e}")

                    executor.submit(run_trans, task)
            watch_folders[fpath]["last_scan"] = time.time()
            save_watch()


def _send_webhook(task: dict):
    '''发送翻译完成通知（同步）'''
    url = config.get("webhook_url", "").strip()
    if not url:
        return
    wtype = config.get("webhook_type", "wecom")
    try:
        import urllib.request
        vname = Path(task.get("video","")).name
        total = task.get("total", 0)
        msg = f"✅ 字幕翻译完成\n\n📺 {vname}\n📊 {total}条\n📁 {task.get('output','')}"
        if wtype == "dingtalk":
            payload = {"msgtype": "text", "text": {"content": msg}}
        elif wtype == "wecom":
            payload = {"msgtype": "text", "text": {"content": msg}}
        else:
            payload = {"content": msg}
        data = json.dumps(payload).encode()
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:
        logger.warning(f"Webhook failed: {e}")


def _add_history(task: dict):
    entry = {
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "video": Path(task.get("video", "")).name,
        "video_path": task.get("video", ""),
        "anime": task.get("anime_name"),
        "output": task.get("output"),
        "total": task.get("total", 0),
        "state": task.get("state", "done"),
        "source": task.get("source", "manual"),
    }
    history.insert(0, entry)
    save_history()
    # 发送通知（在单独线程中）
    import threading
    threading.Thread(target=_send_webhook, args=(task,), daemon=True).start()


load_config()
load_history()
load_watch()

# Start watch thread
if watch_folders:
    try:
        watch_thread = threading.Thread(target=watch_loop, daemon=True)
        watch_thread.start()
    except Exception as e:
        pass


@app.get("/favicon.ico")
async def favicon():
    from fastapi.responses import FileResponse
    import os
    ico = os.path.join(os.path.dirname(__file__), "static", "icon.ico")
    if os.path.exists(ico):
        return FileResponse(ico, media_type="image/x-icon")
    return ""

@app.get("/icon_256.png")
async def icon_256_file():
    from fastapi.responses import FileResponse
    import os
    png = os.path.join(os.path.dirname(__file__), "static", "icon_256.png")
    if os.path.exists(png):
        return FileResponse(png, media_type="image/png")
    return ""
async def icon_36():
    from fastapi.responses import FileResponse
    ico_dir = os.path.join(os.path.dirname(__file__), "static")
    png = os.path.join(ico_dir, "icon_36.png")
    if os.path.exists(png):
        return FileResponse(png, media_type="image/png")
    return ""

@app.get("/")
async def index():
    with open(os.path.join(os.path.dirname(__file__), "static", "index.html"), encoding="utf-8") as f:
        return HTMLResponse(f.read())


# ── Config ──

class ConfigUpdate(BaseModel):
    api_key: str = ""
    base_url: str = ""
    model: str = "deepseek-v4-flash"
    context_model: str = "deepseek-v4-pro"
    batch_size: int = 12
    use_context: bool = False
    webhook_url: str = ""
    webhook_type: str = "wecom"


@app.get("/api/config")
async def get_config():
    return {k: v for k, v in config.items() if k != "api_key"} | {
        "api_key": "***" if config.get("api_key") else ""}


@app.post("/api/config")
async def update_config(data: ConfigUpdate):
    global translator
    if data.api_key and data.api_key != "***":
        config["api_key"] = data.api_key
    # 只更新非空字段，保留已有值
    if data.base_url:
        config["base_url"] = data.base_url
    if data.model:
        config["model"] = data.model
    if data.context_model:
        config["context_model"] = data.context_model
    if data.batch_size and data.batch_size >= 5:
        config["batch_size"] = data.batch_size
    config["use_context"] = data.use_context
    if data.webhook_url:
        config["webhook_url"] = data.webhook_url
    if data.webhook_type:
        config["webhook_type"] = data.webhook_type
    save_config()
    if config["api_key"]:
        translator = Translator(config["api_key"], config["base_url"])
    return {"ok": True}


# ── Browse ──

@app.get("/api/browse")
async def browse(path: str = "/", sort: str = "name"):
    real_path = map_path(path)
    if not os.path.exists(real_path):
        return {"error": "Path not found"}
    entries = []
    for name in os.listdir(real_path):
        full = os.path.join(real_path, name)
        is_dir = os.path.isdir(full)
        ext = os.path.splitext(name)[1].lower() if not is_dir else ""
        mtime = os.path.getmtime(full)
        entries.append({
            "name": name, "path": os.path.join(path, name).replace("//", "/"),
            "is_dir": is_dir,
            "is_video": ext in (".mkv", ".mp4", ".avi", ".mov", ".ts"), "is_srt": ext in (".srt", ".ass"),
            "mtime": mtime,
            "mtime_str": time.strftime("%Y-%m-%d %H:%M", time.localtime(mtime)),
        })
    if sort == "time":
        entries.sort(key=lambda e: (not e["is_dir"], -e["mtime"]))
    else:
        entries.sort(key=lambda e: (not e["is_dir"], e["name"]))
    return {"path": path, "entries": entries, "sort": sort}


# ── Tracks ──

@app.get("/api/tracks")
async def get_tracks(path: str):
    real = map_path(path)
    if not translator:
        raise HTTPException(400, "请先配置 API")
    tracks = translator.list_tracks(real)
    anime = detect_anime_name(real)
    return {"tracks": [{"index": t["index"], "language": t["language"],
                        "title": t["title"]} for t in tracks],
            "anime_name": anime, "filename": Path(real).name}


# ── Scan folder ──

@app.get("/api/scan_folder")
async def scan_folder(path: str = "/"):
    videos = scan_videos(path)
    result = []
    for v in videos:
        name = Path(v).name
        has_chi = os.path.exists(str(Path(v).parent / f"{Path(v).stem}.chi.srt"))
        result.append({"path": v, "name": name, "translated": has_chi,
                       "anime": detect_anime_name(v)})
    return {"videos": result, "total": len(result),
            "translated": sum(1 for r in result if r["translated"])}


# ── Translate ──

class TranslateRequest(BaseModel):
    path: str
    track_index: int = 0
    anime_name: Optional[str] = None
    use_context: Optional[bool] = None
    videos: Optional[list] = None  # 批量翻译时指定具体视频路径


@app.post("/api/translate")
async def start_translate(req: TranslateRequest):
    if not translator:
        raise HTTPException(400, "请先配置 API")
    real = map_path(req.path)
    if not os.path.exists(real):
        raise HTTPException(404, "文件不存在")
    anime = req.anime_name or detect_anime_name(real)
    use_ctx = req.use_context if req.use_context is not None else config.get("use_context", False)

    tid = str(uuid.uuid4())[:8]
    task = {"task_id": tid, "video": real, "anime_name": anime,
            "state": "pending", "progress": 0, "total": 0,
            "message": "Queued...", "logs": [], "guide": None,
            "output": None, "samples": [], "source": "manual"}
    tasks[tid] = task

    def update(**kw):
        if kw.get("check_cancelled"):
            return task.get("state") in ("cancelled", "error")
        # If cancelled, skip all updates and reject
        if task.get("state") == "cancelled":
            return True
        for k, v in kw.items():
            if k == "log":
                task["logs"].append(f"[{task['state']}] {v}")
            elif k in task:
                task[k] = v
        if kw.get("state") == "done":
            _add_history(task)

    def run():
        try:
            translator.run_translation(
                real, req.track_index, anime,
                config["model"], config["context_model"],
                config["batch_size"], use_ctx, update)
        except Exception as e:
            task["state"] = "error"
            task["message"] = str(e)
            task["logs"].append(f"[error] {e}")

    executor.submit(run)
    return {"task_id": tid, "anime_name": anime}


@app.post("/api/translate_batch")
async def start_batch(req: TranslateRequest):
    """翻译文件夹，支持选择性翻译"""
    if not translator: raise HTTPException(400, "请先配置 API")
    if req.videos:
        videos = [map_path(v) for v in req.videos if not os.path.exists(
            str(Path(map_path(v)).parent / f"{Path(map_path(v)).stem}.chi.srt"))]
    else:
        videos = [v for v in scan_videos(req.path) if not os.path.exists(
            str(Path(v).parent / f"{Path(v).stem}.chi.srt"))]
    if not videos:
        return {"message": "没有需要翻译的视频", "tasks": []}

    use_ctx = req.use_context if req.use_context is not None else config.get("use_context", False)
    
    # Pre-generate guide ONCE for the anime (avoids race condition)
    anime = req.anime_name or detect_anime_name(videos[0])
    if anime and anime not in Translator._guide_cache:
        try:
            guide = translator.generate_guide(anime, config.get("context_model", "deepseek-v4-pro"))
            logger.info(f"Batch: guide pre-generated for {anime}")
        except Exception as e:
            logger.warning(f"Batch: guide pre-generation failed: {e}")
    
    tids = []
    for v in videos:
        anime = req.anime_name or detect_anime_name(v)
        tid = str(uuid.uuid4())[:8]
        task = {"task_id": tid, "video": v, "anime_name": anime,
                "state": "queued", "progress": 0, "total": 0,
                "message": "排队中", "logs": [], "guide": None,
                "output": None, "samples": [], "source": "batch"}
        tasks[tid] = task
        tids.append(tid)

        # Get first English track
        tracks = translator.list_tracks(v)
        eng = next((t for t in tracks if t["language"] in ("eng", "en", "en-US", "en-GB")), None)
        track_idx = eng["index"] if eng else (tracks[0]["index"] if tracks else 0)

        def make_fn(t):
            def fn(**kw):
                for k, v in kw.items():
                    if k in t: t[k] = v
                if kw.get("state") == "done":
                    _add_history(t)
            return fn

        def run_trans(t, vpath, tidx, an, ctx):
            try:
                translator.run_translation(
                    vpath, tidx, an, config["model"], config["context_model"],
                    config["batch_size"], ctx, make_fn(t))
            except Exception as e:
                t["state"] = "error"
                t["message"] = str(e)
                t["logs"].append(f"[error] {e}")

        executor.submit(run_trans, task, v, track_idx, anime, use_ctx)
        time.sleep(0.5)  # stagger

    return {"message": f"已启动 {len(tids)} 个翻译任务", "tasks": tids}


# ── Watch folders ──

@app.get("/api/watch")
async def get_watch():
    return {"folders": watch_folders}


@app.post("/api/watch/add")
async def add_watch(path: str):
    global watch_thread
    if path not in watch_folders:
        watch_folders[path] = {"enabled": True, "last_scan": 0}
        save_watch()
    if not watch_thread or not watch_thread.is_alive():
        watch_thread = threading.Thread(target=watch_loop, daemon=True)
        watch_thread.start()
    return {"ok": True}


@app.post("/api/watch/remove")
async def remove_watch(path: str):
    watch_folders.pop(path, None)
    save_watch()
    return {"ok": True}


@app.post("/api/watch/toggle")
async def toggle_watch(path: str):
    if path in watch_folders:
        watch_folders[path]["enabled"] = not watch_folders[path].get("enabled", True)
        save_watch()
    return {"ok": True}


# ── Task / History ──

@app.get("/api/task/{task_id}")
async def get_task(task_id: str):
    task = tasks.get(task_id)
    if not task:
        raise HTTPException(404)
    return {k: task.get(k) for k in
            ["task_id", "video", "state", "progress", "total", "message",
             "output", "guide", "logs", "samples"]}


@app.get("/api/history")
async def get_history():
    return {"history": history}


@app.get("/api/tasks_active")
async def get_active():
    return {"tasks": [{k: t.get(k) for k in ["task_id", "video", "state", "progress", "total", "message", "output"]}
                      for t in tasks.values() if t.get("state") not in ("done", "error")]}


# ── Download ──


# ── Subtitle Editor ──

@app.get("/api/subtitle_read")
async def subtitle_read(path: str):
    path = map_path(path)
    """读取已翻译的字幕文件内容，返回所有条目的原文和译文"""
    if not os.path.exists(path):
        raise HTTPException(404, "字幕文件不存在")
    try:
        subs = pysrt.open(path, encoding="utf-8")
        import re
        entries = []
        for i, s in enumerate(subs):
            text = s.text.strip()
            # Skip HTML tags for display
            clean = re.sub(r'<[^>]+>', '', text)
            clean = re.sub(r'\{[^}]*\}', '', clean)
            entries.append({
                "index": i,
                "start": str(s.start),
                "end": str(s.end),
                "text": clean,
                "raw": text,
            })
        return {"path": path, "entries": entries, "total": len(entries)}
    except Exception as e:
        raise HTTPException(500, str(e))


class SubtitleSaveRequest(BaseModel):
    path: str
    entries: list[dict]


@app.post("/api/subtitle_save")
async def subtitle_save(data: SubtitleSaveRequest):
    """保存编辑后的字幕"""
    path = map_path(data.path)
    if not os.path.exists(path):
        raise HTTPException(404, "字幕文件不存在")
    try:
        subs = pysrt.open(path, encoding="utf-8")
        update_map = {e["index"]: e["text"] for e in data.entries if "index" in e and "text" in e}
        for i, s in enumerate(subs):
            if i in update_map:
                s.text = update_map[i]
        subs.save(path, encoding="utf-8")
        return {"ok": True, "saved": len(update_map)}
    except Exception as e:
        raise HTTPException(500, str(e))

# ── Guide Refinement ──

class ReviewRequest(BaseModel):
    path: str
    guide: str = ""

class GuideRefineRequest(BaseModel):
    anime_name: str
    corrections: list[dict]  # [{en: "...", old_zh: "...", new_zh: "..."}, ...]
    current_guide: str = ""  # 当前指南，作为微调基础


@app.post("/api/review")
async def review_translation(req: ReviewRequest):
    """用 Pro 模型审核翻译质量"""
    if not translator:
        raise HTTPException(400, "请先配置 API")
    real = map_path(req.path)
    # Handle paths that already end with .chi.srt or .eng.srt
    rp = str(real)
    if rp.endswith('.chi.srt') or rp.endswith('.eng.srt'):
        base = rp[:-8]
    else:
        base = rp.rsplit('.', 1)[0]
    chi_path = base + '.chi.srt'
    eng_path = base + '.eng.srt'
    
    if not os.path.exists(chi_path):
        raise HTTPException(404, "未找到翻译文件(.chi.srt)")
    if not os.path.exists(eng_path):
        raise HTTPException(404, "未找到原文文件(.eng.srt)")
    
    try:
        corrections = translator.review_translation(eng_path, chi_path, guide=req.guide)
        return {"ok": True, "corrections": corrections, "count": len(corrections)}
    except Exception as e:
        raise HTTPException(500, str(e))

@app.post("/api/guide_refine")
async def guide_refine(data: GuideRefineRequest):
    """用户修正字幕后，用V4-Pro分析修正模式，微调翻译指南"""
    if not translator:
        raise HTTPException(400, "请先配置 API")
    
    # Pure guide save (no corrections) - just update cache
    if not data.corrections or len(data.corrections) == 0:
        if data.current_guide.strip():
            Translator._guide_cache[data.anime_name] = data.current_guide
            translator._save_cache()
            return {"ok": True, "guide": data.current_guide, "saved": True}

    corrections_text = ""
    for i, c in enumerate(data.corrections[:20]):
        en = c.get('en', '')
        old_zh = c.get('old_zh', '')
        new_zh = c.get('new_zh', '')
        corrections_text += f"{i+1}. EN: {en}\n   AI译: {old_zh}\n   修正: {new_zh}\n\n"

    guide_context = ""
    if data.current_guide and data.current_guide.strip():
        guide_context = f"当前的翻译指南如下：\n{data.current_guide}\n\n"

    prompt = (
        f"你是翻译顾问。番剧《{data.anime_name}》的翻译指南需要根据用户修正来微调。\n\n"
        f"{guide_context}"
        "【重要】你必须原样保留作品背景、主要角色名字和角色设定描述，只允许修改和优化翻译规则、用词建议部分。绝对不要重写或改写角色名字和作品背景描述。\n\n"
        f"以下是用户对AI翻译的修正（EN原文 → AI翻译 → 用户修正）：\n\n"
        f"{corrections_text}"
        "请分析用户的修正模式（不要改动作品背景）：\n"
        "1. 用户偏好什么语气？（更口语/更书面/更简洁/更生动？）\n"
        "2. 用户在纠正哪些类型的翻译错误？\n"
        "3. 角色对话风格是否需要调整？\n\n"
        "在保留原指南中关于番剧背景、角色设定等内容的基础上，输出一份更新后的翻译指南（500字内），直接输出指南内容。"
    )

    try:
        r = translator.client.chat.completions.create(
            model=config.get("context_model", "deepseek-v4-pro"),
            messages=[{"role": "user", "content": prompt}],
            temperature=0.4, max_tokens=4000,
            extra_body={"enable_thinking": False},
        )
        refined = r.choices[0].message.content.strip()

        # 更新缓存
        Translator._guide_cache[data.anime_name] = refined
        translator._save_cache()

        return {"ok": True, "guide": refined, "chars": len(refined)}
    except Exception as e:
        raise HTTPException(500, str(e))


class WebhookTestRequest(BaseModel):
    url: str
    type: str = "wecom"

@app.post("/api/webhook_test")
async def webhook_test(data: WebhookTestRequest):
    """发送测试通知到 webhook"""
    import urllib.request
    msg = "字幕翻译器测试消息\n\n如果你看到这条消息，说明 Webhook 配置正确"
    try:
        if data.type == "dingtalk":
            payload = {"msgtype": "text", "text": {"content": msg}}
        elif data.type == "wecom":
            payload = {"msgtype": "text", "text": {"content": msg}}
        else:
            payload = {"content": msg}
        body = json.dumps(payload).encode()
        req = urllib.request.Request(data.url, data=body, headers={"Content-Type": "application/json"}, method="POST")
        resp = urllib.request.urlopen(req, timeout=10)
        if resp.status == 200:
            return {"ok": True}
        else:
            return {"ok": False, "error": f"HTTP {resp.status}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ── History Management ──

class HistoryDeleteRequest(BaseModel):
    index: int  # 删除指定索引的记录

@app.post("/api/history/delete")
async def history_delete(data: HistoryDeleteRequest):
    global history
    if 0 <= data.index < len(history):
        history.pop(data.index)
        save_history()
        return {"ok": True}
    return {"ok": False, "error": "Index out of range"}

@app.post("/api/history/clear")
async def history_clear():
    global history
    history = []
    save_history()
    return {"ok": True}

# ── Task Cancel ──

@app.post("/api/task/{task_id}/cancel")
async def task_cancel(task_id: str):
    task = tasks.get(task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    task["state"] = "cancelled"
    task["message"] = "已手动中断"
    task["logs"].append("[cancelled] 用户手动中断翻译")
    return {"ok": True}


# ── Standalone Guide Generation ──

class GuideRequest(BaseModel):
    path: str
    anime_name: Optional[str] = None
    force: bool = False

@app.post("/api/generate_guide")
async def generate_guide_standalone(req: GuideRequest):
    """独立生成翻译指南（不启动翻译）"""
    if not translator:
        raise HTTPException(400, "请先配置 API")
    real = map_path(req.path)
    anime = req.anime_name or detect_anime_name(real)
    if not anime:
        raise HTTPException(400, "无法识别番剧名，请手动输入")

    # 检查缓存（跳过空内容），force=True 时强制重新生成
    if not req.force and anime in Translator._guide_cache and len(Translator._guide_cache[anime].strip()) > 10:
        return {"ok": True, "guide": Translator._guide_cache[anime], "cached": True}

    try:
        guide = translator.generate_guide(anime, config.get("context_model", "deepseek-v4-pro"), force=req.force)
        return {"ok": True, "guide": guide, "cached": False}
    except Exception as e:
        raise HTTPException(500, str(e))


@app.get("/api/guide_cache_get")
async def guide_cache_get(anime: str = ""):
    """Check cache only - no API call"""
    if anime in Translator._guide_cache and len(Translator._guide_cache[anime].strip()) > 10:
        return {"ok": True, "guide": Translator._guide_cache[anime]}
    return {"ok": False, "guide": ""}

@app.get("/api/guide_cache_list")
async def guide_cache_list():
    """返回所有已缓存的番剧指南名称"""
    guides = list(Translator._guide_cache.keys())
    return {"guides": guides}


@app.get("/api/download")
async def download(path: str):
    if not os.path.exists(path):
        raise HTTPException(404)
    return FileResponse(path, filename=os.path.basename(path),
                        media_type="application/octet-stream")
