"""Self-update from the app's git repository (fetch + fast-forward pull)."""
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

import requests
from PySide6.QtCore import QObject, Signal

from . import __version__
from .paths import FROZEN, ROOT, SRC, VENDOR

RELEASES_REPO = "OffTeams/windows-player-pro"     # installed builds take new versions from this repo's Releases

NO_WINDOW = 0x08000000 if os.name == "nt" else 0


def git_exe():
    bundled = VENDOR / "git" / "cmd" / "git.exe"
    return str(bundled) if bundled.exists() else shutil.which("git")


def _gh():
    for p in (shutil.which("gh"), r"C:\Program Files\GitHub CLI\gh.exe",
              os.path.expandvars(r"%LOCALAPPDATA%\Programs\GitHub CLI\gh.exe")):
        if p and os.path.exists(p):
            return p
    return None


def git(*args, timeout=60):
    exe = git_exe()
    if not exe:
        raise RuntimeError("git не найден")
    # safe.directory: exFAT/FAT drives record no file owner, and git would refuse to touch the repo
    cmd = [exe, "-c", f"safe.directory={SRC.as_posix()}", "-c", "credential.interactive=never"]
    gh = _gh()
    if gh:      # signed-in GitHub CLI answers git's credential requests (private repositories)
        cmd += ["-c", f"credential.https://github.com.helper=!'{Path(gh).as_posix()}' auth git-credential"]
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
    r = subprocess.run([*cmd, "-C", str(SRC), *args], capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=timeout, creationflags=NO_WINDOW, env=env)
    if r.returncode != 0:
        out = (r.stderr or r.stdout).strip()
        if "Authentication failed" in out or "could not read Username" in out or "Repository not found" in out:
            raise RuntimeError("нет доступа к репозиторию обновлений (он закрытый: войдите в GitHub или откройте доступ)")
        raise RuntimeError(out.splitlines()[-1] if out else "ошибка git")
    return r.stdout.strip()


def is_repo():
    return (SRC / ".git").exists() and bool(git_exe())


def runtime_outdated():
    """The installed executable bundles Python packages; code that needs newer ones requires the full installer."""
    if not FROZEN:
        return False
    try:
        need = int((SRC / "runtime.txt").read_text().strip())
        have = int((ROOT / "runtime.txt").read_text().strip())
        return need > have
    except (OSError, ValueError):
        return False


def remote_url():
    try:
        return git("remote", "get-url", "origin")
    except Exception:
        return ""


def set_remote(url):
    url = url.strip()
    if not url:
        try:
            git("remote", "remove", "origin")
        except Exception:
            pass
        return
    if remote_url():
        git("remote", "set-url", "origin", url)
    else:
        git("remote", "add", "origin", url)


def current_version():
    try:
        return git("log", "-1", "--format=%h · %cd", "--date=format:%d.%m.%Y")
    except Exception:
        return ""


class Updater(QObject):
    status = Signal(str, str)           # text, tone: "" | ok | warn | err
    available = Signal(int, str)        # commits behind, summary
    applied = Signal(str)               # summary; restart needed

    def __init__(self):
        super().__init__()
        self.busy = False

    def _bg(self, fn):
        if self.busy:
            return
        self.busy = True

        def run():
            try:
                fn()
            except Exception as e:
                self.status.emit(f"Обновление не удалось: {e}", "err")
            finally:
                self.busy = False
        threading.Thread(target=run, daemon=True).start()

    # ----- installed build: GitHub Releases -----
    installer_ready = Signal(str)       # path of the downloaded setup .exe

    def _release_check(self, quiet):
        if not quiet:
            self.status.emit("Проверяю обновления…", "")
        r = requests.get(f"https://api.github.com/repos/{RELEASES_REPO}/releases/latest",
                         headers={"Accept": "application/vnd.github+json", "User-Agent": "WindowsPlayerPro"}, timeout=20)
        if r.status_code == 404:
            return self.status.emit("Источник обновлений недоступен", "warn") if not quiet else None
        r.raise_for_status()
        rel = r.json()
        latest = rel.get("tag_name", "").lstrip("v")
        asset = next((a for a in rel.get("assets", []) if a["name"].lower().endswith("setup.exe")), None)
        if asset and _ver(latest) > _ver(__version__):
            self.release = {"version": latest, "url": asset["browser_download_url"], "size": asset.get("size", 0),
                            "notes": (rel.get("body") or "").strip()}
            self.available.emit(1, f"Версия {latest}\n" + self.release["notes"][:600])
            self.status.emit(f"Доступна версия {latest}", "ok")
        elif not quiet:
            self.status.emit("Установлена последняя версия", "ok")

    def _release_apply(self):
        rel = getattr(self, "release", None)
        if not rel:
            return self.status.emit("Сначала проверьте обновления", "warn")
        dst = Path(tempfile.gettempdir()) / f"WindowsPlayerPro-{rel['version']}-setup.exe"
        with requests.get(rel["url"], stream=True, timeout=30, headers={"User-Agent": "WindowsPlayerPro"}) as r:
            r.raise_for_status()
            total = int(r.headers.get("Content-Length") or rel["size"] or 0)
            done = 0
            with open(dst, "wb") as f:
                for chunk in r.iter_content(1 << 20):
                    f.write(chunk)
                    done += len(chunk)
                    if total:
                        self.status.emit(f"Загружаю обновление: {done * 100 // total}%", "")
        self.status.emit("Обновление загружено", "ok")
        self.installer_ready.emit(str(dst))

    def check(self, quiet=False):
        def work():
            if not is_repo():
                return self._release_check(quiet)
            if not remote_url():
                return self.status.emit("Источник обновлений не задан. Укажите адрес git-репозитория", "warn")
            if not quiet:
                self.status.emit("Проверяю обновления…", "")
            git("fetch", "--quiet", "origin", timeout=120)
            try:
                upstream = git("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
            except Exception:
                branch = git("rev-parse", "--abbrev-ref", "HEAD")
                upstream = f"origin/{branch}"
                git("branch", "--set-upstream-to", upstream)
            behind = int(git("rev-list", "--count", f"HEAD..{upstream}") or 0)
            if behind:
                log = git("log", "--format=• %s", f"HEAD..{upstream}", "-n", "8")
                self.available.emit(behind, log)
                self.status.emit(f"Доступно обновление: {behind} изм.", "ok")
            elif not quiet:
                self.status.emit("Установлена последняя версия", "ok")
        self._bg(work)

    def _needs_new_runtime(self):
        try:
            need = int(git("show", "@{u}:runtime.txt").strip())
            have = int((ROOT / "runtime.txt").read_text().strip())
            return need > have
        except Exception:
            return False

    def apply(self):
        def work():
            if not is_repo():
                return self._release_apply()
            if git("status", "--porcelain", "--untracked-files=no"):
                return self.status.emit("Есть локальные изменения в файлах программы. Сохраните или отмените их, затем обновите", "err")
            if FROZEN and self._needs_new_runtime():
                # the new code needs packages this executable lacks: take the full installer instead of a pull
                self.status.emit("Для этой версии нужна новая сборка, загружаю установщик…", "")
                self._release_check(quiet=True)
                return self._release_apply()
            before = git("rev-parse", "HEAD")
            self.status.emit("Загружаю обновление…", "")
            git("pull", "--ff-only", "--quiet", timeout=300)
            changed = git("diff", "--name-only", before, "HEAD")
            if "requirements.txt" in changed.split() and not FROZEN:
                self.status.emit("Обновляю зависимости…", "")
                subprocess.run([sys.executable, "-m", "pip", "install", "-q", "-r", str(ROOT / "requirements.txt")],
                               capture_output=True, creationflags=NO_WINDOW, timeout=900)
            summary = git("log", "--format=• %s", f"{before}..HEAD", "-n", "8")
            self.status.emit("Обновление установлено. Перезапустите плеер", "ok")
            self.applied.emit(summary)
        self._bg(work)


def run_installer(path):
    """Start the downloaded setup silently and quit; the setup closes the helper, updates the files and
    starts the player again. History, playlist and downloads live in %APPDATA% and are not touched."""
    from PySide6.QtWidgets import QApplication
    subprocess.Popen([path, "/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CLOSEAPPLICATIONS", "/UPDATE=1"],
                     creationflags=0x00000008)
    QApplication.quit()


def _ver(s):
    return tuple(int(x) for x in re.findall(r"\d+", s or "0")[:4]) or (0,)


def restart():
    """Restart the player on the updated code; the helper is stopped too and the new player starts it again."""
    from PySide6.QtCore import QProcess
    from PySide6.QtWidgets import QApplication
    from . import ipc
    ipc.send(ipc.HELPER, {"cmd": "quit"}, 500)
    # argv[0] is the program itself; passing it on would make the new player open its own .exe as media
    QProcess.startDetached(sys.executable, sys.argv[1:] if FROZEN else [str(SRC / "main.py"), *sys.argv[1:]])
    QApplication.quit()
