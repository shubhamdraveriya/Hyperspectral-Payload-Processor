"""HPDP Learning Guide, part B: how each block works (chapters 6 to 16)."""

from hpdp_pdf import TEXT_W, PageBreak, bullets, code, h1, h2, note, p, steps

import guide_figs as gf
from guide_common import check, example, fig, key, tab

E = lambda n: f"10<super>−{n}</super>"  # noqa: E731  ten to the power minus n


def part_b():
    s = [PageBreak(), h1("Part B  How each block works"),
         p("Part B goes through the data chain block by block, in the sequence of the data. For each "
           "block, it tells what the block does, why it is necessary, how it operates and what the "
           "tests measured.")]

    # ------------------------------------------------------------------ 6
    s += [h1("6  The data budget")]
    s += [h2("6.1  Why the budget is the start of the design"),
          p("Before the team designed a block, they calculated how much data the camera makes and how "
            "much the link can carry. This calculation is the data budget. It shows that compression is "
            "necessary, and it tells how good the compression must be."),
          h2("6.2  The calculation"),
          *tab(["Quantity", "Value", "How to calculate it"], [
              ["Raw data (12-bit samples)", "921.6 Mbit/s", "76.8 Msamples/s × 12 bits"],
              ["Link rate", "1.25 Gbaud", "symbols each second on the fiber"],
              ["Link payload after 8b/10b", "1000 Mbit/s", "1250 × 8 / 10 (Chapter 14)"],
              ["CADU efficiency", "86.3 %", "884-byte packet zone / 1024-byte CADU (Chapters 11, 12)"],
              ["Space packet efficiency", "99.3 %", "2048 data bytes / 2062-byte packet"],
              ["<b>User data capacity</b>", "<b>≈ 857 Mbit/s</b>", "1000 × 0.863 × 0.993"],
              ["Does raw data fit?", "<b>No</b>", "921.6 is more than 857"],
              ["Compressed at 2:1", "460.8 Mbit/s", "54 % of the capacity"],
              ["Compressed at 3:1", "307.2 Mbit/s", "36 % of the capacity"]],
              [2.4, 1.3, 3.2], "The data budget of the reference mission"),
          fig("link_budget.png", "Raw and compressed data rates against the link capacity"),
          p("The figure shows the measured results. The mission-size test scene needs 439 Mbit/s. The "
            "average real AVIRIS scene needs 340 Mbit/s. The worst real scene of the 63 that the team "
            "tested needs 609 Mbit/s, which is 71 % of the capacity. All of them fit."),
          h2("6.3  The proof that raw data does not fit"),
          p("The HPDP has a RAW mode. In this mode, the compressor sends the samples without "
            "compression. Requirement SYS-09 says that RAW mode must show lost lines at the mission "
            "rate. The board test confirmed it: 99.1 % of the lines were dropped, and the HPDP counted "
            "each dropped line. In COMPRESS mode at the same rate, no line was dropped in 10 minutes."),
          key(["The link carries approximately 857 Mbit/s of user data, but the camera makes 921.6 Mbit/s.",
               "Compression of approximately 2:1 or better makes the data fit with a good margin.",
               "RAW mode shows the problem on the board: almost all lines are lost."]),
          check("Chapter 6: The data budget", [
              ("Why is the user data capacity (857 Mbit/s) less than the link payload (1000 Mbit/s)?",
               "Part of each CADU is not user data: the sync marker, the Reed-Solomon parity and the frame "
               "headers (CADU efficiency 86.3 %). The packet headers take a small part more (99.3 %)."),
              ("What percentage of the link does a scene with 2:1 compression use?",
               "460.8 / 857 = approximately 54 %."),
              ("Which mode shows that raw data does not fit, and what did the board test show?",
               "RAW mode. At the mission rate, 99.1 % of the lines were dropped and counted.")])]

    # ------------------------------------------------------------------ 7
    s += [h1("7  The sensor emulator")]
    s += [h2("7.1  What it does"),
          p("The HPDP has no real camera. The sensor emulator takes the place of the camera. It reads a recorded raw scene from the DDR4 memory. It sends the scene line by line, at the time and in the sequence of a real pushbroom detector."),
          h2("7.2  How it operates"),
          steps(["A DMA engine (AXI DMA, MM2S direction) reads the scene from DDR4. The data goes into a "
                 "32 KB prefetch buffer, so that the next line is ready before it starts.",
                 "A counter on clk_sensor (100 MHz) starts a new line every LINE_PERIOD clock cycles.",
                 "At the start of a line, the emulator sends all samples of the line, one in each clock "
                 "cycle, with no gap.",
                 "It sends them in BIL order: band 0 of all pixels, then band 1 of all pixels, and so on.",
                 "It marks the first sample of each line and of each scene, so that later blocks know "
                 "where the lines and scenes start."]),
          example("the line period for the mission rate", [
              "The mission rate is 1000 lines each second, thus one line each millisecond.",
              "clk_sensor is 100 MHz, thus 1 ms = 100,000 clock cycles. Set LINE_PERIOD to 100,000.",
              "One line has 512 × 150 = 76,800 samples. The emulator needs 76,801 cycles to send it "
              "(0.768 ms).",
              "For the rest of the millisecond, the emulator waits for the next line start."]),
          h2("7.3  It never waits"),
          p("A real detector cannot wait for the electronics. Thus the emulator never waits for the "
            "next block. If the next block cannot accept a sample, the emulator drops the sample. It "
            "marks the line and counts the event. The HPDP never loses data without a record of the "
            "loss. Requirement SYS-05 calls this rule “never silent”."),
          p("If the DMA does not deliver the data in time, the emulator sends zeros and counts an "
            "underrun. An underrun is a fault of the test setup, not of the design."),
          h2("7.4  Other functions"),
          bullets(["Line and scene counters, and a 24-bit line timestamp. The timestamp at the start of "
                   "each scene goes into the packet headers (Chapter 11).",
                   "A built-in ramp and a pseudo-random (LFSR) pattern generator. They let you test the "
                   "chain without DMA."]),
          h2("7.5  Measured results"),
          p("In the M4 tests, the emulator sent up to 99.8 Msamples/s, its maximum rate. The "
            "compressor can accept 250 Msamples/s, thus the emulator, not the compressor, set the "
            "speed limit of these tests."),
          key(["The sensor emulator replays recorded scenes with the timing of a real pushbroom camera.",
               "It sends each line in BIL order, one sample each clock cycle.",
               "It never waits. Lost samples and late data are counted, never silent."]),
          check("Chapter 7: The sensor emulator", [
              ("Which value of LINE_PERIOD gives 1000 lines each second?",
               "100,000, because clk_sensor is 100 MHz and one line must start each millisecond."),
              ("What does the emulator do when the next block cannot accept a sample?",
               "It drops the sample, marks the line and counts the event. It does not wait."),
              ("What is an underrun?",
               "The DMA did not deliver the data of a line in time. The emulator sends zeros and counts "
               "the event. It is a fault of the test setup.")])]

    # ------------------------------------------------------------------ 8
    s += [h1("8  Radiometric correction")]
    s += [h2("8.1  Why a detector needs correction"),
          p("A detector has many small light sensors, one for each pixel and band. These sensors are "
            "not all equal:"),
          bullets(["<b>Dark offset.</b> A sensor gives a small value even when it receives no light.",
                   "<b>Gain.</b> Two sensors that receive the same light can give different values, "
                   "because their sensitivity is different.",
                   "<b>Bad pixels.</b> Some sensors are dead (always low) or hot (always high)."]),
          p("If you do not correct these errors, the image has stripes, and the compressor works less "
            "well. The correction uses a calibration table. It has three values for each band and pixel: dark (16 bits), gain (16 bits) and a bad-pixel flag (1 bit)."),
          h2("8.2  The formula"),
          p("For a raw sample x, the block calculates the corrected value c:"),
          code(["c = clamp( ((x − dark) × gain + 2^13) >> 14 ,  0 ,  2^D − 1 )"]),
          bullets(["The gain is a fixed-point number with 14 fraction bits. 0x4000 (16384) means a "
                   "gain of 1.0.",
                   "Adding 2^13 (8192) before the shift by 14 bits rounds the result to the nearest "
                   "integer.",
                   "Clamp keeps the result between 0 and the maximum value (4095 for D = 12). The block "
                   "counts each clamp.",
                   "One DSP slice does the multiplication and the addition in one step."]),
          example("one corrected sample", [
              "Raw sample x = 1000, dark = 100, gain = 0x4800 = 18432 (this is 1.125).",
              "(1000 − 100) × 18432 = 16,588,800.",
              "16,588,800 + 8192 = 16,596,992.",
              "16,596,992 >> 14 = 1013. Check: 900 × 1.125 = 1012.5, and this rounds up to 1013.",
              "1013 is between 0 and 4095, thus c = 1013."]),
          h2("8.3  Bad-pixel replacement"),
          p("When the flag of a pixel shows “bad”, the block replaces the value. It uses the "
            "neighbours in the same band and the same line:"),
          *tab(["Condition", "New value"], [
              ["The pixel is good", "its own corrected value c[x]"],
              ["Both neighbours are good", "the average: (c[x−1] + c[x+1] + 1) >> 1"],
              ["Only the left neighbour is good", "c[x−1]"],
              ["Only the right neighbour is good", "c[x+1]"],
              ["No neighbour is good", "0, and the block counts the event"]],
              [2.5, 3.5], "Bad-pixel replacement rules"),
          example("a bad pixel", ["The left neighbour is 1010 and the right neighbour is 1020. Both are good.",
                                  "New value = (1010 + 1020 + 1) >> 1 = 2031 >> 1 = 1015."]),
          p("The data comes in BIL order. Thus the left and right neighbours in a band are next to the "
            "pixel in time. The block needs only a window of three samples. This is a good reason for "
            "the correction to come before the corner turn."),
          h2("8.4  Where the table is"),
          p("The calibration table is in UltraRAM. A DMA transfer loads it before a run. The table for "
            "512 pixels and 256 bands uses 32 UltraRAMs, with error correction (ECC, Chapter 20). For "
            "the 1024-pixel build, the table packs two entries into each memory word, so that it fits "
            "(Chapter 22)."),
          note("The public hyperspectral images that the team used for tests are already corrected. To "
               "test this block, the team applied synthetic dark, gain and bad-pixel maps in reverse. "
               "This made “raw” data with a known correct answer."),
          key(["The correction removes dark offset and gain differences and replaces bad pixels.",
               "c = clamp(((x − dark) × gain + 2^13) >> 14, 0, 2^D − 1); 0x4000 means a gain of 1.0.",
               "A bad pixel gets the average of its good neighbours in the same band and line."]),
          check("Chapter 8: Radiometric correction", [
              ("A sensor gives a value when there is no light. What is the name of this error?", "Dark offset."),
              ("Which gain value in the table means “no change”?", "0x4000 (16384), which is 1.0."),
              ("x = 500, dark = 100, gain = 0x2000. What is c?",
               "0x2000 is 0.5. (500 − 100) × 8192 = 3,276,800; + 8192 = 3,284,992; >> 14 = 200. "
               "Check: 400 × 0.5 = 200."),
              ("A bad pixel has a bad left neighbour and a good right neighbour (value 700). What is the "
               "new value?", "700, the value of the right neighbour.")])]

    # ------------------------------------------------------------------ 9
    s += [h1("9  Corner turn and scene guard")]
    s += [h2("9.1  Why the sequence must change"),
          p("The sensor sends samples in BIL order (band-interleaved by line): all pixels of band 0, "
            "then all pixels of band 1. The compressor needs BIP order (band-interleaved by pixel): all "
            "bands of pixel 0, then all bands of pixel 1. Section 10.7 tells why. The corner turn makes "
            "this change."),
          fig(gf.bil_bip(), "BIL and BIP order for 3 pixels and 3 bands"),
          h2("9.2  How it operates"),
          p("The corner turn has two line buffers in UltraRAM. Each buffer holds one full line "
            "(512 pixels × 150 bands). It writes line n into one buffer and at the same time reads "
            "line n−1 from the other buffer in the new sequence. At the end of each line, the two "
            "buffers change roles. This method is “ping-pong” buffering. Both sides move one sample in "
            "each clock cycle. The delay is approximately one line."),
          h2("9.3  When the compressor is slow: fill lines"),
          p("The compressor can stop the corner turn for a short time (back-pressure). Sometimes both buffers are still full when the next line starts. Then the corner turn drops the new line, counts it and sends a line of zeros in its place. This line of zeros is a <b>fill line</b>."),
          p("Why send zeros, and not just drop the line? Each scene must have exactly 256 lines. If a "
            "line is missing, all the following lines move up by one, and the compressor loses the "
            "boundaries of the scenes. A fill line keeps the geometry correct. The packet header of "
            "the scene then carries the flag “lines dropped”."),
          h2("9.4  The scene guard"),
          p("The team found a second problem in the M5 tests, under a very high overload. Sometimes "
            "even a fill line could not go out. Then a scene arrived short, the compressor did not "
            "end it, and the next scenes merged into it. Only 13 of 234 scenes were complete."),
          p("The scene guard solves this problem. It sits between the corner turn and the compressor. "
            "Sometimes a new scene starts before the current scene is complete. Then the scene guard holds the input and adds zeros until the current scene is complete. The compressor then ends the "
            "scene normally. After this change, all 218 scenes that reached the compressor were "
            "delivered."),
          key(["The corner turn changes BIL order into BIP order with two ping-pong line buffers.",
               "If the compressor is too slow, a dropped line becomes a counted fill line of zeros.",
               "The scene guard makes sure that each scene that the compressor sees has its full size."]),
          check("Chapter 9: Corner turn", [
              ("In BIP order, which sample comes after pixel 0, band 0?", "Pixel 0, band 1."),
              ("Why does the corner turn need two line buffers?",
               "It must write a new line while it reads the previous line in a different sequence. One "
               "buffer receives while the other buffer sends."),
              ("Why does the HPDP send a fill line in place of a dropped line?",
               "Each scene must keep its 256 lines. Without a fill line, the following lines move and "
               "the compressor loses the scene boundaries.")])]

    # ------------------------------------------------------------------ 10
    s += [h1("10  CCSDS 123 compression")]
    s += [h2("10.1  Lossless compression"),
          p("Compression makes the data smaller. <b>Lossless</b> compression lets the receiver rebuild "
            "the data exactly. Scientists need the exact values, so the HPDP uses lossless compression. "
            "The method is the CCSDS 123.0-B-2 standard. It is made for multispectral and "
            "hyperspectral images, and it is simple enough for space hardware."),
          h2("10.2  The idea: predict, then send the error"),
          p("Neighbouring samples are similar. A pixel is similar to the pixels next to it. Much more "
            "important, the value of a pixel in one band is very similar to its value in the band "
            "before. The compressor uses this fact:"),
          steps(["It predicts the value of the next sample from samples that it has already sent.",
                 "It calculates the residual: actual value minus predicted value.",
                 "It sends only the residual. A good prediction gives a small residual, and a small "
                 "number needs fewer bits."]),
          p("The decoder on the ground makes the same prediction from the same samples, because it has "
            "already decoded them. It adds the residual and gets the exact value back. Thus no "
            "information is lost."),
          h2("10.3  The neighbourhood"),
          fig(gf.neighbourhood(), "The samples that the predictor uses, and the steps after the prediction"),
          bullets(["In the current band, the predictor uses the neighbours W (west), NW, N (north) and "
                   "NE. They make the <b>local sum</b>.",
                   "It also uses up to P = 15 earlier bands of the same pixel. From them, it takes the <b>local differences</b>: how each band differed from its own local sum.",
                   "The prediction is the local sum plus a weighted sum of the local differences."]),
          h2("10.4  Weights that learn"),
          p("Each band has its own set of weights. After each sample, the predictor compares its "
            "prediction with the actual value. It then changes the weights a small step in the "
            "direction that makes the error smaller. The weights thus adapt to the image while the "
            "compression continues. The parameters of this adaptation are the weight resolution "
            "(Ω = 13 by default) and the update scaling values (ν<sub>min</sub>, ν<sub>max</sub>, "
            "t<sub>inc</sub>)."),
          h2("10.5  The mapper and the entropy coder"),
          p("A residual can be positive or negative. The <b>mapper</b> changes it into a number that is "
            "zero or more. Small residuals become small numbers."),
          p("The <b>entropy coder</b> then writes each number with a variable number of bits. The HPDP "
            "uses the sample-adaptive coder of the standard. For each band, it keeps a running average "
            "of the recent numbers. From this average, it selects a code parameter k:"),
          bullets(["The code is: (number >> k) zeros, then a one, then the k lowest bits of the number.",
                   "A band with small residuals gets a small k, thus short codes.",
                   "A band with large residuals gets a larger k, so that the codes do not become too long. "
                   "The limit U<sub>max</sub> (18 by default) stops very long codes."]),
          example("one sample", [
              "The neighbours and the earlier bands give a predicted value of 1843. The actual value is "
              "1846.",
              "Residual = 1846 − 1843 = +3. The mapper changes it into a small number, for example 6.",
              "With k = 2: 6 >> 2 = 1, thus one zero, then a one, then the 2 low bits of 6 (binary 10).",
              "Code = 0 1 10, which is 4 bits in place of 12 bits."]),
          p("A bit packer joins all codes into bytes. In front of each scene, the compressor writes a "
            "header. The header gives the image size and the settings, so that the decoder can start."),
          h2("10.6  One scene, one image"),
          p("The compressor starts each scene with new weights, new coder statistics and a full header. "
            "Thus each scene can be decoded alone. If the link destroys a part of one scene, the next "
            "scene is not affected (requirement SYS-06)."),
          h2("10.7  Why BIP order makes 250 MHz possible"),
          p("This is the most important design idea of the compressor. The weights and the coder "
            "statistics of a band change after each sample of that band. In BIP order, the next sample "
            "of the same band comes Nz clock cycles later (150 cycles for the mission). Thus the "
            "circuit has Nz cycles to finish the update."),
          p("In lossless mode, the predictor uses the original samples, not values that it calculated "
            "itself. Thus there is no loop from one sample to the next. The only loops are the "
            "per-band weights and statistics, and they have Nz cycles of time. The team could thus "
            "make a deep pipeline of approximately 25 stages, plus the packer:"),
          *tab(["Stages", "Function"], [
              ["S1 – S3", "Neighbourhood: read the line buffer (UltraRAM) and the delay line"],
              ["S4 – S5", "Local sums and local differences"],
              ["P1 – P15", "Predictor and weight update"],
              ["C0 – C4", "Mapper and sample-adaptive coder"],
              ["Packer", "Codes into bytes, through a 32-entry codeword buffer"]],
              [1.2, 5], "The pipeline of the compressor"),
          p("The pipeline depth sets the minimum number of bands. A band must not come back before its "
            "previous update is complete. Thus the HPDP needs at least 16 bands (NZ_MIN = 16). When the "
            "next block applies back-pressure, one enable signal stops the full pipeline. No data is "
            "lost."),
          p("The compressor uses 13.6 k LUTs, 6.2 k flip-flops, 19 DSP slices, 5.5 block RAMs and "
            "8 UltraRAMs, and it meets 250 MHz. The team packed four samples into each UltraRAM word of "
            "the line buffer. This change reduced the line buffer from 32 to 8 UltraRAMs and also made "
            "the timing better."),
          h2("10.8  Results"),
          fig("compression.png", "Compression of 63 real hyperspectral scenes"),
          *tab(["Data", "Scenes", "Bits per sample, mean (range)", "Ratio", "Link use, mean (worst)"], [
              ["AVIRIS (airborne), 512 × 512 × 224", "49", "4.43 (2.99 – 7.93)", "3.12:1", "40 % (71 %)"],
              ["Hyperion (EO-1 satellite), 256 × 1024 × 242", "13", "4.95 (4.74 – 5.16)", "2.86:1",
               "44 % (46 %)"],
              ["Indian Pines (AVIRIS), 145 × 145 × 220", "1", "6.01", "2.33:1", "54 %"],
              ["Synthetic ramp pattern, D = 10", "–", "1.20", "8.30:1", "–"],
              ["Random (LFSR) pattern, D = 14", "–", "14.50", "0.97:1", "–"]],
              [3.2, 0.8, 2, 0.9, 1.6], "Measured compression results"),
          bullets(["All 63 real scenes were lossless: the decoder rebuilt each one exactly.",
                   "CCSDS 123 needs 1.6 to 2.0 times fewer bits than ZIP (deflate) on the same data.",
                   "Without the earlier bands (P = 0), the scenes need 12 % to 67 % more bits. The "
                   "spectral prediction is the most important part.",
                   "Random data cannot be compressed. The LFSR pattern became 3 % larger, because the "
                   "coder adds a small overhead. This is normal for all lossless methods."]),
          h2("10.9  Supported settings"),
          p("All settings come from registers. You do not have to build the FPGA again to change them. "
            "The team tested all 864 combinations of the main settings (Chapter 21)."),
          *tab(["Setting", "Range in the HPDP", "Default"], [
              ["Sample depth D", "2 to 16 bits", "12"],
              ["Earlier bands for prediction P", "0 to 15", "3"],
              ["Prediction mode", "full or reduced", "full"],
              ["Local sum type", "wide or narrow, neighbour- or column-oriented", "wide, neighbour"],
              ["Weight resolution Ω", "4 to 19", "13"],
              ["Code length limit U<sub>max</sub>", "8 to 32", "18"],
              ["Image size", "Nx up to 512 (1024 build), Nz 16 to 256", "512 × 256 × 150"]],
              [2.4, 3, 1.4], "Compressor settings"),
          key(["The compressor predicts each sample from neighbours and earlier bands and sends only the "
               "residual, with a short adaptive code.",
               "The ground makes the same prediction, thus the result is lossless.",
               "BIP order gives each band Nz cycles to update its state. Thus the pipeline can be deep and fast: 1 sample each clock at 250 MHz.",
               "Real scenes compress 2.3:1 to 3.1:1 on average; all 63 were lossless."]),
          check("Chapter 10: CCSDS 123", [
              ("Why can the decoder make the same prediction as the encoder?",
               "The prediction uses only samples that come before the current sample. The decoder has "
               "already decoded these samples exactly."),
              ("Which neighbours does the predictor use in the current band?", "W, NW, N and NE."),
              ("Why does BIP order help the hardware to run at 250 MHz?",
               "The state of a band is necessary again only Nz cycles later. The circuit has Nz cycles to "
               "update it, so the pipeline can be deep without hazards."),
              ("Write the code for the number 9 with k = 2.",
               "9 >> 2 = 2, thus two zeros and then a one. Then come the 2 low bits of 9 (binary 01): 0 0 1 01, "
               "which is 5 bits."),
              ("What happens when you set P = 0?",
               "The predictor does not use earlier bands. Real scenes then need 12 % to 67 % more bits.")])]

    # ------------------------------------------------------------------ 11
    s += [h1("11  Packets and frames")]
    s += [h2("11.1  Why the data needs labels"),
          p("The compressed scene is a long sequence of bits. The ground station must know which scene "
            "a part belongs to, in which sequence the parts come and if a part is missing. Two "
            "layers of labels give this information: Space Packets and AOS transfer frames."),
          fig(gf.nesting(), "From packets to CADUs: each layer goes inside the next layer (not to scale)"),
          h2("11.2  Space Packets (CCSDS 133.0)"),
          p("The space packetizer cuts the compressed scene into packets of 2048 data bytes. The last "
            "packet of a scene can be shorter. Each packet has a 6-byte primary header and an 8-byte "
            "secondary header:"),
          *tab(["Field", "Size", "Contents"], [
              ["Version, type, flags", "5 bits", "telemetry packet with a secondary header"],
              ["APID", "11 bits", "the source of the data (0x100 by default)"],
              ["Sequence flags", "2 bits", "first packet of a scene, middle, last, or the full scene in one packet"],
              ["Sequence count", "14 bits", "increases by one for each packet"],
              ["Data length", "16 bits", "length of the packet data, minus 1"],
              ["Scene ID", "16 bits", "increases by one for each scene"],
              ["Packet index", "16 bits", "0, 1, 2, ... inside the scene"],
              ["Scene timestamp", "24 bits", "line timestamp at the start of the scene (Chapter 7)"],
              ["Flags", "8 bits", "uncorrectable internal error, lines dropped, RAW mode"]],
              [1.6, 0.8, 4], "Space Packet header (primary and secondary)"),
          h2("11.3  AOS transfer frames (CCSDS 732.0)"),
          p("The AOS framer puts the packets into frames of a fixed length: 892 bytes. Why 892? It is "
            "223 × 4. One frame then fills exactly one Reed-Solomon codeblock (Chapter 12). Each frame "
            "has:"),
          bullets(["A 6-byte frame header: version, spacecraft ID, virtual channel ID (VCID) and a frame "
                   "counter for each virtual channel.",
                   "A 2-byte M_PDU header with the <b>first header pointer</b> (FHP).",
                   "A packet zone of 884 bytes."]),
          p("A packet does not have to fit in one frame. A long packet continues in the next frame. "
            "The first header pointer tells where the first new packet starts in the packet zone. If a "
            "frame is lost, the ground station uses the pointer of the next frame to find the next "
            "complete packet. If no packet starts in a frame, the pointer is 0x7FF."),
          example("the first header pointer", [
              "A frame starts with the last 190 bytes of a packet that began in the previous frame.",
              "The next packet header thus starts at byte 190 of the packet zone.",
              "The FHP of this frame is 190."]),
          h2("11.4  Idle frames"),
          p("A real downlink must never stop, because the receiver must stay synchronized. When no "
            "science data is ready, the framer sends an idle frame on virtual channel 63. The ground "
            "station removes the idle frames."),
          p("The ground station identifies idle frames by the bit distance of the VCID field to 63, "
            "not by an exact match. At a bit error rate of 1 in 1000, an exact match would let "
            "approximately 0.6 % of the idle frames through as false data."),
          p("The framer can also limit the rate of science frames (a register). This lets you make a "
            "slower link for tests, with the same hardware."),
          key(["Space Packets (2048 data bytes and a 14-byte header) label each part of a scene.",
               "AOS frames have a fixed 892 bytes, so that one frame fills one Reed-Solomon codeblock.",
               "The first header pointer lets the ground find packet starts, also after a lost frame.",
               "Idle frames (VCID 63) keep the link full when there is no science data."]),
          check("Chapter 11: Packets and frames", [
              ("Why is the AOS frame 892 bytes long?",
               "892 = 223 × 4. One frame fills one Reed-Solomon codeblock of four interleaved codewords, "
               "each with 223 data bytes."),
              ("What does the first header pointer value 0x7FF mean?", "No packet starts in this frame."),
              ("Why does the framer send idle frames?",
               "The downlink must never stop, so that the receiver stays synchronized. Idle frames fill "
               "the time when no science data is ready.")])]

    # ------------------------------------------------------------------ 12
    s += [h1("12  Channel coding")]
    s += [p("Channel coding prepares each frame for a noisy channel. It has three steps: Reed-Solomon "
            "coding, randomization and the sync marker (CCSDS 131.0)."),
          h2("12.1  Reed-Solomon code"),
          p("A Reed-Solomon (RS) code adds parity bytes to the data. The HPDP uses RS(255,223): each "
            "codeword has 223 data bytes and 32 parity bytes, total 255 bytes. The decoder on the "
            "ground can correct up to 16 bad bytes in each codeword, at any positions. If more than "
            "16 bytes are bad, the decoder usually reports “uncorrectable”."),
          p("RS works with bytes, not bits. A byte with 1 bad bit and a byte with 8 bad bits are both "
            "one error. This makes RS strong against short groups of bad bits."),
          h2("12.2  Interleaving"),
          p("The HPDP uses interleaving depth I = 4. It divides each frame of 892 bytes into four "
            "codewords. Byte 1 goes to codeword 1, byte 2 to codeword 2, byte 3 to codeword 3 and byte 4 "
            "to codeword 4. Byte 5 goes to codeword 1 again, and so on."),
          fig(gf.interleave(), "Interleaving spreads a burst of errors over four codewords"),
          p("A burst of bad bytes in a row thus goes into four codewords. Each codeword can correct "
            "16 bytes, so the HPDP can correct a burst of up to 4 × 16 = 64 bytes in a row. The four "
            "codewords with their parity make one codeblock of 4 × 255 = 1020 bytes."),
          h2("12.3  Randomizer"),
          p("The randomizer adds (XOR) a fixed pseudo-random sequence to the codeblock. The sequence "
            "starts with the bytes FF 48 0E C0 and repeats after 255 bytes. It starts again at each "
            "codeblock."),
          p("Why is this necessary? Some data has long runs of the same bit, for example an idle frame "
            "full of zeros. A receiver needs many changes between 0 and 1 to keep its clock. After the "
            "randomizer, the bits look random, and they have many changes. The ground does the same XOR "
            "again, which gives the original data back (XOR twice with the same value cancels)."),
          h2("12.4  The sync marker and the CADU"),
          p("In front of each randomized codeblock, the HPDP puts the 4-byte attached sync marker (ASM): "
            "1A CF FC 1D. The ASM is not randomized. The ground searches the stream for this pattern to "
            "find the start of each block. The result is the CADU (channel access data unit): "
            "4 + 1020 = 1024 bytes."),
          note("An advanced fact: an error pattern that repeats with a period that divides 255 is itself "
               "a valid RS codeword. The RS decoder then sees no error. In the HPDP, the frame header "
               "checks of the ground station catch such cases."),
          key(["RS(255,223) adds 32 parity bytes to 223 data bytes and corrects up to 16 bad bytes.",
               "Interleaving depth 4 lets the HPDP correct a burst of up to 64 bad bytes in a row.",
               "The randomizer gives the bit stream enough changes; the ground removes it with the same XOR.",
               "A CADU is the 4-byte sync marker 1ACFFC1D plus the 1020-byte codeblock."]),
          check("Chapter 12: Channel coding", [
              ("How many bad bytes can RS(255,223) correct in one codeword?", "16."),
              ("Why does interleaving help against a burst of errors?",
               "Consecutive bytes go to different codewords. A burst thus divides over four codewords, and "
               "each codeword gets only a quarter of the bad bytes."),
              ("Why is the sync marker not randomized?",
               "The ground must find it in the raw stream before it can remove the randomization. A fixed, "
               "known pattern is necessary for the search.")])]

    # ------------------------------------------------------------------ 13
    s += [h1("13  The error injector")]
    s += [h2("13.1  Why add errors on purpose"),
          p("The optical fiber on the desk is almost perfect: the measured bit error rate is less than "
            "1 in 10<super>12</super>. A real radio link has many more errors. To prove that the error "
            "correction works, the HPDP must have errors. The error injector adds them on purpose, "
            "between the channel coding and the link."),
          h2("13.2  Modes"),
          *tab(["Mode", "What it does"], [
              ["Off", "No change to the data"],
              ["Random", f"Changes random bits with a set bit error rate (BER), resolution 2<super>−32</super>"],
              ["Burst", "Changes L bits in a row every N bytes"],
              ["ASM", "Changes k bits in each sync marker, to test the frame synchronizer"]],
              [1, 5], "Error injector modes"),
          p("The start value (seed) of the random generator is a register, so a test can be repeated "
            "exactly. The injector counts each bit that it changes. This count is the true number of "
            "errors, and the tests compare the ground results with it."),
          h2("13.3  Results: how far the code holds"),
          *tab(["Injected BER", "Result at the ground (4 scenes)"], [
              ["0", "4 of 4 scenes bit-exact"],
              [E(4), "1432 RS symbols corrected; 4 of 4 bit-exact"],
              [E(3), "14,508 RS symbols corrected; 4 of 4 bit-exact"],
              [f"2 × {E(3)}", "4 of 4 bit-exact"],
              [f"3 × {E(3)}", "1 uncorrectable codeblock; 3 of 4 bit-exact (only the scene with the bad "
                                    "codeblock is lost)"],
              [f"4 × {E(3)} and more", "0 of 4: scenes lost"]],
              [1.6, 5], "Bit error rate sweep over the fiber"),
          p("The RS code thus holds up to approximately 2 errors in 1000 bits. At 3 in 1000, one "
            "codeblock fails, and only its own scene is lost, as requirement SYS-06 requires."),
          key(["The error injector makes a realistic noisy channel on a perfect fiber.",
               "It counts the errors it adds, so the tests can compare against the true number.",
               f"The RS code corrected all errors up to a BER of 2 × {E(3)}."]),
          check("Chapter 13: The error injector", [
              ("Why is a programmable seed useful?",
               "The same seed gives the same error pattern, so a failed test can be repeated exactly."),
              (f"What happened at a BER of 3 × {E(3)}?",
               "One codeblock was uncorrectable. Only the scene that contained it was lost; the other three "
               "scenes were bit-exact.")])]

    # ------------------------------------------------------------------ 14
    s += [h1("14  The optical link")]
    s += [h2("14.1  The problems of a serial line"),
          p("The fiber carries one bit after the other. There is no separate clock wire. The receiver "
            "must solve three problems:"),
          bullets(["It must find the clock in the data. It can only do this if the data changes often "
                   "between 0 and 1.",
                   "The average signal must stay in the middle (DC balance), because the optical "
                   "receiver cannot pass a constant level for a long time.",
                   "It must find where each byte starts."]),
          h2("14.2  The 8b/10b code"),
          p("The 8b/10b code solves all three problems. It changes each 8-bit byte into a 10-bit "
            "symbol. Of the 1024 possible 10-bit patterns, it uses only the patterns that have good "
            "properties:"),
          bullets(["A symbol has five ones and five zeros, or six and four. A running disparity counter "
                   "selects between two forms of each symbol, so the totals stay equal over time.",
                   "There are never more than five equal bits in a row.",
                   "Some extra symbols are control characters (K characters). K28.5 contains a "
                   "<b>comma</b>: a bit pattern that no sequence of data symbols can make. The receiver "
                   "uses it to find the start of each symbol."]),
          fig(gf.b8b10(), "A data byte and the K28.5 comma as 10-bit symbols"),
          h2("14.3  The link transmitter"),
          bullets(["It sends the CADU bytes as data characters, two bytes in each 16-bit word.",
                   "When it has no data, it sends idle words: K28.5 followed by D16.2.",
                   "At least every 2048 words (register CC_INTERVAL), it sends an idle word for clock "
                   "correction (Section 14.5), also when data waits."]),
          h2("14.4  The GTH transceiver and the SFP module"),
          p("The GTH transceiver is a fixed block in the FPGA. It does the 8b/10b coding and the "
            "serialization:"),
          example("the link rate", [
              "The link logic gives the GTH one 16-bit word each cycle of clk_link (62.5 MHz).",
              "After 8b/10b, a 16-bit word is 20 bits. 20 bits × 62.5 MHz = 1.25 Gbaud.",
              "One bit on the fiber thus lasts 800 picoseconds.",
              "User payload: 16 bits × 62.5 MHz = 1000 Mbit/s. After the clock-correction words, at "
              "least 999 Mbit/s are available for CADUs."]),
          p("The GTH gets its reference clock (156.25 MHz) from the Si570 oscillator on the board. The "
            "team measured it at 156,250,064 Hz. The SFP module changes the electrical bits into light "
            "pulses from an 850 nm laser. The PL drives the TX_DISABLE_B pins of the SFP cages high to "
            "switch the transmitters on."),
          h2("14.5  The link receiver"),
          fig(gf.link_rx(), "The steps of the link receiver"),
          steps(["SFP1 changes the light back into an electrical signal.",
                 "The clock and data recovery (CDR) circuit of the GTH finds the bit clock from the "
                 "changes in the signal.",
                 "The deserializer makes 20-bit words. Comma alignment moves the word boundary until "
                 "K28.5 is in byte 0.",
                 "The 8b/10b decoder changes each symbol back into a byte. It reports a code error if a "
                 "symbol is not in the table or if the disparity is wrong.",
                 "The elastic buffer absorbs small differences between the recovered clock and the local "
                 "clock. It removes or adds idle words to do this. This is clock correction.",
                 "The link receiver removes all K characters. Only the CADU bytes go on."]),
          h2("14.6  The lock state machine"),
          p("The receiver passes data on only when it trusts the link. Its lock state machine declares "
            "LOCKED after 1024 words in a row with no code error. It loses lock if more than 8 code "
            "errors occur in 1024 words. It counts the code errors and the losses of lock."),
          h2("14.7  The PRBS test"),
          p("To measure the quality of the fiber link alone, the GTH has a PRBS-31 generator and "
            "checker. PRBS-31 is a pseudo-random bit sequence that repeats only after 2<super>31</super>−1 "
            "bits. The checker knows the sequence, so it can count each wrong bit."),
          example("what “zero errors” proves", [
              "Test VER-06 ran PRBS-31 for 2700 seconds: 3.375 × 10<super>12</super> bits with 0 errors.",
              "With 0 errors in N bits, the BER is less than 3 / N with 95 % confidence.",
              f"3 / (3.375 × 10<super>12</super>) = 8.9 × {E(13)}. Thus BER < 8.9 × {E(13)}."]),
          h2("14.8  The eye scan"),
          p("An eye diagram shows many bits on top of each other. The open area in the middle is the "
            "“eye”. A large open eye means that the receiver can sample each bit with a good margin "
            "for noise and time errors. The In-System IBERT tool moves the sample point in time "
            "(horizontal) and in voltage (vertical) and measures the error rate at each point."),
          fig("eye_scans.png", "Measured eye scans in both directions of the fiber", width=0.78 * TEXT_W),
          p("From SFP0 to SFP1, the eye is open over 77.5 % of the bit time and 96.8 % of the voltage "
            "range. In the other direction, it is 77.5 % and 90.5 %. The fiber cable has two fibers, so "
            "both directions work."),
          key(["8b/10b gives clock changes, DC balance and the K28.5 comma for word alignment.",
               "20 bits × 62.5 MHz = 1.25 Gbaud; the payload is 1000 Mbit/s.",
               "The receiver recovers the clock, aligns on the comma, decodes, does clock correction and "
               "locks after 1024 clean words.",
               f"PRBS-31: 3.375 × 10<super>12</super> bits, 0 errors, BER < 8.9 × {E(13)}. The eye is wide open."]),
          check("Chapter 14: The optical link", [
              ("Why does 8b/10b limit the number of equal bits in a row?",
               "The receiver finds the clock from the changes between 0 and 1. Long runs with no change "
               "would let the recovered clock drift."),
              ("What is a comma, and why is it necessary?",
               "A bit pattern (in K28.5) that data symbols never make. The receiver uses it to find where "
               "each symbol and word starts."),
              ("When does the lock state machine declare LOCKED?",
               "After 1024 words in a row with no code error."),
              ("A PRBS test runs 10<super>12</super> bits with 0 errors. What can you say about the BER?",
               f"BER < 3 / 10<super>12</super> = 3 × {E(12)}, with 95 % confidence.")])]

    # ------------------------------------------------------------------ 15
    s += [h1("15  The ground receiver")]
    s += [h2("15.1  Frame synchronization"),
          p("The link receiver gives a stream of bytes. The frame synchronizer must find the CADUs in "
            "this stream. It compares the stream with the sync marker 1ACFFC1D. It accepts a match with "
            "up to 2 wrong bits (register ASM_TOL), because the channel can damage the marker too."),
          fig(gf.sync_fsm(), "The states of the frame synchronizer"),
          bullets(["<b>SEARCH:</b> look for the marker at each byte position.",
                   "<b>VERIFY:</b> a marker was found. Check that the next marker is exactly 1024 bytes "
                   "later. A random match in the data does not pass this check.",
                   "<b>LOCK:</b> the synchronizer knows where each CADU starts.",
                   "<b>FLYWHEEL:</b> one marker is missing, maybe because of errors. The synchronizer "
                   "continues at the expected positions. After more than 3 missing markers in a row "
                   "(register FLYWHEEL), it goes back to SEARCH."]),
          h2("15.2  Derandomizer and capture"),
          p("The derandomizer does the same XOR as the randomizer, which gives the original codeblock. "
            "The capture block then adds a 4-byte status word to each 1020-byte codeblock. A DMA "
            "engine (S2MM direction) writes these 1024-byte records into a ring buffer in DDR4."),
          *tab(["Bits", "Field of the status word"], [
              ["5 – 0", "Number of wrong bits in the sync marker"],
              ["7 – 6", "Synchronizer state: 0 SEARCH, 1 VERIFY, 2 LOCK, 3 FLYWHEEL"],
              ["15 – 8", "Reserved"],
              ["31 – 16", "CADU sequence number"]],
              [1, 5], "The capture status word"),
          p("The ground receiver counts the CADUs, the losses of synchronization and the bit errors in "
            "the markers. It does not decode the Reed-Solomon code. The PC does that (Chapter 16). A "
            "hardware RS decoder was an optional item, and the team did not make it."),
          key(["The frame synchronizer finds the sync markers with up to 2 wrong bits and locks after "
               "a second marker 1024 bytes later.",
               "The flywheel keeps the lock through up to 3 missing markers.",
               "Each received codeblock goes to DDR4 as a 1024-byte record with a status word."]),
          check("Chapter 15: The ground receiver", [
              ("Why does the synchronizer need the VERIFY state?",
               "The 4-byte pattern can occur by chance in the data. A second marker exactly 1024 bytes "
               "later confirms that the first one was a real marker."),
              ("What does the flywheel do?",
               "It keeps the lock when a marker is damaged, and it continues at the expected positions "
               "for up to 3 missing markers.")])]

    # ------------------------------------------------------------------ 16
    s += [h1("16  The PC ground station")]
    s += [h2("16.1  What it does"),
          p("The ground station is the Python program sw/ground/hpdp_gs.py. It reads the received "
            "records and gets the image back. It does these steps:"),
          steps(["Reed-Solomon decoding of each codeword, with the dual-basis conversion of the CCSDS "
                 "standard. It corrects the errors and counts the corrections.",
                 "Frame checks: version, spacecraft ID and the frame counters. A gap in a counter shows a "
                 "lost frame.",
                 "Removal of the idle frames.",
                 "Packet extraction with the first header pointer.",
                 "Scene assembly from the sequence flags and counters. A gap shows a lost packet.",
                 "CCSDS 123 decompression of each scene.",
                 "Comparison with the corrected scene of the Python model: pass or fail for each scene.",
                 "Display: a false-colour image from three bands, a single band, the spectrum of a "
                 "pixel and the statistics."]),
          h2("16.2  Commands"),
          *tab(["Command", "Use"], [
              ["decode", "Decodes a capture file or a TCP stream. Options: --ref (reference for the comparison), "
                         "--report, --out, --png, --crosscheck, --show"],
              ["view", "Shows decoded scenes"],
              ["serve", "Sends a capture file over TCP, to test the live input"]],
              [1, 5], "Ground station commands"),
          h2("16.3  How the data gets to the PC"),
          p("In the board tests, the host script reads the records from the DDR4 memory through JTAG. "
            "The specification also asks for an upload over Ethernet (requirement SW-05). The board program has this function, and it builds. The team did not test it on the board, because the PC had no free Ethernet port. The project records this as a deviation."),
          h2("16.4  Why the decoding is on the PC"),
          p("The ground side of a real mission has large computers. Thus the team put the RS decoding "
            "and the decompression in Python, where they are easy to check. The RS decoder agreed with "
            "an independent library (reedsolo) on all 7088 codewords of a test. The cost is speed: "
            "approximately 3.4 ms for each codeblock and 2.5 s for each 128 × 64 × 64 scene."),
          note("The reference for the comparison is the model's correction of the raw scene, not the clean "
               "scene before the reverse calibration. The correction rounds values, so only the model "
               "output is a bit-exact reference."),
          key(["The ground station corrects errors, checks frames, extracts packets, rebuilds scenes, "
               "decompresses and compares bit for bit.",
               "In the board tests, the records come to the PC through JTAG; the Ethernet upload is "
               "a documented deviation."]),
          check("Chapter 16: The ground station", [
              ("How does the ground station find a lost frame?",
               "The frame counter of the virtual channel has a gap."),
              ("Why is the model's corrected scene the reference, and not the clean scene?",
               "The correction rounds and clamps values. Only the output of the bit-exact model is "
               "identical to what the hardware makes.")])]
    return s
