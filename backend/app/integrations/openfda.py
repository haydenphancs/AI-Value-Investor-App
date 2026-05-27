"""OpenFDA Integration — Drug Approval Counts for Moat Scoring

Used by ip_intel_service (Phase 3C) to fetch active drug approvals
per pharmaceutical company, which feeds the Intangible Assets pillar
of moat_scoring_service.

API: https://api.fda.gov/
  - Public, no API key required (free key boosts the rate limit
    from 240 req/hour to 120K req/hour — out of scope for v1)
  - Drug-approval endpoint: /drug/drugsfda.json
  - Search by sponsor_name to get approvals per company

Per project rule (.claude/rules/integrations.md), the integration is a
thin HTTP wrapper. Caching, _inflight dedup, and the per-ticker
orchestration live in ip_intel_service.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)


# ── Exception hierarchy ────────────────────────────────────────────────


class OpenFDAException(Exception):
    """Base for OpenFDA-related errors."""


class OpenFDARateLimitException(OpenFDAException):
    """Raised when OpenFDA returns a 429."""


class OpenFDATimeoutException(OpenFDAException):
    """Raised when the upstream request times out."""


# ── Client ─────────────────────────────────────────────────────────────


class OpenFDAClient:
    """Thin async wrapper around the OpenFDA Drug Approval endpoint."""

    BASE_URL = "https://api.fda.gov"

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

    async def get_drug_approvals(
        self,
        sponsor_name: str,
        *,
        limit: int = 100,
    ) -> Dict[str, Any]:
        """Fetch recent drug approvals for a given sponsor (pharma
        company name as registered with FDA).

        Args:
            sponsor_name: Company name (e.g. "PFIZER INC", "Eli Lilly").
                FDA's sponsor_name field is typically uppercased; the
                Lucene search is case-insensitive.
            limit: Max approvals to return. Capped at 1000 by OpenFDA.

        Returns:
            dict with keys:
              - "total_hits": int — total approval count
              - "approvals": list[dict] — page of approval records
              - "active_count": int — approvals with at least one
                product that isn't marked discontinued
            On 404 (no results), returns {total_hits: 0, ...} without
            raising. On other errors, returns {error: "..."} so the
            caller can degrade gracefully.
        """
        if not sponsor_name or not isinstance(sponsor_name, str):
            return {"total_hits": 0, "approvals": [], "active_count": 0,
                    "error": "empty_sponsor"}

        url = f"{self.BASE_URL}/drug/drugsfda.json"
        # Lucene query with exact phrase match — OpenFDA returns
        # everything containing the phrase in sponsor_name.
        params = {
            "search": f'sponsor_name:"{sponsor_name}"',
            "limit": min(int(limit), 1000),
        }

        try:
            client = await self._get_client()
            resp = await client.get(url, params=params)
        except httpx.TimeoutException:
            raise OpenFDATimeoutException(
                f"OpenFDA request timed out for {sponsor_name}"
            )
        except Exception as exc:
            logger.warning(
                "OpenFDA: request failed for %s: %s: %s",
                sponsor_name, type(exc).__name__, exc,
            )
            return {"total_hits": 0, "approvals": [], "active_count": 0,
                    "error": str(exc)}

        if resp.status_code == 429:
            raise OpenFDARateLimitException(
                f"OpenFDA returned 429 for {sponsor_name}"
            )
        if resp.status_code == 404:
            # OpenFDA returns 404 when the search has zero hits.
            # That's a legitimate "no approvals" outcome, not an error.
            return {"total_hits": 0, "approvals": [], "active_count": 0}
        if resp.status_code != 200:
            return {
                "total_hits": 0, "approvals": [], "active_count": 0,
                "error": f"http_{resp.status_code}: {resp.text[:200]}",
            }

        try:
            data = resp.json()
        except Exception as exc:
            return {"total_hits": 0, "approvals": [], "active_count": 0,
                    "error": f"json_parse: {exc}"}

        meta = data.get("meta") or {}
        results = data.get("results") or []
        if not isinstance(results, list):
            results = []

        total_hits = (
            (meta.get("results") or {}).get("total")
            if isinstance(meta.get("results"), dict)
            else len(results)
        )
        total_hits = int(total_hits or len(results))

        # Count approvals where at least one product isn't discontinued.
        # OpenFDA's `products[*].marketing_status` is one of:
        #   "Prescription", "Over-the-counter", "Discontinued", "None",
        #   "For Further Manufacturing Use"
        active = 0
        for approval in results:
            products = approval.get("products") or []
            if any(
                (p.get("marketing_status") or "").lower().strip()
                in ("prescription", "over-the-counter", "otc")
                for p in products if isinstance(p, dict)
            ):
                active += 1

        return {
            "total_hits": total_hits,
            "approvals": results,
            "active_count": active,
        }


# ── Singleton ──────────────────────────────────────────────────────────


_client_singleton: Optional[OpenFDAClient] = None


def get_openfda_client() -> OpenFDAClient:
    global _client_singleton
    if _client_singleton is None:
        _client_singleton = OpenFDAClient()
    return _client_singleton


async def close_openfda_client() -> None:
    """Tear-down hook for app.main lifespan."""
    global _client_singleton
    if _client_singleton is not None:
        await _client_singleton.close()
        _client_singleton = None
