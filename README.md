# Hyperspectral Payload Data Processor (HPDP)

An FPGA emulation of the onboard data chain of a hyperspectral Earth-observation
satellite, on the AMD ZCU106 (Zynq UltraScale+ XCZU7EV):

raw pushbroom sensor data → radiometric correction → **CCSDS 123.0-B-2** lossless
compression → CCSDS Space Packets and AOS frames → Reed-Solomon, randomization and
sync markers → 1.25 Gbaud optical link (SFP, OM3) → ground receiver that recovers the
image bit-exactly.

The full specification is in [SPEC.md](SPEC.md).

## Status

| Milestone | Content | State |
|---|---|---|
| M0 | Repository, tool flow, ZCU106 base design with DMA loopback | **done**: 64 MB loopback passes on the board at 3865 MB/s ([notes](docs/m0_bringup.md)) |
| M1 | Python reference models (CCSDS 123, framing, RS) | models done, 137 tests pass; independent CCSDS 123 check pending ([notes](docs/m1_models.md)) |
| M2 | CCSDS 123 compressor RTL | — |
| M3 | Sensor emulator, radiometric correction, corner turn | — |
| M4 | On-board compression run | — |
| M5 | Downlink chain and optical link | — |
| M6 | PC ground station | — |
| M7 | Radiation mitigation | — |

## Layout

```
SPEC.md             specification (requirements, interfaces, milestones)
rtl/common/         shared blocks (AXI-Stream skid buffer, ...)
tb/                 self-checking testbenches, tb/run_sim.tcl regression
model/              Python reference models (stdlib only) and pytest tests
vivado/             project, block design and build scripts
sw/baremetal/       A53 bare-metal applications and their Vitis build script
```

## Requirements

- Vivado and Vitis 2025.2 with Zynq UltraScale+ device support
- Python 3.10+ with pytest (`pip install -r requirements.txt`)
- ZCU106 connected over USB (JTAG + UART) for board tests

## Usage

Python models:

```
python -m pytest
python model/hpdp_tool.py gen hyperspectral 64 32 50 scene.hpdp
python model/hpdp_tool.py compress scene.hpdp scene.c123
```

RTL regression (xsim, no project needed):

```
vivado -mode batch -source tb/run_sim.tcl
```

FPGA build (creates `build/vivado/hpdp.xpr`, then writes `build/hpdp.xsa` and reports
in `build/reports/`):

```
vivado -mode batch -source vivado/build.tcl
```

Bare-metal software (platform + applications, workspace in `~/hpdp_vitis`):

```
vitis -s sw/baremetal/build.py
```

Run an application on the board from the Vitis IDE, or with XSDB: program the
bitstream, run `psu_init`, download `dma_loopback.elf` to `psu_cortexa53_0`.
The ZCU106 PS UART is the first port of the CP2108 bridge, 115200 8N1.

Board-specific notes (DDR4 SODIMM settings, JTAG boot override, Vitis path
limitations) are in [docs/m0_bringup.md](docs/m0_bringup.md).

## M0 base design

```
PS DDR4 ─HP0─ AXI DMA MM2S ──► AXI-Stream FIFO ──► AXI DMA S2MM ─HP0─ PS DDR4
```

`pl_clk0` (100 MHz) drives AXI4-Lite control, `pl_clk1` (250 MHz) the 128-bit data
path, and the DMA registers sit at `0xA00A_0000` (SPEC.md §8.1). The FIFO marks where
the HPDP data path will go. `dma_loopback` checks 16 × 4 MB transfers byte for byte
and reports throughput.
