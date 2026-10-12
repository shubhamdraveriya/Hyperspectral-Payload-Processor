"""Ground station display (SPEC.md 9.2, GS-05): false-colour RGB from three
selectable bands, a single-band view, the spectrum of a selected pixel, and the
per-scene and link statistics. Needs numpy and matplotlib.

Viewer is interactive (scene and band sliders, R G B box, click a pixel for its
spectrum, left/right keys for the scene, f to follow new scenes) and can be fed
while it is open (live decoding). render_png() draws the same panels to a file.
"""

import queue

import numpy as np

from gs_report import link_lines, scene_line

CMAP = "gray"


def cube(scene):
    """(ny, nz, nx) array of a BIL scene: [line, band, pixel]."""
    return np.frombuffer(scene.samples.tobytes(), dtype="<u2").reshape(scene.ny, scene.nz, scene.nx)


def default_bands(nz):
    """Three bands spread over the spectrum: R, G, B = 3/4, 1/2, 1/4 of the range."""
    return (min(nz - 1, 3 * nz // 4), nz // 2, nz // 4)


def stretch(img):
    lo, hi = np.percentile(img, (2, 98))
    return np.clip((img.astype(float) - lo) / max(hi - lo, 1.0), 0.0, 1.0)


def rgb(c, bands):
    return np.dstack([stretch(c[:, b, :]) for b in bands])


def scene_text(r, v, ref_residual=None):
    lines = [scene_line(r, v)]
    if r.scene is not None:
        lines.append(f"timestamp {r.timestamp} lines, {r.packets} packets, {r.frames} frames, "
                     f"{r.rs_uncorrectable} uncorrectable / {r.lost_frames} lost frames, "
                     f"{r.asm_errors} ASM bit errors, {r.flywheel} FLYWHEEL records")
        if v is not None and v.residual is not None:
            lines.append(f"residual errors against the reference: {v.residual} samples")
    return lines


def _draw(axes, r, v, ref, bands, band, pixel, stats):
    ax_rgb, ax_band, ax_spec, ax_text = axes
    for ax in axes:
        ax.clear()
    ax_text.axis("off")
    text = scene_text(r, v) + ([" ", "link, so far:"] + link_lines(stats) if stats else [])
    ax_text.text(0.0, 1.0, "\n".join(text), va="top", ha="left", family="monospace", fontsize=8,
                 transform=ax_text.transAxes)
    if r.scene is None:
        for ax in (ax_rgb, ax_band, ax_spec):
            ax.axis("off")
        ax_rgb.text(0.5, 0.5, f"scene {r.scene_id}\nnot reconstructed\n{r.reason}", ha="center", va="center",
                    transform=ax_rgb.transAxes)
        return
    c = cube(r.scene)
    ny, nz, nx = c.shape
    bands = tuple(min(max(b, 0), nz - 1) for b in bands)
    band = min(max(band, 0), nz - 1)
    x, y = pixel if pixel else (nx // 2, ny // 2)
    x, y = min(max(x, 0), nx - 1), min(max(y, 0), ny - 1)

    ax_rgb.imshow(rgb(c, bands), interpolation="nearest")
    ax_rgb.set_title(f"scene {r.scene_id}: R G B = bands {bands[0]} {bands[1]} {bands[2]}", fontsize=9)
    ax_band.imshow(c[:, band, :], cmap=CMAP, interpolation="nearest")
    ax_band.set_title(f"band {band}", fontsize=9)
    for ax in (ax_rgb, ax_band):
        ax.plot([x], [y], marker="+", color="red", markersize=12, mew=1.5)
        ax.set_xlabel("pixel (across track)", fontsize=8)
        ax.set_ylabel("line (along track)", fontsize=8)
        ax.tick_params(labelsize=7)

    ax_spec.plot(np.arange(nz), c[y, :, x], label="received", lw=1.2)
    if ref is not None and (ref.nx, ref.ny, ref.nz) == (nx, ny, nz):
        ax_spec.plot(np.arange(nz), cube(ref)[y, :, x], "--", label="reference", lw=1.0)
        ax_spec.legend(fontsize=7)
    ax_spec.axvline(band, color="gray", lw=0.6)
    ax_spec.set_title(f"spectrum of pixel {x}, line {y}", fontsize=9)
    ax_spec.set_xlabel("band", fontsize=8)
    ax_spec.set_ylabel(f"value (D = {r.scene.depth})", fontsize=8)
    ax_spec.tick_params(labelsize=7)


def _figure(plt):
    fig = plt.figure(figsize=(14, 7.5))
    gs = fig.add_gridspec(2, 3, height_ratios=(3, 1.25), left=0.05, right=0.98, top=0.93, bottom=0.14,
                          wspace=0.25, hspace=0.3)
    axes = (fig.add_subplot(gs[0, 0]), fig.add_subplot(gs[0, 1]), fig.add_subplot(gs[0, 2]),
            fig.add_subplot(gs[1, :]))
    return fig, axes


def render_png(path, r, v, ref, stats, bands=None, band=None, pixel=None):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, axes = _figure(plt)
    nz = r.scene.nz if r.scene else 1
    _draw(axes, r, v, ref, bands or default_bands(nz), nz // 2 if band is None else band, pixel, stats)
    fig.suptitle("HPDP ground station", fontsize=11)
    fig.savefig(path, dpi=90)
    plt.close(fig)


class Viewer:
    """Interactive display; add() may be called from another thread through feed()."""

    def __init__(self, refs, bands=None, title="HPDP ground station"):
        import matplotlib.pyplot as plt
        from matplotlib.widgets import Slider, TextBox
        self.plt = plt
        self.refs = refs
        self.results, self.verdicts, self.stats = [], {}, None
        self.bands = bands
        self.band = None
        self.pixel = None
        self.idx = 0
        self.follow = True
        self.queue = queue.Queue()
        self.fig, self.axes = _figure(plt)
        self.fig.suptitle(title, fontsize=11)
        self.s_scene = Slider(self.fig.add_axes((0.08, 0.06, 0.35, 0.025)), "scene", 0, 1, valinit=0, valstep=1)
        self.s_band = Slider(self.fig.add_axes((0.55, 0.06, 0.25, 0.025)), "band", 0, 1, valinit=0, valstep=1)
        self.t_rgb = TextBox(self.fig.add_axes((0.88, 0.055, 0.08, 0.035)), "R G B ", initial="")
        self.s_scene.on_changed(self._on_scene)
        self.s_band.on_changed(self._on_band)
        self.t_rgb.on_submit(self._on_rgb)
        self.fig.canvas.mpl_connect("button_press_event", self._on_click)
        self.fig.canvas.mpl_connect("key_press_event", self._on_key)
        self.timer = self.fig.canvas.new_timer(interval=400)
        self.timer.add_callback(self._poll)
        self.timer.start()
        self._updating = False

    # thread-safe entry for the decoding thread
    def feed(self, result=None, verdict=None, stats=None):
        self.queue.put((result, verdict, stats))

    def show(self):
        self._redraw()
        self.plt.show()

    # ------------------------------------------------------------------
    def _poll(self):
        changed = False
        while True:
            try:
                r, v, st = self.queue.get_nowait()
            except queue.Empty:
                break
            if st is not None:
                self.stats = st
                changed = True
            if r is not None:
                self.results.append(r)
                if v is not None:
                    self.verdicts[r.scene_id] = v
                if self.follow:
                    self.idx = len(self.results) - 1
                changed = True
        if changed:
            self._redraw()

    def _current(self):
        return self.results[self.idx] if self.results else None

    def _redraw(self):
        r = self._current()
        self._updating = True
        n = max(len(self.results) - 1, 1)
        self.s_scene.valmax = n
        self.s_scene.ax.set_xlim(0, n)
        self.s_scene.set_val(self.idx)
        if r is not None and r.scene is not None:
            nz = r.scene.nz
            if self.bands is None:
                self.bands = default_bands(nz)
                self.t_rgb.set_val(" ".join(str(b) for b in self.bands))
            if self.band is None:
                self.band = nz // 2
            self.s_band.valmax = nz - 1
            self.s_band.ax.set_xlim(0, nz - 1)
            self.s_band.set_val(self.band)
        self._updating = False
        if r is None:
            for ax in self.axes:
                ax.clear()
                ax.axis("off")
            self.axes[3].text(0, 1, "waiting for scenes ...", va="top", transform=self.axes[3].transAxes)
        else:
            _draw(self.axes, r, self.verdicts.get(r.scene_id), self.refs.get(r.scene_id),
                  self.bands or (0, 0, 0), self.band or 0, self.pixel, self.stats)
        self.fig.canvas.draw_idle()

    def _on_scene(self, val):
        if self._updating or not self.results:
            return
        self.idx = int(val)
        self.follow = self.idx == len(self.results) - 1
        self._redraw()

    def _on_band(self, val):
        if self._updating:
            return
        self.band = int(val)
        self._redraw()

    def _on_rgb(self, text):
        try:
            b = tuple(int(t) for t in text.replace(",", " ").split())
        except ValueError:
            return
        if len(b) == 3:
            self.bands = b
            self._redraw()

    def _on_click(self, event):
        if event.inaxes in self.axes[:2] and event.xdata is not None:
            self.pixel = (int(round(event.xdata)), int(round(event.ydata)))
            self._redraw()

    def _on_key(self, event):
        if not self.results:
            return
        if event.key == "right":
            self.idx = min(self.idx + 1, len(self.results) - 1)
        elif event.key == "left":
            self.idx = max(self.idx - 1, 0)
        elif event.key == "f":
            self.idx = len(self.results) - 1
        else:
            return
        self.follow = self.idx == len(self.results) - 1
        self._redraw()
