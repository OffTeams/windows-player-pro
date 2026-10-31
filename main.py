"""Windows Player Pro — entry point."""
import faulthandler
import logging
import os
import sys

if getattr(sys, "frozen", False):
    # installed build: the player's code lives in <install>\app as a git checkout (updated by `git pull`);
    # the executable only carries the runtime (Python, Qt, libmpv, FFmpeg)
    _src = os.path.join(os.path.dirname(sys.executable), "app")
    if os.path.isdir(os.path.join(_src, "wpp")):
        sys.path.insert(0, _src)

from wpp import APP_NAME, paths  # noqa: E402

paths.setup_native()
logging.basicConfig(filename=str(paths.DATA / "player.log"), level=logging.WARNING,
                    format="%(asctime)s %(name)s %(levelname)s %(message)s", encoding="utf-8")
try:
    faulthandler.enable(open(paths.DATA / "crash.log", "a", encoding="utf-8"))
except OSError:
    pass


def _log_exception(exc_type, exc, tb):
    """The windowed build has no console: write uncaught errors (Qt slots included) to player.log."""
    logging.getLogger("wpp").error("uncaught", exc_info=(exc_type, exc, tb))


sys.excepthook = _log_exception
import threading  # noqa: E402
threading.excepthook = lambda a: _log_exception(a.exc_type, a.exc_value, a.exc_traceback)

os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--autoplay-policy=no-user-gesture-required --mute-audio")

from PySide6.QtCore import QCoreApplication, Qt  # noqa: E402
import PySide6.QtWebEngineCore  # noqa: E402,F401  (must be imported before QApplication)
from PySide6.QtWidgets import QApplication  # noqa: E402


def main():
    QCoreApplication.setAttribute(Qt.AA_ShareOpenGLContexts)
    app = QApplication(sys.argv)
    app.setStyle("Fusion")      # the native windows11 style ignores parts of the stylesheet (sliders, combo popups)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(APP_NAME)
    app.setQuitOnLastWindowClosed(False)
    if sys.platform == "win32":
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("WindowsPlayerPro.Player")
    from wpp.appicon import app_icon
    app.setWindowIcon(app_icon())       # every window (dialogs, tray) shares it, so the taskbar never falls back to a default

    files = [os.path.abspath(a) if "://" not in a else a for a in sys.argv[1:]
             if not a.startswith("-") and not a.lower().endswith((".exe", ".py"))]
    from wpp import ipc
    # one player per user: a second launch hands its files to the running window and exits
    if ipc.send(ipc.PLAYER, {"cmd": "open", "files": files} if files else {"cmd": "show"}):
        return

    import locale
    locale.setlocale(locale.LC_NUMERIC, "C")    # libmpv requires the C numeric locale

    from wpp.theme import init_theme
    init_theme()
    from wpp.ui.winfx import PopupRounder
    rounder = PopupRounder(app)
    app.installEventFilter(rounder)
    from wpp.ui.main_window import MainWindow
    win = MainWindow(files=files)
    if "--background" not in sys.argv:      # started by the helper: tray icon only, no main window
        win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
