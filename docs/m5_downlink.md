# M5: Downlink chain and optical link

Status: **done** (2026-10-12). The whole chain is integrated in `hpdp_core` and `hpdp_top`
and matches the Python model in simulation (VER-05). On the board, PRBS-31 over the fiber
ran 3.375 × 10^12 bits without an error (VER-06), and scenes sent over the fiber were
reconstructed bit-exactly at the ground at BER 0, 1e-4 and 1e-3 (VER-07). Open: the
eye scan with In-System IBERT (LNK-10, *should*).

```
                         ┌─► AXI DMA S2MM (compressed scenes, CAPTURE_CMP)
compressor ─► fork ──────┤
 (SYS.MODE)              └─► space packetizer ─► AOS framer ─► RS(255,223) I=4 ─► randomizer + ASM
                                                                                        │
     ┌──────────────────────────────────────────────────────────────────────────────────┘
     ▼
error injector ─► link_layer TX ─► GTH SFP0 + SFP1 ══ fiber ══ GTH SFP1 (or SFP0) ─► link_layer RX
                  clk_sys → clk_link                                   clk_link → clk_sys │
     ┌─────────────────────────────────────────────────────────────────────────────────────┘
     ▼
frame sync + derandomizer ─► gr_capture ─► AXI DMA "dma_gr" S2MM (1024-byte records)
```

## hpdp_core additions (`rtl/system/hpdp_core.sv`)

| Part | Detail |
|---|---|
| Fork | the compressor output goes to the capture DMA, the downlink chain, both or neither (`SYS.MODE.capture`, `.downlink`); with both, the compressor moves on when both have taken a word |
| Scene timestamps | `sensor_frontend` counts lines in `clk_sys` at the emulator output; at each scene's first sample the count (the emulator's `SCENE_TIMESTAMP`, SE-05) is queued (16 deep) for the packetizer's secondary header. Queued only while `MODE.downlink` is set; an overflow is counted (`PKT.TS_OVERFLOWS`) |
| Lines-dropped flag | a corner-turn drop sets packet header flag bit 1 for the scene in progress |
| Link domain | `clk_link` = GTH TX user clock (62.5 MHz). The RX data of both channels runs on it too, through the GTH elastic buffers. Held in reset until the transceivers' TX reset is done; its own `cdc_bus` snapshots; counted in `cfg_pending` only while the link is up |
| Channel select | both channels transmit the same words; `LNK.CTRL.rx_sel` picks the receiving channel (default SFP1, fed by the fiber from SFP0); `fabric_loopback` feeds the receiver from the transmitter inside the FPGA |
| PRBS | `LNK.CTRL.prbs_sel` drives the GTH generators and checkers (5 = PRBS-31); `PRBS_ERRORS` counts error words, `PRBS_WORDS` (48 bit) counts words checked while locked; `CNT_CLEAR` also pulses `RXPRBSCNTRESET`, and `PRBS_INJECT` pulses `TXPRBSFORCEERR` |
| Elastic buffer | clock corrections counted; an under/overflow is counted and followed by a short `RXBUFRESET` of that channel |
| Interrupts | `SYS.IRQ_STATUS` gains link lock loss and frame sync loss |
| Errors | `SYS.STATUS.error` also covers timestamp overflows, link RX FIFO overflows and dropped ground records |

The register window grew to 1 MB for the new blocks, so the DMA instances moved to
`0xA010_0000` and `0xA011_0000` (SPEC.md 8.1).

### New register blocks

| Block | Base | Contents |
|---|---|---|
| PKT | `0xA005_0000` | APID, user bytes per packet; packets, scenes, timestamp overflows |
| AOS | `0xA006_0000` | idle frames on/off, SCID / VCID, science-frame spacing (clk_sys cycles, AOS-02); science frames, idle frames, CADUs |
| EI | `0xA007_0000` | mode, BER threshold, seed + seed load, burst length, period, ASM bits; injected bits |
| LNK | `0xA008_0000` | SFP TX enable, GTH resets, fabric loopback, receiving channel, GTH loopback, polarities, PRBS; status (TX/RX ready, aligned, LOCKED, PRBS locked, CPLL lock, power good); clock-correction interval; counters |
| GR | `0xA009_0000` | capture on/off, skip idle frames, ASM tolerance, flywheel, records per DMA transfer; sync state; counters |

## Ground capture (`rtl/ground/gr_capture.sv`)

- Packs the frame sync's 1024-byte records (1020 derandomized codeblock bytes + status
  word) into 64-bit words for the ground DMA; a 16 KB FIFO absorbs DMA re-arm gaps,
  and when it is nearly full the frame sync drops (and counts) whole records.
- Idle frames are not captured. The decision uses the VCID field before RS decoding,
  so it is made by distance: nearer (in bits) to 63 than to the science VCID. An exact
  match on 63 would let about 0.6 % of the idle frames through at BER 1e-3 (any of
  6 bits hit), hundreds of junk records per second; with the distance rule a frame is
  misjudged only with 3 or more errors in those 6 bits.
- The DMA runs in simple mode, so tlast ends a transfer: after every `GR.XFER` records,
  and after a partial group once no record has been captured for 2^16 cycles. The last
  word of a record that does not complete a group is held back until the next record
  starts (no tlast) or the timeout (tlast), so a record is never split.

## Top level (`rtl/system/hpdp_top.sv`, `vivado/constraints/hpdp.xdc`)

| Item | Detail |
|---|---|
| Transceivers | `gt_link` (UltraScale wizard, `vivado/ip/gt_link.tcl`): X0Y10 = SFP0, X0Y11 = SFP1, 1.25 Gbaud on each CPLL, 8b/10b, K28.5 alignment into byte 0, clock correction on {K28.5, D16.2}, settings from the SFP link already brought up on this board |
| Reference clock | 156.25 MHz Si570 (through an SI53340) on MGTREFCLK1 of quad 226, pins U10/U9; no I2C setup (O-2) |
| Free-running clock | 125 MHz board clock (H9/G9) / 4 = 31.25 MHz, BUFGCE_DIV; `FREERUN_FREQUENCY` = 31.25 |
| Resets | 2 ms power-on reset on the free-running clock, then `LNK.CTRL.gt_reset` / `rx_reset` from software |
| SFP control | `TX_DISABLE_B` on AE22 / AF20 (high = transmitter on), from `LNK.CTRL.tx_enable`. `LOS` and `TX_FAULT` have no PL pins on the ZCU106 (O-3) |
| Block design | second AXI DMA `dma_gr` (S2MM only) on HP0; the `gt_*` pins of `hpdp_core` are block design ports |

## Verification

| Level | Tests |
|---|---|
| Blocks | `dl_*` (packetizer → CADU framer, 7), `gr_*` (error injector → frame sync, 9), `link` (link layer with a GTH user-interface model) |
| System (VER-05) | `sys_*`: the system test runs each acquisition twice with the compressor output going to the capture DMA **and** down the downlink chain, through a transceiver model that loops the transmitted words back on the receiving channel (or the fabric loopback), into the ground capture DMA. `tb/frontend/gen_vectors.py` builds the same downlink, error injection and frame sync with the model; every ground record is compared byte for byte, and the tlast grouping is checked |
| Host, offline | `run_m5.py --fake-board` runs the model in place of the board and checks the host's ground chain: clean, random BER, burst and ASM errors all reconstruct every scene |

| Test | Downlink | Errors | Link | Ground |
|---|---|---|---|---|
| `sys_dma_basic` | 16 CADUs, 2048-byte packets | none | SFP1 | 16 records, one per transfer |
| `sys_dma_stalls` | 16 CADUs, 300-byte packets, DMA and S2MM stalls | random, BER 1e-3 (123 bits) | SFP0 | 16 records, 3 per transfer |
| `sys_max_dims` | 20 CADUs, 1000-byte packets | 40-bit bursts every 3000 bytes | fabric loopback | the first burst destroys the first ASM: the frame sync acquires on CADU 1, as the model does; 19 records, 2 per transfer |

Full regression: 61/61. Model: 144/144 (with a test of `pipeline.ground_records`).

## Build

`HPDP_BUILD_DIR=build/m5`, `vivado -mode batch -source vivado/build.tcl` (commit abbf644,
Vivado 2025.2), `build/m5/hpdp.xsa`. The M4 outputs in `build/` are left as they were.

| Item | Result |
|---|---|
| Timing | met on all clocks: WNS +0.195 ns, WHS +0.010 ns (clk_sys 250 MHz); every `set_bus_skew` met |
| Utilization (whole design) | 28.0 k LUT (12.2 %), 32.3 k FF (7.0 %), 32.5 BRAM36 (10.4 %), 40 URAM (41.7 %), 20 DSP (1.2 %), 2 GTHE4 channels, 3 BUFG_GT. M4 was 21.3 k LUT, 20.4 k FF, 21 BRAM36 |
| `report_cdc` | no unsafe or unknown crossings once waived (`vivado/constraints/waivers.xdc`). CDC-11 in the GTH wizard: one TX-user-clock "active" flag feeds the rx_active and tx_active reset synchronizers, because the RX side runs on the TX user clock. Remaining Warning rows are all safe: the wizard's CPLL-calibration counters into `clk_fr`, and `clk_sensor` → `clk_pl_1` (as in M4) |
| `report_methodology` | 5 × LUTAR-1 waived: LUTs on asynchronous resets inside the GTH wizard, and the user-clock helper reset `~&txpmaresetdone` (AMD's multi-channel example). CLKC-40 / CLKC-56 advisories on the clk_sensor MMCM, as in M4 |
| Software | `hpdp_run.elf` builds against the XSA (Vitis 2025.2, SDT; `HPDP_VITIS_WS=~/hpdp_vitis_m5`); the BSP lists both DMAs at `0xA010_0000` / `0xA011_0000` |

The waivers were checked on the routed checkpoint; they take effect from the next build
(they change reports only, not the bitstream).

## Software

`sw/baremetal/hpdp_run` (job and result version 2, `hpdp_job.h`, `sw/host/hpdp_job.py`):

- **DOWNLINK** jobs bring the link up first (SW-03): transmitters on, the transceiver
  reset sequence if TX is not up, then up to 1 s for `LNK.STATUS.locked`, with up to
  four RX data-path resets for a receiver that came up before anything was sent.
  The scenes then run with idle frames on; the ground DMA is re-armed after each group
  of records, back to back at `GR_ADDR`. The run ends in steps: every scene packetized,
  the science-frame count settled, idle frames on and 3 more CADUs sent (so the last
  science CADU has passed the error injector), error injector off (clean idle frames),
  then every captured record in memory and none for 5 ms.
- **PRBS** jobs run PRBS-31 for the given time, print progress every 10 s, and report
  error words, words checked (20 line bits each) and the checker's lock.
- **CAPTURE** jobs are the M4 runs, unchanged (`run_m4.py` writes version 2 jobs).

`sw/host/run_m5.py` builds the scenes, computes the expected packets and frames, writes
the job and the XSDB script (JTAG only), and checks the result: the compressed scenes
as `run_m4.py` does, then the captured records through the model's ground chain
(`pipeline.ground_records`: RS decoding, deframing, packets, scenes, decompression).

## Board runs (2026-10-12)

Bitstream `build/m5/hpdp.xsa` (build ID `0x0C00D8EA`), `hpdp_run.elf` from
`~/hpdp_vitis_m5`, JTAG only. Two 1000BASE-SX modules in SFP0 and SFP1 with an OM3
duplex patch cord between them; the receiver takes SFP1 (fed by SFP0's transmitter).
Scenes: 4 synthetic 128 × 64 × 64, D = 12, DMA source, correction on, default compressor
settings (5.92 bits/sample, 2.03:1), 2048-byte packets, 760 packets in 1772 science
frames; the compressed scenes also went to the capture DMA and matched the model every
time.

| Run | Link | Result |
|---|---|---|
| Downlink, fabric loopback | inside the FPGA | 1772 records, all LOCK, 0 RS corrections; 4/4 scenes bit-exact at the ground |
| Downlink, near-end PMA loopback | GTH serializer → deserializer | same: 0 code errors, 0 RS corrections, 4/4 scenes |
| PRBS-31, PMA loopback, 60 s | | 7.5 × 10^10 bits, 16 error words, all in the first 10 s while the SFP modules were being plugged in; none in the remaining 50 s |
| PRBS-31, PMA loopback, 20 s | | 2.5 × 10^10 bits, 0 errors |
| PRBS-31, fiber, 60 s | SFP0 → OM3 → SFP1 | 7.5 × 10^10 bits, 0 errors (BER < 4 × 10^-11, 95 %) |
| **VER-06**: PRBS-31, fiber, 2700 s | SFP0 → OM3 → SFP1 | **3.375 × 10^12 bits, 0 errors: BER < 8.9 × 10^-13 at 95 %**; checker locked throughout, 1.2499 Gb/s, 0 clock corrections, 0 elastic-buffer errors |
| **VER-07**, fiber, BER 0 | SFP0 → OM3 → SFP1 | 1772 records, all LOCK, 0 code errors, 0 RS corrections; **4/4 scenes bit-exact** |
| **VER-07**, fiber, BER 1e-4 | | 2187 bits injected, 1432 RS symbols corrected, 0 uncorrectable; **4/4 scenes bit-exact** |
| **VER-07**, fiber, BER 1e-3 | | 22 200 bits injected, 14 508 RS symbols corrected, 0 uncorrectable, frame sync stayed in LOCK (60 ASM bit errors); **4/4 scenes bit-exact** |

BER sweep beyond (VER-07), same scenes:

| Injected BER | RS symbols corrected | Uncorrectable codeblocks | Scenes bit-exact |
|---|---|---|---|
| 2e-3 | 28 931 | 0 / 1772 | 4 / 4 |
| 3e-3 | 42 957 | 1 | 3 / 4: the lost frame costs only its own scene (SYS-06) |
| 4e-3 | 56 353 | 24 | 0 / 4 |
| 6e-3 | 53 256 | 602 | 0 / 4 |

RS(255,223) with I = 4 holds to 2 × 10^-3 and starts to fail at 3 × 10^-3, as expected
(SPEC VER-07: 3–5 × 10^-3). The frame sync stayed locked throughout (one FLYWHEEL at
2e-3 and at 6e-3). "Injected bits" divided by all CADUs sent understates the BER a little,
because idle CADUs after the run (error injector off) are counted too.

### VER-08: throughput at the reference mission rate (2026-10-12)

Reference mission scene (Nx 512, Nz 150, D 12, 256 lines, 1 line per ms = 76.8
Msamples/s): one synthetic raw scene in DDR, streamed 2344 times by the looping DMA
source (`run_m5.py --size 512 256 150 12 --scenes 1 --line-period 100000 --repeat 2344
--no-capture`), COMPRESS mode, downlink over the fiber, ground capture counting only.

| | Result |
|---|---|
| Duration | **600.1 s**, 600 064 lines, 2344 scenes, 46.08 × 10^9 samples: **76.8 Msamples/s** (922 Mbit/s raw), compression 2.10:1 |
| Sensor emulator | 0 underruns, 0 overruns, 0 dropped samples |
| Corner turn | 600 064 lines, **0 dropped** |
| Downlink | 2344 scenes in 16 007 176 packets, 37 335 232 science + 35 879 980 idle frames, 73 215 212 CADUs |
| Ground | 73 215 211 CADUs (the last one in flight), 0 sync losses, 0 records dropped |
| Link | 0 code errors, 0 lock losses, 0 RX overflows |

**VER-08 passes** for COMPRESS mode. For long runs the XSDB script now polls and reads back
through the debug port's memory access without halting the A53 (a halt can delay a DMA
re-arm and cause emulator underruns).

### SYS-09: RAW mode at the same rate

Same settings with the compressor bypassed (`--raw`, 60 s): raw data (922 Mbit/s)
exceeds the downlink, so the corner turn drops lines, as SYS-09 expects: of 59 904 lines,
52 797 were dropped and replaced by fill lines and 4 185 could not even be filled
(`CT.DROPPED_LINES`, `CT.UNFILLED_LINES`); the link and the ground kept up with everything
sent (0 sync losses).

Two findings, for M7:

- Under sustained overload the fill-line policy (CT-04) sends mostly fill lines: a fill
  line costs as much bandwidth as a real line, so once the buffers are full nearly every
  new line is dropped. Geometry cannot be kept at all when the output is slower than the
  input (the unfilled lines).
- **A scene short of lines is never ended.** The compressor restarts its position
  counters on the next first-of-scene marker but ends an image (tlast) only at the last
  sample of a full scene, so the short scene's bitstream merges with the next ones: 13 of
  234 scenes completed. One lost line can cost every scene until the framing realigns,
  against the intent of SYS-06. Fix: end the current image on a premature scene start
  (flagged in the packet header). `hpdp_run` also needs an end-of-run condition that does
  not depend on the scene count (it waited for its deadline).

Observations:

- End to end, the compressed scenes reach the ground at 582 Mbit/s: the sensor
  emulator's 99.8 Msamples/s at 5.92 bits/sample, so the link (about 857 Mbit/s of
  packet capacity) is not the limit. The downlink still back-pressures the compressor
  in bursts (18 % output stall cycles in the last scene, none in M4) without any dropped
  line or loss of throughput.
- 0 clock corrections and 0 elastic-buffer errors: both ends run from the same
  reference oscillator.
- The link locked at the first attempt in every run (no RX reset needed).
- The 16 PRBS error words in the first PMA-loopback run all fell in its first 10 s, while
  the SFP modules were being hot-plugged (supply transient); the repeat and every later
  run were clean. Don't plug modules during a measurement.
- `hpdp_run` prints PRBS progress every 10 s on the UART; the result itself comes back
  through the result block, so a lost console (as happened on the PC side during VER-06)
  does not affect the measurement.

## Board procedure

The sequence used for the runs above:

```
vivado -mode batch -source vivado/build.tcl                # HPDP_BUILD_DIR=build/m5 -> build/m5/hpdp.xsa
vitis -s sw/baremetal/build.py build/m5/hpdp.xsa           # HPDP_VITIS_WS=~/hpdp_vitis_m5
python sw/host/run_m5.py --loopback fabric                 # chain without transceivers
python sw/host/run_m5.py --loopback pma                    # GTH near-end PMA loopback
python sw/host/run_m5.py --prbs 60 --loopback pma          # PRBS checker sanity
python sw/host/run_m5.py --prbs 2700                       # VER-06 over the fiber, 3.4e12 bits
python sw/host/run_m5.py                                   # VER-07, BER 0
python sw/host/run_m5.py --ber 1e-4
python sw/host/run_m5.py --ber 1e-3
python sw/host/run_m5.py --ber 3e-3                        # sweep beyond: where RS fails
```

## Open points

- LNK-10 (*should*): In-System IBERT eye scan. The wizard's DRP and eye-scan ports exist
  (tied off); the earlier SFP link project shows how to wire `in_system_ibert`.
- O-1: whether the OM3 cable is duplex (only the return-channel stretch goal needs it).
- `LOS` / `TX_FAULT` from the SFP modules' diagnostics over I2C (*may*).
