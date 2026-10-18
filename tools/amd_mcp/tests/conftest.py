import json
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


@pytest.fixture(autouse=True)
def state(tmp_path, monkeypatch):
    """Each test gets its own state folder (hand-off file, logs)."""
    folder = tmp_path / "state"
    monkeypatch.setenv("AMD_MCP_STATE_DIR", str(folder))
    return folder


def make_xsa(path: Path, family: str = "zynqmp", bit: bool = True, microblaze: str | None = None,
             stamp: str = "1") -> Path:
    """A minimal XSA with the members amd_mcp reads."""
    meta = {"topModuleName": "top", "generatedVersion": "2025.2", "generatedTimestamp": stamp,
            "board": {"part": "xczu7ev-ffvc1156-2-e", "boardPart": "xilinx.com:zcu106:part0:2.6"}}
    sysdef = ('<?xml version="1.0"?><Project><TOOL_VERSION Version="2025.2"/>'
              '<SYSTEMINFO BOARD="b" PART="xczu7ev-ffvc1156-2-e"/><HIERARCHY Name="top"/></Project>')
    hwh = "<EDKSYSTEM><MODULES>"
    if microblaze:
        hwh += f'<MODULE INSTANCE="{microblaze}" MODTYPE="microblaze"/>'
    hwh += '<MODULE INSTANCE="axi_gpio_0" MODTYPE="axi_gpio"/></MODULES></EDKSYSTEM>'
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("xsa.json", json.dumps(meta))
        z.writestr("sysdef.xml", sysdef)
        z.writestr("system.hwh", hwh)
        if family == "zynqmp":
            z.writestr("psu_init.tcl", "proc psu_init {} {}")
        elif family == "zynq":
            z.writestr("ps7_init.tcl", "proc ps7_init {} {}")
        elif family == "versal":
            z.writestr("top.pdi", b"\0")
        if bit:
            z.writestr("top.bit", b"\0" * 16)
    return path
