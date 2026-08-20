"""
schema_doc_svg.py — the relationship map, drawn as a hand-authored-looking SVG.

Matches the house style of the sibling diagrams in documents/System Design/
(caydex-report-architecture.svg, caydex-100-users-dataflow.svg): `rx=8` boxes,
1px category strokes over a very light tint, 1.5px `#9ca3af` arrows through one
shared marker, and `.th`/`.ts` text classes on the system font stack.

Layout is a deterministic org chart per hub — hub box on top, a spine down the
centre, one rail per child row, a short drop from the rail to each child. No
randomness, so the output diffs cleanly.
"""

from __future__ import annotations

from collections import defaultdict
from html import escape

BOX_W = 158
BOX_H = 36
GAP_X = 12
GAP_Y = 44
PER_ROW = 6
HUB_W = 218
HUB_H = 46
RAIL_DROP = 20
CLUSTER_GAP = 46
PAD_X = 28
PAD_TOP = 26

WIDTH = PAD_X * 2 + PER_ROW * BOX_W + (PER_ROW - 1) * GAP_X  # 1042


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


def build_svg(tables: dict, hard: list[dict], soft: list[dict]) -> str:
    """One cluster per table that has inbound edges, biggest hub first."""
    inbound: dict[str, list[dict]] = defaultdict(list)
    for e in hard + soft:
        # Only draw edges between tables the page knows about.
        if e["from"] in tables and e["to"] in tables:
            inbound[e["to"]].append(e)

    hubs = sorted(
        inbound.items(),
        key=lambda kv: (-len(kv[1]), kv[0]),
    )
    # A hub with a single child that is itself a hub reads better folded in; keep
    # it simple instead and just show every hub with >= 1 child.
    body: list[str] = []
    y = PAD_TOP

    for hub_q, edges in hubs:
        hub = tables[hub_q]
        edges = sorted(edges, key=lambda e: (e["kind"] != "fk", e["from"]))
        n_fk = sum(1 for e in edges if e["kind"] == "fk")
        rows = [edges[i : i + PER_ROW] for i in range(0, len(edges), PER_ROW)]

        hub_x = (WIDTH - HUB_W) // 2
        sub = f"{n_fk} enforced"
        if len(edges) - n_fk:
            sub += f" · {len(edges) - n_fk} unenforced"
        body.append(_box(hub_x, y, HUB_W, HUB_H, "c-purple", hub_q, sub))

        spine_x = WIDTH // 2
        spine_top = y + HUB_H
        last_rail = spine_top

        for r, row in enumerate(rows):
            row_y = y + HUB_H + RAIL_DROP + 16 + r * (BOX_H + GAP_Y)
            rail_y = row_y - 16
            row_w = len(row) * BOX_W + (len(row) - 1) * GAP_X
            start_x = (WIDTH - row_w) // 2
            centers = [start_x + i * (BOX_W + GAP_X) + BOX_W // 2 for i in range(len(row))]
            rail_a = min(centers + [spine_x])
            rail_b = max(centers + [spine_x])
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
                child = tables[e["from"]]
                cls = "c-blue" if e["kind"] == "fk" else "c-amber"
                label = _trunc(child["name"], 21)
                detail = e["col"]
                if e["kind"] == "fk" and e["onDelete"] and e["onDelete"] != "NO ACTION":
                    detail += f" · {e['onDelete'].lower()}"
                body.append(
                    _box(cx - BOX_W // 2, row_y, BOX_W, BOX_H, cls, label,
                         _trunc(detail, 24))
                )
            last_rail = rail_y

        body.append(
            f'<line class="arr" x1="{spine_x}" y1="{spine_top}" '
            f'x2="{spine_x}" y2="{last_rail}"/>'
        )
        y = y + HUB_H + RAIL_DROP + 16 + len(rows) * (BOX_H + GAP_Y) + CLUSTER_GAP

    legend_y = y - CLUSTER_GAP + 14
    body.append(
        f'<rect x="{PAD_X}" y="{legend_y}" width="12" height="12" rx="2" class="c-purple"/>'
        f'<text class="ts" x="{PAD_X + 18}" y="{legend_y + 10}">parent</text>'
        f'<rect x="{PAD_X + 84}" y="{legend_y}" width="12" height="12" rx="2" class="c-blue"/>'
        f'<text class="ts" x="{PAD_X + 102}" y="{legend_y + 10}">FOREIGN KEY — enforced, '
        f'cascades</text>'
        f'<rect x="{PAD_X + 336}" y="{legend_y}" width="12" height="12" rx="2" class="c-amber"/>'
        f'<text class="ts" x="{PAD_X + 354}" y="{legend_y + 10}">logical join — no constraint '
        f'(dashed): deletion and integrity are the application’s job</text>'
    )
    height = legend_y + 34

    style = (
        "text{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,"
        "sans-serif}"
        ".t{font-size:13px;fill:#111827}"
        ".th{font-size:12.5px;font-weight:500;fill:#111827}"
        ".ts{font-size:11px;fill:#6b7280}"
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
        f'<svg width="100%" viewBox="0 0 {WIDTH} {height}" role="img" '
        f'xmlns="http://www.w3.org/2000/svg">'
        f"<title>Caydex database — relationship map</title>"
        f"<desc>Every table that other tables point at, with its children. Solid blue children "
        f"are enforced FOREIGN KEY references; dashed amber children join by column value with "
        f"no constraint behind them.</desc>"
        f"<style>{style}</style>{marker}{''.join(body)}</svg>"
    )
