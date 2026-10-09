"""The 2026-10-08 blocks of Ask Cay AI's ownership tool — outliers and degraded paths.

Everything is read from the Holders build the tool already holds (no new upstream call on
the request path; short interest only through its own cache, bounded):

  * `insider_activity` — 3/6/12-month open-market buying and selling: months in any order,
    gaps tolerated, malformed labels skipped, an unavailable insider tab "could not be
    loaded" (never zero);
  * institutional change per largest holder, other large changes, the quarter's flow;
  * `float` — ONE float source, and the insiders' percentage derived from that same figure;
  * `short_interest` — the Key Stats rule (`short_percent_of_float`), "not loaded" when the
    cache does not answer in time;
  * the foreign-issuer note, only with no Form 4 filer AND a non-US or ADR issuer;
  * 40 insiders and full side lists still fit the tool-result cap, everyone shown or named.

Hermetic: fake holders service; side reads stubbed.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
from datetime import datetime, timezone

import pytest

from app.schemas.holders import (
    HoldersResponse,
    InsiderActivitiesDataSchema,
    InsiderActivitySchema,
    InsiderActivitySummarySchema,
    InsiderHoldingSchema,
    InsiderHoldingsSchema,
    InsiderOwnerSchema,
    InsiderTradeSchema,
    InstitutionalActivitySchema,
    OwnershipDetailSchema,
    RecentActivitiesFlowSummarySchema,
    RecentActivitiesSchema,
    ShareholderBreakdownSchema,
    SmartMoneyDataSchema,
    SmartMoneyFlowDataPointSchema,
    SmartMoneyFlowSummarySchema,
    Top10OwnersSchema,
    TopInstitutionSchema,
)
from app.services import chat_ownership_tool as cot
from app.services import holders_service as hs
from app.services.holders_service import HoldersService, _float_stamps
from app.services.stock_overview_service import profile_country_fields, short_percent_of_float

_BUILT = "2026-10-08T15:00:00+00:00"
# The real side reads, captured before the autouse fixture stubs them.
_REAL_PROFILE_FLAGS = cot._issuer_profile_flags


def _owner(name="Brian M. Venturo", n_holdings=1, date="2026-09-30"):
    return InsiderOwnerSchema(
        name=name, role="director", latest_transaction_date=date, latest_filing_date=date,
        latest_trades=[InsiderTradeSchema(transaction_type="F-InKind", acquired=False,
                                          shares=12000, average_price=87.0)],
        holdings=[InsiderHoldingSchema(security="Class B Common Stock" if j else "Class A Common Stock",
                                       held="indirect" if j else "direct", shares=302526 + j,
                                       as_of=date) for j in range(n_holdings)],
    )


def _resp(*, people=None, flow=None, acts=None, summary=None, card=None, smart_unavailable=None,
          acts_unavailable=None, detail_extra=None, breakdown=None, inst_acts=None, inst_flow=None,
          built_at=_BUILT):
    detail = dict(insider_holdings=InsiderHoldingsSchema(
        insiders=people if people is not None else [_owner()]),
        institutions_quarter="Q2 2026", built_at=built_at)
    detail.update(detail_extra or {})
    return HoldersResponse(
        symbol="CRWV",
        shareholder_breakdown=breakdown or ShareholderBreakdownSchema(
            insiders_percent=41.2, institutions_percent=38.5, public_other_percent=20.3,
            institutions_source="summary",
            top_10_owners=Top10OwnersSchema(institutions=[
                TopInstitutionSchema(rank=1, name="Vanguard Group Inc", value_in_billions=2.5,
                                     percent_ownership=6.1)])),
        insider_data=SmartMoneyDataSchema(
            tab="Insider", flow_data=flow or [], summary=card or SmartMoneyFlowSummarySchema(),
            unavailable=smart_unavailable),
        recent_activities=RecentActivitiesSchema(
            insider_activities=InsiderActivitiesDataSchema(
                summary=summary or InsiderActivitySummarySchema(), activities=acts or [],
                unavailable=acts_unavailable),
            institutional_activities=inst_acts or [],
            institutional_flow_summary=inst_flow or RecentActivitiesFlowSummarySchema(),
        ),
        ownership_detail=OwnershipDetailSchema(**detail),
    )


class _Holders:
    def __init__(self, resp, degraded=()):
        self.resp, self.degraded = resp, list(degraded)

    async def get_holders_with_status(self, ticker, force_refresh=False):
        return self.resp, list(self.degraded)

    async def newer_insider_filing(self, ticker, detail):
        return False


@pytest.fixture(autouse=True)
def _hermetic(monkeypatch):
    async def _no_short(sym):
        return {}

    async def _no_profile(sym):
        return None

    monkeypatch.setattr(cot, "_load_short_interest", _no_short)
    monkeypatch.setattr(cot, "_issuer_profile_flags", _no_profile)
    monkeypatch.setattr(cot, "_side_tasks", set())   # never another test's (or loop's) tasks
    monkeypatch.setattr(cot, "_fresh_reads", {})
    monkeypatch.setattr(cot, "_fresh_views", {})
    monkeypatch.setattr(cot, "_last_probe", {})
    monkeypatch.setattr(cot, "_probe_inflight", {})


def _serve(monkeypatch, resp, degraded=()):
    fake = _Holders(resp, degraded)
    monkeypatch.setattr(hs, "get_holders_service", lambda: fake)
    return fake


def _act(name, date, shares, price, kind):
    sign = -1 if "Sell" in kind else 1
    return InsiderActivitySchema(name=name, date=date, change_in_millions=sign * shares / 1e6,
                                 transaction_type=kind, price_at_transaction=price)


# ── insider_activity ────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_windows_sum_by_month_label_in_any_order_with_gaps(monkeypatch):
    flow = [  # out of order, 09/2026 missing, a malformed label, a duplicate label
        SmartMoneyFlowDataPointSchema(month="10/2026", buy_volume=0.0, sell_volume=0.065616),
        SmartMoneyFlowDataPointSchema(month="05/2026", buy_volume=0.5),
        SmartMoneyFlowDataPointSchema(month="08/2026", buy_volume=0.001),
        SmartMoneyFlowDataPointSchema(month="13/2026", buy_volume=9.0),
        SmartMoneyFlowDataPointSchema(month="garbage", buy_volume=9.0),
        SmartMoneyFlowDataPointSchema(month="08/2026", buy_volume=0.002),
        SmartMoneyFlowDataPointSchema(month="04/2026", sell_volume=7.0),
    ]
    acts = [
        _act("Brian M. Venturo", "2026-10-02", 65616, 87.69, "Informative Sell"),
        _act("A Buyer", "2026-08-15", 3000, 10.0, "Informative Buy"),
        _act("Early Buyer", "2026-05-03", 500000, 2.0, "Informative Buy"),
        _act("Awardee", "2026-10-01", 9999, 0.0, "Uninformative Buy"),
    ]
    _serve(monkeypatch, _resp(flow=flow, acts=acts))
    block = (await cot.fetch_ownership("CRWV"))["insider_activity"]
    assert block["available"] is True and "excluded" in block["basis"]
    three = block["last_3_months"]
    assert three.startswith("last 3 months (08/2026-10/2026")
    assert "bought 3,000 shares (about $30,000)" in three
    assert "sold 65,616 shares (about $5.75 million in proceeds)" in three
    assert "1 buyer(s), 1 seller(s)" in three and "net about -$5.72 million (net selling)" in three
    six = block["last_6_months"]
    assert six.startswith("last 6 months (05/2026-10/2026")
    assert "bought 503,000 shares" in six and "2 buyer(s)" in six
    assert "7,000,000" not in six, "04/2026 is outside six months ending 10/2026"
    assert "9,000,000" not in json.dumps(block), "a malformed label never counts"


@pytest.mark.asyncio
async def test_the_twelve_months_are_the_tabs_own_summary(monkeypatch):
    summary = InsiderActivitySummarySchema(informative_buys_in_millions=0.0,
                                           informative_sells_in_millions=0.065616,
                                           num_buyers=0, num_sellers=1)
    card = SmartMoneyFlowSummarySchema(total_buy_usd_millions=0.0, total_sell_usd_millions=5.7539,
                                       net_flow_usd_millions=-5.7539)
    _serve(monkeypatch, _resp(summary=summary, card=card))
    twelve = (await cot.fetch_ownership("CRWV"))["insider_activity"]["last_12_months"]
    assert twelve.startswith("last 12 months (trailing 365 days to 2026-10-08)")
    assert "sold 65,616 shares (about $5.75 million in proceeds)" in twelve
    assert "0 buyer(s), 1 seller(s)" in twelve


@pytest.mark.asyncio
async def test_an_old_build_without_dollar_totals_states_shares_only(monkeypatch):
    summary = InsiderActivitySummarySchema(informative_buys_in_millions=0.01, num_buyers=1)
    _serve(monkeypatch, _resp(summary=summary))   # card dollars None
    twelve = (await cot.fetch_ownership("CRWV"))["insider_activity"]["last_12_months"]
    assert "bought 10,000 shares" in twelve and "$" not in twelve


@pytest.mark.asyncio
@pytest.mark.parametrize("which", ["smart", "list"])
async def test_an_unavailable_insider_tab_is_could_not_be_loaded_never_zero(monkeypatch, which):
    _serve(monkeypatch, _resp(smart_unavailable=True if which == "smart" else None,
                              acts_unavailable=True if which == "list" else None))
    block = (await cot.fetch_ownership("CRWV"))["insider_activity"]
    assert block["available"] is False and "could not be loaded" in block["note"]
    assert "last_3_months" not in block


@pytest.mark.asyncio
async def test_no_activity_and_unpriced_trades_are_said(monkeypatch):
    acts = [_act("Quiet Buyer", "2026-10-01", 100, 0.0, "Informative Buy")]
    flow = [SmartMoneyFlowDataPointSchema(month="10/2026", buy_volume=0.0001)]
    _serve(monkeypatch, _resp(flow=flow, acts=acts))
    block = (await cot.fetch_ownership("CRWV"))["insider_activity"]
    assert "1 trade(s) reported no price" in block["last_3_months"]
    assert block["last_12_months"].endswith("no open-market purchase or sale was reported")


@pytest.mark.asyncio
async def test_nan_and_negative_bars_never_become_figures(monkeypatch):
    flow = [SmartMoneyFlowDataPointSchema.model_construct(month="10/2026", buy_volume=float("nan"),
                                                          sell_volume=-3.0, has_activity=True)]
    _serve(monkeypatch, _resp(flow=flow))
    three = (await cot.fetch_ownership("CRWV"))["insider_activity"]["last_3_months"]
    assert three.endswith("no open-market purchase or sale was reported")
    assert "nan" not in three.lower()


@pytest.mark.asyncio
async def test_without_bars_the_windows_read_the_activity_list(monkeypatch):
    acts = [_act("Small Buyer", "2026-09-10", 0.5, 10.0, "Informative Buy"),
            _act("Seller", "2026-07-01", 1000, 5.0, "Informative Sell")]
    _serve(monkeypatch, _resp(acts=acts))
    block = (await cot.fetch_ownership("CRWV"))["insider_activity"]
    assert "bought 0.5 shares (about $5)" in block["last_3_months"], "a fraction never reads as 0"
    assert "sold 0 shares" in block["last_3_months"]
    assert "sold 1,000 shares (about $5,000 in proceeds)" in block["last_6_months"]


@pytest.mark.asyncio
async def test_an_unstamped_build_anchors_on_its_newest_bar(monkeypatch):
    flow = [SmartMoneyFlowDataPointSchema(month="02/2025", buy_volume=0.002),
            SmartMoneyFlowDataPointSchema(month="12/2024", buy_volume=0.001)]
    _serve(monkeypatch, _resp(flow=flow, built_at=None))
    three = (await cot.fetch_ownership("CRWV"))["insider_activity"]["last_3_months"]
    assert three.startswith("last 3 months (12/2024-02/2025") and "bought 3,000 shares" in three


# ── institutions ────────────────────────────────────────────────────────────────────────

def _inst(name, change, pct, *, new=False, held=1.0, date="2026-08-14"):
    return InstitutionalActivitySchema(institution_name=name, date=date, change_in_millions=change,
                                       change_percent=pct, total_held_in_billions=held,
                                       is_new_position=new)


@pytest.mark.asyncio
async def test_each_listed_holder_carries_its_change_and_new_positions_say_new(monkeypatch):
    acts = [_inst("Vanguard Group Inc", 120.5, 3.2), _inst("Fresh Capital LP", 45.0, 100.0, new=True,
                                                          held=0.045),
            _inst("Seller Fund", -80.0, -4.1)]
    flow = RecentActivitiesFlowSummarySchema(period_description="Apr - Jun 2026",
                                             quarter_description="Q2", in_flow_in_billions=1.2,
                                             out_flow_in_billions=0.8)
    _serve(monkeypatch, _resp(inst_acts=acts, inst_flow=flow))
    inst = (await cot.fetch_ownership("CRWV"))["institutions"]
    assert inst["largest_institutions"][0].endswith(
        "; +3.2% shares in the quarter (bought about $120.50 million)")
    others = inst["other_large_changes"]
    assert others[0] == "Fresh Capital LP: a NEW position in the quarter, worth about $45.00 million"
    assert "+100" not in others[0], "a new position never reads as 'doubled'"
    assert others[1] == "Seller Fund: -4.1% shares in the quarter (sold about $80.00 million)"
    assert inst["quarter_flow"].startswith("Q2 (Apr - Jun 2026): about $1.20 billion bought and $800.00 million sold")


@pytest.mark.asyncio
async def test_a_change_with_no_usable_figure_adds_nothing(monkeypatch):
    acts = [InstitutionalActivitySchema.model_construct(
        institution_name="Vanguard Group Inc", date="2026-08-14", change_in_millions=float("nan"),
        change_percent=None, total_held_in_billions=1.0, is_new_position=False, category="x")]
    _serve(monkeypatch, _resp(inst_acts=acts))
    inst = (await cot.fetch_ownership("CRWV"))["institutions"]
    assert inst["largest_institutions"][0].endswith("at the quarter's end")
    assert "nan" not in json.dumps(inst).lower()


@pytest.mark.asyncio
async def test_a_zero_flow_and_no_changes_add_nothing(monkeypatch):
    _serve(monkeypatch, _resp())
    inst = (await cot.fetch_ownership("CRWV"))["institutions"]
    assert "quarter_flow" not in inst and "other_large_changes" not in inst
    assert inst["largest_institutions"][0] == (
        "Vanguard Group Inc: 6.1% of shares, worth $2.50 billion at the quarter's end")


# ── float: ONE source, insiders% from the same figure ─────────────────────────────────

@pytest.mark.asyncio
async def test_the_float_block_and_the_insiders_percent_share_one_reading(monkeypatch):
    extra = dict(float_shares=2.4e8, outstanding_shares=4.0e8, free_float_percent=58.84,
                 float_as_of="2026-10-07")
    _serve(monkeypatch, _resp(detail_extra=extra))
    out = await cot.fetch_ownership("CRWV")
    assert out["float"] == {
        "as_of": "2026-10-07", "shares_outstanding": "400.00 million shares",
        "public_float": "240.00 million shares", "free_float_percent": 58.84,
        "basis": out["float"]["basis"],
    }
    assert out["institutions"]["insiders_percent"] == round(100 - 58.84, 2)
    assert "100 minus free_float_percent" in out["institutions"]["insiders_percent_basis"]


@pytest.mark.asyncio
@pytest.mark.parametrize("extra", [
    {}, {"float_shares": 0.0, "outstanding_shares": -5.0, "free_float_percent": 0.0},
    {"float_shares": float("nan"), "free_float_percent": float("inf")},
    {"free_float_percent": 150.0},
])
async def test_a_missing_or_impossible_float_is_not_stated(monkeypatch, extra):
    _serve(monkeypatch, _resp(detail_extra=extra))
    out = await cot.fetch_ownership("CRWV")
    assert out["float"]["available"] is False and "could not be loaded" in out["float"]["note"]
    # falls back to the breakdown's own figure (a pre-v6 row), never a fabricated one
    assert out["institutions"]["insiders_percent"] == 41.2


def test_the_holders_build_stamps_only_real_float_figures():
    assert _float_stamps({"freeFloat": 58.84, "floatShares": 2.4e8, "outstandingShares": 4e8,
                          "date": "2026-10-07 00:00:00"}) == {
        "float_shares": 2.4e8, "outstanding_shares": 4e8, "free_float_percent": 58.84,
        "float_as_of": "2026-10-07"}
    for bad in ({}, {"freeFloat": 0}, {"freeFloat": -1}, {"freeFloat": 101},
                {"freeFloat": float("nan")}, {"freeFloat": True}, {"floatShares": "abc"},
                {"floatShares": True, "outstandingShares": False}, {"date": "2026-99-99"},
                {"date": 20261007}):
        stamped = _float_stamps(bad)
        assert stamped.get("free_float_percent") is None and stamped.get("float_shares") is None
        assert stamped.get("float_as_of") is None
    assert _float_stamps(None) == {} and _float_stamps("x") == {}


# ── short interest ──────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_short_interest_uses_the_key_stats_rule_over_the_builds_float(monkeypatch):
    si = {"shares_short": 12_300_000, "short_ratio": 2.14, "short_change_3m": 12.4,
          "settlement_date": "2026-09-15"}

    async def _short(sym):
        return si
    monkeypatch.setattr(cot, "_load_short_interest", _short)
    _serve(monkeypatch, _resp(detail_extra={"float_shares": 2.4e8}))
    block = (await cot.fetch_ownership("CRWV"))["short_interest"]
    assert block["percent_of_float"] == short_percent_of_float(si, 2.4e8) == 5.12
    assert block["settlement_date"] == "2026-09-15" and block["days_to_cover"] == 2.14
    assert block["change_3_months_percent"] == 12.4 and block["shares_short"] == "12.30 million shares"


@pytest.mark.asyncio
async def test_without_a_float_the_sources_own_percent_is_used(monkeypatch):
    async def _short(sym):
        return {"shares_short": 1000, "short_percent_of_float": 3.3, "settlement_date": "2026-09-15"}
    monkeypatch.setattr(cot, "_load_short_interest", _short)
    _serve(monkeypatch, _resp())
    assert (await cot.fetch_ownership("CRWV"))["short_interest"]["percent_of_float"] == 3.3


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [{}, None, "x", {"settlement_date": "2026-09-15"},
                                   {"shares_short": float("nan"), "short_ratio": True}])
async def test_no_usable_short_interest_is_not_available_never_zero(monkeypatch, value):
    async def _short(sym):
        return value
    monkeypatch.setattr(cot, "_load_short_interest", _short)
    _serve(monkeypatch, _resp())
    block = (await cot.fetch_ownership("CRWV"))["short_interest"]
    assert block["available"] is False and "do not state a figure" in block["note"]


@pytest.mark.asyncio
async def test_a_slow_short_read_is_not_loaded_and_keeps_warming(monkeypatch):
    gate = asyncio.Event()

    async def _slow(sym):
        await gate.wait()
        return {"shares_short": 1}
    monkeypatch.setattr(cot, "_load_short_interest", _slow)
    monkeypatch.setattr(cot, "_SHORT_INTEREST_WAIT", 0.05)
    _serve(monkeypatch, _resp())
    started = asyncio.get_running_loop().time()
    block = (await cot.fetch_ownership("CRWV"))["short_interest"]
    assert asyncio.get_running_loop().time() - started < 2.0
    assert "not loaded in this answer" in block["note"]
    assert cot._side_tasks, "the read keeps running (and warms its cache)"
    gate.set()
    await asyncio.gather(*list(cot._side_tasks), return_exceptions=True)
    assert not cot._side_tasks


@pytest.mark.asyncio
async def test_a_failed_short_read_is_logged_and_said(monkeypatch, caplog):
    async def _boom(sym):
        raise RuntimeError("exchange 503")
    monkeypatch.setattr(cot, "_load_short_interest", _boom)
    _serve(monkeypatch, _resp())
    with caplog.at_level(logging.WARNING):
        block = (await cot.fetch_ownership("CRWV"))["short_interest"]
    assert block["available"] is False
    assert "short interest read failed for CRWV: RuntimeError: exchange 503" in caplog.text


def test_short_percent_of_float_rules():
    assert short_percent_of_float({"shares_short": 5, "short_percent_of_float": 9.9}, 100) == 5.0
    assert short_percent_of_float({"shares_short": 5, "short_percent_of_float": 9.9}, 0) == 9.9
    assert short_percent_of_float({"shares_short": 0, "short_percent_of_float": 9.9}, 100) == 9.9
    assert short_percent_of_float({}, 100, [{"shortPercentFloat": 0.042}]) == pytest.approx(4.2)
    assert short_percent_of_float({}, 100, [{"shortPercentOutstanding": 7.5}]) == 7.5
    assert short_percent_of_float({}, 100, [{"shortPercentFloat": "x"}]) is None
    assert short_percent_of_float(None, None) is None
    assert short_percent_of_float("x", -5, []) is None
    assert short_percent_of_float({"shares_short": 5}, -100) is None


def test_profile_country_fields_copy_only_real_values():
    assert profile_country_fields({"country": " TW ", "isAdr": True}) == {"country": "TW", "is_adr": True}
    assert profile_country_fields({"country": "", "isAdr": "true"}) == {}
    assert profile_country_fields({"country": 5}) == {}
    assert profile_country_fields(None) == {} and profile_country_fields([]) == {}


# ── the foreign-issuer note ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("flags, people, expected", [
    ({"country": "TW"}, [], True),
    ({"country": "Taiwan"}, [], True),
    ({"country": "US", "is_adr": True}, [], True),
    ({"country": "United States"}, [], False),
    ({"country": "us"}, [], False),
    (None, [], False),
    ({"country": ""}, [], False),
    ({"country": "TW"}, "people", False),
])
async def test_the_foreign_issuer_note_needs_no_filers_and_a_non_us_issuer(monkeypatch, flags, people,
                                                                           expected):
    async def _flags(sym):
        return flags
    monkeypatch.setattr(cot, "_issuer_profile_flags", _flags)
    _serve(monkeypatch, _resp(people=[_owner()] if people == "people" else []))
    insiders = (await cot.fetch_ownership("TSM"))["insiders"]
    assert ("foreign_issuer" in insiders) is expected
    if expected:
        assert "exempt from Form 4" in insiders["foreign_issuer"]


@pytest.mark.asyncio
async def test_no_foreign_note_when_the_insiders_could_not_be_loaded(monkeypatch):
    async def _flags(sym):
        return {"country": "TW"}
    monkeypatch.setattr(cot, "_issuer_profile_flags", _flags)
    resp = _resp()
    resp.ownership_detail.insider_holdings = None
    _serve(monkeypatch, resp)
    insiders = (await cot.fetch_ownership("TSM"))["insiders"]
    assert insiders["available"] is False and "foreign_issuer" not in insiders


@pytest.mark.asyncio
async def test_the_profile_read_is_bounded_and_never_raises(monkeypatch, caplog):
    class _Slow:
        def get_cached_company_profile(self, sym):
            import time
            time.sleep(0.3)
            return {"country": "TW"}

    import app.services.stock_overview_service as sos
    monkeypatch.setattr(sos, "get_stock_overview_service", lambda: _Slow())
    monkeypatch.setattr(cot, "_PROFILE_WAIT", 0.05)
    with caplog.at_level(logging.WARNING):
        assert await _REAL_PROFILE_FLAGS("TSM") is None
    assert "company profile read failed for TSM" in caplog.text

    class _Row:
        def get_cached_company_profile(self, sym):
            return {"country": "TW", "is_adr": True}

    monkeypatch.setattr(sos, "get_stock_overview_service", lambda: _Row())
    monkeypatch.setattr(cot, "_PROFILE_WAIT", 2.0)
    assert await _REAL_PROFILE_FLAGS("TSM") == {"country": "TW", "is_adr": True}

    class _NotADict:
        def get_cached_company_profile(self, sym):
            return ["x"]

    monkeypatch.setattr(sos, "get_stock_overview_service", lambda: _NotADict())
    assert await _REAL_PROFILE_FLAGS("TSM") is None


# ── the cap: 40 insiders and full side lists ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_forty_insiders_and_full_side_lists_fit_and_everyone_is_shown_or_named(monkeypatch):
    people = [_owner(name=f"Insider Number {i:02d} With A Long Family Name", n_holdings=5,
                     date=f"2026-09-{30 - (i % 28):02d}") for i in range(40)]
    insts = [TopInstitutionSchema(rank=i + 1, name=f"Institution With A Long Name {i} " + "x" * 40,
                                  value_in_billions=1.0 + i, percent_ownership=1.0) for i in range(10)]
    breakdown = ShareholderBreakdownSchema(insiders_percent=41.2, institutions_percent=38.5,
                                           public_other_percent=20.3,
                                           top_10_owners=Top10OwnersSchema(institutions=insts))
    acts = [_inst(f"Institution With A Long Name {i} " + "x" * 40, 10.0 + i, 1.5) for i in range(15)]
    flow = RecentActivitiesFlowSummarySchema(quarter_description="Q2", in_flow_in_billions=1.0,
                                             out_flow_in_billions=1.0)
    _serve(monkeypatch, _resp(people=people, breakdown=breakdown, inst_acts=acts, inst_flow=flow))
    out = await cot.fetch_ownership("CRWV", user_tier="pro")
    from app.config import settings
    cap = int(getattr(settings, "GEMINI_TOOL_RESULT_MAX_CHARS", 8000) or 8000)
    size = len(json.dumps(out, default=str))
    assert size <= cap - cot._BUDGET_MARGIN, size
    shown = [p["name"] for p in out["insiders"]["people"]]
    cut = out["insiders"].get("not_shown", [])
    assert shown + cut == [p.name for p in people], "everyone listed or named"
    assert "latest_transaction" in out["insiders"]["people"][0]
    assert out["shortened"]
    for key in ("insider_activity", "float", "short_interest", "congress", "how_to_read",
                "beneficial_owners_13d_13g"):
        assert key in out, key


@pytest.mark.asyncio
async def test_no_vendor_name_in_the_extended_result(monkeypatch):
    async def _short(sym):
        return {"shares_short": 10, "settlement_date": "2026-09-15"}
    monkeypatch.setattr(cot, "_load_short_interest", _short)
    _serve(monkeypatch, _resp(detail_extra={"float_shares": 2.4e8}))
    text = json.dumps(await cot.fetch_ownership("CRWV", user_tier="pro")).lower()
    for word in ("fmp", "financial modeling prep", "gemini", "google", "openai", "finra", "nasdaq"):
        assert word not in text, word


@pytest.mark.asyncio
async def test_the_trade_line_explains_tax_withholding(monkeypatch):
    _serve(monkeypatch, _resp())
    line = (await cot.fetch_ownership("CRWV"))["insiders"]["people"][0]["latest_transaction"]
    assert "F-InKind: had 12,000 shares withheld to cover taxes (not a sale" in line
    assert "not a sale" in line and "sold" not in line and "proceeds" not in line


@pytest.mark.asyncio
async def test_the_how_to_read_keeps_its_pins_and_states_the_new_rules(monkeypatch):
    _serve(monkeypatch, _resp())
    how = (await cot.fetch_ownership("CRWV"))["how_to_read"]
    for pinned in ("never present it as a live", "never add holdings up into a total",
                   "never that they own nothing"):
        assert pinned in how
    assert "PROCEEDS" in how and "not a sale" in how and "no tax amount" in how


# ── the build: congressional disclosure dates ─────────────────────────────────────────

def test_a_disclosure_date_is_carried_only_when_it_is_a_date():
    svc = object.__new__(HoldersService)
    base = {"firstName": "Nancy", "lastName": "Pelosi", "district": "CA11", "type": "Purchase",
            "amount": "$1,001 - $15,000", "transactionDate": "2026-07-20"}
    rows = [dict(base, disclosureDate="2026-08-01"), dict(base, disclosureDate="2026-99-01"),
            dict(base, disclosureDate=None), dict(base, disclosureDate=20260801),
            dict(base, disclosureDate="2026-08-01T00:00:00")]
    acts = svc._build_congress_activities([], rows, [])
    assert [a.disclosure_date for a in acts] == ["2026-08-01", None, None, None, "2026-08-01"]


@pytest.mark.asyncio
async def test_a_person_with_no_trade_in_the_windows_is_still_listed_with_their_holding(monkeypatch):
    """The activity windows count trades; the people list is never windowed: a director whose
    latest Form 4 is 16 months old keeps their reported holding and its date."""
    quiet = _owner(name="Quiet Director", date="2025-06-02")
    _serve(monkeypatch, _resp(people=[_owner(), quiet]))
    out = await cot.fetch_ownership("CRWV")
    person = out["insiders"]["people"][1]
    assert person["name"] == "Quiet Director"
    assert person["holdings"][0] == "Class A Common Stock held directly: 302,526 shares as of 2025-06-02"
    assert out["insider_activity"]["last_12_months"].endswith("no open-market purchase or sale was reported")


@pytest.mark.asyncio
async def test_thirteen_d_g_holders_are_omitted_with_a_note_never_called_none(monkeypatch):
    _serve(monkeypatch, _resp())
    note = (await cot.fetch_ownership("CRWV"))["beneficial_owners_13d_13g"]
    assert "13D/13G" in note and "never that there are none" in note


# ── final review 2026-10-09: no net DOLLAR direction beside an unpriced trade ──


@pytest.mark.parametrize("unpriced", [0, 1])
def test_the_net_dollar_direction_is_stated_only_when_every_trade_is_priced(unpriced):
    text = cot._window_text("last 3 months", 1000, 500000, 10000.0, 0.0, 1, 1, unpriced=unpriced)
    if unpriced:
        assert "net buying" not in text and "net selling" not in text and "net about" not in text
        assert "net direction in dollars is not stated: 1 trade(s) reported no price" in text
        assert "bought 1,000 shares (about $10,000)" in text, "the per-side figure stays"
    else:
        assert "net about $10,000 (net buying)" in text


@pytest.mark.asyncio
async def test_a_large_unpriced_sale_is_never_reported_as_net_buying(monkeypatch):
    """The reviewer's case through the real tool: a priced 1,000-share buy and an UNPRICED
    500,000-share sale in the 3-, 6- and 12-month windows."""
    acts = [_act("Small Buyer", "2026-10-01", 1000, 10.0, "Informative Buy"),
            _act("Big Seller", "2026-10-02", 500000, 0.0, "Informative Sell")]   # 0.0 = no price
    flow = [SmartMoneyFlowDataPointSchema(month="10/2026", buy_volume=0.001, sell_volume=0.5)]
    summary = InsiderActivitySummarySchema(informative_buys_in_millions=0.001,
                                           informative_sells_in_millions=0.5,
                                           num_buyers=1, num_sellers=1)
    card = SmartMoneyFlowSummarySchema(total_buy_usd_millions=0.01, total_sell_usd_millions=0.0)
    _serve(monkeypatch, _resp(flow=flow, acts=acts, summary=summary, card=card))
    block = (await cot.fetch_ownership("CRWV"))["insider_activity"]
    for key in ("last_3_months", "last_6_months", "last_12_months"):
        line = block[key]
        assert "net buying" not in line and "net about" not in line, (key, line)
        assert "net direction in dollars is not stated" in line, (key, line)
