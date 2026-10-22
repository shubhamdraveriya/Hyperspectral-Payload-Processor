"""Parsing Vivado report text (no Vivado needed, so it can be tested on saved reports)."""

from __future__ import annotations

import re

_COLUMNS = {
    "WNS(ns)": "wns", "TNS(ns)": "tns", "TNS Failing Endpoints": "tns_failing_endpoints",
    "TNS Total Endpoints": "tns_total_endpoints", "WHS(ns)": "whs", "THS(ns)": "ths",
    "THS Failing Endpoints": "ths_failing_endpoints", "THS Total Endpoints": "ths_total_endpoints",
    "WPWS(ns)": "wpws", "TPWS(ns)": "tpws", "TPWS Failing Endpoints": "tpws_failing_endpoints",
    "TPWS Total Endpoints": "tpws_total_endpoints",
}


def _number(text: str) -> float | int | None:
    try:
        value = float(text)
    except ValueError:
        return None                    # NA, inf
    return int(value) if value.is_integer() and "." not in text else value


def _cell_number(text: str) -> float | int | None:
    text = text.strip().lstrip("<")
    if not text:
        return None
    try:
        value = float(text)
    except ValueError:
        return None
    return int(value) if value.is_integer() and "." not in text else value


def tables(report: str) -> list[tuple[list[str], list[list[str]]]]:
    """The +---+ boxed tables of a Vivado report: (header cells, rows of raw cells)."""
    lines = report.splitlines()
    found = []
    i = 0
    while i + 2 < len(lines):
        if (lines[i].startswith("+") and lines[i + 1].lstrip().startswith("|")
                and lines[i + 2].startswith("+")):
            header = [h.strip() for h in lines[i + 1].strip()[1:-1].split("|")]
            rows = []
            j = i + 3
            while j < len(lines) and lines[j].lstrip().startswith("|"):
                rows.append(lines[j].strip()[1:-1].split("|"))
                j += 1
            found.append((header, rows))
            i = j
            continue
        i += 1
    return found


# summary keys and the row names Vivado uses for them (UltraScale+ first, then 7 series)
_RESOURCES = {
    "lut": ("CLB LUTs", "Slice LUTs"),
    "ff": ("CLB Registers", "Slice Registers"),
    "bram": ("Block RAM Tile",),
    "uram": ("URAM",),
    "dsp": ("DSPs", "DSP Slices"),
    "io": ("Bonded IOB",),
}


def utilization(report: str) -> dict:
    """report_utilization text by column name (Used, Available, Util%), so the extra
    columns of newer releases (Prohibited) do not shift the numbers. Flat reports give
    lut/ff/bram/uram/dsp/io and every top-level row; hierarchical ones the instance table."""
    result: dict = {key: None for key in _RESOURCES}
    resources = []
    hierarchy = []
    for header, rows in tables(report):
        if header and header[0] == "Site Type" and "Used" in header and "Available" in header:
            col = {name: header.index(name) for name in header}
            for cells in rows:
                if len(cells) != len(header):
                    continue
                name = cells[0].strip()
                indent = len(cells[0]) - len(cells[0].lstrip()) - 1
                used = _cell_number(cells[col["Used"]])
                available = _cell_number(cells[col["Available"]])
                if "Util%" in col and _cell_number(cells[col["Util%"]]) is not None:
                    percent = _cell_number(cells[col["Util%"]])
                else:
                    percent = round(100 * used / available, 2) if used is not None and available else None
                entry = {"used": used, "available": available, "percent": percent}
                if indent <= 0:
                    resources.append({"name": name, **entry})
                for key, names in _RESOURCES.items():
                    if result[key] is None and name in names:
                        result[key] = entry
        elif header and header[0] == "Instance":
            for cells in rows:
                if len(cells) == len(header):
                    row = {"Instance": cells[0].rstrip(), **{
                        h: (_cell_number(c) if _cell_number(c) is not None else c.strip())
                        for h, c in zip(header[1:], cells[1:])}}
                    hierarchy.append(row)
    result["resources"] = resources
    if hierarchy:
        result["hierarchy"] = hierarchy
    result["parsed"] = bool(resources or hierarchy)
    return result


_MESSAGE = re.compile(r"^(INFO|WARNING): \[([^\]]+)\]")


def compact_messages(text: str, keep: int = 3) -> str:
    """Keep the first `keep` INFO/WARNING lines of each message ID and count the rest
    (opening a project prints one Board 49-26 warning per board of an absent device)."""
    seen: dict[str, int] = {}
    out = []
    for line in text.splitlines():
        m = _MESSAGE.match(line.strip())
        if m:
            key = f"{m.group(1)}: [{m.group(2)}]"
            seen[key] = seen.get(key, 0) + 1
            if seen[key] > keep:
                continue
        out.append(line)
    dropped = {k: n - keep for k, n in seen.items() if n > keep}
    if dropped:
        out.append("[amd_mcp] omitted: " + ", ".join(f"{n} more {k}" for k, n in dropped.items()))
    return "\n".join(out)


def timing_summary(report: str) -> dict:
    """The Design Timing Summary table of report_timing_summary, and whether the
    constraints are met (Vivado's own verdict line, else WNS, WHS and WPWS >= 0)."""
    result: dict = {key: None for key in _COLUMNS.values()}
    lines = report.splitlines()
    found = False
    for i, line in enumerate(lines):
        if "WNS(ns)" in line and "WHS(ns)" in line:
            headers = re.split(r"\s{2,}", line.strip())
            for candidate in lines[i + 1:i + 6]:
                stripped = candidate.strip()
                if not stripped or set(stripped) <= set("- "):
                    continue
                values = stripped.split()
                if len(values) == len(headers):
                    for header, value in zip(headers, values):
                        if header in _COLUMNS:
                            result[_COLUMNS[header]] = _number(value)
                    found = True
                break
            if found:
                break
    failing = [result[k] for k in ("tns_failing_endpoints", "ths_failing_endpoints",
                                   "tpws_failing_endpoints") if isinstance(result[k], int)]
    result["failing_endpoints"] = sum(failing) if failing else None
    if "All user specified timing constraints are met" in report:
        result["met"] = True
    elif "Timing constraints are not met" in report:
        result["met"] = False
    elif found and all(isinstance(result[k], (int, float)) for k in ("wns", "whs", "wpws")):
        result["met"] = result["wns"] >= 0 and result["whs"] >= 0 and result["wpws"] >= 0
    else:
        result["met"] = None
    if "There are no user specified timing constraints" in report:
        result["note"] = "the design has no timing constraints"
    result["parsed"] = found
    return result
