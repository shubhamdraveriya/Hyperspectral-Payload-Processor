"""Public hyperspectral datasets for the compression experiments (SPEC 11, test
data; O-7), kept in data/hsi/ (git-ignored; sources, licences and checksums in
docs/real_data.md).

    python model/hpdp_datasets.py list                   # the scenes of each dataset
    python model/hpdp_datasets.py experiment [-j N]      # results -> build/real_data_results.md
    python model/hpdp_datasets.py export SCENE [OUT] [--lines N]   # -> .hpdp (BIL): SD card, run_m5 --scene

indian_pines  AVIRIS Indian Pines Site 3, 145 x 145 x 220 (Purdue University Research
              Repository, doi:10.4231/R7RX991C, CC0): a baseline TIFF, one plane per band
aviris        49 AVIRIS calibrated (L1) scenes, 512 x 512 x 224, flights 2008 - 2017
              (GICI, Universitat Autonoma de Barcelona): a ZIP of raw files
hyperion      14 Hyperion (EO-1, L1) scenes, 256 x 1024 x 242, 2017 (GICI): a ZIP of raw files

GICI raw files are named <scene>.<bands>_<width>_<height>_<type>_<byte order>_<RGB>.raw
(the sets' README; width checked against the data): band-sequential; type 2
unsigned or 3 signed 16-bit; byte order 0 big- or 1 little-endian. Scenes are read from the ZIPs, not extracted. Scene names:
indian_pines, aviris/<scene>, hyperion/<scene>.

The experiment compresses every scene with the default settings (and checks the
round trip), one typical scene per dataset (median deflate size) with the other
settings and in shorter scenes (O-7). Results are kept per task in
build/real_data_results.jsonl, so an interrupted run continues where it stopped.
"""

import argparse
import json
import re
import struct
import sys
import time
import zipfile
import zlib
from array import array
from multiprocessing import Pool
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from hpdp_model import ccsds123 as c  # noqa: E402
from hpdp_model import formats as f  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
DATA = REPO / "data" / "hsi"
GICI = "GICI, Universitat Autonoma de Barcelona: https://gici.uab.cat/GiciWebPage/datasets.php"
DATASETS = {
    "indian_pines": dict(
        file=DATA / "indian_pines" / "19920612_AVIRIS_IndianPine_Site3.tif",
        source="Purdue University Research Repository, doi:10.4231/R7RX991C (CC0): "
               "https://purr.purdue.edu/publications/1947/1",
        sensor="AVIRIS (airborne), 1992, 20 m, 400 - 2500 nm", lines=145),
    "aviris": dict(
        file=DATA / "gici_aviris" / "AVIRIS_test.zip",
        source=GICI + " (AVIRIS test set; data NASA/JPL)",
        sensor="AVIRIS (airborne), calibrated radiance (L1), 2008 - 2017", lines=512),
    "hyperion": dict(
        file=DATA / "gici_hyperion" / "Hyperion_test.zip",
        source=GICI + " (Hyperion test set; data NASA/USGS)",
        sensor="Hyperion (EO-1, spaceborne), L1, 2017, 30 m", lines=1024),
}
GICI_NAME = re.compile(r"(?P<scene>[^/]+)\.(?P<nz>\d+)_(?P<nx>\d+)_(?P<ny>\d+)_(?P<type>[23])_(?P<order>[01])_0\.raw$")

SETTINGS = [("default (P 3, full, neighbour-wide, omega 13)", {}),
            ("P = 0 (spatial only)", dict(bands=0)),
            ("P = 15", dict(bands=15)),
            ("reduced prediction", dict(full_prediction=False)),
            ("neighbour-narrow local sum", dict(local_sum="neighbor_narrow")),
            ("column-wide local sum", dict(local_sum="column_wide")),
            ("column-narrow local sum", dict(local_sum="column_narrow")),
            ("omega 19, v_max 9", dict(omega=19, v_max=9))]
DEFAULT = SETTINGS[0][0]
SCENE_LINES = (16, 32, 64, 128, 256, 512)       # O-7: scene lengths compared (default 256)


def read_tiff_planar(path):
    """(nx, ny, nz, signed, planes): an uncompressed little-endian baseline TIFF
    with one 16-bit plane per band (PlanarConfiguration 2), as the AVIRIS files"""
    b = Path(path).read_bytes()
    if b[:4] != b"II*\x00":
        raise ValueError(f"{path}: not a little-endian TIFF")
    off = struct.unpack_from("<I", b, 4)[0]
    tags = {}
    for i in range(struct.unpack_from("<H", b, off)[0]):
        tag, typ, cnt, val = struct.unpack_from("<HHII", b, off + 2 + 12 * i)
        size = {3: 2, 4: 4}.get(typ, 0)
        if size and cnt * size > 4:
            fmt = "<%d%s" % (cnt, "H" if typ == 3 else "I")
            tags[tag] = list(struct.unpack_from(fmt, b, val))
        else:
            tags[tag] = [val & 0xFFFF if typ == 3 else val][:cnt]
    nx, ny, nz = tags[256][0], tags[257][0], tags[277][0]
    if tags.get(259, [1])[0] != 1 or tags.get(284, [1])[0] != 2 or set(tags[258]) != {16}:
        raise ValueError(f"{path}: needs uncompressed 16-bit planes (one per band)")
    signed = 2 in tags.get(339, [1])
    offsets, counts = tags[273], tags[279]
    if len(offsets) != nz or any(n != 2 * nx * ny for n in counts):
        raise ValueError(f"{path}: expected one strip of Nx*Ny samples per band")
    planes = [array("h" if signed else "H", b[o:o + 2 * nx * ny]) for o in offsets]
    return nx, ny, nz, signed, planes


def to_scene(nx, ny, nz, planes):
    """BIL scene from band planes; negative samples (signed data) clipped to 0,
    D from the maximum. Returns (scene, clipped count)."""
    clipped = 0
    s = array("H")
    for y in range(ny):
        for p in planes:
            row = p[y * nx:(y + 1) * nx]
            if row.typecode == "h" and min(row) < 0:
                clipped += sum(1 for v in row if v < 0)
                s.extend(v if v > 0 else 0 for v in row)
            else:
                s.frombytes(row.tobytes())      # non-negative int16: the same bytes as uint16
    depth = max(2, int(max(max(p) for p in planes)).bit_length())
    return f.Scene(nx, ny, nz, depth, s), clipped


def scenes(name):
    """[(scene name, deflate bytes or None)] of a dataset; [] if not downloaded"""
    path = DATASETS[name]["file"]
    if not path.exists():
        return []
    if name == "indian_pines":
        return [(name, None)]
    try:
        with zipfile.ZipFile(path) as z:
            return sorted((f"{name}/{m['scene']}", i.compress_size)
                          for i in z.infolist() if (m := GICI_NAME.search(i.filename)))
    except zipfile.BadZipFile:
        print(f"{name}: {path.name} incomplete (still downloading?)")
        return []


def load(scene_name):
    """(BIL scene, clipped samples, deflate bits per sample of the band planes)"""
    name, _, member = scene_name.partition("/")
    path = DATASETS[name]["file"]
    if name == "indian_pines":
        nx, ny, nz, signed, planes = read_tiff_planar(path)
        deflate = len(zlib.compress(b"".join(p.tobytes() for p in planes), 6))
    else:
        with zipfile.ZipFile(path) as z:
            for info in z.infolist():
                m = GICI_NAME.search(info.filename)
                if m and m["scene"] == member:
                    break
            else:
                raise KeyError(f"{scene_name}: no such scene in {path.name}")
            nx, ny, nz = int(m["nx"]), int(m["ny"]), int(m["nz"])
            signed = m["type"] == "3"
            a = array("h" if signed else "H")
            a.frombytes(z.read(info))
            if (m["order"] == "0") != (sys.byteorder == "big"):
                a.byteswap()
            planes = [a[k * nx * ny:(k + 1) * nx * ny] for k in range(nz)]
            deflate = info.compress_size
    scene, clipped = to_scene(nx, ny, nz, planes)
    return scene, clipped, 8 * deflate / (nx * ny * nz)


def sub_scene(scene, y0, ny):
    """lines y0 .. y0+ny-1 of a BIL scene"""
    line = scene.nx * scene.nz
    return f.Scene(scene.nx, ny, scene.nz, scene.depth, scene.samples[y0 * line:(y0 + ny) * line])


def run_task(task):
    """One experiment task: (scene, setting label, lines per scene or 0, verify)"""
    scene_name, label, lines, verify = task
    t0 = time.time()
    scene, clipped, deflate = load(scene_name)
    cfg = c.Config(**dict(SETTINGS)[label])
    bits, ok = 0, None
    if lines:
        # each scene of `lines` lines compressed on its own, header included
        n = scene.ny // lines
        for k in range(n):
            bits += 8 * len(c.compress(sub_scene(scene, k * lines, lines), cfg))
        samples = n * lines * scene.nx * scene.nz
    else:
        data = c.compress(scene, cfg)
        bits, samples = 8 * len(data), len(scene.samples)
        if verify:
            ok = c.decompress(data).samples == scene.samples
    return dict(scene=scene_name, setting=label, lines=lines or scene.ny, whole=not lines,
                nx=scene.nx, ny=scene.ny, nz=scene.nz, depth=scene.depth, clipped=clipped,
                bits=bits, samples=samples, bps=bits / samples, deflate_bps=deflate,
                round_trip=ok, seconds=round(time.time() - t0))


def task_key(scene_name, label, lines):
    return f"{scene_name}|{label}|{lines}"


def plan():
    """Experiment tasks, the typical scene's first: (scene, setting, lines or 0, verify)"""
    first, rest = [], []
    for name in DATASETS:
        sc = scenes(name)
        if not sc:
            print(f"{name}: {DATASETS[name]['file']} missing (docs/real_data.md)")
            continue
        typical = sorted(sc, key=lambda s: s[1] or 0)[len(sc) // 2][0]
        first += [(typical, label, 0, False) for label, _ in SETTINGS[1:]]
        first += [(typical, DEFAULT, n, False) for n in SCENE_LINES if n < DATASETS[name]["lines"]]
        rest += [(s, DEFAULT, 0, True) for s, _ in sc]
    return first + rest


def cmd_list(_a):
    for name, d in DATASETS.items():
        sc = scenes(name)
        print(f"{name}: {len(sc)} scenes  ({d['sensor']}; {d['source']})")
        for s, deflate in sc:
            print(f"  {s}")


def cmd_experiment(a):
    store = Path(a.store)
    done = {}
    if store.exists():
        for line in store.read_text().splitlines():
            r = json.loads(line)
            done[task_key(r["scene"], r["setting"], 0 if r["whole"] else r["lines"])] = r
    tasks = [t for t in plan() if task_key(t[0], t[1], t[2]) not in done]
    print(f"{len(tasks)} tasks to run ({len(done)} done), {a.jobs} processes", flush=True)
    t0 = time.time()
    with Pool(a.jobs, maxtasksperchild=2) as pool, store.open("a") as fh:
        for k, r in enumerate(pool.imap_unordered(run_task, tasks), 1):
            fh.write(json.dumps(r) + "\n")
            fh.flush()
            done[task_key(r["scene"], r["setting"], 0 if r["whole"] else r["lines"])] = r
            what = r["setting"] if r["whole"] else f"scenes of {r['lines']} lines"
            print(f"[{k}/{len(tasks)}, {(time.time() - t0) / 60:.0f} min] {r['scene']}: {what}: "
                  f"{r['bps']:.3f} bits/sample, round trip {r['round_trip']}, {r['seconds']} s", flush=True)
    report(list(done.values()), Path(a.out))
    print(f"table: {a.out}")


def report(rows, out):
    """Markdown tables of the results"""
    def mean(rs, key):
        return sum(r[key] * r["samples"] for r in rs) / sum(r["samples"] for r in rs)

    whole = [r for r in rows if r["whole"] and r["setting"] == DEFAULT]
    md = ["# Real-data compression results", "",
          "Generated by `model/hpdp_datasets.py experiment`. Lossless CCSDS 123.0-B-2 with the HPDP "
          "compressor's settings (the model is bit-exact with the RTL, VER-02). Bits per sample of "
          "the compressed scene, header included; ratio: D / bits per sample (D from the scene's "
          "largest sample); deflate: the same samples, band-sequential, deflated (ZIP), for reference.",
          "", "## Datasets (default settings)", "",
          "| Dataset | Scenes | Nx x Ny x Nz | D | Bits/sample: mean (min - max) | Ratio | Deflate bits/sample | Round trips |",
          "|---|---:|---|---|---|---:|---:|---|"]
    for name in DATASETS:
        rs = [r for r in whole if r["scene"].split("/")[0] == name]
        if not rs:
            continue
        dims = sorted({f"{r['nx']} x {r['ny']} x {r['nz']}" for r in rs})
        depths = sorted({r["depth"] for r in rs})
        bps = mean(rs, "bps")
        dmean = sum(r["depth"] * r["samples"] for r in rs) / sum(r["samples"] for r in rs)
        ok = sum(1 for r in rs if r["round_trip"])
        d = f"{depths[0]}" if len(depths) == 1 else f"{depths[0]} - {depths[-1]}"
        md.append(f"| {name} | {len(rs)} | {', '.join(dims)} | {d} "
                  f"| {bps:.3f} ({min(r['bps'] for r in rs):.3f} - {max(r['bps'] for r in rs):.3f}) "
                  f"| {dmean / bps:.2f}:1 | {mean(rs, 'deflate_bps'):.3f} | {ok} of {len(rs)} lossless |")

    def change(bps, ref):
        return f"{100 * (bps / ref - 1):+.1f} %" if ref else ""

    base = {r["scene"]: r["bps"] for r in whole}
    typical = sorted({r["scene"] for r in rows if r["setting"] != DEFAULT or not r["whole"]})
    md += ["", "## Settings (the typical scene of each dataset)", "",
           "| Scene | Setting | Bits/sample | Change |", "|---|---|---:|---:|"]
    for s in typical:
        for r in sorted((r for r in rows if r["scene"] == s and r["whole"]), key=lambda r: r["bps"]):
            md.append(f"| {s} | {r['setting']} | {r['bps']:.3f} | {change(r['bps'], base.get(s))} |")
    md += ["", "## Scene length (O-7: each scene compressed on its own)", "",
           "| Scene | Lines per scene | Bits/sample | Change |", "|---|---:|---:|---:|"]
    for s in typical:
        for r in sorted((r for r in rows if r["scene"] == s and r["setting"] == DEFAULT), key=lambda r: r["lines"]):
            md.append(f"| {s} | {r['lines']}{' (whole)' if r['whole'] else ''} | {r['bps']:.3f} | "
                      f"{change(r['bps'], base.get(s))} |")

    md += ["", "## Every scene (default settings)", "",
           "| Scene | Nx x Ny x Nz | D | Clipped | Bits/sample | Ratio | Deflate | Round trip |",
           "|---|---|---:|---:|---:|---:|---:|---|"]
    for r in sorted(whole, key=lambda r: r["scene"]):
        md.append(f"| {r['scene']} | {r['nx']} x {r['ny']} x {r['nz']} | {r['depth']} | {r['clipped']} | "
                  f"{r['bps']:.3f} | {r['depth'] / r['bps']:.2f}:1 | {r['deflate_bps']:.3f} | "
                  f"{'lossless' if r['round_trip'] else 'FAILED' if r['round_trip'] is False else '-'} |")
    out.write_text("\n".join(md) + "\n")


def transpose(scene):
    """the scene with its pixel and line axes swapped (a 1024-pixel line from a
    1024-line Hyperion scene, for the NX_MAX 1024 build)"""
    nx, ny, nz, s = scene.nx, scene.ny, scene.nz, scene.samples
    t = array("H")
    for x in range(nx):                 # new line x: old column x, every band
        for z in range(nz):
            t.extend(s[z * nx + x::nz * nx])
    return f.Scene(ny, nx, nz, scene.depth, t)


def cmd_export(a):
    scene, clipped, _ = load(a.scene)
    name = a.scene.replace("/", "_")
    if a.transpose:
        scene = transpose(scene)
        name += "_t"
    if a.lines:
        scene = sub_scene(scene, a.y0, min(a.lines, scene.ny - a.y0))
        name += f"_y{a.y0}_{scene.ny}"
    out = Path(a.out) if a.out else DATA / "export" / (name + ".hpdp")
    out.parent.mkdir(parents=True, exist_ok=True)
    f.write_scene(out, scene)
    print(f"{a.scene}: {scene.nx} x {scene.ny} x {scene.nz}, D = {scene.depth}, "
          f"{clipped} negative samples clipped -> {out}")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list").set_defaults(func=cmd_list)
    e = sub.add_parser("experiment")
    e.add_argument("-j", "--jobs", type=int, default=4, help="processes")
    e.add_argument("--store", default=str(REPO / "build" / "real_data_results.jsonl"))
    e.add_argument("--out", default=str(REPO / "build" / "real_data_results.md"))
    e.set_defaults(func=cmd_experiment)
    x = sub.add_parser("export")
    x.add_argument("scene")
    x.add_argument("out", nargs="?")
    x.add_argument("--lines", type=int, default=0, help="only N lines (a board run in minutes)")
    x.add_argument("--y0", type=int, default=0, help="first line")
    x.add_argument("--transpose", action="store_true", help="swap pixels and lines (before --lines)")
    x.set_defaults(func=cmd_export)
    a = p.parse_args()
    a.func(a)


if __name__ == "__main__":
    main()
