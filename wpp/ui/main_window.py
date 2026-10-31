"""Main window: custom title bar, navigation rail, pages, tray icon, shortcuts and notifications."""
import ctypes
import html
import os
import sys

import yt_dlp
from PySide6.QtCore import QEvent, QPoint, Qt, QTimer
from PySide6.QtGui import QAction, QGuiApplication, QIcon, QKeySequence, QShortcut
from PySide6.QtWidgets import (QApplication, QButtonGroup, QFrame, QHBoxLayout, QLabel, QMainWindow, QMenu,
                               QPushButton, QStackedWidget, QSystemTrayIcon, QVBoxLayout, QWidget)

from .. import APP_NAME, autostart, icons, ipc, updater
from ..net import ytdl
from ..config import cfg
from ..library import MEDIA_EXT, SUB_EXT, History, Item, Playlist
from ..net.downloads import DownloadManager
from ..theme import theme
from .downloads_view import DownloadsView
from .lists_view import HistoryView, PlaylistView
from .notify import ClipboardWatcher, NotificationCenter
from .player_view import PlayerView
from .settings_view import SettingsView
from .widgets import Toast, icon_button, label
from .winfx import enable_taskbar_minimize

EDGE = 6


from ..appicon import app_icon  # noqa: E402  (OS-matched .ico)


class TitleBar(QFrame):
    def __init__(self, win):
        super().__init__()
        self.win = win
        self.setObjectName("titlebar")
        self.setFixedHeight(40)
        h = QHBoxLayout(self)
        h.setContentsMargins(12, 0, 0, 0)
        h.setSpacing(10)
        logo = QLabel()
        self.logo = logo
        brand = label(APP_NAME, "brand")
        self.sub = label("", "sub")
        self.sub.setMinimumWidth(20)
        from PySide6.QtWidgets import QSizePolicy
        self.sub.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        h.addWidget(logo)
        h.addWidget(brand)
        h.addWidget(self.sub, 1)
        h.addSpacing(6)
        self.b_min = icon_button("min", "Свернуть", 16)
        self.b_max = icon_button("max", "Развернуть", 14)
        self.b_close = icon_button("close", "Закрыть", 16, obj="close")
        for b in (self.b_min, self.b_max, self.b_close):
            b.setFixedSize(46, 40)
            h.addWidget(b)
        self.b_min.clicked.connect(win.showMinimized)
        self.b_max.clicked.connect(win.toggle_max)
        self.b_close.clicked.connect(win.close)

    def refresh(self):
        self.logo.setPixmap(app_icon().pixmap(20, 20))     # the app's own icon, same as the taskbar and shortcuts

    def set_sub(self, text):
        """The title bar shows only the app name; nothing is added next to it."""
        self.sub.setText("")

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.win.windowHandle().startSystemMove()

    def mouseDoubleClickEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.win.toggle_max()


class NavButton(QPushButton):
    def __init__(self, text, icon_name):
        super().__init__(text)
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.NoFocus)
        icons.bind(self, icon_name, 18, "fg")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 8, 0)
        lay.addStretch()
        self.badge = label("", "badge")
        self.badge.setFixedHeight(18)
        self.badge.setMinimumWidth(18)
        self.badge.setAlignment(Qt.AlignCenter)
        self.badge.hide()
        lay.addWidget(self.badge, 0, Qt.AlignVCenter)

    def set_badge(self, n):
        self.badge.setText(str(n))
        self.badge.setVisible(bool(n))

    def paintEvent(self, e):
        super().paintEvent(e)
        if self.isChecked():        # Settings-style accent pill on the selected item
            from PySide6.QtGui import QPainter
            from PySide6.QtCore import QRectF
            p = QPainter(self)
            p.setRenderHint(QPainter.Antialiasing)
            p.setPen(Qt.NoPen)
            p.setBrush(theme.c("accent"))
            p.drawRoundedRect(QRectF(0, self.height() / 2 - 8, 3, 16), 1.5, 1.5)


class MainWindow(QMainWindow):
    def __init__(self, files=()):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(app_icon())
        self.setWindowFlags(Qt.Window | Qt.FramelessWindowHint)
        self.setAcceptDrops(True)
        self.resize(1280, 780)
        self.setMinimumSize(720, 460)

        self.playlist = Playlist()
        self.history = History()
        self.dm = DownloadManager()
        self.upd = updater.Updater()

        root = QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)
        v = QVBoxLayout(root)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        self.titlebar = TitleBar(self)
        v.addWidget(self.titlebar)
        body = QHBoxLayout()
        body.setSpacing(0)
        v.addLayout(body, 1)

        self.nav = QFrame()
        self.nav.setObjectName("nav")
        self.nav.setFixedWidth(140)
        nv = QVBoxLayout(self.nav)
        nv.setContentsMargins(6, 8, 6, 8)
        nv.setSpacing(2)
        self.pages = QStackedWidget()
        body.addWidget(self.nav)
        body.addWidget(self.pages, 1)

        self.notes = NotificationCenter(self)
        self.toast = Toast(self)
        self.player = PlayerView(self.playlist, self.history)
        self.current_page = "player"
        self.clip = ClipboardWatcher(self.notes, {
            "download": lambda opt, res: self.dm.add(opt, res.source, res.page_url),
            "play": self.play_option,
            "options": self.open_options,
            "queue": lambda: (self.bring_to_front(), self.go("downloads")),
        })
        self.downloads = DownloadsView(self.dm)
        self.pl_view = PlaylistView(self.playlist, self.player)
        self.hist_view = HistoryView(self.history, origin_for=lambda p: self._download_origin(p))
        self.settings = SettingsView(self.upd, self.open_eq)

        self.group = QButtonGroup(self)
        self.navs = {}
        for key, text, ic, page in (("player", "Плеер", "play", self.player), ("playlist", "Плейлист", "list", self.pl_view),
                                    ("downloads", "Загрузки", "dl", self.downloads), ("history", "История", "hist", self.hist_view),
                                    ("settings", "Настройки", "gear", self.settings)):
            b = NavButton(text, ic)
            self.group.addButton(b)
            nv.addWidget(b)
            self.pages.addWidget(page)
            b.clicked.connect(lambda _=False, k=key: self.go(k))
            self.navs[key] = (b, page)
        nv.addStretch()
        mpv_v = (self.player.video.get("mpv-version") or "").split("-")[0].replace("mpv ", "")
        ff_v = (self.player.video.get("ffmpeg-version") or "").split("-g")[0]
        self.foot = label(f"libmpv {mpv_v}\nFFmpeg {ff_v}\nyt-dlp {yt_dlp.version.__version__}", "foot")
        self.foot.setContentsMargins(9, 0, 0, 0)    # same left edge as the button icons (padding 8 + border 1)
        nv.addWidget(self.foot)
        self.go("player")

        # signals
        self.player.title_changed.connect(self._title)
        self.player.fullscreen_toggle.connect(self.toggle_fullscreen)
        self.player.fit_video.connect(self.fit_to_video)
        self.player.toast.connect(self.show_toast)
        self.pl_view.play.connect(lambda i: (self.go("player"), self.player.play_index(i)))
        self.hist_view.resume.connect(self.resume_history)
        self.downloads.play_file.connect(self.play_file)
        self.player.origin_for = self._download_origin
        self.playlist.changed.connect(lambda: self.navs["playlist"][0].set_badge(len(self.playlist.items)))
        self.playlist.changed.connect(lambda: self._title(self.player.loaded_title()))
        self.dm.listed.connect(self._dl_badge)
        self.dm.updated.connect(lambda _id: self._dl_badge())
        self.dm.rerouted.connect(self._rerouted)
        self.dm.failed.connect(self._dl_failed)
        self.dm.completed.connect(self._dl_done)
        self.upd.available.connect(self._update_available)
        self.upd.applied.connect(self._update_applied)
        self.upd.status.connect(self._update_progress)
        self.upd.installer_ready.connect(self._installer_ready)
        theme.changed.connect(self._theme_changed)
        self.navs["playlist"][0].set_badge(len(self.playlist.items))
        self._dl_badge()
        self._theme_changed()
        self._shortcuts()
        self._tray()
        QApplication.instance().installEventFilter(self)
        self._resize_edge = None

        to_open = [Item(os.path.abspath(f)) for f in files if os.path.exists(f) or "://" in f]
        if to_open:
            QTimer.singleShot(300, lambda: self.player.add_and_play(to_open))
        if cfg["update_check"]:
            QTimer.singleShot(4000, lambda: self.upd.check(quiet=True))

        # commands from a second launch and from the background helper
        self.ipc = ipc.Server(ipc.PLAYER, self)
        self.ipc.message.connect(self._ipc_message)
        # while the helper runs it owns the clipboard watching; the player takes over if it stops
        self.helper_timer = QTimer(self, interval=4000, timeout=self._sync_helper)
        self.helper_timer.start()
        QTimer.singleShot(0, self._start_helper_if_needed)
        cfg.changed.connect(self._cfg_changed)
        ytdl.warmup()

    # ----- helper / IPC -----
    def _start_helper_if_needed(self):
        if cfg["helper"] and not ipc.is_running(ipc.HELPER):
            ipc.launch(ipc.helper_cmd())
            QTimer.singleShot(2500, self._sync_helper)
        else:
            self._sync_helper()
        try:
            autostart.refresh_command()
        except Exception:
            pass

    def _sync_helper(self):
        running = ipc.is_running(ipc.HELPER)
        self.clip.enabled = not running
        self.helper_running = running
        self._tell_helper_busy()

    def _tell_helper_busy(self):
        if getattr(self, "helper_running", False):
            ipc.send(ipc.HELPER, {"cmd": "busy", "value": bool(self.isActiveWindow() and self.current_page == "downloads")}, 200)

    def _cfg_changed(self, key):
        if getattr(self, "helper_running", False):
            ipc.send(ipc.HELPER, {"cmd": "reload"}, 300)

    def _ipc_message(self, msg):
        cmd = msg.get("cmd")
        if cmd == "open":
            files = [Item(f) for f in msg.get("files", []) if os.path.exists(f) or "://" in f]
            self.bring_to_front()
            if files:
                self.go("player")
                self.player.add_and_play(files)
        elif cmd == "show":
            self.bring_to_front()
            if msg.get("page"):
                self.go(msg["page"])
        elif cmd == "download":
            res = ipc.result_from_dict(msg["res"])
            self.dm.add(ipc.option_from_dict(msg["opt"]), res.source, res.page_url)
        elif cmd == "play":
            self.play_option(ipc.option_from_dict(msg["opt"]), ipc.result_from_dict(msg["res"]))
        elif cmd == "options":
            self.open_options(ipc.result_from_dict(msg["res"]))

    # ----- navigation -----
    def go(self, key):
        b, page = self.navs[key]
        b.setChecked(True)
        self.pages.setCurrentWidget(page)
        self.current_page = key
        # pull keyboard focus back from the native mpv window: otherwise Windows keeps sending
        # mouse-wheel messages to the hidden video and the new page won't scroll
        page.setFocusPolicy(Qt.ClickFocus)
        page.setFocus(Qt.OtherFocusReason)
        self._tell_helper_busy()
        self._title(self.player.loaded_title())

    def _title(self, t):
        """Title bar shows the playing media only (never the tab name)."""
        self.titlebar.set_sub(t or "")

    def _dl_badge(self):
        self.navs["downloads"][0].set_badge(self.dm.active())

    def show_toast(self, text):
        self.toast.show_text(text)

    # ----- actions -----
    def play_file(self, path, title=""):
        if path and os.path.exists(path):
            self.go("player")
            self.player.add_and_play([Item(path, title=os.path.basename(path))])
        else:
            self.show_toast("Файл не найден")

    def play_option(self, opt, res):
        if opt.direct:
            it = Item(opt.url, title=res.title or opt.title, headers=opt.headers, source="перехват", page_url="")
        else:
            it = Item(res.page_url, title=res.title or res.page_url, page_url=res.page_url, source="yt-dlp", format=opt.format)
        self.bring_to_front()
        self.go("player")
        self.player.fit_next = True
        self.player.add_and_play([it])

    def fit_to_video(self, vw, vh):
        """Size the window so the picture fills the player area without bars (window must be normal-sized)."""
        if self.isFullScreen() or self.isMaximized() or not vw or not vh:
            return
        stage = self.player.stack
        extra_w, extra_h = self.width() - stage.width(), self.height() - stage.height()
        avail = self.screen().availableGeometry()
        sw = stage.width()
        sh = round(sw * vh / vw)
        if sh + extra_h > avail.height() * 0.92:
            sh = int(avail.height() * 0.92) - extra_h
            sw = round(sh * vw / vh)
        if sw + extra_w > avail.width() * 0.96:
            sw = int(avail.width() * 0.96) - extra_w
            sh = round(sw * vh / vw)
        w, h = max(self.minimumWidth(), sw + extra_w), max(self.minimumHeight(), sh + extra_h)
        g = self.geometry()
        x = min(max(avail.left(), g.x()), avail.right() - w)
        y = min(max(avail.top(), g.y()), avail.bottom() - h)
        self.setGeometry(x, y, w, h)

    def _download_origin(self, path):
        p = os.path.normcase(os.path.abspath(path))
        t = next((t for t in self.dm.tasks if t.file and os.path.normcase(os.path.abspath(t.file)) == p), None)
        return (t.page_url or t.url) if t else ""

    def open_options(self, res):
        """Separate window with every download setting (folder, tracks, quality, format, subtitles)."""
        from shiboken6 import isValid
        from .download_dialog import DownloadDialog
        d = DownloadDialog(res, self.dm, self)
        # closed windows delete themselves; touching one of them raised an error and the next window never opened
        self._dialogs = [x for x in getattr(self, "_dialogs", []) if isValid(x) and x.isVisible()] + [d]
        d.show()
        d.raise_()
        d.activateWindow()
        if sys.platform == "win32":
            try:
                ctypes.windll.user32.SetForegroundWindow(int(d.winId()))
            except Exception:
                pass
        return d

    def open_in_downloads(self, url, res):
        self.bring_to_front()
        self.go("downloads")
        _ = url, res

    def resume_history(self, e):
        i = next((n for n, it in enumerate(self.playlist.items) if it.key == e["key"]), None)
        if i is None:
            it = Item(e["path"], title=e.get("title", ""), page_url=e.get("page_url", ""), source=e.get("source", ""),
                      format=e.get("format", ""))
            if not it.is_url and not os.path.exists(it.path):
                return self.show_toast("Файл не найден")
            i = self.playlist.add([it])
        self.go("player")
        self.player.play_index(i)

    def open_eq(self):
        self.go("player")
        QTimer.singleShot(50, self.player.eq_popup)

    def bring_to_front(self):
        if self.isMinimized() or not self.isVisible():
            self.showNormal()
        self.raise_()
        self.activateWindow()
        if sys.platform == "win32":
            try:
                ctypes.windll.user32.SetForegroundWindow(int(self.winId()))
            except Exception:
                pass

    def paste_link(self):
        text = QGuiApplication.clipboard().text()
        if not self.clip.check(text, force=True):
            self.show_toast("В буфере обмена нет ссылки")

    # ----- download notifications -----
    def _rerouted(self, t, why):
        self.notes.show("Загрузка идёт в резервную папку",
                        f"<b>{html.escape(t.folder)}</b>",
                        "alert", "warn", [("Настройки", lambda _n: (self.bring_to_front(), self.go("settings"), None)[-1])], timeout=10000)

    def _dl_failed(self, t):
        self.notes.show("Загрузка не удалась", f"«{html.escape(t.title)}»<br>{html.escape(t.error)}", "alert", "err",
                        [("Загрузки", lambda _n: (self.bring_to_front(), self.go("downloads"), None)[-1])], timeout=12000)

    def _dl_done(self, t):
        acts = [("Воспроизвести", lambda _n, t=t: (self.bring_to_front(), self.play_file(t.file, t.title), None)[-1], True, "play")]
        if t.file:
            import subprocess
            acts.append(("Показать в папке", lambda _n, t=t: (subprocess.Popen(["explorer", "/select,", os.path.normpath(t.file)]), None)[-1]))
        self.notes.show("Скачано", f"«{html.escape(t.title)}»<br>{html.escape(t.folder)}" +
                        (" (резервная папка)" if t.backup else ""), "ok", "ok", acts, timeout=20000)

    def _update_available(self, n, log):
        def start(note):
            self._upd_note = note
            note.set("Загружаю обновление…", "", "refresh")
            self.upd.apply()
            return True             # keep the card: it shows the progress
        self.notes.show("Доступно обновление плеера", html.escape(log[:500]).replace("\n", "<br>"), "refresh", "",
                        [("Обновить", start, True), ("Позже", None)], on_top=False)

    def _update_progress(self, text, tone):
        note = getattr(self, "_upd_note", None)
        if note is not None and note.isVisible() and text.startswith("Загружаю"):
            note.title.setText(text)
        elif note is not None and note.isVisible() and tone == "err":
            note.set("Обновление не удалось", html.escape(text), "alert", "err", [("Закрыть", None)])

    def _installer_ready(self, path):
        def install(_n):
            self.shutdown()
            updater.run_installer(path)
        self.notes.show("Обновление готово к установке", "История, плейлист и загрузки сохранятся.", "ok", "ok",
                        [("Установить и перезапустить", install, True), ("Позже", None)], on_top=False)

    def _update_applied(self, summary):
        self.notes.show("Обновление установлено", "",
                        "ok", "ok", [("Перезапустить", lambda _n: (self.shutdown(), updater.restart()), True), ("Позже", None)],
                        on_top=False)

    # ----- window chrome -----
    def _theme_changed(self):
        icons.refresh_all()
        self.titlebar.refresh()
        self._dwm()

    def _dwm(self):
        if sys.platform != "win32":
            return
        try:
            hwnd = int(self.winId())
            pref = ctypes.c_int(2)          # DWMWCP_ROUND
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 33, ctypes.byref(pref), 4)
            dark = ctypes.c_int(1 if theme.dark else 0)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(dark), 4)
        except Exception:
            pass
        enable_taskbar_minimize(self)      # taskbar button click minimizes/restores like a normal window

    def toggle_max(self):
        if self.isFullScreen():
            return
        self.showNormal() if self.isMaximized() else self.showMaximized()

    def changeEvent(self, e):
        if e.type() == QEvent.WindowStateChange:
            icons.set_name(self.titlebar.b_max, "restore" if self.isMaximized() else "max")
        elif e.type() == QEvent.ActivationChange:
            self._tell_helper_busy()
        super().changeEvent(e)

    def toggle_fullscreen(self, force=None):
        on = not self.isFullScreen() if force is None else force
        if on == self.isFullScreen():
            return
        if on:
            self._was_max = self.isMaximized()
            self.go("player")
            self.titlebar.hide()
            self.nav.hide()
            self.showFullScreen()
        else:
            self.titlebar.show()
            self.nav.show()
            self.showMaximized() if getattr(self, "_was_max", False) else self.showNormal()
        self.player.set_fullscreen(on)

    def _edge_at(self, gpos):
        if self.isMaximized() or self.isFullScreen():
            return None
        p = self.mapFromGlobal(gpos)
        w, h = self.width(), self.height()
        edges = Qt.Edges()
        if p.x() < EDGE:
            edges |= Qt.LeftEdge
        if p.x() > w - EDGE:
            edges |= Qt.RightEdge
        if p.y() < EDGE:
            edges |= Qt.TopEdge
        if p.y() > h - EDGE:
            edges |= Qt.BottomEdge
        return edges if edges else None

    def _player_key(self, e):
        """Single-key player controls. Matched by virtual key code, so they work in any keyboard layout."""
        focus = QApplication.focusWidget()
        from PySide6.QtWidgets import QAbstractSpinBox, QComboBox, QLineEdit, QTextEdit
        if isinstance(focus, (QLineEdit, QTextEdit, QAbstractSpinBox)) or (isinstance(focus, QComboBox) and focus.isEditable()):
            return False
        if e.modifiers() & (Qt.ControlModifier | Qt.AltModifier | Qt.MetaModifier):
            return False
        on_player = self.current_page == "player" or self.isFullScreen()
        p = self.player
        shift = bool(e.modifiers() & Qt.ShiftModifier)
        vk = e.nativeVirtualKey()
        key = e.key()
        anywhere = {0x46: lambda: self.toggle_fullscreen(), 0x7A: lambda: self.toggle_fullscreen(),   # F, F11
                    0x4D: p.toggle_mute, 0xDD: lambda: p.speed_step(1), 0xDB: lambda: p.speed_step(-1),  # M ] [
                    0x4E: lambda: p.step(1), 0x50: lambda: p.step(-1),                                   # N P
                    0x45: lambda: (self.go("player"), p.eq_popup())}                                       # E
        player_only = {Qt.Key_Space: p.toggle, Qt.Key_Left: lambda: p.seek_rel(-60 if shift else -10),
                       Qt.Key_Right: lambda: p.seek_rel(60 if shift else 10), Qt.Key_Up: lambda: p.change_volume(5),
                       Qt.Key_Down: lambda: p.change_volume(-5)}
        if key == Qt.Key_Escape and self.isFullScreen():
            self.toggle_fullscreen(False)
            return True
        if on_player and key in player_only:
            player_only[key]()
            return True
        if on_player and vk == 0x53:                                                                      # S
            p.video.command("screenshot")
            self.show_toast("Снимок сохранён на рабочий стол")
            return True
        if vk in anywhere and (on_player or not isinstance(focus, QPushButton)):
            anywhere[vk]()
            return True
        return False

    def eventFilter(self, obj, e):
        t = e.type()
        if t == QEvent.KeyPress and isinstance(obj, QWidget) and obj.window() is self:
            arrows = e.key() in (Qt.Key_Left, Qt.Key_Right, Qt.Key_Up, Qt.Key_Down)
            if (arrows or not e.isAutoRepeat()) and self._player_key(e):
                return True
        if t in (QEvent.MouseMove, QEvent.HoverMove, QEvent.MouseButtonPress) and isinstance(obj, QWidget):
            if obj.window() is not self:
                self._edge_cursor(None)     # pointer is over another window (a card, a popup): drop the resize cursor
                return super().eventFilter(obj, e)
            gp = e.globalPosition().toPoint() if hasattr(e, "globalPosition") else QCursorPos()
            edge = self._edge_at(gp)
            if t == QEvent.MouseButtonPress and edge and e.button() == Qt.LeftButton:
                self._edge_cursor(None)
                self.windowHandle().startSystemResize(edge)
                return True
            self._edge_cursor(edge)
        elif t == QEvent.Leave and obj is self:
            self._edge_cursor(None)
        return super().eventFilter(obj, e)

    def _edge_cursor(self, edge):
        """Resize cursor near the frameless window's edges. Restored as soon as the pointer is elsewhere."""
        if edge == self._resize_edge:
            return
        self._resize_edge = edge
        shape = None
        if edge in (Qt.LeftEdge | Qt.TopEdge, Qt.RightEdge | Qt.BottomEdge):
            shape = Qt.SizeFDiagCursor
        elif edge in (Qt.RightEdge | Qt.TopEdge, Qt.LeftEdge | Qt.BottomEdge):
            shape = Qt.SizeBDiagCursor
        elif edge in (Qt.LeftEdge, Qt.RightEdge):
            shape = Qt.SizeHorCursor
        elif edge in (Qt.TopEdge, Qt.BottomEdge):
            shape = Qt.SizeVerCursor
        if shape is None:
            while QApplication.overrideCursor():
                QApplication.restoreOverrideCursor()
        elif QApplication.overrideCursor():
            QApplication.changeOverrideCursor(shape)
        else:
            QApplication.setOverrideCursor(shape)

    # ----- drag and drop -----
    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls() or e.mimeData().hasText():
            e.acceptProposedAction()

    def dropEvent(self, e):
        md = e.mimeData()
        files, subs = [], []
        for u in md.urls():
            if u.isLocalFile():
                p = u.toLocalFile()
                if os.path.isdir(p):
                    for root, _, names in os.walk(p):
                        files += [os.path.join(root, n) for n in sorted(names) if n.rsplit(".", 1)[-1].lower() in MEDIA_EXT]
                elif p.rsplit(".", 1)[-1].lower() in SUB_EXT:
                    subs.append(p)
                else:
                    files.append(p)
            elif u.scheme() in ("http", "https"):
                self.clip.check(u.toString(), force=True)
        if not md.urls() and md.hasText():
            self.clip.check(md.text(), force=True)
        if files:
            self.go("player")
            self.player.add_and_play([Item(f) for f in files])
        for s in subs:
            self.player.video.command("sub-add", s, "select")
            self.show_toast("Субтитры подключены")

    # ----- keyboard -----
    def _shortcuts(self):
        """Ctrl shortcuts; single-key player controls live in _player_key()."""
        p = self.player
        keys = [
            ("Ctrl+O", p.open_files), ("Ctrl+V", self.paste_link),
            ("Ctrl+1", lambda: self.go("player")), ("Ctrl+2", lambda: self.go("playlist")), ("Ctrl+3", lambda: self.go("downloads")),
            ("Ctrl+4", lambda: self.go("history")), ("Ctrl+5", lambda: self.go("settings")),
        ]
        self._sc = []
        for k, fn in keys:
            sc = QShortcut(QKeySequence(k), self)
            sc.setContext(Qt.WindowShortcut)
            sc.activated.connect(fn)
            self._sc.append(sc)

    # ----- tray: the player's icon is the only one (the helper has none) -----
    def _tray(self):
        self.tray = QSystemTrayIcon(app_icon(), self)
        self.tray.setToolTip(APP_NAME)
        m = QMenu()
        m.addAction("Открыть", self.bring_to_front)
        m.addAction("Загрузки", lambda: (self.bring_to_front(), self.go("downloads")))
        watch = QAction("Распознавать ссылки", m, checkable=True)
        watch.setChecked(cfg["clip_watch"])
        watch.toggled.connect(lambda on: cfg.set("clip_watch", on))
        cfg.changed.connect(lambda k: k == "clip_watch" and watch.setChecked(cfg["clip_watch"]))
        m.addAction(watch)
        m.addSeparator()
        m.addAction("Выход", self.quit)
        self._tray_menu = m
        self.tray.setContextMenu(m)
        self.tray.activated.connect(lambda r: r == QSystemTrayIcon.Trigger and self.bring_to_front())
        self.tray.show()

    def closeEvent(self, e):
        if cfg["tray_on_close"] and not getattr(self, "_quitting", False):
            e.ignore()          # keep playing/downloading; the tray icon brings the window back
            self.hide()
            return
        self.shutdown()
        super().closeEvent(e)
        QApplication.quit()

    def quit(self):
        self._quitting = True
        self.close()

    def shutdown(self):
        if getattr(self, "_down", False):
            return
        self._down = True
        self.player.shutdown()
        self.dm.save()
        self.tray.hide()
        for n in list(self.notes.notes):
            n.close()
        self.toast.close()


def QCursorPos():
    from PySide6.QtGui import QCursor
    return QCursor.pos()
