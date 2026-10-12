"""Ground station verification and report (SPEC.md 9.2, GS-06): every
reconstructed scene against the model's corrected scene, sample for sample."""

import re
import time
from dataclasses import dataclass
from pathlib import Path

from gs_decode import f


@dataclass
class Verdict:
    scene_id: int
    status: str                       # PASS, FAIL, or "-" without a reference
    detail: str = ""
    residual: int = None              # samples that differ from the reference


def load_refs(spec):
    """Reference scenes by scene ID: a directory of ref_NNNN.hpdp files (written
    by sw/host/run_m5.py), or a list of .hpdp files taken as scenes 0, 1, 2, ..."""
    if not spec:
        return {}
    paths = [Path(p) for p in spec]
    if len(paths) == 1 and paths[0].is_dir():
        refs = {}
        for p in sorted(paths[0].glob("ref_*.hpdp")):
            m = re.fullmatch(r"ref_(\d+)\.hpdp", p.name)
            if m:
                refs[int(m.group(1))] = f.read_scene(p)
        return refs
    return {k: f.read_scene(p) for k, p in enumerate(paths)}


def verify(result, refs):
    ref = refs.get(result.scene_id)
    if ref is None:
        return Verdict(result.scene_id, "-", "no reference")
    if result.scene is None:
        return Verdict(result.scene_id, "FAIL", result.reason or "not reconstructed")
    s = result.scene
    if (s.nx, s.ny, s.nz, s.depth) != (ref.nx, ref.ny, ref.nz, ref.depth):
        return Verdict(result.scene_id, "FAIL",
                       f"size {s.nx}x{s.ny}x{s.nz} D={s.depth}, reference {ref.nx}x{ref.ny}x{ref.nz} D={ref.depth}")
    residual = sum(a != b for a, b in zip(s.samples, ref.samples)) if s.samples != ref.samples else 0
    if residual:
        return Verdict(result.scene_id, "FAIL", f"{residual} samples differ from the reference", residual)
    return Verdict(result.scene_id, "PASS", "bit-exact", 0)


def scene_line(r, v=None):
    """One console line per scene."""
    if r.scene is not None:
        s = r.scene
        text = f"scene {r.scene_id}: {s.nx}x{s.ny}x{s.nz} D={s.depth}"
        if r.payload:
            text += (f", {len(r.payload)} bytes, {r.bits_per_sample:.2f} bits/sample ({r.ratio:.2f}:1), "
                     f"{r.frames} frames, {r.rs_corrected} RS corrections, decoded in {r.decode_seconds:.1f} s")
    else:
        text = f"scene {r.scene_id}: NOT reconstructed: {r.reason}"
        if r.frames:
            text += f" ({r.frames} frames, {r.rs_uncorrectable} uncorrectable, {r.lost_frames} lost)"
    if r.flags:
        text += f" [flags: {r.flag_text()}]"
    if v is not None and v.status != "-":
        text += f" -> {v.status}" + ("" if v.status == "PASS" or v.detail == r.reason else f" ({v.detail})")
    return text


def link_lines(st):
    sync = ", ".join(f"{n} {k}" for k, n in st.sync.items() if n)
    lines = [
        f"records: {st.records} ({sync}), {st.asm_errors} ASM bit errors, "
        f"{st.seq_gaps} sequence numbers not captured (skipped idle frames or drops)",
        f"RS: {st.rs_corrected} symbols corrected, {st.rs_uncorrectable} codeblocks uncorrectable",
        f"frames: {st.science_frames} science, {st.idle_frames} idle, {st.other_vc_frames} other VC, "
        f"{st.foreign_frames} foreign, {st.lost_frames} lost (VC frame count gaps)",
        f"packets: {st.packets} science, {st.pkt_seq_gaps} missing by sequence count, "
        f"{st.packet_errors} unreadable, {st.other_apid_packets} other APID",
        f"scenes: {st.scenes_ok} reconstructed, {st.scenes_failed} not",
    ]
    if st.rs_crosschecked:
        lines.append(f"RS cross-check (reedsolo): {st.rs_crosschecked} codeblocks, "
                     f"{st.rs_crosscheck_mismatches} disagreements")
    return lines


def write_report(path, source, results, verdicts, stats):
    """Markdown report: link statistics and one row per scene."""
    rows = []
    for r in results:
        v = verdicts.get(r.scene_id)
        bps = f"{r.bits_per_sample:.2f}" if r.bits_per_sample else "-"
        ratio = f"{r.ratio:.2f}:1" if r.ratio else "-"
        dims = f"{r.scene.nx}x{r.scene.ny}x{r.scene.nz} D={r.scene.depth}" if r.scene else "-"
        result = v.status if v and v.status != "-" else ("decoded" if r.scene else "not decoded")
        detail = (v.detail if v and v.status == "FAIL" else r.reason) or ""
        rows.append(f"| {r.scene_id} | {r.timestamp} | {dims} | {len(r.payload)} | {bps} | {ratio} | "
                    f"{r.packets} | {r.frames} | {r.rs_corrected} | {r.rs_uncorrectable} | {r.lost_frames} | "
                    f"{r.flag_text()} | **{result}** {detail} |")
    passed = sum(1 for v in verdicts.values() if v.status == "PASS")
    checked = sum(1 for v in verdicts.values() if v.status != "-")
    text = [
        "# HPDP ground station report", "",
        f"Source: `{source}`  ",
        f"Generated: {time.strftime('%Y-%m-%d %H:%M:%S')}  ",
        f"Verification: {passed} of {checked} scenes bit-exact with the reference" if checked
        else "Verification: no reference scenes given", "",
        "## Link", "",
    ] + [f"- {line}" for line in link_lines(stats)] + [
        "", "## Scenes", "",
        "| Scene | Timestamp | Size | Bytes | Bits/sample | Ratio | Packets | Frames | RS corrected "
        "| Uncorrectable | Lost frames | Flags | Result |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ] + rows + [""]
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text("\n".join(text), encoding="utf-8")
