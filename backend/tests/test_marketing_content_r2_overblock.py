"""
Round-2 OVER-BLOCK fixes (W2 review of the round-1 fix pass): honest copy the fix pass started
rejecting, and the super-linear paths it added — each group pins BOTH directions.

* MUST-PASS strings are the reviewers' natural paraphrases (and real drafts), asserted as an
  EMPTY violation list or a published X post — never as the absence of one code, which is how a
  guard goes vacuous.
* MUST-REJECT strings are the attacks the narrowed rule must still stop — the round-1 idx-3/5/
  6/7/8/15 strings and the reviewers' traps.

The real-model corpus is `test_marketing_content_r2_corpus_gate.py`; this file is the per-rule
proof.

Category 1 (pure): the Learn bundle and the vendored lists only.
"""

from __future__ import annotations

import gc
import re
import time
from typing import Callable, List, Tuple

import pytest

from app.services.marketing import compliance as c
from app.services.marketing import content_pool
from app.services.marketing import grounding as g
from app.services.marketing import numbers as nb
from app.services.marketing import post_copy as pc
from app.services.marketing import writer_prompts as wp
from app.services.marketing import writer_service as ws
from app.services.marketing.selection import TEMPLATES
from test_marketing_content_a_writer_gate import RUN_DATE, _baseline, _with

MM = "money_moves:"
NV, LV, MS, TS = (MM + "nvidias-ai-dominance", MM + "the-rise-of-lvmh",
                  MM + "microsofts-cloud-metamorphosis", MM + "tsmc-the-foundry-that-runs-the-world")
BO, HD, AM, TE = (MM + "boeing-vs-airbus-the-aerospace-duopoly", MM + "the-home-depot-vs-lowes",
                  MM + "how-amazon-built-its-moat", MM + "tesla-vs-traditional-auto")
AP, VI, CO, ME = (MM + "apples-services-revolution", MM + "visa-vs-mastercard",
                  MM + "costcos-membership-magic", MM + "metas-metaverse-pivot")
AMD, NF, TT = (MM + "amd-vs-intel-the-cpu-wars", MM + "netflix-vs-disney-plus",
               MM + "the-rise-of-tiktok-vs-instagram-reels")
IT, ET, KS, FO, MR = ("journey:inflation_thief", "journey:etfs_101", "journey:key_statistics",
                      "journey:fomo_cycle", "journey:mr_market")
RR, SB, CI, AI = ("journey:risk_reward", "journey:stock_vs_business", "journey:compound_interest",
                  "journey:ai_and_beyond")


def scan_codes(text: str, strict: bool, **kw) -> List[str]:
    return [v.code for v in c.scan_text("x", c.clean(text), strict_instruments=strict, **kw)]


def _both_modes_empty(text: str) -> None:
    for strict in (True, False):
        got = [(v.code, v.detail) for v in c.scan_text("x", c.clean(text),
                                                        strict_instruments=strict)]
        assert got == [], (text, strict, got)


def _field_clean(key: str, text: str) -> None:
    """The production field scan (compliance + grounding) for the item, empty."""
    item = content_pool.get_item(key)
    got = [(v.code, v.detail) for v in ws._scan("x", c.clean(text), item, allow_emoji=True)]
    assert got == [], (key, text, got)


def _posted(key: str, text: str) -> None:
    item, pkg = _baseline(key)
    res = ws.validate_package(_with(pkg, "x", text), item, RUN_DATE)
    assert "x" in res.posts, (key, text, [(v.code, v.detail) for v in res.outlets.get("x", [])])


def _field_codes(key: str, text: str) -> set:
    item = content_pool.get_item(key)
    return {v.code for v in ws._scan("x", c.clean(text), item, allow_emoji=True)}


# ── 1. numbers: an honest restatement with the writer's own verb (W2-OB-1, W2CB-12) ──────────


HONEST_NUMBERS = (
    (BO, "Airbus was founded in 1970."),
    (BO, "Boeing was founded in 1916."),
    (HD, "Home Depot opened in 1978."),
    (HD, "Home Depot came along in 1978."),
    (TS, "TSMC started in 1987."),
    (TS, "TSMC was launched in 1987."),
    (TS, "This unique business model, established in 1987, focuses solely on manufacturing."),
    (LV, "LVMH was created in 1987."),
    (LV, "LVMH came together in 1987."),
    (MS, "Microsoft bought GitHub in 2018 for roughly $7.5 billion."),
    (MS, "Microsoft paid roughly $7.5 billion for GitHub in 2018."),
    (NV, "NVIDIA bought Mellanox for roughly $6.9 billion."),
    (NV, "NVIDIA acquired Mellanox for about $6.9 billion."),
    (NV, "NVIDIA spent about $6.9 billion on Mellanox."),
    (NV, "NVIDIA released CUDA in 2006."),
    (NV, "In 2006, NVIDIA introduced CUDA."),
    (NV, "In 2006, NVIDIA released CUDA, a way to run ordinary programs on the graphics "
         "processor."),
    (NV, "An unexpected link between game graphics and neural networks, combined with early "
         "software development (CUDA in 2006), created a strong position."),
    (LV, "In 2021, LVMH paid roughly $15.8 billion for Tiffany."),
    (LV, "LVMH acquired Tiffany in 2021."),
    (LV, "Tiffany joined the group in 2021."),
    (LV, "The group added Tiffany in 2021."),
    (LV, "In 2021, Tiffany became part of LVMH."),
    (LV, "LVMH bought Bulgari in 2011."),
    (LV, "LVMH bought Tiffany for about $15.8 billion."),
    (NF, "Netflix counts over 200 million members worldwide."),
    (ET, "ETFs 101: One Basket, Many Stocks"),
    (ET, "Welcome to ETFs 101."),
    (TE, "Tesla focused on electric vehicles 10 years before incumbents."),
    (BO, "No new major manufacturer has emerged in over 50 years."),
    (BO, "No new major manufacturer has established itself since 1970."),
    (BO, "The 737 MAX Example"),
    (IT, "A coffee that cost $3 last year might cost $3.20 this year."),
    (IT, "Prices rise about 3% a year, so they roughly double in about 24 years."),
    (AMD, "AMD's strategic decision to divest its manufacturing operations in 2009 changed it."),
    (AM, "Amazon started as an online bookstore in 1994."),
)


@pytest.mark.parametrize("key, text", HONEST_NUMBERS)
def test_an_honest_restatement_of_a_sheet_number_grounds(key, text):
    _field_clean(key, text)


@pytest.mark.parametrize("key, text", [
    (LV, "In 2021, LVMH paid roughly $15.8 billion for Tiffany."),
    (NV, "NVIDIA acquired Mellanox for about $6.9 billion."),
    (TS, "TSMC was launched in 1987."),
])
def test_an_honest_restatement_reaches_a_post(key, text):
    _posted(key, text)


NUMBER_ATTACKS = (
    # A sheet's inflation rate must not become an equity return (W2CB-8).
    (IT, "Stocks have averaged about 3% a year above inflation.", "number_context"),
    (IT, "Stocks have beaten inflation by about three percent a year.", "number_context"),
    (IT, "The stock market has outpaced inflation by about three percent a year.",
     "number_context"),
    # An acquisition synonym never binds a buyback or another spend (W2CB-12 trap).
    (NV, "NVIDIA bought back $6.9 billion of its own stock.", "number_context"),
    (NV, "Mellanox bought back $6.9 billion of its own stock.", "number_context"),
    (MS, "GitHub repurchased $7.5 billion of its shares.", "number_context"),
    (NV, "NVIDIA spent $6.9 billion on research.", "number_context"),
    # A year whose own verb is another event does not bind (founded ≠ shipped).
    (NV, "NVIDIA was founded in 2006.", "number_context"),
    (ME, "Meta founded Oculus in 2014.", "number_context"),
    # A number of another subject, or another measure, still fails (idx 6).
    (LV, "LVMH hired 75 designers.", "number_context"),
    (AP, "Apple's iPhone sales grew 70%.", "number_context"),
    (AP, "Apple climbed 70%.", "number_context"),
    (CO, "Costco rallied 90% as renewal rates held.", "number_context"),
)


@pytest.mark.parametrize("key, text, code", NUMBER_ATTACKS)
def test_a_misattributed_number_is_still_refused(key, text, code):
    assert code in _field_codes(key, text), (key, text, _field_codes(key, text))


def test_a_year_binds_only_on_its_own_events_verb_class():
    """Unit: the event-kind guard reads the year's own clause, not the whole window."""
    ctx = g.build_context(("In 2006 NVIDIA shipped CUDA.",), frozenset({"shipped"}))
    assert g.check_grounding("f", "NVIDIA released CUDA in 2006.", ctx) == []
    assert [v.code for v in g.check_grounding("f", "NVIDIA was founded in 2006.", ctx)] == [
        "number_context"]
    # "(CUDA in 2006), created a strong position": "created" is the main clause's verb.
    assert g.check_grounding("f", "Early work (CUDA in 2006), created a strong position.",
                             ctx) == []


def test_a_names_only_source_number_meets_the_drafts_names_but_never_its_subject():
    ctx = g.build_context(("Dior, Fendi, Bulgari in 2011, Tiffany in 2021 for roughly $15.8 "
                           "billion.", "Tesla: 500."), frozenset())
    assert g.check_grounding("f", "The group added Tiffany for $15.8 billion.", ctx) == []
    assert g.check_grounding("f", "In 2021, Tiffany joined for $15.8 billion.", ctx) == []
    # The company the sentence OPENS with is its subject, and never meets a names-only row.
    for text in ("Tesla hired 500 staff.", "The Tesla team hired 500 staff."):
        assert "number_context" in [v.code for v in g.check_grounding("f", text, ctx)], text


def test_the_sheets_spoken_durations_and_prices_ground_only_on_the_source_side():
    ctx = g.build_context(("Tesla bet on electric vehicles a decade before the incumbents.",
                           "The coffee that cost three dollars last year might cost "
                           "three-twenty next year."), frozenset())
    assert g.check_grounding("f", "Tesla bet on electric vehicles 10 years before incumbents.",
                             ctx) == []
    assert g.check_grounding("f", "The coffee might cost $3.20 next year.", ctx) == []
    # A draft's own "a decade" stays words — it is never a number the draft must ground.
    assert g.check_grounding("f", "It took a decade.", g.build_context(("Nothing.",),
                                                                       frozenset())) == []
    # A spoken price needs a currency amount in its sentence to read as one.
    bare = g.build_context(("The score was three-twenty.",), frozenset())
    assert [v.code for v in g.check_grounding("f", "It cost $3.20.", bare)] == [
        "ungrounded_number"]


def test_a_rates_period_is_no_anchor_unless_it_is_all_the_source_has():
    ctx = g.build_context(("At just three percent a year, prices roughly double.",
                           "Growth: 7% a year."), frozenset())
    assert "number_context" in [v.code for v in g.check_grounding(
        "f", "Stocks averaged 3% a year.", ctx)]
    assert g.check_grounding("f", "Prices rise 3% a year.", ctx) == []
    # Every fact sentence grounds itself, even one whose only anchor is the period.
    only_period = g.build_context(("7% a year.",), frozenset())
    assert g.check_grounding("f", "7% a year.", only_period) == []


# ── 2. return figures: a ratio stated with earn / compound / multiplied (W2-OB-2) ────────────


MARGIN_FACTS = (
    (VI, "Visa and Mastercard earn operating margins above 50 percent."),
    (VI, "Both networks earn operating margins north of 50 percent."),
    (VI, "With no credit risk and networks already built, the two earn operating margins above "
         "50 percent."),
    (VI, "That sliver, multiplied across billions of payments, means operating margins above "
         "50%."),
    (VI, "Visa earned operating margins above 50%."),
    (AP, "Services earn gross margins near 70 percent."),
    (AP, "Apple earns about 70 percent gross margins on Services."),
    (AP, "Services carry gross margins around 70%, and that is where the strategy compounds."),
    (CO, "Renewal rates above 90% let membership compound."),
)


@pytest.mark.parametrize("key, text", MARGIN_FACTS)
def test_a_business_ratio_with_a_return_word_is_no_return_figure(key, text):
    _field_clean(key, text)


@pytest.mark.parametrize("text", [
    "Index funds earn 10% a year.",
    "Savings compound at 5%.",
    "Your money compounds at 7% a year.",
    "The fund earns 12% a year on margin debt.",
    "Bought on margin, the account earns 20% a year.",
    "Shareholders earned 50%.",
    "Compounding near twenty-nine percent turns one dollar into more than twenty-seven.",
    "Visa's margins north of fifty percent gave its shareholders gains north of fifty percent.",
    "Stocks have averaged about 3% a year above inflation.",
    "The index has averaged 3% a year.",
    "At three percent a year, your money doubles in about twenty-four years.",
    "Stocks have beaten inflation by about three percent a year.",
    "The shares averaged 10% a year.",
])
@pytest.mark.parametrize("strict", [True, False])
def test_an_investment_return_is_still_a_return_figure(text, strict):
    assert "return_figure" in scan_codes(text, strict), text


@pytest.mark.parametrize("text", [
    "Stocks can double your money in about twenty-four years.",
    "Your investments can double in about twenty-four years.",
])
@pytest.mark.parametrize("strict", [True, False])
def test_a_rate_free_doubling_promise_is_promissory(text, strict):
    assert "promissory" in scan_codes(text, strict), text


def test_a_worked_doubling_example_with_its_rate_is_arithmetic():
    _both_modes_empty("At 7% a year, money can double in about ten years.")


# ── 3. a misconception stated AS one (W2-OB-3) ──────────────────────────────────────────────


MYTH_LEGIT = (
    "Myth: stocks always go up if you wait long enough.",
    "Myth: an ETF means you can never lose money.",
    "Myth: a wide moat guarantees success. Fact: no moat is permanent.",
    "Many beginners believe the market always goes up.",
    "Many savers think their money is safe as long as it sits still.",
    "Here is a common myth: stocks always go up if you just wait.",
    "A popular myth is that the market always recovers.",
    "Myth #1: stocks always go up.",
    "Stocks always go up. That is a myth.",
    "Big rewards with no risk? It's a myth.",
    "Can you get big rewards with zero risk?",
    "Do stocks always go up?",
    "Myth: the bigger company always wins.",
    "Myth: size always beats speed.",
    "It is tempting to think stocks always recover.",
    "The idea that stocks always go up is a common misconception.",
    "Promises of big rewards with no risk are a warning.",
    'Beware of "Guaranteed Returns"',
    "Beware of promises of high, guaranteed returns with no risk.",
    "Safe options grow slowly, while stocks can grow faster but also drop.",
    "ETFs make investing simpler and steadier, not magically risk-free.",
    "There is no such thing as guaranteed returns.",
    "Many say the market always recovers. They're wrong.",
    "Nobody can promise the market always recovers. True.",
    "Stocks always go up. False.",
)


@pytest.mark.parametrize("text", MYTH_LEGIT)
def test_a_labelled_misconception_or_a_warning_is_not_a_promise(text):
    _both_modes_empty(text)


MYTH_BYPASSES = (
    "Myth: stocks are risky. Fact: index funds always go up.",
    "Myth: stocks are risky; fact: index funds always go up.",
    "Myth vs fact: the market always recovers.",
    "It's not a myth: index funds always go up.",
    "It's no myth: index funds always go up.",
    "Want returns that always go up?",
    "Ready to never lose money?",
    "Do you want a portfolio that always goes up?",
    "Did you know stocks always go up?",
    "Isn't it true that stocks always go up?",
    "Stocks always go up. That's not a myth.",
    "Many believe it: the market always recovers.",
    "Guaranteed returns!",
    "No fees, guaranteed returns.",
    "Don't miss out: guaranteed returns.",
    "Risk-free returns for everyone.",
    "The safest way, bar none, to grow your money.",
    # A frame, or a question, the text then AFFIRMS frames nothing.
    "Many say the market always recovers, and they're right.",
    "It is a myth that stocks always go up, and that's true.",
    "Do stocks always go up? Yes.",
    "Can you get big rewards with zero risk? Absolutely.",
    "Index funds always go up without warning signs.",
    "Can you believe index funds never lose money?",
    "Is it any wonder stocks always go up?",
    "Truth vs myth: stocks always go up.",
    "Fact or myth: stocks always go up.",
    "Many say the market always recovers. They're right.",
    "Stocks always go up. False? No.",
)


@pytest.mark.parametrize("text", MYTH_BYPASSES)
@pytest.mark.parametrize("strict", [True, False])
def test_a_frame_that_frames_nothing_exempts_nothing(text, strict):
    assert {"promissory", "banned_phrase"} & set(scan_codes(text, strict)), text


def test_a_myth_title_frames_its_bodys_first_sentence_only():
    body = "Stocks always go up if you wait. Index funds never lose money."
    labelled = [(v.code, v.detail) for v in c.scan_text("b", body, myth_framed=True)]
    assert labelled == [("promissory", "never lose money")], labelled
    assert "promissory" in [v.code for v in c.scan_text("b", "Stocks always go up.")]


@pytest.mark.parametrize("title, ok", [("The myth", True), ("Myth #2", True),
                                       ("Common misconception", True), ("Myth vs. Fact", False),
                                       ("Patience pays", False)])
def test_a_card_titled_as_the_myth_reaches_the_gate(title, ok):
    item, pkg = _baseline(RR)
    pkg["cards"][0] = {"title": title, "body": "Stocks always go up if you wait long enough."}
    res = ws.validate_package(pkg, item, RUN_DATE)
    assert res.ok is ok, (title, [(v.field, v.code, v.detail) for v in res.shared])


# ── 4. code-owned / CTA / endorsement / saying rows vs case-study prose (W2-OB-4) ────────────


CASE_STUDY_PROSE = (
    "For years, NVIDIA's cards were built for games, not AI.",
    "The board was built to draw game frames, not AI.",
    "No AI can supply your patience, your goals or your nerve in a storm.",
    "Your judgment, not AI, makes the final call.",
    "Without AI, no person could spot some of these patterns.",
    "Before 2012, almost no AI research ran on these cards.",
    "Patience is a human edge, not an AI one.",
    "TikTok's recommendations came from what you watched, not who you followed.",
    "TikTok ranked videos by watch data, so its recommendations did not depend on friends.",
    "A recommendation feed is judged on whether you kept watching.",
    "Visa and Mastercard share this network model.",
    "Customers used to install the software from a box every few years.",
    "The full story of the App Store is a lesson in recurring revenue.",
    "Fans love Disney's franchises across generations.",
    "Students loved CUDA because it already worked.",
    "A patient investor says no to the daily noise.",
    "Apple focused on the App Store, iCloud, and various subscriptions.",
    "The App Store takes a cut of every purchase.",
    "The competition offers a compelling case study in manufacturing strategy.",
    "Growth in revenue and earnings signals expansion.",
    "This transition signals a maturation of the streaming market.",
    "Prices act as market signals.",
    "Developers get most of each sale on the App Store.",
    "Users install apps from the App Store.",
    "More than a million apps were available on the App Store.",
    "Apple made it easy to sell apps on the App Store.",
    "Fans said the Marvel films felt tired.",
    "Decisions made by people, not AI, still matter.",
    "Buying high and selling low is the FOMO trap.",
    "Home Depot stocks 35,000 products and sales grew 5%.",
    "Accounts payable grew 20%.",
    "NVIDIA kept investing in CUDA, and revenue grew 40%.",
)


@pytest.mark.parametrize("text", CASE_STUDY_PROSE)
def test_case_study_vocabulary_is_not_code_owned(text):
    _both_modes_empty(text)


@pytest.mark.parametrize("text, code", [
    ("No AI was used to write this lesson.", "code_owned"),
    ("This lesson used no AI.", "code_owned"),
    ("Written without AI.", "code_owned"),
    ("Not AI-assisted.", "code_owned"),
    ("Zero AI here.", "code_owned"),
    ("Every word here is 100% human.", "code_owned"),
    ("Our recommendation: buy index funds.", "code_owned"),
    ("This is not a recommendation.", "code_owned"),
    ("Analyst recommendations say buy.", "code_owned"),
    ("Share this with a friend.", "cta"),
    ("Share this post.", "cta"),
    ("Share this!", "cta"),
    ("Install it free today.", "cta"),
    ("Install the app.", "cta"),
    ("The full story is in the app.", "cta"),
    ("Readers love this lesson.", "endorsement"),
    ("Fans love this lesson.", "endorsement"),
    ("Students told us it's the best lesson.", "endorsement"),
    ("Made by humans, not AI.", "code_owned"),
    ("Written by people.", "code_owned"),
    ("Fans say this lesson changed everything.", "endorsement"),
    ("A legendary investor once said patience wins.", "famous_quote"),
    ("Find it on the App Store.", "brand_mention"),
    ("Available on the App Store today.", "brand_mention"),
    ("Rated five stars on the App Store.", "brand_mention"),
    ("It's on the App Store now.", "brand_mention"),
    ("They sell buy signals.", "banned_phrase"),
    ("Get our signals every morning.", "banned_phrase"),
    ("NVIDIA stock is a compelling buy.", "class_b_evaluative"),
])
@pytest.mark.parametrize("strict", [True, False])
def test_the_claim_shapes_are_still_rejected(text, code, strict):
    if code == "class_b_evaluative" and not strict:
        return          # tier-2 words escalate in Journey only when a company is named
    assert code in scan_codes(text, strict), (text, scan_codes(text, strict))


# ── 5. a Journey lesson's own heading words are no company (W2-OB-5) ─────────────────────────


@pytest.mark.parametrize("key, text", [
    (KS, "Your Quick Dashboard: market cap, P/E, growth and the dividend."),
    (KS, "Key Statistics explained: market cap shows size."),
    (KS, "Your Quick Dashboard shows market cap, P/E and growth."),
    (FO, "The FOMO Cycle: a stock climbs, and headlines cheer."),
    (MR, "The Mr. Market Lesson: the share price swings with his mood."),
    (KS, "Checklist Item 1: find the market cap."),
    (KS, "Market Cap"), (KS, "P/E Ratio"), (KS, "The P/E Ratio"), (KS, "What Is Market Cap?"),
    (KS, "Why P/E Matters"), (FO, "When a Stock Climbs"),
    (KS, "The Price-to-Earnings ratio shows how much is paid for each dollar a company earns."),
])
def test_a_lessons_heading_or_title_is_journey_copy(key, text):
    _field_clean(key, text)


@pytest.mark.parametrize("title", ["Market Cap", "P/E Ratio", "What Is Market Cap?"])
def test_a_concept_title_on_a_slide_passes_the_gate(title):
    item, pkg = _baseline(KS)
    pkg["carousel_slides"][1]["title"] = title
    res = ws.validate_package(pkg, item, RUN_DATE)
    assert res.ok and not res.shared, [(v.field, v.code, v.detail) for v in res.shared]


@pytest.mark.parametrize("key, text", [
    (KS, "Apple's market cap is huge."),            # "apples to apples" is in the sheet
    (KS, "Lemonade's market cap is tiny."),
    (KS, "Lemonade looks cheap."),
    (KS, "Why Lemonade Looks Cheap"),
    (KS, "Companies like Chewbacca Corp have a low P/E."),
    (MR, "Target: low P/E, solid dividend."),
])
def test_a_named_company_still_escalates_in_journey(key, text):
    assert _field_codes(key, text) & {"class_b_valuation", "class_b_evaluative",
                                      "ungrounded_entity"}, text


@pytest.mark.parametrize("title", ["Market Cap", "P/E Ratio", "When a Stock Climbs",
                                   "Share Price Basics"])
def test_a_short_title_case_fragment_is_a_title_even_without_the_sheet(title):
    """A two- or three-word card title is not a sentence with a mid-sentence name, whatever
    item it is on (`_title_cased` needs three content words)."""
    assert scan_codes(title, False) == [], title


def test_a_short_title_is_short_capitalised_and_period_free():
    assert c._short_title("Market Cap") and c._short_title("What Is Market Cap?")
    assert not c._short_title("Market cap.")                  # a sentence
    assert not c._short_title("Market cap")                   # not Title Case
    assert not c._short_title("One Two Three Four Five Six Seven")   # too long for a title


def test_the_sheet_word_exemption_needs_the_sheet():
    """Unit: without the item's sheet vocabulary, a mid-sentence capital still escalates — the
    exemption is the SHEET's words, not "any ordinary word" (Apple, Target are words)."""
    text = "Your Quick Dashboard: market cap, P/E, growth and the dividend."
    assert "class_b_valuation" in scan_codes(text, False)
    item = content_pool.get_item(KS)
    assert scan_codes(text, False, sheet_words=item.grounding.tokens) == []


# ── 6. the reader's self-question (W2-OB-8) ──────────────────────────────────────────────────


@pytest.mark.parametrize("text", [
    "Is this a business I'd be glad to hold for years?",
    "An owner asks: is this a business I'd be glad to hold for years?",
    "Ask yourself: is this a business I'd be glad to hold for years?",
    "Ask yourself: what would make me sell?",
    "Ask: is this my plan talking, or my fear?",
    "What would I do if the price fell by half?",
    "Is this price move telling me anything about the business?",
    "Am I scared of a crash?",
    "Do I need this stock?",
])
def test_the_readers_self_question_is_exempt_whatever_its_first_person_word(text):
    assert "first_person" not in scan_codes(text, False), text


@pytest.mark.parametrize("text", [
    "Why did I buy at the top?",
    "How did I turn my savings around?",
    "Did I mention my portfolio tripled?",
    "Is it true I made my money back in a year?",
    "Was I wrong to sell?",
    "What made me buy at the top?",
    "Can you guess what I did next?",
    "Want to know how I stopped panic selling?",
    "Would we be glad to own this?",
])
def test_a_narrated_experience_in_question_form_stays_a_testimonial(text):
    assert "first_person" in scan_codes(text, False), text


def test_the_stock_vs_business_question_reaches_a_post():
    _posted(SB, "Is this a business I'd be glad to hold for years?")


# ── 7. case-study bets, customers who pay more, a named mistake (W2CB-12, real drafts) ──────


@pytest.mark.parametrize("key, text", [
    (ME, "The metaverse pivot was a risky bet for Meta."),
    (ME, "Meta made a risky bet on the metaverse."),
    (ME, "Was the metaverse a bad bet?"),
    (ME, "Reality Labs was a risky bet on the metaverse."),
    (LV, "Buyers pay more for a name with history."),
    (NV, "Few buyers are willing to pay that bill."),
    (FO, "An emotional loop that can lead investors to buy high and sell low."),
    ("journey:common_mistakes", "Don't panic-sell when prices fall, as this locks in losses."),
    ("journey:second_order_thinking", "For instance, when a new product launches, a "
                                      "first-order response might be to consider buying shares "
                                      "of the manufacturer."),
])
def test_business_decisions_and_named_mistakes_pass(key, text):
    _field_clean(key, text)


@pytest.mark.parametrize("key, text, code", [
    (TS, "TSMC is the safest bet in semiconductors.", "class_b_recommendation"),
    (ME, "Meta is a risky bet.", "class_b_recommendation"),
    (ME, "Was Meta a bad bet?", "class_b_valuation"),
    (ME, "Meta's stock was a risky bet on the metaverse.", "class_b_recommendation"),
    (ME, "Investors made a smart bet on Meta.", "class_b_recommendation"),
    (ME, "The metaverse pivot was a smart bet for investors.", "class_b_recommendation"),
    (ME, "The decision to buy Meta was a smart bet.", "class_b_recommendation"),
    (NV, "Buyers paid up for NVIDIA shares.", "class_b_valuation"),
    (AP, "Investors were willing to pay more for Apple.", "class_b_valuation"),
    (MR, "When his fear makes prices low, you can consider buying.", "class_b_recommendation"),
    (MR, "Buy low and sell high.", "class_b_recommendation"),
    (MR, "Sell when everyone is greedy.", "class_b_recommendation"),
    (MR, "Your first move should be to consider buying the index.", "class_b_recommendation"),
    (MR, "Don't wait, buy when prices dip.", "class_b_recommendation"),
    (MR, "Why not buy when prices dip?", "class_b_recommendation"),
    (NV, "The S&P 500 is a safe bet on America.", "class_b_recommendation"),
    (MR, "Buy high, sell higher.", "class_b_recommendation"),
    (CO, "Costco makes an attractive case for investors.", "class_b_evaluative"),
    (MR, "The best habit is to buy when prices dip.", "class_b_recommendation"),
    (NV, "Early investors made a smart bet.", "class_b_recommendation"),
    (NV, "Is this chipmaker a good bet?", "class_b_recommendation"),
])
def test_an_instrument_bet_or_a_directive_is_still_rejected(key, text, code):
    assert code in _field_codes(key, text), (text, _field_codes(key, text))


def test_the_multi_billion_dollar_business_row_stays_fail_closed():
    """DECIDED (W2CB-12 fix notes): "grew into a multi-billion-dollar business" reads as a market
    cap as easily as a revenue size, so the row is not gated on instrument words — gating it
    reopens "NVIDIA became a trillion-dollar company" (idx 8). The repair hint says what to do."""
    assert "class_b_valuation" in _field_codes(AM, "AWS grew into a multi-billion-dollar business.")


# ── 8. links: a Title-Case TLD, and the structural rule pinned (W2CB-13, W2V-12) ─────────────


_NON_LIST_TLDS = ("Read more at investing.education today.", "Notes live at example.blog now.")


@pytest.mark.parametrize("text", [
    "Visit Learn.Money for more.", "The Moat.App shows how a moat works.", "Read at Money.Com",
    "SEC.GOV has the filing.", *_NON_LIST_TLDS,
])
def test_a_written_domain_is_a_link_in_any_case_and_with_any_tld(text):
    assert "link" in scan_codes(text, True), text


def test_the_structural_tld_positives_are_outside_the_list():
    """Self-check: if these TLDs join `_TLDS`, the test above no longer proves the rule is
    structural (a list-based revert would pass it)."""
    for text in _NON_LIST_TLDS:
        tld = re.search(r"\.([a-z]+) ", text).group(1)
        assert tld not in c._KNOWN_TLDS, tld


@pytest.mark.parametrize("text", [
    "It rose fast.Then it fell.", "Prices rose.It was a boom.", "Stay calm.So what?",
    "It fell.No one knew.", "Costs rose.In time it fell.", "Stay the course.Then check back.",
    "It fell in a day.The next day it rose.",
])
def test_a_missing_space_after_a_full_stop_is_not_a_link(text):
    assert "link" not in scan_codes(text, True), text


# ── 9. false-positive guards the fix pass left unpinned (W2V-13) ─────────────────────────────


@pytest.mark.parametrize("text", [
    "Stocks will rise and fall over time.", "The market will go up and down.",
    "Markets will rise and fall.", "Bill is due Friday.", "Mark is visible on the chart.",
])
def test_volatility_and_noun_names_raise_no_violation_at_all(text):
    _both_modes_empty(text)


@pytest.mark.parametrize("text, code", [
    ("The market will keep climbing.", "class_b_forward"),
    ("Uncle Bill said patience wins.", "person_named"),
    ("Bill's habit was patience.", "person_named"),
])
def test_their_positive_twins_still_fire(text, code):
    assert code in scan_codes(text, True), (text, scan_codes(text, True))


# ── 10. every shared field needs words (w2ww-2) ──────────────────────────────────────────────


@pytest.mark.parametrize("mutate, field", [
    (lambda p: p["video_script"].insert(1, "…"), "video_script[1]"),
    (lambda p: p["cards"].__setitem__(0, {"title": "?", "body": p["cards"][0]["body"]}),
     "cards[0].title"),
    (lambda p: p["cards"].__setitem__(0, {"title": p["cards"][0]["title"], "body": "..."}),
     "cards[0].body"),
    (lambda p: p["carousel_slides"].__setitem__(0, {"title": "-", "body": "$"}),
     "carousel_slides[0].title"),
])
def test_a_punctuation_only_shared_field_is_empty(mutate: Callable, field):
    item, pkg = _baseline(LV)
    mutate(pkg)
    res = ws.validate_package(pkg, item, RUN_DATE)
    assert not res.ok and (field, "empty") in {(v.field, v.code) for v in res.shared}, (
        [(v.field, v.code) for v in res.shared])


@pytest.mark.parametrize("title", ["1854", "1987", "P/E", "Q&A"])
def test_a_year_or_symbol_title_is_not_empty(title):
    item, pkg = _baseline(LV)
    pkg["cards"][0]["title"] = title
    res = ws.validate_package(pkg, item, RUN_DATE)
    assert ("cards[0].title", "empty") not in {(v.field, v.code) for v in res.shared}


# ── 11. the prompt states what the gate enforces (W2-OB-9, w2ww-3) ───────────────────────────


def _prompt(key: str) -> str:
    item = content_pool.get_item(key)
    return wp.draft_prompt(item, TEMPLATES[0], RUN_DATE, generation_id="g-1")


def test_a_journey_prompt_may_teach_the_valuation_concepts_its_sheet_teaches():
    ks, nv = _prompt(KS), _prompt(NV)
    assert "may explain the concepts" in ks and "market cap" in ks.lower()
    assert "never use valuation vocabulary" in nv and "may explain the concepts" not in nv


def test_the_prompt_names_youtubes_refused_characters():
    body = wp.SYSTEM_BODY
    assert "< or >" in body and "one line" in body
    spec = _prompt(NV)
    assert "youtube_title: about" in spec and "no < or > characters, one line" in spec
    assert "no < or > characters" in spec.split("youtube_description:")[1].splitlines()[0]


def test_the_system_body_allows_what_the_gate_now_allows():
    body = wp.SYSTEM_BODY.lower()
    for phrase in ("investment recommendations", "apple's app store, is fine",
                   "\"myth: stocks always go up.\"", "is this a business i'd be glad",
                   "built for games, not ai", "\"paid for\", \"founded\""):
        assert phrase in body, phrase
    assert wp.PROMPT_VERSION >= "2026-09-24.3"


def test_the_forbidden_char_hint_never_suggests_a_famous_saying():
    post = pc.ComposedPost("youtube", "Time in the market > timing the market", "x")
    detail = " ".join(v.detail for v in pc._forbidden_chars("youtube_title", post.title))
    assert "beats" not in detail and "→" in detail


@pytest.mark.parametrize("text", ["Time in the market > timing the market.",
                                  "Time in the market → timing the market.",
                                  "Time in the market -> timing the market."])
def test_a_famous_saying_written_with_a_symbol_is_still_one(text):
    assert "famous_quote" in scan_codes(text, False)


def test_a_comparison_symbol_in_ordinary_copy_is_fine():
    _both_modes_empty("Revenue > costs means profit.")


@pytest.mark.parametrize("code", ["number_context", "return_figure", "promissory",
                                  "first_person", "code_owned", "brand_mention",
                                  "platform_forbidden_char", "empty"])
def test_every_round_2_repair_hint_says_what_to_do(code):
    hint = wp.REPAIR_HINTS[code]
    assert len(hint) > 40 and hint != "fix it"


# ── 12. linear time on every new path (W2-OB-6, W2-OB-7) ─────────────────────────────────────
#
# Round 3 (W3OB-8, W3VAC-09): the round-2 version compared best-of-3 WALL-CLOCK times of 3,000 and
# 6,000 characters measured one size after the other, and failed 8 of 8 parallel runs on linear
# code — a descheduled sample landed on one size only. The measurement is now load-robust, and
# it catches a smaller quadratic than before:
#
# * THREAD CPU time (`time.thread_time`): time spent descheduled is not billed to the thread.
# * INTERLEAVED samples (small, big, small, big …), minimum of each, collector off: whatever
#   contention remains hits both sizes alike.
# * An 8x rung — 750 vs 6,000 characters (`_scan` caps a field at 6,000, so a bigger rung would
#   measure the same text): linear work grows ~8x (measured 7.9-8.9 over `_DEGENERATE`),
#   quadratic ~64x, and the bound is 16x. With linear cost L and quadratic cost Q at 6,000
#   characters the ratio crosses 16 once Q > 1.33 L; the old 2x rung at 3x needed Q > 2 L.
# * A suspect must be confirmed by a second, independent measurement before the test fails. A
#   real quadratic fails both; a scheduler hiccup does not repeat.
# * The absolute ceiling stays (0.25 s of CPU for a 6,000-character field): it bounds what any
#   path can cost at the cap, the ratio catches the regression while it is still cheap.
#
# Never "fix" a red run by raising a bound, dropping the confirmation or adding a rerun marker:
# each of those makes the guard vacuous against the W2-OB-6/7 regressions it exists for.
# `test_the_linearity_guard_is_not_vacuous` keeps two quadratic mutants failing it.

_LADDER = (750, 6000)
_RATIO_LIMIT = 16.0
_CEILING = 0.25


def _cpu_pair(small: Callable[[], object], big: Callable[[], object],
              rounds: int = 5) -> Tuple[float, float]:
    """(min, min) thread-CPU seconds of `small` and `big`, measured interleaved."""
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


def _is_linear(ts: float, tb: float, floor: float, limit: float) -> bool:
    return tb < _CEILING and (tb < floor or tb / max(ts, 1e-6) < limit)


def assert_linear(make: Callable[[int], Callable[[], object]], label: object, *,
                  floor: float = 0.004, ladder: Tuple[int, int] = _LADDER,
                  limit: float = _RATIO_LIMIT) -> None:
    """`make(n)` returns the work for an n-character input. Fails only when two independent
    interleaved measurements both say super-linear (or both break the ceiling)."""
    seen = []
    for _attempt in range(2):
        ts, tb = _cpu_pair(make(ladder[0]), make(ladder[1]))
        if _is_linear(ts, tb, floor, limit):
            return
        seen.append((round(ts * 1000, 2), round(tb * 1000, 2)))
    raise AssertionError((label, "super-linear (ms small, ms big)", seen))


#: (item, repeated unit): number-dense runs that reach the MATCHED-number path (the sheet has
#: the number), unspaced runs for the possessive/role rows, and one per new frame or row.
_DEGENERATE = (
    (LV, "75 LVMH "), (LV, "2021 Tiffany "), (LV, "In 2021 Tiffany "), (BO, "1970 "),
    (BO, "737 MAX "), (NV, "$6.9 billion "), (NV, "Mellanox acquired $6.9B "),
    (NV, "U.S."), (NV, "A."), (NV, "A-"), (NV, "S&P&"), (NV, "u.s."),
    (RR, "risk-free. "), (RR, "Myth: "), (RR, "Beware of promises of "), (RR, "Many think "),
    (ME, "a risky bet "), (MR, "consider buying "), (MR, "buy when "), (VI, "margins above 50% "),
    (SB, "Is this a business I'd "), (KS, "Market Cap "), (AI, "no AI "), (AP, "on the App Store "),
    (IT, "three-twenty "), (TE, "a decade "), (IT, "Stocks can double "),
    # Round 3 (entities): the new rows' own shapes.
    (MR, "fear makes prices low, you can "), (IT, "stocks roughly double "),
    (VI, "Visa really "), (CO, "Costco: at a fair price "), (NV, "and so did the stock "),
    (NV, "the man running "), (LV, "LVMH paid $15.8 billion for "), (ET, "Discover ways "),
)


@pytest.mark.parametrize("key, unit", _DEGENERATE)
def test_a_degenerate_field_scans_in_linear_time(key, unit):
    item = content_pool.get_item(key)

    def run(n: int) -> Callable[[], None]:
        text = (unit * (n // len(unit) + 1))[:n]
        return lambda: ws._scan("f", text, item, allow_emoji=True)

    assert_linear(run, (key, unit))


def _compiled_patterns():
    out = []
    for mod in (c, g, pc, nb):
        for name, val in vars(mod).items():
            if isinstance(val, re.Pattern):
                out.append((f"{mod.__name__.split('.')[-1]}.{name}", val))
            elif isinstance(val, tuple):
                for i, x in enumerate(val):
                    if isinstance(x, re.Pattern):
                        out.append((f"{mod.__name__.split('.')[-1]}.{name}[{i}]", x))
                    elif isinstance(x, tuple):
                        out += [(f"{mod.__name__.split('.')[-1]}.{name}[{i}]", y)
                                for y in x if isinstance(y, re.Pattern)]
    return out


#: Patterns that only ever run on a BOUNDED slice (≤ 200 characters), never on a whole field —
#: each is quadratic on an unbounded run by design and must stay window-only.
_WINDOW_ONLY = {"compliance._PREV_WORD_RE"}


#: The pattern sweep's rung: a bare pattern is not capped, so 3,000 vs 12,000 characters (4x:
#: linear ~4x, quadratic ~16x, bound 8x).
_SWEEP_LADDER = (3000, 12000)
_SWEEP_FLOOR = 0.002
_SWEEP_LIMIT = 8.0


def _sweep_suspects(pats, units) -> List[Tuple[str, str]]:
    """One interleaved sample per (pattern, unit): the cheap screen."""
    out = []
    for name, rx in pats:
        if name in _WINDOW_ONLY:
            continue
        for unit in units:
            small = (unit * _SWEEP_LADDER[1])[:_SWEEP_LADDER[0]]
            big = (unit * _SWEEP_LADDER[1])[:_SWEEP_LADDER[1]]
            ts, tb = _cpu_pair(lambda: list(rx.finditer(small)), lambda: list(rx.finditer(big)), 1)
            if not _is_linear(ts, tb, _SWEEP_FLOOR, _SWEEP_LIMIT):
                out.append((name, unit))
    return out


def test_every_compiled_pattern_is_linear_on_unspaced_runs():
    """The per-pattern sweep the W2-OB-7 review ran by hand: every module-level pattern in the
    four validator modules, on 3,000 vs 12,000 characters of each degenerate unit — screened with
    one interleaved CPU sample, and every suspect confirmed by `assert_linear` (two independent
    five-round measurements) before it counts."""
    pats = _compiled_patterns()
    assert len(pats) >= 150, len(pats)
    units = ("a.", "U.S.", "A-", "s&p&", "a'", "9.", "zzco ", "a's ")
    by_name = dict(pats)
    slow = []
    for name, unit in _sweep_suspects(pats, units):
        rx = by_name[name]

        def make(n: int, rx=rx, unit=unit) -> Callable[[], object]:
            text = (unit * n)[:n]
            return lambda: list(rx.finditer(text))

        try:
            assert_linear(make, (name, unit), floor=_SWEEP_FLOOR, ladder=_SWEEP_LADDER,
                          limit=_SWEEP_LIMIT)
        except AssertionError as e:
            slow.append(e.args[0])
    assert slow == [], slow


def test_the_linearity_guard_is_not_vacuous():
    """Mutation check, kept in the suite: a quadratic pattern (the W2-OB-7 shape) and a quadratic
    field path must both fail `assert_linear`, even on a quiet machine."""
    quad = re.compile(r"(?:[a-z]\.)+Z")

    def make_rx(n: int) -> Callable[[], object]:
        text = ("a." * n)[:n]
        return lambda: list(quad.finditer(text))

    with pytest.raises(AssertionError):
        assert_linear(make_rx, "quadratic pattern", floor=_SWEEP_FLOOR, ladder=_SWEEP_LADDER,
                      limit=_SWEEP_LIMIT)

    def make_field(n: int) -> Callable[[], object]:
        text = ("a." * n)[:n]
        # A field path that rescans the rest of the text from every position.
        return lambda: sum(1 for i in range(0, len(text), 2) if quad.match(text, i))

    with pytest.raises(AssertionError):
        assert_linear(make_field, "quadratic field path")
