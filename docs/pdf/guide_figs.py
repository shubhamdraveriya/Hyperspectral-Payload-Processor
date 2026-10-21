"""Teaching diagrams for the HPDP Learning Guide (reportlab graphics).

Each function returns a Drawing that is TEXT_W wide. The colours and the box and
arrow helpers come from hpdp_pdf, so the guide looks like the other HPDP documents."""

import math

from reportlab.graphics.shapes import Drawing, Group, Line, PolyLine, Polygon, Rect, String
from reportlab.lib import colors

from hpdp_pdf import (ACCENT, ACCENT_DARK, BLUE_LIGHT, GRID, INK, INK2, MUTED, ORANGE, ORANGE_LIGHT,
                      PANEL, RULE, TEXT_W, _arrow, _box)

WHITE = colors.white
GREEN_LIGHT = colors.HexColor("#dcefe2")
BAND = [colors.HexColor(c) for c in ("#cde2fb", "#8dbbf0", "#4a8fdd", "#1f5fae")]


def _text(d, x, y, s, size=7.4, bold=False, color=INK, anchor="start", italic=False):
    font = "UI-Bold" if bold else ("UI-Italic" if italic else "UI")
    d.add(String(x, y, s, fontName=font, fontSize=size, fillColor=color, textAnchor=anchor))


def _cell(d, x, y, w, h, fill, label="", size=6.6, color=INK, stroke=RULE):
    d.add(Rect(x, y, w, h, fillColor=fill, strokeColor=stroke, strokeWidth=0.5))
    if label:
        _text(d, x + w / 2, y + h / 2 - size / 2 + 1, label, size=size, anchor="middle", color=color)


# ---------------------------------------------------------------------------
# Part A
# ---------------------------------------------------------------------------
def four_problems(width=TEXT_W):
    rows = [("The camera makes too much data", "921.6 Mbit/s raw; the link carries 857 Mbit/s",
             "Lossless compression (CCSDS 123)", "approximately 2 to 3 times less data"),
            ("The link adds bit errors", "a noisy radio channel in a real satellite",
             "Error correction (Reed-Solomon)", "the ground corrects up to 16 bad bytes per codeword"),
            ("The ground must find each block", "the data is one long stream of bits",
             "Sync markers, packets and frames", "known patterns and headers mark each block"),
            ("Radiation changes bits", "in the FPGA memory and registers",
             "Scrubbing, ECC, TMR, watchdogs", "the HPDP finds and corrects the changes")]
    bh, gap = 34, 8
    h = len(rows) * (bh + gap) + 18
    d = Drawing(width, h)
    wl = width * 0.42
    x2 = width * 0.55
    wr = width - x2
    _text(d, 0, h - 9, "Problem", bold=True, size=8, color=INK2)
    _text(d, x2, h - 9, "What the HPDP does", bold=True, size=8, color=INK2)
    for i, (p1, p2, s1, s2) in enumerate(rows):
        y = h - 18 - (i + 1) * bh - i * gap
        _box(d, 0, y, wl, bh, f"{p1}\n{p2}", ORANGE_LIGHT)
        _box(d, x2, y, wr, bh, f"{s1}\n{s2}", BLUE_LIGHT)
        _arrow(d, wl + 4, y + bh / 2, x2 - 4, y + bh / 2)
    return d


def _vtext(d, x, y, s, size=7, color=INK):
    """Text turned 90 degrees, read from bottom to top, centred on (x, y)."""
    g = Group(String(0, 0, s, fontName="UI", fontSize=size, fillColor=color, textAnchor="middle"))
    g.transform = (0, 1, -1, 0, x, y)
    d.add(g)


def pushbroom_cube(width=TEXT_W):
    h = 186
    d = Drawing(width, h)
    # (a) pushbroom: a ground strip, the slit line and the flight direction
    gx, gy, gw, gh = 4, 30, 116, 104
    d.add(Polygon([gx, gy, gx + gw, gy, gx + gw + 26, gy + gh, gx + 26, gy + gh],
                  fillColor=GREEN_LIGHT, strokeColor=RULE, strokeWidth=0.8))
    for k in range(1, 7):
        yy = gy + k * gh / 7
        xx = gx + 26 * k / 7
        d.add(Line(xx, yy, xx + gw, yy, strokeColor=colors.HexColor("#b9d8c2"), strokeWidth=0.5))
    sy = gy + 4 * gh / 7
    sx = gx + 26 * 4 / 7
    # the satellite above, with its view to the slit
    cx, cy = gx + 78, h - 16
    d.add(Polygon([cx, cy - 6, sx, sy, sx + gw, sy], fillColor=colors.HexColor("#e9f1fb"),
                  strokeColor=colors.HexColor("#9fc3ec"), strokeWidth=0.5))
    d.add(Line(sx, sy, sx + gw, sy, strokeColor=ACCENT_DARK, strokeWidth=2.4))
    d.add(Rect(cx - 12, cy - 6, 24, 12, fillColor=PANEL, strokeColor=INK2, strokeWidth=0.8))
    d.add(Rect(cx - 32, cy - 3, 18, 6, fillColor=BLUE_LIGHT, strokeColor=INK2, strokeWidth=0.6))
    d.add(Rect(cx + 14, cy - 3, 18, 6, fillColor=BLUE_LIGHT, strokeColor=INK2, strokeWidth=0.6))
    _text(d, sx + gw + 4, sy - 3, "slit", size=7, color=ACCENT_DARK, bold=True)
    ax = gx + gw + 44
    _arrow(d, ax, gy + 8, ax, gy + gh - 4, color=INK2)
    _vtext(d, ax + 9, gy + gh / 2, "flight direction")
    _text(d, gx, 10, "(a) Pushbroom scan", size=7.2, italic=True, color=INK2)

    # (b) the cube: x pixels, y lines, z bands
    ox, oy, cw, ch, dx, dy = 222, 36, 96, 80, 40, 30
    for k in range(5, -1, -1):
        fx, fy = ox + dx * k / 5, oy + dy * k / 5
        fill = BAND[min(3, k * 4 // 6)] if k else colors.HexColor("#f4f8fd")
        d.add(Rect(fx, fy, cw, ch, fillColor=fill,
                   strokeColor=ACCENT_DARK if k == 0 else colors.HexColor("#7aa6dc"), strokeWidth=0.7))
    for i in range(1, 8):
        d.add(Line(ox + i * cw / 8, oy, ox + i * cw / 8, oy + ch, strokeColor=GRID, strokeWidth=0.4))
    for j in range(1, 6):
        d.add(Line(ox, oy + j * ch / 6, ox + cw, oy + j * ch / 6, strokeColor=GRID, strokeWidth=0.4))
    d.add(Rect(ox + 3 * cw / 8, oy + 3 * ch / 6, cw / 8, ch / 6, fillColor=ORANGE, strokeColor=ORANGE))
    _text(d, ox + cw / 2, oy - 11, "x: 512 pixels", size=7, anchor="middle")
    _vtext(d, ox - 7, oy + ch / 2, "y: lines, 1000 each s")
    _text(d, ox + cw + dx + 3, oy + ch + dy - 6, "z: 150 bands", size=7)
    _text(d, ox - 6, 10, "(b) Image cube: one cell is one sample", size=7.2, italic=True, color=INK2)

    # (c) the spectrum of the orange pixel
    px, py, pw, ph = width - 74, 48, 70, 70
    d.add(Line(px, py, px + pw, py, strokeColor=INK2, strokeWidth=0.7))
    d.add(Line(px, py, px, py + ph, strokeColor=INK2, strokeWidth=0.7))
    pts = []
    for i in range(41):
        b = i / 40
        v = 0.25 + 0.45 * math.exp(-((b - 0.3) / 0.12) ** 2) + 0.3 * math.exp(-((b - 0.72) / 0.18) ** 2)
        v -= 0.18 * math.exp(-((b - 0.52) / 0.04) ** 2)
        pts += [px + b * pw, py + v * ph]
    d.add(PolyLine(pts, strokeColor=ORANGE, strokeWidth=1.4))
    _text(d, px + pw / 2, py - 10, "band", size=7, anchor="middle")
    _vtext(d, px - 6, py + ph / 2, "value")
    _text(d, px - 8, 10, "(c) One spectrum", size=7.2, italic=True, color=INK2)
    return d


def board_diagram(width=TEXT_W):
    h = 196
    d = Drawing(width, h)
    _box(d, 0, 112, 70, 44, "PC\nPython tools", PANEL)
    bx, by, bw, bh = 92, 6, width - 92, 182
    d.add(Rect(bx, by, bw, bh, rx=6, ry=6, fillColor=colors.HexColor("#fafaf8"), strokeColor=INK2,
               strokeWidth=0.9))
    _text(d, bx + 8, by + bh - 13, "ZCU106 evaluation board", bold=True, size=8)
    # the Zynq device
    zx, zy, zw, zh = bx + 70, by + 26, 190, 118
    d.add(Rect(zx, zy, zw, zh, rx=4, ry=4, fillColor=WHITE, strokeColor=ACCENT_DARK, strokeWidth=1.1))
    _text(d, zx + 6, zy + zh - 11, "Zynq UltraScale+ XCZU7EV", bold=True, size=7.6, color=ACCENT_DARK)
    _box(d, zx + 8, zy + 10, 80, 84, "PS\n4 × Arm A53\none runs hpdp_run\nDDR4 controller", BLUE_LIGHT,
         size=7)
    _box(d, zx + 100, zy + 10, 82, 84, "PL\nthe HPDP data\nchain (your logic)\nGTH transceivers",
         ORANGE_LIGHT, size=7)
    _arrow(d, zx + 88, zy + 60, zx + 100, zy + 60)
    _arrow(d, zx + 100, zy + 44, zx + 88, zy + 44)
    # memory, SD card and USB on the left
    _box(d, bx + 8, by + 92, 54, 30, "DDR4\nSODIMM", PANEL, size=7)
    _box(d, bx + 8, by + 44, 54, 30, "SD card\nscenes", PANEL, size=7)
    d.add(Line(bx + 62, by + 107, zx + 8, by + 107, strokeColor=INK2, strokeWidth=0.8))
    d.add(Line(bx + 62, by + 59, zx + 8, by + 59, strokeColor=INK2, strokeWidth=0.8))
    _box(d, bx + 8, by + 136, 54, 26, "USB\nJTAG + UART", PANEL, size=6.6)
    _arrow(d, 70, 140, bx + 8, by + 152)
    _arrow(d, bx + 8, by + 146, 70, 134)
    # SFP cages and the fiber loop
    sx = zx + zw + 16
    _box(d, sx, zy + 70, 56, 30, "SFP0\ntransmitter", BLUE_LIGHT, size=6.8)
    _box(d, sx, zy + 18, 56, 30, "SFP1\nreceiver", ORANGE_LIGHT, size=6.8)
    d.add(Line(zx + 182, zy + 85, sx, zy + 85, strokeColor=INK2, strokeWidth=0.8))
    d.add(Line(zx + 182, zy + 33, sx, zy + 33, strokeColor=INK2, strokeWidth=0.8))
    fx = sx + 56
    loop = [fx, zy + 85, fx + 6, zy + 85]
    for i in range(21):  # a half circle from the SFP0 port to the SFP1 port
        a = math.pi * i / 20
        loop += [fx + 6 + 22 * math.sin(a), zy + 59 + 26 * math.cos(a)]
    loop += [fx + 6, zy + 33, fx, zy + 33]
    d.add(PolyLine(loop, strokeColor=ORANGE, strokeWidth=2.2))
    _text(d, fx + 32, zy + 60, "OM3", size=7, bold=True, color=ORANGE)
    _text(d, fx + 32, zy + 51, "fiber", size=7, color=ORANGE)
    return d


# ---------------------------------------------------------------------------
# Part B
# ---------------------------------------------------------------------------
def bil_bip(width=TEXT_W):
    h = 128
    d = Drawing(width, h)
    n = 9
    cw = 34
    x0 = 120
    labels_bil = [(b, p) for b in range(3) for p in range(3)]
    labels_bip = [(b, p) for p in range(3) for b in range(3)]
    for row, (name, sub, labs, y) in enumerate([
            ("BIL", "from the sensor", labels_bil, 82),
            ("BIP", "to the compressor", labels_bip, 18)]):
        _text(d, 0, y + 12, name, bold=True, size=9)
        _text(d, 0, y + 1, sub, size=7.2, color=INK2)
        for i, (b, p) in enumerate(labs):
            _cell(d, x0 + i * (cw + 3), y, cw, 24, BAND[b + 1 if b else 0], f"b{b} p{p}", size=7,
                  color=WHITE if b == 2 else INK)
            _text(d, x0 + i * (cw + 3) + cw / 2, y + 28, str(i + 1), size=6.4, anchor="middle", color=MUTED)
    _arrow(d, x0 + 4.5 * (cw + 3), 78, x0 + 4.5 * (cw + 3), 48, color=ORANGE, width=1.4)
    _text(d, x0 + 4.5 * (cw + 3) + 8, 60, "corner turn (two UltraRAM line buffers)", size=7.2, bold=True,
          color=ORANGE)
    _text(d, x0 + n * (cw + 3) + 6, 92, "b = band", size=7, color=INK2)
    _text(d, x0 + n * (cw + 3) + 6, 82, "p = pixel", size=7, color=INK2)
    _text(d, x0 + n * (cw + 3) + 6, 28, "small numbers:", size=7, color=INK2)
    _text(d, x0 + n * (cw + 3) + 6, 19, "time sequence", size=7, color=INK2)
    return d


def neighbourhood(width=TEXT_W):
    h = 176
    d = Drawing(width, h)
    c = 26
    gx, gy = 40, 30
    # previous bands behind
    for L in (3, 2, 1):
        ox, oy = gx + L * 12, gy + L * 12
        for r in range(3):
            for k in range(5):
                d.add(Rect(ox + k * c, oy + r * c, c, c, fillColor=colors.HexColor("#eef4fc"),
                           strokeColor=colors.HexColor("#b9d0ee"), strokeWidth=0.4))
        d.add(Rect(ox + 2 * c, oy + 1 * c, c, c, fillColor=BAND[1], strokeColor=ACCENT, strokeWidth=0.6))
    names = {(2, 1): "NW", (2, 2): "N", (2, 3): "NE", (1, 1): "W"}
    for r in range(3):
        for k in range(5):
            fill = WHITE
            if (r, k) in names:
                fill = BLUE_LIGHT
            if (r, k) == (1, 2):
                fill = ORANGE
            _cell(d, gx + k * c, gy + r * c, c, c, fill, names.get((r, k), ""), size=7.4,
                  color=INK)
    _text(d, gx + 2.5 * c, gy + 1.5 * c - 3, "?", size=10, bold=True, anchor="middle", color=WHITE)
    _text(d, gx, gy - 14, "current band: neighbours W, NW, N, NE", size=7.2, color=INK2)
    _text(d, gx + 12, gy + 3 * c + 36 + 8, "behind: earlier bands (up to P = 15)",
          size=7.2, color=INK2)
    # flow on the right
    fx = 260
    items = [("Predictor", "weighted sum, weights adapt"), ("Residual", "actual − predicted"),
             ("Mapper", "signed → small positive number"), ("Entropy coder", "short code for small numbers")]
    bw, bh, gap = width - fx, 26, 9
    for i, (t, s) in enumerate(items):
        y = h - 12 - (i + 1) * bh - i * gap
        _box(d, fx, y, bw, bh, f"{t}\n{s}", BLUE_LIGHT if i != 1 else ORANGE_LIGHT, size=7.2)
        if i:
            _arrow(d, fx + bw / 2, y + bh + gap, fx + bw / 2, y + bh)
    _arrow(d, gx + 5 * c + 44, gy + 2 * c + 12, fx - 4, h - 12 - bh / 2)
    return d


def nesting(width=TEXT_W):
    h = 196
    d = Drawing(width, h)
    lx = 96
    W = width - lx
    rows = [
        ("Space Packet", "2062 bytes", [(14, "header 14", PANEL), (2048, "compressed data 2048 bytes", BLUE_LIGHT)]),
        ("AOS frame", "892 bytes", [(6, "", PANEL), (2, "", ORANGE_LIGHT), (884, "packet zone 884 bytes (parts of packets)", BLUE_LIGHT)]),
        ("RS codeblock", "1020 bytes", [(892, "one AOS frame 892 bytes", BLUE_LIGHT), (128, "parity 128", ORANGE_LIGHT)]),
        ("CADU", "1024 bytes", [(4, "", ORANGE), (1020, "randomized codeblock 1020 bytes", BLUE_LIGHT)]),
    ]
    rh, gap = 26, 20
    for i, (name, size, segs) in enumerate(rows):
        y = h - 10 - (i + 1) * rh - i * gap
        _text(d, 0, y + rh / 2 + 1, name, bold=True, size=8)
        _text(d, 0, y + rh / 2 - 9, size, size=7, color=INK2)
        # widths are not to scale: the headers would be too thin to see
        widths = {0: {14: 44, 2048: W - 44}, 1: {6: 26, 2: 16, 884: W - 42},
                  2: {892: W * 0.8, 128: W * 0.2}, 3: {4: 30, 1020: W - 30}}[i]
        x = lx
        for s, lab, fill in segs:
            w = widths[s]
            d.add(Rect(x, y, w, rh, fillColor=fill, strokeColor=RULE, strokeWidth=0.6))
            if lab:
                _text(d, x + w / 2, y + rh / 2 - 3, lab, size=7, anchor="middle")
            x += w
        if i < 3:
            _arrow(d, lx + W / 2, y - 2, lx + W / 2, y - gap + 2, color=INK2)
    # small legends for the narrow parts
    y1 = h - 10 - 2 * rh - gap
    _text(d, lx, y1 - 9, "frame header 6 + pointer 2", size=6.6, color=INK2)
    y3 = h - 10 - 4 * rh - 3 * gap
    _text(d, lx, y3 - 9, "sync marker 1ACFFC1D (4 bytes, not randomized)", size=6.6, color=INK2)
    return d


def interleave(width=TEXT_W):
    h = 150
    d = Drawing(width, h)
    n = 24
    cw = (width - 110) / n
    x0 = 100
    y_tx = h - 40
    _text(d, 0, y_tx + 8, "Bytes on the link", bold=True, size=8)
    _text(d, 0, y_tx - 2, "(time sequence)", size=7, color=INK2)
    for i in range(n):
        _cell(d, x0 + i * cw, y_tx, cw - 1, 20, BAND[i % 4], str(i % 4 + 1), size=6.8,
              color=WHITE if i % 4 >= 2 else INK)
    b0, b1 = 9, 17
    d.add(Rect(x0 + b0 * cw - 1, y_tx - 3, (b1 - b0) * cw + 1, 26, fillColor=None, strokeColor=ORANGE,
               strokeWidth=2))
    _text(d, x0 + (b0 + b1) / 2 * cw, y_tx + 28, "burst: 8 bytes in a row are bad", size=7.2, bold=True,
          color=ORANGE, anchor="middle")
    # codewords
    for k in range(4):
        y = 14 + (3 - k) * 18
        _text(d, 0, y + 4, f"Codeword {k + 1}", size=7.4)
        for j in range(n // 4):
            i = j * 4 + k
            bad = b0 <= i < b1
            _cell(d, x0 + j * (cw * 1.6), y, cw * 1.6 - 1, 14, ORANGE if bad else BAND[k], "",
                  stroke=RULE)
        _text(d, x0 + (n // 4) * cw * 1.6 + 8, y + 4, "2 bad bytes: RS corrects them (limit 16)", size=7,
              color=INK2)
    return d


def b8b10(width=TEXT_W):
    h = 120
    d = Drawing(width, h)
    rows = [("Data byte 1A (D26.0)", "0101101011", BLUE_LIGHT, "6 ones, 4 zeros"),
            ("Comma K28.5", "0011111010", ORANGE_LIGHT, "a pattern that data never makes")]
    cw = 17
    x0 = 120
    for r, (name, bits, fill, note) in enumerate(rows):
        y = h - 34 - r * 44
        _text(d, 0, y + 10, name, bold=True, size=8)
        _text(d, 0, y - 1, note, size=7, color=INK2)
        for i, b in enumerate(bits):
            _cell(d, x0 + i * cw + (6 if i >= 6 else 0), y, cw - 1, 20, fill, b, size=8)
        # the same bits as a signal on the line
        wx = x0 + 10 * cw + 30
        pts = []
        for i, b in enumerate(bits):
            yy = y + (18 if b == "1" else 2)
            pts += [wx + i * 13, yy, wx + (i + 1) * 13, yy]
        d.add(PolyLine(pts, strokeColor=ACCENT_DARK if r == 0 else ORANGE, strokeWidth=1.4))
    _text(d, x0 + 10 * cw + 30, h - 10, "signal on the line", size=7, color=INK2)
    _text(d, x0, 6, "8 bits become 10 bits. Never more than 5 equal bits in a row.", size=7.2, color=INK2)
    return d


def chain_row(items, width=TEXT_W, fills=None, h=None, size=7.0):
    """A row of boxes with arrows: items are (title, subtitle)."""
    n = len(items)
    gap = 10
    bw = (width - (n - 1) * gap) / n
    bh = 40
    h = h or bh + 8
    d = Drawing(width, h)
    y = (h - bh) / 2
    for i, (t, s) in enumerate(items):
        fill = fills[i] if fills else BLUE_LIGHT
        _box(d, i * (bw + gap), y, bw, bh, f"{t}\n{s}", fill, size=size)
        if i < n - 1:
            _arrow(d, i * (bw + gap) + bw, y + bh / 2, (i + 1) * (bw + gap), y + bh / 2)
    return d


def link_rx(width=TEXT_W):
    return chain_row([("SFP1", "light to voltage"), ("CDR", "finds the bit clock"),
                      ("Deserializer", "20 bits per word"), ("Comma align", "K28.5 in byte 0"),
                      ("8b/10b decode", "10 bits → 8 bits"), ("Elastic buffer", "clock correction"),
                      ("Lock FSM", "1024 clean words")], width, size=6.6)


def sync_fsm(width=TEXT_W):
    h = 120
    d = Drawing(width, h)
    names = ["SEARCH", "VERIFY", "LOCK", "FLYWHEEL"]
    bw, bh = 84, 30
    gap = (width - 4 * bw) / 3
    y = 52
    xs = [i * (bw + gap) for i in range(4)]
    for i, nme in enumerate(names):
        _box(d, xs[i], y, bw, bh, nme, ORANGE_LIGHT if nme == "LOCK" else BLUE_LIGHT, size=8)
    fwd = ["ASM found (2 bad bits or fewer)", "ASM again 1024 bytes later", "an ASM is missing"]
    for i in range(3):
        _arrow(d, xs[i] + bw, y + bh - 8, xs[i + 1], y + bh - 8)
        _text(d, (xs[i] + bw + xs[i + 1]) / 2, y + bh + 6, fwd[i], size=6.8, anchor="middle")
    # returns
    _arrow(d, xs[1], y + 8, xs[0] + bw, y + 8, color=ORANGE)
    _text(d, (xs[0] + bw + xs[1]) / 2, y - 6, "no ASM there", size=6.8, anchor="middle", color=ORANGE)
    _arrow(d, xs[3], y + 8, xs[2] + bw, y + 8, color=ORANGE)
    _text(d, (xs[2] + bw + xs[3]) / 2, y - 6, "ASM found again", size=6.8, anchor="middle", color=ORANGE)
    # flywheel back to search
    yb = 14
    d.add(Line(xs[3] + bw / 2, y, xs[3] + bw / 2, yb, strokeColor=ORANGE, strokeWidth=1))
    d.add(Line(xs[3] + bw / 2, yb, xs[0] + bw / 2, yb, strokeColor=ORANGE, strokeWidth=1))
    _arrow(d, xs[0] + bw / 2, yb, xs[0] + bw / 2, y, color=ORANGE)
    _text(d, (xs[0] + xs[3]) / 2 + bw / 2, yb + 4, "more than 3 ASMs missing in a row (FLYWHEEL = 3)", size=6.8,
          anchor="middle", color=ORANGE)
    return d


# ---------------------------------------------------------------------------
# Part C
# ---------------------------------------------------------------------------
def clock_domains(width=TEXT_W):
    h = 150
    d = Drawing(width, h)
    bw, bh = 118, 46
    boxes = {
        "ctrl": (0, 96, "clk_ctrl  100 MHz", "registers, AXI4-Lite"),
        "sensor": (0, 20, "clk_sensor  100 MHz", "sensor emulator output"),
        "sys": ((width - bw) / 2, 58, "clk_sys  250 MHz", "data path: correction to RS"),
        "link": (width - bw, 58, "clk_link  62.5 MHz", "link TX and RX (GTH)"),
        "fr": (width - bw, 0, "clk_fr  31.25 MHz", "GTH helper, SEM, debug"),
    }
    for k, (x, y, t, s) in boxes.items():
        _box(d, x, y, bw, bh if k != "fr" else 34, f"{t}\n{s}",
             ORANGE_LIGHT if k == "sys" else BLUE_LIGHT, size=7.2)
    sx = boxes["sys"][0]
    _arrow(d, bw, 43, sx, 70, color=ACCENT_DARK, width=1.2)
    _text(d, bw + 6, 32, "async FIFO", size=6.8, color=ACCENT_DARK)
    _arrow(d, bw, 119, sx, 96, color=INK2)
    _arrow(d, sx, 90, bw, 113, color=INK2)
    _text(d, bw + 8, 124, "handshake snapshots", size=6.8, color=INK2)
    lx = boxes["link"][0]
    _arrow(d, sx + bw, 88, lx, 88, color=ACCENT_DARK, width=1.2)
    _arrow(d, lx, 72, sx + bw, 72, color=ACCENT_DARK, width=1.2)
    _text(d, (sx + bw + lx) / 2, 93, "async FIFOs", size=6.8, anchor="middle", color=ACCENT_DARK)
    return d


def tmr(width=TEXT_W):
    h = 120
    d = Drawing(width, h)
    ys = [80, 46, 12]
    for i, y in enumerate(ys):
        hit = i == 1
        _box(d, 40, y, 90, 26, f"Copy {'ABC'[i]}\n{'state 0110' if not hit else 'state 0100 (upset)'}",
             ORANGE_LIGHT if hit else BLUE_LIGHT, size=7.2)
        _arrow(d, 130, y + 13, 200, 59)
    _box(d, 200, 40, 90, 38, "Voter\n2 out of 3 win", PANEL, size=7.4)
    _arrow(d, 290, 59, 350, 59)
    _box(d, 350, 44, width - 350, 30, "Output\nstate 0110 (correct)", GREEN_LIGHT, size=7.4)
    d.add(Line(245, 40, 245, 4, strokeColor=ORANGE, strokeWidth=1))
    d.add(Line(245, 4, 20, 4, strokeColor=ORANGE, strokeWidth=1))
    d.add(Line(20, 4, 20, 93, strokeColor=ORANGE, strokeWidth=1))
    for y in ys:
        _arrow(d, 20, y + 13, 40, y + 13, color=ORANGE)
    _text(d, 252, 8, "each copy is written again from the vote in each clock cycle", size=6.8, color=ORANGE)
    return d


def board_run(width=TEXT_W):
    items = [("run_m5.py", "makes a job on the PC"), ("JTAG", "bitstream, app, job"),
             ("hpdp_run", "sets registers, starts"), ("Data chain", "runs the scenes"),
             ("Result block", "counters, captures"), ("JTAG read", "records to the PC"),
             ("hpdp_gs.py", "decode and compare")]
    fills = [PANEL, PANEL, BLUE_LIGHT, ORANGE_LIGHT, BLUE_LIGHT, PANEL, PANEL]
    return chain_row(items, width, fills=fills, size=6.6)
