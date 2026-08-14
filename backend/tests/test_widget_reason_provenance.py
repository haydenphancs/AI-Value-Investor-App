"""What the widget is ALLOWED to claim about why a stock moved.

This is the honesty guard, and it exists because of a measurement: on 2026-08-14
the live database held 12 insight cards and **zero** carried a `price_move`. Every
card had a `headline`. So the naive implementation — "show the card's text" —
ships 100% news roll-ups presented as causal explanations, under a big red
percentage, on a surface the reader glances at for one second and cannot
interrogate.

Three kinds, and the tag is not decoration:

* ``catalyst`` — web-grounded and source-cited. May be framed as *why*.
* ``context``  — the news headline. Says what is going on; establishes nothing.
* ``none``     — arithmetic only. Always available, never wrong.

The two guards on promoting to ``catalyst`` are sign and session. Both defend
against a stored reason outliving the move it explains, which is not a
hypothetical: `price_catalyst_cache` keys on ``(ticker, window_label, direction)``
with a 24h TTL and the sweeper always passes ``window_label="today"`` — a label
with no date in it. Migration 136 fixes the key; this read-side check is what
protects the widget in the meantime, and is worth keeping afterwards.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.schemas.widget import WidgetReasonKind
from app.services.widget_movers_service import (
    deterministic_reason,
    resolve_reason,
)
from app.utils.market_hours import ET

TODAY_ET = datetime.now(ET).date().isoformat()


def _ts(days_ago: int = 0) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


# Distinguishes "caller did not specify" from "caller explicitly passed a falsy
# value". `generated_at or _ts(0)` would turn an explicit None/"" into *now* and
# quietly make the freshness guard untestable — the helper would be under test
# instead of the code.
_UNSET = object()


def _card(price_move=None, generated_at=_UNSET, headline="Some market headline", **extra):
    return {
        "headline": headline,
        "ai_generated": True,
        "generated_at": _ts(0) if generated_at is _UNSET else generated_at,
        "price_move": price_move,
        "sources": [{"title": "Reuters story", "url": "https://example.com/a"}],
        **extra,
    }


_PM = {
    "reason": "Archer fell after an analyst downgrade.",
    "change_percent": -4.8,
    "catalyst_tag": "Analyst Downgrade",
    "tier": "Unusual",
}


# ── promotion to catalyst ─────────────────────────────────────────────


def test_fresh_same_direction_catalyst_is_used_and_cited():
    r = resolve_reason(_card(_PM), -4.81, TODAY_ET)
    assert r.kind == WidgetReasonKind.CATALYST
    assert r.text == _PM["reason"]
    assert r.catalyst_tag == "Analyst Downgrade"
    assert len(r.sources) == 1


def test_opposite_sign_catalyst_is_refused():
    """A stored −4.8% reason under a live +5.2% quote is not stale — it is WRONG.

    It would render "fell after a downgrade" beside a green rise.
    """
    r = resolve_reason(_card(_PM), +5.2, TODAY_ET)
    assert r.kind == WidgetReasonKind.CONTEXT
    assert "downgrade" not in r.text.lower()


def test_catalyst_from_a_previous_session_is_refused():
    """The 24h TTL plus an undated "today" label makes yesterday's reason servable."""
    r = resolve_reason(_card(_PM, generated_at=_ts(days_ago=1)), -4.81, TODAY_ET)
    assert r.kind == WidgetReasonKind.CONTEXT


@pytest.mark.parametrize("stored", [None, float("nan"), float("inf"), "abc"])
def test_catalyst_without_a_usable_stored_change_cannot_be_sign_checked_so_is_refused(stored):
    pm = {**_PM, "change_percent": stored}
    assert resolve_reason(_card(pm), -4.81, TODAY_ET).kind == WidgetReasonKind.CONTEXT


@pytest.mark.parametrize("live", [0.0, -0.0, None])
def test_a_zero_or_missing_live_change_cannot_confirm_direction(live):
    """Zero has no sign, so it can never agree with a stored direction."""
    assert resolve_reason(_card(_PM), live, TODAY_ET).kind != WidgetReasonKind.CATALYST


@pytest.mark.parametrize("text", ["", "   ", "\n\t "])
def test_a_blank_reason_is_not_a_catalyst(text):
    pm = {**_PM, "reason": text}
    assert resolve_reason(_card(pm), -4.81, TODAY_ET).kind == WidgetReasonKind.CONTEXT


def test_a_catalyst_with_no_tag_is_still_a_catalyst():
    """`catalyst_tag=None` is the documented "no clear catalyst" outcome, and the
    reason sentence still explains the move — it just has no short label."""
    pm = {**_PM, "catalyst_tag": None}
    r = resolve_reason(_card(pm), -4.81, TODAY_ET)
    assert r.kind == WidgetReasonKind.CATALYST
    assert r.catalyst_tag is None


# ── context and none ──────────────────────────────────────────────────


def test_a_headline_is_never_labelled_catalyst():
    """The single most important assertion in this file."""
    r = resolve_reason(_card(price_move=None), -4.81, TODAY_ET)
    assert r.kind == WidgetReasonKind.CONTEXT
    assert r.text == "Some market headline"


def test_a_context_reason_carries_no_sources():
    """Sources belong to the news roll-up. Attaching them to a line that makes no
    causal claim would make an unsourced statement look sourced."""
    r = resolve_reason(_card(price_move=None), -4.81, TODAY_ET)
    assert r.sources == []
    assert r.catalyst_tag is None


def test_a_non_ai_card_does_not_supply_a_context_line():
    r = resolve_reason(_card(price_move=None, ai_generated=False), -4.81, TODAY_ET)
    assert r.kind == WidgetReasonKind.NONE


@pytest.mark.parametrize("card", [None, {}, {"headline": ""}, {"headline": "   "}])
def test_no_usable_card_degrades_to_arithmetic(card):
    r = resolve_reason(card, -4.81, TODAY_ET)
    assert r.kind == WidgetReasonKind.NONE
    assert "4.8" in r.text


def test_a_malformed_price_move_does_not_crash_the_widget():
    for bad in ["a string", 42, [], {"reason": None}]:
        r = resolve_reason(_card(price_move=bad), -4.81, TODAY_ET)
        assert r.kind in (WidgetReasonKind.CONTEXT, WidgetReasonKind.NONE)


def test_an_unparseable_generated_at_is_treated_as_not_today():
    for bad in ["not-a-date", "", None, 12345]:
        r = resolve_reason(_card(_PM, generated_at=bad), -4.81, TODAY_ET)
        assert r.kind == WidgetReasonKind.CONTEXT


# ── the deterministic line ────────────────────────────────────────────


def test_deterministic_reason_states_the_multiple_when_sigma_is_known():
    text = deterministic_reason(-4.81, 1.057)
    assert "Down 4.8%" in text
    assert "1.1×" in text


def test_deterministic_reason_omits_the_multiple_when_sigma_is_unknown():
    text = deterministic_reason(-4.81, None)
    assert "Down 4.8%" in text
    assert "×" not in text


@pytest.mark.parametrize("pct", [None, float("nan"), float("inf")])
def test_deterministic_reason_never_invents_a_number(pct):
    assert "unavailable" in deterministic_reason(pct, None).lower()


def test_deterministic_reason_handles_exact_zero():
    assert "Flat" in deterministic_reason(0.0, 0.0)


def test_deterministic_reason_is_never_empty():
    for pct in [None, 0.0, -0.0, 1e-9, -12.5, 1e6, float("nan")]:
        for z in [None, 0.0, 3.2]:
            assert deterministic_reason(pct, z).strip()
