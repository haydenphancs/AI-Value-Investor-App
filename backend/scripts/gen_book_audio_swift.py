"""
Bake per-book narration audio info into the iOS app as generated Swift.

Reads every manifest in backend/data/book_audio/<order>_<slug>.manifest.json (produced by
generate_book_audio.py; the public URL is filled in by seed_book_audio.py) and emits
frontend/ios/ios/Models/BookAudioContent.swift:

    BookAudioInfo.byOrder[curriculumOrder] = { audioUrl, totalSeconds, coreStartSeconds[coreNumber] }

The app uses this to (a) stream ONE file per book and (b) seek to a core's start. The audio URL is
deterministic — {SUPABASE_URL}/storage/v1/object/public/book-media/audio/<file> — so this can run
BEFORE seeding; the file just won't resolve until seed_book_audio.py uploads it.

Usage (from backend/):
    ./venv/bin/python scripts/gen_book_audio_swift.py
"""
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]                 # backend/
REPO = ROOT.parent
AUDIO_DIR = ROOT / "data/book_audio"
OUT = REPO / "frontend/ios/ios/Models/BookAudioContent.swift"
BUCKET = "book-media"


def supabase_url() -> str:
    url = os.environ.get("SUPABASE_URL")
    env = ROOT / ".env"
    if not url and env.exists():
        for line in env.read_text().splitlines():
            if line.startswith("SUPABASE_URL="):
                url = line.split("=", 1)[1].strip().strip('"').strip("'")
                break
    assert url, "SUPABASE_URL not found in env or backend/.env"
    return url.rstrip("/")


def public_url(base: str, fname: str) -> str:
    return f"{base}/storage/v1/object/public/{BUCKET}/audio/{fname}"


def main():
    base = supabase_url()
    manifests = sorted(AUDIO_DIR.glob("*.manifest.json"))
    if not manifests:
        raise SystemExit(f"No manifests in {AUDIO_DIR} (run generate_book_audio.py first).")

    entries = []
    for mpath in manifests:
        m = json.loads(mpath.read_text())
        order = m["curriculum_order"]
        # Supabase get_public_url appends a bare trailing "?" — strip it so the baked URL is clean
        # (both forms resolve, but the clean one is tidier and matches the deterministic fallback).
        url = (m.get("audio_url") or public_url(base, m["audio_file"])).rstrip("?")
        total = int(round(m["total_seconds"]))
        starts = ", ".join(
            f"{c['number']}: {int(round(c['start_seconds']))}"
            for c in sorted(m["cores"], key=lambda c: c["number"])
        )
        entry = (
            f"        {order}: BookAudioInfo(\n"
            f'            audioUrl: "{url}",\n'
            f"            totalSeconds: {total},\n"
            f"            coreStartSeconds: [{starts}]\n"
            f"        ),"
        )
        entries.append((order, entry, m["book_title"], total, len(m["cores"])))

    entries.sort(key=lambda e: e[0])
    body = "\n".join(e[1] for e in entries)

    swift = f"""//
//  BookAudioContent.swift
//  ios
//
//  Per-book narration audio for the Book Library: ONE streamed .m4a per book (Supabase
//  '{BUCKET}' bucket) plus the start offset (seconds) of each core within that single file,
//  so the player can show a per-core timestamp and seek(to:) a core's start.
//
//  Generated from backend/data/book_audio/*.manifest.json by
//  backend/scripts/gen_book_audio_swift.py. Do not hand-edit — regenerate from the manifests.
//

import Foundation

struct BookAudioInfo {{
    /// Public Supabase Storage URL of the single book narration file (streamed by AVPlayer).
    let audioUrl: String
    /// Real measured length of the whole book narration, in seconds.
    let totalSeconds: Int
    /// Core number -> start offset (seconds) within the single book audio file.
    let coreStartSeconds: [Int: Int]
}}

extension BookAudioInfo {{
    /// Keyed by LibraryBook.curriculumOrder. Only books with generated narration appear here;
    /// a missing order means "no narration yet" (the app shows no Listen audio for that book).
    static let byOrder: [Int: BookAudioInfo] = [
{body}
    ]
}}
"""
    OUT.write_text(swift)
    print(f"wrote {OUT}")
    for order, _, title, total, ncores in entries:
        print(f"  order {order}: {title} — {total}s, {ncores} cores")


if __name__ == "__main__":
    main()
