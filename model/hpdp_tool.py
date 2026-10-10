#!/usr/bin/env python3
"""Command-line front end for the HPDP reference models.

    python model/hpdp_tool.py gen hyperspectral 64 32 50 scene.hpdp [--cal scene.cal --raw raw.hpdp]
    python model/hpdp_tool.py compress scene.hpdp scene.c123 [--bands 3 --omega 13 ...]
    python model/hpdp_tool.py decompress scene.c123 back.hpdp
    python model/hpdp_tool.py downlink raw.hpdp [raw2.hpdp ...] --cal scene.cal -o stream.cadu [--ber 1e-4]
    python model/hpdp_tool.py ground stream.cadu outdir
"""

import argparse
import sys
import time
from pathlib import Path

from hpdp_model import ccsds123, channel, formats, pipeline, radiometric, synth


def cmd_gen(a):
    scene = synth.PATTERNS[a.pattern](a.nx, a.ny, a.nz, a.depth, seed=a.seed)
    formats.write_scene(a.out, scene)
    print(f"wrote {a.out}: {a.nx} x {a.ny} x {a.nz}, D = {a.depth}")
    if a.cal:
        cal = radiometric.synthetic_calibration(a.nx, a.nz, seed=a.seed)
        formats.write_cal(a.cal, cal)
        print(f"wrote {a.cal}: {sum(cal.bad)} bad pixels")
        if a.raw:
            formats.write_scene(a.raw, radiometric.make_raw(scene, cal, seed=a.seed))
            print(f"wrote {a.raw}: raw detector counts for that calibration")


def config_from(a):
    return ccsds123.Config(bands=a.bands, full_prediction=not a.reduced, local_sum=a.local_sum,
                           omega=a.omega, v_min=a.v_min, v_max=a.v_max, t_inc_log2=a.t_inc_log2,
                           u_max=a.u_max, gamma0=a.gamma0, gamma_star=a.gamma_star, k_init=a.k_init,
                           order=a.order, interleave=a.interleave)


def cmd_compress(a):
    scene = formats.read_scene(a.input)
    t0 = time.time()
    data = ccsds123.compress(scene, config_from(a))
    Path(a.out).write_bytes(data)
    raw = scene.depth * len(scene.samples) / 8
    print(f"{len(scene.samples)} samples -> {len(data)} bytes, ratio {raw / len(data):.3f}, "
          f"{8 * len(data) / len(scene.samples):.3f} bits/sample, {time.time() - t0:.1f} s")


def cmd_decompress(a):
    scene = ccsds123.decompress(Path(a.input).read_bytes())
    formats.write_scene(a.out, scene)
    print(f"wrote {a.out}: {scene.nx} x {scene.ny} x {scene.nz}, D = {scene.depth}")


def cmd_downlink(a):
    raws = [formats.read_scene(p) for p in a.inputs]
    cal = formats.read_cal(a.cal) if a.cal else None
    stream, _, _ = pipeline.satellite(raws, cal, config_from(a))
    if a.ber:
        stream, flips = channel.inject_bit_errors(stream, a.ber, seed=a.seed)
        print(f"injected {flips} bit errors")
    Path(a.out).write_bytes(stream)
    print(f"wrote {a.out}: {len(stream) // channel.CADU_BYTES} CADUs")


def cmd_ground(a):
    results, st = pipeline.ground(Path(a.input).read_bytes())
    out = Path(a.outdir)
    out.mkdir(parents=True, exist_ok=True)
    for sid, scene in results:
        if scene is not None:
            formats.write_scene(out / f"scene_{sid:05d}.hpdp", scene)
    print(f"CADUs {st.cadus}, RS corrected symbols {st.rs_corrected_symbols}, "
          f"uncorrectable {st.rs_uncorrectable}, lost frames {st.lost_frames}, idle frames {st.idle_frames}")
    for sid, ok, why in st.scenes:
        print(f"  scene {sid}: {'ok' if ok else 'FAILED ' + why}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("gen", help="generate a synthetic scene")
    p.add_argument("pattern", choices=sorted(synth.PATTERNS))
    p.add_argument("nx", type=int)
    p.add_argument("ny", type=int)
    p.add_argument("nz", type=int)
    p.add_argument("out")
    p.add_argument("--depth", type=int, default=12)
    p.add_argument("--seed", type=int, default=1)
    p.add_argument("--cal", help="also write a synthetic calibration table")
    p.add_argument("--raw", help="also write raw detector counts (needs --cal)")
    p.set_defaults(func=cmd_gen)

    def coding(p):
        d = ccsds123.Config()
        p.add_argument("--bands", type=int, default=d.bands)
        p.add_argument("--reduced", action="store_true")
        p.add_argument("--local-sum", default=d.local_sum, choices=ccsds123.LOCAL_SUMS)
        p.add_argument("--omega", type=int, default=d.omega)
        p.add_argument("--v-min", type=int, default=d.v_min)
        p.add_argument("--v-max", type=int, default=d.v_max)
        p.add_argument("--t-inc-log2", type=int, default=d.t_inc_log2)
        p.add_argument("--u-max", type=int, default=d.u_max)
        p.add_argument("--gamma0", type=int, default=d.gamma0)
        p.add_argument("--gamma-star", type=int, default=d.gamma_star)
        p.add_argument("--k-init", type=int, default=d.k_init)
        p.add_argument("--order", default=d.order, choices=("bi", "bsq"))
        p.add_argument("--interleave", type=int, default=d.interleave)

    p = sub.add_parser("compress", help="CCSDS 123 compress a scene")
    p.add_argument("input")
    p.add_argument("out")
    coding(p)
    p.set_defaults(func=cmd_compress)

    p = sub.add_parser("decompress", help="decompress a CCSDS 123 image")
    p.add_argument("input")
    p.add_argument("out")
    p.set_defaults(func=cmd_decompress)

    p = sub.add_parser("downlink", help="raw scenes -> CADU stream (whole satellite chain)")
    p.add_argument("inputs", nargs="+")
    p.add_argument("-o", "--out", required=True)
    p.add_argument("--cal")
    p.add_argument("--ber", type=float, default=0.0)
    p.add_argument("--seed", type=int, default=1)
    coding(p)
    p.set_defaults(func=cmd_downlink)

    p = sub.add_parser("ground", help="CADU stream -> recovered scenes")
    p.add_argument("input")
    p.add_argument("outdir")
    p.set_defaults(func=cmd_ground)

    a = ap.parse_args(argv)
    a.func(a)


if __name__ == "__main__":
    sys.exit(main())
