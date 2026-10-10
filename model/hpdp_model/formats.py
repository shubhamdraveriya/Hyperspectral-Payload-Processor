"""File formats shared by the Python tools, the bare-metal application and
the hardware (SPEC.md section 7). Standard library only.

Scene (.hpdp)        64-byte header, then little-endian uint16 samples
Calibration (.cal)   64-byte header, then one 8-byte record per (band, pixel)
Capture (.cap)       1024-byte records: 1020-byte codeblock + uint32 status
"""

import struct
import sys
from array import array
from dataclasses import dataclass, field
from pathlib import Path

HEADER_SIZE = 64
VERSION = 1

ORDER_BIL, ORDER_BIP, ORDER_BSQ = 0, 1, 2
ORDER_NAMES = {ORDER_BIL: "BIL", ORDER_BIP: "BIP", ORDER_BSQ: "BSQ"}

# magic, version, header size, nx, ny, nz, depth, order   (rest of the 64 bytes is zero)
_SCENE_HDR = struct.Struct("<4sHHIIIBB")
# magic, version, header size, nx, nz, gain fraction bits
_CAL_HDR = struct.Struct("<4sHHIIB")
# dark, gain, flags, 3 reserved bytes
_CAL_REC = struct.Struct("<HHB3x")

CODEBLOCK_BYTES = 1020
CAPTURE_RECORD_BYTES = 1024

SYNC_STATES = ("SEARCH", "VERIFY", "LOCK", "FLYWHEEL")


class FormatError(ValueError):
    pass


def _u16_array(data=b""):
    a = array("H")
    a.frombytes(data)
    if sys.byteorder == "big":
        a.byteswap()
    return a


def _u16_bytes(a):
    if sys.byteorder == "big":
        a = array("H", a)
        a.byteswap()
    return a.tobytes()


# ---------------------------------------------------------------------------
# Sample orders
# ---------------------------------------------------------------------------
def index(order, nx, ny, nz, x, y, z):
    """Position of sample (x, y, z) in a flat array stored in the given order."""
    if order == ORDER_BIL:
        return (y * nz + z) * nx + x
    if order == ORDER_BIP:
        return (y * nx + x) * nz + z
    if order == ORDER_BSQ:
        return (z * ny + y) * nx + x
    raise FormatError(f"unknown sample order {order}")


def reorder(samples, nx, ny, nz, src, dst):
    """Return the samples rearranged from order src to order dst."""
    if src == dst:
        return array("H", samples)
    out = array("H", bytes(2 * len(samples)))
    for y in range(ny):
        for z in range(nz):
            for x in range(nx):
                out[index(dst, nx, ny, nz, x, y, z)] = samples[index(src, nx, ny, nz, x, y, z)]
    return out


# ---------------------------------------------------------------------------
# Scenes
# ---------------------------------------------------------------------------
@dataclass
class Scene:
    """A hyperspectral scene. Samples are held in BIL order, as the sensor emulator outputs them."""

    nx: int
    ny: int
    nz: int
    depth: int
    samples: array = field(repr=False)

    def __post_init__(self):
        if not (1 <= self.nx and 1 <= self.ny and 1 <= self.nz):
            raise FormatError("image dimensions must be at least 1")
        if not 2 <= self.depth <= 16:
            raise FormatError(f"sample depth {self.depth} outside 2..16")
        if len(self.samples) != self.nx * self.ny * self.nz:
            raise FormatError(f"{len(self.samples)} samples for a {self.nx}x{self.ny}x{self.nz} scene")
        limit = 1 << self.depth
        if any(s >= limit for s in self.samples):
            raise FormatError(f"sample value exceeds {self.depth}-bit range")

    def get(self, x, y, z):
        return self.samples[index(ORDER_BIL, self.nx, self.ny, self.nz, x, y, z)]

    def in_order(self, order):
        return reorder(self.samples, self.nx, self.ny, self.nz, ORDER_BIL, order)


def write_scene(path, scene, order=ORDER_BIL):
    header = _SCENE_HDR.pack(b"HPDP", VERSION, HEADER_SIZE, scene.nx, scene.ny, scene.nz, scene.depth, order)
    data = header.ljust(HEADER_SIZE, b"\0") + _u16_bytes(scene.in_order(order))
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_bytes(data)


def read_scene(path):
    data = Path(path).read_bytes()
    if len(data) < HEADER_SIZE:
        raise FormatError("file shorter than the header")
    magic, version, hsize, nx, ny, nz, depth, order = _SCENE_HDR.unpack_from(data)
    if magic != b"HPDP":
        raise FormatError(f"bad magic {magic!r}, expected b'HPDP'")
    if version != VERSION or hsize != HEADER_SIZE:
        raise FormatError(f"unsupported version {version} / header size {hsize}")
    if order not in ORDER_NAMES:
        raise FormatError(f"unknown sample order {order}")
    expected = HEADER_SIZE + 2 * nx * ny * nz
    if len(data) != expected:
        raise FormatError(f"file is {len(data)} bytes, expected {expected}")
    samples = _u16_array(data[HEADER_SIZE:])
    return Scene(nx, ny, nz, depth, reorder(samples, nx, ny, nz, order, ORDER_BIL))


# ---------------------------------------------------------------------------
# Calibration tables
# ---------------------------------------------------------------------------
@dataclass
class Calibration:
    """Per (band, pixel) dark offset, gain (unsigned, gain_frac_bits fraction bits) and bad flag."""

    nx: int
    nz: int
    dark: array = field(repr=False)
    gain: array = field(repr=False)
    bad: bytearray = field(repr=False)
    gain_frac_bits: int = 14

    def __post_init__(self):
        n = self.nx * self.nz
        if not (len(self.dark) == len(self.gain) == len(self.bad) == n):
            raise FormatError(f"calibration tables must each hold {n} entries")

    @classmethod
    def identity(cls, nx, nz):
        n = nx * nz
        return cls(nx, nz, array("H", [0] * n), array("H", [1 << 14] * n), bytearray(n))

    def entry(self, x, z):
        i = z * self.nx + x
        return self.dark[i], self.gain[i], bool(self.bad[i])


def write_cal(path, cal):
    header = _CAL_HDR.pack(b"HCAL", VERSION, HEADER_SIZE, cal.nx, cal.nz, cal.gain_frac_bits)
    body = b"".join(_CAL_REC.pack(d, g, b & 1) for d, g, b in zip(cal.dark, cal.gain, cal.bad))
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_bytes(header.ljust(HEADER_SIZE, b"\0") + body)


def read_cal(path):
    data = Path(path).read_bytes()
    if len(data) < HEADER_SIZE:
        raise FormatError("file shorter than the header")
    magic, version, hsize, nx, nz, frac = _CAL_HDR.unpack_from(data)
    if magic != b"HCAL":
        raise FormatError(f"bad magic {magic!r}, expected b'HCAL'")
    if version != VERSION or hsize != HEADER_SIZE:
        raise FormatError(f"unsupported version {version} / header size {hsize}")
    if len(data) != HEADER_SIZE + _CAL_REC.size * nx * nz:
        raise FormatError("file size does not match the table dimensions")
    dark, gain, bad = array("H"), array("H"), bytearray()
    for d, g, flags in _CAL_REC.iter_unpack(data[HEADER_SIZE:]):
        dark.append(d)
        gain.append(g)
        bad.append(flags & 1)
    return Calibration(nx, nz, dark, gain, bad, frac)


# ---------------------------------------------------------------------------
# Ground captures
# ---------------------------------------------------------------------------
@dataclass
class CaptureRecord:
    codeblock: bytes      # 1020 bytes, derandomized, before RS decoding
    asm_errors: int       # mismatched bits in the sync marker (0..32)
    sync_state: str       # SEARCH / VERIFY / LOCK / FLYWHEEL
    sequence: int         # 16-bit CADU counter

    @staticmethod
    def status_word(asm_errors, sync_state, sequence):
        """Status word layout: [5:0] ASM bit errors, [7:6] sync state, [15:8] reserved, [31:16] sequence."""
        return (asm_errors & 0x3F) | (SYNC_STATES.index(sync_state) << 6) | ((sequence & 0xFFFF) << 16)


def read_capture(path):
    data = Path(path).read_bytes()
    if len(data) % CAPTURE_RECORD_BYTES:
        raise FormatError(f"capture size {len(data)} is not a multiple of {CAPTURE_RECORD_BYTES}")
    records = []
    for off in range(0, len(data), CAPTURE_RECORD_BYTES):
        block = data[off:off + CODEBLOCK_BYTES]
        (status,) = struct.unpack_from("<I", data, off + CODEBLOCK_BYTES)
        records.append(CaptureRecord(block, status & 0x3F, SYNC_STATES[(status >> 6) & 3], status >> 16))
    return records


def write_capture(path, records):
    out = bytearray()
    for r in records:
        if len(r.codeblock) != CODEBLOCK_BYTES:
            raise FormatError(f"codeblock must be {CODEBLOCK_BYTES} bytes")
        out += r.codeblock
        out += struct.pack("<I", CaptureRecord.status_word(r.asm_errors, r.sync_state, r.sequence))
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_bytes(bytes(out))
