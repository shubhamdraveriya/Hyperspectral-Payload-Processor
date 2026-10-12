"""M4 board run: compress scenes on the ZCU106 and check them against the model.

    python sw/host/run_m4.py [options]

1. Builds the raw scenes and calibration (synthetic, or from .hpdp / .cal files)
   and computes with the Python model what the board must produce: the
   corrected scenes and their CCSDS 123 bitstreams.
2. Writes cal.bin, raw.bin and job.bin (sw/baremetal/hpdp_run/src/hpdp_job.h)
   and an XSDB script into the work directory.
3. Runs XSDB over JTAG (volatile only: the boot mode override and the
   bitstream are lost at power-off; flash is never written): programs the
   bitstream from the XSA, initializes the PS, loads the data and hpdp_run,
   waits for the result, reads the compressed scenes back.
4. Checks every scene byte for byte against the model, decodes it with the
   model decoder, compares the counters, and reports the throughput.

--dry-run stops after step 2. --verify-only re-checks a previous run.
--fake-board writes the model's own output as the board's (tests steps 2 and 4).
"""

import argparse
import math
import shutil
import struct
import subprocess
import sys
import zipfile
from array import array
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO / "model"))
sys.path.insert(0, str(HERE))

from hpdp_model import ccsds123 as c  # noqa: E402
from hpdp_model import formats as f  # noqa: E402
from hpdp_model import radiometric as rc  # noqa: E402
from hpdp_model import sensor, synth  # noqa: E402
import hpdp_regs  # noqa: E402
from hpdp_job import (CAL_ADDR, JOB_ADDR, JOB_LOAD_TABLE, MAX_SCENES, MODE_CAPTURE,  # noqa: E402
                      OUT_ADDR, RAW_ADDR, RESULT_ADDR, RESULT_MAGIC, RESULT_WORDS, pack_job,
                      pack_result, status_text, unpack_result)

SOURCES = {"dma": sensor.SOURCE_DMA, "ramp": sensor.SOURCE_RAMP, "lfsr": sensor.SOURCE_LFSR}

XSDB = Path("C:/AMDDesignTools/2025.2/Vitis/bin/xsdb.bat")


def pack_reg(reg, **values):
    """Register value from field values, using the generated field map."""
    word = 0
    for name, v in values.items():
        addr, lsb, width = hpdp_regs.FIELDS[f"{reg}.{name}"]
        word |= (v & ((1 << width) - 1)) << lsb
    return word


def compressor_regs(cfg, ctx):
    return dict(
        cmp_pred=pack_reg("cmp.PRED", bands=cfg.bands, full=int(cfg.full_prediction),
                          local_sum=c.LOCAL_SUMS.index(cfg.local_sum), omega=cfg.omega,
                          reg_size=ctx.register_size),
        cmp_wupd=pack_reg("cmp.WUPD", v_min=cfg.v_min, v_max=cfg.v_max, t_inc_log2=cfg.t_inc_log2),
        cmp_coder=pack_reg("cmp.CODER", u_max=cfg.u_max, gamma0=cfg.gamma0, gamma_star=cfg.gamma_star,
                           k_init=ctx.k_init, word_bytes=cfg.output_word_bytes),
        cmp_opts=pack_reg("cmp.OPTS", raw=0, user_data=cfg.user_data))


# ---------------------------------------------------------------------------
# Inputs and expected output
# ---------------------------------------------------------------------------
def build_inputs(a):
    if a.scene:
        raws = [f.read_scene(p) for p in a.scene]
        nx, ny, nz, depth = raws[0].nx, raws[0].ny, raws[0].nz, raws[0].depth
        if any((s.nx, s.ny, s.nz, s.depth) != (nx, ny, nz, depth) for s in raws):
            sys.exit("all scenes must have the same size and depth")
    else:
        nx, ny, nz, depth = a.size
        raws = None
    cal = f.read_cal(a.cal) if a.cal else rc.synthetic_calibration(nx, nz, seed=a.seed, bad_fraction=0.003)
    if (cal.nx, cal.nz) != (nx, nz):
        sys.exit("calibration table does not match the scene size")
    source = SOURCES[a.source]
    if raws is None:
        if source != sensor.SOURCE_DMA:
            raws = [sensor.pattern_scene(source, nx, ny, nz, depth)] * a.scenes
        else:
            raws = [rc.make_raw(synth.hyperspectral(nx, ny, nz, depth, seed=a.seed + 100 * k), cal, seed=k + 1)
                    for k in range(a.scenes)]
    return raws, cal, (nx, ny, nz, depth)


def expected_output(raws, cal, cfg, correction):
    out = []
    for raw in raws:
        fixed, stats = rc.correct(raw, cal) if correction else (raw, {"clamped": 0, "replaced": 0})
        out.append((fixed, c.compress(fixed, cfg), stats))
    return out


def write_inputs(work, raws, cal, dims, a, cfg, ctx, expect):
    nx, ny, nz, depth = dims
    work.mkdir(parents=True, exist_ok=True)
    recs = b"".join(struct.pack("<HHB3x", d, g, b & 1) for d, g, b in zip(cal.dark, cal.gain, cal.bad))
    recs += bytes(-len(recs) % 16)
    raw = array("H")
    for s in raws:
        raw += s.samples
    raw_bytes = raw.tobytes() + bytes(-2 * len(raw) % 16)
    (work / "cal.bin").write_bytes(recs)
    (work / "raw.bin").write_bytes(raw_bytes if SOURCES[a.source] == sensor.SOURCE_DMA else bytes(16))

    expected_bytes = sum(len(e[1]) for e in expect)
    capacity = (expected_bytes * 3 // 2 + 64 * len(raws) + (1 << 20) + 0xFFFF) & ~0xFFFF
    line_period = a.line_period or nx * nz + 1
    job = dict(seq=a.seq, flags=JOB_LOAD_TABLE, mode=MODE_CAPTURE,
               nx=nx, ny=ny, nz=nz, depth=depth, scenes=len(raws), line_period=line_period,
               source=SOURCES[a.source], rc_enable=int(not a.no_correction),
               cal_addr=CAL_ADDR, cal_bytes=len(recs), raw_addr=RAW_ADDR, raw_bytes=len(raw_bytes),
               out_addr=OUT_ADDR, out_capacity=capacity, **compressor_regs(cfg, ctx))
    (work / "job.bin").write_bytes(pack_job(**job))
    return job


def find_elf(a):
    if a.elf:
        return Path(a.elf)
    return Path.home() / "hpdp_vitis" / "hpdp_run" / "build" / "hpdp_run.elf"


def write_xsdb(work, job, a, run_seconds):
    """XSDB script: JTAG boot override, bitstream, PS init, data, program, result."""
    w = work.as_posix()
    xsdb_script(work, a.xsa, find_elf(a), [("cal.bin", CAL_ADDR), ("raw.bin", RAW_ADDR)], job["seq"],
                int(run_seconds * 3 + 30),
                f"mrd -bin -file {{{w}/out.bin}} 0x{OUT_ADDR:08X} {job['out_capacity'] // 4}\n",
                "run_m4")


def xsdb_script(work, xsa, elf, loads, seq, timeout, reads, tool):
    """Writes work/run.tcl: JTAG boot override, bitstream from the XSA, PS init,
    data files (name, address), job.bin and hpdp_run, wait for the result with
    this seq, read result.bin, then the TCL lines `reads`."""
    with zipfile.ZipFile(Path(xsa).resolve()) as z:
        names = z.namelist()
        bit = next(n for n in names if n.endswith(".bit"))
        init = next(n for n in names if n.endswith("psu_init.tcl"))
        for n in (bit, init):
            (work / Path(n).name).write_bytes(z.read(n))
    shutil.copy2(elf, work / "hpdp_run.elf")
    w = work.as_posix()
    data = "".join(f"dow -data {{{w}/{name}}} 0x{addr:08X}\n" for name, addr in loads)
    tcl = f"""# Generated by sw/host/{tool}.py. JTAG only: nothing here writes flash.
connect
targets -set -nocase -filter {{name =~ "PSU"}}
# boot mode JTAG until power-off, so the QSPI image does not start (docs/m0_bringup.md)
mwr 0xFF5E0200 0x0100
rst -system
after 2000
fpga -file {{{w}/{Path(bit).name}}}
targets -set -nocase -filter {{name =~ "APU*"}}
source {{{w}/psu_init.tcl}}
psu_init
after 1000
psu_ps_pl_isolation_removal
after 1000
psu_ps_pl_reset_config
catch {{psu_protection}}
targets -set -nocase -filter {{name =~ "*A53*#0"}}
rst -processor
{data}mwr 0x{RESULT_ADDR:08X} 0
dow -data {{{w}/job.bin}} 0x{JOB_ADDR:08X}
dow {{{w}/hpdp_run.elf}}
con
set t0 [clock seconds]
while {{1}} {{
    after 1000
    stop
    set magic [mrd -value 0x{RESULT_ADDR:08X}]
    set seq [mrd -value 0x{RESULT_ADDR + 8:08X}]
    if {{$magic == 0x{RESULT_MAGIC:08X} && $seq == {seq}}} break
    con
    if {{[clock seconds] - $t0 > {timeout}}} {{ error "no result after {timeout} s" }}
}}
mrd -bin -file {{{w}/result.bin}} 0x{RESULT_ADDR:08X} {RESULT_WORDS}
{reads}con
puts "{tool}: result read back"
"""
    (work / "run.tcl").write_text(tcl, encoding="utf-8", newline="\n")


def fake_board(work, job, expect, dims):
    """The model's own output, laid out as hpdp_run would write it."""
    nx, ny, nz, _ = dims
    out = bytearray(job["out_capacity"])
    offsets, sizes, off = [], [], 0
    for _, image, _ in expect:
        out[off:off + len(image)] = image
        offsets.append(off)
        sizes.append(len(image))
        off = (off + len(image) + 63) & ~63
    last = expect[-1][2]
    r = dict(magic=RESULT_MAGIC, version=3, seq=job["seq"], status=0, build_id=0,
             scenes_done=len(expect), timer_hz=100_000_000, ticks_lo=0, ticks_hi=0,
             se_lines=ny * len(expect), se_underrun=0, se_overruns=0, se_dropped=0,
             rc_clamped=last["clamped"], rc_replaced=last["replaced"], tbl_words=(nx * nz + 1) // 2,
             tbl_mismatch=0, ct_lines=ny * len(expect), ct_dropped=0, ct_unfilled=0,
             cmp_samples=nx * ny * nz * len(expect), cmp_bytes=sum(sizes), cmp_last_cycles=0,
             cmp_stalls=0)
    ticks = int(len(expect) * ny * job["line_period"] / 100e6 * r["timer_hz"])
    r["ticks_lo"], r["ticks_hi"] = ticks & 0xFFFF_FFFF, ticks >> 32
    (work / "result.bin").write_bytes(pack_result(r, offsets, sizes))
    (work / "out.bin").write_bytes(bytes(out))


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------
def verify(work, job, expect, dims, name="run_m4"):
    nx, ny, nz, depth = dims
    r, offsets, sizes = unpack_result((work / "result.bin").read_bytes())
    out = (work / "out.bin").read_bytes()
    scenes = len(expect)
    problems = []

    if r["magic"] != RESULT_MAGIC or r["seq"] != job["seq"]:
        problems.append("no result block for this job")
    if r["status"] != 0:
        problems.append(f"status {r['status']} ({status_text(r['status'])})")
    print(f"build 0x{r['build_id']:08X}, {r['scenes_done']} scenes compressed")

    for k, (fixed, image, _) in enumerate(expect):
        got = out[offsets[k]:offsets[k] + sizes[k]] if k < r["scenes_done"] else b""
        bits = 8 * len(image) / (nx * ny * nz)
        if got == image:
            ok = c.decompress(bytes(got)).samples == fixed.samples
            print(f"  scene {k}: {len(got)} bytes ({bits:.2f} bits/sample), identical to the model, "
                  f"{'decodes bit-exactly' if ok else 'DOES NOT DECODE'}")
            if not ok:
                problems.append(f"scene {k} does not decode")
        else:
            first = next((i for i, (x, y) in enumerate(zip(got, image)) if x != y), min(len(got), len(image)))
            print(f"  scene {k}: {len(got)} bytes, model {len(image)}: first difference at byte {first}")
            problems.append(f"scene {k} differs from the model")

    last = expect[-1][2]
    checks = [("emulator lines", r["se_lines"], ny * scenes), ("underruns", r["se_underrun"], 0),
              ("line period overruns", r["se_overruns"], 0), ("emulator drops", r["se_dropped"], 0),
              ("corner turn lines", r["ct_lines"], ny * scenes), ("dropped lines", r["ct_dropped"], 0),
              ("clamped (last scene)", r["rc_clamped"], last["clamped"]),
              ("replaced (last scene)", r["rc_replaced"], last["replaced"]),
              ("table words", r["tbl_words"], (nx * nz + 1) // 2), ("table readback mismatches", r["tbl_mismatch"], 0),
              ("compressor input samples", r["cmp_samples"], nx * ny * nz * scenes),
              ("compressor output bytes", r["cmp_bytes"], sum(sizes[:scenes]))]
    for what, got, exp in checks:
        if got != exp:
            problems.append(f"{what}: {got}, expected {exp}")

    ticks = r["ticks_lo"] | (r["ticks_hi"] << 32)
    if ticks and r["timer_hz"]:
        seconds = ticks / r["timer_hz"]
        samples = nx * ny * nz * scenes
        raw_bits = samples * depth
        print(f"throughput: {samples / seconds / 1e6:.1f} Msamples/s "
              f"({raw_bits / seconds / 1e6:.0f} Mbit/s raw), line period {job['line_period']} cycles "
              f"= {100e6 / job['line_period'] * nx * nz / 1e6:.1f} Msamples/s offered")
    total = sum(len(e[1]) for e in expect)
    print(f"compression: {nx * ny * nz * scenes * depth / (8 * total):.2f}:1 over {scenes} scenes")
    if r["cmp_last_cycles"]:
        print(f"compressor: last scene {r['cmp_last_cycles']} clk_sys cycles, {r['cmp_stalls']} output stall cycles")

    for p in problems:
        print(f"  problem: {p}")
    print(("PASS" if not problems else "FAIL") + f": {name}, {scenes} scenes {nx}x{ny}x{nz} D={depth}")
    return not problems


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--scene", nargs="+", help=".hpdp raw scenes (BIL), instead of synthetic ones")
    p.add_argument("--cal", help=".cal calibration table, instead of a synthetic one")
    p.add_argument("--size", nargs=4, type=int, metavar=("NX", "NY", "NZ", "D"), default=(128, 64, 64, 12),
                   help="synthetic scene size and depth (default 128 64 64 12)")
    p.add_argument("--scenes", type=int, default=4, help="synthetic scenes (default 4)")
    p.add_argument("--seed", type=int, default=1)
    p.add_argument("--source", choices=SOURCES, default="dma", help="sensor emulator source")
    p.add_argument("--no-correction", action="store_true", help="radiometric correction bypass")
    p.add_argument("--line-period", type=int, default=0, help="clk_sensor cycles per line (default Nx*Nz+1)")
    p.add_argument("--cfg", nargs="*", default=[], metavar="KEY=VALUE",
                   help="compressor settings (ccsds123.Config fields, e.g. bands=15 omega=16)")
    p.add_argument("--seq", type=int, default=1, help="job sequence number")
    p.add_argument("--work", default=str(Path.home() / "hpdp_m4"), help="work directory (no spaces)")
    p.add_argument("--xsa", default=str(REPO / "build" / "hpdp.xsa"))
    p.add_argument("--elf", help="hpdp_run.elf (default ~/hpdp_vitis/hpdp_run/build/hpdp_run.elf)")
    p.add_argument("--xsdb", default=str(XSDB))
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="write the files and the XSDB script only")
    mode.add_argument("--verify-only", action="store_true", help="check result.bin / out.bin from a previous run")
    mode.add_argument("--fake-board", action="store_true", help="use the model's output as the board's")
    a = p.parse_args()

    work = Path(a.work)
    if " " in str(work.resolve()):
        sys.exit("the work directory must not contain spaces (XSDB)")
    raws, cal, dims = build_inputs(a)
    nx, ny, nz, depth = dims
    if len(raws) > MAX_SCENES:
        sys.exit(f"at most {MAX_SCENES} scenes")
    cfg = c.Config(**{k: int(v) if v.lstrip("-").isdigit() else v
                      for k, v in (kv.split("=", 1) for kv in a.cfg)})
    ctx = c._Ctx.build(nx, ny, nz, depth, cfg)
    print(f"{len(raws)} scenes {nx}x{ny}x{nz} D={depth}, source {a.source}, "
          f"correction {'off' if a.no_correction else 'on'}: computing the expected output ...")
    expect = expected_output(raws, cal, cfg, not a.no_correction)
    job = write_inputs(work, raws, cal, dims, a, cfg, ctx, expect)
    run_seconds = len(raws) * ny * job["line_period"] / 100e6

    if a.fake_board:
        fake_board(work, job, expect, dims)
    elif not a.verify_only:
        write_xsdb(work, job, a, run_seconds)
        print(f"files and XSDB script in {work}")
        if a.dry_run:
            return 0
        print("running XSDB (JTAG; volatile configuration only) ...")
        rc_ = subprocess.call([a.xsdb, str(work / "run.tcl")])
        if rc_ != 0:
            print("FAIL: XSDB reported an error")
            return 1
    return 0 if verify(work, job, expect, dims) else 1


if __name__ == "__main__":
    sys.exit(main())
