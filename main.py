# Project maintainer: Nick Volpison
# SPDX-License-Identifier: CC0-1.0
#
# PUBLIC-DOMAIN DEDICATION
# 
# This project is dedicated to the public domain under CC0 1.0 Universal,
# to the extent permitted by law:
# https://creativecommons.org/publicdomain/zero/1.0/
# 
# The source code was generated primarily with the assistance of
# artificial intelligence. Human contributions, where copyrightable,
# are included in the dedication to the extent the relevant rights
# can be waived.
# 
# Third-party components, dependencies, and other materials may remain
# subject to their respective licenses and rights. See the relevant
# notices for details.
# 
# WARRANTY DISCLAIMER
# 
# THE SOFTWARE IS PROVIDED "AS IS" AND THE AUTHOR DISCLAIMS ALL
# WARRANTIES WITH REGARD TO THIS SOFTWARE INCLUDING ALL IMPLIED WARRANTIES
# OF MERCHANTABILITY AND FITNESS. IN NO EVENT SHALL THE AUTHOR BE LIABLE
# FOR ANY SPECIAL, DIRECT, INDIRECT, OR CONSEQUENTIAL DAMAGES OR ANY
# DAMAGES WHATSOEVER RESULTING FROM LOSS OF USE, DATA OR PROFITS, WHETHER IN
# AN ACTION OF CONTRACT, NEGLIGENCE OR OTHER TORTIOUS ACTION, ARISING OUT
# OF OR IN CONNECTION WITH THE USE OR PERFORMANCE OF THIS SOFTWARE.

import sys

from PyQt6.QtCore import QEvent, Qt
from PyQt6.QtGui import QFontDatabase
from PyQt6.QtWidgets import (
    QFileDialog,
    QApplication, QCheckBox, QComboBox, QFrame, QHBoxLayout, QLabel, QMainWindow,
    QPushButton, QSizePolicy, QSlider, QSpinBox, QDoubleSpinBox, QVBoxLayout, QWidget,
)

from canvas import Canvas


# Exact zoom steps (percent)
ZOOM_PRESETS = [
    12.5, 25, 50, 75, 100, 150, 200, 300, 400, 600, 800, 1200, 1600, 2400, 3200
]


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Brush Demo – Sub-pixel brush study")
        self.resize(1400, 900)

        # Canvas must exist before sidebar widgets that close over self.canvas
        self.canvas = Canvas(1200, 800)
        self.canvas.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        central = QWidget()
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(6)

        # ------------------------------------------------------------------
        # Left sidebar – all brush parameters
        # ------------------------------------------------------------------
        sidebar = QFrame()
        sidebar.setFrameShape(QFrame.Shape.StyledPanel)
        sidebar.setFixedWidth(248)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(8, 8, 8, 8)
        side.setSpacing(6)

        def section(title: str):
            lbl = QLabel(title)
            lbl.setStyleSheet("font-weight: bold; color: #aaa; margin-top: 4px;")
            side.addWidget(lbl)

        def make_param(label, min_v, max_v, value, is_float, slot, suffix=""):
            """Vertical row: label + slider + spin, kept in sync."""
            row = QHBoxLayout()
            row.setSpacing(4)
            row.addWidget(QLabel(label), 0)

            slider = QSlider(Qt.Orientation.Horizontal)
            slider.setRange(min_v if not is_float else int(min_v * 100),
                            max_v if not is_float else int(max_v * 100))
            slider.setValue(value if not is_float else int(value * 100))
            slider.setFixedHeight(18)
            slider.setFocusPolicy(Qt.FocusPolicy.NoFocus)

            if is_float:
                spin = QDoubleSpinBox()
                spin.setRange(min_v, max_v)
                spin.setDecimals(1)
                spin.setSingleStep(0.5)
                spin.setValue(float(value))
            else:
                spin = QSpinBox()
                spin.setRange(min_v, max_v)
                spin.setValue(int(value))

            spin.setFixedWidth(58)
            spin.setFixedHeight(22)
            spin.setSuffix(suffix)
            spin.setFocusPolicy(Qt.FocusPolicy.ClickFocus)

            def on_slider(v, sp=spin, cb=slot, fl=is_float):
                sp.blockSignals(True)
                sp.setValue(v / 100.0 if fl else v)
                sp.blockSignals(False)
                cb(v / 100.0 if fl else v)

            def on_spin(v, sl=slider, cb=slot, fl=is_float):
                sl.blockSignals(True)
                sl.setValue(int(round(v * 100)) if fl else int(v))
                sl.blockSignals(False)
                cb(v)

            slider.valueChanged.connect(on_slider)
            spin.valueChanged.connect(on_spin)
            spin.editingFinished.connect(self.canvas.setFocus)   # Enter / focus lost
            spin.installEventFilter(self)                        # see eventFilter()
            spin.lineEdit().installEventFilter(self)

            row.addWidget(slider, 1)
            row.addWidget(spin, 0)
            side.addLayout(row)
            return slider, spin

        # ---- Brush core ----
        section("Brush")
        make_param("Size", 1, 160, 42, False,
                   lambda v: self.canvas.set_brush_size(int(v)), "px")
        make_param("Hard", 0, 100, 72, False,
                   lambda v: self.canvas.set_hardness(int(v)), "%")
        make_param("Space", 1, 200, 13, False,
                   lambda v: self.canvas.set_spacing(int(v)), "%")
        make_param("Flow", 1, 100, 100, False,
                   lambda v: self.canvas.set_flow(int(v)), "%")
        make_param("Opac", 1, 100, 100, False,
                   lambda v: self.canvas.set_opacity(int(v)), "%")

        # ---- Smoothing ----
        section("Smoothing")
        make_param("Strength", 0, 100, 0, False,
                   lambda v: self.canvas.set_stabilizer(int(v)), "%")

        smode_row = QHBoxLayout()
        smode_row.addWidget(QLabel("Mode"))
        self.smode_combo = QComboBox()
        self.smode_combo.addItem("Pulled string (px)", "string")
        self.smode_combo.addItem("Low-pass (time)", "lowpass")
        self.smode_combo.setFixedHeight(22)
        self.smode_combo.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.smode_combo.setToolTip(
            "Pulled string: the tip hangs on a rope (up to 80 px). Lag is bounded and\n"
            "independent of the device's event rate.\n"
            "Low-pass: exponential smoothing (up to 150 ms) driven by event timestamps.")
        self.smode_combo.currentIndexChanged.connect(
            lambda _i: self.canvas.set_stabilizer_mode(self.smode_combo.currentData()))
        smode_row.addWidget(self.smode_combo, 1)
        side.addLayout(smode_row)

        self.catchup_cb = QCheckBox("Catch-up on stroke end")
        self.catchup_cb.setChecked(True)
        self.catchup_cb.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.catchup_cb.setToolTip(
            "On release, glide the tip to where the pen was lifted.\n"
            "Off: the stroke stops wherever the smoothed tip is.")
        self.catchup_cb.toggled.connect(self.canvas.set_stabilizer_catch_up)
        side.addWidget(self.catchup_cb)

        # ---- Accumulation ----
        section("Accumulation")
        accum_row = QHBoxLayout()
        accum_row.addWidget(QLabel("Mode"))
        self.accum_combo = QComboBox()
        self.accum_combo.addItem("Build-up", "buildup")
        self.accum_combo.addItem("Max", "max")
        self.accum_combo.setFixedHeight(22)
        self.accum_combo.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.accum_combo.setToolTip(
            "Build-up: overlaps get denser (classic over)\n"
            "Max: only the strongest dab wins — no extra darkening"
        )
        self.accum_combo.currentIndexChanged.connect(self._on_accum_changed)
        accum_row.addWidget(self.accum_combo, 1)
        side.addLayout(accum_row)

        # ---- Dynamics ----
        section("Dynamics")
        self.psize_cb = QCheckBox("Pressure → Size")
        self.psize_cb.setChecked(True)
        self.psize_cb.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.psize_cb.setToolTip("Pen pressure controls dab size (like Shape Dynamics)")
        self.psize_cb.toggled.connect(self.canvas.set_pressure_size)
        side.addWidget(self.psize_cb)

        self.popac_cb = QCheckBox("Pressure → Opacity")
        self.popac_cb.setChecked(True)
        self.popac_cb.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.popac_cb.setToolTip("Pen pressure controls dab opacity (like Transfer)")
        self.popac_cb.toggled.connect(self.canvas.set_pressure_opacity)
        side.addWidget(self.popac_cb)

        self.snap_cb = QCheckBox("Snap tiny hard (1 px)")
        self.snap_cb.setChecked(False)
        self.snap_cb.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.snap_cb.setToolTip(
            "Pencil-style: brushes ≤ 1.5 px with hardness ≥ 95% snap to pixel centres.\n"
            "Removes all fractional coverage (1-bit, aliased line). Off = true sub-pixel.")
        self.snap_cb.toggled.connect(self.canvas.set_snap_tiny_hard)
        side.addWidget(self.snap_cb)

        # ---- Study toggles ----
        section("Study")
        self.grid_cb = QCheckBox("Pixel grid (G)")
        self.grid_cb.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.grid_cb.setToolTip("Pixel grid when zoom ≥ 400%")
        self.grid_cb.toggled.connect(self.canvas.set_show_grid)
        side.addWidget(self.grid_cb)

        self.hover_cb = QCheckBox("Hover dab preview (H)")
        self.hover_cb.setChecked(True)
        self.hover_cb.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.hover_cb.setToolTip("Preview real dab footprint")
        self.hover_cb.toggled.connect(self.canvas.set_show_hover_dab)
        side.addWidget(self.hover_cb)

        self.debug_cb = QCheckBox("Debug paths (D)")
        self.debug_cb.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.debug_cb.setToolTip("Show raw / stabilized path / dab centres")
        self.debug_cb.toggled.connect(self.canvas.set_show_debug)
        side.addWidget(self.debug_cb)

        # ---- Actions ----
        section("Actions")
        btn_row = QHBoxLayout()
        btn_row.setSpacing(4)

        clear_btn = QPushButton("Clear")
        clear_btn.setFixedHeight(26)
        clear_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        clear_btn.clicked.connect(self.canvas.clear)
        btn_row.addWidget(clear_btn)

        csv_btn = QPushButton("CSV")
        csv_btn.setFixedHeight(26)
        csv_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        csv_btn.setToolTip("Export last stroke (inputs + dabs) to human-readable CSV")
        csv_btn.clicked.connect(self._export_csv)
        btn_row.addWidget(csv_btn)

        bin_btn = QPushButton("BIN")
        bin_btn.setFixedHeight(26)
        bin_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        bin_btn.setToolTip(
            "Export last stroke as compressed NPZ (binary).\n"
            "Much smaller than CSV; same data + settings.")
        bin_btn.clicked.connect(self._export_bin)
        btn_row.addWidget(bin_btn)

        side.addLayout(btn_row)
        side.addStretch(1)

        root.addWidget(sidebar)

        # ------------------------------------------------------------------
        # Right side: canvas + thin zoom bar
        # ------------------------------------------------------------------
        right = QVBoxLayout()
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(4)

        right.addWidget(self.canvas, stretch=1)

        # Thin zoom / status bar at the bottom of the canvas area
        zoom_bar = QHBoxLayout()
        zoom_bar.setContentsMargins(0, 0, 0, 0)
        zoom_bar.setSpacing(4)

        zoom_bar.addWidget(QLabel("Zoom"))

        self.zoom_out_btn = QPushButton("−")
        self.zoom_out_btn.setFixedSize(28, 22)
        self.zoom_out_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.zoom_out_btn.setToolTip("Previous preset")
        self.zoom_out_btn.clicked.connect(self._zoom_out)
        zoom_bar.addWidget(self.zoom_out_btn)

        self.zoom_label = QLabel("100%")
        self.zoom_label.setFixedWidth(56)
        self.zoom_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        zoom_bar.addWidget(self.zoom_label)

        self.zoom_in_btn = QPushButton("+")
        self.zoom_in_btn.setFixedSize(28, 22)
        self.zoom_in_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.zoom_in_btn.setToolTip("Next preset")
        self.zoom_in_btn.clicked.connect(self._zoom_in)
        zoom_bar.addWidget(self.zoom_in_btn)

        for pct in (50, 100, 200, 400, 800, 1600):
            b = QPushButton(f"{pct:g}%")
            b.setFixedHeight(22)
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            b.clicked.connect(lambda checked=False, p=pct: self._set_zoom_percent(p))
            zoom_bar.addWidget(b)

        zoom_bar.addStretch()
        right.addLayout(zoom_bar)

        root.addLayout(right, stretch=1)

        self.canvas.zoomChanged.connect(self._update_zoom_label)
        # Live pointer readout (permanent widget, so temporary messages don't hide it)
        self.pointer_label = QLabel("")
        self.pointer_label.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        self.pointer_label.setMinimumWidth(620)
        self.statusBar().addPermanentWidget(self.pointer_label)
        self.canvas.pointerInfo.connect(self.pointer_label.setText)

        # Keyboard toggles (G/H/D) must update the checkboxes too
        self.canvas.optionToggled.connect(self._on_option_toggled)
        # (Space/G/H/D routing while a number box has focus: see eventFilter(),
        #  installed on the spin boxes in make_param.)

        self.canvas.setFocus()
        self._update_zoom_label(self.canvas.zoom)

    # ------------------------------------------------------------------
    def _current_preset_index(self) -> int:
        pct = self.canvas.zoom * 100.0
        best = 0
        best_dist = abs(ZOOM_PRESETS[0] - pct)
        for i, p in enumerate(ZOOM_PRESETS):
            d = abs(p - pct)
            if d < best_dist:
                best, best_dist = i, d
        return best

    def _set_zoom_percent(self, percent: float):
        """Set zoom to an exact preset, keeping view centre stable."""
        cx = self.canvas.width() / 2.0
        cy = self.canvas.height() / 2.0
        target = percent / 100.0
        if self.canvas.zoom <= 0:
            return
        factor = target / self.canvas.zoom
        self.canvas.zoom_at(factor, cx, cy)

    def _zoom_in(self):
        idx = self._current_preset_index()
        if idx < len(ZOOM_PRESETS) - 1:
            self._set_zoom_percent(ZOOM_PRESETS[idx + 1])

    def _zoom_out(self):
        idx = self._current_preset_index()
        if idx > 0:
            self._set_zoom_percent(ZOOM_PRESETS[idx - 1])

    def _export_csv(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Export stroke CSV", "stroke.csv", "CSV (*.csv)")
        if not path:
            return
        if self.canvas.export_stroke_csv(path):
            self.statusBar().showMessage(f"Saved {path}", 3000)
        else:
            self.statusBar().showMessage("No stroke data to export — draw first", 3000)

    def _export_bin(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Export stroke binary (NPZ)", "stroke.npz",
            "Compressed NPZ (*.npz)")
        if not path:
            return
        if not path.lower().endswith(".npz"):
            path += ".npz"
        if self.canvas.export_stroke_npz(path):
            self.statusBar().showMessage(f"Saved {path}", 3000)
        else:
            self.statusBar().showMessage("No stroke data to export — draw first", 3000)

    def _on_option_toggled(self, name: str, value: bool):
        cb = {"grid": self.grid_cb, "hover": self.hover_cb, "debug": self.debug_cb}.get(name)
        if cb is not None:
            cb.blockSignals(True)       # the canvas already applied the change
            cb.setChecked(value)
            cb.blockSignals(False)

    def eventFilter(self, obj, event):
        """Space (pan) and G/H/D work wherever the keyboard focus is in this window.

        Only digits are typed into the number boxes, so these keys are free there.
        The filter is installed only on the spin boxes (and their line edits), so
        no other widget or window (e.g. file dialogs) is ever affected.
        """
        if (event.type() in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease)
                and obj is not self.canvas
                and hasattr(obj, "window") and obj.window() is self
                and event.key() in (Qt.Key.Key_Space, Qt.Key.Key_G,
                                    Qt.Key.Key_H, Qt.Key.Key_D)):
            if event.type() == QEvent.Type.KeyPress:
                self.canvas.keyPressEvent(event)
            else:
                self.canvas.keyReleaseEvent(event)
            return True
        return super().eventFilter(obj, event)

    def _on_accum_changed(self, _index: int = 0):
        mode = self.accum_combo.currentData()
        self.canvas.set_accum_mode(mode)

    def _update_zoom_label(self, zoom: float):
        pct = zoom * 100.0
        if abs(pct - round(pct)) < 0.05:
            self.zoom_label.setText(f"{pct:.0f}%")
        else:
            self.zoom_label.setText(f"{pct:.1f}%")


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
