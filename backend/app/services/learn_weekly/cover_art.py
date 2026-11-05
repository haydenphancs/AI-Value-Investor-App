"""COVER ART for a Wiser weekly piece (owner decision 2026-11-01: "W44 now + every weekly piece").

One accepted draft → one plate in the catalog's own art system (`money_moves_art_style`: one metaphor
object on a designed ground, in the category's palette, NO text, no people, no brands), stored where
the catalog's plates live and served the same way:

    draft (title, subtitle, category, keyHighlights)
      └─ (a) a SUBJECT sentence from the text model, asked with the shared subject prompt and checked
             like the script's `--auto` (length, the brand/person deny-list, Battles = two identical
             objects) plus the piece's own company names — at most `SUBJECT_ATTEMPTS` short calls,
             and a retry only when an answer is unusable
      └─ (b) the treatment `default_treatment(slug, category)` — deterministic on the slug
      └─ (c) ONE image (`GeminiClient.generate_image`, `LEARN_ART_MODEL`, 16:9, 2K)
      └─ (d) master → hero 1206×678 + card 640×360 JPEGs (Pillow, off the event loop)
      └─ (e) both uploaded to the PUBLIC `money-moves-images` bucket (migration 137 — never signed)
             at `articles/<slug>.hero.jpg` / `.card.jpg`, `x-upsert: false`, image/jpeg,
             cache-control 300 (the seeder's value)
      └─ (f) {"imageUrl": hero URL, "imageCardUrl": card URL, "coverArt": {subject, treatment, model,
             prompt_sha1, …}}

IDEMPOTENT on a rebuild / Regenerate: when BOTH objects already exist for the slug they are reused and
NO model is called (a weekly slug ends in its publish date, so it only ever names this piece's plates —
never a catalog plate). A HALF pair (one upload of an earlier run failed) is removed first and the pair
re-made, so the hero and the card are always the same picture. An upload that collides with an object
written in between is reused when its size is equal, else refused (artless, loudly).

Cost: one image + one short text call per piece (more text calls only for an unusable subject).

`cover_art_for` is the build's entry point and NEVER raises (except CancelledError): it is gated by
`LEARN_ART_ENABLED` (fail-closed), bounded by `LEARN_ART_TIMEOUT_SECONDS`, and any failure is a WARNING
naming the piece, the slug and the error type — the piece goes on ARTLESS (iOS draws the category
gradient). The record it returns is stored in the piece's `validation.cover_art`, NOT in the draft:
the draft is served to the app verbatim, and the record names the image model (CLAUDE.md #7).

FMP-free (the learn_weekly boundary): the subject is asked from the draft's own text only.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from app.config import settings
from app.services import money_moves_art_style as style
from app.services.learn_weekly import contracts as C
from app.services.learn_weekly.errors import LearnWeeklyError
from app.services.learn_weekly.validators import SLUG_RE

logger = logging.getLogger(__name__)

#: Migration 137's PUBLIC bucket — the catalog's plates live there too (`seed_money_moves.IMAGE_BUCKET`).
#: Never `money-moves-media` (private, narration) and never signed: artwork is free content.
BUCKET = "money-moves-images"
PREFIX = "articles"
CONTENT_TYPE = "image/jpeg"
#: The seeder's value: the filename is stable per slug, so a long TTL would pin a superseded plate.
CACHE_CONTROL = "300"
#: Migration 137 caps an object at 2 MB; a derivative runs 30-300 KB, so anything near it is a bug.
MAX_OBJECT_BYTES = 1_900_000
USAGE_TAG = "learn_weekly_cover_art"
#: Text calls for one subject at most (a retry only follows an unusable answer).
SUBJECT_ATTEMPTS = 3
#: The subject prompt asks for under 55 words; a far longer answer is not a subject.
SUBJECT_MAX_CHARS = 600
SUBJECT_MAX_OUTPUT_TOKENS = 256
#: Key highlights shown to the subject model as context (the draft has exactly three).
_HIGHLIGHTS_SHOWN = 3
_FIELD_CAP = 300
#: Corporate suffixes dropped from a subject company's name before it joins the deny-list.
_NAME_SUFFIXES = frozenset({
    "inc", "incorporated", "corp", "corporation", "co", "company", "ltd", "limited", "plc", "llc",
    "lp", "sa", "nv", "ag", "se", "holdings", "holding", "group", "the", "class", "a", "b", "c",
    "de", "adr", "ads",
})
_WORD = re.compile(r"[a-z0-9]+(?:['’][a-z]+)?")
#: A ticker shorter than this never joins a subject's deny-list (`subject_ban_terms`).
MIN_TICKER_BAN_CHARS = 2

#: Record statuses (`validation.cover_art.status`).
STATUS_OK = "ok"
STATUS_OFF = "off"
STATUS_FAILED = "failed"
STATUS_SKIPPED = "skipped"


class CoverArtError(LearnWeeklyError):
    """The cover art could not be made (base). The piece goes on artless."""


class CoverArtSubjectError(CoverArtError):
    """No usable subject sentence: the text call failed, or every answer broke a subject rule."""


class CoverArtImageError(CoverArtError):
    """The image call or the image itself failed (refused, unavailable, not decodable, too large)."""


class CoverArtStorageError(CoverArtError):
    """The bucket could not be read or written, or another object holds the path."""


# ── pure parts ────────────────────────────────────────────────────────────────────────────────


def art_paths(slug: Any) -> Dict[str, str]:
    """{"hero": "articles/<slug>.hero.jpg", "card": "articles/<slug>.card.jpg"} for a WEEKLY slug
    (`<kebab-title>-<yyyy-mm-dd>`, `validators.SLUG_RE`). ValueError on anything else — a slug outside
    the pattern could name a catalog plate or escape the folder."""
    if not isinstance(slug, str) or len(slug) > 160 or ".." in slug or not SLUG_RE.fullmatch(slug):
        raise ValueError(f"cover art: {slug!r} is not a weekly slug")
    return {name: f"{PREFIX}/{slug}.{name}.jpg" for name in style.DERIVATIVES}


def public_url(path: str) -> str:
    """The unsigned public URL of an object in the image bucket — the form the catalog rows store."""
    base = str(settings.SUPABASE_URL or "").rstrip("/")
    return f"{base}/storage/v1/object/public/{BUCKET}/{path}"


def clean_subject(raw: Any) -> str:
    """The model's answer as one line, outer quotes stripped (the script's `--auto` normalisation)."""
    if not isinstance(raw, str):
        return ""
    return " ".join(raw.split()).strip().strip('"').strip()


def subject_ban_terms(subjects: Iterable[Any]) -> List[str]:
    """What a subject may not contain, from the piece's main subject companies: each NAME without its
    corporate suffixes, lower-case ("NVIDIA CORP" → "nvidia", "Taiwan Semiconductor Manufacturing Co
    Ltd" → "taiwan semiconductor manufacturing"), and each TICKER, upper-case. The shared deny-list
    knows the catalog's brands only; a weekly piece can be about any filer. Whole names only — one
    generic word of a longer name ("advanced") is never banned on its own — and a ticker is matched
    case-SENSITIVELY (`subject_problem`), so "ON" or "LOW" never refuses the words "on" or "low".

    A ONE-letter ticker (A, C, D, F, K, L, O, T, V …) is left out: as a case-sensitive whole word it
    matches the article "A" that opens most subject sentences, or any lone capital, so every answer
    would be refused and the piece would go artless after `SUBJECT_ATTEMPTS` paid calls. The company's
    NAME stays banned, and a single capital letter names no company in a picture with no text."""
    out: List[str] = []
    for s in subjects or ():
        name = s.get("name") if isinstance(s, Mapping) else getattr(s, "name", None)
        ticker = s.get("ticker") if isinstance(s, Mapping) else getattr(s, "ticker", None)
        if isinstance(name, str):
            phrase = " ".join(w for w in _WORD.findall(name.lower()) if w not in _NAME_SUFFIXES)
            if len(phrase) >= 3 and phrase not in out:
                out.append(phrase)
        if isinstance(ticker, str) and ticker.strip():
            t = ticker.strip().upper()
            if len(t) >= MIN_TICKER_BAN_CHARS and t not in out:
                out.append(t)
    return out


def subject_problem(text: str, category: str, banned: Sequence[str] = ()) -> Optional[str]:
    """Why `text` cannot be this piece's subject, or None: the shared checks
    (`money_moves_art_style.subject_problem`), a length cap, and the piece's own company names (a
    lower-case term against the folded text) and tickers (an upper-case term, case-sensitive, as a
    whole word)."""
    problem = style.subject_problem(text, category)
    if problem is not None:
        return problem
    if len(text) > SUBJECT_MAX_CHARS:
        return f"too long ({len(text)} characters)"
    folded = " ".join(_WORD.findall(text.lower()))
    for term in banned:
        if not isinstance(term, str) or not term:
            continue
        haystack = text if term.isupper() else folded
        if re.search(rf"(?<![A-Za-z0-9]){re.escape(term)}(?![A-Za-z0-9])", haystack):
            return f"names the company {term!r}"
    return None


def _field(draft: Mapping[str, Any], key: str) -> str:
    value = draft.get(key)
    return " ".join(value.split())[:_FIELD_CAP] if isinstance(value, str) else ""


def subject_request(draft: Mapping[str, Any], *, attempt: int) -> str:
    """The subject prompt for this draft: the catalog's `--auto` prompt (title, subtitle, category)
    plus the article's key points as context. Opens with a nonce line so a retry after an unusable
    answer is never served that answer back from the response cache."""
    category = draft.get("category") if draft.get("category") in C.CATEGORIES else "blueprints"
    body = style.subject_prompt(_field(draft, "title"), _field(draft, "subtitle"), category)
    points: List[str] = []
    highlights = draft.get("keyHighlights")
    for h in (highlights if isinstance(highlights, list) else [])[:_HIGHLIGHTS_SHOWN]:
        if isinstance(h, Mapping):
            title, desc = _field(h, "title"), _field(h, "description")
            if title or desc:
                points.append(f"- {title}: {desc}".rstrip(": "))
    context = ("\n\nKEY POINTS OF THE ARTICLE (context only — the object still follows every rule "
               "above):\n" + "\n".join(points)) if points else ""
    return (f"REQUEST cover-art {_field(draft, 'slug')} attempt {int(attempt)}\n\n{body}{context}\n\n"
            "Reply with the subject description ONLY — one sentence.")


def image_prompt(subject: str, category: str, slug: str) -> Tuple[str, str]:
    """(the image prompt, its treatment): the catalog's BASE prompt for `subject` on the slug's
    deterministic treatment, in the category's palette (no per-slug palette override — that table
    is the catalog's)."""
    category = category if category in style.PALETTES else "blueprints"
    treatment = style.default_treatment(slug, category)
    return style.prompt_for(subject, category, treatment), treatment


def prompt_sha1(prompt: str) -> str:
    """sha1(prompt)[:12] — the catalog manifests' `art_prompt_sha1` form."""
    return hashlib.sha1(prompt.encode("utf-8")).hexdigest()[:12]


def _ids(draft: Mapping[str, Any], piece_id: Any) -> Dict[str, Any]:
    return {"piece_id": piece_id, "slug": draft.get("slug") if isinstance(draft.get("slug"), str) else None}


# ── the model calls ───────────────────────────────────────────────────────────────────────────


def _gemini() -> Any:
    from app.integrations.gemini import get_gemini_client

    return get_gemini_client()


async def write_subject(draft: Mapping[str, Any], *, banned: Sequence[str] = (), client: Any = None,
                        piece_id: Any = None) -> str:
    """One usable subject sentence for `draft` (module docstring step a). CoverArtSubjectError when
    the text call fails or `SUBJECT_ATTEMPTS` answers all break a rule (each logged)."""
    from app.integrations.gemini import is_length_cut

    client = client if client is not None else _gemini()
    category = draft.get("category") if draft.get("category") in C.CATEGORIES else "blueprints"
    last = "no answer"
    for attempt in range(1, SUBJECT_ATTEMPTS + 1):
        try:
            result = await client.generate_text(
                subject_request(draft, attempt=attempt), model_name=settings.LEARN_ART_SUBJECT_MODEL,
                max_output_tokens=SUBJECT_MAX_OUTPUT_TOKENS, thinking_budget=0)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            raise CoverArtSubjectError(f"subject call failed: {type(e).__name__}: {str(e)[:200]}",
                                       step="subject", **_ids(draft, piece_id)) from e
        result = result if isinstance(result, Mapping) else {}
        if is_length_cut(result.get("finish_reason")):
            last = f"answer cut at the output limit ({result.get('finish_reason')})"
        else:
            text = clean_subject(result.get("text"))
            problem = subject_problem(text, category, banned)
            if problem is None:
                return text
            last = problem
        logger.warning("learn cover art: subject attempt %d/%d for piece %s (%s) unusable: %s", attempt,
                       SUBJECT_ATTEMPTS, piece_id, draft.get("slug"), last)
    raise CoverArtSubjectError(f"no usable subject after {SUBJECT_ATTEMPTS} attempts: {last}",
                               step="subject", **_ids(draft, piece_id))


async def _render(prompt: str, *, client: Any, draft: Mapping[str, Any], piece_id: Any
                  ) -> Tuple[Dict[str, bytes], Tuple[int, int]]:
    """(the hero and card JPEGs, the master's size) — one image call, then Pillow off the loop."""
    from app.integrations.gemini import GeminiImageError

    try:
        blob = await client.generate_image(
            prompt, model_name=settings.LEARN_ART_MODEL, aspect_ratio=style.ART_ASPECT,
            image_size=style.ART_SIZE, usage_tag=USAGE_TAG)
    except asyncio.CancelledError:
        raise
    except GeminiImageError as e:
        raise CoverArtImageError(f"image call: {type(e).__name__}: {e}", step="image",
                                 **_ids(draft, piece_id)) from e
    except Exception as e:
        raise CoverArtImageError(f"image call: {type(e).__name__}: {str(e)[:200]}", step="image",
                                 **_ids(draft, piece_id)) from e
    if not isinstance(blob, (bytes, bytearray)) or not blob:
        raise CoverArtImageError("image call returned no bytes", step="image", **_ids(draft, piece_id))
    try:
        master, size = await asyncio.to_thread(style.encode_master, bytes(blob))
        derived = await asyncio.to_thread(style.derive_all_jpeg, master)
    except asyncio.CancelledError:
        raise
    except Exception as e:      # Pillow missing, or bytes that are not a whole image
        raise CoverArtImageError(f"image decode/derive: {type(e).__name__}: {str(e)[:200]}",
                                 step="derive", **_ids(draft, piece_id)) from e
    for name, data in derived.items():
        if not data or len(data) > MAX_OBJECT_BYTES:
            raise CoverArtImageError(f"{name} is {len(data or b'')} bytes (cap {MAX_OBJECT_BYTES})",
                                     step="derive", **_ids(draft, piece_id))
    return derived, size


# ── storage (the Supabase SDK is sync: every call inside asyncio.to_thread) ────────────────────


def _sb() -> Any:
    from app.database import get_supabase

    return get_supabase()


def _is_conflict(exc: BaseException) -> bool:
    """A Storage upload refused because the key already holds an object (`x-upsert: false`): storage3's
    error with status 409 / "Duplicate" (the status arrives as an int or a string)."""
    status = str(getattr(exc, "status", "") or getattr(exc, "statusCode", "") or "").strip()
    code = str(getattr(exc, "code", "") or "").lower()
    return status == "409" or code in ("duplicate", "409") or "'statuscode': 409" in str(exc).lower()


async def _stat(sb: Any, path: str) -> Optional[Dict[str, Any]]:
    """{"size", "mimetype"} of `path`, or None when ABSENT. Read from a prefix LIST (it carries each
    object's metadata; a HEAD does not). A failed listing raises CoverArtStorageError — never "absent"."""
    prefix, _, name = path.rpartition("/")
    try:
        listing = await asyncio.to_thread(
            lambda: sb.storage.from_(BUCKET).list(prefix, {"limit": 100, "search": name}))
    except Exception as e:
        raise CoverArtStorageError(f"storage LIST failed for {path}: {type(e).__name__}: {str(e)[:200]}",
                                   step="storage") from e
    for item in listing or []:
        if isinstance(item, Mapping) and item.get("name") == name:
            md = item.get("metadata") if isinstance(item.get("metadata"), Mapping) else {}
            return {"size": md.get("size", md.get("contentLength")), "mimetype": md.get("mimetype")}
    return None


def _same_size(stat: Optional[Mapping[str, Any]], n: int) -> bool:
    try:
        return stat is not None and stat.get("size") is not None and int(stat["size"]) == n
    except (TypeError, ValueError):
        return False


async def _upload(sb: Any, path: str, data: bytes) -> str:
    """Store `data` at `path` (never overwriting): "uploaded", or "reused" when a concurrent writer
    stored an object of the same size there first. CoverArtStorageError otherwise."""
    try:
        await asyncio.to_thread(lambda: sb.storage.from_(BUCKET).upload(
            path, data, {"content-type": CONTENT_TYPE, "cache-control": CACHE_CONTROL, "upsert": "false"}))
        return "uploaded"
    except asyncio.CancelledError:
        raise
    except Exception as e:
        if not _is_conflict(e):
            raise CoverArtStorageError(f"upload of {path} failed: {type(e).__name__}: {str(e)[:200]}",
                                       step="storage") from e
    stat = await _stat(sb, path)
    if _same_size(stat, len(data)):
        logger.info("learn cover art: %s was stored by a concurrent writer with the same size — reusing it",
                    path)
        return "reused"
    raise CoverArtStorageError(f"{path} already holds another object (size {(stat or {}).get('size')!r}, "
                               f"want {len(data)}) — left in place", step="storage")


async def _remove(sb: Any, paths: Sequence[str]) -> None:
    try:
        await asyncio.to_thread(lambda: sb.storage.from_(BUCKET).remove(list(paths)))
    except Exception as e:
        raise CoverArtStorageError(f"removing the half pair {list(paths)} failed: {type(e).__name__}: "
                                   f"{str(e)[:200]}", step="storage") from e


# ── the whole step ────────────────────────────────────────────────────────────────────────────


def _result(paths: Mapping[str, str], record: Dict[str, Any]) -> Dict[str, Any]:
    return {"imageUrl": public_url(paths["hero"]), "imageCardUrl": public_url(paths["card"]),
            "coverArt": record}


async def make_cover_art(draft: Mapping[str, Any], *, subjects: Iterable[Any] = (), client: Any = None,
                         sb: Any = None, piece_id: Any = None,
                         previous: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    """The cover art of `draft` (module docstring, steps a-f). Raises CoverArtError (or ValueError for
    a draft with no weekly slug); the build calls `cover_art_for`, which never raises. `previous` is the
    piece's last `validation.cover_art`: when the plates are reused, its subject and prompt hash are
    carried forward so the review still says what the picture shows."""
    if not isinstance(draft, Mapping):
        raise ValueError(f"cover art: the draft is {type(draft).__name__}, not an object")
    slug = draft.get("slug")
    paths = art_paths(slug)
    category = draft.get("category") if draft.get("category") in C.CATEGORIES else "blueprints"
    sb = sb if sb is not None else _sb()

    stats = {name: await _stat(sb, path) for name, path in paths.items()}
    present = [name for name, st in stats.items() if st is not None]
    if len(present) == len(paths):
        record: Dict[str, Any] = {"status": STATUS_OK, "reused": True,
                                  "treatment": style.default_treatment(slug, category)}
        prev = previous if isinstance(previous, Mapping) else {}
        if prev.get("imageUrl") == public_url(paths["hero"]):
            record.update({k: prev[k] for k in ("subject", "treatment", "model", "prompt_sha1")
                           if prev.get(k) is not None})
        logger.info("learn cover art: piece %s (%s) reuses its plates — no model call", piece_id, slug)
        return _result(paths, record)
    if present:
        # A half pair: an earlier run stored one derivative and failed on the other. The pair must be ONE
        # picture, and nothing points at a half pair (the URLs are returned only for a whole one).
        stale = [paths[name] for name in present]
        logger.warning("learn cover art: piece %s (%s) has a half pair %s — removing it and making the "
                       "pair again", piece_id, slug, stale)
        await _remove(sb, stale)

    client = client if client is not None else _gemini()
    subject = await write_subject(draft, banned=subject_ban_terms(subjects), client=client,
                                  piece_id=piece_id)
    prompt, treatment = image_prompt(subject, category, slug)
    derived, (width, height) = await _render(prompt, client=client, draft=draft, piece_id=piece_id)
    outcomes = {}
    for name, path in paths.items():
        outcomes[name] = await _upload(sb, path, derived[name])
    record = {"status": STATUS_OK, "reused": False, "subject": subject, "treatment": treatment,
              "model": str(settings.LEARN_ART_MODEL), "prompt_sha1": prompt_sha1(prompt),
              "width": width, "height": height,
              "bytes": {name: len(data) for name, data in derived.items()}}
    logger.info("learn cover art: piece %s (%s) → %s · %s · %dx%d (hero %d B, card %d B, %s)", piece_id,
                slug, treatment, subject[:80], width, height, len(derived["hero"]), len(derived["card"]),
                outcomes)
    return _result(paths, record)


# ── ready-made plates (a piece built before the art step: W44, owner 2026-11-01 "W44 now") ─────


def plate_problems(plates: Any) -> List[str]:
    """Why `plates` ({"hero": JPEG bytes, "card": JPEG bytes}, e.g. a one-off plate made with the
    catalog script) cannot be stored as a weekly piece's cover art, or [] (pure; Pillow reads each
    image whole, so a truncated file is refused): exactly the two derivatives, each a JPEG of its
    shipping size (`money_moves_art_style.DERIVATIVES`), non-empty and under `MAX_OBJECT_BYTES`."""
    if not isinstance(plates, Mapping):
        return [f"the plates are {type(plates).__name__}, not a mapping"]
    out: List[str] = []
    if set(plates) != set(style.DERIVATIVES):
        out.append(f"want exactly {sorted(style.DERIVATIVES)}, got {sorted(map(str, plates))}")
    for name, (w, h) in style.DERIVATIVES.items():
        data = plates.get(name)
        if not isinstance(data, (bytes, bytearray)) or not data:
            out.append(f"{name}: no bytes")
            continue
        if len(data) > MAX_OBJECT_BYTES:
            out.append(f"{name}: {len(data)} bytes (cap {MAX_OBJECT_BYTES})")
            continue
        try:
            import io

            from PIL import Image

            with Image.open(io.BytesIO(bytes(data))) as im:
                fmt, size = im.format, im.size
                im.load()
        except Exception as e:  # noqa: BLE001 — reported as the problem
            out.append(f"{name}: not a readable image ({type(e).__name__}: {str(e)[:120]})")
            continue
        if fmt != "JPEG":
            out.append(f"{name}: {fmt} is not a JPEG")
        if size != (w, h):
            out.append(f"{name}: {size[0]}x{size[1]}, want {w}x{h}")
    return out


async def store_plates(slug: Any, plates: Mapping[str, bytes], *, sb: Any = None,
                       piece_id: Any = None) -> Dict[str, Any]:
    """Store ready-made derivatives as the weekly piece `slug`'s cover art — NO model call. Same layout,
    bucket, headers and never-overwrite rule as `make_cover_art`, and the same result shape. A whole pair
    already in the bucket is reused only when both sizes equal the plates' (a rerun); another picture
    there is refused and left in place. A half pair is removed and the pair stored again, so the hero
    and the card are always one picture. CoverArtImageError for unusable plates (checked before any
    I/O), CoverArtStorageError for the bucket."""
    paths = art_paths(slug)
    problems = await asyncio.to_thread(plate_problems, plates)
    if problems:
        raise CoverArtImageError("ready-made plates refused: " + "; ".join(problems), step="plates",
                                 piece_id=piece_id, slug=slug)
    sb = sb if sb is not None else _sb()
    stats = {name: await _stat(sb, path) for name, path in paths.items()}
    present = [name for name, st in stats.items() if st is not None]
    record: Dict[str, Any] = {"status": STATUS_OK, "source": "ready-made plates",
                              "bytes": {name: len(plates[name]) for name in paths}}
    if len(present) == len(paths):
        differ = [paths[n] for n in paths if not _same_size(stats[n], len(plates[n]))]
        if differ:
            raise CoverArtStorageError(f"{differ} already hold another picture (sizes "
                                       f"{[(stats[n] or {}).get('size') for n in paths]!r}) — left in place",
                                       step="storage", piece_id=piece_id, slug=slug)
        logger.info("learn cover art: piece %s (%s) — the ready-made plates are already stored; reused",
                    piece_id, slug)
        return _result(paths, {**record, "reused": True})
    if present:
        stale = [paths[name] for name in present]
        logger.warning("learn cover art: piece %s (%s) has a half pair %s — removing it before storing the "
                       "ready-made pair", piece_id, slug, stale)
        await _remove(sb, stale)
    outcomes = {name: await _upload(sb, path, bytes(plates[name])) for name, path in paths.items()}
    logger.info("learn cover art: piece %s (%s) ready-made plates stored (%s)", piece_id, slug, outcomes)
    return _result(paths, {**record, "reused": False})


def _previous_record(validation: Any) -> Optional[Mapping[str, Any]]:
    record = validation.get("cover_art") if isinstance(validation, Mapping) else None
    return record if isinstance(record, Mapping) else None


async def cover_art_for(draft: Mapping[str, Any], *, subjects: Iterable[Any] = (), piece_id: Any = None,
                        previous_validation: Any = None, client: Any = None, sb: Any = None
                        ) -> Tuple[Optional[Dict[str, str]], Dict[str, Any]]:
    """The build's entry point: (the image fields to merge into the draft — `imageUrl` and
    `imageCardUrl` — or None, the record for `validation.cover_art`). NEVER raises (CancelledError
    aside): off → (None, {"status": "off"}); any failure or a step longer than
    `LEARN_ART_TIMEOUT_SECONDS` → (None, {"status": "failed", "error": …}) with a WARNING naming the
    piece, the slug and the error type."""
    if not settings.LEARN_ART_ENABLED:
        return None, {"status": STATUS_OFF}
    slug = draft.get("slug") if isinstance(draft, Mapping) else None
    timeout = float(settings.LEARN_ART_TIMEOUT_SECONDS)
    try:
        art = await asyncio.wait_for(
            make_cover_art(draft, subjects=subjects, client=client, sb=sb, piece_id=piece_id,
                           previous=_previous_record(previous_validation)),
            timeout=timeout)
    except asyncio.CancelledError:
        raise
    except CoverArtError as e:
        logger.warning("learn cover art: piece %s (%s) goes on without cover art — %s: %s", piece_id, slug,
                       type(e).__name__, e)
        return None, {"status": STATUS_FAILED, "error": f"{type(e).__name__}: {e}"[:_FIELD_CAP]}
    except TimeoutError as e:
        # Every model / storage error inside is wrapped in a CoverArtError, so a bare TimeoutError
        # here is this step's own bound.
        logger.warning("learn cover art: piece %s (%s) goes on without cover art — %s after %gs", piece_id,
                       slug, type(e).__name__, timeout)
        return None, {"status": STATUS_FAILED, "error": f"timed out after {timeout:g}s"}
    except Exception as e:  # a bug (or a draft with no weekly slug): artless, with the stack
        logger.warning("learn cover art: piece %s (%s) goes on without cover art — unexpected %s: %s",
                       piece_id, slug, type(e).__name__, e, exc_info=True)
        return None, {"status": STATUS_FAILED, "error": f"{type(e).__name__}: {e}"[:_FIELD_CAP]}
    record = dict(art["coverArt"])
    record["imageUrl"] = art["imageUrl"]
    return {"imageUrl": art["imageUrl"], "imageCardUrl": art["imageCardUrl"]}, record


__all__ = [
    "BUCKET", "CACHE_CONTROL", "CONTENT_TYPE", "CoverArtError", "CoverArtImageError",
    "CoverArtStorageError", "CoverArtSubjectError", "MIN_TICKER_BAN_CHARS", "PREFIX", "SUBJECT_ATTEMPTS",
    "art_paths", "clean_subject", "cover_art_for", "image_prompt", "make_cover_art", "plate_problems",
    "prompt_sha1", "public_url", "store_plates", "subject_ban_terms", "subject_problem", "subject_request",
    "write_subject",
]
