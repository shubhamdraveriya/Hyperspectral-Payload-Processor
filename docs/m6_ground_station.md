# M6: PC ground station

Status: **in progress** (2026-10-12). The ground station decodes captures from a file or
a TCP stream, verifies every scene and displays it (GS-01 .. GS-06). The board's upload
over Ethernet (SW-05) is written and builds; the board test is still to do (the board's
Ethernet is not connected yet).

```
.cap file / TCP ──► records ──► RS(255,223) I=4 ──► AOS frame checks ──► packets ──► scenes ──► CCSDS 123
 (1024 bytes:        status     dual basis,          version, SCID,       first header  sequence   decompression
  codeblock +        word       reedsolo cross-      VCID, VC frame       pointer,      flags,
  status word)                  check (--crosscheck) counts (lost frames) sequence     packet index,
                                                                         counts        scene IDs
                                    └──► per-scene statistics ──► verification against the reference (GS-06)
                                                               └──► display (GS-05), report, .hpdp, .png
```

## Use

```
python -m venv .venv
.venv/Scripts/python -m pip install -r sw/ground/requirements.txt

python sw/ground/hpdp_gs.py decode build/runs/hpdp_m5_fiber_ber1e-3/gr.bin --ref build/runs/hpdp_m5_fiber_ber1e-3 \
    --report report.md --out scenes --png png --crosscheck
python sw/ground/hpdp_gs.py decode CAPTURE.cap --show            # interactive display
python sw/ground/hpdp_gs.py decode --tcp HOST:5001 --show        # live from the board (SW-05)
python sw/ground/hpdp_gs.py serve CAPTURE.cap --port 5001        # replays a capture over TCP
python sw/ground/hpdp_gs.py view scenes/scene_0000.hpdp ...
python -m pytest sw/ground
```

`run_m5.py` now writes the model's corrected scenes (`ref_NNNN.hpdp`) into its work
directory, so every board run has references for `--ref`.

## Parts (`sw/ground/`)

| File | Contents |
|---|---|
| `gs_decode.py` | `GroundDecoder`: incremental, one record at a time, so a file and a live stream go through the same code. RS decoding with the dual-basis conversion and optionally reedsolo (an independent implementation) on every codeword (GS-02); frame header checks, VC frame counts and lost frames, packet extraction by the first header pointer, packet sequence counts, scene assembly from sequence flags, packet indices and scene IDs (missing scenes reported), decompression (GS-03, GS-04). Statistics per scene (frames, RS corrections, uncorrectable codeblocks, lost frames, ASM errors, FLYWHEEL records) and for the link. Sources: `.cap` files and TCP (back-to-back 1024-byte records) |
| `gs_report.py` | Reference scenes, bit-exact comparison with the count of differing samples (GS-06), console lines, Markdown report |
| `gs_view.py` | Display (GS-05): false-colour RGB from three selectable bands, single band with a band slider, spectrum of the clicked pixel with the reference spectrum, per-scene and link statistics; scene slider, left/right keys, `f` follows new scenes while decoding live. `render_png` draws the same panels to a file |
| `hpdp_gs.py` | Command line: `decode`, `view`, `serve` |
| `test_gs.py` | Decoder tests with model streams: idle frames, RS errors (and the reedsolo cross-check), a lost codeblock, a foreign spacecraft's frame, a missing scene, the report |

Decoding and reports need only the standard library and `model/`; numpy and matplotlib
(the venv) only for the display and pictures.

## Results on the M5 board captures

| Capture | Records | RS corrections | Uncorrectable | Scenes bit-exact | Time |
|---|---|---|---|---|---|
| fiber, BER 1e-3, with `--crosscheck` | 1772 | 14 508 (per scene 3662 / 3550 / 3574 / 3722) | 0 | 4 / 4 | 30 s |
| fiber, BER 3e-3 | 1772 | 42 957 | 1 (scene 0: 1 frame lost, 1 packet missing) | 3 / 4 | 16 s |
| fiber, BER 1e-4, live over TCP from `serve` | 1772 | 1 432 | 0 | 4 / 4 | 14 s |

The reedsolo cross-check agreed on all 1772 codeblocks (7088 codewords). Scene timestamps
came back as 0, 64, 128, 192 lines.

Speed: RS decoding about 3.4 ms per codeblock and CCSDS 123 decompression about 2.5 s
per 128 × 64 × 64 scene, in pure Python. Live display therefore runs behind the board's
rate (about 4 scenes per 21 ms); a faster decoder (numba or C) is a GS-04 *may*.

A note from the tests: an error pattern that repeats with a period dividing 255 is a
valid RS codeword (zero syndromes for these generator roots), so it passes the decoder as
"no errors" and is caught only by the frame header checks. Random errors do not do that.

## Upload from the board (SW-05): written, not yet run on the board

`hpdp_run` (job and result version 3) serves the ground records over TCP when a
DOWNLINK job sets `net_ctrl` bit 0 (`run_m5.py --upload`). `sw/baremetal/hpdp_run/src/net.c`:

- lwIP 2.2, raw API, no OS (BSP settings of AMD's `lwip_tcp_perf_server`, added by
  `sw/baremetal/build.py`); GEM3, RGMII to the board's DP83867; locally administered MAC
  `02:48:50:44:50:01`. A 50 ms xiltimer tick only sets flags: the lwIP timers, received
  packets and sending all run in `net_poll()` from the main loop.
- Address: DHCP for up to 15 s (fine on the office network), else the link-local
  `169.254.77.10/16` (a cable straight to a PC port, where Windows takes a 169.254
  address by itself), or a fixed one (`--board-ip`). Printed on the UART and kept in a
  status block at `0x1000_2000`.
- TCP server on port 5001, one client at a time; the ground station connects to it, so
  the PC needs no open port and no administrator rights. Each job's records are sent as
  the ground DMA completes them (live, best effort), the connection closes when all are
  acknowledged, and a client that connects after the run still gets the whole stream
  until the next job (complete upload after the run, as SW-05 asks).
- The app (with lwIP's pools) ends at 37 MB, below the job block at `0x1000_0000`.

`run_m5.py --upload` fetches the stream after the run and compares it byte for byte with
the copy read over JTAG; `hpdp_gs.py decode --tcp <board>:5001 --show` decodes and
displays it, during or after the run. Builds cleanly; the board test waits until the
board's Ethernet can be connected.
