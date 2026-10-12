# M7: Radiation mitigation and robustness: plan

Status: **plan** (2026-10-12). SPEC.md 10 (RAD-01 .. RAD-07); done when RAD-01 .. RAD-07
are met and the fault-injection report is written.

## Facts found so far

- **SEM IP (O-8): available.** `xilinx.com:ip:sem_ultra:3.1` (UltraScale Soft Error
  Mitigation) supports Zynq UltraScale+ in production and instantiates for the XCZU7EV in
  Vivado 2025.2; `tmr_sem` 1.0 too. No licence key is needed.
- **UltraRAM ECC needs full-word writes.** UltraRAM's built-in ECC (RAD-02) works on 64-bit
  words without byte writes. Today both UltraRAM users write parts of words:
  - `sample_ram` (corner turn line buffers, compressor line buffer: 32 URAM) packs four
    16-bit samples per word and writes one lane at a time;
  - the calibration table (`radiometric_corr`, 16 URAM) packs two 33-bit entries
    (dark 16, gain 16, bad 1) per 72-bit word, and its debug port writes one entry.
- **Scene framing does not recover from lost lines** (SYS-09 board run, docs/m5_downlink.md):
  a scene short of lines is never ended by the compressor, so later scenes merge.

## Order

| # | Item | Requirement | Board |
|---|---|---|---|
| 0 | **Scene guard**: a scene that is cut short (a new first-of-scene marker before Nx·Ny·Nz samples) is padded with zero samples so the compressor ends it; counted (`CT.SCENES_PADDED`). `hpdp_run` ends a run on inactivity, not only on scene counts. Re-run SYS-09 | SYS-05, SYS-06 | yes (re-run) |
| 1 | UltraRAM ECC with SBE/DBE counters and error injection (`xpm_memory`, ECC mode both): write-gathering in `sample_ram` (four lanes, then one word), new layout for the calibration table (below) | RAD-02 | no |
| 2 | An uncorrectable error sets flag bit 0 in the packet headers of the scene it hit, and raises an interrupt | RAD-06 | no |
| 3 | Watchdogs: PL pipeline watchdog (input pending, no progress for N cycles: error interrupt, counted); PS system watchdog in `hpdp_run` | RAD-05 | test |
| 4 | SEM IP in correction mode: ICAPE3 / FRAME_ECCE4 support wrapper, status and counters in a new RAD register block (`0xA00F_0000`), command interface (error injection) from the PS | RAD-01 | yes |
| 5 | SECDED (Hamming) on the compressor's per-band state (weights, accumulators) and on the configuration snapshots; triplicated control state machines with `DONT_TOUCH` / `KEEP_HIERARCHY` and mismatch counters | RAD-03, RAD-04 | no |
| 6 | Fault-injection campaign: simulation (random flips in registers and memories, each outcome classified masked / corrected / detected / silent) and hardware (SEM configuration upsets, ECC injection); report with coverage figures | RAD-07 | yes |

Each item: RTL with a self-checking testbench, the full regression, a build with timing
met, and (where marked) a board run.

## Decision needed: calibration table layout (item 1)

ECC words hold 64 data bits; two table entries need 66. Options:

| Option | URAM | Notes |
|---|---|---|
| **A. One entry per word (recommended)** | 16 → 32 (total 40 → 56 of 96, 58 %) | Simplest; debug writes become full-word writes. Over the SPEC 5.1 URAM budget of < 50 % by 8 points: the budget line would change |
| B. Two dark+gain pairs per word (64 bits), bad flags in a separate BRAM with its own ECC | 16 (+ 4 BRAM18) | Keeps the URAM count; two memories to keep consistent, more logic |
| C. Two entries per word without ECC on the table, SECDED in fabric instead | 16 + LUTs | Custom Hamming on 66 bits: no hardware ECC counters, larger |
