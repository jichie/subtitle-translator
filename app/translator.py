import json, os, re, subprocess, time, threading
from pathlib import Path
from typing import Optional

import pysrt
from openai import OpenAI

VIDEO_ROOT = os.environ.get("VIDEO_MOUNT", "/videos")
DATA_DIR = os.environ.get("DATA_DIR", "/data")

# ffmpeg/ffprobe 查找路径：优先 FFMPEG_BIN 环境变量，否则靠 PATH
def _ffmpeg_bin(name: str) -> str:
    """返回 ffmpeg 或 ffprobe 的完整路径（Windows 自动加 .exe）"""
    if os.name == 'nt' and not name.endswith('.exe'):
        name += '.exe'
    bindir = os.environ.get("FFMPEG_BIN")
    if bindir:
        full = os.path.join(bindir, name)
        if os.path.exists(full):
            return full
    return name


class Translator:

    _guide_cache: dict = {}

    def __init__(self, api_key: str, base_url: str):
        self.client = OpenAI(api_key=api_key, base_url=base_url, timeout=120.0)
        self._load_cache()

    def _load_cache(self):
        try:
            cache_file = os.path.join(DATA_DIR, "guide_cache.json")
            if os.path.exists(cache_file):
                with open(cache_file, 'r', encoding='utf-8') as f:
                    Translator._guide_cache = json.load(f)
        except Exception:
            pass

    def _save_cache(self):
        try:
            os.makedirs(DATA_DIR, exist_ok=True)
            with open(os.path.join(DATA_DIR, "guide_cache.json"), 'w', encoding='utf-8') as f:
                json.dump(Translator._guide_cache, f, ensure_ascii=False)
        except Exception:
            pass

    def list_tracks(self, video_path: str):
        ffprobe = _ffmpeg_bin("ffprobe")
        cmd = [ffprobe, "-v", "quiet", "-print_format", "json",
               "-show_streams", "-select_streams", "s", video_path]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            return []
        return [{"index": s["index"], "language": s.get("tags", {}).get("language", "und"),
                 "title": s.get("tags", {}).get("title", "")}
                for s in json.loads(r.stdout).get("streams", [])]

    def extract_subtitle(self, video_path: str, stream_index: int, output_path: str) -> bool:
        ffmpeg = _ffmpeg_bin("ffmpeg")
        cmd = [ffmpeg, "-y", "-v", "quiet", "-i", video_path,
               "-map", f"0:{stream_index}", "-c:s", "srt", output_path]
        return subprocess.run(cmd, capture_output=True, text=True).returncode == 0

    def _strip_html(self, text: str) -> str:
        text = re.sub(r'<[^>]+>', '', text)
        text = re.sub(r'\{[^}]*\}', '', text)
        return re.sub(r'\s+', ' ', text).strip()

    def _clean_punct(self, text: str) -> str:
        """去掉句号，保留？！"""
        return re.sub(r'[。.]', '', text)

    def generate_guide(self, anime_name: str, context_model: str, log_fn=None, force=False) -> str:
        """生成翻译指南（同一番剧只生成一次，缓存复用）"""
        # 查缓存
        if not force and anime_name in Translator._guide_cache:
            cached = Translator._guide_cache[anime_name]
            if cached and len(cached) > 10:
                if log_fn: log_fn(f"Cached guide: {len(cached)} chars")
                return cached
            else:
                if log_fn: log_fn(f"Skipping empty cache ({len(cached)} chars), regenerating...")

        prompt = (
            f"为番剧《{anime_name}》生成翻译风格指南。直接输出指南内容。\n"
            "必须包含：作品介绍（故事背景）、主要角色名字及语气区别、关键名词译法、翻译禁忌。\n"
            "注意：角色名字不能省略，必须写出具体人名。不超过600字。\n"
            "输出：1.整体风格基调 2.主要角色语气区别 3.关键名词译法 "
            "4.特殊表达处理 5.翻译禁忌。精炼不超过500字。"
        )
        try:
            if log_fn: log_fn(f"Calling {context_model}...")
            r = self.client.chat.completions.create(
                model=context_model, messages=[{"role": "user", "content": prompt}],
                temperature=0.4, max_tokens=4000,
                extra_body={"enable_thinking": False})
            guide = r.choices[0].message.content.strip()
            Translator._guide_cache[anime_name] = guide
            self._save_cache()
            if log_fn: log_fn(f"Generated and cached ({len(guide)} chars)")
            return guide
        except Exception as e:
            if log_fn: log_fn(f"Guide failed: {e}")
            return anime_name

    def translate_batch(self, texts, sys_prompt, model, use_context=False,
                        prev_context=None, next_context=None, log_fn=None):
        n = len(texts)

        if use_context:
            inp_lines = []
            if prev_context:
                for j, t in enumerate(prev_context):
                    k = len(prev_context) - j
                    inp_lines.append(f"[CTX-{k}] {t}" if t.strip() else f"[CTX-{k}] ")
            else:
                inp_lines.append("[CTX] (scene start)")
            for i, t in enumerate(texts):
                inp_lines.append(f"[{i}] {t}" if t.strip() else f"[{i}] ")
            if next_context:
                for j, t in enumerate(next_context):
                    inp_lines.append(f"[CTX+{j+1}] {t}" if t.strip() else f"[CTX+{j+1}] ")
            else:
                inp_lines.append("[CTX] (scene continues)")
            inp = "\n".join(inp_lines)
            note = "\nIMPORTANT: Only output lines for [0]-[N]. NEVER include [CTX] markers in your response. Follow the translation guide strictly for tone, terminology, and character voice. Remove periods (。) from translations."
        else:
            inp = "\n".join(f"[{i}] {t}" if t.strip() else f"[{i}] " for i, t in enumerate(texts))
            note = "\nFollow the translation guide above for tone, terminology, and character voice. Remove periods (。) from translations. Keep ? and !."

        full_prompt = sys_prompt + note

        for attempt in range(3):
            try:
                if log_fn and attempt > 0:
                    log_fn(f"Retry batch (attempt {attempt + 1})...")
                r = self.client.chat.completions.create(
                    model=model,
                    messages=[{"role": "system", "content": full_prompt},
                              {"role": "user", "content": inp}],
                    temperature=0.3, max_tokens=6000,
                    extra_body={"enable_thinking": False})
                out = r.choices[0].message.content.strip()
                trans = [""] * n
                cur_idx = -1
                for line in out.split('\n'):
                    m = re.match(r'\[(\d+)\]\s*(.*)', line.strip())
                    if m:
                        cur_idx = int(m.group(1))
                        if 0 <= cur_idx < n:
                            trans[cur_idx] = m.group(2).strip()
                    elif cur_idx >= 0 and cur_idx < n:
                        if trans[cur_idx]:
                            trans[cur_idx] += " "
                        trans[cur_idx] += line.strip()
                for i in range(n):
                    if trans[i]:
                        trans[i] = self._clean_punct(trans[i])
                        # Remove any CTX markers that leaked into translation
                        trans[i] = re.sub(r'\[CTX[^\]]*\]', '', trans[i]).strip()
                missing = sum(1 for i, t in enumerate(trans) if not t and texts[i].strip())
                if missing > 0 and attempt < 2:
                    if log_fn: log_fn(f"Missing {missing} items, retry (attempt {attempt+2})...")
                    time.sleep(4 * (attempt + 1))
                    continue
                return trans
            except Exception as e:
                if log_fn: log_fn(f"API error: {e}")
                if attempt < 2: time.sleep(5 * (attempt + 1))
                else: raise
        return [""] * n

    def run_translation(self, video_path, track_index, anime_name, model,
                        context_model, batch_size, use_context, update_fn) -> str:
        def log(msg): update_fn(log=msg)
        # Cancellation check helper
        def is_cancelled():
            return update_fn(check_cancelled=True)

        base = Path(video_path).stem
        parent = Path(video_path).parent
        eng_srt = str(parent / f"{base}.eng.srt")
        out_srt = str(parent / f"{base}.chi.srt")

        try:
                # 1. Extract
            update_fn(state="extracting", message="提取字幕...")
            log(f"Extracting track #{track_index} from {Path(video_path).name}")
            if not self.extract_subtitle(video_path, track_index, eng_srt):
                log("Extraction failed!")
                raise RuntimeError("字幕提取失败")
            log(f"Extracted: {Path(eng_srt).name}")

            subs = pysrt.open(eng_srt, encoding="utf-8")
            total = len(subs)
            clean = [self._strip_html(s.text) for s in subs]
            log(f"Loaded {total} subtitles")

            # 2. Guide
            guide_text = None
            if anime_name:
                update_fn(state="analyzing", message=f"分析《{anime_name}》...")
                log(f"Generating guide for: {anime_name}")
                guide_text = self.generate_guide(anime_name, context_model, log_fn=log)
                prompt = (f"你是专业日漫中文字幕翻译器。\n【番剧翻译指南】\n{guide_text}\n"
                          "将英文字幕译为简体中文。保留[N]格式，只输出翻译。")
            else:
                prompt = "你是日漫字幕翻译器。翻译英文→简体中文。保留[N]格式，只输出翻译。"
                log("Generic translation mode")

            update_fn(guide=guide_text)
            ctx_label = "ON" if use_context else "OFF"
            log(f"Context mode: {ctx_label}")

            # 3. Translate
            nbatch = (total + batch_size - 1) // batch_size
            log(f"Translating: {total} lines, {nbatch} batches, model={model}")
            update_fn(state="translating", total=total, message=f"翻译中 0/{total}")

            translated = []
            t_start = time.time()
            for i in range(0, total, batch_size):
                # Check for cancellation before each batch
                try:
                    if update_fn(check_cancelled=True):
                        log("Translation cancelled by user")
                        raise RuntimeError("翻译已取消")
                except RuntimeError:
                    raise
                except Exception:
                    pass
                chunk = clean[i:i + batch_size]
                log(f"Batch {i // batch_size + 1}/{nbatch} ({len(chunk)} lines)")
                # 真实上下文：前后各2条
                prev_ctx = clean[i-2:i] if i >= 2 else None
                next_ctx = clean[i+batch_size:i+batch_size+2] if i+batch_size < total else None
                result = self.translate_batch(
                    chunk, prompt, model,
                    use_context=use_context,
                    prev_context=prev_ctx if use_context else None,
                    next_context=next_ctx if use_context else None,
                    log_fn=log)
                translated.extend(result)
                time.sleep(1.5)  # 避免 API 限流导致后面批次变慢
                progress = min(i + batch_size, total)
                elapsed = time.time() - t_start
                speed = progress / elapsed if elapsed > 0 else 0
                eta = (total - progress) / speed if speed > 0 else 0
                update_fn(state="translating", progress=progress, total=total,
                          message=f"翻译中 {progress}/{total} ({speed:.1f}/s, ETA {eta:.0f}s)")

            # 4. Verify & Save
            untranslated = 0
            for i in range(total):
                if not translated[i].strip() and clean[i].strip():
                    translated[i] = "[未翻译]"
                    untranslated += 1
            if untranslated > 0:
                log(f"WARNING: {untranslated}/{total} untranslated (marked)")

            update_fn(state="saving", message="保存...")
            log("Saving...")
            for i, s in enumerate(subs):
                if i < len(translated) and translated[i].strip():
                    s.text = translated[i]
            subs.save(out_srt, encoding="utf-8")
            log(f"Saved: {out_srt}")

            # 5. Better samples: first 2 dialogues, 2 middle, 1 longest
            # 5. Build full result for editor
            samples = []
            for i in range(total):
                if i < len(translated):
                    samples.append({
                        "index": i,
                        "en": clean[i],
                        "zh": translated[i]
                    })

            elapsed = time.time() - t_start
            log(f"Complete! {total} lines in {elapsed:.1f}s")
            update_fn(state="done", progress=total, total=total,
                      message=f"完成！{total}条，{elapsed:.0f}秒",
                      output=out_srt, samples=samples)
            return out_srt

        except Exception as e:
            # Only remove incomplete output, keep eng_srt for re-translation
            for f in [out_srt]:
                try:
                    if os.path.exists(f): os.remove(f); log(f"Cleaned up: {Path(f).name}")
                except Exception: pass
            raise
        # finally:
        #     Keep .eng.srt for guide refinement
        #     try:
        #         if os.path.exists(eng_srt): os.remove(eng_srt)
        #     except Exception: pass


def detect_anime_name(video_path: str) -> Optional[str]:
    parts = Path(video_path).parts
    # Format 1: /AnimeName/Season 1/AnimeName S01E01.mkv
    for i, p in enumerate(parts):
        if re.match(r'^Season\s*\d+$|^S\d+$', p, re.I) and i > 0:
            name = parts[i - 1]
            if re.match(r'^(vol\d*|mnt|home|media|storage)$', name, re.I):
                return None
            return name
    # Format 2: /AnimeName/AnimeName S01E01.mkv (no season subdir)
    filename = Path(video_path).stem
    m = re.match(r'^(.+?)[\s_.-]*[SE]\d{2,}(?:E\d{2,})?$', filename, re.I)
    if m:
        name = m.group(1).strip()
        if len(name) > 1:
            return name
    # Format 3: Use parent directory name if filename contains it
    if len(parts) >= 2:
        parent = parts[-2]
        if parent.lower() in filename.lower():
            return parent
    # Format 4: Filename starts with SxxExx - use parent directory
    if len(parts) >= 2:
        parent = parts[-2]
        if re.match(r'^S\d+E\d+', filename, re.I):
            return parent
    # Format 5: Filename starts with episode indicator - use parent directory
    if len(parts) >= 2:
        parent = parts[-2]
        if re.match(r'^\d+(?:\s|\D)|^(?:第)?\d+[集話話]?(?:[.\s-]\d+)?$|^E\d+', filename, re.I):
            return parent
    # Format 6: Strip episode suffix and match against parent directory
    if len(parts) >= 2:
        parent = parts[-2]
        stripped = re.sub(r'[\s_.-]*(?:S\d+E\d+|E\d+|4K\b|\d+[vV]\d*|\d{2,}(?![a-zA-Z])|1080[pP]|720[pP]|\d+[集話話]?|\bHD\b)[\s_.-]*', '', filename).strip()
        if len(stripped) > 1 and len(parent) > 1:
            common = sum(1 for c in stripped if c in parent)
            if common >= max(len(stripped), len(parent)) * 0.6:
                return parent
    # Format 7: Filename starts with release group tag [Group] - use parent dir
    if len(parts) >= 2:
        parent = parts[-2]
        if re.match(r'^\[', filename):
            return parent
    return None


def map_path(web_path: str) -> str:
    p = web_path.strip()
    if p.startswith("/videos/"): return os.path.join(VIDEO_ROOT, p[8:])
    if p.startswith("/vol3/"): p = p[6:]
    elif p.startswith("vol3/"): p = p[5:]
    if p.startswith("/"): return os.path.join(VIDEO_ROOT, p.lstrip("/"))
    return os.path.join(VIDEO_ROOT, p)


def scan_videos(folder_path: str) -> list[str]:
    """扫描文件夹内所有视频文件"""
    videos = []
    try:
        for root, dirs, files in os.walk(map_path(folder_path)):
            for f in sorted(files):
                if re.search(r'\.(mkv|mp4|avi|mov|ts)$', f, re.I):
                    videos.append(os.path.join(root, f))
    except Exception:
        pass
    return videos
