"""Tests of the ground station decoder with streams built by the Python model.

    python -m pytest sw/ground
"""

import random
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "model" / "tests"))

from gs_decode import GroundDecoder, SceneResult  # noqa: E402
from gs_report import load_refs, verify, write_report  # noqa: E402
from hpdp_model import channel, formats as f, pipeline, radiometric  # noqa: E402
from scenes import smooth  # noqa: E402

NX, NY, NZ = 24, 8, 16


def scenes_and_stream(n, link=None):
    cal = radiometric.synthetic_calibration(NX, NZ, seed=11)
    raws = [radiometric.make_raw(smooth(NX, NY, NZ, 12, seed=s), cal, seed=s) for s in range(n)]
    stream, corrected, starts = pipeline.satellite(raws, cal, link=link or pipeline.LinkConfig())
    return stream, corrected, starts


def records_of(stream):
    """Capture records as the hardware ground receiver writes them."""
    return [r.codeblock + struct.pack("<I", f.CaptureRecord.status_word(r.asm_errors, r.sync_state, r.sequence))
            for r in channel.frame_sync(stream)]


def run(records, **kw):
    dec = GroundDecoder(**kw)
    out = []
    for rec in records:
        out += dec.feed(rec)
    return out + dec.flush(), dec.stats


def test_clean_capture_with_idle_frames():
    stream, corrected, _ = scenes_and_stream(3, pipeline.LinkConfig(idle_frames_between_scenes=1))
    results, st = run(records_of(stream))
    assert [r.scene_id for r in results] == [0, 1, 2]
    assert all(r.scene.samples == corrected[r.scene_id].samples for r in results)
    assert [r.timestamp for r in results] == [0, NY, 2 * NY]
    assert st.idle_frames == 3 and st.rs_corrected == 0 and st.lost_frames == 0 and st.scenes_ok == 3


def test_errors_corrected_and_attributed_to_scenes():
    stream, corrected, _ = scenes_and_stream(2)
    noisy, _ = channel.inject_bit_errors(stream, 1e-3, seed=5)
    results, st = run(records_of(noisy), crosscheck=True)
    assert all(r.ok and r.scene.samples == corrected[r.scene_id].samples for r in results)
    assert st.rs_corrected > 0 and sum(r.rs_corrected for r in results) == st.rs_corrected
    assert st.rs_crosscheck_mismatches == 0 and st.rs_crosschecked == st.records


def test_lost_codeblock_costs_one_scene_and_foreign_frames_are_ignored():
    stream, corrected, starts = scenes_and_stream(3)
    recs = records_of(stream)
    k = starts[1] + 1                                   # a codeblock inside scene 1
    bad = bytearray(recs[k])
    rng = random.Random(1)
    # random errors far beyond RS correction (a periodic pattern would not do:
    # an error repeating with a period that divides 255 is itself a codeword)
    for i in rng.sample(range(1020), 300):
        bad[i] ^= rng.randrange(1, 256)
    recs[k] = bytes(bad)
    other, _, _ = scenes_and_stream(1, pipeline.LinkConfig(scid=0x17))
    recs.insert(2, records_of(other)[0])                # another spacecraft's frame
    results, st = run(recs)
    ok = {r.scene_id: r.ok for r in results}
    assert ok == {0: True, 1: False, 2: True}
    assert st.rs_uncorrectable == 1 and st.lost_frames == 1 and st.foreign_frames == 1
    # (these scenes are only 3 frames long: the lost frame may take every packet of
    # scene 1, which is then reported missing; the loss is counted once either way)
    assert results[1].reason.startswith(("incomplete", "missing"))
    assert sum(r.rs_uncorrectable for r in results) == 1


def test_missing_scene_and_verification_report(tmp_path):
    stream, corrected, starts = scenes_and_stream(3)
    recs = records_of(stream)
    recs = recs[:starts[1]] + recs[starts[2]:]          # every frame of scene 1 lost
    results, st = run(recs)
    assert [(r.scene_id, r.ok) for r in results] == [(0, True), (1, False), (2, True)]
    assert results[1].reason.startswith("missing")
    for k, sc in enumerate(corrected):
        f.write_scene(tmp_path / f"ref_{k:04d}.hpdp", sc)
    refs = load_refs([tmp_path])
    verdicts = {r.scene_id: verify(r, refs) for r in results}
    assert [verdicts[k].status for k in range(3)] == ["PASS", "FAIL", "PASS"]
    write_report(tmp_path / "report.md", "test", results, verdicts, st)
    text = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert "2 of 3 scenes bit-exact" in text and "| 1 |" in text


def test_verify_detects_a_wrong_sample():
    _, corrected, _ = scenes_and_stream(1)
    good = corrected[0]
    wrong = f.Scene(good.nx, good.ny, good.nz, good.depth, type(good.samples)("H", good.samples))
    wrong.samples[5] ^= 1
    v = verify(SceneResult(0, scene=wrong, complete=True), {0: good})
    assert v.status == "FAIL" and v.residual == 1
