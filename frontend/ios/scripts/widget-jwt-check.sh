#!/usr/bin/env bash
#
# widget-jwt-check.sh — assert the widget reads the right claims out of a JWT.
#
# WHY A STANDALONE swiftc HARNESS
# -------------------------------
# There is NO XCTest target (see .claude/rules/testing.md and the sibling
# `widget-session-label-check.sh`). `xcodebuild build` proves `WidgetJWT` compiles; it
# proves nothing about whether base64url → padding → JSON is right, and a slip there is
# SILENT — every caller treats "could not read it" as a normal answer:
#
#   * `subject` nil  ⇒ every Holdings snapshot is stored with owner nil ⇒ every launch
#     looks like an account switch: the Holdings tile is blanked and refreshed again, on
#     every cold launch, with nothing logged above a warning;
#   * `expiry` nil   ⇒ the app cannot tell its widget token is still good and mints a new,
#     unrevocable 90-day token on every launch.
#
# WHAT IT GUARDS
#   1. The three payload lengths a base64url segment can have (≡ 0, 2, 3 mod 4) — the
#      padding arithmetic — and the impossible one (≡ 1) is refused.
#   2. The two URL-safe characters ('-' and '_') are translated back.
#   3. Anything that is not a three-segment token with a JSON-object payload is "unknown"
#      (nil), never a crash and never a half-read claim.
#   4. `exp` must be a real number (not a string, not a JSON boolean, not ≤ 0); `sub` a
#      non-empty string.
#   5. A REAL token minted by the backend's own `create_widget_token` (signed with a dummy
#      key — the signature is never checked here) decodes to the claims it was minted with.
#      `backend/tests/test_ios_widget_self_refresh.py` re-mints one and checks the backend
#      still issues the claim names and types this fixture pins.
#
# It compiles the REAL source file and nothing else (it is dependency-free on purpose), so
# a signature change fails the harness rather than letting it drift.
#
# 🔴 Machine safety (CLAUDE.md): a `swiftc` compile — run it from the main session only.
#
# Usage:  ./frontend/ios/scripts/widget-jwt-check.sh
# Exit 0 = all assertions hold.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
SRC="$ROOT/frontend/ios/Shared/WidgetJWT.swift"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

[ -f "$SRC" ] || { echo "missing $SRC"; exit 1; }

cat > "$WORK/main.swift" <<'SWIFT'
import Foundation

var failures = 0
func check(_ label: String, _ got: String, _ want: String) {
    if got != want { failures += 1 }
    print("\(got == want ? "  ok" : "FAIL")  \(label)")
    if got != want { print("        got=\"\(got)\"  want=\"\(want)\"") }
}
func subjectOf(_ token: String) -> String { WidgetJWT.subject(of: token) ?? "<nil>" }
func expiryOf(_ token: String) -> String {
    guard let date = WidgetJWT.expiry(of: token) else { return "<nil>" }
    return String(format: "%.1f", date.timeIntervalSince1970)
}
func readable(_ token: String) -> String { WidgetJWT.claims(of: token) == nil ? "nil" : "claims" }

// The header and signature are never read; any segment will do.
let h = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
func token(_ payload: String) -> String { "\(h).\(payload).sig" }

print("— the three payload lengths base64url can have —")
// {"sub":"uuu","exp":1798598977}  — 40 chars, ≡ 0 mod 4 (no padding)
let rem0 = token("eyJzdWIiOiJ1dXUiLCJleHAiOjE3OTg1OTg5Nzd9")
check("≡ 0 mod 4 → sub", subjectOf(rem0), "uuu")
check("≡ 0 mod 4 → exp", expiryOf(rem0), "1798598977.0")
// {"sub":"u","exp":1798598977}  — ≡ 2 mod 4 (two '=' restored)
let rem2 = token("eyJzdWIiOiJ1IiwiZXhwIjoxNzk4NTk4OTc3fQ")
check("≡ 2 mod 4 → sub", subjectOf(rem2), "u")
check("≡ 2 mod 4 → exp", expiryOf(rem2), "1798598977.0")
// {"sub":"uu","exp":1798598977}  — ≡ 3 mod 4 (one '=' restored)
let rem3 = token("eyJzdWIiOiJ1dSIsImV4cCI6MTc5ODU5ODk3N30")
check("≡ 3 mod 4 → sub", subjectOf(rem3), "uu")
check("≡ 3 mod 4 → exp", expiryOf(rem3), "1798598977.0")
// ≡ 1 mod 4 cannot be base64 at all.
check("≡ 1 mod 4 → unknown", readable(token("eyJzd")), "nil")

print("— the URL-safe alphabet —")
// {"sub":"?ab>cd",…} encodes to a payload holding both '_' and '-'.
let urlSafe = token("eyJzdWIiOiI_YWI-Y2QiLCJleHAiOjE3OTg1OTg5Nzd9")
check("'_' and '-' are translated back (≡ 0)", subjectOf(urlSafe), "?ab>cd")
let urlSafe3 = token("eyJzdWIiOiI_YWI-YyIsImV4cCI6MTc5ODU5ODk3N30")
check("'_' and '-' are translated back (≡ 3)", subjectOf(urlSafe3), "?ab>c")
check("…and exp beside them", expiryOf(urlSafe3), "1798598977.0")

print("— anything else is UNKNOWN, never a guess —")
check("empty string", readable(""), "nil")
check("two segments", readable("\(h).eyJzdWIiOiJ1dXUiLCJleHAiOjE3OTg1OTg5Nzd9"), "nil")
check("four segments", readable("\(h).eyJzdWIiOiJ1dXUiLCJleHAiOjE3OTg1OTg5Nzd9.sig.extra"), "nil")
check("a payload that is not base64", readable(token("eyJ!!!")), "nil")
check("a payload that is not JSON", readable(token("bm90IGpzb24gYXQgYWxs")), "nil")
check("a JSON array, not an object", readable(token("WyJzdWIiLCJleHAiXQ")), "nil")
check("…and neither claim is read from it", subjectOf(token("WyJzdWIiLCJleHAiXQ")) + "|" + expiryOf(token("WyJzdWIiLCJleHAiXQ")), "<nil>|<nil>")

print("— each claim on its own —")
check("no sub → nil sub", subjectOf(token("eyJleHAiOjE3OTg1OTg5Nzd9")), "<nil>")
check("…but its exp still reads", expiryOf(token("eyJleHAiOjE3OTg1OTg5Nzd9")), "1798598977.0")
check("no exp → nil exp", expiryOf(token("eyJzdWIiOiJhYmMifQ")), "<nil>")
check("…but its sub still reads", subjectOf(token("eyJzdWIiOiJhYmMifQ")), "abc")
check("exp as a STRING → nil", expiryOf(token("eyJzdWIiOiJhYmMiLCJleHAiOiIxNzk4NTk4OTc3In0")), "<nil>")
check("exp as a JSON BOOLEAN → nil, not 1970", expiryOf(token("eyJzdWIiOiJhYmMiLCJleHAiOnRydWV9")), "<nil>")
check("exp negative → nil", expiryOf(token("eyJzdWIiOiJhYmMiLCJleHAiOi01fQ")), "<nil>")
check("exp fractional → kept", expiryOf(token("eyJzdWIiOiJhYmMiLCJleHAiOjE3OTg1OTg5NzcuNX0")), "1798598977.5")
check("sub as a NUMBER → nil", subjectOf(token("eyJzdWIiOjEyMywiZXhwIjoxNzk4NTk4OTc3fQ")), "<nil>")
check("sub empty → nil", subjectOf(token("eyJzdWIiOiIiLCJleHAiOjE3OTg1OTg5Nzd9")), "<nil>")

print("— a real widget token, minted by the backend's create_widget_token —")
// Signed with a DUMMY key ("dummy-harness-key-not-a-secret"), never the real SECRET_KEY: the
// signature is not checked here, and this file is committed. Claims: sub, type "widget",
// scope "widget:market", iat 1790822977, exp 1798598977 (90 days later).
let real = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIzZjJiOGMxZS03YTRkLTRlMGItOWM1NS0wZDFlMmYzYTRiNWMiLCJ0eXBlIjoid2lkZ2V0Iiwic2NvcGUiOiJ3aWRnZXQ6bWFya2V0IiwiaWF0IjoxNzkwODIyOTc3LCJleHAiOjE3OTg1OTg5Nzd9.SIUAGeTKmc9Q9WDVIkn4ikt2JLaF13ti1k_v8qlLNMA"
check("sub is the user id", subjectOf(real), "3f2b8c1e-7a4d-4e0b-9c55-0d1e2f3a4b5c")
check("exp is the 90-day expiry", expiryOf(real), "1798598977.0")
check("the other claims are there too",
      (WidgetJWT.claims(of: real)?["type"] as? String ?? "<nil>") + "|"
        + (WidgetJWT.claims(of: real)?["scope"] as? String ?? "<nil>"),
      "widget|widget:market")

print(failures == 0 ? "\nALL PASS" : "\n\(failures) FAILURE(S)")
exit(failures == 0 ? 0 : 1)
SWIFT

swiftc -O -o "$WORK/harness" "$WORK/main.swift" "$SRC"
"$WORK/harness"
