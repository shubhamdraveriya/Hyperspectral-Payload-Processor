# M7: Radiation mitigation and robustness: plan

Status: **in progress** (2026-10-12). SPEC.md 10 (RAD-01 .. RAD-07); done when RAD-01 ..
RAD-07 are met and the fault-injection report is written.

## Progress

| # | State |
|---|---|
| 0 | Scene guard (`rtl/frontend/scene_guard.sv`, `tb_scene_guard`): regression passes; build met timing after pipelining the Nx·Ny·Nz product (WNS +0.175 ns). SYS-09 board re-run pending |
| 1 | UltraRAM ECC (below): regression passes; build met timing (WNS +0.100 ns; 28.7 k LUT, 33.9 k FF, 32.5 BRAM36, 56 URAM): all 56 URAM288 have ECC on (encode on the write port, decode on the read port). Board test pending (needs the job/result v4 software) |
| 2 | Uncorrectable errors flag the scenes and raise an interrupt (below): regression passes (65 tests) |

### Item 1: memory ECC (RAD-02)

- `ecc_ram`: 64-bit simple dual-port `xpm_memory_sdpram` with ECC (encode and decode),
  UltraRAM or block RAM; `sbe` / `dbe` pulse once per read that counts (`re`) and returned a
  corrected / an uncorrectable word; `inj_sbe` / `inj_dbe` on a write store an error.
- `sample_ram` (corner-turn buffers, compressor line buffer) gathers four samples per word:
  runs of consecutive writes start on a word boundary, `wlast` ends a run, a word is written
  at lane 3 or the run's end. A read of the word being gathered still returns its previous
  content, which is what the compressor's look-ahead read (Nz samples ahead) needs.
- Calibration table: layout A, one entry per word, in two banks (even / odd entries) so a
  128-bit load beat still writes in one cycle: 32 URAM (was 16). Scrubbing: a read that
  returns a corrected entry writes it back (the table is static; without this a second upset
  in the word would make it uncorrectable), unless the table was written since the read.
- `RAD` register block (`0xA00F_0000`): `STATUS.inj_pending`, `ECC_INJECT` (memory, single /
  double, go) with `ECC_INJECT_ADDR` (the word that holds this sample / entry address gets
  the error at its next write), counters `CAL_SBE` / `CAL_DBE`, `CT_SBE` / `CT_DBE`,
  `LB_SBE` / `LB_DBE` (reads, not upsets: a line-buffer word is read once per sample).
- Tests: `tb_sample_ram` (both configurations: UltraRAM latency 4, block RAM latency 2 with
  stalls; random runs, abandoned runs, reads in the gathered word, injections: about 73 k
  checked reads); `tb_hpdp_core` injects a single-bit error into a read word of each memory:
  outputs stay bit-exact, counters table 1 (written back after the first read; without
  scrubbing it would be 25), corner turn 4, line buffer 4; the front-end tests check that no
  error is ever reported without injection.

### Item 2: uncorrectable errors (RAD-06)

- A read that returns an uncorrectable word (any of the three memories) flags every scene
  in flight between the front end and the end of its packetization (32 per-scene flags,
  numbered as the packetizer's scene IDs): the scene the error hit is among them, at most
  a neighbour is flagged too. The packetizer puts the flag of the scene it is sending into
  secondary-header flag bit 0 of each packet; the ground ORs the flags over the scene
  (sw/ground reports "internal error").
- `SYS.IRQ_STATUS.ecc_uncorrectable` (bit 4, enable `IRQ_EN` bit 4) is set when any
  `RAD.*_DBE` counter increments.
- Test `sys_ecc_dbe` (vectors `fe/ecc_dbe`): a double-bit error in a calibration table entry
  that every line reads, with the correction bypassed so the data is intact: the ground
  records, with flag bit 0 in every packet, match the model bit-exactly; `RAD.CAL_DBE` =
  1 + 24 (not written back); the interrupt bit is set. With the flag forced to 0 the test
  fails (264 ground bytes differ).

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
