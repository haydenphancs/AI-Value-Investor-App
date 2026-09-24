"""`_whale_common.diff_13f_positions` — quarter-over-quarter SHARE changes by CUSIP.

Part 1 replays the four real 2026-Q2-vs-Q1 corporate 13Fs (NVIDIA, Alphabet, Amazon, AMD;
FMP rows fixtured on 2026-09-24) through `normalize_rows` -> the diff, and pins what the
research established by hand. Part 2 is the strange input: put/call and PRN rows, NaN / 0
/ negative numbers, empty sides, a gap, duplicate CUSIPs, a CUSIP re-key, forward and
reverse splits, the magnitude backstop. Hermetic: pure functions and fixture files.
"""
from __future__ import annotations

import copy
import json
import logging
import math
from datetime import date
from pathlib import Path

import pytest

from app.schemas.trillion_club import CHANGE_KINDS
from app.services._whale_common import diff_13f_positions
from app.services.trillion_club import rules
from app.services.trillion_club.builder import normalize_rows

FIX = Path(__file__).parent / "fixtures" / "trillion_club"
EXTRACTS = json.loads((FIX / "extracts_2026.json").read_text())
PROFILES = json.loads((FIX / "profiles.json").read_text())
SEARCH = json.loads((FIX / "search.json").read_text())

NVIDIA, ALPHABET, AMAZON, AMD = "0001045810", "0001652044", "0001018724", "0000002488"
Q1_END = date(2026, 3, 31)


def _holdings(cik: str, period: str):
    """Fixture extract -> normalised rows with the symbol / ipo_date / weight the builder
    would attach (null symbols resolved the way the builder does, via search-isin)."""
    y, q = rules.parse_period(period)
    norm = normalize_rows(EXTRACTS[cik][period], expected_period_end=rules.quarter_end(y, q))
    total = sum(r["value"] for r in norm.rows)
    out = []
    for r in norm.rows:
        sym = r["symbol"]
        if sym is None:
            hits = SEARCH["search_isin"].get(rules.cusip_to_us_isin(r["cusip"]) or "", [])
            sym = hits[0]["symbol"] if len(hits) == 1 else None
        prof = PROFILES.get(sym) or {}
        out.append({**r, "symbol": sym, "ipo_date": prof.get("ipoDate"), "weight": r["value"] / total})
    return out


def _diff(cik: str, **kw):
    return diff_13f_positions(
        _holdings(cik, "2026-Q2"), _holdings(cik, "2026-Q1"),
        split_ratios=kw.pop("split_ratios", {}), unclassified=kw.pop("unclassified", set()),
        comparison="quarter", prev_ipo_cutoff=Q1_END, prev_period="2026-Q1",
    )


def _by(changes, key="symbol"):
    return {r[key]: r for r in changes["rows"]}


# ── Part 1: the four real filings ───────────────────────────────────────────────────


def test_nvidia_q2_spacex_is_newly_reported_and_newly_listed_the_rest_unchanged():
    h = _holdings(NVIDIA, "2026-Q2")
    assert len(h) == 8
    assert sum(x["value"] for x in h) == 63_439_974_569
    ch = _diff(NVIDIA)
    assert ch["comparison"] == "quarter" and ch["prev_period"] == "2026-Q1"
    assert ch["counts"] == {"newly_reported": 1, "increased": 0, "decreased": 0,
                            "no_longer_reported": 0, "unchanged": 7, "corporate_action": 0}
    (spcx,) = ch["rows"]
    assert spcx["symbol"] == "SPCX" and spcx["change"] == "newly_reported"
    assert spcx["newly_listed"] is True, "SpaceX listed 2026-06-12, after Q1 ended"
    assert spcx["shares"] == 122_764_805 and spcx["prev_shares"] is None
    assert spcx["share_change"] is None
    assert spcx["weight"] == pytest.approx(0.3306, abs=1e-4)


def test_alphabet_spacex_newly_listed_and_ethos_resolves_to_life():
    ch = _diff(ALPHABET)
    rows = _by(ch)
    assert rows["SPCX"]["change"] == "newly_reported" and rows["SPCX"]["newly_listed"] is True
    # Ethos has NO symbol in FMP's extract (CUSIP 29765A101); search-isin gives LIFE, and
    # keyed by CUSIP it is one position across both quarters: a decrease, not an exit+entry.
    assert rows["LIFE"]["change"] == "decreased" and rows["LIFE"]["cusip"] == "29765A101"
    assert rows["LIFE"]["share_change"] == 3_622_604 - 3_770_156
    assert {s for s, r in rows.items() if r["change"] == "newly_reported"} == {"SPCX", "PBLS", "FRVO"}
    assert {s for s, r in rows.items() if r["change"] == "decreased"} == {"RVMD", "RLAY", "GLUE", "LIFE"}
    assert {s for s, r in rows.items() if r["change"] == "no_longer_reported"} == {"TNYA"}
    assert ch["counts"]["unchanged"] == 21
    assert rows["RVMD"]["share_change"] == 2_946_619 - 3_292_525


def test_amazon_xe_and_algt_newly_reported_naut_no_longer_reported():
    ch = _diff(AMAZON)
    rows = _by(ch)
    assert {s for s, r in rows.items() if r["change"] == "newly_reported"} == {"XE", "ALGT"}
    assert rows["XE"]["newly_listed"] is True, "X-Energy listed 2026-04-24"
    assert rows["ALGT"]["newly_listed"] is False, "Allegiant listed in 2006 — a real new position"
    naut = rows["NAUT"]
    assert naut["change"] == "no_longer_reported"
    assert (naut["shares"], naut["value"], naut["weight"], naut["share_change"]) == (None, None, None, None)
    assert naut["prev_shares"] == 1_457_055
    assert ch["counts"]["unchanged"] == 4


def test_amd_spcx_ntnx_cbrs_newly_reported_mrvl_no_longer_reported():
    ch = _diff(AMD)
    rows = _by(ch)
    assert {s for s, r in rows.items() if r["change"] == "newly_reported"} == {"SPCX", "NTNX", "CBRS"}
    assert rows["SPCX"]["newly_listed"] and rows["CBRS"]["newly_listed"]
    assert rows["NTNX"]["newly_listed"] is False
    assert {s for s, r in rows.items() if r["change"] == "no_longer_reported"} == {"MRVL"}


@pytest.mark.parametrize("cik", [NVIDIA, ALPHABET, AMAZON, AMD])
def test_weights_are_fractions_summing_to_one(cik):
    h = _holdings(cik, "2026-Q2")
    assert math.fsum(x["weight"] for x in h) == pytest.approx(1.0, abs=1e-12)
    assert all(0 < x["weight"] <= 1 for x in h)


@pytest.mark.parametrize("cik", [NVIDIA, ALPHABET, AMAZON, AMD])
def test_rows_never_list_unchanged_and_counts_cover_every_cusip(cik):
    ch = _diff(cik)
    assert all(r["change"] != "unchanged" for r in ch["rows"])
    cusips = {r["cusip"] for r in _holdings(cik, "2026-Q2")} | {r["cusip"] for r in _holdings(cik, "2026-Q1")}
    assert sum(ch["counts"].values()) == len(cusips)
    assert set(ch["counts"]) == set(CHANGE_KINDS)
    json.dumps(ch, allow_nan=False)


def test_rows_are_ordered_by_kind_then_value():
    rows = _diff(ALPHABET)["rows"]
    order = ["newly_reported", "increased", "decreased", "no_longer_reported", "corporate_action"]
    kinds = [order.index(r["change"]) for r in rows]
    assert kinds == sorted(kinds)
    new_values = [r["value"] for r in rows if r["change"] == "newly_reported"]
    assert new_values == sorted(new_values, reverse=True)


# ── Part 2: strange input ────────────────────────────────────────────────────────────


def _h(cusip, shares, *, symbol=None, value=None, ipo=None, weight=None, name="X"):
    return {"cusip": cusip, "symbol": symbol, "name": name, "shares": shares,
            "value": value if value is not None else shares * 10.0, "weight": weight,
            "ipo_date": ipo}


def _q(curr, prev, **kw):
    return diff_13f_positions(
        curr, prev, split_ratios=kw.get("split_ratios", {}),
        unclassified=kw.get("unclassified", set()), comparison=kw.get("comparison", "quarter"),
        prev_ipo_cutoff=kw.get("cutoff", Q1_END), prev_period=kw.get("prev_period", "2026-Q1"),
    )


def test_first_filing_and_gap_list_nothing():
    curr = [_h("458140100", 100.0, symbol="INTC")]
    first = _q(curr, None, comparison="first_filing", prev_period="2026-Q1")
    assert first["rows"] == [] and set(first["counts"].values()) == {0}
    assert first["prev_period"] is None
    gap = _q(curr, [_h("458140100", 1.0)], comparison="gap", prev_period="2025-Q4")
    assert gap["rows"] == [] and set(gap["counts"].values()) == {0}
    assert gap["prev_period"] == "2025-Q4" and gap["comparison"] == "gap"


def test_a_gap_is_not_compared_so_its_zero_counts_are_not_no_changes():
    """The contract the READ path depends on (correctness-gap-reads-as-no-change /
    resilience-2): for ``comparison='gap'`` the diff returns ``rows == []`` and ALL SIX
    counts 0 even when the two books plainly differ — 0 here means NOT COMPARED. The same
    books diffed as an adjacent quarter show the real changes. A reader must therefore key
    off ``comparison`` and never render a gap's counts as "no share-count changes" or label
    its holdings "unchanged" (the service owns that display)."""
    curr = [_h("458140100", 300.0, symbol="INTC"), _h("21873S108", 50.0, symbol="CRWV")]
    prev = [_h("458140100", 100.0, symbol="INTC"), _h("64110L106", 10.0, symbol="NFLX")]
    compared = _q(curr, prev, comparison="quarter")
    assert compared["counts"]["increased"] == 1 and compared["counts"]["newly_reported"] == 1
    assert compared["counts"]["no_longer_reported"] == 1
    for prev_period in ("2025-Q3", None):              # jobs pass only "an older one exists"
        gap = _q(curr, prev, comparison="gap", prev_period=prev_period)
        assert gap == {"comparison": "gap", "prev_period": prev_period,
                       "counts": {k: 0 for k in ("newly_reported", "increased", "decreased",
                                                 "no_longer_reported", "unchanged",
                                                 "corporate_action")},
                       "rows": []}
        assert gap["counts"]["unchanged"] == 0, "not even 'unchanged': nothing was compared"
    assert _q(curr, None, comparison="gap")["rows"] == [], "a gap never reads its prev side"


@pytest.mark.parametrize("prev", [None, [], [_h("458140100", float("nan"))], ["junk"]])
def test_a_quarter_diff_without_a_usable_previous_filing_raises(prev):
    """An empty previous side would book the whole book as newly reported."""
    with pytest.raises(ValueError):
        _q([_h("458140100", 100.0)], prev)


def test_a_quarter_diff_without_a_usable_current_filing_raises():
    with pytest.raises(ValueError):
        _q([], [_h("458140100", 100.0)])


def test_unknown_comparison_raises():
    with pytest.raises(ValueError):
        _q([_h("458140100", 1.0)], [_h("458140100", 1.0)], comparison="annual")


def test_nan_zero_negative_and_missing_shares_are_skipped(caplog):
    curr = [_h("458140100", 100.0, symbol="INTC"), _h("111111118", float("nan")),
            _h("222222226", 0.0), _h("333333334", -5.0), {"cusip": "444444442"},
            {"cusip": None, "shares": 5.0}, _h("bad", 5.0), "junk", None]
    prev = [_h("458140100", 100.0, symbol="INTC"), _h("555555550", float("inf"))]
    with caplog.at_level(logging.WARNING):
        ch = _q(curr, prev)
    assert ch["counts"]["unchanged"] == 1 and ch["rows"] == []
    assert sum(ch["counts"].values()) == 1
    assert "skipped 8 current row(s)" in caplog.text and "skipped 1 previous row(s)" in caplog.text


def test_nan_values_and_weights_never_reach_the_output():
    curr = [_h("458140100", 150.0, symbol="INTC", value=float("nan"), weight=float("inf"))]
    ch = _q(curr, [_h("458140100", 100.0, symbol="INTC")])
    (row,) = ch["rows"]
    assert row["value"] is None and row["weight"] is None and row["change"] == "increased"
    json.dumps(ch, allow_nan=False)


def test_duplicate_cusips_are_summed(caplog):
    curr = [_h("458140100", 60.0, symbol="INTC"), _h("458140100", 40.0, symbol="INTC")]
    with caplog.at_level(logging.WARNING):
        ch = _q(curr, [_h("458140100", 100.0, symbol="INTC")])
    assert ch["counts"]["unchanged"] == 1
    assert "duplicate CUSIP" in caplog.text


def test_a_cusip_rekey_with_the_same_symbol_is_one_position():
    prev = [_h("111111118", 100.0, symbol="ABC"), _h("458140100", 5.0, symbol="INTC")]
    same = _q([_h("222222226", 100.0, symbol="ABC"), _h("458140100", 5.0, symbol="INTC")], prev)
    assert same["rows"] == [] and same["counts"]["unchanged"] == 2
    assert same["counts"]["newly_reported"] == same["counts"]["no_longer_reported"] == 0
    more = _q([_h("222222226", 130.0, symbol="ABC"), _h("458140100", 5.0, symbol="INTC")], prev)
    (row,) = more["rows"]
    assert row["change"] == "increased" and row["cusip"] == "222222226" and row["share_change"] == 30.0


def test_an_ambiguous_rekey_is_not_joined(caplog):
    prev = [_h("111111118", 100.0, symbol="ABC")]
    curr = [_h("222222226", 60.0, symbol="ABC"), _h("333333334", 40.0, symbol="ABC")]
    with caplog.at_level(logging.WARNING):
        ch = _q(curr, prev)
    assert ch["counts"]["newly_reported"] == 2 and ch["counts"]["no_longer_reported"] == 1
    assert "ambiguous" in caplog.text


def test_a_symbol_less_new_and_gone_pair_is_never_joined():
    ch = _q([_h("222222226", 100.0)], [_h("111111118", 100.0)])
    assert ch["counts"]["newly_reported"] == 1 and ch["counts"]["no_longer_reported"] == 1


def test_a_forward_split_is_restated_not_a_ninefold_increase():
    prev = [_h("67066G104", 100_000.0, symbol="NVDA")]
    held = _q([_h("67066G104", 1_000_000.0, symbol="NVDA")], prev, split_ratios={"NVDA": 10.0})
    assert held["counts"]["unchanged"] == 1 and held["rows"] == []
    bought = _q([_h("67066G104", 1_020_000.0, symbol="NVDA")], prev, split_ratios={"NVDA": 10.0})
    (row,) = bought["rows"]
    assert row["change"] == "increased" and row["prev_shares"] == 1_000_000.0
    assert row["share_change"] == 20_000.0
    raw = _q([_h("67066G104", 1_000_000.0, symbol="NVDA")], prev)
    assert raw["rows"][0]["change"] == "increased", "control: no ratio -> the raw diff"


def test_a_reverse_split_is_restated():
    prev = [_h("482480100", 1_000_000.0, symbol="KLAC")]
    ch = _q([_h("482480100", 95_000.0, symbol="KLAC")], prev, split_ratios={"KLAC": 0.1})
    (row,) = ch["rows"]
    assert row["change"] == "decreased" and row["prev_shares"] == pytest.approx(100_000.0)


def test_a_split_tangled_with_real_flow_is_a_corporate_action():
    """10:1 with ratio_obs 7 — neither "held" nor "raw": SPLIT_SUPPRESS, never a guess."""
    ch = _q([_h("67066G104", 700.0, symbol="NVDA")], [_h("67066G104", 100.0, symbol="NVDA")],
            split_ratios={"NVDA": 10.0})
    (row,) = ch["rows"]
    assert row["change"] == "corporate_action" and row["share_change"] is None


def test_fractional_residual_after_a_split_is_unchanged():
    ch = _q([_h("111111118", 150_001.0, symbol="ABC")], [_h("111111118", 100_001.0, symbol="ABC")],
            split_ratios={"ABC": 1.5})
    assert ch["counts"]["unchanged"] == 1


def test_the_magnitude_backstop_needs_an_unclassified_action():
    prev = [_h("111111118", 100.0, symbol="ABC")]
    curr = [_h("111111118", 300.0, symbol="ABC")]
    assert _q(curr, prev)["rows"][0]["change"] == "increased"
    flagged = _q(curr, prev, unclassified={"ABC"})
    assert flagged["rows"][0]["change"] == "corporate_action"
    small = _q([_h("111111118", 110.0, symbol="ABC")], prev, unclassified={"ABC"})
    assert small["rows"][0]["change"] == "increased", "a plausible move is not suppressed"


@pytest.mark.parametrize("ratio", [float("nan"), float("inf"), -10.0, 0.0, 1.0, "10", None])
def test_junk_split_ratios_are_ignored(ratio):
    ch = _q([_h("111111118", 1000.0, symbol="ABC")], [_h("111111118", 100.0, symbol="ABC")],
            split_ratios={"ABC": ratio})
    expected = "unchanged" if ratio == "10" else "increased"   # "10" parses as a real 10:1
    assert (ch["rows"][0]["change"] if ch["rows"] else "unchanged") == expected


def test_split_ratio_is_found_through_the_previous_symbol():
    ch = _q([_h("111111118", 1000.0, symbol="NEW")], [_h("111111118", 100.0, symbol="OLD")],
            split_ratios={"OLD": 10.0})
    assert ch["counts"]["unchanged"] == 1


@pytest.mark.parametrize("ipo, cutoff, expected", [
    ("2026-06-12", Q1_END, True),
    ("2026-03-31", Q1_END, False),        # listed ON the previous period end: already listed
    ("2006-12-08", Q1_END, False),
    (None, Q1_END, False),                 # unknown listing date -> never implies a listing
    ("not-a-date", Q1_END, False),
    ("2026-06-12", None, False),           # no cutoff -> never
    (date(2026, 5, 1), Q1_END, True),
])
def test_newly_listed_only_when_the_listing_is_known_and_after_the_cutoff(ipo, cutoff, expected):
    ch = _q([_h("84615Q103", 5.0, symbol="SPCX", ipo=ipo), _h("458140100", 1.0, symbol="INTC")],
            [_h("458140100", 1.0, symbol="INTC")], cutoff=cutoff)
    assert _by(ch)["SPCX"]["newly_listed"] is expected


def test_newly_listed_is_never_set_on_other_changes():
    ch = _q([_h("84615Q103", 10.0, symbol="SPCX", ipo="2026-06-12")],
            [_h("84615Q103", 5.0, symbol="SPCX", ipo="2026-06-12")])
    assert ch["rows"][0]["change"] == "increased" and ch["rows"][0]["newly_listed"] is False


def test_the_inputs_are_not_mutated():
    curr = [_h("458140100", 60.0, symbol="INTC"), _h("458140100", 40.0, symbol="INTC")]
    prev = [_h("458140100", 90.0, symbol="INTC")]
    snap = copy.deepcopy((curr, prev))
    _q(curr, prev)
    assert (curr, prev) == snap


# ── the normalisation that feeds the diff ─────────────────────────────────────────────


def _raw(cusip, shares, value, *, symbol="ABC", put_call="", shares_type="SH", link_acc="000104581026000065",
         filed="2026-08-14", period="2026-06-30", name="ABC CORP"):
    return {"securityCusip": cusip, "shares": shares, "value": value, "symbol": symbol,
            "putCallShare": put_call, "sharesType": shares_type, "nameOfIssuer": name,
            "titleOfClass": "COM", "date": period, "filingDate": filed, "acceptedDate": filed,
            "cik": "0001045810",
            "link": f"https://www.sec.gov/Archives/edgar/data/1045810/{link_acc}/x-index.htm"}


def test_put_call_and_prn_rows_are_excluded_and_counted():
    raw = [_raw("458140100", 100, 1000), _raw("458140100", 50, 500, put_call="Put"),
           _raw("458140100", 50, 500, put_call="CALL"), _raw("111111118", 1_000_000, 990_000, shares_type="PRN"),
           _raw("222222226", 10, 100, shares_type=None)]
    norm = normalize_rows(raw)
    assert [(r["cusip"], r["shares"]) for r in norm.rows] == [("458140100", 100.0)]
    assert norm.excluded_rows == 4


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), 0, -1, None, "x", True])
def test_non_finite_zero_or_negative_numbers_are_excluded(bad):
    norm = normalize_rows([_raw("458140100", 100, 1000), _raw("111111118", 100, bad), _raw("222222226", bad, 100)])
    assert [r["cusip"] for r in norm.rows] == ["458140100"] and norm.excluded_rows == 2


def test_empty_and_junk_input_normalise_to_nothing():
    for raw in ([], None, ["junk", 3, None]):
        norm = normalize_rows(raw)
        assert norm.rows == [] and norm.accessions == []
    assert normalize_rows(["junk", 3]).excluded_rows == 2
