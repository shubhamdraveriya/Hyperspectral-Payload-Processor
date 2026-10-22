"""Finding the AMD tools, the state folder, and starting tool processes safely."""

from __future__ import annotations

import glob
import os
import re
import shutil
import signal
import subprocess
from pathlib import Path

WINDOWS = os.name == "nt"
BAT = ".bat" if WINDOWS else ""

# Variables that would make a child Python (Vitis's own) pick up this server's venv
_PYTHON_LEAKS = ("__PYVENV_LAUNCHER__", "PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV",
                 "PYTHONEXECUTABLE", "PYTHONSTARTUP")


def version_key(path: str | os.PathLike[str]) -> tuple[int, int]:
    """(year, minor) of the last 20yy.n in a path, (0, 0) if none."""
    found = re.findall(r"(?<!\d)(20\d{2})\.(\d+)(?!\d)", str(path))
    return (int(found[-1][0]), int(found[-1][1])) if found else (0, 0)


def _search_bases() -> list[str]:
    if WINDOWS:
        return [f"{d}:/" for d in "CDEFGHIJKLMNOPQRSTUVWXYZ" if os.path.exists(f"{d}:\\")]
    return ["/"]


def install_roots(tool: str) -> list[Path]:
    """Folders holding <tool>/bin (tool = "Vivado" or "Vitis"), newest release first.

    Covers both layouts: <base>/Xilinx/Vitis/2024.2 (up to 2024) and
    <base>/Xilinx/2025.2/Vitis (2025 on), under the usual prefixes, and <drive>:/2025.2/Vitis.
    """
    prefixes = ("Xilinx", "AMD", "AMDDesignTools", "Program Files/AMD", "Program Files/Xilinx",
                "tools/Xilinx", "tools/AMD", "opt/Xilinx", "opt/AMD")
    patterns = []
    for base in _search_bases():
        for prefix in prefixes:
            patterns.append(f"{base}{prefix}/{tool}/20*")
            patterns.append(f"{base}{prefix}/20*/{tool}")
        patterns.append(f"{base}20*/{tool}")
    found = {Path(p) for pattern in patterns for p in glob.glob(pattern)
             if (Path(p) / "bin" / f"{tool.lower()}{BAT}").is_file()}
    return sorted(found, key=version_key, reverse=True)


def _root_from(value: str, exe: str, origin: str) -> Path:
    """Install root (the folder holding bin/<exe>) from a launcher, bin folder or root."""
    path = Path(os.path.expandvars(os.path.expanduser(value.strip().strip('"'))))
    candidates = []
    if path.is_file():
        candidates.append(path.parent.parent)
    candidates += [path, path.parent]
    for root in candidates:
        if (root / "bin" / f"{exe}{BAT}").is_file():
            return root.resolve()
    raise FileNotFoundError(f"{origin}={value!r} does not lead to bin/{exe}{BAT}")


def vivado_root() -> Path | None:
    """Vivado install root from VIVADO_PATH, PATH or the usual folders; None if not found."""
    if os.environ.get("VIVADO_PATH", "").strip():
        return _root_from(os.environ["VIVADO_PATH"], "vivado", "VIVADO_PATH")
    found = shutil.which(f"vivado{BAT}")
    if found:
        return Path(found).resolve().parent.parent
    roots = install_roots("Vivado")
    return roots[0] if roots else None


def ensure_vivado_path() -> None:
    """Set VIVADO_PATH from this module's search when unset: vivado-mcp-native's own search
    covers only <drive>:/Xilinx and <drive>:/AMD folders (not e.g. D:/2025.2/Vivado)."""
    if os.environ.get("VIVADO_PATH", "").strip():
        return
    try:
        root = vivado_root()
    except FileNotFoundError:
        return
    if root is not None:
        os.environ["VIVADO_PATH"] = str(root / "bin" / f"vivado{BAT}")


def vitis_root() -> Path:
    """Vitis install root: VITIS_PATH, XILINX_VITIS, beside Vivado, PATH, the usual folders."""
    for var in ("VITIS_PATH", "XILINX_VITIS"):
        if os.environ.get(var, "").strip():
            return _root_from(os.environ[var], "vitis", var)
    try:
        vivado = vivado_root()
    except FileNotFoundError:
        vivado = None
    if vivado is not None:
        # 2025 layout: <r>/2025.2/Vivado -> <r>/2025.2/Vitis; older: <r>/Vivado/2024.2 -> <r>/Vitis/2024.2
        for sibling in (vivado.parent / "Vitis", vivado.parent.parent / "Vitis" / vivado.name):
            if (sibling / "bin" / f"vitis{BAT}").is_file():
                return sibling.resolve()
    found = shutil.which(f"vitis{BAT}")
    if found:
        return Path(found).resolve().parent.parent
    roots = install_roots("Vitis")
    if roots:
        return roots[0]
    raise FileNotFoundError(
        "Vitis was not found: set VITIS_PATH to the Vitis folder, e.g. D:\\2025.2\\Vitis")


def xsdb_launcher() -> Path:
    """XSDB launcher: XSDB_PATH, else Vitis's bin, else Vivado's bin."""
    if os.environ.get("XSDB_PATH", "").strip():
        path = Path(os.path.expandvars(os.path.expanduser(os.environ["XSDB_PATH"].strip())))
        if path.is_file():
            return path.resolve()
        raise FileNotFoundError(f"XSDB_PATH={os.environ['XSDB_PATH']!r} is not a file")
    roots = []
    try:
        roots.append(vitis_root())
    except FileNotFoundError:
        pass
    try:
        if vivado_root() is not None:
            roots.append(vivado_root())
    except FileNotFoundError:
        pass
    for root in roots:
        candidate = root / "bin" / f"xsdb{BAT}"
        if candidate.is_file():
            return candidate
    raise FileNotFoundError("xsdb was not found: set XSDB_PATH or VITIS_PATH")


def state_dir() -> Path:
    """Folder shared by both servers: hand-off record, worker scripts, logs, staging."""
    folder = Path(os.environ.get("AMD_MCP_STATE_DIR") or Path.home() / ".amd-mcp")
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def sub_dir(*parts: str) -> Path:
    folder = state_dir().joinpath(*parts)
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def prune(folder: Path, keep: int = 0, older_than_days: float | None = None) -> None:
    """Delete the oldest entries of a folder beyond `keep`, or those older than a number
    of days (staging copies of bitstreams and old logs would otherwise pile up)."""
    import time

    try:
        entries = sorted(folder.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        return
    cutoff = time.time() - older_than_days * 86400 if older_than_days is not None else None
    for index, entry in enumerate(entries):
        try:
            if (cutoff is not None and entry.stat().st_mtime < cutoff) or \
                    (cutoff is None and index >= keep):
                shutil.rmtree(entry) if entry.is_dir() else entry.unlink()
        except OSError:
            pass


def no_space_path(path: Path) -> Path:
    """The path itself if it has no space, else its 8.3 short form (Windows).

    The AMD launchers split arguments on spaces, and Vitis and XSDB fail on such paths.
    """
    text = str(path)
    if " " not in text:
        return path
    if WINDOWS:
        import ctypes

        buffer = ctypes.create_unicode_buffer(32768)
        length = ctypes.windll.kernel32.GetShortPathNameW(text, buffer, len(buffer))
        if 0 < length < len(buffer) and " " not in buffer.value:
            return Path(buffer.value)
    raise ValueError(f"path contains a space and has no short name: {text} "
                     "(set AMD_MCP_STATE_DIR to a folder without spaces)")


def child_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    """This process's environment without the variables that leak its Python into a tool's."""
    env = {k: v for k, v in os.environ.items() if k.upper() not in _PYTHON_LEAKS}
    if extra:
        env.update(extra)
    return env


def popen_flags() -> dict:
    """No console window, own process group (so a tree kill does not reach this server)."""
    if WINDOWS:
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW}
    return {"start_new_session": True}


def batch_command(launcher: Path, args: list[str]) -> str | list[str]:
    """Command for a .bat launcher: cmd /d /s /c "<launcher> <args>" (one string, so Python
    does not escape the inner quotes); a plain list elsewhere."""
    full = [str(launcher), *args]
    if WINDOWS and launcher.suffix.lower() in (".bat", ".cmd"):
        shell = os.environ.get("COMSPEC", "cmd.exe")
        return f'{subprocess.list2cmdline([shell])} /d /s /c "{subprocess.list2cmdline(full)}"'
    return full


def kill_tree(process: subprocess.Popen) -> None:
    """Stop a process and everything it started (Vivado, XSDB and Vitis start helpers)."""
    if process.poll() is None:
        if WINDOWS:
            subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                           stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, check=False,
                           creationflags=subprocess.CREATE_NO_WINDOW)
        else:
            try:
                os.killpg(os.getpgid(process.pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                process.kill()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        pass
