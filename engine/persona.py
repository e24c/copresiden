"""お客様の声とアバター（`copresiden-assets` の <顧客コード>/）。

置き場所のルールは docs/assets.md。置き場所は環境変数 COPRESIDEN_ASSETS（既定 ../copresiden-assets）。
見つからなければ None を返し、make_video.py は仮の声・仮の顔を使う。

声は Style-Bert-VITS2。ライブラリの版はモデルを作った版に合わせる（C0001 は 2.7.0）。
"""

from __future__ import annotations

import os
import random
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter


def assets_dir(customer: str) -> Path | None:
    root = Path(os.environ.get("COPRESIDEN_ASSETS", Path(__file__).resolve().parents[2] / "copresiden-assets"))
    d = root / customer
    return d if d.is_dir() else None


# ---------------------------------------------------------------- 声


class Voice:
    """Style-Bert-VITS2 で読み上げる。出力は sr（48000）にそろえる。"""

    BERT = "ku-nlp/deberta-v2-large-japanese-char-wwm"

    def __init__(self, voice_dir: Path, sr: int, style: str = "Neutral", **infer_kw):
        os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
        from style_bert_vits2.constants import Languages
        from style_bert_vits2.nlp import bert_models
        from style_bert_vits2.tts_model import TTSModel

        bert_models.load_model(Languages.JP, self.BERT)
        bert_models.load_tokenizer(Languages.JP, self.BERT)
        self.model = TTSModel(model_path=voice_dir / "model.safetensors", config_path=voice_dir / "config.json",
                              style_vec_path=voice_dir / "style_vectors.npy", device="cpu")
        self.model.load()
        # モデルは半精度で保存されている。新しい PyTorch では型が食い違うので単精度にそろえる
        (getattr(self.model, "net_g", None) or getattr(self.model, "_TTSModel__net_g")).float()
        bert_models.load_model(Languages.JP).float()
        self.sr, self.style, self.kw = sr, style, infer_kw

    def __call__(self, text: str) -> np.ndarray:
        from scipy.signal import resample_poly

        sr0, a = self.model.infer(text=text, style=self.style, **self.kw)
        a = a.astype(np.float32) / 32768.0 if a.dtype == np.int16 else a.astype(np.float32)
        if sr0 != self.sr:
            g = np.gcd(sr0, self.sr)
            a = resample_poly(a, self.sr // g, sr0 // g).astype(np.float32)
        return a


# ---------------------------------------------------------------- アバター


def _patch(base: Image.Image, src: Image.Image, box: tuple[int, int, int, int], feather: int = 18) -> Image.Image:
    """src の box の部分だけを base に貼る。周りの肌の色の差をならし、境目をぼかす。"""
    x0, y0, x1, y1 = box
    b = np.asarray(base, dtype=np.float32)
    s = np.asarray(src, dtype=np.float32)
    # 箱のふち（肌）で色の差を測って、貼る部分を base の色に寄せる
    ring = np.zeros(b.shape[:2], bool)
    ring[y0:y1, x0:x1] = True
    ring[y0 + feather:y1 - feather, x0 + feather:x1 - feather] = False
    s = s + (b[ring].mean(0) - s[ring].mean(0))
    m = Image.new("L", base.size, 0)
    ImageDraw.Draw(m).rounded_rectangle([x0 + feather, y0 + feather, x1 - feather, y1 - feather], feather, fill=255)
    m = np.asarray(m.filter(ImageFilter.GaussianBlur(feather / 2)), dtype=np.float32)[..., None] / 255
    out = b * (1 - m) + s * m
    return Image.fromarray(np.clip(out, 0, 255).astype(np.uint8))


def cutout_alpha(img: Image.Image, tol: int = 22) -> Image.Image:
    """背景（画像のふちとつながった、ふちと同じ色の部分）を透明にするための alpha。

    背景つきの JPEG 用。背景と同じ色の服（白い背景に白のTシャツなど）は切り抜けない。
    正式なアバター画像は背景が透明な PNG で作ってもらう（docs/assets.md、docs/issues.md の3）。
    """
    import cv2

    a = np.asarray(img.convert("RGB")).astype(np.int16)
    border = np.concatenate([a[0], a[-1], a[:, 0], a[:, -1]])
    bg = np.median(border, 0)
    cand = (np.abs(a - bg).max(2) < tol).astype(np.uint8)
    _, lab = cv2.connectedComponents(cand, connectivity=4)
    edge = set(np.unique(np.concatenate([lab[0], lab[-1], lab[:, 0], lab[:, -1]]))) - {0}
    fg = (~np.isin(lab, list(edge))).astype(np.uint8) * 255
    fg = cv2.morphologyEx(fg, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
    fg = cv2.erode(fg, np.ones((3, 3), np.uint8))
    return Image.fromarray(cv2.GaussianBlur(fg, (0, 0), 1.5))


class Avatar:
    """口パク（声の大きさで closed／half／open）、まばたき、ポーズ（point／welcome）。"""

    # 上半身の切り出し（元画像 1792×2388 で、手のポーズが入る範囲）
    CROP = (196, 380, 1596, 2200)
    # 口と目の範囲（元画像の座標）。口パクとまばたきは、この部分だけを closed に貼って作る
    #（画像ごとに体・髪・色味が少しずつ違うので、まるごと切り替えるとちらつく）
    MOUTH = (740, 930, 1060, 1150)
    EYES = (640, 700, 1160, 860)

    def __init__(self, avatar_dir: Path, outfit: str):
        d = avatar_dir / outfit
        if not d.is_dir():
            d = next(p for p in sorted(avatar_dir.iterdir()) if p.is_dir())
        self.outfit = d.name

        def load(name: str) -> Image.Image | None:
            for ext in (".png", ".jpg", ".jpeg"):
                f = d / (name + ext)
                if f.exists():
                    return Image.open(f)
            return None

        def rgb_alpha(im: Image.Image) -> tuple[Image.Image, Image.Image]:
            if im.mode == "RGBA":  # 背景が透明な PNG はそのまま使う
                return im.convert("RGB"), im.getchannel("A")
            return im.convert("RGB"), cutout_alpha(im)

        closed, self.alpha_face = rgb_alpha(load("closed"))
        raw = {k: load(k) for k in ("half", "open", "blink", "point", "point_blink", "welcome")}
        raw = {k: (v.convert("RGB") if k in ("half", "open", "blink") and v is not None else v) for k, v in raw.items()}
        raw["half"] = raw["half"] or raw["open"] or closed
        raw["open"] = raw["open"] or raw["half"]
        self.full = {"closed": closed}
        for k in ("half", "open"):
            self.full[k] = _patch(closed, raw[k], self.MOUTH)
        for k in ("closed", "half", "open"):
            self.full[k + "_blink"] = _patch(self.full[k], raw["blink"], self.EYES) if raw["blink"] else self.full[k]
        # ポーズは手の形が違うので、まるごと使う（alpha も画像ごと）。無いものは近いもので代用する（docs/assets.md）
        self.alpha = {k: self.alpha_face for k in self.full}
        for k in ("point", "point_blink", "welcome"):
            if raw[k] is not None:
                self.full[k], self.alpha[k] = rgb_alpha(raw[k])
        for k, alt in (("point", "open"), ("point_blink", "point"), ("welcome", "point")):
            if k not in self.full:
                self.full[k], self.alpha[k] = self.full[alt], self.alpha[alt]
        self.img = {k: v.crop(self.CROP) for k, v in self.full.items()}  # カード用
        self._cache: dict[tuple, Image.Image] = {}

    def cut(self, key: str, box: tuple[int, int, int, int], width: int) -> Image.Image:
        """背景を切り抜いた画像（元画像の box の範囲を、幅 width にして返す）。"""
        ck = ("cut", key, box, width)
        if ck not in self._cache:
            im = self.full[key].convert("RGBA")
            im.putalpha(self.alpha[key])
            im = im.crop(box)
            h = round(im.height * width / im.width)
            self._cache[ck] = im.resize((width, h), Image.LANCZOS)
        return self._cache[ck]

    def card(self, key: str, w: int, h: int, radius: int = 36) -> Image.Image:
        """角の丸いカード（背景つきのまま見せる）。"""
        ck = (key, w, h)
        if ck not in self._cache:
            im = self.img[key]
            sc = max(w / im.width, h / im.height)
            im = im.resize((round(im.width * sc), round(im.height * sc)), Image.LANCZOS)
            ox = (im.width - w) // 2
            im = im.crop((ox, 0, ox + w, h)).convert("RGBA")
            mask = Image.new("L", (w, h), 0)
            ImageDraw.Draw(mask).rounded_rectangle([0, 0, w - 1, h - 1], radius, fill=255)
            im.putalpha(mask)
            self._cache[ck] = im
        return self._cache[ck]


def mouth_track(audio: np.ndarray, sr: int, fps: int) -> list[str]:
    """1コマごとの口の形（closed／half／open）。短いちらつきは前の形を保つ。"""
    hop = sr // fps
    n = len(audio) // hop + 1
    rms = np.array([np.sqrt(np.mean(audio[i * hop:(i + 1) * hop] ** 2) + 1e-12) for i in range(n)])
    voiced = rms[rms > 0.01]
    ref = np.percentile(voiced, 90) if len(voiced) else 1.0
    lv = rms / ref
    out: list[str] = []
    for v in lv:
        out.append("closed" if v < 0.12 else "half" if v < 0.5 else "open")
    # 1コマだけの変化はならす
    for i in range(1, len(out) - 1):
        if out[i - 1] == out[i + 1] != out[i]:
            out[i] = out[i - 1]
    return out


def blink_track(n_frames: int, fps: int, seed: int = 7) -> list[bool]:
    """3〜5秒に1回、0.13秒ほど目を閉じる。"""
    rnd = random.Random(seed)
    out = [False] * n_frames
    t = rnd.uniform(1.5, 3.0)
    while t * fps < n_frames:
        s = int(t * fps)
        for i in range(s, min(n_frames, s + 4)):
            out[i] = True
        t += rnd.uniform(3.0, 5.0)
    return out
