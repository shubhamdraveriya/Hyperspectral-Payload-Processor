# M7: Radiation mitigation and robustness: plan

Status: **done** (2026-10-13). SPEC.md 10 (RAD-01 .. RAD-07): all met, on the board where SPEC
asks for it; fault-injection report `docs/m7_fault_injection.md`. Final build **m7h** (commit
b506d30), app job/result v7 (`build/m7g/hpdp_run_v7.elf`). Left for later: SW-05 (Ethernet
upload, M6) needs the board's Ethernet connected; O-5 (ESA / CNES decoder cross-check) needs
access to those tools.

## Progress

| # | State |
|---|---|
| 0 | Scene guard (`rtl/frontend/scene_guard.sv`, `tb_scene_guard`): regression passes; build met timing after pipelining the Nx·Ny·Nz product (WNS +0.175 ns). SYS-09 board re-run 1 (below) found scene starts lost with unfilled lines: fixed in the corner turn (`tb_corner_turn`); re-run 2 (m7d) keeps scene starts but loses whole scenes. Decision (a) taken (below); scene timestamps and packet flags now travel with the samples. **SYS-09 re-run 3 (m7g, 2026-10-13): PASS**: RAW at 76.8 Msamples/s for 1 min, 59 353 of 59 904 lines dropped (99.1 %, counted), 218 of 234 scenes reached the compressor and all 218 were packetized, 7.4 M frames with 0 code errors, 0 sync losses, no stall |
| 1 | UltraRAM ECC (below): regression passes; build met timing (WNS +0.100 ns; 28.7 k LUT, 33.9 k FF, 32.5 BRAM36, 56 URAM): all 56 URAM288 have ECC on (encode on the write port, decode on the read port). Board (build m7c, job/result v4, `run_m5.py --ecc`): single-bit errors in all three memories, 4 scenes 128×64×64: counts 1 / 4 / 4 as in simulation, capture and ground bit-exact |
| 2 | Uncorrectable errors flag the scenes and raise an interrupt (below): regression passes; since 2026-10-13 table and corner-turn errors are attributed exactly (marks on the samples). Board (m7c): double-bit error in table entry 4100, correction bypassed: `CAL_DBE` 256 (4 scenes × 64 lines, not written back), data bit-exact, all 4 scenes flagged "internal error" by the ground station (`hpdp_gs.py decode`) |
| 3 | Watchdogs (RAD-05, below): PL pipeline watchdogs (regression passes; mutation-checked; commit 1a0397f) and the PS watchdog in `hpdp_run`. **Board (m7g, 2026-10-13): PASS**: armed during a run (`--wdt`), kicked, no reset; `--wdt-test` hangs on purpose and the board resets, `CRL_APB.RESET_REASON` 0x4 (system error) |
| LNK-10 | In-System IBERT on both channels, optional (`HPDP_IBERT=1`), after the user's JSB MARK 1 setup; built (`build/m7f_ibert`). **Eye scans (2026-10-13)**, PRBS-31 traffic, increments 2/4, dwell BER 1e-6 (`docs/lnk10/`): SFP0 -> SFP1 horizontal 77.52 %, vertical 96.83 %; SFP1 -> SFP0 77.52 % / 90.48 % (JSB MARK 1: 82.95 % / 90.48 %); PRBS-31 meanwhile 1.125e12 bits, 0 errors (BER < 2.66e-12). The reverse direction works: the cable is duplex (O-1 closed) |
| 4 | SEM controller (RAD-01, below): `sem_ultra` with ICAPE3 / FRAME_ECCE4 in `hpdp_top` on clk_fr; RAD registers, monitor FIFO, counters, interrupt; `hpdp_run` hands the configuration logic to the ICAP and grants it. Register path tested with a controller stand-in (regression 67 tests). Built (m7f, WNS +0.054 ns). **Board (m7g, 2026-10-13): PASS**: observing from power-up; one upset (frame 0x9000, ECC word) corrected after 131 ms; campaigns (RAD-07 b) below and in the report |
| 5 | SECDED / TMR (RAD-03) and triplicated state machines (RAD-04), below: regression passes (tests of every mechanism with forced upsets, mutation-checked); build m7g met timing (WNS +0.103 ns, WHS +0.010 ns; 37.4 k LUT, 38.7 k FF, 38.5 BRAM36, 56 URAM, 24 DSP); app v7 `build/m7g/hpdp_run_v7.elf` reports the weight ECC and TMR counters. **Board (m7g, 2026-10-13)**: baseline over the fiber, ECC single-bit 1 / 4 / 4 and double-bit 256 (scenes flagged) as on m7c, **VER-08 10 min PASS** (2344 scenes, 0 dropped lines, 0 code errors, the same frame counts as M5); the RAD-03 / RAD-04 counters stayed 0 in every run. **Build m7h** (commit b506d30) adds TMR on the control state of the corner turn, packetizer and AOS framer, found by the campaign (report): WNS +0.087 ns, 38.2 k LUT, 39.2 k FF; board baseline and **VER-08 10 min PASS** (the same frame counts as m7g) |
| 6 | Fault-injection campaign (RAD-07), report `docs/m7_fault_injection.md`. (a) Simulation done (`tb_fault`, chain to the AOS framer): on m7g the steering registers of the corner turn, packetizer and framer were silent on 93 - 100 % of upsets (40 % of all unprotected trials), so m7h triplicates them (decision 6). Final, m7h: protected classes 820 trials, **0 silent** (567 corrected, 238 detected, 15 masked; all 220 upsets in triplicated state corrected); remaining unprotected datapath registers 108 of 480 silent (22.5 %), accepted as residual risk (decision 7). (b) ECC injection on the board (m7c, m7g, rows 1, 2, 5) and **SEM campaigns on the board (m7g, 2026-10-13)**: 200 upsets in frames' ECC words, 158 corrected in 125 - 153 ms (mean 138), 42 not seen by the controller (masked or unused frames, undone), 0 uncorrectable, scenes bit-exact; 50 upsets in design bits, 39 corrected, 11 not seen, the scenes after them bit-exact, no stall |

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

### Item 0: SYS-09 re-run 1 (scene guard build m7a)

RAW mode, 234 scenes 512×256×150 at 76.8 Msamples/s: the link and the ground kept up
(0 sync losses, 0 code errors), 55 927 of 59 904 lines dropped (SYS-09 shows drops), but
28 852 of them were lost without a fill line, and when such a line was a scene's first
line, the scene's first-of-scene marker was lost with it. The scene guard then counted
the next scene's lines into the short scene: 217 compressor scenes for 234 sensor
scenes, one timestamp queue overflow (the queue gained one entry per merge), and
`hpdp_run` timed out waiting for scenes that no longer existed.

Fix (commit e522551): the corner turn passes a lost line's first-of-scene marker to the
next line it sends, so a scene start is only lost with a whole scene. `tb_corner_turn`
checks it under sustained overload (471 of 900 lines lost, 155 of them first of a
scene) and fails without the fix.

Why so many lines are dropped (93 %, the link carries 93 % of the raw rate): in RAW
mode a fill line or scene-guard padding costs as much downlink time as a real line, so
the overload never clears; fill descriptors queue ahead of real lines and hold the two
buffers, so most real lines find no free buffer. The requirement (lines dropped, counted)
is met; making RAW mode keep more real lines (no fill lines when the compressor is
bypassed) is left as a possible improvement.

### Item 0: SYS-09 re-run 2 (build m7d, marker fix) and the RAW overload decision

Same run: the link and the ground clean again (0 sync losses, 0 code errors), the
downlink busy with science data for the first ~60 s, then idle frames while
`hpdp_run` waited. But 59 353 of 59 904 lines dropped (99.1 %), 55 814 of them without
fill: 16 of the 234 scenes kept no line at all, so their starts merged into the next
scene's; 218 scenes packetized, one timestamp queue overflow, and the run timed out.

The cause is the cost model of RAW mode: a fill line, or the scene guard's padding of a
short scene, costs as much downlink time as a real line (in compressed mode zeros cost
almost nothing). With the input 7.5 % above the link, the queue never drains and the
fill / padding traffic crowds out the real lines: now that every scene start is kept,
every short scene is padded, which made it worse (99.1 % instead of 93 %).

SYS-09 itself ("RAW mode shows line drops") is met; the questions are what RAW mode
should deliver under overload and what `run_m5.py` should check:
- (a) Accept: RAW mode is a demonstration that raw data exceeds the link. Change the
  check to "lines dropped, link and ground clean, every scene that reaches the
  compressor is complete"; let `hpdp_run` end a RAW run on inactivity instead of the
  scene count.
- (b) Drop whole scenes in RAW mode: once a line of a scene is dropped, drop the rest of
  that scene (no fill lines, no padding); scenes that get through are complete and real,
  and about 93 % of them would get through instead of < 1 % of the lines. Needs the
  corner turn to cancel a partly sent scene, so the compressor side must be able to
  abandon an image (or the decision is taken at the scene start from the queue state).

### Item 4: SEM controller (RAD-01)

- `rtl/rad/sem_support.sv`: `sem_ip` (sem_ultra 3.1, mitigation and testing, no classification) with
  ICAPE3 and FRAME_ECCE4, in `hpdp_top` on clk_fr (31.25 MHz from the board oscillator, so scrubbing
  does not depend on the PS clocks). Nothing else uses the ICAP: `cap_gnt` is the software grant,
  `cap_rel` low. The monitor interface is passed on byte by byte and never back-pressures the
  controller; its receive side is unused (commands go through the command port).
- `hpdp_core`: the controller's signals arrive through block design ports (`clk_sem`, `sem_*`) and
  cross to clk_ctrl: configuration and commands as a snapshot with a sequence count (one
  `command_strobe` per new count, when not busy), status and counters back as a snapshot, monitor
  bytes through an asynchronous FIFO (2 KB; bytes that find it full are counted).
- Registers (RAD block): `SEM_CTRL.grant`; `SEM_CMD` (PG187 codes: 0xE Idle, 0xA Observation, 0xF
  Detect only, 0xD Diagnostic scan, 0xB reset, 0xC injection at `SEM_INJ_ADDR` = frame, word, bit);
  `SEM_STATUS` (the ten status outputs, busy, ICAP available); `SEM_MON` (one report byte per read);
  counters `SEM_CORRECTIONS`, `SEM_UNCORRECTABLE`, `SEM_HEARTBEATS`, `SEM_MON_DROPPED`; interrupt
  `SYS.IRQ_STATUS.sem_uncorrectable`.
- `hpdp_run` (job/result v6): at start-up clears CSU `pcap_ctrl.PCAP_PR` (the configuration logic
  goes from the PCAP to the ICAP, PG187 "Zynq UltraScale+ MPSoC Considerations"), grants the ICAP and
  waits for Observation; a job can inject a bit error before the scenes (Idle, injection,
  Observation, wait up to 5 s for the correction) and echoes the controller's reports on the UART.
  An injection into a masked frame is ignored by the controller (no correction): try another frame.

### Item 5: SECDED / TMR (RAD-03) and triplicated state machines (RAD-04)

- **Compressor weights** (396 bits per band, block RAM): `ecc_ram` with `WIDTH` 448 (padded to a
  multiple of 64), block RAM ECC (SECDED); every band's weights are rewritten after each use, so a
  corrected word goes back corrected. Counters `RAD.W_SBE` / `W_DBE`; an uncorrectable read flags
  the scenes in the compressor (RAD-06), as line-buffer errors do. One BRAM36 more than before.
- **Coder accumulators and neighbourhood memories** (LUT RAM, read asynchronously):
  `tmr_ram`, three copies (separate instances with `DONT_TOUCH`) and a bitwise vote on read. A
  Hamming decoder in these paths (syndrome, error position, correction) would add five or six
  logic levels at 250 MHz; the vote adds one. Each band's entries are rewritten with every
  sample of the band, so a bad copy heals itself. Reads where the copies disagreed:
  `RAD.TMR_CMP`.
- **Configuration registers**: the register block in three copies (`hpdp_regs_tmr`), every
  AXI4-Lite transaction to all three, every output voted (configuration, read data, handshake),
  so an upset is masked on reads too; `RAD.TMR_REGS` counts disagreements; a copy heals when the
  register is written again (`hpdp_run` rewrites the configuration for every job). The clock
  domain copies of the configuration (`cdc_bus`) are resent continuously and need nothing more.
- **Control state machines** (RAD-04) of the flight path: corner turn (`wb`, `full`, `rd_on`),
  scene guard (`pad`), compressor packer (`state`), packetizer (`busy`, `in_hdr`), AOS framer
  (`os`). Three copies with `DONT_TOUCH`, logic reads the vote, every assignment goes to all
  three, and every cycle each copy reloads the voted value (an upset copy heals at the next
  clock). `RAD.TMR_FSM` counts disagreements. The ground-side state machines (link receiver,
  frame sync, ground capture) and the sensor emulator are test equipment, not payload, and stay
  as they are; the transmit chain (RS encoder, CADU framer, link TX) is driven by counters.
- Tests: `tb_tmr_ram` (random upsets in any copy: masked, flagged, healed by the next write);
  `tb_regs_tmr` (an upset in each copy: configuration output and register reads unchanged,
  flagged, healed by a rewrite); `tb_corner_turn` forces 30 upsets into one copy of `wb`, `full`
  or `rd_on` during the overload run (every output exact, each healed at the next clock; without
  the refresh, 6 375 mismatch cycles stay and the test fails); the compressor tests check that
  no ECC or TMR event appears without a fault.

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


## Decisions of 2026-10-13 (taken on the user's behalf)

The user asked for work that needs no board and for decisions to be taken for them.

1. **SYS-09 RAW overload: option (a).** RAW mode demonstrates that raw data exceeds the
   link (the requirement); it is not made to deliver more. Option (b) (drop whole scenes)
   would need the compressor to abandon images and buys nothing the requirement asks for.
   `hpdp_run` ends a run once the sensor is done and the compressor and packetizer have
   made no progress for a second, and reports how many scenes reached the compressor;
   `run_m5.py` judges RAW runs by drops, a clean link and ground, and complete delivery of
   every scene the compressor got.
2. **Scene timestamps and packet flags travel with the samples** (found while deciding 1):
   numbering scenes at the front end gave every scene after a lost one the wrong
   timestamp, a timestamp queue overflow, and a shifted RAD-06 flag window. Now each line
   carries its scene's timestamp and an uncorrectable-error mark through the corner turn
   (fill lines and scene-guard padding marked as fill); the timestamp queue and the
   per-scene flags live at the compressor input, numbered as the packetizer's scene IDs.
   The lines-dropped flag (bit 1) is exact too, instead of "since the first packet header".
3. **RAD-05 PL watchdogs** on the compressor (input or output pending), the downlink
   (packetizer output pending) and the ground capture (records pending for the DMA): a
   stage that moves nothing for `RAD.WD_TIMEOUT` clk_sys cycles (default 2^26, 0.27 s) is
   counted (`RAD.WD_CMP/DL/GR`), shown in `RAD.STATUS.wd_stalled` and raises
   `SYS.IRQ_STATUS.stall`; `hpdp_run` ends the job with `ST_STALL` (a full ground buffer
   keeps `ST_GR_OVERFLOW`). The system test runs with a 20 000-cycle timeout (no false
   stall under random back-pressure) and then forces a ground stall, which is caught.
4. **RAD-05 PS watchdog: per job, not always on.** The FPD system watchdog (10 s), kicked
   whenever `hpdp_run` reads the time (every wait loop does), armed by job flag `JOB_WDT`
   (`run_m5.py --wdt`) and stopped at the end of the job, so debug sessions that halt the
   processor are not reset. No PMU firmware runs in the JTAG flow, so `hpdp_run` routes the
   watchdog error to a system reset itself (UG1087: `PMU_GLOBAL.ERROR_EN_1` and
   `ERROR_SRST_EN_1`, bit `FPD_SWDT`). Test: `run_m5.py --wdt-test` (the app hangs on
   purpose; `CRL_APB.RESET_REASON.pmu_sys_reset` must be set afterwards).
5. **LNK-10: In-System IBERT in the HPDP design**, as SPEC asks, optional at build time so
   the normal bitstream carries no debug cores; settings from the user's working setup on
   this board (JSB MARK 1/gth_sfp_link).
6. **TMR on the steering state found by the fault campaign (build m7h).** The corner turn's
   write / read addresses, counters and line descriptor, the packetizer's header index,
   byte count, sequence count, packet index and timestamp, and the AOS framer's zone
   position, first header pointer, buffer flags, frame count and padding were silent on
   nearly every upset in simulation (an error there moves every later sample, header or
   frame, some until reset). They are triplicated as in RAD-04: +0.7 k LUT, +0.5 k FF, WNS
   +0.087 ns; every one of 220 simulated upsets in them was corrected.
7. **Residual risk accepted:** upsets in the datapath registers (samples, residuals, sums,
   the coder's bit accumulator) are silent about one time in four in simulation. Per bit,
   flip-flops upset far less often than configuration memory (which SEM corrects), an upset
   corrupts at most the scene in flight, and triplicating the datapath would triple its
   flip-flops at 250 MHz. First candidate if a mission needs more: the packer's 128-bit bit
   accumulator (85 % silent).

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
