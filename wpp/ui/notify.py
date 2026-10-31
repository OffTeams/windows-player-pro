"""Desktop notification card (bottom-right of the screen) and the clipboard link watcher.

Only one card is on screen at a time: a new one takes the old one's place.
The watcher probes a copied link silently and shows a card only when the page really has media.
"""
import html
import threading
import time

import requests
from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QGuiApplication, QImage, QPixmap
from PySide6.QtWidgets import (QComboBox, QFrame, QGraphicsDropShadowEffect, QGridLayout, QHBoxLayout, QLabel,
                               QVBoxLayout, QWidget)

from .. import icons
from ..config import cfg
from ..net.common import UA, find_url
from ..net.probe import Prober, normalize
from ..theme import theme
from .widgets import fmt_size, fmt_time, icon_button, label, push

THUMB_W, THUMB_H = 104, 58


class Note(QWidget):
    closed = Signal(object)
    _thumb_ready = Signal(bytes)

    def __init__(self, center, on_top=True):
        flags = Qt.Tool | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint
        if on_top:
            flags |= Qt.WindowStaysOnTopHint
        super().__init__(None, flags)
        self.center = center
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setFixedWidth(400)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 12)
        self.card = QFrame()
        self.card.setObjectName("note")
        sh = QGraphicsDropShadowEffect(self.card)
        sh.setBlurRadius(24)
        sh.setOffset(0, 4)
        sh.setColor(Qt.black if theme.dark else Qt.gray)
        self.card.setGraphicsEffect(sh)
        outer.addWidget(self.card)
        g = QGridLayout(self.card)
        g.setContentsMargins(12, 10, 6, 12)
        g.setHorizontalSpacing(10)
        g.setVerticalSpacing(8)
        # row 0: picture (preview or icon) | title + one line of details | close
        self.ic = QLabel()
        self.ic.setAlignment(Qt.AlignTop)
        self.thumb = QLabel()
        self.thumb.setObjectName("thumb")
        self.thumb.setFixedSize(THUMB_W, THUMB_H)
        self.thumb.setAlignment(Qt.AlignCenter)
        self.thumb.hide()
        pic = QHBoxLayout()
        pic.setSpacing(0)
        pic.addWidget(self.ic, 0, Qt.AlignTop)
        pic.addWidget(self.thumb, 0, Qt.AlignTop)
        g.addLayout(pic, 0, 0)
        col = QVBoxLayout()
        col.setSpacing(2)
        self.title = label("", "title", wrap=True)
        self.body = label("", "body", wrap=True)
        self.body.setTextFormat(Qt.RichText)
        col.addWidget(self.title)
        col.addWidget(self.body)
        col.addStretch()
        g.addLayout(col, 0, 1)
        x = icon_button("close", "Закрыть", 14)
        x.setFixedSize(26, 26)
        x.clicked.connect(self.dismiss)
        g.addWidget(x, 0, 2, Qt.AlignTop)
        self.extra = QVBoxLayout()
        self.acts = QHBoxLayout()
        self.acts.setSpacing(6)
        g.addLayout(self.extra, 1, 0, 1, 3)
        g.addLayout(self.acts, 2, 0, 1, 3)
        g.setColumnStretch(1, 1)
        self.timer = QTimer(self, singleShot=True, timeout=self.dismiss)
        self.combo = None
        self.cancelled = False
        self._spec = None
        self._thumb_ready.connect(self._set_thumb)
        self.restyle()
        theme.changed.connect(self.restyle)

    def restyle(self):
        """Top-level tool windows don't reliably pick up app stylesheet changes, so the card carries its own."""
        t = theme.t
        self.card.setStyleSheet(
            f"QFrame#note {{ background: {t['raise_']}; border: 1px solid {t['line']}; border-radius: 10px; }}"
            f"QFrame#note QLabel {{ color: {t['fg']}; background: transparent; }}"
            f"QFrame#note QLabel#title {{ font-weight: 600; font-size: 10.5pt; }}"
            f"QFrame#note QLabel#body {{ color: {t['muted']}; font-size: 9pt; }}"
            f"QFrame#note QLabel#thumb {{ background: {t['panel']}; border-radius: 6px; }}"
            f"QFrame#note QPushButton {{ background: {t['raise_']}; color: {t['fg']}; border: 1px solid {t['line']};"
            f" border-radius: 6px; padding: 6px 12px; }}"
            f"QFrame#note QPushButton:hover {{ background: {t['menu_hl']}; }}"
            f"QFrame#note QPushButton#primary {{ background: {t['inv_bg']}; color: {t['inv_fg']}; border-color: {t['inv_bg']};"
            f" font-weight: 600; }}"
            f"QFrame#note QComboBox {{ background: {t['bg']}; color: {t['fg']}; border: 1px solid {t['line']};"
            f" border-radius: 6px; padding: 5px 8px; }}")
        eff = self.card.graphicsEffect()
        if eff:
            eff.setColor(Qt.black if theme.dark else Qt.gray)
        if self._spec:
            self.ic.setPixmap(icons.pixmap(self._spec[0], self._tone_color(self._spec[1]), 20))

    @staticmethod
    def _tone_color(tone):
        return {"ok": theme.t["ok"], "warn": theme.t["warn"], "err": theme.t["err"]}.get(tone, theme.t["fg"])

    def set(self, title, body="", icon="ok", tone="", actions=(), timeout=0, combo=None, thumb_url=""):
        self._spec = (icon, tone)
        self.ic.setPixmap(icons.pixmap(icon, self._tone_color(tone), 20))
        self.title.setText(title)
        self.body.setText(body)
        self.body.setVisible(bool(body))
        while self.extra.count():
            w = self.extra.takeAt(0).widget()
            w and w.deleteLater()
        while self.acts.count():
            w = self.acts.takeAt(0).widget()
            w and w.deleteLater()
        self.combo = None
        if combo:
            self.combo = QComboBox()
            self.combo.addItems(combo)
            self.extra.addWidget(self.combo)
        for a in actions:
            text, fn = a[0], a[1]
            b = push(text, a[3] if len(a) > 3 else None, primary=len(a) > 2 and a[2])
            b.clicked.connect(lambda _=False, fn=fn: self._act(fn))
            self.acts.addWidget(b)
        self.acts.addStretch()
        self.thumb.setVisible(bool(thumb_url))
        self.ic.setVisible(not thumb_url)
        if thumb_url:
            self.thumb.setPixmap(icons.pixmap("film", theme.t["muted"], 26))
            threading.Thread(target=self._fetch_thumb, args=(thumb_url,), daemon=True).start()
        self.timer.stop()
        if timeout:
            self.timer.start(timeout)
        self.adjustSize()
        self.center.relayout()

    def _fetch_thumb(self, url):
        try:
            r = requests.get(url, headers={"User-Agent": UA}, timeout=8)
            if r.ok and r.content:
                self._thumb_ready.emit(r.content)
        except Exception:
            pass

    def _set_thumb(self, data):
        img = QImage.fromData(data)
        if img.isNull():
            return
        dpr = self.devicePixelRatioF()
        pm = QPixmap.fromImage(img).scaled(int(THUMB_W * dpr), int(THUMB_H * dpr), Qt.KeepAspectRatioByExpanding,
                                           Qt.SmoothTransformation)
        x = (pm.width() - int(THUMB_W * dpr)) // 2
        y = (pm.height() - int(THUMB_H * dpr)) // 2
        pm = pm.copy(x, y, int(THUMB_W * dpr), int(THUMB_H * dpr))
        pm.setDevicePixelRatio(dpr)
        self.thumb.setPixmap(pm)

    def _act(self, fn):
        keep = fn(self) if fn else False
        if not keep:
            self.dismiss()

    def dismiss(self):
        self.cancelled = True
        self.closed.emit(self)
        self.close()

    def enterEvent(self, e):
        if self.timer.isActive():
            self.timer.setProperty("rest", self.timer.remainingTime())
            self.timer.stop()

    def leaveEvent(self, e):
        rest = self.timer.property("rest")
        if rest:
            self.timer.start(max(1500, rest))
            self.timer.setProperty("rest", 0)


class NotificationCenter(QObject):
    """Keeps a single card on screen; showing a new one closes the previous one in its place."""

    def __init__(self, window=None):
        super().__init__()
        self.window = window
        self.notes = []

    def show(self, title, body="", icon="ok", tone="", actions=(), timeout=0, combo=None, on_top=None, thumb_url=""):
        on_top = cfg["clip_top"] if on_top is None else on_top
        for old in list(self.notes):
            old.dismiss()
        n = Note(self, on_top)
        n.closed.connect(self._closed)
        self.notes.append(n)
        n.set(title, body, icon, tone, actions, timeout, combo, thumb_url)
        n.show()
        self.relayout()
        return n

    def _closed(self, n):
        if n in self.notes:
            self.notes.remove(n)

    def relayout(self):
        win = self.window
        if win is not None and win.isVisible() and not win.isMinimized():
            scr = win.screen().availableGeometry()
        else:
            scr = QGuiApplication.primaryScreen().availableGeometry()
        for n in self.notes:
            n.adjustSize()
            n.move(scr.right() - n.width() - 6, scr.bottom() - n.height() - 6)


def plural(n, one, few, many):
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


class ClipboardWatcher(QObject):
    """Offers to download media whenever a link to a page with video/audio lands in the clipboard.

    The page is checked first; a card appears only if media was found. The same link copied again is
    offered again. `actions` holds callbacks so the same watcher runs inside the player or in the helper:
      download(option, result), play(option, result), options(result), queue()
    """

    def __init__(self, notes: NotificationCenter, actions: dict, busy_with_links=lambda: False):
        super().__init__()
        self.notes = notes
        self.actions = actions
        self.busy_with_links = busy_with_links      # True while the user works in the Downloads tab
        self.ignore_next = None
        self.cb = QGuiApplication.clipboard()
        self.started = time.monotonic()
        self.initial = self.cb.text()               # what was already in the clipboard before we started
        self.last = ("", 0.0)
        self.running = {}                           # url -> Prober
        self.enabled = True
        self.cb.dataChanged.connect(self._changed)

    def ignore(self, text):
        """Skip a text the app itself put into the clipboard."""
        self.ignore_next = text

    def _changed(self):
        if not (self.enabled and cfg["clip_watch"]):
            return
        text = self.cb.text()
        now = time.monotonic()
        if text and text == self.ignore_next:
            self.ignore_next = None
            return
        # Windows re-announces the existing clipboard when a listener registers; that isn't a new copy
        if text == self.initial and now - self.started < 5:
            return
        # some programs set the clipboard twice per copy
        if text == self.last[0] and now - self.last[1] < 1.5:
            return
        self.last = (text, now)
        if self.busy_with_links():
            return
        self.check(text)

    def check(self, text, force=False):
        """Probe a link silently. Returns False when the text holds no link."""
        url = normalize(find_url(text or "") or "")
        if not url:
            return False
        if url in self.running:
            return True
        pr = Prober(self)
        self.running[url] = pr
        pr.finished.connect(lambda res, pr=pr, url=url, force=force: self._result(pr, url, res, force))
        pr.start(url)
        return True

    def _result(self, pr, url, res, force):
        self.running.pop(url, None)
        pr.deleteLater()
        if res.state != "ok" or not res.options:
            if force:       # the user asked explicitly (Ctrl+V, tray): tell them; a plain copy stays silent
                why = {"drm": "Видео защищено DRM, его нельзя скачать."}.get(res.state, "На странице нет видео или аудио.")
                self.notes.show("Медиа не найдено", html.escape(res.message or why), "clip", timeout=6000)
            return
        from ..net.ytdl import default_index
        di = default_index(res.options)
        best = res.options[di]
        name = res.title or best.title
        short = html.escape(name if len(name) <= 80 else name[:78] + "…")
        thumb = (res.info or {}).get("thumbnail") or res.thumbnail
        dur = (res.info or {}).get("duration") or best.duration
        a = self.actions
        if cfg["clip_auto"]:
            a["download"](best, res)
            self.notes.show(short, "Загрузка началась", "dl", "ok",
                            [("Очередь", lambda _n: (a["queue"](), None)[1])], timeout=9000, thumb_url=thumb)
            return
        count = res.entries or 0
        if count > 1:
            body = f"Плейлист · {count} видео"
        else:
            body = fmt_time(dur) if dur else ""
        combo = [" · ".join(x for x in (o.label, o.detail, ("≈" + fmt_size(o.size)) if o.size else "") if x)
                 for o in res.options]

        def chosen(nn):
            return res.options[nn.combo.currentIndex() if nn.combo else di]

        # each action returns None, so the card closes after it
        n = self.notes.show(short, body, "dl", "",
                            [("Скачать", lambda nn: (a["download"](chosen(nn), res), None)[1], True, "dl"),
                             ("Смотреть", lambda nn: (a["play"](chosen(nn), res), None)[1]),
                             ("Подробнее", lambda nn: (a["options"](res), None)[1])],
                            combo=combo if len(combo) > 1 else None, thumb_url=thumb)
        if n.combo:
            n.combo.setCurrentIndex(di)
