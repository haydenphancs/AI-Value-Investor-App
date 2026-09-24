"""
Round-3 FRAMES (W3 review of the round-2 fix pass): the exemption machinery for promise, forecast
and directive rows — warning, belief, myth and question frames, directive leads, suitability.

Three rounds oscillated between over-blocking and bypasses because each exemption was wider than
its reason: a frame noun anywhere in the segment, a warning object across a comma, a belief the
sentence then agreed with, a question answered yes. Every exemption here is POSITIONAL (the
claim's own clause, its complement, its predicate or its label), and every rule is pinned by a
MUST-REJECT twin and a MUST-PASS twin:

* MUST-REJECT strings are the W3 repros plus the natural variants, through `scan_text` in BOTH
  modes and, where the finding reached a post, end to end through `validate_package` as the
  shared hook (the package is rejected) and as the X caption (the X post is dropped).
* MUST-PASS strings are asserted as an EMPTY violation list (or a published X post) — never as
  the absence of one code, which is how a guard goes vacuous.

The real-model guards are the two corpus fixtures (`test_marketing_content_r2_corpus_gate.py`,
`test_marketing_content_r2_real_drafts.py`); the fresh-run false positives this round fixed are
inlined below (section 10), verbatim.

Category 1 (pure): the Learn bundle and the vendored lists only.
"""

from __future__ import annotations

import copy
import re
from typing import Callable, List

import pytest

from app.services.marketing import compliance as c
from app.services.marketing import content_pool
from app.services.marketing import writer_prompts as wp
from app.services.marketing import writer_service as ws
from test_marketing_content_a_writer_gate import (RUN_DATE, _assert_rejected_everywhere,
                                                  _baseline, _with)
from test_marketing_content_r2_overblock import assert_linear

MM = "money_moves:"
RR, ET, PD, CI, FO, MR = ("journey:risk_reward", "journey:etfs_101", "journey:power_of_discipline",
                          "journey:compound_interest", "journey:fomo_cycle", "journey:mr_market")
HD, TS, NV, VI, CO, AM, AMD, NF, AP, BO = (
    MM + "the-home-depot-vs-lowes", MM + "tsmc-the-foundry-that-runs-the-world",
    MM + "nvidias-ai-dominance", MM + "visa-vs-mastercard", MM + "costcos-membership-magic",
    MM + "how-amazon-built-its-moat", MM + "amd-vs-intel-the-cpu-wars",
    MM + "netflix-vs-disney-plus", MM + "apples-services-revolution",
    MM + "boeing-vs-airbus-the-aerospace-duopoly")


def codes(text: str, strict: bool, **kw) -> List[str]:
    return [v.code for v in c.scan_text("x", c.clean(text), strict_instruments=strict, **kw)]


def _both_modes_empty(text: str) -> None:
    for strict in (True, False):
        got = [(v.code, v.detail) for v in c.scan_text("x", c.clean(text),
                                                        strict_instruments=strict)]
        assert got == [], (text, strict, got)


def _field(key: str, text: str) -> list:
    item = content_pool.get_item(key)
    return [(v.code, v.detail) for v in ws._scan("x", c.clean(text), item, allow_emoji=True)]


def _posted(key: str, text: str) -> None:
    item, pkg = _baseline(key)
    res = ws.validate_package(_with(pkg, "x", text), item, RUN_DATE)
    assert "x" in res.posts, (key, text, [(v.code, v.detail) for v in res.outlets.get("x", [])])


def _flags(text: str, want: str) -> None:
    for strict in (True, False):
        assert want in codes(text, strict), (text, strict, codes(text, strict))


# ── 1. warning OBJECTS and BELIEF frames are positional (W3CB-1) ─────────────────────────────


FRAME_BYPASSES = (
    ("Ignore the hype, compounding guarantees your money grows.", "promissory"),
    ("Ignore the hype, index funds are a safe way to grow your money.", "promissory"),
    ("Avoid the hype, the market always recovers.", "promissory"),
    ("Ignore the tips, the market always recovers.", "promissory"),
    ("Ignore the talk, stocks always go up in the long run.", "promissory"),
    ("Question the hype, the market always goes up over time.", "promissory"),
    # No complement at all: a warning OBJECT governs only what it takes as its complement.
    ("Ignore the hype index funds always go up.", "promissory"),
    # A complement does not reach across a comma into a new clause.
    ("Beware of promises of big returns, index funds always go up.", "promissory"),
    ("Beware of promises of big returns with index funds, which always go up.", "promissory"),
    # The affirmation veto is OPEN for a reported belief: any continuation that is not a turn.
    ("Most investors think stocks always recover, and they are usually right.", "promissory"),
    ("Most investors think stocks always recover, and history backs them up.", "promissory"),
    ("Most investors think stocks always recover, and that has held for a century.",
     "promissory"),
    ("Many say the market always recovers, as history shows.", "promissory"),
    ("Many say the market always recovers; they are usually right.", "promissory"),
    ("Many believe stocks always go up, with good reason.", "promissory"),
    ("Many believe stocks always go up (and they do).", "promissory"),
    ("Many say the market always recovers. They are usually right.", "promissory"),
    ("Many say the market always recovers. History backs them up.", "promissory"),
    # A belief frame reaches across a comma only through "that": a comma splice is two clauses.
    ("Many beginners believe it, stocks always go up.", "promissory"),
    ("Many beginners believe it, and stocks always go up.", "promissory"),
    # A negated belief verb governs its complement, not the next clause (positional too).
    ("Never assume that stocks are safe, the market always recovers.", "promissory"),
    ("If someone says stocks can fall, the market always recovers.", "promissory"),
    # Forecasts: no belief or warning-object exemption without a correction.
    ("Ignore the hype, the S&P 500 will keep climbing.", "class_b_forward"),
    ("Many investors think the S&P 500 will keep climbing, and they're right.",
     "class_b_forward"),
    ("Many investors say the market is due for a rally, and they're right.", "class_b_forward"),
    ("Some say a crash is coming.", "class_b_forward"),
    ("Many people think the market will keep rising.", "class_b_forward"),
    ("Many people believe the market will always recover quickly.", "class_b_forward"),
    ("Many think the S&P 500 will rise next year, but it won't.", "class_b_forward"),
    ("It's a myth that the market will keep climbing, and that is usually true.",
     "class_b_forward"),
    # A REPORTED forecast is a belief frame: it needs its correction too.
    ("Pundits predict the market will keep climbing.", "class_b_forward"),
    ("The hype says the market will keep climbing.", "class_b_forward"),
    ("Some claim a crash is coming.", "class_b_forward"),
    # …and the report frame never widens the PROMISE side (an appeal to authority).
    ("Experts say index funds always go up.", "promissory"),
    ("People say index funds never lose money.", "promissory"),
    # An affirmation a few words after the turn.
    ("Many believe stocks always go up; history says they're right.", "promissory"),
    # A continuation joined by "so" / "because" that EVALUATES is the author's own claim.
    ("Many believe stocks always go up, so they're a safe bet.", "promissory"),
    ("Many believe index funds always go up, because they are safe.", "promissory"),
    # A negated DOUBT asserts: its "no one" / "never" / "don't" exempts nothing.
    ("No one should doubt the market always recovers.", "promissory"),
    ("Nobody doubts that stocks always go up.", "promissory"),
    ("No one questions that index funds never lose money.", "promissory"),
    ("Never forget that the market always recovers.", "promissory"),
    ("Don't underestimate how index funds always go up.", "promissory"),
    ("Nobody can doubt the market will keep climbing.", "class_b_forward"),
    ("Don't hesitate to buy when prices dip.", "class_b_recommendation"),
    ("Don't be afraid to consider buying when prices fall.", "class_b_recommendation"),
    # Tier-1 advice rows get no frame exemption at all.
    ("Ignore the hype, consider buying an index fund.", "class_b_recommendation"),
    ("Ignore the hype, buy when prices drop.", "class_b_recommendation"),
)


@pytest.mark.parametrize("text, code", FRAME_BYPASSES)
def test_a_frame_in_another_clause_or_an_agreed_belief_exempts_nothing(text, code):
    _flags(text, code)


@pytest.mark.parametrize("key, text, code", [
    (PD, "Ignore the hype, compounding guarantees your money grows.", "promissory"),
    (PD, "Most investors think stocks always recover, and they are usually right.", "promissory"),
    (ET, "Ignore the hype, the S&P 500 will keep climbing.", "class_b_forward"),
    (PD, "Many investors think the S&P 500 will keep climbing, and they're right.",
     "class_b_forward"),
    (ET, "Ignore the hype, consider buying an index fund.", "class_b_recommendation"),
    (PD, "Ignore the hype, buy when prices drop.", "class_b_recommendation"),
])
def test_frame_bypasses_never_reach_a_post(key, text, code):
    _assert_rejected_everywhere(key, text, code)


FRAME_LEGIT = (
    "Many beginners believe the market always goes up.",
    "Many beginners believe that, over time, stocks always go up.",
    "Many investors assume the market always recovers quickly after a crash.",
    "Many beginners believe stocks always go up if they wait and hold.",
    "Many beginners believe stocks always go up, especially after a long rally.",
    "Many investors think stocks always go up, so they panic when prices fall.",
    "Many believe stocks always go up because they have only seen a bull market.",
    "A common belief is that index funds never lose money.",
    "Many doubt the market will keep climbing.",
    "It is tempting to think stocks always recover, but history shows long slumps.",
    "It's a common mistake to think stocks always go up.",
    "A common belief is that investing in an ETF means no risk. This isn't quite true.",
    "Many say the market always recovers. They're wrong.",
    "Beware of promises of high, guaranteed returns with no risk.",
    "Be wary of promises for big rewards with no risk.",
    "Beware of promises like guaranteed returns or zero risk.",
    "Ignore claims that index funds always go up.",
    "Avoid schemes that promise returns with no risk.",
    "Walk away from anyone promising guaranteed returns.",
    "Question anyone who says stocks always go up.",
    "Be alert to promises of big rewards without risk; this combination does not exist in "
    "honest investing.",
    "Finally, be highly skeptical of any promise of significant rewards with absolutely no "
    "risk; this combination does not exist in legitimate investing practices.",
    # A reported forecast WITH its correction, and a universal one debunked next.
    "Many investors think the market will keep climbing, but nobody knows.",
    "Many people believe the market will always recover. That is a myth.",
    "Pundits predict the market will keep climbing, but nobody knows.",
    "Ignore claims that the market will keep climbing.",
    "Beware of claims that a crash is coming.",
    "Don't believe the hype that the market will keep climbing.",
    "Some say stocks will always go up, but they won't.",
    # The original negated-belief frames, still across their own parenthetical.
    "Never assume that, over time, stocks always go up.",
    "Don't assume, as many do, that the market always recovers.",
    "Nobody knows whether, after a crash, the market will recover.",
    "Beware anyone who says, the market always recovers.",
)


@pytest.mark.parametrize("text", FRAME_LEGIT)
def test_a_frame_that_holds_its_claim_still_exempts_it(text):
    _both_modes_empty(text)


# ── 2. a yes/no question exempts only when unanswered or debunked (W3CB-2) ───────────────────


QUESTION_BYPASSES = (
    ("Does the stock market always recover? History says yes.", "promissory"),
    ("Can index funds make you rich? Slowly, yes.", "promissory"),
    ("Do index funds always go up over time? Over the long run, yes.", "promissory"),
    ("Do stocks always go up over the long run? It always has.", "promissory"),
    ("Will the market always recover? Historically, yes.", "promissory"),
    ("Do index funds always go up? Over decades they have.", "promissory"),
    ("Can you get rich with index funds? Many people have.", "banned_phrase"),
    ("Is investing in index funds risk-free? For the long-term investor, pretty much.",
     "banned_phrase"),
    # Questions that PRESUPPOSE the claim.
    ("Have you noticed how the market always bounces back?", "promissory"),
    ("Is it a coincidence that the market always recovers?", "promissory"),
    ("Would you rather own a fund that never loses value?", "promissory"),
    ("Did you ever wonder why stocks always go up?", "promissory"),
    # An answer that only looks like a debunk.
    ("Do stocks always go up? Not only that, they compound.", "promissory"),
    ("Do stocks always go up? Not in a straight line.", "promissory"),
    ("Stocks always go up. Not every day, but always over time.", "promissory"),
    ("Stocks always go up. No, really.", "promissory"),
    # A debunk NARROWED to something else debunks nothing.
    ("Stocks always go up. That's wrong for most funds, though.", "promissory"),
    # A question that turns into a pitch after the claim asks nothing.
    ("Can index funds make you rich, so why wait?", "promissory"),
    ("Do stocks always go up? Nope, they always recover.", "promissory"),
)


@pytest.mark.parametrize("text, code", QUESTION_BYPASSES)
def test_a_question_answered_with_anything_but_a_debunk_is_the_claim(text, code):
    _flags(text, code)


@pytest.mark.parametrize("key, text", [
    (RR, "Does the stock market always recover? History says yes."),
    (CI, "Can index funds make you rich? Slowly, yes."),
    (RR, "Have you noticed how the market always bounces back?"),
])
def test_an_answered_question_never_reaches_a_post(key, text):
    _assert_rejected_everywhere(key, text, "promissory")


QUESTION_LEGIT = (
    "Can you get big rewards with zero risk?",
    "Do stocks always go up?",
    "Is it true that stocks always go up?",
    "Do stocks always go up? Not always.",
    "Do stocks always go up? No. Some years they fall.",
    "Do stocks always go up? No, they don't.",
    "Do stocks always go up? Nope.",
    "Can you get big rewards with zero risk? No.",
    "Do stocks always go up? Not over every stretch.",
    "Big rewards with no risk? It's a myth.",
    "Stocks always go up. That's a myth.",
    "Many new investors believe ETFs are completely risk-free. This is a common myth among "
    "beginners.",
    "ETFs: Myth vs. Fact - Are They Really Without Risk?",
    "ETFs: Myth vs. Fact - Are They Really Risk-Free?",
)


@pytest.mark.parametrize("text", QUESTION_LEGIT)
def test_an_unanswered_or_debunked_question_is_not_a_promise(text):
    _both_modes_empty(text)


def _shared(res) -> list:
    return [(v.field, v.code) for v in res.shared]


def test_a_question_answered_in_the_next_script_line_is_the_claim():
    """Cross-field: the hook's question is answered by the first script line."""
    item, pkg = _baseline(RR)
    pkg = copy.deepcopy(pkg)
    pkg["hook"] = "Does the stock market always recover?"
    pkg["video_script"][0] = "Yes. Every single time so far."
    res = ws.validate_package(pkg, item, RUN_DATE)
    assert not res.ok and ("hook", "promissory") in _shared(res), _shared(res)
    # The debunk twin: the same question, answered "Not always."
    pkg["video_script"][0] = "Not always. Some recoveries took many years."
    res = ws.validate_package(pkg, item, RUN_DATE)
    assert res.ok and not res.shared, _shared(res)


def test_a_question_titled_card_is_answered_by_its_body():
    item, pkg = _baseline(RR)
    pkg = copy.deepcopy(pkg)
    pkg["cards"][0] = {"title": "Does the market always recover?",
                       "body": "It always has, every single time."}
    res = ws.validate_package(pkg, item, RUN_DATE)
    assert not res.ok and ("cards[0].title", "promissory") in _shared(res), _shared(res)
    pkg["cards"][0] = {"title": "Do stocks always go up?", "body": "Not always. Some years they "
                                                                   "fall."}
    res = ws.validate_package(pkg, item, RUN_DATE)
    assert res.ok and not res.shared, _shared(res)


def test_a_script_line_question_is_answered_by_the_next_line():
    item, pkg = _baseline(RR)
    pkg = copy.deepcopy(pkg)
    pkg["video_script"][2] = "Do stocks always go up over the long run?"
    pkg["video_script"][3] = "Yes. Every single time so far."
    res = ws.validate_package(pkg, item, RUN_DATE)
    assert ("video_script[2]", "promissory") in _shared(res), _shared(res)


def test_the_field_scan_reads_only_the_next_fields_first_sentence():
    """Unit: `next_text` answers a closing question; an empty one leaves it unanswered."""
    q = "Do stocks always go up?"
    assert codes(q, False) == []
    assert "promissory" in codes(q, False, next_text="Yes. And they always will.")
    assert codes(q, False, next_text="Not always. They fell in 2008.") == []
    # A debunk in the next field covers a closing CLAIM too, as a next sentence would.
    assert codes("Stocks always go up.", False, next_text="That's a myth.") == []


# ── 3. a warning frame holds its claim as SUBJECT or COMPLEMENT (W3CB-5) ────────────────────


WARNING_BYPASSES = (
    ("Unlike crypto scams, the S&P 500 always recovers.", "promissory"),
    ("Unlike crypto scams, the S&P 500 will keep climbing.", "class_b_forward"),
    ("Crypto is full of scams, while index funds always recover.", "promissory"),
    ("Hot tips are a red flag, and index funds are a safe way to grow your money.",
     "promissory"),
    ("Unlike a crypto scam, index funds are guaranteed to grow.", "banned_phrase"),
    ("Unlike crypto fraud, index funds are risk-free.", "banned_phrase"),
    ("Unlike get-rich-quick scams, index funds always go up over time.", "promissory"),
    ("Stay away from scams, and your savings will keep growing.", "promissory"),
    ("Crypto scams promise overnight riches, while index funds always recover over time.",
     "promissory"),
    ("Scams aside, index funds always go up.", "promissory"),
    # Predicate position needs a copula and no cut; a negated debunk affirms.
    ("Index funds always go up unlike crypto scams.", "promissory"),
    ("Index funds always go up and scams don't.", "promissory"),
    ("Index funds always go up, which is no myth.", "promissory"),
    ("Index funds always go up, which isn't a myth.", "promissory"),
    ("Stocks always go up, but that's not a myth.", "promissory"),
    ("Index funds always go up, which is not true of single stocks.", "promissory"),
    ("Index funds always go up, and that is not a myth.", "promissory"),
    # A frame the sentence then affirms, with a qualifier.
    ("It is a myth that stocks always go up, and that is usually true.", "promissory"),
)


@pytest.mark.parametrize("text, code", WARNING_BYPASSES)
def test_a_warning_about_something_else_frames_nothing(text, code):
    _flags(text, code)


@pytest.mark.parametrize("text, code", [
    ("Unlike crypto scams, the S&P 500 always recovers.", "promissory"),
    ("Unlike crypto fraud, index funds are risk-free.", "banned_phrase"),
])
def test_a_contrast_with_scams_never_reaches_a_post(text, code):
    _assert_rejected_everywhere(RR, text, code)


WARNING_LEGIT = (
    "It is a myth that stocks always go up.",
    "It's a common myth that ETFs are entirely risk-free.",
    "It's a red flag when someone says index funds always go up.",
    "Scams promise returns that never lose money.",
    "Promises of big rewards with no risk are a warning.",
    "Anyone who promises returns with no risk is running a scam.",
    "The idea that stocks always go up is a common misconception.",
    "The idea that index funds never lose money is a myth.",
    "Believing that stocks always go up is a common mistake.",
    "Thinking the market always recovers quickly is a trap.",
    "If someone promises big rewards with no risk at all, that combination doesn't exist.",
    "'Guaranteed high returns' do not exist in honest investing.",
    "'Guaranteed high returns' should always be seen as a warning sign.",
    'The words "guaranteed high returns" aren\'t a gift.',
    "Index funds always go up over time, but that is a common myth.",
    "Many new investors think ETFs are entirely risk-free, but that's a common misconception.",
    "Investing in an ETF means zero risk. This isn't quite true.",
)


@pytest.mark.parametrize("text", WARNING_LEGIT)
def test_a_frame_whose_subject_or_complement_is_the_claim_still_exempts_it(text):
    _both_modes_empty(text)


# ── 4. Journey directives: any comma, a dash, the inverted suitability order (W3CB-7) ───────


DIRECTIVE_BYPASSES = (
    "The right choice for most beginners is an S&P 500 index fund.",
    "For most beginners, the smart choice is a low-cost index fund.",
    "The best choice for most beginners is a broad index fund.",
    "The best first step for a beginner is an index fund.",
    "For beginners, the simplest move is to buy an index fund.",
    "The smart move for beginners is to buy a broad index fund.",
    "The right move is to buy low-cost index funds.",
    "If you are new to investing, keep it simple, pick an S&P 500 index fund.",
    "Keep it simple, stay calm, and buy an index fund.",
    "Your first step is easy - buy a broad index fund.",
    "Keep it simple - buy an S&P 500 index fund.",
    "Keep it simple — buy an S&P 500 index fund.",
    "You can't go wrong with an S&P 500 index fund.",
    "You can't go wrong with a diversified index fund.",
    # A comma-led verb after a third-person clause, but no list closes it: a comma splice.
    "Some investors panic when prices fall, buy stocks.",
    "Investors, buy an index fund.",
    "Investors, just buy index funds and hold.",
    "Stay calm then buy an index fund.",
    # The actor must OPEN its clause ("Ask others" opens with a verb), and a subordinate clause
    # ends the walk back to a list's subject.
    "Ask others then buy stocks.",
    "Investors see a crash, when that happens, buy stocks, and hold.",
    "Many investors wisely stay calm, buy stocks, and hold.",
    # "They" can point back at an approving subject in the sentence before.
    "The smartest investors? They just buy index funds.",
)


@pytest.mark.parametrize("text", DIRECTIVE_BYPASSES)
def test_a_directive_after_any_comma_or_dash_or_inverted_is_a_recommendation(text):
    _flags(text, "class_b_recommendation")


@pytest.mark.parametrize("key, text", [
    (ET, "The right choice for most beginners is an S&P 500 index fund."),
    (ET, "Your first step is easy - buy a broad index fund."),
    (ET, "You can't go wrong with an S&P 500 index fund."),
    (AM, "For most beginners, the smart choice is a low-cost index fund."),
])
def test_softened_directives_never_reach_a_post(key, text):
    _assert_rejected_everywhere(key, text, "class_b_recommendation")


DIRECTIVE_LEGIT = (
    "The right choice is the one you can stick with.",
    "The smartest move is often doing nothing.",
    "A good first step is to learn the basics.",
    "You can't go wrong with patience.",
    "When prices soar, some investors panic, sell stocks, and miss the rebound.",
    "Many beginners see a rally, buy stocks late, and sell in fear.",
    "Investors see a rally, get excited, and buy stocks at the top.",
    "Investors who hold, sell, or buy stocks pay fees.",
    "That's why many people use a broad ETF as the calm, steady core of their plan, then add "
    "a few single stocks around it.",
)


@pytest.mark.parametrize("text", DIRECTIVE_LEGIT)
def test_a_serial_description_or_a_non_investable_choice_is_not_a_directive(text):
    _both_modes_empty(text)


# ── 5. "never failed to <an outcome>" is a track-record promise (W3CB-8) ─────────────────────


NEVER_FAILED = (
    "The market has never failed to bounce back.",
    "The S&P 500 has never once failed to recover.",
    "Index funds have never failed to reward patient investors.",
    "Stocks have never failed to recover from a crash.",
    "Patient investing has never failed to pay off.",
    "Compounding never fails to grow your money.",
    "Long-term investors have never failed to make money.",
    "Markets have never failed to recover, though some took decades.",
    "The market has never failed to eventually recover.",
    "Stocks have never failed to reach new highs.",
    "The market has never failed to grow over twenty years.",
    "Discipline has never failed to pay off for patient investors.",
)


@pytest.mark.parametrize("text", NEVER_FAILED)
def test_never_failed_to_an_outcome_is_promissory(text):
    _flags(text, "promissory")


@pytest.mark.parametrize("text", ["The market has never failed to bounce back.",
                                  "Index funds have never failed to reward patient investors."])
def test_never_failed_to_recover_never_reaches_a_post(text):
    _assert_rejected_everywhere(RR, text, "promissory")


@pytest.mark.parametrize("text", [
    "Never fail to diversify.",
    "Never fail to read the annual report.",
    "The market never fails to surprise.",
    "Crashes never fail to scare new investors.",
    "The company never failed to pay its bills.",
    "Netflix never failed to grow its subscriber base.",
    "Don't assume the market has never failed to recover.",
])
def test_never_failed_to_something_else_is_not_a_promise(text):
    assert "promissory" not in codes(text, True) and "promissory" not in codes(text, False), text


# ── 6. a third-person description is not a directive (W3OB-1, the fomo_cycle real draft) ────


@pytest.mark.parametrize("key, text", [
    (ET, "Many beginners simply start with a broad ETF."),
    ("journey:common_mistakes", "Many beginners simply pick a stock everyone is shouting about."),
    (ET, "Most beginners today start with an ETF."),
    (FO, "Investors might then pick a stock near the peak."),
    (FO, "Investors then buy stocks at the top."),
    (FO, "It's an emotional pattern that often leads investors to buy when prices are high and "
         "sell when prices are low."),
    (FO, "Emotional investors buy when prices are high."),
    (BO, "A new rival can't just get into the market; certification takes a decade."),
    (TS, "Designers just go with TSMC because it never competes with them."),
    (NV, "Labs just stay with NVIDIA rather than rewrite their tools."),
    (CO, "Shoppers just stay with Costco year after year."),
    (TS, "Chip designers now choose TSMC because it never competes with them."),
    (NF, "Many households now choose Netflix over cable."),
    (AMD, "Data centers now pick AMD for many servers."),
    (VI, "Merchants simply choose Visa because every customer carries one."),
    (AP, "Once in the ecosystem, users simply stay with Apple."),
    (AMD, "PC makers then bet on AMD chips."),
])
def test_what_people_or_customers_do_is_described_not_directed(key, text):
    assert _field(key, text) == [], (key, text, _field(key, text))


@pytest.mark.parametrize("text", [
    "Smart investors simply start with a low-cost index fund.",
    "You can simply pick an index fund and forget about it.",
    "Wise savers just pick an S&P 500 index fund and forget it.",
    "You can just start with an S&P 500 ETF.",
    "Smart investors simply buy an index fund and wait.",
    "Many successful investors simply buy an index fund.",
    "Smart investors see a dip, and buy index funds.",
    "People like you simply buy index funds.",
    "Investors who see a dip just buy index funds.",
    "Why not simply buy an index fund?",
    "Just buy an index fund.",
    "Beginners can then buy an index fund.",
    "Investors should simply buy index funds.",
    "When his fear makes prices low, you can consider buying.",
    "Smart investors buy when prices are low.",
    "Buy low and sell high.",
])
def test_an_endorsed_or_second_person_behaviour_is_still_a_directive(text):
    _flags(text, "class_b_recommendation")


def test_the_directive_detail_names_the_company_not_the_placeholder():
    """A repair prompt must quote the sentence the model wrote, not "now choose zzco"."""
    got = _field(NV, "Smart investors simply pick NVIDIA.")
    rec = [d for code, d in got if code == "class_b_recommendation"]
    assert rec and all(c.COMPANY_MARK.lower() not in d for d in rec), got
    assert any("nvidia" in d for d in rec), got


# ── 7. a company is its CUSTOMERS' natural choice, never an investor's (W3OB-5) ─────────────


@pytest.mark.parametrize("key, text", [
    (HD, "Home Depot is the natural choice for contractors."),
    (HD, "Lowe's is the natural choice for a weekend project."),
    (TS, "TSMC is the natural choice for fabless designers."),
    (TS, "TSMC is the safe choice for designers because it never competes with them."),
    (NV, "NVIDIA is the natural choice for researchers who already know CUDA."),
    (VI, "Visa and Mastercard are the obvious choice for merchants who want to be paid."),
    (CO, "Costco is the obvious choice for families who buy in bulk."),
    (AMD, "AMD is the smart choice for data centers."),
])
def test_a_customer_choice_is_a_case_study(key, text):
    assert _field(key, text) == [], (key, text, _field(key, text))


@pytest.mark.parametrize("key, text", [
    (HD, "Home Depot is the smart choice for investors."),
    (HD, "Home Depot is the right choice for your portfolio."),
    (HD, "For long-term investors, Home Depot is the obvious choice."),
    (HD, "Home Depot is the best choice for beginners."),
    (NV, "Nvidia is the obvious pick for anyone investing in AI."),
    (NV, "Nvidia is the natural choice for an AI portfolio."),
    (TS, "TSMC is a solid choice for patient investors."),
    (HD, "Home Depot is the obvious choice."),
    (HD, "Home Depot is the safe bet for contractors who want to own stock."),
    (HD, "Home Depot is the natural choice for contractors, and investors love it."),
    # "buyers" may be buyers of the stock: not a customer audience.
    (NV, "NVIDIA is the obvious choice for buyers."),
])
def test_a_company_suitability_verdict_for_investors_is_a_recommendation(key, text):
    got = {code for code, _d in _field(key, text)}
    assert "class_b_recommendation" in got, (key, text, got)


# ── 8. a Myth-labelled misconception in the future tense (W3OB-4) ────────────────────────────


@pytest.mark.parametrize("text", [
    "Myth: the market will always recover quickly.",
    "Myth: stocks will always go up in the long run.",
    "Myth: index funds will always beat single stocks.",
    "Myth one: stocks always go up.",
    "Here is a common myth: the market will always recover quickly.",
])
def test_a_labelled_universal_forecast_is_the_misconception(text):
    _both_modes_empty(text)


MYTH_FORECAST_LEGIT = (
    "Myth: the market will always recover quickly.",
    "Myth: stocks will always go up in the long run.",
    "Myth: index funds will always beat single stocks.",
    "Here is a common myth: the market will always recover quickly.",
)


@pytest.mark.parametrize("text", [
    "Myth: stocks are risky; fact: the market will rise.",
    "Myth: stocks are risky. The market will rise.",
    "It's not a myth: the market will rise.",
    # Not universal: its negation is a forecast of the opposite.
    "Myth: the S&P 500 will rise next year.",
    "Myth: the market will keep climbing.",
    # A named company is never exempt.
    "Myth: the market will always beat Costco.",
])
def test_a_forecast_the_label_cannot_hold_is_still_a_forecast(text):
    _flags(text, "class_b_forward")


@pytest.mark.parametrize("title, body, ok", [
    ("Myth", "Stocks will always go up in the long run.", True),
    ("The Myth", "The market will always recover.", True),
    ("Myth One", "Stocks always go up.", True),
    ("Myth No. 1", "Stocks always go up.", True),
    ("Myth Number One", "Stocks always go up.", True),
    ("Myth", "The market will rise next year.", False),
    ("Myth vs. Fact", "The market will always recover.", False),
])
def test_a_myth_titled_card_labels_a_universal_forecast_in_its_body(title, body, ok):
    item, pkg = _baseline(RR)
    pkg = copy.deepcopy(pkg)
    pkg["cards"][0] = {"title": title, "body": body}
    res = ws.validate_package(pkg, item, RUN_DATE)
    assert res.ok is ok, (title, body, _shared(res))


# ── 9. pins for guards no test decided (W3VAC-04, W3VAC-05) ─────────────────────────────────

#: A COMMA and a turn word only `_MYTH_TURN_RE` knows (fact / truth / reality / actually / in
#: fact / however): a "." / ";" / "but" case is decided by the hard cut and proves nothing.
COMMA_TURN_BYPASSES = (
    "Myth: stocks are a gamble, in reality stocks always recover.",
    "The myth: stocks are too risky, the truth is index funds always go up.",
    "Myth: investing is risky, in fact the market always recovers.",
    "Myth: stocks are too risky, actually the market always recovers.",
    "Myth: index funds are boring, however they always go up.",
)


@pytest.mark.parametrize("text", COMMA_TURN_BYPASSES)
def test_a_turn_inside_the_labelled_clause_ends_the_label(text):
    _flags(text, "promissory")


def test_a_turn_inside_a_myth_titled_body_ends_the_label():
    item, pkg = _baseline(RR)
    pkg = copy.deepcopy(pkg)
    pkg["cards"][0] = {"title": "The myth", "body": "Stocks are too risky for beginners, in "
                                                    "reality index funds always go up."}
    res = ws.validate_package(pkg, item, RUN_DATE)
    assert not res.ok and ("cards[0].body", "promissory") in _shared(res), _shared(res)


@pytest.mark.parametrize("body, phrase", [
    ("Stocks can fall. Index funds are risk-free.", "risk-free"),
    ("Patience matters. Index funds help you get rich.", "get rich"),
    ("Stocks can fall. With index funds you can't lose.", "can't lose"),
])
def test_a_myth_title_labels_only_its_bodys_first_sentence_for_banned_promises(body, phrase):
    got = [(v.code, v.detail) for v in c.scan_text("b", body, myth_framed=True)]
    assert ("banned_phrase", phrase) in got, got


def test_the_myth_titles_first_sentence_is_still_labelled_for_banned_promises():
    """The twin: the labelled FIRST sentence stays exempt, so the test above cannot be passed by
    dropping the title label altogether."""
    assert c.scan_text("b", "Index funds are risk-free. Stocks can fall.", myth_framed=True) == []
    assert "banned_phrase" in codes("Index funds are risk-free. Stocks can fall.", True)


# ── 10. the fresh real run's false positives (2026-09-24, round-2 rules), verbatim ──────────

#: journey:etfs_101 (myth_vs_fact) and journey:fomo_cycle (checklist): every one of these was
#: rejected by the round-2 gate in a real gemini-2.5-flash run and is honest copy.
FRESH_RUN_HONEST = (
    (ET, "carousel_slides.body", "A common belief is that investing in an ETF means zero risk. "
                                 "This isn't quite true."),
    (ET, "carousel_slides.body", "A common belief is that investing in an ETF means no risk. "
                                 "This isn't quite true."),
    (ET, "carousel_slides.title", "Myth: ETFs are risk-free"),
    (ET, "youtube_title", "ETFs: Myth vs. Fact - Are They Really Risk-Free?"),
    (ET, "youtube_title", "ETFs: Myth vs. Fact - Are They Really Without Risk?"),
    (ET, "tiktok", "Many think ETFs are without risk. That's a myth. An ETF is a basket of "
                   "stocks, spreading your money across many companies for diversification."),
    (ET, "instagram", "Many new investors believe ETFs are completely without risk. This is a "
                      "common myth. An ETF is essentially a basket filled with many different "
                      "stocks."),
    (ET, "youtube_description", "A common misconception about ETFs is that they are entirely "
                                "risk-free. An ETF is like a basket of many stocks."),
    (ET, "facebook", "It's a common myth that ETFs are entirely without risk. An ETF functions "
                     "like a basket holding many different stocks."),
    (ET, "linkedin", "A common misconception among new investors is that Exchange Traded Funds "
                     "(ETFs) are entirely risk-free. In reality, an ETF is structured like a "
                     "basket containing numerous different stocks."),
    (ET, "video_script", "Many new investors believe ETFs are entirely risk-free, but that's a "
                         "common misconception."),
    (FO, "carousel_slides.body", "It's an emotional pattern that often leads investors to buy "
                                 "when prices are high and sell when prices are low."),
    (RR, "carousel_slides.body", "If someone promises big rewards with no risk, it's a warning. "
                                 "'Guaranteed high returns' do not exist in honest investing."),
)


@pytest.mark.parametrize("key, field, text", FRESH_RUN_HONEST)
def test_the_fresh_runs_honest_lines_pass_the_field_scan(key, field, text):
    item = content_pool.get_item(key)
    emoji = "." not in field and field not in ("hook", "video_script")
    got = [(v.code, v.detail) for v in ws._scan(field, c.clean(text), item, allow_emoji=emoji)]
    assert got == [], (key, text, got)


def test_the_etfs_101_myth_slide_passes_the_gate_as_a_pair():
    item, pkg = _baseline(ET)
    pkg = copy.deepcopy(pkg)
    pkg["carousel_slides"][0] = {"title": "Myth: ETFs are risk-free",
                                 "body": "A common belief is that investing in an ETF means zero "
                                         "risk. This isn't quite true."}
    pkg["captions"]["youtube_title"] = "ETFs: Myth vs. Fact - Are They Really Without Risk?"
    res = ws.validate_package(pkg, item, RUN_DATE)
    assert res.ok and not res.violations, [(v.field, v.code, v.detail) for v in res.violations]


# ── 11. business pricing is not a price verdict (W3OB-7) ─────────────────────────────────────


@pytest.mark.parametrize("key, text", [
    (CO, "Costco wins loyalty with a fair price and a tight selection."),
    (HD, "Home Depot wins pros with deep stock and a fair price."),
    (AM, "Amazon wins shoppers with fast shipping and a great price."),
    (CO, "Costco earns trust with a fair price."),
])
def test_a_price_for_customers_is_the_store(key, text):
    assert _field(key, text) == [], (key, text, _field(key, text))


@pytest.mark.parametrize("key, text", [
    (VI, "Visa has a wide moat and a great price."),
    (VI, "Visa pairs a wide moat with a fair price."),
    (VI, "Visa combines a wide moat with a fair price."),
    (CO, "Costco is a great deal for owners who hold for years."),
    (NV, "NVIDIA was a great deal for owners."),
])
def test_a_price_for_owners_or_on_a_moat_is_still_a_verdict(key, text):
    assert "class_b_valuation" in {code for code, _d in _field(key, text)}, (key, text)


# ── anti-vacuity: every MUST-PASS string above is matched by a row BEFORE its exemption ─────


def _raw_hit(text: str) -> bool:
    """A row matches the text before any exemption runs — so the empty scan above is the
    exemption's doing, not an unmatched string's."""
    folded = c.fold(c.clean(text))
    sk = c.skeleton(c.clean(text))
    views = [view for _s, _m, _f, view in c._prepared_sentences(sk)]
    if any(rx.search(folded) for rx in c._PROMISE_RES + c._SAFETY_RES) \
            or c._FORECAST_RE.search(folded):
        return True
    if any(m.group(0) in c._PROMISE_BANNED for m in c._BANNED_RE.finditer(folded)):
        return True
    rows = c._FORECAST_RES + c._DIRECTIVE_RES + tuple(
        re.compile(p) for p in (c._BUY_WHEN_ROW, c._CONSIDER_ROW))
    return any(rx.search(v) for rx in rows for v in views)


@pytest.mark.parametrize("text", FRAME_LEGIT + QUESTION_LEGIT + WARNING_LEGIT
                         + MYTH_FORECAST_LEGIT + DIRECTIVE_LEGIT[4:8]
                         + tuple(t for _k, _f, t in FRESH_RUN_HONEST if "Myth:" not in t))
def test_every_exempted_must_pass_string_is_matched_before_its_exemption(text):
    assert _raw_hit(text), text


# ── 12. the prompt states the round-3 rules, and the repair hints say what to do ────────────


@pytest.mark.parametrize("phrase", [
    "answer it no or not always first", "without agreeing with it", "a warning about something "
    "else", "ignore the hype", "the smart move", "can't go wrong", "a model to copy",
    "the natural choice for contractors", "never failed to recover",
    "a myth about what the market will do may be labelled only when it says always or never",
    "nobody knows", "no one should doubt",
])
def test_the_system_body_states_the_round_3_rules(phrase):
    assert phrase in wp.SYSTEM_BODY.lower(), phrase


@pytest.mark.parametrize("code, phrase", [
    ("promissory", "'not always'"), ("promissory", "unlike scams"),
    ("class_b_forward", "nobody knows"), ("class_b_forward", "'always' or 'never'"),
    ("class_b_recommendation", "can't go"), ("class_b_recommendation", "customers"),
])
def test_the_repair_hints_name_the_round_3_rules(code, phrase):
    assert phrase in wp.REPAIR_HINTS[code].lower(), (code, phrase)


def test_the_prompt_version_moved():
    assert wp.PROMPT_VERSION >= "2026-09-24.4"


# ── 13. linear time on every new path ────────────────────────────────────────────────────────


@pytest.mark.parametrize("unit", [
    "Beware of promises of ", "Many think ", "Ignore the hype, ", "leads investors to buy when ",
    "investors just go with ", ", sell stocks", "Do stocks always go up? ", "never failed to ",
    "The right choice for beginners is ", "Unlike crypto scams, ", "high, guaranteed ",
    "Myth: a (ETFs) ", "that, ", "No, ", "zzco is the natural choice for contractors ",
])
def test_a_degenerate_field_on_a_new_path_scans_in_linear_time(unit):
    item = content_pool.get_item(ET)

    def run(n: int) -> Callable[[], None]:
        text = (unit * (n // len(unit) + 1))[:n]
        return lambda: ws._scan("f", text, item, allow_emoji=True, next_text=text)

    # Load-robust (thread CPU time, interleaved, confirmed twice) — round 3, W3OB-8/W3VAC-09.
    assert_linear(run, unit)
