"""Radiometric correction (SPEC.md 6.2) and synthetic calibration data.

    c = clamp(((x - dark) * gain + 2^(F-1)) >> F, 0, 2^D - 1),  F = gain fraction bits (14)

then bad pixels are replaced from their nearest good neighbours in the same
band and line (pre-replacement values; positions outside the line count as bad).
"""

import random
from array import array

from .formats import ORDER_BIL, Calibration, Scene, index


def correct_line(raw, dark, gain, bad, depth, frac_bits=14):
    """Correct one band of one line. All arguments are sequences of length Nx."""
    top = (1 << depth) - 1
    half = 1 << (frac_bits - 1)
    c = [max(0, min(((x - d) * g + half) >> frac_bits, top)) for x, d, g in zip(raw, dark, gain)]
    nx = len(c)
    good = [not b for b in bad]
    out = []
    replaced = 0
    for x in range(nx):
        if good[x]:
            out.append(c[x])
            continue
        replaced += 1
        left = x > 0 and good[x - 1]
        right = x < nx - 1 and good[x + 1]
        if left and right:
            out.append((c[x - 1] + c[x + 1] + 1) >> 1)
        elif left:
            out.append(c[x - 1])
        elif right:
            out.append(c[x + 1])
        else:
            out.append(0)
    return out, replaced


def correct(scene, cal):
    """Apply the correction to a whole scene. Returns (corrected Scene, stats)."""
    if (cal.nx, cal.nz) != (scene.nx, scene.nz):
        raise ValueError("calibration table does not match the scene size")
    nx, ny, nz = scene.nx, scene.ny, scene.nz
    out = array("H", bytes(2 * len(scene.samples)))
    stats = {"replaced": 0, "clamped": 0}
    top = (1 << scene.depth) - 1
    half = 1 << (cal.gain_frac_bits - 1)
    for y in range(ny):
        for z in range(nz):
            base = index(ORDER_BIL, nx, ny, nz, 0, y, z)
            raw = scene.samples[base:base + nx]
            row = slice(z * nx, (z + 1) * nx)
            dark, gain, bad = cal.dark[row], cal.gain[row], cal.bad[row]
            line, rep = correct_line(raw, dark, gain, bad, scene.depth, cal.gain_frac_bits)
            stats["replaced"] += rep
            for x in range(nx):
                v = ((raw[x] - dark[x]) * gain[x] + half) >> cal.gain_frac_bits
                if v < 0 or v > top:
                    stats["clamped"] += 1
            out[base:base + nx] = array("H", line)
    return Scene(nx, ny, nz, scene.depth, out), stats


def synthetic_calibration(nx, nz, seed=1, dark_mean=200, dark_spread=30,
                          gain_spread=0.08, bad_fraction=0.002):
    """Plausible detector calibration: per-pixel dark offset, gain near 1.0, a few bad pixels."""
    rng = random.Random(seed)
    n = nx * nz
    dark = array("H", [max(0, int(rng.gauss(dark_mean, dark_spread))) for _ in range(n)])
    gain = array("H", [int((1.0 + rng.uniform(-gain_spread, gain_spread)) * (1 << 14)) for _ in range(n)])
    bad = bytearray(1 if rng.random() < bad_fraction else 0 for _ in range(n))
    return Calibration(nx, nz, dark, gain, bad)


def make_raw(clean, cal, seed=1):
    """Emulate raw detector counts from a calibrated scene: x = clean / gain + dark,
    with arbitrary values on bad pixels. correct(make_raw(s)) reproduces s within
    rounding on good pixels."""
    rng = random.Random(seed)
    nx, ny, nz = clean.nx, clean.ny, clean.nz
    top = (1 << clean.depth) - 1
    out = array("H", bytes(2 * len(clean.samples)))
    for y in range(ny):
        for z in range(nz):
            for x in range(nx):
                i = index(ORDER_BIL, nx, ny, nz, x, y, z)
                d, g, b = cal.entry(x, z)
                if b:
                    out[i] = rng.randrange(top + 1)
                else:
                    out[i] = max(0, min(round(clean.samples[i] * (1 << 14) / g) + d, top))
    return Scene(nx, ny, nz, clean.depth, out)
