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


from __future__ import annotations

import math

import numpy as np
from PyQt6.QtCore import QPointF, QRect, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QImage, QMouseEvent, QPainter, QPen, QTabletEvent, QWheelEvent
from PyQt6.QtWidgets import QWidget

from brush_engine import BrushEngine


class Canvas(QWidget):
    """Qt view + input handling. All painting maths lives in BrushEngine."""

    zoomChanged = pyqtSignal(float)
    pointerInfo = pyqtSignal(str)            # live readout for the status bar
    optionToggled = pyqtSignal(str, bool)    # "grid" | "hover" | "debug" (keyboard)

    def __init__(self, width=1200, height=800):
        super().__init__()

        self.engine = BrushEngine(width, height)
        self.doc_width = width
        self.doc_height = height

        # View transform
        self.zoom = 1.0
        self.offset_x = 0.0
        self.offset_y = 0.0
        self.min_zoom = 0.1
        self.max_zoom = 32.0

        # Interaction state
        self.drawing = False
        self.panning = False
        self.space_held = False
        self.last_pan_pos = None
        self._hover: tuple[float, float] | None = None   # screen px
        self._view_initialised = False

        # Study / investigation toggles
        self.show_grid = False
        self.grid_min_zoom = 4.0          # only draw grid when zoomed in enough
        self.show_hover_dab = True        # preview the real dab footprint
        self.show_debug = False           # dab centres + raw input samples

        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_TabletTracking, True)
        self._tablet_down = False
        self._last_pressure = 1.0        # last NON-ZERO pressure of the current stroke
        self._last_ev_t: float | None = None   # for the event-rate readout
        self._rate_hz = 0.0
        self._apply_cursor()

    @property
    def buffer(self) -> np.ndarray:
        return self.engine.buffer

    # ------------------------------------------------------------------
    def screen_to_doc(self, sx: float, sy: float) -> tuple[float, float]:
        return (sx - self.offset_x) / self.zoom, (sy - self.offset_y) / self.zoom

    def doc_to_screen(self, dx: float, dy: float) -> tuple[float, float]:
        return dx * self.zoom + self.offset_x, dy * self.zoom + self.offset_y

    # ------------------------------------------------------------------
    def set_brush_size(self, value: int):
        self.engine.size = float(value)
        self.update()

    def set_hardness(self, value: int):
        self.engine.hardness = value / 100.0
        self.update()

    def set_spacing(self, value: int):
        self.engine.spacing = max(0.01, value / 100.0)

    def set_flow(self, value: int):
        self.engine.flow = value / 100.0

    def set_opacity(self, value: int):
        self.engine.opacity = value / 100.0

    def set_snap_tiny_hard(self, checked: bool):
        self.engine.snap_tiny_hard = bool(checked)

    def set_pressure_size(self, checked: bool):
        self.engine.pressure_size = bool(checked)

    def set_pressure_opacity(self, checked: bool):
        self.engine.pressure_opacity = bool(checked)

    def set_show_grid(self, checked: bool):
        self.show_grid = checked
        self.update()

    def set_show_hover_dab(self, checked: bool):
        self.show_hover_dab = checked
        self.update()

    def set_show_debug(self, checked: bool):
        self.show_debug = checked
        self.update()

    def set_stabilizer(self, value: int):
        """UI sends 0–100."""
        self.engine.stabilizer = max(0.0, min(1.0, value / 100.0))

    def set_stabilizer_mode(self, mode: str):
        self.engine.stabilizer_mode = mode if mode in ("string", "lowpass") else "string"

    def set_stabilizer_catch_up(self, checked: bool):
        self.engine.stabilizer_catch_up = bool(checked)

    def set_accum_mode(self, mode: str):
        """mode: 'buildup' or 'max'."""
        self.engine.accum_mode = mode if mode in ("buildup", "max") else "buildup"

    def clear(self):
        self.drawing = False
        self.engine.clear()
        self._flush_dirty()

    def export_stroke_csv(self, path: str) -> bool:
        return self.engine.export_stroke_csv(path)

    def export_stroke_npz(self, path: str) -> bool:
        return self.engine.export_stroke_npz(path)

    # ------------------------------------------------------------------
    def zoom_at(self, factor: float, sx: float, sy: float):
        new_zoom = float(np.clip(self.zoom * factor, self.min_zoom, self.max_zoom))
        if new_zoom == self.zoom:
            return
        dx, dy = self.screen_to_doc(sx, sy)
        self.zoom = new_zoom
        self.offset_x = sx - dx * new_zoom
        self.offset_y = sy - dy * new_zoom
        self.update()
        self.zoomChanged.emit(self.zoom)

    def reset_view(self):
        self.zoom = 1.0
        self.offset_x = (self.width() - self.doc_width) / 2.0
        self.offset_y = (self.height() - self.doc_height) / 2.0
        self.update()
        self.zoomChanged.emit(self.zoom)

    def showEvent(self, event):
        super().showEvent(event)
        if not self._view_initialised:
            self._view_initialised = True
            self.reset_view()

    def _apply_cursor(self):
        if self.panning:
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
        elif self.space_held:
            self.setCursor(Qt.CursorShape.OpenHandCursor)
        else:
            self.setCursor(Qt.CursorShape.BlankCursor)

    # ------------------------------------------------------------------
    def _flush_dirty(self):
        d = self.engine.take_dirty()
        if d is None:
            return
        sx0, sy0 = self.doc_to_screen(d[0], d[1])
        sx1, sy1 = self.doc_to_screen(d[2], d[3])
        x, y = math.floor(sx0) - 2, math.floor(sy0) - 2
        self.update(QRect(x, y, math.ceil(sx1) - x + 3, math.ceil(sy1) - y + 3))

    def _cursor_rect(self, hx: float, hy: float) -> QRect:
        # Must cover the full hover-dab patch (radius + AA margin) in screen space.
        # Using a generous pad prevents trails at high zoom / small brushes.
        doc_reach = self.engine.size * 0.5 + 2.0
        r = int(math.ceil(doc_reach * self.zoom)) + 4
        return QRect(int(hx) - r, int(hy) - r, 2 * r + 1, 2 * r + 1)

    def _set_hover(self, pos: QPointF | None):
        old = self._hover
        self._hover = None if pos is None else (pos.x(), pos.y())
        # Invalidate both old and new regions so previous dab edges are cleared.
        for h in (old, self._hover):
            if h is not None:
                self.update(self._cursor_rect(*h))

    def leaveEvent(self, event):
        self._set_hover(None)
        self._last_ev_t = None
        self._rate_hz = 0.0
        self.pointerInfo.emit("")
        super().leaveEvent(event)

    def _report_pointer(self, event, pressure: float | None = None,
                        tilt: tuple[float, float] | None = None):
        """Publish: fractional document position, the pixel under the cursor,
        pen pressure/tilt (tablet only) and the pointer event rate."""
        pos = event.position()
        dx, dy = self.screen_to_doc(pos.x(), pos.y())

        t = self._event_time(event)
        if self._last_ev_t is not None:
            dt = t - self._last_ev_t
            if 0.0 < dt < 0.25:
                inst = 1.0 / dt
                self._rate_hz = inst if self._rate_hz == 0.0 else 0.9 * self._rate_hz + 0.1 * inst
        self._last_ev_t = t

        ix, iy = math.floor(dx), math.floor(dy)
        buf = self.engine.buffer
        if 0 <= ix < buf.shape[1] and 0 <= iy < buf.shape[0]:
            r, g, b, a = (int(v) for v in buf[iy, ix])
            pix = f"px ({ix},{iy}) RGBA {r},{g},{b},{a}"
        else:
            pix = "px outside document"
        pen = "mouse" if pressure is None else f"P {pressure:.3f}"
        if tilt is not None:
            pen += f"  tilt {tilt[0]:+.0f}°,{tilt[1]:+.0f}°"
        self.pointerInfo.emit(
            f"x {dx:9.3f}  y {dy:9.3f}  |  {pix}  |  {pen}  |  {self._rate_hz:4.0f} Hz")

    # ------------------------------------------------------------------
    # Pixel grid (screen-space overlay, only when zoomed in)
    # ------------------------------------------------------------------
    def _draw_pixel_grid(self, painter: QPainter):
        if not self.show_grid or self.zoom < self.grid_min_zoom:
            return

        # Visible document range in document pixels
        x0, y0 = self.screen_to_doc(0, 0)
        x1, y1 = self.screen_to_doc(self.width(), self.height())

        ix0 = max(0, math.floor(x0))
        iy0 = max(0, math.floor(y0))
        ix1 = min(self.doc_width, math.ceil(x1) + 1)
        iy1 = min(self.doc_height, math.ceil(y1) + 1)

        # Fade grid intensity a bit at very high zoom so it doesn't dominate
        alpha = int(np.clip(40 + self.zoom * 3, 40, 90))
        painter.setPen(QPen(QColor(0, 0, 0, alpha), 1.0))

        # Vertical lines
        for ix in range(ix0, ix1 + 1):
            sx, _ = self.doc_to_screen(ix, 0)
            painter.drawLine(QPointF(sx, 0), QPointF(sx, self.height()))

        # Horizontal lines
        for iy in range(iy0, iy1 + 1):
            _, sy = self.doc_to_screen(0, iy)
            painter.drawLine(QPointF(0, sy), QPointF(self.width(), sy))

    # ------------------------------------------------------------------
    # Hover dab preview – shows the real hardness falloff footprint
    # ------------------------------------------------------------------
    def _draw_hover_dab(self, painter: QPainter, hx: float, hy: float):
        """Draw a semi-transparent preview of the dab that would be stamped."""
        dx, dy = self.screen_to_doc(hx, hy)
        size = self.engine.size
        hardness = self.engine.hardness
        r = size * 0.5
        if r < 0.05:
            return

        # Same radial profile the engine uses
        reach = r + 1.5
        # Work in a small local buffer in document space, then draw scaled
        side = int(math.ceil(reach * 2)) + 2
        if side < 2:
            return

        # Build alpha mask at document resolution
        # Patch top-left in document space. The dab centre inside the patch is
        # then simply (cursor - top-left), which is exact for ANY patch size.
        # (The old `cx - 0.5 + frac` form was 0.5 px off whenever `side` was even.)
        doc_x = math.floor(dx) - (side // 2)
        doc_y = math.floor(dy) - (side // 2)
        local_cx = dx - doc_x
        local_cy = dy - doc_y

        ys = np.arange(side, dtype=np.float32)
        xs = np.arange(side, dtype=np.float32)
        # Distance from each *pixel centre* (i+0.5) — same convention as engine
        dist = np.sqrt(
            (xs[None, :] + 0.5 - local_cx) ** 2 +
            (ys[:, None] + 0.5 - local_cy) ** 2
        )

        h = min(max(hardness, 0.0), 1.0)
        inner, outer = r * h, r
        if outer - inner <= 1.0:
            mid = 0.5 * (inner + outer)
            outer = mid + 0.5
            alpha = np.clip(outer - dist, 0.0, 1.0)
        else:
            t = np.clip((dist - inner) / (outer - inner), 0.0, 1.0)
            alpha = 1.0 - t * t * (3.0 - 2.0 * t)

        alpha = np.clip(alpha * 0.45, 0.0, 1.0)  # keep preview translucent

        # RGBA preview image
        rgba = np.zeros((side, side, 4), dtype=np.uint8)
        col = self.engine.color
        rgba[..., 0] = col[0]
        rgba[..., 1] = col[1]
        rgba[..., 2] = col[2]
        rgba[..., 3] = (alpha * 255).astype(np.uint8)

        qimg = QImage(rgba.data, side, side, side * 4, QImage.Format.Format_RGBA8888)

        # Draw in document space under the current view transform
        painter.save()
        painter.translate(self.offset_x, self.offset_y)
        painter.scale(self.zoom, self.zoom)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
        painter.drawImage(QPointF(doc_x, doc_y), qimg)
        painter.restore()

        # Also draw a thin outline so the extent is obvious
        sr = r * self.zoom
        c = QPointF(hx, hy)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(0, 0, 0, 160), 1.0))
        painter.drawEllipse(c, sr + 0.5, sr + 0.5)
        painter.setPen(QPen(QColor(255, 255, 255, 180), 1.0))
        painter.drawEllipse(c, max(sr - 0.5, 0.0), max(sr - 0.5, 0.0))
        painter.drawPoint(c)


    # ------------------------------------------------------------------
    # Debug overlay: raw input samples + dab centres (last stroke)
    # ------------------------------------------------------------------
    def _draw_debug_overlay(self, painter: QPainter):
        if not self.show_debug:
            return

        from PyQt6.QtCore import QRectF
        from PyQt6.QtGui import QPolygonF

        eng = self.engine
        inputs = eng.last_input_pts
        stab = getattr(eng, "last_stabilized_pts", [])
        dabs = eng.last_dab_centres
        if not inputs and not dabs and not stab:
            return

        def _xy(p):
            return p[0], p[1]

        painter.save()
        painter.translate(self.offset_x, self.offset_y)
        painter.scale(self.zoom, self.zoom)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        # Raw input path (blue)
        if len(inputs) >= 2:
            poly = QPolygonF([QPointF(*_xy(p)) for p in inputs])
            pen = QPen(QColor(30, 120, 255, 180))
            pen.setWidth(0)
            painter.setPen(pen)
            painter.drawPolyline(poly)

        half = 1.3 / max(self.zoom, 0.01)
        painter.setPen(QPen(QColor(20, 80, 220, 230), 0))
        painter.setBrush(QColor(80, 160, 255, 160))
        for p in inputs:
            x, y = _xy(p)
            painter.drawRect(QRectF(x - half, y - half, half * 2, half * 2))

        # Stabilized path (green) — what spacing/dabs actually follow
        if len(stab) >= 2:
            poly = QPolygonF([QPointF(*_xy(p)) for p in stab])
            pen = QPen(QColor(20, 160, 70, 220))
            pen.setWidth(0)
            painter.setPen(pen)
            painter.drawPolyline(poly)
        painter.setPen(QPen(QColor(10, 140, 50, 230), 0))
        painter.setBrush(QColor(40, 200, 90, 160))
        r = 1.1 / max(self.zoom, 0.01)
        for p in stab:
            x, y = _xy(p)
            painter.drawEllipse(QPointF(x, y), r, r)

        # Dab centres (red)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(220, 40, 40, 230), 0))
        cross = 2.0 / max(self.zoom, 0.01)
        for d in dabs:
            x, y = _xy(d)
            painter.drawLine(QPointF(x - cross, y), QPointF(x + cross, y))
            painter.drawLine(QPointF(x, y - cross), QPointF(x, y + cross))
            painter.drawEllipse(QPointF(x, y), cross * 0.4, cross * 0.4)

        painter.restore()

        painter.setPen(QColor(20, 20, 20))
        painter.drawText(
            8, 18,
            f"Debug: {len(inputs)} raw · {len(stab)} stabilized · {len(dabs)} dabs"
            f"  |  stab {eng.stabilizer*100:.0f}%",
        )
        painter.setPen(QColor(30, 90, 200))
        painter.drawText(8, 34, "blue  = raw pointer samples")
        painter.setPen(QColor(10, 130, 50))
        painter.drawText(8, 50, "green = stabilized path (feeds spacing)")
        painter.setPen(QColor(200, 30, 30))
        painter.drawText(8, 66, "red   = dab centres")

    # ------------------------------------------------------------------
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(event.rect(), QColor(60, 60, 60))

        buf = self.engine.buffer
        h, w = buf.shape[:2]
        qimg = QImage(buf.data, w, h, buf.strides[0], QImage.Format.Format_RGBA8888)

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, self.zoom < 1.0)
        painter.translate(self.offset_x, self.offset_y)
        painter.scale(self.zoom, self.zoom)
        painter.drawImage(0, 0, qimg)
        painter.restore()

        # Pixel grid on top of the document
        self._draw_pixel_grid(painter)

        # Hover preview
        if self._hover is not None and not (self.panning or self.space_held or self.drawing):
            hx, hy = self._hover
            if self.show_hover_dab:
                self._draw_hover_dab(painter, hx, hy)
            else:
                # Simple outline only
                r = self.engine.size * 0.5 * self.zoom
                c = QPointF(hx, hy)
                painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.setPen(QPen(QColor(0, 0, 0, 190), 1.0))
                painter.drawEllipse(c, r + 0.5, r + 0.5)
                painter.setPen(QPen(QColor(255, 255, 255, 210), 1.0))
                painter.drawEllipse(c, max(r - 0.5, 0.0), max(r - 0.5, 0.0))
                painter.drawPoint(c)

        # Debug overlay last so it stays visible on top
        self._draw_debug_overlay(painter)

    # ------------------------------------------------------------------
    def wheelEvent(self, event: QWheelEvent):
        delta = event.angleDelta().y()
        if delta == 0:
            return
        pos = event.position()
        self.zoom_at(1.15 ** (delta / 120.0), pos.x(), pos.y())

    def keyPressEvent(self, event):
        key = event.key()
        cx, cy = self.width() / 2.0, self.height() / 2.0
        if key == Qt.Key.Key_Space and not event.isAutoRepeat():
            self.space_held = True
            self._apply_cursor()
            self.update()
        elif key in (Qt.Key.Key_Plus, Qt.Key.Key_Equal):
            self.zoom_at(1.2, cx, cy)
        elif key == Qt.Key.Key_Minus:
            self.zoom_at(1 / 1.2, cx, cy)
        elif key == Qt.Key.Key_0:
            self.reset_view()
        elif key == Qt.Key.Key_G:
            self.set_show_grid(not self.show_grid)
            self.optionToggled.emit("grid", self.show_grid)
        elif key == Qt.Key.Key_H:
            self.set_show_hover_dab(not self.show_hover_dab)
            self.optionToggled.emit("hover", self.show_hover_dab)
        elif key == Qt.Key.Key_D:
            self.set_show_debug(not self.show_debug)
            self.optionToggled.emit("debug", self.show_debug)
        else:
            super().keyPressEvent(event)

    def keyReleaseEvent(self, event):
        if event.key() == Qt.Key.Key_Space and not event.isAutoRepeat():
            self.space_held = False
            self._apply_cursor()
            self.update()
        else:
            super().keyReleaseEvent(event)


    @staticmethod
    def _event_time(event) -> float:
        """Event time in seconds. Qt stamps events when the OS delivered them,
        which is more faithful than the time our handler happens to run."""
        return event.timestamp() / 1000.0

    def _pointer_doc(self, event) -> tuple[float, float, float, float, float]:
        """Return (doc_x, doc_y, pressure, tilt_x, tilt_y) from mouse or tablet event.

        Qt reports pressure 0 on TabletRelease (and can on the very first move).
        Feeding that into the stroke would taper/fade its last span, so a zero
        is replaced by the last non-zero pressure seen during this stroke.
        """
        if isinstance(event, QTabletEvent):
            pos = event.position()
            pr = float(event.pressure())
            if pr > 0.0:
                self._last_pressure = pr
            elif event.type() == QTabletEvent.Type.TabletRelease:
                pr = self._last_pressure
            return (*self.screen_to_doc(pos.x(), pos.y()), pr, float(event.xTilt()), float(event.yTilt()))
        pos = event.position()
        return (*self.screen_to_doc(pos.x(), pos.y()), 1.0, 0.0, 0.0)

    def tabletEvent(self, event: QTabletEvent):
        t = event.type()
        pos = event.position()

        if t == QTabletEvent.Type.TabletPress:
            if self.space_held:
                self.panning = True
                self.last_pan_pos = pos
                self._apply_cursor()
                self.update()
                event.accept()
                return
            self._last_pressure = 1.0
            dx, dy, pr, tx, ty = self._pointer_doc(event)
            self.drawing = True
            self._tablet_down = True
            self.engine.begin_stroke(dx, dy, pr, tx, ty, t=self._event_time(event))
            self._flush_dirty()
            event.accept()
            return

        if t == QTabletEvent.Type.TabletMove:
            if self.panning and self.last_pan_pos is not None:
                self.offset_x += pos.x() - self.last_pan_pos.x()
                self.offset_y += pos.y() - self.last_pan_pos.y()
                self.last_pan_pos = pos
                self.update()
                event.accept()
                return
            self._set_hover(pos)
            self._report_pointer(event, float(event.pressure()),
                                 (float(event.xTilt()), float(event.yTilt())))
            if self.drawing and self._tablet_down:
                dx, dy, pr, tx, ty = self._pointer_doc(event)
                self.engine.add_point(dx, dy, pr, tx, ty, t=self._event_time(event))
                self._flush_dirty()
            event.accept()
            return

        if t == QTabletEvent.Type.TabletRelease:
            if self.panning:
                self.panning = False
                self.last_pan_pos = None
                self._apply_cursor()
                self.update()
                event.accept()
                return
            if self.drawing and self._tablet_down:
                dx, dy, pr, tx, ty = self._pointer_doc(event)
                self.engine.end_stroke(dx, dy, pr, tx, ty, t=self._event_time(event))
                self.drawing = False
                self._tablet_down = False
                self._flush_dirty()
                if self.show_debug:
                    self.update()
            event.accept()
            return

        event.ignore()

    # ------------------------------------------------------------------
    def mousePressEvent(self, event: QMouseEvent):
        btn = event.button()
        if btn == Qt.MouseButton.MiddleButton or (
            btn == Qt.MouseButton.LeftButton and self.space_held
        ):
            self.panning = True
            self.last_pan_pos = event.position()
            self._apply_cursor()
            self.update()
            return

        if btn != Qt.MouseButton.LeftButton:
            return

        # Prefer tablet path when stylus is active
        if self._tablet_down:
            return
        dx, dy, pr, tx, ty = self._pointer_doc(event)
        self.drawing = True
        self.engine.begin_stroke(dx, dy, pr, tx, ty, t=self._event_time(event))
        self._flush_dirty()

    def mouseMoveEvent(self, event: QMouseEvent):
        pos = event.position()

        if self.panning and self.last_pan_pos is not None:
            self.offset_x += pos.x() - self.last_pan_pos.x()
            self.offset_y += pos.y() - self.last_pan_pos.y()
            self.last_pan_pos = pos
            self.update()
            return

        self._set_hover(pos)
        self._report_pointer(event)

        if self.drawing and not self._tablet_down:
            dx, dy, pr, tx, ty = self._pointer_doc(event)
            self.engine.add_point(dx, dy, pr, tx, ty, t=self._event_time(event))
            self._flush_dirty()

    def mouseReleaseEvent(self, event: QMouseEvent):
        btn = event.button()
        if self.panning and btn in (Qt.MouseButton.MiddleButton, Qt.MouseButton.LeftButton):
            self.panning = False
            self.last_pan_pos = None
            self._apply_cursor()
            self.update()
            return

        if btn == Qt.MouseButton.LeftButton and self.drawing and not self._tablet_down:
            dx, dy, pr, tx, ty = self._pointer_doc(event)
            self.engine.end_stroke(dx, dy, pr, tx, ty, t=self._event_time(event))
            self.drawing = False
            self._flush_dirty()
            if self.show_debug:
                self.update()
