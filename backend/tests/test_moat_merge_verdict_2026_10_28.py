"""The moat radar's merge, peer provenance and verdict (owner decisions 2026-10-28).

JOBY's report read Switching Costs 0.0 (an AI pillar, or the collector's own placeholder)
against a 5.0 "Peer Avg" that no industry row backed, and the moat vital, persona score and
Insight all counted AI guesses as measurements. Pinned here:

1. A pillar neither tier scored is DROPPED, never a fabricated 0.0/10 (the merge used to
   synthesize ``{"score": 0.0, "peer_score": 5.0}``); Stage A names match case/space-blind.
2. Every pillar says where its peer point came from (`peer_source`); a real industry 0.0
   stays 0.0, the AI's template 0.0 becomes the 5.0 baseline.
3. The moat vital uses MEASURED pillars only; with none, its score is an explicit None that
   persona scoring renormalizes out (a dict without `score` read 3.0).
4. A scoring exception and a transient statement fetch failure keep the report out of the
   shared caches; a permanent refusal does not.
5. The block carries the scorer vintage the paid reuse gate compares.

Hermetic: the scorer and the industry lookup are stubbed at the bindings the collector uses.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Dict, Optional

import httpx
import pytest

import app.services.agents.ticker_report_data_collector as C
import app.services.industry_moat_benchmark_service as imb_mod
import app.services.moat_scoring_service as moat_mod
from app.integrations.fmp import (
    FMPNotEntitledException,
    FMPRateLimitException,
    FMPUnavailableException,
)
from app.services.agents.narrative_prompts import stage_a_fallback
from app.services.agents.persona_scoring import _vital_score
from app.services.moat_scoring_service import (
    PILLAR_BRAND,
    PILLAR_COST,
    PILLAR_INTANGIBLE,
    PILLAR_NETWORK,
    PILLAR_SWITCHING,
    SCORER_VERSION,
    MoatScores,
    PillarResult,
)

# The schema-parity builder and its autouse fixture (stubs every live benchmark / moat
# lookup `assemble_report` reaches). Tests below override the moat stubs.
from test_ticker_report_schema_parity import (  # noqa: F401
    _make_collected_data,
    _no_live_benchmark_lookup,
)


def _scores(**by_pillar: Optional[float]) -> MoatScores:
    out = MoatScores()
    for name in moat_mod.PILLAR_ORDER:
        score = by_pillar.get(name)
        out[name] = PillarResult(
            name=name, score=score, confidence="medium" if score is not None else "low",
        )
    return out


def _assemble(monkeypatch, scores: MoatScores, ai_dims, peer_avgs: Dict[str, float]):
    monkeypatch.setattr(moat_mod, "score_moat_dimensions", lambda **_k: scores)

    class _Peers:
        def get_pillar_benchmarks(self, industry):
            return dict(peer_avgs)

    monkeypatch.setattr(imb_mod, "_lookup_singleton", _Peers())
    shell = stage_a_fallback()
    shell["moat_competition"]["dimensions"] = ai_dims
    out = _make_collected_data()
    return C.TickerReportDataCollector().assemble_report(out, shell)


def _dims(report) -> Dict[str, Dict[str, Any]]:
    return {d["name"]: d for d in report["moat_competition"]["dimensions"]}


def test_a_pillar_no_tier_scored_is_dropped_never_a_fabricated_zero(monkeypatch):
    report = _assemble(
        monkeypatch, _scores(**{PILLAR_COST: 0.0}),
        ai_dims=[
            # Switching Costs absent; Network Effects misspaced/cased; Brand with no score.
            {"name": "  network effects ", "score": 3.0, "peer_score": 6.0},
            {"name": PILLAR_BRAND, "score": None, "peer_score": 4.0},
            {"name": PILLAR_INTANGIBLE, "score": 2.0, "peer_score": 0.0},
        ],
        peer_avgs={},
    )
    dims = _dims(report)
    assert PILLAR_SWITCHING not in dims          # was {"score": 0.0, "peer_score": 5.0}
    assert PILLAR_BRAND not in dims              # a null AI score is dropped too
    assert dims[PILLAR_NETWORK]["source"] == "ai_legacy"
    assert dims[PILLAR_NETWORK]["score"] == 3.0
    assert dims[PILLAR_NETWORK]["peer_source"] == "ai_estimate"
    assert dims[PILLAR_NETWORK]["peer_score"] == 6.0
    # The AI's template 0.0 peer is no peer: the 5.0 baseline, tagged as such.
    assert dims[PILLAR_INTANGIBLE]["peer_score"] == 5.0
    assert dims[PILLAR_INTANGIBLE]["peer_source"] == "baseline"
    assert dims[PILLAR_COST]["source"] == "deterministic"


def test_peer_source_tags_industry_rows_and_keeps_a_real_industry_zero(monkeypatch):
    report = _assemble(
        monkeypatch, _scores(**{PILLAR_COST: 4.0, PILLAR_BRAND: 6.0}),
        ai_dims=[{"name": PILLAR_SWITCHING, "score": 7.0, "peer_score": 7.0}],
        peer_avgs={PILLAR_COST: 0.0, PILLAR_SWITCHING: 6.5},
    )
    dims = _dims(report)
    assert dims[PILLAR_COST]["peer_score"] == 0.0            # a real industry 0.0 stays
    assert dims[PILLAR_COST]["peer_source"] == "industry"
    assert dims[PILLAR_BRAND]["peer_score"] == 5.0           # scorer anchor, no row
    assert dims[PILLAR_BRAND]["peer_source"] == "baseline"
    assert dims[PILLAR_SWITCHING]["peer_score"] == 6.5       # industry row beats AI guess
    assert dims[PILLAR_SWITCHING]["peer_source"] == "industry"
    assert report["moat_competition"]["model_version"] == SCORER_VERSION


def test_the_moat_vital_counts_measured_pillars_only(monkeypatch):
    report = _assemble(
        monkeypatch, _scores(**{PILLAR_COST: 3.0}),
        ai_dims=[{"name": PILLAR_SWITCHING, "score": 9.5, "peer_score": 7.0}],
        peer_avgs={},
    )
    vital = report["_scoring_inputs"]["moat"]
    # The AI's 9.5 Switching Costs would have made this "wide".
    assert vital["overall_rating"] == "none"
    assert vital["primary_source"] == PILLAR_COST
    assert vital["score"]["value"] == 3.0


def test_no_measured_pillar_leaves_the_moat_vital_unmeasured(monkeypatch):
    report = _assemble(
        monkeypatch, _scores(),
        ai_dims=[{"name": PILLAR_SWITCHING, "score": 9.5, "peer_score": 7.0}],
        peer_avgs={},
    )
    vital = report["_scoring_inputs"]["moat"]
    assert vital["score"] == {"value": None, "status": "neutral"}
    assert _vital_score({"moat": vital}, "moat") is None      # renormalized out, not 3.0
    # The radar still shows the AI pillar.
    assert _dims(report)[PILLAR_SWITCHING]["source"] == "ai_legacy"


def test_an_empty_dimension_list_never_reads_as_a_measured_3():
    assert _vital_score({"moat": C._derive_moat_vital([])}, "moat") is None


def test_a_scoring_exception_is_logged_with_its_stack_but_not_recorded(monkeypatch, caplog):
    """A scorer exception is a bug or a data shape, not an outage (a failed median read is
    caught inside the scorer): it repeats on every rebuild, so recording it as degraded
    would keep the ticker out of every cache for good and charge each opener a new
    20-credit generation — the `_settle_caydex_dcf_failure` rule. Logged at ERROR with
    the stack instead."""
    import logging

    def _boom(**_k):
        raise RuntimeError("scorer bug")

    monkeypatch.setattr(moat_mod, "score_moat_dimensions", _boom)

    class _Peers:
        def get_pillar_benchmarks(self, industry):
            return {}

    monkeypatch.setattr(imb_mod, "_lookup_singleton", _Peers())
    shell = stage_a_fallback()
    shell["moat_competition"]["dimensions"] = [
        {"name": PILLAR_COST, "score": 4.0, "peer_score": 5.0},
    ]
    with caplog.at_level(logging.ERROR, logger=C.logger.name):
        report = C.TickerReportDataCollector().assemble_report(_make_collected_data(), shell)
    assert "moat:benchmarks" not in (report.get(C.DEGRADED_SECTIONS_KEY) or [])
    assert [d["source"] for d in report["moat_competition"]["dimensions"]] == ["ai_legacy"]
    failed = [r for r in caplog.records if "[report-moat-scoring-failed]" in r.getMessage()]
    assert failed and failed[0].exc_info is not None and "AAPL" in failed[0].getMessage()


def _http_status_error(status: int) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "https://example.invalid/stable/ratios")
    return httpx.HTTPStatusError(
        f"{status}", request=request, response=httpx.Response(status, request=request),
    )


@pytest.mark.parametrize("attr", ["income", "balance", "ratios"])
@pytest.mark.parametrize("exc,marked", [
    (FMPRateLimitException("429"), True),
    (FMPUnavailableException("503"), True),
    (TimeoutError(), True),
    # classify_fetch_failure files a raw ConnectError as "error": only the collector's
    # own transport branch marks it.
    (httpx.ConnectError("reset"), True),
    (_http_status_error(520), True),          # a Cloudflare 5xx the client does not retry
    (_http_status_error(404), False),         # a refusal repeats: stays cacheable
    (FMPNotEntitledException("402"), False),
    (ValueError("bad body"), False),
])
def test_a_transient_statement_failure_marks_the_collection_degraded(attr, exc, marked):
    out = SimpleNamespace(degraded_sections=[])
    C._settle_pass1_result(out, attr, exc, [], "JOBY")
    assert getattr(out, attr) == []
    assert (f"{attr}:{type(exc).__name__}" in out.degraded_sections) is marked


def test_other_pass1_attrs_are_not_marked_by_the_statement_rule():
    out = SimpleNamespace(degraded_sections=[])
    C._settle_pass1_result(out, "key_metrics", FMPRateLimitException("429"), [], "JOBY")
    assert out.degraded_sections == []


def test_the_coverage_note_reads_a_real_industry_zero_and_the_baseline_tag():
    """A real industry 0.0 peer is a reading (no longer lifted to 5.0), and "no peer row"
    is the `baseline` tag — never a 5.0 that a real industry average can also be."""
    dims = [
        {"name": PILLAR_INTANGIBLE, "score": 0.4, "peer_score": 0.0,
         "source": "deterministic", "peer_source": "industry"},
        {"name": PILLAR_SWITCHING, "score": 8.0, "peer_score": 0.0,
         "source": "ai_legacy", "peer_source": "industry"},
        {"name": PILLAR_BRAND, "score": 3.0, "peer_score": 5.0,
         "source": "ai_legacy", "peer_source": "industry"},     # a real 5.0 average
        {"name": PILLAR_NETWORK, "score": 3.0, "peer_score": 5.0,
         "source": "ai_legacy", "peer_source": "baseline"},
    ]
    note = C._build_moat_coverage_note(dims, "Gold")
    assert note is not None
    assert PILLAR_INTANGIBLE in note and PILLAR_NETWORK in note
    assert PILLAR_SWITCHING not in note and PILLAR_BRAND not in note
