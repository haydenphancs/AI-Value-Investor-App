"""Guest report budget — durable per-INSTALL monthly ceiling on free AI reports.

⚠️ **DEPRECATED — `claim()` and `release()` have NO production callers.** AI report generation
became account-only (both doors: `POST /research/generate` and `GET /stocks/{ticker}/report`),
so the guest branches that used this were removed from `ticker_report.py` and `research.py`.
The only live entry point is `sweep_expired`, called from the `app/main.py` lifespan to retain
existing rows.

The reason it went away is the reason not to bring it back: this meters on
`guest_user_id_for(X-Guest-Id)`, a UUID5 of a header the CLIENT chooses, so rotating that header
minted a fresh monthly allowance on every request — no ceiling at all on the most expensive call
in the product. Credits replaced it precisely because they are FK-bound to a real
`public.users` row and cannot be rotated.

`GUEST_REPORT_MONTHLY_LIMIT` (`config.py`), the `claim_guest_report`/`release_guest_report` RPCs
and the `guest_report_budget` table are transitively dead alongside the two methods below.
`tests/test_auth_dependency_matrix.py::test_the_rotatable_guest_budget_is_no_longer_consulted`
pins their absence. Keep the retention sweep; drop the rest when the table is retired.

--- original design notes follow ---

Signed-out users could generate UNLIMITED reports: `GET /stocks/{ticker}/report`
skips the credit precharge for guests (the credit RPCs early-return on the shared
guest sentinel — migration 101 — because debiting it would drain ONE pool for every
signed-out user at once), and the endpoint had no other ceiling. Meanwhile a
signed-in Free user got 2/month, so signing in strictly reduced what you could do.

This meters guests per INSTALL instead, keyed off the same `X-Guest-Id`-derived
UUID5 that Learn progress and the chat budget already use.

**Why not `user_credits`?** That table is FK-bound to `public.users`
(`user_credits_user_id_fkey`), and a synthetic per-install id has no users row by
design. `chat_usage_budget` is the correct precedent — a bare uuid column with no
FK — and this mirrors it (migrations 096/097 → 106).

Contract, mirroring ChatBudgetService's NULL-vs-raise split:
  - `try_claim_report` returns the new count on success, or -1 when the cap is
    reached (the RPC returns -1, no mutation). It RAISES `GuestReportBudgetUnavailable`
    only when the RPC round-trip itself fails (Supabase/network/PostgREST).
  - Callers FAIL OPEN on that raise. A DB blip must not wall a user out of the
    product's headline feature, and the blast radius stays bounded by the two
    in-process controls that do not depend on Supabase at all:
    REPORT_RATE_LIMIT_PER_MINUTE (burst) and REPORT_GET_MAX_INFLIGHT (concurrency).
  - `release_report` is best-effort; it swallows and logs failures.
"""

import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.config import settings
from app.database import get_supabase

logger = logging.getLogger(__name__)

# Period boundary. ET matches the app's trading-day convention (migration 089) so a
# guest's monthly allowance rolls at a predictable, market-aligned boundary rather
# than drifting with the server's timezone.
_BUDGET_TZ = ZoneInfo("America/New_York")


class GuestReportBudgetUnavailable(Exception):
    """The budget RPC round-trip failed for a transient/system reason (Supabase down,
    network blip, PostgREST/RLS error) — as opposed to the guest genuinely hitting the
    cap (signalled by the RPC returning -1, not by raising). Callers MUST fail OPEN on
    this: a DB blip must never tell a user they're out of reports."""


def _period_month() -> str:
    """First day of the current month (ET) as an ISO date string — the period key."""
    return datetime.now(_BUDGET_TZ).date().replace(day=1).isoformat()


class GuestReportBudgetService:
    def __init__(self):
        self.supabase = get_supabase()

    def current_period(self) -> str:
        """The period key a claim lands in. Callers capture this AT CLAIM TIME and
        hand it back to `release_report` — see that method for why recomputing it
        is wrong across a month boundary."""
        return _period_month()

    def try_claim_report(self, bucket_key: str) -> int:
        """Atomically claim one report for `bucket_key` this month.

        Returns the new count on success, or -1 when the cap is reached (no
        mutation). Raises `GuestReportBudgetUnavailable` if the RPC round-trip fails.
        """
        limit = settings.GUEST_REPORT_MONTHLY_LIMIT
        try:
            result = self.supabase.rpc(
                "claim_guest_report",
                {
                    "p_bucket_key": bucket_key,
                    "p_period": _period_month(),
                    "p_limit": limit,
                },
            ).execute()
        except Exception as e:
            logger.error(
                "claim_guest_report RPC failed for bucket=%s: %s: %s",
                bucket_key, type(e).__name__, e,
            )
            raise GuestReportBudgetUnavailable(
                f"claim_guest_report RPC failed for bucket={bucket_key}"
            ) from e

        count = result.data
        if count is None:
            # A well-formed RPC always returns an int; None means an unexpected
            # transport shape — treat as unavailable (fail open), not cap-reached.
            logger.error(
                "claim_guest_report returned no data for bucket=%s — treating as unavailable",
                bucket_key,
            )
            raise GuestReportBudgetUnavailable(
                f"claim_guest_report returned no data for bucket={bucket_key}"
            )

        count = int(count)
        if count == -1:
            logger.info(
                "Guest report cap reached for bucket=%s (limit=%s)", bucket_key, limit
            )
        return count

    def release_report(self, bucket_key: str, period: str) -> None:
        """Best-effort: release a claim when generation did NOT deliver (migration 106),
        so a Gemini outage doesn't burn a guest's whole monthly allowance on reports
        they never saw. Symmetric with the credit refund an authenticated user gets on
        the same paths. Never raises — a release failure must not affect the
        (already-failed) request's error response. The RPC floors at 0, so a stray
        double-release can't grant extra reports.

        `period` MUST be the value captured at CLAIM time (`current_period()`), not
        recomputed here. A generation that starts at 23:5x ET on the last day of a
        month and fails after midnight would otherwise release against the NEXT
        month's row — either silently burning the old month's slot or decrementing a
        fresh row and granting an extra free report.
        """
        try:
            self.supabase.rpc(
                "release_guest_report",
                {"p_bucket_key": bucket_key, "p_period": period},
            ).execute()
        except Exception as e:
            logger.warning(
                "release_guest_report failed for bucket=%s (%s: %s) — claim not released",
                bucket_key, type(e).__name__, e,
            )

    # Rows older than this are deleted by the sweep below. Only the CURRENT month's
    # row is ever read, so anything older is pure history.
    RETENTION_DAYS = 90

    def sweep_expired(self) -> int:
        """Delete `guest_report_budget` rows older than `RETENTION_DAYS`.

        Best-effort housekeeping so the table can't grow without bound as installs
        churn — one row per install per month, and installs are never cleaned up
        otherwise. Returns the number of rows deleted (0 on failure). Never raises.
        """
        cutoff = (
            datetime.now(_BUDGET_TZ).date().replace(day=1)
            - timedelta(days=self.RETENTION_DAYS)
        ).isoformat()
        try:
            result = (
                self.supabase.table("guest_report_budget")
                .delete()
                .lt("period_month", cutoff)
                .execute()
            )
            deleted = len(result.data or [])
            if deleted:
                logger.info(
                    "guest_report_budget sweep: deleted %d row(s) older than %s",
                    deleted, cutoff,
                )
            return deleted
        except Exception as e:
            logger.warning(
                "guest_report_budget sweep failed (cutoff=%s): %s: %s",
                cutoff, type(e).__name__, e,
            )
            return 0


_service: GuestReportBudgetService | None = None


def get_guest_report_budget_service() -> GuestReportBudgetService:
    global _service
    if _service is None:
        _service = GuestReportBudgetService()
    return _service
