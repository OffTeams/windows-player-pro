# PyInstaller spec: two executables (player + helper) sharing one folder.
# Built "onedir" and without UPX: packed/self-extracting executables are what antivirus heuristics flag most often.
# Run: python -m PyInstaller build_tools/WindowsPlayerPro.spec --noconfirm --distpath build/dist --workpath build/work
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files

ROOT = Path(SPECPATH).parent
sys.path.insert(0, str(ROOT))
from wpp import __version__  # noqa: E402

VENDOR = ROOT / "vendor"
datas = [(str(ROOT / "assets"), "assets"), (str(ROOT / "runtime.txt"), ".")]
for name in ("libmpv-2.dll", "ffmpeg.exe", "deno.exe"):
    datas.append((str(VENDOR / name), "vendor"))
datas.append((str(VENDOR / "git"), "vendor/git"))          # MinGit: the installed player updates itself with `git pull`
datas += collect_data_files("yt_dlp_ejs")
# the player's own code (wpp) is NOT frozen: it ships as a git checkout in <install>\app. These stdlib modules are
# bundled in advance so later code updates can use them without a new runtime (bump runtime.txt when that isn't enough).
stdlib_spare = ["sqlite3", "zipfile", "hashlib", "hmac", "base64", "csv", "xml.etree.ElementTree", "xml.dom.minidom",
                "email", "email.mime.text", "asyncio", "concurrent.futures", "http.server", "http.cookiejar", "urllib.request",
                "gzip", "bz2", "lzma", "shlex", "fnmatch", "glob", "statistics", "secrets", "uuid", "wave", "struct",
                "configparser", "textwrap", "difflib", "heapq", "bisect", "queue", "socket", "ssl", "winreg", "ctypes.wintypes"]
hidden = ["yt_dlp_ejs", "PySide6.QtWebEngineCore", "PySide6.QtNetwork", "PySide6.QtSvg", *stdlib_spare]
excludes = ["tkinter", "PySide6.QtQml", "PySide6.QtQuick", "PySide6.Qt3DCore", "PySide6.QtCharts", "PySide6.QtDataVisualization",
            "PySide6.QtMultimedia", "PySide6.QtPdf", "PySide6.QtDesigner", "PySide6.QtTest", "py7zr", "PyInstaller",
            "PIL"]


def version_file(name, desc, exe):
    v = tuple(int(x) for x in __version__.split(".")) + (0,) * (4 - len(__version__.split(".")))
    p = ROOT / "build" / f"version_{name}.txt"
    p.parent.mkdir(exist_ok=True)
    p.write_text(f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers={v}, prodvers={v}, mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[StringFileInfo([StringTable('041904B0', [
      StringStruct('CompanyName', 'OFFTeams'),
      StringStruct('FileDescription', '{desc}'),
      StringStruct('FileVersion', '{__version__}'),
      StringStruct('InternalName', '{name}'),
      StringStruct('LegalCopyright', 'OFFTeams'),
      StringStruct('OriginalFilename', '{exe}'),
      StringStruct('ProductName', 'Windows Player Pro'),
      StringStruct('ProductVersion', '{__version__}')])]),
    VarFileInfo([VarStruct('Translation', [0x0419, 1200])])])
""", "utf-8")
    return str(p)


def analysis(script):
    return Analysis([str(ROOT / script)], pathex=[str(ROOT)], datas=datas, hiddenimports=hidden, excludes=excludes,
                    noarchive=False)


a_player = analysis("main.py")
a_helper = analysis("helper.py")
# wpp is analysed (so its dependencies get bundled) but left out of the archive: it is loaded from <install>\app
a_player.pure = [m for m in a_player.pure if not (m[0] == "wpp" or m[0].startswith("wpp."))]
a_helper.pure = [m for m in a_helper.pure if not (m[0] == "wpp" or m[0].startswith("wpp."))]
pyz_player = PYZ(a_player.pure)
pyz_helper = PYZ(a_helper.pure)
icon = str(ROOT / "assets" / "icons" / "win11.ico")

exe_player = EXE(pyz_player, a_player.scripts, [], exclude_binaries=True, name="WindowsPlayerPro", console=False,
                 icon=icon, upx=False, version=version_file("WindowsPlayerPro", "Windows Player Pro", "WindowsPlayerPro.exe"))
exe_helper = EXE(pyz_helper, a_helper.scripts, [], exclude_binaries=True, name="WPPHelper", console=False,
                 icon=icon, upx=False, version=version_file("WPPHelper", "Windows Player Pro Helper", "WPPHelper.exe"))
coll = COLLECT(exe_player, exe_helper, a_player.binaries, a_player.datas, a_helper.binaries, a_helper.datas,
               upx=False, name="WindowsPlayerPro")
