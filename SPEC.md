# Hyperspectral Payload Data Processor (HPDP) — Specification

| | |
|---|---|
| Document | SPEC-HPDP-001 |
| Version | 0.1 (draft) |
| Date | 2026-10-10 |
| Owner | Shubham Draveriya |
| Platform | AMD ZCU106 (Zynq UltraScale+ XCZU7EV-2FFVC1156) |
| Tools | Vivado / Vitis 2025.2, Python 3 |
| Builds on | Image-Processing-Accelerator-for-Edge-Detection (line buffers, CDC, flow control, golden-model verification) |

**Requirement keywords.** *shall* = required for v1.0. *should* = goal for v1.0. *may* = stretch goal (§15).
Each requirement has an ID (e.g. `CMP-03`) that tests and documentation refer back to.

---

## 1. Purpose and scope

The HPDP is an FPGA emulation of the onboard data chain of a hyperspectral
Earth-observation satellite. It:

1. takes raw pushbroom sensor data (emulated from images stored in DDR),
2. corrects it radiometrically (dark offset, gain, bad pixels),
3. compresses it losslessly according to **CCSDS 123.0-B-2**,
4. formats it for downlink according to CCSDS (Space Packets, AOS transfer frames,
   Reed-Solomon coding, pseudo-randomization, sync marker),
5. sends it over a 1.25 Gbaud optical link (SFP, OM3 fiber),
6. receives it on a "ground" side that recovers the corrected image bit-exactly.

v1.0 covers milestones M0 to M7 (§14). Real flight hardware would use radiation-tolerant
parts; this project reproduces the architecture, the standards and the verification
approach on a commercial board.

---

## 2. Reference mission

The design is sized for this scaled-down hyperspectral payload. All image dimensions are
run-time registers, so other sizes up to the build-time maxima (§4, `SYS-04`) also work.

| Parameter | Value |
|---|---|
| Pixels across track (Nx) | 512 |
| Spectral bands (Nz) | 150 |
| Sample depth (D) | 12 bits, carried in 16-bit containers |
| Line rate | 1,000 lines/s (line period 1 ms) |
| Samples per line | 76,800 |
| Raw sample rate | 76.8 Msamples/s |
| Raw data rate | 921.6 Mbit/s (12-bit) |
| Scene length (Ny) | 256 lines (default, register) |
| Scene size | 19.7 Msamples, 39.3 MB in 16-bit containers |

---

## 3. System overview

### 3.1 Data flow

```
SATELLITE SIDE (PL)                                 GROUND SIDE
PS DDR4: raw scenes, calibration tables             PC ground station (Python):
  │ AXI DMA (MM2S)                                    RS decode, frames → packets,
  ▼                                                   decompress, display, verify
Sensor emulator ......... clk_sensor                  ▲ TCP over 1 GbE
  ▼  (non-stallable, BIL order)                     A53 bare-metal application
Radiometric correction  ┐                             ▲
  ▼                     │                           PS DDR4 capture buffer
Corner turn BIL → BIP   │                             ▲ AXI DMA (S2MM)
  ▼                     │ clk_sys                   Derandomizer
CCSDS 123 compressor    │                             ▲
  ▼                     │                           Frame sync (ASM search/verify/lock)
Space packetizer        │                             ▲ clk_sys
  ▼                     │                           Link RX: 8b/10b, comma align,
AOS framer (+ idle)     │                             ▲ clock correction, lock FSM
  ▼                     │                             │ clk_link
RS(255,223) I=4 → randomizer → ASM                    │
  ▼                     ┘                             │
Error injector                                        │
  ▼                                                   │
Link TX: 8b/10b, idles, CC ── GTH ── SFP0 ══ OM3 ══ SFP1 ── GTH
            clk_link (TX and RX)
```

### 3.2 Hardware / software split

| Part | Runs on | Responsibilities |
|---|---|---|
| Satellite chain | PL | Sensor emulator → link TX |
| Ground receiver | PL | Link RX, frame sync, derandomizer, capture DMA |
| Control software | A53 core, bare-metal | Load data from SD, configure blocks, run scenes, collect statistics, upload captures |
| Ground station | PC, Python | RS decoding, frame and packet extraction, decompression, display, verification |

### 3.3 Physical setup

- SFP0 TX → OM3 multimode fiber (1 m) → SFP1 RX, using 1000BASE-SX modules (850 nm, 1.25 Gbaud).
- PC on the PS Gigabit Ethernet port (RJ45). UART console over USB.
- SD card holds raw scenes and calibration tables.

### 3.4 Operating modes

| Mode | Description |
|---|---|
| `COMPRESS` | Normal operation: full chain with CCSDS 123 compression |
| `RAW` | Compressor bypass: samples packed raw. Demonstrates that raw data exceeds link capacity (`SYS-09`) |
| `CAPTURE_CMP` | Compressor output written straight to DDR via DMA (used in M4, before the downlink chain exists) |
| `PRBS` | Link test: GTH PRBS-31 generator/checker, BER counting |

---

## 4. System requirements

| ID | Requirement |
|---|---|
| SYS-01 | The HPDP shall process the reference mission rate (76.8 Msamples/s) continuously, without dropping lines, in `COMPRESS` mode for scenes whose compressed rate fits the link. |
| SYS-02 | In lossless mode the image reconstructed at the ground station shall be bit-identical to the radiometrically corrected image (as produced by the Python model), for link BER up to 1×10⁻³. |
| SYS-03 | All run-time parameters (image size, compressor settings, framing settings, modes) shall be set through AXI4-Lite registers from the PS, without rebuilding the FPGA, within the build-time maxima. |
| SYS-04 | Build-time maxima shall be parameters: `NX_MAX` = 512 (shall scale to 1024 by changing the parameter), `NZ_MAX` = 256, `D_MAX` = 16. |
| SYS-05 | The sensor emulator output shall not be back-pressurable, like a real detector. Any overflow anywhere downstream shall be detected, counted and reported, never silent. |
| SYS-06 | Each scene shall be compressed independently, so that an uncorrectable transmission error affects at most one scene. |
| SYS-07 | Every block shall provide status counters (samples, lines, packets, frames, codeblocks, sync losses, link errors, overflows) readable by the PS. |
| SYS-08 | The design shall meet timing on all clocks, and `report_cdc` shall show no unsafe or unknown crossings without a documented waiver. |
| SYS-09 | In `RAW` mode at the reference rate the system shall show line drops (raw data exceeds link capacity, §12.2); in `COMPRESS` mode it shall not. |
| SYS-10 | The Vivado project shall be recreated from Tcl scripts in the repository. No binary project archives in version control. |

---

## 5. Platform

### 5.1 Device resources (XCZU7EV)

| Resource | Available | v1.0 budget (estimate, §12.1) |
|---|---|---|
| LUTs | 230,400 | < 25 % |
| Flip-flops | 460,800 | < 20 % |
| DSP48E2 | 1,728 | < 5 % |
| Block RAM (36 Kb) | 312 | < 40 % |
| UltraRAM (288 Kb) | 96 | < 60 % (RAD-02: the ECC calibration table holds one entry per 64-bit word, 32 URAM) |
| GTH transceivers | used: 2 lanes (SFP0 TX, SFP1 RX) | |

### 5.2 Clocks

| Clock | Frequency | Source | Used by |
|---|---|---|---|
| `clk_ctrl` | 100 MHz | PS `pl_clk0` | AXI4-Lite control, register blocks |
| `clk_sys` | 250 MHz target, 200 MHz minimum | PS `pl_clk1` or MMCM | DMA data path, front end, compressor, downlink chain, ground receiver |
| `clk_sensor` | 100 MHz | MMCM, independent of `clk_sys` | Sensor emulator output timing |
| `clk_link` | 62.5 MHz | GTH TXOUTCLK via BUFG_GT (wizard TX user clock helper) | Link layer (16-bit user interface). The RX side runs on the same clock: the GTH elastic buffers bridge the recovered clocks |
| `clk_fr` | 31.25 MHz | 125 MHz board clock (H9/G9) / 4, BUFGCE_DIV | GTH free-running and DRP clock |
| GTH reference | 156.25 MHz | Si570 through an SI53340 buffer on MGTREFCLK1 of quad 226 (U10/U9), used by quad 225 one quad south; no I2C setup needed (O-2) | Transceivers |

All clock-domain crossings use XPM macros (`xpm_fifo_async`, `xpm_cdc_*`). No hand-written
synchronizers except where XPM has no equivalent.

### 5.3 Resets

- One reset synchronizer per clock domain: asynchronous assert, synchronous release.
- Reset sources: PS `pl_resetn0`, MMCM `locked`, software soft reset (global and per block).
- GTH resets follow the Transceivers Wizard reset helpers; the link layer stays in reset
  until `tx_reset_done` / `rx_reset_done`.

### 5.4 Interface conventions

| Rule | Detail |
|---|---|
| Streams | AXI4-Stream between all data-path blocks |
| Sample streams | `tdata[15:0]` = unsigned sample in the D LSBs. `tuser[0]` = first sample of scene. `tuser[1]` = first sample of line. `tlast` = last sample of line |
| Byte streams | `tdata[7:0]`. `tlast` and `tuser` meaning defined per interface (end of packet / frame / CADU) |
| Back-pressure | Every block shall accept any `tvalid`/`tready` pattern. Verified with random stalls (`VER-01`) |
| Registered boundaries | `tdata`/`tvalid` registered at every module output. `tready` registered through skid buffers |
| Control | AXI4-Lite, 32-bit, one 64 KB window per block (§8) |
| Bulk data | AXI4 via AXI DMA into PS DDR4 through S_AXI_HP ports |

---

## 6. Block specifications

### 6.1 Sensor emulator (SE)

Emulates a pushbroom detector. Each line is read out band by band, i.e. in BIL order: band 0
pixels 0..Nx−1, then band 1, and so on.

| ID | Requirement |
|---|---|
| SE-01 | Shall read raw scenes from PS DDR4 through AXI DMA (MM2S) in BIL order. |
| SE-02 | Shall start a line every `LINE_PERIOD` cycles of `clk_sensor` (32-bit register) and output that line's samples back to back, one per cycle. |
| SE-03 | Shall buffer ≥ 32 KB from DMA ahead of the output. If the data for a line is not ready when the line starts, it shall count an emulator underrun (a test-setup fault, not a design failure). |
| SE-04 | Its output shall never wait for downstream. If the downstream CDC FIFO is full, the sample is dropped and the line flagged and counted (`SYS-05`). |
| SE-05 | Shall keep line and scene counters and a free-running 24-bit line timestamp, recorded at each scene start (used in packet headers, §6.5). |
| SE-06 | Shall include a built-in pattern generator (ramp, LFSR) for bring-up without DMA. |

### 6.2 Radiometric correction (RC)

Operates on the BIL stream, so spatial neighbours in the same band are adjacent in time.

For sample `x` of band `z`:

```
c = clamp( ((x − dark[z][x]) × gain[z][x] + 2^13) >> 14 , 0 , 2^D − 1 )     gain is unsigned Q2.14 (0x4000 = 1.0)
```

Bad-pixel replacement, using pre-replacement values `c` and good flags `g` (positions outside
0..Nx−1 count as bad):

```
out[x] = c[x]                          if g[x]
       = (c[x−1] + c[x+1] + 1) >> 1    else if g[x−1] and g[x+1]
       = c[x−1]                        else if g[x−1]
       = c[x+1]                        else if g[x+1]
       = 0 (and counted)               otherwise
```

| ID | Requirement |
|---|---|
| RC-01 | Shall store `dark` (16 bit), `gain` (16 bit) and `bad` (1 bit) per (band, pixel) in UltraRAM, sized for `NX_MAX × NZ_MAX`. |
| RC-02 | Tables shall be loadable by DMA (table-load stream) and readable/writable through AXI4-Lite for debug. |
| RC-03 | Throughput 1 sample per `clk_sys` cycle. |
| RC-04 | Shall provide a bypass mode (output = input). |
| RC-05 | Shall count clamp events and replaced bad pixels per scene. |
| RC-06 | *may*: double-buffered tables, swapped at scene boundaries. |

### 6.3 Corner turn (CT)

| ID | Requirement |
|---|---|
| CT-01 | Shall reorder each line from BIL to BIP (pixel 0 bands 0..Nz−1, then pixel 1, ...). |
| CT-02 | Shall use two line buffers in UltraRAM (ping-pong): write line n while reading line n−1. |
| CT-03 | Throughput 1 sample per cycle on both sides. Latency about one line. |
| CT-04 | Output may be back-pressured by the compressor. If both buffers are full when a new line starts, the line shall be dropped and counted (`SYS-05`), and a fill line of zeros sent in its place, so that scenes keep their geometry and the compressor stays aligned with scene boundaries. |

### 6.4 CCSDS 123.0-B-2 compressor (CMP)

#### 6.4.1 Supported configuration

| Parameter | Standard allows | v1.0 supports |
|---|---|---|
| Nx, Ny, Nz | 1 – 65,535 each | Nx ≤ `NX_MAX`, Ny ≤ 65,535, `NZ_MIN` ≤ Nz ≤ `NZ_MAX` |
| Dynamic range D | 2 – 32 bits | 2 – 16 bits, unsigned samples |
| Encoding order | BSQ, or band-interleaved with depth M | Band-interleaved with M = Nz (BIP) |
| Prediction bands P | 0 – 15 | 0 – `P_MAX` (build-time, ≤ 15, default 15) |
| Prediction mode | full, reduced | both |
| Local sum type | wide / narrow, neighbour- / column-oriented | all four |
| Weight resolution Ω | 4 – 19 | 4 – 19 |
| Register size R | max(32, D+Ω+2) – 64 | as the standard |
| Weight update scaling (ν_min, ν_max, t_inc) | standard ranges | standard ranges |
| Weight exponent offsets | standard ranges | zero (*may*: configurable) |
| Weight initialization | default, custom | default (*may*: custom) |
| Quantization | lossless; absolute and/or relative error limits; band-dependent; periodic updates | lossless (v1.0). Absolute, band-independent limit in v1.1 (`CMP-10`). Others *may* |
| Entropy coder | sample-adaptive, hybrid, block-adaptive | sample-adaptive (v1.0). Hybrid *may*. Block-adaptive not planned |
| Sample-adaptive parameters (U_max, γ₀, γ*, K) | standard ranges | standard ranges |
| Output word size B | 1 – 8 bytes | 1 – 8 bytes |
| Supplementary information tables | 0 – 15 | none |
| Header | image, predictor and entropy-coder metadata | generated in hardware from registers |

#### 6.4.2 Requirements

| ID | Requirement |
|---|---|
| CMP-01 | The output bitstream shall be bit-identical to the Python reference model (`model/ccsds123`) for every supported configuration. |
| CMP-02 | The output shall decode bit-exactly with an independent CCSDS 123.0-B-2 decoder (ESA WhiteDwarf 2.0 decompressor or CNES tools). *Deviation (2026-10-15, O-5): shown instead by byte identity with an independent CCSDS 123.0-B-2 compressor; see docs/project_report.md §6.* |
| CMP-03 | Lossless mode shall sustain 1 sample per cycle at `clk_sys` ≥ 200 MHz (target 250 MHz), for any image content, when the output is not back-pressured. |
| CMP-04 | Shall support output back-pressure by stalling the whole pipeline, without losing data. |
| CMP-05 | Per-band state (weights, entropy-coder accumulators and counters) shall live in on-chip RAM and be reused every Nz samples. `NZ_MIN` (expected ≥ 32) shall be documented from the final pipeline depth. |
| CMP-06 | Shall hold one previous line (Nx × Nz samples) for neighbour access. |
| CMP-07 | Bypass mode (`RAW`): emit samples packed at D bits, MSB first, in the same output stream. |
| CMP-08 | Each scene shall start from freshly initialized state and a complete header (`SYS-06`). |
| CMP-09 | Shall count input samples and output bytes per scene (compression-ratio readout). |
| CMP-10 | v1.1: near-lossless with an absolute, band-independent error limit. May run below 1 sample per cycle; the achieved rate shall be documented. |
| CMP-11 | *may*: near-lossless at full rate (removing the quantizer from the feedback loop, Sánchez et al. 2022). |
| CMP-12 | *may*: hybrid entropy coder. |

#### 6.4.3 Architecture notes (informative)

- **Why BIP.** In BIP order, consecutive samples belong to different bands of the same
  pixel. A band's weights and accumulators are needed again only Nz cycles later, so the
  weight-update and coder-state loops can be pipelined deeply without hazards. That is how
  1 sample per cycle at 200+ MHz becomes realistic, and it is where `NZ_MIN` comes from.
- **Neighbourhood.** Local sums use the current band's neighbours (W, NW, N, NE). This is the
  3D successor of the edge-detection project's window generator: a previous-line buffer
  plus a short delay line.
- **Near-lossless loop.** In near-lossless mode, prediction uses reconstructed (quantized)
  values. The previous band of the same pixel is the immediately preceding sample in BIP,
  so the quantizer sits inside a one-sample feedback loop. That is why v1.0 is lossless
  and v1.1 near-lossless may run slower.

### 6.5 Space packetizer (PKT) — CCSDS 133.0

Each scene's compressed bitstream is split into packets carrying `PKT_DATA` user bytes
(register, default 2048). The last packet of a scene may be shorter.

| Field | Bits | Value |
|---|---|---|
| Packet version | 3 | `000` |
| Packet type | 1 | `0` (telemetry) |
| Secondary header flag | 1 | `1` |
| APID | 11 | register (default `0x100`) |
| Sequence flags | 2 | `01` first packet of scene, `00` continuation, `10` last, `11` scene fits in one packet |
| Packet sequence count | 14 | per APID, wraps |
| Packet data length | 16 | (secondary header + user data bytes) − 1 |
| Secondary header: scene ID | 16 | increments per scene |
| Secondary header: packet index in scene | 16 | 0, 1, 2, ... |
| Secondary header: scene start timestamp | 24 | line timestamp from `SE-05` |
| Secondary header: flags | 8 | bit 0 internal uncorrectable error in scene, bit 1 lines dropped in scene, bit 2 `RAW` mode, others 0 |
| User data | ≤ `PKT_DATA` × 8 | compressed bitstream (or raw samples in `RAW` mode) |

| ID | Requirement |
|---|---|
| PKT-01 | Shall produce packets exactly as above, matching the Python model. |
| PKT-02 | Throughput ≥ 1 byte per `clk_sys` cycle. |
| PKT-03 | Shall count packets and scenes. |

### 6.6 AOS framer (AOS) — CCSDS 732.0

Fixed frame length of 892 bytes (= 223 × I, with I = 4), so that one frame fills one RS codeblock.

| Field | Bits | Value |
|---|---|---|
| Transfer frame version | 2 | `01` |
| Spacecraft ID | 8 | register |
| Virtual channel ID | 6 | science: register (default 1). Idle frames: 63 |
| VC frame count | 24 | per virtual channel |
| Signaling: replay flag | 1 | `0` |
| Signaling: VC frame count usage flag | 1 | `0` |
| Signaling: spare | 2 | `00` |
| Signaling: VC frame count cycle | 4 | `0000` |
| M_PDU header: spare | 5 | `00000` |
| M_PDU header: first header pointer | 11 | offset of the first packet header in the packet zone; `0x7FF` if no packet starts in this frame |
| Packet zone | 884 bytes | space packets, which may span frames |
| Frame header error control, OCF, FECF | — | not used (Reed-Solomon provides error control) |

| ID | Requirement |
|---|---|
| AOS-01 | Shall output frames continuously. When no science data is ready and the link can accept a frame, it shall insert an idle frame (VCID 63, data field filled with a fixed pattern), so the downlink never pauses. |
| AOS-02 | Shall support an optional science-frame rate limit (register: minimum `clk_sys` cycles between science frames, set by software from a frames-per-second figure; 0 = unlimited) to emulate a lower downlink capacity. |
| AOS-03 | Shall set the first header pointer correctly when packets span frames. |
| AOS-04 | Shall count science frames and idle frames. |

### 6.7 Channel coding (CC) — CCSDS 131.0

Order: transfer frame → Reed-Solomon encode → randomize the codeblock → prepend ASM → CADU.

| ID | Requirement |
|---|---|
| CC-01 | RS(255,223), E = 16, per CCSDS 131.0: field polynomial x⁸+x⁷+x²+x+1, generator roots α^(11j) for j = 112..143, symbols in Berlekamp dual-basis representation. |
| CC-02 | Interleaving depth I = 4 (build-time parameter; the standard allows 1–5 and 8), giving a 1020-byte codeblock. |
| CC-03 | Pseudo-randomizer h(x) = x⁸+x⁷+x⁵+x³+1, reset to all ones at the start of each codeblock, XORed over the codeblock only (not the ASM). The sequence starts `FF 48 0E C0 ...`. |
| CC-04 | Attached sync marker `1A CF FC 1D` prepended, giving a 1024-byte CADU. |
| CC-05 | Throughput ≥ 1 byte per `clk_sys` cycle. |
| CC-06 | The RS encoder output shall match an independent implementation (libfec `encode_rs_ccsds`) on random frames. |

### 6.8 Error injector (EI)

Sits on the CADU byte stream between channel coding and the link, standing in for an RF channel.

| ID | Requirement |
|---|---|
| EI-01 | Modes: off; random bit errors at a programmable BER (32-bit threshold, resolution 2⁻³²); burst errors (L consecutive bits every N bytes); ASM-targeted (k bits flipped in the sync marker). |
| EI-02 | Seed shall be programmable, so runs are reproducible. |
| EI-03 | Shall count injected bit errors (ground truth for BER measurements). |
| EI-04 | Shall operate at ≥ 1 byte per cycle with no throughput loss. |

### 6.9 Optical link (LNK)

| ID | Requirement |
|---|---|
| LNK-01 | GTH transceivers at 1.25 Gbaud with the GTH's built-in 8b/10b encoding and a 16-bit user interface (62.5 MHz), configured with the UltraScale Transceivers Wizard. |
| LNK-02 | Physical path SFP0 TX → OM3 → SFP1 RX with 1000BASE-SX modules. Shall work in one direction only (no back channel), like an RF downlink. |
| LNK-03 | TX link layer: CADU bytes sent as data characters. When its input FIFO is empty it sends idle words `{K28.5, D16.2}`. It inserts a clock-correction word at least every `CC_INTERVAL` words (register, default 2048). |
| LNK-04 | RX: comma alignment on K28.5 into byte 0 of the 16-bit word, clock correction in the elastic buffer, K-characters stripped, data bytes output as a stream. |
| LNK-05 | RX lock state machine: `LOCKED` after 1024 consecutive error-free words following alignment. Loses lock after more than 8 code errors (disparity or not-in-table) within 1024 words. Counts code errors and lock losses. |
| LNK-06 | `PRBS` mode: GTH built-in PRBS-31 generator and checker, with a readable error counter. |
| LNK-07 | ≥ 999 Mbit/s available to CADUs (1.0 Gbit/s after 8b/10b, minus clock-correction overhead). |
| LNK-08 | Crossings between `clk_sys` and the link clocks through XPM async FIFOs. |
| LNK-09 | SFP `TX_DISABLE` shall be driven low through its control path (on the ZCU106: PL pins `SFP0_TX_DISABLE_B` AE22 and `SFP1_TX_DISABLE_B` AF20, high = transmitter on). `LOS` / `TX_FAULT` are not wired to the PL on this board (O-3); *may*: read them from the module's diagnostics over I2C. |
| LNK-10 | *should*: eye scan with In-System IBERT, documented in the project report. |

### 6.10 Ground receiver (GR)

| ID | Requirement |
|---|---|
| GR-01 | Frame sync on the received byte stream: ASM correlation allowing ≤ `ASM_TOL` mismatched bits (register, default 2). States SEARCH → VERIFY (2 consecutive ASMs 1024 bytes apart) → LOCK. In LOCK, coast through up to `FLYWHEEL` (default 3) consecutive missed ASMs before returning to SEARCH. |
| GR-02 | Byte-aligned search in v1.0. *may*: bit-level search with polarity-inversion detection, as a real demodulator output would need. |
| GR-03 | Shall derandomize each 1020-byte codeblock. |
| GR-04 | Shall write each codeblock plus a 4-byte status word (ASM bit errors, sync state, sequence number) to PS DDR4 through AXI DMA (S2MM): 1024 bytes per record. |
| GR-05 | Shall count CADUs, sync losses and ASM bit errors. |
| GR-06 | *may*: hardware RS decoder (Berlekamp–Massey, Chien search, Forney), with correction counters. |

### 6.11 Data movement and memory (DM)

| ID | Requirement |
|---|---|
| DM-01 | AXI DMA (PG021) instances: MM2S for sensor data and table loads; S2MM for ground capture; S2MM for compressor-output capture (`CAPTURE_CMP` mode). |
| DM-02 | PS DDR4 holds the raw scene buffer, the calibration tables and the capture ring buffer. Addresses are set by software. |
| DM-03 | DMA data paths use S_AXI_HP ports, 128 bit, at `clk_sys`. |

---

## 7. File formats

These formats are the contract between the Python tools, the bare-metal application and the hardware.

All multi-byte fields are little-endian. `model/hpdp_model/formats.py` is the reference implementation.

| File | Format |
|---|---|
| Raw scene (`.hpdp`) | 64-byte header, then `uint16` samples in the order given by the header (BIL by default) |
| Calibration table (`.cal`) | 64-byte header, then one 8-byte record per (band, pixel), band-major: `uint16` dark, `uint16` gain, `uint8` flags (bit 0 = bad), 3 reserved bytes. 8-byte records keep DMA table loads aligned |
| Ground capture (`.cap`) | Sequence of 1024-byte records: 1020-byte derandomized codeblock + `uint32` status word (`GR-04`) |
| Reconstructed scene | Same as raw scene format, with D = sample depth after correction |

Scene header (unused bytes up to 64 are zero):

| Offset | Type | Field |
|---|---|---|
| 0 | char[4] | magic `HPDP` |
| 4 | uint16 | version (1) |
| 6 | uint16 | header size (64) |
| 8 | uint32 | Nx |
| 12 | uint32 | Ny |
| 16 | uint32 | Nz |
| 20 | uint8 | D (sample depth, 2–16) |
| 21 | uint8 | sample order: 0 BIL, 1 BIP, 2 BSQ |

Calibration header: magic `HCAL` (char[4]), version (uint16), header size (uint16),
Nx (uint32), Nz (uint32), gain fraction bits (uint8, 14), zero padding to 64 bytes.

Capture status word: bits [5:0] ASM mismatched bits, [7:6] sync state (0 SEARCH,
1 VERIFY, 2 LOCK, 3 FLYWHEEL), [15:8] reserved, [31:16] CADU sequence number.

---

## 8. Control and status

### 8.1 Address map (PS master port M_AXI_HPM0_FPD)

| Base address | Block |
|---|---|
| `0xA000_0000` | System: ID, build info, global control, mode, interrupt summary |
| `0xA001_0000` | Sensor emulator |
| `0xA002_0000` | Radiometric correction (registers + table window) |
| `0xA003_0000` | Corner turn |
| `0xA004_0000` | CCSDS 123 compressor |
| `0xA005_0000` | Space packetizer |
| `0xA006_0000` | AOS framer + channel coding |
| `0xA007_0000` | Error injector |
| `0xA008_0000` | Link TX/RX (GTH control, PRBS, counters, SFP status) |
| `0xA009_0000` | Ground receiver |
| `0xA00F_0000` | Radiation monitor (SEM IP interface, ECC/TMR counters) |
| `0xA010_0000` | AXI DMA: MM2S (raw scenes, tables), S2MM (compressor capture) |
| `0xA011_0000` | AXI DMA: S2MM (ground capture) |

The register blocks share one 1 MB window (`0xA000_0000` – `0xA00F_FFFF`), so the DMA
instances sit above it.

### 8.2 Common register layout (every block)

| Offset | Name | Description |
|---|---|---|
| `0x00` | `ID` | Block ID (16 bit) and version (16 bit) |
| `0x04` | `CTRL` | bit 0 enable, bit 1 soft reset, bit 2 start, others block-specific |
| `0x08` | `STATUS` | bit 0 busy, bit 1 error, others block-specific |
| `0x0C` | `IRQ_EN` | Interrupt enables |
| `0x10` | `IRQ_STATUS` | Interrupt flags, write 1 to clear |
| `0x14`–`0x3C` | — | Reserved |
| `0x40`+ | — | Block-specific registers and counters |

### 8.3 Register implementation

- All register maps shall be described in **SystemRDL** (`regs/`). PeakRDL generates the
  SystemVerilog register blocks, C headers, Python definitions and HTML documentation, so
  hardware, software and docs never disagree.
- Counters are 32 bit and saturating. Writing 1 to a block's `CNT_CLEAR` (offset `0x7C`)
  clears its counters; the block's counters start at `0x80`.
- The register block runs on `clk_ctrl`; configuration reaches the data-path clocks, and
  status comes back, as consistent snapshots through XPM handshake crossings (about a
  microsecond). `SYS.STATUS.cfg_pending` is set until every register write has arrived in
  every clock domain: software waits for 0 after configuring, before starting DMA or a run.

### 8.4 Interrupts

Each block drives one interrupt line, concatenated into `pl_ps_irq0`. Sources: scene done,
overflow, sync loss, link lock loss, uncorrectable ECC error, watchdog.

---

## 9. Software

### 9.1 PS bare-metal application (SW)

| ID | Requirement |
|---|---|
| SW-01 | Standalone application on one A53 core, built with Vitis 2025.2. |
| SW-02 | Shall load raw scenes and calibration tables from the SD card (FAT32, §7). |
| SW-03 | Startup sequence: reset → link bring-up (wait for `LOCKED`) → load tables → configure blocks → start. |
| SW-04 | UART console commands: run scene(s), select mode, PRBS test, error-injector settings, status dump. |
| SW-05 | Shall capture the ground-receiver output into a DDR ring buffer and upload it to the PC over TCP (lwIP, 1 GbE). Live streaming best effort; complete upload after the run guaranteed. |
| SW-06 | Shall report per-scene statistics (compression ratio, frames, errors) on the console and to the PC. |

### 9.2 PC ground station (GS)

| ID | Requirement |
|---|---|
| GS-01 | Python 3.10+ with numpy. Input from TCP or a `.cap` file. |
| GS-02 | CCSDS RS(255,223) decoding with dual-basis conversion (from `model/`), cross-checked against libfec. |
| GS-03 | AOS frame checks (version, spacecraft ID, VC frame counts → lost-frame detection). Packet extraction with the first header pointer. Scene reassembly from sequence flags and counts (gap detection). |
| GS-04 | Decompression with the project's Python decoder (*may*: C or numba acceleration). Optional cross-check with the ESA/CNES decoder. |
| GS-05 | Display: false-colour RGB from 3 selectable bands, single-band view, spectrum of a selected pixel, per-scene statistics (compression ratio, RS corrections, sync losses, residual errors). |
| GS-06 | Verification report: bit-exact comparison with the model's corrected scene, pass/fail per scene. |

### 9.3 Python models (MDL)

| ID | Requirement |
|---|---|
| MDL-01 | `model/` shall contain a bit-exact model of every block, with pytest unit tests. |
| MDL-02 | CCSDS 123 encoder **and** decoder, with round-trip tests over the configuration matrix (`VER-02`) and cross-validation against an independent decoder. *Deviation (2026-10-15, O-5): cross-validated against an independent compressor instead (`model/xcheck_ntnu.py`); see docs/project_report.md §6.* |
| MDL-03 | Shall generate the test vectors used by the RTL testbenches. |
| MDL-04 | End-to-end model: raw scene → CADUs, and captured codeblocks → reconstructed scene. |

---

## 10. Radiation mitigation (RAD)

| ID | Requirement |
|---|---|
| RAD-01 | AMD Soft Error Mitigation IP (PG187) shall run in correction mode (configuration memory scrubbing). Status and counters exposed to the PS. Error injection through its command interface, controlled from the PS. |
| RAD-02 | UltraRAM used for data and tables shall run with its built-in ECC enabled (single-bit correct, double-bit detect), with counters. |
| RAD-03 | Other critical state (compressor weights and accumulators, configuration registers) shall be protected by SECDED (custom Hamming code) or TMR. |
| RAD-04 | Control state machines shall be triplicated with majority voting, kept separate through synthesis (`DONT_TOUCH` / `KEEP_HIERARCHY`), with mismatch counters. |
| RAD-05 | Watchdogs: a PL pipeline watchdog (no progress for N cycles while input is pending → error interrupt) and the PS system watchdog for software. |
| RAD-06 | A detected uncorrectable error shall set the flag in the affected scene's packet headers (§6.5) and raise an interrupt. |
| RAD-07 | Fault-injection campaign: (a) in simulation, random bit flips in registers and memories, each outcome classified as masked, corrected, detected or silent corruption; (b) on hardware, SEM IP configuration upsets and ECC error injection. Results written up with coverage figures. |

---

## 11. Verification and validation (VER)

| ID | Requirement |
|---|---|
| VER-01 | Every RTL block shall have a self-checking testbench driven by model vectors, with random `tvalid`/`tready` stalls on all stream interfaces. |
| VER-02 | Compressor configuration matrix: local sum type (4) × prediction mode (2) × P ∈ {0, 1, 3, `P_MAX`} × D ∈ {8, 12, 16} × Ω ∈ {4, 13, 19} × U_max ∈ {8, 18, 32}. A random subset in every regression, the full matrix in a nightly run. |
| VER-03 | Corner-case images: all zero, all maximum, random (incompressible), Nx = 1, Nz = `NZ_MIN`, Nz = `NZ_MAX`, Nx = `NX_MAX`. |
| VER-04 | Downlink chain output identical to the model. RS encoder also checked against libfec (`CC-06`). |
| VER-05 | System simulation: a small scene (e.g. 64 × 32 × 32, 8 lines) through the whole chain, with a link-layer loopback model and injected errors. Ground output identical to the model. |
| VER-06 | Hardware link test: PRBS-31 for ≥ 3×10¹² bits with zero errors (BER < 10⁻¹² at 95 % confidence; about 40 minutes at 1.25 Gbaud). |
| VER-07 | Hardware end-to-end: every test scene reconstructed bit-exact at injected BER 0, 10⁻⁴ and 10⁻³. BER sweep beyond that to document where RS fails (expected around 3–5 × 10⁻³). |
| VER-08 | Throughput: reference mission rate sustained for ≥ 10 minutes in `COMPRESS` mode with zero line drops. `RAW` mode shows drops (`SYS-09`). |
| VER-09 | Timing closure on all clocks. `report_cdc` and `report_methodology` reviewed, every remaining warning waived with a reason. |
| VER-10 | Regressions run headless from scripts (Tcl / Python), with logged results. |

**Test data.** Public AVIRIS and ROSIS scenes (e.g. Indian Pines, 145 × 145 pixels; Salinas,
512 × 217 pixels; Pavia University, 610 × 340 pixels, 103 bands, cropped to `NX_MAX`), plus
synthetic images. These datasets are already calibrated, so raw sensor counts are emulated
by applying synthetic dark, gain and bad-pixel maps in reverse. The correction stage can then
be checked against the known original. Used for the compression results (2026-10-14,
docs/real_data.md): Indian Pines, and the GICI test sets of 49 AVIRIS and 13 Hyperion
scenes in place of Salinas and Pavia University, which could not be obtained.

---

## 12. Budgets

### 12.1 Resource estimate

Rows marked * are measured (out-of-context implementation at `NX_MAX` = 512,
`NZ_MAX` = 256); the others are estimates, replaced as blocks are built. The M4 design
(PS, DMA, interconnect, registers, front end, compressor) measures 21.3 k LUT, 20.4 k FF,
21 BRAM36, 40 URAM and 20 DSP in total; the M5 design (adding the downlink chain, error
injector, link layer with two GTH channels, ground receiver and ground DMA) measures
28.0 k LUT, 32.3 k FF, 32.5 BRAM36, 40 URAM and 20 DSP. At `NX_MAX` = 1024 (`SYS-04`,
build m7i_1024b, docs/nx_max_1024.md) the calibration table holds two entries per word,
and the full design measures 38.9 k LUT, 40.1 k FF, 46.5 BRAM36, 80 URAM (83 %) and
24 DSP, with 250 MHz met.

| Block | LUT | FF | DSP | BRAM36 | URAM |
|---|---|---|---|---|---|
| Sensor emulator * | 0.5 k | 0.8 k | 0 | 7.5 | 0 |
| Radiometric correction * | 0.3 k | 0.3 k | 1 | 0 | 32 |
| Corner turn * | 0.2 k | 0.3 k | 0 | 0 | 16 |
| CCSDS 123 compressor * | 13.6 k | 6.2 k | 19 | 5.5 | 8 |
| Packetizer, framer, RS, randomizer, ASM | 4 k | 5 k | 0 | 8 | 0 |
| Error injector | 1 k | 1 k | 0 | 0 | 0 |
| Link (GTH wrapper + link layer) | 2 k | 3 k | 0 | 2 | 0 |
| Ground receiver | 2 k | 3 k | 0 | 4 | 0 |
| DMA, interconnect, register blocks | 10 k | 15 k | 0 | 15 | 0 |
| Radiation mitigation (SEM IP, ECC, TMR) | 3 k | 4 k | 0 | 2 | 0 |
| **Total** | **≈ 37 k (16 %)** | **≈ 39 k (8 %)** | **≈ 20 (1 %)** | **≈ 44 (14 %)** | **56 (58 %)** |

### 12.2 Data-rate budget

| Quantity | Value |
|---|---|
| Raw data (12-bit) | 921.6 Mbit/s |
| Link payload after 8b/10b | 1,000 Mbit/s |
| CADU efficiency (884-byte packet zone / 1024-byte CADU) | 86.3 % |
| Space packet efficiency (2048 / 2062 bytes) | 99.3 % |
| **User data capacity** | **≈ 857 Mbit/s** |
| Raw data fits? | **No** (921.6 > 857), which is why `RAW` mode drops lines |
| Lossless compressed at 2:1 | 460.8 Mbit/s → 54 % of capacity |
| Lossless compressed at 3:1 | 307.2 Mbit/s → 36 % of capacity |

---

## 13. Repository layout

```
Hyperspectral-Payload-Processor/
├── SPEC.md            this document
├── rtl/
│   ├── common/        AXI-Stream skid buffers, XPM CDC wrappers, ECC, TMR voters
│   ├── frontend/      sensor_emulator, radiometric_corr, corner_turn
│   ├── ccsds123/      local sums/differences, predictor, weight update, mapper,
│   │                  sample-adaptive coder, bit packer, header generator
│   ├── downlink/      space_packetizer, aos_framer, rs_encoder, randomizer_asm, error_injector
│   ├── link/          GTH wrapper, link_tx, link_rx
│   ├── ground/        frame_sync (with the derandomizer), gr_capture
│   ├── regs/          register block generated from regs/ (PeakRDL)
│   └── system/        hpdp_core (registers, clock-domain crossings, data path), its
│                      block-design wrapper, hpdp_top (transceivers + block design)
├── regs/              SystemRDL register description and generator
├── tb/                block and system testbenches
├── model/             Python golden models and pytest tests
├── sw/
│   ├── baremetal/     A53 application
│   ├── host/          PC tools for board runs (JTAG loading, verification)
│   └── ground/        PC ground station
├── vivado/            project and block-design Tcl, constraints
├── data/              small test scenes (large datasets fetched by script, not committed)
└── docs/              design notes, test reports, diagrams
```

---

## 14. Milestones

| # | Milestone | Done when | Effort (part-time) |
|---|---|---|---|
| M0 | Repository, tool flow, CI. ZCU106 base design: PS + AXI DMA loopback | DMA loopback passes on the board | 2 wk |
| M1 | Python models: CCSDS 123 encoder + decoder, correction, packets, frames, RS, randomizer | Python encoder output decodes bit-exactly with an independent decoder | 3 wk |
| M2 | CCSDS 123 compressor RTL (lossless) | RTL matches the model on every test image and configuration; ≥ 200 MHz on the ZU7EV | 8 wk |
| M3 | Sensor emulator, radiometric correction, corner turn | All match their models | 3 wk |
| M4 | On-board run in `CAPTURE_CMP` mode, bare-metal application | Compressed output from the board decodes bit-exactly on the PC; throughput measured | 3 wk |
| M5 | Downlink chain and optical link: (a) IBERT eye scan and PRBS-31 over the fiber, (b) 8b/10b link layer with lock FSM, (c) packets → frames → RS → CADUs over the link, error injector, ground receiver | `VER-06` and `VER-07` pass | 6 wk |
| M6 | PC ground station (overlaps M5) | Live false-colour display, statistics and per-scene verification | 2 wk |
| M7 | Radiation mitigation | `RAD-01` to `RAD-07` done, fault-injection report written | 4 wk |

Total about 31 weeks at 15–20 hours per week.

---

## 15. Stretch goals (*may*)

- Near-lossless at full rate (`CMP-11`) and the hybrid entropy coder (`CMP-12`)
- Hardware RS decoder (`GR-06`) and bit-level frame sync (`GR-02`)
- Store-and-forward recording of compressed data in PL-side DDR4, downlinked later
- Several compressor lanes in parallel, for wider sensors
- Linux with proper drivers instead of bare-metal
- Onboard cloud screening (DPU), so cloudy scenes are never downlinked
- Real sensor input through FMC
- 10 Gbit/s link (10GBASE-SR modules; the OM3 fiber already supports it)
- Return channel for retransmission (needs a duplex cable, open item O-1)

---

## 16. Risks

| Risk | Mitigation |
|---|---|
| Misreading the CCSDS 123 standard | Python model first, validated against an independent decoder before any RTL (M1 before M2) |
| Getting the ESA/CNES reference tools takes time | Request access during M0. Until then rely on round trips between the project's own encoder and decoder. In the end an independent open-source model served instead (O-5) |
| Compressor misses 200 MHz | BIP gives Nz cycles of slack on per-band state; pipeline deeply; check timing per block as it is built |
| Near-lossless feedback loop limits speed | Ship lossless at full rate (v1.0); near-lossless may run slower (v1.1) |
| GTH / SFP bring-up problems | Start from the Wizard example design and IBERT; fall back to internal near-end loopback, which needs no fiber |
| Python decoder too slow for long runs | Short scenes for routine tests; C or numba decoder as a stretch item |
| Project grows too large | Fixed milestones, each with its own "done when" and usable on its own |

---

## 17. Out of scope

- Radiation-tolerant hardware and qualification
- RF modulation and the physical RF link (the optical link stands in for it)
- A real sensor in v1.0 (emulated from DDR)
- CCSDS 123 block-adaptive entropy coder and BSQ order
- Signed samples and D > 16 in v1.0
- Operating system in v1.0 (bare-metal)

---

## 18. Open items

| # | Item | Needed by |
|---|---|---|
| O-1 | ~~Is the OM3 cable duplex (2 fibers) or simplex (1 fiber)?~~ Closed (2026-10-13): duplex. Both directions carry the link: SFP1 -> SFP0 locks with 0 code errors and its eye scan is open (docs/lnk10/) | M5 |
| O-2 | ~~Which oscillator feeds the SFP quad, and must it be programmed over I2C at boot?~~ Closed: 156.25 MHz from the Si570 (through an SI53340) on MGTREFCLK1 of quad 226, no I2C setup; measured 156,250,064 Hz on this board. The Si5328 on MGTREFCLK1 of quad 225 would need I2C programming | M5 |
| O-3 | ~~How SFP `TX_DISABLE`, `LOS` and `TX_FAULT` are wired~~ Closed (UG1244 table 3-32): `TX_DISABLE_B` on PL pins AE22 / AF20 (LVCMOS12); `LOS` and `TX_FAULT` are not wired to the PL | M5 |
| O-4 | ~~Vivado licence covering XCZU7EV (ZCU106 kit voucher)~~ Closed: every XCZU7EV build since M0 (2026-10-10) ran with the installed licence | M0 |
| O-5 | ~~Access to ESA WhiteDwarf 2.0 / CNES CCSDS 123.0-B-2 tools~~ Closed (2026-10-15) without them: the NTNU SmallSat Lab's independent CCSDS 123.0-B-2 model, set up from our headers, produces byte-identical output in 947 of 949 runs (the RTL tests, the whole `VER-02` matrix, Indian Pines crops; `model/xcheck_ntnu.py`, docs/project_report.md §6). The other two found O-9 | M1 |
| O-6 | ~~`NZ_MIN` from the final compressor pipeline depth~~ Closed: `NZ_MIN` = 16 (docs/m2_compressor.md; tested in `c123_min_bands`) | M2 |
| O-7 | ~~Default scene length (256 lines) and packet size (2048 bytes), to revisit after compression experiments~~ Closed (2026-10-14, docs/real_data.md): on real AVIRIS and Hyperion scenes, 256-line scenes cost 0.3 % against whole images (16 lines: 2 – 4 %), so the default stands; the packet size does not affect compression and stays at 2048 bytes (99.3 % packet efficiency, §12.2) | M4 |
| O-8 | ~~SEM IP availability and licence for ZU7EV in Vivado 2025.2~~ Closed: `sem_ultra` 3.1 supports the XCZU7EV in Vivado 2025.2 with no licence key; running on the board since build m7f (docs/m7_plan.md) | M7 |
| O-9 | Full prediction mode at Nx = 1: the model and RTL accept it, the NTNU model refuses it and cites CCSDS 123.0-B-2 §4.3.1 (reduced mode required). To confirm against the standard; if so, reject it in the model and in `cfg_error`, and test `one_column` in reduced mode (it matches the NTNU model there) | M2 |

---

## 19. References

| Reference | Title |
|---|---|
| CCSDS 123.0-B-2 | Low-Complexity Lossless and Near-Lossless Multispectral and Hyperspectral Image Compression |
| CCSDS 133.0-B | Space Packet Protocol |
| CCSDS 732.0-B | AOS Space Data Link Protocol |
| CCSDS 131.0-B | TM Synchronization and Channel Coding |
| AMD UG1244 | ZCU106 Evaluation Board User Guide |
| AMD UG1085 | Zynq UltraScale+ Device Technical Reference Manual |
| AMD UG576 | UltraScale Architecture GTH Transceivers |
| AMD PG182 | UltraScale FPGAs Transceivers Wizard |
| AMD PG246 | In-System IBERT |
| AMD PG021 | AXI DMA |
| AMD PG187 | Soft Error Mitigation Controller (UltraScale) |
| AMD UG573 | UltraScale Architecture Memory Resources (BRAM, UltraRAM) |
| Sánchez et al., IEEE GRSL, 2022 | Removing the quantizer from the CCSDS 123.0-B-2 near-lossless feedback loop |
| ESA WhiteDwarf 2.0 | Cross-validated CCSDS implementations (includes a 123.0-B-2 decompressor) |
| Vorhaug, Boyle and Orlandić, WHISPERS 2024 | High-Level CCSDS 123.0-B-2 Hyperspectral Image Compressor Verification Model (NTNU SmallSat Lab; github.com/NTNU-SmallSat-Lab/ccsds123_issue_2_verification_model) |
| libfec (Phil Karn) | Reference implementation of CCSDS Reed-Solomon (`encode_rs_ccsds`) |

---

## 20. Glossary

| Term | Meaning |
|---|---|
| AOS | Advanced Orbiting Systems, a CCSDS data link protocol for downlink frames |
| APID | Application Process Identifier in a space packet |
| ASM | Attached Sync Marker (`1ACFFC1D`), marks the start of each CADU |
| BER | Bit error rate |
| BIL / BIP / BSQ | Band-interleaved by line / by pixel / band-sequential sample orders |
| CADU | Channel Access Data Unit: ASM + randomized RS codeblock |
| CC | Clock correction (removing or inserting idle words to absorb clock differences) |
| CCSDS | Consultative Committee for Space Data Systems, which publishes these standards |
| CDC | Clock-domain crossing |
| FHP | First Header Pointer in the AOS M_PDU header |
| GTH | AMD multi-gigabit transceiver type in UltraScale+ devices |
| M_PDU | Multiplexing protocol data unit: the packet zone of an AOS frame |
| PRBS | Pseudo-random bit sequence, used for link bit-error testing |
| Pushbroom | Imaging one ground line at a time as the satellite moves |
| RS | Reed-Solomon error-correcting code |
| SECDED | Single-error-correct, double-error-detect code |
| SEM IP | AMD Soft Error Mitigation IP (configuration memory scrubbing) |
| SEU | Single-event upset: a bit flipped by radiation |
| TMR | Triple modular redundancy with majority voting |
| VC / VCID | Virtual channel / virtual channel ID in AOS frames |
