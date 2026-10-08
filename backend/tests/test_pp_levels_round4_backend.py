"""Profit Power names each drawn peer LINE by its own group (round 4, 2026-10-08).

`get_benchmark_series` picks industry-or-sector separately for every metric and every
period type (the industry when it is mature at that line's newest period, else the
sector). `profit_power_service` used to pool the votes of all four margins into ONE level
per tab, so:

* PP-LEVEL-1 — the report drill-down's FCF Margin tab read "Industry Average … vs
  industry" over the SECTOR median (fcf_margin's cash-flow ∩ income join is the thinnest
  sample, so it is the margin most often below the n=20 floor);
* IOS-R3-2 / R3-CONTRACT-3 — the live card, which draws ONLY the net-margin peer line,
  could label it with the other three margins' group, and Cay AI's peer sentence
  (`peer_group_level`) inherited the same word.

Now `peer_group_levels` carries "<period>.<metric>" per margin line, "annual"/"quarterly"
are the NET-margin line's level, and `peer_group_level` is the annual net line's level, else
the quarterly one's. Each test below asserts the CORRECT degraded behaviour too: a line
that draws nothing (no cell, an unusable NaN/inf/bool/str cell, no company period to hang
it on) names nothing. Hermetic: fake FMP + fake lookup, no network, no Supabase.

Mutation reasoning (each was checked by hand against a mutated copy of the service):
* revert to the pooled vote → the FCF/net-vs-others tests fail on "annual";
* drop the per-metric keys → every "annual.<metric>" assertion fails;
* drop `_drawable_cells` → the unusable-cell tests fail (a key names a line not drawn);
* leave `_PP_PAYLOAD_VERSION` at 6 → the v6-row test fails (a v6 row would be served).
"""

from __future__ import annotations

import logging
from typing import Any, Dict

import pytest

from app.schemas.profit_power import ProfitPowerDataPointSchema, ProfitPowerResponse
from app.services import profit_power_service as pp
from app.services.sector_benchmark_lookup import CALENDAR_QUARTER_PERIOD_TYPE

_PROFILE = {"symbol": "ZZZ", "sector": "Technology", "industry": "Solar"}
_MARGINS = ("net_margin", "gross_margin", "operating_margin", "fcf_margin")


class _FakeFMP:
    """Statement calls keyed by (method, period); anything unlisted answers []."""

    def __init__(self, **answers: Any) -> None:
        self._answers = answers

    def __getattr__(self, name: str):
        async def _call(*args, **kwargs):
            key = f"{name}:{kwargs['period']}" if "period" in kwargs else name
            return self._answers.get(key, self._answers.get(name, []))

        return _call


class _FakeLookup:
    """One-group-per-line cells per period type, as `get_benchmark_series` serves them."""

    def __init__(self, annual: Dict[str, Any], quarterly: Dict[str, Any] | None = None):
        self._by_type = {"annual": annual, CALENDAR_QUARTER_PERIOD_TYPE: quarterly or {}}

    def get_benchmark_series(self, industry, sector, metrics, period_type):
        src = self._by_type.get(period_type, {})
        return {m: {k: dict(v) for k, v in src.get(m, {}).items()} for m in metrics}


def _cell(value, level, n=40):
    return {"value": value, "n": n, "level": level, "peer_group_name": level}


def _line(level, values=(0.10, 0.12), labels=("2024", "2025"), n=40):
    """One metric's line: every period from ONE group (what get_benchmark_series serves)."""
    return {label: _cell(v, level, n) for label, v in zip(labels, values)}


def _annual_row(date, fy, net=10.0):
    return {"date": date, "fiscalYear": fy, "period": "FY", "revenue": 100.0,
            "grossProfit": 50.0, "operatingIncome": 20.0, "netIncome": net}


_ANNUAL_ROWS = [_annual_row("2024-12-31", "2024"), _annual_row("2025-12-31", "2025", net=12.0)]
_QUARTER_ROWS = [
    {"date": "2025-12-31", "period": "Q4", "fiscalYear": "2025", "revenue": 100.0,
     "grossProfit": 50.0, "operatingIncome": 20.0, "netIncome": 10.0},
    {"date": "2026-03-31", "period": "Q1", "fiscalYear": "2026", "revenue": 100.0,
     "grossProfit": 50.0, "operatingIncome": 20.0, "netIncome": 12.0},
]
_Q_LABELS = ("Q4'25", "Q1'26")


def _fmp(annual=None, quarterly=None) -> _FakeFMP:
    return _FakeFMP(**{
        "get_company_profile": dict(_PROFILE),
        "get_income_statement:annual": list(_ANNUAL_ROWS if annual is None else annual),
        "get_income_statement:quarter": list(_QUARTER_ROWS if quarterly is None else quarterly),
        "get_cash_flow_statement:annual": [],
        "get_cash_flow_statement:quarter": [],
        "get_earning_calendar_full": [],
    })


async def _build(monkeypatch, annual_cells, quarterly_cells=None, **fmp_kw) -> ProfitPowerResponse:
    lookup = _FakeLookup(annual_cells, quarterly_cells)
    monkeypatch.setattr(pp, "get_sector_benchmark_lookup", lambda: lookup)
    svc = pp.ProfitPowerService.__new__(pp.ProfitPowerService)
    svc.fmp = _fmp(**fmp_kw)
    svc.supabase = None
    result, _next, degraded = await svc._build_profit_power("ZZZ")
    assert degraded == [] and result.degraded == []
    return result


# ── PP-LEVEL-1: the FCF line names its OWN group ─────────────────────────────────


@pytest.mark.asyncio
async def test_a_sector_fcf_line_is_named_sector_while_the_other_margins_are_industry(monkeypatch):
    """The reviewer's repro: Solar has n=22 for net/gross/operating (industry lines) but
    n=18 for fcf_margin (sector line, Technology's median). Pooled 3:1, the FCF tab was
    labelled "Industry" over the sector's 5%."""
    annual = {
        "net_margin": _line("industry", (0.11, 0.12), n=22),
        "gross_margin": _line("industry", (0.30, 0.31), n=22),
        "operating_margin": _line("industry", (0.15, 0.16), n=22),
        "fcf_margin": _line("sector", (0.04, 0.05), n=300),
    }
    r = await _build(monkeypatch, annual)
    lv = r.peer_group_levels
    assert lv["annual.fcf_margin"] == "sector"
    assert lv["annual.net_margin"] == lv["annual.gross_margin"] == lv["annual.operating_margin"] == "industry"
    assert lv["annual"] == "industry" and r.peer_group_level == "industry"
    # The FCF line really is the sector's median on every drawn point.
    assert [p.sector_average_fcf_margin for p in r.annual] == [4.0, 5.0]
    # No quarterly cells at all → no quarterly key of any kind.
    assert not any(k == "quarterly" or k.startswith("quarterly.") for k in lv), lv


# ── IOS-R3-2 / R3-CONTRACT-3: the tab word is the NET line's, never a pooled vote ──


@pytest.mark.parametrize("net_level, other_level", [("industry", "sector"), ("sector", "industry")])
@pytest.mark.asyncio
async def test_the_tab_level_is_the_net_margin_lines_even_when_outvoted_3_to_1(
    monkeypatch, net_level, other_level,
):
    annual = {"net_margin": _line(net_level)}
    annual.update({m: _line(other_level) for m in _MARGINS if m != "net_margin"})
    quarterly = {"net_margin": _line(net_level, labels=_Q_LABELS)}
    quarterly.update({m: _line(other_level, labels=_Q_LABELS) for m in _MARGINS if m != "net_margin"})
    r = await _build(monkeypatch, annual, quarterly)
    assert r.peer_group_levels["annual"] == net_level
    assert r.peer_group_levels["quarterly"] == net_level
    assert r.peer_group_level == net_level
    for period in ("annual", "quarterly"):
        for m in _MARGINS:
            want = net_level if m == "net_margin" else other_level
            assert r.peer_group_levels[f"{period}.{m}"] == want, (period, m)


@pytest.mark.asyncio
async def test_cay_ais_peer_sentence_names_the_net_lines_group(monkeypatch):
    """Cay AI quotes the NET-margin median and labels it with `peer_group_level`: with the
    net line at industry and the other three at sector, it must say Industry."""
    from app.services.chat_service import ChatService

    annual = {"net_margin": _line("industry", (0.11, 0.123))}
    annual.update({m: _line("sector") for m in _MARGINS if m != "net_margin"})
    r = await _build(monkeypatch, annual)
    text = ChatService._format_profit_summary("ZZZ", r)
    assert "Industry peer-group median net margin 12.3%" in text, text
    assert "Sector peer-group" not in text


# ── the two tabs are named separately ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_an_industry_annual_line_and_a_sector_quarterly_line_keep_their_own_words(monkeypatch):
    """20-F/ADR filers file no quarters, so an industry can be mature on its newest year
    and thin on its newest calendar quarter: each tab names its own net line."""
    r = await _build(
        monkeypatch,
        {"net_margin": _line("industry")},
        {"net_margin": _line("sector", labels=_Q_LABELS)},
    )
    assert r.peer_group_levels == {
        "annual.net_margin": "industry", "annual": "industry",
        "quarterly.net_margin": "sector", "quarterly": "sector",
    }
    assert r.peer_group_level == "industry", "the legacy field is the ANNUAL net line's"


@pytest.mark.asyncio
async def test_with_no_annual_net_line_the_legacy_field_is_the_quarterly_net_lines(monkeypatch):
    r = await _build(
        monkeypatch,
        {"gross_margin": _line("industry")},           # annual: gross only
        {"net_margin": _line("sector", labels=_Q_LABELS)},
    )
    assert "annual" not in r.peer_group_levels, "the live card draws no annual net line"
    assert r.peer_group_levels["annual.gross_margin"] == "industry"
    assert r.peer_group_level == "sector"


@pytest.mark.asyncio
async def test_no_net_line_anywhere_names_no_tab_and_no_legacy_level(monkeypatch):
    """Only gross margin has a peer line: the drill-down's Gross tab is named, but the live
    card (net line only) and Cay AI get no word — never a guess from another margin."""
    r = await _build(monkeypatch, {"gross_margin": _line("sector")})
    assert r.peer_group_levels == {"annual.gross_margin": "sector"}
    assert r.peer_group_level is None
    assert all(p.sector_average_net_margin is None for p in r.annual)


# ── a line that draws nothing names nothing ──────────────────────────────────────


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf"), True, "0.05"])
@pytest.mark.asyncio
async def test_an_unusable_cell_neither_draws_nor_votes(monkeypatch, caplog, bad):
    """`_to_schemas` drops a NaN / inf / bool / non-numeric cell (it would 500 the response
    at JSON encoding). A line made only of such cells draws nothing, so it must carry no
    level — a key would make the drill-down name a line that is not on screen."""
    caplog.set_level(logging.WARNING, logger=pp.logger.name)
    annual = {
        "net_margin": _line("industry"),
        "fcf_margin": {"2024": _cell(bad, "sector"), "2025": _cell(bad, "sector")},
    }
    r = await _build(monkeypatch, annual)
    assert all(p.sector_average_fcf_margin is None for p in r.annual)
    assert "annual.fcf_margin" not in r.peer_group_levels, r.peer_group_levels
    assert r.peer_group_levels["annual"] == "industry"
    r.model_dump_json()  # still serialisable
    assert "unusable benchmark cell" in caplog.text, "the dropped cell must be logged"


@pytest.mark.asyncio
async def test_one_unusable_cell_does_not_stop_the_rest_of_the_line_from_naming_it(monkeypatch):
    annual = {"net_margin": {"2024": _cell(float("nan"), "industry"), "2025": _cell(0.12, "industry")}}
    r = await _build(monkeypatch, annual)
    assert [p.sector_average_net_margin for p in r.annual] == [None, 12.0]
    assert r.peer_group_levels["annual.net_margin"] == "industry"


@pytest.mark.asyncio
async def test_cells_with_no_company_period_to_hang_on_name_nothing(monkeypatch):
    """No annual income statements → no annual points → nothing drawn, whatever the cells."""
    r = await _build(monkeypatch, {m: _line("industry") for m in _MARGINS}, annual=[])
    assert r.annual == []
    assert not any(k == "annual" or k.startswith("annual.") for k in r.peer_group_levels)
    assert r.peer_group_level is None


@pytest.mark.asyncio
async def test_a_cell_without_a_declared_level_draws_but_never_guesses_a_word(monkeypatch):
    annual = {"net_margin": {"2024": {"value": 0.10, "n": 40}, "2025": {"value": 0.12, "n": 40}}}
    r = await _build(monkeypatch, annual)
    assert [p.sector_average_net_margin for p in r.annual] == [10.0, 12.0]
    assert r.peer_group_levels == {} and r.peer_group_level is None


@pytest.mark.asyncio
async def test_every_level_value_is_a_known_group_and_every_key_a_known_line(monkeypatch):
    annual = {m: _line("industry" if i % 2 else "sector") for i, m in enumerate(_MARGINS)}
    quarterly = {m: _line("sector", labels=_Q_LABELS) for m in _MARGINS}
    r = await _build(monkeypatch, annual, quarterly)
    known = {"annual", "quarterly"} | {f"{p}.{m}" for p in ("annual", "quarterly") for m in _MARGINS}
    assert set(r.peer_group_levels) == known
    assert set(r.peer_group_levels.values()) <= {"industry", "sector"}


# ── persisted payload: v7 keys round-trip, a v6 row is rebuilt ───────────────────


def test_the_payload_version_was_bumped_for_the_new_level_meaning():
    assert pp._PP_PAYLOAD_VERSION >= 7


def test_the_dotted_keys_round_trip_through_the_persisted_json():
    body = ProfitPowerResponse(
        symbol="ZZZ", quarterly=[], peer_group_level="industry",
        peer_group_levels={"annual": "industry", "annual.fcf_margin": "sector"},
        annual=[ProfitPowerDataPointSchema(period="2025", net_margin=12.0,
                                           sector_average_fcf_margin=5.0)],
    )
    again = ProfitPowerResponse.model_validate(body.model_dump(mode="json"))
    assert again.peer_group_levels == {"annual": "industry", "annual.fcf_margin": "sector"}


def _row(json_data):
    from datetime import datetime, timezone
    from types import SimpleNamespace

    class _Q:
        def select(self, *a, **k):
            return self

        def eq(self, *a, **k):
            return self

        def limit(self, *a, **k):
            return self

        def execute(self):
            return SimpleNamespace(data=[{
                "response_json": json_data,
                "cached_at": datetime.now(timezone.utc).isoformat(),
                "next_earnings_date": None,
            }])

    return SimpleNamespace(table=lambda name: _Q())


def test_a_v6_row_with_a_pooled_tab_level_is_rebuilt_not_served():
    body = ProfitPowerResponse(
        symbol="ZZZ", quarterly=[], peer_group_level="industry",
        peer_group_levels={"annual": "industry"},
        annual=[ProfitPowerDataPointSchema(period="2025", net_margin=12.0,
                                           sector_average_fcf_margin=5.0)],
    ).model_dump()
    svc = pp.ProfitPowerService.__new__(pp.ProfitPowerService)
    svc.supabase = _row({**body, "payload_version": 6})
    assert svc._check_supabase_cache("ZZZ") is None
    svc.supabase = _row({**body, "payload_version": pp._PP_PAYLOAD_VERSION})
    assert svc._check_supabase_cache("ZZZ") is not None
