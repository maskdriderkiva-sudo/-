"""配布版 GBForge の起動スクリプト (PyInstaller のエントリポイント)。

引数なしでダブルクリック → エディタ (ネイティブアプリ) のスタート画面が開く (PySide6 が無ければブラウザ版)。
"GBForge build MyGame" のように CLI としても使える。
"""
import multiprocessing
import sys

from gbforge.cli import main

if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.exit(main())
