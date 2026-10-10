"""13F whale dates name their QUARTER on the Whale Profile and the Tracking timeline (2026-10-09).

A 13F row's `date` (`whale_trades.date` / `whale_trade_groups.date`) is the QUARTER END its
filing reports holdings for — FMP's institutional-ownership `date`, with the hydrators'
`{y}-{q*3:02d}-30` fallback — never the day the 13F was filed. Two surfaces rendered it as
recency: the Whale Profile's trade-group card ("Today" / "Yesterday" / "N days ago" up to 14
days) and the Tracking tab's whale timeline ("N day(s) ago" up to 7), so a fund that filed
five days after the quarter closed read as having traded five days ago. Both now read
"Q2 2026 13F" through `ThirteenFQuarter.label` — the Home signals drill-down's wording
(`SignalDetailFormat.whaleDate`, test_ios_signal_whale_quarter_label.py). Congressional labels
("Traded … · Disclosed …", "Disclosed …") are unchanged.

Two halves:

* EXECUTED. `ThirteenFQuarter.swift` is Foundation-only, so it is piped into `xcrun swift -`
  and run across time zones (precedent: test_money_moves_date_label.py). Skips when xcrun is
  unavailable (CI containers, Linux) rather than failing.
* SOURCE SCAN (testing.md §3). Comments are stripped first — each fix's comment names the
  tokens asserted on — and every check is brace-bound to its declaration, because "Today"
  and "Disclosed" legitimately live elsewhere in the same files. Mutation-tested by hand:
  each assertion was watched to go red against a reverted or broken source.

Category 1 (pure) — no network, no Supabase.
"""

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_IOS = _REPO / "frontend/ios/ios"
_HELPER = _IOS / "Core/Utilities/ThirteenFQuarter.swift"
_PROFILE_MODELS = _IOS / "Models/WhaleProfileModels.swift"
_TRACKING_MODELS = _IOS / "Models/TrackingModels.swift"
_PROFILE_VIEW = _IOS / "Views/Screens/WhaleProfileView.swift"
_TRACKING_VIEW = _IOS / "Views/Screens/TrackingView.swift"
_TRACKING_VM = _IOS / "ViewModels/TrackingViewModel.swift"
_SIGNAL_MODELS = _IOS / "Models/SignalDetailModels.swift"

_QUARTER_CALL = "ThirteenFQuarter.label(for: date)"
_RELATIVE = ('"Today"', '"Yesterday"', ' ago"')


# ── extraction ───────────────────────────────────────────────────────────────


def _strip_comments(src: str) -> str:
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    out = []
    for line in src.splitlines():
        if line.strip().startswith("//"):
            continue
        out.append(re.sub(r"\s//.*$", "", line))
    return "\n".join(out)


def _code(path: Path) -> str:
    return _strip_comments(path.read_text(encoding="utf-8"))


def _braced(code: str, decl: str, opener: str = "{", closer: str = "}") -> str:
    """The body of the FIRST match of the regex `decl`, bracket-matched — or a loud failure.

    Bracket-matched rather than "scan to the next landmark": deleting the thing under test
    makes a landmark-bounded window grow until it finds an unrelated match.
    """
    m = re.search(decl, code)
    if not m:
        pytest.fail(f"{decl!r} not found — this scan has drifted")
    start = code.index(opener, m.start())
    depth = 0
    for i in range(start, len(code)):
        if code[i] == opener:
            depth += 1
        elif code[i] == closer:
            depth -= 1
            if depth == 0:
                return code[start:i + 1]
    pytest.fail(f"unbalanced {opener}{closer} after {decl!r}")


def _template(literal_body: str) -> str:
    """`Q\\(quarter) \\(year) 13F` → `Q\\(_) \\(_) 13F`: the wording, not the variable names."""
    return re.sub(r"\\\(\w+\)", r"\\(_)", literal_body)


# ── the helper itself ────────────────────────────────────────────────────────


def test_the_helper_is_foundation_only_gregorian_and_month_based():
    raw = _HELPER.read_text(encoding="utf-8")
    code = _strip_comments(raw)
    assert "import SwiftUI" not in code, (
        "ThirteenFQuarter imports SwiftUI — it can no longer run under `xcrun swift -` and "
        "every executed case below silently stops being tested")
    assert "import Foundation" in code
    # Anti-vacuity: the header DOES name `import SwiftUI` while explaining the rule, so a
    # broken strip would fail the first assertion on correct code.
    assert "import SwiftUI" in raw
    # Nonisolated: the target defaults to MainActor, and the Home signals DTO mapping that
    # could share this helper runs nonisolated.
    assert re.search(r"\bnonisolated\s+enum\s+ThirteenFQuarter\b", code)

    body = _braced(code, r"static func label\(for date: Date, timeZone: TimeZone = \.current\)")
    # A 13F quarter is a CALENDAR quarter: a Buddhist-calendar device would print 2569.
    assert "Calendar(identifier: .gregorian)" in body
    assert "calendar.timeZone = timeZone" in body
    for ambient in ("Calendar.current", "autoupdatingCurrent", "Locale"):
        assert ambient not in body, f"the label reads the device's {ambient}"
    # The quarter comes from the MONTH (1-12 → Q1-Q4), never the day — so the hydrators'
    # 03-30 / 12-30 fallback lands in Q1 / Q4.
    assert re.search(r"\(\s*month\s*-\s*1\s*\)\s*/\s*3\s*\+\s*1", body)
    assert ".day" not in body
    assert re.search(r'"Q\\\(\w+\) \\\(\w+\) 13F"', body), "expected the 'Q2 2026 13F' label"


def test_the_wording_matches_the_home_signals_drill_down():
    """One 13F row, one label, wherever it shows. The Home drill-down's `whaleDate` takes an
    ISO string (its own DTO), this helper a parsed Date — so the WORDING is compared."""
    ours = re.search(r'"(Q\\\(\w+\) \\\(\w+\) 13F)"', _braced(_code(_HELPER), r"static func label\(for date"))
    theirs = re.search(
        r'"(Q\\\(\w+\) \\\(\w+\) 13F)"',
        _braced(_code(_SIGNAL_MODELS), r"static func whaleDate\("),
    )
    assert ours and theirs, "a quarter label literal is gone — this comparison has drifted"
    assert _template(ours.group(1)) == _template(theirs.group(1))


# ── executed ─────────────────────────────────────────────────────────────────

# `parsed` mirrors `DateParser` (Models/WhaleDTOs.swift): "yyyy-MM-dd", en_US_POSIX, and NO
# explicit zone there — so the device's zone, which `zone` stands in for here.
_HARNESS = r"""
var failures = 0
func check(_ name: String, _ got: String, _ expect: String) {
    if got == expect { print("ok|\(name)") }
    else { failures += 1; print("FAIL|\(name)|got=\(got)|expect=\(expect)") }
}
func zone(_ id: String) -> TimeZone { TimeZone(identifier: id)! }
func parsed(_ day: String, in zone: TimeZone) -> Date {
    let f = DateFormatter()
    f.dateFormat = "yyyy-MM-dd"
    f.locale = Locale(identifier: "en_US_POSIX")
    f.timeZone = zone
    return f.date(from: day)!
}

// -11 h and +14 h are the extremes a phone can sit in; the rest are where users are.
let zones = ["UTC", "America/New_York", "America/Los_Angeles", "Pacific/Pago_Pago",
             "Pacific/Kiritimati", "Asia/Tokyo", "Asia/Bangkok", "Australia/Sydney"]

// Real quarter ends AND the hydrators' `{y}-{q*3:02d}-30` fallback (03-30 / 12-30).
let quarterEnds: [(String, String)] = [
    ("2026-03-31", "Q1 2026 13F"), ("2026-03-30", "Q1 2026 13F"),
    ("2026-06-30", "Q2 2026 13F"),
    ("2026-09-30", "Q3 2026 13F"),
    ("2026-12-31", "Q4 2026 13F"), ("2026-12-30", "Q4 2026 13F"),
    ("2025-12-31", "Q4 2025 13F"),
]
// The first day of each quarter: an off-by-one on the month boundary shows up here.
let quarterStarts: [(String, String)] = [
    ("2026-01-01", "Q1 2026 13F"), ("2026-04-01", "Q2 2026 13F"),
    ("2026-07-01", "Q3 2026 13F"), ("2026-10-01", "Q4 2026 13F"),
]
for id in zones {
    for (day, expect) in quarterEnds + quarterStarts {
        check("same_zone|\(id)|\(day)",
              ThirteenFQuarter.label(for: parsed(day, in: zone(id)), timeZone: zone(id)), expect)
    }
}

// Every month, written out rather than computed, so the expectation is not the formula.
let expectedQuarter = [1, 1, 1, 2, 2, 2, 3, 3, 3, 4, 4, 4]
for month in 1...12 {
    let day = "2026-" + (month < 10 ? "0" : "") + "\(month)-15"
    check("month|\(month)", ThirteenFQuarter.label(for: parsed(day, in: zone("UTC")), timeZone: zone("UTC")),
          "Q\(expectedQuarter[month - 1]) 2026 13F")
}

// `DateParser`'s second path: a UTC timestamp. A quarter end must stay in its quarter in
// every zone a phone can be in.
let iso = ISO8601DateFormatter()
for id in ["Pacific/Pago_Pago", "Etc/GMT+12", "Pacific/Kiritimati", "America/Los_Angeles", "Asia/Tokyo"] {
    check("iso|\(id)|q1", ThirteenFQuarter.label(for: iso.date(from: "2026-03-31T00:00:00Z")!, timeZone: zone(id)), "Q1 2026 13F")
    check("iso|\(id)|q2", ThirteenFQuarter.label(for: iso.date(from: "2026-06-30T00:00:00Z")!, timeZone: zone(id)), "Q2 2026 13F")
    check("iso|\(id)|q4", ThirteenFQuarter.label(for: iso.date(from: "2026-12-31T00:00:00Z")!, timeZone: zone(id)), "Q4 2026 13F")
}

// The DEFAULT zone is the device's — the zone `DateParser` parsed in. The process runs with
// TZ=Pacific/Kiritimati (+14 h); April 1 at local midnight is still March 31 in UTC, so a
// default that silently became UTC labels it Q1.
print("tz|\(TimeZone.current.identifier)")
check("default_zone_is_the_devices",
      ThirteenFQuarter.label(for: parsed("2026-04-01", in: .current)), "Q2 2026 13F")

print("DONE|\(failures)")
"""


def _run_swift() -> str:
    if not shutil.which("xcrun"):
        pytest.skip("xcrun unavailable — Swift cannot be executed on this host")
    assert _HELPER.exists(), f"{_HELPER} is missing — every executed case is vacuous"
    try:
        proc = subprocess.run(
            ["xcrun", "swift", "-"], input=_HELPER.read_text(encoding="utf-8") + "\n" + _HARNESS,
            text=True, capture_output=True, timeout=180,
            env={**os.environ, "TZ": "Pacific/Kiritimati"},
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        pytest.skip(f"could not run swift: {type(exc).__name__}: {exc}")
    if "DONE|" not in proc.stdout:
        pytest.fail(
            "the Swift harness did not run to completion — usually the helper stopped "
            "compiling standalone (a SwiftUI import will do it).\n"
            f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr[-4000:]}"
        )
    return proc.stdout


@pytest.fixture(scope="module")
def swift_output() -> str:
    return _run_swift()


def test_every_quarter_label_case(swift_output: str):
    failures = [line for line in swift_output.splitlines() if line.startswith("FAIL|")]
    assert not failures, "quarter label mismatches:\n  " + "\n  ".join(
        line.replace("|", "  ") for line in failures)


def test_the_harness_actually_asserted_something(swift_output: str):
    """Anti-vacuity: a harness that compiled but ran no checks would pass the test above."""
    oks = [line.split("|", 1)[1] for line in swift_output.splitlines() if line.startswith("ok|")]
    assert len(oks) >= 8 * 11 + 12 + 15 + 1, f"only {len(oks)} checks ran — the harness was truncated"
    for required in ("same_zone|Pacific/Kiritimati|2026-12-30", "same_zone|Pacific/Pago_Pago|2026-04-01",
                     "month|12", "iso|Etc/GMT+12|q4", "default_zone_is_the_devices"):
        assert required in oks, f"the {required} case did not run"
    # The default-zone case proves nothing unless the process really ran at +14 h.
    assert "tz|Pacific/Kiritimati" in swift_output.splitlines(), (
        "the harness did not run in TZ=Pacific/Kiritimati, so the default-zone case is vacuous")


# ── Whale Profile: the trade-group card ──────────────────────────────────────


def _profile_formatted_date() -> str:
    group = _braced(_code(_PROFILE_MODELS), r"struct WhaleTradeGroup\b[^{]*")
    assert not re.search(r"var formattedDate\s*:", group), (
        "the property form is back — a caller can render the label without the profile's "
        "congressional verdict")
    return _braced(group, r"func formattedDate\(whaleIsCongressional: Bool\) -> String")


def test_profile_13f_groups_name_the_quarter():
    fn = _profile_formatted_date()
    split = fn.index("guard WhaleTradeGroup.isReal(date)")
    congress, rest = fn[:split], fn[split:]
    assert _QUARTER_CALL in rest, "a 13F trade group no longer names its quarter"
    for relative in _RELATIVE:
        assert relative not in fn, f"a trade-group date is relative again ({relative})"
    assert "Calendar.current" not in fn and "Date()" not in fn, "the label counts days from now again"
    # A politician files no 13F: a congressional row missed by `isCongressional` (written
    # before migration 076) must hit the profile's verdict BEFORE the quarter.
    guard = re.search(r"if whaleIsCongressional \{ return formattedDateFull \}", rest)
    assert guard, "the profile's congressional verdict no longer gates the 13F quarter"
    assert guard.start() < rest.index(_QUARTER_CALL)
    # The congressional labels are unchanged and never become a quarter.
    assert "ThirteenFQuarter" not in congress
    for label in ('"Traded \\(tx) · Disclosed \\(disc)"', '"Traded \\(tx)"',
                  '"Disclosed \\(disc)"', '"Recently disclosed"'):
        assert label in congress, f"the congressional label {label} changed"


def test_the_profile_card_passes_the_profiles_congressional_verdict():
    view = _code(_PROFILE_VIEW)
    card = _braced(view, r"struct WhaleTradeGroupCard\b[^{]*")
    assert re.search(r"\blet isCongressionalWhale: Bool\b", card)
    assert "group.formattedDate(whaleIsCongressional: isCongressionalWhale)" in card

    section = _braced(view, r"struct WhaleRecentTradesSection\b[^{]*")
    card_call = _braced(section, r"WhaleTradeGroupCard\(", "(", ")")
    assert "isCongressionalWhale: isCongressional" in card_call, (
        "the section no longer hands the profile's verdict to the card")

    screen_call = _braced(view, r"WhaleRecentTradesSection\(", "(", ")")
    assert "isCongressional: profile.isCongressional" in screen_call


# ── Tracking: the whale timeline ─────────────────────────────────────────────


def _activity_formatted_date() -> str:
    activity = _braced(_code(_TRACKING_MODELS), r"struct WhaleTradeGroupActivity\b[^{]*")
    return _braced(activity, r"var formattedDate: String")


def test_tracking_13f_rows_name_the_quarter():
    fn = _activity_formatted_date()
    assert re.search(
        r"if category == \.investors \|\| category == \.institutions \{\s*"
        + re.escape(f"return {_QUARTER_CALL}") + r"\s*\}",
        fn,
    ), "13F filers (investors / institutions) no longer get the quarter label"
    for relative in _RELATIVE:
        assert relative not in fn, f"a timeline date is relative again ({relative})"
    assert "Calendar.current" not in fn and "Date()" not in fn, "the label counts days from now again"

    politicians = _braced(fn, r"if category == \.politicians")
    assert '"Disclosed \\(formatter.string(from: date))"' in politicians, (
        "the congressional 'Disclosed …' label changed")
    assert "ThirteenFQuarter" not in politicians


def test_the_timeline_renders_and_buckets_by_that_label():
    row = _braced(_code(_TRACKING_VIEW), r"struct WhaleTradeTimelineRow\b[^{]*")
    assert "Text(activity.formattedDate)" in row
    bucket = _braced(_code(_TRACKING_VM), r"private static func bucketByDate\(")
    assert "last.sectionTitle == activity.formattedDate" in bucket
    assert "sectionTitle: activity.formattedDate" in bucket


# ── anti-vacuity ─────────────────────────────────────────────────────────────


def test_the_scan_still_sees_the_explanatory_comments():
    """The timeline formatter's own comments quote the banned relative wording ("Today",
    "5 days ago"), so the strip above is load-bearing: without it the relative-wording
    assertions would fail on correct code — and the fix for that must not be deleting them."""
    raw_activity = _braced(_TRACKING_MODELS.read_text(encoding="utf-8"),
                           r"struct WhaleTradeGroupActivity\b[^{]*")
    raw_fn = _braced(raw_activity, r"var formattedDate: String")
    assert '"Today"' in raw_fn and ' ago"' in raw_fn
    assert "QUARTER END" in raw_fn
    assert "quarter END" in _PROFILE_MODELS.read_text(encoding="utf-8"), (
        "the trade-group label lost the comment explaining why it names the quarter")
