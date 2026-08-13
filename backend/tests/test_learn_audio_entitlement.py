"""Wiser (Learn) narration gate — read free, listen with Pro.

The packaging: every word of all 27 Journey lessons, 13 Money Moves articles and 10 books is
free on every tier, and so is progress tracking. What is paid is the produced NARRATION and
the read-along highlighting it drives.

Three things here are load-bearing beyond the happy path:

  • **The redactors must not mutate their input.** Both content services hold a process-wide
    1-hour cache of ONE response object shared by every caller, so an in-place strip would
    withhold narration from every PAYING user until the next rebuild — the same trap the
    signals and whale gates are shaped around.
  • **`hasAudioVersion` must survive the lock.** It means "narration exists", and the client
    hides the Listen control entirely when false. If locking cleared it, the upgrade offer
    would be invisible on exactly the articles that would sell it.
  • **Text must survive untouched.** The whole premise is that the library stays readable;
    a redactor that trimmed prose would be a different product.

Pure module — no network, no Supabase, no fixtures.
"""
from __future__ import annotations

import json

import pytest

from app.schemas.journey import JourneyLessonResponse, JourneyResponse
from app.schemas.money_moves import MoneyMovesResponse
from app.services import entitlements as ent
from app.services.learn_audio_gate import redact_journey, redact_money_moves


# ── builders ─────────────────────────────────────────────────────────────────

_JOURNEY_AUDIO = "https://xyz.supabase.co/storage/v1/object/public/journey-media/audio/l1c1.m4a"
_MM_AUDIO = "https://xyz.supabase.co/storage/v1/object/public/money-moves-media/audio/enron.m4a"
_LESSON_PROSE = "A stock is a share of a business, not a ticker that wiggles."
_ARTICLE_PROSE = "Enron's revenue grew while its cash flow did not."


def _journey(**overrides) -> JourneyResponse:
    story = {
        "lessonLabel": "Lesson 1",
        "lessonNumber": 1,
        "totalLessonsInLevel": 7,
        "estimatedMinutes": 5,
        "cards": [
            {
                "type": "title", "headline": "Stock vs. Business", "text": _LESSON_PROSE,
                "audioUrl": _JOURNEY_AUDIO, "imageUrl": None, "videoUrl": None,
                "readAlongWords": [{"start": 0.0, "end": 0.4}, {"start": 0.4, "end": 0.9}],
            },
            {
                "type": "content", "headline": "Why it matters", "text": _LESSON_PROSE,
                "audioUrl": _JOURNEY_AUDIO, "readAlongWords": [{"start": 0.0, "end": 0.5}],
            },
            # The completion card carries no narration in the real data.
            {"type": "completion", "headline": "Nice work", "text": "You finished."},
        ],
    }
    story.update(overrides.pop("story", {}))
    lesson = JourneyLessonResponse(
        id="l1", title="Stock vs. Business", description="d",
        level="foundation", duration_minutes=5, category="standard", sort_order=1,
        story_content=overrides.pop("story_content", story),
    )
    return JourneyResponse(lessons=[lesson], **overrides)


def _money_moves(**overrides) -> MoneyMovesResponse:
    article = {
        "slug": "the-fall-of-enron",
        "title": "The Fall of Enron",
        "readTimeMinutes": 6,
        "hasAudioVersion": True,
        "audioUrl": _MM_AUDIO,
        "audioDurationSeconds": 372,
        "sections": [
            {
                "title": "What happened",
                "content": [
                    {
                        "type": "paragraph", "text": _ARTICLE_PROSE,
                        "readAlong": [{"start": 0.0, "end": 3.2}],
                    },
                    {
                        "type": "bulletList", "items": ["a", "b"],
                        "itemsReadAlong": [[{"start": 0.0, "end": 1.0}]],
                    },
                ],
            }
        ],
    }
    articles = overrides.pop("articles", [article])
    return MoneyMovesResponse(articles=articles, **overrides)


# ── the tier table ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("tier", ["pro", "premium", " Pro ", "PREMIUM"])
def test_paid_tiers_hear_narration(tier):
    assert ent.learn_audio_unlocked(tier) is True
    assert ent.required_tier_for_learn_audio(tier) is None


@pytest.mark.parametrize("tier", ["free", "FREE", "", None, "enterprise", 0, [], {}])
def test_free_unknown_and_garbage_tiers_are_locked(tier):
    assert ent.learn_audio_unlocked(tier) is False
    assert ent.required_tier_for_learn_audio(tier) == ent.TIER_PRO


def test_all_three_gates_share_one_floor():
    """Signals, whale detail and Learn audio are deliberately the same frozenset so they
    cannot drift into 'Pro unlocks two of them but only Max unlocks the third' — which no
    pricing page would ever describe."""
    assert ent.LEARN_AUDIO_UNLOCKED_TIERS is ent.SIGNALS_UNLOCKED_TIERS
    assert ent.WHALE_DETAIL_UNLOCKED_TIERS is ent.SIGNALS_UNLOCKED_TIERS


# ── Journey redaction ────────────────────────────────────────────────────────

def test_locked_journey_withholds_narration():
    out = redact_journey(_journey(), "pro")
    cards = out.lessons[0].story_content["cards"]
    assert all("audioUrl" not in c for c in cards)
    assert all("readAlongWords" not in c for c in cards)
    assert out.audio_locked is True
    assert out.tier_required == "pro"


def test_locked_journey_keeps_every_word():
    """The premise of the whole gate: the lesson still reads end to end."""
    out = redact_journey(_journey(), "pro")
    cards = out.lessons[0].story_content["cards"]
    assert len(cards) == 3
    assert all(c["text"] for c in cards)
    assert cards[0]["headline"] == "Stock vs. Business"
    assert out.lessons[0].title == "Stock vs. Business"
    assert out.lessons[0].story_content["estimatedMinutes"] == 5


def test_no_journey_audio_url_survives_serialisation():
    """Asserted on the JSON, not the object graph, so a card field added later that carries
    a Storage URL fails here rather than in production."""
    blob = redact_journey(_journey(), "pro").model_dump_json()
    assert _JOURNEY_AUDIO not in blob
    assert "journey-media" not in blob
    assert _LESSON_PROSE in blob          # ...and the prose is still there


# ── Money Moves redaction ────────────────────────────────────────────────────

def test_locked_money_moves_withholds_narration():
    out = redact_money_moves(_money_moves(), "pro")
    article = out.articles[0]
    assert "audioUrl" not in article
    assert "audioDurationSeconds" not in article
    blocks = article["sections"][0]["content"]
    assert all("readAlong" not in b for b in blocks)
    assert all("itemsReadAlong" not in b for b in blocks)
    assert out.audio_locked is True
    assert out.tier_required == "pro"


def test_has_audio_version_survives_the_lock():
    """THE distinction that makes the offer visible. `hasAudioVersion` means 'narration
    exists' and the client hides Listen entirely when it is false — clearing it here would
    make a locked article indistinguishable from one that was never narrated."""
    out = redact_money_moves(_money_moves(), "pro")
    assert out.articles[0]["hasAudioVersion"] is True


def test_an_article_that_was_never_narrated_stays_that_way():
    """Not every article must have audio. One with `hasAudioVersion: false` has to keep it,
    so the client shows nothing at all rather than a lock that unlocks silence."""
    silent = {"slug": "x", "title": "X", "hasAudioVersion": False, "sections": []}
    out = redact_money_moves(_money_moves(articles=[silent]), "pro")
    assert out.articles[0]["hasAudioVersion"] is False


def test_locked_money_moves_keeps_every_word():
    out = redact_money_moves(_money_moves(), "pro")
    blocks = out.articles[0]["sections"][0]["content"]
    assert blocks[0]["text"] == _ARTICLE_PROSE
    assert blocks[1]["items"] == ["a", "b"]
    assert out.articles[0]["title"] == "The Fall of Enron"


def test_no_money_moves_audio_url_survives_serialisation():
    blob = redact_money_moves(_money_moves(), "pro").model_dump_json()
    assert _MM_AUDIO not in blob
    assert "money-moves-media" not in blob
    assert _ARTICLE_PROSE in blob


# ── the shared-cache regression (P0) ─────────────────────────────────────────

def test_journey_redaction_does_not_mutate_its_input():
    """`get_journey()` returns the object held in a process-wide 1-hour cache, shared by
    every caller regardless of plan. An in-place strip would silence every PAYING user for
    up to an hour, long after the free request that caused it."""
    response = _journey()
    before = response.model_dump_json()

    redact_journey(response, "pro")

    assert response.model_dump_json() == before
    assert response.lessons[0].story_content["cards"][0]["audioUrl"] == _JOURNEY_AUDIO
    assert response.audio_locked is False


def test_money_moves_redaction_does_not_mutate_its_input():
    response = _money_moves()
    before = response.model_dump_json()

    redact_money_moves(response, "pro")

    assert response.model_dump_json() == before
    assert response.articles[0]["audioUrl"] == _MM_AUDIO
    assert response.articles[0]["sections"][0]["content"][0]["readAlong"]


def test_redactions_return_new_objects_all_the_way_down():
    """A non-mutating call that still hands back the SAME nested dict is a landmine: any
    later write downstream lands in the cache."""
    j, m = _journey(), _money_moves()
    rj, rm = redact_journey(j, "pro"), redact_money_moves(m, "pro")
    assert rj is not j and rj.lessons[0].story_content is not j.lessons[0].story_content
    assert rm is not m and rm.articles[0] is not m.articles[0]


def test_a_free_request_then_a_paid_one_both_get_the_right_thing():
    """Order must not matter — the end-to-end form of the cache regression."""
    cached_journey, cached_mm = _journey(), _money_moves()

    free_j = redact_journey(cached_journey, "pro")
    free_m = redact_money_moves(cached_mm, "pro")
    assert free_j.audio_locked and free_m.audio_locked

    # A paid caller is served the cache object directly, unredacted.
    assert cached_journey.lessons[0].story_content["cards"][0]["audioUrl"] == _JOURNEY_AUDIO
    assert cached_mm.articles[0]["audioUrl"] == _MM_AUDIO
    assert cached_journey.audio_locked is False


# ── degenerate inputs ────────────────────────────────────────────────────────

def test_lesson_with_no_story_content_does_not_raise():
    out = redact_journey(_journey(story_content=None), "pro")
    assert out.lessons[0].story_content is None
    assert out.audio_locked is True


def test_malformed_story_content_passes_through_rather_than_500ing():
    """`story_content` is authored JSONB a bad row edit can malform. A 500 on the Learn tab
    is worse than one un-stripped card, so the walk is shape-tolerant."""
    for bad in ({"cards": "not-a-list"}, {"no_cards_key": 1}, {"cards": ["str", None, 7]}):
        out = redact_journey(_journey(story_content=bad), "pro")
        assert out.audio_locked is True


def test_article_with_no_sections_or_odd_shapes_does_not_raise():
    for bad in ({"slug": "x"}, {"slug": "x", "sections": "nope"},
                {"slug": "x", "sections": [{"content": "nope"}]},
                {"slug": "x", "sections": [{"content": [None, "str", 3]}]}):
        out = redact_money_moves(_money_moves(articles=[bad]), "pro")
        assert out.audio_locked is True


def test_empty_catalogues_do_not_raise():
    assert redact_journey(JourneyResponse(lessons=[]), "pro").lessons == []
    assert redact_money_moves(MoneyMovesResponse(articles=[]), "pro").articles == []


def test_a_card_or_block_without_narration_is_left_alone():
    """The completion card and un-narrated blocks must survive byte-identically — popping a
    key that isn't there must not invent one or drop a sibling."""
    out = redact_journey(_journey(), "pro")
    completion = out.lessons[0].story_content["cards"][2]
    assert completion == {"type": "completion", "headline": "Nice work", "text": "You finished."}


# ── the response shapes iOS decodes ──────────────────────────────────────────

def test_unlocked_responses_default_to_not_locked():
    """Defaulted so an already-shipped build, which knows neither field, decodes unchanged —
    and so a paid caller's untouched response reads as unlocked without anyone setting it."""
    assert json.loads(_journey().model_dump_json())["audio_locked"] is False
    assert json.loads(_money_moves().model_dump_json())["tier_required"] is None
