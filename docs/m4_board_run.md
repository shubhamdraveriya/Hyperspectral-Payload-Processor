# M4: On-board compression run (CAPTURE_CMP)

Status: **done** (2026-10-12). Every compressed scene from the board is identical to
the Python model and decodes bit-exactly on the PC; throughput 99.8 Msamples/s, the
full rate of the sensor emulator.

```
PS DDR4 ─HP0─ AXI DMA MM2S ──► hpdp_core ──► AXI DMA S2MM ─HP0─ PS DDR4
               (raw scenes or            (one transfer per compressed scene)
                calibration table)
```

## hpdp_core (`rtl/system/`)

| Part | Detail |
|---|---|
| Registers | `regs/hpdp.rdl` (SystemRDL) → `rtl/regs/hpdp_regs.sv` (PeakRDL-regblock, AXI4-Lite) on `clk_ctrl`, 512 KB window at `0xA000_0000` |
| Crossings | four `cdc_bus` snapshots (continuously resent `xpm_cdc_handshake`): configuration to `clk_sys` and `clk_sensor`, status back from each. Every update carries all bits from one source cycle, so related fields never mix |
| Events | counter clears and calibration-table commands travel as counts inside the snapshots; the receiving domain acts when a count changes |
| Write tracking | the register block counts AXI writes; each domain echoes the count it has received. `SYS.STATUS.cfg_pending` is 1 until every write has arrived everywhere |
| DMA routing | `SYS.DMA_ROUTE`: MM2S stream to the sensor emulator or to the calibration table (tlast ends the table) |
| Output | `SYS.MODE.capture`: compressed words to S2MM, or discarded (for throughput tests without DMA) |
| Statistics (CMP-09) | scenes, input samples, output bytes, bytes and clk_sys cycles of the last scene (first input sample to last output word), output stall cycles |
| Interrupt | `SYS.IRQ_STATUS` (write 1 to clear): scene done, overflow; level output to `pl_ps_irq0[2]` |
| Resets | per-domain resets from `proc_sys_reset`; `SYS.CTRL.soft_reset` holds the data path of both clocks in reset (registers, crossings and the calibration table are kept) |

Blocks changed for M4: counters saturate and clear through `CNT_CLEAR`; the emulator
stops by itself after `SE.LINES` lines (`STATUS.done`), so software need not stop it in
time; the configuration enters the front end once per clock domain; the calibration
table gained a debug read/write port (SPEC RC-02, through `RC.TBL_*`), using the byte
write enables of the UltraRAM so a write touches one entry of a word.

### Register map

| Block | Base | Contents |
|---|---|---|
| SYS | `0xA000_0000` | ID `0x4850_0100`, soft reset, status (busy, error, sensor clock locked, cfg_pending), interrupts, build ID (git commit), mode, DMA route, image size Nx/Ny/Nz/D |
| SE | `0xA001_0000` | run, status (busy, done), line period, lines per run, source; timestamps and counters |
| RC | `0xA002_0000` | correction enable, clamp/replace counts of the current scene, table words loaded, table debug access |
| CT | `0xA003_0000` | lines out, dropped lines, unfilled lines |
| CMP | `0xA004_0000` | prediction, weight update, coder and output settings; status; statistics |

Generated alongside the RTL: `sw/baremetal/common/hpdp_regs.h` (field masks and
positions; offsets through `offsetof`), `tb/system/hpdp_regs_addr_pkg.sv`,
`sw/host/hpdp_regs.py`, and HTML documentation (`python regs/generate.py --html`).
The C header's structs are `packed`, so the software never accesses registers through
them (the compiler could split the accesses); it uses `Xil_In32`/`Xil_Out32`.

## Software

`sw/baremetal/hpdp_run` (A53 #0, standalone):

1. Waits for a job block at `0x1000_0000` (`hpdp_job.h`), loaded by the host.
2. Soft reset, configuration, counter clears; waits for `cfg_pending` = 0.
3. Calibration table by DMA (`DMA_ROUTE` = table), checks `RC.TBL_WORDS`, reads three
   entries back through the debug registers and compares them with the table in DDR.
4. Arms S2MM for scene 0, streams the raw scenes by MM2S (32 MB chunks), starts the
   sensor; re-arms S2MM after each scene (64-byte aligned), recording its length.
5. Writes the result block at `0x1000_1000` (status, per-scene offsets and sizes,
   counters, timer ticks from sensor start to the last scene) and prints a summary.

Without a job it runs a self-test after 3 s: two identical LFSR-pattern scenes must
compress to identical bitstreams with a valid header.

`sw/host/run_m4.py` (PC): generates the scenes and calibration with the model (or
takes `.hpdp` / `.cal` files), computes the expected corrected scenes and bitstreams,
writes `cal.bin`, `raw.bin`, `job.bin` and an XSDB script, runs XSDB over JTAG (JTAG
boot-mode override, bitstream and `psu_init` from the XSA, data, program; volatile
only, flash untouched), reads the result and the compressed scenes back, and checks
every scene byte for byte, decodes it with the model decoder, checks the counters and
reports throughput and compression ratio. `--fake-board` runs the checking path on
the model's own output; `--dry-run` only writes the files.

## Verification

`tb/system/tb_hpdp_core.sv` drives `hpdp_core` through AXI4-Lite exactly as
`hpdp_run` does: identification and reset values, configuration, table load by DMA
model, table readback and write through the debug registers, two acquisitions
without reset (each scene byte checked, random S2MM back-pressure), all counters,
interrupt raise and clear, counter clears, soft reset. Three tests (`sys_dma_basic`,
`sys_dma_stalls`, `sys_max_dims`) on the front-end vectors; the regression is 44 tests.

Found by the system test: starting the DMA right after writing `DMA_ROUTE` sent the
first table beats to the sensor emulator, because the route needs about a microsecond
to cross into `clk_sys`. That led to `cfg_pending`.

## Implementation (`vivado/build.tcl`, XCZU7EV-2)

| | Result |
|---|---|
| WNS / WHS | +0.170 ns / +0.011 ns, all clocks met (`clk_sys` 250 MHz, `clk_ctrl` 100 MHz, `clk_sensor` 100 MHz) |
| Worst path | SmartConnect register → PS `SAXIGP2WDATA` (HP0 write data): routing into the PS block, 0 logic levels |
| CDC (`report_cdc`) | every crossing safe: 0 unsafe, 0 unknown (handshake and FIFO crossings, max delay datapath-only) |
| Methodology | 2 advisories on the `clk_sensor` MMCM (could be a PLL; no LOC); no warnings |
| LUT / FF | 21.3 k (9.2 %) / 20.4 k (4.4 %) |
| BRAM / URAM / DSP | 21 (6.7 %) / 40 (41.7 %) / 20 (1.2 %) |

## Board run (2026-10-12)

```
vivado -mode batch -source vivado/build.tcl          # build/hpdp.xsa
vitis -s sw/baremetal/build.py                       # hpdp_run.elf
python sw/host/run_m4.py [options]                   # JTAG load, run, read back, check
```

Six runs, 15 scenes, all identical to the model and decoded bit-exactly:

| Run | Scenes | Source | Settings | Bits/sample | Ratio | Throughput |
|---|---|---|---|---|---|---|
| default | 4 × 128×64×64, D 12 | DMA, corrected | P 3, Ω 13 | 5.92 | 2.03:1 | 99.8 Msamples/s |
| maximum size | 2 × 512×16×256, D 16 | DMA, corrected | P 15, Ω 16 | 10.9–11.1 | 1.45:1 | 98.8 Msamples/s |
| reduced | 3 × 64×32×32, D 12 | DMA, corrected | reduced, column narrow, B 8, user data 0xA5 | 6.89 | 1.74:1 | 99.4 Msamples/s |
| LFSR | 2 × 256×32×64, D 14 | LFSR, bypass | default | 14.50 | 0.97:1 | 99.3 Msamples/s |
| ramp | 2 × 101×20×48, D 10 | ramp, corrected | default (odd Nx) | 1.20 | 8.30:1 | 98.9 Msamples/s |
| bypass | 2 × 96×24×40, D 12 | DMA, bypass | P 0 | 8.10 | 1.48:1 | 99.0 Msamples/s |

The line period was Nx·Nz + 1 in every run, so the emulator offered its maximum of
100 Msamples/s (above the reference mission's 76.8). The measured rate (sensor start to
the last scene's S2MM completion) is 98.8–99.8 Msamples/s; the difference is the
pipeline fill and drain. No underruns, overruns or dropped lines, no compressor output
stalls, and the clamp and replacement counts matched the model in every run. The
compressor itself runs at up to 250 Msamples/s, so the sensor emulator, not the
compressor, sets the rate.

The application's self-test (no job: two identical LFSR scenes must give identical
bitstreams) also passes on the board.

Board notes:

- Vitis leaves some generated files read-only; `sw/baremetal/build.py` now clears the
  attribute before recreating the workspace.
- An interrupted Vivado build leaves its runs marked as running; delete `build/vivado`
  and build again.
- `list_jtag_targets` of the Vitis MCP server showed only the PS TAP branch while the
  QSPI image was running; XSDB's `targets` listed PSU / APU / Cortex-A53 as usual, and
  the run worked.
