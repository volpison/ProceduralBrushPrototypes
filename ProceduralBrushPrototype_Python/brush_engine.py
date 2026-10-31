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

"""Qt-free procedural brush engine with true sub-pixel dab placement.

Conventions
-----------
* Document space is continuous. Pixel (i, j) covers [i, i+1) x [j, j+1) and its
  CENTRE is at (i + 0.5, j + 0.5). A dab at document position (cx, cy) is
  therefore centred exactly under a cursor reported at (cx, cy).
* Dabs are evaluated analytically at their fractional centre (no pre-rendered
  tip, no rounding), so every sub-pixel offset gives a different, correct
  anti-aliased footprint.

Stroke model (Photoshop-like Flow vs Opacity)
---------------------------------------------
* Each dab adds ``dab_alpha * flow`` to a per-stroke float coverage buffer
  using "over" accumulation:  cov = cov + a * (1 - cov).
* The layer pixels are always recomputed from a snapshot taken at stroke
  start:  result = over(base, colour, cov * opacity).
  This avoids the 8-bit rounding drift of compositing each dab into a uint8
  buffer, and makes Opacity a true ceiling for the whole stroke.
"""
from __future__ import annotations

import math
import time

import numpy as np

Rect = tuple[int, int, int, int]  # x0, y0, x1, y1 (document pixels, x1/y1 exclusive)

STRING_MAX_PX = 80.0     # rope length at 100 % strength (document px)
LOWPASS_MAX_S = 0.15     # time constant at 100 % strength (seconds)
NPZ_SCHEMA_VERSION = 2


def centripetal_catmull_rom(p0, p1, p2, p3, n, alpha=0.5):
    """Sample the p1->p2 span of a centripetal Catmull-Rom spline.

    Returns (points[n, 2], u[n]) with u running 0..1 along the span.
    Centripetal parameterisation (alpha=0.5) avoids the overshoot, loops and
    cusps that the uniform version produces when mouse samples are unevenly
    spaced (fast flick followed by a slow move).
    """

    def knot(t, a, b):
        return t + max(math.hypot(b[0] - a[0], b[1] - a[1]), 1e-4) ** alpha

    t0 = 0.0
    t1 = knot(t0, p0, p1)
    t2 = knot(t1, p1, p2)
    t3 = knot(t2, p2, p3)

    t = np.linspace(t1, t2, n)[:, None]
    a1 = ((t1 - t) * p0 + (t - t0) * p1) / (t1 - t0)
    a2 = ((t2 - t) * p1 + (t - t1) * p2) / (t2 - t1)
    a3 = ((t3 - t) * p2 + (t - t2) * p3) / (t3 - t2)
    b1 = ((t2 - t) * a1 + (t - t0) * a2) / (t2 - t0)
    b2 = ((t3 - t) * a2 + (t - t1) * a3) / (t3 - t1)
    c = ((t2 - t) * b1 + (t - t1) * b2) / (t2 - t1)
    return c, (t[:, 0] - t1) / (t2 - t1)


class BrushEngine:
    def __init__(self, width: int, height: int):
        self.width = width
        self.height = height
        self.buffer = np.full((height, width, 4), 255, dtype=np.uint8)  # RGBA

        # Brush parameters
        self.size = 42.0          # diameter in document pixels
        self.hardness = 0.72      # 0..1
        self.spacing = 0.13       # fraction of diameter
        self.flow = 1.0           # 0..1, per-dab
        self.opacity = 1.0        # 0..1, per-stroke ceiling
        self.color = (25, 25, 25)
        self.min_step = 0.5       # never space dabs closer than this (pixels)
        # Pencil-style pixel snapping for tiny hard brushes (size <= 1.5 px and
        # hardness >= 95%). OFF by default: snapping moves every dab to a pixel
        # centre, which removes all fractional coverage and turns the stroke into
        # a 1-bit, aliased line. Turn it on only to study that behaviour.
        self.snap_tiny_hard = False
        # How overlapping dabs combine inside one stroke:
        #   "buildup" = classic over  (cov + a*(1-cov))  — denser overlaps
        #   "max"     = cov = max(cov, a)                — no extra darkening
        self.accum_mode = "buildup"

        # Pressure dynamics, independent like Photoshop's Shape Dynamics (size)
        # and Transfer (opacity). With both off, pressure is recorded only.
        self.pressure_size = True       # pressure scales the dab diameter
        self.pressure_opacity = True    # pressure scales the dab alpha
        self.pressure_min_size = 0.18   # diameter fraction at pressure 0

        # Stroke smoothing. `stabilizer` is the strength, 0 (off) .. 1 (max).
        #   "string"  : the tip hangs on a rope of length  strength * STRING_MAX_PX
        #               (document px). The tip only moves when the pointer pulls the
        #               rope taut, so lag is bounded by the rope and does NOT depend
        #               on how many input events per second the device produces.
        #   "lowpass" : exponential low-pass with time constant
        #               strength * LOWPASS_MAX_S, driven by event timestamps
        #               (follow = 1 - exp(-dt / tau)), so it is also rate independent.
        # `stabilizer_catch_up`: on release the tip glides to the pointer so the
        # stroke ends where the pen was lifted (like Photoshop's "Catch-up on Stroke
        # End"). Off = the stroke stops wherever the tip happens to be.
        self.stabilizer = 0.0
        self.stabilizer_mode = "string"
        self.stabilizer_catch_up = True

        # Stroke state
        # Each input point: (x, y, pressure, tilt_x_deg, tilt_y_deg, t_s)
        self._active = False
        self._pts: list[tuple[float, float, float, float, float, float]] = []
        self._next_seg = 0
        self._carry = 0.0         # distance still to travel until the next dab
        self._base: np.ndarray | None = None
        self._cov: np.ndarray | None = None
        self._dirty: Rect | None = None

        # Debug / study: last completed stroke geometry
        # input (raw): (x, y, pressure, tilt_x, tilt_y, t_s)
        self.last_input_pts: list[tuple[float, float, float, float, float, float]] = []
        # stabilized path fed to spacing/dabs: (x, y, pressure, tilt_x, tilt_y, t_s)
        self.last_stabilized_pts: list[tuple[float, float, float, float, float, float]] = []
        # dabs: (x, y, pressure, diameter, dab_alpha, peak_alpha, t_s)
        self.last_dab_centres: list[tuple] = []
        self._dab_centres: list[tuple] = []
        self._raw_pts: list[tuple[float, float, float, float, float, float]] = []
        self._t0 = 0.0            # stroke start (seconds, same clock as event times)
        self._last_t = 0.0        # seconds since stroke start of the newest input
        self._stab_x = 0.0
        self._stab_y = 0.0
        self._stab_t = 0.0        # time of the previous stabilizer update (s)

    # ------------------------------------------------------------------
    @property
    def step(self) -> float:
        return max(self.size * self.spacing, self.min_step)

    def clear(self):
        self.buffer[:] = 255
        self._active = False
        self._base = None
        self._cov = None
        self._pts = []
        self._raw_pts = []
        self._dab_centres = []
        self.last_input_pts = []
        self.last_stabilized_pts = []
        self.last_dab_centres = []
        self._mark(0, 0, self.width, self.height)

    def _stabilize(self, x: float, y: float, ts: float) -> tuple[float, float]:
        """Move the drawing tip toward the pointer (x, y) at time `ts` (seconds
        since stroke start) and return the tip. Strength 0 = identity."""
        s = min(max(self.stabilizer, 0.0), 1.0)
        if s <= 1e-6:
            self._stab_x, self._stab_y = x, y
        elif self.stabilizer_mode == "lowpass":
            dt = max(ts - self._stab_t, 0.0)
            follow = 1.0 - math.exp(-dt / (s * LOWPASS_MAX_S))
            self._stab_x += (x - self._stab_x) * follow
            self._stab_y += (y - self._stab_y) * follow
        else:  # "string"
            rope = s * STRING_MAX_PX
            dx, dy = x - self._stab_x, y - self._stab_y
            d = math.hypot(dx, dy)
            if d > rope:
                k = (d - rope) / d          # pull the tip until the rope is taut
                self._stab_x += dx * k
                self._stab_y += dy * k
        self._stab_t = ts
        return self._stab_x, self._stab_y

    def take_dirty(self) -> Rect | None:
        d, self._dirty = self._dirty, None
        return d

    def _mark(self, x0, y0, x1, y1):
        d = self._dirty
        self._dirty = (x0, y0, x1, y1) if d is None else (
            min(d[0], x0), min(d[1], y0), max(d[2], x1), max(d[3], y1))

    # ------------------------------------------------------------------
    # Stroke API (all coordinates are continuous document coordinates)
    # ------------------------------------------------------------------
    def begin_stroke(self, x: float, y: float, pressure: float = 1.0,
                     tilt_x: float = 0.0, tilt_y: float = 0.0,
                     t: float | None = None):
        """`t` is the event time in seconds (any clock, only differences matter).
        If omitted, time.perf_counter() is used."""
        self._t0 = time.perf_counter() if t is None else t
        self._last_t = 0.0
        self._base = self.buffer.copy()
        self._cov = np.zeros((self.height, self.width), dtype=np.float32)
        self._stab_x, self._stab_y = x, y
        self._stab_t = 0.0
        raw = (x, y, pressure, tilt_x, tilt_y, 0.0)
        self._raw_pts = [raw]
        self._pts = [raw]   # first point is never lagged
        self._next_seg = 0
        self._active = True
        self._dab_centres = []
        self._stamp(x, y, pressure, 0.0)
        self._carry = self.step

    def add_point(self, x: float, y: float, pressure: float = 1.0,
                  tilt_x: float = 0.0, tilt_y: float = 0.0,
                  t: float | None = None):
        if not self._active:
            return
        # Thin on raw motion so we keep the real pointer trail for debug
        if self._raw_pts:
            lx, ly = self._raw_pts[-1][0], self._raw_pts[-1][1]
            if math.hypot(x - lx, y - ly) < 1e-3:
                return
        now = time.perf_counter() if t is None else t
        ts = max(now - self._t0, self._last_t)
        self._last_t = ts
        self._raw_pts.append((x, y, pressure, tilt_x, tilt_y, ts))
        sx, sy = self._stabilize(x, y, ts)
        if self.stabilizer > 1e-6:
            px, py = self._pts[-1][0], self._pts[-1][1]
            if math.hypot(sx - px, sy - py) < 1e-9:
                return          # rope still slack: the tip did not move
        self._pts.append((sx, sy, pressure, tilt_x, tilt_y, ts))
        while len(self._pts) >= self._next_seg + 3:
            self._render_segment(self._next_seg)
            self._next_seg += 1

    def end_stroke(self, x: float | None = None, y: float | None = None,
                   pressure: float = 1.0, tilt_x: float = 0.0, tilt_y: float = 0.0,
                   t: float | None = None):
        if not self._active:
            return
        if x is not None and y is not None:
            self.add_point(x, y, pressure, tilt_x, tilt_y, t)
        if self.stabilizer > 1e-6 and self.stabilizer_catch_up:
            self._catch_up()
        while self._next_seg + 1 < len(self._pts):
            self._render_segment(self._next_seg)
            self._next_seg += 1
        self._active = False
        self._base = None
        self._cov = None
        self.last_input_pts = list(self._raw_pts)
        self.last_stabilized_pts = list(self._pts)
        self.last_dab_centres = list(self._dab_centres)
        self._pts = []
        self._raw_pts = []
        self._dab_centres = []

    def _catch_up(self):
        """Glide the tip in a straight line to the last raw pointer position."""
        tx, ty = self._pts[-1][0], self._pts[-1][1]
        gx, gy, pr, tix, tiy, ts = self._raw_pts[-1]
        dist = math.hypot(gx - tx, gy - ty)
        if dist < 1e-3:
            return
        n = max(1, math.ceil(dist / 8.0))       # a point every <= 8 px keeps the
        for k in range(1, n + 1):               # spline close to the straight line
            f = k / n
            self._pts.append((tx + (gx - tx) * f, ty + (gy - ty) * f, pr, tix, tiy, ts))

    # ------------------------------------------------------------------
    # Spline -> evenly spaced dabs
    # ------------------------------------------------------------------
    def _render_segment(self, i: int):
        pts = self._pts
        p1 = np.array(pts[i][:2], dtype=np.float64)
        p2 = np.array(pts[i + 1][:2], dtype=np.float64)
        # Mirror the missing neighbour at stroke start/end: clean end tangents.
        p0 = np.array(pts[i - 1][:2], dtype=np.float64) if i > 0 else 2 * p1 - p2
        p3 = (np.array(pts[i + 2][:2], dtype=np.float64)
              if i + 2 < len(pts) else 2 * p2 - p1)
        pr1, pr2 = pts[i][2], pts[i + 1][2]  # pressure
        t1, t2 = pts[i][5], pts[i + 1][5]    # seconds since stroke start

        chord = float(np.hypot(*(p2 - p1)))
        if chord < 1e-9:
            return

        step = self.step
        n = int(min(max(math.ceil(chord / (step * 0.25)), 8), 4096)) + 1
        curve, u = centripetal_catmull_rom(p0, p1, p2, p3, n)

        # Arc length along the sampled curve, then place dabs at exact
        # multiples of `step` by interpolating *between* samples. The leftover
        # distance (`_carry`) rolls over into the next span, so spacing stays
        # uniform across span boundaries.
        seg = np.hypot(np.diff(curve[:, 0]), np.diff(curve[:, 1]))
        cum = np.concatenate(([0.0], np.cumsum(seg)))
        total = float(cum[-1])
        if total < 1e-9:
            return
        if self._carry > total:
            self._carry -= total
            return

        s = np.arange(self._carry, total + 1e-9, step)
        if s.size == 0:
            self._carry -= total
            return
        xs = np.interp(s, cum, curve[:, 0])
        ys = np.interp(s, cum, curve[:, 1])
        us = np.interp(s, cum, u)
        ps = pr1 + (pr2 - pr1) * us
        ts = t1 + (t2 - t1) * us        # a dab inherits the time the pen passed it
        for x, y, p, t in zip(xs, ys, ps, ts):
            self._stamp(float(x), float(y), float(p), float(t))
        self._carry = float(s[-1]) + step - total

    # ------------------------------------------------------------------
    # One dab, evaluated analytically at a fractional centre
    # ------------------------------------------------------------------
    def _stamp(self, cx: float, cy: float, pressure: float, t: float = 0.0):
        if self._cov is None or self._base is None:
            return
        pressure = min(max(pressure, 0.0), 1.0)
        if self.pressure_size:
            m = self.pressure_min_size
            diameter = self.size * (m + (1.0 - m) * pressure)
        else:
            diameter = self.size

        # Tiny hard pencil path (only when user enables Snap 1px).
        tiny_hard = (
            self.snap_tiny_hard and self.size <= 1.5 and self.hardness >= 0.95
        )
        if tiny_hard:
            cx = math.floor(cx) + 0.5   # snap to the pixel centre
            cy = math.floor(cy) + 0.5

        dab_alpha = self.flow * (pressure if self.pressure_opacity else 1.0)
        # Record for debug overlay / CSV (after snap so we see the real centre used).
        # peak_alpha (index 5) is filled in below once the profile is evaluated.
        rec = [cx, cy, pressure, diameter, dab_alpha, 0.0, t]
        self._dab_centres.append(rec)

        r = diameter * 0.5
        if r < 0.05:
            return

        reach = r + 1.5  # radius + anti-alias margin
        x0 = max(0, math.floor(cx - reach))
        x1 = min(self.width, math.ceil(cx + reach))
        y0 = max(0, math.floor(cy - reach))
        y1 = min(self.height, math.ceil(cy + reach))
        if x0 >= x1 or y0 >= y1:
            return

        # Distance from each PIXEL CENTRE (i + 0.5) to the fractional dab centre.
        dx = (np.arange(x0, x1) + 0.5 - cx).astype(np.float32)
        dy = (np.arange(y0, y1) + 0.5 - cy).astype(np.float32)[:, None]
        dist = np.sqrt(dx * dx + dy * dy)

        # Radial profile. The transition band is never narrower than 1 px, so
        # hard brushes (and nearly-hard ones) are always anti-aliased and move
        # smoothly instead of popping between pixels.
        h = min(max(self.hardness, 0.0), 1.0)
        inner, outer = r * h, r
        if outer - inner <= 1.0:
            mid = 0.5 * (inner + outer)
            outer = mid + 0.5
            alpha = np.clip(outer - dist, 0.0, 1.0)          # exact 1-px linear ramp
        else:
            t = np.clip((dist - inner) / (outer - inner), 0.0, 1.0)
            alpha = 1.0 - t * t * (3.0 - 2.0 * t)            # smoothstep falloff
        alpha *= dab_alpha
        rec[5] = float(alpha.max())

        # Accumulate stroke coverage, then recomposite this ROI from the snapshot.
        # When Snap 1px is active, force Max so repeated hits on the same pixel
        # still respect Flow instead of racing to full opacity under Build-up.
        cov = self._cov[y0:y1, x0:x1]
        use_max = (self.accum_mode == "max") or tiny_hard
        if use_max:
            np.maximum(cov, alpha, out=cov)
        else:
            # classic build-up (Photoshop-style "over")
            cov += alpha * (1.0 - cov)
        e = (cov * self.opacity)[..., None]

        base = self._base[y0:y1, x0:x1].astype(np.float32)
        ba = base[..., 3:4] * (1.0 / 255.0)
        out_a = e + ba * (1.0 - e)
        col = np.asarray(self.color, dtype=np.float32)
        rgb = (col * e + base[..., :3] * ba * (1.0 - e)) / np.maximum(out_a, 1e-6)

        out = self.buffer[y0:y1, x0:x1]
        out[..., :3] = np.clip(rgb + 0.5, 0, 255).astype(np.uint8)
        out[..., 3] = np.clip(out_a[..., 0] * 255.0 + 0.5, 0, 255).astype(np.uint8)
        self._mark(x0, y0, x1, y1)

    def export_stroke_csv(self, path: str) -> bool:
        """Write last stroke input samples and dab records to CSV.

        Columns: kind (input|dab), index, t_ms (since stroke start), x, y,
        pressure, tilt_x_deg / tilt_y_deg (inputs only), diameter, dab_alpha,
        peak_alpha (dabs only), then the brush settings repeated on every row.
        """
        import csv
        if not self.last_input_pts and not self.last_dab_centres:
            return False
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow([
                "kind", "index", "t_ms", "x", "y", "pressure",
                "tilt_x_deg", "tilt_y_deg", "diameter", "dab_alpha", "peak_alpha",
                "brush_size", "hardness", "spacing", "flow", "opacity", "accum_mode",
                "pressure_size", "pressure_opacity", "snap_tiny_hard", "stabilizer",
                "stabilizer_mode", "stabilizer_catch_up",
            ])
            meta = [self.size, self.hardness, self.spacing, self.flow,
                    self.opacity, self.accum_mode,
                    int(self.pressure_size), int(self.pressure_opacity),
                    int(self.snap_tiny_hard), self.stabilizer,
                    self.stabilizer_mode, int(self.stabilizer_catch_up)]
            for i, (x, y, pr, tx, ty, ts) in enumerate(self.last_input_pts):
                w.writerow(["input", i, f"{ts * 1000.0:.3f}", x, y, pr, tx, ty,
                            "", "", "", *meta])
            for i, (x, y, pr, tx, ty, ts) in enumerate(self.last_stabilized_pts):
                w.writerow(["stabilized", i, f"{ts * 1000.0:.3f}", x, y, pr, tx, ty,
                            "", "", "", *meta])
            for i, (x, y, pr, diam, da, pa, ts) in enumerate(self.last_dab_centres):
                w.writerow(["dab", i, f"{ts * 1000.0:.3f}", x, y, pr, "", "",
                            f"{diam:.4f}", f"{da:.5f}", f"{pa:.5f}", *meta])
        return True

    def export_stroke_npz(self, path: str) -> bool:
        """Write last stroke as a compressed NPZ that loads WITHOUT pickle.

        Arrays (float64, so a replay reproduces the dabs exactly):
          inputs      (N, 6)  - x, y, pressure, tilt_x, tilt_y, t_s   (raw pointer)
          stabilized  (M, 6)  - same layout (what spacing/dabs followed)
          dabs        (K, 7)  - x, y, pressure, diameter, dab_alpha, peak_alpha, t_s
          schema      ()      - int, NPZ_SCHEMA_VERSION
          settings    ()      - JSON text (plain unicode string, not an object array)

        Read it back with BrushEngine.load_stroke_npz(path).
        """
        if not self.last_input_pts and not self.last_dab_centres:
            return False

        def to_f64(rows, ncols):
            if not rows:
                return np.zeros((0, ncols), dtype=np.float64)
            return np.asarray(rows, dtype=np.float64)

        settings = {
            "brush_size": float(self.size),
            "hardness": float(self.hardness),
            "spacing": float(self.spacing),
            "flow": float(self.flow),
            "opacity": float(self.opacity),
            "accum_mode": str(self.accum_mode),
            "pressure_size": bool(self.pressure_size),
            "pressure_opacity": bool(self.pressure_opacity),
            "pressure_min_size": float(self.pressure_min_size),
            "snap_tiny_hard": bool(self.snap_tiny_hard),
            "stabilizer": float(self.stabilizer),
            "stabilizer_mode": str(self.stabilizer_mode),
            "stabilizer_catch_up": bool(self.stabilizer_catch_up),
            "min_step": float(self.min_step),
            "color": [int(c) for c in self.color],
        }
        import json
        np.savez_compressed(
            path,
            inputs=to_f64(self.last_input_pts, 6),
            stabilized=to_f64(self.last_stabilized_pts, 6),
            dabs=to_f64(self.last_dab_centres, 7),
            schema=np.int64(NPZ_SCHEMA_VERSION),
            settings=np.array(json.dumps(settings)),     # 0-d unicode, no pickle
        )
        return True

    @staticmethod
    def load_stroke_npz(path: str) -> dict:
        """Load a stroke written by export_stroke_npz (pickle is never enabled).

        Returns {"inputs", "stabilized", "dabs": ndarrays, "settings": dict,
        "schema": int}.
        """
        import json
        with np.load(path, allow_pickle=False) as z:
            if "schema" not in z.files:
                raise ValueError(
                    "Old-format stroke file (no schema field; its settings were "
                    "stored as a pickled object array). Re-export it with this version.")
            return {
                "schema": int(z["schema"]),
                "inputs": z["inputs"],
                "stabilized": z["stabilized"],
                "dabs": z["dabs"],
                "settings": json.loads(str(z["settings"])),
            }
