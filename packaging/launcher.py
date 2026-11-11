"""配布版 GBForge の起動スクリプト (PyInstaller のエントリポイント)。

引数なしでダブルクリック → エディタ (スタート画面) がブラウザで開く。
"GBForge build MyGame" のように CLI としても使える。
"""
import multiprocessing
import sys

from gbforge.cli import main

if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.exit(main())
