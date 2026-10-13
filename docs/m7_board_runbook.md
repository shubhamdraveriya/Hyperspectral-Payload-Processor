# M7 board session runbook

Everything below needs the ZCU106 with the SFP0 <-> SFP1 fiber, JTAG only (volatile
configuration; nothing writes flash). Builds: `build/m7f` (normal) and `build/m7f_ibert`
(In-System IBERT, `HPDP_IBERT=1`), both from commit 1a0397f or later; the app is built
against `build/m7f/hpdp.xsa` (`vitis -s sw/baremetal/build.py build/m7f/hpdp.xsa`, workspace
`HPDP_VITIS_WS=~/hpdp_vitis_m5`) and works with both bitstreams (same register map).

| # | What | Command | Pass when |
|---|---|---|---|
| 1 | Baseline: capture + downlink, SEM observing, no stall | `python sw/host/run_m5.py --xsa build/m7f/hpdp.xsa --work ~/hpdp_m7_base` | PASS; SEM observing, 0 uncorrectable; 0 stalls |
| 2 | RAD-02 / RAD-06 again on this build | `run_m5.py ... --ecc cal:4100 --ecc ct:8 --ecc lb:128` then `... --no-correction --ecc cal:4100:dbe` | counts 1 / 4 / 4, bit-exact; DBE run: 256, scenes flagged |
| 3 | RAD-01 SEM correction | `run_m5.py ... --sem-inject 0x00100:10:5` | "corrected after N ms"; if "masked frame?", try other frames (e.g. 0x00200, 0x01000) |
| 4 | RAD-05 PS watchdog armed during a run | `run_m5.py ... --wdt` | PASS (the app kicks it: no reset) |
| 5 | RAD-05 PS watchdog fires | `run_m5.py --wdt-test --xsa build/m7f/hpdp.xsa --work ~/hpdp_m7_wdt` | RESET_REASON pmu_sys_reset set |
| 6 | SYS-09 re-run 3 (decision a) | `run_m5.py --size 512 256 150 12 --scenes 1 --line-period 100000 --repeat 234 --no-capture --raw --xsa build/m7f/hpdp.xsa --work ~/hpdp_m7_sys09d` | PASS: drops counted, link clean, every compressed scene packetized, no timestamp overflow |
| 7 | VER-08 still holds (compressed, 10 min) | as in docs/m5_downlink.md, `--xsa build/m7f/hpdp.xsa` | 0 dropped lines, 0 stalls |
| 8 | LNK-10 eye scans | `run_m5.py --prbs 900 --xsa build/m7f_ibert/hpdp.xsa --work ~/hpdp_m7_prbs` in the background, then `vivado -mode batch -source sw/host/ibert_scan.tcl -tclargs build/m7f_ibert <out dir>` | eyes saved for SFP0 -> SFP1 (and SFP1 -> SFP0 if the cable is duplex: O-1); compare with JSB MARK 1's 82.95 % / 90.48 % |

Order: 1 first (sanity); 5 resets the board (run it last, or re-run 1 after it); 8 needs the
IBERT bitstream and a 6 MHz JTAG clock (the scan script sets it), so run it after the others.
