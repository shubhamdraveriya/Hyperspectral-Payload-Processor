"""Test vectors for tb_c123_core, from the Python reference model.

    python tb/ccsds123/gen_vectors.py <outdir>

For each test: <outdir>/<test>/cfg.txt, input.hex (samples, BIP order),
expected.hex (compressed bytes), delta.hex (mapped residuals, for debugging).
<outdir>/tests.txt lists the tests.

Besides the named tests, the configuration matrix of SPEC VER-02 (MATRIX, 864
configurations, tests mx_*): environment variable HPDP_C123_MATRIX picks how many
go into a run, a random subset (default 6) or "all" (the nightly run);
HPDP_C123_SEED fixes the subset (default: a new seed each run). The seed and the
chosen tests are printed and written to <outdir>/matrix.txt, so a failing subset
can be run again.
"""

import itertools
import os
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "model"))

from hpdp_model import ccsds123 as c  # noqa: E402
from hpdp_model import formats as f  # noqa: E402
from hpdp_model import synth  # noqa: E402
from hpdp_model.bitstream import BitWriter  # noqa: E402

NX_MAX = NZ_MAX = 64          # tb_c123_core parameters

DEFAULT = dict(pattern="hyperspectral", dims=(16, 5, 20), depth=12, cfg={}, scenes=2,
               in_pct=100, out_pct=100, raw=False)

TESTS = {
    "basic":        {},
    "stalls":       dict(in_pct=60, out_pct=35),
    "reduced":      dict(cfg=dict(full_prediction=False)),
    "narrow_nbr":   dict(cfg=dict(local_sum="neighbor_narrow")),
    "wide_col":     dict(cfg=dict(local_sum="column_wide")),
    "reduced_col":  dict(cfg=dict(full_prediction=False, local_sum="column_narrow")),
    "p0":           dict(cfg=dict(bands=0)),
    "p1":           dict(cfg=dict(bands=1)),
    "p15":          dict(cfg=dict(bands=15)),
    "omega4":       dict(cfg=dict(omega=4)),
    "omega19":      dict(cfg=dict(omega=19)),
    "wrap_r32":     dict(pattern="noise", depth=16, cfg=dict(omega=14)),   # R = 32, mod_R wraps
    "r64":          dict(cfg=dict(register_size=64)),
    "fast_weights": dict(pattern="textured", cfg=dict(v_min=-6, v_max=9, t_inc_log2=4)),
    "umax8":        dict(pattern="noise", cfg=dict(u_max=8)),
    "umax32":       dict(cfg=dict(u_max=32)),
    "counter":      dict(cfg=dict(gamma0=8, gamma_star=11)),
    "k0":           dict(cfg=dict(k_init=0)),
    "d16":          dict(depth=16),
    "d8":           dict(depth=8),
    "d2":           dict(pattern="noise", depth=2),
    "word3":        dict(cfg=dict(output_word_bytes=3)),
    "word8":        dict(cfg=dict(output_word_bytes=8)),
    "raw":          dict(raw=True, cfg=dict(output_word_bytes=5)),
    "max_dims":     dict(dims=(NX_MAX, 2, NZ_MAX), scenes=1),
    "min_bands":    dict(dims=(24, 5, 16)),
    "one_column":   dict(dims=(1, 12, 20), cfg=dict(local_sum="column_wide", full_prediction=False)),
    "one_row":      dict(dims=(40, 1, 20)),
    # corner-case images (VER-03); random, incompressible ones are the noise tests
    "all_zero":     dict(pattern="zero", cfg=dict(bands=15)),
    "all_max":      dict(pattern="full_scale", cfg=dict(bands=15)),
    "all_max_d16":  dict(pattern="full_scale", depth=16, cfg=dict(omega=19, u_max=32)),
}

# VER-02: local sum (4) x prediction mode (2) x P x D x omega x U_max, one scene each
LS_CODE = {"neighbor_wide": "nw", "neighbor_narrow": "nn", "column_wide": "cw", "column_narrow": "cn"}
MATRIX = {
    f"mx_{LS_CODE[ls]}{'f' if full else 'r'}_p{p}_d{d}_o{om}_u{u}":
        dict(depth=d, scenes=1, cfg=dict(local_sum=ls, full_prediction=full, bands=p, omega=om, u_max=u))
    for ls, full, p, d, om, u in itertools.product(
        c.LOCAL_SUMS, (True, False), (0, 1, 3, 15), (8, 12, 16), (4, 13, 19), (8, 18, 32))
}


def matrix_selection(out):
    """the matrix tests of this run (HPDP_C123_MATRIX, HPDP_C123_SEED)"""
    want = os.environ.get("HPDP_C123_MATRIX", "6").strip().lower()
    seed = int(os.environ.get("HPDP_C123_SEED", str(int(time.time()) % 1000000)))
    names = list(MATRIX)
    if want != "all":
        names = sorted(random.Random(seed).sample(names, min(int(want), len(names))))
    print(f"VER-02 matrix: {len(names)} of {len(MATRIX)} configurations, seed {seed}"
          + ("" if want == "all" else f" (again: HPDP_C123_SEED={seed})"))
    out.mkdir(parents=True, exist_ok=True)
    (out / "matrix.txt").write_text(f"seed {seed}\n" + "".join(f"{n}\n" for n in names))
    return names


def raw_image(scene, word_bytes):
    bw = BitWriter()
    for s in scene.in_order(f.ORDER_BIP):
        bw.write(s, scene.depth)
    bw.pad_to_bytes(word_bytes)
    return bw.getvalue()


def write_test(out, name, spec):
    t = {**DEFAULT, **spec}
    nx, ny, nz = t["dims"]
    cfg = c.Config(**t["cfg"])
    ctx = c._Ctx.build(nx, ny, nz, t["depth"], cfg)      # resolves R and K
    samples, expected, deltas, sizes = [], bytearray(), [], []
    for k in range(t["scenes"]):
        scene = synth.PATTERNS[t["pattern"]](nx, ny, nz, t["depth"], seed=100 * k + 7)
        samples += list(scene.in_order(f.ORDER_BIP))
        trace = []
        image = c.compress(scene, cfg, trace=trace)
        if c.decompress(image).samples != scene.samples:
            raise RuntimeError(f"{name}: model round trip failed")
        image = raw_image(scene, cfg.output_word_bytes) if t["raw"] else image
        expected += image
        sizes.append(len(image))
        deltas += [tr[7] for tr in trace]
    d = out / name
    d.mkdir(parents=True, exist_ok=True)
    fields = [nx, ny, nz, t["depth"], cfg.bands, int(cfg.full_prediction), c.LOCAL_SUMS.index(cfg.local_sum),
              cfg.omega, ctx.register_size, cfg.v_min, cfg.v_max, cfg.t_inc_log2, cfg.u_max, cfg.gamma0,
              cfg.gamma_star, ctx.k_init, cfg.output_word_bytes, int(t["raw"]), cfg.user_data,
              t["scenes"], t["in_pct"], t["out_pct"]]
    (d / "cfg.txt").write_text(" ".join(str(v) for v in fields) + "\n")
    (d / "input.hex").write_text("".join(f"{s:04x}\n" for s in samples))
    (d / "expected.hex").write_text("".join(f"{b:02x}\n" for b in expected))
    (d / "delta.hex").write_text("".join(f"{v:04x}\n" for v in deltas))
    (d / "sizes.txt").write_text("".join(f"{s}\n" for s in sizes))
    return len(samples), len(expected)


def main():
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "build/sim/c123")
    names = sys.argv[2:] or list(TESTS) + matrix_selection(out)
    for name in names:
        n, b = write_test(out, name, {**TESTS, **MATRIX}[name])
        print(f"{name:14} {n:6} samples -> {b:6} bytes")
    (out / "tests.txt").write_text("\n".join(names) + "\n")


if __name__ == "__main__":
    main()
