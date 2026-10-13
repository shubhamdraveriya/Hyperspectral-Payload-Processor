"""In-System IBERT 2-D eye scan (CSV from write_hw_sio_scan) -> SVG heat map
of log10(BER), with the scan's own opening figures. No dependencies.

    python sw/host/eye_svg.py <scan.csv> [<out.svg>]
"""
import math
import sys
from pathlib import Path


def read_scan(path):
    """(header dict, horizontal codes, [(vertical code, [BER, ...])])"""
    hdr, rows, cols = {}, [], None
    lines = Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
    it = iter(lines)
    for line in it:
        if line.startswith("Scan Start"):
            break
        k, _, v = line.partition(",")
        hdr[k.strip()] = v.strip()
    for line in it:
        if line.startswith("Scan End") or not line.strip():
            break
        f = line.split(",")
        if f[0] == "2d statistical":
            cols = [int(x) for x in f[1:] if x.strip()]
        else:
            rows.append((int(f[0]), [float(x) for x in f[1:len(cols) + 1]]))
    return hdr, cols, rows


def colour(ber, floor):
    """dark blue (open, BER <= floor) through green and yellow to red (BER 0.5)"""
    t = 0.0 if ber <= 0 else min(1.0, max(0.0, (math.log10(ber) - floor) / (math.log10(0.5) - floor)))
    stops = [(0.0, (16, 32, 96)), (0.35, (32, 144, 160)), (0.65, (120, 200, 80)), (0.85, (240, 220, 60)),
             (1.0, (220, 40, 30))]
    for (t0, c0), (t1, c1) in zip(stops, stops[1:]):
        if t <= t1:
            a = (t - t0) / (t1 - t0) if t1 > t0 else 0
            return "#%02x%02x%02x" % tuple(round(c0[i] + a * (c1[i] - c0[i])) for i in range(3))
    return "#dc281e"


def to_svg(path, out):
    hdr, cols, rows = read_scan(path)
    floor = math.log10(float(hdr.get("Dwell BER", "1e-6")))
    cw, ch, left, top = 4, 4, 60, 40
    w, h = len(cols) * cw, len(rows) * ch
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{left + w + 150}" height="{top + h + 60}" '
             f'font-family="sans-serif" font-size="11">',
             f'<rect width="100%" height="100%" fill="white"/>',
             f'<text x="{left}" y="16" font-size="13" font-weight="bold">{hdr.get("Scan Name", path)}</text>',
             f'<text x="{left}" y="31">{hdr.get("Date and Time Started", "")}: horizontal '
             f'{hdr.get("Horizontal Percentage", "?")} %, vertical {hdr.get("Vertical Percentage", "?")} %, '
             f'open area {hdr.get("Open Area", "?")}, dwell BER {hdr.get("Dwell BER", "?")}</text>']
    for r, (_, bers) in enumerate(rows):
        for c, ber in enumerate(bers):
            parts.append(f'<rect x="{left + c * cw}" y="{top + r * ch}" width="{cw}" height="{ch}" '
                         f'fill="{colour(ber, floor)}"/>')
    # axes: horizontal in UI, vertical in codes
    for c in range(0, len(cols), 16):
        x = left + c * cw
        parts.append(f'<text x="{x}" y="{top + h + 14}" text-anchor="middle">{cols[c] / (2 * max(cols)):+.2f}</text>')
    parts.append(f'<text x="{left + w / 2}" y="{top + h + 30}" text-anchor="middle">UI</text>')
    for r in range(0, len(rows), 8):
        parts.append(f'<text x="{left - 6}" y="{top + r * ch + 4}" text-anchor="end">{rows[r][0]}</text>')
    parts.append(f'<text x="14" y="{top + h / 2}" transform="rotate(-90 14 {top + h / 2})" '
                 f'text-anchor="middle">vertical offset (codes)</text>')
    # legend
    lx = left + w + 20
    for i in range(21):
        e = floor + i * (math.log10(0.5) - floor) / 20
        parts.append(f'<rect x="{lx}" y="{top + h - (i + 1) * h / 21}" width="16" height="{h / 21 + 0.5}" '
                     f'fill="{colour(10 ** e, floor)}"/>')
    parts.append(f'<text x="{lx + 22}" y="{top + h}">&lt;= 1e{floor:.0f}</text>')
    parts.append(f'<text x="{lx + 22}" y="{top + 10}">0.5</text>')
    parts.append(f'<text x="{lx}" y="{top + h + 30}">log10 BER</text>')
    parts.append("</svg>")
    Path(out).write_text("\n".join(parts), encoding="utf-8")
    return hdr


if __name__ == "__main__":
    src = sys.argv[1]
    dst = sys.argv[2] if len(sys.argv) > 2 else str(Path(src).with_suffix(".svg"))
    h = to_svg(src, dst)
    print(f"{dst}: horizontal {h.get('Horizontal Percentage')} %, vertical {h.get('Vertical Percentage')} %, "
          f"open area {h.get('Open Area')}")
