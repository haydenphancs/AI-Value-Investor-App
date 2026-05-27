"""USPTO Open Data Portal Integration — Patent Counts for Moat Scoring

Used by ip_intel_service (Phase 3C) to fetch patent counts per company,
which feed the Intangible Assets pillar of moat_scoring_service.

API: https://api.uspto.gov/api/v1/patent/applications/search
  - PatentsView was decommissioned in early 2026 and migrated to the
    USPTO Open Data Portal (https://data.uspto.gov/apis). The legacy
    search.patentsview.org hostname no longer resolves.
  - POST endpoint with structured JSON query body
  - Requires an API key (X-API-KEY header). Register at:
      https://data.uspto.gov/apis/getting-started
    Add to backend/.env as USPTO_API_KEY
  - When the key is absent, this integration silently no-ops (returns
    an empty result) so the rest of the report still renders without
    the patents driver contributing.

Public methods are async + return plain Python dicts. The service
layer (ip_intel_service.py) owns caching, _inflight dedup, and the
per-ticker name resolution. This integration is a thin HTTP wrapper
per project rule (.claude/rules/integrations.md).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


# ── Exception hierarchy ────────────────────────────────────────────────


class USPTOException(Exception):
    """Base for USPTO-related errors."""


class USPTORateLimitException(USPTOException):
    """Raised when PatentsView returns a 429."""


class USPTOTimeoutException(USPTOException):
    """Raised when the upstream request times out."""


# ── Client ─────────────────────────────────────────────────────────────


class USPTOClient:
    """Thin async wrapper around USPTO Open Data Portal patent search."""

    BASE_URL = "https://api.uspto.gov"
    SEARCH_PATH = "/api/v1/patent/applications/search"

    def __init__(self) -> None:
        self._client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(30.0, connect=10.0),
                limits=httpx.Limits(
                    max_connections=10, max_keepalive_connections=5,
                ),
            )
        return self._client

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def _api_key(self) -> Optional[str]:
        return getattr(settings, "USPTO_API_KEY", None) or None

    async def get_patents_for_assignee(
        self,
        assignee_name: str,
        *,
        since_year: Optional[int] = None,
        page_size: int = 100,
    ) -> Dict[str, Any]:
        """Fetch recent patents granted to a given assignee organization.

        Args:
            assignee_name: Company name as registered with USPTO (e.g.
                "Oracle Corporation", "Apple Inc.").
            since_year: When set, restrict to patents filed in or after
                this year (used to compute the "recent 5y" count via the
                `applicationMetaData.filingDate` field).
            page_size: Max patents to return per page. ODP caps at 100.

        Returns:
            dict with keys:
              - "total_hits": int — total assignee patent count
              - "patents":   list[dict] — page of patents (subset)
              - "since_year": echoed input for caller's reference
            When the API key is missing or the call fails, returns
            {"total_hits": 0, "patents": [], "error": "<reason>"}.
        """
        if not assignee_name or not isinstance(assignee_name, str):
            return {"total_hits": 0, "patents": [], "error": "empty_assignee"}

        api_key = self._api_key()
        if not api_key:
            logger.info(
                "USPTO: USPTO_API_KEY not set — skipping patent fetch for %s",
                assignee_name,
            )
            return {"total_hits": 0, "patents": [], "error": "no_api_key"}

        # ODP search accepts an OpenSearch-style `q` string. Quoting the
        # assignee name lets multi-word organizations match as a phrase;
        # any embedded double-quote is escaped so it can't break out.
        escaped = assignee_name.replace('\\', '\\\\').replace('"', '\\"')
        q_parts = [f'assignmentBag.assigneeBag.assigneeNameText:"{escaped}"']
        if since_year:
            q_parts.append(
                f'applicationMetaData.filingDate:[{int(since_year)}-01-01 TO 2099-12-31]'
            )
        body: Dict[str, Any] = {
            "q": " AND ".join(q_parts),
            "pagination": {"offset": 0, "limit": min(int(page_size), 100)},
            "sort": [{"field": "applicationMetaData.filingDate", "order": "desc"}],
        }

        url = f"{self.BASE_URL}{self.SEARCH_PATH}"
        headers = {"X-API-KEY": api_key, "Content-Type": "application/json"}

        try:
            client = await self._get_client()
            resp = await client.post(url, json=body, headers=headers)
        except httpx.TimeoutException:
            raise USPTOTimeoutException(
                f"USPTO request timed out for {assignee_name}"
            )
        except Exception as exc:
            logger.warning(
                "USPTO: request failed for %s: %s: %s",
                assignee_name, type(exc).__name__, exc,
            )
            return {"total_hits": 0, "patents": [], "error": str(exc)}

        if resp.status_code == 429:
            raise USPTORateLimitException(
                f"USPTO returned 429 for {assignee_name}"
            )
        # ODP returns 404 with a JSON body when an assignee has zero
        # matches — that's a legitimate "no patents" answer, not an
        # outage. Treat as empty result rather than an error.
        if resp.status_code == 404:
            return {"total_hits": 0, "patents": [], "since_year": since_year}
        if resp.status_code != 200:
            return {
                "total_hits": 0, "patents": [],
                "error": f"http_{resp.status_code}: {resp.text[:200]}",
            }

        try:
            data = resp.json()
        except Exception as exc:
            return {"total_hits": 0, "patents": [], "error": f"json_parse: {exc}"}

        # ODP response shape: {count, patentFileWrapperDataBag, requestIdentifier}.
        total_hits = int(data.get("count") or 0)
        patents = data.get("patentFileWrapperDataBag") or []
        if not isinstance(patents, list):
            patents = []
        return {
            "total_hits": total_hits,
            "patents": patents,
            "since_year": since_year,
        }


# ── Singleton ──────────────────────────────────────────────────────────


_client_singleton: Optional[USPTOClient] = None


def get_uspto_client() -> USPTOClient:
    global _client_singleton
    if _client_singleton is None:
        _client_singleton = USPTOClient()
    return _client_singleton


async def close_uspto_client() -> None:
    """Tear-down hook for app.main lifespan."""
    global _client_singleton
    if _client_singleton is not None:
        await _client_singleton.close()
        _client_singleton = None


# Convenience helper — what ip_intel_service typically wants.
def current_year() -> int:
    return datetime.now(timezone.utc).year
