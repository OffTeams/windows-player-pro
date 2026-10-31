"""yt-dlp: list downloadable formats and resolve playable stream URLs. Runs in worker threads."""
import html

import yt_dlp
from yt_dlp.utils import DownloadError

from ..paths import deno_location, ffmpeg_location
from .common import ACODEC, VCODEC, Option, ProbeResult, codec_name


class _Logger:
    def __init__(self, log):
        self.log = log

    def debug(self, msg):
        if msg.startswith("[") and not msg.startswith("[debug]") and "Downloading" not in msg[:40]:
            self.log(html.escape(msg[:300]))

    def info(self, msg):
        self.debug(msg)

    def warning(self, msg):
        self.log(f'<span class="warn">{html.escape(msg[:300])}</span>')

    def error(self, msg):
        self.log(f'<span class="err">{html.escape(msg[:300])}</span>')


def base_opts(log=None):
    o = {"quiet": True, "no_warnings": log is None, "noprogress": True, "socket_timeout": 20,
         "ffmpeg_location": ffmpeg_location(), "noplaylist": True}
    deno = deno_location()
    if deno:
        o["js_runtimes"] = {"deno": {"path": deno}}
    if log:
        o["logger"] = _Logger(log)
    return o


def ydl(opts):
    """YoutubeDL with our extractors tried before yt-dlp's own (its generic one would claim every URL)."""
    from .collaps import CollapsIE
    y = yt_dlp.YoutubeDL(opts)
    y.add_info_extractor(CollapsIE())
    y._ies = {CollapsIE.ie_key(): y._ies[CollapsIE.ie_key()], **y._ies}
    return y


def _size(f, dur):
    s = f.get("filesize") or f.get("filesize_approx")
    if not s and f.get("tbr") and dur:
        s = f["tbr"] * 1000 / 8 * dur
    return s or 0


def options_from_info(info, url):
    dur = info.get("duration") or 0
    title = info.get("title") or url
    if info.get("_type") == "playlist":
        n = len(list(info.get("entries") or []))
        return [Option(f"Плейлист целиком · {n}", "лучшее качество", kind="playlist", format="bv*+ba/b", url=url,
                       title=title, playlist=True),
                Option(f"Плейлист, только аудио · {n}", "лучшее аудио", kind="audio", format="ba/b", url=url,
                       title=title, playlist=True)]
    fmts = [f for f in (info.get("formats") or []) if f.get("protocol") not in ("mhtml",)]
    vids = [f for f in fmts if f.get("vcodec") not in (None, "none") and f.get("height")]
    auds = [f for f in fmts if f.get("acodec") not in (None, "none") and f.get("vcodec") in (None, "none")]
    best_a = max(auds, key=lambda f: f.get("abr") or f.get("tbr") or 0, default=None)
    out = []
    seen = set()
    for f in sorted(vids, key=lambda f: (f.get("height") or 0, f.get("fps") or 0, f.get("tbr") or 0), reverse=True):
        h = f["height"]
        if h in seen or len(seen) >= 6:
            continue
        seen.add(h)
        fps = int(f.get("fps") or 0)
        vc = codec_name(f.get("vcodec"), VCODEC)
        has_audio = f.get("acodec") not in (None, "none")
        ac = codec_name(f.get("acodec") if has_audio else (best_a or {}).get("acodec"), ACODEC)
        size = _size(f, dur) + (0 if has_audio or not best_a else _size(best_a, dur))
        hdr = " HDR" if (f.get("dynamic_range") or "SDR") != "SDR" else ""
        out.append(Option(f"{h}p{fps if fps > 30 else ''}{hdr}", " + ".join(x for x in (vc, ac) if x), size, "video",
                          f"bv*[height<={h}]+ba/b[height<={h}]/b", url, title=title, duration=dur))
    if out:
        out[0].label = "Лучшее · " + out[0].label
    if best_a:
        ac = codec_name(best_a.get("acodec"), ACODEC)
        abr = int(best_a.get("abr") or best_a.get("tbr") or 0)
        out.append(Option("Только аудио", f"{ac} · {abr} кбит/с" if abr else ac, _size(best_a, dur), "audio", "ba/b", url,
                          title=title, duration=dur))
        out.append(Option("Аудио в MP3", "перекодирование, 320 кбит/с", 320 * 125 * dur, "audio", "ba/b", url,
                          title=title, extract_audio="mp3", duration=dur))
    if not out:
        ext = (info.get("ext") or "").upper()
        out.append(Option("Лучшее качество", ext, _size(info, dur), "audio" if info.get("vcodec") == "none" else "video",
                          "best/bv*+ba", url, title=title, duration=dur))
    return out


def probe(url, log, embeds=True):
    """Ask yt-dlp. Returns None when no extractor (generic included) can handle the page."""
    log(f'<b>$ yt-dlp --dump-json --no-download "{html.escape(url)}"</b>')
    opts = base_opts(log) | {"skip_download": True, "extract_flat": "in_playlist"}
    try:
        with ydl(opts) as y:
            # a page only the generic extractor would take: a known player on it beats trailers and ads around it
            if embeds and next((k for k, ie in y._ies.items() if ie.suitable(url)), "") == "Generic":
                found = _embedded(url, log)
                if found:
                    return found
                embeds = False
            info = y.extract_info(url, download=False)
    except DownloadError as e:
        msg = str(e)
        low = msg.lower()
        if "drm" in low:
            return ProbeResult("drm", "yt-dlp", page_url=url,
                               message="Видео защищено DRM. Скачать его нельзя, смотрите на самом сайте.")
        if "unsupported url" in low or "no video formats" in low or "unable to extract" in low:
            return _embedded(url, log) if embeds else None
        if any(k in low for k in ("sign in", "login", "private", "members", "age")):
            return ProbeResult("error", "yt-dlp", page_url=url, message="Сайт требует входа в аккаунт: " + msg[:200])
        if any(k in low for k in ("http error 404", "not found", "unavailable", "removed")):
            return ProbeResult("error", "yt-dlp", page_url=url, message="Видео недоступно: " + msg[:200])
        return None
    except Exception as e:
        log(f'<span class="err">{html.escape(str(e)[:200])}</span>')
        return None
    if not info:
        return None
    url = info.get("wpp_current") or url        # a series: pin the episode shown now, not whatever is newest later
    opts_list = options_from_info(info, url)
    if info.get("_type") != "playlist" and any(o.kind == "video" for o in opts_list):
        opts_list.insert(0, custom_option(url, info.get("title") or url, info.get("duration") or 0))
    log(f'<span class="ok">yt-dlp: {html.escape(info.get("extractor_key") or "")}, вариантов: {len(opts_list)}</span>')
    thumb = info.get("thumbnail") or ""
    if not thumb and info.get("thumbnails"):
        thumb = info["thumbnails"][-1].get("url", "")
    entries = len(list(info.get("entries") or [])) if info.get("_type") == "playlist" else 0
    if entries and not thumb:
        first = next(iter(info.get("entries") or []), None) or {}
        thumb = (first.get("thumbnails") or [{}])[-1].get("url", "") if first.get("thumbnails") else ""
    return ProbeResult("ok", "yt-dlp", info.get("title") or url, url, opts_list,
                       info=yt_dlp.YoutubeDL.sanitize_info(info), thumbnail=thumb, entries=entries)


def _embedded(url, log):
    """A known player embedded in a page yt-dlp doesn't support."""
    from .collaps import find_embed
    embed = find_embed(url)
    if not embed:
        return None
    log(f'<span class="ok">Плеер на странице: {html.escape(embed)}</span>')
    return probe(embed, log, embeds=False)


AUDIO_SEL = {"best": "ba", "aac": "ba[acodec^=mp4a]/ba", "opus": "ba[acodec=opus]/ba", "low": "wa[abr>=40]/wa/ba"}


def custom_option(url, title="", duration=0):
    """'Свой вариант': the video and sound chosen as defaults in the settings."""
    from ..config import AUDIO_DEFAULTS, VIDEO_DEFAULTS, cfg
    v, a = cfg["dl_video"], cfg["dl_audio"]
    vsel = "bv*" if v == "best" else f"bv*[height<={v}]"
    asel = AUDIO_SEL.get(a, "ba")
    vname = dict(VIDEO_DEFAULTS).get(v, v)
    aname = dict(AUDIO_DEFAULTS).get(a, a)
    return Option("Свой вариант", f"{vname} · {aname}", 0, "video", f"{vsel}+{asel}/{vsel}+ba/b", url,
                  title=title, duration=duration)


def default_index(options):
    """Which option the 'Качество по умолчанию' setting points at."""
    from ..config import cfg
    q = cfg["quality"]
    for i, o in enumerate(options):
        if q == "custom" and o.label == "Свой вариант":
            return i
        if q == "audio" and o.kind == "audio" and not o.extract_audio:
            return i
    return next((i for i, o in enumerate(options) if o.label != "Свой вариант"), 0) if q == "best" else 0


def warmup():
    """Load yt-dlp's extractor table in the background so the first copied link is answered quickly."""
    def run():
        try:
            from yt_dlp.extractor import gen_extractor_classes
            list(gen_extractor_classes())
            with ydl(base_opts()) as y:
                y.get_info_extractor("Youtube")
                y.get_info_extractor("Generic")
        except Exception:
            pass
    import threading
    threading.Thread(target=run, daemon=True).start()


def stream_format(screen_height=1080):
    """Format for watching online: no taller than the screen, and AV1 avoided above 1080p (often CPU-decoded)."""
    h = max(360, min(2160, int(screen_height or 1080)))
    av1 = "" if h <= 1080 else "[vcodec!^=av01]"
    return f"bv*[height<={h}]{av1}+ba/bv*[height<={h}]+ba/b[height<={h}]/b"


def resolve(page_url, fmt="bv*[height<=2160]+ba/b"):
    """Turn a page URL into direct stream URL(s) for mpv: (video_url, audio_url, headers, title, duration)."""
    with ydl(base_opts() | {"format": fmt or "bv*+ba/b"}) as y:
        info = y.extract_info(page_url, download=False)
    if info.get("wpp_play"):        # the player's own manifest: mpv picks quality and voice-over itself
        return info["wpp_play"], "", info.get("http_headers") or {}, info.get("title", ""), info.get("duration") or 0
    if info.get("_type") == "playlist":
        entries = [e for e in (info.get("entries") or []) if e]
        if not entries:
            raise RuntimeError("Плейлист пуст")
        info = entries[0]
    req = info.get("requested_formats")
    if req:
        v = next((f for f in req if f.get("vcodec") not in (None, "none")), req[0])
        a = next((f for f in req if f is not v), None)
        return v["url"], (a or {}).get("url", ""), v.get("http_headers") or {}, info.get("title", ""), info.get("duration") or 0
    return info["url"], "", info.get("http_headers") or {}, info.get("title", ""), info.get("duration") or 0
