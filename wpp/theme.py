"""Light and dark palettes modelled on the Windows 11 Settings app, and the application stylesheet."""
from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QColor, QGuiApplication, QPalette

from .config import cfg
from .paths import DATA

# Windows 11 Settings: window #F3F3F3 / #202020, cards #FBFBFB / #2B2B2B, accent #005FB8 / #60CDFF
LIGHT = dict(bg="#F3F3F3", panel="#F3F3F3", raise_="#FBFBFB", card_hover="#F6F6F6", fg="#1B1B1B", muted="#5F5F5F",
             line="#E5E5E5", field="#FFFFFF", field_line="#E5E5E5", rail="#C4C4C4", toggle_off="#8A8A8A",
             hover="rgba(0,0,0,10)", sel="rgba(0,0,0,16)", nav_sel="#EAEAEA",
             accent="#005FB8", accent_hover="#1A6FC2", on_accent="#FFFFFF",
             inv_bg="#005FB8", inv_fg="#FFFFFF", track="#C4C4C4",
             stage="#EBEBEB", stage_fg="#1B1B1B", stage_muted="#5F5F5F", menu_hl="#F0F0F0", tip_fg="#1B1B1B",
             ok="#0F7B0F", warn="#9D5D00", err="#C42B1C")
DARK = dict(bg="#202020", panel="#202020", raise_="#2B2B2B", card_hover="#323232", fg="#FFFFFF", muted="#C5C5C5",
            line="#1D1D1D", field="#2D2D2D", field_line="#3A3A3A", rail="#9E9E9E", toggle_off="#9E9E9E",
            hover="rgba(255,255,255,10)", sel="rgba(255,255,255,16)", nav_sel="#2D2D2D",
            accent="#60CDFF", accent_hover="#5AB8E6", on_accent="#000000",
            inv_bg="#60CDFF", inv_fg="#000000", track="#9E9E9E",
            stage="#1B1B1B", stage_fg="#FFFFFF", stage_muted="#C5C5C5", menu_hl="#383838", tip_fg="#FFFFFF",
            ok="#6CCB5F", warn="#FCE100", err="#FF99A4")

UI_FONT = "Segoe UI Variable Text"
MONO_FONT = "Cascadia Mono"


def _asset(name, svg):
    """Small SVGs the stylesheet references by file (menu check dot, combo chevron)."""
    d = DATA / "ui"
    d.mkdir(parents=True, exist_ok=True)
    p = d / name
    if not p.exists() or p.read_text("utf-8") != svg:
        p.write_text(svg, "utf-8")
    return p.as_posix()


class Theme(QObject):
    changed = Signal()

    def __init__(self):
        super().__init__()
        self.t = LIGHT
        hints = QGuiApplication.styleHints()
        hints.colorSchemeChanged.connect(lambda *_: self.apply())
        cfg.changed.connect(lambda k: k == "theme" and self.apply())

    @property
    def dark(self):
        return self.t is DARK

    def c(self, key) -> QColor:
        v = self.t[key]
        if v.startswith("rgba"):
            r, g, b, a = (int(x) for x in v[5:-1].split(","))
            return QColor(r, g, b, a)
        return QColor(v)

    def apply(self):
        mode = cfg["theme"]
        if mode == "system":
            dark = QGuiApplication.styleHints().colorScheme() == Qt.ColorScheme.Dark
        else:
            dark = mode == "dark"
        self.t = DARK if dark else LIGHT
        app = QGuiApplication.instance()
        pal = QPalette()
        t = self.t
        for role, key in ((QPalette.Window, "bg"), (QPalette.Base, "field"), (QPalette.AlternateBase, "raise_"),
                          (QPalette.Text, "fg"), (QPalette.WindowText, "fg"), (QPalette.Button, "raise_"),
                          (QPalette.ButtonText, "fg"), (QPalette.ToolTipBase, "raise_"), (QPalette.ToolTipText, "tip_fg"),
                          (QPalette.Highlight, "menu_hl"), (QPalette.HighlightedText, "fg"), (QPalette.PlaceholderText, "muted")):
            pal.setColor(role, self.c(key))
        app.setPalette(pal)
        app.setStyleSheet(stylesheet(t))
        self.changed.emit()


def stylesheet(t):
    tag = "d" if t is DARK else "l"
    dot = _asset(f"dot_{tag}.svg", f'<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16"><circle cx="8" cy="8" '
                                   f'r="3" fill="{t["fg"]}"/></svg>')
    chev = _asset(f"chev_{tag}.svg", f'<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12">'
                                     f'<path d="M3 4.5 6 7.5 9 4.5" fill="none" stroke="{t["muted"]}" stroke-width="1.4" '
                                     f'stroke-linecap="round" stroke-linejoin="round"/></svg>')
    return f"""
* {{ font-family: "{UI_FONT}", "Segoe UI", sans-serif; font-size: 10pt; color: {t['fg']}; outline: none; }}
QMainWindow, #root {{ background: {t['bg']}; }}
QWidget#page, QScrollArea#page > QWidget > QWidget {{ background: {t['bg']}; }}
QScrollArea {{ border: none; background: {t['bg']}; }}
QDialog {{ background: {t['bg']}; }}

#titlebar {{ background: {t['bg']}; }}
#titlebar QLabel#brand {{ font-size: 9pt; }}
#titlebar QToolButton {{ border: none; border-radius: 0; background: transparent; }}
#titlebar QToolButton:hover {{ background: {t['hover']}; }}
#titlebar QToolButton#close:hover {{ background: #C42B1C; }}

#nav {{ background: {t['bg']}; }}
#nav QPushButton {{ text-align: left; padding: 8px 10px; border: none; border-radius: 4px;
    background: transparent; color: {t['fg']}; }}
#nav QPushButton:hover {{ background: {t['hover']}; }}
#nav QPushButton:checked {{ background: {t['nav_sel']}; }}
#nav QLabel#foot {{ color: {t['muted']}; font-size: 8pt; }}
QLabel#badge {{ background: {t['accent']}; color: {t['on_accent']}; border-radius: 8px; padding: 0 6px;
    font-size: 8pt; font-weight: 600; }}

#stage, #empty {{ background: {t['stage']}; }}
#empty QLabel {{ color: {t['stage_fg']}; }}
#controls {{ background: {t['bg']}; }}
#fsbar #controls {{ background: {t['raise_']}; border: 1px solid {t['line']}; border-radius: 8px; }}
QLabel#time {{ color: {t['muted']}; font-family: "{MONO_FONT}", Consolas; font-size: 8.5pt; }}
#controls QToolButton {{ border: none; border-radius: 4px; padding: 0; background: transparent; }}
#controls QToolButton:hover {{ background: {t['hover']}; }}
#controls QToolButton:pressed {{ background: {t['sel']}; }}
#controls QToolButton:checked {{ background: {t['sel']}; }}
QToolButton#pill {{ border: 1px solid {t['field_line']}; border-radius: 4px; padding: 0 6px; background: {t['raise_']};
    font-family: "{MONO_FONT}", Consolas; font-size: 8.5pt; }}
QToolButton::menu-indicator {{ image: none; width: 0; }}

QLabel#h1 {{ font-size: 20pt; font-weight: 600; }}
QLabel#desc, QLabel#hint {{ color: {t['muted']}; font-size: 9pt; }}
QLabel#sec {{ font-size: 10pt; font-weight: 600; }}
QLabel#ok {{ color: {t['ok']}; font-size: 9pt; }}
QLabel#err {{ color: {t['err']}; font-size: 9pt; }}
QLabel#warn {{ color: {t['warn']}; font-size: 9pt; }}
QLabel#warnTag, QLabel#okTag, QLabel#errTag, QLabel#tag {{ border: 1px solid {t['field_line']}; border-radius: 4px;
    padding: 0 6px; font-size: 8pt; color: {t['muted']}; }}
QLabel#chip {{ border: 1px solid {t['field_line']}; border-radius: 4px; padding: 1px 6px; background: {t['raise_']};
    font-family: "{MONO_FONT}", Consolas; font-size: 8pt; color: {t['fg']}; }}
QLabel#okTag {{ color: {t['ok']}; border-color: {t['ok']}; }}
QLabel#warnTag {{ color: {t['warn']}; border-color: {t['warn']}; }}
QLabel#errTag {{ color: {t['err']}; border-color: {t['err']}; }}
QFrame#list, QFrame#card {{ background: {t['raise_']}; border: 1px solid {t['line']}; border-radius: 6px; }}
QFrame#row {{ background: transparent; border: none; border-top: 1px solid {t['line']}; }}
QFrame#row[first="true"] {{ border-top: none; }}
QFrame#row:hover {{ background: {t['card_hover']}; }}
QFrame#row[current="true"] {{ background: {t['nav_sel']}; }}
QLabel#name {{ font-size: 10pt; }}
QLabel#meta {{ color: {t['muted']}; font-size: 8.5pt; }}
QLabel#num {{ color: {t['muted']}; font-family: "{MONO_FONT}", Consolas; font-size: 8.5pt; }}
QFrame#row QToolButton {{ border: none; border-radius: 4px; padding: 5px; background: transparent; }}
QFrame#row QToolButton:hover {{ background: {t['hover']}; }}
QCheckBox {{ spacing: 8px; }}
QCheckBox::indicator {{ width: 16px; height: 16px; border: 1px solid {t['toggle_off']}; border-radius: 4px; background: {t['field']}; }}
QCheckBox::indicator:checked {{ background: {t['accent']}; border-color: {t['accent']}; }}
QRadioButton::indicator {{ width: 14px; height: 14px; border: 1px solid {t['toggle_off']}; border-radius: 8px; background: {t['field']}; }}
QRadioButton::indicator:checked {{ border: 4px solid {t['accent']}; width: 8px; height: 8px; background: {t['on_accent']}; }}

QPushButton {{ background: {t['raise_']}; border: 1px solid {t['field_line']}; border-radius: 4px; padding: 5px 14px; }}
QPushButton:hover {{ background: {t['card_hover']}; }}
QPushButton:pressed {{ background: {t['bg']}; color: {t['muted']}; }}
QPushButton#primary {{ background: {t['accent']}; color: {t['on_accent']}; border-color: {t['accent']}; }}
QPushButton#primary:hover {{ background: {t['accent_hover']}; border-color: {t['accent_hover']}; }}
QPushButton:disabled {{ color: {t['muted']}; }}
QPushButton#primary:disabled {{ background: {t['rail']}; border-color: {t['rail']}; color: {t['bg']}; }}
QLineEdit, QComboBox, QSpinBox {{ background: {t['field']}; border: 1px solid {t['field_line']}; border-radius: 4px; padding: 5px 10px;
    selection-background-color: {t['accent']}; selection-color: {t['on_accent']}; }}
QLineEdit:focus {{ border-bottom: 2px solid {t['accent']}; }}
QComboBox {{ combobox-popup: 0; padding-right: 26px; background: {t['raise_']}; }}
QComboBox:hover {{ background: {t['card_hover']}; }}
QComboBox::drop-down {{ border: none; width: 24px; subcontrol-origin: padding; subcontrol-position: center right; }}
QComboBox::down-arrow {{ image: url({chev}); width: 12px; height: 12px; }}
QComboBox QAbstractItemView {{ background: {t['raise_']}; border: 1px solid {t['field_line']}; border-radius: 8px;
    padding: 4px; outline: none; selection-background-color: {t['menu_hl']}; selection-color: {t['fg']}; }}
QComboBox QAbstractItemView::item {{ min-height: 30px; padding: 0 10px; border-radius: 4px; color: {t['fg']}; }}
QComboBox QAbstractItemView::item:hover, QComboBox QAbstractItemView::item:selected {{ background: {t['menu_hl']};
    color: {t['fg']}; }}
QComboBox QAbstractItemView::indicator {{ width: 16px; height: 16px; border: 1px solid {t['toggle_off']}; border-radius: 4px;
    background: {t['field']}; margin-right: 6px; }}
QComboBox QAbstractItemView::indicator:checked {{ background: {t['accent']}; border-color: {t['accent']}; }}

QProgressBar {{ background: {t['rail']}; border: none; border-radius: 2px; max-height: 4px; min-height: 4px; }}
QProgressBar::chunk {{ background: {t['accent']}; border-radius: 2px; }}

QMenu {{ background: {t['raise_']}; border: 1px solid {t['field_line']}; padding: 4px; }}
QMenu::item {{ padding: 7px 36px 7px 32px; border-radius: 4px; margin: 1px 0; }}
QMenu::item:selected {{ background: {t['menu_hl']}; color: {t['fg']}; }}
QMenu::item:disabled {{ color: {t['muted']}; }}
QMenu::separator {{ height: 1px; background: {t['field_line']}; margin: 4px 8px; }}
QMenu::indicator {{ width: 16px; height: 16px; left: 10px; }}
QMenu::indicator:checked {{ image: url({dot}); }}
QMenu::indicator:unchecked {{ image: none; }}
QFrame#popup {{ background: {t['raise_']}; border: 1px solid {t['field_line']}; border-radius: 8px; }}
QFrame#popup QLabel#out {{ color: {t['muted']}; font-family: "{MONO_FONT}", Consolas; font-size: 8pt; }}

QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {t['rail']}; border-radius: 3px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: {t['muted']}; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; background: none; }}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: none; }}
QScrollBar:horizontal {{ height: 0; }}

QFrame#toast {{ background: {t['raise_']}; border: 1px solid {t['field_line']}; border-radius: 6px; }}
QFrame#toast QLabel {{ color: {t['fg']}; }}
"""


theme = None


def init_theme():
    global theme
    theme = Theme()
    theme.apply()
    return theme
