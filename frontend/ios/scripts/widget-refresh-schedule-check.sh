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
# Two properties, and BOTH matter:
#
#   1. The next refresh is always STRICTLY AFTER now. A date in the past makes
#      WidgetKit reload immediately, in a loop, until the day's allowance is gone —
#      a fix that would look like the original bug within an hour.
#   2. The daily REQUEST COUNT stays inside WidgetKit's allowance (a few dozen).
#      Asking for more does not get more; it gets throttled, and the tile can end up
#      staler than a modest cadence would have left it. The count is asserted here
#      because it is the actual design constraint and it is invisible at any single
#      call site.
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

// 2026-08-26 is a Wednesday; 2026-08-28 a Friday; 08-29/30 the weekend.
print("— regular session: 20 minutes —")
check("mid-session",            next("2026-08-26 10:00"), "2026-08-26 10:20")
check("just after the open",    next("2026-08-26 09:31"), "2026-08-26 09:51")
check("just before the close",  next("2026-08-26 15:50"), "2026-08-26 16:10")

print("— extended hours: 60 minutes —")
check("pre-market",             next("2026-08-26 05:00"), "2026-08-26 06:00")
check("after-hours",            next("2026-08-26 17:00"), "2026-08-26 18:00")

print("— never sleep through the opening bell —")
check("08:45 stops at 09:30, not 09:45", next("2026-08-26 08:45"), "2026-08-26 09:30")

print("— overnight and weekends go quiet until pre-market —")
check("after 20:00 → next weekday 04:00", next("2026-08-26 21:00"), "2026-08-27 04:00")
check("before 04:00 → today 04:00",       next("2026-08-26 02:00"), "2026-08-26 04:00")
check("Friday night → MONDAY, not Saturday", next("2026-08-28 21:00"), "2026-08-31 04:00")
check("Saturday      → Monday",           next("2026-08-29 12:00"), "2026-08-31 04:00")
check("Sunday        → Monday",           next("2026-08-30 12:00"), "2026-08-31 04:00")

print("— property 1: always strictly in the future —")
var probe = d("2026-08-28 00:00")
var everBackwards = false
for _ in 0..<(60 * 24 * 4) {          // every minute for four days, incl. the weekend
    if WidgetRefreshSchedule.nextRefresh(after: probe) <= probe { everBackwards = true; break }
    probe = probe.addingTimeInterval(60)
}
expect("no minute of four days yields a past date", !everBackwards,
       "a past date makes WidgetKit reload in a loop until the allowance is gone")

print("— property 2: the daily request count fits the allowance —")
var cursor = d("2026-08-26 00:00")
let dayEnd = d("2026-08-27 00:00")
var requests = 0
while cursor < dayEnd && requests < 500 {
    cursor = WidgetRefreshSchedule.nextRefresh(after: cursor)
    if cursor < dayEnd { requests += 1 }
}
print("        a full Wednesday asks for \(requests) refreshes")
expect("a trading day stays under 40 requests", requests < 40,
       "asked for \(requests); WidgetKit throttles past its allowance and the tile gets STALER")
expect("a trading day asks for at least 20", requests >= 20,
       "asked for only \(requests) — the session would feel frozen")

if failures == 0 { print("\nall assertions hold") } else { print("\n\(failures) FAILED") }
exit(failures == 0 ? 0 : 1)
SWIFT

swiftc -O -o "$WORK/harness" "$WORK/main.swift" "$SRC"
"$WORK/harness"
