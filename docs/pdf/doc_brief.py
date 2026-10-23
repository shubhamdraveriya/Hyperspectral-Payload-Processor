"""HPDP Project Brief: a three-page overview for readers who want the essentials."""

from hpdp_pdf import (HpdpDoc, Spacer, bullets, chain_diagram, drawing_figure, figure, h1, h2, p,
                      table)

TITLE = "HPDP Project Brief"
DOC_ID = "HPDP-BRF-001"
REV = "B"
DATE = "2026-10-23"


def story(doc):
    s = doc.cover_flowables([
        ("Document", f"{DOC_ID}, revision {REV}"),
        ("Date", DATE),
        ("Prepared by", "Shubham Draveriya"),
        ("Hardware", "AMD ZCU106 evaluation board, Zynq UltraScale+ XCZU7EV-2"),
        ("Standards", "CCSDS 123.0-B-2 (compression), 133.0-B (space packets), 732.0-B (AOS "
                      "frames), 131.0-B (channel coding)"),
        ("Duration", "10 to 15 October 2026, eight milestones, 51 commits; Ethernet upload tested "
                     "on the board 23 October"),
        ("Status", "All milestones (M0 to M7) complete; all 102 requirements met"),
    ], extra=[table(["At a glance", ""], [
        ["What", "The complete onboard data chain of a hyperspectral imaging satellite, running "
                 "in FPGA logic, with a fiber link to a ground receiver"],
        ["Throughput", "76.8 Msamples/s (the mission rate) for 10 minutes, no line lost"],
        ["Integrity", "Every scene rebuilt bit for bit at the ground, at bit error rates up to "
                      "10<super>-3</super>"],
        ["Link", "3.4 × 10<super>12</super> bits over the fiber with zero errors"],
        ["Radiation", "Configuration upsets corrected in 153 ms or less; no silent error in "
                      "protected logic"]],
        [1, 4]),
        Spacer(1, 8 * 2.8346),
        table(["Further documents", ""], [
            ["HPDP-PRC-001", "Process Description: how the project was done, step by step, with "
                             "the procedures to rebuild and run it (Simplified Technical English)"],
            ["HPDP-RPT-001", "Results Report: all test results, charts and the requirement "
                             "compliance"],
            ["HPDP-GDE-001", "Learning Guide: the HPDP taught from the basics to the full design, "
                             "with check questions (Simplified Technical English)"],
            ["SPEC.md", "The specification (v1.0)"],
            ["docs/", "project_report.md and the engineering notes of each milestone (m0 to m7)"]],
            [1, 4])])

    s += [h1("1  What the HPDP is"),
          p("A hyperspectral satellite photographs the ground in hundreds of narrow colour bands. "
            "Its camera produces far more data than its radio downlink can carry, the downlink "
            "corrupts bits, and radiation in orbit flips bits inside the electronics. The "
            "<b>Hyperspectral Payload Data Processor (HPDP)</b> is a working model of the hardware "
            "that solves these problems on board, built on a single AMD ZCU106 FPGA board."),
          p("Raw sensor data enters the FPGA, is cleaned up (radiometric correction), compressed "
            "without loss (CCSDS 123.0-B-2), packed into space packets and transfer frames, "
            "protected with Reed-Solomon coding, and sent at 1.25 Gbaud over an optical fiber. A "
            "ground receiver on the same board recovers the frames, and a PC ground station "
            "rebuilds the original image and checks it bit for bit."),
          drawing_figure(chain_diagram(), "Figure 1: The HPDP data chain. Everything up to the "
                                          "frame synchronizer runs in the FPGA logic; the Arm "
                                          "processor on the same chip runs the control software."),
          h2("Why compression is essential"),
          p("At the reference mission rate of 76.8 million 12-bit samples per second, the raw data "
            "(922 Mbit/s) does not fit the link's packet capacity (857 Mbit/s). Lossless "
            "compression brings it down to roughly half, with room to spare even for the hardest "
            "real scene tested."),
          figure("link_budget.png", "Figure 2: Data rate at the mission rate against the link "
                                    "capacity. The raw data does not fit; compressed data does.")]

    s += [h1("2  Key results"),
          table(["Area", "Result", "Requirement"], [
              ["Throughput", "76.8 Msamples/s (922 Mbit/s raw) for 10 minutes with no dropped "
                             "line; the compressor alone runs at 250 Msamples/s", "VER-08"],
              ["End-to-end integrity", "Every scene rebuilt bit-exactly at the ground with "
                                       "injected bit error rates of 0, 10<super>-4</super> and "
                                       "10<super>-3</super>", "VER-07"],
              ["Optical link", "PRBS-31 over the fiber: 3.4 × 10<super>12</super> bits, zero "
                               "errors (BER below 8.9 × 10<super>-13</super>); open eye "
                               "scans in both directions", "VER-06, LNK-10"],
              ["Compression, real data", "63 public AVIRIS and Hyperion scenes, all lossless, "
                                         "2.3:1 to 3.1:1 on average per dataset", "SPEC §11"],
              ["Radiation mitigation", "Configuration scrubbing, ECC on all memories, triple "
                                       "redundancy, watchdogs; 251 upsets injected on the board, "
                                       "all handled", "RAD-01 to 07"],
              ["Timing and resources", "Met on all 11 clocks (250 MHz data path); 17 % of the LUTs "
                                       "and 58 % of the UltraRAM", "VER-09"],
              ["Verification", "150 model tests, 87 RTL tests, all 864 compressor configurations "
                               "bit-exact, 1300-trial fault campaign", "VER-01 to 10"]],
              [1.3, 4.0, 1.1]),
          Spacer(1, 6),
          h2("How it was built"),
          bullets(["<b>Specification first.</b> A 102-requirement specification with "
                   "identifiers, milestones and acceptance criteria came before any hardware.",
                   "<b>Model first.</b> Bit-exact Python models of every block generate the test "
                   "vectors and the expected results.",
                   "<b>Bit-exact verification.</b> Every RTL test compares every output bit with "
                   "the model, with random stalls on every interface.",
                   "<b>Safe board tests.</b> All hardware runs load over JTAG only; the board's "
                   "flash was never written.",
                   "<b>Tools.</b> AMD Vivado and Vitis 2025.2, Python, SystemRDL with PeakRDL and Git. "
                   "The specification estimated about 31 part-time weeks; the work took six days."]),
          h2("Milestones"),
          table(["Milestone", "Content", "Result"], [
              ["M0", "Tool flow, board bring-up", "DMA loopback at 3865 MB/s"],
              ["M1", "Python reference models", "150 tests"],
              ["M2", "CCSDS 123 compressor RTL", "Bit-exact, 250 MHz"],
              ["M3", "Sensor emulator, correction, corner turn", "Bit-exact, 250 MHz"],
              ["M4", "First compression on the board", "15 scenes bit-exact"],
              ["M5", "Downlink chain and optical link", "Zero-error fiber link; scenes bit-exact"],
              ["M6", "PC ground station", "Decode, verify, display; Ethernet upload"],
              ["M7", "Radiation mitigation", "Scrubbing, ECC, TMR, watchdogs"]],
              [0.9, 3.0, 2.6]),
          Spacer(1, 6),
          h2("What remains"),
          p("Nothing that the specification requires. The compressed output is byte-identical with "
            "an independent CCSDS 123.0-B-2 implementation (the NTNU SmallSat Lab's model) on 944 "
            "images, the board's scenes among them. Optional: a decoder-side check with the CNES "
            "tools or hcomp.")]
    return s


def build(path):
    doc = HpdpDoc(path, TITLE, DOC_ID, REV, DATE,
                  subtitle="Hyperspectral Payload Data Processor: an FPGA model of a satellite's "
                           "onboard data chain, from raw sensor data to a bit-exact image at the "
                           "ground", author="Shubham Draveriya")
    doc.make(story(doc))
