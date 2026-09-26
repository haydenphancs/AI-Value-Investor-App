"""
Round-3 ENTITIES (W3 review of the round-2 fix pass): companies, people, numbers, grounding.

Three rounds oscillated between over-blocking and bypasses because each rule was wider or
narrower than its reason. Every rule here is STRUCTURAL where it can be — an optional adverb slot
between a company and its verb, the case study's own word-brand as the issuer, a verb-brand's
imperative told apart from its subject use by what follows it, an amount bound to the names of
its OWN clause — and every rule is pinned by a MUST-REJECT twin and a MUST-PASS twin:

* MUST-REJECT strings are the W3 repros plus natural variants, end to end through
  `validate_package` as the shared hook (the package is rejected) and as the X caption (the X
  post is dropped), or through `scan_text` in both modes where the rule is mode-free.
* MUST-PASS strings are asserted as an EMPTY field scan or a published post — never as the
  absence of one code, which is how a guard goes vacuous.
* The new ALTERNATIVES of multi-branch rows are neutralised one at a time (the mutant a careless
  edit makes) and their sample must lose its code: a row-level sample proves nothing about a
  sibling alternative (W3VAC-03: the label branch was deletable with the suite green).

Category 1 (pure): the Learn bundle and the vendored lists only.
"""

from __future__ import annotations

import re
from typing import List

import pytest

from app.services.marketing import compliance as c
from app.services.marketing import content_pool
from app.services.marketing import grounding as g
from app.services.marketing import post_copy as pc
from app.services.marketing import writer_prompts as wp
from app.services.marketing import writer_service as ws
from test_marketing_content_a_writer_gate import (RUN_DATE, _assert_rejected_everywhere,
                                                  _baseline, _with)

MM = "money_moves:"
VI, AP, ME, NV, CO, AM, MS, LV, TE, NF, HD, TT = (
    MM + "visa-vs-mastercard", MM + "apples-services-revolution", MM + "metas-metaverse-pivot",
    MM + "nvidias-ai-dominance", MM + "costcos-membership-magic", MM + "how-amazon-built-its-moat",
    MM + "microsofts-cloud-metamorphosis", MM + "the-rise-of-lvmh", MM + "tesla-vs-traditional-auto",
    MM + "netflix-vs-disney-plus", MM + "the-home-depot-vs-lowes",
    MM + "the-rise-of-tiktok-vs-instagram-reels")
MR, KS, EM, AS, FO, CM, ET, SB, PG, IS, IT = (
    "journey:mr_market", "journey:key_statistics", "journey:economic_moats",
    "journey:art_of_selling", "journey:fomo_cycle", "journey:common_mistakes", "journey:etfs_101",
    "journey:stock_vs_business", "journey:portfolio_gardening", "journey:income_statement",
    "journey:inflation_thief")


def codes(text: str, strict: bool) -> List[str]:
    return [v.code for v in c.scan_text("x", c.clean(text), strict_instruments=strict)]


def _field(key: str, text: str) -> list:
    item = content_pool.get_item(key)
    return [(v.code, v.detail) for v in ws._scan("x", c.clean(text), item, allow_emoji=True)]


def _posted(key: str, text: str) -> None:
    item, pkg = _baseline(key)
    res = ws.validate_package(_with(pkg, "x", text), item, RUN_DATE)
    assert "x" in res.posts, (key, text, [(v.code, v.detail) for v in res.outlets.get("x", [])])


def _script_ok(key: str, text: str) -> None:
    """A SHARED field (the first script line): the whole package must stay clean."""
    item, pkg = _baseline(key)
    res = ws.validate_package(_with(pkg, "script", text), item, RUN_DATE)
    assert res.ok and not res.violations, (key, text, [(v.field, v.code, v.detail)
                                                       for v in res.violations])


def _mentions(text: str, own=frozenset()) -> List[str]:
    return [n for s in c.sentences(c.skeleton(c.clean(text)))
            for n, _a, _b in c.sentence_company_mentions(s, own)]


# ── 1. W3CB-3: Money Moves — an adverb slot in every company row, and the case study's own
#       word-brand is the issuer before any verb ─────────────────────────────────────────────


MONEY_MOVES_ADVERB_BYPASSES = (
    (VI, "Visa belongs in every long-term portfolio.", "class_b_recommendation"),
    (VI, "Visa Belongs In Every Portfolio", "class_b_recommendation"),
    (VI, "Visa truly is one of the best businesses to own.", "class_b_recommendation"),
    (VI, "Visa is arguably one of the best businesses to own.", "class_b_recommendation"),
    (AP, "Apple belongs in every long-term portfolio.", "class_b_recommendation"),
    (AP, "Apple still looks unstoppable.", "class_b_forward"),
    (ME, "Meta belongs in every portfolio.", "class_b_recommendation"),
    (ME, "Meta simply is one of the greatest compounders of all time.", "class_b_recommendation"),
    (NV, "NVIDIA arguably belongs in every long-term portfolio.", "class_b_recommendation"),
    (NV, "NVIDIA honestly looks unstoppable.", "class_b_forward"),
    (NV, "NVIDIA still looks unstoppable.", "class_b_forward"),
    (NV, "NVIDIA now looks unstoppable.", "class_b_forward"),
    (NV, "NVIDIA looks almost unstoppable.", "class_b_forward"),
    (CO, "Costco genuinely is a stock you can hold forever.", "class_b_recommendation"),
    (CO, "Costco is still a stock you can hold forever.", "class_b_recommendation"),
    (CO, "Costco probably can keep compounding for decades.", "class_b_forward"),
    (CO, "Costco arguably belongs in every portfolio.", "class_b_recommendation"),
    # The case study's own word-brand before an UNLISTED verb (no `_CO_VERBS` entry, no price
    # word): only the own-brand rule makes "Visa" the issuer here.
    (VI, "Visa quietly compounds and has years of growth ahead.", "class_b_forward"),
)


@pytest.mark.parametrize("key, text, code", MONEY_MOVES_ADVERB_BYPASSES)
def test_an_adverb_or_an_unlisted_verb_no_longer_hides_a_money_moves_opinion(key, text, code):
    _assert_rejected_everywhere(key, text, code)


@pytest.mark.parametrize("key, text", [
    (VI, "Visa owns a piece of critical infrastructure."),
    (VI, "Visa cards work almost everywhere."),
    (VI, "Visa and Mastercard run the payment rails."),
    (AP, "Apple is one of the greatest companies of all time."),
    (CO, "Costco can keep prices low for members."),
    (CO, "Costco is one people keep coming back to."),
    (CO, "Costco is a company people buy from in bulk."),
])
def test_the_case_study_company_describing_its_business_still_posts(key, text):
    _posted(key, text)


def test_the_own_word_brand_is_the_issuer_only_on_its_own_case_study():
    text = "Visa quietly compounds and has years of growth ahead."
    assert _mentions(text) == []                       # no listed verb: a word elsewhere
    assert _mentions(text, frozenset({"visa"})) == ["visa"]
    # Only lexicon WORD-brands of the item are "own": the noise a sheet capitalises is not.
    assert c.own_word_brands(frozenset({"visa", "mastercard", "ceo", "store", "tv"})) == {"visa"}
    # An imperative stays a word even for an own brand ("Target a low P/E…").
    assert _mentions("Target a low P/E and a solid dividend.", frozenset({"target"})) == []


@pytest.mark.parametrize("text", [
    # The NON-verdict branch (no price word: "portfolio" is not one) with an adverb before a
    # listed verb, and the new placement verb itself.
    "Apple truly belongs in every portfolio.",
    "Apple belongs in every portfolio.",
])
def test_a_journey_word_brand_before_an_adverb_or_belongs_is_a_company(text):
    assert _mentions(text) == ["apple"], text
    _assert_rejected_everywhere(MR, text, "ungrounded_entity")


# ── 2. W3CB-4: Journey — a word-brand, an adverb, then its verb, in a price sentence ─────────


JOURNEY_ADVERB_VERDICTS = (
    (MR, "Apple really looks cheap right now."),
    (KS, "Apple currently trades at a low P/E."),
    (EM, "Coke finally looks like a bargain."),
    (EM, "Visa honestly deserves a premium price."),
    (MR, "Meta suddenly looks cheap."),
    (MR, "Apple truly looks cheap today."),
    (MR, "Apple really is a bargain right now."),
    (EM, "Coke certainly deserves a premium price."),
    (KS, "Target currently trades at a low P/E."),
    # The residuals a one-adverb skip left: the listed two- and three-word adverbials.
    (MR, "Apple right now looks cheap."),
    (MR, "Apple today looks cheap."),
    (MR, "Apple sure looks cheap right now."),
    (MR, "Apple at this price looks cheap."),
    # A verb-brand's third-person verb takes an object, whatever the object is.
    (KS, "Target generates huge cash, and it looks cheap."),
    (KS, "Target quietly generates huge cash, and it looks cheap."),
    # A question inverts the auxiliary with its SUBJECT: the brand after it is the company.
    (KS, "Did Target Look Cheap Last Year?"),
    (KS, "Can Target Recover From Its Cheap Price?"),
    # A verb-brand before a preposition in a headline is a noun: the company.
    (KS, "Target At A Bargain Price"),
    (KS, "Chase On Sale Right Now"),
    (KS, "Target still looks cheap."),
    (KS, "Coke still cheap after the selloff."),
)


@pytest.mark.parametrize("key, text", JOURNEY_ADVERB_VERDICTS)
def test_a_word_brand_before_an_adverb_is_a_company_in_a_price_sentence(key, text):
    assert _mentions(text), text
    _assert_rejected_everywhere(key, text, "ungrounded_entity")


# ── 3. W3OB-3 / W3OB-9: a word-brand used as a WORD — an imperative, a verb, a modifier ─────


WORD_BRAND_AS_A_WORD = (
    (AS, "Discover ways to sell without letting emotion take over."),
    (KS, "Discover tools like the P/E ratio and market cap."),
    (AS, "Discover tips for selling a stock without panic."),
    (AS, "Discover strategies for knowing when to sell."),
    (ET, "Discover funds that hold hundreds of stocks at once."),
    (SB, "Discover numbers that tell you most of a stock's story."),
    (AM, "Discover lessons from how Amazon built its moat, without buying a single share."),
    (FO, "Chase headlines and you sell at the bottom."),
    (FO, "Chase headlines, sell in a panic, and repeat."),
    (CM, "Snap decisions often mean selling at the very bottom."),
    (CM, "Snap judgments lead investors to sell in a panic."),
    (CM, "Snap decisions to sell rarely end well."),
    (ET, "Chase really cheap stocks and you get burned."),
    (ET, "Discover exactly how a low P/E can mislead you."),
    # A listed adverb after a VERB-brand is an adverb, not its verb (W3 self-review).
    (ET, "Discover just how cheap index funds are."),
    (ET, "Chase only cheap stocks and you get burned."),
    # Title Case: after a negation or a modal, after a plural subject, a numeral determiner, a
    # phrasal imperative, an imperative + adjective object.
    (FO, "Don't Chase Hot Stocks"),
    (FO, "Why Investors Chase Rising Stocks"),
    (PG, "Why You Should Zoom Out Before You Sell"),
    (ET, "One Apple Or A Basket Of Stocks?"),
    (SB, "Zoom In On The Business, Not The Stock"),
    (AS, "Block Emotional Selling With Rules"),
    # The income statement's own metaphor (W3OB-9).
    (IS, "Lemonade stands have revenue and costs too."),
    (IS, "Lemonade stands teach the income statement."),
    (IS, "What Lemonade Stands Teach"),
    (IS, "Why Lemonade Stands Explain Profit"),
    (IS, "Lemonade Stand Math: How Much Did You Sell?"),
    (IS, "The Lemonade Stand"),
)


@pytest.mark.parametrize("key, text", WORD_BRAND_AS_A_WORD)
def test_a_word_brand_used_as_a_word_is_no_company(key, text):
    assert _mentions(text) == [] or key == AM, text
    assert _field(key, text) == [], (key, text, _field(key, text))


@pytest.mark.parametrize("key, text", [
    # Was journey:art_of_selling, EXCLUDED 2026-09-26 (a post needs an eligible item); the
    # word-brand shape ("Discover" as a verb) is item-independent.
    (CM, "Discover ways to sell without letting emotion take over."),
    (FO, "Don't Chase Hot Stocks"),
    (IS, "Lemonade stands have revenue and costs too."),
])
def test_a_word_brand_used_as_a_word_reaches_a_post(key, text):
    _posted(key, text)


@pytest.mark.parametrize("key, text", [
    # The same brands as COMPANIES: a verb follows, a price word, a possessive, an instrument.
    (KS, "Target sports a low P/E."),
    (KS, "Target Trading Below Its Worth"),
    (EM, "Apple enjoys a wide moat, and it looks cheap today."),
    (IS, "Lemonade looks cheap."),
    (IS, "Lemonade stock looks cheap."),
    (IS, "Why Lemonade Looks Cheap"),
    (IS, "Lemonade's market cap is tiny."),
    (FO, "Chase Stock Looks Cheap"),
])
def test_the_same_brands_as_companies_are_still_ungrounded(key, text):
    _assert_rejected_everywhere(key, text, "ungrounded_entity")


def test_the_compound_shield_is_keyed_on_the_brand_and_the_next_word_only():
    assert _mentions("Lemonade stands have revenue.") == []
    assert _mentions("Lemonade looks cheap.") == ["lemonade"]
    assert _mentions("Lemonade's stand looks cheap.") == ["lemonade"]     # possessive
    assert _mentions("Snap decisions to sell rarely end well.") == []
    assert _mentions("Snap shares look cheap.") == ["snap"]


# ── 4. W3CB-6: the case-study idioms for a stock's performance or a verdict ─────────────────


IDIOM_BYPASSES = (
    (NV, "Data-center sales hit record highs, and so did the stock.", "class_b_valuation"),
    (NV, "Revenue soared, and so did the stock.", "class_b_valuation"),
    (NV, "Profits hit records, and the stock followed.", "class_b_valuation"),
    (NV, "Profits hit record highs, as did the shares.", "class_b_valuation"),
    (CO, "Costco is the ultimate buy-and-hold stock.", "class_b_recommendation"),
    (CO, "Costco is the perfect buy-and-hold stock.", "class_b_recommendation"),
    (CO, "Costco: The Ultimate Buy-And-Hold Stock", "class_b_recommendation"),
    (CO, "Costco is a classic buy-and-hold stock.", "class_b_recommendation"),
    # The verdict adjectives themselves, with no "buy-and-hold" ("the ultimate stock").
    (CO, "Costco is the ultimate stock.", "class_b_valuation"),
    (CO, "Costco is the quintessential company to own.", "class_b_valuation"),
    (NV, "NVIDIA is still in the early innings.", "class_b_forward"),
    (CO, "The best is yet to come for Costco.", "class_b_forward"),
    (CO, "Costco has a long runway ahead.", "class_b_forward"),
    (NV, "NVIDIA still has plenty of runway.", "class_b_forward"),
    (NV, "Anyone who bought NVIDIA early made a fortune.", "class_b_valuation"),
    (AM, "A small bet on Amazon in the early days made a fortune.", "class_b_valuation"),
    (CO, "Long-term Costco shareholders have done extremely well.", "class_b_valuation"),
    (CO, "Everyone who held Costco has done extremely well.", "class_b_valuation"),
    (NV, "NVIDIA stock was one of the decade's biggest winners.", "class_b_valuation"),
    (NV, "NVIDIA was one of the biggest winners of the AI boom.", "class_b_valuation"),
)


@pytest.mark.parametrize("key, text, code", IDIOM_BYPASSES)
def test_a_stock_performance_or_verdict_idiom_never_reaches_a_post(key, text, code):
    _assert_rejected_everywhere(key, text, code)


@pytest.mark.parametrize("key, text", [
    (NF, "When Netflix spends a fortune on a global blockbuster, it can spread that cost."),
    (NF, "Netflix emerged as one of the biggest winners of the streaming wars."),
    (CO, "Buy-and-hold investors watched Costco's membership grow."),
    (AM, "Amazon had a long runway for growth in 2000."),
    (HD, "Home Depot kept deeper stock of the boring items, and so did its rival."),
    (CO, "Costco's sales grew, and so did its membership."),
])
def test_business_prose_near_those_idioms_still_posts(key, text):
    _posted(key, text)


# ── 5. W3VAC-03 + W3CB-6: every new ALTERNATIVE of a multi-branch company row carries its own
#       weight — neutralise it alone and its sample loses the code ──────────────────────────


def _without_alternative(alt: str):
    rows = []
    hits = 0
    for code, pattern in c._COMPANY_ROWS:
        hits += pattern.count(alt)
        rows.append((code, re.compile(pattern.replace(alt, r"(?!x)x"))))
    assert hits == 1, ("the alternative must sit in exactly one row", hits)
    return tuple(rows)


@pytest.mark.parametrize("alt, key, text, code", [
    ("_CO_LABEL_PRICE_ALT", CO, "Costco: A Wide Moat At A Fair Price", "class_b_valuation"),
    ("_CO_LABEL_PRICE_ALT", NV, "NVIDIA: Dominance At A Reasonable Price", "class_b_valuation"),
    ("_CO_BUY_AND_HOLD_ALT", CO, "Costco: The Ultimate Buy-And-Hold Stock",
     "class_b_recommendation"),
    ("_CO_INNINGS_ALT", NV, "NVIDIA is still in the early innings.", "class_b_forward"),
    ("_CO_BEST_TO_COME_ALT", CO, "The best is yet to come for Costco.", "class_b_forward"),
    ("_CO_RUNWAY_ALT", CO, "Costco has a long runway ahead.", "class_b_forward"),
])
def test_removing_one_company_row_alternative_loses_its_code(alt, key, text, code, monkeypatch):
    assert code in {k for k, _d in _field(key, text)}, (text, _field(key, text))
    monkeypatch.setattr(c, "_COMPANY_ROWS_RE", _without_alternative(getattr(c, alt)))
    assert code not in {k for k, _d in _field(key, text)}, (
        f"{alt} can be removed with {text!r} still flagged {code}: another rule covers it — "
        f"pick a sample only this alternative catches")


@pytest.mark.parametrize("key, text", [
    (CO, "Costco: A Wide Moat At A Fair Price"),
    (NV, "NVIDIA: Dominance At A Reasonable Price"),
    (VI, "Visa: A Toll Road At A Fair Price"),
    # Positional: a store word elsewhere in the label is not the thing priced.
    (CO, "Costco: A Membership Moat At A Fair Price"),
])
def test_a_money_moves_label_title_at_a_fair_price_never_reaches_a_post(key, text):
    _assert_rejected_everywhere(key, text, "class_b_valuation")


@pytest.mark.parametrize("key, text", [
    # The store's price, not the stock's: a product or a customer in the label.
    (CO, "Costco: Bulk Goods At A Fair Price"),
    (NF, "Netflix: Streaming At A Fair Price"),
    (CO, "Costco: Hot Dogs At A Fair Price"),
    (CO, "Costco: A Membership At A Fair Price"),
])
def test_a_store_pricing_label_title_posts(key, text):
    _posted(key, text)


#: Strict tier-1 rows added this round: (a pattern fragment that names the row, its sample).
_NEW_TIER1_ROWS = (
    (r"(?:anyone|anybody|everyone", "Anyone who held it early made a fortune."),
    (r"(?:shareholders|stockholders|investors|owners|holders|backers)\b[^.!?\n]{0,30}?",
     "Long-term holders have done extremely well."),
    (r"(?:biggest|best|top|greatest|largest)[- ](?:stock[- ]market",
     "It was one of the decade's biggest winners."),
    (r"(?:and|as)\s+(?:so\s+)?did\s+", "Sales hit a record, and so did the stock."),
)


@pytest.mark.parametrize("fragment, sample", _NEW_TIER1_ROWS)
def test_removing_one_new_tier1_row_loses_its_code(fragment, sample, monkeypatch):
    rows = [r for r in c._CLASS_B_TIER1_RE if r[1].pattern.startswith(r"\b" + fragment)]
    assert len(rows) == 1, (fragment, len(rows))
    assert "class_b_valuation" in codes(sample, True)
    assert "class_b_valuation" not in codes(sample, False)            # a strict row
    monkeypatch.setattr(c, "_CLASS_B_TIER1_RE",
                        tuple(r for r in c._CLASS_B_TIER1_RE if r is not rows[0]))
    assert "class_b_valuation" not in codes(sample, True), sample


# ── 6. W3OB-11: inventory "stock" and the verb "shares" are no instrument move ───────────────


@pytest.mark.parametrize("text", [
    "Home Depot kept deeper stock of the boring items, and pro visits took off.",
    "Each house shares the back office, and costs went down.",
    "Visa shares the road with Mastercard, and both took off as card payments grew.",
    "Costco keeps less stock per aisle, so costs went down.",
    "Everyone shares the same warehouse, so costs went down.",
    "Where the stock is deep, visits went up.",
])
def test_inventory_stock_and_the_verb_shares_are_no_price_move(text):
    assert "class_b_valuation" not in codes(text, True), (text, codes(text, True))


@pytest.mark.parametrize("text", [
    "NVIDIA shares shot higher.", "NVIDIA stock took off.", "Shares of NVIDIA exploded.",
    "The stock of NVIDIA took off.", "NVIDIA shares a year later had tripled.",
    "NVIDIA shares the next day jumped.",
    "NVIDIA shares its lead with nobody, and the stock soared.",
])
def test_an_instrument_move_is_still_a_price_move(text):
    assert "class_b_valuation" in codes(text, True), text


# ── 7. W3CB-9: a doubling SPELLED as a verb with an investment subject is a return ───────────


DOUBLING_RETURNS = (
    "The S&P 500 can roughly double your money in about twenty-four years.",
    "Stocks roughly double in about twenty-four years.",
    "Index funds roughly double in about twenty-four years.",
    "An index fund can roughly double in about twenty-four years.",
    "The S&P 500 roughly doubled in about twenty-four years.",
    "Your money can roughly double in about twenty-four years.",
    "Stocks doubled in about twenty-four years.",
    "Your cash halves, but stocks can roughly double in about twenty-four years.",
    "The stock market can roughly double your money in about twenty-four years.",
    "Amazon shareholders doubled their money.",
    "LVMH shareholders roughly doubled their money.",
    "Investors who held Apple roughly doubled their money.",
    "Early shareholders saw their stake roughly double.",
)


@pytest.mark.parametrize("text", DOUBLING_RETURNS)
@pytest.mark.parametrize("strict", [True, False])
def test_a_spelled_doubling_of_an_investment_is_a_return_figure(text, strict):
    assert "return_figure" in codes(text, strict), text


@pytest.mark.parametrize("key, text", [
    (IT, "Stocks roughly double in about twenty-four years."),
    (IT, "The S&P 500 can roughly double your money in about twenty-four years."),
    (AM, "Amazon shareholders doubled their money."),
])
def test_a_spelled_doubling_never_reaches_a_post(key, text):
    _assert_rejected_everywhere(key, text, "return_figure")


@pytest.mark.parametrize("text", [
    "Inflation eats your savings: prices roughly double in about 24 years.",
    "At 3% inflation per year, prices can roughly double in about 24 years.",
    "Services offered roughly double the profit margin of hardware.",
    "The fund doubled its staff.",
    "Index funds doubled their share of the market.",
    "Investors doubled down on the flywheel.",
    "The number of shareholders doubled.",
    "Home Depot stocks double-size packs.",
    # A PERSON subject with no money object is a head count, not a return.
    "Retail investors doubled that year.",
    "Shareholders doubled.",
    "The fund doubled its assets under management.",
])
def test_a_doubling_that_is_no_investment_return_is_clean(text):
    for strict in (True, False):
        assert "return_figure" not in codes(text, strict), (text, strict)


def test_the_inflation_sheet_still_states_its_own_doubling():
    item = content_pool.get_item(IT)
    assert any("roughly double" in s for s in item.fact_sentences)
    _posted(IT, "Inflation eats your savings: prices roughly double in about 24 years.")


# ── 8. W3CB-10 / W3VAC-01 / W3VAC-06: a real person by role, share, or wealth ───────────────


PEOPLE_BY_ROLE = (
    (AM, "The man running Amazon reinvested every dollar."),
    (NV, "The engineer behind NVIDIA bet the company on CUDA."),
    (NV, "The engineer who runs NVIDIA bet the company on CUDA."),
    (MS, "The architect of Microsoft's turnaround bet everything on Azure."),
    (MS, "A new leader took over Microsoft and bet on Azure."),
    (LV, "LVMH's controlling shareholder bought Tiffany."),
    (TE, "Tesla's outspoken frontman called it production hell."),
    (ME, "Meta's controlling shareholder bet the company on the metaverse."),
    (ME, "Its largest shareholder still controls the vote."),
    (LV, "LVMH's owner is one of the richest people in the world."),
    (LV, "LVMH made its owner one of the wealthiest men alive."),
    (LV, "The group is run by the wealthiest man in France."),
    (TE, "The force behind Tesla slept on the factory floor."),
    (TE, "The driving force behind Tesla slept on the factory floor."),
    (TE, "The one in charge slept on the factory floor."),
    (TE, "Everyone trusted its leader."),
    # A trade after a copula keeps the person: the thing-head exemption is for a THING subject.
    (TE, "A South African engineer is the force behind Tesla."),
)


@pytest.mark.parametrize("key, text", PEOPLE_BY_ROLE)
def test_a_real_person_by_role_share_or_wealth_never_reaches_a_post(key, text):
    _assert_rejected_everywhere(key, text, "person_named")


@pytest.mark.parametrize("text", [
    # W3VAC-06: every wealth word is pinned by its OWN sample, asserted on person_named (not
    # "any violation": a year in the sentence would mask the mutant).
    "The group is run by the wealthiest man in France.",
    "Its wealthiest owner bought brand after brand.",
    "One of the world's wealthiest investors called it a moat.",
    "The richest man in France bought brand after brand.",
])
@pytest.mark.parametrize("strict", [True, False])
def test_a_wealth_epithet_is_a_person_in_both_modes(text, strict):
    assert "person_named" in codes(text, strict), text


@pytest.mark.parametrize("key, text", [
    # W3OB-2: a head a THING can hold, after a copula, describes the copula's subject.
    (AM, "Scale is the force behind the flywheel."),
    (AM, "AWS is the force behind Amazon's low prices."),
    (CO, "The membership fee is the force behind Costco's low prices."),
    (NV, "CUDA is the force behind NVIDIA's moat."),
    (NF, "Data was the force behind Netflix's hits."),
    (VI, "Software was the brains behind the toll booth."),
    (VI, "Your bank is the one in charge of the loan."),
    (VI, "The bank is the one calling the shots on credit."),
    # A market's leader at the end of a clause is a company.
    (NF, "Netflix became streaming's leader."),
    (LV, "LVMH became luxury's leader."),
    (NV, "NVIDIA became AI's leader."),
    (AM, "Amazon became e-commerce's leader."),
    (CO, "Costco became warehouse retail's leader."),
    # A bare owner is the company (the LVMH sheet's own thesis), and so is a company holder.
    (LV, "A luxury brand can be destroyed by its owner."),
    (LV, "Tiffany kept its name after its owner changed."),
    (CO, "The architect of Costco's model was the membership fee."),
    (TT, "A newcomer who took over the market with a better product."),
])
def test_roles_a_thing_or_a_company_holds_still_reach_a_post(key, text):
    _script_ok(key, text)


# ── 9. W3CB-12: testimonials by a customer-shaped noun, and THIS content changing a crowd ───


TESTIMONIALS = (
    "A subscriber said this lesson changed how she invests.",
    "This lesson has changed how thousands of people invest.",
    "Beginners everywhere are loving this simple rule.",
    "A subscriber told us this rule changed how they save.",
    "One viewer wrote in to say this lesson finally made ETFs click.",
    "A member told us this lesson changed how she invests.",
    "A student said this rule made investing finally make sense.",
    "This rule has transformed how thousands of beginners invest.",
    "Thousands of people have changed how they invest after this lesson.",
    "Beginners are loving this simple rule.",
)


@pytest.mark.parametrize("text", TESTIMONIALS)
@pytest.mark.parametrize("strict", [True, False])
def test_a_testimonial_or_social_proof_is_an_endorsement(text, strict):
    assert "endorsement" in codes(text, strict), text


@pytest.mark.parametrize("key, text", [
    (ET, "A subscriber told us this rule changed how they save."),
    (CO, "This lesson has changed how thousands of people invest."),
])
def test_a_testimonial_never_reaches_a_post(key, text):
    _assert_rejected_everywhere(key, text, "endorsement")


@pytest.mark.parametrize("text", [
    "This habit has changed how many people think about saving.",
    "A follower shared the video with friends.",
    "A member renewed every year.",
    "Members love the treasure hunt.",
])
def test_customers_and_questions_are_no_testimonial(text):
    for strict in (True, False):
        assert "endorsement" not in codes(text, strict), (text, strict)


def test_the_helped_row_is_about_this_content_not_a_strategy():
    """Round-2's "helped" row used the strategy/system nouns a case study also has."""
    assert "endorsement" not in codes("This strategy helped millions of people save.", True)
    assert "endorsement" in codes("This lesson helped millions of people save.", True)


# ── 10. W3VAC-02: a trade TIMED on prices or moods is a directive ────────────────────────────


TIMED_TRADES = (
    "When his fear leads to low prices, you might find a chance to buy.",
    "His fear can present opportunities to buy.",
    "When his greed makes prices high, you can choose to pass or sell.",
    "When his greed pushes prices high, you might choose to sell or simply pass.",
    "When his fear makes prices low, you can choose to buy.",
    "When his fear makes a bargain, you can buy.",
    "His fear can create a bargain, and his greed can signal a time to pass or sell.",
    "When the market drops, it may be a chance to buy.",
)


@pytest.mark.parametrize("text", TIMED_TRADES)
@pytest.mark.parametrize("strict", [True, False])
def test_a_timed_trade_is_a_recommendation(text, strict):
    assert "class_b_recommendation" in codes(text, strict), text


def test_the_timed_trade_is_gone_from_its_own_fact_sheet_and_the_item_stays():
    """The Mr. Market SOURCE says "When his fear offers you a real bargain, you can buy." — the
    line the model kept echoing. The sheet scrub drops it now (as it dropped the ETF imperative);
    the lesson stays eligible on the rest of its sheet."""
    item = content_pool.get_item(MR)
    assert item is not None and item.eligible
    assert not any("you can buy" in s.lower() for s in item.fact_sentences)
    assert any("mr. market" in s.lower() for s in item.fact_sentences)


def test_a_timed_trade_never_reaches_a_post():
    _assert_rejected_everywhere(MR, "His fear can present opportunities to buy.",
                                "class_b_recommendation")


@pytest.mark.parametrize("text", [
    "When Mr. Market is thrilled, he might offer to buy your shares at a very high price, if "
    "you choose to sell.",
    "Every day Mr. Market gives you a chance to buy or sell.",
    "You are never forced to buy or sell.",
    "You can choose to pass.",
    "When his fear makes prices low, you can consider the situation.",
    "When his fear leads to low prices, you might find a chance to assess.",
    "Never treat a dip as a chance to buy.",
    "Most investors focus on when to buy.",
    "Knowing when to sell is the hardest part.",
    "With an ETF, you can buy hundreds of companies at once.",
])
def test_the_parable_and_the_choice_are_no_timed_trade(text):
    for strict in (True, False):
        assert "class_b_recommendation" not in codes(text, strict), (text, strict)


# ── 11. W3CB-11 / W3OB-10: an amount binds to what IT is the price of ───────────────────────


@pytest.mark.parametrize("key, text", [
    (LV, "LVMH bought Bulgari for about $15.8 billion."),
    (LV, "LVMH acquired Bulgari for $15.8 billion."),
    (LV, "LVMH spent about $15.8 billion on Fendi."),
    (LV, "LVMH bought Dior for $15.8 billion."),
    (LV, "In 2011 LVMH bought Bulgari for $15.8 billion."),
    (LV, "LVMH paid about $15.8 billion for Celine."),
    (LV, "Bulgari became part of LVMH for roughly $15.8 billion."),
    (NV, "NVIDIA paid about $6.9 billion for research."),
    # The widened acquisition class never binds a present-day or a holder's price.
    (NV, "Mellanox would cost about $6.9 billion today."),
    (NV, "The Mellanox deal cost NVIDIA shareholders about $6.9 billion."),
    (MS, "GitHub would cost Microsoft about $7.5 billion today."),
])
def test_an_amount_rebound_to_another_company_or_to_today_is_refused(key, text):
    item = content_pool.get_item(key)
    got = [v.code for v in g.check_grounding("x", text, item.grounding)]
    assert "number_context" in got, (key, text, got)


@pytest.mark.parametrize("key, text", [
    (LV, "LVMH paid about $15.8 billion for Tiffany."),
    (LV, "The group added Tiffany for $15.8 billion."),
    (LV, "LVMH added Bulgari, then Tiffany for roughly $15.8 billion."),
    (LV, "LVMH bought Bulgari in 2011."),
    (LV, "Tiffany became part of LVMH in 2021 for roughly $15.8 billion."),
    (LV, "Tiffany joined in 2021 for roughly $15.8 billion."),
    (LV, "Examples include Dior, Fendi, Celine, and Tiffany, acquired for about $15.8 billion "
         "in 2021."),
    (NV, "NVIDIA paid roughly 6.9 billion dollars for Mellanox."),
    (NV, "NVIDIA picked up Mellanox for about $6.9 billion."),
    (NV, "The Mellanox deal cost NVIDIA about $6.9 billion."),
    (MS, "GitHub cost Microsoft roughly $7.5 billion in 2018."),
    (MS, "In 2018, Microsoft picked up GitHub for about $7.5 billion."),
    (MS, "The GitHub deal cost about $7.5 billion."),
])
def test_an_honest_deal_restatement_grounds(key, text):
    item = content_pool.get_item(key)
    assert g.check_grounding("x", text, item.grounding) == [], (key, text)


def test_a_names_only_amount_carries_only_its_own_clauses_names():
    row = [f for f in content_pool.get_item(LV).grounding.numbers
           if f.names_only and f.raw.strip().startswith("$15.8")]
    assert row and all(f.clause_names == {"tiffany"} for f in row), [
        (f.raw, sorted(f.local), sorted(f.clause_names)) for f in row]


# ── 12. W3VAC-08: two round-2 content edits, pinned on the rule that owns them ───────────────


@pytest.mark.parametrize("text", ["NVIDIA's stock is a compelling story.",
                                  "Costco shares are a compelling story for investors."])
@pytest.mark.parametrize("strict", [True, False])
def test_a_compelling_story_about_a_stock_is_a_verdict(text, strict):
    assert "class_b_evaluative" in codes(text, strict), text


@pytest.mark.parametrize("text", [
    # Verbs only `_INVESTOR_GROWTH_RE` carries ("averaged", "beaten"), so the subject list is
    # what the test pins — "returned" alone is a return word and would mask the mutant.
    "The stock market has averaged about 10% a year.",
    "Stock markets have beaten prices by three percent a year.",
])
@pytest.mark.parametrize("strict", [True, False])
def test_the_stock_market_is_a_return_subject_for_compliance_alone(text, strict):
    assert "return_figure" in codes(text, strict), text


def test_the_stock_market_return_never_reaches_a_post_on_the_inflation_lesson():
    _assert_rejected_everywhere(IT, "Stock markets have beaten prices by three percent a year.",
                                "return_figure")


# ── 13. W3VAC-10 / W3-SWW-3: X counts a glued abbreviation as the text it is ─────────────────


@pytest.mark.parametrize("text, expected", [
    ("U.S.dollar", 10), ("e.g.the", 7), ("i.e.the", 7), ("vs.the", 6), ("etc.and", 7),
    ("the U.S.economy grew", 20), ("Visa vs.the rest.", 17),
    # A real gTLD after the glue: X DOES autolink these, so they keep the URL weight.
    ("U.S.markets", 23), ("U.S.market", 23), ("e.g.bank", 23), ("i.e.one", 23),
    ("e.g.you", 23),
    # Not an abbreviation run at all: a typo X links, and the validator rejects.
    ("sell.then", 23), ("investor.gov", 23),
])
def test_x_counts_only_what_it_autolinks_as_a_url(text, expected):
    assert pc.x_weighted_length(text) == expected, text


def test_a_glued_abbreviation_no_longer_drops_the_x_post():
    item, pkg = _baseline("journey:compound_interest")
    budget = pc.body_budget("x", item.category, RUN_DATE)
    sentence = "Compounding rewards time, i.e.the habit matters."
    body = sentence
    while len(body) + 1 + len(sentence) <= budget:
        body += " " + sentence
    # Glued, the body fits by X's own count; weighed as URLs it would not.
    assert pc.x_weighted_length(body) == len(body) <= budget
    assert len(body) + body.count("i.e.the") * (23 - len("i.e.the")) > budget
    res = ws.validate_package(_with(pkg, "x", body), item, RUN_DATE)
    assert "x" in res.posts, [(v.code, v.detail) for v in res.outlets.get("x", [])]


# ── 14. the prompt and the repair hints state what the entity rules enforce ──────────────────


@pytest.mark.parametrize("phrase", [
    "the man running it", "the engineer behind it", "a new leader took over",
    "its controlling shareholder", "one of the richest people", "early innings",
    "a long runway", "the best yet to come", "buy-and-hold stock", "and so did the stock",
    "made a fortune", "shareholders have done well", "biggest winners",
    "never tie a trade to prices or moods", "doubles or triples", "subscriber, viewer, member",
    "changed how thousands invest",
])
def test_the_system_body_states_the_round_3_entity_rules(phrase):
    assert phrase in wp.SYSTEM_BODY.lower(), phrase


@pytest.mark.parametrize("code, phrase", [
    ("person_named", "the man running it"), ("person_named", "controlling shareholder"),
    ("class_b_valuation", "and so did the stock"), ("class_b_valuation", "biggest winners"),
    ("class_b_recommendation", "buy-and-hold"), ("class_b_recommendation", "a chance to buy"),
    ("class_b_forward", "early innings"), ("return_figure", "doubles or triples"),
    ("endorsement", "subscriber"),
])
def test_the_repair_hints_name_the_round_3_entity_rules(code, phrase):
    assert phrase in wp.REPAIR_HINTS[code].lower(), (code, phrase)


def test_the_prompt_version_moved_for_the_entity_rules():
    assert wp.PROMPT_VERSION >= "2026-09-24.5"
