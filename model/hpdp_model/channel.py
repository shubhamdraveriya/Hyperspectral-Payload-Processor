"""Channel coding around the RS codeblock (CCSDS 131.0-B): pseudo-randomizer,
attached sync marker, CADUs, and the ground receiver's frame synchronizer
(SPEC.md 6.7, 6.8, 6.10)."""

import math
import random

from .formats import CODEBLOCK_BYTES, CaptureRecord

ASM = bytes.fromhex("1ACFFC1D")
CADU_BYTES = len(ASM) + CODEBLOCK_BYTES     # 1024


def pn_sequence(nbytes):
    """CCSDS pseudo-randomizer, h(x) = x^8 + x^7 + x^5 + x^3 + 1, all-ones seed.

    8-bit LFSR, output = MSB, feedback = parity of bits 7, 4, 2, 0."""
    r, out = 0xFF, bytearray()
    for _ in range(nbytes):
        b = 0
        for _ in range(8):
            b = (b << 1) | (r >> 7)
            fb = (r ^ (r >> 2) ^ (r >> 4) ^ (r >> 7)) & 1
            r = ((r << 1) | fb) & 0xFF
        out.append(b)
    return bytes(out)


_PN = pn_sequence(CODEBLOCK_BYTES)


def randomize(block):
    """XOR with the PN sequence restarted at the block start (self-inverse)."""
    return bytes(a ^ b for a, b in zip(block, _PN))


def make_cadu(codeblock):
    if len(codeblock) != CODEBLOCK_BYTES:
        raise ValueError(f"codeblock must be {CODEBLOCK_BYTES} bytes")
    return ASM + randomize(codeblock)


def _asm_errors(stream, pos):
    return sum(bin(a ^ b).count("1") for a, b in zip(stream[pos:pos + 4], ASM))


def frame_sync(stream, asm_tol=2, flywheel=3):
    """Byte-aligned frame synchronizer (SPEC GR-01..GR-03).

    SEARCH: find an ASM with <= asm_tol wrong bits. VERIFY: the next ASM one CADU
    later must also match; then both CADUs are delivered and the state is LOCK.
    In LOCK a missed ASM moves to FLYWHEEL (the CADU is still delivered);
    more than `flywheel` consecutive misses return to SEARCH.

    Returns a list of CaptureRecord with derandomized codeblocks.
    """
    out, seq = [], 0
    n = len(stream)
    pos, state, misses = 0, "SEARCH", 0

    def deliver(p, errors, st):
        nonlocal seq
        block = randomize(stream[p + 4:p + CADU_BYTES])
        out.append(CaptureRecord(block, min(errors, 63), st, seq & 0xFFFF))
        seq += 1

    while pos + CADU_BYTES <= n:
        if state == "SEARCH":
            if _asm_errors(stream, pos) <= asm_tol:
                nxt = pos + CADU_BYTES
                if nxt + CADU_BYTES <= n and _asm_errors(stream, nxt) <= asm_tol:
                    deliver(pos, _asm_errors(stream, pos), "VERIFY")
                    pos, state, misses = nxt, "LOCK", 0
                    continue
            pos += 1
            continue
        errors = _asm_errors(stream, pos)
        if errors <= asm_tol:
            state, misses = "LOCK", 0
        else:
            misses += 1
            if misses > flywheel:
                state, misses = "SEARCH", 0
                pos += 1
                continue
            state = "FLYWHEEL"
        deliver(pos, errors, state)
        pos += CADU_BYTES
    return out


def inject_bit_errors(data, ber, seed=1):
    """Flip each bit independently with probability ber. Returns (new bytes, number of flips)."""
    rng = random.Random(seed)
    out = bytearray(data)
    flips = 0
    if ber <= 0:
        return bytes(out), 0
    nbits = 8 * len(out)
    # geometric gaps between errors: cheap even for long streams
    pos = -1
    while True:
        gap = int(math.log(1.0 - rng.random()) / math.log(1.0 - ber)) if ber < 1 else 0
        pos += gap + 1
        if pos >= nbits:
            break
        out[pos >> 3] ^= 0x80 >> (pos & 7)
        flips += 1
    return bytes(out), flips


def inject_burst(data, start_bit, length):
    """Flip `length` consecutive bits starting at start_bit."""
    out = bytearray(data)
    for b in range(start_bit, min(start_bit + length, 8 * len(out))):
        out[b >> 3] ^= 0x80 >> (b & 7)
    return bytes(out)


# ---------------------------------------------------------------------------
# Bit-exact model of the error injector RTL (rtl/downlink/error_injector.sv)
# ---------------------------------------------------------------------------
INJ_OFF, INJ_RANDOM, INJ_BURST, INJ_ASM = 0, 1, 2, 3
_GOLDEN = 0x9E3779B9


def _xorshift32(x):
    x ^= (x << 13) & 0xFFFFFFFF
    x ^= x >> 17
    x ^= (x << 5) & 0xFFFFFFFF
    return x


def inject_hw(data, mode, seed=1, threshold=0, burst_len=0, period=1, asm_bits=0, cadu_bytes=CADU_BYTES):
    """Apply the hardware error injector to a byte stream whose CADUs start at
    multiples of cadu_bytes. Returns (new bytes, number of flipped bits).

    random: bit 7-k of each byte flips when lane k (xorshift32, seeded with
            seed ^ (k+1) * 0x9E3779B9) is below threshold; all lanes step per byte.
    burst:  burst_len bits from the first bit of every period-th byte.
    asm:    the first asm_bits bits of the marker of every period-th CADU.
    """
    lanes = [((seed ^ (_GOLDEN * (k + 1))) & 0xFFFFFFFF) or 1 for k in range(8)]
    out = bytearray(data)
    flips = bc = cc = burst_left = 0
    hit = False
    for i, b in enumerate(data):
        mask = 0
        if mode == INJ_RANDOM:
            for k in range(8):
                if lanes[k] < threshold:
                    mask |= 0x80 >> k
        elif mode == INJ_BURST:
            bl = burst_len if bc == 0 else burst_left
            n = min(bl, 8)
            mask = (0xFF00 >> n) & 0xFF
            burst_left = bl - n
        elif mode == INJ_ASM:
            j = i % cadu_bytes
            if j == 0:
                hit = cc == 0
                cc = (cc + 1) % period if period > 1 else 0
            if hit and j < 4:
                n = min(max(asm_bits - 8 * j, 0), 8)
                mask = (0xFF00 >> n) & 0xFF
        lanes = [_xorshift32(x) for x in lanes]
        bc = (bc + 1) % period if period > 1 else 0
        out[i] = b ^ mask
        flips += bin(mask).count("1")
    return bytes(out), flips
