"""
Bake per-book narration audio info into the iOS app as generated Swift.

Reads every manifest in backend/data/book_audio/<order>_<slug>.manifest.json (produced by
generate_book_audio.py) and emits frontend/ios/ios/Models/BookAudioContent.swift:

    BookAudioInfo.byOrder[curriculumOrder] = { totalSeconds, coreStartSeconds[coreNumber] }

The app uses this to show a per-core timestamp and to seek(to:) a core's start.

⚠️ NO URL IS EMITTED ANY MORE. This file used to bake the PUBLIC Storage URL of every book
into the binary, which meant anyone reading one network request could stream all 276 MB of
narration on any plan. Book audio is Pro/Max, so the URL is now a short-lived SIGNED url
fetched at play time from GET /api/v1/learn/books/audio (app/services/book_audio_service.py,
which reads the same manifests this script does).

The durations and offsets deliberately STAY compiled in: they drive free UI that advertises
the narration (the per-core "2:37" labels, the full-screen player's current-core readout,
auto-advance), and a locked caller must still see all of it.

Usage (from backend/):
    ./venv/bin/python scripts/gen_book_audio_swift.py
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]                 # backend/
REPO = ROOT.parent
AUDIO_DIR = ROOT / "data/book_audio"
OUT = REPO / "frontend/ios/ios/Models/BookAudioContent.swift"
BUCKET = "book-media"


def main():
    manifests = sorted(AUDIO_DIR.glob("*.manifest.json"))
    if not manifests:
        raise SystemExit(f"No manifests in {AUDIO_DIR} (run generate_book_audio.py first).")

    entries = []
    for mpath in manifests:
        m = json.loads(mpath.read_text())
        order = m["curriculum_order"]
        total = int(round(m["total_seconds"]))
        starts = ", ".join(
            f"{c['number']}: {int(round(c['start_seconds']))}"
            for c in sorted(m["cores"], key=lambda c: c["number"])
        )
        entry = (
            f"        {order}: BookAudioInfo(\n"
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
//  ⚠️ NO AUDIO URL HERE. It used to carry the PUBLIC Storage URL of every book, so one
//  network request handed anyone all 276 MB of narration on any plan. Narration is Pro/Max:
//  the URL is now a short-lived SIGNED url fetched at play time by `BookAudioURLStore`
//  from GET /api/v1/learn/books/audio.
//
//  Durations and offsets stay here on purpose — they drive FREE UI that advertises the
//  narration (per-core timestamps, the player's current-core readout, auto-advance), and a
//  locked user must still see all of it.
//

import Foundation

struct BookAudioInfo {{
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
