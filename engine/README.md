# 作る部品

| 部品 | 中身 | 状況 |
| --- | --- | --- |
| 入稿の取り込み | Jotform APIで入稿と添付を取る（`GET /submission/<送信ID>`）。SharePointに移ったのを確かめてから `DELETE /submission/<送信ID>` を2回 | 手作業で確認済み（スクリプト化はこれから） |
| 中身の読み取り | 音声：faster-whisper（medium、`HF_HUB_DISABLE_XET=1`）。字幕：1秒ごとのコマを読む | 手作業で確認済み |
| 台本 | 型・台帳・ルールから作り、`customers/<お客様>/work/<送信ID>.script.json` に書く | 台本JSONの形を決めた |
| 写真 | Geminiで生成。お客様の写真を優先する | 環境変数 `GEMINI_API_KEY` があれば `photo_prompt` から写真を作る（未試験）。無ければ図のカード |
| 声 | お客様の声のモデルで合成する | **仮**：Open JTalk の標準の声（`make_video.py` の `synth()` を差し替える） |
| アバター | 左下に小さく、オープニングだけ大きく | **仮**：丸い仮の顔。使うサービス未定 |
| 動画 | 縦長1080×1920、字幕54px、問いかけを1秒で1文字ずつ | `make_video.py` で作れる（BGMは未） |
| 置く | SharePoint `<お客様>/03_完成` | **未**：接続の道具は1MBまでしか置けない。Graph APIの直の接続が要る |
| 承認 | Jotform「社長の副操縦士 動画の承認」 https://form.jotform.com/263222568702456 （隠し項目 `sid`・`cid`・`ver` をURLで渡す） | 作った |
| 知らせ | 動画と承認フォームのリンクをメールで送る（最初の1週は下書き） | Gmailの下書きで試した（動画は添付） |
| 作業記録 | かかった時間、作り直し、失敗 | 作業記録の md に手で書いている |

## 動画の作り方

```bash
pip install pyopenjtalk pillow numpy
apt-get install -y fonts-noto-cjk
python3 engine/make_video.py customers/aen/work/6708844608416389033.script.json <出力フォルダー>
```

出力は `video.mp4`、`post.txt`（説明欄の案、「※AI音声」入り）、`timing.json`（字幕の時刻）。
読み間違いは台本JSONの `reading` で直す（例：「白髪」→「しらが」）。
