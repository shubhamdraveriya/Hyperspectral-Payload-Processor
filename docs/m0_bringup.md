# M0 bring-up notes (ZCU106)

## Result (2026-10-10)

`dma_loopback` on the board, UART output:

```
HPDP M0: AXI DMA loopback, 16 x 4096 KB
PASS: dma_loopback, 64 MB looped back, 3865 MB/s per direction
```

3865 MB/s is 97 % of the 4000 MB/s a 128-bit stream at 250 MHz can carry.

| Implementation (Vivado 2025.2, XCZU7EV-2) | |
|---|---|
| WNS / WHS | +0.691 ns / +0.012 ns (all clocks met) |
| CDC | 22 crossings between pl_clk0 and pl_clk1, all safe, 0 unsafe / unknown |
| Bus skew | met |
| Utilization | 5.3 k LUT (2.3 %), 8.9 k FF (1.9 %), 12.5 BRAM (4 %), 0 DSP, 0 URAM |

Simulation: `tb_axis_skid_buffer` passes 21,000 random-stall transfers plus a
full-throughput phase (1 transfer/cycle). Python: 13 format tests pass.

## Board facts found during bring-up

| Item | Value |
|---|---|
| JTAG | Digilent/FTDI FT232H on the USB JTAG port, device `xczu7` (IDCODE 0x14730093) |
| PS UART0 | CP2108 interface 0 (COM18 on the development PC), 115200 8N1 |
| Boot mode pins | QSPI32 (`BOOT_MODE_USER[3:0]` = 0x2). The QSPI flash holds a 2018.3 FSBL image |
| PS DDR4 SODIMM | Micron MTA4ATF51264HZ-2G6E1: 4 GB, 1 rank, **x16** devices, 8 Gb, 2 bank groups, 16 row / 10 column bits (from SPD, PS I2C1 → mux 0x75 channel 3 → 0x51) |

## Issue 1: board preset DDR settings do not match the fitted SODIMM

The Vivado ZCU106 board files (2.4, 2.5 and 2.6) configure the PS DDR controller for
the older x8 module (MTA8ATF51264HZ: x8, 4 Gb, 4 bank groups, 15 row bits). With
the x16 module fitted on this board, the controller drives a bank-group bit the
devices do not have, so HIF address bit 14 is not decoded: a write to `base + 0x4000`
overwrites `base`. Symptom: an ELF downloaded to DDR at 0x0 shows the code from
0x4000 at address 0, and the A53 takes an undefined-instruction exception at 0xC.

Fix (in `vivado/bd/base.tcl`, after the board preset):

```
PSU__DDRC__DRAM_WIDTH      16 Bits
PSU__DDRC__DEVICE_CAPACITY 8192 MBits
PSU__DDRC__BG_ADDR_COUNT   1
PSU__DDRC__ROW_ADDR_COUNT  16
```

The remaining preset timings (DDR4-2133P, CL15, tFAW 30 ns, tRC 47.06 ns) are valid
for this DDR4-2666 module run at 2133; Vivado derives tRFC/tRRD from the new density
and width. Set `HPDP_DDR_BOARD_PRESET` before creating the project to keep the board
preset on a ZCU106 that has the x8 module.

How it was diagnosed (XSDB, PSU target):

```
# alias test: bit 14 aliases
mwr -force 0x01000000 0xAAAA0000; mwr -force 0x01004000 0x5555000E; mrd -force 0x01000000
# SPD: Cadence I2C1 at 0xFF030000, mux 0x75 <- 0x08, EEPROM 0x51, page 0/1 via 0x36/0x37
```

## Issue 2: the board boots the QSPI image on every reset

With the boot pins on QSPI32, `rst -system` lets the boot ROM load the FSBL and
application from flash, which run on A53 #0 and overwrite DDR while the debugger is
loading the test program. For JTAG work either set SW6 to JTAG mode, or (volatile,
cleared by a power cycle, flash untouched):

```
targets -set -filter {name =~ "PSU"}
mwr 0xFF5E0200 0x0100      ;# BOOT_MODE_USER: use alternate boot mode = JTAG
rst -system
```

## Issue 3: Vitis and paths with spaces

Vitis 2025.2 reports "invalid os, valid OSes are []" when the XSA path contains a
space (the repository lives under `Linux Boot`). `sw/baremetal/build.py` copies the
XSA into the workspace, and the workspace has no space in its path.

Since 2026-10-13 everything stays inside the repository (so the folder can move to
another machine as it is). The workspace's files are in `vitis/` (git-ignored), and
Vitis sees them through a directory junction without spaces, `~/hpdp_vitis_ws`, which
`build.py` creates if it is missing (junctions need no administrator rights).
XSDB has the same limit; board-run directories are in `build/runs/`, through the
junction `~/hpdp_runs` (`run_m4.py` / `run_m5.py --work <name>`). On a new machine
nothing needs setting up: the scripts make the junctions. Only the Python venv for
the ground station (`.venv/`, git-ignored) must be made again (docs/m6), because a
venv is tied to the Python installation it was made with.
