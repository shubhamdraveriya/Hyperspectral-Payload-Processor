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
