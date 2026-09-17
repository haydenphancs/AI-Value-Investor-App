#!/usr/bin/env python3
"""Pull TestFlight feedback — screenshot submissions AND crash submissions — out of App Store
Connect into a local, gitignored folder so the whole backlog can be read and triaged offline.

WHY
---
TestFlight feedback only lives in the App Store Connect web UI, one item per click, and the
screenshot URLs Apple hands out EXPIRE. Reading "a lot of" feedback that way is slow and nothing
persists. This script drains the two feedback endpoints (App Store Connect API 4.0, 2025) into
one folder per submission — `meta.json` + the images / crash log — plus an `index.json` that a
triage pass can walk without touching the network again.

It is READ-ONLY. It issues only GETs. (The API also offers DELETE on both resources; nothing
here calls it.)

CREDENTIALS
-----------
The same key `scripts/asc_audit.py` uses. Three variables, read from the environment first and
then from `backend/.env` (gitignored) so the script can run from a tool shell with nothing
exported:

    ASC_KEY_ID=XXXXXXXXXX
    ASC_ISSUER_ID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
    ASC_PRIVATE_KEY_PATH=~/.appstoreconnect/private_keys/AuthKey_XXXXXXXXXX.p8

The token is minted per run, expires in 15 minutes (Apple's cap is 20), and is never printed.
TestFlight feedback is readable by Developer and above; the audit key is App Manager.

RUN
---
    cd backend && ./venv/bin/python scripts/pull_testflight_feedback.py
        [--out ../.testflight]   # default; MUST stay gitignored — it holds tester emails
        [--no-crashes]           # screenshots only
        [--since 2026-09-01]     # client-side filter on createdDate
        [--limit 200]            # per-page size, Apple caps at 200

OUTPUT
------
    <out>/
      index.json                          one row per item, both kinds, newest first
      feedback/<createdDate>_<id>/        one folder per screenshot submission
          meta.json                       every attribute + build + tester
          screenshot_1.png …              downloaded in the same run (URLs expire)
      crashes/<createdDate>_<id>/
          meta.json
          crash.txt                       BetaCrashLog.logText, as Apple returns it

Idempotent: a folder that already holds its `meta.json` is skipped, so re-running only fetches
what is new. `index.json` is rebuilt from disk every run, so it is always complete.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import httpx

_REPO = Path(__file__).resolve().parents[2]
_BACKEND_ENV = _REPO / "backend" / ".env"
_BUNDLE_ID = "com.phan.caydex"
_API = "https://api.appstoreconnect.apple.com"

_OK = "\033[32m✓\033[0m"
_BAD = "\033[31m✗\033[0m"
_WARN = "\033[33m!\033[0m"

_KEY_NAMES = ("ASC_KEY_ID", "ASC_ISSUER_ID", "ASC_PRIVATE_KEY_PATH")

# Screenshot + crash submissions share this attribute list (crash has no `screenshots`).
_FEEDBACK_FIELDS = (
    "createdDate,comment,email,deviceModel,osVersion,locale,timeZone,architecture,"
    "connectionType,pairedAppleWatch,appUptimeInMilliseconds,diskBytesAvailable,"
    "diskBytesTotal,batteryPercentage,screenWidthInPoints,screenHeightInPoints,"
    "appPlatform,devicePlatform,deviceFamily,buildBundleId"
)
_BUILD_FIELDS = "version,uploadedDate,expired,processingState,preReleaseVersion"
_TESTER_FIELDS = "firstName,lastName,email"


# ── credentials ─────────────────────────────────────────────────────────────

def _credentials() -> Dict[str, str]:
    """ASC_* from the environment, else from backend/.env. Values are never printed."""
    values = {name: os.environ.get(name, "").strip() for name in _KEY_NAMES}
    if all(values.values()):
        return values

    if _BACKEND_ENV.exists():
        try:
            from dotenv import dotenv_values  # noqa: PLC0415
        except ImportError:  # pragma: no cover - python-dotenv ships with pydantic-settings
            dotenv_values = None  # type: ignore[assignment]
        if dotenv_values is not None:
            file_values = dotenv_values(_BACKEND_ENV)
            for name in _KEY_NAMES:
                if not values[name]:
                    values[name] = (file_values.get(name) or "").strip()

    missing = [name for name in _KEY_NAMES if not values[name]]
    if missing:
        sys.exit(
            f"{_BAD} missing {', '.join(missing)} — export them or add them to "
            f"backend/.env (see the CREDENTIALS block at the top of this file)"
        )
    return values


def _token() -> str:
    """A short-lived ES256 JWT for the ASC API. Same shape checks as asc_audit.py."""
    from jose import jwt  # noqa: PLC0415

    creds = _credentials()
    key_id, issuer, key_path = (creds[n] for n in _KEY_NAMES)

    looks_like_uuid = re.fullmatch(r"[0-9a-fA-F-]{36}", issuer) is not None
    key_looks_like_uuid = re.fullmatch(r"[0-9a-fA-F-]{36}", key_id) is not None
    if key_looks_like_uuid and not looks_like_uuid:
        sys.exit(
            f"{_BAD} ASC_KEY_ID and ASC_ISSUER_ID look swapped.\n"
            f"    ASC_KEY_ID is a UUID — that is the Issuer ID, shown ABOVE the keys table.\n"
            f"    The Key ID is the 10-character value in the KEY ID column (AuthKey_<KEY ID>.p8)."
        )
    if not looks_like_uuid:
        sys.exit(
            f"{_BAD} ASC_ISSUER_ID is not a UUID. It is the 36-character value shown above the "
            f"keys table on Users and Access -> Integrations -> App Store Connect API."
        )
    if not re.fullmatch(r"[A-Z0-9]{8,12}", key_id):
        print(f"{_WARN} ASC_KEY_ID {key_id!r} is not the usual 10 uppercase alphanumerics")

    path = Path(key_path).expanduser()
    if not path.exists():
        sys.exit(f"{_BAD} private key not found at {path}")
    m = re.search(r"AuthKey_([A-Z0-9]+)\.p8$", path.name)
    if m and m.group(1) != key_id:
        sys.exit(
            f"{_BAD} ASC_KEY_ID is {key_id}, but the key file is {path.name} "
            f"(key {m.group(1)}). They must be the same key."
        )
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

def _get(client: httpx.Client, token: str, path: str, **params: Any) -> Dict[str, Any]:
    url = path if path.startswith("http") else f"{_API}{path}"
    r = client.get(url, headers={"Authorization": f"Bearer {token}"}, params=params or None)
    if r.status_code == 401:
        sys.exit(f"{_BAD} 401 from ASC — the key, issuer or .p8 do not match, or the key was revoked")
    if r.status_code == 403:
        sys.exit(
            f"{_BAD} 403 from ASC on {path} — the key's role cannot read TestFlight feedback "
            "(needs Developer, App Manager or Admin)"
        )
    if r.status_code == 429:
        sys.exit(f"{_BAD} 429 from ASC on {path} — rate limited; wait a minute and re-run (idempotent)")
    if r.status_code >= 400:
        sys.exit(f"{_BAD} {r.status_code} from ASC on {path}: {r.text[:300]}")
    return r.json()


def _all(client: httpx.Client, token: str, path: str, **params: Any) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Follow `links.next` — ASC pages at 50 by default and silently truncates otherwise.

    Returns (data, included). `included` is accumulated across pages because the build /
    tester a later page references may only be included on that page.
    """
    data: List[Dict[str, Any]] = []
    included: List[Dict[str, Any]] = []
    page = _get(client, token, path, **params)
    while True:
        data.extend(page.get("data") or [])
        included.extend(page.get("included") or [])
        nxt = (page.get("links") or {}).get("next")
        if not nxt:
            return data, included
        page = _get(client, token, nxt)


def _download(client: httpx.Client, url: str, dest_stem: Path) -> Optional[Path]:
    """Fetch a signed screenshot URL. No bearer header — it is a third-party signed link, and
    the signature is the credential. Extension comes from Content-Type, then the URL."""
    try:
        r = client.get(url, follow_redirects=True)
    except httpx.HTTPError as e:
        print(f"{_WARN} download failed ({type(e).__name__}: {e}) for {dest_stem.name}")
        return None
    if r.status_code >= 400:
        print(f"{_WARN} {r.status_code} downloading {dest_stem.name} — the URL may have expired")
        return None
    ctype = (r.headers.get("content-type") or "").split(";")[0].strip().lower()
    ext = {
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "image/jpg": ".jpg",
        "image/heic": ".heic",
        "image/webp": ".webp",
    }.get(ctype)
    if not ext:
        m = re.search(r"\.(png|jpe?g|heic|webp)(?:[?#]|$)", url, re.IGNORECASE)
        ext = f".{m.group(1).lower()}" if m else ".bin"
        if ext == ".jpeg":
            ext = ".jpg"
    dest = dest_stem.with_suffix(ext)
    dest.write_bytes(r.content)
    return dest


# ── shaping ─────────────────────────────────────────────────────────────────

def _index_included(included: Iterable[Dict[str, Any]]) -> Dict[Tuple[str, str], Dict[str, Any]]:
    return {(row.get("type", ""), row.get("id", "")): row for row in included}


def _rel_id(item: Dict[str, Any], name: str) -> Optional[str]:
    rel = ((item.get("relationships") or {}).get(name) or {}).get("data") or {}
    return rel.get("id")


def _folder_name(created: str, item_id: str) -> str:
    # createdDate is ISO-8601 with offset; keep it sortable and filesystem-safe.
    stamp = re.sub(r"[^0-9T]", "", created.split("+")[0].split(".")[0]) or "unknown"
    return f"{stamp}_{item_id}"


def _shape_meta(
    kind: str,
    item: Dict[str, Any],
    included: Dict[Tuple[str, str], Dict[str, Any]],
) -> Dict[str, Any]:
    attrs = dict(item.get("attributes") or {})
    build_id = _rel_id(item, "build")
    tester_id = _rel_id(item, "tester")
    build = (included.get(("builds", build_id or "")) or {}).get("attributes") or {}
    tester = (included.get(("betaTesters", tester_id or "")) or {}).get("attributes") or {}
    return {
        "id": item.get("id"),
        "kind": kind,
        **attrs,
        "build": {
            "id": build_id,
            "buildNumber": build.get("version"),
            "appVersion": build.get("preReleaseVersion"),
            "uploadedDate": build.get("uploadedDate"),
            "expired": build.get("expired"),
        },
        "tester": {
            "id": tester_id,
            "firstName": tester.get("firstName"),
            "lastName": tester.get("lastName"),
            "email": tester.get("email") or attrs.get("email"),
        },
    }


def _since_ok(created: Optional[str], since: Optional[datetime]) -> bool:
    if since is None or not created:
        return True
    try:
        when = datetime.fromisoformat(created.replace("Z", "+00:00"))
    except ValueError:
        return True
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return when >= since


# ── the two pulls ───────────────────────────────────────────────────────────

def _pull_screenshots(
    client: httpx.Client, token: str, app_id: str, out: Path, since: Optional[datetime], limit: int
) -> Tuple[int, int]:
    data, included_rows = _all(
        client,
        token,
        f"/v1/apps/{app_id}/betaFeedbackScreenshotSubmissions",
        **{
            "include": "build,tester",
            "sort": "-createdDate",
            "limit": limit,
            "fields[betaFeedbackScreenshotSubmissions]": _FEEDBACK_FIELDS + ",screenshots,build,tester",
            "fields[builds]": _BUILD_FIELDS,
            "fields[betaTesters]": _TESTER_FIELDS,
        },
    )
    included = _index_included(included_rows)
    root = out / "feedback"
    root.mkdir(parents=True, exist_ok=True)

    total = new = 0
    for item in data:
        attrs = item.get("attributes") or {}
        if not _since_ok(attrs.get("createdDate"), since):
            continue
        total += 1
        folder = root / _folder_name(attrs.get("createdDate") or "", item["id"])
        if (folder / "meta.json").exists():
            continue
        folder.mkdir(parents=True, exist_ok=True)

        meta = _shape_meta("screenshot", item, included)
        files: List[str] = []
        shots = attrs.get("screenshots") or []
        for n, shot in enumerate(shots, start=1):
            url = (shot or {}).get("url")
            if not url:
                continue
            saved = _download(client, url, folder / f"screenshot_{n}")
            if saved:
                files.append(saved.name)
        if shots and not files:
            print(f"{_WARN} {item['id']}: {len(shots)} screenshot URL(s) but nothing downloaded")
        # The signed URLs are useless after expiry; keep dimensions, drop the links.
        meta["screenshots"] = [
            {"width": (s or {}).get("width"), "height": (s or {}).get("height")} for s in shots
        ]
        meta["files"] = files
        (folder / "meta.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False))
        new += 1
    return total, new


def _pull_crashes(
    client: httpx.Client, token: str, app_id: str, out: Path, since: Optional[datetime], limit: int
) -> Tuple[int, int]:
    data, included_rows = _all(
        client,
        token,
        f"/v1/apps/{app_id}/betaFeedbackCrashSubmissions",
        **{
            "include": "build,tester",
            "sort": "-createdDate",
            "limit": limit,
            "fields[betaFeedbackCrashSubmissions]": _FEEDBACK_FIELDS + ",build,tester",
            "fields[builds]": _BUILD_FIELDS,
            "fields[betaTesters]": _TESTER_FIELDS,
        },
    )
    included = _index_included(included_rows)
    root = out / "crashes"
    root.mkdir(parents=True, exist_ok=True)

    total = new = 0
    for item in data:
        attrs = item.get("attributes") or {}
        if not _since_ok(attrs.get("createdDate"), since):
            continue
        total += 1
        folder = root / _folder_name(attrs.get("createdDate") or "", item["id"])
        if (folder / "meta.json").exists():
            continue
        folder.mkdir(parents=True, exist_ok=True)

        meta = _shape_meta("crash", item, included)
        files: List[str] = []
        log = _get(client, token, f"/v1/betaFeedbackCrashSubmissions/{item['id']}/crashLog")
        text = ((log.get("data") or {}).get("attributes") or {}).get("logText")
        if text:
            (folder / "crash.txt").write_text(text)
            files.append("crash.txt")
        else:
            print(f"{_WARN} {item['id']}: crashLog returned no logText")
        meta["files"] = files
        (folder / "meta.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False))
        new += 1
    return total, new


# ── build → app version ─────────────────────────────────────────────────────
#
# `preReleaseVersion` is a RELATIONSHIP on a build, not an attribute, so the list call above
# returns only its id. Resolve it once per distinct build and back-fill every meta.json still
# carrying `appVersion: null` — including folders written by an earlier run, so the pull
# self-heals rather than leaving the first 46 items permanently unlabelled.

def _backfill_app_versions(client: httpx.Client, token: str, out: Path) -> int:
    cache: Dict[str, Optional[str]] = {}
    fixed = 0
    for meta_path in sorted(out.glob("*/*/meta.json")):
        try:
            meta = json.loads(meta_path.read_text())
        except (OSError, ValueError):
            continue  # _rebuild_index warns about it
        build = meta.get("build") or {}
        build_id = build.get("id")
        if not build_id or build.get("appVersion"):
            continue
        if build_id not in cache:
            page = _get(
                client, token, f"/v1/builds/{build_id}/preReleaseVersion",
                **{"fields[preReleaseVersions]": "version"},
            )
            cache[build_id] = ((page.get("data") or {}).get("attributes") or {}).get("version")
            if not cache[build_id]:
                print(f"{_WARN} build {build_id}: no preReleaseVersion.version in the response")
        if cache[build_id]:
            build["appVersion"] = cache[build_id]
            meta["build"] = build
            meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False))
            fixed += 1
    return fixed


# ── index ───────────────────────────────────────────────────────────────────

def _rebuild_index(out: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for sub in ("feedback", "crashes"):
        root = out / sub
        if not root.exists():
            continue
        for meta_path in sorted(root.glob("*/meta.json")):
            try:
                meta = json.loads(meta_path.read_text())
            except (OSError, ValueError) as e:
                print(f"{_WARN} unreadable {meta_path}: {type(e).__name__}: {e}")
                continue
            rows.append(
                {
                    "id": meta.get("id"),
                    "kind": meta.get("kind"),
                    "createdDate": meta.get("createdDate"),
                    "appVersion": (meta.get("build") or {}).get("appVersion"),
                    "buildNumber": (meta.get("build") or {}).get("buildNumber"),
                    "deviceModel": meta.get("deviceModel"),
                    "osVersion": meta.get("osVersion"),
                    "locale": meta.get("locale"),
                    "comment": meta.get("comment"),
                    "dir": str(meta_path.parent.relative_to(out)),
                    "files": meta.get("files") or [],
                }
            )
    rows.sort(key=lambda r: r.get("createdDate") or "", reverse=True)
    (out / "index.json").write_text(json.dumps(rows, indent=2, ensure_ascii=False))
    return rows


# ── main ────────────────────────────────────────────────────────────────────

def _parse_since(raw: Optional[str]) -> Optional[datetime]:
    if not raw:
        return None
    try:
        when = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        sys.exit(f"{_BAD} --since must be ISO-8601 (e.g. 2026-09-01 or 2026-09-01T00:00:00Z)")
    return when if when.tzinfo else when.replace(tzinfo=timezone.utc)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", default=str(_REPO / "backend" / ".testflight"),
                    help="output folder (default backend/.testflight — gitignored)")
    ap.add_argument("--no-crashes", action="store_true", help="skip crash submissions")
    ap.add_argument("--since", default=None, help="only items created at/after this ISO date")
    ap.add_argument("--limit", type=int, default=200, help="page size (Apple caps at 200)")
    args = ap.parse_args()

    out = Path(args.out).expanduser().resolve()
    out.mkdir(parents=True, exist_ok=True)
    try:
        rel = out.relative_to(_REPO)
        if not any(part.startswith(".") for part in rel.parts):
            print(f"{_WARN} {rel} is inside the repo and not a dot-folder — make sure it is gitignored "
                  "(it will contain tester emails)")
    except ValueError:
        pass

    since = _parse_since(args.since)
    limit = max(1, min(args.limit, 200))
    token = _token()

    with httpx.Client(timeout=httpx.Timeout(60.0, connect=10.0)) as client:
        apps, _ = _all(client, token, "/v1/apps", **{"filter[bundleId]": _BUNDLE_ID})
        if not apps:
            sys.exit(f"{_BAD} no app with bundle id {_BUNDLE_ID} visible to this key")
        app_id = apps[0]["id"]
        print(f"{_OK} app {_BUNDLE_ID} → id {app_id}")

        shots_total, shots_new = _pull_screenshots(client, token, app_id, out, since, limit)
        print(f"{_OK} screenshot feedback: {shots_total} ({shots_new} new)")

        if args.no_crashes:
            crash_total = crash_new = 0
        else:
            crash_total, crash_new = _pull_crashes(client, token, app_id, out, since, limit)
            print(f"{_OK} crash feedback: {crash_total} ({crash_new} new)")

        fixed = _backfill_app_versions(client, token, out)
        if fixed:
            print(f"{_OK} app version back-filled on {fixed} item(s)")

    rows = _rebuild_index(out)
    builds = sorted(
        {f"{r.get('appVersion')} ({r.get('buildNumber')})" for r in rows if r.get("buildNumber")}
    )
    print(
        f"{_OK} index.json: {len(rows)} items on disk under {out} — "
        f"builds: {', '.join(builds) if builds else 'n/a'}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
