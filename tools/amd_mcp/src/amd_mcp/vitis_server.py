"""MCP server for AMD Vitis (unified IDE Python API) and XSDB (JTAG), fed by Vivado's XSA.

The flow it serves:
    vivado: export_hardware  -> XSA written and recorded in the hand-off
    vitis:  create_platform / sync_from_vivado (XSA defaults to that export)
            -> create_app, build_app -> ELF
            -> program_and_run (bitstream and PS init from the same XSA, the ELF) over JTAG
"""

from __future__ import annotations

import atexit
import functools
import json
import os
import re
import shutil
import sys
import uuid
from pathlib import Path
from typing import Any

import anyio
from mcp.server.fastmcp import FastMCP

from . import __version__, handoff
from . import xsa as xsa_mod
from .paths import (ensure_vivado_path, no_space_path, prune, state_dir, sub_dir, vitis_root,
                    xsdb_launcher)
from .tcl_session import TclSession
from .vitis_session import VitisWorker, tail

INSTRUCTIONS = """\
Vitis embedded software flow and JTAG runs. The XSA comes from the Vivado server's
export_hardware (recorded in a hand-off file), so after a hardware change: vivado
export_hardware, then vitis sync_from_vivado (re-imports the XSA into the platform,
rebuilds it and its applications). New design: set_workspace, create_platform (XSA defaults
to the latest export), create_app, build_app, then program_and_run. Workspace and XSA paths
must not contain spaces for Vitis; XSA files are copied into the workspace. Every call
returns ok, and on failure error / error_lines; full logs are in the files named "log".
"""

mcp = FastMCP("vitis", instructions=INSTRUCTIONS)

_worker = VitisWorker()
_xsdb: TclSession | None = None
_xsdb_connected: str | None = None
_state: dict[str, Any] = {"workspace": os.environ.get("AMD_MCP_VITIS_WORKSPACE") or None}

PREFERRED_CPUS = ("psu_cortexa53_0", "ps7_cortexa9_0", "psv_cortexa72_0", "psx_cortexa78_0",
                  "microblaze_0", "microblaze_riscv_0")


# ------------------------------------------------------------------------ helpers
async def _in_thread(func, *args, **kwargs):
    """Run blocking tool work off the event loop, so the server keeps answering."""
    return await anyio.to_thread.run_sync(functools.partial(func, *args, **kwargs))


def _guard(func):
    """Turn exceptions into an {"ok": false, "error"} reply instead of a protocol error."""
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception as exc:
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    return wrapper


def _abs(path: str) -> Path:
    p = Path(os.path.expandvars(os.path.expanduser(path.strip().strip('"'))))
    return (p if p.is_absolute() else Path.cwd() / p).resolve()


def _workspace(workspace: str | None) -> Path:
    if workspace:
        ws = _abs(workspace)
    elif _state["workspace"]:
        ws = Path(_state["workspace"])
    else:
        raise ValueError("no workspace: call set_workspace first or pass workspace=")
    if " " in str(ws):
        raise ValueError(
            f"Vitis cannot use a workspace path with spaces: {ws}. Make a junction without "
            f'spaces, e.g. cmd /c mklink /J C:\\vitis_ws "{ws}", and use that path')
    return ws


def _reply(r, **extra) -> dict:
    out = r.as_dict()
    out.update(extra)
    return out


def _resolve_xsa(xsa: str | None) -> tuple[Path, dict | None, list[str]]:
    """An explicit XSA, else the latest export in the hand-off."""
    warnings: list[str] = []
    if xsa:
        path = _abs(xsa)
        if not path.is_file():
            raise FileNotFoundError(f"XSA not found: {path}")
        return path, None, warnings
    latest = handoff.export_state(handoff.latest_export())
    if latest is None:
        raise ValueError("no xsa given, and the hand-off has no export yet: run the Vivado "
                         "server's export_hardware, or pass xsa=")
    if latest["file_state"] == "missing":
        raise FileNotFoundError(f"the latest exported XSA is gone: {latest['xsa']}")
    if latest["file_state"] != "unchanged":
        warnings.append(f"{latest['xsa']} {latest['file_state']}")
    return Path(latest["xsa"]), latest, warnings


def _same_file(a: str | Path, b: str | Path) -> bool:
    return Path(a).resolve().as_posix().lower() == Path(b).resolve().as_posix().lower()


def _resolve_platform_xsa(ws: Path, platform: str,
                          xsa: str | None) -> tuple[Path, dict | None, list[str]]:
    """The XSA for re-importing into a platform: xsa= if given, else the file the platform
    was made from (re-exported by Vivado in the usual flow), else the latest export."""
    if xsa:
        return _resolve_xsa(xsa)
    record = handoff.platform_record(ws, platform)
    if not record or not Path(record["xsa"]).is_file():
        return _resolve_xsa(None)
    source = Path(record["xsa"])
    latest = handoff.latest_export()
    notes: list[str] = []
    export = latest if latest and _same_file(latest["xsa"], source) else None
    if latest and export is None and latest["exported_at"] > record.get("updated_at", ""):
        notes.append(f"Vivado's latest export is another file ({latest['xsa']}); this platform "
                     f"follows {source.as_posix()}. Pass xsa= to move it to the new one.")
    return source, export, notes


def _stage_xsa(ws: Path, platform: str, source: Path) -> Path:
    """Copy the XSA into the workspace: Vitis cannot read one from a path with spaces, and
    Vivado may overwrite the original with the next export."""
    folder = ws / "_handoff" / platform
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / source.name.replace(" ", "_")
    if source.resolve() != target.resolve():
        shutil.copy2(source, target)
    return target


def _pick_cpu(listing: Any, os_name: str) -> str | None:
    """A CPU from Vitis's processor/OS list ({"os_processors": [{"os", "cpuInstance"}]}),
    for os_name, preferring the first application core; never the PMU."""
    names: list[str] = []
    entries = listing.get("os_processors") if isinstance(listing, dict) else None
    for entry in entries or []:
        if isinstance(entry, dict) and entry.get("os") == os_name:
            cpus = entry.get("cpuInstance") or entry.get("cpu_instance") or []
            names += [cpus] if isinstance(cpus, str) else list(cpus)
    if not names:
        names = re.findall(r"\b(?:psu|ps7|psv|psx|microblaze)\w*", json.dumps(listing))
    names = [n for n in names if "pmu" not in n.lower()]
    for cpu in PREFERRED_CPUS:
        if cpu in names:
            return cpu
    return names[0] if names else None


def _app_entries(ws: Path, platform: str) -> list[dict]:
    data = handoff.load()["apps"]
    return [a for a in data.values()
            if a.get("workspace") == ws.as_posix() and a.get("platform") == platform]


# ------------------------------------------------------------------------ info / hand-off
@mcp.tool()
async def vitis_info() -> dict[str, Any]:
    """Vitis and XSDB install paths, worker and JTAG session state, current workspace."""
    def work():
        info: dict[str, Any] = {"ok": True, "server_version": __version__,
                                "server_cwd": str(Path.cwd()), "state_dir": str(state_dir()),
                                "workspace": _state["workspace"]}
        try:
            info["vitis_root"] = str(vitis_root())
        except FileNotFoundError as exc:
            info["ok"] = False
            info["vitis_error"] = str(exc)
        try:
            info["xsdb"] = str(xsdb_launcher())
        except FileNotFoundError as exc:
            info["xsdb_error"] = str(exc)
        if _worker.environment is not None:
            info["vitis_python"] = str(_worker.environment.python)
            info["vitis_python_version"] = _worker.environment.python_version
        info["worker"] = {"running": _worker.running, "started_at": _worker.started_at,
                          "log": str(_worker.session_log) if _worker.session_log else None}
        info["xsdb_session"] = {"running": bool(_xsdb and _xsdb.running),
                                "connected": _xsdb_connected,
                                "log": str(_xsdb.log_path) if _xsdb and _xsdb.log_path else None}
        latest = handoff.latest_export()
        info["latest_export"] = latest["xsa"] if latest else None
        return info
    return await _in_thread(_guard(work))


@mcp.tool()
async def handoff_status() -> dict[str, Any]:
    """What Vivado exported last (XSA, time, part, timing) and, for each Vitis platform and
    application built through this server, which XSA it came from; stale = true means the
    platform's XSA file has been re-exported since its build (run sync_from_vivado)."""
    return await _in_thread(_guard(lambda: {"ok": True, **handoff.status()}))


@mcp.tool()
async def inspect_xsa(path: str | None = None, processors: bool = False) -> dict[str, Any]:
    """Describe an XSA: part, board, top, Vivado version, bitstream, PS init (family zynqmp /
    zynq / versal / fpga) and the default CPU. path: an .xsa file, a folder (lists the
    XSAs under it, newest first) or omitted (the latest export). processors=true also asks
    Vitis for the full processor/OS list (starts the Vitis worker; takes minutes)."""
    def work():
        if path and _abs(path).is_dir():
            return {"ok": True, "xsa_files": xsa_mod.find(_abs(path))}
        target, _, warnings = _resolve_xsa(path)
        info = xsa_mod.inspect(target)
        info.pop("files", None)
        result = {"ok": True, **info, "default_cpu": xsa_mod.default_cpu(info)}
        if warnings:
            result["warnings"] = warnings
        if processors:
            staged = no_space_path(sub_dir("xsa_cache")) / f"{info['sha256'][:12]}.xsa"
            if not staged.exists():
                shutil.copy2(target, staged)
            reply = _worker.call("processor_os_list", timeout=900, xsa=str(staged))
            result["processor_os"] = reply.value if reply.ok else reply.error
        return result
    return await _in_thread(_guard(work))


# ------------------------------------------------------------------------ workspace
@mcp.tool()
async def set_workspace(path: str) -> dict[str, Any]:
    """Select (and create) the Vitis workspace used by the other tools when they get no
    workspace= argument. The path must not contain spaces."""
    def work():
        ws = _workspace(path)
        ws.mkdir(parents=True, exist_ok=True)
        r = _worker.call("set_workspace", timeout=600, workspace=str(ws))
        if r.ok:
            _state["workspace"] = str(ws)
        return _reply(r)
    return await _in_thread(_guard(work))


@mcp.tool()
async def list_components(workspace: str | None = None) -> dict[str, Any]:
    """Platforms and applications in the workspace (with .xpfm / .elf when built)."""
    def work():
        return _reply(_worker.call("list_components", timeout=600,
                                   workspace=str(_workspace(workspace))))
    return await _in_thread(_guard(work))


@mcp.tool()
async def list_app_templates() -> dict[str, Any]:
    """Embedded application templates (e.g. empty_application, hello_world)."""
    return await _in_thread(_guard(lambda: _reply(_worker.call("list_templates", timeout=600))))


# ------------------------------------------------------------------------ platform
@mcp.tool()
async def create_platform(name: str, xsa: str | None = None, cpu: str | None = None,
                          os: str = "standalone", domain_name: str | None = None,
                          build: bool = True, replace: bool = False,
                          workspace: str | None = None, timeout: int = 3600) -> dict[str, Any]:
    """Create a platform component from an XSA (default: the latest Vivado export) with one
    domain, and build it. cpu: e.g. psu_cortexa53_0, psu_cortexr5_0, ps7_cortexa9_0,
    microblaze_0 (default: picked from the XSA's processors, A53 #0 first). os: standalone,
    freertos or linux. replace=true deletes an existing component of that name first."""
    def work():
        ws = _workspace(workspace)
        source, export, warnings = _resolve_xsa(xsa)
        staged = _stage_xsa(ws, name, source)
        # from the XSA itself when it can tell (Vitis's own listing takes minutes)
        chosen = cpu or xsa_mod.default_cpu(xsa_mod.inspect(staged, with_hash=False))
        if not chosen:
            listing = _worker.call("processor_os_list", timeout=900, xsa=str(staged))
            if not listing.ok:
                return _reply(listing, step="processor_os_list")
            chosen = _pick_cpu(listing.value, os)
            if not chosen:
                return {"ok": False, "error": f"no processor for os={os} in the XSA; pass cpu=",
                        "processor_os": listing.value}
        r = _worker.call("create_platform", timeout=timeout, workspace=str(ws), name=name,
                         xsa=str(staged), cpu=chosen, os_name=os, domain_name=domain_name,
                         build=build, replace=replace)
        ok = r.ok and (not build or (r.value or {}).get("build_status") == "SUCCESS")
        out = _reply(r, ok=ok, cpu=chosen, xsa=str(source), staged_xsa=str(staged))
        if warnings:
            out["warnings"] = warnings
        if r.ok:
            handoff.record_platform(ws, name, xsa=str(source), sha256=xsa_mod.sha256_file(staged),
                                    staged_xsa=str(staged), cpu=chosen, os=os,
                                    export_id=export["id"] if export else None,
                                    built=bool(build and ok),
                                    xpfm=(r.value or {}).get("xpfm"))
        return out
    return await _in_thread(_guard(work))


@mcp.tool()
async def update_platform_hw(name: str, xsa: str | None = None, build: bool = True,
                             workspace: str | None = None, timeout: int = 3600) -> dict[str, Any]:
    """Re-import hardware into an existing platform and rebuild it. Default XSA: the file the
    platform was made from (Vivado re-exports to it), else the latest export. Applications
    must be rebuilt afterwards (sync_from_vivado does both)."""
    def work():
        ws = _workspace(workspace)
        return _update_platform(name, _resolve_platform_xsa(ws, name, xsa), build, ws, timeout)
    return await _in_thread(_guard(work))


def _update_platform(name: str, resolved: tuple[Path, dict | None, list[str]], build: bool,
                     ws: Path, timeout: int) -> dict:
    source, export, warnings = resolved
    staged = _stage_xsa(ws, name, source)
    r = _worker.call("update_platform_hw", timeout=timeout, workspace=str(ws), name=name,
                     xsa=str(staged), build=build)
    ok = r.ok and (not build or (r.value or {}).get("build_status") == "SUCCESS")
    out = _reply(r, ok=ok, xsa=str(source), staged_xsa=str(staged))
    if warnings:
        out["warnings"] = warnings
    if r.ok:
        old = handoff.platform_record(ws, name) or {}
        handoff.record_platform(ws, name, xsa=str(source), sha256=xsa_mod.sha256_file(staged),
                                staged_xsa=str(staged), cpu=old.get("cpu"), os=old.get("os"),
                                export_id=export["id"] if export else None,
                                built=bool(build and ok), xpfm=(r.value or {}).get("xpfm"))
    return out


@mcp.tool()
async def build_platform(name: str, clean: bool = False, workspace: str | None = None,
                         timeout: int = 3600) -> dict[str, Any]:
    """Build (generate) a platform component; clean=true cleans it first."""
    def work():
        r = _worker.call("build_platform", timeout=timeout,
                         workspace=str(_workspace(workspace)), name=name, clean=clean)
        return _reply(r, ok=r.ok and (r.value or {}).get("build_status") == "SUCCESS")
    return await _in_thread(_guard(work))


@mcp.tool()
async def domain_info(platform: str, domain: str | None = None,
                      workspace: str | None = None) -> dict[str, Any]:
    """Domains of a platform; with domain=, its OS, libraries, drivers and applicable libs."""
    def work():
        return _reply(_worker.call("domain_info", timeout=600, workspace=str(_workspace(workspace)),
                                   platform=platform, domain=domain))
    return await _in_thread(_guard(work))


@mcp.tool()
async def configure_domain(platform: str, domain: str, add_libs: list[str] | None = None,
                           remove_libs: list[str] | None = None,
                           settings: list[dict[str, Any]] | None = None, build: bool = False,
                           workspace: str | None = None, timeout: int = 3600) -> dict[str, Any]:
    """Add/remove BSP libraries and set BSP parameters of a domain, e.g.
    add_libs=["lwip220", "xilffs"], settings=[{"option": "lib", "lib_name": "xilffs",
    "param": "XILFFS_use_lfn", "value": "1"}, {"option": "os", "param":
    "standalone_stdin", "value": "psu_uart_1"}]. build=true rebuilds the platform after."""
    def work():
        items = settings or []
        for s in items:
            missing = {"option", "param", "value"} - set(s)
            if missing:
                raise ValueError(f"setting {s} lacks {sorted(missing)}")
        r = _worker.call("configure_domain", timeout=timeout, workspace=str(_workspace(workspace)),
                         platform=platform, domain=domain, add_libs=add_libs or [],
                         remove_libs=remove_libs or [], settings=items, build=build)
        return _reply(r, ok=r.ok and (not build or (r.value or {}).get("build_status") == "SUCCESS"))
    return await _in_thread(_guard(work))


# ------------------------------------------------------------------------ applications
def _sources(sources: list[dict[str, Any] | str] | None) -> list[dict]:
    items = []
    for s in sources or []:
        item = {"path": s} if isinstance(s, str) else dict(s)
        if "path" not in item:
            raise ValueError(f"source {s} has no path")
        path = _abs(item["path"])
        if not path.exists():
            raise FileNotFoundError(f"source not found: {path}")
        item["path"] = str(path)
        items.append(item)
    return items


@mcp.tool()
async def create_app(name: str, platform: str, domain: str | None = None,
                     template: str = "empty_application",
                     sources: list[dict[str, Any] | str] | None = None,
                     stack_size: str | None = None, heap_size: str | None = None,
                     replace: bool = False, build: bool = False,
                     workspace: str | None = None, timeout: int = 3600) -> dict[str, Any]:
    """Create an application component on a built platform (name in the workspace, or an
    .xpfm path). sources: folders or files to import, as paths or {"path", "files": [...],
    "dest": "src"} (a folder imports its top-level files). stack_size / heap_size: e.g.
    "0x10000". build=true builds it right away."""
    def work():
        ws = _workspace(workspace)
        r = _worker.call("create_app", timeout=600, workspace=str(ws), name=name,
                         platform=platform, domain=domain, template=template,
                         sources=_sources(sources), stack_size=stack_size,
                         heap_size=heap_size, replace=replace)
        if not r.ok or not build:
            return _reply(r)
        built = _build_app(name, False, ws, timeout)
        return {"ok": built["ok"], "create": _reply(r), "build": built}
    return await _in_thread(_guard(work))


@mcp.tool()
async def import_sources(name: str, sources: list[dict[str, Any] | str],
                         workspace: str | None = None) -> dict[str, Any]:
    """Import (copy) more source files or folders into an application, e.g. after editing
    sources outside the workspace. Same source format as create_app."""
    def work():
        return _reply(_worker.call("import_sources", timeout=600,
                                   workspace=str(_workspace(workspace)), name=name,
                                   sources=_sources(sources)))
    return await _in_thread(_guard(work))


def _app_component(ws: Path, name: str) -> tuple[str | None, str | None]:
    """(platform name, cpu) of an application, from its vitis-comp.json
    ({"platform": "<name or .xpfm path>", "cpuInstance": "psu_cortexa53_0", ...})."""
    try:
        data = json.loads((ws / name / "vitis-comp.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None, None
    platform = data.get("platform") or None
    if isinstance(platform, str) and platform.lower().endswith(".xpfm"):
        platform = Path(platform).stem
    return platform, data.get("cpuInstance") or None


def _build_app(name: str, clean: bool, ws: Path, timeout: int) -> dict:
    r = _worker.call("build_app", timeout=timeout, workspace=str(ws), name=name, clean=clean)
    value = r.value or {}
    ok = r.ok and value.get("build_status") == "SUCCESS" and bool(value.get("elf"))
    out = _reply(r, ok=ok)
    if ok:
        platform, cpu = _app_component(ws, name)
        record = handoff.platform_record(ws, platform) if platform else None
        handoff.record_app(ws, name, elf=value["elf"], platform=platform,
                           xsa=(record or {}).get("staged_xsa"),
                           xsa_sha256=(record or {}).get("sha256"),
                           cpu=cpu or (record or {}).get("cpu"))
        if record and handoff.source_changed(record):
            out["warning"] = (f"the XSA platform '{platform}' was built from ({record['xsa']}) "
                              "has changed since: run sync_from_vivado to build against it")
    return out


@mcp.tool()
async def build_app(name: str, clean: bool = False, workspace: str | None = None,
                    timeout: int = 3600) -> dict[str, Any]:
    """Build an application; returns the ELF path, build status and compiler error lines.
    The ELF becomes program_and_run's default."""
    return await _in_thread(_guard(lambda: _build_app(name, clean, _workspace(workspace), timeout)))


@mcp.tool()
async def sync_from_vivado(platform: str, apps: list[str] | None = None, xsa: str | None = None,
                           force: bool = False, workspace: str | None = None,
                           timeout: int = 3600) -> dict[str, Any]:
    """The Vivado -> Vitis step after a hardware change: re-import the platform's XSA (the
    file it was made from, which export_hardware rewrites; or xsa= to move it to another),
    rebuild the platform, then rebuild its applications (apps= names; default: those built
    on it through this server). Skips the platform when that XSA is unchanged since its
    build, unless force=true."""
    def work():
        ws = _workspace(workspace)
        resolved = _resolve_platform_xsa(ws, platform, xsa)
        source = resolved[0]
        record = handoff.platform_record(ws, platform)
        result: dict[str, Any] = {"platform": platform, "xsa": str(source)}
        if resolved[2]:
            result["warnings"] = resolved[2]
        if not (ws / platform).is_dir():
            return {"ok": False, "error": f"platform '{platform}' does not exist in {ws}: "
                    "create it with create_platform"}
        current = bool(record and record.get("built")
                       and record.get("sha256") == xsa_mod.sha256_file(source))
        if current and not force:
            result["platform_step"] = "up to date with this XSA"
        else:
            step = _update_platform(platform, resolved, True, ws, timeout)
            result["platform_step"] = step
            if not step["ok"]:
                return {"ok": False, **result}
        names = apps if apps is not None else sorted({a["name"] for a in _app_entries(ws, platform)})
        result["apps"] = {}
        ok = True
        for name in names:
            built = _build_app(name, False, ws, timeout)
            result["apps"][name] = built
            ok = ok and built["ok"]
        if not names:
            result["note"] = "no applications recorded for this platform; pass apps=[...]"
        return {"ok": ok, **result}
    return await _in_thread(_guard(work))


@mcp.tool()
async def clean_component(name: str, workspace: str | None = None) -> dict[str, Any]:
    """Clean an application or platform build (the component stays)."""
    def work():
        code = ("c = client().get_component(name=_name)\n"
                "c.clean()\n"
                "_result = 'cleaned ' + _name\n")
        ws = _workspace(workspace)
        r = _worker.call("exec", timeout=1800,
                         code=f"ensure_workspace({str(ws)!r})\n_name = {name!r}\n" + code)
        return _reply(r)
    return await _in_thread(_guard(work))


@mcp.tool()
async def delete_component(name: str, workspace: str | None = None) -> dict[str, Any]:
    """Delete a component (platform or application) and its folder from the workspace."""
    def work():
        return _reply(_worker.call("delete_component", timeout=600,
                                   workspace=str(_workspace(workspace)), name=name))
    return await _in_thread(_guard(work))


# ------------------------------------------------------------------------ escape hatches
@mcp.tool()
async def run_vitis_python(code: str, timeout: int = 1800) -> dict[str, Any]:
    """Run Python in the persistent Vitis worker (Vitis's Python 3.13 with the vitis module).
    In scope: vitis, client() (the started Vitis client), ensure_workspace(path), os, sys,
    and names from earlier calls. Set _result to return a value. Example:
    ensure_workspace(r"C:/ws"); _result = client().list_components()"""
    return await _in_thread(_guard(lambda: _reply(_worker.call("exec", timeout=timeout, code=code))))


def _predefined_names(root: Path) -> set[str]:
    path = root / "cli" / "vitis" / "_predefined_classes.txt"
    try:
        return set(re.findall(r"'([^']+)'", path.read_text(encoding="utf-8", errors="replace")))
    except OSError:
        return set()


@mcp.tool()
async def run_vitis_script(script: str, args: list[str] | None = None, cwd: str | None = None,
                           timeout: int = 7200) -> dict[str, Any]:
    """Run a Vitis Python script as its own process, like `vitis -s script args` (e.g. a
    project's build script). Arguments may contain spaces. cwd defaults to this server's
    working directory (see vitis_info)."""
    def work():
        path = _abs(script)
        if not path.is_file():
            raise FileNotFoundError(f"script not found: {path}")
        env = _worker.env()
        if path.stem in _predefined_names(env.root):
            raise ValueError(f"'{path.name}' has the name of a module Vitis imports; rename it")
        return _worker.run_script(path, list(args or []), _abs(cwd) if cwd else None, timeout)
    return await _in_thread(_guard(work))


@mcp.tool()
async def stop_vitis() -> dict[str, Any]:
    """Stop the Vitis worker and its Vitis server (the next Vitis call starts them again)."""
    def work():
        message = _worker.stop()
        return {"ok": True, "message": message}
    return await _in_thread(_guard(work))


# ------------------------------------------------------------------------ JTAG (XSDB)
def _xsdb_session() -> TclSession:
    global _xsdb, _xsdb_connected
    if _xsdb is None:
        _xsdb = TclSession("xsdb", xsdb_launcher(), [], sub_dir("logs"),
                           cwd=sub_dir("xsdb"))
    if not _xsdb.running:
        _xsdb_connected = None
        _xsdb.start(timeout=180)
    return _xsdb


def _xsdb_connect(url: str | None) -> dict:
    global _xsdb_connected
    session = _xsdb_session()
    wanted = url or "local"
    if _xsdb_connected == wanted:
        return {"ok": True, "connected": wanted}
    r = session.run("connect" + (f" -url {{{url}}}" if url else ""), timeout=120)
    if not r.ok:
        return {"ok": False, "step": "connect", "error": r.error or r.result, "output": r.output}
    _xsdb_connected = wanted
    return {"ok": True, "connected": wanted, "channel": r.result, "output": r.output}


def _tcl_reply(r) -> dict:
    out = {"ok": r.ok, "result": tail(r.result, 8000), "elapsed_s": r.elapsed_s}
    if r.output:
        out["output"] = tail(r.output, 8000)
    if not r.ok:
        out["error"] = tail(r.error, 4000)
    return out


@mcp.tool()
async def xsdb(command: str, timeout: int = 300) -> dict[str, Any]:
    """Run XSDB Tcl in a persistent session (started on first use), e.g. "connect",
    "targets", "targets -set -filter {name =~ \\"*A53*#0\\"}; mrd 0xFF000000 4". Returns the
    Tcl result and printed output. On timeout the session is stopped."""
    def work():
        session = _xsdb_session()
        return _tcl_reply(session.run(command, timeout=timeout))
    return await _in_thread(_guard(work))


_TARGET_LINE = re.compile(r"^(\s*)(\d+)(\*?)\s+(.+?)\s*$")


def _parse_targets(text: str) -> list[dict]:
    targets = []
    for line in text.splitlines():
        m = _TARGET_LINE.match(line)
        if not m:
            continue
        name, state = m.group(4), None
        sm = re.match(r"^(.*?)\s*\((.*)\)$", name)
        if sm:
            name, state = sm.group(1), sm.group(2)
        targets.append({"id": int(m.group(2)), "name": name, "state": state,
                        "selected": bool(m.group(3)), "indent": len(m.group(1))})
    return targets


@mcp.tool()
async def jtag_targets(url: str | None = None) -> dict[str, Any]:
    """Connect to hw_server (local by default, or url like tcp:host:3121) and list the
    JTAG chain and the debug targets. Read-only: nothing is reset or programmed."""
    def work():
        connected = _xsdb_connect(url)
        if not connected["ok"]:
            return connected
        session = _xsdb_session()
        chain = session.run("jtag targets", timeout=60)
        targets = session.run("targets", timeout=60)
        out = {"ok": chain.ok and targets.ok, "connected": connected["connected"],
               "jtag_chain": chain.result or chain.error,
               "targets_text": targets.result or targets.error,
               "targets": _parse_targets(targets.result) if targets.ok else []}
        if chain.ok and not chain.result.strip():
            out["note"] = ("no JTAG cable or device found: check the board's power and its "
                           "USB-JTAG cable (and that no other tool holds the cable)")
        return out
    return await _in_thread(_guard(work))


def _processor_filter(cpu: str | None, family: str) -> str:
    if cpu and cpu.startswith("filter:"):
        return cpu[len("filter:"):]
    patterns = ((r"cortexa53_(\d)", "*A53*#{}"), (r"cortexr5_(\d)", "*R5*#{}"),
                (r"cortexa9_(\d)", "*A9*#{}"), (r"cortexa72_(\d)", "*A72*#{}"),
                (r"microblaze(?:_riscv)?_(\d)", "*MicroBlaze*#{}"))
    for regex, pattern in patterns:
        m = re.search(regex, cpu or "", re.I)
        if m:
            return pattern.format(m.group(1))
    return {"zynqmp": "*A53*#0", "zynq": "*A9*#0"}.get(family, "*MicroBlaze*#0")


def _jtag_steps(family: str, bit: str | None, init: str | None, elf: str, target: str,
                jtag_boot_mode: bool, data: list[tuple[str, int]], run: bool) -> list[tuple[str, str]]:
    sel = lambda pattern: f'targets -set -nocase -filter {{name =~ "{pattern}"}}'  # noqa: E731
    loads = [(f"load {Path(f).name}", f"dow -data {{{f}}} 0x{a:08X}") for f, a in data]
    tail_steps = [("select processor", sel(target)), ("processor reset", "rst -processor"),
                  *loads, ("download ELF", f"dow {{{elf}}}")]
    if run:
        tail_steps.append(("run", "con"))
    if family == "zynqmp":   # as sw/host/run_m4.py, proven on the ZCU106
        steps = [("select PSU", sel("PSU"))]
        if jtag_boot_mode:
            steps.append(("boot mode JTAG until power-off", "mwr 0xFF5E0200 0x0100"))
        steps += [("system reset", "rst -system"), ("settle", "after 2000")]
        if bit:
            steps.append(("program FPGA", f"fpga -file {{{bit}}}"))
        steps += [("select APU", sel("APU*")), ("load psu_init", f"source {{{init}}}"),
                  ("psu_init", "psu_init"), ("settle", "after 1000"),
                  ("PS-PL isolation removal", "psu_ps_pl_isolation_removal"),
                  ("settle", "after 1000"), ("PS-PL reset", "psu_ps_pl_reset_config"),
                  ("protection", "catch {psu_protection}")]
        return steps + tail_steps
    if family == "zynq":
        steps = [("select APU", sel("APU*")), ("system reset", "rst -system"),
                 ("settle", "after 3000")]
        if bit:
            steps.append(("program FPGA", f"fpga -file {{{bit}}}"))
        steps += [("select APU", sel("APU*")), ("load ps7_init", f"source {{{init}}}"),
                  ("ps7_init", "ps7_init"), ("ps7_post_config", "ps7_post_config")]
        return steps + tail_steps
    # fpga: soft processor, configured with the bitstream
    steps = [("program FPGA", f"fpga -file {{{bit}}}"), ("settle", "after 1000")] if bit else []
    return steps + tail_steps


@mcp.tool()
async def program_and_run(elf: str | None = None, xsa: str | None = None,
                          processor: str | None = None, program_fpga: bool = True,
                          jtag_boot_mode: bool = True, run: bool = True,
                          data: list[dict[str, Any]] | None = None, url: str | None = None,
                          step_timeout: int = 300) -> dict[str, Any]:
    """Load and start an application over JTAG (volatile, nothing is written to flash):
    reset, bitstream and PS init from the XSA, optional data files, the ELF, then run.
    Defaults: elf = the last built by build_app; xsa = that app's platform XSA, else the
    latest Vivado export; processor from the platform's cpu (or psu_cortexa53_0-style name,
    or "filter:<xsdb target filter>"). data: [{"file": path, "address": "0x10000000"}].
    jtag_boot_mode (Zynq UltraScale+): set boot mode to JTAG so a flash image cannot start.
    Supports Zynq UltraScale+, Zynq-7000 and MicroBlaze designs. This resets the board."""
    def work():
        app = None
        if elf:
            elf_path = _abs(elf)
        else:
            app = handoff.latest_app()
            if app is None:
                raise ValueError("no elf given and no application built through build_app yet")
            elf_path = Path(app["elf"])
        if not elf_path.is_file():
            raise FileNotFoundError(f"ELF not found: {elf_path}")
        if xsa:
            xsa_path = _abs(xsa)
        elif app and app.get("xsa") and Path(app["xsa"]).is_file():
            xsa_path = Path(app["xsa"])
        else:
            xsa_path, _, _ = _resolve_xsa(None)
        info = xsa_mod.inspect(xsa_path, with_hash=False)
        family = info["family"]
        if family == "versal":
            raise ValueError("Versal boots from a PDI (device program); use the xsdb tool")
        if family != "fpga" and not info["ps_init_tcl"]:
            raise ValueError(f"{xsa_path} has no PS init Tcl")
        if program_fpga and not info["bitstream"]:
            raise ValueError(f"{xsa_path} has no bitstream: export it with include_bit, "
                             "or pass program_fpga=false")
        # XSDB fails on paths with spaces: stage everything in a folder without them
        stage_root = no_space_path(sub_dir("xsdb", "stage"))
        prune(stage_root, keep=4)
        stage = stage_root / uuid.uuid4().hex[:8]
        stage.mkdir(parents=True)
        bit = xsa_mod.extract(xsa_path, info["bitstream"], stage).as_posix() \
            if program_fpga else None
        init = xsa_mod.extract(xsa_path, info["ps_init_tcl"], stage).as_posix() \
            if info["ps_init_tcl"] else None
        staged_elf = stage / elf_path.name.replace(" ", "_")
        shutil.copy2(elf_path, staged_elf)
        loads = []
        for item in data or []:
            src = _abs(item["file"])
            if not src.is_file():
                raise FileNotFoundError(f"data file not found: {src}")
            dst = stage / src.name.replace(" ", "_")
            shutil.copy2(src, dst)
            address = item["address"]
            loads.append((dst.as_posix(), int(address, 0) if isinstance(address, str) else int(address)))
        cpu = processor or (app or {}).get("cpu")
        target = _processor_filter(cpu, family)
        steps = _jtag_steps(family, bit, init, staged_elf.as_posix(), target, jtag_boot_mode,
                            loads, run)
        connected = _xsdb_connect(url)
        if not connected["ok"]:
            return connected
        session = _xsdb_session()
        done = []
        for label, command in steps:
            r = session.run(command, timeout=step_timeout)
            entry = {"step": label, "command": command, "ok": r.ok, "elapsed_s": r.elapsed_s}
            if r.output:
                entry["output"] = tail(r.output, 1500)
            if not r.ok:
                entry["error"] = tail(r.error or r.result, 2000)
                done.append(entry)
                return {"ok": False, "failed_step": label, "steps": done, "elf": str(elf_path),
                        "xsa": str(xsa_path), "family": family, "target_filter": target}
            done.append(entry)
        return {"ok": True, "running": run, "elf": str(elf_path), "xsa": str(xsa_path),
                "family": family, "target_filter": target, "steps": done,
                "stage_dir": str(stage)}
    return await _in_thread(_guard(work))


@mcp.tool()
async def stop_xsdb() -> dict[str, Any]:
    """Stop the XSDB session (hw_server it started may keep running, as with Vitis)."""
    def work():
        global _xsdb_connected
        _xsdb_connected = None
        return {"ok": True, "message": _xsdb.stop() if _xsdb else "not running"}
    return await _in_thread(_guard(work))


# ------------------------------------------------------------------------ entry point
def _cleanup() -> None:
    try:
        _worker.stop()
    except Exception:
        pass
    try:
        if _xsdb is not None:
            _xsdb.stop()
    except Exception:
        pass


def main() -> None:
    for stream in (sys.stderr,):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    atexit.register(_cleanup)
    ensure_vivado_path()
    prune(sub_dir("logs"), older_than_days=14)
    prune(sub_dir("xsa_cache"), keep=10)
    prune(sub_dir("vitis", "replies"), older_than_days=1)
    mcp.run()


if __name__ == "__main__":
    main()
