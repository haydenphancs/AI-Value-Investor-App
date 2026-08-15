"""
Seed Money Moves article content into Supabase + publish narration audio.

Reads the SAME bundled JSON the iOS app ships (single source of truth; frontend path locally,
vendored backend/data/money_moves.json on Railway). For each article it:
  1. Uploads its narration clip (backend/data/money_moves_audio/<slug>.m4a, produced by
     generate_money_moves_audio.py) to the public 'money-moves-media' bucket, if present.
  2. Bakes the public audioUrl into the article `content` (so the iOS DTO decodes it) and the
     first-class audio_url column; has_audio_version follows audio presence.
  3. Upserts one row per article into public.money_move_articles, storing the full iOS-shaped
     article in `content` JSONB plus the queryable first-class columns.

Articles without a generated clip are still seeded (audioUrl stays null) so the catalog is
served from the backend regardless of audio readiness. Deterministic id per slug (uuid5) so
re-running updates in place instead of duplicating.

Prerequisites:
  - Migration 065_money_moves_content.sql applied (columns + grants + bucket).
  - backend/.env with SUPABASE service-role credentials (uses app.database.get_supabase()).

Usage (from backend/):
    ./venv/bin/python scripts/seed_money_moves.py
    ./venv/bin/python scripts/seed_money_moves.py --dry-run     # build + print, no writes
"""
import json
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Make `app` importable when run from backend/
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.database import get_supabase  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]      # backend/
REPO = ROOT.parent
# Frontend tree is the source of truth locally; on Railway only backend/ ships, so fall back
# to the vendored copy at backend/data/money_moves.json.
_FRONTEND_JSON = REPO / "frontend/ios/ios/Resources/MoneyMoves/money_moves.json"
JSON_PATH = _FRONTEND_JSON if _FRONTEND_JSON.exists() else ROOT / "data/money_moves.json"
AUDIO_DIR = ROOT / "data/money_moves_audio"
ART_DIR = ROOT / "data/money_moves_art"

BUCKET = "money-moves-media"
# Artwork lives in its OWN public bucket, never in money-moves-media. Narration is Pro/Max
# and money-moves-media went private in migration 128; artwork is FREE and must render for
# a locked, signed-out reader, so it cannot be behind a signed URL. See 137's header.
IMAGE_BUCKET = "money-moves-images"
IMAGE_PREFIX = "articles"
# Stable namespace so the same article slug always maps to the same id.
NS = uuid.UUID("b2c3d4e5-0000-4000-8000-000000000000")
DRY = "--dry-run" in sys.argv
FORCE = "--force" in sys.argv   # overwrite existing bucket audio (for re-runs / revoice)

_EXISTING_AUDIO: set[str] = set()   # objects already in money-moves-media/audio/ — skip re-upload
_EXISTING_IMAGES: set[str] = set()  # objects already in money-moves-images/articles/
# slug -> the published_at ALREADY stored for that row. Loaded once in main() so a re-seed
# preserves the original publication date instead of re-deriving it. See _resolve_published_at.
_EXISTING_PUBLISHED_AT: dict[str, str] = {}
# False until the bucket is confirmed to exist. Without this a --dry-run reports [art] for
# every article while the real run that follows would publish none of it — the upload throws
# and is swallowed per-file. A dry run that disagrees with the real run is worse than no dry
# run, because it is trusted.
_IMAGE_BUCKET_OK = False

# manifest key -> the content field iOS decodes. Order matters only for logging.
_ART_FIELDS = {"hero": "imageUrl", "card": "imageCardUrl"}


def upload_audio(sb, slug: str) -> str | None:
    """Public URL for <slug>.m4a. Reuses the bucket object when it already exists — even if the
    local clip is absent — so a re-seed from an env without backend/data/money_moves_audio/ never
    wipes a previously-published audio_url. Uploads from the local clip otherwise."""
    path = f"audio/{slug}.m4a"
    local = AUDIO_DIR / f"{slug}.m4a"
    in_bucket = f"{slug}.m4a" in _EXISTING_AUDIO
    if in_bucket and not FORCE:
        return sb.storage.from_(BUCKET).get_public_url(path)
    if not local.exists():
        return sb.storage.from_(BUCKET).get_public_url(path) if in_bucket else None
    if not DRY:
        sb.storage.from_(BUCKET).upload(
            path,
            local.read_bytes(),
            {"content-type": "audio/mp4", "upsert": "true"},
        )
        _EXISTING_AUDIO.add(f"{slug}.m4a")
        print(f"    + uploaded {slug}.m4a")
    return sb.storage.from_(BUCKET).get_public_url(path)


def upload_art(sb, slug: str) -> dict:
    """Publish this article's plates and return {"imageUrl": ..., "imageCardUrl": ...}.

    Three behaviours that are each load-bearing:

    1. SKIPS ON THE CONTENT SHA256, NOT THE FILENAME. Artwork gets iterated on and the
       filename never changes across a re-roll, so a name-based skip would silently keep the
       superseded picture live forever. The generator stamps each derivative's sha256 into
       the manifest; we upload whenever the bucket has not seen that exact content.
    2. NEVER RAISES. One article's Storage error must not abort the other twelve, and a
       missing money-moves-images bucket (migration 137 not applied yet) must leave the
       audio-only seed working exactly as before.
    3. A MISSING LOCAL FILE NEVER WIPES A LIVE URL. Seeding from a box without
       backend/data/money_moves_art/ reuses whatever is already published, the same rule
       upload_audio follows for narration.
    """
    if not _IMAGE_BUCKET_OK:
        return {}                                   # migration 137 not applied — see main()
    man_path = ART_DIR / f"{slug}.manifest.json"
    if not man_path.exists():
        return {}                                   # no art yet — the normal case
    try:
        man = json.loads(man_path.read_text())
    except Exception as exc:  # noqa: BLE001
        print(f"    ! unreadable manifest for {slug}: {type(exc).__name__}: {exc}")
        return {}

    out: dict[str, str] = {}
    uploaded = man.setdefault("uploaded", {})
    for key, field in _ART_FIELDS.items():
        rec = (man.get("masters") or {}).get(key) or {}
        name, sha = rec.get("file"), rec.get("sha256")
        if not name:
            print(f"    ! {slug}: manifest has no masters.{key} — run generate_money_moves_art.py")
            continue
        path = f"{IMAGE_PREFIX}/{name}"
        # `or None` guards the empty string: iOS checks for a non-empty url, and an empty
        # one would still occupy the slot and suppress the gradient fallback.
        public = (sb.storage.from_(IMAGE_BUCKET).get_public_url(path).rstrip("?") or None)
        local = ART_DIR / name
        already = name in _EXISTING_IMAGES and uploaded.get(key) == sha
        if not already and local.exists() and not DRY:
            try:
                sb.storage.from_(IMAGE_BUCKET).upload(
                    path,
                    local.read_bytes(),
                    # cache-control 300 while the art is still being iterated: the filename
                    # is stable across re-rolls, so a long TTL would pin a superseded plate
                    # on device. Raise it once the set is settled.
                    {"content-type": "image/jpeg", "upsert": "true", "cache-control": "300"},
                )
            except Exception as exc:  # noqa: BLE001
                print(f"    ! image upload failed for {slug}.{key}: "
                      f"{type(exc).__name__}: {exc}")
                continue
            _EXISTING_IMAGES.add(name)
            uploaded[key] = sha
            print(f"    + uploaded {name}")
        elif not already and not local.exists() and name not in _EXISTING_IMAGES:
            continue                                # nothing local, nothing published
        if public:
            out[field] = public

    if out and not DRY:
        man["image_url"] = out.get("imageUrl")
        man_path.write_text(json.dumps(man, indent=2) + "\n")
    return out


def upload_placeholder_art(sb) -> dict[str, str]:
    """Publish plates for catalog topics that have no article row yet.

    `MoneyMove.sampleData` carries a few "coming soon" teasers that are not in
    money_moves.json, so there is no `content` blob to bake a URL into and nothing for the
    endpoint to serve. Their plates still go to the same bucket at the same
    `articles/<slug>.*` path — the slug is the one the article will have once written — and
    the URL is compiled into the Swift static instead (the gen_book_covers_swift.py
    precedent). When the article is finally authored, its row supersedes the static and the
    plate is already at the right path.

    Returns {slug: cardUrl} purely so the run can print what iOS should be pointing at.
    """
    import generate_money_moves_art as art_gen

    out: dict[str, str] = {}
    for slug in art_gen.PLACEHOLDER_TOPICS:
        urls = upload_art(sb, slug)
        if urls.get("imageCardUrl"):
            out[slug] = urls["imageCardUrl"]
    return out


def _resolve_published_at(article: dict, slug: str) -> str:
    """The publication timestamp for `slug`, resolved so a RE-SEED NEVER MOVES A DATE.

    `publishedDaysAgo` is a RELATIVE offset, and this used to be the only source:
    `now - publishedDaysAgo`, written unconditionally through `upsert(on_conflict="id")`.
    That re-dated the entire catalog on every run. Every offset in the catalog is 0-13 days,
    so the whole library perpetually claimed to have been published in the last fortnight:
    re-seed in October and an article read in August comes back stamped "Today".

    Harmless while nothing selected the column — but `published_at` is now the label rendered
    on every card AND the sort key behind the row's "Most Recent" heading, so the jump is
    user-visible. Resolution order:

      1. An explicit `publishedAt` ISO string in the authored JSON. Authoritative: it is the
         only source that is a FACT rather than a re-derivation, so an editor can pin a real
         date and it survives everything below.
      2. The value already stored for this row. This is what makes a re-seed a no-op for
         dates — the first publish decides, and re-running to add artwork or a new article
         leaves the other 15 untouched.
      3. `now - publishedDaysAgo`. Reached only on a FIRST publish, which preserves the
         existing behaviour for a genuinely new slug.
    """
    explicit = article.get("publishedAt")
    if isinstance(explicit, str) and explicit.strip():
        return explicit.strip()

    existing = _EXISTING_PUBLISHED_AT.get(slug)
    if existing:
        return existing

    days_ago = article.get("publishedDaysAgo", 3)
    if slug in _EXISTING_PUBLISHED_AT:
        # The row exists but stores NULL. Deriving is the only option left, so this is correct
        # — but it is also the one branch that can move a date that a reader already saw, and
        # it is invisible in the row-per-article log below. Say so.
        print(f"    ! {slug}: row exists with no published_at — deriving from "
              f"publishedDaysAgo={days_ago}; this date will move again on the next re-seed "
              f"unless it is pinned in the JSON as publishedAt")
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()


def build_row(sb, article: dict, sort_order: int) -> dict:
    slug = article["slug"]
    published_at = _resolve_published_at(article, slug)

    audio_url = upload_audio(sb, slug)
    if audio_url:
        article["audioUrl"] = audio_url          # baked into the iOS MoneyMoveArticleDTO passthrough
        article["hasAudioVersion"] = True

    # Baked into the same passthrough blob, which is why new artwork reaches installed apps
    # with no App Store release: the endpoint serves `content` verbatim.
    art = upload_art(sb, slug)
    article.update(art)

    return {
        "id": str(uuid.uuid5(NS, slug)),
        "slug": slug,
        "title": article["title"],
        "subtitle": article.get("subtitle"),
        "category": article["category"],          # enum money_move_category (blueprints/valueTraps/battles)
        "published_at": published_at,
        "read_time_minutes": article.get("readTimeMinutes"),
        "view_count": article.get("viewCount"),
        "is_featured": bool(article.get("isFeatured", False)),
        "has_audio_version": bool(audio_url),
        "audio_url": audio_url,
        "audio_duration_seconds": article.get("audioDurationSeconds"),
        "image_url": art.get("imageUrl"),
        "sort_order": sort_order,
        "content": article,                       # full iOS MoneyMoveArticleDTO passthrough
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


def main():
    data = json.loads(JSON_PATH.read_text())
    articles = data["articles"]

    sb = get_supabase()

    # Learn the publication dates already stored, so `_resolve_published_at` can preserve them
    # instead of re-deriving from the relative `publishedDaysAgo` offset on every run.
    #
    # A FAILURE HERE MUST ABORT. Degrading to "no existing dates" is not a safe fallback: it
    # silently takes the derive branch for all 16 articles and re-dates the whole catalog —
    # exactly the bug this exists to prevent, with no signal that it happened. A transient
    # Supabase 520 must not be able to rewrite every publication date.
    try:
        stored = sb.table("money_move_articles").select("slug, published_at").execute()
        for row in getattr(stored, "data", None) or []:
            slug = row.get("slug")
            if slug:
                _EXISTING_PUBLISHED_AT[slug] = row.get("published_at")
        dated = sum(1 for v in _EXISTING_PUBLISHED_AT.values() if v)
        print(f"{len(_EXISTING_PUBLISHED_AT)} existing row(s), {dated} with a publication date "
              f"— those dates will be preserved.\n")
    except Exception as exc:
        raise SystemExit(
            f"ABORT: could not read existing published_at ({type(exc).__name__}: {exc}). "
            f"Seeding now would re-date every article in the catalog. Fix the connection and "
            f"re-run."
        ) from exc

    # Learn which audio objects already exist so we only upload new ones.
    try:
        listed = sb.storage.from_(BUCKET).list("audio", {"limit": 2000})
        _EXISTING_AUDIO.update(item["name"] for item in listed)
        print(f"{len(_EXISTING_AUDIO)} audio file(s) already in Storage — will skip those.\n")
    except Exception as exc:  # noqa: BLE001
        print(f"(could not list existing audio: {exc})\n")

    # Same for artwork, in its own try/except: a missing money-moves-images bucket (migration
    # 137 not applied yet) must print a note and let the audio-only seed proceed untouched.
    #
    # The bucket is PROBED separately rather than inferred from the listing, because
    # `.list()` on a bucket that does not exist returns an empty list rather than raising —
    # so "migration 137 has not been applied" and "the bucket is there and empty" print the
    # identical `0 image(s)` line. They call for opposite actions, so they must not look
    # the same.
    global _IMAGE_BUCKET_OK
    try:
        sb.storage.get_bucket(IMAGE_BUCKET)
        _IMAGE_BUCKET_OK = True
    except Exception as exc:  # noqa: BLE001
        print(f"⚠ bucket '{IMAGE_BUCKET}' not found — apply migration "
              f"137_money_moves_images.sql, then re-run. Seeding audio and text only. "
              f"({type(exc).__name__}: {exc})\n")
    else:
        try:
            listed = sb.storage.from_(IMAGE_BUCKET).list(IMAGE_PREFIX, {"limit": 500})
            _EXISTING_IMAGES.update(item["name"] for item in listed)
            print(f"{len(_EXISTING_IMAGES)} image(s) already in Storage — "
                  f"will skip unchanged.\n")
        except Exception as exc:  # noqa: BLE001
            print(f"(could not list {IMAGE_BUCKET}/{IMAGE_PREFIX}: "
                  f"{type(exc).__name__}: {exc})\n")

    rows = []
    for idx, article in enumerate(articles):
        row = build_row(sb, article, sort_order=idx)
        flag = "audio" if row["audio_url"] else "no-audio"
        art_flag = "art" if row["image_url"] else "no-art"
        print(f"[{idx}] {article['title']} ({article['slug']}) [{flag}] [{art_flag}]")
        rows.append(row)

    # Related-article tiles are matched by TITLE in the authored JSON, not by slug, and they
    # carry their own copy of the fields the tile renders. Stamping the card URL here — after
    # every article's art is published — means the tile needs no runtime title->slug lookup
    # on the client and no second request. Done as a second pass because an article's related
    # list can point at an article seeded later in the loop.
    by_title = {r["content"]["title"]: r["content"].get("imageCardUrl") for r in rows}
    stamped = 0
    for row in rows:
        for rel in row["content"].get("relatedArticles") or []:
            if not isinstance(rel, dict):
                continue
            url = by_title.get(rel.get("title"))
            if url:
                rel["imageCardUrl"] = url
                stamped += 1
    if stamped:
        print(f"\nStamped {stamped} related-article tile(s) with artwork.")

    # Teaser topics with no row yet. Printed so the Swift statics can be checked against
    # what was actually published rather than assumed.
    try:
        sys.path.insert(0, str(ROOT / "scripts"))
        placeholders = upload_placeholder_art(sb)
        if placeholders:
            print(f"\n{len(placeholders)} placeholder topic(s) published "
                  f"(URLs are compiled into MoneyMove.sampleData):")
            for slug, url in placeholders.items():
                print(f"    {slug}\n      {url}")
    except Exception as exc:  # noqa: BLE001 — teasers must never break the real seed
        print(f"\n(placeholder art skipped: {type(exc).__name__}: {exc})")

    if DRY:
        print(f"\n[dry-run] built {len(rows)} article row(s); no writes performed.")
        sample = dict(rows[0])
        sample["content"] = "<full article DTO omitted>"
        print(json.dumps(sample, indent=2))
        return

    sb.table("money_move_articles").upsert(rows, on_conflict="id").execute()
    print(f"\nUpserted {len(rows)} article(s) into public.money_move_articles.")


if __name__ == "__main__":
    main()
