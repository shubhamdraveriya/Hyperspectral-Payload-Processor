# UART console and SD card (SW-02, SW-04)

`hpdp_run` takes commands on the PS UART (UART0, 115200 8N1, COM18 on the development
PC), next to the JTAG job interface the host scripts use.

The console works as a second way of writing a job:

- Scenes and calibration tables come from the SD card (FAT32), as SPEC SW-02 asks.
- `run` (or `prbs`) writes a job block at `0x1000_0000` with the same defaults as
  `sw/host/run_m5.py`.
- `main()` runs that job with the same code as a host job: the same checks, link
  bring-up, counters and per-scene summary on the console (SW-06).

Code: `sw/baremetal/hpdp_run/src/console.c`. FatFs (`xilffs`) is in the BSP with long
file names and **read-only**, so the app never writes to the card.

## Preparing an SD card

Files in the card's root, in the formats of SPEC 7. For example, with the model:

```
python model/hpdp_tool.py gen hyperspectral 128 64 64 scene_c.hpdp --cal table.cal --raw scene0.hpdp
python model/hpdp_tool.py gen hyperspectral 128 64 64 scene_c1.hpdp --seed 2 --cal table1.cal --raw scene1.hpdp
```

The files are:

- `scene0.hpdp`: raw detector counts, the input;
- `table.cal`: its calibration table;
- `scene_c.hpdp`: the corrected scene, the reference for the ground station (`--ref`).

Several scenes in one run must have the same size and depth, and use one table. Scenes
must be in BIL order (the default).

## Commands

| Command | |
|---|---|
| `ls` | files on the card |
| `load FILE.hpdp [FILE.hpdp ...]` | raw scenes into DDR (`0x1200_0000`, up to 480 MB, 64 scenes) |
| `cal FILE.cal` | calibration table into DDR (`0x1100_0000`) |
| `mode capture\|downlink\|both` | capture DMA, downlink chain, or both (default downlink) |
| `raw on\|off` | compressor bypass, RAW mode (SYS-09) |
| `correction on\|off` | radiometric correction (default on; needs a table of the scenes' Nx × Nz) |
| `period CYCLES` | line period in `clk_sensor` cycles; 0 = Nx·Nz + 1, the fastest (the reference mission is 100 000) |
| `repeat N` | stream the loaded scenes N times (downlink only, ground capture counting only: long runs) |
| `ei off \| ber X \| burst LEN PERIOD \| asm BITS PERIOD` | error injector; `seed N` sets its seed |
| `link [sfp1\|sfp0] [none\|pcs\|pma\|fabric]` | receiving SFP cage, GTH or fabric loopback |
| `upload on\|off` | serve the ground records over TCP during the run (SW-05) |
| `run` | run the loaded scenes |
| `prbs SECONDS` | PRBS-31 over the link (VER-06) |
| `status` | SYS, link, block counters, ECC / TMR / watchdog / SEM state |
| `show` | current settings |

The compressor uses its default configuration (P = 3, full prediction, neighbour-wide
local sum, Ω = 13; R and K follow the depth, as in the model). Other configurations
still go through the host scripts.

A session:

```
hpdp> ls
       65600  table.cal
     1048640  scene0.hpdp
hpdp> cal table.cal
hpdp> load scene0.hpdp
hpdp> mode both
hpdp> ei ber 1e-4
hpdp> run
job 1124073473: 128x64x64 D=12, 1 scenes, line period 8193, source 0, correction on, capture downlink
  ...per-scene summary, counters...
DONE: hpdp_run job 1124073473, status 0, 1 scenes
```

The ground records are in DDR at `0x4000_0000`. They are served over TCP with
`upload on` (`python sw/ground/hpdp_gs.py decode --tcp <board>:5001 --show`, docs/m6),
or read over JTAG.

## Status

Written and built (2026-10-14). The board test, with an SD card, is still to do.
