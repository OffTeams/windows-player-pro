"""Windows Player Pro helper — background clipboard watcher (see wpp/helper_app.py)."""
import logging
import os
import sys

if getattr(sys, "frozen", False):
    _src = os.path.join(os.path.dirname(sys.executable), "app")     # git checkout of the code (see main.py)
    if os.path.isdir(os.path.join(_src, "wpp")):
        sys.path.insert(0, _src)

from wpp import paths  # noqa: E402

paths.setup_native()
logging.basicConfig(filename=str(paths.DATA / "helper.log"), level=logging.WARNING,
                    format="%(asctime)s %(name)s %(levelname)s %(message)s", encoding="utf-8")
sys.excepthook = lambda t, e, tb: logging.getLogger("wpp").error("uncaught", exc_info=(t, e, tb))
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--autoplay-policy=no-user-gesture-required --mute-audio")

from PySide6.QtCore import QCoreApplication, Qt  # noqa: E402
import PySide6.QtWebEngineCore  # noqa: E402,F401  (must be imported before QApplication)
from PySide6.QtWidgets import QApplication  # noqa: E402


def main():
    QCoreApplication.setAttribute(Qt.AA_ShareOpenGLContexts)
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setApplicationName("Windows Player Pro Helper")
    app.setQuitOnLastWindowClosed(False)
    if sys.platform == "win32":
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("WindowsPlayerPro.Helper")
    from wpp.theme import init_theme
    init_theme()
    from wpp.ui.winfx import PopupRounder
    rounder = PopupRounder(app)
    app.installEventFilter(rounder)
    from wpp.helper_app import run
    sys.exit(run())


if __name__ == "__main__":
    main()
