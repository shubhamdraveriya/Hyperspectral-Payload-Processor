"""HPDP Learning Guide, part A: the basics (chapters 1 to 5)."""

from hpdp_pdf import Spacer, bullets, caution, chain_diagram, h1, h2, note, p, steps

import guide_figs as gf
from guide_common import check, example, fig, key, tab


def part_a():
    s = []
    # ------------------------------------------------------------------ 1
    s += [h1("Part A  The basics"),
          p("Part A is for a reader with no special knowledge. It tells what the HPDP is, which "
            "problems it solves and which basic ideas you must know before you read Part B."),
          h1("1  About this guide")]
    s += [h2("1.1  Purpose"),
          p("This guide teaches you the HPDP from the start. HPDP is the short name for the "
            "Hyperspectral Payload Data Processor. The guide starts with simple ideas. Each chapter "
            "adds more detail. At the end, you will know what each part of the system does, why it "
            "is there and how the project team tested it."),
          h2("1.2  How to read this guide"),
          p("The guide has three parts. Read them in sequence, because each part uses the words and "
            "the ideas of the part before it."),
          *tab(["Part", "Level", "Contents"], [
              ["A", "Basic", "The problem, the basic ideas (bits, data rate, clock, image cube), the "
                             "hardware and the data chain at a glance"],
              ["B", "Intermediate", "Each block of the data chain: what it does, how it does it, a worked "
                                    "example and the measured results"],
              ["C", "Advanced", "Registers and software, clocks, data streams, radiation protection, the "
                                "verification method, the results and the procedures"]],
              [0.6, 1.2, 6], "The three parts of this guide"),
          p("Each chapter ends with two boxes:"),
          bullets(["<b>KEY POINTS</b> gives the most important facts of the chapter.",
                   "<b>Check your knowledge</b> gives some questions. Try to answer them before you "
                   "continue. Appendix A gives the answers."]),
          p("Appendix B gives the important numbers on one page. Appendix C tells where each part "
            "of the project is in the repository. Appendix D gives the technical names and "
            "abbreviations."),
          h2("1.3  Writing rules"),
          p("This guide uses ASD-STE100 Simplified Technical English (STE). STE is a controlled "
            "language for technical documents. These are the rules that you will see:"),
          bullets(["A sentence in a description has a maximum of 25 words.",
                   "A sentence in a procedure has a maximum of 20 words. Each step gives one instruction.",
                   "Instructions use the imperative form of the verb, for example “Connect the fiber”.",
                   "Each word has only one meaning.",
                   "Technical names, for example “Reed-Solomon code”, are used where necessary. "
                   "Appendix D tells what they mean."]),
          h2("1.4  Warnings, cautions and notes"),
          *tab(["Type", "Meaning"], [
              ["<b>WARNING</b>", "Tells you about a risk of injury to persons."],
              ["<b>CAUTION</b>", "Tells you about a risk of damage to the equipment, to the data or to the "
                                 "test results."],
              ["<b>NOTE</b>", "Gives information that helps you."]],
              [1, 5], "Warnings, cautions and notes"),
          Spacer(1, 4)]

    # ------------------------------------------------------------------ 2
    s += [h1("2  The HPDP in one page")]
    s += [h2("2.1  What the HPDP is"),
          p("An Earth observation satellite carries a camera. The camera records the ground. The "
            "satellite must then send the images to a station on the ground. Between the camera and "
            "the radio transmitter, there is an electronic unit that prepares the data. This unit is "
            "the payload data processor."),
          p("The HPDP is a working model of this unit. It does not fly. It operates on one AMD "
            "ZCU106 evaluation board on a desk. The board also contains the receiver of the ground "
            "station, so that one board can do the full test."),
          p("The HPDP does not change the image. The ground station gets back each bit of the image "
            "exactly as the satellite had it. The project calls this result <b>bit-exact</b>. "
            "Bit-exact is the pass criterion of almost all tests in this project."),
          h2("2.2  The four problems that the HPDP solves"),
          p("Figure 1 shows the four problems and the answer of the HPDP to each problem."),
          fig(gf.four_problems(), "The four problems of a satellite data chain and the answers of the HPDP"),
          p("You can compare the data chain to a shipment of books through a narrow pipe that "
            "sometimes damages the parcels:"),
          bullets(["Compression packs the books tightly, so that more books go through the pipe.",
                   "Error correction adds spare information, so that the receiver can repair a "
                   "damaged parcel.",
                   "Packets, frames and sync markers put labels on each parcel, so that the receiver "
                   "knows where each parcel starts and which book it holds.",
                   "Radiation protection checks the packing machine all the time, because space "
                   "radiation can damage it."]),
          h2("2.3  What the HPDP is not"),
          p("The HPDP shows the architecture, the standards and the test method of a real payload "
            "processor. Some things are not in the project:"),
          bullets(["The parts are commercial parts, not radiation-tolerant parts.",
                   "There is no radio transmitter. A 1-metre optical fiber is used in place of the "
                   "radio link.",
                   "There is no real camera. The board reads recorded images from memory and sends "
                   "them at the speed of a real camera."]),
          key(["The HPDP is a working model of the data processor of a hyperspectral satellite.",
               "It compresses the data, adds error correction and labels, sends the data over a "
               "fiber and gets the image back at a ground receiver.",
               "The pass criterion is bit-exact: the ground image is identical to the original, bit "
               "for bit."]),
          check("Chapter 2: The HPDP in one page", [
              ("Why must a satellite compress its image data?",
               "The camera makes more data than the link can send. In the HPDP reference mission, the "
               "camera makes 921.6 Mbit/s, but the link carries only approximately 857 Mbit/s of user "
               "data."),
              ("What does “bit-exact” mean?",
               "The image that the ground station rebuilds is identical to the original corrected "
               "image. Not one bit is different."),
              ("Which part of a real satellite does the optical fiber replace?",
               "The radio link between the satellite and the ground station.")])]

    # ------------------------------------------------------------------ 3
    s += [h1("3  Basic ideas that you must know")]
    s += [h2("3.1  Bits, bytes and samples"),
          p("A <b>bit</b> has one of two values: 0 or 1. A <b>byte</b> is a group of 8 bits. A byte "
            "has 256 possible values (0 to 255)."),
          p("A camera measures the light at each point. Each measurement is a number. In this project, "
            "one such number is a <b>sample</b>. The HPDP reference camera uses 12 bits for each "
            "sample, so a sample has a value from 0 to 4095. The hardware keeps each sample in a "
            "16-bit container, because 16 bits are easier to move."),
          h2("3.2  Data rate"),
          p("The data rate tells how many bits go through a point in one second. The unit is bit/s. "
            "1 Mbit/s is one million bits each second. 1 Gbit/s is one thousand million bits each "
            "second."),
          example("the raw data rate of the HPDP camera", [
              "The camera records 1000 lines each second.",
              "Each line has 512 pixels, and each pixel has 150 colour bands.",
              "Samples each second = 1000 × 512 × 150 = 76,800,000 = 76.8 Msamples/s.",
              "Bits each second = 76.8 Msamples/s × 12 bits = 921.6 Mbit/s.",
              "The link can carry approximately 857 Mbit/s of user data. Thus the raw data does not fit. "
              "Chapter 6 shows the full calculation."]),
          h2("3.3  Clock and clock cycle"),
          p("Digital logic does its work in steps. A <b>clock</b> is a signal that gives the time of "
            "each step. One step is one <b>clock cycle</b>. The clock frequency tells how many steps "
            "occur in one second."),
          p("The main HPDP clock (clk_sys) has a frequency of 250 MHz. Thus one clock cycle is "
            "4 nanoseconds. Each block of the data chain accepts one sample (or one byte) in each "
            "clock cycle. At 250 MHz, the compressor can thus accept 250 Msamples/s. This is more "
            "than three times the 76.8 Msamples/s of the camera."),
          h2("3.4  Hyperspectral images"),
          p("A normal colour camera records three bands of colour: red, green and blue. A "
            "<b>hyperspectral</b> camera records many narrow bands. The HPDP reference camera records "
            "150 bands. The set of values of one pixel in all bands is the <b>spectrum</b> of that "
            "pixel. Different materials (water, plants, rock, metal) have different spectra. Thus a "
            "scientist can identify materials from space."),
          p("A hyperspectral image has three dimensions. Figure 2 shows them:"),
          bullets(["x: the position across the track of the satellite (512 pixels).",
                   "y: the position along the track. Each new line is a new y (1000 lines each second).",
                   "z: the spectral band (150 bands)."]),
          fig(gf.pushbroom_cube(), "Pushbroom imaging, the image cube and the spectrum of one pixel"),
          p("One <b>scene</b> is a part of the cube with a fixed number of lines. The HPDP uses "
            "256 lines for each scene. One scene thus has 512 × 256 × 150 = 19,660,800 samples. "
            "The HPDP compresses each scene independently. An error that destroys one scene thus "
            "cannot damage the next scene."),
          h2("3.5  Pushbroom imaging"),
          p("The HPDP camera is a <b>pushbroom</b> camera. It does not take a full picture at one "
            "time. It looks through a narrow slit and records one line of the ground. The satellite "
            "moves forward, so the next line is a new strip of ground. A broom that you push along "
            "the floor makes the same pattern. That is the origin of the name."),
          p("A pushbroom camera cannot stop. The ground moves under it at a constant speed. If the "
            "electronics cannot accept a line at the correct time, that line is lost. This fact is "
            "important for the design of the HPDP. Chapter 7 tells how the HPDP handles it."),
          h2("3.6  Streams and blocks"),
          p("The data moves through the HPDP as a <b>stream</b>: a long sequence of samples or bytes, "
            "one after the other. Some blocks cut the stream into parts of fixed length, for example "
            "packets and frames. Other blocks change each value but keep the stream length."),
          h2("3.7  Standards and CCSDS"),
          p("A satellite and its ground station must use the same data formats. The Consultative "
            "Committee for Space Data Systems (CCSDS) writes these formats as standards. Space "
            "agencies all over the world use them. The HPDP follows four CCSDS standards:"),
          *tab(["Standard", "Subject", "Used in"], [
              ["CCSDS 123.0-B-2", "Lossless compression of multispectral and hyperspectral images",
               "Chapter 10"],
              ["CCSDS 133.0-B", "Space Packet Protocol", "Chapter 11"],
              ["CCSDS 732.0-B", "AOS Space Data Link Protocol (transfer frames)", "Chapter 11"],
              ["CCSDS 131.0-B", "Synchronization and channel coding (Reed-Solomon, randomizer, sync marker)",
               "Chapter 12"]],
              [1.4, 4, 1], "The CCSDS standards that the HPDP uses"),
          key(["A sample is one measured number. The HPDP camera uses 12-bit samples.",
               "The raw data rate is 1000 lines × 512 pixels × 150 bands × 12 bits = 921.6 Mbit/s.",
               "At 250 MHz, one clock cycle is 4 ns. Each block moves one sample or byte in each cycle.",
               "A hyperspectral image is a cube: x (pixels), y (lines), z (bands). A scene is 256 lines.",
               "A pushbroom camera cannot stop, so the electronics must keep pace with it."]),
          check("Chapter 3: Basic ideas", [
              ("How many values can a 12-bit sample have?",
               "4096 values (0 to 4095), because 2 to the power 12 is 4096."),
              ("The clock is 250 MHz. How long is one clock cycle?",
               "4 nanoseconds (1 divided by 250,000,000)."),
              ("How many samples does one HPDP scene have?",
               "512 × 256 × 150 = 19,660,800 samples."),
              ("Why is it a problem when the electronics cannot accept a line from a pushbroom camera?",
               "The camera cannot wait. The ground moves on, so the line is lost and cannot be recorded "
               "again.")])]

    # ------------------------------------------------------------------ 4
    s += [h1("4  The hardware")]
    s += [h2("4.1  What an FPGA is"),
          p("An FPGA (field-programmable gate array) is a chip that contains a large quantity of small "
            "logic parts. You do not program it like a processor. You describe a digital circuit in a "
            "hardware description language, and a tool connects the parts of the FPGA to make that "
            "circuit. The HPDP team wrote the circuit in SystemVerilog. The tool is AMD Vivado."),
          p("The circuit in an FPGA does many things at the same time. Each block of the data chain is "
            "a separate circuit, and all blocks operate in each clock cycle. This is why an FPGA can "
            "move hundreds of millions of samples each second."),
          *tab(["Resource", "What it is", "Quantity in the XCZU7EV"], [
              ["LUT (lookup table)", "A small logic function of up to 6 inputs", "230,400"],
              ["Flip-flop (FF)", "A 1-bit memory that changes at a clock edge", "460,800"],
              ["Block RAM (BRAM)", "A 36-kilobit memory", "312"],
              ["UltraRAM (URAM)", "A 288-kilobit memory, the largest memory in the chip", "96"],
              ["DSP slice", "A fast multiplier and adder", "1,728"],
              ["GTH transceiver", "A serial transmitter and receiver for fast links", "used: 2"]],
              [1.6, 3.6, 1.6], "The main resources of the FPGA"),
          h2("4.2  The Zynq device: PS and PL"),
          p("The ZCU106 board has an AMD Zynq UltraScale+ device, type XCZU7EV. This device has two "
            "parts in one chip:"),
          bullets(["The <b>processing system (PS)</b>. It has four Arm Cortex-A53 processors and a DDR4 "
                   "memory controller. One processor runs the HPDP control program, hpdp_run.",
                   "The <b>programmable logic (PL)</b>. This is the FPGA part. It contains the full "
                   "data chain."]),
          p("The PS and the PL connect through AXI buses. AXI is a standard set of on-chip "
            "connections. The PS uses AXI to write the settings of the data chain. The DMA engines "
            "use AXI to move data between the DDR4 memory and the PL."),
          h2("4.3  The ZCU106 board"),
          fig(gf.board_diagram(), "The parts of the ZCU106 board that the HPDP uses"),
          *tab(["Part", "Use in the HPDP"], [
              ["DDR4 memory (SODIMM)", "Holds the raw scenes, the calibration tables and the received data"],
              ["SD card", "Holds raw scenes and tables for the UART console (optional)"],
              ["USB: JTAG", "Loads the FPGA configuration, the program and the test data from the PC; "
                            "reads the results back"],
              ["USB: UART", "Text console of the program, 115200 bit/s"],
              ["SFP0 cage", "The satellite transmitter: an 850 nm optical module"],
              ["SFP1 cage", "The ground receiver: a second optical module"],
              ["Si570 oscillator", "Gives the 156.25 MHz reference clock of the transceivers"]],
              [1.6, 5], "Board parts and their use"),
          h2("4.4  SFP modules and the optical fiber"),
          p("An SFP module is a small plug-in unit that changes electrical signals into light and "
            "light into electrical signals. The HPDP uses two 1000BASE-SX modules. They send infrared "
            "light with a wavelength of 850 nm."),
          p("A 1-metre OM3 multimode fiber connects SFP0 to SFP1. Thus the light goes from the "
            "satellite side to the ground side of the same board. The data goes in one direction only, "
            "like a real satellite downlink."),
          note("The 1000BASE-SX modules are Class 1 laser products, which are safe in normal use. The "
               "850 nm light is not visible. As a good practice, do not look into an open SFP port or "
               "into the end of a fiber."),
          caution("Do not bend the fiber tightly. Put the dust caps on the modules and on the fiber ends "
                  "when you disconnect them. Dirt on a fiber end causes bit errors."),
          key(["An FPGA is a chip of logic parts that you connect to make your own circuit.",
               "The Zynq device has a PS (four Arm processors) and a PL (the FPGA). The data chain is "
               "in the PL; the control program runs in the PS.",
               "The PC talks to the board through USB (JTAG and UART).",
               "Two SFP modules and a fiber make the optical link on the same board."]),
          check("Chapter 4: The hardware", [
              ("What is the difference between the PS and the PL?",
               "The PS is the processor part (four Arm A53 processors). It runs the control program. "
               "The PL is the FPGA part. It contains the data chain circuit."),
              ("Which memory type is the largest on-chip memory, and how many does the XCZU7EV have?",
               "UltraRAM (288 kilobits each). The XCZU7EV has 96."),
              ("What does an SFP module do?",
               "It changes electrical signals into light (transmitter) and light into electrical signals "
               "(receiver).")])]

    # ------------------------------------------------------------------ 5
    s += [h1("5  The data chain at a glance")]
    s += [h2("5.1  The blocks"),
          p("Figure 4 shows all blocks of the data chain. Read the first row from left to right, the "
            "second row from right to left and the third row from left to right."),
          fig(chain_diagram(), "The HPDP data chain"),
          h2("5.2  The journey of one sample"),
          p("Follow one sample through the full chain:"),
          steps(["The PC loads a raw scene into the DDR4 memory of the board.",
                 "A DMA engine reads the scene. The sensor emulator sends one line at the time of a "
                 "real camera.",
                 "The radiometric correction removes the errors of the detector from the sample.",
                 "The corner turn changes the sequence of the samples, so that all bands of one "
                 "pixel come together.",
                 "The CCSDS 123 compressor predicts the sample from its neighbours. It sends only the "
                 "small difference, with a short code.",
                 "The space packetizer puts the compressed bits into packets with a header.",
                 "The AOS framer puts the packets into frames of fixed length.",
                 "The Reed-Solomon encoder adds parity bytes to each frame. The randomizer mixes the "
                 "bits. A sync marker goes in front. The result is a CADU of 1024 bytes.",
                 "The error injector can add bit errors on purpose, to test the error correction.",
                 "The link transmitter changes each byte into a 10-bit symbol. The GTH transceiver "
                 "sends the symbols as light through the fiber at 1.25 Gbaud.",
                 "The ground receiver finds the sync markers, removes the randomization and writes "
                 "the data into the DDR4 memory.",
                 "The PC reads the data. The ground station program corrects the errors, unpacks the "
                 "frames and packets and decompresses the image.",
                 "The ground station compares the image with the original. The result must be "
                 "bit-exact."]),
          h2("5.3  The satellite side and the ground side"),
          p("In a real mission, the satellite and the ground station are hundreds of kilometres "
            "apart. In the HPDP, both are on one board. The blocks from the sensor emulator to the "
            "link transmitter are the satellite side. The blocks from the link receiver to the ground "
            "station program are the ground side. The ground side does not use any information from "
            "the satellite side, except the data that comes through the fiber. Thus the test is "
            "honest."),
          note("The ground station program on the PC does the Reed-Solomon decoding and the "
               "decompression in Python. The FPGA ground receiver only finds the frames and stores "
               "them. Chapter 16 tells why."),
          key(["The chain has three groups. The satellite processing does correction, corner turn and compression. Channel coding adds packets, frames, Reed-Solomon and sync markers. The ground has the receiver and the PC.",
               "Both sides are on one board, and only the fiber connects them."]),
          check("Chapter 5: The data chain", [
              ("Which block puts the compressed bits into packets?", "The space packetizer."),
              ("How long is one CADU?", "1024 bytes: a 4-byte sync marker and a 1020-byte codeblock."),
              ("Which program does the decompression?",
               "The ground station program on the PC (hpdp_gs.py), written in Python.")])]
    return s
