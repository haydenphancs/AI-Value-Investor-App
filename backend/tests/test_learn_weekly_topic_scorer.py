"""The Wiser weekly topic scorer (plan §1.5): robust z, ticker and theme scores, the 20% abort,
fail-closed exclusions, the no-repeat windows, the quiet-week fallback and order independence.

Pure module, no I/O: every input is built inline. Exact thresholds (≥ 3 inclusive, > 1.0 strict)
are tested through a patched `ticker_score` so the boundary is hit exactly — robust z divides by
1.4826, which no count lands on exactly; the end-to-end cases use the real scoring.
"""

from __future__ import annotations

import ast
import json
import logging
import math
import random
from pathlib import Path

import pytest

from app.services.learn_weekly import topic_scorer as ts
from app.services.learn_weekly.contracts import ThemeInput, TickerSignal, TopicCandidate

LOGGER = "app.services.learn_weekly.topic_scorer"
FLAT = (0, 0, 0, 0, 0, 0, 0, 0)          # median 0, MAD 0 → floor 1 → z = x / 1.4826


def sig(ticker, news=None, base=FLAT, **kw):
    kw.setdefault("size_ok", True)
    return TickerSignal(ticker=ticker, news_week=news, news_baseline=base, **kw)


def theme(slug, members, eligible=True, title=None, last_covered=None):
    return ThemeInput(slug=slug, title=title if title is not None else slug.replace("-", " ").title(),
                      members=tuple(members), eligible=eligible, last_covered=last_covered)


def news_for(score):
    """A news count whose real score (FLAT baseline, no 8-K) is `score` (to float precision)."""
    return score / 0.6 * ts.MAD_SCALE


ALL = object()   # `rank(...)` default: every signal's ticker has a verified company primer


def _all_tickers(signals):
    return [getattr(s, "ticker", None) for s in signals if isinstance(getattr(s, "ticker", None), str)]


def rank(signals, themes, recent_themes=(), recent_companies=(), top_n=3, primed=ALL):
    signals = list(signals)
    return ts.rank_topics(signals, themes, recent_themes=recent_themes,
                          recent_companies=recent_companies,
                          primed_tickers=_all_tickers(signals) if primed is ALL else primed,
                          top_n=top_n)


@pytest.fixture
def fixed_scores(monkeypatch):
    """Patch `ticker_score` (the module global `score_tickers` calls) with exact scores per ticker.
    Exclusions still run first in `rank_topics`, so the signals must be non-excluded."""
    table = {}

    def fake(sig_):
        if ts.exclusion_reason(sig_) is not None or sig_.ticker not in table:
            return None
        return table[sig_.ticker], {"z_news": 0.0, "z_8k": 0.0, "z_8k_missing": 1.0,
                                    "earnings_bonus": 0.0, "saturated": 0.0}

    monkeypatch.setattr(ts, "ticker_score", fake)
    return table


def fixed(table, scores):
    table.update(scores)
    return [sig(t, news=1) for t in scores]


# ── robust_z ──────────────────────────────────────────────────────────────────────────────────


def test_robust_z_flat_baseline_uses_the_mad_floor_not_a_zero_division():
    assert ts.robust_z(10, (2, 2, 2, 2)) == pytest.approx(8 / 1.4826)
    assert ts.robust_z(2, (2, 2, 2, 2)) == 0.0


def test_robust_z_uses_median_and_mad_when_the_mad_exceeds_the_floor():
    # median 20, |dev| = 20,10,0,10,20 → MAD 10.
    assert ts.robust_z(40, (0, 10, 20, 30, 40)) == pytest.approx(20 / (1.4826 * 10))


def test_robust_z_one_outlier_week_in_the_baseline_does_not_move_it():
    calm = ts.robust_z(9, (3, 3, 4, 4, 5, 5, 4, 3))
    spiked = ts.robust_z(9, (3, 3, 4, 4, 5, 5, 4, 300))
    assert calm == pytest.approx(spiked)


def test_robust_z_clamps_to_minus_five_and_ten():
    assert ts.robust_z(1000, (1, 1, 1, 1)) == 10.0
    assert ts.robust_z(1e308, (1, 1, 1, 1)) == 10.0
    assert ts.robust_z(0, (100, 100, 100, 100)) == -5.0


def test_robust_z_needs_four_usable_baseline_weeks():
    assert ts.robust_z(5, (1, 1, 1)) is None
    assert ts.robust_z(5, ()) is None
    assert ts.robust_z(5, (1, 1, 1, 1)) is not None


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -float("inf"), -1, True, False, "3", None,
                                 10 ** 400])
def test_robust_z_drops_malformed_baseline_weeks_never_reads_them_as_zero(bad):
    # Three good weeks + one malformed = three usable → None (a zero would have made four).
    assert ts.robust_z(5, (1, 1, 1, bad)) is None
    # With four good weeks the malformed one is ignored, not averaged in.
    assert ts.robust_z(5, (1, 1, 1, 1, bad)) == ts.robust_z(5, (1, 1, 1, 1))


@pytest.mark.parametrize("bad", [None, float("nan"), float("inf"), -1, -0.5, True, False, "5", 10 ** 400, [5]])
def test_robust_z_missing_or_malformed_x_is_none(bad):
    assert ts.robust_z(bad, (1, 1, 1, 1)) is None


def test_robust_z_zero_is_a_count_not_missing():
    assert ts.robust_z(0, (4, 4, 4, 4)) == pytest.approx(-4 / 1.4826)


@pytest.mark.parametrize("baseline", [None, "1111", 7, {"a": 1}])
def test_robust_z_malformed_baseline_container_is_none(baseline):
    assert ts.robust_z(5, baseline) is None


def test_robust_z_accepts_a_list_and_float_counts():
    assert ts.robust_z(3.5, [1.0, 1, 1, 1]) == pytest.approx(2.5 / 1.4826)


# ── exclusions and ticker_score ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize("kw, reason", [
    ({"size_ok": None}, ts.EXCLUDED_SIZE),
    ({"size_ok": False}, ts.EXCLUDED_SIZE),
    ({"size_ok": 1}, ts.EXCLUDED_SIZE),               # truthy non-bool: fail closed
    ({"foreign_filer": True}, ts.EXCLUDED_FOREIGN),
    ({"foreign_filer": "yes"}, ts.EXCLUDED_FOREIGN),  # malformed: fail closed
    ({"foreign_filer": 1}, ts.EXCLUDED_FOREIGN),
    ({"excluded_reason": "etf"}, ts.EXCLUDED_UPSTREAM),
    ({"excluded_reason": ""}, ts.EXCLUDED_UPSTREAM),  # a set field is a decision
])
def test_exclusions_are_fail_closed(kw, reason):
    s = sig("NVDA", news=50, **kw)
    assert ts.exclusion_reason(s) == reason
    assert ts.ticker_score(s) is None


@pytest.mark.parametrize("foreign", [False, None])
def test_known_domestic_or_unknown_foreign_status_is_scored(foreign):
    assert ts.ticker_score(sig("NVDA", news=5, foreign_filer=foreign)) is not None


@pytest.mark.parametrize("ticker", ["", "   ", None, 7])
def test_a_malformed_ticker_is_excluded(ticker):
    s = TickerSignal(ticker=ticker, news_week=5, news_baseline=FLAT, size_ok=True)
    assert ts.exclusion_reason(s) == ts.EXCLUDED_BAD_TICKER
    assert ts.ticker_score(s) is None


def test_missing_news_is_none_but_zero_news_is_a_score():
    assert ts.ticker_score(sig("NVDA", news=None)) is None
    zero = ts.ticker_score(sig("NVDA", news=0, base=(3, 3, 3, 3)))
    assert zero is not None and zero[0] < 0


def test_a_short_news_baseline_is_unscoreable():
    assert ts.ticker_score(sig("CRWV", news=40, base=(1, 2, 3))) is None


def test_ticker_score_formula_and_components():
    s = sig("NVDA", news=10, base=(2, 2, 2, 2), filings_8k_week=3, filings_8k_baseline=(1, 1, 1, 1),
            earnings_this_week=True, items_8k=("2.02", "9.01"), news_saturated=True)
    score, comp = ts.ticker_score(s)
    z_news, z_8k = 8 / 1.4826, 2 / 1.4826
    assert score == pytest.approx(0.6 * z_news + 0.4 * z_8k + 0.5)
    assert comp == pytest.approx({"z_news": z_news, "z_8k": z_8k, "z_8k_missing": 0.0,
                                  "earnings_bonus": 0.5, "saturated": 1.0})


def test_missing_8k_z_counts_as_zero_and_is_flagged():
    for kw in ({"filings_8k_week": None, "filings_8k_baseline": (1, 1, 1, 1)},
               {"filings_8k_week": 2, "filings_8k_baseline": ()},
               {"filings_8k_week": float("nan"), "filings_8k_baseline": (1, 1, 1, 1)}):
        score, comp = ts.ticker_score(sig("NVDA", news=10, base=(2, 2, 2, 2), **kw))
        assert comp["z_8k"] == 0.0 and comp["z_8k_missing"] == 1.0
        assert score == pytest.approx(0.6 * 8 / 1.4826)


@pytest.mark.parametrize("earnings, items, bonus", [
    (True, ("2.02",), 0.5),
    (True, (" 2.02 ",), 0.5),
    (True, ("1.01", "8.01"), 0.0),      # earnings week but no results 8-K
    (False, ("2.02",), 0.0),            # a 2.02 outside an earnings week
    (1, ("2.02",), 0.0),                # truthy non-bool earnings flag
    (True, "12.02", 0.0),               # a bare string must not substring-match
    (True, "2.02", 0.0),
    (True, (None, 2.02), 0.0),          # non-string items ignored
])
def test_earnings_bonus_needs_an_earnings_week_and_item_2_02(earnings, items, bonus):
    _, comp = ts.ticker_score(sig("NVDA", news=10, earnings_this_week=earnings, items_8k=items))
    assert comp["earnings_bonus"] == bonus


def test_a_string_items_8k_is_reported_not_silently_read(caplog):
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        _, comp = ts.ticker_score(sig("NVDA", news=10, earnings_this_week=True, items_8k="2.02"))
    assert comp["earnings_bonus"] == 0.0
    assert "NVDA items_8k is a str" in caplog.text


def test_a_saturated_count_is_flagged_and_its_score_is_bounded():
    score, comp = ts.ticker_score(sig("NVDA", news=10 ** 6, news_saturated=True,
                                      filings_8k_week=10 ** 6, filings_8k_baseline=FLAT,
                                      earnings_this_week=True, items_8k=("2.02",)))
    assert comp["saturated"] == 1.0 and comp["z_news"] == 10.0
    assert score == pytest.approx(10.5)          # the highest score any ticker can reach
    _, plain = ts.ticker_score(sig("NVDA", news=5))
    assert plain["saturated"] == 0.0


# ── rank_topics: themes ───────────────────────────────────────────────────────────────────────


def test_theme_scores_the_mean_of_its_top_three_members(fixed_scores):
    s = fixed(fixed_scores, {"A": 2.9, "B": 2.0, "C": 1.5, "D": 1.2})
    out = rank(s, [theme("chips", ["D", "C", "B", "A"])])
    assert len(out) == 1
    c = out[0]
    assert (c.kind, c.key, c.reason) == ("theme", "chips", "ranked")
    assert c.tickers == ("A", "B", "C")
    assert c.score == pytest.approx((2.9 + 2.0 + 1.5) / 3)
    assert c.components["members_scored"] == 4.0 and c.components["score:A"] == 2.9


def test_theme_needs_two_members_strictly_above_one(fixed_scores):
    s = fixed(fixed_scores, {"A": 2.5, "B": 1.0, "C": 0.9})
    assert rank(s, [theme("chips", ["A", "B", "C"])])[0].reason == ts.REASON_QUIET_WEEK
    fixed_scores["B"] = 1.0001
    out = rank(s, [theme("chips", ["A", "B", "C"])])
    assert out[0].reason == "ranked" and out[0].score == pytest.approx((2.5 + 1.0001 + 0.9) / 3)


def test_a_two_member_theme_averages_two(fixed_scores):
    s = fixed(fixed_scores, {"A": 2.0, "B": 1.5})
    out = rank(s, [theme("pair", ["A", "B", "UNSCORED"])])
    assert out[0].score == pytest.approx(1.75) and out[0].tickers == ("A", "B")


def test_a_member_listed_twice_in_one_theme_counts_once(fixed_scores):
    s = fixed(fixed_scores, {"A": 2.0, "B": 0.5})
    out = rank(s, [theme("dup", ["A", "a", " A ", "B"])])
    assert out[0].reason == ts.REASON_QUIET_WEEK     # only ONE member above 1.0


def test_a_ticker_in_two_themes_counts_in_both(fixed_scores):
    s = fixed(fixed_scores, {"NVDA": 2.5, "AMD": 2.0, "VST": 1.8})
    out = rank(s, [theme("chips", ["NVDA", "AMD"]), theme("power", ["NVDA", "VST"])])
    assert {c.key: c.score for c in out} == pytest.approx({"chips": 2.25, "power": 2.15})
    assert [c.key for c in out] == ["chips", "power"]


def test_excluded_members_do_not_lift_a_theme(fixed_scores):
    fixed_scores.update({"NVDA": 2.0, "TSM": 9.0})
    s = [sig("NVDA", news=1), sig("TSM", news=1, foreign_filer=True)]
    out = rank(s, [theme("chips", ["NVDA", "TSM"])])
    assert out[0].reason == ts.REASON_QUIET_WEEK


@pytest.mark.parametrize("eligible", [False, None, 1, "yes"])
def test_only_an_eligible_true_theme_is_ranked(fixed_scores, eligible):
    s = fixed(fixed_scores, {"A": 2.0, "B": 2.0})
    assert rank(s, [theme("chips", ["A", "B"], eligible=eligible)]) == []


def test_a_recent_theme_is_never_offered(fixed_scores):
    s = fixed(fixed_scores, {"A": 2.0, "B": 2.0, "C": 1.5, "D": 1.5})
    themes = [theme("chips", ["A", "B"]), theme("power", ["C", "D"])]
    out = rank(s, themes, recent_themes=[" Chips "])
    assert [c.key for c in out] == ["power"]


def test_malformed_theme_members_and_slugs_are_skipped_loudly(fixed_scores, caplog):
    s = fixed(fixed_scores, {"A": 2.0, "B": 2.0})
    bad_members = ThemeInput(slug="str-members", title="x", members="AB", eligible=True)
    bad_slug = ThemeInput(slug="  ", title="x", members=("A", "B"), eligible=True)
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        out = rank(s, [bad_members, bad_slug, theme("ok", ["A", None, "B"])])
    assert [c.key for c in out] == ["ok"]
    assert "not a sequence" in caplog.text and "malformed slug" in caplog.text
    assert "malformed member" in caplog.text


def test_a_theme_without_a_title_falls_back_to_its_slug(fixed_scores):
    s = fixed(fixed_scores, {"A": 2.0, "B": 2.0})
    assert rank(s, [theme("chips", ["A", "B"], title="  ")])[0].title == "chips"


def test_duplicate_theme_slugs_keep_the_first(fixed_scores, caplog):
    s = fixed(fixed_scores, {"A": 2.0, "B": 2.0, "C": 5.0, "D": 5.0})
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        out = rank(s, [theme("chips", ["A", "B"]), theme("CHIPS", ["C", "D"])], recent_companies=["C", "D"])
    assert [(c.key, c.tickers) for c in out] == [("chips", ("A", "B"))]
    assert "duplicate theme slug" in caplog.text


# ── rank_topics: single tickers and the drop rule ─────────────────────────────────────────────


def test_a_single_ticker_qualifies_at_three_inclusive(fixed_scores):
    s = fixed(fixed_scores, {"CRWV": 3.0, "ORCL": 2.9999})
    out = rank(s, [])
    assert [(c.kind, c.key, c.tickers, c.title) for c in out] == [("ticker", "CRWV", ("CRWV",), "CRWV")]


# ── A lone company needs its verified company primer (owner decision 2026-10-24) ────────────


def test_a_lone_ticker_without_a_company_primer_is_never_offered(fixed_scores):
    """"Skip single-company topics that have no verified case-library entry" — the 3M week: a
    breakout ticker with no company primer is passed over and the next candidate fills its slot."""
    s = fixed(fixed_scores, {"MMM": 9.0, "CRWV": 4.0, "NVDA": 3.5})
    out = rank(s, [], primed=["CRWV", "NVDA"])
    assert [(c.kind, c.key) for c in out] == [("ticker", "CRWV"), ("ticker", "NVDA")]
    assert rank(s, [], primed=[]) == []                     # no primer, no theme: nothing offered
    out = rank(s, [theme("ai", ["CRWV", "NVDA"])], primed=[])
    assert [(c.kind, c.key) for c in out] == [("theme", "ai")]


def test_an_unprimed_ticker_still_lifts_its_theme_and_claims_no_slot(fixed_scores):
    """MMM is NOT the theme's top member (HON is), so primed it would hold its own slot beside the
    theme; unprimed it holds none — yet still counts in the theme's mean (review 2026-10-24: the
    first version made MMM the top member, which the claimed rule drops anyway)."""
    s = fixed(fixed_scores, {"HON": 9.5, "MMM": 9.0, "GE": 2.0})
    themes = [theme("industrials", ["MMM", "HON", "GE"])]
    # Primed: MMM (9.0) keeps its own slot; HON's is taken by the theme it tops.
    assert [(c.kind, c.key) for c in rank(s, themes)] == [("ticker", "MMM"), ("theme", "industrials")]
    out = rank(s, themes, primed=["HON", "GE"])
    assert [(c.kind, c.key) for c in out] == [("theme", "industrials")]
    assert out[0].tickers == ("HON", "MMM", "GE")
    assert out[0].score == pytest.approx((9.5 + 9.0 + 2.0) / 3)


def test_unprimed_breakouts_names_what_was_passed_over(fixed_scores):
    s = fixed(fixed_scores, {"MMM": 9.0, "GE": 3.0, "HON": 2.99, "CRWV": 5.0, "AMD": 4.0})
    scores = ts.score_tickers(s)
    assert ts.unprimed_breakouts(scores, ["CRWV"], ["AMD"]) == ["MMM", "GE"]   # best first; ≥ 3 only
    assert ts.unprimed_breakouts(scores, [" mmm ", "ge", "crwv"], []) == ["AMD"]
    assert ts.unprimed_breakouts({}, [], []) == []
    with pytest.raises(TypeError):
        ts.unprimed_breakouts(scores, "MMM", [])


def test_primed_tickers_are_normalised_and_junk_is_skipped(fixed_scores, caplog):
    s = fixed(fixed_scores, {"CRWV": 4.0, "MMM": 5.0})
    with caplog.at_level(logging.INFO, logger=ts.logger.name):
        out = rank(s, [], primed=[" crwv ", None, 7, ""])
    assert [c.key for c in out] == ["CRWV"]
    assert "no verified company primer" in caplog.text and "MMM" in caplog.text


def test_the_primer_rule_is_order_independent(fixed_scores):
    s = fixed(fixed_scores, {"AA": 5.0, "BB": 4.0, "CC": 3.5, "DD": 3.2})
    primed = ["DD", "BB", "CC"]
    expected = rank(s, [], primed=primed)
    rng = random.Random(1024)
    for _ in range(20):
        sh, pr = s[:], primed[:]
        rng.shuffle(sh)
        rng.shuffle(pr)
        assert rank(sh, [], primed=set(pr)) == expected
    assert [c.key for c in expected] == ["BB", "CC", "DD"]


def test_primed_tickers_is_required():
    with pytest.raises(TypeError):
        ts.rank_topics([], [], recent_themes=(), recent_companies=())   # type: ignore[call-arg]


def test_a_recent_company_is_not_offered_alone_but_still_lifts_its_theme(fixed_scores):
    s = fixed(fixed_scores, {"CRWV": 6.0, "NVDA": 1.5})
    out = rank(s, [theme("ai-infra", ["CRWV", "NVDA"])], recent_companies=[" crwv "])
    assert [(c.kind, c.key) for c in out] == [("theme", "ai-infra")]
    assert out[0].tickers == ("CRWV", "NVDA")


def test_the_top_member_of_a_selected_theme_is_not_offered_again(fixed_scores):
    # NVDA (6.0) outranks the theme it leads (mean 4.0): the same story twice. The theme keeps it.
    s = fixed(fixed_scores, {"NVDA": 6.0, "AMD": 3.0, "AVGO": 3.0, "CRWV": 3.5})
    out = rank(s, [theme("chips", ["NVDA", "AMD", "AVGO"])])
    assert [(c.kind, c.key) for c in out] == [("theme", "chips"), ("ticker", "CRWV"), ("ticker", "AMD")]


def test_the_drop_rule_also_holds_when_the_theme_ranks_higher_on_a_tie(fixed_scores):
    s = fixed(fixed_scores, {"NVDA": 4.0, "AMD": 4.0})
    out = rank(s, [theme("a-chips", ["NVDA", "AMD"])])
    # Theme 4.0 vs NVDA 4.0 vs AMD 4.0: key asc puts "AMD" < "NVDA" < "a-chips" (uppercase first).
    # AMD is the theme's top member (tie → ticker asc), so AMD is dropped; NVDA is not its top member.
    assert [(c.kind, c.key) for c in out] == [("ticker", "NVDA"), ("theme", "a-chips")]


def test_a_ticker_reached_after_its_selected_theme_is_skipped(fixed_scores):
    # A theme ranks strictly above its top member only on a tie its key wins: an upper-case slug
    # ("AI") sorts before "NVDA". The ticker is then reached AFTER the theme claimed it.
    s = fixed(fixed_scores, {"NVDA": 4.0, "ZZ": 4.0})
    out = rank(s, [theme("AI", ["NVDA", "ZZ"])])
    assert [(c.kind, c.key) for c in out] == [("theme", "AI"), ("ticker", "ZZ")]


def test_a_non_top_member_ticker_is_kept_beside_its_theme(fixed_scores):
    s = fixed(fixed_scores, {"NVDA": 5.0, "AMD": 4.0, "AVGO": 1.5})
    out = rank(s, [theme("chips", ["NVDA", "AMD", "AVGO"])])
    assert [(c.kind, c.key) for c in out] == [("ticker", "AMD"), ("theme", "chips")]


def test_a_ticker_that_fills_the_list_before_its_theme_is_reached_stays(fixed_scores):
    s = fixed(fixed_scores, {"NVDA": 9.0, "X": 8.0, "Y": 7.0, "AMD": 1.5})
    out = rank(s, [theme("chips", ["NVDA", "AMD"])], top_n=3)
    assert [c.key for c in out] == ["NVDA", "X", "Y"]


def test_ties_sort_by_key_and_top_n_cuts(fixed_scores):
    s = fixed(fixed_scores, {"ZZ": 4.0, "AA": 4.0, "MM": 4.0, "BB": 5.0})
    assert [c.key for c in rank(s, [])] == ["BB", "AA", "MM"]
    assert [c.key for c in rank(s, [], top_n=1)] == ["BB"]
    assert [c.key for c in rank(s, [], top_n=10)] == ["BB", "AA", "MM", "ZZ"]


@pytest.mark.parametrize("top_n", [0, -1, True, "3", 2.0, None])
def test_a_bad_top_n_is_a_caller_bug(top_n):
    with pytest.raises(ValueError):
        rank([], [], top_n=top_n)


@pytest.mark.parametrize("window", [None, "chips", b"chips"])
def test_a_missing_or_string_window_is_a_caller_bug(window):
    with pytest.raises(TypeError):
        ts.rank_topics([], [], recent_themes=window, recent_companies=(), primed_tickers=())
    with pytest.raises(TypeError):
        ts.rank_topics([], [], recent_themes=(), recent_companies=window, primed_tickers=())
    with pytest.raises(TypeError):
        ts.rank_topics([], [], recent_themes=(), recent_companies=(), primed_tickers=window)


# ── rank_topics: end to end with the real scoring ─────────────────────────────────────────────


def test_end_to_end_real_scores_pick_the_busy_theme_and_the_breakout_ticker():
    signals = [
        sig("NVDA", news=news_for(4.0)), sig("AMD", news=news_for(2.0)), sig("AVGO", news=news_for(1.2)),
        sig("VST", news=news_for(0.4)), sig("CEG", news=news_for(0.2)),
        sig("CRWV", news=news_for(3.5)),
        sig("TSM", news=50, foreign_filer=True), sig("SPY", news=90, excluded_reason="etf"),
    ]
    themes = [theme("ai-chips", ["NVDA", "AMD", "AVGO", "TSM"]), theme("ai-power", ["VST", "CEG"])]
    out = rank(signals, themes)
    assert [(c.kind, c.key) for c in out] == [("ticker", "CRWV"), ("theme", "ai-chips")]
    assert out[1].score == pytest.approx((4.0 + 2.0 + 1.2) / 3)
    assert out[1].tickers == ("NVDA", "AMD", "AVGO")
    json.dumps([c.to_json() for c in out])          # the ledger's topic jsonb must serialise


def test_a_negative_news_spike_never_qualifies():
    signals = [sig(t, news=0, base=(30, 30, 30, 30)) for t in ("A", "B", "C")]
    out = rank(signals, [theme("quiet", ["A", "B", "C"])])
    assert out[0].reason == ts.REASON_QUIET_WEEK and out[0].score == 0.0


def test_one_outlier_member_cannot_carry_a_theme_alone():
    # A 10.5 member (the clamp ceiling) plus flat peers: breadth fails, the outlier stands alone.
    signals = [sig("MEME", news=10 ** 9, news_saturated=True, filings_8k_week=10 ** 9,
                   filings_8k_baseline=FLAT, earnings_this_week=True, items_8k=("2.02",)),
               sig("B", news=0), sig("C", news=0)]
    out = rank(signals, [theme("memes", ["MEME", "B", "C"])])
    assert [(c.kind, c.key) for c in out] == [("ticker", "MEME")]
    assert out[0].score == pytest.approx(10.5) and out[0].components["saturated"] == 1.0


# ── duplicates ────────────────────────────────────────────────────────────────────────────────


def test_a_duplicate_ticker_signal_keeps_the_first_and_warns(caplog):
    first = sig("CRWV", news=news_for(5.0))
    second = sig("crwv", news=None)          # would count as missing (and abort 1 of 1) if kept
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        out = rank([first, second], [])
    assert [c.key for c in out] == ["CRWV"]
    assert "duplicate TickerSignal for CRWV" in caplog.text
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        with pytest.raises(ts.ScoringAborted):
            rank([second, first], [])


def test_score_tickers_normalises_and_dedupes():
    scores = ts.score_tickers([sig(" nvda ", news=5), sig("NVDA", news=50), sig("X", news=None)])
    assert list(scores) == ["NVDA"]
    assert scores["NVDA"][0] == pytest.approx(0.6 * 5 / 1.4826)


# ── the 20% missing abort ─────────────────────────────────────────────────────────────────────


def _universe(n_ok, n_missing, missing_value=None):
    ok = [sig(f"OK{i}", news=1) for i in range(n_ok)]
    missing = [sig(f"MISS{i}", news=missing_value) for i in range(n_missing)]
    return ok + missing


def test_more_than_twenty_percent_missing_aborts(caplog):
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        with pytest.raises(ts.ScoringAborted) as exc:
            rank(_universe(7, 3), [theme("t", ["OK0", "OK1"])])
    assert (exc.value.missing, exc.value.universe) == (3, 10)
    assert exc.value.sample == ("MISS0", "MISS1", "MISS2")
    assert "3 of 10" in str(exc.value) and "aborting" in caplog.text
    with pytest.raises(ts.ScoringAborted):
        rank(_universe(3, 1), [])                   # 25%


def test_exactly_twenty_percent_missing_does_not_abort():
    rank(_universe(8, 2), [])                       # 20% of 10
    rank(_universe(4, 1), [])                       # 20% of 5
    rank(_universe(80, 20), [])


@pytest.mark.parametrize("bad", [float("nan"), True, -3, "12", float("inf")])
def test_a_malformed_news_count_counts_as_missing(bad):
    with pytest.raises(ts.ScoringAborted):
        rank(_universe(3, 1, missing_value=bad), [])


def test_zero_news_is_not_missing():
    rank(_universe(3, 1, missing_value=0), [])      # 0 is a real count: no abort


def test_excluded_tickers_do_not_count_toward_the_abort():
    excluded = [sig(f"X{i}", news=None, size_ok=None) for i in range(50)]
    excluded += [sig("F", news=None, foreign_filer=True), sig("E", news=None, excluded_reason="etf")]
    rank(_universe(8, 2) + excluded, [])            # 2 of 10 non-excluded: no abort


def test_an_empty_or_all_excluded_universe_falls_back_without_aborting():
    themes = [theme("chips", ["A", "B"])]
    assert rank([], themes)[0].reason == ts.REASON_QUIET_WEEK
    assert rank([sig("A", news=None, size_ok=False)], themes)[0].reason == ts.REASON_QUIET_WEEK
    assert rank([], []) == []


def test_a_short_baseline_is_unscoreable_not_missing(caplog):
    short = [sig(f"NEW{i}", news=40, base=(1, 2)) for i in range(5)]
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        out = rank(short, [theme("ipo", ["NEW0", "NEW1"])])
    assert out[0].reason == ts.REASON_QUIET_WEEK    # no abort: the counts were fetched
    assert "too short a news baseline" in caplog.text


# ── quiet-week fallback ───────────────────────────────────────────────────────────────────────


def test_quiet_week_picks_never_covered_then_oldest_then_slug():
    themes = [
        theme("b-old", ["A"], last_covered="2026-01-02"),
        theme("a-old", ["A"], last_covered="2026-01-02"),
        theme("new", ["A"], last_covered="2026-10-01"),
        theme("zz-never", ["A"]),
        theme("yy-never", ["A"]),
    ]
    assert rank([], themes)[0].key == "yy-never"
    covered = [t for t in themes if t.last_covered]
    assert rank([], covered)[0].key == "a-old"
    out = rank([], covered, recent_themes=["a-old", "b-old"])
    assert [(c.key, c.score, c.reason, c.kind) for c in out] == [("new", 0.0, ts.REASON_QUIET_WEEK, "theme")]


def test_quiet_week_skips_ineligible_and_recent_themes_and_can_return_nothing():
    themes = [theme("never-but-ineligible", ["A"], eligible=False),
              theme("recent", ["A"], last_covered="2026-10-16"),
              theme("old", ["A"], last_covered="2025-01-01")]
    assert rank([], themes)[0].key == "old"            # never-covered but ineligible is skipped
    assert rank([], themes, recent_themes=["old"])[0].key == "recent"
    assert rank([], themes, recent_themes=["recent", "old"]) == []


def test_quiet_week_orders_an_unreadable_date_last(caplog):
    themes = [theme("garbage", ["A"], last_covered="last friday"),
              theme("bad-month", ["A"], last_covered="2026-13-40"),
              theme("dated", ["A"], last_covered="2026-10-16T05:00:00Z")]
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        assert rank([], themes)[0].key == "dated"
    assert "unreadable last_covered" in caplog.text
    assert rank([], themes[:2])[0].key == "bad-month"     # both unreadable → slug order


def test_quiet_week_carries_the_themes_scored_members_but_claims_no_score(fixed_scores):
    s = fixed(fixed_scores, {"A": 0.9, "B": 0.5, "C": -1.0, "D": 0.1})
    out = rank(s, [theme("chips", ["D", "C", "B", "A"])])
    assert out[0].reason == ts.REASON_QUIET_WEEK and out[0].score == 0.0
    assert out[0].tickers == ("A", "B", "D")


def test_quiet_week_fallback_without_scores_after_an_abort():
    out = ts.quiet_week_fallback([theme("chips", ["A", "B"])], recent_themes=[])
    assert out == [TopicCandidate(kind="theme", key="chips", title="Chips", score=0.0, tickers=(),
                                  components={"members_scored": 0.0}, reason=ts.REASON_QUIET_WEEK)]
    with pytest.raises(TypeError):
        ts.quiet_week_fallback([], recent_themes=None)


# ── order independence and determinism ────────────────────────────────────────────────────────


def _rich_universe():
    signals = [
        sig("NVDA", news=news_for(4.2), filings_8k_week=2, filings_8k_baseline=(0, 1, 0, 0, 1, 0, 0, 0),
            earnings_this_week=True, items_8k=("2.02",)),
        sig("AMD", news=news_for(2.6)), sig("AVGO", news=news_for(2.6)), sig("MU", news=news_for(1.1)),
        sig("VST", news=news_for(1.9)), sig("CEG", news=news_for(1.4)), sig("NRG", news=news_for(0.3)),
        sig("CRWV", news=news_for(3.6), news_saturated=True), sig("ORCL", news=news_for(3.6)),
        sig("MSFT", news=news_for(3.1)), sig("PLTR", news=news_for(2.95)),
        sig("TSM", news=99, foreign_filer=True), sig("SMH", news=99, excluded_reason="etf"),
        sig("TINY", news=99, size_ok=False), sig("IPO", news=50, base=(1, 2)),
        sig("GONE", news=None),
    ]
    themes = [
        theme("ai-chips", ["NVDA", "AMD", "AVGO", "MU", "TSM"], last_covered="2026-09-04"),
        theme("ai-power", ["VST", "CEG", "NRG", "NVDA"], last_covered="2026-08-01"),
        theme("cloud", ["MSFT", "ORCL", "CRWV"]),
        theme("old-news", ["AMD", "AVGO"], last_covered="2026-10-16"),
        theme("not-ready", ["NVDA", "AMD"], eligible=False),
    ]
    return signals, themes


def test_shuffling_every_input_fifty_times_never_changes_the_answer():
    signals, themes = _rich_universe()
    kwargs = dict(recent_themes=["old-news"], recent_companies=["MSFT"],
                  primed_tickers=_all_tickers(signals))
    expected = ts.rank_topics(signals, themes, **kwargs)
    assert expected and all(c.reason == "ranked" for c in expected)
    rng = random.Random(20261022)
    for _ in range(50):
        sh_signals = signals[:]
        rng.shuffle(sh_signals)
        sh_themes = []
        for t in themes:
            members = list(t.members)
            rng.shuffle(members)
            sh_themes.append(ThemeInput(t.slug, t.title, tuple(members), t.eligible, t.last_covered))
        rng.shuffle(sh_themes)
        recent_t, recent_c = list(kwargs["recent_themes"]), list(kwargs["recent_companies"])
        primed = list(kwargs["primed_tickers"])
        rng.shuffle(primed)
        assert ts.rank_topics(sh_signals, sh_themes, recent_themes=set(recent_t),
                              recent_companies=reversed(recent_c), primed_tickers=primed) == expected


def test_quiet_week_is_order_independent_too():
    themes = [theme(s, ["A"], last_covered=d) for s, d in
              (("c", None), ("a", None), ("b", "2026-01-01"), ("d", "x"))]
    rng = random.Random(7)
    for _ in range(50):
        sh = themes[:]
        rng.shuffle(sh)
        assert rank([], sh)[0].key == "a"


def test_the_rich_universe_ranks_as_designed():
    signals, themes = _rich_universe()
    kwargs = dict(recent_themes=["old-news"], recent_companies=["MSFT"],
                  primed_tickers=_all_tickers(signals))
    # Scores: NVDA 4.2 + 0.4·z_8k(2 vs a 0-median baseline) + 0.5 earnings ≈ 5.24; CRWV = ORCL = 3.6;
    # ai-chips = mean(NVDA, AMD, AVGO) ≈ 3.48; cloud = mean(ORCL, CRWV, MSFT) ≈ 3.43;
    # ai-power = mean(NVDA, VST, CEG) ≈ 2.85; PLTR 2.95 < 3; MSFT recent; old-news recent.
    nvda = ts.ticker_score(signals[0])[0]
    assert nvda == pytest.approx(4.2 + 0.4 * 2 / 1.4826 + 0.5)
    out = ts.rank_topics(signals, themes, **kwargs)
    # The three tickers fill the card before either theme is reached, so none is dropped.
    assert [(c.kind, c.key) for c in out] == [("ticker", "NVDA"), ("ticker", "CRWV"), ("ticker", "ORCL")]
    wide = ts.rank_topics(signals, themes, top_n=6, **kwargs)
    # With room, ai-chips claims NVDA and cloud claims CRWV (its top member on the 3.6 tie, ticker
    # asc); ai-power's top member NVDA is already claimed, so the theme itself is still offered.
    assert [(c.kind, c.key) for c in wide] == [("ticker", "ORCL"), ("theme", "ai-chips"),
                                                ("theme", "cloud"), ("theme", "ai-power")]
    assert wide[1].score == pytest.approx((nvda + 2.6 + 2.6) / 3)
    assert wide[2].tickers == ("CRWV", "ORCL", "MSFT")   # a recent company still lifts its theme
    for c in out + wide:
        assert math.isfinite(c.score)
        assert "IPO" not in c.tickers and "TSM" not in c.tickers and "GONE" not in c.tickers
        json.dumps(c.to_json())


# ── purity guard: stdlib + contracts only (the FMP boundary is plan-level) ───────────────────

_ALLOWED_IMPORTS = {"__future__", "logging", "math", "re", "statistics", "datetime", "typing",
                    "app.services.learn_weekly.contracts"}


def _imports(source: str) -> set:
    found = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            found.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            found.add(node.module or "")
    return found


def test_the_scorer_imports_only_the_stdlib_and_contracts():
    source = Path(ts.__file__).read_text(encoding="utf-8")
    assert _imports(source) <= _ALLOWED_IMPORTS, _imports(source) - _ALLOWED_IMPORTS
    # The guard is not vacuous: an FMP import (anywhere, even inside a function) is caught.
    for mutant in ("from app.integrations.fmp import FMPClient\n",
                   "def f():\n    import app.integrations.fmp\n"):
        assert not _imports(source + "\n" + mutant) <= _ALLOWED_IMPORTS
