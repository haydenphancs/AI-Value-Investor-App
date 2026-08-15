"""Investor Journey narration is FREE on every tier — the endpoint-level proof.

There was no endpoint test for `/learn/journey` before this: the entitlement and signing
suites are both service-level, so the *route's* behaviour — which is where the product
decision actually lives — was unpinned. These tests call the handlers directly with a
stubbed content service.

What is pinned here, and why each one is worth a test:

  • A free account, and a signed-out guest, hear the Journey. That is the feature.
  • A garbage/unknown tier ALSO hears it. The Journey gate fails OPEN, the exact inversion
    of every other gate in `entitlements`, so a well-meaning "make it fail closed like its
    neighbours" edit is caught here.
  • Money Moves and Books are STILL locked for the same free caller. This is the guard
    against someone "simplifying" the two Learn gates back into one and giving away the
    paid library. It is the highest-value test in the file.
  • A free body carries `/object/sign/` and NEVER a public `journey-media` URL —
    `journey-media` is private (migration 128), so a public URL on the wire is a dead link
    and silence, not a leak. That failure is invisible server-side.

Pure: the content service and the Storage round trip are both stubbed. No network.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from app.api.v1.endpoints import learn as learn_ep
from app.schemas.journey import JourneyLessonResponse, JourneyResponse
from app.schemas.money_moves import MoneyMovesResponse
from app.services import learn_audio_urls as urls

_JOURNEY_AUDIO = "https://xyz.supabase.co/storage/v1/object/public/journey-media/audio/l1c1.m4a"
_JOURNEY_IMAGE = "https://xyz.supabase.co/storage/v1/object/public/journey-images/lessons/l1.hero.jpg"
_MM_AUDIO = "https://xyz.supabase.co/storage/v1/object/public/money-moves-media/audio/enron.m4a"

# The identity dicts `get_learn_identity` actually produces (dependencies.py).
_GUEST = {"id": "8f14e45f-ceea-5e78-b2ea-3f7b1a1c9d00", "email": "guest@local",
          "tier": "free", "is_guest": True}
_FREE = {"id": "u-1", "email": "a@b.c", "tier": "free", "is_guest": False}
_PRO = {"id": "u-2", "email": "p@b.c", "tier": "pro", "is_guest": False}
_GARBAGE = {"id": "u-3", "email": "g@b.c", "tier": "enterprise", "is_guest": False}
_NO_TIER = {"id": "u-4", "email": "n@b.c", "is_guest": False}


@pytest.fixture(autouse=True)
def _clean_cache():
    urls.reset_cache_for_tests()
    yield
    urls.reset_cache_for_tests()


@pytest.fixture(autouse=True)
def fake_sign(monkeypatch):
    def _fake(bucket: str, paths: list[str]) -> dict[str, str]:
        return {p: f"https://xyz.supabase.co/storage/v1/object/sign/{bucket}/{p}?token=t"
                for p in paths}

    monkeypatch.setattr(urls, "_sign_batch_sync", _fake)


def _journey_response() -> JourneyResponse:
    story = {
        "cards": [
            {"type": "title", "headline": "H", "text": "prose", "audioUrl": _JOURNEY_AUDIO,
             "imageUrl": _JOURNEY_IMAGE, "videoUrl": None,
             "readAlongWords": [{"start": 0.0, "end": 0.4}]},
            {"type": "content", "headline": "H2", "text": "more prose",
             "audioUrl": _JOURNEY_AUDIO, "imageUrl": None},
            {"type": "completion", "headline": "Done", "text": "You finished."},
        ]
    }
    lesson = JourneyLessonResponse(
        id="l1", title="Compound Interest", description="d", level="foundation",
        duration_minutes=3, category="standard", sort_order=1, story_content=story,
    )
    return JourneyResponse(lessons=[lesson])


def _money_moves_response() -> MoneyMovesResponse:
    return MoneyMovesResponse(articles=[{
        "slug": "enron", "title": "Enron", "readTimeMinutes": 6,
        "hasAudioVersion": True, "audioUrl": _MM_AUDIO, "audioDurationSeconds": 372,
        "sections": [{"title": "s", "content": [
            {"type": "paragraph", "text": "prose", "readAlong": [{"start": 0.0, "end": 1.0}]},
        ]}],
    }])


@pytest.fixture(autouse=True)
def stub_services(monkeypatch):
    class _Journey:
        async def get_journey(self):
            return _journey_response()

    class _MoneyMoves:
        async def get_money_moves(self):
            return _money_moves_response()

    monkeypatch.setattr(learn_ep, "get_journey_content_service", lambda: _Journey())
    monkeypatch.setattr(learn_ep, "get_money_moves_content_service", lambda: _MoneyMoves())


def _cards(response: JourneyResponse) -> list[dict]:
    return response.lessons[0].story_content["cards"]


# ── the feature ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize(
    "identity", [_FREE, _GUEST, _PRO, _GARBAGE, _NO_TIER],
    ids=["free", "signed-out-guest", "pro", "garbage-tier", "tier-key-missing"],
)
async def test_every_caller_hears_journey_narration(identity):
    out = await learn_ep.get_journey(user=identity)

    narrated = [c for c in _cards(out) if c["type"] != "completion"]
    assert narrated, "fixture must contain narrated cards or this passes vacuously"
    for card in narrated:
        assert card.get("audioUrl"), f"{identity['id']}: narration withheld"
        assert "/object/sign/" in card["audioUrl"]
    assert _cards(out)[0].get("readAlongWords"), "read-along timings withheld"

    assert out.audio_locked is False
    assert out.tier_required is None


@pytest.mark.asyncio
async def test_a_free_journey_body_carries_no_public_journey_media_url():
    """`journey-media` is PRIVATE (migration 128). A public-form URL surviving to the wire
    is a dead link and silence on device — a failure with no server-side symptom."""
    blob = (await learn_ep.get_journey(user=_FREE)).model_dump_json()

    assert "/object/sign/journey-media/" in blob
    assert "/object/public/journey-media/" not in blob


@pytest.mark.asyncio
async def test_lesson_artwork_stays_public_and_unsigned_for_a_free_caller():
    """Artwork lives in the separate PUBLIC `journey-images` bucket so it caches
    indefinitely. Narration is signed; artwork must not be."""
    out = await learn_ep.get_journey(user=_FREE)
    title_card = _cards(out)[0]

    assert title_card["imageUrl"] == _JOURNEY_IMAGE
    assert "/object/public/journey-images/" in title_card["imageUrl"]
    assert "/object/sign/" not in title_card["imageUrl"]


# ── the paid library is untouched ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_money_moves_is_still_locked_for_a_free_caller():
    """The guard against freeing the whole Learn library by 'simplifying' one gate."""
    out = await learn_ep.get_money_moves(user=_FREE)

    assert out.audio_locked is True
    assert out.tier_required == "pro"
    article = out.articles[0]
    assert "audioUrl" not in article
    assert "/storage/v1/object/" not in json.dumps(out.articles)


@pytest.mark.asyncio
async def test_money_moves_is_still_locked_for_a_guest():
    out = await learn_ep.get_money_moves(user=_GUEST)
    assert out.audio_locked is True


@pytest.mark.asyncio
async def test_money_moves_is_unlocked_for_pro():
    out = await learn_ep.get_money_moves(user=_PRO)
    assert out.audio_locked is False
    assert "/object/sign/" in out.model_dump_json()


@pytest.mark.asyncio
async def test_the_same_free_caller_gets_journey_but_not_money_moves():
    """The product decision in one assertion: one identity, two Learn products, opposite
    outcomes. If both ever agree again, this is where it surfaces."""
    journey = await learn_ep.get_journey(user=_FREE)
    money_moves = await learn_ep.get_money_moves(user=_FREE)

    assert journey.audio_locked is False
    assert money_moves.audio_locked is True
    assert "/object/sign/" in journey.model_dump_json()
    assert "/object/sign/" not in money_moves.model_dump_json()


# ── the client half ──────────────────────────────────────────────────────────
#
# The backend tests above can ALL pass while users hear nothing: the Journey lock was
# never on the wire. `JourneyContentStore` decodes only `lessons` — it has never read
# `audio_locked` — so the gate lived entirely in four client-side `isUnlocked` reads.
# These scans are the only automated protection against the client half being reverted.
#
# Comments are stripped before scanning. The files deliberately still MENTION
# LearnAudioEntitlement in prose ("do not add this back"), and a scan that counted those
# would pass while the guard was live — the exact vacuity these are meant to avoid.

_REPO = Path(__file__).resolve().parents[2]
_LESSON_VIEW = _REPO / "frontend/ios/ios/Views/Organisms/LessonTopicCardView.swift"
_VOICE_MANAGER = _REPO / "frontend/ios/ios/Services/AIVoiceManager.swift"
_AUDIO_MANAGER = _REPO / "frontend/ios/ios/Services/AudioManager.swift"


def _code_only(path: Path) -> str:
    """Swift source with // line comments and /* */ blocks removed."""
    src = re.sub(r"/\*.*?\*/", "", path.read_text(), flags=re.S)
    return "\n".join(re.sub(r"//.*$", "", line) for line in src.splitlines())


def test_the_journey_view_does_not_consult_the_paid_entitlement():
    code = _code_only(_LESSON_VIEW)
    assert "LearnAudioEntitlement" not in code, (
        "LessonTopicCardView is gating on the paid Learn entitlement again — Journey "
        "narration is free on every tier")
    assert "lock.fill" not in code, "the Journey play control must never render a lock"
    assert "learnAudioPaywall" not in code, (
        "nothing in the Journey cover can raise upgradeRequested; the paywall presenter "
        "is unreachable there")


def test_the_journey_audio_engine_does_not_consult_the_paid_entitlement():
    code = _code_only(_VOICE_MANAGER)
    assert "LearnAudioEntitlement" not in code, (
        "AIVoiceManager is the Journey engine — gating it silences free users no matter "
        "what the button looks like")
    assert "isUnlocked" not in code


def test_the_paid_engine_still_consults_the_entitlement():
    """The other half of the divergence. If this ever goes quiet, Money Moves and the book
    library became free by accident — which is the failure the scans above cannot see."""
    code = _code_only(_AUDIO_MANAGER)
    assert "LearnAudioEntitlement" in code
    assert "stopForLostEntitlement" in code


def test_the_scans_are_not_vacuous():
    """Three absence assertions are only worth anything if the window is real and the
    stripper strips exactly what it claims to. A scan that reads an empty string, or that
    silently deletes the code it was supposed to search, passes forever."""
    for path in (_LESSON_VIEW, _VOICE_MANAGER, _AUDIO_MANAGER):
        code = _code_only(path)
        assert path.exists(), f"{path.name} moved — the scan is pointing at nothing"
        assert len(code) > 2000, f"{path.name}: scan window looks empty"
        assert "func " in code, f"{path.name}: window contains no code"

    # The stripper removes comments and ONLY comments.
    sample = (
        'let a = 1  // LearnAudioEntitlement.shared.isUnlocked\n'
        '/* lock.fill\n   LearnAudioEntitlement */\n'
        'guard LearnAudioEntitlement.shared.isUnlocked else { return }\n'
    )
    stripped = "\n".join(
        re.sub(r"//.*$", "", ln) for ln in re.sub(r"/\*.*?\*/", "", sample, flags=re.S).splitlines()
    )
    assert stripped.count("LearnAudioEntitlement") == 1, "stripper ate real code, or missed a comment"
    assert "lock.fill" not in stripped
    assert "let a = 1" in stripped
