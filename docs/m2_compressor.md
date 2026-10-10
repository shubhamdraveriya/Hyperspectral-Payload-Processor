# M2: CCSDS 123.0-B-2 compressor RTL

`rtl/ccsds123/`: lossless CCSDS 123.0-B-2 compressor, BIP order, one sample per
clock, bit-exact with the Python model (`model/hpdp_model/ccsds123.py`).

## Interfaces (`c123_core`)

| Port | Description |
|---|---|
| `s_axis_*` | 16-bit samples in BIP order; `tuser` = first sample of a scene |
| `m_axis_*` | 64-bit words of compressed image, first byte in `tdata[7:0]`, `tkeep` on the last word, `tlast` per image |
| `cfg` | `c123_pkg::cfg_t`: image size, D, P, prediction mode, local sum, Ω, R, ν_min/ν_max/t_inc, U_max, γ0, γ*, K, B, raw mode, user data. Static while a scene is processed |
| `cfg_error` | configuration outside what the core supports |

Build parameters: `NX_MAX` (512) and `NZ_MAX` (256). `NZ_MIN` = 16.

## Architecture

Lossless coding makes the sample representatives equal to the samples, so every
local difference, including those of the previous bands, depends only on input
data. There is no sample-to-sample feedback; the only loops are the per-band
weights and entropy-coder state, which in BIP order are reused Nz samples later.
That allows a deep pipeline at one sample per clock.

| Stage | Module | Work |
|---|---|---|
| S1–S3 | `c123_neighborhood` | position counters; previous-row line buffer (1R1W, read Nz samples ahead of the write); per-band LUTRAMs for W, N (from the previous pixel's NE), NW, and the row's first pixel |
| S4–S5 | `c123_localdiff` | local sum (4 types), directional and central differences, last-15 shift register of the previous bands' central differences |
| P1–P15 | `c123_predictor` | weight RAM (default initialization substituted at t = 1, so no clearing), 18 products, adder tree, mod-R wrap, clipping, double-resolution prediction, mapped index, sign-LMS weight update and write-back |
| C0–C4 | `c123_coder` | per-band counter and accumulator (LUTRAM), k, length-limited GPO2 codeword (or D raw bits at t = 0 / raw mode) |
| — | `c123_packer` | 19-byte header, MSB-first packing into 64-bit words, zero fill to a byte and to B bytes, `tlast` |

A 32-entry codeword FIFO decouples the pipeline from the packer. The pipeline
advances as a whole on `ce`, which drops when that FIFO is nearly full; bubbles
flow through when no sample is offered.

## Verification

`tb/ccsds123/tb_c123_core.sv` runs vectors produced by `tb/ccsds123/gen_vectors.py`
from the Python model. It drives input with random gaps, applies random output
back-pressure, checks every output byte and `tlast` position, and checks every
mapped residual against the model trace (so a mismatch names the exact sample).

28 configurations, two scenes each, all bit-exact:

| Area | Tests |
|---|---|
| prediction | full / reduced, all four local sums, P = 0, 1, 3, 15, Ω = 4, 13, 19 |
| register size | R = 32 with D = 16, Ω = 14 (76 % of predictions wrap), R = 64 |
| weight update | ν_min = −6, ν_max = 9, t_inc = 2^4 on the shared-texture scene |
| entropy coder | U_max = 8, 18, 32; γ0 = 8, γ* = 11; K = 0 |
| samples | D = 2, 8, 12, 16 |
| output | B = 1, 3, 5, 8; raw (bypass) mode |
| geometry | Nx = 1, Ny = 1, Nx = Nz = 64 (testbench maximum), Nz = 16 (minimum) |
| flow control | 60 % input valid with 35 % output ready |

Run: `vivado -mode batch -source tb/run_sim.tcl` (29 tests including the skid buffer).

## Implementation (out of context, `vivado/ooc_c123.tcl`)

XCZU7EV-2, NX_MAX = 512, NZ_MAX = 256, 250 MHz:

| | Result |
|---|---|
| WNS / WHS | +0.160 ns / +0.019 ns (met) |
| LUT / FF | 13.6 k (5.9 %) / 6.1 k (1.3 %) |
| DSP48E2 | 19 |
| BRAM | 5.5 (weight RAM) |
| UltraRAM | 32 (line buffer, one 16-bit sample per 72-bit word) |

Worst path: the line buffer's UltraRAM read mux.

## Open items

- Pack 4 samples per UltraRAM word in the line buffer: 32 → 8 URAM (SPEC budget).
- Near-lossless mode (SPEC CMP-10) and the hybrid entropy coder are not implemented.
- Register block (SystemRDL) and counters (CMP-09) come with system integration (M4).
