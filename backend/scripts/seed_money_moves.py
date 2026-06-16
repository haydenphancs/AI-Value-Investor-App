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

BUCKET = "money-moves-media"
# Stable namespace so the same article slug always maps to the same id.
NS = uuid.UUID("b2c3d4e5-0000-4000-8000-000000000000")
DRY = "--dry-run" in sys.argv

_EXISTING_AUDIO: set[str] = set()   # objects already in money-moves-media/audio/ — skip re-upload


def upload_audio(sb, slug: str) -> str | None:
    """Upload <slug>.m4a to money-moves-media/audio/ (skip if already there); return public URL."""
    local = AUDIO_DIR / f"{slug}.m4a"
    if not local.exists():
        return None
    path = f"audio/{slug}.m4a"
    if f"{slug}.m4a" not in _EXISTING_AUDIO and not DRY:
        sb.storage.from_(BUCKET).upload(
            path,
            local.read_bytes(),
            {"content-type": "audio/mp4", "upsert": "true"},
        )
        _EXISTING_AUDIO.add(f"{slug}.m4a")
        print(f"    + uploaded {slug}.m4a")
    return sb.storage.from_(BUCKET).get_public_url(path)


def build_row(sb, article: dict, sort_order: int) -> dict:
    slug = article["slug"]
    days_ago = article.get("publishedDaysAgo", 3)
    published_at = (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()

    audio_url = upload_audio(sb, slug)
    if audio_url:
        article["audioUrl"] = audio_url          # baked into the iOS MoneyMoveArticleDTO passthrough
        article["hasAudioVersion"] = True

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
        "sort_order": sort_order,
        "content": article,                       # full iOS MoneyMoveArticleDTO passthrough
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


def main():
    data = json.loads(JSON_PATH.read_text())
    articles = data["articles"]

    sb = get_supabase()

    # Learn which audio objects already exist so we only upload new ones.
    try:
        listed = sb.storage.from_(BUCKET).list("audio", {"limit": 2000})
        _EXISTING_AUDIO.update(item["name"] for item in listed)
        print(f"{len(_EXISTING_AUDIO)} audio file(s) already in Storage — will skip those.\n")
    except Exception as exc:  # noqa: BLE001
        print(f"(could not list existing audio: {exc})\n")

    rows = []
    for idx, article in enumerate(articles):
        row = build_row(sb, article, sort_order=idx)
        flag = "audio" if row["audio_url"] else "no-audio"
        print(f"[{idx}] {article['title']} ({article['slug']}) [{flag}]")
        rows.append(row)

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
