"""台本（JSON）から縦長のショート動画を作る。

使い方:
    python3 engine/make_video.py <台本.json> <出力フォルダー>

出力:
    video.mp4   … 1080×1920 の動画
    post.txt    … 投稿の説明欄の下書き（「AI音声」を入れる）
    timing.json … 字幕の時刻（点検用）

いまの部品は「仮」:
    声   … Open JTalk の標準の声（お客様の声のモデルができたら差し替える）
    写真 … 図のカード（Gemini の鍵が入ったら写真に差し替える）
    アバター … 丸い仮の顔（アバターができたら差し替える）
"""

from __future__ import annotations

import json
import math
import subprocess
import sys
import wave
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

W, H = 1080, 1920
FPS = 30
SR = 48000

FONT_BLACK = "/usr/share/fonts/opentype/noto/NotoSansCJK-Black.ttc"
FONT_BOLD = "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"
FONT_REG = "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"
JP = 0  # ttc の中の日本語の字体の番号

# 色（お客様ごとに series.md で決める。いまは仮）
BG_TOP = (247, 239, 228)
BG_BOTTOM = (233, 219, 200)
INK = (58, 46, 38)
ACCENT = (176, 122, 84)
SOFT = (255, 252, 247)

CAPTION_SIZE = 54  # デザインのルール：字幕は 54px
SAFE_TOP = 230  # 投稿先の画面の上の帯にかからないように
CAPTION_Y = 1290
AVATAR_SMALL = 230
AVATAR_POS = (60, 1500)


def font(path: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(path, size, index=JP)


# ---------------------------------------------------------------- 声


def synth(text: str, reading: dict[str, str]) -> np.ndarray:
    """仮の声。お客様の声のモデルに替えるときは、この関数だけを差し替える。"""
    import pyopenjtalk

    for k, v in reading.items():
        text = text.replace(k, v)
    x, sr = pyopenjtalk.tts(text, speed=1.05)
    assert sr == SR
    return (x / 32768.0).astype(np.float32)


# ---------------------------------------------------------------- 字幕の区切り


def split_caption(text: str, max_chars: int = 14) -> list[str]:
    """句点・読点で区切り、1枚の字幕を最大2行にする。"""
    parts: list[str] = []
    buf = ""
    for ch in text:
        buf += ch
        if ch in "。…！？":
            parts.append(buf)
            buf = ""
    if buf:
        parts.append(buf)
    out: list[str] = []
    for p in parts:
        p = p.strip()
        if len(p.rstrip("。")) <= max_chars * 2:
            out.append(p)
            continue
        # 長い文は読点で分ける
        cur = ""
        for seg in p.replace("、", "、\n").split("\n"):
            if cur and len(cur) + len(seg) > max_chars * 2:
                out.append(cur)
                cur = seg
            else:
                cur += seg
        if cur:
            out.append(cur)
    # 字幕には句点を出さない
    return [c.rstrip("。") for c in out if c.strip("。")]


def wrap(text: str, f: ImageFont.FreeTypeFont, max_w: int) -> list[str]:
    lines: list[str] = []
    for para in text.split("\n"):
        cur = ""
        for ch in para:
            if f.getlength(cur + ch) > max_w and cur:
                # 句読点を行頭に置かない
                if ch in "、。…）」":
                    cur += ch
                    continue
                lines.append(cur)
                cur = ch
            else:
                cur += ch
        if cur:
            lines.append(cur)
    return lines


def wrap_phrases(text: str, f: ImageFont.FreeTypeFont, max_w: int) -> list[str]:
    """読点の位置で改行を優先し、単語の途中で切れにくくする。"""
    segs = [s for s in text.replace("、", "、\n").split("\n") if s]
    lines: list[str] = []
    cur = ""
    for seg in segs:
        if f.getlength(cur + seg) <= max_w:
            cur += seg
            continue
        if cur:
            lines.append(cur)
        cur = ""
        if f.getlength(seg) <= max_w:
            cur = seg
        else:
            lines += wrap(seg, f, max_w)
    if cur:
        lines.append(cur)
    return lines


# ---------------------------------------------------------------- 絵（仮のカード）


def background() -> Image.Image:
    g = np.linspace(0, 1, H)[:, None]
    top = np.array(BG_TOP)[None, None, :]
    bot = np.array(BG_BOTTOM)[None, None, :]
    arr = (top * (1 - g[..., None]) + bot * g[..., None]).repeat(W, axis=1)
    return Image.fromarray(arr.astype(np.uint8), "RGB")


def draw_head(d: ImageDraw.ImageDraw, cx: int, cy: int, r: int) -> None:
    """横顔のシルエット（右向き）。"""
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(236, 212, 190))
    d.rectangle([cx - r * 0.35, cy + r * 0.6, cx + r * 0.25, cy + r * 1.5], fill=(236, 212, 190))
    d.ellipse([cx - r * 0.2, cy - r * 0.05, cx + r * 0.12, cy + r * 0.35], fill=(222, 190, 165))  # 耳
    d.pieslice([cx - r * 1.05, cy - r * 1.1, cx + r * 1.0, cy + r * 0.6], 180, 360, fill=(70, 52, 42))  # 髪
    d.ellipse([cx + r * 0.85, cy - r * 0.05, cx + r * 1.12, cy + r * 0.25], fill=(236, 212, 190))  # 鼻


def arrow(d: ImageDraw.ImageDraw, pts: list[tuple[float, float]], width: int = 14) -> None:
    d.line(pts, fill=ACCENT, width=width, joint="curve")
    (x1, y1), (x2, y2) = pts[-2], pts[-1]
    a = math.atan2(y2 - y1, x2 - x1)
    s = width * 3
    d.polygon(
        [
            (x2 + math.cos(a) * s * 0.6, y2 + math.sin(a) * s * 0.6),
            (x2 + math.cos(a + 2.4) * s, y2 + math.sin(a + 2.4) * s),
            (x2 + math.cos(a - 2.4) * s, y2 + math.sin(a - 2.4) * s),
        ],
        fill=ACCENT,
    )


def icon(kind: str, size: int = 760) -> Image.Image:
    im = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    c = size // 2
    if kind == "brush":
        draw_head(d, c - 30, c + 40, 210)
        # 生え際から後ろへ
        arrow(d, [(c + 200, c - 170), (c + 40, c - 250), (c - 140, c - 230), (c - 250, c - 120)])
        # ブラシ
        d.rounded_rectangle([c + 120, c - 330, c + 330, c - 200], 40, fill=(190, 190, 196))
        d.rounded_rectangle([c + 300, c - 290, c + 470, c - 245], 20, fill=(120, 96, 80))
    elif kind == "shine":
        draw_head(d, c - 30, c + 40, 210)
        for (x, y, s) in [(c - 120, c - 300, 60), (c + 120, c - 280, 44), (c - 260, c - 160, 36)]:
            d.polygon([(x, y - s), (x + s * 0.25, y - s * 0.25), (x + s, y), (x + s * 0.25, y + s * 0.25),
                       (x, y + s), (x - s * 0.25, y + s * 0.25), (x - s, y), (x - s * 0.25, y - s * 0.25)],
                      fill=(255, 255, 255))
    elif kind == "neck":
        draw_head(d, c - 30, c + 0, 210)
        # 耳の後ろ → あごの下 → 首すじ
        arrow(d, [(c - 120, c + 20), (c - 120, c + 160), (c - 60, c + 300)])
        for (x, y) in [(c - 120, c + 20), (c - 120, c + 160)]:
            d.ellipse([x - 22, y - 22, x + 22, y + 22], outline=ACCENT, width=8)
    elif kind == "moon":
        d.ellipse([c - 200, c - 200, c + 200, c + 200], fill=(250, 226, 160))
        d.ellipse([c - 110, c - 260, c + 270, c + 120], fill=SOFT)
        for (x, y) in [(c + 210, c + 150), (c - 250, c - 230), (c + 260, c - 260)]:
            d.ellipse([x - 12, y - 12, x + 12, y + 12], fill=(250, 226, 160))
    return im


def calendar(d: ImageDraw.ImageDraw, x: int, y: int, checked: int, label: str, good: bool) -> None:
    cell, gap = 62, 12
    f = font(FONT_BLACK, 52)
    d.text((x + (7 * (cell + gap)) / 2, y - 50), label, font=f, fill=INK, anchor="mm")
    for i in range(14):
        cx = x + (i % 7) * (cell + gap)
        cy = y + 10 + (i // 7) * (cell + gap)
        on = i < checked
        d.rounded_rectangle([cx, cy, cx + cell, cy + cell], 12,
                            fill=(ACCENT if on else SOFT), outline=(210, 196, 180), width=3)
        if on:
            d.line([(cx + 14, cy + 32), (cx + 27, cy + 46), (cx + 49, cy + 18)], fill=SOFT, width=7)
    if not good:
        d.text((x + 7 * (cell + gap) - 10, y + 2 * (cell + gap) + 40), "…", font=f, fill=INK, anchor="rm")


def scene_card(visual: dict) -> Image.Image:
    im = background()
    d = ImageDraw.Draw(im)
    panel = [60, SAFE_TOP, W - 60, CAPTION_Y - 50]
    d.rounded_rectangle(panel, 48, fill=SOFT)
    kind = visual.get("kind", "card")
    if kind == "compare":
        calendar(d, 290, SAFE_TOP + 200, 14, visual.get("left", "続けた"), True)
        calendar(d, 290, SAFE_TOP + 580, 3, visual.get("right", "三日でやめた"), False)
    else:
        if visual.get("step"):
            f = font(FONT_BLACK, 44)
            tag = f"手順 {visual['step']}"
            tw = f.getlength(tag)
            d.rounded_rectangle([110, SAFE_TOP + 50, 110 + tw + 60, SAFE_TOP + 130], 40, fill=ACCENT)
            d.text((140, SAFE_TOP + 90), tag, font=f, fill=SOFT, anchor="lm")
        title = visual.get("title", "")
        f = font(FONT_BLACK, 76)
        y = SAFE_TOP + 170
        for line in title.split("\n"):
            d.text((W // 2, y), line, font=f, fill=INK, anchor="mt")
            y += 100
        if visual.get("icon"):
            ic = icon(visual["icon"], 620)
            im.paste(ic, ((W - 620) // 2, y + 10), ic)
    # 仮の印
    d.text((W - 90, SAFE_TOP + 40), "仮の絵", font=font(FONT_REG, 30), fill=(170, 150, 130), anchor="ra")
    return im


# ---------------------------------------------------------------- アバター（仮）


def avatar(size: int) -> Image.Image:
    im = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.ellipse([0, 0, size - 1, size - 1], fill=(120, 92, 74))
    d.ellipse([size * 0.06, size * 0.06, size * 0.94, size * 0.94], fill=(244, 228, 210))
    d.ellipse([size * 0.32, size * 0.2, size * 0.68, size * 0.56], fill=(120, 92, 74))
    d.pieslice([size * 0.18, size * 0.55, size * 0.82, size * 1.15], 180, 360, fill=(120, 92, 74))
    d.text((size / 2, size * 0.86), "アバター（仮）", font=font(FONT_BOLD, max(14, size // 11)),
           fill=SOFT, anchor="mm")
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse([0, 0, size - 1, size - 1], fill=255)
    im.putalpha(mask)
    return im


# ---------------------------------------------------------------- 字幕の絵


def caption_layer(text: str) -> Image.Image:
    im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    f = font(FONT_BLACK, CAPTION_SIZE)
    lines = wrap_phrases(text, f, W - 150)
    lh = int(CAPTION_SIZE * 1.4)
    box_h = lh * len(lines) + 50
    top = CAPTION_Y
    d.rounded_rectangle([50, top, W - 50, top + box_h], 30, fill=(255, 255, 255, 235))
    for i, line in enumerate(lines):
        d.text((W // 2, top + 25 + lh * i + lh // 2), line, font=f, fill=INK, anchor="mm")
    return im


def opening_layer(question: str, n_chars: int) -> Image.Image:
    """最初の1秒：問いかけを1文字ずつ出す。"""
    im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    f = font(FONT_BLACK, 92)
    lines = wrap(question, f, W - 160)
    shown = n_chars
    y = 300
    for line in lines:
        part = line[: max(0, shown)]
        shown -= len(line)
        d.text((W // 2 + 4, y + 4), part, font=f, fill=(0, 0, 0, 60), anchor="mt")
        d.text((W // 2, y), part, font=f, fill=ACCENT, anchor="mt")
        y += 130
    return im


# ---------------------------------------------------------------- 組み立て


@dataclass
class Cue:
    start: float
    end: float
    text: str
    scene: int


def build(script_path: Path, out_dir: Path) -> None:
    spec = json.loads(script_path.read_text(encoding="utf-8"))
    out_dir.mkdir(parents=True, exist_ok=True)
    reading = spec.get("reading", {})
    gap = 0.25

    # 1. 声と字幕の時刻
    audio_parts: list[np.ndarray] = []
    cues: list[Cue] = []
    scene_spans: list[tuple[float, float]] = []
    t = 0.3
    audio_parts.append(np.zeros(int(SR * 0.3), np.float32))
    for si, sc in enumerate(spec["scenes"]):
        chunks = split_caption(sc["voice"])
        s0 = t
        for ch in chunks:
            a = synth(ch, reading)
            dur = len(a) / SR
            cues.append(Cue(t, t + dur + gap, ch, si))
            audio_parts += [a, np.zeros(int(SR * gap), np.float32)]
            t += dur + gap
        pause = sc.get("pause", 0.3)
        audio_parts.append(np.zeros(int(SR * pause), np.float32))
        t += pause
        scene_spans.append((s0, t))
    total = t + 0.6
    audio_parts.append(np.zeros(int(SR * 0.6), np.float32))
    audio = np.concatenate(audio_parts)
    peak = np.abs(audio).max() or 1
    audio = audio / peak * 0.85
    wav_path = out_dir / "voice.wav"
    with wave.open(str(wav_path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((audio * 32767).astype(np.int16).tobytes())

    # 2. 絵を先に作っておく
    scene_imgs = [scene_card(sc["visual"]).convert("RGBA") for sc in spec["scenes"]]
    cap_imgs = [caption_layer(c.text) for c in cues]
    av_small = avatar(AVATAR_SMALL)
    av_big = avatar(560)
    question = spec.get("opening", {}).get("question", "")

    # 3. 1コマずつ描いて ffmpeg に流す
    n_frames = int(total * FPS)
    cmd = [
        "ffmpeg", "-v", "error", "-y",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-",
        "-i", str(wav_path),
        "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "160k", "-shortest", "-movflags", "+faststart",
        str(out_dir / "video.mp4"),
    ]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    # オープニングは最初の字幕の間だけ（大きいアバター＋問いかけ）。そのあとは1つ目の場面の絵に移る
    opening_end = cues[0].end if question else 0
    for fi in range(n_frames):
        tt = fi / FPS
        si = next((i for i, (a, b) in enumerate(scene_spans) if a <= tt < b), len(scene_spans) - 1)
        if tt < scene_spans[0][0]:
            si = 0
        frame = scene_imgs[si].copy()
        # 場面の切り替え：0.2秒で重ねる
        s0 = scene_spans[si][0]
        if si > 0 and tt - s0 < 0.2:
            frame = Image.blend(scene_imgs[si - 1], frame, (tt - s0) / 0.2)
        if question and tt < opening_end:
            # オープニング：アバター大きめ、問いかけを1文字ずつ（1秒で出し切る）
            n = int(len(question) * min(1.0, tt / 1.0)) + 1
            frame = background().convert("RGBA")
            frame.alpha_composite(opening_layer(question, n))
            frame.alpha_composite(av_big, ((W - 560) // 2, 820))
        else:
            frame.alpha_composite(av_small, AVATAR_POS)
        cue = next((i for i, c in enumerate(cues) if c.start <= tt < c.end), None)
        if cue is not None and not (question and tt < opening_end):
            frame.alpha_composite(cap_imgs[cue])
        elif cue is not None and question and tt < opening_end and tt > 1.0:
            # オープニングの字幕は問いかけの下に出さず、アバターの下に出す
            frame.alpha_composite(cap_imgs[cue], (0, 160))
        proc.stdin.write(frame.convert("RGB").tobytes())
    proc.stdin.close()
    proc.wait()

    # 4. 点検用の時刻と、投稿の説明欄
    (out_dir / "timing.json").write_text(
        json.dumps([c.__dict__ for c in cues], ensure_ascii=False, indent=1), encoding="utf-8")
    post = spec.get("post", {})
    lines = [post.get("text", "")]
    if post.get("hashtags"):
        lines.append(" ".join("#" + h for h in post["hashtags"]))
    lines.append("※AI音声")
    (out_dir / "post.txt").write_text("\n\n".join(l for l in lines if l) + "\n", encoding="utf-8")
    print(f"done: {out_dir/'video.mp4'} ({total:.1f}s, {len(cues)} captions)")


if __name__ == "__main__":
    build(Path(sys.argv[1]), Path(sys.argv[2]))
