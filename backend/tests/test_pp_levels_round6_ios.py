"""Round 6 (2026-10-08), PP4-LVL-FALLBACK: in a per-line (v7) Profit Power payload a missing
"<period>.<metric>" key means that line draws NO peer point, so iOS names no peer group for it.

Before: `toMarginSeries` read `levels["annual.<metric>"] ?? levels["annual"] ?? peerGroupLevel`,
stamped `peerLevel: peerGroupLevel` on every margin, and `ProfitPowerSectionData.peerWord(for:)`
read `levels["<period>"] ?? peerGroupLevel`. None of them could tell "v7 map, this line absent"
from "an older payload". So when a report froze with its annual FCF peer line blanked (the
collector pops `annual.fcf_margin` for exactly that reason), the drill-down's FCF Annual tab
read "Industry Average" — the NET line's group — over no dashed line; any margin whose peer
line was missing on a tab did the same; ROE/ROA inherited that word through the sheet's
`marginSeries.first?.peerLevel` fallback; and the live card's Quarterly tooltip, with no
quarterly peer net line, read "Industry Avg —" from the ANNUAL net line.

Now `ProfitPowerSectionData.hasPerLineLevels` decides, for both surfaces: a per-line map answers
ONLY with that line's own key (nil → no level → the neutral "Sector"), and the margins'
period-agnostic `peerLevel` is nil; only an older map (tab keys only, or none) falls back to the
tab key and then `peerGroupLevel` — `GrowthSectionData.peerWord`'s "map present → own key only".

No XCTest target and no Swift compiler in this session (testing.md §3): comments are stripped,
every read is brace-bound to ONE declaration, and the Swift is PORTED (refusing any shape the
port does not know) and run against case tables AND against what the REAL `ProfitPowerService`
and the REAL report narrowing (`_narrow_profit_power`) emit. The margin ports live in
tests/test_pp_levels_round4_ios.py (imported, so one path constant reaches both files).

Known remainder, outside these models: `ProfitabilityChartSheet.peerWord` still ends in
`?? card.peerGroupLevel` (the ticker-wide card level) and always draws the dashed-legend entry;
these tests read the sheet with no card level so they pin what the MODELS hand it.

Mutation reasoning (each run on a mutated COPY of the Swift, the round-4 path constants pointed
at it; an unmutated copy passes all 47 tests of both files):
* `peerWord(for:)`'s per-line branch given `?? peerGroupLevel` → the "no net line on this tab"
  rows and the live/quarterly-leg e2e tests fail (5);
* that branch deleted (the pre-fix one-line chain alone) → the port refuses the body (16);
* `hasPerLineLevels` as `!levels.isEmpty` → its port refuses the body (32); its separator
  changed to "_" — equivalent on every real map, since only the per-line keys hold "_" — is
  caught by test_the_per_line_marker_is_the_backends_key_separator, which pins the literal ".";
* `lineLevel`'s per-line return given `?? levels[tab]` (4) or `?? legacyLevel` (7), or its
  older branch losing `?? legacyLevel` (2) → the chain rows / e2e rows fail;
* `toMarginSeries` back to `peerLevel: peerGroupLevel` (6), `fallbackLevel`'s arms swapped (8),
  the round-4 chain restored or a `?? peerGroupLevel` appended to `lineLevel(...)` (20 each,
  the wiring port refuses them), or `.annual`/`.quarterly` swapped (8) → fail.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

import pytest

import test_pp_levels_round4_ios as r4


# ── the live card's word: `ProfitPowerSectionData.peerWord(for:)` ─────────────────


def _swift_pp_peer_word_for():
    """Port of `peerWord(for:)`, both branches, from the Swift text."""
    body = _flat_norm(r4._decl(
        r4._pp_section_data(),
        r"\bfunc\s+peerWord\s*\(\s*for\s+period:\s*ProfitPowerPeriodType\s*\)\s*->\s*String",
    ))
    m = re.fullmatch(
        r'\{ if Self\.hasPerLineLevels\(peerGroupLevels\) \{ '
        r'let own: String\? = (?P<v7>.+?) return own == "(?P<a1>\w+)" \? "(?P<b1>\w+)" : "(?P<c1>\w+)" \} '
        r'let level = (?P<old>.+?) return level == "(?P<a2>\w+)" \? "(?P<b2>\w+)" : "(?P<c2>\w+)" \}',
        body,
    )
    assert m, f"peerWord(for:) changed shape — re-port it: {body}"
    known = ("peerGroupLevels[Self.peerLevelKey(for: period)]", "peerGroupLevel")

    def terms(chain: str) -> List[str]:
        out = [t.strip() for t in chain.split("??")]
        for t in out:
            assert t in known, f"a term this port does not know: {t!r}"
        return out

    per_line_terms, older_terms = terms(m.group("v7")), terms(m.group("old"))
    key_of, per_line = r4._swift_pp_key_of(), r4._swift_has_per_line_levels()

    def word(levels: Dict[str, str], legacy: Optional[str], period: str) -> str:
        values = {known[0]: levels.get(key_of[period]), known[1]: legacy}
        branch = "1" if per_line(levels) else "2"
        chain = per_line_terms if branch == "1" else older_terms
        level = next((values[t] for t in chain if values[t] is not None), None)
        industry, yes, no = (m.group(g + branch) for g in ("a", "b", "c"))
        return yes if level == industry else no
    return word


def _flat_norm(text: str) -> str:
    return r4._norm_parens(r4._flat(text))


@pytest.mark.parametrize("levels, legacy, period, word", [
    # v7, both net lines drawn: each tab its own word.
    ({"annual": "industry", "annual.net_margin": "industry",
      "quarterly": "sector", "quarterly.net_margin": "sector"}, "industry", "annual", "Industry"),
    ({"annual": "industry", "annual.net_margin": "industry",
      "quarterly": "sector", "quarterly.net_margin": "sector"}, "industry", "quarterly", "Sector"),
    # v7, NO quarterly net line (another quarterly margin is drawn): no level, never the annual
    # word through `peer_group_level` — the "Industry Avg —" tooltip (PP4-LVL-FALLBACK).
    ({"annual": "industry", "annual.net_margin": "industry", "quarterly.gross_margin": "industry"},
     "industry", "quarterly", "Sector"),
    ({"annual": "industry", "annual.net_margin": "industry"}, "industry", "quarterly", "Sector"),
    # v7, no annual net line: peer_group_level is then the QUARTERLY net level — not the annual word.
    ({"quarterly": "industry", "quarterly.net_margin": "industry", "annual.fcf_margin": "industry"},
     "industry", "annual", "Sector"),
    ({"quarterly": "industry", "quarterly.net_margin": "industry"}, "industry", "quarterly", "Industry"),
    # Older payloads keep today's chain: the tab key, then the payload-wide level.
    ({"annual": "industry"}, "industry", "quarterly", "Industry"),
    ({"annual": "industry", "quarterly": "sector"}, "industry", "quarterly", "Sector"),
    ({}, "industry", "annual", "Industry"),
    ({}, None, "quarterly", "Sector"),
    # An unknown word is never "Industry".
    ({"annual": "market", "annual.net_margin": "market"}, "industry", "annual", "Sector"),
])
def test_the_live_cards_word_names_no_undrawn_line(levels, legacy, period, word):
    assert _swift_pp_peer_word_for()(levels, legacy, period) == word


def test_the_per_line_marker_is_the_backends_key_separator():
    """`hasPerLineLevels` looks for the separator the backend writes between period and
    metric, and `lineLevel` joins with the same one."""
    data = r4._pp_section_data()
    flat = r4._flat(data)
    assert re.search(r'\$0\.contains\("\."\)', flat), "hasPerLineLevels no longer looks for '.'"
    assert re.search(r'let own: String = tab \+ "\." \+ metric', flat), "lineLevel no longer joins with '.'"
    src = r4._PP_SERVICE.read_text()
    assert 'peer_group_levels[f"{series}.{metric}"] = level' in src, "the backend's per-line key moved"
    # The live card reads the TAB key in a per-line map: the backend writes it only from the
    # headline (net-margin) line, the one the card draws.
    assert re.search(r'^_HEADLINE_PEER_METRIC\s*=\s*"net_margin"\s*$', src, re.M)
    assert 'headline = peer_group_levels.get(f"{series}.{_HEADLINE_PEER_METRIC}")' in src
    assert "peer_group_levels[series] = headline" in src


def test_the_sheet_hands_roe_and_roa_the_margins_fallback():
    """The ROE/ROA rows below rely on this wiring (also pinned by round 4)."""
    init = r4._flat(r4._decl(r4._sheet(), r"\binit\s*\(\s*card:\s*DeepDiveMetricCard\s*,"))
    assert "let fallbackLevel: String? = marginSeries.first?.peerLevel" in init
    roe = r4._flat(r4._roe_mapper())
    assert re.search(r"peerLevel: peerLevel, annualPeerLevel: sectorAnnualLevel, "
                     r"quarterlyPeerLevel: sectorQuarterlyLevel \)", roe), roe


# ── end to end: the REAL backend's maps, read through the Swift ports ─────────────


_YEARS, _QUARTERS = ("2024", "2025"), ("Q4'25", "Q1'26")
_METRICS = ("net_margin", "gross_margin", "operating_margin", "fcf_margin")


async def _build(monkeypatch, annual: Dict[str, Any], quarterly: Dict[str, Any]):
    from app.services import profit_power_service as pp
    from app.services.sector_benchmark_lookup import CALENDAR_QUARTER_PERIOD_TYPE as CQ

    monkeypatch.setattr(pp, "get_sector_benchmark_lookup",
                        lambda: r4._FakeLookup({"annual": annual, CQ: quarterly}))
    row = {"period": "FY", "revenue": 100.0, "grossProfit": 50.0, "operatingIncome": 20.0, "netIncome": 10.0}
    svc = pp.ProfitPowerService.__new__(pp.ProfitPowerService)
    svc.fmp = r4._FakeFMP(**{
        "get_company_profile": {"symbol": "ZZZ", "sector": "Technology", "industry": "Solar"},
        "get_income_statement:annual": [{**row, "date": "2024-12-31", "fiscalYear": "2024"},
                                        {**row, "date": "2025-12-31", "fiscalYear": "2025"}],
        "get_income_statement:quarter": [{**row, "period": "Q4", "date": "2025-12-31", "fiscalYear": "2025"},
                                         {**row, "period": "Q1", "date": "2026-03-31", "fiscalYear": "2026"}],
    })
    svc.supabase = None
    resp, _n, degraded = await svc._build_profit_power("ZZZ")
    assert degraded == []
    return resp


def _drawn(wire: Dict[str, Any], period: str, metric: str) -> bool:
    return any(p.get(f"sector_average_{metric}") is not None for p in wire[period] or [])


def _assert_keys_follow_the_drawn_lines(wire: Dict[str, Any]) -> None:
    """The contract the Swift relies on: a per-line key exists exactly for a drawn peer line,
    and the tab key is the drawn NET line's."""
    levels = wire["peer_group_levels"]
    for period in ("annual", "quarterly"):
        for metric in _METRICS:
            assert (f"{period}.{metric}" in levels) == _drawn(wire, period, metric), (period, metric, levels)
        assert levels.get(period) == levels.get(f"{period}.net_margin"), (period, levels)


def _words(wire: Dict[str, Any]):
    """(margin series by key, the sheet's word, the live card's word) for one wire payload."""
    levels, legacy = wire["peer_group_levels"], wire["peer_group_level"]
    margin_series = r4._swift_margin_series()
    series = {key: margin_series(levels, legacy, key) for key in r4._make_calls()}
    sheet_word, card_word = r4._swift_peer_word(), _swift_pp_peer_word_for()
    return series, sheet_word, (lambda period: card_word(levels, legacy, period))


@pytest.mark.asyncio
async def test_a_live_v7_payload_names_no_undrawn_line(monkeypatch):
    """Annual: net/gross/operating are industry lines, FCF has NO peer cells. Quarterly: only
    gross has a (sector) line. Every undrawn chip × tab gets no level."""
    annual = {m: r4._line("industry", _YEARS) for m in ("net_margin", "gross_margin", "operating_margin")}
    quarterly = {"gross_margin": r4._line("sector", _QUARTERS)}
    wire = (await _build(monkeypatch, annual, quarterly)).model_dump(mode="json")
    _assert_keys_follow_the_drawn_lines(wire)
    assert wire["peer_group_level"] == "industry"
    assert "annual.fcf_margin" not in wire["peer_group_levels"]
    assert "quarterly" not in wire["peer_group_levels"]

    series, sheet_word, card_word = _words(wire)
    for key, s in series.items():
        assert s["peerLevel"] is None, (key, s)
        want_annual = None if key == "fcf_margin" else "industry"
        want_quarterly = "sector" if key == "gross_margin" else None
        assert s["annualPeerLevel"] == want_annual, (key, s)
        assert s["quarterlyPeerLevel"] == want_quarterly, (key, s)
    # The drill-down: the FCF Annual tab (no dashed line) no longer reads "Industry Average".
    assert sheet_word(series["fcf_margin"], None, "annual") == "Sector"
    assert sheet_word(series["net_margin"], None, "annual") == "Industry"
    assert sheet_word(series["gross_margin"], None, "quarterly") == "Sector"
    # The live card: the Quarterly tab has no peer net line → no annual word in its tooltip.
    assert card_word("annual") == "Industry"
    assert card_word("quarterly") == "Sector"

    # ROE/ROA: the sheet hands them `marginSeries.first?.peerLevel` — nil in a v7 payload, so a
    # ROE tab without its own level no longer borrows the annual NET line's word.
    roe = {"peerLevel": next(iter(series.values()))["peerLevel"],
           "annualPeerLevel": "industry", "quarterlyPeerLevel": None}
    assert sheet_word(roe, None, "annual") == "Industry"
    assert sheet_word(roe, None, "quarterly") == "Sector"


async def _full_build(monkeypatch):
    """Every margin line drawn on both tabs: industry annual lines, a sector FCF annual line,
    sector quarterly lines (the round-4 fixture)."""
    annual = {m: r4._line("industry", _YEARS) for m in ("net_margin", "gross_margin", "operating_margin")}
    annual["fcf_margin"] = r4._line("sector", _YEARS)
    quarterly = {m: r4._line("sector", _QUARTERS) for m in _METRICS}
    return await _build(monkeypatch, annual, quarterly)


@pytest.mark.asyncio
async def test_a_frozen_report_with_a_failed_annual_cashflow_leg(monkeypatch):
    """The finding's scenario: the annual FCF line is blanked and `annual.fcf_margin` popped
    while the net line is industry. The FCF Annual tab must name nothing."""
    from app.services.agents.ticker_report_data_collector import _narrow_profit_power

    narrowed = _narrow_profit_power(await _full_build(monkeypatch), ["annual_cashflow"])
    assert narrowed is not None
    wire = narrowed.model_dump(mode="json")
    _assert_keys_follow_the_drawn_lines(wire)
    assert "annual.fcf_margin" not in wire["peer_group_levels"]
    assert wire["peer_group_levels"]["annual"] == "industry"

    series, sheet_word, _card = _words(wire)
    fcf = series["fcf_margin"]
    assert fcf == {"peerLevel": None, "annualPeerLevel": None, "quarterlyPeerLevel": "sector"}, fcf
    assert sheet_word(fcf, None, "annual") == "Sector"           # was "Industry" (the net line's)
    assert sheet_word(fcf, None, "quarterly") == "Sector"
    assert series["net_margin"]["annualPeerLevel"] == "industry"
    assert sheet_word(series["net_margin"], None, "annual") == "Industry"


@pytest.mark.asyncio
@pytest.mark.parametrize("blocking, period", [
    (["quarterly_income"], "quarterly"),     # the quarterly list emptied: every quarterly key popped
    (["quarterly_cashflow"], "quarterly"),   # only the quarterly FCF line blanked
])
async def test_a_frozen_report_with_a_failed_quarterly_leg(monkeypatch, blocking, period):
    from app.services.agents.ticker_report_data_collector import _narrow_profit_power

    narrowed = _narrow_profit_power(await _full_build(monkeypatch), blocking)
    assert narrowed is not None
    wire = narrowed.model_dump(mode="json")
    _assert_keys_follow_the_drawn_lines(wire)
    series, sheet_word, card_word = _words(wire)
    emptied = blocking == ["quarterly_income"]
    for key, s in series.items():
        assert s["peerLevel"] is None, (key, s)
        gone = emptied or key == "fcf_margin"
        assert s["quarterlyPeerLevel"] == (None if gone else "sector"), (key, s)
        assert s["annualPeerLevel"] == ("sector" if key == "fcf_margin" else "industry"), (key, s)
    assert sheet_word(series["fcf_margin"], None, period) == "Sector"
    # The live card's Quarterly tab: no net line once the list is emptied; it is still a
    # sector line when only FCF was blanked.
    assert card_word("quarterly") == "Sector"
    assert card_word("annual") == "Industry"
    assert wire["peer_group_level"] == "industry"


@pytest.mark.asyncio
async def test_a_stripped_report_falls_back_to_no_word_at_all(monkeypatch):
    """A profile/benchmark failure strips every peer: an empty map and no payload-wide level,
    so the older-payload branch has nothing to fall back to either."""
    from app.services.agents.ticker_report_data_collector import _narrow_profit_power

    narrowed = _narrow_profit_power(await _full_build(monkeypatch), ["benchmarks"])
    assert narrowed is not None
    wire = narrowed.model_dump(mode="json")
    assert wire["peer_group_levels"] == {} and wire["peer_group_level"] is None
    series, sheet_word, card_word = _words(wire)
    for key, s in series.items():
        assert s == {"peerLevel": None, "annualPeerLevel": None, "quarterlyPeerLevel": None}, (key, s)
        assert sheet_word(s, None, "annual") == "Sector"
    assert card_word("annual") == card_word("quarterly") == "Sector"
