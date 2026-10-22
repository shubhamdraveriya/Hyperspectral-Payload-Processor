"""amd-mcp-register: register the vivado and vitis servers with Claude Code.

    amd-mcp-register [--vivado <vivado.bat>] [--vitis <Vitis folder>] [--scope user]
                     [--dry-run]

Finds Vivado and Vitis as the servers do when the paths are not given, and runs
`claude mcp add-json` for each server with this installation's Python (replacing an entry
of the same name in that scope). Arguments go to the CLI as a list, so the JSON survives
Windows quoting.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from .paths import BAT, vitis_root, vivado_root


def configs(vivado: Path, vitis: Path) -> dict[str, dict]:
    python = sys.executable          # this installation's Python
    return {
        "vivado": {"type": "stdio", "command": python,
                   "args": ["-I", "-m", "amd_mcp.vivado_server"],
                   "env": {"VIVADO_PATH": str(vivado)}},
        "vitis": {"type": "stdio", "command": python,
                  "args": ["-I", "-m", "amd_mcp.vitis_server"],
                  "env": {"VITIS_PATH": str(vitis), "VIVADO_PATH": str(vivado)}},
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--vivado", help="vivado launcher (default: found like the servers do)")
    parser.add_argument("--vitis", help="Vitis folder (default: found like the servers do)")
    parser.add_argument("--scope", default="user", choices=("user", "local", "project"))
    parser.add_argument("--dry-run", action="store_true", help="print the entries only")
    a = parser.parse_args(argv)

    if a.vivado:
        os.environ["VIVADO_PATH"] = a.vivado
    if a.vitis:
        os.environ["VITIS_PATH"] = a.vitis
    try:
        root = vivado_root()
        vivado = (root / "bin" / f"vivado{BAT}").resolve() if root else None
        if vivado is None or not vivado.is_file():
            raise FileNotFoundError("Vivado was not found")
        os.environ["VIVADO_PATH"] = str(vivado)     # lets vitis_root look beside it
        vitis = vitis_root()
    except FileNotFoundError as exc:
        print(f"{exc}: pass --vivado <...\\bin\\vivado.bat> and --vitis <Vitis folder>",
              file=sys.stderr)
        return 1

    entries = configs(vivado, vitis)
    if a.dry_run:
        print(json.dumps(entries, indent=2))
        return 0
    claude = shutil.which("claude")
    if claude is None:
        print("the claude CLI is not on PATH; add these entries by hand:", file=sys.stderr)
        print(json.dumps(entries, indent=2))
        return 1
    status = 0
    for name, entry in entries.items():
        subprocess.run([claude, "mcp", "remove", name, "-s", a.scope],
                       stdin=subprocess.DEVNULL, capture_output=True)
        done = subprocess.run([claude, "mcp", "add-json", "-s", a.scope, name, json.dumps(entry)],
                              stdin=subprocess.DEVNULL, capture_output=True, text=True)
        print(f"{name}: {(done.stdout or done.stderr).strip()}")
        status |= done.returncode
    if status == 0:
        print("Start a new Claude Code session to load them; check with `claude mcp list` "
              "and amd-mcp-doctor.")
    return status


if __name__ == "__main__":
    raise SystemExit(main())
