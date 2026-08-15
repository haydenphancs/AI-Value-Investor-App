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
checkNil("later the same session → still nothing", aged(d("2026-08-14 16:38")))
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

print(failures == 0 ? "\nALL PASS" : "\n\(failures) FAILURE(S)")
exit(failures == 0 ? 0 : 1)
SWIFT

swiftc -O -o "$WORK/harness" "$WORK/main.swift" "$SRC"
"$WORK/harness"
