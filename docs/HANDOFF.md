# GBForge 引き継ぎ書

別の AI (ChatGPT / Codex など) や人がこのリポジトリの作業を続けるための資料です。
最終更新: 2026-11-21 / 版 0.3.0 (`gbforge/__init__.py` の `__version__`)

---

## 1. これは何か

**GBForge** = ゲームボーイ / ゲームボーイカラー用のゲーム開発環境 (「ゲームボーイ版の Unity」)。

- コンパイラは **GBDK-2020 4.3.0** (`lcc`)。無ければ自動ダウンロードしてセットアップする (`gbforge setup`)
- **プロジェクト形式** (`project.gbproj`) でゲームを管理する。2 種類ある
  - **engine 型**: シーン・アクター・会話をエディタで作る。エンジン (`gbforge/engine/*.c`) にユーザーの C スクリプトを組み合わせる
  - **c 型**: GBDK の C プログラムを自由に書く。既存の GBDK フォルダ (main.c / build.bat) も「取り込み」で開ける
- **日本語フォント**: 美咲フォント 8×8 を内蔵。C 用ライブラリ `gbjp` (`#include "gbjp.h"`) で使える
- **ロムライタ**: CUBIC STYLE (GB Cart Flasher 互換) と USB シリアルで通信し、書き込み・ベリファイ・吸い出し・SRAM
- **Visual Studio / VS Code 連携**: `.sln` / `.vscode` を GBDK と gbjp のインクルードパス込みで自動生成
- **GB Media Studio**: 写真・動画を GB / GBC 用に変換し、タイムラインでフェードなどの演出を付けて C ソースに書き出す
- **エディタ**: 0.3.0 から **Qt (PySide6) のネイティブアプリ**が既定。ブラウザ版 (`gbforge editor`) も残っている
- **Windows インストーラー** (NSIS)。Python・GBDK・Qt・(完全版は) PyBoy を同梱

利用者 (オーナー) について:
- 日本語で会話する。UI・メッセージ・コメント・コミットメッセージはすべて **やさしい日本語** にする
- Windows 10/11 を使う。GBDK は `C:\gbdk-win64\gbdk`、自分のゲームは `C:\gbdk-win64\gbdk\Project\` に
  `HighwayStar_GBC_v1.9_HW` や `HighwayStar_GBC_v2.0\HighwayStar_GBC_v2.0` (入れ子) のように **版ごとのフォルダ** で管理している
- 既存のプロジェクトを壊さないことを最優先にしている (下の「守ること」)

---

## 2. リポジトリの構成

```
gbforge/
  cli.py          コマンドライン (gbforge app / editor / build / flash / new / setup / doctor ...)
  project.py      project.gbproj の読み書き、ファイル配置 (engine 型 / c 型 / flat = 既存フォルダ)
  builder.py      ビルド (エンジン生成コード + GBDK コンパイル、または既存 build.bat の実行)
  toolchain.py    GBDK-2020 の検出・ダウンロード・チェックサム検証
  flasher.py      ロムライタ (GB Cart Flasher プロトコル) のドライバ
  actions.py      ビルド・エミュレーター起動・書き込みなど、CLI とエディタ共通の処理
  server.py       エディタの API (HTTP)。★ ネイティブアプリもこの処理をプロセス内で呼ぶ
  adopt.py        既存 GBDK フォルダの取り込み (project.gbproj を 1 つ足すだけ) と build.bat への追記
  browse.py       PC 内の GBDK プロジェクトの検索 (版ごとのフォルダをまとめる)
  versions.py     「_v1.9 → _v2.0」形式の版の作成 (フォルダのコピー + zip)
  stages.py       ステージエディタ (タイルマップ ↔ C 配列 / stage_data.c)
  carray.py       C ソース中の配列の読み取り・書き戻し (初期化子 {…} の中だけを書き換える)
  convert.py      画像 → タイル / スプライト / マップ / CGB パレット
  ide.py          Visual Studio / VS Code のプロジェクト生成と起動
  font.py jpfont.py gbjp/ fonts/   日本語フォントと gbjp ライブラリ
  engine/         engine 型のゲームエンジン (C)
  templates.py    新規プロジェクトのひな形
  editor/         ブラウザ版エディタ (index.html, app.js, tools.js, media.js, style.css)
  app/            ★ ネイティブアプリ (Qt)
    local.py      LocalApi: server.py の Handler を HTTP なしで直接呼ぶ (ソケットは開かない)
    main.py       メインウィンドウ (ドック・タブ・ツールバー・メニュー・ジョブの進み具合・状態の表示)
    home.py       スタート画面・既存フォルダ取り込みのダイアログ
    tree.py       左の「プロジェクト」ツリー
    core.py       テーマ・run_async (別スレッド実行)・画像変換・フォーム部品・Slider
    view.py       タブに開く画面の基本クラスと @register
    views/        code, texts, assets, settings, convert, carray, versions, scene, stage, media
  media/          ★ GB Media Studio
    imageproc.py  画像の調整・160x144 化・ディザ・DMG/CGB 減色・プリセット
    tiles.py      2bpp タイル化・統合
    encode.py     静止画・動画のエンコード (差分レコード)、転送量の見積もり upload_updates()
    video.py      動画の読み込み (ffmpeg / PyAV / Pillow)
    timeline.py   タイムライン → 再生イベント (明るさのトラック、演出)
    sim.py        再生ライブラリと同じ動きをする Python シミュレーター (プレビューはこれで作る)
    studio.py     .gbmedia の読み書き・変換キャッシュ・ビルド・ハードの制約の診断 (make_report)
    export.py     GBDK 用 C ソースの書き出し (63 段階のフェード表も作る)
    demo.py       単体 ROM プレビュー (デモ ROM のビルド)
    verify.py     PyBoy でデモ ROM とプレビューを 1 フレームずつ比べる
    api.py        /api/media/* (server.py から呼ばれる)
    runtime/      GBDK 用の再生ライブラリ gbmedia.c / gbmedia.h (ROM に入る C)
    samples/      サンプル素材 (パブリックドメイン / CC0) と sample.gbmedia
packaging/
  windows/        build_windows.py (インストーラー作成), installer.nsi, launcher.c, trim_qt.py, README.html
  build_app.py    macOS / Linux 用 (PyInstaller)
tests/            pytest (53 件)
download/         公開しているインストーラーと SHA256SUMS.txt (README のリンク先)
docs/             media_studio.md (Media Studio の詳しい説明と計測値)、スクリーンショット、この引き継ぎ書
TODO.md           これからやること
```

---

## 3. 守ること (重要)

### 既存のプロジェクトを壊さない
- 既存の GBDK フォルダの「取り込み」は `project.gbproj` を 1 つ足すだけ。ソースや build.bat はそのまま
- build.bat への追記 (`adopt.add_to_build_scripts`) は元のファイルを `.bak` に残す
- C 配列の書き戻し (`carray.write_back`) は初期化子 `{ … }` の中だけを変える。コメントや他のコードは触らない
- `stage_data.c` など、ユーザーが自分で作ったファイルは上書きしない
- GB Media Studio は取り込んだ素材を `media/sources/` にコピーして使い、元ファイルを書き換えない。保存のたびに `.gbmedia.bak` を残す。書き出しで作り直すのは出力先 (`gbmedia/`) だけ
- **KIVA GB STUDIO の `.kgbproj` / KIVA GB IMAGE CONVERTER** はリポジトリに無く形式が不明なので、**読まない・書かない**。連携は未実装

### 嘘をつかない
- 出せない FPS を「出せる」と表示しない。動画は「目標 FPS → 実際の FPS」と「画質を下げた枚数」を必ず出す
- プレビュー画像だけで「ROM で動いた」としない。確認は PyBoy の ROM 検証 (`media/verify.py`) で行う
- 未実装の機能を実装済みと書かない。確かめていないこと (例: Windows 実機での起動) は「未確認」と書く

### 2 つの画面は同じ処理を通す
- 新しい機能は **まず `server.py` (Media Studio は `media/api.py`) に API として作り**、
  ネイティブアプリ (`gbforge/app`) は `LocalApi` 経由でそれを呼ぶ。ブラウザ版も同じ API を使う
- ネイティブアプリ側に処理 (ファイルの読み書きなど) を直接書かない。結果が 2 つの画面でずれる原因になる

### GB Media Studio: プレビューと実機を一致させる
プレビュー (`media/sim.py`) と再生ライブラリ (`media/runtime/gbmedia.c`) とエンコーダー (`media/encode.py`) は
**同じ規則** で動く必要がある。どれかを変えたら他も直し、`tests/test_media.py` の ROM 比較を通すこと。
- レコード形式: `encode.py` 先頭と `gbmedia.c` 先頭のコメント
- 1 フレームの転送量 `BYTES_PER_UPDATE` = 初代 448 / CGB 等速 384 / CGB 倍速 768 バイト (`media/__init__.py`)
- 区間 1 つの手間 `RUN_COST` = 22 / 22 / 32 バイト分。区間を始めるのに RUN_COST が足りなければ次のフレーム
- 走査線の安全装置 `LY_STOP` = 128 / 128 / 136。表示部分 (LY < LY_STOP) に入った後だけ判定する
- フェード: 初代は `dmg_step` / `dmg_map` の表 (BGP 4 階調)、カラーは書き出し時に作る **63 段階 × 64 バイトのパレット表** を VBlank の始めに書くだけ
- 計算は前のフレームの終わり (`stage()`) に済ませ、VBlank の始めには **レジスタに書くだけ** (遅れると画面の上が古い色になる)
- これらの値は PyBoy の負荷試験で決めた (`docs/media_studio.md` の表)。上げる場合は必ず同じ試験をやり直す

---

## 4. 開発環境

```bash
pip install -e ".[test]"          # pillow, pyserial, numpy, PySide6-Essentials, pytest, pyboy
gbforge setup                     # GBDK-2020 を ~/.gbforge にダウンロード (または GBDK_HOME を設定)
```

- Linux で Qt を動かすには `libegl1 libgl1 libxkbcommon0 libfontconfig1` と日本語フォント (`fonts-noto-cjk`) が必要
- 画面の無い環境では `QT_QPA_PLATFORM=offscreen` (テストは自動で設定する)
- 動画の読み込みには ffmpeg (Windows: `winget install Gyan.FFmpeg`)。GPL のため同梱しない
- PyBoy は ROM のテストと「ROM 検証」に使う (完全版インストーラーには入っている)

### よく使うコマンド
```bash
gbforge app [プロジェクトのフォルダ]      # ネイティブアプリ
gbforge editor [フォルダ]                  # ブラウザ版
gbforge build MyGame / gbforge run MyGame / gbforge flash MyGame --port COM3
GBDK_HOME=<gbdk のフォルダ> pytest -q      # 全テスト (GBDK が無いと一部はスキップ)
```

### テスト (53 件)
| ファイル | 内容 |
|---|---|
| test_units.py / test_existing_project.py | プロジェクト・取り込み・ステージ・C 配列など |
| test_flasher.py / test_serial_pty.py | ロムライタの通信 (仮想デバイス・pty) |
| test_rom_pyboy.py / test_gbjp.py | 実際に ROM を作って PyBoy で遊ぶ・日本語表示 |
| test_toolchain.py | GBDK のダウンロード・検証 |
| test_media.py | Media Studio: 表と規則の一致、サンプルを初代/CGB 等速/CGB 倍速で ROM 化して全フレームがプレビューと一致すること |
| test_media_api.py | Media Studio の API (取り込み → 変換 → 書き出し → プロジェクトのビルド) |
| test_app.py | ネイティブアプリを画面なしで実際に操作 (マウス操作・保存・ビルド・Media Studio・ROM 検証) |

### スクリーンショットで確認する方法 (ネイティブアプリ)
`QT_QPA_PLATFORM=offscreen` で `MainWindow(LocalApi(path))` を作り、`win.open_tab(kind, name)` してから
`win.grab().save("x.png")`。tests/test_app.py の `window()` / `pump()` が参考になる。

---

## 5. Windows インストーラーの作り方

```bash
pip install pefile
python packaging/windows/build_windows.py                 # 完全版 (dist/GBForge-<版>-Setup.exe と zip)
python packaging/windows/build_windows.py --no-emulator   # 軽量版 (PyBoy なし)
```
- 必要: MinGW-w64 (`x86_64-w64-mingw32-gcc`, `windres`)、NSIS (`makensis`)、インターネット (公式 Windows 版 Python、PyPI の wheel、GBDK)
- `--wheels build/windows/cache/wheels` でダウンロード済みの wheel を使える
- ランチャーは同じ `launcher.c` から 2 つ作る
  - `GBForge.exe` (GUI。`pythonw -m gbforge app`。コンソール画面が出ない)
  - `GBForgeCLI.exe` (コンソール。IDE・コマンドプロンプト用。環境変数 `GBFORGE_LAUNCHER` はこちらを指す)
- Qt は `trim_qt.py` が **DLL の import テーブルを辿って** 必要な物だけ残す (219 MB → 44 MB)。足りない DLL があればビルドが止まる。
  ネイティブアプリで QtCore / QtGui / QtWidgets 以外のモジュールを使う場合は `trim_qt.py` の `ENTRY` に足すこと
- 公開の手順: `dist/` の Setup.exe 2 つを `download/` に置き、古い版は `git rm`、`download/SHA256SUMS.txt` を作り直し、README のダウンロード表を更新
- 版を上げる時は `gbforge/__init__.py` と `pyproject.toml` の両方

---

## 6. ハマりどころ (分かっていること)

- **SDCC**: 構造体の値渡しで内部エラー (「Unbalanced stack」) になることがある → 番号とオフセットを別々に渡す
- **SDCC**: `(uint16_t)cnt << 4` を 8 ビットで計算することがある → `x = cnt; x <<= 4;` と分ける
- **SDCC**: 普通の C の書き方だと遅い (1 色のフェード計算に数百クロック)。時間に厳しい所はアセンブリか ROM の表にした
- GBDK の `set_bkg_palette` は CGB 等速だと VBlank に収まらない → 専用の `pal_write` (gbmedia.c)
- `static` の変数・関数はリンカーの `.noi` に出ない。調べる時は `-Dstatic=` を付けてビルドすると全部出る
- PyBoy の CGB 倍速では、153 行目付近の LY の読み値が 136〜143 に見えることがあった → 安全装置は表示部分に入ってからだけ判定
- 調べるために C にコードを足すとタイミングが変わって現象が消えることがある → PyBoy の `hook_register` (PC フック) でコードを変えずに観察する
- この開発環境では wine が起動直後に落ちるので、Windows 版は実際に動かしての確認ができていない

---

## 7. これからやること (TODO.md と同じ)

- インストーラーの「更新」対応 (インストール済みなら「0.2.0 → 0.3.0 に更新」と表示し、ユーザーのデータに触れない)
- Windows 実機での 0.3.0 の起動確認 (Qt の DLL が足りているか)
- macOS / Linux のオールインワン zip の作り直し (`packaging/build_app.py` は Qt 対応済み)
- GB Media Studio
  - 実機での転送量の確認 (今は PyBoy の計測値に余裕を持たせた値)
  - `.kgbproj` / KIVA GB IMAGE CONVERTER との連携 (形式が分かれば)
  - カラーで画像を切り替える時に VRAM バンク 0/1 を交互に使い、暗転しなくても乱れないようにする
  - engine 型プロジェクトのシーンから Media Studio のシーンを呼ぶ
- ブラウザ版エディタは当面残す。ネイティブアプリで足りない機能があれば移す

---

## 8. 作業の進め方 (これまでの約束)

- 変更したらテストを実行し、結果をそのまま報告する (失敗を隠さない)
- コミットメッセージは日本語。1 行目に要点、その下に箇条書き
- オーナーの許可を得て、これまで `main` に直接 push してきた。プルリクエストは頼まれた時だけ
- 大きな変更の後は、画面のスクリーンショットを撮って見た目も確認する
- 公開物 (インストーラー) を更新したら README のダウンロード表と SHA256SUMS も更新する
