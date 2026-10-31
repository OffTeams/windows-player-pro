"""Windows look for Qt popups and the frameless main window."""
import ctypes
import sys

from PySide6.QtCore import QEvent, QObject, QPoint, QRectF, Qt, QTimer
from PySide6.QtGui import QCursor, QPainter, QPen
from PySide6.QtWidgets import QAbstractItemView, QApplication, QComboBox, QLabel, QMenu, QWidget

DWMWA_WINDOW_CORNER_PREFERENCE = 33
DWMWCP_ROUND = 2
GWL_STYLE = -16
WS_MINIMIZEBOX, WS_MAXIMIZEBOX, WS_SYSMENU = 0x00020000, 0x00010000, 0x00080000


def round_corners(widget: QWidget):
    if sys.platform != "win32":
        return
    try:
        pref = ctypes.c_int(DWMWCP_ROUND)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(int(widget.winId()), DWMWA_WINDOW_CORNER_PREFERENCE,
                                                   ctypes.byref(pref), ctypes.sizeof(pref))
    except Exception:
        pass


def enable_taskbar_minimize(widget: QWidget):
    """A frameless window lacks WS_MINIMIZEBOX, so clicking its taskbar button doesn't minimize it. Add the styles back."""
    if sys.platform != "win32":
        return
    try:
        u = ctypes.windll.user32
        u.GetWindowLongPtrW.restype = ctypes.c_ssize_t
        hwnd = int(widget.winId())
        style = u.GetWindowLongPtrW(hwnd, GWL_STYLE)
        u.SetWindowLongPtrW(hwnd, GWL_STYLE, ctypes.c_ssize_t(style | WS_MINIMIZEBOX | WS_MAXIMIZEBOX | WS_SYSMENU))
    except Exception:
        pass


def _make_card_window(w: QWidget):
    """Transparent, shadowless top-level. Must run before the native window exists: Windows picks the
    drop-shadow window class and the alpha mode when the window is created, and ignores later changes —
    which is what left dark square corners and a shadow around the rounded cards."""
    w.setAttribute(Qt.WA_TranslucentBackground)
    # Qt on Windows honours per-pixel alpha only for windows flagged frameless; Qt.Popup alone isn't enough
    w.setWindowFlags(w.windowFlags() | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)


class Tip(QLabel):
    """Tooltip drawn by the app: rounded, no system shadow, compact."""
    _inst = None

    def __init__(self):
        super().__init__(None, Qt.ToolTip | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)
        _make_card_window(self)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setContentsMargins(7, 2, 7, 3)
        self.hide_timer = QTimer(self, singleShot=True, interval=7000, timeout=self.hide)
        self.owner = None

    @classmethod
    def get(cls):
        if cls._inst is None:
            cls._inst = Tip()
        return cls._inst

    def show_for(self, widget, text, gpos):
        from ..theme import theme
        self.owner = widget
        self.setStyleSheet(f"QLabel {{ color: {theme.t['fg']}; font-size: 8.5pt; background: transparent; }}")
        self.setText(text)
        self.adjustSize()
        scr = (widget.screen() or self.screen()).availableGeometry()
        x = min(max(scr.left(), gpos.x() + 2), scr.right() - self.width())
        y = gpos.y() + 20
        if y + self.height() > scr.bottom():
            y = gpos.y() - self.height() - 6
        self.move(x, y)
        self.show()
        self.raise_()
        self.update()
        self.hide_timer.start()

    def paintEvent(self, e):
        from ..theme import theme
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QPen(theme.c("line"), 1))
        p.setBrush(theme.c("raise_"))
        p.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 5, 5)
        p.end()
        super().paintEvent(e)


class PopupRounder(QObject):
    """App-wide filter for popup windows and tooltips."""

    def __init__(self, parent=None):
        super().__init__(parent)
        # Qt plays the list's slide-open animation in a separate opaque window: black corners for ~100 ms
        QApplication.setEffectEnabled(Qt.UI_AnimateCombo, False)

    def eventFilter(self, obj, e):
        t = e.type()
        if t == QEvent.ToolTip and isinstance(obj, QWidget):
            text = obj.toolTip()
            if text:
                Tip.get().show_for(obj, text, e.globalPos())
            else:
                Tip.get().hide()
            return True                 # replaces Qt's own tooltip window
        if t in (QEvent.Leave, QEvent.MouseButtonPress, QEvent.Wheel, QEvent.KeyPress, QEvent.Hide):
            tip = Tip._inst
            if tip is not None and tip.isVisible() and obj is tip.owner:
                tip.hide()
        if t == QEvent.Polish and isinstance(obj, QComboBox):
            # create the list's popup window now, while it can still be made transparent and shadowless
            container = obj.view().window()
            if container is not obj and not container.testAttribute(Qt.WA_WState_Created):
                _make_card_window(container)
                container.setStyleSheet("QComboBoxPrivateContainer { background: transparent; border: none; }")
        elif t == QEvent.Show and isinstance(obj, QMenu) and obj.isWindow():
            round_corners(obj)
        elif t == QEvent.Show and obj.metaObject().className() == "QComboBoxPrivateContainer":
            # the list laid its rows out before the stylesheet made them 30px tall: they overlapped,
            # leaving an empty strip at the bottom of a popup sized for the real height
            view = obj.findChild(QAbstractItemView)
            if view:
                view.doItemsLayout()
        return False
