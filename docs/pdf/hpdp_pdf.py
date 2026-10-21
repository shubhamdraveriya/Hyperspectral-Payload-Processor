"""Shared layout for the HPDP PDF documents (reportlab): fonts, styles, page
templates with header and footer, a table of contents, admonitions
(WARNING / CAUTION / NOTE), tables, figures and the data-chain diagram."""

from pathlib import Path

from reportlab.graphics.shapes import Drawing, Line, Polygon, Rect, String
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (BaseDocTemplate, Frame, Image, KeepTogether, ListFlowable,
                                ListItem, NextPageTemplate, PageBreak, PageTemplate,
                                Paragraph, Spacer, Table, TableStyle)
from reportlab.platypus.tableofcontents import TableOfContents

HERE = Path(__file__).resolve().parent
FIG = HERE / "figures"

# ---------------------------------------------------------------------------
# Fonts and colours
# ---------------------------------------------------------------------------
FONTS = Path("C:/Windows/Fonts")
pdfmetrics.registerFont(TTFont("UI", str(FONTS / "segoeui.ttf")))
pdfmetrics.registerFont(TTFont("UI-Bold", str(FONTS / "segoeuib.ttf")))
pdfmetrics.registerFont(TTFont("UI-Italic", str(FONTS / "segoeuii.ttf")))
pdfmetrics.registerFont(TTFont("UI-BoldItalic", str(FONTS / "segoeuiz.ttf")))
pdfmetrics.registerFont(TTFont("Mono", str(FONTS / "consola.ttf")))
pdfmetrics.registerFontFamily("UI", normal="UI", bold="UI-Bold", italic="UI-Italic",
                              boldItalic="UI-BoldItalic")

INK = colors.HexColor("#0b0b0b")
INK2 = colors.HexColor("#52514e")
MUTED = colors.HexColor("#898781")
RULE = colors.HexColor("#c3c2b7")
GRID = colors.HexColor("#e1e0d9")
PANEL = colors.HexColor("#f0efec")
ACCENT = colors.HexColor("#2a78d6")
ACCENT_DARK = colors.HexColor("#184f95")
BLUE_LIGHT = colors.HexColor("#cde2fb")
ORANGE = colors.HexColor("#eb6834")
ORANGE_LIGHT = colors.HexColor("#fbe1d6")
# status colours, always with a text label (WARNING / CAUTION / NOTE)
CRITICAL = colors.HexColor("#d03b3b")
WARN = colors.HexColor("#fab219")

# ---------------------------------------------------------------------------
# Styles
# ---------------------------------------------------------------------------
BODY = ParagraphStyle("body", fontName="UI", fontSize=9.6, leading=13.6, textColor=INK,
                      spaceAfter=5, alignment=TA_LEFT)
SMALL = ParagraphStyle("small", parent=BODY, fontSize=8.4, leading=11.4, spaceAfter=0)
CELL = ParagraphStyle("cell", parent=BODY, fontSize=8.4, leading=11, spaceAfter=0)
CELL_B = ParagraphStyle("cellb", parent=CELL, fontName="UI-Bold")
H1 = ParagraphStyle("h1", fontName="UI-Bold", fontSize=15, leading=19, textColor=INK,
                    spaceBefore=6, spaceAfter=8, keepWithNext=1)
H2 = ParagraphStyle("h2", fontName="UI-Bold", fontSize=11.6, leading=15, textColor=INK,
                    spaceBefore=9, spaceAfter=4, keepWithNext=1)
H3 = ParagraphStyle("h3", fontName="UI-Bold", fontSize=10, leading=13, textColor=INK2,
                    spaceBefore=6, spaceAfter=3, keepWithNext=1)
CAPTION = ParagraphStyle("caption", parent=BODY, fontName="UI-Italic", fontSize=8.2,
                         leading=10.5, textColor=INK2, spaceBefore=2, spaceAfter=9)
CODE = ParagraphStyle("code", fontName="Mono", fontSize=8.2, leading=11, textColor=INK)
TOC1 = ParagraphStyle("toc1", fontName="UI-Bold", fontSize=9.6, leading=14, leftIndent=0)
TOC2 = ParagraphStyle("toc2", fontName="UI", fontSize=9, leading=12.5, leftIndent=14)

PAGE_W, PAGE_H = A4
MARGIN_L = MARGIN_R = 20 * mm
MARGIN_T = 22 * mm
MARGIN_B = 20 * mm
TEXT_W = PAGE_W - MARGIN_L - MARGIN_R


# ---------------------------------------------------------------------------
# Flowable helpers
# ---------------------------------------------------------------------------
class Heading(Paragraph):
    """A heading that also goes into the table of contents."""

    def __init__(self, text, style, level):
        super().__init__(text, style)
        self.toc_level = level
        self.toc_text = text


def h1(text):
    return Heading(text, H1, 0)


def h2(text):
    return Heading(text, H2, 1)


def h3(text):
    return Paragraph(text, H3)


def p(text, style=BODY):
    para = Paragraph(text, style)
    if text.rstrip().endswith(":"):
        para.keepWithNext = 1  # a lead-in stays with the list that follows
    return para


def bullets(items, style=BODY):
    return ListFlowable([ListItem(Paragraph(t, style), leftIndent=12) for t in items],
                        bulletType="bullet", start="\u2022", leftIndent=12, bulletFontName="UI",
                        bulletFontSize=9, bulletColor=INK2, spaceBefore=0, spaceAfter=5)


def steps(items, style=BODY, start=1):
    """Numbered procedure steps. An item can be (text, [sub-steps])."""
    flow = []
    for t in items:
        if isinstance(t, tuple):
            text, subs = t
            inner = [Paragraph(text, style),
                     ListFlowable([ListItem(Paragraph(s, style), leftIndent=12) for s in subs],
                                  bulletType="a", leftIndent=14, bulletFontName="UI",
                                  bulletFontSize=9)]
            flow.append(ListItem(inner, leftIndent=16))
        else:
            flow.append(ListItem(Paragraph(t, style), leftIndent=16))
    return ListFlowable(flow, bulletType="1", start=start, leftIndent=16, bulletFontName="UI-Bold",
                        bulletFontSize=9.2, bulletColor=INK, spaceAfter=6)


def table(header, rows, widths, font=CELL, head_bg=PANEL, zebra=False, align_top=True):
    """A table with a bold header row; cells are paragraphs, so text wraps."""
    data = [[Paragraph(str(c), CELL_B) for c in header]]
    for r in rows:
        data.append([c if not isinstance(c, str) else Paragraph(c, font) for c in r])
    total = sum(widths)
    t = Table(data, colWidths=[w * TEXT_W / total for w in widths], repeatRows=1,
              hAlign="LEFT")
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), head_bg),
        ("LINEBELOW", (0, 0), (-1, 0), 0.8, RULE),
        ("LINEBELOW", (0, 1), (-1, -1), 0.4, GRID),
        ("VALIGN", (0, 0), (-1, -1), "TOP" if align_top else "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 3.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3.5),
    ]
    if zebra:
        for i in range(2, len(data), 2):
            style.append(("BACKGROUND", (0, i), (-1, i), colors.HexColor("#f7f7f5")))
    t.setStyle(TableStyle(style))
    return t


def _admonition(label, text, bar, bg):
    body = [Paragraph(f"<b>{label}</b>", ParagraphStyle("lab", parent=BODY, fontName="UI-Bold",
                                                         fontSize=9.4, textColor=INK,
                                                         spaceAfter=2))]
    for t in (text if isinstance(text, list) else [text]):
        body.append(Paragraph(t, ParagraphStyle("adm", parent=BODY, spaceAfter=2)))
    tbl = Table([[None, body]], colWidths=[4, TEXT_W - 4], hAlign="LEFT")
    tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, 0), bar),
        ("BACKGROUND", (1, 0), (1, 0), bg),
        ("LEFTPADDING", (1, 0), (1, 0), 9),
        ("RIGHTPADDING", (1, 0), (1, 0), 9),
        ("TOPPADDING", (1, 0), (1, 0), 6),
        ("BOTTOMPADDING", (1, 0), (1, 0), 6),
        ("LEFTPADDING", (0, 0), (0, 0), 0),
        ("RIGHTPADDING", (0, 0), (0, 0), 0),
    ]))
    return KeepTogether([Spacer(1, 2), tbl, Spacer(1, 7)])


def warning(text):
    return _admonition("WARNING", text, CRITICAL, colors.HexColor("#fbe9e9"))


def caution(text):
    return _admonition("CAUTION", text, WARN, colors.HexColor("#fff6e0"))


def note(text):
    return _admonition("NOTE", text, ACCENT, colors.HexColor("#eef4fc"))


def code(lines):
    text = "<br/>".join(l.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                        .replace(" ", "&nbsp;") for l in lines)
    t = Table([[Paragraph(text, CODE)]], colWidths=[TEXT_W], hAlign="LEFT")
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f4f4f1")),
        ("BOX", (0, 0), (-1, -1), 0.4, GRID),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    return KeepTogether([t, Spacer(1, 7)])


def figure(name, caption, width=TEXT_W):
    path = FIG / name
    img = Image(str(path))
    ratio = img.imageHeight / img.imageWidth
    img.drawWidth = width
    img.drawHeight = width * ratio
    return KeepTogether([img, Paragraph(caption, CAPTION)])


def drawing_figure(drawing, caption):
    return KeepTogether([drawing, Paragraph(caption, CAPTION)])


# ---------------------------------------------------------------------------
# Diagrams
# ---------------------------------------------------------------------------
def _box(d, x, y, w, h, text, fill, stroke=RULE, size=7.6, bold_first=True):
    d.add(Rect(x, y, w, h, rx=4, ry=4, fillColor=fill, strokeColor=stroke, strokeWidth=0.8))
    lines = text.split("\n")
    lh = size + 2
    y0 = y + h / 2 + (len(lines) - 1) * lh / 2 - size / 2 + 1
    for i, ln in enumerate(lines):
        d.add(String(x + w / 2, y0 - i * lh, ln, fontName="UI-Bold" if (i == 0 and bold_first) else "UI",
                     fontSize=size, fillColor=INK, textAnchor="middle"))


def _arrow(d, x1, y1, x2, y2, color=INK2, width=1.0):
    d.add(Line(x1, y1, x2, y2, strokeColor=color, strokeWidth=width))
    import math
    a = math.atan2(y2 - y1, x2 - x1)
    s = 4.2
    p1 = (x2 - s * math.cos(a) + s * 0.55 * math.sin(a), y2 - s * math.sin(a) - s * 0.55 * math.cos(a))
    p2 = (x2 - s * math.cos(a) - s * 0.55 * math.sin(a), y2 - s * math.sin(a) + s * 0.55 * math.cos(a))
    d.add(Polygon([x2, y2, p1[0], p1[1], p2[0], p2[1]], fillColor=color, strokeColor=color,
                  strokeWidth=0.5))


def chain_diagram(width=TEXT_W):
    """The HPDP data chain as three rows of blocks (a snake)."""
    n = 5
    gap = 13
    bw = (width - (n - 1) * gap) / n
    bh = 34
    row_gap = 26
    h = 3 * bh + 2 * row_gap + 42
    d = Drawing(width, h)
    ys = [h - 14 - bh, h - 14 - 2 * bh - row_gap, h - 14 - 3 * bh - 2 * row_gap]
    xs = [i * (bw + gap) for i in range(n)]
    onboard = BLUE_LIGHT
    channel = colors.HexColor("#e6effa")
    ground = ORANGE_LIGHT
    row1 = [("PS DDR4 + DMA", "raw scenes, calibration"), ("Sensor emulator", "1 line per period"),
            ("Radiometric", "correction"), ("Corner turn", "BIL \u2192 BIP, scene guard"),
            ("CCSDS 123", "lossless compressor")]
    row2 = [("Error injector", "test BER, bursts"), ("Randomizer", "+ sync marker (ASM)"),
            ("Reed-Solomon", "(255,223), I = 4"), ("AOS framer", "892-byte frames"),
            ("Space packetizer", "2048-byte packets")]
    row3 = [("Link TX + GTH", "8b/10b, SFP0"), ("OM3 fiber", "1.25 Gbaud, 1 m"),
            ("GTH + link RX", "SFP1, lock FSM"), ("Frame sync", "derandomizer, DMA"),
            ("PC ground station", "decode, verify, display")]
    for i, (t, s) in enumerate(row1):
        _box(d, xs[i], ys[0], bw, bh, f"{t}\n{s}", onboard)
    for i, (t, s) in enumerate(row2):
        _box(d, xs[i], ys[1], bw, bh, f"{t}\n{s}", channel)
    for i, (t, s) in enumerate(row3):
        fill = colors.white if i == 1 else ground
        _box(d, xs[i], ys[2], bw, bh, f"{t}\n{s}", fill)
    for i in range(n - 1):
        _arrow(d, xs[i] + bw, ys[0] + bh / 2, xs[i + 1], ys[0] + bh / 2)
        _arrow(d, xs[i + 1], ys[1] + bh / 2, xs[i] + bw, ys[1] + bh / 2)
        _arrow(d, xs[i] + bw, ys[2] + bh / 2, xs[i + 1], ys[2] + bh / 2)
    _arrow(d, xs[4] + bw / 2, ys[0], xs[4] + bw / 2, ys[1] + bh)
    _arrow(d, xs[0] + bw / 2, ys[1], xs[0] + bw / 2, ys[2] + bh)
    # legend
    ly = 4
    for k, (label, fill) in enumerate([("Satellite: processing (PL)", onboard),
                                       ("Satellite: channel coding (PL)", channel),
                                       ("Ground: receiver (PL) and PC", ground)]):
        x = k * (width / 3)
        d.add(Rect(x, ly, 10, 8, fillColor=fill, strokeColor=RULE, strokeWidth=0.6))
        d.add(String(x + 14, ly + 1, label, fontName="UI", fontSize=7.4, fillColor=INK2))
    return d


def flow_diagram(width=TEXT_W):
    """The work method: specification -> model -> RTL -> tests -> board -> record."""
    items = [("Specification", "SPEC.md, IDs"), ("Python model", "bit-exact"),
             ("RTL", "SystemVerilog"), ("Simulation", "compare with model"),
             ("Build", "timing, CDC"), ("Board test", "JTAG, compare"), ("Record", "commit, notes")]
    n = len(items)
    gap = 9
    bw = (width - (n - 1) * gap) / n
    bh = 36
    h = bh + 46
    d = Drawing(width, h)
    y = h - bh - 6
    for i, (t, s) in enumerate(items):
        x = i * (bw + gap)
        _box(d, x, y, bw, bh, f"{t}\n{s}", BLUE_LIGHT if i not in (3, 5) else ORANGE_LIGHT, size=7.4)
        if i < n - 1:
            _arrow(d, x + bw, y + bh / 2, x + bw + gap, y + bh / 2)
    # loop back from the tests to the RTL / model
    x_sim = 3 * (bw + gap) + bw / 2
    x_brd = 5 * (bw + gap) + bw / 2
    x_rtl = 2 * (bw + gap) + bw / 2
    yb = y - 16
    for xs in (x_sim, x_brd):
        d.add(Line(xs, y, xs, yb, strokeColor=ORANGE, strokeWidth=1))
    d.add(Line(x_brd, yb, x_rtl, yb, strokeColor=ORANGE, strokeWidth=1))
    _arrow(d, x_rtl, yb, x_rtl, y, color=ORANGE)
    d.add(String((x_rtl + x_brd) / 2, yb - 10, "difference found: find the cause, correct, test again",
                 fontName="UI-Italic", fontSize=7.4, fillColor=INK2, textAnchor="middle"))
    return d


# ---------------------------------------------------------------------------
# Document template: cover page, body pages with header / footer, TOC
# ---------------------------------------------------------------------------
class HpdpDoc(BaseDocTemplate):
    def __init__(self, path, title, doc_id, revision, date, subtitle="", author="",
                 toc=False):
        super().__init__(str(path), pagesize=A4, leftMargin=MARGIN_L, rightMargin=MARGIN_R,
                         topMargin=MARGIN_T, bottomMargin=MARGIN_B, title=title,
                         author=author, subject=subtitle, creator="docs/pdf/build_docs.py")
        self.doc_title, self.doc_id, self.revision, self.date = title, doc_id, revision, date
        self.subtitle, self.author_name, self.use_toc = subtitle, author, toc
        frame = Frame(MARGIN_L, MARGIN_B, TEXT_W, PAGE_H - MARGIN_T - MARGIN_B, id="body",
                      leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)
        self.addPageTemplates([PageTemplate("cover", frames=[frame], onPage=self._cover),
                               PageTemplate("body", frames=[frame], onPage=self._body)])

    # page decorations -------------------------------------------------------
    def _cover(self, c, doc):
        c.saveState()
        c.setFillColor(ACCENT_DARK)
        c.rect(0, PAGE_H - 9 * mm, PAGE_W, 9 * mm, stroke=0, fill=1)
        c.setFillColor(ACCENT)
        c.rect(0, PAGE_H - 11 * mm, PAGE_W, 2 * mm, stroke=0, fill=1)
        c.setFont("UI", 8)
        c.setFillColor(MUTED)
        c.drawString(MARGIN_L, 12 * mm, f"{self.doc_id}  \u00b7  Revision {self.revision}  \u00b7  {self.date}")
        c.drawRightString(PAGE_W - MARGIN_R, 12 * mm, "Hyperspectral Payload Data Processor (HPDP)")
        c.restoreState()

    def _body(self, c, doc):
        c.saveState()
        c.setStrokeColor(RULE)
        c.setLineWidth(0.5)
        c.line(MARGIN_L, PAGE_H - 14 * mm, PAGE_W - MARGIN_R, PAGE_H - 14 * mm)
        c.setFont("UI", 7.8)
        c.setFillColor(INK2)
        c.drawString(MARGIN_L, PAGE_H - 12 * mm, self.doc_title)
        c.drawRightString(PAGE_W - MARGIN_R, PAGE_H - 12 * mm, f"{self.doc_id}  Rev. {self.revision}")
        c.line(MARGIN_L, 13 * mm, PAGE_W - MARGIN_R, 13 * mm)
        c.setFillColor(MUTED)
        c.drawString(MARGIN_L, 9 * mm, f"HPDP  \u00b7  {self.date}")
        c.drawRightString(PAGE_W - MARGIN_R, 9 * mm, f"Page {doc.page}")
        c.restoreState()

    def afterFlowable(self, flowable):
        if isinstance(flowable, Heading):
            key = f"h{id(flowable)}"
            self.canv.bookmarkPage(key)
            self.canv.addOutlineEntry(flowable.toc_text.replace("&amp;", "&"), key,
                                      level=flowable.toc_level, closed=flowable.toc_level > 0)
            self.notify("TOCEntry", (flowable.toc_level, flowable.toc_text, self.page, key))

    def handle_keepWithNext(self, flowables):
        # reportlab does not join a heading with a container (table, note, figure, list),
        # so the heading can stay alone at the bottom of a page. Merge them here.
        i = 0
        while (i < len(flowables) and flowables[i].getKeepWithNext()
               and not isinstance(flowables[i], (KeepTogether, ListFlowable))):
            i += 1
        if i and i < len(flowables) and isinstance(flowables[i], (KeepTogether, ListFlowable)):
            nxt = flowables[i]
            body = list(nxt._content) if type(nxt) is KeepTogether else [nxt]
            heads = flowables[:i]
            mbe = getattr(self, "_multiBuildEdits", None)
            for f in heads:  # as in reportlab: clear the flag, restore it for the next pass
                if mbe:
                    if "keepWithNext" in f.__dict__:
                        mbe((setattr, f, "keepWithNext", f.keepWithNext))
                    else:
                        mbe((delattr, f, "keepWithNext"))
                f.__dict__["keepWithNext"] = 0
            del flowables[:i + 1]
            flowables.insert(0, KeepTogether(heads + body))
            return
        super().handle_keepWithNext(flowables)

    # building ------------------------------------------------------------------
    def cover_flowables(self, rows, extra=None):
        title = ParagraphStyle("ct", fontName="UI-Bold", fontSize=26, leading=31, textColor=INK)
        sub = ParagraphStyle("cs", fontName="UI", fontSize=12.5, leading=17, textColor=INK2)
        flow = [Spacer(1, 38 * mm), Paragraph(self.doc_title, title), Spacer(1, 4 * mm),
                Paragraph(self.subtitle, sub), Spacer(1, 12 * mm)]
        meta = Table([[Paragraph(f"<b>{k}</b>", CELL), Paragraph(v, CELL)] for k, v in rows],
                     colWidths=[42 * mm, TEXT_W - 42 * mm], hAlign="LEFT")
        meta.setStyle(TableStyle([
            ("LINEBELOW", (0, 0), (-1, -1), 0.4, GRID),
            ("LINEABOVE", (0, 0), (-1, 0), 0.8, RULE),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ]))
        flow.append(meta)
        if extra:
            flow += [Spacer(1, 10 * mm)] + extra
        flow.append(NextPageTemplate("body"))
        flow.append(PageBreak())
        if self.use_toc:
            toc = TableOfContents()
            toc.levelStyles = [TOC1, TOC2]
            toc.dotsMinLevel = 0
            toc.tableStyle = TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"),
                                         ("LEFTPADDING", (0, 0), (-1, -1), 0),
                                         ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                                         ("TOPPADDING", (0, 0), (-1, -1), 1),
                                         ("BOTTOMPADDING", (0, 0), (-1, -1), 1)])
            flow += [Paragraph("Contents", H1), toc, PageBreak()]
        return flow

    def make(self, story):
        if self.use_toc:
            self.multiBuild(story)
        else:
            self.build(story)
