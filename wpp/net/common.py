"""Data shared by the yt-dlp prober, the page sniffer and the download queue."""
import re
from dataclasses import dataclass, field

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/141.0.0.0 Safari/537.36")

URL_RE = re.compile(r"""(?:https?://|www\.)[^\s"'<>\]\[{}|\\^`]+""", re.I)


def find_url(text):
    m = URL_RE.search(text or "")
    if not m:
        return None
    u = m.group(0).rstrip(".,;:!?)»")
    if u.lower().startswith("www."):
        u = "https://" + u
    return u


@dataclass
class Option:
    label: str                      # "1080p60", "Только аудио"
    detail: str = ""                # "H.264 + AAC"
    size: float = 0                 # bytes, approximate
    kind: str = "video"             # video | audio | playlist
    format: str = ""                # yt-dlp format selector
    url: str = ""                   # page URL (yt-dlp) or direct media URL (sniffer)
    headers: dict = field(default_factory=dict)
    title: str = ""
    extract_audio: str = ""         # "mp3" to convert after download
    playlist: bool = False
    duration: float = 0
    direct: bool = False            # url is the media itself (sniffed)


@dataclass
class ProbeResult:
    state: str                      # ok | none | drm | error
    source: str = ""                # yt-dlp | перехват
    title: str = ""
    page_url: str = ""
    options: list = field(default_factory=list)
    message: str = ""
    info: dict = None               # full yt-dlp info (formats, subtitles) for the download options window
    thumbnail: str = ""             # preview image URL
    entries: int = 0                # number of videos in a playlist/channel link


VCODEC = (("avc", "H.264"), ("h264", "H.264"), ("hev", "HEVC"), ("hvc", "HEVC"), ("h265", "HEVC"), ("av01", "AV1"),
          ("av1", "AV1"), ("vp09", "VP9"), ("vp9", "VP9"), ("vp8", "VP8"), ("vvc", "VVC"), ("prores", "ProRes"))
ACODEC = (("mp4a", "AAC"), ("aac", "AAC"), ("opus", "Opus"), ("vorbis", "Vorbis"), ("mp3", "MP3"), ("ac-3", "AC3"),
          ("ac3", "AC3"), ("ec-3", "E-AC3"), ("eac3", "E-AC3"), ("flac", "FLAC"), ("alac", "ALAC"), ("dts", "DTS"))


def codec_name(c, table):
    c = (c or "").lower()
    if not c or c == "none":
        return ""
    for k, v in table:
        if c.startswith(k):
            return v
    return c.split(".")[0].upper()
