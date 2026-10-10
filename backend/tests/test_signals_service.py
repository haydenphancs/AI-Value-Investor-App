"""
App-Exclusive Signals — aggregation math + service-degradation tests.

Guards (mirrors testing.md — pure inputs inline, no network / Supabase):
1. The three pure aggregators (`_aggregate_congress` / `_aggregate_whale` /
   `_aggregate_earnings`) rank correctly and degrade honestly on the messy /
   outlier inputs FMP + the whale registry actually produce — distinct-member and
   distinct-CIK dedup, disclosure windowing, surprise thresholds/caps, signed
   misses, missing/zero/NaN fields, class-share folding — never a wrong count,
   never a fabricated card.
2. The service build degrades per-branch (one source failing → that card None,
   the others render) and never caches an all-empty (failed) build.
"""

import asyncio
import functools
from datetime import datetime, timezone

import pytest

from app.schemas.home_dashboard import (
    SignalGroupResponse,
    SignalRowResponse,
    SignalsGroupResponse,
)
from app.services import signals_service as ssvc
from app.services.signals_service import (
    _aggregate_congress,
    _aggregate_whale,
    _aggregate_earnings,
    _whale_adds,
)
from _price_fakes import PriceFromFMPFake

# Fixed "now" so disclosure windowing is deterministic. 30-day window → on/after 2026-05-31.
NOW = datetime(2026, 6, 30, 12, 0, tzinfo=timezone.utc)


# ── 1. Congress aggregation ────────────────────────────────────────────


def test_congress_counts_distinct_members_and_ignores_sales():
    senate = [
        {"symbol": "NVDA", "type": "Purchase", "disclosureDate": "2026-06-25",
         "firstName": "Jane", "lastName": "Doe", "assetDescription": "NVIDIA Corp"},
        # SAME member buys NVDA again → must count ONCE.
        {"symbol": "NVDA", "type": "Purchase", "disclosureDate": "2026-06-26",
         "firstName": "Jane", "lastName": "Doe"},
        {"symbol": "NVDA", "type": "purchase", "disclosureDate": "2026-06-24",
         "firstName": "John", "lastName": "Smith"},
        {"symbol": "MSFT", "type": "Purchase", "disclosureDate": "2026-06-20",
         "firstName": "Al", "lastName": "Gore"},
    ]
    house = [
        {"symbol": "NVDA", "type": "Purchase", "disclosureDate": "2026-06-23",
         "firstName": "Bob", "lastName": "Roe"},
        # A SALE must be ignored entirely.
        {"symbol": "AAPL", "type": "Sale", "disclosureDate": "2026-06-25",
         "firstName": "X", "lastName": "Y"},
    ]
    g = _aggregate_congress(senate, house, now=NOW)
    assert g is not None and g.kind == "congress"
    assert [(r.symbol, r.value) for r in g.entries] == [("NVDA", 3.0), ("MSFT", 1.0)]
    assert g.entries[0].name == "NVIDIA Corp"        # first non-empty assetDescription
    assert g.as_of_date == "2026-06-26"              # latest disclosure among counted


def test_congress_all_sales_returns_none():
    senate = [{"symbol": "NVDA", "type": "Sale", "disclosureDate": "2026-06-25",
               "lastName": "Doe"}]
    assert _aggregate_congress(senate, [], now=NOW) is None


def test_congress_top_below_min_members_returns_none():
    # Only one member bought anything → no honest "most-bought" headline.
    senate = [{"symbol": "MSFT", "type": "Purchase", "disclosureDate": "2026-06-20",
               "lastName": "Gore"}]
    assert _aggregate_congress(senate, [], now=NOW) is None


def test_congress_folds_class_share_symbol_variants():
    senate = [
        {"symbol": "BRK.B", "type": "Purchase", "disclosureDate": "2026-06-25", "lastName": "A"},
        {"symbol": "BRK-B", "type": "Purchase", "disclosureDate": "2026-06-24", "lastName": "B"},
    ]
    g = _aggregate_congress(senate, [], now=NOW)
    assert g is not None
    assert g.entries[0].symbol == "BRK-B" and g.entries[0].value == 2.0


def test_congress_windows_out_stale_disclosures():
    senate = [
        {"symbol": "NVDA", "type": "Purchase", "disclosureDate": "2026-06-25", "lastName": "A"},
        {"symbol": "NVDA", "type": "Purchase", "disclosureDate": "2026-06-26", "lastName": "B"},
        {"symbol": "OLD", "type": "Purchase", "disclosureDate": "2026-01-01", "lastName": "C"},
        {"symbol": "OLD", "type": "Purchase", "disclosureDate": "2026-01-02", "lastName": "D"},
    ]
    g = _aggregate_congress(senate, [], now=NOW)
    assert g is not None
    assert [r.symbol for r in g.entries] == ["NVDA"]   # OLD (January) excluded by window


def test_congress_falls_back_to_all_rows_when_no_date_parses():
    # Degenerate feed: no parseable dates → keep buys so the card stays alive; as_of None.
    senate = [
        {"symbol": "NVDA", "type": "Purchase", "lastName": "A"},
        {"symbol": "NVDA", "type": "Purchase", "lastName": "B"},
    ]
    g = _aggregate_congress(senate, [], now=NOW)
    assert g is not None and g.entries[0].value == 2.0 and g.as_of_date is None


def test_congress_identifies_member_by_office_when_name_missing():
    senate = [
        {"symbol": "NVDA", "type": "Purchase", "disclosureDate": "2026-06-25", "office": "Jane Doe"},
        {"symbol": "NVDA", "type": "Purchase", "disclosureDate": "2026-06-24", "office": "John Smith"},
    ]
    g = _aggregate_congress(senate, [], now=NOW)
    assert g is not None and g.entries[0].value == 2.0


def test_congress_empty_inputs_return_none():
    assert _aggregate_congress([], [], now=NOW) is None
    assert _aggregate_congress("garbage", None, now=NOW) is None


# ── 2. Whale aggregation: SHARE increases in each fund's latest 13F ──────
#
# 2026-10-09: "adding" used to be `whale_holdings.change_percent > 0` — the change in the
# stock's portfolio WEIGHT, so a rally alone counted. It is now a `whale_trades` BOUGHT row
# of type New / Increased (share-based) in the fund's LATEST, CURRENT 13F group.
# At NOW (2026-06-30) the expected 13F quarter is 2026-Q1 (45-day lag + 7-day grace).

_Q1 = "2026-03-31"


def _whales13f(ciks, filed=None):
    return [{"id": wid, "cik": cik, "last_filing_period": (filed or {}).get(wid, "2026-Q1")}
            for wid, cik in ciks.items()]


def _group(wid, date=_Q1):
    return {"id": f"g-{wid}-{date}", "whale_id": wid, "date": date}


def _trade(wid, ticker, *, action="BOUGHT", trade_type="Increased", amount=1_000_000.0,
           date=_Q1, alloc=1.0, name=None, group_date=None):
    return {"id": f"t-{wid}-{ticker}-{action}-{date}", "whale_id": wid,
            "trade_group_id": f"g-{wid}-{group_date or date}", "ticker": ticker,
            "company_name": name if name is not None else ticker, "action": action,
            "trade_type": trade_type, "amount": amount, "new_allocation": alloc, "date": date}


def _adds(ciks, trades, *, groups=None, filed=None, now=NOW):
    groups = groups if groups is not None else [_group(w) for w in ciks]
    return _whale_adds(_whales13f(ciks, filed), groups, trades, now=now)


def test_whale_dedups_person_and_fund_sharing_a_cik():
    # w1 (person) and w2 (their fund) share ONE CIK → count as one fund, not two.
    ciks = {"w1": "CIK1", "w2": "CIK1", "w3": "CIK2"}
    adds = _adds(ciks, [_trade("w1", "NVDA"), _trade("w2", "NVDA"), _trade("w3", "NVDA")])
    g = _aggregate_whale(adds, names={"NVDA": "NVIDIA"})
    assert g is not None and g.kind == "whale"
    assert g.entries[0].symbol == "NVDA" and g.entries[0].value == 2.0   # {CIK1, CIK2}
    assert g.entries[0].name == "NVIDIA"


def test_whale_registry_of_25_with_6_shared_pairs_yields_19_funds():
    # Mirrors the real registry: 6 person↔fund pairs share a CIK, 13 singles → 19 distinct.
    ciks = {}
    for i in range(6):
        ciks[f"p{i}a"] = f"PAIR{i}"
        ciks[f"p{i}b"] = f"PAIR{i}"
    for i in range(13):
        ciks[f"s{i}"] = f"SOLO{i}"
    assert len(ciks) == 25
    g = _aggregate_whale(_adds(ciks, [_trade(wid, "NVDA") for wid in ciks]))
    assert g is not None and g.entries[0].value == 19.0


def test_whale_null_cik_whales_stay_distinct():
    # A blank / null CIK gets a per-whale `nocik:` key and must NOT collapse.
    adds = _adds({"w1": None, "w2": ""}, [_trade("w1", "NVDA"), _trade("w2", "NVDA")])
    assert set(adds["NVDA"]) == {"nocik:w1", "nocik:w2"}
    assert _aggregate_whale(adds).entries[0].value == 2.0


def test_whale_counts_only_share_increases_never_a_weight_move():
    ciks = {f"w{i}": f"C{i}" for i in range(1, 8)}
    trades = [
        _trade("w1", "NVDA", trade_type="Increased"),
        _trade("w2", "NVDA", trade_type="New"),
        # A share increase while the WEIGHT FELL (the stock lagged the book) still counts.
        _trade("w3", "NVDA", trade_type="Increased", alloc=0.4),
        _trade("w4", "NVDA", action="SOLD", trade_type="Decreased"),
        _trade("w5", "NVDA", action="SOLD", trade_type="Closed"),
        # A BOUGHT row with a non-add type (corrupt) is not trusted as an add.
        _trade("w6", "NVDA", trade_type="Decreased"),
        # w7: the weight rose on price alone — no trade row at all → not "adding".
        _trade("wX", "NVDA"),        # not a 13F registry whale (no roster row, no group)
    ]
    adds = _adds(ciks, trades)
    assert set(adds["NVDA"]) == {"C1", "C2", "C3"}
    assert adds["NVDA"]["C2"].trade_type == "New"


def test_whale_only_the_latest_current_quarter_counts():
    now = datetime(2026, 10, 9, 12, 0, tzinfo=timezone.utc)    # expected quarter: 2026-Q2
    ciks = {"early": "C1", "onq2": "C2", "late": "C3", "filednone": "C4"}
    groups = [
        _group("early", "2026-06-30"), _group("early", "2026-09-30"),   # filed Q3 early
        _group("onq2", "2026-06-30"),
        _group("late", "2026-03-31"),                                   # missed Q2: late
        _group("filednone", "2026-06-30"),                              # filed Q3, no trades
    ]
    trades = [
        _trade("early", "OLDBUY", date="2026-06-30"),    # its Q2 buy: superseded by its Q3 filing
        _trade("early", "NVDA", date="2026-09-30"),
        _trade("onq2", "NVDA", date="2026-06-30"),
        _trade("late", "NVDA", date="2026-03-31"),
        _trade("filednone", "NVDA", date="2026-06-30"),
    ]
    adds = _adds(ciks, trades, groups=groups, now=now,
                 filed={"early": "2026-Q3", "onq2": "2026-Q2", "late": "2026-Q1",
                        "filednone": "2026-Q3"})
    assert set(adds) == {"NVDA"}
    assert set(adds["NVDA"]) == {"C1", "C2"}
    assert adds["NVDA"]["C1"].quarter_end == "2026-09-30"


def test_a_deadline_day_filer_is_not_dropped_before_the_next_sweep():
    # Q2 13Fs are due Aug 14. On Aug 15 a fund still showing Q1 may simply not have been
    # hydrated yet: the 7-day grace keeps it; a week later it is late.
    ciks = {"w1": "C1", "w2": "C2"}
    trades = [_trade("w1", "NVDA"), _trade("w2", "NVDA")]
    on_deadline = datetime(2026, 8, 15, 12, 0, tzinfo=timezone.utc)
    week_after = datetime(2026, 8, 25, 12, 0, tzinfo=timezone.utc)
    assert len(_adds(ciks, trades, now=on_deadline)["NVDA"]) == 2
    assert _adds(ciks, trades, now=week_after) == {}


def test_the_fallback_quarter_end_is_the_same_quarter_and_one_group_is_read():
    # The hydrators fall back to `{y}-{q*3:02d}-30`, so Q1 can exist as 03-30 AND 03-31.
    ciks = {"w1": "C1", "w2": "C2"}
    groups = [_group("w1", "2026-03-30"), _group("w1", "2026-03-31"), _group("w2", "2026-03-30")]
    trades = [
        _trade("w1", "STALE", date="2026-03-30"),          # the older group: not read
        _trade("w1", "NVDA", date="2026-03-31"),
        _trade("w2", "NVDA", date="2026-03-30"),           # w2's only group is the fallback date
    ]
    adds = _adds(ciks, trades, groups=groups)
    assert set(adds) == {"NVDA"} and set(adds["NVDA"]) == {"C1", "C2"}


def test_a_stale_bought_and_sold_pair_is_refused_and_logged(caplog):
    import logging
    ciks = {"w1": "C1", "w2": "C2", "w3": "C3"}
    trades = [
        _trade("w1", "NVDA"), _trade("w1", "NVDA", action="SOLD", trade_type="Decreased"),
        _trade("w2", "NVDA"), _trade("w3", "NVDA"),
    ]
    with caplog.at_level(logging.WARNING, logger="app.services.signals_service"):
        adds = _adds(ciks, trades)
    assert set(adds["NVDA"]) == {"C2", "C3"}
    assert any("both a BOUGHT and a SOLD" in r.getMessage() and "NVDA@w1" in r.getMessage()
               for r in caplog.records)


def test_rows_outside_their_group_are_not_read(caplog):
    import logging
    ciks = {"w1": "C1", "w2": "C2"}
    trades = [
        _trade("w1", "NVDA"),
        _trade("w2", "NVDA", date="2026-02-15", group_date=_Q1),   # date ≠ its group's
        {**_trade("w2", "AAPL"), "whale_id": "w1"},                # filed under another whale
        {**_trade("w2", "MSFT"), "trade_group_id": None},          # no group at all
    ]
    with caplog.at_level(logging.WARNING, logger="app.services.signals_service"):
        adds = _adds(ciks, trades)
    assert set(adds) == {"NVDA"} and set(adds["NVDA"]) == {"C1"}
    assert any("date differs" in r.getMessage() for r in caplog.records)


def test_class_share_variants_fold_and_amounts_degrade_to_none():
    ciks = {"w1": "C1", "w2": "C2"}
    trades = [
        _trade("w1", "BRK.B", amount=float("nan")),           # unknown $: still an add, amount None
        _trade("w2", "BRK-B", amount=-5.0, alloc=float("inf")),
    ]
    adds = _adds(ciks, trades)
    assert set(adds) == {"BRK-B"} and len(adds["BRK-B"]) == 2
    by = adds["BRK-B"]
    assert by["C1"].amount is None and by["C1"].new_allocation == 1.0    # NaN $ → None, weight kept
    assert by["C2"].amount is None and by["C2"].new_allocation is None    # negative $ / inf weight → None


def test_whale_top_below_min_funds_returns_none():
    assert _aggregate_whale(_adds({"w1": "C1"}, [_trade("w1", "NVDA")])) is None


def test_whale_empty_returns_none_and_passes_through_as_of():
    assert _aggregate_whale({}) is None
    assert _aggregate_whale(_adds({"w1": "C1"}, [])) is None
    g = _aggregate_whale(_adds({"w1": "C1", "w2": "C2"}, [_trade("w1", "NVDA"), _trade("w2", "NVDA")]),
                         as_of="2026-03-31")
    assert g is not None and g.as_of_date == "2026-03-31"


def test_card_names_prefer_holdings_then_a_real_trade_name_never_a_bare_ticker():
    ciks = {"w1": "C1", "w2": "C2"}
    trades = [_trade(w, t, name=n) for w in ciks for t, n in
              (("NVDA", "NVDA"), ("TSM", "TAIWAN SEMICONDUCTOR"), ("BRK-B", "BRK.B"))]
    g = _aggregate_whale(_adds(ciks, trades), names={"NVDA": "NVIDIA Corporation"})
    names = {e.symbol: e.name for e in g.entries}
    assert names == {"NVDA": "NVIDIA Corporation", "TSM": "TAIWAN SEMICONDUCTOR", "BRK-B": ""}


# ── 3. Earnings aggregation ────────────────────────────────────────────


def test_earnings_ranks_freshest_first_not_by_magnitude():
    # Freshest-first: the most-recent report leads even when an OLDER one has a bigger
    # |surprise| (the "not last week's data" guarantee).
    cal = [
        {"symbol": "AVGO", "epsActual": 1.22, "epsEstimated": 1.0, "date": "2026-06-27"},  # +22%, FRESHER
        {"symbol": "XYZ", "epsActual": 0.75, "epsEstimated": 1.0, "date": "2026-06-26"},   # -25%, older/bigger
        {"symbol": "SMALL", "epsActual": 1.05, "epsEstimated": 1.0, "date": "2026-06-25"}, # +5% (below floor)
    ]
    g = _aggregate_earnings(cal)
    assert g is not None and g.kind == "earnings"
    assert [(r.symbol, r.value) for r in g.entries] == [("AVGO", 22.0), ("XYZ", -25.0)]
    assert g.as_of_date == "2026-06-27"


def test_earnings_skips_missing_and_zero_estimates():
    cal = [
        {"symbol": "A", "epsActual": 1.2},                                    # no estimate → skip
        {"symbol": "Z", "epsActual": 1.2, "epsEstimated": 0.0},               # est 0 → surprise None → skip
        {"symbol": "B", "epsActual": 1.5, "epsEstimated": 1.0, "date": "2026-06-27"},  # +50%
    ]
    g = _aggregate_earnings(cal)
    assert g is not None and [r.symbol for r in g.entries] == ["B"]


def test_earnings_caps_penny_eps_blowups():
    cal = [
        {"symbol": "PENNY", "epsActual": 0.5, "epsEstimated": 0.01, "date": "2026-06-27"},  # 4900% → capped out
        {"symbol": "B", "epsActual": 1.5, "epsEstimated": 1.0, "date": "2026-06-26"},       # +50%
    ]
    g = _aggregate_earnings(cal)
    assert g is not None and [r.symbol for r in g.entries] == ["B"]


def test_earnings_accepts_legacy_field_names():
    cal = [{"symbol": "L", "eps": 1.4, "epsEstimate": 1.0, "date": "2026-06-27"}]  # +40% via legacy keys
    g = _aggregate_earnings(cal)
    assert g is not None and g.entries[0].symbol == "L" and g.entries[0].value == 40.0


def test_earnings_dedups_symbol_keeping_larger_magnitude():
    cal = [
        {"symbol": "D", "epsActual": 1.15, "epsEstimated": 1.0, "date": "2026-06-20"},  # +15%
        {"symbol": "D", "epsActual": 0.60, "epsEstimated": 1.0, "date": "2026-06-27"},  # -40% (bigger)
    ]
    g = _aggregate_earnings(cal)
    assert g is not None and len(g.entries) == 1
    assert g.entries[0].value == -40.0


def test_earnings_rejects_nan_actuals_and_returns_none_when_nothing_clears():
    cal = [
        {"symbol": "N", "epsActual": float("nan"), "epsEstimated": 1.0},   # NaN → skip
        {"symbol": "B", "epsActual": 1.5, "epsEstimated": 1.0, "date": "2026-06-27"},
    ]
    g = _aggregate_earnings(cal)
    assert g is not None and [r.symbol for r in g.entries] == ["B"]
    # Nothing above the 10% floor → honest empty.
    assert _aggregate_earnings([{"symbol": "F", "epsActual": 1.02, "epsEstimated": 1.0}]) is None
    assert _aggregate_earnings([]) is None


# ── 4. Service degradation / dedup / caching ───────────────────────────


@pytest.mark.asyncio
async def test_build_degrades_per_branch():
    s = ssvc.SignalsService()

    async def boom():
        raise RuntimeError("congress feed down")

    async def whale_ok():
        return SignalGroupResponse(
            kind="whale",
            entries=[SignalRowResponse(rank=1, symbol="MSFT", name="", value=3.0)],
        )

    async def earnings_none():
        return None

    s._build_congress = boom          # type: ignore[assignment]
    s._build_whale = whale_ok         # type: ignore[assignment]
    s._build_earnings = earnings_none # type: ignore[assignment]
    s._build_ceo = earnings_none      # type: ignore[assignment]  (else the real FMP call runs)

    result, failed = await s._build()
    assert result.congress is None                       # raised → degraded, not fatal
    assert result.whale is not None and result.whale.entries[0].symbol == "MSFT"
    assert result.earnings is None
    assert result.ceo is None
    # Only the branch that RAISED is a failure; `None` is an honest empty.
    assert failed == frozenset({"congress"})


@pytest.mark.asyncio
async def test_all_none_build_is_not_cached(monkeypatch):
    ssvc.SignalsService._cache.clear()
    ssvc.SignalsService._inflight.clear()
    s = ssvc.SignalsService()
    monkeypatch.setattr(s, "_read_supabase_cache", lambda: None)

    async def empty_build():
        return SignalsGroupResponse(), frozenset()

    monkeypatch.setattr(s, "_build", empty_build)

    r = await s.get_signals()
    assert r.congress is None and r.whale is None and r.earnings is None and r.ceo is None
    # A transient triple-failure must NOT be pinned → the next request retries.
    assert ssvc._SIGNALS_CACHE_KEY not in ssvc.SignalsService._cache


@pytest.mark.asyncio
async def test_get_signals_dedups_concurrent_cold_builds(monkeypatch):
    ssvc.SignalsService._cache.clear()
    ssvc.SignalsService._inflight.clear()
    s = ssvc.SignalsService()
    monkeypatch.setattr(s, "_read_supabase_cache", lambda: None)
    monkeypatch.setattr(s, "_write_supabase_cache", lambda result: None)

    calls = {"n": 0}

    async def counting_build():
        calls["n"] += 1
        await asyncio.sleep(0.01)  # a window for the 2nd caller to join
        return SignalsGroupResponse(
            congress=SignalGroupResponse(
                kind="congress",
                entries=[SignalRowResponse(rank=1, symbol="NVDA", name="", value=3.0)],
            )
        ), frozenset()

    monkeypatch.setattr(s, "_build", counting_build)

    a, b = await asyncio.gather(s.get_signals(), s.get_signals())
    assert calls["n"] == 1  # in-flight dedup → ONE build for two concurrent opens
    assert a.congress.entries[0].symbol == "NVDA"
    assert b.congress.entries[0].symbol == "NVDA"


# ── 5. Outlier / boundary hardening (deep-review additions) ─────────────


def test_congress_includes_future_disclosure_within_2day_buffer():
    # disclosureDate can lead `now` by a day (TZ/clock skew) — the window allows up
    # to 2 days ahead, excludes 3+. With NOW=2026-06-30: 07-02 in, 07-03 out.
    senate = [
        {"symbol": "INCL", "type": "Purchase", "disclosureDate": "2026-07-02", "lastName": "A"},
        {"symbol": "INCL", "type": "Purchase", "disclosureDate": "2026-07-02", "lastName": "B"},
        {"symbol": "EXCL", "type": "Purchase", "disclosureDate": "2026-07-03", "lastName": "C"},
        {"symbol": "EXCL", "type": "Purchase", "disclosureDate": "2026-07-03", "lastName": "D"},
    ]
    g = _aggregate_congress(senate, [], now=NOW)
    assert g is not None and [r.symbol for r in g.entries] == ["INCL"]


def test_congress_past_window_boundary_30_in_31_out():
    # 30 days back (2026-05-31) included; 31 days back (2026-05-30) excluded.
    senate = [
        {"symbol": "IN", "type": "Purchase", "disclosureDate": "2026-05-31", "lastName": "A"},
        {"symbol": "IN", "type": "Purchase", "disclosureDate": "2026-05-31", "lastName": "B"},
        {"symbol": "OUT", "type": "Purchase", "disclosureDate": "2026-05-30", "lastName": "C"},
        {"symbol": "OUT", "type": "Purchase", "disclosureDate": "2026-05-30", "lastName": "D"},
    ]
    g = _aggregate_congress(senate, [], now=NOW)
    assert g is not None
    assert [r.symbol for r in g.entries] == ["IN"]
    assert g.as_of_date == "2026-05-31"


def test_congress_two_members_different_tickers_returns_none():
    # 2 distinct members, but 1 per ticker → no ticker reaches the 2-member floor.
    senate = [
        {"symbol": "AAPL", "type": "Purchase", "disclosureDate": "2026-06-25", "lastName": "M1"},
        {"symbol": "MSFT", "type": "Purchase", "disclosureDate": "2026-06-24", "lastName": "M2"},
    ]
    assert _aggregate_congress(senate, [], now=NOW) is None


def test_congress_skips_rows_with_no_member_identity():
    # A row with every identity field blank can't be attributed → dropped (not a
    # phantom member). Here that leaves NVDA with only 1 real member → below floor.
    senate = [
        {"symbol": "NVDA", "type": "Purchase", "disclosureDate": "2026-06-25",
         "lastName": "", "firstName": "", "office": "", "district": ""},
        {"symbol": "NVDA", "type": "Purchase", "disclosureDate": "2026-06-24", "lastName": "A"},
    ]
    assert _aggregate_congress(senate, [], now=NOW) is None


def test_congress_as_of_is_global_max_not_top_ticker_date():
    # Top-ranked ticker (by member count) is NOT the one with the latest disclosure.
    senate = [
        {"symbol": "NVDA", "type": "Purchase", "disclosureDate": "2026-06-25", "lastName": "A"},
        {"symbol": "NVDA", "type": "Purchase", "disclosureDate": "2026-06-24", "lastName": "B"},
        {"symbol": "NVDA", "type": "Purchase", "disclosureDate": "2026-06-23", "lastName": "C"},
        {"symbol": "ZZZ", "type": "Purchase", "disclosureDate": "2026-06-28", "lastName": "D"},
        {"symbol": "ZZZ", "type": "Purchase", "disclosureDate": "2026-06-28", "lastName": "E"},
    ]
    g = _aggregate_congress(senate, [], now=NOW)
    assert g is not None
    assert g.entries[0].symbol == "NVDA"       # ranked by member count
    assert g.as_of_date == "2026-06-28"        # global max across ALL tickers, not NVDA's


def test_earnings_drops_foreign_listings():
    cal = [
        {"symbol": "AAPL", "epsActual": 1.2, "epsEstimated": 1.0, "date": "2026-06-27"},     # +20% US
        {"symbol": "ZOO.L", "epsActual": 2.0, "epsEstimated": 1.0, "date": "2026-06-27"},    # London → dropped
        {"symbol": "005930.KS", "epsActual": 3.0, "epsEstimated": 1.0, "date": "2026-06-27"},# Korea → dropped
    ]
    g = _aggregate_earnings(cal)
    assert g is not None and [r.symbol for r in g.entries] == ["AAPL"]


def test_earnings_dedup_keeps_freshest_report_and_as_of():
    # Same symbol reports 3× in the window: keep the MOST-RECENT report (freshest-first),
    # and as_of is that latest date — NOT the largest-magnitude one.
    cal = [
        {"symbol": "D", "epsActual": 1.15, "epsEstimated": 1.0, "date": "2026-06-20"},  # +15%
        {"symbol": "D", "epsActual": 0.60, "epsEstimated": 1.0, "date": "2026-06-27"},  # -40% (bigger, older)
        {"symbol": "D", "epsActual": 1.30, "epsEstimated": 1.0, "date": "2026-06-28"},  # +30% (latest → kept)
    ]
    g = _aggregate_earnings(cal)
    assert g is not None and len(g.entries) == 1
    assert g.entries[0].value == 30.0      # freshest report kept, not the largest |surprise|
    assert g.as_of_date == "2026-06-28"


def test_earnings_all_missing_dates_yields_none_as_of():
    cal = [
        {"symbol": "A", "epsActual": 1.5, "epsEstimated": 1.0},   # +50%, no date
        {"symbol": "B", "epsActual": 2.0, "epsEstimated": 1.0},   # +100%, no date
    ]
    g = _aggregate_earnings(cal)
    assert g is not None and len(g.entries) == 2
    assert g.entries[0].symbol == "B"      # +100% ranks first
    assert g.as_of_date is None


def test_aggregators_skip_malformed_non_dict_rows():
    senate = [
        "not-a-dict", None, 42,
        {"symbol": "NVDA", "type": "Purchase", "disclosureDate": "2026-06-25", "lastName": "A"},
        {"symbol": "NVDA", "type": "Purchase", "disclosureDate": "2026-06-24", "lastName": "B"},
    ]
    cg = _aggregate_congress(senate, [], now=NOW)
    assert cg is not None and cg.entries[0].value == 2.0

    wh = _aggregate_whale(_whale_adds(
        ["x", None, {"cik": "C0"}, {"id": "w1", "cik": "C1"}, {"id": "w2", "cik": "C2"}],
        ["x", None, {"id": "g-bad", "whale_id": "w1", "date": "not-a-date"},
         _group("w1"), _group("w2"), _group("ghost")],
        ["x", None, {}, _trade("w1", "NVDA"), _trade("w2", "NVDA"), _trade("w1", "--"),
         _trade("w2", "")],
        now=NOW,
    ))
    assert wh is not None and [(e.symbol, e.value) for e in wh.entries] == [("NVDA", 2.0)]

    ea = _aggregate_earnings(
        ["x", None,
         {"symbol": "AAPL", "epsActual": 1.5, "epsEstimated": 1.0, "date": "2026-06-27"},
         {"symbol": "MSFT", "epsActual": 1.3, "epsEstimated": 1.0, "date": "2026-06-27"}]
    )
    assert ea is not None and {r.symbol for r in ea.entries} == {"AAPL", "MSFT"}


class _FakeEarningsFMP:
    """Minimal FMP stub for _build_earnings: a DATE-AWARE calendar (answers only the rows
    dated inside the requested span, as the real endpoint does) + batch quotes. Every
    calendar call is recorded so the one-day-per-call contract can be asserted."""

    def __init__(self, calendar, quotes):
        self._calendar = calendar
        self._quotes = quotes  # {symbol: {"symbol":..., "marketCap":...}}
        self.calendar_calls = []

    async def get_earnings_calendar(self, from_date, to_date):
        self.calendar_calls.append((from_date, to_date))
        return [
            r for r in self._calendar
            if isinstance(r, dict) and from_date <= str(r.get("date") or "")[:10] <= to_date
        ]

    async def get_batch_quotes_bulk(self, symbols):
        return [self._quotes[s] for s in symbols if s in self._quotes]


@pytest.mark.asyncio
async def test_build_earnings_applies_exchange_and_market_cap_gate():
    cal = [
        {"symbol": "OTCBIG", "epsActual": 1.8, "epsEstimated": 1.0, "date": "2026-06-28"}, # +80%, OTC → dropped
        {"symbol": "BIG", "epsActual": 1.5, "epsEstimated": 1.0, "date": "2026-06-27"},    # +50%, NYSE large cap
        {"symbol": "TINY", "epsActual": 2.0, "epsEstimated": 1.0, "date": "2026-06-26"},   # +100%, micro cap
        {"symbol": "ZOO.L", "epsActual": 3.0, "epsEstimated": 1.0, "date": "2026-06-27"},  # foreign → dropped pre-quote
    ]
    quotes = {
        # OTCBIG clears the $250M cap but is OTC → the new exchange gate drops it.
        "OTCBIG": {"symbol": "OTCBIG", "exchange": "OTC", "marketCap": 5_000_000_000, "name": "OTC Big Corp"},
        "BIG": {"symbol": "BIG", "exchange": "NYSE", "marketCap": 5_000_000_000, "name": "Big Industries, Inc."},
        "TINY": {"symbol": "TINY", "exchange": "NASDAQ", "marketCap": 50_000_000, "name": "Tiny Co"},  # below floor
    }
    s = ssvc.SignalsService()
    s.fmp = _FakeEarningsFMP(cal, quotes)  # type: ignore[assignment]
    s.price = PriceFromFMPFake(s.fmp)
    g = await s._build_earnings(now=NOW)
    assert g is not None
    # ZOO.L never reaches quotes (foreign); OTCBIG dropped by the exchange gate; TINY by the floor.
    assert [r.symbol for r in g.entries] == ["BIG"]
    assert g.entries[0].value == 50.0
    assert g.entries[0].name == "Big Industries, Inc."   # name now populated from the quote


@pytest.mark.asyncio
async def test_build_earnings_none_when_no_candidate_clears_floor():
    cal = [{"symbol": "TINY", "epsActual": 2.0, "epsEstimated": 1.0, "date": "2026-06-27"}]  # +100%
    quotes = {"TINY": {"symbol": "TINY", "marketCap": 10_000_000}}
    s = ssvc.SignalsService()
    s.fmp = _FakeEarningsFMP(cal, quotes)  # type: ignore[assignment]
    s.price = PriceFromFMPFake(s.fmp)
    assert await s._build_earnings(now=NOW) is None


# ── Earnings: a quote OUTAGE is a failure, not an honest empty ─────────
#
# `_build` persists a `None` branch to the 24 h `signals_cache` tier as "nothing
# qualified", but keeps a RAISED branch in memory for 5 min only. So when the batch
# quote returns nothing for every candidate, `_build_earnings` must raise — returning
# None would hide Earnings Shockers for up to a day after the quote source recovers.


class _CountingEarningsFMP(_FakeEarningsFMP):
    def __init__(self, calendar, quotes, raw_quotes=None):
        super().__init__(calendar, quotes)
        self._raw_quotes = raw_quotes
        self.quote_calls = 0

    async def get_batch_quotes_bulk(self, symbols):
        self.quote_calls += 1
        if self._raw_quotes is not None:
            return self._raw_quotes
        return await super().get_batch_quotes_bulk(symbols)


def _earnings_svc(fmp):
    s = ssvc.SignalsService()
    s.fmp = fmp  # type: ignore[assignment]
    s.price = PriceFromFMPFake(s.fmp)
    return s


_SHOCKER_CAL = [
    {"symbol": "BIG", "epsActual": 1.5, "epsEstimated": 1.0, "date": "2026-06-27"},   # +50%
    {"symbol": "HUGE", "epsActual": 3.0, "epsEstimated": 1.0, "date": "2026-06-26"},  # +200%
]


@pytest.mark.asyncio
async def test_build_earnings_zero_quotes_is_an_outage_not_an_empty_card():
    s = _earnings_svc(_CountingEarningsFMP(_SHOCKER_CAL, quotes={}))
    with pytest.raises(ssvc.FMPUnavailableException, match="no quotes returned for 2 candidate"):
        await s._build_earnings(now=NOW)


@pytest.mark.asyncio
async def test_build_earnings_only_malformed_quote_rows_is_an_outage():
    # Rows with no symbol (or not a dict) can't be mapped to any candidate — still
    # "not one usable quote came back", so still a failure rather than an honest None.
    fmp = _CountingEarningsFMP(_SHOCKER_CAL, quotes={}, raw_quotes=[None, "x", {"price": 10.0}, {"symbol": ""}])
    with pytest.raises(ssvc.FMPUnavailableException):
        await _earnings_svc(fmp)._build_earnings(now=NOW)


@pytest.mark.asyncio
async def test_build_earnings_no_candidates_is_honest_none_without_quoting():
    # An empty calendar (or nothing past the surprise threshold) is the honest empty:
    # it must stay None and must not reach the quote call, let alone the outage raise.
    for cal in ([], [{"symbol": "FLAT", "epsActual": 1.01, "epsEstimated": 1.0, "date": "2026-06-27"}]):
        fmp = _CountingEarningsFMP(cal, quotes={})
        assert await _earnings_svc(fmp)._build_earnings(now=NOW) is None
        assert fmp.quote_calls == 0


@pytest.mark.asyncio
async def test_build_earnings_partial_quotes_that_fail_the_gate_is_honest_none():
    # SOME quotes came back and none clears the floor → the honest "no shocker" None,
    # NOT the outage raise. The raise is reserved for zero usable quotes.
    quotes = {"BIG": {"symbol": "BIG", "exchange": "NYSE", "marketCap": 10_000_000}}  # sub-floor; HUGE unquoted
    assert await _earnings_svc(_CountingEarningsFMP(_SHOCKER_CAL, quotes))._build_earnings(now=NOW) is None


@pytest.mark.asyncio
async def test_earnings_quote_outage_degrades_the_build_and_is_never_persisted(monkeypatch):
    ssvc.SignalsService._cache.clear()
    ssvc.SignalsService._inflight.clear()
    ssvc.SignalsService._degraded_keys.clear()
    try:
        s = _earnings_svc(_CountingEarningsFMP(_SHOCKER_CAL, quotes={}))
        writes = []
        monkeypatch.setattr(s, "_read_supabase_cache", lambda: None)
        monkeypatch.setattr(s, "_write_supabase_cache", lambda r: writes.append(r))

        async def congress_ok():
            return SignalGroupResponse(
                kind="congress",
                entries=[SignalRowResponse(rank=1, symbol="NVDA", name="", value=3.0)],
            )

        async def none_():
            return None

        s._build_congress, s._build_whale, s._build_ceo = congress_ok, none_, none_  # type: ignore
        # _build_earnings is the REAL method, fed an empty quote batch — only its clock is
        # pinned, so the 2026-06 calendar rows sit inside its ET week.
        monkeypatch.setattr(
            s, "_build_earnings", functools.partial(ssvc.SignalsService._build_earnings, s, now=NOW)
        )

        result, failed = await s._build()
        assert failed == frozenset({"earnings"}) and result.earnings is None

        r = await s.get_signals()
        assert r.congress is not None and r.earnings is None
        assert writes == [], "a quote outage must not be pinned to the 24 h tier"
        assert ssvc._SIGNALS_CACHE_KEY in ssvc.SignalsService._degraded_keys
    finally:
        ssvc.SignalsService._cache.clear()
        ssvc.SignalsService._inflight.clear()
        ssvc.SignalsService._degraded_keys.clear()


# ── Earnings: one ET day per calendar call (2026-10-09) ────────────────
#
# FMP cuts an `earnings-calendar` answer at 4,000 rows and keeps the NEWEST dates, so the
# old single D-7..D request silently lost the oldest days of the week in peak season. The
# card now asks for one ET day per call through `earnings_window_service.fetch_calendar_days`
# (all-or-nothing): a failed or malformed day fails the card (never persisted), and a row
# is only ever read under its own date.

_WEEK = [f"2026-06-{d:02d}" for d in range(23, 31)]   # NOW (06-30 12:00 UTC) is 06-30 in ET


def _big_quote(sym, name=None, exchange="NYSE"):
    return {"symbol": sym, "exchange": exchange, "marketCap": 5_000_000_000,
            "name": name or f"{sym} Corp"}


@pytest.mark.asyncio
async def test_build_earnings_asks_one_et_day_per_call():
    fmp = _FakeEarningsFMP([], quotes={})
    assert await _earnings_svc(fmp)._build_earnings(now=NOW) is None
    assert sorted(fmp.calendar_calls) == [(d, d) for d in _WEEK]
    # ET, not UTC: 02:00 UTC on 07-01 is still 06-30 in New York.
    late = _FakeEarningsFMP([], quotes={})
    await _earnings_svc(late)._build_earnings(now=datetime(2026, 7, 1, 2, 0, tzinfo=timezone.utc))
    assert sorted(late.calendar_calls) == [(d, d) for d in _WEEK]


class _LenientEarningsFMP(_FakeEarningsFMP):
    """An upstream that ignores the date span and answers the whole calendar every time."""

    async def get_earnings_calendar(self, from_date, to_date):
        self.calendar_calls.append((from_date, to_date))
        return list(self._calendar)


@pytest.mark.asyncio
async def test_build_earnings_reads_rows_only_under_their_own_day():
    cal = [
        {"symbol": "BIG", "epsActual": 1.5, "epsEstimated": 1.0, "date": "2026-06-27"},   # +50%, in the week
        {"symbol": "OLD", "epsActual": 9.0, "epsEstimated": 1.0, "date": "2026-06-01"},   # +800%, NOT this week
    ]
    fmp = _LenientEarningsFMP(cal, {"BIG": _big_quote("BIG"), "OLD": _big_quote("OLD")})
    g = await _earnings_svc(fmp)._build_earnings(now=NOW)
    # Eight calls each answered with BOTH rows: BIG is read once (under 06-27), OLD never.
    assert [(r.symbol, r.value) for r in g.entries] == [("BIG", 50.0)]


class _TruncatingEarningsFMP(_FakeEarningsFMP):
    """FMP's silent cap, scaled down: an answer longer than `cap` keeps the NEWEST rows."""

    def __init__(self, calendar, quotes, cap):
        super().__init__(calendar, quotes)
        self._cap = cap

    async def get_earnings_calendar(self, from_date, to_date):
        rows = await super().get_earnings_calendar(from_date, to_date)
        rows.sort(key=lambda r: r["date"])
        return rows[-self._cap:]


@pytest.mark.asyncio
async def test_a_busy_week_no_longer_loses_its_oldest_day():
    # Three reports a day; the cap (5) is far below a week (24) but above any one day (3).
    cal = [
        {"symbol": f"S{d[-2:]}{i}", "epsActual": 1.0, "epsEstimated": 1.0, "date": d}
        for d in _WEEK for i in range(3)
    ]
    cal[0] = {"symbol": "MONDAYSHOCK", "epsActual": 3.0, "epsEstimated": 1.0, "date": _WEEK[0]}  # +200%
    fmp = _TruncatingEarningsFMP(cal, {"MONDAYSHOCK": _big_quote("MONDAYSHOCK")}, cap=5)
    # The old single-window request would have kept only the newest 5 rows (06-29/06-30).
    assert all(r["date"] >= "2026-06-29" for r in await fmp.get_earnings_calendar(_WEEK[0], _WEEK[-1]))
    g = await _earnings_svc(fmp)._build_earnings(now=NOW)
    assert g is not None and [r.symbol for r in g.entries] == ["MONDAYSHOCK"]


class _FailingDayFMP(_FakeEarningsFMP):
    def __init__(self, calendar, quotes, bad_day, answer):
        super().__init__(calendar, quotes)
        self._bad_day, self._answer = bad_day, answer

    async def get_earnings_calendar(self, from_date, to_date):
        if from_date == self._bad_day:
            self.calendar_calls.append((from_date, to_date))
            if isinstance(self._answer, BaseException):
                raise self._answer
            return self._answer
        return await super().get_earnings_calendar(from_date, to_date)


@pytest.mark.asyncio
async def test_one_failed_day_fails_the_card_rather_than_publishing_a_short_week():
    fmp = _FailingDayFMP(_SHOCKER_CAL, {"BIG": _big_quote("BIG")}, "2026-06-24",
                         ssvc.FMPUnavailableException("FMP 503"))
    with pytest.raises(ssvc.FMPUnavailableException, match="FMP 503"):
        await _earnings_svc(fmp)._build_earnings(now=NOW)


@pytest.mark.asyncio
async def test_a_malformed_day_fails_the_card_instead_of_reading_as_empty():
    # An FMP error payload arrives as a 200 dict. It used to read as "no shockers" and be
    # pinned to the 24 h tier; it is a failure.
    fmp = _FailingDayFMP(_SHOCKER_CAL, {"BIG": _big_quote("BIG")}, "2026-06-30",
                         {"Error Message": "Limit Reach"})
    with pytest.raises(TypeError, match="2026-06-30"):
        await _earnings_svc(fmp)._build_earnings(now=NOW)


class _SlowDayFMP(_FakeEarningsFMP):
    async def get_earnings_calendar(self, from_date, to_date):
        if from_date == "2026-06-26":
            await asyncio.sleep(5)
        return await super().get_earnings_calendar(from_date, to_date)


@pytest.mark.asyncio
async def test_a_stalled_calendar_is_a_typed_failure(monkeypatch):
    monkeypatch.setattr(ssvc, "_EARNINGS_FETCH_DEADLINE_SECONDS", 0.05)
    with pytest.raises(ssvc.FMPUnavailableException, match=r"2026-06-23\.\.2026-06-30 .*not fetched within"):
        await _earnings_svc(_SlowDayFMP(_SHOCKER_CAL, {}))._build_earnings(now=NOW)


@pytest.mark.asyncio
async def test_a_day_at_the_row_cap_is_logged_as_truncated_and_still_builds(caplog):
    from app.services import earnings_window_service as ews

    filler = [{"symbol": f"F{i}", "epsActual": 1.0, "epsEstimated": 1.0, "date": "2026-06-29"}
              for i in range(ews._TRUNCATION_ROWS - 1)]
    cal = filler + [{"symbol": "BIG", "epsActual": 1.5, "epsEstimated": 1.0, "date": "2026-06-29"}]
    import logging
    with caplog.at_level(logging.ERROR, logger="app.services.earnings_window_service"):
        g = await _earnings_svc(_FakeEarningsFMP(cal, {"BIG": _big_quote("BIG")}))._build_earnings(now=NOW)
    assert [r.symbol for r in g.entries] == ["BIG"]
    assert any("TRUNCATED" in r.getMessage() and "2026-06-29" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_a_warrant_line_never_ranks_but_its_parent_does(caplog):
    # The live 2026-10-09 shape: FMP copies the issuer's EPS onto its warrant, the warrant's
    # quote clears the exchange + $250M gate, and only the NAME / fifth letter give it away.
    cal = [
        {"symbol": "RZLV", "epsActual": -0.0564, "epsEstimated": 0.2, "date": "2026-06-29"},
        {"symbol": "RZLVW", "epsActual": -0.0564, "epsEstimated": 0.2, "date": "2026-06-29"},
        {"symbol": "ABCD-WT", "epsActual": 2.0, "epsEstimated": 1.0, "date": "2026-06-29"},   # dropped pre-quote
    ]
    quotes = {
        "RZLV": _big_quote("RZLV", "Rezolve AI Limited Ordinary Shares", "NASDAQ"),
        "RZLVW": _big_quote("RZLVW", "Rezolve AI Limited Warrants", "NASDAQ"),
        "ABCD-WT": _big_quote("ABCD-WT"),
    }
    fmp = _CountingEarningsFMP(cal, quotes)
    import logging
    with caplog.at_level(logging.INFO, logger="app.services.signals_service"):
        g = await _earnings_svc(fmp)._build_earnings(now=NOW)
    assert [(r.symbol, r.value) for r in g.entries] == [("RZLV", -128.2)]
    text = " ".join(r.getMessage() for r in caplog.records)
    assert "RZLVW (Rezolve AI Limited Warrants)" in text and "ABCD-WT" in text


@pytest.mark.asyncio
async def test_a_digit_shifted_actual_never_reaches_the_card():
    cal = [
        {"symbol": "GLITCH", "epsActual": 0.169, "epsEstimated": 1.70, "date": "2026-06-30"},  # "-90%"
        {"symbol": "BIG", "epsActual": 1.5, "epsEstimated": 1.0, "date": "2026-06-29"},
    ]
    fmp = _CountingEarningsFMP(cal, {"GLITCH": _big_quote("GLITCH"), "BIG": _big_quote("BIG")})
    g = await _earnings_svc(fmp)._build_earnings(now=NOW)
    assert [r.symbol for r in g.entries] == ["BIG"]
