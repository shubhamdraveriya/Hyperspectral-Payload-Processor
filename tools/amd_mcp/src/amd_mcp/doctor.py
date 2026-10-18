"""amd-mcp-doctor: check that both servers can reach their tools, without building anything.

    amd-mcp-doctor [--json] [--skip-vivado] [--skip-vitis] [--skip-xsdb]

Uses the same environment variables as the servers (VIVADO_PATH, VITIS_PATH, XSDB_PATH,
AMD_MCP_STATE_DIR). Exit code 0 when every check that ran passed.
"""

from __future__ import annotations

import argparse
import json
import sys
import time


def _check(report: list, name: str, func) -> None:
    t0 = time.monotonic()
    try:
        detail = func()
        report.append({"check": name, "ok": True, "detail": detail,
                       "seconds": round(time.monotonic() - t0, 1)})
    except Exception as exc:
        report.append({"check": name, "ok": False, "detail": f"{type(exc).__name__}: {exc}",
                       "seconds": round(time.monotonic() - t0, 1)})


def run(skip_vivado: bool = False, skip_vitis: bool = False, skip_xsdb: bool = False) -> list:
    from . import handoff
    from .paths import ensure_vivado_path, state_dir, sub_dir, vitis_root, vivado_root, xsdb_launcher

    ensure_vivado_path()
    report: list = []
    _check(report, "state folder", lambda: str(state_dir()))
    _check(report, "hand-off record", lambda: {
        "file": str(handoff.handoff_file()),
        "latest_export": (handoff.latest_export() or {}).get("xsa")})
    _check(report, "Vivado install", lambda: str(vivado_root()))
    _check(report, "Vitis install", lambda: str(vitis_root()))
    _check(report, "XSDB launcher", lambda: str(xsdb_launcher()))

    if not skip_vivado:
        def vivado():
            from vivado_mcp.vivado_session import VivadoSession

            session = VivadoSession(timeout=300)
            started = session.start()
            if not started.success:
                raise RuntimeError(started.output)
            try:
                version = session.run_tcl("version -short").output
                tcl = session.run_tcl("info patchlevel").output
            finally:
                session.stop()
            return f"Vivado {version}, Tcl {tcl}"
        _check(report, "Vivado Tcl session", vivado)

    if not skip_vitis:
        def vitis():
            from .vitis_session import VitisWorker

            worker = VitisWorker()
            try:
                reply = worker.call("info", timeout=300)
                if not reply.ok:
                    raise RuntimeError(reply.error)
                return {"python": reply.value["python"], "executable": reply.value["executable"],
                        "xilinx_vitis": reply.value["xilinx_vitis"]}
            finally:
                worker.stop()
        _check(report, "Vitis Python worker", vitis)

    if not skip_xsdb:
        def xsdb():
            from .tcl_session import TclSession

            session = TclSession("xsdb", xsdb_launcher(), [], sub_dir("logs"), cwd=sub_dir("xsdb"))
            session.start(timeout=180)
            try:
                return f"XSDB {session.run('version', timeout=60).result}"
            finally:
                session.stop()
        _check(report, "XSDB Tcl session", xsdb)
    return report


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument("--skip-vivado", action="store_true")
    parser.add_argument("--skip-vitis", action="store_true")
    parser.add_argument("--skip-xsdb", action="store_true")
    a = parser.parse_args(argv)
    report = run(a.skip_vivado, a.skip_vitis, a.skip_xsdb)
    if a.json:
        print(json.dumps(report, indent=2))
    else:
        for item in report:
            print(f"[{'OK' if item['ok'] else 'FAIL'}] {item['check']} ({item['seconds']} s)")
            print(f"     {item['detail']}")
    return 0 if all(item["ok"] for item in report) else 1


if __name__ == "__main__":
    raise SystemExit(main())
