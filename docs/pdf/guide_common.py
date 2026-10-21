"""Helpers for the HPDP Learning Guide: numbered figures and tables, key-point
boxes, worked examples and the check questions (their answers go to Appendix A)."""

from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import KeepTogether, Paragraph, Spacer, Table, TableStyle

from hpdp_pdf import (ACCENT_DARK, BODY, INK2, PANEL, TEXT_W, _admonition, drawing_figure, figure,
                      table, table_caption)

_fig = [0]
_tab = [0]
ANSWERS = []  # (chapter title, [(question, answer), ...])


def fig(obj, caption, width=TEXT_W):
    """A drawing or a PNG name from docs/pdf/figures, with a numbered caption."""
    _fig[0] += 1
    text = f"Figure {_fig[0]}: {caption}"
    if isinstance(obj, str):
        return figure(obj, text, width)
    return drawing_figure(obj, text)


def tab(header, rows, widths, caption, **kw):
    """A table with a numbered caption above it, as two flowables (use *tab(...) in a
    story list). The caption stays with the table's first rows; a long table splits."""
    _tab[0] += 1
    return [table_caption(f"Table {_tab[0]}: {caption}"), table(header, rows, widths, **kw),
            Spacer(1, 7)]


def key(items):
    """The key points at the end of a chapter."""
    text = "<br/>".join(f"• {t}" for t in items)
    return _admonition("KEY POINTS", text, ACCENT_DARK, colors.HexColor("#e9f1fb"))


def example(title, lines):
    """A worked example: a title and lines of text in a grey panel."""
    head = ParagraphStyle("exh", parent=BODY, fontName="UI-Bold", spaceAfter=3)
    body = ParagraphStyle("exb", parent=BODY, spaceAfter=2)
    cell = [Paragraph(f"Worked example: {title}", head)] + [Paragraph(l, body) for l in lines]
    t = Table([[cell]], colWidths=[TEXT_W], hAlign="LEFT")
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), PANEL),
                           ("BOX", (0, 0), (-1, -1), 0.4, colors.HexColor("#d9d8d2")),
                           ("LEFTPADDING", (0, 0), (-1, -1), 10), ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                           ("TOPPADDING", (0, 0), (-1, -1), 7), ("BOTTOMPADDING", (0, 0), (-1, -1), 7)]))
    return KeepTogether([Spacer(1, 2), t, Spacer(1, 8)])


def check(chapter, qa):
    """Check questions at the end of a chapter; the answers go to Appendix A."""
    ANSWERS.append((chapter, qa))
    style = ParagraphStyle("q", parent=BODY, spaceAfter=3)
    head = ParagraphStyle("qh", parent=BODY, fontName="UI-Bold", textColor=INK2, spaceAfter=3)
    rows = [Paragraph("Check your knowledge (the answers are in Appendix A)", head)]
    rows += [Paragraph(f"{i + 1}.&nbsp;&nbsp;{q}", style) for i, (q, _) in enumerate(qa)]
    t = Table([[rows]], colWidths=[TEXT_W], hAlign="LEFT")
    t.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor("#c3c2b7")),
                           ("LEFTPADDING", (0, 0), (-1, -1), 10), ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                           ("TOPPADDING", (0, 0), (-1, -1), 7), ("BOTTOMPADDING", (0, 0), (-1, -1), 6)]))
    return KeepTogether([Spacer(1, 4), t, Spacer(1, 8)])


def answers_flowables():
    """Appendix A: every check question with its answer."""
    from hpdp_pdf import H2, p
    out = []
    qs = ParagraphStyle("aq", parent=BODY, fontName="UI-Bold", spaceAfter=1)
    for chapter, qa in ANSWERS:
        out.append(Paragraph(chapter, H2))  # not in the table of contents
        for i, (q, a) in enumerate(qa):
            out.append(KeepTogether([Paragraph(f"{i + 1}. {q}", qs), p(a)]))
    return out
