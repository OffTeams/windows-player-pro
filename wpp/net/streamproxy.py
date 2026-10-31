"""Local HTTP proxy for watching online.

Video hosts (YouTube above all) throttle one long-running request to roughly real-time speed a few seconds
after it starts, which is what made playback stall. The proxy serves mpv's byte-range requests from
127.0.0.1 and fetches the upstream file in short ranged requests on a keep-alive connection — the same
trick yt-dlp's downloader uses — while reading ahead one chunk in parallel.
"""
import http.server
import re
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor

import requests

from .common import UA

CHUNK = 8 * 1024 * 1024
PIECE = 256 * 1024


class _Stream:
    def __init__(self, url, headers):
        self.url = url
        self.headers = {"User-Agent": UA, **(headers or {})}
        self.size = None
        self.ctype = "application/octet-stream"
        self.lock = threading.Lock()
        self.local = threading.local()

    def session(self):
        s = getattr(self.local, "s", None)
        if s is None:
            s = self.local.s = requests.Session()
        return s

    def fetch(self, start, end):
        h = dict(self.headers, Range=f"bytes={start}-{end}")
        for attempt in range(3):
            try:
                r = self.session().get(self.url, headers=h, timeout=20)
                if r.status_code in (200, 206):
                    self._learn(r)
                    data = r.content
                    if r.status_code == 200 and start:
                        data = data[start:end + 1]      # server ignored Range
                    return data
                if r.status_code in (403, 404, 410):
                    return None
            except requests.RequestException:
                pass
        return None

    def _learn(self, r):
        cr = r.headers.get("Content-Range", "")
        m = re.search(r"/(\d+)$", cr)
        with self.lock:
            if m:
                self.size = int(m.group(1))
            elif self.size is None and r.status_code == 200 and r.headers.get("Content-Length", "").isdigit():
                self.size = int(r.headers["Content-Length"])
            self.ctype = r.headers.get("Content-Type", self.ctype)

    def probe(self):
        if self.size is None:
            self.fetch(0, 0)
        return self.size


class _Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    proxy = None

    def log_message(self, *a):
        pass

    def _stream(self):
        m = re.match(r"^/s/([0-9a-f]+)", self.path)
        return self.proxy.streams.get(m.group(1)) if m else None

    def do_HEAD(self):
        self._serve(head=True)

    def do_GET(self):
        self._serve(head=False)

    def _serve(self, head):
        st = self._stream()
        if not st:
            self.send_error(404)
            return
        size = st.probe()
        if size is None:
            self.send_error(502)
            return
        start, end = 0, size - 1
        rng = self.headers.get("Range", "")
        m = re.match(r"bytes=(\d*)-(\d*)", rng)
        if m:
            if m.group(1):
                start = int(m.group(1))
                if m.group(2):
                    end = min(size - 1, int(m.group(2)))
            elif m.group(2):
                start = max(0, size - int(m.group(2)))
        if start >= size:
            self.send_response(416)
            self.send_header("Content-Range", f"bytes */{size}")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self.send_response(206 if rng else 200)
        self.send_header("Content-Type", st.ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        if rng:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        if head:
            return
        pos = start
        nxt = None
        with ThreadPoolExecutor(max_workers=1) as pool:
            while pos <= end:
                size_now = CHUNK if pos > start else 1024 * 1024     # small first piece: playback starts sooner
                cend = min(end, pos + size_now - 1)
                cur = nxt if nxt is not None else pool.submit(st.fetch, pos, cend)
                after = cend + 1
                nxt = pool.submit(st.fetch, after, min(end, after + CHUNK - 1)) if after <= end else None   # read ahead
                data = cur.result()
                if not data:
                    return
                try:
                    for i in range(0, len(data), PIECE):
                        self.wfile.write(data[i:i + PIECE])
                except (ConnectionError, OSError):
                    if nxt:
                        nxt.cancel()
                    return          # mpv seeked or stopped; it will open a new range
                pos += len(data)


class _Server(http.server.ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        pass            # mpv drops connections on every seek; that's normal, not an error


class StreamProxy:
    def __init__(self):
        self.streams = {}
        handler = type("H", (_Handler,), {"proxy": self})
        self.server = _Server(("127.0.0.1", 0), handler)
        self.server.daemon_threads = True
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def register(self, url, headers=None):
        sid = uuid.uuid4().hex[:16]
        self.streams[sid] = _Stream(url, headers)
        if len(self.streams) > 40:
            self.streams.pop(next(iter(self.streams)))
        return f"http://127.0.0.1:{self.server.server_address[1]}/s/{sid}"


_proxy = None


def proxied(url, headers=None):
    """Wrap a direct http(s) media URL; manifests (HLS/DASH) and local files pass through unchanged."""
    global _proxy
    if not url or not url.startswith(("http://", "https://")) or re.search(r"\.(m3u8|mpd)(\?|$)|/manifest/", url):
        return url
    if _proxy is None:
        _proxy = StreamProxy()
    return _proxy.register(url, headers)
