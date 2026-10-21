"""Build the HPDP PDF documents.

    .venv/Scripts/python docs/pdf/make_charts.py     # charts (matplotlib)
    .venv/Scripts/python docs/pdf/build_docs.py      # PDFs (reportlab)

One interpreter for both: the project venv with docs/pdf/requirements.txt installed.

Writes docs/pdf/HPDP_Project_Brief.pdf, HPDP_Process_Description_STE.pdf,
HPDP_Results_Report.pdf and HPDP_Learning_Guide_STE.pdf.
"""

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import doc_brief  # noqa: E402
import doc_guide  # noqa: E402
import doc_process  # noqa: E402
import doc_report  # noqa: E402

OUTPUTS = [
    (doc_brief, "HPDP_Project_Brief.pdf"),
    (doc_process, "HPDP_Process_Description_STE.pdf"),
    (doc_report, "HPDP_Results_Report.pdf"),
    (doc_guide, "HPDP_Learning_Guide_STE.pdf"),
]

if __name__ == "__main__":
    for module, name in OUTPUTS:
        module.build(HERE / name)
        print("wrote", HERE / name)
