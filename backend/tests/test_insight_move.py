"""`insight_move` — the ONE contract between the Insights gate, the card writer and the jump
search (2026-11-27): the move tag a card's stored reason LEADS with, its strict parser, the kind
ranks behind the downgrade guard, the same-ET-day hold and the move direction.

Pure, stdlib only (category 1). The round trip at the end goes through the writer itself: what
`generate_and_store` stores is what the gate's `parse_move_tag` and the read path's
`_row_to_card` read back, past the 200-character clip.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.services import insight_move as im
from app.services.insight_move import (
    MarketContext,
    MoveContext,
    MoveOutcome,
    MoveTag,
    direction_of,
    et_date,
    format_move_tag,
    held_move,
    is_move_downgrade,
    market_card_lost,
    move_rank,
    parse_move_tag,
    strip_move_tag,
    with_move_tag,
)

# Mon 2026-11-23 15:00 ET
NOW = datetime(2026, 11, 23, 20, 0, tzinfo=timezone.utc)


# ── the tag ─────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("direction", ["up", "down"])
@pytest.mark.parametrize("kind", ["company", "market", "none"])
def test_every_tag_round_trips(direction, kind):
    tag = format_move_tag(direction, kind)
    assert tag == f"move:{direction}:{kind}"
    assert parse_move_tag(tag) == MoveTag(kind=kind, direction=direction)
    assert parse_move_tag(f"{tag} + new_articles (9) + web (3)") == MoveTag(kind, direction)
    assert parse_move_tag(with_move_tag("web_only + band A->B", tag)) == MoveTag(kind, direction)


@pytest.mark.parametrize("direction,kind", [
    ("sideways", "company"), ("up", "cause"), ("UP", "company"), (None, "none"), ("up", None),
    (1, "none"), ("up", ""),
])
def test_format_refuses_anything_outside_the_vocabularies(direction, kind):
    assert format_move_tag(direction, kind) is None


@pytest.mark.parametrize("reason", [
    None, 7, b"move:up:company", "", "web_only + move:up:company", " move:up:company",
    "move:up:companyx", "move:up:company+x", "move=company:up", "Move:up:company",
    "move:up:", "move::company", "move:down:rumour", "xmove:up:none",
])
def test_the_parser_is_strict_and_never_raises(reason):
    assert parse_move_tag(reason) is None


def test_the_tag_ends_at_a_space_or_the_end():
    assert parse_move_tag("move:up:none") == MoveTag("none", "up")
    assert parse_move_tag("move:up:none\tx") == MoveTag("none", "up")


def test_with_move_tag_replaces_a_leading_tag_and_drops_an_invalid_one():
    assert with_move_tag("move:up:company + new_articles (3)", "move:down:none") == \
        "move:down:none + new_articles (3)"
    assert with_move_tag("move:up:company + new_articles (3)", None) == "new_articles (3)"
    assert with_move_tag("move:up:company + x", "move:bogus:none") == "x"
    assert with_move_tag("", "move:up:none") == "move:up:none"
    assert with_move_tag(None, "move:up:none") == "move:up:none"
    assert strip_move_tag("move:up:none") == "" and strip_move_tag(5) == ""
    assert strip_move_tag("web_only + move:up:none") == "web_only + move:up:none"


def test_the_tag_survives_the_200_character_clip_because_it_leads():
    from app.services.news_insight_service import _TRIGGER_REASON_CHARS, _clip

    long_reason = "new_articles (25) + " + "x" * 400
    stored = _clip(with_move_tag(long_reason, "move:up:market"), _TRIGGER_REASON_CHARS)
    assert len(stored) <= _TRIGGER_REASON_CHARS
    assert parse_move_tag(stored) == MoveTag("market", "up")


# ── ranks and the downgrade table ───────────────────────────────────────────────────────────

def test_ranks():
    assert move_rank("company") > move_rank("market") > move_rank("none") > move_rank(None) == 0
    assert move_rank("rumour") == 0 and move_rank(5) == 0


def test_an_unhashable_kind_ranks_zero():
    assert move_rank(["company"]) == 0


@pytest.mark.parametrize("held,kind,direction,downgrade", [
    # same direction: a lower kind is refused
    (MoveTag("company", "up"), "market", "up", True),
    (MoveTag("company", "up"), "none", "up", True),
    (MoveTag("company", "up"), None, "up", True),
    (MoveTag("market", "up"), "none", "up", True),
    (MoveTag("none", "up"), None, "up", True),
    # the same or a higher kind writes
    (MoveTag("company", "up"), "company", "up", False),
    (MoveTag("market", "up"), "company", "up", False),
    (MoveTag("none", "up"), "none", "up", False),
    (MoveTag("none", "up"), "market", "up", False),
    # a reversal always writes
    (MoveTag("company", "up"), "none", "down", False),
    (MoveTag("company", "down"), None, "up", False),
    # nothing held, or a junk held tag
    (None, "none", "up", False),
    (MoveTag("rumour", "up"), "none", "up", False),
    # a general write with no direction never counts as the same direction
    (MoveTag("company", "up"), None, None, False),
])
def test_the_downgrade_table(held, kind, direction, downgrade):
    assert is_move_downgrade(held, kind, direction) is downgrade


# ── the hold: same ET day, never a future stamp ─────────────────────────────────────────────

@pytest.mark.parametrize("generated_at,held", [
    (NOW - timedelta(hours=1), True),
    ("2026-11-23T14:31:00Z", True),                          # 09:31 ET same day
    ("2026-11-23T04:59:00Z", False),                         # 23:59 ET the day before
    ("2026-11-23T05:00:00+00:00", True),                     # 00:00 ET
    (NOW + timedelta(minutes=4), True),                      # inside clock skew
    (NOW + timedelta(minutes=6), False),                     # beyond clock skew
    (NOW - timedelta(days=1), False),
    ("2026-11-23T15:00:00", True),                           # naive = UTC
    ("garbage", False), ("", False), (None, False), (5, False),
])
def test_held_move_is_today_only(generated_at, held):
    got = held_move("move:up:company + x", generated_at, NOW)
    assert (got == MoveTag("company", "up")) is held
    if not held:
        assert got is None


def test_held_move_needs_a_valid_tag_and_a_readable_now():
    assert held_move("new_articles (3)", NOW, NOW) is None
    assert held_move(None, NOW, NOW) is None
    assert held_move("move:up:none", NOW, "not a time") is None


def test_et_date():
    assert str(et_date("2026-11-24T03:00:00Z")) == "2026-11-23"
    assert et_date(None) is None and et_date("x") is None


# ── the direction ───────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("change,want", [
    (22.4, "up"), (-10.0, "down"), (0.004, None), (-0.004, None), (0, None), (0.0, None),
    (0.006, "up"), (float("nan"), None), (float("inf"), None), (float("-inf"), None),
    (True, None), (False, None), (None, None), ("12.0", None), ([1], None), (10 ** 6, "up"),
])
def test_direction_of_junk_and_edges(change, want):
    assert direction_of(change) == want


def test_direction_of_a_huge_int_is_none():
    assert direction_of(10 ** 400) is None


# ── the contexts ────────────────────────────────────────────────────────────────────────────

def test_contexts_refuse_unknown_vocabularies():
    with pytest.raises(ValueError):
        MoveContext(direction="sideways")
    with pytest.raises(ValueError):
        MoveContext(direction="up", basis="vibes")
    with pytest.raises(ValueError):
        MarketContext(basis="sector")
    with pytest.raises(ValueError):
        MoveOutcome(kind="none", direction="up", verdict="maybe", why="x")


def test_index_against_defaults_off_and_must_be_a_bool():
    assert MoveContext(direction="up").index_against is False
    assert MoveContext(direction="up", index_against=True).index_against is True
    for junk in (1, "yes", None):
        with pytest.raises(ValueError):
            MoveContext(direction="up", index_against=junk)


@pytest.mark.parametrize("held,direction,against,lost", [
    (MoveTag("market", "up"), "up", True, True),
    (MoveTag("market", "up"), "up", False, False),      # SPY merely under the threshold
    (MoveTag("market", "up"), "down", True, False),     # a reversal writes anyway
    (MoveTag("company", "up"), "up", True, False),      # a company cause never depends on SPY
    (MoveTag("none", "up"), "up", True, False),
    (None, "up", True, False),
    ("move:up:market", "up", True, False),              # a raw string is no tag
    (MoveTag("market", "up"), "up", 1, False),          # only a real True counts
])
def test_market_card_lost(held, direction, against, lost):
    assert market_card_lost(held, direction, against) is lost


def test_a_market_story_is_allowed_only_on_the_index_basis_with_items():
    rows = ({"headline": "A chip maker names a new chief executive"},)
    assert MarketContext(basis="index", items=rows).story_allowed
    assert not MarketContext(basis="index").story_allowed
    assert not MarketContext(basis="breadth", items=rows).story_allowed


# ── the round trip through the writer ───────────────────────────────────────────────────────

class _Gemini:
    def __init__(self, *answers):
        self.answers = list(answers)

    async def generate_json(self, **kwargs):
        return {"text": json.dumps(self.answers.pop(0))}


class _Table:
    def table(self, _n):
        return self

    def select(self, *_a):
        return self

    def eq(self, *_a):
        return self

    def limit(self, _n):
        return self

    def execute(self):
        return SimpleNamespace(data=[])


@pytest.mark.asyncio
async def test_the_writer_stores_a_tag_the_gate_and_the_read_path_parse():
    from app.services.news_insight_service import (
        _MIN_SERVABLE_PROMPT_VERSION,
        NewsInsightService,
        _clip,
        move_card_today,
    )

    class _Svc(NewsInsightService):
        def __init__(self, gem):
            self.supabase, self.gemini = _Table(), gem
            self._cache, self._inflight, self.rows = {}, {}, []

        def _store(self, scope, card, iid, reason, count, active, sources=None, *, model=None):
            self.rows.append({
                "scope": scope, "headline": card["headline"], "bullets": card["bullets"],
                "sentiment": card["sentiment"], "article_count": count,
                "generated_at": NOW.isoformat(),
                "soft_expires_at": (NOW + timedelta(minutes=15)).isoformat(),
                "prompt_version": _MIN_SERVABLE_PROMPT_VERSION + 1, "sources": sources,
                "trigger_reason": _clip(reason, 200), "ai_model": model,
            })
            return True

    corpus = [{"headline": "Plug Power names a new finance chief", "article_url": "https://x.example/1",
               "summary": "Plug Power named a new chief financial officer.",
               "published_at": (NOW - timedelta(hours=1)).isoformat()}]
    none = {"cause_kind": "none", "cause_refs": [], "market_story": "", "headline": "",
            "points": ["Plug Power named a new chief financial officer."], "sentiment": "neutral",
            "conclusion": ""}
    svc = _Svc(_Gemini(none))
    card = await svc.generate_and_store(
        scope="PLUG", corpus=corpus, inputset_id="iid", price_band="Unusual",
        trigger_reason="new_articles (1) " + "y" * 300, quote=None, market_active=True,
        now=NOW, company_name="Plug Power Inc.", move=MoveContext(direction="down"),
    )
    stored = svc.rows[-1]["trigger_reason"]
    assert card["stored_reason"] == stored and len(stored) <= 200
    assert parse_move_tag(stored) == MoveTag("none", "down")          # the gate's read
    assert held_move(stored, svc.rows[-1]["generated_at"], NOW) == MoveTag("none", "down")
    read = svc._row_to_card(svc.rows[-1], market_active=True)
    assert read["move"] == {"kind": "none", "direction": "down"}
    assert move_card_today(read, NOW) == MoveTag("none", "down")
    outcome = svc.pop_move_outcome("PLUG")
    assert outcome == MoveOutcome(kind="none", direction="down", verdict=im.VERDICT_NO_CAUSE,
                                  why="model_none", written=True)
