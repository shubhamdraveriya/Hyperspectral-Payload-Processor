# amd_mcp: Vivado and Vitis MCP servers with an XSA hand-off

Two MCP servers for Claude Code, so one conversation can go from RTL to a running
application on the board:

| Server | What it drives | Tools |
|---|---|---|
| `vivado` (`amd_mcp.vivado_server`) | a persistent Vivado Tcl session ([vivado-mcp-native](https://github.com/Arthurzxy/vivado_mcp_native) 0.2.1, extended) | the 40 base tools (project, synthesis, implementation, timing, utilization, xsim, `run_tcl`, ...) plus `export_hardware`, `get_handoff` |
| `vitis` (`amd_mcp.vitis_server`) | a persistent worker in Vitis's own Python (unified IDE API), and a persistent XSDB session | platforms, domains, applications, builds, `sync_from_vivado`, JTAG `program_and_run`, raw `xsdb`, `run_vitis_python`, `run_vitis_script` |

The two meet in the **hand-off file** (`~/.amd-mcp/handoff.json`): `export_hardware`
writes the XSA (`write_hw_platform -fixed`, bitstream included) and records it there; the
Vitis tools take the latest export as their default XSA and record which XSA (SHA-256)
each platform and ELF was built from, so a platform older than the hardware shows as
`stale`.

```
vivado  generate_bitstream ─► export_hardware ──► handoff.json ◄── handoff_status / get_handoff
                                                      │
vitis   create_platform (new) or sync_from_vivado (after a hardware change)
          ─► create_app ─► build_app (ELF) ─► program_and_run (bitstream + psu_init + ELF, JTAG)
```

## Install and register

```
uv tool install --python 3.11 tools/amd_mcp
amd-mcp-register        # or: --vivado D:\2025.2\Vivado\bin\vivado.bat --vitis D:\2025.2\Vitis
amd-mcp-doctor
```

`amd-mcp-register` finds Vivado and Vitis (or takes the paths), and adds the `vivado` and
`vitis` servers at user scope (`--scope`, `--dry-run` to see the entries) with this
installation's Python run as `python -I -m amd_mcp.<server>`: `-I` keeps the project folder,
the servers' working directory, off Python's module path. New Claude Code sessions load
them. `amd-mcp-doctor` starts Vivado, the Vitis worker and XSDB once each and reports what
it found (`--skip-vivado`, `--skip-vitis`, `--skip-xsdb`, `--json`).

After changing the source: `uv tool install --reinstall --python 3.11 tools/amd_mcp`, then
a new session.

Installing from inside the Claude desktop app (a packaged app): its writes to `%APPDATA%`
are redirected into the app's own storage, which a terminal outside the app does not see
and an app reset deletes. So there, install with the tool folders under the profile, as
on this computer:

```
set UV_TOOL_DIR=%USERPROFILE%\.local\share\uv\tools
set UV_TOOL_BIN_DIR=%USERPROFILE%\.local\bin
uv tool install --python 3.11 tools/amd_mcp
```

`amd-mcp-register` registers the Python it runs from, so the entries follow either choice.

## Environment variables

| Variable | Used by | Meaning |
|---|---|---|
| `VIVADO_PATH` | both | `...\Vivado\bin\vivado.bat` (else PATH, else the usual install folders) |
| `VITIS_PATH` | vitis | the Vitis folder, e.g. `D:\2025.2\Vitis` (else `XILINX_VITIS`, beside Vivado, PATH, the usual folders) |
| `XSDB_PATH` | vitis | `xsdb.bat` (default: Vitis's, else Vivado's) |
| `VIVADO_MCP_TIMEOUT` | vivado | default seconds per Tcl command, 900 (the base server had 300) |
| `AMD_MCP_STATE_DIR` | both | hand-off file, logs, worker scripts, JTAG staging (default `~/.amd-mcp`) |
| `AMD_MCP_VITIS_WORKSPACE` | vitis | initial workspace (otherwise `set_workspace`) |

## A session, as Claude would run it

```
vivado: open_project  build/vivado/hpdp.xpr
vivado: generate_bitstream  jobs=8          # synthesizes first if out of date
vivado: export_hardware  xsa_path=C:/.../build/hpdp.xsa
vitis:  set_workspace  C:/Users/<you>/hpdp_vitis_ws
vitis:  create_platform  name=hpdp_platform   # xsa = that export, cpu = psu_cortexa53_0
vitis:  configure_domain  platform=hpdp_platform domain=standalone_psu_cortexa53_0 add_libs=["xilffs"] ...
vitis:  create_app  name=hpdp_run platform=hpdp_platform sources=["sw/baremetal/hpdp_run/src", "sw/baremetal/common"] build=true
vitis:  program_and_run                       # ELF just built, bitstream + psu_init from the same XSA
... change the RTL ...
vivado: generate_bitstream, export_hardware
vitis:  sync_from_vivado  platform=hpdp_platform   # re-imports the XSA, rebuilds platform and hpdp_run
```

The project's own scripts still work through the servers: `vivado run_tcl
"source vivado/build.tcl"` (with a long `timeout`) and `vitis run_vitis_script
sw/baremetal/build.py args=["build/m7h/hpdp.xsa"]`.

## Changes to the base Vivado server

`amd_mcp.vivado_server` replaces these parts of vivado-mcp-native 0.2.1 (pinned, since it
hooks the base's tool table):

| Tool | Base behaviour | Here |
|---|---|---|
| `generate_bitstream` | the 300 s command timeout, which stops Vivado mid-run | jobs and timeout (7200 s); synthesizes or resets out-of-date runs first; "up to date" when nothing changed |
| `get_timing_summary` | looked for `WNS(ns) : x` lines, which Vivado does not print: always null, `met: false` | parses the Design Timing Summary table and Vivado's own met / not met line |
| `get_utilization` | numbers shifted by 2025.2's Prohibited column (e.g. 0 LUTs available) | tables read by column name; adds URAM and every top-level row |
| `run_tcl`, `open_project`, simulation tools, `generate_full_report` | fixed 300 s | `timeout` argument, default `VIVADO_MCP_TIMEOUT` (900 s) |
| `get_host_status` | told the client to use another server below 64 GB free | plain status |
| all | blocked the server during long commands; needed `start_session` first | run off the event loop; Vivado starts on demand; repeated INFO/WARNING IDs (e.g. one Board 49-26 warning per board of an absent device on `open_project`) collapsed |

## Behaviour worth knowing

- Every tool answers with `ok` / `success`; on failure `error` and, for builds,
  `error_lines` (compiler and make errors). Full logs are written to `~/.amd-mcp/logs/`
  and named in the reply (`log`).
- A command that times out stops its process (Vivado, the Vitis worker or XSDB): it may be
  stuck mid-command. The next call starts it again; the open Vivado project is lost.
- Vivado tools other than the session ones start Vivado when it is not running.
- Vitis cannot use paths with spaces: workspaces with spaces are refused (make a junction),
  XSAs are copied into `<workspace>/_handoff/<platform>/`, JTAG files into
  `~/.amd-mcp/xsdb/stage/`.
- The Vitis worker is Vitis's own Python 3.13 started with the environment `vitis -s`
  sets up (captured once per server start), so scripts and arguments with spaces are safe.
- `program_and_run` resets the board and loads over JTAG only (volatile; nothing is
  written to flash). The Zynq UltraScale+ sequence is the one of `sw/host/run_m4.py`,
  including the JTAG boot-mode override. Zynq-7000 and MicroBlaze are supported; Versal
  is not (use the `xsdb` tool).
- `jtag_targets` only lists the chain and targets.

## Tests

```
cd tools/amd_mcp
uv run --no-project --python 3.11 --with pytest --with "mcp>=1.20,<2" python -m pytest -q
```

The unit tests cover the XSA reader, the hand-off record and the platform-follows-its-XSA
rule, the Tcl framing (run on a real Tcl 8.6 interpreter through tkinter), the report
parsers (including build/m7h/reports) and the JTAG step sequences.

Checked by hand on 2026-10-18 with Vivado / Vitis 2025.2 (Embedded edition) through the
MCP protocol, as Claude Code drives it: a ZCU106 design (PS + AXI GPIO on the LEDs) made
with `run_tcl`, `generate_bitstream`, `export_hardware` (with and without bitstream);
`create_platform` from that export (CPU from the XSA) and from build/m7h/hpdp.xsa;
`hello_world` and an AXI GPIO application built (the BSP's `XPAR_AXI_GPIO_0_BASEADDR`
matched Vivado's address map); re-export, then `sync_from_vivado` rebuilt the platform and
both applications and skipped the unchanged m7h platform; `run_vitis_script` arguments
with spaces, `=`, `,`, `;`, `!`; XSDB session, `jtag_targets` on a local hw_server.
Not run: `program_and_run` on a board (none was connected), Zynq-7000, MicroBlaze.
