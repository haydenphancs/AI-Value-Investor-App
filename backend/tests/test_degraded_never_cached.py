"""A degraded build must never be persisted, and a degraded report must never be charged.

Three services shared one bug: an upstream failure was replaced with an empty default, the
result still RENDERED, and it was written to a cache with a 24-hour TTL. So a one-second FMP
blip or a brief Gemini outage pinned a hole for a full day — and, because the report and
snapshot services read the same rows, froze it into paid output for everyone.

The report case is the worst of the three. When Gemini is unavailable, Stage A falls back to
an empty shell and every Stage B narrative uses its sentinel. The result still validates
against `TickerReportResponse`, so it used to be:

  1. charged 20 credits,
  2. returned to the buyer as a success — an empty report, and
  3. written to `ticker_report_cache`, where it was then served FREE to every other user for
     the rest of the close cycle.

The 5-minute in-memory tier still absorbs the retry storm in every case, so the cost of not
persisting is one extra upstream fan-out per 5 minutes — against a full day of wrong data.
"""
from __future__ import annotations

import pytest

from app.services import growth_service as growth_mod
from app.services import profit_power_service as pp_mod
from app.services import ticker_report_service as trs


# ── The report path ───────────────────────────────────────────────────────────

def test_stage_a_fallback_is_tagged_as_degraded():
    """`stage_a_fallback()` on its own is indistinguishable from a real shell — that is
    exactly why this shipped. The tag is what makes it detectable downstream."""
    shell = trs._mark_degraded({"core_thesis": {}}, "stage_a_ResourceExhausted")
    assert trs._degraded_reason(shell) == "stage_a_ResourceExhausted"


def test_a_healthy_shell_is_not_degraded():
    assert trs._degraded_reason({"core_thesis": {"bull_case": ["x"]}}) is None
    assert trs._degraded_reason(None) is None
    assert trs._degraded_reason("not a dict") is None


def test_both_stage_a_failure_paths_tag_the_shell():
    """There are two ways Stage A degrades — an exception, and unparseable JSON. Both must
    tag, or the untagged one silently keeps the old behaviour."""
    import inspect

    src = inspect.getsource(trs.TickerReportService._generate_stage_a)
    # Every `return stage_a_fallback()` must be wrapped.
    assert "return stage_a_fallback()" not in src, (
        "a raw `return stage_a_fallback()` is back — that path will be cached and charged"
    )
    assert src.count("_mark_degraded(stage_a_fallback()") == 2


def test_the_cache_write_is_gated_on_degradation():
    import inspect

    # The build+cache lives in `_generate_uncontended`; `generate_fresh_report` is the
    # dedup wrapper around it.
    src = inspect.getsource(trs.TickerReportService._generate_uncontended)
    assert "_degraded_reason(shell)" in src
    gate = src[src.index("_degraded_reason(shell)"):]
    assert "upsert_cached_report" in gate
    assert "else:" in gate.split("upsert_cached_report")[0], (
        "upsert_cached_report must sit in the else-branch of the degraded check"
    )


def test_the_endpoint_refunds_rather_than_delivering_a_shell():
    """`delivered` must stay False on a degraded report so the `finally` refunds."""
    import inspect
    from app.api.v1.endpoints import ticker_report as ep

    src = inspect.getsource(ep)
    assert 'report.get("_degraded")' in src
    seg = src[src.index('report.get("_degraded")'):]
    seg = seg[: seg.index("delivered = True")]
    assert "return make_error_response" in seg, (
        "the degraded branch must RETURN before `delivered = True`, or the user is charged "
        "for an empty report"
    )


# ── The two FMP services ──────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "mod,fn",
    [
        (growth_mod, "_build_growth"),
        (pp_mod, "_build_profit_power"),
    ],
)
def test_every_empty_default_substitution_records_a_degraded_leg(mod, fn):
    """Each `except`-substitution replaces a failed FMP leg with `[]` or `{}`. Every one must
    append to `degraded`, or that leg's failure is silently persisted for 24h."""
    import inspect

    src = inspect.getsource(getattr(mod.__dict__[
        "GrowthService" if mod is growth_mod else "ProfitPowerService"
    ], fn))
    # The five parallel legs each get a substitution + a degraded.append.
    assert src.count("degraded.append(") >= 4, (
        f"{fn} substitutes empty defaults without recording them as degraded"
    )
    assert "degraded: list[str] = []" in src


@pytest.mark.parametrize("mod", [growth_mod, pp_mod])
def test_supabase_write_through_is_gated(mod):
    """The 24h tier must be skipped when degraded; the 5-min in-memory tier must NOT be —
    dropping that too would re-fan-out to FMP on every single request during an outage."""
    import inspect

    src = inspect.getsource(mod)
    idx = src.index("if degraded:")
    window = src[idx: idx + 1400]
    assert "_upsert_supabase_cache_safe" in window
    assert "NOT persisted" in window, "the skip must be logged — silent degradation is worse"
    # `_cache_set` (the in-memory tier) must still run unconditionally afterwards.
    assert "_cache_set(cache_key" in src
