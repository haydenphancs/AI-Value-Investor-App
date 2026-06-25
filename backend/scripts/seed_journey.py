"""
Seed the Investor Journey content into Supabase.

Steps:
  1. Upload each lesson's narration clips (from backend/data/journey_audio/, produced
     by generate_journey_audio.py) to the public 'journey-media' Storage bucket.
  2. Build each lesson's story_content JSON (the iOS LessonStoryContent shape) with the
     public audio URLs baked into the cards.
  3. Upsert one row per lesson into public.lessons (deterministic id per lesson key, so
     re-running updates in place instead of duplicating).

Prerequisites:
  - Migration 061_journey_media_bucket.sql applied (creates the bucket + policies).
  - generate_journey_audio.py already run (clips present in backend/data/journey_audio/).
  - backend/.env with SUPABASE service-role credentials (uses app.database.get_supabase()).

Usage (from backend/):
    ./venv/bin/python scripts/seed_journey.py
    ./venv/bin/python scripts/seed_journey.py --dry-run     # build + print, no writes
"""
import json
import re
import sys
import uuid
from pathlib import Path

# Make `app` importable when run from backend/
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.database import get_supabase  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
BACKEND = Path(__file__).resolve().parents[1]
# Frontend tree is the source of truth locally; on Railway only backend/ ships,
# so fall back to the vendored copy at backend/data/journey_lessons.json.
_FRONTEND_JSON = REPO / "frontend/ios/ios/Resources/Journey/journey_lessons.json"
JSON_PATH = _FRONTEND_JSON if _FRONTEND_JSON.exists() else BACKEND / "data/journey_lessons.json"
AUDIO_DIR = BACKEND / "data/journey_audio"

BUCKET = "journey-media"
# Stable namespace so the same lesson key always maps to the same lessons.id.
NS = uuid.UUID("a1b2c3d4-0000-4000-8000-000000000000")
DRY = "--dry-run" in sys.argv
FORCE = "--force" in sys.argv   # overwrite existing bucket audio (needed to replace the old Gemini clips)

_LEVEL_TOTALS: dict[str, int] = {}
_EXISTING_AUDIO: set[str] = set()   # objects already in journey-media/audio/ — skip re-upload


def lesson_key(cards: list[dict]) -> str:
    """Derive the lesson key from the first card's audioClip ('mr_market_01' -> 'mr_market')."""
    for c in cards:
        clip = c.get("audioClip")
        if clip:
            return re.sub(r"_\d+$", "", clip)
    return ""


def upload_audio(sb, clip: str) -> str | None:
    """Upload <clip>.m4a to journey-media/audio/ (skipping if already there) and return its public URL."""
    path = f"audio/{clip}.m4a"
    local = AUDIO_DIR / f"{clip}.m4a"
    in_bucket = f"{clip}.m4a" in _EXISTING_AUDIO
    if in_bucket and not FORCE:
        # Already in the bucket from a prior run — reuse its URL even if the local clip is absent,
        # so a re-seed from an env without backend/data/journey_audio/ never wipes a good audioUrl.
        return sb.storage.from_(BUCKET).get_public_url(path)
    if not local.exists():
        if in_bucket:
            return sb.storage.from_(BUCKET).get_public_url(path)  # --force but no local clip: keep existing
        print(f"    ! missing audio {local.name} — skipping (audioUrl will be null)")
        return None
    if not DRY:
        sb.storage.from_(BUCKET).upload(
            path,
            local.read_bytes(),
            {"content-type": "audio/mp4", "upsert": "true"},
        )
        _EXISTING_AUDIO.add(f"{clip}.m4a")
        print(f"    + uploaded {clip}.m4a")
    return sb.storage.from_(BUCKET).get_public_url(path)


def build_story_content(sb, lesson: dict) -> dict:
    title = lesson["title"]
    level = lesson["level"]
    sort_order = lesson["sortOrder"]
    cards_out = []
    for card in lesson["cards"]:
        clip = card.get("audioClip")
        audio_url = upload_audio(sb, clip) if clip else None
        cards_out.append({
            "type": card["type"],
            "headline": card.get("headline"),
            "text": card.get("text"),          # keeps **highlight** markup for iOS
            "audioUrl": audio_url,
            "imageUrl": None,                  # filled in later when artwork exists
            "videoUrl": None,
            "cta": card.get("cta"),
            # Per-word read-along timings (from align_journey_audio.py); null until aligned.
            "readAlongWords": card.get("readAlongWords"),
        })
    return {
        "lessonLabel": f"LESSON {sort_order}: {title.upper()}",
        "lessonNumber": sort_order,
        "totalLessonsInLevel": _LEVEL_TOTALS[level],
        "estimatedMinutes": lesson.get("estimatedMinutes"),
        "cards": cards_out,
    }


def main():
    data = json.loads(JSON_PATH.read_text())
    lessons = data["lessons"]
    for l in lessons:
        _LEVEL_TOTALS[l["level"]] = _LEVEL_TOTALS.get(l["level"], 0) + 1

    sb = get_supabase()

    # Learn which audio objects already exist so we only upload new ones.
    try:
        listed = sb.storage.from_(BUCKET).list("audio", {"limit": 2000})
        _EXISTING_AUDIO.update(item["name"] for item in listed)
        print(f"{len(_EXISTING_AUDIO)} audio file(s) already in Storage — will skip those.\n")
    except Exception as exc:  # noqa: BLE001
        print(f"(could not list existing audio: {exc})\n")

    rows = []
    for lesson in lessons:
        key = lesson_key(lesson["cards"])
        print(f"[{lesson['level']}/{lesson['sortOrder']}] {lesson['title']} ({key})")
        story = build_story_content(sb, lesson)
        rows.append({
            "id": str(uuid.uuid5(NS, key)),
            "title": lesson["title"],
            "description": lesson.get("description"),
            "level": lesson["level"],
            "duration_minutes": lesson.get("estimatedMinutes"),
            "category": lesson.get("category", "standard"),
            "sort_order": lesson["sortOrder"],
            "story_content": story,
        })

    if DRY:
        print(f"\n[dry-run] built {len(rows)} lesson rows; no writes performed.")
        print(json.dumps(rows[0]["story_content"], indent=2)[:800])
        return

    sb.table("lessons").upsert(rows, on_conflict="id").execute()
    print(f"\nUpserted {len(rows)} lessons into public.lessons.")


if __name__ == "__main__":
    main()
