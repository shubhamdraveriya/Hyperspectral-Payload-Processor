# Post-M7 board tests (runbook)

The board tests still open after M7 (project report §7), in the order to run them. All
JTAG-only, flash untouched (docs/m0). Prepared 2026-10-14 while the board was busy:
every run below was rehearsed with `--fake-board` (the model in place of the board)
and passes; the expected figures come from those rehearsals.

## Before starting

Connections (tell the user before the session which step needs which):

| Steps | Needs |
|---|---|
| all | the board free (ask first), powered; USB (JTAG + UART, COM18) |
| 1 – 5 | **the OM3 fiber looping SFP0 ↔ SFP1** (`run_m5` receives on SFP1); without it the link does not lock (LNK.STATUS 0xF07, millions of code errors); `--loopback pma` runs without it |
| 4 | an SD card (FAT32) in the board's slot, with the files of `data/sdcard/` (a card reader, or `sw/host/sd_put.py` through the board) |
| 5 | an Ethernet cable from the board's RJ45 to a PC port (link-local 169.254.x.x, no setup) or to the office network (DHCP) |
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
   ZCU106's SD slot. `EXPECTED.txt` there lists the expected compressed sizes. Without
   a card reader, put the card in the board and write them through it:
   `vitis -s sw/baremetal/build_sd_put.py`, then `python sw/host/sd_put.py data/sdcard/*`
   (resets the board; adds or replaces files, reads each back).
2. Load bitstream and app: step 1 (any `run_m5` job); the app then keeps polling the
   console.
3. On COM18:

```
ls
cal table.cal
load scene0.hpdp
mode both
run                         -> 1 scene, 387 331 bytes (5.91 bits/sample), 80 clamped, 1472 replaced
correction off
load avi64.hpdp avi64b.hpdp
run                         -> 3 898 952 and 7 063 902 bytes
status
```

## 5. Ethernet upload (SW-05)

Needs the board's Ethernet connected to the PC (docs/m6): `run_m5.py --upload
--board-ip <ip>` and `python sw/ground/hpdp_gs.py decode --tcp <ip>:5001 --show`.

## Results (2026-10-14, after the user's soak test)

| Step | Result |
|---|---|
| 1 | First attempt: link not locked (the fiber was not connected after the soak test); with `--loopback pma` PASS; with the fiber connected: **PASS**, 4 scenes bit-exact at the ground, 0 code errors |
| 2 | **PASS** on m7h over the fiber: AVIRIS 3 898 952 and 7 063 902 bytes, Hyperion 4 985 044, Indian Pines 3 476 350, each identical to the model and rebuilt bit-exactly at the ground; 99.3 – 99.6 Msamples/s; 0 code errors |
| 3 | **PASS** on m7i_1024b (build 0x9018F99A): the 1024 × 32 × 242 real scene, 4 905 137 bytes; 1024 × 32 × 64 with the correction on (the table in pairs on the hardware); the ECC injection at corner-turn address 2¹⁸ + 8: 4 corrected reads, 0 uncorrectable, scenes intact; `status`: `NX_MAX 1024, NZ_MAX 256` |
| 4 | **PASS** on m7h: no card reader, so `sd_put` wrote the 6 files to an empty card (each read back and compared); `ls` lists them; `cal` + `load` + `run`: 387 331 bytes (the model's, with the correction: 80 clamped, 1472 replaced); `correction off`, the two real scenes: 3 898 952 and 7 063 902 bytes; `prbs 10`: 12 Gbit, 0 errors; `help`, `show`, `mode`, `ei`, `seed`, `status` |
| 5 | not run: skipped (decision 2026-10-15, no Ethernet port free on the PC); SW-05 is recorded as a deviation in the project report §6. The commands of §5 stay valid for a later test |

Found and fixed: the per-scene bits/sample print overflowed 32 bits above 5 MB per scene
(7 063 902 bytes printed as 1.84; data unaffected), and the RX word counter printed
signed; `hpdp_run` fixed and re-checked on the board (7.69). `EXPECTED.txt` and
docs/console.md had the synthetic scene's reference wrong (the clean scene instead of
the correction of the raw one); corrected.
