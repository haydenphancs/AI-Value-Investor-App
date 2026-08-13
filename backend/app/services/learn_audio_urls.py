"""Signed-URL minting for Wiser (Learn) narration.

The narration gate (`learn_audio_gate`) withholds `audioUrl` from a caller whose plan
doesn't include audio. That strip is only *enforcement* if the object itself is unreachable
without a signature — and today it isn't: `book-media`, `journey-media` and
`money-moves-media` are all PUBLIC buckets (migrations 068 / 061 / 065) whose paths are
deterministic, and the clip NAMES ship inside the app bundle (207 in
`Resources/Journey/journey_lessons.json`, 13 in `Resources/MoneyMoves/money_moves.json`).
Anyone with a build can reconstruct every URL offline. Stripping the field is obscurity.

This module is the other half: every narration URL handed to an entitled caller is a
short-lived **signed** URL, so migration 128 can flip those three buckets private and the
gate becomes real.

⚠️ **Why signed URLs and not an authed proxy.** Both iOS audio engines build players with a
bare `AVPlayerItem(url:)` (`AudioManager.swift`, `AIVoiceManager.swift`) — there is no
`AVURLAsset`, no `AVURLAssetHTTPHeaderFieldsKey` and no `AVAssetResourceLoaderDelegate`
anywhere in the app, so the client physically cannot attach an Authorization header to an
audio request. The `research-pdfs` proxy shape (buffer the whole object into memory and
return it) is also unusable here: 276 MB of audio, with seeking.

⚠️ **Never raise.** A signing failure must not take down the Learn tab. Every failure path
falls back to the ORIGINAL url and logs — which today still plays, because the buckets are
still public. Migration 128 converts that degradation from "still works" to "playback
error", which is exactly why 128's header says signing health must be proven in production
BEFORE the flip.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Dict, Iterable, List, Optional, Set, Tuple
from urllib.parse import urlsplit

from app.database import get_supabase

logger = logging.getLogger(__name__)

# The marker Supabase puts in a public object URL. Everything after it is
# "<bucket>/<object path>".
_PUBLIC_MARKER = "/storage/v1/object/public/"
# Already-signed URLs carry this instead; they must be left alone rather than re-parsed.
_SIGNED_MARKER = "/storage/v1/object/sign/"

# ⚠️ LOAD-BEARING SECURITY BOUNDARY, not hygiene.
#
# `sign_many` runs on the SERVICE-ROLE key, which bypasses RLS — it can sign anything in any
# bucket. The URLs it is handed come from `lessons.story_content` and
# `money_move_articles.content`: authored JSONB blobs edited in Supabase Studio, outside the
# migration flow. Without this allowlist, one row edit pointing a card's `audioUrl` at
# `research-pdfs/<user_id>/<report_id>.pdf` would turn GET /learn/journey into a signed-URL
# oracle for another user's private research report — served to anyone on Pro.
#
# Anything not listed here parses to None, i.e. "leave the value exactly as found".
_SIGNABLE_BUCKETS = frozenset({"journey-media", "money-moves-media", "book-media"})

# Ceiling on one bucket's mint. `_sign_batch_sync` issues up to a few sequential POSTs, and
# a hung Storage call must not hold the Learn tab open — the caller degrades to the stored
# URL instead. Note this bounds LATENCY, not the worker thread: `asyncio.to_thread` is not
# cancellable. The per-bucket lock is what caps concurrent sign threads at one per bucket.
_SIGN_TIMEOUT_SECONDS = 4.0

# How long a minted signature stays valid, and how long we reuse one before minting again.
#
# The gap between them is the whole safety argument. A URL handed out at the very end of its
# cache life still has (SIGNED - CACHE) = 18h of validity left, against a longest single
# asset of one 58:22 book .m4a (`10_the-most-important-thing.manifest.json`). So a signature
# can never expire part-way through playback, and AVPlayer's range requests for a seek late
# in a book are still authorised.
#
# ⚠️ The binding constraint is the CLIENT's cache, not the server's. Signing happens
# per-request, AFTER the content-cache read, so `journey_content_service._TTL_SECONDS` (3600)
# constrains nothing here. What can outlive a signature is the iOS side: the Learn content
# stores latch a once-per-session prefetch, and `BookAudioURLStore` memoises URLs, so a
# long-foregrounded app can hold a payload for days. No signature length fixes that — only
# the client's refresh interval and `AudioManager`'s refresh-and-retry-once on a playback
# failure do. Tune those, not these, if stale URLs ever show up in the wild.
_SIGNED_TTL_SECONDS = 24 * 60 * 60      # 24h signature lifetime
_CACHE_TTL_SECONDS = 6 * 60 * 60        # 6h reuse

# Supabase rejects an over-long batch; Journey alone asks for ~207 paths in one go.
_MAX_BATCH = 100

# (bucket, object_path) -> (minted_at, signed_url)
_cache: Dict[Tuple[str, str], Tuple[float, str]] = {}
# bucket -> lock, so a burst of concurrent Learn requests mints each path once.
_locks: Dict[str, asyncio.Lock] = {}


def _lock_for(bucket: str) -> asyncio.Lock:
    """One lock per bucket. Created lazily — an `asyncio.Lock` built at import time under
    Python 3.11 binds to no loop, so this is safe, but keeping it lazy also keeps the dict
    to the three buckets actually in use."""
    lock = _locks.get(bucket)
    if lock is None:
        lock = asyncio.Lock()
        _locks[bucket] = lock
    return lock


def parse_storage_url(url: Optional[str]) -> Optional[Tuple[str, str]]:
    """Pure: split a public Supabase Storage URL into ``(bucket, object_path)``.

    Returns ``None`` — meaning "leave this value exactly as it is" — for every input that
    isn't a public Storage object URL: an empty/None value, a URL that is ALREADY signed, a
    third-party URL, or anything malformed. Callers treat ``None`` as "don't touch it", so
    a surprise in the data degrades to today's behaviour instead of a 500 on the Learn tab.

    The query string is dropped deliberately: ``storage3.get_public_url`` appends a bare
    ``?`` (visible in `backend/data/book_audio/*.manifest.json`), and that trailing marker
    is not part of the object path.
    """
    if not url or not isinstance(url, str):
        return None
    if _SIGNED_MARKER in url:
        return None
    idx = url.find(_PUBLIC_MARKER)
    if idx == -1:
        return None
    tail = url[idx + len(_PUBLIC_MARKER):]
    # Strip ?query / #fragment before splitting — the object path is the path component.
    tail = urlsplit(tail).path
    bucket, _, object_path = tail.partition("/")
    if not bucket or not object_path:
        return None
    if bucket not in _SIGNABLE_BUCKETS:
        # Not a mistake to paper over: authored content pointing outside the Learn buckets is
        # either a bad row edit or an attempt to have the service role sign someone else's
        # object. Log it loudly and refuse; the caller leaves the URL untouched.
        logger.warning(
            "learn_audio_urls: refusing to sign outside the Learn buckets (bucket=%s) — "
            "check the authored content row that produced this URL", bucket,
        )
        return None
    return bucket, object_path


def _cached(bucket: str, object_path: str, now: float) -> Optional[str]:
    hit = _cache.get((bucket, object_path))
    if hit and now - hit[0] < _CACHE_TTL_SECONDS:
        return hit[1]
    return None


def _sign_batch_sync(bucket: str, paths: List[str]) -> Dict[str, str]:
    """Blocking: mint signatures for ``paths`` in ``bucket``. Returns only the successes.

    Runs on a worker thread — the Supabase Python SDK is synchronous and would otherwise
    block the event loop (`.claude/rules/backend-python.md`).
    """
    out: Dict[str, str] = {}
    storage = get_supabase().storage.from_(bucket)
    for start in range(0, len(paths), _MAX_BATCH):
        chunk = paths[start:start + _MAX_BATCH]
        for item in storage.create_signed_urls(chunk, _SIGNED_TTL_SECONDS):
            # Per-item failures come back in the row rather than as an exception — a missing
            # object is the common one (a clip named in content that was never uploaded).
            if item.get("error"):
                logger.warning(
                    "learn_audio_urls: sign failed bucket=%s path=%s error=%s",
                    bucket, item.get("path"), item["error"],
                )
                continue
            signed = item.get("signedURL") or item.get("signedUrl")
            path = item.get("path")
            if signed and path:
                # Supabase echoes the path back WITHOUT a leading slash; normalise so the
                # key matches what we asked for.
                out[path.lstrip("/")] = signed
    return out


async def _sign_bucket(bucket: str, paths: Set[str]) -> Dict[str, str]:
    """Mint every missing signature for one bucket, deduping concurrent callers.

    The dedup is a per-bucket lock rather than a shared future because callers ask for
    DIFFERENT path sets (one wants Journey's 207 clips, the next wants one book). Holding
    the lock and re-checking the cache inside it means a burst of cold requests mints each
    path once, and later arrivals do no I/O at all — whereas a single shared future would
    have to be keyed on the exact path set to be correct.
    """
    async with _lock_for(bucket):
        now = time.time()
        missing = sorted(p for p in paths if _cached(bucket, p, now) is None)
        if missing:
            try:
                # `asyncio.TimeoutError` subclasses Exception on 3.11, so the handler below
                # catches it and degrades exactly like any other signing failure.
                signed = await asyncio.wait_for(
                    asyncio.to_thread(_sign_batch_sync, bucket, missing),
                    timeout=_SIGN_TIMEOUT_SECONDS,
                )
                minted_at = time.time()
                for path, url in signed.items():
                    _cache[(bucket, path)] = (minted_at, url)
            except Exception as e:
                # Deliberately non-fatal: callers fall back to the original URL. Logged with
                # the exception TYPE + bucket so it is diagnosable from logs alone, with no
                # repro. Nothing is cached, so the next request retries.
                logger.warning(
                    "learn_audio_urls: signing bucket=%s failed for %d path(s), falling "
                    "back to public URLs (%s: %s)",
                    bucket, len(missing), type(e).__name__, e,
                )
        now = time.time()
        return {
            p: url
            for p in paths
            if (url := _cached(bucket, p, now)) is not None
        }


async def sign_many(pairs: Iterable[Tuple[str, str]]) -> Dict[Tuple[str, str], str]:
    """Mint (or reuse) signed URLs for every ``(bucket, object_path)`` given.

    One `create_signed_urls` round trip per bucket per 100 paths — NOT one per URL. Journey
    alone carries ~207 clips, so per-URL signing would be ~207 sequential round trips on a
    cache-cold Learn request.

    A pair that could not be signed is simply ABSENT from the result; the caller then leaves
    that URL as it found it.
    """
    now = time.time()
    result: Dict[Tuple[str, str], str] = {}
    wanted: Dict[str, Set[str]] = {}

    for bucket, object_path in pairs:
        hit = _cached(bucket, object_path, now)
        if hit is not None:
            result[(bucket, object_path)] = hit
        else:
            wanted.setdefault(bucket, set()).add(object_path)

    if not wanted:
        return result

    buckets = sorted(wanted)
    minted = await asyncio.gather(
        *(_sign_bucket(b, wanted[b]) for b in buckets),
        return_exceptions=True,
    )
    for bucket, got in zip(buckets, minted):
        if isinstance(got, Exception):
            logger.warning(
                "learn_audio_urls: bucket=%s signing raised (%s: %s)",
                bucket, type(got).__name__, got,
            )
            continue
        for path, url in got.items():
            result[(bucket, path)] = url
    return result


async def sign_url(url: Optional[str]) -> Optional[str]:
    """Convenience: sign ONE public Storage URL, or return it untouched.

    Returns the input unchanged when it isn't a public Storage URL or when signing failed —
    never ``None`` for a non-empty input, so a caller can always assign the result back.
    """
    parsed = parse_storage_url(url)
    if parsed is None:
        return url
    signed = await sign_many([parsed])
    return signed.get(parsed, url)


def reset_cache_for_tests() -> None:
    """Drop the memo. Tests only — production has no reason to invalidate."""
    _cache.clear()
    _locks.clear()
