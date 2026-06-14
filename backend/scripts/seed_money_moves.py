"""
Seed the Money Moves article content into Supabase.

Reads the SAME bundled JSON the iOS app ships (single source of truth) and upserts one
row per article into public.money_move_articles, storing the full iOS-shaped article in
the `content` JSONB column plus the queryable first-class columns. The narration voice
is generated/uploaded separately (a later step); audio_url is left NULL here.

Deterministic id per slug (uuid5) so re-running updates in place instead of duplicating.

Prerequisites:
  - Migration 065_money_moves_content.sql applied (adds columns + grants + bucket).
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

REPO = Path(__file__).resolve().parents[2]
JSON_PATH = REPO / "frontend/ios/ios/Resources/MoneyMoves/money_moves.json"

# Stable namespace so the same article slug always maps to the same id.
NS = uuid.UUID("b2c3d4e5-0000-4000-8000-000000000000")
DRY = "--dry-run" in sys.argv


def build_row(article: dict, sort_order: int) -> dict:
    slug = article["slug"]
    days_ago = article.get("publishedDaysAgo", 3)
    published_at = (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()
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
        "has_audio_version": bool(article.get("hasAudioVersion", False)),
        "sort_order": sort_order,
        "content": article,                       # full iOS MoneyMoveArticleDTO passthrough
        "updated_at": datetime.now(timezone.utc).isoformat(),
        # audio_url / audio_duration_seconds left unset — the voice is generated later.
    }


def main():
    data = json.loads(JSON_PATH.read_text())
    articles = data["articles"]

    rows = []
    for idx, article in enumerate(articles):
        print(f"[{idx}] {article['title']} ({article['slug']})")
        rows.append(build_row(article, sort_order=idx))

    if DRY:
        print(f"\n[dry-run] built {len(rows)} article row(s); no writes performed.")
        sample = dict(rows[0])
        sample["content"] = "<full article DTO omitted>"
        print(json.dumps(sample, indent=2))
        return

    sb = get_supabase()
    sb.table("money_move_articles").upsert(rows, on_conflict="id").execute()
    print(f"\nUpserted {len(rows)} article(s) into public.money_move_articles.")


if __name__ == "__main__":
    main()
