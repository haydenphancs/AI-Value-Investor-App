"""Build the broad benchmark universe for INDUSTRY + SECTOR medians.

Per FMP industry, pulls every actively-traded, US-LISTED operating company ABOVE a
market-cap floor (default $500M — excludes micro/penny-stock noise while keeping
small+mid+large caps, so medians are fair to small-cap companies). Groups by industry +
modal parent sector, capturing each ticker's market cap.

US-listed operating companies only (owner decision 2026-10-07). The 2026-06-24 file sent
none of the filters below and carried ~1,275 open-end mutual funds / ETFs, 615 `.TO`
listings (147 of them a second copy of a US ticker), and an 'Asset Management' list cut
at exactly the 1,000-row `limit` (TROW, BEN, IVZ… missing). So:

  * The screener is asked for `isEtf=false`, `isFund=false`, `isActivelyTrading=true` and
    `exchange=NYSE,NASDAQ,AMEX` — the US-listing filter `price_service`'s universe sweep
    and `theme_rotation` already send. `exchange`, NOT `country=US`: `country` is the
    issuer's domicile, so it would drop US-listed foreign issuers (TSM, ASML, NVO — real
    NYSE/NASDAQ listings) while saying nothing about WHERE a row trades.
  * The market-cap floor is applied HERE, never sent as `marketCapMoreThan` (2026-10-08):
    FMP filters on a server-side cap of its own and HIDES every row whose server-side cap
    is null — even when the row it would return carries a real one. VMRK (Vivmark
    Residential, the EQR + AVB merger, $22.5B), VYLR (Vylor, $48.6B), SKYD (Skydance,
    $10.1B), LYNX, ADIG… — about 17 legitimate listings of $500M or more were missing from
    the 2026-10-08 file. Each industry is fetched at every cap and the floor is read off
    the row's own `marketCap` (`below_floor` / `bad_market_cap`, INFO: expected now). Which
    kept rows the old filter would have hidden is NOT knowable from the answer (their row
    cap looks normal), so it is not counted.
  * Every row is re-checked here, because a filter FMP silently ignores looks exactly like
    a clean answer. Dropped and counted: a row flagged `isEtf` / `isFund`; a dotted
    foreign-exchange suffix (`SHOP.TO`, `VOD.L` — FMP spells US share classes with a
    DASH, `BRK-B` / `BF-B`, and the 2026-06-24 file held no dotted US symbol); a non-US
    exchange; a cap that is missing, non-finite or under the floor. A fund / ETF / foreign
    drop means a server-side filter was not honoured, so it is logged at WARNING.
  * Only operating companies' COMMON shares vote (`not_common_share`, INFO — the screener
    cannot filter these). FMP lists an issuer's preferreds, notes, warrants, units and
    when-issued rows under the issuer's name and prices them with the issuer's share count
    (SOJC, a Southern Co. note: $20.7B; TBB, an AT&T note: $126B; EP-PC / MER-PK: $112B /
    $192B), so each would vote again in the issuer's median with junk ratios. The June file
    held ~15 of the 52 Regulated Electric names this way. Caught with the ticker-search
    grammar (`stock_search_service`), on symbol AND `companyName`: dash and NASDAQ
    5th-letter preferreds (EP-PC, FCNCN, AGNCP), notes named as notes (SOJC "… JR 2017B NT
    77", TBB "… 5.35% GLB NTS 66"), one-letter dash actions (ABC-U / -R / -W), a NASDAQ
    5th-letter W/U/R/V whose name says warrant / unit / right / when-issued, and twins of a
    base listing (SOMN beside SO, PMTU beside PMT, HONIV beside HON, CCXIW beside CCXI).
    Plus three debt words the search does not carry (`_BUILDER_DEBT_NAME_RE`): "bonds",
    "first mortgage" and "collateral trust" — a utility SUBSIDIARY's mortgage bonds
    ("Entergy Arkansas, LLC First Mortgage Bonds", EAI) carry the subsidiary's name, so no
    twin rule can pair them with the parent ("Collateral Tr" too: FMP's cut names, ELC).
  * Issuers checked BY HAND (`hand_checked`, INFO; `_HAND_CHECKED_ISSUERS`, 2026-10-08):
    notes, preferreds, partnership units and finance vehicles under a plain issuer name
    (MGR = AMG's notes, STRC = Strategy's preferred, FISK = ESRT's OP units, CCZ, BNH…),
    found by matching every row's TTM ratios against the rest of its industry. Keyed by
    issuer name; the issuer's operating common keeps its vote.
  * The twin rules read a MARKET-WIDE directory (every industry's common rows, collected
    before any filtering): FMP files a note under another industry than its issuer (CGABL in
    Financial - Credit Services beside CG in Asset Management; HONAV in Aerospace & Defense
    beside HON in Conglomerates). The issuer-name equality still guards distinct companies;
    every twin of a listing in ANOTHER industry is named at WARNING.
  * An ADR / GDS is common equity: "American Depositary / Depository Shares" and "Global
    Depositary / Depository Shares" never drop a row on their own — only beside a coupon,
    notes or preferred marker. A "Series B / A / L" beside it is KEPT: it names a Mexican
    or Chilean issuer's ordinary class (PAC, AMX, SQM, KOF, FMX).
  * An ADR over a foreign issuer's PREFERRED share CLASS is that issuer's equity and votes,
    once per issuer (owner call 2026-10-08): Brazil lists its main US equity on that class
    (ITUB, BBD, PBR-A, GGB, CIG, ELP). FMP's names for them carry no class at all (probe
    2026-10-08: "Itaú Unibanco Holding S.A.", "Banco Bradesco S.A."), so they were never
    dropped; a depositary description ("… American Depositary Shares, each representing
    one Preferred Share") is cut like the ADR phrase (`_ADR_PREFERRED_CLASS_RE`) so it
    cannot drop them either — only when it ENDS the name. A coupon, "non-cumulative", a
    "Series", a fractional interest, words after the description, or a fixed-income word
    anywhere in the name (cumulative, perpetual, redeemable, callable, convertible,
    liquidation, floating, fixed, funding, … — `_PREFERRED_FIXED_INCOME_WORD_RE`) still
    drops the row as a fixed-income preferred.
  * A name that says Fund / ETF / ETN is dropped (`fund_name`, INFO): FMP's `isFund` marks
    open-end mutual funds only, so closed-end funds ("… Income Fund") pass the server filter.
    A trust that only reads like a fund is kept and NAMED at WARNING — "Trust" also names
    operating REITs and royalty trusts. A business development company is an operating
    company (it files a 10-K) and votes (ARCC, OBDC, FSK, MAIN) — and so does a listed BDC
    whose name says "Lending Fund" (MSDL "Morgan Stanley Direct Lending Fund", BXSL
    "Blackstone Secured Lending Fund"; owner call 2026-10-08, `_is_listed_lending_fund`).
    That exemption is the whole-word phrase only, on a row FMP flags `isFund=false`, with no
    other fund / ETF word and no fifth-letter-X fund symbol: "Cohen & Steers Infrastructure
    Fund" and "Cliffwater Corporate Lending Fund" (CCLFX, an interval fund) still drop. Every
    row kept by either owner call is named at INFO (`_log_owner_call_keeps`). A closed-end
    fund whose name says neither Fund nor Trust (Tri-Continental Corporation) is neither
    dropped nor named.
  * A kept row trading under 0.02% of its reported cap a day (price × avgVolume) is NAMED
    at WARNING: the signature of a note FMP prices with the issuer's share count that no
    name rule could read (FMP cuts these names at ~31 characters: "Entergy Louisiana, LLC
    Collater", "PPL Capital Funding, Inc. 2007"). A thinly traded ADR can show it too, so
    it is a list to read, not a drop.
  * One vote per issuer INSIDE AN INDUSTRY (`same_issuer`, INFO): GOOG + GOOGL, BRK-A +
    BRK-B, FOX + FOXA each carry the issuer's fundamentals, so only the most liquid class of
    each is kept. So do Bradesco's preferred and common ADRs (BBD + BBDO) and Petrobras's
    (PBR + PBR-A): one issuer name, and symbols that read as classes of one listing. Not a
    market-wide guarantee: classes FMP files under DIFFERENT industries (the June file had
    BBDO under Banks, BBD under Banks - Regional) are both kept — each votes in its own
    industry, and both in a shared sector's median — and every such pair is named at
    WARNING (`_cross_industry_share_classes`) for the owner to read before uploading.
  * One vote per SET OF STATEMENTS (`same_statements`, INFO, one line per drop; 2026-10-08):
    after the filters, every kept row's `ratios-ttm` is fetched (paced: `_TWIN_CONCURRENCY`
    at `_TWIN_CALLS_PER_SECOND`) and its five price-free ratios (gross / operating / net
    margin, current ratio, D/E) are its fingerprint. Rows whose five slots are EXACTLY equal,
    with at least `_TWIN_MIN_INFORMATIVE` informative non-zero values, report one company's
    statements under two listings (a note, preferred, OP unit, re-pointed SPAC, share class
    — APXT/AVPT, FWONA/FWONK, BN/BNH were the 2026-10-08 scan's pairs, and none was a false
    match over 2,971 rows). Inside an industry the most liquid one votes (`_vote_order`);
    ACROSS industries each industry's vote is KEPT and the group is named at WARNING (with
    any same-industry member it lost) — which class
    belongs to which industry is FMP's call (B2). A failed fingerprint call keeps its row
    and is named at WARNING; more than `_TWIN_MAX_FAILURE_PERCENT`% failed is an outage and
    fails the build (exit 1). A row whose `ratios-ttm` is `[]` has no statements at FMP (it
    can vote in no median) and is named in one WARNING. `--skip-twin-scan` skips the pass
    for a quick local run (WARNING) — never for a file that is uploaded. Blind spot: only
    KEPT rows are fingerprinted, so a note whose issuer's own common is NOT kept (a closed-
    end fund's notes — ECCU over ECC — or a parent under the floor) has no twin to match and
    still votes; the 0.02%-turnover WARNING and `_HAND_CHECKED_ISSUERS` are its guard.
  * Credit Services review (2026-10-09): a member of a MIXED industry
    (`financials_metric_gate.is_mixed_lender_industry`) that is neither a curated non-lender
    (`NON_LENDER_MEMBERS`) nor a reviewed lender (`REVIEWED_CREDIT_SERVICES_LENDERS`) is
    named at WARNING. It is gated as a lender until classified — the safe default — but a
    new payment business there would lose its liquidity rows and be compared with lenders.
  * An industry is read in ONE screener call of `_SCREENER_PAGE_LIMIT` (5,000) rows — the
    largest US industry at every cap was 607 (Biotechnology, 2026-10-08). A FULL page
    FAILS the build instead of being paged (2026-10-09 review): a second call cannot be
    made consistent with the first — a row that leaves the listing between the two calls
    shifts the boundary, so the first row of the next page lands on neither page, and
    nothing repeats to show it. A full page means the industry outgrew the limit: raise
    `_SCREENER_PAGE_LIMIT` (the screener serves up to 10,000 rows a call).
  * Any failed request (a 429 is retried with backoff first) FAILS the build: exit 1,
    nothing written. A universe missing an industry empties that industry's peer
    medians until the next build — worse than keeping the previous file.
  * The build is compared with the file it replaces and REFUSED (exit 3, nothing
    written) when:
      - its `market_cap_floor` differs from the previous file's, unless
        `--allow-floor-change` (a previous file without the key: WARNING). `--floor 0`
        aimed at this file without `--output` would otherwise replace the $500M universe
        with the floor-0 industry file — growth the shrink guard never refuses;
      - the ticker count drops by more than 10%. `--allow-shrink PCT` raises that bar to
        PCT% (50 when the flag is given bare) — never further: an override that let any
        drop through would also wave through the soft failure it exists to catch;
      - an industry that held at least 20 operating tickers (no dotted suffix, not a
        5-letter X fund symbol) has none now, unless it is named with `--allow-missing`.
        A screener that answers a real industry with an empty page looks exactly like an
        industry that left, and its peer medians would be empty until the next build.

This is a SEPARATE file from `industry_universe.json` (NO floor; feeds the moat / dossier
jobs and the report's competitor candidates) — do not conflate them. That file is built by
THIS builder at floor 0 through `scripts/discover_industries.py`, which writes it to its own
path. Output here: backend/data/benchmark_universe.json, format unchanged (read by
`industry_benchmark_service._load_universe` through `universe_data.load_universe`). It is
NOT in git (FMP ToS §2.6.1): after a build, upload it to the private `universe-data`
Supabase Storage bucket.
~160 screener-side FMP calls (1 available-industries + ~159 screener calls) plus one
`ratios-ttm` call per kept row (~3,000 here, ~5,500 for the floor-0 industry file): about
10 minutes here, ~19 for the industry file, paced at `_TWIN_CALLS_PER_SECOND` (the runtime
is logged). Run it outside Sun 02:00-08:00 UTC and never while a benchmark sweep runs — it
shares the production FMP key.

Usage (from backend/):
    ./venv/bin/python -m scripts.build_benchmark_universe                    # $500M floor
    ./venv/bin/python -m scripts.build_benchmark_universe --floor 1000000000 \
        --allow-floor-change                                                  # $1B floor

    # The FIRST US-only regeneration (2026-10) shrinks the file by far more than 10%: the
    # June file held 615 dotted rows, 1,292 five-letter-X funds and ~800 ETF-like rows in
    # the 'Asset Management*' industries, so about 5,704 → ~3,100 (~45%). It needs the
    # override — once. Industries that only held funds leave with them: the refusal names
    # each one and the exact flag to add, after you have checked it really left:
    ./venv/bin/python -m scripts.build_benchmark_universe --allow-shrink 50 \
        --allow-missing "Asset Management - Bonds" --allow-missing "Asset Management - Global"

The shrink guard compares with the CURRENT local file. The live copy is the one in the
bucket: download it to backend/data/ first, or the guard has no baseline (WARNING, the
build proceeds).

Exit codes: 0 written · 1 a request failed, an industry would be truncated, more than
`_TWIN_MAX_FAILURE_PERCENT`% of the fingerprint calls failed, or nothing usable came back
(nothing written) · 3 shrink refused, an industry went missing, or the floor changed
(nothing written).

Idempotent — re-running overwrites the file with a fresh snapshot (written atomically).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import math
import re
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, Iterable, List, NamedTuple, Optional, Tuple, Union

import httpx

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from app.integrations.fmp import (  # noqa: E402
    FMPClient,
    FMPException,
    FMPRateLimitException,
)
from app.log_redaction import SecretRedactingFilter, redact_secrets  # noqa: E402
from app.schemas.stock import StockSearchResult  # noqa: E402
# The ticker-search listing grammar, REUSED rather than re-derived: it was built from a live
# sweep of FMP's own rows for exactly these families (dash and NASDAQ 5th-letter preferreds,
# notes on bare tickers, corporate-action and same-issuer twins) and is pinned row by row by
# tests/test_stock_search_listing_rules.py.
from app.services.financials_metric_gate import (  # noqa: E402
    NON_LENDER_MEMBERS,
    REVIEWED_CREDIT_SERVICES_LENDERS,
    is_mixed_lender_industry,
    normalize_ticker,
)
from app.services.stock_search_service import (  # noqa: E402
    _DASH_ACTION_SUFFIX_RE,
    _DEBT_PREF_NAME_RE,
    _MAX_NAME_CHARS,
    _dedupe_secondary_listings,
    _grammar_drop_reason,
    _is_root_twin,
    _is_root_twin_candidate,
    _issuer_key,
    _secondary_base_symbol,
)

logger = logging.getLogger(__name__)
# FMP puts `apikey=` in the query string and a raw httpx error repeats the whole URL. Every
# record THIS module logs is scrubbed here — message and traceback — whoever configured
# logging (a test, another script calling `main()`); `__main__` also filters the root
# handlers, as app/main.py does, for the other modules' records.
logger.addFilter(SecretRedactingFilter())

_OUTPUT_PATH = _REPO_ROOT / "data" / "benchmark_universe.json"
_DEFAULT_FLOOR = 500_000_000  # $500M — small-cap inclusive, micro/penny excluded

# US listing venues, as the screener's `exchange` filter spells them.
_US_EXCHANGES: Tuple[str, ...] = ("NYSE", "NASDAQ", "AMEX")
# A row's own exchange is accepted when it IS one of these or starts with one plus a space
# ("NASDAQ Global Select", "NYSE American") — the long form some rows carry in `exchange`.
_US_EXCHANGE_PREFIXES: Tuple[str, ...] = _US_EXCHANGES + ("NEW YORK STOCK EXCHANGE",)

# Rows per screener call (the screener caps a call at 10,000 rows whatever `limit` says —
# `FMPClient.get_company_screener`). With no server-side cap filter (see the module
# docstring) every cap comes back: the largest US industry was 607 rows (Biotechnology,
# probe 2026-10-08), and the WHOLE active, fund-free US market (the same exchange / isEtf /
# isFund / isActivelyTrading filters, no industry) was 6,073 rows in one call that day. So
# 5,000 keeps every industry in ONE call, and `_screener_for_industry` refuses a full one
# rather than page it — a second page can lose a row between two calls (module docstring).
_SCREENER_PAGE_LIMIT = 5000

# A 429 is backpressure, not an answer: retried on this schedule (or the server's own
# Retry-After, capped) before the industry counts as failed.
_RATE_LIMIT_BACKOFF_SECONDS: Tuple[float, ...] = (5.0, 15.0, 45.0)
_MAX_RETRY_AFTER_SECONDS = 120.0

_CONCURRENCY = 8

# The build is refused when it would drop MORE than this share of the previous tickers.
_MAX_SHRINK_PERCENT = 10
# `--allow-shrink` given with no value. Bounded on purpose: the first US-only build is
# expected to drop ~45%, and a bar any larger would also pass an industry that FMP
# soft-failed with an empty page on that same run.
_DEFAULT_ALLOW_SHRINK_PERCENT = 50
# A previous industry with at least this many operating tickers (no dotted suffix, not a
# 5-letter X fund symbol) that has none now refuses the build unless it is named with
# `--allow-missing`. Smaller ones only get the 'no constituents now' WARNING.
_MISSING_INDUSTRY_MIN_TICKERS = 20

EXIT_OK = 0
EXIT_BUILD_FAILED = 1
EXIT_SHRINK_REFUSED = 3

# The SHAPE of a US common-share symbol: 1-5 letters, optionally a one-letter class after a
# dash (BRK-B, BF-B, MKC-V). Only a first gate — the dash preferreds (EP-PC) and the
# two-letter dash actions (-WT, -UN, -RI) fail it, but notes on bare tickers (SOJC, TBB),
# NASDAQ 5th-letter preferreds (FCNCN, AGNCP), warrants (CCXIW), when-issued rows (HONIV)
# and one-letter dash actions (ABC-U) all match it. `_listing_reason` and the twin rules in
# `_filter_rows` catch those.
_COMMON_SHARE_SYMBOL_RE = re.compile(r"^[A-Z]{1,5}(?:-[A-Z])?$")

# The search grammar's verdicts that are NOT a drop here. Its "mutual_fund" (a NASDAQ
# 5-letter X) is left to FMP's `isFund` flag, this builder's authority for funds; an
# unflagged one is kept and named (`_log_fund_like_symbols`).
_SEARCH_GRAMMAR_KEPT = frozenset({"mutual_fund"})

# NASDAQ's fifth-letter codes for a warrant (W), unit (U), right (R) and when-issued (V)
# listing. A symbol of this shape is dropped only with proof: a same-issuer base row (the
# twin rules) or a name that says what it is. One kept without either is named at WARNING.
_NASDAQ_ACTION_SYMBOL_RE = re.compile(r"^[A-Z]{4}[WURV]$")
# Consulted ONLY for that symbol shape: "Unit Corporation" is an operating company.
_ACTION_NAME_RE = re.compile(r"\b(?:warrants?|wts?|units?|rights?|when[\s-]?issued)\b",
                             re.IGNORECASE)

# A name that SAYS fund / ETF / ETN: the same two patterns `stocks._get_asset_type` lets win
# outright (copied, not imported, so this script does not load the endpoint layer;
# tests/test_benchmark_universe_builder_round2_listings.py pins them equal). FMP's `isFund`
# marks open-end mutual funds; closed-end funds ("Eaton Vance Limited Duration Income Fund",
# "CBRE Global Real Estate Income Fund") come back isFund=false and would vote in the
# 'Asset Management - *' medians and the Financial Services sector median. "Trust" alone is
# NOT a fund word — Federal Realty Investment Trust, Sabine Royalty Trust and Northern Trust
# operate — so a trust that only READS like a fund is named, not dropped (`_log_suspect_rows`).
_FUND_NAME_RE = re.compile(r"\bfunds?\b", re.IGNORECASE)
_ETF_NAME_RE = re.compile(r"\b(?:etfs?|etns?|adrhedged)\b", re.IGNORECASE)
_TRUST_RE = re.compile(r"\btrust\b", re.IGNORECASE)
# Read only beside "Trust". The second line (round 3) adds the closed-end and physical-metal
# trust forms the first missed: "Royce Value Trust", "The Gabelli Equity Trust", "BlackRock
# Science and Technology Trust", "BlackRock Capital Allocation Term Trust", "Sprott Physical
# Gold and Silver Trust", "Royce Micro-Cap Trust" — none of them is an operating REIT's or a
# royalty trust's word.
_FUND_STYLE_WORD_RE = re.compile(
    r"\b(?:income|municipal|muni|bonds?|dividend|premium|yield|credit|opportunit(?:y|ies)"
    r"|strateg(?:y|ies|ic)|buy[-\s]?write|tax[-\s](?:advantaged|exempt|free|managed)"
    r"|value|equity|physical|term|allocation|science|technology|(?:micro|small|mid)[-\s]?cap)\b",
    re.IGNORECASE,
)

# A listed business development company named "… Lending Fund" (owner call 2026-10-08). FMP
# profiles: MSDL "Morgan Stanley Direct Lending Fund" (Financial - Conglomerates, isFund
# false, NYSE, $1.1B) and BXSL "Blackstone Secured Lending Fund" (Asset Management, isFund
# false, $5.6B) — BDCs that file a 10-K like the ~40 peers that already vote (ARCC, OBDC,
# FSK, MAIN). The fund-name rule cannot tell them from a closed-end fund, so this whole-word
# phrase is exempted, and nothing wider: `_is_listed_lending_fund` holds the other gates.
# The search endpoint types the same rows "stock" (`stocks._is_listed_lending_fund`, the
# same gates; a search row usually carries no isFund flag) with this pattern, pinned equal.
_LENDING_FUND_RE = re.compile(r"\blending\s+fund\b", re.IGNORECASE)

# Debt words the shared search grammar does not carry — applied HERE only (the search keeps
# its own vocabulary). A utility subsidiary's mortgage bonds list under the SUBSIDIARY's name
# ("Entergy Arkansas, LLC First Mortgage Bonds", EAI $0.94B; "Entergy Louisiana, LLC
# Collateral Trust Mortgage Bonds", ELC $37.75B in the June file), so no twin rule can pair
# them with the parent (ETR). Plural "bonds" only: "BlackRock Taxable Municipal Bond Trust"
# is a closed-end fund named at WARNING, not a note. "Debentures" is already in the shared
# `_DEBT_PREF_NAME_RE`.
# "Collateral Tr" too: FMP cuts names at ~40 characters, and an abbreviation survives where
# the full word does not ("Entergy Louisiana, LLC COLLATERAL TR MT", ELC — Entergy's mortgage
# bonds, priced off the subsidiary's share count, 2026-10-08).
_BUILDER_DEBT_NAME_RE = re.compile(
    r"\bbonds\b|\bfirst\s+mortgage\b|\bcollateral\s+tr(?:ust)?\b", re.IGNORECASE,
)

# Issuers FMP lists through a security that is NOT their operating common — a note, a
# preferred, an operating-partnership unit, a finance vehicle — under a plain issuer name with
# no debt word, so no name or symbol rule can see it; each would vote its parent's (or a funding
# vehicle's) statements again. Found 2026-10-08 by fetching the TTM ratios of every row of the
# US-only file (seven same-industry pairs had identical margins, current ratio, D/E and ROE) and
# reading the rows under 0.02% daily turnover, then confirmed on each FMP profile (one CIK with
# the parent). Keyed by `_issuer_key`; the value is (the symbol that IS the operating common,
# kept — or None — and what the other rows are). A row of one of these issuers is dropped as
# `hand_checked` unless it is that common. Keyed by NAME, never by symbol: dropping MGR alone
# let its sibling note MGRB win the one-vote-per-issuer rule and vote in its place, and a reused
# symbol under a new name is never dropped by an old entry.
_HAND_CHECKED_ISSUERS: Dict[str, Tuple[Optional[str], str]] = {
    "affiliated managers group": ("AMG", "junior subordinated notes (MGR, MGRB, MGRD, MGRE)"),
    "reinsurance group of america": ("RGA", "subordinated debentures (RZC, RZB)"),
    "hercules capital": ("HTGC", "notes (HCXY)"),
    "strategy": ("MSTR", "perpetual preferreds (STRC, STRK, STRF, STRD)"),
    # FWONK votes under "Liberty Media Corporation" today; exempt by symbol all the same, so
    # a rename to "Formula One Group" can never drop both series.
    "formula one group": (
        "FWONK", "Series A of the Formula One tracking stock (FWONA); FWONK, which FMP names "
        "\"Liberty Media Corporation\", votes for it",
    ),
    "comcast holdings": (None, "exchangeable subordinated debentures (CCZ); CMCSA votes"),
    "brookfield finance": (None, "Brookfield's finance subsidiary's notes (BNH); BN votes"),
    "empire state realty op": (None, "operating-partnership units (FISK, OGCP, ESBA); ESRT votes"),
    "transcanada pipelines": (None, "TransCanada PipeLines notes (TCPA); TRP votes"),
    "kkr group finance ix": (None, "subordinated notes (KKRS); KKR votes"),
    "aegon funding": (None, "subordinated notes (AEFC); AEG votes"),
    "maiden holdings north america": (None, "senior notes (MHNC)"),
    "bip bermuda holdings i": (None, "perpetual preferred (BIPI); BIP votes"),
    "apex treasury": (
        None, "a blank-check company (APXT) that FMP serves AvePoint's statements for — "
        "AvePoint's former SPAC symbol; AVPT votes",
    ),
    # Profile probes 2026-10-08 (read-only). CRBD "Corebridge Financial Inc. 6.375" shares
    # CRBG's CIK 0001889539 and FMP holds no ratios for it. Priced at $0.50B, it sits right
    # on the $500M floor (and always in the floor-0 industry file), under Insurance - Life;
    # CRBG votes in Asset Management.
    "corebridge financial": ("CRBG", "6.375% junior subordinated debentures (CRBD); CRBG votes"),
    # SPME "Sound Point Meridian Capital, Inc." ($24.75, ~3k shares a day) shares CIK
    # 0001930147 with SPMC, which FMP flags isFund: a closed-end CLO fund. FMP holds NO
    # statements for SPME (ratios-ttm, key-metrics-ttm, income and balance sheet all []), so
    # it voted in no median and dropping it changes none — it goes because the universe is
    # US-listed operating companies only.
    "sound point meridian capital": (
        None, "a closed-end CLO fund (SPMC, isFund) and its listed security (SPME); FMP holds "
        "no statements for SPME, so no median changes",
    ),
    # RML "Resolution Minerals Ltd. Sponsored ADR": profile cap $13.0B at $5.26 and ~117k
    # ADRs a day (0.005% of that cap a day), where the ordinary share (RML.AX) is A$44.5M —
    # the ADR priced on the ordinary share count. It would have been #1 in Other Precious
    # Metals. FMP holds no statements for it.
    "resolution minerals sponsored adr": (
        None, "an ADR cap priced on the ordinary share count (RML: $13.0B vs A$44.5M for "
        "RML.AX)",
    ),
    # The same defect, checked against FMP's OWN share count (`shares-float`, read-only
    # probes 2026-10-09) where no home listing is served. PHOS "First Phosphate Corp.
    # Sponsored ADR": profile cap $2,387,909,297 at $13.27 = 179.95M shares, exactly 10 ×
    # its outstandingShares 17,994,795 — the real cap is ~$239M, under the $500M floor (it
    # would have been #7 in Other Precious Metals). SGLD "Scorpio Gold Corporation American
    # Depositary Shares" (key "scorpio gold american": `_issuer_key` keeps the word):
    # $1,054,315,395 at $3.48 = 302.96M shares, exactly 20 × its outstandingShares
    # 15,148,210 and equal to the TSXV ordinary's (SGN.V) 302,964,000 shares at C$0.355
    # (C$107.6M) — the real cap is ~$53M. It turns over 0.22% of that cap a day, so the
    # thin-turnover WARNING never named it. FMP holds no statements for either
    # (`ratios-ttm` []), so no median changes; both leave the floor-0 industry file's HHI and
    # cap-ranked competitor candidates, which would carry the 10× / 20× caps.
    "first phosphate sponsored adr": (
        None, "an ADR cap priced on the ordinary share count (PHOS: $2.39B = 10 × FMP's own "
        "17,994,795 outstanding shares at $13.27; ~$239M)",
    ),
    "scorpio gold american": (
        None, "an ADR cap priced on the ordinary share count (SGLD: $1.05B = 20 × FMP's own "
        "15,148,210 outstanding shares at $3.48 — SGN.V's 302,964,000 ordinaries; ~$53M)",
    ),
    # Admitted when the server-side cap filter went (2026-10-09 build), confirmed by FMP
    # profiles (read-only): $25-par securities FMP prices with the issuer's share count.
    # TRNI "Trinity Capital Inc." shares TRIN's CIK 0001786108 (listed 2026-07-27, no FMP
    # statements) and sat in Credit Services while TRIN votes in Asset Management.
    "trinity capital": ("TRIN", "notes (TRNI); TRIN votes"),
    # DCBG "Dime Commercial Bancshares, Inc." (FMP's misspelling; CUSIP 25432X300 beside
    # DCOM's 25432X102, listed 2026-04-07) votes ratios near DCOM's — never exact, so the
    # statement-twin pass cannot pair them.
    "dime commercial bancshares": (
        None, "a Dime Community Bancshares security (DCBG, CUSIP 25432X300 beside DCOM's "
        "25432X102); DCOM votes",
    ),
    # ECCU "Eagle Point Credit Company Inc.": notes of ECC, a closed-end fund (CIK
    # 0001604174) that is itself never kept — so the twin pass has no common to match and the
    # notes voted the fund's statements in Asset Management (ECCC / ECCV were its siblings).
    "eagle point credit": (
        None, "notes of a closed-end fund (ECCU, ECCC, ECCV; ECC is the fund)",
    ),
    # AOMN "Angel Oak Mortgage REIT, Inc. 9": 9.5% notes sharing AOMR's CIK 0001766478;
    # AOMR (key "angel oak mortgage") sits under the floor, so the notes voted alone in
    # REIT - Mortgage.
    "angel oak mortgage reit": (None, "notes (AOMN); the common is AOMR"),
    # The Tennessee Valley Authority issues no stock: TVC and TVE are its power bonds
    # (identical TTM ratios, filed under two utility industries, ~$10M caps) — the 2026-10-09
    # floor-0 industry build named them as a cross-industry statement twin.
    "tennessee valley authority": (None, "power bonds (TVC); TVA issues no stock"),
    "tennessee valley authority parrs a": (None, "PARRS bonds (TVE); TVA issues no stock"),
}

# A depositary receipt over a foreign issuer's ORDINARY shares is that issuer's US common
# listing (ARM, TSM, NVO). The shared grammar spares only the exact "American Depositary
# Shares" spelling (a look-behind); "American Depository Shares" and "Global Depositary /
# Depository Shares" hit its depositary-share branches, which exist for PREFERRED
# depositary shares (RILYL "… Depositary Shares … Preferred Stock", GOOGN "Depository Shs
# Repr 1/20th Conv Pfd"). So the ADR / GDS phrase is cut out of the name before the debt /
# preferred rules read it: a coupon, "notes", "pfd" or "preferred stock" left over still
# drops the row, and so does a PREFERRED marker beside the ADR phrase (`_ADR_NON_COMMON_RE`:
# "%", "pfd", "non-cum", "pref", "preferred", "preference shares" — the first three are the
# shared rule's too, restated so the ADR vocabulary reads in one place; bare "pref" /
# "preferred" and "preference shares" are this rule's alone).
# Never "series" (round 5, B4-1): a Mexican or Chilean issuer's ORDINARY voting class is
# named "Series B" / "Series A" / "Series L" ("Grupo Aeroportuario del Pacífico, S.A.B. de
# C.V. American Depositary Shares, each representing 10 Series B shares" — PAC, ASR, OMAB,
# AMX, SQM, KOF, FMX), so a series word beside an ADR phrase is that issuer's common.
_ADR_PHRASE_RE = re.compile(
    r"\b(?:american|global)\s+deposit(?:a|o)ry\s+(?:shares?|shs|receipts?)\b",
    re.IGNORECASE,
)
_ADR_NON_COMMON_RE = re.compile(
    r"%|\bpfd\b|\bnon[-\s]?cum|\bpref(?:erred)?\b|\bpreference\s+(?:shares?|shs|stock)\b",
    re.IGNORECASE,
)
# An ADR over a foreign issuer's PREFERRED share CLASS is that issuer's equity (owner call
# 2026-10-08): Brazilian issuers list their main US equity on it (ITUB, BBD, PBR-A, GGB,
# CIG, ELP), and the row carries the issuer's fundamentals like any other class. FMP's names
# for these rows name no class (probe 2026-10-08), so this only keeps the depositary's own
# description form from dropping them: "… American Depositary Shares, each representing one
# Preferred Share" / "… representing 2 Class B Preferred Shares". Read ONLY beside an ADR /
# GDS phrase, and only a COUNT (plus an optional "Class X") may stand between "representing"
# and "preferred share(s)" — so a coupon, "non-cumulative", "Series A" or a fractional
# interest ("representing a 1/40th interest in a share of … Preferred Stock") never matches
# and the markers above still drop it. The description must also END the name (trailing
# punctuation aside; B1, 2026-10-08): the words a fixed-income preferred carries usually
# come AFTER it ("… each representing one Preferred Share, Cumulative" / "… Liquidation
# Preference $25"), and `_ADR_NON_COMMON_RE` does not know them. And the cut is refused
# (`_without_preferred_class`) when the name says "series" anywhere — a preferred SERIES is
# a fixed-income issue — or carries a fixed-income word anywhere
# (`_PREFERRED_FIXED_INCOME_WORD_RE`: "XYZ Capital Funding Trust American Depositary Shares,
# each representing one Preferred Share" is a trust preferred). A dash or NASDAQ fifth-letter
# preferred symbol is dropped on its symbol before any name is read.
_ADR_PREFERRED_CLASS_RE = re.compile(
    r"\brepresenting\s+(?:one|two|three|four|five|six|seven|eight|nine|ten|an?|\d{1,4})\s+"
    r"(?:class\s+[a-z]\s+)?preferred\s+shares?\b[\s,.;)]*\Z",
    re.IGNORECASE,
)
_SERIES_WORD_RE = re.compile(r"\bseries\b", re.IGNORECASE)
# Whole words (plus a dollar par value) that make a preferred a FIXED-INCOME issue, never a
# share class: the verifier's B1 endings (cumulative, perpetual, redeemable, callable,
# convertible, liquidation, floating, fixed, funding) and their close kin. "Non-Cumulative"
# and "Fixed-to-Floating" match too (a hyphen is a word boundary). None of them appears in a
# Brazilian issuer's name (ITUB, BBD, PBR-A, GGB, CIG, ELP, EBR-B).
_PREFERRED_FIXED_INCOME_WORD_RE = re.compile(
    r"\b(?:cumulative|perpetual|redeemable|callable|convertible|exchangeable|liquidation"
    r"|floating|fixed|variable|adjustable|reset|auction|funding|trust|preference)\b"
    r"|\$\s?\d",
    re.IGNORECASE,
)

# A kept row whose daily dollar volume (price × avgVolume) is under this share of its
# reported cap is named at WARNING. A note priced with the issuer's share count trades a
# sliver of that cap: SOJC $0.84M a day against $20.7B (0.004%); a common trades far more
# (DUK ~0.38%).
_THIN_DAILY_TURNOVER = 0.0002   # 0.02%

# A row dropped ONLY for its cap is still a live US common listing, so it can be the base
# that proves another row a twin (a SPAC's unit can sit over the floor while its common is
# just under it).
_TWIN_BASE_REASONS = frozenset({"below_floor", "bad_market_cap"})
_SUSPECTS_NAMED = 30

# Drop reasons that mean a SERVER-side filter was not honoured (WARNING), as opposed to
# what the screener cannot filter at all or a known drift (INFO, with the note below).
# `bad_market_cap` is not one of them since the floor moved client-side (2026-10-08): the
# screener now answers every cap, zero and missing ones included (Shell Companies held 9
# zero-cap rows on the 2026-10-08 probe).
_UNEXPECTED_DROP_REASONS = frozenset({
    "etf", "fund", "inactive", "foreign_suffix", "non_us_exchange", "malformed",
})
_EXPECTED_DROP_NOTES = {
    "not_common_share": (
        "preferreds, notes, warrants, units, rights and when-issued listings (by symbol, "
        "name, or as a same-issuer twin), which the screener cannot filter"
    ),
    "fund_name": (
        "named as a fund / ETF / ETN — FMP's isFund flags open-end funds only, so "
        "closed-end funds pass the server filter"
    ),
    "same_issuer": (
        "another share class of an issuer already counted in the same industry (one vote "
        "per issuer per industry: the most liquid class is kept)"
    ),
    "hand_checked": (
        "a note, preferred, partnership unit or finance vehicle FMP lists under a plain "
        "issuer name, or a symbol FMP serves another company's statements for — checked by "
        "hand (`_HAND_CHECKED_ISSUERS`, 2026-10-08)"
    ),
    # The screener is asked for every cap (`marketCapMoreThan` hid real companies), so most
    # rows of most industries fall under a $500M floor: the floor is applied HERE.
    "below_floor": (
        "under the market-cap floor, applied here on the row's own marketCap (never sent "
        "as marketCapMoreThan: FMP hides rows whose server-side cap is null)"
    ),
    "bad_market_cap": (
        "no usable marketCap on the row (missing, zero, non-finite) — the screener answers "
        "every cap now, so this is FMP's own data, not an ignored filter"
    ),
    "same_statements": (
        "one company's statements under a second listing in the same industry (all five "
        "TTM ratios equal): the most liquid listing votes — each one named above"
    ),
}
_DROP_EXAMPLES = 5

Sleep = Callable[[float], Awaitable[Any]]


class UniverseBuildError(Exception):
    """An FMP answer the build cannot use. Fails the whole build (exit 1)."""


class UnusableAnswerError(UniverseBuildError):
    """FMP answered, but not with a list of rows (or with no industry at all)."""


class TruncatedIndustryError(UniverseBuildError):
    """An industry the screener cannot return completely — never written truncated."""


# Failures whose message says it all; anything else is a bug and gets its stack.
_EXPECTED_FAILURES = (FMPException, httpx.HTTPError, UniverseBuildError)


def _describe(exc: BaseException) -> str:
    """`Type: message` with secrets scrubbed — the ONLY form an exception takes in this
    script's log lines and failure summary. `FMPClient` re-raises a raw
    `httpx.HTTPStatusError` for a 400 / 403 / 404 / Cloudflare 52x, and its message is the
    request URL, `apikey=<key>` included."""
    return redact_secrets(f"{type(exc).__name__}: {exc}")


def _retry_delay(exc: BaseException, default: float) -> float:
    """The server's Retry-After in seconds when it is a usable number, else `default`."""
    raw = getattr(exc, "retry_after", None)
    try:
        secs = float(raw)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(secs) or secs <= 0:
        return default
    return min(secs, _MAX_RETRY_AFTER_SECONDS)


async def _request(
    fmp: Any, endpoint: str, params: Optional[Dict[str, Any]], *, sleep: Sleep, context: str,
) -> Any:
    """One FMP call, a 429 retried on `_RATE_LIMIT_BACKOFF_SECONDS`; anything else raises."""
    backoff = _RATE_LIMIT_BACKOFF_SECONDS
    for attempt in range(len(backoff) + 1):
        try:
            # A fresh dict per attempt: the client adds `apikey` to the one it is given.
            return await fmp._make_request(
                endpoint, params=dict(params) if params is not None else None,
            )
        except FMPRateLimitException as exc:
            if attempt >= len(backoff):
                raise
            delay = _retry_delay(exc, backoff[attempt])
            logger.warning(
                "benchmark universe: %s rate-limited (429) — retry %d/%d in %.0fs",
                context, attempt + 1, len(backoff), delay,
            )
            await sleep(delay)
    raise AssertionError("unreachable")  # pragma: no cover — the loop returns or raises


async def _list_industries(fmp: Any, *, sleep: Sleep = asyncio.sleep) -> List[str]:
    rows = await _request(fmp, "available-industries", None, sleep=sleep,
                          context="available-industries")
    if not isinstance(rows, list):
        raise UnusableAnswerError(
            f"available-industries answered {type(rows).__name__}, expected a list"
        )
    out: List[str] = []
    for row in rows:
        name = (row.get("industry") if isinstance(row, dict) else "") or ""
        name = name.strip() if isinstance(name, str) else ""
        if name:
            out.append(name)
    if not out:
        # FMP classifies every ticker into ~159 industries; none at all is FMP failing.
        raise UnusableAnswerError(
            f"available-industries returned no industry names ({len(rows)} rows)"
        )
    return sorted(set(out))


def _row_symbol(row: Any) -> Optional[str]:
    if not isinstance(row, dict):
        return None
    sym = row.get("symbol")
    if not isinstance(sym, str) or not sym.strip():
        return None
    return sym.strip().upper()


async def _screener_for_industry(
    fmp: Any,
    industry: str,
    *,
    page_limit: int = _SCREENER_PAGE_LIMIT,
    sleep: Sleep = asyncio.sleep,
) -> List[Any]:
    """Every row the screener holds for one industry, AT EVERY CAP, in ONE call.

    No `marketCapMoreThan`: FMP applies it to a server-side cap of its own and hides every
    row where that cap is null (VMRK, VYLR, SKYD — 2026-10-08), so the floor is applied to
    the rows' own `marketCap` in `_drop_reason`.

    Never paged (2026-10-09 review): a row that LEAVES the listing between two page calls
    moves the boundary up, so the next page starts one row later and the row that was first
    on it is on neither page — nothing repeats, so no check on the two pages can see it. An
    industry that fills the page therefore cannot be read completely and is refused.

    Raises (never returns a partial list): `TruncatedIndustryError` when the answer is a
    full `page_limit`-row page; `UnusableAnswerError` for a non-list answer; and whatever
    the request raised.
    """
    params: Dict[str, Any] = {
        "industry": industry,
        "exchange": ",".join(_US_EXCHANGES),
        "isEtf": "false",
        "isFund": "false",
        "isActivelyTrading": "true",
        "limit": str(page_limit),
    }
    rows = await _request(fmp, "company-screener", params, sleep=sleep,
                          context=f"industry={industry!r}")
    if not isinstance(rows, list):
        raise UnusableAnswerError(
            f"industry={industry!r}: company-screener answered {type(rows).__name__}, "
            f"expected a list"
        )
    if len(rows) >= page_limit:
        raise TruncatedIndustryError(
            f"industry={industry!r}: the screener answered a full {page_limit}-row page "
            f"({len(rows)} rows) — the industry may hold more, and a second page can lose a "
            f"row between calls; raise _SCREENER_PAGE_LIMIT (the screener serves up to "
            f"10,000 rows a call) — nothing written"
        )
    return rows


def _flag_true(value: Any) -> bool:
    return value is True or (isinstance(value, str) and value.strip().lower() == "true")


def _flag_false(value: Any) -> bool:
    return value is False or (isinstance(value, str) and value.strip().lower() == "false")


def _row_exchange(row: Dict[str, Any]) -> str:
    for key in ("exchangeShortName", "exchange"):
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip().upper()
    return ""


def _is_us_exchange(exchange: str) -> bool:
    if not exchange:
        # No exchange on the row: the server-side `exchange` filter is the gate, and the
        # symbol checks still catch a foreign listing.
        return True
    return any(exchange == p or exchange.startswith(p + " ") for p in _US_EXCHANGE_PREFIXES)


def _positive_finite(value: Any) -> Optional[float]:
    """A real, finite, positive number, else None (a bool, a string, NaN and inf are not)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    out = float(value)
    if not math.isfinite(out) or out <= 0:
        return None
    return out


def _market_cap(row: Dict[str, Any]) -> Optional[float]:
    return _positive_finite(row.get("marketCap"))


def _row_name(row: Dict[str, Any]) -> str:
    """The row's `companyName`, length-capped BEFORE any regex sees it and folded onto one
    line, or "" when FMP sent none (or not a string)."""
    name = row.get("companyName")
    if not isinstance(name, str):
        return ""
    return " ".join(name[:_MAX_NAME_CHARS].split())


def _as_search_row(
    row: Dict[str, Any], sym: str, name: Optional[str] = None,
) -> StockSearchResult:
    """The row in the shape the ticker-search grammar reads (`name` overrides the row's own
    name). Every screener row here is a non-ETF, non-fund listing by FMP's flags, i.e. the
    search's type "stock"."""
    return StockSearchResult(symbol=sym, name=_row_name(row) if name is None else name,
                             exchange_short_name=_row_exchange(row) or None, type="stock")


def _without_adr_phrase(name: str) -> Tuple[str, bool]:
    """(the name with every ADR / GDS phrase cut out, whether one was there)."""
    cut, n = _ADR_PHRASE_RE.subn(" ", name)
    return (" ".join(cut.split()), True) if n else (name, False)


def _without_preferred_class(name: str) -> Tuple[str, bool]:
    """(an ADR's name with its "representing <n> [Class X] preferred share(s)" description
    cut out, whether it was cut). Called only on a name that carried an ADR / GDS phrase.
    Cut only when that description ENDS the name (`_ADR_PREFERRED_CLASS_RE`), and never when
    a "series" word or a fixed-income word (`_PREFERRED_FIXED_INCOME_WORD_RE`: cumulative,
    perpetual, redeemable, callable, convertible, liquidation, floating, fixed, funding, …)
    is anywhere in the name: those mark a fixed-income preferred, never an issuer's share
    class, and the uncut name then drops on its "preferred"."""
    if _SERIES_WORD_RE.search(name) or _PREFERRED_FIXED_INCOME_WORD_RE.search(name):
        return name, False
    cut, n = _ADR_PREFERRED_CLASS_RE.subn(" ", name)
    return (" ".join(cut.split()), True) if n else (name, False)


def _is_listed_lending_fund(row: Dict[str, Any], sym: str, name: str) -> bool:
    """Is a fund-named row a listed BDC named "… Lending Fund" (MSDL, BXSL)? Every gate is
    needed (owner call 2026-10-08):

      * the whole-word phrase "Lending Fund" — never a bare "Fund" ("Cohen & Steers
        Infrastructure Fund"), "Lending Funds" or "Lending Risk Premium Fund";
      * no OTHER fund / ETF / ETN word once that phrase is cut out ("… Lending Fund ETF");
      * FMP's `isFund` is explicitly false — a missing or unreadable flag proves nothing;
      * not NASDAQ's fifth-letter-X mutual-fund symbol: "Cliffwater Corporate Lending Fund"
        (CCLFX) is an interval fund.
    """
    if not _LENDING_FUND_RE.search(name):
        return False
    rest = _LENDING_FUND_RE.sub(" ", name)
    if _FUND_NAME_RE.search(rest) or _ETF_NAME_RE.search(rest):
        return False
    return _flag_false(row.get("isFund")) and not _fund_like_symbol(sym)


def _listing_reason(row: Dict[str, Any], sym: str) -> Optional[str]:
    """`fund_name` or `not_common_share` from the row's OWN symbol and name, or None.

    The rules that need the other rows (a twin of a base listing, a second share class of
    one issuer) run in `_filter_market`.
    """
    name = _row_name(row)
    if ((_FUND_NAME_RE.search(name) or _ETF_NAME_RE.search(name))
            and not _is_listed_lending_fund(row, sym, name)):
        return "fund_name"
    # ABC-U / ABC-R / ABC-W: unit, right, warrant. Class letters (BRK-B, MKC-V) are not
    # action suffixes, and the two-letter forms already failed the symbol shape.
    if _DASH_ACTION_SUFFIX_RE.search(sym):
        return "not_common_share"
    # The debt / preferred rules read the name WITHOUT its ADR / GDS phrase (an ADR is the
    # foreign issuer's common); what is left must not name a preferred either. A "Series B"
    # there is a Latin American issuer's ordinary class, not a preferred (B4-1). Nor is a
    # depositary's "each representing one Preferred Share" ENDING the name: that is a foreign
    # issuer's preferred CLASS, which is its equity (owner call 2026-10-08) — unless the name
    # carries a series or fixed-income word (`_without_preferred_class`).
    debt_name, is_adr = _without_adr_phrase(name)
    if is_adr:
        debt_name, _ = _without_preferred_class(debt_name)
    if is_adr and _ADR_NON_COMMON_RE.search(debt_name):
        return "not_common_share"
    # Dash and NASDAQ 5th-letter (P/O/N/M) preferreds, and the gated debt-name rule.
    verdict = _grammar_drop_reason(_as_search_row(row, sym, debt_name), sym)
    if verdict not in (None, *_SEARCH_GRAMMAR_KEPT):
        return "not_common_share"
    # The same debt / preferred NAME rule WITHOUT the search's issuer gate. The search needs
    # the gate so stock-typed ETF products ("Corgi U.S. Equities 30% Structu") stay
    # findable; this universe wants none of those, and the gate let a trust's notes through —
    # "PennyMac Mortgage Investment Trust 8.50% Senior Notes due 2028" (PMTU) names no
    # Inc/Corp. Plus this builder's own debt words.
    if _DEBT_PREF_NAME_RE.search(debt_name) or _BUILDER_DEBT_NAME_RE.search(debt_name):
        return "not_common_share"
    if _NASDAQ_ACTION_SYMBOL_RE.match(sym) and _ACTION_NAME_RE.search(name):
        return "not_common_share"
    return None


def _hand_checked(row: Dict[str, Any], sym: str) -> bool:
    """A row of a `_HAND_CHECKED_ISSUERS` issuer that is not its operating common."""
    entry = _HAND_CHECKED_ISSUERS.get(_issuer_key(_row_name(row)))
    return entry is not None and sym != entry[0]


def _drop_reason(row: Any, floor: int) -> Optional[str]:
    """Why a screener row is not a US-listed operating company above the floor, or None.

    Judged on the row ALONE; `_filter_rows` adds the twin and one-vote-per-issuer rules.
    """
    sym = _row_symbol(row)
    if sym is None:
        return "malformed"
    if _flag_true(row.get("isEtf")):
        return "etf"
    if _flag_true(row.get("isFund")):
        return "fund"
    if _flag_false(row.get("isActivelyTrading")):
        return "inactive"
    if "." in sym:
        return "foreign_suffix"
    if not _is_us_exchange(_row_exchange(row)):
        return "non_us_exchange"
    if not _COMMON_SHARE_SYMBOL_RE.match(sym):
        return "not_common_share"
    listing = _listing_reason(row, sym)
    if listing is not None:
        return listing
    if _hand_checked(row, sym):
        return "hand_checked"
    cap = _market_cap(row)
    if cap is None:
        return "bad_market_cap"
    if cap < floor:
        return "below_floor"
    return None


def _drop_label(row: Any, reason: str) -> str:
    label = _row_symbol(row) or repr(row)[:40]
    if reason == "non_us_exchange":
        return f"{label} [{_row_exchange(row)}]"
    if reason in ("not_common_share", "fund_name", "hand_checked") and isinstance(row, dict):
        name = _row_name(row)
        if name:
            return f'{label} "{name[:60]}"'
    return label


def _listing_twins(
    kept: Dict[str, Dict[str, Any]], bases: Dict[str, Dict[str, Any]],
) -> Dict[str, str]:
    """{symbol: its base} for each kept row that is another listing of a live common — the
    search's two twin rules, run with `kept` + `bases` as the directory (the whole market in
    `_filter_market`, so a note filed under another industry than its issuer is still
    paired):

      * corporate-action twins: a …W/U/R/V or dash-action symbol whose base (the symbol minus
        the suffix) carries the same name — CCXIW / CCXIU beside CCXI, NOVTU beside NOVT;
      * root twins: the issuer's ticker plus letters under the issuer's own name — SOMN and
        SOJE beside SO, PMTU beside PMT, HONIV beside HON. A single class letter A/B/C/J/K
        (FOXA, LILAK) and GOOGL are exempt, exactly as in the search.

    A base is any live US common listing the screener returned, whatever its cap
    (`_TWIN_BASE_REASONS`). Distinct companies stay apart because both rules also need the
    two names to be one issuer's.
    """
    page = {sym: _as_search_row(r, sym) for sym, r in {**bases, **kept}.items()}
    directory = {sym: sr.name for sym, sr in page.items()}
    twins: Dict[str, str] = {}
    survivors = {sr.symbol for sr in _dedupe_secondary_listings(list(page.values()))}
    for sym in kept:
        if sym not in survivors:
            twins[sym] = _secondary_base_symbol(sym) or "?"
    for sym in kept:
        if sym in twins:
            continue
        sr = page[sym]
        if not _is_root_twin_candidate(sr, sym, ""):
            continue
        key = _issuer_key(sr.name)
        if key and _is_root_twin(sr, sym, key, page, directory):
            twins[sym] = next(
                (sym[:i] for i in range(1, len(sym))
                 if sym[:i] in directory and _issuer_key(directory[sym[:i]]) == key),
                "?",
            )
    return twins


def _share_class_siblings(a: str, b: str) -> bool:
    """Do two symbols read as classes of ONE listing? BRK-A/BRK-B and PBR/PBR-A (one dash
    root), GOOG/GOOGL, FOX/FOXA, Z/ZG and BBD/BBDO (one letter added — Bradesco's preferred
    and common ADRs, both "Banco Bradesco S.A."), BATRA/BATRK and FWONA/FWONK (only the last
    letter differs).

    The issuer name alone is not proof: First Bancorp (FBNC) and First BanCorp. (FBP) are
    two banks, both in Banks - Regional, with one normalised name; and the symbols alone are
    not proof either — BBDC (Barings BDC) reads as a class of BBD, and only the issuer-name
    grouping in `_one_vote_per_issuer` keeps it apart.
    """
    ra, rb = a.split("-", 1)[0], b.split("-", 1)[0]
    if ra == rb:
        return True
    short, long_ = sorted((ra, rb), key=len)
    if len(long_) - len(short) == 1 and long_.startswith(short):
        return True
    return len(ra) == len(rb) >= 3 and ra[:-1] == rb[:-1]


def _liquidity(row: Dict[str, Any]) -> float:
    """Dollar volume (price × average, else last, volume); 0.0 when FMP sent no usable pair."""
    price = _positive_finite(row.get("price"))
    volume = _positive_finite(row.get("avgVolume")) or _positive_finite(row.get("volume"))
    if price is None or volume is None:
        return 0.0
    out = price * volume
    return out if math.isfinite(out) else 0.0


def _vote_order(row: Dict[str, Any]) -> Tuple[float, float, int, str]:
    """Sort key, best first: most liquid, then largest cap, then the shorter (then the
    alphabetically first) symbol — deterministic whatever order FMP listed the classes in.
    A thinly traded listing that slipped past every rule loses to the issuer's common even
    when FMP reports it a bigger cap (SOJC carried $20.7B)."""
    sym = row["symbol"]
    return (-_liquidity(row), -(_market_cap(row) or 0.0), len(sym), sym)


def _one_vote_per_issuer(
    rows: List[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], List[Tuple[Dict[str, Any], Dict[str, Any]]]]:
    """(one row per issuer, [(dropped row, the row kept in its place)]).

    Each class of a dual-class issuer comes back with the issuer's fundamentals, so GOOG +
    GOOGL or BRK-A + BRK-B voted twice in the industry median — and four such issuers in a
    32-name industry (Entertainment: FOX/FOXA, NWS/NWSA, BATRA/BATRK, FWONA/FWONK) moved it.
    Grouped by the search's normalised issuer name AND symbols that read as share classes
    (`_share_class_siblings`); a name too short to prove one issuer ("3M Company") is never
    grouped. A foreign issuer's preferred-class and common ADRs are classes like any other
    (owner call 2026-10-08): BBD + BBDO and PBR + PBR-A each vote once — WHEN `rows` holds
    both, i.e. when FMP files both classes under one industry. `_filter_market` calls this
    per industry, so classes filed under different industries are never merged here; they
    are named by `_cross_industry_share_classes`. Tracking stocks under one issuer name with
    unrelated symbols (FWONK / LSXMK) are NOT grouped — the symbols do not read as classes
    of one listing.
    """
    out: List[Dict[str, Any]] = []
    by_issuer: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows:
        key = _issuer_key(_row_name(row))
        if key:
            by_issuer.setdefault(key, []).append(row)
        else:
            out.append(row)
    duplicates: List[Tuple[Dict[str, Any], Dict[str, Any]]] = []
    for members in by_issuer.values():
        groups: List[List[Dict[str, Any]]] = []
        for row in members:
            joined = [g for g in groups
                      if any(_share_class_siblings(row["symbol"], m["symbol"]) for m in g)]
            merged = [row] + [m for g in joined for m in g]
            groups = [g for g in groups if not any(g is j for j in joined)] + [merged]
        for group in groups:
            ranked = sorted(group, key=_vote_order)
            out.append(ranked[0])
            duplicates.extend((m, ranked[0]) for m in ranked[1:])
    return out, duplicates


class _MarketFilter(NamedTuple):
    kept: Dict[str, List[Dict[str, Any]]]       # industry → one row per symbol and issuer
    dropped: Counter                             # reason → count, all industries
    examples: Dict[str, List[str]]               # reason → a few labels
    drops_by_industry: Dict[str, int]
    cross_industry_twins: List[str]              # twins of a common in ANOTHER industry


def _filter_market(rows_by_industry: Dict[str, List[Any]], floor: int) -> _MarketFilter:
    """Filter every industry's screener answer against ONE market-wide directory.

    Per row first (`_drop_reason`); then the twin rules over the common rows of EVERY
    industry (FMP files CGABL under Financial - Credit Services and CG under Asset
    Management, HONAV under Aerospace & Defense and HON under Conglomerates — an
    industry-local directory never saw the base); then one vote per issuer inside each
    industry ONLY — one issuer's classes kept in two industries both stay (`main()` names
    them via `_cross_industry_share_classes`). Industries are walked in sorted order, so the
    result does not depend on the order the requests completed in.
    """
    kept_by: Dict[str, Dict[str, Dict[str, Any]]] = {}
    market_kept: Dict[str, Dict[str, Any]] = {}
    market_bases: Dict[str, Dict[str, Any]] = {}
    home: Dict[str, str] = {}              # symbol → the industry its directory row is from
    dropped: Counter = Counter()
    examples: Dict[str, List[str]] = {}
    drops_by_industry: Counter = Counter()
    cross: List[str] = []

    def _note(industry: str, reason: str, label: str) -> None:
        dropped[reason] += 1
        drops_by_industry[industry] += 1
        sample = examples.setdefault(reason, [])
        if len(sample) < _DROP_EXAMPLES:
            sample.append(label)

    for industry in sorted(rows_by_industry):
        kept = kept_by.setdefault(industry, {})
        for row in rows_by_industry[industry]:
            reason = _drop_reason(row, floor)
            if reason is None:
                sym = _row_symbol(row)
                if sym not in kept:          # a row the screener served twice: one vote
                    kept[sym] = {**row, "symbol": sym}
                    if sym not in market_kept:
                        market_kept[sym] = kept[sym]
                        home[sym] = industry
                continue
            if reason in _TWIN_BASE_REASONS:
                sym = _row_symbol(row)
                if sym not in market_bases:
                    market_bases[sym] = {**row, "symbol": sym}
                    home.setdefault(sym, industry)
            _note(industry, reason, _drop_label(row, reason))

    for sym, base in sorted(_listing_twins(market_kept, market_bases).items()):
        base_industry = home.get(base)
        for industry in sorted(kept_by):
            row = kept_by[industry].pop(sym, None)
            if row is None:
                continue
            if base_industry is not None and base_industry != industry:
                label = f"{sym} (twin of {base} in {base_industry})"
                cross.append(f'{sym} "{_row_name(row)[:60]}" [{industry}] → '
                             f'{base} [{base_industry}]')
            else:
                label = f"{sym} (twin of {base})"
            _note(industry, "not_common_share", label)

    out: Dict[str, List[Dict[str, Any]]] = {}
    for industry in sorted(kept_by):
        survivors, duplicates = _one_vote_per_issuer(list(kept_by[industry].values()))
        for row, winner in duplicates:
            _note(industry, "same_issuer", f"{row['symbol']} (kept {winner['symbol']})")
        out[industry] = survivors
    return _MarketFilter(out, dropped, examples, dict(drops_by_industry), cross)


def _filter_rows(
    rows: List[Any], floor: int,
) -> Tuple[List[Dict[str, Any]], Counter, Dict[str, List[str]]]:
    """(kept rows — one per symbol AND one per issuer; drop counts by reason; a few examples
    per reason) for ONE industry treated as the whole market — `_filter_market` with a
    single industry."""
    result = _filter_market({"": rows}, floor)
    return result.kept[""], result.dropped, result.examples


def _cross_industry_share_classes(by_industry: Dict[str, List[Dict[str, Any]]]) -> List[str]:
    """Every pair of KEPT rows that are one issuer's share classes (one `_issuer_key`, symbols
    that `_share_class_siblings` reads as classes of one listing) filed under DIFFERENT
    industries — one entry per pair, sorted, BOTH rows still kept (B2, 2026-10-08).

    `_one_vote_per_issuer` runs inside each industry, so it cannot merge such a pair: the
    issuer votes in each industry's median, and twice in the sector median when the two
    industries pool into one sector (`industry_benchmark_service._pool_into_sector`). The
    June 2026 file had exactly this: BBDO under "Banks", BBD under "Banks - Regional" (the
    2026-10-08 probe files both under Banks - Regional). Which class belongs to which
    industry is FMP's call, not a rule this builder can make, so the pairs are only NAMED
    (`_log_cross_industry_share_classes`) for the owner to read before uploading. A name too
    short to prove one issuer ("3M Company") has no key and is never paired; distinct
    companies with one key keep apart on their symbols (First Bancorp FBNC / First BanCorp.
    FBP).
    """
    by_issuer: Dict[str, List[Tuple[str, Dict[str, Any]]]] = {}
    for industry in sorted(by_industry):
        for row in sorted(by_industry[industry], key=lambda r: r["symbol"]):
            key = _issuer_key(_row_name(row))
            if key:
                by_issuer.setdefault(key, []).append((industry, row))
    entries: List[str] = []
    for key in sorted(by_issuer):
        members = by_issuer[key]
        for i, (industry_a, a) in enumerate(members):
            for industry_b, b in members[i + 1:]:
                if industry_a != industry_b and _share_class_siblings(a["symbol"],
                                                                       b["symbol"]):
                    entries.append(f'{a["symbol"]} [{industry_a}] + {b["symbol"]} '
                                   f'[{industry_b}] "{_row_name(a)[:60]}"')
    return entries


# ── one vote per set of statements: the statement-twin pass (2026-10-08) ──────────────────
#
# FMP serves a company's statements under listings that are not its common — a note, a
# preferred, an operating-partnership unit, a re-pointed SPAC symbol, a second share class
# — and no name or symbol rule sees all of them. The five TTM ratios below do not depend on
# price or share count, so a second listing of one set of statements matches the first
# EXACTLY (bit for bit: the 2026-10-08 scan of 2,971 rows found the same 11 pairs at 4, 6 or
# 8 decimal places and with exact equality — APXT/AVPT, BN/BNH, FWONA/FWONK, MSTR/STRC… —
# and no false pair; 3 dp first adds one, CSIQ/TBBB). CIK is not the key: FMP's statement
# CIK is wrong for tracking stocks, and DLC / OP-unit twins file under other CIKs.
_TWIN_RATIO_FIELDS: Tuple[str, ...] = (
    "grossProfitMarginTTM", "operatingProfitMarginTTM", "netProfitMarginTTM",
    "currentRatioTTM", "debtToEquityRatioTTM",
)
# Two rows are twins only when at least this many of the five equal slots are informative
# (a finite number other than 0): two shells with zero margins are not one company.
_TWIN_MIN_INFORMATIVE = 3
# One `ratios-ttm` call per kept row (~3,000 here, ~5,500 at floor 0), on the production key
# the live app also uses: at most this many in flight, starting at most this many a second
# (300/min — well inside the plan, with room for the app). ~10 min for ~3,000 rows.
_TWIN_CONCURRENCY = 4
_TWIN_CALLS_PER_SECOND = 5.0
# A failed fingerprint call costs at most one extra vote, so it keeps its row (named at
# WARNING). More than this share of the calls failing is an outage, not a few bad symbols:
# the build fails (exit 1) — and stops calling as soon as the share is exceeded.
_TWIN_MAX_FAILURE_PERCENT = 1

Fingerprint = Tuple[Optional[float], ...]
Clock = Callable[[], float]


def _twin_slot(value: Any) -> Optional[float]:
    """A fingerprint slot: a finite real number as a float; None for anything else — a
    string, a bool, None, NaN or inf is not informative (and never coerced). An integer too
    large for a float (JSON allows a 400-digit literal) is not informative either: it must
    never raise out of the fingerprint pass, which runs outside each call's `try`."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        out = float(value)
    except (OverflowError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _statement_fingerprint(row: Any) -> Optional[Fingerprint]:
    """The five `_TWIN_RATIO_FIELDS` slots of one `ratios-ttm` row, or None when fewer than
    `_TWIN_MIN_INFORMATIVE` of them are informative non-zero numbers (such a row is never
    anyone's twin)."""
    if not isinstance(row, dict):
        return None
    slots = tuple(_twin_slot(row.get(field)) for field in _TWIN_RATIO_FIELDS)
    informative = sum(1 for v in slots if v is not None and v != 0.0)
    return slots if informative >= _TWIN_MIN_INFORMATIVE else None


class _Pacer:
    """Spaces call STARTS at least `1 / rate` seconds apart, shared by every worker. The
    first call starts at once; each later one reserves the next free slot (no await between
    the read and the write, so two workers never take one slot)."""

    def __init__(self, rate: float, *, sleep: Sleep, clock: Clock) -> None:
        if not (isinstance(rate, (int, float)) and not isinstance(rate, bool)
                and math.isfinite(rate) and rate > 0):
            raise ValueError(f"rate must be a positive finite number, got {rate!r}")
        self._interval = 1.0 / float(rate)
        self._next: Optional[float] = None
        self._sleep = sleep
        self._clock = clock

    async def wait(self) -> None:
        now = self._clock()
        if self._next is None or self._next <= now:
            self._next = now + self._interval
            return
        delay = self._next - now
        self._next += self._interval
        await self._sleep(delay)


class _FingerprintScan(NamedTuple):
    fingerprints: Dict[str, Fingerprint]   # symbol → its five slots (informative rows only)
    no_statements: List[str]               # ratios-ttm answered [] — FMP holds no statements
    uninformative: List[str]               # a row, with fewer than 3 informative ratios
    failed: Dict[str, str]                 # symbol → the scrubbed failure
    not_called: List[str]                  # left uncalled once the failure share was exceeded
    planned: int                           # the symbols the scan was asked for
    seconds: float


def _twin_failures_fatal(failed: int, planned: int) -> bool:
    """More than `_TWIN_MAX_FAILURE_PERCENT`% of the planned calls failed (an outage)."""
    return failed * 100 > planned * _TWIN_MAX_FAILURE_PERCENT


# A scan that "succeeds" while fingerprinting almost nothing is a silent no-op: FMP renaming
# the five ratio fields, or answering [] for everyone, leaves every twin voting twice behind
# an exit 0. The 2026-10-09 production build fingerprinted 2,840 of the 2,942 rows that
# answered (96.5%) and saw 25 empty answers of 2,967 (0.8%), so these bars sit far from a
# healthy run. Judged only on a scan of at least `_TWIN_SANITY_MIN_ROWS` rows (a test or a
# one-industry run has too few to judge).
_TWIN_MIN_FINGERPRINTED_SHARE = 0.5
_TWIN_MAX_EMPTY_PERCENT = 10
_TWIN_SANITY_MIN_ROWS = 50


def _twin_scan_problem(scan: "_FingerprintScan") -> Optional[str]:
    """Why a scan with no failed calls still proves nothing, or None when it is sane."""
    if scan.planned < _TWIN_SANITY_MIN_ROWS:
        return None
    if len(scan.no_statements) * 100 > scan.planned * _TWIN_MAX_EMPTY_PERCENT:
        return (f"{len(scan.no_statements)} of {scan.planned} ratios-ttm answers were empty "
                f"(more than {_TWIN_MAX_EMPTY_PERCENT}%) — the endpoint is answering [] for "
                f"rows that have statements")
    answered = len(scan.fingerprints) + len(scan.uninformative)
    if answered and len(scan.fingerprints) < answered * _TWIN_MIN_FINGERPRINTED_SHARE:
        return (f"only {len(scan.fingerprints)} of {answered} non-empty ratios-ttm answers "
                f"carried {_TWIN_MIN_INFORMATIVE} informative ratios (under "
                f"{_TWIN_MIN_FINGERPRINTED_SHARE:.0%}) — FMP renamed or emptied the fields "
                f"{', '.join(_TWIN_RATIO_FIELDS)}?")
    return None


async def _fetch_statement_fingerprints(
    fmp: Any, symbols: Iterable[str], *, sleep: Sleep = asyncio.sleep,
    clock: Clock = time.monotonic,
) -> _FingerprintScan:
    """One paced `ratios-ttm` call per symbol (a 429 retried by `_request`; the client
    retries 5xx itself). Never raises for an FMP answer: a failure is recorded, its row
    kept, and named at WARNING here; once more than `_TWIN_MAX_FAILURE_PERCENT`% have failed
    the rest are not called (the caller fails the build)."""
    ordered = sorted({s for s in symbols if isinstance(s, str) and s})
    pacer = _Pacer(_TWIN_CALLS_PER_SECOND, sleep=sleep, clock=clock)
    sem = asyncio.Semaphore(_TWIN_CONCURRENCY)
    fingerprints: Dict[str, Fingerprint] = {}
    no_statements: List[str] = []
    uninformative: List[str] = []
    failed: Dict[str, str] = {}
    not_called: List[str] = []
    started = clock()

    def _fail(sym: str, reason: str, exc: Optional[BaseException] = None) -> None:
        failed[sym] = reason
        logger.warning(
            "benchmark universe: ratios-ttm for %s FAILED (%s) — no fingerprint, the row is "
            "kept (it may vote a second time if it is another listing's twin)", sym, reason,
            exc_info=exc is not None and not isinstance(exc, _EXPECTED_FAILURES),
        )

    async def _one(sym: str) -> None:
        async with sem:
            if _twin_failures_fatal(len(failed), len(ordered)):
                not_called.append(sym)
                return
            await pacer.wait()
            try:
                answer = await _request(fmp, "ratios-ttm", {"symbol": sym}, sleep=sleep,
                                        context=f"ratios-ttm symbol={sym}")
            except Exception as exc:
                _fail(sym, _describe(exc), exc)
                return
        if not isinstance(answer, list):
            _fail(sym, f"UnusableAnswerError: ratios-ttm answered {type(answer).__name__}, "
                       f"expected a list")
            return
        if not answer:
            no_statements.append(sym)
            return
        if not isinstance(answer[0], dict):
            _fail(sym, f"UnusableAnswerError: ratios-ttm row is {type(answer[0]).__name__}, "
                       f"expected an object")
            return
        fp = _statement_fingerprint(answer[0])
        if fp is None:
            uninformative.append(sym)
        else:
            fingerprints[sym] = fp

    await asyncio.gather(*[_one(s) for s in ordered])
    return _FingerprintScan(fingerprints, sorted(no_statements), sorted(uninformative),
                            dict(sorted(failed.items())), sorted(not_called), len(ordered),
                            max(0.0, clock() - started))


def _log_fingerprint_scan(scan: _FingerprintScan) -> None:
    """The runtime line (INFO), the rows FMP holds no statements for (ONE WARNING, every
    symbol named), and the count of rows with too few ratios to fingerprint (INFO)."""
    calls = scan.planned - len(scan.not_called)
    logger.info(
        "benchmark universe: statement-twin scan — %d ratios-ttm call(s) in %.1fs (%.1f/s; "
        "%d fingerprinted, %d without statements, %d with fewer than %d informative ratios, "
        "%d failed)", calls, scan.seconds, calls / scan.seconds if scan.seconds > 0 else 0.0,
        len(scan.fingerprints), len(scan.no_statements), len(scan.uninformative),
        _TWIN_MIN_INFORMATIVE, len(scan.failed),
    )
    if scan.no_statements:
        logger.warning(
            "benchmark universe: %d kept row(s) have NO statements at FMP (ratios-ttm "
            "answered []) — they vote in no median but count in the file; check before "
            "uploading (a security of a fund or issuer that no rule caught?): %s",
            len(scan.no_statements), ", ".join(scan.no_statements),
        )
    if scan.uninformative:
        logger.info(
            "benchmark universe: %d kept row(s) report fewer than %d informative TTM ratios "
            "(pre-revenue or zero margins) — checked by the name rules only: %s",
            len(scan.uninformative), _TWIN_MIN_INFORMATIVE, _named(scan.uninformative),
        )


class _TwinOutcome(NamedTuple):
    filtered: _MarketFilter      # the market with same-industry twins removed
    drops: List[str]             # one entry per same-industry drop
    cross_groups: List[str]      # one entry per group kept in more than one industry


def _one_vote_per_statement_set(
    filtered: _MarketFilter, fingerprints: Dict[str, Fingerprint],
) -> _TwinOutcome:
    """Rows with one fingerprint report one set of statements. Inside an industry only the
    most liquid votes (`_vote_order`); the rest are dropped as `same_statements`. Across
    industries each industry's most liquid member is KEPT and the group is named — B2
    (2026-10-08): which class belongs to which industry is FMP's call, never this
    builder's. A group's entry also names the members dropped inside their own industry, so
    the WARNING never reads as if the whole group was kept. One symbol kept in two
    industries is not a twin of itself. Deterministic whatever the input order."""
    by_fp: Dict[Fingerprint, List[Tuple[str, Dict[str, Any]]]] = {}
    for industry in sorted(filtered.kept):
        for row in filtered.kept[industry]:
            fp = fingerprints.get(row["symbol"])
            if fp is not None:
                by_fp.setdefault(fp, []).append((industry, row))

    gone: Dict[str, set] = {}
    dropped = Counter(filtered.dropped)
    examples = {k: list(v) for k, v in filtered.examples.items()}
    drops_by_industry = dict(filtered.drops_by_industry)
    drops: List[str] = []
    cross: List[str] = []
    groups = sorted(by_fp.values(), key=lambda m: sorted((r["symbol"], i) for i, r in m))
    for members in groups:
        if len({r["symbol"] for _, r in members}) < 2:
            continue
        by_industry: Dict[str, List[Dict[str, Any]]] = {}
        for industry, row in members:
            by_industry.setdefault(industry, []).append(row)
        survivors: List[Tuple[str, Dict[str, Any]]] = []
        group_losers: List[str] = []
        for industry in sorted(by_industry):
            ranked = sorted(by_industry[industry], key=_vote_order)
            winner = ranked[0]
            survivors.append((industry, winner))
            for loser in ranked[1:]:
                gone.setdefault(industry, set()).add(loser["symbol"])
                dropped["same_statements"] += 1
                drops_by_industry[industry] = drops_by_industry.get(industry, 0) + 1
                sample = examples.setdefault("same_statements", [])
                if len(sample) < _DROP_EXAMPLES:
                    sample.append(f"{loser['symbol']} (kept {winner['symbol']})")
                drops.append(f'{loser["symbol"]} "{_row_name(loser)[:60]}" [{industry}] — '
                             f'{winner["symbol"]} "{_row_name(winner)[:60]}" votes')
                group_losers.append(f'{loser["symbol"]} [{industry}]')
        if (len({i for i, _ in survivors}) > 1
                and len({r["symbol"] for _, r in survivors}) > 1):
            entry = " + ".join(f'{r["symbol"]} [{i}] "{_row_name(r)[:60]}"'
                               for i, r in survivors)
            if group_losers:
                entry += (f" (and {len(group_losers)} same-industry listing(s) of the group "
                          f"dropped: {', '.join(group_losers)})")
            cross.append(entry)

    kept = {industry: [r for r in rows if r["symbol"] not in gone.get(industry, set())]
            for industry, rows in filtered.kept.items()}
    return _TwinOutcome(
        _MarketFilter(kept, dropped, examples, drops_by_industry,
                      list(filtered.cross_industry_twins)),
        drops, cross,
    )


def _log_statement_twins(outcome: _TwinOutcome) -> None:
    """Every same-industry drop on its OWN INFO line (`_log_drops` names only five per
    reason), and every group kept in more than one industry at WARNING."""
    for entry in outcome.drops:
        logger.info(
            "benchmark universe: same_statements — dropped %s (all five TTM ratios equal: one "
            "company's statements, the most liquid listing votes)", entry,
        )
    if outcome.cross_groups:
        logger.warning(
            "benchmark universe: %d group(s) of rows filed under more than one industry "
            "report the SAME statements (all five TTM ratios equal) — each industry keeps its "
            "most liquid member, so the company votes in each of those industries' medians "
            "and twice in a shared sector's; check before uploading: %s",
            len(outcome.cross_groups), _named(outcome.cross_groups),
        )


def _aggregate(by_industry: Dict[str, List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    """Resolve (industry → screener rows) → {industry, sector(modal), tickers, market_caps}."""
    out: List[Dict[str, Any]] = []
    for industry, rows in by_industry.items():
        if not rows:
            continue
        sector_counts: Dict[str, int] = {}
        caps: Dict[str, float] = {}
        for r in rows:
            sym = _row_symbol(r)
            sec = r.get("sector")
            sec = sec.strip() if isinstance(sec, str) else ""
            cap = _market_cap(r)
            if sym and cap is not None:
                caps[sym] = cap
                if sec:
                    sector_counts[sec] = sector_counts.get(sec, 0) + 1
        if not caps:
            continue
        sector = (
            max(sector_counts.items(), key=lambda kv: kv[1])[0]
            if sector_counts else "Unknown"
        )
        out.append({
            "industry": industry,
            "sector": sector,
            "tickers": sorted(caps.keys()),
            "market_caps": caps,
        })
    out.sort(key=lambda d: (d["sector"], d["industry"]))
    return out


def _ticker_count(industries: List[Any]) -> int:
    return sum(
        len(e["tickers"]) for e in industries
        if isinstance(e, dict) and isinstance(e.get("tickers"), list)
    )


def _load_previous(path: Path) -> Optional[Dict[str, Any]]:
    """The payload of the file this build replaces — a dict whose `industries` is a list —
    or None (no baseline)."""
    if not path.exists():
        logger.warning(
            "benchmark universe: no previous file at %s — the shrink guard has no baseline. "
            "The live copy is in the 'universe-data' bucket; download it there first to "
            "compare.", path,
        )
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        industries = payload.get("industries") if isinstance(payload, dict) else None
        if not isinstance(industries, list):
            raise ValueError(f"'industries' is {type(industries).__name__}, expected list")
    except Exception as exc:
        logger.warning(
            "benchmark universe: previous file %s is unreadable (%s) — the shrink guard "
            "has no baseline", path, _describe(exc),
        )
        return None
    return payload


def _floor_change_allowed(
    previous: Dict[str, Any], floor: Any, allow_floor_change: bool = False,
) -> bool:
    """False when the previous file was built at another `market_cap_floor` and
    `--allow-floor-change` was not given. `--floor 0` aimed at the $500M benchmark file (no
    `--output`) would replace it with the floor-0 industry universe: thousands of micro caps
    in every peer median, and growth the shrink guard never refuses. A previous file
    without the key (the old `discover_industries` never wrote one), or with a value that is
    not a finite number, cannot be compared: WARNING, allowed."""
    if "market_cap_floor" not in previous:
        logger.warning(
            "benchmark universe: the previous file carries no market_cap_floor — the floor "
            "guard cannot compare it with this build's $%s; check it is the file you meant "
            "to replace", floor,
        )
        return True
    raw = previous.get("market_cap_floor")
    before = _twin_slot(raw)
    if before is None:
        logger.warning(
            "benchmark universe: the previous file's market_cap_floor is unreadable (%r) — "
            "the floor guard cannot compare it with this build's $%s", raw, floor,
        )
        return True
    if before == floor:
        return True
    if allow_floor_change:
        logger.warning(
            "benchmark universe: market-cap floor changes $%s → $%s — allowed by "
            "--allow-floor-change", raw, floor,
        )
        return True
    logger.error(
        "benchmark universe: REFUSED — the previous file was built at a $%s market-cap floor "
        "and this build is at $%s. Nothing written. Wrong --output (the benchmark file is "
        "$500M, the industry file floor 0)? If the change is meant, re-run with "
        "--allow-floor-change.", raw, floor,
    )
    return False


ShrinkOverride = Union[None, bool, int, float]


def _shrink_limit(allow_shrink: ShrinkOverride) -> Optional[float]:
    """The largest drop `--allow-shrink` permits, in percent, or None (no override).

    `True` is the bare flag (`_DEFAULT_ALLOW_SHRINK_PERCENT`); a number must be finite and in
    (0, 100]. Anything else is a caller bug and raises ValueError — never read as "any".
    """
    if allow_shrink is None or allow_shrink is False:
        return None
    if allow_shrink is True:
        return float(_DEFAULT_ALLOW_SHRINK_PERCENT)
    if (isinstance(allow_shrink, (int, float)) and math.isfinite(allow_shrink)
            and 0 < allow_shrink <= 100):
        return float(allow_shrink)
    raise ValueError(f"allow_shrink must be a percentage in (0, 100], got {allow_shrink!r}")


def _shrink_allowed(previous: int, new: int, allow_shrink: ShrinkOverride) -> bool:
    """False when `new` drops MORE than `_MAX_SHRINK_PERCENT` below `previous`, or — with
    `--allow-shrink PCT` — more than PCT (a PCT under the default bar changes nothing)."""
    if previous <= 0 or (previous - new) * 100 <= previous * _MAX_SHRINK_PERCENT:
        return True
    drop = 100.0 * (previous - new) / previous
    limit = _shrink_limit(allow_shrink)
    if limit is not None and (previous - new) * 100 <= previous * limit:
        logger.warning(
            "benchmark universe: ticker count drops %.1f%% (%d → %d) — allowed by "
            "--allow-shrink %g", drop, previous, new, limit,
        )
        return True
    if limit is not None:
        logger.error(
            "benchmark universe: REFUSED — ticker count drops %.1f%% (%d → %d), more than "
            "the %g%% --allow-shrink permits. Nothing written. Find out why before raising "
            "it: a drop this size is also what a screener answering real industries with "
            "empty pages looks like.", drop, previous, new, limit,
        )
        return False
    logger.error(
        "benchmark universe: REFUSED — ticker count drops %.1f%% (%d → %d), more than "
        "%d%%. Nothing written. If the drop is expected (e.g. the first US-only build), "
        "re-run with --allow-shrink PCT (bare: %d%%).", drop, previous, new,
        _MAX_SHRINK_PERCENT, _DEFAULT_ALLOW_SHRINK_PERCENT,
    )
    return False


def _fund_like_symbol(sym: str) -> bool:
    """NASDAQ's fifth-letter-X mutual-fund shape (`____X`)."""
    return len(sym) == 5 and sym.endswith("X")


def _operating_ticker_count(tickers: Any) -> int:
    """Tickers that read as a US operating listing: a string, no dotted foreign suffix, not
    a 5-letter X fund symbol. The previous file carries no names or flags, so an ETF with
    an ordinary symbol still counts."""
    if not isinstance(tickers, list):
        return 0
    return sum(
        1 for t in tickers
        if isinstance(t, str) and t.strip() and "." not in t
        and not _fund_like_symbol(t.strip().upper())
    )


def _missing_industries_allowed(
    previous: List[Any], new: List[Dict[str, Any]], allow_missing: Iterable[str] = (),
) -> bool:
    """False when an industry that held at least `_MISSING_INDUSTRY_MIN_TICKERS` operating
    tickers in the previous file has none now and is not named in `allow_missing`.

    The screener answering a real industry with `[]` is not a failed request — it reads as
    an industry with no constituents — and on a run that also needs `--allow-shrink` its
    single 'no constituents now' line is lost among the industries that really left.
    """
    allowed = {a.strip().casefold() for a in allow_missing
               if isinstance(a, str) and a.strip()}
    now = {e["industry"] for e in new}
    gone: Dict[str, int] = {}
    for e in previous:
        if not isinstance(e, dict):
            continue
        industry = e.get("industry")
        if not isinstance(industry, str) or not industry.strip() or industry in now:
            continue
        gone[industry] = max(gone.get(industry, 0), _operating_ticker_count(e.get("tickers")))

    named = {i for i in gone if i.strip().casefold() in allowed}
    blocking = sorted((i, n) for i, n in gone.items()
                      if n >= _MISSING_INDUSTRY_MIN_TICKERS and i not in named)
    for industry in sorted(named):
        logger.warning(
            "benchmark universe: industry=%r (%d operating tickers before) has none now — "
            "allowed by --allow-missing", industry, gone[industry],
        )
    unused = sorted(allowed - {i.strip().casefold() for i in gone})
    if unused:
        logger.warning(
            "benchmark universe: --allow-missing named %d industr%s that did not go missing "
            "(a typo, or it still has constituents): %s",
            len(unused), "y" if len(unused) == 1 else "ies", ", ".join(unused),
        )
    if not blocking:
        return True
    logger.error(
        "benchmark universe: REFUSED — %d industr%s with at least %d operating tickers in the "
        "previous file %s none now: %s. Nothing written. An empty screener answer for a real "
        "industry looks exactly like this. If each one really left, re-run with: %s",
        len(blocking), "y" if len(blocking) == 1 else "ies", _MISSING_INDUSTRY_MIN_TICKERS,
        "has" if len(blocking) == 1 else "have",
        ", ".join(f"{i} ({n})" for i, n in blocking),
        " ".join(f'--allow-missing "{i}"' for i, _ in blocking),
    )
    return False


def _log_comparison(previous: List[Any], new: List[Dict[str, Any]]) -> None:
    def by_sector(industries: List[Any]) -> Dict[str, int]:
        out: Dict[str, int] = {}
        for e in industries:
            if isinstance(e, dict) and isinstance(e.get("tickers"), list):
                sector = str(e.get("sector") or "Unknown")
                out[sector] = out.get(sector, 0) + len(e["tickers"])
        return out

    before, after = by_sector(previous), by_sector(new)
    for sector in sorted(set(before) | set(after)):
        logger.info("  %-25s %5d → %5d tickers", sector, before.get(sector, 0),
                    after.get(sector, 0))
    gone = sorted(
        {e["industry"] for e in previous
         if isinstance(e, dict) and isinstance(e.get("industry"), str) and e["industry"]}
        - {e["industry"] for e in new}
    )
    if gone:
        logger.warning(
            "benchmark universe: %d industries in the previous file have no constituents "
            "now: %s", len(gone), ", ".join(map(str, gone)),
        )


def _log_drops(dropped: Counter, examples: Dict[str, List[str]], rows_seen: int) -> None:
    for reason, n in sorted(dropped.items()):
        unexpected = reason in _UNEXPECTED_DROP_REASONS
        note = ("a server-side screener filter was not honoured" if unexpected
                else _EXPECTED_DROP_NOTES.get(reason, "expected"))
        logger.log(
            logging.WARNING if unexpected else logging.INFO,
            "benchmark universe: dropped %d %s row(s) of %d, e.g. %s — %s",
            n, reason, rows_seen, ", ".join(examples.get(reason, [])), note,
        )


def _log_fund_like_symbols(industries: List[Dict[str, Any]]) -> None:
    """A 5-letter symbol ending in X is a mutual fund by NASDAQ's fifth-letter convention.

    Kept (FMP's `isFund` is the authority), but named, so a fund FMP failed to flag is
    seen before the file is uploaded.
    """
    suspects = sorted({t for e in industries for t in e["tickers"] if _fund_like_symbol(t)})
    if suspects:
        logger.warning(
            "benchmark universe: %d kept symbol(s) follow NASDAQ's fifth-letter-X mutual-fund "
            "convention but FMP did not flag them isFund — check before uploading: %s",
            len(suspects), ", ".join(suspects[:20]),
        )


def _named(entries: List[str]) -> str:
    """The first `_SUSPECTS_NAMED` entries, and how many more there are."""
    more = len(entries) - _SUSPECTS_NAMED
    return "; ".join(entries[:_SUSPECTS_NAMED]) + (f"; (+{more} more)" if more > 0 else "")


def _daily_turnover(row: Dict[str, Any]) -> Optional[float]:
    """price × avgVolume ÷ marketCap — the share of the reported cap traded a day — or None
    when any of the three is missing, non-numeric, non-finite or not positive."""
    price = _positive_finite(row.get("price"))
    avg_volume = _positive_finite(row.get("avgVolume"))
    cap = _market_cap(row)
    if price is None or avg_volume is None or cap is None:
        return None
    out = price * avg_volume / cap
    return out if math.isfinite(out) else None


def _log_cross_industry_twins(entries: List[str]) -> None:
    """Rows dropped as the twin of a common in ANOTHER industry, every one named: the
    market-wide directory is what lets a distinct company be mistaken for a note, so the
    owner checks the list before uploading."""
    if entries:
        logger.warning(
            "benchmark universe: %d row(s) dropped as twins of a common listed in ANOTHER "
            "industry (the issuer's ticker plus letters, under the issuer's own name) — "
            "check before uploading: %s", len(entries), _named(entries),
        )


def _log_cross_industry_share_classes(entries: List[str]) -> None:
    """Share classes of one issuer kept in DIFFERENT industries, every pair named at WARNING
    (from `_cross_industry_share_classes`): one vote per issuer holds only inside an
    industry, so each of these issuers votes once per industry."""
    if entries:
        logger.warning(
            "benchmark universe: %d pair(s) of one issuer's share classes are filed under "
            "DIFFERENT industries — both kept, so the issuer votes in each industry's median "
            "and twice in the sector median when the two industries share a sector; check "
            "before uploading: %s", len(entries), _named(entries),
        )


def _log_suspect_rows(by_industry: Dict[str, List[Dict[str, Any]]]) -> None:
    """Kept rows no rule can settle on its own — named at WARNING, never dropped, so the
    owner reads them before uploading:

      * a NASDAQ fifth-letter W/U/R/V symbol (warrant / unit / right / when-issued) with no
        same-issuer base row and a name that does not say what it is;
      * a trust whose name reads like a closed-end fund ("BlackRock Taxable Municipal Bond
        Trust"). "Trust" alone also names operating REITs and royalty trusts, and this list
        will name some of them too (Universal Health Realty Income Trust) — which is why it
        is a list to read, not a drop;
      * a row trading under `_THIN_DAILY_TURNOVER` of its reported cap a day — a note FMP
        prices with the issuer's share count whose (often truncated) name says nothing, or
        a thinly traded ADR. Most suspicious first.
    """
    actions: List[str] = []
    trusts: List[str] = []
    thin: List[Tuple[float, str]] = []
    for industry, rows in sorted(by_industry.items()):
        for row in rows:
            sym, name = row["symbol"], _row_name(row)
            entry = f'{sym} "{name[:60]}" [{industry}]'
            if _NASDAQ_ACTION_SYMBOL_RE.match(sym):
                actions.append(entry)
            if _TRUST_RE.search(name) and _FUND_STYLE_WORD_RE.search(name):
                trusts.append(entry)
            turnover = _daily_turnover(row)
            if turnover is not None and turnover < _THIN_DAILY_TURNOVER:
                thin.append((turnover, f"{entry} {turnover * 100:.4f}%/day"))

    if thin:
        thin.sort()
        logger.warning(
            "benchmark universe: %d kept row(s) trade under %.2f%% of their reported market "
            "cap a day (price × avgVolume) — the mark of a note or preferred FMP prices with "
            "the issuer's share count, or of a thinly traded ADR — kept; check before "
            "uploading: %s", len(thin), _THIN_DAILY_TURNOVER * 100, _named([e for _, e in thin]),
        )
    if actions:
        logger.warning(
            "benchmark universe: %d kept symbol(s) carry NASDAQ's fifth-letter warrant / unit "
            "/ right / when-issued code (W/U/R/V) with no same-issuer base row and a plain "
            "name — kept; check before uploading: %s", len(actions), _named(actions),
        )
    if trusts:
        logger.warning(
            "benchmark universe: %d kept row(s) are trusts whose name reads like a closed-end "
            "fund (FMP flags only open-end funds isFund) — kept; check before uploading: %s",
            len(trusts), _named(trusts),
        )


def _log_owner_call_keeps(by_industry: Dict[str, List[Dict[str, Any]]]) -> None:
    """Name, at INFO, every kept row that votes only because of an owner call (2026-10-08),
    so each build shows what the two exemptions let in:

      * a listed BDC whose name says "Lending Fund" (MSDL, BXSL) — the fund-name rule
        would have dropped it;
      * an ADR whose name describes a foreign issuer's PREFERRED share class ("each
        representing one Preferred Share") — the ADR preferred markers would have dropped
        it. FMP's names for ITUB / BBD / PBR-A carry no such description today, so this
        line is normally absent.
    """
    lenders: List[str] = []
    preferred_class: List[str] = []
    for industry, rows in sorted(by_industry.items()):
        for row in rows:
            sym, name = row["symbol"], _row_name(row)
            entry = f'{sym} "{name[:60]}" [{industry}]'
            if ((_FUND_NAME_RE.search(name) or _ETF_NAME_RE.search(name))
                    and _is_listed_lending_fund(row, sym, name)):
                lenders.append(entry)
            cut, is_adr = _without_adr_phrase(name)
            if is_adr and _without_preferred_class(cut)[1]:
                preferred_class.append(entry)
    if lenders:
        logger.info(
            "benchmark universe: kept %d listed BDC(s) named \"Lending Fund\" (isFund=false) "
            "as operating companies — owner call 2026-10-08: %s", len(lenders), _named(lenders),
        )
    if preferred_class:
        logger.info(
            "benchmark universe: kept %d ADR(s) over a foreign issuer's preferred share class "
            "as that issuer's equity — owner call 2026-10-08: %s",
            len(preferred_class), _named(preferred_class),
        )


def _unreviewed_mixed_members(by_industry: Dict[str, List[Dict[str, Any]]]) -> List[str]:
    """Kept rows of a MIXED industry (`financials_metric_gate.is_mixed_lender_industry`:
    "Financial - Credit Services") that nobody has classified — neither a curated non-lender
    (`NON_LENDER_MEMBERS`) nor a reviewed lender (`REVIEWED_CREDIT_SERVICES_LENDERS`)."""
    out: List[str] = []
    for industry, rows in sorted(by_industry.items()):
        if not is_mixed_lender_industry(industry):
            continue
        for row in rows:
            sym = normalize_ticker(row.get("symbol"))
            if sym and sym not in NON_LENDER_MEMBERS and sym not in REVIEWED_CREDIT_SERVICES_LENDERS:
                out.append(f'{row.get("symbol")} "{_row_name(row)[:60]}" [{industry}]')
    return out


def _log_unreviewed_mixed_members(by_industry: Dict[str, List[Dict[str, Any]]]) -> None:
    """WARNING naming every unclassified member of a mixed industry (see
    `_unreviewed_mixed_members`). Gated as a lender until classified — the safe default —
    but a new payment or fee business there would lose its liquidity rows and be compared
    with lenders, so the owner classifies it before uploading."""
    entries = _unreviewed_mixed_members(by_industry)
    if entries:
        logger.warning(
            "benchmark universe: %d member(s) of a mixed lender industry are not classified "
            "— gated as lenders until reviewed; add each to "
            "financials_metric_gate.REVIEWED_CREDIT_SERVICES_LENDERS (a lender, with a reason) "
            "or to the curated non-lender list (with evidence) before uploading: %s",
            len(entries), _named(entries),
        )


class _quiet:
    """Silence one logger for a block (a guard asked for its verdict only); restored on
    exit, whatever happens inside."""

    def __init__(self, target: logging.Logger) -> None:
        self._target = target
        self._was: bool = target.disabled

    def __enter__(self) -> None:
        self._was = self._target.disabled
        self._target.disabled = True

    def __exit__(self, *exc: Any) -> None:
        self._target.disabled = self._was


def _write_atomically(path: Path, payload: Dict[str, Any]) -> None:
    """Via a `.part` file and a rename, so a crash never leaves a half-written universe."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".part")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp.replace(path)


async def main(
    floor: int = _DEFAULT_FLOOR,
    *,
    output: Path = _OUTPUT_PATH,
    allow_shrink: ShrinkOverride = None,
    allow_missing: Optional[Iterable[str]] = (),
    fmp: Any = None,
    sleep: Sleep = asyncio.sleep,
    page_limit: int = _SCREENER_PAGE_LIMIT,
    allow_floor_change: bool = False,
    skip_twin_scan: bool = False,
    clock: Clock = time.monotonic,
) -> int:
    """Build, check and write the universe. Returns the process exit code.

    `allow_shrink`: the largest drop to permit, in percent (`True` = the bare flag, 50);
    `allow_missing`: industries that may leave (see `_missing_industries_allowed`);
    `allow_floor_change`: write over a file built at another floor (`_floor_change_allowed`);
    `skip_twin_scan`: no `ratios-ttm` calls — a quick local run, never a file to upload.
    """
    _shrink_limit(allow_shrink)          # a bad override fails here, before any FMP call
    # A bare string is one industry, never its characters; None is none.
    if allow_missing is None:
        allow_missing = []
    allow_missing = [allow_missing] if isinstance(allow_missing, str) else list(allow_missing)
    # The floor guard needs no FMP answer: refused before ~3,000 calls are spent on a build
    # that could never be written.
    previous = _load_previous(output)
    if previous is not None and not _floor_change_allowed(previous, floor,
                                                          bool(allow_floor_change)):
        return EXIT_SHRINK_REFUSED
    owns_client = fmp is None
    client = FMPClient() if owns_client else fmp
    try:
        try:
            industries = await _list_industries(client, sleep=sleep)
        except Exception as exc:
            logger.error(
                "benchmark universe: could not list FMP industries (%s) — nothing written",
                _describe(exc),
                exc_info=not isinstance(exc, _EXPECTED_FAILURES),
            )
            return EXIT_BUILD_FAILED
        logger.info("FMP available-industries: %d entries (floor=$%.0fM)",
                    len(industries), floor / 1e6)

        sem = asyncio.Semaphore(_CONCURRENCY)
        raw_by_industry: Dict[str, List[Any]] = {}
        failures: Dict[str, str] = {}
        rows_seen = 0

        async def _one(name: str) -> None:
            nonlocal rows_seen
            async with sem:
                try:
                    rows = await _screener_for_industry(
                        client, name, page_limit=page_limit, sleep=sleep,
                    )
                except Exception as exc:
                    # Scrubbed BEFORE it is stored: the summary below joins these strings.
                    failures[name] = _describe(exc)
                    logger.warning(
                        "benchmark universe: industry=%r FAILED (%s)", name, failures[name],
                        exc_info=not isinstance(exc, _EXPECTED_FAILURES),
                    )
                    return
            rows_seen += len(rows)
            raw_by_industry[name] = rows

        await asyncio.gather(*[_one(i) for i in industries])

        if failures:
            logger.error(
                "benchmark universe: %d of %d industries FAILED — nothing written (a universe "
                "missing an industry empties its peer medians until the next build): %s",
                len(failures), len(industries),
                "; ".join(f"{k} ({v})" for k, v in sorted(failures.items())),
            )
            return EXIT_BUILD_FAILED

        # Filtered only now that every industry is in: the twin rules read the whole market.
        filtered = _filter_market(raw_by_industry, floor)
        twin_counts: Optional[Tuple[int, int]] = None
        # The twin pass only REMOVES rows, so a shrink or missing-industry refusal is already
        # certain before it: refuse now, before ~3,000 ratios-ttm calls (the guards run again,
        # logged, on the final rows below).
        if previous is not None and not skip_twin_scan:
            pre = _aggregate(filtered.kept)
            with _quiet(logger):
                pre_ok = (_shrink_allowed(_ticker_count(previous["industries"]),
                                          _ticker_count(pre), allow_shrink)
                          and _missing_industries_allowed(previous["industries"], pre,
                                                          allow_missing))
            if not pre_ok:
                _shrink_allowed(_ticker_count(previous["industries"]), _ticker_count(pre),
                                allow_shrink)
                _missing_industries_allowed(previous["industries"], pre, allow_missing)
                logger.info(
                    "benchmark universe: refused BEFORE the statement-twin scan — no "
                    "ratios-ttm call was spent (see the REFUSED line above)",
                )
                return EXIT_SHRINK_REFUSED
        # The statement-twin pass runs BEFORE anything reads the result, so every count,
        # drop line, cross-industry warning and guard below sees the rows that will vote.
        if skip_twin_scan:
            logger.warning(
                "benchmark universe: statement-twin scan SKIPPED (--skip-twin-scan) — a "
                "company FMP serves under two listings may vote twice; never upload this file",
            )
        else:
            scan = await _fetch_statement_fingerprints(
                client, (r["symbol"] for rows in filtered.kept.values() for r in rows),
                sleep=sleep, clock=clock,
            )
            _log_fingerprint_scan(scan)
            if _twin_failures_fatal(len(scan.failed), scan.planned):
                logger.error(
                    "benchmark universe: %d of %d ratios-ttm calls FAILED (more than %d%%: an "
                    "outage, not a few bad symbols; %d left uncalled) — nothing written: %s",
                    len(scan.failed), scan.planned, _TWIN_MAX_FAILURE_PERCENT,
                    len(scan.not_called),
                    "; ".join(f"{k} ({v})" for k, v in list(scan.failed.items())[:10]),
                )
                return EXIT_BUILD_FAILED
            problem = _twin_scan_problem(scan)
            if problem is not None:
                logger.error(
                    "benchmark universe: statement-twin scan proved nothing — %s. Nothing "
                    "written: every listing FMP serves twice would vote twice behind a clean "
                    "exit. Re-run once the endpoint answers normally (--skip-twin-scan only "
                    "for a local file that is never uploaded).", problem,
                )
                return EXIT_BUILD_FAILED
            twin_counts = (len(scan.fingerprints), scan.planned)
            if scan.failed:
                logger.warning(
                    "benchmark universe: %d of %d ratios-ttm call(s) failed (within the %d%% "
                    "budget) — those rows are kept unfingerprinted: %s", len(scan.failed),
                    scan.planned, _TWIN_MAX_FAILURE_PERCENT, ", ".join(scan.failed),
                )
            outcome = _one_vote_per_statement_set(filtered, scan.fingerprints)
            _log_statement_twins(outcome)
            filtered = outcome.filtered
        by_industry = filtered.kept
        for name, kept in by_industry.items():
            logger.info("  %-45s %d tickers (%d dropped)", name, len(kept),
                        filtered.drops_by_industry.get(name, 0))
        _log_drops(filtered.dropped, filtered.examples, rows_seen)
        _log_cross_industry_twins(filtered.cross_industry_twins)
        _log_cross_industry_share_classes(_cross_industry_share_classes(by_industry))
        _log_suspect_rows(by_industry)
        _log_owner_call_keeps(by_industry)
        _log_unreviewed_mixed_members(by_industry)
        aggregated = _aggregate(by_industry)
        total = _ticker_count(aggregated)
        logger.info("Industries with constituents: %d  Total tickers: %d", len(aggregated), total)
        if not aggregated:
            # Never written, even with --allow-shrink: an empty universe blanks every
            # industry and sector median.
            logger.error(
                "benchmark universe: no industry has a usable constituent (%d industries "
                "asked) — nothing written", len(industries),
            )
            return EXIT_BUILD_FAILED
        _log_fund_like_symbols(aggregated)

        if previous is not None:
            before = previous["industries"]
            _log_comparison(before, aggregated)
            # Both checks run (and log) before either refuses, so one run names every problem.
            shrink_ok = _shrink_allowed(_ticker_count(before), total, allow_shrink)
            missing_ok = _missing_industries_allowed(before, aggregated, allow_missing)
            if not (shrink_ok and missing_ok):
                return EXIT_SHRINK_REFUSED
        elif allow_missing:
            logger.warning(
                "benchmark universe: --allow-missing given but there is no previous file to "
                "compare with — it changes nothing",
            )

        payload = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "source": (
                "fmp /stable/available-industries + /stable/company-screener "
                f"(exchange={','.join(_US_EXCHANGES)}, isEtf=false, isFund=false, "
                "isActivelyTrading=true; market-cap floor applied to each row's marketCap)"
                + (" — statement-twin scan SKIPPED" if skip_twin_scan
                   else " + /stable/ratios-ttm (one vote per set of statements: "
                        f"{twin_counts[0]} of {twin_counts[1]} rows fingerprinted)"
                   if twin_counts is not None
                   else " + /stable/ratios-ttm (one vote per set of statements)")
            ),
            "market_cap_floor": floor,
            "industry_count": len(aggregated),
            "ticker_count": total,
            "industries": aggregated,
        }
        _write_atomically(output, payload)
        logger.info("Wrote %s — upload it to the 'universe-data' bucket", output)

        per_sector: Dict[str, int] = {}
        for e in aggregated:
            per_sector[e["sector"]] = per_sector.get(e["sector"], 0) + 1
        print("\nIndustries per sector:")
        for sector, n in sorted(per_sector.items()):
            print(f"  {sector:<25} {n}")
        return EXIT_OK
    finally:
        if owns_client:
            await client.close()


def _shrink_percent_arg(text: str) -> int:
    """`--allow-shrink PCT`: a whole percentage, 1-100."""
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected a whole percentage, got {text!r}")
    if not 1 <= value <= 100:
        raise argparse.ArgumentTypeError(f"expected 1-100, got {value}")
    return value


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build the broad benchmark universe")
    parser.add_argument("--floor", type=int, default=_DEFAULT_FLOOR,
                        help="Market-cap floor in USD (default 500M)")
    parser.add_argument("--allow-shrink", nargs="?", type=_shrink_percent_arg, default=None,
                        const=_DEFAULT_ALLOW_SHRINK_PERCENT, metavar="PCT",
                        help=f"Write even when the ticker count drops more than "
                             f"{_MAX_SHRINK_PERCENT}%% below the previous file — up to PCT%% "
                             f"(default {_DEFAULT_ALLOW_SHRINK_PERCENT} when given bare)")
    parser.add_argument("--allow-missing", action="append", default=[], metavar="INDUSTRY",
                        help=f"An industry that held at least {_MISSING_INDUSTRY_MIN_TICKERS} "
                             f"operating tickers before and may have none now (repeatable; "
                             f"quote names with spaces)")
    parser.add_argument("--output", type=Path, default=_OUTPUT_PATH,
                        help="Where to write (default backend/data/benchmark_universe.json); "
                             "the shrink guard compares with this file")
    _add_shared_flags(parser)
    return parser


def _add_shared_flags(parser: argparse.ArgumentParser) -> None:
    """The two flags `scripts.discover_industries` passes through too."""
    parser.add_argument("--allow-floor-change", action="store_true",
                        help="Write even when the previous file was built at another "
                             "market-cap floor")
    parser.add_argument("--skip-twin-scan", action="store_true",
                        help="Skip the statement-twin pass (one ratios-ttm call per kept "
                             "row) — a quick local run only, never a file to upload")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
    # As app/main.py does: every record that reaches the console, from ANY module (the FMP
    # client's own lines included), is scrubbed of `apikey=` and the other secrets.
    for _handler in logging.getLogger().handlers:
        _handler.addFilter(SecretRedactingFilter())
    logging.getLogger("httpx").setLevel(logging.WARNING)
    args = _build_parser().parse_args()
    sys.exit(asyncio.run(main(args.floor, output=args.output, allow_shrink=args.allow_shrink,
                              allow_missing=args.allow_missing,
                              allow_floor_change=args.allow_floor_change,
                              skip_twin_scan=args.skip_twin_scan)))
