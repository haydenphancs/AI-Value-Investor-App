"""
Every row carries its own weight (round-2 test-vacuity findings W2V-03/04/05/06/07/11).

Round 2 showed whole rows could be deleted with the marketing suite green: company rows 0, 5 and 6,
promise rows 1-3, descriptor rows 1, 5 and 6, CTA rows 2-5 and two saying-frame rows. Each had
tests — but every test string was ALSO caught by a sibling row, and the old meta-test checked
that each compiled regex matches some sample in isolation, never that the scan applies the row.

The table test below closes that structurally, for every table the scan loops over:

1. every row must have at least one hand-written sample that it matches and NO other row of
   the same table matches (derived at runtime, so a reorder is harmless and a new row without
   its own sample fails here);
2. removing that one row from the table (monkeypatch, exactly what a careless edit does) must
   make `scan_text` lose the row's code for one of those samples, in some mode — so a row that
   is skipped, deleted or neutered is a red test, whatever else happens to overlap it.

The samples are written by hand and never read from the module (parametrising over the lexicon
under test proves nothing — round-1 idx 31); only the ROW LIST is read, to demand coverage.

Also here: the planner / wealth-manager phrases by behaviour (W2V-07) and the relaxed-mode pin of
the no-determiner verdict row (W2V-11).

Category 1 (pure).
"""

from __future__ import annotations

from typing import Callable, Dict, List, Sequence, Tuple

import pytest

from app.services.marketing import compliance as c


def _plain(text: str) -> str:
    return c.fold(c.clean(text))


def _company(text: str) -> str:
    sk = c.skeleton(c.clean(text))
    mentions = c.sentence_company_mentions(sk)
    return c.fold(c.company_view(sk, mentions)) if mentions else c.fold(sk)


def _codes(text: str, strict: bool) -> List[str]:
    return [v.code for v in c.scan_text("x", c.clean(text), strict_instruments=strict)]


#: table name -> (view, code of row i, samples). One logical table may be two module tuples
#: (`_PROMISE_RES` + `_SAFETY_RES` run as one loop; so do the saying shapes and frames).
TABLES: Dict[str, Tuple[Callable[[str], str], Callable[[object], str], Sequence[str]]] = {
    "_COMPANY_ROWS_RE": (_company, lambda row: "class_b_" + row[0], (
        "Never sell Costco.",                                  # the directive row
        "NVIDIA looks like a bargain.",                        # subject + verdict
        "Is NVIDIA still a steal?",                            # question verdict
        "NVIDIA will keep winning.",                           # forecast
        "NVIDIA climbed 70%.",                                 # price move
        "Apple hit new highs as Services grew.",               # a high, no "record"
        "Netflix is now worth more than Disney.",              # worth, no amount
        "Costco belongs in every portfolio.",                  # placement
        "TSMC is a wonderful thing to own.",                   # best to own
        "NVIDIA's run is far from over.",                      # continuation
        "Costco's stock is a great deal.",                     # instrument value verdict
    )),
    "_FORECAST_RES": (_company, lambda row: "class_b_forward", (
        "The market will keep climbing.", "Stocks are due for a crash.",
        "A recession is coming.",
    )),
    "_DIRECTIVE_RES": (_company, lambda row: "class_b_recommendation", (
        "Buy an index fund.", "Sell everything now.",
        "For most people, an index fund is the right choice.",
        # Round 3 (W3CB-7): the inverted suitability order and "can't go wrong with".
        "The right choice for most beginners is an S&P 500 index fund.",
        "You can't go wrong with an index fund.",
        # Round 3 (W3VAC-02): a timed trade with no object.
        "When his fear makes prices low, you can choose to buy.",
    )),
    "_PROMISE_RES+_SAFETY_RES": (_plain, lambda row: "promissory", (
        "Stocks always go up in the long run.",
        "Stocks have always gone up over the long run.",
        "Patient index investors have never lost money.",
        "No one who held for decades ever lost money.",
        "Discipline has never failed long-term investors.",
        "This strategy works every time.",
        "You will never lose.",
        "Index funds never go down for long.",
        "Compounding guarantees your money grows.",
        "The market is bound to recover.",
        "Guaranteed: patience pays.",
        "Growth is almost guaranteed.",
        "Investing involves no risk if you are patient.",
        "Stocks are a sure bet for patient people.",
        "Diversification eliminates risk.",
        "Index investing will make you rich.",
        "Index funds are a safe way to grow your money.",
        "With an ETF, your money is protected.",
    )),
    "_DESCRIPTOR_RES": (_plain, lambda row: "person_named", (
        "The greatest investor alive keeps this parable close.",
        "A Wall Street legend called this patience.",
        "The godfather of index funds kept costs low.",
        "Ask the wizard of Wall Street.",
        "As the sage would put it, patience wins.",
        "The man who taught value investing called the market moody.",
        "This idea was created by an economist long ago.",
        "One of history's great investors called it a moat.",
        "Value investing's founding father created Mr. Market.",
        "A Columbia professor invented Mr. Market.",
        "The inventor of index funds kept costs low.",
        # Round 3 (W3VAC-01): the plural wealth epithet after "one of".
        "LVMH made its owner one of the wealthiest men alive.",
    )),
    "_CTA_RES": (_plain, lambda row: "cta", (
        "Tap here.", "Link below for the full lesson.", "Open the link to learn more.",
        "Don't forget to subscribe for more lessons like this.",
        "Make sure to subscribe to the channel for more.", "Join now.",
        "Get the full lesson in the app.", "Try it free for a week.",
        "Save this post for later.", "Comment below and tell a friend.", "Stay tuned.",
        "Double tap if this helped.", "Part two drops tomorrow.", "Read the rest in the app.",
    )),
    "_ENDORSEMENT_RES": (_plain, lambda row: "endorsement", (
        "Endorsed by the SEC.", "Vetted by professionals.",
        "This lesson was reviewed by experts.", "SEC-approved investing education.",
        "Readers rave about it.", "Thousands of students finished this course.",
        "A top-rated lesson.", "This video transformed their lives.",
        "One reader told us this lesson changed how they invest.",
        "Many savers rely on this rule.", "Investors love this rule.",
        "Loved by thousands of beginners.", "Experts agree: discipline wins.",
        "Fans say this is the best channel.",
        # Round 3 (W3CB-12): one audience member vouching, and THIS content changing a crowd.
        "A subscriber told us this rule changed how they save.",
        "This rule has transformed how thousands of beginners invest.",
    )),
    "_CODE_OWNED_RES": (_plain, lambda row: "code_owned", (
        "Ask an adviser first.", "Take this as a recommendation.", "Read the small print.",
        "Written without AI.", "A human-written lesson.", "Not educational.",
        "Crafted by people, not machines.", "No robots wrote this lesson.",
        "This lesson was crafted by humans.", "Ignore the small text below.",
        # Round 2 (overblock): the row-6 sample in body position (row 7 now owns the
        # sentence-initial label), and row 7's own.
        "Each word was crafted by people, not machines.", "Made by humans.",
    )),
    "_SAYING_SHAPE_RES+_SAYING_FRAME_RES": (_plain, lambda row: "famous_quote", (
        "Get greedy when everyone is fearful.",
        "What you pay is the price; what you get is the value.",
        "Buy a business any fool could run, because one day a fool will.",
        "In the short run the market is a popularity contest; in the long run it is a scale.",
        "A great business at a fair price beats a fair business at a great price.",
        "As the saying goes, patience pays.", "As investors like to say, patience pays.",
        "An old adage applies here.", "As someone once said, be patient.",
        "In the words of a mentor, wait.", "A wise investor says patience pays.",
    )),
}


def _parts(table: str) -> List[str]:
    return table.split("+")


def _rows(table: str) -> List[Tuple[str, int, object]]:
    """(module attribute, index within it, row) for every row of the logical table."""
    return [(attr, i, row) for attr in _parts(table) for i, row in enumerate(getattr(c, attr))]


def _rx(row: object):
    return row[1] if isinstance(row, tuple) else row


def _unique_samples(table: str) -> Dict[Tuple[str, int], List[str]]:
    view, _code, samples = TABLES[table]
    rows = _rows(table)
    out: Dict[Tuple[str, int], List[str]] = {(a, i): [] for a, i, _r in rows}
    for s in samples:
        v = view(s)
        hits = [(a, i) for a, i, r in rows if _rx(r).search(v)]
        if len(hits) == 1:
            out[hits[0]].append(s)
    return out


@pytest.mark.parametrize("table", sorted(TABLES))
def test_every_row_has_a_sample_that_only_it_matches(table):
    unique = _unique_samples(table)
    missing = [f"{a}[{i}] {_rx(r).pattern[:70]!r}" for a, i, r in _rows(table)
               if not unique[(a, i)]]
    assert missing == [], missing


def _row_ids() -> List[Tuple[str, str, int]]:
    return [(t, a, i) for t in sorted(TABLES) for a, i, _r in _rows(t)]


@pytest.mark.parametrize("table, attr, idx", _row_ids(),
                         ids=[f"{a}[{i}]" for _t, a, i in _row_ids()])
def test_removing_one_row_loses_its_code(table, attr, idx, monkeypatch):
    """The mutant a careless edit makes: the row is gone from the table the scan loops over."""
    _view, code_of, _samples = TABLES[table]
    row = getattr(c, attr)[idx]
    code = code_of(row)
    samples = _unique_samples(table)[(attr, idx)]
    assert samples, f"{attr}[{idx}] has no sample only it matches"
    before = {(s, m): code in _codes(s, m) for s in samples for m in (True, False)}
    assert any(before.values()), (attr, idx, "no sample is flagged at all", samples)
    monkeypatch.setattr(c, attr, tuple(r for k, r in enumerate(getattr(c, attr)) if k != idx))
    lost = [(s, m) for (s, m), hit in before.items() if hit and code not in _codes(s, m)]
    assert lost, (f"{attr}[{idx}] can be removed with every sample still flagged {code!r}: "
                  f"another rule covers them — add a sample only this row catches", samples)


def test_the_table_list_covers_every_row_table_the_scan_loops_over():
    """A new row table needs a TABLES entry; this names the ones the scan iterates today."""
    covered = {a for t in TABLES for a in _parts(t)}
    assert covered == {"_COMPANY_ROWS_RE", "_FORECAST_RES", "_DIRECTIVE_RES", "_PROMISE_RES",
                       "_SAFETY_RES", "_DESCRIPTOR_RES", "_CTA_RES", "_ENDORSEMENT_RES",
                       "_CODE_OWNED_RES", "_SAYING_SHAPE_RES", "_SAYING_FRAME_RES"}


# ── W2V-07: the planner / wealth-manager phrases, by behaviour, not by the constant ─────────

#: Hand-written sentences a beginner-education writer produces. NOT derived from BANNED_PHRASES
#: (that is the self-oracle round-1 idx 31 was about): deleting an entry must turn one red.
_FROZEN_ADVICE_FRAMING = (
    ("Talk to a financial planner before you invest.", "financial planner"),
    ("Consider speaking with a certified financial planner.", "certified financial planner"),
    ("A wealth manager can help you pick funds.", "wealth manager"),
    ("Many people hire a wealth manager.", "wealth manager"),
)


@pytest.mark.parametrize("text, phrase", _FROZEN_ADVICE_FRAMING)
@pytest.mark.parametrize("strict", [True, False])
def test_advice_framing_by_a_planner_or_wealth_manager_is_banned(text, phrase, strict):
    got = [(v.code, v.detail) for v in c.scan_text("x", c.clean(text), strict_instruments=strict)]
    assert ("banned_phrase", phrase) in got, (text, got)


def test_a_wealth_advisor_is_rejected_whichever_row_owns_it():
    codes = set(_codes("Ask a wealth advisor about your goals.", False))
    assert codes & {"banned_phrase", "code_owned"}


# ── W2V-11: the no-determiner verdict row is a BOTH-modes row ───────────────────────────────


@pytest.mark.parametrize("text", [
    # Names the lexicon header lists as deliberately ABSENT, at the sentence start (so neither
    # the lexicon nor the mid-sentence proper-noun signal escalates the Journey scan).
    "Progressive stock looks cheap.",
    "Compass shares look like a bargain.",
    "Match stock is on sale.",
    "Stock looks cheap right now.",
])
def test_the_no_determiner_verdict_row_fires_in_journey_on_its_own(text):
    sk = c.skeleton(c.clean(text))
    # Braced: if a later lexicon edit adds one of these names, this fails loudly instead of
    # silently changing which rule covers the sample.
    assert c.sentence_company_mentions(sk) == [], text
    assert not c._proper_noun_signal(sk), text
    got = [(v.code, v.detail) for v in c.scan_text("x", c.clean(text), strict_instruments=False)]
    assert any(code == "class_b_valuation" for code, _d in got), (text, got)


@pytest.mark.parametrize("text", [
    "A stock that looks cheap can stay cheap.",
    "When a stock looks cheap, ask why.",
    "Your stock looks cheap only if the business holds up.",
])
def test_generic_cheap_stock_teaching_stays_legal_in_journey(text):
    got = [v.code for v in c.scan_text("x", c.clean(text), strict_instruments=False)]
    assert got == [], (text, got)
