from scenes import smooth

from hpdp_model import channel, pipeline, radiometric

NX, NY, NZ = 24, 8, 16


def make_raw_scenes(n):
    cal = radiometric.synthetic_calibration(NX, NZ, seed=11)
    clean = [smooth(NX, NY, NZ, 12, seed=s) for s in range(n)]
    return [radiometric.make_raw(s, cal, seed=i) for i, s in enumerate(clean)], cal


def check(results, corrected):
    return [sid for sid, sc in results if sc is not None and sc.samples == corrected[sid].samples]


def test_clean_link():
    raws, cal = make_raw_scenes(3)
    stream, corrected, _ = pipeline.satellite(raws, cal, link=pipeline.LinkConfig(idle_frames_between_scenes=2))
    assert len(stream) % channel.CADU_BYTES == 0
    results, st = pipeline.ground(stream)
    assert check(results, corrected) == [0, 1, 2]
    assert st.rs_corrected_symbols == 0 and st.rs_uncorrectable == 0 and st.idle_frames == 6


def test_noisy_link_is_corrected():
    raws, cal = make_raw_scenes(2)
    stream, corrected, _ = pipeline.satellite(raws, cal)
    noisy, flips = channel.inject_bit_errors(stream, 1e-3, seed=3)
    results, st = pipeline.ground(noisy)
    assert flips > 0 and st.rs_corrected_symbols > 0
    assert check(results, corrected) == [0, 1]


def test_ground_from_captured_records():
    # the hardware ground receiver delivers derandomized codeblocks; records may
    # come with idle frames skipped and a foreign frame mixed in
    raws, cal = make_raw_scenes(2)
    stream, corrected, _ = pipeline.satellite(raws, cal, link=pipeline.LinkConfig(idle_frames_between_scenes=1))
    noisy, _ = channel.inject_bit_errors(stream, 5e-4, seed=8)
    blocks = [rec.codeblock for rec in channel.frame_sync(noisy)]
    foreign = pipeline.LinkConfig(scid=0x17)
    other, _, _ = pipeline.satellite(raws[:1], cal, link=foreign)
    blocks.insert(1, channel.frame_sync(other)[0].codeblock)
    results, st = pipeline.ground_records(blocks)
    assert check(results, corrected) == [0, 1]
    assert st.foreign_frames == 1 and st.rs_corrected_symbols > 0 and st.idle_frames == 2


def test_uncorrectable_burst_is_contained_to_one_scene():
    raws, cal = make_raw_scenes(3)
    stream, corrected, starts = pipeline.satellite(raws, cal)
    # wipe 400 bytes of the first CADU of scene 1: far beyond what RS can correct
    hit = channel.inject_burst(stream, 8 * (starts[1] * channel.CADU_BYTES + 100), 8 * 400)
    results, st = pipeline.ground(hit)
    assert st.rs_uncorrectable == 1 and st.lost_frames == 1
    assert check(results, corrected) == [0, 2]
    assert (1, False, "missing") in st.scenes or any(s[0] == 1 and not s[1] for s in st.scenes)
