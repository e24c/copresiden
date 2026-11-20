"""台本（JSON）から、手本 ep1 の画面の作りでショート動画を作る（docs/reference-ep1.md）。

使い方:
    python3 engine/make_short.py <台本.json> <出力フォルダー>

画面（上から）:
    シリーズの背景（シリーズ名は背景に入っている）
    お店の名前（右上）／場面の大見出し（淡い帯に明朝体）／話数の見出し＋点線
    画像（横幅いっぱい、※イメージです）＋縦書きの大きな文字
    左下にアバター（背景を切り抜く）、その右に字幕（白い太字に黒いふち）
    締めの案内の札（シリーズで「入れる」のときだけ）
音: 本人の声 ＋ BGM（声の間は上げ、話している間は下げる）

make_video.py（v1）の部品（声・写真・字幕の区切り）を使う。
"""

from __future__ import annotations

import json
import subprocess
import sys
import wave
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

import make_video as mv
import persona

W, H, FPS, SR = mv.W, mv.H, mv.FPS, mv.SR
SERIF = "/usr/share/fonts/opentype/noto/NotoSerifCJK-Bold.ttc"
SANS = mv.FONT_BOLD

INK = (72, 62, 58)
BAND = (243, 233, 222, 225)  # 見出しの帯
DOT = (214, 146, 104)


def font(path: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(path, size, index=0)


@dataclass
class Cue:
    start: float
    end: float
    text: str
    scene: int  # -1 はオープニング


# ---------------------------------------------------------------- 文字の部品


def heading_layer(lines: list[str], y0: int, n_chars: int | None = None) -> Image.Image:
    """場面の大見出し：1行ずつ淡い帯に乗せる（n_chars で1文字ずつ出す）。"""
    im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    f = font(SERIF, 76)
    lh, padx, pady = 104, 22, 10
    left = n_chars if n_chars is not None else 10**6
    y = y0
    for line in lines:
        shown = line[: max(0, left)]
        left -= len(line)
        full_w = f.getlength(line)
        d.rectangle([48, y, 48 + full_w + padx * 2, y + lh - 8], fill=BAND)
        if shown:
            d.text((48 + padx, y + (lh - 8) // 2), shown, font=f, fill=INK, anchor="lm")
        y += lh + 10
    return im


def episode_layer(title: str, theme: str, y: int, brand: str, brand_y: int) -> Image.Image:
    im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    if title or theme:
        f = font(SERIF, 34)
        d.text((W // 2, y), "｜".join(x for x in (title, theme) if x), font=f, fill=(95, 85, 80), anchor="mm")
        for x in range(60, W - 60, 14):
            d.ellipse([x, y + 30, x + 5, y + 35], fill=DOT)
    if brand:
        d.text((W - 56, brand_y), brand, font=font(SERIF, 30), fill=(120, 110, 105), anchor="ra")
    return im


def vertical_text(lines: list[str], size: int = 88) -> Image.Image:
    """縦書き（右の行から左へ）。句読点は右上に寄せ、長音などは向きを変える。"""
    f = font(SERIF, size)
    col_w = int(size * 1.18)
    rows = max(len(l) for l in lines)
    im = Image.new("RGBA", (col_w * len(lines) + 40, int(size * 1.08) * rows + 40), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    for ci, line in enumerate(lines):
        cx = im.width - 20 - col_w * ci - col_w // 2
        for ri, ch in enumerate(line):
            cy = 20 + int(size * 1.08) * ri + size // 2
            if ch in "ー〜…―":
                g = Image.new("RGBA", (size * 2, size * 2), (0, 0, 0, 0))
                ImageDraw.Draw(g).text((size, size), ch, font=f, fill=INK + (255,), anchor="mm")
                g = g.rotate(-90)
                im.alpha_composite(g, (cx - size, cy - size))
            elif ch in "、。":
                d.text((cx + size * 0.32, cy - size * 0.32), ch, font=f, fill=INK, anchor="mm")
            elif ch in "っゃゅょぁぃぅぇぉッャュョァィゥェォ":
                d.text((cx + size * 0.08, cy - size * 0.06), ch, font=f, fill=INK, anchor="mm")
            else:
                d.text((cx, cy), ch, font=f, fill=INK, anchor="mm")
    # 写真の上でも読めるように、白いにじみを下に敷く
    glow = Image.new("RGBA", im.size, (255, 255, 255, 0))
    a = im.getchannel("A").filter(ImageFilter.MaxFilter(9)).filter(ImageFilter.GaussianBlur(10))
    glow.putalpha(a.point(lambda v: min(255, int(v * 1.6))))
    glow.alpha_composite(im)
    return glow


def wrap_jp(text: str, f: ImageFont.FreeTypeFont, max_w: int) -> list[str]:
    """読点で改行し、それでも長い部分は助詞のあとで切る（言葉の途中で切らない）。"""
    out: list[str] = []
    segs = [x for x in text.replace("、", "、\n").split("\n") if x]
    cur = ""
    for seg in segs:
        if f.getlength(cur + seg) <= max_w:
            cur += seg
            continue
        if cur:
            out.append(cur)
        cur = seg
        while f.getlength(cur) > max_w:
            # 入りきる範囲で、いちばん後ろの助詞のあとで切る。無ければ入るところまで
            fit = max(i for i in range(1, len(cur) + 1) if f.getlength(cur[:i]) <= max_w)
            cut = next((i for i in range(fit, 1, -1) if cur[i - 1] in "がをにはでとのもへや"), fit)
            out.append(cur[:cut])
            cur = cur[cut:]
    if cur:
        out.append(cur)
    return out


def caption_layer(text: str, x0: int, bottom: int) -> Image.Image:
    """字幕：白い太字に黒いふち。アバターの右に、下をそろえて3行まで。"""
    im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    f = font(SANS, 44)
    lines = wrap_jp(text, f, W - x0 - 36)
    lh = 58
    y = bottom - lh * len(lines)
    for line in lines:
        d.text((x0, y), line, font=f, fill=(255, 255, 255), stroke_width=6, stroke_fill=(30, 30, 30))
        y += lh
    return im


def pill(text: str, cy: int) -> Image.Image:
    im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    f = font(SANS, 30)
    w = f.getlength(text) + 56
    d.rounded_rectangle([(W - w) / 2, cy - 30, (W + w) / 2, cy + 30], 30, fill=(255, 255, 255, 235))
    d.text((W // 2, cy), text, font=f, fill=(60, 50, 45), anchor="mm")
    return im


def image_panel(photo: Image.Image, top: int) -> Image.Image:
    """画像を横幅いっぱい、top から下の端まで（はみ出しは切る）。"""
    bw, bh = W, H - top
    sc = max(bw / photo.width, bh / photo.height)
    p = photo.resize((round(photo.width * sc), round(photo.height * sc)), Image.LANCZOS)
    ox, oy = (p.width - bw) // 2, (p.height - bh) // 3
    return p.crop((ox, oy, ox + bw, oy + bh)).convert("RGBA")


def label(text: str) -> Image.Image:
    f = font(SANS, 26)
    im = Image.new("RGBA", (int(f.getlength(text)) + 20, 44), (0, 0, 0, 0))
    ImageDraw.Draw(im).text((10, 22), text, font=f, fill=(70, 60, 55), anchor="lm",
                            stroke_width=3, stroke_fill=(255, 255, 255))
    return im


# ---------------------------------------------------------------- 組み立て


def build(script_path: Path, out_dir: Path) -> None:
    spec = json.loads(script_path.read_text(encoding="utf-8"))
    out_dir.mkdir(parents=True, exist_ok=True)
    reading = spec.get("reading", {})
    top = spec.get("layout", {}).get("top", 300)  # シリーズ名の帯の下
    head_y, ep_y = top + 30, top + 268
    img_y = top + 330

    # 声とアバター
    pdir = persona.assets_dir(spec.get("customer", ""))
    if pdir and (pdir / "voice" / "model.safetensors").exists():
        voice = persona.Voice(pdir / "voice", SR, **spec.get("voice", {}))

        def say(text: str) -> np.ndarray:
            for k, v in reading.items():
                text = text.replace(k, v)
            return voice(text)
    else:
        def say(text: str) -> np.ndarray:
            return mv.synth(text, reading)
    av = persona.Avatar(pdir / "avatar", spec.get("avatar", {}).get("outfit", "gray"))

    # 背景
    bg_name = spec.get("background")
    if pdir and bg_name and (pdir / "backgrounds" / f"{bg_name}.jpg").exists():
        bg = Image.open(pdir / "backgrounds" / f"{bg_name}.jpg").convert("RGBA").resize((W, H))
    else:
        bg = mv.background().convert("RGBA")

    # 1. 声と字幕の時刻（オープニング → 場面）
    gap = 0.25
    parts: list[np.ndarray] = [np.zeros(int(SR * 0.3), np.float32)]
    cues: list[Cue] = []
    spans: list[tuple[float, float]] = []
    t = 0.3
    blocks = [(-1, spec["opening"])] + list(enumerate(spec["scenes"]))
    for si, sc in blocks:
        s0 = t
        for ch in mv.split_caption(sc["voice"], max_chars=20):
            a = say(ch)
            dur = len(a) / SR
            cues.append(Cue(t, t + dur + gap, ch, si))
            parts += [a, np.zeros(int(SR * gap), np.float32)]
            t += dur + gap
        pause = sc.get("pause", 0.35)
        parts.append(np.zeros(int(SR * pause), np.float32))
        t += pause
        spans.append((s0, t))
    total = t + 0.8
    parts.append(np.zeros(int(SR * 0.8), np.float32))
    audio = np.concatenate(parts)
    audio = audio / (np.abs(audio).max() or 1) * 0.85
    wav = out_dir / "voice.wav"
    with wave.open(str(wav), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((audio * 32767).astype(np.int16).tobytes())

    # 2. 絵の部品
    ep = spec.get("episode", {})
    brand = spec.get("brand", "")
    epi = episode_layer(ep.get("title", ""), ep.get("theme", ""), ep_y, brand, top - 6)
    heads = [heading_layer(sc.get("heading", []), head_y) for sc in spec["scenes"]]
    scene_imgs = []
    for sc in spec["scenes"]:
        layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        model = spec.get("model_image")  # シリーズのモデルの人物（<顧客コード>/models/）
        ref = (pdir / model) if (pdir and model and sc.get("model", True)) else None
        photo = mv.gemini_photo(sc.get("photo_prompt", ""), out_dir / "photos", ref)
        if photo is not None:
            layer.alpha_composite(image_panel(photo, img_y), (0, img_y))
            if sc.get("label", "※イメージです"):
                layer.alpha_composite(label(sc.get("label", "※イメージです")), (16, img_y + 12))
        if sc.get("overlay"):
            vt = vertical_text(sc["overlay"])
            x = 28 if sc.get("overlay_side", "right") == "left" else W - vt.width - 28
            # 締めの案内の札があるときは、その下から置く
            layer.alpha_composite(vt, (x, img_y + (150 if spec.get("cta") and sc is spec["scenes"][-1] else 40)))
        scene_imgs.append(layer)
    caps = [caption_layer(c.text, 440, H - 48) for c in cues]
    cta = pill("感想はコメント欄へ、保存・フォローをして見返してね！", img_y + 96) if spec.get("cta") else None

    n_frames = int(total * FPS)
    mouths = persona.mouth_track(audio, SR, FPS)
    blinks = persona.blink_track(n_frames, FPS)
    open_lines = spec["opening"].get("heading", [])
    n_open = sum(len(l) for l in open_lines)
    open_end = spans[0][1]
    welcome_until = 2.5  # オープニングの最初は「こちらへ」のポーズ（手本 ep1）
    # アバターの切り出し：髪のいちばん上から少し余白をとる（画像から測る。決め打ちだと頭が切れた）
    hair_top = min(int(np.where(np.asarray(av.alpha[k]).max(1) > 128)[0].min()) for k in ("closed", "welcome"))
    y0 = max(0, hair_top - 50)
    fw = av.full["closed"].width
    BIG = ((40, y0, fw - 40, av.full["closed"].height), 900)
    SMALL = ((fw // 2 - 560, y0, fw // 2 + 560, y0 + 1330), 360)

    cmd = ["ffmpeg", "-v", "error", "-y",
           "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-",
           "-i", str(wav)]
    bgm = spec.get("bgm")
    bgm_path = (pdir.parent / "bgm" / bgm) if (pdir and bgm) else None
    if bgm_path and bgm_path.exists():
        vol = spec.get("bgm_volume", 0.22)
        cmd += ["-i", str(bgm_path), "-filter_complex",
                f"[1:a]aresample=48000,aformat=channel_layouts=stereo,asplit=2[v][sc];"
                f"[2:a]aresample=48000,aformat=channel_layouts=stereo,atrim=0:{total:.2f},"
                f"afade=t=in:d=1.5,afade=t=out:st={total - 2.5:.2f}:d=2.5,volume={vol}[b];"
                f"[b][sc]sidechaincompress=threshold=0.02:ratio=6:attack=20:release=400[bd];"
                f"[v][bd]amix=inputs=2:duration=first:normalize=0[a]",
                "-map", "0:v", "-map", "[a]"]
        print(f"BGM：{bgm}")
    cmd += ["-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k", "-t", f"{total:.2f}", "-movflags", "+faststart",
            str(out_dir / "video.mp4")]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)

    for fi in range(n_frames):
        tt = fi / FPS
        frame = bg.copy()
        mouth = mouths[min(fi, len(mouths) - 1)] + ("_blink" if blinks[fi] else "")
        if tt < open_end:
            # オープニング：見出しを1文字ずつ（1秒で出し切る）、アバターは大きく
            n = int(n_open * min(1.0, tt / 1.0)) + 1
            frame.alpha_composite(heading_layer(open_lines, head_y, n))
            frame.alpha_composite(epi)
            key = "welcome" if tt < welcome_until else mouth
            a = av.cut(key, *BIG)
            frame.alpha_composite(a, ((W - a.width) // 2, H - a.height))
        else:
            si = next((i for i, (s, e) in enumerate(spans[1:]) if s <= tt < e), len(spans) - 2)
            layer = scene_imgs[si]
            s0 = spans[si + 1][0]
            if si > 0 and tt - s0 < 0.2:  # 画像の切り替えは0.2秒で重ねる
                layer = Image.blend(scene_imgs[si - 1], layer, (tt - s0) / 0.2)
            frame.alpha_composite(layer)
            frame.alpha_composite(heads[si])
            frame.alpha_composite(epi)
            if cta is not None and si == len(spec["scenes"]) - 1:
                frame.alpha_composite(cta)
            a = av.cut(mouth, *SMALL)
            frame.alpha_composite(a, (64, H - a.height))
            cue = next((i for i, c in enumerate(cues) if c.start <= tt < c.end), None)
            if cue is not None and cues[cue].scene >= 0:
                frame.alpha_composite(caps[cue])
        proc.stdin.write(frame.convert("RGB").tobytes())
    proc.stdin.close()
    proc.wait()

    (out_dir / "timing.json").write_text(
        json.dumps([c.__dict__ for c in cues], ensure_ascii=False, indent=1), encoding="utf-8")
    post = spec.get("post", {})
    lines = [post.get("text", "")]
    if post.get("hashtags"):
        lines.append(" ".join("#" + h for h in post["hashtags"]))
    lines.append("※AI音声")
    (out_dir / "post.txt").write_text("\n\n".join(l for l in lines if l) + "\n", encoding="utf-8")
    print(f"done: {out_dir / 'video.mp4'} ({total:.1f}s, {len(cues)} captions)")


if __name__ == "__main__":
    build(Path(sys.argv[1]), Path(sys.argv[2]))
