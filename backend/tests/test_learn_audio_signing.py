"""Signed-URL minting for Learn narration — the half that makes the gate enforceable.

`test_learn_audio_entitlement.py` covers the LOCKED path (strip the URL). This covers the
ENTITLED path (replace the public URL with a signed one) plus the parsing boundary, and it
exists because three things here fail silently if they regress:

  • **The bucket allowlist is a security boundary, not hygiene.** `sign_many` runs on the
    service-role key, which bypasses RLS, and the URLs it is handed come from authored JSONB
    edited outside the migration flow. Without the allowlist, a card's `audioUrl` pointing at
    `research-pdfs/<user>/<report>.pdf` makes GET /learn/journey mint a signed URL for
    someone else's private report.
  • **The entitled path must not mutate the shared cache entry.** It used to be a bare
    `return response` — that object IS the process-wide 1-hour cache. Rewriting URLs on it in
    place would pin one caller's expiring URLs into every other caller's payload.
  • **A signing failure must degrade, never lock.** A paying user seeing a paywall because
    Storage hiccuped is worse than a URL that doesn't play.

Pure: `_sign_batch_sync` is monkeypatched throughout. Never touches Supabase.
"""
from __future__ import annotations

import asyncio

import pytest

from app.schemas.journey import JourneyLessonResponse, JourneyResponse
from app.schemas.money_moves import MoneyMovesResponse
from app.services import learn_audio_urls as urls
from app.services.learn_audio_gate import (
    redact_journey,
    redact_money_moves,
    sign_journey,
    sign_money_moves,
)

_JOURNEY_AUDIO = "https://xyz.supabase.co/storage/v1/object/public/journey-media/audio/l1c1.m4a"
_MM_AUDIO = "https://xyz.supabase.co/storage/v1/object/public/money-moves-media/audio/enron.m4a"


@pytest.fixture(autouse=True)
def _clean_cache():
    """The memo is module-global; a leak between tests would make order matter."""
    urls.reset_cache_for_tests()
    yield
    urls.reset_cache_for_tests()


@pytest.fixture
def fake_sign(monkeypatch):
    """Deterministic stand-in for the Storage round trip. Records what it was asked to sign."""
    calls: list[tuple[str, list[str]]] = []

    def _fake(bucket: str, paths: list[str]) -> dict[str, str]:
        calls.append((bucket, list(paths)))
        return {
            p: f"https://xyz.supabase.co/storage/v1/object/sign/{bucket}/{p}?token=tok-{len(p)}"
            for p in paths
        }

    monkeypatch.setattr(urls, "_sign_batch_sync", _fake)
    return calls


# ── builders ─────────────────────────────────────────────────────────────────

def _journey(audio=_JOURNEY_AUDIO, story_content=..., **kw) -> JourneyResponse:
    story = {
        "cards": [
            {"type": "title", "headline": "H", "text": "prose", "audioUrl": audio,
             "readAlongWords": [{"start": 0.0, "end": 0.4}]},
            {"type": "content", "headline": "H2", "text": "prose", "audioUrl": audio},
            {"type": "completion", "headline": "Done", "text": "You finished."},
        ]
    }
    lesson = JourneyLessonResponse(
        id="l1", title="T", description="d", level="foundation",
        duration_minutes=5, category="standard", sort_order=1,
        story_content=story if story_content is ... else story_content,
    )
    return JourneyResponse(lessons=[lesson], **kw)


def _money_moves(audio=_MM_AUDIO, **kw) -> MoneyMovesResponse:
    article = {
        "slug": "enron", "title": "Enron", "readTimeMinutes": 6,
        "hasAudioVersion": True, "audioUrl": audio, "audioDurationSeconds": 372,
        "sections": [{"title": "s", "content": [
            {"type": "paragraph", "text": "prose", "readAlong": [{"start": 0.0, "end": 3.2}]},
        ]}],
    }
    articles = kw.pop("articles", [article])
    return MoneyMovesResponse(articles=articles, **kw)


# ── the security boundary ────────────────────────────────────────────────────

@pytest.mark.parametrize("bucket", [
    "research-pdfs",        # the one that matters: private per-user report PDFs
    "avatars",
    "storage",
    "..",
    "book-media-evil",
    "BOOK-MEDIA",           # case-sensitive on purpose; Supabase bucket ids are
])
def test_only_the_three_learn_buckets_are_signable(bucket):
    url = f"https://xyz.supabase.co/storage/v1/object/public/{bucket}/secret/thing.pdf"
    assert urls.parse_storage_url(url) is None


@pytest.mark.parametrize("bucket", ["journey-media", "money-moves-media", "book-media"])
def test_the_learn_buckets_parse(bucket):
    url = f"https://xyz.supabase.co/storage/v1/object/public/{bucket}/audio/x.m4a"
    assert urls.parse_storage_url(url) == (bucket, "audio/x.m4a")


@pytest.mark.asyncio
async def test_a_foreign_bucket_url_survives_signing_untouched(fake_sign):
    """A bad content row must leave the payload as-is, not raise and not get signed."""
    foreign = "https://xyz.supabase.co/storage/v1/object/public/research-pdfs/u/r.pdf"
    out = await sign_journey(_journey(audio=foreign))
    assert out.lessons[0].story_content["cards"][0]["audioUrl"] == foreign
    assert fake_sign == []          # nothing was even asked for


@pytest.mark.parametrize("value", [
    None, "", "not a url", 12345,
    "https://cdn.example.com/audio/x.m4a",
    "https://xyz.supabase.co/storage/v1/object/sign/book-media/audio/x.m4a?token=abc",
    "https://xyz.supabase.co/storage/v1/object/public/",
    "https://xyz.supabase.co/storage/v1/object/public/book-media",
])
def test_parse_returns_none_for_anything_that_is_not_a_public_learn_object(value):
    assert urls.parse_storage_url(value) is None


# ── non-mutation on the ENTITLED path (P0) ───────────────────────────────────

@pytest.mark.asyncio
async def test_sign_journey_does_not_mutate_the_shared_cache_entry(fake_sign):
    response = _journey()
    before = response.model_dump_json()

    out = await sign_journey(response)

    # The mapping must be non-empty or the `if not mapping: return response` short-circuit
    # would make this pass vacuously.
    assert "/object/sign/" in out.model_dump_json()
    assert response.model_dump_json() == before
    assert response.lessons[0].story_content["cards"][0]["audioUrl"] == _JOURNEY_AUDIO
    assert out.lessons[0].story_content is not response.lessons[0].story_content


@pytest.mark.asyncio
async def test_sign_money_moves_does_not_mutate_the_shared_cache_entry(fake_sign):
    response = _money_moves()
    before = response.model_dump_json()

    out = await sign_money_moves(response)

    assert "/object/sign/" in out.model_dump_json()
    assert response.model_dump_json() == before
    assert response.articles[0]["audioUrl"] == _MM_AUDIO
    assert out.articles[0] is not response.articles[0]


@pytest.mark.asyncio
async def test_alternating_paid_and_free_callers_each_get_the_right_payload(fake_sign):
    """The end-to-end shape of the cache trap: one shared object, many plans, any order."""
    shared = _journey()

    paid_1 = (await sign_journey(shared)).model_dump_json()
    free = redact_journey(shared, "pro").model_dump_json()
    paid_2 = (await sign_journey(shared)).model_dump_json()

    assert "/object/sign/" in paid_1
    assert "/storage/v1/object/" not in free
    # The regression this guards: after a free request, a paying caller still gets audio.
    assert "/object/sign/" in paid_2


# ── what actually goes over the wire ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_entitled_bodies_carry_signed_urls_and_no_public_ones(fake_sign):
    """Asserted on the SERIALISED body, so a field added later that carries a Storage URL
    fails here rather than in production."""
    for body in (
        (await sign_journey(_journey())).model_dump_json(),
        (await sign_money_moves(_money_moves())).model_dump_json(),
    ):
        assert "/object/sign/" in body
        assert "token=" in body
        assert "/object/public/" not in body


@pytest.mark.asyncio
async def test_signing_leaves_prose_and_read_along_intact(fake_sign):
    out = await sign_journey(_journey())
    card = out.lessons[0].story_content["cards"][0]
    assert card["text"] == "prose"
    assert card["readAlongWords"] == [{"start": 0.0, "end": 0.4}]
    assert out.audio_locked is False and out.tier_required is None


# ── batching ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_repeated_urls_are_signed_once_and_batched_per_bucket(fake_sign):
    """Journey carries ~207 clips; per-URL signing would be ~207 sequential round trips."""
    await sign_journey(_journey())
    assert len(fake_sign) == 1                     # one bucket, one call
    bucket, paths = fake_sign[0]
    assert bucket == "journey-media"
    assert paths == ["audio/l1c1.m4a"]             # the duplicate card collapsed


@pytest.mark.asyncio
async def test_the_second_request_reuses_the_memo(fake_sign):
    await sign_journey(_journey())
    await sign_journey(_journey())
    assert len(fake_sign) == 1                     # no second round trip


@pytest.mark.asyncio
async def test_concurrent_cold_callers_mint_once(fake_sign):
    await asyncio.gather(*(sign_journey(_journey()) for _ in range(8)))
    assert len(fake_sign) == 1                     # the per-bucket lock did its job


# ── degradation ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_signing_exception_leaves_the_public_url_and_does_not_lock(monkeypatch):
    def _boom(bucket, paths):
        raise RuntimeError("storage is down")
    monkeypatch.setattr(urls, "_sign_batch_sync", _boom)

    out = await sign_money_moves(_money_moves())

    # Degrades to what was already on the wire — which still plays while the buckets are
    # public. A paying user must NOT be shown a lock because Storage hiccuped.
    assert out.articles[0]["audioUrl"] == _MM_AUDIO
    assert out.audio_locked is False
    assert out.articles[0]["hasAudioVersion"] is True


@pytest.mark.asyncio
async def test_a_signing_timeout_degrades_the_same_way(monkeypatch):
    monkeypatch.setattr(urls, "_SIGN_TIMEOUT_SECONDS", 0.01)

    def _slow(bucket, paths):
        import time
        time.sleep(0.2)
        return {}
    monkeypatch.setattr(urls, "_sign_batch_sync", _slow)

    out = await sign_journey(_journey())
    assert out.lessons[0].story_content["cards"][0]["audioUrl"] == _JOURNEY_AUDIO


@pytest.mark.asyncio
async def test_a_per_object_error_row_leaves_that_one_url_alone(monkeypatch):
    """Supabase reports a missing object in the row, not as an exception."""
    monkeypatch.setattr(urls, "_sign_batch_sync", lambda bucket, paths: {})
    out = await sign_journey(_journey())
    assert out.lessons[0].story_content["cards"][0]["audioUrl"] == _JOURNEY_AUDIO


# ── degenerate content ───────────────────────────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("story", [
    None, {}, {"cards": None}, {"cards": "nope"}, {"cards": []},
    {"cards": [None, "str", 7]},
    {"cards": [{"type": "content"}]},                       # no audioUrl key
    {"cards": [{"type": "content", "audioUrl": None}]},
    {"cards": [{"type": "content", "audioUrl": 123}]},
])
async def test_malformed_story_content_never_raises(fake_sign, story):
    out = await sign_journey(_journey(story_content=story))
    assert out.lessons[0].story_content == story


@pytest.mark.asyncio
@pytest.mark.parametrize("articles", [
    # `MoneyMovesResponse.articles` is `List[Dict[str, Any]]`, so pydantic rejects a non-dict
    # article at construction — the `isinstance` guards in the gate cover a shape that cannot
    # reach it through the schema. These are the malformed articles that CAN.
    [], [{}], [{"audioUrl": None}], [{"audioUrl": ""}],
    [{"audioUrl": _MM_AUDIO, "sections": None}],
    [{"audioUrl": _MM_AUDIO, "sections": [None, "str", 7]}],
])
async def test_malformed_articles_never_raise(fake_sign, articles):
    out = await sign_money_moves(_money_moves(articles=articles))
    assert len(out.articles) == len(articles)


@pytest.mark.asyncio
async def test_an_article_with_no_narration_stays_no_narration_not_locked(fake_sign):
    """`hasAudioVersion: False` means "never narrated". Signing must not turn that into a
    lock, or the client shows an upgrade offer for audio that does not exist."""
    article = {"slug": "s", "title": "T", "hasAudioVersion": False, "sections": []}
    out = await sign_money_moves(_money_moves(articles=[article]))
    assert out.articles[0]["hasAudioVersion"] is False
    assert "audioUrl" not in out.articles[0]
    assert out.audio_locked is False


# ── the TTL invariant ────────────────────────────────────────────────────────

def test_a_signature_outlives_the_longest_asset_by_a_wide_margin():
    """The whole safety argument: a URL handed out at the end of its reuse window still has
    (SIGNED - CACHE) left, and the longest single asset is one 58:22 book."""
    headroom = urls._SIGNED_TTL_SECONDS - urls._CACHE_TTL_SECONDS
    longest_asset_seconds = 3502
    assert headroom > longest_asset_seconds * 4
