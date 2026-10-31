"""Downloads tab: the download queue."""
import os
import subprocess

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QProgressBar, QScrollArea, QVBoxLayout, QWidget

from .. import icons
from ..theme import theme
from .widgets import ListBox, Row, fmt_size, icon_button, label, push, text_block
STATE_TAG = {"done": ("Готово", "okTag"), "pause": ("Пауза", "tag"), "error": ("Ошибка", "errTag"),
             "queued": ("В очереди", "tag"), "cancel": ("Отменено", "tag")}


def fmt_eta(s):
    s = int(s or 0)
    if not s:
        return ""
    return f"{s // 3600}:{s // 60 % 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"


class TaskRow(Row):
    def __init__(self, view, task):
        super().__init__()
        self.view, self.t = view, task
        ic = label()
        ic.setPixmap(icons.pixmap("dl", theme.t["muted"], 18))
        self.h.addWidget(ic)
        box, self.name, self.meta = text_block(task.title, "")
        self.h.addLayout(box, 1)
        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.bar.setTextVisible(False)
        self.bar.setFixedWidth(150)
        self.tag = label("", "tag")
        self.num = label("", "num")
        self.num.setMinimumWidth(150)
        self.num.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.h.addWidget(self.bar)
        self.h.addWidget(self.tag)
        self.h.addWidget(self.num)
        self.b1 = icon_button("pause", "")
        self.b2 = icon_button("close", "")
        self.h.addWidget(self.b1)
        self.h.addWidget(self.b2)
        self.b1.clicked.connect(self._b1)
        self.b2.clicked.connect(self._b2)
        self.refresh()

    def refresh(self):
        t = self.t
        where = t.folder or "—"
        meta = " · ".join(x for x in (t.label, t.detail, where) if x)
        if t.backup:
            meta += "  (резервная папка)"
        if t.state == "error" and t.error:
            meta = t.error
        self.meta.setText(meta)
        self.meta.setToolTip(t.error or t.file or t.url)
        self.name.setText(t.title)
        running = t.state == "run"
        self.bar.setVisible(t.state in ("run", "pause", "queued"))
        self.bar.setValue(int(t.progress * 1000))
        text, obj = STATE_TAG.get(t.state, ("", "tag"))
        if t.backup and t.state in ("run", "done"):
            text, obj = ("Резерв" if running else "Готово · резерв"), "warnTag"
        self.tag.setText(text)
        self.tag.setObjectName(obj)
        self.tag.setVisible(bool(text) and not running or t.backup)
        self.tag.style().unpolish(self.tag)
        self.tag.style().polish(self.tag)
        if running:
            sp = f"{t.speed / 1e6:.1f} МБ/с" if t.speed else "…"
            eta = fmt_eta(t.eta)
            self.num.setText(f"{t.progress * 100:.0f}% · {sp}" + (f" · {eta}" if eta else ""))
        elif t.state == "done":
            self.num.setText(fmt_size(t.total or t.done))
        elif t.state == "pause":
            self.num.setText(f"{t.progress * 100:.0f}%")
        else:
            self.num.setText("")
        if t.state == "done":
            icons.set_name(self.b1, "play"); self.b1.setToolTip("Воспроизвести")
            icons.set_name(self.b2, "folder"); self.b2.setToolTip("Показать в папке")
        elif t.state in ("pause", "error"):
            icons.set_name(self.b1, "refresh" if t.state == "error" else "play")
            self.b1.setToolTip("Повторить" if t.state == "error" else "Продолжить")
            icons.set_name(self.b2, "close"); self.b2.setToolTip("Убрать")
        else:
            icons.set_name(self.b1, "pause"); self.b1.setToolTip("Пауза")
            icons.set_name(self.b2, "close"); self.b2.setToolTip("Отменить")

    def _b1(self):
        t, dm = self.t, self.view.dm
        if t.state == "done":
            self.view.play_file.emit(t.file, t.title)
        elif t.state in ("pause", "error"):
            dm.resume(t)
        else:
            dm.pause(t)

    def _b2(self):
        t, dm = self.t, self.view.dm
        if t.state == "done":
            if t.file and os.path.exists(t.file):
                subprocess.Popen(["explorer", "/select,", os.path.normpath(t.file)])
            elif t.folder:
                os.startfile(t.folder)
        elif t.state in ("pause", "error", "cancel"):
            dm.remove(t)
        else:
            dm.cancel(t)



class DownloadsView(QScrollArea):
    play_file = Signal(str, str)

    def __init__(self, downloads, parent=None):
        super().__init__(parent)
        self.dm = downloads
        self.setObjectName("page")
        self.setWidgetResizable(True)
        body = QWidget()
        body.setObjectName("page")
        self.setWidget(body)
        v = QVBoxLayout(body)
        v.setContentsMargins(28, 22, 28, 24)
        v.setSpacing(10)
        head = QHBoxLayout()
        head.addWidget(label("Загрузки", "h1"))
        head.addStretch()
        clear = push("Убрать завершённые")
        clear.clicked.connect(self.dm.clear_finished)
        head.addWidget(clear, 0, Qt.AlignBottom)
        v.addLayout(head)
        v.addSpacing(6)
        self.queue = ListBox()
        v.addWidget(self.queue)
        v.addStretch()
        self.rows = {}
        self.dm.listed.connect(self.render_queue)
        self.dm.updated.connect(self._task_updated)
        self.render_queue()

    def render_queue(self):
        self.queue.clear()
        self.rows = {}
        if not self.dm.tasks:
            self.queue.empty("Очередь пуста")
            return
        for t in self.dm.tasks:
            row = TaskRow(self, t)
            self.rows[t.id] = row
            self.queue.add(row)

    def _task_updated(self, tid):
        row = self.rows.get(tid)
        if row:
            row.refresh()