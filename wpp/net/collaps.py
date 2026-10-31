"""Collaps embed player (api.<host>/embed/movie/<id>), used by many film and series sites.

The embed page carries the whole playlist: seasons, episodes, DASH/HLS manifests, voice-overs and subtitles.
Links carry a timestamp, so they are read again from the embed page whenever something is downloaded or played.
The page that embeds the player travels in the URL fragment (<embed>#ref=<page>): with it as Referer the player
opens the episode the site shows. A chosen episode travels the same way: <embed>#s=2&e=7.
"""
import json
import re
from urllib.parse import parse_qs, urlencode, urljoin, urlparse

import requests
from yt_dlp.extractor.common import InfoExtractor
from yt_dlp.utils import ExtractorError

from .common import UA

EMBED_RE = re.compile(r"""(?:https?:)?//api\.[\w-]+\.[a-z]{2,}/embed/(?:movie|kp|imdb)/\w+""", re.I)
SUB_LANGS = (("рус", "ru"), ("rus", "ru"), ("укр", "uk"), ("ukr", "uk"), ("eng", "en"), ("англ", "en"))


def find_embed(page_url):
    """Collaps player embedded in a page, or None."""
    try:
        r = requests.get(page_url, headers={"User-Agent": UA}, timeout=15)
    except requests.RequestException:
        return None
    for src in re.findall(r"""<iframe[^>]+src=["']([^"']+)""", r.text, re.I):
        if EMBED_RE.match(src):
            embed = urljoin(page_url, "https:" + src if src.startswith("//") else src)
            return f"{embed.split('#')[0]}#{urlencode({'ref': page_url})}"
    return None


def episode_url(embed, season, episode, ref=None):
    return f"{embed.split('#')[0]}#{urlencode({'s': season, 'e': episode} | ({'ref': ref} if ref else {}))}"


def _js_value(text, key, start=0):
    """The JSON value written after `key:` in a JS object literal."""
    i = text.find(key + ":", start)
    if i < 0:
        return None
    try:
        return json.JSONDecoder().raw_decode(text[i + len(key) + 1:].lstrip())[0]
    except ValueError:
        return None


def parse(page):
    """(seasons, current (season, episode) or None). A film comes back as one season 0 with one episode."""
    at = page.find("playlist:")
    seasons = _js_value(page, "seasons", at) if at >= 0 else None
    cur = re.search(r"current:\s*\{\s*season:\s*\"?(\d+)\"?\s*,\s*episode:\s*\"?([\w.-]+)", page)
    if seasons:
        return seasons, (cur.groups() if cur else None)
    film = {}
    for k in ("hls", "dash", "duration", "title", "poster"):
        m = re.search(rf"""\b{k}\s*:\s*(?:"((?:[^"\\]|\\.)*)"|'([^']*)'|(\d+))""", page)
        if m:
            film[k] = json.loads(f'"{m.group(1)}"') if m.group(1) is not None else (m.group(2) or int(m.group(3)))
    audio = _js_value(page, "audio")
    if audio:
        film["audio"] = audio
    cc = _js_value(page, "cc")
    if cc:
        film["cc"] = cc
    if not (film.get("hls") or film.get("dash")):
        return [], None
    film["episode"] = ""
    return [{"season": 0, "episodes": [film]}], None


def voice_language(name):
    """ISO 639-2 language of a voice-over by its name: 'Eng.Original' -> eng; studio dubs are Russian."""
    low = name.lower()
    for keys, code in ((("eng", "англ"), "eng"), (("укр", "ukr"), "ukr"), (("каз", "kaz"), "kaz")):
        if any(k in low for k in keys):
            return code
    return "rus" if name else "und"


def sub_lang(name, taken):
    low = name.lower()
    code = next((c for k, c in SUB_LANGS if k in low), "und")
    n, base = 2, code
    while code in taken:
        code, n = f"{base}-{n}", n + 1
    return code


class CollapsIE(InfoExtractor):
    IE_NAME = "Collaps"
    _VALID_URL = r"https?://api\.[\w-]+\.[a-z]{2,}/embed/(?:movie|kp|imdb)/(?P<id>\w+)"

    def _real_extract(self, url):
        embed, _, frag = url.partition("#")
        want = {k: v[0] for k, v in parse_qs(frag).items()}
        headers = {"User-Agent": UA} | ({"Referer": want["ref"]} if want.get("ref") else {})
        page = self._download_webpage(embed, self._match_id(embed), headers=headers)
        seasons, cur = parse(page)
        if not seasons:
            raise ExtractorError("Видео недоступно", expected=True)
        key = (want.get("s"), want.get("e")) if "e" in want else cur
        all_eps = [(s, e) for s in seasons if not s.get("blocked") for e in s.get("episodes") or []]
        s, ep = next(((s, e) for s, e in all_eps if key and (str(s["season"]), str(e["episode"])) == tuple(map(str, key))),
                     all_eps[0])
        info = self._episode(embed, s, ep)
        if not info["thumbnail"]:       # episodes without a still: another episode's, else the site's poster
            info["thumbnail"] = next((e["poster"] for _, e in all_eps if str(e.get("poster", "")).startswith("http")), None) \
                or self._site_poster(want.get("ref"))
        if len(all_eps) > 1:
            info["wpp_episodes"] = [{"season": x["season"], "episode": str(e["episode"]),
                                     "title": e.get("title") or f"Сезон {x['season']}, серия {e['episode']}",
                                     "url": episode_url(embed, x["season"], e["episode"], want.get("ref")), "duration": e.get("duration")}
                                    for x, e in all_eps]
            info["wpp_current"] = episode_url(embed, s["season"], ep["episode"], want.get("ref"))
        return info

    def _site_poster(self, page_url):
        if not page_url:
            return None
        page = self._download_webpage(page_url, "poster", headers={"User-Agent": UA}, fatal=False) or ""
        m = re.search(r"""<meta[^>]+property=["']og:image["'][^>]+content=["']([^"']+)""", page)
        return urljoin(page_url, m.group(1)) if m else None

    def _episode(self, embed, season, ep):
        vid = str(ep.get("id") or ep.get("videoKey") or self._match_id(embed))
        origin = f"https://{urlparse(embed).hostname}/"
        headers = {"Referer": origin, "Origin": origin.rstrip("/"), "User-Agent": UA}
        names = (ep.get("audio") or {}).get("names") or []
        formats, voices = [], {}
        if ep.get("dash"):
            fmts = self._extract_mpd_formats(ep["dash"], vid, mpd_id="dash", headers=headers, fatal=False)
            for f in fmts:
                lang = f.get("language") or ""
                if f.get("vcodec") == "none" and lang:
                    i = int(re.sub(r"\D", "", lang) or 0)
                    voices[lang] = names[i] if i < len(names) else lang
                    f["format_note"] = voices[lang]
            formats += fmts
        if ep.get("hls") and not any(f.get("vcodec") != "none" for f in formats):
            fmts = self._extract_m3u8_formats(ep["hls"], vid, "mp4", m3u8_id="hls", headers=headers, fatal=False)
            # the master lists every rendition twice: the second copy points at failover hosts that often stall
            seen, keep = set(), []
            for f in fmts:
                k = (f.get("height"), f.get("tbr"), f.get("vcodec"), f.get("language"))
                if k not in seen:
                    seen.add(k)
                    keep.append(f)
            formats += keep
        for f in formats:
            f["http_headers"] = headers
            # the manifests declare about nine times the real bitrate (13.7 Mbit/s listed, 1.5 delivered):
            # no estimate is better than a size ten times too large
            for k in ("tbr", "vbr", "abr", "filesize_approx"):
                f.pop(k, None)
        subs, taken = {}, set()
        for c in ep.get("cc") or []:
            if c.get("url"):
                code = sub_lang(c.get("name") or "", taken)
                taken.add(code)
                subs[code] = [{"url": c["url"], "ext": "vtt", "name": c.get("name") or code}]
        title = ep.get("title") or (f"Сезон {season['season']}, серия {ep['episode']}" if season["season"] else vid)
        return {
            "id": vid, "title": title, "duration": ep.get("duration"),
            "thumbnail": ep["poster"] if str(ep.get("poster", "")).startswith("http") else None,
            "formats": formats, "subtitles": subs, "http_headers": headers,
            "season_number": season["season"] or None, "episode_number": int(ep["episode"]) if str(ep.get("episode")).isdigit() else None,
            # DASH carries every quality up to 1080p and all voice-overs; the HLS master stops at 720p
            "wpp_voices": voices, "wpp_play": ep.get("dash") or ep.get("hls"),
        }
