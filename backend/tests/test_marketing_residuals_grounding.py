"""
Grounding residuals (b) and (c) — FINAL decision 2026-09-26, after two fix rounds and two
adversarial reviews: `grounding.py` and `numbers.py` are back to HEAD for both.

(b) FAIL CLOSED. A source-side unit inheritance for the Costco sheet's bare "twenty-five, fifty,
    or more" anchored its percent twins on word overlap, so invented Costco percentages grounded
    ("Costco caps its markups at 25%" — the cap is 15%). The sheet's 25 and 50 stay unit-less;
    the model can restate the sheet's own "25, 50, or more", and a draft's "25% or 50%" is
    `ungrounded_number` (the repair hint names it).

(c) JUDGE-OWNED. Refusing a present-day or hypothetical worth claim about a deal amount ("Mellanox
    would sell for about $6.9 billion today") was tried twice in the regex: as a marker denylist
    (round 4: rewordings passed, "and now" restatements were refused) and as a past-tense
    requirement (round 5: it refused honest lines built from the items' own vocabulary —
    "switching costs", "Bought, Not Merged", "Why would NVIDIA pay …?" — and a fronted "today"
    still passed). The semantic judge (`judge.py`, rule `judge_company_claim`) flags every one of
    these lines in calibration; they are its MUST-FAIL entries (`scripts/marketing_judge_calibrate.py`),
    and the regex gap is pinned below as a strict xfail so a future regex fix is noticed.

The honest deal restatements the two rounds collected (55) are kept as a regression guard: HEAD
grounds every one of them, and so must any future change.

Category 1 (pure): the Learn bundle only.
"""

from __future__ import annotations

from typing import List, Tuple

import pytest

from app.services.marketing import content_pool
from app.services.marketing import grounding as g
from app.services.marketing import writer_service as ws
from app.services.marketing.compliance import clean
from app.services.marketing.numbers import PERCENT, PLAIN

MM = "money_moves:"
CO, NV, LV, MS = (MM + "costcos-membership-magic", MM + "nvidias-ai-dominance",
                  MM + "the-rise-of-lvmh", MM + "microsofts-cloud-metamorphosis")
IT = "journey:inflation_thief"


def grounding(key: str, text: str) -> List[Tuple[str, str]]:
    item = content_pool.get_item(key)
    assert item is not None and item.eligible, key
    return [(v.code, v.detail) for v in g.check_grounding("f", clean(text), item.grounding)]


def scan(key: str, text: str) -> List[Tuple[str, str]]:
    """The production field scan (`writer_service._scan`: compliance + grounding)."""
    item = content_pool.get_item(key)
    return [(v.code, v.detail) for v in ws._scan("f", clean(text), item, allow_emoji=True)]


def refused_amount(key: str, text: str) -> bool:
    """The draft's deal amount is `number_context` (and nothing else about it)."""
    got = grounding(key, text)
    return bool(got) and all(code == "number_context" for code, _d in got)


# ── (b) the Costco percent list: reverted, fail closed ───────────────────────────────────────


def test_the_costco_sheets_25_and_50_stay_unit_less():
    """The sheet states 15 PERCENT and a bare "25, 50, or more": no 25% or 50% exists to ground
    on (round 4's `percent_list_twins` gave them one)."""
    got = {(f.value, f.unit) for f in content_pool.get_item(CO).grounding.numbers}
    assert {(15.0, PERCENT), (25.0, PLAIN), (50.0, PLAIN)} <= got, got
    assert not {(25.0, PERCENT), (50.0, PERCENT)} & got, got


#: The ten invented Costco percentages the review CONFIRMED grounded after round 4 (HEAD: all
#: `ungrounded_number`). The detail is the draft percentage.
COSTCO_INVENTED = (
    ("Costco's sales grew 25% last year.", "25%"),
    ("Costco caps its markups at 25%.", "25%"),
    ("Costco caps most markups around 50%.", "50%"),
    ("Costco routinely takes 25% on most items.", "25%"),
    ("Costco runs on razor-thin margins of 25%.", "25%"),
    ("Costco keeps prices 50% below other retailers.", "50%"),
    ("Costco sells 50% more than other retailers.", "50%"),
    ("Costco members routinely save 25% or more.", "25%"),
    ("Costco sells goods at a 25% markup.", "25%"),
    ("Costco prices are 25% lower than other retailers.", "25%"),
)


@pytest.mark.parametrize("text, detail", COSTCO_INVENTED)
def test_an_invented_costco_percentage_is_ungrounded(text, detail):
    got = grounding(CO, text)
    assert ("ungrounded_number", detail) in got, (text, got)
    assert ("ungrounded_number", detail) in scan(CO, text), text


@pytest.mark.parametrize("text, expected", [
    # The decision itself: the comparison, restated with units, is not the sheet's.
    ("Other retailers routinely take 25% or 50%.", [("ungrounded_number", "25%"),
                                                    ("ungrounded_number", "50%")]),
    ("Other retailers routinely take 25-50%.", [("ungrounded_number", "25"),
                                               ("ungrounded_number", "50%")]),
])
def test_the_costco_comparison_with_units_is_ungrounded(text, expected):
    assert grounding(CO, text) == expected, text


#: (must-pass, must-reject): the sheet's own figure against an invented one in the same frame.
COSTCO_TWINS = (
    ("Costco caps most markups around 15%.", "Costco caps most markups around 50%."),
    ("Costco caps its markups at around 15 percent.", "Costco caps its markups at 25%."),
    ("Other retailers routinely take 25, 50, or more.",
     "Other retailers routinely take 25% or 50%."),
    ("Costco caps most markups around 15 percent when other retailers routinely take 25, 50, or "
     "more.", "Costco caps most markups around 15 percent when other retailers routinely take "
              "25%, 50%, or more."),
)


@pytest.mark.parametrize("honest, invented", COSTCO_TWINS)
def test_the_sheets_own_costco_figures_still_ground(honest, invented):
    assert grounding(CO, honest) == [], (honest, grounding(CO, honest))
    assert "ungrounded_number" in [c for c, _d in grounding(CO, invented)], invented


# ── (c) present-value claims about a deal amount: judge-owned ─────────────────────────────────


REWORDINGS = (
    (NV, "Mellanox can sell for about $6.9 billion."),
    (NV, "Mellanox sells at about $6.9 billion."),
    (NV, "Mellanox is selling at roughly $6.9 billion."),
    (NV, "Mellanox is for sale at $6.9 billion."),
    (NV, "Mellanox is sold for about $6.9 billion."),
    (NV, "A buyer pays $6.9 billion for Mellanox."),
    (NV, "Mellanox is a $6.9 billion company."),
    (NV, "Mellanox costs about $6.9 billion."),
    (MS, "GitHub costs about $7.5 billion."),
    (LV, "Tiffany costs roughly $15.8 billion."),
)
#: Round 4's own must-refuse lines.
ROUND4_REFUSED = (
    (NV, "Mellanox would sell for about $6.9 billion today."),
    (NV, "Mellanox could sell for $6.9 billion now."),
    (NV, "Mellanox is worth about $6.9 billion today."),
)
#: Further off-deal restatements: a modal, a negation, a present passive or price verb, a flow
#: measure, a present time after the amount, a trade, a present price behind a relative clause.
AND_NOW = (
    (NV, "NVIDIA acquired Mellanox for $6.9 billion and now owns the networking between its "
         "chips."),
    (NV, "NVIDIA bought Mellanox for about $6.9 billion and now sells whole rooms of chips."),
    (NV, "NVIDIA paid roughly $6.9 billion for Mellanox and today sells networking too."),
    (NV, "NVIDIA paid about $6.9 billion for what is now its networking arm, Mellanox."),
    (LV, "LVMH bought Tiffany for $15.8 billion and still owns it today."),
    (LV, "LVMH now owns Tiffany after paying roughly $15.8 billion in 2021."),
    (LV, "LVMH paid roughly $15.8 billion for Tiffany in 2021 and now runs it as its own house."),
    (MS, "Microsoft bought GitHub for about $7.5 billion and now hosts most open-source code."),
)
#: HEAD-honest restatements in every shape the requirement reads.
DEAL_HONEST = (
    # The sheets' own rows, verbatim: a label, a sentence, a dated list.
    (NV, "Paid for Mellanox: ~$6.9B."),
    (MS, "Paid for GitHub: ~$7.5B."),
    (LV, "Paid for Tiffany: ~$15.8B."),
    (NV, "NVIDIA paid roughly 6.9 billion dollars for the networking company Mellanox because a "
         "single chip stopped being the unit of sale — a room full of them, wired together, is."),
    (MS, "GitHub, the centre of open-source development, was acquired in 2018 for roughly "
         "$7.5 billion."),
    (LV, "Dior, Fendi, Celine, Loewe, Bulgari in 2011, Tiffany in 2021 for roughly $15.8 "
         "billion."),
    # A past verb, a past "cost", a deal noun with "was", a definite deal.
    (NV, "NVIDIA acquired Mellanox for approximately 6.9 billion dollars."),
    (NV, "Mellanox sold for about $6.9 billion."),
    (NV, "Mellanox was sold to NVIDIA for about $6.9 billion."),
    (NV, "The Mellanox deal cost NVIDIA about $6.9 billion."),
    (NV, "Mellanox has cost NVIDIA $6.9 billion."),
    (NV, "The Mellanox deal was about $6.9 billion."),
    (NV, "NVIDIA's roughly $6.9 billion purchase of Mellanox brought networking in-house."),
    (MS, "Microsoft's $7.5 billion GitHub deal gave it the home of open-source code."),
    (LV, "LVMH's $15.8 billion bet on Tiffany worked."),
    (MS, "GitHub cost Microsoft roughly $7.5 billion in 2018."),
    (MS, "Microsoft paid roughly $7.5 billion for GitHub back in 2018."),
    (LV, "LVMH agreed to pay $15.8 billion for Tiffany."),
    (LV, "LVMH did pay $15.8 billion for Tiffany."),
    (NV, "NVIDIA ended up paying $6.9 billion for Mellanox."),
    (NV, "NVIDIA picked up Mellanox for about $6.9 billion."),
    (LV, "Tiffany joined in 2021 for roughly $15.8 billion."),
    (LV, "Tiffany became part of LVMH in 2021 for roughly $15.8 billion."),
    (LV, "LVMH invested $15.8 billion in Tiffany."),
    (NV, "About $6.9 billion was paid for Mellanox."),
    # A verbless continuation read with the clause before it (or, fronted, after it).
    (LV, "LVMH added Bulgari, then Tiffany for roughly $15.8 billion."),
    (LV, "LVMH bought Bulgari and then Tiffany for roughly $15.8 billion."),
    (NV, "NVIDIA bought Mellanox, the networking company, for about $6.9 billion."),
    (NV, "NVIDIA bought Mellanox, which makes networking chips, for $6.9 billion."),
    (MS, "Microsoft bought GitHub, the home of open-source code, for about $7.5 billion."),
    (NV, "NVIDIA bought Mellanox ($6.9 billion)."),
    (NV, "NVIDIA paid for Mellanox — $6.9 billion."),
    (NV, "For about $6.9 billion, NVIDIA bought Mellanox."),
    (NV, "Paying about $6.9 billion, NVIDIA bought Mellanox."),
    (LV, "Tiffany in 2021 for roughly $15.8 billion."),
    # A present-day or modal clause that is ANOTHER clause.
    (NV, "NVIDIA paid $6.9 billion for Mellanox so it could sell whole rooms of chips."),
    (NV, "NVIDIA paid $6.9 billion for Mellanox because a single chip was no longer the unit of "
         "sale."),
    (NV, "NVIDIA, which now owns Mellanox, paid $6.9 billion for it."),
    (NV, "NVIDIA, now a networking giant too, paid $6.9 billion for Mellanox."),
    (NV, "NVIDIA paid $6.9 billion for Mellanox (now part of NVIDIA)."),
    (NV, "NVIDIA paid $6.9 billion for Mellanox, and today networking is a key business."),
    (LV, "LVMH, which today owns 75 brands, paid $15.8 billion for Tiffany."),
    (NV, "The company that sells GPUs paid $6.9 billion for Mellanox."),
    (NV, "NVIDIA made its biggest bet when it paid $6.9 billion for Mellanox."),
    (NV, "NVIDIA paid $6.9 billion in cash for Mellanox."),
    (NV, "NVIDIA paid $6.9 billion for Mellanox in May."),
    # Not a deal: the sheet's own subject, so "might" is the lesson, not a price.
    (IT, "A coffee that cost $3 last year might cost $3.20 this year."),
)


PRESENT_VALUE = REWORDINGS + (
    (NV, "Mellanox would sell for about $6.9 billion today."),
    (NV, "Mellanox could sell for $6.9 billion now."),
)


@pytest.mark.parametrize("key, text", AND_NOW + DEAL_HONEST)
def test_an_honest_deal_restatement_still_grounds(key, text):
    assert grounding(key, text) == [], (key, text, grounding(key, text))


@pytest.mark.xfail(strict=True, reason="residual (c): the regex grounds a present-value claim on "
                                       "a deal amount; the semantic judge owns it (2026-09-26)")
@pytest.mark.parametrize("key, text", PRESENT_VALUE)
def test_the_regex_does_not_refuse_a_present_value_claim(key, text):
    assert grounding(key, text) != [], text


def test_every_present_value_claim_is_a_judge_must_fail_calibration_line():
    """The judge is the gate for (c): each line must be in the calibration's must-fail sets, so
    a rubric or model change that stops flagging it fails the calibration."""
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    import marketing_judge_calibrate as cal

    must_fail = {t for _i, _k, t, _r in cal.MUST_FAIL_HANDWRITTEN + cal.MUST_FAIL_HOLDOUT
                 + cal.MUST_FAIL_HOLDOUT_2}
    missing = [t for _k, t in PRESENT_VALUE if t not in must_fail]
    assert missing == [], missing
