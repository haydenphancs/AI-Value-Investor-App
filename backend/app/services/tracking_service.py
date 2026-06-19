"""
Tracking Service — aggregates watchlist + FMP market data for the Assets tab.

Design (mirrors home_service.py):
- All external calls (FMP, Supabase) run concurrently via asyncio.gather.
- Each section degrades gracefully: if one data source fails, the rest
  still return so the Assets tab always loads.
- Sparkline data is cached per-ticker for 5 minutes.
- Full feed is cached per-user for 30 seconds.
"""

import asyncio
import time as _time
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any, Tuple
import logging

from app.integrations.fmp import get_fmp_client, FMPClient
from app.services.chart_helper import fetch_chart_data
from app.database import get_supabase
from app.schemas.tracking import (
    TrackedAssetResponse,
    AlertResponse,
    TrackingFeedResponse,
    WhaleTradeItemResponse,
    AnalystRatingItemResponse,
    InsiderTransactionItemResponse,
)
from app.services._insider_common import classify_for_alerts
from app.services._analyst_common import classify_for_alerts as classify_analyst_for_alerts
from app.services._earnings_common import (
    parse_fmp_timing,
    timing_sentence,
    alert_report_time,
)

logger = logging.getLogger(__name__)

# ── Simple TTL Caches ───────────────────────────────────────────────

_feed_cache: Dict[str, Tuple[float, Any]] = {}
FEED_CACHE_TTL = 30  # 30 seconds per-user

_sparkline_cache: Dict[str, Tuple[float, List[float]]] = {}
SPARKLINE_CACHE_TTL = 300  # 5 minutes per-ticker


def _feed_cache_get(user_id: str) -> Optional[TrackingFeedResponse]:
    entry = _feed_cache.get(user_id)
    if entry is None:
        return None
    ts, value = entry
    if _time.monotonic() - ts > FEED_CACHE_TTL:
        del _feed_cache[user_id]
        return None
    return value


def _feed_cache_set(user_id: str, value: TrackingFeedResponse) -> None:
    _feed_cache[user_id] = (_time.monotonic(), value)


def _sparkline_cache_get(ticker: str) -> Optional[List[float]]:
    entry = _sparkline_cache.get(ticker)
    if entry is None:
        return None
    ts, value = entry
    if _time.monotonic() - ts > SPARKLINE_CACHE_TTL:
        del _sparkline_cache[ticker]
        return None
    return value


def _sparkline_cache_set(ticker: str, value: List[float]) -> None:
    _sparkline_cache[ticker] = (_time.monotonic(), value)


def _downsample(values: List[float], target: int) -> List[float]:
    """Evenly downsample to at most *target* points, always keeping the FIRST
    and LAST (the iOS SparklineView colors green/red off values[0] and dots
    values[-1], so the open baseline and end point must survive)."""
    if len(values) <= target:
        return values
    step = (len(values) - 1) / (target - 1)
    idxs = sorted({round(i * step) for i in range(target)} | {0, len(values) - 1})
    return [values[i] for i in idxs]


# ── Service ─────────────────────────────────────────────────────────


class TrackingService:
    """Builds the enriched tracking feed from Supabase watchlist + FMP data."""

    def __init__(self) -> None:
        self.fmp: FMPClient = get_fmp_client()

    async def get_tracking_feed(self, user_id: str) -> TrackingFeedResponse:
        """Return complete tracking feed for the Assets tab."""

        # Check cache first
        cached = _feed_cache_get(user_id)
        if cached is not None:
            logger.debug("Tracking feed served from cache for user %s", user_id)
            return cached

        # 1. Fetch user's watchlist from Supabase
        sb = get_supabase()
        try:
            result = (
                sb.table("watchlist_items")
                .select("*")
                .eq("user_id", user_id)
                .order("added_at", desc=True)
                .execute()
            )
            watchlist = result.data or []
        except Exception as exc:
            logger.error("Failed to fetch watchlist for user %s: %s", user_id, exc)
            return TrackingFeedResponse()

        if not watchlist:
            return TrackingFeedResponse()

        tickers = [item["ticker"] for item in watchlist]

        # 2. Fetch data concurrently
        quotes_task = self._get_batch_quotes(tickers)
        sparklines_task = self._get_all_sparklines(tickers)
        earnings_task = self._get_earnings_alerts(tickers)
        whale_task = self._get_whale_trade_alerts(tickers)
        analyst_task = self._get_analyst_rating_alerts(tickers)
        insider_task = self._get_insider_transaction_alerts(tickers)

        results = await asyncio.gather(
            quotes_task,
            sparklines_task,
            earnings_task,
            whale_task,
            analyst_task,
            insider_task,
            return_exceptions=True,
        )

        quotes_map: Dict[str, Dict] = (
            results[0] if not isinstance(results[0], BaseException) else {}
        )
        sparklines_map: Dict[str, List[float]] = (
            results[1] if not isinstance(results[1], BaseException) else {}
        )
        earnings_alerts: List[AlertResponse] = (
            results[2] if not isinstance(results[2], BaseException) else []
        )
        whale_alerts: List[AlertResponse] = (
            results[3] if not isinstance(results[3], BaseException) else []
        )
        analyst_alerts: List[AlertResponse] = (
            results[4] if not isinstance(results[4], BaseException) else []
        )
        insider_alerts: List[AlertResponse] = (
            results[5] if not isinstance(results[5], BaseException) else []
        )

        section_names = [
            "batch_quotes",
            "sparklines",
            "earnings_alerts",
            "whale_trade_alerts",
            "analyst_rating_alerts",
            "insider_transaction_alerts",
        ]
        for idx, res in enumerate(results):
            if isinstance(res, BaseException):
                logger.error("[Tracking] %s failed: %s", section_names[idx], res)

        alerts: List[AlertResponse] = (
            earnings_alerts + whale_alerts + analyst_alerts + insider_alerts
        )

        # 3. Merge watchlist + quotes + sparklines into TrackedAssetResponse
        assets: List[TrackedAssetResponse] = []
        for item in watchlist:
            ticker = item.get("ticker", "")
            if not ticker:
                logger.warning("[Tracking] Skipping watchlist item with no ticker: %s", item)
                continue
            try:
                quote = quotes_map.get(ticker, {})
                sparkline = sparklines_map.get(ticker, [])

                change_pct = quote.get("changePercentage") or 0
                price = quote.get("price") or 0
                market_cap_raw = quote.get("marketCap")

                # Holding info — these columns live on watchlist_items and
                # are populated by the Portfolio Insights config sheet. iOS
                # uses them to pre-fill the inputs and decide which rows
                # count toward the diversification score.
                shares = item.get("shares")
                stored_value = item.get("market_value")

                assets.append(
                    TrackedAssetResponse(
                        ticker=ticker,
                        company_name=item.get("company_name") or quote.get("name") or ticker,
                        price=round(float(price), 2),
                        change_percent=round(float(change_pct), 2),
                        sparkline_data=sparkline,
                        logo_url=item.get("logo_url"),
                        sector=item.get("sector") or quote.get("sector"),
                        country=item.get("country") or quote.get("country"),
                        market_cap=float(market_cap_raw) if market_cap_raw else None,
                        shares=float(shares) if shares is not None else None,
                        market_value=float(stored_value) if stored_value is not None else None,
                        asset_type=item.get("asset_type"),
                    )
                )
            except Exception as exc:
                logger.error("[Tracking] Failed to enrich ticker %s: %s", ticker, exc)
                # Still include the asset with minimal data so it shows in the list
                assets.append(
                    TrackedAssetResponse(
                        ticker=ticker,
                        company_name=item.get("company_name") or ticker,
                    )
                )

        feed = TrackingFeedResponse(assets=assets, alerts=alerts)
        _feed_cache_set(user_id, feed)
        return feed

    # ── Batch Quotes ────────────────────────────────────────────────

    async def _get_batch_quotes(
        self, tickers: List[str]
    ) -> Dict[str, Dict[str, Any]]:
        """Fetch real-time quotes for all tickers in a single FMP call."""
        try:
            quotes = await self.fmp.get_batch_quotes(tickers)
            return {q["symbol"]: q for q in quotes if q.get("symbol")}
        except Exception as exc:
            logger.warning("Batch quotes failed: %s", exc)
            return {}

    # ── Sparklines ──────────────────────────────────────────────────

    async def _get_all_sparklines(
        self, tickers: List[str]
    ) -> Dict[str, List[float]]:
        """Fetch sparkline data for all tickers concurrently."""

        async def _fetch_one(ticker: str) -> Tuple[str, List[float]]:
            # Check per-ticker cache
            cached = _sparkline_cache_get(ticker)
            if cached is not None:
                return (ticker, cached)

            try:
                # Use the SAME series the TickerDetailView 1D chart draws:
                # 5-min intraday bars, regular market hours only, oldest-first
                # (via the shared chart_helper). This keeps the holdings-card
                # sparkline visually consistent with the chart the user sees
                # when they open the ticker — the old path drew a ~1-month
                # daily-EOD line, which looked nothing like the 1D chart.
                bars = await fetch_chart_data(self.fmp, ticker, "1D")
                if not bars:
                    # Honest empty — never fabricate. iOS SparklineView draws
                    # nothing for an empty/1-point series.
                    _sparkline_cache_set(ticker, [])
                    return (ticker, [])

                # Keep only the most recent trading day — mirrors the iOS
                # TradingDayHelper.filterToLatestDay step, so the multi-day
                # warm-up bars don't fold several sessions into one mini-chart.
                last_day = str(bars[-1].get("date", ""))[:10]  # "YYYY-MM-DD"
                day_bars = [
                    b for b in bars if str(b.get("date", "")).startswith(last_day)
                ]

                closes = [
                    float(b["close"]) for b in day_bars if b.get("close") is not None
                ]
                if len(closes) < 2:
                    _sparkline_cache_set(ticker, [])
                    return (ticker, [])

                # ~78 five-min bars per session → downsample so the card payload
                # stays small and the tiny chart reads cleanly.
                sparkline = [round(c, 2) for c in _downsample(closes, 30)]
                _sparkline_cache_set(ticker, sparkline)
                return (ticker, sparkline)
            except Exception as exc:
                logger.warning(
                    "Sparkline (1D intraday) for %s failed: %s: %s",
                    ticker, type(exc).__name__, exc,
                )
                return (ticker, [])

        results = await asyncio.gather(*[_fetch_one(t) for t in tickers])
        return dict(results)

    # ── Earnings Alerts ─────────────────────────────────────────────

    async def _get_earnings_alerts(
        self, watchlist_tickers: List[str]
    ) -> List[AlertResponse]:
        """Fetch upcoming earnings from FMP, filtered to user's watchlist."""
        try:
            today = datetime.now().strftime("%Y-%m-%d")
            future = (datetime.now() + timedelta(days=14)).strftime("%Y-%m-%d")
            calendar = await self.fmp.get_earnings_calendar(
                from_date=today, to_date=future
            )
            if not calendar:
                return []

            ticker_set = {t.upper() for t in watchlist_tickers}
            alerts: List[AlertResponse] = []

            for entry in calendar:
                symbol = (entry.get("symbol") or "").upper()
                if symbol not in ticker_set:
                    continue

                # Parse date for day/month
                date_str = entry.get("date", "")
                day = None
                month = None
                if date_str:
                    try:
                        dt = datetime.strptime(date_str, "%Y-%m-%d")
                        day = dt.day
                        month = dt.strftime("%b").upper()
                    except ValueError:
                        pass

                # Determine report time (shared parser keeps this in sync
                # with the Financials Earnings section's next_earnings_date).
                timing_token = parse_fmp_timing(entry.get("time"))
                report_time = alert_report_time(timing_token)

                # Consensus numbers — emitted as structured fields so the
                # iOS detail view shows "EPS Est: $X | Rev Est: $YB" in the
                # Consensus row without repeating the sentence.
                try:
                    eps_est = float(entry["epsEstimated"]) if entry.get("epsEstimated") is not None else None
                except (TypeError, ValueError):
                    eps_est = None
                try:
                    rev_est = float(entry["revenueEstimated"]) if entry.get("revenueEstimated") is not None else None
                except (TypeError, ValueError):
                    rev_est = None

                # One-line description for the card. iOS rebuilds its own
                # version for the alert card, but keep a sane fallback here.
                date_phrase = f"on {month} {day}" if day and month else ""
                sentence = timing_sentence(timing_token)
                pieces = [f"{symbol} reports earnings"]
                if date_phrase:
                    pieces.append(date_phrase)
                if sentence:
                    pieces.append(sentence)
                full_desc = " ".join(pieces) + "."

                alerts.append(
                    AlertResponse(
                        type="earnings",
                        ticker=symbol,
                        company_name=entry.get("companyName") or symbol,
                        title="Earnings Alert",
                        description=full_desc,
                        day=day,
                        month=month,
                        report_time=report_time,
                        eps_estimate=eps_est,
                        revenue_estimate=rev_est,
                    )
                )

            return alerts

        except Exception as exc:
            logger.warning("Earnings alerts failed: %s", exc)
            return []

    # ── Whale Trade Alerts ──────────────────────────────────────────

    async def _get_whale_trade_alerts(
        self, watchlist_tickers: List[str]
    ) -> List[AlertResponse]:
        """Aggregate recent whale trades on watchlist tickers.

        Returns at most two rolled-up alerts: one "Whales Bought" and one
        "Whales Sold", each carrying a per-ticker breakdown in
        `whale_trade_items`.
        """
        if not watchlist_tickers:
            return []

        ticker_list = [t.upper() for t in watchlist_tickers]
        cutoff_iso = (datetime.now() - timedelta(days=7)).isoformat()

        sb = get_supabase()
        try:
            result = (
                sb.table("whale_trades")
                .select("ticker, company_name, action, amount, date, created_at, whale_id, whales(name, avatar_url)")
                .in_("ticker", ticker_list)
                .gte("created_at", cutoff_iso)
                .order("created_at", desc=True)
                .limit(500)
                .execute()
            )
            rows = result.data or []
        except Exception as exc:
            logger.warning("[Tracking] whale_trades query failed: %s", exc)
            return []

        # First pass: bucket by (ticker, action) — each bucket becomes one item.
        buckets: Dict[Tuple[str, str], Dict[str, Any]] = {}
        for row in rows:
            ticker = (row.get("ticker") or "").upper()
            action = (row.get("action") or "").upper()
            if not ticker or action not in ("BOUGHT", "SOLD"):
                continue
            key = (ticker, action)
            bucket = buckets.setdefault(
                key,
                {
                    "company_name": row.get("company_name") or ticker,
                    "total_amount": 0.0,
                    "whale_ids": set(),
                    "lead_whale_id": None,
                    "lead_whale_name": None,
                    "lead_whale_avatar": None,
                },
            )
            try:
                bucket["total_amount"] += float(row.get("amount") or 0)
            except (TypeError, ValueError):
                pass
            whale_id = row.get("whale_id")
            if whale_id:
                bucket["whale_ids"].add(whale_id)
            if bucket["lead_whale_name"] is None:
                whale = row.get("whales") or {}
                if isinstance(whale, dict):
                    bucket["lead_whale_id"] = whale_id
                    bucket["lead_whale_name"] = whale.get("name")
                    bucket["lead_whale_avatar"] = whale.get("avatar_url")

        # Second pass: group items by action → one rolled-up alert per action.
        action_groups: Dict[str, List[WhaleTradeItemResponse]] = {
            "bought": [],
            "sold": [],
        }
        action_totals: Dict[str, float] = {"bought": 0.0, "sold": 0.0}
        for (ticker, action), bucket in buckets.items():
            whale_count = len(bucket["whale_ids"])
            if whale_count == 0:
                continue
            action_word = "bought" if action == "BOUGHT" else "sold"
            amount_label = _format_amount(bucket["total_amount"])
            action_groups[action_word].append(
                WhaleTradeItemResponse(
                    ticker=ticker,
                    company_name=bucket["company_name"],
                    whale_count=whale_count,
                    amount=amount_label,
                    raw_amount=bucket["total_amount"],
                    lead_whale_id=bucket["lead_whale_id"],
                    lead_whale_name=bucket["lead_whale_name"],
                    lead_whale_avatar_name=bucket["lead_whale_avatar"],
                )
            )
            action_totals[action_word] += bucket["total_amount"]

        alerts: List[AlertResponse] = []
        for action_word in ("bought", "sold"):
            items = action_groups[action_word]
            if not items:
                continue
            # Largest position first
            items.sort(
                key=lambda it: _amount_sort_key(it.amount), reverse=True
            )
            total_label = _format_amount(action_totals[action_word])
            title = "Whales Bought" if action_word == "bought" else "Whales Sold"
            description = (
                f"{_join_tickers([it.ticker for it in items])} this week"
                f" — totaling {total_label}."
            )
            alerts.append(
                AlertResponse(
                    type="whale_trade",
                    title=title,
                    description=description,
                    action=action_word,
                    total_amount=total_label,
                    time_window_label="this week",
                    whale_trade_items=items,
                )
            )

        return alerts

    # ── Analyst Rating Alerts ───────────────────────────────────────

    async def _get_analyst_rating_alerts(
        self, watchlist_tickers: List[str]
    ) -> List[AlertResponse]:
        """Roll all recent analyst grade changes into a single alert."""
        if not watchlist_tickers:
            return []

        cutoff = datetime.now() - timedelta(days=14)

        async def _fetch_one(ticker: str) -> Optional[AnalystRatingItemResponse]:
            try:
                grades = await self.fmp.get_grades(ticker, limit=20)
            except Exception as exc:
                logger.warning("Analyst grades for %s failed: %s", ticker, exc)
                return None
            if not isinstance(grades, list) or not grades:
                return None

            for entry in grades:
                date_str = entry.get("publishedDate") or entry.get("date") or ""
                dt = _parse_date(date_str)
                if dt is None or dt < cutoff:
                    continue

                firm = (
                    entry.get("gradingCompany")
                    or entry.get("analystCompany")
                    or entry.get("newsPublisher")
                    or "Analyst"
                )
                new_rating = entry.get("newGrade") or ""
                previous_rating = entry.get("previousGrade") or None

                # Shared normalizer keeps this in lockstep with the
                # Analysis tab's Actions screen — same row will be
                # classified the same way in both views.
                rating_action, material = classify_analyst_for_alerts(
                    entry.get("action"), previous_rating, new_rating
                )
                if not material:
                    # Maintain / reiterate — firm didn't change its view.
                    # Keep scanning for a material action within the window.
                    continue

                price_target = _opt_float(entry.get("priceTarget"))
                previous_price_target = _opt_float(entry.get("previousPriceTarget"))

                return AnalystRatingItemResponse(
                    ticker=ticker.upper(),
                    firm_name=firm,
                    rating_action=rating_action,
                    new_rating=new_rating,
                    previous_rating=previous_rating,
                    price_target=price_target,
                    previous_price_target=previous_price_target,
                    day=dt.day,
                    month=dt.strftime("%b").upper(),
                )
            return None

        results = await asyncio.gather(
            *[_fetch_one(t) for t in watchlist_tickers], return_exceptions=True
        )
        items: List[AnalystRatingItemResponse] = [
            r for r in results if isinstance(r, AnalystRatingItemResponse)
        ]
        if not items:
            return []

        # Most notable first: upgrades/downgrades above reiterations
        rank = {"upgrade": 0, "downgrade": 1, "initiate": 2, "reiterate": 3}
        items.sort(key=lambda it: rank.get(it.rating_action, 4))

        tickers = [it.ticker for it in items]
        count_label = (
            "1 rating change" if len(items) == 1 else f"{len(items)} rating changes"
        )
        description = (
            f"{count_label} on {_join_tickers(tickers)} this week."
        )
        return [
            AlertResponse(
                type="analyst_rating",
                title="Analyst Ratings",
                description=description,
                time_window_label="this week",
                analyst_rating_items=items,
            )
        ]

    # ── Insider Transaction Alerts ──────────────────────────────────

    async def _get_insider_transaction_alerts(
        self, watchlist_tickers: List[str]
    ) -> List[AlertResponse]:
        """Roll up recent notable insider (Form 4) transactions into at most
        two alerts: one "Insider Bought" and one "Insider Sold".
        """
        if not watchlist_tickers:
            return []

        cutoff = datetime.now() - timedelta(days=14)
        MIN_AMOUNT = 100_000  # $100K threshold to reduce noise

        async def _fetch_one(
            ticker: str,
        ) -> Optional[Tuple[str, InsiderTransactionItemResponse, float]]:
            """Return the most notable insider transaction per ticker, if any.

            Returns (action_word, item, raw_amount).
            """
            try:
                trades = await self.fmp.get_insider_trading(ticker, limit=30)
            except Exception as exc:
                logger.warning("Insider trading for %s failed: %s", ticker, exc)
                return None
            if not isinstance(trades, list) or not trades:
                return None

            # Aggregate by (insider_name, transaction_date, action) because
            # Form 4 filings often split one decision across many small rows.
            buckets: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
            for tx in trades:
                date_str = tx.get("transactionDate") or tx.get("filingDate") or ""
                dt = _parse_date(date_str)
                if dt is None or dt < cutoff:
                    continue

                # Shared classifier keeps this in lockstep with the Holders
                # tab — only surface trades the Holders tab would label
                # "Informative Buy/Sell". Option exercises, tax withholding,
                # and composite S+OE sales are filtered out.
                action_word, informative = classify_for_alerts(
                    tx.get("transactionType") or ""
                )
                if not informative:
                    continue

                try:
                    shares = float(tx.get("securitiesTransacted") or 0)
                    price = float(tx.get("price") or 0)
                    amount = shares * price
                except (TypeError, ValueError):
                    continue
                if amount <= 0:
                    continue

                insider_name = (tx.get("reportingName") or "Insider").strip()
                insider_title = (tx.get("typeOfOwner") or "Officer").strip()
                key = (insider_name, date_str, action_word)
                bucket = buckets.setdefault(
                    key,
                    {
                        "insider_title": insider_title,
                        "amount": 0.0,
                        "dt": dt,
                    },
                )
                bucket["amount"] += amount

            if not buckets:
                return None

            best: Optional[Tuple[Tuple[str, str, str], Dict[str, Any]]] = None
            for key, bucket in buckets.items():
                if bucket["amount"] < MIN_AMOUNT:
                    continue
                if best is None or bucket["amount"] > best[1]["amount"]:
                    best = (key, bucket)
            if best is None:
                return None

            (insider_name, _, action_word), bucket = best
            item = InsiderTransactionItemResponse(
                ticker=ticker.upper(),
                insider_name=insider_name,
                insider_title=bucket["insider_title"],
                amount=_format_amount(bucket["amount"]),
                raw_amount=bucket["amount"],
                day=bucket["dt"].day,
                month=bucket["dt"].strftime("%b").upper(),
            )
            return (action_word, item, bucket["amount"])

        results = await asyncio.gather(
            *[_fetch_one(t) for t in watchlist_tickers], return_exceptions=True
        )

        action_groups: Dict[str, List[InsiderTransactionItemResponse]] = {
            "bought": [],
            "sold": [],
        }
        action_totals: Dict[str, float] = {"bought": 0.0, "sold": 0.0}
        for res in results:
            if not isinstance(res, tuple):
                continue
            action_word, item, raw_amount = res
            action_groups[action_word].append(item)
            action_totals[action_word] += raw_amount

        alerts: List[AlertResponse] = []
        for action_word in ("bought", "sold"):
            items = action_groups[action_word]
            if not items:
                continue
            items.sort(key=lambda it: _amount_sort_key(it.amount), reverse=True)
            total_label = _format_amount(action_totals[action_word])
            title = "Insider Bought" if action_word == "bought" else "Insider Sold"
            description = (
                f"{_join_tickers([it.ticker for it in items])} this week"
                f" — totaling {total_label}."
            )
            alerts.append(
                AlertResponse(
                    type="insider_transaction",
                    title=title,
                    description=description,
                    action=action_word,
                    total_amount=total_label,
                    time_window_label="this week",
                    insider_transaction_items=items,
                )
            )

        return alerts


# ── Helpers ─────────────────────────────────────────────────────────


def _format_amount(value: float) -> str:
    """Format a dollar amount as $X.XB / $X.XM / $XK."""
    amt = abs(value)
    if amt >= 1_000_000_000:
        return f"${amt / 1_000_000_000:.2f}B"
    if amt >= 1_000_000:
        return f"${amt / 1_000_000:.1f}M"
    if amt >= 1_000:
        return f"${amt / 1_000:.0f}K"
    return f"${amt:.0f}"


def _amount_sort_key(label: str) -> float:
    """Convert a $X.XB / $X.XM / $XK label back to a float for sorting."""
    if not label:
        return 0.0
    s = label.replace("$", "").replace(",", "").strip()
    mult = 1.0
    if s.endswith("B"):
        mult, s = 1_000_000_000.0, s[:-1]
    elif s.endswith("M"):
        mult, s = 1_000_000.0, s[:-1]
    elif s.endswith("K"):
        mult, s = 1_000.0, s[:-1]
    try:
        return float(s) * mult
    except ValueError:
        return 0.0


def _join_tickers(tickers: List[str], max_visible: int = 4) -> str:
    """Join tickers for card descriptions, truncating long lists."""
    if not tickers:
        return ""
    if len(tickers) <= max_visible:
        return ", ".join(tickers)
    head = ", ".join(tickers[:max_visible])
    return f"{head} and {len(tickers) - max_visible} more"


def _opt_float(value: Any) -> Optional[float]:
    """Return ``float(value)`` when possible, else ``None``."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_date(date_str: str) -> Optional[datetime]:
    """Parse a date string in common FMP formats."""
    if not date_str:
        return None
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(date_str, fmt)
        except ValueError:
            continue
    return None


_RATING_RANK = {
    "strong sell": 0, "sell": 1, "underperform": 1, "underweight": 1,
    "hold": 2, "neutral": 2, "market perform": 2, "equal-weight": 2, "equal weight": 2,
    "buy": 3, "overweight": 3, "outperform": 3, "accumulate": 3,
    "strong buy": 4, "conviction buy": 4,
}


def _infer_rating_action(previous: str, new: str) -> str:
    """Infer upgrade/downgrade/reiterate from two rating labels."""
    prev = _RATING_RANK.get(previous.strip().lower())
    curr = _RATING_RANK.get(new.strip().lower())
    if prev is None or curr is None:
        return "reiterate"
    if curr > prev:
        return "upgrade"
    if curr < prev:
        return "downgrade"
    return "reiterate"
