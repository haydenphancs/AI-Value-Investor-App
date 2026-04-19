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
