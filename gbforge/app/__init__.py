"""GBForge ネイティブアプリ (Qt / PySide6)。

画面はすべて Qt のウィジェットで作り、処理 (ビルド・ロムライタ・日本語フォント・GB Media Studio など) は
エディタサーバーと同じ処理をプロセス内で直接呼び出す (gbforge/app/local.py)。HTTP サーバーやブラウザは使わない。

    gbforge app [プロジェクトのフォルダ]
"""
