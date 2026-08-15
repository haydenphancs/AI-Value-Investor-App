"""Execute the Money Moves date formatter for real, by piping it into `xcrun swift -`.

This project has NO XCTest target, so every other iOS guard here is a source scan — which can
prove a branch *exists* but never that it produces the right string. `MoneyMoveDateFormatting`
is a ladder of six branches with three genuinely subtle ones (DST, the calendar-year boundary,
and exactly-seven-days-back), so "the branch exists" is not worth much.

It runs because the formatter was deliberately written to be runnable: Foundation-only, no
`import SwiftUI`, and `now`/`calendar` injected instead of read from the environment. Keep
those properties or this file goes dark. Precedent for shelling out from pytest:
`test_money_moves_art_parity.py` (git check-ignore) and `test_ios_theme_parity.py`.

Skips when `xcrun`/`swift` is unavailable (CI containers, Linux) rather than failing.

Category 1 (pure) — no network, no Supabase.
"""
import re
import shutil
import subprocess
from pathlib import Path

import pytest


def _strip_swift_comments(src: str) -> str:
    """A rule quoted in a `//` comment must never satisfy an assertion about the code.

    Load-bearing here: the formatter's own header comment says "no `import SwiftUI`", which a
    naive `"import SwiftUI" not in src` reads as the import itself.
    """
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("//"))

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parent
FORMATTER = REPO / "frontend/ios/ios/Core/Utilities/MoneyMoveDateFormatting.swift"

# "now" is Wednesday 2026-08-12 12:00 in America/Denver. A fixed weekday matters: several
# expectations below are weekday NAMES.
HARNESS = r"""
var cal = Calendar(identifier: .gregorian)
cal.timeZone = TimeZone(identifier: "America/Denver")!
cal.locale = Locale(identifier: "en_US")
let iso = ISO8601DateFormatter(); iso.formatOptions = [.withInternetDateTime]
func d(_ s: String) -> Date { iso.date(from: s)! }

var failures = 0
func check(_ name: String, _ got: String?, _ expect: String?) {
    if got == expect { print("ok|\(name)") }
    else { failures += 1; print("FAIL|\(name)|got=\(got ?? "nil")|expect=\(expect ?? "nil")") }
}
func label(_ when: String, now: String, style: MoneyMoveDateFormatting.Style = .full) -> String? {
    MoneyMoveDateFormatting.label(for: d(when), style: style, now: d(now), calendar: cal)
}

let NOW = "2026-08-12T18:00:00Z"   // Wednesday

check("today",               label("2026-08-12T09:00:00Z", now: NOW), "Today")
check("today_late_in_day",   label("2026-08-12T23:30:00Z", now: NOW), "Today")
check("future_clock_skew",   label("2026-08-20T09:00:00Z", now: NOW), "Today")
check("yesterday",           label("2026-08-11T18:00:00Z", now: NOW), "Yesterday")
check("two_days_weekday",    label("2026-08-10T18:00:00Z", now: NOW), "Monday")
check("six_days_weekday",    label("2026-08-06T18:00:00Z", now: NOW), "Thursday")
check("exactly_seven_days",  label("2026-08-05T18:00:00Z", now: NOW), "Aug 5")
check("eight_days",          label("2026-08-04T18:00:00Z", now: NOW), "Aug 4")
check("same_year_absolute",  label("2026-02-03T18:00:00Z", now: NOW), "Feb 3")
check("prior_year_absolute", label("2025-08-03T18:00:00Z", now: NOW), "Aug 3, 2025")
check("short_weekday",       label("2026-08-06T18:00:00Z", now: NOW, style: .short), "Thu")
check("short_keeps_full_year", label("2025-08-03T18:00:00Z", now: NOW, style: .short), "Aug 3, 2025")

// The sentinel. Seven placeholder cards ship with .distantPast; formatted naively they read
// "Jan 1, 1". nil means the label is omitted entirely.
check("distant_past_is_nil",
      MoneyMoveDateFormatting.label(for: .distantPast, now: d(NOW), calendar: cal), nil)

// Calendar-year boundary, NOT a 365-day window. From Jan 5 2027: Dec 31 is 5 days back so it
// is still a weekday, while Dec 28 is 8 days back and must carry the year.
check("cross_year_five_days", label("2026-12-31T18:00:00Z", now: "2027-01-05T18:00:00Z"), "Thursday")
check("cross_year_eight_days", label("2026-12-28T18:00:00Z", now: "2027-01-05T18:00:00Z"), "Dec 28, 2026")

// DST. US spring-forward is 2026-03-08, so a 23-hour day sits between Mar 7 and Mar 9.
// `timeIntervalSince / 86400` reports 1 here and would print "Yesterday".
check("dst_spring_forward", label("2026-03-07T18:00:00Z", now: "2026-03-09T18:00:00Z"), "Saturday")
// US fall-back is 2026-11-01 (a 25-hour day).
check("dst_fall_back", label("2026-10-31T18:00:00Z", now: "2026-11-02T18:00:00Z"), "Saturday")

// ISO parsing: the service normalizes to whole seconds, but un-normalized values must still
// resolve rather than dropping the article onto its drifting estimate.
func parsed(_ s: String) -> String? { MoneyMoveDateFormatting.parseISO8601(s).map { iso.string(from: $0) } }
check("iso_plain",       parsed("2026-06-12T14:03:21Z"), "2026-06-12T14:03:21Z")
check("iso_millis",      parsed("2026-06-12T14:03:21.123Z"), "2026-06-12T14:03:21Z")
check("iso_micros",      parsed("2026-06-12T14:03:21.123456Z"), "2026-06-12T14:03:21Z")
check("iso_offset",      parsed("2026-06-12T08:03:21-06:00"), "2026-06-12T14:03:21Z")
check("iso_garbage",     parsed("not-a-date"), nil)
check("iso_empty",       parsed(""), nil)
check("iso_whitespace",  parsed("  2026-06-12T14:03:21Z  "), "2026-06-12T14:03:21Z")

print("DONE|\(failures)")
"""


def _run_swift() -> str:
    if not shutil.which("xcrun"):
        pytest.skip("xcrun unavailable — Swift cannot be executed on this host")
    assert FORMATTER.exists(), f"{FORMATTER} is missing — the whole suite below is vacuous"
    src = FORMATTER.read_text() + "\n" + HARNESS
    try:
        proc = subprocess.run(
            ["xcrun", "swift", "-"], input=src, text=True,
            capture_output=True, timeout=180,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        pytest.skip(f"could not run swift: {type(exc).__name__}: {exc}")
    if "DONE|" not in proc.stdout:
        pytest.fail(
            "the Swift harness did not run to completion — this usually means the formatter "
            f"stopped compiling standalone (a SwiftUI import will do it).\n"
            f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr[-4000:]}"
        )
    return proc.stdout


@pytest.fixture(scope="module")
def swift_output() -> str:
    return _run_swift()


def test_formatter_is_foundation_only_and_injectable():
    """Both properties are what make this file executable outside a simulator."""
    raw = FORMATTER.read_text()
    src = _strip_swift_comments(raw)
    assert "import SwiftUI" not in src, (
        "MoneyMoveDateFormatting imports SwiftUI — it can no longer run under `xcrun swift -` "
        "and every case below silently stops being tested")
    assert "now: Date = Date()" in src and "calendar: Calendar = .current" in src, (
        "`now`/`calendar` must stay injectable — without them only the 'Today' case is testable")
    # Anti-vacuity: the header comment DOES name `import SwiftUI` while explaining the rule, so
    # if stripping ever broke, the assertion above would fail on correct code and someone would
    # "fix" it by deleting the check.
    assert "import SwiftUI" in raw, (
        "the comment explaining why SwiftUI is banned is gone — the strip check proves nothing")
    assert "import Foundation" in src, "the formatter must still import Foundation"


def test_every_date_label_branch(swift_output: str):
    failures = [l for l in swift_output.splitlines() if l.startswith("FAIL|")]
    assert not failures, "date label mismatches:\n  " + "\n  ".join(
        l.replace("|", "  ") for l in failures)


def test_the_harness_actually_asserted_something(swift_output: str):
    """Anti-vacuity: a harness that compiled but ran no checks would pass test above."""
    oks = [l for l in swift_output.splitlines() if l.startswith("ok|")]
    assert len(oks) >= 24, f"only {len(oks)} checks ran — the harness was truncated"
    names = {l.split("|", 1)[1] for l in oks}
    # The three subtle branches must be among them, by name.
    for required in ("distant_past_is_nil", "exactly_seven_days", "dst_spring_forward",
                     "cross_year_eight_days", "future_clock_skew"):
        assert required in names, f"the {required} case did not run"
