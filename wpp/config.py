"""Persistent settings stored as JSON in %APPDATA%\\WindowsPlayerPro."""
import copy
import json

from PySide6.QtCore import QObject, Signal

from .paths import DATA, user_folder

EQ_BANDS = [32, 64, 125, 250, 500, 1000, 2000, 4000, 8000, 16000]
EQ_PRESETS = {
    "Ровно": [0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
    "Усиление баса": [7, 6, 4, 2, 0, 0, 0, 0, 0, 0],
    "Кино": [4, 3, 1, 0, -1, 1, 3, 4, 3, 2],
    "Голос и диалоги": [-3, -2, -1, 1, 4, 5, 4, 2, 0, -2],
    "Рок": [5, 4, 2, -1, -2, -1, 2, 4, 5, 5],
    "Классика": [3, 2, 1, 0, 0, 0, -1, -2, -1, 2],
    "Электроника": [5, 4, 1, 0, -2, 1, 0, 2, 4, 5],
    "Ночной режим": [-4, -2, 0, 1, 2, 2, 1, 0, -2, -4],
    "Наушники": [3, 2, 0, -1, 0, 1, 2, 3, 2, 1],
}

VIDEO_DEFAULTS = [("best", "Лучшее"), ("2160", "2160p"), ("1440", "1440p"), ("1080", "1080p"), ("720", "720p"),
                  ("480", "480p"), ("360", "360p")]
AUDIO_DEFAULTS = [("best", "Лучший"), ("aac", "AAC"), ("opus", "Opus"), ("low", "Экономный")]

DEFAULTS = {
    "theme": "system",            # system | light | dark
    "hwdec": "auto-safe",
    "hdr": "auto",                # auto | passthrough | tonemap
    "spdif": False,
    "resume": True,
    "volume": 80,
    "dl_dir": str(user_folder("Videos") / "Windows Player Pro"),
    "dl_backup": str(user_folder("Downloads")),
    "quality": "best",            # best | custom (dl_video + dl_audio) | audio
    "dl_video": "1080",
    "dl_audio": "best",
    "sniff": True,
    "clip_watch": True,
    "clip_top": True,
    "clip_auto": False,
    "tray_on_close": False,
    "max_parallel": 2,
    "update_check": True,
    "probe_log": "full",          # full | simple | off — how the Downloads tab shows the search
    "helper": True,               # background helper watches the clipboard (and starts with Windows)
    "dl_container": "auto",       # auto | mp4 | mkv | webm
    "eq": {"on": False, "pre": 0.0, "g": [0.0] * 10, "preset": "Ровно"},
}


class Config(QObject):
    changed = Signal(str)

    def __init__(self):
        super().__init__()
        self.path = DATA / "config.json"
        self.reload()

    def reload(self):
        """Re-read the file (the helper calls this when the player says settings changed)."""
        self.data = copy.deepcopy(DEFAULTS)
        try:
            stored = json.loads(self.path.read_text("utf-8"))
            for k, v in stored.items():
                if k not in self.data:
                    continue
                d = self.data[k]
                num = (int, float)
                if type(v) is type(d) or (isinstance(v, num) and isinstance(d, num) and not isinstance(v, bool)):
                    self.data[k] = v
        except (OSError, ValueError):
            pass

    def __getitem__(self, key):
        return self.data[key]

    def set(self, key, value):
        if self.data.get(key) == value:
            return
        self.data[key] = value
        self.save()
        self.changed.emit(key)

    def save(self):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=1), "utf-8")
            tmp.replace(self.path)
        except OSError:
            pass


cfg = Config()


def load_json(name, default):
    try:
        return json.loads((DATA / name).read_text("utf-8"))
    except (OSError, ValueError):
        return default


def save_json(name, value):
    try:
        DATA.mkdir(parents=True, exist_ok=True)
        tmp = DATA / (name + ".tmp")
        tmp.write_text(json.dumps(value, ensure_ascii=False), "utf-8")
        tmp.replace(DATA / name)
    except OSError:
        pass
