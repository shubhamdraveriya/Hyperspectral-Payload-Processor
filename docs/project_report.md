# HPDP project report

Hyperspectral Payload Data Processor: an FPGA emulation of the onboard data chain of a
hyperspectral Earth-observation satellite, on the AMD ZCU106 (Zynq UltraScale+ XCZU7EV-2).

| | |
|---|---|
| Report date | 2026-10-13 |
| Final build | **m7h** (commit b506d30; documentation up to 4a0d164) |
| Specification | [SPEC.md](../SPEC.md) v1.0 |
| Milestone notes | [m0](m0_bringup.md), [m1](m1_models.md), [m2](m2_compressor.md), [m3](m3_frontend.md), [m4](m4_board_run.md), [m5](m5_downlink.md), [m6](m6_ground_station.md), [m7 plan](m7_plan.md), [m7 runbook](m7_board_runbook.md), [fault injection](m7_fault_injection.md) |

## 1. Summary

The HPDP takes raw pushbroom sensor data and runs the whole chain in the PL of the
XCZU7EV:

1. radiometric correction;
2. CCSDS 123.0-B-2 lossless compression;
3. CCSDS Space Packets and AOS frames;
4. Reed-Solomon (255,223) coding, randomization and sync markers;
5. a 1.25 Gbaud optical link over OM3 fiber;
6. a ground receiver whose output a PC ground station decodes back into the original
   image, bit for bit.

The PS runs a bare-metal application that configures and runs it.

Results on the board:

- **Throughput:** the reference mission rate (76.8 Msamples/s, 922 Mbit/s raw) for
  10 minutes with no dropped line (VER-08), on the final, radiation-mitigated build. The
  compressor alone runs at up to 250 Msamples/s.
- **Bit-exact end to end:** every scene reconstructed bit-exactly at the ground at
  injected BER 0, 10⁻⁴ and 10⁻³ (VER-07). The Reed-Solomon code holds to 2 × 10⁻³.
- **Link:** PRBS-31 over the fiber, 3.4 × 10¹² bits with 0 errors, so BER < 8.9 × 10⁻¹³
  at 95 % (VER-06). The eye scans are open in both directions (LNK-10).
- **Radiation mitigation:** SEM configuration scrubbing, ECC on every RAM, TMR on
  compressor state, configuration and control state, and pipeline and PS watchdogs.
  Configuration upsets were injected on the board and corrected within 153 ms. A
  1 300-trial simulated fault campaign found no silent corruption in protected state
  (RAD-01 … RAD-07).
- **Timing:** met on every clock; CDC and methodology clean with documented waivers
  (VER-09).

Status:

- 94 of 102 v1.0 *shall* / *should* requirements are met.
- **2 are not met:** SW-02 (load scenes from the SD card) and SW-04 (UART command
  console). The application takes jobs over JTAG instead.
- **6 are partly met or pending:** see §6. The largest are the independent CCSDS 123
  decoder check (needs ESA / CNES tools) and the Ethernet upload board test (needs the
  board's Ethernet connected).

## 2. System

```
PS DDR4 ─► AXI DMA ─► sensor emulator ─► radiometric ─► corner turn ─► scene guard ─► CCSDS 123
 (raw scenes,          (line timing,       correction     (BIL→BIP,      (short scenes    compressor
  calibration)          never stalls)      (dark, gain,    ping-pong       padded)        (1 sample/clk,
                                            bad pixels)    UltraRAM)                       250 MHz)
                                                                                              │
     ┌──── capture DMA ◄── fork ◄─────────────────────────────────────────────────────────────┘
     │                      │
     │                      └─► space packetizer ─► AOS framer ─► RS(255,223) I=4 ─► randomizer + ASM
     ▼                                                                                   │
  PS DDR4                       error injector ─► link layer (8b/10b) ─► GTH ══ OM3 fiber ══ GTH
                                                                                         │
  PC ground station ◄── TCP / file ◄── PS DDR4 ◄── ground DMA ◄── frame sync + derandomizer
  (RS decode, frames, packets,
   scenes, CCSDS 123 decoder,
   display, verification)
```

| Item | Value |
|---|---|
| Device / board | XCZU7EV-2 on the ZCU106; PS DDR4 SODIMM (x16, needs the DDR override of docs/m0) |
| Clocks | `clk_sys` 250 MHz (data path), `clk_sensor` 100 MHz (emulator), `clk_ctrl` 100 MHz (registers), `clk_link` 62.5 MHz (GTH user clock), `clk_fr` 31.25 MHz (free-running: GTH, SEM, debug hub) |
| Build-time maxima | `NX_MAX` 512, `NZ_MAX` 256, `NZ_MIN` 16, `D_MAX` 16 |
| Registers | SystemRDL (`regs/hpdp.rdl`) → PeakRDL AXI4-Lite block in three voted copies; C, Python and SV headers generated |
| Software | `hpdp_run` (A53 #0, standalone, Vitis 2025.2) runs job blocks loaded over JTAG (job / result format v7); host tools `run_m4.py`, `run_m5.py`; ground station `hpdp_gs.py` |
| Models | `model/hpdp_model/`: bit-exact Python models of every block, which generate the RTL test vectors |

## 3. Results

### 3.1 Throughput

| Measurement | Result |
|---|---|
| VER-08 (m5, m7g and m7h builds): reference mission scene 512 × 256 × 150, D 12, 1 line per ms, 2344 scenes | 600.1 s at **76.8 Msamples/s**, 0 dropped lines, 0 underruns or overruns, 37.3 M science frames, 0 code errors, 0 sync losses; identical frame counts on all three builds |
| M4 capture runs | 98.8–99.8 Msamples/s, the emulator's maximum |
| Compressor capacity | 1 sample per clock at 250 MHz (250 Msamples/s) |
| SYS-09: RAW mode at the reference rate | the raw data exceeds the link, as required: 99.1 % of lines dropped and counted, every compressed scene that reached the compressor delivered |
| Link | about 857 Mbit/s of packet capacity; compressed data at the mission rate needs about 440 Mbit/s |

### 3.2 Compression

All measured on **synthetic** scenes, bit-exact and decoded at the ground. SPEC's public
AVIRIS / ROSIS test data were not used (see §7).

| Scene | Bits / sample | Ratio |
|---|---|---|
| 128 × 64 × 64, D 12, spectral mixture with noise | 5.92 | 2.03:1 |
| 512 × 256 × 150, D 12 (mission size) | – | 2.10:1 |
| 512 × 16 × 256, D 16, P 15 (build maximum) | 10.9–11.1 | 1.45:1 |
| ramp pattern, D 10 | 1.20 | 8.30:1 |
| LFSR pattern, D 14 (incompressible) | 14.50 | 0.97:1 |

### 3.3 Link and channel coding

| Test | Result |
|---|---|
| VER-06: PRBS-31 over the fiber, 2700 s | 3.375 × 10¹² bits, 0 errors: BER < 8.9 × 10⁻¹³ (95 %) |
| VER-07: scenes over the fiber | 4 / 4 scenes bit-exact at injected BER 0, 10⁻⁴ (1 432 RS symbols corrected), 10⁻³ (14 508 corrected) |
| BER sweep | 2 × 10⁻³: all corrected; 3 × 10⁻³: 1 uncorrectable codeblock, which costs only its own scene (SYS-06); ≥ 4 × 10⁻³: scenes lost. Expected range 3–5 × 10⁻³ |
| LNK-10: eye scans (In-System IBERT, increments 2/4, dwell BER 10⁻⁶; [docs/lnk10](lnk10/)) | SFP0 → SFP1: horizontal 77.5 %, vertical 96.8 %; SFP1 → SFP0: 77.5 % / 90.5 %; PRBS-31 meanwhile 1.1 × 10¹² bits, 0 errors |
| Ground station | RS decoding cross-checked against `reedsolo` on 7 088 codewords; 3.4 ms per codeblock and 2.5 s per 128 × 64 × 64 scene in pure Python |

![SFP0 to SFP1 eye](lnk10/hpdp_eye_sfp0_to_sfp1_20261013_1450.svg)

### 3.4 Radiation mitigation (M7)

| Requirement | Mechanism | Board evidence |
|---|---|---|
| RAD-01 | `sem_ultra` in correction mode on `clk_fr`, ICAP handed over by `hpdp_run`, status and counters in the RAD block, injection from the PS | 251 configuration upsets: 198 corrected in 125–153 ms (one 154 ms scan of the configuration memory), 53 in masked or unused frames (undone), 0 uncorrectable; scenes bit-exact afterwards |
| RAD-02 | built-in ECC on all 56 UltraRAMs (calibration table, corner turn, line buffer), counters, injection | single-bit errors corrected (1 / 4 / 4 reads), double-bit errors detected |
| RAD-03 | weights in block RAM with ECC; coder and neighbourhood memories in voted LUT RAM; the register block in three voted copies | counters 0 in every run, including 10 minutes at the mission rate |
| RAD-04 | control state in three `DONT_TOUCH` copies, refreshed from the vote every cycle; extended in m7h to the steering state of the corner turn, packetizer and AOS framer | as above |
| RAD-05 | PL watchdogs (compressor, downlink, ground capture), PS watchdog per job | the PS watchdog reset the board when the app hung on purpose |
| RAD-06 | uncorrectable errors flag the scene's packet headers and raise an interrupt | flagged scenes seen by the ground station |
| RAD-07 | fault-injection campaign | simulation: 820 trials in protected state, **0 silent**; 22.5 % silent in the remaining unprotected datapath registers, accepted as residual risk; hardware: above |

The campaign drove a design change. In m7g, upsets in the corner turn's, packetizer's and
framer's address and count registers were silent 93–100 % of the time. Build m7h
triplicates them (+0.7 k LUT); after that, all 220 simulated upsets in them were corrected.

### 3.5 Implementation (m7h)

| | Result |
|---|---|
| Utilization | 38.2 k LUT (16.6 %), 39.2 k FF (8.5 %), 38.5 BRAM36 (12.3 %), 56 URAM (58.3 %), 24 DSP (1.4 %), 2 GTHE4 |
| Timing (VER-09) | met on all 11 clocks: setup WNS +0.087 ns (`clk_sys` 250 MHz), hold WHS +0.010 ns; 36 `set_bus_skew` constraints met (worst slack +2.94 ns); `check_timing` clean apart from the two static SFP `TX_DISABLE_B` outputs (false path) |
| `report_cdc` | 0 unsafe, 0 unknown crossings. Waived: CDC-11 in the GTH wizard (2 endpoints, one monotonic "active" flag into two reset synchronizers; `vivado/constraints/waivers.xdc`). The remaining Warning rows are safe (the wizard's CPLL-calibration false paths; `clk_sensor` → `clk_pl_1` with max-delay constraints) |
| `report_methodology` | 5 × LUTAR-1 waived (GTH wizard reset structure, as AMD's example design); 2 advisories on the `clk_sensor` MMCM (CLKC-40 / CLKC-56, could be a PLL; no LOC) |

## 4. Verification

| Level | What |
|---|---|
| Python models | 144 pytest tests: CCSDS 123 encoder / decoder round trips over 20 configurations and D ∈ {2 … 16}, RS against `reedsolo`, framing, frame sync, end-to-end chain |
| RTL regression | 70 self-checking xsim tests from model vectors with random stalls, run headless by `tb/run_sim.tcl` (VER-01, VER-10): 28 compressor configurations; front end; downlink and ground blocks; link layer; system tests through the registers (VER-05); ECC, TMR, watchdog and SEM-path tests; the fault campaign (3 trials per class per regression) |
| Fault campaign | `tb_fault`, 1 300 trials on m7h (`docs/m7_fault_injection.md`) |
| Ground station | `pytest sw/ground` (decoder, reports) and three board captures decoded |
| Board | M0 DMA loopback (3 865 MB/s); M4 15 scenes; M5 VER-06 / 07 / 08 and SYS-09; M7 runbook (`docs/m7_board_runbook.md`, results section); all JTAG-only, flash untouched |

## 5. Requirements compliance

Status of every v1.0 requirement in SPEC. *may* items and the v1.1 item are listed
separately.

| Area | Met | Not fully met |
|---|---|---|
| System (SYS-01 … 10) | 01, 02, 03, 05, 06, 07, 08, 09, 10 | **SYS-04**: the `NX_MAX` = 1024 scaling was never built |
| Sensor emulator (SE-01 … 06) | all | – |
| Radiometric correction (RC-01 … 05) | all | – |
| Corner turn (CT-01 … 04) | all | – |
| Compressor (CMP-01, 03 … 09) | all | **CMP-02**: no independent CCSDS 123 decoder yet (O-5) |
| Packetizer, AOS framer (PKT, AOS) | all | – |
| Channel coding (CC-01 … 06) | all; CC-06 cross-checked with `reedsolo` instead of libfec | – |
| Error injector (EI-01 … 04) | all | – |
| Link (LNK-01 … 10) | all, including LNK-10 (*should*) | – |
| Ground receiver (GR-01, 03 … 05; GR-02 byte-aligned) | all | – |
| Data movement (DM-01 … 03) | all | – |
| PS software (SW-01 … 06) | 01, 03, 06 | **SW-02** not met: scenes and tables come over JTAG, not from the SD card. **SW-04** not met: no UART command console; the app runs job blocks loaded by the host scripts over JTAG. **SW-05**: TCP upload written and built, board test pending (Ethernet not connected) |
| Ground station (GS-01 … 06) | all; GS-02 cross-checked with `reedsolo` instead of libfec | – |
| Models (MDL-01 … 04) | 01, 03, 04 | **MDL-02**: cross-validation against an independent decoder (O-5) |
| Radiation (RAD-01 … 07) | all | – |
| Verification (VER-01 … 10) | 01, 04 (with `reedsolo`), 05 … 10 | **VER-02**: 28 chosen configurations, not a random subset per regression plus a nightly full matrix. **VER-03**: all-zero and all-maximum images not in the RTL tests (random, Nx = 1, `NZ_MIN`, `NX_MAX` and `NZ_MAX` are, the last two on the board) |

Other items:

- *may*, not done: RC-06 (double-buffered tables), CMP-11 (near-lossless at full rate),
  CMP-12 (hybrid entropy coder), GR-06 (hardware RS decoder), bit-level frame sync
  (GR-02), the C / numba decoder (GS-04).
- v1.1, not done: CMP-10 (near-lossless).

## 6. Deviations and decisions

| Item | What and why |
|---|---|
| Job interface instead of SD card and UART console (SW-02, SW-04) | Since M4 the host builds each job (scenes, tables, settings) and loads it over JTAG together with the bitstream and the app; the app prints a summary and returns a result block that the host checks bit for bit. This made every board run reproducible and self-checking, but leaves SW-02 and SW-04 open. It was not recorded as a decision at the time; it is recorded here |
| `reedsolo` instead of libfec (CC-06, GS-02, VER-04) | An independent RS implementation in Python, with the same field and roots, available without building libfec on Windows |
| Calibration table layout A (M7) | One entry per 64-bit word (the user's choice) |
| SYS-09 RAW overload: option (a) | RAW mode shows that raw data exceeds the link; whole scenes are not dropped on purpose (docs/m7_plan.md decision 1) |
| Scene guard | A scene cut short is padded and ended, so one lost line cannot merge scenes (found in M5) |
| PS watchdog per job | Armed by a job flag, so debugging that halts the processor is not reset |
| TMR on the steering state (m7h) | Found by the fault campaign (above) |
| Residual risk | Upsets in datapath registers are silent about one time in four in simulation; accepted (docs/m7_fault_injection.md) |
| Project folder self-contained | The Vitis workspace (`vitis/`), board runs (`build/runs/`) and the venv (`.venv/`) are inside the repository; tools that fail on the space in the path reach them through directory junctions (docs/m0, issue 3) |

## 7. Open items

| Item | Needs | Effort |
|---|---|---|
| SW-05 board test (Ethernet upload) | the board's Ethernet connected | about an hour |
| SW-02 SD card loading, SW-04 UART console | implementation in `hpdp_run` (FatFs from the BSP, a command parser), then a board test with an SD card | 1–2 days |
| CMP-02 / MDL-02 / O-5: independent CCSDS 123 decoder | access to ESA WhiteDwarf 2.0 or the CNES tools | half a day once available |
| VER-02 matrix, VER-03 all-zero / all-maximum images | more test vectors and a nightly run; no board | a few hours |
| SYS-04: `NX_MAX` = 1024 | one build with the parameter changed, timing check | a few hours |
| Real hyperspectral data (SPEC test data, O-7) | the public AVIRIS / ROSIS scenes; compression ratios so far are on synthetic data | half a day |
| SPEC open items O-4 (licence, in use), O-6 (`NZ_MIN` = 16, settled in M2) | mark as closed | minutes |

Stretch goals (SPEC §15) are untouched. The return channel is now possible: the cable
is duplex (O-1).

## 8. Reproducing

```
python -m pytest model                                   # Python models
vivado -mode batch -source tb/run_sim.tcl                # RTL regression (70 tests)
vivado -mode batch -source vivado/build.tcl              # bitstream + XSA (HPDP_BUILD_DIR, default build/)
vitis -s sw/baremetal/build.py build/m7h/hpdp.xsa        # app, workspace vitis/
python sw/host/run_m5.py --work <name>                   # a board run, JTAG only (build/runs/<name>)
python sw/ground/hpdp_gs.py decode build/runs/<name>/gr.bin --ref build/runs/<name> --show
```

The board procedure for each milestone is in its notes; M7's is in
[docs/m7_board_runbook.md](m7_board_runbook.md).
