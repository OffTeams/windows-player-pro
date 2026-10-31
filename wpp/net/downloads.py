"""Download queue on top of yt-dlp, with the backup-folder fallback."""
import errno
import os
import re
import subprocess
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from ..config import cfg, load_json, save_json
from ..paths import ffmpeg_location
from .collaps import CollapsIE, voice_language
from .common import Option
from .ytdl import base_opts, ydl

WRITE_ERRNOS = {errno.EACCES, errno.EPERM, errno.ENOENT, errno.ENOSPC, errno.EROFS, errno.ENODEV, errno.EIO}


def folder_problem(path):
    """Why a folder can't be written to, or '' if it can. Creates it when missing."""
    path = (path or "").strip()
    if not path:
        return "папка не указана"
    p = Path(path)
    drive = p.drive
    if drive and not os.path.exists(drive + "\\"):
        return f"диск {drive} не найден"
    try:
        p.mkdir(parents=True, exist_ok=True)
        probe = p / f".wpp-write-test-{uuid.uuid4().hex[:6]}"
        probe.write_bytes(b"")
        probe.unlink()
    except PermissionError:
        return "нет прав на запись"
    except OSError as e:
        if e.errno == errno.ENOSPC:
            return "нет места на диске"
        if path.startswith("\\\\"):
            return "сетевая папка недоступна"
        return e.strerror or "папка недоступна"
    return ""


def pick_folder(preferred=None):
    """(folder, used_backup, reason) or (None, False, reason) when neither folder works.

    `preferred` is a folder chosen for this one download; it falls back to the backup folder the same way.
    """
    primary, backup = (preferred or cfg["dl_dir"]), cfg["dl_backup"]
    why = folder_problem(primary)
    if not why:
        return primary, False, ""
    why2 = folder_problem(backup)
    if not why2:
        return backup, True, why
    return None, False, f"основная папка: {why}; резервная: {why2}"


class Cancelled(Exception):
    pass


@dataclass
class Task:
    title: str
    url: str
    label: str = ""
    detail: str = ""
    format: str = ""
    headers: dict = field(default_factory=dict)
    extract_audio: str = ""
    playlist: bool = False
    direct: bool = False
    source: str = ""
    page_url: str = ""
    folder: str = ""
    backup: bool = False
    state: str = "queued"           # queued | run | pause | done | error | cancel
    done: float = 0
    total: float = 0
    speed: float = 0
    eta: float = 0
    file: str = ""
    error: str = ""
    chosen_folder: str = ""         # folder picked in the options window (empty: the configured one)
    merge: str = ""                 # output container for video: mp4 | mkv | webm ("" = automatic)
    subs: list = field(default_factory=list)
    embed_subs: bool = True
    audio_quality: str = ""         # kbit/s for lossy audio conversion
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:10])
    added: float = field(default_factory=time.time)

    @property
    def progress(self):
        return self.done / self.total if self.total else 0.0


class DownloadManager(QObject):
    updated = Signal(str)               # task id
    listed = Signal()                   # tasks added/removed
    rerouted = Signal(object, str)      # task, reason (moved to backup folder)
    failed = Signal(object)
    completed = Signal(object)
    _kick = Signal()                    # worker thread -> GUI thread: start the next queued task

    def __init__(self):
        super().__init__()
        self._kick.connect(self._pump)
        self.tasks = []
        for d in load_json("downloads.json", []):
            try:
                t = Task(**d)
            except TypeError:
                continue
            if t.state in ("run", "queued"):
                t.state = "pause"
            t.speed = 0
            if t.state == "done" and t.file and not os.path.exists(t.file):
                t.file = self._final_name(t.file) or t.file
            self.tasks.append(t)
        self._flags = {}
        self._last_save = 0

    # ----- public -----
    def add(self, opt: Option, source="", page_url="", folder=None, merge="", subs=(), embed_subs=True, audio_quality=""):
        chosen = folder
        folder, backup, why = pick_folder(chosen)
        t = Task(opt.title or opt.label, opt.url, opt.label, opt.detail, opt.format, dict(opt.headers), opt.extract_audio,
                 opt.playlist, opt.direct, source, page_url or opt.url, total=opt.size or 0, chosen_folder=chosen or "",
                 merge=merge or ("" if cfg["dl_container"] == "auto" else cfg["dl_container"]), subs=list(subs),
                 embed_subs=embed_subs, audio_quality=audio_quality)
        if folder is None:
            t.state, t.error = "error", why
            self.tasks.insert(0, t)
            self.listed.emit()
            self.failed.emit(t)
            self.save()
            return t
        t.folder, t.backup = folder, backup
        self.tasks.insert(0, t)
        self.listed.emit()
        if backup:
            self.rerouted.emit(t, why)
        self._pump()
        self.save()
        return t

    def get(self, tid):
        return next((t for t in self.tasks if t.id == tid), None)

    def pause(self, t):
        if t.state == "run":
            self._flags[t.id] = "pause"
        elif t.state == "queued":
            t.state = "pause"
            self.updated.emit(t.id)

    def resume(self, t):
        if t.state in ("pause", "error"):
            t.state, t.error = "queued", ""
            if not t.folder or folder_problem(t.folder):
                folder, backup, why = pick_folder(t.chosen_folder or None)
                if folder is None:
                    t.state, t.error = "error", why
                    self.updated.emit(t.id)
                    self.failed.emit(t)
                    return
                if folder != t.folder:
                    t.folder, t.backup = folder, backup
                    if backup:
                        self.rerouted.emit(t, why)
            self.updated.emit(t.id)
            self._pump()

    def cancel(self, t):
        if t.state == "run":
            self._flags[t.id] = "cancel"
        else:
            t.state = "cancel"
            self._remove_parts(t)
            self.remove(t)

    def remove(self, t):
        if t in self.tasks:
            if t.state == "run":
                self._flags[t.id] = "cancel"
            self.tasks.remove(t)
            self.listed.emit()
            self.save()

    def clear_finished(self):
        self.tasks = [t for t in self.tasks if t.state not in ("done", "cancel")]
        self.listed.emit()
        self.save()

    def active(self):
        return sum(1 for t in self.tasks if t.state in ("run", "queued"))

    def save(self):
        save_json("downloads.json", [asdict(t) for t in self.tasks[:200]])

    # ----- workers -----
    def _pump(self):
        running = sum(1 for t in self.tasks if t.state == "run")
        for t in reversed(self.tasks):
            if running >= max(1, cfg["max_parallel"]):
                break
            if t.state == "queued":
                t.state = "run"
                running += 1
                self.updated.emit(t.id)
                threading.Thread(target=self._run, args=(t,), daemon=True).start()

    def _outtmpl(self, t):
        if t.direct:
            name = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', " ", t.title).strip()[:150] or "media"
            return str(Path(t.folder) / f"{name}.%(ext)s")
        if t.playlist:
            return str(Path(t.folder) / "%(playlist_title).80B" / "%(playlist_index)03d. %(title).150B.%(ext)s")
        if CollapsIE.suitable(t.url):       # episode titles are unique already; the site's numeric id only clutters
            return str(Path(t.folder) / "%(title).170B.%(ext)s")
        return str(Path(t.folder) / "%(title).170B [%(id)s].%(ext)s")

    def _hook(self, t):
        def hook(d):
            flag = self._flags.get(t.id)
            if flag:
                raise Cancelled(flag)
            if d.get("status") == "downloading":
                t.done = d.get("downloaded_bytes") or 0
                t.total = d.get("total_bytes") or d.get("total_bytes_estimate") or t.total
                t.speed = d.get("speed") or 0
                t.eta = d.get("eta") or 0
                t._tmp = d.get("tmpfilename") or ""
                self.updated.emit(t.id)
            elif d.get("status") == "finished":
                t.file = d.get("filename") or t.file
        return hook

    def _run(self, t: Task):
        attempt_backup = True
        while True:
            opts = base_opts() | {
                "outtmpl": self._outtmpl(t), "format": t.format or "bv*+ba/b", "progress_hooks": [self._hook(t)],
                "continuedl": True, "noplaylist": not t.playlist, "windowsfilenames": True,
                # 10 MB ranged requests dodge YouTube's per-request throttling; HLS/DASH fragments go 8 at a time
                "concurrent_fragment_downloads": 8, "http_chunk_size": 10 << 20, "retries": 5, "fragment_retries": 10, "overwrites": False,
            }
            if t.headers:
                opts["http_headers"] = t.headers
            pp = []
            if t.extract_audio:
                pp.append({"key": "FFmpegExtractAudio", "preferredcodec": t.extract_audio,
                           "preferredquality": t.audio_quality or "320"})
            elif t.merge:
                opts["merge_output_format"] = t.merge
                if t.merge in ("mp4", "mkv"):           # WebM can't hold H.264/AAC, so it's only used for merging
                    pp.append({"key": "FFmpegVideoRemuxer", "preferedformat": t.merge})
            if t.subs:
                opts.update({"writesubtitles": True, "writeautomaticsub": True, "subtitleslangs": t.subs,
                             "subtitlesformat": "srt/vtt/best"})
                pp.append({"key": "FFmpegSubtitlesConvertor", "format": "srt"})
                if t.embed_subs and not t.extract_audio:
                    pp.append({"key": "FFmpegEmbedSubtitle", "already_have_subtitle": False})
            if pp:
                opts["postprocessors"] = pp
            try:
                with ydl(opts) as y:
                    info = y.extract_info(t.url, download=True)
                    final = ((info or {}).get("requested_downloads") or [{}])[-1].get("filepath")
                    if final:                   # path after merging/remuxing/conversion, not a temporary part
                        t.file = final
                    if info and not t.file:
                        try:
                            t.file = y.prepare_filename(info)
                        except Exception:
                            pass
                if t.extract_audio and t.file:
                    t.file = os.path.splitext(t.file)[0] + "." + t.extract_audio
                elif t.merge and t.file and not os.path.exists(t.file):
                    alt = os.path.splitext(t.file)[0] + "." + t.merge
                    if os.path.exists(alt):
                        t.file = alt
                if info and info.get("wpp_voices") and t.file and os.path.exists(t.file):
                    self._tag_voices(t.file, info)
                t.state, t.speed, t.eta = "done", 0, 0
                if t.total:
                    t.done = t.total
                self.updated.emit(t.id)
                self.completed.emit(t)
                break
            except Cancelled as c:
                t.speed = 0
                if str(c) == "cancel":
                    t.state = "cancel"
                    self._remove_parts(t)
                else:
                    t.state = "pause"
                self.updated.emit(t.id)
                break
            except Exception as e:
                os_err = self._os_error(e)
                if os_err and attempt_backup and not t.backup and not folder_problem(cfg["dl_backup"]):
                    attempt_backup = False
                    reason = f"ошибка записи: {os_err}"
                    t.folder, t.backup = cfg["dl_backup"], True
                    self.rerouted.emit(t, reason)
                    continue
                t.state, t.speed = "error", 0
                t.error = self._nice_error(e)
                self.updated.emit(t.id)
                self.failed.emit(t)
                break
        self._flags.pop(t.id, None)
        self.save()
        self._kick.emit()

    @staticmethod
    def _tag_voices(path, info):
        """The player's manifests label tracks 'ru0', 'ru1': name the voice-over and give real languages."""
        voices = info["wpp_voices"]
        fmts = info.get("requested_formats") or [info]
        audio = [f.get("language") for f in fmts if f.get("vcodec") in (None, "none") and f.get("acodec") != "none"]
        meta = ["-metadata:s:v:0", "language=und"]
        for i, code in enumerate(audio):
            name = voices.get(code) or ""
            meta += [f"-metadata:s:a:{i}", f"title={name}", f"-metadata:s:a:{i}", f"language={voice_language(name)}"]
        tmp = path + ".tag" + os.path.splitext(path)[1]
        try:
            r = subprocess.run([ffmpeg_location() or "ffmpeg", "-v", "error", "-y", "-i", path, "-map", "0", "-c", "copy",
                                *meta, tmp], capture_output=True, timeout=600)
            if r.returncode == 0 and os.path.getsize(tmp) > 0:
                os.replace(tmp, path)
        except (OSError, subprocess.SubprocessError):
            pass
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass

    @staticmethod
    def _final_name(path):
        """Older records could point at a temporary track like 'name.f380.m4a'; find the merged file next to it."""
        base = re.sub(r"\.f[\w-]+\.\w+$", "", path)
        base = os.path.splitext(base)[0] if base == path else base
        for ext in (".mkv", ".mp4", ".webm", ".m4a", ".mp3", ".opus", ".flac", ".mka"):
            if os.path.exists(base + ext):
                return base + ext
        return ""

    @staticmethod
    def _os_error(e):
        cur = e
        for _ in range(5):
            if isinstance(cur, OSError) and (cur.errno in WRITE_ERRNOS or isinstance(cur, PermissionError)):
                return cur.strerror or str(cur)
            cur = getattr(cur, "exc_info", None) and cur.exc_info[1] or cur.__cause__ or cur.__context__
            if cur is None:
                break
        msg = str(e).lower()
        if "permission denied" in msg or "no space left" in msg or "errno 13" in msg or "errno 28" in msg:
            return str(e)[:120]
        return ""

    @staticmethod
    def _nice_error(e):
        msg = re.sub(r"\x1b\[[0-9;]*m", "", str(e)).replace("ERROR: ", "")
        low = msg.lower()
        if "http error 403" in low:
            return "Сервер отказал в доступе (403). Ссылка могла устареть, найдите медиа заново."
        if "http error 404" in low:
            return "Файл не найден на сервере (404)."
        if "ffmpeg" in low and "not found" in low:
            return "Не найден FFmpeg, нужен для склейки видео и аудио."
        return msg[:240]

    def _remove_parts(self, t):
        tmp = getattr(t, "_tmp", "")
        for p in (tmp, tmp + ".ytdl" if tmp else ""):
            if p and os.path.exists(p):
                try:
                    os.remove(p)
                except OSError:
                    pass
