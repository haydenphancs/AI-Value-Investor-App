"""Phase 4 invariants: index and commodity screens are served by ENTITLED proxies,
and every user-visible field says which instrument it is describing.

FMP 402s every `^`-prefixed index symbol and every `*USD` commodity future — those are
separate Data Packages that are not on the Order Form. Each affected screen is therefore
backed by an ordinary security instead. Two things can go wrong, and neither shows up in
a build or a green suite:

  1. **A blocked symbol reaches FMP again.** The call answers `{}` or raises, and the
     screen degrades — historically to `$0.00` under a live "market open" badge.
  2. **The label keeps describing the index while the number describes the proxy.**
     SPY trades near $770 against an S&P 500 near 6,600. That is not a rounding
     difference; it is a fabricated price, the same class of defect as the Bitcoin
     "52-Week Low $67.81" (its 2013 all-time low) that this phase removed.

Proxy fidelity is measured, not assumed — every figure quoted below was produced against
live FMP/FRED data on 2026-09-08 and is reproduced in the source comments.
"""
from __future__ import annotations

import inspect
import re

import pytest

from app.integrations.fmp_entitlements import is_blocked_symbol
from app.services import index_service as IDX
from app.services.index_service import _INDEX_PROFILES, _proxy_for


# ── The proxies themselves ───────────────────────────────────────────────────

def test_every_profiled_index_has_a_proxy_that_is_actually_entitled():
    assert set(_INDEX_PROFILES) == {"^GSPC", "^IXIC", "^DJI"}, "the profile set moved"
    for sym, profile in _INDEX_PROFILES.items():
        proxy = profile.get("proxy_symbol")
        assert proxy, f"{sym} has no proxy — its screen cannot be served at all"
        assert not is_blocked_symbol(proxy), (
            f"{sym}'s proxy {proxy!r} is itself outside the licence"
        )
        assert not proxy.startswith("^")


def test_the_nasdaq_proxy_is_ONEQ_and_not_QQQ():
    """QQQ tracks the Nasdaq-100; this screen is the Nasdaq COMPOSITE.

    Measured against FRED `NASDAQCOM` over the year to 2026-09-05, with a SPY-vs-VOO
    measurement floor of TE 0.78%:
        ONEQ  corr 0.9976  TE 1.28%  1Y gap +0.06pp   ✅
        QQQ   corr 0.9728  TE 4.55%  1Y gap +2.54pp   🔴 four times the tracking error,
                                                          and the wrong index besides.
    """
    assert _INDEX_PROFILES["^IXIC"]["proxy_symbol"] == "ONEQ"
    proxies = {p["proxy_symbol"] for p in _INDEX_PROFILES.values()}
    assert "QQQ" not in proxies, "QQQ is the Nasdaq-100 — wrong index for a Composite screen"
    assert proxies == {"SPY", "ONEQ", "DIA"}


@pytest.mark.parametrize("sym,proxy", [("^GSPC", "SPY"), ("^IXIC", "ONEQ"), ("^DJI", "DIA")])
def test_proxy_lookup_resolves(sym, proxy):
    assert _proxy_for(sym) == proxy and _proxy_for(sym.lower()) == proxy


@pytest.mark.parametrize("unknown", ["^RUT", "^VIX", "^FTSE", "", None])
def test_an_unprofiled_symbol_falls_back_to_itself_so_it_fails_loudly(unknown):
    """`indices.py`'s route regex accepts far more than the three profiled symbols, so
    this path is reachable. Resolving an unknown `^` symbol to some plausible ETF would
    invent an answer; returning it unchanged keeps it hitting the entitlement guard."""
    assert _proxy_for(unknown) == unknown


# ── No blocked symbol may reach FMP from this service ────────────────────────

def _src_without_comments(obj) -> str:
    """Source with `#` comments AND docstrings removed.

    Stripping docstrings is load-bearing, not tidiness: every function these scans cover
    EXPLAINS the invariant in its own docstring, naming the very tokens the scan asserts
    are absent. `_fred_quote`'s docstring says it "omits `dayHigh`/`dayLow`/`volume`",
    so a comment-only strip made this file fail on the correct implementation. The
    mirror-image failure is the dangerous one: a scan for a token's PRESENCE passing on
    a revert whose explanatory comment survived.
    """
    src = inspect.getsource(obj)
    src = re.sub(r'("""|\'\'\')(?:(?!\1).)*?\1', "", src, flags=re.S)
    return "\n".join(l for l in src.splitlines() if not l.strip().startswith("#"))


@pytest.mark.parametrize("fn,call", [
    # Leading `.`/`(` so the pattern cannot match the enclosing function's OWN signature
    # — `async def _get_quote(self, symbol: str)` matched a bare "get_quote(" and made
    # the first version of this guard fail on the fix rather than on the bug.
    ("_get_quote", ".get_quote("),
    ("_get_history", "_fetch_all_daily("),
    ("_get_chart", "fetch_chart_data("),
])
def test_every_upstream_call_passes_the_proxy_not_the_index_symbol(fn, call):
    body = _src_without_comments(getattr(IDX.IndexService, fn))
    assert call in body, f"scan drifted — {fn} no longer makes a {call!r} call"
    for m in re.finditer(re.escape(call) + r"([^)]*)", body):
        args = m.group(1)
        assert "_proxy_for(" in args or "proxy" in args, (
            f"{fn} passes a raw symbol to {call}: {args.strip()!r} — a `^` symbol there "
            "is refused by `is_blocked_symbol` and the screen degrades silently"
        )


# ── The labels describe the instrument whose number is shown ─────────────────

@pytest.mark.parametrize("sym", sorted(_INDEX_PROFILES))
def test_the_display_name_is_the_fund_not_the_bare_index(sym):
    """`index_name` is the screen's headline, rendered directly above the price."""
    name = _INDEX_PROFILES[sym]["name"]
    proxy = _INDEX_PROFILES[sym]["proxy_symbol"]
    assert name not in ("S&P 500", "Nasdaq Composite", "Dow Jones Industrial Average"), (
        f"{sym} still headlines the INDEX while showing {proxy}'s price"
    )
    assert any(w in name for w in ("ETF", "Trust")), (
        f"{sym}'s name must identify the fund, got {name!r}"
    )


@pytest.mark.parametrize("sym", sorted(_INDEX_PROFILES))
def test_the_description_says_the_numbers_are_the_funds(sym):
    desc = _INDEX_PROFILES[sym]["description"]
    assert "track" in desc.lower(), f"{sym} does not say it TRACKS the index"
    assert "not identical" in desc.lower(), (
        f"{sym}'s description does not warn that fund prices differ from the index level"
    )


@pytest.mark.parametrize("sym,inception", [
    ("^GSPC", "1993"), ("^IXIC", "2003"), ("^DJI", "1998"),
])
def test_inception_is_the_funds_not_the_indexs(sym, inception):
    """1957 / 1971 / 1896 are the INDICES' inceptions. Under a fund name they read as
    the fund's, which would be off by decades."""
    got = _INDEX_PROFILES[sym]["inception_date"]
    assert inception in got, f"{sym} inception {got!r} is not the fund's"
    for index_era in ("1957", "1971", "1896"):
        assert index_era not in got


def test_the_dead_index_return_constant_is_not_reintroduced():
    for sym, p in _INDEX_PROFILES.items():
        assert "avg_annual_return" not in p, (
            f"{sym}: this constant used to render as the S&P benchmark on every index"
        )


# ═══════════════════════════════════════════════════════════════════════════
# Commodity screens
# ═══════════════════════════════════════════════════════════════════════════

from app.integrations.fmp import FMPNotEntitledException          # noqa: E402
from app.services import commodity_service as COM                 # noqa: E402
from app.services.commodity_service import (                      # noqa: E402
    _COMMODITY_PROFILES,
    _COMMODITY_SOURCE_ETF,
    _COMMODITY_SOURCE_FRED,
    _WITHDRAWN_COMMODITIES,
    _commodity_market_status,
    _raise_if_withdrawn,
    _ref_of,
    _source_of,
)

#: Physically-backed only. A futures-based fund is a DIFFERENT ASSET from the commodity:
#: measured 2021-01→2026-07 against each underlying benchmark, USO ran +202pp vs WTI and
#: UNG −69.9pp vs Henry Hub, while the funds that hold the metal drift only by their
#: expense ratio (GLD vs IAU: −1.8pp over the same window).
_PHYSICAL_METAL_FUNDS = {"GLD", "SLV", "PPLT", "PALL"}
_FUTURES_BACKED_FUNDS = {"USO", "USL", "DBO", "BNO", "OILK", "UNG", "UNL", "BOIL",
                         "CPER", "WEAT", "CORN", "SOYB", "CANE"}


def test_the_served_commodity_set_is_exactly_the_six_with_an_honest_source():
    assert set(_COMMODITY_PROFILES) == {"GC", "SI", "PL", "PA", "CL", "NG"}
    assert set(_WITHDRAWN_COMMODITIES) == {"KC", "CT", "CC", "HG", "ZW", "ZC", "ZS", "SB"}
    assert not (set(_COMMODITY_PROFILES) & set(_WITHDRAWN_COMMODITIES))


def test_no_commodity_screen_is_backed_by_a_futures_fund():
    """The whole reason eight screens were withdrawn rather than re-pointed."""
    for root, meta in _COMMODITY_PROFILES.items():
        ref = meta["ref"]
        assert ref not in _FUTURES_BACKED_FUNDS, (
            f"{root} is served by {ref}, a futures-based fund — its roll yield makes it a "
            f"different asset from the commodity the screen is named after"
        )
        if meta["source"] == _COMMODITY_SOURCE_ETF:
            assert ref in _PHYSICAL_METAL_FUNDS, f"{root}: {ref} is not physically backed"
        else:
            assert meta["source"] == _COMMODITY_SOURCE_FRED


def test_cocoa_is_not_the_beverage_company():
    """`COCO` resolves, trades, and has a price — it is The Vita Coco Company, Inc., a
    NASDAQ beverage stock. It was listed as the cocoa proxy in the phase handoff. A
    ticker that resolves is not a proxy that is correct."""
    refs = {m["ref"] for m in _COMMODITY_PROFILES.values()}
    assert "COCO" not in refs, "COCO is Vita Coco (beverages), not cocoa"
    assert "CC" not in _COMMODITY_PROFILES, "cocoa has no live proxy — every ETN is delisted"


@pytest.mark.parametrize("root", sorted(_WITHDRAWN_COMMODITIES))
def test_a_withdrawn_commodity_refuses_contractually(root):
    """`FMP_NOT_ENTITLED`, not `FMP_UNAVAILABLE`: retrying can never fix a package we
    did not buy, and iOS branches on the contractual code to stop the retry loop."""
    from app.api.error_response import classify_exception

    with pytest.raises(FMPNotEntitledException) as ei:
        _raise_if_withdrawn(root)
    assert _WITHDRAWN_COMMODITIES[root] in str(ei.value).lower()
    assert classify_exception(ei.value)[0].value == "FMP_NOT_ENTITLED"
    # ...and the `*USD` spelling a saved watchlist row would carry.
    with pytest.raises(FMPNotEntitledException):
        _raise_if_withdrawn(f"{root}USD")


@pytest.mark.parametrize("root", sorted(_COMMODITY_PROFILES))
def test_a_served_commodity_is_not_refused(root):
    _raise_if_withdrawn(root)          # must not raise
    assert not is_blocked_symbol(_ref_of(root)), f"{root}'s source is itself blocked"


def test_the_entry_points_all_guard_against_a_withdrawn_symbol():
    """Three routes reach a commodity; `get_commodity_quote` projects from the detail
    build, so the guard has to sit on the other two."""
    for fn in ("get_commodity_core", "_build_commodity_detail"):
        body = _src_without_comments(getattr(COM.CommodityService, fn))
        assert "_raise_if_withdrawn(" in body, f"{fn} serves a withdrawn commodity"


def test_fred_backed_screens_never_claim_an_open_market():
    """FRED's EIA series publish ~5 BUSINESS days behind (measured 2026-09-08:
    DCOILWTICO newest 2026-09-01). "Market Open" over a settled print five days old is
    the same false liveness as the index `$0.00` badge."""
    for root in ("CL", "NG"):
        assert _source_of(root) == _COMMODITY_SOURCE_FRED
        assert _commodity_market_status(root) == "Market Closed"
        assert _commodity_market_status(f"{root}USD") == "Market Closed"


def test_etf_backed_screens_follow_EQUITY_hours_not_the_futures_session():
    """GLD is a fund: it trades 09:30–16:00, not the ~23h futures session this module's
    status helper was originally written for. Claiming the futures session over a fund's
    last trade would assert liveness at 03:00 ET."""
    body = _src_without_comments(COM._commodity_market_status)
    assert "session_phase" in body, (
        "ETF-backed commodity screens are back on the 23h futures session"
    )
    assert "_COMMODITY_SOURCE_ETF" in body


def test_fred_history_is_close_only_and_never_fabricates_a_candle():
    """A daily spot series has ONE value per day. Emitting open=high=low=close would
    render a candle asserting an intraday range that was never observed."""
    body = _src_without_comments(COM.CommodityService._fred_history)
    assert '"close"' in body
    for ohlc in ('"open"', '"high"', '"low"', '"volume"'):
        assert ohlc not in body, f"FRED history is inventing {ohlc}"


def test_fred_quote_omits_unknown_keys_rather_than_zeroing_them():
    """`quote.get("dayHigh") or 0` means a PRESENT-but-zero key renders "$0.00" while an
    ABSENT one renders "—". Same rule `price_service._shape` follows for its 52w band."""
    body = _src_without_comments(COM.CommodityService._fred_quote)
    for absent in ("dayHigh", "dayLow", "volume", "avgVolume", "yearHigh", "yearLow"):
        assert absent not in body, f"_fred_quote is emitting a fabricated {absent}"
    assert '"price"' in body and '"previousClose"' in body


def test_the_two_market_index_constants_agree():
    """`updates_insight_sweeper` and `widget_movers_service` each define their own
    `MARKET_INDEX_SYMBOL`. They feed the same idea — "what did the whole market do" —
    into two surfaces, and Phase 4 initially re-pointed only one of them. A split would
    be invisible: each surface would look internally consistent while disagreeing with
    the other about the market's move."""
    from app.services.updates_insight_sweeper import MARKET_INDEX_SYMBOL as SWEEPER
    from app.services.widget_movers_service import MARKET_INDEX_SYMBOL as WIDGET

    assert SWEEPER == WIDGET, f"market index constants have drifted: {SWEEPER} vs {WIDGET}"
    assert not is_blocked_symbol(SWEEPER), (
        f"{SWEEPER} is outside the licence — `market_change` would be permanently None, "
        "which fails the market-wide-crash guard OPEN"
    )


# ── The number on screen and the label above it must be the same instrument ──
#
# Caught during the adversarial pass, AFTER the proxies were wired and every test was
# green: the metal screens kept their commodity names while showing the FUND's share
# price. "Platinum $16.46" — PPLT holds ~1/100th of an ounce, and platinum spot is near
# $1,600. A 100x error, rendered with a confident "/oz" suffix. Exactly the index
# "$770 under an S&P 500 heading" defect, one screen over.

_METAL_ROOTS = ("GC", "SI", "PL", "PA")
_ENERGY_ROOTS = ("CL", "NG")


@pytest.mark.parametrize("root", _METAL_ROOTS)
def test_a_metal_screen_names_the_fund_it_prices(root):
    meta = _COMMODITY_PROFILES[root]
    assert meta["source"] == _COMMODITY_SOURCE_ETF
    name = meta["name"]
    for bare in ("Gold", "Silver", "Platinum", "Palladium"):
        assert name != bare, (
            f"{root} is headlined {name!r} while showing {meta['ref']}'s SHARE price — "
            "a share is a fraction of an ounce"
        )
    assert any(w in name for w in ("Shares", "Trust")), f"{root}: {name!r} is not a fund name"


@pytest.mark.parametrize("root", _METAL_ROOTS)
def test_a_metal_screen_does_not_claim_a_per_ounce_price(root):
    """`unit` drives a "/oz" suffix on the headline price via `_UNIT_ABBREV`."""
    assert _COMMODITY_PROFILES[root]["unit"] == "share", (
        f"{root} appends a per-ounce suffix to a per-share price"
    )


@pytest.mark.parametrize("root", _METAL_ROOTS)
def test_a_metal_description_warns_that_the_price_is_the_funds(root):
    desc = _COMMODITY_PROFILES[root]["description"].lower()
    assert "share price" in desc and "spot price" in desc, (
        f"{root}'s description does not distinguish the fund's price from spot"
    )


@pytest.mark.parametrize("root,unit", [("CL", "barrel"), ("NG", "mmbtu")])
def test_an_energy_screen_keeps_the_commodity_name_and_unit(root, unit):
    """The counter-case, and the reason the rule is about the SOURCE rather than a blanket
    rename: FRED gives the actual benchmark spot in the actual unit, so "Crude Oil WTI
    $91.48/bbl" is exactly right — renaming it after a fund would be its own inaccuracy."""
    meta = _COMMODITY_PROFILES[root]
    assert meta["source"] == _COMMODITY_SOURCE_FRED
    assert meta["unit"] == unit, f"{root} lost its real unit"
    assert "Shares" not in meta["name"] and "Trust" not in meta["name"]


def test_the_related_row_quotes_the_ref_not_the_blocked_code():
    """`related` holds `*USD` futures codes — all blocked. Quoting them directly returned
    `{}` for every one, so "People Also Check" rendered empty on every commodity screen."""
    body = _src_without_comments(COM.CommodityService._get_related)
    assert "_ref_of(" in body, "the related row is quoting blocked futures codes again"


# ── Phase 4's exit criterion, in one place ───────────────────────────────────
#
# "No `^`/commodity symbol reaches FMP." Every list below is fed STRAIGHT to a quote or
# history call, so a blocked entry is not an error — it is a silently missing tile, an
# absent index band, or a macro factor that never fires. All four failure modes look
# identical to "nothing happened today".

def test_no_symbol_list_that_feeds_FMP_contains_a_blocked_symbol():
    import app.services.home_dashboard_service as HD
    import app.services.home_service as HS
    import app.services.news_cache_service as NC
    import app.services.widget_movers_service as WM
    from app.services.agents.ticker_report_data_collector import _MACRO_SERIES

    feeds = {
        "home_dashboard._PULSE_SYMBOLS": [c["symbol"] for c in HD._PULSE_SYMBOLS],
        "home_service.DEFAULT_MARKET_TICKERS": [c["symbol"] for c in HS.DEFAULT_MARKET_TICKERS],
        "widget_movers._INDEX_SYMBOLS": [s for s, _ in WM._INDEX_SYMBOLS],
        "news_cache.MARKET_INDEX_SYMBOLS": NC.MARKET_INDEX_SYMBOLS.split(","),
        "index_service proxies": [p["proxy_symbol"] for p in _INDEX_PROFILES.values()],
        "commodity_service refs": [
            m["ref"] for m in _COMMODITY_PROFILES.values()
            if m["source"] == _COMMODITY_SOURCE_ETF
        ],
        "macro ETF-backed refs": [
            s.ref for s in _MACRO_SERIES if s.source in ("etf", "realized_vol")
        ],
    }
    offenders = {
        name: [s for s in syms if is_blocked_symbol(s)]
        for name, syms in feeds.items()
    }
    offenders = {k: v for k, v in offenders.items() if v}
    assert not offenders, f"blocked symbols still fed to FMP: {offenders}"

    # Anti-vacuity: every list must be non-empty, or "no offenders" means nothing.
    empty = [name for name, syms in feeds.items() if not syms]
    assert not empty, f"empty symbol list — this guard would pass trivially: {empty}"


def test_the_macro_tier_reads_only_licensed_sources():
    from app.services.agents.ticker_report_data_collector import _MACRO_SERIES

    assert {s.key for s in _MACRO_SERIES} == {"WTI", "GOLD", "VOL", "UST10Y", "USD"}
    for spec in _MACRO_SERIES:
        assert spec.source in ("fred", "etf", "realized_vol"), spec
        if spec.source != "fred":
            assert not is_blocked_symbol(spec.ref), f"{spec.key} reads a blocked symbol"

    # The two FRED series that carry a reproduction prohibition must never appear.
    # S&P DJI's notice on `SP500`/`DJIA` reads: "Reproduction ... in any form is
    # prohibited except with the prior written permission of S&P Dow Jones Indices LLC."
    # SPY and DIA are entitled and measure at the noise floor, so they are not needed.
    refs = {s.ref for s in _MACRO_SERIES}
    assert not (refs & {"SP500", "DJIA"}), (
        "a FRED series carrying an S&P DJI reproduction prohibition is wired in"
    )
    # And `VIXCLS` carries a Cboe copyright whose reprint permission runs to FRED, not us.
    assert "VIXCLS" not in refs, "the Cboe-copyrighted VIX series is wired in"
