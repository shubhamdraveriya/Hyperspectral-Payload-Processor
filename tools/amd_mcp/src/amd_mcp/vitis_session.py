"""The Vitis side: Vitis's environment, the persistent worker, and batch scripts.

vitis.bat -s <script> only sets up the environment and runs Vitis's bundled Python on the
script, but it splits its arguments on spaces, commas and '=' and drops quotes. So the
environment is captured once (a tiny script prints os.environ from inside Vitis) and the
worker and batch scripts are then started with that Python and environment directly,
where any path or argument is safe.
"""

from __future__ import annotations

import codecs
import json
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .paths import (WINDOWS, batch_command, child_env, kill_tree, no_space_path, popen_flags,
                    sub_dir, vitis_root)

ENV_MARK = "__AMDMCP_ENV__"
_ENV_SCRIPT = f"""import json, os, sys
print("{ENV_MARK}" + json.dumps({{"env": dict(os.environ), "executable": sys.executable,
      "version": sys.version.split()[0]}}).encode("utf-8").hex(), flush=True)
"""

# lines worth showing from a build log: compiler and make errors, tool ERROR/CRITICAL messages
_ERROR_LINE = re.compile(
    r"(\berror\b\s*:|\berror:|^\s*ERROR\b|CRITICAL WARNING|make(\[\d+\])?: \*\*\*|"
    r"undefined reference|No such file or directory|Traceback \(most recent call last\)|"
    r"\bException\b|\bFAILED\b)", re.IGNORECASE)


def error_lines(text: str, limit: int = 40) -> list[str]:
    found = []
    for line in text.splitlines():
        if _ERROR_LINE.search(line) and "-Werror" not in line:
            found.append(line.strip()[:400])
            if len(found) >= limit:
                break
    return found


_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def plain(text: str | None) -> str | None:
    """Without the terminal colour codes the Vitis API puts in its messages."""
    return _ANSI.sub("", text) if text else text


def tail(text: str, chars: int = 6000) -> str:
    return text if len(text) <= chars else "...[earlier output in the log file]...\n" + text[-chars:]


@dataclass
class VitisEnvironment:
    root: Path
    python: Path
    env: dict[str, str]
    python_version: str


def capture_environment(timeout: float = 300) -> VitisEnvironment:
    """Run vitis.bat -s on a script that prints the environment Vitis sets up."""
    root = vitis_root()
    launcher = root / "bin" / ("vitis.bat" if WINDOWS else "vitis")
    folder = no_space_path(sub_dir("vitis"))
    script = folder / "amd_mcp_vitis_env.py"
    script.write_text(_ENV_SCRIPT, encoding="utf-8")
    done = subprocess.run(batch_command(launcher, ["-s", str(script)]), stdin=subprocess.DEVNULL,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout,
                          cwd=str(folder), env=child_env(), **popen_flags())
    text = done.stdout.decode("utf-8", "replace")
    for line in text.splitlines():
        if line.startswith(ENV_MARK):
            data = json.loads(bytes.fromhex(line[len(ENV_MARK):].strip()).decode("utf-8"))
            return VitisEnvironment(root=root, python=Path(data["executable"]), env=data["env"],
                                    python_version=data["version"])
    raise RuntimeError(f"could not read Vitis's environment from {launcher} "
                       f"(exit code {done.returncode}):\n{text[-3000:]}")


@dataclass
class WorkerReply:
    ok: bool
    value: object = None
    error: str | None = None
    traceback: str | None = None
    output: str = ""
    elapsed_s: float = 0.0
    log: str | None = None
    errors: list[str] = field(default_factory=list)

    def as_dict(self, output_chars: int = 6000) -> dict:
        reply = {"ok": self.ok, "elapsed_s": self.elapsed_s}
        if self.ok:
            reply["value"] = self.value
        else:
            reply["error"] = self.error
            if self.traceback:
                reply["traceback"] = tail(self.traceback, 3000)
        if self.errors:
            reply["error_lines"] = self.errors
        if self.output.strip():
            reply["output_tail"] = tail(self.output, output_chars)
        if self.log:
            reply["log"] = self.log
        return reply


class VitisWorker:
    """One long-lived worker process running vitis_worker.py in Vitis's Python."""

    def __init__(self):
        self.environment: VitisEnvironment | None = None
        self.process: subprocess.Popen | None = None
        self.started_at: str | None = None
        self.session_log: Path | None = None
        self._lock = threading.Lock()
        self._cond = threading.Condition()
        self._buffer = ""
        self._reader_error: str | None = None
        self._logs = sub_dir("logs")
        self._replies = sub_dir("vitis", "replies")

    @property
    def running(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def env(self) -> VitisEnvironment:
        if self.environment is None:
            self.environment = capture_environment()
        return self.environment

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
                with self._cond:
                    self._buffer += decoder.decode(chunk)
                    self._cond.notify_all()
        except OSError as exc:
            with self._cond:
                self._reader_error = str(exc)
        finally:
            log.close()
            with self._cond:
                self._cond.notify_all()

    def _wait(self, marker: str, deadline: float) -> str:
        with self._cond:
            while True:
                index = self._buffer.find(marker)
                if index >= 0:
                    before = self._buffer[:index]
                    self._buffer = self._buffer[index + len(marker):]
                    return before
                if self._reader_error:
                    raise RuntimeError(f"worker output reader failed: {self._reader_error}")
                if self.process is None or self.process.poll() is not None:
                    code = None if self.process is None else self.process.poll()
                    raise RuntimeError(f"Vitis worker exited (code {code}). Output tail:\n"
                                       f"{self._buffer[-3000:]}")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(marker)
                self._cond.wait(timeout=min(remaining, 0.25))

    def _start_locked(self, timeout: float = 300) -> None:
        if self.running:
            return
        environment = self.env()
        folder = sub_dir("vitis")
        worker = folder / "amd_mcp_vitis_worker.py"   # alone in its folder: nothing to shadow
        shutil.copyfile(Path(__file__).with_name("vitis_worker.py"), worker)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.session_log = self._logs / f"vitis_worker_{stamp}.log"
        env = dict(environment.env)
        env["AMD_MCP_VITIS_SERVER_LOG"] = str(self._logs / f"vitis_server_{stamp}.log")
        self._buffer = ""
        self._reader_error = None
        self.process = subprocess.Popen(
            [str(environment.python), "-u", str(worker)], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, bufsize=0, env=env,
            cwd=str(sub_dir("vitis", "run")), **popen_flags())
        threading.Thread(target=self._reader, args=(self.process, open(self.session_log, "ab")),
                         daemon=True, name="vitis-worker-reader").start()
        try:
            self._wait("__AMDMCP_WORKER_READY__", time.monotonic() + timeout)
        except Exception:
            self._kill_locked()
            raise
        self.started_at = datetime.now().isoformat(timespec="seconds")

    def _kill_locked(self) -> None:
        if self.process is not None:
            kill_tree(self.process)
        self.process = None

    def call(self, op: str, timeout: float = 600, **kwargs) -> WorkerReply:
        """Run one op in the worker. On timeout the worker and its Vitis server are stopped."""
        with self._lock:
            self._start_locked()
            rid = uuid.uuid4().hex
            reply_file = self._replies / f"{rid}.json"
            request = json.dumps({"id": rid, "op": op, "kwargs": kwargs, "reply": str(reply_file)})
            t0 = time.monotonic()
            deadline = t0 + timeout
            try:
                self.process.stdin.write(request.encode("utf-8").hex().encode("ascii") + b"\n")
                self.process.stdin.flush()
                self._wait(f"__AMDMCP_BEGIN_{rid}__", deadline)
                output = self._wait(f"__AMDMCP_END_{rid}__", deadline).strip("\r\n")
            except TimeoutError:
                self._kill_locked()
                return WorkerReply(ok=False, error=f"'{op}' timed out after {timeout:g} s; the "
                                   "Vitis worker and its server were stopped (the next call "
                                   "starts them again)", log=str(self.session_log),
                                   output=self._buffer, elapsed_s=round(time.monotonic() - t0, 1))
            except (OSError, RuntimeError) as exc:
                self._kill_locked()
                return WorkerReply(ok=False, error=str(exc), log=str(self.session_log),
                                   elapsed_s=round(time.monotonic() - t0, 1))
            try:
                data = json.loads(reply_file.read_text(encoding="utf-8"))
                reply_file.unlink()
            except (OSError, ValueError) as exc:
                data = {"ok": False, "error": f"no reply from the worker: {exc}"}
            call_log = self._logs / f"vitis_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{op}.log"
            call_log.write_text(output, encoding="utf-8")
            output = plain(output)
            return WorkerReply(ok=bool(data.get("ok")), value=data.get("value"),
                               error=plain(data.get("error")),
                               traceback=plain(data.get("traceback")),
                               output=output, elapsed_s=round(time.monotonic() - t0, 1),
                               log=str(call_log), errors=error_lines(output))

    def stop(self) -> str:
        with self._lock:
            if not self.running:
                self.process = None
                return "not running"
            rid = uuid.uuid4().hex
            reply_file = self._replies / f"{rid}.json"
            request = json.dumps({"id": rid, "op": "shutdown", "kwargs": {}, "reply": str(reply_file)})
            try:
                self.process.stdin.write(request.encode("utf-8").hex().encode("ascii") + b"\n")
                self.process.stdin.flush()
                self.process.wait(timeout=60)
            except (OSError, subprocess.TimeoutExpired):
                pass
            self._kill_locked()
            try:
                reply_file.unlink()
            except FileNotFoundError:
                pass
            return "stopped"

    def run_script(self, script: Path, args: list[str], cwd: Path | None,
                   timeout: float) -> dict:
        """Run a Vitis Python script as its own process (like vitis -s script args)."""
        environment = self.env()
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        log = self._logs / f"vitis_script_{stamp}_{script.stem}.log"
        t0 = time.monotonic()
        # script's own folder first on sys.path, as under vitis -s
        process = subprocess.Popen([str(environment.python), "-u", str(script), *args],
                                   stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, env=dict(environment.env),
                                   cwd=str(cwd) if cwd else None, **popen_flags())
        try:
            out, _ = process.communicate(timeout=timeout)
            timed_out = False
        except subprocess.TimeoutExpired:
            kill_tree(process)
            out, _ = process.communicate()
            timed_out = True
        text = out.decode("utf-8", "replace")
        log.write_text(text, encoding="utf-8")
        text = plain(text)
        return {"ok": process.returncode == 0 and not timed_out, "exit_code": process.returncode,
                "timed_out": timed_out, "elapsed_s": round(time.monotonic() - t0, 1),
                "error_lines": error_lines(text), "output_tail": tail(text), "log": str(log)}
