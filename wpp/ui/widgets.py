"""Small shared widgets: icon buttons, switches, the seek bar, list rows and popups."""
from PySide6.QtCore import QEvent, QPoint, QRect, QRectF, QSize, Qt, QTimer, Signal, Property, QPropertyAnimation
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (QAbstractButton, QApplication, QFrame, QHBoxLayout, QLabel, QSizePolicy, QSlider, QStyle,
                               QStyleOptionSlider, QToolButton, QVBoxLayout, QWidget)

from .. import icons
from ..theme import MONO_FONT, theme


def fmt_time(s):
    s = max(0, int(s or 0))
    h, m, x = s // 3600, s // 60 % 60, s % 60
    return f"{h}:{m:02d}:{x:02d}" if h else f"{m}:{x:02d}"


def fmt_size(b):
    if not b:
        return "—"
    for unit, d in (("ГБ", 1e9), ("МБ", 1e6), ("КБ", 1e3)):
        if b >= d:
            return f"{b / d:.2f} {unit}" if unit == "ГБ" else f"{b / d:.0f} {unit}"
    return f"{b:.0f} Б"


def label(text="", obj=None, wrap=False, sel=False):
    l = QLabel(text)
    if obj:
        l.setObjectName(obj)
    if wrap:
        l.setWordWrap(True)
    if sel:
        l.setTextInteractionFlags(Qt.TextSelectableByMouse)
    return l


def icon_button(name, tip="", size=18, checkable=False, obj=None):
    b = QToolButton()
    b.setCursor(Qt.PointingHandCursor)
    b.setToolTip(tip)
    b.setAccessibleName(tip)
    b.setCheckable(checkable)
    b.setAutoRaise(True)
    if obj:
        b.setObjectName(obj)
    icons.bind(b, name, size)
    return b


def push(text, name=None, primary=False):
    from PySide6.QtWidgets import QPushButton
    b = QPushButton(text)
    b.setCursor(Qt.PointingHandCursor)
    if primary:
        b.setObjectName("primary")
    if name:
        icons.bind(b, name, 16, "inv_fg" if primary else "fg")
    return b


class Switch(QAbstractButton):
    def __init__(self, checked=False, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setChecked(checked)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(40, 22)
        self._x = 1.0 if checked else 0.0
        self._anim = QPropertyAnimation(self, b"knob", self)
        self._anim.setDuration(120)
        self.toggled.connect(self._go)

    def _go(self, on):
        self._anim.stop()
        self._anim.setEndValue(1.0 if on else 0.0)
        self._anim.start()

    def get_knob(self):
        return self._x

    def set_knob(self, v):
        self._x = v
        self.update()

    knob = Property(float, get_knob, set_knob)

    def sizeHint(self):
        return QSize(40, 22)

    def paintEvent(self, _):
        from PySide6.QtCore import QAbstractAnimation
        if self._anim.state() != QAbstractAnimation.Running:
            # setChecked() with blocked signals doesn't animate: draw the real state, not a stale knob position
            self._x = 1.0 if self.isChecked() else 0.0
        # Windows 11 toggle: off = outlined track with a dark knob, on = accent fill with a light knob
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        x = self._x
        track = QRectF(0.5, 1.5, 39, 19)
        if x < 0.5:
            p.setPen(QPen(theme.c("toggle_off"), 1))
            p.setBrush(theme.c("field"))
            p.drawRoundedRect(track, 9.5, 9.5)
            knob, d = theme.c("toggle_off" if theme.dark else "muted"), 12
        else:
            fill = theme.c("accent")
            if not self.isEnabled():
                fill.setAlpha(110)
            p.setPen(Qt.NoPen)
            p.setBrush(fill)
            p.drawRoundedRect(track, 9.5, 9.5)
            knob, d = theme.c("on_accent"), 12
        p.setPen(Qt.NoPen)
        p.setBrush(knob)
        cx = 10 + 20 * x
        p.drawEllipse(QRectF(cx - d / 2, 11 - d / 2, d, d))


def knob(p, cx, cy, color):
    """Windows 11 slider thumb: accent dot in a light ring with a hairline edge."""
    p.setPen(QPen(theme.c("field_line"), 1))
    p.setBrush(theme.c("raise_"))
    p.drawEllipse(QRectF(cx - 9.5, cy - 9.5, 19, 19))
    p.setPen(Qt.NoPen)
    p.setBrush(color)
    p.drawEllipse(QRectF(cx - 5, cy - 5, 10, 10))


class ThinSlider(QSlider):
    """QSlider with its own painting: 4px track, filled part, round knob. Behaviour stays the stock QSlider."""
    def __init__(self, orientation, parent=None):
        super().__init__(orientation, parent)
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumSize(22, 22)

    def paintEvent(self, _):
        opt = QStyleOptionSlider()
        self.initStyleOption(opt)
        hr = self.style().subControlRect(QStyle.CC_Slider, opt, QStyle.SC_SliderHandle, self)
        c = hr.center()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        fg = theme.c("accent" if self.isEnabled() else "rail")
        if self.orientation() == Qt.Horizontal:
            y = self.height() / 2 - 2
            p.setBrush(theme.c("rail"))
            p.drawRoundedRect(QRectF(2, y, self.width() - 4, 4), 2, 2)
            p.setBrush(fg)
            p.drawRoundedRect(QRectF(2, y, max(0, c.x() - 2), 4), 2, 2)
        else:
            x = self.width() / 2 - 2
            p.setBrush(theme.c("rail"))
            p.drawRoundedRect(QRectF(x, 2, 4, self.height() - 4), 2, 2)
            p.setBrush(fg)
            p.drawRoundedRect(QRectF(x, c.y(), 4, max(0, self.height() - 2 - c.y())), 2, 2)
        knob(p, c.x(), c.y(), fg)


class SeekBar(QWidget):
    """Timeline with buffered range, chapter ticks and a hover time tooltip."""
    seek = Signal(float)
    scrub = Signal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)
        self.setFixedHeight(22)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setAccessibleName("Позиция воспроизведения")
        self.dur = 0.0
        self.pos = 0.0
        self.buf = 0.0
        self.chapters = []
        self.hover = None
        self.drag = False

    def set_state(self, pos=None, dur=None, buf=None):
        if self.drag and not (QApplication.mouseButtons() & Qt.LeftButton):
            self.drag = False       # the release was lost (window resized/moved under the pointer): unstick
        if pos is not None and not self.drag:
            self.pos = pos
        if dur is not None:
            self.dur = dur
        if buf is not None:
            self.buf = buf
        self.update()

    def _x2t(self, x):
        w = max(1, self.width() - 20)
        return max(0.0, min(1.0, (x - 10) / w)) * self.dur

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton and self.dur:
            self.drag = True
            self.pos = self._x2t(e.position().x())
            self.scrub.emit(self.pos)
            self.update()

    def mouseMoveEvent(self, e):
        self.hover = e.position().x()
        if self.drag:
            self.pos = self._x2t(self.hover)
            self.scrub.emit(self.pos)
        self.update()

    def mouseReleaseEvent(self, e):
        if self.drag:
            self.drag = False
            self.seek.emit(self._x2t(e.position().x()))

    def leaveEvent(self, _):
        self.hover = None
        self.update()

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key_Left, Qt.Key_Right) and self.dur:
            self.seek.emit(max(0, min(self.dur, self.pos + (5 if e.key() == Qt.Key_Right else -5))))
        else:
            super().keyPressEvent(e)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w = self.width() - 12
        y = self.height() / 2 - 2
        p.setPen(Qt.NoPen)
        p.setBrush(theme.c("rail"))
        p.drawRoundedRect(QRectF(10, y, w - 8, 4), 2, 2)
        if self.dur:
            f = min(1.0, self.pos / self.dur)
            p.setBrush(theme.c("accent"))
            p.drawRoundedRect(QRectF(10, y, (w - 8) * f, 4), 2, 2)
            p.setBrush(theme.c("bg"))
            for t in self.chapters:
                if 0 < t < self.dur:
                    p.drawRect(QRectF(10 + (w - 8) * t / self.dur - 1, y, 2, 4))
            if self.hover is not None or self.drag:
                knob(p, 10 + (w - 8) * f, y + 2, theme.c("accent"))
        if self.hover is not None and self.dur:
            t = fmt_time(self._x2t(self.hover))
            self.setToolTip(t)


class Row(QFrame):
    """One line in a bordered list."""
    def __init__(self, first=False):
        super().__init__()
        self.setObjectName("row")
        self.setProperty("first", "true" if first else "false")
        self.h = QHBoxLayout(self)
        self.h.setContentsMargins(12, 8, 8, 8)
        self.h.setSpacing(10)


class ListBox(QFrame):
    def __init__(self):
        super().__init__()
        self.setObjectName("list")
        self.v = QVBoxLayout(self)
        self.v.setContentsMargins(0, 0, 0, 0)
        self.v.setSpacing(0)

    def clear(self):
        while self.v.count():
            w = self.v.takeAt(0).widget()
            if w:
                w.deleteLater()

    def add(self, row):
        row.setProperty("first", "true" if self.v.count() == 0 else "false")
        self.v.addWidget(row)
        return row

    def empty(self, title, desc=""):
        r = Row(first=True)
        r.h.setContentsMargins(14, 12, 14, 12)
        box = QVBoxLayout()
        box.setSpacing(2)
        box.addWidget(label(title, "meta"))
        if desc:            # only factual messages (an error's reason), never explanations
            box.addWidget(label(desc, "meta", wrap=True))
        r.h.addLayout(box, 1)
        self.add(r)


def text_block(name, meta):
    box = QVBoxLayout()
    box.setSpacing(1)
    n = label(name, "name")
    n.setMinimumWidth(40)
    n.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
    m = label(meta, "meta")
    m.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
    box.addWidget(n)
    box.addWidget(m)
    return box, n, m


class Popup(QFrame):
    """Frameless popup panel anchored above a button."""
    def __init__(self, parent=None):
        super().__init__(parent, Qt.Popup | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.card = QFrame()
        self.card.setObjectName("popup")
        outer.addWidget(self.card)
        self.v = QVBoxLayout(self.card)
        self.v.setContentsMargins(12, 10, 12, 12)
        self.v.setSpacing(8)

    def show_above(self, anchor: QWidget):
        self.adjustSize()
        g = anchor.mapToGlobal(QPoint(anchor.width(), 0))
        scr = anchor.screen().availableGeometry()
        x = max(scr.left() + 8, min(scr.right() - self.width() - 8, g.x() - self.width()))
        y = g.y() - self.height() - 8
        if y < scr.top():
            y = anchor.mapToGlobal(QPoint(0, anchor.height())).y() + 8
        self.move(x, y)
        self.show()


class Toast(QWidget):
    """Short message near the bottom of the main window (a top-level window so it shows over video)."""
    def __init__(self, host: QWidget):
        super().__init__(None, Qt.ToolTip | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.NoDropShadowWindowHint)
        self.host = host
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        fr = QFrame()
        fr.setObjectName("toast")
        fl = QHBoxLayout(fr)
        fl.setContentsMargins(16, 9, 16, 9)
        self.text = QLabel()
        fl.addWidget(self.text)
        lay.addWidget(fr)
        self.timer = QTimer(self, singleShot=True, timeout=self.hide)

    def show_text(self, text, ms=2400):
        self.text.setText(text)
        self.adjustSize()
        g = self.host.geometry()
        self.move(g.center().x() - self.width() // 2, g.bottom() - self.height() - 110)
        self.show()
        self.raise_()
        self.timer.start(ms)
