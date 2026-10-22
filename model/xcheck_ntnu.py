#!/usr/bin/env python3
"""Cross-check of the CCSDS 123 model against an independent implementation (SPEC O-5,
CMP-02, MDL-02).

The NTNU SmallSat Lab's CCSDS 123.0-B-2 high-level model (Python, MIT per its README;
checked by its authors against the CCSDS test vectors Test1-20190201 and the CNES tool;
https://github.com/NTNU-SmallSat-Lab/ccsds123_issue_2_verification_model) compresses
each scene with the settings it reads from our header. Its output must equal ours,
header and body, byte for byte. The synthetic scenes are those of
tb/ccsds123/gen_vectors.py, which the RTL matches bit for bit, so a match covers the
RTL vectors too.

    git clone https://github.com/NTNU-SmallSat-Lab/ccsds123_issue_2_verification_model <hlm>
    pip install numpy bitarray
    python model/xcheck_ntnu.py --hlm <hlm> [-j 4] [named | matrix | extra | all | test ...]
    python model/xcheck_ntnu.py --hlm <hlm> --real indian_pines [--crop X0 Y0 NX NY]

named: the tests of gen_vectors.py except raw (not CCSDS 123 output); matrix: the 864
configurations of VER-02; extra: BSQ and BI with M < Nz (model and ground station only;
the RTL codes BIP) and Nx = 1 in reduced mode. --real: a crop (all bands) of a scene of model/hpdp_datasets.py,
with the real-data experiment's settings. --hlm defaults to HPDP_NTNU_HLM. Each run's
files are in --work (default build/xcheck_ntnu), deleted for matching runs unless
--keep; a mismatch reports the first differing byte and the first mapped prediction
residual that differs (from the NTNU model's predictor-14 CSV).
"""

import argparse
import os
import shutil
import subprocess
import sys
import time
from array import array
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "model"))
sys.path.insert(0, str(REPO / "tb" / "ccsds123"))

import gen_vectors as gv  # noqa: E402
from hpdp_model import ccsds123 as c  # noqa: E402
from hpdp_model import formats as f  # noqa: E402
from hpdp_model import synth  # noqa: E402

# One fresh interpreter per run: the NTNU model keeps state in class attributes. Paths
# are relative to the run directory, because the model reads the image size from the
# whole path (regex x(.+)x), so no other "x" may appear in it.
RUNNER = """
import sys
from ccsds123_i2_hlm import ccsds123
image = ccsds123.CCSDS123(sys.argv[1])
image.output_folder = sys.argv[3]
image.set_header_file(sys.argv[2])
image.compress_image()
"""

EXTRA = {
    "order_bsq": dict(cfg=dict(order="bsq")),
    "order_bil": dict(cfg=dict(order="bi", interleave=1)),
    "order_bi7": dict(cfg=dict(order="bi", interleave=7)),
    # one_column in reduced mode: the NTNU model refuses full prediction at Nx = 1 (SPEC O-9)
    "nx1_reduced_cw": dict(dims=(1, 12, 20), cfg=dict(local_sum="column_wide", full_prediction=False)),
    "nx1_reduced_cn": dict(dims=(1, 12, 20), cfg=dict(local_sum="column_narrow", full_prediction=False)),
}


def synthetic(name, spec):
    """[(run name, scene, Config, note)] of a gen_vectors test: the same scenes"""
    t = {**gv.DEFAULT, **spec}
    nx, ny, nz = t["dims"]
    cfg = c.Config(**t["cfg"])
    return [(name if t["scenes"] == 1 else f"{name}_s{k}",
             synth.PATTERNS[t["pattern"]](nx, ny, nz, t["depth"], seed=100 * k + 7), cfg, "")
            for k in range(t["scenes"])]


def crop(scene, x0, y0, nx, ny):
    """pixels x0 .. x0+nx-1 of lines y0 .. y0+ny-1 of a BIL scene, all bands"""
    if x0 + nx > scene.nx or y0 + ny > scene.ny:
        raise ValueError(f"crop outside the {scene.nx} x {scene.ny} scene")
    s = array("H")
    for y in range(y0, y0 + ny):
        for z in range(scene.nz):
            start = (y * scene.nz + z) * scene.nx + x0
            s.extend(scene.samples[start:start + nx])
    return f.Scene(nx, ny, scene.nz, scene.depth, s)


def diagnose(d, ours, theirs, scene, trace):
    n = min(len(ours), len(theirs))
    first = next((i for i in range(n) if ours[i] != theirs[i]), n)
    msg = (f"first differing byte {first} ({'header' if first < c.HEADER_BYTES else 'body'}), "
           f"sizes {len(ours)} / {len(theirs)}")
    csv = d / "ntnu" / "predictor-14-mapped_quantizer_index.csv"
    if csv.exists():
        rows = [[int(v) for v in line.split(",")] for line in csv.read_text().split()]
        for z, y, x, *_, delta, _k in trace:
            if rows[y * scene.nx + x][z] != delta:
                return msg + f"; first mapped residual that differs: z {z} y {y} x {x}, ours {delta}, NTNU {rows[y * scene.nx + x][z]}"
        msg += "; mapped residuals identical (entropy coder or packing differs)"
    return msg


def run(case, hlm, work, keep):
    """(name, status, our bytes, NTNU bytes, note)"""
    name, scene, cfg, note = case
    d = work / name
    shutil.rmtree(d, ignore_errors=True)
    (d / "ntnu").mkdir(parents=True)
    trace = []
    ours = c.compress(scene, cfg, trace=trace)
    bsq = scene.in_order(f.ORDER_BSQ)
    if sys.byteorder == "little":
        bsq.byteswap()                                  # the NTNU model reads u16be
    raw = f"img-u16be-{scene.nz}x{scene.ny}x{scene.nx}.raw"
    (d / raw).write_bytes(bsq.tobytes())
    (d / "header.bin").write_bytes(ours[:c.HEADER_BYTES])
    (d / "ours.bin").write_bytes(ours)
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(filter(None, [str(hlm), os.environ.get("PYTHONPATH")]))}
    p = subprocess.run([sys.executable, "-c", RUNNER, raw, "header.bin", "ntnu"],
                       cwd=d, env=env, capture_output=True, text=True)
    out = d / "ntnu" / "z-output-bitstream.bin"
    if p.returncode or not out.exists():
        (d / "ntnu.log").write_text(p.stdout + p.stderr)
        last = (p.stderr.strip().splitlines() or ["no output"])[-1]
        return name, "ERROR", len(ours), 0, f"{last} ({d / 'ntnu.log'})"
    theirs = out.read_bytes()
    if theirs == ours:
        if not keep:
            shutil.rmtree(d)
        return name, "match", len(ours), len(theirs), note
    return name, "MISMATCH", len(ours), len(theirs), diagnose(d, ours, theirs, scene, trace) + (f"; {note}" if note else "")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("tests", nargs="*", help="named, matrix, extra, all, or test names (default named)")
    p.add_argument("--hlm", default=os.environ.get("HPDP_NTNU_HLM"), help="the NTNU model's clone")
    p.add_argument("--work", default=str(REPO / "build" / "xcheck_ntnu"))
    p.add_argument("-j", "--jobs", type=int, default=4)
    p.add_argument("--keep", action="store_true", help="keep the files of matching runs")
    p.add_argument("--real", help="a scene of model/hpdp_datasets.py, e.g. indian_pines")
    p.add_argument("--crop", type=int, nargs=4, default=[0, 0, 32, 16], metavar=("X0", "Y0", "NX", "NY"))
    a = p.parse_args()
    hlm = Path(a.hlm or "")
    if not a.hlm or not (hlm / "ccsds123_i2_hlm").is_dir():
        p.error("--hlm (or HPDP_NTNU_HLM) must point at a clone of the NTNU model")

    named = {k: v for k, v in gv.TESTS.items() if not v.get("raw")}
    groups = {"named": named, "matrix": gv.MATRIX, "extra": EXTRA}
    groups["all"] = {**named, **gv.MATRIX, **EXTRA}
    specs = {}
    for t in a.tests or ([] if a.real else ["named"]):
        if t in groups:
            specs.update(groups[t])
        elif t in groups["all"]:
            specs[t] = groups["all"][t]
        else:
            p.error(f"unknown test {t}")
    cases = [case for name, spec in specs.items() for case in synthetic(name, spec)]
    if a.real:
        import hpdp_datasets as ds
        scene, _, _ = ds.load(a.real)
        part = crop(scene, *a.crop)
        print(f"{a.real}: {scene.nx} x {scene.ny} x {scene.nz}, D {scene.depth}; "
              f"crop x {a.crop[0]} y {a.crop[1]}, {part.nx} x {part.ny} x {part.nz}")
        cases += [(f"real_{k}", part, c.Config(**opts), label) for k, (label, opts) in enumerate(ds.SETTINGS)]

    work = Path(a.work)
    work.mkdir(parents=True, exist_ok=True)
    print(f"{len(cases)} runs, {a.jobs} at a time, NTNU model {hlm}")
    t0 = time.time()
    results = []
    with ThreadPoolExecutor(a.jobs) as pool:
        for k, r in enumerate(pool.map(lambda case: run(case, hlm, work, a.keep), cases), 1):
            results.append(r)
            if r[1] != "match":
                print(f"  {r[1]:8} {r[0]}: {r[4]}")
            elif r[4]:
                print(f"  match    {r[0]} ({r[4]}): {r[2]} bytes")
            if k % 50 == 0 or k == len(cases):
                print(f"  {k} of {len(cases)} done, {time.time() - t0:.0f} s")

    bad = [r for r in results if r[1] != "match"]
    lines = [f"{s:8} {n:28} {o:8} {t:8}  {note}" for n, s, o, t, note in results]
    (work / "report.txt").write_text("status   run                          ours     NTNU     note\n"
                                     + "\n".join(lines) + "\n")
    print(f"{len(results) - len(bad)} of {len(results)} identical to the NTNU model; "
          f"{time.time() - t0:.0f} s; report {work / 'report.txt'}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
