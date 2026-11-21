"""C ソース・スクリプトのエディタ (C の色分け付き)。"""
from __future__ import annotations

import re

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QKeySequence, QShortcut, QSyntaxHighlighter, QTextCharFormat
from PySide6.QtWidgets import QPlainTextEdit, QSplitter, QToolBar, QVBoxLayout, QWidget

from .. import core
from ..core import button, hint, label
from ..local import q
from ..view import View, register

KEYWORDS = ("auto break case char const continue default do double else enum extern float for goto if inline int long "
            "register return short signed sizeof static struct switch typedef union unsigned void volatile while "
            "uint8_t int8_t uint16_t int16_t uint32_t int32_t bool true false NULL BANKREF BANK").split()


def _fmt(color: str, bold: bool = False, italic: bool = False) -> QTextCharFormat:
    f = QTextCharFormat()
    f.setForeground(QColor(color))
    if bold:
        f.setFontWeight(QFont.Bold)
    f.setFontItalic(italic)
    return f


class CHighlighter(QSyntaxHighlighter):
    def __init__(self, doc):
        super().__init__(doc)
        self.rules = [
            (re.compile(r"\b(" + "|".join(KEYWORDS) + r")\b"), _fmt("#569cd6", True)),
            (re.compile(r"^\s*#\s*\w+.*$"), _fmt("#c586c0")),
            (re.compile(r"\b0[xX][0-9A-Fa-f]+\b|\b\d+\b"), _fmt("#b5cea8")),
            (re.compile(r"\b[A-Z_][A-Z0-9_]{2,}\b"), _fmt("#4ec9b0")),
            (re.compile(r"\b\w+(?=\s*\()"), _fmt("#dcdcaa")),
            (re.compile(r'"([^"\\]|\\.)*"|\'([^\'\\]|\\.)*\''), _fmt("#ce9178")),
            (re.compile(r"//.*$"), _fmt("#6a9955", italic=True)),
        ]
        self.comment = _fmt("#6a9955", italic=True)

    def highlightBlock(self, text: str):
        for rx, fmt in self.rules:
            for m in rx.finditer(text):
                self.setFormat(m.start(), m.end() - m.start(), fmt)
        # /* */ (複数行)
        self.setCurrentBlockState(0)
        start = 0 if self.previousBlockState() == 1 else text.find("/*")
        while start >= 0:
            end = text.find("*/", start + (0 if self.previousBlockState() == 1 and start == 0 else 2))
            if end < 0:
                self.setCurrentBlockState(1)
                self.setFormat(start, len(text) - start, self.comment)
                break
            self.setFormat(start, end + 2 - start, self.comment)
            start = text.find("/*", end + 2)


class CodeEdit(QPlainTextEdit):
    def __init__(self):
        super().__init__()
        self.setFont(core.mono_font())
        self.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.setTabStopDistance(self.fontMetrics().horizontalAdvance(" ") * 4)
        self.hl = CHighlighter(self.document())

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Tab and not e.modifiers():
            self.insertPlainText("    ")
            return
        if e.key() in (Qt.Key_Return, Qt.Key_Enter):     # 自動インデント
            cur = self.textCursor()
            line = cur.block().text()
            ind = re.match(r"\s*", line).group(0)
            if line.rstrip().endswith("{"):
                ind += "    "
            super().keyPressEvent(e)
            self.insertPlainText(ind)
            return
        super().keyPressEvent(e)


@register("file")
class FileView(View):
    icon = "📄"

    def __init__(self, win, name):
        super().__init__(win, name)
        self.dirty = False
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        tb = QToolBar()
        self.lbl = label("", "title", wrap=False)
        tb.addWidget(self.lbl)
        tb.addWidget(button("保存 (Ctrl+S)", lambda: self.save(False)))
        tb.addWidget(button("</> IDE で開く", lambda: self.win.open_ide(self.ide_path())))
        tb.addWidget(button("API リファレンス", self.toggle_ref))
        self.hint_lbl = label("", "hint", wrap=False)
        tb.addWidget(self.hint_lbl)
        lay.addWidget(tb)
        self.split = QSplitter()
        self.edit = CodeEdit()
        self.edit.textChanged.connect(self._edited)
        self.ref = QPlainTextEdit()
        self.ref.setReadOnly(True)
        self.ref.setFont(core.mono_font())
        self.ref.hide()
        self.split.addWidget(self.edit)
        self.split.addWidget(self.ref)
        lay.addWidget(self.split, 1)
        self._loading = False

    def title(self):
        return self.name.split("/")[-1]

    def ide_path(self):
        return self.name

    def url(self):
        return "/api/file?path=" + q(self.name)

    def load(self):
        r = self.api.call("GET", self.url())
        self._loading = True
        self.edit.setPlainText(r["code"])
        self._loading = False
        self.dirty = False
        self.lbl.setText(self.name + "  ")

    def _edited(self):
        if not self._loading and not self.dirty:
            self.dirty = True
            self.changed()

    def is_dirty(self):
        return self.dirty

    def save(self, quiet=True):
        self.api.call("PUT", self.url(), {"code": self.edit.toPlainText()})
        self.dirty = False
        if not quiet:
            self.log(f"{self.name} を保存しました", "o")
        self.changed()

    def toggle_ref(self):
        if not self.ref.toPlainText():
            self.ref.setPlainText(self.api.get("/api/api_reference")["header"])
        self.ref.setVisible(not self.ref.isVisible())

    def inspector(self):
        return core.vbox(
            core.group("📄 " + self.name, button("</> Visual Studio / VS Code で開く", lambda: self.win.open_ide(self.name)),
                       hint("IDE 用の設定 (.sln / .vscode) は GBDK と gbjp のインクルードパス込みで自動生成されます。"
                            "IDE からのビルド (Ctrl+Shift+B / Ctrl+B) や実行 (F5) も GBForge を呼び出します。")),
            core.group("日本語 (gbjp)", label("jp_init(128, 128);\njp_print(x, y, \"日本語\");\njp_print_box(x, y, w, h, \"長文…\");\n"
                                               "jp_print_int(x, y, n);\njp_set_palette(p);  // GBC\njp_set_layer(JP_WIN);\njp_set_speed(2);", "hint"),
                       hint("ソースに書いた文字だけがフォントとして ROM に入ります (設定で全文字モードも可)。")))


@register("script")
class ScriptView(FileView):
    icon = "📜"

    def title(self):
        return self.name + ".c"

    def ide_path(self):
        return f"scripts/{self.name}.c"

    def url(self):
        return "/api/script/" + q(self.name)

    def load(self):
        super().load()
        self.lbl.setText(self.name + ".c  ")
        self.hint_lbl.setText("関数名: 名前_start / 名前_update / 名前_interact")

    def save(self, quiet=True):
        self.api.put(self.url(), {"code": self.edit.toPlainText()})
        self.dirty = False
        if not quiet:
            self.log(f"{self.name}.c を保存しました", "o")
        self.changed()

    def inspector(self):
        return hint("スクリプトはアクターまたはシーンのインスペクターで割り当てます。関数 名前_start / 名前_update / 名前_interact "
                    "を定義すると自動で呼ばれます。")
