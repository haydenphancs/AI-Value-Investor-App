"""Trillion Club stake finder — the templates over other companies' filings (plan v3, "Templates").

Pins ``app.services.trillion_club.stake_templates.classify_document`` against the recorded public
SEC documents under ``tests/fixtures/trillion_club_stakes`` (each expected outcome is the plan's
"Tests" section) and against synthetic must-not-match text. Every case asserts the OUTCOME apart
from the facts, and a refused fact must degrade to a heads-up or a skip — never to a wrong
number on a proposal.

§1 vocabulary, result invariants and import purity · §2 the recorded documents · §3 the plan's
must-not-match list · §4 deny terms bind to the member's mention · §5 T1 rules · §6 T2 · §7 T3
· §8 T4 · §9 EX-4 exhibits · §10 names · §11 inputs · §12 the CPU budget · §13 sentence and
span helpers · §14 linear time: every compiled pattern of the module over ~1M-character inputs
· §15 the 2026-10-28 review (S2-1 … S2-10): each finding's must-flag shapes and honest twins
· §16 the 2026-10-29 review (Review A: the T2 "up to" scope, the T1 closing date, the T1 deal
window, the T4 completion clause, the CPU budget in the last two loops): the reviewer's
scenarios verbatim as must-not-match cases, with their honest twins.

Hermetic: no network, no Supabase — the documents are files and every input is built here.
"""

from __future__ import annotations

import ast
import dataclasses
import gc
import inspect
import json
import logging
import re
import time
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

import pytest

from app.schemas import trillion_club as schema
from app.services.learn_weekly import sec_facts
from app.services.trillion_club import stake_contracts as C
from app.services.trillion_club import stake_templates as T
from app.utils import filing_text

BACKEND = Path(__file__).resolve().parents[1]
FIX = BACKEND / "tests" / "fixtures" / "trillion_club_stakes"
MANIFEST = json.loads((FIX / "MANIFEST.json").read_text(encoding="utf-8"))
CONFIGS = C.load_member_configs(str(BACKEND / "data" / "trillion_club_filers.json"))


def aliases(slug: str) -> List[str]:
    return list(CONFIGS[slug].s2_aliases)


def others(slug: str) -> List[str]:
    return [a for s, c in CONFIGS.items() if s != slug for a in c.s2_aliases]


def html_doc(name: str) -> str:
    raw = (FIX / "html" / f"{name}.htm").read_text(encoding="utf-8")
    return filing_text.html_to_text(raw, document=f"{name}.htm", max_chars=None, log_tag="tc-stakes")


def text_doc(name: str) -> str:
    return (FIX / "text" / f"{name}.txt").read_text(encoding="utf-8")


def efts_hit(efts: str, accession: str, document: str) -> dict:
    """The recorded EDGAR full-text-search hit for one document (its form, items, date, type)."""
    data = json.loads((FIX / "json" / f"{efts}.json").read_text(encoding="utf-8"))
    for hit in data["hits"]["hits"]:
        if hit["_id"] == f"{accession}:{document}":
            return hit["_source"]
    raise AssertionError(f"no recorded hit {accession}:{document} in {efts}")


def classify(text: str, *, slug: Optional[str] = None, member_aliases: Optional[Sequence[str]] = None,
             conformed: str = "ACME CORP", form: str = "8-K", items: Sequence[str] = ("3.02",),
             filed_on: date = date(2026, 3, 10), file_type: Optional[str] = "8-K",
             other_aliases: Optional[Sequence[str]] = None, budget: float = 2.0) -> T.TemplateResult:
    slug = slug or "nvidia"
    return T.classify_document(
        text=text, member_aliases=list(member_aliases or aliases(slug)), filer_conformed_name=conformed,
        form=form, items=list(items), filed_on=filed_on, file_type=file_type,
        other_member_aliases=list(others(slug) if other_aliases is None else other_aliases),
        cpu_budget_seconds=budget,
    )


def classify_hit(name: str, efts: str, *, slug: str = "nvidia", issuer: str) -> T.TemplateResult:
    """Classify a recorded document from its recorded EFTS hit, as the finder will."""
    doc = MANIFEST["documents"][name]
    hit = efts_hit(efts, doc["accession"], doc["document"])
    return classify(
        html_doc(name), slug=slug, conformed=MANIFEST["issuers"][issuer]["name"], form=hit["root_forms"][0],
        items=hit.get("items") or (), filed_on=date.fromisoformat(hit["file_date"]), file_type=hit["file_type"],
    )


def assert_spans_verified(result: T.TemplateResult, text: str) -> None:
    """Every span is a 40-400 character verbatim quote of the FULL text, none inside another."""
    norm = filing_text.normalize_peg_text(text)
    assert 1 <= len(result.spans) <= T.MAX_RESULT_SPANS, result.spans
    for span in result.spans:
        assert filing_text.verify_peg_span(span, text, normalized=norm), span
        assert filing_text.verify_peg_span(span, text), span  # the same answer, normalised or not
        assert sum(span in other for other in result.spans) == 1, span


HEAD = "Item 3.02 Unregistered Sales of Equity Securities. "
BASE = (
    HEAD + "On March 2, 2026, Acme Corporation (the “Company”) completed the issuance and sale of "
    "1,000,000 shares of the Company’s common stock, par value $0.001 per share (the “Shares”), to "
    "NVIDIA Corporation (“NVIDIA”), for an aggregate purchase price of $50,000,000 in cash, representing a "
    "price per share of $50.00. "
)
TO_NVIDIA = "to NVIDIA Corporation (“NVIDIA”), for"
PROSPECTUS = "In this prospectus, “Acme,” “we,” “us” and “our” refer to Acme Corporation. "


# ── §1 vocabulary, result invariants and import purity ──────────────────────────────────


def test_the_outcomes_are_the_findings_outcomes_minus_auto():
    assert T.TEMPLATE_OUTCOMES == (C.OUTCOME_PROPOSAL, C.OUTCOME_HEADS_UP, C.OUTCOME_SKIPPED)
    assert set(T.TEMPLATE_OUTCOMES) < set(C.FINDING_OUTCOMES)
    assert C.OUTCOME_AUTO not in T.TEMPLATE_OUTCOMES  # the finder alone decides auto, after its gates


def test_the_value_bases_are_the_screens_own():
    assert T.TEMPLATE_VALUE_BASES == ("invested", "committed_up_to")
    assert set(T.TEMPLATE_VALUE_BASES) <= set(schema.VALUE_BASES)


def test_the_instrument_vocabulary_is_the_plans_closed_list():
    assert T.INSTRUMENT_LABELS == (
        "(convertible preferred)", "(preferred)", "(warrant)", "(pre-funded warrant)",
        "(convertible note)", "(SAFE)", "(units)",
    )


def test_the_result_has_exactly_the_contract_fields():
    assert [f.name for f in dataclasses.fields(T.TemplateResult)] == [
        "tier", "reason", "outcome", "company_name", "instrument_label", "amount_usd", "value_basis",
        "shares", "price_per_share", "event_date", "spans", "group_term", "group_members", "detail",
        "closed",
    ]
    # closed is the one field added after the contract was published: it has a default, so
    # a caller that builds a result without it keeps working.
    assert dataclasses.fields(T.TemplateResult)[-1].default is False
    assert T.TemplateResult.__dataclass_params__.frozen


def test_classify_document_takes_the_contract_keywords():
    sig = inspect.signature(T.classify_document)
    assert list(sig.parameters) == [
        "text", "member_aliases", "filer_conformed_name", "form", "items", "filed_on", "file_type",
        "other_member_aliases", "cpu_budget_seconds",
    ]
    assert all(p.kind is inspect.Parameter.KEYWORD_ONLY for p in sig.parameters.values())
    assert sig.parameters["cpu_budget_seconds"].default == 2.0 == T.DEFAULT_CPU_BUDGET_SECONDS


def test_spans_are_verified_by_the_shared_filing_text_check():
    """The contract: ``verify_peg_span`` comes through ``learn_weekly.sec_facts`` (A1 re-exports
    the lifted object), and ``normalized=`` is the same module's normalisation."""
    assert T.verify_peg_span is sec_facts.verify_peg_span is filing_text.verify_peg_span
    assert T.normalize_peg_text is filing_text.normalize_peg_text
    assert (T.PEG_SPAN_MIN_CHARS, T.PEG_SPAN_MAX_CHARS) == (40, 400)


_ALLOWED_APP_IMPORTS = {
    "app.services.learn_weekly.sec_facts",
    "app.services.trillion_club.stake_contracts",
    "app.services.trillion_club.stake_names",
    "app.utils.filing_text",
}


def test_the_module_imports_only_the_stdlib_and_the_contract_modules():
    """Pure (plan v3): no Supabase, no FMP, no LLM, no HTTP — read from the AST, so a lazy import
    inside a function counts too."""
    tree = ast.parse(Path(T.__file__).read_text(encoding="utf-8"))
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            found.add(node.module or "")
    app = {m for m in found if m.split(".")[0] == "app"}
    stdlib = found - app
    assert app == _ALLOWED_APP_IMPORTS, app
    assert stdlib <= {"__future__", "bisect", "dataclasses", "datetime", "decimal", "functools",
                      "logging", "math", "re", "time", "typing"}, stdlib


def test_every_reason_and_tier_the_module_names_is_in_the_closed_sets():
    """A reason or tier literal outside ``stake_contracts`` would only fail when that branch runs;
    read every string literal next to a ``reason``/tier position from the source."""
    src = Path(T.__file__).read_text(encoding="utf-8")
    literals = set(re.findall(r'"([a-z0-9_]{3,40})"', src))
    named_reasons = {s for s in literals if s in C.REASONS}
    for must in ("t1_closing", "t2_agreement", "t3_indication", "t4_directed", "instrument", "member_filed",
                 "commercial_only", "co_investor_only", "exhibit_skipped", "no_template",
                 "s2_name_mismatch", "name_unusable", "dates_invalid"):
        assert must in named_reasons, must
    for tier in (C.TIER_SEC_CLOSING, C.TIER_SEC_AGREEMENT, C.TIER_IOI, C.TIER_DIRECTED, C.TIER_INSTRUMENT,
                 C.TIER_OTHER):
        assert tier in C.FINDING_TIERS


def _result(**kw) -> T.TemplateResult:
    base = dict(
        tier=C.TIER_SEC_CLOSING, reason="t1_closing", outcome=C.OUTCOME_PROPOSAL, company_name="Acme",
        instrument_label=None, amount_usd=Decimal("5"), value_basis="invested", shares=1,
        price_per_share=Decimal("5"), event_date=date(2026, 1, 1), spans=("x" * 50,), group_term=None,
        group_members=(), detail="d",
    )
    base.update(kw)
    return T.TemplateResult(**base)


@pytest.mark.parametrize("kw", [
    dict(tier="sec_close"), dict(reason="t5"), dict(outcome="auto"), dict(company_name=""),
    dict(company_name="x" * 61), dict(instrument_label="(option)"), dict(amount_usd=Decimal("-1")),
    dict(amount_usd=Decimal("NaN")), dict(amount_usd=5), dict(amount_usd=Decimal(10) ** 14),
    dict(value_basis="fair_value"), dict(value_basis=None),  # an amount on a proposal needs its basis
    dict(amount_usd=None),  # … and a basis needs an amount
    dict(shares=0), dict(shares=True), dict(shares=1.5), dict(price_per_share=Decimal(0)),
    dict(event_date=datetime(2026, 1, 1)), dict(spans=["x" * 50]), dict(spans=("",)),
    dict(spans=("x" * 50,) * 6), dict(spans=()), dict(company_name=None), dict(group_term=""),
    dict(group_members=("",)), dict(group_members=["NVIDIA Corporation"]), dict(detail="x" * 301),
])
def test_a_result_that_breaks_an_invariant_is_refused(kw):
    with pytest.raises(ValueError):
        _result(**kw)


def test_a_heads_up_may_carry_an_informational_amount_but_never_a_basis():
    _result(outcome=C.OUTCOME_HEADS_UP, value_basis=None, spans=(), company_name=None)
    with pytest.raises(ValueError):
        _result(outcome=C.OUTCOME_HEADS_UP, spans=(), company_name=None)  # basis on a heads-up


# ── §2 the recorded documents (plan v3 "Tests": the expected outcomes) ─────────────────


@pytest.mark.parametrize("name, issuer, company, amount, shares, price, when", [
    ("intel_closing_8k", "intel", "Intel", 5_000_000_000, 214_776_632, "23.28", date(2025, 12, 26)),
    ("coherent_closing_8k", "coherent", "Coherent", 2_000_000_000, 7_788_161, "256.80", date(2026, 3, 2)),
    ("synopsys_closing_8k", "synopsys", "Synopsys", 2_000_000_000, 4_821_717, "414.79", date(2025, 12, 1)),
    ("coreweave_closing_8k", "coreweave", "CoreWeave", 2_000_000_000, 22_935_780, "87.20", date(2026, 1, 23)),
])
def test_a_recorded_8k_closing_is_a_t1_trial_proposal(name, issuer, company, amount, shares, price, when):
    r = classify_hit(name, "efts_nvidia_2025q4_2026q1", issuer=issuer)
    assert (r.tier, r.reason, r.outcome) == (C.TIER_SEC_CLOSING, "t1_closing", C.OUTCOME_PROPOSAL), r.detail
    assert r.company_name == company
    assert r.amount_usd == Decimal(amount) and r.value_basis == "invested"
    assert r.shares == shares and r.price_per_share == Decimal(price) and r.event_date == when
    assert r.instrument_label is None and r.group_term is None and r.group_members == ()
    assert_spans_verified(r, html_doc(name))
    # The spans carry the facts: the shares sold and the amount.
    joined = " ".join(r.spans)
    assert f"{shares:,}" in joined and ("$5.0 billion" in joined or "$2 billion" in joined)


def test_intel_closing_reads_the_closing_date_not_the_agreements():
    """The closing sentence's "On December 26, 2025" wins over the next sentence's agreement
    "dated September 15, 2025" (the seed's as_of is the closing)."""
    r = classify_hit("intel_closing_8k", "efts_nvidia_2025q4_2026q1", issuer="intel")
    assert r.event_date == date(2025, 12, 26)


def test_coherents_item_7_01_collaboration_does_not_deny_the_closing():
    """The 8-K's Item 7.01 names "a collaboration between Coherent and NVIDIA" and its
    forward-looking statements "the supply relationship between the Company and NVIDIA": deny
    terms bind to the purchaser mention, so the closing in Item 3.02 still stands."""
    text = html_doc("coherent_closing_8k")
    assert "a collaboration between Coherent and NVIDIA" in text and "supply relationship" in text
    r = classify_hit("coherent_closing_8k", "efts_nvidia_2025q4_2026q1", issuer="coherent")
    assert r.outcome == C.OUTCOME_PROPOSAL and r.tier == C.TIER_SEC_CLOSING
    # Without the closing, the same collaboration wording is only a commercial counterparty (the
    # defining clause stays, so the document is still a hit for "NVIDIA Corporation").
    without = (text[:text.index("Item 3.02")] + "Item 1.01 Entry into a Material Definitive Agreement. On March "
               "2, 2026, Coherent Corp. (“Coherent” or the “Company”) entered into a collaboration agreement with "
               "NVIDIA Corporation (“NVIDIA”). " + text[text.index("Item 7.01"):])
    r2 = classify(without, conformed="COHERENT CORP.", items=("1.01", "7.01", "9.01"), filed_on=date(2026, 3, 2))
    assert (r2.reason, r2.outcome) == ("commercial_only", C.OUTCOME_SKIPPED)


@pytest.mark.parametrize("name, issuer, company, shares, price, when", [
    ("marvell_preferred_8k", "marvell", "Marvell Technology", 2_000_000, None, date(2026, 3, 31)),
    ("lumentum_preferred_8k", "lumentum", "Lumentum Holdings", 2_876_415, "695.31", date(2026, 3, 2)),
])
def test_a_recorded_convertible_preferred_closing_is_an_instrument_proposal(name, issuer, company, shares,
                                                                            price, when):
    r = classify_hit(name, "efts_nvidia_2025q4_2026q1", issuer=issuer)
    assert (r.tier, r.reason, r.outcome) == (C.TIER_INSTRUMENT, "instrument", C.OUTCOME_PROPOSAL), r.detail
    assert r.instrument_label == "(convertible preferred)"
    assert r.company_name == company and f"{r.company_name} {r.instrument_label}" == {
        "marvell_preferred_8k": "Marvell Technology (convertible preferred)",
        "lumentum_preferred_8k": "Lumentum Holdings (convertible preferred)",
    }[name]  # the seed's own spelling of these two rows
    assert r.amount_usd == Decimal(2_000_000_000) and r.value_basis == "invested"
    assert r.shares == shares and r.event_date == when
    assert r.price_per_share == (Decimal(price) if price else None)
    assert_spans_verified(r, html_doc(name))


def test_marvells_stated_value_and_conversion_price_are_never_the_price():
    """Marvell's 8-K states "an initial stated value of $1,000" and "an initial conversion price of
    approximately $91.8355 per share" — neither is what NVIDIA paid per share."""
    text = html_doc("marvell_preferred_8k")
    assert "stated value of $1,000" in text and "$91.8355 per share" in text
    r = classify_hit("marvell_preferred_8k", "efts_nvidia_2025q4_2026q1", issuer="marvell")
    assert r.price_per_share is None and r.amount_usd == Decimal(2_000_000_000)


def test_intels_signing_8k_is_a_commitment_with_no_figure():
    """intel_signing_8k (Item 3.02): "NVIDIA agreed to purchase … The Shares are to be issued" —
    signed, not closed: T2, kind commitment, and a FIXED amount is no figure on the row."""
    text = html_doc("intel_signing_8k")
    r = classify(text, conformed="INTEL CORP", items=("3.02", "7.01", "9.01"), filed_on=date(2025, 9, 18))
    assert (r.tier, r.reason, r.outcome) == (C.TIER_SEC_AGREEMENT, "t2_agreement", C.OUTCOME_PROPOSAL)
    assert r.amount_usd is None and r.value_basis is None
    assert r.shares == 214_776_632 and r.price_per_share == Decimal("23.28")
    assert r.event_date == date(2025, 9, 15) and r.company_name == "Intel"
    assert "$5,000,000,000" in r.detail and "not shown on the row" in r.detail
    assert_spans_verified(r, text)


def test_the_meta_424b5_is_one_indication_of_interest_for_each_cornerstone_investor():
    """NVIDIA, AMD and Broadcom (the “Cornerstone Investors”) indicated a non-binding interest of up
    to $5.0 billion combined — a heads-up for each, never a row, and the underwriter sentences
    around it do not kill it."""
    text = text_doc("meta_424b5_final")
    assert "acting as representatives of the underwriters" in text
    hit = efts_hit("efts_nvidia_2026_10", "0001193125-26-436410", "d94886d424b5.htm")
    for slug in ("nvidia", "amd", "broadcom"):
        r = classify(text, slug=slug, conformed=MANIFEST["issuers"]["meta"]["name"], form=hit["root_forms"][0],
                     items=(), filed_on=date.fromisoformat(hit["file_date"]), file_type=hit["file_type"])
        assert (r.tier, r.reason, r.outcome) == (C.TIER_IOI, "t3_indication", C.OUTCOME_HEADS_UP), (slug, r)
        assert r.group_term == "Cornerstone Investors"
        assert r.group_members == ("NVIDIA Corporation", "Advanced Micro Devices, Inc.", "Broadcom Inc.")
        assert r.amount_usd == Decimal(5_000_000_000) and r.value_basis is None
        assert r.company_name == "Meta Platforms"
        assert any("(collectively, the “Cornerstone Investors”)" in s for s in r.spans)
        assert_spans_verified(r, text)


def test_the_meta_424b5_is_meta_s_own_filing_for_meta_and_absent_for_others():
    text = text_doc("meta_424b5_final")
    own = classify(text, slug="meta", conformed="Meta Platforms, Inc.", form="424B5", items=(), file_type="424B5")
    assert (own.reason, own.outcome) == ("member_filed", C.OUTCOME_HEADS_UP)
    apple = classify(text, slug="apple", conformed="Meta Platforms, Inc.", form="424B5", items=(), file_type="424B5")
    assert (apple.reason, apple.outcome) == ("no_template", C.OUTCOME_SKIPPED)


def test_iambic_is_not_an_indication_for_nvidia():
    """The Iambic 424B4's interest is ARK's and Duquesne's; NVIDIA appears only as a partner."""
    text = text_doc("iambic_424b4")
    assert "have indicated an interest in purchasing" in text and "NVIDIA Corporation (NVIDIA)" in text
    r = classify(text, conformed="Iambic Therapeutics, Inc.", form="424B4", items=(),
                 filed_on=date(2026, 10, 15), file_type="424B4")
    assert r.tier != C.TIER_IOI and r.outcome == C.OUTCOME_SKIPPED
    assert r.reason == "commercial_only"


def test_amazons_ex_10_1_is_a_co_investor_for_nvidia_and_amazons_own_filing_for_amazon():
    """Amazon's equity commitment letter to OpenAI names NVIDIA only among OpenAI's other investors."""
    name = "amazon_openai_ex101"
    doc = MANIFEST["documents"][name]
    hit = efts_hit("efts_nvidia_2025q4_2026q1", doc["accession"], doc["document"])
    common = dict(conformed=MANIFEST["members"]["amazon"]["name"], form=hit["root_forms"][0],
                  items=hit["items"], filed_on=date.fromisoformat(hit["file_date"]), file_type=hit["file_type"])
    nv = classify(html_doc(name), slug="nvidia", **common)
    assert (nv.tier, nv.reason, nv.outcome) == (C.TIER_OTHER, "co_investor_only", C.OUTCOME_SKIPPED)
    amzn = classify(html_doc(name), slug="amazon", **common)
    assert (amzn.reason, amzn.outcome) == ("member_filed", C.OUTCOME_HEADS_UP)


def test_inuvo_names_google_llc_only_as_a_services_counterparty():
    name = "inuvo_noise_8k"
    doc = MANIFEST["documents"][name]
    hit = efts_hit("efts_google_llc_2026", doc["accession"], doc["document"])
    r = classify(html_doc(name), slug="alphabet", conformed="INUVO, INC.", form=hit["root_forms"][0],
                 items=hit["items"], filed_on=date.fromisoformat(hit["file_date"]), file_type=hit["file_type"])
    assert (r.reason, r.outcome) == ("commercial_only", C.OUTCOME_SKIPPED)


def test_noka_s_6k_directed_issuance_is_a_t4_proposal():
    r = classify_hit("nokia_directed_6k", "efts_nvidia_2025q4_2026q1", issuer="nokia")
    assert (r.tier, r.reason, r.outcome) == (C.TIER_DIRECTED, "t4_directed", C.OUTCOME_PROPOSAL), r.detail
    assert r.company_name == "Nokia" and r.shares == 166_389_351
    assert r.event_date is None  # "were today registered": the finder dates it by the filing
    assert r.amount_usd is None and r.value_basis is None
    assert "2.90%" in r.detail and "completed" in r.detail
    assert_spans_verified(r, html_doc("nokia_directed_6k"))


def test_the_marvell_warrant_exhibit_is_a_warrant_proposal_when_google_llc_is_a_party():
    name = "marvell_google_warrant_ex41"
    doc = MANIFEST["documents"][name]
    hit = efts_hit("efts_google_llc_2026", doc["accession"], doc["document"])
    common = dict(conformed=MANIFEST["issuers"]["marvell"]["name"], form=hit["root_forms"][0],
                  items=hit["items"], filed_on=date.fromisoformat(hit["file_date"]), file_type=hit["file_type"])
    r = classify(html_doc(name), slug="alphabet", **common)
    assert (r.tier, r.reason, r.outcome) == (C.TIER_INSTRUMENT, "instrument", C.OUTCOME_PROPOSAL), r.detail
    assert r.instrument_label == "(warrant)" and r.company_name == "Marvell Technology"
    assert r.shares == 58_970_907 and r.event_date == date(2026, 8, 18)
    assert r.amount_usd is None and r.value_basis is None
    assert any("(b) Google LLC" in s for s in r.spans)
    assert_spans_verified(r, html_doc(name))
    # The same exhibit naming a member only far below the preamble: not a party → skipped.
    text = html_doc(name)
    text = text[:30000] + " The Corporation may sell products to NVIDIA Corporation from time to time. " + text[30000:]
    nv = classify(text, slug="nvidia", **common)
    assert (nv.reason, nv.outcome) == ("exhibit_skipped", C.OUTCOME_SKIPPED)


def test_every_recorded_document_classifies_the_same_twice():
    """Deterministic: the same input gives an equal result."""
    for name, issuer in (("intel_closing_8k", "intel"), ("nokia_directed_6k", "nokia"),
                         ("marvell_preferred_8k", "marvell")):
        a = classify_hit(name, "efts_nvidia_2025q4_2026q1", issuer=issuer)
        b = classify_hit(name, "efts_nvidia_2025q4_2026q1", issuer=issuer)
        assert a == b


# ── §3 the plan's must-not-match list ───────────────────────────────────────────────────


def test_will_be_issued_and_sold_is_never_a_closing():
    text = (HEAD + "On March 2, 2026, Acme Corporation (the “Company”) entered into a Securities Purchase "
            "Agreement with NVIDIA Corporation (“NVIDIA”). The Shares will be issued and sold to NVIDIA at the "
            "closing for an aggregate purchase price of $50,000,000. ")
    r = classify(text)
    assert r.outcome != C.OUTCOME_PROPOSAL and r.tier != C.TIER_SEC_CLOSING


@pytest.mark.parametrize("purchaser", [
    "to NVIDIA Corporation and certain other investors for",
    "to NVIDIA Corporation and other purchasers for",
    "to NVIDIA Corporation, together with Microsoft Corporation, for",
    "to each of NVIDIA Corporation and Microsoft Corporation, for",
    "to NVIDIA Corporation, Microsoft Corporation and Broadcom Inc., for",
    "to certain investors, including NVIDIA Corporation, for",
    "to NVIDIA Corporation or its designee for",
])
def test_a_sale_to_the_member_and_others_is_a_heads_up(purchaser):
    r = classify(BASE.replace(TO_NVIDIA, purchaser))
    assert (r.tier, r.reason, r.outcome) == (C.TIER_SEC_CLOSING, "t1_closing", C.OUTCOME_HEADS_UP), r.detail
    assert r.amount_usd is None or r.value_basis is None
    assert "several purchasers" in r.detail


@pytest.mark.parametrize("sold", [
    "the sale of all of the issued and outstanding shares of its wholly-owned subsidiary, Acme Robotics, Inc.,",
    "the sale of all of the outstanding shares of Acme Robotics, Inc., a subsidiary of the Company,",
    "the sale of its robotics business",
    "the sale of the assets of its robotics division",
])
def test_a_subsidiary_or_business_sale_to_the_member_is_skipped_as_a_divestiture(sold):
    text = (HEAD + f"On March 2, 2026, Acme Corporation (the “Company”) completed {sold} to NVIDIA Corporation "
            "for an aggregate purchase price of $500 million. ")
    r = classify(text)
    assert (r.reason, r.outcome) == ("no_template", C.OUTCOME_SKIPPED) and "divestiture" in r.detail


def test_item_2_01_without_item_3_02_is_a_divestiture():
    r = classify(BASE, items=("2.01", "9.01"))
    assert r.outcome == C.OUTCOME_SKIPPED and "divestiture" in r.detail


@pytest.mark.parametrize("money", ["€50,000,000", "EUR 50,000,000", "£50,000,000", "C$50,000,000",
                                   "HK$50,000,000", "50,000,000", "¥5,000,000,000"])
def test_a_price_not_in_us_dollars_is_never_a_figure(money):
    r = classify(BASE.replace("$50,000,000", money))
    assert r.outcome == C.OUTCOME_HEADS_UP and r.amount_usd is None and r.value_basis is None
    assert "not in U.S. dollars" in r.detail


@pytest.mark.parametrize("money", ["US$50,000,000", "U.S.$50,000,000", "$50 million", "$0.05 billion",
                                   "$50,000,000.00", "USD 50,000,000"])
def test_us_dollar_spellings_are_read(money):
    r = classify(BASE.replace("$50,000,000", money))
    assert r.outcome == C.OUTCOME_PROPOSAL and r.amount_usd == Decimal(50_000_000), (money, r.detail)


def test_a_number_cut_at_a_window_edge_is_read_whole_or_not_at_all(monkeypatch):
    """A sentence force-split INSIDE "$2,000,000,000": numbers are read on the full text, so the
    result carries the whole figure (or none) — never "$2,000" — and every span still verifies."""
    clause = ("On March 2, 2026, Acme Corporation (the “Company”) completed the issuance and sale of 40,000,000 "
              "shares of the Company’s common stock to NVIDIA Corporation for an aggregate purchase price of ")
    text = HEAD + clause + "$2,000,000,000 in cash and the Company used the proceeds to fund its operations "
    cut_at = text.index("$2,000,000,000") + len("$2,000")
    start = len(HEAD)
    monkeypatch.setattr(T, "SENTENCE_MAX_CHARS", cut_at - start)
    monkeypatch.setattr(T, "_force_cut", lambda t, s: s + T.SENTENCE_MAX_CHARS)  # a hard cut, always
    # The guard is not vacuous: read inside the window, the figure WOULD be cut.
    window = text[:cut_at]
    assert T._MONEY_AT_RE.match(window, window.index("$2,000")).group("num") == "2,000"
    r = classify(text)
    assert r.amount_usd in (Decimal(2_000_000_000), None), r
    if r.outcome == C.OUTCOME_PROPOSAL:
        assert r.amount_usd == Decimal(2_000_000_000)
        assert_spans_verified(r, text)
    # And a count never starts inside a longer number ("…40,|000,000 shares").
    mid = text.index("40,000,000") + 3
    assert all(m.start() != mid for m in T._SHARE_COUNT_RE.finditer(text))


def test_a_signing_1_01_and_3_02_pair_is_an_agreement_not_a_closing():
    text = ("Item 1.01 Entry into a Material Definitive Agreement. On March 2, 2026, Acme Corporation (the "
            "“Company”) entered into a Securities Purchase Agreement (the “Purchase Agreement”) with NVIDIA "
            "Corporation (“NVIDIA”), pursuant to which NVIDIA agreed to purchase 1,000,000 shares of the "
            "Company’s common stock for an aggregate purchase price of $50,000,000. The closing is subject to "
            "customary closing conditions. " + HEAD + "The information in Item 1.01 is incorporated herein by "
            "reference. ")
    r = classify(text, items=("1.01", "3.02", "9.01"))
    assert (r.tier, r.outcome) == (C.TIER_SEC_AGREEMENT, C.OUTCOME_PROPOSAL)
    assert r.amount_usd is None and r.shares == 1_000_000 and r.event_date == date(2026, 3, 2)


# ── §4 deny terms bind to the member's mention ──────────────────────────────────────────


@pytest.mark.parametrize("purchaser", [
    "to NVIDIA Corporation, as underwriter, for",
    "to NVIDIA Corporation, in its capacity as placement agent, for",
    "to NVIDIA Corporation (“NVIDIA”), as initial purchaser, for",
    "to NVIDIA Corporation, a customer of the Company, for",
    "to NVIDIA Corporation, one of our significant customers, for",
])
def test_a_deny_term_on_the_purchaser_mention_skips_it_as_commercial(purchaser):
    r = classify(BASE.replace(TO_NVIDIA, purchaser))
    assert (r.reason, r.outcome) == ("commercial_only", C.OUTCOME_SKIPPED), r.detail


def test_the_possessive_products_deny_binds_to_the_mention():
    text = (HEAD + "On March 2, 2026, Acme Corporation (the “Company”) announced that NVIDIA Corporation’s products "
            "will be used in its data centers. ")
    r = classify(text)
    assert r.outcome == C.OUTCOME_SKIPPED and r.reason == "commercial_only"


def test_from_the_member_is_never_a_purchase_by_the_member():
    text = (HEAD + "On March 2, 2026, Acme Corporation (the “Company”) completed the purchase of 10,000 accelerator "
            "systems from NVIDIA Corporation for $50 million. ")
    r = classify(text)
    assert r.outcome == C.OUTCOME_SKIPPED


def test_a_deny_word_elsewhere_in_the_sentence_does_not_deny_the_member():
    """"underwriter" bound to someone ELSE (Morgan Stanley) never refuses NVIDIA's purchase."""
    text = BASE.replace("in cash, representing", "in cash, with Morgan Stanley & Co. LLC acting as underwriter, "
                                                  "representing")
    r = classify(text)
    assert r.outcome == C.OUTCOME_PROPOSAL and r.tier == C.TIER_SEC_CLOSING, r.detail


# ── §5 T1 rules ─────────────────────────────────────────────────────────────────────────


def test_the_baseline_closing():
    r = classify(BASE)
    assert (r.tier, r.reason, r.outcome) == (C.TIER_SEC_CLOSING, "t1_closing", C.OUTCOME_PROPOSAL)
    assert (r.company_name, r.amount_usd, r.shares, r.price_per_share, r.event_date) == (
        "Acme", Decimal(50_000_000), 1_000_000, Decimal("50.00"), date(2026, 3, 2))
    assert_spans_verified(r, BASE)


@pytest.mark.parametrize("purchaser, why", [
    ("to an affiliate of NVIDIA Corporation, for", "not exactly the member"),
    ("to NVIDIA Corporation’s wholly owned subsidiary, for", "not exactly the member"),
    ("to entities affiliated with NVIDIA Corporation, for", "not exactly the member"),
])
def test_a_related_purchaser_is_a_heads_up(purchaser, why):
    r = classify(BASE.replace(TO_NVIDIA, purchaser))
    assert r.outcome == C.OUTCOME_HEADS_UP and why in r.detail


def test_a_sale_to_someone_else_is_not_the_members_even_when_it_is_named_later():
    text = (HEAD + "On March 2, 2026, Acme Corporation (the “Company”) completed the issuance and sale of 1,000,000 "
            "shares of the Company’s common stock to Beta Capital LLC for an aggregate purchase price of $50,000,000 "
            "in cash, which NVIDIA Corporation funded. ")
    assert classify(text).outcome == C.OUTCOME_SKIPPED


@pytest.mark.parametrize("sentence, ok", [
    ("Pursuant to the Purchase Agreement, NVIDIA purchased 1,000,000 shares of the Company’s common stock", True),
    ("Pursuant to the Purchase Agreement, NVIDIA purchased 1,000,000 shares of our common stock", True),
    ("Pursuant to the Purchase Agreement, NVIDIA purchased 1,000,000 shares of its common stock", False),
    ("Pursuant to the Purchase Agreement, NVIDIA purchased 1,000,000 shares of common stock of Beta Corp.", False),
    ("Pursuant to the Purchase Agreement, NVIDIA purchased $50,000,000 of the Company’s common stock", True),
    ("Pursuant to the Purchase Agreement, the Company and NVIDIA purchased 1,000,000 shares of the Company’s "
     "common stock", False),
])
def test_the_sold_noun_must_be_the_filers_own_stock(sentence, ok):
    text = (HEAD + "On March 2, 2026, Acme Corporation (the “Company”) entered into a Securities Purchase Agreement "
            "(the “Purchase Agreement”) with NVIDIA Corporation (“NVIDIA”). " + sentence +
            " for an aggregate purchase price of $50,000,000 in cash. ")
    # Filed the day it was signed (the Synopsys shape): the signing date is then the closing date.
    r = classify(text, filed_on=date(2026, 3, 2))
    if ok:
        assert r.outcome == C.OUTCOME_PROPOSAL and r.amount_usd == Decimal(50_000_000), r.detail
    else:
        assert r.outcome != C.OUTCOME_PROPOSAL, r.detail


def test_a_purchase_of_products_is_never_a_stake():
    text = (HEAD + "On March 2, 2026, Acme Corporation (the “Company”) announced that NVIDIA Corporation purchased "
            "$50 million of the Company’s products. ")
    r = classify(text)
    assert r.outcome == C.OUTCOME_SKIPPED and r.reason == "commercial_only"


def test_a_closing_needs_item_3_02_in_an_8k():
    r = classify(BASE, items=("1.01", "8.01", "9.01"))
    assert r.outcome == C.OUTCOME_HEADS_UP and "Item 3.02" in r.detail
    # Malformed EFTS items are ignored, never read as 3.02.
    r2 = classify(BASE, items=["Item 3.02", "3.020", None, 3.02])  # type: ignore[list-item]
    assert r2.outcome == C.OUTCOME_HEADS_UP
    assert classify(BASE, items=[" 3.02 "]).outcome == C.OUTCOME_PROPOSAL


def test_a_closing_outside_an_8k_is_a_heads_up():
    r = classify(BASE, form="424B5", items=(), file_type="424B5")
    assert r.outcome == C.OUTCOME_HEADS_UP and r.tier == C.TIER_SEC_CLOSING


def test_a_closing_needs_its_date_and_a_date_after_the_filing_is_refused():
    no_date = classify(BASE.replace("On March 2, 2026, ", ""))
    assert no_date.outcome == C.OUTCOME_HEADS_UP and "no closing date" in no_date.detail
    late = classify(BASE.replace("March 2, 2026", "March 20, 2026"))
    assert (late.reason, late.outcome) == ("dates_invalid", C.OUTCOME_HEADS_UP)
    same_day = classify(BASE.replace("March 2, 2026", "March 10, 2026"))
    assert same_day.outcome == C.OUTCOME_PROPOSAL and same_day.event_date == date(2026, 3, 10)
    on_or_about = classify(BASE.replace("On March 2, 2026", "On or about March 2, 2026"))
    assert on_or_about.outcome == C.OUTCOME_HEADS_UP  # an approximate date is no closing date


def test_the_day_month_year_spelling_is_read():
    r = classify(BASE.replace("March 2, 2026", "2 March 2026"))
    assert r.outcome == C.OUTCOME_PROPOSAL and r.event_date == date(2026, 3, 2)


@pytest.mark.parametrize("bad", ["February 30, 2026", "March 2, 1890"])
def test_an_impossible_date_is_no_date(bad):
    r = classify(BASE.replace("March 2, 2026", bad))
    assert r.outcome == C.OUTCOME_HEADS_UP and r.event_date is None


@pytest.mark.parametrize("price, ok", [("50.00", True), ("50.49", True), ("49.51", True), ("50.51", False),
                                       ("49.49", False)])
def test_shares_times_price_must_be_within_one_percent(price, ok):
    r = classify(BASE.replace("$50.00", f"${price}"))
    assert (r.outcome == C.OUTCOME_PROPOSAL) is ok, (price, r.detail)
    if not ok:
        assert "disagrees" in r.detail and r.amount_usd is None


def test_two_different_aggregate_prices_are_a_heads_up():
    r = classify(BASE + "The Shares were sold for an aggregate purchase price of $60,000,000 in cash. ")
    assert r.outcome == C.OUTCOME_HEADS_UP and "several different purchase prices" in r.detail


def test_a_per_share_figure_is_never_the_aggregate_amount():
    text = BASE.replace("an aggregate purchase price of $50,000,000 in cash, representing a price per share of "
                        "$50.00", "an aggregate purchase price of $50.00 per share")
    r = classify(text)
    assert r.amount_usd is None or r.amount_usd != Decimal(50)


@pytest.mark.parametrize("old, new", [
    ("$50,000,000", "$５０,000,000"),            # fullwidth digits: Decimal("５") would read them
    ("$50,000,000", "$٥٠,000,000"),            # Arabic-Indic digits
    ("1,000,000 shares", "１,000,000 shares"),
])
def test_a_unicode_digit_is_never_read_as_a_figure(old, new):
    r = classify(BASE.replace(old, new))
    assert r.outcome == C.OUTCOME_HEADS_UP, r  # its evidence holds a homoglyph digit: it cannot verify
    if old.startswith("$"):
        assert r.amount_usd is None
    else:
        assert r.shares is None


def test_evidence_that_does_not_verify_turns_a_proposal_into_a_heads_up(monkeypatch, caplog):
    """The degrade path behind every span: a span ``verify_peg_span`` refuses (a cut number, a
    homoglyph digit) never reaches the owner as a pre-filled row."""
    monkeypatch.setattr(T, "verify_peg_span", lambda span, text, normalized=None: False)
    with caplog.at_level(logging.WARNING, logger=T.__name__):
        r = classify(BASE)
    assert (r.tier, r.reason, r.outcome) == (C.TIER_SEC_CLOSING, "t1_closing", C.OUTCOME_HEADS_UP)
    assert r.spans == () and r.value_basis is None and "evidence did not verify" in r.detail
    assert any("[tc-stakes-template-verify]" in rec.getMessage() for rec in caplog.records)


def test_a_figure_that_continues_past_the_match_is_malformed():
    r = classify(BASE.replace("$50,000,000", "$50,000,0000"))
    assert r.amount_usd is None


def test_two_closings_in_one_document_are_a_heads_up_never_half_the_story():
    second = ("On March 3, 2026, the Company completed the issuance and sale of 2,000,000 shares of the Company’s "
              "common stock to NVIDIA Corporation for an aggregate purchase price of $90,000,000 in cash. ")
    assert classify(BASE + second).outcome == C.OUTCOME_HEADS_UP
    # A readable closing next to an UNREADABLE second deal is a heads-up too.
    third = ("On March 4, 2026, the Company completed the issuance and sale of 500,000 shares of the Company’s "
             "common stock to NVIDIA Corporation and certain other investors. ")
    r = classify(BASE + third)
    assert r.outcome == C.OUTCOME_HEADS_UP and "second deal" in r.detail


def test_the_same_closing_told_twice_is_one_proposal():
    again = ("The Shares were issued and sold to NVIDIA in a private placement relying upon the exemption provided "
             "by Section 4(a)(2) of the Securities Act. ")
    r = classify(BASE + again)
    assert r.outcome == C.OUTCOME_PROPOSAL and r.amount_usd == Decimal(50_000_000)


def test_a_purchaser_link_must_name_the_same_securities():
    """"The Warrants were issued and sold to NVIDIA" never names the buyer of a SHARE sale."""
    text = (HEAD + "On March 2, 2026, Acme Corporation (the “Company”) completed the issuance and sale of 1,000,000 "
            "shares of the Company’s common stock (the “Shares”) for an aggregate purchase price of $50,000,000. "
            "The Warrants were issued and sold to NVIDIA Corporation in a private placement. ")
    r = classify(text)
    assert not (r.outcome == C.OUTCOME_PROPOSAL and r.tier == C.TIER_SEC_CLOSING), r


@pytest.mark.parametrize("noun, label", [
    ("Series A Convertible Preferred Stock", "(convertible preferred)"),
    ("Series B Preferred Stock", "(preferred)"),
    ("Series A Preference Shares", "(preferred)"),
    ("pre-funded warrants to purchase common stock", "(pre-funded warrant)"),
    ("warrants to purchase common stock", "(warrant)"),
    ("4.00% Convertible Senior Notes", "(convertible note)"),
    ("Simple Agreements for Future Equity", "(SAFE)"),
    ("units, each consisting of one share and one warrant", "(warrant)"),
    ("units of the Company", "(units)"),
])
def test_an_instrument_closing_carries_its_label(noun, label):
    text = BASE.replace("1,000,000 shares of the Company’s common stock", f"1,000,000 shares of the Company’s {noun}")
    if "units" in noun or "Notes" in noun or "Future Equity" in noun:
        text = BASE.replace("1,000,000 shares of the Company’s common stock", f"1,000,000 of the Company’s {noun}")
    r = classify(text)
    assert r.instrument_label == label, (noun, r)
    if r.outcome == C.OUTCOME_PROPOSAL:
        assert r.tier == C.TIER_INSTRUMENT and r.reason == "instrument"


@pytest.mark.parametrize("noun", ["Senior Secured Notes due 2031", "exchangeable debentures", "options",
                                  "Class B common stock"])
def test_an_instrument_outside_the_vocabulary_is_a_heads_up(noun):
    text = BASE.replace("1,000,000 shares of the Company’s common stock", f"1,000,000 shares of the Company’s {noun}")
    r = classify(text)
    assert r.outcome == C.OUTCOME_HEADS_UP and r.instrument_label is None, (noun, r)


def test_the_instrument_name_must_fit_sixty_characters():
    long_name = "Acme Advanced Semiconductor Packaging Technologies Corporation"
    text = (HEAD + f"On March 2, 2026, {long_name} (the “Company”) completed the issuance and sale of 1,000,000 "
            "shares of the Company’s Series A Convertible Preferred Stock to NVIDIA Corporation for an aggregate "
            "purchase price of $50,000,000 in cash. ")
    r = classify(text, conformed="ACME ADVANCED SEMICONDUCTOR PACKAGING TECHNOLOGIES CORP")
    assert (r.reason, r.outcome) == ("name_unusable", C.OUTCOME_HEADS_UP), r


def test_a_warrant_sold_to_the_member_reads_its_count_past_to_purchase():
    """" to " in "warrants to purchase" opens an infinitive: it is never the purchaser's "to"."""
    text = (HEAD + "On March 2, 2026, Acme Corporation (the “Company”) completed the issuance and sale of warrants to "
            "purchase 1,000,000 shares of the Company’s common stock to NVIDIA Corporation for an aggregate purchase "
            "price of $5,000,000 in cash. ")
    r = classify(text)
    assert (r.tier, r.instrument_label, r.outcome) == (C.TIER_INSTRUMENT, "(warrant)", C.OUTCOME_PROPOSAL), r
    assert r.shares == 1_000_000 and r.amount_usd == Decimal(5_000_000)


@pytest.mark.parametrize("purchaser, expected", [
    ("to Beta Capital LLC and NVIDIA Corporation, for", "several"),
    ("to Beta Capital LLC of 1,000,000 shares, and NVIDIA Corporation is a customer, for", "other"),
    ("to Beta Capital LLC, which NVIDIA Corporation advised, for", "other"),
])
def test_the_purchaser_phrase_ends_at_a_new_clause(purchaser, expected):
    r = classify(BASE.replace(TO_NVIDIA, purchaser))
    if expected == "several":
        assert r.outcome == C.OUTCOME_HEADS_UP and "several purchasers" in r.detail, r
    else:
        assert r.outcome == C.OUTCOME_SKIPPED, r


def test_a_warrant_issued_to_the_member_is_an_instrument_proposal():
    for clause in ("issued to NVIDIA Corporation (“NVIDIA”) a warrant (the “Warrant”) to purchase up to 5,000,000 "
                   "shares of the Company’s common stock",
                   "issued a warrant to purchase 5,000,000 shares of the Company’s common stock to NVIDIA Corporation"):
        text = HEAD + f"On March 2, 2026, Acme Corporation (the “Company”) {clause}. "
        r = classify(text)
        assert (r.tier, r.instrument_label, r.outcome) == (C.TIER_INSTRUMENT, "(warrant)", C.OUTCOME_PROPOSAL), clause
        assert r.shares == 5_000_000 and r.event_date == date(2026, 3, 2)
    future = HEAD + "On March 2, 2026, Acme Corporation (the “Company”) agreed that a warrant is to be issued to NVIDIA Corporation. "
    assert classify(future).outcome != C.OUTCOME_PROPOSAL


def test_a_6k_closing_is_a_t4_proposal():
    r = classify(BASE.replace(HEAD, ""), form="6-K", items=(), file_type="6-K")
    assert (r.tier, r.reason, r.outcome) == (C.TIER_DIRECTED, "t4_directed", C.OUTCOME_PROPOSAL)
    assert r.amount_usd == Decimal(50_000_000) and r.value_basis == "invested"


# ── §6 T2 ───────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("phrase", ["up to $500 million", "not to exceed $500 million", "a maximum of $500 million",
                                    "up to an aggregate of $500 million"])
def test_an_up_to_commitment_carries_committed_up_to(phrase):
    text = (f"In a separate private placement concurrent with this offering, NVIDIA Corporation has agreed to purchase "
            f"{phrase} of shares of our common stock at the initial public offering price. " + PROSPECTUS)
    r = classify(text, form="424B4", items=(), file_type="424B4")
    assert (r.tier, r.outcome) == (C.TIER_SEC_AGREEMENT, C.OUTCOME_PROPOSAL), r.detail
    assert r.amount_usd == Decimal(500_000_000) and r.value_basis == "committed_up_to"
    assert_spans_verified(r, text)


def test_a_fixed_commitment_has_no_figure():
    text = ("NVIDIA Corporation has agreed to purchase $500 million of shares of our common stock in a concurrent "
            "private placement. " + PROSPECTUS)
    r = classify(text, form="424B4", items=(), file_type="424B4")
    assert r.outcome == C.OUTCOME_PROPOSAL and r.amount_usd is None and r.value_basis is None
    assert "$500,000,000" in r.detail


def test_up_to_a_number_of_shares_is_no_dollar_figure():
    text = ("NVIDIA Corporation has agreed to purchase up to 10,000,000 shares of our common stock in a concurrent "
            "private placement. " + PROSPECTUS)
    r = classify(text, form="424B4", items=(), file_type="424B4")
    assert r.outcome == C.OUTCOME_PROPOSAL and r.amount_usd is None and r.shares == 10_000_000


def test_the_company_side_agreement_is_read():
    text = ("Item 1.01 Entry into a Material Definitive Agreement. On March 2, 2026, Acme Corporation (the “Company”) "
            "agreed to issue and sell to NVIDIA Corporation (“NVIDIA”) 1,000,000 shares of the Company’s common stock "
            "for an aggregate purchase price of $50,000,000. ")
    r = classify(text, items=("1.01",))
    assert (r.tier, r.outcome) == (C.TIER_SEC_AGREEMENT, C.OUTCOME_PROPOSAL) and r.shares == 1_000_000


@pytest.mark.parametrize("text, outcome", [
    ("NVIDIA Corporation may agree to purchase up to $500 million of shares of our common stock. ", None),
    ("NVIDIA Corporation and Microsoft Corporation have agreed to purchase up to $500 million of shares of our "
     "common stock. ", C.OUTCOME_HEADS_UP),
    ("NVIDIA Corporation has agreed to purchase up to €500 million of shares of our common stock. ", C.OUTCOME_HEADS_UP),
    ("NVIDIA Corporation has agreed to purchase $50 million of our products over three years. ", C.OUTCOME_SKIPPED),
])
def test_an_agreement_that_is_not_a_clean_commitment_is_never_a_proposal(text, outcome):
    r = classify(text + PROSPECTUS, form="424B4", items=(), file_type="424B4")
    assert r.outcome != C.OUTCOME_PROPOSAL, r
    if outcome:
        assert r.outcome == outcome, r


def test_a_bare_spa_with_the_member_is_a_heads_up_unless_a_closing_follows():
    spa = (HEAD + "On March 2, 2026, Acme Corporation (the “Company”) entered into a Securities Purchase Agreement (the "
           "“Purchase Agreement”) with NVIDIA Corporation (“NVIDIA”). ")
    r = classify(spa)
    assert (r.tier, r.outcome) == (C.TIER_SEC_AGREEMENT, C.OUTCOME_HEADS_UP)
    closed = spa + ("On March 2, 2026, the Company completed the issuance and sale of 1,000,000 shares of the Company’s "
                    "common stock, pursuant to the Purchase Agreement, for an aggregate purchase price of $50,000,000 in "
                    "cash. The Shares were issued and sold to NVIDIA in a private placement. ")
    r2 = classify(closed)
    assert (r2.tier, r2.outcome) == (C.TIER_SEC_CLOSING, C.OUTCOME_PROPOSAL)


# ── §7 T3 ───────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("sentence, is_ioi", [
    ("NVIDIA Corporation has indicated an interest in purchasing up to $50 million of shares in this offering.", True),
    ("NVIDIA Corporation, an existing stockholder, has indicated an interest in purchasing up to $50 million of "
     "shares in this offering.", True),
    ("Entities affiliated with NVIDIA Corporation have indicated an interest in purchasing up to $50 million of "
     "shares in this offering.", True),
    ("Following discussions with NVIDIA Corporation, certain of our existing stockholders have indicated an "
     "interest in purchasing up to $50 million of shares in this offering.", False),
    ("Certain investors, excluding NVIDIA Corporation, have indicated an interest in purchasing up to $50 million "
     "of shares in this offering.", False),
    ("NVIDIA Corporation has not indicated an interest in purchasing any shares in this offering.", False),
    ("ARK Investment Management LLC and Duquesne Family Office LLC have indicated an interest in purchasing up to "
     "$60.0 million of shares; NVIDIA Corporation is a partner of ours.", False),
])
def test_the_member_must_be_the_subject_of_indicated_an_interest(sentence, is_ioi):
    r = classify(sentence + " " + PROSPECTUS, form="424B4", items=(), file_type="424B4")
    assert (r.tier == C.TIER_IOI) is is_ioi, (sentence, r)
    if is_ioi:
        assert r.outcome == C.OUTCOME_HEADS_UP and r.amount_usd == Decimal(50_000_000)
        assert r.group_term is None and r.group_members == ()
    else:
        assert r.outcome != C.OUTCOME_PROPOSAL


def test_a_group_defined_inside_a_later_partys_parenthetical_is_found():
    text = ("NVIDIA Corporation (“NVIDIA”), Advanced Micro Devices, Inc. (“AMD”) and Broadcom Inc. (“Broadcom” and, "
            "together with NVIDIA and AMD, the “Cornerstone Investors”) are existing partners. The Cornerstone "
            "Investors have, severally and not jointly, indicated an interest in purchasing up to an aggregate of "
            "$5.0 billion of shares of our Class A common stock in this offering. " + PROSPECTUS)
    for slug in ("nvidia", "amd", "broadcom"):
        r = classify(text, slug=slug, form="424B5", items=(), file_type="424B5")
        assert (r.tier, r.group_term) == (C.TIER_IOI, "Cornerstone Investors"), (slug, r)
        assert set(r.group_members) == {"NVIDIA Corporation", "Advanced Micro Devices, Inc.", "Broadcom Inc."}
    micron = classify(text, slug="micron", form="424B5", items=(), file_type="424B5")
    assert micron.tier != C.TIER_IOI


def test_together_with_its_affiliates_is_the_partys_own_term_not_a_group():
    text = ("NVIDIA Corporation (together with its affiliates, “NVIDIA”) has agreed to purchase up to $100 million of "
            "shares of our common stock. " + PROSPECTUS)
    r = classify(text, form="424B4", items=(), file_type="424B4")
    assert r.outcome == C.OUTCOME_PROPOSAL and r.group_term is None


# ── §8 T4 ───────────────────────────────────────────────────────────────────────────────


def test_a_directed_issuance_to_someone_else_is_not_the_members():
    text = ("Nokia Corporation (“Nokia”) completed a directed share issuance to Beta Capital Oy of 1 000 000 new Nokia "
            "shares, and NVIDIA Corporation is a customer. ")
    r = classify(text, conformed="NOKIA CORP", form="6-K", items=(), file_type="6-K")
    assert r.tier != C.TIER_DIRECTED


def test_a_directed_issuance_only_counts_in_a_6k():
    text = ("Following the completion of the directed share issuance to NVIDIA Corporation (“NVIDIA”), announced by "
            "Nokia Corporation (“Nokia”) on 28 October 2025, a total of 166 389 351 new Nokia shares were registered. ")
    assert classify(text, conformed="NOKIA CORP", form="6-K", items=(), file_type="6-K").tier == C.TIER_DIRECTED
    assert classify(text, conformed="NOKIA CORP", form="8-K", items=("8.01",)).tier != C.TIER_DIRECTED


def test_a_space_grouped_count_is_read_from_its_first_group_only():
    text = ("Following the completion of the directed share issuance to NVIDIA Corporation (“NVIDIA”), announced by "
            "Nokia Corporation (“Nokia”), a total of 166 389 351 new Nokia shares were registered. ")
    r = classify(text, conformed="NOKIA CORP", form="6-K", items=(), file_type="6-K")
    assert r.shares == 166_389_351


# ── §9 EX-4 exhibits ────────────────────────────────────────────────────────────────────


def test_an_ex4_the_member_is_a_party_to_but_not_a_warrant_is_a_heads_up():
    text = ("INVESTORS’ RIGHTS AGREEMENT. This Agreement is made by and between Acme Corporation (the “Company”) and "
            "NVIDIA Corporation (the “Investor”). ")
    r = classify(text, file_type="EX-4.2", items=(), form="S-1")
    assert (r.reason, r.outcome) == ("no_template", C.OUTCOME_HEADS_UP)


def test_an_ex4_naming_the_member_outside_the_parties_is_skipped():
    text = ("INDENTURE dated as of March 1, 2026 between Acme Corporation (the “Company”) and U.S. Bank Trust Company, "
            "National Association, as trustee. The Company sells products to NVIDIA Corporation and others. ")
    r = classify(text, file_type="EX-4.1", items=("1.01",))
    assert (r.reason, r.outcome) == ("exhibit_skipped", C.OUTCOME_SKIPPED)


def test_a_pre_funded_warrant_exhibit_carries_its_label():
    text = ("PRE-FUNDED WARRANT TO PURCHASE COMMON STOCK. Acme Corporation, a Delaware corporation (the “Company”), "
            "hereby certifies that, for value received, NVIDIA Corporation (the “Holder”) is entitled to purchase "
            "1,000,000 shares of Common Stock. Issue Date: March 2, 2026. ")
    r = classify(text, file_type="EX-4.3", items=("1.01", "3.02"))
    assert (r.instrument_label, r.outcome) == ("(pre-funded warrant)", C.OUTCOME_PROPOSAL), r
    assert r.event_date == date(2026, 3, 2)


# ── §10 names ───────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("a, b", [
    ("INTEL CORP", "Intel Corporation"), ("ELI LILLY & Co", "Eli Lilly and Company"),
    ("TAIWAN SEMICONDUCTOR MANUFACTURING CO LTD", "Taiwan Semiconductor Manufacturing"),
    ("SAMSUNG ELECTRONICS CO LTD /FI", "Samsung Electronics"), ("Ionetix Corp / DE /", "Ionetix Corporation"),
    ("NOKIA CORP", "Nokia Oyj"), ("AMAZON COM INC", "Amazon.com, Inc."), ("COHERENT CORP.", "Coherent Corp."),
    ("Marvell Technology, Inc.", "MARVELL TECHNOLOGY INC"),
])
def test_company_keys_equal_across_secs_and_the_documents_spelling(a, b):
    assert T._company_key(a) == T._company_key(b) != ""


@pytest.mark.parametrize("a, b", [("NVIDIA CORP", "Nvidia Holdings Corp"), ("Meta Platforms, Inc.", "Meta Materials Inc."),
                                  ("Eli Lilly and Company", "Eli Lilly"), ("Intel Corp", "Intel Capital Corp")])
def test_different_companies_have_different_keys(a, b):
    assert T._company_key(a) != T._company_key(b)


def test_a_document_that_defines_another_company_is_a_name_mismatch():
    r = classify(BASE, conformed="BETA HOLDINGS INC")
    assert (r.reason, r.outcome) == ("s2_name_mismatch", C.OUTCOME_HEADS_UP)
    assert r.company_name is None and r.value_basis is None


def test_two_different_companies_called_the_company_fail_closed():
    text = BASE + "Beta Holdings, Inc. (the “Company”) is not a party to the Purchase Agreement. "
    r = classify(text)
    assert (r.reason, r.outcome) == ("s2_name_mismatch", C.OUTCOME_HEADS_UP)


def test_an_all_caps_defining_clause_is_unusable_as_a_name():
    r = classify(BASE.replace("Acme Corporation (the", "ACME CORPORATION (the"))
    assert (r.reason, r.outcome) == ("name_unusable", C.OUTCOME_HEADS_UP)


@pytest.mark.parametrize("clause, name", [
    ("Coherent Corp. (“Coherent” or the “Company”)", "Coherent"),
    ("Synopsys, Inc. (“Synopsys”)", "Synopsys"),
    ("Marvell Technology, Inc., a Delaware corporation (the “Corporation”)", "Marvell Technology"),
])
def test_the_defining_clause_shapes(clause, name):
    conformed = {"Coherent": "COHERENT CORP.", "Synopsys": "SYNOPSYS INC",
                 "Marvell Technology": "Marvell Technology, Inc."}[name]
    term = clause.split("“")[1].split("”")[0]
    possessive = f"{term}’s" if term not in ("Synopsys",) else "Synopsys’"
    if term in ("Company", "Corporation"):
        possessive = f"the {term}’s"
    text = (HEAD + f"On March 2, 2026, {clause} completed the issuance and sale of 1,000,000 shares of {possessive} "
            "common stock to NVIDIA Corporation for an aggregate purchase price of $50,000,000 in cash. ")
    r = classify(text, conformed=conformed)
    assert (r.outcome, r.company_name) == (C.OUTCOME_PROPOSAL, name), (clause, r)


def test_the_we_us_our_clause_names_a_prospectus_issuer():
    text = ("NVIDIA Corporation has agreed to purchase up to $100 million of shares of our common stock. "
            "In this prospectus, “Acme,” “we,” “us” and “our” refer to Acme Corporation and, unless stated "
            "otherwise, not its subsidiaries. ")
    r = classify(text, form="424B4", items=(), file_type="424B4")
    assert (r.outcome, r.company_name) == (C.OUTCOME_PROPOSAL, "Acme")


def test_the_members_own_filing_is_a_heads_up_whatever_it_says():
    for conformed in ("NVIDIA CORP", "NVIDIA Corporation", "nvidia corp"):
        r = classify(BASE, conformed=conformed)
        assert (r.reason, r.outcome) == ("member_filed", C.OUTCOME_HEADS_UP)


# ── §11 inputs ──────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("kw", [
    dict(text=None), dict(text=b"bytes"), dict(member_aliases="NVIDIA Corporation"), dict(member_aliases=[]),
    dict(member_aliases=[""]), dict(member_aliases=[None]), dict(member_aliases=["NVIDIA\nCorporation"]),
    dict(other_member_aliases="Broadcom Inc."), dict(other_member_aliases=[3]), dict(filer_conformed_name=None),
    dict(form=None), dict(file_type=5), dict(filed_on=datetime(2026, 3, 10)), dict(filed_on="2026-03-10"),
    dict(items="3.02"), dict(items=None), dict(cpu_budget_seconds=0), dict(cpu_budget_seconds=-1),
    dict(cpu_budget_seconds=float("nan")), dict(cpu_budget_seconds=float("inf")), dict(cpu_budget_seconds=True),
    dict(cpu_budget_seconds=61), dict(cpu_budget_seconds="2"),
])
def test_a_callers_bug_raises_value_error(kw):
    args = dict(text=BASE, member_aliases=["NVIDIA Corporation"], filer_conformed_name="ACME CORP", form="8-K",
                items=["3.02"], filed_on=date(2026, 3, 10), file_type="8-K", other_member_aliases=[],
                cpu_budget_seconds=2.0)
    args.update(kw)
    with pytest.raises(ValueError):
        T.classify_document(**args)


@pytest.mark.parametrize("text, why", [
    ("", "empty document"), ("   \n ", "empty document"),
    ("Acme Corporation completed the sale of shares to Beta Capital LLC.", "not in the text"),
])
def test_a_document_without_the_member_is_skipped(text, why):
    r = classify(text)
    assert (r.reason, r.outcome) == ("no_template", C.OUTCOME_SKIPPED) and why in r.detail


def test_the_alias_is_matched_case_insensitively_and_word_bounded():
    shouted = BASE.replace("NVIDIA Corporation (“NVIDIA”)", "NVIDIA CORPORATION (“NVIDIA”)")
    assert classify(shouted).outcome == C.OUTCOME_PROPOSAL
    glued = "Acme sold shares to XNVIDIA Corporations Ltd. in 2026."
    assert "not in the text" in classify(glued).detail


def test_a_document_with_too_many_deal_mentions_is_a_heads_up(monkeypatch):
    monkeypatch.setattr(T, "MAX_DEAL_SENTENCES", 3)
    text = "".join(f"NVIDIA Corporation purchased {i},000 shares of our common stock on day {i}. " for i in range(1, 8))
    r = classify(text, form="424B4", items=(), file_type="424B4")
    assert (r.reason, r.outcome) == ("no_template", C.OUTCOME_HEADS_UP) and "too many" in r.detail


# ── §12 the CPU budget ──────────────────────────────────────────────────────────────────


def test_an_overrun_is_a_heads_up_named_cpu_budget(monkeypatch, caplog):
    ticks = iter(range(0, 10 ** 6, 5))
    monkeypatch.setattr(T, "_cpu_clock", lambda: float(next(ticks)))
    with caplog.at_level(logging.WARNING, logger=T.__name__):
        r = classify(BASE, budget=2.0)
    assert (r.tier, r.reason, r.outcome, r.detail) == (C.TIER_OTHER, "no_template", C.OUTCOME_HEADS_UP, "cpu budget")
    assert r.spans == () and r.amount_usd is None
    assert any("[tc-stakes-template-budget]" in rec.getMessage() for rec in caplog.records)


def test_a_budget_spent_in_the_last_step_still_returns_a_heads_up(monkeypatch):
    """Whatever step the clock runs out in, the result is the budget heads-up — never a partial row."""
    calls = {"n": 0}
    real = T._cpu_clock

    for limit in range(1, 40):
        calls["n"] = 0

        def clock(limit=limit) -> float:
            calls["n"] += 1
            return 0.0 if calls["n"] <= limit else 100.0

        monkeypatch.setattr(T, "_cpu_clock", clock)
        r = classify(BASE)
        assert r.outcome in (C.OUTCOME_HEADS_UP, C.OUTCOME_PROPOSAL)
        if r.outcome == C.OUTCOME_HEADS_UP:
            assert r.detail == "cpu budget"
    monkeypatch.setattr(T, "_cpu_clock", real)


# ── §13 sentence and span helpers ───────────────────────────────────────────────────────


def _sentences(text: str) -> List[str]:
    c = T._Classifier(text=text, aliases=("NVIDIA Corporation",), others=(), conformed="ACME", form="8-K",
                      items=frozenset(), filed_on=date(2026, 1, 1), file_type="8-K", budget=T._Budget(10))
    c.starts = c._segment()
    return [text[slice(*c._bounds(k))] for k in range(len(c.starts))]


def test_sentences_do_not_split_at_abbreviations_or_decimals():
    text = ("On March 2, 2026, Coherent Corp. (“Coherent”) sold 7.5 million shares to NVIDIA Corp. in the U.S. "
            "market at $256.80 per share. The Shares were issued under Section 4(a)(2). No. 1 is next.")
    assert _sentences(text) == [
        "On March 2, 2026, Coherent Corp. (“Coherent”) sold 7.5 million shares to NVIDIA Corp. in the U.S. market "
        "at $256.80 per share.",
        "The Shares were issued under Section 4(a)(2).",
        "No. 1 is next.",
    ]


def test_a_sentence_with_no_boundary_is_force_split_at_1500_characters():
    text = ("word " * 700).strip()
    parts = _sentences(text)
    assert len(parts) >= 3 and all(len(p) <= T.SENTENCE_MAX_CHARS for p in parts)
    assert " ".join(parts) == text  # nothing lost, nothing doubled
    unspaced = "x" * 4000
    parts = _sentences(unspaced)
    assert [len(p) for p in parts] == [1500, 1500, 1000]


def test_the_force_cut_never_falls_between_digit_groups():
    text = "y" * 1300 + " 166 389 351 " * 30
    cut = T._force_cut(text, 0)
    assert cut <= T.SENTENCE_MAX_CHARS
    assert not (text[cut - 2:cut - 1].isdigit() and text[cut:cut + 1].isdigit())


def test_long_sentences_are_cut_into_verifiable_clause_chunks():
    text = BASE.replace("representing a price per share of $50.00.", "representing a price per share of $50.00, " +
                        "and the Company agreed to use the proceeds for general corporate purposes, including " * 6 +
                        "working capital.")
    sentence = max(_sentences(text), key=len)
    start = text.index(sentence)
    chunks = T._clause_chunks(text, start, start + len(sentence))
    assert chunks[0][0] == start and chunks[-1][1] == start + len(sentence)
    for a, b in chunks:
        assert 40 <= b - a <= 400 and filing_text.verify_peg_span(text[a:b].strip(), text)


def test_money_is_read_on_the_full_text():
    text = "for an aggregate purchase price of $2,000,000,000.00 in cash"
    m = T._parse_money(text, text.index("$"))
    assert m is not None and m.usd and m.value == Decimal(2_000_000_000) and not m.per_share
    per = T._parse_money("at $23.28 per share", 3)
    assert per.per_share and per.value == Decimal("23.28")
    assert T._parse_money("of €2 billion", 3).usd is False
    assert T._parse_money("of 2 billion euros", 3).usd is False
    assert T._parse_money("of $2,000,0000 in cash", 3) is None  # malformed
    assert T._parse_money("of $0 in cash", 3) is None


# ── §14 linear time: every compiled pattern of the module ───────────────────────────────
#
# ``re`` holds the GIL, so a slow pattern in the finder's worker thread stalls the event loop.
# Every module-level pattern runs a bare ``finditer`` over ~1M characters of each table-like or
# degenerate unit below and must finish under 0.5 s of THREAD CPU time (collector off; a slow
# sample is re-measured once before it counts). Patterns built at run time from an alias or a
# defined term are ``re.escape``d literals. Never "fix" a red run by raising the bound or
# dropping a unit: rewrite the pattern so it starts on a literal and its gaps are bounded.

_SWEEP_CHARS = 1_000_000
_SWEEP_LIMIT_S = 0.5
_SWEEP_UNITS = (
    "| $ 1,000,000 | (2,000) | 9.01 | ", "1", "1,", "166 389 351 ", "\u00a0", "(“A", "a.", "Inc. ",
    "NVIDIA Corporation, ", ", a ", "On March 1, 2026, ", "Total 40,000,000 shares ", " ",
)


def _module_patterns() -> List[Tuple[str, "re.Pattern[str]"]]:
    return [(name, val) for name, val in vars(T).items() if isinstance(val, re.Pattern)]


def _cpu(rx: "re.Pattern[str]", text: str) -> float:
    was = gc.isenabled()
    gc.disable()
    try:
        t0 = time.thread_time()
        for _ in rx.finditer(text):
            pass
        return time.thread_time() - t0
    finally:
        if was:
            gc.enable()


def _slow(pats: List[Tuple[str, "re.Pattern[str]"]], units: Sequence[str], chars: int,
          limit: float) -> List[Tuple[str, str, float]]:
    out = []
    texts = [(u, (u * (chars // len(u) + 1))[:chars]) for u in units]
    for name, rx in pats:
        for unit, text in texts:
            spent = _cpu(rx, text)
            if spent >= limit:
                spent = min(spent, _cpu(rx, text))  # a scheduler hiccup does not repeat
            if spent >= limit:
                out.append((name, unit, round(spent, 3)))
    return out


#: The patterns the 2026-10-28 review fixes added or changed (S2-1 … S2-10). Named here so a
#: sweep that stopped seeing one of them (renamed, built at call time) fails loudly.
_REVIEW_PATTERNS = (
    "_SALE_ACTIVE_RE", "_BUYER_VERB_RE", "_OTHER_TO_RE", "_COUNT_CONTEXT_RE", "_SIGNING_RE",
    "_SIGNING_VERB_RE", "_CLOSING_VERB_RE", "_CLOSING_ON_RE", "_CLOSING_DATE_TERM_RE", "_OPTION_TO_RE",
    "_OTHER_PARTY_CLAUSE_RE", "_NOT_THE_MEMBER_RE", "_IOI_OBJECT_BEFORE_RE", "_IOI_AFFILIATE_BEFORE_RE",
    "_IOI_LIST_BEFORE_RE", "_PROPOSED_RE", "_UNDERTAKING_RE", "_NEGATED_BEFORE_RE", "_OUR_CLASS_RE",
    "_OWNED_AFTER_TERM_RE", "_DIVESTITURE_RE", "_FORM_OF_RE", "_PLACEHOLDER_DATE_RE", "_EX4_NOT_DONE_RE",
    "_USD_MARK_RE",
    # Review A (2026-10-29): the T2 object's second purchase verb and party tokens, the T1 date
    # clause, the T4 completion clause.
    "_SECOND_PURCHASE_RE", "_TOKEN_RE", "_REDUCED_RELATIVE_BEFORE_RE", "_PREVIOUSLY_RE",
    "_COMPLETED_BEFORE_RE", "_COMPLETED_PASSIVE_RE", "_SUBORDINATE_LEAD_RE", "_CONDITION_TAIL_RE",
)
#: Units that trigger the review patterns' literal starts (run at 250k characters; a quadratic
#: pattern is seconds there, these are milliseconds).
_SWEEP_UNITS_REVIEW = (
    "_", "[__], ", "closing ", "sold ", ", to ", "of the Company’s ", "Neither ", "has agreed ",
    "$2bn ", "convertible into ", "Form of ", "affiliated with ",
    # Review A (2026-10-29)
    "will purchase ", "intends to ", "Agreement entered into ", "As previously disclosed ", "has completed the ",
    "has been completed ", "as soon as ", "subject to ", "x" * 79 + " ",
)


def test_every_compiled_pattern_is_linear_on_million_character_inputs():
    pats = _module_patterns()
    assert len(pats) >= 100, len(pats)  # the sweep is not vacuous: it sees the module's patterns
    assert set(_REVIEW_PATTERNS) <= {name for name, _ in pats}
    assert _slow(pats, _SWEEP_UNITS, _SWEEP_CHARS, _SWEEP_LIMIT_S) == []


def test_every_compiled_pattern_is_linear_on_the_review_patterns_triggers():
    assert _slow(_module_patterns(), _SWEEP_UNITS_REVIEW, 250_000, _SWEEP_LIMIT_S / 4) == []


def test_the_linearity_guard_is_not_vacuous():
    """A quadratic pattern (a nested repeat that fails at the end) must fail the same check."""
    quadratic = re.compile(r"(?:[a-z]\.)+Z")
    assert _slow([("quadratic", quadratic)], ("a.",), 30_000, 0.05) != []


#: Ten alias definitions of thirty distinct quoted terms each (S2-10): 300 distinct terms,
#: each a whole-text scan before the cap existed (about 10 s on these 2,000,000 characters).
_MANY_TERMS_UNIT = "".join(
    "NVIDIA Corporation (" + ", ".join(f"“T{j}x{i}”" for i in range(30)) + ") is named here. " for j in range(10)
)


@pytest.mark.parametrize("unit", [
    "| NVIDIA Corporation | 1,000,000 | $ 2,000,000 | ",
    "NVIDIA Corporation GPUs power our data centers. ",
    "(the “Company”) NVIDIA Corporation (“NVIDIA”) ",
    "NVIDIA Corporation completed the sale of 1,000 shares to NVIDIA Corporation and ",
    _MANY_TERMS_UNIT,
])
def test_a_huge_degenerate_document_finishes_well_inside_the_budget(unit):
    text = (unit * (2_000_000 // len(unit) + 1))[:2_000_000]
    t0 = time.process_time()
    r = classify(text, budget=30.0)
    assert time.process_time() - t0 < T.DEFAULT_CPU_BUDGET_SECONDS
    assert r.outcome in T.TEMPLATE_OUTCOMES


# ── §5b groups and longer names as purchasers ───────────────────────────────────────────


GROUP_SPA = (
    HEAD + "On March 2, 2026, Acme Corporation (the “Company”) entered into a Securities Purchase Agreement with "
    "NVIDIA Corporation and Beta Capital LLC (each, a “Purchaser” and together, the “Purchasers”). "
)


@pytest.mark.parametrize("clause", [
    "On March 2, 2026, the Company completed the issuance and sale of 1,000,000 shares of the Company’s common "
    "stock to the Purchasers for an aggregate purchase price of $50,000,000 in cash.",
    "On March 2, 2026, the Purchasers purchased 1,000,000 shares of the Company’s common stock for an aggregate "
    "purchase price of $50,000,000 in cash.",
])
def test_a_sale_to_a_defined_group_that_lists_the_member_is_a_heads_up(clause):
    r = classify(GROUP_SPA + clause + " ")
    assert (r.outcome, r.tier) == (C.OUTCOME_HEADS_UP, C.TIER_SEC_CLOSING), r
    assert "several purchasers" in r.detail


def test_a_purchaser_whose_name_only_starts_with_the_alias_is_not_the_member():
    r = classify(BASE.replace(TO_NVIDIA, "to the NVIDIA Corporation Employee Benefit Plan, for"))
    assert r.outcome == C.OUTCOME_HEADS_UP and "not exactly the member" in r.detail, r


# ── §15 the 2026-10-28 review (S2-1 … S2-10) ────────────────────────────────────────────
#
# Each finding: the reviewer's must-flag shapes (never a proposal carrying the wrong fact) and
# the honest twins that must still pass (the over-block guard). The recorded documents of §2
# pin the real-filing side of every twin.

SALE_TO_NVIDIA = (HEAD + "On March 2, 2026, Acme Corporation (the “Company”) completed the issuance and sale of 1,000,000 "
                  "shares of the Company’s common stock to NVIDIA Corporation (“NVIDIA”). ")


# S2-1 — the T1 price is bound to the member's own clause.

@pytest.mark.parametrize("after", [
    "Concurrently, the Company sold 9,000,000 shares of its common stock to Beta Capital LLC for an aggregate "
    "purchase price of $450,000,000 in cash. ",
    "Concurrently, 9,000,000 shares of the Company’s common stock were sold to Beta Capital LLC for an aggregate "
    "purchase price of $450,000,000 in cash. ",
    "The Company also issued 9,000,000 shares to Beta Capital LLC for an aggregate purchase price of $450,000,000 "
    "in cash. ",
    "Beta Capital LLC also paid an aggregate purchase price of $450,000,000 for its shares. ",
    "The Company also granted to Beta Capital LLC warrants for an aggregate purchase price of $450,000,000. ",
])
def test_another_sales_price_in_the_next_sentence_is_never_the_members(after):
    """The deal window stops at a sentence holding a sale, issue or purchase to ANYONE: the
    member's closing stays a proposal of 1,000,000 shares with no amount — never $450,000,000."""
    r = classify(SALE_TO_NVIDIA + after)
    assert (r.tier, r.outcome, r.shares, r.closed) == (C.TIER_SEC_CLOSING, C.OUTCOME_PROPOSAL, 1_000_000, True), r
    assert r.amount_usd is None and r.value_basis is None, r


def test_another_issue_beside_a_restated_purchaser_is_never_the_members_price():
    r = classify(SALE_TO_NVIDIA + "The Shares were issued and sold to NVIDIA, and the Company issued a warrant to Beta "
                 "Capital LLC for an aggregate purchase price of $450,000,000. ")
    assert r.outcome != C.OUTCOME_PROPOSAL or r.amount_usd is None, r
    assert r.amount_usd is None and r.value_basis is None


def test_a_cut_window_says_the_next_sentences_price_is_another_deals():
    r = classify(SALE_TO_NVIDIA + "Concurrently, the Company sold 9,000,000 shares of its common stock to Beta Capital "
                 "LLC for an aggregate purchase price of $450,000,000 in cash. ")
    assert r.outcome == C.OUTCOME_PROPOSAL and r.amount_usd is None
    assert "another deal's" in r.detail


@pytest.mark.parametrize("text, why", [
    # The placement's total: a second share count in the window.
    (SALE_TO_NVIDIA + "In the private placement, the Company placed an aggregate of 10,000,000 shares for an aggregate "
     "purchase price of $500,000,000 in cash. ", "a second share count"),
    # A second buyer past the purchaser stop (" pursuant"), with its own count …
    (HEAD + "On March 2, 2026, Acme Corporation (the “Company”) completed the issuance and sale of 1,000,000 shares of "
     "the Company’s common stock to NVIDIA Corporation pursuant to the Purchase Agreement and 9,000,000 shares to Beta "
     "Capital LLC, for an aggregate purchase price of $500,000,000 in cash. ", "a second share count"),
    # … or without one.
    (HEAD + "On March 2, 2026, Acme Corporation (the “Company”) completed the issuance and sale of 1,000,000 shares of "
     "the Company’s common stock to NVIDIA Corporation pursuant to the Purchase Agreement and to Beta Capital LLC "
     "pursuant to the Beta Agreement, for an aggregate purchase price of $500,000,000 in cash. ", "another purchaser"),
    # Another buyer's clause before the price.
    (HEAD + "On March 2, 2026, Acme Corporation (the “Company”) completed the issuance and sale of 1,000,000 shares of "
     "the Company’s common stock to NVIDIA Corporation pursuant to the Purchase Agreement, while Beta Capital LLC "
     "acquired its shares for an aggregate purchase price of $450,000,000 in cash. ", "another buyer"),
])
def test_a_price_not_bound_to_the_member_is_a_heads_up(text, why):
    r = classify(text)
    assert (r.tier, r.reason, r.outcome) == (C.TIER_SEC_CLOSING, "t1_closing", C.OUTCOME_HEADS_UP), r
    assert r.amount_usd is None and r.value_basis is None
    assert "price not bound to the member" in r.detail and why in r.detail, r.detail


@pytest.mark.parametrize("text", [
    # The link the sale took its purchaser from carries the price.
    HEAD + "On March 2, 2026, Acme Corporation (the “Company”) completed the issuance and sale of 1,000,000 shares of "
    "the Company’s common stock (the “Shares”). The Shares were issued and sold to NVIDIA Corporation for an aggregate "
    "purchase price of $50,000,000 in cash. ",
    # The member's own conversion shares are no second sale (the Marvell shape).
    SALE_TO_NVIDIA + "The shares were sold at a price of $50.00 per share for an aggregate purchase price of $50,000,000 "
    "in cash and are convertible into a maximum of 2,000,000 shares of the Company’s Series B stock. ",
    # The shares outstanding after the sale are no sale either.
    SALE_TO_NVIDIA.replace("(“NVIDIA”). ", "(“NVIDIA”) for an aggregate purchase price of $50,000,000 in cash, after "
                           "which 120,000,000 shares of common stock were outstanding. "),
    # A placement agent named with the price is no purchaser.
    BASE.replace("in cash, representing", "in cash, with Morgan Stanley & Co. LLC acting as placement agent, "
                 "representing"),
    # The member's holding after the closing is no second sale — in the next sentence or the same one.
    BASE + "Following the closing, NVIDIA owns 10,000,000 shares of the Company’s common stock. ",
    BASE + "Following the closing, the Company had 120,000,000 shares of common stock. ",
    # The member's own relative clause carries the price.
    SALE_TO_NVIDIA.replace("(“NVIDIA”). ", "(“NVIDIA”), which paid an aggregate purchase price of $50,000,000 in cash. "),
    # A security's capitalised title is no purchaser ("Warrants to Purchase Common Stock").
    BASE.replace("in cash, representing", "in cash (no Warrants to Purchase Common Stock were issued), representing"),
    BASE.replace("in cash, representing", "in cash, after which NVIDIA beneficially owns 10,000,000 shares of the "
                 "Company’s common stock, representing"),
])
def test_a_price_bound_to_the_member_stays_a_proposal(text):
    r = classify(text)
    assert (r.tier, r.outcome) == (C.TIER_SEC_CLOSING, C.OUTCOME_PROPOSAL), r
    assert r.amount_usd == Decimal(50_000_000) and r.value_basis == "invested" and r.closed is True


def test_the_deal_window_exempts_only_the_link_the_sale_used():
    text = (HEAD + "On March 2, 2026, Acme Corporation (the “Company”) completed the issuance and sale of 1,000,000 shares "
            "of the Company’s common stock (the “Shares”). The Shares were issued and sold to NVIDIA Corporation for an "
            "aggregate purchase price of $50,000,000 in cash. ")
    c = T._Classifier(text=text, aliases=("NVIDIA Corporation",), others=(), conformed="ACME CORP", form="8-K",
                      items=frozenset({"3.02"}), filed_on=date(2026, 3, 10), file_type="8-K", budget=T._Budget(10))
    c.starts = c._segment()
    ns, ne = c._bounds(2)
    link = text.index("were issued and sold")
    assert c._holds_other_sale(ns, ne, frozenset()) is True
    assert c._holds_other_sale(ns, ne, frozenset({link})) is False
    assert c._holds_other_sale(ns, ne, frozenset({link + 200})) is True  # another link's exemption is not this one's


# S2-2 — a purchaser link must speak of the same securities.

def test_a_preferred_link_is_never_the_buyer_of_a_common_sale():
    text = (HEAD + "On March 2, 2026, Acme Corporation (the “Company”) completed the issuance and sale of 1,000,000 shares "
            "of the Company’s common stock (the “Shares”) in a registered direct offering for an aggregate purchase price "
            "of $50,000,000. Separately, shares of the Company’s Series A Preferred Stock were issued and sold to NVIDIA "
            "Corporation in a private placement. ")
    r = classify(text)
    assert r.outcome == C.OUTCOME_HEADS_UP and r.amount_usd is None and r.shares is None, r
    assert (r.tier, r.instrument_label) == (C.TIER_INSTRUMENT, "(preferred)")  # the link's own securities
    assert "without its terms" in r.detail


@pytest.mark.parametrize("first, link", [
    ("to Beta Capital LLC", "Separately, shares of the Company’s Series A Preferred Stock were issued and sold to NVIDIA "
                            "Corporation in a private placement."),
    ("(the “Shares”)", "The shares of Class B common stock were issued and sold to NVIDIA Corporation in a private "
                       "placement."),
])
def test_an_unused_link_near_a_sale_is_a_heads_up_never_dropped(first, link):
    """The reverse of the above: the member's real purchase sits in a link no sale used — it is the
    owner's to read, never skipped because a sale verb is nearby."""
    text = (HEAD + "On March 2, 2026, Acme Corporation (the “Company”) completed the issuance and sale of 1,000,000 shares "
            f"of the Company’s Class A common stock {first} for an aggregate purchase price of $50,000,000. {link} ")
    r = classify(text)
    assert r.outcome == C.OUTCOME_HEADS_UP and r.amount_usd is None, r


@pytest.mark.parametrize("noun, link_subject, tier", [
    ("Series A Convertible Preferred Stock (the “Series A Preferred Stock”)", "The shares of Series A Preferred Stock",
     C.TIER_INSTRUMENT),
    ("common stock", "The shares of common stock", C.TIER_SEC_CLOSING),
    ("Class A common stock (the “Shares”)", "The Shares", C.TIER_SEC_CLOSING),
])
def test_a_link_of_the_same_securities_still_names_the_buyer(noun, link_subject, tier):
    text = (HEAD + f"On March 2, 2026, Acme Corporation (the “Company”) completed the issuance and sale of 1,000,000 shares "
            f"of the Company’s {noun} for an aggregate purchase price of $50,000,000. {link_subject} were issued and sold "
            "to NVIDIA Corporation in a private placement. ")
    r = classify(text)
    assert (r.tier, r.outcome, r.amount_usd, r.shares) == (tier, C.OUTCOME_PROPOSAL, Decimal(50_000_000), 1_000_000), r


def test_a_link_restating_the_sales_own_purchaser_is_no_second_deal():
    """Lumentum / Marvell: the sale names NVIDIA directly and "The shares of Series A Preferred Stock
    were issued and sold to NVIDIA" restates it two sentences later — one proposal, no stray deal."""
    text = (HEAD + "On March 2, 2026, Acme Corporation (the “Company”) completed the issuance and sale of 1,000,000 shares "
            "of the Company’s Series A Convertible Preferred Stock (the “Series A Preferred Stock”) to NVIDIA Corporation "
            "(“NVIDIA”) for an aggregate purchase price of $50,000,000. The Company will use the proceeds for general "
            "purposes. The shares of Series A Preferred Stock were issued and sold to NVIDIA in a private placement. ")
    c = T._Classifier(text=text, aliases=("NVIDIA Corporation",), others=(), conformed="ACME CORP", form="8-K",
                      items=frozenset({"3.02"}), filed_on=date(2026, 3, 10), file_type="8-K", budget=T._Budget(10))
    c.company = c._find_company()
    c._collect_terms(c._find_aliases())
    c.starts = c._segment()
    assert [link.kind for links in c._sold_to(list(range(len(c.starts)))).values() for link in links] == ["exact"]
    r = classify(text)
    assert (r.tier, r.outcome) == (C.TIER_INSTRUMENT, C.OUTCOME_PROPOSAL), r
    # … but a restating link that names several purchasers contradicts the sale: the owner's call.
    several = text.replace("sold to NVIDIA in a private", "sold to NVIDIA and Beta Capital LLC in a private")
    assert classify(several).outcome == C.OUTCOME_HEADS_UP


# S2-3 — the fallback closing date comes only from a closing clause.

SIGNED_BEFORE = (HEAD + "As previously disclosed, on September 15, 2025, Acme Corporation (the “Company”) entered into a "
                 "Securities Purchase Agreement (the “Purchase Agreement”) with NVIDIA Corporation (“NVIDIA”). The Company "
                 "completed the issuance and sale of 1,000,000 shares of the Company’s common stock to NVIDIA for an "
                 "aggregate purchase price of $50,000,000 in cash. ")


@pytest.mark.parametrize("text", [
    SIGNED_BEFORE,  # the signing sentence's date is never the closing date of a sale filed months later
    HEAD + "As previously disclosed, on September 15, 2025, Acme Corporation (the “Company”) entered into a Securities "
    "Purchase Agreement with NVIDIA Corporation (“NVIDIA”), and the Company completed the issuance and sale of 1,000,000 "
    "shares of the Company’s common stock to NVIDIA for an aggregate purchase price of $50,000,000 in cash. ",
    SIGNED_BEFORE + "The closing of the Beta Capital sale occurred on April 1, 2026. ",  # another party's closing
    SIGNED_BEFORE + "The transaction with Beta Capital LLC closed on April 1, 2026. ",
    SIGNED_BEFORE + "Beta closed on April 1, 2026. ",
])
def test_a_signing_date_is_never_the_closing_date(text):
    r = classify(text)
    assert r.outcome == C.OUTCOME_HEADS_UP and r.event_date is None, r
    assert "no closing date" in r.detail
    assert r.event_date != date(2025, 9, 15)


@pytest.mark.parametrize("text, filed_on, when", [
    (SIGNED_BEFORE + "The closing occurred on March 2, 2026. ", date(2026, 3, 10), date(2026, 3, 2)),
    (SIGNED_BEFORE + "The private placement closed on March 2, 2026. ", date(2026, 3, 10), date(2026, 3, 2)),
    (SIGNED_BEFORE + "The closing under the Securities Act occurred on March 2, 2026. ", date(2026, 3, 10),
     date(2026, 3, 2)),
    (SIGNED_BEFORE + "The closing under Section 2 of the Purchase Agreement occurred on March 2, 2026. ",
     date(2026, 3, 10), date(2026, 3, 2)),
    (SIGNED_BEFORE.replace("The Company completed", "On March 2, 2026 (the “Closing Date”), the Company completed"),
     date(2026, 3, 10), date(2026, 3, 2)),
    # Signed and filed the same day (Synopsys): the closing fell on that day.
    (HEAD + "On March 10, 2026, Acme Corporation (the “Company”) entered into a Securities Purchase Agreement (the "
     "“Purchase Agreement”) with NVIDIA Corporation (“NVIDIA”). Pursuant to the Purchase Agreement, NVIDIA purchased "
     "1,000,000 shares of the Company’s common stock for an aggregate purchase price of $50,000,000 in cash. ",
     date(2026, 3, 10), date(2026, 3, 10)),
    # A closing clause in the sentence before.
    (HEAD + "On March 2, 2026, Acme Corporation (the “Company”) closed the private placement with NVIDIA Corporation "
     "(“NVIDIA”). Pursuant to the Purchase Agreement, NVIDIA purchased 1,000,000 shares of the Company’s common stock "
     "for an aggregate purchase price of $50,000,000 in cash. ", date(2026, 3, 10), date(2026, 3, 2)),
    # "dated as of" is a participle of the agreement, not the clause's verb.
    (HEAD + "On March 2, 2026, pursuant to the Purchase Agreement dated as of February 1, 2026, Acme Corporation (the "
     "“Company”) completed the issuance and sale of 1,000,000 shares of the Company’s common stock to NVIDIA Corporation "
     "for an aggregate purchase price of $50,000,000 in cash. ", date(2026, 3, 10), date(2026, 3, 2)),
])
def test_a_closing_clause_dates_the_closing(text, filed_on, when):
    r = classify(text, filed_on=filed_on)
    assert (r.outcome, r.event_date) == (C.OUTCOME_PROPOSAL, when), r


# S2-4 — the filer's own stock is an exact filer term bound to the class.

PREFIX_ACME = (HEAD + "On March 2, 2026, Acme Corporation (“Acme” or the “Company”) completed the issuance and sale of "
               "5,000,000 shares of ")
SUFFIX_ACME = " to NVIDIA Corporation for an aggregate purchase price of $500,000,000 in cash. "


@pytest.mark.parametrize("noun, outcome", [
    ("common stock of Acme Robotics, Inc.", C.OUTCOME_HEADS_UP),
    ("Beta Acme’s common stock", C.OUTCOME_HEADS_UP),
    ("Series A Preferred Stock of Acme Robotics, Inc.", C.OUTCOME_HEADS_UP),
    ("Class A common stock of our joint venture, Acme Photonics LLC", C.OUTCOME_SKIPPED),
    ("common stock of our portfolio company, Beta Robotics, Inc.", C.OUTCOME_SKIPPED),
    ("common stock of its affiliate, Acme Labs", C.OUTCOME_SKIPPED),
    ("common stock of a majority-owned investee", C.OUTCOME_SKIPPED),
    # The filer's own HOLDING of another company's stock is that company's stock.
    ("the Company’s shares of common stock of Beta Robotics, Inc.", C.OUTCOME_HEADS_UP),
])
def test_another_entitys_stock_is_never_the_filers(noun, outcome):
    r = classify(PREFIX_ACME + noun + SUFFIX_ACME)
    assert r.outcome == outcome and r.amount_usd is None, (noun, r)


@pytest.mark.parametrize("noun", [
    "the Company’s common stock", "Acme’s common stock", "common stock of the Company", "common stock of Acme",
    "common stock of Acme Corporation", "its common stock", "our common stock", "the Company’s Series A Preferred Stock",
    "Series A Preferred Stock of the Company",
])
def test_the_filers_own_stock_in_every_spelling_stays_a_proposal(noun):
    r = classify(PREFIX_ACME + noun + SUFFIX_ACME)
    assert r.outcome == C.OUTCOME_PROPOSAL and r.amount_usd == Decimal(500_000_000), (noun, r)


@pytest.mark.parametrize("noun, owned", [
    ("the Company’s common stock", True), ("Synopsys’ common stock", True), ("our Class A common stock", True),
    ("common stock of the Company, par value $0.01", True), ("Nokia shares", True),
    ("common stock of our portfolio company", False), ("our portfolio company’s common stock", False),
    ("common stock of Acme Robotics, Inc.", False), ("BigAcme’s common stock", False), ("Beta Acme’s common stock", False),
    ("its common stock", False),  # "its" only when the filer is the seller (company_side)
    ("the Company’s shares of common stock of Beta Robotics, Inc.", False),
])
def test_owns_noun_needs_an_exact_term_bound_to_the_class(noun, owned):
    terms = frozenset({"Company", "Acme", "Acme Corporation", "Synopsys", "Nokia"})
    assert T._owns_noun(noun, terms, company_side=False) is owned


@pytest.mark.parametrize("noun, term, owned", [
    ("common stock of Acme Robotics, Inc.", "Acme", False), ("common stock of Acme, Inc.", "Acme", True),
    ("common stock of Acme Corporation", "Acme", True),  # its legal form may follow the term
    ("common stock of Acme Corporation", "Acme Corporation", True), ("common stock of Acme Labs Corp.", "Acme", False),
    ("common stock of the Company (the “Shares”)", "Company", True), ("common stock of the Company, a Delaware", "Company",
                                                                   True),
    ("portfolio company’s common stock", "Company", False), ("Acme’s common stock", "Acme", True),
])
def test_term_owns_needs_the_term_to_end_the_owner_phrase(noun, term, owned):
    """``_term_owns`` alone (``_owns_noun`` adds the foreign-owner check on top): after "of", the
    exact term may be followed only by its legal form ("of Acme Corporation" is Acme's), never
    by another name token ("of Acme Labs Corp." is not)."""
    assert T._term_owns(noun, term) is owned


# S2-5 — "up to" belongs to the member's own object only.

@pytest.mark.parametrize("sentence, fixed", [
    ("NVIDIA Corporation has agreed to purchase 1,000,000 shares of our common stock in a concurrent private placement, "
     "and certain of our existing stockholders have agreed to purchase up to $250 million of shares of our common "
     "stock.", None),
    ("NVIDIA Corporation has agreed to purchase $100 million of shares of our common stock in a concurrent private "
     "placement, and certain of our existing stockholders have indicated an interest in purchasing up to $250 million "
     "of shares of our common stock.", "$100,000,000"),
    ("NVIDIA Corporation has agreed to purchase 1,000,000 shares of our common stock and certain of our existing "
     "stockholders have agreed to purchase up to $250 million of shares.", None),
    ("NVIDIA Corporation has agreed to purchase 1,000,000 shares of our common stock at the offering price and the "
     "underwriters have an option to purchase up to $50 million of additional shares.", None),
])
def test_another_partys_cap_is_never_the_members_committed_up_to(sentence, fixed):
    """Never the other party's figure. A member with a FIXED amount of its own stays a proposal (the
    cap is never even read); a cap past the member's own object is refused as a heads-up since
    Review A (2026-10-29: "refuse instead of guessing") — it used to be a proposal with no figure."""
    r = classify(sentence + " " + PROSPECTUS, form="424B4", items=(), file_type="424B4")
    assert r.tier == C.TIER_SEC_AGREEMENT and r.amount_usd is None and r.value_basis is None, r
    if fixed:
        assert r.outcome == C.OUTCOME_PROPOSAL, r
        assert fixed in r.detail  # the member's fixed amount stays fixed (named in detail only)
    else:
        assert r.outcome == C.OUTCOME_HEADS_UP and "not bound to the member" in r.detail, r


def test_a_fixed_amount_stays_fixed_beside_an_up_to_in_the_same_object():
    r = classify("NVIDIA Corporation has agreed to purchase $100 million of shares of our common stock representing up "
                 "to 9.9% of our outstanding common stock. " + PROSPECTUS, form="424B4", items=(), file_type="424B4")
    assert (r.outcome, r.amount_usd, r.value_basis) == (C.OUTCOME_PROPOSAL, None, None), r
    assert "$100,000,000" in r.detail


def test_up_to_inside_the_members_own_object_is_read():
    r = classify("NVIDIA Corporation has agreed to purchase shares of our common stock having an aggregate value of up to "
                 "$500 million in a concurrent private placement. " + PROSPECTUS, form="424B4", items=(), file_type="424B4")
    assert (r.outcome, r.amount_usd, r.value_basis) == (C.OUTCOME_PROPOSAL, Decimal(500_000_000), "committed_up_to")


# S2-6 — a directed issuance needs completion in its own clause; the closed flag.

NOKIA = dict(conformed="NOKIA CORP", form="6-K", items=(), file_type="6-K", filed_on=date(2025, 10, 28))


@pytest.mark.parametrize("text", [
    "Nokia Corporation (“Nokia”) will carry out a directed share issuance to NVIDIA Corporation of 166 389 351 new Nokia "
    "shares, subject to customary closing conditions including regulatory approvals. ",
    "The Board of Directors of Nokia Corporation (“Nokia”) proposes that the Extraordinary General Meeting resolve on a "
    "directed share issuance to NVIDIA Corporation of 166 389 351 new Nokia shares, conditional on approval by the EGM. ",
    "Nokia Corporation (“Nokia”) announced a directed share issuance to NVIDIA Corporation of 166 389 351 new Nokia "
    "shares. ",
    "Nokia Corporation (“Nokia”) announced a directed share issuance to NVIDIA Corporation of 166 389 351 new Nokia "
    "shares, the completion of which is subject to EGM approval. ",
    # An undertaking that does not bind the member alone, or is negated, or sits beside a proposal.
    "Nokia Corporation (“Nokia”) will carry out a directed share issuance to NVIDIA Corporation (“NVIDIA”) of 166 389 351 "
    "new Nokia shares, and Microsoft Corporation and NVIDIA have undertaken to subscribe for the new shares. ",
    "Nokia Corporation (“Nokia”) will carry out a directed share issuance to NVIDIA Corporation (“NVIDIA”) of 166 389 351 "
    "new Nokia shares, and an affiliate of NVIDIA has undertaken to subscribe for the new shares. ",
    "Nokia Corporation (“Nokia”) will carry out a directed share issuance to NVIDIA Corporation (“NVIDIA”) of 166 389 351 "
    "new Nokia shares; NVIDIA has not undertaken to subscribe. ",
    "The Board of Nokia Corporation (“Nokia”) proposes a directed share issuance to NVIDIA Corporation (“NVIDIA”) of "
    "166 389 351 new Nokia shares, and NVIDIA has irrevocably undertaken to subscribe for the new shares. ",
    "Nokia Corporation (“Nokia”) will carry out a directed share issuance to NVIDIA Corporation (“NVIDIA”) of 166 389 351 "
    "new Nokia shares, and Beta Capital LLC and NVIDIA undertook to subscribe for the new shares. ",
])
def test_an_announced_or_conditional_directed_issuance_is_a_heads_up(text):
    r = classify(text, **NOKIA)
    assert (r.tier, r.reason, r.outcome) == (C.TIER_DIRECTED, "t4_directed", C.OUTCOME_HEADS_UP), r
    assert r.closed is False and "not completed" in r.detail


@pytest.mark.parametrize("undertaking", [
    ", and NVIDIA has irrevocably undertaken to subscribe for the new shares, subject to regulatory approvals. ",
    ". NVIDIA undertakes to subscribe for the new shares, subject to regulatory approvals. ",
])
def test_the_members_own_undertaking_makes_a_commitment_proposal(undertaking):
    text = ("Nokia Corporation (“Nokia”) will carry out a directed share issuance to NVIDIA Corporation (“NVIDIA”) of "
            "166 389 351 new Nokia shares" + undertaking)
    r = classify(text, **NOKIA)
    assert (r.tier, r.reason, r.outcome) == (C.TIER_SEC_AGREEMENT, "t2_agreement", C.OUTCOME_PROPOSAL), r
    assert (r.closed, r.amount_usd, r.value_basis, r.shares) == (False, None, None, 166_389_351)


def test_a_completed_directed_issuance_is_closed():
    r = classify("Nokia Corporation (“Nokia”) completed a directed share issuance to NVIDIA Corporation of 166 389 351 new "
                 "Nokia shares. ", **NOKIA)
    assert (r.tier, r.outcome, r.closed) == (C.TIER_DIRECTED, C.OUTCOME_PROPOSAL, True)


@pytest.mark.parametrize("lead", [
    "As of March 2, 2026, Acme Corporation (the “Company”) has not yet completed",
    "On March 2, 2026, Acme Corporation (the “Company”) hasn’t completed",
    "On March 2, 2026, Acme Corporation (the “Company”) has yet to have completed",
])
def test_a_negated_closing_is_never_a_closing(lead):
    r = classify(BASE.replace("On March 2, 2026, Acme Corporation (the “Company”) completed", lead))
    assert r.outcome != C.OUTCOME_PROPOSAL and r.tier != C.TIER_SEC_CLOSING and r.closed is False, r


def test_a_not_elsewhere_in_the_sentence_does_not_negate_the_closing():
    r = classify(BASE.replace("On March 2, 2026, Acme", "On March 2, 2026, in a transaction not involving a public "
                                                      "offering, Acme"))
    assert (r.tier, r.outcome) == (C.TIER_SEC_CLOSING, C.OUTCOME_PROPOSAL), r


def test_the_closed_flag_on_the_recorded_documents():
    closed = {name: classify_hit(name, "efts_nvidia_2025q4_2026q1", issuer=issuer).closed for name, issuer in (
        ("intel_closing_8k", "intel"), ("lumentum_preferred_8k", "lumentum"), ("nokia_directed_6k", "nokia"))}
    assert closed == {"intel_closing_8k": True, "lumentum_preferred_8k": True, "nokia_directed_6k": True}
    signing = classify(html_doc("intel_signing_8k"), conformed="INTEL CORP", items=("3.02", "7.01", "9.01"),
                       filed_on=date(2025, 9, 18))
    assert signing.closed is False
    ioi = classify(text_doc("meta_424b5_final"), conformed="Meta Platforms, Inc.", form="424B5", items=(),
                   filed_on=date(2026, 10, 28), file_type="424B5")
    assert (ioi.tier, ioi.closed) == (C.TIER_IOI, False)


@pytest.mark.parametrize("kw", [
    dict(closed=1), dict(closed=None), dict(closed=True, tier=C.TIER_SEC_AGREEMENT, reason="t2_agreement"),
    dict(closed=True, tier=C.TIER_IOI, reason="t3_indication", outcome=C.OUTCOME_HEADS_UP, value_basis=None),
    dict(closed=True, outcome=C.OUTCOME_SKIPPED, value_basis=None, amount_usd=None, spans=(), company_name=None),
])
def test_the_closed_flag_is_a_bool_never_on_an_agreement_an_indication_or_a_skip(kw):
    with pytest.raises(ValueError):
        _result(**kw)
    assert _result().closed is False  # the default: a caller built before the field keeps working


# S2-7 — the instrument tier needs the T1 form gate; an EX-4 must be an executed instrument.

@pytest.mark.parametrize("text, form, items", [
    (PROSPECTUS + "On March 15, 2025, we completed the issuance and sale of 10,000,000 shares of our Series C convertible "
     "preferred stock to NVIDIA Corporation for an aggregate purchase price of $100,000,000. Upon the closing of this "
     "offering, all outstanding shares of our convertible preferred stock will convert into shares of our common "
     "stock. ", "S-1", ()),
    ("Item 8.01 Other Events. On March 2, 2026, Acme Corporation (the “Company”) completed the issuance and sale of 2,000 "
     "shares of the Company’s Series A Preferred Stock to NVIDIA Corporation for an aggregate purchase price of "
     "$100,000,000 in cash. ", "8-K", ("8.01",)),
])
def test_an_instrument_sale_outside_an_8k_with_3_02_is_a_heads_up(text, form, items):
    r = classify(text, form=form, items=items, file_type=form)
    assert (r.tier, r.outcome, r.amount_usd) == (C.TIER_INSTRUMENT, C.OUTCOME_HEADS_UP, None), r
    assert "outside an 8-K with Item 3.02" in r.detail


def test_an_instrument_sale_in_a_6k_stays_a_proposal():
    r = classify("On March 2, 2026, Acme Corporation (the “Company”) completed the issuance and sale of 2,000 shares of "
                 "the Company’s Series A Preferred Stock to NVIDIA Corporation for an aggregate purchase price of "
                 "$100,000,000 in cash. ", form="6-K", items=(), file_type="6-K")
    assert (r.tier, r.outcome, r.amount_usd, r.closed) == (C.TIER_INSTRUMENT, C.OUTCOME_PROPOSAL, Decimal(100_000_000),
                                                           True)


EX4_WARRANT = ("WARRANT TO PURCHASE COMMON STOCK. Acme Corporation, a Delaware corporation (the “Company”), hereby "
               "certifies that, for value received, NVIDIA Corporation (the “Holder”) is entitled to purchase 5,000,000 "
               "shares of Common Stock. Issue Date: March 2, 2026. ")


@pytest.mark.parametrize("text, items, why", [
    ("FORM OF " + EX4_WARRANT, ("1.01", "3.02"), "a form of"),
    ("Form of " + EX4_WARRANT, ("1.01", "3.02"), "a form of"),
    (EX4_WARRANT.replace("March 2, 2026", "[●], 2026"), ("1.01", "3.02"), "placeholder"),
    (EX4_WARRANT.replace("Issue Date: March 2, 2026", "Issue Date: [__]"), ("1.01", "3.02"), "placeholder"),
    (EX4_WARRANT.replace("March 2, 2026", "March [__], 2026"), ("1.01", "3.02"), "placeholder"),
    (EX4_WARRANT.replace("March 2, 2026", "______, 2026"), ("1.01", "3.02"), "placeholder"),
    (EX4_WARRANT, ("1.01", "9.01"), "outside an 8-K"),  # a signing-only 8-K
    (EX4_WARRANT + "This Warrant shall be issued to the Holder at the Closing. ", ("1.01", "3.02"), "to be issued"),
    (EX4_WARRANT + "The Warrants to be issued pursuant to the Purchase Agreement are described below. ",
     ("1.01", "3.02"), "to be issued"),
])
def test_an_ex4_that_is_not_an_executed_instrument_is_a_heads_up(text, items, why):
    r = classify(text, file_type="EX-4.1", items=items)
    assert (r.tier, r.outcome, r.closed) == (C.TIER_INSTRUMENT, C.OUTCOME_HEADS_UP, False), r
    assert why in r.detail, r.detail


@pytest.mark.parametrize("text, form, items", [
    (EX4_WARRANT + "The Warrant Shares shall be issued upon exercise in the name of the Holder. ", "8-K",
     ("1.01", "3.02")),
    (EX4_WARRANT + "Certain terms are redacted [***] as confidential. ", "8-K", ("1.01", "3.02")),
    (EX4_WARRANT, "6-K", ()),
])
def test_an_executed_ex4_warrant_stays_a_proposal(text, form, items):
    r = classify(text, file_type="EX-4.1", form=form, items=items)
    assert (r.instrument_label, r.outcome, r.shares, r.closed) == ("(warrant)", C.OUTCOME_PROPOSAL, 5_000_000, True), r


# S2-8 — a scale abbreviation is mapped explicitly or refused.

@pytest.mark.parametrize("money, value", [
    ("$2bn", 2 * 10 ** 9), ("US$2bn", 2 * 10 ** 9), ("$2 bn", 2 * 10 ** 9), ("$2  bn", 2 * 10 ** 9),
    ("$2.5bn", 25 * 10 ** 8), ("USD 1bn", 10 ** 9), ("$2 bln", 2 * 10 ** 9), ("$2BN", 2 * 10 ** 9),
    ("$500mn", 5 * 10 ** 8), ("$500 mln", 5 * 10 ** 8), ("$500MM", 5 * 10 ** 8), ("$500 mm", 5 * 10 ** 8),
    ("$500M", 5 * 10 ** 8), ("$500 M", 5 * 10 ** 8), ("$2-billion", 2 * 10 ** 9),
    # Ordinary words after a figure are no suffix.
    ("$50,000,000 in cash", 5 * 10 ** 7), ("$50,000,000 consideration", 5 * 10 ** 7), ("$5.0 billion", 5 * 10 ** 9),
])
def test_a_mapped_scale_abbreviation_is_read(money, value):
    m = T._parse_money(money + " x", 0)
    assert m is not None and m.usd and m.value == Decimal(value), (money, m)


@pytest.mark.parametrize("money", ["$2b", "$2B", "$2 B", "$50m", "$50 m", "$5k", "$2 tn", "$2 millions",
                                   "$2-plus", "$50,000,000in", "$50млн", "$50 млн"])
def test_an_unmapped_scale_abbreviation_is_refused(money):
    assert T._parse_money(money + " x", 0) is None, money


@pytest.mark.parametrize("money, outcome, amount", [
    ("US$2bn", C.OUTCOME_PROPOSAL, Decimal(2_000_000_000)),
    ("$2b", C.OUTCOME_HEADS_UP, None),
    ("$50m", C.OUTCOME_HEADS_UP, None),
    ("$2 millions", C.OUTCOME_HEADS_UP, None),
])
def test_an_unreadable_price_is_a_heads_up_never_a_proposal_without_its_amount(money, outcome, amount):
    r = classify(HEAD + "On March 2, 2026, Acme Corporation (the “Company”) completed the issuance and sale of 40,000,000 "
                 f"shares of the Company’s common stock to NVIDIA Corporation for an aggregate purchase price of {money}. ")
    assert (r.outcome, r.amount_usd) == (outcome, amount), r
    if outcome == C.OUTCOME_HEADS_UP:
        assert "could not be read" in r.detail
    commitment = classify(f"NVIDIA Corporation has agreed to purchase up to {money} of shares of our common stock. "
                          + PROSPECTUS, form="424B4", items=(), file_type="424B4")
    assert commitment.outcome == outcome and commitment.amount_usd == amount, commitment


@pytest.mark.parametrize("sentence", [
    "NVIDIA Corporation has agreed to purchase $2b of shares of our common stock.",  # a fixed amount at the start
    "NVIDIA Corporation has agreed to purchase shares of our common stock having an aggregate value of up to $2b.",
])
def test_an_unreadable_commitment_amount_is_a_heads_up(sentence):
    r = classify(sentence + " " + PROSPECTUS, form="424B4", items=(), file_type="424B4")
    assert (r.tier, r.outcome, r.amount_usd) == (C.TIER_SEC_AGREEMENT, C.OUTCOME_HEADS_UP, None), r
    assert "could not be read" in r.detail


def test_a_6k_amount_in_a_mapped_abbreviation_is_read():
    r = classify("On March 2, 2026, Nokia Corporation (“Nokia”) completed the issuance and sale of 85,000,000 shares of "
                 "Nokia’s ordinary shares to NVIDIA Corporation for an aggregate purchase price of USD 1bn. ",
                 conformed="NOKIA CORP", form="6-K", items=(), file_type="6-K")
    assert (r.tier, r.outcome, r.amount_usd) == (C.TIER_DIRECTED, C.OUTCOME_PROPOSAL, Decimal(1_000_000_000))


# S2-9 — the member heads the T3 subject; a several-name subject is a combined figure.

IOI_TAIL = " indicated an interest in purchasing up to $500 million of shares in this offering. " + PROSPECTUS


@pytest.mark.parametrize("subject", [
    "Neither NVIDIA Corporation nor any of its affiliates has",
    "None of NVIDIA Corporation, its affiliates or our directors have",
    "No existing stockholder, including NVIDIA Corporation, has",
    "Beta Capital LLC, a partner of NVIDIA Corporation, has",
    "Beta Capital LLC, an investor alongside NVIDIA Corporation, has",
    "The chief executive officer of NVIDIA Corporation has",
    "Funds managed by Beta Capital LLC with NVIDIA Corporation have",
])
def test_the_member_not_heading_the_subject_is_no_indication(subject):
    r = classify(subject + IOI_TAIL, form="424B4", items=(), file_type="424B4")
    assert r.tier != C.TIER_IOI and r.outcome != C.OUTCOME_PROPOSAL, (subject, r)


@pytest.mark.parametrize("subject, members", [
    ("NVIDIA Corporation and Microsoft Corporation have", ("NVIDIA Corporation", "Microsoft Corporation")),
    ("NVIDIA Corporation and Beta Capital LLC have", ("NVIDIA Corporation",)),
    ("Certain of our existing stockholders and NVIDIA Corporation have", ("NVIDIA Corporation",)),
    ("Certain existing stockholders, including NVIDIA Corporation, have", ("NVIDIA Corporation",)),
])
def test_a_several_name_subject_is_a_combined_amount(subject, members):
    r = classify(subject + IOI_TAIL, form="424B4", items=(), file_type="424B4")
    assert (r.tier, r.outcome, r.group_term) == (C.TIER_IOI, C.OUTCOME_HEADS_UP, None), r
    assert r.group_members == members and r.amount_usd == Decimal(500_000_000)
    assert "combined" in r.detail and "others" in r.detail


def test_a_several_name_subject_always_lists_the_member():
    """Non-empty ``group_members`` is the finder's "combined" signal: the member is in it even when
    the subject names it only by its defined term."""
    text = ("NVIDIA Corporation (“NVIDIA”) is an existing partner. NVIDIA and Beta Capital LLC have" + IOI_TAIL)
    r = classify(text, form="424B4", items=(), file_type="424B4")
    assert (r.tier, r.group_term) == (C.TIER_IOI, None), r
    assert r.group_members[:1] == ("NVIDIA Corporation",) and "combined" in r.detail


@pytest.mark.parametrize("subject", [
    "NVIDIA Corporation has", "NVIDIA Corporation, an existing stockholder, has",
    "Entities affiliated with NVIDIA Corporation have", "NVIDIA Corporation, a partner of Beta Capital LLC, has",
])
def test_the_member_heading_the_subject_alone_is_its_own_indication(subject):
    r = classify(subject + IOI_TAIL, form="424B4", items=(), file_type="424B4")
    assert (r.tier, r.outcome, r.group_members) == (C.TIER_IOI, C.OUTCOME_HEADS_UP, ()), (subject, r)
    assert r.amount_usd == Decimal(500_000_000) and "combined" not in r.detail


def test_an_unreadable_indication_amount_says_so():
    r = classify("NVIDIA Corporation has" + IOI_TAIL.replace("$500 million", "$2b"), form="424B4", items=(),
                 file_type="424B4")
    assert (r.tier, r.amount_usd) == (C.TIER_IOI, None) and "could not be read" in r.detail


# S2-10 — the CPU budget inside the term collection.

def _many_terms_doc(n_terms: int) -> str:
    body = ", ".join(f"“T{i}x”" for i in range(n_terms))
    return BASE + f"NVIDIA Corporation ({body}) is named here. "


def test_too_many_defined_terms_is_a_heads_up():
    r = classify(_many_terms_doc(T.MAX_ALIAS_TERMS + 1))
    assert (r.reason, r.outcome) == ("no_template", C.OUTCOME_HEADS_UP) and "too many defined terms" in r.detail
    at_cap = classify(_many_terms_doc(T.MAX_ALIAS_TERMS - 1))  # + "NVIDIA" from BASE = the cap itself
    assert at_cap.outcome == C.OUTCOME_PROPOSAL, at_cap
    assert (T.MAX_ALIAS_TERMS, T.MAX_GROUP_TERMS) == (16, 8)


@pytest.mark.parametrize("group", [
    # listed before a "(collectively, …)" parenthetical …
    "NVIDIA Corporation and Beta{i} LLC (collectively, the “Group{i} Investors”) are named here. ",
    # … or defined inside a LATER party's parenthetical (the Meta 424B5 shape).
    "NVIDIA Corporation and Beta{i} LLC (“Beta{i}” and, together with NVIDIA Corporation, the “Group{i} Investors”) "
    "are named here. ",
])
def test_too_many_groups_is_a_heads_up(group):
    groups = "".join(group.format(i=i) for i in range(T.MAX_GROUP_TERMS + 1))
    r = classify(BASE + groups)
    assert r.outcome == C.OUTCOME_HEADS_UP and "too many defined terms" in r.detail, r
    at_cap = classify(BASE + "".join(group.format(i=i) for i in range(T.MAX_GROUP_TERMS)))
    assert "too many defined terms" not in at_cap.detail, at_cap


def test_the_budget_is_checked_inside_the_term_scan(monkeypatch):
    """Each whole-text term scan advances a fake clock by a second: with a 2.5 s budget the run stops
    after the third scan — never all ten (the budget used to be checked only after the loop)."""
    clock = {"t": 0.0}
    scans = {"n": 0}
    real_term_regex = T._term_regex

    def counting_term_regex(term):
        rx = real_term_regex(term)

        class _Counting:
            def finditer(self, text, *a):
                scans["n"] += 1
                clock["t"] += 1.0
                return rx.finditer(text, *a)

            def search(self, *a):
                return rx.search(*a)

            def sub(self, *a):
                return rx.sub(*a)

        return _Counting()

    monkeypatch.setattr(T, "_cpu_clock", lambda: clock["t"])
    monkeypatch.setattr(T, "_term_regex", counting_term_regex)
    r = classify(_many_terms_doc(10), budget=2.5)
    assert r.detail == "cpu budget"
    assert scans["n"] <= 4, scans


def test_the_budget_is_checked_while_picking_deal_sentences(monkeypatch):
    clock = {"t": 0.0}
    calls = {"n": 0}
    real = T._Classifier._near_deal_words

    def slow_near(self, k):
        calls["n"] += 1
        clock["t"] += 0.01
        return real(self, k)

    monkeypatch.setattr(T, "_cpu_clock", lambda: clock["t"])
    monkeypatch.setattr(T._Classifier, "_near_deal_words", slow_near)
    text = "".join(f"NVIDIA Corporation is named in sentence {i}. Filler text follows here. " for i in range(1200))
    r = classify(text, budget=2.0)
    assert r.detail == "cpu budget"
    assert calls["n"] <= 2.0 / 0.01 + 256, calls


# ── §16 the 2026-10-29 review (Review A) ────────────────────────────────────────────────
#
# Each finding: the reviewer's scenario texts verbatim as must-not-match cases (every one gave the
# wrong fact before this round) and the honest twins that must keep passing.

P424 = dict(form="424B4", items=(), file_type="424B4")


def _classifier(text: str, conformed: str = "ACME CORP") -> "T._Classifier":
    """A classifier with its document read up to the candidates (names, terms, sentences)."""
    c = T._Classifier(text=text, aliases=tuple(aliases("nvidia")), others=tuple(others("nvidia")), conformed=conformed,
                      form="8-K", items=frozenset({"3.02"}), filed_on=date(2026, 3, 10), file_type="8-K",
                      budget=T._Budget(10))
    c.company = c._find_company()
    c._collect_terms(c._find_aliases())
    c.starts = c._segment()
    return c


# T2-UPTO-SCOPE-BYPASS — another party's "up to", or the member's own OPTIONAL one, is never the
# member's committed_up_to: its object closes at a second purchase verb, at another named party
# or at a new clause after " and ", and a cap past it is refused (a heads-up), never guessed at.

@pytest.mark.parametrize("sentence", [
    # The reviewer's scenarios: a second party joined by a bare " and ", its verb outside the old
    # denylist (will / intend to / are expected to / plans to).
    "NVIDIA Corporation has agreed to purchase up to 10,000,000 shares of our common stock in a concurrent private "
    "placement and Beta Capital LLC will purchase up to $250 million of shares of our common stock in this offering.",
    "NVIDIA Corporation has agreed to purchase up to 10,000,000 shares of our common stock in a concurrent private "
    "placement and certain of our existing stockholders intend to purchase up to $250 million of shares of our common "
    "stock in this offering.",
    "NVIDIA Corporation has agreed to purchase up to 10,000,000 shares of our common stock in a concurrent private "
    "placement and certain of our existing stockholders are expected to purchase up to $250 million of shares of our "
    "common stock in this offering.",
    "In a separate private placement concurrent with this offering, NVIDIA Corporation has agreed to purchase shares of "
    "our common stock at the initial public offering price and certain of our existing stockholders intend to purchase "
    "up to $250 million of shares of our common stock in this offering.",
    "NVIDIA Corporation has agreed to purchase shares of our common stock in a concurrent private placement at the "
    "initial public offering price and entities affiliated with Beta Capital LLC are expected to purchase up to $250 "
    "million of shares of our common stock in this offering.",
    "NVIDIA Corporation has agreed to purchase 1,000,000 shares of our common stock and Beta Capital LLC plans to "
    "purchase up to $250 million of shares.",
    # The verifier's: the member's own OPTIONAL extra is no commitment.
    "NVIDIA Corporation has agreed to purchase 1,000,000 shares of our common stock at a price of $50.00 per share and "
    "may purchase up to $250 million of additional shares.",
    # A cap shared with another named party that has no verb of its own.
    "NVIDIA Corporation has agreed to purchase shares of our common stock in a concurrent private placement, as has Beta "
    "Capital LLC, for up to $250 million in the aggregate.",
    "NVIDIA Corporation has agreed to purchase shares of our common stock together with Gamma Partners LLC for up to "
    "$250 million.",
    "NVIDIA Corporation has agreed to purchase shares of our common stock in a concurrent private placement, as have "
    "certain of our directors, for up to $250 million in the aggregate.",
    # Each closing marker on its own: a second purchase verb with no new clause, and a new clause
    # whose verb is no purchase verb.
    "NVIDIA Corporation has agreed to purchase 1,000,000 shares of our common stock, with certain of our directors "
    "expected to purchase up to $250 million of shares.",
    "NVIDIA Corporation has agreed to purchase 1,000,000 shares of our common stock and certain of our directors will "
    "contribute up to $250 million to this offering.",
])
def test_a_cap_past_the_members_own_object_is_a_heads_up_never_its_committed_up_to(sentence):
    r = classify(sentence + " " + PROSPECTUS, **P424)
    assert (r.tier, r.reason, r.outcome) == (C.TIER_SEC_AGREEMENT, "t2_agreement", C.OUTCOME_HEADS_UP), r
    assert r.amount_usd is None and r.value_basis is None, r
    assert "up to $250,000,000 is not bound to the member" in r.detail, r.detail


def test_another_members_name_closes_the_members_object():
    """Another club member named in the object, with no legal form and no verb of its own."""
    r = classify("NVIDIA Corporation has agreed to purchase shares of our common stock alongside Gamma for up to $250 "
                 "million. " + PROSPECTUS, other_aliases=["Gamma"], **P424)
    assert (r.tier, r.outcome, r.amount_usd, r.value_basis) == (C.TIER_SEC_AGREEMENT, C.OUTCOME_HEADS_UP, None, None), r


def test_a_group_that_lists_the_member_closes_the_members_object():
    """"… alongside the other Cornerstone Investors for up to $500 million" is a shared cap."""
    r = classify("NVIDIA Corporation and Beta Capital LLC (collectively, the “Cornerstone Investors”) are named here. "
                 "NVIDIA Corporation has agreed to purchase shares of our common stock alongside the other Cornerstone "
                 "Investors for up to $500 million. " + PROSPECTUS, **P424)
    assert r.tier == C.TIER_SEC_AGREEMENT and r.amount_usd is None and r.value_basis is None, r
    assert r.outcome == C.OUTCOME_HEADS_UP and "not bound to the member" in r.detail, r


def test_a_percentage_past_the_members_object_is_no_cap():
    """A bare "up to 9.9%" after another clause is no money figure: the member's own commitment
    stays a proposal (with no figure)."""
    r = classify("NVIDIA Corporation has agreed to purchase 1,000,000 shares of our common stock and certain of our "
                 "existing stockholders may purchase up to 9.9% of our outstanding shares. " + PROSPECTUS, **P424)
    assert (r.tier, r.outcome, r.amount_usd, r.value_basis, r.shares) == (
        C.TIER_SEC_AGREEMENT, C.OUTCOME_PROPOSAL, None, None, 1_000_000), r


@pytest.mark.parametrize("text", [
    # The reviewer's 8-K 1.01 scenario: another party's optional cap beside the member's shares × price.
    "Item 1.01 Entry into a Material Definitive Agreement. On March 2, 2026, NVIDIA Corporation agreed to purchase "
    "1,000,000 shares of the common stock of Acme Corporation (the “Company”) at a price of $50.00 per share and Beta "
    "Capital LLC may purchase up to $250 million of additional shares. ",
    # The member's own optional extra, after the signing sentence.
    "Item 1.01 Entry into a Material Definitive Agreement. On March 2, 2026, Acme Corporation (the “Company”) entered "
    "into a Securities Purchase Agreement with NVIDIA Corporation (“NVIDIA”). NVIDIA has agreed to purchase 1,000,000 "
    "shares of the Company’s common stock at a price of $50.00 per share and may purchase up to $250 million of "
    "additional shares. ",
])
def test_an_8k_agreement_never_carries_another_cap_as_the_members(text):
    r = classify(text, items=("1.01",))
    assert (r.tier, r.outcome) == (C.TIER_SEC_AGREEMENT, C.OUTCOME_HEADS_UP), r
    assert r.amount_usd is None and r.value_basis is None, r


def test_an_up_to_that_disagrees_with_shares_times_price_is_refused():
    r = classify("NVIDIA Corporation has agreed to purchase 1,000,000 shares of our common stock at a price of $50.00 per "
                 "share for up to $250 million. " + PROSPECTUS, **P424)
    assert (r.tier, r.outcome, r.amount_usd, r.value_basis) == (C.TIER_SEC_AGREEMENT, C.OUTCOME_HEADS_UP, None, None), r
    assert "disagrees with the 'up to' figure" in r.detail


@pytest.mark.parametrize("sentence, amount", [
    ("NVIDIA Corporation has agreed to purchase up to $500 million of shares of our common stock at the initial public "
     "offering price.", 500_000_000),
    ("NVIDIA Corporation has agreed to purchase shares of our Class A common stock and Class B common stock having an "
     "aggregate value of up to $500 million.", 500_000_000),
    ("NVIDIA Corporation has agreed to purchase shares of our Class A Common Stock listed on The Nasdaq Global Select "
     "Market having an aggregate value of up to $500 million.", 500_000_000),
    ("NVIDIA Corporation has agreed to purchase shares of our common stock, as described under Description of Capital "
     "Stock, having an aggregate value of up to $500 million.", 500_000_000),
    ("NVIDIA Corporation has agreed to purchase up to 1,000,000 shares of our common stock at a price of $50.00 per share, "
     "for an aggregate of up to $50 million.", 50_000_000),
    ("NVIDIA Corporation (“NVIDIA”) has agreed to purchase shares of our common stock having an aggregate value of up to "
     "$500 million, and NVIDIA has agreed not to sell those shares for 180 days.", 500_000_000),
])
def test_the_members_own_up_to_stays_its_committed_up_to(sentence, amount):
    """The honest twins: an "up to" inside the member's own object — past capitalised security
    titles, an exchange's name, a second class joined by " and ", or with a matching shares ×
    price — is still the member's cap."""
    r = classify(sentence + " " + PROSPECTUS, **P424)
    assert (r.tier, r.outcome, r.amount_usd, r.value_basis) == (
        C.TIER_SEC_AGREEMENT, C.OUTCOME_PROPOSAL, Decimal(amount), "committed_up_to"), r


@pytest.mark.parametrize("tail, new_clause", [
    (" and Beta Capital LLC will purchase up to $250 million", True),
    (" and certain of our existing stockholders intend to purchase up to $250 million", True),
    (" and may purchase up to $250 million of additional shares", True),
    (" and Class B common stock having an aggregate value of up to $500 million", False),
    (" and warrants that are exercisable for shares of common stock", False),
    (" and (with its affiliates, which are named below) up to $500 million", False),
    # A parenthesis or a comma ends the words read after " and ".
    (" and Series A Preferred Stock (the preferred shares are convertible into common stock) having", False),
    (" and Class B common stock, the holders of each class are entitled to vote, having", False),
])
def test_a_new_clause_after_and_is_found_by_its_finite_verb(tail, new_clause):
    text = PROSPECTUS + "NVIDIA Corporation has agreed to purchase shares of our common stock" + tail + ". "
    c = _classifier(text)
    lo = text.index("shares of our common stock")
    assert (c._new_clause_and(lo, len(text)) is not None) is new_clause


@pytest.mark.parametrize("segment, party", [
    ("together with Gamma Partners LLC for up to", "Gamma Partners LLC"),
    ("with Morgan Stanley & Co. LLC acting as placement agent for up to", "Morgan Stanley & Co. LLC"),
    ("alongside Broadcom Inc. for up to", "Broadcom Inc."),  # another club member's alias
    ("of our Class A Common Stock listed on The Nasdaq Global Select Market for up to", None),
    ("of our Capital Stock under the Securities Purchase Agreement for up to", None),
    ("of the common stock of Acme Corporation (the “Company”) for up to", None),  # the filer
    ("from NVIDIA Corporation’s own holdings for up to", None),  # the member itself
    ("on March 2, 2026 under Regulation S for up to", None),
])
def test_another_named_party_in_the_members_object(segment, party):
    text = PROSPECTUS + "NVIDIA Corporation has agreed to purchase shares " + segment + " $500 million. "
    c = _classifier(text)
    lo = text.index("shares " + segment)
    found = c._other_party_in(lo, len(text))
    assert (text[found:found + len(party)] if found is not None else None) == party, (segment, found)


# T1-LEAD-DATE-STEP1 — the closing date is the CLAUSE-INITIAL "On <date>", never a date inside a
# phrase; a same-sentence signing and closing (Coherent) or a signing sentence before the
# purchase (Synopsys) filed a few days later keeps its date.

@pytest.mark.parametrize("text, wrong", [
    (HEAD + "On March 2, 2026, pursuant to the Securities Purchase Agreement entered into on September 15, 2025 (the "
     "“Purchase Agreement”) by and between Acme Corporation (the “Company”) and NVIDIA Corporation (“NVIDIA”), the "
     "Company completed the issuance and sale of 1,000,000 shares of the Company’s common stock to NVIDIA for an aggregate "
     "purchase price of $50,000,000 in cash. ", date(2025, 9, 15)),
    (HEAD + "On March 2, 2026, following the expiration of the HSR waiting period on February 20, 2026, Acme Corporation "
     "(the “Company”) completed the issuance and sale of 1,000,000 shares of the Company’s common stock to NVIDIA "
     "Corporation (“NVIDIA”) for an aggregate purchase price of $50,000,000 in cash. ", date(2026, 2, 20)),
    (HEAD + "On March 2, 2026, as previously announced on September 15, 2025, Acme Corporation (the “Company”) completed "
     "the issuance and sale of 1,000,000 shares of the Company’s common stock to NVIDIA Corporation (“NVIDIA”) for an "
     "aggregate purchase price of $50,000,000 in cash. ", date(2025, 9, 15)),
    (HEAD + "On March 2, 2026, following stockholder approval on February 27, 2026, Acme Corporation (the “Company”) "
     "completed the issuance and sale of 1,000,000 shares of the Company’s common stock to NVIDIA Corporation (“NVIDIA”) "
     "for an aggregate purchase price of $50,000,000 in cash. ", date(2026, 2, 27)),
    (HEAD + "On March 2, 2026, pursuant to the Securities Purchase Agreement entered into on September 15, 2025, Acme "
     "Corporation (the “Company”) completed the issuance and sale of 1,000,000 shares of the Company’s common stock to "
     "NVIDIA Corporation (“NVIDIA”) for an aggregate purchase price of $50,000,000 in cash. ", date(2025, 9, 15)),
])
def test_a_date_inside_a_phrase_is_never_the_closing_date(text, wrong):
    r = classify(text)
    assert r.event_date != wrong, r
    assert (r.tier, r.outcome, r.event_date, r.amount_usd) == (
        C.TIER_SEC_CLOSING, C.OUTCOME_PROPOSAL, date(2026, 3, 2), Decimal(50_000_000)), r


def test_another_filings_date_is_never_the_closing_date():
    """The reviewer's third shape: the only lower-case "on <date>" before the verb is another 8-K's
    filing date. No clause-initial date, no closing clause: a heads-up, never 2025-09-18."""
    r = classify(HEAD + "As previously disclosed in the Current Report on Form 8-K filed on September 18, 2025, Acme "
                 "Corporation (the “Company”) completed the issuance and sale of 1,000,000 shares of the Company’s common "
                 "stock to NVIDIA Corporation (“NVIDIA”) on March 2, 2026, for an aggregate purchase price of $50,000,000 "
                 "in cash. ")
    assert r.event_date != date(2025, 9, 18), r
    assert (r.tier, r.outcome, r.event_date) == (C.TIER_SEC_CLOSING, C.OUTCOME_HEADS_UP, None), r
    assert "no closing date" in r.detail


@pytest.mark.parametrize("text, when", [
    (HEAD + "Subsequently, on March 2, 2026, Acme Corporation (the “Company”) completed the issuance and sale of 1,000,000 "
     "shares of the Company’s common stock to NVIDIA Corporation for an aggregate purchase price of $50,000,000 in cash. ",
     date(2026, 3, 2)),
    # A heading the segmenter could not split off (no period after it): its capital "On" still opens the clause.
    ("Item 3.02 Unregistered Sales of Equity Securities On March 2, 2026, Acme Corporation (the “Company”) completed the "
     "issuance and sale of 1,000,000 shares of the Company’s common stock to NVIDIA Corporation for an aggregate purchase "
     "price of $50,000,000 in cash. ", date(2026, 3, 2)),
    # A clause-initial date after ", and ".
    (HEAD + "On March 2, 2026, Acme Corporation (the “Company”) entered into a Securities Purchase Agreement with NVIDIA "
     "Corporation (“NVIDIA”), and on March 5, 2026, the Company completed the issuance and sale of 1,000,000 shares of the "
     "Company’s common stock to NVIDIA for an aggregate purchase price of $50,000,000 in cash. ", date(2026, 3, 5)),
    # A signing on the filing day itself closes that day, coordinated or not.
    (HEAD + "On March 10, 2026, Acme Corporation (the “Company”) entered into a Securities Purchase Agreement with NVIDIA "
     "Corporation (“NVIDIA”) pursuant to which the Company completed the issuance and sale of 1,000,000 shares of the "
     "Company’s common stock to NVIDIA for an aggregate purchase price of $50,000,000 in cash. ", date(2026, 3, 10)),
])
def test_a_clause_initial_date_dates_the_closing(text, when):
    r = classify(text, filed_on=date(2026, 3, 10))
    assert (r.outcome, r.event_date) == (C.OUTCOME_PROPOSAL, when), r


def _recorded_closing(name: str, issuer: str, lag: int) -> T.TemplateResult:
    doc = MANIFEST["documents"][name]
    hit = efts_hit("efts_nvidia_2025q4_2026q1", doc["accession"], doc["document"])
    return classify(html_doc(name), conformed=MANIFEST["issuers"][issuer]["name"], form=hit["root_forms"][0],
                    items=hit.get("items") or (), file_type=hit["file_type"],
                    filed_on=date.fromisoformat(hit["file_date"]) + timedelta(days=lag))


@pytest.mark.parametrize("lag", [1, 3, 4])
@pytest.mark.parametrize("name, issuer, closing", [
    ("coherent_closing_8k", "coherent", date(2026, 3, 2)),  # "On D, Coherent entered into …, and Coherent completed …"
    ("synopsys_closing_8k", "synopsys", date(2025, 12, 1)),  # "On D, Synopsys entered into …. … NVIDIA purchased …"
])
def test_a_same_day_signing_and_closing_filed_days_later_keeps_its_date(name, issuer, closing, lag):
    r = _recorded_closing(name, issuer, lag)
    assert (r.tier, r.outcome, r.event_date, r.amount_usd) == (
        C.TIER_SEC_CLOSING, C.OUTCOME_PROPOSAL, closing, Decimal(2_000_000_000)), (lag, r)


@pytest.mark.parametrize("name, issuer", [("coherent_closing_8k", "coherent"), ("synopsys_closing_8k", "synopsys")])
def test_a_signing_date_past_the_8k_window_is_no_closing_date(name, issuer):
    r = _recorded_closing(name, issuer, T.SIGNING_CLOSING_WINDOW_DAYS + 1)
    assert (r.outcome, r.event_date) == (C.OUTCOME_HEADS_UP, None) and "no closing date" in r.detail, r
    assert T.SIGNING_CLOSING_WINDOW_DAYS == 7


@pytest.mark.parametrize("text", [
    # A coordinated signing restated "as previously disclosed" — even inside the 8-K window.
    HEAD + "On March 5, 2026, as previously disclosed, Acme Corporation (the “Company”) entered into a Securities Purchase "
    "Agreement with NVIDIA Corporation (“NVIDIA”), and the Company completed the issuance and sale of 1,000,000 shares of "
    "the Company’s common stock to NVIDIA for an aggregate purchase price of $50,000,000 in cash. ",
    # A coordinated signing months before the filing.
    HEAD + "On September 15, 2025, Acme Corporation (the “Company”) entered into a Securities Purchase Agreement with NVIDIA "
    "Corporation (“NVIDIA”), and the Company completed the issuance and sale of 1,000,000 shares of the Company’s common "
    "stock to NVIDIA for an aggregate purchase price of $50,000,000 in cash. ",
    # A signing the sale is not coordinated with ("pursuant to which"), inside the window but not on the filing day.
    HEAD + "On March 5, 2026, Acme Corporation (the “Company”) entered into a Securities Purchase Agreement with NVIDIA "
    "Corporation (“NVIDIA”) pursuant to which the Company completed the issuance and sale of 1,000,000 shares of the "
    "Company’s common stock to NVIDIA for an aggregate purchase price of $50,000,000 in cash. ",
    # The signing sentence BEFORE the sale (step 3), restated "as previously disclosed", inside the window.
    HEAD + "On March 5, 2026, as previously disclosed, Acme Corporation (the “Company”) entered into a Securities Purchase "
    "Agreement with NVIDIA Corporation (“NVIDIA”). The Company completed the issuance and sale of 1,000,000 shares of the "
    "Company’s common stock to NVIDIA for an aggregate purchase price of $50,000,000 in cash. ",
])
def test_a_signing_date_outside_the_coordinated_window_is_no_closing_date(text):
    r = classify(text, filed_on=date(2026, 3, 10))
    assert (r.outcome, r.event_date) == (C.OUTCOME_HEADS_UP, None) and "no closing date" in r.detail, r


# T1-WINDOW-DROPS-OWN-PRICE — the member restating its own purchase in the next sentence keeps its
# price; the window still stops at another party's deal or at other securities.

SALE_OF_SHARES_TO_NVIDIA = (
    HEAD + "On March 2, 2026, Acme Corporation (the “Company”) completed the issuance and sale of 1,000,000 shares of the "
    "Company’s common stock (the “Shares”) to NVIDIA Corporation (“NVIDIA”) pursuant to the Purchase Agreement. "
)


@pytest.mark.parametrize("after, price", [
    ("The Shares were sold to NVIDIA at a price of $50.00 per share, for an aggregate purchase price of $50,000,000.",
     Decimal("50.00")),
    ("NVIDIA paid an aggregate purchase price of $50,000,000 in cash for the Shares.", None),
    ("NVIDIA invested an aggregate purchase price of $50,000,000 in the Company.", None),
    ("NVIDIA subscribed for the Shares at an aggregate purchase price of $50,000,000.", None),
    ("The Shares, which were issued to NVIDIA at an aggregate purchase price of $50,000,000, are restricted securities.",
     None),
    # The restating link's own relative clause, past the link's verb.
    ("The Shares were sold to NVIDIA Corporation (“NVIDIA”), which paid an aggregate purchase price of $50,000,000 for "
     "them.", None),
    # The passive: the member is the agent ("paid by NVIDIA").
    ("The aggregate purchase price of $50,000,000 was paid by NVIDIA in cash.", None),
])
def test_the_member_restating_its_own_purchase_keeps_its_price(after, price):
    r = classify(SALE_OF_SHARES_TO_NVIDIA + after + " ")
    assert (r.tier, r.outcome, r.amount_usd, r.value_basis, r.price_per_share) == (
        C.TIER_SEC_CLOSING, C.OUTCOME_PROPOSAL, Decimal(50_000_000), "invested", price), r
    assert "another deal" not in r.detail, r.detail


@pytest.mark.parametrize("after", [
    # The member's OTHER securities: never this common sale's price.
    "NVIDIA also paid an aggregate purchase price of $450,000,000 for warrants to purchase shares of the Company’s "
    "common stock.",
    "The Shares were sold to NVIDIA, together with the Warrants, for an aggregate purchase price of $55,000,000.",
    "Shares of the Company’s Series A Preferred Stock were sold to NVIDIA for an aggregate purchase price of $450,000,000.",
])
def test_the_members_other_securities_never_lend_their_price(after):
    r = classify(SALE_OF_SHARES_TO_NVIDIA + after + " ")
    assert r.amount_usd is None and r.value_basis is None, r
    assert "another deal's" not in r.detail, r.detail  # the member's own other deal, never "another" party's


@pytest.mark.parametrize("after", [
    # A completed sale with no purchaser named, and another party's agreement: each still cuts the
    # window (the S2-1 rule, on the window code this round rewrote).
    "The Company also completed the private placement of its Series B Preferred Stock for an aggregate purchase price of "
    "$450,000,000 in cash.",
    "Beta Capital LLC agreed to purchase shares of the Company’s Series B Preferred Stock for an aggregate purchase price "
    "of $450,000,000.",
    # Another sale right after the member's restatement (inside its exempt reach): still another deal.
    "The Shares were sold to NVIDIA; Acme agreed to sell to Beta Capital LLC shares for an aggregate purchase price of "
    "$450,000,000.",
])
def test_another_deal_in_the_next_sentence_still_cuts_the_window(after):
    r = classify(SALE_OF_SHARES_TO_NVIDIA + after + " ")
    assert (r.tier, r.outcome, r.shares) == (C.TIER_SEC_CLOSING, C.OUTCOME_PROPOSAL, 1_000_000), r
    assert r.amount_usd is None and r.value_basis is None, r


@pytest.mark.parametrize("after", [
    "Concurrently, the Company sold 9,000,000 shares of its common stock to Beta Capital LLC for an aggregate purchase "
    "price of $450,000,000 in cash.",
    "Beta Capital LLC also paid an aggregate purchase price of $450,000,000 for its shares.",
    "The aggregate purchase price of $450,000,000 for its shares was paid by Beta Capital LLC in cash.",
])
def test_the_another_deals_note_names_only_another_partys_deal(after):
    """The note is kept where it is true — the clause that cut the window is ANOTHER party's."""
    r = classify(SALE_OF_SHARES_TO_NVIDIA + after + " ")
    assert (r.outcome, r.amount_usd) == (C.OUTCOME_PROPOSAL, None), r
    assert "the next sentence's price is another deal's" in r.detail, r.detail


def test_a_sale_with_no_named_buyer_cuts_the_window_without_the_note():
    """"… pursuant to the Purchase Agreement" names a document, not a buyer: the window is cut (the
    price may be any deal's), but the note never claims it is another party's."""
    r = classify(SALE_OF_SHARES_TO_NVIDIA + "On March 3, 2026, the Company completed the sale of its Series B Preferred "
                 "Stock pursuant to the Purchase Agreement for an aggregate purchase price of $450,000,000. ")
    assert (r.outcome, r.amount_usd) == (C.OUTCOME_PROPOSAL, None), r
    assert "another deal's" not in r.detail, r.detail


def test_a_sale_clause_with_its_own_count_keeps_the_restated_price():
    """The same window on the "<n> shares … were issued and sold to <member>" sale clause: the next
    sentence restating it carries its price, and the restatement is no second deal."""
    r = classify(HEAD + "On March 2, 2026, 1,000,000 shares of the Company’s common stock (the “Shares”) were issued and "
                 "sold to NVIDIA Corporation (“NVIDIA”). The Shares were sold to NVIDIA at a price of $50.00 per share, for "
                 "an aggregate purchase price of $50,000,000. " + "Acme Corporation (the “Company”) is the issuer. ")
    assert (r.tier, r.outcome, r.amount_usd, r.price_per_share, r.shares) == (
        C.TIER_SEC_CLOSING, C.OUTCOME_PROPOSAL, Decimal(50_000_000), Decimal("50.00"), 1_000_000), r


# T4-COMPLETED-WITH-FUTURE-TAIL — completion that governs the directed issuance itself reports it
# done; a later step in the same sentence (listing, delivery) does not undo it.

NOKIA_DONE = dict(conformed="NOKIA CORP", form="6-K", items=(), file_type="6-K", filed_on=date(2025, 11, 13))


@pytest.mark.parametrize("text", [
    "Nokia Corporation (“Nokia”) has completed the directed share issuance to NVIDIA Corporation of 166 389 351 new Nokia "
    "shares, which are expected to be admitted to trading on Nasdaq Helsinki on or about 14 November 2025. ",
    "Nokia Corporation (“Nokia”) has completed the directed share issuance to NVIDIA Corporation of 166 389 351 new Nokia "
    "shares, and the new shares will be delivered to NVIDIA in the form of American Depositary Shares. ",
    "The directed share issuance to NVIDIA Corporation of 166 389 351 new Nokia shares by Nokia Corporation (“Nokia”) was "
    "completed on 13 November 2025, and the new shares will be delivered to NVIDIA in the form of American Depositary "
    "Shares. ",
])
def test_a_completed_directed_issuance_with_a_future_tail_is_still_completed(text):
    r = classify(text, **NOKIA_DONE)
    assert (r.tier, r.reason, r.outcome, r.closed, r.shares) == (
        C.TIER_DIRECTED, "t4_directed", C.OUTCOME_PROPOSAL, True, 166_389_351), r


@pytest.mark.parametrize("text", [
    # A temporal or conditional lead: the completion is subordinate, the main clause is future.
    "Upon completion of the directed share issuance to NVIDIA Corporation of 166 389 351 new Nokia shares, NVIDIA will "
    "hold approximately 2.9% of Nokia’s shares. Nokia Corporation (“Nokia”) is the issuer. ",
    "Once Nokia Corporation (“Nokia”) has completed the directed share issuance to NVIDIA Corporation of 166 389 351 new "
    "Nokia shares, NVIDIA will hold approximately 2.9% of Nokia’s shares. ",
    "Following the completion of the directed share issuance to NVIDIA Corporation of 166 389 351 new Nokia shares, NVIDIA "
    "will hold approximately 2.9% of Nokia’s shares. Nokia Corporation (“Nokia”) is the issuer. ",
    # Not done yet, before the phrase or in its own passive.
    "Nokia Corporation (“Nokia”) expects to have completed the directed share issuance to NVIDIA Corporation of 166 389 351 "
    "new Nokia shares by 13 November 2025. ",
    "The directed share issuance to NVIDIA Corporation of 166 389 351 new Nokia shares by Nokia Corporation (“Nokia”) is "
    "expected to be completed on 13 November 2025. ",
    "The directed share issuance to NVIDIA Corporation of 166 389 351 new Nokia shares by Nokia Corporation (“Nokia”) will "
    "have been completed by 13 November 2025. ",
    # A completion inside a relative clause about something else is not the issuance's own.
    "The directed share issuance to NVIDIA Corporation of 166 389 351 new Nokia shares, which follows the rights offering "
    "that was completed in October 2025, will be carried out by Nokia Corporation (“Nokia”) in November 2025. ",
    # A governing completion still yields to a condition in its own clause.
    "Nokia Corporation (“Nokia”) has completed the directed share issuance to NVIDIA Corporation of 166 389 351 new Nokia "
    "shares, the completion of which is subject to EGM approval. ",
    "Nokia Corporation (“Nokia”) has completed the directed share issuance to NVIDIA Corporation of 166 389 351 new Nokia "
    "shares, subject to customary closing conditions. ",
    "Nokia Corporation (“Nokia”) has completed the directed share issuance to NVIDIA Corporation of 166 389 351 new Nokia "
    "shares, which the Board proposes to register next week. ",
])
def test_a_directed_issuance_completed_only_in_a_subordinate_or_future_clause_is_not_completed(text):
    r = classify(text, **NOKIA_DONE)
    assert (r.tier, r.outcome, r.closed) == (C.TIER_DIRECTED, C.OUTCOME_HEADS_UP, False), r
    assert "not completed" in r.detail


# T-CPU-BUDGET-UNCHECKED-LOOPS — the link loop and the choice among deals check the budget, the
# choice stops at a third distinct deal, and more than MAX_DEAL_CANDIDATES clauses is a heads-up.

def _many_sale_links_doc(n_sent: int, per: int = 14) -> str:
    """The reviewer's document: an Item 3.02 8-K of ``n_sent`` sentences of ``per`` clauses "On March
    2, 2026, <n> shares of the Company’s common stock were issued and sold to NVIDIA Corporation",
    each <n> distinct (1,000 sentences = 1.46 MB, far under the 12 MiB document cap)."""
    parts = [HEAD, "Acme Corporation (the “Company”) reports the following sales. "]
    c = 1
    for _ in range(n_sent):
        clauses = []
        for _ in range(per):
            clauses.append(f"On March 2, 2026, {c} shares of the Company’s common stock were issued and sold to NVIDIA "
                           "Corporation")
            c += 1
        parts.append("; ".join(clauses) + ". ")
    return "".join(parts)


def test_a_document_of_many_distinct_sale_links_finishes_inside_the_budget(caplog):
    """14,000 distinct links used about 55 s of CPU on a 2 s budget (a quadratic choice among
    deals, no budget check) and answered "14000 different deals". Now the candidate cap answers in
    a fraction of the budget, and says so in the log."""
    text = _many_sale_links_doc(1000)
    assert 1_400_000 < len(text) < 1_500_000
    t0 = time.process_time()
    with caplog.at_level(logging.WARNING, logger=T.__name__):
        r = classify(text, budget=T.DEFAULT_CPU_BUDGET_SECONDS)
    assert time.process_time() - t0 < T.DEFAULT_CPU_BUDGET_SECONDS
    assert (r.tier, r.reason, r.outcome, r.spans) == (C.TIER_OTHER, "no_template", C.OUTCOME_HEADS_UP, ()), r
    assert "too many deal clauses" in r.detail
    assert any("[tc-stakes-template-too-many]" in rec.getMessage() for rec in caplog.records)


def test_the_candidate_cap_is_a_heads_up_one_past_it(monkeypatch):
    assert T.MAX_DEAL_CANDIDATES == 200  # far above any recorded document (at most 3 candidates today)
    monkeypatch.setattr(T, "MAX_DEAL_CANDIDATES", 3)
    at_cap = classify(_many_sale_links_doc(1, per=3))
    assert "too many deal clauses" not in at_cap.detail and "different deals" in at_cap.detail, at_cap
    over = classify(_many_sale_links_doc(1, per=4))
    assert (over.reason, over.outcome) == ("no_template", C.OUTCOME_HEADS_UP) and "too many deal clauses" in over.detail
    # The cap on the sentence loop too: four closings, each a sale verb of its own.
    closings = HEAD + "".join(
        f"On March {d}, 2026, Acme Corporation (the “Company”) completed the issuance and sale of {d},000 shares of the "
        f"Company’s common stock to NVIDIA Corporation for an aggregate purchase price of ${d},000,000 in cash. "
        for d in range(2, 6))
    assert "too many deal clauses" in classify(closings).detail
    # … and on the near misses (links with no terms of their own).
    nears = HEAD + "Acme Corporation (the “Company”) reports the following. " + "".join(
        "The shares of common stock were issued and sold to NVIDIA Corporation in a private placement. " for _ in range(4))
    assert "too many deal clauses" in classify(nears).detail


def test_the_budget_is_checked_inside_the_near_miss_loop(monkeypatch):
    """Each near miss built from a link with no terms advances a fake clock by 0.05 s: with a 1 s
    budget the loop stops within one 64-link check of the deadline — never after all 200."""
    clock = {"t": 0.0}
    calls = {"n": 0}
    real = T._near

    def slow(*a, **kw):
        calls["n"] += 1
        clock["t"] += 0.05
        return real(*a, **kw)

    monkeypatch.setattr(T, "_cpu_clock", lambda: clock["t"])
    monkeypatch.setattr(T, "_near", slow)
    monkeypatch.setattr(T, "MAX_DEAL_CANDIDATES", 10 ** 6)
    text = HEAD + "Acme Corporation (the “Company”) reports the following. " + "".join(
        "The shares of common stock were issued and sold to NVIDIA Corporation in a private placement. "
        for _ in range(200))
    r = classify(text, budget=1.0)
    assert r.detail == "cpu budget"
    assert calls["n"] <= 1.0 / 0.05 + 64, calls


def test_the_budget_is_checked_inside_the_sale_link_loop(monkeypatch):
    """Each sale-link assessment advances a fake clock by 0.05 s: with a 1 s budget the run stops
    within one 64-link check of the deadline — never after all 280 links."""
    clock = {"t": 0.0}
    calls = {"n": 0}
    real = T._Classifier._assess_sale

    def slow(self, *a, **kw):
        calls["n"] += 1
        clock["t"] += 0.05
        return real(self, *a, **kw)

    monkeypatch.setattr(T, "_cpu_clock", lambda: clock["t"])
    monkeypatch.setattr(T._Classifier, "_assess_sale", slow)
    monkeypatch.setattr(T, "MAX_DEAL_CANDIDATES", 10 ** 6)
    r = classify(_many_sale_links_doc(20), budget=1.0)
    assert r.detail == "cpu budget"
    assert calls["n"] <= 1.0 / 0.05 + 64, calls


def test_the_budget_is_checked_inside_the_choice_among_deals(monkeypatch):
    """Each same-deal comparison advances a fake clock by 0.05 s: with a 1 s budget the choice stops
    within one 64-candidate check — never after all 200 near misses were compared."""
    clock = {"t": 0.0}
    calls = {"n": 0}
    real = T._Candidate.same_deal

    def slow(self, other):
        calls["n"] += 1
        clock["t"] += 0.05
        return real(self, other)

    monkeypatch.setattr(T, "_cpu_clock", lambda: clock["t"])
    monkeypatch.setattr(T._Candidate, "same_deal", slow)
    monkeypatch.setattr(T, "MAX_DEAL_CANDIDATES", 10 ** 6)
    text = HEAD + "Acme Corporation (the “Company”) reports the following. " + "".join(
        "The shares of common stock were issued and sold to NVIDIA Corporation in a private placement. "
        for _ in range(200))
    r = classify(text, budget=1.0)
    assert r.detail == "cpu budget"
    assert calls["n"] <= 1.0 / 0.05 + 64, calls


def test_choosing_among_many_distinct_deals_stops_at_the_third(monkeypatch):
    """Two distinct deals already make the "different deals" heads-up: the choice compares each
    candidate with at most two deals (linear), and says "several" once a third appears."""
    calls = {"n": 0}
    real = T._Candidate.same_deal

    def counting(self, other):
        calls["n"] += 1
        return real(self, other)

    monkeypatch.setattr(T._Candidate, "same_deal", counting)
    monkeypatch.setattr(T, "MAX_DEAL_CANDIDATES", 10 ** 6)
    r = classify(_many_sale_links_doc(10), budget=30.0)  # 140 distinct closings
    assert r.outcome == C.OUTCOME_HEADS_UP and r.detail.startswith("several (3 or more) different deals"), r
    assert calls["n"] <= 2 * 140, calls
    two = classify(_many_sale_links_doc(1, per=2))
    assert two.outcome == C.OUTCOME_HEADS_UP and two.detail.startswith("2 different deals"), two
