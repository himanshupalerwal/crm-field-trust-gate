"""Draw the two flow diagrams as SVG and PNG (figures/fig1_gate.*, figures/fig2_metadata_sources.*).
Sized for a ~700px article column: 880 units wide, body text 15, names 16.5."""
from pathlib import Path

import cairosvg

FIG = Path(__file__).parent / "figures"
FIG.mkdir(exist_ok=True)

INK, MUTED, EDGE, ACCENT, TINT = "#0b0b0b", "#52514e", "#8f8d87", "#2a78d6", "#f6f5f2"
FONT = "Inter, DejaVu Sans, sans-serif"
W = 880


def text(x, y, s, size=15, weight=400, color=INK, anchor="start"):
    s = s.replace("&", "&amp;").replace("<", "&lt;")
    return (f'<text x="{x}" y="{y}" font-family="{FONT}" font-size="{size}" '
            f'font-weight="{weight}" fill="{color}" text-anchor="{anchor}">{s}</text>')


def box(x, y, w, h, name, lines=(), accent=False, center=False):
    fill = f'fill="{ACCENT}" fill-opacity="0.10"' if accent else 'fill="#ffffff"'
    stroke = f'stroke="{ACCENT}" stroke-width="2"' if accent else f'stroke="{EDGE}" stroke-width="1.25"'
    out = [f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="8" {fill} {stroke}/>']
    cx, anchor, tx = (x + w / 2, "middle", None) if center else (x + 16, "start", None)
    out.append(text(cx, y + 28, name, 16.5, 600, anchor=anchor))
    for i, ln in enumerate(lines):
        out.append(text(cx, y + 52 + 20 * i, ln, 15, color=MUTED, anchor=anchor))
    return "".join(out)


def arrow(d):
    return (f'<path d="{d}" fill="none" stroke="{EDGE}" stroke-width="1.5" '
            f'marker-end="url(#arrow)"/>')


def line(d):
    return f'<path d="{d}" fill="none" stroke="{EDGE}" stroke-width="1.5"/>'


def svg(h, body, title):
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {h}" width="{W}" height="{h}">'
            f'<rect width="{W}" height="{h}" fill="#ffffff"/>'
            f'<defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" '
            f'markerHeight="7" orient="auto-start-reverse"><path d="M0 0L10 5L0 10z" fill="{EDGE}"/>'
            f'</marker></defs>{text(30, 46, title, 20, 600)}{body}</svg>')


def save(name, s):
    (FIG / f"{name}.svg").write_text(s)
    cairosvg.svg2png(bytestring=s.encode(), write_to=str(FIG / f"{name}.png"), scale=2)


def fig1_metadata_sources():
    b = []
    # row 1: who writes
    bw, gap, y0 = 196, 12, 76
    writers = [("Billing system", ["integration user", "billing_sync"], False),
               ("Contract system", ["integration user", "contract_sync"], False),
               ("Sales reps", ["edit fields by", "hand in the UI"], False),
               ("Renewal agent", ["writes only after", "the gate approves"], True)]
    for i, (n, ls, acc) in enumerate(writers):
        x = 30 + i * (bw + gap)
        b.append(box(x, y0, bw, 96, n, ls, accent=acc))
        b.append(line(f"M{x + bw / 2} {y0 + 96}V196"))
    b.append(line(f"M{30 + bw / 2} 196H{30 + 3 * (bw + gap) + bw / 2}"))
    b.append(arrow("M440 196V222"))
    # row 2: the CRM record
    b.append(f'<rect x="30" y="226" width="820" height="166" rx="10" fill="{TINT}" '
             f'stroke="{EDGE}" stroke-width="1.25"/>')
    b.append(text(46, 254, "CRM record", 16.5, 600))
    iw = 188
    parts = [("Field value", ["what every API", "call returns"]),
             ("Field history", ["who changed it,", "when, old and new"]),
             ("Sync timestamp", ["you add it; set", "on every sync run"]),
             ("Provenance", ["you add it: agent,", "run, inputs, confirm"])]
    for i, (n, ls) in enumerate(parts):
        b.append(box(46 + i * (iw + 8), 270, iw, 106, n, ls))
    # row 3: matching rules + what the gate receives
    b.append(box(30, 457, 300, 96, "Matching rules", ["how many records", "match this customer"]))
    b.append(box(390, 440, 460, 130, "What the gate receives",
                 ["source_system: from the writer", "last_synced: from the sync timestamp",
                  "last_writer: from history or provenance", "matches: from the matching rules"]))
    b.append(arrow("M620 392V436"))
    b.append(arrow("M330 505H386"))
    # row 4: the gate
    b.append(box(390, 610, 460, 76, "Field trust gate", ["proceed, draft or ask"], accent=True, center=True))
    b.append(arrow("M620 570V606"))
    save("fig2_metadata_sources",
         svg(714, "".join(b), "The gate reads metadata the CRM keeps, plus two fields you add"))


def fig2_gate():
    b = []
    b.append(box(30, 236, 170, 76, "Renewal agent", ["proposes a write"], center=True))
    b.append(box(246, 211, 286, 126, "Field trust gate",
                 ["Authority: which system wins", "Freshness: within its SLA",
                  "Identity: one resolved entity", "Provenance: allowed writer?"]))
    ox, ow = 578, 272
    outs = [("Ask", ["a check failed: ask the", "user or the field's owner"], False, 76),
            ("Draft", ["an input is advisory:", "a human approves it"], False, 236),
            ("Proceed", ["all inputs action-grade:", "write and stamp it"], True, 396)]
    for n, ls, acc, y in outs:
        b.append(box(ox, y, ow, 96, n, ls, accent=acc, center=True))
    b.append(arrow("M200 274H242"))
    b.append(arrow(f"M532 274H556V124H{ox - 4}"))
    b.append(arrow(f"M532 274H{ox - 4}"))
    b.append(arrow(f"M532 274H556V444H{ox - 4}"))
    b.append(arrow(f"M{ox + ow / 2} 492V532H115V316"))
    b.append(text(330, 522, "stamped values read as advisory until confirmed", 14,
                  color=MUTED, anchor="middle"))
    save("fig1_gate", svg(560, "".join(b), "Only action-grade inputs can drive an autonomous write"))


if __name__ == "__main__":
    fig1_metadata_sources()
    fig2_gate()
    print("wrote", sorted(p.name for p in FIG.glob("fig[12]*")))
