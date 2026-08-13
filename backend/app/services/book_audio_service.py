"""Book narration catalog + signed-URL serving.

Books are the one Learn product with no content table — `BooksContent.swift` ships in the
app and the audio was, until now, ten public URLs compiled into `BookAudioContent.swift`.
So the backend needs its own record of which object belongs to which book.

**Source of truth: `backend/data/book_audio/*.manifest.json`.** Those ten manifests are what
`scripts/seed_book_audio.py` wrote when it uploaded the clips and what
`scripts/gen_book_audio_swift.py` reads to generate the Swift file — so they cannot drift
from what is actually in the bucket. Deriving `audio/{order}_{slug}.m4a` from a book title
instead would re-implement the slug rules in a third place, and listing the bucket per
request would add I/O to a hot path for data that changes when a book is added, i.e. never
at runtime.

Loaded once at import. A missing or malformed manifest logs and omits that one book rather
than raising — a bad file must not take the Learn tab down.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Dict, List

from app.schemas.learn_books_audio import BookAudioURLResponse, BooksAudioResponse
from app.services.learn_audio_urls import sign_many

logger = logging.getLogger(__name__)

_MANIFEST_DIR = Path(__file__).resolve().parents[2] / "data" / "book_audio"
_BUCKET = "book-media"


def _load_catalog() -> Dict[int, str]:
    """curriculum_order -> object path within `book-media` (e.g. "audio/1_rich-dad.m4a")."""
    catalog: Dict[int, str] = {}
    if not _MANIFEST_DIR.is_dir():
        logger.warning(
            "book_audio_service: manifest dir missing at %s — book narration will be "
            "unavailable to every caller", _MANIFEST_DIR,
        )
        return catalog

    for path in sorted(_MANIFEST_DIR.glob("*.manifest.json")):
        try:
            data = json.loads(path.read_text())
            order = int(data["curriculum_order"])
            audio_file = str(data["audio_file"]).strip()
            if not audio_file:
                raise ValueError("empty audio_file")
        except Exception as e:
            # Non-fatal by design: one bad manifest costs one book, not the endpoint.
            logger.warning(
                "book_audio_service: skipping manifest %s (%s: %s)",
                path.name, type(e).__name__, e,
            )
            continue
        if order in catalog:
            logger.warning(
                "book_audio_service: duplicate curriculum_order %s in %s — keeping %s",
                order, path.name, catalog[order],
            )
            continue
        catalog[order] = f"audio/{audio_file}"

    if not catalog:
        logger.warning("book_audio_service: no usable manifests found in %s", _MANIFEST_DIR)
    else:
        logger.info("book_audio_service: loaded %d book audio manifest(s)", len(catalog))
    return catalog


# Immutable after import; the manifests are build-time data.
BOOK_AUDIO_CATALOG: Dict[int, str] = _load_catalog()


async def get_books_audio(*, unlocked: bool, tier_required: str | None) -> BooksAudioResponse:
    """Signed narration URLs for every book, or a locked envelope.

    Locked callers get 200 + an empty list (see `BooksAudioResponse`). Entitled callers get
    one signed URL per book; a book whose signature could not be minted is OMITTED rather
    than served a public URL, because unlike Journey/Money Moves there is no pre-existing
    URL on the wire to fall back to — the client treats a missing order as "no narration"
    and refuses playback, which is the safe direction.
    """
    if not unlocked:
        return BooksAudioResponse(books=[], audio_locked=True, tier_required=tier_required)

    if not BOOK_AUDIO_CATALOG:
        return BooksAudioResponse(books=[], audio_locked=False, tier_required=None)

    pairs = [(_BUCKET, object_path) for object_path in BOOK_AUDIO_CATALOG.values()]
    signed = await sign_many(pairs)

    books: List[BookAudioURLResponse] = []
    for order in sorted(BOOK_AUDIO_CATALOG):
        url = signed.get((_BUCKET, BOOK_AUDIO_CATALOG[order]))
        if not url:
            logger.warning(
                "book_audio_service: no signed URL for book order=%s path=%s — omitting",
                order, BOOK_AUDIO_CATALOG[order],
            )
            continue
        books.append(BookAudioURLResponse(curriculum_order=order, audio_url=url))

    return BooksAudioResponse(books=books, audio_locked=False, tier_required=None)


__all__ = ["BOOK_AUDIO_CATALOG", "get_books_audio"]
