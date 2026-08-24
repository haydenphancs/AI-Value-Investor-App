"""Price alerts — storage, CRUD and the 60-second evaluation loop.

The decision math lives in `price_alert_engine` (pure, exhaustively tested). This module
is the I/O half: which tickers to quote, which rules to load, and what to write back.

WHY A SEPARATE LOOP RATHER THAN FOLDING INTO THE UPDATES SWEEPER
----------------------------------------------------------------
The sweeper already pulls batch quotes every five minutes, so reusing it looks free.
Three reasons it is not:

  1. Its universe is `get_top_watchlist_tickers` capped at 200. An alerted ticker is
     frequently outside that — alerts skew toward the specific names a user is waiting
     on, not the popular ones — so the rule would simply never be evaluated.
  2. Five minutes is a poor latency for a threshold the user typed in themselves. This
     is the one notification with genuine time pressure, which is also why it is the one
     kind that ships `time-sensitive` and skips quiet hours.
  3. The sweeper's 150-second startup stagger and jitter are tuned for its own Gemini
     budget. Coupling the two makes both harder to reason about.

Cost is one `batch-quote` call per cycle over the DISTINCT alerted tickers, which is a
short list. `PRICE_ALERT_INTERVAL_SECONDS` is the single knob.

NO CROSS-INSTANCE CLAIM, deliberately. Two instances both evaluating is harmless: the
dedup key in `notification_events` is the lock, so the second instance's claim conflicts
and nothing is sent twice. The state writes (`last_price`, `armed`) are last-writer-wins
over near-identical values read microseconds apart.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from app.config import settings
from app.database import get_supabase
from app.integrations.fmp import get_fmp_client
from app.services.notification_kinds import KIND_PRICE_ALERT, ticker_route
from app.services.price_alert_engine import (
    KIND_PERCENT,
    VALID_KINDS,
    VALID_REPEAT_MODES,
    AlertDecision,
    evaluate_alert,
    finite_percent,
    finite_price,
)
from app.services.push_dispatch_service import get_push_dispatch_service, trading_date_et
from app.utils.market_hours import session_phase

logger = logging.getLogger(__name__)

TABLE = "price_alerts"

# Bound on the tickers quoted per cycle. Well above any realistic alerted universe;
# exceeding it is logged rather than silently truncated.
MAX_UNIVERSE = 500

# Bound on rules loaded per cycle.
MAX_RULES = 5_000


class PriceAlertLimitReached(Exception):
    """The user is at their alert quota."""


class PriceAlertInvalid(Exception):
    """The submitted rule is not usable."""


class PriceAlertUnavailable(Exception):
    """Storage could not be read or written.

    Raised rather than returning empty, for the same reason the settings and inbox
    services do: "you have no alerts" and "we could not read your alerts" look identical
    in the UI, and the second silently rendered as the first is a failure nobody reports.
    """


class PriceAlertService:
    def __init__(self) -> None:
        self.supabase = get_supabase()

    # ── CRUD ─────────────────────────────────────────────────────────

    def list_for_user(self, user_id: str, ticker: Optional[str] = None) -> List[dict]:
        try:
            query = (
                self.supabase.table(TABLE)
                .select("*")
                .eq("user_id", user_id)
                .order("created_at", desc=True)
                .limit(settings.PRICE_ALERT_MAX_PER_USER * 2)
            )
            if ticker:
                query = query.eq("ticker", ticker.upper())
            return query.execute().data or []
        except Exception as e:
            logger.error(
                "price alerts: list failed for user=%s (%s: %s)",
                user_id, type(e).__name__, e, exc_info=True,
            )
            raise PriceAlertUnavailable(str(e)) from e

    def _count_for_user(self, user_id: str, ticker: Optional[str] = None) -> int:
        query = (
            self.supabase.table(TABLE)
            .select("id")
            .eq("user_id", user_id)
            .eq("is_active", True)
            .limit(settings.PRICE_ALERT_MAX_PER_USER + 5)
        )
        if ticker:
            query = query.eq("ticker", ticker.upper())
        return len(query.execute().data or [])

    def create(
        self,
        user_id: str,
        *,
        ticker: str,
        kind: str,
        threshold: Any,
        asset_type: str = "stock",
        repeat_mode: str = "once",
        note: Optional[str] = None,
        seed_price: Optional[float] = None,
    ) -> dict:
        """Create one rule.

        `seed_price` is the live quote at creation time and it is LOAD-BEARING, not a
        nicety. With `last_price` NULL the engine seeds on the first evaluation and stays
        silent — correct, but it means an alert created at "above $250" on a stock
        already at $260 sits inert until the price dips below $250 and comes back. Seeding
        here makes that state explicit from the first cycle, and lets the endpoint tell
        the user "AAPL is already above $250 — this fires on the next crossing" instead of
        leaving them to wonder.
        """
        symbol = (ticker or "").strip().upper()
        if not symbol:
            raise PriceAlertInvalid("A ticker is required.")
        if kind not in VALID_KINDS:
            raise PriceAlertInvalid(f"Unknown alert type {kind!r}.")
        if repeat_mode not in VALID_REPEAT_MODES:
            raise PriceAlertInvalid(f"Unknown repeat mode {repeat_mode!r}.")

        # Validate the threshold through the SAME finite guards the evaluator uses, so a
        # value that would silently never fire is refused at creation instead of becoming
        # a row that does nothing forever.
        limit = finite_percent(threshold) if kind == KIND_PERCENT else finite_price(threshold)
        if limit is None or limit <= 0:
            raise PriceAlertInvalid("Enter a positive number.")
        if kind == KIND_PERCENT and limit > 100:
            raise PriceAlertInvalid("Percent moves above 100% aren't supported.")

        try:
            if self._count_for_user(user_id) >= settings.PRICE_ALERT_MAX_PER_USER:
                raise PriceAlertLimitReached(
                    f"You can track up to {settings.PRICE_ALERT_MAX_PER_USER} price alerts."
                )
            if self._count_for_user(user_id, symbol) >= settings.PRICE_ALERT_MAX_PER_TICKER_PER_USER:
                raise PriceAlertLimitReached(
                    f"You can track up to {settings.PRICE_ALERT_MAX_PER_TICKER_PER_USER} "
                    f"alerts per ticker."
                )
        except PriceAlertLimitReached:
            raise
        except Exception as e:
            logger.error(
                "price alerts: quota check failed for user=%s (%s: %s)",
                user_id, type(e).__name__, e, exc_info=True,
            )
            raise PriceAlertUnavailable(str(e)) from e

        row = {
            "user_id": user_id,
            "ticker": symbol,
            "asset_type": (asset_type or "stock").strip().lower() or "stock",
            "kind": kind,
            "threshold": limit,
            "repeat_mode": repeat_mode,
            "note": (note or None),
            "last_price": finite_price(seed_price),
        }
        try:
            result = self.supabase.table(TABLE).insert(row).execute()
            return (result.data or [row])[0]
        except Exception as e:
            if getattr(e, "code", None) == "23505" or "duplicate key" in str(e).lower():
                raise PriceAlertInvalid("You already have that alert.") from e
            logger.error(
                "price alerts: create failed for user=%s ticker=%s (%s: %s)",
                user_id, symbol, type(e).__name__, e, exc_info=True,
            )
            raise PriceAlertUnavailable(str(e)) from e

    def update(self, user_id: str, alert_id: str, patch: Dict[str, Any]) -> Optional[dict]:
        """Patch a rule. Returns None when the id is not this user's.

        ⚠️ SCOPED ON user_id IN ADDITION TO id. Filtering on `id` alone is a textbook
        IDOR — one user editing another's alerts — and because the backend holds the
        service-role key, this in-code filter is the effective wall rather than RLS.
        """
        allowed = {"threshold", "is_active", "repeat_mode", "note"}
        clean: Dict[str, Any] = {}
        for key, value in (patch or {}).items():
            if key not in allowed:
                continue
            if key == "threshold":
                limit = finite_price(value)
                if limit is None:
                    raise PriceAlertInvalid("Enter a positive number.")
                clean["threshold"] = limit
                # A new threshold invalidates the latch AND the baseline: the old
                # `last_price` was measured against a different line, and leaving the
                # latch down would keep a re-armed rule silent.
                clean["armed"] = True
                clean["last_price"] = None
            elif key == "repeat_mode":
                if value not in VALID_REPEAT_MODES:
                    raise PriceAlertInvalid(f"Unknown repeat mode {value!r}.")
                clean["repeat_mode"] = value
            elif key == "is_active":
                clean["is_active"] = bool(value)
                if clean["is_active"]:
                    # Re-enabling a fired one-shot rule must re-arm it, or it would be
                    # active and permanently silent.
                    clean["armed"] = True
                    clean["last_price"] = None
            else:
                clean["note"] = (value or None)

        if not clean:
            raise PriceAlertInvalid("Nothing to update.")
        clean["updated_at"] = datetime.now(timezone.utc).isoformat()

        try:
            result = (
                self.supabase.table(TABLE)
                .update(clean)
                .eq("id", alert_id)
                .eq("user_id", user_id)
                .execute()
            )
            rows = result.data or []
            return rows[0] if rows else None
        except Exception as e:
            logger.error(
                "price alerts: update failed for user=%s id=%s (%s: %s)",
                user_id, alert_id, type(e).__name__, e, exc_info=True,
            )
            raise PriceAlertUnavailable(str(e)) from e

    def delete(self, user_id: str, alert_id: str) -> bool:
        """Delete a rule. Same user-scoping requirement as `update`."""
        try:
            result = (
                self.supabase.table(TABLE)
                .delete()
                .eq("id", alert_id)
                .eq("user_id", user_id)
                .execute()
            )
            return bool(result.data)
        except Exception as e:
            logger.error(
                "price alerts: delete failed for user=%s id=%s (%s: %s)",
                user_id, alert_id, type(e).__name__, e, exc_info=True,
            )
            raise PriceAlertUnavailable(str(e)) from e

    # ── evaluation ───────────────────────────────────────────────────

    def _active_universe(self) -> List[str]:
        try:
            rows = (
                self.supabase.table(TABLE)
                .select("ticker")
                .eq("is_active", True)
                .limit(MAX_RULES)
                .execute()
                .data
                or []
            )
        except Exception as e:
            logger.warning(
                "price alerts: universe read failed (%s: %s) — no evaluation this cycle",
                type(e).__name__, e,
            )
            return []
        tickers = list(dict.fromkeys(str(r["ticker"]).upper() for r in rows if r.get("ticker")))
        if len(tickers) > MAX_UNIVERSE:
            logger.warning(
                "price alerts: %d distinct alerted tickers exceeds the %d cap — "
                "evaluating the first %d this cycle",
                len(tickers), MAX_UNIVERSE, MAX_UNIVERSE,
            )
            tickers = tickers[:MAX_UNIVERSE]
        return tickers

    def _active_rules(self, tickers: List[str]) -> List[dict]:
        try:
            return (
                self.supabase.table(TABLE)
                .select("id, user_id, ticker, asset_type, kind, threshold, repeat_mode, "
                        "armed, last_price, trigger_count")
                .eq("is_active", True)
                .in_("ticker", tickers)
                .limit(MAX_RULES)
                .execute()
                .data
                or []
            )
        except Exception as e:
            logger.warning(
                "price alerts: rule read failed (%s: %s) — no evaluation this cycle",
                type(e).__name__, e,
            )
            return []

    def _persist(self, rule: dict, decision: AlertDecision) -> None:
        """Write the rule's new state. Best-effort but LOUD.

        A lost state write is not a duplicate notification — the dedup claim already
        blocks that — but a lost `armed=False` means the latch never drops, so the next
        oscillation fires again. Worth a warning every time.
        """
        patch: Dict[str, Any] = {
            "last_price": decision.new_last_price,
            "armed": decision.new_armed,
            "last_evaluated_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        if decision.fire:
            patch["last_triggered_at"] = datetime.now(timezone.utc).isoformat()
            patch["trigger_count"] = int(rule.get("trigger_count") or 0) + 1
        if decision.deactivate:
            patch["is_active"] = False
        try:
            self.supabase.table(TABLE).update(patch).eq("id", rule["id"]).execute()
        except Exception as e:
            logger.warning(
                "price alerts: state write failed for id=%s (%s: %s) — the latch may "
                "re-fire on the next crossing",
                rule.get("id"), type(e).__name__, e,
            )

    @staticmethod
    def fire_copy(rule: dict, price: float, decision: AlertDecision) -> Tuple[str, str]:
        """Copy for a fired alert.

        Purely factual — the user set this threshold themselves, so restating it and the
        current price is the entire useful content. No suggestion about what to do next.
        """
        symbol = str(rule.get("ticker") or "").upper()
        kind = rule.get("kind")
        threshold = finite_price(rule.get("threshold")) or 0.0
        if kind == KIND_PERCENT:
            return (
                f"{symbol} moved {threshold:g}%",
                f"{symbol} is at ${price:,.2f} — past your {threshold:g}% alert.",
            )
        direction = "above" if kind == "price_above" else "below"
        return (
            f"{symbol} is {direction} ${threshold:,.2f}",
            f"{symbol} is trading at ${price:,.2f}.",
        )

    def dedup_key(self, rule: dict, repeat_mode: str) -> str:
        """One-shot rules key on the alert id alone (once ever). Daily rules add the ET
        trading date, so a re-armed rule can fire again tomorrow but not twice today."""
        base = f"pa:{rule.get('id')}"
        return base if repeat_mode == "once" else f"{base}:{trading_date_et()}"

    async def evaluate_once(self) -> Dict[str, int]:
        """One evaluation cycle. Never raises."""
        stats = {"tickers": 0, "rules": 0, "fired": 0, "sent": 0}

        tickers = await asyncio.to_thread(self._active_universe)
        if not tickers:
            return stats
        stats["tickers"] = len(tickers)

        try:
            quotes = await get_fmp_client().get_batch_quotes_bulk(tickers)
        except Exception as e:
            logger.warning(
                "price alerts: batch quote failed for %d ticker(s) (%s: %s) — "
                "skipping this cycle",
                len(tickers), type(e).__name__, e,
            )
            return stats

        by_symbol = {
            str(q.get("symbol") or "").upper(): q
            for q in (quotes or [])
            if isinstance(q, dict) and q.get("symbol")
        }

        rules = await asyncio.to_thread(self._active_rules, tickers)
        stats["rules"] = len(rules)
        dispatcher = get_push_dispatch_service()

        for rule in rules:
            symbol = str(rule.get("ticker") or "").upper()
            quote = by_symbol.get(symbol)
            # A ticker missing from the bulk response (delisted mid-session, halted, or
            # simply not returned) yields no observation. `evaluate_alert` holds the
            # baseline rather than clobbering it — see the engine's decision 4.
            decision = evaluate_alert(
                kind=rule.get("kind") or "",
                threshold=rule.get("threshold"),
                repeat_mode=rule.get("repeat_mode") or "once",
                armed=bool(rule.get("armed", True)),
                last_price=rule.get("last_price"),
                price=(quote or {}).get("price"),
                change_percent=(quote or {}).get("changePercentage"),
                rearm_pct=settings.PRICE_ALERT_REARM_PCT,
            )

            await asyncio.to_thread(self._persist, rule, decision)
            if not decision.fire:
                continue
            stats["fired"] += 1

            price = finite_price((quote or {}).get("price")) or 0.0
            title, body = self.fire_copy(rule, price, decision)
            stats["sent"] += await dispatcher.notify_users(
                [rule["user_id"]],
                kind=KIND_PRICE_ALERT,
                title=title,
                body=body,
                dedup_key=self.dedup_key(rule, rule.get("repeat_mode") or "once"),
                route={
                    # From the ROW, not hardcoded: a crypto alert must open the crypto
                    # screen. The old tap handler hardcoded `.stock` for everything.
                    **ticker_route(
                        KIND_PRICE_ALERT,
                        symbol,
                        asset_type=str(rule.get("asset_type") or "stock"),
                    ),
                    # Not read by the client yet; carried so a future tap can open the
                    # rule that fired rather than just the ticker.
                    "alert_id": str(rule.get("id") or ""),
                },
            )

        if stats["fired"]:
            logger.info("price alerts: %s", stats)
        return stats


_service: Optional[PriceAlertService] = None


def get_price_alert_service() -> PriceAlertService:
    global _service
    if _service is None:
        _service = PriceAlertService()
    return _service


async def run_price_alert_loop() -> None:
    """Background loop: evaluate every active rule on a short cadence.

    Gated on `session_phase() != "closed"`, i.e. 04:00-20:00 ET — the extended session,
    not just regular hours, because a threshold crossed in pre-market is exactly the kind
    of move a user set an alert for. Outside that window there are no new prices, and the
    first cycle after the open catches gap crossings correctly (the engine treats
    yesterday's close → today's open as a genuine crossing).
    """
    await asyncio.sleep(30)
    interval = max(settings.PRICE_ALERT_INTERVAL_SECONDS, 15)
    while True:
        try:
            if session_phase() != "closed":
                await get_price_alert_service().evaluate_once()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(
                "Price alert cycle failed (%s: %s)", type(e).__name__, e, exc_info=True
            )
        await asyncio.sleep(interval)
