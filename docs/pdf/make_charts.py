"""Charts for the HPDP documents (docs/pdf). Run with the project venv, which has
docs/pdf/requirements.txt installed (the same interpreter as build_docs.py):

    .venv/Scripts/python docs/pdf/make_charts.py

Writes PNG files to docs/pdf/figures/. Numbers come from docs/project_report.md,
docs/real_data.md and docs/lnk10/ (the eye scans).
"""

import csv
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib.colors import LinearSegmentedColormap  # noqa: E402

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
OUT = HERE / "figures"
OUT.mkdir(exist_ok=True)

# Reference palette (dataviz skill, references/palette.md), light mode, unchanged
SERIES = ["#2a78d6", "#eb6834"]
SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK2 = "#52514e"
MUTED = "#898781"
GRID = "#e1e0d9"
AXIS = "#c3c2b7"
BLUE_RAMP = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"]

plt.rcParams.update({
    "font.family": "sans-serif",
    "font.sans-serif": ["Segoe UI", "DejaVu Sans"],     # DejaVu ships with matplotlib
    "font.size": 9,
    "axes.edgecolor": AXIS,
    "axes.labelcolor": INK2,
    "axes.titlesize": 10,
    "axes.titleweight": "bold",
    "axes.titlecolor": INK,
    "xtick.color": MUTED,
    "ytick.color": INK2,
    "axes.facecolor": SURFACE,
    "figure.facecolor": SURFACE,
    "savefig.facecolor": SURFACE,
    "axes.spines.top": False,
    "axes.spines.right": False,
})


def tidy(ax, grid_axis="x"):
    ax.grid(axis=grid_axis, color=GRID, linewidth=0.6)
    ax.set_axisbelow(True)
    ax.tick_params(length=0)


# ---------------------------------------------------------------------------
# Link budget: data rates against the link's packet capacity
# ---------------------------------------------------------------------------
MISSION_RATE = 76.8          # Msamples/s (reference mission)
LINK_CAPACITY = 857          # Mbit/s of packet capacity

# Real-data compression (docs/real_data.md): dataset, mean, min, max bits per sample.
# Both the link budget and the compression chart use this table.
REAL_DATA = [
    ("AVIRIS (49 scenes)", 4.43, 2.99, 7.93),
    ("Hyperion (13 scenes)", 4.95, 4.74, 5.16),
    ("Indian Pines (1 scene)", 6.01, 6.01, 6.01),
]


def link_budget():
    # Mbit/s = Msamples/s x bits per sample
    aviris_mean = REAL_DATA[0][1]
    worst = max(r[3] for r in REAL_DATA)
    rows = [
        ("Raw sensor data (12-bit)", MISSION_RATE * 12),
        ("Compressed, mission-size synthetic scene (2.10:1)", MISSION_RATE * 12 / 2.10),
        ("Compressed, real AVIRIS scenes, mean", MISSION_RATE * aviris_mean),
        ("Compressed, worst real scene", MISSION_RATE * worst),
    ]
    fig, ax = plt.subplots(figsize=(6.6, 2.4))
    y = np.arange(len(rows))[::-1]
    vals = [r[1] for r in rows]
    ax.barh(y, vals, height=0.55, color=SERIES[0], edgecolor=SURFACE, linewidth=2)
    for yi, v in zip(y, vals):
        ax.text(v + 12, yi, f"{v:.0f} Mbit/s", va="center", color=INK, fontsize=8.5)
    ax.axvline(LINK_CAPACITY, color=INK2, linewidth=1.2, linestyle=(0, (4, 3)))
    ax.text(LINK_CAPACITY - 10, len(rows) - 0.35, f"link capacity {LINK_CAPACITY} Mbit/s", ha="right",
            va="bottom", color=INK2, fontsize=8)
    ax.set_yticks(y, [r[0] for r in rows])
    ax.set_xlim(0, 1100)
    ax.set_ylim(-0.6, len(rows) - 0.1)
    ax.set_xlabel(f"Mbit/s at the mission rate ({MISSION_RATE} Msamples/s)")
    tidy(ax)
    fig.tight_layout()
    fig.savefig(OUT / "link_budget.png", dpi=220)
    plt.close(fig)


# ---------------------------------------------------------------------------
# Compression on real data: mean and range of bits per sample per dataset
# ---------------------------------------------------------------------------
def compression():
    data = REAL_DATA
    fig, ax = plt.subplots(figsize=(6.6, 2.0))
    y = np.arange(len(data))[::-1]
    for yi, (name, mean, lo, hi) in zip(y, data):
        if hi > lo:
            ax.plot([lo, hi], [yi, yi], color=SERIES[0], linewidth=2, solid_capstyle="round")
        ax.plot([mean], [yi], marker="o", markersize=8, color=SERIES[0],
                markeredgecolor=SURFACE, markeredgewidth=2, zorder=3)
        label = f"mean {mean:.2f}" + (f"  (range {lo:.2f} – {hi:.2f})" if hi > lo else "")
        ax.text(max(hi, mean) + 0.25, yi, label, va="center", color=INK, fontsize=8.5)
    ax.set_yticks(y, [d[0] for d in data])
    ax.set_xlim(0, 12)
    ax.set_ylim(-0.6, len(data) - 0.4)
    ax.set_xlabel("Bits per sample after lossless compression (input: 12 to 16 bits per sample)")
    tidy(ax)
    fig.tight_layout()
    fig.savefig(OUT / "compression.png", dpi=220)
    plt.close(fig)


# ---------------------------------------------------------------------------
# Eye scans (In-System IBERT, LNK-10)
# ---------------------------------------------------------------------------
def read_eye(path):
    """(horizontal codes, vertical codes, BER grid, horizontal %, vertical %) of an
    IBERT eye-scan CSV; the opening percentages come from the file's header"""
    rows, cols, in_scan, pct = [], None, False, {}
    with open(path, newline="") as fh:
        for rec in csv.reader(fh):
            if not rec:
                continue
            if rec[0] in ("Horizontal Percentage", "Vertical Percentage"):
                pct[rec[0]] = float(rec[1])
                continue
            if rec[0] == "Scan Start":
                in_scan = True
                continue
            if rec[0] == "Scan End":
                break
            if in_scan and rec[0] == "2d statistical":
                cols = [int(v) for v in rec[1:] if v.strip()]
                continue
            if in_scan and cols:
                vals = [float(v) for v in rec[1:len(cols) + 1]]
                rows.append((int(rec[0]), vals))
    missing = {"Horizontal Percentage", "Vertical Percentage"} - set(pct)
    if missing or not rows:
        raise ValueError(f"{path}: not an IBERT eye-scan export as expected "
                         f"({'no ' + ', '.join(sorted(missing)) if missing else 'no scan rows'})")
    rows.sort(key=lambda r: r[0])
    vert = np.array([r[0] for r in rows])
    ber = np.array([r[1] for r in rows])
    return np.array(cols), vert, ber, pct["Horizontal Percentage"], pct["Vertical Percentage"]


def eye_scans():
    files = [
        ("SFP0 → SFP1", REPO / "docs/lnk10/hpdp_eye_sfp0_to_sfp1_20261013_1450.csv"),
        ("SFP1 → SFP0", REPO / "docs/lnk10/hpdp_eye_sfp1_to_sfp0_20261013_1450.csv"),
    ]
    cmap = LinearSegmentedColormap.from_list("blue", ["#f4f8fd"] + BLUE_RAMP)
    fig, axes = plt.subplots(1, 2, figsize=(6.6, 2.7), sharey=True)
    for ax, (title, path) in zip(axes, files):
        hor, vert, ber, h, v = read_eye(path)
        logber = np.log10(np.clip(ber, 1e-7, 1))
        ui = hor / (2 * hor.max())
        im = ax.pcolormesh(ui, vert, logber, cmap=cmap, vmin=-7, vmax=0, shading="nearest")
        ax.set_title(f"{title}\neye opening H {h:.1f} %, V {v:.1f} %", fontsize=8.5)
        ax.set_xlabel("Horizontal offset (UI)")
        ax.tick_params(length=0)
        for s in ax.spines.values():
            s.set_visible(False)
    axes[0].set_ylabel("Vertical offset (code)")
    cb = fig.colorbar(im, ax=axes, shrink=0.85, pad=0.02)
    cb.set_label("log10(BER)", color=INK2)
    cb.outline.set_visible(False)
    cb.ax.tick_params(length=0, colors=MUTED)
    fig.savefig(OUT / "eye_scans.png", dpi=220, bbox_inches="tight")
    plt.close(fig)


# ---------------------------------------------------------------------------
# FPGA utilization of the two final builds
# ---------------------------------------------------------------------------
# XCZU7EV resources, and what each final build uses (the builds' utilization reports,
# docs/project_report.md section 3.5 and docs/nx_max_1024.md)
DEVICE = {"LUT": 230400, "Flip-flops": 460800, "Block RAM": 312, "UltraRAM": 96, "DSP": 1728}
USED = {
    "m7h (NX_MAX 512, board-tested)": [38151, 39231, 38.5, 56, 24],
    "m7i_1024b (NX_MAX 1024, board-tested)": [38906, 40120, 46.5, 80, 24],
}


def utilization():
    res = list(DEVICE)
    shares = {build: [100 * u / DEVICE[r] for u, r in zip(used, res)] for build, used in USED.items()}
    fig, ax = plt.subplots(figsize=(6.6, 2.6))
    y = np.arange(len(res))[::-1]
    h = 0.36
    for k, (build, vals) in enumerate(shares.items()):
        yk = y + h / 2 - k * h
        ax.barh(yk, vals, height=h, color=SERIES[k], edgecolor=SURFACE, linewidth=1.5, label=build)
        for yi, v in zip(yk, vals):
            ax.text(v + 1, yi, f"{v:.1f} %", va="center", fontsize=8, color=INK)
    ax.set_yticks(y, res)
    ax.set_xlim(0, 100)
    ax.set_xlabel("Share of the XCZU7EV resource used")
    ax.legend(loc="lower right", frameon=False, fontsize=8)
    tidy(ax)
    fig.tight_layout()
    fig.savefig(OUT / "utilization.png", dpi=220)
    plt.close(fig)


if __name__ == "__main__":
    link_budget()
    compression()
    eye_scans()
    utilization()
    print("charts in", OUT)
