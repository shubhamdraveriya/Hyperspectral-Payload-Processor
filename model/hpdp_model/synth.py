"""Synthetic hyperspectral scenes for tests, experiments and RTL test vectors."""

import math
import random
from array import array

from .formats import Scene


def hyperspectral(nx, ny, nz, depth=12, seed=1, endmembers=4, noise=6.0):
    """Pixels mixing a few smooth endmember spectra with spatially varying
    abundances, plus Gaussian noise: spectrally correlated like real imagery."""
    rng = random.Random(seed)
    top = (1 << depth) - 1
    ends = [[0.5 + 0.4 * math.sin(2 * math.pi * (z / nz) * (k + 1) + k) for z in range(nz)]
            for k in range(endmembers)]
    s = array("H")
    for y in range(ny):
        ab = [[0.5 + 0.5 * math.sin(0.15 * (k + 1) * x + 0.1 * (k + 2) * y + k) for k in range(endmembers)]
              for x in range(nx)]
        for z in range(nz):
            for x in range(nx):
                w = ab[x]
                v = sum(wk * ends[k][z] for k, wk in enumerate(w)) / sum(w)
                s.append(max(0, min(int(v * 0.8 * top + rng.gauss(0, noise)), top)))
    return Scene(nx, ny, nz, depth, s)


def textured(nx, ny, nz, depth=12, seed=1, step=100):
    """The same random spatial texture in every band plus a per-band offset."""
    rng = random.Random(seed)
    span = (1 << depth) - 1 - step * (nz - 1)
    if span <= 0:
        raise ValueError("offset step too large for the dynamic range")
    tex = [rng.randrange(span) for _ in range(nx * ny)]
    return Scene(nx, ny, nz, depth, array("H", [tex[y * nx + x] + step * z
                                                for y in range(ny) for z in range(nz) for x in range(nx)]))


def noise(nx, ny, nz, depth=12, seed=1):
    rng = random.Random(seed)
    return Scene(nx, ny, nz, depth, array("H", [rng.randrange(1 << depth) for _ in range(nx * ny * nz)]))


def zero(nx, ny, nz, depth=12, seed=1):
    """Every sample 0 (corner case, SPEC VER-03)."""
    return Scene(nx, ny, nz, depth, array("H", [0] * (nx * ny * nz)))


def full_scale(nx, ny, nz, depth=12, seed=1):
    """Every sample at the maximum, 2^D - 1 (corner case, SPEC VER-03)."""
    return Scene(nx, ny, nz, depth, array("H", [(1 << depth) - 1] * (nx * ny * nz)))


PATTERNS = {"hyperspectral": hyperspectral, "textured": textured, "noise": noise,
            "zero": zero, "full_scale": full_scale}
