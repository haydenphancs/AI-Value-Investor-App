"""Re-label a fixed sample of live News Tone headlines with the CURRENT labeller — READ-ONLY.

The News Tone chart counts Cay AI's bullish / bearish / neutral labels. On 2026-10-10 a blind
audit (three independent raters per headline, 93% agreement among them) found the labeller
over-directional: 70.5% agreement with the raters, 69 bullish where they saw 50. The labeller's
question was rewritten (scope-named subject, neutral as a real answer, a `relevance` answer, the
chart gate). This script is the measuring half of the check:

1. it reads up to N labelled rows from `ticker_news_cache` (one SELECT; ticker scopes plus a
   Market slice, picked by a stable hash so a re-run picks the same rows while they live);
2. re-labels them with the live request itself (`news_sentiment_backfill_service.label_request`
   → `build_enrichment_prompt`, temperature 0, no response cache) through the current news model;
3. writes, for every row, the stored (OLD) label, the NEW label, its `relevance` / Market scope
   and whether the chart gate would count it — ids and labels only, NO article text (migration
   104) — to ``--out``.

The other half is a blind rater pass over the same ids (a workflow), which the owner reads beside
this file. It writes nothing to the database and has no write flag. Keys are never printed.
Cost: about one flash-lite call per 25 rows (a few cents for 200).

    ./venv/bin/python -m scripts.eval_news_tone_labels --limit 176 --market 24 --out /tmp/eval.json
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

logger = logging.getLogger("eval_news_tone_labels")

_COLUMNS = "id,ticker,external_id,headline,summary,sentiment,sentiment_confidence,published_at"
_PAGE = 1000


def _stable(row: Dict[str, Any]) -> str:
    return hashlib.md5(str(row.get("id")).encode("utf-8"), usedforsecurity=False).hexdigest()


def pick_sample(rows: List[Dict[str, Any]], *, limit: int, market: int) -> List[Dict[str, Any]]:
    """A stable, hash-ordered pick: `limit` ticker rows and `market` Market rows. Pure."""
    labelled = [r for r in rows if isinstance(r, dict) and r.get("sentiment") in ("bullish", "bearish", "neutral")
                and isinstance(r.get("headline"), str) and r["headline"].strip()]
    tickers = sorted((r for r in labelled if r.get("ticker") != "__MARKET__"), key=_stable)[:limit]
    markets = sorted((r for r in labelled if r.get("ticker") == "__MARKET__"), key=_stable)[:market]
    return tickers + markets


def read_rows_by_id(supabase: Any, ids: List[str]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for s in range(0, len(ids), 100):
        page = supabase.table("ticker_news_cache").select(_COLUMNS).in_("id", ids[s: s + 100]).execute()
        out.extend(getattr(page, "data", None) or [])
    return out


def score_against(results: List[Dict[str, Any]], gold: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """Agreement of the OLD and NEW labels with a rater gold set (``{id: {"maj": label}}``;
    "split" rows are left out). Pure."""
    rows = [r for r in results if (gold.get(r["id"]) or {}).get("maj") in ("bullish", "bearish", "neutral")]
    new_rows = [r for r in rows if r["new"]]
    chart = [r for r in new_rows if r["new_eligible"]]
    pct = lambda n, d: round(100 * n / d, 1) if d else None  # noqa: E731
    return {
        "scored": len(rows),
        "old_agree_pct": pct(sum(1 for r in rows if r["old"] == gold[r["id"]]["maj"]), len(rows)),
        "new_agree_pct": pct(sum(1 for r in new_rows if r["new"] == gold[r["id"]]["maj"]), len(new_rows)),
        "raters": dict(Counter(gold[r["id"]]["maj"] for r in rows)),
        "chart_new": dict(Counter(r["new"] for r in chart)),
        "chart_raters_same_rows": dict(Counter(gold[r["id"]]["maj"] for r in chart)),
        "chart_agree_pct": pct(sum(1 for r in chart if r["new"] == gold[r["id"]]["maj"]), len(chart)),
    }


def read_rows(supabase: Any) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    start = 0
    while True:
        page = (
            supabase.table("ticker_news_cache").select(_COLUMNS).eq("ai_processed", True)
            .range(start, start + _PAGE - 1).execute()
        )
        data = getattr(page, "data", None) or []
        out.extend(data)
        if len(data) < _PAGE:
            return out
        start += _PAGE


async def relabel(sample: List[Dict[str, Any]], *, batch: int = 25) -> List[Dict[str, Any]]:
    from app.services.market_news_relevance import is_market_story, stamp_model_scope
    from app.database import get_supabase
    from app.services.news_cache_service import NewsCacheService, scope_profile
    from app.services.news_llm import generate_news_json, news_model_name
    from app.services.news_sentiment_backfill_service import label_request
    from app.services.news_sentiment_trend_service import tone_skip_reason

    by_scope: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for r in sample:
        by_scope[r["ticker"]].append(r)
    results: List[Dict[str, Any]] = []
    for scope, rows in by_scope.items():
        for s in range(0, len(rows), batch):
            chunk = rows[s: s + batch]
            articles = [{"title": r.get("headline") or "", "text": r.get("summary") or ""} for r in chunk]
            try:
                name, is_etf = await scope_profile(get_supabase(), scope)
                answer = await generate_news_json(
                    **label_request(scope, articles, company_name=name, is_etf=is_etf),
                    usage_tag="news_tone_eval", cache=False,
                )
                mapped = NewsCacheService._map_enrichments(json.loads((answer or {}).get("text") or ""), len(chunk))
            except Exception as e:  # noqa: BLE001 — one failed batch is reported, not fatal
                logger.warning("eval: %s batch of %d failed (%s)", scope, len(chunk), type(e).__name__)
                mapped = {}
            for pos, r in enumerate(chunk):
                item = mapped.get(pos) or {}
                usable = bool(item.get("bullets")) and item.get("sentiment_valid", False)
                relevance = item.get("relevance")
                if scope == "__MARKET__" and usable:
                    kept = is_market_story({"title": r.get("headline"),
                                            "ai_model": stamp_model_scope(news_model_name(), item.get("scope"))})
                    relevance = None if kept else "passing"
                gate = tone_skip_reason({"headline": r.get("headline"), "relevance": relevance})
                results.append({
                    "id": r["id"], "scope": scope,
                    "old": r.get("sentiment"),
                    "old_eligible": tone_skip_reason({"headline": r.get("headline")}) is None,
                    "new": item.get("sentiment") if usable else None,
                    "new_relevance": relevance,
                    "new_eligible": usable and gate is None,
                    "new_skip": gate,
                })
    return results


def summarize(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Distribution of old vs new labels, all rows and chart-eligible rows. Pure."""
    return {
        "rows": len(results),
        "unlabelled_new": sum(1 for r in results if r["new"] is None),
        "old_all": dict(Counter(r["old"] for r in results)),
        "new_all": dict(Counter(r["new"] for r in results if r["new"])),
        "new_chart": dict(Counter(r["new"] for r in results if r["new_eligible"])),
        "new_skips": dict(Counter(r["new_skip"] for r in results if r["new_skip"])),
        "old_vs_new_agree_pct": round(100 * sum(1 for r in results if r["new"] and r["new"] == r["old"])
                                      / max(1, sum(1 for r in results if r["new"])), 1),
    }


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--limit", type=int, default=176, help="ticker-scope rows (default 176)")
    p.add_argument("--market", type=int, default=24, help="Market rows (default 24)")
    p.add_argument("--out", type=Path, required=True, help="where to write ids + labels (no text)")
    p.add_argument("--ids", type=Path, help="JSON list of ticker_news_cache ids to re-label instead of a sample")
    p.add_argument("--gold", type=Path, help='JSON {id: {"maj": label}} rater labels to score against')
    return p.parse_args(argv)


async def main(argv: Optional[Sequence[str]] = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = parse_args(argv)
    from app.database import get_supabase

    if args.ids:
        sample = read_rows_by_id(get_supabase(), json.loads(args.ids.read_text()))
    else:
        sample = pick_sample(read_rows(get_supabase()), limit=args.limit, market=args.market)
    if not sample:
        print("no labelled rows to evaluate")
        return 1
    results = await relabel(sample)
    args.out.write_text(json.dumps(results, indent=1))
    print(json.dumps(summarize(results), indent=1))
    if args.gold:
        print(json.dumps(score_against(results, json.loads(args.gold.read_text())), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
