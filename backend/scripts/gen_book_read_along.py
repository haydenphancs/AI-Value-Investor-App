"""
Generate per-sentence read-along timings for the Book Library narration.

For each book that has generated audio (manifest in backend/data/book_audio/), compute the
start/end time (seconds, in the single book audio file) of every narrated SENTENCE, so the iOS
reading view can highlight the sentence currently being read.

Timing model: each core segment (bridge + body) is normalized to a uniform TARGET_WPM, so a word's
time offset within the segment is proportional to its word index. We anchor each core at its
MEASURED start (from the manifest) and distribute words linearly across the segment's measured
duration. The bridge (spoken, not shown in the reading view) is consumed first, then the body
blocks; only the BODY sentences are emitted (the bridge and action plan are not shown/highlighted).

Output: frontend/ios/ios/Models/BookReadAlong.swift
    ReadAlongBlock.byBook[curriculumOrder][coreNumber] = [ReadAlongBlock(isHeading, sentences)]
in narration order (a prefix of the core's rendered sections; the action plan is excluded).

Usage (from backend/):
    ./venv/bin/python scripts/gen_book_read_along.py
"""
import contextlib
import io
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
with contextlib.redirect_stdout(io.StringIO()):
    import gen_books_swift as g  # noqa: E402
import generate_book_audio as gba  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]                 # backend/
REPO = ROOT.parent
AUDIO_DIR = ROOT / "data/book_audio"
OUT = REPO / "frontend/ios/ios/Models/BookReadAlong.swift"
WPM = gba.TARGET_WPM
BREAK = gba.BREAK_SECONDS

# Split a paragraph into sentences: a sentence terminator (. ! ?) optionally followed by a closing
# quote, then whitespace, then the start of the next sentence. Good enough for narration prose.
_SENT = re.compile(r'(?<=[.!?])["”’]?\s+(?=["“‘A-Z0-9])')


def split_sentences(text: str) -> list[str]:
    text = text.strip()
    if not text:
        return []
    return [s.strip() for s in _SENT.split(text) if s.strip()]


def narrated_blocks(sections):
    """(is_heading, text) for each narrated block — every section except the action plan."""
    out = []
    for kind, text in sections:
        if kind == "heading" and re.search(r"action\s+plan", text, re.I):
            continue
        t = gba.strip_markup(text)
        if t:
            out.append((kind == "heading", t))
    return out


def sw(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')


def build_book(order: int, dirname: str, manifest: dict):
    cores = sorted([p for p in (g.BD / dirname).glob("*.txt")
                    if re.fullmatch(r"core\s+\d+", p.stem, re.I)], key=g.core_num)
    parsed = [(g.core_num(p), *g.parse_core(p)) for p in cores]
    bridges = gba.BRIDGES.get(order) or gba._generic_bridges(
        manifest["book_title"], manifest["author"], [t for _, t, *_ in parsed])

    starts = {c["number"]: c["start_seconds"] for c in manifest["cores"]}
    total = manifest["total_seconds"]
    nums = sorted(starts)

    core_blocks: dict[int, list] = {}
    for idx, (num, title, sections, *_rest) in enumerate(parsed):
        core_start = starts[num]
        nxt = nums[idx + 1] if idx + 1 < len(nums) else None
        seg_dur = (starts[nxt] - core_start - BREAK) if nxt is not None else (total - core_start)

        blocks = narrated_blocks(sections)
        bridge_words = len(bridges[idx].split())
        body_words = sum(len(t.split()) for _, t in blocks)
        seg_words = bridge_words + body_words
        sec_per_word = seg_dur / seg_words if seg_words else 0.0

        cursor = bridge_words                      # bridge is spoken first
        out_blocks = []
        for is_heading, text in blocks:
            sents = [text] if is_heading else split_sentences(text)
            timed = []
            for sent in sents:
                w = max(1, len(sent.split()))
                start = core_start + cursor * sec_per_word
                cursor += w
                end = core_start + cursor * sec_per_word
                timed.append((sent, round(start, 2), round(end, 2)))
            out_blocks.append((is_heading, timed))
        core_blocks[num] = out_blocks
    return core_blocks


def emit_book(order: int, core_blocks: dict) -> str:
    lines = [f"        {order}: ["]
    for num in sorted(core_blocks):
        lines.append(f"            {num}: [")
        for is_heading, timed in core_blocks[num]:
            lines.append(f"                ReadAlongBlock(isHeading: {str(is_heading).lower()}, sentences: [")
            for sent, start, end in timed:
                lines.append(f'                    ReadAlongSentence(text: "{sw(sent)}", start: {start}, end: {end}),')
            lines.append("                ]),")
        lines.append("            ],")
    lines.append("        ],")
    return "\n".join(lines)


def aligned_book(data: dict):
    """Convert a forced-alignment readalong.json into emit_book's shape, filling any null times
    (rare OOV-only sentences) by carrying the playhead forward from neighbours."""
    cores = {}
    for num_str, blocks in data["cores"].items():
        out = []
        for blk in blocks:
            timed = [(s["text"], s["start"], s["end"]) for s in blk["sentences"]]
            out.append((blk["isHeading"], timed))
        cores[int(num_str)] = out

    for num, core in cores.items():
        flat = [(bi, si) for bi, (_, ts) in enumerate(core) for si in range(len(ts))]
        last = 0.0
        for bi, si in flat:                                   # forward fill nulls
            text, s, e = core[bi][1][si]
            s = last if s is None else s
            e = (s if e is None else e)
            core[bi][1][si] = (text, round(s, 2), round(e, 2))
            last = core[bi][1][si][2]
    return cores


def main():
    manifests = sorted(AUDIO_DIR.glob("*.manifest.json"))
    if not manifests:
        raise SystemExit(f"No manifests in {AUDIO_DIR} (run generate_book_audio.py first).")

    blocks = []
    summary = []
    for mpath in manifests:
        m = json.loads(mpath.read_text())
        order = m["curriculum_order"]
        dirname = next(b[1] for b in g.BOOKS if b[0] == order)
        align_path = mpath.with_name(f"{order}_{m['slug']}.readalong.json")
        if align_path.exists():
            cb = aligned_book(json.loads(align_path.read_text()))
            src = "aligned"
        else:
            cb = build_book(order, dirname, m)
            src = "estimated"
        blocks.append((order, emit_book(order, cb)))
        nsent = sum(len(t) for core in cb.values() for _, t in core)
        summary.append((order, m["book_title"], len(cb), nsent, src))

    blocks.sort(key=lambda b: b[0])
    body = "\n".join(b[1] for b in blocks)
    swift = f"""//
//  BookReadAlong.swift
//  ios
//
//  Per-sentence read-along timings for the Book Library narration. For each book/core, the list of
//  narrated blocks (headings + paragraphs, in render order; the action plan is excluded) with each
//  sentence's start/end offset (seconds) within the single book audio file. Drives sentence
//  highlighting in BookCoreDetailView as the narration plays.
//
//  Generated from backend/data/book_audio/*.manifest.json + the authored core text by
//  backend/scripts/gen_book_read_along.py. Do not hand-edit — regenerate from source.
//

import Foundation

struct ReadAlongSentence {{
    let text: String
    let start: Double
    let end: Double
}}

struct ReadAlongBlock {{
    let isHeading: Bool
    let sentences: [ReadAlongSentence]
}}

extension ReadAlongBlock {{
    /// [curriculumOrder: [coreNumber: [blocks in narration order]]].
    static let byBook: [Int: [Int: [ReadAlongBlock]]] = [
{body}
    ]
}}
"""
    OUT.write_text(swift)
    print(f"wrote {OUT}")
    for order, title, ncores, nsent, src in summary:
        print(f"  order {order}: {title} — {ncores} cores, {nsent} sentences [{src}]")


if __name__ == "__main__":
    main()
