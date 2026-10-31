"""Start the helper with Windows: HKCU Run key, the same entry Task Manager lists under «Автозагрузка».

Task Manager's on/off switch is stored separately in StartupApproved\\Run; both are honoured.
"""
import subprocess
import winreg

from .ipc import helper_cmd

RUN = r"Software\Microsoft\Windows\CurrentVersion\Run"
APPROVED = r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run"
NAME = "Windows Player Pro Helper"


def command():
    return subprocess.list2cmdline(helper_cmd("--autostart"))


def _approved():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, APPROVED) as k:
            data, _ = winreg.QueryValueEx(k, NAME)
            return not (data and data[0] & 1)      # 0x02/0x06 = enabled, 0x03/0x07 = disabled in Task Manager
    except OSError:
        return True


def _qt_has_value():
    from PySide6.QtCore import QSettings
    s = QSettings("HKEY_CURRENT_USER\\" + RUN, QSettings.NativeFormat)
    return s.contains(NAME)


def is_enabled():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN) as k:
            winreg.QueryValueEx(k, NAME)
    except FileNotFoundError:
        import logging
        qt = _qt_has_value()
        logging.getLogger("wpp").warning("autostart: winreg found no value; Qt says %s; module %s", qt, winreg)
        if not qt:
            return False
    except OSError:
        import logging
        logging.getLogger("wpp").exception("autostart: reading %s", RUN)
        return False
    ok = _approved()
    if not ok:
        import logging
        logging.getLogger("wpp").warning("autostart: switched off in Task Manager (StartupApproved)")
    return ok


def set_enabled(on):
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN) as k:
        if on:
            winreg.SetValueEx(k, NAME, 0, winreg.REG_SZ, command())
        else:
            try:
                winreg.DeleteValue(k, NAME)
            except OSError:
                pass
    if on:
        try:        # if it was switched off in Task Manager, our explicit «on» switches it back
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, APPROVED) as k:
                winreg.SetValueEx(k, NAME, 0, winreg.REG_BINARY, b"\x02" + b"\x00" * 11)
        except OSError:
            pass


def refresh_command():
    """Keep the Run entry pointing at the installed helper (e.g. after the install folder moved).
    Runs from source never touch it, so a development checkout can't take over the user's autostart."""
    from .paths import FROZEN
    if FROZEN and is_enabled():
        set_enabled(True)
