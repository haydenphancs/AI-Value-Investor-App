"""GET /learn/books/audio — the catalog, the gate, and the drift guard.

Book narration is the one Learn product with no content table: the audio used to be ten
PUBLIC Storage URLs compiled straight into `BookAudioContent.swift`, so one network request
(or one `strings` pass over the binary) handed anyone all 276 MB on any plan. This endpoint
replaces that with signed URLs minted per request.

The drift guard at the bottom is the point of the file: two generated artifacts — the Swift
constants and the backend catalog — are built from the SAME manifests, and nothing else
would notice if they stopped agreeing.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.schemas.learn_books_audio import BooksAudioResponse
from app.services import book_audio_service as svc
from app.services import learn_audio_urls as urls

_REPO = Path(__file__).resolve().parents[2]
_SWIFT = _REPO / "frontend/ios/ios/Models/BookAudioContent.swift"


@pytest.fixture(autouse=True)
def _clean_cache():
    urls.reset_cache_for_tests()
    yield
    urls.reset_cache_for_tests()


@pytest.fixture
def fake_sign(monkeypatch):
    def _fake(bucket: str, paths: list[str]) -> dict[str, str]:
        return {p: f"https://xyz.supabase.co/storage/v1/object/sign/{bucket}/{p}?token=t" for p in paths}
    monkeypatch.setattr(urls, "_sign_batch_sync", _fake)


# ── the catalog ──────────────────────────────────────────────────────────────

def test_the_catalog_loaded_from_the_manifests():
    """Empty means the deploy dropped backend/data/book_audio/ and the feature is dead."""
    assert svc.BOOK_AUDIO_CATALOG, "no book audio manifests were loaded"


def test_every_catalog_entry_is_a_plausible_object_path():
    for order, path in svc.BOOK_AUDIO_CATALOG.items():
        assert isinstance(order, int) and order > 0
        assert re.fullmatch(r"audio/\d+_[a-z0-9-]+\.m4a", path), (order, path)


# ── the gate ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_locked_caller_gets_an_empty_list_and_the_upgrade_fields(fake_sign):
    out = await svc.get_books_audio(unlocked=False, tier_required="pro")
    assert out.books == []
    assert out.audio_locked is True
    assert out.tier_required == "pro"
    # The whole point: no Storage URL of any kind reaches a locked caller.
    assert "/storage/v1/object/" not in out.model_dump_json()


@pytest.mark.asyncio
async def test_an_entitled_caller_gets_one_signed_url_per_book(fake_sign):
    out = await svc.get_books_audio(unlocked=True, tier_required=None)

    assert len(out.books) == len(svc.BOOK_AUDIO_CATALOG)
    assert out.audio_locked is False and out.tier_required is None
    assert [b.curriculum_order for b in out.books] == sorted(svc.BOOK_AUDIO_CATALOG)

    body = out.model_dump_json()
    assert "/object/sign/" in body and "token=" in body
    assert "/object/public/" not in body


@pytest.mark.asyncio
async def test_signing_is_batched_into_one_round_trip(monkeypatch):
    calls: list[list[str]] = []

    def _fake(bucket, paths):
        calls.append(list(paths))
        return {p: f"https://x/storage/v1/object/sign/{bucket}/{p}?token=t" for p in paths}
    monkeypatch.setattr(urls, "_sign_batch_sync", _fake)

    await svc.get_books_audio(unlocked=True, tier_required=None)
    assert len(calls) == 1, "all ten books must sign in a single Storage call"


# ── degradation ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_signing_failure_omits_books_but_does_not_lock(monkeypatch):
    """A third state: no URLs right now, but NOT a paywall. Showing a lock because Storage
    hiccuped would tell a paying user to upgrade the plan they already have."""
    def _boom(bucket, paths):
        raise RuntimeError("storage down")
    monkeypatch.setattr(urls, "_sign_batch_sync", _boom)

    out = await svc.get_books_audio(unlocked=True, tier_required=None)
    assert out.books == []
    assert out.audio_locked is False
    assert out.tier_required is None
    # ...and it must SAY so, or the client cannot tell this from "no book is narrated" and
    # overwrites its ten cached URLs with an empty dict.
    assert out.temporarily_unavailable is True


@pytest.mark.asyncio
async def test_a_total_signing_failure_is_not_a_plain_unlocked_empty_envelope(monkeypatch):
    """The exact shape that used to wipe the client's memo."""
    monkeypatch.setattr(urls, "_sign_batch_sync", lambda bucket, paths: {})
    out = await svc.get_books_audio(unlocked=True, tier_required=None)
    plain_empty = BooksAudioResponse(books=[], audio_locked=False, tier_required=None)
    assert out.model_dump_json() != plain_empty.model_dump_json()


@pytest.mark.asyncio
async def test_an_empty_catalog_is_also_flagged_temporarily_unavailable(monkeypatch, fake_sign):
    """A deploy that dropped backend/data/book_audio/ is an outage, not an empty library."""
    monkeypatch.setattr(svc, "BOOK_AUDIO_CATALOG", {})
    out = await svc.get_books_audio(unlocked=True, tier_required=None)
    assert out.books == [] and out.audio_locked is False
    assert out.temporarily_unavailable is True


@pytest.mark.asyncio
async def test_one_unsignable_book_does_not_take_the_others_with_it(monkeypatch):
    skipped = svc.BOOK_AUDIO_CATALOG[min(svc.BOOK_AUDIO_CATALOG)]

    def _partial(bucket, paths):
        return {
            p: f"https://x/storage/v1/object/sign/{bucket}/{p}?token=t"
            for p in paths if p != skipped
        }
    monkeypatch.setattr(urls, "_sign_batch_sync", _partial)

    out = await svc.get_books_audio(unlocked=True, tier_required=None)
    assert len(out.books) == len(svc.BOOK_AUDIO_CATALOG) - 1
    assert min(svc.BOOK_AUDIO_CATALOG) not in {b.curriculum_order for b in out.books}


@pytest.mark.asyncio
async def test_an_empty_catalog_degrades_instead_of_raising(monkeypatch, fake_sign):
    monkeypatch.setattr(svc, "BOOK_AUDIO_CATALOG", {})
    out = await svc.get_books_audio(unlocked=True, tier_required=None)
    assert out.books == [] and out.audio_locked is False


# ── schema parity with the iOS decoder ───────────────────────────────────────

def test_the_wire_keys_match_the_swift_coding_keys():
    """iOS decodes these exact snake_case keys in `BookAudioURLStore.swift`."""
    body = BooksAudioResponse(books=[], audio_locked=True, tier_required="pro").model_dump()
    assert set(body) == {"books", "audio_locked", "tier_required", "temporarily_unavailable"}

    fields = BooksAudioResponse.model_fields
    # Defaulted so an already-shipped client decodes a response without them.
    assert fields["audio_locked"].default is False
    assert fields["tier_required"].default is None
    assert fields["temporarily_unavailable"].default is False


def test_the_three_states_are_distinguishable_on_the_wire():
    """locked / available / temporarily-unavailable must not collapse into one shape.

    They did: a signing outage and "entitled, nothing narrated" were both
    `{"books": [], "audio_locked": false}`, and the client's memo — ten working signed URLs —
    was overwritten by the empty list, killing every Play button until the next fetch.
    """
    locked = BooksAudioResponse(books=[], audio_locked=True, tier_required="pro")
    outage = BooksAudioResponse(books=[], audio_locked=False, temporarily_unavailable=True)
    assert (locked.audio_locked, locked.temporarily_unavailable) == (True, False)
    assert (outage.audio_locked, outage.temporarily_unavailable) == (False, True)
    assert locked.model_dump_json() != outage.model_dump_json()


# ── the drift guard ──────────────────────────────────────────────────────────

def test_the_backend_catalog_and_the_generated_swift_cover_the_same_books():
    """Both are generated from backend/data/book_audio/*.manifest.json. If they diverge, a
    book gets a signed URL the app has no offsets for (or offsets with no URL) — and nothing
    else in either suite would notice."""
    swift = _SWIFT.read_text()
    swift_orders = {int(m) for m in re.findall(r"^\s*(\d+): BookAudioInfo\(", swift, re.M)}
    assert swift_orders == set(svc.BOOK_AUDIO_CATALOG), (
        f"swift={sorted(swift_orders)} backend={sorted(svc.BOOK_AUDIO_CATALOG)} — "
        f"re-run backend/scripts/gen_book_audio_swift.py"
    )


def test_the_generated_swift_carries_no_storage_url():
    """The regression that made the gate cosmetic. Regenerating with the old script, or
    hand-editing a URL back in, must fail here."""
    swift = _SWIFT.read_text()
    assert "/storage/v1/object/" not in swift
    assert "supabase.co" not in swift
    assert "audioUrl" not in swift
