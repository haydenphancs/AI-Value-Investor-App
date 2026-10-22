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
  - Only `count` is read, so the search asks for ONE record. Every record
    is a full file wrapper (11-141 KB each on 2026-10-21), and the old
    100-record page went over the API's 6 MB response cap for Google,
    Tesla, NIKE, Lucid and Archer Aviation: HTTP 413 on every collection.

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


class USPTORejectedException(USPTOException):
    """A definite refusal: a 4xx other than 404 (no matches), 408 and 429.
    The same query is refused again, so the caller may stop asking for a
    while (a 413 was the live case: the response went over the 6 MB cap)."""

    def __init__(self, message: str, *, status_code: int) -> None:
        super().__init__(message)
        self.status_code = status_code


class USPTOUnavailableException(USPTOException):
    """Transient: a 5xx or 408, a network error, or a 200 whose body has no
    usable `count`. Worth asking again on the next collection."""

    def __init__(self, message: str, *, status_code: Optional[int] = None) -> None:
        super().__init__(message)
        self.status_code = status_code


# ── Client ─────────────────────────────────────────────────────────────


# The search's page size. Only `count` is read, and it comes with any page.
COUNT_PAGE_LIMIT = 1


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

    async def count_patents_for_assignee(
        self,
        assignee_name: str,
        *,
        since_year: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Count the patent applications of a given assignee organization.

        Args:
            assignee_name: Company name as registered with USPTO (e.g.
                "Oracle Corporation", "Apple Inc.").
            since_year: When set, restrict to patents filed in or after
                this year (used to compute the "recent 5y" count via the
                `applicationMetaData.filingDate` field).

        Returns:
            {"total_hits": int, "since_year": <echoed input>}; a 404 (no
            matches) is a total of 0. Without an API key or an assignee no
            call is made: {"total_hits": None, ..., "error": "<reason>"}.

        Raises:
            USPTORateLimitException: 429.
            USPTOTimeoutException: the request timed out.
            USPTORejectedException: any other 4xx but 408 (definite).
            USPTOUnavailableException: 5xx, 408, network error, or a 200
                without a usable `count` (transient).
        """
        if not assignee_name or not isinstance(assignee_name, str):
            return {"total_hits": None, "since_year": since_year, "error": "empty_assignee"}

        api_key = self._api_key()
        if not api_key:
            logger.info(
                "USPTO: USPTO_API_KEY not set — skipping patent fetch for %s",
                assignee_name,
            )
            return {"total_hits": None, "since_year": since_year, "error": "no_api_key"}

        # ODP search accepts an OpenSearch-style `q` string. Quoting the
        # assignee name lets multi-word organizations match as a phrase;
        # any embedded double-quote is escaped so it can't break out.
        escaped = assignee_name.replace('\\', '\\\\').replace('"', '\\"')
        q_parts = [f'assignmentBag.assigneeBag.assigneeNameText:"{escaped}"']
        if since_year:
            q_parts.append(
                f'applicationMetaData.filingDate:[{int(since_year)}-01-01 TO 2099-12-31]'
            )
        # One record, never more: `count` is the whole answer and comes with
        # any page size (module docstring: 100 records answered 413).
        body: Dict[str, Any] = {
            "q": " AND ".join(q_parts),
            "pagination": {"offset": 0, "limit": COUNT_PAGE_LIMIT},
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
        except httpx.HTTPError as exc:
            raise USPTOUnavailableException(
                f"USPTO request failed for {assignee_name}: {type(exc).__name__}: {exc}"
            ) from exc

        status = resp.status_code
        if status == 429:
            raise USPTORateLimitException(
                f"USPTO returned 429 for {assignee_name}"
            )
        # ODP returns 404 with a JSON body when an assignee has zero
        # matches — that's a legitimate "no patents" answer, not an
        # outage. Treat as empty result rather than an error.
        if status == 404:
            return {"total_hits": 0, "since_year": since_year}
        if status != 200:
            # The body names the reason (the 413's says "Response payload
            # exceeds allowed limit of 6MB"); folded onto one line and capped.
            detail = " ".join(resp.text[:200].split())
            message = f"USPTO answered HTTP {status} for {assignee_name}: {detail}"
            if 400 <= status < 500 and status != 408:
                raise USPTORejectedException(message, status_code=status)
            raise USPTOUnavailableException(message, status_code=status)

        try:
            data = resp.json()
        except ValueError as exc:
            raise USPTOUnavailableException(
                f"USPTO answered 200 with a body that is not JSON for "
                f"{assignee_name}: {type(exc).__name__}",
                status_code=status,
            ) from exc

        # ODP response shape: {count, patentFileWrapperDataBag, requestIdentifier}.
        # A missing or odd count is an unknown, never a count of 0.
        count = data.get("count") if isinstance(data, dict) else None
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise USPTOUnavailableException(
                f"USPTO answered 200 without a usable count for "
                f"{assignee_name}: {repr(count)[:80]}",
                status_code=status,
            )
        return {"total_hits": count, "since_year": since_year}


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
