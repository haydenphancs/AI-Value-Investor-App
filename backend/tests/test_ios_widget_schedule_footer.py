"""Source-scan guards: the widget's quiet-hours cadence, its ET 00:01 rollover and its off-hours footer.

The holdings-widget plan of 2026-11-27 (owner: "Every 90 min (Recommended)"; the compact footer
keeps no AM/PM) changed two Shared files the extension and the app both compile:

  * `WidgetRefreshSchedule.swift` — the quiet hours (overnight, the weekend, and the rest of a day
    the SERVER has called closed) reload every 90 minutes instead of sleeping to the next weekday's
    04:00 ET, never sleeping through a weekday's 04:00; a payload built on a weekday between 04:00
    and 20:00 ET whose `market_session` is exactly "closed" ends that ET day's market cadence; and
    the next ET 00:01 is ALWAYS an entry, even past the reload (the review's blocking finding: a
    90-minute reload no longer reaches midnight from an evening timeline, so one deferred reload
    left yesterday's evening render up after 00:00 as today's).
  * `WidgetSessionLabel.swift` — a same-day after-hours or closed snapshot older than
    `offHoursAgeLimit` (2 h, longer than every off-hours reload) says "As of 7:42 PM ET", with a
    render scheduled one second past that limit and capped to the session's ET day.

What the code DOES is proven by `frontend/ios/scripts/widget-refresh-schedule-check.sh` and
`widget-session-label-check.sh`, which compile the real files with `swiftc` — main session only, in
a build slot (CLAUDE.md, Machine safety). Those harnesses also hold rows that FLIPPED on purpose
(the overnight rows, the evening "closed" footer, the off-hours age boundaries); this file pins the
source and those rows from Python so a later session cannot quietly "fix them back", and it runs in
the ordinary suite with no Swift compile.

Per `.claude/rules/testing.md` §3: comments are stripped before every assertion, each scan is
brace-bound to the declaration it means, and every guard is mutation-tested — `test_each_guard_bites`
applies a plausible regression to an in-memory COPY and asserts the guard turns red, for the right
reason. The files on disk are never touched.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Callable

import pytest

_IOS = Path(__file__).resolve().parents[2] / "frontend" / "ios"
_PATHS = {
    "schedule": _IOS / "Shared" / "WidgetRefreshSchedule.swift",
    "label": _IOS / "Shared" / "WidgetSessionLabel.swift",
    "schedule_harness": _IOS / "scripts" / "widget-refresh-schedule-check.sh",
    "label_harness": _IOS / "scripts" / "widget-session-label-check.sh",
}


def _sources() -> dict[str, str]:
    return {key: path.read_text(encoding="utf-8") for key, path in _PATHS.items()}


def _strip_comments(src: str) -> str:
    """Drop `/* */` blocks and `//` comments, blanking lines so positions stay meaningful.

    `\\s//`, not a bare `//`, for trailing comments: a bare one would eat `https://` in a literal.
    Same rule as `test_ios_widget_extension_guards._strip_comments`.
    """
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    out = []
    for line in src.splitlines():
        out.append("" if line.strip().startswith("//") else re.sub(r"\s//.*$", "", line))
    return "\n".join(out)


def _balanced_from(code: str, open_brace: int) -> str:
    depth = 0
    for i in range(open_brace, len(code)):
        if code[i] == "{":
            depth += 1
        elif code[i] == "}":
            depth -= 1
            if depth == 0:
                return code[open_brace : i + 1]
    raise LookupError(f"unbalanced braces from offset {open_brace}")


def _block(code: str, header: str) -> str:
    """The brace-balanced body after `header` in ALREADY comment-stripped `code`."""
    start = code.find(header)
    if start == -1:
        raise LookupError(f"{header!r} not found — this scan has drifted")
    return _balanced_from(code, code.index("{", start))


def _flat(code: str) -> str:
    """Whitespace collapsed to single spaces, so a re-wrapped line still matches."""
    return re.sub(r"\s+", " ", code).strip()


def _literal_seconds(code: str, name: str) -> float:
    """`static let <name>: TimeInterval = <integer arithmetic>` → seconds."""
    m = re.search(rf"static let {name}: TimeInterval = ([0-9 *+]+)\n", code)
    if not m:
        raise LookupError(f"`static let {name}: TimeInterval = <literal>` is gone")
    expr = m.group(1).strip()
    if not re.fullmatch(r"[0-9]+(?:\s*[*+]\s*[0-9]+)*", expr):
        raise LookupError(f"{name} is not a plain literal: {expr!r}")
    return float(eval(expr))  # digits, `*` and `+` only — checked on the line above


def _safe(guard: Callable[[dict[str, str]], list[str]], src: dict[str, str]) -> list[str]:
    """A guard whose anchor moved reports that as a problem rather than crashing the run."""
    try:
        return guard(src)
    except LookupError as e:
        return [f"scan could not find its anchor: {e}"]


# ── 1. One quiet branch: 90 minutes, never past a weekday's 04:00 ────────────────────

_QUIET_CONDITION = (
    "if isWeekend || minuteOfDay < premarketStart || minuteOfDay >= afterHoursEnd "
    "|| closedForTheDay(marketSession: marketSession, builtAt: builtAt, now: now, cal: cal) {"
)
_CLAMP = (
    "if !isWeekend, minuteOfDay < premarketStart, "
    "let open = atMinute(premarketStart, on: now, cal: cal), open < step { return open }"
)
_SIGNATURE = (
    "public static func nextRefresh( after now: Date, marketSession: String? = nil, "
    "builtAt: Date? = nil ) -> Date {"
)


def _problems_quiet_cadence(src: dict[str, str]) -> list[str]:
    code = _strip_comments(src["schedule"])
    problems: list[str] = []

    regular = _literal_seconds(code, "regularInterval")
    extended = _literal_seconds(code, "extendedInterval")
    quiet = _literal_seconds(code, "quietInterval")
    if not regular < extended < quiet:
        problems.append(
            f"the cadences no longer rise from the session outwards (regular={regular}s, "
            f"extended={extended}s, quiet={quiet}s) — the budget is spent where nothing trades"
        )
    if quiet != 90 * 60:
        problems.append(
            f"quietInterval is {quiet}s; the owner chose 90 minutes (2026-11-27). 60 asks for 37 "
            "a trading day and 23 a weekend day; longer leaves a revoked Holdings grant on screen"
        )

    if _SIGNATURE not in _flat(code):
        problems.append(
            "nextRefresh's signature changed: it must be `nextRefresh(after:marketSession:builtAt:)` "
            "with both hints defaulting to nil, so the provider's hint and every old call compile"
        )
    body = _block(code, "public static func nextRefresh(")
    flat = _flat(body)
    if flat.count(_QUIET_CONDITION) != 1:
        problems.append(
            "the ONE quiet branch (weekend || before 04:00 || from 20:00 || closedForTheDay(…)) is "
            "gone, split, or no longer passes the payload's hint through"
        )
    else:
        quiet_at = body.index("closedForTheDay(marketSession:")
        branch = _balanced_from(body, body.index("{", quiet_at))
        branch_flat = _flat(branch)
        if "let step = now.addingTimeInterval(quietInterval)" not in branch_flat:
            problems.append("the quiet branch no longer steps by quietInterval")
        if _CLAMP not in branch_flat:
            problems.append(
                "the quiet branch no longer stops at a weekday's 04:00 — a 03:00 step would land at "
                "04:30 and skip the start of pre-market"
            )
        if "return step" not in branch_flat:
            problems.append("the quiet branch no longer returns its 90-minute step")
        if "let interval =" in body and body.index("let interval =") < quiet_at:
            problems.append("the quiet branch runs after the session cadence is chosen")
    # The old overnight sleep was a jump to the next weekday's 04:00. Any day-sized step is it.
    for banned in ("nextPremarketOpen", "byAdding: .day"):
        if banned in code:
            problems.append(f"`{banned}` is back — the tile sleeps to the next weekday again")
    return problems


# ── 2. The server's "closed" ends the day — exactly, and only for today's build ────────

def _problems_closed_for_the_day(src: dict[str, str]) -> list[str]:
    code = _strip_comments(src["schedule"])
    problems: list[str] = []
    body = _flat(_block(code, "static func closedForTheDay("))
    if 'guard marketSession == "closed", let builtAt else { return false }' not in body:
        problems.append("closedForTheDay no longer requires exactly \"closed\" and a build time")
    if re.search(r"lowercased|uppercased|caseInsensitive|localizedCompare", body):
        problems.append("the phase is compared loosely — the wire value is a closed set, compared exactly")
    if "guard builtAt <= now.addingTimeInterval(buildSkewTolerance) else { return false }" not in body:
        problems.append(
            "a build stamped after now (beyond the skew tolerance) is honoured — a skewed clock "
            "could decide a day it was not built on"
        )
    if "guard cal.isDate(builtAt, inSameDayAs: now) else { return false }" not in body:
        problems.append("yesterday's closed build now ends today's market cadence")
    if "guard weekday != 1, weekday != 7 else { return false }" not in body:
        problems.append("a weekend build is honoured as a weekday hint")
    if "return builtMinute >= premarketStart && builtMinute < afterHoursEnd" not in body:
        problems.append(
            "a build outside 04:00-20:00 ET is honoured — every weekday is 'closed' before 04:00, "
            "so a 03:59 build would put a whole trading day on the quiet cadence"
        )
    tolerance = _literal_seconds(code, "buildSkewTolerance")
    if not 0 < tolerance <= 10 * 60:
        problems.append(
            f"buildSkewTolerance is {tolerance}s: it must cover a fetch (≤ 12 s) and a little skew, "
            "never a build from a different part of the day"
        )
    return problems


# ── 3. The ET 00:01 rollover is ALWAYS an entry ────────────────────────────────────────

def _problems_rollover_always(src: dict[str, str]) -> list[str]:
    code = _strip_comments(src["schedule"])
    problems: list[str] = []
    body = _block(code, "public static func renderDates(now: Date, reload: Date)")
    flat = _flat(body)
    if (
        "if let rollover = easternCalendar.nextDate( after: now, "
        "matching: DateComponents(hour: 0, minute: 1), matchingPolicy: .nextTime ) {"
    ) not in flat:
        problems.append(
            "the rollover is no longer the next ET 00:01 taken unconditionally (a clause after "
            "`.nextTime )` filters it again)"
        )
    if "dates.append(rollover)" not in flat:
        problems.append("the rollover is computed but never becomes an entry")
    if re.search(r"rollover\s*[<>]=?|[<>]=?\s*rollover\b", body):
        problems.append(
            "the rollover is compared with something again — it was dropped whenever it came after "
            "the reload, which a 90-minute evening reload always is"
        )
    if "if d < reload { dates.append(d) }" not in flat:
        problems.append("the +20/+60/+180 renders are no longer limited to before the reload")
    return problems


# ── 4. A same-day after-hours or closed snapshot says WHEN past 2 hours ───────────────

_OFF_HOURS_ARM = (
    'if marketSession == "afterhours" || marketSession == "closed", '
    "now.timeIntervalSince(asOf) > offHoursAgeLimit { return .asOf(asOf) }"
)
_AGED_CLAIM_CASES = {"asOf", "dayAndTime", "dayClose", "monthDay", "legacy"}


def _problems_off_hours_age(src: dict[str, str]) -> list[str]:
    code = _strip_comments(src["label"])
    problems: list[str] = []

    limit = _literal_seconds(code, "offHoursAgeLimit")
    if limit != 2 * 60 * 60:
        problems.append(f"offHoursAgeLimit is {limit}s; the agreed limit is 2 hours (2026-11-27)")

    claim = _block(code, "static func agedClaim(")
    same_day = _flat(_block(claim, "if daysAgo <= 0 {"))
    if same_day.count(_OFF_HOURS_ARM) != 1:
        problems.append(
            "agedClaim's same-day block lost the after-hours/closed rule (both phases, "
            "offHoursAgeLimit, strictly past it, the existing .asOf claim)"
        )

    boundary = _block(code, "public static func ageBoundary(")
    flat = _flat(boundary)
    if 'case "afterhours", "closed": boundary = asOf.addingTimeInterval(offHoursAgeLimit + 1)' not in flat:
        problems.append(
            "ageBoundary no longer renders one second past offHoursAgeLimit for after-hours and "
            "closed — the label waits for a reload instead"
        )
    if "guard boundary > now, cal.isDate(boundary, inSameDayAs: day) else { return nil }" not in flat:
        problems.append(
            "ageBoundary is no longer capped to the session's ET day — a 22:30 closed build would "
            "schedule a 'same-day' render at 00:30 tomorrow"
        )

    # D14: the off-hours rule REUSES the existing claim, so the compact/full parity property
    # keeps holding. A new case means new wording on both sides and new harness rows.
    enum = _block(code, "enum AgedClaim")
    cases = set(re.findall(r"\bcase\s+(\w+)", enum))
    if cases != _AGED_CLAIM_CASES:
        problems.append(
            f"AgedClaim's cases changed ({sorted(cases)}): the off-hours rule reuses .asOf by "
            "decision; a new claim needs its own compact wording and harness rows"
        )
    short = _block(code, "private static func shortClock(")
    formats = re.findall(r'\.dateFormat\s*=\s*"([^"]*)"', short)
    if formats != ["h:mm"]:
        problems.append(
            f"the compact clock's format is {formats} — the owner kept it without AM/PM "
            "(2026-11-27); changing it needs his word and new compact rows in the label harness"
        )
    # The rationale beside it used to claim the opposite of what is now true.
    if "the trading hours make it" in src["label"]:
        problems.append(
            "shortClock's comment claims the compact time is unambiguous again; the off-hours "
            "'As of' claims make that false (rewritten 2026-11-27)"
        )
    return problems


# ── 5. The cross-file relation: each age limit outlasts the reloads it sits beside ────

def _problems_age_limits_outlast_reloads(src: dict[str, str]) -> list[str]:
    schedule = _strip_comments(src["schedule"])
    label = _strip_comments(src["label"])
    problems: list[str] = []
    regular = _literal_seconds(schedule, "regularInterval")
    extended = _literal_seconds(schedule, "extendedInterval")
    quiet = _literal_seconds(schedule, "quietInterval")
    intraday = _literal_seconds(label, "intradayAgeLimit")
    off_hours = _literal_seconds(label, "offHoursAgeLimit")
    if not intraday > regular:
        problems.append(
            f"intradayAgeLimit ({intraday}s) no longer outlasts the regular reload ({regular}s): a "
            "tile that keeps up would say 'As of' between every two reloads"
        )
    if not off_hours > max(extended, quiet):
        problems.append(
            f"offHoursAgeLimit ({off_hours}s) no longer outlasts the off-hours reloads "
            f"({extended}s, {quiet}s): a tile that keeps up would say 'As of' all evening"
        )
    return problems


# ── 6. The harness rows that flipped on purpose stay flipped ──────────────────────────

_RENDER_ROW = re.compile(r'renders\("([^"\n]+)"\),\s*"([^"\n]+)"\)')


def _problems_harness_rows(src: dict[str, str]) -> list[str]:
    sched = _strip_comments(src["schedule_harness"])
    label = _strip_comments(src["label_harness"])
    problems: list[str] = []

    rows = _RENDER_ROW.findall(sched)
    if len(rows) < 6:
        problems.append(f"the schedule harness checks only {len(rows)} render rows")
    for now, expected in rows:
        if " 00:01" not in expected:
            problems.append(
                f"the render row for {now} expects no ET 00:01 ({expected!r}) — every timeline "
                "carries it now, past the reload included"
            )
    for want in (
        'next("2026-08-26 21:00"), "2026-08-26 22:30")',
        'next("2026-08-28 21:00"), "2026-08-28 22:30")',
        'next("2026-08-29 12:00"), "2026-08-29 13:30")',
        'next("2026-08-26 03:00"), "2026-08-26 04:00")',
        'built: "2026-11-26 10:00"), "2026-11-26 11:30")',
        "$0 >= reload && $0 != rollover",
    ):
        if want not in sched:
            problems.append(f"the schedule harness lost {want!r}")
    for count in ('"35")', '"15")', '"16")', '"27")'):
        if count not in sched:
            problems.append(f"the schedule harness no longer pins a daily count of {count[:-1]}")
    if sched.count("nextH(") < 10:
        problems.append("the schedule harness lost its closed-for-the-day table")

    for want in (
        'aged(d("2026-08-14 21:00"), phase: "closed", server: "Fri close") ?? "<nil>", "As of 3:58 PM ET")',
        '"2026-08-11 17:10"), "2026-08-11 19:02:01")',
        '"2026-08-11 21:05"), "2026-08-11 23:00:01")',
        '"2026-08-11 22:35"), "<nil>")',
        '("2026-08-11 17:02", "afterhours", "2026-08-11 17:10")',
        '("2026-08-11 21:00", "closed", "2026-08-11 21:05")',
        '"afterhours", "2026-08-11 19:03"), "As of 5:02 ET")',
    ):
        if want not in label:
            problems.append(f"the label harness lost {want!r}")
    for path in ("schedule_harness", "label_harness"):
        if "for tz in " not in src[path]:
            problems.append(f"{path} no longer runs under several device time zones")
    return problems


_GUARDS: dict[str, Callable[[dict[str, str]], list[str]]] = {
    "quiet_cadence": _problems_quiet_cadence,
    "closed_for_the_day": _problems_closed_for_the_day,
    "rollover_always": _problems_rollover_always,
    "off_hours_age": _problems_off_hours_age,
    "age_limits_outlast_reloads": _problems_age_limits_outlast_reloads,
    "harness_rows": _problems_harness_rows,
}


@pytest.mark.parametrize("name", sorted(_GUARDS))
def test_the_current_source_passes(name):
    problems = _safe(_GUARDS[name], _sources())
    assert problems == [], f"{name}: {problems}"


_QUIET_IF_SRC = (
    "        if isWeekend || minuteOfDay < premarketStart || minuteOfDay >= afterHoursEnd\n"
    "            || closedForTheDay(marketSession: marketSession, builtAt: builtAt, now: now, cal: cal) {\n"
)
_CLAMP_SRC = (
    "            if !isWeekend, minuteOfDay < premarketStart,\n"
    "               let open = atMinute(premarketStart, on: now, cal: cal), open < step {\n"
    "                return open\n"
    "            }\n"
)
_ROLLOVER_SRC = "            matchingPolicy: .nextTime\n        ) {\n            dates.append(rollover)\n"

# Each: (guard, file, the exact text to replace, its regression). The text must exist exactly
# once — a mutation that no longer applies means the source moved and this table needs updating.
_MUTATIONS: list[tuple[str, str, str, str]] = [
    # The night sleeps again, in the ways it could come back.
    ("quiet_cadence", "schedule",
     "    static let quietInterval: TimeInterval = 90 * 60\n",
     "    static let quietInterval: TimeInterval = 20 * 60\n"),
    ("quiet_cadence", "schedule",
     "    static let quietInterval: TimeInterval = 90 * 60\n",
     "    static let quietInterval: TimeInterval = 60 * 60\n"),
    ("quiet_cadence", "schedule", _QUIET_IF_SRC,
     "        if isWeekend || minuteOfDay < premarketStart || minuteOfDay >= afterHoursEnd {\n"),
    ("quiet_cadence", "schedule", _QUIET_IF_SRC,
     "        if isWeekend || minuteOfDay < premarketStart || minuteOfDay >= afterHoursEnd\n"
     "            || closedForTheDay(marketSession: nil, builtAt: builtAt, now: now, cal: cal) {\n"),
    ("quiet_cadence", "schedule", _CLAMP_SRC, ""),
    ("quiet_cadence", "schedule", _CLAMP_SRC, _CLAMP_SRC.replace("open < step", "open > step")),
    ("quiet_cadence", "schedule", _CLAMP_SRC, _CLAMP_SRC.replace("if !isWeekend, ", "if ")),
    ("quiet_cadence", "schedule",
     "            return step\n        }\n",
     "            return nextPremarketOpen(after: now, cal: cal)\n        }\n"),
    ("quiet_cadence", "schedule",
     "        after now: Date, marketSession: String? = nil, builtAt: Date? = nil\n",
     "        after now: Date, marketSession: String?, builtAt: Date?\n"),
    # The hint widened past what the server's phase can prove.
    ("closed_for_the_day", "schedule",
     '        guard marketSession == "closed", let builtAt else { return false }\n',
     '        guard marketSession?.lowercased() == "closed", let builtAt else { return false }\n'),
    ("closed_for_the_day", "schedule",
     "        guard builtAt <= now.addingTimeInterval(buildSkewTolerance) else { return false }\n", ""),
    ("closed_for_the_day", "schedule",
     "        guard cal.isDate(builtAt, inSameDayAs: now) else { return false }\n", ""),
    ("closed_for_the_day", "schedule",
     "        return builtMinute >= premarketStart && builtMinute < afterHoursEnd\n",
     "        return builtMinute < afterHoursEnd\n"),
    ("closed_for_the_day", "schedule",
     "    static let buildSkewTolerance: TimeInterval = 5 * 60\n",
     "    static let buildSkewTolerance: TimeInterval = 60 * 60\n"),
    # The rollover filtered again, or not appended at all.
    ("rollover_always", "schedule", _ROLLOVER_SRC,
     "            matchingPolicy: .nextTime\n        ), rollover < reload {\n            dates.append(rollover)\n"),
    ("rollover_always", "schedule", _ROLLOVER_SRC,
     "            matchingPolicy: .nextTime\n        ) {\n            _ = rollover\n"),
    ("rollover_always", "schedule",
     "            after: now, matching: DateComponents(hour: 0, minute: 1),\n",
     "            after: now, matching: DateComponents(hour: 4, minute: 1),\n"),
    # The off-hours footer, narrowed, re-timed, uncapped or reworded.
    ("off_hours_age", "label",
     '            if marketSession == "afterhours" || marketSession == "closed",\n',
     '            if marketSession == "afterhours",\n'),
    ("off_hours_age", "label",
     "               now.timeIntervalSince(asOf) > offHoursAgeLimit {\n",
     "               now.timeIntervalSince(asOf) > intradayAgeLimit {\n"),
    ("off_hours_age", "label",
     "            boundary = asOf.addingTimeInterval(offHoursAgeLimit + 1)\n",
     "            boundary = asOf.addingTimeInterval(2 * 60 * 60 + 1)\n"),
    ("off_hours_age", "label",
     "        guard boundary > now, cal.isDate(boundary, inSameDayAs: day) else { return nil }\n",
     "        guard boundary > now else { return nil }\n"),
    ("off_hours_age", "label",
     '        case "afterhours", "closed":\n            boundary = asOf.addingTimeInterval(offHoursAgeLimit + 1)\n',
     ""),
    ("off_hours_age", "label",
     "    static let offHoursAgeLimit: TimeInterval = 2 * 60 * 60\n",
     "    static let offHoursAgeLimit: TimeInterval = 45 * 60\n"),
    ("off_hours_age", "label",
     '        f.dateFormat = "h:mm"\n',
     '        f.dateFormat = "h:mm a"\n'),
    ("off_hours_age", "label",
     "        case legacy(String)\n",
     "        case legacy(String)\n        case updated(Date)\n"),
    ("off_hours_age", "label",
     "    /// \" ET\", never a literal \"EST\": that is wrong whenever New York is on EDT.\n",
     "    /// \" ET\", never a literal \"EST\": that is wrong whenever New York is on EDT.\n"
     "    /// No AM/PM: beside a weekday or \"As of\" on a market tile, the trading hours make it\n"
     "    /// unambiguous.\n"),
    # The relation between the two files, broken from either side.
    ("age_limits_outlast_reloads", "schedule",
     "    static let quietInterval: TimeInterval = 90 * 60\n",
     "    static let quietInterval: TimeInterval = 3 * 60 * 60\n"),
    ("age_limits_outlast_reloads", "label",
     "    static let offHoursAgeLimit: TimeInterval = 2 * 60 * 60\n",
     "    static let offHoursAgeLimit: TimeInterval = 60 * 60\n"),
    ("age_limits_outlast_reloads", "label",
     "    static let intradayAgeLimit: TimeInterval = 45 * 60\n",
     "    static let intradayAgeLimit: TimeInterval = 15 * 60\n"),
    # A flipped row flipped back, or the rollover dropped from an expectation.
    ("harness_rows", "schedule_harness",
     'next("2026-08-26 21:00"), "2026-08-26 22:30")',
     'next("2026-08-26 21:00"), "2026-08-27 04:00")'),
    ("harness_rows", "schedule_harness",
     '"2026-08-25 20:30,2026-08-25 20:50,2026-08-25 21:30,2026-08-26 00:01")',
     '"2026-08-25 20:30,2026-08-25 20:50,2026-08-25 21:30")'),
    ("harness_rows", "schedule_harness",
     "$0 >= reload && $0 != rollover", "$0 >= reload"),
    ("harness_rows", "schedule_harness",
     'check("the 13:00 half day: exactly 27", "\\(halfDay)", "27")\n', ""),
    ("harness_rows", "label_harness",
     'aged(d("2026-08-14 21:00"), phase: "closed", server: "Fri close") ?? "<nil>", "As of 3:58 PM ET")',
     'aged(d("2026-08-14 21:00"), phase: "closed", server: "Fri close") ?? "<nil>", "<nil>")'),
    ("harness_rows", "label_harness",
     '"2026-08-11 17:10"), "2026-08-11 19:02:01")', '"2026-08-11 17:10"), "<nil>")'),
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
        # The off-hours arm deleted from the code but left standing in a comment.
        ("off_hours_age", "label",
         '            if marketSession == "afterhours" || marketSession == "closed",\n'
         "               now.timeIntervalSince(asOf) > offHoursAgeLimit {\n"
         "                return .asOf(asOf)\n"
         "            }\n",
         '            // if marketSession == "afterhours" || marketSession == "closed",\n'
         "            //    now.timeIntervalSince(asOf) > offHoursAgeLimit {\n"
         "            //     return .asOf(asOf)\n"
         "            // }\n"),
        # The filter re-added, with the unfiltered form kept in a trailing comment.
        ("rollover_always", "schedule", _ROLLOVER_SRC,
         "            matchingPolicy: .nextTime\n        ), rollover < reload { // ) {\n"
         "            dates.append(rollover)\n"),
    ],
    ids=["off-hours-arm-commented-out", "rollover-filter-behind-a-comment"],
)
def test_a_comment_cannot_satisfy_a_guard(guard, file, old, new):
    sources = _sources()
    assert sources[file].count(old) == 1
    mutated = dict(sources)
    mutated[file] = sources[file].replace(old, new, 1)
    assert _safe(_GUARDS[guard], mutated)


# ── 7. The two server-side facts the closed-for-the-day hint stands on ─────────────────
#
# The client never re-derives the phase; it trusts the payload's `market_session` at `as_of`.
# That is safe only while (a) a weekday that reads "closed" at or after 04:00 ET stays closed to
# ET midnight, and (b) `as_of` is taken BEFORE the phase, so a phase read that straddles 04:00 can
# only say "closed" at an instant that really was closed. Flip (b) and a "closed" read at 03:59:59
# stamped 04:00:00 would put a whole trading day on the 90-minute cadence.

_PHASES = {"closed", "premarket", "regular", "afterhours"}


def _first_closed_from_four(day) -> str | None:
    """The ET minute ("HH:MM") from which `session_phase` reads closed for the rest of `day`
    (from 04:00 on), or a description of the first minute that reopened."""
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo

    from app.utils.market_hours import session_phase

    et = ZoneInfo("America/New_York")
    first_closed = None
    t = datetime(day.year, day.month, day.day, 4, 0, tzinfo=et)
    end = datetime(day.year, day.month, day.day, 23, 59, tzinfo=et)
    while t <= end:
        phase = session_phase(t)
        assert phase in _PHASES, f"session_phase returned {phase!r}, outside the wire's closed set"
        if phase == "closed" and first_closed is None:
            first_closed = t
        elif phase != "closed" and first_closed is not None:
            return f"REOPENED at {t:%H:%M} after closing at {first_closed:%H:%M}"
        t += timedelta(minutes=1)
    return None if first_closed is None else f"{first_closed:%H:%M}"


@pytest.mark.parametrize(
    "day, expected",
    [
        ("2026-11-25", "20:00"),  # an ordinary Wednesday
        ("2026-11-26", "04:00"),  # Thanksgiving: closed all day
        ("2026-11-27", "13:00"),  # the half day: closed from its 13:00 close
        ("2026-08-28", "20:00"),  # an ordinary Friday
    ],
)
def test_a_weekday_that_closes_from_four_stays_closed_to_midnight(day, expected):
    from datetime import date

    assert _first_closed_from_four(date.fromisoformat(day)) == expected


def test_no_weekday_of_the_year_reopens_after_closing():
    """Every weekday of 2026 and 2027, minute by minute from 04:00 — the tables change yearly."""
    from datetime import date, timedelta

    d = date(2026, 1, 1)
    reopened = []
    while d <= date(2027, 12, 31):
        if d.weekday() < 5:
            got = _first_closed_from_four(d)
            if got is None or got.startswith("REOPENED"):
                reopened.append(f"{d}: {got}")
        d += timedelta(days=1)
    assert reopened == [], reopened[:5]


_PAYLOAD_SOURCES = (
    Path(__file__).resolve().parents[1] / "app" / "services" / "widget_movers_service.py",
    Path(__file__).resolve().parents[1] / "app" / "api" / "v1" / "endpoints" / "widget.py",
)


def _payload_order_problems(sources: dict[str, str]) -> list[str]:
    import ast

    problems: list[str] = []
    found = 0
    for name, text in sources.items():
        for node in ast.walk(ast.parse(text)):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id == "WidgetMoverPayload"):
                continue
            found += 1
            keys = [k.arg for k in node.keywords]
            if "as_of" not in keys or "market_session" not in keys:
                problems.append(f"{name}:{node.lineno}: WidgetMoverPayload( without as_of AND market_session")
                continue
            if keys.index("as_of") > keys.index("market_session"):
                problems.append(
                    f"{name}:{node.lineno}: market_session is evaluated before as_of — a 'closed' "
                    "read at 03:59:59 could be stamped 04:00:00 and end a trading day"
                )
            as_of = next(k.value for k in node.keywords if k.arg == "as_of")
            src = ast.unparse(as_of)
            if not (src == "_iso_now()" or (".strftime(" in src and "%f" not in src)):
                problems.append(
                    f"{name}:{node.lineno}: as_of is not a whole-second timestamp ({src}) — the "
                    "client's same-minute arithmetic and Swift's .iso8601 both assume one"
                )
    if found < 2:
        problems.append(f"found {found} WidgetMoverPayload( constructors — this scan has drifted")
    return problems


def test_every_widget_payload_stamps_as_of_before_its_phase():
    sources = {p.name: p.read_text(encoding="utf-8") for p in _PAYLOAD_SOURCES}
    assert _payload_order_problems(sources) == []


@pytest.mark.parametrize("which", [0, 1])
def test_the_payload_order_scan_bites(which):
    """Swap the two keywords in ONE constructor, in a copy: the scan must turn red."""
    import re as _re

    sources = {p.name: p.read_text(encoding="utf-8") for p in _PAYLOAD_SOURCES}
    name = _PAYLOAD_SOURCES[which].name
    text = sources[name]
    m = _re.search(r"(\n(\s*)as_of=[^\n]*\n)(\s*market_session=[^\n]*\n)", text)
    assert m, f"the as_of/market_session pair moved in {name} — update this mutation"
    swapped = text[: m.start()] + "\n" + m.group(3) + m.group(1)[1:] + text[m.end():]
    assert swapped != text
    mutated = dict(sources)
    mutated[name] = swapped
    assert _payload_order_problems(mutated), f"swapping the keywords in {name} passed the scan"


def test_the_scanner_helpers_are_not_vacuous():
    assert _strip_comments('let u = "https://x" // trailing') == 'let u = "https://x"'
    assert _strip_comments("/* a { */ b()\n    // c()\nd()") == " b()\n\nd()"
    code = "func a() {\n  if x { y() }\n}\nz()"
    assert _block(code, "func a(") == "{\n  if x { y() }\n}"
    assert _flat("a\n    b  c") == "a b c"
    assert _literal_seconds("static let x: TimeInterval = 90 * 60\n", "x") == 5400.0
    with pytest.raises(LookupError):
        _literal_seconds("static let x: TimeInterval = other * 60\n", "x")
    with pytest.raises(LookupError):
        _block("func b() {}", "func a(")
    for path in _PATHS.values():
        assert path.exists(), f"{path} moved — every scan above would silently fail to anchor"
