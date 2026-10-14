"""Build sd_put (write files from DDR to the SD card) with Vitis (2025.2).

    vitis -s sw/baremetal/build_sd_put.py [path/to/hpdp.xsa]

A workspace of its own, build/vitis_sdput, reached through the junction
~/hpdp_sdput_ws (no spaces, docs/m0_bringup.md issue 3): a standalone platform
with FatFs read-write (hpdp_run's is read-only). The PL design does not matter;
only the PS configuration (DDR, SD controller) is used. Then sw/host/sd_put.py.
"""

import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import vitis

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
REPO_WS = REPO / "build" / "vitis_sdput"
LINK = Path.home() / "hpdp_sdput_ws"
PLATFORM, DOMAIN, CPU, APP = "sdput_platform", "standalone_a53_0", "psu_cortexa53_0", "sd_put"


def _remove_readonly(func, path, _exc):
    os.chmod(path, stat.S_IWRITE)
    func(path)


xsa = Path(sys.argv[1] if len(sys.argv) > 1 else REPO / "build" / "m7h" / "hpdp.xsa").resolve()
if not xsa.exists():
    sys.exit(f"XSA not found: {xsa}")
REPO_WS.mkdir(parents=True, exist_ok=True)
if not os.path.lexists(LINK):
    subprocess.run(["cmd", "/c", "mklink", "/J", str(LINK), str(REPO_WS)], check=True, capture_output=True)
if os.path.realpath(LINK) != os.path.realpath(REPO_WS):
    sys.exit(f"{LINK} exists but does not lead to {REPO_WS}")
for child in LINK.iterdir():                     # start from scratch, through the junction
    if child.is_dir() and not child.is_symlink():
        shutil.rmtree(child, onexc=_remove_readonly)
    else:
        child.unlink()

client = vitis.create_client()
client.set_workspace(path=str(LINK))
local_xsa = LINK / "hpdp.xsa"
shutil.copy2(xsa, local_xsa)
platform = client.create_platform_component(
    name=PLATFORM, hw_design=str(local_xsa), os="standalone", cpu=CPU, domain_name=DOMAIN)
domain = platform.get_domain(DOMAIN)
domain.set_lib(lib_name="xilffs")
domain.set_config(option="lib", param="XILFFS_use_lfn", value="1", lib_name="xilffs")
domain.set_config(option="lib", param="XILFFS_read_only", value="false", lib_name="xilffs")
platform.build()
xpfm = LINK / PLATFORM / "export" / PLATFORM / f"{PLATFORM}.xpfm"

src = HERE / APP / "src"
app = client.create_app_component(name=APP, platform=str(xpfm), domain=DOMAIN, template="empty_application")
app.import_files(from_loc=str(src), files=sorted(p.name for p in src.iterdir()), dest_dir_in_cmp="src")
app.build()
print(f"built {LINK / APP / 'build' / (APP + '.elf')}")
vitis.dispose()
