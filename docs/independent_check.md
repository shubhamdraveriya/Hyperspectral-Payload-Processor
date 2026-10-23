# CMP-02, MDL-02: the independent CCSDS 123.0-B-2 check

SPEC CMP-02: "The output shall decode bit-exactly with an independent CCSDS 123.0-B-2
decoder (ESA WhiteDwarf 2.0 decompressor or CNES tools)"; MDL-02 asks for the same
cross-validation of the model. Neither named tool is available here: ESA's software
repository (ESSR) registers only industry of ESA member states (2026-10-23), and the
CNES tools are object code behind a separate registration. This note describes the
check done instead (2026-10-23), with an independent compressor, and why it shows the
same thing.

## The independent implementation

The NTNU SmallSat Lab's CCSDS 123.0-B-2 verification model
(github.com/NTNU-SmallSat-Lab/ccsds123_issue_2_verification_model, commit 9fabb03 of
2026-04-10; Python; MIT licence by its README). Its authors verified it against the
CCSDS test vector set (`Test1-20190201`) and compare it with the CNES tool (its
`verify.py` and `make compare`). It shares no code with this project.

It is a compressor. CCSDS 123 coding is fully determined by the image and the header
(no choices are left to the encoder), so a stream that is byte-identical with the
output of a conforming implementation, for the same image and header, is a
conforming stream: every conforming decoder, WhiteDwarf's and CNES's included,
decodes it to the same image. Our own decoder already gives back every scene
bit-exactly, so the check closes the loop that CMP-02 asks about: that the header and
the coding follow the standard and not only our reading of it.

## The check

`model/xcheck_ntnu.py` compresses each image with our model, gives the NTNU model the
same image (BSQ, 16-bit big-endian) and the header our model wrote, and compares the
two outputs byte for byte, header included; the NTNU model also writes the header
back from its parsed fields, which must equal ours. For board runs the comparison is
with the board's own bytes (`out.bin`) for each scene's compressor input
(`ref_NNNN.hpdp`).

Setup (outside git, under build/ext):

```
git clone https://github.com/NTNU-SmallSat-Lab/ccsds123_issue_2_verification_model build/ext/ntnu_hlm
python -m venv build/ext/ntnu_venv
build/ext/ntnu_venv/Scripts/python -m pip install numpy bitarray psutil
```

Run (14 at a time; about 1 minute for the vectors; a real board scene takes 1 to 2
minutes and 2 to 3 GB, so use `--jobs 4` or fewer with them):

```
python model/xcheck_ntnu.py --hlm build/ext/ntnu_hlm --python build/ext/ntnu_venv/Scripts/python.exe
python model/xcheck_ntnu.py --hlm build/ext/ntnu_hlm --python build/ext/ntnu_venv/Scripts/python.exe \
    --no-vectors --jobs 4 --board build/runs/pm7_real_aviris build/runs/pm7_real_ip
```

The NTNU code runs unchanged, except that `numpy.savetxt` is a no-op (its CSV dumps of
intermediate values would be gigabytes for a real scene; the bitstream and the header
are written by other code). Its pinned package versions do not install on Python 3.14;
the current numpy, bitarray and psutil work.

## Results (2026-10-23)

**944 of 944 images byte-identical** with the NTNU model, header included (log
`build/xcheck_ntnu.log`, after the Nx = 1 change below):

| Set | Images | Content |
|---|---|---|
| VER-02 matrix (`tb/ccsds123/gen_vectors.py`) | 864 | local sum 4 × prediction mode 2 × P ∈ {0, 1, 3, 15} × D ∈ {8, 12, 16} × Ω ∈ {4, 13, 19} × U<sub>max</sub> ∈ {8, 18, 32}, the RTL vectors' scenes |
| Named and corner-case tests (same file) | 59 | R = 32 wrap-around and R = 64, fast weight updates, γ₀ / γ\* and K limits, D = 2, 8, 16, output words of 3 and 8 bytes, Nx = 64, Nz = 16, Nx = 1, Ny = 1, all zero, all maximum at D = 12 and 16 (VER-03) |
| Board runs, synthetic scenes | 16 | `hpdp_m5_fiber_ber0` (M5), `hpdp_m7h_base` (final build m7h), `pm7_sw05_clean2` (2026-10-23), `pm7_1024_syn` and `pm7_1024_ecc` (m7i_1024b, 1024-pixel lines) |
| Board runs, real scenes | 5 | AVIRIS 3 898 952 and 7 063 902 bytes, Hyperion 4 985 044, Hyperion at 1024 pixels 4 905 137 (m7i_1024b), Indian Pines 3 476 350 |

The board's bytes are the RTL's output, so the hardware's streams themselves are
byte-identical with the independent implementation, from M5 to the final builds.

## Finding: full prediction with Nx = 1

The first run had two refusals: the `one_column` test (Nx = 1, column-oriented local
sum) used full prediction, and the NTNU model rejects full prediction for Nx = 1 (its
header check, citing the standard's 4.3.1). With one column the three directional
local differences of full prediction are always 0, so the mode gains nothing there;
the same images in reduced mode were byte-identical. The model now refuses full
prediction for Nx = 1 (`ccsds123._Ctx._validate`), and the `one_column` tests of the
compressor and front-end vectors (VER-03) use reduced mode; the RTL regression passed
with them (all 87 tests, 2026-10-23, `build/sim_regression_2026-10-23.log`; matrix
subset seed 750526). Jobs from the host (`run_m4.py`, `run_m5.py`) are built through
the model's checks, so they cannot ask for it. Open: `hpdp_run` itself only refuses
Nx = 0, and the console runs every SD-card scene with the default configuration (full
prediction, neighbour-oriented local sum), so a 1-pixel-wide scene loaded from a card
would be coded in a configuration that is not valid for Nx = 1 (the local sum too).
No such scene exists in the project's data; a check in `hpdp_run` (Nx ≥ 2 unless
reduced prediction with a column-oriented sum) would close it.

## Status

CMP-02 and MDL-02 are met by this check, as a documented substitution of the named
tools (project report §6), like `reedsolo` for libfec (CC-06). SPEC O-5 is closed. A
decoder-side check stays possible later: hcomp (OCaml, ISC licence;
tangled.org/gazagnaire.org/ocaml-hcomp) is a CCSDS 123.0-B-2 decompressor that its
author checks byte for byte against the same NTNU model, and the CNES tools if a
registration becomes possible.
