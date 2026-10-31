"""Playback screen: video stage, timeline, transport and track/picture/equalizer menus."""
import os
import re
import threading

from PySide6.QtCore import QEvent, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QActionGroup, QCursor
from PySide6.QtWidgets import (QFileDialog, QFrame, QHBoxLayout, QLabel, QMenu, QSizePolicy, QSlider, QStackedWidget,
                               QToolButton, QVBoxLayout, QWidget)

from .. import icons
from ..config import cfg
from ..library import Item, file_filter
from ..net import ytdl
from ..net.streamproxy import proxied
from ..player.mpvwidget import MpvWidget
from ..theme import theme
from .eq import EqPopup
from .widgets import Popup, SeekBar, ThinSlider, fmt_time, icon_button, label, push


class FullscreenBar(QWidget):
    """Floating transport for full screen: a borderless window owned by the main window, faded in and out.

    It stays shown at opacity 0 when hidden, so hovering the bottom edge brings it back.
    """
    def __init__(self, view):
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint | Qt.WindowDoesNotAcceptFocus)
        self.view = view
        self.setObjectName("fsbar")
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(0, 0, 0, 0)
        from PySide6.QtCore import QPropertyAnimation
        self.anim = QPropertyAnimation(self, b"windowOpacity", self)
        self.anim.setDuration(220)
        self.controls = None

    def attach(self, controls, owner):
        self.controls = controls
        if self.parent() is not owner:
            self.setParent(owner, self.windowFlags())       # owned window: always above the full-screen player
        self.lay.addWidget(controls)
        controls.show()
        scr = owner.screen().geometry()
        w = min(scr.width() - 64, 1400)
        self.setFixedWidth(w)
        self.adjustSize()
        self.move(scr.center().x() - w // 2, scr.bottom() - self.height() - 24)
        self.setWindowOpacity(0.0)
        self.show()

    def detach(self):
        self.anim.stop()
        self.hide()
        if self.controls:
            self.lay.removeWidget(self.controls)
            c, self.controls = self.controls, None
            return c

    def _fade(self, to):
        self.anim.stop()
        self.anim.setStartValue(self.windowOpacity())
        self.anim.setEndValue(to)
        self.anim.start()

    def _running_to(self, value):
        from PySide6.QtCore import QAbstractAnimation
        return self.anim.state() == QAbstractAnimation.Running and self.anim.endValue() == value

    def reveal(self):
        if self.windowOpacity() < 0.99 and not self._running_to(1.0):
            self._fade(1.0)

    def fade_out(self):
        if self.windowOpacity() > 0.01 and not self._running_to(0.0):
            self._fade(0.0)

    def enterEvent(self, e):
        self.reveal()
        self.view.osd_timer.start()

    def mouseMoveEvent(self, e):
        self.reveal()
        self.view.osd_timer.start()

SPEEDS = [0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0, 3.0, 4.0]
LANG = {"rus": "Русский", "ru": "Русский", "eng": "Английский", "en": "Английский", "ukr": "Украинский",
        "uk": "Украинский", "jpn": "Японский", "ja": "Японский", "ger": "Немецкий", "deu": "Немецкий", "de": "Немецкий",
        "fre": "Французский", "fra": "Французский", "fr": "Французский", "spa": "Испанский", "es": "Испанский",
        "ita": "Итальянский", "it": "Итальянский", "chi": "Китайский", "zho": "Китайский", "zh": "Китайский",
        "kor": "Корейский", "ko": "Корейский", "und": ""}


CODEC_SHORT = {"ac3": "AC3", "eac3": "E-AC3", "truehd": "TrueHD", "mlp": "MLP", "dts": "DTS", "aac": "AAC", "opus": "Opus",
               "vorbis": "Vorbis", "flac": "FLAC", "alac": "ALAC", "mp3": "MP3", "mp2": "MP2", "wmav2": "WMA", "wmapro": "WMA Pro",
               "ape": "APE", "wavpack": "WavPack", "tta": "TTA", "dsd_lsbf": "DSD", "dsd_msbf": "DSD", "hevc": "HEVC",
               "h264": "H.264", "av1": "AV1", "vp9": "VP9", "vvc": "VVC", "mpeg2video": "MPEG-2", "vc1": "VC-1",
               "subrip": "SRT", "ass": "ASS", "ssa": "SSA", "hdmv_pgs_subtitle": "PGS", "dvd_subtitle": "VobSub",
               "webvtt": "WebVTT", "mov_text": "TX3G", "dvb_subtitle": "DVB"}


def codec_short(t):
    c = (t.get("codec") or "").lower()
    if c.startswith("pcm"):
        return "PCM"
    name = CODEC_SHORT.get(c) or (t.get("codec-desc") or c).split(" (")[0]
    prof = t.get("codec-profile") or ""
    if c == "dts" and prof and prof.upper() != "DTS":
        name = prof.replace("DTS-HD MA", "DTS-HD MA")
    return name


def track_label(t):
    parts = []
    lang = LANG.get((t.get("lang") or "").lower(), (t.get("lang") or "").upper())
    if lang:
        parts.append(lang)
    if t.get("title"):
        parts.append(t["title"])
    codec = codec_short(t)
    if t["type"] == "audio":
        ch = t.get("demux-channel-count")
        codec = f"{codec} {ch}.0".replace(" 6.0", " 5.1").replace(" 8.0", " 7.1") if ch else codec
    if codec:
        parts.append(codec)
    if t.get("external"):
        parts.append("внешние")
    if t.get("forced"):
        parts.append("форсированные")
    return " · ".join(parts) or f"Дорожка {t['id']}"


class PlayerView(QWidget):
    title_changed = Signal(str)
    fullscreen_toggle = Signal()
    fit_video = Signal(int, int)
    toast = Signal(str)

    def __init__(self, playlist, history, parent=None):
        super().__init__(parent)
        self.pl = playlist
        self.history = history
        self.pos = 0.0
        self.dur = 0.0
        self.paused = True
        self.tracks = []
        self.loaded = False
        self.fullscreen = False

        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        self.stack = QStackedWidget()
        self.stack.setObjectName("stage")
        v.addWidget(self.stack, 1)

        empty = QWidget()
        empty.setObjectName("empty")
        empty.setAttribute(Qt.WA_StyledBackground)
        ev = QVBoxLayout(empty)
        ev.addStretch()
        self.empty_icon = QLabel()
        self.empty_icon.setAlignment(Qt.AlignCenter)
        ev.addWidget(self.empty_icon)
        t1 = label("Перетащите файл сюда")
        t1.setAlignment(Qt.AlignCenter)
        t1.setStyleSheet("font-size:13pt")
        ev.addWidget(t1)
        ev.addSpacing(6)
        ob = push("Открыть файл", "folder")
        ob.clicked.connect(self.open_files)
        row = QHBoxLayout()
        row.addStretch()
        row.addWidget(ob)
        row.addStretch()
        ev.addLayout(row)
        ev.addStretch()
        self.stack.addWidget(empty)

        self.video = MpvWidget()
        self.stack.addWidget(self.video)
        self.mpv = self.video.mpv

        self.controls = QFrame()
        self.controls.setObjectName("controls")
        cv = QVBoxLayout(self.controls)
        cv.setContentsMargins(12, 0, 12, 4)
        cv.setSpacing(0)
        sr = QHBoxLayout()
        self.t_cur = label("0:00", "time")
        self.t_dur = label("0:00", "time")
        self.seek = SeekBar()
        sr.addWidget(self.t_cur)
        sr.addWidget(self.seek, 1)
        sr.addWidget(self.t_dur)
        cv.addLayout(sr)
        br = QHBoxLayout()
        br.setSpacing(2)
        ICON = 18          # one size for every control icon
        self.b_prev = icon_button("prev", "Предыдущий (P)", ICON)
        self.b_back = icon_button("back10", "Назад 10 с (←)", ICON)
        self.b_play = icon_button("play", "Пауза (Пробел)", ICON)
        self.b_fwd = icon_button("fwd10", "Вперёд 10 с (→)", ICON)
        self.b_next = icon_button("next", "Следующий (N)", ICON)
        self.b_mute = icon_button("vol", "Звук (M)", ICON)
        self.vol = ThinSlider(Qt.Horizontal)
        self.vol.setRange(0, 150)
        self.vol.setValue(cfg["volume"])
        self.vol.setFixedWidth(96)
        self.vol.setAccessibleName("Громкость")
        self.vol.setToolTip("Громкость")
        for b in (self.b_prev, self.b_back, self.b_play, self.b_fwd, self.b_next, self.b_mute):
            br.addWidget(b)
        br.addWidget(self.vol)
        br.addStretch()
        self.b_speed = QToolButton()
        self.b_speed.setObjectName("pill")
        self.b_speed.setText("1.0×")
        self.b_speed.setToolTip("Скорость ([ и ])")
        self.b_speed.setCursor(Qt.PointingHandCursor)
        self.b_loop = icon_button("repeat", "Повтор", ICON, checkable=True)
        self.b_sub = icon_button("cc", "Субтитры", ICON)
        self.b_aud = icon_button("audio", "Аудиодорожка", ICON)
        self.b_eq = icon_button("eq", "Эквалайзер (E)", ICON)
        self.b_adj = icon_button("adj", "Изображение", ICON)
        self.b_open = icon_button("folder", "Открыть файл (Ctrl+O)", ICON)
        self.b_full = icon_button("full", "Полный экран (F)", ICON)
        for b in (self.b_prev, self.b_back, self.b_play, self.b_fwd, self.b_next, self.b_mute, self.b_loop,
                  self.b_sub, self.b_aud, self.b_eq, self.b_adj, self.b_open, self.b_full):
            b.setFixedSize(32, 32)
        self.b_speed.setFixedHeight(26)
        for b in (self.b_speed, self.b_loop, self.b_sub, self.b_aud, self.b_eq, self.b_adj, self.b_open, self.b_full):
            br.addWidget(b)
        # keyboard focus stays off the transport so Space/arrows always reach the player
        for b in (self.b_prev, self.b_back, self.b_play, self.b_fwd, self.b_next, self.b_mute, self.vol, self.b_speed,
                  self.b_loop, self.b_sub, self.b_aud, self.b_eq, self.b_adj, self.b_open, self.b_full, self.seek):
            b.setFocusPolicy(Qt.NoFocus)
        cv.addLayout(br)
        v.addWidget(self.controls)

        # wiring
        self.b_play.clicked.connect(self.toggle)
        self.b_prev.clicked.connect(lambda: self.step(-1))
        self.b_next.clicked.connect(lambda: self.step(1))
        self.b_back.clicked.connect(lambda: self.seek_rel(-10))
        self.b_fwd.clicked.connect(lambda: self.seek_rel(10))
        self.b_mute.clicked.connect(self.toggle_mute)
        self.vol.valueChanged.connect(self._vol_changed)
        self.b_loop.toggled.connect(lambda on: self.video._set("loop-file", "inf" if on else "no"))
        self.b_speed.clicked.connect(self.speed_menu)
        self.b_sub.clicked.connect(self.sub_menu)
        self.b_aud.clicked.connect(self.audio_menu)
        self.b_eq.clicked.connect(self.eq_popup)
        self.b_adj.clicked.connect(self.picture_popup)
        self.b_open.clicked.connect(self.open_files)
        self.b_full.clicked.connect(self.fullscreen_toggle)
        self.seek.seek.connect(self.seek_abs)
        self.seek.scrub.connect(lambda t: self.t_cur.setText(fmt_time(t)))
        self.video.prop.connect(self._prop)
        self.video.file_loaded.connect(self._file_loaded)
        self.video.end_file.connect(self._end_file)
        self.video.mouse.connect(self._mouse)
        self.pl.changed.connect(self._sync_buttons)

        self.origin_for = lambda path: ""       # set by the main window: page a downloaded file came from
        self.fit_next = False                   # resize the window to the next video's aspect ratio
        self.fsbar = FullscreenBar(self)
        # a fully transparent layered window gets no mouse input and mpv doesn't always report motion,
        # so in full screen the cursor position itself is watched
        self._last_cursor = None
        self.cursor_timer = QTimer(self, interval=120, timeout=self._watch_cursor)
        theme.changed.connect(self._theme_changed)
        self._theme_changed()

        self.click_timer = QTimer(self, singleShot=True, interval=230, timeout=self.toggle)
        self.osd_timer = QTimer(self, singleShot=True, interval=2600, timeout=self._idle)
        self.save_timer = QTimer(self, interval=5000, timeout=self._save_pos)
        self.save_timer.start()
        # mpv reports initial values before these slots are connected; read them now and re-read
        # the core state periodically so the UI can't stay stale after a missed event
        self.sync_timer = QTimer(self, interval=700, timeout=self._resync)
        self.sync_timer.start()
        self.info_tags = []
        self._resync(full=True)
        self._sync_buttons()

    def _resync(self, full=False):
        names = ("pause", "time-pos", "duration", "speed", "mute", "volume")
        if full:
            names += ("track-list", "chapter-list", "video-params", "hwdec-current")
        for n in names:
            v = self.video.get(n)
            if v is not None:
                self._prop(n, v)

    # ----- loading -----
    def open_files(self):
        files, _ = QFileDialog.getOpenFileNames(self, "Открыть медиафайлы", "", file_filter())
        if files:
            self.add_and_play([Item(f) for f in files])

    def add_and_play(self, items):
        if not items:
            return
        start = self.pl.add(items)
        self.play_index(start)

    def play_index(self, i):
        if not 0 <= i < len(self.pl.items):
            return
        self._save_pos()
        self.pl.set_index(i)
        it = self.pl.current
        self.loaded = False
        self.stack.setCurrentIndex(1)
        self.title_changed.emit(it.title)
        start = self.history.position(it) if cfg["resume"] else 0
        if it.page_url and it.source == "yt-dlp":
            self.video.show_text("Получаю поток…", 4000)
            m = re.search(r"height<=(\d+)", it.format or "")
            screen_h = self.screen().geometry().height() * self.screen().devicePixelRatio()
            fmt = ytdl.stream_format(min(int(m.group(1)), screen_h) if m else screen_h)
            threading.Thread(target=self._resolve_and_play, args=(it, start, fmt), daemon=True).start()
        elif it.is_url:
            self.video.load(proxied(it.path, it.headers), start=start, headers=it.headers,
                            audio_url=proxied(it.audio_url, it.headers) if it.audio_url else "")
        else:
            self.video.load(it.path, start=start)
        if start:
            self.toast.emit(f"Продолжаем с {fmt_time(start)}")
        self.history.touch(it, origin=None if it.is_url else self.origin_for(it.path))

    def _resolve_and_play(self, it, start, fmt):
        try:
            url, aurl, headers, title, dur = ytdl.resolve(it.page_url, fmt)
        except Exception as e:
            msg = str(e).replace("ERROR: ", "")[:160]
            self.toast.emit("Поток не открылся: " + msg)
            return
        if self.pl.current is not it:
            return
        if title and it.title == it.page_url:
            it.title = title
        # direct googlevideo/CDN files go through the chunking proxy; manifests stay with mpv
        self.video.load(proxied(url, headers), start=start, headers=headers,
                        audio_url=proxied(aurl, headers) if aurl else "")

    def step(self, d):
        if self.pl.items:
            j = self.pl.index + d
            if 0 <= j < len(self.pl.items):
                self.play_index(j)
                self.video.show_text("Следующий" if d > 0 else "Предыдущий")

    # ----- transport -----
    def toggle(self):
        if not self.pl.current:
            return self.open_files()
        if not self.loaded and self.stack.currentIndex() == 0:
            return self.play_index(self.pl.index)
        if self.paused and (self.video.get("eof-reached") or (self.dur and self.pos >= self.dur - 0.3)):
            self.video.command("seek", 0, "absolute")       # ▶ at the end starts the file over
        self.video._set("pause", not self.paused)
        self.video.show_text("Пауза" if not self.paused else "▶", 600)

    def seek_rel(self, s):
        if self.loaded:
            self.video.command("seek", s, "relative+exact" if abs(s) < 30 else "relative")
            self.video.show_text(f"{'+' if s > 0 else '−'}{abs(s)} с", 500)

    def seek_abs(self, t):
        if self.loaded:
            self.video.command("seek", t, "absolute+keyframes")

    def toggle_mute(self):
        self.video._set("mute", not self.video.get("mute", False))

    def change_volume(self, d):
        self.vol.setValue(max(0, min(150, self.vol.value() + d)))

    def _vol_changed(self, v):
        self.video._set("volume", v)
        if self.video.get("mute"):
            self.video._set("mute", False)
        self.video.show_text(f"Громкость {v}%", 600)
        cfg.data["volume"] = v

    def set_speed(self, s):
        self.video._set("speed", s)
        self.video.show_text(f"Скорость {s:g}×", 600)

    def speed_step(self, d):
        cur = self.video.get("speed", 1.0)
        idx = min(range(len(SPEEDS)), key=lambda i: abs(SPEEDS[i] - cur))
        self.set_speed(SPEEDS[max(0, min(len(SPEEDS) - 1, idx + d))])

    # ----- menus -----
    def _menu_at(self, menu, btn):
        menu.adjustSize()
        g = btn.mapToGlobal(btn.rect().topRight())
        menu.exec(g.__class__(g.x() - menu.sizeHint().width(), g.y() - menu.sizeHint().height() - 6))

    def speed_menu(self):
        m = QMenu(self)
        grp = QActionGroup(m)
        cur = self.video.get("speed", 1.0)
        for s in SPEEDS:
            a = m.addAction(f"{s:g}×" + ("   обычная" if s == 1 else ""))
            a.setCheckable(True)
            a.setChecked(abs(s - cur) < 0.01)
            grp.addAction(a)
            a.triggered.connect(lambda _=False, s=s: self.set_speed(s))
        self._menu_at(m, self.b_speed)

    def _track_menu(self, kind, btn):
        m = QMenu(self)
        grp = QActionGroup(m)
        tracks = [t for t in self.tracks if t.get("type") == kind]
        if kind == "sub":
            off = m.addAction("Выключены")
            off.setCheckable(True)
            off.setChecked(not any(t.get("selected") for t in tracks))
            grp.addAction(off)
            off.triggered.connect(lambda: self.video._set("sid", "no"))
        for t in tracks:
            a = m.addAction(track_label(t))
            a.setCheckable(True)
            a.setChecked(bool(t.get("selected")))
            grp.addAction(a)
            prop = "aid" if kind == "audio" else "sid"
            a.triggered.connect(lambda _=False, tid=t["id"], p=prop: self.video._set(p, tid))
        if not tracks and kind == "audio":
            m.addAction("Нет аудиодорожек").setEnabled(False)
        m.addSeparator()
        if kind == "sub":
            m.addAction("Загрузить файл субтитров…", self._load_sub)
            sz = m.addMenu("Размер субтитров")
            for name, scale in (("Мелкий", 0.8), ("Обычный", 1.0), ("Крупный", 1.25), ("Очень крупный", 1.6)):
                sz.addAction(name, lambda s=scale: self.video._set("sub-scale", s))
            dl = m.addMenu("Задержка субтитров")
            for d in (-1.0, -0.1, 0.1, 1.0):
                dl.addAction(f"{d:+.1f} с", lambda d=d: self._delay("sub-delay", d))
            dl.addAction("Сбросить", lambda: self._delay("sub-delay", None))
        else:
            m.addAction("Загрузить внешнюю дорожку…", self._load_audio)
            dl = m.addMenu("Задержка звука")
            for d in (-0.5, -0.1, -0.05, 0.05, 0.1, 0.5):
                dl.addAction(f"{d * 1000:+.0f} мс", lambda d=d: self._delay("audio-delay", d))
            dl.addAction("Сбросить", lambda: self._delay("audio-delay", None))
        self._menu_at(m, btn)

    def _delay(self, prop, d):
        v = 0.0 if d is None else (self.video.get(prop, 0.0) or 0.0) + d
        self.video._set(prop, v)
        self.video.show_text(("Субтитры" if prop.startswith("sub") else "Звук") + f": задержка {v * 1000:+.0f} мс", 900)

    def sub_menu(self):
        self._track_menu("sub", self.b_sub)

    def audio_menu(self):
        self._track_menu("audio", self.b_aud)

    def _load_sub(self):
        f, _ = QFileDialog.getOpenFileName(self, "Файл субтитров", "", "Субтитры (*.srt *.ass *.ssa *.vtt *.sub *.sup *.idx *.smi);;Все файлы (*)")
        if f:
            self.video.command("sub-add", f, "select")

    def _load_audio(self):
        f, _ = QFileDialog.getOpenFileName(self, "Аудиодорожка", "", "Аудио (*.mka *.ac3 *.eac3 *.dts *.flac *.aac *.m4a *.mp3 *.opus *.thd *.wav);;Все файлы (*)")
        if f:
            self.video.command("audio-add", f, "select")

    def eq_popup(self):
        p = EqPopup(self)
        p.show_above(self.b_eq)

    def picture_popup(self):
        p = Popup(self)
        p.card.setMinimumWidth(300)
        p.v.addWidget(label("ИЗОБРАЖЕНИЕ", "sec"))
        sliders = []
        from PySide6.QtWidgets import QGridLayout
        g = QGridLayout()
        for r, (prop, name) in enumerate((("brightness", "Яркость"), ("contrast", "Контраст"),
                                          ("saturation", "Насыщенность"), ("gamma", "Гамма"), ("hue", "Оттенок"))):
            s = ThinSlider(Qt.Horizontal)
            s.setRange(-100, 100)
            s.setValue(int(self.video.get(prop, 0) or 0))
            out = label(str(s.value()), "out")
            out.setMinimumWidth(28)
            s.valueChanged.connect(lambda val, pr=prop, o=out: (self.video._set(pr, val), o.setText(str(val))))
            g.addWidget(label(name), r, 0)
            g.addWidget(s, r, 1)
            g.addWidget(out, r, 2)
            sliders.append(s)
        p.v.addLayout(g)
        row = QHBoxLayout()
        asp = push("Пропорции")
        am = QMenu(asp)
        for name, val in (("Как в файле", "-1"), ("16:9", "16:9"), ("4:3", "4:3"), ("21:9", "2.33"), ("1:1", "1")):
            am.addAction(name, lambda v=val: self.video._set("video-aspect-override", v))
        asp.setMenu(am)
        shot = push("Снимок экрана")
        shot.clicked.connect(lambda: (self.video.command("screenshot"), self.toast.emit("Снимок сохранён на рабочий стол")))
        reset = push("Сбросить")
        reset.clicked.connect(lambda: [s.setValue(0) for s in sliders])
        row.addWidget(asp)
        row.addWidget(shot)
        row.addStretch()
        row.addWidget(reset)
        p.v.addLayout(row)
        p.show_above(self.b_adj)

    # ----- mpv events -----
    def _prop(self, name, value):
        if name == "time-pos" and value is not None:
            self.pos = value
            self.seek.set_state(pos=value)
            if not self.seek.drag:
                self.t_cur.setText(fmt_time(value))
        elif name == "duration":
            self.dur = value or 0
            self.seek.set_state(dur=self.dur)
            self.t_dur.setText(fmt_time(self.dur))
        elif name == "pause":
            self.paused = bool(value)
            self._play_icon()
        elif name == "idle-active" and value:
            self.loaded = False
            self._play_icon()
        elif name == "demuxer-cache-time" and value:
            self.seek.set_state(buf=value)
        elif name == "track-list":
            self.tracks = value or []
            self._update_tags()
        elif name == "chapter-list":
            self.seek.chapters = [c.get("time", 0) for c in (value or [])]
            self.seek.update()
        elif name in ("mute", "volume"):
            muted = self.video.get("mute", False)
            icons.set_name(self.b_mute, "mute" if muted or self.vol.value() == 0 else "vol")
        elif name == "speed" and value:
            self.b_speed.setText(f"{value:.1f}×" if float(value).is_integer() else f"{value:g}×")
        elif name in ("video-params", "hwdec-current"):
            self._update_tags()
        elif name == "media-title" and value and self.pl.current and self.pl.current.title in ("", self.pl.current.path):
            self.pl.current.title = value
            self.title_changed.emit(value)

    def _play_icon(self):
        playing = self.loaded and not self.paused
        name = "pause" if playing else "play"
        if getattr(self.b_play, "_icon_spec", (None,))[0] != name:
            icons.set_name(self.b_play, name)
            self.b_play.setToolTip(("Пауза" if playing else "Воспроизвести") + " (Пробел)")

    def _file_loaded(self):
        self.loaded = True
        self._play_icon()
        if self.fit_next:
            self.fit_next = False
            QTimer.singleShot(250, lambda: self._emit_fit(6))
        it = self.pl.current
        self.tracks = self.video.get("track-list") or self.tracks     # the queued event may still hold the previous file
        vids = [t for t in self.tracks if t.get("type") == "video"]
        if not vids or all(t.get("albumart") for t in vids):
            if not vids and (self.video.get("aid") not in (None, False, "no")):
                self.video.audio_visual(True)
        if it:
            self.history.touch(it, dur=self.video.get("duration", 0))

    def _emit_fit(self, tries):
        """Ask the window to take the video's proportions (online viewing), once mpv knows the display size."""
        vp = self.video.get("video-params") or {}
        w, h = vp.get("dw") or vp.get("w"), vp.get("dh") or vp.get("h")
        if w and h:
            self.fit_video.emit(int(w), int(h))
        elif tries:
            QTimer.singleShot(300, lambda: self._emit_fit(tries - 1))

    def _end_file(self, reason):
        if reason == "error":
            self.toast.emit("Не удалось открыть")
            self.loaded = False
            self.title_changed.emit("")
        elif reason == "eof":
            self._save_pos(final=True)
            if self.pl.index < len(self.pl.items) - 1 and not self.b_loop.isChecked():
                QTimer.singleShot(0, lambda: self.step(1))

    def _mouse(self, what):
        if what == "click":
            self.click_timer.start()
        elif what == "dblclick":
            self.click_timer.stop()
            self.fullscreen_toggle.emit()
        elif what == "wheel_up":
            self.change_volume(5)
        elif what == "wheel_down":
            self.change_volume(-5)
        if what in ("click", "dblclick"):
            self.window().activateWindow()
            self.setFocus(Qt.MouseFocusReason)      # keep keyboard focus in Qt, not in mpv's native window

    def _show_osd(self):
        """Full screen: show the floating bar (it hides again once the pointer leaves its zone)."""
        if self.fullscreen:
            self.fsbar.reveal()
            self.osd_timer.start()

    def _in_bar_zone(self, pos):
        """Activation zone: the bar itself plus a strip above it, on the player's own screen only."""
        g = self.fsbar.geometry().adjusted(-40, -90, 40, 40)
        return g.contains(pos)

    def _idle(self):
        if self.fullscreen and self._in_bar_zone(QCursor.pos()):
            self.osd_timer.start()
            return
        if self.fullscreen:
            self.fsbar.fade_out()

    def set_fullscreen(self, on):
        """In full screen the transport floats over the picture, so the video never jumps when it appears."""
        if on == self.fullscreen:
            return
        self.fullscreen = on
        lay = self.layout()
        if on:
            lay.removeWidget(self.controls)
            self.fsbar.attach(self.controls, self.window())
            self.fsbar.reveal()         # shown briefly on entry, then only when the pointer comes to it
        else:
            self.fsbar.detach()
            lay.addWidget(self.controls)
            self.controls.show()
        icons.set_name(self.b_full, "unfull" if on else "full")
        self.video.command("set", "cursor-autohide", "1000")
        self._last_cursor = QCursor.pos()
        self.cursor_timer.start() if on else self.cursor_timer.stop()
        self.osd_timer.setInterval(700)
        if on:
            QTimer.singleShot(2000, self._idle)

    def _watch_cursor(self):
        pos = QCursor.pos()
        if pos == self._last_cursor:
            return
        self._last_cursor = pos
        if self._in_bar_zone(pos):
            self._show_osd()
        elif self.fsbar.windowOpacity() > 0.01 and not self.osd_timer.isActive():
            self.osd_timer.start()

    def loaded_title(self):
        it = self.pl.current
        return it.title if it and self.loaded and self.stack.currentIndex() == 1 else ""

    def _theme_changed(self):
        self.empty_icon.setPixmap(icons.pixmap("film", theme.t["stage_fg"], 38))
        self.video._set("background-color", theme.t["stage"])
        self.video._set("background", "color")

    def _update_tags(self):
        tags = []
        vp = self.video.get("video-params") or {}
        vt = next((t for t in self.tracks if t.get("type") == "video" and t.get("selected") and not t.get("albumart")), None)
        if vt:
            codec = codec_short(vt)
            fmt = (vp.get("hw-pixelformat") or vp.get("pixelformat") or "").lower()   # GPU surfaces report "d3d11"
            # p010/yuv420p10le -> 10-bit, p012/yuv420p12le -> 12-bit; nv12 is 8-bit despite the "12"
            depth = ("12-bit" if re.search(r"p0?12|12[lb]e", fmt) else
                     "10-bit" if re.search(r"p0?10|10[lb]e|y210|x2rgb10", fmt) else "")
            tags.append(" ".join(x for x in (codec[:18], depth) if x))
            if vp.get("w"):
                tags.append(f"{vp['w']}×{vp['h']}")
            gamma = vp.get("gamma") or ""
            hdr = {"pq": "HDR10", "hlg": "HLG"}.get(gamma, "")
            if vt.get("dolby-vision-profile"):
                hdr = f"Dolby Vision P{vt['dolby-vision-profile']}" + (" · HDR10" if gamma == "pq" else "")
            if hdr:
                tags.append(hdr)
        at = next((t for t in self.tracks if t.get("type") == "audio" and t.get("selected")), None)
        if at:
            tags.append(track_label(at).split(" · ")[-1])
        st = next((t for t in self.tracks if t.get("type") == "sub" and t.get("selected")), None)
        if st:
            tags.append(codec_short(st) or "SUB")
        hw = self.video.get("hwdec-current") or ""
        if vt:
            tags.append(f"GPU · {hw.upper()}" if hw and hw != "no" else "CPU-декодер")
        self.info_tags = tags

    def _tags_ass(self):
        txt = r"\h\h\h".join(self.info_tags).replace("{", "(").replace("}", ")")
        return (r"{\an9\pos(1270,14)\fnCascadia Mono\fs17\bord3\shad0\1c&HE6E6E6&\3c&H101010&\3a&H30&\b1}"
                + txt)

    def _save_pos(self, final=False):
        it = self.pl.current
        if it and self.loaded and self.dur:
            self.history.touch(it, pos=self.dur if final else self.pos, dur=self.dur)

    def _sync_buttons(self):
        has = bool(self.pl.items)
        if not has:
            self.stack.setCurrentIndex(0)
            self.video.stop()
            self.title_changed.emit("")

    def shutdown(self):
        self.fsbar.detach()
        self._save_pos()
        cfg.save()
        self.video.shutdown()
