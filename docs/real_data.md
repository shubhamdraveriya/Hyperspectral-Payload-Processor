# Real hyperspectral data (SPEC §11 test data; O-7)

Until M7 every compression figure came from synthetic scenes. This note measures the
HPDP's CCSDS 123 compressor on 63 public hyperspectral scenes: one airborne scene of
the classic AVIRIS Indian Pines site, 49 AVIRIS scenes from 13 flights, and 13
scenes from the Hyperion spectrometer on the EO-1 satellite (2026-10-14).

The scenes go through the Python model (`model/hpdp_model/ccsds123.py`), which is
bit-exact with the RTL on all 864 configurations of the VER-02 matrix, so the figures
are the hardware's. Every scene was also decoded and compared: **63 of 63 lossless**.

## Results in short

| Dataset | Scenes | Bits/sample, mean (range) | Link use at 76.8 Msamples/s, mean (worst scene) | Deflate (ZIP) for comparison |
|---|---:|---|---|---:|
| AVIRIS (airborne, 2008 – 2017) | 49 | **4.43** (2.99 – 7.93) | 340 Mbit/s, 40 % (609 Mbit/s, 71 %) | 8.89 |
| Hyperion (EO-1, 2017) | 13 | **4.95** (4.74 – 5.16) | 380 Mbit/s, 44 % (396 Mbit/s, 46 %) | 8.03 |
| Indian Pines (AVIRIS, 1992) | 1 | **6.01** | 462 Mbit/s, 54 % | 9.98 |

- **Link budget (SPEC §12.2):** at the reference mission rate of 76.8 Msamples/s,
  every real scene fits in the 857 Mbit/s of packet capacity; the worst one, an
  AVIRIS scene at 7.93 bits/sample, takes 71 %. The budget's example of 2:1 on
  12-bit data (6 bits/sample) is met by 55 of the 63 scenes; the 7 AVIRIS scenes of
  the second group below and Indian Pines (6.01) need more.
- **Against a general-purpose compressor:** CCSDS 123 needs 1.6 – 2.0 times fewer
  bits than deflate on the same samples.
- **AVIRIS falls into two groups.** 42 scenes need 2.99 – 4.33 bits/sample (mean
  3.91); 7 scenes need 7.08 – 7.93 (mean 7.53). The 7 are every scene of three
  flights (f080706, f080713, f090705), so it is a property of those flights' data,
  not of individual scenes; the cause was not investigated.
- **Settings:** the defaults are within 1 % of the best setting tried on each
  dataset's typical scene (below). Spectral prediction matters most: without it
  (P = 0) the scenes need 12 – 67 % more bits. No change to the defaults.
- **Scene length (O-7):** compressing 256-line scenes instead of whole images costs
  0.3 %; 16-line scenes cost 2 – 4 %. The default of 256 lines stands, and O-7 is
  closed.

## Data

| Dataset | Content | Source | Licence | File (in `data/hsi/`, git-ignored) |
|---|---|---|---|---|
| `indian_pines` | 145 × 145 pixels × 220 bands, 16-bit; AVIRIS, 12 June 1992, 20 m | Purdue University Research Repository, doi:10.4231/R7RX991C ([publication](https://purr.purdue.edu/publications/1947/1)) | CC0 | `indian_pines/19920612_AVIRIS_IndianPine_Site3.tif` (baseline TIFF, one plane per band) |
| `aviris` | 49 scenes of 512 × 512 × 224, 16-bit, calibrated radiance (L1), 13 flights 2008 – 2017 across North America | GICI, Universitat Autònoma de Barcelona, [public datasets](https://gici.uab.cat/GiciWebPage/datasets.php): AVIRIS test set (data NASA/JPL) | none stated by GICI; NASA data | `gici_aviris/AVIRIS_test.zip` (3 198 180 286 bytes) |
| `hyperion` | 13 scenes of 256 × 1024 × 242, 16-bit, L1, 2017 | GICI public datasets: Hyperion test set (data from USGS Earth Explorer, per its README) | none stated by GICI; USGS data | `gici_hyperion/Hyperion_test.zip` (828 058 844 bytes) |

SHA-256:

```
d995a8ace3b9258269e0e6fe780e2bd39fadc1a49192fd67c3ddee0b77e373e5  indian_pines/19920612_AVIRIS_IndianPine_Site3.tif
46570b3085da5f05c0a084b76992440acf96138be646619c216a6e0594045196  gici_aviris/AVIRIS_test.zip
edac833476a7610b44504e77f044570fcf5f14421a4d929fc165fdab262d6abd  gici_hyperion/Hyperion_test.zip
```

Getting them (about 4 GB):

```
curl -L -o data/hsi/gici_aviris/AVIRIS_test.zip https://gici.uab.cat/datasets/AVIRIS_test/AVIRIS_test.zip
curl -L -o data/hsi/gici_hyperion/Hyperion_test.zip https://gici.uab.cat/datasets/Hyperion_test/Hyperion_test.zip
```

Indian Pines: `19920612_AVIRIS_IndianPine_Site3.tif` from the PURR publication page.

**Why not the SPEC's Salinas and Pavia University.** SPEC §11 named Indian Pines,
Salinas and Pavia University (ROSIS). The usual source of Salinas and Pavia, the
University of the Basque Country's site, refuses this network (HTTP 403), and there
is no official mirror. The CCSDS working group's own test corpus (once on
cwe.ccsds.org) now sits behind a login. The GICI sets replace them: that group works
on CCSDS 123, the sets are much larger (62 scenes instead of 2), and Hyperion adds a
spaceborne sensor. All scenes fit the 512-pixel build (`NX_MAX` 512, `NZ_MAX` 256).

**Format of the GICI files.** Raw files named
`<scene>.<bands>_<width>_<height>_<type>_<byte order>_<RGB>.raw` (the Hyperion
README): band-sequential, type 2 = unsigned 16-bit, byte order 1 = little-endian.
The width was checked against the data (adjacent rows correlate at an offset of 256
samples, not 1024). `model/hpdp_datasets.py` reads the scenes straight from the ZIPs.

## Method

- **Settings:** the HPDP defaults: P = 3, full prediction, neighbour-oriented wide
  local sums, Ω = 13, R = max(32, D + Ω + 2), v_min = −1, v_max = 3, t_inc = 2⁶,
  U_max = 18, γ₀ = 1, γ* = 6, K = min(7, D − 2), BIP order. The settings study
  changes one thing at a time.
- **Dynamic range:** D per scene from its largest sample (12 – 16 bits). No scene had
  negative samples, so nothing was clipped.
- **Bits per sample** include the image header. "Ratio" is D / bits per sample.
- **Round trip:** every default-settings scene decoded by the model's decoder and
  compared sample by sample.
- **Deflate:** the size of the same samples (band-sequential, little-endian) in the
  ZIP; for Indian Pines, zlib level 6. A reference point only.
- **Typical scene:** for the settings and scene-length studies, the scene with the
  median deflate size in its dataset.
- **Scene length:** the image cut into scenes of N lines, each compressed on its own
  (header and adaptation start again each time), as the HPDP does with each scene.
- **What the data stand for:** in the HPDP the compressor sees radiometrically
  corrected samples; these sets are calibrated radiance, so they stand for that
  stream. (SPEC §11's inverse-calibration idea is for checking the correction stage,
  which the RTL tests do with synthetic maps.)
- **Cost:** 99 tasks, 10.2 CPU hours, 2 h 7 min on 5 processes (the model codes
  about 210 k samples/s in Python). The hardware would take 0.24 s per AVIRIS scene.

## Reproducing

```
python -I model/hpdp_datasets.py list
python -I model/hpdp_datasets.py experiment -j 5     # build/real_data_results.md; resumable
python -I model/hpdp_datasets.py export aviris/f090710t01p00r09rdn_b_crop19   # .hpdp for the SD card
```

## On the board (2026-10-14)

[post_m7_board_tests.md](post_m7_board_tests.md) §2 – §4: parts of real scenes,
exported with `hpdp_datasets.py export --lines` (correction off, so the compressor sees
the data as stored), compressed by the board exactly as by the model and rebuilt
bit-exactly at the ground over the fiber:

| Scene | Build | Compressed bytes (bits/sample) |
|---|---|---|
| AVIRIS f090710t01p00r09rdn_b_crop19, lines 0 – 63 (512 × 64 × 224) | m7h | 3 898 952 (4.25) |
| AVIRIS f090705t01p00r07rdn_14, lines 0 – 63 (a hard flight) | m7h | 7 063 902 (7.70) |
| Hyperion EO1H2230682017070110KF_2, lines 0 – 127 (256 × 128 × 242) | m7h | 4 985 044 (5.03) |
| Indian Pines (145 × 145 × 220) | m7h | 3 476 350 (6.01) |
| Hyperion EO1H2230682017070110KF_2 with pixels and lines swapped, 1024 × 32 × 242 | m7i_1024b | 4 905 137 (4.95) |

The two AVIRIS parts also ran from the SD card through the UART console, with the same
sizes.

## Full results

As written by `model/hpdp_datasets.py experiment` (`build/real_data_results.md`).

### Datasets (default settings)

| Dataset | Scenes | Nx x Ny x Nz | D | Bits/sample: mean (min - max) | Ratio | Deflate bits/sample | Round trips |
|---|---:|---|---|---|---:|---:|---|
| indian_pines | 1 | 145 x 145 x 220 | 14 | 6.012 (6.012 - 6.012) | 2.33:1 | 9.984 | 1 of 1 lossless |
| aviris | 49 | 512 x 512 x 224 | 12 - 16 | 4.427 (2.985 - 7.929) | 3.12:1 | 8.892 | 49 of 49 lossless |
| hyperion | 13 | 256 x 1024 x 242 | 13 - 15 | 4.945 (4.736 - 5.157) | 2.86:1 | 8.032 | 13 of 13 lossless |

### Settings (the typical scene of each dataset)

| Scene | Setting | Bits/sample | Change |
|---|---|---:|---:|
| aviris/f090710t01p00r09rdn_b_crop19 | reduced prediction | 3.903 | -0.7 % |
| aviris/f090710t01p00r09rdn_b_crop19 | default (P 3, full, neighbour-wide, omega 13) | 3.932 | +0.0 % |
| aviris/f090710t01p00r09rdn_b_crop19 | neighbour-narrow local sum | 4.048 | +2.9 % |
| aviris/f090710t01p00r09rdn_b_crop19 | omega 19, v_max 9 | 4.102 | +4.3 % |
| aviris/f090710t01p00r09rdn_b_crop19 | P = 15 | 4.105 | +4.4 % |
| aviris/f090710t01p00r09rdn_b_crop19 | column-wide local sum | 4.215 | +7.2 % |
| aviris/f090710t01p00r09rdn_b_crop19 | column-narrow local sum | 4.233 | +7.7 % |
| aviris/f090710t01p00r09rdn_b_crop19 | P = 0 (spatial only) | 6.568 | +67.0 % |
| hyperion/EO1H2230682017070110KF_2 | P = 15 | 4.881 | -0.7 % |
| hyperion/EO1H2230682017070110KF_2 | default (P 3, full, neighbour-wide, omega 13) | 4.915 | +0.0 % |
| hyperion/EO1H2230682017070110KF_2 | reduced prediction | 4.963 | +1.0 % |
| hyperion/EO1H2230682017070110KF_2 | neighbour-narrow local sum | 4.992 | +1.6 % |
| hyperion/EO1H2230682017070110KF_2 | column-wide local sum | 5.084 | +3.4 % |
| hyperion/EO1H2230682017070110KF_2 | column-narrow local sum | 5.089 | +3.5 % |
| hyperion/EO1H2230682017070110KF_2 | P = 0 (spatial only) | 5.484 | +11.6 % |
| hyperion/EO1H2230682017070110KF_2 | omega 19, v_max 9 | 5.675 | +15.5 % |
| indian_pines | default (P 3, full, neighbour-wide, omega 13) | 6.012 | +0.0 % |
| indian_pines | reduced prediction | 6.019 | +0.1 % |
| indian_pines | P = 15 | 6.061 | +0.8 % |
| indian_pines | omega 19, v_max 9 | 6.065 | +0.9 % |
| indian_pines | neighbour-narrow local sum | 6.190 | +2.9 % |
| indian_pines | column-wide local sum | 6.296 | +4.7 % |
| indian_pines | column-narrow local sum | 6.325 | +5.2 % |
| indian_pines | P = 0 (spatial only) | 7.433 | +23.6 % |

### Scene length (O-7: each scene compressed on its own)

| Scene | Lines per scene | Bits/sample | Change |
|---|---:|---:|---:|
| aviris/f090710t01p00r09rdn_b_crop19 | 16 | 4.084 | +3.9 % |
| aviris/f090710t01p00r09rdn_b_crop19 | 32 | 4.030 | +2.5 % |
| aviris/f090710t01p00r09rdn_b_crop19 | 64 | 3.993 | +1.5 % |
| aviris/f090710t01p00r09rdn_b_crop19 | 128 | 3.963 | +0.8 % |
| aviris/f090710t01p00r09rdn_b_crop19 | 256 | 3.945 | +0.3 % |
| aviris/f090710t01p00r09rdn_b_crop19 | 512 (whole) | 3.932 | +0.0 % |
| hyperion/EO1H2230682017070110KF_2 | 16 | 5.030 | +2.3 % |
| hyperion/EO1H2230682017070110KF_2 | 32 | 4.994 | +1.6 % |
| hyperion/EO1H2230682017070110KF_2 | 64 | 4.965 | +1.0 % |
| hyperion/EO1H2230682017070110KF_2 | 128 | 4.943 | +0.6 % |
| hyperion/EO1H2230682017070110KF_2 | 256 | 4.928 | +0.3 % |
| hyperion/EO1H2230682017070110KF_2 | 512 | 4.920 | +0.1 % |
| hyperion/EO1H2230682017070110KF_2 | 1024 (whole) | 4.915 | +0.0 % |
| indian_pines | 16 | 6.118 | +1.8 % |
| indian_pines | 32 | 6.072 | +1.0 % |
| indian_pines | 64 | 6.042 | +0.5 % |
| indian_pines | 128 | 6.015 | +0.0 % |
| indian_pines | 145 (whole) | 6.012 | +0.0 % |

### Every scene (default settings)

| Scene | Nx x Ny x Nz | D | Clipped | Bits/sample | Ratio | Deflate | Round trip |
|---|---|---:|---:|---:|---:|---:|---|
| aviris/f080706t01p00r08rdn_0 | 512 x 512 x 224 | 13 | 0 | 7.085 | 1.83:1 | 8.631 | lossless |
| aviris/f080713t01p00r15rdn_2 | 512 x 512 x 224 | 14 | 0 | 7.882 | 1.78:1 | 10.288 | lossless |
| aviris/f080806t01p00r07_crop0 | 512 x 512 x 224 | 13 | 0 | 4.027 | 3.23:1 | 8.628 | lossless |
| aviris/f080806t01p00r07_crop2 | 512 x 512 x 224 | 14 | 0 | 4.093 | 3.42:1 | 8.905 | lossless |
| aviris/f080806t01p00r07_crop4 | 512 x 512 x 224 | 14 | 0 | 3.993 | 3.51:1 | 8.614 | lossless |
| aviris/f080806t01p00r07_crop5 | 512 x 512 x 224 | 14 | 0 | 3.947 | 3.55:1 | 8.608 | lossless |
| aviris/f080806t01p00r08_crop8 | 512 x 512 x 224 | 15 | 0 | 4.242 | 3.54:1 | 9.547 | lossless |
| aviris/f080806t01p00r08_crop9 | 512 x 512 x 224 | 14 | 0 | 4.256 | 3.29:1 | 9.690 | lossless |
| aviris/f080814t01p00r17_crop0 | 512 x 512 x 224 | 13 | 0 | 3.844 | 3.38:1 | 8.484 | lossless |
| aviris/f080814t01p00r17_crop4 | 512 x 512 x 224 | 13 | 0 | 3.400 | 3.82:1 | 7.177 | lossless |
| aviris/f080814t01p00r17_crop5 | 512 x 512 x 224 | 13 | 0 | 3.232 | 4.02:1 | 6.474 | lossless |
| aviris/f090705t01p00r07rdn_10 | 512 x 512 x 224 | 14 | 0 | 7.465 | 1.88:1 | 9.311 | lossless |
| aviris/f090705t01p00r07rdn_14 | 512 x 512 x 224 | 14 | 0 | 7.929 | 1.77:1 | 10.407 | lossless |
| aviris/f090705t01p00r07rdn_25 | 512 x 512 x 224 | 14 | 0 | 7.081 | 1.98:1 | 8.744 | lossless |
| aviris/f090705t01p00r07rdn_6 | 512 x 512 x 224 | 14 | 0 | 7.650 | 1.83:1 | 9.986 | lossless |
| aviris/f090705t01p00r07rdn_8 | 512 x 512 x 224 | 14 | 0 | 7.597 | 1.84:1 | 9.406 | lossless |
| aviris/f090710t01p00r09rdn_b_crop10 | 512 x 512 x 224 | 13 | 0 | 3.982 | 3.26:1 | 8.766 | lossless |
| aviris/f090710t01p00r09rdn_b_crop11 | 512 x 512 x 224 | 14 | 0 | 3.962 | 3.53:1 | 9.202 | lossless |
| aviris/f090710t01p00r09rdn_b_crop12 | 512 x 512 x 224 | 14 | 0 | 3.985 | 3.51:1 | 9.224 | lossless |
| aviris/f090710t01p00r09rdn_b_crop13 | 512 x 512 x 224 | 14 | 0 | 3.976 | 3.52:1 | 8.969 | lossless |
| aviris/f090710t01p00r09rdn_b_crop14 | 512 x 512 x 224 | 14 | 0 | 3.950 | 3.54:1 | 8.979 | lossless |
| aviris/f090710t01p00r09rdn_b_crop16 | 512 x 512 x 224 | 14 | 0 | 3.904 | 3.59:1 | 8.947 | lossless |
| aviris/f090710t01p00r09rdn_b_crop19 | 512 x 512 x 224 | 14 | 0 | 3.932 | 3.56:1 | 9.139 | lossless |
| aviris/f090710t01p00r09rdn_b_crop2 | 512 x 512 x 224 | 14 | 0 | 4.045 | 3.46:1 | 9.100 | lossless |
| aviris/f090710t01p00r09rdn_b_crop29 | 512 x 512 x 224 | 14 | 0 | 3.839 | 3.65:1 | 8.642 | lossless |
| aviris/f090710t01p00r09rdn_b_crop3 | 512 x 512 x 224 | 13 | 0 | 3.996 | 3.25:1 | 8.850 | lossless |
| aviris/f090710t01p00r09rdn_b_crop31 | 512 x 512 x 224 | 14 | 0 | 3.849 | 3.64:1 | 8.933 | lossless |
| aviris/f090710t01p00r09rdn_b_crop8 | 512 x 512 x 224 | 14 | 0 | 3.957 | 3.54:1 | 9.462 | lossless |
| aviris/f090710t01p00r09rdn_b_crop9 | 512 x 512 x 224 | 14 | 0 | 3.937 | 3.56:1 | 8.783 | lossless |
| aviris/f090729t01p00r09rdn_b_crop1 | 512 x 512 x 224 | 13 | 0 | 4.263 | 3.05:1 | 9.652 | lossless |
| aviris/f100731t01p00r05rdn_b_crop0 | 512 x 512 x 224 | 14 | 0 | 4.112 | 3.40:1 | 9.486 | lossless |
| aviris/f100731t01p00r05rdn_b_crop3 | 512 x 512 x 224 | 15 | 0 | 4.105 | 3.65:1 | 9.552 | lossless |
| aviris/f100731t01p00r05rdn_b_crop4 | 512 x 512 x 224 | 15 | 0 | 4.126 | 3.64:1 | 9.211 | lossless |
| aviris/f100825t01p00r08rdn_b_crop0 | 512 x 512 x 224 | 14 | 0 | 3.498 | 4.00:1 | 6.824 | lossless |
| aviris/f100825t01p00r08rdn_b_crop12 | 512 x 512 x 224 | 13 | 0 | 4.131 | 3.15:1 | 9.964 | lossless |
| aviris/f100825t01p00r08rdn_b_crop13 | 512 x 512 x 224 | 13 | 0 | 4.171 | 3.12:1 | 9.884 | lossless |
| aviris/f100825t01p00r08rdn_b_crop6 | 512 x 512 x 224 | 13 | 0 | 4.169 | 3.12:1 | 9.607 | lossless |
| aviris/f100830t01p00r20rdn_b_sc01_crop4 | 512 x 512 x 224 | 12 | 0 | 2.985 | 4.02:1 | 5.373 | lossless |
| aviris/f100830t01p00r20rdn_b_sc01_crop5 | 512 x 512 x 224 | 12 | 0 | 3.020 | 3.97:1 | 5.367 | lossless |
| aviris/f110512t01p00r04rdn_c_sc01_crop5 | 512 x 512 x 224 | 14 | 0 | 4.116 | 3.40:1 | 9.493 | lossless |
| aviris/f110615t01p00r08rdn_c_crop10 | 512 x 512 x 224 | 14 | 0 | 4.136 | 3.39:1 | 9.760 | lossless |
| aviris/f110615t01p00r08rdn_c_crop11 | 512 x 512 x 224 | 16 | 0 | 4.136 | 3.87:1 | 9.732 | lossless |
| aviris/f110615t01p00r08rdn_c_crop13 | 512 x 512 x 224 | 16 | 0 | 4.198 | 3.81:1 | 9.555 | lossless |
| aviris/f110615t01p00r08rdn_c_crop6 | 512 x 512 x 224 | 16 | 0 | 4.330 | 3.70:1 | 9.321 | lossless |
| aviris/f170509t01p00r02rdn_e_sc01_crop12 | 512 x 512 x 224 | 13 | 0 | 3.484 | 3.73:1 | 6.326 | lossless |
| aviris/f170509t01p00r02rdn_e_sc01_crop21 | 512 x 512 x 224 | 13 | 0 | 3.798 | 3.42:1 | 9.846 | lossless |
| aviris/f170509t01p00r02rdn_e_sc01_crop27 | 512 x 512 x 224 | 13 | 0 | 3.689 | 3.52:1 | 8.302 | lossless |
| aviris/f170509t01p00r02rdn_e_sc01_crop32 | 512 x 512 x 224 | 13 | 0 | 3.625 | 3.59:1 | 8.498 | lossless |
| aviris/f170509t01p00r02rdn_e_sc01_crop34 | 512 x 512 x 224 | 14 | 0 | 3.792 | 3.69:1 | 10.066 | lossless |
| hyperion/EO1H0280342017063110K7_1 | 256 x 1024 x 242 | 13 | 0 | 4.928 | 2.64:1 | 7.828 | lossless |
| hyperion/EO1H0280342017063110K7_2 | 256 x 1024 x 242 | 13 | 0 | 4.903 | 2.65:1 | 7.591 | lossless |
| hyperion/EO1H0430342017071110KF_2 | 256 x 1024 x 242 | 13 | 0 | 4.837 | 2.69:1 | 7.599 | lossless |
| hyperion/EO1H1070332017063110K8_4 | 256 x 1024 x 242 | 15 | 0 | 4.952 | 3.03:1 | 8.242 | lossless |
| hyperion/EO1H1160142017064110KF_0 | 256 x 1024 x 242 | 15 | 0 | 4.773 | 3.14:1 | 7.519 | lossless |
| hyperion/EO1H1290502017065110KF_1 | 256 x 1024 x 242 | 13 | 0 | 4.736 | 2.74:1 | 7.085 | lossless |
| hyperion/EO1H1650432017063110K3_1 | 256 x 1024 x 242 | 15 | 0 | 5.157 | 2.91:1 | 8.730 | lossless |
| hyperion/EO1H1790732017071110KG_2 | 256 x 1024 x 242 | 15 | 0 | 4.979 | 3.01:1 | 8.239 | lossless |
| hyperion/EO1H1870432017064110T3_2 | 256 x 1024 x 242 | 14 | 0 | 5.099 | 2.75:1 | 7.641 | lossless |
| hyperion/EO1H1980182017066110K3_0 | 256 x 1024 x 242 | 15 | 0 | 5.008 | 3.00:1 | 8.700 | lossless |
| hyperion/EO1H1980182017066110K3_2 | 256 x 1024 x 242 | 15 | 0 | 4.998 | 3.00:1 | 8.741 | lossless |
| hyperion/EO1H1980182017066110K3_3 | 256 x 1024 x 242 | 13 | 0 | 5.006 | 2.60:1 | 8.593 | lossless |
| hyperion/EO1H2230682017070110KF_2 | 256 x 1024 x 242 | 15 | 0 | 4.915 | 3.05:1 | 7.912 | lossless |
| indian_pines | 145 x 145 x 220 | 14 | 0 | 6.012 | 2.33:1 | 9.984 | lossless |
