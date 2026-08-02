#!/usr/bin/env bash
#
# verify_social_signin.sh — check Google + Apple sign-in config WITHOUT opening the app.
#
# Why: the app surfaces these failures as raw Supabase JSON inside a web sheet, and the two
# most common mistakes (provider disabled / redirect URL not allow-listed) look identical from
# the outside. This probes Supabase directly so you know which step is missing.
#
# Reads nothing secret: only public endpoints and the project ref from the app's Info.plist.
#
#   ./backend/scripts/verify_social_signin.sh
#
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PLIST="$REPO/frontend/ios/ios/Info.plist"

SUPA=$(/usr/libexec/PlistBuddy -c 'Print :SupabaseURL' "$PLIST" 2>/dev/null)
if [ -z "${SUPA:-}" ]; then
  echo "✗ Could not read SupabaseURL from $PLIST"
  exit 1
fi
SUPA="${SUPA%/}"
CALLBACK="caydex://auth-callback"

pass() { printf '  \033[32m✓\033[0m %s\n' "$1"; }
fail() { printf '  \033[31m✗\033[0m %s\n' "$1"; }
info() { printf '    %s\n' "$1"; }

echo
echo "Supabase project: $SUPA"
echo

# ── 1. Google provider enabled? ──────────────────────────────────────────────
# `/authorize` answers 400 `validation_failed` when the provider is off, and 302 to
# accounts.google.com when it is on.
echo "1. Google provider"
body=$(curl -s --max-time 10 "$SUPA/auth/v1/authorize?provider=google&redirect_to=$CALLBACK")
loc=$(curl -s -o /dev/null -w '%{redirect_url}' --max-time 10 \
        "$SUPA/auth/v1/authorize?provider=google&redirect_to=$CALLBACK")

if printf '%s' "$body" | grep -q 'provider is not enabled'; then
  fail "DISABLED — Supabase says 'provider is not enabled'"
  info "Fix: Supabase → Authentication → Sign In / Providers → Google → paste Client ID"
  info "     + Secret from Google Cloud, then toggle Enable."
elif printf '%s' "$loc" | grep -q 'accounts.google.com'; then
  pass "enabled (redirects to accounts.google.com)"
  if printf '%s' "$loc" | grep -q 'redirect_uri=.*supabase.co%2Fauth%2Fv1%2Fcallback'; then
    pass "Google is being asked to return to Supabase's callback (as it should)"
  fi
else
  fail "unexpected response"
  info "$(printf '%s' "$body" | head -c 200)"
fi
echo

# ── 2. Apple provider enabled? ───────────────────────────────────────────────
echo "2. Apple provider"
# This app uses NATIVE Sign in with Apple: ASAuthorizationAppleIDProvider hands an identity
# token to the backend, which calls sign_in_with_id_token (POST /token?grant_type=id_token).
# It never touches /authorize, and it needs no OAuth secret — only the Client IDs allow-list.
#
# So `missing OAuth secret` from /authorize is a PASS here, not a failure: it is the WEB flow
# reporting that it has no JWT secret, which is exactly the intended configuration. Crucially,
# only an ENABLED provider gets far enough to produce that message — a disabled one answers
# `provider is not enabled` instead. That difference is what this check reads.
abody=$(curl -s --max-time 10 "$SUPA/auth/v1/authorize?provider=apple&redirect_to=$CALLBACK")
aloc=$(curl -s -o /dev/null -w '%{redirect_url}' --max-time 10 \
        "$SUPA/auth/v1/authorize?provider=apple&redirect_to=$CALLBACK")
if printf '%s' "$abody" | grep -q 'provider is not enabled'; then
  fail "DISABLED — Supabase says 'provider is not enabled'"
  info "Fix: Supabase → Authentication → Sign In / Providers → Apple → enable, and set"
  info "     Client IDs to the BUNDLE ID (com.phan.caydex). Leave Secret Key EMPTY."
  info "     Also tick Sign in with Apple on the identifier in the Apple Developer portal."
elif printf '%s' "$abody" | grep -q 'missing OAuth secret'; then
  pass "enabled, native-only (no web OAuth secret — correct for this app)"
  info "The web flow is intentionally unconfigured; the app posts the identity token to"
  info "grant_type=id_token, which authorises off the Client IDs list alone."
elif printf '%s' "$aloc" | grep -q 'appleid.apple.com'; then
  pass "enabled, with the web flow also configured"
else
  fail "unexpected response"
  info "$(printf '%s' "$abody" | head -c 200)"
fi
echo

# ── 3. Is the app's callback URL allow-listed? ───────────────────────────────
# A non-allow-listed redirect_to is silently replaced by the project's Site URL, so the app's
# ASWebAuthenticationSession never sees its scheme and the sign-in appears to hang or error.
echo "3. Redirect URL allow-list ($CALLBACK)"
if printf '%s' "$loc" | grep -q 'accounts.google.com'; then
  inner=$(printf '%s' "$loc" | sed -n 's/.*redirect_to=\([^&]*\).*/\1/p')
  if [ -n "$inner" ] && printf '%s' "$inner" | grep -qi 'caydex'; then
    pass "allow-listed (Supabase preserved the app's scheme)"
  else
    fail "NOT allow-listed — Supabase dropped/replaced the app's redirect"
    info "Fix: Supabase → Authentication → URL Configuration → Redirect URLs → add:"
    info "     $CALLBACK"
    info "Symptom if skipped: Google consent succeeds, then the app never regains control."
  fi
else
  info "(skipped — enable the Google provider first)"
fi
echo

# ── 4. App-side prerequisites ────────────────────────────────────────────────
echo "4. App side"
schemes=$(/usr/libexec/PlistBuddy -c 'Print :CFBundleURLTypes:0:CFBundleURLSchemes:0' "$PLIST" 2>/dev/null)
[ "$schemes" = "caydex" ] && pass "URL scheme 'caydex' registered in Info.plist" \
                          || fail "URL scheme missing from Info.plist (got: ${schemes:-none})"

for f in ios.entitlements ios-Release.entitlements; do
  if /usr/libexec/PlistBuddy -c 'Print :com.apple.developer.applesignin' \
        "$REPO/frontend/ios/ios/$f" >/dev/null 2>&1; then
    pass "Sign in with Apple entitlement present in $f"
  else
    fail "Sign in with Apple entitlement MISSING from $f"
    info "Release builds silently lose the capability — it fails only in TestFlight/App Store."
  fi
done
echo

echo "Note: this cannot see your Google OAuth consent screen's publishing status."
echo "If it is still in Testing, sign-in works for allow-listed testers only (max 100) and"
echo "refresh tokens expire after 7 days. Publish it before launch."
echo
