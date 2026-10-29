"""Templates over other companies' filings — the S2 half of the Trillion Club stake finder.

Pure and deterministic: no I/O, no LLM, no FMP, no clock but the CPU budget. EDGAR full-text
search (``stake_finder``) returns a document of ANOTHER company that names a member's alias
near deal wording; the finder fetches it, runs ``html_to_text(..., max_chars=None)`` and asks
``classify_document`` what, if anything, the document shows the member bought:

* **T1 — closing** (tier ``sec_closing``). An 8-K with Item 3.02 reports a COMPLETED sale of
  the filer's own common / ordinary / Class A stock to the member alone: "completed the
  issuance and sale of 214,776,632 shares … to NVIDIA Corporation", "NVIDIA purchased …", or a
  sale clause followed by "The Shares were issued and sold to NVIDIA" (a link of the SAME
  securities: never "shares of Series A Preferred Stock were issued and sold to NVIDIA" for a
  common sale; a link no sale used is a heads-up, never dropped). The amount is the single
  "aggregate purchase price … of $X" BOUND to the member's clause: the deal window takes in the
  next sentence only when it reports no sale, issue or purchase to anyone — the member
  restating THIS sale aside ("The Shares were sold to NVIDIA at a price of $50.00 per share,
  for …", "NVIDIA paid an aggregate purchase price of …"), when that sentence names no other
  securities — and a second share count, another purchaser past the member ("… and to Beta
  Capital LLC") or another buyer's clause before the price is the heads-up "price not bound to
  the member". When a per-share price is stated too, shares × price must be within 1% of it.
  The closing date is the sentence's own CLAUSE-INITIAL "On <date>" — never a date inside a
  phrase ("the Agreement entered into on …", "the waiting period on …", "the Form 8-K filed
  on …") — unless its clause is a SIGNING ("On …, Acme entered into …"), which counts only on
  the filing date, or, when the sale is coordinated with it (", and Acme completed …"), at most
  ``SIGNING_CLOSING_WINDOW_DAYS`` before the filing date and never "as previously disclosed";
  else a closing clause after the verb or in the next sentence ("The closing occurred on …",
  "(the “Closing Date”)"), else a closing (or a same-window signing) sentence before; no date is
  a heads-up. "has not (yet) completed" is no closing. Phase 1: a PROPOSAL (the finder labels
  it "trial — would auto-publish"; the 8-K closing auto tier is a reviewed follow-up).
* **Instrument** (tier ``instrument``). The same completed sale — or a warrant issued — when
  the sold noun is preferred stock, a warrant, a convertible, a SAFE or units: a proposal whose
  name carries the instrument from the closed ``INSTRUMENT_LABELS`` vocabulary, the seed's own
  style ("Marvell Technology (convertible preferred)"). Like T1 it needs an 8-K with Item 3.02
  (or a 6-K): a pre-IPO round restated in an S-1 is a heads-up. A warrant EXHIBIT (EX-4.x)
  counts only under such a filing, when the member is a party in its preamble, and when it is
  an EXECUTED instrument — no "Form of" title, no blank date ("[●], 2026"), no warrant that
  "shall be issued".
* **T2 — signed, not closed** (tier ``sec_agreement``). "<member> agreed to purchase …" (an 8-K
  agreement, or a concurrent private placement in a prospectus) → a commitment proposal. A
  figure only when the member's OWN object says "up to" / "not to exceed" / "a maximum of"
  (``committed_up_to``) — never another party's cap later in the sentence, nor the member's own
  optional one: the object ends at another party's clause, a second purchase verb ("… and Beta
  Capital LLC will purchase", "… intend to / are expected to / may purchase"), another named
  party or a new clause after " and ", and an "up to" past it is a heads-up ("not bound to the
  member"), never a guess; one that disagrees with a stated shares × per-share price by more
  than 1% is refused too. A FIXED amount is named in ``detail`` only — a commitment row
  carries no figure (precedent: the seed's Nscale row).
* **T3 — indication of interest** (tier ``ioi``). The member, or a defined group containing it
  ("NVIDIA Corporation, Advanced Micro Devices, Inc., and Broadcom Inc. (collectively, the
  “Cornerstone Investors”)"), HEADS the subject of "indicated an interest" → a HEADS-UP, never
  a row. Someone else's interest in the same document (Iambic: ARK and Duquesne), a negated
  subject ("Neither NVIDIA nor …", "No existing stockholder, including NVIDIA, …"), the member
  inside another investor's appositive ("Beta Capital LLC, a partner of NVIDIA Corporation, …")
  or as the object of "of" is not one. A subject that lists several names with no defined term
  carries their COMBINED figure and the club aliases it lists (``group_members``).
* **T4 — 6-K directed issuance** (tier ``directed_issuance``) → a proposal when its clause
  reports the issuance COMPLETED — a completion that governs the issuance itself ("Nokia has
  completed the directed share issuance to …", "… was completed") is not undone by a later step
  in the same sentence (", which are expected to be admitted to trading …"), while one in a
  temporal lead ("Once / Upon / Following … completed …") leaves the main clause to decide. An
  announced, proposed or conditional one ("will carry out …, subject to …", "the Board proposes
  …") is a commitment proposal (tier ``sec_agreement``) when the member's own undertaking binds
  it ("NVIDIA has irrevocably undertaken to subscribe …"), else a heads-up. A 6-K that reports a
  completed sale is T4 too: a foreign private issuer's 6-K has no 8-K items.

``TemplateResult.closed`` says whether the evidence reports a COMPLETED deal (the finder reads
the flag, never ``detail``).

Everything else that names the member is SKIPPED with a reason — ``commercial_only`` (a
services / supply / licence / partnership counterparty, or an agent such as an underwriter),
``co_investor_only`` (another investor in the filer), ``exhibit_skipped`` (an indenture or a
warrant the member is not a party to), ``no_template`` — or, for the member's own document, a
``member_filed`` heads-up. When a rule saw a real deal with the member but refused one of its
facts (several purchasers, an ambiguous or non-USD amount, no closing date, a name it cannot
confirm, evidence that does not verify) the result is a HEADS-UP saying why: nothing is
dropped silently, and nothing doubtful becomes a pre-filled row.

Rules that shape the code (each is a way a wrong stake could reach the screen):

* **Deny terms bind to the member's mention, never to a bare word in the window.** "NVIDIA …
  as underwriter", "NVIDIA, a customer of …", "from NVIDIA", "NVIDIA's products" refuse THAT
  mention as a buyer; the Coherent 8-K's Item 7.01 "collaboration between Coherent and
  NVIDIA" two sentences after the closing does not touch the closing.
* **One purchaser, one deal.** "to NVIDIA Corporation and certain other investors", "together
  with", "each of", a list of names, a defined group that lists the member ("the Purchasers"),
  "an affiliate of NVIDIA" or a longer name that only starts with the alias ("the NVIDIA
  Corporation Employee Benefit Plan") → never a closing row (a heads-up). Two different deals
  in one document, or a readable closing beside one that is not, are a heads-up too.
* **The filer's own stock.** The sold noun must be the filer's: an EXACT filer term bound to
  the class and ending the owner phrase ("the Company’s common stock", "Synopsys’ common
  stock", "common stock of the Company", "Nokia shares"), or "our" / "its" directly before the
  class ("our Class A common stock") — never "common stock of Acme Robotics, Inc." for a filer
  called "Acme", and never any "of <Name>" that is not the filer. A subsidiary, a business, a
  division, assets, "all of the outstanding shares", a joint venture, an affiliate, a portfolio
  company or an investee is a divestiture (skipped).
* **The investee's name comes from the document's defining clause** — ``Intel Corporation (the
  “Company”)``, ``Coherent Corp. (“Coherent” or the “Company”)``, ``Synopsys, Inc.
  (“Synopsys”)``, ``Marvell Technology, Inc., a Delaware corporation (the “Corporation”)``,
  ``“Meta,” “we,” “us” and “our” refer to Meta Platforms, Inc.`` — and must equal SEC's
  conformed name once the EDGAR state tag and legal suffixes are stripped and both are folded
  (``INTEL CORP`` == ``Intel Corporation``); otherwise ``s2_name_mismatch`` and a heads-up. The
  display name is ``stake_names.clean_investee_name`` of the document's spelling, never SEC's
  upper case.
* **Numbers are read on the FULL text at absolute positions, never inside a window**: a
  sentence force-split at ``SENTENCE_MAX_CHARS`` can end inside "$2,000,000,000", and a
  window-relative parse would read "$2,000". Every evidence span is checked with
  ``verify_peg_span`` against the FULL normalised text (40-400 characters, no number cut); a
  span that fails turns the result into a heads-up. A scale abbreviation is mapped explicitly
  ("$2bn", "$1.5 bln" → billions; "$500mn", "$250MM", "$5 M" → millions) or the figure is
  unreadable ("$2b", "$50m", "$2 millions") — and an unreadable price is a heads-up, never a
  proposal that silently has no amount.
* **CPU safety.** ``re`` holds the GIL, so a worker thread does not protect the event loop.
  Windows are picked by literal searches for the alias or its defined terms; sentences are
  force-split at 1,500 characters; every quantifier is bounded and each pattern starts on a
  literal or on a character its bounded gaps exclude — the test file
  ``tests/test_trillion_club_stake_templates.py`` sweeps every compiled pattern of this module
  over ~1M-character table-like inputs; figures are ASCII digits only (``\\d`` would also read
  "５"); a document defining more than ``MAX_ALIAS_TERMS`` names or ``MAX_GROUP_TERMS`` groups
  for the member is a heads-up (each costs a whole-text scan), and so is a document of more
  than ``MAX_DEAL_CANDIDATES`` deal clauses ("too many deal clauses"); choosing among the deals
  stops at a third distinct one (the "different deals" heads-up already); and a per-document
  ``time.process_time`` budget, checked inside every long loop (the sentences, the links, the
  near misses and the choice among deals), turns an overrun into a heads-up ("cpu budget").
"""

from __future__ import annotations

import bisect
import functools
import logging
import math
import re
import time
from dataclasses import dataclass, replace
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, FrozenSet, List, Optional, Sequence, Set, Tuple

from app.services.learn_weekly.sec_facts import (
    PEG_SPAN_MAX_CHARS,
    PEG_SPAN_MIN_CHARS,
    verify_peg_span,
)
from app.services.trillion_club.stake_contracts import (
    FINDING_TIERS,
    OUTCOME_HEADS_UP,
    OUTCOME_PROPOSAL,
    OUTCOME_SKIPPED,
    REASONS,
    TIER_DIRECTED,
    TIER_INSTRUMENT,
    TIER_IOI,
    TIER_OTHER,
    TIER_SEC_AGREEMENT,
    TIER_SEC_CLOSING,
)
from app.services.trillion_club.stake_names import (
    INVESTEE_NAME_MAX,
    clean_investee_name,
    fold_entity_name,
    strip_state_tag,
)
from app.utils.filing_text import normalize_peg_text

logger = logging.getLogger(__name__)

# ── Public vocabulary ────────────────────────────────────────────────────────────────────

#: A template never decides ``auto``: the finder applies the gates (plan v3, Gates 1-10).
TEMPLATE_OUTCOMES: Tuple[str, ...] = (OUTCOME_PROPOSAL, OUTCOME_HEADS_UP, OUTCOME_SKIPPED)

#: The ``value_basis`` values a template may propose (a subset of
#: ``schemas.trillion_club.VALUE_BASES``, pinned by a test).
VALUE_BASIS_INVESTED = "invested"
VALUE_BASIS_COMMITTED_UP_TO = "committed_up_to"
TEMPLATE_VALUE_BASES: Tuple[str, ...] = (VALUE_BASIS_INVESTED, VALUE_BASIS_COMMITTED_UP_TO)

#: The closed instrument vocabulary (plan v3, "Instrument naming"). The finder appends the
#: label to the company name; an instrument it cannot name is a heads-up.
INSTRUMENT_CONVERTIBLE_PREFERRED = "(convertible preferred)"
INSTRUMENT_PREFERRED = "(preferred)"
INSTRUMENT_WARRANT = "(warrant)"
INSTRUMENT_PRE_FUNDED_WARRANT = "(pre-funded warrant)"
INSTRUMENT_CONVERTIBLE_NOTE = "(convertible note)"
INSTRUMENT_SAFE = "(SAFE)"
INSTRUMENT_UNITS = "(units)"
INSTRUMENT_LABELS: Tuple[str, ...] = (
    INSTRUMENT_CONVERTIBLE_PREFERRED, INSTRUMENT_PREFERRED, INSTRUMENT_WARRANT,
    INSTRUMENT_PRE_FUNDED_WARRANT, INSTRUMENT_CONVERTIBLE_NOTE, INSTRUMENT_SAFE,
    INSTRUMENT_UNITS,
)

#: A sentence with no boundary is force-split at this many characters.
SENTENCE_MAX_CHARS = 1_500
#: A template's facts come from at most this many ADJACENT sentences.
CHAIN_MAX_SENTENCES = 3
#: Evidence spans kept per result (the ledger's ``verified_spans`` is capped too).
MAX_RESULT_SPANS = 5
DEFAULT_CPU_BUDGET_SECONDS = 2.0
MAX_CPU_BUDGET_SECONDS = 60.0
#: Sentences near deal wording examined per document. More is a heads-up, never a guess.
MAX_DEAL_SENTENCES = 1_000
#: Deal clauses (candidates) read per document. More is a heads-up ("too many deal clauses"),
#: never minutes of CPU: each one is a closing / date / price read, and choosing among them
#: compares them pairwise.
MAX_DEAL_CANDIDATES = 200
#: A signing date that is also the closing date of a sale coordinated in the same sentence ("On
#: D, Acme entered into …, and Acme completed …") — or of a sale in the sentence after the
#: signing (Synopsys) — only when D is at most this many days before the filing date: an 8-K is
#: due within four business days of its event.
SIGNING_CLOSING_WINDOW_DAYS = 7
#: Mentions of the member collected per document (a 12 MiB prospectus stays bounded).
MAX_MENTIONS = 20_000
#: Distinct defined terms for the member ("NVIDIA", "Initial Warrantholder") and groups that
#: list it ("Cornerstone Investors") per document; each costs a whole-text scan. More is a
#: heads-up ("too many defined terms"), never minutes of CPU.
MAX_ALIAS_TERMS = 16
MAX_GROUP_TERMS = 8
#: An EX-4.x exhibit's preamble: where the member must appear as a party.
EX4_PREAMBLE_CHARS = 6_000
#: shares × per-share price must be within this fraction of the stated aggregate price.
SHARES_PRICE_TOLERANCE = Decimal("0.01")
MAX_SHARES = 10 ** 12
MAX_AMOUNT_USD = Decimal(10) ** 13
MAX_PRICE_PER_SHARE = Decimal(10) ** 7
DETAIL_MAX_CHARS = 300

# Ranks: the evidence class first (closed > signed > interest), then a valid match before a
# near miss of the same class. A deny (a divestiture, an underwriter) ranks last.
_RANK_CLOSED = 50
_RANK_CLOSED_NEAR = 45
_RANK_SIGNED = 40
_RANK_SIGNED_NEAR = 35
_RANK_IOI = 30
_RANK_DENY = 10


# ── The result ───────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class TemplateResult:
    """What one document shows the member bought, as far as the templates can tell.

    ``tier`` is the evidence class (``stake_contracts.FINDING_TIERS``), ``reason`` a
    ``stake_contracts.REASONS`` code and ``outcome`` one of ``TEMPLATE_OUTCOMES``. A PROPOSAL
    always carries ``company_name`` (the document's own spelling, cleaned) and its evidence
    ``spans`` (each verified against the full text); ``amount_usd`` comes with its
    ``value_basis`` whenever it is set on a proposal. On a heads-up, ``amount_usd`` is
    informational (a T3 group's combined "up to") and ``value_basis`` is None. A T2 FIXED
    amount is never returned as a figure (``detail`` names it). ``event_date`` is the closing /
    agreement / issue date the text binds to the deal, or None (the finder then uses the filing
    date). ``group_term`` / ``group_members`` name a defined group: the term ("Cornerstone
    Investors") and the club aliases listed in it, in text order — or, for a T3 subject that
    lists several names with no defined term, ``group_term`` None and the club aliases it lists,
    the member's always among them. Non-empty ``group_members`` means the amount is the group's
    COMBINED figure, never the member's own (``detail`` says "combined" too).

    ``closed`` is True only when the evidence reports a COMPLETED deal — a closing, an issued
    instrument, a registered directed issuance — never on an agreement (``sec_agreement``), an
    indication (``ioi``) or a skip. Every proposal on ``sec_closing`` / ``instrument`` /
    ``directed_issuance`` carries it; a heads-up may carry it when the completion was read but
    another fact was refused. The finder reads this flag, never ``detail``.
    """

    tier: str
    reason: str
    outcome: str
    company_name: Optional[str]
    instrument_label: Optional[str]
    amount_usd: Optional[Decimal]
    value_basis: Optional[str]
    shares: Optional[int]
    price_per_share: Optional[Decimal]
    event_date: Optional[date]
    spans: Tuple[str, ...]
    group_term: Optional[str]
    group_members: Tuple[str, ...]
    detail: str
    closed: bool = False

    def __post_init__(self) -> None:
        problems = _result_problems(self)
        if problems:
            raise ValueError(f"invalid TemplateResult: {'; '.join(problems)}")


def _positive_decimal(value: Any, cap: Decimal) -> bool:
    return isinstance(value, Decimal) and value.is_finite() and Decimal(0) < value <= cap


def _result_problems(r: TemplateResult) -> List[str]:
    out: List[str] = []
    if r.tier not in FINDING_TIERS:
        out.append(f"tier {r.tier!r}")
    if r.reason not in REASONS:
        out.append(f"reason {r.reason!r}")
    if r.outcome not in TEMPLATE_OUTCOMES:
        out.append(f"outcome {r.outcome!r}")
    if r.company_name is not None and (
        not isinstance(r.company_name, str) or not r.company_name.strip()
        or len(r.company_name) > INVESTEE_NAME_MAX
    ):
        out.append(f"company_name {r.company_name!r}")
    if r.instrument_label is not None and r.instrument_label not in INSTRUMENT_LABELS:
        out.append(f"instrument_label {r.instrument_label!r}")
    if r.amount_usd is not None and not _positive_decimal(r.amount_usd, MAX_AMOUNT_USD):
        out.append(f"amount_usd {r.amount_usd!r}")
    if r.value_basis is not None:
        if r.value_basis not in TEMPLATE_VALUE_BASES:
            out.append(f"value_basis {r.value_basis!r}")
        if r.amount_usd is None or r.outcome != OUTCOME_PROPOSAL:
            out.append("a value_basis needs an amount on a proposal")
    if r.outcome == OUTCOME_PROPOSAL and r.amount_usd is not None and r.value_basis is None:
        out.append("a proposal's amount needs a value_basis")
    if r.shares is not None and (
        isinstance(r.shares, bool) or not isinstance(r.shares, int) or not 0 < r.shares <= MAX_SHARES
    ):
        out.append(f"shares {r.shares!r}")
    if r.price_per_share is not None and not _positive_decimal(r.price_per_share, MAX_PRICE_PER_SHARE):
        out.append(f"price_per_share {r.price_per_share!r}")
    if r.event_date is not None and (isinstance(r.event_date, datetime) or not isinstance(r.event_date, date)):
        out.append(f"event_date {r.event_date!r}")
    if not isinstance(r.spans, tuple) or not all(isinstance(s, str) and s.strip() for s in r.spans):
        out.append("spans must be a tuple of non-empty strings")
    elif len(r.spans) > MAX_RESULT_SPANS:
        out.append(f"{len(r.spans)} spans (at most {MAX_RESULT_SPANS})")
    if r.outcome == OUTCOME_PROPOSAL and (not r.spans or not r.company_name):
        out.append("a proposal needs verified spans and a company name")
    if r.group_term is not None and (not isinstance(r.group_term, str) or not r.group_term.strip()):
        out.append(f"group_term {r.group_term!r}")
    if not isinstance(r.group_members, tuple) or not all(isinstance(m, str) and m for m in r.group_members):
        out.append("group_members must be a tuple of non-empty strings")
    if not isinstance(r.detail, str) or len(r.detail) > DETAIL_MAX_CHARS:
        out.append(f"detail must be a string of at most {DETAIL_MAX_CHARS} characters")
    if not isinstance(r.closed, bool):
        out.append(f"closed {r.closed!r} (a bool)")
    elif r.closed and (r.tier in (TIER_SEC_AGREEMENT, TIER_IOI) or r.outcome == OUTCOME_SKIPPED):
        out.append("closed is never set on an agreement, an indication or a skip")
    return out


# ── CPU budget ───────────────────────────────────────────────────────────────────────────

#: The clock the budget reads (plan v3: per-document ``time.process_time``). A module
#: attribute so a test can drive it.
_cpu_clock = time.process_time


class _OverBudget(Exception):
    """The per-document CPU budget ran out (caught in ``classify_document``)."""


class _TooManyDeals(Exception):
    """More than ``MAX_DEAL_CANDIDATES`` deal clauses name the member (caught in ``run``)."""


class _Budget:
    __slots__ = ("_deadline",)

    def __init__(self, seconds: float) -> None:
        self._deadline = _cpu_clock() + seconds

    def check(self) -> None:
        if _cpu_clock() > self._deadline:
            raise _OverBudget()


# ── Patterns ─────────────────────────────────────────────────────────────────────────────
# Every quantifier is bounded, no group repeats an ambiguous body, and each pattern starts on a
# literal or a fixed character class — so a whole-text ``finditer`` stays linear (swept by the
# test). Patterns that read a figure run at an absolute offset of the FULL text.

_ITEM_RE = re.compile(r"[0-9]{1,2}\.[0-9]{2}")
# Sentence terminator: ".", "!" or "?" (plus closing brackets / quotes) before whitespace.
_TERMINATOR_RE = re.compile(r"[.!?][)\]”’\"']{0,2}(?=\s)")
_DOTTED_ABBREVIATION_RE = re.compile(r"(?:[a-z]\.){1,4}[a-z]")

# A party's definition: ", a Delaware limited liability company (…)" right after its name.
_ALIAS_APPOSITIVE_RE = re.compile(r",\s{1,3}an?\s{1,3}[^,()“”\"]{1,80}?(?=,?\s{1,3}\()")
_PAREN_BODY_RE = re.compile(r"\(([^()]{1,300})\)")
_QUOTED_TERM_RE = re.compile(r"[“\"]([A-Z0-9][^“”\"]{0,59})[”\"]")
# "(NVIDIA)" with no quotes — the prospectus style ("NVIDIA Corporation (NVIDIA)").
_UNQUOTED_TERM_RE = re.compile(r"[A-Z][A-Za-z0-9&.\- ]{1,39}")
# Inside a definition: what follows is a GROUP's term, unless it only adds the party's own
# affiliates ("together with its affiliates, “NVIDIA”").
_GROUP_WORDS_RE = re.compile(r"\b(?:together\s{1,3}with|collectively|along\s{1,3}with)\b", re.IGNORECASE)
_OWN_GROUP_RE = re.compile(
    r"\b(?:together|along)\s{1,3}with\s{1,3}(?:any\s{1,3}|each\s{1,3}of\s{1,3})?(?:its|their|any|such)\b",
    re.IGNORECASE,
)
# A group after a list of names: "… Broadcom Inc. (collectively, the “Cornerstone Investors”)".
_GROUP_PAREN_RE = re.compile(r"\((?:collectively|together|each)\b[^()]{0,200}\)", re.IGNORECASE)
_LIST_DOT_RE = re.compile(r"[.;:](?!\s?(?:\(|,|\s[a-z]))")
_PAREN_STRIP_RE = re.compile(r"\([^()]{0,120}\)")

# The filer's defining clause.
_COMPANY_GENERIC_DEF_RE = re.compile(
    r"\((?:the\s{1,3})?[“\"](?P<term>Company|Corporation|Issuer|Registrant)[”\"]\)"
)
_COMPANY_OR_DEF_RE = re.compile(
    r"\([“\"](?P<short>[A-Z0-9][^“”\"()]{0,39})[”\"],?\s{1,3}(?:or|and)\s{1,3}(?:the\s{1,3})?"
    r"[“\"](?P<term>Company|Corporation|Issuer)[”\"]\)"
)
_SHORT_DEF_RE = re.compile(r"\([“\"](?P<short>[A-Z0-9][^“”\"()]{0,39})[”\"]\)")
_REFER_TO_RE = re.compile(r"[”\"]\s{1,3}(?:refer|refers)\s{1,3}to\s{1,3}")
_WE_TERM_RE = re.compile(r"[“\"](?:we|us|our)[,]?[”\"]")
# ", a Delaware corporation" between a name and its definition.
_APPOSITIVE_TAIL_RE = re.compile(r",\s{1,3}an?\s{1,3}[A-Za-z][A-Za-z .'\-]{1,80}$")
_NAME_TOKEN_RE = re.compile(r"[A-Z0-9][\w.&'’\-]{0,40}|[a-z]{1,2}[A-Z][\w.&'’\-]{0,40}")

# Sale clauses (T1 / instrument / T4).
_SALE_VERB_RE = re.compile(
    r"\b(?:completed|closed|consummated)\s{1,3}(?:the\s{1,3}|its\s{1,3})?"
    r"(?:previously\s{1,3}announced\s{1,3})?"
    r"(?:private\s{1,3}placement|issuance\s{1,3}and\s{1,3}sale|sale\s{1,3}and\s{1,3}issuance|"
    r"sale|issuance)\s{1,3}(?:of\s{1,3})?",
    re.IGNORECASE,
)
_PURCHASED_RE = re.compile(
    r"\bpurchased\s{1,3}(?=(?:an\s{1,3}aggregate\s{1,3}of\s{1,3})?(?:[0-9$]|US\$|U\.S\.\s?\$))"
)
_SOLD_TO_RE = re.compile(
    r"\b(?:were|was|have\s{1,3}been|has\s{1,3}been)\s{1,3}"
    r"(?:issued\s{1,3}and\s{1,3}sold|sold\s{1,3}and\s{1,3}issued|sold|issued)\s{1,3}to\s{1,3}",
    re.IGNORECASE,
)
_SOLD_SUBJECT_RE = re.compile(r"\b(?:shares?|stock|warrants?|notes?|units?)\b", re.IGNORECASE)
_ISSUED_TO_RE = re.compile(r"\b(?:issued|granted)\s{1,3}to\s{1,3}")
# A sale or issue in the active voice ("the Company sold 9,000,000 shares … to Beta"): the
# " to <purchaser>" is found with a bounded literal search after the verb, never a regex gap.
_SALE_ACTIVE_RE = re.compile(r"\b(?:sold|sells|sell|issued|issues|issue|allotted|allots|allot)\b", re.IGNORECASE)
# What a BUYER does ("Beta Capital LLC paid …", "NVIDIA acquired …"): another purchaser's clause.
_BUYER_VERB_RE = re.compile(r"\b(?:purchased|paid|acquired|subscribed|bought|invested)\b", re.IGNORECASE)
# A second purchaser named past the member's own: "… and to Beta Capital LLC", "… 9,000,000
# shares to Beta Capital LLC", ", to Beta Capital LLC".
_OTHER_TO_RE = re.compile(
    r"(?:\b(?:and|or|shares?|Shares?|stock|Stock|warrants?|Warrants?|units?|Units?)|,)\s{1,3}to\s{1,3}"
    r"(?:the\s{1,3})?(?=[A-Z])"
)
# A count that is not a sale's: the member's conversion / exercise shares ("convertible … into a
# maximum of 21,778,000 shares"), shares reserved or authorized.
_COUNT_CONTEXT_RE = re.compile(
    r"\b(?:convert(?:ible|ed|s)?|conversion|exchangeable|exercis(?:e|es|ed|able)|issuable|underlying|"
    r"reserved|authori[sz]ed|owns?|owned|holds?|held|holding|beneficially)\b",
    re.IGNORECASE,
)
# "<member>, which paid …": the buyer's clause is the member's own.
_WHICH_BEFORE_RE = re.compile(r",\s{1,3}(?:which|who)\s{1,3}(?:also\s{1,3}|has\s{1,3}|have\s{1,3})?$")
_ISSUED_WARRANT_RE = re.compile(
    r"\b(?:issued|granted)\s{1,3}(?:an?\s{1,3})?(?:pre-funded\s{1,3}|prefunded\s{1,3})?"
    r"warrants?\b"
)
_WARRANT_AFTER_RE = re.compile(
    r"(?:an?\s{1,3})?(?P<pre>pre-?\s?funded\s{1,3})?warrants?\b", re.IGNORECASE
)
# A count never starts inside a number; the space-grouped form ("166 389 351") is tried only
# where no digit group precedes it, so a run of groups is not re-read from every group.
_NUM = (
    r"(?<![0-9,.])(?:[0-9]{1,3}(?:,[0-9]{3}){1,5}"
    r"|(?<![0-9][ \u00a0\u202f])[0-9]{1,3}(?:[ \u00a0\u202f][0-9]{3}){1,5}|[0-9]{1,12})"
)
_SHARE_COUNT_RE = re.compile(
    r"(?:an\s{1,3}aggregate\s{1,3}of\s{1,3}|a\s{1,3}total\s{1,3}of\s{1,3})?"
    rf"(?P<n>{_NUM})\s{{1,3}}(?P<new>new\s{{1,3}})?(?:[A-Z][\w.&’'\-]{{0,30}}\s{{1,3}})?"
    r"(?:shares|Shares|share|Share)\b"
)
_OF_RE = re.compile(r"\s{1,3}of\s{1,3}")
_PERCENT_OF_TOTAL_RE = re.compile(
    r"\b(?:approximately\s{1,3})?(?P<pct>[0-9]{1,2}(?:\.[0-9]{1,2})?)\s?%\s{1,3}of\s{1,3}the\s{1,3}total\s{1,3}"
    r"(?:number\s{1,3}of\s{1,3})?",
    re.IGNORECASE,
)

# Money. Read at an absolute offset of the FULL text (never inside a window).
_MONEY_AT_RE = re.compile(
    r"(?:approximately\s{1,3}|about\s{1,3})?(?:US\$|U\.S\.\s?\$|USD\s{1,2}|\$)\s{0,2}"
    r"(?P<num>[0-9]{1,3}(?:,[0-9]{3}){1,6}(?:\.[0-9]{1,6})?|[0-9]{1,16}(?:\.[0-9]{1,6})?)"
    r"(?:\s{1,3}(?P<scale>thousand|million|billion|trillion)\b)?",
    re.IGNORECASE,
)
_FOREIGN_AT_RE = re.compile(
    r"(?:approximately\s{1,3}|about\s{1,3})?"
    r"(?:€|£|¥|₩|₹|(?:CHF|EUR|GBP|JPY|KRW|INR|RMB|CNY|SEK|NOK|DKK|CAD|AUD|HKD)\b|[A-Z]{1,3}\$)"
)
_BARE_NUMBER_AT_RE = re.compile(r"(?:approximately\s{1,3}|about\s{1,3})?[0-9]")
# A U.S.-dollar marker followed by a digit: a figure ``_parse_money`` should have read.
_USD_MARK_RE = re.compile(
    r"(?:approximately\s{1,3}|about\s{1,3})?(?:US\$|U\.S\.\s?\$|USD\s{1,2}|\$)\s{0,2}[0-9]", re.IGNORECASE
)
_PER_SHARE_AFTER_RE = re.compile(r"\s{0,3}(?:per|a|each)\s{1,3}(?:share|Share)\b")
_AGG_PRICE_RE = re.compile(
    r"\baggregate\s{1,3}(?:cash\s{1,3})?(?:purchase|subscription)\s{1,3}price\b"
    r"(?:\s{1,3}in\s{1,3}cash)?(?:\s{1,3}(?:of|equal\s{1,3}to|totaling|totalling))?\s{1,3}",
    re.IGNORECASE,
)
_AGG_PRICE_TAIL_RE = re.compile(
    r"\baggregate\s{1,3}(?:cash\s{1,3})?(?:purchase|subscription)\s{1,3}$", re.IGNORECASE
)
_PRICE_PER_SHARE_RE = re.compile(
    r"\bprice\s{1,3}per\s{1,3}share\s{1,3}(?:of\s{1,3}|equal\s{1,3}to\s{1,3})?", re.IGNORECASE
)
_PRICE_OF_RE = re.compile(r"\bprice\s{1,3}of\s{1,3}", re.IGNORECASE)
_AT_MONEY_RE = re.compile(r"\bat\s{1,3}(?=(?:US)?\$)", re.IGNORECASE)
_UP_TO_RE = re.compile(
    r"\b(?:up\s{1,3}to|not\s{1,3}to\s{1,3}exceed|a\s{1,3}maximum\s{1,3}of)\s{1,3}"
    r"(?:an\s{1,3}aggregate\s{1,3}of\s{1,3})?",
    re.IGNORECASE,
)
# A conversion, exercise or stated price is never the purchase price.
_OTHER_PRICE_RE = re.compile(
    r"\b(?:conversion|exercise|strike|stated|par|liquidation|redemption)\b", re.IGNORECASE
)

# Dates.
_MONTHS: Dict[str, int] = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6, "july": 7,
    "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
    "jan.": 1, "feb.": 2, "mar.": 3, "apr.": 4, "jun.": 6, "jul.": 7, "aug.": 8,
    "sep.": 9, "sept.": 9, "oct.": 10, "nov.": 11, "dec.": 12,
}
_MONTH_ALT = (
    r"January|February|March|April|May|June|July|August|September|October|November|December|"
    r"Jan\.|Feb\.|Mar\.|Apr\.|Jun\.|Jul\.|Aug\.|Sept\.|Sep\.|Oct\.|Nov\.|Dec\."
)
_DATE_AT_RE = re.compile(
    rf"(?:(?P<m1>{_MONTH_ALT})\s{{1,3}}(?P<d1>[0-9]{{1,2}}),\s{{0,3}}(?P<y1>[0-9]{{4}})"
    rf"|(?P<d2>[0-9]{{1,2}})\s{{1,3}}(?P<m2>{_MONTH_ALT})\s{{1,3}}(?P<y2>[0-9]{{4}}))(?![0-9])"
)
_ON_DATE_RE = re.compile(r"\b[Oo]n\s{1,3}(?=[0-9]|[A-Z])")
#: A sentence-opening lead-in after which "on <date>," is still the clause's own date
#: ("Subsequently, on March 2, 2026, the Company completed …").
_ON_LEAD_INS: FrozenSet[str] = frozenset({
    "subsequently", "thereafter", "additionally", "accordingly", "further", "furthermore", "also",
    "separately", "in addition", "then", "later", "finally", "consequently",
})
_DATED_RE = re.compile(
    r"\b[Dd]ated\s{1,3}(?:as\s{1,3}of\s{1,3})?(?=[0-9]|[A-Z])"
    r"|\b(?:Original\s{1,3})?Issue\s{1,3}Date:?\s{1,3}(?=[0-9]|[A-Z])"
    r"|\bDate\s{1,3}of\s{1,3}Issuance:?\s{1,3}(?=[0-9]|[A-Z])"
)
_AGREEMENT_NAME_RE = re.compile(r"\b(?:Purchase|Subscription|Investment)\s{1,3}Agreement\b")
# What a date is bound to. A SIGNING date ("On September 15, 2025, Acme entered into …") is a
# closing date only when it is the filing date itself (the closing then fell on that day); a
# CLOSING clause ("The closing occurred on …", "… closed on …", "(the “Closing Date”)") always is.
_SIGNING_RE = re.compile(
    r"\b(?:entered\s{1,3}into|signed|executed|announced|agreed|disclosed|dated|reported|filed)\b",
    re.IGNORECASE,
)
# The narrow form, for the clause between a sentence's own "On <date>," and its sale verb: a
# finite signing verb ("On …, Coherent entered into …, and Coherent completed …") — never a
# participle of a subordinate phrase ("… the Purchase Agreement dated as of …", "as previously
# announced").
_SIGNING_VERB_RE = re.compile(r"\b(?:entered\s{1,3}into|signed|executed|agreed)\b", re.IGNORECASE)
# Before a signing verb: it is a participle of the agreement it follows ("the Securities Purchase
# Agreement entered into on September 15, 2025", "as agreed"), never the clause's own verb.
_REDUCED_RELATIVE_BEFORE_RE = re.compile(
    r"\b[Aa]greements?[”\"’']?\)?,?\s{1,3}(?:previously\s{1,3}|originally\s{1,3})?$|\b[Aa]s\s{1,3}$"
)
# "As previously disclosed / announced / reported": what follows restates an older event.
_PREVIOUSLY_RE = re.compile(r"\b[Aa]s\s{1,3}previously\s{1,3}(?:disclosed|announced|reported)\b")
_CLOSING_VERB_RE = re.compile(
    r"\b(?:completed|closed|consummated|sold|purchased)\b|\bissued\s{1,3}and\s{1,3}sold\b", re.IGNORECASE
)
_CLOSING_ON_RE = re.compile(
    r"\b[Cc]losings?\b[^.;]{0,80}?\b(?:occurred|took\s{1,3}place|was\s{1,3}(?:completed|consummated|effected|held))"
    r"\s{1,3}on\s{1,3}(?=[0-9]|[A-Z])"
    r"|\b(?:closed|completed|consummated)\s{1,3}on\s{1,3}(?=[0-9]|[A-Z])"
)
_CLOSING_DATE_TERM_RE = re.compile(r"\s{0,2}\((?:the\s{1,3})?[“\"]Closing\s{1,3}Date[”\"]\)")

# T2 — signed, not closed.
_AGREED_RE = re.compile(
    r"\b(?:agreed|agrees|committed|commits)\s{1,3}to\s{1,3}"
    r"(?:purchase|subscribe\s{1,3}for|acquire|buy|invest)\b",
    re.IGNORECASE,
)
_AGREED_SELL_RE = re.compile(
    r"\b(?:agreed|agrees)\s{1,3}to\s{1,3}"
    r"(?:issue\s{1,3}and\s{1,3}sell|sell\s{1,3}and\s{1,3}issue|sell|issue)\s{1,3}to\s{1,3}",
    re.IGNORECASE,
)
_SPA_WITH_RE = re.compile(
    r"\b(?:entered\s{1,3}into|executed|signed)\s{1,3}(?:a|an)\s{1,3}"
    r"(?:securities|share|stock|subscription|investment)\s{1,3}(?:purchase\s{1,3})?agreement\b",
    re.IGNORECASE,
)
_WITH_RE = re.compile(r"with\s{1,3}")
# An agreement that is itself hypothetical is no commitment ("agreed" is the verb here).
_HYPOTHETICAL_RE = re.compile(r"\b(?:may|might|could|would|[Ii]f|[Uu]nless|[Ww]hether)\b")
# Where the member's own object ends: another party's option or clause ("…, and certain of our
# existing stockholders have agreed to purchase up to $250 million") is never the member's cap.
_OPTION_TO_RE = re.compile(r"\boptions?\s{1,3}to\s{1,3}(?:purchase|acquire|buy|subscribe)\b", re.IGNORECASE)
_OTHER_PARTY_CLAUSE_RE = re.compile(
    r"\b(?:has|have)\s{1,3}(?:also\s{1,3})?(?:indicated|agreed|committed|expressed|elected|undertaken)\b",
    re.IGNORECASE,
)
_OWN_OBJECT_STOPS: Tuple[str, ...] = (
    ", and ", ", while ", ", but ", ", whereas ", ", whilst ", "; ",
    ", as has ", ", as have ", ", as does ", ", as do ", ", as did ", ", as will ",
)
# A second purchase verb after the member's own: another party's purchase — or the member's own
# OPTIONAL one ("… and may purchase up to $250 million of additional shares") — starts there,
# never the member's committed object.
_SECOND_PURCHASE_RE = re.compile(
    r"\b(?:to|will|may|shall|would|might|could|can|intends?\s{1,3}to|expects?\s{1,3}to|plans?\s{1,3}to|"
    r"are\s{1,3}expected\s{1,3}to)\s{1,3}(?:purchase|subscribe|buy|acquire|invest)\b",
    re.IGNORECASE,
)
# A whitespace-delimited token (the party scan of a T2 object, a few hundred characters).
_TOKEN_RE = re.compile(r"\S{1,80}")
#: After a depth-0 " and " in the member's object, one of these within a few words opens a NEW
#: clause ("… and Beta Capital LLC will purchase …", "… and certain of our existing stockholders
#: intend to …", "… and may purchase …") — unless a relative pronoun introduces it ("… and
#: warrants that are exercisable …").
_FINITE_WORDS: FrozenSet[str] = frozenset({
    "will", "shall", "may", "might", "would", "could", "can", "has", "have", "had", "is", "are", "was",
    "were", "intend", "intends", "expect", "expects", "plan", "plans", "agree", "agrees", "agreed",
    "commit", "commits", "committed", "purchases", "purchased", "subscribes", "subscribed", "acquires",
    "acquired", "invests", "invested", "indicated", "undertakes", "undertook", "undertaken", "seek",
    "seeks", "wish", "wishes",
})
_RELATIVE_WORDS: FrozenSet[str] = frozenset({"that", "which", "who", "whom", "whose"})
#: Words that make a capitalised run another investor's name ("Beta Capital", "Gamma Partners").
_ENTITY_WORDS: FrozenSet[str] = frozenset({
    "capital", "partners", "management", "holdings", "ventures", "fund", "funds", "advisors", "advisers",
    "associates",
})
_BARE_STOCK_RE = re.compile(r"\b(?:common|ordinary)\s{1,3}(?:stock|shares)\b|\bclass\s{1,3}a\b", re.IGNORECASE)

# T3 — indication of interest.
_IOI_RE = re.compile(
    r"\b(?:has|have)(?:,\s{0,3}severally\s{1,3}and\s{1,3}not\s{1,3}jointly,|"
    r"\s{1,3}(?:each|collectively|jointly|separately|severally|also))?\s{1,3}"
    r"indicated\s{1,3}(?:an|their|its)\s{1,3}interest\s{1,3}in\s{1,3}"
    r"(?:purchasing|acquiring|subscribing\s{1,3}for|buying)\b",
    re.IGNORECASE,
)
_NOT_THE_MEMBER_RE = re.compile(
    r"\b(?:other\s{1,3}than|excluding|except|not\s{1,3}including|(?:do|does)\s{1,3}not\s{1,3}include|"
    r"but\s{1,3}not|rather\s{1,3}than|instead\s{1,3}of|unlike|none\s{1,3}of|neither|nor|"
    r"no\s{1,3}(?:other\s{1,3}|existing\s{1,3}|current\s{1,3}|such\s{1,3})?"
    r"(?:stockholders?|shareholders?|investors?|holders?|persons?|entity|entities|affiliates?|"
    r"purchasers?|one))\b",
    re.IGNORECASE,
)
# A T3 subject's appositive ("Beta Capital LLC, a partner of NVIDIA Corporation, has …") and the
# words that make the member the OBJECT of a phrase ("a partner of NVIDIA"), never its head —
# except its own affiliates ("Entities affiliated with NVIDIA Corporation").
_IOI_OBJECT_BEFORE_RE = re.compile(r"\b(?:of|with|by|for|from|to)\s{1,3}(?:the\s{1,3})?$", re.IGNORECASE)
_IOI_AFFILIATE_BEFORE_RE = re.compile(
    r"\b(?:affiliated\s{1,3}with|affiliates?\s{1,3}of|controlled\s{1,3}by|managed\s{1,3}by|"
    r"subsidiar(?:y|ies)\s{1,3}of)\s{1,3}(?:the\s{1,3})?$",
    re.IGNORECASE,
)
_IOI_LIST_BEFORE_RE = re.compile(
    r"(?:\b(?:and|or|nor|plus|including|namely)|&|,|\b(?:together|along)\s{1,3}with|\beach\s{1,3}of|\bboth|"
    r"\bsuch\s{1,3}as)\s{1,3}(?:the\s{1,3})?$",
    re.IGNORECASE,
)

# T4 — 6-K directed issuance.
_DIRECTED_RE = re.compile(
    r"\bdirected\s{1,3}(?:share\s{1,3})?(?:issuance|issue|offering|placement)\s{1,3}to\s{1,3}",
    re.IGNORECASE,
)
_COMPLETED_RE = re.compile(r"\bcomplet(?:ed|ion)\b|\bregistered\b", re.IGNORECASE)
# Proposed or conditional, not done: "the Board proposes …", "conditional on EGM approval".
_PROPOSED_RE = re.compile(r"\b(?:[Pp]ropos(?:e|es|ed|al|als)|[Cc]onditional|[Cc]onditioned)\b")
# The member's own binding undertaking ("NVIDIA has irrevocably undertaken to subscribe for …").
_UNDERTAKING_RE = re.compile(
    r"\b(?:irrevocably\s{1,3})?(?P<verb>agreed|agrees|undertaken|undertakes|undertook|committed|commits)\s{1,3}"
    r"to\s{1,3}(?:subscribe|purchase|acquire|invest)\b",
    re.IGNORECASE,
)
#: A directed issuance's clause ends at one of these (a release's headline lines carry no period).
_STRONG_CLAUSE_STOPS: Tuple[str, ...] = (". ", "; ", ": ", " – ", " — ", "· ", "• ")
# Completion that governs the directed issuance itself: its clause's own verb right before the
# phrase ("Nokia has completed the directed share issuance to …") or its passive right after it
# ("The directed share issuance to NVIDIA … was completed") — a future tail in the same sentence
# ("…, which are expected to be admitted to trading …") then speaks of later steps.
_COMPLETED_BEFORE_RE = re.compile(
    r"\b(?:completed|consummated|closed|registered)\s{1,3}(?:the\s{1,3}|a\s{1,3}|an\s{1,3}|its\s{1,3})?$",
    re.IGNORECASE,
)
_COMPLETED_PASSIVE_RE = re.compile(
    r"\b(?:was|were|has\s{1,3}been|have\s{1,3}been|had\s{1,3}been)\s{1,3}(?:duly\s{1,3})?"
    r"(?:completed|consummated|closed|registered)\b",
    re.IGNORECASE,
)
# A condition after a governing completion still undoes it ("… has completed the directed share
# issuance …, the completion of which is subject to EGM approval"); only a later step's future
# tense ("…, which are expected to be admitted to trading …") does not.
_CONDITION_TAIL_RE = re.compile(r"\b(?:[Ss]ubject\s{1,3}to|[Uu]nless|[Ii]f|[Pp]rovided\s{1,3}that)\b")
# A temporal or conditional lead before the completion verb ("Once Nokia has completed …",
# "After …", "Upon …"): the completion is then subordinate, and the main clause's tense decides.
_SUBORDINATE_LEAD_RE = re.compile(
    r"\b(?:once|after|when|upon|until|before|if|unless|whether|following|as\s{1,3}soon\s{1,3}as|"
    r"prior\s{1,3}to)\b",
    re.IGNORECASE,
)

# The purchaser phrase after the member's mention (definition and appositive skipped).
_SEVERAL_AFTER_RE = re.compile(
    r"(?:and|or|plus|together\s{1,3}with|along\s{1,3}with|as\s{1,3}well\s{1,3}as|et\s{1,3}al)\b|&",
    re.IGNORECASE,
)
_POSSESSIVE_AFTER_RE = re.compile(r"[’'](?:s\b|\s)")
# The alias as the first words of a LONGER name: "NVIDIA Corporation Employee Benefit Plan".
_LONGER_NAME_RE = re.compile(r" (?:[A-Z][a-z]|[0-9])")
# A finite verb right after a name: the name is the subject of a new clause, not a purchaser.
_VERB_AFTER_RE = re.compile(
    r"(?:is|was|are|were|has|have|had|will|shall|may|might|would|could|did|does|do)\b"
)
_SEVERAL_BEFORE_RE = re.compile(
    r"\b(?:each\s{1,3}of|among|certain|including|investors?|purchasers?|and|or)\b|[,&;]",
    re.IGNORECASE,
)

# Deny terms, bound to ONE mention of the member (after it / before it).
_DENY_AFTER_RE = re.compile(
    r"(?:"
    r"(?:as|in\s{1,3}(?:its|their)\s{1,3}capacity\s{1,3}as)\s{1,3}(?:an?\s{1,3}|the\s{1,3}|"
    r"one\s{1,3}of\s{1,3}the\s{1,3})?(?:representatives?\s{1,3}of\s{1,3}the\s{1,3})?"
    r"(?:underwriters?|initial\s{1,3}purchasers?|placement\s{1,3}agents?|sales\s{1,3}agents?|"
    r"dealers?|lenders?|trustees?|agents?|arrangers?|book-?runners?)\b"
    r"|(?:a|an|one\s{1,3}of\s{1,3}(?:the|our|its)|our|its|the)\s{1,3}"
    r"(?:(?:significant|major|large|key|principal|strategic)\s{1,3})?"
    r"(?:customers?|suppliers?|vendors?|licensors?|licensees?|distributors?|resellers?)\b"
    r")",
    re.IGNORECASE,
)
_DENY_POSSESSIVE_RE = re.compile(
    r"[’']s\s{1,3}(?:products?|platforms?|technology|technologies|gpus?|chips?|services?|"
    r"software|hardware|systems?|accelerators?|devices?|solutions?)\b",
    re.IGNORECASE,
)
_DENY_BEFORE_RE = re.compile(r"\bfrom\s{1,3}(?:the\s{1,3})?$", re.IGNORECASE)

# Not a completed sale: future, conditional or agreed-only wording before the verb.
_NOT_DONE_RE = re.compile(
    r"\b(?:will|shall|would|could|might|expects?|expected|anticipates?|anticipated|intends?|"
    r"plans?|agreed|agrees|[Tt]o\s{1,3}be|[Uu]pon\s{1,3}(?:the\s{1,3})?closing|"
    r"[Aa]t\s{1,3}(?:the\s{1,3})?closing|[Ss]ubject\s{1,3}to|[Ff]ollowing\s{1,3}(?:the\s{1,3})?closing|"
    r"[Ii]f|[Ww]hen|[Uu]nless|[Ww]hether)\b"
)
# "to be issued", "will be issued" — never "has been issued".
_BE_BEFORE_RE = re.compile(r"\bbe\s{1,3}$")
# A negation bound to the verb: "has not (yet) completed", "hasn’t completed", "has yet to",
# "failed to" — searched only in the few characters before the verb, never the whole sentence.
_NEGATED_BEFORE_RE = re.compile(
    r"(?:\b(?:not|never)|n[’']t|\byet\s{1,3}to|\bfailed\s{1,3}to)\s{1,3}(?:yet\s{1,3})?(?:been\s{1,3}|have\s{1,3})?$",
    re.IGNORECASE,
)

# The sold noun.
_PRE_FUNDED_RE = re.compile(r"\bpre-?\s?funded\s{1,3}warrants?\b", re.IGNORECASE)
_WARRANT_RE = re.compile(r"\bwarrants?\b", re.IGNORECASE)
_PREFERRED_RE = re.compile(r"\b(?:preferred|preference)\b", re.IGNORECASE)
_CONVERTIBLE_RE = re.compile(r"\bconvertible\b", re.IGNORECASE)
_DEBT_RE = re.compile(r"\b(?:notes?|debentures?|bonds?|loans?)\b", re.IGNORECASE)
_SAFE_RE = re.compile(r"\bsimple\s{1,3}agreements?\s{1,3}for\s{1,3}future\s{1,3}equity\b|\bSAFEs?\b", re.IGNORECASE)
_SAFE_WORD_RE = re.compile(r"\bSAFEs?\b")
_UNITS_RE = re.compile(r"\bunits?\b", re.IGNORECASE)
_UNNAMED_INSTRUMENT_RE = re.compile(r"\b(?:options?|rights?|exchangeable|debt)\b", re.IGNORECASE)
_CLASS_LETTER_RE = re.compile(r"\bclass\s{1,3}([a-z])\b", re.IGNORECASE)
_PLAIN_STOCK_RE = re.compile(r"\b(?:common|ordinary)\b|\bshares?\b", re.IGNORECASE)
# The filer's own class (read on the lower-cased noun): "our" / "its" only DIRECTLY before the
# class ("our common stock", "its Class A common stock") — never "of our portfolio company, …".
_OUR_CLASS_RE = re.compile(r"\bour\s{1,3}(?:own\s{1,3})?(?:class\s{1,3}a\s{1,3})?(?:common|ordinary|shares)\b")
_ITS_STOCK_RE = re.compile(r"\bits\s{1,3}(?:own\s{1,3})?(?:class\s{1,3}a\s{1,3})?(?:common|ordinary|shares)")
# Right after an exact filer term: its possessive or a bare class ("the Company’s common stock",
# "Synopsys’ common stock", "Nokia shares").
_OWNED_AFTER_TERM_RE = re.compile(
    r"(?:'s?\s{1,3}|\s{1,3}(?:new\s{1,3})?)(?:class\s{1,3}a\s{1,3})?(?:common|ordinary|shares)\b"
)
_FOREIGN_OWNER_RE = re.compile(r"\bof\s{1,3}(?:the\s{1,3})?([A-Z][\w.&'’\-]{1,40})")
#: The first word of a security's title after "of" ("shares of Common Stock", "of Class A
#: Ordinary Shares") — never an owner. The filer's generic names ("the Company") own only when the
#: document defines them for the filer (they are then among its terms).
_SECURITY_TITLE_WORDS: FrozenSet[str] = frozenset({
    "common", "class", "series", "preferred", "ordinary", "stock", "shares", "share", "units",
    "warrants", "notes", "capital", "american", "restricted", "voting", "non-voting",
    "convertible", "depositary", "subordinate", "multiple",
})
# What a securities purchase buys (a purchase of products or services names none of these).
_SECURITY_WORD_RE = re.compile(
    r"\b(?:stock|shares?|securities|equity|interests?|units?|warrants?|notes?|preferred|"
    r"debentures?|safes?)\b",
    re.IGNORECASE,
)
_DIVESTITURE_RE = re.compile(
    r"\b(?:subsidiar(?:y|ies)|wholly[\s-]{1,2}owned|majority[\s-]{1,2}owned|all\s{1,3}of\s{1,3}the\s{1,3}"
    r"(?:issued\s{1,3}and\s{1,3})?outstanding|business(?:es)?|divisions?|assets?|disposition|"
    r"carve[\s-]{0,2}out|joint\s{1,3}ventures?|affiliates?|portfolio\s{1,3}compan(?:y|ies)|investees?)\b",
    re.IGNORECASE,
)

# Context words for a document with no template match (skipped reasons only).
_COMMERCIAL_RE = re.compile(
    r"\b(?:services?\s{1,3}agreements?|supply|suppliers?|licen[cs](?:e|es|ed|ing|or|ors)|"
    r"collaborat(?:e|es|ed|ing|ion|ions)|partner(?:s|ship|ships)?|customers?|vendors?|"
    r"products?|platforms?|cloud|commercial|resellers?|distribut(?:or|ors|ion)|"
    r"purchase\s{1,3}orders?|leases?|gpus?|chips?)\b",
    re.IGNORECASE,
)
_CO_INVESTOR_RE = re.compile(
    r"\b(?:investors?|stockholders?|shareholders?|limited\s{1,3}partners?|holders\s{1,3}of)\b",
    re.IGNORECASE,
)

# EX-4.x: the member as a party in the preamble.
_PARTY_RE = re.compile(
    r"\b(?:by\s{1,3}and\s{1,3}between|between|issued\s{1,3}to|registered\s{1,3}holder|"
    r"in\s{1,3}favou?r\s{1,3}of|certifies\s{1,3}that|Holder|Warrantholder|Purchaser|Investor)\b"
)
_TO_PURCHASE_SHARES_RE = re.compile(
    r"\bto\s{1,3}purchase\s{1,3}(?:up\s{1,3}to\s{1,3})?(?:an\s{1,3}aggregate\s{1,3}of\s{1,3})?"
    r"(?=[0-9])",
    re.IGNORECASE,
)
# An exhibit that is not an executed instrument: a "Form of Warrant", a blank date ("Issue Date:
# [●]", "dated as of [__]", "[__], 2026", "March [●], 2026"), or a warrant still "to be issued".
_EX4_TITLE_CHARS = 300
_FORM_OF_RE = re.compile(r"\bform\s{1,3}of\b", re.IGNORECASE)
_PLACEHOLDER = r"(?:\[\s{0,3}(?:_{1,40}|●{1,3}|•{1,3}|\*{1,5}|[Xx]{2,5})?\s{0,3}\]|(?<!_)_{3,40}(?!_))"
_PLACEHOLDER_DATE_RE = re.compile(
    r"\b(?:[Dd]ated|DATED|[Ii]ssue\s{1,3}[Dd]ate|ISSUE\s{1,3}DATE|[Dd]ate\s{1,3}of\s{1,3}[Ii]ssuance)"
    rf"(?:\s{{1,3}}(?:as\s{{1,3}}of|AS\s{{1,3}}OF))?:?\s{{0,3}}{_PLACEHOLDER}"
    rf"|{_PLACEHOLDER}\s{{0,3}},?\s{{0,3}}(?:19|20)[0-9]{{2}}\b"
    rf"|\b(?:{_MONTH_ALT})\s{{1,3}}{_PLACEHOLDER}"
)
_EX4_NOT_DONE_RE = re.compile(
    r"\b(?:[Ww]arrants?|WARRANTS?|[Nn]otes?|NOTES?|[Ss]ecurities|SECURITIES)\s{1,3}"
    r"(?:(?:shall|will|SHALL|WILL)\s{1,3}be|(?:is|are|IS|ARE)\s{1,3}to\s{1,3}be|to\s{1,3}be|TO\s{1,3}BE)"
    r"\s{1,3}(?:issued|ISSUED)\b"
)

#: Cheap pre-check: a sentence near a mention is examined only if it holds one of these.
_DEAL_WORDS: Tuple[str, ...] = (
    "purchas", "issu", "sold", "sale", "subscri", "agreed", "agrees", "commit", "interest",
    "warrant", "preferred", "convertible", "directed", "placement", "invest",
)

# Terms a definition gives that never stand for one party alone.
_GENERIC_TERMS: FrozenSet[str] = frozenset({
    "company", "corporation", "issuer", "registrant", "agreement", "purchase agreement",
    "parties", "party", "shares", "closing", "transaction", "transactions", "warrant",
    "notes", "securities", "commitment", "effective date", "issue date", "we", "us", "our",
})
_HOLDER_TERM_ENDINGS: Tuple[str, ...] = ("holder", "purchaser", "investor", "subscriber")
#: Capitalised words a closing clause may hold without naming another party.
_CLOSING_CLAUSE_WORDS: FrozenSet[str] = frozenset({
    "the", "closing", "closings", "purchase", "agreement", "transaction", "transactions", "private",
    "placement", "shares", "share", "securities", "investment", "offering", "sale", "issuance", "such",
    "this", "that", "these", "subscription", "first", "second", "initial", "final", "it", "its", "act",
    "exchange", "commission", "current", "report", "common", "stock", "class", "series", "preferred",
    "on", "upon", "following", "as", "in", "pursuant", "concurrently", "accordingly", "thereafter", "u.s",
})
#: A capitalised word after " to " that refers to a document, never a purchaser.
_REFERENCE_WORDS: FrozenSet[str] = frozenset({
    "section", "sections", "item", "items", "rule", "rules", "regulation", "form", "exhibit",
    "exhibits", "annex", "schedule", "article", "note", "notes", "part", "appendix",
})

# Name tokens.
_LEGAL_SUFFIX_TOKENS: FrozenSet[str] = frozenset({
    "inc", "incorporated", "corp", "corporation", "co", "company", "ltd", "limited", "llc",
    "l.l.c", "lp", "l.p", "llp", "l.l.p", "plc", "p.l.c", "nv", "n.v", "sa", "s.a", "ag",
    "se", "oyj", "ab", "asa", "gmbh", "pbc", "bv", "b.v", "spa", "s.p.a", "kk", "k.k",
    "pte", "pty", "sarl", "s.a.r.l",
})
#: Folded legal forms stripped from the end of a comparison key (``fold_entity_name`` has
#: already turned "Corp" into "corporation" and "N.V." into "nv").
_KEY_SUFFIXES: FrozenSet[str] = frozenset({
    "incorporated", "corporation", "company", "limited", "llc", "lp", "llp", "plc", "nv",
    "sa", "ag", "se", "oyj", "ab", "asa", "gmbh", "pbc", "bv", "spa", "kk", "pte", "pty",
    "sarl",
})
_NAME_CONNECTORS: FrozenSet[str] = frozenset({"and", "&", "of", "de", "du", "der", "la", "le", "von", "van"})
_ABBREVIATIONS: FrozenSet[str] = frozenset({
    "inc", "corp", "co", "ltd", "llc", "lp", "plc", "no", "nos", "mr", "mrs", "ms", "dr", "jr",
    "sr", "st", "vs", "etc", "approx", "sec", "secs", "art", "arts", "fig", "ex", "exh", "nv",
    "sa", "ag", "bros", "dept", "assn", "mfg", "intl", "natl", "para", "paras", "cl", "pp",
    "vol", "ch", "mt", "ft", "ave", "blvd", "rd", "ste", "jan", "feb", "mar", "apr", "jun",
    "jul", "aug", "sep", "sept", "oct", "nov", "dec", "esq", "gen", "gov", "hon", "messrs",
    "prof", "rev",
})
_OPENERS = "(“\"‘'[•"
_SCALES: Dict[str, Decimal] = {
    "thousand": Decimal(10) ** 3, "million": Decimal(10) ** 6,
    "billion": Decimal(10) ** 9, "trillion": Decimal(10) ** 12,
}
_DIGIT_SEPARATORS = str.maketrans("", "", ", \u00a0\u202f")

# Where a phrase ends (depth-0 positions only): a sale's object, its purchaser, its noun.
_OBJECT_STOPS: Tuple[str, ...] = (
    " to ", ", pursuant", " pursuant to", ", for ", " for an ", " for a ", " for $", " for US$",
    ", at ", " at a ", " at $", " at the ", "; ", ". ", " in a private", " in exchange",
    " in consideration", ", which", ", in ",
)
_NOUN_STOPS: Tuple[str, ...] = _OBJECT_STOPS + (" which ", " that ", " were ", " was ", " from ")
_PURCHASER_STOPS: Tuple[str, ...] = (
    ", for", " for ", ", pursuant", " pursuant", ", in ", " in a ", " in an ", ", at ", " at a ",
    " at $", " at the ", ", representing", ", which", ", who", ". ", "; ", ", on ", " on ",
    ", under", " under ", ", dated", " dated ", ", subject", " subject to", " in exchange",
    " in consideration", " upon ", ", by ",
)


# ── Small helpers ────────────────────────────────────────────────────────────────────────


@functools.lru_cache(maxsize=512)
def _alias_regex(alias: str) -> "re.Pattern[str]":
    """A member alias, case-insensitive (EDGAR's search is), word-bounded, any whitespace.
    A literal pattern: linear on any input."""
    body = r"\s{1,3}".join(re.escape(part) for part in alias.split())
    tail = r"(?!\w)" if alias[-1].isalnum() else ""
    return re.compile(r"(?<![\w&])" + body + tail, re.IGNORECASE)


@functools.lru_cache(maxsize=1024)
def _term_regex(term: str) -> "re.Pattern[str]":
    """A defined term, case-sensitive as defined, word-bounded. A literal pattern."""
    body = r"\s{1,3}".join(re.escape(part) for part in term.split())
    return re.compile(r"(?<![\w&])" + body + r"(?!\w)")


def _company_key(name: Any) -> str:
    """A comparison key for an entity name: the EDGAR state tag dropped, folded
    (``stake_names.fold_entity_name``) and up to two trailing legal forms stripped — never one
    a dangling "and" would be left by ("Eli Lilly and Company" stays whole, as SEC's
    "ELI LILLY & Co" does). ``INTEL CORP`` and ``Intel Corporation`` → "intel"."""
    if not isinstance(name, str):
        return ""
    tokens = fold_entity_name(strip_state_tag(name.strip())).split()
    for _ in range(2):
        if len(tokens) > 1 and tokens[-1] in _KEY_SUFFIXES and tokens[-2] != "and":
            tokens = tokens[:-1]
    return " ".join(tokens)


def _parse_date(m: "re.Match[str]") -> Optional[date]:
    if m.group("m1"):
        month, day, year = m.group("m1"), m.group("d1"), m.group("y1")
    else:
        month, day, year = m.group("m2"), m.group("d2"), m.group("y2")
    month_no = _MONTHS.get(month.lower())
    if month_no is None:
        return None
    try:
        out = date(int(year), month_no, int(day))
    except ValueError:
        return None
    return out if 1990 <= out.year <= 2100 else None


def _integral(value: Decimal) -> Decimal:
    """``Decimal("5000000000.0")`` → ``Decimal("5000000000")``: a whole-dollar figure keeps no
    fraction (and never an exponent)."""
    return Decimal(int(value)) if value == value.to_integral_value() else value


def _fmt_usd(value: Optional[Decimal]) -> str:
    if value is None:
        return "no amount"
    if value == value.to_integral_value():
        return f"${int(value):,}"
    return f"${value:,.2f}"


def _clip(text: str) -> str:
    return text if len(text) <= DETAIL_MAX_CHARS else text[: DETAIL_MAX_CHARS - 1] + "…"


def _share_int(num: str) -> Optional[int]:
    digits = num.translate(_DIGIT_SEPARATORS)
    if not digits.isdigit() or not digits.isascii():
        return None
    value = int(digits)
    return value if 0 < value <= MAX_SHARES else None


def _starts_mid_number(text: str, pos: int) -> bool:
    """True when a number read at ``pos`` would start inside a longer one ("…2,000,|000",
    or the "389 351" of "166 389 351")."""
    if pos <= 0:
        return False
    prev = text[pos - 1]
    if prev.isdigit():
        return True
    if prev in ",." and pos >= 2 and text[pos - 2].isdigit():
        return True
    if prev in " \u00a0\u202f" and pos >= 2 and text[pos - 2].isdigit():
        j, run = pos - 2, 0
        while j >= 0 and text[j].isdigit() and run < 4:
            run, j = run + 1, j - 1
        return run <= 3 and (j < 0 or not text[j].isalnum())
    return False


@dataclass(frozen=True)
class _Money:
    value: Optional[Decimal]   # None: not U.S. dollars, or no readable figure
    usd: bool
    start: int
    end: int
    per_share: bool            # "$23.28 per share" — never an aggregate amount


#: Scale abbreviations after a figure ("$2bn", "US$ 1.5 bln", "$500 mn", "$250MM", "$5 M"),
#: matched case-insensitively — except a bare "M", which is upper-case only.
_SUFFIX_SCALES: Dict[str, Decimal] = {
    "bn": Decimal(10) ** 9, "bln": Decimal(10) ** 9,
    "mn": Decimal(10) ** 6, "mln": Decimal(10) ** 6, "mm": Decimal(10) ** 6,
}
#: Letters after a figure that make it unreadable: a scale we do not map ("$2b", "$50m" — a bare
#: lower-case "m" may be metres or a typo —, "$5k", "$2 tn") or a scale word in a form the
#: pattern does not read ("$2 millions", "$2-billion"). Ordinary words ("in cash") are not here.
_SUFFIX_REFUSED: FrozenSet[str] = frozenset({
    "b", "m", "k", "t", "tn", "trn", "bil", "mil", "mio", "mrd", "thou", "mils", "bils",
    "thousands", "millions", "billions", "trillions",
})


def _money_suffix(text: str, end: int) -> Tuple[Optional[Decimal], int, bool]:
    """``(scale, new_end, refused)`` for what follows a figure ending at ``end``: a mapped scale
    abbreviation (glued or after at most three spaces), a refused one, or nothing. Letters GLUED to
    the digits ("$2bn", "$50in") are always a suffix; spaced ones only when they are a known
    abbreviation, so "$50,000,000 in cash" reads as $50,000,000. A hyphen + scale word
    ("$2-billion") maps; a hyphen + other letters refuses."""
    n = len(text)
    i = end
    hyphen = i < n and text[i] == "-"
    if hyphen:
        i += 1
    else:
        while i < n and i < end + 3 and text[i] in "   ":
            i += 1
    glued = i == end and not hyphen
    if i < n and text[i].isalpha() and not text[i].isascii():
        return None, end, True  # "$50млн", "$50 млн": a scale word of another script
    j = i
    while j < n and j < i + 12 and text[j].isascii() and text[j].isalpha():
        j += 1
    word = text[i:j]
    if not word:
        return None, end, False
    if j < n and (text[j].isalnum() or text[j] in "&'’-"):
        # A longer word ("$50,000,000 consideration"), or one that goes on ("M&A", "m-1"):
        # never a scale — unreadable when glued to the digits, ordinary prose when spaced.
        return None, end, glued or hyphen
    low = word.lower()
    if hyphen:
        if low in _SCALES:
            return _SCALES[low], j, False
        return None, end, True
    if word == "M":
        return Decimal(10) ** 6, j, False
    if low in _SUFFIX_SCALES:
        return _SUFFIX_SCALES[low], j, False
    if glued or low in _SUFFIX_REFUSED:
        return None, end, True
    return None, end, False


def _parse_money(text: str, pos: int) -> Optional[_Money]:
    """The money at ``text[pos:]`` (after up to three spaces), read on the FULL text: a U.S.
    dollar figure, a foreign one (``usd=False``), a bare number of unknown currency
    (``usd=False``) or None. A figure that continues past the match ("$2,000,0000") is
    malformed and never read; so is one followed by a scale abbreviation outside the explicit
    mapping (``_money_suffix``: "$2bn" is $2,000,000,000, "$2b" and "$50m" are None)."""
    end_ws = min(len(text), pos + 3)
    while pos < end_ws and text[pos] == " ":
        pos += 1
    m = _MONEY_AT_RE.match(text, pos)
    if m is not None:
        end = m.end()
        if end < len(text) and (text[end].isdigit() or (
                text[end] in ",." and end + 1 < len(text) and text[end + 1].isdigit()
                and not m.group("scale"))):
            return None
        try:
            value = Decimal(m.group("num").replace(",", ""))
        except InvalidOperation:  # pragma: no cover - the pattern only admits digits
            return None
        scale = m.group("scale")
        if scale:
            value *= _SCALES[scale.lower()]
        else:
            suffix, end, refused = _money_suffix(text, end)
            if refused:
                return None
            if suffix is not None:
                value *= suffix
        if not (value.is_finite() and Decimal(0) < value <= MAX_AMOUNT_USD):
            return None
        return _Money(value, True, m.start(), end, _PER_SHARE_AFTER_RE.match(text, end) is not None)
    fm = _FOREIGN_AT_RE.match(text, pos)
    if fm is not None:
        return _Money(None, False, fm.start(), fm.end(), False)
    bm = _BARE_NUMBER_AT_RE.match(text, pos)
    if bm is not None:
        return _Money(None, False, bm.start(), bm.end(), False)
    return None


def _usd(money: Optional[_Money]) -> bool:
    """A U.S.-dollar aggregate figure (not a per-share price)."""
    return money is not None and money.usd and money.value is not None and not money.per_share


def _unreadable_usd(text: str, pos: int) -> bool:
    """A U.S.-dollar figure starts at ``text[pos:]`` (after up to three spaces) that
    ``_parse_money`` refused — "$2b", "$50m", "$2,000,0000", "$0": a fact that was there but could
    not be read, which the caller turns into a heads-up, never into "no amount stated"."""
    end_ws = min(len(text), pos + 3)
    while pos < end_ws and text[pos] == " ":
        pos += 1
    return _USD_MARK_RE.match(text, pos) is not None and _parse_money(text, pos) is None


def _money_marked(text: str, pos: int) -> bool:
    """A currency-marked figure starts at ``text[pos:]`` (after up to three spaces): U.S. dollars,
    read or not ("$250 million", "$2b"), or another currency ("€250 million") — never a bare
    number ("9.9% of our outstanding shares")."""
    end_ws = min(len(text), pos + 3)
    while pos < end_ws and text[pos] == " ":
        pos += 1
    return _USD_MARK_RE.match(text, pos) is not None or _FOREIGN_AT_RE.match(text, pos) is not None


def _noun_kind(noun: str) -> Tuple[str, Optional[str]]:
    """``("plain" | "instrument" | "unnamed" | "class_other" | "unknown", label)`` for a
    sold noun: "the Company’s Series A Convertible Preferred Stock" → ("instrument",
    "(convertible preferred)"); "the Company’s common stock" → ("plain", None)."""
    if _PRE_FUNDED_RE.search(noun):
        return "instrument", INSTRUMENT_PRE_FUNDED_WARRANT
    if _WARRANT_RE.search(noun):
        return "instrument", INSTRUMENT_WARRANT
    convertible = bool(_CONVERTIBLE_RE.search(noun))
    if _PREFERRED_RE.search(noun):
        return "instrument", INSTRUMENT_CONVERTIBLE_PREFERRED if convertible else INSTRUMENT_PREFERRED
    if _SAFE_RE.search(noun) and (_SAFE_WORD_RE.search(noun) or "future equity" in noun.lower()):
        return "instrument", INSTRUMENT_SAFE
    if _DEBT_RE.search(noun):
        return ("instrument", INSTRUMENT_CONVERTIBLE_NOTE) if convertible else ("unnamed", None)
    if _UNITS_RE.search(noun):
        return "instrument", INSTRUMENT_UNITS
    if convertible or _UNNAMED_INSTRUMENT_RE.search(noun):
        return "unnamed", None
    cm = _CLASS_LETTER_RE.search(noun)
    if cm is not None and cm.group(1).lower() != "a":
        return "class_other", None
    if _PLAIN_STOCK_RE.search(noun) or cm is not None:
        return "plain", None
    return "unknown", None


def _owns_noun(noun: str, terms: FrozenSet[str], *, company_side: bool) -> bool:
    """True when a plain noun is the FILER's stock: an EXACT filer term bound to the share
    class and ending the owner phrase ("the Company’s common stock", "Synopsys’ common stock",
    "common stock of the Company", "Nokia shares"); "our" directly before the class ("our Class A
    common stock"); or — when the filer is the seller ("the Company completed the sale of … its
    common stock") — "its" directly before it. Never when the noun names another owner ("common
    stock of Acme Robotics, Inc." for a filer called "Acme"; ``_foreign_owner``)."""
    if _foreign_owner(noun, terms):
        return False
    low = noun.replace("’", "'").lower()
    if _OUR_CLASS_RE.search(low):
        return True
    if company_side and _ITS_STOCK_RE.search(low):
        return True
    return any(_term_owns(noun, term) for term in terms)


def _term_owns(noun: str, term: str) -> bool:
    """The filer term ``term`` owns the class in ``noun``: word-bounded, never the tail of a
    longer capitalised name ("Beta Acme’s"), and followed by its possessive or a bare class
    ("Acme’s common stock", "Acme Class A common stock", "Nokia shares") — or the object of
    "of" that ends the owner phrase ("common stock of the Company", but never "of Acme
    Robotics")."""
    t = term.replace("’", "'")
    if t.lower() in ("we", "us", "our") or len(t) < 2:
        return False
    orig = noun.replace("’", "'")
    low = orig.lower()
    # Case-sensitive, as the document defines it: "the Company’s" is the filer, "our portfolio
    # company’s" is not.
    i = orig.find(t)
    while i != -1:
        j = i + len(t)
        left_ok = i == 0 or not (low[i - 1].isalnum() or low[i - 1] in "&-")
        right_ok = j == len(low) or not low[j].isalnum()
        if left_ok and right_ok and not _name_token_before(orig, i):
            if _OWNED_AFTER_TERM_RE.match(low, j):
                return True
            head = low[max(0, i - 8):i]
            if head.endswith(("of ", "of the ")) and _ends_owner_phrase(orig, j):
                return True
        i = orig.find(t, i + 1)
    return False


def _name_token_before(text: str, i: int) -> bool:
    """A capitalised name token right before ``i`` ("Beta " in "Beta Acme’s"); an article or a
    preposition ("the", "of") is not one."""
    head = text[max(0, i - 42):i]
    if not head.endswith(" "):
        return False
    words = head.split()
    if not words:
        return False
    word = words[-1]
    return bool(_NAME_TOKEN_RE.fullmatch(word)) and word.lower() not in ("the", "a", "an", "of", "and")


def _ends_owner_phrase(text: str, j: int) -> bool:
    """After a filer term at ``j``: only its legal form ("Acme, Inc."), then the end of the
    name — never another capitalised token ("Acme Robotics")."""
    rest = text[j:j + 80]
    tokens = rest.replace(",", " , ").split()
    k = 0
    while k < len(tokens):
        tok = tokens[k]
        if tok == ",":
            k += 1
            continue
        if tok.rstrip(".").lower() in _LEGAL_SUFFIX_TOKENS:
            k += 1
            continue
        return not (_NAME_TOKEN_RE.fullmatch(tok.rstrip(".,;:)")) and tok[:1].isupper())
    return True


def _foreign_owner(noun: str, terms: FrozenSet[str]) -> bool:
    """"shares of common stock of XYZ Corp." — "of [the] <Capitalised name>" where the name is
    not EXACTLY one of the document's names for the filer (its legal form and possessive
    aside): "of Acme Robotics, Inc." is foreign for a filer called "Acme". A capitalised
    security title ("of Common Stock", "of Class A Ordinary Shares") is not an owner."""
    keys = {_company_key(_strip_possessive(t)) for t in terms if t.lower() not in ("we", "us", "our")} - {""}
    for m in _FOREIGN_OWNER_RE.finditer(noun):
        word = _strip_possessive(m.group(1).rstrip(".,")).lower()
        if word in _SECURITY_TITLE_WORDS:
            continue
        if _company_key(_owner_name(noun, m.start(1))) not in keys:
            return True
    return False


def _owner_name(text: str, pos: int) -> str:
    """The owner's name starting at ``pos``: the capitalised run (``_name_after``) cut after a
    possessive token ("the Company’s Simple Agreements" → "Company"), its possessive and
    trailing punctuation stripped."""
    name = _name_after(text, pos) or text[pos:pos + 40].split(" ")[0]
    out: List[str] = []
    for tok in name.split():
        bare = _strip_possessive(tok)
        out.append(bare)
        if bare != tok:
            break
    return " ".join(out).rstrip(".,;:")


def _strip_possessive(name: str) -> str:
    for tail in ("’s", "'s", "’", "'"):
        if name.endswith(tail):
            return name[: -len(tail)]
    return name


def _is_abbreviation(text: str, dot: int) -> bool:
    lo = max(0, dot - 16)
    token = text[lo:dot]
    cut = max(token.rfind(" "), token.rfind("("), token.rfind("“"), token.rfind('"'), token.rfind("\n"))
    token = token[cut + 1:]
    if not token or token[-1] in ")”\"'’]":
        return False
    low = token.lower()
    if low in _ABBREVIATIONS or _DOTTED_ABBREVIATION_RE.fullmatch(low):
        return True
    return len(token) == 1 and token.isalpha() and token.isupper()


def _force_cut(text: str, s: int) -> int:
    """Where a sentence with no boundary in ``SENTENCE_MAX_CHARS`` is cut: after the last
    space in its final 300 characters that does not sit between two digit groups, else
    exactly at the limit. A number cut there is still read whole (figures are parsed on the
    full text) and no span may cut it (``verify_peg_span``)."""
    limit = s + SENTENCE_MAX_CHARS
    floor = limit - 300
    pos = text.rfind(" ", floor, limit)
    while pos > floor:
        if not (text[pos - 1].isdigit() and pos + 1 < len(text) and text[pos + 1].isdigit()):
            return pos + 1
        pos = text.rfind(" ", floor, pos)
    return limit


def _phrase_end(text: str, start: int, limit: int, stops: Sequence[str]) -> int:
    """The first ``stops`` occurrence in ``text[start:limit]`` outside parentheses, else
    ``limit``. " to " is no stop where it opens an infinitive ("warrants to purchase common
    stock", "to be issued"). Bounded: ``limit - start`` is at most a few hundred characters."""
    limit = max(start, min(limit, len(text)))
    best = limit
    for stop in stops:
        pos = text.find(stop, start, best)
        while pos != -1:
            infinitive = stop == " to " and text.startswith(_INFINITIVES, pos + 4)
            if not infinitive and _paren_depth(text, start, pos) == 0:
                best = pos
                break
            pos = text.find(stop, pos + 1, best)
    return best


#: " to <verb>" is an infinitive, never "to <purchaser>".
_INFINITIVES: Tuple[str, ...] = (
    "purchase", "acquire", "subscribe", "buy ", "be ", "receive", "the extent", "exercise", "convert",
)


def _paren_depth(text: str, start: int, pos: int) -> int:
    depth = 0
    for ch in text[start:pos]:
        if ch == "(":
            depth += 1
        elif ch == ")" and depth:
            depth -= 1
    return depth


def _names_before(text: str, pos: int) -> List[str]:
    """Candidate entity names ending just before ``pos`` (a defining parenthetical), longest
    first. ", a Delaware corporation" before the parenthetical is skipped; a comma inside a
    name is kept only before a legal form ("Marvell Technology, Inc."); a lower-case word, a
    date or a bracket ends the name. "On December 26, 2025, Intel Corporation " →
    ["Intel Corporation", "Corporation"]."""
    seg = text[max(0, pos - 200):pos].rstrip()
    ap = _APPOSITIVE_TAIL_RE.search(seg)
    if ap is not None:
        seg = seg[:ap.start()].rstrip()
    run: List[str] = []  # right to left
    for tok in reversed(seg.split()[-14:]):
        word = tok[:-1] if tok.endswith(",") else tok
        if not word:
            break
        low = word.lower().rstrip(".")
        if tok.endswith(","):
            # "Technology," before "Inc." — a comma only ever precedes a legal form in a name.
            prev = run[-1].lower().rstrip(".,") if run else ""
            if prev in _LEGAL_SUFFIX_TOKENS and len(run) == 1 and _NAME_TOKEN_RE.fullmatch(word):
                run.append(tok)
                continue
            break
        if _NAME_TOKEN_RE.fullmatch(word) or (low in _NAME_CONNECTORS and run):
            run.append(tok)
            continue
        break
    run.reverse()
    while run and run[0].lower() in _NAME_CONNECTORS:
        run.pop(0)
    return [" ".join(run[i:]) for i in range(len(run)) if run[i].lower() not in _NAME_CONNECTORS]


def _name_after(text: str, pos: int) -> Optional[str]:
    """The entity name starting at ``pos``: "Meta Platforms, Inc. and, unless …" →
    "Meta Platforms, Inc."."""
    tokens = text[pos:pos + 160].split()[:12]
    run: List[str] = []
    for i, tok in enumerate(tokens):
        word = tok.rstrip(",;:")
        if word.endswith(".") and "." not in word[:-1] and len(word) > 5:
            word = word[:-1]  # "Corporation." ends the sentence; "Inc." / "Corp." keep their dot
        low = word.lower().rstrip(".")
        nxt = tokens[i + 1].rstrip(",;:").lower().rstrip(".") if i + 1 < len(tokens) else ""
        if not (_NAME_TOKEN_RE.fullmatch(word) or (low in _NAME_CONNECTORS and run)):
            break
        if tok.endswith(","):
            if nxt in _LEGAL_SUFFIX_TOKENS:
                run.append(word + ",")
                continue
            run.append(word)
            break
        run.append(word)
        if tok.endswith((";", ":")) or (low in _LEGAL_SUFFIX_TOKENS and nxt not in _LEGAL_SUFFIX_TOKENS):
            break
    while run and run[-1].lower() in _NAME_CONNECTORS:
        run.pop()
    return " ".join(run) if run else None


def _skip_spaces(text: str, pos: int, most: int = 3) -> int:
    """Past at most ``most`` spaces at ``pos``."""
    end = min(len(text), pos + most)
    while pos < end and text[pos] == " ":
        pos += 1
    return pos


def _safe_space(text: str, i: int) -> bool:
    """A space a span may start or end at: not between two digit groups ("166 389 351")."""
    return text[i] == " " and not (text[i - 1:i].isdigit() and text[i + 1:i + 2].isdigit())


def _clause_chunks(text: str, s: int, e: int) -> List[Tuple[int, int]]:
    """A sentence longer than ``PEG_SPAN_MAX_CHARS`` cut into consecutive pieces of at most
    that length, each ending after a ", " outside parentheses when one is in reach (else on a
    safe space): "…with NVIDIA Corporation (“NVIDIA”), | and Coherent completed the issuance
    …". Linear in the sentence (≤ ``SENTENCE_MAX_CHARS``)."""
    out: List[Tuple[int, int]] = []
    a = s
    while e - a > PEG_SPAN_MAX_CHARS:
        limit = a + PEG_SPAN_MAX_CHARS
        depth, cut = 0, -1
        for i in range(a, limit):
            ch = text[i]
            if ch == "(":
                depth += 1
            elif ch == ")" and depth:
                depth -= 1
            elif ch == "," and depth == 0 and text[i + 1:i + 2] == " " and i + 1 - a >= PEG_SPAN_MIN_CHARS:
                cut = i + 1
        if cut == -1:
            cut = limit
            sp = text.rfind(" ", a + PEG_SPAN_MIN_CHARS, limit)
            while sp != -1 and not _safe_space(text, sp):
                sp = text.rfind(" ", a + PEG_SPAN_MIN_CHARS, sp)
            if sp != -1:
                cut = sp
        out.append((a, cut))
        a = cut
        while a < e and text[a] == " ":
            a += 1
    if a < e:
        if e - a < PEG_SPAN_MIN_CHARS:
            # A short tail: one window ending at the sentence end instead (it may overlap).
            start = max(s, e - 200)
            while start > s and not _safe_space(text, start - 1):
                start -= 1
            out.append((start, e))
        else:
            out.append((a, e))
    return out


def _fit_span(text: str, a: int, b: int, lo: int, hi: int) -> Tuple[int, int]:
    """A window of about 200 characters around the fact ``[a, b)`` inside ``[lo, hi)``,
    never longer than ``PEG_SPAN_MAX_CHARS``, its edges on safe spaces (``_safe_space``)."""
    b = min(max(b, a + 1), a + PEG_SPAN_MAX_CHARS)
    pad = max(0, 200 - (b - a)) // 2
    na, nb = max(lo, a - pad), min(hi, b + pad)
    while na > lo and not _safe_space(text, na - 1) and a - na < pad + 60:
        na -= 1
    while nb < hi and not _safe_space(text, nb) and nb - b < pad + 60:
        nb += 1
    if nb - na > PEG_SPAN_MAX_CHARS:
        nb = na + PEG_SPAN_MAX_CHARS
        while nb > b and not _safe_space(text, nb):
            nb -= 1
    return na, nb


@dataclass(frozen=True)
class _Mention:
    start: int
    end: int
    kind: str                  # "alias" | "term" | "group"
    label: str                 # the alias, the term or the group term as defined


@dataclass(frozen=True)
class _Company:
    display: Optional[str]     # clean display name of the document's own spelling
    matched: bool              # a defining clause names SEC's filer
    conflict: bool             # a "the Company" clause names another entity
    terms: FrozenSet[str]      # how the document refers to the filer ("Company", "Coherent", "we")


@dataclass(frozen=True)
class _SoldTo:
    """"The Shares were issued and sold to NVIDIA": the purchaser of a sale described near it,
    or — with its own count — a sale clause itself."""

    k: int
    verb: int
    clause_start: int
    kind: str
    mention: Optional[_Mention]


@dataclass(frozen=True)
class _Candidate:
    rank: int
    tier: str
    reason: str
    outcome: str
    detail: str
    sentences: Tuple[int, ...] = ()
    facts: Tuple[Tuple[int, int], ...] = ()
    instrument_label: Optional[str] = None
    amount: Optional[Decimal] = None
    value_basis: Optional[str] = None
    shares: Optional[int] = None
    price: Optional[Decimal] = None
    event_date: Optional[date] = None
    group_term: Optional[str] = None
    group_members: Tuple[str, ...] = ()
    needs_name: bool = False   # a proposal: the S2 name rule must pass
    anchor: int = -1           # the offset of the clause it was read from
    closed: bool = False       # the evidence reports a COMPLETED deal (TemplateResult.closed)

    def same_deal(self, other: "_Candidate") -> bool:
        """The same deal read twice: same class and instrument, and every fact equal or
        absent on one side."""
        if (self.tier, self.reason, self.outcome, self.instrument_label, self.group_term, self.value_basis,
                self.closed) != (other.tier, other.reason, other.outcome, other.instrument_label,
                                 other.group_term, other.value_basis, other.closed):
            return False
        return all(a is None or b is None or a == b for a, b in (
            (self.amount, other.amount), (self.shares, other.shares), (self.price, other.price),
            (self.event_date, other.event_date)))

    def merged(self, other: "_Candidate") -> "_Candidate":
        return replace(
            self, facts=self.facts + other.facts,
            sentences=tuple(sorted(set(self.sentences) | set(other.sentences))),
            amount=self.amount if self.amount is not None else other.amount,
            shares=self.shares if self.shares is not None else other.shares,
            price=self.price if self.price is not None else other.price,
            event_date=self.event_date if self.event_date is not None else other.event_date,
            group_members=self.group_members or other.group_members,
        )


def _near(rank: int, tier: str, reason: str, why: str, used: Set[int],
          facts: List[Tuple[int, int]], **kw: Any) -> _Candidate:
    kw.setdefault("anchor", facts[0][0] if facts else -1)
    return _Candidate(rank, tier, reason, OUTCOME_HEADS_UP, why, tuple(sorted(used)), tuple(facts), **kw)


def _deny(why: str, used: Set[int], facts: List[Tuple[int, int]], tier: str = TIER_OTHER,
          reason: str = "commercial_only") -> _Candidate:
    return _Candidate(_RANK_DENY, tier, reason, OUTCOME_SKIPPED, why, tuple(sorted(used)), tuple(facts),
                      anchor=facts[0][0] if facts else -1)


# ── Public entry point ───────────────────────────────────────────────────────────────────


def _alias_tuple(value: Any, what: str, *, allow_empty: bool) -> Tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, (list, tuple)):
        raise ValueError(f"{what} must be a list or tuple of names, got {type(value).__name__}")
    out: List[str] = []
    for alias in value:
        if not isinstance(alias, str) or not alias.strip() or len(alias) > 120 or not alias.isprintable():
            raise ValueError(f"{what}: {alias!r} is not a usable name")
        alias = " ".join(alias.split())
        if alias not in out:
            out.append(alias)
    if not out and not allow_empty:
        raise ValueError(f"{what} must name at least one alias")
    return tuple(out)


def classify_document(
    *,
    text: str,
    member_aliases: Sequence[str],
    filer_conformed_name: str,
    form: str,
    items: Sequence[str],
    filed_on: date,
    file_type: Optional[str],
    other_member_aliases: Sequence[str],
    cpu_budget_seconds: float = DEFAULT_CPU_BUDGET_SECONDS,
) -> TemplateResult:
    """Classify ONE document of another company for ONE member (see the module docstring).

    ``text`` is ``html_to_text(raw, max_chars=None)`` output (curly quotes kept). ``form`` is
    the EFTS hit's ROOT form ("8-K", "6-K", "424B5", "S-1", …), ``items`` its 8-K item list
    (a malformed item is ignored: it can never be "3.02"), ``file_type`` the hit's file type
    ("8-K", "EX-99.1", "EX-10.1", "EX-4.1", …), ``filer_conformed_name`` SEC's name of the
    filer ("INTEL CORP") and ``other_member_aliases`` every OTHER member's aliases (a group's
    members, a second purchaser).

    Deterministic for a given input and bounded: past ``cpu_budget_seconds`` of
    ``time.process_time`` it returns a heads-up (``no_template``, detail "cpu budget").

    Raises:
        ValueError: an argument of the wrong type or shape — a caller's bug, never a
            document's: a non-string text, no member alias, a bare string where a list of
            aliases or items belongs, a datetime for ``filed_on``, a budget that is not a
            positive finite number of seconds (at most ``MAX_CPU_BUDGET_SECONDS``).
    """
    if not isinstance(text, str):
        raise ValueError(f"text must be a str, got {type(text).__name__}")
    aliases = _alias_tuple(member_aliases, "member_aliases", allow_empty=False)
    others = tuple(a for a in _alias_tuple(other_member_aliases, "other_member_aliases", allow_empty=True)
                   if a not in aliases)
    if not isinstance(filer_conformed_name, str):
        raise ValueError("filer_conformed_name must be a str")
    if not isinstance(form, str):
        raise ValueError("form must be a str (the hit's root form)")
    if file_type is not None and not isinstance(file_type, str):
        raise ValueError("file_type must be a str or None")
    if isinstance(filed_on, datetime) or not isinstance(filed_on, date):
        raise ValueError("filed_on must be a date (not a datetime)")
    if isinstance(items, (str, bytes)) or not isinstance(items, (list, tuple)):
        raise ValueError("items must be a list or tuple of 8-K item codes")
    if (isinstance(cpu_budget_seconds, bool) or not isinstance(cpu_budget_seconds, (int, float))
            or not math.isfinite(cpu_budget_seconds) or not 0 < cpu_budget_seconds <= MAX_CPU_BUDGET_SECONDS):
        raise ValueError(f"cpu_budget_seconds must be in (0, {MAX_CPU_BUDGET_SECONDS}]")
    item_set = frozenset(i.strip() for i in items if isinstance(i, str) and _ITEM_RE.fullmatch(i.strip()))
    budget = _Budget(float(cpu_budget_seconds))
    try:
        return _Classifier(
            text=text, aliases=aliases, others=others, conformed=filer_conformed_name,
            form=form.strip().upper(), items=item_set, filed_on=filed_on,
            file_type=(file_type or "").strip().upper(), budget=budget,
        ).run()
    except _OverBudget:
        logger.warning(
            "[tc-stakes-template-budget] %s %s %s: over the %.1f s CPU budget on %d chars — "
            "a heads-up, never a guess", filer_conformed_name[:80], form, file_type,
            cpu_budget_seconds, len(text),
        )
        return _plain(TIER_OTHER, "no_template", OUTCOME_HEADS_UP, "cpu budget")


def _plain(tier: str, reason: str, outcome: str, detail: str,
           company_name: Optional[str] = None) -> TemplateResult:
    return TemplateResult(
        tier=tier, reason=reason, outcome=outcome, company_name=company_name,
        instrument_label=None, amount_usd=None, value_basis=None, shares=None,
        price_per_share=None, event_date=None, spans=(), group_term=None, group_members=(),
        detail=_clip(detail),
    )


# ── The classifier ───────────────────────────────────────────────────────────────────────


class _Classifier:
    def __init__(
        self, *, text: str, aliases: Tuple[str, ...], others: Tuple[str, ...], conformed: str,
        form: str, items: FrozenSet[str], filed_on: date, file_type: str, budget: _Budget,
    ) -> None:
        self.text = text
        self.aliases = aliases
        self.others = others
        self.conformed = conformed
        self.form = form
        self.items = items
        self.filed_on = filed_on
        self.file_type = file_type
        self.budget = budget
        self.mentions: List[_Mention] = []
        self._mention_starts: List[int] = []
        self.alias_terms: Set[str] = set()
        self.groups: Dict[str, Tuple[str, ...]] = {}       # group term → club aliases in it
        self.company = _Company(None, False, False, frozenset())
        self.starts: List[int] = []                        # sentence start offsets
        self._norm: Optional[str] = None
        self._consumed: Set[int] = set()                   # verbs of _SoldTo used as purchasers
        self._considered: Set[int] = set()                 # verbs of _SoldTo a sale restated

    @property
    def _closed_tier(self) -> Tuple[str, str]:
        return (TIER_DIRECTED, "t4_directed") if self.form == "6-K" else (TIER_SEC_CLOSING, "t1_closing")

    # ── driver ──

    def run(self) -> TemplateResult:
        if not self.text.strip():
            return _plain(TIER_OTHER, "no_template", OUTCOME_SKIPPED, "empty document")
        member_keys = {_company_key(a) for a in self.aliases} - {""}
        if _company_key(self.conformed) in member_keys:
            # The member's own filing: what it says about a stake is the owner's to read.
            return _plain(TIER_OTHER, "member_filed", OUTCOME_HEADS_UP, "the member's own filing names a deal")
        alias_mentions = self._find_aliases()
        if not alias_mentions:
            return _plain(TIER_OTHER, "no_template", OUTCOME_SKIPPED, "the member's name is not in the text")
        self.budget.check()
        self.company = self._find_company()
        self.budget.check()
        if not self._collect_terms(alias_mentions):
            return _plain(TIER_OTHER, "no_template", OUTCOME_HEADS_UP,
                          f"too many defined terms for the member (more than {MAX_ALIAS_TERMS} names or "
                          f"{MAX_GROUP_TERMS} groups) — not read automatically", self.company.display)
        self.budget.check()
        self.starts = self._segment()
        if self.file_type.startswith("EX-4"):
            return self._finish(self._ex4_candidates(), fallback_reason="exhibit_skipped",
                                fallback_detail="an EX-4 exhibit the member is not a party to")
        anchors = sorted({self._sentence_of(m.start) for m in self.mentions})
        deal: List[int] = []
        for i, k in enumerate(anchors):
            if i % 256 == 0:
                self.budget.check()
            if self._near_deal_words(k):
                deal.append(k)
        if len(deal) > MAX_DEAL_SENTENCES:
            return _plain(TIER_OTHER, "no_template", OUTCOME_HEADS_UP,
                          f"{len(deal)} sentences name the member near deal wording — too many to "
                          "read automatically", self.company.display)
        try:
            candidates = self._candidates(deal)
        except _TooManyDeals:
            logger.warning(
                "[tc-stakes-template-too-many] %s %s %s: more than %d deal clauses name the member in "
                "%d chars — a heads-up, never a guess", self.conformed[:80], self.form, self.file_type,
                MAX_DEAL_CANDIDATES, len(self.text),
            )
            return _plain(TIER_OTHER, "no_template", OUTCOME_HEADS_UP,
                          f"more than {MAX_DEAL_CANDIDATES} deal clauses name the member — too many deal "
                          "clauses to read automatically", self.company.display)
        if candidates:
            return self._finish(candidates)
        return self._fallback(anchors)

    # ── mentions and defined terms ──

    def _find_aliases(self) -> List[_Mention]:
        found: List[_Mention] = []
        for alias in self.aliases:
            self.budget.check()
            for m in _alias_regex(alias).finditer(self.text):
                found.append(_Mention(m.start(), m.end(), "alias", alias))
                if len(found) >= MAX_MENTIONS:
                    break
        found.sort(key=lambda x: (x.start, -x.end))
        return found

    def _collect_terms(self, alias_mentions: List[_Mention]) -> bool:
        """The member's defined terms ("NVIDIA", "Initial Warrantholder") and the groups that
        list it ("Cornerstone Investors"), then every mention of the three. False when the
        document defines more than ``MAX_ALIAS_TERMS`` names or ``MAX_GROUP_TERMS`` groups for
        the member — each costs a whole-text scan, so a crafted filing with thousands of
        quoted terms is a heads-up, never minutes of CPU. The budget is checked per mention,
        per term and every 256 matches."""
        text = self.text
        for i, m in enumerate(alias_mentions[:200]):
            if i % 16 == 0:
                self.budget.check()
            listed = self._group_after(m)  # first: its member list is the precise one
            if listed is not None:
                self.groups.setdefault(*listed)
            body = self._definition_after(m.end)
            if body is not None:
                own, group = _split_definition(body)
                self.alias_terms.update(_terms_in(own, m.label))
                if group:
                    self.groups.setdefault(group, self._members_in(text[max(0, m.start - 300):m.end + 400]))
            if len(self.alias_terms) > MAX_ALIAS_TERMS or len(self.groups) > MAX_GROUP_TERMS:
                return False
        # A group defined inside a LATER party's parenthetical: (“Broadcom” and, together with
        # NVIDIA and AMD, the “Cornerstone Investors”).
        for i, m in enumerate(alias_mentions[:200]):
            if i % 16 == 0:
                self.budget.check()
            for pm in _PAREN_BODY_RE.finditer(text, m.end, min(len(text), m.end + 400)):
                _own, group = _split_definition(pm.group(1))
                if group and self._names_member(pm.group(1)):
                    self.groups.setdefault(group, self._members_in(text[max(0, m.start - 300):pm.end()]))
                    break
            if len(self.groups) > MAX_GROUP_TERMS:
                return False
        self.alias_terms -= set(self.groups)
        mentions = list(alias_mentions)
        for kind, terms in (("term", sorted(self.alias_terms)), ("group", sorted(self.groups))):
            for term in terms:
                self.budget.check()
                for n, tm in enumerate(_term_regex(term).finditer(text)):
                    if n % 256 == 255:
                        self.budget.check()
                    if len(mentions) >= MAX_MENTIONS:
                        break
                    mentions.append(_Mention(tm.start(), tm.end(), kind, term))
        mentions.sort(key=lambda x: (x.start, -x.end))
        self.mentions = mentions[:MAX_MENTIONS]
        self._mention_starts = [m.start for m in self.mentions]
        return True

    def _names_member(self, segment: str) -> bool:
        return any(_alias_regex(a).search(segment) for a in self.aliases) or any(
            _term_regex(t).search(segment) for t in self.alias_terms)

    def _definition_after(self, pos: int) -> Optional[str]:
        """The parenthetical that defines the name ending at ``pos`` (an appositive such as
        ", a Delaware corporation" may sit between them), or None."""
        after = self._after_appositive(pos)
        if after < len(self.text) and self.text[after] == "(":
            pm = _PAREN_BODY_RE.match(self.text, after)
            return pm.group(1) if pm else None
        return None

    def _after_appositive(self, pos: int) -> int:
        text = self.text
        i = pos
        while i < len(text) and i < pos + 3 and text[i] == " ":
            i += 1
        if i < len(text) and text[i] == ",":
            ap = _ALIAS_APPOSITIVE_RE.match(text, i)
            if ap is None:
                return pos
            i = ap.end()
            if i < len(text) and text[i] == ",":
                i += 1
            stop = i + 3
            while i < len(text) and i < stop and text[i] == " ":
                i += 1
        return i

    def _after_name(self, pos: int) -> int:
        """Past the name's definition parenthetical (and the appositive before it), else
        ``pos``. "NVIDIA Corporation (“NVIDIA”), for …" → the offset of ", for …"."""
        i = self._after_appositive(pos)
        if i < len(self.text) and self.text[i] == "(":
            pm = _PAREN_BODY_RE.match(self.text, i)
            if pm:
                return pm.end()
        return pos

    def _group_after(self, m: _Mention) -> Optional[Tuple[str, Tuple[str, ...]]]:
        """``("Cornerstone Investors", (aliases…))`` when the alias is LISTED before a
        "(collectively, the “X”)" parenthetical of its sentence, else None."""
        text = self.text
        gm = _GROUP_PAREN_RE.search(text, m.end, min(len(text), m.end + 300))
        if gm is None or not _is_list_gap(text[m.end:gm.start()]):
            return None
        terms = [t for t in _QUOTED_TERM_RE.findall(gm.group(0)) if t.lower() not in _GENERIC_TERMS]
        if not terms:
            return None
        lo = max(0, m.start - 400)
        head = text[lo:m.start]
        cut = max(head.rfind(". "), head.rfind("; "), head.rfind(": "))
        return terms[-1], self._members_in(text[lo + cut + 1 if cut >= 0 else lo:gm.start()])

    def _members_in(self, segment: str) -> Tuple[str, ...]:
        found: List[Tuple[int, str]] = []
        for alias in self.aliases + self.others:
            hit = _alias_regex(alias).search(segment)
            if hit:
                found.append((hit.start(), alias))
        return tuple(a for _, a in sorted(found))

    def _mentions_in(self, lo: int, hi: int) -> List[_Mention]:
        i = bisect.bisect_left(self._mention_starts, lo)
        out: List[_Mention] = []
        while i < len(self.mentions) and self.mentions[i].start < hi:
            if self.mentions[i].end <= hi:
                out.append(self.mentions[i])
            i += 1
        return out

    # ── the filer's name ──

    def _find_company(self) -> _Company:
        """The filer as the document defines it (module docstring, "The investee's name")."""
        text = self.text
        key = _company_key(self.conformed)
        if not key:
            return _Company(None, False, False, frozenset())
        accepted: List[Tuple[str, FrozenSet[str]]] = []
        conflict = False
        defs: List[Tuple[int, List[str]]] = []
        for i, m in enumerate(_COMPANY_GENERIC_DEF_RE.finditer(text)):
            if i >= 30:
                break
            defs.append((m.start(), [m.group("term")]))
        for i, m in enumerate(_COMPANY_OR_DEF_RE.finditer(text)):
            if i >= 30:
                break
            defs.append((m.start(), [m.group("short"), m.group("term")]))
        doc_terms: Set[str] = set()
        for start, terms in defs:
            doc_terms.update(terms)
            names = _names_before(text, start)
            hit = next((n for n in names if _company_key(n) == key), None)
            if hit is not None:
                accepted.append((hit, frozenset(terms) | {hit}))
            elif names:
                conflict = True
        for i, m in enumerate(_SHORT_DEF_RE.finditer(text)):
            if i >= 300 or len(accepted) >= 8:
                break
            hit = next((n for n in _names_before(text, m.start()) if _company_key(n) == key), None)
            if hit is not None:
                accepted.append((hit, frozenset({m.group("short"), hit})))
        for i, m in enumerate(_REFER_TO_RE.finditer(text)):
            if i >= 30:
                break
            left = text[max(0, m.start() - 160):m.start() + 1]
            if not _WE_TERM_RE.search(left):
                continue
            name = _name_after(text, m.end())
            doc_terms.update(_QUOTED_TERM_RE.findall(left))
            doc_terms.update({"we", "us", "our"})
            if name and _company_key(name) == key:
                accepted.append((name, frozenset(_QUOTED_TERM_RE.findall(left)) | {"we", "us", "our", name}))
            elif name:
                conflict = True
        clean_terms = frozenset(t.strip(" ,") for t in doc_terms if t.strip(" ,"))
        if not accepted or conflict:
            # Two different entities called "the Company", or none that is SEC's filer: the
            # name cannot be confirmed — fail closed (a proposal becomes a heads-up). The
            # document's own terms still say which stock is "the Company's".
            return _Company(None, False, conflict, clean_terms)
        display = next((d for d in (clean_investee_name(n) for n, _t in accepted) if d), None)
        terms: Set[str] = set(clean_terms)
        for _name, t in accepted:
            terms |= t
        return _Company(display, True, conflict, frozenset(t.strip(" ,") for t in terms if t.strip(" ,")))

    # ── sentences ──

    def _segment(self) -> List[int]:
        """Sentence start offsets over the WHOLE text: a terminator before an upper-case
        letter, a digit or an opening quote / bracket, unless the token before it is an
        abbreviation ("Inc.", "U.S.", "No."); a run with no boundary is force-split at
        ``SENTENCE_MAX_CHARS`` (``_force_cut``)."""
        text, n = self.text, len(self.text)
        raw = [0]
        for i, m in enumerate(_TERMINATOR_RE.finditer(text)):
            if i % 2048 == 0:
                self.budget.check()
            j, limit = m.end(), min(n, m.end() + 8)
            while j < limit and text[j].isspace():
                j += 1
            if j >= n:
                break
            nxt = text[j]
            if not (nxt.isupper() or nxt.isdigit() or nxt in _OPENERS):
                continue
            if text[m.start()] == "." and _is_abbreviation(text, m.start()):
                continue
            if j > raw[-1]:
                raw.append(j)
        out: List[int] = []
        for k, s in enumerate(raw):
            e = raw[k + 1] if k + 1 < len(raw) else n
            while e - s > SENTENCE_MAX_CHARS:
                out.append(s)
                s = _force_cut(text, s)
            out.append(s)
        return out

    def _sentence_of(self, pos: int) -> int:
        return max(0, bisect.bisect_right(self.starts, pos) - 1)

    def _bounds(self, k: int) -> Tuple[int, int]:
        k = max(0, min(k, len(self.starts) - 1))
        s = self.starts[k]
        e = self.starts[k + 1] if k + 1 < len(self.starts) else len(self.text)
        while e > s and self.text[e - 1].isspace():
            e -= 1
        return s, e

    def _near_deal_words(self, k: int) -> bool:
        lo = self._bounds(k - 1)[0] if k > 0 else self._bounds(k)[0]
        hi = self._bounds(k + 1)[1]
        window = self.text[lo:hi].lower()
        return any(w in window for w in _DEAL_WORDS)

    # ── who: the purchaser and the subject ──

    def _skip_filler(self, pos: int) -> int:
        """Past up to three spaces, ONE parenthetical (a definition) and ONE comma — the filler
        between a name and what the sentence says about it. Bounded: a few hundred characters."""
        text = self.text
        i = _skip_spaces(text, pos)
        if i < len(text) and text[i] == "(":
            pm = _PAREN_BODY_RE.match(text, i)
            if pm is not None:
                i = _skip_spaces(text, pm.end())
        if i < len(text) and text[i] == ",":
            i = _skip_spaces(text, i + 1)
        return i

    def _denied(self, m: _Mention) -> bool:
        if _DENY_POSSESSIVE_RE.match(self.text, m.end) or _DENY_AFTER_RE.match(self.text, self._skip_filler(m.end)):
            return True
        return bool(_DENY_BEFORE_RE.search(self.text[max(0, m.start - 12):m.start]))

    def _several_follows(self, pos: int) -> bool:
        """"NVIDIA Corporation and certain other investors", "…, together with …", or a list
        ("NVIDIA Corporation, Microsoft Corporation and …") right after the name at ``pos``."""
        text = self.text
        i = _skip_spaces(text, pos)
        if i < len(text) and text[i] == ",":
            j = _skip_spaces(text, i + 1)
            if j > i + 1 and j < len(text) and text[j].isupper():
                return True  # ", Microsoft Corporation …": a list of names
            i = j
        return bool(_SEVERAL_AFTER_RE.match(text, i))

    def _purchaser(self, start: int, hi: int) -> Tuple[str, Optional[_Mention]]:
        """Who a sale went to, read at ``text[start:]``: ``("exact", m)`` when the phrase is
        the member alone, ``("several", m)`` when it lists others too, ``("related", m)`` for
        "an affiliate of NVIDIA" / "NVIDIA’s subsidiary", ``("denied", m)`` when the mention is
        bound to a deny term, ``("other", None)`` when the member is not the purchaser."""
        text = self.text
        # The member must START inside the purchaser phrase ("to Acme Corp. for $X, which
        # NVIDIA Corporation funded" names Acme, not NVIDIA).
        end = _phrase_end(text, start, min(hi, start + 220), _PURCHASER_STOPS)
        end = _clause_cut(text, start, end)
        member = [m for m in self._mentions_in(start, min(hi, start + 220)) if m.start < max(end, start + 1)]
        if not member:
            return "other", None
        m = member[0]
        if m.kind == "group":
            return "several", m  # "to the Purchasers": a defined group that lists the member
        pre = text[start:m.start]
        if self._denied(m):
            return "denied", m
        pre_l = pre.strip().lower()
        if pre_l not in ("", "the"):
            if len(pre_l) > 80 or _phrase_end(text, start, m.start, (". ", "; ")) < m.start:
                return "other", None
            if _SEVERAL_BEFORE_RE.search(pre_l) or any(_alias_regex(a).search(pre) for a in self.others):
                return "several", m
            return "related", m
        after = self._after_name(m.end)
        if _VERB_AFTER_RE.match(text, _skip_spaces(text, after)):
            return "other", None  # "to Acme, and NVIDIA Corporation is a customer"
        if self._several_follows(after):
            return "several", m
        if after == m.end and (_POSSESSIVE_AFTER_RE.match(text, after) or _LONGER_NAME_RE.match(text, after)):
            return "related", m  # "NVIDIA Corporation’s subsidiary", "the NVIDIA Corporation 401(k) Plan"
        return "exact", m

    def _subject_before(self, s: int, verb: int) -> Optional[Tuple[str, Optional[_Mention]]]:
        """Who did the buying / agreeing: ``(kind, mention)`` as in ``_purchaser`` when the
        member ends the subject before ``verb`` ("Pursuant to the Purchase Agreement, NVIDIA
        purchased"), ``("several", m)`` when it is listed with others, else None."""
        text = self.text
        lo = max(s, verb - 200)
        left = text[lo:verb].rstrip()
        for word in ("has", "have", "also", "each", "previously", "subsequently", "hereby"):
            if left.endswith(" " + word):
                left = left[: -len(word) - 1].rstrip()
        if left.endswith(")"):
            depth, i = 0, len(left) - 1
            while i >= 0:
                if left[i] == ")":
                    depth += 1
                elif left[i] == "(":
                    depth -= 1
                    if depth == 0:
                        break
                i -= 1
            if i > 0:
                left = left[:i].rstrip()
        end_abs = lo + len(left)
        members = self._mentions_in(lo, end_abs + 1)
        if not members:
            return None
        # The mention that reaches furthest: "NVIDIA Corporation", not its nested term "NVIDIA".
        last = max(members, key=lambda m: (m.end, m.end - m.start))
        if last.kind == "group":
            return ("several", last) if last.end == end_abs else None  # "the Purchasers purchased …"
        if last.end != end_abs:
            # "NVIDIA and Microsoft Corporation agreed": the member listed with another buyer.
            after = self._after_name(last.end)
            tail = text[after:end_abs]
            if len(tail) < 120 and ". " not in tail and "; " not in tail and self._several_follows(after):
                return "several", last
            return None
        if self._denied(last):
            return "denied", last
        words = text[max(s, last.start - 40):last.start].split()
        word = words[-1].lower().strip(",") if words else ""
        if word in {"and", "or", "&", "with", "plus", "nor"}:
            return "several", last
        if word in {"from", "by", "of", "to", "for"}:
            return None
        if any(_alias_regex(a).search(text[lo:last.start]) for a in self.others):
            return "several", last
        return "exact", last

    def _not_done(self, s: int, verb: int) -> bool:
        """Future, conditional or agreed-only wording before the verb, or a negation bound to it
        ("has not yet completed", "hasn’t completed")."""
        return bool(_NOT_DONE_RE.search(self.text, s, verb)
                    or _NEGATED_BEFORE_RE.search(self.text, max(s, verb - 40), verb))

    # ── candidates ──

    def _candidates(self, deal: List[int]) -> List[_Candidate]:
        n = len(self.starts)
        near = sorted({j for k in deal for j in range(max(0, k - 2), min(n, k + 3))})
        links = self._sold_to(near)
        out: List[_Candidate] = []
        seen: Set[Tuple[str, int]] = set()
        for k in near:
            self.budget.check()
            out += self._sale_candidates(k, links, seen)
            out += self._agreement_candidates(k, seen)
            out += self._ioi_candidates(k, seen)
            out += self._directed_candidates(k, seen)
            if len(out) > MAX_DEAL_CANDIDATES:
                raise _TooManyDeals()
        out += self._sold_to_candidates(links, len(out))
        return out

    def _sold_to(self, near: List[int]) -> Dict[int, List[_SoldTo]]:
        text = self.text
        links: Dict[int, List[_SoldTo]] = {}
        for i, k in enumerate(near):
            if i % 64 == 0:
                self.budget.check()
            s, e = self._bounds(k)
            for m in _SOLD_TO_RE.finditer(text, s, e):
                semi = text.rfind("; ", s, m.start())
                clause_start = semi + 2 if semi != -1 else s
                if self._not_done(clause_start, m.start()):
                    continue
                if not _SOLD_SUBJECT_RE.search(text, clause_start, m.start()):
                    continue
                kind, mention = self._purchaser(m.end(), e)
                if kind != "other":
                    links.setdefault(k, []).append(_SoldTo(k, m.start(), clause_start, kind, mention))
        return links

    # sale clauses -----------------------------------------------------------------------

    def _sale_candidates(self, k: int, links: Dict[int, List[_SoldTo]], seen: Set[Tuple[str, int]]) -> List[_Candidate]:
        text = self.text
        s, e = self._bounds(k)
        out: List[_Candidate] = []
        for m in _SALE_VERB_RE.finditer(text, s, e):
            if ("sale", m.start()) not in seen:
                seen.add(("sale", m.start()))
                full = _SALE_VERB_RE.match(text, m.start())
                cand = self._sale_clause(k, s, e, m.start(), full.end() if full else m.end(), links,
                                         company_side=True)
                if cand is not None:
                    out.append(cand)
        for m in _PURCHASED_RE.finditer(text, s, e):
            if ("purchased", m.start()) not in seen:
                seen.add(("purchased", m.start()))
                subject = self._subject_before(s, m.start())
                if subject is not None:
                    cand = self._sale_clause(k, s, e, m.start(), m.end(), links, company_side=False,
                                             subject=subject)
                    if cand is not None:
                        out.append(cand)
        for m in _ISSUED_WARRANT_RE.finditer(text, s, e):
            if ("warrant", m.start()) not in seen:
                seen.add(("warrant", m.start()))
                cand = self._warrant_issued(k, s, e, m.start(), m.end())
                if cand is not None:
                    out.append(cand)
        for m in _ISSUED_TO_RE.finditer(text, s, e):
            if ("issued_to", m.start()) not in seen:
                seen.add(("issued_to", m.start()))
                cand = self._issued_to(k, s, e, m.start(), m.end())
                if cand is not None:
                    out.append(cand)
        return out

    def _sale_clause(
        self, k: int, s: int, e: int, verb: int, obj_start: int, links: Dict[int, List[_SoldTo]],
        *, company_side: bool, subject: Optional[Tuple[str, Optional[_Mention]]] = None,
    ) -> Optional[_Candidate]:
        """"completed the issuance and sale of <N> shares of <noun> [to <member>]" or
        "<member> purchased <N> shares / $X of <noun>"."""
        text = self.text
        obj_end = _phrase_end(text, obj_start, min(e, obj_start + 260), _OBJECT_STOPS)
        obj = text[obj_start:obj_end]
        shares: Optional[int] = None
        noun_start = obj_start
        direct: Optional[_Money] = None
        cm = _SHARE_COUNT_RE.match(text, obj_start)
        if cm is not None:
            shares = _share_int(cm.group("n"))
            of = _OF_RE.match(text, cm.end())
            noun_start = of.end() if of else cm.end()
        elif not company_side:
            money = _parse_money(text, obj_start)
            if not _usd(money):
                return None
            direct = money
            of = _OF_RE.match(text, money.end)
            noun_start = of.end() if of else money.end
        noun_end = _phrase_end(text, noun_start, min(e, noun_start + 220), _NOUN_STOPS)
        noun = text[noun_start:noun_end]
        link: Optional[_SoldTo] = None
        if subject is not None:
            p_kind, p_mention = subject
        else:
            p_kind, p_mention = "other", None
            if text.startswith(" to ", obj_end):
                p_kind, p_mention = self._purchaser(obj_end + 4, e)
            else:
                link = self._link_for(k, links, obj, noun)
                if link is not None:
                    p_kind, p_mention = link.kind, link.mention
        if p_kind == "other":
            return None
        # Every link that restates THIS sale's purchaser — the same securities, the same kind of
        # purchaser: "The Shares were sold to NVIDIA at a price of $50.00 per share, for …" after
        # "… sale of … (the “Shares”) to NVIDIA …" — is found BEFORE the sale is assessed, so the
        # deal window reads its price as this sale's (never "another deal's").
        restating = frozenset(other.verb for other in self._links_near(k, links)
                              if other.kind == p_kind and self._link_names_sale(other, obj, noun))
        exempt = restating | (frozenset({link.verb}) if link is not None else frozenset())
        cand = self._assess_sale(k, s, verb, p_kind, p_mention, obj, noun, shares, direct,
                                 company_side=company_side, facts_end=max(noun_end, obj_end), exempt=exempt)
        if cand is not None:
            # This sale accounts for its own link and for every link that restates its
            # purchaser ("The shares of Series A Preferred Stock were issued and sold to NVIDIA"
            # after "… sale of … Series A Preferred Stock … to NVIDIA"). A link no sale used or
            # restated stays a near miss in ``_sold_to_candidates`` — never dropped.
            if link is not None:
                self._consumed.add(link.verb)
            self._considered.update(restating)
        return cand

    def _links_near(self, k: int, links: Dict[int, List[_SoldTo]]) -> List[_SoldTo]:
        """The "were issued and sold to <member>" clauses that may name a sale's purchaser: the
        next sentence first, then this one, the one after, the one before — skipping one with
        a count of its own (a sale clause, read by ``_sold_to_candidates``)."""
        out: List[_SoldTo] = []
        for j in (k + 1, k, k + 2, k - 1):
            for link in links.get(j, ()):
                if not _SHARE_COUNT_RE.search(self.text, link.clause_start, link.verb):
                    out.append(link)
        return out

    def _link_for(self, k: int, links: Dict[int, List[_SoldTo]], obj: str, noun: str) -> Optional[_SoldTo]:
        """The "were issued and sold to <member>" clause that names this sale's purchaser
        (``_links_near`` order), when it speaks of the SAME securities (``_link_names_sale``)."""
        for link in self._links_near(k, links):
            if self._link_names_sale(link, obj, noun):
                return link
        return None

    def _link_names_sale(self, link: _SoldTo, obj: str, noun: str) -> bool:
        """A link's subject is this sale's securities: a term the sale DEFINED ("The Shares",
        "The shares of Series A Preferred Stock"), case-sensitive and with nothing else named
        beside it — or the same kind of security, label and class letter ("The shares" for a
        common-stock sale). "Shares of the Company’s Series A Preferred Stock were issued and
        sold to NVIDIA" never names the buyer of a COMMON sale; "The Warrants …" never the
        buyer of a share sale."""
        subject = self.text[link.clause_start:link.verb]
        for term in _QUOTED_TERM_RE.findall(obj):
            rx = _term_regex(term)
            if rx.search(subject):
                rest = rx.sub(" ", subject)
                if _noun_kind(rest)[0] in ("plain", "unknown") and not _CLASS_LETTER_RE.search(rest):
                    return True
        if not noun.strip():
            return _noun_kind(subject) == ("plain", None) and not _CLASS_LETTER_RE.search(subject)
        if _noun_kind(subject) != _noun_kind(noun):
            return False
        a, b = _CLASS_LETTER_RE.search(subject), _CLASS_LETTER_RE.search(noun)
        return a is None or b is None or a.group(1).lower() == b.group(1).lower()

    def _assess_sale(
        self, k: int, s: int, verb: int, p_kind: str, p_mention: Optional[_Mention], obj: str,
        noun: str, shares: Optional[int], direct: Optional[_Money], *, company_side: bool,
        facts_end: int, exempt: FrozenSet[int] = frozenset(),
    ) -> Optional[_Candidate]:
        """``exempt``: the verbs of the "were issued and sold to <member>" links that restate THIS
        sale (the one it took its purchaser from included) — the deal window may read their price."""
        closed_tier, reason = self._closed_tier
        used = {k}
        facts: List[Tuple[int, int]] = [(verb, facts_end)]
        if p_mention is not None:
            used.add(self._sentence_of(p_mention.start))
            facts.append((p_mention.start, p_mention.end))
        if p_kind == "denied":
            return _deny("the member is a sale's agent or a commercial counterparty, not a buyer", used, facts)
        if _DIVESTITURE_RE.search(obj) or (self.form == "8-K" and "2.01" in self.items and "3.02" not in self.items):
            return _deny("divestiture: a subsidiary, business or assets sold to the member", used, facts,
                         tier=closed_tier, reason="no_template")
        if p_kind in ("several", "related"):
            return _near(_RANK_CLOSED_NEAR, closed_tier, reason,
                         "sold to several purchasers" if p_kind == "several"
                         else "the purchaser is not exactly the member", used, facts, shares=shares,
                         closed=True)
        noun_kind, label = _noun_kind(noun)
        if noun_kind == "unknown" and not _SECURITY_WORD_RE.search(obj):
            return None  # "NVIDIA purchased $50 million of the Company's products": not a stake
        if shares is None and label in (INSTRUMENT_WARRANT, INSTRUMENT_PRE_FUNDED_WARRANT):
            tp = _TO_PURCHASE_SHARES_RE.search(noun)  # "warrants to purchase 1,000,000 shares"
            cm = _SHARE_COUNT_RE.match(noun, tp.end()) if tp is not None else None
            shares = _share_int(cm.group("n")) if cm is not None else None
        not_closed = self._not_done(s, verb)
        if p_mention is not None and self._sentence_of(p_mention.start) != k:
            ps = self._bounds(self._sentence_of(p_mention.start))[0]
            not_closed = not_closed or self._not_done(ps, p_mention.start)
        if not_closed:
            return _near(_RANK_SIGNED_NEAR, TIER_SEC_AGREEMENT, "t2_agreement",
                         "a sale to the member that had not closed when filed", used, facts, shares=shares)
        if noun_kind == "plain" and not _owns_noun(noun, self.company.terms, company_side=company_side):
            return _near(_RANK_CLOSED_NEAR, closed_tier, reason,
                         "the shares sold are not clearly the company's own stock", used, facts, shares=shares,
                         closed=True)
        if noun_kind == "instrument" and _foreign_owner(noun, self.company.terms):
            return _near(_RANK_CLOSED_NEAR, TIER_INSTRUMENT, "instrument",
                         "the securities sold are not clearly the company's own", used, facts, shares=shares,
                         closed=True)
        if noun_kind == "unnamed":
            return _near(_RANK_CLOSED_NEAR, TIER_INSTRUMENT, "instrument",
                         "an instrument outside the named vocabulary", used, facts, shares=shares, closed=True)
        if noun_kind == "class_other":
            return _near(_RANK_CLOSED_NEAR, closed_tier, reason,
                         "a share class other than common, ordinary or Class A", used, facts, shares=shares,
                         closed=True)
        if noun_kind == "unknown":
            return _near(_RANK_CLOSED_NEAR, closed_tier, reason,
                         "the security sold could not be read", used, facts, shares=shares, closed=True)
        return self._closing(k, s, verb, used, facts, shares, label, direct, exempt=exempt,
                             kind=(noun_kind, label))

    def _closing(
        self, k: int, s: int, verb: int, used: Set[int], facts: List[Tuple[int, int]],
        shares: Optional[int], label: Optional[str], direct: Optional[_Money],
        what: str = "completed sale", *, exempt: FrozenSet[int] = frozenset(),
        kind: Optional[Tuple[str, Optional[str]]] = None,
    ) -> _Candidate:
        """Date, amount and per-share price of a completed sale to the member alone. The price
        must be BOUND to the member's own clause (``_price_unbound``); the instrument tier, like
        T1, needs an 8-K with Item 3.02 (a 6-K is the foreign issuer's report). ``kind`` is the
        sold noun's ``_noun_kind`` (the exempt clauses of the next sentence must speak of it)."""
        closed_tier, closed_reason = self._closed_tier
        tier = TIER_INSTRUMENT if label else closed_tier
        reason = "instrument" if label else closed_reason
        when, when_k, when_span = self._lead_date(k, s, verb)

        def near(why: str, why_reason: str = reason, closed: bool = True) -> _Candidate:
            return _near(_RANK_CLOSED_NEAR, tier, why_reason, why, used, facts,
                         instrument_label=label, shares=shares, event_date=when, anchor=verb, closed=closed)

        if when_span is not None:
            facts.append(when_span)
            used.add(when_k)
        lo, hi, stop = self._deal_window(k, exempt, kind or (("instrument", label) if label else ("plain", None)))
        amounts, foreign = self._aggregate_amounts(lo, hi)
        if direct is not None:
            amounts.append(direct)
        if foreign:
            return near("the purchase price is not in U.S. dollars")
        if self._unreadable_aggregate(lo, hi):
            return near("the purchase price could not be read")
        if len({a.value for a in amounts}) > 1:
            return near("several different purchase prices")
        if amounts and direct is None:
            why = self._price_unbound(verb, lo, hi, shares, exempt, amounts[0])
            if why is not None:
                return near(f"price not bound to the member: {why}")
        amount = amounts[0].value if amounts else None
        if amounts:
            facts.append((amounts[0].start, amounts[0].end))
            used.add(self._sentence_of(amounts[0].start))
        prices = self._share_prices(lo, hi)
        price = prices[0].value if len({p.value for p in prices}) == 1 else None
        if price is not None:
            facts.append((prices[0].start, prices[0].end))
            used.add(self._sentence_of(prices[0].start))
        if shares is not None and price is not None and amount is not None and (
                abs(Decimal(shares) * price - amount) > amount * SHARES_PRICE_TOLERANCE):
            return near("shares × price disagrees with the aggregate price")
        if when is None and tier != TIER_DIRECTED:
            # A 6-K release dates itself ("were today registered"): the finder uses the filing date.
            return near("no closing date bound to the closing")
        if when is not None and when > self.filed_on:
            return near("the closing date is after the filing date", "dates_invalid", closed=False)
        if max(used) - min(used) + 1 > CHAIN_MAX_SENTENCES:
            return near(f"the facts span more than {CHAIN_MAX_SENTENCES} sentences")
        if tier != TIER_DIRECTED and self.form != "6-K" and not (self.form == "8-K" and "3.02" in self.items):
            return near("a completed sale outside an 8-K with Item 3.02" if tier == TIER_SEC_CLOSING
                        else "an instrument issued outside an 8-K with Item 3.02 or a 6-K")
        detail = what + (f" {label}" if label else "") + (f": {shares:,} shares" if shares else "") + (
            f" for {_fmt_usd(amount)}" if amount is not None else "") + (
            f" on {when.isoformat()}" if when else "")
        if amount is None and stop == "other":
            # Said only when the clause that cut the window is ANOTHER party's ("Concurrently, the
            # Company sold … to Beta Capital LLC for …") — never of the member's own restatement.
            ns, ne = self._bounds(k + 1)
            if self._aggregate_amounts(ns, ne)[0]:
                detail += "; the next sentence's price is another deal's, not bound to the member"
        return _Candidate(
            _RANK_CLOSED, tier, reason, OUTCOME_PROPOSAL, detail, tuple(sorted(used)), tuple(facts),
            instrument_label=label, amount=_integral(amount) if amount is not None else None,
            value_basis=VALUE_BASIS_INVESTED if amount is not None else None, shares=shares,
            price=price, event_date=when, needs_name=True, anchor=verb, closed=True,
        )

    def _deal_window(self, k: int, exempt: FrozenSet[int] = frozenset(),
                     kind: Tuple[str, Optional[str]] = ("plain", None)) -> Tuple[int, int, Optional[str]]:
        """``(lo, hi, stop)``: where a sale's price is read — its sentence, and the next one unless
        that sentence reports a sale, an issue or a purchase of its own (to ANYONE, the member's
        other deals included: their price is not this sale's) or an agreement. The exceptions are
        the member restating THIS sale: a link in ``exempt`` ("The Shares were sold to NVIDIA at a
        price of $50.00 per share, for …") and the member's own buyer verb ("NVIDIA paid an aggregate
        purchase price of …"), each only when the sentence names no other securities than ``kind``.
        ``stop`` is the party of the clause that cut the window (``_window_stop``), else None."""
        lo, hi = self._bounds(k)
        stop: Optional[str] = None
        if k + 1 < len(self.starts):
            ns, ne = self._bounds(k + 1)
            stop = self._window_stop(ns, ne, exempt, kind)
            if stop is None:
                hi = ne
        return lo, hi, stop

    def _holds_other_sale(self, lo: int, hi: int, exempt: FrozenSet[int] = frozenset(),
                          kind: Tuple[str, Optional[str]] = ("plain", None)) -> bool:
        """``text[lo:hi]`` reports a sale, issue, purchase or agreement other than the member's
        own restatement of the sale (``_window_stop``)."""
        return self._window_stop(lo, hi, exempt, kind) is not None

    def _window_stop(self, lo: int, hi: int, exempt: FrozenSet[int],
                     kind: Tuple[str, Optional[str]]) -> Optional[str]:
        """None when ``text[lo:hi]`` may carry the price of the sale before it; else the party of
        the first clause that reports a deal of its own: ``"other"`` (another party's — "Beta
        Capital LLC paid …", "… sold … to Beta Capital LLC"), ``"member"`` (the member's other
        deal, or one it shares) or ``"unknown"``. A completed-sale, purchase, agreement or
        warrant-issue verb always stops; a buyer's verb whose subject is EXACTLY the member, and a
        link in ``exempt`` (a "were issued and sold to <member>" restating the sale), do not —
        when the sentence names no securities other than the sale's own ``kind``."""
        text = self.text
        same = _noun_kind(text[lo:hi]) in (kind, ("unknown", None))

        def exempt_at(pos: int) -> bool:  # inside an exempt link's own "were issued and sold to"
            return any(v <= pos < v + 40 for v in exempt)

        def party_to(pos: int) -> str:  # "… to / by <purchaser>" at ``pos``
            p_kind, _m = self._purchaser(pos, hi)
            if p_kind != "other":
                return "member"
            # Another party only when a NAME follows — never "pursuant to the Purchase Agreement".
            at = _skip_spaces(text, pos)
            if text.startswith("the ", at):
                at += 4
            name = (_name_after(text, at) or "").strip()
            first = name.split()[0].rstrip(".,;:").lower() if name.split() else ""
            if not name or name.lower() in _GENERIC_TERMS or first in _GENERIC_TERMS or first in _REFERENCE_WORDS:
                return "unknown"
            return "other"

        def party_subject(pos: int) -> str:  # "<subject> purchased / agreed / paid …" at ``pos``
            subject = self._subject_before(lo, pos)
            return "other" if subject is None else "member"

        for rx in (_SALE_VERB_RE, _ISSUED_WARRANT_RE):
            m = rx.search(text, lo, hi)
            if m is not None:
                to = text.find(" to ", m.end(), min(hi, m.end() + 260))
                return party_to(to + 4) if to != -1 else "unknown"
        for rx in (_PURCHASED_RE, _AGREED_RE):
            m = rx.search(text, lo, hi)
            if m is not None:
                return party_subject(m.start())
        m = _AGREED_SELL_RE.search(text, lo, hi)
        if m is not None:
            return party_to(m.end())
        for bm in _BUYER_VERB_RE.finditer(text, lo, hi):
            if same and (exempt_at(bm.start()) or self._member_relative_before(lo, bm.start())):
                continue  # "… sold to NVIDIA, which paid …": the restatement's own clause
            by = _skip_spaces(text, bm.end())
            if text.startswith("by ", by):
                # The passive: "The aggregate purchase price of $X was paid by NVIDIA …".
                if same and self._purchaser(by + 3, hi)[0] == "exact":
                    continue
                return party_to(by + 3)
            subject = self._subject_before(lo, bm.start())
            if same and subject is not None and subject[0] == "exact":
                continue  # "NVIDIA paid an aggregate purchase price of …": the member restating this sale
            return "other" if subject is None else "member"
        for rx in (_SOLD_TO_RE, _ISSUED_TO_RE):
            for m in rx.finditer(text, lo, hi):
                if same and exempt_at(m.start()):
                    continue
                return party_to(m.end())
        for m in _SALE_ACTIVE_RE.finditer(text, lo, hi):
            if same and exempt_at(m.start()):
                continue
            to = text.find(" to ", m.end(), min(hi, m.end() + 160))
            if to != -1 and not text.startswith(_INFINITIVES, to + 4):
                return party_to(to + 4)
        return None

    def _price_unbound(self, verb: int, lo: int, hi: int, shares: Optional[int], exempt: FrozenSet[int],
                       money: _Money) -> Optional[str]:
        """Why an aggregate price read in ``[lo, hi)`` may not be the member's alone, else None:
        a second sale count (another purchaser's, or the whole placement's — never the member's
        own conversion or exercise shares), another purchaser named past the member ("… and 9,000,000
        shares to Beta Capital LLC", "… and to Beta Capital LLC"), or another buyer's clause
        before the price ("…, and Beta Capital LLC paid an aggregate purchase price of …")."""
        text = self.text
        price_k = self._sentence_of(money.start)
        for cm in _SHARE_COUNT_RE.finditer(text, lo, hi):
            if _starts_mid_number(text, cm.start("n")):
                continue
            n = _share_int(cm.group("n"))
            if n is None or n == shares:
                continue
            if cm.start() > money.start and self._sentence_of(cm.start()) != price_k:
                continue  # a later sentence's count ("Following the closing, NVIDIA owns …") is no price's
            cs = self._bounds(self._sentence_of(cm.start()))[0]
            if _COUNT_CONTEXT_RE.search(text, max(lo, cs, cm.start() - 60), cm.start()):
                continue  # "convertible … into a maximum of 21,778,000 shares", "NVIDIA owns 10,000,000 shares"
            tail = text[cm.end():min(hi, cm.end() + 60)]
            if "outstanding" in tail.split(". ")[0]:
                continue  # "… 1,234,567,890 shares of common stock outstanding"
            return f"a second share count ({n:,} shares)"
        own_starts = {m.start for m in self._mentions_in(lo, hi) if m.kind != "group"}
        terms = {t for t in self.company.terms if len(t) >= 2}
        for om in _OTHER_TO_RE.finditer(text, lo, hi):
            at = om.end()
            if at in own_starts or any(text.startswith(t, at) for t in terms):
                continue
            if text[at:at + 12].lower().startswith(_INFINITIVES):
                continue  # "Warrants to Purchase Common Stock": a title, no purchaser
            name = (_name_after(text, at) or text[at:at + 30]).strip()
            first = name.split()[0].rstrip(".,;:").lower() if name.split() else ""
            if name.lower() in _GENERIC_TERMS or first in _GENERIC_TERMS or first in _REFERENCE_WORDS:
                continue  # "… to the Purchase Agreement", ", to Section 4(a)(2)"
            return f"another purchaser ({name[:40]})"
        for bm in _BUYER_VERB_RE.finditer(text, max(lo, verb + 1), money.start):
            if any(abs(bm.start() - v) < 40 for v in exempt):
                continue
            if self._member_relative_before(lo, bm.start()):
                continue  # "… to NVIDIA Corporation, which paid an aggregate purchase price of …"
            subject = self._subject_before(self._bounds(self._sentence_of(bm.start()))[0], bm.start())
            if subject is None or subject[0] != "exact":
                return "another buyer's clause before the price"
        return None

    def _member_relative_before(self, lo: int, pos: int) -> bool:
        """``text[:pos]`` ends with "<member>, which " / "<member> (“NVIDIA”), who ": the clause at
        ``pos`` is the member's own."""
        text = self.text
        rm = _WHICH_BEFORE_RE.search(text, max(lo, pos - 30), pos)
        if rm is None:
            return False
        comma = rm.start()
        return any(m.kind != "group" and comma in (m.end, self._after_name(m.end))
                   for m in self._mentions_in(max(lo, comma - 200), comma + 1))

    def _warrant_issued(self, k: int, s: int, e: int, verb: int, verb_end: int) -> Optional[_Candidate]:
        """"the Company issued a warrant to purchase N shares … to Google LLC"."""
        text = self.text
        if _BE_BEFORE_RE.search(text, max(s, verb - 12), verb) or self._not_done(s, verb):
            return None
        label = INSTRUMENT_PRE_FUNDED_WARRANT if _PRE_FUNDED_RE.search(text, verb, verb_end) else INSTRUMENT_WARRANT
        stop = _phrase_end(text, verb_end, min(e, verb_end + 260), (". ", "; "))
        shares = None
        pos = verb_end
        tp = _TO_PURCHASE_SHARES_RE.match(text, verb_end + 1) if text.startswith(" to ", verb_end) else None
        if tp is not None:
            cm = _SHARE_COUNT_RE.match(text, tp.end())
            if cm is not None:
                shares = _share_int(cm.group("n"))
                pos = cm.end()
        to = text.find(" to ", pos, stop)
        if to == -1:
            return None
        kind, mention = self._purchaser(to + 4, e)
        if mention is None or kind == "other":
            return None
        return self._instrument_issue(k, s, verb, kind, mention, label, shares)

    def _issued_to(self, k: int, s: int, e: int, verb: int, verb_end: int) -> Optional[_Candidate]:
        """"the Company issued to Google LLC a warrant (the “Warrant”) to purchase …"."""
        text = self.text
        if _BE_BEFORE_RE.search(text, max(s, verb - 12), verb) or self._not_done(s, verb):
            return None
        kind, mention = self._purchaser(verb_end, e)
        if mention is None or kind == "other":
            return None
        wm = _WARRANT_AFTER_RE.match(text, self._skip_filler(self._after_name(mention.end)))
        if wm is None:
            return None
        label = INSTRUMENT_PRE_FUNDED_WARRANT if wm.group("pre") else INSTRUMENT_WARRANT
        shares = None
        tp = _TO_PURCHASE_SHARES_RE.search(text, wm.end(), min(e, wm.end() + 200))
        if tp is not None:
            cm = _SHARE_COUNT_RE.match(text, tp.end())
            if cm is not None:
                shares = _share_int(cm.group("n"))
        return self._instrument_issue(k, s, verb, kind, mention, label, shares)

    def _instrument_issue(self, k: int, s: int, verb: int, kind: str, mention: _Mention,
                          label: str, shares: Optional[int]) -> _Candidate:
        used = {k, self._sentence_of(mention.start)}
        facts = [(verb, mention.end)]
        if kind == "denied":
            return _deny("the member is an agent or a counterparty, not the holder", used, facts)
        if kind != "exact":
            return _near(_RANK_CLOSED_NEAR, TIER_INSTRUMENT, "instrument",
                         "a warrant issued to several holders", used, facts, closed=True)
        return self._closing(k, s, verb, used, facts, shares, label, None, what="issued")

    def _sold_to_candidates(self, links: Dict[int, List[_SoldTo]], prior: int = 0) -> List[_Candidate]:
        """A "were issued and sold to <member>" clause with its OWN count ("2,000,000 shares
        of the Company’s common stock were issued and sold to NVIDIA") is a sale clause; one
        that no sale nearby used or restated (``_consumed`` / ``_considered``, filled by
        ``_sale_clause`` and by the first pass here, only for a sale of the SAME securities) is a
        near miss — never dropped, even beside a sale verb: "Separately, shares of Series A
        Preferred Stock were issued and sold to NVIDIA" after a common-stock sale to someone else
        is the owner's to read. Two passes (the sale clauses first, so a restatement in the
        sentence BEFORE one is accounted for too), the budget checked every 64 links, and more
        than ``MAX_DEAL_CANDIDATES`` candidates in all (``prior`` of them already read) is
        ``_TooManyDeals``."""
        text = self.text
        out: List[_Candidate] = []
        closed_tier, reason = self._closed_tier
        ordered = [(k, link) for k in sorted(links) for link in links[k]]
        unread: List[Tuple[int, _SoldTo]] = []
        for n, (k, link) in enumerate(ordered):
            if n % 64 == 0:
                self.budget.check()
            if link.verb in self._consumed or link.verb in self._considered:
                continue
            cm = next((c for c in _SHARE_COUNT_RE.finditer(text, link.clause_start, link.verb)
                       if not _starts_mid_number(text, c.start("n"))), None)
            if cm is None:
                unread.append((k, link))
                continue
            of = _OF_RE.match(text, cm.end())
            noun = text[of.end() if of else cm.end():link.verb]
            obj = text[cm.start():link.verb]
            restating = frozenset(o.verb for o in self._links_near(k, links)
                                  if o.kind == link.kind and self._link_names_sale(o, obj, noun))
            cand = self._assess_sale(k, self._bounds(k)[0], link.verb, link.kind, link.mention, obj, noun,
                                     _share_int(cm.group("n")), None, company_side=True, facts_end=link.verb,
                                     exempt=restating)
            if cand is not None:
                out.append(cand)
                self._considered.update(restating)
                if prior + len(out) > MAX_DEAL_CANDIDATES:
                    raise _TooManyDeals()
        for n, (k, link) in enumerate(unread):
            if n % 64 == 0:
                self.budget.check()
            if link.verb in self._considered:
                continue
            facts = [(link.clause_start, link.mention.end if link.mention else link.verb)]
            if link.kind == "denied":
                out.append(_deny("the member is a sale's agent, not a buyer", {k}, facts))
            else:
                why = {"exact": "a sale to the member without its terms nearby",
                       "several": "sold to several purchasers",
                       "related": "the purchaser is not exactly the member"}.get(link.kind, "a sale")
                kind, label = _noun_kind(text[link.clause_start:link.verb])
                if kind == "instrument":
                    out.append(_near(_RANK_CLOSED_NEAR, TIER_INSTRUMENT, "instrument", why, {k}, facts,
                                     instrument_label=label, closed=True))
                else:
                    out.append(_near(_RANK_CLOSED_NEAR, closed_tier, reason, why, {k}, facts, closed=True))
            if prior + len(out) > MAX_DEAL_CANDIDATES:
                raise _TooManyDeals()
        return out

    def _lead_date(self, k: int, s: int, verb: int) -> Tuple[Optional[date], int, Optional[Tuple[int, int]]]:
        """The closing date of the sale whose verb is at ``verb`` in sentence ``k``, else None
        (the "no closing date" heads-up). In order:

        1. the CLAUSE-INITIAL "On <date>," nearest before the verb in its sentence
           (``_clause_on_date``: never a date inside a phrase — "the Purchase Agreement entered
           into on …", "the HSR waiting period on …", "the Form 8-K filed on …", "as previously
           announced on …") — unless the clause between the date and the verb is a SIGNING ("On
           September 15, 2025, Acme entered into …, and Acme completed …"), which is the closing
           date only when it is the filing date itself, or when the sale verb is coordinated with
           the signing (", and Acme completed …", the Coherent shape) and the date is at most
           ``SIGNING_CLOSING_WINDOW_DAYS`` before the filing date — never after "As previously
           disclosed / announced";
        2. a closing clause after the verb, in the sentence or the next one ("… closed on
           March 2, 2026", "The closing occurred on March 2, 2026", "(the “Closing Date”)");
        3. a clause-initial "On <date>" in either of the two sentences before that also names
           the member or its purchase agreement, when ITS clause is a closing ("On March 2, 2026,
           the Company closed the private placement") — or a signing on the filing date or
           within the same window before it, never one that sentence restates "as previously
           disclosed" (a same-day signing and closing, the Synopsys shape). "As previously
           disclosed, on September 15, 2025, … entered into …" is never the closing date of a
           sale filed months later."""
        text = self.text
        found = self._clause_on_date(s, verb)
        if found is not None:
            day, span = found
            signing = self._signing_verb(span[1], verb)
            if signing is None or day == self.filed_on or (
                    _phrase_end(text, signing.end(), verb, (", and ",)) < verb
                    and self._in_signing_window(day) and not _PREVIOUSLY_RE.search(text, s, verb)):
                return day, k, span
        for j in (k, k + 1):
            if j >= len(self.starts):
                break
            js, je = self._bounds(j)
            hit = self._closing_clause_date(max(js, verb) if j == k else js, je)
            if hit is not None:
                return hit[0], j, hit[1]
        for j in (k - 1, k - 2):
            if j < 0:
                break
            js, je = self._bounds(j)
            if not (self._mentions_in(js, je) or _AGREEMENT_NAME_RE.search(self.text, js, je)):
                continue
            hit = self._closing_clause_date(js, je)
            if hit is not None:
                return hit[0], j, hit[1]
            found = self._clause_on_date(js, je)
            if found is None:
                continue
            # Its clause: to the sentence end (the segmenter already kept "Inc." whole) or the
            # first depth-0 ", and " / "; ".
            clause = text[found[1][1]:_phrase_end(text, found[1][1], je, (", and ", "; "))]
            signing = bool(_SIGNING_RE.search(clause))
            if (not signing and _CLOSING_VERB_RE.search(clause)) or (signing and (
                    found[0] == self.filed_on
                    or (self._in_signing_window(found[0]) and not _PREVIOUSLY_RE.search(text, js, je)))):
                return found[0], j, found[1]
        return None, k, None

    def _in_signing_window(self, day: date) -> bool:
        """``day`` is on or at most ``SIGNING_CLOSING_WINDOW_DAYS`` before the filing date."""
        return 0 <= (self.filed_on - day).days <= SIGNING_CLOSING_WINDOW_DAYS

    def _signing_verb(self, lo: int, hi: int) -> Optional["re.Match[str]"]:
        """The first FINITE signing verb in ``[lo, hi)`` ("Acme entered into …", "signed",
        "executed", "agreed") — never a participle of the agreement it follows ("the Securities
        Purchase Agreement entered into on September 15, 2025, …", "as agreed")."""
        text = self.text
        for m in _SIGNING_VERB_RE.finditer(text, lo, hi):
            if _REDUCED_RELATIVE_BEFORE_RE.search(text, max(lo, m.start() - 40), m.start()):
                continue
            return m
        return None

    def _closing_clause_date(self, lo: int, hi: int) -> Optional[Tuple[date, Tuple[int, int]]]:
        """The first date a CLOSING clause in ``[lo, hi)`` binds: "The closing occurred on
        <date>", "… closed / completed / consummated on <date>", "<date> (the “Closing Date”)"."""
        text = self.text
        for m in _CLOSING_ON_RE.finditer(text, lo, hi):
            dm = _DATE_AT_RE.match(text, m.end())
            day = _parse_date(dm) if dm is not None and dm.end() <= hi else None
            # The whole clause up to the date must name no other party ("The transaction with
            # Beta Capital LLC closed on …" is not this sale's closing).
            c_lo = max([lo] + [text.rfind(stop, lo, m.start()) + len(stop) for stop in (", and ", "; ")
                               if text.rfind(stop, lo, m.start()) != -1])
            if day is not None and not self._names_other_party(c_lo, m.end()):
                return day, (m.start(), dm.end())
        for dm in _DATE_AT_RE.finditer(text, lo, hi):
            if _CLOSING_DATE_TERM_RE.match(text, dm.end()):
                day = _parse_date(dm)
                if day is not None:
                    return day, (dm.start(), dm.end())
        return None

    def _names_other_party(self, lo: int, hi: int) -> bool:
        """``text[lo:hi]`` (a short closing clause) names an entity that is neither the member
        nor the filer: "The closing of the Beta Capital sale occurred on …" is not this sale's."""
        covered = [(m.start, m.end) for m in self._mentions_in(lo, hi)]
        terms = {t.lower() for t in self.company.terms}
        for tm in _NAME_TOKEN_RE.finditer(self.text, lo, hi):
            word = _strip_possessive(tm.group(0).rstrip(".,;:")).lower()
            if (not tm.group(0)[:1].isupper() or word in _CLOSING_CLAUSE_WORDS or word in _REFERENCE_WORDS
                    or word in terms or any(a <= tm.start() < b for a, b in covered)):
                continue
            return True
        return False

    def _clause_on_date(self, lo: int, hi: int) -> Optional[Tuple[date, Tuple[int, int]]]:
        """The LAST clause-initial "On <date>" in ``[lo, hi)`` (the one nearest a following verb):
        a capital "On" (a sentence's own date, or one after a heading the segmenter could not
        split off: "… Securities Purchase Agreement On June 30, 2026, …"), or "on" right after
        "; " / ", and " or a sentence-opening lead-in ("Subsequently, on …"). Never a lower-case
        "on <date>" inside a phrase: "the Agreement entered into on …", "the waiting period on
        …", "the Form 8-K filed on …", "as previously announced on …", "approval on …"."""
        text = self.text
        last: Optional[Tuple[date, Tuple[int, int]]] = None
        for m in _ON_DATE_RE.finditer(text, lo, hi):
            pos = m.start()
            if text[pos] != "O" and not (
                    text.endswith(("; ", ", and "), max(lo, pos - 6), pos)
                    or (pos - lo <= 40 and text[lo:pos].strip().rstrip(",").lower() in _ON_LEAD_INS)):
                continue
            dm = _DATE_AT_RE.match(text, m.end())
            if dm is None or dm.end() > hi:
                continue
            day = _parse_date(dm)
            if day is not None:
                last = (day, (pos, dm.end()))
        return last

    def _aggregate_amounts(self, lo: int, hi: int) -> Tuple[List[_Money], bool]:
        """Every "aggregate purchase price … of $X" in ``[lo, hi)``, and whether one was not in
        U.S. dollars. The figure is read on the full text, past ``hi`` if need be."""
        out: List[_Money] = []
        foreign = False
        for m in _AGG_PRICE_RE.finditer(self.text, lo, hi):
            full = _AGG_PRICE_RE.match(self.text, m.start())
            money = _parse_money(self.text, (full or m).end())
            if money is None or money.per_share:
                continue
            if not money.usd or money.value is None:
                foreign = True
                continue
            out.append(money)
        return out, foreign

    def _unreadable_aggregate(self, lo: int, hi: int) -> bool:
        """An "aggregate purchase price … of $X" in ``[lo, hi)`` whose dollar figure could not
        be read ("$2b", "$50m", "$2,000,0000"): a refused fact, so a heads-up — never a proposal
        that silently has no amount."""
        for m in _AGG_PRICE_RE.finditer(self.text, lo, hi):
            full = _AGG_PRICE_RE.match(self.text, m.start())
            if _unreadable_usd(self.text, (full or m).end()):
                return True
        return False

    def _share_prices(self, lo: int, hi: int) -> List[_Money]:
        """The per-share prices in ``[lo, hi)``: "price per share of $X", "a price of $X per
        share", "at $X per share" — never a conversion, exercise, par or stated price."""
        text = self.text
        out: List[_Money] = []
        for rx, need_per_share in ((_PRICE_PER_SHARE_RE, False), (_PRICE_OF_RE, True), (_AT_MONEY_RE, True)):
            for m in rx.finditer(text, lo, hi):
                if _OTHER_PRICE_RE.search(text, max(lo, m.start() - 30), m.start()):
                    continue
                if rx is _PRICE_OF_RE and _AGG_PRICE_TAIL_RE.search(text, max(lo, m.start() - 40), m.start()):
                    continue
                money = _parse_money(text, m.end())
                if money is None or not money.usd or money.value is None or money.value > MAX_PRICE_PER_SHARE:
                    continue
                if need_per_share and not money.per_share:
                    continue
                if all(money.start != o.start for o in out):
                    out.append(money)
        return out

    # T2 ---------------------------------------------------------------------------------

    def _agreement_candidates(self, k: int, seen: Set[Tuple[str, int]]) -> List[_Candidate]:
        text = self.text
        s, e = self._bounds(k)
        out: List[_Candidate] = []
        agreed = False
        for m in _AGREED_RE.finditer(text, s, e):
            if ("agreed", m.start()) in seen:
                continue
            seen.add(("agreed", m.start()))
            subject = self._subject_before(s, m.start())
            if subject is None:
                continue
            agreed = True
            cand = self._agreement(k, s, e, m.start(), m.end(), subject[0], subject[1], company_side=False)
            if cand is not None:
                out.append(cand)
        for m in _AGREED_SELL_RE.finditer(text, s, e):
            if ("agreed_sell", m.start()) in seen:
                continue
            seen.add(("agreed_sell", m.start()))
            kind, mention = self._purchaser(m.end(), e)
            if kind == "other" or mention is None:
                continue
            agreed = True
            cand = self._agreement(k, s, e, m.start(), self._after_name(mention.end), kind, mention,
                                   company_side=True)
            if cand is not None:
                out.append(cand)
        if agreed:
            return out
        for m in _SPA_WITH_RE.finditer(text, s, e):
            if ("spa", m.start()) in seen:
                continue
            seen.add(("spa", m.start()))
            wm = _WITH_RE.match(text, self._skip_filler(m.end()))
            if wm is None:
                continue
            kind, mention = self._purchaser(wm.end(), e)
            if kind in ("other", "denied") or mention is None or self._closing_nearby(k):
                continue  # a closing nearby describes this agreement itself
            out.append(_near(_RANK_SIGNED_NEAR, TIER_SEC_AGREEMENT, "t2_agreement",
                             "a purchase agreement with the member; its terms were not read",
                             {k}, [(m.start(), mention.end)]))
        return out

    def _closing_nearby(self, k: int) -> bool:
        for j in (k, k + 1, k + 2):
            if 0 <= j < len(self.starts):
                js, je = self._bounds(j)
                if (_SALE_VERB_RE.search(self.text, js, je) or _PURCHASED_RE.search(self.text, js, je)
                        or _SOLD_TO_RE.search(self.text, js, je)):
                    return True
        return False

    def _agreement(self, k: int, s: int, e: int, verb: int, obj_start: int, kind: str,
                   mention: Optional[_Mention], *, company_side: bool) -> Optional[_Candidate]:
        """"<member> agreed to purchase <N> shares / up to $X / $X of <noun>" or "the Company
        agreed to issue and sell to <member> …"."""
        text = self.text
        used = {k}
        facts: List[Tuple[int, int]] = [(verb, min(e, obj_start + 1))]
        if mention is not None:
            facts.append((mention.start, mention.end))
            used.add(self._sentence_of(mention.start))

        def near(why: str, why_reason: str = "t2_agreement") -> _Candidate:
            return _near(_RANK_SIGNED_NEAR, TIER_SEC_AGREEMENT, why_reason, why, used, facts)

        if kind == "denied":
            return _deny("the member agreed to buy as an agent or a customer, not an investor", used, facts)
        if kind != "exact":
            return near("agreed together with other purchasers")
        if _HYPOTHETICAL_RE.search(text, s, verb):
            return None
        i = obj_start
        while i < e and text[i] == " ":
            i += 1
        obj_end = _phrase_end(text, i, min(e, i + 300), (". ", "; "))
        shares: Optional[int] = None
        up_to: Optional[_Money] = None
        direct: Optional[_Money] = None
        noun_start = i
        um = _UP_TO_RE.match(text, i)
        pos = um.end() if um else i
        # A share count first: "agreed to purchase 214,776,632 shares" is not a figure of
        # unknown currency.
        cm = _SHARE_COUNT_RE.match(text, pos)
        if cm is not None:
            shares = _share_int(cm.group("n"))
            of = _OF_RE.match(text, cm.end())
            noun_start = of.end() if of else cm.end()
        else:
            money = _parse_money(text, pos)
            if money is not None and not money.usd:
                return near("the commitment is not in U.S. dollars")
            if money is None and _unreadable_usd(text, pos):
                return near("the commitment amount could not be read")
            if _usd(money):
                up_to, direct = (money, None) if um else (None, money)
                of = _OF_RE.match(text, money.end)
                noun_start = of.end() if of else money.end
        noun_end = _phrase_end(text, noun_start, min(e, noun_start + 220), _NOUN_STOPS)
        noun = text[noun_start:noun_end]
        facts[0] = (verb, max(noun_end, facts[0][1]))
        noun_kind, label = _noun_kind(noun)
        if noun_kind == "unknown":
            if not _SECURITY_WORD_RE.search(text, verb, obj_end):
                return None  # "agreed to purchase $50 million of products": a commercial deal
            return near("the security agreed could not be read")
        if noun_kind in ("unnamed", "class_other"):
            return near("the security agreed could not be named")
        if noun_kind == "plain" and not _owns_noun(noun, self.company.terms, company_side=company_side) and (
                _foreign_owner(noun, self.company.terms) or not _BARE_STOCK_RE.search(noun)):
            return near("the shares agreed are not clearly the company's own stock")
        if up_to is None and direct is None:
            # "up to" only inside the member's OWN object (``_own_object_end``) — never another
            # party's cap later in the sentence (", and certain of our existing stockholders have
            # agreed to purchase up to $250 million", "… and Beta Capital LLC will purchase up to
            # $250 million") nor the member's own OPTIONAL one ("… and may purchase up to $250
            # million of additional shares"). A cap past the object is refused, never guessed at:
            # a heads-up. A FIXED amount the member agreed to stays fixed (the scan never runs).
            own_end = self._own_object_end(i, obj_end, verb)
            for um2 in _UP_TO_RE.finditer(text, i, obj_end):
                if _SHARE_COUNT_RE.match(text, um2.end()):
                    continue  # "up to 10,000,000 shares": a count, not money
                candidate = _parse_money(text, um2.end())
                if um2.start() >= own_end:
                    if not _money_marked(text, um2.end()):
                        continue  # "up to 9.9% of our outstanding shares": no cap in money
                    cap = f"up to {_fmt_usd(_integral(candidate.value))}" if _usd(candidate) else "an 'up to' cap"
                    return near(f"{cap} is not bound to the member's own purchase: it follows another "
                                "party's clause or a second purchase in the sentence")
                if candidate is not None and not candidate.usd:
                    return near("the commitment is not in U.S. dollars")
                if candidate is None and _unreadable_usd(text, um2.end()):
                    return near("the commitment amount could not be read")
                if _usd(candidate):
                    up_to = candidate
                    break
        fixed, foreign = self._aggregate_amounts(s, e)
        if foreign:
            return near("the commitment is not in U.S. dollars")
        if self._unreadable_aggregate(s, e):
            return near("the commitment amount could not be read")
        if direct is not None:
            fixed.append(direct)
        if len({f.value for f in fixed}) > 1:
            return near("several different purchase prices")
        fixed_amount = fixed[0].value if fixed else None
        if fixed:
            facts.append((fixed[0].start, fixed[0].end))
        prices = self._share_prices(s, e)
        price = prices[0].value if len({p.value for p in prices}) == 1 else None
        if shares is not None and price is not None and fixed_amount is not None and (
                abs(Decimal(shares) * price - fixed_amount) > fixed_amount * SHARES_PRICE_TOLERANCE):
            return near("shares × price disagrees with the aggregate price")
        if shares is not None and price is not None and up_to is not None and (
                abs(Decimal(shares) * price - up_to.value) > up_to.value * SHARES_PRICE_TOLERANCE):
            return near("shares × price disagrees with the 'up to' figure")
        when = self._agreement_date(k, s, e, verb)
        if when is not None and when[0] > self.filed_on:
            return near("the agreement date is after the filing date", "dates_invalid")
        event_date = None
        if when is not None:
            event_date = when[0]
            facts.append(when[1])
            used.add(self._sentence_of(when[1][0]))
        amount = basis = None
        if up_to is not None:
            amount, basis = _integral(up_to.value), VALUE_BASIS_COMMITTED_UP_TO
            facts.append((up_to.start, up_to.end))
        if max(used) - min(used) + 1 > CHAIN_MAX_SENTENCES:
            return near(f"the facts span more than {CHAIN_MAX_SENTENCES} sentences")
        if amount is not None:
            what = f"up to {_fmt_usd(amount)}"
        elif fixed_amount is not None:
            what = f"a fixed {_fmt_usd(_integral(fixed_amount))} (not shown on the row)"
        else:
            what = "no amount stated"
        detail = "agreed to purchase" + (f" {shares:,} shares" if shares else "") + (
            f" {label}" if label else "") + f": {what}" + (
            f", agreement dated {event_date.isoformat()}" if event_date else "")
        return _Candidate(
            _RANK_SIGNED, TIER_SEC_AGREEMENT, "t2_agreement", OUTCOME_PROPOSAL, detail,
            tuple(sorted(used)), tuple(facts), instrument_label=label, amount=amount,
            value_basis=basis, shares=shares, price=price, event_date=event_date, needs_name=True,
            anchor=verb,
        )

    def _own_object_end(self, i: int, obj_end: int, verb: int) -> int:
        """Where the member's own object (starting at ``i``) ends: the first depth-0 ", and" /
        ", while" / ", but" / "; " / ", as has", a second agreed / indicated verb, an option to
        purchase, a second purchase verb with any auxiliary ("will / may / intend to / are
        expected to / plans to purchase", ``_SECOND_PURCHASE_RE``), a depth-0 " and " that opens a
        new clause (``_new_clause_and``), another named party (``_other_party_in``), or
        ``obj_end``. Bounded by ``obj_end - i`` (≤ 300 characters)."""
        text = self.text
        end = _phrase_end(text, i, obj_end, _OWN_OBJECT_STOPS)
        for rx in (_AGREED_RE, _AGREED_SELL_RE, _IOI_RE, _OPTION_TO_RE, _OTHER_PARTY_CLAUSE_RE, _SECOND_PURCHASE_RE):
            m = rx.search(text, i, end)
            if m is not None and m.start() > verb:
                end = m.start()
        for found in (self._new_clause_and(i, end), self._other_party_in(i, end)):
            if found is not None and found < end:
                end = found
        return end

    def _new_clause_and(self, lo: int, hi: int) -> Optional[int]:
        """The first depth-0 " and " in ``text[lo:hi]`` after which a finite verb comes within ten
        words (before a comma, a semicolon or a parenthesis), unless a relative pronoun introduces
        it: a NEW clause — "… and Beta Capital LLC will purchase …", "… and certain of our existing
        stockholders intend to …", "… and may purchase …" — never "… and Class B common stock
        having …" or "… and warrants that are exercisable …". Bounded: ``hi - lo`` ≤ 300."""
        text = self.text
        pos = text.find(" and ", lo, hi)
        while pos != -1:
            if _paren_depth(text, lo, pos) == 0:
                seg_end = min(hi, pos + 125)
                for stop in (", ", "; ", "("):
                    cut = text.find(stop, pos + 5, seg_end)
                    if cut != -1:
                        seg_end = cut
                prev = ""
                for word in text[pos + 5:seg_end].split()[:10]:
                    low = word.strip(".,;:“”\"'’").lower()
                    if low in _FINITE_WORDS and prev not in _RELATIVE_WORDS:
                        return pos
                    prev = low
            pos = text.find(" and ", pos + 1, hi)
        return None

    def _other_party_in(self, lo: int, hi: int) -> Optional[int]:
        """The offset of the first name in ``text[lo:hi]`` that is another PARTY: another club
        member's alias, or a run of two or more capitalised words ending in a legal form ("Beta
        Capital LLC", "Morgan Stanley & Co. LLC") or holding an investor word ("Gamma Partners") —
        never the member's own mention and never one of the filer's names — or a defined group that
        lists the member ("… alongside the other Cornerstone Investors …": a shared purchase).
        Security titles, deal terms and dates ("Class A Common Stock", "the Purchase Agreement",
        "March 2, 2026") are no party. Bounded: ``hi - lo`` ≤ 300."""
        text = self.text
        best: Optional[int] = None
        for alias in self.others:
            om = _alias_regex(alias).search(text, lo, hi)
            if om is not None and (best is None or om.start() < best):
                best = om.start()
        nearby = self._mentions_in(max(0, lo - 200), min(len(text), hi + 200))
        group = next((m.start for m in nearby if m.kind == "group" and lo <= m.start < hi), None)
        if group is not None and (best is None or group < best):
            best = group
        covered = [(m.start, m.end) for m in nearby if m.kind != "group"]
        filer_keys = {_company_key(t) for t in self.company.terms} | {_company_key(self.conformed)}
        tokens = [(tm.start(), tm.group(0)) for tm in _TOKEN_RE.finditer(text, lo, hi)]
        run: List[Tuple[int, str]] = []

        def party(run: List[Tuple[int, str]]) -> bool:
            words = [_strip_possessive(w.strip("()“”\"',;:")).rstrip(".").lower() for _p, w in run]
            if words and words[0] == "the":
                words, run = words[1:], run[1:]
            if len(words) < 2 or words[-1] in _SECURITY_TITLE_WORDS or not (
                    words[-1] in _LEGAL_SUFFIX_TOKENS or any(w in _ENTITY_WORDS for w in words)):
                return False  # "Class A Common Stock", "Capital Stock", "the Purchase Agreement"
            start, end = run[0][0], run[-1][0] + len(run[-1][1])
            if any(a < end and start < b for a, b in covered):
                return False  # the member's own name ("NVIDIA Corporation")
            name = _strip_possessive(" ".join(w.strip("()“”\"',;:") for _p, w in run))
            return _company_key(name) not in filer_keys

        for n, (p, tok) in enumerate(tokens + [(hi, "")]):
            core = tok.strip("()“”\"',;:")
            nxt = tokens[n + 1][1].strip("()“”\"',;:").rstrip(".").lower() if n + 1 < len(tokens) else ""
            joins = bool(run) and (core == "&" or core.rstrip(".").lower() in _LEGAL_SUFFIX_TOKENS)
            if core and (core[:1].isupper() or joins):
                run.append((p, tok))
                if not tok.endswith((",", ";", ")")) or nxt in _LEGAL_SUFFIX_TOKENS:
                    continue  # "Technology, | Inc." stays one name
            if run and party(run):
                return run[0][0] if best is None else min(best, run[0][0])
            run = []
        return best

    def _agreement_date(self, k: int, s: int, e: int, verb: int) -> Optional[Tuple[date, Tuple[int, int]]]:
        found = self._clause_on_date(s, verb)
        if found is not None:
            return found
        for m in _DATED_RE.finditer(self.text, s, e):
            dm = _DATE_AT_RE.match(self.text, m.end())
            day = _parse_date(dm) if dm is not None else None
            if day is not None:
                return day, (m.start(), dm.end())
        if k >= 1:
            js, je = self._bounds(k - 1)
            if self._mentions_in(js, je):
                return self._clause_on_date(js, je)
        return None

    # T3 ---------------------------------------------------------------------------------

    def _ioi_candidates(self, k: int, seen: Set[Tuple[str, int]]) -> List[_Candidate]:
        text = self.text
        s, e = self._bounds(k)
        out: List[_Candidate] = []
        for m in _IOI_RE.finditer(text, s, e):
            if ("ioi", m.start()) in seen:
                continue
            seen.add(("ioi", m.start()))
            semi = text.rfind("; ", s, m.start())
            subject_lo, appositives = _ioi_subject(text, semi + 2 if semi != -1 else s, m.start())
            if _NOT_THE_MEMBER_RE.search(text, subject_lo, m.start()):
                continue  # "Neither NVIDIA nor …", "None of …", "No existing stockholder …"
            mentions = [mt for mt in self._mentions_in(subject_lo, m.start())
                        if (_paren_depth(text, subject_lo, mt.start) == 0 or mt.kind == "group")
                        and not any(a <= mt.start < b for a, b in appositives)]
            group = next((mt for mt in mentions if mt.kind == "group"), None)
            heads = [mt for mt in mentions if mt.kind != "group" and self._heads_ioi_subject(subject_lo, mt)]
            if group is None and not heads:
                continue  # "Beta Capital LLC, a partner of NVIDIA Corporation, has …"
            if group is None and all(self._denied(mt) for mt in heads):
                continue
            group_term = group.label if group else None
            members = self.groups.get(group_term, ()) if group_term else ()
            listed = False
            if group is None:
                # A subject that lists several names ("NVIDIA Corporation and Microsoft
                # Corporation have …", "Certain of our stockholders and NVIDIA …"): the amount is
                # their COMBINED figure, never the member's own.
                bare = _without_spans(text, subject_lo, m.start(), appositives)
                listed = any(self._listed_in_ioi_subject(subject_lo, m.start(), mt, bare) for mt in heads)
                if listed:
                    # Non-empty ``group_members`` is what marks the amount COMBINED for the
                    # finder, so the member is always among them (named by its first alias when
                    # the subject uses only its defined term: "NVIDIA and Beta Capital LLC").
                    members = self._members_in(bare)
                    if not any(a in members for a in self.aliases):
                        members = (self.aliases[0],) + members
            facts: List[Tuple[int, int]] = [(subject_lo, m.end())]
            amount: Optional[Decimal] = None
            unreadable = False
            um = _UP_TO_RE.match(text, m.end() + 1) if text.startswith(" ", m.end()) else None
            if um is not None:
                money = _parse_money(text, um.end())
                if _usd(money):
                    amount = _integral(money.value)
                    facts.append((um.start(), money.end))
                unreadable = money is None and _unreadable_usd(text, um.end())
            who = f"the {group_term}" if group_term else ("the member and others" if listed else "the member")
            detail = f"{who} indicated a non-binding interest" + (
                f", up to {_fmt_usd(amount)}{' combined' if group_term or listed else ''}" if amount else "") + (
                ", amount could not be read" if unreadable else "")
            out.append(_Candidate(_RANK_IOI, TIER_IOI, "t3_indication", OUTCOME_HEADS_UP, detail, (k,),
                                  tuple(facts), amount=amount, group_term=group_term, group_members=members,
                                  anchor=m.start()))
        return out

    def _heads_ioi_subject(self, subject_lo: int, mt: _Mention) -> bool:
        """The member's mention HEADS the subject: it is not the object of "of" / "with" / "by"
        after another word ("a partner of NVIDIA Corporation", "the chief executive of NVIDIA")
        — except the member's own affiliates ("Entities affiliated with NVIDIA Corporation") or
        a list ("together with NVIDIA Corporation", "each of NVIDIA and AMD")."""
        text = self.text
        lo = max(subject_lo, mt.start - 60)
        if not _IOI_OBJECT_BEFORE_RE.search(text, lo, mt.start):
            return True
        return bool(_IOI_AFFILIATE_BEFORE_RE.search(text, lo, mt.start)
                    or _IOI_LIST_BEFORE_RE.search(text, lo, mt.start))

    def _listed_in_ioi_subject(self, subject_lo: int, verb: int, mt: _Mention, bare: str) -> bool:
        """The member is one of several names in the subject: another name follows it ("NVIDIA
        Corporation and Microsoft Corporation have …"), precedes it in a list ("Certain of our
        existing stockholders and NVIDIA Corporation have …"), or another member is named outside
        an appositive (``bare``: the subject with its appositives blanked)."""
        text = self.text
        after = self._after_name(mt.end)
        if after < verb and self._several_follows(after):
            return True
        if _IOI_LIST_BEFORE_RE.search(text, max(subject_lo, mt.start - 60), mt.start):
            head = text[subject_lo:mt.start].strip(" ,")
            if head and not head.lower().endswith(("each of", "one of")):
                return True
        return any(_alias_regex(a).search(bare) for a in self.others)

    # T4 ---------------------------------------------------------------------------------

    def _directed_candidates(self, k: int, seen: Set[Tuple[str, int]]) -> List[_Candidate]:
        if self.form != "6-K":
            return []
        text = self.text
        s, e = self._bounds(k)
        out: List[_Candidate] = []
        for m in _DIRECTED_RE.finditer(text, s, e):
            if ("directed", m.start()) in seen:
                continue
            seen.add(("directed", m.start()))
            kind, mention = self._purchaser(m.end(), e)
            if kind == "other" or mention is None:
                continue
            facts: List[Tuple[int, int]] = [(m.start(), mention.end)]
            if kind == "denied":
                out.append(_deny("the member is an agent, not the subscriber", {k}, facts))
                continue
            if kind != "exact":
                out.append(_near(_RANK_CLOSED_NEAR, TIER_DIRECTED, "t4_directed",
                                 "a directed issuance to several subscribers", {k}, facts))
                continue
            shares = None
            for cm in _SHARE_COUNT_RE.finditer(text, s, e):
                if cm.group("new") and not _starts_mid_number(text, cm.start("n")):
                    shares = _share_int(cm.group("n"))
                    facts.append((cm.start(), cm.end()))
                    break
            # Completion in the SAME clause: the clause runs between the release's strong
            # stops (". ", "; ", ": ", " – ", "· ") — a cover page's "whether … will file" before
            # the headline is another clause.
            c_lo = max([s] + [text.rfind(stop, s, m.start()) + len(stop) for stop in _STRONG_CLAUSE_STOPS
                              if text.rfind(stop, s, m.start()) != -1])
            c_hi = _phrase_end(text, m.end(), e, _STRONG_CLAUSE_STOPS)
            before = text[c_lo:m.start()]
            # Completion that governs the issuance ITSELF ("Nokia has completed the directed share
            # issuance to …", "The directed share issuance to … was completed") reports it done: a
            # future tail in the same sentence (", which are expected to be admitted to trading …",
            # ", and the new shares will be delivered …") speaks of later steps, so only the words
            # before the phrase — and a condition or proposal after it ("…, subject to …") — may
            # still make it undone. Otherwise the whole clause is read, as "announced …, the
            # completion of which is subject to EGM approval" needs.
            governs = self._completion_governs(c_lo, m.start(), m.end(), c_hi)
            undone = bool(_NOT_DONE_RE.search(before) or _HYPOTHETICAL_RE.search(before)
                          or _PROPOSED_RE.search(before) or _NEGATED_BEFORE_RE.search(before)
                          or _PROPOSED_RE.search(text, m.end(), c_hi)
                          or (_CONDITION_TAIL_RE.search(text, m.end(), c_hi) if governs
                              else _NOT_DONE_RE.search(text, m.end(), c_hi)))
            if undone or not _COMPLETED_RE.search(text, c_lo, c_hi):
                out.append(self._directed_not_done(k, s, e, m.start(), facts, shares,
                                                   hypothetical=bool(_HYPOTHETICAL_RE.search(before)
                                                                     or _PROPOSED_RE.search(text, c_lo, c_hi))))
                continue
            pct = None
            for j in range(k, min(len(self.starts), k + 4)):
                js, je = self._bounds(j)
                pm = _PERCENT_OF_TOTAL_RE.search(text, js, je)
                if pm is not None:
                    pct = pm.group("pct")
                    break
            detail = "completed directed share issuance to the member" + (
                f": {shares:,} new shares" if shares else "") + (
                f", about {pct}% of the shares after it" if pct else "")
            out.append(_Candidate(_RANK_CLOSED, TIER_DIRECTED, "t4_directed", OUTCOME_PROPOSAL, detail,
                                  (k,), tuple(facts), shares=shares, needs_name=True, anchor=m.start(),
                                  closed=True))
        return out

    def _completion_governs(self, c_lo: int, at: int, end: int, c_hi: int) -> bool:
        """The directed phrase ``[at, end)`` is the object of its clause's own completion verb ("…
        has completed the directed share issuance to …") or the subject of a passive one ("The
        directed share issuance to … was completed"), with no temporal or conditional lead before
        it ("Once / After / Upon / Following …": the completion is then subordinate and the main
        clause decides, so the whole clause is still read). Bounded: one clause."""
        text = self.text
        if _SUBORDINATE_LEAD_RE.search(text, c_lo, at):
            return False
        if _COMPLETED_BEFORE_RE.search(text, max(c_lo, at - 40), at):
            return True
        own_hi = _phrase_end(text, end, c_hi, (", which", ", and ", ", with "))
        pm = _COMPLETED_PASSIVE_RE.search(text, end, own_hi)
        return pm is not None and not (_NOT_DONE_RE.search(text, end, pm.start())
                                       or _PROPOSED_RE.search(text, end, pm.start()))

    def _directed_not_done(self, k: int, s: int, e: int, at: int, facts: List[Tuple[int, int]],
                           shares: Optional[int], *, hypothetical: bool) -> _Candidate:
        """A directed issuance to the member that had NOT completed when filed ("will carry
        out …, subject to closing conditions", "the Board proposes …"). The member's own binding
        undertaking in the sentence ("NVIDIA has irrevocably undertaken to subscribe for …")
        makes it a commitment proposal (tier ``sec_agreement``, the T2 semantics: no figure);
        anything else — the issuer's announcement alone, a proposal, a condition — is a
        heads-up. Never a held stake as of the filing date."""
        text = self.text
        if not hypothetical:
            # The member's undertaking in this sentence or the next ("… NVIDIA undertakes to
            # subscribe for the new shares").
            u_lo, u_hi = s, (self._bounds(k + 1)[1] if k + 1 < len(self.starts) else e)
            for um in _UNDERTAKING_RE.finditer(text, u_lo, u_hi):
                uk = self._sentence_of(um.start())
                us = self._bounds(uk)[0]
                if _NEGATED_BEFORE_RE.search(text, max(us, um.start() - 40), um.start()):
                    continue  # "has not undertaken to subscribe"
                mention = self._undertaking_subject(us, um)
                if mention is None:
                    continue
                if _HYPOTHETICAL_RE.search(text, max(us, mention.start - 80), um.start()):
                    continue  # "if NVIDIA has agreed to subscribe …"
                detail = "agreed to subscribe in a directed share issuance" + (
                    f" for {shares:,} new shares" if shares else "") + " (not completed when filed)"
                return _Candidate(_RANK_SIGNED, TIER_SEC_AGREEMENT, "t2_agreement", OUTCOME_PROPOSAL, detail,
                                  tuple(sorted({k, uk})),
                                  tuple(facts) + ((um.start(), um.end()), (mention.start, mention.end)),
                                  shares=shares, needs_name=True, anchor=at)
        return _near(_RANK_SIGNED_NEAR, TIER_DIRECTED, "t4_directed",
                     "a directed share issuance to the member that had not completed when filed", {k}, facts,
                     shares=shares, anchor=at)

    def _undertaking_subject(self, s: int, um: "re.Match[str]") -> Optional[_Mention]:
        """The member as the SINGULAR subject of an undertaking ("…, and NVIDIA has irrevocably
        undertaken to subscribe", "NVIDIA undertakes to subscribe"): its mention (definition
        aside) ends right before "has" or a third-person verb, it is no object of "of" / "by" /
        "with" ("an affiliate of NVIDIA has …"), carries no deny term, and no other member is
        named just before it. "have" (a compound subject) or "agreed" with no auxiliary is
        never the member alone."""
        text = self.text
        lo = max(s, um.start() - 200)
        head = text[lo:um.start()].rstrip()
        if head.endswith(" has"):
            head = head[:-4].rstrip()
        elif um.group("verb").lower() not in ("undertakes", "agrees", "commits"):
            return None
        end = lo + len(head)
        for m in reversed(self._mentions_in(lo, end + 1)):
            if m.kind == "group" or end not in (m.end, self._after_name(m.end)):
                continue
            if self._denied(m) or _IOI_OBJECT_BEFORE_RE.search(text, max(s, m.start - 30), m.start):
                return None
            if any(_alias_regex(a).search(text, max(s, m.start - 80), m.start) for a in self.others):
                return None
            return m
        return None

    # EX-4.x -----------------------------------------------------------------------------

    def _ex4_candidates(self) -> List[_Candidate]:
        """An indenture / warrant exhibit counts only when the member is a PARTY in its
        preamble; a warrant is then an instrument proposal, anything else a heads-up."""
        text = self.text
        preamble_end = min(len(text), EX4_PREAMBLE_CHARS)
        party: Optional[_Mention] = None
        for m in self.mentions:
            if m.start >= preamble_end:
                break
            if m.kind == "group" or self._denied(m):
                continue
            s = self._bounds(self._sentence_of(m.start))[0]
            holder_term = m.kind == "alias" and any(
                t.lower().endswith(_HOLDER_TERM_ENDINGS)
                for t in _terms_in(_split_definition(self._definition_after(m.end) or "")[0], m.label))
            if _PARTY_RE.search(text, s, m.start) or holder_term:
                party = m
                break
        if party is None:
            return []
        title = text[:900]
        if _PRE_FUNDED_RE.search(title):
            label: Optional[str] = INSTRUMENT_PRE_FUNDED_WARRANT
        elif _WARRANT_RE.search(title):
            label = INSTRUMENT_WARRANT
        elif _CONVERTIBLE_RE.search(title) and _DEBT_RE.search(title):
            label = INSTRUMENT_CONVERTIBLE_NOTE
        elif _SAFE_RE.search(title) and (_SAFE_WORD_RE.search(title) or "future equity" in title.lower()):
            label = INSTRUMENT_SAFE
        else:
            label = None
        k = self._sentence_of(party.start)
        facts: List[Tuple[int, int]] = [(party.start, party.end)]
        used = {k}
        if label is None:
            return [_near(_RANK_CLOSED_NEAR, TIER_OTHER, "no_template",
                          "the member is a party to an EX-4 exhibit that is not a warrant", used, facts)]

        def refused(why: str) -> List[_Candidate]:
            return [_near(_RANK_CLOSED_NEAR, TIER_INSTRUMENT, "instrument", why, used, facts,
                          instrument_label=label)]

        # An executed instrument only: filed under an 8-K with Item 3.02 (or a 6-K), not a "Form
        # of Warrant", no blank date, and not a warrant that "shall be issued" at a closing.
        if not ((self.form == "8-K" and "3.02" in self.items) or self.form == "6-K"):
            return refused("an EX-4 exhibit outside an 8-K with Item 3.02 or a 6-K")
        if _FORM_OF_RE.search(text, 0, min(len(text), _EX4_TITLE_CHARS)):
            return refused("a form of the instrument, not an executed one")
        if _PLACEHOLDER_DATE_RE.search(text, 0, preamble_end):
            return refused("the exhibit's date is a blank placeholder")
        if _EX4_NOT_DONE_RE.search(text, 0, preamble_end):
            return refused("the instrument is still to be issued")
        shares = None
        tp = _TO_PURCHASE_SHARES_RE.search(text, 0, preamble_end)
        if tp is not None:
            cm = _SHARE_COUNT_RE.match(text, tp.end())
            if cm is not None:
                shares = _share_int(cm.group("n"))
                facts.append((tp.start(), cm.end()))
                used.add(self._sentence_of(tp.start()))
        when = None
        for dm0 in _DATED_RE.finditer(text, 0, preamble_end):
            dm = _DATE_AT_RE.match(text, dm0.end())
            day = _parse_date(dm) if dm is not None else None
            if day is not None:
                when = day
                facts.append((dm0.start(), dm.end()))
                used.add(self._sentence_of(dm0.start()))
                break
        if when is not None and when > self.filed_on:
            return [_near(_RANK_CLOSED_NEAR, TIER_INSTRUMENT, "dates_invalid",
                          "the issue date is after the filing date", used, facts)]
        detail = f"{label[1:-1]} issued to the member" + (f" for {shares:,} shares" if shares else "") + (
            f", dated {when.isoformat()}" if when else "")
        return [_Candidate(_RANK_CLOSED, TIER_INSTRUMENT, "instrument", OUTCOME_PROPOSAL, detail,
                           tuple(sorted(used)), tuple(facts), instrument_label=label, shares=shares,
                           event_date=when, needs_name=True, anchor=party.start, closed=True)]

    # ── choosing and finishing ──

    def _finish(self, candidates: List[_Candidate], *, fallback_reason: str = "no_template",
                fallback_detail: str = "no rule matched") -> TemplateResult:
        if not candidates:
            return _plain(TIER_OTHER, fallback_reason, OUTCOME_SKIPPED, fallback_detail, self.company.display)
        top = max(c.rank for c in candidates)
        if top == _RANK_CLOSED:
            valid = {c.anchor for c in candidates if c.rank == _RANK_CLOSED}
            stray = [c for c in candidates if c.rank == _RANK_CLOSED_NEAR and c.anchor not in valid]
            if stray:
                return _plain(stray[0].tier, stray[0].reason, OUTCOME_HEADS_UP,
                              f"a second deal with the member could not be read ({stray[0].detail})",
                              self.company.display)
        # The same deal read twice is merged. Merging stops at a THIRD distinct deal of a real rank
        # (the result is already the "different deals" heads-up), so the loop is linear, and the
        # budget is checked every 64 candidates.
        deals: List[_Candidate] = []
        several = False
        for n, c in enumerate(c for c in candidates if c.rank == top):
            if n % 64 == 63:
                self.budget.check()
            for i, d in enumerate(deals):
                if d.same_deal(c):
                    deals[i] = d.merged(c)
                    break
            else:
                if len(deals) >= 2 and top > _RANK_DENY:
                    several = True
                    break
                deals.append(c)
        if len(deals) > 1 and top > _RANK_DENY:
            first = deals[0]
            count = "several (3 or more)" if several else str(len(deals))
            return _plain(first.tier, first.reason, OUTCOME_HEADS_UP,
                          f"{count} different deals with the member in one document", self.company.display)
        return self._result(deals[0])

    def _result(self, c: _Candidate) -> TemplateResult:
        outcome, reason, detail = c.outcome, c.reason, c.detail
        name = self.company.display
        if c.needs_name and outcome == OUTCOME_PROPOSAL:
            if not self.company.matched:
                outcome, reason = OUTCOME_HEADS_UP, "s2_name_mismatch"
                detail = ("the company the document defines is not SEC's filer" if self.company.conflict
                          else "no defining clause names SEC's filer") + f"; {detail}"
            elif not name:
                outcome, reason = OUTCOME_HEADS_UP, "name_unusable"
                detail = f"the company name cannot be shown as written; {detail}"
            elif c.instrument_label and len(f"{name} {c.instrument_label}") > INVESTEE_NAME_MAX:
                outcome, reason = OUTCOME_HEADS_UP, "name_unusable"
                detail = f"the name with its instrument is over {INVESTEE_NAME_MAX} characters; {detail}"
        spans: Tuple[str, ...] = ()
        if outcome != OUTCOME_SKIPPED and c.facts:
            built = self._spans(c)
            if built is None:
                logger.warning(
                    "[tc-stakes-template-verify] %s %s: the evidence for %s/%s did not verify "
                    "against the full text — a heads-up instead", self.conformed[:80], self.form,
                    c.tier, c.reason,
                )
                outcome = OUTCOME_HEADS_UP
                detail = f"evidence did not verify; {detail}"
            else:
                spans = built
        proposal = outcome == OUTCOME_PROPOSAL
        return TemplateResult(
            tier=c.tier, reason=reason, outcome=outcome, company_name=name,
            instrument_label=c.instrument_label,
            amount_usd=c.amount if (c.value_basis is not None or not proposal) else None,
            value_basis=c.value_basis if proposal else None,
            shares=c.shares, price_per_share=c.price, event_date=c.event_date, spans=spans,
            group_term=c.group_term, group_members=c.group_members, detail=_clip(detail),
            closed=c.closed and outcome != OUTCOME_SKIPPED and c.tier not in (TIER_SEC_AGREEMENT, TIER_IOI),
        )

    def _spans(self, c: _Candidate) -> Optional[Tuple[str, ...]]:
        """The evidence: the sentence a fact sits in when it is 40-400 characters, else a
        ≤ 400-character piece around the fact; each verified against the FULL text. None when
        any piece fails (a cut number, a Unicode digit) — the caller downgrades."""
        text = self.text
        pieces: List[Tuple[int, int]] = []
        for a, b in sorted(set(c.facts)):
            s, e = self._bounds(self._sentence_of(a))
            if PEG_SPAN_MIN_CHARS <= e - s <= PEG_SPAN_MAX_CHARS and b <= e:
                pieces.append((s, e))
            elif e - s > PEG_SPAN_MAX_CHARS and b <= e:
                pieces += [(ca, cb) for ca, cb in _clause_chunks(text, s, e) if ca < b and a < cb]
            else:
                # A fragment under 40 characters, or a figure read past a forced split: a
                # window that may reach into the neighbouring sentences.
                pieces.append(_fit_span(text, a, b, max(0, s - 200), min(len(text), max(e, b) + 200)))
        merged: List[Tuple[int, int]] = []
        for a, b in sorted(set(pieces)):
            if merged and a <= merged[-1][1]:
                pa, pb = merged[-1]
                if b <= pb:
                    continue
                if b - pa <= PEG_SPAN_MAX_CHARS:
                    merged[-1] = (pa, b)
                    continue
            merged.append((a, b))
        if self._norm is None:
            self.budget.check()
            self._norm = normalize_peg_text(text)
        out: List[str] = []
        for a, b in merged:
            self.budget.check()
            span = text[a:b].strip()
            if not verify_peg_span(span, text, normalized=self._norm):
                return None
            if any(span in o for o in out):
                continue
            out = [o for o in out if o not in span]
            out.append(span)
            if len(out) >= MAX_RESULT_SPANS:
                break
        return tuple(out) if out else None

    # ── no template ──

    def _fallback(self, anchors: List[int]) -> TemplateResult:
        """No rule matched: say why the member is named, as a skipped reason."""
        co_investor = commercial = False
        for i, k in enumerate(anchors[:2000]):
            if i % 256 == 0:
                self.budget.check()
            s, e = self._bounds(k)
            co_investor = co_investor or bool(_CO_INVESTOR_RE.search(self.text, s, e))
            commercial = commercial or bool(_COMMERCIAL_RE.search(self.text, s, e))
        if co_investor:
            return _plain(TIER_OTHER, "co_investor_only", OUTCOME_SKIPPED,
                          "the member is named only as another investor", self.company.display)
        if commercial:
            return _plain(TIER_OTHER, "commercial_only", OUTCOME_SKIPPED,
                          "the member is named only as a commercial counterparty", self.company.display)
        return _plain(TIER_OTHER, "no_template", OUTCOME_SKIPPED,
                      "the member is named, but no rule matched", self.company.display)


def _clause_cut(text: str, start: int, end: int) -> int:
    """Where a purchaser phrase stops at the latest: before a quantity (" of 1 000 000 new
    shares") or a depth-0 ", " that opens a lower-case clause (", which …"), unless that word
    continues a list ("and", "or", "including", "together with" …). Bounded by ``end``."""
    depth = 0
    for i in range(start, end):
        ch = text[i]
        if ch == "(":
            depth += 1
        elif ch == ")" and depth:
            depth -= 1
        elif depth:
            continue
        elif ch == " " and text.startswith(" of ", i) and text[i + 4:i + 5].isdigit():
            return i
        elif ch == "," and text[i + 1:i + 2] == " " and text[i + 2:i + 3].islower():
            word = text[i + 2:i + 20].split(" ", 1)[0].rstrip(",")
            if word not in _LIST_WORDS:
                return i
    return end


#: Lower-case words that continue a list of purchasers after a comma.
_LIST_WORDS: FrozenSet[str] = frozenset({
    "and", "or", "including", "together", "along", "each", "plus", "as", "namely", "nor",
})


def _depth0_commas(text: str, lo: int, hi: int) -> List[int]:
    """Offsets of every ", " in ``text[lo:hi]`` outside parentheses."""
    out: List[int] = []
    depth = 0
    for i in range(lo, hi):
        ch = text[i]
        if ch == "(":
            depth += 1
        elif ch == ")" and depth:
            depth -= 1
        elif ch == "," and depth == 0 and text[i + 1:i + 2] == " ":
            out.append(i)
    return out


def _ioi_subject(text: str, lo: int, verb: int) -> Tuple[int, List[Tuple[int, int]]]:
    """``(start, appositives)`` of the subject of "has / have indicated an interest": the last
    comma-separated segment before the verb, extended left only through a list of names
    ("NVIDIA Corporation, Advanced Micro Devices, Inc., and Broadcom Inc. (collectively, …)
    have") or an appositive ("NVIDIA Corporation, an existing stockholder, has"). A lead-in
    clause ("Following discussions with NVIDIA Corporation, certain of our stockholders have")
    is never part of it, so the member it names is not the one showing interest. The
    appositive segments are returned too: a member named only INSIDE one ("Beta Capital LLC, a
    partner of NVIDIA Corporation, has") never heads the subject."""
    cuts = [lo] + [c + 2 for c in _depth0_commas(text, lo, verb)]
    segments = [(a, b - 2 if i + 1 < len(cuts) else verb)
                for i, (a, b) in enumerate(zip(cuts, cuts[1:] + [verb + 2]))]
    while len(segments) > 1 and not text[segments[-1][0]:segments[-1][1]].strip():
        segments.pop()  # "NVIDIA Corporation, an existing stockholder, | has"
    i = len(segments) - 1
    listing = False
    appositives: List[Tuple[int, int]] = []
    while i > 0:
        cur = text[segments[i][0]:segments[i][1]].strip()
        prev = text[segments[i - 1][0]:segments[i - 1][1]].strip()
        cur_l, prev_first = cur.lower(), (prev.split() or [""])[0]
        if prev_first.rstrip(",").lower() in _LEAD_IN_WORDS:
            break
        if cur_l.rstrip(".") in _LEGAL_SUFFIX_TOKENS:
            i -= 1  # "…Devices, | Inc." — one name
            continue
        if cur_l.startswith(("and ", "or ")):
            listing = True
            i -= 1
            continue
        if listing and prev[:1].isupper():
            i -= 1
            continue
        if cur_l.startswith(_INCLUDING_STARTS):
            i -= 1  # "No existing stockholder, | including NVIDIA Corporation, | has": one subject
            continue
        if cur_l.startswith(_APPOSITIVE_STARTS) and prev[:1].isupper():
            appositives.append(segments[i])
            i -= 1  # "NVIDIA Corporation, | an existing stockholder, | has"
            continue
        break
    return segments[i][0], appositives


def _without_spans(text: str, lo: int, hi: int, spans: Sequence[Tuple[int, int]]) -> str:
    """``text[lo:hi]`` with every span blanked (lengths kept)."""
    out = list(text[lo:hi])
    for a, b in spans:
        for p in range(max(a, lo), min(b, hi)):
            out[p - lo] = " "
    return "".join(out)


#: A segment that opens a sentence with one of these is a lead-in, never a subject.
_LEAD_IN_WORDS: FrozenSet[str] = frozenset({
    "in", "as", "following", "after", "before", "pursuant", "under", "upon", "subject",
    "additionally", "separately", "accordingly", "also", "further", "furthermore", "moreover",
    "concurrently", "simultaneously", "previously", "on", "during", "since", "while", "if",
    "although", "because", "through", "with",
})
_APPOSITIVE_STARTS: Tuple[str, ...] = ("a ", "an ", "one of ", "our ", "its ", "the ", "each of ")
#: A segment that names members of the subject before it ("certain stockholders, including
#: NVIDIA Corporation, have"): part of the subject, never an appositive.
_INCLUDING_STARTS: Tuple[str, ...] = ("including ", "such as ", "namely ", "in particular ")


def _split_definition(body: str) -> Tuple[str, Optional[str]]:
    """A party's definition parenthetical → (its own part, a GROUP term defined in it).
    "“Broadcom” and, together with NVIDIA and AMD, the “Cornerstone Investors”" → ("“Broadcom”
    and, ", "Cornerstone Investors"); "together with its affiliates, “NVIDIA”" stays the
    party's own."""
    stripped = body.strip()
    low = stripped.lower()
    if low.startswith(("collectively", "together,", "each,", "each ")):
        terms = [t for t in _QUOTED_TERM_RE.findall(stripped) if t.lower() not in _GENERIC_TERMS]
        return "", terms[-1] if terms else None
    gm = _GROUP_WORDS_RE.search(stripped)
    if gm is None or _OWN_GROUP_RE.match(stripped, gm.start()):
        return stripped, None
    terms = [t for t in _QUOTED_TERM_RE.findall(stripped, gm.end()) if t.lower() not in _GENERIC_TERMS]
    return stripped[:gm.start()], terms[-1] if terms else None


def _terms_in(body: str, alias: str) -> List[str]:
    """The party's own defined terms in its definition: quoted ("NVIDIA", "Initial
    Warrantholder") or the unquoted prospectus abbreviation "(NVIDIA)"."""
    terms = [t.strip() for t in _QUOTED_TERM_RE.findall(body)]
    if not terms and _UNQUOTED_TERM_RE.fullmatch(body.strip()):
        if body.strip().split()[0].lower() in alias.lower().split():
            terms = [body.strip()]
    return [t for t in terms if len(t) >= 2 and t.lower() not in _GENERIC_TERMS]


def _is_list_gap(gap: str) -> bool:
    """True when ``gap`` — between a listed name and a group parenthetical — only continues a
    list of names: commas, "and" / "or" / "of" / "&", capitalised words and legal forms."""
    if len(gap) > 260 or _LIST_DOT_RE.search(_PAREN_STRIP_RE.sub(" ", gap).replace("Inc.", "Inc").replace(
            "Corp.", "Corp").replace("Co.", "Co").replace("Ltd.", "Ltd")):
        return False
    for word in _PAREN_STRIP_RE.sub(" ", gap).replace(",", " ").split():
        low = word.lower().strip(".")
        if low in {"and", "or", "of", "&"} or low in _LEGAL_SUFFIX_TOKENS:
            continue
        if not (word[:1].isupper() or word[:1].isdigit()):
            return False
    return True


__all__: Sequence[str] = (
    "TEMPLATE_OUTCOMES", "VALUE_BASIS_INVESTED", "VALUE_BASIS_COMMITTED_UP_TO",
    "TEMPLATE_VALUE_BASES", "INSTRUMENT_CONVERTIBLE_PREFERRED", "INSTRUMENT_PREFERRED",
    "INSTRUMENT_WARRANT", "INSTRUMENT_PRE_FUNDED_WARRANT", "INSTRUMENT_CONVERTIBLE_NOTE",
    "INSTRUMENT_SAFE", "INSTRUMENT_UNITS", "INSTRUMENT_LABELS", "SENTENCE_MAX_CHARS",
    "CHAIN_MAX_SENTENCES", "MAX_RESULT_SPANS", "DEFAULT_CPU_BUDGET_SECONDS",
    "MAX_CPU_BUDGET_SECONDS", "MAX_DEAL_SENTENCES", "MAX_DEAL_CANDIDATES", "SIGNING_CLOSING_WINDOW_DAYS",
    "MAX_ALIAS_TERMS", "MAX_GROUP_TERMS",
    "EX4_PREAMBLE_CHARS", "SHARES_PRICE_TOLERANCE", "TemplateResult", "classify_document",
)
