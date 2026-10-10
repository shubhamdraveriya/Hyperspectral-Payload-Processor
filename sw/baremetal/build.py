"""Build the bare-metal platform and applications with Vitis (2025.2).

    vitis -s sw/baremetal/build.py [path/to/hpdp.xsa]

The XSA defaults to build/hpdp.xsa (written by vivado/build.tcl). The workspace
is recreated from scratch at ~/hpdp_vitis, or at HPDP_VITIS_WS if set: Vitis
builds are unreliable in paths containing spaces, so it stays outside the repo.
"""

import os
import shutil
import sys
from pathlib import Path

import vitis

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent

PLATFORM = "hpdp_platform"
DOMAIN = "standalone_a53_0"
CPU = "psu_cortexa53_0"
APPS = ["dma_loopback"]

xsa = Path(sys.argv[1] if len(sys.argv) > 1 else REPO / "build" / "hpdp.xsa").resolve()
ws = Path(os.environ.get("HPDP_VITIS_WS", Path.home() / "hpdp_vitis")).resolve()
if not xsa.exists():
    sys.exit(f"XSA not found: {xsa} (run vivado/build.tcl first)")
if " " in str(ws):
    sys.exit(f"workspace path contains a space: {ws} (set HPDP_VITIS_WS)")

if ws.exists():
    shutil.rmtree(ws)
client = vitis.create_client()
client.set_workspace(path=str(ws))

# Vitis cannot read an XSA from a path with spaces either: work on a copy
local_xsa = ws / "hpdp.xsa"
shutil.copy2(xsa, local_xsa)

platform = client.create_platform_component(
    name=PLATFORM, hw_design=str(local_xsa), os="standalone", cpu=CPU, domain_name=DOMAIN)
platform.build()
xpfm = ws / PLATFORM / "export" / PLATFORM / f"{PLATFORM}.xpfm"

for name in APPS:
    src = HERE / name / "src"
    app = client.create_app_component(name=name, platform=str(xpfm), domain=DOMAIN,
                                      template="empty_application")
    app.import_files(from_loc=str(src), files=sorted(p.name for p in src.iterdir()),
                     dest_dir_in_cmp="src")
    app.build()
    print(f"built {ws / name / 'build' / (name + '.elf')}")

vitis.dispose()
