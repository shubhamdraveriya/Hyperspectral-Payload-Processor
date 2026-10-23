"""HPDP Learning Guide, part C: advanced topics, results and procedures (chapters 17 to 25),
and appendices B to D."""

from hpdp_pdf import PageBreak, Spacer, bullets, caution, code, flow_diagram, h1, h2, note, p, steps

import guide_figs as gf
from guide_common import check, example, fig, key, tab

E = lambda n: f"10<super>−{n}</super>"  # noqa: E731


def part_c():
    s = [h1("Part C  Advanced topics"),
         p("Part C tells how the parts work together. It explains the control of the hardware, the clocks, the data streams and the protection against radiation. It also gives the method that the team used to prove that all of it is correct. It ends with the results and the procedures.")]

    # ------------------------------------------------------------------ 17
    s += [h1("17  Registers and software")]
    s += [h2("17.1  Registers"),
          p("The PS controls the data chain through <b>registers</b>. A register is a 32-bit memory "
            "location in the PL that the processor can read or write through the AXI4-Lite bus. "
            "Settings (for example the image size) are registers that the PS writes. Counters and status "
            "flags are registers that the PS reads."),
          *tab(["Address", "Block"], [
              ["0xA000_0000", "System: ID, build information, global control, mode, interrupts"],
              ["0xA001_0000 – 0xA004_0000", "Sensor emulator, radiometric correction, corner turn, compressor"],
              ["0xA005_0000 – 0xA007_0000", "Packetizer, AOS framer and channel coding, error injector"],
              ["0xA008_0000, 0xA009_0000", "Link, ground receiver"],
              ["0xA00F_0000", "Radiation monitor: SEM, ECC and TMR counters"],
              ["0xA010_0000, 0xA011_0000", "DMA engines"]],
              [2, 4], "The register address map (simplified)"),
          p("Each block has the same first registers: ID at 0x00, CTRL at 0x04 (enable, soft reset, "
            "start), STATUS at 0x08 and two interrupt registers. The counters start at 0x80. They are "
            "32 bits wide and they stop at the maximum value (they do not wrap). A write to CNT_CLEAR "
            "(0x7C) sets them to zero."),
          h2("17.2  One source for hardware, software and documents"),
          p("All registers are described in one file: regs/hpdp.rdl, in the SystemRDL language. From this one file, the PeakRDL tool makes the SystemVerilog register block, the C header, the Python definitions and an HTML description. Thus "
            "the hardware, the software and the documents always agree."),
          code(["python regs/generate.py --html"]),
          h2("17.3  The cfg_pending flag: a lesson from M4"),
          p("The register block operates on clk_ctrl (100 MHz). The data chain operates on other clocks. "
            "A new setting crosses into the other clocks as a complete snapshot. This takes "
            "approximately one microsecond."),
          p("In M4, the system test found a fault. The program wrote the DMA route register and started "
            "the DMA at once. The new route had not arrived yet, so the first part of the calibration "
            "table went to the sensor emulator. The fix is the flag SYS.STATUS.cfg_pending. It stays 1 "
            "until each write has arrived in each clock domain. The program now waits for 0 before it "
            "starts a DMA transfer or a run."),
          h2("17.4  The control program hpdp_run"),
          p("hpdp_run is a bare-metal program: it runs directly on one A53 processor, with no "
            "operating system. It is built with AMD Vitis 2025.2. At each run, it does this sequence: "
            "reset, link start (wait for LOCKED), load the tables, write the settings, start."),
          p("The program has two ways to receive work:"),
          bullets(["<b>Job blocks through JTAG</b> (all test runs). The host script makes a job block "
                   "with the scenes, the tables and the settings. It loads the job, the program and the "
                   "FPGA configuration through JTAG. The program reads the job at address 0x1000_0000 "
                   "and writes a result block at 0x1000_1000. The host reads the results and checks "
                   "them bit for bit. Each run is thus repeatable and checks itself.",
                   "<b>The UART console with the SD card.</b> You type commands on a serial terminal "
                   "(115200 bit/s). The program reads scenes and tables from a FAT32 SD card. The "
                   "console makes the same job block, so both ways use the same checks."]),
          *tab(["Command", "What it does"], [
              ["ls, load, cal", "List the SD card, load a scene file, load a calibration table"],
              ["mode capture | downlink | both", "Select where the compressed data goes"],
              ["raw, correction, period, repeat", "RAW mode, correction on or off, line period, repeats"],
              ["ei off | ber | burst | asm, seed", "Error injector settings"],
              ["link, prbs", "Link loopback settings, PRBS test"],
              ["run, status, show", "Start a run, show the counters, show the settings"]],
              [2.2, 4], "Main console commands"),
          h2("17.5  The host scripts and a board run"),
          fig(gf.board_run(), "The sequence of a board run"),
          p("sw/host/run_m5.py makes a full run of the chain. It generates or selects the scenes, runs "
            "the job, reads the results and checks them. Its main options are:"),
          *tab(["Option", "Use"], [
              ["--work NAME", "Folder for the run results (build/runs/NAME)"],
              ["--size NX NY NZ D, --scenes N", "Synthetic scene size and number of scenes"],
              ["--scene FILE", "Use a real scene file"],
              ["--line-period N, --repeat N", "Line timing; repeat the scenes for long runs"],
              ["--ber X, --prbs N", "Inject bit errors; run a PRBS test for N seconds"],
              ["--loopback fabric | pma", "Test the link without the fiber"],
              ["--raw, --no-capture", "RAW mode; do not capture the compressor output"],
              ["--xsa FILE", "Use another FPGA build, for example the 1024-pixel build"],
              ["--wdt, --ecc, --sem-campaign", "Radiation tests: watchdog, ECC injection, SEM upsets"],
              ["--fake-board, --dry-run", "Rehearse the run with no board"]],
              [2.4, 4], "Main options of run_m5.py"),
          key(["The PS writes settings to and reads counters from registers through AXI4-Lite.",
               "One SystemRDL file generates the register hardware, the C and Python headers and the documents.",
               "Wait for cfg_pending = 0 after you write settings.",
               "hpdp_run takes job blocks through JTAG or commands from the UART console."]),
          check("Chapter 17: Registers and software", [
              ("Why does the project generate the registers from one SystemRDL file?",
               "So that the hardware, the C program, the Python scripts and the documents always use the "
               "same addresses and fields."),
              ("What problem does cfg_pending solve?",
               "A setting needs approximately 1 µs to reach the other clock domains. Without the flag, "
               "the program can start a DMA transfer before the new setting is active."),
              ("Where does hpdp_run read its job, and where does it write the result?",
               "Job at 0x1000_0000, result block at 0x1000_1000.")])]

    # ------------------------------------------------------------------ 18
    s += [h1("18  Clocks, resets and clock-domain crossings")]
    s += [h2("18.1  Why there are several clocks"),
          *tab(["Clock", "Frequency", "Used by", "Why this clock"], [
              ["clk_sys", "250 MHz", "data path from DMA to ground receiver", "fast enough for 1 sample "
               "each cycle with margin"],
              ["clk_sensor", "100 MHz", "sensor emulator output", "independent, like the oscillator of a real "
               "camera"],
              ["clk_ctrl", "100 MHz", "registers, AXI4-Lite", "the PS control bus"],
              ["clk_link", "62.5 MHz", "link transmitter and receiver", "set by the GTH: 1.25 Gbaud / 20"],
              ["clk_fr", "31.25 MHz", "GTH helper logic, SEM, debug", "free-running: it must run before the "
               "GTH clocks start"]],
              [1.1, 0.9, 2.2, 2.6], "The clocks of the HPDP"),
          fig(gf.clock_domains(), "Clock domains and the crossings between them"),
          h2("18.2  The danger at a clock crossing"),
          p("A flip-flop samples its input at the edge of its own clock. If the input changes at that "
            "moment, the flip-flop can stay between 0 and 1 for a short time. This is "
            "<b>metastability</b>. Also, a multi-bit value can arrive half old and half new. Thus a "
            "signal must never go from one clock domain to another without a special circuit."),
          p("The HPDP uses only the AMD XPM library circuits for this:"),
          bullets(["<b>Asynchronous FIFOs</b> for data streams: sensor to data path, and data path to and "
                   "from the link. A FIFO is a small memory with a write side and a read side on "
                   "different clocks.",
                   "<b>Handshake crossings</b> for register snapshots: a complete group of settings or "
                   "counters crosses at one time, so it is never half old and half new."]),
          p("The Vivado report_cdc check finds each crossing. The final build has 0 unsafe and "
            "0 unknown crossings. One warning inside the AMD transceiver wizard has a written waiver "
            "with the reason."),
          h2("18.3  Resets"),
          p("Each clock domain has its own reset synchronizer. The reset starts at once (asynchronous) "
            "but ends at a clock edge (synchronous). Thus all flip-flops of a domain leave reset in the "
            "same clock cycle. The link logic stays in reset until the GTH reports that its own reset "
            "is complete."),
          h2("18.4  Timing closure"),
          p("At 250 MHz, a signal has 4 ns to go from one flip-flop through the logic to the next "
            "flip-flop. The tool calculates the time of each path. The <b>worst negative slack</b> (WNS) "
            "is the time that remains on the slowest path. A positive WNS means that all paths are fast "
            "enough."),
          example("the final build m7h", [
              "WNS = +0.087 ns on clk_sys (250 MHz): the slowest path has 87 picoseconds of spare time.",
              "WHS (hold) = +0.010 ns: no path is too fast either.",
              "All 11 clocks meet timing. 36 bus-skew constraints on the clock crossings are met."]),
          key(["Five clocks: 250 MHz data path, 100 MHz sensor and control, 62.5 MHz link, 31.25 MHz free-running.",
               "Data crosses clocks only through XPM asynchronous FIFOs or handshake snapshots.",
               "report_cdc: 0 unsafe, 0 unknown. Timing: WNS +0.087 ns at 250 MHz."]),
          check("Chapter 18: Clocks", [
              ("Why does the sensor emulator have its own clock?",
               "A real camera has its own oscillator, independent of the processor. The separate clock "
               "makes the test realistic, including the clock crossing."),
              ("What is metastability?",
               "A flip-flop that samples a changing input can stay between 0 and 1 for a short time. It "
               "can cause wrong values at a clock crossing."),
              ("What does a WNS of +0.087 ns mean?",
               "The slowest path of the design has 0.087 ns of spare time. All paths meet the 4 ns limit.")])]

    # ------------------------------------------------------------------ 19
    s += [h1("19  Data streams, back-pressure and overflow")]
    s += [h2("19.1  The AXI4-Stream handshake"),
          p("All blocks of the data path connect with AXI4-Stream. The sender drives the data (tdata) "
            "and a valid signal (tvalid). The receiver drives a ready signal (tready). One transfer "
            "occurs at each clock edge where tvalid and tready are both 1."),
          *tab(["Signal", "Meaning in the sample streams"], [
              ["tdata[15:0]", "the sample, in the D low bits"],
              ["tvalid", "the sender has a sample"],
              ["tready", "the receiver can accept a sample"],
              ["tuser[0]", "first sample of a scene"],
              ["tuser[1]", "first sample of a line"],
              ["tlast", "last sample of a line"]],
              [1.2, 4], "AXI4-Stream signals"),
          h2("19.2  Back-pressure"),
          p("When a receiver sets tready to 0, the sender must hold its data. This is "
            "<b>back-pressure</b>. Back-pressure is permitted after the corner turn. The compressor "
            "stops its full pipeline with one enable signal, and no data is lost. The sensor emulator "
            "does not accept back-pressure (Chapter 7)."),
          p("Each block output has a register. Each tready path goes through a <b>skid buffer</b>. A skid "
            "buffer holds one extra word, so the tready signal can also have a register. This keeps the "
            "timing paths short at 250 MHz."),
          p("Each block was tested with random tvalid and tready patterns (requirement VER-01). For "
            "example, the skid buffer test passed 21,000 transfers with random stalls."),
          h2("19.3  Where data can be lost, and how the HPDP reports it"),
          *tab(["Place", "Cause", "What the HPDP does"], [
              ["Sensor emulator", "the next block is full", "drops the sample, marks the line, counts it"],
              ["Sensor emulator", "DMA data late", "sends zeros, counts an underrun"],
              ["Corner turn", "both line buffers full", "sends a fill line, counts the dropped line"],
              ["Scene guard", "a scene is short", "adds zeros to complete it; packets carry a flag"],
              ["AOS framer", "no science data ready", "sends idle frames (no data lost)"],
              ["Pipeline", "no progress while input waits", "the watchdog raises an interrupt"]],
              [1.4, 2, 3], "Loss cases and reports"),
          p("The rule is: a loss is allowed only if it is counted. The HPDP never loses data silently."),
          key(["A transfer occurs when tvalid and tready are both 1.",
               "Back-pressure is allowed after the corner turn, but never at the sensor emulator.",
               "Every loss of data is counted and reported."]),
          check("Chapter 19: Data streams", [
              ("When does a transfer occur on an AXI4-Stream interface?",
               "At a clock edge where tvalid and tready are both 1."),
              ("What does a skid buffer do?",
               "It holds one extra word, so the tready signal can have a register and the timing path "
               "stays short.")])]

    # ------------------------------------------------------------------ 20
    s += [h1("20  Protection against radiation")]
    s += [h2("20.1  What radiation does to an FPGA"),
          p("In space, high-energy particles go through the electronics. A particle can change the "
            "value of one memory bit or one flip-flop. This is a <b>single-event upset</b> (SEU). In an "
            "FPGA like the XCZU7EV, the configuration of the circuit is also in memory cells. An upset "
            "there can change the circuit itself."),
          p("The HPDP uses commercial parts, so it is not protected at the material level. It uses the "
            "same design methods as a real payload, in five layers."),
          h2("20.2  Layer 1: configuration scrubbing"),
          p("The AMD Soft Error Mitigation (SEM) IP reads the full configuration memory all the time. "
            "Each frame of the configuration memory has an error-correcting code. When SEM finds a "
            "single wrong bit, it writes the correct value back. One full scan of the 51,906 frames "
            "takes 154 ms."),
          p("On the board, the team injected 251 configuration upsets. SEM corrected 198 of them in "
            "125 to 153 ms (the mean was 138 ms). The other 53 were in frames that the design does not "
            "use or that SEM masks. There were 0 uncorrectable upsets, and the scenes after the tests "
            "were bit-exact."),
          h2("20.3  Layer 2: error-correcting memory (ECC)"),
          p("A SECDED code adds check bits to each memory word. It corrects each single wrong bit "
            "(single-error correct) and finds each double wrong bit (double-error detect). All 56 "
            "UltraRAMs of the design use their built-in ECC. The compressor weights are in block RAM "
            "with ECC. When the calibration table reads a corrected entry, it writes the correct entry "
            "back, so that errors do not collect."),
          h2("20.4  Layer 3: triple modular redundancy (TMR)"),
          fig(gf.tmr(), "TMR: three copies and a majority voter"),
          p("TMR keeps three copies of an important value. A voter selects the value that at least two "
            "copies agree on. Each copy is written again from the vote in each clock cycle, so an upset "
            "disappears after one cycle. A synthesis attribute (DONT_TOUCH) stops the tool from merging "
            "the three copies into one."),
          p("The HPDP uses TMR for the control state machines, the register block and the coder and neighbourhood memories of the compressor. Since build m7h, it also uses TMR for the address and count registers of the corner turn, the packetizer and the framer. For the small memories, a vote "
            "adds one logic level. A Hamming decoder would add five or six levels, which is too slow "
            "at 250 MHz."),
          h2("20.5  Layer 4: watchdogs"),
          bullets(["PL watchdogs are in the compressor, the downlink chain and the ground capture. If a block makes no progress for 2<super>26</super> cycles (approximately 0.27 s) while data waits, it raises an interrupt.",
                   "The PS system watchdog (10 s) resets the board if the program stops. A job flag arms "
                   "it, so a debug session that halts the processor does not cause a reset. In the "
                   "test, the program stopped on purpose and the board reset as expected."]),
          h2("20.6  Layer 5: flags in the data"),
          p("Sometimes the HPDP finds an error that it cannot correct. Then it sets bit 0 of the flags in the packet headers of the scene and raises an interrupt. The ground station thus "
            "knows which scenes it must not trust. In a board test, a double-bit error in the "
            "calibration table caused all scenes of the run to have the flag."),
          h2("20.7  The fault-injection campaign"),
          p("How do you know that the protection works? The team changed random bits in a simulation "
            "of the design and classified each result:"),
          *tab(["Result", "Meaning"], [
              ["Masked", "the changed bit had no effect on the output"],
              ["Corrected", "ECC or TMR corrected the bit"],
              ["Detected", "the design found the error and reported it (flag or counter)"],
              ["Silent", "the output was wrong and nothing reported it: the worst result"]],
              [1, 5], "Fault classes"),
          *tab(["Area", "Trials", "Masked", "Corrected", "Detected", "Silent"], [
              ["Protected state", "820", "15", "567", "238", "<b>0</b>"],
              ["Unprotected data registers", "480", "364", "0", "8", "108 (22.5 %)"]],
              [2.4, 0.8, 0.8, 0.9, 0.9, 1.2], "Fault campaign results, final build m7h (1300 trials)"),
          p("Zero silent results in 820 trials means that the silent fraction in the protected state is "
            "less than 0.4 %, with 95 % confidence. The unprotected data registers hold samples that "
            "pass through once. An upset there damages some samples. The team accepted this as a "
            "residual risk."),
          h2("20.8  How the campaign changed the design"),
          p("The campaign on build m7g examined the address and count registers of the corner turn, the packetizer and the framer. Upsets there were silent 93 % to 100 % of the time. Build m7h "
            "added TMR to these registers, for 0.7 k more LUTs. After this change, all 220 simulated "
            "upsets in them were corrected."),
          note("The campaign also found a fault in the test itself. In the simulator, the statement "
               "x[$urandom_range(...)] ^= 1 calculates the index two times, so it changed nothing in "
               "462 of 1000 trials. The test now stores the index in a variable first. Always check "
               "that your test does what you think."),
          key(["SEM scrubbing corrected configuration upsets on the board in 125 to 153 ms.",
               "ECC protects all UltraRAMs and the weights; TMR protects state machines, registers and "
               "control counters.",
               "Watchdogs catch a stopped pipeline or program; flags mark scenes with uncorrectable errors.",
               "The fault campaign found 0 silent results in 820 trials in protected state."]),
          check("Chapter 20: Radiation", [
              ("Why is an upset in the configuration memory of an FPGA dangerous?",
               "The configuration memory defines the circuit. An upset can change the circuit itself, not "
               "only one data value."),
              ("What does SECDED mean?", "Single-error correct, double-error detect."),
              ("Why is each TMR copy written again from the vote in each cycle?",
               "So that an upset in one copy disappears after one cycle and cannot add to a second upset "
               "later."),
              ("Which result class is the worst in a fault campaign, and why?",
               "Silent: the output is wrong and nothing reports it, so nobody knows that the data is bad.")])]

    # ------------------------------------------------------------------ 21
    s += [h1("21  How the team proved that it works")]
    s += [h2("21.1  Model first"),
          p("Before the team wrote any hardware, they wrote a Python model of each block. The model is "
            "bit-exact: it gives exactly the bits that the hardware must give. The hardware is then "
            "correct when its output is identical to the model output. One different bit shows a fault."),
          fig(flow_diagram(), "The method of work for each block"),
          p("The compressor model can also record each intermediate value of each sample. When the "
            "hardware differs from the model, this record shows the exact sample and the exact pipeline "
            "stage where the difference starts."),
          h2("21.2  The test levels"),
          *tab(["Level", "What", "Result"], [
              ["Python models", "150 tests: compression round trips, RS, framing, frame sync, full chain",
               "all pass"],
              ["RTL regression", "87 self-checking simulations with model data and random stalls; "
                                 "headless, approximately 9 minutes", "all pass"],
              ["Configuration matrix", "all 864 compressor setting combinations (VER-02), 57 minutes",
               "864 of 864 bit-exact"],
              ["Corner cases", "all zero, all maximum, random data, Nx = 1, minimum and maximum bands "
                               "(VER-03)", "all pass"],
              ["System simulation", "small scenes through the full chain with a link model and errors "
                                    "(VER-05)", "bit-exact"],
              ["Fault campaign", "1300 random upsets", "0 silent in protected state"],
              ["Board", "tests after each milestone, JTAG only", "see Chapter 23"]],
              [1.5, 4, 1.4], "The test levels"),
          h2("21.3  The milestones"),
          *tab(["Milestone", "Content", "Done when (and result)"], [
              ["M0", "Tool flow, base design, DMA loopback", "64 MB loopback at 3865 MB/s"],
              ["M1", "Python models", "bit-exact models of each block, with tests (150 today)"],
              ["M2", "Compressor hardware", "bit-exact on all configurations, 250 MHz met"],
              ["M3", "Sensor emulator, correction, corner turn", "bit-exact with the models, 250 MHz met"],
              ["M4", "First board run (compressor output to memory)", "15 scenes bit-exact, 99.8 Msamples/s"],
              ["M5", "Downlink chain and optical link", "PRBS, BER sweep and 10-minute rate test pass"],
              ["M6", "PC ground station", "decode, display and verify; Ethernet upload tested on the board"],
              ["M7", "Radiation protection", "SEM, ECC, TMR, watchdogs; fault campaign; final build m7h"]],
              [0.9, 2.8, 3.2], "The project milestones"),
          h2("21.4  Problems that the team found, and the lessons"),
          *tab(["Problem", "Cause", "Solution"], [
              ["The processor stopped at address 0xC after the first DDR4 write",
               "The board preset assumed another memory module (x8 devices, not x16)",
               "Correct DDR4 settings in the block design"],
              ["The board started the flash image at each reset and damaged the loaded data",
               "Boot mode pins set to QSPI flash", "A temporary JTAG boot override at each session; the "
                                                   "flash stays untouched"],
              ["Vitis reported “invalid os”", "A space in the folder path", "Directory junctions with no spaces"],
              ["The first table data went to the wrong block", "A setting had not crossed the clock domain yet",
               "The cfg_pending flag (Section 17.3)"],
              ["Scenes merged under heavy overload", "A short scene never ended", "The scene guard (Section 9.4)"],
              ["Idle frames passed as data at high BER", "Exact match of the VCID",
               "Match by bit distance (Section 11.4)"],
              ["Long runs had underruns", "The debugger halted the processor to read memory",
               "Read memory with no halt"],
              ["The fault test changed nothing in half of the trials", "A simulator statement evaluated "
               "twice", "Store the random index first"],
              ["The Ethernet upload stopped after approximately 290 kB", "No timer was selected for the "
               "lwIP timers, so a lost TCP segment was never sent again", "Select a timer (TTC0) in the "
                                                                          "software settings"],
              ["The PC connection did not get to the board after a board reset", "Windows sent it "
               "through the office network adapter", "Connect from the PC's own link-local address"]],
              [2.2, 2.3, 2.3], "Problems found during the project"),
          key(["A bit-exact Python model comes first; the hardware must match it bit for bit.",
               "87 regression tests, the 864-configuration matrix, a fault campaign and board tests "
               "prove each requirement.",
               "Many important design features (cfg_pending, scene guard, TMR on the steering state) "
               "came from faults that the tests found."]),
          check("Chapter 21: Verification", [
              ("Why is a bit-exact model so useful for hardware tests?",
               "The hardware output must be identical to the model output. Any difference is a fault, "
               "and the model trace shows where it starts."),
              ("How many compressor configurations did the team test, and with which result?",
               "All 864 configurations of the VER-02 matrix; all 864 were bit-exact.")])]

    # ------------------------------------------------------------------ 22
    s += [h1("22  Implementation results")]
    s += [fig("utilization.png", "FPGA resources used by the two final builds"),
          *tab(["Resource", "Final build m7h (512 pixels)", "Build m7i_1024b (1024 pixels)"], [
              ["LUT", "38.2 k (16.6 %)", "38.9 k"],
              ["Flip-flop", "39.2 k (8.5 %)", "40.1 k"],
              ["Block RAM (36 Kb)", "38.5 (12.3 %)", "46.5"],
              ["UltraRAM", "56 (58.3 %)", "80 (83.3 %)"],
              ["DSP", "24 (1.4 %)", "24"],
              ["GTH transceivers", "2", "2"],
              ["Setup slack (WNS) at 250 MHz", "+0.087 ns", "+0.012 ns"]],
              [2, 2.4, 2.4], "Resources and timing of the two final builds"),
          p("The design uses less than one fifth of the logic. The largest user of a resource is "
            "UltraRAM: the calibration table, the corner-turn buffers and the compressor line buffer "
            "with ECC."),
          h2("22.1  The 1024-pixel build"),
          p("Requirement SYS-04 says that the design must scale to 1024 pixels by a parameter. With one table entry in each memory word, the calibration table would need 64 UltraRAMs. The design would then need 112 UltraRAMs, but only 96 exist. The solution is a second table layout: two "
            "entries (dark and gain) in each word, and the bad-pixel flags in a separate block RAM. The "
            "build selects this layout automatically. The 1024-pixel build uses 80 UltraRAMs and meets "
            "250 MHz. The team tested it on the board with a 1024-pixel Hyperion scene. A build for "
            "1024 × 512 would need 160 UltraRAMs and does not fit."),
          key(["The final build uses 16.6 % of the LUTs and 58.3 % of the UltraRAMs and meets 250 MHz.",
               "A packed table layout lets the 1024-pixel build fit in 80 of 96 UltraRAMs."]),
          check("Chapter 22: Implementation", [
              ("Which resource limits the size of the HPDP?",
               "UltraRAM: the 1024-pixel build already uses 80 of the 96 UltraRAMs.")])]

    # ------------------------------------------------------------------ 23
    s += [h1("23  Results summary")]
    s += [*tab(["Area", "Test", "Result"], [
              ["Throughput", "Mission rate for 10 minutes (VER-08)", "600.1 s at 76.8 Msamples/s, 2344 scenes, "
                                                                     "0 dropped lines, 0 sync losses"],
              ["Throughput", "Compressor capacity", "250 Msamples/s (1 sample each clock at 250 MHz)"],
              ["End to end", f"Scenes over the fiber at BER 0, {E(4)}, {E(3)} (VER-07)", "all bit-exact"],
              ["End to end", "BER sweep", f"all corrected up to 2 × {E(3)}"],
              ["Compression", "63 real scenes", "all lossless; 4.4 to 6.0 bits per sample on average "
                                                "(2.3:1 to 3.1:1)"],
              ["Link", "PRBS-31 over the fiber (VER-06)", f"3.4 × 10<super>12</super> bits, 0 errors, "
                                                          f"BER < 8.9 × {E(13)}"],
              ["Link", "Eye scan (LNK-10)", "77.5 % × 96.8 % open (SFP0 to SFP1)"],
              ["RAW mode", "SYS-09", "99.1 % of lines dropped and counted, as required"],
              ["Radiation", "SEM upsets on the board", "198 corrected in 125 to 153 ms, 0 uncorrectable"],
              ["Radiation", "Fault campaign", "0 silent in 820 protected trials"],
              ["Timing", "All clocks (VER-09)", "met; WNS +0.087 ns; report_cdc clean"],
              ["Requirements", "102 v1.0 shall/should items", "100 met, 2 pending (O-5)"]],
              [1.2, 2.6, 3.2], "The main results"),
          key(["The HPDP meets the mission rate, gets every test scene back bit-exactly and survives "
               "injected upsets."])]

    # ------------------------------------------------------------------ 24
    s += [h1("24  Status, limits and next steps")]
    s += [h2("24.1  Status"),
          bullets(["100 of the 102 v1.0 requirements are met. None is unmet. The last one, SW-05 "
                   "(the Ethernet upload of the received data), was tested on the board on 23 October "
                   "2026.",
                   "<b>CMP-02 and MDL-02 are pending</b> (open item O-5). They need a check of the "
                   "compressor output with an independent CCSDS 123 decoder: ESA WhiteDwarf 2.0 or the "
                   "CNES tools. Both need a user account."]),
          h2("24.2  Limits"),
          bullets(["Commercial parts, no radiation qualification.",
                   "No radio link and no real camera.",
                   "Lossless mode only. The near-lossless mode of the standard is not in v1.0.",
                   "RS decoding and decompression are in Python on the PC, so they are slow.",
                   "Upsets in unprotected data registers are silent approximately one time in four "
                   "(accepted residual risk)."]),
          h2("24.3  Possible next steps"),
          bullets(["Decode the test data with an independent CCSDS 123 decoder (closes O-5).",
                   "Near-lossless compression and the hybrid entropy coder.",
                   "A hardware RS decoder, a faster ground decoder (C or numba), and Linux in place of "
                   "bare-metal.",
                   "A return channel: the fiber cable has two fibers, so this is now possible."]),
          key(["The project is complete except one external check (independent decoder)."])]

    # ------------------------------------------------------------------ 25
    s += [h1("25  Procedures")]
    s += [p("This chapter gives the procedures to repeat the main tests. Do the procedures in the "
            "project folder. The commands are for the Windows command line with Vivado and Vitis "
            "2025.2 installed."),
          h2("25.1  Prepare the board"),
          caution(["Use JTAG only. Do not write the QSPI flash of the board. The test flow loads all "
                   "files into volatile memory.",
                   "Do not bend the fiber tightly. Put the dust caps on the SFP modules and the fiber ends "
                   "when you disconnect them."]),
          steps(["Connect the USB cable (JTAG and UART) between the board and the PC.",
                 "Connect the OM3 fiber between SFP0 and SFP1.",
                 "If you use the UART console, put the SD card with the scene files into the board.",
                 "Set the power switch of the board to on."]),
          h2("25.2  Run the model tests"),
          steps(["Open a command window in the project folder.",
                 "Type the command below and push Enter.",
                 "Make sure that all 150 tests pass."]),
          code(["python -m pytest model"]),
          h2("25.3  Run the hardware regression"),
          steps(["Type the command below. The regression takes approximately 10 minutes.",
                 "Make sure that all 87 tests show PASS."]),
          code(["vivado -mode batch -source tb/run_sim.tcl"]),
          h2("25.4  Build the FPGA configuration"),
          steps(["Set HPDP_BUILD_DIR to a new folder in build/ if you want to keep the old build.",
                 "For the 1024-pixel build, set HPDP_NX_MAX to 1024.",
                 "Type the command below.",
                 "Make sure that the exit code is 0. The script returns 1 if the timing is not met."]),
          code(["vivado -mode batch -source vivado/build.tcl"]),
          h2("25.5  Build the program"),
          code(["vitis -s sw/baremetal/build.py build/m7h/hpdp.xsa"]),
          h2("25.6  Do a board run"),
          steps(["Type the command below. Use a new name for each run.",
                 "Wait until the script shows the result.",
                 "Make sure that the result is PASS. The run files are in build/runs/NAME."]),
          code(["python sw/host/run_m5.py --work NAME"]),
          h2("25.7  Decode the run at the ground station"),
          code(["python sw/ground/hpdp_gs.py decode build/runs/NAME/gr.bin --ref build/runs/NAME --show"]),
          h2("25.8  If a fault occurs"),
          *tab(["Symptom", "Possible cause", "Action"], [
              ["The link does not lock. LNK.STATUS shows 0xF07 and many code errors",
               "The fiber is not connected", "Connect the fiber. To test the board without the fiber, use "
                                             "--loopback pma"],
              ["The processor stops at address 0xC", "Wrong DDR4 settings for the memory module",
               "Use the block design of the project, which has the correct settings"],
              ["The loaded program disappears after a reset", "The board starts from the QSPI flash",
               "Let the host script set the JTAG boot override; do not change the flash"],
              ["Vitis reports “invalid os”", "A space in the path", "Use the junction folders "
                                                                    "(docs/new_system_setup.md)"],
              ["Underruns in long runs", "The processor was halted to read memory",
               "Use the read method that does not halt the processor"]],
              [2.3, 2, 2.5], "Faults and actions"),
          Spacer(1, 4)]
    return s


def appendices_bcd():
    s = [PageBreak(), h1("Appendix B  Numbers to remember")]
    s += [*tab(["Item", "Value"], [
              ["Image", "512 pixels × 150 bands, 12-bit samples, 1000 lines each second"],
              ["Raw rate", "76.8 Msamples/s = 921.6 Mbit/s"],
              ["Scene", "256 lines = 19,660,800 samples"],
              ["Data path clock", "250 MHz (4 ns); 1 sample each clock"],
              ["Calibration", "c = clamp(((x − dark) × gain + 2^13) >> 14, 0, 2^D − 1); gain 0x4000 = 1.0"],
              ["Compressor", "P = 3 earlier bands (up to 15), Ω = 13, U<sub>max</sub> = 18; NZ_MIN = 16"],
              ["Real compression", "2.3:1 to 3.1:1 on average; all lossless"],
              ["Space Packet", "14-byte header + 2048 data bytes"],
              ["AOS frame", "892 bytes = 6 + 2 + 884; idle frames on VCID 63"],
              ["Reed-Solomon", "RS(255,223), corrects 16 bytes per codeword; I = 4; codeblock 1020 bytes"],
              ["Randomizer", "starts FF 48 0E C0; period 255 bytes"],
              ["Sync marker, CADU", "1A CF FC 1D; CADU = 1024 bytes"],
              ["Link", "1.25 Gbaud, 8b/10b, 62.5 MHz × 16 bits, 1000 Mbit/s payload, 850 nm, OM3"],
              ["Link capacity for user data", "≈ 857 Mbit/s"],
              ["Link lock", "1024 clean words; lost after more than 8 code errors in 1024 words"],
              ["Frame sync", "up to 2 wrong ASM bits; VERIFY 1024 bytes later; FLYWHEEL 3"],
              ["Capture record", "1020-byte codeblock + 4-byte status word"],
              ["SEM", "full scan 154 ms; upsets corrected in 125 to 153 ms"],
              ["Final build m7h", "38.2 k LUT, 56 URAM, 24 DSP, WNS +0.087 ns"]],
              [1.8, 5], "Numbers to remember")]

    s += [PageBreak(), h1("Appendix C  Where things are in the repository")]
    s += [*tab(["Folder", "Contents"], [
              ["SPEC.md", "The specification: all requirements, interfaces and milestones"],
              ["rtl/frontend/", "Sensor emulator, radiometric correction, corner turn, scene guard"],
              ["rtl/ccsds123/", "The compressor: neighbourhood, local differences, predictor, coder, packer"],
              ["rtl/downlink/", "Space packetizer, AOS framer, RS encoder, CADU framer, error injector"],
              ["rtl/link/", "Link layer, link transmitter and receiver"],
              ["rtl/ground/", "Frame synchronizer with derandomizer, ground capture"],
              ["rtl/common/", "Skid buffer, clock-crossing bus, ECC and TMR memories, FIFO, watchdog"],
              ["rtl/rad/", "SEM controller support"],
              ["rtl/system/", "hpdp_core (data path, registers, crossings), hpdp_top (transceivers and block design)"],
              ["rtl/regs/, regs/", "The generated register block; the SystemRDL source and its generator"],
              ["model/", "Bit-exact Python models, their tests, hpdp_tool.py and hpdp_datasets.py"],
              ["tb/", "Testbenches and test-vector generators; tb/run_sim.tcl runs the regression"],
              ["vivado/", "Build scripts, block design, constraints and IP settings"],
              ["sw/baremetal/", "The A53 program hpdp_run (job, console, network) and its build script"],
              ["sw/host/", "Host scripts: run_m4.py, run_m5.py, sd_put.py, eye-scan tools"],
              ["sw/ground/", "The ground station hpdp_gs.py and its tests"],
              ["docs/", "Milestone notes, project report, real-data results, runbooks, these PDF documents"],
              ["build/", "Builds, reports and run results (not in version control)"]],
              [1.5, 5], "The repository folders")]

    s += [PageBreak(), h1("Appendix D  Technical names and abbreviations")]
    s += [*tab(["Name", "Meaning"], [
              ["AOS", "Advanced Orbiting Systems: the CCSDS protocol for transfer frames"],
              ["APID", "Application process identifier: the source field of a Space Packet"],
              ["ASM", "Attached sync marker, 1A CF FC 1D: marks the start of each CADU"],
              ["AXI", "The on-chip bus standard of AMD devices (AXI4-Lite for registers, AXI4-Stream for data)"],
              ["BER", "Bit error rate: wrong bits divided by all bits"],
              ["BIL, BIP, BSQ", "Band-interleaved by line, by pixel; band-sequential: sample sequences"],
              ["BRAM, URAM", "Block RAM (36 Kb) and UltraRAM (288 Kb): on-chip memories"],
              ["CADU", "Channel access data unit: ASM plus randomized codeblock (1024 bytes)"],
              ["CCSDS", "Consultative Committee for Space Data Systems: writes the space data standards"],
              ["CDC", "Clock-domain crossing"],
              ["CDR", "Clock and data recovery: finds the bit clock in a serial signal"],
              ["DMA", "Direct memory access: moves data between memory and the PL without the processor"],
              ["DSP", "A hardware multiplier and adder in the FPGA"],
              ["ECC, SECDED", "Error-correcting code; single-error correct, double-error detect"],
              ["FHP", "First header pointer of an AOS frame"],
              ["FIFO", "First in, first out: a memory that keeps the sequence of the data"],
              ["FPGA", "Field-programmable gate array"],
              ["FSM", "Finite state machine"],
              ["GTH", "The multi-gigabit transceiver of the device"],
              ["JTAG", "The debug and programming connection of the board"],
              ["LFSR, PRBS", "Linear feedback shift register; pseudo-random bit sequence"],
              ["LUT", "Lookup table: the basic logic part of the FPGA"],
              ["PS, PL", "Processing system (Arm processors); programmable logic (FPGA part)"],
              ["RS", "Reed-Solomon error-correcting code"],
              ["SEM", "Soft Error Mitigation IP: scrubs the configuration memory"],
              ["SEU", "Single-event upset: a bit changed by radiation"],
              ["SFP", "Small form-factor pluggable optical module"],
              ["TMR", "Triple modular redundancy: three copies and a majority voter"],
              ["VCID", "Virtual channel identifier of an AOS frame (63 = idle)"],
              ["WNS, WHS", "Worst negative slack (setup) and worst hold slack: timing margins"],
              ["XPM", "AMD macro library, used for the clock-domain crossings"]],
              [1.3, 5], "Technical names and abbreviations")]
    return s
