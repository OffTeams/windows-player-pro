"""libmpv embedded into a native Qt child window.

All decoding is done by mpv/FFmpeg, so every format FFmpeg knows plays here:
HEVC/AV1/VVC 10-bit, HDR10/HDR10+/Dolby Vision, TrueHD/DTS-HD, MKV/M2TS, HLS/DASH and so on.
Property observers fire on mpv's event thread; they only emit Qt signals, which Qt queues to the GUI thread.
"""
import logging

import mpv
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QWidget

from ..config import EQ_BANDS, cfg

log = logging.getLogger("wpp.mpv")

OBSERVED = ["time-pos", "duration", "pause", "eof-reached", "track-list", "chapter-list", "volume", "mute",
            "speed", "video-params", "hwdec-current", "demuxer-cache-time", "mouse-pos", "idle-active",
            "media-title", "loop-file"]


class MpvWidget(QWidget):
    prop = Signal(str, object)
    file_loaded = Signal()
    end_file = Signal(str)          # reason: eof | stop | error | quit | redirect
    mouse = Signal(str)             # click | dblclick | wheel_up | wheel_down | move

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_DontCreateNativeAncestors)
        self.setAttribute(Qt.WA_NativeWindow)
        self.setAttribute(Qt.WA_OpaquePaintEvent)
        self.setAttribute(Qt.WA_NoSystemBackground)
        self.setMouseTracking(True)
        self.setMinimumSize(160, 90)
        opts = dict(
            wid=str(int(self.winId())),
            vo="gpu-next", gpu_api="d3d11", hwdec=cfg["hwdec"], keep_open="yes", idle="yes",
            osc=False, input_default_bindings=False, input_vo_keyboard=False, input_builtin_bindings=False,
            osd_level=1, osd_font="Segoe UI Variable Text", osd_font_size=36, osd_border_size=2,
            osd_duration=900, ytdl=False, volume_max=150, volume=cfg["volume"],
            sub_auto="fuzzy", audio_file_auto="fuzzy", sub_font="Segoe UI Variable Text",
            screenshot_directory="~~desktop/", cache="yes", demuxer_max_bytes="400MiB",
            demuxer_max_back_bytes="100MiB", demuxer_readahead_secs=60, cache_pause_wait=2, network_timeout=20,
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/141.0 Safari/537.36",
        )
        self.mpv = mpv.MPV(log_handler=self._log, loglevel="warn", **opts)
        for o, v in (("drag-and-drop", "no"), ("target-colorspace-hint", "auto")):
            try:
                self.mpv[o] = v
            except Exception:
                pass
        for name in OBSERVED:
            self.mpv.observe_property(name, self._observer(name))
        self.mpv.event_callback("file-loaded")(lambda e: self.file_loaded.emit())

        @self.mpv.event_callback("end-file")
        def _end(ev):
            try:
                reason = ev.data.reason
                self.end_file.emit({0: "eof", 2: "stop", 3: "quit", 4: "error", 5: "redirect"}.get(int(reason), "eof"))
            except Exception:
                self.end_file.emit("eof")

        for key, what in (("MBTN_LEFT", "click"), ("MBTN_LEFT_DBL", "dblclick"),
                          ("WHEEL_UP", "wheel_up"), ("WHEEL_DOWN", "wheel_down"), ("MBTN_RIGHT", "rclick")):
            self.mpv.register_key_binding(key, self._binder(what))
        self.apply_settings()
        cfg.changed.connect(self._cfg_changed)

    # ----- plumbing -----
    def _log(self, level, prefix, text):
        log.warning("mpv[%s] %s: %s", level, prefix, text.strip())

    def _observer(self, name):
        def handler(_name, value):
            if name == "mouse-pos":
                self.mouse.emit("move")
            else:
                self.prop.emit(name, value)
        return handler

    def _binder(self, what):
        def handler(state, *_):
            if state and state[0] == "d" or state == "p-":
                self.mouse.emit(what)
        return handler

    def _cfg_changed(self, key):
        if key in ("hwdec", "hdr", "spdif", "eq"):
            self.apply_settings()

    def apply_settings(self):
        m = self.mpv
        self._set("hwdec", cfg["hwdec"])
        hdr = cfg["hdr"]
        self._set("target-colorspace-hint", {"passthrough": "yes", "tonemap": "no"}.get(hdr, "auto"))
        self._set("tone-mapping", "auto")
        self._set("audio-spdif", "ac3,eac3,dts,dts-hd,truehd" if cfg["spdif"] else "")
        self.apply_eq()
        return m

    def apply_eq(self):
        eq = cfg["eq"]
        if not eq.get("on"):
            self._set("af", "")
            return
        parts = [f"volume={eq['pre']:+.1f}dB"] if eq.get("pre") else []
        for f, g in zip(EQ_BANDS, eq["g"]):
            if abs(g) > 0.01:
                parts.append(f"equalizer=f={f}:t=o:w=1:g={g:.1f}")
        self._set("af", f"lavfi=[{','.join(parts)}]" if parts else "")

    def _set(self, name, value):
        try:
            self.mpv[name] = value
        except Exception as e:
            log.warning("set %s=%r failed: %s", name, value, e)

    def get(self, name, default=None):
        """Read a runtime property (mpv[...] in python-mpv reads options/, which lacks most properties)."""
        try:
            v = getattr(self.mpv, name.replace("-", "_"))
        except Exception:
            try:
                v = self.mpv[name]
            except Exception:
                return default
        return default if v is None else v

    # ----- commands -----
    def load(self, url, start=None, headers=None, audio_url=None):
        """Play a file or URL; headers/audio_url are for streams resolved by yt-dlp or the page sniffer."""
        opts = {}
        if start:
            opts["start"] = f"{start:.2f}"
        if headers:
            hf = [f"{k}: {v}" for k, v in headers.items() if k.lower() not in ("user-agent", "referer")]
            if hf:
                opts["http-header-fields"] = ",".join(h.replace(",", r"\,") for h in hf)
            for k, v in headers.items():
                if k.lower() == "user-agent":
                    opts["user-agent"] = v
                elif k.lower() == "referer":
                    opts["referrer"] = v
        if audio_url:
            opts["audio-files"] = audio_url.replace(";", r"\;")
        # loadfile joins options with commas; %len% quoting keeps commas inside values (User-Agent, headers)
        opts = {k: f"%{len(v.encode('utf-8'))}%{v}" for k, v in opts.items()}
        self._set("lavfi-complex", "")
        self.mpv.loadfile(url, "replace", -1, **opts)
        self._set("pause", False)

    def stop(self):
        try:
            self.mpv.command("stop")
        except Exception:
            pass

    def command(self, *args, **kw):
        try:
            self.mpv.command(*args, **kw)
        except Exception as e:
            log.warning("command %s failed: %s", args, e)

    def show_text(self, text, ms=900):
        self.command("show-text", text, ms)

    def overlay(self, ass_text):
        if ass_text:
            self.command("osd-overlay", id=7, format="ass-events", data=ass_text, res_x=0, res_y=720, z=0, hidden=False)
        else:
            self.command("osd-overlay", id=7, format="none", data="")

    def audio_visual(self, on):
        """Spectrum picture for audio files without cover art."""
        self._set("lavfi-complex", "[aid1]asplit[ao][a];[a]showcqt=s=1280x720:r=30:axis=0:text=0:basefreq=30:"
                  "endfreq=16000:bar_g=2:sono_g=3:bar_v=12:sono_v=10,format=yuv420p[vo]" if on else "")

    def shutdown(self):
        try:
            self.mpv.terminate()
        except Exception:
            pass
