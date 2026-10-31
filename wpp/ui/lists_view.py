"""Playlist and history tabs."""
import html
import os
import subprocess
import time

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QFileDialog, QHBoxLayout, QLabel, QProgressBar, QScrollArea, QSizePolicy, QVBoxLayout,
                               QWidget)

from .. import icons
from ..library import MEDIA_EXT, Item
from ..theme import theme
from .widgets import ListBox, Row, fmt_time, icon_button, label, push, text_block


class Page(QScrollArea):
    def __init__(self, title, lead, parent=None):
        super().__init__(parent)
        self.setObjectName("page")
        self.setWidgetResizable(True)
        body = QWidget()
        body.setObjectName("page")
        self.setWidget(body)
        self.v = QVBoxLayout(body)
        self.v.setContentsMargins(28, 22, 28, 24)
        self.v.setSpacing(8)
        self.v.addWidget(label(title, "h1"))

        self.tools = QHBoxLayout()
        self.v.addLayout(self.tools)
        self.list = ListBox()
        self.v.addSpacing(6)
        self.v.addWidget(self.list)
        self.v.addStretch()


class PlaylistView(Page):
    play = Signal(int)
    open_files = Signal()

    def __init__(self, playlist, player, parent=None):
        super().__init__("Плейлист", "Файлы с диска, потоки и найденное на страницах. Двойной щелчок — воспроизвести.", parent)
        self.pl = playlist
        self.player = player
        add = push("Добавить файлы", "plus")
        folder = push("Добавить папку", "folder")
        clear = push("Очистить", "trash")
        add.clicked.connect(self._add)
        folder.clicked.connect(self._add_folder)
        clear.clicked.connect(self.pl.clear)
        for b in (add, folder, clear):
            self.tools.addWidget(b)
        self.tools.addStretch()
        self.pl.changed.connect(self.render)
        player.video.prop.connect(lambda n, v: n == "pause" and self.render())
        self.render()

    def _add(self):
        from ..library import file_filter
        files, _ = QFileDialog.getOpenFileNames(self, "Добавить в плейлист", "", file_filter())
        if files:
            self.pl.add([Item(f) for f in files])

    def _add_folder(self):
        d = QFileDialog.getExistingDirectory(self, "Папка с медиа")
        if not d:
            return
        found = []
        for root, _, files in os.walk(d):
            for f in sorted(files):
                if f.rsplit(".", 1)[-1].lower() in MEDIA_EXT:
                    found.append(Item(os.path.join(root, f)))
        if found:
            self.pl.add(found)

    def render(self):
        self.list.clear()
        if not self.pl.items:
            self.list.empty("Плейлист пуст")
            return
        for i, it in enumerate(self.pl.items):
            r = Row()
            r.setProperty("current", "true" if i == self.pl.index else "false")
            ic = label()
            ic.setPixmap(icons.pixmap("note" if it.audio_only else ("link" if it.is_url else "film"), theme.t["muted"], 18))
            r.h.addWidget(ic)
            if it.is_url:
                from urllib.parse import urlparse
                meta = (urlparse(it.page_url or it.path).hostname or "").removeprefix("www.")
            else:
                meta = os.path.dirname(it.path)
            box, _, _ = text_block(it.title, meta)
            r.h.addLayout(box, 1)
            up = icon_button("prev", "Выше", 14)
            up.setEnabled(i > 0)
            up.clicked.connect(lambda _=False, i=i: self.pl.move(i, i - 1))
            playing = i == self.pl.index and not self.player.paused and self.player.loaded
            pb = icon_button("pause" if playing else "play", "Воспроизвести")
            pb.clicked.connect(lambda _=False, i=i: self.player.toggle() if i == self.pl.index and self.player.loaded else self.play.emit(i))
            rm = icon_button("trash", "Убрать из плейлиста")
            rm.clicked.connect(lambda _=False, i=i: self.pl.remove(i))
            r.h.addWidget(up)
            r.h.addWidget(pb)
            r.h.addWidget(rm)
            r.mouseDoubleClickEvent = lambda e, i=i: self.play.emit(i)
            self.list.add(r)


def when_text(ts):
    d = time.time() - (ts or 0)
    lt = time.localtime(ts or 0)
    if d < 86400 and time.localtime().tm_mday == lt.tm_mday:
        return "Сегодня, " + time.strftime("%H:%M", lt)
    if d < 2 * 86400:
        return "Вчера, " + time.strftime("%H:%M", lt)
    return time.strftime("%d.%m.%Y", lt)


class HistoryView(Page):
    resume = Signal(dict)

    def __init__(self, history, origin_for=lambda path: "", parent=None):
        super().__init__("История", "Плеер помнит, где вы остановились, и продолжит с того же места.", parent)
        self.h = history
        self.origin_for = origin_for
        clear = push("Очистить историю", "trash")
        clear.clicked.connect(self.h.clear)
        self.tools.addWidget(clear)
        self.tools.addStretch()
        self.h.changed.connect(self.render)
        self.render()

    @staticmethod
    def _link(url):
        short = url if len(url) <= 70 else url[:68] + "…"
        return f'<a href="{html.escape(url, quote=True)}" style="color:{theme.t["muted"]}">{html.escape(short)}</a>'

    def render(self):
        self.list.clear()
        if not self.h.entries:
            self.list.empty("История пуста")
            return
        for e in self.h.entries[:150]:
            r = Row()
            ic = label()
            ic.setPixmap(icons.pixmap("hist", theme.t["muted"], 18))
            r.h.addWidget(ic)
            dur, pos = e.get("dur") or 0, e.get("pos") or 0
            frac = min(1.0, pos / dur) if dur else 0
            state = "досмотрено" if frac >= 0.97 else (f"остановились на {fmt_time(pos)} из {fmt_time(dur)}" if dur else "")
            box, _, _ = text_block(e.get("title") or e["path"], " · ".join(x for x in (when_text(e.get("when")), state) if x))
            is_url = "://" in e["path"]
            where = e.get("where") or (e.get("page_url") or e["path"] if is_url else os.path.dirname(e["path"]))
            origin = e.get("origin") or ("" if is_url else self.origin_for(e["path"]))
            src = QLabel()
            src.setObjectName("meta")
            src.setTextFormat(Qt.RichText)
            src.setOpenExternalLinks(True)
            src.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
            parts = []
            if is_url:
                parts.append(f"Поток: {self._link(where)}")
            else:
                parts.append(f"Папка: {html.escape(os.path.normpath(where))}")
                if origin:
                    parts.append(f"скачано с {self._link(origin)}")
            src.setText(" · ".join(parts))
            src.setToolTip("\n".join(x for x in (e["path"], origin) if x))
            box.addWidget(src)
            r.h.addLayout(box, 1)
            if not is_url and os.path.exists(e["path"]):
                fb = icon_button("folder", "Показать в папке")
                fb.clicked.connect(lambda _=False, p=e["path"]: subprocess.Popen(["explorer", "/select,", os.path.normpath(p)]))
                r.h.addWidget(fb)
            bar = QProgressBar()
            bar.setRange(0, 1000)
            bar.setValue(int(frac * 1000))
            bar.setTextVisible(False)
            bar.setFixedWidth(120)
            r.h.addWidget(bar)
            b = icon_button("play", "Продолжить")
            b.clicked.connect(lambda _=False, e=e: self.resume.emit(e))
            rm = icon_button("close", "Удалить из истории")
            rm.clicked.connect(lambda _=False, k=e["key"]: self.h.remove(k))
            r.h.addWidget(b)
            r.h.addWidget(rm)
            self.list.add(r)
