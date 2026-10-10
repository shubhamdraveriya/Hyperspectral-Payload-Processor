from scenes import smooth

from hpdp_model import radiometric as rad

ONE = 1 << 14


def test_gain_and_offset():
    line, rep = rad.correct_line([1000, 2000, 3000], [100, 100, 0], [ONE, ONE // 2, ONE + ONE // 4], [0, 0, 0], 12)
    assert line == [900, 950, 3750] and rep == 0


def test_rounding_and_clamping():
    # (5 * 0.5) = 2.5 rounds up; dark above the sample clamps to 0; overflow clamps to 2^D - 1
    line, _ = rad.correct_line([105, 50, 4000], [100, 60, 0], [ONE // 2, ONE, 2 * ONE], [0, 0, 0], 12)
    assert line == [3, 0, 4095]


def test_bad_pixel_replacement_rules():
    raw = [10, 20, 30, 40, 50, 60]
    z = [0] * 6
    g = [ONE] * 6
    # interior with two good neighbours -> rounded mean; edges -> the inner neighbour
    assert rad.correct_line(raw, z, g, [1, 0, 1, 0, 0, 1], 12) == ([20, 20, 30, 40, 50, 50], 3)
    # bad neighbour on one side -> copy the good one; isolated run of bad pixels -> 0
    assert rad.correct_line(raw, z, g, [0, 1, 1, 0, 0, 0], 12)[0] == [10, 10, 40, 40, 50, 60]
    assert rad.correct_line([7, 8, 9], [0] * 3, [ONE] * 3, [1, 1, 1], 12)[0] == [0, 0, 0]


def test_synthetic_raw_corrects_back():
    clean = smooth(20, 4, 9, 12, noise=8)
    cal = rad.synthetic_calibration(20, 9, seed=3, bad_fraction=0.02)
    raw = rad.make_raw(clean, cal)
    fixed, stats = rad.correct(raw, cal)
    assert stats["replaced"] == 4 * sum(cal.bad)
    for z in range(9):
        for x in range(20):
            if cal.bad[z * 20 + x]:
                continue
            for y in range(4):
                assert abs(fixed.get(x, y, z) - clean.get(x, y, z)) <= 1
