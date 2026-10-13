# M7 board session runbook

Everything below needs the ZCU106 with the SFP0 <-> SFP1 fiber, JTAG only (volatile
configuration; nothing writes flash). Builds: `build/m7g` (normal: RAD-01 .. RAD-06, commit
d8d2878) and `build/m7f_ibert` (In-System IBERT, `HPDP_IBERT=1`, commit 1a0397f: without the
RAD-03 / RAD-04 counters, which read 0 there). The app (job/result v7) is
`build/m7g/hpdp_run_v7.elf`, built against `build/m7g/hpdp.xsa` (`vitis -s
sw/baremetal/build.py build/m7g/hpdp.xsa`, workspace `HPDP_VITIS_WS=~/hpdp_vitis_m7g`), and
works with both bitstreams. **Pass `--elf build/m7g/hpdp_run_v7.elf` to every `run_m5.py`
command below**: the default (`~/hpdp_vitis_m5`) holds the v6 app, which rejects v7 jobs
("bad job"). Every run also prints the RAD-03 / RAD-04 counters (weights ECC, TMR
disagreements): they must be 0 (nothing injects into them on the board).

| # | What | Command | Pass when |
|---|---|---|---|
| 1 | Baseline: capture + downlink, SEM observing, no stall | `python sw/host/run_m5.py --xsa build/m7g/hpdp.xsa --work ~/hpdp_m7_base` | PASS; SEM observing, 0 uncorrectable; 0 stalls |
| 2 | RAD-02 / RAD-06 again on this build | `run_m5.py ... --ecc cal:4100 --ecc ct:8 --ecc lb:128` then `... --no-correction --ecc cal:4100:dbe` | counts 1 / 4 / 4, bit-exact; DBE run: 256, scenes flagged |
| 3 | RAD-01 SEM correction | `run_m5.py ... --sem-inject 0x9000:46:0` (frame above 2/3 of the device, its ECC word: PG187) | "corrected after N ms"; if "masked frame?", try other frames (e.g. 0x9800, 0xA000) |
| 3a | RAD-07 (b) SEM campaign, ECC words | `run_m5.py ... --sem-campaign 200` (frames 34 604 .. 47 000) | PASS: 200 injected, all seen ones corrected, scenes bit-exact; note latency and "not seen" count. Many "not seen": narrow the range (`--sem-campaign 200:LO:HI`) |
| 3b | RAD-07 (b) SEM campaign, design bits | `run_m5.py ... --sem-campaign 50 --sem-design` | every seen injection corrected; scenes bit-exact (a failure here is a finding for the report, not a tool fault: note it and reconfigure) |
| 4 | RAD-05 PS watchdog armed during a run | `run_m5.py ... --wdt` | PASS (the app kicks it: no reset) |
| 5 | RAD-05 PS watchdog fires | `run_m5.py --wdt-test --xsa build/m7g/hpdp.xsa --work ~/hpdp_m7_wdt` | RESET_REASON pmu_sys_reset set |
| 6 | SYS-09 re-run 3 (decision a) | `run_m5.py --size 512 256 150 12 --scenes 1 --line-period 100000 --repeat 234 --no-capture --raw --xsa build/m7g/hpdp.xsa --work ~/hpdp_m7_sys09d` | PASS: drops counted, link clean, every compressed scene packetized, no timestamp overflow |
| 7 | VER-08 still holds (compressed, 10 min) | as in docs/m5_downlink.md, `--xsa build/m7g/hpdp.xsa` | 0 dropped lines, 0 stalls |
| 8 | LNK-10 eye scans | `run_m5.py --prbs 900 --xsa build/m7f_ibert/hpdp.xsa --work ~/hpdp_m7_prbs` in the background, then `vivado -mode batch -source sw/host/ibert_scan.tcl -tclargs build/m7f_ibert <out dir>` | eyes saved for SFP0 -> SFP1 (and SFP1 -> SFP0 if the cable is duplex: O-1); compare with JSB MARK 1's 82.95 % / 90.48 % |

Order: 1 first (sanity); 5 resets the board (run it last, or re-run 1 after it); 8 needs the
IBERT bitstream and a 6 MHz JTAG clock (the scan script sets it), so run it after the others.
After 3b, or any run that reports an uncorrectable SEM error, the next run reprograms the
device anyway (`run_m5.py` loads the bitstream every time). Results of 3, 3a and 3b go into
docs/m7_fault_injection.md (part b).
