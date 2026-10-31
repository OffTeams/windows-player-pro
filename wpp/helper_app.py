"""Background helper: watches the clipboard while the player may be closed.

It has no window, taskbar button or tray icon of its own. It probes copied links and shows the offer card;
the actions are carried out by the player, which it starts when needed — in the background (tray only) for
downloads and the options window, in front for watching.
"""
import sys

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from . import ipc
from .config import cfg
from .net import ytdl
from .theme import theme
from .ui.notify import ClipboardWatcher, NotificationCenter


class Helper:
    def __init__(self, app):
        self.app = app
        self.server = ipc.Server(ipc.HELPER)
        self.server.message.connect(self._message)
        self.link = ipc.PlayerLink()
        self.notes = NotificationCenter(None)
        self.link.failed.connect(lambda m: self.notes.show(m, "", "alert", "err", timeout=6000))

        def send(cmd, background, **data):
            self.link.send({"cmd": cmd, **data}, background=background)

        actions = {
            "download": lambda opt, res: send("download", True, opt=ipc.asdict(opt), res=ipc.result_to_dict(res)),
            "play": lambda opt, res: send("play", False, opt=ipc.asdict(opt), res=ipc.result_to_dict(res)),
            "options": lambda res: send("options", True, res=ipc.result_to_dict(res)),
            "queue": lambda: send("show", False, page="downloads"),
        }
        self.watcher = ClipboardWatcher(self.notes, actions)
        ytdl.warmup()

    def _message(self, msg):
        cmd = msg.get("cmd")
        if cmd == "reload":
            cfg.reload()
            theme.apply()
        elif cmd == "check":
            self.watcher.check(msg.get("text", ""), force=True)
        elif cmd == "quit":
            self.quit()

    def quit(self):
        for n in list(self.notes.notes):
            n.close()
        QTimer.singleShot(0, self.app.quit)


def run():
    app = QApplication.instance()
    if ipc.is_running(ipc.HELPER):         # one helper per user session
        return 0
    if not cfg["helper"] and "--autostart" in sys.argv:
        return 0
    helper = Helper(app)
    app._helper = helper
    return app.exec()
