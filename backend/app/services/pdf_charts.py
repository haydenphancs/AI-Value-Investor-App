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
