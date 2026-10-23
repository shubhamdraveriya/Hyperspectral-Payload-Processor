"""HPDP Learning Guide: the HPDP taught from the basics to the advanced details, written
to the rules of ASD-STE100 Simplified Technical English (STE).

    .venv/Scripts/python docs/pdf/doc_guide.py      # this document only
    .venv/Scripts/python docs/pdf/build_docs.py     # all HPDP documents (docs/pdf/requirements.txt)
"""

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import guide_common  # noqa: E402
from guide_a import part_a  # noqa: E402
from guide_b import part_b  # noqa: E402
from guide_c import appendices_bcd, part_c  # noqa: E402
from hpdp_pdf import HpdpDoc, PageBreak, h1, p  # noqa: E402

TITLE = "HPDP Learning Guide"
DOC_ID = "HPDP-GDE-001"
REV = "B"
DATE = "2026-10-23"


def story(doc):
    guide_common.ANSWERS.clear()
    guide_common._fig[0] = 0
    guide_common._tab[0] = 0
    s = doc.cover_flowables([
        ("Document", f"{DOC_ID}, revision {REV}"),
        ("Date", DATE),
        ("Prepared for", "Shubham Draveriya"),
        ("System", "Hyperspectral Payload Data Processor (HPDP) on the AMD ZCU106 board; final builds m7h "
                   "(512 pixels) and m7i_1024b (1024 pixels)"),
        ("Language", "ASD-STE100 Simplified Technical English (STE)"),
        ("Level", "From no special knowledge (Part A) to the full design (Part C)"),
        ("Related documents", "SPEC.md (specification v1.0), docs/project_report.md, HPDP-BRF-001 "
                              "(project brief), HPDP-PRC-001 (process description), HPDP-RPT-001 "
                              "(results report), docs/m0 to m7 (milestone notes)"),
    ], extra=[p("This guide teaches what the HPDP is, which problems it solves and how each part of it "
                "operates. It starts with the basic ideas and ends with the design details, the test "
                "results and the procedures.")])
    s += part_a()
    s += part_b()
    s += part_c()
    s += [PageBreak(), h1("Appendix A  Answers to the check questions")]
    s += guide_common.answers_flowables()
    s += appendices_bcd()
    return s


def build(path):
    doc = HpdpDoc(path, TITLE, DOC_ID, REV, DATE,
                  subtitle="The Hyperspectral Payload Data Processor from the basics to the advanced "
                           "details: what it is, what it solves and how it works",
                  author="Shubham Draveriya", toc=True)
    doc.make(story(doc))


if __name__ == "__main__":
    out = HERE / "HPDP_Learning_Guide_STE.pdf"
    build(out)
    print("wrote", out)
