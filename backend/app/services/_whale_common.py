"""
Shared helpers for whale-trade amount parsing.

Ensures the Whales Bought/Sold alert feed (reads Supabase ``whale_trades``),
the per-whale profile view (reads Supabase ``whale_trades``), and the
Ticker Holders tab (computes live from FMP) all agree on dollar amounts
for the same underlying congressional disclosure or 13F filing.

Without these helpers, each call-site implemented its own range parser
and trade-dollar formula, giving different answers for the same trade.
"""

from typing import Optional, Tuple


# ── Snapshot persistence guard ──────────────────────────────────────

# Keys that live on the in-memory snapshot dict but are NOT columns on the
# whale_filing_snapshots table. `trade_groups` (the full per-filing timeline) is
# synced to the whale_trade_groups TABLE instead; sending it as a column makes
# PostgREST reject the entire upsert (PGRST204) and silently kills the snapshot
# cache tier for congress AND 13F whales.
_SNAPSHOT_NON_COLUMNS = ("trade_groups",)


def snapshot_db_row(snapshot: dict) -> dict:
    """Return a copy of ``snapshot`` safe to upsert into whale_filing_snapshots.

    Strips in-memory-only keys (see ``_SNAPSHOT_NON_COLUMNS``). Keeps the full
    dict callers pass around for downstream syncing / rendering intact.
    """
    return {k: v for k, v in snapshot.items() if k not in _SNAPSHOT_NON_COLUMNS}


# ── Congressional (range-based) ─────────────────────────────────────


def parse_congress_amount_dollars(amount_str: str) -> float:
    """Parse FMP's congressional amount range → midpoint in DOLLARS.

    Politicians report trades in ranges (by law). FMP returns strings like
    ``"$1,001 - $15,000"``. We convert to the range midpoint.

    Handles:
      - Ranges:  ``"$1,001 - $15,000"``    → ``8_000.5``
      - Over-X:  ``"Over 50,000,000"``      → ``75_000_000.0`` (1.5× base)
      - Single:  ``"100000"``               → ``100_000.0``
      - Empty / unparseable                  → ``0.0``
    """
    if not amount_str:
        return 0.0

    clean = amount_str.replace("$", "").replace(",", "").strip()

    if " - " in clean:
        parts = clean.split(" - ")
        try:
            low = float(parts[0].strip())
            high = float(parts[1].strip())
            return (low + high) / 2
        except (ValueError, IndexError):
            pass

    if clean.lower().startswith("over "):
        try:
            base = float(clean[5:].strip())
            return base * 1.5
        except ValueError:
            pass

    try:
        return float(clean)
    except ValueError:
        return 0.0


def parse_congress_amount_bounds(
    amount_str: str,
) -> Tuple[float, Optional[float]]:
    """Parse FMP's congressional amount range → ``(low, high)`` DOLLAR bounds.

    Politicians disclose trades ONLY as ranges (by law) — never an exact
    figure. Returns the honest bounds so the UI can show a range instead of
    the fabricated-precision midpoint that :func:`parse_congress_amount_dollars`
    produces (that midpoint is still used internally for sorting / net math).

      - Range:   ``"$1,001 - $15,000"``   → ``(1001.0, 15000.0)``
      - Over-X:  ``"Over $50,000,000"``    → ``(50_000_000.0, None)`` (open high)
      - Single:  ``"100000"``              → ``(100_000.0, 100_000.0)``
      - Empty / unparseable                 → ``(0.0, 0.0)``
    """
    if not amount_str:
        return (0.0, 0.0)

    clean = amount_str.replace("$", "").replace(",", "").strip()

    if " - " in clean:
        parts = clean.split(" - ")
        try:
            low = float(parts[0].strip())
            high = float(parts[1].strip())
            return (low, high)
        except (ValueError, IndexError):
            pass

    if clean.lower().startswith("over "):
        try:
            base = float(clean[5:].strip())
            return (base, None)  # open-ended top bucket
        except ValueError:
            pass

    try:
        v = float(clean)
        return (v, v)
    except ValueError:
        return (0.0, 0.0)


def sum_amount_bounds(
    bounds: list,
) -> Tuple[float, Optional[float]]:
    """Sum a list of ``(low, high)`` bounds into a single summed range.

    If ANY high is ``None`` (open-ended "Over $X" bucket), the summed high is
    ``None`` too — the total is open-ended.
    """
    total_low = 0.0
    total_high: Optional[float] = 0.0
    for low, high in bounds:
        total_low += low
        if total_high is not None:
            total_high = None if high is None else total_high + high
    return (total_low, total_high)


def format_amount_short(value: float) -> str:
    """Compact dollar label with no sign: ``$8K`` / ``$1.5M`` / ``$2.34B``.

    Rolls up to the next unit when rounding would render a four-digit mantissa
    in the lower unit (999_600 → ``$1.0M``, not ``$1000K``)."""
    amt = abs(value)
    if amt >= 1_000_000_000 or round(amt / 1_000_000, 1) >= 1000:
        return f"${amt / 1_000_000_000:.2f}B"
    if amt >= 1_000_000 or round(amt / 1_000, 0) >= 1000:
        return f"${amt / 1_000_000:.1f}M"
    if amt >= 1_000:
        return f"${amt / 1_000:.0f}K"
    return f"${amt:.0f}"


def format_amount_range(low: float, high: Optional[float]) -> str:
    """Format a summed congressional dollar RANGE for display.

      - Open-ended high (``None``)  → ``"$50M+"``
      - Collapsed (``low == high``) → ``"$8K"``
      - Otherwise                   → ``"$50K – $250K"``
    """
    if high is None:
        return f"{format_amount_short(low)}+"
    if abs(high - low) < 1.0:
        return format_amount_short(low)
    return f"{format_amount_short(low)} – {format_amount_short(high)}"


# ── 13F Institutional (shares × implied price) ─────────────────────


def calc_13f_trade_dollars(
    curr_shares: float,
    curr_value: float,
    prev_shares: float,
    prev_value: float,
    min_amount: float = 1_000.0,
) -> Tuple[Optional[str], float]:
    """Compute institutional trade action + dollar size between two quarters.

    Uses ``shares_change × implied_price`` to strip out stock-price
    appreciation — otherwise a holder who sold shares during a rally could
    appear to have "bought" because their position's dollar value grew.

    Same formula as ``_build_institutional_activities`` in holders_service,
    so alert amounts match what the Ticker Holders tab shows.

    Returns ``(action, amount)``:
      - ``action``: ``"BOUGHT"`` | ``"SOLD"`` | ``None`` (below threshold)
      - ``amount``: absolute dollar value (always positive when non-None)
    """
    shares_change = curr_shares - prev_shares

    # Prefer the current quarter's implied price; fall back to prev
    # (useful for "Closed" positions where curr is zero/empty).
    implied_price = 0.0
    if curr_shares > 0 and curr_value > 0:
        implied_price = curr_value / curr_shares
    elif prev_shares > 0 and prev_value > 0:
        implied_price = prev_value / prev_shares

    if implied_price <= 0:
        return (None, 0.0)

    amount = abs(shares_change) * implied_price

    if amount < min_amount:
        return (None, 0.0)

    action = "BOUGHT" if shares_change > 0 else "SOLD"
    return (action, amount)
