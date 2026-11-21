"""タブに開く画面 (ビュー) の基本クラスと登録。"""
from __future__ import annotations

from PySide6.QtWidgets import QWidget

VIEWS: dict[str, type] = {}


def register(kind: str):
    def deco(cls):
        cls.kind = kind
        VIEWS[kind] = cls
        return cls
    return deco


class View(QWidget):
    kind = ""
    icon = "📄"

    def __init__(self, win, name: str):
        super().__init__()
        self.win = win
        self.api = win.api
        self.name = name

    # ---- 必要に応じて上書き ----
    def title(self) -> str:
        return self.name

    def load(self):
        """開いた時・再読み込みの時に呼ばれる"""

    def is_dirty(self) -> bool:
        return False

    def save(self, quiet: bool = True):
        """未保存の変更を保存する"""

    def inspector(self) -> QWidget | None:
        """右のインスペクターに出す内容 (毎回作り直す)"""
        return None

    def hierarchy(self) -> list:
        """ヒエラルキーの項目 [(表示, 選択中か, クリック時の関数)]"""
        return []

    def hierarchy_title(self) -> str:
        return ""

    def closing(self):
        """タブを閉じる直前"""

    # ---- 便利 ----
    def log(self, msg: str, cls: str = ""):
        self.win.log(msg, cls)

    def changed(self):
        self.win.update_save_state()

    def refresh_inspector(self):
        self.win.refresh_inspector()

    def refresh_hierarchy(self):
        self.win.refresh_hierarchy()
