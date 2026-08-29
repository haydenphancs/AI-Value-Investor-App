"""The widget is a DAILY surface. Nothing older than today's session may reach it.

This replaces `test_widget_reason_provenance.py`, whose subject — a three-kind
catalyst/context/none union — no longer exists. The failure it guarded against is still
real, but the mechanism changed: instead of labelling a stale or off-window statement
and hoping the label is respected, the widget now only ever renders facts that are
dated to today by construction.

The concrete bug this pins: the cached ACHR catalyst read *"stock surged"* on a **+42.7%
fifteen-day window**, while the widget was rendering **−5.02% today**. Serving it would
not have been a near-miss — it would have printed the opposite of what happened, with
total confidence, on a Home Screen tile the reader cannot interrogate.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from app.services.daily_move_attribution import CauseKind, attribute
from app.services.widget_movers_service import _classified_today_news, _et_date
from app.utils.market_hours import ET

TODAY_ISO = datetime.now(ET).date().isoformat()


def _ts(days_ago: int = 0) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


_UNSET = object()


def _card(headline="Chipmaker lowers guidance for the year", generated_at=_UNSET, **extra):
    return {
        "headline": headline,
        "ai_generated": True,
        "generated_at": _ts(0) if generated_at is _UNSET else generated_at,
        **extra,
    }


# ── today's news only ─────────────────────────────────────────────────


def test_a_card_generated_today_can_supply_a_classified_catalyst():
    classified, had_news, checked = _classified_today_news(_card(), TODAY_ISO)
    assert checked is True
    assert had_news is True
    assert classified and classified[0][0] == "Guidance Cut"


def test_the_classifier_vocabulary_is_narrow_on_purpose():
    """It matches "lowers guidance" but NOT "cuts guidance", and that is the right
    trade for this surface: a loose matcher would put a confident wrong tag on a
    Home Screen tile. A missed catalyst degrades to `none`, which is honest; a
    mislabelled one is a lie."""
    hit, _, _ = _classified_today_news(_card("Acme lowers guidance"), TODAY_ISO)
    miss, had_news, _ = _classified_today_news(_card("Acme cuts guidance"), TODAY_ISO)
    assert hit and hit[0][0] == "Guidance Cut"
    assert miss == [] and had_news is True


def test_a_card_from_a_previous_session_supplies_nothing():
    """A 48h news roll-up is not a same-day catalyst, whatever it says."""
    classified, had_news, checked = _classified_today_news(
        _card(generated_at=_ts(days_ago=1)), TODAY_ISO
    )
    # The sweeper HAS covered this ticker; it simply has nothing from today. That is
    # a real negative and may be stated, unlike a ticker with no row at all.
    assert checked is True
    assert classified == []
    assert had_news is False


@pytest.mark.parametrize("bad", [None, "", "not-a-date", 12345, {}])
def test_an_unreadable_generated_at_is_treated_as_not_today(bad):
    classified, had_news, _ = _classified_today_news(_card(generated_at=bad), TODAY_ISO)
    assert classified == [] and had_news is False


def test_an_unclassifiable_headline_still_reports_that_news_exists():
    """"There was news but none of it explains the move" is a different, and more
    honest, statement than "there was no news"."""
    classified, had_news, _ = _classified_today_news(
        _card(headline="Company announces new office location"), TODAY_ISO
    )
    assert classified == []
    assert had_news is True


@pytest.mark.parametrize("card", [None, "junk", 42])
def test_a_ticker_with_no_insight_row_is_UNCHECKED_not_news_free(card):
    """`get_cards` yields None for a scope it has never stored or whose card expired.

    Most reachable in portfolio mode, where holdings need not be inside the swept
    top-200 universe — so without the third state the tile would announce "No company
    news today" about a ticker nothing has ever examined.
    """
    classified, had_news, checked = _classified_today_news(card, TODAY_ISO)
    assert classified == [] and had_news is False and checked is False


@pytest.mark.parametrize("card", [{}, {"headline": "   "}])
def test_a_present_but_empty_card_is_checked_and_news_free(card):
    classified, had_news, checked = _classified_today_news(card, TODAY_ISO)
    assert classified == [] and had_news is False


# ── the date helper the whole daily contract rests on ─────────────────


def test_et_date_parses_the_shapes_the_backend_actually_emits():
    assert _et_date("2026-08-14T22:47:09Z") is not None
    assert _et_date(datetime(2026, 8, 14, 22, 47, tzinfo=timezone.utc)) is not None


@pytest.mark.parametrize("bad", [None, "", "nope", 42, [], {}])
def test_et_date_returns_none_rather_than_guessing(bad):
    assert _et_date(bad) is None


def test_a_late_utc_evening_is_still_the_same_ET_trading_day():
    """23:30 UTC is 19:30 ET — the same session, not tomorrow. Getting this backwards
    is the exact class of bug migration 089 was written to fix."""
    assert _et_date("2026-08-14T23:30:00Z") == "2026-08-14"


# ── nothing multi-day can reach the wire ──────────────────────────────


def test_no_attribution_output_can_describe_a_multi_day_window():
    """The regression, stated directly. Every string the widget renders comes from
    `attribute`, and none of its branches has access to a multi-day window at all."""
    banned = ("15 days", "30 days", "45 days", "past month", "since jul", "since aug",
              "surged", "rally", "over the last")
    samples = [
        attribute(ticker="ACHR", change_percent=-5.02, today=date(2026, 8, 14),
                  sigma_daily=0.0455, industry_name="Aerospace & Defense",
                  industry_change_percent=-1.23, had_news=True),
        attribute(ticker="CRM", change_percent=-4.2, today=date(2026, 8, 14),
                  sigma_daily=0.028,
                  grade_rows=[{"date": "2026-08-14", "gradingCompany": "MS",
                               "action": "downgrade", "newGrade": "Equal Weight"}]),
        attribute(ticker="AAPL", change_percent=3.1, today=date(2026, 8, 14),
                  sigma_daily=0.016,
                  earnings_row={"date": "2026-08-14", "epsActual": 1.4,
                                "epsEstimated": 1.25}),
        attribute(ticker="X", change_percent=-2.4, today=date(2026, 8, 14),
                  sigma_daily=0.02, industry_name="Semis",
                  industry_change_percent=-2.1),
    ]
    for a in samples:
        low = a.detail.lower()
        assert not any(b in low for b in banned), a.detail


def test_the_detail_line_is_never_empty_for_any_cause():
    """A blank line under a red percentage is worse than a plain one."""
    for kw in (
        {"earnings_row": {"date": "2026-08-14"}},
        {"grade_rows": [{"date": "2026-08-14", "gradingCompany": "X",
                         "action": "upgrade", "newGrade": "Buy"}]},
        {"classified_news": [("M&A", "Co to acquire rival")]},
        {"industry_name": "Tech", "industry_change_percent": -2.6},
        {},
    ):
        a = attribute(ticker="T", change_percent=-2.8, today=date(2026, 8, 14),
                      sigma_daily=0.02, **kw)
        assert a.detail.strip()


def test_a_none_cause_never_carries_a_tag():
    """A badge implies a known cause. `none` must not render one."""
    a = attribute(ticker="T", change_percent=-2.8, today=date(2026, 8, 14),
                  sigma_daily=0.02)
    assert a.kind is CauseKind.NONE
    assert a.tag is None


# ── the Market tile's headline is bound by the same rule ─────────────
#
# `_market_brief` puts the `__MARKET__` roll-up on the Market tile. That roll-up's own
# corpus window is 24-96h and its hard TTL is 96h, so it can comfortably outlive the
# session it describes — which is exactly why `WidgetMoverPayload` used to refuse to
# carry a market headline at all ("not a move and not today-scoped").
#
# It carries one now, so the refusal has to be re-implemented as a GATE. Rendering
# Friday's market read on a Monday morning is the same class of error as the ACHR
# "+42.7% fifteen-day rally" above: confident, wrong, and on a surface with no way to
# interrogate it.


def _brief_card(headline="AI, Fed Speech Drive Market Cautious Tone", **extra):
    card = {"headline": headline, "sentiment": "Neutral", "generated_at": _ts(0)}
    card.update(extra)
    return card


def test_a_roll_up_from_this_session_reaches_the_tile():
    from app.services.widget_movers_service import _market_brief

    brief = _market_brief(_brief_card(), TODAY_ISO)
    assert brief is not None
    assert brief.headline == "AI, Fed Speech Drive Market Cautious Tone"
    assert brief.sentiment == "Neutral"


@pytest.mark.parametrize("days_ago", [1, 3, 7])
def test_a_roll_up_from_a_previous_session_never_reaches_the_tile(days_ago):
    from app.services.widget_movers_service import _market_brief

    assert _market_brief(_brief_card(generated_at=_ts(days_ago)), TODAY_ISO) is None, (
        "an off-session market headline was served. On a Monday morning that is "
        "Friday's read presented as what the market is doing right now."
    )


@pytest.mark.parametrize(
    "card",
    [
        None,
        {},
        "not a dict",
        {"headline": "x"},                                   # undated ⇒ unprovable
        {"headline": "", "generated_at": _ts(0)},            # empty
        {"headline": "   ", "generated_at": _ts(0)},         # whitespace only
        {"headline": "x", "generated_at": "not a timestamp"},
    ],
)
def test_an_unusable_roll_up_yields_no_brief_rather_than_a_blank_line(card):
    from app.services.widget_movers_service import _market_brief

    assert _market_brief(card, TODAY_ISO) is None


def test_the_gate_is_the_same_one_the_news_detectors_use():
    """One rule, one implementation — a second date comparison would drift."""
    import inspect

    from app.services.widget_movers_service import _classified_today_news, _market_brief

    for fn in (_market_brief, _classified_today_news):
        src = inspect.getsource(fn)
        assert '_et_date(card.get("generated_at")) != today_et' in src, (
            f"{fn.__name__} no longer gates on the session date the rest of the payload "
            "describes"
        )
