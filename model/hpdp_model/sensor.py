"""Sensor emulator test patterns (SPEC.md SE-06), bit-exact with
rtl/frontend/sensor_emulator.sv.

    ramp: sample(x, y, z) = (x + z + y) mod 2^D, y = line within the scene
    lfsr: 16-bit Galois LFSR, taps 0xB400 (x^16 + x^14 + x^13 + x^11 + 1),
          seeded with 0xACE1 at the first sample of every scene; each sample is
          the state's D low bits, then the LFSR steps once.
Samples are produced in BIL order, like the detector readout.
"""

from array import array

from .formats import Scene

LFSR_SEED = 0xACE1
LFSR_TAPS = 0xB400
SOURCE_DMA, SOURCE_RAMP, SOURCE_LFSR = 0, 1, 2


def lfsr_step(state):
    lsb = state & 1
    state >>= 1
    return state ^ LFSR_TAPS if lsb else state


def pattern_scene(source, nx, ny, nz, depth):
    mask = (1 << depth) - 1
    out = array("H")
    state = LFSR_SEED
    for y in range(ny):
        for z in range(nz):
            for x in range(nx):
                if source == SOURCE_RAMP:
                    out.append((x + z + y) & mask)
                elif source == SOURCE_LFSR:
                    out.append(state & mask)
                    state = lfsr_step(state)
                else:
                    raise ValueError("pattern source must be ramp or lfsr")
    return Scene(nx, ny, nz, depth, out)
