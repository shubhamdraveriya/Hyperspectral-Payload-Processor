# SYS-04: the `NX_MAX` = 1024 build

SPEC SYS-04: "`NX_MAX` = 512 (shall scale to 1024 by changing the parameter)". The
512-pixel design did not scale as it stood: the calibration table, one entry per
64-bit UltraRAM word, would have needed 64 UltraRAMs at 1024 × 256, 112 of the
XCZU7EV's 96 with the corner turn and the line buffer. This note describes the
changes (commit d2eda42) and the 1024 build, m7i_1024b.

## Building

```
set HPDP_NX_MAX=1024              (PowerShell: $env:HPDP_NX_MAX = "1024")
set HPDP_BUILD_DIR=<repo>\build\m7i_1024b
vivado -mode batch -source vivado/build.tcl
```

`vivado/bd/system.tcl` passes `HPDP_NX_MAX` and `HPDP_NZ_MAX` to the `hpdp_core_bd`
parameters (default 512 and 256) and stops above 2¹⁸ table entries: `RC.TBL_ADDR`,
the table debug address, is 18 bits.

## Changes

| Item | 512 × 256 (m7h) | Above 512 × 256 |
|---|---|---|
| Calibration table (`radiometric_corr`, `TABLE_PAIRS`) | layout A: `{bad, gain, dark}` of one entry per 64-bit word, 32 UltraRAMs | layout B, automatic above `NX_MAX × NZ_MAX` = 131 072: `{gain, dark}` of two neighbouring entries per word (UltraRAM, N/2 words) and the bad flags in a block RAM of their own (64 per word, N/64 words), both with ECC. A debug write (`RC.TBL_CMD`) reads the entry's words and writes them back, since ECC words are written whole; each memory is scrubbed on its own |
| ECC injection address (`RAD.ECC_INJECT_ADDR`, `ecc_inj_t`) | 18 bits were enough | 20 bits: the corner-turn buffers of a 1024 build hold 2 × 2¹⁸ samples. Job `ecc_inject` words keep their layout (bits 19:18 were reserved), so the job format stays v7 |
| `SYS.LIMITS` (0x60, new) | a 512 build of this commit reads 512 / 256 (m7h predates it and reads 0) | reads the build's `NX_MAX` / `NZ_MAX`. `hpdp_run` checks jobs and console runs against it (512 × 256 on older bitstreams, which read 0 there) and prints it in `status` |

The first 1024 attempt (build/m7i_1024) failed in synthesis on the injection address
(`sample_ram`: part-select [18:2] of an 18-bit address); the fix is the 20-bit
address above.

## Verification

- Layout B runs in the regression at the testbenches' small size, forced with
  `TABLE_PAIRS = 1` (`tb/frontend/tb_frontend_pairs.sv`): the five front-end tests
  `fe_pairs_*` (sample stream, stalls, clamping, maximum dimensions, line drops; the
  table debug port included) and the table's ECC classes of the fault campaign,
  `fault_pairs_sbe` (10 single-bit trials, all corrected) and `fault_pairs_dbe`
  (10 double-bit trials, all detected and flagged).
- `sys_nx1024` (`tb/system/tb_hpdp_core_1024.sv`): the system test with `hpdp_core`
  at the build's own size, `NX_MAX` 1024 and `NZ_MAX` 256 (table in pairs, full-size
  corner-turn and line buffers), on a 1024 × 3 × 24 scene through the registers, the
  compressor and the downlink to the ground records; it also reads `SYS.LIMITS`
  (1024 / 256; the other system tests read their 64 / 64). Writing it found a 32-bit
  overflow in the testbench's cycle-count bound at 1024-pixel lines (testbench only).
- The full regression passes: 87 tests (86 on d2eda42, then `sys_nx1024`).

## Build m7i_1024b (`NX_MAX` 1024, `NZ_MAX` 256)

| | m7h (512) | m7i_1024b (1024) |
|---|---|---|
| LUT | 38 151 (16.6 %) | 38 906 (16.9 %) |
| FF | 39 231 (8.5 %) | 40 120 (8.7 %) |
| BRAM36 | 38.5 (12.3 %) | 46.5 (14.9 %) |
| URAM | 56 (58.3 %) | **80 (83.3 %)**: corner turn 32, table 32, line buffer 16 |
| DSP | 24 | 24 |
| Setup WNS / hold WHS | +0.087 / +0.010 ns | **+0.012 / +0.010 ns**, all 11 clocks met; worst on `clk_sys` (250 MHz) |
| Bus skew (36 constraints) | worst slack +2.94 ns | worst slack +2.85 ns |
| `report_cdc`, `report_methodology` | (VER-09) | identical to m7h: no unsafe or unknown crossings; the same 5 waived LUTAR-1 checks and 2 MMCM advisories |

Build ID 0x9018F99A, 22 minutes, XSA in `build/m7i_1024b/hpdp.xsa`.

The 250 MHz margin is thin (+12 ps) but met; the same RTL at 512 closes with +87 ps.
A 1024 × 512 build would need 160 UltraRAMs and does not fit the XCZU7EV; the build
script's limit (2¹⁸ table entries) allows 1024 × 256 and 512 × 512.

## Not done yet

A board run of the 1024 bitstream: a 1024-pixel real scene through the chain, the
table in pairs on the hardware, an ECC injection above 2¹⁸ in the corner turn, and
`SYS.LIMITS` reading 1024 / 256. Commands and expected results:
[post_m7_board_tests.md](post_m7_board_tests.md) §3.
