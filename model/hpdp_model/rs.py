"""CCSDS Reed-Solomon (255,223), E = 16 (CCSDS 131.0-B, section 4).

Field generator F(x) = x^8 + x^7 + x^2 + x + 1, code generator roots
alpha^(11 j) for j = 112..143, symbols on the channel in Berlekamp's dual
basis. The dual-basis conversion uses the same matrix as Phil Karn's libfec
(ccsds_tal.c), so encode() is meant to match libfec's encode_rs_ccsds().

Codewords are lists/bytes of 255 symbols, first symbol transmitted first
(the highest-order coefficient).
"""

N, K, NROOTS = 255, 223, 32
T = NROOTS // 2
FCR, PRIM = 112, 11
_POLY = 0x187

# GF(256) tables, conventional (polynomial) basis
EXP = [0] * 512
LOG = [0] * 256
_x = 1
for _i in range(255):
    EXP[_i] = _x
    LOG[_x] = _i
    _x <<= 1
    if _x & 0x100:
        _x ^= _POLY
for _i in range(255, 512):
    EXP[_i] = EXP[_i - 255]


def gmul(a, b):
    if a == 0 or b == 0:
        return 0
    return EXP[LOG[a] + LOG[b]]


def gdiv(a, b):
    if b == 0:
        raise ZeroDivisionError
    if a == 0:
        return 0
    return EXP[(LOG[a] - LOG[b]) % 255]


def gpow(a, n):
    if a == 0:
        return 0
    return EXP[(LOG[a] * n) % 255]


# Generator polynomial g(x) = prod (x - alpha^(PRIM*j)), j = FCR .. FCR+NROOTS-1,
# coefficients highest degree first
GENERATOR = [1]
for _j in range(FCR, FCR + NROOTS):
    root = EXP[(PRIM * _j) % 255]
    nxt = GENERATOR + [0]
    for _i in range(1, len(nxt)):
        nxt[_i] ^= gmul(GENERATOR[_i - 1], root)
    GENERATOR = nxt

# Dual-basis conversion (libfec ccsds_tal.c)
_TAL = (0x8D, 0xEF, 0xEC, 0x86, 0xFA, 0x99, 0xAF, 0x7B)
TO_DUAL = [0] * 256      # conventional -> dual (Taltab)
FROM_DUAL = [0] * 256    # dual -> conventional (Tal1tab)
for _i in range(256):
    _t = 0
    for _k in range(8):
        if _i & (1 << _k):
            _t ^= _TAL[7 - _k]
    TO_DUAL[_i] = _t
    FROM_DUAL[_t] = _i


def _parity_conventional(data):
    """Systematic parity (remainder of data(x) * x^32 / g(x)), conventional basis."""
    rem = [0] * NROOTS
    for d in data:
        fb = d ^ rem[0]
        rem = rem[1:] + [0]
        if fb:
            for i in range(NROOTS):
                rem[i] ^= gmul(fb, GENERATOR[i + 1])
    return rem


def encode(data):
    """223 dual-basis data symbols -> 255-symbol codeword (data followed by parity)."""
    if len(data) != K:
        raise ValueError(f"need {K} data symbols")
    parity = _parity_conventional([FROM_DUAL[b] for b in data])
    return bytes(data) + bytes(TO_DUAL[p] for p in parity)


def syndromes(conv):
    """S_j = c(alpha^(PRIM*(FCR+j))), j = 0..31, codeword in conventional basis."""
    out = []
    for j in range(NROOTS):
        x = EXP[(PRIM * (FCR + j)) % 255]
        s = 0
        for c in conv:
            s = gmul(s, x) ^ c
        out.append(s)
    return out


def decode(codeword):
    """Correct up to 16 symbol errors.

    Returns (223 corrected data symbols, number of corrected symbols), or
    (None, -1) when the codeword is uncorrectable.
    """
    if len(codeword) != N:
        raise ValueError(f"need {N} symbols")
    r = [FROM_DUAL[b] for b in codeword]
    s = syndromes(r)
    if not any(s):
        return bytes(codeword[:K]), 0

    # Berlekamp-Massey: error locator lambda(x), lowest degree first
    lam, prev = [1] + [0] * NROOTS, [1] + [0] * NROOTS
    length, shift, b = 0, 1, 1
    for n in range(NROOTS):
        d = s[n]
        for i in range(1, length + 1):
            d ^= gmul(lam[i], s[n - i])
        if d == 0:
            shift += 1
            continue
        coef = gdiv(d, b)
        new = lam[:]
        for i in range(shift, NROOTS + 1):
            new[i] ^= gmul(coef, prev[i - shift])
        if 2 * length <= n:
            prev, length, b, shift = lam, n + 1 - length, d, 1
        else:
            shift += 1
        lam = new
    if length > T:
        return None, -1

    # Chien search over positions: symbol at index i has locator X = beta^(N-1-i),
    # beta = alpha^PRIM
    positions, inv_locators = [], []
    for i in range(N):
        power = N - 1 - i
        xinv = EXP[(-PRIM * power) % 255]
        v = 0
        for c in reversed(lam[:length + 1]):
            v = gmul(v, xinv) ^ c
        if v == 0:
            positions.append(i)
            inv_locators.append(xinv)
    if len(positions) != length:
        return None, -1

    # Forney: e = X^(1-FCR) * omega(X^-1) / lambda'(X^-1)
    omega = [0] * NROOTS
    for i in range(NROOTS):
        acc = 0
        for j in range(min(i, length) + 1):
            acc ^= gmul(lam[j], s[i - j])
        omega[i] = acc
    for pos, xinv in zip(positions, inv_locators):
        num = 0
        for c in reversed(omega):
            num = gmul(num, xinv) ^ c
        den = 0
        for i in range(1, length + 1, 2):          # formal derivative: odd terms only
            den ^= gmul(lam[i], gpow(xinv, i - 1))
        if den == 0:
            return None, -1
        x = gdiv(1, xinv)
        r[pos] ^= gmul(gpow(x, (1 - FCR) % 255), gdiv(num, den))
    if any(syndromes(r)):
        return None, -1
    return bytes(TO_DUAL[c] for c in r[:K]), len(positions)


# ---------------------------------------------------------------------------
# Interleaving (131.0-B 4.3): codeword j holds symbols j, j+I, j+2I, ...
# ---------------------------------------------------------------------------
def encode_interleaved(frame, depth):
    """Transfer frame of 223*I bytes -> codeblock of 255*I bytes."""
    if len(frame) != K * depth:
        raise ValueError(f"frame must be {K * depth} bytes for I = {depth}")
    words = [encode(frame[j::depth]) for j in range(depth)]
    out = bytearray(N * depth)
    for j, w in enumerate(words):
        out[j::depth] = w
    return bytes(out)


def decode_interleaved(block, depth):
    """Codeblock of 255*I bytes -> (frame or None, corrected symbols per codeword)."""
    if len(block) != N * depth:
        raise ValueError(f"codeblock must be {N * depth} bytes for I = {depth}")
    frame = bytearray(K * depth)
    counts = []
    ok = True
    for j in range(depth):
        data, n = decode(block[j::depth])
        counts.append(n)
        if data is None:
            ok = False
        else:
            frame[j::depth] = data
    return (bytes(frame) if ok else None), counts
