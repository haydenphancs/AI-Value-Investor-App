"""Which FMP industries' balance sheets make a liquidity or coverage ratio meaningless.

A bank's or insurer's current ratio, quick ratio and interest coverage say nothing about
its health: deposits and policy reserves are its funding, and interest is its cost of
goods, not a burden on operating earnings. Production rows showed it (2026-10-07): C read
all three red, BAC a 0.54 current ratio as "positive". For these industries the rows are
OMITTED (not shown, not scored) — and, because they are meaningless, their values are kept
out of the Financial Services SECTOR median for these metrics too, so a data vendor or an
exchange compared against its sector is not compared against banks.

One shared source, used by every place that must agree:
  * `health_check_service` (the Financials tab Health Check) and `health_snapshot_service`
    / the Overview fallback card (which rows exist);
  * every comparison surface — the Health Check, the profitability / valuation / growth /
    profit-power services and the report collector (pass 2: the company verdict first,
    then no drill-down read at all for a listed non-lender member) — asks
    `peer_median_comparable` / `comparable_peer_metrics` before its benchmark read;
  * `industry_benchmark_service` (which companies' values the sector aggregate pools, and
    which members a mixed industry's own median leaves out);
  * the report collector's MODEL context (`_context_gated_rows`, via
    `company_metric_applicable`): a gated or withheld row reaches Stage A / Stage B as a
    stated "not meaningful" / "not available" line, never as a number.

Matched on FMP's /stable `industry` string, case- and dash-insensitively ("Banks—Regional"
from the older feed is "Banks - Regional"). An unknown or empty industry keeps every row.
`tests/test_health_check_2026_10_07.py` walks every Financial Services and Real Estate
industry in the universe files, so a new FMP industry name fails the build until someone
decides which side it is on.

MIXED industries (owner decisions 2026-10-08 / 2026-10-09): "Financial - Credit Services"
holds card networks, payment processors and money-transfer firms (V, MA, PYPL, WU, GPN) and
two non-lender fee businesses (TREE, PMTS) beside the lenders (AXP, COF, SYF, ALLY, SOFI,
ENVA, SEZL …). The industry stays gated — its median of these metrics pools lenders, and the
benchmark producer keeps its values out of the Financial Services sector pool. A company
there is a NON-LENDER MEMBER only when its ticker is on the curated `NON_LENDER_MEMBERS`
(`PAYMENT_NETWORKS` | `CREDIT_SERVICES_FEE_BUSINESSES`) AND its own income does not read as
a lender's (`lender_verdict`, a data sanity check: True → gated, logged at WARNING; unknown
data → the list stands). Every other member stays gated, whatever its interest data (fail
closed), and so does a company whose ticker the caller could not supply. The list is the
ONLY way in; data checks can only take rows away.

A non-lender member KEEPS current ratio, quick ratio and interest coverage
(`company_metric_applicable`), unless a curated per-company fact withholds one
(`CURATED_WITHHELD_ROWS`: WU), and EVERY metric of it is judged without the industry median
(`peer_median_comparable` False for all metrics, 2026-10-09): margins, ROE / ROA, the price
multiples, growth and the balance-sheet rows score on the existing absolute bands (or stay
unscored), no industry or sector line is drawn, no label names a peer. The Credit Services
median is a lenders' yardstick — the producer computes it from lenders only
(`excluded_from_industry_median`).

Why a list and not the interest-income share (the first cut, same day): FMP zero-fills the
interest income of real lenders, so the share cannot tell them from the networks. Real FMP
data (read-only probe, 2026-10-08, latest annual interestIncome / revenue | netInterestIncome
/ revenue | interestExpense / revenue): ENVA (consumer lender) 0.00 | -0.05 | 0.05, SEZL
(BNPL) 0.00 | -0.03 | 0.03, QFIN 0.02 | 0.02 | 0.00, FINV 0.10 | 0.10 | 0.00 — the same
shape as V 0.00 | -0.01 | 0.01, MA 0.00 | -0.02 | 0.02, PYPL 0.02 | 0.00 | 0.01, WU 0.00 |
-0.03 | 0.04, GPN 0.02 | -0.06 | 0.08 (GPN's interest expense alone rules out an
interest-expense line too).
"""

from __future__ import annotations

import logging
import math
import re
from types import MappingProxyType
from typing import Any, Dict, Iterable, List, Mapping, NamedTuple, Optional, Tuple

logger = logging.getLogger(__name__)

#: Benchmark / Health Check metric names (same spelling in both places).
CURRENT_RATIO = "current_ratio"
QUICK_RATIO = "quick_ratio"
INTEREST_COVERAGE = "interest_coverage"
DEBT_TO_EQUITY = "debt_to_equity"
LIQUIDITY_METRICS = frozenset({CURRENT_RATIO, QUICK_RATIO})
GATED_METRICS = frozenset({CURRENT_RATIO, QUICK_RATIO, INTEREST_COVERAGE})

# Current and quick ratio are meaningless for every industry below.
_NO_LIQUIDITY_INDUSTRIES = frozenset({
    # banks
    "banks",
    "banks - diversified",
    "banks - regional",
    # insurers (brokers too: fiduciary funds sit in their current assets and liabilities)
    "insurance - brokers",
    "insurance - diversified",
    "insurance - life",
    "insurance - property & casualty",
    "insurance - reinsurance",
    "insurance - specialty",
    # capital markets: broker-dealers and investment banks
    "financial - capital markets",
    "investment - banking & investment services",
    # asset managers (consolidated funds and insurers; the suffixed groups are mostly funds)
    "asset management",
    "asset management - bonds",
    "asset management - cryptocurrency",
    "asset management - global",
    "asset management - income",
    "asset management - leveraged",
    # lenders: card issuers and consumer/commercial credit, mortgage lenders and REITs.
    # "Financial - Credit Services" is also MIXED (`_MIXED_LENDER_INDUSTRIES`): its listed
    # non-lender members (`NON_LENDER_MEMBERS`: the payment networks V, MA, PYPL, WU, GPN
    # and the fee businesses TREE, PMTS) get the rows back company by company
    # (`company_metric_applicable`); the INDUSTRY stays here, so its median of these
    # metrics (which pools lenders) is never a peer group and never joins the sector pool.
    "financial - credit services",
    "financial - mortgages",
    "reit - mortgage",
    # financial holding companies
    "financial - conglomerates",
    "financial - diversified",
})

# Interest coverage is meaningless for the same industries EXCEPT insurance brokers: a
# broker (MMC, AON, AJG) is a fee business that borrows like any other, and its lenders
# watch exactly this ratio (review 2026-10-07: the first gate dropped it for them).
_NO_COVERAGE_INDUSTRIES = _NO_LIQUIDITY_INDUSTRIES - frozenset({"insurance - brokers"})

_DASH_RE = re.compile(r"\s*[-–—]\s*")
_SPACE_RE = re.compile(r"\s+")


def industry_key(industry: Any) -> str:
    """FMP industry name → lower-case, single-spaced, every dash as " - "."""
    if not isinstance(industry, str):
        return ""
    key = _DASH_RE.sub(" - ", industry.strip().lower())
    return _SPACE_RE.sub(" ", key).strip()


def liquidity_ratios_applicable(industry: Optional[str]) -> bool:
    """False when the current and quick ratio mean nothing for this industry."""
    return industry_key(industry) not in _NO_LIQUIDITY_INDUSTRIES


def interest_coverage_applicable(industry: Optional[str]) -> bool:
    """False when interest coverage means nothing for this industry."""
    return industry_key(industry) not in _NO_COVERAGE_INDUSTRIES


def peer_metric_applicable(metric: str, industry: Optional[str]) -> bool:
    """Whether ``metric`` is meaningful for a company in ``industry`` — an INDUSTRY-level
    answer (the benchmark producer's sector pool and every industry-only caller). Every
    metric outside `GATED_METRICS` is applicable everywhere. A company in a MIXED industry
    may still keep the rows: `company_metric_applicable`."""
    if metric in LIQUIDITY_METRICS:
        return liquidity_ratios_applicable(industry)
    if metric == INTEREST_COVERAGE:
        return interest_coverage_applicable(industry)
    return True


# ── Mixed industries: payment networks vs lenders (owner decision 2026-10-08) ──────────

#: Industries that hold both lenders and non-lender members (payment networks, fee
#: businesses). Each MUST also be in both gated sets above (tests pin it): the
#: industry-level answer stays "gated", so its pooled median is never a peer group and
#: never joins the Financial Services sector pool.
_MIXED_LENDER_INDUSTRIES = frozenset({"financial - credit services"})

#: The payment networks, processors and money-transfer firms of "Financial - Credit
#: Services" (owner decision 2026-10-08), upper-case FMP symbols.
PAYMENT_NETWORKS = frozenset({"V", "MA", "PYPL", "WU", "GPN"})

#: The non-lender FEE businesses FMP also files under "Financial - Credit Services" (owner
#: decision 2026-10-09; both verified filed there by a read-only FMP profile call on
#: 2026-10-08). A separate set from `PAYMENT_NETWORKS` so the logs — and any future
#: payments peer group — can tell them apart (`non_lender_category`). Evidence per ticker:
#: `NON_LENDER_EVIDENCE`.
CREDIT_SERVICES_FEE_BUSINESSES = frozenset({"TREE", "PMTS"})

#: Every curated non-lender member of a mixed industry — the ONLY companies there that keep
#: current ratio, quick ratio and interest coverage, and whose every metric is judged
#: without the industry median. ADMISSION RULE: a fee / payments business whose credit is
#: incidental, not its core product (PYPL holds ~$6.7B of loans; FCFS, EZPW, PRG lend as
#: their business), whose debt is corporate financing; never a pawn lender, lease-to-own,
#: BNPL, bank or debt buyer. A member that is not listed stays gated whatever its interest
#: data: FMP zero-fills real lenders' interest income (ENVA, SEZL), so no income share can
#: free a company here. NRDS (NerdWallet) is not listed because FMP files it under
#: "Internet Content & Information", where this list has no effect.
NON_LENDER_MEMBERS = PAYMENT_NETWORKS | CREDIT_SERVICES_FEE_BUSINESSES

#: Categories `non_lender_category` names (and the verdict's ``category``).
CATEGORY_PAYMENT_NETWORK = "payment_network"
CATEGORY_FEE_BUSINESS = "fee_business"

#: One-line evidence for each non-payment-network member (the networks are self-evident).
NON_LENDER_EVIDENCE: Mapping[str, str] = MappingProxyType({
    "TREE": (
        "LendingTree: a loan marketplace earning referral fees — interestIncome 0, a "
        "corporate term loan, a classified balance sheet"
    ),
    "PMTS": (
        "CPI Card Group: a payment-card manufacturer — senior notes, carries inventory"
    ),
})

#: The 32 lender members of "Financial - Credit Services" in the benchmark universe
#: (backend/data/benchmark_universe.json, reviewed 2026-10-09), each with why it is a
#: lender. Informational: an unlisted member is gated whether or not it is here (the safe
#: default). The universe builder WARNs on any Credit Services member in neither this nor
#: `NON_LENDER_MEMBERS` (`scripts/build_benchmark_universe._log_unreviewed_mixed_members`),
#: so a new member is classified by a person, never by data; locally,
#: `test_every_credit_services_member_is_classified` checks the uploaded file too (it SKIPS
#: where the gitignored benchmark_universe.json is absent, e.g. CI). Disjoint from
#: `NON_LENDER_MEMBERS` (a test pins it).
REVIEWED_CREDIT_SERVICES_LENDERS: Mapping[str, str] = MappingProxyType({
    "AXP": "American Express: card issuer with a loan book and deposits",
    "COF": "Capital One: bank holding company, card and auto lender",
    "SYF": "Synchrony: private-label card issuer funded by deposits",
    "ALLY": "Ally Financial: bank holding company, auto lender",
    "SOFI": "SoFi: chartered bank, personal and student loans",
    "SLM": "Sallie Mae: private student lender funded by deposits",
    "BFH": "Bread Financial: card issuer funded by deposits",
    "GDOT": "Green Dot: bank holding company (prepaid cards and deposits)",
    "KLAR": "Klarna: BNPL lender with a banking licence",
    "AFRM": "Affirm: BNPL lender carrying loans",
    "UPST": "Upstart: lending platform that holds loans on its balance sheet",
    "SEZL": "Sezzle: BNPL lender",
    "ENVA": "Enova: online consumer and small-business lender",
    "OPFI": "OppFi: consumer installment lender",
    "OMF": "OneMain: consumer installment lender",
    "CACC": "Credit Acceptance: subprime auto lender",
    "WRLD": "World Acceptance: consumer installment lender",
    "ATLC": "Atlanticus: credit-card and point-of-sale lender",
    "ECPG": "Encore Capital: debt buyer",
    "PRAA": "PRA Group: debt buyer",
    "JCAP": "Jefferson Capital: debt buyer",
    "PRG": "PROG Holdings: lease-to-own",
    "IX": "ORIX: leasing and lending conglomerate",
    "NNI": "Nelnet: student-loan holder and servicer, owns a bank",
    "NAVI": "Navient: student-loan holder",
    "AGM": "Farmer Mac: agricultural mortgage lender",
    "LU": "Lufax: consumer and small-business lender",
    "QFIN": "Qfin Holdings: consumer credit platform",
    "FINV": "FinVolution: consumer credit platform",
    "SWRD": "Stewards Inc.: revenue-based funding and other financing for small businesses",
    "FCFS": "FirstCash: pawn lender",
    "EZPW": "EZCORP: pawn lender",
})

#: Rows a curated per-company fact withholds wherever the gate decides company rows (the
#: Health Check, the health-snapshot fallback, the Overview fallback, the report's lines),
#: whatever the industry: ticker → {metric: reason}. Fail-closed and with NO expiry — an
#: entry leaves only when someone removes it by hand (OWNER_TASKS carries the recheck).
#: Owner decision 2026-10-09 (NET-5): a data heuristic on FMP's balance-sheet shape catches
#: WU's made-up split in only one quarter of twelve, so the fact is curated instead.
_WU_NO_SPLIT = (
    "WU's own SEC filings carry no current/non-current split (no us-gaap AssetsCurrent / "
    "LiabilitiesCurrent for CIK 1365135); FMP's split is made up (3.75 on 2026-06-30, ~1.0 "
    "or 7.12 in other quarters)"
)
CURATED_WITHHELD_ROWS: Mapping[str, Mapping[str, str]] = MappingProxyType({
    "WU": MappingProxyType({
        CURRENT_RATIO: _WU_NO_SPLIT,
        QUICK_RATIO: _WU_NO_SPLIT,
        INTEREST_COVERAGE: (
            "FMP's Q3-25 / Q4-25 quarterly interestExpense ($175.6M / $101.7M) is not WU's "
            "(SEC InterestExpenseDebt $37.0M / $36.7M), so the ratio (1.85, 'negative') is "
            "known-wrong; held until removed by hand (recheck after WU's Q4-26 report, "
            "~Feb 2027)"
        ),
    }),
})

#: A listed company whose own interest income is at least this share of revenue reads as a
#: LENDER — a data sanity check on the allow-list (`lender_verdict`), never a way in. The
#: 2026-10-08 probe's lenders that FMP did not zero-fill sit far above it (AXP 0.32, AFRM
#: 0.52, COF 0.85, SYF 1.18); the listed networks far below (V, MA, WU 0.00; PYPL, GPN 0.02).
LENDER_INTEREST_SHARE = 0.25

#: The income-statement fields the sanity check reads (FMP /stable names).
_REVENUE = "revenue"
_GROSS_INTEREST = "interestIncome"
_NET_INTEREST = "netInterestIncome"
_INTEREST_FIELDS = (_GROSS_INTEREST, _NET_INTEREST)

# Four quarters make the trailing twelve months `trailing_interest_row` sums.
_TTM_QUARTERS = 4


def is_mixed_lender_industry(industry: Any) -> bool:
    """True when ``industry`` holds both lenders and payment networks (the company's
    ticker decides its rows: `payment_network_verdict`)."""
    return industry_key(industry) in _MIXED_LENDER_INDUSTRIES


def normalize_ticker(ticker: Any) -> str:
    """Upper-case, whitespace-stripped ticker; "" for a non-string (no ticker)."""
    if not isinstance(ticker, str):
        return ""
    return ticker.strip().upper()


def _finite_number(value: Any) -> Optional[float]:
    """A finite int / float, else None — a bool, a string, NaN, ±inf or an int too large
    for a float is no reading."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)
    except OverflowError:
        return None
    return number if math.isfinite(number) else None


def _describe_shares(shares: Dict[str, float]) -> str:
    return ", ".join(f"{field}/revenue {share:.3f}" for field, share in shares.items())


def lender_verdict(income_row: Any) -> Tuple[Optional[bool], str]:
    """``(reads_as_lender, why)`` from one income-statement row covering a year (the latest
    annual row, or `trailing_interest_row`'s sum of four quarters) — the SANITY CHECK on a
    `NON_LENDER_MEMBERS` member, never a way into the list. Pure; ``why`` names both shares
    for the caller's log line (the caller has the ticker).

      * True  — ``interestIncome`` or ``netInterestIncome`` is at least
        `LENDER_INTEREST_SHARE` of revenue (one readable field is enough);
      * False — both fields readable, not both exactly 0, and both below the share;
      * None  — unknown: no row, revenue missing / non-numeric / ≤ 0, a field missing or
        junk (bool, string, NaN, ±inf), a NEGATIVE ``interestIncome`` (interest income
        cannot be negative: a sign-flipped or junk reading — review LND-3; a negative
        ``netInterestIncome`` is a real reading, V's interest on its own debt), or both
        exactly 0 (how FMP zero-fills a statement it did not parse). A listed network with
        an unknown verdict stays a network: the list is curated, the data only vetoes."""
    if not isinstance(income_row, dict):
        return None, f"no income row ({type(income_row).__name__})"
    revenue = _finite_number(income_row.get(_REVENUE))
    if revenue is None:
        return None, f"revenue missing or not a finite number ({income_row.get(_REVENUE)!r})"
    if revenue <= 0:
        return None, f"revenue {revenue!r} <= 0"
    shares: Dict[str, float] = {}
    unreadable = []
    for field in _INTEREST_FIELDS:
        raw = income_row.get(field)
        value = _finite_number(raw)
        if value is None:
            unreadable.append(f"{field}={raw!r} missing or not a finite number")
        elif field == _GROSS_INTEREST and value < 0:
            unreadable.append(f"{field}={raw!r} is negative (unreadable)")
        else:
            shares[field] = value / revenue
    described = _describe_shares(shares)
    if shares and max(shares.values()) >= LENDER_INTEREST_SHARE:
        return True, (
            f"{described} — at least {LENDER_INTEREST_SHARE:.2f} of revenue (a lender's "
            f"income)"
        )
    if unreadable:
        return None, "; ".join([*unreadable, *([described] if described else [])])
    if all(share == 0 for share in shares.values()):
        return None, "interestIncome and netInterestIncome are both exactly 0 (zero-filled?)"
    return False, f"{described} < {LENDER_INTEREST_SHARE:.2f}"


def trailing_interest_row(quarters: Any) -> Optional[Dict[str, Any]]:
    """The newest four quarterly income rows as ONE twelve-month row of the fields
    `lender_verdict` reads — for the callers that hold quarters, not an annual row (the
    Health Check, the Financial Health snapshot and the report collector already fetch
    them; no extra FMP call).

    Rows that are not dicts or carry no string ``date`` are dropped; a date seen twice
    counts once (the first row given for it); the rest sorts newest first. Fewer than four
    dated quarters → None (a part-year sum is no annual share). A field is summed only when
    all four quarters carry it as a finite number, else it is left out (the verdict then
    reads it as missing). ``date`` is the newest quarter's."""
    if not isinstance(quarters, list):
        return None
    by_date: Dict[str, Dict[str, Any]] = {}
    for row in quarters:
        if isinstance(row, dict) and isinstance(row.get("date"), str) and row["date"]:
            by_date.setdefault(row["date"], row)
    newest = [by_date[d] for d in sorted(by_date, reverse=True)[:_TTM_QUARTERS]]
    if len(newest) < _TTM_QUARTERS:
        return None
    out: Dict[str, Any] = {"date": newest[0]["date"], "period": "TTM"}
    for field in (_REVENUE, *_INTEREST_FIELDS):
        values = [_finite_number(row.get(field)) for row in newest]
        if all(v is not None for v in values):
            out[field] = sum(values)
    return out


class NetworkVerdict(NamedTuple):
    """`payment_network_verdict`'s answer: ``is_network`` (a listed non-lender member: keeps
    the rows, every metric judged without the industry median), ``reason`` for the log
    line, ``warn`` when the answer is degraded or suspicious (logged at WARNING), and
    ``category`` — `CATEGORY_PAYMENT_NETWORK` / `CATEGORY_FEE_BUSINESS` for a listed ticker
    (even one the data vetoed), else None. A future payments peer group must read the
    CATEGORY, never ``is_network``: TREE and PMTS are not payment networks."""

    is_network: bool
    reason: str
    warn: bool
    category: Optional[str] = None


def non_lender_category(ticker: Any) -> Optional[str]:
    """`CATEGORY_PAYMENT_NETWORK` or `CATEGORY_FEE_BUSINESS` for a curated non-lender member
    (normalised ticker), else None. Says nothing about the industry or the data."""
    symbol = normalize_ticker(ticker)
    if symbol in PAYMENT_NETWORKS:
        return CATEGORY_PAYMENT_NETWORK
    if symbol in CREDIT_SERVICES_FEE_BUSINESSES:
        return CATEGORY_FEE_BUSINESS
    return None


_CATEGORY_NOUN = {
    CATEGORY_PAYMENT_NETWORK: "a listed payment network",
    CATEGORY_FEE_BUSINESS: "a listed non-lender fee business",
}


def payment_network_verdict(ticker: Any, industry: Any, income_row: Any) -> NetworkVerdict:
    """Whether ``ticker`` is a listed NON-LENDER member of a mixed industry (a payment
    network or a non-lender fee business — `NON_LENDER_MEMBERS`). Pure (no logging). The
    name is kept from the networks-only first cut (every caller and test uses it).

    ``is_network`` is True only when ALL hold: the industry is mixed, the normalised ticker
    is in `NON_LENDER_MEMBERS`, and `lender_verdict(income_row)` is not True. So:
      * outside a mixed industry → False (nothing to decide; the industry gate rules);
      * no ticker (a non-string or blank) → False, ``warn`` (fail closed: a caller that
        cannot say which company it holds never frees a row);
      * a ticker not on the list → False (every other member stays gated, whatever its
        interest data);
      * a listed ticker whose income reads as a lender's → False, ``warn`` (the list is
        curated, the data can still veto it);
      * a listed ticker with unreadable interest data → True (the list stands).
    The reason names the category ("a listed payment network" / "a listed non-lender fee
    business"); ``category`` carries it for code."""
    if not is_mixed_lender_industry(industry):
        return NetworkVerdict(False, f"industry {industry!r} is not mixed", False)
    symbol = normalize_ticker(ticker)
    if not symbol:
        return NetworkVerdict(
            False, f"no ticker ({ticker!r}) — cannot check the non-lender member list "
            f"(fail closed)", True,
        )
    category = non_lender_category(symbol)
    if category is None:
        return NetworkVerdict(
            False, f"{symbol} is not a listed payment network or non-lender fee business",
            False,
        )
    noun = _CATEGORY_NOUN[category]
    lender, why = lender_verdict(income_row)
    if lender is True:
        return NetworkVerdict(
            False, f"{symbol} is {noun} but its income reads as a lender's ({why}) — gated "
            f"(fail closed); check the list and FMP's row", True, category,
        )
    if lender is None:
        return NetworkVerdict(
            True, f"{symbol} is {noun} (interest data unreadable: {why}; the list stands)",
            False, category,
        )
    return NetworkVerdict(True, f"{symbol} is {noun} ({why})", False, category)


def resolve_payment_network(
    ticker: Any, industry: Any, income_row: Any, *, source: str,
) -> bool:
    """`payment_network_verdict`, logged with the ticker, the caller (``source``: e.g.
    "health_check", "health_snapshot_fallback", "overview_fallback_health",
    "report_peer_lines", "profitability_snapshot", "valuation_snapshot", "growth",
    "profit_power") and the reason — at WARNING when the answer is degraded or suspicious.
    Outside a mixed industry: False, no log line (nothing was decided).

    ``income_row`` is ONE input everywhere (owner decision 2026-10-09):
    `trailing_interest_row(quarterly income)` where the caller already fetched the
    quarters, else None (the curated list stands) — never an annual row on one card only,
    or two cards of one company could disagree near the lender line."""
    if not is_mixed_lender_industry(industry):
        return False
    verdict = payment_network_verdict(ticker, industry, income_row)
    log = logger.warning if verdict.warn else logger.info
    log(
        "[network-gate] ticker=%s step=%s industry=%r: %s — %s",
        normalize_ticker(ticker) or repr(ticker), source, industry, verdict.reason,
        "a non-lender member: current ratio, quick ratio and interest coverage KEPT (unless "
        "a curated fact withholds one); EVERY metric judged without the industry median — "
        "absolute bands or unscored, no peer line, no peer wording (the industry median is "
        "a lenders' yardstick)" if verdict.is_network else
        "gated: current ratio, quick ratio and interest coverage omitted; its other "
        "metrics compared with the industry median, as a lender's",
    )
    return verdict.is_network


def withheld_company_rows(ticker: Any) -> Dict[str, str]:
    """``{metric: reason}`` the curated `CURATED_WITHHELD_ROWS` withholds for ``ticker``
    (normalised), whatever its industry; ``{}`` for every other company and for no
    ticker. Pure (no logging): `resolve_withheld_company_rows` logs."""
    rows = CURATED_WITHHELD_ROWS.get(normalize_ticker(ticker))
    return dict(rows) if rows else {}


def resolve_withheld_company_rows(ticker: Any, *, source: str) -> frozenset:
    """`withheld_company_rows`, logged ONCE (INFO, ``[curated-withheld]``) with the ticker,
    the caller and each row's reason when anything is withheld — call it once per build.
    Returns the withheld metric names. `company_metric_applicable` applies the same table
    on its own (a caller that skips this only loses the log line, never the gate)."""
    rows = withheld_company_rows(ticker)
    if rows:
        logger.info(
            "[curated-withheld] ticker=%s step=%s: %s withheld by a curated per-company "
            "fact (no expiry; removed only by hand) — %s",
            normalize_ticker(ticker), source, ", ".join(sorted(rows)),
            "; ".join(f"{metric}: {why}" for metric, why in sorted(rows.items())),
        )
    return frozenset(rows)


def company_metric_applicable(
    metric: str, industry: Optional[str], *, network: bool, ticker: Any,
) -> bool:
    """`peer_metric_applicable` for ONE company.

      * A row the curated `CURATED_WITHHELD_ROWS` withholds for ``ticker`` → False, in
        every industry (WU's current ratio, quick ratio and interest coverage).
      * In a mixed industry the gated metrics are kept only for a listed non-lender member
        (``network is True``, from `payment_network_verdict`); anything else — a lender, an
        unlisted member, no ticker — is gated.
      * Every other industry answers exactly as `peer_metric_applicable` does, whatever
        ``network`` says.

    ``ticker`` is REQUIRED (keyword, no default): a caller that forgot it would silently
    show WU's withheld rows. ``None`` is an honest "no ticker": nothing curated applies."""
    if metric in withheld_company_rows(ticker):
        return False
    if metric in GATED_METRICS and is_mixed_lender_industry(industry):
        return network is True
    return peer_metric_applicable(metric, industry)


def peer_median_comparable(
    metric: str, industry: Optional[str], *, network: bool = False,
) -> bool:
    """Whether a company's ``metric`` may be compared with a peer median at all.

      * EVERY metric: False for a listed non-lender member (``network is True``) of a mixed
        industry (owner decision 2026-10-09, NET-4) — the Credit Services median is a
        lenders' yardstick, so V's P/E, margins, ROE, growth and balance-sheet rows are
        judged on absolute bands (or not at all), with no line and no peer wording. Truthy
        junk (``1``, ``"yes"``) is not a verdict;
      * current ratio, quick ratio, interest coverage: False in every gated or mixed
        industry (a mixed industry's median pools lenders' meaningless values);
      * every other metric / industry: True — a lender there still compares everything
        else with its industry."""
    if network is True and is_mixed_lender_industry(industry):
        return False
    if metric in GATED_METRICS:
        if is_mixed_lender_industry(industry):
            return False
        return peer_metric_applicable(metric, industry)
    return True


def comparable_peer_metrics(
    metrics: Iterable[str], industry: Optional[str], *, network: bool,
) -> List[str]:
    """``metrics`` filtered to those `peer_median_comparable` allows, order preserved — the
    list a service asks the benchmark lookup for. ``[]`` for a listed non-lender member of a
    mixed industry: the caller then makes NO lookup and the build is not degraded (a peer-
    free card is a company state, not an outage). ``network`` is REQUIRED (keyword)."""
    return [m for m in metrics if peer_median_comparable(m, industry, network=network)]


def excluded_from_industry_median(ticker: Any, industry: Any) -> bool:
    """True when the benchmark PRODUCER must leave ``ticker`` out of ``industry``'s median:
    a curated non-lender member (`NON_LENDER_MEMBERS`, normalised) of a mixed industry, so
    the Credit Services median is computed from lenders only. The static list alone (the
    producer holds no income row); excluding a member from a median it is never compared
    with is harmless. They also leave the Financial Services sector pool (immaterial)."""
    return is_mixed_lender_industry(industry) and normalize_ticker(ticker) in NON_LENDER_MEMBERS
