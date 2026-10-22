from pathlib import Path

import pytest

from amd_mcp.vivado_reports import compact_messages, timing_summary, utilization

M7H = Path(__file__).resolve().parents[3] / "build" / "m7h" / "reports"

# from report_timing_summary of the ZCU106 self-test design (Vivado 2025.2)
MET = """\
------------------------------------------------------------------------------------------------
| Design Timing Summary
| ---------------------
------------------------------------------------------------------------------------------------

    WNS(ns)      TNS(ns)  TNS Failing Endpoints  TNS Total Endpoints      WHS(ns)      THS(ns)  THS Failing Endpoints  THS Total Endpoints     WPWS(ns)     TPWS(ns)  TPWS Failing Endpoints  TPWS Total Endpoints
    -------      -------  ---------------------  -------------------      -------      -------  ---------------------  -------------------     --------     --------  ----------------------  --------------------
      5.162        0.000                      0                 2162        0.008        0.000                      0                 2162        3.500        0.000                       0                   868


All user specified timing constraints are met.
"""


def test_met():
    r = timing_summary(MET)
    assert r["parsed"] and r["met"] is True
    assert r["wns"] == 5.162 and r["whs"] == 0.008 and r["wpws"] == 3.5
    assert r["tns_total_endpoints"] == 2162 and r["failing_endpoints"] == 0


def test_not_met():
    text = (MET.replace("      5.162        0.000                      0",
                        "     -0.412      -12.900                     37")
               .replace("All user specified timing constraints are met.",
                        "Timing constraints are not met."))
    r = timing_summary(text)
    assert r["met"] is False and r["wns"] == -0.412 and r["tns"] == -12.9
    assert r["tns_failing_endpoints"] == 37 and r["failing_endpoints"] == 37


def test_unconstrained_and_unparseable():
    text = MET.replace("      5.162        0.000", "         NA           NA").replace(
        "All user specified timing constraints are met.",
        "There are no user specified timing constraints.")
    r = timing_summary(text)
    assert r["wns"] is None and r["met"] is None and "no timing constraints" in r["note"]
    empty = timing_summary("nothing here")
    assert empty["parsed"] is False and empty["met"] is None


# 2025.2 layout: a Prohibited column between Fixed and Available
UTIL = """\
+----------------------------+------+-------+------------+-----------+-------+
|          Site Type         | Used | Fixed | Prohibited | Available | Util% |
+----------------------------+------+-------+------------+-----------+-------+
| CLB LUTs                   |  784 |     0 |          0 |    230400 |  0.34 |
|   LUT as Logic             |  782 |     0 |          0 |    230400 |  0.34 |
|     LUT as Shift Register  |    2 |     0 |            |           |       |
| CLB Registers              |  865 |     0 |          0 |    460800 |  0.19 |
| F7 Muxes                   |    3 |     0 |          0 |    115200 | <0.01 |
+----------------------------+------+-------+------------+-----------+-------+
"""


def test_utilization_by_column_name():
    r = utilization(UTIL)
    assert r["lut"] == {"used": 784, "available": 230400, "percent": 0.34}
    assert r["ff"] == {"used": 865, "available": 460800, "percent": 0.19}
    assert [x["name"] for x in r["resources"]] == ["CLB LUTs", "CLB Registers", "F7 Muxes"]
    assert r["resources"][2]["percent"] == 0.01 and r["dsp"] is None


def test_utilization_7_series_layout():
    text = UTIL.replace("| Prohibited ", "").replace("|          0 |", "|").replace(
        "|            |", "|").replace("CLB LUTs  ", "Slice LUTs")
    r = utilization(text)
    assert r["lut"]["used"] == 784 and r["lut"]["available"] == 230400


@pytest.mark.skipif(not M7H.is_dir(), reason="needs the repository's build/m7h/reports")
def test_m7h_reports():
    u = utilization((M7H / "utilization.rpt").read_text(encoding="utf-8", errors="replace"))
    assert u["lut"]["used"] == 38151 and u["ff"]["used"] == 39231      # as the XSA's sysdef
    assert u["bram"]["used"] == 38.5 and u["dsp"]["used"] == 24
    assert u["uram"] == {"used": 56, "available": 96, "percent": 58.33}
    t = timing_summary((M7H / "timing_summary.rpt").read_text(encoding="utf-8", errors="replace"))
    assert t["met"] is True and t["wns"] == 0.087 and t["whs"] == 0.01


def test_compact_messages():
    board = "WARNING: [Board 49-26] cannot add Board Part x"
    text = "\n".join(["Scanning sources..."] + [board] * 40 + ["ERROR: [Common 17-53] e"] * 5)
    out = compact_messages(text).splitlines()
    assert out.count(board) == 3 and out.count("ERROR: [Common 17-53] e") == 5   # errors kept
    assert out[-1] == "[amd_mcp] omitted: 37 more WARNING: [Board 49-26]"
