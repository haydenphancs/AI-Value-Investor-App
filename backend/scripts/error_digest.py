#!/usr/bin/env python3
"""
Fetch + group recent Sentry issues for the daily error-triage digest.

This is the DETERMINISTIC half of the error-monitoring pipeline (see the plan):
it pulls the last N unresolved issues from the Sentry API and shapes them into a
compact list. A scheduled Claude Routine then runs this, correlates the top
issues with the codebase (file:line + root cause), and sends a digest via
push/email. Pure I/O — no LLM here — so it's cheap and testable on its own.

Standalone by design: reads its (read-only) creds straight from the ENVIRONMENT,
NOT from app.config, so it runs in a bare scheduled environment without the
full backend .env.

Usage (from backend/):
    python -m scripts.error_digest                 # last 24h, human-readable
    python -m scripts.error_digest --json          # machine-readable (for the agent)
    python -m scripts.error_digest --period 7d --limit 50

Required env (read-only, no write access to anything):
    SENTRY_API_TOKEN   Sentry auth token with project:read + event:read
    SENTRY_ORG         org slug
    SENTRY_PROJECT     project slug
    SENTRY_BASE_URL    optional; defaults to https://sentry.io (set for self-hosted)
"""

import argparse
import asyncio
import json
import logging
import os
import re
import sys
from typing import Any

import httpx

# Best-effort: load backend/.env for LOCAL runs so `python -m scripts.error_digest`
# picks up SENTRY_* without manual export. Harmless if python-dotenv or the file is
# absent — the scheduled environment supplies the creds as real env vars instead.
try:
    from dotenv import load_dotenv

    load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))
except Exception:  # noqa: BLE001
    pass

logger = logging.getLogger("error_digest")

_SENTRY_BASE = os.environ.get("SENTRY_BASE_URL", "https://sentry.io").rstrip("/")

# Defense-in-depth: the backend now redacts secrets before they reach Sentry, but
# scrub API keys / tokens from issue text HERE too, so the digest can never forward a
# leaked key to Discord. (Mirrors app/log_redaction.redact_secrets — kept standalone
# so this script has no app imports.)
_SECRET_QS_RE = re.compile(
    r"(?i)([?&](?:api[_-]?key|token|access[_-]?token|secret|password|key)=)[^&\s'\"]+"
)


def _redact(text: Any) -> Any:
    return _SECRET_QS_RE.sub(r"\1***", text) if isinstance(text, str) else text


async def fetch_issues(period: str, limit: int) -> list[dict[str, Any]]:
    """Return raw Sentry issue dicts for the window, most-frequent first."""
    token = os.environ.get("SENTRY_API_TOKEN")
    org = os.environ.get("SENTRY_ORG")
    project = os.environ.get("SENTRY_PROJECT")
    missing = [
        name
        for name, val in (
            ("SENTRY_API_TOKEN", token),
            ("SENTRY_ORG", org),
            ("SENTRY_PROJECT", project),
        )
        if not val
    ]
    if missing:
        raise SystemExit(
            f"Missing required env: {', '.join(missing)} "
            "(set them in backend/.env for local testing, or in the Routine's run env)."
        )

    url = f"{_SENTRY_BASE}/api/0/projects/{org}/{project}/issues/"
    params = {
        "statsPeriod": period,
        "query": "is:unresolved",
        "sort": "freq",
        "limit": str(limit),
    }
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(url, params=params, headers=headers)
        resp.raise_for_status()
        data = resp.json()
    return data if isinstance(data, list) else []


def shape(issue: dict[str, Any]) -> dict[str, Any]:
    """Compact projection of a Sentry issue for the triage agent."""
    meta = issue.get("metadata") or {}
    return {
        "id": issue.get("id"),
        "title": _redact(issue.get("title") or meta.get("type") or "(untitled)"),
        "type": meta.get("type"),          # e.g. "FMPRateLimitException"
        "value": _redact(meta.get("value")),   # exception message (secrets scrubbed)
        "culprit": issue.get("culprit"),   # e.g. "app.services.x in fn" → the code site
        "level": issue.get("level"),
        "count": int(issue.get("count") or 0),
        "users": int(issue.get("userCount") or 0),
        "first_seen": issue.get("firstSeen"),
        "last_seen": issue.get("lastSeen"),
        "permalink": issue.get("permalink"),
    }


def _build_discord_embed(issues: list[dict[str, Any]], period: str) -> dict[str, Any]:
    """Format the digest as one Discord embed (markdown links render inside embeds)."""
    if not issues:
        return {"embeds": [{
            "title": f"Caydex error digest — last {period}",
            "description": "No unresolved Sentry issues. All clear. ✅",
            "color": 0x2ECC71,  # green
        }]}
    shown = issues[:10]
    blocks = []
    for n, i in enumerate(shown, 1):
        title = (i["title"] or "(untitled)")[:150]
        link = i.get("permalink") or ""
        head = f"**{n}. [{title}]({link})**" if link else f"**{n}. {title}**"
        meta = f"{i['count']}× · {i['users']} users"
        culprit = f"\n`{i['culprit']}`" if i.get("culprit") else ""
        blocks.append(f"{head}\n{meta}{culprit}")
    desc = "\n\n".join(blocks)
    if len(issues) > len(shown):
        desc += f"\n\n…and {len(issues) - len(shown)} more."
    if len(desc) > 4000:
        desc = desc[:4000] + "…"
    plural = "s" if len(issues) != 1 else ""
    return {"embeds": [{
        "title": f"Caydex error digest — last {period} ({len(issues)} issue{plural})",
        "description": desc,
        "color": 0xE74C3C,  # red
    }]}


async def post_to_discord(issues: list[dict[str, Any]], period: str) -> None:
    """POST the digest embed to the DISCORD_WEBHOOK_URL channel."""
    webhook = os.environ.get("DISCORD_WEBHOOK_URL")
    if not webhook:
        raise SystemExit("DISCORD_WEBHOOK_URL not set (add it to backend/.env or the run env).")
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(webhook, json=_build_discord_embed(issues, period))
        resp.raise_for_status()


async def main(args: argparse.Namespace) -> int:
    try:
        raw = await fetch_issues(args.period, args.limit)
    except httpx.HTTPStatusError as e:
        logger.error("Sentry API %s: %s", e.response.status_code, e.response.text[:200])
        return 2
    except httpx.HTTPError as e:
        logger.error("Sentry request failed: %s: %s", type(e).__name__, e)
        return 2

    issues = [shape(i) for i in raw if isinstance(i, dict)]
    issues.sort(key=lambda x: (-x["count"], x["last_seen"] or ""))

    if args.discord:
        try:
            await post_to_discord(issues, args.period)
            logger.info("Posted digest to Discord (%d issue(s)).", len(issues))
        except httpx.HTTPError as e:
            logger.error("Discord post failed: %s: %s", type(e).__name__, e)
            return 2

    if args.json:
        print(json.dumps(
            {"period": args.period, "count": len(issues), "issues": issues},
            indent=2,
        ))
        return 0

    if not issues:
        print(f"No unresolved Sentry issues in the last {args.period}. ✅")
        return 0

    print(f"{len(issues)} unresolved issue(s) in the last {args.period} (by frequency):\n")
    for n, i in enumerate(issues, 1):
        print(f"{n:>2}. [{i['count']}x · {i['users']} users] {i['title']}")
        if i["culprit"]:
            print(f"      at: {i['culprit']}")
        print(f"      last: {i['last_seen']}  ·  {i['permalink']}")
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    p = argparse.ArgumentParser(
        description="Fetch + group recent Sentry issues for the daily triage digest."
    )
    p.add_argument("--period", default="24h", help="Sentry statsPeriod (e.g. 24h, 7d). Default 24h.")
    p.add_argument("--limit", type=int, default=25, help="Max issues to fetch. Default 25.")
    p.add_argument("--discord", action="store_true", help="Also POST the digest to DISCORD_WEBHOOK_URL.")
    p.add_argument("--json", action="store_true", help="Emit JSON (for the triage agent).")
    sys.exit(asyncio.run(main(p.parse_args())))
