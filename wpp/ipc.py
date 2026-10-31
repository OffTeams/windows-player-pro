"""Local IPC between the player, a second player launch and the background helper (QLocalSocket, JSON lines)."""
import json
import os
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

from .net.common import Option, ProbeResult
from .paths import FROZEN, ROOT

PLAYER = "WindowsPlayerPro.Player"
HELPER = "WindowsPlayerPro.Helper"
NO_WINDOW = 0x08000000 if os.name == "nt" else 0


def _pythonw():
    exe = Path(sys.executable)
    w = exe.with_name("pythonw.exe")
    return str(w if w.exists() else exe)


def player_cmd(*args):
    if FROZEN:
        return [str(Path(sys.executable).with_name("WindowsPlayerPro.exe")), *args]
    return [_pythonw(), str(ROOT / "main.py"), *args]


def helper_cmd(*args):
    if FROZEN:
        return [str(Path(sys.executable).with_name("WPPHelper.exe")), *args]
    return [_pythonw(), str(ROOT / "helper.py"), *args]


def launch(cmd):
    subprocess.Popen(cmd, cwd=str(ROOT), creationflags=NO_WINDOW | 0x00000008)   # DETACHED_PROCESS


def allow_foreground():
    """The helper was just clicked, so it may bring windows forward; pass that right on to the player.
    Without it Windows only flashes the player's taskbar button instead of showing the window."""
    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.user32.AllowSetForegroundWindow(-1)      # ASFW_ANY
        except Exception:
            pass


def result_to_dict(res: ProbeResult):
    return asdict(res)


def result_from_dict(d):
    d = dict(d)
    d["options"] = [Option(**o) for o in d.get("options", [])]
    return ProbeResult(**d)


def option_from_dict(d):
    return Option(**d)


def send(name, msg, timeout=1500):
    """Deliver one message to a running instance. Returns False when nobody listens."""
    s = QLocalSocket()
    s.connectToServer(name)
    if not s.waitForConnected(timeout):
        return False
    s.write((json.dumps(msg, ensure_ascii=False) + "\n").encode("utf-8"))
    s.flush()
    s.waitForBytesWritten(timeout)
    s.disconnectFromServer()
    return True


def is_running(name):
    s = QLocalSocket()
    s.connectToServer(name)
    ok = s.waitForConnected(300)
    if ok:
        s.disconnectFromServer()
    return ok


class Server(QObject):
    """Listens under `name`; every received JSON message is emitted as a dict."""
    message = Signal(dict)

    def __init__(self, name, parent=None):
        super().__init__(parent)
        self.name = name
        self.server = QLocalServer(self)
        self.server.setSocketOptions(QLocalServer.UserAccessOption)
        if not self.server.listen(name):
            QLocalServer.removeServer(name)          # stale socket from a crashed run
            self.server.listen(name)
        self.server.newConnection.connect(self._conn)
        self._buf = {}

    def _conn(self):
        while self.server.hasPendingConnections():
            s = self.server.nextPendingConnection()
            self._buf[s] = b""
            s.readyRead.connect(lambda s=s: self._read(s))
            s.disconnected.connect(lambda s=s: (self._read(s), self._buf.pop(s, None), s.deleteLater()))

    def _read(self, s):
        if s not in self._buf:
            return
        self._buf[s] += bytes(s.readAll())
        while b"\n" in self._buf[s]:
            line, self._buf[s] = self._buf[s].split(b"\n", 1)
            try:
                self.message.emit(json.loads(line.decode("utf-8")))
            except ValueError:
                pass


class PlayerLink(QObject):
    """Used by the helper: sends a command to the player, starting it first when it isn't running."""
    failed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.pending = []
        self.timer = QTimer(self, interval=400, timeout=self._retry)
        self.tries = 0

    def send(self, msg, background=False):
        allow_foreground()          # the options window or the player may need to come to the front
        if send(PLAYER, msg):
            return
        self.pending.append(msg)
        if not self.timer.isActive():
            # background: the player starts in the tray only, without its main window
            launch(player_cmd("--background") if background else player_cmd())
            self.tries = 0
            self.timer.start()

    def _retry(self):
        self.tries += 1
        if not self.pending:
            self.timer.stop()
            return
        if send(PLAYER, self.pending[0], 300):
            self.pending.pop(0)
            while self.pending and send(PLAYER, self.pending[0], 300):
                self.pending.pop(0)
            if not self.pending:
                self.timer.stop()
        elif self.tries > 75:           # ~30 s
            self.timer.stop()
            self.pending.clear()
            self.failed.emit("Плеер не запустился")
