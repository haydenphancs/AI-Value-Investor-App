"""
Profile pictures: validate, store, sign, import.

The user's avatar lives in the PRIVATE `user-avatars` Storage bucket (migration 152), written
only by the service role, and `users.avatar_url` holds the PUBLIC-FORM URL of that object. The
read path mints a short-lived signed URL — the same shape migration 128 and `learn_audio_urls`
established for the Learn buckets, and for the same reason: `ProfileAvatarView` hands the URL
straight to `AsyncImage`, which cannot attach an `Authorization` header, so an authed byte
proxy (the `research-pdfs` model) is not available to us.

Storing the public FORM of a private object's URL is deliberate. The column stays a stable,
non-expiring value, so nothing in the database expires, and flipping the bucket's visibility
later is a server-side change rather than a data migration.

⚠️ THE OBJECT KEY IS DERIVED ENTIRELY SERVER-SIDE and is CONTENT-ADDRESSED:

    avatars/<user_id>/<sha256(jpeg)[:32]>.jpg

Two independent properties come from that. Nothing in the key comes from the request, so path
traversal and cross-user overwrite are structurally impossible. And replacing the photo yields
a NEW path, hence a NEW URL — so neither Supabase's CDN nor the iOS `URLCache` (raised to
32MB/128MB in `iosApp.swift` precisely so `AsyncImage` caches) can serve the old face at the
one moment the user is looking for the change. A stable key plus `upsert` would have had a
visible stale window exactly then; `seed_journey.py` documents the same trap for Journey art.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import logging
import time
from typing import Dict, Optional, Tuple
from urllib.parse import urlsplit

import httpx

from app.database import get_supabase
from app.schemas.user import AVATAR_MAX_BYTES, AVATAR_URL_MAX_LENGTH

logger = logging.getLogger(__name__)

BUCKET = "user-avatars"
#: Everything this service will ever touch lives under here. The signer refuses anything else.
PREFIX = "avatars"

_PUBLIC_MARKER = "/storage/v1/object/public/"
_SIGNED_MARKER = "/storage/v1/object/sign/"

#: JPEG SOI + APP marker. The ONLY structural check we make on the bytes.
_JPEG_MAGIC = b"\xff\xd8\xff"

#: Optional data-URL wrapper. Accepted so a hand-rolled or web caller isn't silently rejected
#: for a cosmetic prefix; the iOS client never sends one.
_DATA_URL_PREFIXES = ("data:image/jpeg;base64,", "data:image/jpg;base64,")

#: Signature lifetime, and how long one is reused before re-minting. Copied from
#: `learn_audio_urls` — the gap is the safety argument: a URL handed out at the very end of its
#: cache life still has (SIGNED - CACHE) = 18h of validity left.
_SIGNED_TTL_SECONDS = 24 * 60 * 60
_CACHE_TTL_SECONDS = 6 * 60 * 60

#: Ceiling on how long a caller waits for a mint. A hung Storage call must not hold the profile
#: response open; the caller degrades to the stored (unsigned) URL instead.
_SIGN_TIMEOUT_SECONDS = 4.0

#: Cache-control written on the object. LONG on purpose: the key is content-addressed, so the
#: bytes at a given path are immutable and a new photo is a new path. A short max-age here would
#: throw away the client-side caching the key scheme exists to make safe.
_OBJECT_CACHE_CONTROL = "31536000"

#: Cap on an IMPORTED third-party avatar (see `import_external_avatar`). Larger than
#: AVATAR_MAX_BYTES because we are not the ones who encoded it; the fetch is aborted past this.
_IMPORT_MAX_BYTES = 2 * 1024 * 1024
_IMPORT_TIMEOUT_SECONDS = 6.0

# (user_id, object_path) -> (minted_at, signed_url)
_cache: Dict[Tuple[str, str], Tuple[float, str]] = {}


class AvatarError(Exception):
    """Base for every avatar failure. Never raise a bare Exception from here."""


class AvatarTooLargeError(AvatarError):
    """The decoded JPEG exceeds AVATAR_MAX_BYTES."""


class AvatarInvalidImageError(AvatarError):
    """Not valid base64, or the bytes are not a JPEG."""


class AvatarStorageError(AvatarError):
    """Supabase Storage refused the write or delete."""


# ── Validation ────────────────────────────────────────────────────────────────


def decode_and_validate(image_base64: str) -> bytes:
    """Base64 -> JPEG bytes, or a typed exception.

    ⚠️ This NEVER DECODES THE IMAGE, and that is the point. Handing an attacker-supplied file
    to an image decoder is the whole decompression-bomb class: a few-KB payload that expands to
    hundreds of megapixels in RAM. We judge on two cheap, non-expanding facts — the byte count
    and the first three bytes — and let the client (which re-encodes through UIImage on the
    user's own device, at their own cost) be the thing that normalises HEIC and strips EXIF.

    Pillow could not be primary here anyway: the pinned build has no HEIF decoder, and iPhones
    shoot HEIC by default, so a server-side re-encode would reject most real photos.
    """
    if not isinstance(image_base64, str):
        raise AvatarInvalidImageError("image_base64 must be a string")

    payload = image_base64.strip()
    for prefix in _DATA_URL_PREFIXES:
        if payload.startswith(prefix):
            payload = payload[len(prefix):]
            break
    if not payload:
        raise AvatarInvalidImageError("image_base64 is empty")

    try:
        raw = base64.b64decode(payload, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise AvatarInvalidImageError(f"not valid base64: {type(exc).__name__}") from exc

    if not raw:
        raise AvatarInvalidImageError("image_base64 decoded to zero bytes")
    # Size BEFORE magic bytes: a huge non-JPEG should read as "too large", which is the
    # actionable message, rather than sending the user hunting for a different format.
    if len(raw) > AVATAR_MAX_BYTES:
        raise AvatarTooLargeError(f"{len(raw)} bytes exceeds the {AVATAR_MAX_BYTES} cap")
    if not raw.startswith(_JPEG_MAGIC):
        raise AvatarInvalidImageError("bytes are not a JPEG (bad magic)")
    return raw


def object_path_for(user_id: str, jpeg: bytes) -> str:
    """`avatars/<user_id>/<sha256[:32]>.jpg` — derived only from the caller's identity and the
    bytes, never from anything in the request body."""
    digest = hashlib.sha256(jpeg).hexdigest()[:32]
    return f"{PREFIX}/{user_id}/{digest}.jpg"


# ── Storage ───────────────────────────────────────────────────────────────────


def _public_url(object_path: str) -> str:
    # `.rstrip("?")` because storage3's get_public_url appends a bare `?`.
    return get_supabase().storage.from_(BUCKET).get_public_url(object_path).rstrip("?")


def _upload_sync(object_path: str, jpeg: bytes) -> str:
    storage = get_supabase().storage.from_(BUCKET)
    storage.upload(
        object_path,
        jpeg,
        {
            "content-type": "image/jpeg",
            "cache-control": _OBJECT_CACHE_CONTROL,
            # Idempotent: re-uploading identical bytes lands on the identical content-addressed
            # path, and a retry after a partial failure must not 409.
            "upsert": "true",
        },
    )
    return _public_url(object_path)


async def store_avatar(user_id: str, jpeg: bytes) -> str:
    """Upload the JPEG and return the public-FORM URL to persist. Raises AvatarStorageError."""
    object_path = object_path_for(user_id, jpeg)
    try:
        url = await asyncio.to_thread(_upload_sync, object_path, jpeg)
    except Exception as exc:
        logger.error(
            "[Avatar] upload failed user_id=%s path=%s: %s",
            user_id, object_path, f"{type(exc).__name__}: {exc}",
        )
        raise AvatarStorageError(str(exc)) from exc

    # The URL is ours to construct, so this can only fire if the storage host changes shape.
    # Better a loud 503 than a silent truncation into a column with no CHECK.
    if len(url) > AVATAR_URL_MAX_LENGTH:
        logger.error(
            "[Avatar] constructed URL is %d chars, over the %d ceiling (user_id=%s)",
            len(url), AVATAR_URL_MAX_LENGTH, user_id,
        )
        raise AvatarStorageError("constructed avatar URL exceeds AVATAR_URL_MAX_LENGTH")
    return url


def _remove_sync(object_paths: list[str]) -> None:
    get_supabase().storage.from_(BUCKET).remove(object_paths)


async def remove_object(user_id: str, stored_url: Optional[str]) -> None:
    """Best-effort delete of one previously stored avatar object.

    Non-fatal by design — a leaked object is swept by the account-deletion purge — but NEVER
    silent: a warning here is the only trace that the bucket is accumulating orphans.
    """
    parsed = _own_object_path(user_id, stored_url)
    if parsed is None:
        return
    try:
        await asyncio.to_thread(_remove_sync, [parsed])
    except Exception as exc:
        logger.warning(
            "[Avatar] could not remove the previous object user_id=%s path=%s: %s "
            "(non-fatal: the object is now orphaned until account deletion sweeps it)",
            user_id, parsed, f"{type(exc).__name__}: {exc}",
        )
    else:
        _cache.pop((user_id, parsed), None)


# ── Signing ───────────────────────────────────────────────────────────────────


def _own_object_path(user_id: str, url: Optional[str]) -> Optional[str]:
    """The object path inside OUR bucket, under THIS user's prefix — else None.

    ⚠️ LOAD-BEARING SECURITY BOUNDARY, not hygiene. Signing runs on the SERVICE-ROLE key, which
    bypasses RLS and can sign anything in any bucket. Returning None means "leave the value
    exactly as found", which is what makes an imported third-party URL, an already-signed URL,
    and a poisoned column all degrade safely.

    The per-USER prefix check is the part that goes beyond a bucket allowlist: it means that
    even if `users.avatar_url` were somehow set to another account's object, this refuses to
    mint a signature for it. That is why `avatar_url` was removed from the client-writable
    profile request — the two changes are one mechanism.

    ⚠️ WIDENING THIS IS A PRODUCT DECISION, NOT A REFACTOR. The day avatars become visible to
    OTHER users (comments, shared portfolios, a leaderboard), a viewer must be able to see an
    avatar that is not theirs, and this rule has to relax from "this caller's prefix" to "any
    object under `avatars/`". Doing that also switches on App Store Guideline 1.2 for
    user-generated content — content filtering, a report mechanism, user blocking, published
    contact info — and changes the App Review Information answer in `app-privacy-answers.md`
    §7, which currently states no user content is visible to other users. Do not relax this
    rule without that whole checklist.
    """
    if not url or not isinstance(url, str):
        return None
    if _SIGNED_MARKER in url:
        return None
    idx = url.find(_PUBLIC_MARKER)
    if idx == -1:
        return None
    tail = urlsplit(url[idx + len(_PUBLIC_MARKER):]).path
    bucket, _, object_path = tail.partition("/")
    if bucket != BUCKET or not object_path:
        return None
    expected = f"{PREFIX}/{user_id}/"
    if not object_path.startswith(expected):
        logger.warning(
            "[Avatar] refusing to sign an object outside this caller's prefix "
            "(user_id=%s path=%s) — check how users.avatar_url was written",
            user_id, object_path,
        )
        return None
    return object_path


def _sign_sync(object_path: str) -> Optional[str]:
    res = get_supabase().storage.from_(BUCKET).create_signed_url(
        object_path, _SIGNED_TTL_SECONDS
    )
    if isinstance(res, dict):
        return res.get("signedURL") or res.get("signedUrl")
    return None


async def signed_avatar_url(user_id: str, stored_url: Optional[str]) -> Optional[str]:
    """Turn a stored avatar URL into a short-lived signed one.

    NEVER raises: an avatar is decoration on a profile response, and a Storage hiccup must not
    fail `GET /users/me`. Every failure degrades to the stored value and logs a warning.
    """
    object_path = _own_object_path(user_id, stored_url)
    if object_path is None:
        return stored_url

    now = time.time()
    hit = _cache.get((user_id, object_path))
    if hit and now - hit[0] < _CACHE_TTL_SECONDS:
        return hit[1]

    try:
        signed = await asyncio.wait_for(
            asyncio.to_thread(_sign_sync, object_path), timeout=_SIGN_TIMEOUT_SECONDS
        )
    except Exception as exc:
        logger.warning(
            "[Avatar] signing failed user_id=%s path=%s: %s — serving the stored URL",
            user_id, object_path, f"{type(exc).__name__}: {exc}",
        )
        return stored_url

    if not signed:
        logger.warning(
            "[Avatar] signing returned nothing user_id=%s path=%s — serving the stored URL",
            user_id, object_path,
        )
        return stored_url

    _cache[(user_id, object_path)] = (now, signed)
    return signed


# ── Importing a third-party (OAuth) avatar ────────────────────────────────────


def is_external(url: Optional[str]) -> bool:
    """True for an avatar we do NOT host — i.e. one worth importing.

    `public.handle_new_auth_user()` copies `raw_user_meta_data->>'avatar_url'` into
    `public.users.avatar_url` at signup, so every Google sign-in arrives with a
    googleusercontent.com URL already in the column.
    """
    if not url or not isinstance(url, str):
        return False
    if not url.startswith(("http://", "https://")):
        return False
    return f"/{BUCKET}/" not in url


async def import_external_avatar(user_id: str, url: str) -> Optional[str]:
    """Fetch a third-party avatar once and re-host it in our bucket.

    Why bother: left alone, a Google-hosted avatar is fetched from `lh3.googleusercontent.com`
    on EVERY profile render, which discloses the user's IP to a third party our privacy
    policy's service-provider list does not name, and leaves the image outside our retention
    and deletion story entirely. Importing makes every avatar ours, with one host and one
    lifecycle, while the user keeps the picture they already had.

    Returns the new public-form URL, or None if anything at all went wrong — the caller keeps
    the existing value and simply tries again on a later read. NEVER raises: this runs inside a
    profile GET, and an unreachable third party must not fail it.
    """
    try:
        async with httpx.AsyncClient(timeout=_IMPORT_TIMEOUT_SECONDS, follow_redirects=True) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            raw = resp.content
    except Exception as exc:
        logger.warning(
            "[Avatar] import fetch failed user_id=%s: %s — keeping the external URL",
            user_id, f"{type(exc).__name__}: {exc}",
        )
        return None

    if len(raw) > _IMPORT_MAX_BYTES:
        logger.warning(
            "[Avatar] import too large user_id=%s bytes=%d — keeping the external URL",
            user_id, len(raw),
        )
        return None
    # Same discipline as the upload path: judged on magic bytes, never decoded. Google serves
    # JPEG for these; anything else we simply do not import rather than guess.
    if not raw.startswith(_JPEG_MAGIC):
        logger.warning(
            "[Avatar] import is not a JPEG user_id=%s — keeping the external URL", user_id
        )
        return None
    if len(raw) > AVATAR_MAX_BYTES:
        logger.warning(
            "[Avatar] imported avatar is %d bytes, over the %d store cap (user_id=%s) — "
            "keeping the external URL", len(raw), AVATAR_MAX_BYTES, user_id,
        )
        return None

    try:
        return await store_avatar(user_id, raw)
    except AvatarError as exc:
        logger.warning(
            "[Avatar] import store failed user_id=%s: %s — keeping the external URL",
            user_id, f"{type(exc).__name__}: {exc}",
        )
        return None
