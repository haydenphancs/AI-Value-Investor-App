"""
Shared symbol → asset-class detection.

Single source of truth for "what kind of thing is this ticker", extracted from
`chat_service._detect_asset_type` so the intraday-chart paths can answer the one
question that actually matters to them: **does this asset trade outside US equity
regular hours?**

Why this exists as a shared module rather than a per-service heuristic: the
holdings card, the Home Market Pulse tile and the asset DETAIL chart must draw
the same series by construction. `chart_helper._filter_regular_hours` is a pure
time-of-day filter, so a 24/7 series fetched with `extended_hours=False` loses
roughly 70% of its bars and then `_intraday_sparkline` pins the "latest day" to
the last SURVIVING bar — a Bitcoin card ends up drawing yesterday's 09:30–16:00
slice beside a live price. `crypto_service` and `commodity_service` already pass
`extended_hours=True` for their detail charts, so any caller that doesn't
diverges from the chart one tap away.

Deliberately symbol-based, NOT database-based. `watchlist_items.asset_type`
exists but defaults to `'Stock'` and is never written by `POST /api/v1/watchlist`
(the only path the iOS add-ticker flow uses), so every normally-added row claims
to be a stock. A stored value is honoured when it is meaningful; otherwise the
symbol decides.
"""

from typing import Optional

# FMP's USD-suffixed commodity codes. Checked BEFORE the crypto heuristic below:
# the generic `endswith("USD")` test would otherwise swallow GCUSD/CLUSD/... and
# label gold and crude oil as crypto. No symbol collides between the two sets.
# PAUSD (palladium) and ZWUSD (wheat) were MISSING while both are live in
# commodity_service._COMMODITY_PROFILES and commodities._COMMODITY_NEWS_TICKERS — so
# every classifier built on this set fell through to the generic `endswith("USD")`
# crypto rule and called them crypto. That is not cosmetic: it drives the
# extended-hours fetch window and the technical-analysis TTL + weekly bucketing.
# `ZUSD` looks like the typo that lost ZWUSD; it is kept because nothing serves it
# either way, and removing a symbol is a behaviour change for no benefit.
_COMMODITY_SYMBOLS = frozenset({
    "GCUSD", "SIUSD", "CLUSD", "NGUSD", "PLUSD", "PAUSD", "HGUSD",
    "ZSUSD", "ZCUSD", "ZWUSD", "ZUSD", "LBUSD", "OJUSD", "KCUSD",
    "SBUSD", "CTUSD", "CCUSD",
})

# Friendly English names, matched ONLY when a caller opts in. Deliberately NOT
# part of the session-window decision: several are real listed equities —
# "GOLD" is Barrick Mining (NYSE), and this repo's own commodities endpoint
# lists it among the gold-related EQUITIES ("GC": "GLD,IAU,GOLD,NEM,AEM").
# Treating it as a commodity would give an ordinary equity holding a 24/7
# refresh gate and an extended-hours sparkline unlike every other equity row
# beside it. Chat opts in because it only voices the name, never fetches a chart.
_COMMODITY_ALIASES = frozenset({
    "GOLD", "SILVER", "OIL", "NATGAS", "PLATINUM", "COPPER",
})

_BARE_CRYPTO_SYMBOLS = frozenset({
    "BTC", "ETH", "SOL", "ADA", "DOT", "AVAX", "MATIC", "LINK",
    "XRP", "DOGE", "SHIB", "UNI", "AAVE", "LTC", "BCH", "ATOM",
})

# Asset classes whose sessions are NOT the US equity 09:30–16:00 ET window:
# crypto trades 24/7, and the FMP commodity codes are continuously-quoted
# futures (~23h/day). Both must be fetched with extended_hours=True.
_ROUND_THE_CLOCK = frozenset({"crypto", "commodity"})

# Stored `asset_type` values that are specific enough to trust. Anything else
# (None, "", the "Stock" column default, "equity", …) falls through to symbol
# detection, because the default is indistinguishable from a real stock.
_TRUSTED_STORED_CLASSES = frozenset({"crypto", "commodity", "index", "etf"})


def detect_asset_class(
    symbol: Optional[str], *, include_aliases: bool = False
) -> str:
    """Classify a symbol as ``index`` | ``commodity`` | ``crypto`` | ``stock``.

    Lowercase to match the app's wire vocabulary (``MarketTickerType`` on iOS,
    ``_PULSE_SYMBOLS[i]["type"]`` on the backend). An empty/unknown symbol is
    treated as a stock — the conservative default, since it keeps the regular
    -hours filter on rather than silently widening a series.

    ``include_aliases`` opts in to the friendly English commodity names
    (``GOLD``, ``OIL``, …). Off by default because those collide with real
    listed equities; only callers that merely *describe* an asset (chat) should
    turn it on, never a caller choosing a chart's session window.
    """
    if not symbol:
        return "stock"
    sid = str(symbol).strip().upper()
    if not sid:
        return "stock"
    if sid.startswith("^"):
        return "index"
    if sid in _COMMODITY_SYMBOLS or (include_aliases and sid in _COMMODITY_ALIASES):
        return "commodity"
    # The USD/USDT suffix rule needs a base symbol in front of it. "USD" on its
    # own is a real listed ETF (ProShares Ultra Semiconductors); without the
    # length check it would be classified as a coin and fetched with extended
    # hours, disagreeing with its own ETF detail chart.
    if len(sid) > 3 and (sid.endswith("USD") or sid.endswith("USDT")):
        return "crypto"
    if sid in _BARE_CRYPTO_SYMBOLS:
        return "crypto"
    return "stock"


def resolve_asset_class(symbol: Optional[str], stored: Optional[str] = None) -> str:
    """Asset class for a symbol, preferring a *meaningful* stored value.

    ``stored`` is the persisted ``asset_type`` (e.g. ``watchlist_items.asset_type``).
    It is used only when it names a specific non-stock class — the column's
    ``'Stock'`` default is applied to every row inserted by the watchlist endpoint,
    so trusting it blindly would classify Bitcoin as an equity.
    """
    normalized = (stored or "").strip().lower()
    if normalized in _TRUSTED_STORED_CLASSES:
        return normalized
    return detect_asset_class(symbol)


def trades_extended_hours(asset_class: str) -> bool:
    """True when the class trades outside the US equity regular session.

    Feeds ``chart_helper.fetch_chart_data(..., extended_hours=...)``: pass True
    and the intraday series keeps its full session instead of being clipped to
    09:30–16:00 ET.
    """
    return (asset_class or "").strip().lower() in _ROUND_THE_CLOCK


def symbol_trades_extended_hours(
    symbol: Optional[str], stored: Optional[str] = None
) -> bool:
    """Convenience: resolve the class for *symbol* and answer the 24/7 question."""
    return trades_extended_hours(resolve_asset_class(symbol, stored))


def uses_coingecko_price(symbol: Optional[str]) -> bool:
    """True when CoinGecko is the correct PRICE source for *symbol*.

    ⚠️ Crypto CLASSIFICATION alone is not this question, and using it alone shipped a
    real production bug. `_BARE_CRYPTO_SYMBOLS` deliberately calls a bare ``BTC`` /
    ``ETH`` / ``LTC`` "crypto" so its CHART gets a 24/7 session window — but on FMP those
    same tickers are listed securities we are fully licensed for:

        BTC  → Grayscale Bitcoin Mini Trust ETF   (AMEX)   real $34.68
        ETH  → Grayscale Ethereum Mini Trust ETF  (AMEX)   real $23.68
        XRP  → Bitwise XRP ETF                    (AMEX)   real $15.92
        ATOM → Atomera Incorporated               (NASDAQ) real $4.14
        BCH  → Banco de Chile                     (NYSE)   real $42.37
        LTC  → LTC Properties, Inc. (a REIT)      (NYSE)   real $42.01

    Routing those to CoinGecko served the COIN price for a listed security — BTC at
    $78,984 against a real $34.68, a **2,277x** error, under the name "Bitcoin", and a
    REIT rendered as "Litecoin". Measured against FMP on 2026-09-09.

    The correct test is the CONJUNCTION: this is crypto *and* FMP genuinely cannot serve
    it. `is_blocked_symbol` is the licence question and is the authority on the second
    half — a symbol FMP can serve must keep going to FMP. That makes the pair form
    (``BTCUSD``) route to CoinGecko while the bare form (``BTC``) stays on FMP, which is
    exactly the distinction the two ticker namespaces carry.

    Does NOT consult ``CRYPTO_PRICE_SOURCE``: the callers AND this with their own
    ``_fmp_crypto_enabled()`` gate so the "hide, don't remove" kill-switch stays one
    explicit test at each call site.
    """
    # Local import: `fmp_entitlements` pulls in the CoinGecko id map, and keeping this
    # out of module scope leaves `asset_class` importable by anything without dragging
    # the integration layer in behind it.
    from app.integrations.fmp_entitlements import _is_fx_pair, is_blocked_symbol

    if detect_asset_class(symbol) != "crypto" or not is_blocked_symbol(symbol):
        return False
    # `detect_asset_class` calls ANY sufficiently long USD-suffixed symbol crypto, so an
    # FX pair lands here too — and CoinGecko happily answers for it. `EURUSD` resolved to
    # a euro STABLECOIN quoting $1.18: close enough to the real EUR/USD rate to look
    # correct, and wrong the moment that coin depegs. FX is blocked on FMP and has no
    # licensed substitute, so it must stay absent rather than borrow a lookalike.
    return not _is_fx_pair(str(symbol or "").strip().upper())
