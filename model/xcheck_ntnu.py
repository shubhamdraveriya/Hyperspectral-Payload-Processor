#!/usr/bin/env python3
"""Cross-check against an independent CCSDS 123.0-B-2 implementation (SPEC CMP-02, MDL-02, O-5).

    python model/xcheck_ntnu.py --hlm <clone> --python <its venv's python> [--matrix all|N]
                                [--board RUN ...] [--jobs 8]

The independent implementation is the NTNU SmallSat Lab's Issue 2 verification model
(github.com/NTNU-SmallSat-Lab/ccsds123_issue_2_verification_model), which its authors
verified against the CCSDS test vector set. It is a compressor: given an image and the
header our model wrote, its output (header and body) must be byte-identical to ours.
CCSDS 123 coding is fully determined by the image and the header, so identical bytes
mean that any conforming decoder decodes our output bit-exactly.

Cases: the named tests and the VER-02 matrix of tb/ccsds123/gen_vectors.py (the same
scenes as the RTL test vectors; the RAW-mode test is not CCSDS 123 and is left out),
and for each --board run (a run_m5.py / run_m4.py work folder) every scene: the board's
compressed bytes from out.bin against the NTNU output for the scene's reference input
(ref_NNNN.hpdp).

The NTNU model always writes to <clone>/output, so each worker runs in a copy of the
clone (under build/ext/xcheck_work). Its file-name parser reads the image size from the
name and cannot take an "x" elsewhere in it, so every image is called "img-...". Its
code runs unchanged, except that numpy.savetxt is a no-op: the CSV files of
intermediate values would be gigabytes for a real scene; the bitstream and the header
are written by other code.
"""

import argparse
import os
import shutil
import struct
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "model"))
sys.path.insert(0, str(REPO / "tb" / "ccsds123"))
sys.path.insert(0, str(REPO / "sw" / "host"))

from hpdp_model import ccsds123 as c  # noqa: E402
from hpdp_model import formats as f  # noqa: E402
from hpdp_model import synth  # noqa: E402
import gen_vectors as gv  # noqa: E402

WORK = REPO / "build" / "ext" / "xcheck_work"
ENTRY = "ccsds123_0_b_2_high_level_model.py"
# runs the NTNU entry script (argv[1]) without its CSV dumps; the working folder (only
# the image and the header) is taken off sys.path, the script's own folder put on it
WRAPPER = ("import os, runpy, sys, numpy; numpy.savetxt = lambda *a, **k: None; "
           "sys.argv = sys.argv[1:]; sys.path[:] = [p for p in sys.path if p not in ('', '.')]; "
           "sys.path.insert(0, os.path.dirname(os.path.abspath(sys.argv[0]))); "
           "runpy.run_path(sys.argv[0], run_name='__main__')")


def bsq_u16be(scene):
    """The scene as the NTNU model reads it: BSQ, 16-bit big-endian."""
    nx, ny, nz = scene.nx, scene.ny, scene.nz
    out = bytearray(2 * nx * ny * nz)
    i = 0
    for z in range(nz):
        for y in range(ny):
            for x in range(nx):
                struct.pack_into(">H", out, i, scene.samples[f.index(f.ORDER_BIL, nx, ny, nz, x, y, z)])
                i += 2
    return bytes(out)


def vector_cases(matrix):
    """(name, scene, our compressed bytes) for the gen_vectors tests."""
    names = [n for n in gv.TESTS if not {**gv.DEFAULT, **gv.TESTS[n]}["raw"]]
    mx = list(gv.MATRIX)
    if matrix != "all":
        mx = mx[:: max(1, len(mx) // int(matrix))][: int(matrix)]
    for name in names + mx:
        t = {**gv.DEFAULT, **(gv.TESTS[name] if name in gv.TESTS else gv.MATRIX[name])}
        nx, ny, nz = t["dims"]
        cfg = c.Config(**t["cfg"])
        for k in range(t["scenes"]):
            scene = synth.PATTERNS[t["pattern"]](nx, ny, nz, t["depth"], seed=100 * k + 7)
            yield f"{name}#{k}", scene, c.compress(scene, cfg)


def board_cases(run):
    """(name, reference scene, the board's compressed bytes) for the scenes of a board run."""
    from hpdp_job import MAX_SCENES
    work = Path(run)
    # any result version: the scene offset and size tables end the block (sw/host/hpdp_job.py),
    # scenes_done is its sixth word in every version
    data = (work / "result.bin").read_bytes()
    words = struct.unpack(f"<{len(data) // 4}I", data)
    offsets, sizes = words[-2 * MAX_SCENES:-MAX_SCENES], words[-MAX_SCENES:]
    out = (work / "out.bin").read_bytes()
    for k in range(words[5]):
        scene = f.read_scene(work / f"ref_{k:04d}.hpdp")
        yield f"{work.name}#{k}", scene, out[offsets[k]:offsets[k] + sizes[k]]


def run_ntnu(python, clone, name, scene, ours):
    """Compress with the NTNU model, using our header; returns (verdict, detail)."""
    hdr_len = c.parse_header(ours)[5]
    w = WORK / f"w{os.getpid()}_{abs(hash(name)) % 10**8}"
    shutil.rmtree(w, ignore_errors=True)
    shutil.copytree(clone, w / "hlm", ignore=shutil.ignore_patterns(".git", "output"))
    (w / "hlm" / "output").mkdir()
    img = f"img-u16be-{scene.nz}x{scene.ny}x{scene.nx}.raw"
    (w / img).write_bytes(bsq_u16be(scene))
    (w / "hdr.bin").write_bytes(ours[:hdr_len])
    try:
        p = subprocess.run([python, "-E", "-s", "-c", WRAPPER, str(w / "hlm" / ENTRY), img, "--header", "hdr.bin"],
                           cwd=w, capture_output=True, text=True, errors="replace")
        if p.returncode:
            last = (p.stderr.strip().splitlines() or ["?"])[-1]
            return "REFUSED", last
        theirs = (w / "hlm" / "output" / "z-output-bitstream.bin").read_bytes()
        hdr_same = (w / "hlm" / "output" / "header.bin").read_bytes() == ours[:hdr_len]
        cost = "; ".join(l.strip() for l in p.stdout.splitlines() if l.startswith(("Done!", "Memory")))
        if theirs == ours:
            return "IDENTICAL", f"{len(ours)} bytes" + ("" if hdr_same else ", but header.bin differs") + \
                (f" ({cost})" if scene.nx * scene.ny * scene.nz > 10**6 else "")
        first = next((i for i, (a, b) in enumerate(zip(theirs, ours)) if a != b), min(len(theirs), len(ours)))
        return "DIFFERENT", f"ours {len(ours)} bytes, NTNU {len(theirs)}, first difference at byte {first}"
    finally:
        shutil.rmtree(w, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--hlm", required=True, help="the clone of ccsds123_issue_2_verification_model")
    ap.add_argument("--python", required=True, help="a Python with numpy, bitarray and psutil")
    ap.add_argument("--matrix", default="all", help="VER-02 matrix configurations: all or a number")
    ap.add_argument("--board", nargs="*", default=[], help="board run folders (build/runs/<name>)")
    ap.add_argument("--no-vectors", action="store_true", help="board runs only")
    ap.add_argument("--jobs", type=int, default=os.cpu_count() or 4)
    a = ap.parse_args()

    cases = [] if a.no_vectors else list(vector_cases(a.matrix))
    for run in a.board:
        cases += list(board_cases(run))
    WORK.mkdir(parents=True, exist_ok=True)
    print(f"{len(cases)} images through the NTNU model ({a.jobs} at a time)")
    tally = {}
    python, clone = str(Path(a.python).absolute()), Path(a.hlm).absolute()   # workers run elsewhere
    with ThreadPoolExecutor(a.jobs) as pool:
        futures = [(n, pool.submit(run_ntnu, python, clone, n, s, o)) for n, s, o in cases]
        for n, fut in futures:
            verdict, detail = fut.result()
            tally[verdict] = tally.get(verdict, 0) + 1
            if verdict != "IDENTICAL" or not n.startswith("mx_"):
                print(f"  {n}: {verdict}, {detail}", flush=True)
    print(", ".join(f"{v} {k}" for k, v in sorted(tally.items())))
    ok = set(tally) == {"IDENTICAL"}
    print(("PASS" if ok else "FAIL") + f": {tally.get('IDENTICAL', 0)} of {len(cases)} images byte-identical "
          "with the NTNU CCSDS 123.0-B-2 model")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
