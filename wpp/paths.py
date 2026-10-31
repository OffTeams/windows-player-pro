"""Filesystem locations and native library setup."""
import os
import shutil
import sys
from pathlib import Path

from . import APP_ID

FROZEN = getattr(sys, "frozen", False)
ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))     # runtime files (vendor, assets)
SRC = Path(__file__).resolve().parent.parent      # git work tree: the repo in development, <install>\app when installed
VENDOR = ROOT / "vendor"
DATA = Path(os.environ.get("APPDATA") or Path.home()) / APP_ID


def _hide_child_consoles():
    """A windowed app has no console, so every console tool it starts (deno for yt-dlp, ffmpeg, git) would
    flash its own black window. Start them all with CREATE_NO_WINDOW instead."""
    import subprocess
    if os.name != "nt" or getattr(subprocess.Popen, "_wpp_hidden", False):
        return
    orig = subprocess.Popen.__init__

    def init(self, *args, **kw):
        flags = kw.get("creationflags", 0) or 0
        if not flags & 0x00000008:                      # DETACHED_PROCESS already has no console
            kw["creationflags"] = flags | 0x08000000     # CREATE_NO_WINDOW
        orig(self, *args, **kw)
    subprocess.Popen.__init__ = init
    subprocess.Popen._wpp_hidden = True


def setup_native() -> None:
    """Put vendor/ (libmpv-2.dll, ffmpeg.exe) on the DLL search path before `import mpv`."""
    DATA.mkdir(parents=True, exist_ok=True)
    _hide_child_consoles()
    if VENDOR.is_dir():
        os.environ["PATH"] = str(VENDOR) + os.pathsep + os.environ.get("PATH", "")
        if hasattr(os, "add_dll_directory"):
            os.add_dll_directory(str(VENDOR))


def ffmpeg_location() -> str | None:
    exe = VENDOR / "ffmpeg.exe"
    if exe.exists():
        return str(exe)
    return shutil.which("ffmpeg")


def deno_location() -> str | None:
    """JavaScript runtime yt-dlp needs for YouTube signature/n-challenge solving."""
    exe = VENDOR / "deno.exe"
    if exe.exists():
        return str(exe)
    return shutil.which("deno")


def user_folder(name: str) -> Path:
    """Known user folders (Downloads, Videos), falling back to the home directory."""
    p = Path.home() / name
    return p if p.exists() else Path.home()
