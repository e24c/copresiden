# 作る部品

| 部品 | 中身 | 状況 |
| --- | --- | --- |
| 入稿の取り込み | Jotform APIで入稿と添付を取る（`GET /submission/<送信ID>`）。SharePointに移ったのを確かめてから `DELETE /submission/<送信ID>` を2回 | 手作業で確認済み（スクリプト化はこれから） |
| 中身の読み取り | 音声：faster-whisper（medium、`HF_HUB_DISABLE_XET=1`）。字幕：1秒ごとのコマを読む | 手作業で確認済み |
| 台本 | 型・台帳・ルールから作り、`customers/<お客様>/work/<送信ID>.script.json` に書く | 台本JSONの形を決めた |
| 写真 | Geminiで生成。お客様の写真を優先する | 環境変数 `GEMINI_API_KEY` があれば `photo_prompt` から写真を作る（2026-11-19 A.ENで試験済み）。無ければ図のカード |
| 声 | お客様の声のモデルで合成する | `copresiden-assets` の `<顧客コード>/voice` があれば Style-Bert-VITS2 2.7.0（`persona.py` の `Voice`）。無ければ仮の声（Open JTalk）。2026-11-20 C0001 で試験済み（なまりは課題、`docs/issues.md`） |
| アバター | 左下に小さく、オープニングだけ大きく | `<顧客コード>/avatar/<服>` の画像で作る（`persona.py` の `Avatar`）。口パクは声の大きさで closed／half／open の口だけを貼り替え、まばたきは目だけを貼り替える（まるごと切り替えると色がちらつくため）。オープニングは指差し、締めは「こちらへ」。服は台本JSONの `avatar.outfit`（既定 gray） |
| 動画 | 縦長1080×1920、字幕54px、問いかけを1秒で1文字ずつ | `make_video.py` で作れる（BGMは未） |
| 置く | SharePoint `<お客様>/03_完成` | **未**：接続の道具は1MBまでしか置けない。Graph APIの直の接続が要る |
| 承認 | Jotform「社長の副操縦士 動画の承認」 https://form.jotform.com/263222568702456 （隠し項目 `sid`・`cid`・`ver` をURLで渡す） | 作った |
| 知らせ | 動画と承認フォームのリンクをメールで送る（最初の1週は下書き） | Gmailの下書きで試した（動画は添付） |
| 作業記録 | かかった時間、作り直し、失敗 | 作業記録の md に手で書いている |

## 動画の作り方

本人の声を使うときは、Style-Bert-VITS2 2.7.0 を入れた Python で動かす（声のモデルを作った版に合わせる）。

```bash
git clone --depth 1 https://github.com/litagin02/Style-Bert-VITS2 sbv2-src
uv venv -p 3.11 venv27
VIRTUAL_ENV=venv27 uv pip install ./sbv2-src "numpy<2" "setuptools<81" soundfile scipy pillow
apt-get install -y fonts-noto-cjk
HF_HUB_DISABLE_XET=1 venv27/bin/python engine/make_video.py customers/C0001/work/6708844608416389033.script.json <出力フォルダー>
```

- 声とアバターは `../copresiden-assets/<顧客コード>/` から読む（場所は環境変数 `COPRESIDEN_ASSETS` で変えられる）。無ければ仮の声・仮の顔で作る
- `numpy<2`：読みの辞書（pyopenjtalk）が numpy 2 では動かない。`setuptools<81`：同じく古い書き方を使っている
- 出力は `video.mp4`、`post.txt`（説明欄の案、「※AI音声」入り）、`timing.json`（字幕の時刻）
- 読み間違いは台本JSONの `reading` で直す（例：「白髪」→「しらが」）
