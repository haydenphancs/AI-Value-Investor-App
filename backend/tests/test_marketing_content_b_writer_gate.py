"""
The writer's gate end to end (`writer_service.validate_package`) for the content-B review:
named-company value and price opinions, predictions, advice, return claims and re-attached
numbers — in Money Moves AND in Journey posts.

Why a separate file: `compliance.scan_text` and `grounding.check_grounding` are unit-tested in
their own files, but what reaches a public post is decided by `validate_package` — the shared
fields must be clean or the package is rejected, and a failing caption drops only its outlet.
Every attack string below was ACCEPTED by `validate_package` before the fix (the review's
repro); each test is the per-item proof that it is not any more, in a shared field (the hook)
AND as the X caption. The baseline package for each item is built from that item's OWN fact
sentences and asserted clean first, so a rejection proves something about the injected string.

Where one policy has two layers (a company outside the fact sheet is BOTH an ungrounded entity
and, with a verdict, class B), the layer-specific tests below pin each layer on its own, so a
regression in either one fails a test even while the other still rejects the post.

Category 1 (pure): no network, no Supabase; the Learn bundle and the vendored lists only.
"""

from __future__ import annotations

import copy
import re
from datetime import date
from typing import Any, Dict, FrozenSet, List, Tuple

import pytest

from app.services.marketing import compliance as c
from app.services.marketing import content_pool
from app.services.marketing import grounding as g
from app.services.marketing import writer_prompts as wp
from app.services.marketing import writer_service as ws

RUN_DATE = date(2026, 9, 21)

NV = "money_moves:nvidias-ai-dominance"
AM = "money_moves:how-amazon-built-its-moat"
VI = "money_moves:visa-vs-mastercard"
TE = "money_moves:tesla-vs-traditional-auto"
CO = "money_moves:costcos-membership-magic"
TS = "money_moves:tsmc-the-foundry-that-runs-the-world"
AP = "money_moves:apples-services-revolution"
HD = "money_moves:the-home-depot-vs-lowes"
MS = "money_moves:microsofts-cloud-metamorphosis"
ME = "money_moves:metas-metaverse-pivot"
AMD = "money_moves:amd-vs-intel-the-cpu-wars"
NF = "money_moves:netflix-vs-disney-plus"
LV = "money_moves:the-rise-of-lvmh"
KS = "journey:key_statistics"
EM = "journey:economic_moats"
MR = "journey:mr_market"
SB = "journey:stock_vs_business"
ET = "journey:etfs_101"
FO = "journey:fomo_cycle"
CI = "journey:compound_interest"
IT = "journey:inflation_thief"

_BASELINES: Dict[str, Tuple[content_pool.ContentItem, Dict[str, Any]]] = {}


def _usable(item: content_pool.ContentItem) -> List[str]:
    out = [s for s in item.fact_sentences
           if s.endswith(".") and 6 <= len(s.split()) <= 20 and "Mr." not in s
           and not ws._scan("x", s, item, allow_emoji=False)]
    assert len(out) >= 12, (item.key, len(out))
    return out


def _baseline(key: str) -> Tuple[content_pool.ContentItem, Dict[str, Any]]:
    if key in _BASELINES:
        return _BASELINES[key]
    item = content_pool.get_item(key)
    assert item is not None and item.eligible, key
    gs = _usable(item)
    short = min(gs, key=len)
    pkg = {
        "hook": min(gs[:6], key=lambda s: len(s.split())),
        "video_script": gs[:8],
        "cards": [{"title": " ".join(gs[i].split()[:4]).rstrip(",.:;"), "body": gs[i + 1]}
                  for i in (0, 2, 4)],
        "carousel_slides": [{"title": " ".join(gs[i].split()[:4]).rstrip(",.:;"),
                             "body": f"{gs[i + 1]} {gs[i + 2]}"} for i in range(0, 10, 2)],
        "captions": {
            "tiktok": " ".join(gs[0:2]), "youtube_title": short,
            "youtube_description": " ".join(gs[2:4]), "instagram": " ".join(gs[3:5]),
            "facebook": " ".join(gs[4:6]), "x": short, "threads": gs[6], "bluesky": short,
            "linkedin": " ".join(gs[:3]),
        },
    }
    base = ws.validate_package(pkg, item, RUN_DATE)
    assert base.ok and not base.violations, (key, [(v.field, v.code, v.detail)
                                                   for v in base.violations])
    _BASELINES[key] = (item, pkg)
    return item, pkg


def _with(pkg: Dict[str, Any], field: str, text: str) -> Dict[str, Any]:
    out = copy.deepcopy(pkg)
    if field == "hook":
        out["hook"] = text
    else:
        out["captions"][field] = text
    return out


def _hook_codes(key: str, text: str) -> set:
    item, pkg = _baseline(key)
    res = ws.validate_package(_with(pkg, "hook", text), item, RUN_DATE)
    return {v.code for v in res.shared if v.field == "hook"}


def _assert_rejected_everywhere(key: str, text: str, codes: FrozenSet[str]) -> None:
    """The hook rejects the package with one of `codes`, and the X caption drops its outlet
    with one of `codes` while every other outlet still publishes (a scoped rejection)."""
    item, pkg = _baseline(key)
    shared = ws.validate_package(_with(pkg, "hook", text), item, RUN_DATE)
    got = {v.code for v in shared.shared if v.field == "hook"}
    assert not shared.ok and got & codes, (key, text, sorted(got))
    cap = ws.validate_package(_with(pkg, "x", text), item, RUN_DATE)
    assert "x" not in cap.posts, (key, text)
    got = {v.code for v in cap.outlets.get("x", [])}
    assert got & codes, (key, text, sorted(got))
    assert cap.ok and len(cap.posts) == len(ws.PLATFORMS) - 1


VAL = frozenset({"class_b_valuation"})
REC = frozenset({"class_b_recommendation"})
FWD = frozenset({"class_b_forward"})
EVAL = frozenset({"class_b_evaluative", "class_b_valuation"})
OPINION = frozenset({"class_b_valuation", "class_b_recommendation", "class_b_forward",
                     "class_b_evaluative"})


# ── idx 3: Money Moves — the named company as the instrument ────────────────


@pytest.mark.parametrize("key, text, codes", [
    (NV, "NVIDIA is a buy.", REC),
    (NV, "Buy NVIDIA.", REC),
    (NV, "NVIDIA is richly valued.", VAL),
    (NV, "NVIDIA is in a bubble.", VAL),
    (NV, "NVIDIA is likely to outperform.", FWD),
    (NV, "NVIDIA was a great investment.", REC),
    (NV, "Hold NVIDIA forever.", REC),
    (AM, "Buy Amazon and hold it for decades.", REC),
    (VI, "Visa is a buy; Mastercard is a hold.", REC),
    (VI, "Visa and Mastercard will keep winning as cash disappears.", FWD),
    (TE, "Tesla is overhyped and priced for perfection.", VAL),
    (TE, "Tesla got a decade head start on EVs. It is expensive.", VAL),
    (CO, "Costco is a wonderful business and a great investment.", REC),
    (TS, "TSMC is the safest bet in semiconductors.", REC),
    (NV, "NVIDIA stock is a buy.", REC),
    (NV, "Is NVIDIA still a buy?", REC),
    (TE, "Is Tesla overhyped?", VAL),
    (TE, "Is Tesla worth the hype?", VAL),
    (AM, "Early Amazon shareholders were richly rewarded.", VAL),
    (NV, "NVIDIA shares have been one of the best investments of the decade.", REC),
    (NV, "NVIDIA's stock has a lot of room to run.", FWD | REC),
    (CO, "Costco is a stock to hold for decades.", REC),
    (CO, "Costco deserves its premium.", VAL),
    (AP, "Apple remains a core holding for many long-term investors.", REC),
    (VI, "Visa and Mastercard look like long-term winners.", VAL),
    (NV, "NVIDIA shares will keep climbing.", FWD | VAL),
    (AMD, "Buy AMD and hold it forever.", REC),
    (AMD, "AMD will keep climbing.", FWD),
    (AMD, "Put your savings into AMD.", REC),
    (AMD, "Own AMD for the long run.", REC),
    (AMD, "AMD is the one to own.", REC),
])
def test_a_named_company_verdict_directive_or_forecast_never_reaches_a_post(key, text, codes):
    _assert_rejected_everywhere(key, text, codes)


# ── idx 5: the fact sheet's own valuation lines, echoed or paraphrased ───────


@pytest.mark.parametrize("key, text, codes", [
    (VI, "Visa and Mastercard are quiet compounding machines that can reward patient owners "
         "for decades.", FWD),
    (AP, "Services earn roughly double hardware's margin, quietly re-rating the whole company.",
     VAL),
    (AP, "Apple changed what investors were willing to pay for it.", VAL),
    (AM, "Wall Street often punished Amazon's thin margins, and patient investors earned the "
         "premium.", VAL),
    (AP, "The market re-rated Apple as a services company.", VAL),
    (AP, "Investors rewarded Apple with a higher multiple.", VAL),
    (AP, "Investors began paying more for every dollar Apple earned.", VAL),
    (VI, "Visa has rewarded long-term shareholders.", FWD),
    (NV, "NVIDIA paid a price for Mellanox that looks steep.", VAL),
    (AP, "Apple stock was re-rated to a higher multiple.", VAL),
])
def test_a_valuation_line_of_the_source_is_rejected_when_echoed(key, text, codes):
    _assert_rejected_everywhere(key, text, codes)


@pytest.mark.parametrize("key, fragment", [
    (VI, "reward patient owners"),
    (AP, "re-rating the whole company"),
    (AP, "willing to pay for it"),
    (AM, "Wall Street often punished"),
    (NV, "looks steep"),
    (NV, "a good investment"),
])
def test_the_valuation_lines_are_gone_from_the_fact_sheet_and_the_item_stays(key, fragment):
    item = content_pool.get_item(key)
    assert fragment not in item.fact_text, (key, fragment)
    assert any(fragment in s for s, _code in item.dropped), (key, fragment)
    assert item.eligible, (key, item.ineligible_reason)


# ── idx 6: a fact-sheet number re-attached to a price move or a company's worth ───


@pytest.mark.parametrize("key, text", [
    (AP, "As Services took off, Apple climbed 70%."),
    (CO, "Costco rallied 90% as renewal rates held."),
    (VI, "Visa rose 50% on its operating margins."),
    (HD, "Home Depot jumped 50% on sales to pros."),
    (AM, "Amazon rose 60% on AWS profits."),
    (NV, "NVIDIA was worth $6.9 billion."),
    (MS, "Microsoft was worth roughly $7.5 billion in 2018."),
    (CO, "Costco surged 90% on renewals."),
    (CO, "A Costco share costs $1.50."),
])
def test_a_number_restated_as_a_price_move_or_a_worth_is_refused_by_both_layers(key, text):
    """Compliance rejects the claim (class_b_valuation) and grounding refuses the number
    (number_context) — each on its own."""
    item, _pkg = _baseline(key)
    scan = {v.code for v in c.scan_text("x", c.clean(text), strict_instruments=True,
                                        company_terms=item.company_terms)}
    assert "class_b_valuation" in scan, (key, text, scan)
    ground = {v.code for v in g.check_grounding("x", c.clean(text), item.grounding)}
    assert "number_context" in ground, (key, text, ground)
    _assert_rejected_everywhere(key, text, VAL | {"number_context"})


def test_a_sheets_loss_cannot_come_back_as_a_profit():
    item, _pkg = _baseline(ME)
    assert ("number_context", "$40B") in [
        (v.code, v.detail) for v in g.check_grounding("x", "Reality Labs made a $40B profit.",
                                                        item.grounding)]
    _assert_rejected_everywhere(ME, "Reality Labs made a $40B profit.",
                                frozenset({"number_context"}))


@pytest.mark.parametrize("key, text", [
    (AM, "AWS drives about 60% of Amazon's operating profit."),
    (NV, "NVIDIA paid $6.9 billion for Mellanox."),
    (NV, "NVIDIA paid roughly 6.9 billion dollars for Mellanox."),
    (CO, "Costco's renewal rate stays above 90%."),
    (ME, "Reality Labs lost about $40 billion from 2021 to 2023."),
])
def test_an_honest_paraphrase_of_a_fact_sheet_number_still_passes(key, text):
    item, pkg = _baseline(key)
    res = ws.validate_package(_with(pkg, "x", text), item, RUN_DATE)
    assert "x" in res.posts, (key, text, [(v.code, v.detail) for v in res.outlets.get("x", [])])


# ── idx 7: spelled-out return percentages ────────────────────────────────────


@pytest.mark.parametrize("key, text", [
    (AP, "Apple's Services business returned seventy percent to its owners."),
    (IT, "At three percent a year, your returns roughly double in about twenty-four years."),
    (VI, "Visa's margins north of fifty percent gave its shareholders gains north of fifty "
         "percent."),
    (IT, "A savings account that earned three percent a year only kept pace with prices."),
])
def test_a_spelled_out_return_percentage_is_a_return_figure(key, text):
    _assert_rejected_everywhere(key, text, frozenset({"return_figure"}))


# ── idx 8: digit-free market caps and price records ──────────────────────────


@pytest.mark.parametrize("key, text", [
    (NV, "NVIDIA became a trillion-dollar company."),
    (NV, "NVIDIA became the world's largest company."),
    (NV, "NVIDIA's stock hit an all-time high."),
    (NV, "NVIDIA stock hit record highs."),
    (AP, "Apple is a multi-trillion-dollar giant."),
    (NV, "NVIDIA shares hit an all-time high."),
    (NV, "NVIDIA joined the trillion-dollar club."),
    (NV, "NVIDIA is worth trillions."),
    (AP, "Apple became the first trillion-dollar company."),
    (NV, "NVIDIA became one of the world's largest companies."),
])
def test_a_digit_free_market_cap_or_price_record_is_rejected(key, text):
    _assert_rejected_everywhere(key, text, VAL)


@pytest.mark.parametrize("key, text", [
    (KS, "Apple's market cap is in the trillions."),
    (MR, "Apple's market cap is in the trillions."),
    (MR, "Apple became a trillion-dollar company."),
])
def test_a_journey_post_gets_the_market_cap_rows_when_it_names_a_company(key, text):
    item, _pkg = _baseline(key)
    scan = {v.code for v in c.scan_text("x", c.clean(text), strict_instruments=False)}
    assert "class_b_valuation" in scan, (key, text, scan)
    _assert_rejected_everywhere(key, text, VAL)


@pytest.mark.parametrize("key, text", [
    (NV, "NVIDIA became a trillion-dollar company."),
    (NV, "NVIDIA is worth trillions."),
    (AP, "Apple is a multi-trillion-dollar giant."),
])
def test_a_magnitude_the_sheet_never_states_is_an_ungrounded_number(key, text):
    item, _pkg = _baseline(key)
    assert "ungrounded_number" in {v.code for v in g.check_grounding("x", c.clean(text),
                                                                    item.grounding)}


# ── idx 9: multiples ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("text", [
    "NVIDIA grew twentyfold.",
    "An early stake in NVIDIA grew a hundredfold.",
    "NVIDIA rose 5-fold.",
    "NVIDIA was a 4-bagger for early owners.",
    "NVIDIA grew fivefold.",
    "NVIDIA grew five-fold.",
    "NVIDIA revenue grew twentyfold.",
    "NVIDIA stock delivered 5-fold returns.",
])
def test_a_fold_or_bagger_multiple_is_an_ungrounded_number(text):
    item, _pkg = _baseline(NV)
    assert "ungrounded_number" in {v.code for v in g.check_grounding("x", c.clean(text),
                                                                    item.grounding)}, text
    _assert_rejected_everywhere(NV, text, frozenset({"ungrounded_number"}))


@pytest.mark.parametrize("text", [
    "NVIDIA holders saw triple-digit gains.",
    "An early stake in NVIDIA grew a hundredfold.",
])
def test_a_digit_free_or_multiple_return_is_a_return_figure(text):
    assert "return_figure" in {v.code for v in c.scan_text("x", c.clean(text))}
    _assert_rejected_everywhere(NV, text, frozenset({"return_figure"}))


# ── idx 10: advice and predictions in Journey (and the same shapes in Money Moves) ───


@pytest.mark.parametrize("key, text, codes", [
    (ET, "Buy an S&P 500 ETF and hold it forever.", REC),
    (ET, "Put every paycheck into an S&P 500 ETF.", REC),
    (ET, "The S&P 500 will keep climbing.", FWD),
    (ET, "The S&P 500 always goes up over time.", frozenset({"promissory"})),
    (ET, "Buy an index fund today.", REC),
    (ET, "You should put your savings in an index fund.", REC),
    (ET, "Sell your stocks and buy index funds instead.", REC),
    (ET, "The market will go up this year.", FWD),
    (FO, "Stocks are due for a crash soon.", FWD),
    (FO, "A recession is coming, so sell before it hits.", FWD),
    (FO, "Sell everything before the crash.", REC),
    (FO, "Get out of stocks now.", REC),
    (CI, "Invest early and you will retire rich.", frozenset({"promissory"})),
    (CI, "Stocks will make you rich over time.", FWD),
    (ET, "Buy one S&P 500 ETF and you own a slice of 500 companies.", REC),
])
def test_journey_advice_and_predictions_never_reach_a_post(key, text, codes):
    _assert_rejected_everywhere(key, text, codes)


def test_the_etf_imperative_is_gone_from_its_own_fact_sheet():
    item = content_pool.get_item(ET)
    assert not any("Buy that single ETF" in s for s in item.fact_sentences)
    assert ("Buy that single ETF, and in one click you own a slice of all of them.",
            "class_b_recommendation") in item.dropped
    assert item.eligible


# ── idx 4 / 11 / 12: word-brands in Journey posts, and across Money Moves items ─────


@pytest.mark.parametrize("key, text", [
    (KS, "Oracle looks like a bargain on a P/E basis."),
    (KS, "Chevron pays a steady dividend and trades at a low P/E."),
    (KS, "Meta's P/E ratio can mean a bargain, or trouble."),
    (KS, "Target stock is a bargain."),
    (EM, "Apple has a wide moat, which makes it a great investment."),
    (KS, "Apple trades at a high valuation."),
    (KS, "Buy Apple."),
    (MR, "Apple stock looks cheap right now."),
    (KS, "Apple shares are a bargain at this P/E."),
    (MR, "Ford stock is on sale right now."),
    (KS, "A company like Oracle might trade at a lower P/E than a fast grower."),
    (SB, "Apple shares look cheap next to the business it owns."),
    (MR, "Chevron stock is a bargain today."),
    (MR, "Kodak once looked like a bargain too."),
    (MR, "Chevron shares looked cheap to many investors."),
    (MR, "Oracle stock looks cheap right now."),
    (MR, "Target shares are a bargain."),
    (MR, "Cisco is a bargain."),
    (MR, "Adobe stock is too expensive."),
    (MR, "When the market panics, even Apple can look like a bargain."),
    (MR, "Apple stock looks cheap when the crowd is scared."),
    (EM, "Coca-Cola stock is rarely a bargain."),
    (EM, "Apple has a wide moat, and its stock still looks cheap."),
    (SB, "Apple stock looked cheap long before the business changed."),
    (MR, "In a panic, Coca-Cola shares can trade like a bargain."),
    (SB, "Apple's stock looks expensive."),
    (KS, "Shares of Apple look cheap."),
    (KS, "Take Apple: its market cap is bigger than its profits suggest."),
    (MR, "Target shares trade below their value."),
    (KS, "Target's P/E is low, which can mean a bargain, or trouble."),
    (KS, "A high P/E, like Apple's, means investors expect a lot."),
    (MR, "When Mr. Market panics, Apple can become a real bargain."),
    (SB, "Apple stock can look expensive while the business keeps growing."),
    (MR, "Target looks like a bargain for investors."),
    (MR, "Target, the retailer, looks cheap."),
    (MR, "Why Apple Looks Cheap"),
])
def test_a_journey_post_naming_a_company_gets_the_class_b_rules(key, text):
    """The COMPLIANCE layer on its own, in relaxed (Journey) mode: the sentence names a company,
    so the strict rows and the tier-2 check run on it."""
    scan = {v.code for v in c.scan_text("x", c.clean(text), strict_instruments=False)}
    assert scan & OPINION, (key, text, scan)
    _assert_rejected_everywhere(key, text, OPINION | {"ungrounded_entity"})


@pytest.mark.parametrize("text", [
    # Round 2 put "~Lemonade" into the lexicon (W2CB-3), so the unlisted name here is one the
    # lexicon header says is deliberately ABSENT.
    "Companies like Progressive look cheap.",
    "Investors thought Blue Harbor shares looked cheap.",
])
def test_a_journey_sentence_naming_an_unlisted_proper_noun_gets_the_strict_rows(text):
    """A company the lexicon does not list, written as a name mid-sentence, still switches the
    Journey scan to the Money Moves rows (`_proper_noun_signal`)."""
    assert c.company_mentions(c.skeleton(text)) == []
    scan = {v.code for v in c.scan_text("x", c.clean(text), strict_instruments=False)}
    assert scan & OPINION, (text, scan)


@pytest.mark.parametrize("key, text, name", [
    (EM, "Companies like Apple and Visa have wide moats.", "Apple"),
    (KS, "Chevron shares offer a high dividend yield.", "Chevron"),
    (EM, "Coke has a wide moat and a great price.", "Coke"),
    (EM, "Coca-Cola built a wide moat.", "Coca-Cola"),
    (NV, "NVIDIA overtook Apple as a supplier.", "Apple"),
    (VI, "Apple is cheap.", "Apple"),
    (CO, "Target is expensive.", "Target"),
    (KS, "Apples to apples, Apple wins.", "Apple"),
])
def test_a_company_the_fact_sheet_never_names_is_an_ungrounded_entity(key, text, name):
    """The GROUNDING layer on its own: a company whose name is an English word, or a hyphenated
    name whose parts are words, still has to be in THIS item's fact sheet."""
    item, _pkg = _baseline(key)
    got = [(v.code, v.detail) for v in g.check_grounding("x", c.clean(text), item.grounding)]
    assert ("ungrounded_entity", name) in got, (key, text, got)


@pytest.mark.parametrize("key, text", [
    (NV, "NVIDIA overtook Apple as the world's biggest company."),
    (NV, "Apple is a bargain."),
    (VI, "Apple is cheap."),
    (CO, "Target is expensive."),
    (CO, "Kodak once looked like a bargain too."),
    (NF, "Blockbuster seemed like a steal until it was not."),
    (CO, "Kodak was a steal once."),
    (ME, "Apple was a bargain back then."),
])
def test_a_money_moves_post_cannot_judge_another_company(key, text):
    _assert_rejected_everywhere(key, text, OPINION | {"ungrounded_entity"})


# ── what must still pass ─────────────────────────────────────────────────────


@pytest.mark.parametrize("text", [
    "Revenue grew 10% in 2019.",
    "Index funds spread risk across many companies.",
    "Costco sells in bulk.",
    "Over long periods, broad markets have tended to rise, with deep falls along the way.",
    "When you buy one ETF, your money spreads across many companies.",
    "Don't sell in a panic.",
    "Investors bought high and sold low.",
    "The stock market crashed in 1929.",
    "Nobody knows whether the market will rise or fall next year.",
    "Nobody knows if the market will keep climbing.",
    "Beware anyone who says a crash is coming.",
    "Target a savings rate you can keep.",
    "Chase returns, and fear usually follows.",
    "Mr. Market offers you a price every day.",
    "Costco is worth studying.",
    "A wide moat protects a company's profits.",
    "Compounding turns small sums into large ones over time.",
    "Netflix was the winner of the streaming wars.",
    "Tesla doubled production.",
    "Intel fell behind.",
    "Microsoft bought GitHub.",
    "Discover how a moat protects profits.",
    "Shell out less on fees.",
    "Index funds won't make you rich overnight.",
    "Cheaper batteries widened the margin.",
    "It is expensive to run a warehouse.",
    "The dot-com bubble burst in 2000.",
])
@pytest.mark.parametrize("strict", [True, False])
def test_legitimate_copy_still_passes_the_scan(text, strict):
    """Pool safety: the new rows target shapes, not words. Company-free education, history and
    business facts stay legal in BOTH modes (numbers are grounding's, tested per item above)."""
    got = [(v.code, v.detail) for v in c.scan_text("x", c.clean(text), strict_instruments=strict)]
    assert got == [], (text, strict, got)


@pytest.mark.parametrize("text", [
    "The P/E ratio compares price with profit.",
    "A stock that looks cheap can stay cheap.",
    "ETFs are usually cheap to own.",
    "A stock everyone is shouting about is often already expensive.",
    "Growth stocks can look expensive for years.",
    "Mr. Market sometimes offers a bargain.",
    "On Wall Street, the P/E ratio is the most quoted number.",
])
def test_journey_concept_vocabulary_still_passes_in_relaxed_mode(text):
    """The concept teaching class A exists for: valuation vocabulary with NO company named. The
    relaxed-mode escalation must not fire on "Mr. Market", "Wall Street" or a sentence start."""
    got = [(v.code, v.detail) for v in c.scan_text("x", c.clean(text), strict_instruments=False)]
    assert got == [], (text, got)


@pytest.mark.parametrize("key, text", [
    (TE, "Tesla doubled production."),
    (AMD, "Intel fell behind."),
    (MS, "Microsoft bought GitHub."),
    (LV, "Louis Vuitton began making trunks in 1854."),
    (CO, "Costco sells in bulk."),
    (KS, "Target a savings rate you can keep."),
    (ET, "Index funds spread risk across many companies."),
    # The sheet names these only in a heading or after a dash, never in a "name position" —
    # it still grounds them (`grounding._sheet_company_names`).
    (TS, "Apple chose TSMC for its chips."),
    (NF, "Disney owns Marvel."),
    (CO, "Members say the fee is worth it."),
])
def test_legitimate_copy_still_reaches_a_post(key, text):
    item, pkg = _baseline(key)
    res = ws.validate_package(_with(pkg, "x", text), item, RUN_DATE)
    assert "x" in res.posts, (key, text, [(v.code, v.detail) for v in res.outlets.get("x", [])])


# ── anti-vacuity for the rows that live outside CLASS_B_TIER1 ─────────────────

_COMPANY_ROW_SAMPLES = (
    "Buy NVIDIA.", "NVIDIA looks like a bargain.", "Is NVIDIA still a steal?",
    "NVIDIA will keep winning.", "NVIDIA climbed 70%.", "NVIDIA hit a record high.",
    "NVIDIA is worth trillions.",
    # Round 2 (W2CB-4, W2V-02): placement, best-to-own, continuation, instrument value verdict.
    "Costco belongs in every portfolio.", "TSMC is a wonderful thing to own.",
    "NVIDIA's run is far from over.", "Costco's stock is a great deal.",
)
_FORECAST_SAMPLES = ("The market will keep climbing.", "Stocks are due for a crash.",
                     "A recession is coming.")
_DIRECTIVE_SAMPLES = ("Buy an index fund.", "Sell everything now.",
                      "For most people, an index fund is the right choice.",
                      "The right choice for most beginners is an S&P 500 index fund.",
                      "You can't go wrong with an index fund.",
                      "When his fear makes prices low, you can choose to buy.")


def _view(text: str) -> str:
    sk = c.skeleton(c.clean(text))
    mentions = c.sentence_company_mentions(sk)
    return c.fold(c._company_view(sk, mentions)) if mentions else c.fold(sk)


@pytest.mark.parametrize("rows, samples", [
    ("_COMPANY_ROWS_RE", _COMPANY_ROW_SAMPLES),
    ("_FORECAST_RES", _FORECAST_SAMPLES),
    ("_DIRECTIVE_RES", _DIRECTIVE_SAMPLES),
])
def test_every_sentence_level_row_is_exercised_by_a_sample(rows, samples):
    views = [_view(s) for s in samples]
    compiled = [r[1] if isinstance(r, tuple) else r for r in getattr(c, rows)]
    uncovered = [rx.pattern[:60] for rx in compiled if not any(rx.search(v) for v in views)]
    assert uncovered == [], rows


# ── every code a round can carry has a repair hint ───────────────────────────


def _emitted_codes() -> set:
    src = ""
    for mod in ("compliance", "grounding", "writer_service", "post_copy"):
        src += (content_pool.DATA_DIR.parent / "app" / "services" / "marketing"
                / f"{mod}.py").read_text(encoding="utf-8")
    codes = set(re.findall(r'\bv\("([a-z_]+)"', src))
    codes |= set(re.findall(r'Violation\([^,()]+,\s*"([a-z_]+)"', src))
    codes |= set(re.findall(r'add\("([a-z_]+)"', src))
    codes |= set(re.findall(r'out\.append\(\("([a-z_]+)"', src))
    codes |= set(re.findall(r'\(_[A-Z_]+_RE, "([a-z_]+)"\)', src))    # (_BANNED_RE, "banned_phrase")
    codes |= {f"class_b_{code}" for code, _p, _s in c.CLASS_B_TIER1}
    codes |= {f"class_b_{code}" for code, _p in c._COMPANY_ROWS}
    # The semantic judge's rubric codes (judge.py builds them from RULE_CODES at runtime, so no
    # source regex above can see them) — every one lands in a round's violations in `enforce`.
    from app.services.marketing import judge

    codes |= set(judge.EMITTED_CODES)
    codes.discard("code")
    return codes


def test_every_violation_code_the_scanners_emit_has_a_repair_hint():
    codes = _emitted_codes()
    # Anti-vacuity: the source scan must find the codes it is meant to find.
    assert {"person_named", "ungrounded_number", "number_context", "class_b_valuation",
            "class_b_forward", "return_figure", "promissory", "schema", "not_json",
            "over_platform_limit", "grounding_error", "identity_leak", "brand_mention",
            "misattribution", "judge_directive", "judge_risk_softening",
            "judge_unclassified"} <= codes, sorted(codes)
    missing = sorted(code for code in codes if code not in wp.REPAIR_HINTS)
    assert missing == [], missing
