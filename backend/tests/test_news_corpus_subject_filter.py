"""A ticker's insight card must be built from articles about THAT ticker.

THE FAILING INPUT, taken verbatim from production (`ai_insight_cache`, scope PLUG):

    article_count 1
    source        "FuelCell Energy Sinks 8%, Bloom Energy Falls 3%, Plug Power Drops 3%:
                   What's Behind the Hydrogen Stock Selloff?"
    headline      "Hydrogen Stocks Face Selloff"
    trigger       band Notable->Typical (+4.16%)

One sector wrap, led by two other companies, was the SOLE input for a card about PLUG —
and the model announced a selloff on a day PLUG rose 4.16%. The summary was faithful to
its corpus; the corpus was wrong.

⚠️ Neither obvious test catches that article, which is why the filter looks the way it
does: FMP genuinely tags it `PLUG`, AND "Plug Power" really is in the title. It is just
THIRD. Position is the signal, not presence.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.services.news_insight_service import (
    PRIMARY_WINDOW_HOURS,
    article_is_about,
    filter_to_subject,
    select_recent_corpus,
)

# The real headline. Kept verbatim — a paraphrase would not exercise the ordering.
ROUNDUP = (
    "FuelCell Energy Sinks 8%, Bloom Energy Falls 3%, Plug Power Drops 3%: "
    "What's Behind the Hydrogen Stock Selloff?"
)


def _row(headline, tickers, *, hours_ago=1.0):
    return {
        "headline": headline,
        "related_tickers": list(tickers),
        "published_at": (
            datetime.now(timezone.utc) - timedelta(hours=hours_ago)
        ).isoformat(),
    }


# ── the discriminator ────────────────────────────────────────────────────────


def test_the_production_roundup_is_rejected_for_a_trailing_member():
    assert article_is_about(_row(ROUNDUP, ["FCEL", "BE", "PLUG"]), "PLUG") is False


def test_the_same_roundup_is_accepted_for_the_company_it_leads_with():
    """Anti-vacuity: proves the rejection is about ORDER, not about the word 'roundup'.
    Rejecting every multi-ticker article would also be wrong — FuelCell's own readers
    should get this card."""
    assert article_is_about(_row(ROUNDUP, ["FCEL", "BE", "PLUG"]), "FCEL") is True


def test_a_story_about_the_company_is_kept():
    row = _row("Plug Power beats Q2 revenue estimates, raises outlook", ["PLUG"])
    assert article_is_about(row, "PLUG") is True


def test_a_peer_story_that_merely_tags_us_is_rejected():
    assert article_is_about(_row("Bloom Energy lands data-centre deal", ["BE", "PLUG"]), "PLUG") is False


def test_an_oblique_headline_tagged_only_to_us_is_kept():
    """The company is not named, but nobody else is tagged — dropping this would lose
    real single-company coverage, which is the over-correction to avoid."""
    assert article_is_about(_row("Hydrogen maker lands 5MW order", ["PLUG"]), "PLUG") is True


def test_an_untitled_row_is_rejected():
    assert article_is_about(_row("", ["PLUG"]), "PLUG") is False


@pytest.mark.parametrize("junk", [None, {}, {"headline": None}, "not-a-dict", 42])
def test_malformed_rows_never_raise(junk):
    assert article_is_about(junk, "PLUG") is False


def test_an_empty_scope_never_matches():
    assert article_is_about(_row(ROUNDUP, ["PLUG"]), "") is False


def test_the_company_name_matches_without_the_symbol():
    row = _row("Plug Power wins hydrogen contract", ["PLUG"])
    assert article_is_about(row, "PLUG", "Plug Power Inc.") is True


# ── fail-open-to-nothing ─────────────────────────────────────────────────────


def test_a_scope_whose_only_article_is_a_peer_roundup_yields_NO_corpus():
    """The whole point. An empty corpus means no card, which is the honest outcome —
    falling back to the unfiltered set "so there is at least a card" is exactly what
    produced the Hydrogen-selloff headline."""
    rows = [_row(ROUNDUP, ["FCEL", "BE", "PLUG"])]
    assert filter_to_subject(rows, "PLUG") == []
    kept, _ = select_recent_corpus(rows, datetime.now(timezone.utc), scope="PLUG")
    assert kept == []


def test_market_scope_is_not_filtered():
    """`scope=None` is how MARKET opts out; market coverage is about the market."""
    rows = [_row(ROUNDUP, ["FCEL", "BE", "PLUG"])]
    kept, _ = select_recent_corpus(rows, datetime.now(timezone.utc))
    assert len(kept) == 1


# ── ordering: filter BEFORE the window ───────────────────────────────────────


def test_a_real_article_just_outside_24h_is_preferred_over_an_empty_24h_window():
    """Ordering is load-bearing. Windowing first would pick the 24h window on the
    strength of a peer round-up, filter it to empty, and emit no card — while a real
    36h-old story about the company sat unread in the 48h window."""
    now = datetime.now(timezone.utc)
    rows = [
        _row(ROUNDUP, ["FCEL", "BE", "PLUG"], hours_ago=2),          # recent, not ours
        _row("Plug Power wins hydrogen contract", ["PLUG"], hours_ago=36),  # ours, older
    ]
    kept, window = select_recent_corpus(rows, now, scope="PLUG")
    assert [r["headline"] for r in kept] == ["Plug Power wins hydrogen contract"]
    assert window > PRIMARY_WINDOW_HOURS, "should have fallen back to the 48h window"


def test_the_lead_tag_admits_a_wrap_for_its_primary_symbol():
    """FMP lists a wrap's primary symbol first, and that is the ONLY signal available
    when the title names companies while the tags are symbols — "FuelCell Energy" is
    unmatchable from "FCEL" with no ticker->name map.

    A soft signal on purpose: it is FMP convention, not a contract, so it is an OR and
    never a veto. Worst case one extra member of a wrap is admitted.
    """
    row = _row(ROUNDUP, ["FCEL", "BE", "PLUG"])
    assert article_is_about(row, "FCEL") is True   # lead tag
    assert article_is_about(row, "BE") is False    # neither lead tag nor lead clause
    assert article_is_about(row, "PLUG") is False


def test_the_lead_clause_alone_admits_a_wrap_when_the_symbol_is_in_it():
    """Proves the lead-CLAUSE rule is live and not masked by the lead-TAG rule: here the
    lead tag is someone else, but our symbol opens the title."""
    row = _row("PLUG Jumps 9%, FuelCell Falls, Bloom Slips: Hydrogen Movers",
               ["FCEL", "BE", "PLUG"])
    assert article_is_about(row, "PLUG") is True
