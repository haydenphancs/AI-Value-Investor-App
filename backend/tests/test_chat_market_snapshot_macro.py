"""The market snapshot's macro leg: official FRED readings, each dated, public domain only.

What must hold:
  * every reading has a label, a value, a unit, an as_of and a source; CPI and core PCE are
    year-on-year; a negative spread keeps its sign;
  * an unusable or failed series is OMITTED and named under `unavailable` — never 0;
  * a slow series past the bound is named, not awaited, and keeps running (it warms the
    cache) — the leg never cancels it;
  * FRED unconfigured / everything failing → the block says so (and the VIX note);
  * the rest of the snapshot is byte-for-byte what it was;
  * the allowlist holds: no ICE BofA series, no VIX series, no vendor names.
Hermetic: a fake FRED client; the breadth legs are stubbed.
"""

from __future__ import annotations

import asyncio
import json
import math
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.integrations.fred import FREDObservation, FREDSeriesSnapshot
from app.services import chat_market_tools as cmt


def _snap(series, latest, as_of="2026-09-01", yoy=None):
    return FREDSeriesSnapshot(series_id=series, latest=latest, as_of=as_of, yoy_pct=yoy)


def _months_back(year, month, k):
    idx = year * 12 + (month - 1) - k
    return f"{idx // 12:04d}-{idx % 12 + 1:02d}-01"


def _monthly(latest, prior, *, year=2026, month=8, n=14, missing=(), overrides=None):
    """Newest-first monthly observations as the macro client returns them ('.' rows already
    dropped): `latest` at the newest month, `prior` at exactly one year before, a straight
    line between and before. `missing` = month offsets left out; `overrides` = {offset: v}."""
    rows = []
    for k in range(n):
        if k in missing:
            continue
        value = latest + (prior - latest) * k / 12
        if overrides and k in overrides:
            value = overrides[k]
        rows.append(FREDObservation(date=_months_back(year, month, k), value=value))
    return rows


# CPI +2.94% y/y, core PCE −0.26% y/y, both for August 2026.
_CPI_PRIOR = 323.364 / 1.0294
_PCE_PRIOR = 128.1 / (1 - 0.0026)
_GOOD_OBS = {
    "CPIAUCSL": _monthly(323.364, _CPI_PRIOR),
    "PCEPILFE": _monthly(128.1, _PCE_PRIOR),
}

_GOOD = {
    "FEDFUNDS": _snap("FEDFUNDS", 4.33, "2026-09-01"),
    "DGS10": _snap("DGS10", 4.118, "2026-10-06"),
    "T10Y2Y": _snap("T10Y2Y", -0.354, "2026-10-06"),
    "UNRATE": _snap("UNRATE", 4.3, "2026-09-01"),
    # A positional yoy_pct the leg must NEVER read for a year-on-year series.
    "CPIAUCSL": _snap("CPIAUCSL", 323.364, "2026-08-01", yoy=99.0),
    "PCEPILFE": _snap("PCEPILFE", 128.1, "2026-08-01", yoy=99.0),
    "DEXUSEU": _snap("DEXUSEU", 1.17123, "2026-10-03"),
    "DEXJPUS": _snap("DEXJPUS", 147.335, "2026-10-03"),
    "DEXUSUK": _snap("DEXUSUK", 1.34561, "2026-10-03"),
    "DTWEXBGS": _snap("DTWEXBGS", 120.456, "2026-10-03"),
}
_YOY_SERIES = {s[0] for s in cmt._MACRO_SERIES if s[4]}


class _FakeFRED:
    def __init__(self, snaps=None, configured=True):
        self.snaps = dict(_GOOD if snaps is None else snaps)
        self.obs = {k: list(v) for k, v in _GOOD_OBS.items()}
        self.is_configured = configured
        self.calls = []
        self.snapshot_calls = []
        self.obs_limits = {}
        self.slow = set()
        self.finished = set()
        self.gate = asyncio.Event()

    async def get_snapshot(self, series):
        self.calls.append(series)
        self.snapshot_calls.append(series)
        if series in self.slow:
            await self.gate.wait()
        value = self.snaps.get(series)
        if isinstance(value, BaseException):
            raise value
        self.finished.add(series)
        return value

    async def get_observations(self, series, *, limit=13):
        self.calls.append(series)
        self.obs_limits[series] = limit
        if series in self.slow:
            await self.gate.wait()
        value = self.obs.get(series, [])
        if isinstance(value, BaseException):
            raise value
        self.finished.add(series)
        return value


@pytest.fixture
def fred(monkeypatch):
    fake = _FakeFRED()
    monkeypatch.setattr("app.integrations.fred.get_fred_client", lambda: fake)
    yield fake
    fake.gate.set()


def _by_label(block):
    return {r["label"]: r for r in block["readings"]}


# ── the block itself ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_every_series_is_a_dated_labelled_reading_in_table_order(fred):
    block = await cmt._fetch_macro_block()
    assert [r["label"] for r in block["readings"]] == [s[1] for s in cmt._MACRO_SERIES]
    for r in block["readings"]:
        assert set(r) == {"label", "value", "unit", "as_of"}
        assert isinstance(r["value"], float) and math.isfinite(r["value"])
        assert len(r["as_of"]) == 10
    rows = _by_label(block)
    assert rows["US CPI inflation, monthly"]["value"] == 2.9
    assert rows["US CPI inflation, monthly"]["unit"] == "% year-on-year"
    assert rows["US core PCE inflation, monthly"]["value"] == -0.3, \
        "a negative year-on-year keeps its sign"
    assert rows["10-year minus 2-year Treasury spread"]["value"] == -0.35, \
        "an inverted curve keeps its sign"
    assert rows["Euro"]["value"] == 1.1712
    assert rows["Euro"]["unit"] == "US dollars per euro"
    assert rows["Japanese yen"]["unit"] == "yen per US dollar"
    assert "monthly average" in next(r for r in block["readings"]
                                     if r["label"].startswith("Effective federal funds rate"))["label"]
    assert "unavailable" not in block
    assert "VIX is not in Caydex data" in block["note"]
    assert set(fred.calls) == {s[0] for s in cmt._MACRO_SERIES}
    assert not _YOY_SERIES & set(fred.snapshot_calls), \
        "a year-on-year series never reads the client's positional yoy_pct"
    assert fred.obs_limits == {sid: cmt._YOY_OBS_LIMIT for sid in _YOY_SERIES}


@pytest.mark.asyncio
@pytest.mark.parametrize("series,snap", [
    ("DGS10", _snap("DGS10", float("inf"), "2026-10-06")),
    ("DGS10", _snap("DGS10", float("nan"), "2026-10-06")),
    ("DGS10", _snap("DGS10", True, "2026-10-06")),
    ("DEXUSEU", _snap("DEXUSEU", 0.0, "2026-10-03")),
    ("DEXJPUS", _snap("DEXJPUS", -147.0, "2026-10-03")),
    ("UNRATE", _snap("UNRATE", 0.0, "2026-09-01")),
    ("FEDFUNDS", _snap("FEDFUNDS", 4.33, "")),
    ("FEDFUNDS", _snap("FEDFUNDS", 4.33, "2026-13-01")),
    ("FEDFUNDS", None),
])
async def test_an_unusable_series_is_omitted_and_named_never_zero(fred, series, snap):
    fred.snaps[series] = snap
    block = await cmt._fetch_macro_block()
    label = next(s[1] for s in cmt._MACRO_SERIES if s[0] == series)
    assert label not in _by_label(block)
    assert label in block["unavailable"]
    assert "never estimate" in block["unavailable_note"]
    assert len(block["readings"]) == len(cmt._MACRO_SERIES) - 1


@pytest.mark.asyncio
@pytest.mark.parametrize("series,obs", [
    ("CPIAUCSL", []),                                                  # the client's failure
    ("CPIAUCSL", _monthly(323.364, _CPI_PRIOR, missing={12})),         # no anniversary row
    ("CPIAUCSL", _monthly(323.364, _CPI_PRIOR, n=12)),                 # under a year of rows
    ("PCEPILFE", _monthly(128.1, _PCE_PRIOR, overrides={12: float("nan")})),
    ("PCEPILFE", _monthly(128.1, _PCE_PRIOR, overrides={12: 0.0})),
    ("PCEPILFE", _monthly(128.1, _PCE_PRIOR, overrides={12: -128.0})),
    ("PCEPILFE", _monthly(128.1, _PCE_PRIOR, overrides={12: True})),
    ("CPIAUCSL", RuntimeError("macro source down")),
])
async def test_an_unusable_year_on_year_series_is_omitted_and_named(fred, series, obs):
    fred.obs[series] = obs
    block = await cmt._fetch_macro_block()
    label = next(s[1] for s in cmt._MACRO_SERIES if s[0] == series)
    assert label not in _by_label(block)
    assert label in block["unavailable"]
    assert len(block["readings"]) == len(cmt._MACRO_SERIES) - 1


@pytest.mark.asyncio
async def test_a_missing_month_never_turns_year_on_year_into_thirteen_months(fred):
    """October 2025 CPI was never published: the client drops that '.' row, so its 13th row
    of the newest 14 is July 2025 and its positional `yoy_pct` covers 13 months. The leg
    finds August 2025 BY DATE."""
    aug_2025 = 314.0
    jul_2025 = 300.0
    rows = _monthly(323.364, aug_2025, missing={10}, overrides={13: jul_2025})
    assert rows[12].date == "2025-07-01", "the positional row is a year and a month back"
    fred.obs["CPIAUCSL"] = rows
    block = await cmt._fetch_macro_block()
    cpi = _by_label(block)["US CPI inflation, monthly"]
    assert cpi["value"] == round((323.364 - aug_2025) / aug_2025 * 100, 1) == 3.0
    assert cpi["as_of"] == "2026-08-01"
    assert cpi["value"] != round((323.364 - jul_2025) / jul_2025 * 100, 1)


def test_yoy_by_date_is_by_date_and_degrades_to_none():
    exact = cmt._yoy_by_date(_monthly(110.0, 100.0))
    assert exact.as_of == "2026-08-01" and math.isclose(exact.yoy_pct, 10.0)
    # Unsorted and duplicated rows read the same.
    rows = _monthly(110.0, 100.0)
    shuffled = list(reversed(rows)) + rows[:3]
    assert math.isclose(cmt._yoy_by_date(shuffled).yoy_pct, 10.0)
    # A newest row with two different values is ambiguous: dropped, the month before stands
    # in, dated as itself (and its own anniversary is read).
    clash = [FREDObservation(date="2026-08-01", value=999.0)] + rows
    stood_in = cmt._yoy_by_date(clash)
    assert stood_in.as_of == "2026-07-01"
    # 1e15-scale levels are still finite arithmetic.
    huge = cmt._yoy_by_date(_monthly(2e15, 1e15))
    assert math.isclose(huge.yoy_pct, 100.0) and math.isfinite(huge.yoy_pct)
    # A negative change keeps its sign.
    assert cmt._yoy_by_date(_monthly(95.0, 100.0)).yoy_pct < 0
    for bad in (None, [], "rows", 7, [object()], [SimpleNamespace(date=None, value=1.0)],
                [FREDObservation(date="2026-13-01", value=1.0)],
                [FREDObservation(date="2026-08-01", value=float("inf"))],
                [FREDObservation(date="2026-08-01", value=110.0)]):
        assert cmt._yoy_by_date(bad) is None, bad
    # A daily series on 29 February has no same-day anniversary — no reading, never a guess.
    leap = [FREDObservation(date="2028-02-29", value=110.0),
            FREDObservation(date="2027-02-28", value=100.0)]
    assert cmt._yoy_by_date(leap) is None


@pytest.mark.asyncio
async def test_a_genuine_zero_rate_is_a_reading(fred):
    """A zero-bound policy rate is a fact, unlike a zero exchange rate."""
    fred.snaps["FEDFUNDS"] = _snap("FEDFUNDS", 0.0, "2026-09-01")
    block = await cmt._fetch_macro_block()
    assert _by_label(block)["Effective federal funds rate, monthly average"]["value"] == 0.0


@pytest.mark.asyncio
async def test_a_raising_series_is_omitted_and_logged(fred, caplog):
    fred.snaps["DGS10"] = RuntimeError("boom")
    with caplog.at_level("WARNING", logger="app.services.chat_market_tools"):
        block = await cmt._fetch_macro_block()
    assert "10-year Treasury yield" in block["unavailable"]
    assert any("DGS10" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_a_slow_series_is_named_not_awaited_and_never_cancelled(fred, monkeypatch):
    monkeypatch.setattr(cmt, "_MACRO_WAIT_SECONDS", 0.05)
    fred.slow = {"DTWEXBGS", "CPIAUCSL"}
    started = asyncio.get_running_loop().time()
    block = await cmt._fetch_macro_block()
    assert asyncio.get_running_loop().time() - started < 1.0
    labels = {s[0]: s[1] for s in cmt._MACRO_SERIES}
    assert labels["DTWEXBGS"] in block["unavailable"]
    assert labels["CPIAUCSL"] in block["unavailable"], "a slow year-on-year read is bounded too"
    assert len(block["readings"]) == len(cmt._MACRO_SERIES) - 2
    fred.gate.set()
    for _ in range(10):
        await asyncio.sleep(0)
    assert {"DTWEXBGS", "CPIAUCSL"} <= fred.finished, \
        "the abandoned reads finished (and warmed the cache)"


def test_the_broad_dollar_index_is_never_passed_off_as_the_dxy():
    label = next(s[1] for s in cmt._MACRO_SERIES if s[0] == "DTWEXBGS")
    assert "not the DXY" in label and "Federal Reserve" in label
    assert "nor is the DXY" in cmt._MACRO_NOTE
    assert "the broad dollar index is not the DXY" in cmt._MACRO_NOTE


@pytest.mark.asyncio
async def test_unconfigured_or_all_failed_says_so(fred, monkeypatch):
    fred.is_configured = False
    block = await cmt._fetch_macro_block()
    assert block["available"] is False and "VIX" in block["note"]
    assert "readings" not in block and fred.calls == []

    fred.is_configured = True
    fred.snaps = {s[0]: RuntimeError("down") for s in cmt._MACRO_SERIES}
    fred.obs = {sid: RuntimeError("down") for sid in _YOY_SERIES}
    block = await cmt._fetch_macro_block()
    assert block["available"] is False and "never estimate" in block["note"]


@pytest.mark.asyncio
async def test_the_client_failing_to_build_is_the_unavailable_block(monkeypatch):
    def _boom():
        raise RuntimeError("settings broken")

    monkeypatch.setattr("app.integrations.fred.get_fred_client", _boom)
    block = await cmt._fetch_macro_block()
    assert block["available"] is False


# ── inside the snapshot ───────────────────────────────────────────────────────

class _Movers:
    async def get_sector_performance(self):
        return [{"sector": "Technology", "changesPercentage": 0.8, "constituents": 300,
                 "date": "2026-10-07"}]

    async def get_industry_performance(self):
        return [{"industry": "Semiconductors", "sector": "Technology",
                 "changesPercentage": 1.9, "date": "2026-10-07"}]

    async def get_scanner_inputs(self):
        return ({}, {})


class _DeadMovers:
    async def get_sector_performance(self):
        raise RuntimeError("down")

    async def get_industry_performance(self):
        raise RuntimeError("down")

    async def get_scanner_inputs(self):
        raise RuntimeError("down")


def _stub_breadth(monkeypatch, movers):
    monkeypatch.setattr("app.services.market_movers_service.get_market_movers_service",
                        lambda: movers)
    monkeypatch.setattr("app.services.news_insight_service.get_news_insight_service",
                        lambda: SimpleNamespace(get_cards=AsyncMock(return_value={})))


@pytest.mark.asyncio
async def test_the_rest_of_the_snapshot_is_unchanged(fred, monkeypatch):
    _stub_breadth(monkeypatch, _Movers())
    with_macro = await cmt.fetch_market_snapshot()

    async def _absent():
        return {}

    monkeypatch.setattr(cmt, "_fetch_macro_block", _absent)
    without = await cmt.fetch_market_snapshot()
    assert "macro" in with_macro and "macro" not in without
    rest = {k: v for k, v in with_macro.items() if k != "macro"}
    assert rest == without
    assert with_macro["macro"]["readings"]


@pytest.mark.asyncio
async def test_macro_alone_is_not_an_outage_and_does_not_hide_the_breadth_failure(
        fred, monkeypatch):
    _stub_breadth(monkeypatch, _DeadMovers())
    out = await cmt.fetch_market_snapshot()
    assert "error" not in out and out["macro"]["readings"]
    assert "never describe the market's day as flat" in out["market_breadth_note"]
    assert "sectors" not in out


@pytest.mark.asyncio
async def test_no_breadth_and_no_macro_is_the_old_outage(fred, monkeypatch):
    _stub_breadth(monkeypatch, _DeadMovers())
    fred.is_configured = False
    out = await cmt.fetch_market_snapshot()
    assert out == {"error": "No market data could be read right now.", "upstream": True}


@pytest.mark.asyncio
async def test_a_raising_macro_leg_never_breaks_the_snapshot(monkeypatch):
    _stub_breadth(monkeypatch, _Movers())

    async def _boom():
        raise RuntimeError("macro leg exploded")

    monkeypatch.setattr(cmt, "_fetch_macro_block", _boom)
    out = await cmt.fetch_market_snapshot()
    assert out["sectors"][0]["sector"] == "Technology"
    assert out["macro"]["available"] is False


def _breadth(n_industries: int, name_len: int):
    class _Full:
        async def get_sector_performance(self):
            return [{"sector": f"Sector {i:02d}", "changesPercentage": -3.21 + i,
                     "constituents": 600, "date": "2026-10-07"} for i in range(11)]

        async def get_industry_performance(self):
            return [{"industry": f"Industry {i:03d} " + "x" * name_len,
                     "sector": "Consumer Cyclical", "changesPercentage": 9.99 - i * 0.13,
                     "date": "2026-10-07"} for i in range(n_industries)]

        async def get_scanner_inputs(self):
            return ({}, {})

    return _Full()


def _stub_full(monkeypatch, n_industries, name_len, mover_name_len, card_len=400):
    monkeypatch.setattr("app.services.market_movers_service.get_market_movers_service",
                        lambda: _breadth(n_industries, name_len))
    card = {"headline": "H" * card_len, "bullets": ["B" * card_len] * 6,
            "generated_at": "2026-10-07T21:00:00Z"}
    monkeypatch.setattr("app.services.news_insight_service.get_news_insight_service",
                        lambda: SimpleNamespace(get_cards=AsyncMock(
                            return_value={"__MARKET__": card})))
    movers = [{"symbol": f"SYM{i}", "name": "N" * mover_name_len, "change_percent": 12.34,
               "session_date": "2026-10-07"} for i in range(5)]
    monkeypatch.setattr(cmt, "_hot_tickers",
                        lambda *_a, **_k: {"top_gainers": movers, "top_losers": movers})


@pytest.mark.asyncio
async def test_the_macro_block_itself_stays_small(fred):
    block = await cmt._fetch_macro_block()
    assert len(json.dumps(block)) < 1600, len(json.dumps(block))


@pytest.mark.asyncio
async def test_a_busy_day_gives_way_from_the_smallest_industry_moves_and_says_so(
        fred, monkeypatch):
    """A busy day's breadth already approaches the 8,000-char cap; the macro readings take
    the place of the SMALLEST industry moves at the tail, counted — never the sectors, the
    ends, the movers or the market card, and never silently."""
    _stub_full(monkeypatch, n_industries=150, name_len=12, mover_name_len=22)

    async def _absent():
        return {}

    real_macro = cmt._fetch_macro_block
    monkeypatch.setattr(cmt, "_fetch_macro_block", _absent)
    before = await cmt.fetch_market_snapshot()
    monkeypatch.setattr(cmt, "_fetch_macro_block", real_macro)
    out = await cmt.fetch_market_snapshot()

    encoded = json.dumps(out, allow_nan=False)
    assert len(encoded) <= 8000 - cmt._SNAPSHOT_MARGIN, len(encoded)
    assert len(out["macro"]["readings"]) == len(cmt._MACRO_SERIES)
    for key in ("sectors", "leading_industries", "lagging_industries", "top_gainers",
                "top_losers", "market_story", "as_of_session"):
        assert out[key] == before[key], key
    kept = out.get("other_industries_that_moved", [])
    assert kept == before["other_industries_that_moved"][: len(kept)], "the head survives"
    dropped = len(before["other_industries_that_moved"]) - len(kept)
    assert dropped > 0 and f"{dropped} more industries" in out["other_industries_not_listed"]


@pytest.mark.asyncio
async def test_a_quiet_day_keeps_every_industry(fred, monkeypatch):
    _stub_full(monkeypatch, n_industries=20, name_len=12, mover_name_len=22, card_len=150)
    out = await cmt.fetch_market_snapshot()
    assert "other_industries_not_listed" not in out
    assert len(out["other_industries_that_moved"]) == 10
    assert out["macro"]["readings"]


@pytest.mark.asyncio
async def test_without_macro_readings_the_breadth_is_never_trimmed(fred, monkeypatch):
    """The fit exists only to make room for macro; an unavailable block changes nothing."""
    _stub_full(monkeypatch, n_industries=150, name_len=40, mover_name_len=60)
    fred.is_configured = False
    out = await cmt.fetch_market_snapshot()
    assert "other_industries_not_listed" not in out
    assert len(out["other_industries_that_moved"]) == cmt._INDUSTRY_REST_CAP


@pytest.mark.asyncio
async def test_under_a_pathological_breadth_the_macro_readings_survive_whole(
        fred, monkeypatch):
    """Long names everywhere: after the fit, the result fits without the generic pruner, so
    no list — the macro readings included — is capped blind."""
    from app.integrations.gemini import truncate_tool_result

    _stub_full(monkeypatch, n_industries=150, name_len=40, mover_name_len=60)
    out = await cmt.fetch_market_snapshot()
    pruned = truncate_tool_result(out)
    assert pruned is out or pruned == out, "the generic pruner had nothing to cut"
    assert len(out["macro"]["readings"]) == len(cmt._MACRO_SERIES)


# ── the allowlist ─────────────────────────────────────────────────────────────

_PUBLIC_DOMAIN = {"FEDFUNDS", "DGS10", "T10Y2Y", "UNRATE", "CPIAUCSL", "PCEPILFE",
                  "DEXUSEU", "DEXJPUS", "DEXUSUK", "DTWEXBGS"}


def test_only_public_domain_series_are_read():
    ids = {s[0] for s in cmt._MACRO_SERIES}
    assert ids == _PUBLIC_DOMAIN
    for sid in ids:
        assert not sid.startswith("BAML"), "ICE BofA series are copyrighted"
        assert "VIX" not in sid, "the VIX is Cboe's"


def test_no_vendor_is_named_in_the_model_facing_text():
    import re

    blob = json.dumps([list(s) for s in cmt._MACRO_SERIES]) + cmt._MACRO_NOTE + \
        json.dumps(cmt._MACRO_UNAVAILABLE)
    for vendor in ("FMP", "Financial Modeling", "Gemini", "Brave", "Google", "OpenAI",
                   "FRED", "St. Louis", "Cboe", "ICE", "BofA"):
        assert not re.search(rf"\b{re.escape(vendor)}\b", blob, re.IGNORECASE), vendor


def test_the_snapshot_reads_the_macro_leg_inside_its_gather():
    """Source scan (AST, docstrings and comments cannot satisfy it): the leg is awaited in
    the same gather as the breadth legs — concurrently, not after them."""
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(cmt.fetch_market_snapshot))
    gathers = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
               and isinstance(n.func, ast.Attribute) and n.func.attr == "gather"]
    assert len(gathers) == 1
    names = {a.func.id for a in gathers[0].args
             if isinstance(a, ast.Call) and isinstance(a.func, ast.Name)}
    assert "_fetch_macro_block" in names
