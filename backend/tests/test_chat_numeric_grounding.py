"""The log-only numeric grounding audit (`app/services/chat_numeric_grounding.py`), 2026-10-08.

Pure tests: no network, no Supabase, no Gemini. They pin the buckets (exempt / grounded /
scaled / prior_answer_only / ungrounded, plus shadow_enforce), the rounding tolerance, the
names that are not numbers, the caps, the never-raise contract, the web-results exclusion
(Brave Search API terms §3(b)(xiii): an AI answer is never measured against search results),
the counts-only log line, and that every module-level pattern is linear.
"""

from __future__ import annotations

import ast
import asyncio
import gc
import logging
import random
import re
import time
from pathlib import Path
from typing import Callable, Tuple

import pytest

import app.services.chat_numeric_grounding as g
from app.services.chat_numeric_grounding import (
    GroundingAudit,
    GroundingEvidence,
    audit_answer,
    audit_answer_bounded,
    log_grounding_audit,
    settle_grounding_audit,
    start_grounding_audit,
)

_BUCKETS = ("exempt", "grounded", "scaled", "prior_answer_only", "ungrounded")


def _ev(caydex=(), user=(), prior=(), tools=()):
    ev = GroundingEvidence.from_seed({"caydex": list(caydex), "user": list(user),
                                      "prior_answer": list(prior)})
    for name, result in tools:
        ev.add_tool_result(name, result)
    return ev


def _one(answer, ev) -> str:
    """The single non-exempt bucket an answer with ONE number lands in."""
    a = audit_answer(answer, ev)
    assert a.skipped is None, a
    hit = [b for b in _BUCKETS if getattr(a, b)]
    assert a.numbers == 1 and len(hit) == 1, a
    return hit[0]


def _assert_invariant(a: GroundingAudit) -> None:
    assert a.numbers == sum(getattr(a, b) for b in _BUCKETS), a
    assert 0 <= a.shadow_enforce <= a.ungrounded, a


# ── rounding tolerance ────────────────────────────────────────────────────────

def test_a_rounded_billion_figure_is_grounded_and_a_wrong_one_is_not():
    ev = _ev(caydex=["Revenue: 4,213,000,000"])
    assert _one("Revenue was $4.2B.", ev) == "grounded"
    assert _one("Revenue was $4.2 billion.", ev) == "grounded"
    assert _one("Revenue was about $4 billion.", ev) == "grounded"   # half a unit = 0.5B
    assert _one("Revenue was $4.5B.", ev) == "ungrounded"
    assert _one("Revenue was $4.21B.", ev) == "grounded"
    assert _one("Revenue was $4.25B.", ev) == "ungrounded"           # 2 decimals → 0.005B


def test_a_percent_is_grounded_within_its_own_last_digit():
    ev = _ev(tools=[("t", {"net_margin": 23.41})])
    assert _one("Net margin is 23.4%.", ev) == "grounded"
    assert _one("Net margin is 23%.", ev) == "grounded"
    assert _one("Net margin is 23.5%.", ev) == "ungrounded"


def test_an_exact_integer_needs_the_exact_integer():
    ev = _ev(tools=[("ownership", {"shares": 302526})])
    assert _one("He holds 302,526 shares.", ev) == "grounded"
    assert _one("He holds 302.5K shares.", ev) == "grounded"
    assert _one("He holds 984,380 shares.", ev) == "ungrounded"


def test_ratio_to_percent_and_unit_scales_land_in_the_scaled_bucket():
    ev = _ev(tools=[("t", {"gross_margin": 0.4512, "revenue_millions": 4213})])
    assert _one("Gross margin was 45.1%.", ev) == "scaled"
    assert _one("Revenue was $4.2 billion.", ev) == "scaled"


def test_signs_are_compared_on_absolute_values():
    ev = _ev(tools=[("t", {"net_income": -1.2e9, "change_pct": -3.4})])
    assert _one("A loss of $1.2 billion.", ev) == "grounded"
    assert _one("It fell 3.4% today.", ev) == "grounded"


def test_a_unit_mismatch_between_two_written_units_does_not_ground():
    ev = _ev(caydex=["The dividend yield was 23.4%."])
    assert _one("The price was $23.4.", ev) == "ungrounded"
    # …but unit-less JSON leaves ground any unit.
    assert _one("The price was $23.4.", _ev(tools=[("t", {"p": 23.4})])) == "grounded"


# ── "scaled" is only what the answer's own writing allows (fix round, 2026-10-08) ──

def _chart(n=260, price=181.2, volume=23_412_345):
    """A dense OHLCV result, like `get_stock_chart_data`'s: the densest evidence a turn has."""
    rows = []
    for i in range(n):
        p = round(price * (1 + 0.003 * ((i * 7) % 11 - 5)), 2)
        rows.append({"date": f"2026-{(i % 12) + 1:02d}-{(i % 28) + 1:02d}", "open": p,
                     "high": round(p * 1.01, 2), "low": round(p * 0.99, 2), "close": p,
                     "volume": volume + i * 1_013})
    return {"symbol": "AAPL", "points": rows}


def test_the_reviewers_volume_coincidence_is_ungrounded_and_shadow_counted():
    # 23.4 × 10^6 sits inside a 23,412,345-share volume's window — it used to be "scaled".
    ev = _ev(caydex=["Net 24.0%"], tools=[("get_stock_chart_data",
                                           {"close": 181.2, "volume": 23_412_345})])
    a = audit_answer("Its net margin was 23.4% last year.", ev)
    assert (a.scaled, a.ungrounded, a.shadow_enforce) == (0, 1, 1), a


def test_a_percent_gets_the_ratio_factor_and_nothing_else():
    assert _one("Margin was 23.4%.", _ev(tools=[("t", {"m": 0.234})])) == "scaled"
    # No unit scale for a percent, in either direction.
    for leaf in (23_400_000, 23_400, 0.0000234, 23.4e9):
        assert _one("Margin was 23.4%.", _ev(tools=[("t", {"m": leaf})])) == "ungrounded", leaf
    # Never the reverse ÷100: "1.8%" is not a 180 anything.
    assert _one("It moved 1.8% today.", _ev(tools=[("t", {"m": 180})])) == "ungrounded"
    # The ratio factor reaches only ratio-sized evidence (≤ 10, i.e. 1,000%).
    assert _one("Growth was 1500%.", _ev(tools=[("t", {"m": 15.0})])) == "ungrounded"
    assert _one("Growth was 950%.", _ev(tools=[("t", {"m": 9.5})])) == "scaled"
    # A written percent is never a ratio of another written percent.
    assert _one("Margin was 45.1%.", _ev(caydex=["yield 0.451%"])) == "ungrounded"


def test_a_unit_less_number_may_be_a_ratio_of_a_written_percent_and_back():
    assert _one("The payout ratio was 0.45.", _ev(caydex=["Payout 45%"])) == "scaled"
    assert _one("The payout ratio was 45.", _ev(tools=[("t", {"payout": 0.45})])) == "scaled"
    # …but "18,120" is not a $181.20 price restated as a ratio (the ≤ 10 bound).
    assert _one("It has 18,120 stores.", _ev(tools=[("t", {"close": 181.2})])) == "ungrounded"
    assert _one("It has 18,120 stores.", _ev(tools=[("t", {"m": 181.2})])) == "ungrounded"


def test_only_a_suffixed_figure_restates_a_smaller_unit_and_never_past_its_suffix():
    millions = _ev(tools=[("t", {"revenue_millions": 4213})])
    assert _one("Revenue was $4.2B.", millions) == "scaled"
    assert _one("Revenue was $4.2 billion.", millions) == "scaled"
    assert _one("Revenue was 4.2 billion.", millions) == "scaled"                 # PLAIN too
    assert _one("Revenue was $4.2B.", _ev(caydex=['{"revenue": 4.2, "unit": "B"}'])) == "scaled"
    assert _one("Revenue was $4.2B.", _ev(tools=[("t", {"rev_k": 4_213_000})])) == "scaled"
    # "$42M" is 42 × 10^6: it may restate 42,000 (thousands) or 42 (millions) — never 0.042.
    assert _one("Revenue was $42M.", _ev(tools=[("t", {"r": 42_000})])) == "scaled"
    assert _one("Revenue was $42M.", _ev(tools=[("t", {"r": 0.042})])) == "ungrounded"
    # No suffix → no unit scale at all ("in millions" tables land in ungrounded, by design).
    assert _one("Revenue was $4,213.", _ev(tools=[("t", {"r": 4.213e9})])) == "ungrounded"
    assert _one("Revenue was 4,213.", _ev(tools=[("t", {"r": 4.213e9})])) == "ungrounded"
    # A multiple or a fraction never takes a unit scale.
    assert _one("It trades at 31.2x.", _ev(tools=[("t", {"r": 31_200})])) == "ungrounded"


def test_a_count_leaf_grounds_only_a_unit_less_number():
    ev = _ev(tools=[("chart", {"points": [{"volume": 123_000_000}, {"volume": 7_100_000}]})])
    assert _one("Revenue was $123M.", ev) == "ungrounded"                 # money ≠ shares
    assert _one("It traded 123M shares.", ev) == "grounded"
    assert _one("It traded 123 million shares.", ev) == "grounded"
    assert _one("Volume was 123,000,000.", ev) == "grounded"
    assert _one("Margin was 7.1%.", ev) == "ungrounded"
    for key in ("sharesOutstanding", "avgVolume", "employees", "fullTimeEmployees", "shares_held"):
        assert _one("That is $123M.", _ev(tools=[("t", {key: 123_000_000})])) == "ungrounded", key
    # A key that also names money is not a count.
    for key in ("dollarVolume", "shares_value_usd", "volume_usd", "marketCap"):
        assert _one("That is $123M.", _ev(tools=[("t", {key: 123_000_000})])) == "grounded", key
    # A list under a count key holds counts.
    assert _one("That is $5.5M.", _ev(tools=[("t", {"volumes": [5_500_000]})])) == "ungrounded"


def test_a_price_leaf_grounds_a_price_but_never_a_percent_or_a_unit_restatement():
    ev = _ev(tools=[("chart", _chart())])
    close = _chart()["points"][3]["close"]
    assert _one(f"It closed at ${close:.2f}.", ev) == "grounded"
    assert _one(f"It closed at {close:.2f}.", ev) == "grounded"
    assert _one(f"Margin was {close:.1f}%.", ev) == "ungrounded"
    assert _one(f"Revenue was ${close:.0f}M.", ev) == "ungrounded"         # not ×10^6 of a close
    assert _one(f"Revenue was ${close:.0f}B.", ev) == "ungrounded"
    for key in ("price", "dayHigh", "year_low", "previousClose", "adjClose", "vwap", "Close"):
        assert _one("Revenue was $181M.", _ev(tools=[("q", {key: 181.2})])) == "ungrounded", key
    # Only the EXACT key: a ratio whose name contains "price" is not a per-share price.
    assert _one("Revenue was $31.2M.",
                _ev(tools=[("q", {"priceEarningsRatio": 31.2})])) == "scaled"


@pytest.mark.parametrize("answer", [
    "Its net margin was {p:.1f}% last year.",
    "Revenue was ${b:.1f}B last year.",
    "Revenue was ${m:.0f}M last year.",
    "It has {i:,} stores.",
])
@pytest.mark.parametrize("price, volume", [(181.2, 23_412_345), (120.0, 200_000_000),
                                           (4.2, 4_000_000)])
def test_invented_figures_almost_never_ground_on_a_dense_chart(answer, price, volume):
    """The reviewer's measurement, pinned: against a 260-point OHLCV chart, invented figures
    used to land in "scaled" 7-25% of the time (a % × 10^6 on a volume, a "$xxxM" ÷ 10^6 on a
    close). They must now stay ungrounded (a direct coincidence with a close is still allowed,
    and rare)."""
    ev = _ev(caydex=["Net 24.0%"], tools=[("get_stock_chart_data", _chart(price=price,
                                                                          volume=volume))])
    rng = random.Random(f"{answer}{price}")
    hits = 0
    for _ in range(200):
        text = answer.format(p=rng.uniform(0.1, 99.9), b=rng.uniform(1, 99),
                             m=rng.uniform(1, 999), i=rng.randint(13, 99_999))
        a = audit_answer(text, ev)
        assert a.numbers == 1, (text, a)
        hits += a.grounded + a.scaled
    assert hits <= 4, (answer, price, hits)


def test_chart_dates_are_names_not_evidence_and_not_answer_numbers():
    ev = _ev(tools=[("chart", _chart())])
    assert _one("It has 27 stores.", ev) == "ungrounded"       # no date hands over "27"
    a = audit_answer("As of 2026-10-08 and 2026-10-08T14:05:00Z it held.", _ev())
    assert a.numbers == 0, a
    # A malformed date is not a name: its digits are still read.
    assert audit_answer("Code 2026-13-45 shipped.", _ev()).numbers >= 1


def test_leaf_kind_degrades_on_odd_keys():
    assert g._leaf_kind(None, "plain") == "plain"
    assert g._leaf_kind(5, g._COUNT) == g._COUNT                  # a non-str key inherits
    assert g._leaf_kind("", g._PRICE) == g._PRICE
    assert g._leaf_kind("x" * 10_000 + "volume", "plain") == "plain"   # cut before the scan
    assert g._leaf_kind("close", "plain") == g._PRICE
    assert g._leaf_kind("closed_deals", "plain") == "plain"
    # A count key under a price parent wins; a plain key under a count parent inherits.
    assert g._leaf_kind("volume", g._PRICE) == g._COUNT
    assert g._leaf_kind("date", g._COUNT) == g._COUNT


def test_suffix_scale_reads_only_a_real_suffix():
    def m(text):
        (mention,) = g._numbers_in(text)
        return mention
    assert g._suffix_scale(m("$4.2B")) == 1e9
    assert g._suffix_scale(m("4,213 million")) == 1e6
    assert g._suffix_scale(m("302.5K")) == 1e3
    assert g._suffix_scale(m("$4,213")) == 1.0
    assert g._suffix_scale(m("50 bps")) == 1.0
    assert g._suffix_scale(m("0.0B")) == 1.0                   # zero digits: no scale read


# ── exemptions and names ─────────────────────────────────────────────────────

def test_years_small_counts_and_days_of_month_are_exempt():
    a = audit_answer("In 2025 there were 3 segments; on Oct 8, 2026 and 12 March it held. "
                     "The 1990s were different.", _ev())
    assert a.numbers == a.exempt == 6, a


def test_thirteen_is_not_a_small_count_and_a_bare_day_is_not_a_date():
    assert _one("It has 13 segments.", _ev()) == "ungrounded"
    assert _one("Up 21 points.", _ev()) == "ungrounded"


@pytest.mark.parametrize("text", [
    "It trades in 27 markets worldwide.", "Analysts covering it declined 18 last quarter.",
    "Shares fell 19 margin points.", "They maybe 14 in all.", "A separate 20 units.",
    "Junk 25 bonds.", "It had 22 marches.", "Octane 17 grades.", "It decided 23 cases.",
    "It sold 27 marketable lots.", "Novel 16 designs.", "Augmented 21 times.",
])
def test_a_word_that_only_starts_like_a_month_does_not_exempt_a_day(text):
    assert _one(text, _ev()) == "ungrounded", text


@pytest.mark.parametrize("text", [
    "On Oct 27 it reported.", "On Oct. 27 it reported.", "On October 27 it reported.",
    "On 27 October it reported.", "On 27th of Oct it reported.", "On Sept 3 it paid.",
    "On Sep. 30 it paid.", "On September 30 it paid.", "On June 14 it met.", "On Jun 14 it met.",
    "On 15 March it met.", "On May 21 it met.", "On Dec 31 it closed.", "On 1st Jan it opened.",
])
def test_a_whole_month_token_exempts_its_day(text):
    a = audit_answer(text, _ev())
    assert a.numbers == a.exempt == 1, (text, a)


@pytest.mark.parametrize("name", [
    "its 10-K", "a Form 4", "the 52-week high", "Q3", "FY2025", "the S&P 500", "an 8-K",
    "the Nasdaq-100", "the Russell 2000", "the Dow 30", "a 13G", "the 10-year yield",
    "its 200-day average", "an S-1", "a 20-F",
])
def test_names_with_digits_are_not_numbers(name):
    a = audit_answer(f"See {name} for details.", _ev())
    assert a.numbers == 0, (name, a)


def test_a_plural_window_is_still_audited():
    assert _one("Over the past 90 days it rose.", _ev()) == "ungrounded"


# ── sources ──────────────────────────────────────────────────────────────────

def test_a_number_the_user_stated_is_grounded():
    assert _one("Investing $500 would buy that.", _ev(user=["if I invest $500"])) == "grounded"


def test_a_number_only_a_prior_answer_stated_is_prior_answer_only():
    ev = _ev(prior=["Assistant: margins are 37.7%"])
    assert _one("Margins are 37.7%.", ev) == "prior_answer_only"
    # …and primary evidence wins over prior when both state it.
    ev2 = _ev(caydex=["margin 37.7%"], prior=["margins are 37.7%"])
    assert _one("Margins are 37.7%.", ev2) == "grounded"


def test_empty_evidence_leaves_every_non_exempt_number_ungrounded():
    a = audit_answer("Revenue $9.9B, margin 41.2%, 2025.", _ev())
    assert (a.numbers, a.exempt, a.ungrounded) == (3, 1, 2), a


# ── web results are never evidence ───────────────────────────────────────────

def test_a_web_turn_is_skipped():
    a = audit_answer("Revenue $9.9B.", _ev(caydex=["9.9B"]), web_turn=True)
    assert a.skipped == "web_turn" and a.numbers == 0


def test_a_web_result_with_results_marks_the_turn_and_is_never_evidence():
    web = {"web_search": True, "status": "ok", "result_count": 1,
           "results": [{"title": "Revenue $9.9B", "snippet": "9.9B"}]}
    ev = _ev(tools=[("web_search", web)])
    assert ev.web_seen is True
    assert audit_answer("Revenue $9.9B.", ev).skipped == "web_turn"


@pytest.mark.parametrize("web", [
    {"web_search": True, "status": "capped", "results": []},
    {"web_search": True, "error": "unavailable"},
])
def test_an_empty_or_failed_web_result_is_ignored_not_evidence(web):
    ev = _ev(tools=[("web_search", web), ("other", {"x": 41.0})])
    assert ev.web_seen is False
    assert _one("Margin 41%.", ev) == "grounded"
    assert _one("Margin 77%.", ev) == "ungrounded"


def test_a_result_named_web_search_is_ignored_whatever_its_shape():
    ev = _ev(tools=[("web_search", {"anything": 88.8})])
    assert _one("It is 88.8.", ev) == "ungrounded"


def test_the_module_never_imports_the_web_search_service():
    src = Path(g.__file__).read_text()
    names = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            names.add(node.module or "")
    assert not any("chat_web_search_service" in n or "brave" in n for n in names), names


# ── outlier tool results ─────────────────────────────────────────────────────

def test_nan_inf_bool_none_and_huge_values_are_ignored_without_raising():
    ev = _ev(tools=[("t", {"a": float("nan"), "b": float("inf"), "c": True, "d": None,
                           "e": 10 ** 400, "f": -float("inf"), "g": 41.5})])
    assert _one("It is 41.5.", ev) == "grounded"
    # True must not ground "1"-like figures nor 1.0.
    assert _one("It is 1.0x.", _ev(tools=[("t", {"flag": True})])) == "ungrounded"


@pytest.mark.parametrize("result", ["Revenue 9.9B text", [9.9e9, "x"], 9.9e9, ("t", 9.9e9)])
def test_non_dict_tool_results_are_read(result):
    assert _one("Revenue $9.9B.", _ev(tools=[("t", result)])) == "grounded"


def test_a_self_referencing_result_terminates():
    loop = []
    loop.append(loop)
    d = {"v": 41.5}
    d["self"] = d
    ev = _ev(tools=[("t", loop), ("t2", d)])
    assert ev.capped is True
    assert _one("It is 41.5.", ev) == "grounded"


def test_a_too_deep_result_is_capped():
    deep = {"v": 1.0}
    for _ in range(50):
        deep = {"k": deep}
    ev = _ev(tools=[("t", deep)])
    assert ev.capped is True


def test_a_result_with_an_unknown_object_is_skipped():
    class Weird:
        def __repr__(self):
            raise RuntimeError("no")
    ev = _ev(tools=[("t", {"w": Weird(), "v": 41.5})])
    assert _one("It is 41.5.", ev) == "grounded"


def test_from_seed_ignores_garbage():
    for seed in (None, "text", 5, [], {"caydex": 5}, {"user": [None, 3, b"x"]}):
        ev = GroundingEvidence.from_seed(seed)
        assert isinstance(ev, GroundingEvidence)
    ev = GroundingEvidence.from_seed({"caydex": "a single string 41.5"})
    assert _one("It is 41.5.", ev) == "grounded"


def test_evidence_budgets_cap_and_flag():
    ev = GroundingEvidence.from_seed({"caydex": ["x 1.5 " * 40_000]})
    assert ev.capped is True
    a = audit_answer("It is 1.5.", ev)
    assert a.evidence_capped is True and a.grounded == 1


# ── answer shapes ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("answer, reason", [
    ("", "empty"), ("   ", "empty"), (None, "empty"), (123, "empty"),
    ("x" * (g.ANSWER_MAX_CHARS + 1), "too_long"),
])
def test_unauditable_answers_are_skipped_with_a_reason(answer, reason):
    assert audit_answer(answer, _ev()).skipped == reason


def test_a_missing_evidence_object_is_a_skip():
    assert audit_answer("It is 41.5.", None).skipped == "no_evidence"
    assert audit_answer("It is 41.5.", {"caydex": ["41.5"]}).skipped == "no_evidence"


def test_a_long_answer_is_audited_up_to_the_cap():
    answer = "Margin 41.5%. " + "word " * 5_000
    a = audit_answer(answer, _ev(caydex=["41.5%"]))
    assert a.answer_capped is True and a.grounded == 1


def test_the_number_cap_bounds_the_work():
    answer = " ".join(f"{i}.5" for i in range(13, 13 + g.MAX_ANSWER_NUMBERS + 100))
    a = audit_answer(answer, _ev())
    assert a.numbers == g.MAX_ANSWER_NUMBERS


def test_an_overflowing_digit_run_is_ungrounded_not_a_crash():
    a = audit_answer("It is " + "9" * 60 + ".", _ev(caydex=["9" * 60]))
    assert a.ungrounded == 1, a


def test_an_internal_failure_is_a_logged_skip(monkeypatch, caplog):
    def _boom(*a, **k):
        raise RuntimeError("bug")
    monkeypatch.setattr(g, "_audit", _boom)
    with caplog.at_level(logging.WARNING, logger=g.__name__):
        a = audit_answer("It is 41.5.", _ev())
    assert a.skipped == "error"
    assert any("CHAT_GROUNDING audit failed" in r.getMessage() for r in caplog.records)


def test_an_indexing_failure_inside_the_audit_is_contained(monkeypatch):
    monkeypatch.setattr(GroundingEvidence, "_indexes", lambda self: 1 / 0)
    assert audit_answer("It is 41.5.", _ev()).skipped == "error"


# ── shadow_enforce ───────────────────────────────────────────────────────────

def test_shadow_enforce_counts_ungrounded_money_or_percent_in_a_metric_sentence():
    ev = _ev(caydex=["revenue 4,213,000,000"])
    a = audit_answer("Revenue was $9.9B last year. You could invest $500. "
                     "Revenue was $4.2B. Gross margin hit 77.7%. The index rose 3.3 points.", ev)
    _assert_invariant(a)
    # $9.9B (revenue) and 77.7% (margin) — not $500 (no metric), not $4.2B (grounded), not
    # 3.3 (no unit).
    assert a.shadow_enforce == 2, a


def test_a_decimal_point_does_not_split_a_sentence():
    a = audit_answer("Revenue was $4.9B and then nothing.", _ev())
    assert a.shadow_enforce == 1


# ── the invariant under fuzz ─────────────────────────────────────────────────

def test_the_buckets_always_add_up_under_fuzz():
    rng = random.Random(20261008)
    pieces = ["$4.2B", "23.4%", "302,526", "2025", "Oct 8", "7", "0.45", "13", "1.5x",
              "50 bps", "3/4", "¥40", "NT$40", "10-K", "revenue", "margin", ".", "\n", "-",
              "1e9", "9" * 45, "$.50", "15-20%", "€3", "Q3", "52-week"]
    ev = _ev(caydex=["4,213,000,000 23.41% 0.45"], tools=[("t", {"x": 302526, "y": 0.234})],
             prior=["13 and 1.5x"])
    for _ in range(300):
        answer = " ".join(rng.choice(pieces) for _ in range(rng.randint(0, 40)))
        a = audit_answer(answer, ev)
        if a.skipped is None:
            _assert_invariant(a)
        else:
            assert a.skipped == "empty"


# ── the log line ─────────────────────────────────────────────────────────────

def _grounding_lines(caplog):
    return [r.getMessage() for r in caplog.records if r.getMessage().startswith("CHAT_GROUNDING door=")]


def test_the_log_line_carries_counts_and_no_answer_text(caplog):
    answer = "Zebracorn revenue was $9.9B in a secret phrase."
    a = audit_answer(answer, _ev())
    with caplog.at_level(logging.INFO, logger=g.__name__):
        log_grounding_audit(a, door="stream", session_id="sess-1", asset_type="STOCK",
                            context_type="TICKER_REPORT")
    lines = _grounding_lines(caplog)
    assert len(lines) == 1
    line = lines[0]
    for token in ("door=stream", "session=sess-1", "asset=STOCK", "ctx=TICKER_REPORT",
                  "numbers=1", "ungrounded=1", "shadow_enforce=1", "skipped=-"):
        assert token in line, (token, line)
    assert "Zebracorn" not in line and "secret" not in line and "9.9" not in line


def test_client_controlled_labels_cannot_inject_into_the_line(caplog):
    with caplog.at_level(logging.INFO, logger=g.__name__):
        log_grounding_audit({"numbers": 1}, door="send", session_id="a\nFAKE=1 x",
                            context_type="STOCK\nCHAT_GROUNDING door=evil")
    line = _grounding_lines(caplog)[0]
    assert "\n" not in line and "door=evil" not in line


@pytest.mark.parametrize("audit", [
    None, "garbage", 5, {"numbers": -3, "grounded": True, "exempt": "7", "ungrounded": 2.5},
])
def test_a_malformed_audit_logs_zeros_never_raises(audit, caplog):
    with caplog.at_level(logging.INFO, logger=g.__name__):
        log_grounding_audit(audit, door="send")
    line = _grounding_lines(caplog)[0]
    assert "numbers=0" in line and "grounded=0" in line and "exempt=0" in line


def test_a_precomputed_dict_carries_its_asset(caplog):
    with caplog.at_level(logging.INFO, logger=g.__name__):
        log_grounding_audit({**GroundingAudit(numbers=2, grounded=2).as_dict(), "asset": "ETF"},
                            door="send")
    assert "asset=ETF" in _grounding_lines(caplog)[0]


# ── the async door helpers ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_the_task_audits_off_the_loop_and_logs_one_line(monkeypatch, caplog):
    import threading
    loop_thread = threading.get_ident()
    seen = {}
    real = g.audit_answer

    def _spy(answer, evidence, **k):
        seen["thread"] = threading.get_ident()
        return real(answer, evidence, **k)

    monkeypatch.setattr(g, "audit_answer", _spy)
    with caplog.at_level(logging.INFO, logger=g.__name__):
        task = start_grounding_audit("Margin 41.5%.", _ev(caydex=["41.5%"]), door="stream",
                                     session_id="s")
        await settle_grounding_audit(task)
    assert "thread" in seen, "VACUOUS: the audit never ran"
    assert seen["thread"] != loop_thread, "the audit ran ON the event loop thread"
    lines = _grounding_lines(caplog)
    assert len(lines) == 1 and "grounded=1" in lines[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("kw, expect", [
    ({"precomputed": {"numbers": 4, "grounded": 4}}, "grounded=4"),
    ({"replay": "cached"}, "skipped=cached"),
    ({"web_turn": True}, "skipped=web_turn"),
    ({"precomputed": {"skipped": "no_audit"}, "fallback": True}, "fallback=True"),
])
async def test_the_task_logs_skips_and_precomputed_audits(kw, expect, caplog):
    with caplog.at_level(logging.INFO, logger=g.__name__):
        await settle_grounding_audit(start_grounding_audit("Margin 41.5%.", _ev(),
                                                           door="stream", **kw))
    lines = _grounding_lines(caplog)
    assert len(lines) == 1 and expect in lines[0], lines


@pytest.mark.asyncio
async def test_an_audit_that_raises_still_logs_a_skip(monkeypatch, caplog):
    def _boom(*a, **k):
        raise RuntimeError("bug")
    monkeypatch.setattr(g, "audit_answer", _boom)
    with caplog.at_level(logging.INFO, logger=g.__name__):
        await settle_grounding_audit(start_grounding_audit("x 41.5", _ev(), door="stream"))
    assert "skipped=error" in _grounding_lines(caplog)[0]


@pytest.mark.asyncio
async def test_settle_never_cancels_a_slow_audit(monkeypatch, caplog):
    import threading
    gate = threading.Event()

    def _slow(answer, evidence, **k):
        gate.wait(5)
        return GroundingAudit(numbers=1, grounded=1)

    monkeypatch.setattr(g, "audit_answer", _slow)
    with caplog.at_level(logging.INFO, logger=g.__name__):
        task = start_grounding_audit("x", _ev(), door="stream")
        await settle_grounding_audit(task, timeout=0.05)
        assert not task.done() and not task.cancelled()
        gate.set()
        await asyncio.wait({task}, timeout=5)
    assert task.done() and not task.cancelled()
    assert "grounded=1" in _grounding_lines(caplog)[0]


@pytest.mark.asyncio
async def test_settle_tolerates_none_and_a_finished_task():
    await settle_grounding_audit(None)
    task = start_grounding_audit("x", _ev(), door="stream", replay="warm")
    await settle_grounding_audit(task)
    await settle_grounding_audit(task)


@pytest.mark.asyncio
async def test_the_bounded_audit_returns_the_counts():
    a = await audit_answer_bounded("Margin 41.5%.", _ev(caydex=["41.5%"]))
    assert a.grounded == 1 and a.skipped is None


@pytest.mark.asyncio
async def test_the_bounded_audit_gives_up_on_a_busy_pool_without_raising(monkeypatch, caplog):
    import threading
    gate = threading.Event()

    def _stuck(answer, evidence, **k):
        gate.wait(5)
        return GroundingAudit(numbers=1, grounded=1)

    monkeypatch.setattr(g, "audit_answer", _stuck)
    monkeypatch.setattr(g, "INLINE_AUDIT_SECONDS", 0.05)       # read at call time
    t0 = time.monotonic()
    with caplog.at_level(logging.WARNING, logger=g.__name__):
        a = await audit_answer_bounded("x 41.5", _ev())
    gate.set()
    assert a.skipped == "timeout" and a.numbers == 0
    assert time.monotonic() - t0 < 1.0
    assert any("inline audit exceeded" in r.getMessage() for r in caplog.records)
    # An explicit timeout wins over the module default.
    gate.clear()
    t0 = time.monotonic()
    assert (await audit_answer_bounded("x", _ev(), timeout=0.01)).skipped == "timeout"
    gate.set()
    assert time.monotonic() - t0 < 1.0


@pytest.mark.asyncio
async def test_the_bounded_audit_turns_a_failure_into_a_skip(monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("bug")
    monkeypatch.setattr(g, "audit_answer", _boom)
    assert (await audit_answer_bounded("x 41.5", _ev())).skipped == "error"


@pytest.mark.asyncio
async def test_the_bounded_audit_lets_the_callers_cancellation_through(monkeypatch):
    import threading
    gate = threading.Event()
    monkeypatch.setattr(g, "audit_answer", lambda *a, **k: gate.wait(5))
    task = asyncio.ensure_future(audit_answer_bounded("x", _ev(), timeout=5))
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    gate.set()


def test_start_without_a_running_loop_returns_none(caplog):
    with caplog.at_level(logging.WARNING, logger=g.__name__):
        assert start_grounding_audit("x", _ev(), door="stream") is None
    assert any("not started" in r.getMessage() for r in caplog.records)


# ── every module-level pattern is linear ─────────────────────────────────────

def _cpu_pair(small: Callable[[], object], big: Callable[[], object],
              rounds: int = 5) -> Tuple[float, float]:
    was = gc.isenabled()
    gc.disable()
    try:
        ts = tb = float("inf")
        for _ in range(rounds):
            t0 = time.thread_time()
            small()
            ts = min(ts, time.thread_time() - t0)
            t0 = time.thread_time()
            big()
            tb = min(tb, time.thread_time() - t0)
        return ts, tb
    finally:
        if was:
            gc.enable()


def _linear(rx, unit, small=3000, big=12000, floor=0.002, limit=8.0) -> bool:
    """Same load-robust shape as the marketing validators' sweep: interleaved thread-CPU
    minima on a 4x rung (linear ≈ 4x, quadratic ≈ 16x), confirmed by a second measurement."""
    for _ in range(2):
        s = (unit * big)[:small]
        b = (unit * big)[:big]
        ts, tb = _cpu_pair(lambda: list(rx.finditer(s)), lambda: list(rx.finditer(b)))
        if tb < 0.25 and (tb < floor or tb / max(ts, 1e-6) < limit):
            return True
    return False


_UNITS = ("a.", "9.", "9,", "10-", "Form ", "s&p ", "oct ", "1 day ", "$1.", "jan.  ",
          "revenue ", "x\n", "9 ")


def _module_patterns():
    return [(name, v) for name, v in vars(g).items() if isinstance(v, re.Pattern)]


def test_every_module_pattern_is_linear():
    pats = _module_patterns()
    assert len(pats) >= 6, pats
    slow = [(name, unit) for name, rx in pats for unit in _UNITS if not _linear(rx, unit)]
    assert slow == [], slow


def test_the_linearity_check_is_not_vacuous():
    assert not _linear(re.compile(r"(?:[a-z]\.)+Z"), "a.")


def test_the_whole_audit_is_linear_in_the_answer():
    ev = _ev(caydex=["4,213,000,000 23.41%"])

    def run(n):
        text = ("Revenue $4.2B, margin 23.4%, Oct 8. " * (n // 36 + 1))[:n]
        return lambda: audit_answer(text, ev)

    for _ in range(2):
        ts, tb = _cpu_pair(run(2000), run(16000), rounds=3)
        if tb < 1.0 and (tb < 0.01 or tb / max(ts, 1e-6) < 16):
            return
    raise AssertionError(("super-linear audit", ts, tb))
