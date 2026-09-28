"""`scripts/calibrate_sentiment_backfill.py` — hermetic checks of its pure parts and its
read-only guarantees. Nothing here reaches Supabase or a model."""

import pytest

from scripts import calibrate_sentiment_backfill as cal


def test_agreement_counts_an_unlabelled_article_as_a_miss():
    report = cal.agreement([("bullish", "bullish"), ("bearish", "neutral"), ("neutral", None)])
    assert report["total"] == 3 and report["matches"] == 1
    assert report["rate"] == pytest.approx(1 / 3)
    assert report["unlabelled"] == 1
    assert report["confusion"]["neutral"] == {"none": 1}


def test_agreement_on_nothing_is_zero_not_a_crash():
    assert cal.agreement([])["rate"] == 0.0


def test_the_tripwire_refuses_every_supabase_use(monkeypatch):
    import app.database as database

    monkeypatch.setattr(database, "_supabase_client", None)
    cal.install_supabase_tripwire()
    for name in ("table", "rpc", "storage"):
        with pytest.raises(cal.SupabaseRefused):
            getattr(database._supabase_client, name)


def test_there_is_no_write_flag():
    args = cal.parse_args(["--limit", "10"])
    assert not any("write" in k or "apply" in k for k in vars(args))


def test_scrub_hides_keys():
    assert "SECRETKEY123" not in cal.scrub("boom apikey=SECRETKEY123", ["SECRETKEY123"])


@pytest.mark.parametrize("backfill,baseline,expected", [
    (0.86, None, "PASS"),          # the absolute bar
    (0.847, 0.817, "PASS"),        # measured 2026-09-27: better than the live self-agreement
    (0.80, 0.817, "PASS"),         # within the 2-point tolerance
    (0.79, 0.817, "BELOW TARGET"),
    (0.80, None, "BELOW TARGET"),  # no baseline: only the absolute bar applies
])
def test_verdict_is_relative_to_the_live_labellers_own_consistency(backfill, baseline, expected):
    assert cal.verdict(backfill, baseline) == expected


def test_the_baseline_leaves_failed_batches_out_of_its_denominator():
    pairs = [("bullish", "bullish"), ("bearish", "bearish"), ("neutral", None), ("neutral", None)]
    candidate = cal.agreement(pairs)
    baseline = cal.agreement(pairs, drop_unlabelled=True)
    assert candidate["rate"] == 0.5, "a candidate's missing label is still a miss"
    assert baseline["rate"] == 1.0 and baseline["total"] == 2
    assert baseline["coverage"] == 0.5 < cal.MIN_BASELINE_COVERAGE, "too thin → the run aborts"


def test_the_baseline_runs_on_gemini_whatever_the_settings_say(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "NEWS_LLM_PROVIDER", "openai_compat")
    monkeypatch.setattr(settings, "NEWS_LLM_MODEL", "deepseek-flash")
    with cal.gemini_live_labeller():
        assert (settings.NEWS_LLM_PROVIDER, settings.NEWS_LLM_MODEL) == ("gemini", "gemini-2.5-flash-lite")
    assert (settings.NEWS_LLM_PROVIDER, settings.NEWS_LLM_MODEL) == ("openai_compat", "deepseek-flash")


@pytest.mark.parametrize("rates,baseline,expected", [
    ([0.847], 0.817, "PASS"),
    ([0.847, 0.62], 0.817, "BELOW TARGET"),   # a candidate must also pass on the LIVE prompt
    ([0.86, 0.86], None, "PASS"),
    ([], 0.817, "BELOW TARGET"),
])
def test_every_candidate_rate_must_pass(rates, baseline, expected):
    assert cal.overall_verdict(rates, baseline) == expected


@pytest.mark.asyncio
async def test_a_moderation_refusal_splits_like_the_backfill_and_other_errors_abort(monkeypatch):
    import json as _json

    import app.services.news_llm as news_llm
    from app.integrations.openai_compat import OpenAICompatContentRejected, OpenAICompatError

    rows = [{"scope": "TSM", "title": f"T{i}" + (" FORBIDDEN" if i == 0 else ""), "text": "",
             "live": "bullish"} for i in range(4)]

    async def _gen(**kw):
        if "FORBIDDEN" in kw["prompt"]:
            raise OpenAICompatContentRejected("400 Content Exists Risk")
        n = kw["prompt"].count("<<<END_ARTICLE ") - 1        # minus the instructions' example
        return {"text": _json.dumps([{"index": i, "sentiment": "bullish", "confidence": 60}
                                     for i in range(n)])}

    monkeypatch.setattr(news_llm, "generate_news_json", _gen)
    pairs = await cal.relabel(rows, batch=4)
    assert [p[1] for p in pairs] == [None, None, "bullish", "bullish"], "one split, the clean half labelled"

    async def _broken(**kw):
        raise OpenAICompatError("401 bad key")

    monkeypatch.setattr(news_llm, "generate_news_json", _broken)
    with pytest.raises(OpenAICompatError):
        await cal.relabel(rows, batch=4)


@pytest.mark.asyncio
@pytest.mark.parametrize("provider,model,expect_live_runs", [
    ("gemini", "gemini-2.5-flash-lite", 1),        # the default labeller: baseline only
    ("gemini", "gemini-3-flash-lite", 2),          # a new Gemini model switches live too
])
async def test_any_non_default_model_is_checked_on_the_live_prompt(monkeypatch, provider, model, expect_live_runs):
    from app.config import settings

    monkeypatch.setattr(settings, "NEWS_LLM_PROVIDER", provider)
    monkeypatch.setattr(settings, "NEWS_LLM_MODEL", model)
    rows = [{"scope": "ORCL", "title": "t", "text": "", "live": "bullish"}]
    monkeypatch.setattr(cal, "read_labelled_rows", lambda *_a, **_k: rows)
    monkeypatch.setattr(cal, "install_supabase_tripwire", lambda: None)
    runs = []

    async def _relabel(r, **_k):
        return [("bullish", "bullish")]

    async def _live(r, **_k):
        runs.append(1)
        return [("bullish", "bullish")]

    monkeypatch.setattr(cal, "relabel", _relabel)
    monkeypatch.setattr(cal, "live_baseline", _live)
    import app.database as database
    monkeypatch.setattr(database, "get_supabase", lambda: object())
    assert await cal.main(["--limit", "1"]) == 0
    assert len(runs) == expect_live_runs
