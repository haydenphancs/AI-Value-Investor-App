#!/bin/bash
# Upload an archive's dSYMs to Sentry so PRODUCTION crashes symbolicate.
#
#   ./frontend/ios/scripts/upload-dsyms.sh              # newest archive
#   ./frontend/ios/scripts/upload-dsyms.sh <path.xcarchive>
#
# WHY THIS IS A SCRIPT AND NOT AN XCODE RUN PHASE
#
# The obvious design is a "Run Script" build phase so this happens automatically on every
# archive. It is deliberately NOT that, because the project sets
# `ENABLE_USER_SCRIPT_SANDBOXING = YES` (project.pbxproj:313,378). Under sandboxing a build
# phase cannot read `~/.sentryclirc`, so wiring one up means flipping that setting to NO
# project-wide — weakening a real security control on a fintech app, to save one command on
# an action performed a handful of times per release. Revisit after launch if archives
# become frequent; the trade is the setting, not the convenience.
#
# WHAT THIS FIXES, AND WHAT IT DOES NOT
#
# It uploads OUR dSYMs (Caydex + CaydexWidgets) to Sentry, so a crash in a TestFlight or App
# Store build shows function names and line numbers instead of raw addresses. Without it,
# production crash reports are effectively undiagnosable.
#
# It has NOTHING to do with Xcode's "Upload Symbols Failed … dSYM for Sentry.framework"
# warning at validation. That one is Apple asking for SENTRY'S OWN dSYM, which Sentry does
# not ship in Sentry.xcframework for any slice — there is no file to supply, it cannot be
# fixed from here, and it blocks nothing.
#
# AUTH: an Organization Token (Sentry → Settings → Developer Settings → Organization Tokens,
# scope `org:ci` — verified sufficient for debug-file upload on 2026-08-22). Store it in
# ~/.sentryclirc, which lives OUTSIDE the repo:
#
#   [auth]
#   token=sntrys_...
#
# Unlike the iOS client DSN — public, write-only, committed in MonitoringConfig.swift — this
# token can write to the org. It must never be committed. `SENTRY_AUTH_TOKEN` in the
# environment also works and takes precedence.

set -euo pipefail

ORG="caydex"
PROJECT="caydex-apple-ios"

if ! command -v sentry-cli >/dev/null 2>&1; then
    echo "error: sentry-cli not installed — 'brew install getsentry/tools/sentry-cli'" >&2
    exit 1
fi

if [ $# -ge 1 ]; then
    ARCHIVE="$1"
    if [ ! -d "$ARCHIVE" ]; then
        echo "error: no such archive: $ARCHIVE" >&2
        exit 1
    fi
else
    ARCHIVE=$(ls -td "$HOME"/Library/Developer/Xcode/Archives/*/*.xcarchive 2>/dev/null | head -1 || true)
    if [ -z "${ARCHIVE:-}" ]; then
        echo "error: no .xcarchive under ~/Library/Developer/Xcode/Archives." >&2
        echo "       Archive in Xcode first, or pass a path explicitly." >&2
        exit 1
    fi
fi

DSYMS="$ARCHIVE/dSYMs"
if [ ! -d "$DSYMS" ]; then
    echo "error: $ARCHIVE has no dSYMs/ directory." >&2
    echo "       Release must build with DEBUG_INFORMATION_FORMAT = dwarf-with-dsym." >&2
    exit 1
fi

# Fail loudly on an archive that carries no OWN dSYM. Sentry-framework-only would upload
# "successfully" and still leave every crash in our code unsymbolicated — the exact silent
# outcome this script exists to prevent.
if [ -z "$(find "$DSYMS" -maxdepth 1 -name 'Caydex*.dSYM' -print -quit)" ]; then
    echo "error: no Caydex dSYM in $DSYMS — refusing to report success." >&2
    ls -1 "$DSYMS" >&2
    exit 1
fi

if [ -z "${SENTRY_AUTH_TOKEN:-}" ] && [ ! -f "$HOME/.sentryclirc" ]; then
    echo "error: no Sentry auth. Create ~/.sentryclirc with:" >&2
    echo "         [auth]" >&2
    echo "         token=sntrys_..." >&2
    echo "       or export SENTRY_AUTH_TOKEN. See the header for which token to mint." >&2
    exit 1
fi

echo "archive : $(basename "$ARCHIVE")"
echo "version : $(/usr/libexec/PlistBuddy -c 'Print :ApplicationProperties:CFBundleShortVersionString' "$ARCHIVE/Info.plist" 2>/dev/null || echo '?') ($(/usr/libexec/PlistBuddy -c 'Print :ApplicationProperties:CFBundleVersion' "$ARCHIVE/Info.plist" 2>/dev/null || echo '?'))"
echo "dSYMs   : $(ls -1 "$DSYMS" | tr '\n' ' ')"
echo

sentry-cli debug-files upload --org "$ORG" --project "$PROJECT" --type dsym "$DSYMS"

echo
echo "Verify at https://sentry.io/settings/$ORG/projects/$PROJECT/debug-symbols/"
