"""The Vivado MCP server: vivado-mcp-native's tools with the hand-off to Vitis.

Changes to the base server (vivado-mcp-native 0.2.1, pinned):
- export_hardware (new): writes the XSA (write_hw_platform -fixed, bitstream included by
  default) and records it in the hand-off, where the Vitis server picks it up.
- get_handoff (new): the hand-off record.
- generate_bitstream: synthesizes / resets out-of-date runs first, takes jobs and a
  timeout (the base used the 300 s command timeout, which stops Vivado mid-run).
- run_tcl, open_project, the simulation tools and generate_full_report take a timeout;
  the default is VIVADO_MCP_TIMEOUT (900 s) instead of 300 s.
- Tools other than the session ones start Vivado when it is not running.
- get_timing_summary parses Vivado's Design Timing Summary table (the base parser found
  nothing and reported met = false for every design); get_utilization reads its tables by
  column name (the base parser's numbers were shifted by 2025.2's Prohibited column).
- Repeated INFO/WARNING message IDs in a tool's output are collapsed to the first three
  and a count.
- get_host_status no longer tells the client to switch to another server below 64 GB.
- Tool calls run off the event loop, one at a time, so the server keeps answering.
"""

from __future__ import annotations

import asyncio
import copy
import functools
import json
import os
import threading
import time
from pathlib import Path

import anyio
from mcp.types import TextContent, Tool
from vivado_mcp import server as base
from vivado_mcp.vivado_session import get_session

from . import handoff, vivado_reports
from . import xsa as xsa_mod
from .paths import ensure_vivado_path

DEFAULT_TIMEOUT = float(os.environ.get("VIVADO_MCP_TIMEOUT", "900"))
SESSION_TOOLS = {"start_session", "stop_session", "session_status", "check_session_health",
                 "get_host_status", "get_handoff"}
TIMEOUT_TOOLS = {"run_tcl", "open_project", "launch_simulation", "run_simulation",
                 "restart_simulation", "step_simulation", "generate_full_report"}
_TIMEOUT_PROPERTY = {
    "type": "integer", "minimum": 1,
    "description": (f"Seconds to wait (default {DEFAULT_TIMEOUT:g}). On timeout Vivado is stopped "
                    "(the open project and design are lost), so give long commands enough."),
}

_base_list_tools = base.list_tools
_base_call_tool = base.call_tool
_call_lock = threading.Lock()


def _text(data: dict) -> list[TextContent]:
    return [TextContent(type="text", text=json.dumps(data, indent=2, default=str))]


def _tail(text: str, chars: int = 4000) -> str:
    return text if len(text) <= chars else "...\n" + text[-chars:]


# ------------------------------------------------------------------------ tool table
EXTRA_TOOLS = [
    Tool(
        name="export_hardware",
        description=(
            "Export the hardware platform (XSA) for Vitis and hand it over: runs "
            "write_hw_platform -fixed (bitstream included by default, which needs a completed "
            "write_bitstream; the implemented run is opened if needed) and records the XSA in "
            "the hand-off file, where the Vitis server's create_platform / sync_from_vivado "
            "take it as their default. Returns the XSA path, part, timing and the record."),
        inputSchema={
            "type": "object",
            "properties": {
                "xsa_path": {"type": "string", "description": (
                    "Where to write the XSA (default: <project dir>/<project>.xsa). Relative "
                    "paths are relative to the project directory.")},
                "include_bit": {"type": "boolean", "description": (
                    "Include the bitstream (default true). false exports the hardware "
                    "description only, which is enough to start software before implementation.")},
                "impl_run": {"type": "string", "description": "Implementation run (default impl_1)"},
                "timeout": {"type": "integer", "minimum": 1,
                            "description": "Seconds for opening the run and writing (default 1800)"},
            },
            "required": [],
        },
    ),
    Tool(
        name="get_handoff",
        description=("The Vivado -> Vitis hand-off record: the latest exported XSA (and whether "
                     "the file is unchanged since), and the Vitis platforms built from it."),
        inputSchema={"type": "object", "properties": {}, "required": []},
    ),
]

GENERATE_BITSTREAM = Tool(
    name="generate_bitstream",
    description=(
        "Generate the bitstream of the current project: runs synthesis first when it is "
        "out of date or not done, resets an out-of-date implementation, then runs "
        "implementation to write_bitstream. Does nothing when the bitstream is up to date. "
        "Follow with export_hardware to hand the design to Vitis."),
    inputSchema={
        "type": "object",
        "properties": {
            "jobs": {"type": "integer", "minimum": 1, "description": "Parallel jobs (default 4)"},
            "timeout": {"type": "integer", "minimum": 1,
                        "description": "Seconds for the whole flow (default 7200)"},
            "impl_run": {"type": "string", "description": "Implementation run (default impl_1)"},
        },
        "required": [],
    },
)


@base.server.list_tools()
async def list_tools() -> list[Tool]:
    tools = []
    for tool in await _base_list_tools():
        tool = copy.deepcopy(tool)
        if tool.name == "generate_bitstream":
            tool = GENERATE_BITSTREAM
        elif tool.name == "get_timing_summary":
            tool.description = ("Timing summary of the open design: WNS/TNS (setup), WHS/THS "
                                "(hold), WPWS/TPWS (pulse width), failing endpoints, and met "
                                "(Vivado's own verdict). detail_level standard/full adds the "
                                "report text. Needs an open synthesized or implemented design.")
        elif tool.name == "get_utilization":
            tool.description = ("Resource use of the open design: lut, ff, bram, uram, dsp, io "
                                "(used / available / percent) and every top-level row of the "
                                "report; hierarchical=true gives the per-instance table. "
                                "threshold_percent keeps rows at or above it.")
        elif tool.name == "get_host_status":
            tool.description = ("Hostname, free and total memory, and whether the Vivado "
                                "session is running.")
        elif tool.name == "start_session":
            tool.description = ("Start the persistent Vivado Tcl session (Vivado from the "
                                "VIVADO_PATH environment variable unless vivado_path is given). "
                                "Other tools start it on demand.")
        if tool.name in TIMEOUT_TOOLS:
            tool.inputSchema = copy.deepcopy(tool.inputSchema)
            tool.inputSchema.setdefault("properties", {})["timeout"] = _TIMEOUT_PROPERTY
        tools.append(tool)
    return tools + EXTRA_TOOLS


# ------------------------------------------------------------------------ Tcl helpers
def _value(session, command: str, timeout: float = 120) -> str:
    """Scalar result of a query command (its last output line), "" on failure."""
    r = session.run_tcl(command, timeout_override=timeout)
    if not r.success:
        return ""
    lines = [line.strip() for line in r.output.splitlines() if line.strip()]
    return lines[-1] if lines else ""


def _run_state(session, run: str) -> dict:
    return {"status": _value(session, f"get_property STATUS [get_runs {run}]"),
            "needs_refresh": _value(session, f"get_property NEEDS_REFRESH [get_runs {run}]") == "1"}


def _failed(status: str) -> bool:
    s = status.lower()
    return "error" in s or "failed" in s


def generate_bitstream(session, args: dict) -> dict:
    jobs = int(args.get("jobs", 4))
    run = args.get("impl_run") or "impl_1"
    deadline = time.monotonic() + float(args.get("timeout", 7200))
    remaining = lambda: max(1.0, deadline - time.monotonic())  # noqa: E731
    if not _value(session, "current_project -quiet"):
        return {"success": False, "error": "no project is open (open_project first)"}
    if not _value(session, f"get_runs -quiet {run}"):
        return {"success": False, "error": f"no run named {run}"}
    steps = []
    impl = _run_state(session, run)
    if "write_bitstream complete" in impl["status"].lower() and not impl["needs_refresh"]:
        return {"success": True, "message": "bitstream is up to date", "run_status": impl["status"],
                "next": "export_hardware"}
    synth = _value(session, f"get_property PARENT [get_runs {run}]") or "synth_1"
    s = _run_state(session, synth)
    if s["needs_refresh"] or "complete" not in s["status"].lower() or _failed(s["status"]):
        r = session.run_tcl(f"reset_run {synth}; launch_runs {synth} -jobs {jobs}; wait_on_run {synth}",
                            timeout_override=remaining())
        s = _run_state(session, synth)
        steps.append({"step": f"synthesis ({synth})", "status": s["status"], "output": _tail(r.output, 2000)})
        if "complete" not in s["status"].lower() or _failed(s["status"]):
            return {"success": False, "error": f"synthesis did not complete: {s['status']}",
                    "steps": steps}
        impl = _run_state(session, run)
    if impl["needs_refresh"] or _failed(impl["status"]):
        session.run_tcl(f"reset_run {run}", timeout_override=remaining())
        steps.append({"step": f"reset {run}", "previous_status": impl["status"]})
    r = session.run_tcl(f"launch_runs {run} -to_step write_bitstream -jobs {jobs}; wait_on_run {run}",
                        timeout_override=remaining())
    verification = base.verify_run_status(session, run)
    ok = "write_bitstream complete" in verification["status"].lower()
    steps.append({"step": f"implementation ({run}) to write_bitstream",
                  "status": verification["status"], "output": _tail(r.output, 3000)})
    out = {"success": ok, "run_status": verification["status"], "run_progress": verification["progress"],
           "steps": steps}
    if ok:
        out["next"] = "export_hardware"
    else:
        out["error"] = f"{run} did not reach write_bitstream: {verification['status']}"
    return out


def export_hardware(session, args: dict) -> dict:
    include_bit = bool(args.get("include_bit", True))
    run = args.get("impl_run") or "impl_1"
    timeout = float(args.get("timeout", 1800))
    project = _value(session, "current_project -quiet")
    project_dir = _value(session, "get_property DIRECTORY [current_project]") if project else ""
    if args.get("xsa_path"):
        target = Path(os.path.expandvars(os.path.expanduser(args["xsa_path"])))
        if not target.is_absolute():
            base_dir = project_dir or _value(session, "pwd")
            target = Path(base_dir) / target
    elif project:
        target = Path(project_dir) / f"{project}.xsa"
    else:
        return {"success": False, "error": "no project is open: open_project, or pass xsa_path "
                "for the design in memory"}
    target = target.resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    warnings, meta = [], {}
    if include_bit and project:
        state = _run_state(session, run)
        if "write_bitstream complete" not in state["status"].lower():
            return {"success": False, "error": (
                f"{run} has no bitstream (status: {state['status'] or 'unknown'}): run "
                "generate_bitstream first, or export with include_bit=false")}
        if state["needs_refresh"]:
            warnings.append(f"{run} is out of date with the sources: the exported bitstream is "
                            "from the previous run (generate_bitstream rebuilds it)")
        opened = session.run_tcl(
            f"if {{[get_designs -quiet {run}] eq {{}}}} {{open_run {run}}} else {{current_design {run}}}",
            timeout_override=timeout)
        # judged by the Tcl return code and the design, not by ERROR-looking message lines
        if opened.return_value != "0" or _value(session, f"get_designs -quiet {run}") != run:
            return {"success": False, "error": f"could not open {run}", "output": _tail(opened.output)}
        for stat in ("WNS", "TNS", "WHS", "THS"):
            meta[stat.lower()] = _value(session, f"get_property STATS.{stat} [get_runs {run}]")
        meta["impl_run"] = run
    flags = "-fixed -include_bit -force" if include_bit else "-fixed -force"
    started = time.time()
    written = session.run_tcl(f"write_hw_platform {flags} {{{target.as_posix()}}}",
                              timeout_override=timeout)
    fresh = target.is_file() and target.stat().st_mtime >= started - 2
    if written.return_value != "0" or not fresh:
        return {"success": False, "error": "write_hw_platform failed", "output": _tail(written.output)}
    info = xsa_mod.inspect(target, with_hash=False)
    if include_bit and not info["has_bitstream"]:
        warnings.append("the XSA has no bitstream although include_bit was set")
    xpr = (Path(project_dir) / f"{project}.xpr").as_posix() if project else None
    record = handoff.record_export(
        target, vivado_project=xpr, project=project or None,
        part=_value(session, "get_property PART [current_project]") if project else info["part"],
        board=(_value(session, "get_property BOARD_PART [current_project]") if project else None)
        or info["board"],
        top=_value(session, "get_property TOP [current_fileset]") if project else info["top"],
        include_bit=include_bit, has_bitstream=info["has_bitstream"], family=info["family"],
        vivado_version=_value(session, "version -short"), warnings=warnings, **meta)
    return {"success": True, "xsa": target.as_posix(), "handoff": record, "warnings": warnings,
            "next": ("vitis: create_platform (new platform; xsa defaults to this export) or "
                     "sync_from_vivado (existing platform and its applications)")}


def timing_summary(session, args: dict) -> dict:
    """report_timing_summary of the current design, parsed from its table (the base
    server's parser expected 'WNS(ns) : x' lines, which Vivado does not print)."""
    r = session.run_tcl("report_timing_summary -no_header -return_string")
    if r.return_value != "0":
        return {"success": False, "error": "report_timing_summary failed (is a synthesized or "
                "implemented design open? open_run impl_1)", "output": _tail(r.output, 2000)}
    out = {"success": True, "design": _value(session, "current_design -quiet"),
           **vivado_reports.timing_summary(r.output), "elapsed_ms": r.elapsed_ms}
    detail = args.get("detail_level", "summary")
    if detail in ("standard", "full"):
        out["raw"] = _tail(r.output, 4000 if detail == "standard" else 8000)
    return out


def utilization(session, args: dict) -> dict:
    """report_utilization parsed by column name (the base parser read Prohibited as
    Available and Available as Util% on Vivado 2025.2)."""
    cmd = "report_utilization -return_string"
    if args.get("hierarchical"):
        cmd += " -hierarchical"
        if args.get("module_filter"):
            cmd += f" -hierarchical_pattern {{{args['module_filter']}}}"
    r = session.run_tcl(cmd)
    if r.return_value != "0":
        return {"success": False, "error": "report_utilization failed (is a synthesized or "
                "implemented design open?)", "output": _tail(r.output, 2000)}
    out = {"success": True, "design": _value(session, "current_design -quiet"),
           **vivado_reports.utilization(r.output), "elapsed_ms": r.elapsed_ms}
    threshold = args.get("threshold_percent")
    if threshold is not None:
        out["resources"] = [x for x in out["resources"]
                            if isinstance(x["percent"], (int, float)) and x["percent"] >= threshold]
    if len(out.get("hierarchy", [])) > 60:
        out["hierarchy_truncated"] = len(out["hierarchy"])
        out["hierarchy"] = out["hierarchy"][:60]
    detail = args.get("detail_level", "summary")
    if detail in ("standard", "full"):
        out["raw"] = _tail(r.output, 4000 if detail == "standard" else 8000)
    return out


def _compacted(contents: list[TextContent]) -> list[TextContent]:
    """Base tool replies with repeated INFO/WARNING message IDs in "output" collapsed."""
    result = []
    for item in contents:
        try:
            data = json.loads(item.text)
        except (AttributeError, ValueError):
            result.append(item)
            continue
        if isinstance(data, dict) and isinstance(data.get("output"), str):
            data["output"] = vivado_reports.compact_messages(data["output"])
            item = TextContent(type="text", text=json.dumps(data, indent=2, default=str))
        result.append(item)
    return result


def _host_status(session) -> dict:
    import socket

    import psutil

    mem = psutil.virtual_memory()
    return {"hostname": socket.gethostname(),
            "memory_free_gb": round(mem.available / 1024 ** 3, 1),
            "memory_total_gb": round(mem.total / 1024 ** 3, 1),
            "memory_percent_used": mem.percent,
            "vivado_session_active": bool(session.is_running)}


# ------------------------------------------------------------------------ dispatch
def _call_sync(name: str, arguments: dict) -> list[TextContent]:
    with _call_lock:
        session = get_session()
        if name == "get_handoff":
            return _text({"success": True, **handoff.status()})
        if name == "get_host_status":
            return _text(_host_status(session))
        if name not in SESSION_TOOLS and not (session.is_running and session.process is not None
                                              and session.process.poll() is None):
            started = session.start()
            if not started.success:
                return _text({"success": False, "error": f"could not start Vivado: {started.output}"})
        if name == "export_hardware":
            return _text(export_hardware(session, arguments))
        if name == "generate_bitstream":
            return _text(generate_bitstream(session, arguments))
        if name == "get_timing_summary":
            return _text(timing_summary(session, arguments))
        if name == "get_utilization":
            return _text(utilization(session, arguments))
        arguments = dict(arguments)
        timeout = arguments.pop("timeout", None) if name in TIMEOUT_TOOLS else None
        session.timeout = float(timeout) if timeout else DEFAULT_TIMEOUT
        try:
            return _compacted(asyncio.run(_base_call_tool(name, arguments)))
        finally:
            session.timeout = DEFAULT_TIMEOUT


@base.server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    return await anyio.to_thread.run_sync(functools.partial(_call_sync, name, arguments or {}))


def main() -> None:
    ensure_vivado_path()
    get_session().timeout = DEFAULT_TIMEOUT
    asyncio.run(base.main())


if __name__ == "__main__":
    main()
