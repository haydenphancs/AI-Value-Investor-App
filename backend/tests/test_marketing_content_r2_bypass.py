"""
Round-2 content bypasses (W2 review of the round-1 fix pass), end to end.

Round 1 closed each bypass with rows, and round 2 found the rows were closed LISTS with a
bag-of-words exemption: a negation anywhere in the sentence ("Don't panic, the market always
recovers"), a conditional wrapped around a main-clause prediction ("If you stay patient, the
S&P 500 will keep climbing"), double quotes around a promise, a word-brand in a Title-Case title,
and every synonym the lists did not name. The fixes are structural, and each group below pins
both directions:

* MUST-FLAG strings — the review's repro plus the natural variants — through `scan_text` in both
  modes and, for the ones that reach a post, through `writer_service.validate_package` as the
  shared hook (the package is rejected) and as the X caption (the X post is dropped).
* MUST-PASS strings — the honest copy a too-wide fix would reject, asserted as an EMPTY
  violation list (not the absence of one code, which is how a guard goes vacuous).

The real-model false-positive guard is `test_marketing_content_r2_real_drafts.py`; the
per-row anti-vacuity guard is `test_marketing_content_r2_row_weight.py`.

Category 1 (pure): the Learn bundle and the vendored lists only.
"""

from __future__ import annotations

from typing import List

import pytest

from app.services.marketing import compliance as c
from app.services.marketing import writer_service as ws
from test_marketing_content_a_writer_gate import (RUN_DATE, _assert_rejected_everywhere,
                                                  _baseline, _with)


def codes(text: str, strict: bool) -> List[str]:
    return [v.code for v in c.scan_text("x", c.clean(text), strict_instruments=strict)]


def _both_modes_empty(text: str) -> None:
    for strict in (True, False):
        got = [(v.code, v.detail) for v in c.scan_text("x", c.clean(text),
                                                        strict_instruments=strict)]
        assert got == [], (text, strict, got)


def _posted(key: str, text: str) -> None:
    item, pkg = _baseline(key)
    res = ws.validate_package(_with(pkg, "x", text), item, RUN_DATE)
    assert "x" in res.posts, (key, text, [(v.code, v.detail) for v in res.outlets.get("x", [])])


# ── 1. clause scope: a negation in ANOTHER clause exempts nothing (W2CB-1, W2V-01) ──────────


SCOPED_PROMISES = (
    "Don't panic, the market always recovers.",
    "Don't panic: the market always recovers.",
    "Don't wait: compounding guarantees your money grows.",
    "Don't overthink it: an index fund is a safe way to grow your money.",
    "Not every year is good, but the market always recovers.",
    "Nobody likes a crash, yet stocks always go up in the end.",
    "Don't worry about the dips, your money is protected.",
    "It's not hard: stocks always go up.",
    "It isn't luck: compounding will make you wealthy.",
    "Don't sell in a panic; the market has always recovered.",
    "No need to worry: stocks always go up over time.",
    "You can't time the market, but it always recovers.",
    "Don't sell and you'll never lose money.",
    # A negated or labelled warning frame frames nothing (W2-OB-3's bypass half).
    "It's not a myth: index funds always go up.",
    "Believe it or not, index funds always go up.",
    "No joke, index funds always go up.",
    "No investment, however diversified, is without risk, and stocks always go up.",
    "Index funds always go up, and that is not a myth.",
    # A negation that AFFIRMS the claim exempts nothing.
    "It is not a myth that stocks always go up.",
    "It's no secret that the market always recovers.",
)


@pytest.mark.parametrize("text", SCOPED_PROMISES)
@pytest.mark.parametrize("strict", [True, False])
def test_a_negation_in_another_clause_does_not_exempt_a_promise(text, strict):
    assert "promissory" in codes(text, strict), text


SCOPED_FORECASTS = (
    "Never mind the headlines: the market will keep climbing.",
    "You can't time it, but the S&P 500 will keep climbing.",
    "If you stay patient, the S&P 500 will keep climbing.",
    "If you just hold on, the market will recover.",
    "Whether you like it or not, the market will recover.",
    "Ignore the hype: the market will go higher.",
    "If history is any guide, stocks will keep climbing.",
    "Even if it hurts, the market will recover.",
    "Unless a recession hits, the market will keep climbing.",
    "Crypto scams are everywhere, but the market will keep climbing.",
    "Stay the course and the market will reward you.",
    # A conditional in the SAME clause is still a conditional, never a doubt ("nobody knows
    # if"): only the complementiser after a doubt/report verb exempts.
    "If you wait the market will recover.",
    "Unless you panic the market will keep climbing.",
    "No doubt the market will recover.",
    "There's no question the market will keep climbing.",
)


@pytest.mark.parametrize("text", SCOPED_FORECASTS)
@pytest.mark.parametrize("strict", [True, False])
def test_a_conditional_or_negation_in_another_clause_does_not_exempt_a_forecast(text, strict):
    assert "class_b_forward" in codes(text, strict), text


@pytest.mark.parametrize("key, text, code", [
    ("journey:power_of_discipline", "Don't panic, the market always recovers.", "promissory"),
    ("journey:compound_interest", "Don't wait: compounding guarantees your money grows.",
     "promissory"),
    ("journey:fomo_cycle", "No need to worry: stocks always go up over time.", "promissory"),
    ("journey:etfs_101", "If you stay patient, the S&P 500 will keep climbing.",
     "class_b_forward"),
    ("journey:power_of_discipline", "If you just hold on, the market will recover.",
     "class_b_forward"),
])
def test_scoped_promises_and_forecasts_never_reach_a_post(key, text, code):
    _assert_rejected_everywhere(key, text, code)


#: The claim's OWN clause negates, doubts or reports it, or a belief/speech verb governs it
#: across commas: every one of these must stay clean in both modes.
SCOPED_LEGIT = (
    "Nobody can promise the market always recovers.",
    "Don't assume the market always recovers.",
    "Don't assume, as many do, that the market always recovers.",
    "Never assume that, over time, stocks always go up.",
    "Beware anyone who says, the market always recovers.",
    "Nobody knows whether, after a crash, the market will recover.",
    "Nobody knows if the market will keep climbing.",
    "Nobody knows whether the market will rise or fall next year.",
    "Beware anyone who says a crash is coming.",
    "It is a myth that stocks always go up.",
    "Stocks always going up is a myth.",
    "If someone promises big rewards with no risk at all, that combination doesn't exist.",
    "You simply can't have high reward with zero risk.",
    'The words "guaranteed high returns" aren\'t a gift.',
    "No investment, however diversified, is without risk.",
    "If you invest regularly, your money will grow with the market over decades.",
    "When prices wobble, and they always will, stay calm.",
    "Neither stocks nor bonds always go up.",
    "It's unclear whether the market will recover this year.",
    "Diversification spreads risk, but it doesn't erase it.",
    "Markets have always risen and fallen.",
    "Stocks have always gone up and down.",
    "Don't lose out on decades of compounding.",
    "No one wants to lose money.",
    "Nobody likes to lose money, so many sell in a panic.",
    "Never fail to read the annual report.",
    "The market rewards patience over decades.",
)


@pytest.mark.parametrize("text", SCOPED_LEGIT)
def test_a_negation_or_doubt_in_the_claims_own_clause_still_exempts_it(text):
    got = [v.code for v in c.scan_text("x", c.clean(text), strict_instruments=False)]
    assert got == [], (text, got)


# ── 2. quotes: no exemption for a quoted promise or a quoted testimonial ─────────────────────


@pytest.mark.parametrize("text, code", [
    ('Remember the rule: "the market always recovers."', "promissory"),
    ('Long-term investors know "stocks always go up."', "promissory"),
    ('"We have never lost money this way."', "promissory"),
    ('"We have never lost money this way."', "first_person"),
    ('One reader put it best: "This lesson changed how I invest."', "first_person"),
    ('A beginner told us: "I finally stopped panic selling."', "first_person"),
    ('"This changed how I invest," says one reader.', "first_person"),
])
def test_quote_marks_do_not_hide_a_promise_or_a_testimonial(text, code):
    assert code in codes(text, True) and code in codes(text, False), text


@pytest.mark.parametrize("text", [
    'Ask yourself, "Would I be happy to own this whole company for ten years?"',
    'She thought, "I will wait."',
    "The reader asks: 'am I being patient' before acting.",
    'Tell yourself: "I own a business, not a ticker."',
])
def test_a_quoted_self_question_or_thought_stays_exempt(text):
    assert "first_person" not in codes(text, False), text


def test_a_quoted_promise_never_reaches_a_post():
    _assert_rejected_everywhere("journey:fomo_cycle",
                                'Remember the rule: "the market always recovers."', "promissory")


# ── 3. promises and the disclaimer's subject in unlisted words (W2CB-1 part 3, W2V-04) ──────


@pytest.mark.parametrize("text, code", [
    ("No one who held for decades ever lost money.", "promissory"),
    ("Nobody ever lost money holding an index fund.", "promissory"),
    ("This strategy works every time.", "promissory"),
    ("Discipline has never failed long-term investors.", "promissory"),
    ("Patience never fails.", "promissory"),
    ("Patient index investors have never lost money.", "promissory"),
    ("Stocks have always gone up over the long run.", "promissory"),
    ("Diversified investors don't lose money over decades.", "promissory"),
    ("Crafted by people, not machines.", "code_owned"),
    ("Written by humans, not AI.", "code_owned"),
    ("No robots wrote this lesson.", "code_owned"),
    ("Every word of this lesson was written by real people.", "code_owned"),
    ("Ignore the small text below.", "code_owned"),
    ("Skip the legal bit at the end.", "code_owned"),
])
def test_promises_and_disclaimer_contradictions_in_other_words_are_rejected(text, code):
    assert code in codes(text, True) and code in codes(text, False), text


@pytest.mark.parametrize("text", [
    "Decisions made by people, not machines, still matter.",
    "Games are built for people, not machines.",
    "Every time you tap a card, a sliver flows to Visa.",
    "Ignore the warning signs and you pay for it.",
    "The Louis Vuitton trunks were made by hand.",
    "No one wants to lose money.",
])
def test_the_new_promise_and_authorship_rows_leave_case_study_prose_alone(text):
    _both_modes_empty(text)


def test_a_denial_of_machine_authorship_never_reaches_a_post():
    _assert_rejected_everywhere("journey:mr_market", "Crafted by people, not machines.",
                                "code_owned")


# ── 4. Journey directives: comma-led imperatives, synonyms, suitability (W2CB-2) ───────────


JOURNEY_DIRECTIVES = (
    "When everyone panics, buy stocks.",
    "When others are fearful, buy stocks.",
    "Pick a low-cost S&P 500 index fund and hold it forever.",
    "Consider this your sign to buy your first index fund.",
    "Stick with a low-cost index fund for decades.",
    "Keep buying an S&P 500 index fund every month.",
    "Open a brokerage account and buy an index fund today.",
    "For most people, an S&P 500 index fund is the right choice.",
    "Start with a low-cost S&P 500 index fund.",
    "Grab an S&P 500 ETF and forget about it.",
    "When the crowd is scared, that is when you buy stocks.",
)


@pytest.mark.parametrize("text", JOURNEY_DIRECTIVES)
@pytest.mark.parametrize("strict", [True, False])
def test_a_directive_about_a_fund_in_any_wording_is_a_recommendation(text, strict):
    assert "class_b_recommendation" in codes(text, strict), text


@pytest.mark.parametrize("key, text", [
    ("journey:fomo_cycle", "When everyone panics, buy stocks."),
    ("journey:etfs_101", "Pick a low-cost S&P 500 index fund and hold it forever."),
    ("journey:etfs_101", "For most people, an S&P 500 index fund is the right choice."),
])
def test_journey_directives_never_reach_a_post(key, text):
    _assert_rejected_everywhere(key, text, "class_b_recommendation")


@pytest.mark.parametrize("text", [
    "If you buy one ETF, you own a slice of all of them.",
    "When you buy one ETF, you own a slice of every company in it.",
    "Investors often buy stocks at the top.",
    "When prices rise, people pile into stocks at the top.",
    "When fear spikes, many investors sell stocks at the bottom.",
    "In 1999, investors piled into tech stocks.",
    "Don't sell in a panic.",
    "Water your winners, prune the weak ones, and let your garden grow.",
    "Consider trimming holdings if their story has truly broken, freeing space for better "
    "opportunities.",
    "That's why many people use a broad ETF as the calm, steady core of their plan, then add "
    "a few single stocks around it.",
])
def test_descriptions_of_what_investors_do_are_not_directives(text):
    got = [v.code for v in c.scan_text("x", c.clean(text), strict_instruments=False)]
    assert got == [], (text, got)


# ── 5. Journey: a word-brand is a company wherever its sentence talks price (W2CB-3) ────────


@pytest.mark.parametrize("key, text", [
    ("journey:economic_moats", "Apple enjoys a wide moat, and it looks cheap today."),
    ("journey:key_statistics", "Apple At A Bargain Price"),
    ("journey:mr_market", "Mr. Market Is Selling Apple Cheap"),
    ("journey:key_statistics", "Target: low P/E, solid dividend."),
    ("journey:economic_moats", "Lemonade looks cheap."),
    ("journey:key_statistics", "Is Apple A Bargain Right Now?"),
    ("journey:key_statistics", "Is Coke Cheap Right Now?"),
    ("journey:economic_moats", "Coke enjoys a wide moat and a great price."),
    ("journey:key_statistics", "Buy A Slice Of Apple"),
    ("journey:key_statistics", "Target Trading Below Its Worth"),
    ("journey:economic_moats", "Coke: A Wide Moat At A Fair Price"),
    ("journey:key_statistics", "Target sports a low P/E."),
    ("journey:mr_market", "Mr. Market Hands You Apple On Sale"),
    ("journey:economic_moats", "Apple boasts a wide moat and a fair price."),
])
def test_a_word_brand_in_a_price_sentence_is_an_ungrounded_company_in_journey(key, text):
    _assert_rejected_everywhere(key, text, "ungrounded_entity")


@pytest.mark.parametrize("key, text", [
    ("journey:etfs_101", "Don't Chase Hot Returns"),
    ("journey:etfs_101", "Shell Out Less On Fees"),
    ("journey:etfs_101", "Target-Date Funds Are Cheap To Own"),
    ("journey:mr_market", "Mind The Gap Between Price And Value"),
    ("journey:mr_market", "Discover how a stock's price can differ from its value."),
    ("journey:etfs_101", "Chase the cheap stocks and you get burned."),
    ("journey:key_statistics", "Target a low P/E and a solid dividend."),
    # After an article the brand is the word, even beside price vocabulary.
    ("journey:mr_market", "Mind The Gap: Cheap Is Not The Same As Good"),
    # A headline imperative before a capitalised object or wh-word.
    ("journey:etfs_101", "Discover Why Index Funds Beat Buying Single Stocks"),
    ("journey:etfs_101", "Square Your Budget Before You Buy Stocks"),
])
def test_a_word_brand_used_as_a_word_still_passes(key, text):
    _posted(key, text)


def test_the_verdict_rule_marks_the_brand_only_with_price_vocabulary():
    """Detection, not just a rejected post: the same Title-Case shape without price words is
    no company, and with them it is."""
    assert c.company_mentions(c.skeleton("Apple At A Bargain Price")) == ["apple"]
    assert c.company_mentions(c.skeleton("Apple At The Heart Of It")) == []
    assert c.company_mentions(c.skeleton("Target: low P/E, solid dividend.")) == ["target"]
    assert c.company_mentions(c.skeleton("Target: save a little every month.")) == []


# ── 6. Money Moves: the case-study company's opinion, forecast, move and worth (W2CB-4/5,
#       W2V-02) ────────────────────────────────────────────────────────────────────────────

MM = "money_moves:"


@pytest.mark.parametrize("key, text, code", [
    # A value verdict in words the rows never listed.
    ("costcos-membership-magic", "Costco's stock is a great deal.", "class_b_valuation"),
    ("costcos-membership-magic", "Costco's shares still look reasonable.", "class_b_valuation"),
    ("costcos-membership-magic", "Costco's premium is justified.", "class_b_valuation"),
    ("costcos-membership-magic", "Costco is a no-brainer.", "class_b_valuation"),
    ("costcos-membership-magic", "Costco: A Great Deal For Investors", "class_b_valuation"),
    ("visa-vs-mastercard", "Visa has a wide moat and a great price.", "class_b_valuation"),
    ("visa-vs-mastercard", "Visa shares offer a steady dividend yield.", "class_b_valuation"),
    # A recommendation: a place in a portfolio, the best thing to own, a small stake.
    ("costcos-membership-magic", "Costco deserves a place in every portfolio.",
     "class_b_recommendation"),
    ("costcos-membership-magic", "Costco belongs in every long-term portfolio.",
     "class_b_recommendation"),
    ("costcos-membership-magic", "Costco has a place in any long-term portfolio.",
     "class_b_recommendation"),
    ("costcos-membership-magic", "Make room for Costco in your portfolio.",
     "class_b_recommendation"),
    ("visa-vs-mastercard", "Every portfolio needs a little Visa.", "class_b_recommendation"),
    ("visa-vs-mastercard", "Your portfolio needs Visa.", "class_b_recommendation"),
    ("nvidias-ai-dominance", "Why not own a little NVIDIA?", "class_b_recommendation"),
    ("costcos-membership-magic", "Own a piece of Costco.", "class_b_recommendation"),
    ("nvidias-ai-dominance", "NVIDIA deserves a place in your portfolio.",
     "class_b_recommendation"),
    ("apples-services-revolution", "Apple is a cash machine you want in your portfolio.",
     "class_b_recommendation"),
    ("costcos-membership-magic", "Costco is a stock you can hold for decades.",
     "class_b_recommendation"),
    ("visa-vs-mastercard", "Visa is the kind of business long-term investors dream of owning.",
     "class_b_recommendation"),
    ("visa-vs-mastercard", "Visa and Mastercard are two of the best businesses to own.",
     "class_b_recommendation"),
    ("how-amazon-built-its-moat", "Amazon is one of the greatest compounders of all time.",
     "class_b_recommendation"),
    ("tsmc-the-foundry-that-runs-the-world", "TSMC is a wonderful thing to own.",
     "class_b_recommendation"),
    ("costcos-membership-magic", "Never sell Costco.", "class_b_recommendation"),
    ("costcos-membership-magic", "Costco is one to hold forever.", "class_b_valuation"),
    # A forecast that the run continues.
    ("nvidias-ai-dominance", "NVIDIA's run is far from over.", "class_b_forward"),
    ("nvidias-ai-dominance", "NVIDIA has years of growth ahead.", "class_b_forward"),
    ("costcos-membership-magic", "Costco can keep compounding for decades.", "class_b_forward"),
    ("apples-services-revolution", "Apple's best years may still be ahead.", "class_b_forward"),
    ("tsmc-the-foundry-that-runs-the-world", "TSMC's lead should last for years.",
     "class_b_forward"),
    ("nvidias-ai-dominance", "NVIDIA looks unstoppable.", "class_b_forward"),
    # A stock move, a record, a market cap or a return, in unlisted words.
    ("nvidias-ai-dominance", "NVIDIA shares shot higher.", "class_b_valuation"),
    ("nvidias-ai-dominance", "NVIDIA stock took off.", "class_b_valuation"),
    ("nvidias-ai-dominance", "Shares of NVIDIA exploded.", "class_b_valuation"),
    ("nvidias-ai-dominance", "NVIDIA's shares went through the roof.", "class_b_valuation"),
    ("nvidias-ai-dominance", "The shares shot up as data centers bought every chip.",
     "class_b_valuation"),
    ("costcos-membership-magic", "Renewals above 90% have lifted the shares for decades.",
     "class_b_valuation"),
    ("nvidias-ai-dominance", "NVIDIA kept hitting record highs as demand grew.",
     "class_b_valuation"),
    ("costcos-membership-magic", "Costco set record highs as membership grew.",
     "class_b_valuation"),
    ("costcos-membership-magic", "It hit record highs as sales soared.", "class_b_valuation"),
    ("nvidias-ai-dominance", "NVIDIA became the most valuable chipmaker on the planet.",
     "class_b_valuation"),
    ("how-amazon-built-its-moat", "Amazon went on to become one of the most valuable brands on "
                                  "Earth.", "class_b_valuation"),
    ("how-amazon-built-its-moat", "Amazon grew into one of the largest companies in history.",
     "class_b_valuation"),
    ("nvidias-ai-dominance", "NVIDIA stock was one of the best performers of the decade.",
     "class_b_valuation"),
    ("how-amazon-built-its-moat", "A small bet on Amazon in the early days turned into a "
                                  "fortune.", "class_b_valuation"),
])
def test_a_money_moves_opinion_on_its_own_company_never_reaches_a_post(key, text, code):
    _assert_rejected_everywhere(MM + key, text, code)


@pytest.mark.parametrize("key, text", [
    ("costcos-membership-magic", "Costco's membership fees hit record highs."),
    ("nvidias-ai-dominance", "NVIDIA's data-center sales hit record highs."),
    ("nvidias-ai-dominance", "NVIDIA posted record highs in data-center sales."),
    ("netflix-vs-disney-plus", "Disney owns the most valuable library of stories in "
                               "entertainment."),
    ("costcos-membership-magic", "Costco can keep prices low for members."),
    ("costcos-membership-magic", "Costco is a great deal for members."),
    ("costcos-membership-magic", "Costco sells goods at a fair price."),
    ("costcos-membership-magic", "Costco kept the hot dog combo at a fair price for decades."),
    ("visa-vs-mastercard", "Visa owns a piece of critical infrastructure."),
    ("the-rise-of-lvmh", "LVMH is a portfolio of brands."),
    ("tesla-vs-traditional-auto", "Tesla ramped production higher."),
    ("nvidias-ai-dominance", "NVIDIA moved up the value chain."),
    ("costcos-membership-magic", "Costco pushed renewals higher."),
    ("netflix-vs-disney-plus", "When Netflix spends a fortune on a global blockbuster, it can "
                               "spread that cost."),
    ("amd-vs-intel-the-cpu-wars", "AMD's best-performing chips came from outsourcing."),
    # Round-2 self-review: honest case-study sentences a first cut of these rows rejected.
    ("the-rise-of-tiktok-vs-instagram-reels", "On Instagram, each post came from friends, not an "
                                              "algorithm."),
    ("the-rise-of-lvmh", "Each piece is made by hand in France."),
    ("netflix-vs-disney-plus", "Netflix stopped letting members share with someone outside the "
                               "home."),
    ("how-amazon-built-its-moat", "Amazon's plan for part two of the rollout was warehouses."),
    ("the-rise-of-tiktok-vs-instagram-reels", "A follower shared the video with friends."),
    ("costcos-membership-magic", "Costco is one people keep coming back to."),
    ("costcos-membership-magic", "Costco is a company people buy from in bulk."),
    ("apples-services-revolution", "Apple is one of the greatest companies of all time."),
    ("tesla-vs-traditional-auto", "Tesla's lead ahead of legacy automakers came from software."),
    ("costcos-membership-magic", "Costco's premium is worth it for frequent shoppers."),
    ("netflix-vs-disney-plus", "Disney bought Pixar at a fair price."),
    ("costcos-membership-magic", "Most investors want Costco to keep prices low."),
])
def test_case_study_business_history_still_reaches_a_post(key, text):
    _posted(MM + key, text)


def test_a_company_holding_a_makers_role_is_not_a_person():
    for text in ("TikTok's creator, ByteDance, built the algorithm.",
                 "TikTok's owner ByteDance built the algorithm.",
                 # A role noun in ROLE position, held by a company (the group's biggest brand).
                 "LVMH's leader, Louis Vuitton, still drives most of the profit."):
        assert "person_named" not in codes(text, True), text


@pytest.mark.parametrize("text", [
    "Inflation hit a record high.", "Mortgage rates are near record lows.",
    "Unemployment fell to record lows.",
])
def test_an_economic_record_is_not_a_price_level(text):
    _both_modes_empty(text)


@pytest.mark.parametrize("text", ["NVIDIA shot higher after the launch.", "NVIDIA soared in 2023.",
                                  "Tesla went through the roof in 2020.",
                                  "Costco marched higher for years."])
@pytest.mark.parametrize("strict", [True, False])
def test_a_company_moving_like_a_stock_is_a_price_move_in_both_modes(text, strict):
    """No "stock"/"shares" in the sentence: only the company-subject move row sees these."""
    assert "class_b_valuation" in codes(text, strict), text


# ── 7. people pointed at without a lexicon name (W2CB-6, W2CB-7) ────────────────────────────


@pytest.mark.parametrize("key, text", [
    ("journey:risk_vs_uncertainty", "Economist Frank Knight drew this line."),
    ("journey:risk_vs_uncertainty", "Frank Knight called it true uncertainty."),
    ("journey:risk_vs_uncertainty", "As Frank Knight showed, risk and uncertainty differ."),
    ("journey:risk_vs_uncertainty", "Knight called it uncertainty."),
    ("journey:fomo_cycle", "Even Newton got burned chasing a bubble."),
    ("journey:economic_moats", "Warren and his partner loved moats."),
    ("journey:economic_moats", "Warren, famously, loved moats."),
    ("journey:mr_market", "A Columbia professor invented Mr. Market."),
    ("journey:mr_market", "Value investing's founding father created Mr. Market."),
    ("journey:mr_market", "Mr. Market's creator taught at Columbia."),
    ("journey:mr_market", "The author of The Intelligent Investor created Mr. Market."),
    ("journey:economic_moats", "One of history's great investors called it a moat."),
    ("journey:mr_market", "An economist named this idea long ago."),
    (MM + "the-rise-of-lvmh", "The richest man in France bought brand after brand."),
    (MM + "the-rise-of-lvmh", "Its billionaire owner bought brand after brand."),
    (MM + "tesla-vs-traditional-auto", "Tesla's chief once slept on the factory floor."),
    (MM + "tesla-vs-traditional-auto", "Its top executive slept on the factory floor."),
    (MM + "tesla-vs-traditional-auto", "The man at the top slept on the factory floor."),
    (MM + "tesla-vs-traditional-auto", "The person behind Tesla slept on the factory floor."),
    (MM + "tesla-vs-traditional-auto", "Tesla's creator called it production hell."),
    (MM + "tesla-vs-traditional-auto", "Henry's assembly line changed everything."),
    (MM + "nvidias-ai-dominance", "Its longtime leader bet the company on CUDA."),
    (MM + "nvidias-ai-dominance", "The engineer who cofounded NVIDIA bet on CUDA."),
    (MM + "nvidias-ai-dominance", "The company's head bet everything on CUDA."),
    (MM + "microsofts-cloud-metamorphosis", "Microsoft's third leader reframed the company "
                                            "around the cloud."),
    (MM + "how-amazon-built-its-moat", "A former hedge fund analyst started Amazon in a "
                                       "garage."),
    (MM + "netflix-vs-disney-plus", "Uncle Walt built an empire on a mouse."),
    (MM + "netflix-vs-disney-plus", "Walt built an empire on a mouse."),
    (MM + "the-rise-of-lvmh", "Queen Victoria's jewelers admired the trunks."),
])
def test_a_person_by_title_trade_role_or_bare_surname_never_reaches_a_post(key, text):
    _assert_rejected_everywhere(key, text, "person_named")


def test_a_title_word_makes_the_pair_a_person_in_each_layer_on_its_own():
    """Compliance (TITLE_WORDS in the prefix set) and grounding (the title-word pair rule) each
    catch it — pinned separately, so neither can be removed on the strength of the other."""
    from app.services.marketing import content_pool, grounding
    text = "Economist Frank Knight drew this line."
    assert "person_named" in codes(text, False)
    ctx = content_pool.get_item("journey:risk_vs_uncertainty").grounding
    # Title Case, so grounding's mid-sentence rule is off and only the title-word rule sees it.
    title = "Why Economist Frank Knight Drew The Line"
    assert "person_named" in {v.code for v in grounding._check_name_pairs(
        "x", c.skeleton(title), ctx)}


def test_a_lexicon_company_built_on_a_first_name_is_a_company_not_a_person():
    from app.services.marketing import content_pool, grounding
    item = content_pool.get_item("journey:etfs_101")
    text = "Brokers like Charles Schwab cut trading fees to zero."
    got = {v.code for v in grounding.check_grounding("x", text, item.grounding)}
    assert "person_named" not in got and "ungrounded_entity" in got, got


def test_a_person_pointed_at_by_a_book_title_is_a_person():
    text = "The author of Common Stocks and Uncommon Profits wrote about scuttlebutt."
    assert "person_named" in codes(text, False)


@pytest.mark.parametrize("key, text", [
    (MM + "costcos-membership-magic", "Costco's chief advantage is its membership model."),
    (MM + "costcos-membership-magic", "This flips retail on its head."),
    (MM + "nvidias-ai-dominance", "NVIDIA became the leader in AI chips."),
    (MM + "nvidias-ai-dominance", "NVIDIA, the industry's leader, kept investing."),
    (MM + "how-amazon-built-its-moat", "Amazon's head start in logistics mattered."),
    (MM + "the-rise-of-tiktok-vs-instagram-reels", "TikTok's creator fund pays for videos."),
    (MM + "amd-vs-intel-the-cpu-wars", "Intel was the market leader for decades."),
    (MM + "the-rise-of-lvmh", "Moët Hennessy merged with Louis Vuitton in 1987."),
    ("journey:mr_market", "Economists call this opportunity cost."),
    ("journey:stock_vs_business", "Think like an owner, not a trader."),
    ("journey:stock_vs_business", "An owner asks if it is a business they would hold for years."),
    ("journey:mr_market", "A Frank Talk About Risk"),
    ("journey:etfs_101", "Grant Your Future Self Patience"),
])
def test_roles_and_names_as_words_still_reach_a_post(key, text):
    _posted(key, text)


# ── 8. testimonials, calls to action, reworded famous sayings (W2CB-9/10/11) ────────────────


@pytest.mark.parametrize("text, code", [
    ("Thousands of beginners already use this rule.", "endorsement"),
    ("Financial pros swear by this rule.", "endorsement"),
    ("One reader told us this lesson changed how they invest.", "endorsement"),
    ("A listener wrote in to say this changed everything.", "endorsement"),
    ("Beginners love this simple rule.", "endorsement"),
    ("Experts agree: discipline wins.", "endorsement"),
    ("Countless beginners swear by this strategy.", "endorsement"),
    ("Readers call this the best lesson on discipline.", "endorsement"),
    ("This simple rule has helped countless beginners.", "endorsement"),
    ("Loved by thousands of beginners.", "endorsement"),
    ("Trusted by thousands of investors.", "endorsement"),
    ("Want the full lesson? It's in the bio.", "cta"),
    ("Grab the free guide in the bio.", "cta"),
    ("Hit follow for part two.", "cta"),
    ("Double tap if this helped.", "cta"),
    ("Like this post if it helped.", "cta"),
    ("Send it to someone who panics at every dip.", "cta"),
    ("Tag someone who panics at every dip.", "cta"),
    ("Part two drops tomorrow.", "cta"),
    ("Bookmark it for later.", "cta"),
    ("Read the rest in the app.", "cta"),
    ("The app has the rest.", "cta"),
    ("Be fearful when everyone else is greedy, and greedy when everyone else is fearful.",
     "famous_quote"),
    ("Get greedy when everyone is fearful.", "famous_quote"),
    ("A great business at a fair price beats a fair business at a great price.",
     "famous_quote"),
    ("What you pay is the price; what you get is the value.", "famous_quote"),
    ("Buy a business any fool could run, because one day a fool will.", "famous_quote"),
    ("In the short run the market is a popularity contest; in the long run it is a scale.",
     "famous_quote"),
])
def test_testimonials_ctas_and_reworded_sayings_are_rejected(text, code):
    assert code in codes(text, True) and code in codes(text, False), text


@pytest.mark.parametrize("text, code", [
    ("Thousands of beginners already use this rule.", "endorsement"),
    ("Grab the free guide in the bio.", "cta"),
    ("Be fearful when everyone else is greedy, and greedy when everyone else is fearful.",
     "famous_quote"),
])
def test_testimonials_ctas_and_reworded_sayings_never_reach_a_post(text, code):
    _assert_rejected_everywhere("journey:power_of_discipline", text, code)


@pytest.mark.parametrize("text", [
    "Many people use these terms as if they are the same.",
    "Many investors follow the crowd into hot stocks.",
    "Millions of investors panicked in 2008.",
    "Visa is trusted by millions of cardholders.",
    "TikTok is used by millions of people every day.",
    "Users stay in the app for hours.",
    "People spend hours in the app.",
    "In part two of its plan, Amazon built warehouses.",
    "Save it for retirement.",
    "The App Store takes a cut of every purchase.",
    "Patient investors let others be greedy or fearful.",
    "Great investors bought when others were fearful, not when everyone was cheering.",
    "Pros use this term for a falling market.",
    "A student wrote the first CUDA programs.",
    "The crash hit like a storm.",
    "You will find the rest on the balance sheet.",
    "That is when investors buy stocks at the top.",
    "The 2008 crash gave patient investors a chance to buy stocks at low prices.",
    "Investors like this post-earnings drift.",
    "Researchers worked in the bio lab for years.",
])
def test_counts_audiences_and_the_fear_greed_lesson_are_not_social_proof_or_quotes(text):
    _both_modes_empty(text)


# ── 9. the prompt states every rule the round-2 fix enforces ─────────────────────────────────


@pytest.mark.parametrize("phrase", [
    "not by title or trade", "its chief", "the richest man", "not even reworded",
    "in quotation marks", "not even after", "far from over", "belongs in a portfolio",
    "dividend yield", "most valuable", "pick, choose or start with", "the right choice",
    "never fails or works every time", "does not cancel a promise", "the small text below",
    "by people, by machines or by ai", "the bio", "a part two", "a free guide",
    "experts agree", "testimonials",
])
def test_the_system_body_states_the_round_2_rules(phrase):
    from app.services.marketing import writer_prompts as wp
    assert phrase in wp.SYSTEM_BODY.lower(), phrase
