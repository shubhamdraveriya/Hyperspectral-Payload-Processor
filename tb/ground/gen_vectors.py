"""Test vectors for tb_ground (error_injector -> frame_sync), from the Python
model: channel.inject_hw() and channel.frame_sync().

    python tb/ground/gen_vectors.py <outdir> [test ...]

For each test, in <outdir>/<test>/:
    cfg.txt       mode seed threshold burst_len period asm_bits asm_tol flywheel in_pct out_pct drops
    input.txt     byte stream into the injector: byte first_of_cadu
    injected.hex  injector output (expected)
    records.hex   frame sync records (1020-byte codeblock + status word, little-endian)
<outdir>/tests.txt lists the tests.
"""

import random
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "model"))

from hpdp_model import channel as ch  # noqa: E402
from hpdp_model.formats import CaptureRecord  # noqa: E402

DEFAULT = dict(mode=ch.INJ_OFF, seed=1, threshold=0, burst_len=0, period=1, asm_bits=0, asm_tol=2,
               flywheel=3, in_pct=50, out_pct=90, cadus=12, garbage=0, slips=(), drops=0)

TESTS = {
    "clean":     dict(garbage=333),
    # a lost byte in the middle: lock lost after the flywheel, then reacquired
    "slip":      dict(cadus=16, slips=(6 * 1024 + 500,)),
    "asm_tol":   dict(mode=ch.INJ_ASM, asm_bits=2, period=1),           # 2 wrong bits: tolerated
    "flywheel":  dict(mode=ch.INJ_ASM, asm_bits=3, period=4, cadus=16),  # every 4th ASM missed
    "lose_lock": dict(mode=ch.INJ_ASM, asm_bits=5, period=1, cadus=8,    # no ASM matches after
                      garbage=0),
    "ber":       dict(mode=ch.INJ_RANDOM, seed=12345, threshold=int(2e-3 * 2**32), cadus=24),
    "burst":     dict(mode=ch.INJ_BURST, burst_len=40, period=3000, cadus=12),
    "tol0":      dict(mode=ch.INJ_ASM, asm_bits=1, period=3, asm_tol=0, flywheel=1, cadus=12),
    # slow capture: records that do not fit are dropped (and counted)
    "overflow":  dict(cadus=20, in_pct=100, out_pct=5, drops=1),
}


def write_test(out, name, spec):
    t = {**DEFAULT, **spec}
    rng = random.Random(t["seed"] + 99)
    stream = bytearray(rng.randrange(256) for _ in range(t["garbage"]))
    starts = set()
    for _ in range(t["cadus"]):
        starts.add(len(stream))
        stream += ch.make_cadu(bytes(rng.randrange(256) for _ in range(1020)))
    for s in sorted(t["slips"], reverse=True):
        del stream[s]
        starts = {p - 1 if p > s else p for p in starts}
    if t["mode"] == ch.INJ_ASM and (t["garbage"] or t["slips"]):
        raise ValueError("ASM mode tests need CADUs at multiples of 1024")
    injected, flips = ch.inject_hw(bytes(stream), t["mode"], t["seed"], t["threshold"], t["burst_len"],
                                   t["period"], t["asm_bits"])
    records = ch.frame_sync(injected, t["asm_tol"], t["flywheel"])

    d = out / name
    d.mkdir(parents=True, exist_ok=True)
    fields = [t[k] for k in ("mode", "seed", "threshold", "burst_len", "period", "asm_bits", "asm_tol",
                             "flywheel", "in_pct", "out_pct", "drops")]
    (d / "cfg.txt").write_text(" ".join(str(v) for v in fields) + "\n")
    (d / "input.txt").write_text("".join(f"{b:02x} {int(i in starts)}\n" for i, b in enumerate(stream)))
    (d / "injected.hex").write_text("".join(f"{b:02x}\n" for b in injected))
    rec = bytearray()
    for r in records:
        rec += r.codeblock + struct.pack("<I", CaptureRecord.status_word(r.asm_errors, r.sync_state, r.sequence))
    (d / "records.hex").write_text("".join(f"{b:02x}\n" for b in rec))
    states = "".join(r.sync_state[0] for r in records)
    return len(stream), flips, states


def main():
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "build/sim/gr")
    names = sys.argv[2:] or list(TESTS)
    for name in names:
        n, flips, states = write_test(out, name, TESTS[name])
        print(f"{name:10} {n:6} bytes, {flips:4} flipped bits, records {states or '-'}")
    (out / "tests.txt").write_text("\n".join(names) + "\n")


if __name__ == "__main__":
    main()
