#!/usr/bin/env bash
#
# widget-session-label-check.sh — assert the widget names the right trading session.
#
# WHY A STANDALONE swiftc HARNESS
# -------------------------------
# There is NO XCTest target in this project (see .claude/rules/testing.md, and the
# sibling `sparkline-geometry-check.sh`). `xcodebuild build` proves this code
# compiles; it proves nothing about what the tile SAYS.
#
# And this particular failure is invisible to manual testing. Looking at the widget
# on a Tuesday afternoon tells you nothing — to see the bug by hand you have to open
# the app on a Friday, not touch it all weekend, and look again on Sunday.
#
# WHAT IT GUARDS
# --------------
# The widget extension cannot fetch. The app refreshes on cold launch, foreground and
# auth transition only, so between two app opens the SAME bytes re-render
# indefinitely. Any freshness wording baked in at WRITE time is therefore a claim
# that decays with nothing to update it — which is exactly what shipped:
#
#   * `market_session` is captured when the snapshot is written, so a Friday 18:00
#     write rendered "After hours" all weekend, and
#   * a write during regular hours rendered an EMPTY footer forever, so a Friday
#     −5.02% sat on a Monday Home Screen with NO time cue at all.
#
# `WidgetSessionLabel` fixes it structurally by deriving the label from
# `session_date` at RENDER time. The date does not decay, and the multi-entry
# timeline re-evaluates it — so the tile ages its own label with no network, no
# background task, and no flag anyone has to keep true.
#
# THE LOAD-BEARING PROPERTIES
#   1. A snapshot from a previous session NAMES THAT DAY ("Fri close"), on every
#      later day, regardless of what the phase says now.
#   2. An OLD backend (no `session_date`) behaves EXACTLY as before — this ships in
#      an app update while the backend deploys independently, and a regression here
#      would be a silent behaviour change for every user on the old payload.
#
# It compiles the REAL source file (not a copy), so a signature change fails the
# harness rather than letting it drift silently.
#
# Usage:  ./frontend/ios/scripts/widget-session-label-check.sh
# Exit 0 = all assertions hold.

set -euo pipefail

# scripts/ → ios/ → frontend/ → repo root
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
SRC="$ROOT/frontend/ios/Shared/WidgetSessionLabel.swift"
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

var failures = 0
func check(_ label: String, _ got: String, _ want: String) {
    if got != want { failures += 1 }
    print("\(got == want ? "  ok" : "FAIL")  \(label)")
    if got != want { print("        got=\"\(got)\"  want=\"\(want)\"") }
}

// The snapshot under test: written Friday 2026-08-14 at 15:58 ET, mid-session.
// Every case below re-reads THAT SAME snapshot at a different `now`.
let asOf = d("2026-08-14 15:58")
func label(_ now: Date, sessionDate: String? = "2026-08-14",
           phase: String = "regular", server: String? = "Live 3:58 PM ET") -> String {
    WidgetSessionLabel.displayLabel(
        asOf: asOf, sessionDate: sessionDate, marketSession: phase,
        sessionLabel: server, now: now
    )
}

print("— live and decaying —")
check("read immediately → the server's own words",
      label(d("2026-08-14 15:59")), "Live 3:58 PM ET")
check("40 min later → 'Live' is no longer true, the instant still is",
      label(d("2026-08-14 16:38")), "As of 3:58 PM ET")

print("— a previous session names its day (the bug) —")
check("read on Saturday",  label(d("2026-08-15 11:00")), "Fri close")
check("read on Sunday",    label(d("2026-08-16 11:00")), "Fri close")
// The phase on Monday at 08:00 is `premarket`; a phase-only reading called
// Friday's close "Pre-market" and said nothing about the date.
check("read Monday pre-market", label(d("2026-08-17 08:00")), "Fri close")
check("a week later → asks for a refresh rather than naming an ambiguous weekday",
      label(d("2026-08-24 11:00")), "Aug 14 — open Caydex")

print("— same-day phases —")
check("closed, same day",
      WidgetSessionLabel.displayLabel(asOf: d("2026-08-14 21:00"),
        sessionDate: "2026-08-14", marketSession: "closed",
        sessionLabel: "Fri close", now: d("2026-08-14 21:05")), "At the close")
check("pre-market passes through",
      WidgetSessionLabel.displayLabel(asOf: d("2026-08-14 07:31"),
        sessionDate: "2026-08-14", marketSession: "premarket",
        sessionLabel: "Pre-market 7:31 AM ET", now: d("2026-08-14 07:40")),
      "Pre-market 7:31 AM ET")
check("after-hours passes through",
      WidgetSessionLabel.displayLabel(asOf: d("2026-08-14 17:02"),
        sessionDate: "2026-08-14", marketSession: "afterhours",
        sessionLabel: "After hours 5:02 PM ET", now: d("2026-08-14 17:10")),
      "After hours 5:02 PM ET")

// A new app can run against a backend that has not shipped `session_date` yet.
// These must match the ORIGINAL SessionFooter switch exactly.
print("— old backend: behaviour must be unchanged —")
check("regular → empty, as before",
      label(d("2026-08-16 11:00"), sessionDate: nil, server: nil), "")
check("closed → At the close, as before",
      label(d("2026-08-16 11:00"), sessionDate: nil, phase: "closed", server: nil),
      "At the close")
check("premarket → Pre-market, as before",
      label(d("2026-08-16 11:00"), sessionDate: nil, phase: "premarket", server: nil),
      "Pre-market")
check("afterhours → After hours, as before",
      label(d("2026-08-16 11:00"), sessionDate: nil, phase: "afterhours", server: nil),
      "After hours")
check("a malformed session_date falls back to legacy rather than guessing",
      label(d("2026-08-16 11:00"), sessionDate: "not-a-date", phase: "closed"),
      "At the close")

// `agedLabel` is what the VIEWS call. `displayLabel` is only reachable through it, so
// asserting only the latter would leave the render path untested.
//
// The contract: SILENT for the current session (a "Live 2:14 PM ET" line spends a row of
// a 155pt tile telling the reader something they already assume), and LOUD for anything
// older (where saying nothing presents Friday's move as today's).
print("— agedLabel: silent when current, loud when not —")
func aged(_ now: Date, sessionDate: String? = "2026-08-14",
          phase: String = "regular", server: String? = "Live 3:58 PM ET") -> String? {
    WidgetSessionLabel.agedLabel(
        asOf: asOf, sessionDate: sessionDate, marketSession: phase,
        sessionLabel: server, now: now
    )
}
func checkNil(_ label: String, _ got: String?) {
    if got != nil { failures += 1 }
    print("\(got == nil ? "  ok" : "FAIL")  \(label)")
    if got != nil { print("        got=\"\(got!)\"  want=nil") }
}

checkNil("live, same session → nothing", aged(d("2026-08-14 15:59")))
// 40 minutes old is inside the 45-minute grace that keeps a self-refreshing Market tile
// (20-minute cadence) quiet. Past it, the instant is printed — the intraday case below.
checkNil("40 min later, inside the grace → still nothing", aged(d("2026-08-14 16:38")))
check("46 min later → the instant, not silence",
      aged(d("2026-08-14 16:44")) ?? "<nil>", "As of 3:58 PM ET")
checkNil("that evening, after the close → still today's numbers",
         aged(d("2026-08-14 21:00"), phase: "closed", server: "Fri close"))
check("read on Saturday → speaks up", aged(d("2026-08-15 11:00")) ?? "<nil>", "Fri close")
check("read on Sunday → speaks up", aged(d("2026-08-16 11:00")) ?? "<nil>", "Fri close")
check("read Monday pre-market → speaks up", aged(d("2026-08-17 08:00")) ?? "<nil>", "Fri close")
check("a week later → asks for a refresh",
      aged(d("2026-08-24 11:00")) ?? "<nil>", "Aug 14 — open Caydex")
// An old backend cannot tell us which session the numbers are from. Saying what we know
// beats implying a freshness we have not established.
check("old backend, closed → still warns",
      aged(d("2026-08-16 11:00"), sessionDate: nil, phase: "closed", server: nil) ?? "<nil>",
      "At the close")
checkNil("old backend, regular → nothing to say",
         aged(d("2026-08-16 11:00"), sessionDate: nil, phase: "regular", server: nil))

// THE INTRADAY HOLDINGS SNAPSHOT. Holdings cannot refresh itself; the app writes it on
// foreground only. Opened once at 10:05 on Tuesday and not again, the tile used to show the
// 10:05 numbers with NO footer all day, then call them "Tue close" on Wednesday — an
// intraday −1.2% presented as a close that was really −2.7%.
print("— an intraday snapshot says WHEN, and is never called the close —")
let morning = d("2026-08-11 10:05")          // a Tuesday, regular session
func agedMorning(_ now: Date, sessionDate: String = "2026-08-11",
                 phase: String = "regular") -> String? {
    WidgetSessionLabel.agedLabel(
        asOf: morning, sessionDate: sessionDate, marketSession: phase,
        sessionLabel: "Live 10:05 AM ET", now: now
    )
}
checkNil("15 min later → still current, nothing to say", agedMorning(d("2026-08-11 10:20")))
check("at 15:55 the same day → the instant",
      agedMorning(d("2026-08-11 15:55")) ?? "<nil>", "As of 10:05 AM ET")
check("Wednesday pre-market → the day AND the time, not 'Tue close'",
      agedMorning(d("2026-08-12 06:50")) ?? "<nil>", "Tue 10:05 AM ET")
check("Saturday → still names the time",
      agedMorning(d("2026-08-15 11:00")) ?? "<nil>", "Tue 10:05 AM ET")
check("a week later → the refresh ask is unchanged",
      agedMorning(d("2026-08-18 11:00")) ?? "<nil>", "Aug 11 — open Caydex")
check("displayLabel agrees with agedLabel",
      WidgetSessionLabel.displayLabel(asOf: morning, sessionDate: "2026-08-11",
        marketSession: "regular", sessionLabel: "Live 10:05 AM ET",
        now: d("2026-08-12 06:50")), "Tue 10:05 AM ET")
// A 09:31 build whose quotes are all still stamped with MONDAY'S session: regular phase,
// but the numbers ARE Monday's close. Pairing Monday with 9:31 would be a wrong claim.
check("a regular build stamped with the previous session keeps '<Day> close'",
      WidgetSessionLabel.agedLabel(asOf: d("2026-08-11 09:31"), sessionDate: "2026-08-10",
        marketSession: "regular", sessionLabel: "Mon close", now: d("2026-08-11 15:00")) ?? "<nil>",
      "Mon close")
check("an after-hours build keeps '<Day> close'",
      WidgetSessionLabel.agedLabel(asOf: d("2026-08-11 17:02"), sessionDate: "2026-08-11",
        marketSession: "afterhours", sessionLabel: "After hours 5:02 PM ET",
        now: d("2026-08-12 06:50")) ?? "<nil>",
      "Tue close")
check("a build at 15:58 is the close for every practical purpose",
      aged(d("2026-08-15 11:00")) ?? "<nil>", "Fri close")
// A half-day leaves "regular" at 13:00, so a 12:30 build there is honestly intraday.
check("half-day 12:30 build → the time",
      WidgetSessionLabel.agedLabel(asOf: d("2026-11-27 12:30"), sessionDate: "2026-11-27",
        marketSession: "regular", sessionLabel: "Live 12:30 PM ET",
        now: d("2026-11-28 11:00")) ?? "<nil>",
      "Fri 12:30 PM ET")
checkNil("a pre-market snapshot is not aged within its own day",
         WidgetSessionLabel.agedLabel(asOf: d("2026-08-11 07:31"), sessionDate: "2026-08-11",
           marketSession: "premarket", sessionLabel: "Pre-market 7:31 AM ET",
           now: d("2026-08-11 09:00")))

// `isPriorSession` decides whether the cause may still say "today" — a different question
// from whether the footer speaks (it also speaks for a same-day stale intraday reading).
print("— isPriorSession —")
func yes(_ b: Bool) -> String { b ? "true" : "false" }
check("same day → false",
      yes(WidgetSessionLabel.isPriorSession(sessionDate: "2026-08-11", now: d("2026-08-11 23:59"))), "false")
check("next ET day → true",
      yes(WidgetSessionLabel.isPriorSession(sessionDate: "2026-08-11", now: d("2026-08-12 00:01"))), "true")
check("no session date (old backend) → false",
      yes(WidgetSessionLabel.isPriorSession(sessionDate: nil, now: d("2026-08-12 00:01"))), "false")
check("malformed → false",
      yes(WidgetSessionLabel.isPriorSession(sessionDate: "garbage", now: d("2026-08-12 00:01"))), "false")

func expect(_ label: String, _ condition: Bool, _ detail: String = "") {
    if !condition { failures += 1 }
    print("\(condition ? "  ok" : "FAIL")  \(label)")
    if !condition && !detail.isEmpty { print("        \(detail)") }
}

// A PRE-MARKET build dated TODAY: crypto-only Holdings, or a batch with no session stamps (an
// equity pre-market build is dated the PRIOR session by the backend). Holdings cannot refresh
// itself, so the 08:30 reading used to sit there unlabelled all day, and the next morning was
// called "Tue close" — numbers that were never the close.
print("— a pre-market build dated today ages at the bell, and is never called the close —")
let pre = d("2026-08-11 08:30")                 // a Tuesday
func agedPre(_ now: Date) -> String? {
    WidgetSessionLabel.agedLabel(
        asOf: pre, sessionDate: "2026-08-11", marketSession: "premarket",
        sessionLabel: "Pre-market 8:30 AM ET", now: now
    )
}
checkNil("09:29, before the bell → still the latest reading", agedPre(d("2026-08-11 09:29")))
check("09:30, the bell → the instant", agedPre(d("2026-08-11 09:30")) ?? "<nil>", "As of 8:30 AM ET")
check("15:00 the same day → the instant, not silence",
      agedPre(d("2026-08-11 15:00")) ?? "<nil>", "As of 8:30 AM ET")
check("07:00 the next day → the day AND the time, not 'Tue close'",
      agedPre(d("2026-08-12 07:00")) ?? "<nil>", "Tue 8:30 AM ET")
check("an equity pre-market build dated the PRIOR session keeps '<Day> close'",
      WidgetSessionLabel.agedLabel(asOf: d("2026-08-12 08:30"), sessionDate: "2026-08-11",
        marketSession: "premarket", sessionLabel: "Tue close", now: d("2026-08-12 15:00")) ?? "<nil>",
      "Tue close")

// THE LOCK SCREEN'S INLINE LINE. It sheds clauses until one fits, and used to shed the AGE
// first — a bare "AAPL +2.10%" from last Tuesday. `compactAgedLabel` is the same claim, short
// enough to keep: the decision is shared with `agedLabel`, only the wording differs.
print("— compactAgedLabel: the same claim, shorter —")
func compact(_ asOfS: String, _ sessionDate: String?, _ phase: String, _ nowS: String) -> String {
    WidgetSessionLabel.compactAgedLabel(
        asOf: d(asOfS), sessionDate: sessionDate, marketSession: phase, now: d(nowS)
    ) ?? "<nil>"
}
check("live → nothing, like agedLabel",
      compact("2026-08-14 15:58", "2026-08-14", "regular", "2026-08-14 15:59"), "<nil>")
check("exactly 45 min → still nothing (the rule is strictly past it)",
      compact("2026-08-11 10:05", "2026-08-11", "regular", "2026-08-11 10:50"), "<nil>")
check("46 min → the instant, no ET",
      compact("2026-08-11 10:05", "2026-08-11", "regular", "2026-08-11 10:51"), "As of 10:05")
check("next morning, intraday build → the day and the time",
      compact("2026-08-11 10:05", "2026-08-11", "regular", "2026-08-12 06:50"), "Tue 10:05")
check("a 15:54 build is still intraday",
      compact("2026-08-11 15:54", "2026-08-11", "regular", "2026-08-12 06:50"), "Tue 3:54")
check("a 15:55 build is the close",
      compact("2026-08-11 15:55", "2026-08-11", "regular", "2026-08-12 06:50"), "Tue close")
check("after-hours build → the close",
      compact("2026-08-11 17:02", "2026-08-11", "afterhours", "2026-08-12 06:50"), "Tue close")
check("five days on → still a weekday",
      compact("2026-08-14 15:58", "2026-08-14", "regular", "2026-08-19 11:00"), "Fri close")
check("six days on → the date, without '— open Caydex'",
      compact("2026-08-14 15:58", "2026-08-14", "regular", "2026-08-20 11:00"), "Aug 14")
check("…where the full label still asks for a refresh",
      WidgetSessionLabel.agedLabel(asOf: d("2026-08-14 15:58"), sessionDate: "2026-08-14",
        marketSession: "regular", sessionLabel: nil, now: d("2026-08-20 11:00")) ?? "<nil>",
      "Aug 14 — open Caydex")
// 2026-11-01 is the DST change: the ET wall-clock time of the build must survive it.
check("across the DST change → the build's ET time",
      compact("2026-10-30 10:05", "2026-10-30", "regular", "2026-11-02 07:00"), "Fri 10:05")
check("…and the full label agrees",
      WidgetSessionLabel.agedLabel(asOf: d("2026-10-30 10:05"), sessionDate: "2026-10-30",
        marketSession: "regular", sessionLabel: nil, now: d("2026-11-02 07:00")) ?? "<nil>",
      "Fri 10:05 AM ET")
check("pre-market build after the bell → the instant",
      compact("2026-08-11 08:30", "2026-08-11", "premarket", "2026-08-11 15:00"), "As of 8:30")
check("old backend, closed → the legacy wording, unchanged",
      compact("2026-08-14 21:00", nil, "closed", "2026-08-16 11:00"), "At the close")

// THE PROPERTY THE INLINE LINE RELIES ON: whenever the full label speaks, the compact one
// does too (and is no longer). Otherwise the line could fall back to a bare number.
var disagreement: String? = nil
let compactFixtures: [(String, String?, String)] = [
    ("2026-08-11 10:05", "2026-08-11", "regular"),
    ("2026-08-11 15:58", "2026-08-11", "regular"),
    ("2026-08-11 08:30", "2026-08-11", "premarket"),
    ("2026-08-12 08:30", "2026-08-11", "premarket"),
    ("2026-08-11 17:02", "2026-08-11", "afterhours"),
    ("2026-08-11 21:00", "2026-08-11", "closed"),
    ("2026-08-11 10:05", nil, "regular"),
    ("2026-08-11 21:00", nil, "closed"),
]
for (asOfS, sessionDate, phase) in compactFixtures {
    let built = d(asOfS)
    var probe = built
    for _ in 0..<(8 * 24 * 60 / 13) {           // every 13 minutes for eight days
        let full = WidgetSessionLabel.agedLabel(
            asOf: built, sessionDate: sessionDate, marketSession: phase, sessionLabel: nil, now: probe
        )
        let short = WidgetSessionLabel.compactAgedLabel(
            asOf: built, sessionDate: sessionDate, marketSession: phase, now: probe
        )
        if (full == nil) != (short == nil) {
            disagreement = "\(asOfS) \(phase) at \(probe): full=\(full ?? "nil") compact=\(short ?? "nil")"
            break
        }
        if let full, let short, short.count > full.count {
            disagreement = "compact is longer than full at \(probe): \(short) vs \(full)"
            break
        }
        probe = probe.addingTimeInterval(13 * 60)
    }
    if disagreement != nil { break }
}
expect("compact speaks exactly when the full label does, and is never longer",
       disagreement == nil, disagreement ?? "")

// A ROUND-THE-CLOCK headline's cause ages by the ET day it was BUILT. The payload's
// session_date is the equity session: a Saturday build is stamped Friday.
print("— isPriorETDay —")
check("Saturday build, read Saturday evening → false",
      yes(WidgetSessionLabel.isPriorETDay(asOf: d("2026-08-15 11:00"), now: d("2026-08-15 20:00"))), "false")
check("…where the session date already says 'prior' (why the helper exists)",
      yes(WidgetSessionLabel.isPriorSession(sessionDate: "2026-08-14", now: d("2026-08-15 20:00"))), "true")
check("Saturday build, read Sunday 00:01 ET → true",
      yes(WidgetSessionLabel.isPriorETDay(asOf: d("2026-08-15 11:00"), now: d("2026-08-16 00:01"))), "true")
check("23:59 ET build, read at ET midnight → true",
      yes(WidgetSessionLabel.isPriorETDay(asOf: d("2026-08-15 23:59"), now: d("2026-08-16 00:00"))), "true")
check("a build stamped after now (clock skew) → false",
      yes(WidgetSessionLabel.isPriorETDay(asOf: d("2026-08-16 10:00"), now: d("2026-08-15 10:00"))), "false")

// THE RENDER THAT TURNS THE LABEL ON. During regular hours the timeline holds one entry
// before its 20-minute reload, so without this the "As of" line appeared only at a reload
// built 45+ minutes after the snapshot — late, and indefinitely late if WidgetKit deferred.
print("— ageBoundary —")
func showSeconds(_ date: Date?) -> String {
    guard let date else { return "<nil>" }
    let f = DateFormatter()
    f.locale = Locale(identifier: "en_US_POSIX"); f.timeZone = et
    f.dateFormat = "yyyy-MM-dd HH:mm:ss"
    return f.string(from: date)
}
func boundary(_ asOfS: String, _ sessionDate: String?, _ phase: String, _ nowS: String) -> String {
    showSeconds(WidgetSessionLabel.ageBoundary(
        asOf: d(asOfS), sessionDate: sessionDate, marketSession: phase, now: d(nowS)
    ))
}
check("regular 10:05, now 10:25 → one second past 45 minutes",
      boundary("2026-08-11 10:05", "2026-08-11", "regular", "2026-08-11 10:25"), "2026-08-11 10:50:01")
check("already past it → nil (the label is already on)",
      boundary("2026-08-11 10:05", "2026-08-11", "regular", "2026-08-11 10:55"), "<nil>")
check("pre-market 08:30, now 08:40 → the bell",
      boundary("2026-08-11 08:30", "2026-08-11", "premarket", "2026-08-11 08:40"), "2026-08-11 09:30:00")
check("pre-market, now past the bell → nil",
      boundary("2026-08-11 08:30", "2026-08-11", "premarket", "2026-08-11 09:31"), "<nil>")
check("after-hours → nil (nothing changes wording within the day)",
      boundary("2026-08-11 17:02", "2026-08-11", "afterhours", "2026-08-11 17:10"), "<nil>")
check("closed → nil",
      boundary("2026-08-11 21:00", "2026-08-11", "closed", "2026-08-11 21:05"), "<nil>")
check("a previous session → nil (already aged)",
      boundary("2026-08-11 10:05", "2026-08-11", "regular", "2026-08-12 06:50"), "<nil>")
check("old backend → nil",
      boundary("2026-08-11 10:05", nil, "regular", "2026-08-11 10:25"), "<nil>")
for (asOfS, phase, nowS) in [("2026-08-11 10:05", "regular", "2026-08-11 10:25"),
                             ("2026-08-11 08:30", "premarket", "2026-08-11 08:40")] {
    guard let b = WidgetSessionLabel.ageBoundary(
        asOf: d(asOfS), sessionDate: "2026-08-11", marketSession: phase, now: d(nowS)
    ) else {
        expect("\(phase): a boundary exists", false, "ageBoundary returned nil")
        continue
    }
    let at = WidgetSessionLabel.agedLabel(asOf: d(asOfS), sessionDate: "2026-08-11",
                                          marketSession: phase, sessionLabel: nil, now: b)
    let before = WidgetSessionLabel.agedLabel(asOf: d(asOfS), sessionDate: "2026-08-11",
                                              marketSession: phase, sessionLabel: nil,
                                              now: b.addingTimeInterval(-1))
    expect("\(phase): silent one second before the boundary, speaking at it",
           at != nil && before == nil, "at=\(at ?? "nil") before=\(before ?? "nil")")
}

print(failures == 0 ? "\nALL PASS" : "\n\(failures) FAILURE(S)")
exit(failures == 0 ? 0 : 1)
SWIFT

swiftc -O -o "$WORK/harness" "$WORK/main.swift" "$SRC"
# Every assertion is in ET and must hold whatever zone the DEVICE is in. `set -e` stops at
# the first zone that fails.
for tz in America/New_York Asia/Ho_Chi_Minh Europe/Berlin America/Los_Angeles; do
    echo "── device time zone: $tz"
    TZ="$tz" "$WORK/harness"
done
