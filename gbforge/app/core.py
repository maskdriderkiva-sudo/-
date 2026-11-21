"""ネイティブアプリの共通部品: テーマ、バックグラウンド処理、画像変換、フォーム。"""
from __future__ import annotations

import io
import traceback
from typing import Any, Callable

from PIL import Image
from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, Signal
from PySide6.QtGui import QColor, QFont, QImage, QPalette, QPixmap
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QFrame, QHBoxLayout, QLabel,
                               QLineEdit, QMessageBox, QPushButton, QSizePolicy, QSlider, QSpinBox, QVBoxLayout,
                               QWidget)

GB_GREEN = [(224, 248, 208), (136, 192, 112), (52, 104, 86), (8, 24, 32)]
GB_GRAY = [(255, 255, 255), (170, 170, 170), (85, 85, 85), (0, 0, 0)]

COLORS = {
    "bg": "#1e1f22", "panel": "#2b2d31", "panel2": "#232428", "border": "#3a3c42", "text": "#dcdde1",
    "muted": "#8b8e96", "accent": "#4c8bf5", "accent2": "#2f6fd8", "ok": "#3fb950", "warn": "#d29922", "err": "#f85149",
}

STYLE = """
QWidget { color: %(text)s; font-size: 10pt; }
QMainWindow, QDialog { background: %(bg)s; }
QDockWidget { titlebar-close-icon: none; }
QDockWidget::title { background: %(panel2)s; padding: 4px 8px; font-weight: bold; }
QToolBar { background: #111214; border-bottom: 1px solid %(border)s; spacing: 6px; padding: 4px; }
QPushButton { background: #3a3d44; border: 1px solid %(border)s; border-radius: 4px; padding: 4px 10px; }
QPushButton:hover { background: #474a52; }
QPushButton:checked { background: %(accent2)s; border-color: %(accent)s; }
QPushButton:disabled { color: #666; }
QPushButton[role="play"] { background: #2d6a3a; border-color: #3fb950; }
QPushButton[role="play"]:hover { background: #36804a; }
QPushButton[role="flash"] { background: #6a4b2d; border-color: #d29922; }
QPushButton[role="danger"] { color: #ffb4ae; }
QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QDoubleSpinBox, QComboBox, QListWidget, QTreeWidget, QTableWidget {
    background: %(panel2)s; border: 1px solid %(border)s; border-radius: 4px; padding: 2px 4px;
    selection-background-color: %(accent2)s; }
QTreeWidget, QListWidget { padding: 0; }
QTreeWidget::item, QListWidget::item { padding: 3px 2px; }
QTreeWidget::item:selected, QListWidget::item:selected { background: %(accent2)s; color: white; }
QTabWidget::pane { border: none; }
QTabBar::tab { background: %(panel2)s; color: %(muted)s; padding: 6px 12px; border-right: 1px solid %(border)s; }
QTabBar::tab:selected { background: %(bg)s; color: %(text)s; }
QGroupBox { border: 1px solid %(border)s; border-radius: 6px; margin-top: 14px; padding-top: 6px; }
QGroupBox::title { subcontrol-origin: margin; left: 8px; padding: 0 4px; color: %(text)s; font-weight: bold; }
QToolTip { background: #111214; color: %(text)s; border: 1px solid %(border)s; padding: 4px; }
QScrollArea { border: none; }
QLabel[role="muted"] { color: %(muted)s; }
QLabel[role="hint"] { color: %(muted)s; font-size: 9pt; }
QLabel[role="warn"] { color: %(warn)s; }
QLabel[role="err"] { color: %(err)s; }
QLabel[role="ok"] { color: %(ok)s; }
QLabel[role="title"] { font-weight: bold; font-size: 11pt; }
QLabel[role="chip-ok"] { border: 1px solid #2e6b3a; color: #8ee69f; border-radius: 10px; padding: 2px 8px; }
QLabel[role="chip-ng"] { border: 1px solid #7a2e2e; color: #ffb4ae; border-radius: 10px; padding: 2px 8px; }
QLabel[role="chip-idle"] { border: 1px solid %(border)s; color: %(muted)s; border-radius: 10px; padding: 2px 8px; }
QProgressBar { border: 1px solid %(border)s; border-radius: 4px; background: %(panel2)s; text-align: center; }
QProgressBar::chunk { background: %(accent)s; }
""" % COLORS


def apply_theme(app):
    app.setStyle("Fusion")
    pal = QPalette()
    for role, col in ((QPalette.Window, COLORS["bg"]), (QPalette.WindowText, COLORS["text"]),
                      (QPalette.Base, COLORS["panel2"]), (QPalette.AlternateBase, COLORS["panel"]),
                      (QPalette.Text, COLORS["text"]), (QPalette.Button, "#3a3d44"), (QPalette.ButtonText, COLORS["text"]),
                      (QPalette.Highlight, COLORS["accent2"]), (QPalette.HighlightedText, "#ffffff"),
                      (QPalette.ToolTipBase, "#111214"), (QPalette.ToolTipText, COLORS["text"]),
                      (QPalette.PlaceholderText, COLORS["muted"])):
        pal.setColor(role, QColor(col))
    app.setPalette(pal)
    f = app.font()
    for fam in ("Yu Gothic UI", "Meiryo", "Hiragino Sans", "Noto Sans CJK JP", "Noto Sans JP"):
        if fam in __import__("PySide6.QtGui", fromlist=["QFontDatabase"]).QFontDatabase.families():
            f.setFamily(fam)
            break
    app.setFont(f)
    app.setStyleSheet(STYLE)


def mono_font() -> QFont:
    f = QFont("Cascadia Code")
    f.setStyleHint(QFont.Monospace)
    f.setFamilies(["Cascadia Code", "Consolas", "Noto Sans Mono", "DejaVu Sans Mono", "monospace"])
    return f


# ---------------------------------------------------------------------------
# バックグラウンド処理 (画面を止めない)
# ---------------------------------------------------------------------------
class _Signals(QObject):
    done = Signal(object)
    error = Signal(object)


class _Task(QRunnable):
    def __init__(self, fn, args, kwargs):
        super().__init__()
        self.fn, self.args, self.kwargs = fn, args, kwargs
        self.sig = _Signals()

    def run(self):
        try:
            r = self.fn(*self.args, **self.kwargs)
        except Exception as e:  # noqa: BLE001
            e.trace = traceback.format_exc()
            self._emit(self.sig.error, e)
            return
        self._emit(self.sig.done, r)

    @staticmethod
    def _emit(signal, value):
        try:
            signal.emit(value)
        except RuntimeError:     # 画面 (受け取り側) がもう閉じられている
            pass


_keep: set = set()


def run_async(fn: Callable, *args, on_done: Callable[[Any], None] | None = None,
              on_error: Callable[[Exception], None] | None = None, **kwargs):
    """fn を別スレッドで実行し、結果を画面のスレッドでコールバックする。"""
    t = _Task(fn, args, kwargs)
    holder = t.sig
    _keep.add(holder)

    def fin(*_):
        _keep.discard(holder)
    if on_done:
        t.sig.done.connect(on_done)
    if on_error:
        t.sig.error.connect(on_error)
    t.sig.done.connect(fin)
    t.sig.error.connect(fin)
    QThreadPool.globalInstance().start(t)


# ---------------------------------------------------------------------------
# 画像
# ---------------------------------------------------------------------------
def pil_to_qimage(im: Image.Image) -> QImage:
    im = im.convert("RGBA")
    data = im.tobytes("raw", "RGBA")
    q = QImage(data, im.width, im.height, im.width * 4, QImage.Format_RGBA8888)
    return q.copy()


def png_to_pixmap(data: bytes) -> QPixmap:
    pm = QPixmap()
    pm.loadFromData(data)
    return pm


def array_to_qimage(a) -> QImage:
    """numpy (H, W, 3) uint8 → QImage"""
    import numpy as np
    a = np.ascontiguousarray(a.astype(np.uint8))
    h, w = a.shape[:2]
    return QImage(a.data, w, h, w * 3, QImage.Format_RGB888).copy()


def shade_of(r: int, g: int, b: int) -> int:
    y = 0.299 * r + 0.587 * g + 0.114 * b
    return 0 if y >= 192 else 1 if y >= 128 else 2 if y >= 64 else 3


def gb_image(data: bytes, kind: str, bgp: int = 0xE4) -> QImage:
    """PNG をゲームボーイの 4 階調の見た目にした QImage (kind: bg / sprite)。ブラウザ版の gbCanvas と同じ変換。"""
    im = Image.open(io.BytesIO(data)).convert("RGBA")
    px = im.load()
    out = Image.new("RGBA", im.size)
    po = out.load()
    for y in range(im.height):
        for x in range(im.width):
            r, g, b, a = px[x, y]
            s = shade_of(r, g, b)
            if kind == "sprite":
                if a < 128:
                    po[x, y] = (0, 0, 0, 0)
                    continue
                s = (0, 2, 2, 3)[s]
            else:
                s = (bgp >> (s * 2)) & 3
            po[x, y] = GB_GREEN[s] + (255,)
    return pil_to_qimage(out)


# ---------------------------------------------------------------------------
# フォーム部品 (ラベルにツールチップ)
# ---------------------------------------------------------------------------
def label(text: str, role: str | None = None, wrap: bool = True) -> QLabel:
    lb = QLabel(text)
    if role:
        lb.setProperty("role", role)
    lb.setWordWrap(wrap)
    lb.setTextInteractionFlags(Qt.TextSelectableByMouse)
    return lb


def hint(text: str) -> QLabel:
    return label(text, "hint")


def button(text: str, on_click=None, role: str | None = None, tip: str | None = None) -> QPushButton:
    b = QPushButton(text)
    if role:
        b.setProperty("role", role)
    if tip:
        b.setToolTip(tip)
    if on_click:
        b.clicked.connect(lambda *_: on_click())
    return b


def combo(options, value=None, on_change=None, none_label: str | None = None) -> QComboBox:
    """options: [値] か [(値, 表示)]"""
    c = QComboBox()
    if none_label is not None:
        c.addItem(none_label, None)
    for o in options:
        v, t = (o if isinstance(o, (tuple, list)) else (o, str(o)))
        c.addItem(str(t), v)
    i = c.findData(value)
    if i >= 0:
        c.setCurrentIndex(i)
    if on_change:
        c.currentIndexChanged.connect(lambda *_: on_change(c.currentData()))
    return c


def spin(value: int, lo: int, hi: int, on_change=None, step: int = 1) -> QSpinBox:
    s = QSpinBox()
    s.setRange(lo, hi)
    s.setSingleStep(step)
    s.setValue(int(value or 0))
    if on_change:
        s.editingFinished.connect(lambda: on_change(s.value()))
    return s


def dspin(value: float, lo: float, hi: float, step: float, on_change=None, decimals: int = 2) -> QDoubleSpinBox:
    s = QDoubleSpinBox()
    s.setRange(lo, hi)
    s.setDecimals(decimals)
    s.setSingleStep(step)
    s.setValue(float(value or 0))
    if on_change:
        s.editingFinished.connect(lambda: on_change(s.value()))
    return s


def check(value: bool, on_change=None, text: str = "") -> QCheckBox:
    c = QCheckBox(text)
    c.setChecked(bool(value))
    if on_change:
        c.toggled.connect(on_change)
    return c


def line(value: str = "", on_change=None, placeholder: str = "", mono: bool = False) -> QLineEdit:
    e = QLineEdit(value or "")
    if placeholder:
        e.setPlaceholderText(placeholder)
    if mono:
        e.setFont(mono_font())
    if on_change:
        e.editingFinished.connect(lambda: on_change(e.text()))
    return e


class Slider(QWidget):
    """スライダー + 値の表示。live=True はドラッグ中、False は離した時。"""

    def __init__(self, value: float, lo: float, hi: float, step: float, on_change=None, fmt=None):
        super().__init__()
        self.lo, self.step, self.fmt = lo, step, fmt or (lambda v: f"{v:g}")
        self.s = QSlider(Qt.Horizontal)
        self.s.setRange(0, int(round((hi - lo) / step)))
        self.s.setValue(int(round((float(value) - lo) / step)))
        self.v = QLabel(self.fmt(self.value()))
        self.v.setMinimumWidth(44)
        self.v.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.s, 1)
        lay.addWidget(self.v)
        self.on_change = on_change
        self.s.valueChanged.connect(self._moved)
        self.s.sliderReleased.connect(lambda: self._fire(False))

    def value(self) -> float:
        v = self.lo + self.s.value() * self.step
        return round(v, 6) if isinstance(self.step, float) else int(v)

    def _moved(self, *_):
        self.v.setText(self.fmt(self.value()))
        if self.s.isSliderDown():
            self._fire(True)
        else:
            self._fire(False)     # キーボード・クリックで動かした

    def _fire(self, live: bool):
        if self.on_change:
            self.on_change(self.value(), live)


def form(*rows) -> QWidget:
    """rows: (ラベル, ウィジェット, ツールチップ) / ウィジェット"""
    w = QWidget()
    f = QFormLayout(w)
    f.setContentsMargins(0, 0, 0, 0)
    f.setLabelAlignment(Qt.AlignLeft)
    f.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
    for r in rows:
        if r is None:
            continue
        if isinstance(r, QWidget):
            f.addRow(r)
            continue
        text, wid, tip = (list(r) + [None])[:3]
        lb = QLabel(text)
        lb.setProperty("role", "muted")
        if tip:
            lb.setToolTip(tip)
            wid.setToolTip(tip)
        f.addRow(lb, wid)
    return w


def hbox(*widgets, stretch_last: bool = False) -> QWidget:
    w = QWidget()
    lay = QHBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    for x in widgets:
        if x == "stretch":
            lay.addStretch(1)
        elif isinstance(x, str):
            lay.addWidget(QLabel(x))
        elif x is not None:
            lay.addWidget(x)
    if stretch_last:
        lay.addStretch(1)
    return w


def vbox(*widgets, margins: int = 0, spacing: int = 6) -> QWidget:
    w = QWidget()
    lay = QVBoxLayout(w)
    lay.setContentsMargins(margins, margins, margins, margins)
    lay.setSpacing(spacing)
    for x in widgets:
        if x == "stretch":
            lay.addStretch(1)
        elif x is not None:
            lay.addWidget(x)
    return w


def group(title: str, *widgets) -> QWidget:
    from PySide6.QtWidgets import QGroupBox
    g = QGroupBox(title)
    lay = QVBoxLayout(g)
    lay.setSpacing(6)
    for x in widgets:
        if x is not None:
            lay.addWidget(x)
    return g


def separator() -> QFrame:
    f = QFrame()
    f.setFrameShape(QFrame.HLine)
    f.setStyleSheet(f"color: {COLORS['border']};")
    return f


def ask(parent, title: str, text: str) -> bool:
    return QMessageBox.question(parent, title, text, QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes


def info(parent, title: str, text: str):
    QMessageBox.information(parent, title, text)


def error(parent, text: str):
    QMessageBox.warning(parent, "GBForge", text)


def expanding(w: QWidget) -> QWidget:
    w.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
    return w
