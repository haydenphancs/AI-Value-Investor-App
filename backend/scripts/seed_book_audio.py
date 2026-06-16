"""
Publish per-BOOK narration audio to Supabase Storage.

For each manifest produced by generate_book_audio.py (backend/data/book_audio/<order>_<slug>.manifest.json):
  1. Upload its <order>_<slug>.m4a to the public 'book-media' bucket under audio/<file> (skip if
     already there, unless --force).
  2. Capture the public URL and write it back into the manifest as `audio_url` (record of truth;
     also consumed by gen_book_audio_swift.py to bake the URL into BookAudioContent.swift).

Books are static client-side content (BooksContent.swift), so there is NO content table to seed —
this only publishes the audio file. The per-book audio URL is deterministic, so the iOS app can
reference it without a serving endpoint.

Prerequisites:
  - Migration 068_book_media_bucket.sql applied (creates the public 'book-media' bucket).
  - backend/.env with SUPABASE service-role credentials (uses app.database.get_supabase()).

Usage (from backend/):
    ./venv/bin/python scripts/seed_book_audio.py            # all manifests in data/book_audio/
    ./venv/bin/python scripts/seed_book_audio.py 1          # one curriculum order (filename prefix)
    ./venv/bin/python scripts/seed_book_audio.py --force    # re-upload even if already present
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # make `app` importable
from app.database import get_supabase  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]      # backend/
AUDIO_DIR = ROOT / "data/book_audio"
BUCKET = "book-media"

FORCE = "--force" in sys.argv
only = next((a for a in sys.argv[1:] if not a.startswith("-")), None)


def main():
    manifests = sorted(AUDIO_DIR.glob("*.manifest.json"))
    if only:
        manifests = [m for m in manifests if m.name.startswith(f"{only}_")]
    if not manifests:
        raise SystemExit(f"No manifests in {AUDIO_DIR} (run generate_book_audio.py first).")

    sb = get_supabase()

    # Learn which audio objects already exist so we only upload new ones.
    existing: set[str] = set()
    try:
        listed = sb.storage.from_(BUCKET).list("audio", {"limit": 2000})
        existing.update(item["name"] for item in listed)
        print(f"{len(existing)} audio file(s) already in book-media/audio — will skip those.\n")
    except Exception as exc:  # noqa: BLE001
        print(f"(could not list existing audio: {exc})\n")

    for mpath in manifests:
        manifest = json.loads(mpath.read_text())
        fname = manifest["audio_file"]
        local = AUDIO_DIR / fname
        if not local.exists():
            print(f"  {fname:34s} (m4a missing, skip)")
            continue

        path = f"audio/{fname}"
        if fname in existing and not FORCE:
            print(f"  {fname:34s} (exists, skip upload)")
        else:
            sb.storage.from_(BUCKET).upload(
                path,
                local.read_bytes(),
                {"content-type": "audio/mp4", "upsert": "true"},
            )
            print(f"  {fname:34s} + uploaded ({local.stat().st_size // 1024} KB)")

        public_url = sb.storage.from_(BUCKET).get_public_url(path)
        manifest["audio_url"] = public_url
        mpath.write_text(json.dumps(manifest, indent=2))
        print(f"     -> {public_url}")

    print(f"\nDone. {len(manifests)} book audio file(s) published to {BUCKET}.")
    print("Next: ./venv/bin/python scripts/gen_book_audio_swift.py  (bakes URLs + core offsets into iOS)")


if __name__ == "__main__":
    main()
