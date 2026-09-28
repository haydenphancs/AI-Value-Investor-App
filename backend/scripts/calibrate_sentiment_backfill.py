"""Calibrate the backfill's sentiment labeller against live labels — READ-ONLY.

Before switching on SENTIMENT_BACKFILL_ENABLED (or switching NEWS_LLM_PROVIDER to another
model), check that the backfill's sentiment-only prompt labels articles the way the live
enrichment already did. Otherwise backfilled days and live days would be two different
measurements on one chart, with a jump at the boundary.

It reads already-labelled rows from `ticker_news_cache` (ONE SELECT, before a tripwire makes
every further Supabase use raise), re-labels them with the backfill's exact prompt
(`news_sentiment_backfill_service.build_label_prompt`) through the CURRENT news model
(`app/services/news_llm.py`, i.e. whatever NEWS_LLM_* says — so the same run also calibrates a
candidate provider), and reports agreement and a confusion matrix. It writes nothing and has
no write flag. Keys are never printed.

    ./venv/bin/python -m scripts.calibrate_sentiment_backfill --limit 300
    ./venv/bin/python -m scripts.calibrate_sentiment_backfill --scope ORCL --limit 100

What "good enough" means. The live labels are not ground truth — the live labeller is not
even consistent with itself: re-running the exact live enrichment prompt on the same 300
articles agreed with its own earlier labels 81.7% of the time (2026-09-27, default temperature).
So the run also re-labels the SAME articles with the live prompt as a BASELINE, and passes when
the backfill prompt agrees with the live labels at least as well as the live prompt agrees with
itself (minus a 2-point tolerance), or reaches 85% outright. Measured 2026-09-27: baseline
81.7%, backfill 84.7% → PASS. Cost: ≈ 2–3 cents for 300 articles on flash-lite.

The baseline is ALWAYS the Gemini live labeller that made the stored labels, whatever
NEWS_LLM_* says — measured on the candidate it would lower its own bar. Baseline batches that
failed (429, cut-off JSON) are left out of its denominator instead of counting as misses, and
the run aborts when fewer than 95% of the articles got a baseline label. With any news
model other than the default Gemini labeller the LIVE prompt switches too, so the
candidate must pass on BOTH prompts.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from collections import Counter, defaultdict
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.log_redaction import redact_secrets  # noqa: E402

logger = logging.getLogger("calibrate_sentiment_backfill")

TARGET_AGREEMENT = 0.85          # passes outright at this level
BASELINE_TOLERANCE = 0.02        # …or within this of the live labeller's self-agreement
MIN_BASELINE_COVERAGE = 0.95     # below this the baseline is too thin to judge against
LABELS = ("bullish", "bearish", "neutral")


class SupabaseRefused(RuntimeError):
    """This script reads once, then never touches Supabase again."""


class _Tripwire:
    def __getattr__(self, name: str) -> Any:
        raise SupabaseRefused(f"calibration is read-only (refused Supabase .{name})")


def install_supabase_tripwire() -> None:
    import app.database as database

    database._supabase_client = _Tripwire()


def scrub(text: str, secrets: Sequence[Optional[str]] = ()) -> str:
    out = redact_secrets(str(text))
    for secret in secrets:
        if secret and len(secret) >= 8:
            out = out.replace(secret, "***")
    return out


def agreement(pairs: Sequence[Tuple[str, Optional[str]]], *, drop_unlabelled: bool = False) -> Dict[str, Any]:
    """Agreement between live labels and new labels. Pure.

    An article the new model left unlabelled counts as a DISAGREEMENT (it would be missing
    from the chart), never as a match. `drop_unlabelled` is for the BASELINE only: there a
    failed batch says nothing about consistency, and counting it as misses lowers the bar.
    `coverage` is the labelled share either way.
    """
    labelled = sum(1 for _, new in pairs if new is not None)
    coverage = (labelled / len(pairs)) if pairs else 0.0
    if drop_unlabelled:
        pairs = [(live, new) for live, new in pairs if new is not None]
    total = len(pairs)
    matches = sum(1 for live, new in pairs if new is not None and live == new)
    confusion: Dict[str, Counter] = defaultdict(Counter)
    for live, new in pairs:
        confusion[live][new or "none"] += 1
    return {
        "total": total,
        "matches": matches,
        "rate": (matches / total) if total else 0.0,
        "unlabelled": sum(1 for _, new in pairs if new is None),
        "coverage": coverage,
        "confusion": {k: dict(v) for k, v in confusion.items()},
        "live_mix": dict(Counter(live for live, _ in pairs)),
        "new_mix": dict(Counter(new or "none" for _, new in pairs)),
    }


def read_labelled_rows(supabase: Any, *, limit: int, scope: Optional[str]) -> List[Dict[str, Any]]:
    from app.services.news_sentiment_trend_service import normalize_sentiment

    q = (
        supabase.table("ticker_news_cache")
        .select("ticker,headline,summary,sentiment,published_at")
        .eq("ai_processed", True)
        .neq("ticker", "__MARKET__")
    )
    if scope:
        q = q.eq("ticker", scope.upper())
    rows = q.order("published_at", desc=True).limit(max(1, min(limit, 1000))).execute().data or []
    out = []
    for r in rows:
        label = normalize_sentiment(r.get("sentiment"))
        if label and r.get("headline"):
            out.append({"scope": r["ticker"], "title": r["headline"], "text": r.get("summary") or "",
                        "live": label})
    return out


@contextmanager
def gemini_live_labeller():
    """Route the news model to the Gemini default for the duration — the labeller that made
    the stored labels — whatever NEWS_LLM_* says. Script-local; restored on exit."""
    from app.config import settings
    from app.services.news_llm import DEFAULT_GEMINI_MODEL, PROVIDER_GEMINI

    saved = (settings.NEWS_LLM_PROVIDER, settings.NEWS_LLM_MODEL)
    settings.NEWS_LLM_PROVIDER, settings.NEWS_LLM_MODEL = PROVIDER_GEMINI, DEFAULT_GEMINI_MODEL
    try:
        yield
    finally:
        settings.NEWS_LLM_PROVIDER, settings.NEWS_LLM_MODEL = saved


async def live_baseline(rows: List[Dict[str, Any]], *, batch: int = 25) -> List[Tuple[str, Optional[str]]]:
    """Re-label the same rows with the LIVE enrichment prompt through the CURRENT news model.
    Under `gemini_live_labeller()` that is how consistent the live labeller is with itself —
    the backfill cannot be held to a higher bar than that; without it, the candidate's own
    live-prompt agreement."""
    from app.integrations.gemini import get_gemini_client
    from app.services.news_cache_service import NewsCacheService

    svc = object.__new__(NewsCacheService)
    svc.gemini = get_gemini_client()
    by_scope: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for r in rows:
        by_scope[r["scope"]].append(r)
    pairs: List[Tuple[str, Optional[str]]] = []
    for scope, items in by_scope.items():
        for i in range(0, len(items), batch):
            chunk = items[i:i + batch]
            out = await svc._batch_enrich_articles(
                [{"title": r["title"], "text": r["text"]} for r in chunk], ticker=scope,
            )
            pairs.extend((r["live"], _model_label(out.get(j) if out else None))
                         for j, r in enumerate(chunk))
    return pairs


def overall_verdict(rates: Sequence[float], baseline_rate: Optional[float]) -> str:
    """Every measured candidate rate must pass on its own. Pure."""
    return "PASS" if rates and all(verdict(r, baseline_rate) == "PASS" for r in rates) else "BELOW TARGET"


def _model_label(enrichment: Optional[Dict[str, Any]]) -> Optional[str]:
    """The label the model GAVE, or None: a missing/off-list sentiment shows as "neutral" on
    the badge but is not a neutral answer, and must not count as agreement."""
    if not enrichment or not enrichment.get("sentiment_valid", True):
        return None
    return enrichment.get("sentiment")


def verdict(backfill_rate: float, baseline_rate: Optional[float]) -> str:
    """PASS when the backfill is at least as consistent with the live labels as the live
    labeller is with itself (within the tolerance), or reaches the absolute target. Pure."""
    if backfill_rate >= TARGET_AGREEMENT:
        return "PASS"
    if baseline_rate is not None and backfill_rate >= baseline_rate - BASELINE_TOLERANCE:
        return "PASS"
    return "BELOW TARGET"


async def relabel(rows: List[Dict[str, Any]], *, batch: int = 50) -> List[Tuple[str, Optional[str]]]:
    from app.services.agents.persona_config import neutral_system_instruction
    from app.services.news_cache_service import ENRICHMENT_SYSTEM_BASE
    from app.services.news_llm import generate_news_json, is_content_refusal
    from app.services.news_sentiment_backfill_service import (
        LABEL_TEMPERATURE,
        _LABEL_SCHEMA,
        build_label_prompt,
        parse_labels,
    )

    refused = 0

    async def _label(scope: str, chunk: List[Dict[str, Any]], split: bool) -> List[Tuple[str, Optional[str]]]:
        """As the backfill labels: an unusable answer — or a moderation refusal, which the
        backfill treats the same way — splits the chunk once; what is still unusable counts
        as unlabelled (a miss). Any OTHER error still aborts the run."""
        nonlocal refused
        try:
            result = await generate_news_json(
                prompt=build_label_prompt(scope, chunk),
                system_instruction=neutral_system_instruction(ENRICHMENT_SYSTEM_BASE),
                response_schema=_LABEL_SCHEMA,
                usage_tag="sentiment_backfill_calibration",
                temperature=LABEL_TEMPERATURE,
                cache=False,
            )
            labels = parse_labels((result or {}).get("text") or "", len(chunk))
        except Exception as e:  # noqa: BLE001 — narrow: only a moderation refusal is an answer
            if not is_content_refusal(e):
                raise
            refused += 1
            labels = None
        if labels is not None:
            return [(r["live"], new) for r, (new, _conf) in zip(chunk, labels)]
        if split and len(chunk) > 1:
            mid = len(chunk) // 2
            return await _label(scope, chunk[:mid], False) + await _label(scope, chunk[mid:], False)
        return [(r["live"], None) for r in chunk]

    by_scope: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for r in rows:
        by_scope[r["scope"]].append(r)
    pairs: List[Tuple[str, Optional[str]]] = []
    for scope, items in by_scope.items():
        for i in range(0, len(items), batch):
            pairs.extend(await _label(scope, items[i:i + batch], True))
    if refused:
        print(f"note: {refused} batch(es) were refused by the provider's moderation (counted as misses)")
    return pairs


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--limit", type=int, default=300, help="articles to re-label (max 1000)")
    p.add_argument("--scope", help="only this ticker")
    p.add_argument("--no-baseline", action="store_true",
                   help="skip re-running the live prompt (halves the cost; absolute 85%% bar only)")
    return p.parse_args(argv)


async def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    from app.config import settings
    from app.database import get_supabase
    from app.services.news_llm import DEFAULT_GEMINI_MODEL, PROVIDER_GEMINI, news_llm_config

    secrets = [settings.GEMINI_API_KEY, getattr(settings, "NEWS_LLM_API_KEY", None),
               getattr(settings, "SUPABASE_SERVICE_ROLE_KEY", None)]
    rows = read_labelled_rows(get_supabase(), limit=args.limit, scope=args.scope)
    install_supabase_tripwire()
    cfg = news_llm_config()
    print(f"news model: provider={cfg.provider} model={cfg.model}; {len(rows)} live-labelled articles")
    if not rows:
        print("nothing to calibrate")
        return 1
    candidate_live = None
    try:
        pairs = await relabel(rows)
        baseline = None
        if not args.no_baseline:
            with gemini_live_labeller():
                baseline = agreement(await live_baseline(rows), drop_unlabelled=True)
        if (cfg.provider, cfg.model) != (PROVIDER_GEMINI, DEFAULT_GEMINI_MODEL):
            # Live enrichment switches with the news model too (a new Gemini model as much as
            # another provider): measure the candidate on the live prompt as well.
            candidate_live = agreement(await live_baseline(rows))
    except Exception as e:  # noqa: BLE001
        print("relabel failed:", scrub(f"{type(e).__name__}: {e}", secrets))
        return 2
    report = agreement(pairs)
    if baseline is not None:
        print(f"baseline (Gemini live prompt re-run vs live labels): {baseline['matches']}/{baseline['total']}"
              f" = {baseline['rate']:.1%}  (coverage {baseline['coverage']:.0%})")
        if baseline["coverage"] < MIN_BASELINE_COVERAGE:
            print(f"ABORT: only {baseline['coverage']:.0%} of the articles got a baseline label "
                  f"(need {MIN_BASELINE_COVERAGE:.0%}) — re-run when the model is not throttled")
            return 4
    print(f"backfill prompt vs live labels: {report['matches']}/{report['total']} = {report['rate']:.1%}"
          f"  (unlabelled: {report['unlabelled']})")
    if candidate_live is not None:
        print(f"candidate LIVE prompt vs live labels: {candidate_live['matches']}/{candidate_live['total']}"
              f" = {candidate_live['rate']:.1%}  (unlabelled: {candidate_live['unlabelled']})")
    print(f"live mix: {report['live_mix']}")
    print(f"new  mix: {report['new_mix']}")
    print("confusion (live → new):")
    for live in LABELS:
        print(f"  {live:>8}: {report['confusion'].get(live, {})}")
    rates = [report["rate"]] + ([candidate_live["rate"]] if candidate_live is not None else [])
    result = overall_verdict(rates, baseline["rate"] if baseline else None)
    print(f"{result} (≥ {TARGET_AGREEMENT:.0%}, or within {BASELINE_TOLERANCE:.0%} of the baseline)")
    return 0 if result == "PASS" else 3


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    sys.exit(asyncio.run(main()))
