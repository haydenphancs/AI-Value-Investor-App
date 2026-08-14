"""
Stage 3 of 4 — publish the composited covers to Supabase Storage.

Uploads both masters per book to the PUBLIC `book-covers` bucket:
    covers/<order>_<slug>.thumb.jpg     240x330   the three 80x110pt cards
    covers/<order>_<slug>.hero.jpg      480x660   BookDetailView's 160x220pt hero

⚠️ NOT `book-media`. That bucket goes PRIVATE in migration 128 because narration is
Pro/Max. A cover is FREE — a locked, signed-out user must still see it — and its URL
is compiled into BookCoverContent.swift with no signing path. Covers live in their own
public bucket (migration 133) and must stay there.

DELIBERATE DEVIATION from seed_book_audio.py: that script skips on FILENAME presence,
which is right for an .m4a regenerated twice ever. A cover's whole point is iteration,
so name-based skipping would make every recompose silently upload nothing. This skips
on CONTENT HASH instead — recompose then re-seed just works, and an unchanged file
still costs no upload.

Prerequisites:
  - migration 133_book_covers_bucket.sql applied (creates the public bucket)
  - backend/.env with SUPABASE service-role credentials

Usage (from backend/):
    ./venv/bin/python scripts/seed_book_covers.py            # all books
    ./venv/bin/python scripts/seed_book_covers.py 7          # one book
    ./venv/bin/python scripts/seed_book_covers.py --force    # re-upload regardless
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # make `app` importable
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.database import get_supabase  # noqa: E402
from generate_book_cover_art import BOOKS, read_manifest, write_manifest  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data/book_covers"
BUCKET = "book-covers"
PREFIX = "covers"

FORCE = "--force" in sys.argv
ONLY = [int(a) for a in sys.argv[1:] if a.isdigit()]


def main():
    orders = ONLY or sorted(BOOKS)
    sb = get_supabase()

    uploaded = skipped = 0
    failures = []
    for order in orders:
        slug = BOOKS[order][0]
        man = read_manifest(order)
        masters = man.get("masters")
        if not masters:
            print(f"  {order:2d} {slug:44s} (not composed yet, skip)")
            continue

        urls = {}
        for label in ("thumb", "hero"):
            rec = masters[label]
            local = DATA / rec["file"]
            path = f"{PREFIX}/{rec['file']}"
            if not local.exists():
                print(f"  {order:2d} {slug:34s} {label:5s} (missing {local.name}, skip)")
                continue

            already = man.get("uploaded", {}).get(label)
            if already == rec["sha256"] and not FORCE:
                print(f"  {order:2d} {slug:34s} {label:5s} (unchanged, skip)")
                skipped += 1
            else:
                try:
                    sb.storage.from_(BUCKET).upload(
                        path,
                        local.read_bytes(),
                        {"content-type": "image/jpeg", "upsert": "true",
                         "cache-control": "300"},
                    )
                except Exception as exc:  # noqa: BLE001
                    # One object failing must not abort the other nineteen.
                    print(f"  {order:2d} {slug:34s} {label:5s} FAILED "
                          f"{type(exc).__name__}: {str(exc)[:160]}")
                    failures.append((order, label))
                    continue
                man.setdefault("uploaded", {})[label] = rec["sha256"]
                print(f"  {order:2d} {slug:34s} {label:5s} + uploaded "
                      f"({local.stat().st_size // 1024} KB)")
                uploaded += 1

            urls[label] = sb.storage.from_(BUCKET).get_public_url(path).rstrip("?")

        if urls:
            man["cover_urls"] = urls
            write_manifest(order, man)

    print(f"\n{uploaded} uploaded · {skipped} unchanged · {len(failures)} failed")
    if failures:
        print("failed:", failures)
        sys.exit(1)
    print("Next: ./venv/bin/python scripts/gen_book_covers_swift.py")


if __name__ == "__main__":
    main()
