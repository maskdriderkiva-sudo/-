# AGENTS.md — このリポジトリで作業する AI へ

GBForge = ゲームボーイ / カラー用のゲーム開発環境 (GBDK-2020・日本語フォント・ロムライタ・GB Media Studio・Qt のネイティブエディタ)。
**詳しい引き継ぎ書は [docs/HANDOFF.md](docs/HANDOFF.md)**。作業の前に必ず読むこと。

## 最低限のルール
- 利用者とは日本語で話す。UI・メッセージ・コメント・コミットメッセージもやさしい日本語
- 既存のプロジェクト (ユーザーの GBDK フォルダ、build.bat、C ソース) を壊さない。`.kgbproj` は読まない・書かない
- できていないことを「できた」と言わない。出せない FPS を表示しない。確かめていないことは「未確認」と書く
- 新しい機能はまず `gbforge/server.py` (Media Studio は `gbforge/media/api.py`) の API に作り、
  ネイティブアプリ (`gbforge/app`, `LocalApi` 経由) とブラウザ版 (`gbforge/editor`) はそれを呼ぶ
- GB Media Studio の `media/sim.py` (プレビュー)・`media/runtime/gbmedia.c` (ROM)・`media/encode.py` は同じ規則で動く。
  1 つを変えたら全部を合わせ、`tests/test_media.py` の ROM 比較を通す

## テスト
```bash
pip install -e ".[test]"
GBDK_HOME=<GBDK-2020 のフォルダ> pytest -q     # 53 件。GBDK が無いと一部スキップ
```
Linux で Qt を動かすには libegl1 / libgl1 / libxkbcommon0 / fonts-noto-cjk が必要。画面なしは `QT_QPA_PLATFORM=offscreen`。
