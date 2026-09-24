"""Weekly investor quote on the brand cover (TestFlight 1.0(6), home E1).

Tester: *"Collect a lot of famous catchy quotes from famous investors then weekly show in here.
We need 52 quotes (1 year) it will repeat weekly for every year."* Developer decisions
(2026-09-23): brand cover only (`CaydexSloganView`); 52 quotes drafted by us with a verified
PRIMARY source each, bundled in the app; author + citation line shown.

Four halves:

A. The bundled JSON's contract — 52 entries in week order, attribution hygiene (roster, ban list
   of famous misattributions, no recommendation/endorsement wording), and the typographic rules
   the renderer depends on (it adds the curly marks, so the text carries none).
B. `WeeklyQuotePicker` is EXECUTED — piped into `xcrun swift -` (no XCTest target exists;
   precedent `test_ios_lesson_narration_policy.py`). The named cases below are the SPEC, written
   out by hand; the sweep is checked against Python's independent ISO-8601 implementation.
C. Source scans (comment-stripped, brace-bounded) of the loader and the cover.
D. The legal docs list the cover as a surface that names a real investor.

Category 1 (pure) — no network, no Supabase. The Swift half skips when `xcrun` is unavailable.
"""
import json
import re
import shutil
import subprocess
import unicodedata
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parent
IOS = REPO / "frontend/ios/ios"
QUOTES_JSON = IOS / "Resources/InvestorQuotes/weekly_investor_quotes.json"
PICKER = IOS / "Core/Utilities/WeeklyQuotePicker.swift"
MODELS = IOS / "Models/InvestorQuoteModels.swift"
PATH_MODELS = IOS / "Models/InvestorPathModels.swift"
HEADER = IOS / "Views/Organisms/GlobalHeaderView.swift"
RESEARCH_HEADER = IOS / "Views/Organisms/ResearchHeader.swift"
LAUNCH_CHECKLIST = REPO / "documents/legal/LAUNCH_CHECKLIST.md"
STORE_LISTING = REPO / "documents/legal/app-store-listing.md"

WEEKS = 52
# Measured (CoreText, SF italic, lineSpacing 4): a 180-char quote at the 1.4x reading cap is
# 6-7 lines in the 338pt column and still leaves the 338pt art unshrunk on a 17 Pro and an SE.
MAX_CHARS = 180
MIN_CHARS = 25
MAX_WORDS = 40

ALLOWED_AUTHORS = {
    "Warren Buffett", "Benjamin Graham", "Charlie Munger", "Peter Lynch", "John Templeton",
    "Philip A. Fisher", "John C. Bogle", "Howard Marks", "Seth Klarman", "John Maynard Keynes",
    "Charles D. Ellis", "Thomas W. Phelps", "Terry Smith", "Burton G. Malkiel", "Morgan Housel",
    "Walter Schloss", "Joel Greenblatt", "Thomas Rowe Price Jr.", "Shelby Cullom Davis",
}
# Spelled out even though the allow-list already excludes them, so widening the allow-list can
# never quietly let these back in: fiction (Livermore via Lefèvre), non-investors that famous
# misattributions cling to, and polarising / actively-positioned public traders.
BANNED_AUTHORS = {
    "Albert Einstein", "Benjamin Franklin", "Mark Twain", "Winston Churchill", "Oscar Wilde",
    "Jesse Livermore", "Edwin Lefèvre", "George Soros", "Michael Burry", "Bill Ackman",
    "Carl Icahn", "Cathie Wood", "Jim Cramer", "Robert Kiyosaki", "Nathan Rothschild",
}
# Famous misattributions and unsourced lore (normalised substrings). Each is either provably
# someone else's wording or has no primary source.
BANNED_PHRASES = [
    "eighth wonder",                                   # not Einstein
    "compound interest is the most powerful",          # not Einstein
    "irrational longer than you can remain solvent",   # not Keynes
    "when the facts change",                           # not Keynes
    "roughly right than precisely wrong",              # Carveth Read
    "blood in the streets",                            # apocryphal Rothschild
    "investment in knowledge pays",                    # not Franklin
    "sells to optimists",                              # Jason Zweig's 2003 commentary
    "voting machine",                                  # Buffett's paraphrase of Graham
    "price is what you pay",                           # Buffett crediting Graham
    "impatient to the patient",                        # no primary source found
    "never lose money",                                # unsourced lore
    "risk comes from not knowing",                     # unsourced lore
    "rolls royce",                                     # unsourced lore
    "sitting in the shade",                            # unsourced lore
    "protection against ignorance",                    # unsourced lore
    "make money while you sleep",                      # unsourced lore
    "single income",                                   # unsourced lore
    "spend what is left after saving",                 # unsourced lore
    "four most dangerous words",                       # secondary sources only
    "this time is different",
    "this time its different",
    "buy the rumor",                                   # anonymous adage
    "pigs get slaughtered",                            # anonymous adage
    "best time to plant a tree",                       # misattributed proverb
    "price of everything",                             # Wilde paraphrase pinned on Fisher
]
# No company / ticker / asset call, no numbers dressed as promises, and nothing that, placed
# under the brand art, could read as the quoted person talking about the product.
RECOMMENDATION_OR_ENDORSEMENT = re.compile(
    r"%|\$\s?\d|\$[A-Z]{1,5}\b"
    r"|\b(coca[- ]?cola|coke|apple|geico|berkshire|bitcoin|crypto\w*|ethereum|amazon|google"
    r"|microsoft|tesla|nvidia)\b"
    r"|\b(caydex|cay|apps?|ai|artificial intelligence|algorithms?)\b"
    r"|guarantee|get rich|beat the market|double your money",
    re.I,
)
AGGREGATOR_OR_VAGUE_SOURCE = re.compile(
    r"attributed|unknown|\bvia\b|quoted in|internet|goodreads|brainyquote|wikiquote|azquotes",
    re.I,
)
REQUIRED_KEYS = {"week", "text", "author", "source", "year", "locator"}
ALLOWED_KEYS = REQUIRED_KEYS | {"url"}
QUOTE_MARKS = set('"“”\'‘’«»„')


# ---------------------------------------------------------------------------------------------
# helpers (same comment stripper / brace matcher as test_ios_lesson_narration_policy.py)
# ---------------------------------------------------------------------------------------------

def _strip_comments(src: str) -> str:
    """Drop `/* */` blocks, whole-line `//` comments and trailing `//` tails.

    Load-bearing: the explanatory comments next to the fix name every token these tests look
    for, so an un-stripped scan would pass on prose after the code was reverted.
    """
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    out = []
    for line in src.splitlines():
        if line.lstrip().startswith("//"):
            continue
        m = re.search(r"\s//", line)
        if m and line[: m.start()].count('"') % 2 == 0:
            line = line[: m.start()]
        out.append(line)
    return "\n".join(out)


def _code(path: Path) -> str:
    assert path.exists(), f"{path} is missing — every assertion below would be vacuous"
    return _strip_comments(path.read_text())


def _block_after(src: str, anchor: str) -> str:
    """The brace-balanced block that opens at the first `{` after `anchor`."""
    at = src.find(anchor)
    assert at >= 0, f"`{anchor}` not found"
    open_at = src.index("{", at)
    depth = 0
    for i in range(open_at, len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[open_at : i + 1]
    pytest.fail(f"unbalanced braces after `{anchor}`")


def _norm(text: str) -> str:
    """Casefold, straighten apostrophes, drop punctuation — for ban-list and dedup matching."""
    text = unicodedata.normalize("NFKC", text).casefold().replace("’", "'").replace("'", "")
    text = re.sub(r"[^\w\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


@pytest.fixture(scope="module")
def doc() -> dict:
    assert QUOTES_JSON.exists(), f"{QUOTES_JSON} is missing"
    return json.loads(QUOTES_JSON.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def quotes(doc) -> list:
    return doc["quotes"]


# ---------------------------------------------------------------------------------------------
# A. The bundled JSON
# ---------------------------------------------------------------------------------------------

def test_top_level_shape(doc):
    assert set(doc) == {"version", "note", "quotes"}
    assert doc["version"] == 1
    assert isinstance(doc["note"], str) and "PRIMARY" in doc["note"]


def test_exactly_52_and_it_matches_the_swift_constant(quotes):
    assert len(quotes) == WEEKS
    m = re.search(r"static let weeksPerCycle = (\d+)", _code(PICKER))
    assert m and int(m.group(1)) == len(quotes), "JSON count and WeeklyQuotePicker.weeksPerCycle disagree"


def test_weeks_are_1_to_52_in_order(quotes):
    assert [q["week"] for q in quotes] == list(range(1, WEEKS + 1))


def test_required_and_known_keys_only_with_types(quotes):
    for q in quotes:
        w = q.get("week")
        assert REQUIRED_KEYS <= set(q) <= ALLOWED_KEYS, (w, sorted(q))
        assert isinstance(q["week"], int) and not isinstance(q["week"], bool)
        assert isinstance(q["year"], int) and not isinstance(q["year"], bool), w
        for key in ("text", "author", "source", "locator"):
            assert isinstance(q[key], str), (w, key)
        if "url" in q:
            assert isinstance(q["url"], str), w


def test_fields_are_trimmed_and_single_line(quotes):
    for q in quotes:
        for key in ("text", "author", "source", "locator"):
            v = q[key]
            assert v and v == v.strip(), (q["week"], key, v)
            assert "\n" not in v and "\t" not in v and "  " not in v, (q["week"], key, v)


def test_texts_are_unique(quotes):
    seen = {}
    for q in quotes:
        key = _norm(q["text"])
        assert key not in seen, f"week {q['week']} repeats week {seen[key]}"
        seen[key] = q["week"]


def test_no_wrapping_or_straight_quote_marks(quotes):
    """The renderer adds “ ” — a mark in the data would double up; straight marks look cheap."""
    for q in quotes:
        t = q["text"]
        assert t[0] not in QUOTE_MARKS and t[-1] not in QUOTE_MARKS, (q["week"], t)
        for ch in ('"', "“", "”", "'"):
            assert ch not in t, (q["week"], repr(ch), t)


def test_no_markdown_specials(quotes):
    for q in quotes:
        for field in ("text", "author", "source"):
            assert not re.search(r"[*_`\[\]]", q[field]), (q["week"], field, q[field])


def test_text_ends_with_terminal_punctuation(quotes):
    for q in quotes:
        assert q["text"][-1] in ".!?…", (q["week"], q["text"])


def test_length_bounds(quotes):
    for q in quotes:
        t = q["text"]
        assert MIN_CHARS <= len(t) <= MAX_CHARS, (q["week"], len(t), t)
        assert len(t.split()) <= MAX_WORDS, (q["week"], t)


def test_source_is_a_citation_not_a_shrug(quotes):
    for q in quotes:
        s = q["source"]
        assert 4 <= len(s) <= 70, (q["week"], s)
        assert not re.search(r"\b(1[89]|20)\d\d\b", s), f"week {q['week']}: the year belongs in `year`: {s}"
        assert not AGGREGATOR_OR_VAGUE_SOURCE.search(s), (q["week"], s)
        assert len(q["locator"]) >= 3, q["week"]


def test_year_in_range(quotes):
    for q in quotes:
        assert 1900 <= q["year"] <= date.today().year, (q["week"], q["year"])


def test_one_book_one_year(quotes):
    """The cover shows "Source · year": "Beating the Street · 1994" one week and "· 1993" a
    month later read as two different books (review 2026-09-23). Serial sources — a letter
    or a memo series — legitimately span years."""
    serial = {"Berkshire Hathaway shareholder letter"}
    years = {}
    for q in quotes:
        if q["source"] in serial:
            continue
        years.setdefault((q["author"], q["source"]), set()).add(q["year"])
    mixed = {k: v for k, v in years.items() if len(v) > 1}
    assert not mixed, mixed


def test_url_is_https_when_present(quotes):
    for q in quotes:
        if "url" in q:
            assert q["url"].startswith("https://"), (q["week"], q["url"])


def test_author_roster(quotes):
    for q in quotes:
        assert q["author"] in ALLOWED_AUTHORS, (q["week"], q["author"])
        assert q["author"] not in BANNED_AUTHORS, (q["week"], q["author"])


def test_author_distribution(quotes):
    counts = {}
    for q in quotes:
        counts[q["author"]] = counts.get(q["author"], 0) + 1
    assert len(counts) >= 12, counts
    assert counts.get("Warren Buffett", 0) <= 8, counts
    for author, n in counts.items():
        if author != "Warren Buffett":
            assert n <= 6, (author, n)


def test_no_author_twice_in_a_row_including_the_year_wrap(quotes):
    authors = [q["author"] for q in quotes]
    for i in range(len(authors)):
        nxt = authors[(i + 1) % len(authors)]
        assert authors[i] != nxt, f"weeks {i + 1} and {(i + 1) % len(authors) + 1}: {nxt}"


def test_no_famous_misattribution(quotes):
    for q in quotes:
        t = _norm(q["text"])
        for phrase in BANNED_PHRASES:
            assert _norm(phrase) not in t, (q["week"], phrase)


def test_no_recommendation_or_endorsement_wording(quotes):
    for q in quotes:
        m = RECOMMENDATION_OR_ENDORSEMENT.search(q["text"])
        assert not m, f"week {q['week']}: {m.group(0)!r} in {q['text']!r}"


def test_resource_basename_is_unique_in_the_app_bundle():
    """Synchronized groups copy JSON FLAT to the bundle root, so a second file with this name
    anywhere under the app would silently replace it."""
    hits = [p for p in IOS.rglob("weekly_investor_quotes.json")]
    assert hits == [QUOTES_JSON], hits


def test_json_is_utf8_without_bom():
    raw = QUOTES_JSON.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf")
    raw.decode("utf-8")


# ---------------------------------------------------------------------------------------------
# B. The picker, executed
# ---------------------------------------------------------------------------------------------

def _sweep_rows() -> list[str]:
    """`tz|epoch|expected_slot` rows; expectations from Python's own ISO calendar."""
    rows = []
    ny = ZoneInfo("America/New_York")
    tokyo = ZoneInfo("Asia/Tokyo")
    d = date(2025, 12, 1)
    while d <= date(2038, 1, 10):
        noon = datetime(d.year, d.month, d.day, 12, tzinfo=ny)
        rows.append(f"America/New_York|{int(noon.timestamp())}|{min(d.isocalendar().week, 52) - 1}")
        if d.weekday() == 0:  # a Monday: the last second of Sunday and the first of Monday, in Tokyo
            sun = d - timedelta(days=1)
            last = datetime(sun.year, sun.month, sun.day, 23, 59, 59, tzinfo=tokyo)
            first = datetime(d.year, d.month, d.day, 0, 0, 0, tzinfo=tokyo)
            rows.append(f"Asia/Tokyo|{int(last.timestamp())}|{min(sun.isocalendar().week, 52) - 1}")
            rows.append(f"Asia/Tokyo|{int(first.timestamp())}|{min(d.isocalendar().week, 52) - 1}")
        d += timedelta(days=1)
    return rows


SWEEP = _sweep_rows()

HARNESS = r"""
var failures = 0
func check<T: Equatable>(_ name: String, _ got: T, _ expect: T) {
    if got == expect { print("ok|\(name)") }
    else { failures += 1; print("FAIL|\(name)|got=\(got)|expect=\(expect)") }
}
typealias P = WeeklyQuotePicker
let iso = ISO8601DateFormatter()
func at(_ s: String) -> Date { iso.date(from: s)! }
func tz(_ id: String) -> TimeZone { TimeZone(identifier: id)! }
let ny = tz("America/New_York")

// --- The calendar -------------------------------------------------------------------------
check("weeks_per_cycle", P.weeksPerCycle, 52)
check("first_weekday_monday", P.isoCalendar(timeZone: ny).firstWeekday, 2)
check("min_days_first_week_4", P.isoCalendar(timeZone: ny).minimumDaysInFirstWeek, 4)
check("zone_assigned", P.isoCalendar(timeZone: tz("Asia/Tokyo")).timeZone.identifier, "Asia/Tokyo")

// --- Slots: 1…52 straight through, 53 repeats 52, garbage clamps ---------------------------
check("slot_w1", P.slot(forISOWeek: 1), 0)
check("slot_w2", P.slot(forISOWeek: 2), 1)
check("slot_w52", P.slot(forISOWeek: 52), 51)
check("slot_w53_repeats_52", P.slot(forISOWeek: 53), 51)
check("slot_w0_clamps", P.slot(forISOWeek: 0), 0)
check("slot_negative_clamps", P.slot(forISOWeek: -5), 0)
check("slot_w99_clamps", P.slot(forISOWeek: 99), 51)

// --- Named dates across year boundaries (NY local noon) ------------------------------------
func idx(_ s: String, _ zone: TimeZone = ny) -> Int { P.index(for: at(s), count: 52, timeZone: zone)! }
check("d2025_12_28_w52", idx("2025-12-28T12:00:00-05:00"), 51)
check("d2025_12_29_mon_is_w1_of_2026", idx("2025-12-29T12:00:00-05:00"), 0)
check("d2026_01_01_w1", idx("2026-01-01T12:00:00-05:00"), 0)
check("d2026_01_05_w2", idx("2026-01-05T12:00:00-05:00"), 1)
check("d2026_09_23_w39_today", idx("2026-09-23T12:00:00-04:00"), 38)
check("d2026_12_21_w52", idx("2026-12-21T12:00:00-05:00"), 51)
check("w53_2026_12_28", idx("2026-12-28T12:00:00-05:00"), 51)
check("w53_2026_12_31", idx("2026-12-31T12:00:00-05:00"), 51)
check("w53_2027_01_01", idx("2027-01-01T12:00:00-05:00"), 51)
check("w53_2027_01_03", idx("2027-01-03T12:00:00-05:00"), 51)
check("d2027_01_04_mon_w1", idx("2027-01-04T12:00:00-05:00"), 0)
check("d2027_12_31_w52", idx("2027-12-31T12:00:00-05:00"), 51)
check("d2028_01_01_w52", idx("2028-01-01T12:00:00-05:00"), 51)
check("d2028_01_03_w1", idx("2028-01-03T12:00:00-05:00"), 0)
check("w53_2032_12_31", idx("2032-12-31T12:00:00-05:00"), 51)

// --- Same ISO week, different years → same quote -------------------------------------------
check("w10_2026", idx("2026-03-04T12:00:00-05:00"), 9)
check("w10_2027", idx("2027-03-10T12:00:00-05:00"), 9)
check("w10_2030", idx("2030-03-06T12:00:00-05:00"), 9)
check("w40_2026", idx("2026-09-28T12:00:00-04:00"), 39)
check("w40_2027", idx("2027-10-04T12:00:00-04:00"), 39)
check("w40_2030", idx("2030-09-30T12:00:00-04:00"), 39)

// --- Counts ---------------------------------------------------------------------------------
check("count0_nil", P.index(for: at("2026-09-23T12:00:00Z"), count: 0, timeZone: ny) == nil, true)
check("count_negative_nil", P.index(for: at("2026-09-23T12:00:00Z"), count: -1, timeZone: ny) == nil, true)
check("count1_w1", P.index(for: at("2026-01-01T12:00:00-05:00"), count: 1, timeZone: ny)!, 0)
check("count1_w53", P.index(for: at("2026-12-31T12:00:00-05:00"), count: 1, timeZone: ny)!, 0)
check("count7_w30_degrades_mod", P.index(for: at("2026-07-22T12:00:00-04:00"), count: 7, timeZone: ny)!, 1)
check("pick_empty_nil", P.pick([String](), on: at("2026-09-23T12:00:00Z"), timeZone: ny) == nil, true)
check("pick_w10", P.pick(Array(100..<152), on: at("2026-03-04T12:00:00-05:00"), timeZone: ny)!, 109)
check("pick_w53", P.pick(Array(100..<152), on: at("2026-12-31T12:00:00-05:00"), timeZone: ny)!, 151)

// --- Device-local Monday midnight ---------------------------------------------------------
check("tokyo_sun_235959_w53", idx("2027-01-03T23:59:59+09:00", tz("Asia/Tokyo")), 51)
check("tokyo_mon_000000_w1", idx("2027-01-04T00:00:00+09:00", tz("Asia/Tokyo")), 0)
check("same_instant_la_still_sunday", idx("2027-01-04T00:00:00+09:00", tz("America/Los_Angeles")), 51)
check("same_instant_ny_still_sunday", idx("2027-01-04T00:00:00+09:00", ny), 51)
check("la_dst_sunday_235959", idx("2026-03-08T23:59:59-07:00", tz("America/Los_Angeles")), 9)
check("la_dst_monday_000000", idx("2026-03-09T00:00:00-07:00", tz("America/Los_Angeles")), 10)
check("kiritimati_already_monday", idx("2027-01-03T12:00:00Z", tz("Pacific/Kiritimati")), 0)
check("pago_pago_still_sunday", idx("2027-01-03T12:00:00Z", tz("Pacific/Pago_Pago")), 51)

// --- The sweep, against Python's ISO calendar ----------------------------------------------
var checked = 0
var sweepFail = 0
for line in SWEEP.split(separator: "\n") {
    let parts = line.split(separator: "|")
    guard parts.count == 3, let epoch = Double(parts[1]), let expect = Int(parts[2]) else { continue }
    let got = P.index(for: Date(timeIntervalSince1970: epoch), count: 52, timeZone: tz(String(parts[0])))!
    checked += 1
    if got != expect {
        sweepFail += 1
        if sweepFail <= 5 { print("FAIL|sweep|\(line)|got=\(got)") }
    }
}
failures += sweepFail
print("SWEEP|checked=\(checked)|fail=\(sweepFail)")
print("DONE|\(failures)")
"""


def _run_swift() -> str:
    if not shutil.which("xcrun"):
        pytest.skip("xcrun unavailable — Swift cannot be executed on this host")
    sweep_literal = 'let SWEEP = #"""\n' + "\n".join(SWEEP) + '\n"""#\n'
    src = PICKER.read_text() + "\n" + sweep_literal + HARNESS
    try:
        proc = subprocess.run(["xcrun", "swift", "-"], input=src, text=True,
                              capture_output=True, timeout=300)
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        pytest.skip(f"could not run swift: {type(exc).__name__}: {exc}")
    if "DONE|" not in proc.stdout:
        pytest.fail(
            "the Swift harness did not run to completion — the picker probably stopped compiling "
            f"standalone (a SwiftUI import will do it).\nstdout:\n{proc.stdout[-3000:]}\n"
            f"stderr:\n{proc.stderr[-4000:]}")
    return proc.stdout


@pytest.fixture(scope="module")
def swift_output() -> str:
    return _run_swift()


def test_picker_is_foundation_only():
    raw = PICKER.read_text()
    src = _strip_comments(raw)
    assert "import Foundation" in src
    assert "import SwiftUI" not in src and "import UIKit" not in src, (
        "WeeklyQuotePicker must stay Foundation-only — otherwise it cannot run under "
        "`xcrun swift -` and every case below silently stops being tested")
    assert "import SwiftUI" in raw, "the header comment explaining the rule is gone (anti-vacuity)"


def test_picker_uses_an_explicit_iso_calendar():
    src = _code(PICKER)
    assert "Calendar(identifier: .iso8601)" in src
    assert "calendar.firstWeekday = 2" in src
    assert "calendar.minimumDaysInFirstWeek = 4" in src
    assert "calendar.timeZone = timeZone" in src, "the zone must be assigned, never inherited"
    for banned in ("Calendar.current", ".gregorian", "hashValue", "Calendar.autoupdatingCurrent"):
        assert banned not in src, banned


def test_every_picker_case(swift_output: str):
    failures = [l for l in swift_output.splitlines() if l.startswith("FAIL|")]
    assert not failures, "picker mismatches:\n  " + "\n  ".join(failures)


def test_the_sweep_covered_every_row(swift_output: str):
    m = re.search(r"SWEEP\|checked=(\d+)\|fail=(\d+)", swift_output)
    assert m, "the sweep did not report"
    assert int(m.group(1)) == len(SWEEP), (m.group(1), len(SWEEP))
    assert int(m.group(2)) == 0
    assert len(SWEEP) > 5000, "the sweep shrank — it must span several 53-week years"


def test_the_harness_actually_asserted_something(swift_output: str):
    oks = {l.split("|", 1)[1] for l in swift_output.splitlines() if l.startswith("ok|")}
    assert len(oks) >= 40, f"only {len(oks)} checks ran — the harness was truncated"
    for required in ("slot_w53_repeats_52", "w53_2027_01_01", "d2027_01_04_mon_w1",
                     "tokyo_mon_000000_w1", "la_dst_monday_000000", "count0_nil", "pick_w53"):
        assert required in oks, f"the {required} case did not run"


# ---------------------------------------------------------------------------------------------
# C. The loader and the cover
# ---------------------------------------------------------------------------------------------

def test_loader_fails_loudly_and_reads_the_right_resource():
    loader = _block_after(_code(MODELS), "enum BundledInvestorQuotes")
    assert 'forResource: "weekly_investor_quotes", withExtension: "json"' in loader
    assert 'category: "investor-quotes"' in loader
    assert loader.count("assertionFailure(") >= 3, "missing / undecodable / wrong count must all assert"
    assert "WeeklyQuotePicker.weeksPerCycle" in loader
    assert "WeeklyQuotePicker.pick(" in loader
    assert "try? JSONDecoder" not in loader and "try? Data" not in loader, "a decode failure must be logged, not swallowed"
    assert "print(" not in loader


def test_the_week_override_is_debug_only():
    loader = _block_after(_code(MODELS), "enum BundledInvestorQuotes")
    at = loader.find('"CAYDEX_QUOTE_WEEK"')
    assert at >= 0
    before = loader[:at]
    assert before.rfind("#if DEBUG") > before.rfind("#endif"), "CAYDEX_QUOTE_WEEK must sit inside #if DEBUG"
    assert loader.find("#endif", at) > at


def test_the_citation_separator_survives_a_title_that_ends_in_a_full_stop():
    """"Oaktree memo: You Can’t Predict. You Can Prepare., 2001" is what a comma produced on the
    simulator. The separator must read correctly after any terminal punctuation."""
    body = _block_after(_code(MODELS), "var citation: String?")
    assert 'year.map { "\\(source) · \\($0)" }' in body, body
    assert '"\\(source), \\($0)"' not in body


def test_investor_quote_is_declared_once_and_keeps_the_journey_quote():
    decls = [p for p in IOS.rglob("*.swift") if "struct InvestorQuote " in _strip_comments(p.read_text())
             or "struct InvestorQuote{" in _strip_comments(p.read_text())]
    assert decls == [MODELS], decls
    path_models = _code(PATH_MODELS)
    assert "static let buffettQuote = InvestorQuote(" in path_models
    # test_learn_titles_name_no_real_investor.py scans the FIRST `static let sampleData` block of
    # this file — it must still be the Journey sample data, not something the quote work added.
    first = path_models.find("static let sampleData")
    assert first > path_models.find("extension InvestorJourneyData {") >= 0


def test_cover_renders_the_weekly_quote():
    cover = _block_after(_code(HEADER), "struct CaydexSloganView: View")
    assert "@State private var quote: InvestorQuote?" in cover
    assert "BundledInvestorQuotes.quoteOfTheWeek()" in cover
    assert "if let quote" in cover and "BrandCoverQuote(quote: quote)" in cover
    assert ".layoutPriority(1)" in cover
    assert 'Image("CaydexSlogan")' in cover
    assert ".statusBarHidden(true)" in cover
    assert ".environment(\\.colorScheme, .dark)" in cover
    assert ".preferredColorScheme" not in cover


def test_quote_block_uses_fixed_light_ink_and_verbatim_text():
    block = _block_after(_code(HEADER), "private struct BrandCoverQuote: View")
    for needed in ("Text(verbatim:", "\\u{201C}", "\\u{201D}", "AppTypography.body", ".italic()",
                   ".fixedSize(horizontal: false, vertical: true)",
                   ".accessibilityElement(children: .ignore)", ".accessibilityLabel("):
        assert needed in block, needed
    assert block.count("AppColors.textOnAccent") >= 3
    for banned in ("textPrimary", "textSecondary", "textMuted", "primaryBlue", "bullish",
                   ".lineLimit(", ".minimumScaleFactor(", 'Text("'):
        assert banned not in block, f"{banned} in BrandCoverQuote — adaptive ink / truncation / Markdown"


def test_both_presenters_are_unchanged_and_the_journey_is_untouched():
    for path in (HEADER, RESEARCH_HEADER):
        assert "CaydexSloganView()" in _code(path), path
    for rel in ("ViewModels/InvestorPathViewModel.swift", "Views/Molecules/InvestorQuoteCard.swift",
                "Views/Screens/InvestorJourneyView.swift"):
        assert "BundledInvestorQuotes" not in _code(IOS / rel), rel
    assert "@Published var quote: InvestorQuote = .buffettQuote" in _code(IOS / "ViewModels/InvestorPathViewModel.swift")


def test_comment_stripping_is_not_vacuous():
    sample = '// BundledInvestorQuotes.quoteOfTheWeek()\nlet x = 1 // Text(verbatim: "a")\n'
    stripped = _strip_comments(sample)
    assert "quoteOfTheWeek" not in stripped and "verbatim" not in stripped
    assert "let x = 1" in stripped


# ---------------------------------------------------------------------------------------------
# D. The docs
# ---------------------------------------------------------------------------------------------

def test_legal_docs_list_the_cover_as_a_real_name_surface():
    """The store listing is tracked and must name the cover. LAUNCH_CHECKLIST.md is
    GITIGNORED (a local working file), so it is checked only where it exists — reading it
    unconditionally would fail every fresh checkout."""
    listing = STORE_LISTING.read_text()
    assert "Brand cover" in listing and "six in-app surfaces" in listing
    if LAUNCH_CHECKLIST.exists():
        checklist = LAUNCH_CHECKLIST.read_text()
        assert "CaydexSloganView" in checklist and "weekly_investor_quotes.json" in checklist
