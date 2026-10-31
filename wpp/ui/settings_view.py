"""Settings tab."""
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QComboBox, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QScrollArea, QSizePolicy,
                               QVBoxLayout, QWidget)

from .. import EDITION, __version__, autostart, ipc, updater
from ..config import AUDIO_DEFAULTS, VIDEO_DEFAULTS, cfg
from ..net.downloads import folder_problem
from ..theme import theme
from .widgets import ListBox, Row, Switch, label, push

CODECS = ["HEVC / H.265 10-bit", "AV1", "VVC / H.266", "H.264", "VP9", "ProRes", "DNxHR", "MPEG-2", "VC-1", "HDR10",
          "HDR10+", "Dolby Vision", "HLG", "TrueHD Atmos", "DTS-HD MA", "DTS:X", "E-AC3", "FLAC", "ALAC", "DSD", "Opus",
          "APE", "MKV", "M2TS", "HLS", "DASH", "PGS", "ASS"]


class ElidedChip(QLabel):
    """Format chip: cuts long names with “…” and shows the full name in a tooltip."""
    def __init__(self, text):
        super().__init__()
        self.full = text
        self.setObjectName("chip")
        self.setMinimumWidth(30)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        avail = self.contentsRect().width() - 2
        shown = self.fontMetrics().elidedText(self.full, Qt.ElideRight, max(10, avail))
        self.setText(shown)
        self.setToolTip(self.full if shown != self.full else "")


class SettingsView(QScrollArea):
    def __init__(self, upd: updater.Updater, open_eq, parent=None):
        super().__init__(parent)
        self.upd = upd
        self.setObjectName("page")
        self.setWidgetResizable(True)
        body = QWidget()
        body.setObjectName("page")
        self.setWidget(body)
        v = self.v = QVBoxLayout(body)
        v.setContentsMargins(28, 22, 28, 24)
        v.setSpacing(4)
        v.addWidget(label("Настройки", "h1"))

        box = self.section("Оформление")
        self.row(box, "Тема", "",
                 self.combo("theme", [("system", "Как в Windows"), ("light", "Светлая"), ("dark", "Тёмная")]))

        box = self.section("Воспроизведение")
        self.row(box, "Аппаратное декодирование", "",
                 self.combo("hwdec", [("auto-safe", "Авто"), ("d3d11va", "D3D11VA"), ("nvdec", "NVDEC"),
                                      ("qsv", "Intel QSV"), ("vulkan", "Vulkan"), ("no", "Выключено")]))
        self.row(box, "Вывод HDR", "",
                 self.combo("hdr", [("auto", "Авто"), ("passthrough", "Передавать на экран"), ("tonemap", "Преобразовывать в SDR")]))
        self.row(box, "Передача звука по S/PDIF и HDMI", "", self.switch("spdif"))
        self.row(box, "Продолжать с места остановки", "", self.switch("resume"))
        eqb = push("Настроить", "eq")
        eqb.clicked.connect(open_eq)
        self.eq_desc = self.row(box, "Эквалайзер", self._eq_text(), eqb)
        cfg.changed.connect(lambda k: k == "eq" and self.eq_desc.setText(self._eq_text()))

        box = self.section("Загрузки")
        self.dir1 = self.folder_row(box, "dl_dir", "Основная папка", "")
        self.dir2 = self.folder_row(box, "dl_backup", "Резервная папка", "")
        self.row(box, "Качество по умолчанию", "",
                 self.combo("quality", [("best", "Лучшее"), ("custom", "Свой вариант"), ("audio", "Только аудио")]))
        self.row(box, "Видео по умолчанию", "", self.combo("dl_video", VIDEO_DEFAULTS))
        self.row(box, "Звук по умолчанию", "", self.combo("dl_audio", AUDIO_DEFAULTS))
        self.row(box, "Контейнер", "",
                 self.combo("dl_container", [("auto", "Автоматически"), ("mp4", "MP4"), ("mkv", "MKV"), ("webm", "WebM")]))
        par = QComboBox()
        for n in (1, 2, 3, 4):
            par.addItem(str(n), n)
        par.setCurrentIndex(max(0, cfg["max_parallel"] - 1))
        par.currentIndexChanged.connect(lambda i: cfg.set("max_parallel", i + 1))
        self.row(box, "Одновременных загрузок", "", par)
        self.row(box, "Искать медиа на неподдерживаемых сайтах", "", self.switch("sniff"))

        box = self.section("Распознавание ссылок")
        self.helper_sw = Switch(cfg["helper"])
        self.helper_sw.toggled.connect(self._helper_toggled)
        self.helper_state = self.row(box, "Работать в фоне, когда плеер закрыт", "", self.helper_sw)
        self.auto_sw = Switch(self._autostart_on())
        self.auto_sw.toggled.connect(self._autostart_toggled)
        self.row(box, "Запускать вместе с Windows", "", self.auto_sw)
        self.row(box, "Распознавать ссылки из буфера обмена", "", self.switch("clip_watch"))
        self.row(box, "Показывать окно поверх других программ", "", self.switch("clip_top"))
        self.row(box, "Сразу скачивать с качеством по умолчанию", "", self.switch("clip_auto"))
        self.row(box, "Сворачивать в трей при закрытии", "", self.switch("tray_on_close"))

        box = self.section("Обновление")
        self.row(box, "Проверять обновления при запуске", "", self.switch("update_check"))
        actions = QHBoxLayout()
        self.chk = push("Проверить", "refresh")
        self.apply_btn = push("Обновить", "dl", primary=True)
        self.apply_btn.hide()
        actions.addWidget(self.chk)
        actions.addWidget(self.apply_btn)
        w = QWidget()
        w.setLayout(actions)
        actions.setContentsMargins(0, 0, 0, 0)
        self.upd_status = self.row(box, f"Версия {__version__} ({EDITION}) · OFFTeams", updater.current_version(), w)
        self.chk.clicked.connect(lambda: self.upd.check())
        self.apply_btn.clicked.connect(self.upd.apply)
        self.upd.status.connect(self._upd_status)
        self.upd.available.connect(lambda n, log: (self.apply_btn.show(), self.apply_btn.setText(f"Обновить ({n})")))
        self.upd.applied.connect(lambda _s: self.apply_btn.hide())

        self.v.addSpacing(16)
        v.addWidget(label("Поддерживаемые форматы", "sec"))
        v.addSpacing(4)
        flow = QWidget()
        from PySide6.QtWidgets import QGridLayout
        g = QGridLayout(flow)
        g.setContentsMargins(0, 0, 0, 0)
        g.setHorizontalSpacing(4)
        g.setVerticalSpacing(4)
        cols = 14
        for i, c in enumerate(CODECS):
            g.addWidget(ElidedChip(c), i // cols, i % cols)
        for c in range(cols):
            g.setColumnStretch(c, 1)
        v.addWidget(flow)
        self.v.addSpacing(16)
        v.addWidget(label("Горячие клавиши", "sec"))
        v.addSpacing(4)
        v.addWidget(label("Пробел — пауза · ← → — 10 с · ↑ ↓ — громкость · F — полный экран · M — без звука · "
                          "[ ] — скорость · N P — следующий и предыдущий · E — эквалайзер · Ctrl+O — открыть файл · "
                          "Ctrl+V — ссылка из буфера · S — снимок экрана", "desc", wrap=True))
        v.addStretch()
        self._dir_status()

    # ----- builders -----
    def section(self, title):
        self.v.addSpacing(16)
        self.v.addWidget(label(title, "sec"))
        self.v.addSpacing(4)
        box = ListBox()
        self.v.addWidget(box)
        return box

    def row(self, box, title, desc, widget):
        r = Row()
        r.h.setContentsMargins(14, 10, 14, 10)
        col = QVBoxLayout()
        col.setSpacing(2)
        col.addWidget(label(title))
        d = label(desc, "desc", wrap=True)
        col.addWidget(d)
        r.h.addLayout(col, 1)
        r.h.addWidget(widget, 0, Qt.AlignVCenter)
        box.add(r)
        # only after the label has a parent: setVisible(True) on a parentless widget opens it as its own window
        d.setVisible(bool(desc))
        d.col = col
        return d

    def combo(self, key, items):
        c = QComboBox()
        for val, text in items:
            c.addItem(text, val)
        c.setCurrentIndex(max(0, c.findData(cfg[key])))
        c.currentIndexChanged.connect(lambda i: cfg.set(key, c.itemData(i)))
        c.setMinimumWidth(200)
        return c

    def switch(self, key):
        s = Switch(cfg[key])
        s.toggled.connect(lambda on: cfg.set(key, on))
        return s

    def folder_row(self, box, key, title, desc):
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        e = QLineEdit(cfg[key])
        e.setMinimumWidth(280)
        b = push("Обзор…")
        h.addWidget(e)
        h.addWidget(b)

        def commit():
            cfg.set(key, e.text().strip())
            self._dir_status()

        def browse():
            d = QFileDialog.getExistingDirectory(self, title, e.text())
            if d:
                e.setText(d.replace("/", "\\"))
                commit()
        e.editingFinished.connect(commit)
        b.clicked.connect(browse)
        desc_label = self.row(box, title, desc, w)
        lab = label("", "ok")
        desc_label.col.addWidget(lab)
        return lab

    def _dir_status(self):
        for lab, key in ((self.dir1, "dl_dir"), (self.dir2, "dl_backup")):
            why = folder_problem(cfg[key])
            lab.setObjectName("err" if why else "ok")
            lab.setText(f"Недоступна: {why}" if why else "")
            lab.setVisible(bool(why))
            lab.style().unpolish(lab)
            lab.style().polish(lab)

    def _eq_text(self):
        eq = cfg["eq"]
        return f"Включён · {eq['preset']}" if eq["on"] else "Выключен"

    @staticmethod
    def _autostart_on():
        import logging
        try:
            on = autostart.is_enabled()
            return on
        except BaseException:
            logging.getLogger("wpp").exception("autostart state")
            return False

    def _autostart_toggled(self, on):
        try:
            autostart.set_enabled(on)
        except Exception as e:
            self.auto_sw.blockSignals(True)
            self.auto_sw.setChecked(not on)
            self.auto_sw.blockSignals(False)
            self.helper_state.setText(f"Не удалось изменить автозагрузку: {e}")

    def _helper_toggled(self, on):
        cfg.set("helper", on)
        if on:
            if not ipc.is_running(ipc.HELPER):
                ipc.launch(ipc.helper_cmd())
        else:
            ipc.send(ipc.HELPER, {"cmd": "quit"})
        self._helper_status()

    def _helper_status(self):
        running = ipc.is_running(ipc.HELPER)
        self.helper_state.setText("" if running else "Не запущено")
        self.helper_state.setVisible(not running)

    def _upd_status(self, text, tone):
        self.upd_status.setText(text)
        color = {"ok": theme.t["ok"], "warn": theme.t["warn"], "err": theme.t["err"]}.get(tone, theme.t["muted"])
        self.upd_status.setStyleSheet(f"color:{color}")

    def showEvent(self, e):
        self._dir_status()
        self._helper_status()
        self.auto_sw.blockSignals(True)
        self.auto_sw.setChecked(self._autostart_on())      # Task Manager may have switched it
        self.auto_sw.blockSignals(False)
        super().showEvent(e)