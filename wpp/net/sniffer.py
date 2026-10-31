"""Fallback for sites yt-dlp does not support.

1. Fetch the HTML and look for <video>/<audio>/<source>, og:video, JSON blobs and bare media URLs.
2. Load the page in an off-screen Chromium (QtWebEngine), auto-play its players and record every media request.
3. Check what was found (HEAD/range requests, HLS variants) with the page's Referer and cookies.
DRM (Encrypted Media Extensions) is detected and reported instead of being bypassed.
"""
import html
import re
import threading
from urllib.parse import urljoin, urlparse

import requests
from PySide6.QtCore import QObject, QTimer, QUrl, Signal
from PySide6.QtWebEngineCore import (QWebEnginePage, QWebEngineProfile, QWebEngineScript, QWebEngineUrlRequestInfo,
                                     QWebEngineUrlRequestInterceptor)

from .common import UA, Option, ProbeResult

MEDIA_EXT = ("m3u8", "mpd", "mp4", "m4v", "webm", "mkv", "mov", "m4a", "mp3", "ogg", "oga", "opus", "flac", "aac",
             "wav", "flv", "ts")
SEGMENT_RE = re.compile(r"(\.ts|\.m4s|\.aac|/seg[-_]?\d+|/chunk[-_]?\d+|/frag[-_]?\d+|init\.mp4|range=\d+-\d+)(\?|$)", re.I)
URL_IN_TEXT = re.compile(r"""(?:https?:)?(?:\\?/){2}[^\s"'<>()]+?\.(?:%s)(?:\?[^\s"'<>()]*)?(?=["'\s<>)]|$)"""
                         % "|".join(MEDIA_EXT), re.I)
ATTR_RE = re.compile(r"""<(?:video|audio|source|embed)[^>]+?(?:src|data-src)\s*=\s*["']([^"']+)["']""", re.I)
META_RE = re.compile(r"""<meta[^>]+(?:property|name)\s*=\s*["'](?:og:video(?::secure_url|:url)?|og:audio(?::secure_url)?|"""
                     r"""twitter:player:stream)["'][^>]*content\s*=\s*["']([^"']+)["']""", re.I)
DRM_HINT = re.compile(r"widevine|playready|fairplay|/license|drm[-_]?proxy|/wv/|modular-?drm", re.I)

INJECT = r"""
(function(){
  try {
    const orig = navigator.requestMediaKeySystemAccess && navigator.requestMediaKeySystemAccess.bind(navigator);
    if (orig) navigator.requestMediaKeySystemAccess = function(k, c){ window.__wppDRM = String(k); return orig(k, c); };
  } catch(e) {}
})();
"""
AUTOPLAY = r"""
(function(){
  const out = [];
  const tryPlay = d => { d.querySelectorAll('video,audio').forEach(v => {
      try { v.muted = true; const p = v.play(); if (p && p.catch) p.catch(()=>{}); } catch(e) {}
      [v.currentSrc, v.src].concat([...v.querySelectorAll('source')].map(s => s.src))
        .forEach(s => { if (s && !s.startsWith('blob:') && !s.startsWith('data:')) out.push(s); });
    });
    d.querySelectorAll('[class*="play" i],[aria-label*="play" i],[aria-label*="смотреть" i],[data-play]').forEach((b, i) => {
      if (i < 3 && b.offsetParent !== null) { try { b.click(); } catch(e) {} } });
  };
  tryPlay(document);
  document.querySelectorAll('iframe').forEach(f => { try { tryPlay(f.contentDocument); } catch(e) {} });
  return JSON.stringify({src: out, drm: window.__wppDRM || '', title: document.title});
})()
"""


def _clean(u, base):
    u = u.replace("\\/", "/").replace("&amp;", "&").strip()
    if u.startswith("//"):
        u = "https:" + u
    return urljoin(base, u)


def _ext(u):
    path = urlparse(u).path.lower()
    m = re.search(r"\.([a-z0-9]{2,5})$", path)
    if m:
        return m.group(1)
    if "m3u8" in u.lower():
        return "m3u8"
    if ".mpd" in u.lower() or "manifest" in path:
        return "mpd"
    return ""


class _Interceptor(QWebEngineUrlRequestInterceptor):
    """Runs on Chromium's IO thread; hands hits to the GUI thread through a queued signal."""
    hit = Signal(str, str)

    def __init__(self):
        super().__init__()
        self.count = 0

    def interceptRequest(self, info):
        self.count += 1
        url = info.requestUrl().toString()
        rt = info.resourceType()
        if DRM_HINT.search(url):
            self.hit.emit("drm", url)
        if rt == QWebEngineUrlRequestInfo.ResourceType.ResourceTypeMedia or _ext(url) in MEDIA_EXT:
            self.hit.emit("media", url)
        elif rt == QWebEngineUrlRequestInfo.ResourceType.ResourceTypeXhr and ("m3u8" in url or ".mpd" in url):
            self.hit.emit("media", url)


class Sniffer(QObject):
    log = Signal(str)
    finished = Signal(object)
    _step1_done = Signal(list, str)
    _checked = Signal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.profile = None
        self.page = None
        self._step1_done.connect(self._after_html)
        self._checked.connect(self._finish)

    # ----- step 1: plain HTML -----
    def start(self, url):
        self.url = url
        self.found = {}         # url -> origin label
        self.drm = ""
        self.title = ""
        self.thumb = ""
        self.cookies = {}
        self.log.emit("→ Шаг 1. Разбор HTML: &lt;video&gt;, &lt;audio&gt;, &lt;source&gt;, og:video, JSON")
        threading.Thread(target=self._fetch_html, daemon=True).start()

    def _fetch_html(self):
        urls, title = [], ""
        try:
            r = requests.get(self.url, headers={"User-Agent": UA, "Accept-Language": "ru,en;q=0.8"}, timeout=20)
            if "charset" not in r.headers.get("Content-Type", "").lower():
                m = re.search(rb"""<meta[^>]+charset\s*=\s*["']?([\w-]+)""", r.content[:4096], re.I)
                r.encoding = m.group(1).decode() if m else "utf-8"
            text = r.text[:6_000_000]
            m = re.search(r"<title[^>]*>(.*?)</title>", text, re.I | re.S)
            title = html.unescape(m.group(1).strip()) if m else ""
            m = re.search(r"""<meta[^>]+(?:property|name)\s*=\s*["'](?:og:image|twitter:image)["'][^>]*content\s*=\s*["']([^"']+)""",
                          text, re.I)
            self.thumb = _clean(m.group(1), r.url) if m else ""
            for rx in (META_RE, ATTR_RE):
                urls += [_clean(u, r.url) for u in rx.findall(text)]
            urls += [_clean(u, r.url) for u in URL_IN_TEXT.findall(text)]
        except Exception as e:
            self.log.emit(f'<span class="warn">  страница не загрузилась напрямую: {html.escape(str(e)[:120])}</span>')
        self._step1_done.emit(urls, title)

    def _after_html(self, urls, title):
        self.title = title
        for u in urls:
            if not SEGMENT_RE.search(u):
                self.found.setdefault(u, "HTML")
        self.log.emit(f"  найдено в HTML: {len(self.found)}")
        self._start_browser()

    # ----- step 2: headless Chromium -----
    def _start_browser(self):
        self.log.emit("→ Шаг 2. Встроенный браузер (Chromium): загрузка страницы, автозапуск плеера")
        self.profile = QWebEngineProfile(self)           # off-the-record
        self.profile.setHttpUserAgent(UA)
        self.interceptor = _Interceptor()
        self.interceptor.hit.connect(self._sink)
        self.profile.setUrlRequestInterceptor(self.interceptor)
        self.profile.cookieStore().cookieAdded.connect(self._cookie)
        self.page = QWebEnginePage(self.profile, self)
        self.page.setAudioMuted(True)
        s = QWebEngineScript()
        s.setSourceCode(INJECT)
        s.setInjectionPoint(QWebEngineScript.DocumentCreation)
        s.setWorldId(QWebEngineScript.MainWorld)
        s.setRunsOnSubFrames(True)
        self.page.scripts().insert(s)
        self.page.loadFinished.connect(self._loaded)
        self.last_new = 0
        self.ticks = 0
        self.timer = QTimer(self, interval=500, timeout=self._tick)
        self.page.load(QUrl(self.url))
        self.timer.start()

    def _sink(self, kind, url):
        if kind == "drm":
            self.drm = self.drm or url
        elif not SEGMENT_RE.search(url) and url not in self.found:
            self.found[url] = "сеть"
            self.last_new = self.ticks

    def _cookie(self, c):
        self.cookies[bytes(c.name()).decode(errors="ignore")] = (bytes(c.value()).decode(errors="ignore"), c.domain())

    def _loaded(self, ok):
        self.page.runJavaScript(AUTOPLAY, 0, self._js_result)
        QTimer.singleShot(2500, lambda: self.page and self.page.runJavaScript(AUTOPLAY, 0, self._js_result))

    def _js_result(self, res):
        import json
        try:
            d = json.loads(res or "{}")
        except ValueError:
            return
        for u in d.get("src", []):
            if not SEGMENT_RE.search(u):
                self.found.setdefault(u, "плеер")
        if d.get("drm"):
            self.drm = self.drm or d["drm"]
        self.title = self.title or d.get("title", "")

    def _tick(self):
        self.ticks += 1
        streams = [u for u in self.found if _ext(u) in ("m3u8", "mpd")]
        # a manifest usually arrives together with its variants: stop 1 s after the last new one
        enough = (streams and self.ticks - self.last_new >= 2) or (self.found and self.ticks - self.last_new >= 6)
        if self.ticks >= 24 or (self.ticks >= 3 and enough):
            self.timer.stop()
            self.log.emit(f"  перехвачено запросов: {self.interceptor.count}, медиа: {len(self.found)}")
            self._stop_browser()
            self._verify()

    def _stop_browser(self):
        if self.page:
            self.page.triggerAction(QWebEnginePage.Stop)
            self.page.deleteLater()
            self.page = None

    # ----- step 3: check links -----
    def _headers(self, url):
        host = urlparse(url).hostname or ""
        jar = "; ".join(f"{k}={v}" for k, (v, dom) in self.cookies.items() if host.endswith(dom.lstrip(".")) or not dom)
        h = {"User-Agent": UA, "Referer": self.url}
        p = urlparse(self.url)
        h["Origin"] = f"{p.scheme}://{p.netloc}"
        if jar:
            h["Cookie"] = jar
        return h

    def _verify(self):
        if self.drm and not any(_ext(u) in ("mp4", "mp3", "m4a", "webm") for u in self.found):
            self._finish([])
            return
        self.log.emit("→ Шаг 3. Проверка ссылок: Referer и куки страницы")
        cands = list(self.found)[:25]
        threading.Thread(target=self._check_all, args=(cands,), daemon=True).start()

    def _check_all(self, cands):
        opts = []
        seen_variants = set()
        for u in cands:
            h = self._headers(u)
            ext = _ext(u)
            try:
                if ext == "m3u8":
                    r = requests.get(u, headers=h, timeout=12)
                    if r.status_code >= 400 or "#EXTM3U" not in r.text[:200]:
                        continue
                    res = re.findall(r"RESOLUTION=\d+x(\d+)", r.text)
                    if "#EXT-X-STREAM-INF" not in r.text and any(u in v for v in seen_variants):
                        continue
                    seen_variants.update(urljoin(u, l) for l in r.text.splitlines() if l and not l.startswith("#"))
                    q = " / ".join(f"{x}p" for x in sorted({int(x) for x in res}, reverse=True)) if res else "поток"
                    opts.append(Option(f"HLS · {q}", "m3u8", 0, "video", "best", u, h, direct=True))
                elif ext == "mpd":
                    r = requests.get(u, headers=h, timeout=12)
                    if r.status_code >= 400:
                        continue
                    if "cenc:" in r.text or "ContentProtection" in r.text:
                        self.drm = self.drm or "cenc"
                        continue
                    hs = sorted({int(x) for x in re.findall(r'height="(\d+)"', r.text)}, reverse=True)
                    opts.append(Option("DASH · " + (" / ".join(f"{x}p" for x in hs[:4]) or "поток"), "mpd", 0, "video",
                                       "bv*+ba/b", u, h, direct=True))
                else:
                    r = requests.get(u, headers=h | {"Range": "bytes=0-0"}, timeout=12, stream=True)
                    r.close()
                    if r.status_code >= 400:
                        continue
                    ctype = r.headers.get("Content-Type", "")
                    size = 0
                    cr = r.headers.get("Content-Range", "")
                    if "/" in cr and cr.rsplit("/", 1)[1].isdigit():
                        size = int(cr.rsplit("/", 1)[1])
                    elif r.headers.get("Content-Length", "").isdigit():
                        size = int(r.headers["Content-Length"])
                    if "text/html" in ctype or (size and size < 50_000):
                        continue
                    audio = ctype.startswith("audio") or ext in ("mp3", "m4a", "ogg", "oga", "opus", "flac", "aac", "wav")
                    name = urlparse(u).path.rsplit("/", 1)[-1] or u
                    opts.append(Option(name[:80], (ext or ctype.split("/")[-1]).upper(), size, "audio" if audio else "video",
                                       "best", u, h, direct=True))
            except Exception:
                continue
        self._checked.emit(opts)

    def _finish(self, opts):
        title = self.title or urlparse(self.url).hostname or self.url
        for o in opts:
            o.title = title if o.label.startswith(("HLS", "DASH")) else o.label.rsplit(".", 1)[0]
        opts.sort(key=lambda o: (o.kind != "video", not o.label.startswith(("HLS", "DASH")), -(o.size or 0)))
        if opts:
            self.log.emit(f'<span class="ok">Готово, доступно для скачивания: {len(opts)}</span>')
            self.finished.emit(ProbeResult("ok", "перехват", title, self.url, opts, thumbnail=self.thumb))
        elif self.drm:
            self.log.emit('<span class="err">  поток зашифрован DRM, ключи выдаются только сертифицированному плееру</span>')
            self.finished.emit(ProbeResult("drm", "перехват", title, self.url, [],
                                           "Видео на этой странице защищено DRM. Скачать его нельзя, смотрите на самом сайте."))
        else:
            self.log.emit('<span class="warn">  медиа не найдено</span>')
            self.finished.emit(ProbeResult("none", "перехват", title, self.url, [],
                                           "На этой странице не нашлось видео или аудио, которое можно получить."))
