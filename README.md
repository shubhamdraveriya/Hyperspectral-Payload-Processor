# Hyperspectral Payload Data Processor (HPDP)

An FPGA emulation of the onboard data chain of a hyperspectral Earth-observation
satellite, on the AMD ZCU106 (Zynq UltraScale+ XCZU7EV):

raw pushbroom sensor data → radiometric correction → **CCSDS 123.0-B-2** lossless
compression → CCSDS Space Packets and AOS frames → Reed-Solomon, randomization and
sync markers → 1.25 Gbaud optical link (SFP, OM3) → ground receiver that recovers the
image bit-exactly.

The full specification is in [SPEC.md](SPEC.md).

## Status

Project report (results, requirements compliance, open items):
[docs/project_report.md](docs/project_report.md).

PDF documents in [docs/pdf/](docs/pdf/) (built by `docs/pdf/build_docs.py`):

| Document | Contents |
|---|---|
| [HPDP_Project_Brief.pdf](docs/pdf/HPDP_Project_Brief.pdf) | HPDP-BRF-001: the project on three pages |
| [HPDP_Process_Description_STE.pdf](docs/pdf/HPDP_Process_Description_STE.pdf) | HPDP-PRC-001: how the project was done, with the procedures (Simplified Technical English) |
| [HPDP_Results_Report.pdf](docs/pdf/HPDP_Results_Report.pdf) | HPDP-RPT-001: all test results and the requirement compliance |
| [HPDP_Learning_Guide_STE.pdf](docs/pdf/HPDP_Learning_Guide_STE.pdf) | HPDP-GDE-001: the HPDP taught from the basics to the full design, with check questions (Simplified Technical English) |

| Milestone | Content | State |
|---|---|---|
| M0 | Repository, tool flow, ZCU106 base design with DMA loopback | **done**: 64 MB loopback passes on the board at 3865 MB/s ([notes](docs/m0_bringup.md)) |
| M1 | Python reference models (CCSDS 123, framing, RS) | models done, 150 tests pass; independent CCSDS 123 check pending (O-5) ([notes](docs/m1_models.md)) |
| M2 | CCSDS 123 compressor RTL | **done**: bit-exact on all 864 configurations of the VER-02 matrix; 250 MHz met at 512 × 256 (13.6k LUT, 19 DSP, 8 URAM) ([notes](docs/m2_compressor.md)) |
| M3 | Sensor emulator, radiometric correction, corner turn | **done**: bit-exact with the models through the compressor on 12 tests (incl. line drops with fill lines); 250 MHz met at 512 × 256 (1.1k LUT, 1 DSP, 32 URAM) ([notes](docs/m3_frontend.md)) |
| M4 | On-board compression run | **done**: 15 scenes from the board identical to the model and decoded bit-exactly; 99.8 Msamples/s; 250 MHz met on the full chip, CDC clean ([notes](docs/m4_board_run.md)) |
| M5 | Downlink chain and optical link | **done**: PRBS-31 3.4 × 10¹² bits error-free over the fiber (VER-06); scenes bit-exact at BER 0 / 10⁻⁴ / 10⁻³ (VER-07); 76.8 Msamples/s for 10 min without a dropped line (VER-08) ([notes](docs/m5_downlink.md)) |
| M6 | PC ground station | **done**: decodes, verifies and displays captures and live streams; the board's Ethernet upload (SW-05) is written but not tested on the board, a documented deviation ([notes](docs/m6_ground_station.md), [report](docs/project_report.md) §6) |
| M7 | Radiation mitigation | **done**: SEM scrubbing (upsets corrected in ≤ 153 ms on the board), ECC, TMR, watchdogs; fault campaign with no silent corruption in protected state; final build m7h ([plan](docs/m7_plan.md), [report](docs/m7_fault_injection.md)) |
| After M7 | Remaining requirements | `NX_MAX` 1024 build m7i_1024b: 80 of 96 URAM, 250 MHz met, tested on the board ([SYS-04](docs/nx_max_1024.md)); SD card and UART console tested on the board ([console](docs/console.md)); VER-02 matrix and VER-03 corner cases in the regression; real hyperspectral data ([results](docs/real_data.md)) |

## Layout

```
SPEC.md             specification (requirements, interfaces, milestones)
rtl/system/         hpdp_core: registers, clock-domain crossings, data path
rtl/frontend/       sensor emulator, radiometric correction, corner turn
rtl/ccsds123/       CCSDS 123.0-B-2 compressor
rtl/common/         shared blocks (RAMs, FIFO, CDC bus, AXI-Stream skid buffer)
rtl/regs/           register block generated from regs/hpdp.rdl
regs/               SystemRDL register map and generator (PeakRDL)
tb/                 self-checking testbenches, tb/run_sim.tcl regression
model/              Python reference models (stdlib only) and pytest tests
vivado/             project, block design and build scripts
sw/baremetal/       A53 bare-metal application (hpdp_run) and its Vitis build script
sw/host/            PC tools for board runs (run_m4.py: JTAG loading and verification)
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

RTL regression (xsim, no project needed; a random 6 of the 864 compressor
configurations each run, `HPDP_C123_SEED` repeats them; `HPDP_C123_MATRIX=all` with
`-tclargs "c123_mx_*"` runs the whole VER-02 matrix, about an hour):

```
vivado -mode batch -source tb/run_sim.tcl
```

FPGA build (creates `build/vivado/hpdp.xpr`, then writes `build/hpdp.xsa` and reports
in `build/reports/`; `HPDP_BUILD_DIR` builds elsewhere, `HPDP_NX_MAX=1024` makes the
1024-pixel build of [docs/nx_max_1024.md](docs/nx_max_1024.md)):

```
vivado -mode batch -source vivado/build.tcl
```

Bare-metal software (platform + applications, workspace in `vitis/`, which Vitis reaches
through the junction `~/hpdp_vitis_ws` because of the space in the path; docs/m0, issue 3):

```
vitis -s sw/baremetal/build.py
```

Board run (JTAG only; generates scenes with the model, loads them with
`hpdp_run`, reads the compressed scenes back and checks them bit for bit):

```
python sw/host/run_m4.py --size 128 64 64 12 --scenes 4
```

The ZCU106 PS UART is the first port of the CP2108 bridge, 115200 8N1.

Register map: edit `regs/hpdp.rdl`, then regenerate the register block, C header
and address files (`--html` also writes documentation to `build/regs_html/`):

```
python regs/generate.py --html
```

Board-specific notes (DDR4 SODIMM settings, JTAG boot override, Vitis path
limitations) are in [docs/m0_bringup.md](docs/m0_bringup.md). Moving the project to
another computer: [docs/new_system_setup.md](docs/new_system_setup.md).

## Block design (M4)

```
PS DDR4 ─HP0─ AXI DMA MM2S ──► hpdp_core ──► AXI DMA S2MM ─HP0─ PS DDR4
                               sensor emulator → radiometric correction →
                               corner turn → CCSDS 123 compressor
```

`pl_clk0` (100 MHz) drives AXI4-Lite control, `pl_clk1` (250 MHz) the data path, and
an MMCM makes the 100 MHz sensor clock. The `hpdp_core` registers sit at
`0xA000_0000`, the DMA registers at `0xA00A_0000` (SPEC.md §8.1). The M0 loopback
design and its `dma_loopback` test are in the git history (commit dec38fc).
