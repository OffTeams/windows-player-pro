"""Playlist, history and resume positions."""
import os
import time
from dataclasses import asdict, dataclass, field

from PySide6.QtCore import QObject, Signal

from .config import load_json, save_json

VIDEO_EXT = {"mkv", "mp4", "m4v", "mov", "avi", "wmv", "asf", "webm", "flv", "ts", "m2ts", "mts", "m2t", "mpg", "mpeg",
             "vob", "ogv", "3gp", "3g2", "rm", "rmvb", "divx", "xvid", "f4v", "mxf", "dv", "y4m", "ivf", "hevc", "h264",
             "h265", "265", "264", "av1", "obu", "vvc", "266", "evo", "wtv", "dvr-ms", "nut", "bik", "mjpeg"}
AUDIO_EXT = {"mp3", "flac", "wav", "ogg", "oga", "opus", "m4a", "m4b", "aac", "ac3", "eac3", "dts", "dtshd", "thd",
             "truehd", "mlp", "ape", "wv", "tta", "tak", "alac", "aiff", "aif", "dsf", "dff", "wma", "mka", "mpc",
             "spx", "amr", "caf", "w64", "rf64", "mod", "xm", "it", "s3m", "mid", "midi", "au", "ra"}
PLAYLIST_EXT = {"m3u", "m3u8", "pls", "cue", "xspf"}
SUB_EXT = {"srt", "ass", "ssa", "vtt", "sub", "sup", "idx", "smi", "lrc"}
MEDIA_EXT = VIDEO_EXT | AUDIO_EXT | PLAYLIST_EXT


def file_filter():
    v = " ".join(f"*.{e}" for e in sorted(VIDEO_EXT))
    a = " ".join(f"*.{e}" for e in sorted(AUDIO_EXT))
    p = " ".join(f"*.{e}" for e in sorted(PLAYLIST_EXT))
    return f"Все медиафайлы ({v} {a} {p});;Видео ({v});;Аудио ({a});;Плейлисты ({p});;Все файлы (*)"


def is_audio_path(path):
    return path.rsplit(".", 1)[-1].lower() in AUDIO_EXT


@dataclass
class Item:
    path: str                       # file path, page URL or direct stream URL
    title: str = ""
    duration: float = 0.0
    headers: dict = field(default_factory=dict)
    audio_url: str = ""
    page_url: str = ""              # page the stream came from (to re-resolve expiring URLs)
    source: str = ""                # "", "yt-dlp", "перехват"
    format: str = ""                # yt-dlp format selector used when re-resolving

    def __post_init__(self):
        if not self.title:
            self.title = os.path.basename(self.path.rstrip("/\\")) if not self.is_url else self.path

    @property
    def is_url(self):
        return "://" in self.path

    @property
    def key(self):
        return self.page_url or self.path

    @property
    def audio_only(self):
        return not self.is_url and is_audio_path(self.path)


class Playlist(QObject):
    changed = Signal()
    current_changed = Signal(int)

    def __init__(self):
        super().__init__()
        raw = load_json("playlist.json", {})
        self.items = []
        for d in raw.get("items", []):
            try:
                it = Item(**d)
            except TypeError:
                continue
            if it.is_url or os.path.exists(it.path):
                self.items.append(it)
        self.index = min(raw.get("index", -1), len(self.items) - 1)

    @property
    def current(self):
        return self.items[self.index] if 0 <= self.index < len(self.items) else None

    def add(self, items):
        start = len(self.items)
        self.items.extend(items)
        self.save()
        self.changed.emit()
        return start

    def remove(self, i):
        if not 0 <= i < len(self.items):
            return
        del self.items[i]
        if i < self.index:
            self.index -= 1
        elif i == self.index:
            self.index = min(self.index, len(self.items) - 1) if self.items else -1
        self.save()
        self.changed.emit()

    def move(self, i, j):
        if i == j or not (0 <= i < len(self.items) and 0 <= j < len(self.items)):
            return
        cur = self.current
        self.items.insert(j, self.items.pop(i))
        self.index = self.items.index(cur) if cur else -1
        self.save()
        self.changed.emit()

    def clear(self):
        self.items.clear()
        self.index = -1
        self.save()
        self.changed.emit()

    def set_index(self, i):
        self.index = i
        self.save()
        self.current_changed.emit(i)
        self.changed.emit()

    def save(self):
        save_json("playlist.json", {"index": self.index, "items": [asdict(i) for i in self.items]})


class History(QObject):
    changed = Signal()

    def __init__(self):
        super().__init__()
        self.entries = load_json("history.json", [])

    def touch(self, item: Item, pos=None, dur=None, origin=None):
        """`origin`: page the file was downloaded from (for local files that came through the downloader)."""
        e = next((x for x in self.entries if x["key"] == item.key), None)
        if e is None:
            e = {"key": item.key, "path": item.path, "title": item.title, "pos": 0.0, "dur": 0.0,
                 "page_url": item.page_url, "source": item.source, "format": item.format}
        else:
            self.entries.remove(e)
        e["title"] = item.title or e["title"]
        e["where"] = item.page_url or (item.path if item.is_url else os.path.dirname(item.path))
        if origin:
            e["origin"] = origin
        if pos is not None:
            e["pos"] = float(pos)
        if dur:
            e["dur"] = float(dur)
        e["when"] = time.time()
        self.entries.insert(0, e)
        del self.entries[300:]
        save_json("history.json", self.entries)
        self.changed.emit()

    def position(self, item: Item):
        e = next((x for x in self.entries if x["key"] == item.key), None)
        if not e or not e.get("dur"):
            return 0.0
        pos, dur = e["pos"], e["dur"]
        return pos if 15 < pos < dur - 20 else 0.0

    def remove(self, key):
        self.entries = [x for x in self.entries if x["key"] != key]
        save_json("history.json", self.entries)
        self.changed.emit()

    def clear(self):
        self.entries = []
        save_json("history.json", [])
        self.changed.emit()
