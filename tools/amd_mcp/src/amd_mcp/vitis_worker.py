"""Runs inside Vitis's own Python (the vitis -s environment) for the Vitis MCP server.

Standard library and the Vitis Python API only: this file is copied to the state folder
and started by Vitis's Python 3.13, never imported by the server.

Protocol: each request arrives as one line of hex-encoded JSON on the original stdin,
{"id", "op", "kwargs", "reply"}. The worker prints __AMDMCP_BEGIN_<id>__, runs the op (its
output, and that of the build tools it starts, goes to stdout, which the server keeps as
the call's log), writes {"ok", "value" | "error", "traceback"} to the reply file and
prints __AMDMCP_END_<id>__. The Vitis client and its server live across requests.
"""

import glob
import io
import json
import os
import subprocess
import sys
import threading
import time
import traceback

import vitis

STATUS = {0: "SUCCESS", 1: "FAILURE", 2: "IN_PROGRESS", None: "UNKNOWN"}

_client = None
_workspace = None
_namespace = {}


# --------------------------------------------------------------------- streams
def _setup_streams():
    """Keep the request pipe private: fd 0 becomes NUL, so neither Python code nor the
    tools Vitis starts can read (and steal) requests."""
    requests = io.open(os.dup(0), "rb")
    null = os.open(os.devnull, os.O_RDONLY)
    os.dup2(null, 0)
    os.close(null)
    sys.stdin = open(os.devnull, "r")
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    return requests


def _emit(text):
    sys.stdout.write(text)
    sys.stdout.flush()


# --------------------------------------------------------------------- client
def _server_process():
    try:
        return _client._serverObj.cp
    except AttributeError:
        return None


def _drain_server():
    """vitis.create_client reads the server's port from its stdout pipe and then leaves
    the pipe unread; drain it to a log so a chatty server never blocks on a full pipe."""
    process = _server_process()
    if process is None or process.stdout is None:
        return
    log_path = os.environ.get("AMD_MCP_VITIS_SERVER_LOG") or os.devnull

    def pump():
        with open(log_path, "ab") as log:
            for line in iter(process.stdout.readline, b""):
                log.write(line)
                log.flush()

    threading.Thread(target=pump, daemon=True, name="vitis-server-drain").start()


def client():
    """The Vitis client, started on first use and again if its server has died."""
    global _client, _workspace
    process = _server_process()
    if _client is not None and process is not None and process.poll() is not None:
        print(f"Vitis server exited (code {process.poll()}); starting a new one")
        _client = None
        _workspace = None
    if _client is None:
        _client = vitis.create_client()
        _drain_server()
    return _client


def ensure_workspace(path):
    global _workspace
    path = os.path.abspath(path)
    if _workspace != path:
        os.makedirs(path, exist_ok=True)
        client().set_workspace(path=path)
        _workspace = path
    return path


def _stop_server():
    """Stop the Vitis server with its whole tree (the client only kills the cmd shell
    that started it, which leaves the Java server running on Windows)."""
    global _client, _workspace
    process = _server_process()
    if process is not None and process.poll() is None and os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, check=False)
    try:
        vitis.dispose()
    except Exception:
        pass
    _client = None
    _workspace = None


# --------------------------------------------------------------------- helpers
def _xpfm(workspace, platform):
    return os.path.join(workspace, platform, "export", platform, platform + ".xpfm")


def _platform_xpfm(workspace, platform):
    """platform: a name in the workspace or a path to an .xpfm."""
    if platform.lower().endswith(".xpfm"):
        return os.path.abspath(platform)
    xpfm = _xpfm(workspace, platform)
    if not os.path.isfile(xpfm):
        raise FileNotFoundError(f"platform '{platform}' has no export yet ({xpfm}): "
                                "build the platform first")
    return xpfm


def _component(name):
    comp = client().get_component(name=name)
    if comp is None:
        raise LookupError(f"no component '{name}' in workspace {_workspace}")
    return comp


def _status(value):
    return STATUS.get(value, str(value))


def _plain(value):
    """Protobuf messages (some API calls return them) as dicts, so the reply is JSON."""
    if hasattr(value, "DESCRIPTOR") and hasattr(value, "ListFields"):
        from google.protobuf.json_format import MessageToDict

        return MessageToDict(value, preserving_proto_field_name=True)
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    return value


def _component_type(location):
    for name in ("vitis-comp.json", "_ide/vitis-comp.json"):
        path = os.path.join(location, name)
        if os.path.isfile(path):
            try:
                with open(path, encoding="utf-8") as f:
                    data = json.load(f)
                return data.get("type") or data.get("componentType") or data.get("compType")
            except (OSError, ValueError):
                return None
    return None


def _elfs(workspace, name):
    return sorted(glob.glob(os.path.join(workspace, name, "build", "*.elf")),
                  key=os.path.getmtime, reverse=True)


# --------------------------------------------------------------------- operations
def op_ping():
    return "pong"


def op_info():
    process = _server_process()
    return {"python": sys.version.split()[0], "executable": sys.executable,
            "xilinx_vitis": os.environ.get("XILINX_VITIS"),
            "xilinx_vivado": os.environ.get("XILINX_VIVADO"),
            "workspace": _workspace, "client_started": _client is not None,
            "vitis_server_pid": process.pid if process is not None else None}


def op_set_workspace(workspace):
    return {"workspace": ensure_workspace(workspace)}


def op_list_components(workspace):
    ensure_workspace(workspace)
    c = client()
    components = []
    for item in c.list_components() or []:
        entry = dict(item) if isinstance(item, dict) else {"name": str(item)}
        location = entry.get("location") or os.path.join(_workspace, entry.get("name", ""))
        entry["type"] = _component_type(location)
        elfs = _elfs(_workspace, entry.get("name", ""))
        if elfs:
            entry["elf"] = elfs[0]
        xpfm = _xpfm(_workspace, entry.get("name", ""))
        if os.path.isfile(xpfm):
            entry["xpfm"] = xpfm
        components.append(entry)
    platforms = c.list_platform_components()
    return {"workspace": _workspace, "components": components, "platforms": platforms}


def op_processor_os_list(xsa):
    return client().get_processor_os_list(xsa=xsa)


def op_create_platform(workspace, name, xsa, cpu, os_name, domain_name, build, replace,
                       no_boot_bsp=False):
    ensure_workspace(workspace)
    c = client()
    if replace and os.path.exists(os.path.join(_workspace, name)):
        c.delete_component(name=name)
    kwargs = {"name": name, "hw_design": xsa, "os": os_name, "cpu": cpu}
    if domain_name:
        kwargs["domain_name"] = domain_name
    if no_boot_bsp:
        kwargs["no_boot_bsp"] = True
    platform = c.create_platform_component(**kwargs)
    result = {"name": name, "domains": _domains(platform)}
    if build:
        result["build_status"] = _status(platform.build())
        result["xpfm"] = _xpfm(_workspace, name)
        result["xpfm_exists"] = os.path.isfile(result["xpfm"])
    return result


def _domains(platform):
    try:
        return platform.list_domains()
    except Exception as exc:
        return f"list_domains failed: {exc}"


def op_update_platform_hw(workspace, name, xsa, build):
    ensure_workspace(workspace)
    platform = _component(name)
    platform.update_hw(hw_design=xsa)
    result = {"name": name, "xsa": xsa}
    if build:
        result["build_status"] = _status(platform.build())
        result["xpfm"] = _xpfm(_workspace, name)
        result["xpfm_exists"] = os.path.isfile(result["xpfm"])
    return result


def op_build_platform(workspace, name, clean):
    ensure_workspace(workspace)
    platform = _component(name)
    if clean:
        platform.clean()
    status = _status(platform.build())
    xpfm = _xpfm(_workspace, name)
    return {"name": name, "build_status": status, "xpfm": xpfm, "xpfm_exists": os.path.isfile(xpfm)}


def op_domain_info(workspace, platform, domain):
    ensure_workspace(workspace)
    p = _component(platform)
    info = {"platform": platform, "domains": _domains(p)}
    if domain:
        d = p.get_domain(domain)
        for field, getter in (("os", d.get_os), ("libs", d.get_libs), ("drivers", d.get_drivers),
                              ("applicable_libs", d.get_applicable_libs)):
            try:
                info[field] = getter()
            except Exception as exc:
                info[field] = f"failed: {exc}"
    return info


def op_configure_domain(workspace, platform, domain, add_libs, remove_libs, settings, build):
    ensure_workspace(workspace)
    p = _component(platform)
    d = p.get_domain(domain)
    for lib in add_libs:
        d.set_lib(lib_name=lib)
    for lib in remove_libs:
        d.remove_lib(lib_name=lib)
    applied = []
    for s in settings:
        d.set_config(option=s["option"], param=s["param"], value=str(s["value"]),
                     lib_name=s.get("lib_name"))
        applied.append(s)
    result = {"platform": platform, "domain": domain, "added_libs": add_libs,
              "removed_libs": remove_libs, "settings": applied}
    if build:
        result["build_status"] = _status(p.build())
    return result


def op_list_templates():
    return client().get_templates(type="EMBD_APP")


def op_create_app(workspace, name, platform, domain, template, sources, stack_size,
                  heap_size, replace):
    ensure_workspace(workspace)
    c = client()
    xpfm = _platform_xpfm(_workspace, platform)
    if replace and os.path.exists(os.path.join(_workspace, name)):
        c.delete_component(name=name)
    kwargs = {"name": name, "platform": xpfm, "template": template}
    if domain:
        kwargs["domain"] = domain
    app = c.create_app_component(**kwargs)
    imported = _import(app, sources)
    if stack_size or heap_size:
        ld = app.get_ld_script()
        if stack_size:
            ld.set_stack_size(stack_size)
        if heap_size:
            ld.set_heap_size(heap_size)
    return {"name": name, "platform": xpfm, "imported": imported}


def _import(app, sources):
    """Copy sources into the component: a folder's top-level files (as the project's own
    build script does), a single file, or the listed files of a folder."""
    imported = []
    for src in sources:
        from_loc = os.path.abspath(src["path"])
        files = src.get("files")
        if files is None and os.path.isdir(from_loc):
            files = sorted(f for f in os.listdir(from_loc)
                           if os.path.isfile(os.path.join(from_loc, f)))
        elif files is None:
            files = [os.path.basename(from_loc)]
            from_loc = os.path.dirname(from_loc)
        dest = src.get("dest", "src")
        app.import_files(from_loc=from_loc, files=files, dest_dir_in_cmp=dest)
        imported.append({"from": from_loc, "files": files, "dest": dest})
    return imported


def op_import_sources(workspace, name, sources):
    ensure_workspace(workspace)
    return {"name": name, "imported": _import(_component(name), sources)}


def op_build_app(workspace, name, clean):
    ensure_workspace(workspace)
    app = _component(name)
    started = time.time()
    if clean:
        app.clean()
    status = _status(app.build())
    elfs = _elfs(_workspace, name)
    fresh = [e for e in elfs if os.path.getmtime(e) >= started - 2]
    return {"name": name, "build_status": status, "elf": elfs[0] if elfs else None,
            "elf_rebuilt": bool(fresh), "elfs": elfs}


def op_delete_component(workspace, name):
    ensure_workspace(workspace)
    client().delete_component(name=name)
    return {"deleted": name}


def op_exec(code):
    """Run Python with vitis, client(), ensure_workspace() and the previous calls' names in
    scope; the value of `_result`, if set, is returned."""
    _namespace.update({"vitis": vitis, "client": client, "ensure_workspace": ensure_workspace,
                       "os": os, "sys": sys})
    _namespace.pop("_result", None)
    exec(compile(code, "<run_vitis_python>", "exec"), _namespace)
    return _namespace.get("_result")


def op_shutdown():
    _stop_server()
    return "stopped"


OPS = {name[3:]: func for name, func in globals().items() if name.startswith("op_")}


# --------------------------------------------------------------------- main loop
def _write_reply(path, reply):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(reply, f, default=repr)
    os.replace(tmp, path)


def main():
    requests = _setup_streams()
    _emit("__AMDMCP_WORKER_READY__\n")
    while True:
        line = requests.readline()
        if not line:
            break                      # the server has gone
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(bytes.fromhex(line.decode("ascii")).decode("utf-8"))
            rid, op, reply_path = request["id"], request["op"], request["reply"]
        except Exception as exc:
            _emit(f"\nworker: unreadable request ({exc})\n")
            continue
        _emit(f"\n__AMDMCP_BEGIN_{rid}__\n")
        started = time.monotonic()
        try:
            if op not in OPS:
                raise ValueError(f"unknown op {op!r}")
            reply = {"ok": True, "value": _plain(OPS[op](**request.get("kwargs", {})))}
        except BaseException as exc:   # SystemExit and KeyboardInterrupt from user code too
            reply = {"ok": False, "error": f"{type(exc).__name__}: {exc}",
                     "traceback": traceback.format_exc()}
        reply["elapsed_s"] = round(time.monotonic() - started, 3)
        sys.stdout.flush()
        sys.stderr.flush()
        try:
            _write_reply(reply_path, reply)
        except Exception as exc:
            _emit(f"\nworker: cannot write reply {reply_path}: {exc}\n")
        _emit(f"\n__AMDMCP_END_{rid}__\n")
        if op == "shutdown":
            break
    _stop_server()


if __name__ == "__main__":
    main()
