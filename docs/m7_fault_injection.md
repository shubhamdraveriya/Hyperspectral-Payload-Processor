# M7 fault-injection report (RAD-07)

Status (2026-10-13): **done.**

- Part (a), simulation: 1 300 trials on the final design, build **m7h**, commit b506d30.
- Part (b), hardware: ECC injection on m7c and m7g; 251 SEM configuration upsets on m7g.

SPEC RAD-07 asks for:

- (a) random bit flips in registers and memories in simulation, each outcome classified as
  masked, corrected, detected or silent corruption;
- (b) SEM IP configuration upsets and ECC error injection on hardware;
- results written up with coverage figures.

## Summary

Simulation, final design (m7h):

| Fault class | Trials | Masked | Corrected | Detected | Silent |
|---|---:|---:|---:|---:|---:|
| Protected: ECC memories, TMR memories, triplicated state | 820 | 15 | 567 | 238 | **0** |
| Unprotected: datapath and latched header registers | 480 | 364 | 0 | 8 | **108 (22.5 %)** |

Hardware (m7g):

| Fault class | Injected | Corrected | Detected | Not seen | Silent |
|---|---:|---:|---:|---:|---:|
| SEM: configuration upsets, ECC words and design bits | 251 | 198, 125–153 ms after injection | – | 53 (masked or unused frames) | **0** (scenes bit-exact, no stall) |
| Memory ECC single / double-bit (3 memories) | 4 runs | as predicted | DBE: scenes flagged | – | **0** |

Protected state:

- With 0 silent outcomes in 820 trials, the silent fraction is below 0.4 % (95 % confidence,
  rule of three).
- Per class of 60 trials it is below 5 %.
- Every one of the 220 upsets in a triplicated state copy was outvoted and healed at the next
  clock.

Configuration memory:

- It is the largest target: 51 906 frames, about 154 Mbit.
- The SEM controller scans it once every 154 ms at the 31.25 MHz ICAP clock (51 906 frames ×
  93 words).
- On the board, every upset it saw was corrected within one scan (at most 153 ms).

Residual risk: upsets in the datapath registers (one sample, one residual or the coder's bit
accumulator) corrupt the scene silently about one time in four. Below, that risk is broken
down and the decision about it explained.

## How the campaign changed the design

The first campaign ran on build m7g and found control registers that steer the data and had
no protection. Build m7h triplicates them.

| Run | Chain | Unprotected registers silent | Note |
|---|---|---:|---|
| 1 (commit e1bff2a, superseded) | to the compressor | 22 % | understated: half the bit flips were no-ops (below) |
| 2 (m7g, flips fixed) | to the compressor | 33 % (139 / 420) | corner turn write count 36 of 38 silent |
| 3 (m7g) | to the AOS framer | 40 % (166 / 420) | packetizer and framer counters and pointers 93–100 % silent |
| 4 (m7h, final) | to the AOS framer | 22.5 % (108 / 480) | the steering state is triplicated; the 220 upsets in it were all corrected |

Run 3 (m7g), the registers that m7h triplicates:

| Register | Trials | Silent | Detected |
|---|---:|---:|---:|
| packetizer packet index | 22 | 22 | 0 |
| packetizer sequence count | 9 | 9 | 0 |
| AOS framer first header pointer | 19 | 19 | 0 |
| AOS framer virtual channel frame count | 14 | 14 | 0 |
| AOS framer zone write position | 14 | 13 | 1 |
| AOS framer buffers full | 14 | 13 | 0 |
| AOS framer buffer being filled | 20 | 9 | 11 |
| corner turn write count | 11 | 11 | 0 |
| corner turn read band | 15 | 7 | 0 |
| corner turn read address | 15 | 3 | 0 |

An upset there moves every later sample, packet header or frame of the scene. Some are
persistent: the sequence count or frame count stays wrong until the next reset.

**Decision (2026-10-13, taken on the user's behalf): build m7h triplicates this state the
same way as RAD-04.**

- How:
  - three copies with `DONT_TOUCH`;
  - the logic reads the vote;
  - every assignment goes to all three copies;
  - each copy reloads the vote every cycle;
  - the mismatch is counted in `RAD.TMR_FSM`.
- What:
  - corner turn: `wcnt`, `wr_on`, `wr_drop`, `wr_scene`, `scene_lost`, `line_ue`,
    `lines_in`, `cur_ts`, `rd`, `xr`, `zr`, `ra`;
  - packetizer: `plast`, `hi`, `left`, `seq_count`, `pkt_index`, `ts`;
  - AOS framer: `wb`, `wp`, `fhp`, `full`, `vc_count`, `pad`, `pad_i`, `pad_len`.
- Cost: 0.7 k LUT and 0.5 k FF (38.2 k LUT, 39.2 k FF in total).
- Timing: WNS +0.087 ns (m7g: +0.103 ns).
- Board: baseline and the 10-minute VER-08 run pass on m7h.

## Part (a): simulation

### Method

`tb/frontend/tb_fault.sv` runs the payload chain as `hpdp_core` connects it:

- `sensor_frontend`: DMA source, radiometric correction with its ECC calibration table,
  corner turn with ECC buffers.
- `scene_guard`.
- `c123_core`: CCSDS 123 compressor with its ECC line buffer and weights, TMR memories and
  the packer.
- `space_packetizer`: 512-byte packets; the scene timestamp and flags per scene, as in
  `hpdp_core`.
- `aos_framer`: idle frames off, so a scene gives the same frames in every trial.

The input is one scene of the `fe/dma_basic` vectors: 24 × 6 × 20, D = 12, line period 521
sensor clocks. The fault-free reference is 4 AOS frames.

Every trial:

1. Resets the chain and reloads the calibration table.
2. Injects one fault of its class at a random moment within the scene. Errors in the ECC
   memories, which the memories store at the next write of the target word, are instead
   armed before the scene.
3. Waits until the compressor has ended the scene and the framer has sent as many frame bytes
   as the reference, or a timeout.
4. Compares the frames with the reference.

| Outcome | Meaning |
|---|---|
| masked | frames identical, nothing reported |
| corrected | frames identical, a correction reported (ECC single-bit, a TMR copy outvoted) |
| detected | an uncorrectable error reported (ECC double-bit, error marks on the samples, a scene padded) or the scene did not complete (fewer frames: a stalled stage or a missing scene, which the PL watchdogs or `hpdp_run` report, RAD-05), whatever the frames |
| silent | frames different, nothing but corrections reported |

| Class | Mechanism under test | Injection |
|---|---|---|
| ECC SBE / DBE calibration table | UltraScale RAM ECC (RAD-02) | the memory's injection input, a random entry, at its next write |
| ECC SBE / DBE corner-turn buffer | UltraScale RAM ECC (RAD-02) | the same, a random sample of either buffer |
| ECC SBE / DBE compressor line buffer | RAM ECC (RAD-02) | the same, a random sample |
| ECC SBE / DBE compressor weights | block RAM ECC (RAD-03) | the injection input forced at a random moment until the next write |
| TMR memory copy | `tmr_ram` (RAD-03) | one bit of one copy, in a random entry of the coder state or a neighbourhood memory |
| TMR state copy | triplicated state (RAD-04, m7h) | one bit of one copy of one of 22 state registers |
| Unprotected register | none | one bit of one of 20 datapath and latched header registers |

Notes on the method:

- **ECC classes.** The simulation model of the XPM memories does not decode bits flipped in
  its storage array, so the ECC classes use the memories' error-injection inputs. The model
  supports these, and they are the inputs the board uses in part (b) (`RAD.ECC_INJECT`).
- **Bit flips.** xsim evaluates the index of a compound assignment twice:
  `x[$urandom_range(..)] ^= 1` computes `x[i] = !x[j]`, which is a no-op half the time.
  1000 trials from random values: 462 changed nothing. The bench draws the index into a
  variable first. Run 1 (above) had this fault, so its unprotected figure was too low.

### Results: all classes (m7h, 60 trials per class, seed 7)

| Class | Trials | Masked | Corrected | Detected | Silent |
|---|---:|---:|---:|---:|---:|
| ECC SBE calibration table | 60 | 0 | 60 | 0 | 0 |
| ECC DBE calibration table | 60 | 0 | 0 | 60 | 0 |
| ECC SBE corner-turn buffer | 60 | 0 | 60 | 0 | 0 |
| ECC DBE corner-turn buffer | 60 | 0 | 0 | 60 | 0 |
| ECC SBE compressor line buffer | 60 | 5 | 55 | 0 | 0 |
| ECC DBE compressor line buffer | 60 | 2 | 0 | 58 | 0 |
| ECC SBE compressor weights | 60 | 0 | 60 | 0 | 0 |
| ECC DBE compressor weights | 60 | 0 | 0 | 60 | 0 |
| TMR memory copy (coder, neighbourhood) | 60 | 8 | 52 | 0 | 0 |
| TMR state copy | 60 | 0 | 60 | 0 | 0 |
| Unprotected register | 60 | 45 | 0 | 2 | 13 |

Where masked outcomes come from in the protected classes:

- Line buffer: the corrupted word was overwritten before any read whose errors count (a read
  whose data the predictor uses).
- TMR memory: the upset entry was rewritten before it was read; all 8 are in the north-sample
  memory.

Masked is a correct outcome in both cases: the frames are exact.

### Results: triplicated state (m7h, 220 trials, seed 13, `+CLASS=9`)

All 220 were corrected (outvoted, healed at the next clock, `RAD.TMR_FSM` counts). By register:

| Register | Trials | Corrected |
|---|---:|---:|
| corner turn `wb` | 4 | 4 |
| corner turn `full` | 3 | 3 |
| corner turn `rd_on` | 13 | 13 |
| scene guard `pad` | 5 | 5 |
| compressor packer `state` (copy 0) | 15 | 15 |
| compressor packer `state` (copy 2) | 12 | 12 |
| packetizer `busy` | 5 | 5 |
| packetizer `in_hdr` | 12 | 12 |
| AOS framer output state | 7 | 7 |
| corner turn write count | 11 | 11 |
| corner turn read address | 7 | 7 |
| corner turn read band | 10 | 10 |
| corner turn line descriptor | 9 | 9 |
| corner turn line counter | 10 | 10 |
| packetizer sequence count | 10 | 10 |
| packetizer packet index | 17 | 17 |
| packetizer payload bytes left | 15 | 15 |
| AOS framer zone write position | 12 | 12 |
| AOS framer first header pointer | 11 | 11 |
| AOS framer buffers full | 10 | 10 |
| AOS framer frame count | 8 | 8 |
| AOS framer buffer being filled | 14 | 14 |

### Results: unprotected registers (m7h, 420 trials, seed 11, `+CLASS=10`)

| Register | Trials | Masked | Detected | Silent | Silent % |
|---|---:|---:|---:|---:|---:|
| compressor packer: bit accumulator | 20 | 3 | 0 | 17 | 85 % |
| neighbourhood: sample (S1) | 21 | 12 | 0 | 9 | 43 % |
| packetizer: input byte index | 24 | 15 | 0 | 9 | 38 % |
| radiometric: corrected sample | 27 | 18 | 0 | 9 | 33 % |
| radiometric: difference | 19 | 13 | 0 | 6 | 32 % |
| neighbourhood: line buffer address (S1) | 23 | 16 | 0 | 7 | 30 % |
| radiometric: sum | 14 | 10 | 0 | 4 | 29 % |
| coder: counter (pipeline) | 23 | 17 | 0 | 6 | 26 % |
| predictor: doubled prediction | 25 | 19 | 0 | 6 | 24 % |
| neighbourhood: band index (S1) | 27 | 21 | 0 | 6 | 22 % |
| coder: mapped residual | 19 | 15 | 0 | 4 | 21 % |
| AOS framer: output byte index | 12 | 9 | 1 | 2 | 17 % |
| coder: accumulator (pipeline) | 19 | 16 | 0 | 3 | 16 % |
| predictor: wrapped prediction | 20 | 17 | 0 | 3 | 15 % |
| packetizer: input word | 18 | 16 | 0 | 2 | 11 % |
| predictor: prediction sum | 29 | 27 | 0 | 2 | 7 % |
| scene guard: sample count | 20 | 15 | 5 | 0 | 0 % |
| packetizer: header bytes | 17 | 17 | 0 | 0 | 0 % |
| AOS framer: latched first header pointer | 21 | 21 | 0 | 0 | 0 % |
| AOS framer: latched frame count | 22 | 22 | 0 | 0 | 0 % |
| **all** | **420** | **319** | **6** | **95** | **22.6 %** |

The 60-trial run above gave 13 silent of 60 (21.7 %) on the same registers.

What these upsets do:

- **Packer bit accumulator.** An upset corrupts the coded bit stream. The CCSDS 123 decoder on
  the ground then decodes wrong samples from that point to the end of the scene. The payload
  reports nothing, but the decoded image shows it.
- **Datapath registers** (radiometric correction, neighbourhood, predictor, coder). An upset
  changes one sample, one prediction or one residual. Lossless compression reproduces the
  corrupted value at the ground, and later predictions that use it are reproduced the same
  way.
- **Packetizer input byte index.** An upset misplaces bytes of one 64-bit input word.
- **Scene guard sample count.** An upset either changes nothing or makes the guard pad or end
  the scene early, which is reported (detected).
- **Masked fractions depend on the workload.** This scene keeps the pipeline busy about a
  third of the time (2 880 samples in about 7 800 clk_sys cycles), so many upsets land on
  idle registers. At flight sizes and full rate the pipeline is busy nearly all the time,
  and the silent fraction of these registers can rise by up to about 2.7×.

The compressor's line-buffer `sample_ram` prints "starts a run inside a word" (a
simulation-only check) when an upset hits the neighbourhood's line-buffer address. These
messages in the logs are expected.

### Residual risk and decision

The remaining unprotected registers are the datapath (samples, residuals, sums, the coder's
bit accumulator) and header bytes latched for one packet or frame.

Decision (2026-10-13, on the user's behalf): this is accepted as M7's residual risk. Reasons:

- Per bit, flip-flops of 16 nm UltraScale+ devices upset much less often than configuration
  memory (AMD UG116, device reliability report, has the figures). Configuration memory
  upsets are corrected by the SEM controller.
- An upset in these registers corrupts at most the scene in flight. Nothing persists past it,
  unlike the counters triplicated in m7h.
- Triplicating the datapath would roughly triple its flip-flops and its voters at 250 MHz.

If a later mission needs it, the first candidate is the packer's 128-bit bit accumulator. It
is the worst register (85 % silent) and the only one whose upset corrupts the rest of the
scene rather than one sample.

### Limitations

- **Single faults.** One upset per trial. Multi-bit upsets in one memory word are the
  double-bit classes. TMR assumes one copy is hit at a time; copies heal at the next clock
  (state) or the next write (memories).
- **One scene shape.** The scene is small (above); masked fractions are lower at flight sizes.
- **Not in this chain:**
  - the Reed-Solomon encoder, the CADU framer and the link, which are driven by counters;
  - the register block TMR (`hpdp_regs_tmr`), tested on its own by `tb_regs_tmr`;
  - clock and reset networks, DSP and GTH internals, and the processing system.
- **Configuration memory upsets cannot be simulated.** They are part (b).

### Reproducing

```
vivado -mode batch -source tb/run_sim.tcl -tclargs fault_campaign
```

This compiles everything and runs 3 trials per class (33 trials). It is part of the
regression.

For the full runs (in build/sim, after the above):

```
xsim tb_fault -R -testplusarg DIR=fe/dma_basic -testplusarg TRIALS=60 -testplusarg SEED=7
xsim tb_fault -R -testplusarg DIR=fe/dma_basic -testplusarg TRIALS=420 -testplusarg SEED=11 -testplusarg CLASS=10
xsim tb_fault -R -testplusarg DIR=fe/dma_basic -testplusarg TRIALS=220 -testplusarg SEED=13 -testplusarg CLASS=9
```

Each writes `fault_results.csv` (per class) and `fault_targets.csv` (per target of the TMR
and unprotected classes).

The bench's dedicated tests of the m7h copies:

- `tb_corner_turn`: 30 forced upsets;
- `tb_downlink`: up to 40 per test vector set.

Both check that the outputs are exact and every mismatch heals at the next clock.

## Part (b): hardware

All runs used JTAG (volatile configuration), the v7 app (`build/m7g/hpdp_run_v7.elf`), the
fiber link and 4 scenes of 128 × 64 × 64 unless noted.

### ECC error injection (RAD-02, RAD-06)

The RAD registers inject into the calibration table, the corner-turn buffers and the
compressor line buffer (`run_m5.py --ecc`).

| Build | Run | Result |
|---|---|---|
| m7c (2026-10-12) | single-bit errors in all three memories | counts 1 / 4 / 4 as predicted by simulation; capture and ground bit-exact |
| m7c | double-bit error in table entry 4100, correction bypassed | `CAL_DBE` 256 (4 scenes × 64 lines); data bit-exact; all 4 scenes flagged "internal error" by the ground station |
| m7g (2026-10-13) | the same two runs | the same counts, PASS |

The compressor weights' ECC (RAD-03) has no PS-controlled injection. It is covered by part
(a) only.

### SEM configuration upsets (RAD-01), build m7g, 2026-10-13

`hpdp_run` (job v7) injects through the SEM controller's command interface before the scenes
(`run_m5.py --sem-inject`, `--sem-campaign`), one upset at a time:

1. Idle, inject, Observation.
2. Wait up to 5 s for `RAD.SEM_CORRECTIONS` to move.
3. If it doesn't move, inject again to undo the upset.

The latency runs from the return to observation until the correction is counted. Frames are
drawn from 34 604..47 000: PG187 advises frames above 2/3 of the device on parts with a
processing system.

| Run | Injected | Corrected | Latency (min–max, mean) | Not seen | Uncorrectable | Afterwards |
|---|---:|---:|---|---:|---:|---|
| `--sem-inject 0x9000:46:0` | 1 | 1 | 131 ms | 0 | 0 | scenes bit-exact |
| `--sem-campaign 200` (ECC words: word 46, bits 0–7) | 200 | 158 | 125–153 ms, 138 ms | 42 | 0 | scenes bit-exact |
| `--sem-campaign 50 --sem-design` (any word and bit) | 50 | 39 | 125–153 ms, 138 ms | 11 | 0 | scenes bit-exact, no stall, no TMR disagreement |

Observations:

- **Correction time.** Every upset the controller saw was corrected within one scan of the
  configuration memory: 154 ms at the 31.25 MHz ICAP clock; measured 125–153 ms.
- **Not seen (21 % in both campaigns).**
  - These are frames the controller does not check: masked frames, whose contents change
    while the design runs (the LUT RAM of the TMR memories, shift registers, block RAM
    contents), or addresses with no configuration memory behind them.
  - PG187 describes both. The injections were undone, and none caused an error.
  - A real upset in a masked frame is not corrected by SEM. For the LUT RAM, the TMR above
    covers it.
- **Design-bit upsets.** 50 upsets in the design's own configuration bits, corrected within
  about 140 ms each while the pipeline was idle. The scenes afterwards were bit-exact, and no
  stage stalled or flagged a TMR disagreement.
- **Since then.** m7h differs from m7g only in fabric logic (more TMR); its SEM controller and
  configuration are unchanged.
