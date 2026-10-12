import random

from hpdp_model import channel as ch
from hpdp_model.formats import CODEBLOCK_BYTES


def test_pn_sequence_matches_ccsds():
    assert ch.pn_sequence(8) == bytes.fromhex("FF480EC09A0D70BC")
    bits = "".join(f"{b:08b}" for b in ch.pn_sequence(64))
    assert bits[:255] == bits[255:510]               # period 255


def test_randomize_is_self_inverse():
    block = bytes(random.Random(1).randrange(256) for _ in range(CODEBLOCK_BYTES))
    assert ch.randomize(ch.randomize(block)) == block


def test_cadu_layout():
    cadu = ch.make_cadu(bytes(CODEBLOCK_BYTES))
    assert len(cadu) == ch.CADU_BYTES == 1024
    assert cadu[:4] == ch.ASM
    assert cadu[4:12] == bytes.fromhex("FF480EC09A0D70BC")   # zeros come out as the PN sequence


def blocks(n, seed=0):
    rng = random.Random(seed)
    return [bytes(rng.randrange(256) for _ in range(CODEBLOCK_BYTES)) for _ in range(n)]


def test_frame_sync_clean_stream_with_leading_garbage():
    bl = blocks(5)
    stream = bytes(random.Random(9).randrange(256) for _ in range(333)) + b"".join(ch.make_cadu(b) for b in bl)
    recs = ch.frame_sync(stream)
    assert [r.codeblock for r in recs] == bl
    assert [r.sync_state for r in recs] == ["VERIFY", "LOCK", "LOCK", "LOCK", "LOCK"]
    assert [r.sequence for r in recs] == [0, 1, 2, 3, 4]


def flip_asm_bits(stream, cadu_index, nbits):
    out = bytearray(stream)
    base = cadu_index * ch.CADU_BYTES
    for b in range(nbits):
        out[base + b // 8] ^= 0x80 >> (b % 8)
    return bytes(out)


def test_asm_bit_errors_tolerance_and_flywheel():
    bl = blocks(6, seed=1)
    stream = b"".join(ch.make_cadu(b) for b in bl)
    stream = flip_asm_bits(stream, 2, 2)              # within tolerance
    stream = flip_asm_bits(stream, 3, 5)              # missed ASM: flywheel
    recs = ch.frame_sync(stream)
    assert [r.codeblock for r in recs] == bl
    assert [(r.asm_errors, r.sync_state) for r in recs][2:5] == [(2, "LOCK"), (5, "FLYWHEEL"), (0, "LOCK")]


def test_loses_lock_after_too_many_misses_and_reacquires():
    bl = blocks(10, seed=2)
    stream = b"".join(ch.make_cadu(b) for b in bl)
    for i in (2, 3, 4, 5):                            # 4 consecutive misses > flywheel 3
        stream = flip_asm_bits(stream, i, 8)
    recs = ch.frame_sync(stream)
    got = [r.codeblock for r in recs]
    assert got[:2] == bl[:2]
    assert got[-4:] == bl[-4:]                        # reacquired: 6 (VERIFY), 7, 8, 9
    assert any(r.sync_state == "FLYWHEEL" for r in recs)


def test_bit_error_injection():
    data = bytes(10000)
    same, n0 = ch.inject_bit_errors(data, 0.0)
    assert same == data and n0 == 0
    a, na = ch.inject_bit_errors(data, 1e-3, seed=5)
    b, nb = ch.inject_bit_errors(data, 1e-3, seed=5)
    assert a == b and na == nb == sum(bin(x).count("1") for x in a)
    assert 40 < na < 130                              # about 80 expected
    burst = ch.inject_burst(data, 12, 20)
    assert sum(bin(x).count("1") for x in burst) == 20


def test_hw_injector_rates_and_modes():
    data = bytes(64 * 1024)
    out, flips = ch.inject_hw(data, ch.INJ_RANDOM, seed=7, threshold=int(1e-3 * 2**32))
    assert flips == sum(bin(b).count("1") for b in out)
    assert 0.8e-3 < flips / (8 * len(data)) < 1.2e-3
    assert ch.inject_hw(data, ch.INJ_RANDOM, seed=7, threshold=0)[1] == 0
    # burst: 12 bits every 100 bytes
    out, flips = ch.inject_hw(bytes(1000), ch.INJ_BURST, burst_len=12, period=100)
    assert flips == 120 and out[0] == 0xFF and out[1] == 0xF0 and out[100] == 0xFF and out[2] == 0
    # ASM: 3 bits of every second CADU's marker
    stream = b"".join(ch.make_cadu(bytes(1020)) for _ in range(4))
    out, flips = ch.inject_hw(stream, ch.INJ_ASM, asm_bits=3, period=2)
    assert flips == 6 and out[0] == stream[0] ^ 0xE0 and out[1024] == stream[1024] and out[2048] == stream[2048] ^ 0xE0
