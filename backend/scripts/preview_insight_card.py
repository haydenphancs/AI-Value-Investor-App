"""Preview the Updates "✨ Insights" card for a scope, AS OF a moment — READ-ONLY.

Replays what the sweeper would hand the card generator at ``--as-of``: FMP news for the
window before it (mapped exactly like ``ticker_news_cache`` rows, then windowed by the
production ``select_recent_corpus``), the earnings-calendar status for that ET day (one
single-day call per context day, like ``earnings_window_service``), and runs the REAL
``NewsInsightService._generate_card`` — the same prompt, schema, conclusion checks and
at-most-one repair production uses. It prints each sample's headline, points, the ↳
conclusion, and whether a repair ran or the card would have been rejected.

It WRITES NOTHING and has no write flag. Supabase is replaced by a tripwire for the whole
process; ``--compare-stored`` performs exactly one SELECT of ``ai_insight_cache`` BEFORE
the tripwire goes in. Gemini is called (Flash-Lite, a fraction of a cent per sample).
Keys come from ``backend/.env`` via the app's settings and are never printed: every error
line is scrubbed.

Why it exists (2026-09-27): the two TestFlight cards that prompted PROMPT_VERSION 6 —
ORCL on Thu 2026-09-10 ("set to report" after the 16:10 ET release) and ETHUSD the same
day (a "$5,000 dividend" as the conclusion) — can be replayed exactly:

    ./venv/bin/python -m scripts.preview_insight_card --scope ORCL \\
        --as-of 2026-09-10T21:00:00Z --earnings-actuals absent --samples 3
    ./venv/bin/python -m scripts.preview_insight_card --scope ORCL \\
        --as-of 2026-09-10T21:00:00Z --samples 3
    ./venv/bin/python -m scripts.preview_insight_card --scope ETHUSD \\
        --as-of 2026-09-10T16:00:00Z --samples 3
    ./venv/bin/python -m scripts.preview_insight_card --scope ORCL --compare-stored

``--earnings-actuals absent`` masks the actuals on rows dated the as-of ET day — the
calendar as it looked before FMP filled them in (status ``due_today``, not ``reported``).
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.log_redaction import redact_secrets  # noqa: E402

logger = logging.getLogger("preview_insight_card")

_NEWS_LOOKBACK_DAYS = 4
_NEWS_PAGE = 250


# ── Read-only guarantees ───────────────────────────────────────────────────────────


class SupabaseRefused(RuntimeError):
    """Raised by the tripwire: this preview never writes (and, after one optional
    SELECT, never reads) Supabase."""


class _SupabaseTripwire:
    def __getattr__(self, name: str) -> Any:
        raise SupabaseRefused(
            f"preview_insight_card is read-only: refused client.{name}"
        )


def install_supabase_tripwire() -> None:
    """Every ``get_supabase()`` in this process now returns a client that refuses all use."""
    import app.database as database

    database._supabase_client = _SupabaseTripwire()


def safe(text: Any, secrets: Sequence[Optional[str]] = ()) -> str:
    out = redact_secrets(str(text))
    for secret in secrets:
        if secret and len(secret) >= 8:
            out = out.replace(secret, "***")
    return out


class _KeyScrubFilter(logging.Filter):
    def __init__(self, secrets: Sequence[Optional[str]]):
        super().__init__()
        self._secrets = list(secrets)

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = safe(record.getMessage(), self._secrets)
        record.args = ()
        return True


# ── Pure helpers (tested) ──────────────────────────────────────────────────────────


def parse_as_of(text: Optional[str]) -> datetime:
    """ISO instant → aware UTC; None → now. A naive value is read as UTC."""
    if not text:
        return datetime.now(timezone.utc)
    dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def map_fmp_rows(raw: Sequence[Any], scope: str) -> List[Dict[str, Any]]:
    """FMP news rows → the ``ticker_news_cache`` shape the corpus selector reads.

    ``published_at`` goes through the production sanitizer: FMP's ``publishedDate`` is
    an ET wall clock with no offset, and reading it as UTC would shift every article
    by four or five hours.
    """
    from app.services.news_cache_service import NewsCacheService, _sanitize_published_at

    rows: List[Dict[str, Any]] = []
    seen: set = set()
    fallback = None if scope.startswith("__") else scope
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            continue
        external_id = (item.get("url") or item.get("title") or f"unknown_{i}")[:500]
        if external_id in seen:
            continue
        seen.add(external_id)
        rows.append({
            "ticker": scope,
            "external_id": external_id,
            "headline": item.get("title") or "",
            "summary": item.get("text") or "",
            "source_name": item.get("publisher") or item.get("site") or "",
            "published_at": _sanitize_published_at(item.get("publishedDate")),
            "article_url": item.get("url"),
            "related_tickers": NewsCacheService._parse_tickers(item, fallback),
        })
    return rows


def rows_as_of(rows: Sequence[Dict[str, Any]], as_of: datetime) -> List[Dict[str, Any]]:
    """Drop anything published after ``as_of``; newest first (the cache's order)."""
    from app.services.news_insight_service import _parse_ts

    kept = []
    for row in rows:
        ts = _parse_ts(row.get("published_at"))
        if ts is not None and ts <= as_of:
            kept.append((ts, row))
    kept.sort(key=lambda p: p[0], reverse=True)
    return [row for _, row in kept]


def mask_actuals(rows: Sequence[Any], day: date) -> List[Any]:
    """The calendar before FMP filled in ``day``'s results."""
    out = []
    for row in rows:
        if isinstance(row, dict) and str(row.get("date") or "")[:10] == day.isoformat():
            row = {**row, "epsActual": None, "revenueActual": None}
        out.append(row)
    return out


# ── The preview ────────────────────────────────────────────────────────────────────


class _CountingGemini:
    """Delegates to the real client and records each call's usage tag."""

    def __init__(self, inner: Any):
        self._inner = inner
        self.tags: List[str] = []
        self.prompts: List[str] = []

    async def generate_json(self, **kwargs: Any) -> Dict[str, Any]:
        self.tags.append(str(kwargs.get("usage_tag")))
        self.prompts.append(str(kwargs.get("prompt") or ""))
        return await self._inner.generate_json(**kwargs)


async def _fetch_news(fmp: Any, scope: str, as_of: datetime) -> List[Dict[str, Any]]:
    from app.services.news_cache_service import MARKET_INDEX_SYMBOLS, is_crypto_scope

    start = (as_of - timedelta(days=_NEWS_LOOKBACK_DAYS)).date().isoformat()
    end = (as_of + timedelta(days=1)).date().isoformat()
    if scope.startswith("__"):
        general, index = await asyncio.gather(
            fmp.get_general_news(limit=_NEWS_PAGE),
            fmp.get_stock_news(MARKET_INDEX_SYMBOLS, limit=_NEWS_PAGE, from_date=start, to_date=end),
        )
        raw = list(general or []) + list(index or [])
    elif is_crypto_scope(scope):
        raw = await fmp.get_crypto_news(scope, limit=_NEWS_PAGE, from_date=start, to_date=end)
    else:
        raw = await fmp.get_stock_news(scope, limit=_NEWS_PAGE, from_date=start, to_date=end)
    return rows_as_of(map_fmp_rows(raw or [], scope), as_of)


async def _earnings_status(fmp: Any, scope: str, as_of: datetime, actuals: str) -> Any:
    from app.services import earnings_window_service as ews
    from app.services.news_cache_service import is_crypto_scope

    if scope.startswith("__") or is_crypto_scope(scope):
        return None
    today = ews.et_date(as_of)
    fetched = await ews._fetch_days(fmp.get_earnings_calendar, ews.context_days(today))
    rows = [r for day_rows in fetched.values() for r in day_rows]
    if actuals == "absent":
        rows = mask_actuals(rows, today)
    return ews.statuses_by_symbol(rows, today).get(scope.upper())


async def run_preview(args: argparse.Namespace, *, fmp: Any, gemini: Any,
                      stored: Optional[Dict[str, Any]], secrets: Sequence[Optional[str]]) -> int:
    from app.services.news_insight_service import (
        MAX_CORPUS_ARTICLES,
        NewsInsightService,
        select_recent_corpus,
    )

    scope = args.scope.upper() if not args.scope.startswith("__") else args.scope
    as_of = parse_as_of(args.as_of)
    rows = await _fetch_news(fmp, scope, as_of)
    corpus, window = select_recent_corpus(
        rows, as_of, scope=None if scope.startswith("__") else scope, company_name=args.name,
    )
    corpus = corpus[:MAX_CORPUS_ARTICLES]
    status = await _earnings_status(fmp, scope, as_of, args.earnings_actuals)

    print(f"\n=== {scope} as of {as_of.isoformat()} — {len(rows)} fetched, "
          f"{len(corpus)} in the {window}h corpus ===")
    print(f"earnings status: {status.status + ' ' + status.date.isoformat() if status else 'none'}")
    if stored:
        print("\n--- stored now (ai_insight_cache) ---")
        print(f"  {stored.get('headline')}")
        for b in (stored.get("bullets") or [])[:-1]:
            print(f"  • {b}")
        if stored.get("bullets"):
            print(f"  ↳ {stored['bullets'][-1]}")
        print(f"  (prompt_version {stored.get('prompt_version')}, generated {stored.get('generated_at')})")
    if not corpus:
        print("\nno corpus — the sweeper would skip this scope (no_corpus)")
        return 0

    svc = object.__new__(NewsInsightService)
    svc.supabase = None
    svc._cache = {}
    svc._inflight = {}
    counting = _CountingGemini(gemini)
    svc.gemini = counting

    for i in range(args.samples):
        before = len(counting.tags)
        card, reason = await svc._generate_card(
            scope, corpus, f"preview-{scope}-{as_of.isoformat()}-{i}", None, None,
            now=as_of, earnings=status,
        )
        tags = counting.tags[before:]
        if i == 0 and args.show_prompt and counting.prompts:
            print("\n--- prompt (sample 1) ---\n" + safe(counting.prompts[before], secrets))
        print(f"\n--- sample {i + 1} ({len(tags)} call(s): {', '.join(tags)}) ---")
        if card is None:
            print(f"  REJECTED — nothing would be written: {reason}")
            continue
        print(f"  {card['headline']}  [{card['sentiment']}]")
        for b in card["bullets"][:-1]:
            print(f"  • {b}")
        print(f"  ↳ {card['bullets'][-1]}")
    return 0


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    parser.add_argument("--scope", required=True, help="ORCL, ETHUSD, __MARKET__ …")
    parser.add_argument("--as-of", default=None, help="ISO instant (UTC if naive); default now")
    parser.add_argument("--name", default=None, help="company name for the subject filter")
    parser.add_argument("--earnings-actuals", choices=("calendar", "absent"), default="calendar")
    parser.add_argument("--samples", type=int, default=1)
    parser.add_argument("--show-prompt", action="store_true")
    parser.add_argument("--compare-stored", action="store_true",
                        help="one SELECT of ai_insight_cache before the tripwire goes in")
    return parser.parse_args(argv)


def _read_stored(scope: str) -> Optional[Dict[str, Any]]:
    from app.database import get_supabase

    res = (
        get_supabase().table("ai_insight_cache")
        .select("headline,bullets,prompt_version,generated_at")
        .eq("scope", scope).limit(1).execute()
    )
    return (res.data or [None])[0]


async def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    from app.config import settings

    secrets = [
        getattr(settings, "FMP_API_KEY", None),
        getattr(settings, "GEMINI_API_KEY", None),
        getattr(settings, "SUPABASE_SERVICE_ROLE_KEY", None),
    ]
    handler = logging.StreamHandler()
    handler.addFilter(_KeyScrubFilter(secrets))
    handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    logging.basicConfig(level=logging.WARNING, handlers=[handler], force=True)

    stored = None
    if args.compare_stored:
        try:
            stored = _read_stored(args.scope if args.scope.startswith("__") else args.scope.upper())
        except Exception as e:
            print(f"(could not read the stored card: {safe(f'{type(e).__name__}: {e}', secrets)})")
    install_supabase_tripwire()

    from app.integrations.fmp import close_fmp_client, get_fmp_client
    from app.integrations.gemini import get_gemini_client

    fmp = get_fmp_client()
    try:
        return await run_preview(args, fmp=fmp, gemini=get_gemini_client(),
                                 stored=stored, secrets=secrets)
    except Exception as e:
        print(f"preview failed: {safe(f'{type(e).__name__}: {e}', secrets)}")
        return 1
    finally:
        await close_fmp_client()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
