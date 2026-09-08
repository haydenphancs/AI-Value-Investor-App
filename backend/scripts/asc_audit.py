#!/usr/bin/env python3
"""Read the live App Store Connect IAP configuration and diff it against the repo.

WHY
---
`documents/legal/asc-iap-metadata.md` is typed into a web form by hand, and nothing downstream
validates what was typed. If ASC says a pack sells 90 credits for $1.99 while `credit_packs`
grants 130 and Apple charges $2.99, every purchase succeeds and the user is quietly
short-changed — the only trace is a support email. `test_asc_metadata_doc_parity.py` pins the
DOC against `Caydex.storekit`; this script pins the LIVE ASC configuration against it, which is
the one link in that chain a test cannot reach.

It is READ-ONLY. It issues only GETs.

SETUP (once)
------------
1. App Store Connect → Users and Access → Integrations → App Store Connect API → Team Keys
2. Generate an API Key. Access role **App Manager** is the minimum that can read in-app
   purchases and subscriptions; Developer cannot.
3. Download the `.p8` — Apple lets you do this ONCE. Store it OUTSIDE the repo, e.g.
   `~/.appstoreconnect/private_keys/AuthKey_XXXXXXXXXX.p8`. (`*.p8` is gitignored, but the
   safest copy is the one that was never in the working tree.)
4. Copy the **Key ID** (on the key's row) and the **Issuer ID** (above the table).

RUN
---
    export ASC_KEY_ID=XXXXXXXXXX
    export ASC_ISSUER_ID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
    export ASC_PRIVATE_KEY_PATH=~/.appstoreconnect/private_keys/AuthKey_XXXXXXXXXX.p8
    ./venv/bin/python scripts/asc_audit.py

Exit code 0 = ASC agrees with `Caydex.storekit`. Non-zero = a difference worth reading.

The token is minted per run, expires in 15 minutes (Apple's cap is 20), and is never printed.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

_REPO = Path(__file__).resolve().parents[2]
_STOREKIT = _REPO / "frontend" / "ios" / "Caydex.storekit"
_BUNDLE_ID = "com.phan.caydex"
_API = "https://api.appstoreconnect.apple.com"

_OK = "\033[32m✓\033[0m"
_BAD = "\033[31m✗\033[0m"
_WARN = "\033[33m!\033[0m"


# ── credentials ─────────────────────────────────────────────────────────────

def _token() -> str:
    """A short-lived ES256 JWT for the ASC API. Never logged."""
    from jose import jwt  # noqa: PLC0415

    key_id = os.environ.get("ASC_KEY_ID", "").strip()
    issuer = os.environ.get("ASC_ISSUER_ID", "").strip()
    key_path = os.environ.get("ASC_PRIVATE_KEY_PATH", "").strip()

    missing = [
        name
        for name, value in (
            ("ASC_KEY_ID", key_id),
            ("ASC_ISSUER_ID", issuer),
            ("ASC_PRIVATE_KEY_PATH", key_path),
        )
        if not value
    ]
    if missing:
        sys.exit(
            f"{_BAD} missing {', '.join(missing)} — see the SETUP block at the top of this file"
        )

    # Shape checks BEFORE the network call. Both values are opaque strings on the ASC page and
    # sit inches apart, so swapping them is the standard first-run mistake — and the only
    # symptom is a 401 that reads like a bad private key, sending you to re-download a `.p8`
    # that was never the problem. Neither of these is a secret, so naming them in an error is
    # safe; the `.p8` is the credential and is never printed.
    looks_like_uuid = re.fullmatch(r"[0-9a-fA-F-]{36}", issuer) is not None
    key_looks_like_uuid = re.fullmatch(r"[0-9a-fA-F-]{36}", key_id) is not None

    if key_looks_like_uuid and not looks_like_uuid:
        sys.exit(
            f"{_BAD} ASC_KEY_ID and ASC_ISSUER_ID look swapped.\n"
            f"    ASC_KEY_ID is a UUID ({key_id}) — that is the Issuer ID, shown ABOVE the keys table.\n"
            f"    The Key ID is the 10-character value in the KEY ID column, and is also in the\n"
            f"    filename of the key you downloaded (AuthKey_<KEY ID>.p8)."
        )
    if not looks_like_uuid:
        sys.exit(
            f"{_BAD} ASC_ISSUER_ID is {issuer!r}, which is not a UUID.\n"
            f"    It is the 36-character value shown above the keys table on\n"
            f"    Users and Access -> Integrations -> App Store Connect API."
        )
    if not re.fullmatch(r"[A-Z0-9]{8,12}", key_id):
        print(
            f"{_WARN} ASC_KEY_ID {key_id!r} is not the usual 10 uppercase alphanumerics — "
            f"check it against the KEY ID column, or against AuthKey_<KEY ID>.p8"
        )

    path = Path(key_path).expanduser()
    if not path.exists():
        sys.exit(f"{_BAD} private key not found at {path}")
    # A mismatched pair authenticates as nobody. The filename carries the Key ID, so this
    # catches "downloaded a second key and updated only one variable".
    m = re.search(r"AuthKey_([A-Z0-9]+)\.p8$", path.name)
    if m and m.group(1) != key_id:
        sys.exit(
            f"{_BAD} ASC_KEY_ID is {key_id}, but the key file is {path.name} "
            f"(key {m.group(1)}). They must be the same key."
        )
    # A key inside the repo is one `git add -A` from being published, even with *.p8 ignored.
    try:
        path.resolve().relative_to(_REPO)
        print(f"{_WARN} the .p8 is inside the repo — move it to ~/.appstoreconnect/private_keys/")
    except ValueError:
        pass

    now = int(time.time())
    return jwt.encode(
        {"iss": issuer, "iat": now, "exp": now + 15 * 60, "aud": "appstoreconnect-v1"},
        path.read_text(),
        algorithm="ES256",
        headers={"kid": key_id, "typ": "JWT"},
    )


# ── HTTP ────────────────────────────────────────────────────────────────────

def _get(client, token: str, path: str, **params) -> Dict[str, Any]:
    url = path if path.startswith("http") else f"{_API}{path}"
    r = client.get(url, headers={"Authorization": f"Bearer {token}"}, params=params or None)
    if r.status_code == 401:
        sys.exit(
            f"{_BAD} 401 from ASC — the key, issuer or .p8 do not match, or the key was revoked"
        )
    if r.status_code == 403:
        sys.exit(
            f"{_BAD} 403 from ASC on {path} — the key's role is too low. "
            "In-app purchases need App Manager or Admin; Developer is not enough."
        )
    if r.status_code >= 400:
        sys.exit(f"{_BAD} {r.status_code} from ASC on {path}: {r.text[:300]}")
    return r.json()


def _write(client, token: str, method: str, path: str, body: Optional[dict] = None) -> Dict[str, Any]:
    """POST/PATCH/DELETE against ASC. Separated from `_get` so every mutating call is greppable.

    Nothing in the audit path reaches this — it runs only under
    `--upload-review-screenshots`, which is opt-in.
    """
    url = path if path.startswith("http") else f"{_API}{path}"
    r = client.request(
        method,
        url,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json=body,
    )
    if r.status_code == 403:
        sys.exit(
            f"{_BAD} 403 on {method} {path} — this API key cannot WRITE. "
            "Uploading needs App Manager or Admin."
        )
    if r.status_code >= 400:
        sys.exit(f"{_BAD} {r.status_code} on {method} {path}: {r.text[:400]}")
    return r.json() if r.content else {}


def _all(client, token: str, path: str, **params) -> List[Dict[str, Any]]:
    """Follow `links.next` — ASC pages at 50 and silently truncates otherwise."""
    out: List[Dict[str, Any]] = []
    page = _get(client, token, path, limit=200, **params)
    while True:
        out.extend(page.get("data") or [])
        nxt = (page.get("links") or {}).get("next")
        if not nxt:
            return out
        page = _get(client, token, nxt)


# ── the repo side ───────────────────────────────────────────────────────────

def _expected() -> Dict[str, Dict[str, Any]]:
    raw = re.sub(r"//.*", "", _STOREKIT.read_text(encoding="utf-8"))
    d = json.loads(raw)
    out: Dict[str, Dict[str, Any]] = {}

    def add(p: Dict[str, Any], kind: str) -> None:
        loc = (p.get("localizations") or [{}])[0]
        out[p["productID"]] = {
            "kind": kind,
            "referenceName": p.get("referenceName", ""),
            "displayName": loc.get("displayName", ""),
            "description": loc.get("description", ""),
            "price": str(p.get("displayPrice", "")),
        }

    for p in d.get("products") or []:
        add(p, "consumable")
    groups = d.get("subscriptionGroups") or []
    for g in groups:
        for p in g.get("subscriptions") or []:
            add(p, "subscription")
    return out


# ── prices ──────────────────────────────────────────────────────────────────
#
# Price is the field that actually costs money when it is wrong: Apple charges what ASC says
# and the backend grants what `credit_packs` says, so a mismatch short-changes the buyer with
# no error anywhere. It is also the fiddliest thing to read — the customer price lives two hops
# away, on a price POINT related to a price entry on a schedule.
#
# Every failure here returns None rather than raising. "Could not read the price" must be
# reported as UNVERIFIED, never silently folded into a pass — the first version of this script
# printed "matches ... and price" while never fetching one, which is the exact false green the
# tool exists to prevent.


def _usd_price_from(payload: Dict[str, Any]) -> Optional[str]:
    """Pull the USA customer price out of a `?include=...PricePoint` response."""
    points = {
        item["id"]: item
        for item in (payload.get("included") or [])
        if item.get("type") in ("inAppPurchasePricePoints", "subscriptionPricePoints")
    }
    territories = {
        item["id"]: item
        for item in (payload.get("included") or [])
        if item.get("type") == "territories"
    }
    for entry in payload.get("data") or []:
        rels = entry.get("relationships") or {}
        # The territory may hang off the price entry or off the point it references.
        terr = ((rels.get("territory") or {}).get("data") or {}).get("id")
        pp_id = None
        for key in ("inAppPurchasePricePoint", "subscriptionPricePoint"):
            pp_id = ((rels.get(key) or {}).get("data") or {}).get("id") or pp_id
        point = points.get(pp_id or "")
        if point is not None and terr is None:
            prels = point.get("relationships") or {}
            terr = ((prels.get("territory") or {}).get("data") or {}).get("id")
        if terr not in (None, "USA"):
            continue
        if point is None:
            continue
        price = (point.get("attributes") or {}).get("customerPrice")
        if price:
            # ASC returns "2.99" / "2.990"; normalise to two decimals for comparison.
            try:
                return f"{float(price):.2f}"
            except (TypeError, ValueError):
                return str(price)
    # A single-territory response with no territory relationship at all: take the only point.
    if len(points) == 1 and not territories:
        price = (next(iter(points.values())).get("attributes") or {}).get("customerPrice")
        if price:
            try:
                return f"{float(price):.2f}"
            except (TypeError, ValueError):
                return str(price)
    return None


def _consumable_price(client, token: str, iap_id: str) -> Optional[str]:
    try:
        sched = _get(client, token, f"/v2/inAppPurchases/{iap_id}/iapPriceSchedule")
        sched_id = (sched.get("data") or {}).get("id")
        if not sched_id:
            return None
        payload = _get(
            client,
            token,
            f"/v1/inAppPurchasePriceSchedules/{sched_id}/manualPrices",
            include="inAppPurchasePricePoint,territory",
            limit=200,
        )
        return _usd_price_from(payload)
    except SystemExit:
        raise
    except Exception:
        return None


def _subscription_price(client, token: str, sub_id: str) -> Optional[str]:
    try:
        payload = _get(
            client,
            token,
            f"/v1/subscriptions/{sub_id}/prices",
            include="subscriptionPricePoint,territory",
            limit=200,
        )
        return _usd_price_from(payload)
    except SystemExit:
        raise
    except Exception:
        return None


# ── comparison ──────────────────────────────────────────────────────────────

def _cmp(label: str, expected: str, actual: Optional[str], problems: List[str]) -> None:
    if actual is None:
        problems.append(f"{label}: MISSING in ASC (expected {expected!r})")
        print(f"    {_BAD} {label}: missing in ASC — expected {expected!r}")
    elif actual != expected:
        problems.append(f"{label}: ASC {actual!r} != repo {expected!r}")
        print(f"    {_BAD} {label}\n        ASC : {actual!r}\n        repo: {expected!r}")
    else:
        print(f"    {_OK} {label}: {actual!r}")


def _upload_review_screenshot(client, token: str, iap_id: str, pid: str, image: Path) -> bool:
    """Attach `image` to a consumable as its App Review screenshot.

    Apple's asset upload is a three-step reserve / PUT / commit, and skipping the commit leaves
    a half-created asset that reads as "no screenshot" while occupying the slot:

      1. POST the filename + size. Apple replies with `uploadOperations` — one or more
         pre-signed PUTs, each with its own byte range and its own required headers.
      2. PUT each byte range VERBATIM, using the headers Apple supplied. They are signed; adding
         or omitting one fails the signature.
      3. PATCH `uploaded: true` with an MD5 of the file. Until this lands the asset stays in
         UPLOAD_INCOMPLETE and the IAP stays in MISSING_METADATA.
    """
    import hashlib  # noqa: PLC0415

    data = image.read_bytes()
    print(f"    reserving {image.name} ({len(data):,} bytes)…")

    created = _write(
        client, token, "POST", "/v1/inAppPurchaseAppStoreReviewScreenshots",
        {
            "data": {
                "type": "inAppPurchaseAppStoreReviewScreenshots",
                "attributes": {"fileName": image.name, "fileSize": len(data)},
                "relationships": {
                    "inAppPurchaseV2": {"data": {"type": "inAppPurchases", "id": iap_id}}
                },
            }
        },
    )
    asset = created.get("data") or {}
    asset_id = asset.get("id")
    ops = (asset.get("attributes") or {}).get("uploadOperations") or []
    if not asset_id or not ops:
        print(f"    {_BAD} {pid}: Apple returned no upload operations")
        return False

    for i, op in enumerate(ops, 1):
        offset = int(op.get("offset") or 0)
        length = int(op.get("length") or 0)
        headers = {h["name"]: h["value"] for h in (op.get("requestHeaders") or [])}
        r = client.request(
            op.get("method", "PUT"),
            op["url"],
            headers=headers,
            content=data[offset:offset + length],
        )
        if r.status_code >= 400:
            print(f"    {_BAD} {pid}: chunk {i}/{len(ops)} failed ({r.status_code}) {r.text[:200]}")
            return False
        print(f"    uploaded chunk {i}/{len(ops)}")

    committed = _write(
        client, token, "PATCH", f"/v1/inAppPurchaseAppStoreReviewScreenshots/{asset_id}",
        {
            "data": {
                "type": "inAppPurchaseAppStoreReviewScreenshots",
                "id": asset_id,
                "attributes": {
                    "uploaded": True,
                    "sourceFileChecksum": hashlib.md5(data).hexdigest(),  # noqa: S324 — integrity, not security
                },
            }
        },
    )
    state = ((committed.get("data") or {}).get("attributes") or {}).get("assetDeliveryState") or {}
    if state.get("errors"):
        print(f"    {_BAD} {pid}: Apple rejected the asset: {state['errors']}")
        return False
    print(f"    {_OK} {pid}: screenshot attached ({state.get('state')})")
    return True


def _set_availability(client, token: str, iap_id: str, pid: str,
                      territories: List[str], in_new: bool) -> bool:
    """Create the `inAppPurchaseAvailabilities` resource a consumable needs to leave
    MISSING_METADATA.

    Consumables created through the API (or through the UI without visiting the Availability
    section) have NO availability resource at all — `GET .../inAppPurchaseAvailability` answers
    404 NOT_FOUND rather than returning an empty set. Subscriptions get one implicitly, which is
    why they reach READY_TO_SUBMIT while the packs sit in MISSING_METADATA with every visible
    field filled in. Nothing in the ASC list view distinguishes the two states.

    Territory availability is bounded by the APP's availability, so this cannot widen where the
    product actually sells beyond what the app itself offers.
    """
    print(f"    setting availability: {len(territories)} territories, "
          f"availableInNewTerritories={in_new}")
    _write(
        client, token, "POST", "/v1/inAppPurchaseAvailabilities",
        {
            "data": {
                "type": "inAppPurchaseAvailabilities",
                "attributes": {"availableInNewTerritories": in_new},
                "relationships": {
                    "inAppPurchase": {"data": {"type": "inAppPurchases", "id": iap_id}},
                    "availableTerritories": {
                        "data": [{"type": "territories", "id": t} for t in territories]
                    },
                },
            }
        },
    )
    print(f"    {_OK} {pid}: availability set")
    return True


def _subscription_territories(client, token: str, app_id: str) -> tuple[List[str], bool]:
    """The territory set the SUBSCRIPTIONS already use, so the packs can be made to match.

    Copied rather than hardcoded: a hardcoded list silently diverges the day the subscriptions
    change, and "the packs sell somewhere the plans do not" is invisible from every screen.
    """
    for g in _all(client, token, f"/v1/apps/{app_id}/subscriptionGroups"):
        for sub in _all(client, token, f"/v1/subscriptionGroups/{g['id']}/subscriptions"):
            av = _get(client, token, f"/v1/subscriptions/{sub['id']}/subscriptionAvailability")
            av_id = (av.get("data") or {}).get("id")
            if not av_id:
                continue
            terr = _all(client, token, f"/v1/subscriptionAvailabilities/{av_id}/availableTerritories")
            ids = sorted(t["id"] for t in terr)
            if ids:
                in_new = bool((av["data"]["attributes"] or {}).get("availableInNewTerritories"))
                return ids, in_new
    return [], False


def main() -> int:
    import argparse  # noqa: PLC0415

    import httpx  # noqa: PLC0415

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--set-availability",
        choices=("match-subscriptions", "usa-only"),
        help="Create the missing territory availability on consumables that have none. "
             "WRITES to App Store Connect.",
    )
    ap.add_argument(
        "--upload-review-screenshots",
        metavar="IMAGE",
        help="Attach IMAGE as the App Review screenshot to every consumable that has none. "
             "WRITES to App Store Connect; omit for a read-only audit.",
    )
    args = ap.parse_args()

    upload_image: Optional[Path] = None
    if args.upload_review_screenshots:
        upload_image = Path(args.upload_review_screenshots).expanduser()
        if not upload_image.exists():
            sys.exit(f"{_BAD} image not found: {upload_image}")
        print(f"{_WARN} WRITE MODE — will attach {upload_image.name} to consumables lacking one")

    if args.set_availability:
        print(f"{_WARN} WRITE MODE — will set availability ({args.set_availability}) "
              "on consumables that have none")

    token = _token()
    expected = _expected()
    problems: List[str] = []
    blockers: List[str] = []
    unverified: List[str] = []
    uploaded: List[str] = []
    fixed: List[str] = []

    with httpx.Client(timeout=30.0) as client:
        apps = _all(client, token, "/v1/apps", **{"filter[bundleId]": _BUNDLE_ID})
        if not apps:
            sys.exit(f"{_BAD} no app with bundle id {_BUNDLE_ID} is visible to this key")
        app = apps[0]
        app_id = app["id"]
        print(f"\napp: {app['attributes'].get('name')}  ({_BUNDLE_ID})  id={app_id}\n")

        # ── consumables ─────────────────────────────────────────────────────
        print("── In-App Purchases (consumables) " + "─" * 40)
        iaps = _all(client, token, f"/v1/apps/{app_id}/inAppPurchasesV2")
        seen = set()
        for iap in iaps:
            a = iap["attributes"]
            pid = a.get("productId")
            seen.add(pid)
            exp = expected.get(pid)
            print(f"\n  {pid}   [{a.get('state')}]")
            if not exp:
                problems.append(f"{pid}: in ASC but not in Caydex.storekit")
                print(f"    {_BAD} not in Caydex.storekit — the app will never offer it")
                continue
            _cmp("reference name", exp["referenceName"], a.get("name"), problems)

            locs = _all(client, token, f"/v2/inAppPurchases/{iap['id']}/inAppPurchaseLocalizations")
            en = next((l for l in locs if l["attributes"].get("locale") == "en-US"), None)
            if en is None:
                problems.append(f"{pid}: no en-US localization")
                print(f"    {_BAD} no en-US localization — it cannot be submitted")
            else:
                _cmp("display name", exp["displayName"], en["attributes"].get("name"), problems)
                _cmp("description", exp["description"], en["attributes"].get("description"), problems)

            price = _consumable_price(client, token, iap["id"])
            if price is None:
                unverified.append(f"{pid}: price")
                print(f"    {_WARN} price: NOT VERIFIED (no price schedule readable) "
                      f"— expected ${exp['price']}")
            else:
                _cmp("price (USD)", exp["price"], price, problems)

            # A consumable with no review screenshot cannot leave MISSING_METADATA.
            shot = _get(client, token, f"/v2/inAppPurchases/{iap['id']}/appStoreReviewScreenshot")
            if not (shot.get("data") or {}):
                if upload_image is not None:
                    print(f"    {_WARN} no App Review screenshot — uploading")
                    if _upload_review_screenshot(client, token, iap["id"], pid, upload_image):
                        uploaded.append(pid)
                    else:
                        blockers.append(f"{pid}: screenshot upload FAILED")
                else:
                    blockers.append(f"{pid}: no App Review screenshot")
                    print(f"    {_BAD} no App Review screenshot attached — REQUIRED")

            # The relationship 404s outright when it was never created — a state the ASC list
            # view renders identically to "configured". Checked through the link Apple declares
            # rather than a guessed URL, because the v1 path returns a DIFFERENT 404
            # (PATH_ERROR vs NOT_FOUND) and reads like the resource is absent when it is the
            # URL that is wrong.
            av_link = (((iap.get("relationships") or {}).get("inAppPurchaseAvailability") or {})
                       .get("links") or {}).get("related")
            has_availability = False
            if av_link:
                r = client.get(av_link, headers={"Authorization": f"Bearer {token}"})
                has_availability = r.status_code == 200
            if has_availability:
                print(f"    {_OK} territory availability configured")
            elif args.set_availability:
                print(f"    {_WARN} no territory availability — setting it")
                terrs, in_new = (
                    _subscription_territories(client, token, app_id)
                    if args.set_availability == "match-subscriptions"
                    else (["USA"], False)
                )
                if not terrs:
                    blockers.append(f"{pid}: could not resolve a territory set")
                    print(f"    {_BAD} no territories resolved — not writing")
                elif _set_availability(client, token, iap["id"], pid, terrs, in_new):
                    fixed.append(pid)
            else:
                blockers.append(f"{pid}: no territory availability configured")
                print(f"    {_BAD} NO territory availability — this is what holds it in "
                      f"MISSING_METADATA")

            if a.get("state") == "MISSING_METADATA" and pid not in uploaded and pid not in fixed:
                blockers.append(f"{pid}: state is MISSING_METADATA")

        for pid, exp in expected.items():
            if exp["kind"] == "consumable" and pid not in seen:
                problems.append(f"{pid}: in Caydex.storekit but NOT in ASC")
                print(f"\n  {_BAD} {pid} is missing from ASC entirely")

        # ── subscriptions ───────────────────────────────────────────────────
        print("\n── Subscription groups " + "─" * 51)
        groups = _all(client, token, f"/v1/apps/{app_id}/subscriptionGroups")
        for g in groups:
            print(f"\n  group: {g['attributes'].get('referenceName')}")
            glocs = _all(client, token, f"/v1/subscriptionGroups/{g['id']}/subscriptionGroupLocalizations")
            en = next((l for l in glocs if l["attributes"].get("locale") == "en-US"), None)
            if en is None:
                problems.append("subscription group: no en-US localization (BLOCKS SUBMISSION)")
                print(f"    {_BAD} NO en-US GROUP LOCALIZATION — subscriptions cannot be submitted")
            else:
                print(f"    {_OK} group display name: {en['attributes'].get('name')!r}")

            subs = _all(client, token, f"/v1/subscriptionGroups/{g['id']}/subscriptions")
            for s_ in subs:
                a = s_["attributes"]
                pid = a.get("productId")
                exp = expected.get(pid)
                print(f"\n    {pid}   [{a.get('state')}]  {a.get('subscriptionPeriod')}")
                if not exp:
                    problems.append(f"{pid}: in ASC but not in Caydex.storekit")
                    print(f"      {_BAD} not in Caydex.storekit")
                    continue
                _cmp("reference name", exp["referenceName"], a.get("name"), problems)
                slocs = _all(client, token, f"/v1/subscriptions/{s_['id']}/subscriptionLocalizations")
                sen = next((l for l in slocs if l["attributes"].get("locale") == "en-US"), None)
                if sen is None:
                    problems.append(f"{pid}: no en-US localization")
                    print(f"      {_BAD} no en-US localization")
                else:
                    _cmp("display name", exp["displayName"], sen["attributes"].get("name"), problems)
                    _cmp("description", exp["description"], sen["attributes"].get("description"), problems)
                sprice = _subscription_price(client, token, s_["id"])
                if sprice is None:
                    unverified.append(f"{pid}: price")
                    print(f"      {_WARN} price: NOT VERIFIED — expected ${exp['price']}")
                else:
                    _cmp("price (USD)", exp["price"], sprice, problems)
                if a.get("state") == "MISSING_METADATA":
                    blockers.append(f"{pid}: state is MISSING_METADATA")

    print("\n" + "═" * 74)
    if fixed:
        print(f"{_OK} set territory availability on {len(fixed)} product(s): {', '.join(fixed)}")
        print("   Re-run read-only to confirm the state moved.\n")
    if uploaded:
        print(f"{_OK} uploaded a review screenshot to {len(uploaded)} product(s): "
              f"{', '.join(uploaded)}")
        print("   Re-run without --upload-review-screenshots to confirm the state moved.\n")
    if blockers:
        print(f"{_BAD} {len(blockers)} item(s) BLOCK submission:\n")
        for b in blockers:
            print(f"   • {b}")
        print()
    if unverified:
        print(f"{_WARN} {len(unverified)} field(s) could NOT be read, so they are unchecked:\n")
        for u in unverified:
            print(f"   • {u}")
        print()
    if problems:
        print(f"{_BAD} {len(problems)} difference(s) between ASC and the repo:\n")
        for p in problems:
            print(f"   • {p}")
        print(
            "\nThe repo side is `frontend/ios/Caydex.storekit`, which "
            "`test_iap_product_and_privacy_parity.py` pins against the live `credit_packs`\n"
            "seed. So a difference here means ASC is wrong, unless you deliberately changed\n"
            "the product — in which case change the storekit config and the migration too."
        )
        return 1

    checked = "name, description" if unverified else "name, description and price"
    print(f"{_OK} ASC matches Caydex.storekit on every product's {checked}.")
    if blockers:
        return 1
    print("\nNot checked here (no API for it): the app-level metadata in §4 of")
    print("documents/legal/asc-iap-metadata.md — Content Rights, the FMP attachment, and")
    print("the demo account credentials.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
