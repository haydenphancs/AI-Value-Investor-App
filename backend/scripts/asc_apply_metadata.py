#!/usr/bin/env python3
"""Apply the pre-submission metadata that the audit found missing or now-false.

Four things, all verified against the live app first:

  1. `contentRightsDeclaration` — was null. Guideline 5.2.2 asks you to declare third-party
     content; FMP market data and CoinGecko crypto data are exactly that.
  2. The signed FMP Order Form as an App Review attachment — was absent. 5.2.2 lets a reviewer
     demand proof of the right to display it, and answering before it is asked saves a cycle.
  3. The App Store description's "FREE TO BROWSE" section — TRUE THIS MORNING, FALSE NOW. The
     account-only change (End-User Display Rights permit FMP data only through an authenticated
     platform) means a reviewer hits a sign-in wall while the listing promises browsing without
     one. `.claude/rules/auth.md` names this contradiction specifically.
  4. The review notes' demo-account paragraph — same falsehood, stated more explicitly, plus it
     never gave the 5.1.1(v) justification for requiring an account.

DEFAULTS TO --dry-run. Every original is written to a backup directory first, so any of this
can be put back by hand.

    ./venv/bin/python scripts/asc_apply_metadata.py            # show the diff, change nothing
    ./venv/bin/python scripts/asc_apply_metadata.py --apply    # write it
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import httpx

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parents[1]
_BACKUP = Path("/private/tmp/claude-501/-Users-haiphan-BIGDATA-myApp-AI-Value-Investor-App/"
               "a7e66b8b-b633-42b9-a98e-0dc75005bb66/scratchpad/asc-backup")
_FMP_PDF = Path("/Users/haiphan/BIGDATA/myApp/Docs and Research/"
                "DocuSign_FMP_-_Quote_-_Caydex_-_0003.pdf")
_BUNDLE = "com.phan.caydex"

# Reuse the audited client rather than re-implementing auth.
_spec = importlib.util.spec_from_file_location("asc", _HERE / "asc_audit.py")
asc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(asc)

# ── the replacement copy ────────────────────────────────────────────────────

_OLD_DESC_HEADING = "FREE TO BROWSE"
_OLD_DESC_BODY = (
    "Market data, company screens, news, search and the education library need no account. "
    "AI reports and chat cost credits — a free account includes a monthly allowance, Pro and "
    "Max raise it, and credit packs are there if you want more. Credits are spent only inside "
    "the app, are not a currency, and cannot be transferred or cashed out."
)
_NEW_DESC_HEADING = "FREE TO START"
_NEW_DESC_BODY = (
    "Caydex needs a free account — it is how your watchlists, portfolios and reports stay "
    "yours across devices. Every account includes a monthly credit allowance for AI reports "
    "and chat. Pro and Max raise it, and credit packs are there if you want more. Credits are "
    "spent only inside the app, are not a currency, and cannot be transferred or cashed out."
)

_OLD_NOTES_PARA_START = "Demo account — please use this to review."
_NEW_NOTES_PARA = (
    "Demo account — please use this to review. Caydex requires an account. Our market-data "
    "licence (the signed Order Form is attached) grants End-User Display Rights only, which "
    "permit the data to be displayed solely through the licensee's authenticated platform, so "
    "we cannot serve it to a signed-out caller. The app also contains significant "
    "account-based features — credits, paid AI reports, subscriptions, watchlists and "
    "portfolios — and provides in-app account deletion (Profile → Settings → Delete Account) "
    "and Sign in with Apple. Credentials are in the App Review sign-in fields; the account is "
    "pre-loaded with credits so every feature can be exercised without a purchase. In-app "
    "purchases remain fully testable from the paywall: the Max subscription and all four "
    "credit packs are available for sandbox purchase."
)


def _diff(label: str, before: str, after: str) -> None:
    print(f"\n{'─' * 74}\n{label}\n{'─' * 74}")
    for line in difflib.unified_diff(
        before.splitlines(), after.splitlines(), "before", "after", lineterm="", n=1
    ):
        if line.startswith("+++") or line.startswith("---") or line.startswith("@@"):
            continue
        colour = "\033[32m" if line.startswith("+") else "\033[31m" if line.startswith("-") else ""
        print(f"  {colour}{line}\033[0m" if colour else f"  {line}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="write the changes (default: dry run)")
    args = ap.parse_args()
    mode = "APPLY" if args.apply else "DRY RUN — nothing will be written"
    print(f"\n\033[33m▸ {mode}\033[0m")

    _BACKUP.mkdir(parents=True, exist_ok=True)
    token = asc._token()
    problems: list[str] = []

    with httpx.Client(timeout=60.0) as c:
        app = asc._all(c, token, "/v1/apps", **{"filter[bundleId]": _BUNDLE})[0]
        app_id = app["id"]
        vers = asc._all(c, token, f"/v1/apps/{app_id}/appStoreVersions")
        version = next(v for v in vers
                       if v["attributes"].get("appVersionState") == "PREPARE_FOR_SUBMISSION"
                       or v["attributes"].get("appStoreState") == "PREPARE_FOR_SUBMISSION")
        vid = version["id"]
        print(f"  app {app['attributes']['name']} · version {version['attributes']['versionString']}")

        # ── 1. content rights ───────────────────────────────────────────────
        current = app["attributes"].get("contentRightsDeclaration")
        print(f"\n[1] contentRightsDeclaration: {current!r} -> 'USES_THIRD_PARTY_CONTENT'")
        if current == "USES_THIRD_PARTY_CONTENT":
            print("    already set — skipping")
        elif args.apply:
            asc._write(c, token, "PATCH", f"/v1/apps/{app_id}", {
                "data": {"type": "apps", "id": app_id,
                         "attributes": {"contentRightsDeclaration": "USES_THIRD_PARTY_CONTENT"}}
            })
            print("    \033[32m✓\033[0m set")

        # ── 2. description ──────────────────────────────────────────────────
        loc = asc._all(c, token, f"/v1/appStoreVersions/{vid}/appStoreVersionLocalizations")[0]
        desc = loc["attributes"].get("description") or ""
        (_BACKUP / "description.before.txt").write_text(desc, encoding="utf-8")

        new_desc = desc
        if _OLD_DESC_HEADING in new_desc:
            new_desc = new_desc.replace(_OLD_DESC_HEADING, _NEW_DESC_HEADING, 1)
        if _OLD_DESC_BODY in new_desc:
            new_desc = new_desc.replace(_OLD_DESC_BODY, _NEW_DESC_BODY, 1)
        else:
            problems.append("description: the FREE TO BROWSE paragraph did not match verbatim "
                            "— it was edited since the audit; not touching it")

        print(f"\n[2] description: {len(desc)} -> {len(new_desc)} chars (limit 4000)")
        if len(new_desc) > 4000:
            problems.append(f"description would be {len(new_desc)} chars, over the 4000 limit")
        elif new_desc != desc and not problems:
            _diff("App Store description", desc, new_desc)
            (_BACKUP / "description.after.txt").write_text(new_desc, encoding="utf-8")
            if args.apply:
                asc._write(c, token, "PATCH",
                           f"/v1/appStoreVersionLocalizations/{loc['id']}",
                           {"data": {"type": "appStoreVersionLocalizations", "id": loc["id"],
                                     "attributes": {"description": new_desc}}})
                print("    \033[32m✓\033[0m description updated")
        elif new_desc == desc:
            print("    unchanged")

        # ── 3. review notes ─────────────────────────────────────────────────
        det = asc._get(c, token, f"/v1/appStoreVersions/{vid}/appStoreReviewDetail")["data"]
        notes = det["attributes"].get("notes") or ""
        (_BACKUP / "review_notes.before.txt").write_text(notes, encoding="utf-8")

        paras = notes.split("\n")
        new_paras, replaced = [], False
        for p in paras:
            if p.strip().startswith(_OLD_NOTES_PARA_START) and not replaced:
                new_paras.append(_NEW_NOTES_PARA)
                replaced = True
            else:
                new_paras.append(p)
        new_notes = "\n".join(new_paras)
        if not replaced:
            problems.append("review notes: the 'Demo account' paragraph was not found — "
                            "not touching it")

        print(f"\n[3] review notes: {len(notes)} -> {len(new_notes)} chars")
        if replaced:
            _diff("Review notes", notes, new_notes)
            (_BACKUP / "review_notes.after.txt").write_text(new_notes, encoding="utf-8")
            if args.apply:
                asc._write(c, token, "PATCH", f"/v1/appStoreReviewDetails/{det['id']}",
                           {"data": {"type": "appStoreReviewDetails", "id": det["id"],
                                     "attributes": {"notes": new_notes}}})
                print("    \033[32m✓\033[0m notes updated")

        # ── 4. the FMP order form ───────────────────────────────────────────
        existing = asc._all(c, token,
                            f"/v1/appStoreReviewDetails/{det['id']}/appStoreReviewAttachments")
        print(f"\n[4] review attachments: {len(existing)} present")
        for e in existing:
            print(f"    existing: {e['attributes'].get('fileName')}")
        if existing:
            print("    already attached — skipping")
        elif not _FMP_PDF.exists():
            problems.append(f"FMP order form not found at {_FMP_PDF}")
        elif args.apply:
            data = _FMP_PDF.read_bytes()
            print(f"    reserving {_FMP_PDF.name} ({len(data):,} bytes)…")
            created = asc._write(c, token, "POST", "/v1/appStoreReviewAttachments", {
                "data": {"type": "appStoreReviewAttachments",
                         "attributes": {"fileName": _FMP_PDF.name, "fileSize": len(data)},
                         "relationships": {"appStoreReviewDetail": {
                             "data": {"type": "appStoreReviewDetails", "id": det["id"]}}}}
            })
            asset = created["data"]
            for i, op in enumerate(asset["attributes"]["uploadOperations"], 1):
                off, ln = int(op.get("offset") or 0), int(op.get("length") or 0)
                r = c.request(op.get("method", "PUT"), op["url"],
                              headers={h["name"]: h["value"] for h in op.get("requestHeaders") or []},
                              content=data[off:off + ln])
                if r.status_code >= 400:
                    problems.append(f"attachment chunk {i} failed: {r.status_code} {r.text[:200]}")
                    break
                print(f"    uploaded chunk {i}")
            else:
                done = asc._write(c, token, "PATCH",
                                  f"/v1/appStoreReviewAttachments/{asset['id']}",
                                  {"data": {"type": "appStoreReviewAttachments", "id": asset["id"],
                                            "attributes": {"uploaded": True,
                                                           "sourceFileChecksum": hashlib.md5(data).hexdigest()}}})
                st = (done["data"]["attributes"].get("assetDeliveryState") or {})
                if st.get("errors"):
                    problems.append(f"attachment rejected: {st['errors']}")
                else:
                    print(f"    \033[32m✓\033[0m attached ({st.get('state')})")
        else:
            print(f"    would upload {_FMP_PDF.name} ({_FMP_PDF.stat().st_size:,} bytes)")

    print("\n" + "═" * 74)
    print(f"backups: {_BACKUP}")
    if problems:
        print("\n\033[31m✗\033[0m issues:")
        for p in problems:
            print(f"   • {p}")
        return 1
    if not args.apply:
        print("\nDry run only. Re-run with --apply to write.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
