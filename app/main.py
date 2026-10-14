import asyncio, json, logging, os, re, time, uuid, threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional

logger = logging.getLogger("subtitle-translator")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s %(message)s")

import pysrt

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from .translator import Translator, detect_anime_name, map_path, scan_videos
from .translator import VIDEO_ROOT, DATA_DIR
from .translator import (detect_bilingual_srt, is_bilingual_text,
                         merge_to_bilingual, BILINGUAL_SUFFIXES)

app = FastAPI(title="字幕翻译器")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# 静态资源（style.css / app.js 等）
from fastapi.staticfiles import StaticFiles
_static_dir = os.path.join(os.path.dirname(__file__), "static")
if os.path.isdir(_static_dir):
    app.mount("/static", StaticFiles(directory=_static_dir), name="static")

executor = ThreadPoolExecutor(max_workers=5)
tasks: dict[str, dict] = {}
translator: Optional[Translator] = None
config: dict = {}
history: list[dict] = []
watch_folders: dict[str, dict] = {}  # path -> {interval, last_scan, enabled}
watch_thread: Optional[threading.Thread] = None
_watch_stop = threading.Event()
_watch_wake = threading.Event()       # 新增/启用订阅时置位，立即触发一轮扫描
_watch_save_lock = threading.RLock()  # 可重入：端点与 save_watch 嵌套获取

CONFIG_FILE = os.path.join(DATA_DIR, "config.json")
HISTORY_FILE = os.path.join(DATA_DIR, "history.json")
WATCH_FILE = os.path.join(DATA_DIR, "watch_folders.json")


def load_config():
    global config, translator
    try:
        with open(CONFIG_FILE, encoding="utf-8") as f: config = json.load(f)
    except Exception:
        config = {}
    # 补齐缺失键，避免后续 .get 之外的直接下标访问 KeyError
    defaults = {"api_key": "", "base_url": "",
                "model": "deepseek-v4-flash", "context_model": "deepseek-v4-pro",
                "batch_size": 12, "use_context": False, "webhook_url": "", "webhook_type": "wecom",
                "auto_cleanup": False, "bilingual": False, "bilingual_layout": "orig_first"}
    for k, v in defaults.items():
        config.setdefault(k, v)
    if config.get("api_key"):
        translator = Translator(config["api_key"], config.get("base_url") or None)


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
        json.dump(history[:200], f, ensure_ascii=False, indent=2)  # 新记录插在最前，保留最新 200 条


def load_watch():
    global watch_folders
    try:
        with open(WATCH_FILE, encoding="utf-8") as f: watch_folders = json.load(f)
    except Exception:
        watch_folders = {}


def save_watch():
    with _watch_save_lock:
        with open(WATCH_FILE, "w", encoding="utf-8") as f:
            json.dump(watch_folders, f, ensure_ascii=False, indent=2)


def watch_loop():
    """后台线程：定期扫描订阅文件夹。

    先扫描再等待：线程启动和每次唤醒（新增订阅）都会立即扫一轮，
    之后每 5 分钟一轮。单次循环异常只记录日志，绝不杀死线程。
    """
    _fail_info: dict[str, tuple] = {}  # video -> (失败次数, 上次失败时间)
    FAIL_MAX = 3          # 同一视频最多尝试 3 次，避免坏文件无限重试
    FAIL_COOLDOWN = 1800  # 失败后冷却 30 分钟再重试（瞬时 API 错误可自愈）

    def retry_due(v: str) -> bool:
        attempts, last_ts = _fail_info.get(v, (0, 0.0))
        return attempts < FAIL_MAX and time.time() - last_ts >= FAIL_COOLDOWN

    def scan_once():
        # 进行中的任务的视频集合：防止同一集被重复提交（翻译常超过一个扫描周期）
        active_videos = {t.get("video") for t in list(tasks.values())
                         if t.get("state") not in ("done", "error", "cancelled")}
        for fpath, info in list(watch_folders.items()):
            if not info.get("enabled", True):
                continue
            try:
                real = map_path(fpath)
            except ValueError as e:
                logger.warning(f"Watch: invalid path {fpath}: {e}")
                continue
            if not os.path.isdir(real):
                continue
            if not translator:
                logger.warning("Watch: translator not configured, skipping scan")
                continue
            videos = scan_videos(fpath)
            translated = set()
            for v in videos:
                base = Path(v).stem
                if os.path.exists(str(Path(v).parent / f"{base}.chi.srt")):
                    translated.add(v)
            new_videos = [v for v in videos
                          if v not in translated
                          and v not in active_videos
                          and retry_due(v)]
            if new_videos:
                logger.info(f"Watch: found {len(new_videos)} new videos in {fpath}")
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
                                    t.setdefault("logs", []).append(f"[{t.get('state', '?')}] {v}")
                                elif k in t:
                                    t[k] = v
                            if kw.get("state") == "done":
                                _add_history(t)
                                _fail_info.pop(t["video"], None)  # 成功后重置失败计数
                        return fn

                    def run_trans(t):
                        try:
                            translator.run_translation(
                                t["video"], 0, t["anime_name"],
                                config["model"], config["context_model"],
                                config["batch_size"], config.get("use_context", False),
                                make_fn(t), bilingual=config.get("bilingual", False))
                        except Exception as e:
                            if t.get("state") == "cancelled":
                                t.setdefault("logs", []).append(f"[cancelled] {e}")
                                _mark_task_done(t)
                                return
                            t["state"] = "error"
                            t["message"] = str(e)
                            t.setdefault("logs", []).append(f"[error] {e}")
                            _mark_task_done(t)
                            attempts = _fail_info.get(t["video"], (0, 0.0))[0]
                            _fail_info[t["video"]] = (attempts + 1, time.time())
                            logger.warning(f"Watch: translation failed for {Path(t['video']).name}: {e}")

                    executor.submit(run_trans, task)
            # 扫描期间订阅可能被删除，避免 KeyError
            if fpath in watch_folders:
                watch_folders[fpath]["last_scan"] = time.time()
                save_watch()

    while not _watch_stop.is_set():
        try:
            scan_once()
        except Exception:
            logger.exception("Watch: scan cycle error")  # 任何异常都不杀死扫描线程
        # 等待唤醒（新增/启用订阅立即扫描）或超时进入下一轮
        _watch_wake.wait(300)
        _watch_wake.clear()


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
    _mark_task_done(task)
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
    del history[200:]  # 内存中也只保留最新 200 条
    save_history()
    # 发送通知（在单独线程中）
    import threading
    threading.Thread(target=_send_webhook, args=(task,), daemon=True).start()


def _cleanup_tasks_loop():
    """定期清理已完成超过 1 小时的任务（无任务时跳过文件操作）"""
    while True:
        time.sleep(600)  # 每 10 分钟清理一次
        now = time.time()
        expired = [tid for tid, t in list(tasks.items())
                   if t.get("state") in ("done", "error", "cancelled")
                   and now - t.get("_done_time", now) > 3600]
        for tid in expired:
            tasks.pop(tid, None)
        if expired:
            logger.info(f"Cleaned up {len(expired)} expired tasks")


def _mark_task_done(task: dict):
    """任务完成时记录时间戳"""
    task["_done_time"] = time.time()


load_config()
load_history()
load_watch()

# Start watch thread
if watch_folders:
    try:
        watch_thread = threading.Thread(target=watch_loop, daemon=True, name="watch-loop")
        watch_thread.start()
    except Exception as e:
        logger.warning(f"Watch thread start failed: {e}")

# Start cleanup thread
cleanup_thread = threading.Thread(target=_cleanup_tasks_loop, daemon=True)
cleanup_thread.start()


@app.get("/favicon.ico")
async def favicon():
    ico = os.path.join(os.path.dirname(__file__), "static", "icon.ico")
    if os.path.exists(ico):
        return FileResponse(ico, media_type="image/x-icon")
    return ""

@app.get("/icon_256.png")
async def icon_256_file():
    png = os.path.join(os.path.dirname(__file__), "static", "icon_256.png")
    if os.path.exists(png):
        return FileResponse(png, media_type="image/png")
    return ""
@app.get("/icon_36.png")
async def icon_36():
    ico_dir = os.path.join(os.path.dirname(__file__), "static")
    png = os.path.join(ico_dir, "icon_36.png")
    if os.path.exists(png):
        return FileResponse(png, media_type="image/png")
    return ""

@app.get("/")
async def index():
    with open(os.path.join(os.path.dirname(__file__), "static", "index.html"), encoding="utf-8") as f:
        return HTMLResponse(f.read())


# ── Bilingual SRT ──

def _sibling_srt(path: str) -> dict:
    """基于任意同基名字幕路径，返回 {base, chi, eng, bil} 的实际磁盘路径"""
    real = str(map_path(path))
    if real.endswith(".chi.srt") or real.endswith(".eng.srt") \
            or real.endswith(".bilingual.srt") or real.endswith(".zh.srt") \
            or real.endswith(".en.srt"):
        base = real.rsplit(".", 2)[0] if real.count(".") >= 2 else real.rsplit(".", 1)[0]
    else:
        base = real.rsplit(".", 1)[0]
    return {
        "base": base,
        "chi": base + ".chi.srt",
        "eng": base + ".eng.srt",
        "bil": base + ".bilingual.srt",
    }


@app.get("/api/bilingual_srt")
async def get_bilingual_srt(path: str = "", layout: str = "orig_first",
                            translate: bool = False):
    """生成/返回双语字幕。

    优先级：已存在的 .bilingual.srt > 原字幕+译文字幕按时间轴合并。
    layout: orig_first=原文在上(英上中下) / trans_first=译文在上(中上英下)。
    translate=True 时若无 .chi.srt 会启动翻译任务（返回 task_id）。
    """
    real = map_path(path)
    rp = str(real)
    if rp.endswith('.chi.srt') or rp.endswith('.eng.srt'):
        base = rp[:-8]
    else:
        base = rp.rsplit('.', 1)[0]
    chi_path = base + '.chi.srt'
    eng_path = base + '.eng.srt'
    bil_path = base + '.bilingual.srt'

    # 已有双语文件且布局一致，直接返回
    if os.path.exists(bil_path):
        try:
            with open(bil_path, encoding="utf-8") as f:
                return PlainTextResponse(f.read(), media_type="text/plain; charset=utf-8")
        except Exception:
            pass  # 读取失败则重新生成

    # 有原+译：按时间轴合并
    if os.path.exists(chi_path) and os.path.exists(eng_path):
        orig_first = layout != "trans_first"
        matched = merge_to_bilingual(eng_path, chi_path, bil_path, orig_first=orig_first)
        with open(bil_path, encoding="utf-8") as f:
            content = f.read()
        logger.info(f"Bilingual merged: {matched} pairs -> {bil_path}")
        return PlainTextResponse(content, media_type="text/plain; charset=utf-8")

    # 只译文无原文：译文本身即单语，无法组双语
    if os.path.exists(chi_path) and not os.path.exists(eng_path):
        # 译文可能本身已是双语（用户自带的中文字幕轨含双语）
        if detect_bilingual_srt(chi_path):
            with open(chi_path, encoding="utf-8") as f:
                return PlainTextResponse(f.read(), media_type="text/plain; charset=utf-8")
        raise HTTPException(404, "缺少原文字幕（.eng.srt），无法生成双语")

    if translate:
        # 找到对应的视频文件，启动翻译任务
        video = None
        for ext in (".mkv", ".mp4", ".avi", ".mov", ".ts"):
            cand = base + ext
            if os.path.exists(cand):
                video = cand
                break
        if not video:
            raise HTTPException(404, "未找到对应视频文件，无法启动翻译")
        raise HTTPException(400, f"NEED_TRANSLATE:{video}")

    raise HTTPException(404, "字幕文件不存在")


@app.post("/api/bilingual_merge")
async def bilingual_merge(path: str = "", layout: str = "orig_first"):
    """把已有的原字幕与翻译字幕合并为双语 .bilingual.srt 文件（保存到磁盘）"""
    real = map_path(path)
    rp = str(real)
    if rp.endswith('.chi.srt') or rp.endswith('.eng.srt'):
        base = rp[:-8]
    else:
        base = rp.rsplit('.', 1)[0]
    chi_path = base + '.chi.srt'
    eng_path = base + '.eng.srt'
    bil_path = base + '.bilingual.srt'
    if not (os.path.exists(chi_path) and os.path.exists(eng_path)):
        raise HTTPException(404, "需要同时存在 .eng.srt（原文）和 .chi.srt（译文）")
    orig_first = layout != "trans_first"
    matched = merge_to_bilingual(eng_path, chi_path, bil_path, orig_first=orig_first)
    return {"ok": True, "output": bil_path, "matched": matched,
            "layout": "orig_first" if orig_first else "trans_first"}


@app.get("/api/bilingual_detect")
def bilingual_detect(path: str):
    """检测字幕文件是否为双语（逐条统计，前端展示用）"""
    real = map_path(path)
    if not os.path.exists(real):
        raise HTTPException(404, "文件不存在")
    try:
        subs = pysrt.open(real, encoding="utf-8")
    except Exception:
        subs = None
        for enc in ("utf-8-sig", "gbk", "latin-1", "shift-jis"):
            try:
                subs = pysrt.open(real, encoding=enc)
                break
            except Exception:
                continue
    if subs is None:
        raise HTTPException(500, "无法解码字幕文件")
    total = len(subs)
    bi = sum(1 for s in subs if is_bilingual_text(s.text))
    return {"path": path, "total": total, "bilingual_lines": bi,
            "is_bilingual": bi >= max(2, int(total * 0.6))}


class BilingualTranslateRequest(BaseModel):
    path: str            # 源字幕或视频路径
    bilingual: bool = True
    layout: str = "orig_first"  # orig_first / trans_first


@app.post("/api/bilingual_translate")
async def bilingual_translate(req: BilingualTranslateRequest):
    """直接翻译出双语字幕：对已有单语字幕启动翻译，完成后自动组双语。

    - path 可以是视频（走正常提取流程）或 .eng.srt / .chi.srt 字幕文件
    - 若源字幕已是双语：直接拆分保存，不调用 API
    - 翻译完成后自动生成 .bilingual.srt
    """
    if not translator:
        raise HTTPException(400, "请先配置 API")
    real = map_path(req.path)
    if not os.path.exists(real):
        raise HTTPException(404, "文件不存在")

    # 字幕文件 → 找对应视频（提取流程需要视频路径；找不到则改用字幕直翻）
    target_video = real
    if str(real).endswith((".srt", ".ass", ".ssa")):
        base = str(real)
        for suf in (".chi.srt", ".eng.srt", ".bilingual.srt", ".srt", ".ass", ".ssa"):
            if base.endswith(suf):
                base = base[:-len(suf)]
                break
        target_video = None
        for ext in (".mkv", ".mp4", ".avi", ".mov", ".ts"):
            if os.path.exists(base + ext):
                target_video = base + ext
                break
        if target_video is None:
            # 无视频：合成同名 .mkv 路径，让 run_translation 的外挂字幕
            # fallback（find_external_srt）正确以 base 为基名找到源字幕，
            # 避免 srt 文件名被当作视频名产生 "xx.eng.chi.srt" 双重后缀
            target_video = base + ".mkv"

    anime = detect_anime_name(target_video)
    tid = str(uuid.uuid4())[:8]
    task = {"task_id": tid, "video": target_video, "anime_name": anime,
            "state": "pending", "progress": 0, "total": 0,
            "message": "Queued...", "logs": [], "guide": None,
            "output": None, "samples": [], "source": "manual"}
    tasks[tid] = task
    want_layout_orig_first = req.layout != "trans_first"

    def update(**kw):
        if kw.get("check_cancelled"):
            return task.get("state") in ("cancelled", "error")
        if task.get("state") == "cancelled":
            return True
        for k, v in kw.items():
            if k == "log":
                task["logs"].append(f"[{task['state']}] {v}")
            elif k in task:
                task[k] = v
        if kw.get("state") == "done":
            _add_history(task)
            if task.get("output"):
                try:
                    real_out = map_path(task["output"])
                    rp = str(real_out)
                    base = rp[:-8] if rp.endswith('.chi.srt') else rp.rsplit('.', 1)[0]
                    eng_path, chi_path = base + '.eng.srt', base + '.chi.srt'
                    bil_path = base + '.bilingual.srt'
                    if os.path.exists(eng_path) and os.path.exists(chi_path):
                        matched = merge_to_bilingual(eng_path, chi_path, bil_path,
                                                     orig_first=want_layout_orig_first)
                        task["logs"].append(f"[ok] Bilingual SRT generated ({matched} pairs)")
                except Exception as e:
                    task["logs"].append(f"[error] Bilingual failed: {e}")

    def run():
        try:
            translator.run_translation(
                target_video, 0, anime,
                config["model"], config["context_model"],
                config["batch_size"], config.get("use_context", False),
                update, bilingual=True)
        except Exception as e:
            if task.get("state") == "cancelled":
                task["logs"].append(f"[cancelled] {e}")
                return
            task["state"] = "error"
            task["message"] = str(e)
            task["logs"].append(f"[error] {e}")
            _mark_task_done(task)

    executor.submit(run)
    return {"task_id": tid, "anime_name": anime}


# ── Global Search & Replace ──
class SearchReplaceRequest(BaseModel):
    srt_path: str
    search: str
    replace: str
    encoding: str = 'utf-8'

@app.post("/api/search_replace")
async def global_search_replace(req: SearchReplaceRequest):
    """全局搜索替换字幕内容"""
    import pysrt
    real = map_path(req.srt_path)
    if not os.path.exists(real):
        raise HTTPException(404, "文件不存在")
    
    subs = pysrt.open(real, encoding='utf-8')
    count = 0
    for s in subs:
        if req.search in s.text:
            s.text = s.text.replace(req.search, req.replace)
            count += 1
    subs.save(real, encoding='utf-8')
    return {"ok": True, "count": count}


# ── Translation Stats ──
@app.get("/api/stats")
async def get_stats():
    """翻译统计"""
    history_data = []
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE) as f:
                history_data = json.load(f)
        except Exception:
            pass
    
    total_tasks = len(history_data)
    total_lines = 0
    animes = {}
    
    for h in history_data:
        anime = h.get('anime') or h.get('anime_name') or h.get('video', '').split('/')[-1]
        animes[anime] = animes.get(anime, 0) + 1
        total_lines += h.get('total', 0)
    
    # Cache stats
    cache_count = len(Translator._guide_cache)
    cache_size = sum(len(v) for v in Translator._guide_cache.values())
    
    return {
        "total_tasks": total_tasks,
        "total_lines": total_lines,
        "animes": animes,
        "guide_cache_count": cache_count,
        "guide_cache_size": cache_size
    }


# ── Auto cleanup setting ──
@app.post("/api/cleanup_eng_srt")
async def cleanup_eng_srt(path: str = ""):
    """翻译后自动清理 .eng.srt 文件"""
    real = map_path(path)
    rp = str(real)
    if rp.endswith('.chi.srt'):
        base = rp[:-8]
    else:
        base = rp.rsplit('.', 1)[0]
    eng_path = base + '.eng.srt'
    if os.path.exists(eng_path):
        os.remove(eng_path)
        return {"ok": True, "deleted": eng_path}
    return {"ok": False, "message": "文件不存在"}


# ── Config ──

class ConfigUpdate(BaseModel):
    # 全部可选：前端开关只 POST 单个字段，未提供的字段不得覆盖已有配置
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    model: Optional[str] = None
    context_model: Optional[str] = None
    batch_size: Optional[int] = None
    use_context: Optional[bool] = None
    auto_cleanup: Optional[bool] = None
    bilingual: Optional[bool] = None
    bilingual_layout: Optional[str] = None
    webhook_url: Optional[str] = None
    webhook_type: Optional[str] = None


@app.get("/api/config")
async def get_config():
    return {k: v for k, v in config.items() if k != "api_key"} | {
        "api_key": "***" if config.get("api_key") else ""}


@app.post("/api/config")
async def update_config(data: ConfigUpdate):
    global translator
    updates = data.model_dump(exclude_unset=True)
    if updates.get("api_key") not in (None, "", "***"):
        config["api_key"] = updates["api_key"]
    if "base_url" in updates:
        config["base_url"] = updates["base_url"]
    if updates.get("model"):
        config["model"] = updates["model"]
    if updates.get("context_model"):
        config["context_model"] = updates["context_model"]
    if updates.get("batch_size") and updates["batch_size"] >= 5:
        config["batch_size"] = updates["batch_size"]
    if "use_context" in updates:
        config["use_context"] = updates["use_context"]
    if "auto_cleanup" in updates:
        config["auto_cleanup"] = updates["auto_cleanup"]
    if "bilingual" in updates:
        config["bilingual"] = updates["bilingual"]
    if updates.get("bilingual_layout") in ("orig_first", "trans_first"):
        config["bilingual_layout"] = updates["bilingual_layout"]
    if "webhook_url" in updates:
        config["webhook_url"] = updates["webhook_url"]
    if "webhook_type" in updates:
        config["webhook_type"] = updates["webhook_type"]
    save_config()
    if config.get("api_key"):
        translator = Translator(config["api_key"], config.get("base_url") or None)
    return {"ok": True}


# ── Browse ──

@app.get("/api/browse")
def browse(path: str = "/", sort: str = "name"):
    real_path = map_path(path)
    if not os.path.exists(real_path):
        return {"error": "Path not found"}
    entries = []
    for name in os.listdir(real_path):
        full = os.path.join(real_path, name)
        try:
            is_dir = os.path.isdir(full)
            ext = os.path.splitext(name)[1].lower() if not is_dir else ""
            mtime = os.path.getmtime(full)
        except OSError:
            continue  # 扫描期间被删除/无权限的条目直接跳过
        is_bilingual = False
        if ext in (".srt", ".ass"):
            # 双语字幕标记：文件名约定 或 内容检测（内容检测有 IO 开销，仅对 .srt 做）
            if name.lower().endswith(BILINGUAL_SUFFIXES):
                is_bilingual = True
            elif ext == ".srt":
                try:
                    is_bilingual = detect_bilingual_srt(full)
                except Exception:
                    is_bilingual = False
        entries.append({
            "name": name, "path": os.path.join(path, name).replace("//", "/"),
            "is_dir": is_dir,
            "is_video": ext in (".mkv", ".mp4", ".avi", ".mov", ".ts"),
            "is_srt": ext in (".srt", ".ass"),
            "is_bilingual": is_bilingual,
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
def get_tracks(path: str):
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
def scan_folder(path: str = "/"):
    videos = scan_videos(path)
    result = []
    for v in videos:
        name = Path(v).name
        stem = Path(v).stem
        vparent = Path(v).parent
        has_chi = os.path.exists(str(vparent / f"{stem}.chi.srt"))
        has_eng = os.path.exists(str(vparent / f"{stem}.eng.srt"))
        has_bil = os.path.exists(str(vparent / f"{stem}.bilingual.srt"))
        result.append({"path": v, "name": name, "translated": has_chi,
                       "has_eng": has_eng, "has_bilingual": has_bil,
                       "anime": detect_anime_name(v)})
    return {"videos": result, "total": len(result),
            "translated": sum(1 for r in result if r["translated"]),
            "bilingual": sum(1 for r in result if r["has_bilingual"])}


# ── Translate ──

class TranslateRequest(BaseModel):
    path: str
    track_index: int = 0
    anime_name: Optional[str] = None
    use_context: Optional[bool] = None
    videos: Optional[list] = None  # 批量翻译时指定具体视频路径
    bilingual: Optional[bool] = None  # None=跟随全局配置


@app.post("/api/translate")
async def start_translate(req: TranslateRequest):
    if not translator:
        raise HTTPException(400, "请先配置 API")
    real = map_path(req.path)
    if not os.path.exists(real):
        raise HTTPException(404, "文件不存在")
    anime = req.anime_name or detect_anime_name(real)
    use_ctx = req.use_context if req.use_context is not None else config.get("use_context", False)
    want_bilingual = req.bilingual if req.bilingual is not None else config.get("bilingual", False)

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
            # 翻译时未开启双语，但用户请求了双语（单次覆盖）：完成后补生成
            if want_bilingual and task.get("output"):
                try:
                    real_out = map_path(task["output"])
                    rp = str(real_out)
                    base = rp[:-8] if rp.endswith('.chi.srt') else rp.rsplit('.', 1)[0]
                    eng_path = base + '.eng.srt'
                    chi_path = base + '.chi.srt'
                    bil_path = base + '.bilingual.srt'
                    if os.path.exists(eng_path) and os.path.exists(chi_path):
                        matched = merge_to_bilingual(
                            eng_path, chi_path, bil_path,
                            orig_first=config.get("bilingual_layout", "orig_first") == "orig_first")
                        task["logs"].append(f"[ok] Bilingual SRT generated ({matched} pairs)")
                except Exception as e:
                    task["logs"].append(f"[error] Bilingual failed: {e}")
            # Auto cleanup .eng.srt
            if config.get("auto_cleanup", False) and task.get("output"):
                try:
                    real = map_path(task["output"])
                    rp = str(real)
                    base = rp[:-8] if rp.endswith('.chi.srt') else rp.rsplit('.', 1)[0]
                    eng_path = base + '.eng.srt'
                    if os.path.exists(eng_path):
                        os.remove(eng_path)
                        task["logs"].append("[ok] Cleaned up .eng.srt")
                except Exception:
                    pass

    def run():
        try:
            translator.run_translation(
                real, req.track_index, anime,
                config["model"], config["context_model"],
                config["batch_size"], use_ctx, update,
                bilingual=want_bilingual)
        except Exception as e:
            # 用户主动取消：保持 cancelled 状态，不覆盖为 error
            if task.get("state") == "cancelled":
                task["logs"].append(f"[cancelled] {e}")
                return
            task["state"] = "error"
            task["message"] = str(e)
            task["logs"].append(f"[error] {e}")
            _mark_task_done(task)

    executor.submit(run)
    return {"task_id": tid, "anime_name": anime}


@app.post("/api/translate_batch")
def start_batch(req: TranslateRequest):
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
        # If no embedded subtitles, use track_index=0 which triggers auto-detection + external SRT fallback in run_translation

        def make_fn(t):
            def fn(**kw):
                if kw.get("check_cancelled"):
                    return t.get("state") in ("cancelled", "error")
                if t.get("state") == "cancelled":
                    return True
                for k, v in kw.items():
                    if k == "log":
                        t.setdefault("logs", []).append(f"[{t.get('state','?')}] {v}")
                    elif k in t:
                        t[k] = v
                if kw.get("state") == "done":
                    _add_history(t)
            return fn

        def run_trans(t, vpath, tidx, an, ctx):
            try:
                translator.run_translation(
                    vpath, tidx, an, config["model"], config["context_model"],
                    config["batch_size"], ctx, make_fn(t),
                    bilingual=config.get("bilingual", False))
            except Exception as e:
                if t.get("state") == "cancelled":
                    t.setdefault("logs", []).append(f"[cancelled] {e}")
                    _mark_task_done(t)
                    return
                t["state"] = "error"
                t["message"] = str(e)
                t.setdefault("logs", []).append(f"[error] {e}")
                _mark_task_done(t)

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
    with _watch_save_lock:
        if path not in watch_folders:
            watch_folders[path] = {"enabled": True, "last_scan": 0}
        save_watch()
    # 确保扫描线程存活（启动时无订阅则不会创建）
    if not watch_thread or not watch_thread.is_alive():
        watch_thread = threading.Thread(target=watch_loop, daemon=True, name="watch-loop")
        watch_thread.start()
    else:
        _watch_wake.set()  # 立即扫描一轮，无需等 5 分钟
    return {"ok": True}


@app.post("/api/watch/remove")
async def remove_watch(path: str):
    with _watch_save_lock:
        watch_folders.pop(path, None)
        save_watch()
    return {"ok": True}


@app.post("/api/watch/toggle")
async def toggle_watch(path: str):
    with _watch_save_lock:
        if path in watch_folders:
            watch_folders[path]["enabled"] = not watch_folders[path].get("enabled", True)
            save_watch()
    if watch_folders.get(path, {}).get("enabled", False):
        _watch_wake.set()  # 重新启用也立即扫描
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
    return {"tasks": [{k: t.get(k) for k in ["task_id", "video", "state", "progress", "total", "message", "output", "source"]}
                      for t in tasks.values() if t.get("state") not in ("done", "error", "cancelled")]}


# ── Download ──


# ── Subtitle Editor ──

@app.get("/api/subtitle_read")
def subtitle_read(path: str):
    """读取已翻译的字幕文件内容，返回所有条目的原文和译文"""
    path = map_path(path)
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
def subtitle_save(data: SubtitleSaveRequest):
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

class GuideRefineRequest(BaseModel):
    anime_name: str
    corrections: list[dict]  # [{en: "...", old_zh: "...", new_zh: "..."}, ...]
    current_guide: str = ""  # 当前指南，作为微调基础



@app.post("/api/guide_refine")
async def guide_refine(data: GuideRefineRequest):
    """用户修正字幕后，用V4-Pro分析修正模式，微调翻译指南"""
    if not translator:
        raise HTTPException(400, "请先配置 API")
    
    # Pure guide save (no corrections) - just update cache
    if not data.corrections or len(data.corrections) == 0:
        if data.current_guide.strip():
            with Translator._cache_lock:
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
        with Translator._cache_lock:
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
    if task.get("state") in ("done", "error", "cancelled"):
        return {"ok": True, "message": "任务已结束"}
    task["state"] = "cancelled"
    task["message"] = "已手动中断"
    task.setdefault("logs", []).append("[cancelled] 用户手动中断翻译")
    _mark_task_done(task)
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
    from .translator import map_path, VIDEO_ROOT
    real = map_path(path)
    real = os.path.realpath(real)
    root = os.path.realpath(VIDEO_ROOT)
    if real != root and not real.startswith(root + os.sep):
        raise HTTPException(403, "访问被拒绝")
    if not os.path.exists(real):
        raise HTTPException(404, "文件不存在")
    return FileResponse(real, filename=os.path.basename(real),
                        media_type="application/octet-stream")


# ── New Frontend API Endpoints (v1.3) ──

@app.get("/api/logs")
async def api_logs(path: str = ""):
    """返回翻译日志 - 新前端 dashboard 使用"""
    logs = []
    # 如果指定了 path，返回对应视频的日志
    if path:
        for t in tasks.values():
            if t.get("video") and path in str(t.get("video", "")):
                logs.extend(t.get("logs", []))
    else:
        # 返回所有近期活跃任务的日志
        for t in list(tasks.values())[-5:]:
            logs.extend(t.get("logs", []))
    return {"logs": logs[-200:]}


@app.get("/api/subscriptions")
async def api_subscriptions():
    """返回订阅列表 - 新前端 dashboard 使用"""
    subs = []
    for fpath, info in watch_folders.items():
        subs.append({
            "path": fpath,
            "enabled": info.get("enabled", True),
            "last_scan": info.get("last_scan", 0),
        })
    return {"subscriptions": subs}


@app.get("/api/guide")
async def api_guide(path: str = "", force: bool = False):
    """生成/获取翻译指南（GET 版本）- 新前端使用"""
    if not translator:
        raise HTTPException(400, "请先配置 API")
    if not path:
        raise HTTPException(400, "path 参数必填")
    real = map_path(path)
    anime = detect_anime_name(real)
    if not anime:
        raise HTTPException(400, "无法识别番剧名，请手动输入")
    if not force and anime in Translator._guide_cache and len(Translator._guide_cache[anime].strip()) > 10:
        return {"ok": True, "guide": Translator._guide_cache[anime], "cached": True}
    try:
        guide = translator.generate_guide(anime, config.get("context_model", "deepseek-v4-pro"), force=force)
        return {"ok": True, "guide": guide, "cached": False}
    except Exception as e:
        raise HTTPException(500, str(e))


@app.get("/api/video_info")
async def api_video_info(path: str = ""):
    """返回视频信息（含字幕流）- 新前端使用"""
    if not path:
        raise HTTPException(400, "path 参数必填")
    real = map_path(path)
    if not os.path.exists(real):
        raise HTTPException(404, "文件不存在")
    result = {
        "path": real,
        "filename": Path(real).name,
        "exists": True,
        "subtitle_tracks": [],
        "anime_name": detect_anime_name(real),
    }
    if translator:
        tracks = translator.list_tracks(real)
        result["subtitle_tracks"] = [
            {"index": t["index"], "language": t["language"], "title": t.get("title", "")}
            for t in tracks
        ]
    # 检查是否已翻译
    chi = str(Path(real).parent / f"{Path(real).stem}.chi.srt")
    bil = str(Path(real).parent / f"{Path(real).stem}.bilingual.srt")
    eng = str(Path(real).parent / f"{Path(real).stem}.eng.srt")
    result["has_chinese"] = os.path.exists(chi)
    result["has_bilingual"] = os.path.exists(bil)
    result["has_english"] = os.path.exists(eng)
    # 已有中文单语字幕本身是双语的情况（用户自带）
    if not result["has_bilingual"] and result["has_chinese"]:
        try:
            result["chi_is_bilingual"] = detect_bilingual_srt(chi)
        except Exception:
            result["chi_is_bilingual"] = False
    return result


@app.get("/api/progress")
async def api_progress():
    """返回当前活跃翻译任务进度 - 新前端 dashboard 轮询使用"""
    active = []
    for t in tasks.values():
        if t.get("state") not in ("done", "error", "cancelled"):
            active.append({
                "task_id": t.get("task_id", ""),
                "video": str(Path(t.get("video", "")).name) if t.get("video") else "",
                "state": t.get("state", "?"),
                "progress": t.get("progress", 0),
                "total": t.get("total", 0),
                "message": t.get("message", ""),
            })
    return {"active": active, "count": len(active)}


class BatchRequest(BaseModel):
    paths: list = []
    target_lang: str = "zh"
    overwrite: bool = False

@app.post("/api/batch")
async def api_batch(req: BatchRequest):
    """批量翻译（POST 版本）- 新前端使用"""
    if not translator:
        raise HTTPException(400, "请先配置 API")
    if not req.paths:
        return {"message": "没有需要翻译的文件", "tasks": []}

    submitted = []
    for p in req.paths:
        try:
            real = map_path(p)
        except ValueError:
            continue
        if not os.path.exists(real):
            continue
        # 检查是否已有翻译（除非 overwrite）
        chi = str(Path(real).parent / f"{Path(real).stem}.chi.srt")
        if os.path.exists(chi) and not req.overwrite:
            continue
        anime = detect_anime_name(real)
        tid = str(uuid.uuid4())[:8]
        task = {"task_id": tid, "video": real, "anime_name": anime,
                "state": "queued", "progress": 0, "total": 0,
                "message": "排队中", "logs": [], "guide": None,
                "output": None, "samples": [], "source": "batch"}
        tasks[tid] = task
        submitted.append(tid)

        tracks = translator.list_tracks(real)
        eng = next((t for t in tracks if t["language"] in ("eng", "en", "en-US", "en-GB")), None)
        track_idx = eng["index"] if eng else (tracks[0]["index"] if tracks else 0)

        def make_fn(t):
            def fn(**kw):
                if kw.get("check_cancelled"):
                    return t.get("state") in ("cancelled", "error")
                if t.get("state") == "cancelled":
                    return True
                for k, v in kw.items():
                    if k == "log":
                        t.setdefault("logs", []).append(f"[{t.get('state','?')}] {v}")
                    elif k in t:
                        t[k] = v
                if kw.get("state") == "done":
                    _add_history(t)
            return fn

        def run_trans(t, vpath, tidx, an):
            try:
                translator.run_translation(
                    vpath, tidx, an, config["model"], config["context_model"],
                    config["batch_size"], config.get("use_context", False), make_fn(t),
                    bilingual=config.get("bilingual", False))
            except Exception as e:
                if t.get("state") == "cancelled":
                    t.setdefault("logs", []).append(f"[cancelled] {e}")
                    _mark_task_done(t)
                    return
                t["state"] = "error"
                t["message"] = str(e)
                t.setdefault("logs", []).append(f"[error] {e}")
                _mark_task_done(t)

        executor.submit(run_trans, task, real, track_idx, anime)

    return {"message": f"已提交 {len(submitted)} 个翻译任务", "tasks": submitted}


class UnsubscribeRequest(BaseModel):
    path: str = ""

@app.post("/api/unsubscribe")
async def api_unsubscribe(req: UnsubscribeRequest):
    """取消订阅（POST 版本）- 新前端使用"""
    if req.path in watch_folders:
        watch_folders.pop(req.path, None)
        save_watch()
        return {"ok": True}
    return {"ok": False, "error": "未找到该订阅"}
