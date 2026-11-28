#!/usr/bin/env bash
#
# widget-refresh-schedule-check.sh — assert the widget asks to be woken at sane times.
#
# WHY A STANDALONE swiftc HARNESS
# -------------------------------
# There is NO XCTest target (see .claude/rules/testing.md and the sibling
# `widget-session-label-check.sh`). `xcodebuild build` proves this compiles; it proves
# nothing about WHEN the tile asks to be refreshed — and that is date arithmetic across
# weekends, session boundaries and the opening bell, which is exactly the kind of code
# that is wrong in one branch and silent about it.
#
# WHAT IT GUARDS
# --------------
# The shipped bug this replaces: `Timeline(policy: .after(next))` where `next` was the
# NEXT 00:01, i.e. WidgetKit was told not to ask again for the rest of the day. The tile
# could not update even in principle between two app launches.
#
# Three properties, and ALL matter:
#
#   1. The next refresh is always STRICTLY AFTER now, and never more than the 90-minute
#      quiet step after it. A date in the past makes WidgetKit reload immediately, in a
#      loop, until the day's allowance is gone — a fix that would look like the original
#      bug within an hour. A date far ahead is the original bug itself.
#   2. The daily REQUEST COUNT stays inside WidgetKit's allowance (a few dozen; Apple
#      says 40-70 for a widget looked at often). Asking for more does not get more; it
#      gets throttled, and the tile can end up staler than a modest cadence would have
#      left it. The count is asserted here — 35 on a trading day, 15 on a weekend day,
#      16 on a holiday, 27 on a half day — because it is the actual design constraint
#      and it is invisible at any single call site.
#   3. Every timeline carries the next ET 00:01, even past its reload. With a 90-minute
#      quiet reload no evening timeline before 22:31 reaches midnight, so one deferred
#      reload used to leave yesterday's evening render up after 00:00 as today's.
#
# 2026-11-27 (owner: "Every 90 min (Recommended)"): the quiet hours — overnight, the
# weekend, and the rest of a day the SERVER has called closed (a holiday, a half day
# after 13:00) — reload every 90 minutes instead of sleeping to the next weekday's
# 04:00 ET. The extension fetches Holdings itself now, and a sleeping tile showed
# Friday evening's portfolio until Monday. The rows below that changed say so.
#
# It compiles the REAL source file, so a signature change fails the harness rather than
# letting it drift.
#
# Usage:  ./frontend/ios/scripts/widget-refresh-schedule-check.sh
# Exit 0 = all assertions hold.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
SRC="$ROOT/frontend/ios/Shared/WidgetRefreshSchedule.swift"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

[ -f "$SRC" ] || { echo "missing $SRC"; exit 1; }

cat > "$WORK/main.swift" <<'SWIFT'
import Foundation

let et = TimeZone(identifier: "America/New_York")!
func d(_ s: String) -> Date {
    let f = DateFormatter()
    f.locale = Locale(identifier: "en_US_POSIX"); f.timeZone = et
    f.dateFormat = "yyyy-MM-dd HH:mm"
    return f.date(from: s)!
}
func show(_ date: Date) -> String {
    let f = DateFormatter()
    f.locale = Locale(identifier: "en_US_POSIX"); f.timeZone = et
    f.dateFormat = "yyyy-MM-dd HH:mm"
    return f.string(from: date)
}

var failures = 0
func check(_ label: String, _ got: String, _ want: String) {
    if got != want { failures += 1 }
    print("\(got == want ? "  ok" : "FAIL")  \(label)")
    if got != want { print("        got=\"\(got)\"  want=\"\(want)\"") }
}
func expect(_ label: String, _ condition: Bool, _ detail: String = "") {
    if !condition { failures += 1 }
    print("\(condition ? "  ok" : "FAIL")  \(label)")
    if !condition && !detail.isEmpty { print("        \(detail)") }
}

func next(_ s: String) -> String { show(WidgetRefreshSchedule.nextRefresh(after: d(s))) }
/// With the payload's phase hint: `phase` is its `market_session`, `built` its `as_of`.
func nextH(_ s: String, _ phase: String?, built: String?) -> String {
    show(WidgetRefreshSchedule.nextRefresh(after: d(s), marketSession: phase, builtAt: built.map(d)))
}

// 2026-08-26 is a Wednesday; 2026-08-28 a Friday; 08-29/30 the weekend; 08-31 a Monday.
print("— regular session: 20 minutes —")
check("mid-session",            next("2026-08-26 10:00"), "2026-08-26 10:20")
check("just after the open",    next("2026-08-26 09:31"), "2026-08-26 09:51")
check("just before the close",  next("2026-08-26 15:50"), "2026-08-26 16:10")

print("— extended hours: 60 minutes —")
check("pre-market",             next("2026-08-26 05:00"), "2026-08-26 06:00")
check("after-hours",            next("2026-08-26 17:00"), "2026-08-26 18:00")

print("— never sleep through the opening bell —")
check("08:45 stops at 09:30, not 09:45", next("2026-08-26 08:45"), "2026-08-26 09:30")

// FLIPPED 2026-11-27 (owner: "Every 90 min (Recommended)"). These used to read "next weekday
// 04:00": the night and the whole weekend slept. Do not flip them back — the extension fetches
// Holdings itself now, and a sleeping tile held Friday evening's portfolio until Monday.
print("— overnight and weekends: every 90 minutes —")
check("after 20:00 → +90",                next("2026-08-26 21:00"), "2026-08-26 22:30")
check("before 04:00 → +90",               next("2026-08-26 02:00"), "2026-08-26 03:30")
check("Friday night → +90, not Monday",   next("2026-08-28 21:00"), "2026-08-28 22:30")
check("Saturday      → +90",              next("2026-08-29 12:00"), "2026-08-29 13:30")
check("Sunday        → +90",              next("2026-08-30 12:00"), "2026-08-30 13:30")

print("— …but never sleep through a weekday's 04:00 pre-market open —")
check("03:00 stops at 04:00, not 04:30",  next("2026-08-26 03:00"), "2026-08-26 04:00")
check("Monday 03:30 → 04:00",             next("2026-08-31 03:30"), "2026-08-31 04:00")
check("Monday 02:45 → 04:00, not 04:15",  next("2026-08-31 02:45"), "2026-08-31 04:00")
check("Sunday 23:00 → Monday 00:30",      next("2026-08-30 23:00"), "2026-08-31 00:30")
check("Saturday 03:00 → 04:30 (a weekend 04:00 opens nothing)",
      next("2026-08-29 03:00"), "2026-08-29 04:30")

// THE SERVER ENDS THE DAY. There is no client holiday table (see the source's header). A
// payload built on a weekday between 04:00 and 20:00 ET whose phase is exactly "closed" is a
// holiday or a half day after 13:00, and the rest of that ET day goes quiet.
// 2026-11-26 is Thanksgiving (closed); 2026-11-27 the half day (13:00 close).
print("— closed for the day, on the server's word —")
check("Thanksgiving 05:00, built 05:00 → +90",
      nextH("2026-11-26 05:00", "closed", built: "2026-11-26 05:00"), "2026-11-26 06:30")
check("Thanksgiving 10:00 → +90, not +20",
      nextH("2026-11-26 10:00", "closed", built: "2026-11-26 10:00"), "2026-11-26 11:30")
check("Thanksgiving 08:45 → +90: no bell to wait for",
      nextH("2026-11-26 08:45", "closed", built: "2026-11-26 08:45"), "2026-11-26 10:15")
check("half day 13:10, closed → +90",
      nextH("2026-11-27 13:10", "closed", built: "2026-11-27 13:10"), "2026-11-27 14:40")
check("a 03:59 'closed' (every weekday is) read at 04:00 → ignored",
      nextH("2026-08-26 04:00", "closed", built: "2026-08-26 03:59"), "2026-08-26 05:00")
check("yesterday's closed build → ignored",
      nextH("2026-08-26 10:00", "closed", built: "2026-08-25 10:00"), "2026-08-26 10:20")
check("phase 'CLOSED' (not the wire's value) → ignored",
      nextH("2026-08-26 10:00", "CLOSED", built: "2026-08-26 10:00"), "2026-08-26 10:20")
check("phase '' → ignored",
      nextH("2026-08-26 10:00", "", built: "2026-08-26 10:00"), "2026-08-26 10:20")
check("no phase → ignored",
      nextH("2026-08-26 10:00", nil, built: "2026-08-26 10:00"), "2026-08-26 10:20")
check("closed with no build time → ignored",
      nextH("2026-08-26 10:00", "closed", built: nil), "2026-08-26 10:20")
check("a regular phase changes nothing",
      nextH("2026-08-26 10:00", "regular", built: "2026-08-26 10:00"), "2026-08-26 10:20")
// `now` is read BEFORE the fetch, so a fresh build is usually stamped a few seconds after it.
check("built 4 min after now → honoured",
      nextH("2026-08-26 10:00", "closed", built: "2026-08-26 10:04"), "2026-08-26 11:30")
check("built exactly 5 min after now → honoured",
      nextH("2026-08-26 10:00", "closed", built: "2026-08-26 10:05"), "2026-08-26 11:30")
check("built 10 min after now → ignored (a skewed clock decides nothing)",
      nextH("2026-08-26 10:00", "closed", built: "2026-08-26 10:10"), "2026-08-26 10:20")
check("20:30 closed → +90 (quiet anyway)",
      nextH("2026-08-26 20:30", "closed", built: "2026-08-26 20:30"), "2026-08-26 22:00")

// A stand-in for the backend's `market_hours.session_phase`, for the days these walks cover.
// It lives ONLY in this harness, to feed the hint the way a real payload would; the app has
// no holiday table and must never grow one.
let simulatedHolidays: Set<String> = ["2026-11-26"]
let simulatedHalfDays: Set<String> = ["2026-11-27"]
func serverPhase(_ date: Date) -> String {
    var cal = Calendar(identifier: .gregorian)
    cal.timeZone = et
    let c = cal.dateComponents([.hour, .minute, .weekday], from: date)
    let f = DateFormatter()
    f.locale = Locale(identifier: "en_US_POSIX"); f.timeZone = et
    f.dateFormat = "yyyy-MM-dd"
    let ymd = f.string(from: date)
    let minute = (c.hour ?? 0) * 60 + (c.minute ?? 0)
    if c.weekday == 1 || c.weekday == 7 || simulatedHolidays.contains(ymd) { return "closed" }
    if minute < 4 * 60 || minute >= 20 * 60 { return "closed" }
    if simulatedHalfDays.contains(ymd) && minute >= 13 * 60 { return "closed" }
    if minute < 9 * 60 + 30 { return "premarket" }
    if minute < 16 * 60 { return "regular" }
    return "afterhours"
}

print("— property 1: strictly in the future, never more than 90 minutes ahead —")
func walkFourDays(_ label: String, _ from: String, hint: (Date) -> (String?, Date?)) {
    var probe = d(from)
    var broken: String? = nil
    for _ in 0..<(60 * 24 * 4) {          // every minute for four days, incl. a weekend
        let (phase, built) = hint(probe)
        let reload = WidgetRefreshSchedule.nextRefresh(after: probe, marketSession: phase, builtAt: built)
        if reload <= probe {
            broken = "a past date at \(show(probe)) — WidgetKit reloads in a loop until the allowance is gone"
            break
        }
        if reload > probe.addingTimeInterval(WidgetRefreshSchedule.quietInterval) {
            broken = "\(show(reload)) is more than 90 minutes after \(show(probe)) — the tile sleeps"
            break
        }
        probe = probe.addingTimeInterval(60)
    }
    expect("\(label): every minute of four days", broken == nil, broken ?? "")
}
walkFourDays("no hint", "2026-08-28 00:00") { _ in (nil, nil) }
walkFourDays("closed, built now", "2026-08-28 00:00") { ("closed", $0) }
walkFourDays("closed, built 5 min ahead", "2026-08-28 00:00") { ("closed", $0.addingTimeInterval(5 * 60)) }
walkFourDays("closed, built 3 h before", "2026-08-28 00:00") { ("closed", $0.addingTimeInterval(-3 * 3600)) }
walkFourDays("the server's phase, Thanksgiving week", "2026-11-25 00:00") { (serverPhase($0), $0) }

print("— property 2: the daily request count fits the allowance —")
/// The reloads one ET day asks for. `hinted` feeds every build the server's phase at the instant
/// it was built, exactly as the provider does with a fresh payload.
func dailyRequests(_ day: String, _ nextDay: String, hinted: Bool) -> Int {
    var cursor = d("\(day) 00:00")
    let dayEnd = d("\(nextDay) 00:00")
    var count = 0
    while cursor < dayEnd && count < 500 {
        cursor = hinted
            ? WidgetRefreshSchedule.nextRefresh(after: cursor, marketSession: serverPhase(cursor), builtAt: cursor)
            : WidgetRefreshSchedule.nextRefresh(after: cursor)
        if cursor < dayEnd { count += 1 }
    }
    return count
}
let tradingDay = dailyRequests("2026-08-26", "2026-08-27", hinted: false)
print("        a full Wednesday asks for \(tradingDay) refreshes")
expect("a trading day stays under 40 requests", tradingDay < 40,
       "asked for \(tradingDay); WidgetKit throttles past its allowance and the tile gets STALER")
expect("a trading day asks for at least 20", tradingDay >= 20,
       "asked for only \(tradingDay) — the session would feel frozen")
check("a trading day: exactly 35 (20 regular + 10 extended + 5 quiet)", "\(tradingDay)", "35")
check("…the same with the server's phase fed back", "\(dailyRequests("2026-08-26", "2026-08-27", hinted: true))", "35")
let saturday = dailyRequests("2026-08-29", "2026-08-30", hinted: true)
expect("a weekend day stays at or under 20", saturday <= 20, "asked for \(saturday)")
check("a weekend day: exactly 15", "\(saturday)", "15")
let holiday = dailyRequests("2026-11-26", "2026-11-27", hinted: true)
expect("a holiday stays at or under 20", holiday <= 20, "asked for \(holiday)")
check("Thanksgiving, on the server's word: exactly 16", "\(holiday)", "16")
check("…and 35 without it: the hint is what saves the 19",
      "\(dailyRequests("2026-11-26", "2026-11-27", hinted: false))", "35")
let halfDay = dailyRequests("2026-11-27", "2026-11-28", hinted: true)
expect("a half day stays at or under 30", halfDay <= 30, "asked for \(halfDay)")
check("the 13:00 half day: exactly 27", "\(halfDay)", "27")

// THE ENTRY DATES, and the bug they fix: the day-rollover render used `Calendar.current`,
// so for a device outside US Eastern it landed at LOCAL 00:01. In UTC+7 that is after the
// 04:00 ET reload, so it was dropped, and from ET midnight to 04:00 the tile showed
// Tuesday's move with no "Tue close". This binary runs once per device zone (see the loop
// at the bottom of the script); every expectation below is in ET and must hold in all.
//
// 2026-11-27: the rollover is now ALWAYS an entry, even past the reload. With the 90-minute
// quiet reload these rows lost their +180 render, and a timeline built before 22:31 no longer
// reaches midnight — one deferred reload left yesterday's evening render up after 00:00 as
// today's. Keep 00:01 in every row.
print("— render dates: the rollover is ET 00:01, whatever the device zone (\(TimeZone.current.identifier)) —")
func renders(_ s: String) -> String {
    let now = d(s)
    let reload = WidgetRefreshSchedule.nextRefresh(after: now)
    return WidgetRefreshSchedule.renderDates(now: now, reload: reload).map(show).joined(separator: ",")
}
check("Tue 20:30 → +20/+60 before the 22:00 reload, then ET midnight past it",
      renders("2026-08-25 20:30"),
      "2026-08-25 20:30,2026-08-25 20:50,2026-08-25 21:30,2026-08-26 00:01")
check("Fri 20:30 → Saturday 00:01 ET, past the 22:00 reload",
      renders("2026-08-28 20:30"),
      "2026-08-28 20:30,2026-08-28 20:50,2026-08-28 21:30,2026-08-29 00:01")
check("Saturday noon → Sunday 00:01 ET",
      renders("2026-08-29 12:00"),
      "2026-08-29 12:00,2026-08-29 12:20,2026-08-29 13:00,2026-08-30 00:01")
check("Tue 23:00 → midnight AND 00:01, both before the 00:30 reload",
      renders("2026-08-25 23:00"),
      "2026-08-25 23:00,2026-08-25 23:20,2026-08-26 00:00,2026-08-26 00:01")
// The age-boundary render ("As of" at asOf + 45 min, or + 2 h off hours) is NOT this
// function's: the provider adds it once the snapshot is known — `WidgetSessionLabel.ageBoundary`,
// asserted in widget-session-label-check.sh.
check("mid-session → just now, and tonight's rollover in case the reload is deferred all day",
      renders("2026-08-26 10:00"), "2026-08-26 10:00,2026-08-27 00:01")
check("02:00 → +20/+60 before the 03:30 reload, then the NEXT ET 00:01",
      renders("2026-08-26 02:00"),
      "2026-08-26 02:00,2026-08-26 02:20,2026-08-26 03:00,2026-08-27 00:01")

var renderProbe = d("2026-08-27 00:00")
var renderBroken: String? = nil
var etCal = Calendar(identifier: .gregorian)
etCal.timeZone = et
for _ in 0..<(4 * 24 * 60 / 7) {             // every 7 minutes for four days, incl. a weekend
    let reload = WidgetRefreshSchedule.nextRefresh(after: renderProbe)
    let dates = WidgetRefreshSchedule.renderDates(now: renderProbe, reload: reload)
    if dates.first != renderProbe { renderBroken = "first entry is not now at \(show(renderProbe))"; break }
    if zip(dates, dates.dropFirst()).contains(where: { pair in pair.0 >= pair.1 }) {
        renderBroken = "entries not strictly increasing at \(show(renderProbe))"; break
    }
    guard let rollover = etCal.nextDate(after: renderProbe, matching: DateComponents(hour: 0, minute: 1),
                                        matchingPolicy: .nextTime) else {
        renderBroken = "no next ET 00:01 after \(show(renderProbe))"; break
    }
    if !dates.contains(rollover) {
        renderBroken = "ET 00:01 missing at \(show(renderProbe))"; break
    }
    // Relaxed for the rollover ONLY: it is kept past the reload on purpose. Anything else past
    // the reload is dead weight a reload that runs would replace.
    if dates.dropFirst().contains(where: { $0 >= reload && $0 != rollover }) {
        renderBroken = "an entry other than the rollover at or past the reload at \(show(renderProbe))"; break
    }
    renderProbe = renderProbe.addingTimeInterval(7 * 60)
}
expect("every timeline: starts now, increasing, before the reload except the ET 00:01 it always carries",
       renderBroken == nil, renderBroken ?? "")

if failures == 0 { print("\nall assertions hold") } else { print("\n\(failures) FAILED") }
exit(failures == 0 ? 0 : 1)
SWIFT

swiftc -O -o "$WORK/harness" "$WORK/main.swift" "$SRC"
# Every assertion is in ET and must hold whatever zone the DEVICE is in — the rollover bug
# only existed outside US Eastern. `set -e` stops at the first zone that fails.
for tz in America/New_York Asia/Ho_Chi_Minh Europe/Berlin America/Los_Angeles; do
    echo "── device time zone: $tz"
    TZ="$tz" "$WORK/harness"
done
