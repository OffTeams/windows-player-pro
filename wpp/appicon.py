"""Application icon matching the Windows version (Windows 11 Fluent, Windows 10 flat, Windows 7 Aero)."""
import sys

from PySide6.QtGui import QIcon

from .paths import ROOT, SRC


def os_style():
    if sys.platform != "win32":
        return "win11"
    v = sys.getwindowsversion()
    if v.major >= 10:
        return "win11" if v.build >= 22000 else "win10"
    return "win7"


def icon_path(style=None):
    name = f"{style or os_style()}.ico"
    for base in (SRC, ROOT):        # the git checkout first, so a new icon arrives with an update
        p = base / "assets" / "icons" / name
        if p.exists():
            return p
    return ROOT / "assets" / "icons" / name


def app_icon():
    p = icon_path()
    return QIcon(str(p)) if p.exists() else QIcon()
