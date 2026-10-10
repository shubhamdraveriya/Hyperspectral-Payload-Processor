# M1: Python reference models

Bit-exact models of every HPDP block, standard library only. They are the
reference the RTL is checked against in M2–M5 (SPEC.md MDL-01..04).

## Modules (`model/hpdp_model/`)

| Module | Models | SPEC |
|---|---|---|
| `formats.py` | `.hpdp` scenes, `.cal` tables, `.cap` captures, sample orders | §7 |
| `radiometric.py` | dark / gain correction, bad-pixel replacement, synthetic calibration and raw data | §6.2 |
| `ccsds123.py` | CCSDS 123.0-B-2 predictor, quantizer, mapper, sample-adaptive coder, header; encoder **and** decoder | §6.4 |
| `framing.py` | Space Packets, AOS framer / deframer (M_PDU, first header pointer, idle packets and frames), scene reassembly | §6.5, §6.6 |
| `rs.py` | CCSDS RS(255,223) E=16, dual basis, Berlekamp–Massey / Chien / Forney decoder, interleaving | §6.7 |
| `channel.py` | pseudo-randomizer, ASM, CADUs, byte-aligned frame sync with tolerance and flywheel, error injection | §6.7, §6.8, §6.10 |
| `pipeline.py` | whole chain: raw scenes → CADU stream → recovered scenes, with statistics | §3.1 |
| `synth.py` | synthetic scenes (spectral mixtures, shared texture, noise) | — |

`model/hpdp_tool.py` is a command-line front end: `gen`, `compress`, `decompress`,
`downlink`, `ground` (see its `--help`).

## What is verified, and how

| Item | Evidence | Confidence |
|---|---|---|
| CCSDS 123 encoder/decoder consistency | lossless round trip for 20 configurations × D ∈ {8, 12, 16}, noise at D ∈ {2, 5, 16}, constant images, BSQ / BIL / BIP / sub-frame orders | high |
| Predictor behaviour | `test_interband_prediction_converges`: with the same texture in every band, the previous-band weight converges and the late residual falls below 0.5 (0.02 measured); spatial-only prediction cannot do this | high |
| Near-lossless quantizer | max error ≤ m for m = 1, 3, 7 (body only; header subpart not implemented) | high |
| CCSDS 123 conformance (equations, header bit layout) | written from CCSDS 123.0-B-2; **not yet checked against an independent decoder** | pending O-5 |
| RS generator and dual basis | generator palindromic (reciprocal root pairs), zero syndromes on codewords, dual-basis table from libfec `ccsds_tal.c` | medium–high |
| RS decoder | corrects every pattern of ≤ 16 symbol errors tested, flags ≥ 17 as uncorrectable, 64-byte burst at I = 4 corrected | high |
| RS against an independent implementation | `test_matches_independent_implementation` compares with `reedsolo` (same field and roots); skipped until `reedsolo` is installed | pending |
| Randomizer | output starts `FF 48 0E C0 9A 0D 70 BC 8E 2C 93 AD ...`, period 255 | high |
| Framing | header bit layouts, first header pointer with packets spanning frames, idle-packet fill including the < 7-byte case, resynchronization after a lost frame, idle frames | high |
| Frame sync | leading garbage, ASM with 2 wrong bits (lock kept), 5 wrong bits (flywheel), 4 misses (loss of lock and reacquisition) | high |
| End to end | 3 scenes bit-exact over a clean link and at BER 10⁻³ (≈300 bit errors corrected); an uncorrectable burst loses exactly one scene, which is reported missing | high |

Test count: 137 (136 pass, 1 skipped until `reedsolo` is installed).

## Still open for M1's "done when"

1. **Independent CCSDS 123.0-B-2 decoder** (SPEC O-5): request ESA WhiteDwarf 2.0
   and/or the CNES tools, then decompress `compress()` output with them, starting with
   the header. Until then conformance rests on implementing the standard's text.
2. `pip install reedsolo` (listed in `requirements.txt`) to enable the RS cross-check.

## Notes for M2 (RTL)

- `compress(..., trace=list)` records, per sample in coding order:
  `(z, y, x, sigma, dhat, high_res_prediction, s_hat, mapped_index, k)`. These are the
  intermediate values each compressor sub-block in RTL must reproduce.
- Model speed is about 130 k samples/s including decoding: fine for test scenes, too
  slow for full-size scenes (a C or numba port is a stretch item).
- Defaults (`omega = 13`, `v_min = -1`, `v_max = 3`, `t_inc = 2^6`, `u_max = 18`,
  `gamma0 = 1`, `gamma_star = 6`, `K = 7`) follow common practice; the shared-texture
  test shows `v_max = 9` adapts much better when local differences are large. Tune on
  real data once a dataset is available.
