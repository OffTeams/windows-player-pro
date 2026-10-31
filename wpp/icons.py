"""Outline icons drawn from SVG paths and recolored on theme change."""
import weakref

from PySide6.QtCore import QByteArray, QRectF, QSize, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

P = {
    "play": '<path d="M7 4v16l13-8z"/>',
    "pause": '<path d="M7 4h3v16H7zM14 4h3v16h-3z"/>',
    "prev": '<path d="M19 5v14L8 12zM5 5v14"/>',
    "next": '<path d="M5 5v14l11-7zM19 5v14"/>',
    "back10": '<path d="M11 8 7 12l4 4M7 12h8a4 4 0 0 1 0 8h-2"/>',
    "fwd10": '<path d="m13 8 4 4-4 4M17 12H9a4 4 0 0 0 0 8h2"/>',
    "vol": '<path d="M4 9h4l5-4v14l-5-4H4zM16 9a4 4 0 0 1 0 6M18.5 6.5a8 8 0 0 1 0 11"/>',
    "mute": '<path d="M4 9h4l5-4v14l-5-4H4zM16 9l5 6M21 9l-5 6"/>',
    "cc": '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="M10 10.5a2 2 0 1 0 0 3M17 10.5a2 2 0 1 0 0 3"/>',
    "audio": '<path d="M4 10v4M8 7v10M12 4v16M16 8v8M20 11v2"/>',
    "adj": '<path d="M4 6h10M18 6h2M4 12h4M12 12h8M4 18h12"/><circle cx="16" cy="6" r="2"/><circle cx="10" cy="12" r="2"/><circle cx="18" cy="18" r="2"/>',
    "eq": '<path d="M6 4v6M6 14v6M12 4v2M12 10v10M18 4v10M18 18v2M4 12h4M10 8h4M16 16h4"/>',
    "full": '<path d="M4 9V4h5M15 4h5v5M20 15v5h-5M9 20H4v-5"/>',
    "unfull": '<path d="M9 4v5H4M20 9h-5V4M15 20v-5h5M4 15h5v5"/>',
    "list": '<path d="M9 6h11M9 12h11M9 18h11M4 6h.01M4 12h.01M4 18h.01"/>',
    "dl": '<path d="M12 4v11M7 10l5 5 5-5M5 20h14"/>',
    "hist": '<path d="M3 12a9 9 0 1 0 3-6.7L3 8M3 3v5h5M12 7v5l3 2"/>',
    "gear": '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/>',
    "moon": '<path d="M20 14.5A8 8 0 0 1 9.5 4 8 8 0 1 0 20 14.5z"/>',
    "sun": '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>',
    "min": '<path d="M6 12h12"/>',
    "max": '<rect x="6" y="6" width="12" height="12" rx="1"/>',
    "restore": '<rect x="5" y="8" width="11" height="11" rx="1"/><path d="M8 8V5h11v11h-3"/>',
    "close": '<path d="M6 6l12 12M18 6 6 18"/>',
    "folder": '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
    "film": '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M7 4v16M17 4v16M3 9h4M3 15h4M17 9h4M17 15h4"/>',
    "note": '<path d="M9 18V5l11-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="17" cy="16" r="3"/>',
    "trash": '<path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3"/>',
    "search": '<circle cx="11" cy="11" r="7"/><path d="m20 20-4-4"/>',
    "check": '<path d="m5 12 5 5 9-10"/>',
    "plus": '<path d="M12 5v14M5 12h14"/>',
    "repeat": '<path d="M17 2l4 4-4 4M3 11V9a3 3 0 0 1 3-3h15M7 22l-4-4 4-4M21 13v2a3 3 0 0 1-3 3H3"/>',
    "clip": '<rect x="8" y="3" width="8" height="4" rx="1"/><path d="M16 5h2a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2h2M10 13l2 2 4-4"/>',
    "alert": '<path d="M12 3 2 20h20zM12 10v4M12 17h.01"/>',
    "ok": '<circle cx="12" cy="12" r="9"/><path d="m8 12 3 3 5-6"/>',
    "link": '<path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1"/>',
    "refresh": '<path d="M20 11a8 8 0 0 0-14.8-4M4 4v4h4M4 13a8 8 0 0 0 14.8 4M20 20v-4h-4"/>',
    "stop": '<rect x="6" y="6" width="12" height="12" rx="1"/>',
}

_cache = {}


def pixmap(name, color, size=18, dpr=2.0, width=1.7):
    key = (name, color, size, dpr, width)
    if key in _cache:
        return _cache[key]
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" '
           f'stroke-width="{width}" stroke-linecap="round" stroke-linejoin="round">{P[name]}</svg>')
    r = QSvgRenderer(QByteArray(svg.encode()))
    pm = QPixmap(int(size * dpr), int(size * dpr))
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    r.render(p, QRectF(0, 0, pm.width(), pm.height()))
    p.end()
    pm.setDevicePixelRatio(dpr)
    _cache[key] = pm
    return pm


def icon(name, color, size=18):
    return QIcon(pixmap(name, color, size))


_bound = []


def bind(button, name, size=18, role="fg"):
    """Attach a themed icon to a button; it is recolored whenever the theme changes."""
    from .theme import theme
    button._icon_spec = (name, size, role)
    button.setIconSize(QSize(size, size))
    button.setIcon(icon(name, theme.t[role] if not role.startswith("#") else role, size))
    if not any(r() is button for r in _bound):
        _bound.append(weakref.ref(button))


def set_name(button, name):
    _, size, role = button._icon_spec
    bind(button, name, size, role)


def refresh_all():
    from .theme import theme
    alive = []
    for ref in _bound:
        b = ref()
        if b is None:
            continue
        try:
            name, size, role = b._icon_spec
            b.setIcon(icon(name, theme.t[role] if not role.startswith("#") else role, size))
            alive.append(ref)
        except RuntimeError:
            pass
    _bound[:] = alive
