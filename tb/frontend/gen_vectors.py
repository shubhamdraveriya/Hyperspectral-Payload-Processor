"""Test vectors for tb_frontend (sensor emulator -> radiometric correction ->
corner turn -> CCSDS 123 compressor), from the Python reference model.

    python tb/frontend/gen_vectors.py <outdir> [test ...]

For each test, in <outdir>/<test>/:
    cfg.txt       compressor fields (as tb_c123_core), then front-end fields
    tbl.hex       calibration table load stream, 128-bit beats (two .cal records each)
    dma.hex       raw samples as 128-bit DMA beats (8 samples each; DMA source only)
    raw.hex       sensor emulator output, BIL
    corr.hex      radiometric correction output, BIL
    bip.hex       corner turn output, BIP
    expected.hex  compressed bytes; sizes.txt bytes per scene
    stats.txt     clamp and replacement counts of the last scene
  for the system test (tb_hpdp_core, which runs the acquisition SYS_PASSES times
  and sends the compressed scenes down the downlink chain, through the link and
  the ground receiver):
    dl_cfg.txt    pkt_data ei_mode seed threshold burst_len period asm_bits xfer rx_sel fabric
                  cadus packets flips records
    ground.hex    the ground records the capture DMA must deliver (1024 bytes each)
<outdir>/tests.txt lists the tests.
"""

import struct
import sys
from array import array
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "model"))

from hpdp_model import ccsds123 as c  # noqa: E402
from hpdp_model import channel as ch  # noqa: E402
from hpdp_model import formats as f  # noqa: E402
from hpdp_model import framing  # noqa: E402
from hpdp_model import radiometric as rc  # noqa: E402
from hpdp_model import rs  # noqa: E402
from hpdp_model import sensor  # noqa: E402
from hpdp_model import synth  # noqa: E402

NX_MAX = NZ_MAX = 64          # tb_frontend parameters
SYS_PASSES = 2                # tb_hpdp_core PASSES

# Downlink settings of the system test (register reset values for the rest:
# APID 0x100, SCID 0x42, VCID 1). rx_sel: receiving GTH channel (the testbench's
# transceiver model loops the transmitted words back on that channel only);
# fabric: LNK.CTRL.fabric_loopback instead (both channels carry garbage).
DL_DEFAULT = dict(pkt_data=2048, ei_mode=ch.INJ_OFF, seed=1, threshold=0, burst_len=0, period=1,
                  asm_bits=0, xfer=1, rx_sel=0, fabric=0, ecc_dbe=0)
APID, SCID, VCID = 0x100, 0x42, 1

DEFAULT = dict(source=sensor.SOURCE_DMA, pattern="hyperspectral", raw="calibrated", dims=(24, 6, 20),
               depth=12, scenes=2, slack=40, bypass=False, bad_fraction=0.04, dma_pct=100,
               out_pct=100, cfg={}, drop=False, overrun=False, limit=True)

TESTS = {
    # DMA source, calibration with dark/gain/bad pixels (incl. edge and clustered bad pixels)
    "dma_basic":  {},
    # gaps in the DMA stream, back-pressure from the compressor output; downlink with
    # short packets and random bit errors (BER 1e-3), received on SFP0
    "dma_stalls": dict(dma_pct=30, out_pct=50,
                       dl=dict(pkt_data=300, ei_mode=ch.INJ_RANDOM, seed=777,
                               threshold=int(1e-3 * 2**32), xfer=3, rx_sel=1)),
    # raw noise through a strong calibration: clamps at 0 and at 2^D - 1
    "clamp":      dict(raw="noise", pattern="noise"),
    "bypass":     dict(bypass=True),
    "d16":        dict(depth=16, cfg=dict(omega=16)),
    # (stopped by clearing run instead of the emulator's line limit)
    "ramp":       dict(source=sensor.SOURCE_RAMP, depth=10, limit=False),
    "lfsr":       dict(source=sensor.SOURCE_LFSR, depth=16, bypass=True),
    # line period exactly one line + 1 cycle: no overrun
    "min_period": dict(slack=0),
    # line period shorter than a line: lines start late, overruns counted, data intact
    "overrun":    dict(source=sensor.SOURCE_RAMP, slack=-200, overrun=True),
    "one_column": dict(dims=(1, 10, 16), cfg=dict(local_sum="column_wide")),
    # (downlink: burst errors, link looped back inside the FPGA)
    "max_dims":   dict(dims=(NX_MAX, 2, NZ_MAX), scenes=1, slack=0,
                       dl=dict(pkt_data=1000, ei_mode=ch.INJ_BURST, burst_len=40, period=3000,
                               xfer=2, fabric=1)),
    # (system test: a double-bit error in a calibration table entry that every line
    # reads; with the correction bypassed the data is intact, but every scene is
    # flagged in its packets (RAD-06))
    "ecc_dbe":    dict(bypass=True, dl=dict(ecc_dbe=1)),
    # compressor output starved: the corner turn drops lines and sends fill lines,
    # the compressor stays aligned with the scenes
    "drop":       dict(source=sensor.SOURCE_LFSR, dims=(16, 8, 16), bypass=True, slack=0,
                       out_pct=4, scenes=3, drop=True),
}


def calibration(t):
    nx, _, nz = t["dims"]
    if t["source"] != sensor.SOURCE_DMA:
        # test patterns are small numbers: small dark offsets
        cal = rc.synthetic_calibration(nx, nz, seed=4, dark_mean=6, dark_spread=3,
                                       bad_fraction=t["bad_fraction"])
    elif t["raw"] == "noise":
        cal = rc.synthetic_calibration(nx, nz, seed=5, dark_mean=300, dark_spread=60,
                                       gain_spread=0.25, bad_fraction=t["bad_fraction"])
    else:
        cal = rc.synthetic_calibration(nx, nz, seed=3, bad_fraction=t["bad_fraction"])
    # deterministic bad pixels at the edges and in clusters
    def bad(x, z):
        if x < nx and z < nz:
            cal.bad[z * nx + x] = 1
    bad(0, 0)
    bad(nx - 1, 1)
    bad(5, 2); bad(6, 2)
    bad(9, 3); bad(10, 3); bad(11, 3)
    bad(0, 4); bad(1, 4)
    return cal


def raw_scenes(t, cal):
    nx, ny, nz = t["dims"]
    d = t["depth"]
    if t["source"] != sensor.SOURCE_DMA:
        return [sensor.pattern_scene(t["source"], nx, ny, nz, d)] * t["scenes"]
    out = []
    for k in range(t["scenes"]):
        clean = synth.PATTERNS[t["pattern"]](nx, ny, nz, d, seed=100 * k + 7)
        out.append(clean if t["raw"] == "noise" else rc.make_raw(clean, cal, seed=k + 1))
    return out


def table_beats(cal):
    recs = [dk | (g << 16) | ((b & 1) << 32) for dk, g, b in zip(cal.dark, cal.gain, cal.bad)]
    if len(recs) % 2:
        recs.append(0)
    return [recs[i] | (recs[i + 1] << 64) for i in range(0, len(recs), 2)]


def dma_beats(samples):
    s = list(samples) + [0] * (-len(samples) % 8)
    return [sum(v << (16 * k) for k, v in enumerate(s[i:i + 8])) for i in range(0, len(s), 8)]


def cadu(frame):
    return ch.make_cadu(rs.encode_interleaved(frame, framing.RS_DEPTH))


def ground_vectors(dl, images, ny):
    """The system test's downlink: SYS_PASSES acquisitions of the same scenes,
    scene IDs, timestamps (lines before the scene, counted from reset), packet
    sequence counts and frame counts running on; AOS idle frames off while the
    scenes go down. Returns (CADU count, packets, injected bits, captured
    records)."""
    framer = framing.AosFramer(SCID, VCID)
    stream, seq, packets, i = bytearray(), 0, 0, 0
    for _ in range(SYS_PASSES):
        for img in images:
            pk, seq = framing.packetize_scene(img, APID, i, i * ny, seq_count=seq, max_data=dl["pkt_data"],
                                              flags=framing.FLAG_INTERNAL_ERROR if dl["ecc_dbe"] else 0)
            packets += len(pk)
            for p in pk:
                framer.push(p)
            for fr in framer.frames(flush=True):
                stream += cadu(fr)
            i += 1
    ncadu = len(stream) // ch.CADU_BYTES
    injected, flips = ch.inject_hw(bytes(stream), dl["ei_mode"], dl["seed"], dl["threshold"],
                                   dl["burst_len"], dl["period"], dl["asm_bits"])
    # then the testbench turns the error injector off and idle frames on (they
    # let the frame sync decide the last CADUs). A record count of ncadu covers
    # every science CADU even when the first one is lost (max_dims: the first
    # burst destroys its ASM, so the frame sync acquires on the second CADU and
    # the last record is an idle frame).
    idle = framing.AosFramer(SCID, VCID)
    tail = b"".join(cadu(idle.idle()) for _ in range(2))
    records = ch.frame_sync(injected + tail)[:ncadu]
    if len(records) != ncadu or any(r.sequence != k for k, r in enumerate(records)):
        raise RuntimeError("system test downlink: frame sync records do not follow each other")
    # gr_capture skips records whose VCID field is nearer to 63 than to the science VCID
    def idle_like(v):
        return bin(v ^ framing.IDLE_VCID).count("1") < bin(v ^ VCID).count("1")
    kept = [r for r in records if not idle_like(r.codeblock[1] & 0x3F)]
    rec = bytearray()
    for r in kept:
        rec += r.codeblock + struct.pack("<I", f.CaptureRecord.status_word(r.asm_errors, r.sync_state,
                                                                           r.sequence))
    return ncadu, packets, flips, len(kept), rec


def write_test(out, name, spec):
    t = {**DEFAULT, **spec}
    dl = {**DL_DEFAULT, **t.get("dl", {})}
    nx, ny, nz = t["dims"]
    depth = t["depth"]
    cfg = c.Config(**t["cfg"])
    ctx = c._Ctx.build(nx, ny, nz, depth, cfg)      # resolves R and K
    cal = calibration(t)

    raw, corr, bip, expected, sizes, images = array("H"), array("H"), array("H"), bytearray(), [], []
    stats = {"clamped": 0, "replaced": 0}
    for scene in raw_scenes(t, cal):
        if t["bypass"]:
            fixed, stats = scene, {"clamped": 0, "replaced": 0}
        else:
            fixed, stats = rc.correct(scene, cal)
        raw += scene.samples
        corr += fixed.samples
        bip += fixed.in_order(f.ORDER_BIP)
        image = c.compress(fixed, cfg)
        if c.decompress(image).samples != fixed.samples:
            raise RuntimeError(f"{name}: model round trip failed")
        expected += image
        sizes.append(len(image))
        images.append(image)

    line = nx * nz
    period = line + 1 + t["slack"]
    fields = [nx, ny, nz, depth, cfg.bands, int(cfg.full_prediction), c.LOCAL_SUMS.index(cfg.local_sum),
              cfg.omega, ctx.register_size, cfg.v_min, cfg.v_max, cfg.t_inc_log2, cfg.u_max, cfg.gamma0,
              cfg.gamma_star, ctx.k_init, cfg.output_word_bytes, 0, cfg.user_data,
              t["source"], period, int(t["bypass"]), t["scenes"], t["dma_pct"], t["out_pct"],
              int(t["drop"]), int(t["overrun"]), int(t["limit"])]

    d = out / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "cfg.txt").write_text(" ".join(str(v) for v in fields) + "\n")
    (d / "tbl.hex").write_text("".join(f"{b:032x}\n" for b in table_beats(cal)))
    dma = dma_beats(raw) if t["source"] == sensor.SOURCE_DMA else []
    (d / "dma.hex").write_text("".join(f"{b:032x}\n" for b in dma))
    for fname, data in (("raw.hex", raw), ("corr.hex", corr), ("bip.hex", bip)):
        (d / fname).write_text("".join(f"{s:04x}\n" for s in data))
    (d / "expected.hex").write_text("".join(f"{b:02x}\n" for b in expected))
    (d / "sizes.txt").write_text("".join(f"{s}\n" for s in sizes))
    (d / "stats.txt").write_text(f"{stats['clamped']} {stats['replaced']}\n")

    ncadu, packets, flips, nrec, rec = ground_vectors(dl, images, ny)
    dl_fields = [dl[k] for k in ("pkt_data", "ei_mode", "seed", "threshold", "burst_len", "period",
                                 "asm_bits", "xfer", "rx_sel", "fabric")] + [ncadu, packets, flips, nrec,
                                                                             dl["ecc_dbe"]]
    (d / "dl_cfg.txt").write_text(" ".join(str(v) for v in dl_fields) + "\n")
    (d / "ground.hex").write_text("".join(f"{b:02x}\n" for b in rec))
    return len(raw), len(expected), stats


def main():
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "build/sim/fe")
    names = sys.argv[2:] or list(TESTS)
    for name in names:
        n, b, st = write_test(out, name, TESTS[name])
        print(f"{name:12} {n:6} samples -> {b:6} bytes, last scene: "
              f"{st['clamped']} clamped, {st['replaced']} replaced")
    (out / "tests.txt").write_text("\n".join(names) + "\n")


if __name__ == "__main__":
    main()
