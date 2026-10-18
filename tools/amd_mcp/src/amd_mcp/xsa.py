"""Reading an XSA (a zip written by write_hw_platform) without starting any tool."""

from __future__ import annotations

import hashlib
import json
import os
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime
from pathlib import Path


def sha256_file(path: str | os.PathLike[str]) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _family(names: list[str]) -> str:
    """zynqmp (psu_init), zynq (ps7_init), versal (PDI) or fpga (no processing system)."""
    base = {Path(n).name.lower() for n in names}
    if "psu_init.tcl" in base:
        return "zynqmp"
    if "ps7_init.tcl" in base:
        return "zynq"
    if any(n.endswith(".pdi") for n in base):
        return "versal"
    return "fpga"


def inspect(path: str | os.PathLike[str], with_hash: bool = True) -> dict:
    """Part, board, top, tool version, bitstream and PS init files of an XSA."""
    xsa = Path(path).resolve()
    with zipfile.ZipFile(xsa) as z:
        names = z.namelist()
        meta = json.loads(z.read("xsa.json")) if "xsa.json" in names else {}
        sysdef = ET.fromstring(z.read("sysdef.xml")) if "sysdef.xml" in names else None
    board = meta.get("board", {})
    info = {
        "path": xsa.as_posix(),
        "size": xsa.stat().st_size,
        "modified": datetime.fromtimestamp(xsa.stat().st_mtime).isoformat(timespec="seconds"),
        "part": board.get("part"),
        "board": board.get("boardPart") or board.get("name"),
        "top": meta.get("topModuleName"),
        "vivado_version": meta.get("generatedVersion"),
        "generated": meta.get("generatedTimestamp"),
        "family": _family(names),
        "bitstream": next((n for n in names if n.lower().endswith(".bit")), None),
        "ps_init_tcl": next((n for n in names if Path(n).name.lower() in
                             ("psu_init.tcl", "ps7_init.tcl")), None),
        "pdi": next((n for n in names if n.lower().endswith(".pdi")), None),
        "hwh": [n for n in names if n.lower().endswith(".hwh")],
        "files": names,
    }
    if sysdef is not None:
        system = sysdef.find("SYSTEMINFO")
        if system is not None:
            info["part"] = info["part"] or system.get("PART")
            info["board"] = info["board"] or system.get("BOARD")
        hierarchy = sysdef.find("HIERARCHY")
        if hierarchy is not None:
            info["top"] = info["top"] or hierarchy.get("Name")
        tool = sysdef.find("TOOL_VERSION")
        if tool is not None:
            info["vivado_version"] = info["vivado_version"] or tool.get("Version")
    info["has_bitstream"] = info["bitstream"] is not None
    if with_hash:
        info["sha256"] = sha256_file(xsa)
    return info


SOFT_CPUS = ("microblaze", "microblaze_riscv")


def soft_processors(path: str | os.PathLike[str]) -> list[str]:
    """Instance names of the MicroBlaze (and MicroBlaze V) cores in the XSA's .hwh files."""
    found = []
    with zipfile.ZipFile(path) as z:
        for name in z.namelist():
            if not name.lower().endswith(".hwh"):
                continue
            try:
                root = ET.fromstring(z.read(name))
            except ET.ParseError:
                continue
            for module in root.iter("MODULE"):
                if module.get("MODTYPE") in SOFT_CPUS and module.get("INSTANCE"):
                    if module.get("INSTANCE") not in found:
                        found.append(module.get("INSTANCE"))
    return found


def default_cpu(info: dict) -> str | None:
    """The CPU Vitis calls the first application core, from the XSA alone; None when only
    Vitis can tell (Versal generations differ)."""
    if info["family"] == "zynqmp":
        return "psu_cortexa53_0"
    if info["family"] == "zynq":
        return "ps7_cortexa9_0"
    if info["family"] == "fpga":
        cores = soft_processors(info["path"])
        return cores[0] if cores else None
    return None


def extract(path: str | os.PathLike[str], member: str, dest_dir: Path) -> Path:
    """Copy one member of the XSA to dest_dir (flat) and return its path."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    target = dest_dir / Path(member).name
    with zipfile.ZipFile(path) as z:
        target.write_bytes(z.read(member))
    return target


def find(directory: str | os.PathLike[str], max_depth: int = 4, limit: int = 50) -> list[dict]:
    """XSA files under a folder, newest first, with their summary (no hash)."""
    root = Path(directory).resolve()
    found = []
    for current, dirs, files in os.walk(root):
        depth = len(Path(current).relative_to(root).parts)
        if depth >= max_depth:
            dirs[:] = []
        # Vivado project folders hold copies of the XSA inside runs and caches
        dirs[:] = [d for d in dirs if not d.startswith(".") and not d.endswith((".cache", ".gen"))]
        for name in files:
            if name.lower().endswith(".xsa"):
                found.append(Path(current) / name)
    found.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    summaries = []
    for xsa in found[:limit]:
        try:
            item = inspect(xsa, with_hash=False)
            item.pop("files", None)
            summaries.append(item)
        except (zipfile.BadZipFile, OSError, ValueError, ET.ParseError) as exc:
            summaries.append({"path": xsa.as_posix(), "error": f"unreadable XSA: {exc}"})
    return summaries
