"""
Marketing publisher loop — the web-lifespan half of the marketing engine (design doc §12.2, §12.10).

The MEDIA WORKER (a separate Railway cron service, `marketing/main.py`) makes the day's artefacts
and records one `marketing_posts` row per outlet. THIS loop, running in the web process where the
social secrets live, is the ONLY thing that ever calls a platform (rules/marketing.md §2). Each tick:

1. **Expire** — `approved` / `pending_review` posts outside their run day or the next (ET) close
   `skipped`, and finished runs dated before yesterday close (`run_service`). Always runs.
2. **Retract** — published posts whose deletion the owner CONFIRMED in Telegram are deleted on
   their platform and marked `retracted`. Always runs: a kill switch stops new posts, never a
   takedown the owner asked for.
3. **Reconcile** (MARKETING_ENABLED) — `queued` posts whose outcome is unknown (a timeout after
   sending, a crash between the claim and the call) are checked against the platform: found →
   `published`; absent → resent where that is provably safe (Bluesky), otherwise re-checked on a
   schedule and finally ESCALATED to the owner ("It's live" / "Not posted"). Never a blind retry.
4. **Publish** (MARKETING_ENABLED) — `approved` posts of enabled platforms
   (`outlets.enabled_platforms()`): dry-run decided before any write; freshness, back-off, the
   human-approval guard and the adapter's own validation (`prepare`) before the claim; the X spend
   cap before the claim; then ONE fenced write-ahead claim (approved → queued, attempt charged,
   `metadata.publish.state = sending`) and the platform call.
5. **Telegram** (bot configured) — the review sweep (`review_service`), then the publish feed
   (`publish_feed`: "Posted …" with Retract, retract confirmations, alerts).

Outcome rules (outlet_base): PUBLISHED → `published`; NOT_SENT (provably never left) → back to
`approved` with a back-off, `failed` after MARKETING_PUBLISH_MAX_ATTEMPTS; REFUSED (a definite 4xx)
→ `failed`; AMBIGUOUS → stays `queued` for reconcile. A ledger failure AFTER a platform accepted a
post leaves the row `queued`/`sending` — reconcile finds the post; nothing is ever re-sent on a
guess. Every write is a fenced, merging `run_service.transition_post`.

The loop wakes early on an Approve / confirmed Retract (`publisher_wake`).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from app.config import settings
from app.services.marketing import outlets, publish_feed, publisher_wake, review_service
from app.services.marketing.outlet_base import (
    ABSENT,
    AMBIGUOUS,
    FOUND,
    GAVE_UP,
    MANUAL,
    NOT_SENT,
    PUBLISHED,
    REFUSED,
    RETRACTED,
    RETRY,
    Adapter,
    MarketingPublishRefused,
    Outcome,
    Prepared,
    RetractResult,
    backoff_seconds,
    scrub,
)
from app.services.marketing.run_service import (
    _parse_ts,
    get_marketing_run_service,
    is_fresh,
    month_start_utc,
    run_date_et,
)

logger = logging.getLogger(__name__)

#: Rows each step reads per tick (bounded: the publisher runs on the single uvicorn worker).
PUBLISH_SCAN_LIMIT = 100
RECONCILE_SCAN_LIMIT = 20
RETRACT_SCAN_LIMIT = 10
#: Bluesky-style resends of an ABSENT post after an ambiguous call, at most.
MAX_RESENDS = 3
#: Delete attempts before the owner is told to remove a post by hand.
MAX_RETRACT_ATTEMPTS = 3
RETRACT_BACKOFF_SECONDS = 300
#: Cap on the per-post history kept in `metadata.publish.history`.
HISTORY_MAX = 10

#: In-memory: escalated posts already logged today (one ERROR per post per day, not per tick).
_escalation_logged: Dict[str, str] = {}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _meta(post: Dict[str, Any]) -> Dict[str, Any]:
    return post["metadata"] if isinstance(post.get("metadata"), dict) else {}


def _pub(post: Dict[str, Any]) -> Dict[str, Any]:
    pub = _meta(post).get("publish")
    return pub if isinstance(pub, dict) else {}


def _history(post: Dict[str, Any], entry: Dict[str, Any]) -> list:
    hist = _pub(post).get("history")
    hist = list(hist) if isinstance(hist, list) else []
    hist.append(entry)
    return hist[-HISTORY_MAX:]


#: A row is live only when `metadata.dry_run` is exactly False (`create_posts` writes it on every
#: row; a row without it is never sent) — the review bot's rule, shared.
is_rehearsal = review_service.is_rehearsal


def _charge_at(row: Dict[str, Any], op_suffix: str) -> Optional[str]:
    """The time of the row's LAST journal entry whose op ends with `op_suffix` (`_create`, `_read`,
    `_delete`) — what a refund or correction of that charge is dated at, so it lands in the same
    month as the charge it reverses."""
    journal = _meta(row).get("charges")
    if not isinstance(journal, list):
        return None
    for entry in reversed(journal):
        if isinstance(entry, dict) and str(entry.get("op") or "").endswith(op_suffix) and entry.get("at"):
            return str(entry["at"])
    return None


def _alert(kind: str, text: str) -> Dict[str, Any]:
    """Metadata that makes the publish feed send `text` to the owner (once — `alert_notified_at`)."""
    return {"alert_kind": kind, "alert_text": scrub(text), "alert_at": _now().isoformat()}


class _Budget:
    """A platform's monthly spend cap for ONE cycle: the month's journaled charges are read once,
    then kept as a running total. A failed read blocks the platform this cycle (fail-closed)."""

    def __init__(self, svc: Any, platform: str, budget_micros: int) -> None:
        self.svc, self.platform, self.budget = svc, platform, int(budget_micros)
        self.spent: Optional[int] = None
        self.unreadable = False

    async def allows(self, micros: int) -> bool:
        if self.budget <= 0:
            return False
        if self.spent is None and not self.unreadable:
            try:
                self.spent = await self.svc.spend_since(self.platform, month_start_utc())
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self.unreadable = True
                logger.error("marketing publisher: %s spend could not be read (%s: %s) — %s is paused "
                             "this cycle", self.platform, type(e).__name__, e, self.platform, exc_info=True)
        if self.unreadable or self.spent is None:
            return False
        return self.spent + int(micros) <= self.budget

    def add(self, micros: int) -> None:
        if self.spent is not None:
            self.spent += int(micros)


def _budgets(svc: Any) -> Dict[str, _Budget]:
    from app.services.marketing import outlet_x
    return {"x": _Budget(svc, "x", outlet_x.budget_micros())}


async def _cap_alert(svc: Any, post: Dict[str, Any], budget: _Budget) -> None:
    """Tell the owner ONCE per month that the platform's cap stopped a post (the marker on a post
    row survives restarts)."""
    month = month_start_utc().strftime("%Y-%m")
    marker = f"{budget.platform}_cap_alert_month"
    try:
        if await svc.any_post_with_meta(budget.platform, marker, month):
            return
        text = (f"⚠️ {budget.platform.upper()} monthly cap reached: ${(budget.spent or 0) / 1e6:.3f} of "
                f"${budget.budget / 1e6:.2f} spent in {month}. {budget.platform.upper()} posts are paused "
                f"until next month (raise MARKETING_X_MONTHLY_BUDGET_USD on the web service to resume).")
        await svc.transition_post(str(post["id"]), expect_status="approved", observed=post, retries=1,
                                  meta={marker: month, **_alert("x_budget", text)}, unset=("alert_notified_at",))
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.warning("marketing publisher: cap alert for %s not recorded (%s: %s)", budget.platform,
                       type(e).__name__, e)


# ── step 1: publish ───────────────────────────────────────────────────────────────────────────────


async def _send(adapter: Adapter, post: Dict[str, Any], prepared: Prepared) -> Outcome:
    """`adapter.send` with a bug in OUR code read as AMBIGUOUS (the call may have gone out)."""
    try:
        return await adapter.send(post, prepared)
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.error("marketing publish: adapter BUG post_id=%s platform=%s key=%s (%s: %s) — left queued "
                     "for reconcile", post.get("id"), post.get("platform"), post.get("idempotency_key"),
                     type(e).__name__, e, exc_info=True)
        return Outcome(AMBIGUOUS, "bug", error=scrub(f"{type(e).__name__}: {e}"))


async def record_outcome(svc: Any, adapter: Adapter, row: Dict[str, Any], outcome: Outcome,
                         *, reserve_micros: int = 0) -> str:
    """Write one send's outcome onto a `queued` row (fenced on queued). Returns the resulting state
    (`published` / `approved` / `failed` / `queued`). Never raises: a ledger failure is logged with
    every id — after a PUBLISHED outcome it is the line someone reconciles by hand from."""
    post_id, platform, key = str(row["id"]), row.get("platform"), row.get("idempotency_key")
    now = _now()
    attempt = int(row.get("attempts") or 0)
    entry = {"attempt": attempt, "kind": outcome.kind, "category": outcome.category, "at": now.isoformat()}
    try:
        if outcome.kind == PUBLISHED:
            updated = await svc.transition_post(
                post_id, expect_status="queued", observed=row, status="published", retries=1,
                publish={"state": "published", "category": "", "error": None, "history": _history(row, entry),
                         **outcome.publish_meta},
                external_id=outcome.external_id, external_url=outcome.external_url,
                published_at=outcome.published_at or now.isoformat(), last_error=None,
            )
            if updated is None:
                raise RuntimeError("the row was no longer queued")
            logger.info("marketing post PUBLISHED post_id=%s platform=%s key=%s external_id=%s url=%s",
                        post_id, platform, key, outcome.external_id, outcome.external_url)
            return "published"
        if outcome.kind == NOT_SENT:
            charge = None
            if reserve_micros and outcome.category == "transport":
                # It never left: X did not bill it. Dated at the claim's charge (same month).
                charge = ("refund_not_sent", -int(reserve_micros), _charge_at(row, "_create"))
            alert = _alert(outcome.alert, f"{str(platform).upper()} post not sent ({outcome.category}): "
                           f"{outcome.error}") if outcome.alert else {}
            if attempt >= max(int(settings.MARKETING_PUBLISH_MAX_ATTEMPTS), 1):
                updated = await svc.transition_post(
                    post_id, expect_status="queued", observed=row, status="failed", retries=1, charge=charge,
                    publish={"state": "not_sent", "category": outcome.category, "error": outcome.error,
                             "history": _history(row, entry), **outcome.publish_meta},
                    meta=_alert("failed", f"❌ {str(platform).upper()} post FAILED for good after {attempt} "
                                          f"attempts that never reached the platform ({outcome.category}): "
                                          f"{outcome.error}"),
                    unset=("alert_notified_at",), last_error=outcome.error,
                )
                logger.error("marketing post FAILED (not sent, attempts exhausted) post_id=%s platform=%s key=%s: %s",
                             post_id, platform, key, outcome.error)
                return "failed" if updated else "queued"
            retry_at = outcome.retry_at or now + timedelta(seconds=backoff_seconds(attempt))
            await svc.transition_post(
                post_id, expect_status="queued", observed=row, status="approved", retries=1, charge=charge,
                publish={"state": "not_sent", "category": outcome.category, "error": outcome.error,
                         "next_attempt_at": retry_at.isoformat(), "history": _history(row, entry),
                         **outcome.publish_meta},
                meta=alert, unset=("alert_notified_at",) if alert else (), last_error=outcome.error,
            )
            logger.warning("marketing post NOT SENT post_id=%s platform=%s key=%s category=%s attempt=%d — "
                           "retry at %s: %s", post_id, platform, key, outcome.category, attempt,
                           retry_at.isoformat(), outcome.error)
            return "approved"
        if outcome.kind == REFUSED:
            charge = (("refund", int(outcome.refund_micros), _charge_at(row, "_create"))
                      if outcome.refund_micros else None)
            await svc.transition_post(
                post_id, expect_status="queued", observed=row, status="failed", retries=1, charge=charge,
                publish={"state": "refused", "category": outcome.category, "error": outcome.error,
                         "history": _history(row, entry), **outcome.publish_meta},
                meta=_alert(outcome.alert or "failed",
                            f"❌ {str(platform).upper()} refused the post ({outcome.category}): {outcome.error}"),
                unset=("alert_notified_at",), last_error=outcome.error,
            )
            logger.error("marketing post REFUSED post_id=%s platform=%s key=%s category=%s: %s",
                         post_id, platform, key, outcome.category, outcome.error)
            return "failed"
        # AMBIGUOUS — the platform may have it. Stays queued; reconcile decides.
        await svc.transition_post(
            post_id, expect_status="queued", observed=row, retries=1,
            publish={"state": "unknown", "category": outcome.category, "error": outcome.error,
                     "history": _history(row, entry), **outcome.publish_meta},
            last_error=outcome.error,
        )
        logger.warning("marketing post OUTCOME UNKNOWN post_id=%s platform=%s key=%s category=%s — left "
                       "queued for reconcile: %s", post_id, platform, key, outcome.category, outcome.error)
        return "queued"
    except asyncio.CancelledError:
        raise
    except Exception as e:
        if outcome.kind == PUBLISHED:
            logger.error(
                "marketing post PUBLISHED BUT LEDGER WRITE FAILED post_id=%s platform=%s key=%s external_id=%s "
                "external_url=%s: %s: %s — row left queued; reconcile will find it", post_id, platform, key,
                outcome.external_id, outcome.external_url, type(e).__name__, e, exc_info=True)
        else:
            logger.error("marketing post outcome %s NOT RECORDED post_id=%s platform=%s key=%s: %s: %s — row "
                         "left queued for reconcile", outcome.kind, post_id, platform, key, type(e).__name__, e,
                         exc_info=True)
        return "queued"


async def _refuse(svc: Any, post: Dict[str, Any], err: MarketingPublishRefused) -> None:
    """The adapter's guard refused an approved post: `failed` without a claim or a platform call."""
    text = scrub(err)
    updated = await svc.transition_post(
        str(post["id"]), expect_status="approved", observed=post, status="failed", retries=1,
        publish={"state": "refused", "category": getattr(err, "category", "guard"), "error": text},
        meta=_alert("failed", f"❌ {str(post.get('platform')).upper()} post not published — {text}"),
        unset=("alert_notified_at",), last_error=text,
    )
    logger.error("marketing post REFUSED BY GUARD post_id=%s platform=%s key=%s recorded=%s: %s",
                 post.get("id"), post.get("platform"), post.get("idempotency_key"), updated is not None, text)


async def publish_cycle() -> Dict[str, int]:
    """One publish pass. Returns counters for the tick's log line (and tests)."""
    svc = get_marketing_run_service()
    counters = {"approved_waiting": 0, "published": 0, "failed": 0, "skipped": 0, "retry": 0,
                "unknown": 0, "capped": 0}
    platforms = outlets.enabled_platforms()
    if not platforms:
        approved = await svc.list_posts("approved", limit=PUBLISH_SCAN_LIMIT)
        counters["approved_waiting"] = len(approved)
        if approved:
            logger.info("marketing publisher: %d approved post(s) waiting, no platform enabled "
                        "(MARKETING_PUBLISH_PLATFORMS=%r) — nothing sent", len(approved),
                        settings.MARKETING_PUBLISH_PLATFORMS)
        return counters

    # Only rows an enabled adapter can send — and, live, only non-rehearsal rows — are fetched: the
    # filter runs IN the query, before its LIMIT (the starvation lesson, run_service.list_posts).
    dry_switch = bool(settings.MARKETING_DRY_RUN)
    approved = await svc.list_posts("approved", limit=PUBLISH_SCAN_LIMIT, platforms=platforms,
                                    live_only=not dry_switch)
    counters["approved_waiting"] = len(approved)
    now = _now()
    today = run_date_et(now)
    budgets = _budgets(svc)
    alerted = set()
    for post in approved:
        post_id, platform = str(post["id"]), str(post.get("platform"))
        adapter = outlets.adapter_for(platform)
        if adapter is None:
            continue
        if not is_fresh(post, today):
            counters["skipped"] += 1    # the expiry step closes it
            continue
        if post.get("approved_by") == "auto":
            # A human approves every post until the judge round deliberately removes this guard
            # (MARKETING_AUTO_PUBLISH flipped by mistake must not publish unread text).
            logger.warning("marketing publisher: post_id=%s platform=%s was AUTO-approved — not published "
                           "(human approval is required)", post_id, platform)
            counters["skipped"] += 1
            continue
        not_before = _parse_ts(_pub(post).get("next_attempt_at"))
        if not_before is not None and now < not_before:
            counters["retry"] += 1
            continue
        if not adapter.available():
            counters["retry"] += 1
            continue
        dry = dry_switch or is_rehearsal(post)
        try:
            prepared = adapter.prepare(post)
        except MarketingPublishRefused as e:
            if dry:
                logger.info("marketing publisher DRY_RUN: would REFUSE post_id=%s platform=%s: %s",
                            post_id, platform, scrub(e))
                counters["skipped"] += 1
                continue
            try:
                await _refuse(svc, post, e)
            except asyncio.CancelledError:
                raise
            except Exception as err:   # one row's ledger error never stops the rows behind it
                logger.error("marketing publisher: refusal NOT recorded post_id=%s platform=%s (%s: %s)",
                             post_id, platform, type(err).__name__, err, exc_info=True)
            counters["failed"] += 1
            continue
        except asyncio.CancelledError:
            raise
        except Exception as e:
            # A bug in OUR validation for this one row (malformed metadata …): before any claim or
            # platform call, so it is safe to skip — and it must never stop the posts behind it.
            logger.error("marketing publisher: prepare() BUG post_id=%s platform=%s key=%s (%s: %s) — "
                         "skipped this tick", post_id, platform, post.get("idempotency_key"),
                         type(e).__name__, e, exc_info=True)
            counters["skipped"] += 1
            continue
        if dry:
            # Decided BEFORE the claim: a rehearsal touches no row at all.
            logger.info("marketing publisher DRY_RUN: would publish post_id=%s platform=%s key=%s %s",
                        post_id, platform, post.get("idempotency_key"), prepared.summary)
            counters["skipped"] += 1
            continue
        budget = budgets.get(platform)
        if budget is not None and prepared.reserve_micros > 0 and not await budget.allows(prepared.reserve_micros):
            counters["capped"] += 1
            if platform not in alerted and not budget.unreadable:
                alerted.add(platform)
                await _cap_alert(svc, post, budget)
            continue
        attempt = int(post.get("attempts") or 0) + 1
        try:
            claimed = await svc.claim_post(
                post_id, observed=post,
                publish={"attempt": attempt, "started_at": _now().isoformat(), "state": "sending",
                         "text_sha256": prepared.text_sha256, "next_attempt_at": None, "reconcile": None,
                         **prepared.publish_meta},
                charge=(f"{platform}_create", prepared.reserve_micros) if prepared.reserve_micros else None,
            )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            # Nothing was sent. If the claim did land (a lost response), the row is queued/sending and
            # reconcile settles it; either way the rows behind it go on.
            logger.error("marketing publisher: claim FAILED post_id=%s platform=%s key=%s (%s: %s)", post_id,
                         platform, post.get("idempotency_key"), type(e).__name__, e, exc_info=True)
            counters["skipped"] += 1
            continue
        if claimed is None:
            counters["skipped"] += 1    # another tick / container took it, or it changed meanwhile
            continue
        if budget is not None and prepared.reserve_micros:
            budget.add(prepared.reserve_micros)
        outcome = await _send(adapter, claimed, prepared)
        state = await record_outcome(svc, adapter, claimed, outcome, reserve_micros=prepared.reserve_micros)
        if outcome.kind == PUBLISHED:
            counters["published"] += 1
        elif state == "failed":
            counters["failed"] += 1
        elif state == "approved":
            counters["retry"] += 1
        else:
            counters["unknown"] += 1
    return counters


# ── step 2: reconcile ──────────────────────────────────────────────────────────────────────────


async def _escalate(svc: Any, row: Dict[str, Any], reason: str) -> None:
    platform = str(row.get("platform")).upper()
    url_hint = ""
    if row.get("platform") == "x":
        from app.integrations import x_api
        uid = x_api.user_id_from_access_token()
        url_hint = f"\nCheck the profile: https://x.com/i/user/{uid}" if uid else ""
    text = (f"⚠️ {platform} post — outcome UNKNOWN ({reason}). It may or may not be live.{url_hint}\n"
            f"Tap “It's live” if you can see it, or “Not posted” if it is not there. "
            f"It will NOT be resent automatically.")
    when = _now().isoformat()
    # `escalated_at` at the TOP of metadata too: the reconcile scan filters it out in the query, so
    # unanswered escalations can never fill its window and starve a new unknown outcome.
    updated = await svc.transition_post(
        str(row["id"]), expect_status="queued", observed=row, retries=1,
        publish={"state": "escalated", "escalated_at": when},
        meta={"escalated_at": when, **_alert("unknown", text)}, unset=("alert_notified_at",),
    )
    logger.error("marketing post ESCALATED to the owner post_id=%s platform=%s key=%s recorded=%s: %s",
                 row.get("id"), row.get("platform"), row.get("idempotency_key"), updated is not None, reason)


async def _log_escalations(svc: Any, today: Any) -> None:
    """One ERROR per escalated post per day while the owner has not answered (not one per tick)."""
    try:
        rows = await svc.list_posts_filtered(status="queued", order="claimed_at", limit=RECONCILE_SCAN_LIMIT,
                                             not_null=("metadata->>escalated_at",))
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.warning("marketing reconcile: escalated posts could not be listed (%s: %s)", type(e).__name__, e)
        return
    for row in rows:
        key = str(row.get("id"))
        if _escalation_logged.get(key) != today.isoformat():
            _escalation_logged[key] = today.isoformat()
            logger.error("marketing post still ESCALATED post_id=%s platform=%s key=%s since %s — waiting for the "
                         "owner's answer in Telegram", key, row.get("platform"), row.get("idempotency_key"),
                         _meta(row).get("escalated_at"))


async def reconcile_cycle() -> Dict[str, int]:
    """Settle `queued` posts whose outcome is unknown. Never resends unless the adapter proves it
    safe (Bluesky: same key, same record, `RecordNotFound` from the account's own PDS). One row's
    failure is logged and never stops the rows behind it."""
    svc = get_marketing_run_service()
    counters = {"checked": 0, "found": 0, "absent": 0, "resent": 0, "escalated": 0, "expired": 0, "waiting": 0}
    rows = await svc.list_posts_filtered(status="queued", order="claimed_at", limit=RECONCILE_SCAN_LIMIT,
                                         null=("metadata->>escalated_at",))
    now = _now()
    today = run_date_et(now)
    await _log_escalations(svc, today)
    budgets = _budgets(svc)
    for row in rows:
        try:
            key = await _reconcile_one(svc, row, now=now, today=today, budgets=budgets)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error("marketing reconcile FAILED post_id=%s platform=%s key=%s (%s: %s) — retried next tick",
                         row.get("id"), row.get("platform"), row.get("idempotency_key"), type(e).__name__, e,
                         exc_info=True)
            continue
        for k in (key if isinstance(key, tuple) else (key,)):
            if k:
                counters[k] = counters.get(k, 0) + 1
    return counters


async def _reconcile_one(svc: Any, row: Dict[str, Any], *, now: datetime, today: Any,
                         budgets: Dict[str, "_Budget"]) -> Any:
    """One queued row. Returns the counter key(s) to bump: a str, a tuple (a check made AND its
    result, e.g. ("checked", "found")), or None."""
    adapter = outlets.adapter_for(row.get("platform"))
    if adapter is None:
        return None
    pub = _pub(row)
    if pub.get("state") == "escalated":   # belt and braces behind the query filter
        return None
    after = timedelta(seconds=max(int(settings.MARKETING_PUBLISH_RECONCILE_AFTER_SECONDS), 60))
    # A row with no recorded start (a hand-edited or legacy row) falls back to its last write, so it
    # is still checked — and escalated — instead of sitting queued forever unseen.
    started = (_parse_ts(pub.get("started_at")) or _parse_ts(row.get("claimed_at"))
               or _parse_ts(row.get("updated_at")))
    if started is None:
        await _escalate(svc, row, "the row records no start time")
        return "escalated"
    if now - started < after:
        return None
    rec = pub.get("reconcile") if isinstance(pub.get("reconcile"), dict) else {}
    try:
        n = max(int(rec.get("n") or 0), 0)
    except (TypeError, ValueError):
        n = len(adapter.reconcile_schedule)   # corrupt counter: go straight to the owner
    schedule = adapter.reconcile_schedule
    if n >= len(schedule):
        await _escalate(svc, row, f"{n} checks could not confirm it")
        return "escalated"
    if now < started + max(timedelta(seconds=schedule[n]), after):
        return None
    platform = str(row.get("platform"))
    reserve = int(adapter.reconcile_reserve_micros or 0)
    budget = budgets.get(platform)
    if reserve and budget is not None and not await budget.allows(reserve):
        if budget.unreadable:
            # The spend could not be READ this tick (a ledger blip) — not a cap. Wait for the next
            # tick, untouched; only a long outage (past the whole schedule) goes to the owner.
            if now >= started + timedelta(seconds=schedule[-1]):
                await _escalate(svc, row, "the X spend could not be read for the whole check window")
                return "escalated"
            return "waiting"
        reason = ("X checks are switched off (MARKETING_X_MONTHLY_BUDGET_USD is 0)" if budget.budget <= 0
                  else "the monthly X cap blocks the check")
        await _escalate(svc, row, reason)
        return "escalated"
    # Write-ahead the check (and its worst-case cost) BEFORE the call.
    checking = await svc.transition_post(
        str(row["id"]), expect_status="queued", observed=row, retries=0,
        publish={"reconcile": {"n": n + 1, "last_at": now.isoformat()}},
        charge=(f"{platform}_read", reserve) if reserve else None,
    )
    if checking is None:
        return None
    if reserve and budget is not None:
        budget.add(reserve)
    try:
        result = await adapter.reconcile(checking)
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.error("marketing reconcile BUG post_id=%s (%s: %s)", row.get("id"), type(e).__name__, e,
                     exc_info=True)
        return ("checked",)
    correction = int(result.cost_micros) - reserve if reserve else int(result.cost_micros)
    charge = ((f"{platform}_read_correction", correction, _charge_at(checking, "_read"))
              if correction else None)
    rec_meta = {"n": n + 1, "last_at": now.isoformat(), "last_result": result.kind, "error": result.error}
    if result.kind == FOUND:
        await svc.transition_post(
            str(row["id"]), expect_status="queued", observed=checking, status="published", retries=1,
            charge=charge, publish={"state": "published", "reconcile": rec_meta, **result.publish_meta},
            external_id=result.external_id, external_url=result.external_url,
            published_at=result.published_at or now.isoformat(), last_error=None,
        )
        logger.info("marketing reconcile FOUND post_id=%s platform=%s key=%s external_id=%s", row.get("id"),
                    platform, row.get("idempotency_key"), result.external_id)
        return ("checked", "found")
    if result.kind == ABSENT and result.resend_safe and adapter.resend_safe:
        if not is_fresh(checking, today):
            await svc.transition_post(
                str(row["id"]), expect_status="queued", observed=checking, status="skipped", retries=1,
                charge=charge, publish={"state": "absent_expired", "reconcile": rec_meta},
                meta={"skip_reason": "expired",
                      **_alert("expired", f"⏰ {platform.upper()} post not published: its outcome was unknown, "
                                          f"the platform has no copy of it, and its day has passed.")},
                unset=("alert_notified_at",),
                last_error="confirmed absent on the platform after an unknown outcome; expired",
            )
            return ("checked", "expired")
        resends = int(pub.get("resends") or 0) if str(pub.get("resends") or "0").isdigit() else MAX_RESENDS
        can_resend = (settings.MARKETING_ENABLED and not settings.MARKETING_DRY_RUN and not is_rehearsal(checking)
                      and platform in outlets.enabled_platforms() and resends < MAX_RESENDS)
        if can_resend:
            try:
                prepared = adapter.prepare(checking)   # reuses the stored key + record
            except asyncio.CancelledError:
                raise
            except Exception as e:
                if not isinstance(e, MarketingPublishRefused):
                    logger.error("marketing reconcile: prepare() BUG post_id=%s (%s: %s)", row.get("id"),
                                 type(e).__name__, e, exc_info=True)
                await _escalate(svc, checking, f"absent, and the resend could not be prepared: {scrub(e)}")
                return ("checked", "escalated")
            marked = await svc.transition_post(
                str(row["id"]), expect_status="queued", observed=checking, retries=0, charge=charge,
                publish={"state": "sending", "resends": resends + 1, "reconcile": rec_meta,
                         "started_at": _now().isoformat(), **prepared.publish_meta},
            )
            if marked is None:
                return ("checked",)
            outcome = await _send(adapter, marked, prepared)
            await record_outcome(svc, adapter, marked, outcome)
            return ("checked", "resent")
    await svc.transition_post(str(row["id"]), expect_status="queued", observed=checking, retries=1,
                              charge=charge, publish={"reconcile": rec_meta})
    return ("checked", "absent") if result.kind == ABSENT else ("checked",)


# ── step 3: retract ────────────────────────────────────────────────────────────────────────────

#: A confirmed retract waiting on missing credentials is handed to the owner after this long.
RETRACT_WAIT_FOR_CREDENTIALS = timedelta(hours=1)


async def retract_cycle() -> Dict[str, int]:
    """Delete published posts whose retraction the owner confirmed. Runs whatever MARKETING_ENABLED /
    MARKETING_DRY_RUN say, and never blocked by a spend cap (the cost is still journaled). One row's
    failure is logged and never stops the rows behind it."""
    svc = get_marketing_run_service()
    counters = {"retracted": 0, "retry": 0, "manual": 0, "waiting": 0}
    # `retract_closed_at` marks a request that ended without a delete (gave up / by hand): filtered IN
    # the query, or ten such rows would fill the window forever and starve every new request.
    rows = await svc.list_posts_filtered(status="published", order="updated_at", limit=RETRACT_SCAN_LIMIT,
                                         not_null=("metadata->>retract_requested_at",),
                                         null=("metadata->>retract_closed_at",))
    now = _now()
    for row in rows:
        try:
            key = await _retract_one(svc, row, now=now)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error("marketing retract FAILED post_id=%s platform=%s (%s: %s) — retried next tick",
                         row.get("id"), row.get("platform"), type(e).__name__, e, exc_info=True)
            continue
        if key:
            counters[key] = counters.get(key, 0) + 1
    return counters


async def _retract_one(svc: Any, row: Dict[str, Any], *, now: datetime) -> Optional[str]:
    meta = _meta(row)
    retract = meta.get("retract") if isinstance(meta.get("retract"), dict) else {}
    if retract.get("state") in ("done", GAVE_UP, MANUAL):
        return None
    next_at = _parse_ts(retract.get("next_at"))
    if next_at is not None and now < next_at:
        return None
    adapter = outlets.adapter_for(row.get("platform"))
    platform = str(row.get("platform")).upper()
    url = row.get("external_url") or "(no link)"
    if adapter is None or not adapter.retractable:
        await svc.transition_post(
            str(row["id"]), expect_status="published", observed=row, retries=1,
            meta={"retract": {**retract, "state": MANUAL}, "retract_closed_at": now.isoformat(),
                  **_alert("retract_manual", f"🗑 {platform} has no delete API — remove the post by hand: {url}")},
            unset=("alert_notified_at",),
        )
        return "manual"
    if not adapter.configured_for_retract():
        requested = _parse_ts(retract.get("requested_at")) or _parse_ts(meta.get("retract_requested_at"))
        if requested is not None and now - requested >= RETRACT_WAIT_FOR_CREDENTIALS:
            # Credentials removed after the request (a revoked key …): never wait silently forever.
            await svc.transition_post(
                str(row["id"]), expect_status="published", observed=row, retries=1,
                meta={"retract": {**retract, "state": MANUAL, "error": "credentials not set"},
                      "retract_closed_at": now.isoformat(),
                      **_alert("retract_manual", f"🗑 {platform} credentials are not set, so the post could not "
                                                 f"be deleted — remove it by hand: {url}")},
                unset=("alert_notified_at",),
            )
            return "manual"
        logger.warning("marketing retract: post_id=%s platform=%s waits — %s credentials are not set",
                       row.get("id"), row.get("platform"), platform)
        return "waiting"
    try:
        attempts = max(int(retract.get("attempts") or 0), 0) + 1
    except (TypeError, ValueError):
        attempts = MAX_RETRACT_ATTEMPTS
    cost = int(adapter.retract_cost_micros or 0)
    started = await svc.transition_post(
        str(row["id"]), expect_status="published", observed=row, retries=0,
        meta={"retract": {**retract, "attempts": attempts, "state": "deleting", "started_at": now.isoformat()}},
        charge=(f"{row.get('platform')}_delete", cost) if cost else None,
    )
    if started is None:
        return None
    try:
        result = await adapter.retract(started)
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.error("marketing retract BUG post_id=%s (%s: %s)", row.get("id"), type(e).__name__, e,
                     exc_info=True)
        result = RetractResult(RETRY, error=scrub(f"{type(e).__name__}: {e}"), cost_micros=cost)
    correction = int(result.cost_micros) - cost
    charge = ((f"{row.get('platform')}_delete_correction", correction, _charge_at(started, "_delete"))
              if correction else None)
    base = _meta(started).get("retract") if isinstance(_meta(started).get("retract"), dict) else {}
    if result.kind == RETRACTED:
        done = _now().isoformat()
        await svc.transition_post(
            str(row["id"]), expect_status="published", observed=started, status="retracted", retries=1,
            charge=charge, meta={"retract": {**base, "state": "done", "done_at": done, "error": None},
                                 "retract_done_at": done},
            unset=("retract_notified_at",),
        )
        logger.info("marketing post RETRACTED post_id=%s platform=%s external_id=%s", row.get("id"),
                    row.get("platform"), row.get("external_id"))
        return "retracted"
    if result.kind == RETRY and attempts < MAX_RETRACT_ATTEMPTS:
        await svc.transition_post(
            str(row["id"]), expect_status="published", observed=started, retries=1, charge=charge,
            meta={"retract": {**base, "state": "requested", "error": result.error,
                              "next_at": (now + timedelta(seconds=RETRACT_BACKOFF_SECONDS * attempts)).isoformat()}},
        )
        logger.warning("marketing retract RETRY post_id=%s platform=%s attempt=%d: %s", row.get("id"),
                       row.get("platform"), attempts, result.error)
        return "retry"
    state = MANUAL if result.kind == MANUAL else GAVE_UP
    await svc.transition_post(
        str(row["id"]), expect_status="published", observed=started, retries=1, charge=charge,
        meta={"retract": {**base, "state": state, "error": result.error}, "retract_closed_at": _now().isoformat(),
              **_alert("retract_failed", f"🗑 {platform} post could NOT be deleted ({result.error}). "
                                         f"Remove it by hand: {url}")},
        unset=("alert_notified_at",),
    )
    logger.error("marketing retract GAVE UP post_id=%s platform=%s after %d attempt(s): %s", row.get("id"),
                 row.get("platform"), attempts, result.error)
    return "manual"


# ── the tick and the loop ──────────────────────────────────────────────────────────────────────


async def _step(name: str, coro_fn) -> Optional[Dict[str, int]]:
    """Run one step; a failing step is logged and never stops the next (a dead publisher looks
    exactly like "nothing is ever posted" and has no other symptom)."""
    try:
        return await coro_fn()
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.error("marketing publisher step %s failed (%s: %s)", name, type(e).__name__, e, exc_info=True)
        return None


async def _expire_step() -> Dict[str, int]:
    """Housekeeping, always on: auto-approved posts back to review (a human approves every post),
    stale posts expired, finished runs closed — each part isolated from the others."""
    svc = get_marketing_run_service()
    today = run_date_et()
    out = {"auto_demoted": 0, "expired": 0, "runs_closed": 0}
    for key, call in (("auto_demoted", lambda: svc.demote_auto_approved()),
                      ("expired", lambda: svc.expire_stale_posts(today)),
                      ("runs_closed", lambda: svc.close_finished_runs(today))):
        try:
            out[key] = await call()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error("marketing publisher housekeeping %s failed (%s: %s)", key, type(e).__name__, e,
                         exc_info=True)
    return out


async def publisher_tick() -> None:
    """One wake of the loop. Order: expire → retract → (publishing on) reconcile → publish →
    (bot configured) review sweep → publish feed. Publishing runs BEFORE the Telegram I/O so a slow
    Telegram never delays a post."""
    for name, fn in (("expire", _expire_step), ("retract", retract_cycle)):
        counters = await _step(name, fn)
        if counters and any(counters.values()):
            logger.info("marketing publisher %s: %s", name, counters)
    if settings.MARKETING_ENABLED:
        for name, fn in (("reconcile", reconcile_cycle), ("publish", publish_cycle)):
            counters = await _step(name, fn)
            if counters and any(counters.values()):
                logger.info("marketing publisher %s: %s", name, counters)
    if review_service.is_configured():
        review = await _step("review", review_service.review_cycle)
        if review and (review.get("pending") or review.get("failed") or review.get("rate_limited")):
            logger.info("marketing review sweep: %s", review)
        feed = await _step("feed", publish_feed.feed_cycle)
        if feed and any(feed.values()):
            logger.info("marketing publish feed: %s", feed)


async def run_marketing_publisher_loop() -> None:
    """Background task registered by `app/main.py` via `_spawn`. Publishing sleeps quietly while
    MARKETING_ENABLED is False (checked every tick); expiry, retracts and the Telegram sweeps run
    regardless. Sleeps `MARKETING_PUBLISHER_INTERVAL_SECONDS`, or less when an Approve / confirmed
    Retract wakes it (`publisher_wake`)."""
    await asyncio.sleep(60)  # stagger past the startup pre-warm burst
    interval = max(int(settings.MARKETING_PUBLISHER_INTERVAL_SECONDS), 30)
    while True:
        await publisher_tick()
        await publisher_wake.wait(interval)
