import json
import os

from amd_mcp import handoff
from amd_mcp import xsa as xsa_mod
from conftest import make_xsa


def test_inspect_zynqmp(tmp_path):
    info = xsa_mod.inspect(make_xsa(tmp_path / "a.xsa"))
    assert info["family"] == "zynqmp"
    assert info["part"] == "xczu7ev-ffvc1156-2-e"
    assert info["board"] == "xilinx.com:zcu106:part0:2.6"
    assert info["top"] == "top" and info["vivado_version"] == "2025.2"
    assert info["bitstream"] == "top.bit" and info["has_bitstream"]
    assert info["ps_init_tcl"] == "psu_init.tcl"
    assert len(info["sha256"]) == 64
    assert xsa_mod.default_cpu(info) == "psu_cortexa53_0"


def test_families_and_default_cpu(tmp_path):
    zynq = xsa_mod.inspect(make_xsa(tmp_path / "z.xsa", "zynq"))
    assert zynq["family"] == "zynq" and xsa_mod.default_cpu(zynq) == "ps7_cortexa9_0"
    mb = xsa_mod.inspect(make_xsa(tmp_path / "m.xsa", "fpga", microblaze="microblaze_0"))
    assert mb["family"] == "fpga" and xsa_mod.default_cpu(mb) == "microblaze_0"
    versal = xsa_mod.inspect(make_xsa(tmp_path / "v.xsa", "versal"))
    assert versal["family"] == "versal" and xsa_mod.default_cpu(versal) is None   # ask Vitis
    nobit = xsa_mod.inspect(make_xsa(tmp_path / "n.xsa", bit=False))
    assert not nobit["has_bitstream"] and nobit["bitstream"] is None


def test_find_and_extract(tmp_path):
    (tmp_path / "b1").mkdir()
    make_xsa(tmp_path / "b1" / "x.xsa")
    (tmp_path / "proj.cache").mkdir()
    make_xsa(tmp_path / "proj.cache" / "copy.xsa")          # inside a Vivado cache: skipped
    (tmp_path / "bad.xsa").write_bytes(b"not a zip")
    found = xsa_mod.find(tmp_path)
    paths = {os.path.basename(f["path"]) for f in found}
    assert paths == {"x.xsa", "bad.xsa"}
    assert any("error" in f for f in found if f["path"].endswith("bad.xsa"))
    out = xsa_mod.extract(tmp_path / "b1" / "x.xsa", "psu_init.tcl", tmp_path / "out")
    assert out.read_text() == "proc psu_init {} {}"


def test_export_platform_app_records(tmp_path):
    xsa = make_xsa(tmp_path / "a.xsa", stamp="1")
    assert handoff.latest_export() is None
    first = handoff.record_export(xsa, part="p", include_bit=True)
    assert handoff.latest_export()["id"] == first["id"]
    assert handoff.export_state(first)["file_state"] == "unchanged"

    ws = tmp_path / "ws"
    handoff.record_platform(ws, "plat", xsa=str(xsa), sha256=first["sha256"], built=True)
    handoff.record_app(ws, "app", elf="a.elf", platform="plat")
    status = handoff.status()
    assert status["platforms"][0]["stale"] is False
    assert handoff.latest_app()["name"] == "app"
    assert handoff.platform_record(ws, "plat")["sha256"] == first["sha256"]

    make_xsa(xsa, stamp="2")                                  # Vivado rewrote it
    assert handoff.export_state(first)["file_state"].startswith("changed")
    second = handoff.record_export(xsa)
    assert second["sha256"] != first["sha256"]
    assert handoff.status()["platforms"][0]["stale"] is True  # platform predates the export
    assert handoff.status()["previous_exports"] == 1

    xsa.unlink()
    assert handoff.export_state(second)["file_state"] == "missing"


def test_corrupt_handoff_file_is_set_aside(state):
    state.mkdir(parents=True, exist_ok=True)
    handoff.handoff_file().write_text("{not json", encoding="utf-8")
    assert handoff.load()["exports"] == []
    assert any(p.name.startswith("handoff.bad-") for p in state.iterdir())


def test_handoff_file_is_valid_json_after_writes(tmp_path):
    xsa = make_xsa(tmp_path / "a.xsa")
    for _ in range(60):
        handoff.record_export(xsa)
    data = json.loads(handoff.handoff_file().read_text(encoding="utf-8"))
    assert len(data["exports"]) == handoff.KEEP_EXPORTS
    assert not handoff.handoff_file().with_suffix(".lock").exists()
