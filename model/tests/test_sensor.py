import pytest

from hpdp_model import sensor


def test_lfsr_is_maximal_length():
    state, seen = sensor.LFSR_SEED, set()
    while state not in seen:
        seen.add(state)
        state = sensor.lfsr_step(state)
    assert state == sensor.LFSR_SEED and len(seen) == 65535 and 0 not in seen


def test_ramp_values_in_bil_order():
    s = sensor.pattern_scene(sensor.SOURCE_RAMP, 5, 3, 4, 3)
    assert list(s.samples[:6]) == [0, 1, 2, 3, 4, 1]      # band 0 pixels 0..4, then band 1
    for y in range(3):
        for z in range(4):
            for x in range(5):
                assert s.get(x, y, z) == (x + y + z) & 7


def test_lfsr_scene_sequence():
    s = sensor.pattern_scene(sensor.SOURCE_LFSR, 4, 2, 3, 16)
    state = sensor.LFSR_SEED
    for v in s.samples:                     # one LFSR step per sample, in readout order
        assert v == state
        state = sensor.lfsr_step(state)
    low = sensor.pattern_scene(sensor.SOURCE_LFSR, 4, 2, 3, 5)
    assert list(low.samples) == [v & 31 for v in s.samples]


def test_dma_is_not_a_pattern():
    with pytest.raises(ValueError):
        sensor.pattern_scene(sensor.SOURCE_DMA, 4, 2, 3, 12)
