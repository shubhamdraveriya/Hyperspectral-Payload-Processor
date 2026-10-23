# M6: PC ground station

Status: **in progress** (2026-10-12). The ground station decodes captures from a file or
a TCP stream, verifies every scene and displays it (GS-01 .. GS-06). The board's upload
over Ethernet (SW-05) is tested on the board (2026-10-23, below): the records fetched over
TCP are identical to the copy read over JTAG, and the ground station decodes them live,
every scene bit-exact.

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

## Upload from the board (SW-05)

`hpdp_run` (job and result version 3) serves the ground records over TCP when a
DOWNLINK job sets `net_ctrl` bit 0 (`run_m5.py --upload`). `sw/baremetal/hpdp_run/src/net.c`:

- lwIP 2.2, raw API, no OS (BSP settings of AMD's `lwip_tcp_perf_server`, added by
  `sw/baremetal/build.py`); GEM3, RGMII to the board's DP83867; locally administered MAC
  `02:48:50:44:50:01`. A 50 ms xiltimer tick (TTC0, `XILTIMER_tick_timer` in
  `build.py`) only sets flags: the lwIP timers, received packets and sending all run in
  `net_poll()` from the main loop.
- The link: the lwIP port resets the PHY and waits only 5 s for its autonegotiation;
  `net.c` then checks the PHY itself, up to 20 s at the start and every second after
  (a slow link partner, or a cable plugged in later), and sets the MAC to 1000 Mbit/s.
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
displays it, during or after the run. For a link-local board address the PC connects
from its own link-local address (after the board's reset Windows otherwise can send the
connection out of another adapter).

On the board (2026-10-23, a USB-Ethernet adapter, docs/post_m7_board_tests.md §5):
`run_m5.py --upload` four runs in a row (one with the cable plugged in only after the
start), each 1 814 528 bytes over TCP identical to the JTAG copy; `hpdp_gs.py decode --tcp 169.254.77.10:5001`: 1 772 records, 4 of 4 scenes
bit-exact. The first runs found two faults on the board (the lwIP timers never ran; a
slow PHY autonegotiation left the MAC unconfigured) and one on the PC (the adapter
choice); all three are fixed, see the runbook. The ground station's TCP input was tested
on the PC before that against `hpdp_gs.py serve` (M6; 2026-10-15 with the board's Indian
Pines capture: 3 960 records over TCP, the scene bit-exact).
