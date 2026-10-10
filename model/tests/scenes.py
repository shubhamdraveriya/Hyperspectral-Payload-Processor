"""Synthetic test scenes shared by the model tests."""

import random
from array import array

from hpdp_model import formats as f


def smooth(nx, ny, nz, depth, seed=1, noise=16):
    """Spatial and spectral ramps plus uniform noise: compresses well."""
    rng = random.Random(seed)
    top = (1 << depth) - 1
    base = 1 << (depth - 2)
    s = array("H")
    for y in range(ny):
        for z in range(nz):
            for x in range(nx):
                v = base + (top >> 6) * z // 4 + 3 * x + 2 * y + rng.randrange(noise)
                s.append(min(v, top))
    return f.Scene(nx, ny, nz, depth, s)


def noise(nx, ny, nz, depth, seed=1):
    rng = random.Random(seed)
    return f.Scene(nx, ny, nz, depth, array("H", [rng.randrange(1 << depth) for _ in range(nx * ny * nz)]))


def constant(nx, ny, nz, depth, value):
    return f.Scene(nx, ny, nz, depth, array("H", [value] * (nx * ny * nz)))
