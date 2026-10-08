"""Emerging Frontiers theme detail — the Free cut is a SERVER gate.

Owner request (TestFlight 1.0 (11), 2026-10-04): "For Free tier, show the top 5 only. Blur
the rest (with clickable to upgrade to Pro/Max to see all)."

The blur is iOS presentation over placeholder text. What these tests pin is the gate behind
it (`services/theme_detail_redaction.py`, applied by `GET /home/themes/{slug}`):

1. Entitlements: Pro and Max see every company; Free, a missing tier and any unrecognised
   tier see the first `THEME_FREE_COMPANY_LIMIT` — the unknown case falls CLOSED, like every
   paid gate in `entitlements.py` (only the Journey's narration fails open, by design).
2. The cut at every boundary — zero, fewer than, exactly, one over, many over the limit; a
   limit of 0 or below — and the counts iOS draws its blurred rows from.
3. No withheld company reaches a Free caller through ANY field: the list, "What changed this
   month", the "Why it's moving" chips and prose, the news — scanned leaf by leaf. The prose
   ships only when it provably names no withheld company (fail closed); otherwise it becomes
   the fixed locked wording. A visible company's news headline that mentions a hidden one in
   passing is the one accepted residual.
4. The shared 10-minute cache is never edited: Free then Pro on one cache, no bleed.
5. The endpoint reads the tier from the users row and fails CLOSED when redaction itself
   breaks (a structured error, never the unredacted list).
6. Contract: the four new fields are defaulted (an already-shipped build decodes the
   response unchanged), and a worst-case locked payload survives JSON and re-validates.

No network, no Supabase: rows, quotes, the review, insights and news are injected.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

import pytest
from fastapi.responses import JSONResponse

import app.services.home_dashboard_service as hds
from app.api.v1.endpoints import home as home_ep
from app.schemas.themes_detail import (
    ThemeChangeResponse,
    ThemeConstituentResponse,
    ThemeDetailResponse,
    ThemeInsightResponse,
    ThemeNewsItemResponse,
    ThemePerformanceResponse,
    ThemePeriodReturnResponse,
)
from app.services import entitlements as ent
from app.services import theme_detail_redaction as redaction
from app.services.home_dashboard_service import HomeDashboardService
from app.services.theme_detail_redaction import (
    THEME_DETAIL_REDACTION_POLICY,
    redact_theme_detail,
)
from app.services.theme_rotation.read_model import LatestReview, ThemeChange, ThemeReview
from _price_fakes import PriceFromFMPFake

LIMIT = ent.THEME_FREE_COMPANY_LIMIT
SLUG = "the-new-oil"

# The New Oil as the TestFlight screenshot showed it: largest market cap first.
_COMPANIES = [
    ("SCCO", "Southern Copper", 160e9),
    ("RIO", "Rio Tinto Group", 150e9),
    ("FCX", "Freeport-McMoRan", 100e9),
    ("CCJ", "Cameco", 37e9),
    ("TECK", "Teck Resources", 33e9),
    ("SQM", "Sociedad Quimica y Minera", 18e9),
    ("ALB", "Albemarle", 12e9),
    ("HBM", "Hudbay Minerals", 10e9),
    ("MP", "MP Materials", 8e9),
    ("NXE", "NexGen Energy", 5e9),
    ("UEC", "Uranium Energy", 4e9),
    ("SGML", "Sigma Lithium", 1e9),
]
ALL = [t for t, _, _ in _COMPANIES]
VISIBLE = ALL[:LIMIT]
HIDDEN = ALL[LIMIT:]
HIDDEN_NAMES = [n for _, n, _ in _COMPANIES[LIMIT:]]


def _rows(n: int) -> List[ThemeConstituentResponse]:
    return [ThemeConstituentResponse(ticker=t, company_name=name, price=50.0,
                                     change_percent=1.25, market_cap=cap)
            for t, name, cap in _COMPANIES[:n]]


def _change(ticker: str, action: str, name: str = "") -> ThemeChangeResponse:
    return ThemeChangeResponse(ticker=ticker, company_name=name, action=action,
                               reason=f"{action.capitalize()}: a fixed template.")


def _detail(n: int = len(_COMPANIES), *, changes: Optional[List[ThemeChangeResponse]] = None,
            chips: Optional[List[str]] = None,
            news: Optional[List[ThemeNewsItemResponse]] = None,
            insight: Any = "default", updated_on: Optional[str] = "2026-10-01",
            constituents: Optional[List[ThemeConstituentResponse]] = None) -> ThemeDetailResponse:
    if insight == "default":
        insight = ThemeInsightResponse(
            as_of="2026-10-02", headline="Critical minerals rise",
            summary="The basket rose in the latest session.",
            tickers=chips if chips is not None else ["SGML", "FCX", "HBM"])
    return ThemeDetailResponse(
        slug=SLUG, title="The New Oil",
        subtitle="The critical minerals the world is racing to secure",
        image_url="https://example.invalid/hero.jpg", accent_hex="F59E0B",
        constituents=constituents if constituents is not None else _rows(n),
        updated_on=updated_on,
        changes=changes if changes is not None else [],
        performance=ThemePerformanceResponse(
            as_of="2026-10-02",
            periods=[ThemePeriodReturnResponse(period="1M", theme=-0.122, benchmark=0.006)],
            theme_series=[100.0, 103.5], benchmark_series=[100.0, 112.9]),
        insight=insight,
        news=news if news is not None else [
            ThemeNewsItemResponse(title="Freeport-McMoRan operational update", ticker="FCX"),
            ThemeNewsItemResponse(title="Hudbay Minerals output climbs", ticker="HBM"),
        ],
    )


def _lock(detail: ThemeDetailResponse, limit: int = LIMIT) -> ThemeDetailResponse:
    return redact_theme_detail(detail, limit=limit, tier_required=ent.TIER_PRO)


def _string_leaves(node: Any):
    if isinstance(node, dict):
        for v in node.values():
            yield from _string_leaves(v)
    elif isinstance(node, list):
        for v in node:
            yield from _string_leaves(v)
    elif isinstance(node, str):
        yield node


# ══════════════════════════════════════════════════════════════════════════════════════
# 1. Entitlements — falls closed
# ══════════════════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("tier, unlocked", [
    ("pro", True), ("premium", True), ("PRO ", True), (" Premium", True),
    ("free", False), (None, False), ("", False), ("platinum", False), ("max", False),
    (7, False), (True, False),
])
def test_the_gate_falls_closed(tier, unlocked):
    assert ent.theme_companies_unlocked(tier) is unlocked
    assert ent.theme_company_limit(tier) == (None if unlocked else LIMIT)
    assert ent.required_tier_for_theme_companies(tier) == (None if unlocked else ent.TIER_PRO)


def test_the_gate_shares_the_paid_floor():
    """The same frozenset as signals / whale detail / the Trillion Club detail, so the paid
    surfaces cannot drift into "Pro unlocks one, Max another"."""
    assert ent.THEME_COMPANIES_UNLOCKED_TIERS is ent.SIGNALS_UNLOCKED_TIERS


def test_the_free_limit_is_the_owner_s_five():
    """Owner request, 2026-10-04: "show the top 5 only". A change here is a product decision
    (the paywall row and the Swift fallback state it — test_paywall_copy_guards.py)."""
    assert type(LIMIT) is int and LIMIT == 5


# ══════════════════════════════════════════════════════════════════════════════════════
# 2. The cut, at every boundary
# ══════════════════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("n", sorted({0, 1, LIMIT - 1, LIMIT, LIMIT + 1, len(_COMPANIES)}))
def test_the_cut_at_every_boundary(n):
    locked = _lock(_detail(n, changes=[], chips=[], news=[]))
    assert [c.ticker for c in locked.constituents] == ALL[:min(n, LIMIT)]
    assert locked.locked_constituents_count == max(0, n - LIMIT)
    # The blurred list keeps the real length: shown + stand-ins == what Pro sees.
    assert len(locked.constituents) + locked.locked_constituents_count == n
    # Every locked-tier caller is flagged; the COUNT is what draws (or skips) the lock.
    assert locked.is_locked is True and locked.tier_required == ent.TIER_PRO
    assert locked.locked_changes_count == 0


def test_the_screenshot_theme_shows_its_five_largest_and_blurs_seven():
    locked = _lock(_detail())
    assert [c.ticker for c in locked.constituents] == ["SCCO", "RIO", "FCX", "CCJ", "TECK"]
    assert locked.locked_constituents_count == 7
    # A visible row is untouched — price, move, cap, name.
    assert locked.constituents[0].model_dump() == _rows(1)[0].model_dump()


def test_the_cut_keeps_the_order_it_was_given():
    """The service sorts (largest cap first); the redaction must take the first N of THAT
    order and never re-sort, or Free would see a different "top" than Pro's first rows."""
    shuffled = list(reversed(_rows(len(_COMPANIES))))
    locked = _lock(_detail(constituents=shuffled, changes=[], chips=[], news=[]))
    assert [c.ticker for c in locked.constituents] == list(reversed(ALL))[:LIMIT]


@pytest.mark.parametrize("limit", [0, -1, -50])
def test_a_limit_of_zero_or_below_withholds_the_whole_list(limit):
    locked = _lock(_detail(changes=[_change("FCX", "added"), _change("PLL", "removed")]), limit=limit)
    assert locked.constituents == []
    assert locked.locked_constituents_count == len(_COMPANIES)
    assert locked.insight is not None and locked.insight.tickers == []
    assert locked.news == []
    # Nothing is visible, so the added row goes; the removed one names a former member.
    assert [c.ticker for c in locked.changes] == ["PLL"] and locked.locked_changes_count == 1


def test_a_duplicate_row_is_counted_but_never_hides_a_visible_ticker():
    rows = _rows(LIMIT) + [_rows(1)[0]]           # the largest company, twice
    locked = _lock(_detail(constituents=rows, changes=[_change("SCCO", "added")],
                           chips=["SCCO"], news=[ThemeNewsItemResponse(title="x", ticker="SCCO")]))
    assert locked.locked_constituents_count == 1  # one row Pro would see, one stand-in
    assert [c.ticker for c in locked.changes] == ["SCCO"]
    assert locked.insight.tickers == ["SCCO"] and [n.ticker for n in locked.news] == ["SCCO"]


# ══════════════════════════════════════════════════════════════════════════════════════
# 3. No withheld company anywhere in a locked payload
# ══════════════════════════════════════════════════════════════════════════════════════

_CHANGES = [
    _change("FCX", "added", "Freeport-McMoRan"),       # visible member        → kept
    _change("HBM", "added", "Hudbay Minerals"),        # withheld member       → withheld
    _change("SGML", "returned", "Sigma Lithium"),      # withheld member       → withheld
    _change("PLL", "removed", "Piedmont Lithium"),     # former member         → kept
    _change("MP", "removed", "MP Materials"),          # "removed" yet withheld now (Studio) → withheld
    _change("NXE", "promoted", "NexGen Energy"),       # unknown action, withheld → withheld
    _change("CCJ", "promoted", "Cameco"),              # unknown action, visible  → kept
    _change("ZZZ", "added", "Gone Since"),             # added, no longer listed → withheld (allow-list)
]


def test_a_locked_payload_names_no_withheld_company_anywhere():
    detail = _detail(changes=_CHANGES, news=[
        ThemeNewsItemResponse(title="Freeport-McMoRan update", ticker="FCX"),
        ThemeNewsItemResponse(title="Hudbay Minerals output climbs", ticker="HBM"),
        ThemeNewsItemResponse(title="Albemarle lithium deal", ticker="ALB"),
    ])
    unlocked = detail.model_dump_json()
    # Non-vacuity: every withheld ticker and name IS in the unlocked payload, and the fixture
    # really puts withheld names into each side field the redaction must clean.
    for token in HIDDEN + HIDDEN_NAMES:
        assert token in unlocked, token
    assert "HBM" in detail.insight.tickers
    assert any(n.ticker == "HBM" for n in detail.news)
    assert any(c.ticker == "HBM" for c in detail.changes)

    locked = _lock(detail)
    wire = locked.model_dump_json()
    for ticker in HIDDEN:
        assert f'"{ticker}"' not in wire, f"{ticker} leaked into a locked payload"
    for name in HIDDEN_NAMES:
        assert name not in wire, f"{name} leaked into a locked payload"
    leaves = set(_string_leaves(locked.model_dump()))
    assert not (set(HIDDEN) | set(HIDDEN_NAMES)) & leaves


def test_change_rows_by_action():
    locked = _lock(_detail(changes=_CHANGES))
    assert [(c.ticker, c.action) for c in locked.changes] == [
        ("FCX", "added"), ("PLL", "removed"), ("CCJ", "promoted")]
    assert locked.locked_changes_count == 5
    assert locked.updated_on == "2026-10-01"     # rows remain: the review date stays


def test_when_every_change_is_withheld_the_review_date_goes_too():
    """A build that predates the gate prints "Reviewed — no changes this month" for a dated
    review with an empty list. That would be false here, so the date is dropped and such a
    build hides the card; a gated build still draws its locked line from the count."""
    locked = _lock(_detail(changes=[_change("HBM", "added"), _change("SGML", "returned")]))
    assert locked.changes == [] and locked.locked_changes_count == 2
    assert locked.updated_on is None


def test_a_month_with_no_changes_keeps_its_honest_no_changes_line():
    locked = _lock(_detail(changes=[]))
    assert locked.changes == [] and locked.locked_changes_count == 0
    assert locked.updated_on == "2026-10-01"


def test_the_why_it_s_moving_chips_keep_only_visible_companies():
    locked = _lock(_detail(chips=["SGML", "FCX", "HBM"]))
    assert locked.insight.tickers == ["FCX"]


def test_an_insight_whose_chips_are_all_withheld_gets_the_locked_wording():
    locked = _lock(_detail(chips=["SGML", "HBM"]))
    assert locked.insight is not None and locked.insight.tickers == []
    assert locked.insight.headline == redaction.LOCKED_INSIGHT_HEADLINE
    assert locked.insight.summary == redaction.LOCKED_INSIGHT_SUMMARY


def test_no_insight_stays_no_insight():
    assert _lock(_detail(insight=None)).insight is None


def test_an_insight_about_a_withheld_driver_never_reaches_a_locked_caller():
    """Adversarial review 2026-10-06: the generator names each driver and quotes its exact
    % move, and the big movers are usually the companies below the cut. When any driver is
    withheld, a locked caller gets fixed wording that names no company and no figure."""
    hidden_name = HIDDEN_NAMES[0]
    hidden_ticker = HIDDEN[0]
    prose = ThemeInsightResponse(
        as_of="2026-10-02",
        headline=f"{hidden_name} leads the basket",
        summary=f"{hidden_name} rose 9.1% after a supply deal; {ALL[0]} added 1.2%.",
        tickers=[hidden_ticker, ALL[0]],
    )
    locked = _lock(_detail(insight=prose))
    assert locked.insight.as_of == prose.as_of
    assert (locked.insight.headline, locked.insight.summary) == (
        redaction.LOCKED_INSIGHT_HEADLINE, redaction.LOCKED_INSIGHT_SUMMARY)
    assert locked.insight.tickers == [ALL[0]]
    wire = locked.model_dump_json()
    assert hidden_name not in wire and f'"{hidden_ticker}"' not in wire and "9.1%" not in wire


def test_the_locked_wording_names_no_company_and_no_figure():
    text = redaction.LOCKED_INSIGHT_HEADLINE + " " + redaction.LOCKED_INSIGHT_SUMMARY
    assert not re.search(r"\d", text)
    for ticker, name, _ in _COMPANIES:
        assert ticker not in text.split() and name not in text


def test_an_insight_about_visible_drivers_only_ships_as_written():
    prose = ThemeInsightResponse(as_of="2026-10-02", headline=f"{ALL[0]} leads",
                                 summary=f"{ALL[0]} added 1.2%.", tickers=[ALL[0]])
    locked = _lock(_detail(insight=prose))
    assert (locked.insight.headline, locked.insight.summary) == (prose.headline, prose.summary)
    assert locked.insight.tickers == [ALL[0]]


# ── the prose gate fails CLOSED (adversarial review 2026-10-07) ─────────────────────────────


def _visible_prose(summary: str, tickers: Optional[List[str]] = None) -> ThemeInsightResponse:
    return ThemeInsightResponse(as_of="2026-10-02", headline="Copper names lead",
                                summary=summary, tickers=tickers if tickers is not None else ["SCCO", "RIO"])


def _is_locked_text(insight: ThemeInsightResponse) -> bool:
    return (insight.headline, insight.summary) == (redaction.LOCKED_INSIGHT_HEADLINE,
                                                  redaction.LOCKED_INSIGHT_SUMMARY)


def test_a_degraded_empty_list_never_ships_the_prose():
    """A quote failure resolves no list (cached for the TTL): nothing can be checked against it."""
    prose = _visible_prose("Albemarle rose 9.1% after a deal.", tickers=["ALB", "SCCO"])
    locked = _lock(_detail(constituents=[], insight=prose))
    assert _is_locked_text(locked.insight) and locked.insight.tickers == []


def test_an_insight_with_no_drivers_never_ships_the_prose():
    locked = _lock(_detail(insight=_visible_prose("Albemarle rose 9.1% after a deal.", tickers=[])))
    assert _is_locked_text(locked.insight)


@pytest.mark.parametrize("summary", [
    "Southern Copper led; Albemarle rose 9.1% after a deal.",        # a withheld NAME, not a driver
    "Southern Copper led; ALB rose 9.1%.",                           # a withheld TICKER
    "Southern Copper led; $SGML jumped 12%.",                        # a cashtag
    "SIGMA LITHIUM jumped 12% while Southern Copper held.",          # upper-case prose (leading word)
    "Hudbay rose; Southern Copper held.",                            # distinctive first word (6+)
])
def test_prose_naming_a_withheld_non_driver_is_replaced(summary):
    locked = _lock(_detail(insight=_visible_prose(summary)))
    assert _is_locked_text(locked.insight), summary
    assert locked.insight.tickers == ["SCCO", "RIO"]


def test_prose_about_visible_companies_only_still_ships():
    prose = _visible_prose("Southern Copper rose 2.1% and Rio Tinto Group added 1.4%; CCJ held.")
    locked = _lock(_detail(insight=prose))
    assert (locked.insight.headline, locked.insight.summary) == (prose.headline, prose.summary)


def test_a_withheld_ticker_inside_another_word_does_not_trigger():
    """Word-bounded and upper case as written: 'MPa' or 'campaign' never match the ticker MP."""
    prose = _visible_prose("Southern Copper held as the campaign for copper pricing went on.")
    locked = _lock(_detail(insight=prose))
    assert not _is_locked_text(locked.insight)


def _with_hidden(ticker: str, name: str) -> List[ThemeConstituentResponse]:
    """The five visible rows, then ONE withheld company."""
    return _rows(LIMIT) + [ThemeConstituentResponse(ticker=ticker, company_name=name, price=10.0,
                                                    change_percent=3.0, market_cap=1e9)]


@pytest.mark.parametrize("ticker, name, summary", [
    # Registrant-style names as the quote feed sends them (adversarial review 2026-10-07: the
    # comma stayed in the pattern, so no "X, Inc." name ever matched its prose spelling).
    ("TSLA", "Tesla, Inc.", "Southern Copper led as Tesla added 3%."),
    ("IONQ", "IonQ, Inc.", "Southern Copper rose 2.1%; IonQ jumped 12% after a contract."),
    ("UBER", "Uber Technologies, Inc.", "Uber Technologies fell 4% while Southern Copper held."),
    ("AMZN", "Amazon.com, Inc.", "Amazon rose 2% and Southern Copper held."),
    ("KHC", "Kraft Heinz Company (The)", "Kraft Heinz slipped 1%; Southern Copper rose."),
    ("GMBXF", "Grupo Mexico S.A.B. de C.V.", "Grupo Mexico gained 5%; Southern Copper rose."),
    ("GOOGL", "Alphabet Inc. Class A", "Alphabet rose 2%; Southern Copper held."),
    ("TTD", "The Trade Desk, Inc.", "Trade Desk rose 6%; Southern Copper held."),
    # Its leading word ("C3") is too short to match alone, so only the cleaned core catches it.
    ("AI", "C3.ai, Inc.", "C3.ai jumped 9%; Southern Copper held."),
    # A short leading word alone, as a proper noun.
    ("UBER", "Uber Technologies, Inc.", "Uber fell 4% while Southern Copper held."),
    ("JOBY", "Joby Aviation, Inc.", "Southern Copper rose while Joby fell 7.4%."),
    ("JOBY", "JOBY AVIATION INC", "Southern Copper rose while Joby fell 7.4%."),  # upper-case feed
    ("BRK-B", "Berkshire Hathaway Inc.", "Berkshire rose 1%; Southern Copper held."),
    # A class share's ticker as prose writes it.
    ("BRK-B", "Berkshire Hathaway Inc.", "BRK.B rose 1%; Southern Copper held."),
    # A hyphenated leading word alone (review 2026-10-07: "X" / "D" / "T" were too short).
    ("XE", "X-Energy, Inc. Class A Common Stock", "Southern Copper held; X-Energy jumped 18%."),
    ("QBTS", "D-Wave Quantum Inc.", "D-Wave jumped 15%; Southern Copper held."),
    ("TMUS", "T-Mobile US, Inc.", "T-Mobile rose 2%; Southern Copper held."),
    # A 6-letter leading word alone.
    ("ALAB", "Astera Labs, Inc. Common Stock", "Astera Labs rose 4%; Southern Copper held."),
    # Only the share-class TAIL strip catches these — a two-letter leading word and no ticker
    # in the text (review 2026-10-08: removing the tail words kept every case above green).
    # Together they pin all fifteen tail words.
    ("ZCD", "CD Tech, Inc. Common Stock", "Southern Copper held; CD Tech rose 2%."),
    ("NU", "NU Holdings Ltd. Class A Ordinary Shares", "Southern Copper held; Nu Holdings rose 3%."),
    ("ZSH", "SH Tech Ordinary Share", "Southern Copper held; SH Tech rose 2%."),
    ("ZQX", "QX Labs plc American Depositary Shares", "Southern Copper held; QX Labs rose 4%."),
    ("ZDR", "DR Mining Ltd. American Depositary Receipts", "Southern Copper held; DR Mining rose 2%."),
    ("ZAD", "AD Bio ADR", "Southern Copper held; AD Bio rose 2%."),
    ("ZAS", "AS Bio ADS", "Southern Copper held; AS Bio rose 2%."),
    ("ZUN", "UN Power Partners L.P. Units", "Southern Copper held; UN Power Partners rose 2%."),
    ("ZAB", "AB Robotics Ltd. Class B Subordinate Voting Shares", "Southern Copper held; AB Robotics rose 2%."),
    ("ZGW", "GE Widgets Incorporated", "Southern Copper held; GE Widgets rose 2%."),
    ("ZXY", "XY Labs PBC", "Southern Copper held; XY Labs rose 2%."),
    # Only the CASE-INSENSITIVE core catches this one: the leading spellings are IONQ / Ionq
    # and the ticker is matched in upper case.
    ("IONQ", "IONQ INC", "Southern Copper held; IonQ jumped 12%."),
])
def test_prose_naming_a_withheld_company_as_prose_writes_it_is_replaced(ticker, name, summary):
    locked = _lock(_detail(constituents=_with_hidden(ticker, name), insight=_visible_prose(summary)))
    assert _is_locked_text(locked.insight), (name, summary)
    assert locked.insight.tickers == ["SCCO", "RIO"]


@pytest.mark.parametrize("ticker, name, summary", [
    # A leading word is matched as a proper noun only: lower-case prose is not the company.
    ("GM", "General Motors Company", "Southern Copper rose on general market strength."),
    ("UBER", "Uber Technologies, Inc.", "Southern Copper rose while uber-cautious funds waited."),
    # Word-bounded: a longer word that starts with the name is not the name.
    ("AMZN", "Amazon.com, Inc.", "Southern Copper rose as Amazonian supply worries eased."),
    ("TSLA", "Tesla, Inc.", "Southern Copper rose 2.1% on firmer copper prices."),
])
def test_prose_that_names_no_withheld_company_still_ships(ticker, name, summary):
    prose = _visible_prose(summary)
    locked = _lock(_detail(constituents=_with_hidden(ticker, name), insight=prose))
    assert (locked.insight.headline, locked.insight.summary) == (prose.headline, prose.summary)


@pytest.mark.parametrize("name", ["", "   ", "Inc.", "Class A Common Stock"])
def test_a_withheld_company_with_no_usable_name_locks_the_prose(name):
    """Its name cannot be checked, so the prose cannot be proven safe — fail closed."""
    prose = _visible_prose("Southern Copper rose 2.1% on firmer copper prices.")
    locked = _lock(_detail(constituents=_with_hidden("ZZZQ", name), insight=prose))
    assert _is_locked_text(locked.insight)


def test_a_partial_build_locks_the_prose():
    """Members whose quote failed are in no list (`_unresolved_members`, set by the service):
    their names cannot be checked, so the prose fails closed — even about visible drivers."""
    detail = _detail(insight=_visible_prose("Southern Copper rose 2.1%.", tickers=["SCCO"]))
    detail._unresolved_members = frozenset({"SGML"})
    locked = _lock(detail)
    assert _is_locked_text(locked.insight)
    complete = _lock(_detail(insight=_visible_prose("Southern Copper rose 2.1%.", tickers=["SCCO"])))
    assert not _is_locked_text(complete.insight)


def test_a_withheld_driver_locks_the_prose_even_when_nothing_is_withheld_from_the_list():
    """The driver check runs FIRST (review 2026-10-07): a driver outside the visible list locks
    the prose whatever else holds."""
    prose = _visible_prose("Sigma Lithium jumped 12.4%.", tickers=["SGML"])
    locked = _lock(_detail(LIMIT, insight=prose))
    assert locked.locked_constituents_count == 0 and _is_locked_text(locked.insight)


def test_a_theme_of_limit_companies_or_fewer_ships_its_prose_as_written():
    """A COMPLETE theme of `limit` companies or fewer: nothing is withheld, so nothing in the
    text is about a member the caller cannot see — even an insight with no drivers keeps its
    words (the locked wording would tell the caller to upgrade for companies they already
    have). Rotated themes hold 12+, so in production this is a Studio-curated short list; a
    PARTIAL build of a longer theme is locked instead (`test_a_partial_build_locks_the_prose`)."""
    prose = _visible_prose("Albemarle rose 9.1% after a deal.", tickers=[])
    locked = _lock(_detail(LIMIT, insight=prose))
    assert locked.locked_constituents_count == 0
    assert (locked.insight.headline, locked.insight.summary) == (prose.headline, prose.summary)


def test_a_duplicate_row_of_a_visible_company_does_not_lock_the_prose():
    rows = _rows(LIMIT) + [_rows(1)[0]]  # Southern Copper twice: the second copy is cut
    prose = _visible_prose("Southern Copper rose 2.1% on firmer copper prices.", tickers=["SCCO"])
    locked = _lock(_detail(constituents=rows, insight=prose))
    assert (locked.insight.headline, locked.insight.summary) == (prose.headline, prose.summary)


def test_news_keeps_only_items_about_a_visible_company():
    news = [
        ThemeNewsItemResponse(title="a", ticker="FCX"),
        ThemeNewsItemResponse(title="b", ticker="HBM"),       # withheld member
        ThemeNewsItemResponse(title="c", ticker=None),        # cannot be placed → dropped
        ThemeNewsItemResponse(title="d", ticker=" fcx "),     # messy stored form → kept
        ThemeNewsItemResponse(title="e", ticker="SPY"),       # not a member → dropped
        ThemeNewsItemResponse(title="f", ticker=""),          # blank → dropped
    ]
    locked = _lock(_detail(news=news))
    assert [n.title for n in locked.news] == ["a", "d"]


def test_class_shares_join_on_the_canonical_symbol():
    """The list is keyed "BRK-B"; chips, news and change rows may carry "BRK.B"."""
    rows = [ThemeConstituentResponse(ticker="BRK-B", company_name="Berkshire Hathaway",
                                     market_cap=1e12)] + _rows(LIMIT)[1:]
    locked = _lock(_detail(constituents=rows, changes=[_change("BRK.B", "added")],
                           chips=["brk.b"],
                           news=[ThemeNewsItemResponse(title="x", ticker="BRK.B")]))
    assert [c.ticker for c in locked.changes] == ["BRK.B"]
    assert locked.insight.tickers == ["brk.b"]
    assert [n.ticker for n in locked.news] == ["BRK.B"]


def test_performance_is_kept():
    """An equal-weight aggregate of the whole list: it names no company."""
    detail = _detail()
    assert _lock(detail).performance.model_dump() == detail.performance.model_dump()


def test_the_policy_decides_every_field():
    """A field added to ThemeDetailResponse must get an explicit redaction decision —
    otherwise it silently rides through (or is silently dropped by) the gate."""
    assert set(THEME_DETAIL_REDACTION_POLICY) == set(ThemeDetailResponse.model_fields)


def test_redaction_never_edits_the_shared_cached_detail():
    detail = _detail(changes=_CHANGES)
    before = detail.model_dump()
    chips = detail.insight.tickers
    _lock(detail)
    assert detail.model_dump() == before
    assert detail.insight.tickers is chips and chips == ["SGML", "FCX", "HBM"]


# ══════════════════════════════════════════════════════════════════════════════════════
# 4-5. The endpoint, through the real service and its shared cache
# ══════════════════════════════════════════════════════════════════════════════════════

_ROW = {"slug": SLUG, "title": "The New Oil", "subtitle": "Critical minerals",
        "image_url": None, "accent_hex": "F59E0B", "tickers": list(ALL),
        "tickers_as_of": "2026-10-01"}
_QUOTES = {t: {"symbol": t, "name": n, "price": 50.0, "changesPercentage": 1.0, "marketCap": cap}
           for t, n, cap in _COMPANIES}
_REVIEW = LatestReview(run_month="2026-10", themes={SLUG: ThemeReview(
    run_month="2026-10", change_count=2,
    changes=[ThemeChange("FCX", "added", "Added: x."), ThemeChange("HBM", "added", "Added: y."),
             ThemeChange("PLL", "removed", "Removed: z.")],
    roles={}, new={"FCX", "HBM"})})
_INSIGHTS = {SLUG: {
    "slug": SLUG, "as_of": "2026-10-02",
    "performance": {"as_of": "2026-10-02",
                    "periods": {"1M": {"status": "ok", "theme_return_pct": -12.2,
                                       "benchmark_return_pct": 0.6}},
                    "constituents": [{"ticker": t} for t in ALL]},
    "series": {"one_year": {"theme": [100.0, 103.5], "benchmark": [100.0, 112.9]}},
    "summary_headline": "Critical minerals rise", "summary_text": "The basket rose.",
    "summary_as_of": "2026-10-02",
    "drivers": [{"ticker": "SGML"}, {"ticker": "FCX"}, {"ticker": "HBM"}],
}}
_NEWS = [ThemeNewsItemResponse(title="Freeport-McMoRan update", ticker="FCX"),
         ThemeNewsItemResponse(title="Hudbay Minerals output climbs", ticker="HBM")]


class _FakeFMP:
    def __init__(self, quotes: Dict[str, Dict[str, Any]]):
        self.quotes = quotes
        self.batch_calls: List[List[str]] = []

    async def get_batch_quotes_bulk(self, symbols):
        self.batch_calls.append(list(symbols))
        return [self.quotes[s] for s in symbols if s in self.quotes]


def _async_value(value: Any):
    async def _f(*args, **kwargs):
        return value
    return _f


def _clear_detail_cache() -> None:
    HomeDashboardService._theme_detail_cache.clear()
    HomeDashboardService._theme_detail_inflight.clear()


@pytest.fixture
def svc(monkeypatch):
    _clear_detail_cache()
    monkeypatch.setattr(hds, "_latest_theme_review", _async_value(_REVIEW))
    monkeypatch.setattr(hds, "_latest_theme_insights", _async_value(_INSIGHTS))
    monkeypatch.setattr(hds, "_theme_news", _async_value(list(_NEWS)))
    service = HomeDashboardService()
    service.fmp = _FakeFMP(_QUOTES)  # type: ignore[assignment]
    service.price = PriceFromFMPFake(service.fmp)
    service._read_theme_row = lambda slug: dict(_ROW) if slug == SLUG else None  # type: ignore[assignment]
    monkeypatch.setattr(home_ep, "get_home_dashboard_service", lambda: service)
    yield service
    _clear_detail_cache()


_NO_TIER = object()


async def _call(tier: Any = "free", slug: str = SLUG):
    user: Dict[str, Any] = {"id": "u-1"}
    if tier is not _NO_TIER:
        user["tier"] = tier
    return await home_ep.get_theme_detail(slug=slug, user=user)


def _body(resp: JSONResponse):
    assert isinstance(resp, JSONResponse)
    body = json.loads(bytes(resp.body))
    assert {"error_code", "message", "user_message", "action", "details"} <= body.keys()
    for value in body["details"].values():
        assert isinstance(value, (str, int, float, bool))
    return resp.status_code, body


def _assert_free_view(resp: ThemeDetailResponse) -> None:
    assert isinstance(resp, ThemeDetailResponse)
    assert resp.is_locked is True and resp.tier_required == ent.TIER_PRO
    assert [c.ticker for c in resp.constituents] == VISIBLE
    assert resp.locked_constituents_count == len(HIDDEN)
    assert [(c.ticker, c.action) for c in resp.changes] == [("FCX", "added"), ("PLL", "removed")]
    assert resp.locked_changes_count == 1
    assert resp.insight.tickers == ["FCX"]
    # Two of the three drivers are cut, so the prose is the fixed locked wording.
    assert (resp.insight.headline, resp.insight.summary) == (
        redaction.LOCKED_INSIGHT_HEADLINE, redaction.LOCKED_INSIGHT_SUMMARY)
    assert [n.ticker for n in resp.news] == ["FCX"]
    wire = resp.model_dump_json()
    for ticker in HIDDEN:
        assert f'"{ticker}"' not in wire, ticker
    for name in HIDDEN_NAMES:
        assert name not in wire, name


def _assert_full_view(resp: ThemeDetailResponse) -> None:
    assert isinstance(resp, ThemeDetailResponse)
    assert resp.is_locked is False and resp.tier_required is None
    assert [c.ticker for c in resp.constituents] == ALL
    assert resp.locked_constituents_count == 0 and resp.locked_changes_count == 0
    assert [c.ticker for c in resp.changes] == ["FCX", "HBM", "PLL"]
    assert resp.insight.tickers == ["SGML", "FCX", "HBM"]
    # The insight exactly as generated (the _INSIGHTS row) — never the locked wording.
    assert (resp.insight.headline, resp.insight.summary) == ("Critical minerals rise", "The basket rose.")
    assert [n.ticker for n in resp.news] == ["FCX", "HBM"]


@pytest.mark.asyncio
@pytest.mark.parametrize("tier", ["free", None, "", "platinum", "FREE", 7, _NO_TIER])
async def test_endpoint_locks_free_missing_and_unknown_tiers(svc, tier):
    _assert_free_view(await _call(tier))


@pytest.mark.asyncio
@pytest.mark.parametrize("tier", ["pro", "premium", "PRO "])
async def test_endpoint_unlocks_paid_tiers(svc, tier):
    _assert_full_view(await _call(tier))


def _serve(svc, monkeypatch, *, resolved, summary: str, drivers) -> None:
    """Re-point the fixture's service: only ``resolved`` quotes come back, and the stored
    insight reads ``summary`` with ``drivers``."""
    svc.fmp = _FakeFMP({t: q for t, q in _QUOTES.items() if t in set(resolved)})
    svc.price = PriceFromFMPFake(svc.fmp)
    row = {**_INSIGHTS[SLUG], "summary_text": summary, "drivers": [{"ticker": t} for t in drivers]}
    monkeypatch.setattr(hds, "_latest_theme_insights", _async_value({SLUG: row}))
    _clear_detail_cache()


@pytest.mark.asyncio
async def test_a_partial_list_whose_drivers_did_not_resolve_never_ships_their_prose(svc, monkeypatch):
    """Review 2026-10-07 (#10): only the 5 largest quotes resolve, so NOTHING is withheld from
    the list — but the drivers are members whose quotes failed. Free must not read them."""
    _serve(svc, monkeypatch, resolved=VISIBLE, drivers=["SGML", "HBM"],
           summary="Sigma Lithium jumped 12.4% after a supply deal; Hudbay Minerals rose 6.1%.")
    resp = await _call("free")
    assert [c.ticker for c in resp.constituents] == VISIBLE and resp.locked_constituents_count == 0
    assert (resp.insight.headline, resp.insight.summary) == (
        redaction.LOCKED_INSIGHT_HEADLINE, redaction.LOCKED_INSIGHT_SUMMARY)
    assert "Sigma" not in resp.model_dump_json() and "12.4%" not in resp.model_dump_json()


@pytest.mark.asyncio
async def test_an_unresolved_non_driver_named_in_the_prose_never_reaches_free(svc, monkeypatch):
    """Review 2026-10-07 (#11): 7 of 12 quotes resolve, the driver is visible, and the summary
    names a member whose quote failed (so it is in no list for the name check)."""
    _serve(svc, monkeypatch, resolved=ALL[:7], drivers=["SCCO"],
           summary="Southern Copper rose 2.1%; Sigma Lithium jumped 12.4% after a supply deal.")
    resp = await _call("free")
    assert (resp.insight.headline, resp.insight.summary) == (
        redaction.LOCKED_INSIGHT_HEADLINE, redaction.LOCKED_INSIGHT_SUMMARY)
    # The cached build is shared: a paid caller still reads it as written.
    paid = await _call("pro")
    assert paid.insight.summary.startswith("Southern Copper rose 2.1%")


@pytest.mark.asyncio
async def test_a_complete_build_with_safe_prose_still_ships_it_to_free(svc, monkeypatch):
    """The must-keep twin: every quote resolves, the driver is visible and the text names only
    visible companies — Free reads it as written."""
    _serve(svc, monkeypatch, resolved=ALL, drivers=["SCCO"],
           summary="Southern Copper rose 2.1% on firmer copper prices.")
    resp = await _call("free")
    assert resp.insight.summary == "Southern Copper rose 2.1% on firmer copper prices."


@pytest.mark.asyncio
@pytest.mark.parametrize("tier", ["pro", "premium"])
async def test_a_paid_caller_always_gets_the_insight_as_written(svc, tier):
    """Two of the three drivers are cut for Free; a paid caller still reads the generated
    words — the prose gate runs only inside the redaction, never on the paid path."""
    resp = await _call(tier)
    assert (resp.insight.headline, resp.insight.summary) == ("Critical minerals rise", "The basket rose.")


@pytest.mark.asyncio
async def test_free_then_pro_then_free_share_one_cache_without_bleed(svc):
    _assert_free_view(await _call("free"))
    assert [c.ticker for c in HomeDashboardService._theme_detail_cache[SLUG][1].constituents] == ALL
    _assert_full_view(await _call("pro"))
    _assert_free_view(await _call("free"))
    assert len(svc.fmp.batch_calls) == 2      # one list build + one name lookup (PLL), cached after


@pytest.mark.asyncio
async def test_endpoint_unknown_slug_is_still_the_404(svc):
    status, body = _body(await _call("free", slug="no-such-theme"))
    assert status == 404 and body["error_code"] == "THEME_NOT_FOUND"


@pytest.mark.asyncio
async def test_endpoint_malformed_slug_is_still_the_400(svc):
    status, body = _body(await _call("free", slug=""))
    assert status == 400 and body["error_code"] == "INVALID_INPUT"


@pytest.mark.asyncio
async def test_a_redaction_that_breaks_fails_closed(svc, monkeypatch, caplog):
    """Never fall through to the unredacted list: a broken redaction is a structured error."""
    def _boom(*args, **kwargs):
        raise RuntimeError("redaction bug")

    monkeypatch.setattr(home_ep, "redact_theme_detail", _boom)
    with caplog.at_level("ERROR"):
        status, body = _body(await _call("free"))
    assert status >= 500
    text = json.dumps(body)
    for ticker in ALL:
        assert f'"{ticker}"' not in text
    assert "Theme detail redaction failed" in caplog.text and "slug=the-new-oil" in caplog.text
    # A paid caller never reaches the redaction, so the same bug cannot cost Pro the screen.
    _assert_full_view(await _call("pro"))


def test_endpoint_takes_the_users_row_identity():
    """The tier lives on the users row; a token carries none. Sign-in stays the ROUTER's."""
    import inspect

    from app.dependencies import get_current_user_id, get_watchlist_identity

    assert get_current_user_id in [d.dependency for d in home_ep.router.dependencies]
    route = next(r for r in home_ep.router.routes if r.path == "/themes/{slug}")
    assert route.endpoint is home_ep.get_theme_detail
    from app.core.client_app_version import capture_client_app_version

    deps = [p.default.dependency for p in inspect.signature(route.endpoint).parameters.values()
            if hasattr(p.default, "dependency")]
    assert deps == [get_watchlist_identity, capture_client_app_version]


@pytest.mark.parametrize("header, locked", [
    ({"X-App-Version": "1.0"}, False),   # build 1.0 cannot draw the lock: it keeps the full list
    ({"X-App-Version": "1.1"}, True),
    ({"X-App-Version": "1.01"}, True),   # the shipped name (owner, 2026-10-08)
    ({"X-App-Version": "2.0"}, True),
    ({}, True),                          # no header: not an old iOS build → the gate applies
    ({"X-App-Version": "garbage"}, True),
])
def test_a_free_build_1_0_keeps_the_full_list_and_1_1_gets_the_lock(svc, header, locked):
    """1.0 has no blurred rows and no upgrade prompt, so a silent cut to 5 would read as missing
    data (`home.THEME_LOCK_MIN_APP_VERSION`); every newer or unidentified caller is gated."""
    from fastapi.testclient import TestClient

    from app.dependencies import get_current_user_id, get_watchlist_identity
    from app.main import app

    app.dependency_overrides[get_current_user_id] = lambda: "u-1"
    app.dependency_overrides[get_watchlist_identity] = lambda: {"id": "u-1", "tier": "free"}
    try:
        resp = TestClient(app).get(f"/api/v1/home/themes/{SLUG}", headers=header)
    finally:
        app.dependency_overrides.pop(get_current_user_id, None)
        app.dependency_overrides.pop(get_watchlist_identity, None)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["is_locked"] is locked
    assert len(body["constituents"]) == (LIMIT if locked else len(ALL))


@pytest.mark.parametrize("tier, locked", [("free", True), ("pro", False), ("premium", False)])
def test_the_route_serializes_through_fastapi(svc, tier, locked):
    """Through FastAPI's real response serialisation (response_model), Free and paid alike."""
    import warnings

    from fastapi.testclient import TestClient

    from app.dependencies import get_current_user_id, get_watchlist_identity
    from app.main import app

    app.dependency_overrides[get_current_user_id] = lambda: "u-1"
    app.dependency_overrides[get_watchlist_identity] = lambda: {"id": "u-1", "tier": tier}
    try:
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            resp = TestClient(app).get(f"/api/v1/home/themes/{SLUG}")
    finally:
        app.dependency_overrides.pop(get_current_user_id, None)
        app.dependency_overrides.pop(get_watchlist_identity, None)
    assert resp.status_code == 200, resp.text
    assert not [w for w in caught if "serializ" in str(w.message).lower()]
    body = resp.json()
    assert body["is_locked"] is locked
    assert len(body["constituents"]) == (LIMIT if locked else len(ALL))
    assert body["locked_constituents_count"] == (len(HIDDEN) if locked else 0)
    if locked:
        for ticker in HIDDEN:
            assert f'"{ticker}"' not in resp.text, ticker
    ThemeDetailResponse.model_validate(body)


# ══════════════════════════════════════════════════════════════════════════════════════
# 6. Contract — what an already-shipped build decodes
# ══════════════════════════════════════════════════════════════════════════════════════

_GATE_DEFAULTS = {"is_locked": False, "tier_required": None,
                  "locked_constituents_count": 0, "locked_changes_count": 0}


def test_the_gate_fields_are_defaulted_so_a_shipped_build_decodes_unchanged():
    fields = ThemeDetailResponse.model_fields
    for name, default in _GATE_DEFAULTS.items():
        assert not fields[name].is_required(), name
        assert fields[name].default == default and type(fields[name].default) is type(default), name
    dumped = ThemeDetailResponse(slug="s", title="T", accent_hex="22D3EE").model_dump()
    assert {k: dumped[k] for k in _GATE_DEFAULTS} == _GATE_DEFAULTS


def test_a_worst_case_locked_payload_survives_json_and_revalidates():
    """Empty names, null price / move / cap, no performance, no insight, no review."""
    rows = [ThemeConstituentResponse(ticker=f"T{i}") for i in range(LIMIT + 3)]
    detail = ThemeDetailResponse(slug="s", title="T", accent_hex="22D3EE", constituents=rows)
    payload = json.loads(json.dumps(_lock(detail).model_dump(), allow_nan=False))
    ThemeDetailResponse.model_validate(payload)
    assert payload["is_locked"] is True and payload["tier_required"] == "pro"
    assert payload["locked_constituents_count"] == 3 and payload["locked_changes_count"] == 0
    # The four properties iOS decodes as non-Optional are always present and non-null.
    for key in ("slug", "title", "accent_hex", "constituents"):
        assert payload[key] is not None, key
    assert all(isinstance(c["ticker"], str) and c["ticker"] for c in payload["constituents"])
    assert payload["updated_on"] is None and payload["changes"] == [] and payload["news"] == []
