import random

import pytest

from hpdp_model import rs


def random_data(seed, n=rs.K):
    rng = random.Random(seed)
    return bytes(rng.randrange(256) for _ in range(n))


def corrupt(word, positions, rng):
    out = bytearray(word)
    for p in positions:
        out[p] ^= rng.randrange(1, 256)
    return bytes(out)


def test_field_tables():
    assert rs.EXP[255] == rs.EXP[0] == 1
    assert sorted(rs.EXP[:255]) == list(range(1, 256))      # alpha is primitive
    assert all(rs.gmul(a, rs.gdiv(1, a)) == 1 for a in range(1, 256))


def test_generator_polynomial():
    g = rs.GENERATOR
    assert len(g) == rs.NROOTS + 1 and g[0] == 1
    # roots alpha^(11 j), j = 112..143 come in reciprocal pairs: g is palindromic
    assert g == g[::-1]


def test_dual_basis_tables_are_inverse():
    assert sorted(rs.TO_DUAL) == list(range(256))
    assert all(rs.FROM_DUAL[rs.TO_DUAL[i]] == i for i in range(256))


def test_codewords_have_zero_syndromes():
    for seed in range(5):
        word = rs.encode(random_data(seed))
        assert not any(rs.syndromes([rs.FROM_DUAL[b] for b in word]))


def test_all_zero_data():
    assert rs.encode(bytes(223)) == bytes(255)


@pytest.mark.parametrize("nerr", [1, 2, 8, 15, 16])
def test_corrects_up_to_16_errors(nerr):
    rng = random.Random(nerr)
    for trial in range(10):
        data = random_data(100 * nerr + trial)
        bad = corrupt(rs.encode(data), rng.sample(range(255), nerr), rng)
        out, n = rs.decode(bad)
        assert out == data and n == nerr


def test_errors_in_parity_only():
    rng = random.Random(7)
    data = random_data(7)
    bad = corrupt(rs.encode(data), range(223, 239), rng)
    assert rs.decode(bad) == (data, 16)


def test_detects_too_many_errors():
    rng = random.Random(17)
    failures = 0
    for trial in range(30):
        bad = corrupt(rs.encode(random_data(trial)), rng.sample(range(255), 17 + trial % 10), rng)
        out, n = rs.decode(bad)
        failures += out is None and n == -1
    assert failures >= 29        # miscorrection beyond t is possible but very rare


def test_interleaved_round_trip_and_burst():
    rng = random.Random(4)
    frame = random_data(4, 223 * 4)
    block = rs.encode_interleaved(frame, 4)
    assert len(block) == 1020
    assert block[0::4][:223] == frame[0::4]           # systematic
    # a 64-byte burst puts 16 errors in each of the 4 codewords: still correctable
    burst = bytearray(block)
    for i in range(500, 564):
        burst[i] ^= rng.randrange(1, 256)
    out, counts = rs.decode_interleaved(bytes(burst), 4)
    assert out == frame and counts == [16, 16, 16, 16]


def test_matches_independent_implementation():
    reedsolo = pytest.importorskip("reedsolo")
    codec = reedsolo.RSCodec(nsym=32, nsize=255, fcr=112, prim=0x187, generator=rs.EXP[11], c_exp=8)
    for seed in range(5):
        data = random_data(seed)                         # conventional-basis symbols
        ref = bytes(codec.encode(bytearray(data)))
        assert ref[223:] == bytes(rs._parity_conventional(list(data)))
