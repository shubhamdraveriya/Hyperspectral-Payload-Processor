# M7 fault-injection report (RAD-07)

Status (2026-10-13):

- **Part (a), simulation: done.** 1 080 trials. No protected class had a silent outcome.
- **Part (b), hardware:**
  - ECC injection: done on build m7c (docs/m7_plan.md, items 1 and 2).
  - SEM configuration upsets: the tools are ready (`run_m5.py --sem-inject`,
    `--sem-campaign`); the board runs are pending (docs/m7_board_runbook.md).

SPEC RAD-07 asks for:

- (a) random bit flips in registers and memories in simulation, each outcome classified as
  masked, corrected, detected or silent corruption;
- (b) SEM IP configuration upsets and ECC error injection on hardware;
- results written up with coverage figures.

## Summary

| Fault class | Trials | Masked | Corrected | Detected | Silent |
|---|---:|---:|---:|---:|---:|
| Protected (ECC memories, TMR memories, TMR state machines) | 600 | 19 | 343 | 238 | **0** |
| Unprotected registers (pipeline and counters) | 480 | 366 | 0 | 6 | **108 (22.5 %)** |

Protected state:

- All 600 faults ended masked, corrected or detected.
- With 0 silent outcomes in 600 trials, the silent fraction is below 0.5 % (95 % confidence,
  rule of three).
- Per class it is below 5 % (0 of 60).

Unprotected flip-flops (pipeline registers and counters, which no requirement covers):

- About one upset in four corrupts the scene without anything reporting it.
- That is the residual risk.
- It is concentrated in a few registers (below).

Configuration memory:

- It is the largest target: the m7 bitstreams write 51 906 frames of 93 words, about
  154 Mbit.
- It is covered by the SEM controller (RAD-01), which is part (b).

## Part (a): simulation

### Method

`tb/frontend/tb_fault.sv` runs the payload chain as `hpdp_core` connects it:

- `sensor_frontend` (DMA source, radiometric correction with its ECC calibration table,
  corner turn with ECC buffers);
- `scene_guard`;
- `c123_core` (CCSDS 123 compressor with its ECC line buffer and weights, TMR memories and
  the packer).

The input is one scene of the `fe/dma_basic` vectors: 24 × 6 × 20, D = 12, line period 521
sensor clocks. The fault-free reference output is 2 916 compressed bytes.

Every trial:

1. Resets the chain and reloads the calibration table.
2. Injects one fault of its class at a random moment within the scene (uniform over the
   scene's duration). Errors in the ECC memories, which the memories write on the next
   write of the target word, are instead armed before the scene.
3. Lets the scene run to the end, or to a timeout of three scene durations.
4. Compares the output with the reference.

| Outcome | Meaning |
|---|---|
| masked | output identical, nothing reported |
| corrected | output identical, a correction reported (ECC single-bit, a TMR copy outvoted) |
| detected | an uncorrectable error reported (ECC double-bit, error marks on the samples, a scene padded by the scene guard) or the scene did not complete (what the PL watchdog reports, RAD-05), whatever the output |
| silent | output different, nothing but corrections reported |

How each class is injected:

| Class | Mechanism under test | How the fault is injected |
|---|---|---|
| ECC SBE / DBE calibration table | UltraScale RAM ECC (RAD-02) | the memory's injection input: a single or double-bit error into a random entry, at its next write |
| ECC SBE / DBE corner-turn buffer | UltraScale RAM ECC (RAD-02) | the same, a random sample of either buffer |
| ECC SBE / DBE compressor line buffer | RAM ECC (RAD-02) | the same, a random sample |
| ECC SBE / DBE compressor weights | block RAM ECC (RAD-03) | the memory's injection input forced at a random moment until its next write |
| TMR memory copy | `tmr_ram` (RAD-03) | one bit of one copy, in a random entry of the coder state or a neighbourhood memory |
| TMR state machine copy | triplicated state (RAD-04) | one bit of one copy: corner turn `wb` / `full` / `rd_on`, scene guard `pad`, packer `state` |
| Unprotected register | none | one bit of one of 14 pipeline and control registers |

Note on the ECC classes: the simulation model of the XPM memories does not decode bits
flipped in its storage array, so the ECC classes inject through the memories'
error-injection inputs. The model supports these, and they are the same inputs the board
uses in part (b) (`RAD.ECC_INJECT`).

### Results: all classes

Run: 60 trials per class, seed 7 (`fault_results_full.csv`, `fault_targets_full.csv`).

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
| TMR memory copy (coder, neighbourhood) | 60 | 12 | 48 | 0 | 0 |
| TMR state machine copy | 60 | 0 | 60 | 0 | 0 |
| Unprotected register | 60 | 45 | 0 | 1 | 14 |

Where masked outcomes come from in the protected classes:

- Line buffer: the corrupted word was overwritten before any read whose errors count (a read
  whose data the predictor uses).
- TMR memory: the upset copy entry was rewritten before it was read.

Masked is a correct outcome in both cases: the output is exact.

Per target, protected:

| Target | Trials | Masked | Corrected | Detected | Silent |
|---|---:|---:|---:|---:|---:|
| TMR memory: coder state (accumulator, counter) | 12 | 4 | 8 | 0 | 0 |
| TMR memory: neighbourhood west sample | 14 | 0 | 14 | 0 | 0 |
| TMR memory: neighbourhood north sample | 12 | 8 | 4 | 0 | 0 |
| TMR memory: neighbourhood north-west sample | 13 | 0 | 13 | 0 | 0 |
| TMR memory: neighbourhood first-column sample | 9 | 0 | 9 | 0 | 0 |
| TMR state: corner turn `wb` | 11 | 0 | 11 | 0 | 0 |
| TMR state: corner turn `full` | 9 | 0 | 9 | 0 | 0 |
| TMR state: corner turn `rd_on` | 6 | 0 | 6 | 0 | 0 |
| TMR state: scene guard `pad` | 11 | 0 | 11 | 0 | 0 |
| TMR state: packer `state` (copy 0) | 11 | 0 | 11 | 0 | 0 |
| TMR state: packer `state` (copy 2) | 12 | 0 | 12 | 0 | 0 |

### Results: unprotected registers

Run: 420 trials, seed 11, `+CLASS=10`, file `fault_targets_unprot.csv`.

| Register | Trials | Masked | Detected | Silent | Silent % |
|---|---:|---:|---:|---:|---:|
| corner turn: write count | 38 | 16 | 0 | 22 | 58 % |
| compressor packer: bit accumulator | 30 | 16 | 0 | 14 | 47 % |
| corner turn: read band | 20 | 12 | 1 | 7 | 35 % |
| coder: counter (pipeline) | 18 | 13 | 0 | 5 | 28 % |
| coder: mapped residual | 31 | 23 | 0 | 8 | 26 % |
| coder: accumulator (pipeline) | 41 | 33 | 0 | 8 | 20 % |
| radiometric: difference | 30 | 24 | 0 | 6 | 20 % |
| radiometric: corrected sample | 33 | 27 | 0 | 6 | 18 % |
| neighbourhood: line buffer address | 31 | 26 | 0 | 5 | 16 % |
| radiometric: sum | 40 | 34 | 0 | 6 | 15 % |
| neighbourhood: sample | 23 | 20 | 0 | 3 | 13 % |
| corner turn: read address | 25 | 23 | 0 | 2 | 8 % |
| neighbourhood: band index | 30 | 28 | 0 | 2 | 7 % |
| scene guard: sample count | 30 | 26 | 4 | 0 | 0 % |
| **all** | **420** | **321** | **5** | **94** | **22 %** |

The 60-trial run above gave 14 silent of 60 (23 %) on the same registers, consistent with
this.

Observations:

- **Corner turn write count:**
  - An upset moves the rest of the line's samples to wrong buffer addresses until the next
    line starts. That is silent and confined to that line.
  - It also makes a write run start inside a 64-bit word, which the simulation-only check
    in `sample_ram` reports ("starts a run inside a word"). These messages in the campaign
    logs are expected.
  - In hardware, the lanes before such a run's start take stale samples from the gather
    registers.
- **Packer bit accumulator:**
  - An upset corrupts the coded bit stream.
  - The CCSDS 123 decoder on the ground then decodes wrong samples from that point to the
    end of the scene. Nothing reports it on the payload, but the decoded image shows it.
- **Datapath registers (coder, radiometric correction, neighbourhood):**
  - An upset changes one sample or one residual.
  - In lossless compression, the decoder reproduces the corrupted value. Errors carried
    into later predictions are reproduced the same way, so the decoded scene differs from
    the sensor data in the affected samples.
- **Scene guard sample count:** an upset either changes nothing or makes the guard pad or
  end the scene early, which is reported (detected).
- **Masked fractions depend on the workload.** This scene keeps the pipeline busy about a
  third of the time (2 880 samples in about 7 800 clk_sys cycles), so many upsets land on
  idle registers. A flight-size scene at full rate keeps it busy nearly all the time, so
  the silent fraction of the datapath registers can rise by up to about 2.7×.

### Residual risk and decision

The unprotected registers are pipeline registers and counters (the requirement covers
memories, compressor state, configuration registers and control state machines).

Decision (2026-10-13, on the user's behalf): this is accepted as the residual risk of M7 and
recorded here. Reasons:

- Per bit, flip-flops of 16 nm UltraScale+ devices upset much less often than
  configuration memory (AMD UG116, device reliability report, has the figures).
  Configuration memory upsets are corrected by the SEM controller.
- A silent upset corrupts at most the scene in flight.
- Triplicating the datapath would roughly triple its flip-flops, and the build has little
  timing margin at 250 MHz (WNS +0.054 ns on m7f).

The one cheap improvement the data points to: TMR on the corner turn's address counters
(write count, read address, read band). These are about 66 flip-flops, and the write count
alone gives the most silent outcomes. It is being evaluated as an experimental build after
m7g. It is adopted only if timing still closes.

### Limitations

- **Single faults.** One upset per trial. Multi-bit upsets in one memory word are the
  double-bit classes. TMR assumes one copy is hit at a time; copies heal at the next clock
  (state machines) or the next write (memories).
- **One scene shape.** The scene is small (above); masked fractions will be lower at flight
  sizes.
- **Not in this chain:**
  - The downlink side: the packetizer and AOS framer, whose TMR state uses the same code as
    the tested state machines; and the counter-driven Reed-Solomon encoder, CADU framer and
    link.
  - The register block TMR (`hpdp_regs_tmr`), tested on its own by `tb_regs_tmr` (an upset
    in each copy, masked and healed).
- **Configuration memory upsets cannot be simulated.** They are part (b).
- **Out of scope:** clock and reset networks, DSP and GTH internal registers, and the
  processing system.

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
```

They take 3.5 and 2.5 minutes. Each writes `fault_results.csv` (per class) and
`fault_targets.csv` (per target of the TMR and unprotected classes).

## Part (b): hardware

### ECC error injection: done (build m7c, 2026-10-12)

The RAD registers inject into the calibration table, the corner-turn buffers and the
compressor line buffer (`run_m5.py --ecc`).

| Run | Result |
|---|---|
| Single-bit errors in all three memories (4 scenes, 128 × 64 × 64) | counts 1 / 4 / 4 as predicted by simulation; capture and ground bit-exact |
| Double-bit error in table entry 4100 (correction bypassed) | `CAL_DBE` 256 (4 scenes × 64 lines; not written back); data bit-exact; all 4 scenes flagged "internal error" by the ground station |

The compressor weights' ECC (RAD-03) has no PS-controlled injection. It is covered by part
(a) only.

### SEM configuration upsets: tools ready, board pending

Single injection: `run_m5.py --sem-inject FRAME:WORD:BIT` (job v6) puts one bit error in
configuration memory before the scenes, and checks that the controller corrects it.

Campaign: `run_m5.py --sem-campaign COUNT[:LO:HI]` (job/result v7). `hpdp_run` injects COUNT
errors one at a time before the scenes and waits up to 5 s for each correction. Frames are
drawn by xorshift32 (`--sem-seed`) from LO..HI.

- **Default range: 34 604..47 000.**
  - PG187 advises frames above 2/3 of the maximum linear frame on devices with a processing
    system; the lower frames are reserved.
  - The m7 bitstreams write 51 906 frames, which include block RAM content frames and
    padding.
  - The controller's own maximum frame (`MF`) comes only in its status report on the monitor
    interface, whose input is not wired. If the first board run finds many frames not
    corrected, the range is the first thing to narrow.
- **Default target:** each injection goes into the frame's ECC word (word 46, bits 0–7).
  This is AMD's guidance for testing the controller without touching the design.
- **`--sem-design`:** any word and bit instead. The scenes after the campaign then show
  whether the design survived real upsets in its configuration.
- **Not corrected within 5 s** (a masked or unused frame): the injection is repeated, which
  undoes it (PG187), and counted as "not seen".
- **The campaign stops on:**
  - an uncorrectable error (the device must then be reconfigured);
  - a command the controller does not take;
  - 20 not seen in a row (frame range wrong).
- **Reported:** injected, corrected, not seen, and latency (minimum, mean, maximum, from the
  return to observation until the correction counter moves). Then the scenes run and are
  checked bit-exactly as usual.

Board runs (runbook): `--sem-inject` once, `--sem-campaign 200`, then
`--sem-campaign 50 --sem-design`. PASS when every injection the controller sees is corrected
and the scenes after the campaign are bit-exact. The results will be added here.
