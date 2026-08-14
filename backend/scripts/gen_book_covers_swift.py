"""
Stage 4 of 4 — bake the cover URLs into the iOS app as generated Swift.

Reads backend/data/book_covers/*.manifest.json and emits
frontend/ios/ios/Models/BookCoverContent.swift.

The URL is a pure function of (bucket, order, slug), so it is DERIVED here rather than
depended on from a seed run — that lets the iOS side be wired before the bucket exists.
When a manifest does carry a seeded `cover_urls`, this asserts the two agree, so a
bucket rename or a path drift fails here instead of shipping a 404 to users.

Usage (from backend/):
    ./venv/bin/python scripts/gen_book_covers_swift.py
"""
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]          # backend/
REPO = ROOT.parent
DATA = ROOT / "data/book_covers"
OUT = REPO / "frontend/ios/ios/Models/BookCoverContent.swift"
LEARN_MODELS = REPO / "frontend/ios/ios/Models/LearnModels.swift"
BUCKET = "book-covers"
PREFIX = "covers"


def supabase_url() -> str:
    url = os.environ.get("SUPABASE_URL")
    env = ROOT / ".env"
    if not url and env.exists():
        for line in env.read_text().splitlines():
            if line.startswith("SUPABASE_URL="):
                url = line.split("=", 1)[1].strip().strip('"').strip("'")
                break
    if not url:
        raise SystemExit("SUPABASE_URL not found in env or backend/.env")
    return url.rstrip("/")


def public_url(base: str, filename: str) -> str:
    return f"{base}/storage/v1/object/public/{BUCKET}/{PREFIX}/{filename}"


def normalized_key(title: str) -> str:
    """Must match BookCoverArt.normalizedKey in Swift exactly."""
    return "".join(ch for ch in title.lower() if ch.isalnum())


def swift_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')


def main():
    base = supabase_url()
    manifests = sorted(DATA.glob("*.manifest.json"),
                       key=lambda p: int(p.name.split("_", 1)[0]))
    if not manifests:
        raise SystemExit(f"No manifests in {DATA} — run compose_book_cover.py first.")

    entries = []
    for mp in manifests:
        m = json.loads(mp.read_text())
        masters = m.get("masters")
        if not masters:
            print(f"  skip {mp.name}: not composed yet")
            continue
        order, title = m["curriculum_order"], m["title"]
        thumb = public_url(base, masters["thumb"]["file"])
        hero = public_url(base, masters["hero"]["file"])

        seeded = m.get("cover_urls")
        if seeded:
            for label, derived in (("thumb", thumb), ("hero", hero)):
                if seeded.get(label) and seeded[label] != derived:
                    raise SystemExit(
                        f"book {order}: seeded {label} URL disagrees with the derived one.\n"
                        f"  seeded  {seeded[label]}\n  derived {derived}\n"
                        f"A bucket rename or path drift would ship a 404 — fix before generating.")

        entries.append(dict(order=order, title=title, key=normalized_key(title),
                            thumb=thumb, hero=hero))

    keys = [e["key"] for e in entries]
    if len(set(keys)) != len(keys):
        dupes = {k for k in keys if keys.count(k) > 1}
        raise SystemExit(f"normalizedKey collision across books: {dupes}")

    # Cross-check against the app's own catalogue so a title edit cannot silently
    # blank a cover.
    if LEARN_MODELS.exists():
        src = LEARN_MODELS.read_text()
        block = src[src.find("static let sampleData: [LibraryBook]"):]
        # Only the title that OPENS a LibraryBook(...) literal. A bare `title:` search
        # also matches keyHighlights / coreChapters / discussions and reported 25
        # phantom "books with no cover" ("Compounding", "15 Points", ...).
        app_titles = re.findall(r'LibraryBook\(\s*title:\s*"([^"]+)"', block)
        have = set(keys)
        missing = [t for t in app_titles if normalized_key(t) not in have]
        if missing:
            print(f"  ⚠️  {len(missing)} LibraryBook(s) have no cover: {missing}")
        elif app_titles:
            print(f"  ✓ all {len(app_titles)} LibraryBook titles resolve to a cover")

    rows_title = "\n".join(
        f'        "{e["key"]}": BookCoverArt(   // {e["order"]}. {swift_escape(e["title"])}\n'
        f'            thumbURL: "{e["thumb"]}",\n'
        f'            heroURL: "{e["hero"]}"\n'
        f'        ),' for e in entries)
    rows_order = "\n".join(
        f'        {e["order"]}: byTitle["{e["key"]}"]!,' for e in entries)

    swift = f'''//
//  BookCoverContent.swift
//  ios
//
//  Generated cover art for the Book Library — one composited JPEG per book per size,
//  served from the PUBLIC Supabase '{BUCKET}' bucket (migration 133).
//
//  Generated from backend/data/book_covers/*.manifest.json by
//  backend/scripts/gen_book_covers_swift.py. Do not hand-edit — regenerate.
//
//  ⚠️ THE URL IS BAKED IN ON PURPOSE — the opposite of BookAudioContent.swift, and NOT
//  a regression of that file's signed-URL fix. Narration is Pro/Max, so its URL must be
//  minted per request. A COVER IS FREE: a locked, signed-out user must see it. Covers
//  therefore live in their own PUBLIC bucket, deliberately excluded from migration 128's
//  flip of book-media / journey-media / money-moves-media to private. Do not move covers
//  into book-media and do not add book-covers to 128 — either one blanks every cover in
//  the app the day 128 is applied.
//
//  Keyed by NORMALIZED TITLE, not curriculumOrder: LibraryBook has curriculumOrder but
//  EducationBook and SearchBookItem do not. Title is the only field all three share, and
//  it is already this app's cross-model key (LearnView matches EducationBook to
//  LibraryBook by title; BookmarkStore is title-keyed).
//
//  TWO sizes, and they are not interchangeable. The type is re-set optically at each
//  size rather than scaled, because a 2:1 downscale of composited type aliases and
//  shimmers during scroll. Always resolve via `url(forWidth:)`.
//

import CoreGraphics
import Foundation

struct BookCoverArt {{
    /// 240x330 — the 80x110pt cards (LibraryBookCard, EducationBookCard, SearchBookCard).
    let thumbURL: String
    /// 480x660 — BookDetailView's 160x220pt hero.
    let heroURL: String

    /// Picks the master whose type was set for this slot. Never scales one into the
    /// other's job. 120pt sits between the two call sizes (80 and 160).
    func url(forWidth width: CGFloat) -> String {{ width > 120 ? heroURL : thumbURL }}
}}

extension BookCoverArt {{
    /// Title -> cover, keyed by `normalizedKey(_:)`. A missing key means "no cover yet"
    /// and callers fall back to the gradient. Never force-unwrap this.
    static let byTitle: [String: BookCoverArt] = [
{rows_title}
    ]

    /// Convenience for `LibraryBook`, which does carry a curriculum order.
    static let byOrder: [Int: BookCoverArt] = [
{rows_order}
    ]

    /// Lowercased, every non-alphanumeric dropped. A capitalisation or punctuation edit
    /// in LearnModels.swift therefore cannot silently blank a cover. The generator emits
    /// keys through this exact transform.
    static func normalizedKey(_ title: String) -> String {{
        title.lowercased().unicodeScalars
            .filter {{ CharacterSet.alphanumerics.contains($0) }}
            .reduce(into: "") {{ $0.unicodeScalars.append($1) }}
    }}

    static func forTitle(_ title: String) -> BookCoverArt? {{ byTitle[normalizedKey(title)] }}
    static func forOrder(_ order: Int) -> BookCoverArt? {{ byOrder[order] }}
}}
'''
    OUT.write_text(swift)
    print(f"wrote {OUT.relative_to(REPO)}  ({len(entries)} books)")
    for e in entries:
        print(f"  {e['order']:2d} {e['title'][:44]:44s} -> {e['key']}")


if __name__ == "__main__":
    main()
