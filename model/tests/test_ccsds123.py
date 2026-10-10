import random
from array import array

import pytest
from scenes import constant, noise, smooth

from hpdp_model import ccsds123 as c
from hpdp_model import formats as f

CONFIGS = {
    "default": {},
    "reduced": {"full_prediction": False},
    "narrow_nbr": {"local_sum": "neighbor_narrow"},
    "wide_col": {"local_sum": "column_wide"},
    "narrow_col_reduced": {"local_sum": "column_narrow", "full_prediction": False},
    "p0": {"bands": 0},
    "p1": {"bands": 1},
    "p15": {"bands": 15},
    "omega4": {"omega": 4},
    "omega19": {"omega": 19},
    "r64": {"register_size": 64},
    "fast_weights": {"v_min": -6, "v_max": 9, "t_inc_log2": 4},
    "umax8": {"u_max": 8},
    "umax32": {"u_max": 32},
    "slow_counter": {"gamma0": 8, "gamma_star": 11},
    "k0": {"k_init": 0},
    "bsq": {"order": "bsq"},
    "bil": {"interleave": 1},
    "subframe5": {"interleave": 5},
    "word8": {"output_word_bytes": 8},
}


@pytest.mark.parametrize("depth", [8, 12, 16])
@pytest.mark.parametrize("name", list(CONFIGS))
def test_lossless_round_trip(name, depth):
    scene = smooth(11, 5, 17, depth, seed=depth)
    data = c.compress(scene, c.Config(**CONFIGS[name]))
    assert c.decompress(data).samples == scene.samples


@pytest.mark.parametrize("depth", [2, 5, 16])
def test_noise_round_trip_and_bounded_expansion(depth):
    scene = noise(9, 6, 7, depth, seed=depth)
    data = c.compress(scene)
    assert c.decompress(data).samples == scene.samples
    raw_bytes = depth * len(scene.samples) / 8
    assert len(data) < 1.5 * raw_bytes + c.HEADER_BYTES


@pytest.mark.parametrize("value", [0, 4095])
def test_constant_scenes(value):
    scene = constant(8, 4, 6, 12, value)
    assert c.decompress(c.compress(scene)).samples == scene.samples
    # K = 0 starts the coder at 1-bit codewords (K = 7 needs hundreds of samples to adapt)
    data = c.compress(scene, c.Config(k_init=0))
    assert c.decompress(data).samples == scene.samples
    assert len(data) < 0.25 * 12 * len(scene.samples) / 8


def test_smooth_scene_compresses():
    scene = smooth(32, 8, 24, 12, noise=4)
    data = c.compress(scene)
    assert 12 * len(scene.samples) / 8 / len(data) > 3.0


def test_interband_prediction_converges():
    """Every band = the same random texture + an offset. Spatial prediction cannot
    predict the texture; inter-band prediction can, exactly, once the weight of the
    previous band converges to 1.0. Checks the spectral path and the weight update."""
    rng = random.Random(1)
    nx, ny, nz = 32, 32, 12
    tex = [rng.randrange(1500) for _ in range(nx * ny)]
    scene = f.Scene(nx, ny, nz, 12, array("H", [tex[y * nx + x] + 100 * z
                                              for y in range(ny) for z in range(nz) for x in range(nx)]))
    spatial = len(c.compress(scene, c.Config(bands=0)))
    trace = []
    cfg = c.Config(bands=1, full_prediction=False, v_max=9, t_inc_log2=4)
    spectral = len(c.compress(scene, cfg, trace=trace))
    late = [abs(scene.get(x, y, z) - s_hat) for z, y, x, _, _, _, s_hat, _, _ in trace
            if z == nz - 1 and y >= ny // 2]
    assert sum(late) / len(late) < 0.5
    assert spatial / spectral > 2.5


def test_output_padded_to_word_size():
    scene = smooth(7, 3, 5, 12)
    for b in (1, 3, 8):
        assert len(c.compress(scene, c.Config(output_word_bytes=b))) % b == 0


def test_header_round_trip():
    cfg = c.Config(bands=5, full_prediction=False, local_sum="column_narrow", omega=9,
                   register_size=40, v_min=-3, v_max=7, t_inc_log2=9, u_max=24, gamma0=3,
                   gamma_star=9, k_init=5, order="bi", interleave=3, output_word_bytes=4, user_data=0xA5)
    scene = smooth(10, 4, 12, 14)
    data = c.compress(scene, cfg)
    nx, ny, nz, depth, back, start = c.parse_header(data)
    assert (nx, ny, nz, depth, start) == (10, 4, 12, 14, c.HEADER_BYTES)
    assert back == cfg


def test_header_layout():
    scene = smooth(300, 2, 7, 12)
    hdr = c.compress(scene, c.Config(user_data=0x5A))[:c.HEADER_BYTES]
    assert hdr[0] == 0x5A                                  # user defined data
    assert int.from_bytes(hdr[1:3], "big") == 300           # X size
    assert int.from_bytes(hdr[3:5], "big") == 2             # Y size
    assert int.from_bytes(hdr[5:7], "big") == 7             # Z size
    # byte 7: sample type | reserved | large D | dynamic range (4) | encoding order
    assert (hdr[7] >> 1) & 0x0F == 12                        # D mod 16
    assert hdr[7] >> 5 == 0                                  # unsigned, D <= 16
    assert hdr[7] & 1 == 0                                   # band-interleaved order


def test_parse_rejects_unsupported_features():
    data = bytearray(c.compress(smooth(6, 2, 4, 12)))
    data[7] |= 0x80                                          # sample type: signed
    with pytest.raises(NotImplementedError, match="signed"):
        c.parse_header(bytes(data))


def test_trace_records_every_sample():
    scene = smooth(6, 3, 5, 12)
    trace = []
    c.compress(scene, trace=trace)
    assert len(trace) == len(scene.samples)
    firsts = [t for t in trace if t[1] == 0 and t[2] == 0]
    assert len(firsts) == scene.nz and all(t[8] is None for t in firsts)
    assert all(0 <= t[6] < 4096 for t in trace)


def test_near_lossless_error_bound():
    scene = noise(10, 5, 8, 12, seed=4)
    lossless = c.compress(scene, header=False)
    for m in (1, 3, 7):
        cfg = c.Config(max_error=m)
        body = c.compress(scene, cfg, header=False)
        back = c.decompress(body, cfg, dims=(scene.nx, scene.ny, scene.nz, scene.depth))
        assert max(abs(a - b) for a, b in zip(back.samples, scene.samples)) <= m
        assert len(body) < len(lossless)


def test_near_lossless_header_not_supported_yet():
    with pytest.raises(NotImplementedError):
        c.compress(smooth(5, 2, 3, 12), c.Config(max_error=2))


@pytest.mark.parametrize("bad", [
    {"bands": 16}, {"omega": 3}, {"omega": 20}, {"register_size": 31}, {"v_min": 4, "v_max": 3},
    {"t_inc_log2": 12}, {"u_max": 7}, {"gamma0": 0}, {"gamma0": 6, "gamma_star": 6},
    {"k_init": 11}, {"order": "bip"}, {"interleave": 99}, {"output_word_bytes": 9},
])
def test_invalid_parameters(bad):
    with pytest.raises(ValueError):
        c.compress(smooth(6, 2, 8, 12), c.Config(**bad))


def test_single_column_images():
    scene = smooth(1, 6, 5, 12)
    data = c.compress(scene, c.Config(local_sum="column_wide"))
    assert c.decompress(data).samples == scene.samples
    with pytest.raises(ValueError, match="Nx >= 2"):
        c.compress(scene, c.Config(local_sum="neighbor_wide"))
