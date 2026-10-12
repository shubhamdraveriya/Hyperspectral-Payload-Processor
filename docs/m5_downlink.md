# M5: Downlink chain and optical link

Status: **in progress** (2026-10-12). The whole chain is integrated in `hpdp_core` and
`hpdp_top`, and it matches the Python model in simulation (VER-05). Board runs (VER-06
PRBS-31 over the fiber, VER-07 scenes over the link at BER 0, 1e-4, 1e-3) are still to do.

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

## Board procedure (to do; the board is needed)

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
