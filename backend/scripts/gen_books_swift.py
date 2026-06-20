"""
Import authored Book-Core content (documents/books/<Book>/core N.txt) into Swift
CoreChapterContent + BookCoreChapter data for the iOS Book Library. Body text is taken
VERBATIM from the source (line-oriented deterministic parse) — no paraphrasing.

Output: frontend/ios/ios/Models/BooksContent.swift  (keyed by curriculumOrder)
Regenerate from source; do not hand-edit. Rich Dad Poor Dad (order 1) lives separately
in RichDadPoorDadContent.swift.
"""
import os
import re
from pathlib import Path

# Repo root: env override (used on a RunPod/Linux box) else derive from this file's
# location (backend/scripts/ -> repo root). parents[2] == the old hardcoded Mac path,
# so the Mac workflow is byte-for-byte unchanged.
ROOT = Path(os.environ.get("AI_INVESTOR_ROOT") or Path(__file__).resolve().parents[2])
BD = ROOT / "documents/books"
OUT = ROOT / "frontend/ios/ios/Models/BooksContent.swift"
WPM = 150

# (curriculumOrder, source-dir, bookTitle, bookAuthor)
BOOKS = [
    (1,  "Rich Dad Poor Dad ",                            "Rich Dad Poor Dad",                           "Robert T. Kiyosaki"),
    (2,  "The Intelligent Investor",                      "The Intelligent Investor",                    "Benjamin Graham"),
    (3,  "The Psychology of Money ",                      "The Psychology of Money",                     "Morgan Housel"),
    (4,  "One Up On Wall Street",                         "One Up On Wall Street",                        "Peter Lynch"),
    (5,  "Common Stocks and Uncommon Profits",           "Common Stocks and Uncommon Profits",           "Philip Fisher"),
    (6,  "The Little Book of Common Sense Investing ",   "The Little Book of Common Sense Investing",    "John C. Bogle"),
    (7,  "A Random Walk Down Wall Street ",              "A Random Walk Down Wall Street",               "Burton G. Malkiel"),
    (8,  "The Essays of Warren Buffett",                 "The Essays of Warren Buffett",                 "Warren Buffett"),
    (9,  "The Little Book that Still Beats the Market",  "The Little Book that Still Beats the Market",   "Joel Greenblatt"),
    (10, "The Most Important Thing ",                    "The Most Important Thing",                     "Howard Marks"),
]

LABELS = re.compile(r"^(The Hook|The Author[’']s Solution|The ['’]Wiser['’] Analysis|The Friction)\s*:\s*(.+)$")
ACTION_RE = re.compile(r"action plan|action steps|your move|the playbook|the protocol|do this now", re.I)


def sw(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')


def is_short_heading(line: str) -> bool:
    if not line or line[-1] in ".!?":
        return False
    # A line ending in ":" is a heading only if it's a short label/name (e.g. "Final Words:",
    # "Action Plan:"). A long colon-ending line is an intro SENTENCE, not a title -> paragraph.
    if line.endswith(":"):
        return len(line.split()) <= 8
    return len(line.split()) <= 12


def clean_heading(text: str) -> str:
    """Tidy a heading: normalize action-plan headers and drop a trailing label colon."""
    t = text.strip()
    if re.fullmatch(r"(the\s+)?action\s+plan\s*:?", t, re.I):
        return "The Action Plan"
    if t.endswith(":"):
        t = t[:-1].rstrip()
    return t


def core_num(p: Path) -> int:
    m = re.search(r"core\s*(\d+)", p.name, re.I)
    return int(m.group(1)) if m else 0


def parse_core(path: Path):
    lines = [l.strip() for l in path.read_text().splitlines() if l.strip()]
    title = None
    i = 0
    if lines and re.match(r"^Core\s*\d+\s*:", lines[0], re.I):
        title = re.sub(r"^Core\s*\d+\s*:\s*", "", lines[0]).strip()
        i = 1
    elif lines:
        title = lines[0]      # no "Core N:" line -> first line serves as the title
        i = 1
    sections = []             # (kind, text)
    action = []               # (title, description)
    in_action = False
    words = sum(len(l.split()) for l in lines)
    for line in lines[i:]:
        m = LABELS.match(line)
        if m and len(line.split()) > 12:          # inline "Label: subtitle body" -> heading + paragraph
            sections.append(("heading", m.group(1).strip()))
            sections.append(("paragraph", m.group(2).strip()))
            in_action = False
            continue
        if is_short_heading(line):
            sections.append(("heading", clean_heading(line)))
            in_action = bool(ACTION_RE.search(line))
            continue
        if in_action and ": " in line:            # action step "Name: description"
            name, _, desc = line.partition(": ")
            if 0 < len(name.split()) <= 8:
                action.append((name.strip(), desc.strip()))
                continue
        sections.append(("paragraph", line))
    dur = round(words / WPM * 60)
    first_para = next((t for k, t in sections if k == "paragraph"), title or "")
    desc = first_para[:130].rstrip()
    if len(first_para) > 130:
        desc += "…"
    return title, sections, action, dur, words, desc


def emit_core(num, title, sections, action, dur, book_title, book_author):
    o = [f"            {num}: CoreChapterContent("]
    o.append(f"                chapterNumber: {num},")
    o.append(f'                chapterTitle: "{sw(title)}",')
    o.append(f'                bookTitle: "{sw(book_title)}",')
    o.append(f'                bookAuthor: "{sw(book_author)}",')
    o.append("                sections: [")
    for kind, text in sections:
        o.append("                    CoreChapterSection(")
        o.append(f"                        type: .{kind},")
        o.append("                        title: nil,")
        o.append(f'                        content: .text("{sw(text)}")')
        o.append("                    ),")
    if action:
        o.append("                    CoreChapterSection(")
        o.append("                        type: .actionPlan,")
        o.append("                        title: nil,")
        o.append("                        content: .actionPlan([")
        for name, desc in action:
            o.append("                            ActionStep(")
            o.append(f'                                title: "{sw(name)}",')
            o.append(f'                                description: "{sw(desc)}",')
            o.append("                                isCompleted: false")
            o.append("                            ),")
        o.append("                        ])")
        o.append("                    )")
    o.append("                ],")
    o.append(f"                audioDurationSeconds: {dur},")
    o.append("                currentProgress: 0.0")
    o.append("            ),")
    return "\n".join(o)


content_blocks = []
list_blocks = []
read_min = []
summary = []
for order, dirname, btitle, bauthor in BOOKS:
    d = BD / dirname
    # Only ingest strictly-named "core <N>.txt" files. This excludes the course-index
    # "cores.txt" (which has no number -> would land as a bogus "Core 0") and accidental
    # Finder duplicates like "core 1 copy 5.txt" (which all collapse to the same number).
    cores = sorted([p for p in d.glob("*.txt") if re.fullmatch(r"core\s+\d+", p.stem, re.I)], key=core_num)
    # Fail fast on duplicate core numbers: a Swift dictionary literal with duplicate keys
    # traps at runtime ("Dictionary literal contains duplicate keys") the first time
    # booksByOrder is accessed — i.e. when the user opens any core. Catch it at gen time.
    _nums = [core_num(p) for p in cores]
    _dupes = sorted({n for n in _nums if _nums.count(n) > 1})
    if _dupes:
        raise SystemExit(
            f"[{dirname}] duplicate core numbers {_dupes} from files "
            f"{[p.name for p in cores]} — fix the source filenames before regenerating"
        )
    core_entries = []
    list_entries = []
    total_words = 0
    total_dur = 0
    for p in cores:
        n = core_num(p)
        title, sections, action, dur, words, desc = parse_core(p)
        total_words += words
        total_dur += dur
        core_entries.append(emit_core(n, title, sections, action, dur, btitle, bauthor))
        list_entries.append(
            f'            BookCoreChapter(number: {n}, title: "{sw(title)}", description: "{sw(desc)}"),'
        )
    content_blocks.append(f"        {order}: [\n" + "\n".join(core_entries) + "\n        ],")
    list_blocks.append(f"        {order}: [\n" + "\n".join(list_entries) + "\n        ],")
    mins = round(total_words / WPM)
    read_min.append(f"        {order}: {mins},  // {btitle}")
    summary.append((order, btitle, len(cores), total_words, mins, total_dur))

header = """//
//  BooksContent.swift
//  ios
//
//  Real Book-Core detail content + core lists for the Book Library, imported VERBATIM from
//  documents/books/<Book>/core N.txt via backend/scripts/gen_books_swift.py. Keyed by
//  LibraryBook.curriculumOrder. Covers the whole Book Library (orders 1...10).
//  Do not hand-edit — regenerate from source.
//

import Foundation

extension CoreChapterContent {
    /// Core detail content per book, keyed by curriculumOrder then core number.
    static let booksByOrder: [Int: [Int: CoreChapterContent]] = [
"""
mid1 = "\n".join(content_blocks) + "\n    ]\n}\n\n"
mid2 = ("extension BookCoreChapter {\n"
        "    /// The real Core list (timeline rows) per book, keyed by curriculumOrder.\n"
        "    static let listsByOrder: [Int: [BookCoreChapter]] = [\n"
        + "\n".join(list_blocks) + "\n    ]\n}\n\n")
mid3 = ("extension LibraryBook {\n"
        "    /// Total read time (minutes) per book, computed from the authored core content.\n"
        "    static let readMinutesByOrder: [Int: Int] = [\n"
        + "\n".join(read_min) + "\n    ]\n}\n")

OUT.write_text(header + mid1 + mid2 + mid3)

print(f"{'ord':>3} {'cores':>5} {'words':>6} {'min':>4}  book")
tw = tc = 0
for order, btitle, ncores, words, mins, dur in summary:
    print(f"{order:>3} {ncores:>5} {words:>6} {mins:>4}  {btitle}")
    tw += words; tc += ncores
print(f"\nTOTAL: {tc} cores, {tw} words across {len(BOOKS)} books")
print("wrote:", OUT)
