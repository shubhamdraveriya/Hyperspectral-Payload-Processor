# Post-M7 board tests (runbook)

The board tests still open after M7 (project report §7), in the order to run them. All
JTAG-only, flash untouched (docs/m0). Prepared 2026-10-14 while the board was busy:
every run below was rehearsed with `--fake-board` (the model in place of the board)
and passes; the expected figures come from those rehearsals.

## Before starting

- The board is free (ask first), powered, USB (JTAG + UART) connected.
- The fiber loops SFP0 ↔ SFP1, as for VER-07 (`run_m5` receives on SFP1 by default).
- The app: `hpdp_run` built after commit d2eda42 (`SYS.LIMITS`, 20-bit ECC injection
  address), in a workspace of its own so that the soak test's ELF in `vitis/` stayed
  untouched. It works with both bitstreams (it reads `SYS.LIMITS`; m7h, which predates
  the register, reads 0 there and the app assumes 512 × 256).

```
$ELF = "C:\Users\shubham.draveriya\hpdp_build\vitis_next\hpdp_run\build\hpdp_run.elf"
```

(`~/hpdp_build` is a junction to the repository's `build/`; the workspace is
`build/vitis_next`, built by `HPDP_VITIS_WS=~/hpdp_build/vitis_next vitis -s
sw/baremetal/build.py build/m7h/hpdp.xsa`. To make it the default again later, rebuild
`vitis/` the usual way.)

## 1. New app on m7h, synthetic scenes (a minute)

```
python sw/host/run_m5.py --elf $ELF --work pm7_sanity
```

Expected: `PASS: run_m5, 4 scenes 128x64x64 D=12 over the link`, as in M5 / M7.

## 2. Real scenes on m7h (about 5 minutes each, mostly host-side checking)

Correction off, so the compressor sees the real radiance (docs/real_data.md).

```
$e = "data\hsi\export"
python sw/host/run_m5.py --elf $ELF --no-correction --work pm7_real_aviris --scene "$e\aviris_f090710t01p00r09rdn_b_crop19_y0_64.hpdp" "$e\aviris_f090705t01p00r07rdn_14_y0_64.hpdp"
python sw/host/run_m5.py --elf $ELF --no-correction --work pm7_real_hyperion --scene "$e\hyperion_EO1H2230682017070110KF_2_y0_128.hpdp"
python sw/host/run_m5.py --elf $ELF --no-correction --work pm7_real_ip --scene "$e\indian_pines.hpdp"
```

| Run | Scenes | Expected compressed bytes (bits/sample) |
|---|---|---|
| `pm7_real_aviris` | 2 × 512 × 64 × 224, D 14 | 3 898 952 (4.25), 7 063 902 (7.70) |
| `pm7_real_hyperion` | 256 × 128 × 242, D 15 | 4 985 044 (5.03) |
| `pm7_real_ip` | 145 × 145 × 220, D 14 | 3 476 350 (6.01) |

Each must end in `PASS: capture path` (identical to the model) and `PASS: run_m5 ...
over the link` (rebuilt bit-exactly at the ground).

## 3. The `NX_MAX` 1024 build m7i_1024b (SYS-04)

```
$X = "build\m7i_1024b\hpdp.xsa"
python sw/host/run_m5.py --elf $ELF --xsa $X --no-correction --work pm7_1024_real --scene "data\hsi\export\hyperion_EO1H2230682017070110KF_2_t_y0_32.hpdp"
python sw/host/run_m5.py --elf $ELF --xsa $X --size 1024 32 64 12 --scenes 2 --work pm7_1024_syn
python sw/host/run_m5.py --elf $ELF --xsa $X --size 1024 16 64 12 --scenes 2 --ecc ct:262152 --work pm7_1024_ecc
```

- `pm7_1024_real`: a real 1024-pixel line (the Hyperion scene with pixels and lines
  swapped, 1024 × 32 × 242, D 15): expected 4 905 137 bytes (4.95 bits/sample),
  identical to the model, rebuilt bit-exactly.
- `pm7_1024_syn`: with the radiometric correction on, so the calibration table in pairs
  (layout B) corrects every sample on the hardware.
- `pm7_1024_ecc`: a single-bit error injected into corner-turn buffer 1
  (2¹⁸ + 8: the address bit the 20-bit field added); expected corrected and counted.
- Then, on the UART console (COM18, 115200 8N1), `status` must print
  `NX_MAX 1024, NZ_MAX 256`.

## 4. SD card and UART console (SW-02, SW-04)

1. Copy the files of `data/sdcard/` to the root of a FAT32 SD card and put it in the
   ZCU106's SD slot. `EXPECTED.txt` there lists the expected compressed sizes.
2. Load bitstream and app: step 1 (any `run_m5` job); the app then keeps polling the
   console.
3. On COM18:

```
ls
cal table.cal
load scene0.hpdp
mode both
run                         -> 1 scene, 385 860 bytes (5.888 bits/sample)
correction off
load avi64.hpdp avi64b.hpdp
run                         -> 3 898 952 and 7 063 902 bytes
status
```

## 5. Ethernet upload (SW-05)

Needs the board's Ethernet connected to the PC (docs/m6): `run_m5.py --upload
--board-ip <ip>` and `python sw/ground/hpdp_gs.py decode --tcp <ip>:5001 --show`.

## Results

To fill in after the session.
