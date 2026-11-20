# 作る部品

| 部品 | 中身 | 状況 |
| --- | --- | --- |
| 入稿の取り込み | Jotform APIで入稿と添付を取る（`GET /submission/<送信ID>`）。SharePointに移ったのを確かめてから `DELETE /submission/<送信ID>` を2回 | 手作業で確認済み（スクリプト化はこれから） |
| 中身の読み取り | 音声：faster-whisper（medium、`HF_HUB_DISABLE_XET=1`）。字幕：1秒ごとのコマを読む | 手作業で確認済み |
| 台本 | 型・台帳・ルールから作り、`customers/<お客様>/work/<送信ID>.script.json` に書く | 台本JSONの形を決めた |
| 写真 | Geminiで生成。お客様の写真を優先する | 環境変数 `GEMINI_API_KEY` があれば `photo_prompt` から写真を作る（2026-11-19 A.ENで試験済み）。無ければ図のカード |
| 声 | お客様の声のモデルで合成する | `copresiden-assets` の `<顧客コード>/voice` があれば Style-Bert-VITS2 2.7.0（`persona.py` の `Voice`）。無ければ仮の声（Open JTalk）。2026-11-20 C0001 で試験済み（なまりは課題、`docs/issues.md`） |
| アバター | 左下に小さく、オープニングだけ大きく | `<顧客コード>/avatar/<服>` の画像で作る（`persona.py` の `Avatar`）。口パクは声の大きさで closed／half／open の口だけを貼り替え、まばたきは目だけを貼り替える（まるごと切り替えると色がちらつくため）。オープニングは指差し、締めは「こちらへ」。服は台本JSONの `avatar.outfit`（既定 gray） |
| 動画 | 縦長1080×1920 | `make_short.py`：手本 ep1 の画面の作り（見出しの帯、話数の見出し、お店の名前、全幅の画像、縦書きの文字、ふち取りの字幕、切り抜いたアバター、案内の札、BGM）。`make_video.py`：最初の作り（v1） |
| 置く | SharePoint `<お客様>/03_完成` | **未**：接続の道具は1MBまでしか置けない。Graph APIの直の接続が要る |
| 承認 | Jotform「社長の副操縦士 動画の承認」 https://form.jotform.com/263222568702456 （隠し項目 `sid`・`cid`・`ver` をURLで渡す） | 作った |
| 知らせ | 動画と承認フォームのリンクをメールで送る（最初の1週は下書き） | Gmailの下書きで試した（動画は添付） |
| 作業記録 | かかった時間、作り直し、失敗 | 作業記録の md に手で書いている |

## 動画の作り方

本人の声を使うときは、Style-Bert-VITS2 2.7.0 を入れた Python で動かす（声のモデルを作った版に合わせる）。

```bash
git clone --depth 1 https://github.com/litagin02/Style-Bert-VITS2 sbv2-src
uv venv -p 3.11 venv27
VIRTUAL_ENV=venv27 uv pip install ./sbv2-src "numpy<2" "setuptools<81" soundfile scipy pillow "opencv-python-headless<4.11"
apt-get install -y fonts-noto-cjk
HF_HUB_DISABLE_XET=1 venv27/bin/python engine/make_short.py customers/C0001/work/ep1-test.script.json <出力フォルダー>
```

- 声とアバターは `../copresiden-assets/<顧客コード>/` から読む（場所は環境変数 `COPRESIDEN_ASSETS` で変えられる）。無ければ仮の声・仮の顔で作る
- `numpy<2`：読みの辞書（pyopenjtalk）が numpy 2 では動かない。`setuptools<81`：同じく古い書き方を使っている
- 出力は `video.mp4`、`post.txt`（説明欄の案、「※AI音声」入り）、`timing.json`（字幕の時刻）
- 読み間違いは台本JSONの `reading` で直す（例：「白髪」→「しらが」）

## 台本JSON（make_short.py）の項目

| 項目 | 中身 |
| --- | --- |
| `background` | シリーズの背景（`<顧客コード>/backgrounds/<名前>.jpg`） |
| `layout.top` | シリーズ名の帯の下の位置（px）。背景ごとに違う |
| `brand` | 右上に出す会社・お店の呼び名（`series.md` の「会社・お店の呼び名」） |
| `episode.title`・`episode.theme` | 話数の見出し（例：「ブラシでトントン①」「今日の表情ケア」） |
| `avatar.outfit` | アバターの服（`avatar/<服>/`） |
| `bgm`・`bgm_volume` | BGM（`bgm/` のファイル名）と音量（既定 0.22） |
| `cta` | 締めの案内の札を出すか（`series.md` の「締めの案内」） |
| `opening.heading`・`opening.voice` | オープニングの見出し（行ごと）と声 |
| `scenes[].heading` | 場面の大見出し（行ごと） |
| `scenes[].overlay`・`overlay_side` | 画像に重ねる縦書きの文字（行ごと）と、置く側（left／right） |
| `scenes[].photo_prompt` | 画像の指示（写真か水彩画かの作風、文字を置く側の余白も書く） |
| `scenes[].label` | 画像の表示（既定「※イメージです」） |
