"""The 2026-10-29 time-zone rule, where it lands on the widget's compact label and on Account.

The owner's rule (2026-10-29, "ET for markets"): a MARKET time is New York time and says so —
" ET", never a hard-coded "EST", which is wrong while New York is on EDT — and the user's OWN
events are shown in the phone's zone. Two places broke it:

  * The widget's COMPACT label (`WidgetSessionLabel.compactWording`, what the Lock Screen's
    inline line falls back to) printed "As of 10:05" and "Tue 10:05": a New York time with no
    zone and no AM/PM, which a reader in Denver takes for 10:05 Mountain. The full labels
    ("As of 10:05 AM ET", "Tue 10:05 AM ET") were already right. The compact ones now keep
    " ET" and drop only the AM/PM.
  * Account's "Member since" formatted `users.created_at` — an instant, the user's own sign-up
    — in UTC, so an account opened on the evening of March 31 in a US zone said "Apr". It is
    now `.autoupdatingCurrent`, keeping its `en_US_POSIX` locale and Gregorian calendar.

What the label SAYS is proven by `frontend/ios/scripts/widget-session-label-check.sh`, which
compiles the real file and runs it in four device zones (main session only: it is a swiftc
build). There is no XCTest target, so this pins the source from Python. Per
`.claude/rules/testing.md` §3: comments are stripped first (the comments beside both fixes quote
every token scanned here), each scan is brace-bound to the declaration it means, and every guard
is mutation-tested — `test_each_guard_bites` applies a plausible regression to an in-memory COPY
and asserts the guard turns red. The files on disk are never touched.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Callable

import pytest

_IOS = Path(__file__).resolve().parents[2] / "frontend" / "ios"
_PATHS = {
    "label": _IOS / "Shared" / "WidgetSessionLabel.swift",
    "profile": _IOS / "ios" / "ViewModels" / "ProfileViewModel.swift",
    "harness": _IOS / "scripts" / "widget-session-label-check.sh",
}


def _sources() -> dict[str, str]:
    return {key: path.read_text(encoding="utf-8") for key, path in _PATHS.items()}


def _strip_comments(src: str) -> str:
    """Drop `/* */` blocks and `//` comments, blanking lines so positions stay meaningful.

    `\\s//`, not a bare `//`, for trailing comments: a bare one would eat `https://` in a
    literal. Same rule as `test_ios_widget_extension_guards._strip_comments`.
    """
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    out = []
    for line in src.splitlines():
        out.append("" if line.strip().startswith("//") else re.sub(r"\s//.*$", "", line))
    return "\n".join(out)


def _block(code: str, header: str) -> str:
    """The brace-balanced body after `header` in already comment-stripped `code`."""
    start = code.find(header)
    if start == -1:
        raise LookupError(f"{header!r} not found — this scan has drifted")
    open_brace = code.index("{", start)
    depth = 0
    for i in range(open_brace, len(code)):
        if code[i] == "{":
            depth += 1
        elif code[i] == "}":
            depth -= 1
            if depth == 0:
                return code[open_brace : i + 1]
    raise LookupError(f"unbalanced braces after {header!r}")


def _safe(guard: Callable[[dict[str, str]], list[str]], src: dict[str, str]) -> list[str]:
    """A guard whose anchor moved reports that as a problem rather than crashing the run."""
    try:
        return guard(src)
    except LookupError as e:
        return [f"scan could not find its anchor: {e}"]


# ── 1. Every time the label prints is New York time, labelled " ET" ──────────────────

# The two clocks the wordings may print a time through. Each must format in New York time,
# or the " ET" beside its digits is false.
_CLOCKS = ("clock", "shortClock")
# The two day formatters the wordings print beside those clocks, and the arm that prints each.
_DAYS = ("weekday", "monthDay")
_DAY_ARMS = {"dayAndTime": "weekday", "dayClose": "weekday", "monthDay": "monthDay"}
_ZONE_SET = re.compile(r"\.timeZone\s*=\s*([^\n]+)")


def _last_zone_is_new_york(body: str) -> bool:
    """The zone the formatter ends up with: a later `f.timeZone = .current` would win over a
    New York line before it."""
    zones = _ZONE_SET.findall(body)
    return bool(zones) and bool(_NY_ZONE.search(".timeZone = " + zones[-1]))
# The `AgedClaim` arms that print a time; the other three print a day, a date or legacy text.
_TIMED_ARMS = ("asOf", "dayAndTime")
_NY_ZONE = re.compile(r'\.timeZone\s*=\s*TimeZone\(identifier:\s*"America/New_York"\)')
# The returned expression ends in a literal " ET": `… + " ET"` or `"\(…) ET"`.
_ENDS_IN_ET = re.compile(r' ET"\s*$')
# A zone ABBREVIATION written into a string — right for half the year at most.
_HARD_CODED_ABBREVIATION = re.compile(r'"[^"\n]*\bE[SD]T\b[^"\n]*"')


def _returns(body: str) -> list[str]:
    return [m.group(1).strip() for m in re.finditer(r"\breturn\b([^\n]*)", body)]


def _arm(body: str, case: str) -> str:
    """What `case .<case>(…):` returns inside a wording's switch."""
    m = re.search(rf"\bcase\s+\.{case}\b[^\n:]*:\s*return\b([^\n]*)", body)
    if not m:
        raise LookupError(f"the `.{case}` arm (written `case .{case}(…): return …`)")
    return m.group(1).strip()


def _problems_compact_label_names_et(src: dict[str, str]) -> list[str]:
    code = _strip_comments(src["label"])
    problems: list[str] = []

    clock_bodies = {name: _block(code, f"private static func {name}(") for name in _CLOCKS}
    for name, body in clock_bodies.items():
        if not _last_zone_is_new_york(body):
            problems.append(
                f"{name} no longer formats in America/New_York — the ' ET' beside its digits "
                "would be false on every phone outside Eastern time"
            )
    # The day printed beside an ET clock ("Tue 10:05 ET", "Oct 28") is New York's too: the
    # session's day is a New York midnight, which west of New York is still the day before.
    for name in _DAYS:
        if not _last_zone_is_new_york(_block(code, f"private static func {name}(")):
            problems.append(
                f"{name} no longer formats in America/New_York — beside an ET clock it would "
                "print the phone's day, the previous one west of New York"
            )
    # …and every arm that prints a day prints it through those formatters, never its own.
    for wording in ("compactWording", "fullWording"):
        body = _block(code, f"private static func {wording}(")
        for case, formatter in _DAY_ARMS.items():
            expr = _arm(body, case)
            if not re.search(rf"(?<![\w.]){formatter}\(day\)", expr):
                problems.append(
                    f"{wording}'s .{case} arm prints its day without `{formatter}` (New York's "
                    f"day): {expr}"
                )

    def appends_et(name: str) -> bool:
        returns = _returns(clock_bodies[name])
        return bool(returns) and all(_ENDS_IN_ET.search(r) for r in returns)

    for wording in ("compactWording", "fullWording"):
        body = _block(code, f"private static func {wording}(")
        for case in _TIMED_ARMS:
            expr = _arm(body, case)
            used = [n for n in _CLOCKS if re.search(rf"(?<![\w.]){n}\(", expr)]
            if not used:
                problems.append(
                    f"{wording}'s .{case} arm prints its time through neither `clock` nor "
                    f"`shortClock` (the New York clocks): {expr}"
                )
                continue
            if not (_ENDS_IN_ET.search(expr) or all(appends_et(n) for n in used)):
                problems.append(
                    f"{wording}'s .{case} arm prints a New York time with no ' ET' — "
                    f"'Tue 10:05' reads as the phone's own clock: {expr}"
                )

    for m in _HARD_CODED_ABBREVIATION.finditer(code):
        problems.append(
            f"a hard-coded zone abbreviation {m.group(0)} — 'EST' is wrong while New York is "
            "on EDT; say ' ET'"
        )
    return problems


# ── 2. "Member since" is the user's own event: the phone's zone, not UTC ──────────────

_FIXED_ZONE = re.compile(
    r"TimeZone\(\s*(?:identifier|abbreviation|secondsFromGMT)\s*:|\.gmt\b|\"(?:UTC|GMT)\""
)
_AUTOUPDATING = re.compile(r"^(?:TimeZone)?\.autoupdatingCurrent$")


def _problems_member_since_local(src: dict[str, str]) -> list[str]:
    code = _strip_comments(src["profile"])
    problems: list[str] = []

    formatter = _block(code, "private static let memberSinceFormatter")
    zones = [z.strip() for z in re.findall(r"\.timeZone\s*=\s*([^\n]+)", formatter)]
    if len(zones) != 1 or not _AUTOUPDATING.match(zones[0]):
        problems.append(
            "memberSinceFormatter must set exactly one zone, `.autoupdatingCurrent` — "
            f"found {zones or 'none'}. `users.created_at` is the user's own sign-up: UTC put "
            "an account opened on the evening of March 31 in a US zone in April, and `.current` "
            "stays frozen in the zone of the first render"
        )
    for m in _FIXED_ZONE.finditer(formatter):
        problems.append(f"memberSinceFormatter pins a fixed zone ({m.group(0)})")
    if 'f.locale = Locale(identifier: "en_US_POSIX")' not in formatter:
        problems.append(
            "memberSinceFormatter lost its en_US_POSIX locale — a Japanese-calendar phone "
            "renders the era year ('Member since 8月 8')"
        )
    if "f.calendar = Calendar(identifier: .gregorian)" not in formatter:
        problems.append("memberSinceFormatter lost its Gregorian calendar")

    # The formatter checked above must be the one the row draws.
    row = _block(code, "var memberSince: String")
    if "Self.memberSinceFormatter.string(from:" not in row:
        problems.append("memberSince no longer formats through memberSinceFormatter")
    for other in ("DateFormatter(", ".formatted(", ".timeZone"):
        if other in row:
            problems.append(f"memberSince formats the date itself ({other}), around the pinned formatter")
    return problems


# ── 3. The harness that proves the wording still expects " ET" on every compact time ──

# `check("…", compact("<asOf>", <sessionDate>, "<phase>", "<now>"), "<expected>")`
_HARNESS_COMPACT_CASE = re.compile(
    r'compact\("[^"\n]*",\s*(?:"[^"\n]*"|nil),\s*"[^"\n]*",\s*"[^"\n]*"\),\s*"([^"\n]*)"\)'
)


def _problems_harness_expects_et(src: dict[str, str]) -> list[str]:
    code = _strip_comments(src["harness"])
    problems: list[str] = []
    timed = [e for e in _HARNESS_COMPACT_CASE.findall(code) if ":" in e]
    if len(timed) < 4:
        problems.append(
            f"the label harness checks only {len(timed)} compact time(s) — its compact cases "
            "were dropped or reshaped past this scan"
        )
    for expected in timed:
        if not expected.endswith(" ET"):
            problems.append(f"the label harness expects a compact time with no ' ET': {expected!r}")
    if 'hasSuffix(" ET")' not in code:
        problems.append("the label harness's 8-day sweep no longer asserts that every time names ET")
    return problems


_GUARDS: dict[str, Callable[[dict[str, str]], list[str]]] = {
    "compact_label_names_et": _problems_compact_label_names_et,
    "member_since_local": _problems_member_since_local,
    "harness_expects_et": _problems_harness_expects_et,
}


@pytest.mark.parametrize("name", sorted(_GUARDS))
def test_the_current_source_passes(name):
    problems = _safe(_GUARDS[name], _sources())
    assert problems == [], f"{name}: {problems}"


_SHORT_CLOCK_TAIL = '        f.dateFormat = "h:mm"\n        return f.string(from: d) + " ET"\n'
_FULL_CLOCK_TAIL = '        f.dateFormat = "h:mm a"\n        return f.string(from: d) + " ET"\n'
_MEMBER_ZONE = '        f.timeZone = .autoupdatingCurrent\n        f.dateFormat = "MMM yyyy"\n'

# Each: (guard, file, the exact text to replace, its regression). The text must exist — a
# mutation that no longer applies means the source moved and this table needs updating.
_MUTATIONS: list[tuple[str, str, str, str]] = [
    # The defect itself: the compact clock without its zone, or with a hard-coded one.
    ("compact_label_names_et", "label", _SHORT_CLOCK_TAIL,
     '        f.dateFormat = "h:mm"\n        return f.string(from: d)\n'),
    ("compact_label_names_et", "label", _SHORT_CLOCK_TAIL,
     '        f.dateFormat = "h:mm"\n        return f.string(from: d) + " EST"\n'),
    # " ET" kept, but the digits are the phone's clock.
    ("compact_label_names_et", "label",
     '        f.timeZone = TimeZone(identifier: "America/New_York")\n        f.dateFormat = "h:mm"\n',
     '        f.timeZone = .current\n        f.dateFormat = "h:mm"\n'),
    # A compact arm that prints its time some other way.
    ("compact_label_names_et", "label",
     'case .asOf(let t):                return "As of \\(shortClock(t))"',
     'case .asOf(let t):                return "As of \\(t.formatted(date: .omitted, time: .shortened))"'),
    ("compact_label_names_et", "label",
     'case .dayAndTime(let day, let t): return "\\(weekday(day)) \\(shortClock(t))"',
     'case .dayAndTime(let day, let t): return "\\(weekday(day)) \\(hourMinute(t))"'),
    # The day beside the clock, back on the phone's zone.
    ("compact_label_names_et", "label",
     '        f.timeZone = TimeZone(identifier: "America/New_York")\n        f.dateFormat = "EEE"\n',
     '        f.dateFormat = "EEE"\n'),
    ("compact_label_names_et", "label",
     '        f.timeZone = TimeZone(identifier: "America/New_York")\n        f.dateFormat = "MMM d"\n',
     '        f.timeZone = .current\n        f.dateFormat = "MMM d"\n'),
    # New York first, then overridden: the later zone is the one that prints.
    ("compact_label_names_et", "label",
     '        f.timeZone = TimeZone(identifier: "America/New_York")\n        f.dateFormat = "EEE"\n',
     '        f.timeZone = TimeZone(identifier: "America/New_York")\n        f.timeZone = .current\n'
     '        f.dateFormat = "EEE"\n'),
    # An arm printing its day some other way (the phone's zone).
    ("compact_label_names_et", "label",
     'case .monthDay(let day):          return monthDay(day)\n',
     'case .monthDay(let day):          return day.formatted(.dateTime.month().day())\n'),
    ("compact_label_names_et", "label",
     'return "\\(weekday(day)) \\(clock(t))"\n        case .dayClose(let day):          return "\\(weekday(day)) close"',
     'return "\\(weekday(day)) \\(clock(t))"\n'
     '        case .dayClose(let day):          return "\\(day.formatted(.dateTime.weekday())) close"'),
    # The full labels were right and must stay right.
    ("compact_label_names_et", "label", _FULL_CLOCK_TAIL,
     '        f.dateFormat = "h:mm a"\n        return f.string(from: d)\n'),
    ("compact_label_names_et", "label",
     'case .asOf(let t):                return "As of \\(clock(t))"',
     'case .asOf(let t):                return "As of \\(clock(t)) EST"'),
    # Member since: the revert, a second zone after the right one, a frozen zone, the MARKET
    # zone for a user's own event, the zone left implicit.
    ("member_since_local", "profile", _MEMBER_ZONE,
     '        f.timeZone = TimeZone(identifier: "UTC")\n        f.dateFormat = "MMM yyyy"\n'),
    ("member_since_local", "profile", _MEMBER_ZONE,
     '        f.timeZone = .autoupdatingCurrent\n        f.timeZone = TimeZone(secondsFromGMT: 0)!\n'
     '        f.dateFormat = "MMM yyyy"\n'),
    ("member_since_local", "profile", _MEMBER_ZONE,
     '        f.timeZone = .current\n        f.dateFormat = "MMM yyyy"\n'),
    ("member_since_local", "profile", _MEMBER_ZONE,
     '        f.timeZone = TimeZone(identifier: "America/New_York")\n        f.dateFormat = "MMM yyyy"\n'),
    ("member_since_local", "profile", _MEMBER_ZONE, '        f.dateFormat = "MMM yyyy"\n'),
    # …its locale and calendar, kept from the locale fix (test_ios_date_formatter_locale.py).
    ("member_since_local", "profile",
     '        f.locale = Locale(identifier: "en_US_POSIX")\n        f.calendar = Calendar(identifier: .gregorian)\n',
     '        f.locale = .autoupdatingCurrent\n        f.calendar = Calendar(identifier: .gregorian)\n'),
    ("member_since_local", "profile",
     '        f.calendar = Calendar(identifier: .gregorian)\n        f.timeZone = .autoupdatingCurrent\n',
     '        f.timeZone = .autoupdatingCurrent\n'),
    # …and the row drawing through some other formatter.
    ("member_since_local", "profile",
     'return "Member since \\(Self.memberSinceFormatter.string(from: date))"',
     'return "Member since \\(date.formatted(.dateTime.month(.abbreviated).year()))"'),
    # The harness re-pinned to the bare compact time, or its sweep's ET property removed.
    ("harness_expects_et", "harness", '"2026-08-12 06:50"), "Tue 10:05 ET")',
     '"2026-08-12 06:50"), "Tue 10:05")'),
    ("harness_expects_et", "harness", '!$0.hasSuffix(" ET")', '$0.isEmpty'),
]


@pytest.mark.parametrize(
    "guard, file, old, new", _MUTATIONS,
    ids=[f"{g}-{i}" for i, (g, *_rest) in enumerate(_MUTATIONS)],
)
def test_each_guard_bites(guard, file, old, new):
    """The mutation log, executable: the regression applied to a COPY must turn its guard red."""
    sources = _sources()
    assert sources[file].count(old) == 1, (
        f"mutation anchor for {guard!r} is gone or ambiguous in {file} — update the table, "
        "do not delete it"
    )
    mutated = dict(sources)
    mutated[file] = sources[file].replace(old, new, 1)
    problems = _safe(_GUARDS[guard], mutated)
    assert problems, f"{guard} stayed green on a regression: {new!r}"
    # Killed for the RIGHT reason: a mutation that merely broke an anchor proves nothing
    # about whether the guard would notice the regression it names.
    assert not all(p.startswith("scan could not find") for p in problems), problems


@pytest.mark.parametrize(
    "guard, file, old, new",
    [
        # The zone dropped from the code but left standing in a trailing comment.
        ("compact_label_names_et", "label", _SHORT_CLOCK_TAIL,
         '        f.dateFormat = "h:mm"\n        return f.string(from: d) // + " ET"\n'),
        # UTC back, with the right line kept as a whole-line comment above it.
        ("member_since_local", "profile", _MEMBER_ZONE,
         '        // f.timeZone = .autoupdatingCurrent\n        f.timeZone = TimeZone(identifier: "UTC")\n'
         '        f.dateFormat = "MMM yyyy"\n'),
    ],
    ids=["label-trailing-comment", "member-since-commented-line"],
)
def test_a_comment_cannot_satisfy_a_guard(guard, file, old, new):
    sources = _sources()
    assert sources[file].count(old) == 1
    mutated = dict(sources)
    mutated[file] = sources[file].replace(old, new, 1)
    assert _safe(_GUARDS[guard], mutated)


def test_the_scanner_helpers_are_not_vacuous():
    assert _strip_comments('let u = "https://x" // trailing') == 'let u = "https://x"'
    assert _strip_comments('/* a { */ b()\n    // c()\nd()') == " b()\n\nd()"
    assert _block("func a() {\n  if x { y() }\n}\nz()", "func a(") == "{\n  if x { y() }\n}"
    assert _arm('case .asOf(let t):   return "As of \\(shortClock(t))"', "asOf") == (
        '"As of \\(shortClock(t))"'
    )
    assert _ENDS_IN_ET.search('f.string(from: d) + " ET"')
    assert not _ENDS_IN_ET.search('f.string(from: d) + " EST"')
    assert not _ENDS_IN_ET.search("f.string(from: d)")
    assert _HARD_CODED_ABBREVIATION.search('return x + " EDT"')
    assert not _HARD_CODED_ABBREVIATION.search('"YESTERDAY"') and not _HARD_CODED_ABBREVIATION.search('" ET"')
    for path in _PATHS.values():
        assert path.exists(), f"{path} moved — every scan above would silently fail to anchor"
