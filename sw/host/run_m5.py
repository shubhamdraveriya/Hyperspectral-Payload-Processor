"""M5 board run: scenes down the downlink chain and the optical link, checked at the ground.

    python sw/host/run_m5.py [options]               downlink run (VER-07)
    python sw/host/run_m5.py --prbs SECONDS [...]    PRBS-31 link test (VER-06)

Downlink run:
1. Builds the scenes as run_m4.py does and computes with the model what the
   ground must recover (the corrected scenes) and what the satellite side must
   send (packets, science frames).
2. Writes cal.bin, raw.bin, job.bin (hpdp_job.py) and an XSDB script into the
   work directory.
3. Runs XSDB over JTAG (volatile only, as run_m4.py): hpdp_run brings the link
   up, runs the scenes with the compressor output going both to the capture DMA
   (unless --no-capture) and down the downlink chain, and captures the ground
   receiver's records; the script reads both back.
4. Checks the compressed scenes against the model (as run_m4.py), then runs the
   model's ground chain on the captured records: RS decoding, AOS deframing,
   packet extraction, scene reassembly, decompression. Every scene must come back
   bit-exact (VER-07, at the error rate set with --ber / --burst / --asm).

PRBS test: PRBS-31 from both transmitters, checked on the receiving channel for
SECONDS; VER-06 asks for at least 3e12 bits (about 40 minutes) without an error.

--dry-run stops after step 2. --verify-only re-checks a previous run.
--fake-board runs the model instead of the board (AOS idle frames off), to test
the host side.
"""

import argparse
import math
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO / "model"))
sys.path.insert(0, str(HERE))

from hpdp_model import ccsds123 as c  # noqa: E402
from hpdp_model import channel as ch  # noqa: E402
from hpdp_model import formats as f  # noqa: E402
from hpdp_model import framing, pipeline, rs  # noqa: E402
import run_m4 as m4  # noqa: E402
from hpdp_job import (CAL_ADDR, GR_ADDR, JOB_LOAD_TABLE, JOB_NO_GR_CAPTURE, JOB_NO_IDLE, MAX_SCENES,  # noqa: E402
                      MODE_CAPTURE, MODE_DOWNLINK, MODE_PRBS, NET_PORT, NET_UPLOAD, OUT_ADDR, RAW_ADDR,
                      RESULT_ADDR, RESULT_MAGIC, UPLOAD_STATUS, ECC_MEMORIES, ECC_NAMES, ecc_inject_word,
                      pack_job, pack_result, result_offset, status_text, unpack_result)

RECORD = 1024
CLK_SYS_HZ = 250e6
PRBS_BITS_PER_WORD = 20                 # 16-bit user word = 20 line bits (8b/10b width)
LOOPBACKS = {"none": 0, "pcs": 1, "pma": 2}


# ---------------------------------------------------------------------------
# What the satellite side must send
# ---------------------------------------------------------------------------
def cadu(frame):
    return ch.make_cadu(rs.encode_interleaved(frame, framing.RS_DEPTH))


def downlink_model(expect, ny, a):
    """(packet count, science frames) for the scenes, as the hardware builds them:
    scene IDs from 0, timestamps = lines before the scene (counted from the soft
    reset at the start of the job), each scene's last frame padded."""
    framer = framing.AosFramer(a.scid, a.vcid)
    seq, packets, frames = 0, 0, []
    for k, (_, image, _) in enumerate(expect):
        pk, seq = framing.packetize_scene(image, a.apid, k, k * ny, seq_count=seq, max_data=a.pkt_data)
        packets += len(pk)
        for p in pk:
            framer.push(p)
        frames += framer.frames(flush=True)
    return packets, frames


def idle_like(vcid_field, sci_vcid):
    """gr_capture's idle-frame decision"""
    return bin(vcid_field ^ framing.IDLE_VCID).count("1") < bin(vcid_field ^ sci_vcid).count("1")


def ei_settings(a):
    """Error injector registers: (EI.CTRL mode, threshold, burst_len, period, asm_bits)"""
    if a.ber:
        return ch.INJ_RANDOM, min(int(round(a.ber * 2**32)), 0xFFFF_FFFF), 0, 1, 0
    if a.burst:
        return ch.INJ_BURST, 0, a.burst[0], a.burst[1], 0
    if a.asm:
        return ch.INJ_ASM, 0, 0, a.asm[1], a.asm[0]
    return ch.INJ_OFF, 0, 0, 1, 0


# ---------------------------------------------------------------------------
# Job and XSDB script
# ---------------------------------------------------------------------------
def link_ctrl(a):
    return m4.pack_reg("lnk.CTRL", tx_enable=1, fabric_loopback=int(a.loopback == "fabric"),
                       rx_sel=int(a.rx == "sfp0"), gt_loopback=LOOPBACKS.get(a.loopback, 0))


def downlink_job(work, raws, cal, dims, a, cfg, ctx, expect, n_frames):
    nx, ny, nz, depth = dims
    job = m4.write_inputs(work, raws, cal, dims, a, cfg, ctx, expect)   # cal.bin, raw.bin, M4 fields
    mode, threshold, burst_len, period, asm_bits = ei_settings(a)
    # science records, plus idle frames that errors make look like science ones, plus margin
    capacity = ((n_frames + 2 * a.xfer + 256) * RECORD + (1 << 20) + 0xFFFF) & ~0xFFFF
    if OUT_ADDR + job["out_capacity"] > GR_ADDR or GR_ADDR + capacity > 0x8000_0000:
        sys.exit("buffers too large for the low 2 GB of PS DDR")
    if a.raw:
        job["cmp_opts"] = m4.pack_reg("cmp.OPTS", raw=1, user_data=cfg.user_data)
    job.update(flags=JOB_LOAD_TABLE | (JOB_NO_IDLE if a.no_idle else 0) |
               (JOB_NO_GR_CAPTURE if a.no_ground_capture else 0),
               repeat=a.repeat,
               mode=MODE_DOWNLINK | (0 if a.no_capture else MODE_CAPTURE),
               pkt_apid=a.apid, pkt_data=a.pkt_data,
               aos_frame_hdr=m4.pack_reg("aos.FRAME_HDR", scid=a.scid, vcid=a.vcid),
               aos_frame_gap=int(round(CLK_SYS_HZ / a.frame_rate)) if a.frame_rate else 0,
               ei_ctrl=mode, ei_threshold=threshold, ei_seed=a.ei_seed, ei_burst_len=burst_len,
               ei_period=period, ei_asm_bits=asm_bits,
               lnk_ctrl=link_ctrl(a), gr_sync=m4.pack_reg("gr.SYNC", asm_tol=a.asm_tol, flywheel=a.flywheel),
               gr_xfer=a.xfer, gr_addr=GR_ADDR, gr_capacity=capacity, link_timeout_ms=a.link_timeout,
               net_ctrl=NET_UPLOAD if a.upload else 0, net_ip=ip_word(a.board_ip),
               net_mask=ip_word(a.board_mask), net_port=NET_PORT)
    for i, spec in enumerate(a.ecc):
        job[f"ecc_inject{i}"] = ecc_inject_word(spec)
    (work / "job.bin").write_bytes(pack_job(**job))
    return job


def ip_word(text):
    """'a.b.c.d' -> 0xaabbccdd (0 for none)"""
    if not text:
        return 0
    parts = [int(p) for p in text.split(".")]
    if len(parts) != 4 or not all(0 <= p <= 255 for p in parts):
        sys.exit(f"bad IPv4 address: {text}")
    return (parts[0] << 24) | (parts[1] << 16) | (parts[2] << 8) | parts[3]


def ip_text(word):
    return ".".join(str(word >> s & 255) for s in (24, 16, 8, 0))


def check_upload(work, a):
    """SW-05: fetch the ground records the board serves over TCP and compare them
    with the copy read over JTAG (gr.bin)."""
    sys.path.insert(0, str(REPO / "sw" / "ground"))
    from gs_decode import tcp_records
    r, _, _ = unpack_result((work / "result.bin").read_bytes())
    print(f"upload: board at {ip_text(r['net_ip'])}:{NET_PORT}, during the run: "
          f"{UPLOAD_STATUS[r['upload_status']] if r['upload_status'] < len(UPLOAD_STATUS) else r['upload_status']}, "
          f"{r['upload_bytes']} bytes acknowledged")
    if not r["net_ip"]:
        print("  problem: the board has no network address")
        return False
    try:
        got = b"".join(tcp_records(ip_text(r["net_ip"]), NET_PORT, timeout=10))
    except OSError as exc:
        print(f"  problem: cannot fetch the records over TCP: {exc}")
        return False
    (work / "gr_tcp.bin").write_bytes(got)
    ref = (work / "gr.bin").read_bytes()[:r["gr_bytes"]]
    same = got == ref
    print(f"  fetched {len(got)} bytes over TCP: {'identical to' if same else 'DIFFERENT from'} "
          f"the {len(ref)} bytes read over JTAG")
    return same


def prbs_job(work, a):
    work.mkdir(parents=True, exist_ok=True)
    job = dict(seq=a.seq, mode=MODE_PRBS, lnk_ctrl=link_ctrl(a), prbs_seconds=a.prbs,
               link_timeout_ms=a.link_timeout)
    (work / "job.bin").write_bytes(pack_job(**job))
    return job


def write_xsdb(work, job, a, seconds):
    w = work.as_posix()
    if job["mode"] & MODE_PRBS:
        loads, reads, timeout = [], "", int(a.prbs + 120)
    else:
        loads = [("cal.bin", CAL_ADDR), ("raw.bin", RAW_ADDR)]
        timeout = int(seconds * 2 + 120)        # hpdp_run gives up at 2 x seconds + 2 s
        reads = ""
        if job["mode"] & MODE_CAPTURE:
            reads += f"mrd -force -bin -file {{{w}/out.bin}} 0x{OUT_ADDR:08X} {job['out_capacity'] // 4}\n"
        reads += (f"set gr_bytes [mrd -force -value 0x{RESULT_ADDR + result_offset('gr_bytes'):08X}]\n"
                  f"if {{$gr_bytes > 0}} {{ mrd -force -bin -file {{{w}/gr.bin}} 0x{GR_ADDR:08X} "
                  f"[expr {{($gr_bytes + 3) / 4}}] }} else {{ close [open {{{w}/gr.bin}} w] }}\n")
    m4.xsdb_script(work, a.xsa, m4.find_elf(a), loads, job["seq"], timeout, reads, "run_m5")


# ---------------------------------------------------------------------------
# Fake board: the model in place of the hardware
# ---------------------------------------------------------------------------
def fake_board(work, job, a, expect, dims, frames, packets):
    nx, ny, nz, _ = dims
    if job["mode"] & MODE_CAPTURE:
        m4.fake_board(work, job, expect, dims)              # out.bin and the M4 counters
        r, offsets, sizes = unpack_result((work / "result.bin").read_bytes())
    else:
        r, offsets, sizes = {}, [], []
    # idle frames before the scenes (as on the board, where they flow from the
    # start), the scenes' frames, then idle frames that let the frame sync
    # decide the last CADUs (error injector off by then)
    idle = framing.AosFramer(a.scid, a.vcid)
    lead = b"".join(cadu(idle.idle()) for _ in range(4))
    stream = lead + b"".join(cadu(fr) for fr in frames)
    mode, threshold, burst_len, period, asm_bits = ei_settings(a)
    injected, flips = ch.inject_hw(stream, mode, a.ei_seed, threshold, burst_len, period, asm_bits)
    tail = b"".join(cadu(idle.idle()) for _ in range(2))
    records = ch.frame_sync(injected + tail, a.asm_tol, a.flywheel)
    kept = [rec for rec in records if not idle_like(rec.codeblock[1] & 0x3F, a.vcid)]
    gr = b"".join(rec.codeblock + f.CaptureRecord.status_word(rec.asm_errors, rec.sync_state,
                                                              rec.sequence).to_bytes(4, "little")
                  for rec in kept)
    (work / "gr.bin").write_bytes(gr)
    r.update(magic=RESULT_MAGIC, version=3, seq=job["seq"], status=0, scenes_done=len(expect),
             timer_hz=100_000_000, cmp_samples=nx * ny * nz * len(expect),
             cmp_bytes=sum(len(e[1]) for e in expect), lnk_status=0xF0F,
             pkt_packets=packets, pkt_scenes=len(expect), aos_science=len(frames), aos_cadus=len(frames) + 6,
             aos_idle=6, ei_bits=flips, gr_cadus=len(records), gr_skipped=len(records) - len(kept),
             gr_captured=len(kept), gr_bytes=len(gr),
             gr_asm_errors=sum(rec.asm_errors for rec in records))
    (work / "result.bin").write_bytes(pack_result(r, offsets, sizes))


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------
def check_link(r, problems):
    st = r["lnk_status"]
    print(f"link: LNK.STATUS 0x{st:08X} (tx {st & 1}, rx {st >> 1 & 1}, aligned {st >> 2 & 1}, "
          f"locked {st >> 3 & 1}, CPLL {st >> 8 & 3:02b}, power good {st >> 10 & 3:02b}), "
          f"{r['link_tries']} RX resets during bring-up")
    if not st & 0x8:
        problems.append("link not LOCKED after bring-up")


def check_ecc(r, a, problems):
    """RAD-02 counters against the injections asked for (--ecc); True if a
    double-bit (uncorrectable) error was injected: the data is then corrupt."""
    counts = [(r["ecc_cal_sbe"], r["ecc_cal_dbe"]), (r["ecc_ct_sbe"], r["ecc_ct_dbe"]),
              (r["ecc_lb_sbe"], r["ecc_lb_dbe"])]
    print("memory ECC, corrected / uncorrectable reads: " +
          ", ".join(f"{n} {s} / {d}" for n, (s, d) in zip(("table", "corner turn", "line buffer"), counts)))
    injected = {ECC_MEMORIES[spec.split(":")[0]]: spec.endswith(":dbe") for spec in a.ecc}
    for m, (s, d) in enumerate(counts):
        if m not in injected:
            if s or d:
                problems.append(f"{ECC_NAMES[m]}: ECC errors without an injection ({s} corrected, {d} uncorrectable)")
        elif injected[m] and d == 0:
            problems.append(f"{ECC_NAMES[m]}: injected double-bit error not detected")
        elif not injected[m] and (s == 0 or d != 0):
            problems.append(f"{ECC_NAMES[m]}: injected single-bit error: {s} corrected, {d} uncorrectable reads")
    if r["ecc_pending"]:
        problems.append(f"ECC injections armed but never written (RAD.STATUS 0x{r['ecc_pending']:X})")
    return any(injected.values())


def verify_downlink(work, job, a, expect, dims, frames, packets):
    nx, ny, nz, depth = dims
    r, _, _ = unpack_result((work / "result.bin").read_bytes())
    problems = []
    if r["magic"] != RESULT_MAGIC or r["seq"] != job["seq"]:
        problems.append("no result block for this job")
    if r["status"] != 0:
        problems.append(f"status {r['status']} ({status_text(r['status'])})")
    print(f"build 0x{r['build_id']:08X}, {r['scenes_done']} scenes compressed")
    check_link(r, problems)
    corrupt = check_ecc(r, a, problems)
    if corrupt:
        print("  (uncorrectable errors injected: the data is not expected to match the model)")

    ok = True
    if job["mode"] & MODE_CAPTURE:
        print("compressed scenes (capture DMA):")
        ok = m4.verify(work, job, expect, dims, name="capture path") or corrupt

    # ground chain on the captured records
    gr = (work / "gr.bin").read_bytes() if (work / "gr.bin").exists() else b""
    gr = gr[:r["gr_bytes"]]
    recs = [gr[i:i + RECORD] for i in range(0, len(gr) - RECORD + 1, RECORD)]
    states = {0: 0, 1: 0, 2: 0, 3: 0}
    asm_errors = 0
    for rec in recs:
        status = int.from_bytes(rec[1020:1024], "little")
        asm_errors += status & 0x3F
        states[(status >> 6) & 3] += 1
    link = pipeline.LinkConfig(scid=a.scid, vcid=a.vcid, apid=a.apid, packet_data=a.pkt_data)
    results, gs = pipeline.ground_records([rec[:1020] for rec in recs], link)
    got = {sid: scene for sid, scene in results}
    print(f"ground: {len(recs)} records ({states[1]} VERIFY, {states[2]} LOCK, {states[3]} FLYWHEEL), "
          f"{asm_errors} ASM bit errors; RS: {gs.rs_corrected_symbols} symbols corrected, "
          f"{gs.rs_uncorrectable} codeblocks uncorrectable; {gs.lost_frames} frames lost, "
          f"{gs.idle_frames} idle, {gs.foreign_frames} foreign")
    for k, (fixed, image, _) in enumerate(expect):
        scene = got.get(k)
        if scene is not None and scene.samples == fixed.samples:
            print(f"  scene {k}: reconstructed bit-exactly ({len(image)} compressed bytes)")
        else:
            why = next((reason for sid, good, reason in gs.scenes if sid == k), "not received")
            print(f"  scene {k}: NOT reconstructed ({why or 'differs from the corrected scene'})")
            if not corrupt:
                problems.append(f"scene {k} not reconstructed at the ground")

    checks = [("packets", r["pkt_packets"], packets), ("packetized scenes", r["pkt_scenes"], len(expect)),
              ("science frames", r["aos_science"], len(frames)), ("timestamp overflows", r["pkt_ts_overflows"], 0),
              ("ground records dropped", r["gr_dropped"], 0), ("link RX overflows", r["lnk_rx_overflows"], 0),
              ("link lock losses", r["lnk_lock_losses"], 0),
              ("captured bytes", r["gr_bytes"], RECORD * r["gr_captured"])]
    if not (a.ber or a.burst or a.asm):
        checks += [("link code errors", r["lnk_code_errors"], 0), ("ground sync losses", r["gr_sync_losses"], 0),
                   ("RS corrections", gs.rs_corrected_symbols, 0),
                   ("captured records", r["gr_captured"], len(frames))]
    for name, got_v, exp in checks:
        if got_v != exp:
            problems.append(f"{name}: {got_v}, expected {exp}")

    bits = 8 * 1024 * max(r["aos_cadus"], 1)
    print(f"downlink: {r['pkt_packets']} packets, {r['aos_science']} science + {r['aos_idle']} idle frames, "
          f"{r['ei_bits']} bits injected (BER {r['ei_bits'] / bits:.2e} over {r['aos_cadus']} CADUs)")
    print(f"link: {r['lnk_code_errors']} code errors, {r['lnk_clk_corr']} clock corrections, "
          f"{r['lnk_buf_errors']} buffer errors, {r['lnk_tx_data']} data / {r['lnk_tx_idle']} idle words sent")
    ticks = r["downlink_ticks_lo"] | (r["downlink_ticks_hi"] << 32)
    if ticks and r["timer_hz"]:
        payload = sum(len(e[1]) for e in expect)
        print(f"ground: last record {ticks / r['timer_hz'] * 1e3:.1f} ms after the sensor start "
              f"({8 * payload / (ticks / r['timer_hz']) / 1e6:.0f} Mbit/s of compressed data end to end)")
    for p in problems:
        print(f"  problem: {p}")
    ok = ok and not problems
    errs = f", BER {a.ber:g}" if a.ber else ", burst errors" if a.burst else ", ASM errors" if a.asm else ""
    print(("PASS" if ok else "FAIL") + f": run_m5, {len(expect)} scenes {nx}x{ny}x{nz} D={depth} over the link{errs}")
    return ok


def verify_long_run(work, job, a, dims, n_scenes):
    """VER-08 / SYS-09: a long run is judged by its counters (the data itself was
    checked by the shorter runs): every line through, no overflow anywhere, the
    downlink and the ground keeping up. In RAW mode lines must be dropped instead."""
    nx, ny, nz, depth = dims
    r, _, _ = unpack_result((work / "result.bin").read_bytes())
    problems = []
    if r["magic"] != RESULT_MAGIC or r["seq"] != job["seq"]:
        problems.append("no result block for this job")
    if r["status"] != 0:
        problems.append(f"status {r['status']} ({status_text(r['status'])})")
    check_link(r, problems)
    total = n_scenes * a.repeat
    lines = total * ny
    ticks = r["ticks_lo"] | (r["ticks_hi"] << 32)
    seconds = ticks / r["timer_hz"] if r["timer_hz"] else 0
    samples = nx * ny * nz * total
    print(f"{total} scenes ({n_scenes} raw scenes streamed {a.repeat} times), {lines} lines, "
          f"{samples / 1e9:.2f} Gsamples in {seconds:.1f} s: {samples / seconds / 1e6 if seconds else 0:.1f} Msamples/s "
          f"({samples * depth / seconds / 1e6 if seconds else 0:.0f} Mbit/s raw), line period {job['line_period']} "
          f"cycles = {100e6 / job['line_period'] * nx * nz / 1e6:.1f} Msamples/s offered")
    print(f"emulator: {r['se_lines']} lines, {r['se_underrun']} underruns, {r['se_overruns']} overruns, "
          f"{r['se_dropped']} dropped samples; corner turn: {r['ct_lines']} lines, {r['ct_dropped']} dropped, "
          f"{r['ct_unfilled']} unfilled")
    print(f"downlink: {r['pkt_scenes']} scenes packetized, {r['aos_science']} science + {r['aos_idle']} idle "
          f"frames, {r['aos_cadus']} CADUs; compressor output stall cycles (cumulative, saturating) "
          f"{r['cmp_stalls']}")
    print(f"ground: {r['gr_cadus']} CADUs, {r['gr_sync_losses']} sync losses, {r['gr_dropped']} records dropped; "
          f"link: {r['lnk_code_errors']} code errors, {r['lnk_lock_losses']} lock losses, "
          f"{r['lnk_rx_overflows']} RX overflows")
    check_ecc(r, a, problems)
    checks = [("emulator lines", r["se_lines"], lines), ("underruns", r["se_underrun"], 0),
              ("line period overruns", r["se_overruns"], 0), ("emulator drops", r["se_dropped"], 0),
              # lines lost without a fill line (RAW overload) leave the corner turn short;
              # the scene guard still completes every scene
              ("corner turn lines (+ unfilled)", r["ct_lines"] + (r["ct_unfilled"] if a.raw else 0), lines),
              ("packetized scenes", r["pkt_scenes"], total),
              ("timestamp overflows", r["pkt_ts_overflows"], 0), ("ground sync losses", r["gr_sync_losses"], 0),
              ("ground records dropped", r["gr_dropped"], 0), ("link lock losses", r["lnk_lock_losses"], 0),
              ("link RX overflows", r["lnk_rx_overflows"], 0)]
    if not a.raw:
        checks.append(("dropped lines", r["ct_dropped"], 0))
    for name, got, exp in checks:
        if got != exp:
            problems.append(f"{name}: {got}, expected {exp}")
    if r["gr_cadus"] + 4 < r["aos_cadus"]:
        problems.append(f"the ground received {r['gr_cadus']} of {r['aos_cadus']} CADUs")
    if a.raw:
        frac = r["ct_dropped"] / lines if lines else 0
        print(f"RAW mode (SYS-09): {r['ct_dropped']} of {lines} lines dropped ({100 * frac:.1f} %): "
              f"raw data ({nx * nz * depth * 1000 * 100e3 / job['line_period'] / 1e6:.0f} Mbit/s) "
              f"{'exceeds' if r['ct_dropped'] else 'did NOT exceed'} the link")
        if r["ct_dropped"] == 0:
            problems.append("RAW mode dropped no lines")
    for p in problems:
        print(f"  problem: {p}")
    what = "RAW mode drops (SYS-09)" if a.raw else "no line dropped (VER-08)"
    print(("PASS" if not problems else "FAIL") + f": run_m5 long run, {total} scenes {nx}x{ny}x{nz} D={depth}, "
          f"{seconds / 60:.1f} min, {what}")
    return not problems


def verify_prbs(work, job, a):
    r, _, _ = unpack_result((work / "result.bin").read_bytes())
    problems = []
    if r["magic"] != RESULT_MAGIC or r["seq"] != job["seq"]:
        problems.append("no result block for this job")
    if r["status"] != 0:
        problems.append(f"status {r['status']} ({status_text(r['status'])})")
    words = r["prbs_words_lo"] | (r["prbs_words_hi"] << 32)
    bits = PRBS_BITS_PER_WORD * words
    seconds = (r["prbs_ticks_lo"] | (r["prbs_ticks_hi"] << 32)) / max(r["timer_hz"], 1)
    locked = bool(r["prbs_status"] & 0x10)
    print(f"PRBS-31 ({a.loopback} loopback, received on {a.rx.upper()}): {seconds:.0f} s, {bits:.3e} bits, "
          f"{r['prbs_errors']} word errors, checker {'locked' if locked else 'NOT locked'} at the end")
    if r["prbs_errors"]:
        print(f"  BER about {r['prbs_errors'] / bits:.2e} (each error word counted once)")
        problems.append(f"{r['prbs_errors']} PRBS errors")
    elif bits:
        print(f"  no errors: BER < {-math.log(0.05) / bits:.2e} at 95 % confidence")
    if not locked:
        problems.append("PRBS checker not locked")
    if bits < 3e12:
        print(f"  (VER-06 needs >= 3e12 bits: run for at least {math.ceil(3e12 / 1.25e9 / 60)} minutes)")
    for p in problems:
        print(f"  problem: {p}")
    print(("PASS" if not problems else "FAIL") + f": run_m5 PRBS, {bits:.3e} bits")
    return not problems


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--scene", nargs="+", help=".hpdp raw scenes (BIL), instead of synthetic ones")
    p.add_argument("--cal", help=".cal calibration table, instead of a synthetic one")
    p.add_argument("--size", nargs=4, type=int, metavar=("NX", "NY", "NZ", "D"), default=(128, 64, 64, 12),
                   help="synthetic scene size and depth (default 128 64 64 12)")
    p.add_argument("--scenes", type=int, default=4, help="synthetic scenes (default 4)")
    p.add_argument("--seed", type=int, default=1)
    p.add_argument("--source", choices=m4.SOURCES, default="dma", help="sensor emulator source")
    p.add_argument("--no-correction", action="store_true", help="radiometric correction bypass")
    p.add_argument("--line-period", type=int, default=0, help="clk_sensor cycles per line (default Nx*Nz+1)")
    p.add_argument("--cfg", nargs="*", default=[], metavar="KEY=VALUE",
                   help="compressor settings (ccsds123.Config fields, e.g. bands=15 omega=16)")
    g = p.add_argument_group("downlink")
    g.add_argument("--no-capture", action="store_true", help="compressed scenes only down the link")
    g.add_argument("--pkt-data", type=int, default=2048, help="user bytes per packet (default 2048)")
    g.add_argument("--apid", type=lambda s: int(s, 0), default=0x100)
    g.add_argument("--scid", type=lambda s: int(s, 0), default=0x42)
    g.add_argument("--vcid", type=int, default=1)
    g.add_argument("--frame-rate", type=float, default=0, help="science frames per second limit (AOS-02)")
    g.add_argument("--no-idle", action="store_true", help="no AOS idle frames while the scenes go down")
    e = g.add_mutually_exclusive_group()
    e.add_argument("--ber", type=float, default=0, help="random bit errors at this BER")
    e.add_argument("--burst", nargs=2, type=int, metavar=("BITS", "PERIOD"), help="burst errors")
    e.add_argument("--asm", nargs=2, type=int, metavar=("BITS", "PERIOD"), help="errors in every PERIOD-th ASM")
    g.add_argument("--ei-seed", type=int, default=1, help="error injector seed")
    g.add_argument("--asm-tol", type=int, default=2)
    g.add_argument("--flywheel", type=int, default=3)
    g.add_argument("--xfer", type=int, default=64, help="ground records per DMA transfer (default 64)")
    g.add_argument("--repeat", type=int, default=1,
                   help="stream the raw scenes this many times (long runs, VER-08; needs --no-capture)")
    g.add_argument("--no-ground-capture", action="store_true",
                   help="ground records counted, not captured (implied by --repeat > 1)")
    g.add_argument("--raw", action="store_true", help="compressor bypass: RAW mode (SYS-09; a long run)")
    k = p.add_argument_group("link")
    k.add_argument("--rx", choices=("sfp1", "sfp0"), default="sfp1", help="receiving SFP cage (default sfp1)")
    k.add_argument("--loopback", choices=("none", "pcs", "pma", "fabric"), default="none",
                   help="GTH near-end PCS / PMA loopback, or inside the FPGA (default: the fiber)")
    k.add_argument("--link-timeout", type=int, default=1000, help="ms to wait for LOCKED (default 1000)")
    k.add_argument("--prbs", type=int, metavar="SECONDS", help="PRBS-31 test instead of scenes")
    n = p.add_argument_group("ground record upload over Ethernet (SW-05)")
    n.add_argument("--upload", action="store_true",
                   help="the board serves the ground records over TCP; fetched and compared after the run")
    n.add_argument("--board-ip", default="", help="fixed board address (default: DHCP, else 169.254.77.10)")
    n.add_argument("--board-mask", default="", help="netmask for --board-ip (default 255.255.255.0)")
    p.add_argument("--ecc", action="append", default=[], metavar="MEMORY:ADDR[:dbe]",
                   help="RAD-02: inject an ECC error before the scenes into the word that holds ADDR "
                        "(MEMORY cal: table entry z*Nx+x; ct: corner-turn sample buffer*2^17+z*Nx+x; "
                        "lb: line buffer sample x*Nz+z); single-bit unless ':dbe'; up to 3, one per memory")
    p.add_argument("--seq", type=int, default=1, help="job sequence number")
    p.add_argument("--work", default=str(Path.home() / "hpdp_m5"), help="work directory (no spaces)")
    p.add_argument("--xsa", default=str(REPO / "build" / "m5" / "hpdp.xsa"))
    p.add_argument("--elf", default=str(Path.home() / "hpdp_vitis_m5" / "hpdp_run" / "build" / "hpdp_run.elf"),
                   help="hpdp_run.elf (default ~/hpdp_vitis_m5/hpdp_run/build/hpdp_run.elf)")
    p.add_argument("--xsdb", default=str(m4.XSDB))
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="write the files and the XSDB script only")
    mode.add_argument("--verify-only", action="store_true", help="check the files of a previous run")
    mode.add_argument("--fake-board", action="store_true", help="use the model's output as the board's")
    a = p.parse_args()

    work = Path(a.work)
    if " " in str(work.resolve()):
        sys.exit("the work directory must not contain spaces (XSDB)")

    if a.prbs:
        if a.fake_board:
            sys.exit("--fake-board does not apply to --prbs")
        job = prbs_job(work, a)
        if not a.verify_only:
            write_xsdb(work, job, a, a.prbs)
            print(f"files and XSDB script in {work}")
            if a.dry_run:
                return 0
            print(f"running XSDB (JTAG; volatile configuration only), PRBS for {a.prbs} s ...")
            if subprocess.call([a.xsdb, str(work / "run.tcl")]) != 0:
                print("FAIL: XSDB reported an error")
                return 1
        return 0 if verify_prbs(work, job, a) else 1

    if len(a.ecc) > 3 or len({s.split(":")[0] for s in a.ecc}) != len(a.ecc):
        sys.exit("--ecc: at most one injection per memory")
    for spec in a.ecc:
        try:
            ecc_inject_word(spec)
        except ValueError as exc:
            sys.exit(str(exc))
    raws, cal, dims = m4.build_inputs(a)
    nx, ny, nz, depth = dims
    if len(raws) > MAX_SCENES:
        sys.exit(f"at most {MAX_SCENES} scenes")
    if not 1 <= a.pkt_data <= 2048 or not 1 <= a.xfer <= 0xFFFF or not 0 <= a.vcid <= 62:
        sys.exit("--pkt-data 1..2048, --xfer 1..65535, --vcid 0..62")
    cfg = c.Config(**{k_: int(v) if v.lstrip("-").isdigit() else v
                      for k_, v in (kv.split("=", 1) for kv in a.cfg)})
    ctx = c._Ctx.build(nx, ny, nz, depth, cfg)
    long_run = a.repeat > 1 or a.raw
    if long_run:
        # judged by the counters: no model output (full-size scenes take minutes each)
        if not a.no_capture or a.source != "dma" or a.upload:
            sys.exit("long runs (--repeat > 1, --raw) need --no-capture, the DMA source and no --upload")
        a.no_ground_capture = True
        print(f"{len(raws)} scenes {nx}x{ny}x{nz} D={depth} streamed {a.repeat} times "
              f"({len(raws) * a.repeat * ny} lines){', RAW mode' if a.raw else ''}")
        expect = [(raw, b"", {"clamped": 0, "replaced": 0}) for raw in raws]
        packets, frames = 0, []
    else:
        print(f"{len(raws)} scenes {nx}x{ny}x{nz} D={depth}, source {a.source}, "
              f"correction {'off' if a.no_correction else 'on'}: computing the expected output ...")
        expect = m4.expected_output(raws, cal, cfg, not a.no_correction)
        # the corrected scenes, as references for the ground station (sw/ground/hpdp_gs.py --ref)
        work.mkdir(parents=True, exist_ok=True)
        for k, (fixed, _, _) in enumerate(expect):
            f.write_scene(work / f"ref_{k:04d}.hpdp", fixed)
        packets, frames = downlink_model(expect, ny, a)
        print(f"downlink: {packets} packets in {len(frames)} science frames")
    job = downlink_job(work, raws, cal, dims, a, cfg, ctx, expect, len(frames))
    # as hpdp_run's own deadline (sensor time + the data at >= 50 MB/s), so a run that
    # times out on the board still returns its result before XSDB gives up
    total = len(raws) * a.repeat
    seconds = total * ny * job["line_period"] / 100e6 + nx * ny * nz * total * 2 / 50e6 \
        + len(frames) * 1024 / 100e6

    if a.fake_board:
        if long_run:
            sys.exit("--fake-board does not apply to long runs")
        fake_board(work, job, a, expect, dims, frames, packets)
    elif not a.verify_only:
        write_xsdb(work, job, a, seconds)
        print(f"files and XSDB script in {work}")
        if a.dry_run:
            return 0
        print("running XSDB (JTAG; volatile configuration only) ...")
        if subprocess.call([a.xsdb, str(work / "run.tcl")]) != 0:
            print("FAIL: XSDB reported an error")
            return 1
    if long_run:
        return 0 if verify_long_run(work, job, a, dims, len(raws)) else 1
    ok = verify_downlink(work, job, a, expect, dims, frames, packets)
    if a.upload and not a.fake_board:
        ok = check_upload(work, a) and ok
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
