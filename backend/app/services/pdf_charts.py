"""
Server-side SVG chart helpers for the detailed-analysis PDF.

Every function is a PURE `data -> str` transform that returns an inline ``<svg>``
fragment (no JS, no external refs) so WeasyPrint can rasterize it natively. Each
returns ``""`` on empty/degenerate input so the PDF template can render a graceful
"no data" fallback instead of crashing.

Design tokens are passed in (accent colour, size) so the same helpers can be
themed per persona later. Colours default to the Caydex print palette.
"""

from __future__ import annotations

import math
from typing import Any, Iterable

# ── Print palette ─────────────────────────────────────────────────────────────
ACCENT = "#1E40AF"          # deep blue — primary brand accent
INK = "#0F172A"             # slate-900 — body text
MUTED = "#64748B"           # slate-500 — captions
GRID = "#E2E8F0"            # slate-200 — hairlines / gridlines
TRACK = "#EEF2F6"           # gauge / bar track

# Score bands (0-100): blue = strong, amber = mixed, red = weak
_GOOD = "#2563EB"
_AMBER = "#F59E0B"
_RED = "#DC2626"


def band_color(score: float) -> str:
    """Red / amber / green by score band — shared by gauge and vitals bars."""
    try:
        s = float(score)
    except (TypeError, ValueError):
        return MUTED
    if s >= 70:
        return _GOOD
    if s >= 45:
        return _AMBER
    return _RED


def _esc(text: Any) -> str:
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _nums(values: Iterable[Any]) -> list[float]:
    out: list[float] = []
    for v in values or []:
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            out.append(float(v))
    return out


# ── Headline score gauge (donut) ──────────────────────────────────────────────
def score_gauge(value: float, size: int = 156) -> str:
    """A donut gauge: light track + accent arc proportional to ``value`` (0-100),
    with the number centred. Arc colour follows the score band."""
    try:
        v = max(0.0, min(100.0, float(value)))
    except (TypeError, ValueError):
        return ""
    cx = cy = size / 2
    stroke = 13
    r = size / 2 - stroke
    circ = 2 * math.pi * r
    dash = circ * v / 100.0
    color = band_color(v)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" '
        f'viewBox="0 0 {size} {size}">'
        f'<circle cx="{cx}" cy="{cy}" r="{r:.1f}" fill="none" stroke="{TRACK}" '
        f'stroke-width="{stroke}"/>'
        f'<circle cx="{cx}" cy="{cy}" r="{r:.1f}" fill="none" stroke="{color}" '
        f'stroke-width="{stroke}" stroke-linecap="round" '
        f'stroke-dasharray="{dash:.1f} {circ:.1f}" '
        f'transform="rotate(-90 {cx} {cy})"/>'
        f'<text x="{cx}" y="{cy - 2}" text-anchor="middle" dominant-baseline="central" '
        f'font-size="{size*0.30:.0f}" font-weight="800" fill="{INK}" '
        f'font-family="Helvetica, Arial, sans-serif">{int(round(v))}</text>'
        f'<text x="{cx}" y="{cy + size*0.20:.0f}" text-anchor="middle" '
        f'font-size="{size*0.085:.0f}" font-weight="600" fill="{MUTED}" '
        f'font-family="Helvetica, Arial, sans-serif" letter-spacing="1">OUT OF 100</text>'
        f"</svg>"
    )


# ── Price sparkline (line + area) ─────────────────────────────────────────────
def price_sparkline(
    prices: Iterable[Any],
    width: int = 700,
    height: int = 150,
    accent: str = ACCENT,
) -> str:
    pts = _nums(prices)
    if len(pts) < 2:
        return ""
    lo, hi = min(pts), max(pts)
    rng = (hi - lo) or 1.0
    pad_t, pad_b, pad_l, pad_r = 10, 16, 4, 4
    w = width - pad_l - pad_r
    h = height - pad_t - pad_b
    n = len(pts)

    def x(i: int) -> float:
        return pad_l + w * i / (n - 1)

    def y(v: float) -> float:
        return pad_t + h * (1 - (v - lo) / rng)

    line = " ".join(
        f"{'M' if i == 0 else 'L'}{x(i):.1f},{y(v):.1f}" for i, v in enumerate(pts)
    )
    area = (
        f"M{x(0):.1f},{pad_t + h:.1f} "
        + " ".join(f"L{x(i):.1f},{y(v):.1f}" for i, v in enumerate(pts))
        + f" L{x(n - 1):.1f},{pad_t + h:.1f} Z"
    )
    # a few horizontal gridlines
    grid = "".join(
        f'<line x1="{pad_l}" y1="{pad_t + h*f:.1f}" x2="{pad_l + w}" '
        f'y2="{pad_t + h*f:.1f}" stroke="{GRID}" stroke-width="1"/>'
        for f in (0.0, 0.5, 1.0)
    )
    gid = "sparkfill"
    up = pts[-1] >= pts[0]
    end_color = _GOOD if up else _RED
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}">'
        f'<defs><linearGradient id="{gid}" x1="0" y1="0" x2="0" y2="1">'
        f'<stop offset="0%" stop-color="{accent}" stop-opacity="0.22"/>'
        f'<stop offset="100%" stop-color="{accent}" stop-opacity="0.02"/>'
        f"</linearGradient></defs>"
        f"{grid}"
        f'<path d="{area}" fill="url(#{gid})"/>'
        f'<path d="{line}" fill="none" stroke="{accent}" stroke-width="2.2" '
        f'stroke-linejoin="round" stroke-linecap="round"/>'
        f'<circle cx="{x(n-1):.1f}" cy="{y(pts[-1]):.1f}" r="3.4" fill="{end_color}"/>'
        f"</svg>"
    )


# ── Analyst consensus stacked bar ─────────────────────────────────────────────
def analyst_consensus_stacked_bar(
    counts: dict, width: int = 330, height: int = 24
) -> str:
    order = [
        ("strong_buy", "#1E3A8A"),
        ("buy", "#60A5FA"),
        ("hold", "#F59E0B"),
        ("sell", "#F87171"),
        ("strong_sell", "#B91C1C"),
    ]

    def g(k: str) -> int:
        try:
            return max(0, int(counts.get(k, 0) or 0))
        except (TypeError, ValueError):
            return 0

    total = sum(g(k) for k, _ in order)
    if total <= 0:
        return ""
    clip = "consclip"
    segs = ""
    x = 0.0
    for k, c in order:
        v = g(k)
        if v <= 0:
            continue
        sw = width * v / total
        segs += f'<rect x="{x:.1f}" y="0" width="{sw:.2f}" height="{height}" fill="{c}"/>'
        x += sw
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}">'
        f'<defs><clipPath id="{clip}">'
        f'<rect x="0" y="0" width="{width}" height="{height}" rx="6" ry="6"/>'
        f"</clipPath></defs>"
        f'<g clip-path="url(#{clip})">{segs}</g>'
        f"</svg>"
    )


# ── Moat radar ────────────────────────────────────────────────────────────────
def moat_radar(
    dimensions: list[dict],
    size: int = 300,
    max_score: float = 100.0,
    accent: str = ACCENT,
    peer_color: str = "#94A3B8",
) -> str:
    dims = [d for d in (dimensions or []) if isinstance(d, dict) and d.get("name")]
    n = len(dims)
    if n < 3:
        return ""
    cx = cy = size / 2
    R = size / 2 - 52  # leave room for labels
    angles = [-math.pi / 2 + i * 2 * math.pi / n for i in range(n)]

    def at(angle: float, rad: float) -> tuple[float, float]:
        return cx + rad * math.cos(angle), cy + rad * math.sin(angle)

    rings = ""
    for frac in (0.25, 0.5, 0.75, 1.0):
        poly = " ".join(
            f"{cx + R*frac*math.cos(a):.1f},{cy + R*frac*math.sin(a):.1f}"
            for a in angles
        )
        rings += f'<polygon points="{poly}" fill="none" stroke="{GRID}" stroke-width="1"/>'

    axes = ""
    for a in angles:
        ax, ay = at(a, R)
        axes += (
            f'<line x1="{cx}" y1="{cy}" x2="{ax:.1f}" y2="{ay:.1f}" '
            f'stroke="{GRID}" stroke-width="1"/>'
        )

    def poly_for(key: str) -> str:
        out = []
        for d, a in zip(dims, angles):
            try:
                v = max(0.0, min(max_score, float(d.get(key, 0) or 0))) / max_score
            except (TypeError, ValueError):
                v = 0.0
            px, py = at(a, R * v)
            out.append(f"{px:.1f},{py:.1f}")
        return " ".join(out)

    has_peer = any(d.get("peer_score") for d in dims)
    peer_poly = (
        f'<polygon points="{poly_for("peer_score")}" fill="none" '
        f'stroke="{peer_color}" stroke-width="1.5" stroke-dasharray="4 3"/>'
        if has_peer
        else ""
    )
    comp_poly = (
        f'<polygon points="{poly_for("score")}" fill="{accent}" fill-opacity="0.18" '
        f'stroke="{accent}" stroke-width="2"/>'
    )

    dots = ""
    labels = ""
    for d, a in zip(dims, angles):
        try:
            v = max(0.0, min(max_score, float(d.get("score", 0) or 0))) / max_score
        except (TypeError, ValueError):
            v = 0.0
        dx, dy = at(a, R * v)
        dots += f'<circle cx="{dx:.1f}" cy="{dy:.1f}" r="2.6" fill="{accent}"/>'
        lx, ly = at(a, R + 16)
        cos = math.cos(a)
        anchor = "middle" if abs(cos) <= 0.3 else ("start" if cos > 0 else "end")
        labels += (
            f'<text x="{lx:.1f}" y="{ly:.1f}" text-anchor="{anchor}" '
            f'dominant-baseline="central" font-size="9.5" font-weight="600" '
            f'fill="{MUTED}" font-family="Helvetica, Arial, sans-serif">'
            f"{_esc(d['name'])}</text>"
        )

    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" '
        f'viewBox="0 0 {size} {size}">'
        f"{rings}{axes}{peer_poly}{comp_poly}{dots}{labels}"
        f"</svg>"
    )


# ── Earnings timeline (actuals → forecast bars) ───────────────────────────────
def bars_actuals_forecast(
    items: list[dict],
    width: int = 700,
    height: int = 150,
    accent: str = ACCENT,
    forecast_color: str = "#93C5FD",
) -> str:
    """Vertical bars; actuals solid accent, forecast lighter. Each item:
    {label, value, is_forecast, value_label?}."""
    rows = []
    for it in items or []:
        if not isinstance(it, dict):
            continue
        v = it.get("value")
        if not isinstance(v, (int, float)) or isinstance(v, bool):
            continue
        rows.append(
            (str(it.get("label", "")), float(v), bool(it.get("is_forecast")),
             str(it.get("value_label") or ""))
        )
    if len(rows) < 2:
        return ""
    # Sign-aware domain anchored on zero so a loss year (negative EPS/revenue)
    # draws a real downward bar instead of a broken negative-height rect, and an
    # all-negative series still charts (mirrors the iOS GrowthChartView fix).
    vals = [v for _, v, _, _ in rows]
    lo = min(min(vals), 0.0)
    hi = max(max(vals), 0.0)
    if hi == lo:
        hi = lo + 1.0
    rng = hi - lo
    pad_t, pad_b, pad_l, pad_r = 18, 20, 6, 6
    w = width - pad_l - pad_r
    h = height - pad_t - pad_b
    n = len(rows)
    slot = w / n
    bw = min(slot * 0.62, 46)

    def _y_of(val: float) -> float:
        return pad_t + h * (1 - (val - lo) / rng)

    base_y = _y_of(0.0)
    out = ""
    for i, (lbl, v, fc, vlabel) in enumerate(rows):
        cx = pad_l + slot * (i + 0.5)
        yv = _y_of(v)
        # Bar spans between the value and the zero baseline (downward for losses).
        bar_top = min(yv, base_y)
        bar_h = max(abs(yv - base_y), 0.6)
        color = (forecast_color if fc else accent) if v >= 0 else _RED
        out += (
            f'<rect x="{cx - bw/2:.1f}" y="{bar_top:.1f}" width="{bw:.1f}" height="{bar_h:.1f}" '
            f'rx="2" fill="{color}"/>'
        )
        if vlabel:
            out += (
                f'<text x="{cx:.1f}" y="{bar_top - 4:.1f}" text-anchor="middle" font-size="8" '
                f'font-weight="700" fill="{INK}" '
                f'font-family="Helvetica, Arial, sans-serif">{_esc(vlabel)}</text>'
            )
        out += (
            f'<text x="{cx:.1f}" y="{height - 6:.1f}" text-anchor="middle" font-size="8" '
            f'fill="{MUTED}" font-family="Helvetica, Arial, sans-serif">{_esc(lbl)}</text>'
        )
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}">{out}</svg>'
    )


# ── Growth: sign-aware bars + YoY% line + dashed sector line ───────────────────
def growth_bars_line(
    items: list[dict],
    *,
    width: int = 700,
    height: int = 184,
    bar_color: str = ACCENT,
    yoy_color: str = "#F59E0B",
    sector_color: str = MUTED,
    value_fmt: str = "money",
) -> str:
    """Combined growth chart for the PDF — mirrors the app's Growth card.

    Absolute-value BARS (sign-aware: a loss period draws RED and downward from a
    visible zero baseline, and an all-negative series still charts) plus a YoY%
    overlay line and a dashed sector-average line — both BROKEN at periods whose
    value is "not meaningful" (None) instead of bridging a fabricated point. The
    % lines are normalized into a band (no second axis) like the app overlay.
    ``items``: ``{label, value, yoy, sector}`` (value finite float; yoy/sector
    Optional). Returns "" when there are < 2 charted periods."""
    def _opt(x: Any) -> "float | None":
        if isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(float(x)):
            return float(x)
        return None

    rows: list[tuple[str, float, "float | None", "float | None"]] = []
    for it in items or []:
        if not isinstance(it, dict):
            continue
        v = it.get("value")
        if not isinstance(v, (int, float)) or isinstance(v, bool) or not math.isfinite(float(v)):
            continue
        rows.append(
            (str(it.get("label", "")), float(v), _opt(it.get("yoy")), _opt(it.get("sector")))
        )
    if len(rows) < 2:
        return ""

    vals = [v for _, v, _, _ in rows]
    lo = min(min(vals), 0.0)
    hi = max(max(vals), 0.0)
    if hi == lo:
        hi = lo + 1.0
    if hi > 0:
        hi *= 1.12
    if lo < 0:
        lo *= 1.12
    rng = hi - lo

    pad_t, pad_b, pad_l, pad_r = 16, 26, 42, 8
    w = width - pad_l - pad_r
    h = height - pad_t - pad_b
    n = len(rows)
    slot = w / n
    bw = min(slot * 0.5, 30)

    def cx(i: int) -> float:
        return pad_l + slot * (i + 0.5)

    def vy(val: float) -> float:
        return pad_t + h * (1 - (val - lo) / rng)

    base_y = vy(0.0)
    parts: list[str] = []

    # Value gridlines + left-axis ticks (top / zero / bottom).
    for tv in sorted({hi, 0.0, lo}, reverse=True):
        gy = vy(tv)
        parts.append(
            f'<line x1="{pad_l}" y1="{gy:.1f}" x2="{pad_l + w}" y2="{gy:.1f}" '
            f'stroke="{GRID}" stroke-width="1"/>'
        )
        parts.append(
            f'<text x="{pad_l - 4}" y="{gy + 3:.1f}" text-anchor="end" font-size="7" '
            f'fill="{MUTED}" font-family="Helvetica, Arial, sans-serif">'
            f'{_esc(_axis_label(tv, value_fmt))}</text>'
        )

    # Bars (sign-aware: red & downward for losses).
    for i, (_, v, _, _) in enumerate(rows):
        yv = vy(v)
        bar_top = min(yv, base_y)
        bar_h = max(abs(yv - base_y), 0.6)
        col = bar_color if v >= 0 else _RED
        parts.append(
            f'<rect x="{cx(i) - bw / 2:.1f}" y="{bar_top:.1f}" width="{bw:.1f}" '
            f'height="{bar_h:.1f}" rx="2" fill="{col}" opacity="0.85"/>'
        )

    # % overlay lines, normalized into a band (no second axis — like the app).
    pcts = [p for _, _, y, s in rows for p in (y, s) if p is not None]
    if pcts:
        plo, phi = min(pcts), max(pcts)
        if phi == plo:
            phi = plo + 1.0
        band_top = pad_t + h * 0.12
        band_bot = pad_t + h * 0.88

        def py(p: float) -> float:
            return band_bot - (p - plo) / (phi - plo) * (band_bot - band_top)

        def overlay(idx: int, color: str, dashed: bool, label_pts: bool) -> None:
            run: list[tuple[float, float]] = []

            def flush() -> None:
                if len(run) >= 2:
                    d = " ".join(
                        f"{'M' if k == 0 else 'L'}{x:.1f},{yv:.1f}"
                        for k, (x, yv) in enumerate(run)
                    )
                    dash = ' stroke-dasharray="5,3"' if dashed else ""
                    parts.append(
                        f'<path d="{d}" fill="none" stroke="{color}" stroke-width="1.8" '
                        f'stroke-linejoin="round" stroke-linecap="round"{dash}/>'
                    )
                run.clear()

            for i, row in enumerate(rows):
                p = row[idx]
                if p is None:
                    flush()
                    continue
                x, yv = cx(i), py(p)
                run.append((x, yv))
                parts.append(f'<circle cx="{x:.1f}" cy="{yv:.1f}" r="2" fill="{color}"/>')
                if label_pts:
                    parts.append(
                        f'<text x="{x:.1f}" y="{yv - 4:.1f}" text-anchor="middle" font-size="6.5" '
                        f'font-weight="700" fill="{color}" '
                        f'font-family="Helvetica, Arial, sans-serif">{p:+.0f}%</text>'
                    )
            flush()

        overlay(3, sector_color, True, False)   # sector dashed line (behind)
        overlay(2, yoy_color, False, True)       # YoY solid line + labels (front)

    # X-axis labels (thinned).
    label_step = max(1, math.ceil(n / 8))
    for i, (lbl, _, _, _) in enumerate(rows):
        if lbl and (i % label_step == 0 or i == n - 1):
            parts.append(
                f'<text x="{cx(i):.1f}" y="{height - 6:.1f}" text-anchor="middle" '
                f'font-size="7" fill="{MUTED}" '
                f'font-family="Helvetica, Arial, sans-serif">{_esc(lbl)}</text>'
            )

    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}">{"".join(parts)}</svg>'
    )


# ── Diverging buy/sell flow bars (insider + institutional) ────────────────────
def diverging_bars(
    items: list[dict],
    width: int = 330,
    height: int = 130,
    up_color: str = ACCENT,
    down_color: str = _RED,
) -> str:
    """items: {label, up, down}. `up` plotted above the zero line, `down` below.
    Used for insider buy/sell flow and institutional net flow."""
    rows = []
    for it in items or []:
        if not isinstance(it, dict):
            continue
        rows.append((
            str(it.get("label", "")),
            abs(float(it.get("up", 0) or 0)),
            abs(float(it.get("down", 0) or 0)),
        ))
    if not rows:
        return ""
    mx = max([u for _, u, _ in rows] + [d for _, _, d in rows] + [1.0])
    pad_t, pad_b, pad_l, pad_r = 6, 18, 30, 6
    w = width - pad_l - pad_r
    h = height - pad_t - pad_b
    zero = pad_t + h / 2
    n = len(rows)
    slot = w / n
    bw = min(slot * 0.5, 18)
    # A dominant outlier (e.g. one giant insider sale) can shrink every other bar
    # to nothing — floor non-zero bars at a visible sliver. Thin x-axis labels so
    # many monthly buckets don't overlap in the narrow print width.
    min_bar = 2.0
    label_step = max(1, math.ceil(n / 8))
    # Left y-axis: share magnitude at top (buys) and bottom (sells), 0 at centre.
    top_y, bot_y = pad_t, pad_t + h
    out = (
        f'<line x1="{pad_l}" y1="{top_y:.1f}" x2="{pad_l + w}" y2="{top_y:.1f}" '
        f'stroke="{GRID}" stroke-width="1"/>'
        f'<line x1="{pad_l}" y1="{bot_y:.1f}" x2="{pad_l + w}" y2="{bot_y:.1f}" '
        f'stroke="{GRID}" stroke-width="1"/>'
    )
    for yy, txt in ((top_y, _fmt_compact(mx)), (zero, "0"), (bot_y, _fmt_compact(mx))):
        out += (f'<text x="{pad_l - 4}" y="{yy + 3:.1f}" text-anchor="end" font-size="7" '
                f'fill="{MUTED}" font-family="Helvetica, Arial, sans-serif">{_esc(txt)}</text>')
    for i, (lbl, u, d) in enumerate(rows):
        cx = pad_l + slot * (i + 0.5)
        uh = (h / 2) * (u / mx)
        dh = (h / 2) * (d / mx)
        if u > 0:
            uh = max(uh, min_bar)
            out += (f'<rect x="{cx - bw/2:.1f}" y="{zero - uh:.1f}" width="{bw:.1f}" '
                    f'height="{uh:.1f}" rx="1.5" fill="{up_color}"/>')
        if d > 0:
            dh = max(dh, min_bar)
            out += (f'<rect x="{cx - bw/2:.1f}" y="{zero:.1f}" width="{bw:.1f}" '
                    f'height="{dh:.1f}" rx="1.5" fill="{down_color}"/>')
        if lbl and (i % label_step == 0 or i == n - 1):
            out += (f'<text x="{cx:.1f}" y="{height - 5:.1f}" text-anchor="middle" '
                    f'font-size="7" fill="{MUTED}" '
                    f'font-family="Helvetica, Arial, sans-serif">{_esc(lbl)}</text>')
    out += (f'<line x1="{pad_l}" y1="{zero:.1f}" x2="{pad_l + w}" y2="{zero:.1f}" '
            f'stroke="{INK}" stroke-width="1"/>')
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}">{out}</svg>'
    )


# ── Compact trend line (short interest / dilution) ────────────────────────────
def mini_line(
    values: Iterable[Any], width: int = 300, height: int = 76, accent: str = ACCENT
) -> str:
    """Small line+area with no sentiment colouring — end dot uses `accent`."""
    pts = _nums(values)
    if len(pts) < 2:
        return ""
    lo, hi = min(pts), max(pts)
    rng = (hi - lo) or 1.0
    pad = 9
    w = width - 2 * pad
    h = height - 2 * pad
    n = len(pts)

    def x(i: int) -> float:
        return pad + w * i / (n - 1)

    def y(v: float) -> float:
        return pad + h * (1 - (v - lo) / rng)

    line = " ".join(f"{'M' if i == 0 else 'L'}{x(i):.1f},{y(v):.1f}" for i, v in enumerate(pts))
    area = (
        f"M{x(0):.1f},{pad + h:.1f} "
        + " ".join(f"L{x(i):.1f},{y(v):.1f}" for i, v in enumerate(pts))
        + f" L{x(n - 1):.1f},{pad + h:.1f} Z"
    )
    gid = f"ml{abs(hash(accent)) % 9999}"
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}">'
        f'<defs><linearGradient id="{gid}" x1="0" y1="0" x2="0" y2="1">'
        f'<stop offset="0%" stop-color="{accent}" stop-opacity="0.20"/>'
        f'<stop offset="100%" stop-color="{accent}" stop-opacity="0.02"/>'
        f"</linearGradient></defs>"
        f'<path d="{area}" fill="url(#{gid})"/>'
        f'<path d="{line}" fill="none" stroke="{accent}" stroke-width="2" '
        f'stroke-linejoin="round" stroke-linecap="round"/>'
        f'<circle cx="{x(n-1):.1f}" cy="{y(pts[-1]):.1f}" r="3" fill="{accent}"/>'
        f"</svg>"
    )


# ── Axis-label formatting (shared by the axed charts below) ────────────────────
def _fmt_compact(v: float, money: bool = False) -> str:
    """1_400_000_000 -> '$1.4B' / '340M' — compact magnitude label for an axis."""
    sign = "-" if v < 0 else ""
    a = abs(v)
    pre = "$" if money else ""
    if a >= 1e9:
        return f"{sign}{pre}{a / 1e9:.1f}B"
    if a >= 1e6:
        return f"{sign}{pre}{a / 1e6:.0f}M"
    if a >= 1e3:
        return f"{sign}{pre}{a / 1e3:.0f}K"
    if a == 0 or a >= 1:
        return f"{sign}{pre}{a:.0f}"
    return f"{sign}{pre}{a:.1f}"


def _axis_label(v: float, fmt: str) -> str:
    if fmt == "money":
        return _fmt_compact(v, money=True)
    if fmt == "pct":
        return f"{v:.1f}%"
    return _fmt_compact(v, money=False)


# ── Axed grouped bars (1-2 series) — left y-axis + thinned x-axis ──────────────
def axed_bars(
    items: list[dict],
    *,
    colors: list[str],
    width: int = 300,
    height: int = 120,
    fmt: str = "num",
) -> str:
    """Grouped vertical bars with a labelled left y-axis (0 / mid / max ticks +
    gridlines) and thinned x-axis labels. ``items``: ``{label, values:[...]}``;
    one bar per colour. Returns "" when there is no positive data."""
    rows: list[tuple[str, list[float]]] = []
    for it in items or []:
        if not isinstance(it, dict):
            continue
        vals = [
            float(x) if isinstance(x, (int, float)) and not isinstance(x, bool) else 0.0
            for x in (it.get("values") or [])
        ]
        rows.append((str(it.get("label", "")), vals))
    if not rows:
        return ""
    ns = len(colors)
    vmax = max([v for _, vals in rows for v in vals[:ns]] + [0.0])
    if vmax <= 0:
        return ""
    pad_t, pad_b, pad_l, pad_r = 10, 16, 34, 6
    w = width - pad_l - pad_r
    h = height - pad_t - pad_b
    n = len(rows)
    slot = w / n
    group_w = min(slot * 0.7, 26)
    bw = group_w / ns
    out = ""
    for frac in (0.0, 0.5, 1.0):
        gy = pad_t + h * (1 - frac)
        out += (f'<line x1="{pad_l}" y1="{gy:.1f}" x2="{pad_l + w}" y2="{gy:.1f}" '
                f'stroke="{GRID}" stroke-width="1"/>')
        out += (f'<text x="{pad_l - 4}" y="{gy + 3:.1f}" text-anchor="end" font-size="7" '
                f'fill="{MUTED}" font-family="Helvetica, Arial, sans-serif">'
                f'{_esc(_axis_label(vmax * frac, fmt))}</text>')
    label_step = max(1, math.ceil(n / 6))
    for i, (lbl, vals) in enumerate(rows):
        gx = pad_l + slot * (i + 0.5) - group_w / 2
        for j in range(ns):
            v = vals[j] if j < len(vals) else 0.0
            if v <= 0:
                continue
            bh = h * (v / vmax)
            out += (f'<rect x="{gx + bw * j:.1f}" y="{pad_t + h - bh:.1f}" width="{bw:.1f}" '
                    f'height="{bh:.1f}" rx="1" fill="{colors[j]}"/>')
        if lbl and (i % label_step == 0 or i == n - 1):
            cx = pad_l + slot * (i + 0.5)
            out += (f'<text x="{cx:.1f}" y="{height - 4:.1f}" text-anchor="middle" '
                    f'font-size="7" fill="{MUTED}" '
                    f'font-family="Helvetica, Arial, sans-serif">{_esc(lbl)}</text>')
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}">{out}</svg>'
    )


# ── Axed trend line — left y-axis + thinned x-axis ────────────────────────────
def axed_line(
    points: list[dict],
    *,
    accent: str = ACCENT,
    width: int = 300,
    height: int = 110,
    fmt: str = "num",
) -> str:
    """Single trend line with a labelled left y-axis (min / mid / max ticks +
    gridlines) and thinned x-axis labels. ``points``: ``{label, value}``."""
    rows: list[tuple[str, float]] = []
    for p in points or []:
        if not isinstance(p, dict):
            continue
        v = p.get("value")
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            rows.append((str(p.get("label", "")), float(v)))
    if len(rows) < 2:
        return ""
    vals = [v for _, v in rows]
    lo, hi = min(vals), max(vals)
    if hi == lo:
        hi = lo + 1.0
    pad = (hi - lo) * 0.08
    lo, hi = lo - pad, hi + pad
    rng = hi - lo
    pad_t, pad_b, pad_l, pad_r = 10, 16, 36, 6
    w = width - pad_l - pad_r
    h = height - pad_t - pad_b
    n = len(rows)

    def x(i: int) -> float:
        return pad_l + w * i / (n - 1)

    def y(v: float) -> float:
        return pad_t + h * (1 - (v - lo) / rng)

    out = ""
    for frac in (0.0, 0.5, 1.0):
        val = lo + rng * frac
        gy = y(val)
        out += (f'<line x1="{pad_l}" y1="{gy:.1f}" x2="{pad_l + w}" y2="{gy:.1f}" '
                f'stroke="{GRID}" stroke-width="1"/>')
        out += (f'<text x="{pad_l - 4}" y="{gy + 3:.1f}" text-anchor="end" font-size="7" '
                f'fill="{MUTED}" font-family="Helvetica, Arial, sans-serif">'
                f'{_esc(_axis_label(val, fmt))}</text>')
    line = " ".join(
        f"{'M' if i == 0 else 'L'}{x(i):.1f},{y(v):.1f}" for i, (_, v) in enumerate(rows)
    )
    out += (f'<path d="{line}" fill="none" stroke="{accent}" stroke-width="2" '
            f'stroke-linejoin="round" stroke-linecap="round"/>')
    out += f'<circle cx="{x(n - 1):.1f}" cy="{y(rows[-1][1]):.1f}" r="3" fill="{accent}"/>'
    label_step = max(1, math.ceil(n / 6))
    for i, (lbl, _) in enumerate(rows):
        if lbl and (i % label_step == 0 or i == n - 1):
            out += (f'<text x="{x(i):.1f}" y="{height - 4:.1f}" text-anchor="middle" '
                    f'font-size="7" fill="{MUTED}" '
                    f'font-family="Helvetica, Arial, sans-serif">{_esc(lbl)}</text>')
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}">{out}</svg>'
    )
