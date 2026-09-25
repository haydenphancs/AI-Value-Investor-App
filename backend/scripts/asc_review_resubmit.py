#!/usr/bin/env python3
"""Answer the 2026-09-24 Guideline 2.5.4 rejection in App Store Connect's review metadata.

WHY
---
Caydex 1.0 (9) was rejected because App Review "could not locate any features that require
persistent audio". The feature is real — Learn narration keeps playing on the Home and Lock
Screen — but the review notes described it in one sentence with no path to it, and Apple asked
for a physical-device screen recording "in the Notes field of the App Review Information
section ... for future submissions". This script does the two metadata halves of that:

  1. Replaces the notes paragraph that starts "Background modes." with one that gives the
     reviewer a tap-by-tap path to the narration and points at the recording, and corrects
     three more that the 2026-09-24 pre-resubmission audit found inaccurate: the demo-account
     paragraph (deletion path; how to buy each IAP from a Max account), the library paragraph
     ("Learn" is the Wiser tab) and the age rating (18+ is selected, not 17+). Every other
     paragraph is left byte-identical.
  2. Uploads the recording as an App Review attachment, next to the existing FMP Order Form
     PDF (which is kept).

The Resolution Center REPLY is not here on purpose: it is a message sent on the developer's
behalf, and the developer sends it.

DEFAULTS TO --dry-run. The original notes are written to a backup directory first.

    ./venv/bin/python scripts/asc_review_resubmit.py --video ~/Desktop/caydex-bg-audio.mov
    ./venv/bin/python scripts/asc_review_resubmit.py --video ~/Desktop/caydex-bg-audio.mov --apply

Credentials: ASC_KEY_ID / ASC_ISSUER_ID / ASC_PRIVATE_KEY_PATH from the environment, else from
backend/.env (via `pull_testflight_feedback._credentials`). Values are never printed.
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import importlib.util
import os
import sys
import tempfile
from pathlib import Path
from typing import Dict, List, Optional

_HERE = Path(__file__).resolve().parent
_BUNDLE = "com.phan.caydex"
_VERSION = "1.0"
_BACKUP = Path(os.environ.get("ASC_BACKUP_DIR") or tempfile.gettempdir()) / "asc-review-resubmit-backup"

# App Store Connect's hard cap on App Review Information → Notes.
NOTES_LIMIT = 4000

# The paragraph being replaced begins with this. Matched on the stripped paragraph start so a
# leading space or a trailing edit elsewhere in the paragraph does not defeat it.
OLD_PARAGRAPH_START = "Background modes."

# ⚠️ Keep every paragraph below TRUE against the build under review. They name tap paths; if
# a screen is reorganised, re-walk the path on a device and edit this text before the next
# submission — a wrong path in the notes is how a reviewer "cannot locate" a real feature.
NEW_PARAGRAPH = (
    "Background audio (UIBackgroundModes: audio). Caydex narrates its education library, and "
    "the narration keeps playing after the user leaves the app or locks the screen, with play, "
    "pause, skip 15 s and scrubbing on the Lock Screen and in Control Center. Narration is a "
    "Pro/Max feature; the demo account is on Max, so it plays with no purchase. To hear it: "
    "sign in, tap the Wiser tab (last tab), tap the first Money Moves article, then tap Listen "
    "Now. Books work the same way (Wiser > AI-Enabled Books > any book > Listen Now). Then go "
    "to the Home Screen or lock the device: the narration continues. A screen recording of "
    "this on a physical iPhone is attached to App Review Information. The app declares no "
    "other background mode."
)

DEMO_PARAGRAPH = (
    "Demo account — please use this to review. Caydex requires an account. Our market-data "
    "licence (the signed Order Form is attached) grants End-User Display Rights only, which "
    "permit the data to be displayed solely through the licensee's authenticated platform, so we "
    "cannot serve it to a signed-out caller. The app also contains significant account-based "
    "features — credits, paid AI reports, subscriptions, watchlists and portfolios — and "
    "provides Sign in with Apple and in-app account deletion (tap the profile picture at the top "
    "right of Home > General Settings > Delete Account, under Danger Zone). Credentials are in "
    "the App Review sign-in fields. The account is on the Max plan and pre-loaded with credits, "
    "so every feature, including narration, works without a purchase. Every in-app purchase can "
    "still be bought in sandbox from this account: Profile > Plans for the Pro and Max "
    "subscriptions, and Profile > Add Credits for the four credit packs."
)

LIBRARY_PARAGRAPH = (
    "Educational library. The Wiser tab contains original study guides written by us that "
    "summarise the ideas of ten well-known investing books, plus original lessons and articles. "
    "No book text is reproduced; all narration audio is of our own writing."
)

AGE_PARAGRAPH = (
    "Age rating 18+. Our Terms require users to be 18 or the age of majority. The app contains "
    "no gambling, no unrestricted web access and no user-generated content visible to other "
    "users."
)

# (paragraph start marker, replacement). Each marker must match exactly one live paragraph.
REPLACEMENTS = [
    (OLD_PARAGRAPH_START, NEW_PARAGRAPH),
    ("Demo account", DEMO_PARAGRAPH),
    ("Educational library.", LIBRARY_PARAGRAPH),
    ("Age rating", AGE_PARAGRAPH),
]

# ── version metadata (description / promotional text / copyright) ─────────────────────
#
# Guideline 2.3.2: an app description must make clear which features need an additional
# purchase. The 2026-09-24 audit (critic-1, confirmed) found the description advertising
# congressional trades, investor holdings and narration — all Pro/Max-gated in
# `entitlements.py` — with the only plan difference named being "Pro and Max raise [credits]".
# Each swap is exact-match: a live text that no longer contains `old` (and does not already
# contain `new`) is REFUSED, never guessed around.
DESCRIPTION_SWAPS = [
    (
        "with both filing and disclosure dates shown, because the gap between them matters.",
        "with both filing and disclosure dates shown, because the gap between them matters. "
        "Full holdings, trades and congressional data come with Pro or Max.",
    ),
    (
        "Narrated, with read-along highlighting, and it keeps playing when your screen locks.",
        "Narrated, with read-along highlighting, and it keeps playing when your screen locks "
        "(Money Moves and book narration with Pro or Max; Investor Journey narration is free).",
    ),
    (
        "Every account includes a monthly credit allowance for AI reports and chat. Pro and Max "
        "raise it, and credit packs are there if you want more.",
        "Every account includes a monthly credit allowance for AI reports and chat. Pro and Max "
        "raise it and unlock signal tickers, full investor holdings and trades, congressional "
        "data, following more investors, more Updates tickers, and Money Moves and book "
        "narration. Credit packs are there if you want more.",
    ),
    (
        "Terms of Use: https://caydexinvest.com/terms",
        "Terms of Use: https://caydexinvest.com/terms\n"
        "Apple Standard EULA: https://www.apple.com/legal/internet-services/itunes/dev/stdeula/",
    ),
]
DESCRIPTION_LIMIT = 4000

PROMOTIONAL_TEXT = (
    "Research any public company, see what actually moved a stock today, and follow "
    "institutional and congressional filings with Pro or Max."
)
PROMOTIONAL_LIMIT = 170

COPYRIGHT = "2026 Hayden Phan"

# ── in-app purchase review notes ─────────────────────────────────────────────────────────
SUBSCRIPTION_NOTE_SWAP = (
    "Reached from Profile → Upgrade Plan, shown in the screenshot.",
    "Reached from Profile → Upgrade Plan on a Free account, or Profile → Plans on a Pro/Max "
    "account; the review demo account is on Max and can still buy both Pro and Max in sandbox.",
)
SUBSCRIPTION_IDS = ("6802485159", "6802485389")  # pro.monthly, max.monthly

CREDIT_PACK_NOTE = (
    "Consumable credit pack. Reached from Profile → Add Credits (sign-in required; the test "
    "account is in the App Review sign-in fields). Credits are spent only inside the app on AI "
    "reports and Cay AI chat, never expire, and cannot be transferred or cashed out."
)
CREDIT_PACK_IDS = ("6802487770", "6802489829", "6802489853", "6802490274")


def swap_text(text: str, old: str, new: str) -> str:
    """Replace ONE exact occurrence of `old` with `new`; idempotent; refuses when ambiguous."""
    if new in text:
        return text
    count = text.count(old)
    if count != 1:
        raise NotesError(f"expected exactly one {old[:60]!r}…, found {count} — not touching it")
    return text.replace(old, new, 1)


def apply_swaps(text: str, swaps, limit: int) -> str:
    out = text
    for old, new in swaps:
        out = swap_text(out, old, new)
    if len(out) > limit:
        raise NotesError(f"result is {len(out)} chars, over the {limit} limit")
    return out


_VIDEO_TYPES = {".mov", ".mp4", ".m4v"}


class NotesError(ValueError):
    """The notes cannot be rewritten safely — never guessed around."""


def replace_paragraph(notes: str, start: str, new_paragraph: str, limit: int = NOTES_LIMIT) -> str:
    """Swap the ONE paragraph beginning with `start` for `new_paragraph`.

    Idempotent: notes that already carry `new_paragraph` come back unchanged. Refuses (raises
    `NotesError`) rather than guessing when the paragraph is missing, appears twice, or the
    result would exceed App Store Connect's limit — a silently truncated or duplicated
    paragraph is exactly the reviewer-facing mess this exists to prevent.
    """
    if new_paragraph in notes:
        if len(notes) > limit:
            raise NotesError(f"notes are {len(notes)} chars, over the {limit} limit")
        return notes
    lines = notes.split("\n")
    hits = [i for i, line in enumerate(lines) if line.strip().startswith(start)]
    if not hits:
        raise NotesError(f"no paragraph starts with {start!r} — not touching the notes")
    if len(hits) > 1:
        raise NotesError(f"{len(hits)} paragraphs start with {start!r} — ambiguous, not touching")
    lines[hits[0]] = new_paragraph
    out = "\n".join(lines)
    if len(out) > limit:
        raise NotesError(
            f"rewritten notes would be {len(out)} chars, over the {limit} limit — shorten "
            f"NEW_PARAGRAPH by {len(out) - limit}"
        )
    return out


def apply_replacements(notes: str, replacements, limit: int = NOTES_LIMIT) -> str:
    """Apply every (start, paragraph) swap, then enforce the limit ONCE on the result.

    Checked at the end, not per step: an intermediate state may be longer than the final one
    (a later paragraph can shrink), and it is only the final text App Store Connect stores.
    """
    out = notes
    for start, paragraph in replacements:
        out = replace_paragraph(out, start, paragraph, limit=10**9)
    if len(out) > limit:
        raise NotesError(
            f"rewritten notes would be {len(out)} chars, over the {limit} limit — shorten "
            f"the replacement paragraphs by {len(out) - limit}"
        )
    return out


def video_problem(path: Path) -> Optional[str]:
    """Why this file cannot be the recording, or None when it can."""
    if not path.exists():
        return f"not found: {path}"
    if path.suffix.lower() not in _VIDEO_TYPES:
        return f"{path.name}: expected one of {sorted(_VIDEO_TYPES)}"
    if path.stat().st_size == 0:
        return f"{path.name} is empty"
    return None


def _load_asc():
    spec = importlib.util.spec_from_file_location("asc_audit", _HERE / "asc_audit.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _fill_credentials_from_dotenv() -> None:
    """asc_audit reads only the environment; backend/.env holds the three ASC_* values."""
    spec = importlib.util.spec_from_file_location("ptf", _HERE / "pull_testflight_feedback.py")
    ptf = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ptf)
    for name, value in ptf._credentials().items():
        # Not setdefault: an EXPORTED-but-empty variable would survive it, and asc_audit
        # would then report a credential as missing that backend/.env actually holds.
        if not os.environ.get(name, "").strip():
            os.environ[name] = value


def _diff(before: str, after: str) -> None:
    for line in difflib.unified_diff(before.splitlines(), after.splitlines(), lineterm="", n=0):
        if line.startswith(("+++", "---", "@@")):
            continue
        print(f"  {line}")


def _upload_attachment(asc, c, token: str, detail_id: str, video: Path) -> List[str]:
    data = video.read_bytes()
    print(f"    reserving {video.name} ({len(data):,} bytes)…")
    created = asc._write(c, token, "POST", "/v1/appStoreReviewAttachments", {
        "data": {"type": "appStoreReviewAttachments",
                 "attributes": {"fileName": video.name, "fileSize": len(data)},
                 "relationships": {"appStoreReviewDetail": {
                     "data": {"type": "appStoreReviewDetails", "id": detail_id}}}}
    })
    asset = created["data"]
    for i, op in enumerate(asset["attributes"]["uploadOperations"], 1):
        off, ln = int(op.get("offset") or 0), int(op.get("length") or 0)
        r = c.request(op.get("method", "PUT"), op["url"],
                      headers={h["name"]: h["value"] for h in op.get("requestHeaders") or []},
                      content=data[off:off + ln])
        if r.status_code >= 400:
            return [f"attachment chunk {i} failed: HTTP {r.status_code}"]
    done = asc._write(c, token, "PATCH", f"/v1/appStoreReviewAttachments/{asset['id']}", {
        "data": {"type": "appStoreReviewAttachments", "id": asset["id"],
                 "attributes": {"uploaded": True,
                                "sourceFileChecksum": hashlib.md5(data).hexdigest()}}})
    state = (done["data"]["attributes"].get("assetDeliveryState") or {})
    if state.get("errors"):
        return [f"attachment rejected by ASC: {state['errors']}"]
    print(f"    \033[32m✓\033[0m attached ({state.get('state')})")
    return []


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="write the changes (default: dry run)")
    ap.add_argument("--video", type=Path, help="the physical-device screen recording to attach")
    args = ap.parse_args()
    print(f"\n\033[33m▸ {'APPLY' if args.apply else 'DRY RUN — nothing will be written'}\033[0m")

    import httpx  # noqa: PLC0415 — keep the pure helpers importable without it

    problems: List[str] = []
    video = args.video.expanduser() if args.video else None
    if video is not None:
        why = video_problem(video)
        if why:
            print(f"\033[31m✗\033[0m {why}")
            return 1

    _fill_credentials_from_dotenv()
    asc = _load_asc()
    token = asc._token()
    _BACKUP.mkdir(parents=True, exist_ok=True)

    with httpx.Client(timeout=120.0) as c:
        app = asc._all(c, token, "/v1/apps", **{"filter[bundleId]": _BUNDLE})[0]
        versions = asc._all(c, token, f"/v1/apps/{app['id']}/appStoreVersions",
                            **{"filter[platform]": "IOS", "filter[versionString]": _VERSION})
        if len(versions) != 1:
            print(f"\033[31m✗\033[0m expected one iOS {_VERSION} version, found {len(versions)}")
            return 1
        version = versions[0]
        print(f"  {app['attributes']['name']} {_VERSION} · state {version['attributes'].get('appStoreState')}")

        detail = asc._get(c, token, f"/v1/appStoreVersions/{version['id']}/appStoreReviewDetail")["data"]
        notes: str = detail["attributes"].get("notes") or ""
        (_BACKUP / "review_notes.before.txt").write_text(notes, encoding="utf-8")

        # ── 1. notes ────────────────────────────────────────────────────────
        print(f"\n[1] review notes ({len(notes)} chars)")
        try:
            new_notes = apply_replacements(notes, REPLACEMENTS)
        except NotesError as e:
            problems.append(f"notes: {e}")
            new_notes = notes
        if new_notes == notes:
            print("    unchanged")
        else:
            print(f"    {len(notes)} -> {len(new_notes)} chars (limit {NOTES_LIMIT})")
            _diff(notes, new_notes)
            (_BACKUP / "review_notes.after.txt").write_text(new_notes, encoding="utf-8")
            if args.apply:
                asc._write(c, token, "PATCH", f"/v1/appStoreReviewDetails/{detail['id']}",
                           {"data": {"type": "appStoreReviewDetails", "id": detail["id"],
                                     "attributes": {"notes": new_notes}}})
                print("    \033[32m✓\033[0m notes updated")

        # ── 2. the recording ────────────────────────────────────────────────
        existing: List[Dict] = asc._all(
            c, token, f"/v1/appStoreReviewDetails/{detail['id']}/appStoreReviewAttachments")
        names = [e["attributes"].get("fileName") for e in existing]
        print(f"\n[2] review attachments: {names or 'none'}")
        if video is None:
            print("    no --video given — skipping")
        elif video.name in names:
            print(f"    {video.name} already attached — skipping")
        elif args.apply:
            problems += _upload_attachment(asc, c, token, detail["id"], video)
        else:
            print(f"    would upload {video.name} ({video.stat().st_size:,} bytes); existing kept")

        # ── 3. description / promotional text / copyright ─────────────────
        loc = next(l for l in asc._all(c, token, f"/v1/appStoreVersions/{version['id']}/appStoreVersionLocalizations")
                   if l["attributes"].get("locale") == "en-US")
        la = loc["attributes"]
        loc_changes = {}
        print("\n[3] version metadata (en-US)")
        try:
            desc = apply_swaps(la.get("description") or "", DESCRIPTION_SWAPS, DESCRIPTION_LIMIT)
            if desc != (la.get("description") or ""):
                loc_changes["description"] = desc
                (_BACKUP / "description.before.txt").write_text(la.get("description") or "", encoding="utf-8")
                print(f"    description: {len(la.get('description') or '')} -> {len(desc)} chars")
                _diff(la.get("description") or "", desc)
            else:
                print("    description: unchanged")
        except NotesError as e:
            problems.append(f"description: {e}")
        if len(PROMOTIONAL_TEXT) > PROMOTIONAL_LIMIT:
            problems.append(f"promotional text is {len(PROMOTIONAL_TEXT)} chars, over {PROMOTIONAL_LIMIT}")
        elif la.get("promotionalText") != PROMOTIONAL_TEXT:
            loc_changes["promotionalText"] = PROMOTIONAL_TEXT
            print(f"    promo: {la.get('promotionalText')!r}\n       -> {PROMOTIONAL_TEXT!r}")
        if loc_changes and args.apply:
            asc._write(c, token, "PATCH", f"/v1/appStoreVersionLocalizations/{loc['id']}",
                       {"data": {"type": "appStoreVersionLocalizations", "id": loc["id"],
                                 "attributes": loc_changes}})
            print("    \033[32m✓\033[0m localization updated")
        current_copyright = version["attributes"].get("copyright")
        if current_copyright != COPYRIGHT:
            print(f"    copyright: {current_copyright!r} -> {COPYRIGHT!r}")
            if args.apply:
                asc._write(c, token, "PATCH", f"/v1/appStoreVersions/{version['id']}",
                           {"data": {"type": "appStoreVersions", "id": version["id"],
                                     "attributes": {"copyright": COPYRIGHT}}})
                print("    \033[32m✓\033[0m copyright updated")

        # ── 4. in-app purchase review notes ─────────────────────────────────
        print("\n[4] in-app purchase review notes")
        for sid in SUBSCRIPTION_IDS:
            sub = asc._get(c, token, f"/v1/subscriptions/{sid}")["data"]["attributes"]
            try:
                note = swap_text(sub.get("reviewNote") or "", *SUBSCRIPTION_NOTE_SWAP)
            except NotesError as e:
                problems.append(f"subscription {sid}: {e}")
                continue
            if note == (sub.get("reviewNote") or ""):
                print(f"    {sub.get('productId')}: unchanged")
                continue
            print(f"    {sub.get('productId')}: path sentence rewritten")
            if args.apply:
                asc._write(c, token, "PATCH", f"/v1/subscriptions/{sid}",
                           {"data": {"type": "subscriptions", "id": sid, "attributes": {"reviewNote": note}}})
                print("      \033[32m✓\033[0m updated")
        for iid in CREDIT_PACK_IDS:
            iap = asc._get(c, token, f"/v2/inAppPurchases/{iid}")["data"]["attributes"]
            existing_note = iap.get("reviewNote") or ""
            if existing_note == CREDIT_PACK_NOTE:
                print(f"    {iap.get('productId')}: unchanged")
                continue
            if existing_note:
                problems.append(f"{iap.get('productId')} already has a different reviewNote — not overwriting")
                continue
            print(f"    {iap.get('productId')}: add review note")
            if args.apply:
                asc._write(c, token, "PATCH", f"/v2/inAppPurchases/{iid}",
                           {"data": {"type": "inAppPurchases", "id": iid, "attributes": {"reviewNote": CREDIT_PACK_NOTE}}})
                print("      \033[32m✓\033[0m updated")

    print(f"\nbackup: {_BACKUP}")
    if problems:
        print("\n\033[31m✗\033[0m issues:\n   • " + "\n   • ".join(problems))
        return 1
    if not args.apply:
        print("\nDry run only. Re-run with --apply to write.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
