# -*- coding: utf-8 -*-
"""双语字幕功能测试：检测 / 拆分 / 时间轴合并 / run_translation 双语源短路"""
import os
import sys
import tempfile
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("VIDEO_MOUNT", "/tmp/__vid_test")
os.environ.setdefault("DATA_DIR", "/tmp/__data_test")

import pysrt
from app.translator import (is_bilingual_text, detect_bilingual_srt,
                            split_bilingual_lines, merge_to_bilingual)

PASS, FAIL = 0, 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name} {detail}")


def make_srt(path, entries):
    """entries: [(start_ms, end_ms, text)]"""
    with open(path, "w", encoding="utf-8") as f:
        for i, (s, e, t) in enumerate(entries, 1):
            h1, m1, s1, ms1 = s // 3600000, s // 60000 % 60, s // 1000 % 60, s % 1000
            h2, m2, s2, ms2 = e // 3600000, e // 60000 % 60, e // 1000 % 60, e % 1000
            f.write(f"{i}\n{h1:02d}:{m1:02d}:{s1:02d},{ms1:03d} --> "
                    f"{h2:02d}:{m2:02d}:{s2:02d},{ms2:03d}\n{t}\n\n")


print("── 1. is_bilingual_text ──")
check("双语条目(英上中下)", is_bilingual_text("Hello there\n你好啊"))
check("双语条目(中上英下)", is_bilingual_text("你好啊\nHello there"))
check("纯英文", not is_bilingual_text("Hello there my friend"))
check("纯中文", not is_bilingual_text("你好啊朋友"))
check("单行混合算双语", is_bilingual_text("Hello 世界"))

print("── 2. detect_bilingual_srt ──")
tmp = tempfile.mkdtemp()
bi_srt = os.path.join(tmp, "sample.bi.srt")
make_srt(bi_srt, [
    (0, 2000, "Hello there\n你好啊"),
    (2000, 4000, "How are you?\n你好吗？"),
    (4000, 6000, "I am fine\n我很好"),
    (6000, 8000, "Good bye\n再见"),
])
check("双语文件判定", detect_bilingual_srt(bi_srt))

mono_srt = os.path.join(tmp, "sample.eng.srt")
make_srt(mono_srt, [
    (0, 2000, "Hello there"),
    (2000, 4000, "How are you?"),
    (4000, 6000, "I am fine"),
    (6000, 8000, "Good bye"),
])
check("纯英文文件判定", not detect_bilingual_srt(mono_srt))

mixed_srt = os.path.join(tmp, "sample.mixed.srt")
make_srt(mixed_srt, [  # 只有 1/4 双语，不该判为双语
    (0, 2000, "Hello there\n你好啊"),
    (2000, 4000, "How are you?"),
    (4000, 6000, "I am fine"),
    (6000, 8000, "Good bye"),
])
check("少数双语行不误判", not detect_bilingual_srt(mixed_srt))

print("── 3. split_bilingual_lines ──")
latin, cjk = split_bilingual_lines("Hello there\n你好啊")
check("拆分英文侧", latin == "Hello there", f"got {latin!r}")
check("拆分中文侧", cjk == "你好啊", f"got {cjk!r}")
latin, cjk = split_bilingual_lines("你好啊\nHello there")
check("中上英下也可拆", latin == "Hello there" and cjk == "你好啊")
latin, cjk = split_bilingual_lines("纯中文行")
check("纯中文不拆", latin == "" and cjk == "纯中文行")
latin, cjk = split_bilingual_lines("Line one\nLine two\n第一行\n第二行")
check("多行分组", latin == "Line one\nLine two" and cjk == "第一行\n第二行")

print("── 4. merge_to_bilingual ──")
eng_srt = os.path.join(tmp, "ep01.eng.srt")
chi_srt = os.path.join(tmp, "ep01.chi.srt")
bil_srt = os.path.join(tmp, "ep01.bilingual.srt")
make_srt(eng_srt, [
    (0, 2000, "Hello there"),
    (2500, 4500, "How are you?"),
    (5000, 7000, "I am fine"),
])
make_srt(chi_srt, [  # 少一条译文，且时间轴有轻微偏移
    (10, 2000, "你好啊"),
    (2520, 4500, "你好吗"),
    # 缺第三条
])
matched = merge_to_bilingual(eng_srt, chi_srt, bil_srt, orig_first=True)
subs = pysrt.open(bil_srt, encoding="utf-8")
check("合并条数=原文条数", len(subs) == 3, f"got {len(subs)}")
check("对齐计数", matched == 2, f"got {matched}")
check("第1条 英上中下", subs[0].text == "Hello there\n你好啊", f"got {subs[0].text!r}")
check("第2条 英上中下", subs[1].text == "How are you?\n你好吗", f"got {subs[1].text!r}")
check("第3条 无译文保留原文", subs[2].text == "I am fine", f"got {subs[2].text!r}")

# trans_first 布局
matched2 = merge_to_bilingual(eng_srt, chi_srt, bil_srt, orig_first=False)
subs2 = pysrt.open(bil_srt, encoding="utf-8")
check("中上英下布局", subs2[0].text == "你好啊\nHello there", f"got {subs2[0].text!r}")

# 条数相同 + 时间完全一致
make_srt(chi_srt, [
    (0, 2000, "你好啊"),
    (2500, 4500, "你好吗"),
    (5000, 7000, "我很好"),
])
matched3 = merge_to_bilingual(eng_srt, chi_srt, bil_srt, orig_first=True)
subs3 = pysrt.open(bil_srt, encoding="utf-8")
check("全对齐", matched3 == 3, f"got {matched3}")
check("第3条双语", subs3[2].text == "I am fine\n我很好", f"got {subs3[2].text!r}")

print("── 5. run_translation 双语源短路（无 API 调用） ──")
# 构造视频目录：VIDEO_ROOT 下放一个假视频 + 双语外挂字幕
vid_root = os.environ["VIDEO_MOUNT"]
os.makedirs(vid_root, exist_ok=True)
show_dir = os.path.join(vid_root, "TestAnime")
os.makedirs(show_dir, exist_ok=True)
video = os.path.join(show_dir, "TestAnime S01E01.mkv")
with open(video, "wb") as f:
    f.write(b"\x00" * 1024)
ext_bi = os.path.join(show_dir, "TestAnime S01E01.srt")
make_srt(ext_bi, [
    (0, 2000, "Hello there\n你好啊"),
    (2000, 4000, "How are you?\n你好吗？"),
    (4000, 6000, "I am fine\n我很好"),
])

from app.translator import Translator
tr = Translator.__new__(Translator)  # 不初始化 OpenAI client（不联网）
updates = []
def update_fn(**kw):
    updates.append(kw)
    return False

try:
    # list_tracks 会调 ffprobe（不存在→返回[]），find_external_srt 找到双语外挂
    out = tr.run_translation(video, 0, "TestAnime", "m", "m", 12, False,
                             update_fn, bilingual=True)
    done = [u for u in updates if u.get("state") == "done"]
    check("双语源短路完成", bool(done))
    check("输出路径", out == os.path.join(show_dir, "TestAnime S01E01.chi.srt"), f"got {out}")
    check("chi.srt 已生成", os.path.exists(out))
    check("bilingual.srt 已生成", os.path.exists(os.path.join(show_dir, "TestAnime S01E01.bilingual.srt")))
    chi_subs = pysrt.open(out, encoding="utf-8")
    check("chi.srt 只含中文", chi_subs[0].text == "你好啊", f"got {chi_subs[0].text!r}")
    samples = done[0].get("samples") if done else None
    check("samples 含拆分结果", samples and samples[0]["en"] == "Hello there" and samples[0]["zh"] == "你好啊")
    logs = [u for u in updates if "log" in u]
    check("短路日志", any("bilingual" in str(u.get("log", "")).lower() for u in logs))
except Exception as e:
    traceback.print_exc()
    check("双语源短路运行", False, str(e))

print(f"\n结果: {PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
