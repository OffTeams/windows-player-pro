"""Download options window: folder, what to fetch, video and sound chosen separately, audio track,
subtitles (several languages), container with the expected file size."""
import threading
from urllib.parse import parse_qs, urlparse

import requests
from PySide6.QtCore import QRect, Qt, Signal
from PySide6.QtGui import QImage, QPixmap, QStandardItem, QStandardItemModel
from PySide6.QtWidgets import (QComboBox, QDialog, QFileDialog, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
                               QStyle, QStyleOptionComboBox, QStylePainter, QVBoxLayout, QWidget)

from ..appicon import app_icon
from ..config import cfg
from ..net.common import ACODEC, UA, VCODEC, Option, codec_name
from ..net.downloads import folder_problem
from ..net.ytdl import AUDIO_SEL
from .widgets import Switch, fmt_size, fmt_time, label, push

LANG_NAMES = {"ru": "Русский", "en": "Английский", "uk": "Украинский", "de": "Немецкий", "fr": "Французский",
              "es": "Испанский", "it": "Итальянский", "ja": "Японский", "ko": "Корейский", "zh": "Китайский",
              "pt": "Португальский", "pl": "Польский", "tr": "Турецкий", "ar": "Арабский", "hi": "Хинди",
              "kk": "Казахский", "be": "Белорусский", "nl": "Нидерландский", "sv": "Шведский", "cs": "Чешский"}
AUDIO_FORMATS = [("", "Как в источнике"), ("mp3", "MP3"), ("m4a", "M4A"), ("opus", "Opus"), ("flac", "FLAC"), ("wav", "WAV")]
VIDEO_CONTAINERS = [("auto", "Автоматически"), ("mp4", "MP4"), ("mkv", "MKV"), ("webm", "WebM")]


def lang_name(code):
    if not code:
        return "Без указания языка"
    base = code.split("-")[0].lower()
    return LANG_NAMES.get(base, code)


def _size(f, dur):
    if not f:
        return 0
    s = f.get("filesize") or f.get("filesize_approx")
    if not s and f.get("tbr") and dur:
        s = f["tbr"] * 1000 / 8 * dur
    return s or 0


class CheckCombo(QComboBox):
    """Drop-down list where several items can be ticked (subtitle languages)."""
    changed = Signal()

    def __init__(self, placeholder):
        super().__init__()
        self.placeholder = placeholder
        self.setModel(QStandardItemModel(self))
        self.view().pressed.connect(self._toggle)
        self._keep = False

    def add(self, text, data):
        it = QStandardItem(text)
        it.setData(data, Qt.UserRole)
        it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable)
        it.setData(Qt.Unchecked, Qt.CheckStateRole)
        self.model().appendRow(it)

    def _toggle(self, idx):
        it = self.model().itemFromIndex(idx)
        it.setCheckState(Qt.Unchecked if it.checkState() == Qt.Checked else Qt.Checked)
        self._keep = True
        self.changed.emit()
        self.update()

    def hidePopup(self):
        if self._keep:          # a click ticks an item and keeps the list open
            self._keep = False
            return
        super().hidePopup()

    def checked(self):
        m = self.model()
        return [m.item(i) for i in range(m.rowCount()) if m.item(i).checkState() == Qt.Checked]

    def paintEvent(self, _):
        p = QStylePainter(self)
        opt = QStyleOptionComboBox()
        self.initStyleOption(opt)
        names = [it.text() for it in self.checked()]
        opt.currentText = ", ".join(names) if names else self.placeholder
        p.drawComplexControl(QStyle.CC_ComboBox, opt)
        p.drawControl(QStyle.CE_ComboBoxLabel, opt)


class DownloadDialog(QDialog):
    queued = Signal(object)         # ProbeResult whose download was started
    _thumb = Signal(bytes)

    def __init__(self, res, downloads, parent=None):
        super().__init__(None, Qt.Window)
        self.res, self.dm = res, downloads
        self.info = res.info or {}
        self.dur = self.info.get("duration") or 0
        self.setWindowTitle("Загрузка")
        self.setWindowIcon(app_icon())
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setMinimumWidth(560)

        v = QVBoxLayout(self)
        v.setContentsMargins(20, 18, 20, 16)
        v.setSpacing(12)

        head = QHBoxLayout()
        head.setSpacing(12)
        self.pic = QLabel()
        self.pic.setFixedSize(112, 63)
        self.pic.setStyleSheet("background: rgba(127,127,127,40); border-radius: 4px;")
        head.addWidget(self.pic, 0, Qt.AlignTop)
        tcol = QVBoxLayout()
        tcol.setSpacing(2)
        name = res.title or res.page_url
        title = label(name if len(name) <= 110 else name[:108] + "…", wrap=True)
        title.setStyleSheet("font-size: 11pt; font-weight: 600;")
        tcol.addWidget(title)
        ref = parse_qs(urlparse(res.page_url).fragment).get("ref")     # an embedded player: name the site, not the player
        host = (urlparse(ref[0] if ref else res.page_url).hostname or "").removeprefix("www.")
        tcol.addWidget(label(" · ".join(x for x in (host, fmt_time(self.dur) if self.dur else "") if x), "desc"))
        tcol.addStretch()
        head.addLayout(tcol, 1)
        v.addLayout(head)
        thumb = self.info.get("thumbnail") or res.thumbnail
        self.pic.setVisible(bool(thumb))
        if thumb:
            self._thumb.connect(self._set_thumb)
            threading.Thread(target=self._fetch, args=(thumb,), daemon=True).start()

        self.fmts = [f for f in (self.info.get("formats") or []) if f.get("protocol") != "mhtml"]
        self.videos = sorted([f for f in self.fmts if f.get("vcodec") not in (None, "none") and f.get("height")],
                             key=lambda f: (f.get("height") or 0, f.get("fps") or 0, f.get("tbr") or 0), reverse=True)
        self.audios = sorted([f for f in self.fmts if f.get("acodec") not in (None, "none") and f.get("vcodec") in (None, "none")],
                             key=lambda f: (f.get("abr") or f.get("tbr") or 0), reverse=True)
        self.yt = bool(self.videos or self.audios)
        self.episodes = self.info.get("wpp_episodes") or []
        self.voices = self.info.get("wpp_voices") or {}

        card = QFrame()
        card.setObjectName("card")
        self.grid = QGridLayout(card)
        self.grid.setContentsMargins(14, 12, 14, 12)
        self.grid.setHorizontalSpacing(14)
        self.grid.setVerticalSpacing(8)
        self.grid.setColumnStretch(1, 1)
        self.grid.setColumnMinimumWidth(0, 130)
        self.rows = {}

        self.folder = QLineEdit(cfg["dl_dir"])
        browse = push("Обзор…")
        browse.clicked.connect(self._browse)
        fw = QWidget()
        fl = QHBoxLayout(fw)
        fl.setContentsMargins(0, 0, 0, 0)
        fl.setSpacing(6)
        fl.addWidget(self.folder, 1)
        fl.addWidget(browse)
        self._row("folder", "Папка", fw)
        self.folder_state = label("", "err", wrap=True)
        self.grid.addWidget(self.folder_state, self.grid.rowCount(), 1)
        self.folder.textChanged.connect(self._check_folder)

        if self.yt:
            self._build_tracks()
        else:
            self._build_direct()
        v.addWidget(card)

        foot = QHBoxLayout()
        self.summary = label("", "desc")
        foot.addWidget(self.summary, 1)
        cancel = push("Отмена")
        cancel.clicked.connect(self.close)
        self.ok = push("Скачать", "dl", primary=True)
        self.ok.clicked.connect(self._go)
        foot.addWidget(cancel)
        foot.addWidget(self.ok)
        v.addLayout(foot)
        self._check_folder()
        self._update()
        self.adjustSize()

    # ----- helpers
    def _row(self, key, name, widget):
        r = self.grid.rowCount()
        lab = label(name)
        self.grid.addWidget(lab, r, 0)
        self.grid.addWidget(widget, r, 1)
        self.rows[key] = (lab, widget)
        return widget

    def _show(self, key, on):
        for w in self.rows[key]:
            w.setVisible(on)

    def _fetch(self, url):
        try:
            r = requests.get(url, headers={"User-Agent": UA}, timeout=8)
            if r.ok:
                self._thumb.emit(r.content)
        except Exception:
            pass

    def _set_thumb(self, data):
        img = QImage.fromData(data)
        if img.isNull():
            return
        dpr = self.devicePixelRatioF()
        w, h = int(112 * dpr), int(63 * dpr)
        pm = QPixmap.fromImage(img).scaled(w, h, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
        pm = pm.copy(QRect((pm.width() - w) // 2, (pm.height() - h) // 2, w, h))
        pm.setDevicePixelRatio(dpr)
        self.pic.setPixmap(pm)

    def _browse(self):
        d = QFileDialog.getExistingDirectory(self, "Папка", self.folder.text())
        if d:
            self.folder.setText(d.replace("/", "\\"))

    def _check_folder(self):
        why = folder_problem(self.folder.text())
        if not why:
            self.folder_state.hide()
            return
        backup_ok = not folder_problem(cfg["dl_backup"])
        self.folder_state.setText(f"Недоступна: {why}" + (". Файл сохранится в резервную папку" if backup_ok else ""))
        self.folder_state.setObjectName("warn" if backup_ok else "err")
        self.folder_state.style().unpolish(self.folder_state)
        self.folder_state.style().polish(self.folder_state)
        self.folder_state.show()

    # ----- yt-dlp sources: video and sound chosen separately
    def _build_tracks(self):
        if self.episodes:
            self.episode = QComboBox()
            for e in self.episodes:
                self.episode.addItem(e["title"], [e])
            seasons = {}
            for e in self.episodes:
                seasons.setdefault(e["season"], []).append(e)
            if len(seasons) > 1:
                for s, eps in seasons.items():
                    self.episode.addItem(f"Весь {s} сезон · {len(eps)} сер.", eps)
            self.episode.addItem(f"Все серии · {len(self.episodes)}", list(self.episodes))
            cur = self.info.get("wpp_current")
            self.episode.setCurrentIndex(next((i for i, e in enumerate(self.episodes) if e["url"] == cur), 0))
            self._row("episode", "Серия", self.episode)
            self.episode.currentIndexChanged.connect(self._update)
        self.mode = QComboBox()
        for i, text in enumerate(("Видео со звуком", "Только видео", "Только аудио")):
            self.mode.addItem(text, i)
        if not self.videos:
            self.mode.setCurrentIndex(2)
        elif cfg["quality"] == "audio":
            self.mode.setCurrentIndex(2)
        self._row("mode", "Скачать", self.mode)

        self.vq = QComboBox()
        self.vq.addItem("Лучшее", None)
        for f in self.videos:
            fps = int(f.get("fps") or 0)
            hdr = " HDR" if (f.get("dynamic_range") or "SDR") != "SDR" else ""
            parts = [f"{f['height']}p{fps if fps > 30 else ''}{hdr}", codec_name(f.get("vcodec"), VCODEC)]
            if f.get("tbr"):
                parts.append(f"{f['tbr'] / 1000:.1f} Мбит/с")
            self.vq.addItem(" · ".join(p for p in parts if p), f)
        self._row("video", "Видео", self.vq)

        self.lang = QComboBox()
        langs = []
        for f in self.audios:
            code = f.get("language") or ""
            if code not in langs:
                langs.append(code)
        if self.voices:             # voice-overs named by the player, listed in its own order
            langs = sorted(langs, key=lambda c: list(self.voices).index(c) if c in self.voices else 99)
            orig = None
        else:
            orig = next((f.get("language") for f in self.audios if "original" in (f.get("format_note") or "").lower()), None)
        for code in langs:
            name = self.voices.get(code) or lang_name(code)
            self.lang.addItem(name + (" · оригинал" if code and code == orig else ""), code)
        if orig in langs:
            self.lang.setCurrentIndex(langs.index(orig))
        self._row("lang", "Аудиодорожка", self.lang)
        self.aq = QComboBox()
        self._row("audio", "Звук", self.aq)

        subs = self.info.get("subtitles") or {}
        autos = self.info.get("automatic_captions") or {}
        self.subs = CheckCombo("Без субтитров")
        for code in sorted(subs):
            named = self.voices and (subs[code] or [{}])[0].get("name")     # the player names its tracks itself
            self.subs.add(named or lang_name(code), code)
        for code in ("ru", "en", "uk"):
            if code in autos and code not in subs:
                self.subs.add(lang_name(code) + " · автоматические", code)
        self._row("subs", "Язык субтитров", self.subs)
        self.embed = Switch(True)
        self._row("embed", "Встроить в видеофайл", self._left(self.embed))

        self.container = QComboBox()
        self._row("container", "Контейнер", self.container)
        self.abitrate = QComboBox()
        for kb in ("320", "256", "192", "160", "128"):
            self.abitrate.addItem(f"{kb} кбит/с", kb)
        self._row("bitrate", "Битрейт", self.abitrate)

        self._preselect()
        self.mode.currentIndexChanged.connect(self._update)
        self.vq.currentIndexChanged.connect(self._update)
        self.lang.currentIndexChanged.connect(self._fill_audio)
        self.aq.currentIndexChanged.connect(self._update)
        self.container.currentIndexChanged.connect(self._update_bitrate)
        self.abitrate.currentIndexChanged.connect(self._update)
        self.subs.changed.connect(self._update)
        self._fill_audio()

    @staticmethod
    def _left(w):
        box = QWidget()
        h = QHBoxLayout(box)
        h.setContentsMargins(0, 0, 0, 0)
        h.addWidget(w)
        h.addStretch()
        return box

    def _preselect(self):
        """Start from 'Видео по умолчанию' in the settings."""
        want = cfg["dl_video"]
        if want != "best":
            for i in range(1, self.vq.count()):
                if (self.vq.itemData(i).get("height") or 0) <= int(want):
                    self.vq.setCurrentIndex(i)
                    break

    def _fill_audio(self):
        code = self.lang.currentData() if self.lang.count() else None
        self.aq.blockSignals(True)
        self.aq.clear()
        self.aq.addItem("Лучший", None)
        pref = cfg["dl_audio"]
        pick = 0
        for f in self.audios:
            if self.lang.count() > 1 and (f.get("language") or "") != code:
                continue
            abr = int(f.get("abr") or f.get("tbr") or 0)
            ch = f.get("audio_channels")
            name = codec_name(f.get("acodec"), ACODEC)
            parts = [name, f"{abr} кбит/с" if abr else "", f"{ch} кан." if ch and ch > 2 else ""]
            self.aq.addItem(" · ".join(p for p in parts if p), f)
            if not pick and ((pref == "aac" and name == "AAC") or (pref == "opus" and name == "Opus")):
                pick = self.aq.count() - 1
        if pref == "low" and self.aq.count() > 1:
            pick = self.aq.count() - 1
        self.aq.setCurrentIndex(pick)
        self.aq.blockSignals(False)
        self._update()

    # ----- sniffed sources: pick one of the found streams
    def _build_direct(self):
        self.variant = QComboBox()
        for o in self.res.options:
            self.variant.addItem(" · ".join(x for x in (o.label, o.detail) if x), o)
        self._row("variant", "Вариант", self.variant)
        self.name = QLineEdit(self.res.title or "")
        self._row("name", "Имя файла", self.name)
        self.container = QComboBox()
        self._row("container", "Контейнер", self.container)
        self.variant.currentIndexChanged.connect(self._update)

    # ----- result
    def _selector(self):
        """yt-dlp format selector, estimated size, kind."""
        mode = self.mode.currentIndex()
        vf = self.vq.currentData() if mode in (0, 1) else None
        af = self.aq.currentData() if mode in (0, 2) else None
        code = self.lang.currentData() if self.lang.count() > 1 else None
        lang_filter = f"[language^={code}]" if code else ""
        v = vf["format_id"] if vf else ("bv*" if cfg["dl_video"] == "best" else f"bv*[height<={cfg['dl_video']}]")
        a = af["format_id"] if af else AUDIO_SEL.get(cfg["dl_audio"], "ba").replace("ba", f"ba{lang_filter}", 1)
        if self.episodes:           # format ids differ from episode to episode: describe the choice instead
            v = f"bv*[height<={vf['height']}]" if vf else v
            a = f"ba[language={af.get('language')}]" if af and af.get("language") else (f"ba{lang_filter}" if af else a)
        if mode == 0:
            sel = v if vf and vf.get("acodec") not in (None, "none") else f"{v}+{a}/{v}+ba/b"
        elif mode == 1:
            sel = f"{v}/bv*/b"
        else:
            sel = f"{a}/ba/b"
        vbest = self.videos[0] if self.videos else None
        abest = self.audios[0] if self.audios else None
        vsize = _size(vf or vbest, self.dur) if mode in (0, 1) else 0
        size = vsize + (_size(af or abest, self.dur) if mode in (0, 2) else 0)
        if mode in (0, 1) and not vsize:     # video of unknown size: the sound alone would be a false total
            size = 0
        eps = self._chosen_episodes()
        if size and self.dur and len(eps) > 1:      # same bitrate, summed over the episodes' running time
            size = size / self.dur * sum(e.get("duration") or self.dur for e in eps)
        return sel, size, ("audio" if mode == 2 else "video")

    def _chosen_episodes(self):
        return self.episode.currentData() if self.episodes else []

    def _fill_containers(self, items, keep):
        self.container.blockSignals(True)
        self.container.clear()
        for val, text, size in items:
            self.container.addItem(f"{text} · ≈{fmt_size(size)}" if size else text, val)
        i = self.container.findData(keep)
        self.container.setCurrentIndex(max(0, i))
        self.container.blockSignals(False)

    def _update(self, *_):
        if not hasattr(self, "summary"):
            return
        if not self.yt:
            o = self.variant.currentData()
            keep = self.container.currentData() or "auto"
            self._fill_containers([(v, t, o.size if o else 0) for v, t in
                                   (("auto", "Как в источнике"), ("mp4", "MP4"), ("mkv", "MKV"))], keep)
            self.summary.setText("")
            return
        mode = self.mode.currentIndex()
        self._show("video", mode in (0, 1))
        self._show("lang", mode in (0, 2) and self.lang.count() > 1)
        self._show("audio", mode in (0, 2))
        has_subs = self.subs.model().rowCount() > 0
        self._show("subs", has_subs and mode != 2)
        self._show("embed", has_subs and mode != 2 and bool(self.subs.checked()))
        sel, size, kind = self._selector()
        if mode == 2:
            keep = self.container.currentData() if self.container.count() and self.container.itemData(0) == "" else ""
            items = []
            for val, text in AUDIO_FORMATS:
                if val in ("mp3", "m4a", "opus"):
                    est = int(self.abitrate.currentData() or 320) * 125 * self.dur
                elif val in ("flac", "wav"):
                    est = (1411 if val == "wav" else 900) * 125 * self.dur
                else:
                    est = size
                items.append((val, text, est))
            self._fill_containers(items, keep)
        else:
            keep = self.container.currentData() if self.container.count() and self.container.itemData(0) == "auto" \
                else cfg["dl_container"]
            self._fill_containers([(val, text, size) for val, text in VIDEO_CONTAINERS], keep)
        self._update_bitrate()

    def _update_bitrate(self, *_):
        if not self.yt:
            return
        lossy = self.mode.currentIndex() == 2 and self.container.currentData() in ("mp3", "m4a", "opus")
        self._show("bitrate", lossy)
        sel, size, kind = self._selector()
        self.summary.setText(f"≈{fmt_size(size)}" if size and not lossy else "")

    def _go(self):
        folder = self.folder.text().strip()
        if self.yt:
            sel, size, kind = self._selector()
            mode = self.mode.currentIndex()
            conv = self.container.currentData() if mode == 2 else ""
            subs = [it.data(Qt.UserRole) for it in self.subs.checked()] if mode != 2 else []
            label_ = " · ".join(x for x in (self.vq.currentText() if mode in (0, 1) else "",
                                            self.aq.currentText() if mode in (0, 2) else "") if x)
            merge = self.container.currentData() if mode in (0, 1) else ""
            targets = [(e["url"], e["title"], e.get("duration")) for e in self._chosen_episodes()] or \
                      [(self.res.page_url, self.res.title or self.res.page_url, self.dur)]
            total_dur = sum(d or self.dur for _, _, d in targets) or 1
            for url, title, dur in targets:
                share = size * (dur or self.dur) / total_dur if len(targets) > 1 else size
                opt = Option(label_, "", share, kind, sel, url, title=title,
                             extract_audio=conv or "", playlist=any(o.playlist for o in self.res.options))
                # WebM, which "automatic" picks for VP9/Opus, can't hold SRT subtitles
                self.dm.add(opt, self.res.source, url, folder=folder,
                            merge=merge if merge != "auto" else ("mkv" if subs and self.embed.isChecked() else ""),
                            subs=subs, embed_subs=self.embed.isChecked(),
                            audio_quality=self.abitrate.currentData() if conv in ("mp3", "m4a", "opus") else "")
        else:
            o = self.variant.currentData()
            if o is None:
                return self.close()
            opt = Option(o.label, o.detail, o.size, o.kind, o.format, o.url, dict(o.headers),
                         self.name.text().strip() or o.title, o.extract_audio, o.playlist, o.duration, o.direct)
            merge = self.container.currentData()
            self.dm.add(opt, self.res.source, self.res.page_url, folder=folder, merge="" if merge == "auto" else merge)
        self.queued.emit(self.res)
        self.close()
