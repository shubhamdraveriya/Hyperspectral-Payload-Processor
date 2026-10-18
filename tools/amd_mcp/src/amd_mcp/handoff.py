"""The hand-off between the two servers: a JSON file in the state folder.

The Vivado server appends an export record each time it writes an XSA. The Vitis server
takes the latest one as the default XSA of a new platform, and records which XSA file and
content (SHA-256) each platform was built from: a platform follows that file, and is stale
when Vivado has re-exported it since.

    {"schema": 1,
     "exports":   [{"id", "xsa", "sha256", "exported_at", "part", "include_bit", ...}, ...],
     "platforms": {"<workspace>/<name>": {"xsa", "sha256", "updated_at", "xpfm", ...}},
     "apps":      {"<workspace>/<name>": {"platform", "elf", "built_at", ...}}}
"""

from __future__ import annotations

import json
import os
import time
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from .paths import state_dir
from .xsa import sha256_file

SCHEMA = 1
KEEP_EXPORTS = 50


def handoff_file() -> Path:
    return state_dir() / "handoff.json"


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def key(workspace: str | os.PathLike[str], name: str) -> str:
    return f"{Path(workspace).resolve().as_posix()}/{name}"


def _empty() -> dict:
    return {"schema": SCHEMA, "exports": [], "platforms": {}, "apps": {}}


def load() -> dict:
    path = handoff_file()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return _empty()
    except (OSError, ValueError):
        # keep the unreadable file for inspection, start a new record
        try:
            os.replace(path, path.with_name(f"handoff.bad-{int(time.time())}.json"))
        except OSError:
            pass
        return _empty()
    if not isinstance(data, dict):
        return _empty()
    for field, default in _empty().items():
        if not isinstance(data.get(field), type(default)):
            data[field] = default
    return data


@contextmanager
def _locked():
    """Exclusive access across the two server processes (a lock file, stale after 30 s)."""
    lock = handoff_file().with_suffix(".lock")
    deadline = time.monotonic() + 15
    while True:
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode())
            os.close(fd)
            break
        except FileExistsError:
            try:
                if time.time() - lock.stat().st_mtime > 30:
                    lock.unlink()
                    continue
            except FileNotFoundError:
                continue
            if time.monotonic() > deadline:
                raise TimeoutError(f"hand-off file is locked: {lock}")
            time.sleep(0.05)
    try:
        yield
    finally:
        try:
            lock.unlink()
        except FileNotFoundError:
            pass


def _save(data: dict) -> None:
    path = handoff_file()
    tmp = path.with_name(f"handoff.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    for _ in range(40):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:  # a reader has it open (Windows)
            time.sleep(0.05)
    os.replace(tmp, path)


def record_export(xsa: str | os.PathLike[str], **meta) -> dict:
    """Append an export record for an XSA just written; returns the record."""
    path = Path(xsa).resolve()
    entry = {
        "id": uuid.uuid4().hex[:12],
        "xsa": path.as_posix(),
        "sha256": sha256_file(path),
        "size": path.stat().st_size,
        "exported_at": _now(),
        **meta,
    }
    with _locked():
        data = load()
        data["exports"].append(entry)
        data["exports"] = data["exports"][-KEEP_EXPORTS:]
        _save(data)
    return entry


def latest_export() -> dict | None:
    exports = load()["exports"]
    return exports[-1] if exports else None


def record_platform(workspace, name: str, xsa: str, sha256: str, **meta) -> dict:
    entry = {"workspace": Path(workspace).resolve().as_posix(), "name": name, "xsa": xsa,
             "sha256": sha256, "updated_at": _now(), **meta}
    with _locked():
        data = load()
        data["platforms"][key(workspace, name)] = entry
        _save(data)
    return entry


def platform_record(workspace, name: str) -> dict | None:
    return load()["platforms"].get(key(workspace, name))


def record_app(workspace, name: str, **meta) -> dict:
    entry = {"workspace": Path(workspace).resolve().as_posix(), "name": name,
             "built_at": _now(), **meta}
    with _locked():
        data = load()
        data["apps"][key(workspace, name)] = entry
        _save(data)
    return entry


def app_record(workspace, name: str) -> dict | None:
    return load()["apps"].get(key(workspace, name))


def latest_app() -> dict | None:
    apps = list(load()["apps"].values())
    return max(apps, key=lambda a: a.get("built_at", "")) if apps else None


def export_state(entry: dict | None) -> dict | None:
    """The record plus whether its file is still there and unchanged."""
    if entry is None:
        return None
    state = dict(entry)
    path = Path(entry["xsa"])
    if not path.is_file():
        state["file_state"] = "missing"
    elif sha256_file(path) != entry.get("sha256"):
        state["file_state"] = "changed since export (rewritten outside the hand-off)"
    else:
        state["file_state"] = "unchanged"
    return state


def source_changed(entry: dict) -> bool | None:
    """Whether the XSA file a platform was made from differs from what it was built from
    (Vivado re-exported it); None when the file is gone."""
    path = Path(entry.get("xsa", ""))
    if not path.is_file():
        return None
    return sha256_file(path) != entry.get("sha256")


def status() -> dict:
    """Latest export, and every platform with stale = its XSA file changed since its build."""
    data = load()
    latest = data["exports"][-1] if data["exports"] else None
    platforms = []
    for entry in data["platforms"].values():
        item = dict(entry)
        item["stale"] = source_changed(entry)
        if item["stale"] is None:
            item["note"] = f"its XSA file is gone: {entry.get('xsa')}"
        platforms.append(item)
    return {
        "handoff_file": handoff_file().as_posix(),
        "latest_export": export_state(latest),
        "previous_exports": len(data["exports"]) - (1 if latest else 0),
        "platforms": platforms,
        "apps": list(data["apps"].values()),
    }
