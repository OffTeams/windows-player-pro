"""Find media behind a link: yt-dlp first, then the page sniffer."""
import html
import threading
from urllib.parse import urlparse

from PySide6.QtCore import QObject, QTimer, Signal

from ..config import cfg
from . import ytdl
from .common import ProbeResult
from .sniffer import Sniffer


def normalize(raw):
    u = (raw or "").strip()
    if not u:
        return None
    if "://" not in u:
        u = "https://" + u
    p = urlparse(u)
    if p.scheme not in ("http", "https") or not p.hostname or "." not in p.hostname:
        return None
    return u


class Prober(QObject):
    log = Signal(str)
    finished = Signal(object)
    _yt_done = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.busy = False
        self.cancelled = False
        self.sniffer = None
        self._yt_done.connect(self._after_ytdl)

    def start(self, url):
        self.busy = True
        self.cancelled = False
        self.url = url
        threading.Thread(target=lambda: self._yt_done.emit(ytdl.probe(url, self.log.emit)), daemon=True).start()

    def cancel(self):
        self.cancelled = True

    def _after_ytdl(self, res):
        if self.cancelled:
            return self._done(ProbeResult("none", page_url=self.url))
        if res is not None:
            return self._done(res)
        self.log.emit('<span class="warn">ERROR: Unsupported URL — сайт не поддерживается yt-dlp</span>')
        if not cfg["sniff"]:
            return self._done(ProbeResult("none", page_url=self.url,
                                          message="Перехват через встроенный браузер выключен в настройках."))
        self.sniffer = Sniffer(self)
        self.sniffer.log.connect(self.log)
        self.sniffer.finished.connect(self._done)
        self.sniffer.start(self.url)

    def _done(self, res):
        self.busy = False
        if self.sniffer:
            s, self.sniffer = self.sniffer, None
            QTimer.singleShot(1500, s.deleteLater)
        if not self.cancelled:
            self.finished.emit(res)
