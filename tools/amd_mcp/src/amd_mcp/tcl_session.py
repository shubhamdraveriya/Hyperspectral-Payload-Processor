"""A persistent Tcl tool process (XSDB) driven through stdin, one framed command at a time.

Each command travels hex-encoded inside a small Tcl wrapper that prints a begin marker,
runs the command under catch at global level, and prints the result, the error stack and
the return code hex-encoded, then an end marker. Markers carry a fresh UUID, so prompts,
banners and asynchronous messages can never be mistaken for a reply. Needs Tcl 8.6
(binary encode/decode hex), which XSDB 2019.2 and later have.
"""

from __future__ import annotations

import codecs
import os
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .paths import batch_command, child_env, kill_tree, popen_flags


@dataclass
class TclResult:
    ok: bool
    result: str        # the command's Tcl return value
    output: str        # what it printed
    error: str         # Tcl error stack when not ok
    elapsed_s: float


class TclSession:
    def __init__(self, name: str, launcher: Path, args: list[str], log_dir: Path,
                 cwd: Path | None = None):
        self.name = name
        self.launcher = launcher
        self.args = args
        self.log_dir = log_dir
        self.cwd = cwd
        self.process: subprocess.Popen | None = None
        self.log_path: Path | None = None
        self.started_at: str | None = None
        self._lock = threading.Lock()
        self._cond = threading.Condition()
        self._buffer = ""
        self._reader_error: str | None = None

    # ----------------------------------------------------------------- process
    @property
    def running(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def _reader(self, process: subprocess.Popen, log) -> None:
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        fd = process.stdout.fileno()
        try:
            while True:
                chunk = os.read(fd, 65536)
                if not chunk:
                    break
                log.write(chunk)
                log.flush()
                text = decoder.decode(chunk)
                with self._cond:
                    self._buffer += text
                    self._cond.notify_all()
        except OSError as exc:
            with self._cond:
                self._reader_error = str(exc)
        finally:
            log.close()
            with self._cond:
                self._cond.notify_all()

    def start(self, timeout: float = 120) -> str:
        """Start the process and wait until it answers a framed command."""
        with self._lock:
            if self.running:
                return "already running"
            self.log_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            self.log_path = self.log_dir / f"{self.name}_{stamp}_{uuid.uuid4().hex[:6]}.log"
            self._buffer = ""
            self._reader_error = None
            self.process = subprocess.Popen(
                batch_command(self.launcher, self.args), stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, bufsize=0,
                cwd=str(self.cwd) if self.cwd else None, env=child_env(), **popen_flags())
            log = open(self.log_path, "ab")
            threading.Thread(target=self._reader, args=(self.process, log), daemon=True,
                             name=f"{self.name}-reader").start()
            try:
                reply = self._run_locked("set __amd_mcp_ready READY", timeout)
            except Exception:
                self._kill_locked()
                raise
            if not (reply.ok and reply.result == "READY"):
                self._kill_locked()
                raise RuntimeError(f"{self.name} did not answer the handshake: {reply}")
            self.started_at = datetime.now().isoformat(timespec="seconds")
            return f"{self.name} started ({self.launcher})"

    def _kill_locked(self) -> None:
        if self.process is not None:
            kill_tree(self.process)
        self.process = None

    def stop(self) -> str:
        with self._lock:
            if not self.running:
                self.process = None
                return "not running"
            try:
                self.process.stdin.write(b"exit\n")
                self.process.stdin.flush()
                self.process.wait(timeout=15)
            except (OSError, subprocess.TimeoutExpired):
                pass
            self._kill_locked()
            return "stopped"

    # ----------------------------------------------------------------- commands
    @staticmethod
    def _wrapper(command: str, token: str) -> tuple[str, dict[str, str]]:
        m = {k: f"__AMDMCP_{k.upper()}_{token}__" for k in ("begin", "res", "err", "rc", "end")}
        v = f"::__amd_mcp_{token}"
        script = (
            f"set {v}_cmd [encoding convertfrom utf-8 [binary decode hex {command.encode().hex()}]];"
            f'puts "{m["begin"]}"; flush stdout;'
            f"set {v}_rc [catch {{uplevel #0 [set {v}_cmd]}} {v}_res {v}_opt];"
            f'puts "\\n{m["res"]}[binary encode hex [encoding convertto utf-8 [set {v}_res]]]";'
            f"if {{[set {v}_rc] == 1}} {{"
            f'puts "{m["err"]}[binary encode hex [encoding convertto utf-8 '
            f'[dict get [set {v}_opt] -errorinfo]]]"}};'
            f'puts "{m["rc"]}[set {v}_rc]";'
            f'puts "{m["end"]}"; flush stdout;'
            f"unset -nocomplain {v}_cmd {v}_rc {v}_res {v}_opt\n"
        )
        return script, m

    def _wait(self, marker: str, deadline: float) -> str:
        with self._cond:
            while True:
                index = self._buffer.find(marker)
                if index >= 0:
                    before = self._buffer[:index]
                    self._buffer = self._buffer[index + len(marker):]
                    return before
                if self._reader_error:
                    raise RuntimeError(f"{self.name} output reader failed: {self._reader_error}")
                if self.process is None or self.process.poll() is not None:
                    code = None if self.process is None else self.process.poll()
                    tail = self._buffer[-3000:]
                    raise RuntimeError(f"{self.name} exited (code {code}). Output tail:\n{tail}")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(marker)
                self._cond.wait(timeout=min(remaining, 0.25))

    @staticmethod
    def _field(payload: str, start: str, ends: list[str]) -> str:
        i = payload.rfind(start)
        if i < 0:
            return ""
        i += len(start)
        stop = min([j for j in (payload.find(e, i) for e in ends) if j >= 0] or [len(payload)])
        return payload[i:stop].strip()

    @classmethod
    def parse_reply(cls, payload: str, m: dict[str, str], elapsed_s: float = 0.0) -> TclResult:
        """The reply between the begin and end markers."""
        res_at = payload.rfind(m["res"])
        if res_at < 0 or payload.rfind(m["rc"]) < res_at:
            raise RuntimeError(f"malformed reply: {payload[-2000:]}")
        output = payload[:res_at].strip("\r\n")
        result = bytes.fromhex(cls._field(payload, m["res"], [m["err"], m["rc"]]) or "")
        error = bytes.fromhex(cls._field(payload, m["err"], [m["rc"]]) or "")
        rc = cls._field(payload, m["rc"], ["\n"]).strip()
        # catch codes: 0 ok, 1 error, 2 return (a result too), 3 break, 4 continue
        return TclResult(ok=rc in ("0", "2"), result=result.decode("utf-8", "replace"),
                         output=output, error=error.decode("utf-8", "replace"),
                         elapsed_s=elapsed_s)

    def _run_locked(self, command: str, timeout: float) -> TclResult:
        t0 = time.monotonic()
        token = uuid.uuid4().hex
        script, m = self._wrapper(command, token)
        self.process.stdin.write(script.encode("utf-8"))
        self.process.stdin.flush()
        deadline = t0 + timeout
        self._wait(m["begin"], deadline)
        payload = self._wait(m["end"], deadline)
        return self.parse_reply(payload, m, round(time.monotonic() - t0, 3))

    def run(self, command: str, timeout: float = 300) -> TclResult:
        """Run Tcl. On timeout the process is stopped (it may be stuck mid-command)."""
        with self._lock:
            if not self.running:
                raise RuntimeError(f"{self.name} is not running")
            try:
                return self._run_locked(command, timeout)
            except TimeoutError:
                self._kill_locked()
                raise TimeoutError(f"{self.name} command timed out after {timeout:g} s; "
                                   f"the {self.name} process was stopped") from None
            except Exception:
                self._kill_locked()
                raise
