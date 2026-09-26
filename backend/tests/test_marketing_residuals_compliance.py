"""
Round-4 RESIDUALS in the compliance validators and the X length counter (`compliance.py`,
`post_copy.py`, `tlds.py`).

The validators are a regex DENYLIST that oscillated across three review rounds — tightening
over-blocked honest copy, relaxing reopened bypasses. Every change here is POSITIONAL and pinned
by a MUST-REJECT twin and a MUST-PASS twin, in BOTH scan modes:

* (a) FAIL-CLOSED DECISION 2026-09-26 — no exemption. The certainty row has no subject slot, so
  a BUSINESS measure reads as an investment ("Revenue and profit consistently climb"), and the
  safety row's outcome window reaches into a comparison ("Bonds are safer options than stocks").
  Rounds 4 and 5 exempted both; adversarial review found each relaxation still reopened
  bypasses (a named company's safety verdict by pronoun or in another field, a risky asset
  "safer than stocks" with the growth promise in the next sentence, a measure veto that read
  only part of the context). Both exemptions were REMOVED and HEAD's rows restored: the honest
  lines are rejected as promissory (the over-block is accepted; the semantic judge runs after
  these validators but never licenses a regex relaxation). Every must-REJECT list is kept.
* (d) the link validator exempted any glued abbreviation whose tail was missing from a short
  TLD list — but ".markets", ".bank", ".one", ".you" and ".best" are real gTLDs X autolinks
  ("Stocks in U.S.markets rose." passed). The exemption is inverted onto the shared
  `tlds.NON_TLD_TAILS` (tails KNOWN not to be TLDs), and a Title-Case tail ("U.S.Markets") is
  read by the validator and the X counter alike.
* (f) a Title-Case headline hid a person: "Why Frank Knight Mattered" (the verb slot was
  lower-case only). One narrow rule; the semantic judge is the main gate. Round 5 adds the
  present tense ("Matters", "Shapes", "Warns") and requires a KNOWN surname after an
  everyday-word first name or a verb any subject takes ("Why Angel Funding Mattered", "Why Henry
  Hub Matters" were read as people).
* (h) libass override characters ("{", "}", "\\") are refused in every field before Phase 3
  burns captions.

Category 1 (pure): the Learn bundle, the vendored lists and the two corpus fixtures.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Callable, List

import pytest

from app.services.marketing import compliance as c
from app.services.marketing import content_pool
from app.services.marketing import post_copy as pc
from app.services.marketing import tlds
from app.services.marketing import writer_service as ws
from test_marketing_content_a_writer_gate import _assert_rejected_everywhere
from test_marketing_content_r2_overblock import (_SWEEP_FLOOR, _SWEEP_LADDER, _SWEEP_LIMIT,
                                                 _compiled_patterns, assert_linear)

MM = "money_moves:"
RR, ET, MR = "journey:risk_reward", "journey:etfs_101", "journey:mr_market"
CO, AP = MM + "costcos-membership-magic", MM + "apples-services-revolution"

_DATA = Path(__file__).resolve().parent / "data"
_FIXTURES = ("marketing_real_drafts_2026_09_24.json", "marketing_real_draft_honest_texts.json")


def _scan(text: str, strict: bool, field: str = "x") -> List[tuple]:
    return [(v.code, v.detail) for v in c.scan_text(field, c.clean(text), strict_instruments=strict)]


def _codes(text: str, strict: bool, field: str = "x") -> List[str]:
    return [code for code, _d in _scan(text, strict, field)]


def _both_modes_empty(text: str) -> None:
    for strict in (True, False):
        got = _scan(text, strict)
        assert got == [], (text, strict, got)


def _flags(text: str, code: str) -> None:
    for strict in (True, False):
        assert code in _codes(text, strict), (text, strict, _scan(text, strict))


def _field(key: str, text: str) -> list:
    item = content_pool.get_item(key)
    return [(v.code, v.detail) for v in ws._scan("x", c.clean(text), item, allow_emoji=True)]


# ── (a) FAIL-CLOSED DECISION 2026-09-26: no business-measure or "than"-comparand exemption ─
#
# Round 4 exempted a business-measure subject from the certainty row and the object of "than"
# from the safety row; round 5 narrowed both to allowlisted shapes. Adversarial review still
# reproduced bypasses through each (see the module docstring), and the repo rule is that a regex
# relaxation must not reopen one — so both were REMOVED (main session, 2026-09-26) and HEAD's
# rows restored. These honest lines are OVER-BLOCKED on purpose: each is rejected as promissory,
# in both scan modes, by the row that reads it. Do not re-add a must-pass for any of them
# without a gate stronger than a regex (marketing.md §7).
#
# Two round-4 pass lines are not here: "Sales and margins steadily grow." and "The company's
# revenue has consistently grown." are read by no promise row at all ("steadily" is not a
# certainty adverb; "has consistently" is not the "has always" row) and pass exactly as at HEAD.

FAIL_CLOSED_BUSINESS = (
    "Revenue and profit consistently climb.",
    "Revenue and profit consistently climb over the years.",
    "Revenue, profit and margins consistently climb.",
    "Services revenue consistently climbs.",
    "Free cash flow consistently grows.",
    "The company's earnings consistently grow.",
    "Revenue has consistently climbed.",
    "Operating income consistently rises year after year.",
    "Revenue & profit consistently climb.",
    "Revenue and profit consistently climb, year after year.",
)
FAIL_CLOSED_SAFETY = (
    "Bonds are safer options than stocks.",
    "Bonds are safer options than money under a mattress.",
    "Bonds are safer options than stocks, but they grow slowly.",
    "Bonds are safer options than stocks, but they grow more slowly.",
    "Bonds are safer options than stocks, but they still carry risk.",
)


@pytest.mark.parametrize("text", FAIL_CLOSED_BUSINESS)
def test_fail_closed_2026_09_26_a_business_measure_line_is_rejected(text):
    """The certainty row itself rejects it — a "consistently <growth verb>" match."""
    for strict in (True, False):
        got = _scan(text, strict)
        hits = [d for code, d in got if code == "promissory"]
        assert hits and hits[0].startswith("consistently "), (text, strict, got)


@pytest.mark.parametrize("text", FAIL_CLOSED_SAFETY)
def test_fail_closed_2026_09_26_a_than_comparison_is_rejected(text):
    """The safety row itself rejects it — the comparand fills the outcome slot."""
    for strict in (True, False):
        got = _scan(text, strict)
        hits = [d for code, d in got if code == "promissory"]
        assert hits and hits[0].startswith("safer options than "), (text, strict, got)


def test_fail_closed_2026_09_26_holds_in_every_item_and_beside_any_next_field():
    """No context re-opens either reading: a Journey item, a Money Moves item, company terms, or
    a neutral next field."""
    for key in (ET, RR, CO):
        for text in ("Revenue and profit consistently climb.",
                     "Bonds are safer options than stocks."):
            got = {code for code, _d in _field(key, text)}
            assert "promissory" in got, (key, text, got)
    got = {code for code, _d in _field(CO, "Costco's membership fees consistently grow.")}
    assert "promissory" in got, got
    got = {v.code for v in c.scan_text("x", c.clean("Revenue and profit consistently climb."),
                                       company_terms=frozenset({"costco"}))}
    assert "promissory" in got, got
    got = {v.code for v in c.scan_text("x", c.clean("Revenue and profit consistently climb."),
                                       next_text="Margins widen too.")}
    assert "promissory" in got, got


# The rounds' must-REJECT lists, kept as regression pins: every line is a promise. Their
# category comments name the round-5 shape condition each line failed; with the exemption
# gone, the certainty or safety row rejects each one outright.

#: Every line the round-4 skeptic review reproduced (HEAD rejected each as promissory).
CONFIRMED_REGRESSIONS = (
    # CRITICAL — a forecast about a NAMED company.
    "Nvidia's earnings inevitably climb.", "Nvidia's revenue consistently climbs in the years ahead.",
    "Nvidia's revenue inevitably rises as AI spreads.", "Nvidia's sales inevitably grow.",
    "Nvidia's revenue consistently climbs, so the price follows.",
    "Nvidia's profits reliably rise, so patience pays.",
    "Nvidia's revenue consistently climbs, so holders win.",
    # MAJOR — bare profits/earnings/income: an investor's profit promise.
    "Profits consistently climb for patient savers.", "Profits reliably grow for those who wait.",
    "Profits consistently climb when you hold for the long term.",
    "Traders' profits consistently climb.", "Follow the plan: profits consistently climb.",
    "Stocks swing, but profits consistently climb.", "The market dips, but income reliably grows.",
    "Stay the course so profits consistently climb.", "Hold on, because profits reliably rise.",
    "Members' income has always grown.", "Income reliably grows for patient savers.",
    # MAJOR — the exemption covered every verb of the row.
    "Earnings always recover.", "Profits always pay off.", "Profits consistently win.",
    "Earnings reliably outperform.", "Earnings have always recovered.",
    "Profits have always bounced back.", "Earnings have always outperformed.",
    "Profits have always paid off.", "Earnings have always rewarded patience.",
)

BUSINESS_REJECT = (
    # (i) a company named — listed, unlisted, capitalised possessor, prefix, suffix. The first
    # REVERSES a round-4 must-pass on purpose: no record or forecast about a named company.
    "Costco's membership fees consistently grow.", "Costco's membership has always grown.",
    "Zorblax's revenue consistently climbs.", "Zorblax revenue consistently climbs.",
    "At Nvidia, revenue consistently climbs.", "Nvidia: revenue consistently climbs.",
    "Revenue consistently climbs at Nvidia.",
    "Apple's revenue consistently climbs, so the stock always rises.",
    "Costco sells memberships. Revenue and profit consistently climb.",
    # (ii) a certainty adverb that is not "consistently"/"steadily", and the "has always" row.
    "The company's revenue always grows.", "Revenue inevitably climbs.", "Revenue reliably grows.",
    "Revenue invariably rises.", "The company's revenue has always grown.",
    # (iii) a verb that is not growth.
    "Revenue consistently recovers.", "Revenue consistently beats.",
    "Revenue consistently outperforms.", "Revenue consistently pays off.",
    # (iv) the subject: an ambiguous head with no business anchor, a possessor off the
    # allowlist, an investment, a measure that is not the whole subject.
    "Profits consistently climb.", "Earnings consistently grow.", "Income consistently grows.",
    "Their income consistently grows.", "Its earnings consistently climb.",
    "Net income consistently rises.", "Stocks consistently climb.",
    "Your money consistently grows.", "Index funds reliably rise.",
    "Stocks and revenue consistently climb.", "Revenue and stocks consistently climb.",
    "Stocks, like revenue, consistently climb.", "Revenue, like stocks, consistently climbs.",
    "Profits of index funds consistently climb.", "Investors who track earnings reliably win.",
    "Its earnings and share price consistently climb.", "The fund's earnings consistently grow.",
    "The portfolio's income consistently grows.", "Investors' profits consistently climb.",
    "Dividend income consistently grows.", "The account's income consistently grows.",
    "A retiree's income consistently grows.", "Your income consistently grows.",
    "Cash consistently grows.", "It's revenue that consistently climbs.",
    "Stocks have always grown.",
    # (v) an investment or a person anywhere in the field. "Stocks are volatile, but revenue
    # consistently climbs" REVERSES a round-4 must-pass.
    "Stocks are volatile, but revenue consistently climbs.",
    "Revenue consistently climbs, and so does the stock.",
    "Revenue consistently climbs, so investors win.",
    "Stocks rise because earnings consistently climb.",
    "Stocks rise when revenue consistently climbs.", "Unlike stocks, revenue consistently climbs.",
    "Revenue consistently climbs for shareholders.",
    "Stay the course. Revenue consistently climbs.",
    "Revenue and profit consistently climb. So hold on.",
    # (vi) anything after the verb but a CLOSED time adjunct, or between subject and adverb.
    "Revenue will consistently climb.", "Revenue consistently climbs in the years ahead.",
    "Revenue consistently climbs from here.", "Revenue consistently climbs as AI spreads.",
    "Revenue and profit consistently climb and climb.",
    "Revenue consistently climbs and never falls.",
    '"Revenue and profit consistently climb."',
)


@pytest.mark.parametrize("text", CONFIRMED_REGRESSIONS)
def test_every_confirmed_round_4_regression_is_a_promise_again(text):
    _flags(text, "promissory")


@pytest.mark.parametrize("text", BUSINESS_REJECT)
def test_every_business_measure_bypass_is_a_promise(text):
    _flags(text, "promissory")


def test_the_pinned_company_behaviour_neighbours_hold():
    # test_marketing_compliance.py MUST_PASS: a business habit of a named company.
    _both_modes_empty("Costco always keeps prices low.")
    # No frame licenses a promise about a named company's shares (marketing.md §7).
    _flags("Myth: Apple stock always goes up.", "promissory")


def test_a_named_companys_stock_clause_is_rejected_in_its_own_item():
    got = {code for code, _d in _field(AP, "Apple's revenue consistently climbs, so the stock "
                                          "always rises.")}
    assert "promissory" in got, got


#: The four lines the round-4 skeptic review reproduced, and their BOND twins.
SAFETY_CONFIRMED = (
    "Index funds are safer options than stocks yet grow your money.",
    "Index funds are safer options than stocks, while still growing your wealth.",
    "Index funds are safer options than stocks, but they still grow your money.",
    "Index funds are safer options than stocks though they grow your wealth.",
)
SAFETY_AFTER_CONTRAST = tuple(t.replace("Index funds", "Bonds") for t in SAFETY_CONFIRMED)

SAFETY_REJECT = SAFETY_CONFIRMED + SAFETY_AFTER_CONTRAST + (
    "Index funds are a safe way to grow your money.",
    "ETFs are a safe, simple way to start investing.",
    "An index fund is the safest place for your savings.",
    # An outcome AFTER the comparand is the safe thing's own purpose…
    "Index funds are safer options than stocks to grow your money.",
    "Bonds are safer options than stocks and grow your money.",
    "Index funds are safer options than stocks, and they grow your money.",
    "Bonds are safer options than stocks for retirement savings.",
    "Bonds are safer options than stocks for anyone who wants steady growth.",
    # …slow growth is the trade-off only where it ends its clause…
    "Bonds are safer options than stocks, but they grow slowly but surely.",
    "Bonds are safer options than stocks, but they grow slowly and steadily.",
    # …a benefit or a person is a purpose too…
    "Bonds are safer options than stocks, and they keep you safe.",
    "Bonds are safer options than stocks and let you sleep at night.",
    # …and so is an outcome BEFORE the safe thing (fail-closed: "Index funds are safer options
    # than stocks" was rejected at HEAD too).
    "For retirees, bonds are safer options than stocks.",
    "To grow your money, bonds are safer options than stocks.",
    "Index funds are safer options than stocks.",
    # Other words between the safe thing and the comparand, two comparands, a company.
    "Bonds are safer places to park cash than stocks.",
    "Bonds are safer options than stocks or funds.",
    "Apple bonds are safer options than stocks.",
)


@pytest.mark.parametrize("text", SAFETY_REJECT)
def test_a_safe_way_to_an_outcome_is_still_a_promise(text):
    _flags(text, "promissory")


@pytest.mark.parametrize("text", [
    "Bonds are generally safer than stocks.",                       # no safe-THING noun
    "A savings account is a safe place for an emergency fund.",     # insured cash is a fact
    "Savings accounts are safer options than money under a mattress.",  # insured cash
])
def test_the_pinned_safety_neighbours_still_pass(text):
    """HEAD behaviour the revert keeps (test_marketing_compliance.py pins the first)."""
    _both_modes_empty(text)


# ── (d) links: the exemption is decided by tails KNOWN not to be TLDs ───────────────────────

LINK_PASS = (
    "Prices in U.S.dollars rose.", "Costs, e.g.the rent, rose.", "Visa vs.the rest.",
    "The U.S.economy grew.",
    # A Title-Case or upper-case tail that is known not to be a TLD is still a typo.
    "Prices in U.S.Dollars rose.", "The U.S.Economy grew.", "Prices in U.S.DOLLARS rose.",
    # A plain missing space (no abbreviation head) stays a typo, in any case.
    "It rose fast.Then it fell.", "Stay calm.So what?",
)

LINK_REJECT = (
    "Stocks in U.S.markets rose.", "U.S.Markets", "Stocks in U.S.MARKETS rose.",
    "See e.g.bank rules.", "See e.g.Bank rules.", "Look at vs.best options.",
    "Look at vs.Best options.", "Read i.e.one note.", "Ask e.g.you first.",
    "Mr.Market is moody.",                          # .market is a gTLD, and X links it
)


@pytest.mark.parametrize("text", LINK_PASS)
def test_a_glued_abbreviation_with_a_known_non_tld_tail_is_not_a_link(text):
    for strict in (True, False):
        assert "link" not in _codes(text, strict), (text, _scan(text, strict))


@pytest.mark.parametrize("text", LINK_REJECT)
def test_a_glued_abbreviation_ending_in_a_real_tld_is_a_link_in_any_case(text):
    _flags(text, "link")


@pytest.mark.parametrize("span, is_link", [
    ("U.S.markets", True), ("U.S.Markets", True), ("U.S.MARKETS", True), ("e.g.bank", True),
    ("e.g.Bank", True), ("vs.best", True), ("vs.Best", True), ("i.e.one", True),
    ("Mr.Market", True),
    ("U.S.dollars", False), ("U.S.Dollars", False), ("U.S.economy", False),
    ("U.S.Economy", False), ("e.g.the", False), ("vs.the", False), ("Vs.The", False),
])
def test_the_validator_and_the_x_counter_agree_on_what_a_link_is(span, is_link):
    """X autolinks a TLD case-insensitively (post_copy header): whatever the validator calls a
    link weighs 23 on X, and a glued typo it passes is counted character by character."""
    assert ("link" in _codes(f"Look at {span} now.", True)) is is_link, span
    assert pc.x_weighted_length(span) == (23 if is_link else len(span)), span


def test_x_counts_a_title_case_glued_gtld_as_a_url_inside_a_sentence():
    text = "Stocks in U.S.Markets rose."
    assert pc.x_weighted_length(text) == len("Stocks in ") + 23 + len(" rose.")


def test_one_shared_non_tld_list_feeds_both_the_validator_and_the_counter():
    assert c.is_non_tld_tail is tlds.is_non_tld_tail
    assert pc._is_abbreviation_run is c._is_abbreviation_run
    assert pc.DOMAIN_SHAPE_RES is c.DOMAIN_SHAPE_RES
    assert c._CASED_ABBREV_RUN_RE in c.DOMAIN_SHAPE_RES
    assert not hasattr(pc, "_NON_TLD_TAILS"), "a second copy of the tail list drifts"


def test_no_word_that_is_a_real_tld_is_listed_as_a_non_tld_tail():
    """Hand-checked against the IANA root zone (version 2026072500): these ARE delegated."""
    assert tlds.KNOWN_WORD_TLDS
    assert not (tlds.KNOWN_WORD_TLDS & tlds.NON_TLD_TAILS)
    for word in ("markets", "bank", "one", "you", "best"):
        assert not tlds.is_non_tld_tail(word) and not tlds.is_non_tld_tail(word.title())
    for word in ("dollars", "the", "economy"):
        assert tlds.is_non_tld_tail(word) and tlds.is_non_tld_tail(word.upper())


def test_the_spoken_tld_list_was_not_enlarged():
    """The fix inverts the exemption instead of growing `_KNOWN_TLDS` (r2_overblock pins the
    structural positives outside it)."""
    for tld in ("markets", "bank", "one", "you", "best", "education", "blog"):
        assert tld not in c._KNOWN_TLDS, tld


def test_tlds_is_a_stdlib_leaf_module():
    src = (Path(tlds.__file__)).read_text(encoding="utf-8")
    imported = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Import):
            imported |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            imported.add((node.module or "").split(".")[0])
    assert imported <= {"__future__", "typing"}, imported


def test_a_title_case_glued_gtld_is_rejected_everywhere():
    _assert_rejected_everywhere(ET, "Index funds track U.S.Markets closely.", "link")


# ── (f) a Title-Case headline naming a person ───────────────────────────────────────────────

TITLE_PERSON_REJECT = (
    "Why Frank Knight Mattered",
    "What Frank Knight Taught Investors",
    "Why Frank Knight Warned Investors",
    "How Frank Knight Coined Uncertainty",
    # Round 5: the present tense.
    "Why Frank Knight Matters",
    "How Frank Knight Shapes Risk Thinking",
    "Frank Knight Warns Investors About Uncertainty",
)

#: Round 4 over-blocks: a first name that is an everyday word, or a verb any subject takes,
#: before a capitalised word that is no known surname.
TITLE_KNOWN_SURNAME_GATED = (
    "Why Angel Funding Mattered", "Why Angel Funding Matters", "Why Henry Hub Matters",
    "Why Kelly Criterion Matters", "Why Grace Periods Mattered", "Why Frank Talk Matters",
    "What Angel Investors Taught Founders", "Why Holly Leaves Mattered",
)

TITLE_PERSON_PASS = TITLE_KNOWN_SURNAME_GATED + (
    # Pinned neighbours (test_marketing_grounding.py, test_marketing_compliance.py).
    "Mark Your Calendar", "Pay the Bill First", "Why Disney Won", "Max Markup Cap.",
    "Charles Schwab", "Walt Disney Company", "Union Jack",
    # A company the scan reads as one, with the headline verb.
    "Why Louis Vuitton Mattered", "Why Charles Schwab Mattered",
    # A participle a headline uses as a noun-phrase suffix.
    "Grace Period Explained",
    "Why Jackson Hole Matters",
)


@pytest.mark.parametrize("text", TITLE_PERSON_REJECT)
def test_a_title_case_name_with_a_capitalised_person_verb_is_a_person(text):
    for strict in (True, False):
        assert ("person_named", "Frank Knight") in _scan(text, strict), (text, _scan(text, strict))


@pytest.mark.parametrize("text", TITLE_PERSON_PASS)
def test_ordinary_titles_brands_and_headline_suffixes_are_not_people(text):
    _both_modes_empty(text)


def _title_pair(text: str):
    sk = c.skeleton(c.clean(text))
    m = next((m for m in c._FULL_NAME_RE.finditer(sk) if m.group(1).lower() in c.given_names()),
             None)
    assert m is not None, text
    return sk, m, sk[m.end(2):].split()[0]


@pytest.mark.parametrize("text", TITLE_KNOWN_SURNAME_GATED)
def test_the_known_surname_gate_is_what_passes_an_everyday_word_pair(text, monkeypatch):
    """Anti-vacuity: each reaches the rule (Title-Case, a listed given name, a capitalised verb
    it accepts) with a surname nobody is known by — and once that surname is 'known', it fires."""
    sk, m, verb = _title_pair(text)
    assert c._title_cased(sk) and verb[:1].isupper(), text
    assert verb.lower() in c._TITLE_PERSON_VERBS, text
    assert (verb.lower() in c._TITLE_ANY_SUBJECT_VERBS
            or m.group(1).lower() in c._WORD_GIVEN), text
    assert m.group(2).lower() not in c.known_surnames(), text
    known = c.known_surnames() | {m.group(2).lower()}
    monkeypatch.setattr(c, "known_surnames", lambda: known)
    got = _scan(text, True)
    assert ("person_named", f"{m.group(1)} {m.group(2)}") in got, (text, got)


def test_frank_knight_passes_the_gate_because_knight_is_a_known_surname():
    assert "frank" in c._WORD_GIVEN and "knight" in c.known_surnames()
    assert "matters" in c._TITLE_ANY_SUBJECT_VERBS and "matters" in c._TITLE_PERSON_VERBS


@pytest.mark.parametrize("text", ["Why Louis Vuitton Mattered", "Why Charles Schwab Mattered",
                                  "Grace Period Explained"])
def test_the_title_passes_reach_the_new_rule_and_a_guard_stops_them(text, monkeypatch):
    """Anti-vacuity: each is Title-Case, opens with a listed given name and puts a capitalised
    word the old HUMAN verb list knows (or 'mattered') after the pair — and even with its
    surname 'known', the company guard or the headline-suffix exclusion passes it."""
    sk, m, verb = _title_pair(text)
    assert c._title_cased(sk), text
    verb = verb.lower()
    assert verb in c._HUMAN_VERBS | {"mattered"}, text
    known = c.known_surnames() | {m.group(2).lower()}
    monkeypatch.setattr(c, "known_surnames", lambda: known)
    assert _scan(text, True) == [], text
    if verb == "mattered":
        assert c.sentence_company_mentions(sk), text       # the company guard
    else:
        assert verb not in c._TITLE_PERSON_VERBS, text     # the suffix exclusion


#: The lower-case twins: the same name and capitalised verb in PROSE, which is not Title-Case.
PROSE_TWINS = (
    "Economists still ask why Frank Knight Mattered so much to them.",
    "Economists still ask why Frank Knight Matters so much to them.",
)


@pytest.mark.parametrize("text", PROSE_TWINS)
def test_the_title_rule_is_title_case_only(text, monkeypatch):
    """Positional and non-vacuous: in prose the capitalised verb is not read (the lower-case
    verb slot stays the old rule's) — and the Title-Case gate is what stops it: forced open, the
    same line fires."""
    assert not c._title_cased(c.skeleton(c.clean(text)))
    for strict in (True, False):
        assert ("person_named", "Frank Knight") not in _scan(text, strict), text
    monkeypatch.setattr(c, "_title_cased", lambda sent: True)
    assert ("person_named", "Frank Knight") in _scan(text, True), text


# ── (h) libass override characters ──────────────────────────────────────────────────────────

MARKUP_REJECT = ("Profit {\\p1}m 0 0{\\p0} rose.", "A\\Nb", "Costs {rose}",
                 "{\\alpha&HFF&}Costs rose.", "Costs rose.\\hThen", "Costs rose }",
                 "Costs ｛rose｝", "Costs &lbrace;rose&rbrace;")


@pytest.mark.parametrize("field", ["x", "youtube_title", "hook", "cards.body", "tiktok"])
@pytest.mark.parametrize("text", MARKUP_REJECT)
def test_an_ass_override_character_is_markup_in_every_field(text, field):
    for strict in (True, False):
        assert "markup" in _codes(text, strict, field), (text, field)


def test_the_honest_corpus_has_no_ass_override_characters():
    """MUST-PASS side of (h): the real-draft corpus never uses the characters, so refusing them
    everywhere rejects nothing honest."""
    def strings(x):
        if isinstance(x, dict):
            for v in x.values():
                yield from strings(v)
        elif isinstance(x, list):
            for v in x:
                yield from strings(v)
        elif isinstance(x, str):
            yield x
    n = 0
    for name in _FIXTURES:
        for s in strings(json.loads((_DATA / name).read_text(encoding="utf-8"))):
            n += 1
            assert not any(ch in s for ch in "{}\\"), (name, s[:80])
    assert n > 5000, n


def test_the_ass_row_has_a_sample_only_it_matches_and_carries_its_weight(monkeypatch):
    """Row weight (r2_row_weight's rule for a scanned table): the new row alone matches its
    sample among `_MARKUP_RES`, and deleting it loses the code."""
    row = next(rx for rx in c._MARKUP_RES if rx.pattern == r"[{}\\]")
    sample = "Costs {rose}"
    assert [rx for rx in c._MARKUP_RES if rx.search(sample)] == [row]
    assert "markup" in _codes(sample, True)
    monkeypatch.setattr(c, "_MARKUP_RES", tuple(rx for rx in c._MARKUP_RES if rx is not row))
    assert "markup" not in _codes(sample, True)


def test_ass_markup_is_rejected_everywhere():
    _assert_rejected_everywhere(ET, "Index funds {\\p1}hold many stocks.", "markup")


# ── linear time on every new path ───────────────────────────────────────────────────────────

_NEW_PATTERNS = ("compliance._CASED_ABBREV_RUN_RE",)


def test_the_pattern_sweep_covers_every_new_pattern():
    names = {n for n, _rx in _compiled_patterns()}
    missing = [n for n in _NEW_PATTERNS if n not in names]
    assert missing == [], missing
    assert any(rx.pattern == r"[{}\\]" for n, rx in _compiled_patterns()
               if n.startswith("compliance._MARKUP_RES"))


@pytest.mark.parametrize("name", _NEW_PATTERNS)
@pytest.mark.parametrize("unit", ["a.", "U.S.", "A-", "s&p&", "a'", "9.", "zzco ", "a's ",
                                  "e.g.B", "than ", "always ", "revenue and ", "grow slowly ",
                                  "company's "])
def test_every_new_pattern_is_linear_on_unspaced_runs(name, unit):
    rx = dict(_compiled_patterns())[name]

    def make(n: int) -> Callable[[], object]:
        text = (unit * n)[:n]
        return lambda: list(rx.finditer(text))

    assert_linear(make, (name, unit), floor=_SWEEP_FLOOR, ladder=_SWEEP_LADDER, limit=_SWEEP_LIMIT)


@pytest.mark.parametrize("key, unit", [
    (CO, "revenue and profit consistently climb, "),
    (CO, "Costco's membership fees consistently grow and so do "),
    (CO, "revenue, profit, margins, "), (ET, "the fund's earnings consistently grow "),
    (RR, "safer options than stocks "), (RR, "safer options than stocks to grow "),
    (ET, "U.S.Markets "), (ET, "e.g.Bank "), (ET, "vs.the "),
    (RR, "Why Frank Knight Mattered "), (RR, "Why Louis Vuitton Mattered "),
    (ET, "{\\p1}"), (ET, "but revenue consistently climbs "),
    # A Journey field (no company) full of promise matches: one long sentence, many short ones.
    (ET, "revenue and profit consistently climb, "), (ET, "revenue and profit consistently climb. "),
    (ET, "the company's revenue consistently climbs over the years "),
    (RR, "bonds are safer options than stocks, but they grow slowly "),
    (RR, "bonds are safer options than money under a mattress "),
    (RR, "Why Frank Knight Matters "), (RR, "Why Angel Funding Matters "),
])
def test_a_degenerate_field_on_a_residual_path_scans_in_linear_time(key, unit):
    item = content_pool.get_item(key)

    def run(n: int) -> Callable[[], None]:
        text = (unit * (n // len(unit) + 1))[:n]
        return lambda: ws._scan("f", text, item, allow_emoji=True, next_text=text)

    assert_linear(run, (key, unit))


# ---------------------------------------------------------------------------------------------
# The writer prompt steers AROUND the over-blocks this file keeps (fail-closed decision above).
# Every "write this instead" phrasing it recommends must itself pass the validators, or the
# steering sends the model from one rejection to another; and every "refuses even when honest"
# example it quotes must really be refused, or the prompt teaches a rule that does not exist.
# ---------------------------------------------------------------------------------------------

from app.services.marketing import writer_prompts as _wp  # noqa: E402

#: (quoted refused example, recommended rewrite) — each pair as it appears in SYSTEM_BODY.
_STEERING_PAIRS = (
    ("revenue consistently climbs", "revenue grew year after year"),
    ("safer options than stocks", "bonds usually swing less, and usually grow less"),
    ("rewards with no risk are impossible", "higher rewards come with higher risk"),
    ("rewards with no risk are impossible",
     "be wary of anyone who promises big rewards with little risk"),
)


def _sentence(fragment: str) -> str:
    return fragment[0].upper() + fragment[1:] + "."


@pytest.mark.parametrize("refused,rewrite", _STEERING_PAIRS)
def test_every_steering_pair_is_quoted_in_the_writer_prompt(refused, rewrite):
    body = _wp.SYSTEM_BODY
    assert f'\\"{refused}' in body or f'"{refused}' in body, refused
    assert f'"{rewrite}' in body, rewrite


@pytest.mark.parametrize("refused,rewrite", _STEERING_PAIRS)
def test_every_steering_rewrite_passes_and_every_quoted_example_is_refused(refused, rewrite):
    for strict in (True, False):
        assert _scan(_sentence(rewrite), strict) == [], (rewrite, strict)
    # The refused example, in a sentence the way a draft would carry it.
    carriers = {
        "revenue consistently climbs": "Its revenue consistently climbs.",
        "safer options than stocks": "Bonds are safer options than stocks.",
        # The 2026-09-26 preview's own line (journey:risk_reward, rejected twice). The existing
        # scam-warning exemption already passes "…signal fraud" / "…are a warning sign"; it is
        # NOT widened for "something dishonest" — the prompt steers around it instead.
        "rewards with no risk are impossible": "Promises of big rewards with no risk often "
                                               "signal something dishonest.",
    }
    got = _scan(carriers[refused], True)
    assert any(code == "promissory" for code, _ in got), (refused, got)
    # The prompt's own example is refused too (journey:risk_reward's 2026-09-26 repair line).
    assert any(code == "promissory" for code, _ in _scan(_sentence(refused), True)), refused


def test_the_risk_reward_denials_the_preview_wrote_are_still_refused():
    """Pinned as the over-block the prompt now steers around (not relaxed — decision above)."""
    for text in ("High reward with no risk is not possible in investing.",
                 "It is impossible to achieve high returns with no risk at all."):
        for strict in (True, False):
            assert "promissory" in _codes(text, strict), (text, strict)


# ---------------------------------------------------------------------------------------------
# The PRODUCTION scan (compliance + grounding), not compliance alone: a recommended rewrite the
# grounding layer refuses is just as dead (review 2026-09-26: the `link` hint recommended
# "U.S. markets", which grounding refused as `ungrounded_acronym "U.S"` on 34 of 34 items).
# ---------------------------------------------------------------------------------------------

#: Every phrasing SYSTEM_BODY or REPAIR_HINTS tells the model to WRITE, as a sentence.
_RECOMMENDED_SENTENCES = (
    "Prices in US markets move every day.",
    "Many costs, e.g. banks' fees, add up.",
    "Revenue grew year after year.",
    "Bonds usually swing less, and usually grow less.",
    "Higher rewards come with higher risk.",
    "Be wary of anyone who promises big rewards with little risk.",
)


def _production_hits(text: str) -> List[tuple]:
    hits = []
    for key in content_pool.eligible_keys():
        item = content_pool.get_item(key)
        vs = ws._scan("video_script", text, item, allow_emoji=False, myth_framed=False)
        if vs:
            hits.append((key, [(v.code, v.detail) for v in vs]))
    return hits


def test_the_recommended_fragments_are_the_ones_the_prompt_quotes():
    body = _wp.SYSTEM_BODY + " " + " ".join(_wp.REPAIR_HINTS.values())
    for frag in ("US markets", "e.g. banks", "revenue grew year after year",
                 "higher rewards come with higher risk"):
        assert frag in body, frag
    # The prompt must never RECOMMEND the dotted form grounding refuses (the review found the
    # `link` hint doing exactly that): it may name it only as refused.
    assert '\\"U.S. markets' not in body and '"U.S. markets' not in body


@pytest.mark.parametrize("text", _RECOMMENDED_SENTENCES)
def test_every_recommended_rewrite_passes_the_production_scan_on_every_item(text):
    assert _production_hits(text) == []


#: A dotted place acronym stays REFUSED by grounding — on purpose. A W1 fix (2026-09-26)
#: accepted it and W2 showed what grounding had been masking: compliance reads "U.S." as a
#: sentence end, so a frame in one clause reached a promise after it, every period-bounded row
#: gap stopped at the dot, and a dotted name ("U.S. Grant", "U.S. Steel") read as a sentence
#: start. The prompt asks for "US" instead (spoken identically by the voice). Each line below
#: passed EVERY check while the exemption was in.
_DOTTED_MUST_REJECT_EVERYWHERE = (
    "Many U.S. markets closed early that day.",                # the honest over-block, steered
    "Don't panic in the U.S. The market always recovers.",
    "Ignore the hype in the U.S. Buy index funds now.",
    "No one can promise returns in the U.S. Index funds always go up.",
    "Myth: prices only fall in the U.S. You can't lose with index funds.",
    "It's a myth that stocks crash in the E.U. Index funds always go up.",
    "Don't believe the hype in the U.K. Index funds always go up.",
    "Beware of hype in the U.S. Index funds are risk-free.",
    "Nobody who held index funds in the U.S. ever lost money.",
    "The stock, listed in the U.S., soared.",
    "Shares of U.S. Steel keep climbing.",
    "U.S. Grant said patience pays.",
    "U.S. Bank pays a small interest rate.",
    "The U.S.A. is a large market.",
)
_DOTTED_MUST_REJECT_ON = (
    ("money_moves:costcos-membership-magic", "Costco shares in the U.S. keep climbing."),
    ("money_moves:costcos-membership-magic", "Costco in the U.S. still has a long runway."),
    ("money_moves:costcos-membership-magic", "Investors in the U.S. rewarded Costco."),
    ("money_moves:apples-services-revolution", "Apple stock in the U.S. soared."),
)


@pytest.mark.parametrize("text", _DOTTED_MUST_REJECT_EVERYWHERE)
def test_a_dotted_place_acronym_is_refused_on_every_item(text):
    hits = _production_hits(text)
    assert len(hits) == len(content_pool.eligible_keys()), (text, len(hits))


@pytest.mark.parametrize("key,text", _DOTTED_MUST_REJECT_ON)
def test_a_dotted_place_acronym_cannot_hide_a_company_claim_on_its_own_item(key, text):
    item = content_pool.get_item(key)
    assert ws._scan("video_script", text, item, allow_emoji=False, myth_framed=False), text


@pytest.mark.parametrize("text", (
    "Many US markets closed early that day.",
    "In the US, many people save.",
    "The UK and the EU trade a lot.",
    "The USA is a large market.",
))
def test_the_undotted_place_acronym_the_prompt_asks_for_is_grounded_on_every_item(text):
    assert _production_hits(text) == []


@pytest.mark.parametrize("text", (
    "The C.E.O. spoke.",
    "Its C.E.O. made a risky call.",
    "The company's C.F.O. resigned.",
    "The C.O.O. left.",
    "The S.E.C. oversees markets.",
))
def test_a_dotted_role_or_unknown_acronym_is_still_refused_on_every_item(text):
    """Compliance's role-person rule reads "CEO", not "C.E.O.", so grounding's refusal of a
    dotted acronym is what stops a dotted role (it passed every other check while a W1 fix
    briefly exempted dotted acronyms, 2026-09-26)."""
    hits = _production_hits(text)
    assert len(hits) == len(content_pool.eligible_keys()), (text, len(hits))
    assert all(any(code == "ungrounded_acronym" for code, _ in h) for _k, h in hits), text
