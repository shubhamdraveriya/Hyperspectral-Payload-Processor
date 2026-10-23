"""CCSDS 123.0-B-2 compressor and decompressor (reference model).

Supported (SPEC.md 6.4.1): unsigned samples, D = 2..16, full and reduced
prediction, all four local-sum types, P = 0..15, default weight
initialization, zero weight-exponent offsets, sample-adaptive entropy coder,
BI (any sub-frame depth M) and BSQ encoding orders, lossless coding. An
absolute, band-independent error limit (near-lossless, SPEC CMP-10) is
modelled for experiments but cannot be written into a header yet.

Section numbers in comments refer to CCSDS 123.0-B-2. Samples are processed
in encoding order; prediction only uses samples already coded, so the same
predictor serves the encoder and the decoder.

The output, header included, is byte-identical with an independent
implementation, the NTNU SmallSat Lab's Issue 2 verification model, on the
VER-02 matrix, the corner cases and the board's scenes (model/xcheck_ntnu.py,
docs/independent_check.md; SPEC O-5).
"""

from array import array
from dataclasses import dataclass, field, replace

from .bitstream import BitReader, BitWriter
from .formats import ORDER_BIL, Scene, index

LOCAL_SUMS = ("neighbor_wide", "neighbor_narrow", "column_wide", "column_narrow")


@dataclass
class Config:
    """Coding parameters. Image size and dynamic range come from the scene."""

    bands: int = 3                   # P, number of previous bands used for prediction
    full_prediction: bool = True     # False: reduced mode
    local_sum: str = "neighbor_wide"
    omega: int = 13                  # weight resolution
    register_size: int = 0           # R; 0 = max(32, D + omega + 2)
    v_min: int = -1                  # weight update scaling exponent, initial
    v_max: int = 3                   # ... and final
    t_inc_log2: int = 6              # change interval t_inc = 2**t_inc_log2
    u_max: int = 18                  # unary length limit
    gamma0: int = 1                  # initial count exponent
    gamma_star: int = 6              # rescaling counter size
    k_init: int = -1                 # accumulator initialization constant K; -1 = min(7, D - 2)
    order: str = "bi"                # "bi" or "bsq"
    interleave: int = 0              # sub-frame interleaving depth M for "bi"; 0 = Nz (BIP)
    output_word_bytes: int = 1       # B
    max_error: int = 0               # absolute error limit; 0 = lossless
    user_data: int = 0


@dataclass
class _Ctx:
    """Everything the coder needs about one image: dimensions plus resolved parameters."""

    nx: int
    ny: int
    nz: int
    depth: int
    cfg: Config
    register_size: int = 0
    k_init: int = 0
    interleave: int = 0
    weights_per_band: list = field(default_factory=list)

    @classmethod
    def build(cls, nx, ny, nz, depth, cfg):
        c = cls(nx, ny, nz, depth, cfg)
        c.register_size = cfg.register_size or max(32, depth + cfg.omega + 2)
        c.k_init = cfg.k_init if cfg.k_init >= 0 else min(7, depth - 2)
        c.interleave = cfg.interleave or nz
        c._validate()
        return c

    def _validate(self):
        cfg, d = self.cfg, self.depth
        checks = [
            (1 <= self.nx <= 65535 and 1 <= self.ny <= 65535 and 1 <= self.nz <= 65535, "image size"),
            (2 <= d <= 16, "dynamic range D must be 2..16"),
            (0 <= cfg.bands <= 15, "P must be 0..15"),
            (cfg.local_sum in LOCAL_SUMS, f"local_sum must be one of {LOCAL_SUMS}"),
            # With Nx = 1 the x = 0 and x = Nx-1 neighbour-sum cases of 4.4 coincide
            (self.nx >= 2 or cfg.local_sum.startswith("column"), "neighbour-oriented local sums need Nx >= 2"),
            # With Nx = 1 the directional local differences of full prediction are always 0,
            # and the independent NTNU model (model/xcheck_ntnu.py) refuses the mode (4.3.1)
            (self.nx >= 2 or not cfg.full_prediction, "full prediction mode needs Nx >= 2; use reduced mode"),
            (4 <= cfg.omega <= 19, "omega must be 4..19"),
            (max(32, d + cfg.omega + 2) <= self.register_size <= 64, "register size R out of range"),
            (-6 <= cfg.v_min <= cfg.v_max <= 9, "need -6 <= v_min <= v_max <= 9"),
            (4 <= cfg.t_inc_log2 <= 11, "t_inc must be 2**4..2**11"),
            (8 <= cfg.u_max <= 32, "u_max must be 8..32"),
            (1 <= cfg.gamma0 <= 8, "gamma0 must be 1..8"),
            (max(4, cfg.gamma0 + 1) <= cfg.gamma_star <= 11, "gamma_star must be max(4, gamma0+1)..11"),
            (0 <= self.k_init <= d - 2, "K must be 0..D-2"),
            (cfg.order in ("bi", "bsq"), "order must be 'bi' or 'bsq'"),
            (1 <= self.interleave <= self.nz, "interleave must be 1..Nz"),
            (1 <= cfg.output_word_bytes <= 8, "output word size B must be 1..8"),
            (cfg.max_error >= 0, "max_error must be >= 0"),
            (0 <= cfg.user_data <= 255, "user_data must fit in 8 bits"),
        ]
        for ok, msg in checks:
            if not ok:
                raise ValueError(msg)

    def encoding_order(self):
        """(z, y, x) in the order samples are coded (5.4.2)."""
        nx, ny, nz = self.nx, self.ny, self.nz
        if self.cfg.order == "bsq":
            for z in range(nz):
                for y in range(ny):
                    for x in range(nx):
                        yield z, y, x
        else:
            m = self.interleave
            for y in range(ny):
                for f in range(0, nz, m):
                    for x in range(nx):
                        for z in range(f, min(f + m, nz)):
                            yield z, y, x


class _Predictor:
    """Adaptive predictor, quantizer and mapper (4.3 - 4.11), plus the
    sample-adaptive entropy coder state (5.4.3.2)."""

    def __init__(self, ctx, trace=None):
        self.c = ctx
        cfg = ctx.cfg
        n = ctx.nx * ctx.ny
        self.smin, self.smax = 0, (1 << ctx.depth) - 1
        self.smid = 1 << (ctx.depth - 1)
        self.rep = [array("l", bytes(8 * n)) for _ in range(ctx.nz)]    # sample representatives s''
        self.dcen = [array("l", bytes(8 * n)) for _ in range(ctx.nz)]   # central local differences
        self.ndir = 3 if cfg.full_prediction else 0
        om = cfg.omega
        self.wmin, self.wmax = -(1 << (om + 2)), (1 << (om + 2)) - 1
        # Default weight initialization (4.6.3.2)
        self.weights = []
        for z in range(ctx.nz):
            w = [0] * self.ndir
            pz = min(z, cfg.bands)
            if pz:
                w.append(7 << (om - 3))
                for _ in range(pz - 1):
                    w.append(w[-1] // 8)
            self.weights.append(w)
        # Entropy coder state per band (5.4.3.2.2)
        g1 = 1 << cfg.gamma0
        acc0 = (((3 << (ctx.k_init + 6)) - 49) * g1) >> 7
        self.count = [g1] * ctx.nz
        self.acc = [acc0] * ctx.nz
        self.count_limit = (1 << cfg.gamma_star) - 1
        self.trace = trace

    # -- 4.4 local sums ------------------------------------------------------
    def local_sum(self, z, y, x):
        c, rep = self.c, self.rep[z]
        nx, t = c.nx, y * c.nx + x
        kind = c.cfg.local_sum
        if kind in ("column_wide", "column_narrow"):
            if y > 0:
                return 4 * rep[t - nx]
            if kind == "column_wide":
                return 4 * rep[t - 1]
            return 4 * self.rep[z - 1][t - 1] if z > 0 else 4 * self.smid
        if y == 0:
            if kind == "neighbor_wide":
                return 4 * rep[t - 1]
            return 4 * self.rep[z - 1][t - 1] if z > 0 else 4 * self.smid
        n = rep[t - nx]
        if x == 0:
            return 2 * (n + rep[t - nx + 1])
        w, nw = rep[t - 1], rep[t - nx - 1]
        if kind == "neighbor_wide":
            if x < nx - 1:
                return w + nw + n + rep[t - nx + 1]
            return w + nw + 2 * n
        # neighbor_narrow
        if x < nx - 1:
            return nw + 2 * n + rep[t - nx + 1]
        return 2 * (nw + n)

    # -- 4.5 - 4.7 prediction --------------------------------------------------
    def predict(self, z, y, x):
        """Returns (double-resolution predicted sample, sigma, local difference vector)."""
        c, cfg = self.c, self.c.cfg
        nx, t = c.nx, y * c.nx + x
        if t == 0:
            if cfg.bands > 0 and z > 0:
                return 2 * self.rep[z - 1][0], None, None
            return 2 * self.smid, None, None
        sigma = self.local_sum(z, y, x)
        u = []
        if self.ndir:
            if y > 0:
                rep = self.rep[z]
                north4 = 4 * rep[t - nx]
                if x > 0:
                    u = [north4 - sigma, 4 * rep[t - 1] - sigma, 4 * rep[t - nx - 1] - sigma]
                else:
                    u = [north4 - sigma] * 3
            else:
                u = [0, 0, 0]
        for i in range(1, min(z, cfg.bands) + 1):
            u.append(self.dcen[z - i][t])
        om, r = cfg.omega, c.register_size
        dhat = sum(w * v for w, v in zip(self.weights[z], u))
        raw = dhat + ((sigma - 4 * self.smid) << om)
        wrapped = ((raw + (1 << (r - 1))) % (1 << r)) - (1 << (r - 1))
        hr = wrapped + (self.smid << (om + 2)) + (1 << (om + 1))
        hr = max(self.smin << (om + 2), min(hr, (self.smax << (om + 2)) + (1 << (om + 1))))
        if self.trace is not None:
            self._pending = (sigma, dhat, hr)
        return hr >> (om + 1), sigma, u

    def theta(self, s_hat, m):
        return min((s_hat - self.smin + m) // (2 * m + 1), (self.smax - s_hat + m) // (2 * m + 1))

    @staticmethod
    def map_index(q, theta, dbl):
        """Mapped quantizer index (4.11)."""
        aq = abs(q)
        if aq > theta:
            return aq + theta
        signed = q if dbl % 2 == 0 else -q
        return 2 * aq if 0 <= signed <= theta else 2 * aq - 1

    def unmap_index(self, delta, theta, dbl, s_hat, m):
        if delta > 2 * theta:
            mag = delta - theta
            lower = (s_hat - self.smin + m) // (2 * m + 1)
            return mag if theta == lower else -mag
        if delta % 2 == 0:
            v = delta // 2
            return v if dbl % 2 == 0 else -v
        v = (delta + 1) // 2
        return -v if dbl % 2 == 0 else v

    # -- 4.9, 4.10 reconstruction and weight update ----------------------------
    def update(self, z, y, x, q, s_hat, dbl, sigma, u, m):
        """Store the sample representative and central difference, update weights.
        Returns the reconstructed sample s'."""
        c, cfg = self.c, self.c.cfg
        t = y * c.nx + x
        s_prime = max(self.smin, min(s_hat + q * (2 * m + 1), self.smax))
        # With theta = phi = psi = 0 (sample representative flag 0), s'' = s'
        self.rep[z][t] = s_prime
        if t == 0:
            return s_prime
        self.dcen[z][t] = 4 * s_prime - sigma
        e = 2 * s_prime - dbl
        rho = max(cfg.v_min, min(cfg.v_min + (t - c.nx) // (1 << cfg.t_inc_log2), cfg.v_max))
        rho += c.depth - cfg.omega
        sgn = 1 if e >= 0 else -1
        w = self.weights[z]
        for i, v in enumerate(u):
            v *= sgn
            if rho >= 0:
                inc = (v + (1 << rho)) >> (rho + 1)
            else:
                inc = (v * (1 << -rho) + 1) >> 1
            w[i] = max(self.wmin, min(w[i] + inc, self.wmax))
        return s_prime

    # -- 5.4.3.2 sample-adaptive entropy coder --------------------------------
    def code_k(self, z):
        g, depth = self.count[z], self.c.depth
        threshold = self.acc[z] + ((49 * g) >> 7)
        if 2 * g > threshold or depth - 2 < 1:
            return 0
        k = 1
        while k < depth - 2 and (g << (k + 1)) <= threshold:
            k += 1
        return k

    def code_update(self, z, delta):
        if self.count[z] < self.count_limit:
            self.acc[z] += delta
            self.count[z] += 1
        else:
            self.acc[z] = (self.acc[z] + delta + 1) >> 1
            self.count[z] = (self.count[z] + 1) >> 1


# ---------------------------------------------------------------------------
# Header (5.3)
# ---------------------------------------------------------------------------
_IMAGE_FIELDS = (
    ("user_data", 8), ("x_size", 16), ("y_size", 16), ("z_size", 16),
    ("sample_type", 1), ("reserved_a", 1), ("large_d", 1), ("dynamic_range", 4),
    ("encoding_order", 1), ("subframe_depth", 16), ("reserved_b", 2), ("output_word_size", 3),
    ("entropy_coder", 2), ("reserved_c", 1), ("fidelity_method", 2), ("reserved_d", 2),
    ("supplementary_tables", 4),
)
_PREDICTOR_FIELDS = (
    ("reserved", 1), ("sample_rep_flag", 1), ("bands", 4), ("prediction_mode", 1),
    ("weight_exp_offset_flag", 1), ("local_sum_type", 2), ("register_size", 6),
    ("weight_resolution", 4), ("t_inc_exp", 4), ("v_min", 4), ("v_max", 4),
    ("weight_exp_offset_table_flag", 1), ("weight_init_method", 1),
    ("weight_init_table_flag", 1), ("weight_init_resolution", 5),
)
_ENTROPY_FIELDS = (
    ("u_max", 5), ("rescale_counter_size", 3), ("initial_count_exp", 3),
    ("acc_init_constant", 4), ("acc_init_table_flag", 1),
)
HEADER_BYTES = 12 + 5 + 2


def _write_fields(bw, fields, values):
    for name, width in fields:
        bw.write(values.get(name, 0), width)


def _read_fields(br, fields):
    return {name: br.read(width) for name, width in fields}


def _header(ctx):
    cfg = ctx.cfg
    if cfg.max_error:
        raise NotImplementedError("near-lossless headers (quantization subpart) are not supported yet")
    bw = BitWriter()
    _write_fields(bw, _IMAGE_FIELDS, {
        "user_data": cfg.user_data,
        "x_size": ctx.nx % 65536, "y_size": ctx.ny % 65536, "z_size": ctx.nz % 65536,
        "dynamic_range": ctx.depth % 16,
        "encoding_order": 1 if cfg.order == "bsq" else 0,
        "subframe_depth": (ctx.interleave % 65536) if cfg.order == "bi" else 0,
        "output_word_size": cfg.output_word_bytes % 8,
    })
    _write_fields(bw, _PREDICTOR_FIELDS, {
        "bands": cfg.bands,
        "prediction_mode": 0 if cfg.full_prediction else 1,
        "local_sum_type": LOCAL_SUMS.index(cfg.local_sum),
        "register_size": ctx.register_size % 64,
        "weight_resolution": cfg.omega - 4,
        "t_inc_exp": cfg.t_inc_log2 - 4,
        "v_min": cfg.v_min + 6,
        "v_max": cfg.v_max + 6,
    })
    _write_fields(bw, _ENTROPY_FIELDS, {
        "u_max": cfg.u_max % 32,
        "rescale_counter_size": cfg.gamma_star - 4,
        "initial_count_exp": cfg.gamma0 % 8,
        "acc_init_constant": ctx.k_init,
    })
    return bw.getvalue()


def parse_header(data):
    """Return (nx, ny, nz, depth, Config, header length in bytes)."""
    br = BitReader(data)
    im = _read_fields(br, _IMAGE_FIELDS)
    pr = _read_fields(br, _PREDICTOR_FIELDS)
    en = _read_fields(br, _ENTROPY_FIELDS)
    unsupported = {
        "signed samples": im["sample_type"], "D > 16": im["large_d"],
        "non-sample-adaptive entropy coder": im["entropy_coder"],
        "near-lossless": im["fidelity_method"], "supplementary tables": im["supplementary_tables"],
        "sample representative subpart": pr["sample_rep_flag"],
        "weight exponent offsets": pr["weight_exp_offset_flag"] or pr["weight_exp_offset_table_flag"],
        "custom weight initialization": pr["weight_init_method"] or pr["weight_init_table_flag"],
        "accumulator initialization table": en["acc_init_table_flag"],
    }
    bad = [k for k, v in unsupported.items() if v]
    if bad:
        raise NotImplementedError("header uses unsupported features: " + ", ".join(bad))
    nx, ny, nz = im["x_size"] or 65536, im["y_size"] or 65536, im["z_size"] or 65536
    depth = im["dynamic_range"] or 16
    cfg = Config(
        bands=pr["bands"],
        full_prediction=pr["prediction_mode"] == 0,
        local_sum=LOCAL_SUMS[pr["local_sum_type"]],
        omega=pr["weight_resolution"] + 4,
        register_size=pr["register_size"] or 64,
        v_min=pr["v_min"] - 6,
        v_max=pr["v_max"] - 6,
        t_inc_log2=pr["t_inc_exp"] + 4,
        u_max=en["u_max"] or 32,
        gamma0=en["initial_count_exp"] or 8,
        gamma_star=en["rescale_counter_size"] + 4,
        k_init=en["acc_init_constant"],
        order="bsq" if im["encoding_order"] else "bi",
        interleave=im["subframe_depth"] or (65536 if im["encoding_order"] == 0 else 0),
        output_word_bytes=im["output_word_size"] or 8,
        user_data=im["user_data"],
    )
    if cfg.order == "bi" and cfg.interleave > nz:
        raise ValueError("sub-frame interleaving depth larger than Nz")
    return nx, ny, nz, depth, cfg, HEADER_BYTES


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def compress(scene, cfg=None, trace=None, header=True):
    """Compress a Scene. Returns the compressed image (header + body), padded to B bytes.

    trace: optional list; one tuple per sample in coding order is appended:
    (z, y, x, sigma, dhat, high_res_pred, s_hat, mapped_index, k), sigma/dhat/high_res
    being None for t = 0 and k None for the uncoded first sample of each band.
    """
    cfg = cfg or Config()
    ctx = _Ctx.build(scene.nx, scene.ny, scene.nz, scene.depth, cfg)
    pred = _Predictor(ctx, trace)
    m = cfg.max_error
    bw = BitWriter()
    hdr = _header(ctx) if header else b""
    for b in hdr:
        bw.write(b, 8)
    nx, ny, nz, depth = ctx.nx, ctx.ny, ctx.nz, ctx.depth
    samples, u_max = scene.samples, cfg.u_max
    for z, y, x in ctx.encoding_order():
        s = samples[index(ORDER_BIL, nx, ny, nz, x, y, z)]
        dbl, sigma, u = pred.predict(z, y, x)
        s_hat = dbl >> 1
        resid = s - s_hat
        t = y * nx + x
        if t == 0 or m == 0:
            q = resid
        else:
            q = (abs(resid) + m) // (2 * m + 1)
            q = q if resid >= 0 else -q
        theta = pred.theta(s_hat, 0 if t == 0 else m)
        delta = pred.map_index(q, theta, dbl)
        pred.update(z, y, x, q, s_hat, dbl, sigma, u, 0 if t == 0 else m)
        if t == 0:
            bw.write(delta, depth)
            k = None
        else:
            k = pred.code_k(z)
            hi = delta >> k
            if hi < u_max:
                bw.write_unary_zeros(hi)
                bw.write(1, 1)
                bw.write(delta & ((1 << k) - 1), k)
            else:
                bw.write_unary_zeros(u_max)
                bw.write(delta, depth)
            pred.code_update(z, delta)
        if trace is not None:
            if t == 0:
                trace.append((z, y, x, None, None, None, s_hat, delta, None))
            else:
                sg, dh, hr = pred._pending
                trace.append((z, y, x, sg, dh, hr, s_hat, delta, k))
    bw.pad_to_bytes(cfg.output_word_bytes)
    return bw.getvalue()


def decompress(data, cfg=None, dims=None):
    """Decompress a compressed image. Returns a Scene (samples in BIL order).

    For a headerless body pass cfg and dims=(nx, ny, nz, depth).
    """
    if dims is None:
        nx, ny, nz, depth, cfg, start = parse_header(data)
    else:
        (nx, ny, nz, depth), start = dims, 0
    ctx = _Ctx.build(nx, ny, nz, depth, cfg)
    pred = _Predictor(ctx)
    m = cfg.max_error
    br = BitReader(data, start)
    out = array("H", bytes(2 * nx * ny * nz))
    u_max = cfg.u_max
    for z, y, x in ctx.encoding_order():
        t = y * nx + x
        if t == 0:
            delta = br.read(depth)
        else:
            k = pred.code_k(z)
            hi = br.count_zeros(u_max)
            delta = br.read(depth) if hi == u_max else (hi << k) | br.read(k)
            pred.code_update(z, delta)
        dbl, sigma, u = pred.predict(z, y, x)
        s_hat = dbl >> 1
        mz = 0 if t == 0 else m
        q = pred.unmap_index(delta, pred.theta(s_hat, mz), dbl, s_hat, mz)
        out[index(ORDER_BIL, nx, ny, nz, x, y, z)] = pred.update(z, y, x, q, s_hat, dbl, sigma, u, mz)
    return Scene(nx, ny, nz, depth, out)


def with_options(cfg, **changes):
    """Copy of a Config with some fields changed."""
    return replace(cfg, **changes)
