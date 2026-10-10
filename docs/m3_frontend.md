# M3: Sensor front end RTL

`rtl/frontend/`: sensor emulator, radiometric correction and corner turn,
bit-exact with the Python models and verified together with the M2 compressor.

```
PS DDR4 ─DMA─► sensor_emulator ──► radiometric_corr ──► corner_turn ──► c123_core
       128-bit  clk_sys → clk_sensor → clk_sys    clk_sys (BIL)    clk_sys (BIL → BIP)
```

`sensor_frontend` wraps the three blocks. Sample streams follow SPEC 5.4:
`tuser[0]` first sample of a scene, `tuser[1]` first sample of a line, `tlast`
last sample of a line. Only the corner turn output can be back-pressured;
everything before it runs at the sensor's pace and counts any overflow (SYS-05).

## Blocks

### `sensor_emulator` (SPEC 6.1)

| Part | Detail |
|---|---|
| Prefetch | 32 KB `xpm_fifo_async` (2048 × 128, block RAM), `clk_sys` → `clk_sensor`; 128 → 16 gearbox, sample 0 in `[15:0]` |
| Line timing | a line starts every `line_period` `clk_sensor` cycles and its Nx·Nz samples leave back to back. A line takes Nx·Nz + 1 cycles; a shorter period makes the next line start late, counted in `period_overruns` |
| Underrun | a sample missing from the prefetch FIFO is sent as 0 and counted, so lines keep their length |
| Sources | DMA, ramp `(x + z + y) mod 2^D`, LFSR (taps 0xB400, seeded 0xACE1 at every scene start), as in `model/hpdp_model/sensor.py` |
| Output | 32 × 19 distributed `xpm_fifo_async` back to `clk_sys`; it drains 2.5 × faster than it fills, so `dropped_samples` stays 0 unless the clocks are wrong |
| Control | setting `cfg.run` starts a new scene; clearing it stops after the current line |
| Status | 24-bit line timestamp (free-running count of line starts), scene timestamp (line timestamp at the scene's first line, for the packet header, SPEC 6.5), lines, scenes, underruns, period overruns, drops |

### `radiometric_corr` (SPEC 6.2)

| Part | Detail |
|---|---|
| Table | two 36-bit entries `{bad, gain, dark}` per 72-bit word (one UltraRAM word), 16 URAM at 512 × 256. The table-load stream carries the `.cal` records as stored in memory, two 8-byte records per 128-bit beat, from entry 0; `tlast` ends the table |
| Addressing | the entry of sample x of band z is z·Nx + x, the sample's position in its BIL line: a counter reset at every line start |
| Pipeline | table read (4 cycles), x − dark, `(x − dark) × gain + 2^13` in one DSP48E2 (A·B + C, fully registered), `>>> 14` and clamp, bad-pixel window |
| Bad pixels | a sample waits in a one-sample window until its right neighbour arrives (not needed for the last pixel of a band); left neighbour = the previous output's pre-replacement value. One sample per clock in, one out |
| Bypass | output = input, no replacement |
| Status | clamp events and replaced pixels of the current scene (reset at the scene's first sample) |

### `corner_turn` (SPEC 6.3)

| Part | Detail |
|---|---|
| Buffers | two line buffers in one `sample_ram` (four samples per 64-bit UltraRAM word), 16 URAM at 512 × 256 |
| Write | BIL sample i of the line at address i = z·Nx + x, never stalls |
| Read | address z·Nx + x with z inner (BIP), credit-based into a 16-entry FIFO, then a skid buffer (registered AXI-Stream output) |
| Ordering | every line, written or dropped, queues a descriptor in arrival order; the reader follows the queue |
| Drops | a line that finds both buffers full is dropped and replaced by a fill line of zeros at the output, so the scene keeps its geometry and the compressor stays aligned with scene boundaries (CT-04, updated in SPEC). If the descriptor queue is full too, the line is lost without fill (`unfilled_lines`; the scene is then corrupt) |
| Status | lines out, dropped lines, unfilled lines |

Why fill lines: without them a dropped line shifts every following line by one,
and the next scene's start flag arrives mid-scene in the compressor, which then
writes a new header into the unfinished image and loses alignment for good.

## Verification

`tb/frontend/tb_frontend.sv` runs `sensor_frontend` and `c123_core` together on
vectors from `tb/frontend/gen_vectors.py` (`clk_sys` 250 MHz, `clk_sensor`
100 MHz with an unrelated phase). It loads the calibration table, streams the
raw scenes by "DMA" with random gaps, runs the sensor for every line, and checks:

- every sample and tag at the emulator, correction and corner-turn outputs;
- every compressed byte and `tlast` position;
- the status counters (timestamps, clamp and replacement counts against the model).

The compressor output is back-pressured randomly. The calibration has 4 % random
bad pixels plus fixed ones at both line edges, a bad pair, a bad run of three and
a bad pixel next to the first column.

| Test | What it covers |
|---|---|
| `dma_basic` | DMA source, dark/gain/bad-pixel correction, two scenes |
| `dma_stalls` | 30 % DMA valid, 50 % compressor output ready |
| `clamp` | noise through a strong calibration: 244 clamps at 0 and 2^D − 1 in a scene |
| `bypass` | correction off |
| `d16` | D = 16 |
| `ramp`, `lfsr` | built-in patterns (LFSR with D = 16, bypass) |
| `min_period` | line period = one line + 1 cycle: no overrun |
| `overrun` | line period shorter than a line: lines start late (18 overruns), data intact |
| `one_column` | Nx = 1 (every pixel is at both line edges) |
| `max_dims` | Nx = Nz = 64, the testbench maximum (full line buffers) |
| `drop` | compressor output 4 % ready: 17 of 24 lines dropped and replaced by fill lines; the compressor still closes exactly one image per scene |

All 12 pass, and the full regression is 41 tests (skid buffer, 28 compressor
configurations, 12 front-end tests). `model/tests/test_sensor.py` covers the
pattern model (LFSR period 65535, ramp, per-scene seeding).

xsim note: a `push_back` that is the bare body of a `while` loop pushes X;
the testbench file readers keep `begin`/`end`.

## Implementation (out of context, `vivado/ooc_frontend.tcl`)

XCZU7EV-2, NX_MAX = 512, NZ_MAX = 256, `clk_sys` 250 MHz, `clk_sensor` 100 MHz:

| | Result |
|---|---|
| WNS `clk_sys` / `clk_sensor` | +0.651 ns / +6.93 ns, WHS +0.023 ns (met) |
| LUT / FF | 1.1 k / 1.3 k |
| DSP48E2 | 1 |
| BRAM | 7.5 (DMA prefetch FIFO) |
| UltraRAM | 32 (16 calibration table, 16 corner turn) |

The table and line buffers are 8-deep UltraRAM cascades. With a 2-cycle read
they had one output pipeline stage and the worst path ran through 7 cascaded
URAMs (+0.112 ns). `sdp_ram` and `sample_ram` now take a `LATENCY` parameter;
with 4 cycles synthesis absorbs the 3 recommended cascade stages and the slack
rises to +0.651 ns (worst path: last URAM → lane select and x − dark → DSP).

`report_cdc` lists only the XPM FIFO internals (gray-coded pointers, LUTRAM
data path) and the `cfg` inputs, which are unclocked out of context.

## Open items (M4)

- Register blocks: `cfg` will come from `clk_ctrl` and must cross into `clk_sys`
  and `clk_sensor` (quasi-static, written while stopped: synchronized or waived
  with a reason, SYS-08); status counters cross back.
- RC-02 AXI4-Lite access to the calibration table, RC-06 double buffering (*may*).
- Line-drop behaviour on the board in `RAW` mode (SYS-09).
