import random
from array import array

import pytest

from hpdp_model import formats as f


def random_scene(nx=7, ny=5, nz=4, depth=12, seed=1):
    rng = random.Random(seed)
    samples = array("H", [rng.randrange(1 << depth) for _ in range(nx * ny * nz)])
    return f.Scene(nx, ny, nz, depth, samples)


def test_index_orders_are_permutations():
    nx, ny, nz = 3, 4, 5
    for order in (f.ORDER_BIL, f.ORDER_BIP, f.ORDER_BSQ):
        seen = {f.index(order, nx, ny, nz, x, y, z) for x in range(nx) for y in range(ny) for z in range(nz)}
        assert seen == set(range(nx * ny * nz))


def test_bip_puts_bands_of_one_pixel_together():
    nx, ny, nz = 3, 2, 4
    assert [f.index(f.ORDER_BIP, nx, ny, nz, 1, 0, z) for z in range(nz)] == [4, 5, 6, 7]


def test_reorder_round_trip():
    s = random_scene()
    for order in (f.ORDER_BIP, f.ORDER_BSQ):
        there = s.in_order(order)
        back = f.reorder(there, s.nx, s.ny, s.nz, order, f.ORDER_BIL)
        assert back == s.samples


@pytest.mark.parametrize("order", [f.ORDER_BIL, f.ORDER_BIP, f.ORDER_BSQ])
def test_scene_file_round_trip(tmp_path, order):
    s = random_scene()
    path = tmp_path / "scene.hpdp"
    f.write_scene(path, s, order)
    assert path.stat().st_size == f.HEADER_SIZE + 2 * s.nx * s.ny * s.nz
    r = f.read_scene(path)
    assert (r.nx, r.ny, r.nz, r.depth) == (s.nx, s.ny, s.nz, s.depth)
    assert r.samples == s.samples


def test_scene_header_layout(tmp_path):
    s = random_scene(nx=512, ny=1, nz=2)
    path = tmp_path / "scene.hpdp"
    f.write_scene(path, s)
    head = path.read_bytes()[:f.HEADER_SIZE]
    assert head[:4] == b"HPDP"
    assert int.from_bytes(head[8:12], "little") == 512
    assert head[20] == 12 and head[21] == f.ORDER_BIL
    assert head[22:] == bytes(f.HEADER_SIZE - 22)


def test_scene_rejects_bad_files(tmp_path):
    path = tmp_path / "bad.hpdp"
    path.write_bytes(b"XXXX" + bytes(60))
    with pytest.raises(f.FormatError, match="magic"):
        f.read_scene(path)
    s = random_scene()
    f.write_scene(path, s)
    path.write_bytes(path.read_bytes()[:-2])
    with pytest.raises(f.FormatError, match="expected"):
        f.read_scene(path)


def test_scene_validates_contents():
    with pytest.raises(f.FormatError, match="range"):
        f.Scene(2, 1, 1, 8, array("H", [255, 256]))
    with pytest.raises(f.FormatError, match="depth"):
        f.Scene(1, 1, 1, 17, array("H", [0]))


def test_calibration_round_trip(tmp_path):
    rng = random.Random(2)
    nx, nz = 9, 3
    cal = f.Calibration(
        nx, nz,
        array("H", [rng.randrange(1 << 16) for _ in range(nx * nz)]),
        array("H", [rng.randrange(1 << 16) for _ in range(nx * nz)]),
        bytearray(rng.randrange(2) for _ in range(nx * nz)),
    )
    path = tmp_path / "t.cal"
    f.write_cal(path, cal)
    assert path.stat().st_size == f.HEADER_SIZE + 8 * nx * nz
    r = f.read_cal(path)
    assert (r.dark, r.gain, r.bad, r.gain_frac_bits) == (cal.dark, cal.gain, cal.bad, 14)
    assert r.entry(4, 2) == cal.entry(4, 2)


def test_identity_calibration():
    cal = f.Calibration.identity(4, 2)
    assert cal.entry(3, 1) == (0, 0x4000, False)


def test_capture_round_trip(tmp_path):
    records = [
        f.CaptureRecord(bytes([i]) * f.CODEBLOCK_BYTES, i % 33, f.SYNC_STATES[i % 4], 65530 + i)
        for i in range(10)
    ]
    path = tmp_path / "run.cap"
    f.write_capture(path, records)
    back = f.read_capture(path)
    assert [(r.codeblock, r.asm_errors, r.sync_state, r.sequence) for r in back] == [
        (r.codeblock, r.asm_errors, r.sync_state, r.sequence & 0xFFFF) for r in records
    ]


def test_capture_status_word_layout():
    assert f.CaptureRecord.status_word(5, "LOCK", 0x1234) == 0x1234_0000 | (2 << 6) | 5
