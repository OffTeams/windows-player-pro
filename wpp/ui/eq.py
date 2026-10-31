"""10-band equalizer popup. Gains go to mpv's audio filter chain (lavfi equalizer)."""
import math

from PySide6.QtCore import QPointF, Qt, QTimer
from PySide6.QtGui import QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QComboBox, QGridLayout, QHBoxLayout, QLabel, QSlider, QVBoxLayout, QWidget

from ..config import EQ_BANDS, EQ_PRESETS, cfg
from ..theme import theme
from .widgets import Popup, Switch, ThinSlider, label, push


def flabel(f):
    return f"{f // 1000}k" if f >= 1000 else str(f)


def db(v):
    return ("+" if v > 0 else "") + (f"{v:.1f}" if v % 1 else f"{v:.0f}")


class Curve(QWidget):
    def __init__(self, eq):
        super().__init__()
        self.eq = eq
        self.setFixedHeight(48)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        p.setPen(QPen(theme.c("line"), 1))
        p.drawLine(0, h // 2, w, h // 2)
        pen = QPen(theme.c("fg" if self.eq["on"] else "muted"), 2)
        p.setPen(pen)
        path = QPainterPath()
        lo, hi = math.log10(20), math.log10(20000)
        for x in range(0, w + 1, 2):
            lf = lo + x / max(1, w) * (hi - lo)
            g = self.eq["pre"]
            for i, f in enumerate(EQ_BANDS):
                o = lf - math.log10(f)
                if i == 0 and o < 0 or i == len(EQ_BANDS) - 1 and o > 0:
                    k = 1
                else:
                    k = math.exp(-o * o * 30)
                g += self.eq["g"][i] * k
            y = h / 2 - g / 24 * (h / 2 - 3)
            (path.lineTo if x else path.moveTo)(QPointF(x, y))
        p.drawPath(path)


class EqPopup(Popup):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.eq = dict(cfg["eq"])
        self.eq["g"] = list(self.eq["g"])
        self.card.setMinimumWidth(470)
        top = QHBoxLayout()
        top.addWidget(label("Эквалайзер", "sec"))
        top.addStretch()
        self.sw = Switch(self.eq["on"])
        self.state = label("Включён" if self.eq["on"] else "Выключен")
        self.preset = QComboBox()
        self.preset.addItems(list(EQ_PRESETS) + ["Свой"])
        self.preset.setCurrentText(self.eq["preset"] if self.eq["preset"] in EQ_PRESETS else "Свой")
        top.addWidget(self.sw)
        top.addWidget(self.state)
        top.addSpacing(10)
        top.addWidget(self.preset)
        self.v.addLayout(top)
        self.curve = Curve(self.eq)
        self.v.addWidget(self.curve)

        grid = QGridLayout()
        grid.setHorizontalSpacing(4)
        grid.setVerticalSpacing(4)
        self.sliders, self.outs = [], []
        cols = [("Пред.", self.eq["pre"])] + [(flabel(f), g) for f, g in zip(EQ_BANDS, self.eq["g"])]
        for c, (name, val) in enumerate(cols):
            out = QLabel(db(val))
            out.setObjectName("out")
            out.setAlignment(Qt.AlignCenter)
            s = ThinSlider(Qt.Vertical)
            s.setRange(-24, 24)            # half-dB steps
            s.setValue(round(val * 2))
            s.setFixedSize(22, 130)       # narrower sliders make Fusion stretch the groove to the full width
            s.setAccessibleName(name + " Гц" if c else "Предусилитель")
            s.valueChanged.connect(lambda v, i=c: self._moved(i, v / 2))
            n = QLabel(name)
            n.setObjectName("out")
            n.setAlignment(Qt.AlignCenter)
            grid.addWidget(out, 0, c)
            grid.addWidget(s, 1, c, Qt.AlignHCenter)
            grid.addWidget(n, 2, c)
            self.sliders.append(s)
            self.outs.append(out)
        self.v.addLayout(grid)
        foot = QHBoxLayout()
        foot.addStretch()
        reset = push("Сбросить")
        foot.addWidget(reset)
        self.v.addLayout(foot)

        self.sw.toggled.connect(self._toggle)
        self.preset.currentTextChanged.connect(self._preset)
        reset.clicked.connect(lambda: self._preset("Ровно", force=True))
        self.save_timer = QTimer(self, singleShot=True, interval=120, timeout=self._commit)
        self._sync_enabled()

    def _sync_enabled(self):
        for s in self.sliders:
            s.setEnabled(self.eq["on"])
        self.state.setText("Включён" if self.eq["on"] else "Выключен")
        self.curve.update()

    def _toggle(self, on):
        self.eq["on"] = on
        self._sync_enabled()
        self._commit()

    def _preset(self, name, force=False):
        if name not in EQ_PRESETS:
            return
        if force:
            self.eq["pre"] = 0.0
        self.eq["g"] = [float(x) for x in EQ_PRESETS[name]]
        self.eq["preset"] = name
        if not self.eq["on"]:
            self.sw.setChecked(True)
        self.preset.blockSignals(True)
        self.preset.setCurrentText(name)
        self.preset.blockSignals(False)
        for i, s in enumerate(self.sliders):
            s.blockSignals(True)
            val = self.eq["pre"] if i == 0 else self.eq["g"][i - 1]
            s.setValue(round(val * 2))
            self.outs[i].setText(db(val))
            s.blockSignals(False)
        self.curve.update()
        self._commit()

    def _moved(self, i, v):
        self.outs[i].setText(db(v))
        if i == 0:
            self.eq["pre"] = v
        else:
            self.eq["g"][i - 1] = v
            self.eq["preset"] = "Свой"
            self.preset.blockSignals(True)
            self.preset.setCurrentText("Свой")
            self.preset.blockSignals(False)
        self.curve.update()
        self.save_timer.start()

    def _commit(self):
        cfg.set("eq", {"on": self.eq["on"], "pre": self.eq["pre"], "g": list(self.eq["g"]), "preset": self.eq["preset"]})
