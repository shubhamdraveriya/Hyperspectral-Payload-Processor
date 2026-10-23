"""HPDP Results Report: the measured results of the project, the requirement
compliance, deviations and open items. Numbers from docs/project_report.md and
the milestone notes."""

from hpdp_pdf import CELL, HpdpDoc, PageBreak, Spacer, bullets, code, figure, h1, h2, p, table

TITLE = "HPDP Results Report"
DOC_ID = "HPDP-RPT-001"
REV = "B"
DATE = "2026-10-23"
PASS = "<font color='#006300'><b>PASS</b></font>"
OPEN = "<font color='#52514e'><b>OPEN</b></font>"


def story(doc):
    s = doc.cover_flowables([
        ("Document", f"{DOC_ID}, revision {REV}"),
        ("Date", DATE),
        ("Prepared by", "Shubham Draveriya"),
        ("Builds tested", "m7h (commit b506d30, NX_MAX 512) and m7i_1024b (commit d2eda42, "
                          "NX_MAX 1024)"),
        ("Hardware", "AMD ZCU106, XCZU7EV-2; two 1000BASE-SX SFP modules; 1 m duplex OM3 fiber"),
        ("Specification", "SPEC.md v1.0 (102 shall / should requirements)"),
        ("Result", "100 of 102 requirements met; 2 pending an external tool"),
    ], extra=[p("This report gives the measured results of the Hyperspectral Payload Data "
                "Processor: throughput, compression, the optical link, end-to-end integrity, "
                "radiation mitigation, implementation and verification coverage, followed by the "
                "status of every requirement.")])

    # 1 ------------------------------------------------------------------------
    s += [h1("1  Summary"),
          table(["Area", "Key measurement", "Verdict"], [
              ["Throughput (VER-08)", "76.8 Msamples/s for 600 s, 2344 scenes, 0 dropped lines", PASS],
              ["End-to-end integrity (VER-07)", "4 of 4 scenes bit-exact at injected BER 0, "
                                                "10<super>-4</super> and 10<super>-3</super>", PASS],
              ["Optical link (VER-06)", "3.375 × 10<super>12</super> bits PRBS-31, 0 errors; "
                                        "BER &lt; 8.9 × 10<super>-13</super> at 95 %", PASS],
              ["Eye scan (LNK-10)", "Open eye both directions: 77.5 % horizontal, 90.5 to 96.8 % "
                                    "vertical", PASS],
              ["RAW overload (SYS-09)", "Raw data exceeds the link: 99.1 % of lines dropped and "
                                        "counted, all compressed scenes delivered", PASS],
              ["Compression", "63 of 63 real scenes lossless; 4.4 to 6.0 bits/sample mean per "
                              "dataset", PASS],
              ["Radiation (RAD-01 to 07)", "251 configuration upsets on the board, all handled; 0 "
                                           "silent in 820 protected simulated trials", PASS],
              ["Timing (VER-09)", "All 11 clocks met; WNS +0.087 ns at 250 MHz; CDC clean", PASS],
              ["Compressor matrix (VER-02)", "864 of 864 configurations bit-exact", PASS],
              ["Ethernet upload (SW-05)", "1 814 528 bytes over TCP identical to the JTAG copy in "
                                          "4 runs; 4 of 4 scenes bit-exact", PASS],
              ["Independent decoder (CMP-02, MDL-02)", "Needs ESA or CNES tool access (O-5)", OPEN]],
              [1.9, 3.6, 0.8]),
          Spacer(1, 6),
          h2("1.1  Test set-up"),
          table(["Item", "Configuration"], [
              ["Board", "AMD ZCU106 rev. with an x16 DDR4 SODIMM (needs the DDR override of "
                        "docs/m0)"],
              ["Optics", "SFP0 → 1 m OM3 → SFP1, 1000BASE-SX modules, both ends on the "
                         "same 156.25 MHz reference (Si570, MGTREFCLK1 of bank 226)"],
              ["Clocks", "clk_sys 250 MHz, clk_sensor and clk_ctrl 100 MHz, clk_link 62.5 MHz, "
                         "clk_fr 31.25 MHz"],
              ["Software", "hpdp_run (bare metal, Arm A53, job/result format v7); host scripts "
                           "run_m4.py and run_m5.py; ground station hpdp_gs.py"],
              ["Loading", "JTAG only; the QSPI flash was never written"],
              ["Tools", "Vivado / Vitis 2025.2, Python 3 with pytest"]],
              [1.2, 5])]

    # 2 ------------------------------------------------------------------------
    s += [PageBreak(), h1("2  Throughput"),
          table(["Measurement", "Result"], [
              ["VER-08: mission scene 512 × 256 × 150, D = 12, one line per ms, 2344 "
               "scenes (builds m5, m7g and m7h)",
               "600.1 s at <b>76.8 Msamples/s</b>; 0 dropped lines, 0 underruns or overruns; 37.3 "
               "million science frames; 0 code errors; 0 sync losses; identical frame counts on "
               "all three builds"],
              ["M4 capture runs (compressor output straight to DDR)",
               "98.8 to 99.8 Msamples/s, the maximum of the sensor emulator"],
              ["Compressor capacity", "One sample per clock at 250 MHz (250 Msamples/s)"],
              ["SYS-09: RAW mode (no compression) at the mission rate",
               "Raw data exceeds the link as required: 99.1 % of lines dropped and counted; every "
               "compressed scene that reached the compressor was delivered"],
              ["Link capacity", "About 857 Mbit/s for packets; compressed data at the mission rate "
                                "needs about 440 Mbit/s"]],
              [2.4, 4]),
          Spacer(1, 6),
          figure("link_budget.png", "Figure 1: Data rate at the mission rate against the link "
                                    "capacity. Only compressed data fits; the worst real scene "
                                    "uses 71 % of the link.")]

    # 3 ------------------------------------------------------------------------
    s += [h1("3  Compression"),
          h2("3.1  Real hyperspectral data"),
          p("63 public scenes went through the compressor model, which is bit-exact with the RTL "
            "over the full VER-02 matrix. Every scene was decoded and compared: 63 of 63 lossless. "
            "Five of the scenes also went through the board and were rebuilt bit-exactly at the "
            "ground over the fiber."),
          figure("compression.png", "Figure 2: Bits per sample after lossless compression, default "
                                    "settings (P = 3, full prediction, neighbour-oriented wide "
                                    "local sums)."),
          table(["Dataset", "Scenes", "Bits/sample, mean (range)", "Ratio", "Link use, mean (worst)"], [
              ["AVIRIS, airborne, 512 × 512 × 224, 2008 to 2017", "49", "4.43 (2.99 to 7.93)",
               "3.12:1", "40 % (71 %)"],
              ["Hyperion, EO-1 satellite, 256 × 1024 × 242, 2017", "13", "4.95 (4.74 to 5.16)",
               "2.86:1", "44 % (46 %)"],
              ["Indian Pines, AVIRIS, 145 × 145 × 220, 1992", "1", "6.01", "2.33:1", "54 %"]],
              [3.2, 0.8, 1.8, 0.9, 1.6]),
          Spacer(1, 4),
          bullets(["CCSDS 123 needs 1.6 to 2.0 times fewer bits than deflate on the same samples.",
                   "AVIRIS falls into two groups: 42 scenes at 3.91 bits/sample on average, and the "
                   "7 scenes of three flights at 7.53.",
                   "The default settings are within 1 % of the best setting tried on each dataset; "
                   "without spectral prediction (P = 0) scenes need 12 to 67 % more bits.",
                   "Scene length: 256-line scenes cost 0.3 % against whole images, 16-line scenes 2 "
                   "to 4 %. The default of 256 lines stands (open item O-7 closed)."]),
          h2("3.2  Synthetic scenes (bit-exact and decoded at the ground)"),
          table(["Scene", "Bits/sample", "Ratio"], [
              ["128 × 64 × 64, D = 12, spectral mixture with noise", "5.92", "2.03:1"],
              ["512 × 256 × 150, D = 12 (mission size)", "–", "2.10:1"],
              ["512 × 16 × 256, D = 16, P = 15 (build maximum)", "10.9 to 11.1", "1.45:1"],
              ["Ramp pattern, D = 10", "1.20", "8.30:1"],
              ["LFSR pattern, D = 14 (incompressible)", "14.50", "0.97:1"]],
              [4, 1.3, 1])]

    # 4 ------------------------------------------------------------------------
    s += [PageBreak(), h1("4  Optical link and channel coding"),
          h2("4.1  Bit error rate of the link"),
          table(["Test", "Result"], [
              ["VER-06: PRBS-31 over the fiber, 2700 s",
               "3.375 × 10<super>12</super> bits, 0 errors: BER &lt; 8.9 × "
               "10<super>-13</super> at 95 % confidence; checker locked throughout; 0 clock "
               "corrections, 0 elastic-buffer errors (common reference clock)"],
              ["LNK-10: eye scans (In-System IBERT, increments 2/4, dwell BER 10<super>-6</super>)",
               "SFP0 → SFP1: horizontal 77.5 %, vertical 96.8 %. SFP1 → SFP0: 77.5 % / "
               "90.5 %. PRBS-31 during the scans: 1.1 × 10<super>12</super> bits, 0 errors"],
              ["Link start-up", "The link locked at the first attempt in every run"]],
              [2.4, 4]),
          Spacer(1, 6),
          figure("eye_scans.png", "Figure 3: Statistical eye scans of both fiber directions. "
                                  "Light: BER at or below the 10<sup>-6</sup> dwell limit; dark: "
                                  "high BER at the bit edges.", width=400),
          h2("4.2  End-to-end integrity with injected errors (VER-07)"),
          table(["Injected BER", "Records", "Bits injected", "RS symbols corrected", "Uncorrectable",
                 "Scenes bit-exact"], [
              ["0", "1772", "0", "0", "0", "4 of 4"],
              ["10<super>-4</super>", "–", "2187", "1432", "0", "4 of 4"],
              ["10<super>-3</super>", "–", "22 200", "14 508", "0", "4 of 4"]],
              [1.1, 0.9, 1.1, 1.5, 1.2, 1.2]),
          Spacer(1, 4),
          p("At BER 10<super>-3</super> the frame synchronizer stayed in LOCK throughout (60 sync-"
            "marker bit errors tolerated)."),
          h2("4.3  Bit error rate sweep"),
          table(["Injected BER", "Outcome"], [
              ["2 × 10<super>-3</super>", "All errors corrected; scenes bit-exact"],
              ["3 × 10<super>-3</super>", "One uncorrectable codeblock; it costs only its own "
                                               "scene (SYS-06)"],
              ["4 × 10<super>-3</super> and above", "Scenes lost, as expected for RS(255,223) "
                                                         "with I = 4 (expected limit 3 to 5 "
                                                         "× 10<super>-3</super>)"]],
              [1.6, 4.6]),
          Spacer(1, 4),
          p("The ground station's Reed-Solomon decoder was cross-checked against the independent "
            "<i>reedsolo</i> library on 7088 codewords. In pure Python it decodes a codeblock in "
            "3.4 ms and a 128 × 64 × 64 scene in 2.5 s.")]

    # 5 ------------------------------------------------------------------------
    s += [PageBreak(), h1("5  Radiation mitigation"),
          table(["Req.", "Mechanism", "Evidence on the board"], [
              ["RAD-01", "SEM controller (sem_ultra) in correction mode, configuration scrubbing",
               "251 configuration upsets injected: 198 corrected in 125 to 153 ms (one 154 ms scan "
               "of the configuration memory), 53 in masked or unused frames, 0 uncorrectable; "
               "scenes bit-exact afterwards"],
              ["RAD-02", "Built-in ECC on all 56 UltraRAMs (table, corner turn, line buffer), "
                         "counters, injection", "Single-bit errors corrected (1 / 4 / 4 reads); "
                                                "double-bit errors detected"],
              ["RAD-03", "Weights in block RAM with ECC; voted LUT RAM; register block in three "
                         "voted copies", "Counters 0 in every run, including 10 min at the "
                                         "mission rate"],
              ["RAD-04", "Control state in three copies, refreshed from the vote every cycle; "
                         "extended in m7h to the corner turn, packetizer and framer",
               "As RAD-03"],
              ["RAD-05", "PL pipeline watchdogs; PS watchdog per job", "The PS watchdog reset the "
                                                                       "board when the software "
                                                                       "hung on purpose"],
              ["RAD-06", "Uncorrectable errors flag the scene's packet headers and raise an "
                         "interrupt", "Flagged scenes seen by the ground station"],
              ["RAD-07", "Fault-injection campaign", "See section 5.1"]],
              [0.8, 2.6, 3.0]),
          h2("5.1  Fault-injection campaign (simulation, 1300 trials on m7h)"),
          table(["Class", "Trials", "Corrected", "Detected", "Masked", "Silent"], [
              ["Protected state (ECC, TMR)", "820", "567", "238", "15", "<b>0</b>"],
              ["Unprotected datapath registers", "480", "–", "–", "–", "108 (22.5 %)"]],
              [2.6, 0.8, 0.9, 0.9, 0.8, 1.2]),
          Spacer(1, 4),
          p("The campaign drove a design change: in build m7g, upsets in the address and count "
            "registers of the corner turn, packetizer and AOS framer were silent 93 to 100 % of the "
            "time. Build m7h triplicates them (+0.7 k LUT); afterwards all 220 simulated upsets in "
            "them were corrected. The remaining silent rate in datapath registers is accepted as "
            "residual risk.")]

    # 6 ------------------------------------------------------------------------
    s += [h1("6  Implementation"),
          figure("utilization.png", "Figure 4: Device utilization of the two final builds. The "
                                    "1024-pixel build is limited by UltraRAM."),
          table(["Item", "m7h (NX_MAX 512)", "m7i_1024b (NX_MAX 1024)"], [
              ["LUT / FF", "38.2 k (16.6 %) / 39.2 k (8.5 %)", "38.9 k / 40.1 k"],
              ["BRAM36 / URAM / DSP", "38.5 / 56 / 24", "46.5 / 80 / 24"],
              ["Setup WNS / hold WHS", "+0.087 ns / +0.010 ns (all 11 clocks)", "+0.012 ns / "
                                                                                "+0.010 ns"],
              ["Bus skew", "36 constraints met, worst slack +2.94 ns", "worst +2.85 ns"],
              ["report_cdc", "0 unsafe, 0 unknown; one documented waiver (CDC-11 inside the GTH "
                             "wizard)", "identical"],
              ["report_methodology", "5 LUTAR-1 waived (GTH wizard reset structure, as AMD's "
                                     "example); 2 MMCM advisories", "identical"]],
              [1.6, 2.6, 2.0])]

    # 7 ------------------------------------------------------------------------
    s += [h1("7  Verification coverage"),
          table(["Level", "Coverage", "Result"], [
              ["Python models", "Encoder/decoder round trips over 20 configurations and D = 2 to "
                                "16, RS against reedsolo, framing, frame sync, end-to-end chain",
               "150 tests pass"],
              ["RTL regression", "87 self-checking xsim tests from model vectors with random "
                                 "stalls: compressor configurations, corner-case images (all zero, "
                                 "all maximum), front end, downlink and ground blocks, link layer, "
                                 "system tests through the registers, ECC / TMR / watchdog / SEM "
                                 "paths", "87 of 87 pass, 9 minutes"],
              ["Configuration matrix (VER-02)", "All 864 compressor configurations (local sum 4 "
                                                "× mode 2 × P 4 × D 3 × "
                                                "Ω 3 × U<sub>max</sub> 3)",
               "864 of 864 bit-exact, 57 minutes"],
              ["Fault campaign", "tb_fault, 1300 trials", "0 silent in protected state"],
              ["Board", "M0 loopback; M4 15 scenes; M5 VER-06/07/08 and SYS-09; M7 runbook; real "
                        "scenes, 1024 build, SD card and console, Ethernet upload", "All pass, JTAG only"]],
              [1.5, 3.6, 1.4])]

    # 8 ------------------------------------------------------------------------
    s += [h1("8  Requirements compliance"),
          table(["Area", "Met", "Not fully met"], [
              ["System (SYS-01 to 10)", "All; SYS-04 by build m7i_1024b", "–"],
              ["Sensor emulator (SE-01 to 06)", "All", "–"],
              ["Radiometric correction (RC-01 to 05)", "All", "–"],
              ["Corner turn (CT-01 to 04)", "All", "–"],
              ["Compressor (CMP-01, 03 to 09)", "All", "CMP-02: independent decoder (O-5)"],
              ["Packetizer, AOS framer", "All", "–"],
              ["Channel coding (CC-01 to 06)", "All; CC-06 with reedsolo instead of libfec", "–"],
              ["Error injector (EI-01 to 04)", "All", "–"],
              ["Link (LNK-01 to 10)", "All, including LNK-10 (should)", "–"],
              ["Ground receiver (GR-01, 03 to 05)", "All (GR-02 byte-aligned)", "–"],
              ["Data movement (DM-01 to 03)", "All", "–"],
              ["PS software (SW-01 to 06)", "All", "–"],
              ["Ground station (GS-01 to 06)", "All; GS-02 with reedsolo", "–"],
              ["Models (MDL-01 to 04)", "01, 03, 04", "MDL-02: independent decoder (O-5)"],
              ["Radiation (RAD-01 to 07)", "All", "–"],
              ["Verification (VER-01 to 10)", "All; VER-04 with reedsolo", "–"]],
              [2.4, 2.4, 2.2], zebra=True),
          Spacer(1, 4),
          p("Not done (<i>may</i> items): double-buffered tables (RC-06), near-lossless at full "
            "rate (CMP-11), hybrid entropy coder (CMP-12), hardware RS decoder (GR-06), bit-level "
            "frame sync (GR-02), C / numba decoder (GS-04). Version 1.1 item not done: near-"
            "lossless compression (CMP-10).", CELL)]

    # 9 ------------------------------------------------------------------------
    s += [PageBreak(), h1("9  Deviations and decisions"),
          table(["Item", "What and why"], [
              ["Job interface first, console later (SW-02, SW-04)",
               "Board runs are driven by job blocks loaded over JTAG with the bitstream and the "
               "software; every run returns a result block that the host checks bit for bit. The "
               "SD card loading and UART console came after M7 and post the same job block."],
              ["reedsolo instead of libfec (CC-06, GS-02, VER-04)",
               "An independent RS implementation in Python with the same field and roots, "
               "available without building libfec on Windows."],
              ["Calibration table layouts (M7, SYS-04)",
               "Layout A, one entry per 64-bit word up to 512 × 256; layout B above it, so "
               "NX_MAX 1024 fits in 80 of 96 UltraRAMs."],
              ["Test data (SPEC §11)", "Indian Pines and the GICI sets of 49 AVIRIS and 13 "
                                             "Hyperion scenes replace Salinas and Pavia "
                                             "University, whose source was unreachable."],
              ["SYS-09 RAW overload", "RAW mode shows that raw data exceeds the link; whole scenes "
                                      "are not dropped on purpose."],
              ["Scene guard", "A scene cut short is padded and ended, so one lost line cannot "
                              "merge scenes (found on the board in M5)."],
              ["TMR on the steering state (m7h)", "Added after the fault campaign."],
              ["Residual risk", "Upsets in unprotected datapath registers are silent about one "
                                "time in four in simulation; accepted."]],
              [1.8, 4.4]),
          h1("10  Open items"),
          table(["Item", "Needs", "Effort"], [
              ["CMP-02 / MDL-02 / O-5: independent CCSDS 123 decoder",
               "An account for ESA WhiteDwarf 2.0 (ESSR) or the CNES tools; then decode the RTL "
               "test vectors and the real-data scenes", "Half a day once available"]],
              [2.2, 3.2, 1.2]),
          Spacer(1, 4),
          p("The stretch goals of SPEC §15 are untouched. A return channel is now possible "
            "because the fiber is duplex (O-1 closed)."),
          h1("11  Conclusion"),
          p("The HPDP meets its purpose: it carries hyperspectral scenes from a raw sensor stream "
            "to a bit-exact image at the ground, at the full mission rate, through a real optical "
            "link, with error correction that holds to a bit error rate of 2 × "
            "10<super>-3</super> and radiation mitigation that left no silent corruption in "
            "protected logic. 100 of 102 requirements are met and none is unmet; the remaining "
            "two need an external decoder."),
          h2("Appendix A: Reproducing the results"),
          code(["python -m pytest model                                  # Python models",
                "vivado -mode batch -source tb/run_sim.tcl               # RTL regression (87 tests)",
                "vivado -mode batch -source vivado/build.tcl             # bitstream + XSA",
                "vitis -s sw/baremetal/build.py build/m7h/hpdp.xsa       # software",
                "python sw/host/run_m5.py --work <name>                  # board run, JTAG only",
                "python sw/ground/hpdp_gs.py decode build/runs/<name>/gr.bin --ref build/runs/<name> --show"])]
    return s


def build(path):
    doc = HpdpDoc(path, TITLE, DOC_ID, REV, DATE,
                  subtitle="Measured results, requirement compliance and open items of the "
                           "Hyperspectral Payload Data Processor", author="Shubham Draveriya",
                  toc=True)
    doc.make(story(doc))
