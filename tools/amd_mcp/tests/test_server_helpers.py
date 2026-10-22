import pytest

pytest.importorskip("mcp")
pytest.importorskip("anyio")

from amd_mcp import vitis_server as vs  # noqa: E402

# what Vitis 2025.2 returns for the ZCU106 XSA (get_processor_os_list), as a dict
LISTING = {"os_processors": [
    {"os": "standalone", "cpuInstance": ["psu_cortexa53_0", "psu_cortexa53_1", "psu_cortexr5_0",
                                         "psu_pmu_0"]},
    {"os": "freertos", "cpuInstance": ["psu_cortexr5_1"]},
    {"os": "linux", "cpuInstance": ["psu_cortexa53"]}],
    "deviceFamily": "zynquplus", "flow": "EMBEDDED"}


def test_pick_cpu():
    assert vs._pick_cpu(LISTING, "standalone") == "psu_cortexa53_0"
    assert vs._pick_cpu(LISTING, "freertos") == "psu_cortexr5_1"
    assert vs._pick_cpu({"os_processors": [{"os": "standalone", "cpuInstance": ["psu_pmu_0"]}]},
                        "standalone") is None                       # never the PMU
    assert vs._pick_cpu("os: standalone cpuInstance: microblaze_0", "standalone") == "microblaze_0"


def test_processor_filter():
    assert vs._processor_filter("psu_cortexa53_2", "zynqmp") == "*A53*#2"
    assert vs._processor_filter("psu_cortexr5_1", "zynqmp") == "*R5*#1"
    assert vs._processor_filter("ps7_cortexa9_0", "zynq") == "*A9*#0"
    assert vs._processor_filter("microblaze_1", "fpga") == "*MicroBlaze*#1"
    assert vs._processor_filter(None, "zynqmp") == "*A53*#0"
    assert vs._processor_filter("filter:{name =~ x}", "zynqmp") == "{name =~ x}"


def test_zynqmp_sequence_matches_run_m4():
    steps = vs._jtag_steps("zynqmp", "C:/s/top.bit", "C:/s/psu_init.tcl", "C:/s/app.elf",
                           "*A53*#0", True, [("C:/s/d.bin", 0x10000000)], True)
    commands = [c for _, c in steps]
    assert commands == [
        'targets -set -nocase -filter {name =~ "PSU"}',
        "mwr 0xFF5E0200 0x0100",
        "rst -system",
        "after 2000",
        "fpga -file {C:/s/top.bit}",
        'targets -set -nocase -filter {name =~ "APU*"}',
        "source {C:/s/psu_init.tcl}",
        "psu_init",
        "after 1000",
        "psu_ps_pl_isolation_removal",
        "after 1000",
        "psu_ps_pl_reset_config",
        "catch {psu_protection}",
        'targets -set -nocase -filter {name =~ "*A53*#0"}',
        "rst -processor",
        "dow -data {C:/s/d.bin} 0x10000000",
        "dow {C:/s/app.elf}",
        "con",
    ]


def test_sequences_without_bitstream_or_run():
    commands = [c for _, c in vs._jtag_steps("zynqmp", None, "i.tcl", "a.elf", "*A53*#0",
                                             False, [], False)]
    assert not any(c.startswith(("fpga", "mwr", "con")) for c in commands)
    mb = [c for _, c in vs._jtag_steps("fpga", "t.bit", None, "a.elf", "*MicroBlaze*#0",
                                       True, [], True)]
    assert mb[0] == "fpga -file {t.bit}" and mb[-1] == "con" and "mwr 0xFF5E0200 0x0100" not in mb


def test_parse_targets():
    text = ("  1  PS TAP\n     2  PMU\n     3  PL\n  4* PSU\n     5  RPU (Reset)\n"
            "        6  Cortex-R5 #0 (RPU Reset)\n     8  APU\n        9  Cortex-A53 #0 (Running)")
    targets = vs._parse_targets(text)
    assert [t["id"] for t in targets] == [1, 2, 3, 4, 5, 6, 8, 9]
    assert targets[3]["selected"] and targets[3]["name"] == "PSU"
    assert targets[-1] == {"id": 9, "name": "Cortex-A53 #0", "state": "Running",
                           "selected": False, "indent": 8}


def test_workspace_with_spaces_is_refused(tmp_path):
    with pytest.raises(ValueError, match="spaces"):
        vs._workspace(str(tmp_path / "with space"))


def test_platform_follows_its_own_xsa(tmp_path, monkeypatch):
    from amd_mcp import handoff
    from conftest import make_xsa

    ws = tmp_path / "ws"
    own = make_xsa(tmp_path / "own.xsa")
    other = make_xsa(tmp_path / "other.xsa", stamp="9")
    with pytest.raises(ValueError, match="no export"):
        vs._resolve_xsa(None)                                   # nothing exported yet
    monkeypatch.setattr(handoff, "_now", lambda: "2026-01-01T10:00:00")
    first = handoff.record_export(own)
    handoff.record_platform(ws, "p", xsa=str(own), sha256=first["sha256"], built=True)
    source, export, notes = vs._resolve_platform_xsa(ws, "p", None)
    assert vs._same_file(source, own) and export["id"] == first["id"] and notes == []

    monkeypatch.setattr(handoff, "_now", lambda: "2026-01-01T11:00:00")
    handoff.record_export(other)                                # newer export, another file
    source, export, notes = vs._resolve_platform_xsa(ws, "p", None)
    assert vs._same_file(source, own) and export is None        # still follows its own file
    assert "other.xsa" in notes[0]
    assert handoff.status()["platforms"][0]["stale"] is False

    make_xsa(own, stamp="2")                                    # Vivado re-exported own.xsa
    assert handoff.status()["platforms"][0]["stale"] is True
    source, _, _ = vs._resolve_platform_xsa(ws, "p", str(other))  # explicit xsa wins
    assert vs._same_file(source, other)
    # a platform without a record takes the latest export
    assert vs._same_file(vs._resolve_platform_xsa(ws, "unknown", None)[0], other)
    own.unlink()
    assert handoff.status()["platforms"][0]["stale"] is None
