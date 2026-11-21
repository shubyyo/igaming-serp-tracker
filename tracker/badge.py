"""Shields-style SVG badge: [ label | 6.2 elevated ]."""
from xml.sax.saxutils import escape

COLORS = {
    "calm": "#1e6b48",
    "normal": "#4f6b7a",
    "elevated": "#b9770e",
    "storm": "#b3372f",
    None: "#7c8590",
}
LABEL_BG = "#0f2a24"


def _width(text):
    # Rough Verdana 11px advance widths; good enough for a 20px badge.
    w = 0.0
    for ch in text:
        if ch in "il.,:;|!'":
            w += 3.2
        elif ch in "mwMW":
            w += 9.6
        elif ch.isupper() or ch.isdigit():
            w += 7.4
        elif ch == " ":
            w += 3.6
        else:
            w += 6.4
    return int(w + 12)


def render(label, score, band_name, provisional=False):
    value = "no data" if score is None else "%.1f %s" % (score, band_name)
    if provisional and score is not None:
        value += "*"
    lw, vw = _width(label), _width(value)
    total = lw + vw
    color = COLORS.get(band_name, COLORS[None])
    title = "%s: %s%s" % (label, value, " (provisional)" if provisional else "")
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="{t}" height="20" role="img" aria-label="{title}">'
        '<title>{title}</title>'
        '<clipPath id="r"><rect width="{t}" height="20" rx="4"/></clipPath>'
        '<g clip-path="url(#r)"><rect width="{lw}" height="20" fill="{lb}"/>'
        '<rect x="{lw}" width="{vw}" height="20" fill="{c}"/></g>'
        '<g fill="#fff" font-family="Verdana,DejaVu Sans,Geneva,sans-serif" font-size="11" text-anchor="middle">'
        '<text x="{lx}" y="14">{label}</text><text x="{vx}" y="14" font-weight="bold">{value}</text></g></svg>'
    ).format(t=total, lw=lw, vw=vw, lb=LABEL_BG, c=color, lx=lw / 2, vx=lw + vw / 2,
             label=escape(label), value=escape(value), title=escape(title))
