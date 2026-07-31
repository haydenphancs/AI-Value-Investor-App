"""Analytics ingest — first-party product events into Supabase.

Design rule that outranks everything else here: **analytics must never affect the
app**. Every failure path swallows and logs. A dropped event is invisible; a 500 on
the ingest endpoint, or a slow write blocking the event loop, is a user-visible
regression caused by instrumentation. So:

  * the Supabase insert runs in a thread (the SDK is sync — it would otherwise block
    the loop, violating the async-first rule in CLAUDE.md);
  * unknown event names are filtered, not rejected (see `AnalyticsEvent.is_known`);
  * `record_batch` returns counts instead of raising.

Retention: rows are swept after `RETENTION_DAYS` by the news pre-warmer loop, the
same place `chat_usage_budget` and `guest_report_budget` are swept. These are
aggregate inputs, not a system of record.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from app.database import get_supabase
from app.schemas.analytics import AnalyticsEvent

logger = logging.getLogger(__name__)


class AnalyticsService:
    # Long enough for D30 retention cohorts with room to spare; short enough that a
    # behavioural log doesn't accumulate indefinitely for people who deleted the app.
    RETENTION_DAYS = 180

    def __init__(self):
        self.supabase = get_supabase()

    def record_batch(
        self,
        identity_key: str,
        events: List[AnalyticsEvent],
        *,
        session_id: Optional[str] = None,
        app_version: Optional[str] = None,
        platform: Optional[str] = None,
    ) -> Tuple[int, int]:
        """Insert a batch. Returns (accepted, dropped). Never raises.

        `dropped` counts events filtered by the allowlist. A failed INSERT reports
        everything as dropped — the caller still returns 200, because a client that
        retried on failure would amplify an outage into a write storm.
        """
        known = [e for e in events if e.is_known]
        dropped = len(events) - len(known)
        if dropped:
            unknown = sorted({e.event for e in events if not e.is_known})
            logger.info(
                "analytics: dropped %d event(s) not on the allowlist: %s",
                dropped, unknown,
            )
        if not known:
            return 0, dropped

        rows: List[Dict[str, Any]] = [
            {
                "identity_key": identity_key,
                "session_id": session_id,
                "event": e.event,
                "props": e.props,
                "app_version": app_version,
                "platform": platform,
                "client_ts": e.client_ts,
            }
            for e in known
        ]

        try:
            self.supabase.table("analytics_events").insert(rows).execute()
        except Exception as e:
            # Best-effort by design. Logged at warning (not error) so a Supabase blip
            # doesn't page anyone over telemetry — but it IS logged, so a permanently
            # broken pipeline is greppable rather than silently empty.
            logger.warning(
                "analytics: insert of %d row(s) failed for identity=%s: %s: %s",
                len(rows), identity_key, type(e).__name__, e,
            )
            return 0, len(events)

        return len(known), dropped

    # Chunk size for the retention sweep. A single unbounded DELETE over the
    # highest-volume table in the schema risks a statement/PostgREST timeout on the
    # FIRST post-retention sweep — and then it retries the same too-large delete every
    # 2 hours forever, never making progress. Chunking guarantees forward progress.
    _SWEEP_CHUNK = 5000
    _SWEEP_MAX_CHUNKS = 20   # ≤100k rows per pass; the next pass picks up the rest.

    def sweep_expired(self) -> int:
        """Delete rows older than `RETENTION_DAYS`, in bounded chunks.

        Best-effort; never raises. Returns the number deleted this pass.
        """
        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=self.RETENTION_DAYS)
        ).isoformat()
        total = 0
        try:
            for _ in range(self._SWEEP_MAX_CHUNKS):
                # Select ids first, then delete by id. PostgREST has no LIMIT on
                # DELETE, and this also avoids `returning=representation` shipping
                # every deleted row (props and all) back over HTTP just to count it.
                stale = (
                    self.supabase.table("analytics_events")
                    .select("id")
                    .lt("server_ts", cutoff)
                    .limit(self._SWEEP_CHUNK)
                    .execute()
                )
                ids = [r["id"] for r in (stale.data or [])]
                if not ids:
                    break
                self.supabase.table("analytics_events").delete().in_("id", ids).execute()
                total += len(ids)
                if len(ids) < self._SWEEP_CHUNK:
                    break
            if total:
                logger.info(
                    "analytics_events sweep: deleted %d row(s) older than %s",
                    total, cutoff,
                )
            return total
        except Exception as e:
            logger.warning(
                "analytics_events sweep failed (cutoff=%s, deleted %d so far): %s: %s",
                cutoff, total, type(e).__name__, e,
            )
            return total


_service: Optional[AnalyticsService] = None


def get_analytics_service() -> AnalyticsService:
    global _service
    if _service is None:
        _service = AnalyticsService()
    return _service
