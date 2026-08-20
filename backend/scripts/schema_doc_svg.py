"""
schema_doc_svg.py — the relationship map, drawn as a hand-authored-looking SVG.

Matches the house style of the sibling diagrams in documents/System Design/
(caydex-report-architecture.svg, caydex-100-users-dataflow.svg): `rx=8` boxes,
1px category strokes over a very light tint, 1.5px `#9ca3af` connectors through
one shared marker, and `.th`/`.ts` text classes on the system font stack.

Layout, and why it is shaped this way: 21 tables have something pointing at
them, but 11 of those have exactly ONE child. Giving each of them a full
org-chart cluster produced a 4,800px column of mostly whitespace. So:

  * hubs with >= 2 children get a cluster — hub box, a spine down the centre,
    one rail per child row, a short drop to each child;
  * hubs with one child collapse into a compact `child -> parent` strip;
  * `public` comes first, because that is the schema you own.

Deterministic: no randomness, integer grid, sorted inputs. The output diffs
cleanly when the schema changes and not otherwise.
"""

from __future__ import annotations

from collections import defaultdict
from html import escape

BOX_W = 158
BOX_H = 36
GAP_X = 12
GAP_Y = 42
PER_ROW = 6
HUB_W = 232
HUB_H = 46
RAIL_DROP = 20
CLUSTER_GAP = 40
PAD_X = 28
PAD_TOP = 26

PAIR_W = 320
PAIR_H = 30
PAIR_COLS = 3
PAIR_GAP = 10

WIDTH = PAD_X * 2 + PER_ROW * BOX_W + (PER_ROW - 1) * GAP_X


def _trunc(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _box(x: int, y: int, w: int, h: int, cls: str, title: str, sub: str = "") -> str:
    cx = x + w // 2
    out = [f'<g class="{cls}"><rect x="{x}" y="{y}" width="{w}" height="{h}" rx="8"/>']
    if sub:
        out.append(
            f'<text class="th" x="{cx}" y="{y + 16}" text-anchor="middle">{escape(title)}</text>'
            f'<text class="ts" x="{cx}" y="{y + 30}" text-anchor="middle">{escape(sub)}</text>'
        )
    else:
        out.append(
            f'<text class="th" x="{cx}" y="{y + h // 2 + 5}" '
            f'text-anchor="middle">{escape(title)}</text>'
        )
    out.append("</g>")
    return "".join(out)


def _heading(y: int, text: str) -> str:
    return (
        f'<text class="hd" x="{PAD_X}" y="{y}">{escape(text)}</text>'
        f'<line class="rule" x1="{PAD_X}" y1="{y + 7}" x2="{WIDTH - PAD_X}" y2="{y + 7}"/>'
    )


def _edge_label(e: dict) -> str:
    detail = e["col"]
    if e["kind"] == "fk" and e["onDelete"] and e["onDelete"] != "NO ACTION":
        detail += " · " + e["onDelete"].lower()
    return detail


def build_svg(tables: dict, hard: list[dict], soft: list[dict]) -> str:
    inbound: dict[str, list[dict]] = defaultdict(list)
    for e in hard + soft:
        if e["from"] in tables and e["to"] in tables:
            inbound[e["to"]].append(e)

    def rank(q: str) -> tuple:
        # public first, then by fan-in descending, then name.
        return (tables[q]["schema"] != "public", -len(inbound[q]), q)

    clusters = sorted((q for q in inbound if len(inbound[q]) >= 2), key=rank)
    pairs = sorted((q for q in inbound if len(inbound[q]) == 1), key=rank)

    body: list[str] = []
    y = PAD_TOP
    section = None

    for hub_q in clusters:
        hub_schema = "public" if tables[hub_q]["schema"] == "public" else "managed"
        if hub_schema != section:
            section = hub_schema
            y += 6
            body.append(_heading(y, "public — the schema you own" if section == "public"
                                 else "Supabase-managed schemas"))
            y += 24

        edges = sorted(inbound[hub_q], key=lambda e: (e["kind"] != "fk", e["from"]))
        n_fk = sum(1 for e in edges if e["kind"] == "fk")
        rows = [edges[i : i + PER_ROW] for i in range(0, len(edges), PER_ROW)]

        sub = f"{n_fk} enforced"
        if len(edges) - n_fk:
            sub += f" · {len(edges) - n_fk} unenforced"
        body.append(_box((WIDTH - HUB_W) // 2, y, HUB_W, HUB_H, "c-purple", hub_q, sub))

        spine_x = WIDTH // 2
        spine_top = y + HUB_H
        last_rail = spine_top

        for r, row in enumerate(rows):
            row_y = y + HUB_H + RAIL_DROP + 16 + r * (BOX_H + GAP_Y)
            rail_y = row_y - 16
            row_w = len(row) * BOX_W + (len(row) - 1) * GAP_X
            start_x = (WIDTH - row_w) // 2
            centers = [start_x + i * (BOX_W + GAP_X) + BOX_W // 2 for i in range(len(row))]
            rail_a, rail_b = min(centers + [spine_x]), max(centers + [spine_x])
            body.append(
                f'<line class="arr" x1="{rail_a}" y1="{rail_y}" x2="{rail_b}" y2="{rail_y}"/>'
            )
            for i, e in enumerate(row):
                cx = centers[i]
                dash = ' stroke-dasharray="4 3"' if e["kind"] == "soft" else ""
                body.append(
                    f'<line class="arr"{dash} x1="{cx}" y1="{rail_y}" '
                    f'x2="{cx}" y2="{row_y - 2}" marker-end="url(#arrow)"/>'
                )
                cls = "c-blue" if e["kind"] == "fk" else "c-amber"
                body.append(
                    _box(cx - BOX_W // 2, row_y, BOX_W, BOX_H, cls,
                         _trunc(tables[e["from"]]["name"], 21), _trunc(_edge_label(e), 24))
                )
            last_rail = rail_y

        body.append(
            f'<line class="arr" x1="{spine_x}" y1="{spine_top}" '
            f'x2="{spine_x}" y2="{last_rail}"/>'
        )
        y += HUB_H + RAIL_DROP + 16 + len(rows) * (BOX_H + GAP_Y) + CLUSTER_GAP

    if pairs:
        y += 4
        body.append(_heading(y, "single links — one child each"))
        y += 22
        grid_w = PAIR_COLS * PAIR_W + (PAIR_COLS - 1) * PAIR_GAP
        left = (WIDTH - grid_w) // 2
        for i, hub_q in enumerate(pairs):
            e = inbound[hub_q][0]
            col, row = i % PAIR_COLS, i // PAIR_COLS
            x = left + col * (PAIR_W + PAIR_GAP)
            py = y + row * (PAIR_H + 9)
            child = tables[e["from"]]
            parent = tables[hub_q]
            cls = "c-blue" if e["kind"] == "fk" else "c-amber"
            cn = child["name"] if child["schema"] == "public" else e["from"]
            pn = parent["name"] if parent["schema"] == "public" else hub_q
            dash = ' stroke-dasharray="3 3"' if e["kind"] == "soft" else ""
            body.append(
                f'<g class="{cls}"><rect x="{x}" y="{py}" width="{PAIR_W}" height="{PAIR_H}" '
                f'rx="6"/>'
                f'<text class="ts" x="{x + 10}" y="{py + 19}">{escape(_trunc(cn, 24))}'
                f'<tspan class="dim"> · {escape(_trunc(e["col"], 18))}</tspan></text>'
                f'<text class="ts" x="{x + PAIR_W - 10}" y="{py + 19}" text-anchor="end">'
                f'→ {escape(_trunc(pn, 22))}</text></g>'
                f'<line class="arr"{dash} x1="{x + PAIR_W // 2 - 6}" y1="{py + PAIR_H // 2}" '
                f'x2="{x + PAIR_W // 2 + 6}" y2="{py + PAIR_H // 2}"/>'
            )
        rows_n = (len(pairs) + PAIR_COLS - 1) // PAIR_COLS
        y += rows_n * (PAIR_H + 9) + 12

    legend_y = y + 8
    body.append(
        f'<rect x="{PAD_X}" y="{legend_y}" width="12" height="12" rx="2" class="c-purple"/>'
        f'<text class="ts" x="{PAD_X + 18}" y="{legend_y + 10}">parent</text>'
        f'<rect x="{PAD_X + 82}" y="{legend_y}" width="12" height="12" rx="2" class="c-blue"/>'
        f'<text class="ts" x="{PAD_X + 100}" y="{legend_y + 10}">FOREIGN KEY — Postgres '
        f'enforces it and cascades the delete</text>'
        f'<rect x="{PAD_X + 452}" y="{legend_y}" width="12" height="12" rx="2" class="c-amber"/>'
        f'<text class="ts" x="{PAD_X + 470}" y="{legend_y + 10}">join by column value, no '
        f'constraint (dashed) — integrity and deletion are the application’s job</text>'
    )
    height = legend_y + 34

    style = (
        "text{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,"
        "sans-serif}"
        ".th{font-size:12.5px;font-weight:500;fill:#111827}"
        ".ts{font-size:11px;fill:#6b7280}"
        ".dim{fill:#9ca3af}"
        ".hd{font-size:10.5px;font-weight:700;letter-spacing:1.2px;fill:#8592a6;"
        "text-transform:uppercase}"
        ".rule{stroke:#e6e9ef;stroke-width:1}"
        ".arr{stroke:#9ca3af;stroke-width:1.5;fill:none}"
        ".c-blue>rect,rect.c-blue{fill:#eff6ff;stroke:#2563eb}.c-blue>.th{fill:#1e40af}"
        ".c-blue>.ts{fill:#2563eb}"
        ".c-purple>rect,rect.c-purple{fill:#faf5ff;stroke:#9333ea}.c-purple>.th{fill:#6b21a8}"
        ".c-purple>.ts{fill:#9333ea}"
        ".c-amber>rect,rect.c-amber{fill:#fffbeb;stroke:#d97706}.c-amber>.th{fill:#92400e}"
        ".c-amber>.ts{fill:#b45309}"
    )
    marker = (
        '<defs><marker id="arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" '
        'markerHeight="6" orient="auto-start-reverse">'
        '<path d="M2 1L8 5L2 9" fill="none" stroke="#9ca3af" stroke-width="1.5" '
        'stroke-linecap="round" stroke-linejoin="round"/></marker></defs>'
    )
    return (
        f'<svg width="{WIDTH}" height="{height}" viewBox="0 0 {WIDTH} {height}" role="img" '
        f'xmlns="http://www.w3.org/2000/svg">'
        f"<title>Caydex database — relationship map</title>"
        f"<desc>Every table other tables point at, with its children. Solid blue children are "
        f"enforced FOREIGN KEY references; dashed amber children join by column value with no "
        f"constraint behind them.</desc>"
        f"<style>{style}</style>{marker}{''.join(body)}</svg>"
    )
