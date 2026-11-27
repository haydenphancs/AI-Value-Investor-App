"""
Company Weekly (Drop 2a) — THE one FMP exemption of the marketing engine (contract D6).

Every other module under `app/services/marketing/` is FMP-free (`tests/test_marketing_import_boundary.py`):
this one does the I/O and nothing else. It reads FMP and the FMP-backed services, runs the pure
gates of `company_news_rules` over what came back, and hands out frozen RECORDS that carry no
price, market cap, profile CEO, image, URL or member identity (`tests/test_marketing_company_news_leaks.py`
pins the field list and plants canaries in every input key). Its only importer is
`MarketingScriptService._news_source_fn` (lazy, function-scoped — pinned too).

API (contract D6):

* `candidates(series, *, run_date, exclude, limit, deadline, deps)` → `Candidates(series, records,
  skip_reason, rejections)`: at most ``limit`` records in the series' rank order, so a candidate the
  template refuses falls through to the next. ``records == ()`` always comes with a `SKIP_REASONS`
  code (an honest "nothing qualified"); per-candidate refusals are counted in ``rejections``
  (`REJECTION_REASONS`; a ledger hit is counted as ``already_posted`` and never fetched further).
* Any upstream failure, a partial page, less than one second of budget left, or an unexpected
  error RAISES `MarketingNewsUnavailable(series, reason, detail)` — never a record built on
  partial data, never `FMPPartialPageException.partial`, never a retry (the FMP client retries
  5xx itself). An unexpected error is reason ``internal_error``, logged with ``logger.exception``.
  Two deliberate exceptions, both per CANDIDATE: the Form 4/A check's per-issuer read — when it
  fails, that row is dropped (``amendment_check_failed``, WARNING) and the week keeps the rest —
  and an earnings report's quarterly-statement read (a failed read refuses that report as
  ``statement_unavailable``, WARNING — review of 2026-10-16; it was counted ``non_usd_reporter``, so an
  outage read as "nothing qualified"). When EVERY statement read of the call failed and no report is
  held, the series raises ``earnings_statements_unavailable`` (as ``profiles_unavailable`` for the
  profiles).
* Form 4 amendments (review rounds 7-9): ONE issuer-level rule, no exception — ANY amendment row
  filed since the window opened, on the issuer's symbol or issuer CIK (every share class, any
  reporter, any code), refuses every row of that issuer for the week (``amended_filing``), so a
  published row is never built from, or beside, an amendment of its company. The read that
  applies it to the codes the P walk never returns is per ISSUER (``companyCik`` = the profile's
  CIK: every share class and symbol spelling at once, review round 9), and must hold the person's
  newest-day lines and every walk row of the issuer filed since (else ``amendment_check_failed``;
  no profile CIK → the same). A CEO / CFO is published only while every title of theirs is a
  sitting officer's — an allow-list of title words, never a list of bad ones, applied to an
  ``other:`` text naming the role too; a director's ``other:`` text naming the seat must be a
  sitting director's (``role_uncertain``).
* 13F moves are chosen by dollar size alone (review round 8; review 2026-11-20 removed each kind's
  reserved slots — the one ranked list shows the largest changes); an exit carries its
  previous-quarter value (``prev_value_usd``, never drawn) so it ranks too.
* Review round 9 (2026-10-10), each fail-closed:
  - Form 4: a member of Congress is matched by the surname rule too (a legal first name, a
    nickname twin, an initial — `company_news_rules.is_congress_name`), and the whole PERSON is
    dropped on every symbol once any walk row, line or issuer-read row of theirs matches; a
    roster that is not fresh for the run date (`R.roster_fresh_for`: older than
    `R.ROSTER_MAX_AGE_DAYS`, or — review round 11 — not holding the sitting Congress) refuses both
    Form 4 series (`internal_error`, the chain falls back); a person some of
    whose in-window purchases of the issuer the row would not hold (a line a gate or the
    extractor dropped, another class, a line the walk missed) is refused (``partial_person``),
    never re-summed; two CEOs — or two CFOs — on one issuer are grouped BEFORE the role
    refusal, and the per-issuer read is checked for a co-officer or a second one.
  - 13F: a move whose issuer has option / note rows on either book is dropped
    (``move_has_options``, and leaves its kind's count when it is a new / exited holding); the
    subject is the EDGAR filer (`R.THIRTEEN_F_FILERS`; a natural-person filer is refused,
    ``filer_is_person``); the filer's chip only when its profile CIK is the filer's; the club
    path ties share classes against its stored previous book and the profiled names.
  - A held theme survives the next theme's failed lookups; a Congress count is refused beside a
    ticker-less purchase naming the company with its words run together (``count_uncertain``).
* The members-elect window (owner decision 4, 2026-10-15): from an even year's Election Day through
  January 3 every Form 4 row is built with NO person — the roster holds the sitting Congress and cannot
  know a member-elect — and the posts keep running; the Congress drop still runs on every raw name.
  Judged by `R.members_elect_for_run` (review of 2026-10-16): the RUN date and the two days after it,
  when the run's posts can still go out — a Monday-before-Election-Day run is role-only too.
* Money Map (Batch 1, 2026-10-15): a fiscal year that ended `MONEY_MAP_NEXT_YEAR_DUE_DAYS` (364) or
  more days before the run is refused (``money_map_next_year_due``: the next year is due), and a segment
  label that names nothing a reader knows — a code of 1-4 capitals off `R.SEGMENT_ACRONYMS_OK` ("QCT";
  AWS and region names such as "US" stay) or a generic "Products" (`R.segment_fold_reason`) — folds
  into the rest, in a theme's segment fact too. Gross profit is the statement's own line, which FMP
  fills as revenue − cost of revenue where the issuer reports no gross subtotal: never "as reported".
  The review of 2026-10-16: a due year is a date fact, never a content refusal (MM-4: eight December
  year-ends used to close the series from about Jan 1 to March); each symbol's last-read fiscal-year end
  is memoized (`MONEY_MAP_FY_END_TTL_SECONDS`), so a known due year is refused before the fan-out and
  tried after every candidate that can publish (the re-review of 2026-10-19, MM-4-R: after BOTH groups, with
  a year the ledger already posted); B3PD-1 (2026-10-20): every read that cannot publish is remembered too
  (`_money_map_remember_refusal`: a content refusal, a gap, `already_posted`), so a refused never-posted seed
  waits there as well instead of spending every call's content cap, and a re-read of a year it refused is
  answered before the fan-out (`MONEY_MAP_REFUSAL_RECHECK_DAYS`); and a label wrong for ONE company is
  corrected for that company only (`R.SEGMENT_SYMBOL_OVERRIDES`: McDonald's retired "High-Growth Markets"
  is its International Operated Markets; since the re-review of 2026-10-19 PepsiCo's retired "Africa,
  Middle East and South Asia" is its realigned Europe, Middle East and Africa). The round-2 review of
  2026-10-20 (RR2-MM-TEMPLATE-WALL): a record the TEMPLATE refuses is remembered too — a loss larger than
  revenue is refused at the read (``money_map_loss_above_revenue``, the template's ``implausible_figures``
  gate), and any other template refusal is reported back by the script flow (`remember_template_refusal`,
  ``template_refused``) — so such a company never keeps the head of the pool, spending the walk's attempts.
* 13F (Batch 1, 2026-10-15, plan item 3): the lead filers (`R.THIRTEEN_F_LEAD_FILERS`, Berkshire) are
  read first, then the Trillion Club filers, then the rest of the registry, under ONE registry budget
  (still `MAX_LIVE_13F_BUILDS` live builds per call; a filer is read once). A registry CIK with a
  successor (`R.THIRTEEN_F_SUCCESSORS`: Pershing Square → 0002026053, whose old CIK filed only a 13F-NT)
  is read, built, keyed and named as the successor, in its registry CIK's curated place. A filing whose
  split lookups the shared cap trimmed (`BuiltFiling.split_unchecked`; for a club filer, re-derived from
  its stored books — the row keeps no record of it) shows no more / fewer shares: those moves and both
  counts are dropped (``split_unchecked``); newly reported and exited moves stay.
* 13F, the review of 2026-10-16, each dropping every more / fewer move and both counts (never one kind or
  one move: the counts are stated publicly) — newly reported and exited moves stay:
  - F1: EVERY increased / decreased row is re-checked through the builder's own corporate-actions seam
    (`_builder.split_windows`, `THIRTEEN_F_MAX_SPLIT_CHECKS`), because the shared suspect filter never looks
    up a split whose per-share price moved under 30% (a 5:4 split at a flat price, a 3:2 split after a 15%
    rally): a split the build did not restate, an unnameable adjustment or a failed lookup in the quarter →
    ``split_in_window``; a row that cannot be re-checked (no symbol, past the cap, rows that are not what the
    counts count) → ``split_unchecked``. A split the build DID restate stays (the row's previous count is the
    previous book's times the split ratio). It also closes F3: a capped suspect a club re-derivation missed is
    looked up as a row of its own. The re-review of 2026-10-19 (F13-QE-SPLIT): the re-check reads a week past the
    quarter's end (`THIRTEEN_F_SPLIT_RECHECK_SLACK_DAYS`) — a split paid on the last session is ex-dated after it,
    and a filer booking on the payable date reports the post-split count.
  - F13-RENAME (the same re-review): a more / fewer row whose CUSIP sat under ANOTHER symbol on the previous book
    (a rename: FMP keeps the old symbol on the old extract) is looked up under every symbol its CUSIP carries on
    either book, and is clean only when each answers clean.
  - F2: a club filer's STORED build that is not ``complete`` → ``degraded_build`` — since the owner-delegated
    decision of 2026-10-19 for the WHOLE filer, every move (parity with the registry path, which refuses a failed
    split or symbol lookup outright; the row keeps no reason, so a ``profiles_missing`` or ``unresolved_pending``
    build is refused too), and a stored row that cannot be read refuses the filer (``filer_unavailable``). Its
    stored change rows (filed symbols and CUSIPs; the detail carries routable symbols only) are the ones
    re-checked, tied to the detail by the counts.
  - live preview F2: a successor's hand-over quarter (`R.successor_transition`: Pershing's 2026-Q2 against its
    Howard-Hughes-only 2026-Q1) is refused ``successor_transition`` before any FMP call; a registry row curated
    inactive that has a successor is skipped with a WARNING, never silently (F4).
* `fetch_logo(symbol, *, max_bytes, timeout)` → ``(bytes, content_type)`` or None. Never raises,
  never validates (that is `logo_check`), logs a WARNING with the reason — never the URL.
* `remember_template_refusal(record, codes, *, run_date)` → bool (the round-2 review of 2026-10-20): the
  script flow reports a record its template refused; a Money Map record's refusal is remembered for its
  fiscal year (``template_refused``), so the walk tries that company after every candidate that can publish —
  and (the round-3 review, RR3-STAKES-TEMPLATE-WALL) a company stake's by its stake id, so that stake is offered
  after every other one until its row changes. Synchronous, no I/O; any other record is ignored (False).
* The spotlight lane (owner decisions 2026-11-15, "Add a second daily post"; B decisions 5-8), two single-company
  series, shipped with their templates (news-v22) and run only by the spotlight lane's chains:
  - ``insider_spotlight`` (`_collect_insider_spotlight`): since the owner decisions of 2026-11-20 ("each post or each
    image/video will have more than 2,3, ideally are 3 - 5 companies at once") a LIST of `R.INSIDER_MIN_ROWS` to
    `R.INSIDER_MAX_ROWS` issuers (`R.InsiderSpotlight` of `R.InsiderSpotlightCompany` rows: each issuer's largest
    qualifying CEO / CFO / director and up to `R.SPOTLIGHT_MAX_OTHERS` more of it), the largest total first — fewer is
    no post (``insider_spotlight_too_few_companies``; the chain falls through to the theme). The window runs from the
    day after the last window an earlier insider spotlight CHECKED (``spotlight_checked_through``, passed by the script
    flow — C0, 2026-11-16) to the day before the newest filing the market-wide walk holds; the window it checked rides
    back on ``Candidates.checked_window`` for the script flow to record. No new day to check is a quiet skip
    (``insider_spotlight_no_new_window``) while the feed is current, ``insider_feed_behind`` (unavailable, never a skip)
    when it is not. Every Form 4 gate of the roundups runs (the walk-level gates, the profile-stage gates and the
    per-reporter gates are SHARED helpers — `_insider_gated_lines`, `_issuer_lines`, `_reporter_rows` — so the
    roundups' answers are byte for byte what they were), with two windows (`_amendment_verdict`: the 4/A / role /
    co-officer LOOK-BACK, never shorter than the roundups' week, and the PURCHASE window a person's row must hold whole)
    and the per-issuer Form 4/A check run per PERSON (`_spotlight_checked`); one person once across the list. Every
    company of a posted list is keyed once (``news:insider_spotlight:<SYM>+<SYM>…:<window start>``, already_posted for
    that window); a 14-day issuer cooldown by root symbol and display name (``spotlight_cooldown``,
    ``spotlight_recent`` passed by the script flow). The one-issuer post's revenue line is retired with it.
  - The weekly roundups (owner decisions 2026-11-20): fewer than `R.INSIDER_MIN_ROWS` rows is no post
    (``ceo_too_few_companies`` / ``insider_too_few_companies``; Monday's chain falls through to the theme).
  - ``thirteen_f_spotlight`` (`_collect_thirteen_f_spotlight`): ONE filer's largest eligible holdings (up to
    `R.THIRTEEN_F_SPOTLIGHT_MAX_MOVES`, owner feedback 2026-11-19) through the roundup's own walkers; never a lead filer (``lead_filer_reserved``); more / fewer shares only when vouched, never split-restated
    (``split_restated``: the re-check's restated rows travel out of band on the call context, never on a record) and
    only at a 25% change; one 13F post per filer per quarter across both lanes (`_widened_13f_exclude`, which the
    roundup reads too).
  - The Form 4 roundup rows now carry the filing's own cost per share (`R.InsiderPurchase.cost_per_share_min_usd` /
    ``_max_usd``: the lines' own prices, as filed — never the profile price).
* `company_facts(symbol)` → ``{sector, industry}`` or None (workstream C's art brief): name-free, from the profile memo
  (else one read), never raises.
* company_stakes, the round-3 review of 2026-10-20 (RR3-STAKES-TEMPLATE-WALL): the read applies every gate of the
  template's that a stake ROW decides (`_stake_candidate`: the drawn caps on the source title, the investee name
  and the local listing — `R.STAKE_SOURCE_DRAW_MAX_CHARS`, `R.STAKE_TEXT_WORD_MAX_CHARS`,
  `R.STAKE_PAIR_NAME_WORD_MAX_CHARS`, copied from the template and pinned equal by a test — a committed-up-to basis
  only on a commitment, no ``verified_on`` after the run), and the investor's name word cap once its profile is
  read; five stakes refused at the head of the newest-first walk used to end the series every day.
* `COLLECTORS` — one collector per series whose code exists: the 2a four and, since Drop 2b,
  `congress_count`, `company_stakes`, `earnings` and `theme_explainer` (their keys join
  `selection.SHIPPED_SERIES` in the plumbing step; production runs only what MARKETING_NEWS_SERIES
  lists, the 2a four by default).
* Drop 2b, each fail-closed — a refused candidate costs one post; a wrong figure, role or claim
  about a named company, or a narrowed member of Congress, is the harm:
  - ``congress_count``: purchases (never an exchange or a sale) of stock (never an option, a bond
    or a fund) disclosed in the month before the run's month, both chambers together; the walk
    must provably reach past the month (the feeds are sorted by disclosure date, newest first —
    re-checked on every read) and a second read must agree; DISTINCT members ≥ 2, counted by
    salted per-call hashes of the identity fields — every identity field (``senateID`` included)
    is dropped at the source and no hash leaves the count — and refused when the identity
    fields cannot settle the count either way or an in-month purchase of the same company sits
    under another class or no usable symbol. No identity ever reaches a record, a log line, the
    memo or a fact sheet.
  - ``company_stakes``: Trillion Club stakes (published, `stake_problem`-valid, a named investee,
    a disclosed dollar figure and its basis — never a price); catalogue stakes allowed
    (`company_news_rules.STAKES_INCLUDE_CATALOGUE`), newest ``as_of`` first, one per ledger key.
    Since the stake finder (migration 205, 2026-10-28) only a row a person reviewed: hand-kept
    (``origin`` seed), published by the owner's tap (proposal), or found and published automatically
    and then KEPT by the owner (auto + ``owner_kept_at``) — filtered in the read, re-checked strictly
    on every row after the row-cap check and before the catalogue's known words are built (a left-out
    row's investee name never vouches for another row's source title); counted in a log line, never
    a rejection code. news-v23 (the restyle, owner 2026-11-19: "filter for companies that public only. no
    private" + "add 1-3 more companies for the NVIDIA (or the other trillion companies)"): ONE post holds ONE
    investor's stakes in PUBLIC companies (`R.CompanyStakes`, 1-4 — RESTYLE_SPEC §2.5; the picture draws every
    one) — a stake is public only with a kind that can be (`R.PUBLIC_STAKE_KINDS`), a listed U.S. symbol, a
    profile `R.company_from_profile` accepts for the stake's company and ``isActivelyTrading`` exactly True
    (fail closed: ``stake_not_public`` / ``stake_listing_unverified`` / ``stake_not_trading``, each dropped and
    its investee never named); the investor with the most not-yet-posted public stakes leads (`_stakes_records`),
    and the post's ledger key names every stake it holds (`R.stake_ref_ids`), so each is posted once.
  - ``earnings``: the previous seven ET days' calendar, one day per call (a day at the 4,000-row
    cap is unavailable, never ranked), EPS digit-shift and implausible gaps refused — and (owner-delegated,
    2026-10-19) an |EPS actual| or |estimate| above `R.EPS_MAX_ABS`, $1,000,000 a share (``eps_implausible``) —,
    one report per company (two that disagree are refused). Batch 1 (2026-10-15), each refusing the WHOLE report:
    one revenue pair inside [0.5, 1.5] on every row (``revenue_implausible`` — never the EPS kept
    without it; since news-v21 a check only, never printed: the estimate's basis is unknown), a surprise of at
    least 5% on the EPS (``surprise_unclear``), and ONE quarterly-statement read
    (``limit=4``) that must report in USD (``non_usd_reporter`` otherwise: the profile's currency is only
    the trading currency; a read that FAILS is ``statement_unavailable``, below) and place the quarter
    (``period_mismatch`` / ``period_unverified``, `company_news_rules.earnings_period`; the record then
    carries its ``period_end``). Ranked by size: the first 60 by revenue estimate profiled at once, gated, ordered
    by market cap, then read in that order until ``limit`` reports are held (at most 20 reads).
    Review of 2026-10-16, each fail-closed: the surprise must be clear on the PRINTED figures too
    (``surprise_unclear``: "$0.10 vs $0.10" is no surprise); the placed row must be a QUARTER
    (``period_unverified``: a half-year, a single row, a report a whole quarter after the row); a failed read
    is ``statement_unavailable`` (every one failing raises ``earnings_statements_unavailable``); a Real Estate /
    REIT issuer is refused before its read (``eps_basis_reit``) and an actual and estimate on two bases after it
    (``eps_basis_mixed``, against the quarter's statement diluted EPS — Prologis' $1.60 FFO-scale actual beside
    a $0.78 GAAP-scale estimate); ONE record per ISSUER: a 5-letter twin of a 4-letter root listed in the same
    window (FITBI, HBANL, WAFDP, GOOGL beside FITB, HBAN, WAFD, GOOG — never an OTC foreign F / Y line, often
    another company: CMPRF beside CMPR) is never a candidate while its root reported (Alphabet's GOOGL, a curated
    class line, stands for the issuer when GOOG has no reported row), a NASDAQ preferred line (5 letters ending
    P/O/N/M) is refused at the profile gate, listings of one issuer (profile CIK, else display name) keep the ROOT
    — the shortest symbol, never the largest FMP market cap, which prices a $25 preferred on the common's share
    count — and an issuer is refused when its listings' rows do not vouch for one report (the re-review of
    2026-10-19, `_family_problem`: two ACTUALS → ``record_invalid``; an estimate of any listing that leaves the
    surprise unclear or turns it → ``surprise_unclear`` — consensus figures that merely differ per ticker are one
    report), every other listing counted ``share_class_overlap``; and a listing whose issuer already has a posted
    earnings key for a day of this window — or of the `EARNINGS_POSTED_LOOKBACK_DAYS` before it (EME-1) — is
    ``already_posted`` (one report, one post).
  - ``theme_explainer``: an Emerging Frontiers theme's name and ticker list only, every member
    through the company gate, the largest revenue segment of the first members (a member with no
    segment fact is still listed); the record carries the theme's full size in COMPANIES before
    any gate (``theme_size``: share classes of one issuer count once) so the copy never calls a
    gated list complete, nor counts one company twice.

The memo (CLAUDE.md invariant 4, process-local only — the durable copy of what was used is the
run's `marketing_scripts.fact_sheet`): insider raw rows 30 min (the market-wide P walk, and the
per-issuer all-code rows of the Form 4/A check), profiles 10 min, a registry 13F
build 6 h, logo bytes 24 h, the club / whale / theme / stake lists and a club filer's stored
previous-quarter book (an exit's materiality) — and, for every club filing with a move, its stored current
build row (holdings, change rows, build status: Batch 1, the review of 2026-10-16 and the owner-delegated
decision of 2026-10-19) — 10
min, a Congress month's identity-free counts and
each earnings-calendar day and each report's quarterly statements 30 min, and a Money Map symbol's
last-read fiscal-year end (the review of 2026-10-16) 400 days. Concurrent callers share one
upstream walk through ``_inflight`` futures (shielded; failures settled with
`fail_shared_future`). An exception is never memoized, and neither is an empty insider feed.

Logs never carry a member-of-Congress name, a refused raw reporting name, or a URL (exception
texts are scrubbed of URLs before they are logged or stored on the exception).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import re
import secrets
import time
import unicodedata
import uuid
from collections import Counter
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import (
    AbstractSet, Any, Awaitable, Callable, Dict, FrozenSet, Iterable, List, Mapping, Optional, Sequence, Set,
    Tuple, Union,
)

import httpx

from app.config import settings
from app.database import get_supabase
from app.integrations.fmp import FMPUnavailableException, get_fmp_client
from app.services._insider_buys_common import (
    BAD_SYMBOLS as _BAD_FORM4_SYMBOLS, MAX_ROW_DOLLARS as _FORM4_MAX_ROW_DOLLARS, SYMBOL_RE as _FORM4_SYMBOL_RE,
    canonical_symbol as form4_symbol, extract_insider_buys, insider_role, normalize_cik, price_plausible,
    rank_buy_symbols,
)
from app.services._earnings_common import eps_digit_shift_suspect
from app.services._insider_common import (
    classify_insider_transaction, distinct_trades, insider_reporter_key, is_common_stock, officer_title,
)
from app.services._whale_common import (
    MAX_SPLIT_LOOKUPS, is_13f_non_share_row, select_13f_comparison, thirteen_f_share_positions,
)
from app.services.company_facts_service import get_company_facts
# Pure at import (stdlib + market_hours + two pure leaves; checked 2026-10-10): the one-day-per-call,
# all-or-nothing calendar pager the Home Earnings Shockers card uses.
from app.services.earnings_window_service import fetch_calendar_days
from app.services.marketing import company_news_rules as R
from app.services.marketing import selection
from app.services.profit_power_service import get_profit_power_service
from app.services.revenue_breakdown_service import get_revenue_breakdown_service
from app.services.trillion_club import builder as _builder
from app.services.trillion_club import rules as _tc_rules
from app.services.trillion_club_service import get_trillion_club_service, stake_problem
from app.utils.inflight import fail_shared_future
from app.utils.supabase_async import sb_exec

logger = logging.getLogger(__name__)

# ── budgets, TTLs, caps ───────────────────────────────────────────────────────

#: A step never starts with less than this much of the caller's deadline left.
MIN_STEP_SECONDS = 1.0

INSIDER_RAW_TTL_SECONDS = 30 * 60.0
PROFILE_TTL_SECONDS = 10 * 60.0
BUILT_13F_TTL_SECONDS = 6 * 3600.0
LOGO_TTL_SECONDS = 24 * 3600.0
#: The club rows, the 13F whale rows and the Emerging Frontiers ticker lists.
LIST_TTL_SECONDS = 10 * 60.0
_MEMO_MAX_ENTRIES = 4096

#: Form 4 feed walk: 1000 rows ≈ 14 days of market-wide P rows, so one page covers the week.
INSIDER_PAGE_SIZE = 1000
INSIDER_MAX_PAGES = 3
#: Symbols (signals' rank order) that reach the profile stage per call.
INSIDER_PROFILE_CANDIDATES = 15
#: One person's buys above this share of the issuer's market cap are a unit error, not news.
INSIDER_MAX_CAP_SHARE = 0.10
#: The per-ISSUER Form 4/A check (review rounds 2, 7, 8 and 9): the market-wide walk asks FMP for
#: P rows only, so an amendment under another code (a re-code, a derivative-only 4/A) never
#: reaches it. The rows a week can publish (and a few spares) are re-read per ISSUER — by the
#: profile's issuer CIK (``companyCik``), so every share class, symbol spelling and blank symbol
#: of the company comes back at once, whatever the walk happened to show — with NO transaction
#: filter, and THE amendment rule is applied to that read: at most this many rows per call, one
#: page per issuer in the normal case (a short page ends an issuer's feed; a grant-season week of
#: every code still fits one 1000-row page), memoized like the walk and shared by both Form 4
#: series. FMP calls per Form 4 call: ≤ `INSIDER_MAX_PAGES` walk pages + ≤
#: `INSIDER_PROFILE_CANDIDATES` profile calls (one per symbol) + ≤ `INSIDER_AMENDMENT_MAX_SYMBOLS`
#: × `INSIDER_AMENDMENT_MAX_PAGES` per-issuer pages (normally one page per row).
INSIDER_AMENDMENT_MAX_SYMBOLS = R.INSIDER_MAX_ROWS + 3
INSIDER_AMENDMENT_PAGE_SIZE = INSIDER_PAGE_SIZE
INSIDER_AMENDMENT_MAX_PAGES = 2

# ── the spotlight lane (owner decisions 2026-11-15, "Add a second daily post") ─────────────────────────────
#: The insider list reads each issuer's CEO, CFO and directors (`_collect_insider_spotlight`).
SPOTLIGHT_ROLES: Tuple[str, ...] = ("ceo", "cfo", "director")
#: How many qualifying people of one issuer reach the per-person Form 4/A check: the lead and up to
#: `R.SPOTLIGHT_MAX_OTHERS` more, largest first. The cluster is a lower bound ("at least N other insiders"), so a
#: sixth buyer is never needed to say it; a person the check drops leaves fewer, never another.
SPOTLIGHT_PEOPLE_PER_ISSUER = 1 + R.SPOTLIGHT_MAX_OTHERS
#: The insider spotlight's per-PERSON Form 4/A check (`_spotlight_checked`, the cluster variant of
#: `_amendment_checked`: keyed (symbol, reporter), every person of an issuer checked, one memoized per-issuer read
#: per issuer): at most this many issuers (the main lane's cap: `INSIDER_AMENDMENT_MAX_SYMBOLS` per-issuer reads)
#: and this many people in all — at least `R.INSIDER_MAX_ROWS` × (1 + `R.SPOTLIGHT_MAX_OTHERS`), pinned.
SPOTLIGHT_AMENDMENT_MAX_ISSUERS = INSIDER_AMENDMENT_MAX_SYMBOLS
SPOTLIGHT_AMENDMENT_MAX_PEOPLE = SPOTLIGHT_AMENDMENT_MAX_ISSUERS * SPOTLIGHT_PEOPLE_PER_ISSUER
#: The 13F spotlight reads at most this many filings beyond its record limit (each filer yields at most ONE
#: holding, and a filing may yield none: every more / fewer move under 25%, split-restated or withheld).
THIRTEEN_F_SPOTLIGHT_SPARE_FILINGS = 2
#: `company_facts` (workstream C's art brief): one profile read's timeout when the memo does not hold it.
COMPANY_FACTS_TIMEOUT_SECONDS = 8.0

#: Registry 13F builds per call that actually hit FMP (a memo hit is free; a refused book —
#: `FilingRefused` — does not count).
MAX_LIVE_13F_BUILDS = 2
#: This many `FilingUnavailable` (or failed date lists) in one call → the series is unavailable.
THIRTEEN_F_MAX_UNAVAILABLE = 2
#: Moves presented (and profiled) per filing before the final cut to `THIRTEEN_F_MAX_MOVES`. Both cuts are by dollar
#: size alone (`_cut`), in both 13F series — review 2026-11-20: the roundup used to reserve each kind's three largest
#: (`_select_moves`, review round 8, removed), so a filing with all four kinds always kept the top two of EACH — a $70M
#: new holding drawn where $3B, $2B and $1B decreases were in no picture and no caption — while the one list design
#: (news-v23) ranks the largest dollar change first.
THIRTEEN_F_MAX_PROFILED = 10
#: An increase / decrease smaller than this share of the previous count is not shown.
THIRTEEN_F_MIN_CHANGE = 0.10
#: A newly reported or no-longer-reported move is shown only when its value reaches
#: max(`THIRTEEN_F_MATERIAL_MIN_USD`, `THIRTEEN_F_MATERIAL_SHARE` × the filer's reported total)
#: (review round 7, live L1: Berkshire's 2026-Q2 13F led with 3,564 D.R. Horton shares, $580,504
#: in a $299B book). An exit is valued on the PREVIOUS quarter's book (a registry filer's
#: previous extract; a club filer's stored previous-quarter build); an exit whose previous value
#: is unknown is below the floor — fail closed.
THIRTEEN_F_MATERIAL_MIN_USD = 5_000_000.0
THIRTEEN_F_MATERIAL_SHARE = 0.0005
#: The review of 2026-10-16 (F1): EVERY increased / decreased row of a filing — not only the moves shown, since
#: the hook, subtitle and "and N more" state the whole kind's count — is re-checked for a stock split or an
#: unnameable adjustment in its quarter (`_split_window_problem`, through the builder's own corporate-actions
#: seam, `_builder.split_windows`). The shared suspect filter only looks up a holding whose per-share price
#: moved 30% or more (a cost bound for the app's screens): a 5:4 split at a flat price, a 3:2 split after a 15%
#: rally or a 2:1 split after a 54% rally was never looked up, never restated, and published as "reported more
#: shares". A filing with more such rows (distinct symbols) than this shows no more / fewer shares at all
#: (`split_unchecked`; newly reported and exited moves stay). Real books are far below it: Berkshire's 2023-Q3 →
#: Q4 pair had 7 such rows in 42 positions, Alphabet's 2026-Q1 → Q2 had 4 in 28, NVIDIA / Amazon / AMD none. At
#: most this many derivations (two price-series fetches each, `split_windows` runs six at once) per filing on
#: a cold cache; a closed quarter's derivation is then stored for good, and the build's own suspect lookups
#: are cache hits.
THIRTEEN_F_MAX_SPLIT_CHECKS = 40
#: The re-review of 2026-10-19 (F13-QE-SPLIT): the split re-check's window ends this many calendar days AFTER the
#: quarter's end (`_split_window_problem`): a split paid on the last session is ex-dated the next business day, and
#: a filer that books on the payable date reports the post-split count. A week covers a long holiday weekend.
THIRTEEN_F_SPLIT_RECHECK_SLACK_DAYS = 7
#: The BUY-OUT CHECK of a closed-out 13F holding (news-v23, owner 2026-11-19 — "Closed out + buy-out check": a holding
#: whose company stopped trading during or after the quarter — acquired, delisted, merged — is DROPPED, never called
#: "closed out"; `_closed_out_checked`). Two licensed FMP sources ("3 Company Information"), both fail closed: the
#: profile's `isActivelyTrading` (read anyway, in the same batch) must be exactly True, and FMP's delisted-companies
#: list (newest `delistedDate` first, `get_delisted_companies`) must hold no row of the symbol dated on or after the
#: quarter's first day — read page by page until a page's oldest row is older than that day, at most
#: `CLOSED_OUT_DELISTED_MAX_PAGES` pages (the app's own window, `listing_status_service._WINDOW_PAGES`: ~8 months at
#: ~3 pages a month, enough for a quarter and its filing season), memoized `CLOSED_OUT_DELISTED_TTL_SECONDS` and shared
#: by both 13F series. A list that cannot be read that far back (a failed page, an empty or malformed one, the cap
#: reached, too little budget) drops every closed-out row of the filing with a WARNING — never a guess. Read only when a
#: filing still holds a closed-out row after its profiles.
CLOSED_OUT_DELISTED_PAGE_SIZE = 100
CLOSED_OUT_DELISTED_MAX_PAGES = 24
CLOSED_OUT_DELISTED_TTL_SECONDS = 6 * 3600.0
CLOSED_OUT_DELISTED_TIMEOUT_SECONDS = 30.0

#: Money Map, per call: candidates past the ledger check that ended in a record or in an
#: UPSTREAM gap (`_MONEY_MAP_UPSTREAM_GAPS`), the deterministic content refusals (a gross
#: segment stack, a thin breakdown, an excluded sector, a statement mismatch …) — counted apart
#: only while NO record is held, so a pool whose first seeds are always refused still reaches a
#: publishable company; once one is held the loop stops at `MONEY_MAP_MAX_ATTEMPTS` of all three
#: together (the old cap: more reads only risk the budget) — and breakdown reads in total. Out of
#: budget with a record held ends the loop with the records found (never discarded). A year whose
#: NEXT year is due (`money_map_next_year_due`, the review of 2026-10-16, MM-4) is a date fact, not
#: content: it never counts toward the content cap (eight December year-ends at the head of the pool
#: used to end the loop from about Jan 1 to March) — only toward the breakdown reads and, with a
#: record held, the total above.
MONEY_MAP_MAX_ATTEMPTS = 3
MONEY_MAP_MAX_CONTENT_REJECTIONS = 8
MONEY_MAP_MAX_LOOKUPS = 12
#: The review of 2026-10-16 (MM-4): each symbol's last-read fiscal year, its end and the run day that read it
#: — ``("money_map_fy_end", SYM) → (fy, period_end, checked_on)``, process-local like every memo entry —
#: written whenever `_money_map_record` reads the year's statement row. It does two things, neither of
#: which can publish anything: a breakdown still on a year the memo knows to be due (or stale) is refused
#: BEFORE the profit-power / facts / statement / profile fan-out (`_money_map_known_age`), and a symbol the
#: memo knows to be due is tried AFTER the rest of its group (never posted, then posted), least recently
#: checked first (`_money_map_order`) — so twelve December year-ends at the head of the pool no longer use
#: up every breakdown read before a September or May year-end is reached. A fiscal year's end never changes,
#: so the TTL only bounds how long the process trusts that the breakdown still serves that year: past it the
#: symbol is back in its normal place. Long on purpose — a symbol at the back is still read whenever the
#: front leaves reads to spare, and an entry learned in one run must not expire with eleven others on one
#: day and put them all back at the head together. A new process starts empty: its first run can still
#: spend its reads on due years (one run, then the memo carries them).
MONEY_MAP_FY_END_TTL_SECONDS = 400 * 24 * 3600.0
#: `_money_map_record` reasons that mean the data was not there (a degraded breakdown, profit
#: power or statement row, a facts miss) — the rest are content refusals.
_MONEY_MAP_UPSTREAM_GAPS = frozenset({"money_map_degraded", "profile_missing"})
#: B3PD-1 (2026-10-20): each symbol's LAST refusal — ``("money_map_refused", SYM) → (fy, reason, checked_on,
#: read_on)``, process-local like the year-end memo above, with the same TTL — written by every candidate the walk
#: reads that cannot publish: a content refusal (a gross stack, "mostly other", a statement mismatch, a non-USD
#: reporter, an excluded sector, a listing refusal …), an upstream gap (fy "" when the breakdown names no year),
#: an age refusal and `already_posted`; dropped when the symbol yields a record. Before, nothing remembered a
#: refusal: a never-posted seed refused for its current year kept its place at the head of the pool, every call
#: spent its content cap (`MONEY_MAP_MAX_CONTENT_REJECTIONS`) on the same refused symbols, and once the publishable
#: seeds ahead of them were posted the series never yielded again — the club and theme members behind them, and
#: every posted company's NEW fiscal year (a fresh 10-K), were never read. Now such a symbol is tried AFTER every
#: candidate that can publish (`_money_map_order`, least recently checked first, beside the due and posted years),
#: and a re-read of the SAME year is answered from the memo before the fan-out (`_money_map_known_refusal`: a
#: content refusal only — a gap is always read again, the data may be back) without counting toward the content
#: cap. The year changing lifts it on its own (the breakdown serves the new year), and so does
#: `MONEY_MAP_REFUSAL_RECHECK_DAYS`: past it the year is read in full again (a feed correction, a listing change).
MONEY_MAP_REFUSAL_RECHECK_DAYS = 28
#: Refusals a re-read of the same fiscal year never takes from the memo: an upstream gap (the data may be back),
#: an age refusal (the year-end memo answers those, from the date) and `already_posted` (the ledger answers it).
_MONEY_MAP_NOT_REMEMBERED = _MONEY_MAP_UPSTREAM_GAPS | {"money_map_stale", "money_map_next_year_due",
                                                       "already_posted"}
#: The round-2 review of 2026-10-20 (RR2-MM-TEMPLATE-WALL): the refusal the memo holds for a record its TEMPLATE
#: refused (`remember_template_refusal`, reported by the script flow) — a content refusal like the read's own, so a
#: re-read of the same fiscal year is answered from the memo and the symbol waits behind every candidate that can
#: publish. Before, only the read's refusals were remembered; a template refusal (a loss larger than revenue — now
#: also refused at the read, `money_map_loss_above_revenue` — or a segment named like a web address) left the symbol
#: at the head of its group, rebuilt and refused every day.
_TEMPLATE_REFUSED = "template_refused"
#: The fiscal year's statement must be dated within ~18 months of the run.
MONEY_MAP_STALE_DAYS = 548
#: Batch 1 (2026-10-15): a fiscal year that ended at least this many days before the run is refused
#: `money_map_next_year_due` — never an upstream attempt, and (the review of 2026-10-16, MM-4) never a
#: content refusal either: a date fact (`MONEY_MAP_MAX_CONTENT_REJECTIONS`). 364 days is 52 weeks, the
#: shortest a fiscal year runs, so by then the NEXT fiscal year has ended (a 53-week year ends a week
#: later: refused a week early, fail closed) and its annual report is due; a map of the year before it
#: is last year's map. Apple's fiscal 2025 (ended Sep 27, 2025) waits from Sep 26, 2026 for its fiscal
#: 2026 10-K; a December year end pauses from about Jan 1 until its 10-K. The breakdown serves the latest
#: year it can pair with a statement, so the refusal lifts on its own once the new year is filed.
MONEY_MAP_NEXT_YEAR_DUE_DAYS = 364
#: Statement-vs-breakdown agreement (revenue, net income), relative.
MONEY_MAP_STATEMENT_TOLERANCE = 0.005
#: |100·net/revenue − profit power's net margin| above this (points) → inconsistent.
MONEY_MAP_NET_MARGIN_TOLERANCE_PT = 0.5
#: Gross and operating profit are the income statement row's OWN `grossProfit` / `operatingIncome`
#: (review round 15), never a figure we derive from other lines (revenue − cost of sales, gross − FMP
#: `operatingExpenses`: that line can leave out a charge the statement's operating income includes, and
#: a derived bar 1 point of revenue off read 29% high for a 3%-margin company). They are NOT "as
#: reported" by the company, though (Batch 1, 2026-10-15): FMP fills `grossProfit` as revenue minus cost
#: of revenue for an issuer whose statement has no gross subtotal (Amazon reports none), so the post says
#: "annual results" and "Figures come from the company's annual financial statements", and explains gross
#: profit as revenue minus the cost of sales — never "as reported". Each line is cross-checked against
#: profit power's margin for the same year — the same statement line, rounded to 0.01 point there —
#: within this many points: rounding, never judgment. A line that misses it, or is missing or zero (FMP's
#: fill for a line it does not have), or larger than what it is carved from, is None: its bar and
#: sentence are omitted.
MONEY_MAP_BAR_TOLERANCE_PT = 0.05
#: A map whose "Other" (unnamed and folded revenue) is larger than its largest named segment, or
#: more than this share of revenue, explains too little of the company (review round 7, live L2:
#: Microsoft FY2026 drew "Other" at $143.7B, 43% of revenue, above its largest segment) —
#: refused `money_map_mostly_other`, a content refusal.
MONEY_MAP_MAX_OTHER_SHARE = 0.30
_MONEY_MAP_REST_NAMES = frozenset({"other", "unallocated", "interest paid and other"})
_SEGMENT_NAME_RE = re.compile(r"[A-Za-z0-9 &.,'+/-]{2,40}")

# ── Drop 2b bounds (each series' FMP / service calls per `candidates()` call) ───
#
# congress_count: 2 chambers × 8 pages (2000 rows); a chamber that walk did not cover is re-read
#   at 30 pages (7500); then EVERY chamber's covering pages are read once more (the confirming
#   read, at the size that covered it); + the profile "batch", which is one call per symbol (≤
#   `CONGRESS_PROFILE_CANDIDATES`). Normally 2×8 + 2×8 = 32 page requests + ≤ 10 profile calls;
#   at most 2×8 + 2×30 + 2×30 = 136 page requests + ≤ 10 profile calls = 146 (review round 9:
#   the old "107" left out the confirming reads at 7500 — pinned by a page-counting test).
# company_stakes: 0 FMP calls for data (the club group, its rows and one stakes read), + 1
#   profile batch for at most `STAKES_PROFILE_CANDIDATES` stakes' investor and investee (news-v23: the
#   stakes with a listed U.S. symbol and a kind that can be public — the rest are dropped before it).
# earnings: 7 one-day calendar calls (4 at a time, all-or-nothing) + ONE profile pass over the first
#   `EARNINGS_MAX_PROFILES` reports by revenue estimate (60 symbols: the client's batches of 50, so 2
#   calls) + one quarterly income-statement read (`EARNINGS_STATEMENT_ROWS` quarters: the reporting
#   currency and the reported quarter) per report that reaches the read stage, in market-cap order
#   (≤ `EARNINGS_MAX_STATEMENT_READS`; Batch 1, 2026-10-15).
# theme_explainer: 1 themes read; per theme evaluated (≤ `THEME_MAX_EVALUATED`) 1 profile batch; then at most
#   `THEME_MAX_ANGLE_ATTEMPTS` angle evaluations in all — "how they make money" EVERY member's revenue breakdown
#   (≤ `THEME_SEGMENT_LOOKUPS`; the service's own two-tier cache first, memoised per symbol), an older-years read
#   (`get_segment_years`: the card's own two licensed feeds) for each member whose newest stack is gross, and the ONE
#   market-capitalization batch "biggest" reads too (memoised per member set: the tile order); "biggest" that batch.
#   Worst case: 2 profile batches + 2 × 24 breakdowns (two waves of 12 each) + their older-years reads + 1 batch.

#: The two walk sizes of the Congress feeds (rows per chamber; the client fetches 250-row pages
#: in parallel, at most 30). The 2026-10-09 probe: Senate 500 rows ≈ 2.5 months, House 500 rows ≈
#: 1 month — so 2000 covers a disclosure month in the normal case and 7500 is the one retry.
CONGRESS_WALK_LIMITS: Tuple[int, ...] = (2000, 7500)
#: A chamber's walk covers the disclosure month only when its LAST row (the feed is sorted by
#: disclosure date, newest first — verified 2026-10-09, and re-checked on every read) is dated
#: more than this many days before the month's first day.
CONGRESS_COVERAGE_MARGIN_DAYS = 7
#: The month is counted only once its last day is at least this many days behind the run.
CONGRESS_DUE_DAYS = 7
#: Symbols (members desc, then symbol) that reach the profile stage per call.
CONGRESS_PROFILE_CANDIDATES = 10
#: The aggregated counts (never a row, never an identity) are memoized this long.
CONGRESS_TTL_SECONDS = 30 * 60.0
_CONGRESS_CHAMBERS: Tuple[Tuple[str, str], ...] = (
    ("senate", "get_senate_latest"), ("house", "get_house_latest"))
#: Member identity — dropped at the source (main-session decision 3, 2026-10-09): `_scrub_congress`
#: keeps only salted, per-call hashes of these, and those never leave `_congress_month`.
CONGRESS_IDENTITY_FIELDS: FrozenSet[str] = frozenset({
    "firstName", "lastName", "first_name", "last_name", "office", "senator", "representative",
    "district", "owner", "link", "party", "state", "comment", "senateID",
})
_IDENT_SUFFIXES = frozenset({"jr", "sr", "ii", "iii", "iv", "v"})
_IDENT_WORD_RE = re.compile(r"[a-z0-9]+")
_IDENT_MAX = 200
#: An asset description is kept (folded, capped) only to find an in-month purchase of the same
#: company under another or no symbol.
_DESC_MAX = 200
#: Congress asset types that are clearly NOT a share of stock: such a purchase is simply not counted.
#: Any OTHER non-"stock" type ("", "Other", "Other Securities", "REIT" …) on a symbol makes that
#: symbol's count uncertain — the row may be the same stock — so the symbol is refused.
_CONGRESS_NON_STOCK_ASSETS = frozenset({
    "stock option", "corporate bond", "municipal security", "government securities", "mutual fund",
    "etf", "cryptocurrency", "non-public stock",
})
#: A "stock" row whose description names another instrument (FMP's asset type is the filer's
#: box, not a check): the symbol's count is uncertain. Accepted over-block: "Option Care Health".
_NON_COMMON_DESC_RE = re.compile(
    r"\b(?:options?|calls?|puts?|warrants?|preferred|pfd|notes?|bonds?|debentures?|units?|rights?)\b")
#: Words a company name shares with too many descriptions to identify it.
_GENERIC_NAME_WORDS = frozenset({
    "the", "and", "of", "inc", "corp", "corporation", "co", "company", "companies", "holdings",
    "holding", "group", "ltd", "limited", "plc", "class", "common", "stock", "shares",
})

#: Earnings: report days ``[run_date - 7, run_date - 1]``, one ET day per calendar call — eight on a Thursday since
#: news-v23 phase 3 (`R.earnings_window_days`: Wednesday posts the weekly theme first, and Thursday's window takes every
#: report day Wednesday's used to cover).
EARNINGS_WINDOW_DAYS = R.EARNINGS_WINDOW_DAYS
#: The re-review of 2026-10-19 (EME-1): a posted earnings key of a listing counts as THIS report also when its day is
#: up to this many days BEFORE the window (two windows): FMP re-dating a reported row across a window boundary
#: must not post one report twice. Far below the shortest gap between two quarterly reports of one issuer (a
#: quarter is 77+ days, `R.EARNINGS_QUARTER_SPAN_DAYS`; the counted keys span at most 14 + 6 = 20 days), so the
#: next quarter's report is never taken for this one.
EARNINGS_POSTED_LOOKBACK_DAYS = 14
#: FMP's silent row cap: a one-day answer this long is probably TRUNCATED → the series is
#: unavailable (`earnings_window_service` only logs it; a post must not rank a cut list).
EARNINGS_TRUNCATION_ROWS = 4000
EARNINGS_DAY_TTL_SECONDS = 30 * 60.0
#: Batch 1 (2026-10-15): the reports profiled per call — the first by REVENUE ESTIMATE (the size proxy
#: before any profile is read), all at once: two profile batch calls of 50. A report past it is never
#: profiled (logged) and never counted as a rejection: it was not refused, only not reached. Review round
#: 9's batch walk (20 at a time, ranked by the widest gap) is gone with the gap ranking.
EARNINGS_MAX_PROFILES = 60
#: Quarterly income-statement reads per call (one per report that reaches the read stage, in market-cap
#: order, in waves of the reports still needed). The read settles two things: the REPORTING currency
#: (review round 9: the profile's ``currency`` is the TRADING currency, and a USD-traded ordinary share —
#: RY, TD, RACE, SPOT … — reports EPS and revenue in CAD / EUR, figures the template would print with "$")
#: and, since Batch 1, the quarter the figures are for (`company_news_rules.earnings_period`).
EARNINGS_MAX_STATEMENT_READS = 20
#: The rows of that read (FMP ``limit``): the latest quarters, the reported one and those before it — the one
#: before is what shows the reported row is a QUARTER (review of 2026-10-16, `R.EARNINGS_QUARTER_SPAN_DAYS`),
#: and the reported row's diluted EPS what shows the actual and estimate share a basis (`R.earnings_basis_mixed`).
EARNINGS_STATEMENT_ROWS = 4
#: |actual − estimate| above this multiple of |estimate| is a feed error, not a result.
EARNINGS_MAX_GAP_MULTIPLE = 10.0
#: Review of 2026-10-16 (live preview F3): NASDAQ fifth letters that name a share CLASS — A / B / C, J (voting),
#: K (non-voting): a 5-letter symbol ending in one (LBRDA / LBRDK, FWONA / FWONK) is a class of its own, never
#: dropped as the twin of a 4-letter root; the issuer dedupe after the profiles keeps one listing of the issuer.
#: Any OTHER fifth letter beside a 4-letter root listed in the same window (but `EARNINGS_FOREIGN_LETTERS`) is that
#: root's twin line — a preferred or depositary series (FITBI, HBANL, AGNCL, WAFDP: the issuer's plain name on the
#: profile, its own calendar row, an FMP market cap priced on the common's share count) or Alphabet's GOOGL beside
#: GOOG (one company: GOOG kept; `EARNINGS_CLASS_LINES`) — and is never a candidate while its root reported.
EARNINGS_CLASS_LETTERS: FrozenSet[str] = frozenset("ABCJK")
#: The re-review of 2026-10-19 (B1R-ER3): fifth letters of an OTC FOREIGN line — F (a foreign ordinary share), Y (an
#: ADR) — never make a twin. Such a line is often ANOTHER company entirely (live, 2026: CMPRF is Gentera beside
#: Cimpress' CMPR; RAMPF, TARSF, INTAF, ECVTF, STMEF the same), and the twin rule refused the root whenever the two
#: rows differed. It stands on its own: the profile gate refuses an OTC or ADR line, and one that is the root's own
#: issuer and passes the gate is caught by the issuer dedupe (`_one_listing_per_issuer`).
EARNINGS_FOREIGN_LETTERS: FrozenSet[str] = frozenset("FY")
#: The re-review of 2026-10-19 (EME-3): CURATED class shares whose fifth letter is not a class letter — Alphabet's
#: Class A GOOGL beside its Class C GOOG (the "L" of HBANL / AGNCL is a preferred line). Still a member of its root's
#: family: dropped like a twin while the root has a REPORTED row in the window (the root is the one record, and the
#: family's rows are checked with it), but it STANDS FOR the issuer when the root has none (FMP filled GOOGL's
#: actual before GOOG's: the week's largest reporter was lost) — the family check still runs over every other
#: reported row of the family.
EARNINGS_CLASS_LINES: FrozenSet[str] = frozenset({"GOOGL"})

#: Company stakes: the stakes read's row cap (the club service's `_MAX_STAKE_ROWS`); a full page
#: may be cut, so it is unavailable, never a partial catalogue.
STAKES_READ_LIMIT = 2000
#: Unposted stakes (rank order) whose investor and investee reach the profile stage per call — news-v23: only the stakes
#: with a listed U.S. symbol and a kind that can be public (the rest are dropped before it), and enough of them to pick
#: the investor with the most public stakes and fill its post (`R.STAKES_POST_STAKES`, 4). One batch: at most twice
#: this many symbols (fewer: the investors repeat), each memoized `PROFILE_TTL_SECONDS`.
STAKES_PROFILE_CANDIDATES = 12
#: The round-3 review of 2026-10-20 (RR3-STAKES-TEMPLATE-WALL): how long the memo keeps a stake its TEMPLATE refused
#: (`remember_template_refusal` → ``("stake_template_refused", stake_id) → (fingerprint, refused_on)``), process-local
#: like every memo entry. While it holds — and the row still says what was refused (`_stake_fingerprint`) — the stake
#: is offered AFTER every other candidate (`_collect_company_stakes`), never left out: whatever the template refused
#: may pass another day. Long, as `MONEY_MAP_FY_END_TTL_SECONDS` is and for its reason: entries learned on one day
#: must not all expire together and put that day's refused stakes back at the head of the walk at once.
STAKE_TEMPLATE_REFUSAL_TTL_SECONDS = 400 * 24 * 3600.0
_STAKES_TABLE = "trillion_club_stakes"
#: Explicit columns: the club's wire shape lacks ``id`` (the ledger key) and ``created_at`` (new
#: vs catalogue); ``source_url`` is read for `stake_problem` and never leaves this module, and
#: neither do ``origin`` / ``owner_kept_at`` (migration 205), read only by `_stake_left_out`.
_STAKE_COLUMNS = (
    "id,created_at,company_slug,kind,investee_name,investee_us_symbol,local_listing,ownership_pct,"
    "ownership_basis,disclosed_value_usd,value_basis,as_of,source_title,source_url,"
    "source_confidence,published,listed_since,background,verified_on,sort_order,origin,owner_kept_at"
)
#: The Trillion Club stake finder (migration 205, 2026-10-28) writes stake rows itself, and ``origin``
#: says who made each one: ``seed`` (hand-kept, `scripts/seed_trillion_club.py`), ``proposal`` (built
#: by the finder, published by the owner's Publish tap) or ``auto`` (published by the finder with no
#: tap — the owner gets a Withdraw / Keep note). The in-app club screen shows every published row; a
#: PUBLIC post is built only from a row a person reviewed: a seed or proposal row, or an auto row the
#: owner KEPT (``owner_kept_at``). Mirrors `trillion_club.stake_contracts.ORIGIN_*` (pinned equal by
#: the adapter tests: the adapter may not import that module, `tests/test_marketing_import_boundary.py`).
_STAKE_ORIGINS_POSTABLE: Tuple[str, ...] = ("seed", "proposal")
_STAKE_ORIGIN_AUTO = "auto"
#: The same rule as the read's PostgREST filter (AND-ed with the others); `_stake_left_out` re-checks
#: every row it returns, strictly, before anything else reads them.
_STAKE_POSTABLE_FILTER = "origin.in.(seed,proposal),owner_kept_at.not.is.null"

#: Theme explainer — news-v23 phase 3, the WEEKLY theme post (owner 2026-11-19, RESTYLE_SPEC §2 item 7): the active
#: themes rotate oldest-posted first (`_theme_order`), each theme's next ANGLE after its last posted one
#: (`R.next_theme_angle`). Bounded per call: at most THEME_MAX_EVALUATED themes past the ledger and the cheap gates
#: (each costs one profile batch), and at most THEME_MAX_ANGLE_ATTEMPTS angle evaluations that READ (an angle not built,
#: or one the run's worker cannot draw, is skipped without a read and never counts). Each angle's reads are memoised
#: (`_shared`): the breakdowns per symbol, the market values per member set, the annual statements per symbol, the Form 4
#: reads per issuer, the Congress month's walk (shared with congress_count).
#: 2026-11-20 (every angle built): FIVE attempts — one whole rotation of the first theme, so a theme whose next angles
#: are thin (a Congress month of fewer than four companies is common) still reaches the angle after them in the same
#: call ("≥ 4 companies with data, else the next angle"); at three, a theme past "biggest" whose growth, insider and
#: Congress angles were all thin never reached "how they make money" and the whole series skipped. Worst reads of one
#: call: 16 statements + 16 issuers × THEME_INSIDER_MAX_PAGES + one memoised Congress walk + 24 breakdowns (and the
#: older-years reads of the gross ones) + one market value batch — so each angle runs under its OWN share of the time
#: (THEME_ANGLE_BUDGET_SECONDS, below).
THEME_MAX_EVALUATED = 2
THEME_MAX_ANGLE_ATTEMPTS = 5
#: Fix round 2026-11-20 — each angle's own time, inside the series' (`script_service.SERIES_BUDGET_SECONDS`, 120 s): an
#: angle whose reads outlast it loses ITS turn only (an outage: the next angle is tried, and with no record at all the
#: series reports "unavailable", never "nothing qualified"), so one slow source can no longer spend the whole series'
#: time before the angle after it runs (before: one `ctx.step` per angle with ALL the time left — a slow insider walk
#: followed by a slow Congress walk ended the series `budget_exhausted` with nothing posted, and next Wednesday repeated
#: it). Sized from the measured worst ROUND (`THEME_WORST_ROUND_SECONDS`, the FMP client's own 5xx retries included —
#: script_service's notes: ~47 s for seven rounds): make_money three rounds (phase 4, 2026-11-20: two waves of 12
#: breakdowns — every member of the largest theme — then one wave of older-years reads for the members whose newest
#: stack is gross, the market-value batch beside the first wave), biggest one batch,
#: growth_profit two waves of 8 statements, insiders four waves of 4 issuers at one page each (an issuer list past one
#: page is cut: the 12-round three-page worst would take ~80 s), Congress the shared walk's ~30 s worst. Their sum plus
#: THEME_MAX_EVALUATED profile batches is the call's worst (`theme_worst_seconds`), pinned below the series' cap by
#: tests/test_marketing_news_series_switch.py.
THEME_WORST_ROUND_SECONDS = 6.7
THEME_ANGLE_BUDGET_SECONDS: Dict[str, float] = {
    "make_money": 21.0, "biggest": 8.0, "growth_profit": 16.0, "insiders": 28.0, "congress": 32.0,
}


def theme_worst_seconds() -> float:
    """One `candidates("theme_explainer")` call at its worst BEFORE it holds a record: THEME_MAX_EVALUATED profile
    batches (one round each) and the five angles' own shares once each. A theme's rotation tries each angle at most
    once, and the THEME_MAX_ANGLE_ATTEMPTS (5) attempts are the call's: a second theme only tries what the first left,
    and only when fewer angles are on offer (class C off: three; an older worker: one), so its repeats are of those
    cheaper angles (phase 4: ≤ 82 s and ≤ 42 s). After a record, the series' own deadline ends the call with what it holds."""
    return THEME_MAX_EVALUATED * THEME_WORST_ROUND_SECONDS + sum(THEME_ANGLE_BUDGET_SECONDS.values())
#: "How they make money" (phase 4, 2026-11-20 — the owner's 2026-11-19 "for AI CHIPS, i don't see ARM, Intel and SNPS?
#: add for me"): EVERY member's largest segment is looked up — as many as a theme record holds (`R.THEME_MEMBERS`' 24;
#: the largest production theme, Silicon Rush, has 21: with the old first-16-in-list-order Intel, its 20th, was never
#: read) — THEME_SEGMENT_CONCURRENCY in flight, so two waves; the reads start in MARKET-VALUE order (`_theme_read_
#: priority`), so a share cut short leaves only the smallest members unread. A member whose newest stack is gross
#: (intersegment sales) gets one older-years read (THEME_SEGMENT_YEARS_CONCURRENCY in flight, apart from the
#: breakdowns): `_walked_back_fact`. The tiles are the members with a fact, the largest market value first.
THEME_SEGMENT_LOOKUPS = R.THEME_MEMBERS[1]
#: The members whose segment fact the record CARRIES: the picture's tiles (`template_onscreen.MAX_TILES`, pinned equal by
#: tests/test_marketing_company_news_adapter.py) — the largest by market value among those with one. The others are
#: listed without it ("{co} is also in {theme}." in the captions, as an unread member always was): every member's fact
#: in the long captions overflowed Instagram, YouTube and Threads on Silicon Rush (18 facts; 12 keep every outlet).
THEME_FACT_TILES = 12
THEME_SEGMENT_CONCURRENCY = 12
THEME_SEGMENT_YEARS_CONCURRENCY = 4
#: Angle 3's annual statements (and its in-flight bound): THEME_LOOKUP_CONCURRENCY at a time.
THEME_LOOKUP_CONCURRENCY = 8
#: The insider spotlight's segment fact: its fiscal year this recent (years before the run's). A THEME member's fact may
#: be older — up to `R.THEME_TAGGED_FACT_MAX_AGE_YEARS`, drawn with its own "FY…" tag (`news_templates`).
THEME_FACT_MAX_AGE_YEARS = 2
#: Segments may add up to at most this much more than revenue for a share to be stated.
THEME_SEGMENT_SUM_TOLERANCE = 0.005
#: The memo of one member's revenue breakdown (an angle's reads re-asked in a later build of the day are answered
#: here; the service keeps its own two-tier cache behind it) and of one member set's market values.
THEME_BREAKDOWN_TTL_SECONDS = 30 * 60.0
THEME_MARKET_VALUE_TTL_SECONDS = 10 * 60.0
#: Angle 3 "growth + profit" (news-v23 phase 3, 2026-11-20): the members whose ANNUAL income statements are read (theme
#: order) — the picture's 10 rows and spares, THEME_LOOKUP_CONCURRENCY in flight, ONE licensed `income-statement` call
#: each (the two latest years and a spare), memoised THEME_STATEMENT_TTL_SECONDS per symbol.
THEME_STATEMENT_LOOKUPS = 16
THEME_STATEMENT_ROWS = 3
THEME_STATEMENT_TTL_SECONDS = 30 * 60.0
#: Angle 4 "insider scorecard": the members whose Form 4 rows are read (theme order) — each one per-ISSUER read of the
#: licensed `insider-trading/search` (every code, every class, by the profile's issuer CIK), at most
#: THEME_INSIDER_MAX_PAGES pages of THEME_INSIDER_PAGE_SIZE rows (an issuer whose 90 days run past them is LEFT OUT —
#: `FMPPartialPageException`, never a partial total), THEME_INSIDER_CONCURRENCY in flight, memoised like the Form 4
#: series' reads (INSIDER_RAW_TTL_SECONDS). FMP calls per angle attempt: ≤ THEME_INSIDER_LOOKUPS ×
#: THEME_INSIDER_MAX_PAGES.
THEME_INSIDER_LOOKUPS = 16
THEME_INSIDER_PAGE_SIZE = 1000
THEME_INSIDER_MAX_PAGES = 3
THEME_INSIDER_CONCURRENCY = 4
#: The theme columns read. Never `updated_at` (owner decision 2026-11-20, "Trust the app's lists"): a list with no
#: `tickers_as_of` of its own is the app's live list and counts as current (`_theme_as_of`); the row's last write
#: neither gates nor dates it.
_THEME_COLUMNS = "slug,title,tickers,blocked_tickers,tickers_as_of,sort_order,is_active"
_THEME_SLUG_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,62}")

#: The ONLY logo URL `fetch_logo` will request, built from the validated symbol and compared
#: byte for byte with the profile's ``image``.
LOGO_URL_TEMPLATE = "https://images.financialmodelingprep.com/symbol/{symbol}.png"

_CLUB_COMPANIES_TABLE = "trillion_club_companies"
_CLUB_FILINGS_TABLE = "trillion_club_filings"
_WHALES_TABLE = "whales"
_THEMES_TABLE = "trending_themes"
#: `whales.lifecycle_status` values of a filer that is still read. The column is curated as ""
#: or "inactive" (migration 145; `sync_whale_registry.py`), so anything else — "inactive",
#: "dormant", a value added later — is skipped, never read (fail closed).
_ACTIVE_LIFECYCLE = frozenset({"", "active"})
#: Logo bytes as they came off the wire, never decoded: a compressed body inflates past any
#: cap before a decoded-size check could refuse it.
_IDENTITY_ENCODINGS = frozenset({"", "identity"})

# ── public types ──────────────────────────────────────────────────────────────


class MarketingNewsUnavailable(Exception):
    """An upstream failure (or budget exhaustion, or an unexpected error) while collecting one
    series — never "nothing qualified". ``reason`` ∈ `company_news_rules.UNAVAILABLE_REASONS`.

    ``reason`` defaults to ``internal_error`` only so the class is constructible from a single
    argument (the classifier walk in `tests/test_marketing_script_flow.py` builds ``cls("x")``);
    every raise in this module names its reason. ``detail`` is scrubbed of URLs."""

    def __init__(self, series: str, reason: str = "internal_error", detail: str = "") -> None:
        if reason not in R.UNAVAILABLE_REASONS:
            raise ValueError(f"unknown unavailable reason {reason!r}")
        self.series = str(series)
        self.reason = reason
        self.detail = _scrub(detail)
        super().__init__(f"{self.series}: {reason}" + (f" ({self.detail})" if self.detail else ""))


@dataclass(frozen=True)
class Candidates:
    """One series' answer. ``records`` is at most ``limit`` long, in rank order; empty only
    with a `SKIP_REASONS` ``skip_reason``. ``rejections`` counts per-candidate refusals.

    ``checked_window`` (C0, 2026-11-16; the insider spotlight only, None for every other series): the Form 4 window
    ``(start, end)`` the collector CHECKED — whether a record qualified or not. The script flow records it on the
    day's selection trail, and the next insider spotlight's window starts the day after its end."""

    series: str
    records: Tuple[Any, ...]
    skip_reason: Optional[str]
    rejections: Mapping[str, int]
    checked_window: Optional[Tuple[date, date]] = None

    def __post_init__(self) -> None:
        if self.checked_window is not None:
            w = self.checked_window
            if not (isinstance(w, tuple) and len(w) == 2
                    and all(isinstance(d, date) and not isinstance(d, datetime) for d in w) and w[0] <= w[1]):
                raise ValueError(f"Candidates.checked_window must be a (start, end) pair of dates, got {w!r:.80}")
        if not isinstance(self.records, tuple) or any(not isinstance(r, R.RECORD_TYPES) for r in self.records):
            raise ValueError("Candidates.records must be a tuple of news records")
        if self.records and self.skip_reason is not None:
            raise ValueError("Candidates with records carry no skip_reason")
        if not self.records and self.skip_reason not in R.SKIP_REASONS:
            raise ValueError(f"empty Candidates need a SKIP_REASONS code, got {self.skip_reason!r}")
        bad = sorted(k for k in self.rejections if k not in R.REJECTION_REASONS)
        if bad:
            raise ValueError(f"unknown rejection reason(s) {bad}")
        if any(not isinstance(v, int) or isinstance(v, bool) or v < 0 for v in self.rejections.values()):
            raise ValueError("rejection counts must be non-negative ints")


@dataclass(frozen=True)
class NewsDeps:
    """Injection seam (tests); None → the production singleton, resolved only when a series
    needs it. ``facts`` is a ``get_company_facts``-shaped coroutine function; ``sb`` a Supabase
    client. Beyond the contract: ``actions`` (the corporate-actions primitive the 13F builder
    uses for splits; None → the builder's own default) and ``http`` (an httpx transport for
    `fetch_logo`; None → the network)."""

    fmp: Any = None
    club: Any = None
    revenue: Any = None
    profit: Any = None
    facts: Optional[Callable[..., Awaitable[Dict[str, Any]]]] = None
    sb: Any = None
    monotonic: Callable[[], float] = time.monotonic
    actions: Any = None
    http: Any = None


# ── helpers: scrubbing, numbers, dates ────────────────────────────────────────

_URL_RE = re.compile(r"(?:https?|wss?)://\S+", re.IGNORECASE)
_KEY_RE = re.compile(r"(?i)(api[_-]?key|token|secret)=\S+")


def _scrub(text: Any, limit: int = 300) -> str:
    """One line, no URL, no key=value secret, length-capped — safe for a log or an exception."""
    s = _URL_RE.sub("<url>", str(text or ""))
    s = _KEY_RE.sub(r"\1=<redacted>", s)
    s = " ".join(s.split())
    return s if len(s) <= limit else s[: limit - 1] + "…"


def _describe(e: BaseException) -> str:
    return _scrub(f"{type(e).__name__}: {e}")


def _num(v: Any) -> Optional[float]:
    """A finite non-bool number as float, else None (strings are not numbers here, and neither is an int
    too large for a float — a malformed JSON figure must refuse its row, never raise for the series)."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    try:
        f = float(v)
    except OverflowError:
        return None
    return f if math.isfinite(f) else None


def _day(v: Any) -> Optional[date]:
    """The ``YYYY-MM-DD`` prefix of a str (or a date) as a date, else None."""
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    if isinstance(v, str) and len(v) >= 10:
        try:
            return date.fromisoformat(v[:10])
        except ValueError:
            return None
    return None


def _sym_key(raw: Any) -> str:
    """Upper-case, "." → "-": the join key between FMP 13F symbols and our canonical form."""
    return str(raw or "").strip().upper().replace(".", "-")


# ── the memo + in-flight dedup ────────────────────────────────────────────────

_MISS = object()
_MEMO: Dict[Tuple[Any, ...], Tuple[float, Any]] = {}
_INFLIGHT: Dict[Tuple[Any, ...], "asyncio.Future[Any]"] = {}
#: Leader tasks keep running when a caller's deadline expires (the next call reads the memo);
#: held here so they are not garbage-collected mid-flight.
_LEADS: Set["asyncio.Task[Any]"] = set()


def _memo_get(key: Tuple[Any, ...]) -> Any:
    hit = _MEMO.get(key)
    if hit is None:
        return _MISS
    expires, value = hit
    if time.monotonic() >= expires:
        _MEMO.pop(key, None)
        return _MISS
    return value


def _memo_put(key: Tuple[Any, ...], value: Any, ttl: float) -> None:
    if ttl <= 0:
        return
    now = time.monotonic()
    if len(_MEMO) >= _MEMO_MAX_ENTRIES:
        for k in [k for k, (exp, _v) in _MEMO.items() if exp <= now]:
            _MEMO.pop(k, None)
        while len(_MEMO) >= _MEMO_MAX_ENTRIES:
            _MEMO.pop(min(_MEMO, key=lambda k: _MEMO[k][0]), None)
    _MEMO[key] = (now + ttl, value)


def clear_memo() -> None:
    """Drop every memoized value and in-flight future (tests; never needed in production)."""
    _MEMO.clear()
    _INFLIGHT.clear()


async def _shared(key: Tuple[Any, ...], ttl: float, fetch: Callable[[], Awaitable[Any]], *,
                  keep: Callable[[Any], bool] = lambda _v: True) -> Any:
    """Memo hit, else join the in-flight fetch for ``key``, else lead it. The leader runs as its
    own task, so a caller whose deadline expires (``wait_for`` cancels it) leaves the fetch
    running and the next call finds it memoized. A result is memoized only when ``keep`` says
    so; an exception never is."""
    hit = _memo_get(key)
    if hit is not _MISS:
        return hit
    loop = asyncio.get_running_loop()
    fut = _INFLIGHT.get(key)
    if fut is None or fut.get_loop() is not loop:
        fut = loop.create_future()
        _INFLIGHT[key] = fut
        task = loop.create_task(_lead(key, ttl, fetch, fut, keep), name=f"marketing_news:{key[0]}")
        _LEADS.add(task)
        task.add_done_callback(_LEADS.discard)
    return await asyncio.shield(fut)


async def _lead(key: Tuple[Any, ...], ttl: float, fetch: Callable[[], Awaitable[Any]],
                fut: "asyncio.Future[Any]", keep: Callable[[Any], bool]) -> None:
    try:
        value = await fetch()
    except asyncio.CancelledError:
        fail_shared_future(fut, RuntimeError(f"company news fetch {key[0]!r} was cancelled"))
        raise
    except Exception as e:  # noqa: BLE001 — handed to every joiner; never memoized
        fail_shared_future(fut, e)
    else:
        if keep(value):
            _memo_put(key, value, ttl)
        if not fut.done():
            fut.set_result(value)
    finally:
        if _INFLIGHT.get(key) is fut:
            del _INFLIGHT[key]


# ── the per-call context ──────────────────────────────────────────────────────


class _Ctx:
    """One `candidates()` call: its inputs, resolved dependencies, budget and counters.

    The spotlight lane's inputs (2026-11-15) are keyword-only with safe defaults, so every other series — the main
    lane's included — builds exactly the context it always did: ``spotlight_checked_through`` (the end of the last
    Form 4 window an earlier insider spotlight checked — C0, 2026-11-16 — the insider spotlight's window starts the day
    after) and ``spotlight_recent`` (``(symbol, display name)`` of every insider spotlight accepted within the issuer
    cooldown). ``checked_window`` is the window the insider spotlight checked this call (None until it has one); `done`
    hands it back on the answer. ``split_checked`` /
    ``split_restated_any`` are filled by the 13F split re-check as it runs (`_split_window_problem`), out of band:
    the 13F spotlight reads them (``split_restated``) and no record ever carries them; ``counts_partial`` is filled by
    the 13F walkers the same way (`_mark_counts_partial`), read by the 13F spotlight's ``filing_moves``."""

    def __init__(self, series: str, run_date: date, exclude: FrozenSet[str], limit: int,
                 deadline: float, deps: NewsDeps, *, spotlight_checked_through: Optional[date] = None,
                 spotlight_recent: Tuple[Tuple[str, str], ...] = (),
                 theme_history: FrozenSet[str] = frozenset(),
                 theme_angles: FrozenSet[str] = frozenset(R.THEME_ANGLES),
                 theme_classes: Optional[FrozenSet[str]] = None) -> None:
        self.series = series
        #: news-v23 phase 3 (the theme explainer only): the build's own theme ledger refs (THEME_HISTORY_DAYS back,
        #: beside `exclude`), the angles the run's worker can draw and the content classes switched on (None: every
        #: class — an angle of filings, `R.THEME_ANGLE_CLASSES`, runs only while its class is on).
        self.theme_history = theme_history
        self.theme_angles = theme_angles
        self.theme_classes = theme_classes
        self.run_date = run_date
        self.exclude = exclude
        self.limit = limit
        self.deadline = deadline
        self.deps = deps
        self.spotlight_checked_through = spotlight_checked_through
        self.spotlight_recent = spotlight_recent
        self.checked_window: Optional[Tuple[date, date]] = None
        self.rejections: Counter = Counter()
        self.considered = 0
        self.profiles_requested = 0
        self.profiles_returned = 0
        self._resolved: Dict[str, Any] = {}
        #: (filer CIK, period) → the canonical symbols of the more / fewer rows the 13F split re-check found a
        #: split for and passed as RESTATED by the build — written only when every such row of the filing was
        #: verified (a filing absent here never had its more / fewer rows verified).
        self.split_checked: Dict[Tuple[str, str], FrozenSet[str]] = {}
        #: The (filer CIK, period) whose restated rows cannot be tied to the record's moves by symbol (a club
        #: filer: its detail rows carry routable spellings, the stored rows the filed ones) — every more / fewer
        #: move of such a filing counts as restated (fail closed).
        self.split_restated_any: Set[Tuple[str, str]] = set()
        #: The (filer CIK, period) whose four change counts may NOT be every holding change the filing reports
        #: (review of the 13F list, 2026-11-19): a ``corporate_action`` row (a share count the diff could not
        #: separate from a split — "say so, never guess") or an unreadable count of them, an option / note row on
        #: either book (the builder diffs share rows only, so an option position that changed is counted nowhere;
        #: `_option_moves_dropped` also LOWERS a count), or — a club filer, whose stored builds keep no option row —
        #: any excluded row on either stored build, or an unreadable count of them. Written by the walkers, read by
        #: the 13F spotlight only (`_spotlight_holding`: its ``filing_moves`` is then None, so the list never states
        #: its counts as the filing's). No record carries it.
        self.counts_partial: Set[Tuple[str, str]] = set()

    # Production singletons are resolved lazily, so a series only touches what it reads.
    def _dep(self, name: str, factory: Callable[[], Any]) -> Any:
        if name not in self._resolved:
            given = getattr(self.deps, name)
            self._resolved[name] = given if given is not None else factory()
        return self._resolved[name]

    @property
    def fmp(self) -> Any:
        return self._dep("fmp", get_fmp_client)

    @property
    def club(self) -> Any:
        return self._dep("club", get_trillion_club_service)

    @property
    def revenue(self) -> Any:
        return self._dep("revenue", get_revenue_breakdown_service)

    @property
    def profit(self) -> Any:
        return self._dep("profit", get_profit_power_service)

    @property
    def facts(self) -> Callable[..., Awaitable[Dict[str, Any]]]:
        return self._dep("facts", lambda: get_company_facts)

    @property
    def sb(self) -> Any:
        return self._dep("sb", get_supabase)

    def reject(self, reason: str, n: int = 1) -> None:
        if reason not in R.REJECTION_REASONS:
            raise ValueError(f"unknown rejection reason {reason!r}")
        self.rejections[reason] += n

    def left(self) -> float:
        return self.deadline - self.deps.monotonic()

    async def step(self, stage: str, factory: Callable[[], Awaitable[Any]], *, reason: str,
                   passthrough: Tuple[type, ...] = ()) -> Any:
        """Await one upstream step inside the remaining budget. Less than `MIN_STEP_SECONDS`
        left, or the budget running out mid-step → ``budget_exhausted``. Any other exception →
        ``reason`` (an upstream failure), unless it is one of ``passthrough``."""
        left = self.left()
        if left < MIN_STEP_SECONDS:
            raise MarketingNewsUnavailable(self.series, "budget_exhausted",
                                           f"stage={stage} left={left:.1f}s")
        try:
            return await asyncio.wait_for(factory(), timeout=left)
        except MarketingNewsUnavailable as e:
            if e.series != self.series:      # raised for another series sharing the fetch
                raise MarketingNewsUnavailable(self.series, e.reason, e.detail) from e
            raise
        except asyncio.TimeoutError as e:
            raise MarketingNewsUnavailable(self.series, "budget_exhausted",
                                           f"stage={stage} timed out after {left:.1f}s") from e
        except asyncio.CancelledError:
            raise
        except passthrough:
            raise
        except Exception as e:  # noqa: BLE001 — an upstream failure, typed below
            raise MarketingNewsUnavailable(self.series, reason, f"stage={stage}; {_describe(e)}") from e

    def check_profiles(self) -> None:
        """Every requested profile missing → the profile source is down, not "nothing qualified"."""
        if self.profiles_requested and not self.profiles_returned:
            raise MarketingNewsUnavailable(
                self.series, "profiles_unavailable",
                f"0 of {self.profiles_requested} requested profile(s) returned")

    def done(self, records: Sequence[Any], skip_reason: Optional[str]) -> Candidates:
        records = tuple(records)[: self.limit]
        return Candidates(
            series=self.series,
            records=records,
            skip_reason=None if records else skip_reason,
            # A fresh plain dict per call (JSON-serialisable as is), sorted by reason.
            rejections=dict(sorted(self.rejections.items())),
            checked_window=self.checked_window,
        )


# ── shared fetchers ───────────────────────────────────────────────────────────


async def _fetch_profiles(fmp: Any, symbols: Sequence[str]) -> Dict[str, Dict[str, Any]]:
    """``{SYMBOL: profile}`` for the symbols FMP returned (batch of ≤ 50 per call); each is
    memoized on its own. The batch call itself swallows per-symbol failures (→ missing)."""
    want = list(dict.fromkeys(symbols))
    out: Dict[str, Dict[str, Any]] = {}
    for i in range(0, len(want), 50):
        chunk = want[i:i + 50]
        got = await fmp.get_company_profiles_batch(chunk)
        if not isinstance(got, list):
            raise FMPUnavailableException(f"profile batch returned {type(got).__name__}, not a list")
        for p in got:
            if not isinstance(p, dict):
                continue
            sym = R.canonical_symbol(p.get("symbol"))
            if sym in chunk and sym not in out:
                out[sym] = p
                _memo_put(("profile", sym), p, PROFILE_TTL_SECONDS)
    return out


async def _profiles(ctx: _Ctx, symbols: Sequence[str]) -> Dict[str, Dict[str, Any]]:
    want = [s for s in dict.fromkeys(symbols) if s]
    out: Dict[str, Dict[str, Any]] = {}
    missing: List[str] = []
    for s in want:
        hit = _memo_get(("profile", s))
        if hit is _MISS:
            missing.append(s)
        else:
            out[s] = hit
    if missing:
        key = ("profiles", tuple(sorted(missing)))
        got = await ctx.step("profiles", lambda: _shared(key, 0, lambda: _fetch_profiles(ctx.fmp, missing)),
                             reason="profiles_unavailable")
        out.update({s: p for s, p in got.items() if s in missing})
    ctx.profiles_requested += len(want)
    ctx.profiles_returned += sum(1 for s in want if s in out)
    return out


async def _rows(ctx: _Ctx, stage: str, key: Tuple[Any, ...], build_query: Callable[[Any], Any], *,
                reason: str) -> List[Dict[str, Any]]:
    """A memoized Supabase read (10 min), off the event loop; dict rows only."""
    async def fetch() -> List[Dict[str, Any]]:
        res = await sb_exec(build_query(ctx.sb))
        data = getattr(res, "data", None)
        if not isinstance(data, list):
            raise RuntimeError(f"{stage}: Supabase returned {type(data).__name__}, not a list")
        return [r for r in data if isinstance(r, dict)]

    return await ctx.step(stage, lambda: _shared(key, LIST_TTL_SECONDS, fetch), reason=reason)


async def _club_rows(ctx: _Ctx, *, reason: str) -> Dict[str, Dict[str, Any]]:
    rows = await _rows(
        ctx, "club_companies", ("club_rows",),
        lambda sb: sb.table(_CLUB_COMPANIES_TABLE)
        .select("slug,ciks,card_kind,use_13f,detail_symbol,published")
        .eq("published", True).limit(500),
        reason=reason,
    )
    return {r["slug"]: r for r in rows if isinstance(r.get("slug"), str)}


@lru_cache(maxsize=1)
def _registry_order() -> Dict[str, int]:
    """CIK → index of the 13F filers in `data/whale_registry.json` (the curated order). An
    unreadable registry yields {} (no registry filers — fail closed), logged at ERROR."""
    try:
        rows = json.loads(Path(R.WHALE_REGISTRY_PATH).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        logger.error("company news: whale registry unreadable (%s) — no registry 13F filers", _describe(e))
        return {}
    order: Dict[str, int] = {}
    for i, row in enumerate(rows if isinstance(rows, list) else ()):
        if isinstance(row, dict) and row.get("data_source") == "13f":
            cik = R.cik10(row.get("cik"))
            if cik and cik not in order:
                order[cik] = i
    return order


# ── ceo_buys / insider_buys ───────────────────────────────────────────────────


async def _walk_insider(fmp: Any, window_start: date) -> List[Dict[str, Any]]:
    rows = await fmp.get_insider_trades_since(
        window_start.isoformat(), transaction_type="P-Purchase",
        page_size=INSIDER_PAGE_SIZE, max_pages=INSIDER_MAX_PAGES,
    )
    if not isinstance(rows, list):
        raise FMPUnavailableException(f"insider feed returned {type(rows).__name__}, not a list")
    return rows


async def _walk_issuer(fmp: Any, cik: str, window_start: date) -> List[Dict[str, Any]]:
    """One ISSUER's insider rows filed since the window opened — every share class, symbol spelling
    and blank symbol under its CIK, every transaction code and form (the fail-closed per-issuer
    feed of the Form 4/A check; a short page is its normal end). ``cik`` is the 10-digit form."""
    rows = await fmp.get_insider_trades_since(
        window_start.isoformat(), company_cik=cik,
        page_size=INSIDER_AMENDMENT_PAGE_SIZE, max_pages=INSIDER_AMENDMENT_MAX_PAGES,
    )
    if not isinstance(rows, list):
        raise FMPUnavailableException(f"per-issuer insider feed returned {type(rows).__name__}, not a list")
    return rows


# ── THE amendment rule (review rounds 7-8) ────────────────────────────────────
#
# Rounds 1-6 tried to tie each Form 4/A to the lines it replaces (a one-to-one matcher, relabel
# and unit-offering excuses, re-code shapes, a late-filing key, a lag exemption …) and every
# round's exception opened a new hole. Round 7 refused the amended PERSON — and round 8's probes
# showed the person tie is itself an exception: a 4/A filed under the issuer's other share class,
# with an unusable symbol, by a joint co-reporter entity, or naming the person in another word
# order never met the person's key. Refusing costs ONE candidate; a wrong figure or role about a
# named company is the harm. So the rule is per ISSUER and has no exception: ANY amendment row
# filed since the window opened, on the issuer's symbol or its issuer CIK (every share class),
# refuses every row of that issuer for the week (`amended_filing`). A published row is therefore
# never built from, or beside, an amendment of its company.


def _is_amendment_row(row: Mapping[str, Any]) -> bool:
    """An amendment row: its ``formType`` carries "/A" ("4/A", "3/A", "5/A", " 4/a "), read exactly
    as `extract_insider_buys` reads it (stripped, upper-cased; a missing form is a plain "4")."""
    form = row.get("formType")
    return "/A" in (str(form).strip().upper() if form not in (None, "") else "4")


def _row_symbol(row: Mapping[str, Any]) -> str:
    """A raw Form 4 row's symbol in our join form ("BRK.B" → "BRK-B"), or "" when it has none."""
    raw = row.get("symbol")
    return form4_symbol(raw.strip()) if isinstance(raw, str) else ""


def _filed_since(row: Mapping[str, Any], window_start: date) -> bool:
    """Filed on or after ``window_start``, with NO upper bound (the reads run through the run day,
    so a run-day filing counts). An unreadable filing date counts as in the window (fail closed)."""
    filed = _day(row.get("filingDate"))
    return filed is None or filed >= window_start


def _amended_issuers(rows: Iterable[Any], window_start: date) -> Tuple[Set[str], Set[str]]:
    """``(symbols, issuer CIKs)`` refused for the week by the walk's amendment rows.

    Every amendment row (`_is_amendment_row`) filed since ``window_start`` (`_filed_since`) — any
    transaction code, any security, any line, any reporter — adds its symbol and its issuer CIK
    (``companyCik``; a blank- or junk-symbol row still names its issuer this way). Then one hop
    over the rows' own (symbol, issuer CIK) pairs: every symbol the rows show under an amended
    CIK (the issuer's other share classes), and every CIK they show under an amended symbol (an
    amendment row that carries only the symbol). An amendment naming neither is logged and can
    refuse no one."""
    syms: Set[str] = set()
    ciks: Set[str] = set()
    pairs: List[Tuple[str, Optional[str]]] = []
    anonymous = 0
    for r in rows:
        if not isinstance(r, dict):
            continue
        sym, cik = _row_symbol(r), normalize_cik(r.get("companyCik"))
        pairs.append((sym, cik))
        if not _is_amendment_row(r) or not _filed_since(r, window_start):
            continue
        if sym:
            syms.add(sym)
        if cik:
            ciks.add(cik)
        if not sym and not cik:
            anonymous += 1
    if anonymous:
        logger.info("company news: %d amendment row(s) name no symbol and no issuer — tied to no one",
                    anonymous)
    seed_syms, seed_ciks = frozenset(syms), frozenset(ciks)
    for sym, cik in pairs:
        if sym and cik in seed_ciks:
            syms.add(sym)
        if cik and sym in seed_syms:
            ciks.add(cik)
    return syms, ciks


def _issuer_amended(b: Any, syms: Set[str], ciks: Set[str]) -> bool:
    """Is this buy line's issuer — its symbol, or the issuer CIK its line names — refused?"""
    return b.symbol in syms or (b.company_cik is not None and b.company_cik in ciks)


_NAME_WORD_RE = re.compile(r"[a-z0-9]+")
#: A reporting name longer than this is junk; its order-free key is not built.
_REPORTER_NAME_MAX = 200


def _identity_keys(cik: Any, name: Any) -> Set[str]:
    """Every identity key a reporter carries: `insider_reporter_key` of the CIK alone and of the
    name alone, plus an ORDER-FREE key of the name's words ("COHEN RYAN", "Cohen, Ryan" and
    "Ryan Cohen" — which `normalize_insider_name` reads last-first — are one person). Used to find
    the person's OTHER rows (the role check and the per-issuer read's coverage); amendments never
    match on it any more. Matching more rows only checks more titles, so two people whose names
    share every word are an accepted over-block."""
    keys = {insider_reporter_key({"reportingCik": cik}), insider_reporter_key({"reportingName": name})}
    if isinstance(name, str) and len(name) <= _REPORTER_NAME_MAX:
        words = sorted(_NAME_WORD_RE.findall(name.lower()))
        if len(words) >= 2:
            keys.add("words:" + " ".join(words))
    keys.discard("")
    return keys


def _line_keys(b: Any) -> Set[str]:
    """A buy line's identity keys: its reporter key (CIK first, as the extractor keys it) plus the
    keys of its reporting name."""
    return {b.reporter} | _identity_keys(None, b.name_raw)


def _row_keys(r: Mapping[str, Any]) -> Set[str]:
    return _identity_keys(r.get("reportingCik"), r.get("reportingName"))


def _person_keys(lines: Sequence[Any]) -> Set[str]:
    """A week row's PERSON, across issuers (review round 10): every identity key of its lines
    (`_line_keys` — the reporting CIK's key, the name's key and its order-free word key), plus the
    order-free key of the name's words WITHOUT single-letter initials. That last key mirrors the
    template: `news_templates._person_key` reads "Arthur H. Penn" and "Arthur J. Penn" as one
    person (a middle initial ignored) and refuses the WHOLE week as `record_invalid` — so two such
    rows under different CIKs are tied here first and the larger one kept. Two rows sharing any key
    are one person; two people whose names share every word (initials aside) are an accepted
    over-block (one row fewer, never a wrong count). A name with fewer than two multi-letter words
    gets no such key (too broad to tie on)."""
    keys: Set[str] = set()
    for b in lines:
        keys |= _line_keys(b)
        name = b.name_raw
        if isinstance(name, str) and len(name) <= _REPORTER_NAME_MAX:
            words = sorted(w for w in _NAME_WORD_RE.findall(name.lower()) if len(w) > 1)
            if len(words) >= 2:
                keys.add("names:" + " ".join(words))
    keys.discard("")
    return keys


def _shares_of(v: Any) -> Optional[float]:
    """A row's share count as the extractor reads it (`_insider_buys_common._finite`): a finite
    non-bool number or numeric string, else None."""
    if isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


#: One coverage target of the issuer read: (symbol, identity keys, filing day, shares).
_Target = Tuple[str, Set[str], date, float]


def _coverage_targets(symbol: str, lines: Sequence[Any], issuer: str,
                      walk_rows: Sequence[Any]) -> Optional[List[_Target]]:
    """What an up-to-date issuer read must hold (review round 9): the person's published lines
    from their NEWEST filing day, and every walk row of the issuer (its CIK, any symbol) filed on
    or after that day. An amendment is always filed after the line it amends, so a read that
    stops before the newest line the walk saw is stale exactly where a 4/A would sit — "any one
    line" was not enough. A walk row with no readable day, size or identity cannot be matched and
    is not a target; the person's own lines always are. None when the lines carry no filing day
    (fail closed)."""
    days = [d for d in (_day(b.filing_date) for b in lines) if d is not None]
    if not days:
        return None
    newest = max(days)
    person = set().union(*(_line_keys(b) for b in lines))
    targets: List[_Target] = [(b.symbol, person, newest, b.shares)
                              for b in lines if _day(b.filing_date) == newest]
    own = {(sym, filed, round(shares, 4)) for sym, _k, filed, shares in targets}
    for r in walk_rows:
        if not isinstance(r, dict) or normalize_cik(r.get("companyCik")) != issuer:
            continue
        filed, shares, keys = _day(r.get("filingDate")), _shares_of(r.get("securitiesTransacted")), _row_keys(r)
        if filed is None or filed < newest or shares is None or not keys:
            continue
        sym = _row_symbol(r)
        if (sym, filed, round(shares, 4)) in own and keys & person:
            continue                          # one of the person's own lines, already a target
        targets.append((sym, keys, filed, shares))
    return targets


def _missing_targets(feed: Sequence[Any], targets: Sequence[_Target]) -> int:
    """How many ``targets`` the read ``feed`` does not hold: a row of the same symbol, filing day
    and share count (to 4 decimals) whose identity keys meet the target's."""
    index: Dict[Tuple[str, date, float], List[Set[str]]] = {}
    for r in feed:
        if not isinstance(r, dict):
            continue
        filed, shares = _day(r.get("filingDate")), _shares_of(r.get("securitiesTransacted"))
        if filed is None or shares is None:
            continue
        index.setdefault((_row_symbol(r), filed, round(shares, 4)), []).append(_row_keys(r))
    return sum(1 for sym, keys, filed, shares in targets
               if not any(k & keys for k in index.get((sym, filed, round(shares, 4)), ())))


async def _issuer_feed(ctx: "_Ctx", cik: str, window_start: date) -> List[Dict[str, Any]]:
    """One issuer's per-issuer read (every code, every class), memoized like the walk and shared by
    both Form 4 series. Budget exhaustion raises `MarketingNewsUnavailable`; any other failure
    passes through raw (the caller fails the row closed)."""
    return await ctx.step(
        "insider_amendment_check",
        lambda: _shared(("insider_issuer", cik, window_start.isoformat()), INSIDER_RAW_TTL_SECONDS,
                        lambda: _walk_issuer(ctx.fmp, cik, window_start), keep=bool),
        reason="insider_feed_unavailable", passthrough=(Exception,),
    )


async def _amendment_verdict(ctx: "_Ctx", symbol: str, lines: Sequence[Any], *, issuer: Optional[str],
                             walk_rows: Sequence[Any], amend_since: date,
                             purchase_window: Tuple[date, date]) -> Optional[str]:
    """None when the row stands; else its rejection reason. The market-wide walk asks FMP for P
    rows only, so an amendment under another code (a re-code, a derivative-only 4/A) never
    reaches it: the row's ISSUER — ``issuer``, its profile's CIK — is re-read with no transaction
    filter (review round 9: by CIK, so a 4/A under the issuer's other share class, a blank or a
    variant symbol comes back too, whether or not the P walk showed that class).

    Two windows, two jobs (the spotlight lane's review, 2026-11-15): ``amend_since`` is the LOOK-BACK — the issuer
    read's start and the filing day from which a 4/A, a role text and a co-officer count — and ``purchase_window``
    (both ends inclusive) is the window whose purchases the row must hold whole (``partial_person``). The weekly
    roundups pass their own window for both (``(window_start, (window_start, window_end))``: byte for byte what one
    window did); the insider spotlight looks back at least as far as the roundups do, while its purchases are only
    its own few days — an earlier purchase of the person is another window's, never a missing line of this one.

    * No profile CIK to read by, the read failing, a read holding another issuer's rows (the
      filter was not applied), or a read that does not hold what the walk saw
      (`_coverage_targets`: the person's newest-day lines and every walk row of the issuer filed
      since) → ``amendment_check_failed`` (WARNING): fail closed for THIS row, never the week.
      Budget exhaustion still raises.
    * ANY amendment row in the read filed since the window opened → ``amended_filing``, whoever
      filed it and whatever its symbol.
    * A row of the person there whose role text is not the sitting role's (`_role_text_ok`:
      "officer: Former CEO" on an F-coded Form 4 the P walk never returns; a director's
      "other: Former Director") → ``role_uncertain``.
    * Review round 9, on the same read: a row of the person whose name is on the Congress
      block-list → ``congress_name``; for a CEO / CFO row, a co-CEO / co-CFO title anywhere, or
      ANOTHER person with that officer role (the shared role rule or a sitting title) →
      ``ambiguous_ceo``; a purchase of the person filed in the window that the row does not hold
      (`_unaccounted_purchases`) → ``partial_person``."""
    cik = R.cik10(issuer) if issuer is not None else None
    if cik is None:
        logger.warning("company news %s: %s Form 4/A check impossible — its profile names no issuer CIK to "
                       "read; row dropped", ctx.series, symbol)
        return "amendment_check_failed"
    try:
        feed = await _issuer_feed(ctx, cik, amend_since)
    except (MarketingNewsUnavailable, asyncio.CancelledError):
        raise
    except Exception as e:  # noqa: BLE001 — any read failure fails THIS row closed, logged
        logger.warning("company news %s: %s Form 4/A check failed reading its issuer (CIK %s) — row dropped (%s)",
                       ctx.series, symbol, cik, _describe(e))
        return "amendment_check_failed"
    rows = [r for r in feed if isinstance(r, dict)]
    foreign = {c for c in (normalize_cik(r.get("companyCik")) for r in rows) if c is not None and c != issuer}
    if foreign:
        logger.warning("company news %s: %s Form 4/A check read rows of %d other issuer(s) under CIK %s — not "
                       "one issuer's read; row dropped", ctx.series, symbol, len(foreign), cik)
        return "amendment_check_failed"
    if any(_is_amendment_row(r) and _filed_since(r, amend_since) for r in rows):
        logger.info("company news %s: %s an amendment of the issuer was filed since the window opened — "
                    "row dropped", ctx.series, symbol)
        return "amended_filing"
    targets = _coverage_targets(symbol, lines, issuer, walk_rows)
    missing = len(lines) if targets is None else _missing_targets(rows, targets)
    if missing:
        logger.warning("company news %s: %s Form 4/A check read is missing %d row(s) the walk saw from the "
                       "person's newest filing day on (a stale or degraded read) — row dropped",
                       ctx.series, symbol, missing)
        return "amendment_check_failed"
    role = _person_role(lines)
    person = set().union(*(_line_keys(b) for b in lines)) if lines else set()
    for r in rows:
        if (_filed_since(r, amend_since) and _row_keys(r) & person
                and not _role_text_ok(r.get("typeOfOwner"), role)):
            logger.info("company news %s: %s the row's %s has a role text that is not the sitting %s's — "
                        "row dropped", ctx.series, symbol, role, role)
            return "role_uncertain"
    # Review round 9: the read holds every code, so the person's other rows here may carry a
    # spelling of their name the block-list knows (never logged by name) …
    if any(_row_keys(r) & person and R.is_congress_name(r.get("reportingName")) for r in rows):
        logger.info("company news %s: %s the row's person matches the Congress block-list in the issuer "
                    "read — row dropped", ctx.series, symbol)
        return "congress_name"
    # … a co-CEO / co-CFO title (anyone's, any text), or ANOTHER person holding the row's officer
    # role (a tax-withholding or grant row the P walk never returns): "the company's CEO" would be
    # false for one of two …
    if role in ("ceo", "cfo"):
        for r in rows:
            if not _filed_since(r, amend_since):
                continue
            text = r.get("typeOfOwner")
            if role in _co_title_roles(text) or (
                    isinstance(text, str) and not (_row_keys(r) & person)
                    and (insider_role(text) == role or _sitting_title(officer_title(text), role))):
                logger.info("company news %s: %s the issuer read shows a co-%s or a second %s — row dropped",
                            ctx.series, symbol, role, role)
                return "ambiguous_ceo"
    # … and a purchase of the person the row does not hold (the walk's pages, another class, a
    # line the extractor skipped): the row would understate what the filings report — inside the
    # row's own PURCHASE window (an earlier purchase belongs to another window's post).
    purchase_start, purchase_end = purchase_window
    unaccounted = _unaccounted_purchases(_purchase_index(rows), lines, symbol=symbol, issuer=issuer,
                                         window_start=purchase_start, window_end=purchase_end)
    if unaccounted:
        logger.info("company news %s: %s the issuer read holds %d purchase(s) of the row's person the row "
                    "does not — row dropped", ctx.series, symbol, unaccounted)
        return "partial_person"
    return None


async def _amendment_checked(ctx: "_Ctx", purchases: Sequence[Any], chosen: Mapping[str, Sequence[Any]],
                             issuers: Mapping[str, Optional[str]], walk_rows: Sequence[Any], *,
                             amend_since: date, purchase_window: Tuple[date, date]) -> List[Any]:
    """The rows (rank order) whose per-issuer Form 4/A check passes, at most `R.INSIDER_MAX_ROWS`,
    checking at most `INSIDER_AMENDMENT_MAX_SYMBOLS` rows: the rows a week can publish first (in
    parallel), then spares for the rows a check dropped. ``issuers`` maps a row's symbol to its
    PROFILE's issuer CIK (the read's key). ``amend_since`` / ``purchase_window``: `_amendment_verdict`'s two
    windows (the weekly roundups pass their one window for both).

    Review round 10: ONE row per PERSON across issuers. The week counts people ("At least 2 CEOs
    disclosed buying"), and one CEO of two affiliated funds (PennantPark's PNNT and PFLT) buying at
    both was two CEOs. A row whose person (`_person_keys`: the reporting CIK, else the name) already
    holds a kept row is dropped (`same_person`), so the person keeps their LARGEST row that passes
    the check — a queued duplicate is never checked."""
    out: List[Any] = []
    seen: Set[str] = set()
    queue = list(purchases)
    checked = 0
    while queue and len(out) < R.INSIDER_MAX_ROWS and checked < INSIDER_AMENDMENT_MAX_SYMBOLS:
        fresh = []
        for row in queue:
            if _person_keys(chosen[row.company.symbol]) & seen:
                ctx.reject("same_person")
            else:
                fresh.append(row)
        queue = fresh
        if not queue:
            break
        n = min(R.INSIDER_MAX_ROWS - len(out), INSIDER_AMENDMENT_MAX_SYMBOLS - checked, len(queue))
        wave, queue = queue[:n], queue[n:]
        checked += n
        verdicts = await asyncio.gather(
            *(_amendment_verdict(ctx, row.company.symbol, chosen[row.company.symbol],
                                 issuer=issuers.get(row.company.symbol), walk_rows=walk_rows,
                                 amend_since=amend_since, purchase_window=purchase_window)
              for row in wave),
            return_exceptions=True,
        )
        for v in verdicts:
            if isinstance(v, BaseException):
                raise v
        for row, v in zip(wave, verdicts):
            if v is not None:
                ctx.reject(v)
                continue
            keys = _person_keys(chosen[row.company.symbol])
            if keys & seen:                  # a larger row of the person passed in this wave
                ctx.reject("same_person")
                continue
            seen |= keys
            out.append(row)
    return out


@dataclass
class _SpotlightGroup:
    """One issuer's qualifying people before the per-person Form 4/A check (`_spotlight_checked`): ``people`` =
    ``(InsiderPurchase, its lines)`` in `R.spotlight_person_order`, one per person; ``issuer`` = the PROFILE's
    issuer CIK (the per-issuer read's key); ``issuer_of`` = the CIK the lines name, else None (the share-class
    check, as the roundups key it)."""

    symbol: str
    company: Any
    issuer: Optional[str]
    issuer_of: Optional[str]
    people: List[Tuple[Any, List[Any]]]


async def _spotlight_checked(ctx: "_Ctx", groups: Sequence[_SpotlightGroup], walk_rows: Sequence[Any], *,
                             amend_since: date, purchase_window: Tuple[date, date], keep: int
                             ) -> List[Tuple[_SpotlightGroup, List[Any]]]:
    """The cluster variant of `_amendment_checked` (the spotlight lane's review, 2026-11-15): every PERSON of an
    issuer — its first `SPOTLIGHT_PEOPLE_PER_ISSUER` people, keyed (symbol, reporter) — through the same per-issuer
    Form 4/A check (`_amendment_verdict`: one memoized issuer read serves them all), groups in rank order, at most
    ``keep`` of them kept (the insider list's `R.INSIDER_MAX_ROWS`, owner decisions 2026-11-20), checking at most `SPOTLIGHT_AMENDMENT_MAX_ISSUERS` issuers and
    `SPOTLIGHT_AMENDMENT_MAX_PEOPLE` people: the groups a call can return first (in parallel), then spares for the
    groups a check emptied. A person the check drops is counted with its reason (one count per person: an
    issuer-level ``amended_filing`` counts once for each of its people, as the roundups count one per row) and the
    group keeps the rest; a group with nobody left is dropped. Returns ``(group, the InsiderPurchase rows that
    passed, in `R.spotlight_person_order`)``."""
    out: List[Tuple[_SpotlightGroup, List[Any]]] = []
    queue = list(groups)
    issuers = people = 0
    while queue and len(out) < keep and issuers < SPOTLIGHT_AMENDMENT_MAX_ISSUERS:
        wave: List[_SpotlightGroup] = []
        for g in list(queue):
            if len(out) + len(wave) >= keep or issuers + len(wave) >= SPOTLIGHT_AMENDMENT_MAX_ISSUERS:
                break
            need = min(len(g.people), SPOTLIGHT_PEOPLE_PER_ISSUER)
            if people + sum(min(len(w.people), SPOTLIGHT_PEOPLE_PER_ISSUER) for w in wave) + need \
                    > SPOTLIGHT_AMENDMENT_MAX_PEOPLE:
                break
            wave.append(g)
        if not wave:
            break
        queue = queue[len(wave):]
        jobs = [(g, row, lines) for g in wave for row, lines in g.people[:SPOTLIGHT_PEOPLE_PER_ISSUER]]
        issuers += len(wave)
        people += len(jobs)
        verdicts = await asyncio.gather(
            *(_amendment_verdict(ctx, g.symbol, lines, issuer=g.issuer, walk_rows=walk_rows,
                                 amend_since=amend_since, purchase_window=purchase_window)
              for g, _row, lines in jobs),
            return_exceptions=True,
        )
        for v in verdicts:
            if isinstance(v, BaseException):
                raise v
        passed: Dict[int, List[Any]] = {id(g): [] for g in wave}
        for (g, row, _lines), v in zip(jobs, verdicts):
            if v is not None:
                ctx.reject(v)
                continue
            passed[id(g)].append(row)
        for g in wave:
            rows = passed[id(g)]
            if rows:
                out.append((g, sorted(rows, key=R.spotlight_person_order)))
            else:
                logger.info("company news %s: %s every person's Form 4/A check refused — issuer dropped",
                            ctx.series, g.symbol)
    return out


def _holding(ownerships: Iterable[str]) -> Optional[str]:
    kinds = set(ownerships)
    if not kinds or kinds - {"D", "I"}:
        return None                       # an unknown ownership is not guessed
    if kinds == {"D"}:
        return "direct"
    if kinds == {"I"}:
        return "indirect"
    return "mixed"


_ROLE_RANK = {"ceo": 0, "cfo": 1, "director": 2}

#: A co-CEO / co-CFO officer title ("Co-CEO", "Co Chief Executive Officer", "co-CFO"). Marketing
#: only: the shared `is_ceo_role` keeps a co-CEO for the Home card, but "the company's chief
#: executive" (the role is the video's only identifier) is false for either of two.
#: Every Unicode dash U+2010-U+2015 counts as the hyphen (a "Co-CEO" typed with a non-breaking
#: hyphen is still a co-CEO).
_CO_OFFICER_RE = re.compile(
    r"\bco[\s\-\u2010-\u2015]*(?:ceo|cfo|chief[\s\-]+(?:executive|financial))", re.I)


def _co_officer(title_raw: Any) -> bool:
    return bool(_CO_OFFICER_RE.search(officer_title(title_raw)))     # linear: no cap that could hide one


def _raw_co_officers(rows: Sequence[Any]) -> Tuple[Set[Tuple[str, str]], Set[Tuple[str, str]]]:
    """``(role groups, people)`` of every raw walk row whose ``typeOfOwner`` — ANY of its text,
    the free ``other:`` field included, not only the officer title — names a co-CEO or co-CFO
    (review round 6). A role group is ``(role, "sym:<symbol>")`` and ``(role, "cik:<issuer>")``,
    as `_issuer_groups` keys a line; a person is ``(symbol, reporter)``. Such a row is never a
    CEO line when its Officer box is unchecked ("director, other: Co-CEO"), so the symbol's
    other co-CEO, filing as plain "Chief Executive Officer", would read as THE CEO."""
    groups: Set[Tuple[str, str]] = set()
    people: Set[Tuple[str, str]] = set()
    for r in rows:
        if not isinstance(r, dict):
            continue
        text, raw = r.get("typeOfOwner"), r.get("symbol")
        if not isinstance(text, str) or not isinstance(raw, str):
            continue
        roles = {"cfo" if ("cfo" in m.group(0).lower() or "financial" in m.group(0).lower()) else "ceo"
                 for m in _CO_OFFICER_RE.finditer(text)}
        if not roles:
            continue
        sym, cik, reporter = form4_symbol(raw.strip()), normalize_cik(r.get("companyCik")), insider_reporter_key(r)
        for role in roles:
            groups.add((role, f"sym:{sym}"))
            if cik:
                groups.add((role, f"cik:{cik}"))
        if reporter:
            people.add((sym, reporter))
    return groups, people


def _issuer_groups(b: Any) -> Tuple[str, ...]:
    """The co-officer groups of a line: its symbol, and its issuer CIK when the line names one."""
    return (f"sym:{b.symbol}",) + ((f"cik:{b.company_cik}",) if b.company_cik else ())


# ── sitting-officer titles (review rounds 8-9, marketing only) ────────────────
#
# Round 7 refused a CEO / CFO whose role text carried a TRANSITIONAL word (incoming, elect,
# former …): a denylist, and round 8's probes walked straight past it ("Previously Chief Executive
# Officer", "CEO (Resigned)", "CEO until 12/31/2026", "CEO Nominee", "Departing CEO"). The role
# is the post's one identifying fact ("GameStop's CEO disclosed buying …"), so the rule is now an
# ALLOW-LIST: an officer title qualifies only when every one of its words is a word a sitting
# CEO's (or CFO's) title is made of. Anything else — a date, a digit, "previously", "resigned",
# "until", "effective", "nominee", "corporate" — is `role_uncertain` (accepted over-block:
# "Co-Chairman and CEO", "CFO and Corporate Controller", "CFO of the Company").
# Round 9: the free ``other:`` text runs through the same allow-list whenever it NAMES the role
# ("officer: Chief Executive Officer, other: Former CEO" is not the sitting CEO), and a director's
# ``other:`` text naming the seat runs through `DIRECTOR_TITLE_WORDS` ("director, other: Former
# Director" is not "a director"); other free text ("Member of 13(d) group") stays ignored. The
# lists also learned the common spellings of sitting titles ("Pres.", "Exec.", "Principal
# Financial Officer", "Chief Financial and Accounting Officer", "CFO & Director", "Board of
# Directors", "Secretary").
# Marketing only: the shared role rules (`is_ceo_role`, `insider_role`) stay the Home card's.

#: The words of a sitting CEO's officer title, after `_title_words` ("Chief Executive Officer"
#: and "Principal Executive Officer" are "ceo"; "Co-Founder", "Co Founder" and "Cofounder" are
#: "cofounder").
CEO_TITLE_WORDS: FrozenSet[str] = frozenset({
    "ceo", "president", "chairman", "chairwoman", "chair", "chairperson", "executive", "of", "the",
    "board", "and", "director", "directors", "founder", "cofounder", "interim", "acting", "secretary",
})
#: The words of a sitting CFO's officer title ("Chief Financial Officer", "Principal Financial
#: Officer" and "Chief / Principal Financial and (Principal) Accounting Officer" are "cfo").
CFO_TITLE_WORDS: FrozenSet[str] = frozenset({
    "cfo", "treasurer", "evp", "svp", "executive", "senior", "vice", "president", "principal",
    "accounting", "officer", "chief", "and", "interim", "acting", "of", "finance", "director",
    "secretary",
})
#: The words of a sitting director's seat, read only from an ``other:`` text that names it
#: ("Lead Independent Director", "Member of the Board of Directors", "Non-Executive Director").
DIRECTOR_TITLE_WORDS: FrozenSet[str] = frozenset({
    "director", "directors", "independent", "lead", "presiding", "non", "executive", "chairman",
    "chairwoman", "chair", "chairperson", "vice", "of", "the", "board", "and", "member",
})
_TITLE_WORDS = {"ceo": CEO_TITLE_WORDS, "cfo": CFO_TITLE_WORDS, "director": DIRECTOR_TITLE_WORDS}
#: The word(s) that name each role in a title (one must be there).
_ROLE_WORDS: Dict[str, FrozenSet[str]] = {
    "ceo": frozenset({"ceo"}), "cfo": frozenset({"cfo"}), "director": frozenset({"director", "directors"}),
}
#: Does an ``other:`` text NAME the role (on the normalised text: lower case, dots and
#: apostrophes dropped, every other punctuation a space)?
_ROLE_NAMED_RE: Dict[str, "re.Pattern[str]"] = {
    "ceo": re.compile(r"\b(?:ceo|chief executive|principal executive)\b"),
    "cfo": re.compile(r"\b(?:cfo|chief financial|principal financial)\b"),
    "director": re.compile(r"\bdirectors?\b"),
}
#: A title longer than this is refused, never truncated (a cut could drop the word that matters).
_TITLE_MAX = 200
#: FMP's "10 percent owner" FLAG, should it ever trail the officer title: a flag, not a role word.
_TEN_PERCENT_FLAG_RE = re.compile(r"\b10\s*(?:%|percent)\s*owner\b", re.I)
#: "&", ",", "/" and ";" join two titles: each becomes " and ".
_TITLE_JOIN_RE = re.compile(r"[&,/;]")
#: Dots and apostrophes are dropped inside a word ("C.E.O." → "ceo", "Sr." → "sr").
_TITLE_DROP_RE = re.compile(r"[.'‘’ʼ]")
#: Every other non-letter, non-digit (hyphens and every Unicode dash, parentheses, colons …)
#: separates words: "CEO-Elect" is "ceo elect", "Co-Founder" is "co founder".
_TITLE_SPLIT_RE = re.compile(r"[^a-z0-9]+")
#: Applied after the abbreviations are spelled out, so "Principal Exec. Officer" is "ceo" too.
_TITLE_PHRASES = (
    (re.compile(r"\b(?:chief|principal) executive officer\b"), "ceo"),
    (re.compile(r"\b(?:chief|principal) financial and (?:principal )?accounting officer\b"), "cfo"),
    (re.compile(r"\b(?:chief|principal) financial officer\b"), "cfo"),
    (re.compile(r"\bco founder\b"), "cofounder"),
)
#: Spelling only: abbreviations of words already on a list.
_TITLE_ABBREVIATIONS = {"vp": ("vice", "president"), "sr": ("senior",), "pres": ("president",),
                        "exec": ("executive",)}


def _title_words(title: str) -> Optional[List[str]]:
    """An officer title as its words: lower case; "&", ",", "/", ";" → "and"; dots and
    apostrophes dropped; every other punctuation a separator; "vp", "sr", "pres" and "exec"
    spelled out; then "chief / principal executive officer" → "ceo", "chief / principal financial
    officer" and "chief / principal financial and (principal) accounting officer" → "cfo",
    "co founder" → "cofounder". None for a title too long to read whole."""
    if len(title) > _TITLE_MAX:
        return None
    s = _TEN_PERCENT_FLAG_RE.sub(" ", title).lower()
    s = _TITLE_JOIN_RE.sub(" and ", s)
    s = _TITLE_DROP_RE.sub("", s)
    words: List[str] = []
    for w in _TITLE_SPLIT_RE.split(s):
        if w:
            words.extend(_TITLE_ABBREVIATIONS.get(w, (w,)))
    s = " ".join(words)
    for pattern, word in _TITLE_PHRASES:
        s = pattern.sub(word, s)
    return s.split()


def _sitting_title(title: Any, role: str) -> bool:
    """Is ``title`` the SITTING ``role``'s — every word on the role's list (`CEO_TITLE_WORDS`,
    `CFO_TITLE_WORDS`, `DIRECTOR_TITLE_WORDS`) and the role itself named ("ceo", "cfo",
    "director(s)")?"""
    if not isinstance(title, str):
        return False
    words = _title_words(title)
    return bool(words) and bool(set(words) & _ROLE_WORDS[role]) and set(words) <= _TITLE_WORDS[role]


def _other_text(text: str) -> str:
    """The free ``other:`` text of a ``typeOfOwner`` — everything after its first "other:" — or ""."""
    at = text.lower().find("other:")
    return text[at + len("other:"):] if at >= 0 else ""


def _names_role(text: str, role: str) -> bool:
    """Does ``text`` name ``role`` ("Former CEO", "Chief Financial Officer until …", "Director
    Nominee")? Linear: the text is normalised and searched once, whatever its length."""
    s = " ".join(_TITLE_SPLIT_RE.split(_TITLE_DROP_RE.sub("", text.lower())))
    return bool(_ROLE_NAMED_RE[role].search(s))


def _role_text_ok(text: Any, role: str) -> bool:
    """One row of a candidate (its raw ``typeOfOwner``): True when it describes the sitting
    ``role``, or says nothing about it.

    * A CEO / CFO: the officer title must be the sitting officer's — a row filed as a non-officer
      ("director", "10 percent owner", "officer:" with no title) is NOT the sitting officer's,
      that filing says otherwise.
    * Any role: an ``other:`` text that NAMES the role ("Former CEO", "CFO until 12/31/2026",
      "Former Director") must be the sitting role's too; other free text ("Member of 13(d)
      group") is no evidence.
    * No role text at all (None, blank, not a string) is no evidence either way: the person's
      other rows decide."""
    if not isinstance(text, str) or not text.strip():
        return True
    if role in ("ceo", "cfo") and not _sitting_title(officer_title(text), role):
        return False
    other = _other_text(text)
    if other.strip() and _names_role(other, role):
        return _sitting_title(other, role)
    return True


def _person_role(lines: Sequence[Any]) -> str:
    """The role a person is published under: "ceo" before "cfo", else "director"."""
    roles = {b.role for b in lines}
    return "ceo" if "ceo" in roles else "cfo" if "cfo" in roles else "director"


def _role_uncertain_people(rows: Sequence[Any], lines: Sequence[Any],
                           amend_since: date) -> Set[Tuple[str, str]]:
    """``(symbol, reporter)`` of every person any of whose role texts is not the sitting role's
    (`_role_text_ok`: a CEO / CFO's officer title and an ``other:`` text naming the role; a
    director's ``other:`` text naming the seat): their own lines, and EVERY walk row of the same
    person (identity keys) on the same issuer — the symbol, or the issuer CIK the lines name —
    filed since ``amend_since``, the LOOK-BACK (the roundups' window start; the insider spotlight's
    `_amendment_verdict` look-back, never shorter than the roundups'). The per-issuer read applies the
    same check to the codes the P walk never returns (`_amendment_verdict`)."""
    people: Dict[Tuple[str, str], List[Any]] = {}
    for b in lines:
        people.setdefault((b.symbol, b.reporter), []).append(b)
    out: Set[Tuple[str, str]] = set()
    officers: Dict[Tuple[str, str], Tuple[str, Set[str], Set[str]]] = {}
    by_symbol: Dict[str, List[Tuple[str, str]]] = {}
    by_cik: Dict[str, List[Tuple[str, str]]] = {}
    for person, own in people.items():
        role = _person_role(own)
        if any(not _role_text_ok(b.title_raw, role) for b in own):
            out.add(person)
            continue
        ciks = {b.company_cik for b in own if b.company_cik}
        officers[person] = (role, set().union(*(_line_keys(b) for b in own)), ciks)
        by_symbol.setdefault(person[0], []).append(person)
        for cik in ciks:
            by_cik.setdefault(cik, []).append(person)
    if not officers:
        return out
    for r in rows:
        if not isinstance(r, dict) or not _filed_since(r, amend_since):
            continue
        candidates = set(by_symbol.get(_row_symbol(r), ()))
        cik = normalize_cik(r.get("companyCik"))
        if cik:
            candidates.update(by_cik.get(cik, ()))
        candidates -= out
        if not candidates:
            continue
        keys = _row_keys(r)
        for person in candidates:
            role, person_keys, _ciks = officers[person]
            if keys & person_keys and not _role_text_ok(r.get("typeOfOwner"), role):
                out.add(person)
    return out


# ── the whole person (review round 9) ─────────────────────────────────────────
#
# A published row states what the filings report for ONE person on ONE issuer in the window: how
# many purchases, how many shares, how many dollars. A person some of whose purchases were dropped
# one by one (an implausible price, no trade date, a line the extractor skipped for its quality,
# a line under the issuer's other class or under no symbol) would be published with a smaller
# count and amount than the filings report — so such a person is REFUSED (`partial_person`) and
# the next one is tried, never re-summed from what survived. A line of ANOTHER issuer
# (`issuer_mismatch`), a sale, a non-common security and a line filed outside the window are not
# the person's purchases of this company and change nothing.


def _is_purchase_row(r: Mapping[str, Any]) -> bool:
    """A raw Form 4 row the filing reports as an open-market purchase of the common stock: code P
    (`classify_insider_transaction`), acquired (or unmarked), a Form 4, and a common-stock — or
    UNLABELLED (fail closed: it may be the stock) — security."""
    tx = r.get("transactionType")
    if not isinstance(tx, str) or classify_insider_transaction(tx) != "Informative Buy":
        return False
    acq = r.get("acquisitionOrDisposition")
    if acq not in (None, "") and str(acq).strip().upper() != "A":
        return False
    form = r.get("formType")
    if not (str(form).strip().upper() if form not in (None, "") else "4").startswith("4"):
        return False
    sec = r.get("securityName")
    return is_common_stock(sec) or not (isinstance(sec, str) and sec.strip())


def _line_purchase_key(b: Any) -> Tuple[Any, ...]:
    """A buy line as the extractor de-duplicates it: (symbol, trade-or-filing day, ownership,
    shares, price) — the same (shares, price) line on two filings is one purchase."""
    return (b.symbol, b.transaction_date or b.filing_date, b.ownership, round(b.shares, 4), round(b.price, 4))


def _raw_purchase_key(r: Mapping[str, Any]) -> Optional[Tuple[Any, ...]]:
    """`_line_purchase_key` of a raw row, or None when it cannot be read (never accounted for)."""
    shares, price = _shares_of(r.get("securitiesTransacted")), _shares_of(r.get("price"))
    if shares is None or price is None:
        return None
    when = _day(r.get("transactionDate")) or _day(r.get("filingDate"))
    if when is None:
        return None
    own = r.get("directOrIndirect")
    return (_row_symbol(r), when.isoformat(), str(own).strip().upper() if isinstance(own, str) else "",
            round(shares, 4), round(price, 4))


def _purchase_index(raw_rows: Iterable[Any]) -> Dict[str, List[Dict[str, Any]]]:
    """identity key → the purchase rows (`_is_purchase_row`) carrying it: built once per walk, so
    the per-person check reads only that person's rows (the walk holds up to 3,000)."""
    index: Dict[str, List[Dict[str, Any]]] = {}
    for r in raw_rows:
        if isinstance(r, dict) and _is_purchase_row(r):
            for k in _row_keys(r):
                index.setdefault(k, []).append(r)
    return index


def _unaccounted_purchases(index: Mapping[str, Sequence[Dict[str, Any]]], lines: Sequence[Any], *,
                           symbol: str, issuer: Optional[str], window_start: date, window_end: date) -> int:
    """How many purchases (`_purchase_index`) of the person behind ``lines`` (identity keys) on
    the row's issuer — ``issuer`` (a row's own ``companyCik``), else the symbol when either has
    none — filed in ``[window_start, window_end]`` (an unreadable filing day counts) are NOT among
    ``lines``. A row of another issuer is never the person's purchase of this one."""
    if not lines:
        return 0
    person = set().union(*(_line_keys(b) for b in lines))
    have = {_line_purchase_key(b) for b in lines}
    seen: Set[int] = set()
    missing = 0
    for r in (r for k in sorted(person) for r in index.get(k, ())):
        if id(r) in seen:
            continue
        seen.add(id(r))
        rc = normalize_cik(r.get("companyCik"))
        if rc is not None and issuer is not None:
            if rc != issuer:
                continue
        elif _row_symbol(r) != symbol:
            continue
        filed = _day(r.get("filingDate"))
        if filed is not None and not (window_start <= filed <= window_end):
            continue
        if _raw_purchase_key(r) not in have:
            missing += 1
    return missing


def _co_title_roles(text: Any) -> Set[str]:
    """The officer roles ("ceo", "cfo") a ``typeOfOwner`` names a CO- holder of, anywhere in its
    text (the ``other:`` field too)."""
    if not isinstance(text, str):
        return set()
    return {"cfo" if ("cfo" in m.group(0).lower() or "financial" in m.group(0).lower()) else "ceo"
            for m in _CO_OFFICER_RE.finditer(text)}


def _insider_row(ctx: _Ctx, company: "R.CompanyRef", profile: Mapping[str, Any],
                 lines: Sequence[Any]) -> Any:
    """One reporter's plausible lines → `InsiderPurchase`, or a rejection reason (str). Reached
    only while the Congress roster is fresh for the run date (`_collect_insider` refuses the series
    otherwise — review round 11: role-only would still point at a member a stale list misses)."""
    # Unreachable by construction (THE amendment rule refused every issuer with an amendment
    # before ranking), kept so a published row can never carry a 4/A line: `amended` is False.
    if any("/A" in b.form_type for b in lines):
        return "amended_filing"
    # A fund or LLC with a board designee files with the Director box checked: never published
    # as "a director", named or role-only. The reporter loop then tries the next person.
    if any(R.is_entity_reporter(b.name_raw) for b in lines):
        return "reporter_is_entity"
    holding = _holding(b.ownership for b in lines)
    trades = [_day(b.transaction_date) for b in lines]
    filings = sorted({_day(b.filing_date) for b in lines} - {None})
    if holding is None or None in trades or not filings:
        return "record_invalid"
    role = min((b.role for b in lines), key=lambda r: _ROLE_RANK.get(r, 9))
    names = {b.name_raw for b in lines}
    rendered = {R.render_person_name(n, company=company.name) for n in names}
    person = rendered.pop() if len(rendered) == 1 else None
    if person is not None and role == "ceo" and not R.corroborates(person, profile.get("ceo")):
        person = None
    # Owner decision 4 (2026-10-15): from an even year's Election Day through January 3 the people just
    # elected are members-elect the roster (the SITTING Congress) cannot know — the record carries NO
    # person, so neither the post nor its fact sheet can name one. The template withholds names too
    # (`news_templates._insider_parts`). The Congress drop in `_collect_insider` still runs on every raw
    # name: a sitting member's row is dropped, never shown role-only. Review of 2026-10-16 (ME-1): judged over
    # every day the run's posts can go out (`R.members_elect_for_run`: the run date and the two after it), so a
    # Monday-before-Election-Day run builds no person either — its posts can go out on Election Day.
    if person is not None and R.members_elect_for_run(ctx.run_date):
        person = None
    try:
        return R.InsiderPurchase(
            company=company,
            role=role,
            person_name=person,
            amount_usd=math.fsum(b.dollars for b in lines),
            shares=math.fsum(b.shares for b in lines),
            purchases=len(lines),
            earliest_trade_date=min(trades),
            latest_trade_date=max(trades),
            filing_dates=tuple(filings),
            holding=holding,
            amended=False,
            # Owner decision 2026-11-15 ("Captions + picture"): the filing's OWN cost per share — each Form 4
            # line's own price, as filed (`InsiderBuy.price`; its `dollars` is shares × that price), over exactly
            # the lines summed above. NEVER the profile's price: that is only `price_plausible`'s reference and
            # must not travel (tests/test_marketing_company_news_leaks.py plants it as a canary).
            cost_per_share_min_usd=min(b.price for b in lines),
            cost_per_share_max_usd=max(b.price for b in lines),
        )
    except ValueError as e:
        logger.info("company news %s: %s row refused as record_invalid (%s)",
                    ctx.series, company.symbol, _describe(e))
        return "record_invalid"


def _one_row_per_issuer(ctx: _Ctx, purchases: Sequence[Any],
                        issuer_of: Mapping[str, Optional[str]]) -> List[Any]:
    """The issuer, not the symbol, is the unit: two share classes of one company (BF-A and
    BF-B, both "Brown-Forman") would read as two companies — "2 CEOs disclosed buying" about one
    person. ``purchases`` is in rank order; the first (largest) row of an issuer is kept, every
    later row sharing its issuer CIK (the one its Form 4 lines name, already checked against the
    profile) or its display name is dropped (`share_class_overlap`)."""
    seen_ciks: Set[str] = set()
    seen_names: Set[str] = set()
    out = []
    for row in purchases:
        cik = issuer_of.get(row.company.symbol)
        name = row.company.name.casefold()
        if (cik is not None and cik in seen_ciks) or name in seen_names:
            ctx.reject("share_class_overlap")
            continue
        if cik is not None:
            seen_ciks.add(cik)
        seen_names.add(name)
        out.append(row)
    return out


def _insider_series_open(ctx: _Ctx) -> None:
    """The gates EVERY Form 4 series passes before any read — the weekly roundups and the insider spotlight alike:
    a whole Congress block-list, a roster fresh for the run date, and (logged) the members-elect window."""
    if not R.person_names_allowed():
        # The Congress block-list could not be built (roster / registry unreadable, logged at
        # ERROR by the rules module). A member's row must be DROPPED, and role-only would still
        # point at them — so no Form 4 series runs until the block-list is whole again.
        raise MarketingNewsUnavailable(ctx.series, "internal_error",
                                       "the congress block-list is unusable; Form 4 series refused")
    # Review round 11 (main-session decision 2026-10-10; it replaces round 9/10's role-only path): a
    # roster that does not hold the SITTING Congress on the run date (its `congress_start`), is
    # undated, or is older than `R.ROSTER_MAX_AGE_DAYS` cannot know every member — and role-only
    # would still point at one it misses ("GameStop's director disclosed buying …"), exactly as with
    # an unusable block-list above. So the series is REFUSED (the chain falls back) until the roster
    # is refreshed; `R.roster_fresh_for` logs why at ERROR. Read once, from the RUN date.
    if not R.roster_fresh_for(ctx.run_date):
        logger.error("company news %s: the congress roster is not fresh for %s (it must hold the sitting "
                     "Congress) — Form 4 series refused until scripts/refresh_congress_roster.py refreshes it",
                     ctx.series, ctx.run_date)
        raise MarketingNewsUnavailable(ctx.series, "internal_error",
                                       "the congress roster does not hold the sitting Congress, is undated "
                                       "or too old; Form 4 series refused")
    if R.members_elect_for_run(ctx.run_date):
        logger.info("company news %s: %s is inside the members-elect window (Election Day through January 3, "
                    "judged over the run date and the %d days its posts can still go out) — every row is built "
                    "role-only, no person (owner decision 4)", ctx.series, ctx.run_date,
                    R.MEMBERS_ELECT_PUBLISH_DAYS)


async def _insider_walk(ctx: _Ctx, since: date) -> List[Dict[str, Any]]:
    """The market-wide P walk of the rows filed since ``since`` — memoized per start day, so every Form 4 series of
    the process reading from that day shares one walk."""
    rows = await ctx.step(
        "insider_walk",
        lambda: _shared(("insider", since.isoformat()), INSIDER_RAW_TTL_SECONDS,
                        lambda: _walk_insider(ctx.fmp, since), keep=bool),
        reason="insider_feed_unavailable",
    )
    if not rows:
        # A market-wide week of open-market purchases is never empty: an empty answer is the
        # client's page-0 403/404 degrade (or an outage), not "nobody bought".
        raise MarketingNewsUnavailable(ctx.series, "insider_feed_empty",
                                       f"stage=insider_walk; 0 rows filed since {since}")
    return rows


def _insider_gated_lines(ctx: _Ctx, rows: Sequence[Any], *, window_start: date, window_end: date,
                         roles: Tuple[str, ...], amend_since: date) -> Tuple[List[Any], Set[str]]:
    """The walk-level Form 4 gates, shared by the weekly roundups and the insider spotlight (2026-11-15) →
    ``(the lines that may still be published, the issuer CIKs an amendment refused)``. Purchases are the lines
    FILED in ``[window_start, window_end]``; ``amend_since`` is the LOOK-BACK of THE amendment rule and the role
    check (the roundups pass their window start: one window, as before; the spotlight looks back at least as far
    as the roundups do). In order, each counted: the symbol grammar, THE amendment rule, the co-officer refusal,
    the officer groups (built before the role refusal), the sitting-role allow-list, two holders of one officer
    role, and the whole-person Congress drop."""
    buys = extract_insider_buys(rows, window_start=window_start, window_end=window_end, roles=roles)
    ctx.considered = len({b.symbol for b in buys})

    # Symbols our grammar refuses (warrants, units, odd tickers) never reach a profile call.
    usable = []
    refused_symbols: Dict[str, str] = {}
    for b in buys:
        problem = R.symbol_problem(b.symbol)
        if problem:
            refused_symbols[b.symbol] = problem
        else:
            usable.append(b)
    for problem in refused_symbols.values():
        ctx.reject(problem)

    # THE amendment rule (review rounds 7-8): ANY amendment row of an issuer filed since the
    # look-back opened (the walk's rows, run day included) — on its symbol or its issuer CIK, every
    # share class — refuses every person of that issuer for the week (`amended_filing`, one count
    # per person), before anything else reads them. The profile stage refuses a row whose
    # PROFILE CIK is amended; the per-issuer read applies the same rule to the codes the
    # P-only walk never returns.
    amended_syms, amended_ciks = _amended_issuers(rows, amend_since)
    if amended_syms or amended_ciks:
        refused = {(b.symbol, b.reporter) for b in usable if _issuer_amended(b, amended_syms, amended_ciks)}
        if refused:
            ctx.reject("amended_filing", len(refused))
            usable = [b for b in usable if (b.symbol, b.reporter) not in refused]

    # A co-CEO / co-CFO title (review round 5): that person is dropped and, when the line is a
    # CEO or CFO line, so is every line of THAT role on its symbol and issuer CIK — the other
    # co-officer may file as plain "CEO", and dropping only the co- line would hide them from the
    # grouping below. One `ambiguous_ceo` count per person dropped.
    co_lines = [b for b in usable if _co_officer(b.title_raw)]
    co_groups = {(b.role, g) for b in co_lines if b.role in ("ceo", "cfo") for g in _issuer_groups(b)}
    co_people = {(b.symbol, b.reporter) for b in co_lines}
    # Review round 6: a co-CEO / co-CFO title the officer title does not carry ("director,
    # other: Co-CEO" — no Officer box, so never a CEO line) still means the symbol's other CEO
    # is one of two. Every raw row of the walk is read, whatever its role text.
    raw_groups, raw_people = _raw_co_officers(rows)
    co_groups |= raw_groups
    co_people |= raw_people
    if co_groups or co_people:
        dropped = {(b.symbol, b.reporter) for b in usable
                   if (b.symbol, b.reporter) in co_people
                   or any((b.role, g) in co_groups for g in _issuer_groups(b))}
        if dropped:
            ctx.reject("ambiguous_ceo", len(dropped))
            usable = [b for b in usable if (b.symbol, b.reporter) not in dropped]

    # Two or more people holding one officer role on one issuer: "its CEO" (or "its CFO") would be
    # false for one of them. Grouped per SYMBOL and per ISSUER CIK (a line's companyCik), so
    # co-officers buying two share classes are caught too — and a line without a CIK still meets
    # its symbol's other officer. Review round 9: the groups are built BEFORE the role_uncertain
    # refusal below, from EVERY line the shared rule calls that role — a co-CEO whose title the
    # co- pattern does not know ("Joint CEO", "Dual CEO", "CEO (Co-Lead)") is refused as
    # role_uncertain AND still makes the other one of two; an incoming CEO beside the sitting one
    # does too (accepted over-block) — and CFOs are grouped like CEOs (insider_buys), dropping only
    # the symbol's lines of that role (its directors stay).
    officer_roles = tuple(r for r in ("ceo", "cfo") if r in roles)
    reporters: Dict[Tuple[str, str], Set[str]] = {}
    issuer_symbols: Dict[Tuple[str, str], Set[str]] = {}
    for b in usable:
        if b.role in officer_roles:
            for group in _issuer_groups(b):
                reporters.setdefault((b.role, group), set()).add(b.reporter)
                issuer_symbols.setdefault((b.role, group), set()).add(b.symbol)
    ambiguous_sets = {(role, frozenset(issuer_symbols[(role, g)]))
                      for (role, g), who in reporters.items() if len(who) >= 2}

    # Review round 8: a CEO or CFO any of whose titles — its lines, and every walk row of the
    # same person on the issuer — is not a SITTING officer's (the allow-list `_sitting_title`:
    # "Incoming CEO", "CEO (Resigned)", "CEO until 12/31/2026", a filing as a non-officer) is not
    # shown as the company's CEO / CFO: that person is refused (`role_uncertain`, one count per
    # person).
    uncertain = _role_uncertain_people(rows, usable, amend_since)
    if uncertain:
        ctx.reject("role_uncertain", len(uncertain))
        usable = [b for b in usable if (b.symbol, b.reporter) not in uncertain]

    if ambiguous_sets:
        ctx.reject("ambiguous_ceo", len(ambiguous_sets))
        ambiguous = {(role, sym) for role, syms in ambiguous_sets for sym in syms}
        usable = [b for b in usable if (b.role, b.symbol) not in ambiguous]

    # A member of Congress is never shown, and never shown role-only either (the role would
    # still point at the member). Review round 9: the whole PERSON goes, not the matching lines —
    # a member matched on ANY walk row (any code, any spelling of the name) or line is tied to
    # every line by identity key (the reporting CIK, the name), on every symbol. One count per
    # reporter dropped; never logged by name.
    congress_cache: Dict[str, bool] = {}

    def _member(name: Any) -> bool:
        if not isinstance(name, str):
            return False
        hit = congress_cache.get(name)
        if hit is None:
            hit = congress_cache[name] = R.is_congress_name(name)
        return hit

    bad_keys: Set[str] = set()
    for r in rows:
        if isinstance(r, dict) and _member(r.get("reportingName")):
            bad_keys |= _row_keys(r)
    for b in usable:
        if _member(b.name_raw):
            bad_keys |= _line_keys(b)
    kept = usable
    if bad_keys:
        congress_reporters = {b.reporter for b in usable if _line_keys(b) & bad_keys}
        if congress_reporters:
            ctx.reject("congress_name", len(congress_reporters))
            kept = [b for b in usable if b.reporter not in congress_reporters]
    return kept, amended_ciks


def _issuer_lines(ctx: _Ctx, sym: str, profile: Any, kept: Sequence[Any], amended_ciks: Set[str]
                  ) -> Optional[Tuple[Any, Optional[str], List[Any]]]:
    """One profiled symbol's lines after the profile-stage gates → ``(CompanyRef, the PROFILE's issuer CIK, the
    lines that may be summed)``, or None (counted). Shared by the roundups and the insider spotlight: the listing
    gate, THE amendment rule on the profile's issuer CIK, a line naming another issuer, the price PLAUSIBILITY
    check (the profile's price is only the reference — never a travelling figure) and a line with no trade date."""
    company = R.company_from_profile(profile, sym, purpose="insider")
    if isinstance(company, str):
        ctx.reject(company)
        return None
    issuer = normalize_cik(profile.get("cik"))
    # The amendment rule on the PROFILE's issuer CIK: the row's own lines may name no CIK
    # while the walk's amendment row (another share class, a blank symbol) names the issuer.
    if issuer is not None and issuer in amended_ciks:
        ctx.reject("amended_filing")
        return None
    # FMP keys the symbol on the filing's EDGAR folder: a public company filing as the
    # reporting owner of ANOTHER issuer carries its own ticker with that issuer's CIK. Such
    # a line is never this company's purchase; with no profile CIK to check against, no
    # line that names an issuer is trusted (fail closed).
    sym_lines = [b for b in kept if b.symbol == sym]
    if issuer is None and any(b.company_cik for b in sym_lines):
        ctx.reject("issuer_unverified")
        return None
    ref_price = profile.get("price")
    good = []
    for b in sym_lines:
        if b.company_cik is not None and b.company_cik != issuer:
            ctx.reject("issuer_mismatch")
            continue
        if not price_plausible(b.price, ref_price, require_reference=True):
            ref = _num(ref_price)
            ctx.reject("price_reference_missing" if ref is None or ref <= 0 else "price_implausible")
            continue
        if not b.transaction_date:
            ctx.reject("record_invalid")      # a buy with no trade date is not dated news
            continue
        good.append(b)
    return company, issuer, good


def _reporter_rows(ctx: _Ctx, company: Any, profile: Mapping[str, Any], issuer: Optional[str],
                   good: Sequence[Any], *, purchase_index: Mapping[str, Sequence[Dict[str, Any]]],
                   window_start: date, window_end: date) -> Iterable[Tuple[List[Any], Any]]:
    """Each reporter of one symbol's ``good`` lines, LARGEST first (then the reporter key) → ``(their lines, an
    InsiderPurchase or the reason they are refused)``. Lazy: the roundups stop at the first person who passes (one
    person per company row, the largest that passes every gate); the insider spotlight takes every one. The gates,
    in order: the whole person (`_unaccounted_purchases` over the PURCHASE window: a purchase the row would not hold
    → ``partial_person``), the dollar floor, the market-cap share, the per-row maximum, then `_insider_row`."""
    cap = _num(profile.get("marketCap"))
    sym = company.symbol
    by_reporter: Dict[str, List[Any]] = {}
    for b in good:
        by_reporter.setdefault(b.reporter, []).append(b)
    for reporter in sorted(by_reporter, key=lambda r: (-math.fsum(x.dollars for x in by_reporter[r]), r)):
        lines = by_reporter[reporter]
        amount = math.fsum(x.dollars for x in lines)
        if _unaccounted_purchases(purchase_index, lines, symbol=sym, issuer=issuer,
                                  window_start=window_start, window_end=window_end):
            # Review round 9: a purchase of this person on this issuer in the window that the
            # row does not hold (a line dropped above, or one the extractor skipped) — the row
            # would understate the filings. The person is refused, never re-summed.
            got: Any = "partial_person"
        elif amount < R.INSIDER_MIN_AMOUNT_USD:
            got = "below_dollar_floor"
        elif cap is None or amount > INSIDER_MAX_CAP_SHARE * cap:
            got = "over_cap_share"
        elif amount > R.INSIDER_MAX_AMOUNT_USD:
            got = "record_invalid"
        else:
            got = _insider_row(ctx, company, profile, lines)
        yield lines, got


async def _collect_insider(ctx: _Ctx, roles: Tuple[str, ...], none_reason: str, few_reason: str) -> Candidates:
    """One weekly Form 4 roundup (ceo_buys / insider_buys): every issuer's largest qualifying person of ``roles`` over
    the week, through every Form 4 gate, at most `R.INSIDER_MAX_ROWS` rows, the largest first. Owner decisions
    2026-11-20 ("3 - 5 companies at once"): fewer than `R.INSIDER_MIN_ROWS` rows is NO post — ``few_reason`` (one or
    two qualified; the chain falls through to the theme), ``none_reason`` (none)."""
    window_start, window_end = R.insider_window(ctx.run_date)
    key = f"{R.LEDGER_PREFIX}{ctx.series}:{window_start.isoformat()}"
    if key in ctx.exclude:                 # the week is posted: no feed, no profile call
        ctx.reject("already_posted")
        return ctx.done((), none_reason)
    _insider_series_open(ctx)

    rows = await _insider_walk(ctx, window_start)
    # The roundups' one window is both the purchase window and the look-back.
    kept, amended_ciks = _insider_gated_lines(ctx, rows, window_start=window_start, window_end=window_end,
                                              roles=roles, amend_since=window_start)

    ranked = rank_buy_symbols(kept, min_dollars=R.INSIDER_MIN_AMOUNT_USD)
    ranked_syms = [s for s, _total, _latest in ranked]
    below = {b.symbol for b in kept} - set(ranked_syms)
    if below:
        ctx.reject("below_dollar_floor", len(below))
    top = ranked_syms[:INSIDER_PROFILE_CANDIDATES]

    profiles = await _profiles(ctx, top) if top else {}
    purchase_index = _purchase_index(rows)             # the whole-person check (review round 9)
    purchases: List[Any] = []
    issuer_of: Dict[str, Optional[str]] = {}
    profile_cik: Dict[str, Optional[str]] = {}         # symbol → its profile's issuer CIK
    chosen: Dict[str, List[Any]] = {}                  # symbol → its row's person's lines
    for sym in top:
        profile = profiles.get(sym)
        got = _issuer_lines(ctx, sym, profile, kept, amended_ciks)
        if got is None:
            continue
        company, issuer, good = got
        profile_cik[sym] = issuer
        # The row's issuer identity for the share-class check: the CIK its own Form 4 lines
        # name (equal to the profile's after the check above), else none — the name decides.
        issuer_of[sym] = issuer if any(b.company_cik for b in good) else None
        # One person per company row: the largest reporter that passes every gate. When none
        # does, the LARGEST reporter's reason is counted (one count per symbol).
        best, first_reason = None, None
        for lines, row in _reporter_rows(ctx, company, profile, issuer, good, purchase_index=purchase_index,
                                         window_start=window_start, window_end=window_end):
            if not isinstance(row, str):
                best = row
                chosen[sym] = lines
                break
            first_reason = first_reason or row
        if best is None:
            ctx.reject(first_reason or "below_dollar_floor")
            continue
        purchases.append(best)

    ctx.check_profiles()
    purchases.sort(key=lambda r: (-r.amount_usd, r.company.name.casefold(), r.company.symbol))
    purchases = _one_row_per_issuer(ctx, purchases, issuer_of)
    # Only the rows a week can publish (plus spares) are re-read per ISSUER (the profile's CIK:
    # every share class, symbol spelling and blank symbol at once), every code — for an amendment
    # the P-only walk never returned, a stale read, or a role text that is not the sitting role's.
    # The same pass keeps one row per PERSON across issuers (`same_person`, review round 10).
    purchases = await _amendment_checked(ctx, purchases, chosen, profile_cik, rows, amend_since=window_start,
                                         purchase_window=(window_start, window_end))
    rows_out = tuple(purchases[: R.INSIDER_MAX_ROWS])
    if not rows_out:
        return ctx.done((), none_reason)
    if len(rows_out) < R.INSIDER_MIN_ROWS:
        # owner decisions 2026-11-20: never a one- or two-company Form 4 post — a quiet week posts the theme instead
        logger.info("company news %s: only %d compan%s qualified for the week of %s (fewer than %d) — no post: %s",
                    ctx.series, len(rows_out), "y" if len(rows_out) == 1 else "ies", window_start, R.INSIDER_MIN_ROWS,
                    few_reason)
        return ctx.done((), few_reason)
    try:
        record = R.InsiderBuysWeek(series=ctx.series, window_start=window_start,
                                   window_end=window_end, rows=rows_out)
    except ValueError as e:
        logger.warning("company news %s: week record refused (%s)", ctx.series, _describe(e))
        ctx.reject("record_invalid")
        return ctx.done((), none_reason)
    if R.ledger_key(record) != key:       # the pre-check above must have tested THIS key
        raise RuntimeError(f"ledger key drift: {R.ledger_key(record)} != {key}")
    return ctx.done((record,), None)


async def _collect_ceo_buys(ctx: _Ctx) -> Candidates:
    return await _collect_insider(ctx, ("ceo",), "ceo_none_qualified", "ceo_too_few_companies")


async def _collect_insider_buys(ctx: _Ctx) -> Candidates:
    # Reuses the memoized raw rows of ceo_buys (same window, same walk).
    return await _collect_insider(ctx, ("cfo", "director"), "insider_none_qualified", "insider_too_few_companies")


# ── insider_spotlight (the spotlight lane, owner decisions 2026-11-15) ────────────────────────────────────


def _newest_filed(rows: Sequence[Any], run_date: date) -> Optional[date]:
    """The newest filing day of the walk's rows ON OR BEFORE the run date (a later one is junk, never proof), or
    None: what proves how far the market-wide feed has come (`R.spotlight_window`)."""
    days = [d for d in (_day(r.get("filingDate")) for r in rows if isinstance(r, dict))
            if d is not None and d <= run_date]
    return max(days, default=None)


def _issuer_root(symbol: str) -> str:
    """A listing's ROOT symbol — share-class twins are one issuer for the spotlight's cooldown: the part before a
    class suffix ("BF-B" → "BF", "BRK-A" → "BRK"), and a five-letter Nasdaq line's first four ("GOOGL" → "GOOG",
    "LBRDA" → "LBRD", "WAFDP" → "WAFD") — except an OTC foreign line (`EARNINGS_FOREIGN_LETTERS`: often another
    company, CMPRF beside CMPR). An over-match refuses one candidate; a twin it misses is caught by the issuer's
    display name (`_spotlight_cooldown`). The rule itself is `R.issuer_root` (2026-11-16: the cross-lane rule (b) folds
    leads through the same rule, and script_service may not import this module)."""
    return R.issuer_root(symbol) or symbol


def _spotlight_cooldown(ctx: _Ctx) -> Tuple[Set[str], Set[str]]:
    """``(issuer roots, casefolded display names)`` the insider spotlight must not take again
    (``spotlight_cooldown``, `R.SPOTLIGHT_ISSUER_COOLDOWN_DAYS`):

    * every company of every insider spotlight ledger key among the recent refs (`R.insider_spotlight_key_parts`: a
      list's key names each of its companies once) whose window started within the cooldown of the run date — the key
      carries its window's start, never its run date;
    * every ``(symbol, display name)`` the script flow handed in (``ctx.spotlight_recent``: the insider
      spotlights ACCEPTED on runs dated within the cooldown — exact on the run date the key cannot carry).

    A symbol counts by its ROOT (`_issuer_root`): a share-class twin is the same issuer."""
    oldest = ctx.run_date - timedelta(days=R.SPOTLIGHT_ISSUER_COOLDOWN_DAYS)
    roots: Set[str] = set()
    names: Set[str] = set()
    for ref in ctx.exclude:
        parts = R.insider_spotlight_key_parts(ref)
        if parts is not None and parts[1] >= oldest:
            roots.update(_issuer_root(sym) for sym in parts[0])
    for sym, name in ctx.spotlight_recent:
        if sym:
            roots.add(_issuer_root(sym))
        if name:
            names.add(name.casefold())
    return roots, names


def _spotlight_company(ctx: _Ctx, people: Sequence[Any]) -> Optional[Any]:
    """`R.InsiderSpotlightCompany` of one issuer's checked people (`R.spotlight_person_order`: the largest leads, then up
    to `R.SPOTLIGHT_MAX_OTHERS` more). A group the row refuses whole is tried with its lead ALONE before it is refused
    (``record_invalid``): the others are a lower bound ("at least N other insiders"), so fewer is never a false claim."""
    lead, others = people[0], tuple(people[1:1 + R.SPOTLIGHT_MAX_OTHERS])
    attempts = [others, ()] if others else [()]
    for i, group in enumerate(attempts):
        try:
            return R.InsiderSpotlightCompany(lead=lead, others=group)
        except ValueError as e:
            if i + 1 < len(attempts):
                logger.warning("company news %s: %s row with %d other insider(s) refused (%s) — tried with its lead "
                               "alone", ctx.series, lead.company.symbol, len(group), _describe(e))
            else:
                logger.info("company news %s: %s row refused as record_invalid (%s)", ctx.series,
                            lead.company.symbol, _describe(e))
    ctx.reject("record_invalid")
    return None


def _spotlight_list(ctx: _Ctx, checked: Sequence[Tuple[Any, Sequence[Any]]]) -> List[Any]:
    """The insider list's rows (owner decisions 2026-11-20): each checked issuer's people as one
    `R.InsiderSpotlightCompany`, ONE person once across the list — a named person already in an earlier (larger) row is
    dropped from a later one (``same_person``: one man at two affiliated issuers is one buyer; the next person of that
    issuer then leads) — sorted by `R.spotlight_company_order` (the largest TOTAL first), at most `R.INSIDER_MAX_ROWS`."""
    def key(name: Optional[str]) -> Optional[str]:
        return " ".join(name.split()).casefold() if name else None

    seen: Set[str] = set()
    rows: List[Any] = []
    for _g, people in checked:
        kept: List[Any] = []
        for p in people:
            k = key(p.person_name)
            if k is not None and k in seen:
                ctx.reject("same_person")
                continue
            kept.append(p)
        if not kept:
            continue
        row = _spotlight_company(ctx, kept)
        if row is None:
            continue
        seen |= {k for k in (key(p.person_name) for p in row.people) if k is not None}
        rows.append(row)
    rows.sort(key=R.spotlight_company_order)
    return rows[: R.INSIDER_MAX_ROWS]


def _spotlight_posted(ctx: _Ctx, window_start: date) -> Set[str]:
    """Every symbol an insider list ledger key among ``ctx.exclude`` names for THIS window (`R.insider_spotlight_key_parts`,
    each company of a posted list keyed once) — each is ``already_posted`` before any profile call."""
    out: Set[str] = set()
    for ref in ctx.exclude:
        parts = R.insider_spotlight_key_parts(ref)
        if parts is not None and parts[1] == window_start:
            out.update(parts[0])
    return out


def _group_total(g: _SpotlightGroup) -> float:
    """An issuer's total BEFORE the per-person check: its first `SPOTLIGHT_PEOPLE_PER_ISSUER` people's amounts."""
    return math.fsum(row.amount_usd for row, _lines in g.people[:SPOTLIGHT_PEOPLE_PER_ISSUER])


async def _collect_insider_spotlight(ctx: _Ctx) -> Candidates:
    """The spotlight lane's insider LIST over the spotlight's window (owner decisions 2026-11-20: "each post or each
    image/video will have more than 2,3, ideally are 3 - 5 companies at once" — it replaces the one-issuer post of
    2026-11-15, B decisions 5-8): ONE record of `R.INSIDER_MIN_ROWS`..`R.INSIDER_MAX_ROWS` issuers, the largest total
    first; fewer than `R.INSIDER_MIN_ROWS` is no post (``insider_spotlight_too_few_companies`` — the chain falls
    through to the theme — or ``insider_spotlight_none_qualified`` with none).

    * **Window** (`R.spotlight_window`): from the day after the last window an earlier insider spotlight CHECKED
      (``ctx.spotlight_checked_through``, the script flow's ledger read of the recorded trail — C0, 2026-11-16; none
      within the cooldown → the run date − 7, and never earlier) to the day before the newest filing the market-wide
      walk holds on or before the run date — any row of the walk (it carries open-market purchases only: FMP filters
      the code server-side), and that filing proves the feed passed every earlier day: a Saturday run ends on
      Thursday, and Friday rolls into the next window. The window checked is handed back on the answer
      (``Candidates.checked_window``) — chosen, refused or nothing qualified — for the script flow to record.
      No new day to check: a quiet skip ``insider_spotlight_no_new_window`` while the feed is current
      (`R.spotlight_feed_current`: its newest filing at most two federal business days old — the Tuesday after a
      Monday holiday, at 10:00), else ``insider_feed_behind`` (UNAVAILABLE: an infrastructure outcome the digest
      alerts on, never "nothing qualified").
    * **Two windows** (the content review's major #2): the LOOK-BACK ``amend_since`` = min(window start, window end
      − 6) — never shorter than the roundups' week — for THE amendment rule (the company-level 4/A refusal), the
      sitting-role allow-list and the co-officer refusal, on the walk and on every per-issuer read; the PURCHASE
      window for what a person's row must hold whole (``partial_person``: an earlier purchase is another window's).
    * **Every Form 4 gate** of the roundups (`_insider_gated_lines`, `_issuer_lines`, `_reporter_rows`): the block-
      list and roster refusals, role-only inside the members-elect window, the symbol grammar, co-officers, two
      holders of one officer role, the whole-person Congress drop (a member never leads and never joins a row — the
      next person leads), the price PLAUSIBILITY check (the profile price never travels), the floor, the cap share;
      one listing per issuer (`_one_row_per_issuer`); every person through the per-issuer Form 4/A check
      (`_spotlight_checked`, keyed (symbol, reporter)); one person once across the list (`_spotlight_list`).
    * **Roles** CEO, CFO and director (`SPOTLIGHT_ROLES`); a row's lead is its largest qualifying person, up to
      `R.SPOTLIGHT_MAX_OTHERS` more of the same issuer.
    * **Ledger and cooldown**: an issuer an earlier list of THIS window named is ``already_posted``
      (`_spotlight_posted`: every company of a posted list is in its key once); one listed within
      `R.SPOTLIGHT_ISSUER_COOLDOWN_DAYS` — by its root symbol BEFORE any profile call, by its display name after — is
      ``spotlight_cooldown``.
    * **Rank**: issuers by their total before the check (`_group_total`), then the list by its checked totals
      (`R.spotlight_company_order`). The revenue line of the one-issuer post is retired with it."""
    none_reason = "insider_spotlight_none_qualified"
    few_reason = "insider_spotlight_too_few_companies"
    _insider_series_open(ctx)
    base = ctx.run_date - timedelta(days=R.INSIDER_WINDOW_DAYS)
    rows = await _insider_walk(ctx, base)
    newest = _newest_filed(rows, ctx.run_date)
    window = R.spotlight_window(ctx.run_date, ctx.spotlight_checked_through, newest)
    if window is None:
        if R.spotlight_feed_current(newest, ctx.run_date):
            # Nothing new to check yet (the last checked window already reaches the day before the newest filing, and
            # that filing is recent): a quiet content skip — the chain falls through, and the days stay unchecked for
            # the next spotlight. No window was checked, so none is recorded.
            logger.info("company news %s: no new day to check on %s — the last checked window ends %s and the newest "
                        "filing is %s (the feed is current): %s", ctx.series, ctx.run_date,
                        ctx.spotlight_checked_through, newest, "insider_spotlight_no_new_window")
            return ctx.done((), "insider_spotlight_no_new_window")
        raise MarketingNewsUnavailable(
            ctx.series, "insider_feed_behind",
            f"stage=insider_walk; the newest filing on or before {ctx.run_date} is {newest} — the feed has not "
            f"passed the window's first day (last checked window ends {ctx.spotlight_checked_through})")
    window_start, window_end = window
    amend_since = min(window_start, window_end - timedelta(days=R.INSIDER_WINDOW_DAYS - 1))
    if amend_since < base:
        rows = await _insider_walk(ctx, amend_since)
    ctx.checked_window = (window_start, window_end)
    logger.info("company news %s: window %s..%s (last checked window ends %s, newest filing %s), look-back from %s",
                ctx.series, window_start, window_end, ctx.spotlight_checked_through, newest, amend_since)
    kept, amended_ciks = _insider_gated_lines(ctx, rows, window_start=window_start, window_end=window_end,
                                              roles=SPOTLIGHT_ROLES, amend_since=amend_since)

    ranked = rank_buy_symbols(kept, min_dollars=R.INSIDER_MIN_AMOUNT_USD)
    ranked_syms = [s for s, _total, _latest in ranked]
    below = {b.symbol for b in kept} - set(ranked_syms)
    if below:
        ctx.reject("below_dollar_floor", len(below))
    # The ledger (every company of a posted list of this window) and the cooldown by ROOT symbol — before any profile
    # call.
    posted = _spotlight_posted(ctx, window_start)
    cooled_roots, cooled_names = _spotlight_cooldown(ctx)
    open_syms: List[str] = []
    for sym in ranked_syms:
        if sym in posted:
            ctx.reject("already_posted")
        elif _issuer_root(sym) in cooled_roots:
            ctx.reject("spotlight_cooldown")
        else:
            open_syms.append(sym)
    top = open_syms[:INSIDER_PROFILE_CANDIDATES]

    profiles = await _profiles(ctx, top) if top else {}
    purchase_index = _purchase_index(rows)
    groups: List[_SpotlightGroup] = []
    for sym in top:
        profile = profiles.get(sym)
        got = _issuer_lines(ctx, sym, profile, kept, amended_ciks)
        if got is None:
            continue
        company, issuer, good = got
        if company.name.casefold() in cooled_names:
            ctx.reject("spotlight_cooldown")         # the issuer, under another symbol or a twin the root missed
            continue
        people: List[Tuple[Any, List[Any]]] = []
        seen: Set[str] = set()
        for lines, row in _reporter_rows(ctx, company, profile, issuer, good, purchase_index=purchase_index,
                                         window_start=window_start, window_end=window_end):
            if isinstance(row, str):
                ctx.reject(row)                      # one count per person refused
                continue
            keys = _person_keys(lines)
            if keys & seen:                          # one person under two reporter keys: the larger stays
                ctx.reject("same_person")
                continue
            seen |= keys
            people.append((row, list(lines)))
        if not people:
            continue
        people.sort(key=lambda p: R.spotlight_person_order(p[0]))
        groups.append(_SpotlightGroup(symbol=sym, company=company, issuer=issuer,
                                      issuer_of=issuer if any(b.company_cik for b in good) else None,
                                      people=people))
    ctx.check_profiles()

    groups.sort(key=lambda g: (-_group_total(g), g.company.name.casefold(), g.symbol))
    leads = _one_row_per_issuer(ctx, [g.people[0][0] for g in groups], {g.symbol: g.issuer_of for g in groups})
    one_each = {r.company.symbol for r in leads}
    groups = [g for g in groups if g.symbol in one_each]
    checked = await _spotlight_checked(ctx, groups, rows, amend_since=amend_since,
                                       purchase_window=(window_start, window_end), keep=R.INSIDER_MAX_ROWS)
    companies = _spotlight_list(ctx, checked)
    if not companies:
        return ctx.done((), none_reason)
    if len(companies) < R.INSIDER_MIN_ROWS:
        # owner decisions 2026-11-20: never a one- or two-company insider post — the chain falls through to the theme.
        # Review of 2026-11-26: and the window is NOT recorded as checked — its one or two companies stay in reach, so
        # the next spotlight's window reaches back over these days (`spotlight_window`: from the last RECORDED window,
        # at most SPOTLIGHT_MAX_WINDOW_DAYS) and a company filed a day later can make them a list of three. Recording it
        # dropped every sub-threshold slice for good (each window is only 1-2 business days on the Tue/Thu/Sat cadence)
        ctx.checked_window = None
        logger.info("company news %s: only %d compan%s qualified for %s..%s (fewer than %d) — no post, the window is "
                    "left unchecked for the next spotlight: %s", ctx.series, len(companies),
                    "y" if len(companies) == 1 else "ies", window_start, window_end, R.INSIDER_MIN_ROWS, few_reason)
        return ctx.done((), few_reason)
    try:
        record = R.InsiderSpotlight(series="insider_spotlight", window_start=window_start, window_end=window_end,
                                    companies=tuple(companies))
    except ValueError as e:
        logger.warning("company news %s: list record refused (%s)", ctx.series, _describe(e))
        ctx.reject("record_invalid")
        return ctx.done((), none_reason)
    want = (f"{R.LEDGER_PREFIX}insider_spotlight:"
            f"{R.INSIDER_SPOTLIGHT_KEY_JOIN.join(r.company.symbol for r in companies)}:{window_start.isoformat()}")
    if R.ledger_key(record) != want:     # every company's own key part, exactly once, in row order
        raise RuntimeError(f"ledger key drift: {R.ledger_key(record)} != {want}")
    return ctx.done((record,), none_reason)


# ── thirteen_f ────────────────────────────────────────────────────────────────


class _RecordingFMP:
    """Delegates to the FMP client and keeps the raw 13F extracts the builder fetched, so the
    presented share counts can be checked against `_whale_common.thirteen_f_share_positions`
    over the very same rows (zero extra calls)."""

    def __init__(self, fmp: Any) -> None:
        self._fmp = fmp
        self.extracts: Dict[Tuple[int, int], Any] = {}

    def __getattr__(self, name: str) -> Any:
        return getattr(self._fmp, name)

    async def get_institutional_holdings(self, cik: str, year: int, quarter: int, *,
                                         strict: bool = False) -> Any:
        raw = await self._fmp.get_institutional_holdings(cik, year, quarter, strict=strict)
        self.extracts[(int(year), int(quarter))] = raw
        return raw


async def _build_registry(ctx: _Ctx, cik: str, year: int, quarter: int) -> Tuple[Any, Any, Any]:
    proxy = _RecordingFMP(ctx.fmp)
    built = await _builder.build_filing(
        proxy, cik, year, quarter, prev_quarter_available=True, actions=ctx.deps.actions,
        stored_unresolved={}, today=ctx.run_date,
    )
    prev = _tc_rules.previous_quarter(year, quarter)
    return built, proxy.extracts.get((year, quarter)), proxy.extracts.get(prev)


def _share_check(ctx: _Ctx, moves: List[Dict[str, Any]], raw_cur: Any, raw_prev: Any) -> List[Dict[str, Any]]:
    """Registry builds only: a presented share count must agree with the shared 13F share
    positions (`thirteen_f_share_positions`: options / principal rows dropped, per-manager rows
    summed, the latest accession wins) of the same raw extracts. The builder diffs by CUSIP;
    the helper is symbol-keyed, so a symbol it does not carry (resolved from a CUSIP by the
    builder) is left to the builder. Any disagreement drops the move (`degraded_build`)."""
    if not isinstance(raw_cur, list) or not isinstance(raw_prev, list):
        if moves:
            ctx.reject("degraded_build", len(moves))
        return []

    def by_symbol(raw: Any) -> Dict[str, float]:
        out: Dict[str, float] = {}
        for sym, pos in thirteen_f_share_positions(raw).items():
            k = _sym_key(sym)
            out[k] = out.get(k, 0.0) + float(pos.get("shares") or 0)
        return out

    cur, prev = by_symbol(raw_cur), by_symbol(raw_prev)
    kept = []
    for m in moves:
        k = _sym_key(m.get("symbol"))
        shares, prev_shares = _num(m.get("shares")), _num(m.get("prev_shares"))
        ok = True
        if m["move"] in ("newly_reported", "increased", "decreased"):
            if k in cur and (shares is None or abs(cur[k] - shares) >= 1.0):
                ok = False
        if m["move"] == "newly_reported" and prev.get(k, 0.0) > 0:
            ok = False                      # the symbol was on last quarter's book after all
        if m["move"] == "no_longer_reported":
            if cur.get(k, 0.0) > 0:
                ok = False                  # still on this quarter's book under that symbol
            elif k in prev and (prev_shares is None or abs(prev[k] - prev_shares) >= 1.0):
                ok = False
        if ok:
            kept.append(m)
        else:
            ctx.reject("degraded_build")
            logger.warning("company news thirteen_f: %s %s share count disagrees with the shared "
                           "13F positions — move dropped", m["move"], k or "?")
    return kept


def _non_share_issuers(raw: Any) -> Set[str]:
    """Issuer keys of a raw 13F extract's OPTION and NOTE rows (`is_13f_non_share_row`: put, call,
    PRN): the full CUSIP, its 6-character issuer prefix and the symbol."""
    out: Set[str] = set()
    for r in raw if isinstance(raw, list) else ():
        if not isinstance(r, dict) or not is_13f_non_share_row(r):
            continue
        cusip = _tc_rules.normalize_cusip(r.get("securityCusip"))
        if cusip:
            out.update((f"cusip:{cusip}", f"issuer:{cusip[:6]}"))
        sym = _sym_key(r.get("symbol"))
        if sym:
            out.add(f"sym:{sym}")
    return out


def _option_moves_dropped(ctx: _Ctx, moves: List[Dict[str, Any]], counts: Any, raw_cur: Any,
                          raw_prev: Any) -> Tuple[List[Dict[str, Any]], Any]:
    """Review round 9 (#7): the builder diffs SHARE rows only, so "No longer reported: X" was
    published while the same 13F still lists X as call options or notes, and "Newly reported: X"
    while the previous one did. A move whose issuer — its CUSIP, CUSIP issuer prefix or symbol —
    has an option / note row on the current OR the previous book is dropped
    (`move_has_options`), whatever its kind. A dropped newly / no-longer-reported move also leaves
    the builder's count of its kind (the hook's "no longer reports N holdings" must not count a
    position the filing still reports); more / fewer shares counts stay (still true of shares).
    Returns (moves kept, counts)."""
    out_counts = dict(counts) if isinstance(counts, Mapping) else counts   # junk stays junk (record_invalid)
    options = _non_share_issuers(raw_cur) | _non_share_issuers(raw_prev)
    if not options:
        return moves, out_counts
    kept = []
    for m in moves:
        cusip, sym = _tc_rules.normalize_cusip(m.get("cusip")), _sym_key(m.get("symbol"))
        keys = ({f"cusip:{cusip}", f"issuer:{cusip[:6]}"} if cusip else set()) | ({f"sym:{sym}"} if sym else set())
        if not keys & options:
            kept.append(m)
            continue
        ctx.reject("move_has_options")
        if m["move"] in ("newly_reported", "no_longer_reported") and isinstance(out_counts, dict):
            n = out_counts.get(m["move"])
            if isinstance(n, int) and not isinstance(n, bool) and n > 0:
                out_counts[m["move"]] = n - 1
        logger.info("company news thirteen_f: %s %s — the issuer has option / note rows on the current or "
                    "previous book; move dropped", m["move"], sym or "?")
    return kept, out_counts


def _material_floor(total: float) -> float:
    """The value a newly reported / no-longer-reported move must reach to be news."""
    return max(THIRTEEN_F_MATERIAL_MIN_USD, THIRTEEN_F_MATERIAL_SHARE * total)


def _previous_values(raw_prev: Any, *, cik: str, prev_end: date) -> Dict[str, float]:
    """CUSIP → the position's value on the PREVIOUS quarter's book, normalised exactly as the
    builder normalised it for its diff (`normalize_rows`: SH rows of that period and CIK, summed
    per accession, the latest accession wins). An exit's materiality is read from it. ``{}``
    (every exit below the floor) when the extract is missing — `_share_check` has then dropped
    every move already — or cannot be normalised (WARNING)."""
    if not isinstance(raw_prev, list):
        return {}
    try:
        norm = _builder.normalize_rows(raw_prev, expected_period_end=prev_end, expected_cik=cik,
                                       log_ctx=f"cik={cik} previous book (materiality)")
    except Exception as e:  # noqa: BLE001 — pure, but never let it sink the filer
        logger.warning("company news thirteen_f: cik=%s previous book unreadable for materiality — "
                       "no exit shown (%s)", cik, _describe(e))
        return {}
    out: Dict[str, float] = {}
    for r in norm.rows:
        cusip, value = r.get("cusip"), _num(r.get("value"))
        if isinstance(cusip, str) and value is not None:
            out[cusip] = value
    return out


def _previous_shares(raw_prev: Any, *, cik: str, prev_end: date) -> Dict[str, float]:
    """CUSIP → the position's share count on the PREVIOUS quarter's book, normalised exactly as the builder
    normalised it for its diff (`normalize_rows`): what a split restatement multiplied
    (`_split_window_taint`, the review of 2026-10-16). ``{}`` when the extract is missing or cannot be
    normalised (WARNING) — then no split in the window can be shown as restated (fail closed)."""
    if not isinstance(raw_prev, list):
        return {}
    try:
        norm = _builder.normalize_rows(raw_prev, expected_period_end=prev_end, expected_cik=cik,
                                       log_ctx=f"cik={cik} previous book (split re-check)")
    except Exception as e:  # noqa: BLE001 — pure, but never let it sink the filer
        logger.warning("company news thirteen_f: cik=%s previous book unreadable for the split re-check — "
                       "no split shown as restated (%s)", cik, _describe(e))
        return {}
    return _shares_by_cusip(norm.rows)


def _shares_by_cusip(rows: Any) -> Dict[str, float]:
    """CUSIP → shares over a book's rows (builder rows: a normalised extract or a stored build's holdings). A
    CUSIP on two rows is left out — ambiguous, so no split of it can be shown as restated (fail closed)."""
    out: Dict[str, float] = {}
    repeated: Set[str] = set()
    for r in rows if isinstance(rows, list) else ():
        if not isinstance(r, Mapping):
            continue
        cusip, shares = _tc_rules.normalize_cusip(r.get("cusip")), _num(r.get("shares"))
        if cusip is None or shares is None or shares <= 0:
            continue
        if cusip in out:
            repeated.add(cusip)
        out[cusip] = shares
    for cusip in repeated:
        del out[cusip]
    return out


def _pre_gate_move(ctx: _Ctx, m: Mapping[str, Any], total: float) -> Optional[Dict[str, Any]]:
    """The pure gates of one diff row, before any profile call. None = dropped (counted)."""
    move = m.get("move")
    if move not in R.THIRTEEN_F_MOVES:
        return None                          # unchanged / corporate_action are never shown
    sym = R.canonical_symbol(m.get("symbol"))
    if sym is None:
        ctx.reject("move_not_routable")
        return None
    shares, prev, value = _num(m.get("shares")), _num(m.get("prev_shares")), _num(m.get("value"))
    if move in ("newly_reported", "increased", "decreased"):
        if value is None or value <= 0 or shares is None or shares <= 0:
            ctx.reject("record_invalid")
            return None
        if value > total:
            ctx.reject("move_value_exceeds_total")
            return None
    if move in ("increased", "decreased"):
        if prev is None or prev <= 0 or (move == "increased") != (shares > prev) or shares == prev:
            ctx.reject("record_invalid")
            return None
        if abs(shares - prev) / prev < THIRTEEN_F_MIN_CHANGE:
            ctx.reject("move_too_small")
            return None
    if move == "no_longer_reported":
        if prev is None or prev <= 0:
            ctx.reject("record_invalid")
            return None
    prev_value = None
    if move in ("newly_reported", "no_longer_reported"):
        basis = value if move == "newly_reported" else _num(m.get("prev_value"))
        if basis is None or basis < _material_floor(total):
            ctx.reject("move_immaterial")
            return None
        if move == "no_longer_reported":
            prev_value = basis              # kept: the exit's size (`ThirteenFMove.prev_value_usd`)
    if move == "no_longer_reported":
        shares, value = None, None
    if move == "newly_reported":
        prev = None
    flow = abs((shares or 0.0) - (prev or 0.0)) * (value or 0.0) / shares if shares else 0.0
    return {"symbol": sym, "name": str(m.get("name") or ""), "move": move, "shares": shares,
            "prev_shares": prev, "value": value, "prev_value": prev_value,
            "newly_listed": bool(m.get("newly_listed")), "flow": flow, "cusip": m.get("cusip")}


# One position on a 13F book, as the share-class check reads it: (symbol key, the 9-character
# CUSIP or None, folded display name or None).
_BookEntry = Tuple[str, Optional[str], Optional[str]]


def _issuer_prefix(cusip: Optional[str]) -> Optional[str]:
    """The CUSIP's 6-character issuer number (GOOG 02079K107 and GOOGL 02079K305 share
    "02079K"), or None."""
    return cusip[:6] if cusip else None


def _name_key(name: Any) -> Optional[str]:
    shown = R.display_company_name(name)
    return shown.casefold() if shown else None


def _book_entry(symbol: Any, cusip: Any, name: Any) -> _BookEntry:
    return (_sym_key(symbol), _tc_rules.normalize_cusip(cusip), _name_key(name))


def _book_of_holdings(holdings: Any) -> List[_BookEntry]:
    """The builder's current book (`BuiltFiling.holdings`: dicts with cusip/symbol/name)."""
    return [_book_entry(h.get("symbol"), h.get("cusip"), h.get("name"))
            for h in holdings or () if isinstance(h, Mapping)]


def _book_of_raw(raw: Any) -> List[_BookEntry]:
    """A raw FMP 13F extract's SHARE rows (options and principal-amount rows are not
    positions, `is_13f_non_share_row`)."""
    if not isinstance(raw, list):
        return []
    return [_book_entry(r.get("symbol"), r.get("securityCusip"), r.get("nameOfIssuer"))
            for r in raw if isinstance(r, dict) and not is_13f_non_share_row(r)]


def _book_of_club(holdings: Any) -> List[_BookEntry]:
    """A club filer's stored current book (`ClubHoldingResponse`: name and symbol, no CUSIP)."""
    return [_book_entry(getattr(h, "symbol", None), None, getattr(h, "name", None))
            for h in holdings or ()]


def _class_overlaps(ctx: _Ctx, moves: List[Dict[str, Any]], *, current: Sequence[_BookEntry],
                    previous: Sequence[_BookEntry]) -> List[Dict[str, Any]]:
    """The issuer, not the symbol, is the unit. A move is dropped (`share_class_overlap`) when
    its issuer — the CUSIP issuer prefix, or the display name — sits on the book under ANOTHER
    symbol: "No longer reported: Alphabet" is false while GOOGL is still held after GOOG left,
    and "Increased: Alphabet" about one class misstates the issuer's position. An exit is
    checked against the current book; every other move against both books."""
    kept = []
    for m in moves:
        sym, cusip, name = _sym_key(m.get("symbol")), _tc_rules.normalize_cusip(m.get("cusip")), _name_key(m.get("name"))
        pre = _issuer_prefix(cusip)
        books = (current,) if m["move"] == "no_longer_reported" else (current, previous)
        # The move's own security — its symbol, or its CUSIP (a raw row's symbol may be empty;
        # the builder resolved it) — is never "another class" of itself.
        clash = any(
            e_sym != sym and (cusip is None or e_cusip != cusip)
            and ((pre is not None and _issuer_prefix(e_cusip) == pre) or (name is not None and e_name == name))
            for book in books for e_sym, e_cusip, e_name in book
        )
        if clash:
            ctx.reject("share_class_overlap")
            logger.info("company news thirteen_f: %s %s shares an issuer with another class on the "
                        "book — move dropped", m["move"], sym or "?")
        else:
            kept.append(m)
    return kept


#: The record's kind order (the moves tuple reads new, exits, more, fewer — each largest first).
_KIND_RANK = {"newly_reported": 0, "no_longer_reported": 1, "increased": 2, "decreased": 3}


def _move_size(m: Mapping[str, Any]) -> float:
    """A gated move's dollar size, the measure the templates rank by: a new holding's value; an
    exit's value on the PREVIOUS book; more / fewer shares = the shares that changed at the
    filing's value per share (``flow``). Every gated move has one (> 0)."""
    if m["move"] == "newly_reported":
        return m["value"] or 0.0
    if m["move"] == "no_longer_reported":
        return m["prev_value"] or 0.0
    return m["flow"]


def _size_order(m: Mapping[str, Any]) -> Tuple[Any, ...]:
    return (-_move_size(m), _KIND_RANK[m["move"]], m["symbol"])


def _move_order(m: Mapping[str, Any]) -> Tuple[Any, ...]:
    """The order of the record's moves: by kind, then largest first."""
    return (_KIND_RANK[m["move"]], -_move_size(m), m["symbol"])


def _spotlight_gated(ctx: _Ctx, moves: Sequence[Mapping[str, Any]], *, cik: str, period: str,
                     omit_counts: FrozenSet[str]) -> List[Mapping[str, Any]]:
    """The 13F SPOTLIGHT's own per-move gates over pre-gated moves, BEFORE any cut (review of the 13F list,
    2026-11-19: they used to run in `_spotlight_holding`, after the roundup's kind-balanced cut to
    `R.THIRTEEN_F_MAX_MOVES`, so a filing's five largest eligible changes were never all in the record — two of each
    kind were, and a 10-25% trim held a slot it was then dropped from). A more / fewer move stays only when the filing
    VOUCHES for it — neither kind's count withheld (``omit_counts``) and the split re-check run over the filing
    (`ctx.split_checked`; else ``split_unchecked``) — when no split the build restated touches it (``split_restated``:
    its symbol among the restated, or any for a club filing, `ctx.split_restated_any`), and when its share count moved
    by at least `R.THIRTEEN_F_SPOTLIGHT_MIN_CHANGE` (``spotlight_change_too_small``). New and exited moves pass. Each
    move refused is counted; refusing one never refuses another. `_spotlight_holding` keeps the same gates as its
    backstop."""
    key = (cik, period)
    vouched = not (omit_counts & _SPLIT_SENSITIVE_MOVES) and key in ctx.split_checked
    restated = ctx.split_checked.get(key, frozenset())
    kept: List[Mapping[str, Any]] = []
    for m in moves:
        if m["move"] in _SPLIT_SENSITIVE_MOVES:
            if not vouched:
                ctx.reject("split_unchecked")
                continue
            if key in ctx.split_restated_any or m["symbol"] in restated:
                ctx.reject("split_restated")
                logger.info("company news %s: cik=%s %s %s %s — its split was restated by the build; the previous "
                            "count is not the previous 13F's (split_restated)", ctx.series, cik, period, m["move"],
                            m["symbol"])
                continue
            if not R.thirteen_f_change_clear(m["shares"], m["prev_shares"]):
                ctx.reject("spotlight_change_too_small")
                continue
        kept.append(m)
    return kept


def _cut(moves: Sequence[Mapping[str, Any]], limit: int) -> List[Mapping[str, Any]]:
    """At most ``limit`` moves, in the record's order (`_move_order`), chosen by dollar size alone (`_size_order`) — in
    BOTH 13F series (review 2026-11-20: the roundup's kind reserve is gone), so a record holds the filing's largest
    eligible moves — the ones its ranked list shows, whatever their kinds."""
    return sorted(sorted(moves, key=_size_order)[:limit], key=_move_order)


def _delisted_day(raw: Any) -> Optional[date]:
    """A delisted-companies row's ``delistedDate`` as a date, or None (absent / malformed — never a guess)."""
    return _day(raw) if isinstance(raw, str) else None


async def _fetch_delisted_since(fmp: Any, since: date) -> FrozenSet[str]:
    """Every canonical symbol FMP's delisted-companies list dates on or after ``since`` (future-dated, scheduled rows
    included: a pending delisting is as unclear as a done one), read newest first, page by page, until a page's
    oldest row is older than ``since``. RuntimeError when the list cannot vouch for the whole span — an empty page 0
    (FMP's list is never empty), a non-list page, a page with no readable date, a page that ends before reaching
    ``since``, or `CLOSED_OUT_DELISTED_MAX_PAGES` pages without reaching it: the caller fails closed."""
    out: Set[str] = set()
    for page in range(CLOSED_OUT_DELISTED_MAX_PAGES):
        rows = await fmp.get_delisted_companies(page, CLOSED_OUT_DELISTED_PAGE_SIZE)
        if not isinstance(rows, list):
            raise RuntimeError(f"delisted-companies page {page} is a {type(rows).__name__}, not a list")
        if not rows:
            raise RuntimeError(f"delisted-companies page {page} is empty before reaching {since}")
        oldest: Optional[date] = None
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            day = _delisted_day(row.get("delistedDate"))
            if day is None:
                continue
            oldest = day if oldest is None else min(oldest, day)
            sym = R.canonical_symbol(row.get("symbol"))
            if sym is not None and day >= since:
                out.add(sym)
        if oldest is None:
            raise RuntimeError(f"delisted-companies page {page} has no readable delistedDate")
        if oldest < since:
            return frozenset(out)
        if len(rows) < CLOSED_OUT_DELISTED_PAGE_SIZE:
            raise RuntimeError(f"delisted-companies list ends at page {page} ({oldest}) before reaching {since}")
    raise RuntimeError(f"delisted-companies list not back to {since} within {CLOSED_OUT_DELISTED_MAX_PAGES} pages")


async def _delisted_since(ctx: _Ctx, since: date) -> Optional[FrozenSet[str]]:
    """`_fetch_delisted_since` memoized (`CLOSED_OUT_DELISTED_TTL_SECONDS`, shared by both 13F series), or None when it
    cannot answer — the read failed, timed out, or too little of the call's budget is left (WARNING): the caller then
    drops every closed-out row (fail closed). Never raises but for cancellation."""
    left = ctx.left()
    if left < MIN_STEP_SECONDS:
        logger.warning("company news %s: no budget left for the delisted-companies read (%.1fs) — the buy-out check "
                       "cannot run", ctx.series, left)
        return None
    key = ("delisted_since", since.isoformat())
    try:
        return await asyncio.wait_for(
            _shared(key, CLOSED_OUT_DELISTED_TTL_SECONDS, lambda: _fetch_delisted_since(ctx.fmp, since)),
            timeout=min(left, CLOSED_OUT_DELISTED_TIMEOUT_SECONDS))
    except asyncio.CancelledError:
        raise
    except Exception as e:  # noqa: BLE001 — every failure is "cannot vouch": the caller fails closed
        logger.warning("company news %s: the delisted-companies list could not be read back to %s (%s) — the buy-out "
                       "check cannot run", ctx.series, since, _describe(e))
        return None


async def _closed_out_checked(ctx: _Ctx, final: List[Tuple[Mapping[str, Any], Any]],
                              profiles: Mapping[str, Any], *, cik: str, period: str,
                              prev_end: date) -> List[Tuple[Mapping[str, Any], Any]]:
    """The BUY-OUT CHECK (news-v23, owner 2026-11-19 — "Closed out + buy-out check"): a closed-out holding (a move
    ``no_longer_reported``) is kept only when its company is still trading — its profile says
    ``isActivelyTrading`` exactly True (``closed_out_not_trading`` otherwise: False, absent, junk) and FMP's
    delisted-companies list holds no row of its symbol dated on or after the quarter's first day
    (``closed_out_delisted``); when that list cannot be read back so far, every closed-out holding of the filing is
    dropped (``closed_out_unchecked``). Each drop is counted and logged at WARNING with the filer and the symbol; a
    company bought out, delisted or merged during or after the quarter is never called "closed out". Every other move
    passes untouched, in order."""
    kept: List[Tuple[Mapping[str, Any], Any]] = []
    for m, mv in final:
        if m["move"] != "no_longer_reported":
            kept.append((m, mv))
            continue
        trading = (profiles.get(m["symbol"]) or {}).get("isActivelyTrading")
        if trading is not True:
            ctx.reject("closed_out_not_trading")
            logger.warning("company news %s: cik=%s %s closed-out %s — its profile does not say it is actively "
                           "trading (%r): dropped, never called closed out (closed_out_not_trading)", ctx.series, cik,
                           period, m["symbol"], trading if trading is None or isinstance(trading, bool) else "junk")
            continue
        kept.append((m, mv))
    if not any(m["move"] == "no_longer_reported" for m, _mv in kept):
        return kept
    since = prev_end + timedelta(days=1)
    delisted = await _delisted_since(ctx, since)
    out: List[Tuple[Mapping[str, Any], Any]] = []
    for m, mv in kept:
        if m["move"] != "no_longer_reported":
            out.append((m, mv))
        elif delisted is None:
            ctx.reject("closed_out_unchecked")
            logger.warning("company news %s: cik=%s %s closed-out %s — the delisted-companies list could not be "
                           "checked: dropped (closed_out_unchecked)", ctx.series, cik, period, m["symbol"])
        elif m["symbol"] in delisted:
            ctx.reject("closed_out_delisted")
            logger.warning("company news %s: cik=%s %s closed-out %s — FMP lists it delisted on or after %s (bought "
                           "out, merged or delisted): dropped, never called closed out (closed_out_delisted)",
                           ctx.series, cik, period, m["symbol"], since)
        else:
            out.append((m, mv))
    return out


async def _filing_record(
    ctx: _Ctx, *, filer_name: Any, cik: str, filer_symbol: Any, period: str, period_end: date,
    prev_end: date, filed_on: Optional[date], amended_on: Optional[date], total: Any,
    position_count: Any, raw_moves: Sequence[Mapping[str, Any]], counts: Mapping[str, Any],
    current_book: Sequence[_BookEntry], previous_book: Sequence[_BookEntry],
    omit_counts: FrozenSet[str] = frozenset(), roundup: bool = True,
) -> Optional[Any]:
    """``omit_counts`` (Batch 1): the kinds whose count the record must not state at all — their key
    is left out of ``counts`` (`ThirteenFFiling` takes a subset; the templates say nothing of a kind
    they are not given), never written as 0, which would be a false "no holding increased". Their
    moves were dropped by the caller (`_split_unchecked_dropped`).

    ``roundup`` (the spotlight lane, 2026-11-15): since the 13F list (review 2026-11-19) the spotlight's record
    (False) runs its own per-move gates BEFORE the cuts (`_spotlight_gated`); both lanes' cuts are by dollar size alone
    (`_cut`; review 2026-11-20: the roundup's kind reserve is gone), so each record holds the filing's largest ELIGIBLE
    moves. Either way (news-v23, owner 2026-11-19): a closed-out holding passes the
    BUY-OUT CHECK (`_closed_out_checked`) before the final cut, and a filing left with fewer than
    `R.THIRTEEN_F_LIST_MIN_MOVES` moves yields no record (``too_few_holdings``) — never a single-holding 13F post."""
    total_f = _num(total)
    if total_f is None or total_f <= 0 or not isinstance(position_count, int) or isinstance(position_count, bool):
        ctx.reject("record_invalid")
        return None
    pre = [g for g in (_pre_gate_move(ctx, m, total_f) for m in raw_moves) if g is not None]
    pre = _class_overlaps(ctx, pre, current=current_book, previous=previous_book)
    if not roundup:
        pre = _spotlight_gated(ctx, pre, cik=cik, period=period, omit_counts=omit_counts)
    # Both lanes: chosen by dollar size alone (`_cut`). Profiled in record order.
    presented = _cut(pre, THIRTEEN_F_MAX_PROFILED)
    if not presented:
        return None
    # The filer's own ticker rides in the same profile batch: it is kept only when it passes
    # the listing gate (ARK's "ARKK" is an ETF — no ETF data of any kind, §1).
    own = R.canonical_symbol(filer_symbol) if filer_symbol else None
    profiles = await _profiles(ctx, [m["symbol"] for m in presented] + ([own] if own else []))
    if own is not None and isinstance(R.company_from_profile(profiles.get(own), own, purpose="listing"), str):
        own = None
    # Review round 9 (#9 / #23): the logo and chip are the FILER's own listing only — the ticker's
    # profile CIK must be the 13F filer's CIK ("IEP" on Carl C. Icahn's own 13F was another
    # entity's logo). No profile CIK → no chip (fail closed).
    if own is not None and R.cik10((profiles.get(own) or {}).get("cik")) != cik:
        logger.info("company news thirteen_f: cik=%s filer symbol %s is not the filer's own listing (its "
                    "profile CIK differs) — no logo or chip", cik, own)
        own = None

    final: List[Tuple[Mapping[str, Any], Any]] = []
    for m in presented:
        profile = profiles.get(m["symbol"])
        company = R.company_from_profile(profile, m["symbol"], purpose="listing")
        if isinstance(company, str):
            ctx.reject(company)
            continue
        listed_on = None
        if m["move"] == "newly_reported":
            ipo = _day(profile.get("ipoDate"))
            newly_listed = ipo is not None and ipo > prev_end
            # A listing after the quarter's end cannot be a holding AT its end: the date is
            # wrong, and "it was listed in <month>" would be too (`ThirteenFFiling` refuses it).
            if ipo is None or newly_listed != m["newly_listed"] or ipo > period_end:
                ctx.reject("move_unknown_listing")
                continue
            listed_on = ipo if newly_listed else None
        try:
            mv = R.ThirteenFMove(company=company, move=m["move"], shares=m["shares"],
                                 prev_shares=m["prev_shares"], value_usd=m["value"], listed_on=listed_on,
                                 prev_value_usd=m["prev_value"])
        except ValueError as e:
            logger.info("company news thirteen_f: move %s refused (%s)", m["symbol"], _describe(e))
            ctx.reject("record_invalid")
            continue
        final.append((m, mv))
    # Review round 9 (#8): the PROFILED name against the books — an exit's SEC name ("NEWS CORP
    # NEW") never met the club book's display name ("News Corporation") of the class still held.
    # A move whose profiled display name sits on the book under ANOTHER symbol is dropped
    # (`share_class_overlap`): an exit against the current book, every other move against both.
    # Review round 10: the move's OWN security — its CUSIP — is never another class of itself (a
    # ticker rename, SQ → XYZ, leaves the old symbol on the previous raw extract), as in
    # `_class_overlaps`. A club move carries no CUSIP: the name decides.
    tied = []
    for m, mv in final:
        name = mv.company.name.casefold()
        sym, cusip = _sym_key(m["symbol"]), _tc_rules.normalize_cusip(m.get("cusip"))
        books = (current_book,) if m["move"] == "no_longer_reported" else (current_book, previous_book)
        if any(e_name == name and e_sym != sym and (cusip is None or e_cusip != cusip)
               for book in books for e_sym, e_cusip, e_name in book):
            ctx.reject("share_class_overlap")
            logger.info("company news thirteen_f: %s %s — its company is on the book under another class; "
                        "move dropped", m["move"], m["symbol"])
        else:
            tied.append((m, mv))
    final = tied
    # Backstop on the profiled names: two presented moves that resolve to one display name (two
    # share classes the book check could not tie — a club book has no CUSIPs) would print one
    # company twice, possibly with opposite moves. Every move of such a name is dropped.
    by_name = Counter(mv.company.name.casefold() for _m, mv in final)
    for _m, mv in final:
        if by_name[mv.company.name.casefold()] > 1:
            ctx.reject("share_class_overlap")
    final = [(m, mv) for m, mv in final if by_name[mv.company.name.casefold()] == 1]
    # news-v23 (owner 2026-11-19, "Closed out + buy-out check"): a closed-out holding whose company stopped trading
    # during or after the quarter is dropped BEFORE the final cut, so the next eligible move takes its place
    final = await _closed_out_checked(ctx, final, profiles, cik=cik, period=period, prev_end=prev_end)
    # The final cut is by dollar size too, then the record's kind order.
    move_of = {id(m): mv for m, mv in final}
    kept_moves = _cut([m for m, _mv in final], R.THIRTEEN_F_MAX_MOVES)
    moves = tuple(move_of[id(m)] for m in kept_moves)
    if not moves:
        return None
    if len(moves) < R.THIRTEEN_F_LIST_MIN_MOVES:
        # news-v23 (owner 2026-11-19, "the range from 2 to 6 are okay"): never a single-holding 13F post, in either
        # lane — the walk moves on to the next filer (the 13F spotlight checks its own count again after its backstop
        # gates: `_spotlight_holding`)
        ctx.reject("too_few_holdings")
        logger.info("company news %s: cik=%s %s has %d eligible holding(s), fewer than %d — no 13F post "
                    "(too_few_holdings)", ctx.series, cik, period, len(moves), R.THIRTEEN_F_LIST_MIN_MOVES)
        return None

    count_pairs = []
    for kind in R.THIRTEEN_F_MOVES:
        if kind in omit_counts:
            continue
        n = counts.get(kind, 0) if isinstance(counts, Mapping) else None
        if not isinstance(n, int) or isinstance(n, bool) or n < 0:
            ctx.reject("record_invalid")
            return None
        count_pairs.append((kind, n))
    try:
        return R.ThirteenFFiling(
            series="thirteen_f", filer_name=filer_name, filer_cik=cik, filer_symbol=own,
            period=period, period_end=period_end, filed_on=filed_on, amended_on=amended_on,
            total_value_usd=total_f, position_count=position_count, moves=moves,
            counts=tuple(count_pairs),
        )
    except ValueError as e:
        logger.warning("company news thirteen_f: filing %s %s refused (%s)", cik, period, _describe(e))
        ctx.reject("record_invalid")
        return None


def _counts_of(obj: Any) -> Dict[str, Any]:
    if isinstance(obj, Mapping):
        return dict(obj)
    return {k: getattr(obj, k, None) for k in R.THIRTEEN_F_MOVES} if obj is not None else {}


def _field_of(obj: Any, key: str) -> Any:
    """``obj[key]`` of a mapping, else the attribute (a stored row's dict or a response model's object); None when
    absent."""
    return obj.get(key) if isinstance(obj, Mapping) else getattr(obj, key, None)


def _is_zero_count(n: Any) -> bool:
    """A count that is a plain integer 0 — anything else (missing, a bool, junk, a positive count) is NOT zero."""
    return isinstance(n, int) and not isinstance(n, bool) and n == 0


def _mark_counts_partial(ctx: _Ctx, cik: str, period: str, why: Sequence[str]) -> None:
    """Record (``ctx.counts_partial``) that a filing's four change counts may not be every holding change it reports,
    with ``why`` (the reasons found, never empty when called): the 13F spotlight then states no count as the
    filing's (fail closed, INFO — a degraded wording, never a refused filing)."""
    ctx.counts_partial.add((cik, period))
    logger.info("company news %s: cik=%s %s — its change counts may not be every holding change it reports (%s); "
                "a 13F spotlight list never states them as the filing's", ctx.series, cik, period, "; ".join(why))


#: The moves a held-through stock split can fake (Batch 1, 2026-10-15): the split check restates only a
#: holding on BOTH books, so only "more shares" / "fewer shares" depend on it. Newly reported and exited
#: moves never went through it and stay.
_SPLIT_SENSITIVE_MOVES: FrozenSet[str] = frozenset({"increased", "decreased"})


def _more_fewer_dropped(ctx: _Ctx, moves: List[Dict[str, Any]], *, reason: str, why: str, cik: str,
                        period: str) -> Tuple[List[Dict[str, Any]], FrozenSet[str]]:
    """Drop every increased / decreased move (``reason``, one per move) and return both kinds as the counts
    `_filing_record` must OMIT — the record then states nothing about them, never a count of 0 (a false "no
    holding grew"). Never one kind or one move alone: the hook, subtitle and "and N more" state the whole
    kind's count, which a check that failed anywhere in the kind no longer vouches for. Newly reported and
    exited moves stay (a split only ever restates a holding on BOTH books). WARNING, with ``why``. Returns
    (moves kept, the kinds whose counts are omitted)."""
    kept = [m for m in moves if m.get("move") not in _SPLIT_SENSITIVE_MOVES]
    dropped = len(moves) - len(kept)
    if dropped:
        ctx.reject(reason, dropped)
    logger.warning("company news thirteen_f: cik=%s %s — %s; %d more / fewer shares move(s) dropped and both "
                   "counts left out (%s)", cik, period, why, dropped, reason)
    return kept, _SPLIT_SENSITIVE_MOVES


def _split_unchecked_dropped(ctx: _Ctx, moves: List[Dict[str, Any]], *, unchecked: bool, cik: str,
                             period: str) -> Tuple[List[Dict[str, Any]], FrozenSet[str]]:
    """Batch 1 (2026-10-15, plan item 3): the shared split check looks up at most
    `_whale_common.MAX_SPLIT_LOOKUPS` suspects per filing and the overflow keeps its RAW share diff (the
    cap fails open, by design, for the app's screens) — so a held-through 10:1 split past the cap reads as
    "reported 10x more shares". When the cap trimmed anything (``unchecked``), every increased / decreased
    move is dropped (`split_unchecked`) and both counts are omitted (`_more_fewer_dropped`). Runs before every
    other move gate, so the counters name the real reason — and before the per-row re-check
    (`_split_window_problem`), which it spares: no lookup is spent on kinds already dropped."""
    if not unchecked:
        return moves, frozenset()
    return _more_fewer_dropped(ctx, moves, reason="split_unchecked", why="split lookups past the cap were never "
                               "checked", cik=cik, period=period)


def _lookup_symbol(raw: Any) -> Optional[str]:
    """The symbol a diff row is looked up under — exactly as the build's suspect lookups ask for it (upper-cased
    and stripped, as the corporate-actions cache keys it), so a derivation the build made is a cache hit. None
    for a row with no usable symbol (FMP's "--" placeholder included): it cannot be re-checked."""
    if not isinstance(raw, str):
        return None
    sym = raw.strip().upper()
    return sym if sym and sym != "--" else None


def _symbols_by_cusip(*books: Any, cusip_key: str) -> Dict[str, Set[str]]:
    """CUSIP → every lookup symbol (`_lookup_symbol`) the given books' SHARE rows carry it under (the re-review of
    2026-10-19, F13-RENAME: FMP keeps a renamed security's OLD symbol on the old extract — SQ beside XYZ — and its
    split history can sit under either symbol). ``cusip_key``: ``securityCusip`` on a raw FMP extract, ``cusip`` on a
    stored build's holdings. Rows without a CUSIP or a usable symbol, and explicit option / note rows, are skipped."""
    out: Dict[str, Set[str]] = {}
    for rows in books:
        for r in rows if isinstance(rows, list) else ():
            if not isinstance(r, Mapping) or is_13f_non_share_row(r):
                continue
            cusip, sym = _tc_rules.normalize_cusip(r.get(cusip_key)), _lookup_symbol(r.get("symbol"))
            if cusip and sym:
                out.setdefault(cusip, set()).add(sym)
    return out


def _split_found(window: Any) -> bool:
    """Does a `SplitWindow` hold a stock split (a usable ratio other than 1)?"""
    ratio = _num(getattr(window, "ratio", None))
    return ratio is not None and ratio > 0 and abs(ratio - 1.0) > 1e-9


def _split_window_taint(window: Any, row: Mapping[str, Any], prev_shares: Mapping[str, float]) -> Optional[str]:
    """Why one increased / decreased diff row cannot be shown as it stands, or None when it can.

    * the lookup failed, or the probe did (``lookup_failed``), or an adjustment the classifier could not name
      sits in the quarter (``adjustment``: a spin-off, a reverse split past its range — the share change may
      be one);
    * a stock split sits in the quarter: shown only when the BUILD restated the row by it — the row's previous
      count is the previous book's count of its CUSIP times the window's ratio (`restate_prev_shares_for_split`
      clean branch, the same two figures multiplied). A split the build never looked up (the shared suspect
      filter's blind spot), or one it kept the raw diff for (a holder who traded against the split: a 3:2 split
      plus a 25% sale reads as "more shares"), leaves the previous count raw: ``split_not_restated``. A row
      whose CUSIP is not on the previous book once (a re-keyed CUSIP, an unreadable book) cannot prove it."""
    ratio = _num(getattr(window, "ratio", None))          # a finite non-bool number, else None
    flag = getattr(window, "unclassified", None)
    if ratio is None or ratio <= 0 or not isinstance(flag, bool):
        return "lookup_failed"
    if flag:
        return "adjustment"
    if abs(ratio - 1.0) <= 1e-9:
        return None
    book = prev_shares.get(_tc_rules.normalize_cusip(row.get("cusip")) or "")
    shown_prev = _num(row.get("prev_shares"))
    if book is None or shown_prev is None or not math.isclose(shown_prev, book * ratio, rel_tol=1e-9, abs_tol=0.5):
        return "split_not_restated"
    return None


async def _split_window_problem(ctx: _Ctx, rows: Sequence[Mapping[str, Any]], *, counts: Any,
                                prev_shares: Callable[[], Mapping[str, float]], cik: str, period: str,
                                prev_end: date, period_end: date,
                                cusip_symbols: Optional[Mapping[str, AbstractSet[str]]] = None
                                ) -> Optional[Tuple[str, str]]:
    """The review of 2026-10-16 (F1): may this filing's more / fewer moves — and both kinds' counts — be shown?
    None when every increased / decreased row is verified; else ``(reason, why)`` for `_more_fewer_dropped`.

    ``rows`` are ALL the filing's diff rows (``move``, ``symbol`` as the diff carries it, ``cusip``,
    ``prev_shares``) and ``counts`` its per-kind counts: the counts are stated publicly, so every row of both
    kinds is checked, not only the moves shown (`THIRTEEN_F_MAX_SPLIT_CHECKS`). ``prev_shares`` builds the
    previous book's CUSIP → shares map a split's restatement is proved against — called only when a split IS
    found (the registry path normalises the previous extract for it). In order:

    * a junk count of either kind → None: `_filing_record` refuses the whole record (``record_invalid``), and
      no lookup is spent on it;
    * the rows must be exactly what the counts count — a count no listed row backs cannot be checked —, each
      with a usable symbol, at most `THIRTEEN_F_MAX_SPLIT_CHECKS` of them (else ``split_unchecked``, no lookup);
    * one lookup per distinct symbol through the builder's own seam (`_builder.split_windows`: the build's
      quarter start, its seam, ``ctx.deps.actions``) over ``(prev_end, period_end + 7 days]`` — the re-review of
      2026-10-19 (F13-QE-SPLIT): a split ex-dated just after the quarter can stand behind a post-split count the
      filer booked on the payable date — each row judged by `_split_window_taint` → ``split_in_window`` when any
      row is not clean. Only the budget running out raises (``budget_exhausted``).

    ``cusip_symbols`` (the re-review of 2026-10-19, F13-RENAME): CUSIP → every symbol the filing's two books carry it
    under (`_symbols_by_cusip`). A row is looked up under its own symbol AND each of those (a security renamed in the
    quarter: FMP keeps the old symbol on the old extract, and the split may sit only in the old symbol's history),
    every one counted against the cap, and is clean only when EVERY symbol's window is clean (or restated by the
    build — one ratio for the row: two different splits cannot both match its previous count)."""
    sensitive = [r for r in rows if isinstance(r, Mapping) and r.get("move") in _SPLIT_SENSITIVE_MOVES]
    stated: Dict[str, int] = {}
    for kind in _SPLIT_SENSITIVE_MOVES:
        n = counts.get(kind) if isinstance(counts, Mapping) else None
        if not isinstance(n, int) or isinstance(n, bool) or n < 0:
            return None
        stated[kind] = n
    listed = Counter(r["move"] for r in sensitive)
    if any(listed[k] != stated[k] for k in _SPLIT_SENSITIVE_MOVES):
        return ("split_unchecked", f"its more / fewer rows ({dict(listed)}) are not what its counts count "
                                   f"({stated}), so the counts cannot be re-checked")
    if not sensitive:
        ctx.split_checked[(cik, period)] = frozenset()      # nothing to verify: no more / fewer row at all
        return None
    symbols = [_lookup_symbol(r.get("symbol")) for r in sensitive]
    if any(s is None for s in symbols):
        return ("split_unchecked", f"{symbols.count(None)} more / fewer row(s) carry no symbol to re-check a "
                                   "split under")
    # F13-RENAME: each row under its own symbol and every other symbol its CUSIP carries on either book
    aliases = cusip_symbols if isinstance(cusip_symbols, Mapping) else {}
    row_symbols: List[List[str]] = []
    for sym, r in zip(symbols, sensitive):
        cusip = _tc_rules.normalize_cusip(r.get("cusip"))
        others = sorted(s for s in aliases.get(cusip, ()) if isinstance(s, str)) if cusip else []
        row_symbols.append(list(dict.fromkeys([sym, *others])))
    distinct = list(dict.fromkeys(s for syms in row_symbols for s in syms))
    if len(distinct) > THIRTEEN_F_MAX_SPLIT_CHECKS:
        return ("split_unchecked", f"{len(distinct)} more / fewer symbols are more than the split re-check's "
                                   f"cap of {THIRTEEN_F_MAX_SPLIT_CHECKS}")
    # The re-review of 2026-10-19 (F13-QE-SPLIT): the window runs `THIRTEEN_F_SPLIT_RECHECK_SLACK_DAYS` past the
    # quarter's end (never past the run date): a forward split PAID on the quarter's last session is ex-dated the
    # next business day (SMCI's 10:1, paid Sep 30, 2024, traded split-adjusted from Oct 1), and a filer booking the
    # shares on the payable date reports the post-split count — a raw multiple the quarter-end window never sees.
    # The build never restates such a split, so a row beside one is `split_in_window` (both kinds dropped, fail
    # closed: a filer that booked on the ex-date loses its more / fewer moves that quarter). The build and the app
    # keep the quarter's own window, so these derivations are not the build's cache hits.
    check_end = max(period_end, min(period_end + timedelta(days=THIRTEEN_F_SPLIT_RECHECK_SLACK_DAYS), ctx.run_date))
    windows = await ctx.step(
        "13f_split_check",
        lambda: _builder.split_windows(distinct, prev_end.isoformat(), check_end.isoformat(),
                                       actions=ctx.deps.actions, log_ctx=f"marketing cik={cik} period={period}"),
        reason="thirteen_f_unavailable",
    )
    windows = windows if isinstance(windows, Mapping) else {}
    book = prev_shares() if any(_split_found(windows.get(s)) for s in distinct) else {}
    tainted = []
    for syms, r in zip(row_symbols, sensitive):
        for sym in syms:                     # clean only when EVERY symbol of the row answers clean (F13-RENAME)
            why = _split_window_taint(windows.get(sym), r, book)
            if why is not None:
                tainted.append(f"{sym}:{why}")
                break
    if not tainted:
        # The spotlight lane (2026-11-15), out of band: the rows whose split the BUILD restated are clean for the
        # roundup ("reported more shares" stays true) but their previous count is the previous book × the ratio, a
        # number the previous 13F never reported — the 13F spotlight's "down from Y the quarter before" must not
        # print it (``split_restated``). Every symbol the row was looked up under, canonical.
        restated: Set[str] = set()
        for syms, r in zip(row_symbols, sensitive):
            if any(_split_found(windows.get(s)) for s in syms):
                restated.update(c for c in (R.canonical_symbol(s) for s in (*syms, r.get("symbol"))) if c)
        ctx.split_checked[(cik, period)] = frozenset(restated)
        logger.info("company news thirteen_f: cik=%s %s — %d more / fewer row(s) re-checked: no split or adjustment "
                    "the build did not restate%s", cik, period, len(sensitive),
                    f" ({len(restated)} symbol(s) restated by the build: {sorted(restated)[:10]})" if restated else "")
        return None
    shown = ",".join(sorted(set(tainted))[:10]) + (" …" if len(set(tainted)) > 10 else "")
    return ("split_in_window", f"the split re-check refused {len(tainted)} of {len(sensitive)} more / fewer row(s) "
                               f"({shown})")


async def _club_stored_row(ctx: _Ctx, cik: str, period: str, *, stage: str, columns: str,
                           consequence: str) -> Optional[Dict[str, Any]]:
    """One club filer's STORED build row for ``period`` (`trillion_club_filings`, one memoized read keyed by
    ``stage``, the ``columns`` asked for), its JSONB fields given as text parsed; None — logged at WARNING with
    ``consequence`` (what the caller does without it) — when the row is missing or unreadable. Only the budget
    running out raises."""
    try:
        rows = await _rows(
            ctx, stage, (stage, cik, period),
            lambda sb: sb.table(_CLUB_FILINGS_TABLE).select(columns)
            .eq("cik", cik).eq("period", period).limit(1),
            reason="club_unavailable",
        )
    except MarketingNewsUnavailable as e:
        if e.reason == "budget_exhausted":
            raise
        logger.warning("company news thirteen_f: club cik=%s %s book unreadable — %s (%s)", cik, period,
                       consequence, e.detail)
        return None
    row = next((r for r in rows if r.get("cik") == cik and r.get("period") == period), None)
    if row is None:
        logger.warning("company news thirteen_f: club cik=%s has no stored %s build — %s", cik, period, consequence)
        return None
    row = dict(row)
    for key in ("holdings", "changes"):
        if isinstance(row.get(key), str):
            try:
                row[key] = json.loads(row[key])
            except ValueError:
                row[key] = None
    return row


def _holdings_of_row(row: Optional[Mapping[str, Any]], cik: str, period: str, *, consequence: str
                     ) -> Optional[List[Any]]:
    """A stored build row's holdings list, or None — logged at WARNING with ``consequence`` when the row was read but
    its holdings are missing or unreadable (a missing row was logged by `_club_stored_row`)."""
    holdings = row.get("holdings") if row is not None else None
    if not isinstance(holdings, list):
        if row is not None:
            logger.warning("company news thirteen_f: club cik=%s has no readable %s book — %s", cik, period,
                           consequence)
        return None
    return holdings


def _has_more_fewer(moves: Sequence[Mapping[str, Any]], counts: Any) -> bool:
    """Does a filing carry an increased / decreased move, or count one (a count is stated with no move shown)?"""
    if any(m.get("move") in _SPLIT_SENSITIVE_MOVES for m in moves):
        return True
    return isinstance(counts, Mapping) and any(
        isinstance(n, int) and not isinstance(n, bool) and n > 0
        for n in (counts.get(k) for k in _SPLIT_SENSITIVE_MOVES))


async def _club_more_fewer_problem(ctx: _Ctx, *, current: Mapping[str, Any], cik: str, period: str,
                                   prev_end: date, period_end: date, position_count: Any,
                                   counts: Mapping[str, Any],
                                   previous_holdings: List[Any]) -> Optional[Tuple[str, str]]:
    """May a club filer's STORED build show its more / fewer moves and counts? None, or ``(reason, why)`` for
    `_more_fewer_dropped`. ``current`` is the quarter's stored row (holdings, change rows, build status) the
    caller read — a ``complete`` one: the owner-delegated decision of 2026-10-19 refuses the WHOLE filer for any
    other status before this runs (`_club_filings`; the review of 2026-10-16, F2, dropped only these kinds) —
    then, in order:

    * Batch 1: the split-lookup cap, re-derived from the stored current and previous books
      (`builder.stored_split_unchecked`, pure — the row keeps no record of it). Suspects are a subset of the
      positions, so a book of at most `MAX_SPLIT_LOOKUPS` positions never reached it → ``split_unchecked``;
    * F1 (`_split_window_problem`) over the STORED change rows — every row with its filed symbol and CUSIP;
      the detail's rows carry routable symbols only — tied to the detail by the per-kind counts (a rebuild
      between the two reads changes them → ``split_unchecked``); a split shown as restated must match the
      stored previous book (`_shares_by_cusip`). F1 also closes F3: a capped suspect the stored-book
      re-derivation missed (a stale previous book, a symbol only one build resolved) is a more / fewer row and
      is looked up itself."""
    if current.get("build_status") != _builder.BUILD_COMPLETE:      # the caller's rule; never shown on a guess
        return ("degraded_build", "its stored build is not complete")
    holdings, changes = current.get("holdings"), current.get("changes")
    stored_rows = changes.get("rows") if isinstance(changes, Mapping) else None
    if (not isinstance(holdings, list) or not isinstance(changes, Mapping)
            or changes.get("comparison") != "quarter" or not isinstance(stored_rows, list)):
        return ("split_unchecked", "its stored holdings or change rows are unreadable")
    if (isinstance(position_count, int) and not isinstance(position_count, bool)
            and position_count > MAX_SPLIT_LOOKUPS):
        over = _builder.stored_split_unchecked(holdings, previous_holdings)
        if over:
            return ("split_unchecked", f"{len(over)} split suspect(s) past the lookup cap "
                                       f"({','.join(sorted(over)[:10])}) were never checked by its stored build")
    stored_counts = changes.get("counts")
    for kind in _SPLIT_SENSITIVE_MOVES:
        n = counts.get(kind)
        if not isinstance(n, int) or isinstance(n, bool) or n < 0:
            return None                      # `_filing_record` refuses the record (record_invalid)
        stored = stored_counts.get(kind) if isinstance(stored_counts, Mapping) else None
        # an int, never a bool (True == 1): the re-check below would read a junk stored count as "nothing to
        # check" and leave the detail's count standing unverified
        if not isinstance(stored, int) or isinstance(stored, bool) or stored != n:
            return ("split_unchecked", "its stored change counts are not the detail's (a rebuild between the "
                                       "two reads, or a count that is not a number)")
    rows = [{"move": r.get("change"), "symbol": r.get("symbol"), "cusip": r.get("cusip"),
             "prev_shares": r.get("prev_shares")} for r in stored_rows if isinstance(r, Mapping)]
    problem = await _split_window_problem(ctx, rows, counts=stored_counts,
                                          prev_shares=lambda: _shares_by_cusip(previous_holdings),
                                          cik=cik, period=period, prev_end=prev_end, period_end=period_end,
                                          cusip_symbols=_symbols_by_cusip(holdings, previous_holdings,
                                                                          cusip_key="cusip"))
    # The spotlight lane (2026-11-15): the detail's change rows carry ROUTABLE symbols (the current holding's), the
    # stored rows the FILED ones the re-check used — they cannot be tied to each other by symbol, so a restated row
    # anywhere in the filing counts every more / fewer move of it as restated (fail closed: the 13F spotlight then
    # offers only its newly reported and exited holdings).
    if ctx.split_checked.get((cik, period)):
        ctx.split_restated_any.add((cik, period))
    return problem


@dataclass
class _RegistryBudget:
    """One `candidates()` call's registry 13F budget, SHARED by the lead filers and the rest of the
    registry (Batch 1, 2026-10-15 — the lead step is never a budget of its own): builds that hit FMP
    (`MAX_LIVE_13F_BUILDS`; a memo hit or a refused book costs none), unavailable filers
    (`THIRTEEN_F_MAX_UNAVAILABLE` → the series is unavailable) and the filer CIKs already read — a
    successor shared by two registry rows (the old CIK and, if ever curated, the new one) is read once."""

    builds: int = 0
    strikes: int = 0
    seen: Set[str] = field(default_factory=set)


def _filer_cik(registry_cik: str) -> str:
    """The EDGAR filer read for a registry 13F CIK: its successor (`R.THIRTEEN_F_SUCCESSORS`, Batch 1 —
    Pershing Square's holdings moved to CIK 0002026053), else itself. One hop."""
    return R.THIRTEEN_F_SUCCESSORS.get(registry_cik, registry_cik)


def _lead_split(rows: Sequence[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """(the lead filers' rows in `R.THIRTEEN_F_LEAD_FILERS` order, every other row in registry order).
    A row leads when its registry CIK or its successor is listed."""
    rank = {c: i for i, c in enumerate(R.THIRTEEN_F_LEAD_FILERS)}

    def lead_rank(row: Mapping[str, Any]) -> Optional[int]:
        cik = row["cik"]
        return rank.get(cik, rank.get(_filer_cik(cik)))

    lead = sorted((r for r in rows if lead_rank(r) is not None), key=lambda r: lead_rank(r))
    return lead, [r for r in rows if lead_rank(r) is None]


async def _club_filings(ctx: _Ctx, *, year: int, quarter: int, period: str, period_end: date,
                        prev_end: date, room: int, club_ciks: Set[str],
                        skip_ciks: FrozenSet[str] = frozenset(), roundup: bool = True) -> List[Any]:
    """Trillion Club 13F filers (`card_kind == "thirteen_f"` with `use_13f`), in group order.
    Data from the stored builds (`get_detail`, unredacted): zero FMP calls for the filing.
    ``skip_ciks`` (Batch 1): the lead filers' CIKs — already read live from the registry, first —
    so a club company that is also a lead filer is never a second record (the 13F spotlight: never
    read at all, `R.THIRTEEN_F_LEAD_FILERS` belong to the main lane). ``roundup``: `_filing_record`'s."""
    prev_period = R.period_label(*_tc_rules.previous_quarter(year, quarter))
    group = await ctx.step("club_group", lambda: ctx.club.get_group(), reason="club_unavailable")
    rows = await _club_rows(ctx, reason="club_unavailable")
    out: List[Any] = []
    options_unchecked: Set[str] = set()
    for card in getattr(group, "companies", None) or ():
        if getattr(card, "card_kind", None) != "thirteen_f":
            continue
        row = rows.get(getattr(card, "slug", None))
        if not row or row.get("use_13f") is not True:
            continue
        ciks = sorted({c for c in (R.cik10(x) for x in (row.get("ciks") or ())) if c})
        if not ciks:
            continue
        club_ciks.update(ciks)
        if skip_ciks.intersection(ciks):
            continue                         # the lead step read this filer (Batch 1)
        if len(out) >= room:
            continue                         # keep collecting CIKs for the registry dedup
        ctx.considered += 1
        cik = ciks[0]
        if f"{R.LEDGER_PREFIX}thirteen_f:{cik}:{period}" in ctx.exclude:
            ctx.reject("already_posted")
            continue
        problem = R.filer_name_problem(getattr(card, "name", None))
        if problem:
            ctx.reject(problem)
            continue
        if R.filer_looks_like_person(getattr(card, "name", None)):
            ctx.reject("filer_is_person")     # the subject is the filing ENTITY, never a person
            continue
        if getattr(card, "period", None) != period:
            ctx.reject("filer_not_filed")
            continue
        if getattr(card, "comparison", None) != "quarter":
            ctx.reject("non_comparable")
            continue
        detail = await ctx.step("club_detail", lambda slug=card.slug: ctx.club.get_detail(slug),
                                reason="club_unavailable")
        company = getattr(detail, "company", None)
        if detail is None or company is None or getattr(company, "period", None) != period:
            ctx.reject("filer_unavailable")
            continue
        if getattr(company, "comparison", None) != "quarter":
            ctx.reject("non_comparable")
            continue
        filed = _day(getattr(company, "filed_on", None))
        if filed is None or not (period_end < filed <= ctx.run_date):
            ctx.reject("filer_not_filed")
            continue
        amended = _day(getattr(company, "amended_on", None))
        raw_moves = [{
            "symbol": getattr(ch, "symbol", None), "name": getattr(ch, "name", ""),
            "move": getattr(ch, "change", None), "shares": getattr(ch, "shares", None),
            "prev_shares": getattr(ch, "prev_shares", None), "value": getattr(ch, "value", None),
            "newly_listed": getattr(ch, "newly_listed", False), "prev_value": None,
        } for ch in (getattr(detail, "changes", None) or ())]
        # Owner-delegated decision (2026-10-19, the Batch 1 re-review): the STORED current build (one memoized
        # read: holdings, change rows, build status) must be exactly ``complete``, or the WHOLE filer is refused
        # (`degraded_build`) — parity with the registry path, which refuses a build whose split or symbol lookup
        # failed. A stored row keeps no reason, so every degraded build is refused: a failed symbol lookup can
        # split one CUSIP re-key into an exit plus an entry (a false "newly reported" / "no longer reported"), and
        # a ``profiles_missing`` or ``unresolved_pending`` build is refused the same way (fail closed). A row that
        # cannot be read at all cannot show it is complete: the filer is refused (`filer_unavailable`), as for the
        # previous book below. The review of 2026-10-16 (F2) dropped only the more / fewer kinds of such a build.
        current = None
        if raw_moves:
            current = await _club_stored_row(ctx, cik, period, stage="club_current_filing",
                                             columns="cik,period,holdings,changes,build_status,excluded_rows",
                                             consequence="filer refused, no move shown")
            if current is None:
                ctx.reject("filer_unavailable")
                continue
            status = current.get("build_status")
            if status != _builder.BUILD_COMPLETE:
                logger.warning("company news thirteen_f: club cik=%s %s — its stored build is %r, not complete (the "
                               "row keeps no reason: a failed split or symbol lookup is possible; a profiles_missing "
                               "or unresolved_pending build is refused the same way) — filer refused, no move shown "
                               "(degraded_build)", cik, period, _scrub(status, 20))
                ctx.reject("degraded_build")
                continue
        # The stored previous-quarter build (one memoized read): an exit's value (its change row
        # carries none) AND the previous book the share-class tie needs (review round 9 #8: with no
        # previous book, "Newly reported: Alphabet" was published while class C was held last
        # quarter). Missing or unreadable → the filer is refused (fail closed, WARNING).
        previous = await _club_previous_book(ctx, cik, prev_period) if raw_moves else ({}, [], [], None)
        if previous is None:
            ctx.reject("filer_unavailable")
            continue
        prev_values, previous_book, previous_holdings, previous_excluded = previous
        for m in raw_moves:
            if m["move"] == "no_longer_reported":
                m["prev_value"] = prev_values.get(_sym_key(m["symbol"])) if m["symbol"] else None
        # Review round 10, a RECORDED residual (no behaviour change): the stored club builds keep
        # SHARE rows only — `normalize_rows` dropped put / call and PRN rows and kept just an
        # `excluded_rows` COUNT, mixed with other exclusions — so the registry path's options /
        # notes rule (`_option_moves_dropped`, `move_has_options`) cannot run here: "No longer
        # reported: X" may sit beside X calls on the same 13F. Said once per filer.
        if raw_moves and cik not in options_unchecked:
            options_unchecked.add(cik)
            logger.info("company news thirteen_f: club cik=%s %s — its stored build carries no option / note "
                        "rows, so the options rule (move_has_options) is not applied to its %d change row(s)",
                        cik, period, len(raw_moves))
        # Batch 1 (2026-10-15) and the review of 2026-10-16 (F1): a (complete, above) stored build shows its more /
        # fewer moves and counts only when its split lookups were not capped and every increased / decreased row of
        # it passes the split re-check (`_club_more_fewer_problem`); else none of them.
        position_count = getattr(company, "position_count", None)
        counts = _counts_of(getattr(company, "change_counts", None))
        omit: FrozenSet[str] = frozenset()
        if current is not None and _has_more_fewer(raw_moves, counts):
            problem = await _club_more_fewer_problem(
                ctx, current=current, cik=cik, period=period, prev_end=prev_end, period_end=period_end,
                position_count=position_count, counts=counts, previous_holdings=previous_holdings)
            if problem is not None:
                raw_moves, omit = _more_fewer_dropped(ctx, raw_moves, reason=problem[0], why=problem[1],
                                                      cik=cik, period=period)
        if not roundup and raw_moves:        # the 13F spotlight's ``filing_moves`` (review of the 13F list)
            why = []
            if not _is_zero_count(_field_of(getattr(company, "change_counts", None), "corporate_action")):
                why.append("a corporate_action row, or no readable count of them")
            # The stored builds keep no option / note row (above), only an `excluded_rows` count mixed with other
            # exclusions: an option position that changed is visible nowhere, so only a build pair with NO excluded
            # row (both read as a plain 0) can stand behind "every change".
            if current is None or not _is_zero_count(current.get("excluded_rows")) \
                    or not _is_zero_count(previous_excluded):
                why.append("an excluded row on the current or previous stored build (options are not kept), or no "
                           "readable count of them")
            if why:
                _mark_counts_partial(ctx, cik, period, why)
        rec = await _filing_record(
            ctx, filer_name=company.name, cik=cik, filer_symbol=getattr(company, "detail_symbol", None),
            period=period, period_end=period_end, prev_end=prev_end, filed_on=filed,
            amended_on=amended, total=getattr(company, "total_value", None),
            position_count=position_count, raw_moves=raw_moves, counts=counts,
            current_book=_book_of_club(getattr(detail, "holdings", None)), previous_book=previous_book,
            omit_counts=omit, roundup=roundup,
        )
        if rec is not None:
            out.append(rec)
    return out


async def _club_previous_book(ctx: _Ctx, cik: str, prev_period: str
                              ) -> Optional[Tuple[Dict[str, float], List[_BookEntry], List[Any], Any]]:
    """A club filer's stored PREVIOUS-quarter build (`trillion_club_filings.holdings`, one
    memoized read): (symbol key → value, for an exit's materiality; the book's entries — CUSIP,
    symbol, profiled display name — for the share-class tie; the stored holdings themselves, for
    the split-cap check, Batch 1, and the split re-check's restated counts, the review of
    2026-10-16; the row's `excluded_rows` as stored, unchecked — the 13F spotlight's
    ``filing_moves``, review of the 13F list 2026-11-19). None (WARNING) when the row is missing or
    unreadable — the caller refuses the filer; only the budget running out raises."""
    consequence = "filer refused, no move shown"
    row = await _club_stored_row(ctx, cik, prev_period, stage="club_previous_filing",
                                 columns="cik,period,holdings,excluded_rows", consequence=consequence)
    holdings = _holdings_of_row(row, cik, prev_period, consequence=consequence)
    if holdings is None:
        return None
    out: Dict[str, float] = {}
    for h in holdings:
        if not isinstance(h, dict):
            continue
        k, value = _sym_key(h.get("symbol")), _num(h.get("value"))
        if k and value is not None and value > 0:
            out[k] = out.get(k, 0.0) + value
    return out, _book_of_holdings(holdings), holdings, (row or {}).get("excluded_rows")


async def _registry_rows(ctx: _Ctx) -> List[Dict[str, Any]]:
    rows = await _rows(
        ctx, "whales_13f", ("whales_13f",),
        lambda sb: sb.table(_WHALES_TABLE)
        .select("cik,firm_name,category,data_source,associated_ticker,lifecycle_status")
        .eq("data_source", "13f").eq("category", "investors").limit(1000),
        reason="thirteen_f_unavailable",
    )
    order = _registry_order()
    usable = []
    for r in rows:
        if r.get("data_source") != "13f" or r.get("category") != "investors":
            continue
        status = str(r.get("lifecycle_status") or "").strip().lower()
        if status not in _ACTIVE_LIFECYCLE:
            # "inactive" (Scion), "dormant", anything curated later. The review of 2026-10-16 (F4): a row whose
            # holdings are filed by a SUCCESSOR is skipped too, but never silently — its old CIK stops filing,
            # which is exactly when the registry sync suggests a status, and the filer would vanish unnoticed.
            old = R.cik10(r.get("cik"))
            if old in R.THIRTEEN_F_SUCCESSORS:
                logger.warning("company news thirteen_f: registry cik=%s is curated %r and is not read, but its "
                               "holdings are filed by its successor cik=%s (THIRTEEN_F_SUCCESSORS) — if the status "
                               "was set because the old CIK stopped filing, set it back to active", old,
                               status[:20], R.THIRTEEN_F_SUCCESSORS[old])
            continue
        cik = R.cik10(r.get("cik"))
        if cik is None or cik not in order:
            continue                         # only the curated registry's filers
        usable.append((order[cik], cik, r))
    usable.sort(key=lambda t: (t[0], t[1]))
    seen: Set[str] = set()
    out = []
    for _i, cik, r in usable:
        if cik not in seen:
            seen.add(cik)
            out.append({**r, "cik": cik})
    return out


async def _registry_filings(ctx: _Ctx, *, rows: Sequence[Dict[str, Any]], year: int, quarter: int, period: str,
                            period_end: date, prev_end: date, room: int, skip_ciks: Set[str],
                            budget: _RegistryBudget, roundup: bool = True) -> List[Any]:
    """Whale-registry 13F filers (investors, lifecycle "" or "active") — ``rows``, in the order given:
    the lead filers, or the rest in registry order (`_collect_thirteen_f`). The pair is chosen with
    `select_13f_comparison` (owner rule 2026-10-09: never across a missing quarter); a live
    `build_filing` normalises per accession by CUSIP and diffs share counts. Batch 1 (2026-10-15): a
    registry CIK with a successor (`R.THIRTEEN_F_SUCCESSORS`) is read, built, keyed and named as that
    successor (its registry CIK still sets the order); ``budget`` is the call's ONE live-build /
    unavailable-filer budget, shared by both registry steps, and a filer is read once per call; a build
    whose split lookups the cap trimmed shows no more / fewer shares (`_split_unchecked_dropped`). The
    review of 2026-10-16: a successor's hand-over quarter is refused before any call
    (`R.successor_transition`), and every increased / decreased row of a build is re-checked for a split
    it did not restate (`_split_window_problem`)."""
    out: List[Any] = []

    def strike(stage: str, filer: str, e: BaseException) -> None:
        budget.strikes += 1
        ctx.reject("filer_unavailable")
        logger.warning("company news thirteen_f: filer unavailable cik=%s stage=%s (%s)", filer, stage, _describe(e))
        if budget.strikes >= THIRTEEN_F_MAX_UNAVAILABLE:
            raise MarketingNewsUnavailable(ctx.series, "thirteen_f_unavailable",
                                           f"stage={stage}; {budget.strikes} filers unavailable; last {_describe(e)}")

    for row in rows:
        if len(out) >= room:
            break
        registry_cik = row["cik"]
        cik = _filer_cik(registry_cik)       # the EDGAR filer: dates, build, ledger key, name (Batch 1)
        if registry_cik in skip_ciks or cik in skip_ciks or cik in budget.seen:
            continue                         # a club filer, or a filer this call already read
        budget.seen.add(cik)
        ctx.considered += 1
        # Keyed on the filer; a post under the registry CIK's own key counts as posted too (fail closed).
        if any(f"{R.LEDGER_PREFIX}thirteen_f:{c}:{period}" in ctx.exclude for c in (cik, registry_cik)):
            ctx.reject("already_posted")
            continue
        if cik != registry_cik:
            logger.info("company news thirteen_f: registry cik=%s is read as its successor filer cik=%s",
                        registry_cik, cik)
        # Review round 9 (#9 / #23): the subject is the EDGAR FILER (`R.THIRTEEN_F_FILERS`), never
        # the curated firm_name ("Icahn Enterprises" for Carl C. Icahn's own 13F). An unlisted CIK
        # is refused; a natural-person filer too — before any FMP call for this filer.
        if cik not in R.THIRTEEN_F_FILERS:
            ctx.reject("filer_entity_unknown")
            logger.info("company news thirteen_f: cik=%s is not in THIRTEEN_F_FILERS — no verified filer "
                        "entity; filer skipped", cik)
            continue
        firm = R.THIRTEEN_F_FILERS[cik]
        if firm is None or R.filer_looks_like_person(firm):
            ctx.reject("filer_is_person")
            continue
        problem = R.filer_name_problem(firm)
        if problem:
            ctx.reject(problem)              # before any FMP call for this filer
            continue
        # The review of 2026-10-16 (live preview F2): a successor's hand-over quarter would be diffed against
        # its own smaller pre-hand-over book (Pershing's 2026-Q1: Howard Hughes only), so continuing positions
        # read as "newly reported" — refused before any FMP call; the next quarter compares two full books.
        if R.successor_transition(registry_cik, period):
            ctx.reject("successor_transition")
            logger.info("company news thirteen_f: registry cik=%s %s is its successor cik=%s's hand-over quarter "
                        "(first full book %s) — never compared; filer skipped", registry_cik, period, cik,
                        R.THIRTEEN_F_SUCCESSOR_SINCE.get(registry_cik))
            continue
        key = ("13f", cik, period)
        live = _memo_get(key) is _MISS
        if live and budget.builds >= MAX_LIVE_13F_BUILDS:
            break
        try:
            dates = await ctx.step("13f_dates", lambda: ctx.fmp.get_institutional_filing_dates(cik, strict=True),
                                   reason="thirteen_f_unavailable", passthrough=(Exception,))
        except MarketingNewsUnavailable:
            raise
        except Exception as e:  # noqa: BLE001 — an upstream failure for this filer
            strike("13f_dates", cik, e)
            continue
        choice = select_13f_comparison(dates, today=ctx.run_date)
        if choice is None or choice.latest != (year, quarter):
            ctx.reject("filer_not_filed")
            continue
        if choice.comparison != "quarter":
            ctx.reject("non_comparable")     # a gap or a first filing writes no moves
            continue
        if live:
            budget.builds += 1
        try:
            built, raw_cur, raw_prev = await ctx.step(
                "13f_build",
                lambda: _shared(key, BUILT_13F_TTL_SECONDS, lambda: _build_registry(ctx, cik, year, quarter)),
                reason="thirteen_f_unavailable",
                passthrough=(_builder.FilingRefused, _builder.FilingUnavailable),
            )
        except _builder.FilingRefused:
            if live:
                budget.builds -= 1           # a refused book costs no build slot
            ctx.reject("book_too_large")
            continue
        except _builder.FilingUnavailable as e:
            strike("13f_build", cik, e)
            continue
        reasons = list(getattr(built, "degraded_reasons", None) or ())
        if any(r.startswith(("split_lookup_failed", "symbol_lookup_failed")) for r in reasons):
            ctx.reject("degraded_build")
            continue
        changes = getattr(built, "changes", None) or {}
        if changes.get("comparison") != "quarter":
            ctx.reject("non_comparable")
            continue
        if getattr(built, "period", None) != period or getattr(built, "cik", None) != cik:
            ctx.reject("record_invalid")
            continue
        filed = _day(getattr(built, "filed_on", None))
        if filed is None or not (period_end < filed <= ctx.run_date):
            ctx.reject("filer_not_filed")
            continue
        # Batch 1 (2026-10-15): the suspects the split-lookup cap trimmed (never a degraded reason). A
        # missing value or one that is not a set is read as "trimmed" — fail closed, never "all checked".
        split_unchecked = getattr(built, "split_unchecked", None)
        unchecked = not isinstance(split_unchecked, (set, frozenset)) or bool(split_unchecked)
        prev_values = _previous_values(raw_prev, cik=cik, prev_end=prev_end)
        raw_moves = [{
            "symbol": r.get("symbol"), "name": r.get("name") or "", "move": r.get("change"),
            "shares": r.get("shares"), "prev_shares": r.get("prev_shares"), "value": r.get("value"),
            "newly_listed": r.get("newly_listed"), "cusip": r.get("cusip"),
            "prev_value": prev_values.get(_tc_rules.normalize_cusip(r.get("cusip")) or ""),
        } for r in (changes.get("rows") or ()) if isinstance(r, dict)]
        raw_moves, omit = _split_unchecked_dropped(
            ctx, [m for m in raw_moves if m["move"] in R.THIRTEEN_F_MOVES], unchecked=unchecked, cik=cik,
            period=period)
        # The review of 2026-10-16 (F1): every increased / decreased row — the build's own diff, each with its
        # filed symbol and CUSIP — re-checked for a split or adjustment in the quarter the build did not restate.
        if not omit:
            problem = await _split_window_problem(
                ctx, raw_moves, counts=changes.get("counts") or {},
                prev_shares=lambda: _previous_shares(raw_prev, cik=cik, prev_end=prev_end), cik=cik, period=period,
                prev_end=prev_end, period_end=period_end,
                cusip_symbols=_symbols_by_cusip(raw_cur, raw_prev, cusip_key="securityCusip"))
            if problem is not None:
                raw_moves, omit = _more_fewer_dropped(ctx, raw_moves, reason=problem[0], why=problem[1], cik=cik,
                                                      period=period)
        raw_moves, counts = _option_moves_dropped(ctx, raw_moves, changes.get("counts") or {}, raw_cur, raw_prev)
        raw_moves = _share_check(ctx, raw_moves, raw_cur, raw_prev)
        if not roundup:                      # the 13F spotlight's ``filing_moves`` (review of the 13F list)
            why = []
            if not _is_zero_count(_field_of(changes.get("counts"), "corporate_action")):
                why.append("a corporate_action row, or no readable count of them")
            if _non_share_issuers(raw_cur) or _non_share_issuers(raw_prev):
                why.append("option / note rows on the current or previous book, never diffed")
            if why:
                _mark_counts_partial(ctx, cik, period, why)
        rec = await _filing_record(
            ctx, filer_name=firm, cik=cik, filer_symbol=row.get("associated_ticker"), period=period,
            period_end=period_end, prev_end=prev_end, filed_on=filed,
            amended_on=_day(getattr(built, "amended_on", None)),
            total=getattr(built, "total_value", None), position_count=getattr(built, "position_count", None),
            raw_moves=raw_moves, counts=counts,
            current_book=_book_of_holdings(getattr(built, "holdings", None)),
            previous_book=_book_of_raw(raw_prev), omit_counts=omit, roundup=roundup,
        )
        if rec is not None:
            out.append(rec)
    return out


def _widened_13f_exclude(exclude: FrozenSet[str]) -> FrozenSet[str]:
    """``exclude`` plus the main lane's per-filer, per-quarter key of every 13F ledger key in it (`R.thirteen_f_filer_key`):
    a 13F spotlight post (``news:thirteen_f_spotlight:<cik>:<period>:<SYM>``) reads as ``news:thirteen_f:<cik>:<period>``,
    the key both lanes' filer walks check — ONE 13F post per filer per quarter across both lanes (the spotlight lane,
    2026-11-15), in both directions. A roundup key maps to itself, so with no spotlight key the set is unchanged."""
    extra = frozenset(k for k in (R.thirteen_f_filer_key(r) for r in exclude) if k is not None)
    return exclude | extra


async def _collect_thirteen_f(ctx: _Ctx) -> Candidates:
    """Batch 1 (2026-10-15, owner default "Berkshire first in 13F season"): the lead filers
    (`R.THIRTEEN_F_LEAD_FILERS`, read live from the registry), then the Trillion Club filers (stored
    builds), then the rest of the registry — every registry read under ONE budget
    (`_RegistryBudget`: still `MAX_LIVE_13F_BUILDS` live builds per call). A club company that is also
    a lead filer is read once, as the lead. A filer the 13F spotlight already posted for the quarter is
    ``already_posted`` here too (`_widened_13f_exclude`, the spotlight lane, 2026-11-15)."""
    season = selection.thirteen_f_season(ctx.run_date)
    if season is None:
        return ctx.done((), "thirteen_f_off_season")
    ctx.exclude = _widened_13f_exclude(ctx.exclude)
    year, quarter = season
    period = R.period_label(year, quarter)
    period_end = R.period_end_of(period)
    prev_end = _tc_rules.quarter_end(*_tc_rules.previous_quarter(year, quarter))
    window = dict(year=year, quarter=quarter, period=period, period_end=period_end, prev_end=prev_end)
    records: List[Any] = []
    club_ciks: Set[str] = set()
    budget = _RegistryBudget()
    lead_rows, rest_rows = _lead_split(await _registry_rows(ctx))
    records += await _registry_filings(ctx, rows=lead_rows, room=ctx.limit, skip_ciks=set(), budget=budget,
                                       **window)
    lead_ciks = frozenset(c for r in lead_rows for c in (r["cik"], _filer_cik(r["cik"])))
    if len(records) < ctx.limit:
        if settings.TRILLION_CLUB_ENABLED:
            records += await _club_filings(ctx, room=ctx.limit - len(records), club_ciks=club_ciks,
                                           skip_ciks=lead_ciks, **window)
        else:
            logger.info("company news thirteen_f: TRILLION_CLUB_ENABLED is off — no club filers")
    if len(records) < ctx.limit:
        records += await _registry_filings(ctx, rows=rest_rows, room=ctx.limit - len(records),
                                           skip_ciks=club_ciks, budget=budget, **window)
    ctx.check_profiles()
    return ctx.done(records, "thirteen_f_none_qualified")


# ── thirteen_f_spotlight (the spotlight lane, owner decisions 2026-11-15) ─────────────────────────────────


#: The 13F spotlight's pick order among one filing's eligible holdings — THE order the record checks
#: (`R.thirteen_f_spotlight_order`): the largest dollar size first (`R.thirteen_f_move_flow`, THE size the templates rank
#: by; a move without one after every sized move), then the record's kind order, then the symbol.
_spotlight_move_rank = R.thirteen_f_spotlight_order


def _spotlight_holding(ctx: _Ctx, rec: Any) -> Optional[Any]:
    """The holdings a filing (`ThirteenFFiling`, built by the roundup's walkers with every one of their gates) offers
    the 13F spotlight → `R.ThirteenFSpotlight` of its largest `R.THIRTEEN_F_SPOTLIGHT_MAX_MOVES` eligible holdings
    (owner feedback 2026-11-19: a list, "not just one"), or None when none is eligible. Each move refused is counted,
    and refusing one never refuses the others:

    * more / fewer shares only when the filing VOUCHES for them — both kinds' counts kept (a withheld count:
      ``split_unchecked``) and the split re-check run over them (`ctx.split_checked`) — and never a holding whose
      split the build restated (``split_restated``: "down from Y the quarter before" would print the previous book ×
      the split ratio, a count the previous 13F never reported; for a club filer any restated row of the filing
      counts, `ctx.split_restated_any`), and only a change of at least `R.THIRTEEN_F_SPOTLIGHT_MIN_CHANGE` of the
      quarter before (``spotlight_change_too_small``, `R.thirteen_f_change_clear`, exact at 25%);
    * a newly reported holding (its shares) and an exit (the quarter before's shares) as the roundup has them;
    * one holding per ISSUER and per display name (``share_class_overlap``: `R.issuer_root` folds share-class twins
      the roundup's CUSIP check may not have tied — the larger is kept).

    The eligible holdings in `_spotlight_move_rank` (the largest first) — the first `R.THIRTEEN_F_SPOTLIGHT_MAX_MOVES`
    go into the record, the lead first (its symbol keys the ledger); fewer than `R.THIRTEEN_F_LIST_MIN_MOVES` →
    None (``too_few_holdings``: news-v23, no single-holding 13F post). A record built for the spotlight (`_filing_record` with
    ``roundup=False``) already ran the per-move gates above BEFORE its cut, by dollar size alone (`_spotlight_gated`,
    review 2026-11-19), so its moves are the filing's largest eligible ones and the gates here are a backstop.
    ``eligible_moves`` records how many of the record's moves were eligible (the walker profiles only the largest, so
    at most `R.THIRTEEN_F_MAX_MOVES`); ``filing_moves`` the filing's own count of every change — None when a kind's
    count was withheld or the walker marked the counts partial (`ctx.counts_partial`) — which decides whether the list
    may state its counts as the filing's. A record the rules refuse WHOLE gives the filing up (WARNING,
    ``record_invalid``) — never retried with its lead alone (news-v23)."""
    key = (rec.filer_cik, rec.period)
    counts = dict(rec.counts)
    vouched = "increased" in counts and "decreased" in counts and key in ctx.split_checked
    restated = ctx.split_checked.get(key, frozenset())
    eligible = []
    for mv in rec.moves:
        if mv.move in ("increased", "decreased"):
            if not vouched:
                ctx.reject("split_unchecked")
                continue
            if key in ctx.split_restated_any or mv.company.symbol in restated:
                ctx.reject("split_restated")
                logger.info("company news %s: cik=%s %s %s %s — its split was restated by the build; the previous "
                            "count is not the previous 13F's (split_restated)", ctx.series, rec.filer_cik, rec.period,
                            mv.move, mv.company.symbol)
                continue
            if not R.thirteen_f_change_clear(mv.shares, mv.prev_shares):
                ctx.reject("spotlight_change_too_small")
                continue
        eligible.append(mv)
    if not eligible:
        return None
    picked: List[Any] = []
    roots: Set[Optional[str]] = set()
    names: Set[str] = set()
    for mv in sorted(eligible, key=_spotlight_move_rank):
        root, name = R.issuer_root(mv.company.symbol), mv.company.name.casefold()
        if root in roots or name in names:
            ctx.reject("share_class_overlap")
            logger.info("company news %s: cik=%s %s %s %s — another listing of the same issuer is larger; dropped "
                        "(share_class_overlap)", ctx.series, rec.filer_cik, rec.period, mv.move, mv.company.symbol)
            continue
        roots.add(root)
        names.add(name)
        picked.append(mv)
    if len(picked) < R.THIRTEEN_F_LIST_MIN_MOVES:
        # news-v23 (owner 2026-11-19, "the range from 2 to 6 are okay"): no single-holding 13F post — the filing yields
        # nothing and the walk (then the chain) moves on
        ctx.reject("too_few_holdings")
        logger.info("company news %s: cik=%s %s has %d eligible holding(s), fewer than %d — no 13F post "
                    "(too_few_holdings)", ctx.series, rec.filer_cik, rec.period, len(picked),
                    R.THIRTEEN_F_LIST_MIN_MOVES)
        return None
    shown = tuple(picked[:R.THIRTEEN_F_SPOTLIGHT_MAX_MOVES])
    # The filing's own count of every change — unknown (None) when a kind's count was withheld, or when the walker
    # found the counts may leave changes out (`ctx.counts_partial`: a corporate_action row, option / note rows, a
    # club build's excluded rows — review of the 13F list, 2026-11-19), so the list never states them as the filing's.
    filing_moves = (sum(counts[k] for k in R.THIRTEEN_F_MOVES)
                    if key not in ctx.counts_partial and all(isinstance(counts.get(k), int)
                                                             for k in R.THIRTEEN_F_MOVES) else None)
    base = dict(series="thirteen_f_spotlight", filer_name=rec.filer_name, filer_cik=rec.filer_cik,
                filer_symbol=rec.filer_symbol, period=rec.period, period_end=rec.period_end, filed_on=rec.filed_on,
                amended_on=rec.amended_on)
    try:
        return R.ThirteenFSpotlight(**base, moves=shown, eligible_moves=len(picked), filing_moves=filing_moves)
    except ValueError as e:
        # never retried with its lead alone (news-v23: no single-holding 13F post) — the filing yields nothing
        logger.warning("company news %s: cik=%s %s list of %d holdings refused as record_invalid (%s)", ctx.series,
                       rec.filer_cik, rec.period, len(shown), _describe(e))
        ctx.reject("record_invalid")
        return None


async def _collect_thirteen_f_spotlight(ctx: _Ctx) -> Candidates:
    """ONE filer's latest 13F — up to `R.THIRTEEN_F_SPOTLIGHT_MAX_MOVES` of its holdings (owner decisions 2026-11-15; B
    decision 7; a list since the owner's feedback of 2026-11-19) — in 13F season only.

    * The filers: the Trillion Club filers (stored builds), then the rest of the registry — through the roundup's
      own walkers (`_club_filings`, `_registry_filings`) and every one of their gates (materiality, options, share
      classes, the share-count cross-check, the split re-check, a degraded build), under ONE registry budget. Never a
      `R.THIRTEEN_F_LEAD_FILERS` filer (Berkshire's quarter is the main lane's 13F roundup: ``lead_filer_reserved``,
      never read).
    * One 13F post per filer per quarter across BOTH lanes (`_widened_13f_exclude`): a filer the roundup or a
      spotlight posted is ``already_posted`` before any read of it.
    * Per filing, the holdings `_spotlight_holding` picks (owner feedback 2026-11-19: up to
      `R.THIRTEEN_F_SPOTLIGHT_MAX_MOVES`, the largest dollar size first; a more / fewer holding only when vouched,
      unrestated and moved by at least 25%); a filing with none yields nothing. A filing whose only move is one more /
      fewer holding still counts (``roundup=False``).
    * At most ``ctx.limit`` records, in walk order; `THIRTEEN_F_SPOTLIGHT_SPARE_FILINGS` more filings are read for
      the ones that yield nothing."""
    season = selection.thirteen_f_season(ctx.run_date)
    if season is None:
        return ctx.done((), "thirteen_f_off_season")
    ctx.exclude = _widened_13f_exclude(ctx.exclude)
    year, quarter = season
    period = R.period_label(year, quarter)
    period_end = R.period_end_of(period)
    prev_end = _tc_rules.quarter_end(*_tc_rules.previous_quarter(year, quarter))
    window = dict(year=year, quarter=quarter, period=period, period_end=period_end, prev_end=prev_end)
    lead_rows, rest_rows = _lead_split(await _registry_rows(ctx))
    reserved = frozenset(c for cik in (*R.THIRTEEN_F_LEAD_FILERS, *(r["cik"] for r in lead_rows))
                         for c in (cik, _filer_cik(cik)))
    if lead_rows:
        ctx.reject("lead_filer_reserved", len(lead_rows))
        logger.info("company news %s: %d lead filer(s) %s reserved for the main lane's 13F roundup — never read",
                    ctx.series, len(lead_rows), sorted(r["cik"] for r in lead_rows))
    room = ctx.limit + THIRTEEN_F_SPOTLIGHT_SPARE_FILINGS
    filings: List[Any] = []
    club_ciks: Set[str] = set()
    if settings.TRILLION_CLUB_ENABLED:
        filings += await _club_filings(ctx, room=room, club_ciks=club_ciks, skip_ciks=reserved, roundup=False,
                                       **window)
    else:
        logger.info("company news %s: TRILLION_CLUB_ENABLED is off — no club filers", ctx.series)
    if len(filings) < room:
        filings += await _registry_filings(ctx, rows=rest_rows, room=room - len(filings),
                                           skip_ciks=set(club_ciks) | set(reserved), budget=_RegistryBudget(),
                                           roundup=False, **window)
    records: List[Any] = []
    for rec in filings:
        if len(records) >= ctx.limit:
            break
        got = _spotlight_holding(ctx, rec)
        if got is not None:
            records.append(got)
    ctx.check_profiles()
    return ctx.done(records, "thirteen_f_spotlight_none_qualified")


# ── money_map ─────────────────────────────────────────────────────────────────


async def _club_symbols(ctx: _Ctx, *, purpose: str = "money_map") -> List[str]:
    """US-listed Trillion Club MEMBERS (the group's cards and its also-in-club list), in group
    order. Best effort: the curated seed alone is a full pool, so a failed read is a WARNING.
    ``purpose`` names the reader in the log lines only (Money Map's pool; the insider spotlight's
    revenue line, 2026-11-15)."""
    if not settings.TRILLION_CLUB_ENABLED:
        logger.info("company news %s: TRILLION_CLUB_ENABLED is off — no club symbols", purpose)
        return []
    try:
        group = await ctx.step("club_group", lambda: ctx.club.get_group(), reason="club_unavailable")
        rows = await _club_rows(ctx, reason="club_unavailable")
    except MarketingNewsUnavailable as e:
        if e.reason == "budget_exhausted":
            raise
        logger.warning("company news %s: club members unavailable (%s) — seed only", purpose, e)
        return []
    out = [getattr(c, "detail_symbol", None) for c in (getattr(group, "companies", None) or ())]
    for brief in getattr(group, "also_in_club", None) or ():
        row = rows.get(getattr(brief, "slug", None)) or {}
        out.append(row.get("detail_symbol"))
    return [s for s in out if isinstance(s, str) and s]


def _row_fy(row: Mapping[str, Any]) -> str:
    """The revenue breakdown's own year key (`_record_year`): fiscalYear → calendarYear → date."""
    for key in ("fiscalYear", "calendarYear"):
        v = row.get(key)
        if v not in (None, ""):
            return str(v)
    d = row.get("date") or ""
    return d[:4] if isinstance(d, str) and len(d) >= 4 else ""


def _segments(bd: Any) -> Tuple[List[Any], float]:
    """(≤ 5 named `Segment`s, the rest) from the breakdown's sources. Each name goes through
    `R.segment_display_name` first, with the breakdown's own symbol (FMP's own labels: "Linked In
    Corporation" → "LinkedIn", "UNITED STATES" → "United States"; and, for that company only, a retired
    label the review of 2026-10-16 found — McDonald's "High-Growth Markets" → "International Operated
    Markets", `R.SEGMENT_SYMBOL_OVERRIDES`). "Other", "Unallocated", a name we cannot draw, a duplicate —
    and (Batch 1, 2026-10-15) a label that names nothing a reader knows, `R.segment_fold_reason`: a
    company code of 4 capitals or fewer ("QCT", "PBNA"; AWS and region names such as "US" stay) or a
    generic "Products" / "Revenue" — folds into the rest. Shared by the Money Map and a theme's segment
    fact (`_segment_fact`), so neither draws or narrates such a label."""
    named: List[Any] = []
    rest = 0.0
    seen: Set[str] = set()
    symbol = getattr(bd, "symbol", None)
    for src in getattr(bd, "revenue_sources", None) or ():
        name = R.segment_display_name(getattr(src, "name", None), symbol=symbol)
        value = _num(getattr(src, "value", None))
        if value is None or value <= 0:
            continue
        fold = R.segment_fold_reason(name)
        if fold is not None:
            logger.info("company news: %s segment label %r folded into the rest (%s) — it names nothing a "
                        "reader knows", getattr(bd, "symbol", "?"), str(name)[:60], fold)
            rest += value
            continue
        if (not isinstance(name, str) or name.strip().lower() in _MONEY_MAP_REST_NAMES
                or not _SEGMENT_NAME_RE.fullmatch(name) or not any(ch.isalpha() for ch in name)
                or name.casefold() in seen or len(named) >= R.MONEY_MAP_SEGMENTS[1]):
            rest += value
            continue
        try:
            seg = R.Segment(name=name, value_usd=value)
        except ValueError:
            rest += value
            continue
        seen.add(name.casefold())
        named.append(seg)
    return named, rest


def _annual_point(profit: Any, fy: str) -> Any:
    for p in getattr(profit, "annual", None) or ():
        if getattr(p, "period", None) == fy:
            return p
    return None


def _reported_line(sym: str, fy: str, line: str, value: Any, revenue: float, margin: Any) -> Optional[float]:
    """The statement row's own ``line`` (``grossProfit`` / ``operatingIncome``) as the statement
    carries it — FMP's own revenue − cost of revenue for an issuer with no gross subtotal, so never
    called "as reported" (`MONEY_MAP_BAR_TOLERANCE_PT`) — or None: missing, zero (FMP's fill for a
    line it does not have), non-finite, or more than `MONEY_MAP_BAR_TOLERANCE_PT` points of
    ``revenue`` (the row's own) away from profit power's margin for the year. Never derived by us
    from other lines."""
    v, m = _num(value), _num(margin)
    if v is None or v == 0:
        return None
    if m is None or abs(100.0 * v / revenue - m) > MONEY_MAP_BAR_TOLERANCE_PT:
        logger.warning("company news money_map: %s FY%s %s %.0f disagrees with profit power's margin %r "
                       "(tolerance %.2f pt) — omitted, never guessed", sym, fy, line, v, m,
                       MONEY_MAP_BAR_TOLERANCE_PT)
        return None
    return v


def _money_map_fy_end_key(sym: str) -> Tuple[str, str]:
    return ("money_map_fy_end", sym)


def _money_map_age(period_end: date, run_date: date) -> Optional[str]:
    """The age rule for a fiscal year that ended on ``period_end``, on ``run_date``: ``money_map_stale`` (not
    ended yet, or older than `MONEY_MAP_STALE_DAYS`), ``money_map_next_year_due`` (at least
    `MONEY_MAP_NEXT_YEAR_DUE_DAYS` old: the next year has ended), else None. Pure; the ONE rule both the
    statement read (`_money_map_record`) and the memo (`_money_map_known_age`, `_money_map_order`) apply."""
    if period_end > run_date or (run_date - period_end).days > MONEY_MAP_STALE_DAYS:
        return "money_map_stale"
    if (run_date - period_end).days >= MONEY_MAP_NEXT_YEAR_DUE_DAYS:
        return "money_map_next_year_due"
    return None


def _money_map_known(sym: str) -> Optional[Tuple[str, date, date]]:
    """The memo's (fy, period_end, checked_on) for ``sym`` (`MONEY_MAP_FY_END_TTL_SECONDS`), or None —
    also for a malformed entry, which is ignored (fail open to the normal read: it can only cost a read)."""
    hit = _memo_get(_money_map_fy_end_key(sym))
    if hit is _MISS or not isinstance(hit, tuple) or len(hit) != 3:
        return None
    fy, period_end, checked = hit
    if not isinstance(fy, str) or not isinstance(period_end, date) or not isinstance(checked, date):
        return None
    return fy, period_end, checked


def _money_map_known_age(ctx: _Ctx, sym: str, fy: str) -> Optional[str]:
    """The age refusal of a breakdown still on a year the memo has already read (the review of 2026-10-16,
    MM-4): the same `_money_map_age` rule the statement read applies, answered before the profit-power /
    facts / statement / profile fan-out — or None (unknown year, another year, or a fresh one: read it).
    A refusal refreshes the entry's ``checked_on`` (the order puts the least recently checked first)."""
    known = _money_map_known(sym)
    if known is None or known[0] != fy:
        return None
    period_end = known[1]
    reason = _money_map_age(period_end, ctx.run_date)
    if reason is not None:
        _memo_put(_money_map_fy_end_key(sym), (fy, period_end, ctx.run_date), MONEY_MAP_FY_END_TTL_SECONDS)
        logger.info("company news money_map: %s is still on FY%s (ended %s, %d days before the run; last "
                    "checked %s) — %s, refused before the statement and profile reads", sym, fy,
                    period_end.isoformat(), (ctx.run_date - period_end).days, known[2].isoformat(), reason)
    return reason


def _money_map_refused_key(sym: str) -> Tuple[str, str]:
    return ("money_map_refused", sym)


def _money_map_refused(sym: str) -> Optional[Tuple[str, str, date, date]]:
    """The memo's last refusal of ``sym`` — (fy, reason, checked_on, read_on) (B3PD-1) — or None; also for a
    malformed entry, which is ignored (fail open to the normal read and order: it can only cost a read)."""
    hit = _memo_get(_money_map_refused_key(sym))
    if hit is _MISS or not isinstance(hit, tuple) or len(hit) != 4:
        return None
    fy, reason, checked, read = hit
    if not (isinstance(fy, str) and isinstance(reason, str) and isinstance(checked, date)
            and isinstance(read, date)):
        return None
    return fy, reason, checked, read


def _money_map_remember_refusal(sym: str, fy: Any, reason: str, run_date: date) -> None:
    """Record that a READ of ``sym`` on ``run_date`` found it unable to publish (B3PD-1): its breakdown's year
    (``""`` when it names none), the reason, and the run day as both the last check and the last full read."""
    year = fy if isinstance(fy, str) and re.fullmatch(r"[0-9]{4}", fy) else ""
    _memo_put(_money_map_refused_key(sym), (year, reason, run_date, run_date), MONEY_MAP_FY_END_TTL_SECONDS)


def _money_map_known_refusal(ctx: _Ctx, sym: str, fy: str) -> Optional[str]:
    """The refusal a re-read of the SAME fiscal year ``fy`` takes from the memo before the profit-power / facts /
    statement / profile fan-out (B3PD-1) — or None: read it in full. Only a content refusal the memo learned from a
    full read of that year at most `MONEY_MAP_REFUSAL_RECHECK_DAYS` ago (never a gap, an age refusal or
    `already_posted`: `_MONEY_MAP_NOT_REMEMBERED`). An answer refreshes the entry's ``checked_on`` (the order puts
    the least recently checked first) and keeps its ``read_on``."""
    known = _money_map_refused(sym)
    if known is None or known[0] != fy or known[1] in _MONEY_MAP_NOT_REMEMBERED:
        return None
    age = (ctx.run_date - known[3]).days
    if not 0 <= age < MONEY_MAP_REFUSAL_RECHECK_DAYS:
        return None
    _memo_put(_money_map_refused_key(sym), (fy, known[1], ctx.run_date, known[3]), MONEY_MAP_FY_END_TTL_SECONDS)
    logger.info("company news money_map: %s is still on FY%s, refused %s when read in full on %s (%d days before "
                "the run) — refused again before the statement and profile reads", sym, fy, known[1],
                known[3].isoformat(), age)
    return known[1]


def _money_map_held_since(sym: str, posted: Mapping[str, AbstractSet[str]], run_date: date) -> Optional[date]:
    """The run day the memo last found ``sym`` unable to publish now, or None (it may publish: it keeps its place
    in its group). Held: a known year that is due or stale, or already posted (the year-end memo, MM-4/MM-4-R) —
    or a remembered refusal (B3PD-1) that is the LATEST thing learned about the symbol (checked no earlier than
    the year-end entry; a record drops the refusal, so an older one never outlives a newer year)."""
    held: List[date] = []
    known = _money_map_known(sym)
    if known is not None and (_money_map_age(known[1], run_date) is not None or known[0] in posted.get(sym, ())):
        held.append(known[2])
    refused = _money_map_refused(sym)
    if refused is not None and (known is None or refused[2] >= known[2]):
        held.append(refused[2])
    return max(held) if held else None


def _money_map_order(pool: Sequence[str], posted: Mapping[str, AbstractSet[str]], run_date: date) -> List[str]:
    """The candidate order (the review of 2026-10-16, MM-4; the re-review of 2026-10-19, MM-4-R; B3PD-1 of
    2026-10-20): symbols never posted, then those posted for an earlier fiscal year (``posted``: symbol → the
    fiscal years its ledger keys name) — and, AFTER both groups, every symbol the memo KNOWS cannot publish now
    (`_money_map_held_since`): on a due (or stale) year, on the very year already posted (`already_posted` after
    its read), or refused when it was last read (a content refusal of its current year, an upstream gap), least
    recently checked first (never posted before posted, then the pool order, break a tie). Such a symbol never
    outranks a candidate that can publish: before, a never-posted symbol known to be due still came before every
    posted one, and in a February week fourteen of them used up the breakdown reads (`MONEY_MAP_MAX_LOOKUPS`)
    while a posted symbol's NEW fiscal year (a fresh 10-K) waited behind them; and (B3PD-1) never-posted symbols
    refused every day kept the head of the pool and spent every call's content cap, so the walk never reached the
    candidates behind them. The order only decides which candidates those reads reach first; every read of a held
    symbol refreshes its check, so each waiting symbol is re-read in turn (a new 10-K is found whichever it is)."""
    def key(item: Tuple[int, str]) -> Tuple[int, date, int, int]:
        i, sym = item
        group = 1 if sym in posted else 0
        held = _money_map_held_since(sym, posted, run_date)
        if held is None:
            return (group, date.min, 0, i)
        return (2, held, group, i)

    return [sym for _i, sym in sorted(enumerate(pool), key=key)]


async def _money_map_record(ctx: _Ctx, sym: str, bd: Any, fy: str) -> Any:
    """The gates of contract D6 / adapter design §3.6 → `MoneyMap`, or a rejection reason."""
    if list(getattr(bd, "degraded", None) or ()):
        return "money_map_degraded"
    results = await asyncio.gather(
        ctx.step("money_map_profit", lambda: ctx.profit.get_profit_power(sym), reason="revenue_unavailable"),
        ctx.step("money_map_facts", lambda: ctx.facts(sym, need_executives=False), reason="revenue_unavailable"),
        ctx.step("money_map_income", lambda: ctx.fmp.get_income_statement(sym, "annual", limit=5),
                 reason="revenue_unavailable"),
        _profiles(ctx, [sym]),
        return_exceptions=True,
    )
    for r in results:
        if isinstance(r, BaseException):
            raise r
    profit, facts, income, profiles = results
    if list(getattr(profit, "degraded", None) or ()):
        return "money_map_degraded"
    if not isinstance(facts, Mapping) or not facts.get("available"):
        if isinstance(facts, Mapping) and facts.get("upstream"):
            raise MarketingNewsUnavailable(ctx.series, "revenue_unavailable", "stage=money_map_facts; upstream")
        return "profile_missing"
    if facts.get("is_etf") is True or facts.get("is_fund") is True:
        return "etf_or_fund"
    profile = profiles.get(sym)
    company = R.company_from_profile(profile, sym, purpose="listing")
    if isinstance(company, str):
        return company
    sector = facts.get("sector") or (profile or {}).get("sector")
    if not isinstance(sector, str) or not sector.strip() or sector.strip() in R.MONEY_MAP_EXCLUDED_SECTORS:
        return "sector_excluded"             # an unknown sector cannot be shown to be non-financial

    if not isinstance(income, list):
        raise MarketingNewsUnavailable(ctx.series, "revenue_unavailable",
                                       f"stage=money_map_income; {type(income).__name__}, not a list")
    rows = sorted((r for r in income if isinstance(r, dict) and _row_fy(r) == fy),
                  key=lambda r: str(r.get("date") or ""), reverse=True)
    if not rows:
        return "money_map_degraded"
    row = rows[0]
    if str(row.get("reportedCurrency") or "").strip().upper() != "USD":
        return "non_usd_reporter"
    period_end = _day(row.get("date"))
    if period_end is None:
        return "money_map_stale"
    # The review of 2026-10-16 (MM-4): remember the year's end — the next read of a breakdown still on this
    # year is judged without the fan-out above, and a due symbol is tried after the others of its group.
    _memo_put(_money_map_fy_end_key(sym), (fy, period_end, ctx.run_date), MONEY_MAP_FY_END_TTL_SECONDS)
    age_reason = _money_map_age(period_end, ctx.run_date)
    if age_reason == "money_map_next_year_due":
        # Batch 1 (2026-10-15): the next fiscal year has ended — "fiscal 2025" beside a filed or due fiscal
        # 2026 is last year's map. A date fact: `_collect_money_map` counts it as neither an attempt nor a
        # content refusal.
        logger.info("company news money_map: %s FY%s ended %s, %d days before the run (>= %d) — the next "
                    "fiscal year is due; refused until its annual report", sym, fy, period_end.isoformat(),
                    (ctx.run_date - period_end).days, MONEY_MAP_NEXT_YEAR_DUE_DAYS)
    if age_reason is not None:
        return age_reason
    revenue, reported = _num(row.get("revenue")), _num(getattr(bd, "reported_revenue", None))
    if revenue is None or reported is None or revenue <= 0 or reported <= 0 \
            or abs(revenue - reported) > MONEY_MAP_STATEMENT_TOLERANCE * reported:
        return "revenue_mismatch"
    net, net_bd = _num(row.get("netIncome")), _num(getattr(bd, "net_income", None))
    if net is None or net_bd is None or abs(net - net_bd) > MONEY_MAP_STATEMENT_TOLERANCE * abs(net_bd) + 1.0:
        return "net_income_mismatch"
    point = _annual_point(profit, fy)
    net_margin = _num(getattr(point, "net_margin", None))
    if net_margin is None or abs(net_margin - 100.0 * net_bd / reported) > MONEY_MAP_NET_MARGIN_TOLERANCE_PT:
        return "money_map_inconsistent"

    named, rest = _segments(bd)
    if len(named) < R.MONEY_MAP_SEGMENTS[0]:
        return "segments_thin"
    # Segments that add up to MORE than revenue are never published. The breakdown's
    # `intersegment_eliminations` is a residual (Σ segments − revenue), derived whether or not
    # the company reports an eliminations line — a feed double-listing a sub-line ("Products" +
    # "Wearables") would read "Sales between parts of the company, $40 billion", a figure the
    # company never stated. So an eliminations bar is never drawn (`eliminations_usd` stays None)
    # and the map is refused instead.
    elim_mag = _num(getattr(bd, "intersegment_eliminations", None))
    if elim_mag is not None and elim_mag > 0:
        return "money_map_inconsistent"
    gap = reported - (math.fsum(s.value_usd for s in named) + rest)
    if gap < -R.MONEY_MAP_SUM_TOLERANCE * reported:
        return "money_map_inconsistent"      # segments exceed revenue
    if gap > R.MONEY_MAP_SUM_TOLERANCE * reported:
        rest += gap                          # revenue no segment names → "Other", never a bar of its own
    other = rest if rest > 0 else None
    if other is not None and (other > max(s.value_usd for s in named)
                              or other > MONEY_MAP_MAX_OTHER_SHARE * reported):
        return "money_map_mostly_other"

    # Gross and operating profit are the statement row's own lines (review round 15), each
    # cross-checked to rounding against profit power — never a figure we derive (revenue − cost of
    # sales from the breakdown, gross − operating expenses), and operating profit never waits on the
    # gross bar. NOT "as reported" (Batch 1, 2026-10-15): FMP itself fills `grossProfit` as revenue
    # minus cost of revenue for an issuer that reports no gross subtotal (Amazon), so the post explains
    # gross profit that way and never claims the company stated it (`MONEY_MAP_BAR_TOLERANCE_PT`).
    gross = operating = None
    cos = _num(getattr(bd, "cost_of_sales", None))
    g = _reported_line(sym, fy, "grossProfit", row.get("grossProfit"), revenue,
                       getattr(point, "gross_margin", None))
    if g is not None and cos is not None and cos > 0 and g <= reported:
        gross = g                            # a company with no cost-of-sales line reports no gross profit
    o = _reported_line(sym, fy, "operatingIncome", row.get("operatingIncome"), revenue,
                       getattr(point, "operating_margin", None))
    statement_gross = _num(row.get("grossProfit"))
    if o is not None:
        if o > reported or (statement_gross not in (None, 0.0) and o > statement_gross):
            logger.warning("company news money_map: %s FY%s operatingIncome %.0f is above the statement's "
                           "revenue or gross profit — omitted, never guessed", sym, fy, o)
        else:
            operating = o
    # The round-2 review of 2026-10-20 (RR2-MM-TEMPLATE-WALL): a LOSS larger than revenue — the net line, or a gross
    # or operating line kept above — is refused HERE, exactly as the template refuses the record it would make
    # (`news_templates._money_map_parts`: ``implausible_figures``, |net|, |gross| or |operating| above revenue; a
    # profit above revenue never gets this far: the gross and operating lines above are omitted and the record
    # refuses the net line, `record_invalid`). Refused at the read, it is a content refusal the walk remembers
    # (B3PD-1). Before, such a record was built, spent one of the walk's `MONEY_MAP_MAX_ATTEMPTS`, cleared the
    # symbol's remembered refusal and was refused by the template, which nothing fed back: three never-posted
    # companies losing more than their revenue (Moderna-like) at the head of the pool ended the series every day.
    if net_bd < -reported or (gross is not None and gross < -reported) or (
            operating is not None and operating < -reported):
        logger.info("company news money_map: %s FY%s has a loss larger than its revenue %.0f (net %.0f, gross %s, "
                    "operating %s) — refused (money_map_loss_above_revenue): the template cannot draw it", sym, fy,
                    reported, net_bd, "none" if gross is None else f"{gross:.0f}",
                    "none" if operating is None else f"{operating:.0f}")
        return "money_map_loss_above_revenue"
    try:
        return R.MoneyMap(
            series="money_map", company=company, fiscal_year=fy, period_end=period_end,
            segments=tuple(named), other_usd=other, eliminations_usd=None,
            revenue_usd=reported, gross_profit_usd=gross, operating_profit_usd=operating,
            net_income_usd=net_bd,
        )
    except ValueError as e:
        logger.info("company news money_map: %s FY%s refused (%s)", sym, fy, _describe(e))
        return "record_invalid"


async def _collect_money_map(ctx: _Ctx) -> Candidates:
    """Owner decision 2 (2026-10-09): the curated seed FIRST, then the US-listed Trillion Club
    members — de-duplicated, every candidate through every gate. news-v23 (the restyle, owner
    2026-11-19: "make sure to use big or popular companies only"): the Emerging Frontiers theme members
    are no longer in the pool (they were its third source; a theme's own post still names them). Symbols never posted come before those posted for an earlier fiscal year, and after both
    groups every symbol the memo knows cannot publish now — a due year, the year already posted, or
    a refusal when it was last read (`_money_map_order`, the review of 2026-10-16, the re-review of
    2026-10-19, MM-4-R, and B3PD-1 of 2026-10-20) or when its template last refused its record
    (`remember_template_refusal`, RR2-MM-TEMPLATE-WALL). A due year (`money_map_next_year_due`) is a date
    fact: one breakdown read, never an attempt or a content refusal — and, once the memo knows the
    year, no fan-out either. A re-read of a year the memo already refused (B3PD-1) is answered the
    same way: one breakdown read, no fan-out, and it counts only with a record held (like a due year)
    — never toward the content cap."""
    # news-v23 (owner 2026-11-19 "make sure to use big or popular companies only"): the seed and the club ONLY
    pool = R.merge_money_map_pool(await _club_symbols(ctx))
    posted: Dict[str, Set[str]] = {}
    for ref in ctx.exclude:
        parts = ref.split(":") if isinstance(ref, str) else []
        if len(parts) == 4 and parts[0] + ":" == R.LEDGER_PREFIX and parts[1] == "money_map":
            posted.setdefault(parts[2], set()).add(parts[3])
    order = _money_map_order(pool, posted, ctx.run_date)

    records: List[Any] = []
    attempts = refusals = due = remembered = lookups = 0
    for sym in order:
        if (len(records) >= ctx.limit or attempts >= MONEY_MAP_MAX_ATTEMPTS
                # A record held: the old total cap (records + gaps + refusals, due years and remembered
                # refusals included) is back.
                or (records and attempts + refusals + due + remembered >= MONEY_MAP_MAX_ATTEMPTS)
                or refusals >= MONEY_MAP_MAX_CONTENT_REJECTIONS or lookups >= MONEY_MAP_MAX_LOOKUPS):
            break
        lookups += 1
        ctx.considered += 1
        answered = False
        try:
            bd = await ctx.step("money_map_breakdown", lambda s=sym: _breakdown(ctx, s, ("revenue_breakdown", s), 0),
                                reason="revenue_unavailable")
            fy = getattr(bd, "fiscal_year", None)
            if not isinstance(fy, str) or not re.fullmatch(r"[0-9]{4}", fy):
                attempts += 1
                ctx.reject("money_map_degraded")
                _money_map_remember_refusal(sym, fy, "money_map_degraded", ctx.run_date)
                continue
            if f"{R.LEDGER_PREFIX}money_map:{sym}:{fy}" in ctx.exclude:
                ctx.reject("already_posted")     # before any profile call
                # B3PD-1: the check is refreshed, so the posted symbols rotate behind every other held one
                _money_map_remember_refusal(sym, fy, "already_posted", ctx.run_date)
                continue
            # a year the memo already read and knows to be due (or stale), or refused when it was last read in
            # full: answered before the fan-out
            memo = _money_map_known_age(ctx, sym, fy) or _money_map_known_refusal(ctx, sym, fy)
            if memo is not None:
                got, answered = memo, True
            else:
                got = await _money_map_record(ctx, sym, bd, fy)
        except MarketingNewsUnavailable as e:
            if e.reason != "budget_exhausted" or not records:
                raise
            # Out of budget on a LATER candidate: the records found stand (each was built on
            # complete data); only an upstream failure or an empty hand is the series' answer.
            logger.warning(
                "company news money_map: budget exhausted at %s with %d record(s) held — returning them "
                "(lookups=%d attempts=%d refusals=%d due=%d remembered=%d; %s)", sym, len(records), lookups,
                attempts, refusals, due, remembered, e.detail)
            break
        if isinstance(got, str):
            ctx.reject(got)
            if not answered:
                _money_map_remember_refusal(sym, fy, got, ctx.run_date)
            if got in _MONEY_MAP_UPSTREAM_GAPS:
                attempts += 1
            elif got == "money_map_next_year_due":
                due += 1                     # a date fact: never an attempt, never a content refusal
            elif answered:
                remembered += 1              # the memo's answer (B3PD-1): one breakdown read, no fan-out
            else:
                refusals += 1                # deterministic: never eats an upstream attempt
        else:
            attempts += 1
            records.append(got)
            _MEMO.pop(_money_map_refused_key(sym), None)     # it can publish: back in its own group
    ctx.check_profiles()
    return ctx.done(records, "money_map_none_qualified")


def remember_template_refusal(record: Any, codes: Iterable[Any] = (), *, run_date: date) -> bool:
    """Remember that the TEMPLATE refused a record this module offered (the round-2 review of 2026-10-20,
    RR2-MM-TEMPLATE-WALL) — called by the script flow (`MarketingScriptService._build_template_day`) for every
    candidate its template refused (``codes``: the template's refusal codes, for the log only). For a `MoneyMap`
    it writes the B3PD-1 refusal memo (`_money_map_remember_refusal`) for the record's symbol and fiscal year as
    ``template_refused``, read on ``run_date``: from the next call the symbol waits behind every candidate that can
    publish (`_money_map_order`), and a re-read of that year is answered before the fan-out for
    `MONEY_MAP_REFUSAL_RECHECK_DAYS` (`_money_map_known_refusal`); a new fiscal year, or a full re-read the
    template accepts, lifts it, like any remembered refusal. Before, only the read's own refusals were remembered: a
    record the template refuses (a segment named like a web address, or a gate the read does not apply) was
    rebuilt and offered first every day, each one spending one of the walk's `MONEY_MAP_MAX_ATTEMPTS`, so three of
    them at the head of the pool ended the series every day.

    The round-3 review of 2026-10-20 (RR3-STAKES-TEMPLATE-WALL): a `CompanyStake` is remembered too, by its stake id
    with its row's fingerprint (`_stake_fingerprint`) and the run day, for `STAKE_TEMPLATE_REFUSAL_TTL_SECONDS`: from
    the next call that stake is offered after every other candidate (`_collect_company_stakes`; an edit to its row
    lifts it) — the same wall, which five refused stakes at the head of the newest-first walk built every day.

    news-v23 (one investor's stakes in several public companies a post): a `CompanyStakes` the template refused WHOLE
    is remembered stake by stake (each the same entry), and a single `CompanyStake` is still accepted — the script flow
    reports each stake its template DROPPED from a post (`news_templates.drop_refused_rows`) that way, so a stake only
    the template refuses never wins its investor the head of the walk again (`_stakes_records`).

    True when remembered; False — nothing written — for any other record (the other series' walks are bounded by
    their windows, or not in the planned series) or a run date that is not a date. Process-local like the whole memo
    (a new process re-learns it at the first refusal); never raises on a well-formed call."""
    if isinstance(run_date, datetime):
        run_date = run_date.date()
    if not isinstance(record, (R.MoneyMap, R.CompanyStake, R.CompanyStakes)) or not isinstance(run_date, date):
        return False
    if not isinstance(codes, (list, tuple, set, frozenset)):
        codes = (codes,)                 # one bare code (or junk): the log names what it can
    said = sorted({c[:40] for c in codes if isinstance(c, str)})
    if isinstance(record, (R.CompanyStake, R.CompanyStakes)):
        for stake in (record.stakes if isinstance(record, R.CompanyStakes) else (record,)):
            _memo_put(_stake_refused_key(stake.stake_id), (_stake_fingerprint(stake), run_date),
                      STAKE_TEMPLATE_REFUSAL_TTL_SECONDS)
            logger.info("company news company_stakes: stake %s was refused by its template on %s (%s) — remembered: "
                        "tried after every other candidate until its row changes", stake.stake_id,
                        run_date.isoformat(), ", ".join(said[:6]) or "no code")
        return True
    sym, fy = record.company.symbol, record.fiscal_year
    _money_map_remember_refusal(sym, fy, _TEMPLATE_REFUSED, run_date)
    logger.info("company news money_map: %s FY%s was refused by its template on %s (%s) — remembered "
                "(%s): tried after every candidate that can publish, its fiscal year answered from the memo for "
                "%d days", sym, fy, run_date.isoformat(), ", ".join(said[:6]) or "no code", _TEMPLATE_REFUSED,
                MONEY_MAP_REFUSAL_RECHECK_DAYS)
    return True


# ── congress_count (Drop 2b) ──────────────────────────────────────────────────
#
# "N members of Congress disclosed purchases of X stock in <month>": a COUNT, never a member.
# Every member-identity field is dropped at the source (`_scrub_congress`: only salted, per-call
# hashes survive, inside `_congress_month`, and the memo holds the counts alone). The count is
# refused whenever it could be wrong either way: an unidentifiable member, identity fields that
# do not say whether two rows are one member or two, or an in-month purchase of the same company
# under another share class or under no usable symbol. Purchases only (never an exchange or a
# sale), stock only (never an option, a bond, a fund), both chambers together (no split), and the
# month is read only when both feeds provably reach past it — twice, with the two reads equal.


@dataclass(frozen=True)
class _CongressRow:
    """One Congress feed row AFTER the source scrub. No identity field survives: only salted,
    per-call hashes of the identity keys, which never leave `_congress_month`."""

    symbol: str                   # the raw symbol, stripped ("" when none)
    disclosed: Optional[date]
    kind: str                     # folded transaction type: "purchase", "sale (full)", "exchange" …
    asset: str                    # folded asset type: "stock", "stock option" …
    description: str              # folded asset description, capped (stock purchases only)
    ids: Tuple[str, ...]          # hashes of the strict identity keys (an id, the full name, an office)
    last: Optional[str]           # hash of the folded last name; None → the member is unidentifiable


def _ident_text(v: Any) -> str:
    """An identity value as folded ASCII words ("Sánchez Jr." → "sanchez jr"); "" when absent,
    not a string or an int, or over-long."""
    if isinstance(v, int) and not isinstance(v, bool):
        v = str(v)
    if not isinstance(v, str) or len(v) > _IDENT_MAX:
        return ""
    s = unicodedata.normalize("NFKD", v).encode("ascii", "ignore").decode("ascii").lower()
    return " ".join(_IDENT_WORD_RE.findall(s))


def _fold_text(v: Any, limit: int) -> str:
    """Lower-case ASCII, whitespace collapsed, capped (punctuation kept: "(EA)" stays "(ea)")."""
    if not isinstance(v, str):
        return ""
    s = unicodedata.normalize("NFKD", v[: limit * 2]).encode("ascii", "ignore").decode("ascii").lower()
    return " ".join(s.split())[:limit]


def _scrub_congress(raw: Sequence[Any], chamber: str, salt: str) -> List[_CongressRow]:
    """The source scrub: each feed row → a `_CongressRow`. Identity (`CONGRESS_IDENTITY_FIELDS`)
    is read once, hashed with the call's ``salt`` and dropped; nothing else of it is kept.

    Strict identity keys (any one shared → the same member): the feed's member id
    (``senateID``), the full name (last + first) and an office / senator / representative name —
    each chamber-scoped. The last name alone is kept apart, unscoped, to catch two rows the
    strict keys cannot tell apart (`_members`)."""
    def h(key: str) -> str:
        return hashlib.sha256(f"{salt}|{key}".encode("utf-8")).hexdigest()

    out: List[_CongressRow] = []
    for r in raw:
        if not isinstance(r, dict):
            continue
        last = " ".join(w for w in _ident_text(r.get("lastName") or r.get("last_name")).split()
                        if w not in _IDENT_SUFFIXES)
        first = _ident_text(r.get("firstName") or r.get("first_name"))
        ids: List[str] = []
        member_id = _ident_text(r.get("senateID"))
        if member_id:
            ids.append(h(f"{chamber}|id|{member_id}"))
        if last:
            ids.append(h(f"{chamber}|name|{last}|{first}"))
        for field_name in ("office", "senator", "representative"):
            office = _ident_text(r.get(field_name))
            if office:
                ids.append(h(f"{chamber}|office|{office}"))
        kind = " ".join(str(r.get("type") or "").lower().split())[:40]
        asset = " ".join(str(r.get("assetType") or "").lower().split())[:40]
        symbol = r.get("symbol")
        out.append(_CongressRow(
            symbol=symbol.strip()[:16] if isinstance(symbol, str) else "",
            disclosed=_day(r.get("disclosureDate")) if isinstance(r.get("disclosureDate"), str) else None,
            kind=kind,
            asset=asset,
            description=_fold_text(r.get("assetDescription"), _DESC_MAX) if kind == "purchase" else "",
            ids=tuple(ids),
            last=h(f"last|{last}") if last else None,
        ))
    return out


def _feed_problem(rows: Sequence[_CongressRow], month_start: date) -> Optional[str]:
    """None when one chamber's feed, IN ITS OWN ORDER, provably holds every row disclosed on or
    after ``month_start``: every disclosure date parses, the dates never rise (newest first,
    verified 2026-10-09 — a single rise means the order the walk relies on does not hold), and
    the last row is dated more than `CONGRESS_COVERAGE_MARGIN_DAYS` before the month.
    ``congress_feed_unordered`` / ``"uncovered"`` (a longer walk may cover it) otherwise."""
    dates = [r.disclosed for r in rows]
    if not dates or any(d is None for d in dates):
        return "congress_feed_unordered"
    if any(a < b for a, b in zip(dates, dates[1:])):
        return "congress_feed_unordered"
    if dates[-1] < month_start - timedelta(days=CONGRESS_COVERAGE_MARGIN_DAYS):
        return None
    return "uncovered"


def _members(rows: Sequence[_CongressRow]) -> Tuple[int, Optional[str]]:
    """(distinct members, None) among one symbol's in-month purchases, or (0, reason).

    Rows sharing ANY strict identity key are one member (union-find over the hashed keys). The
    count stands only when it is certain both ways: every row has a last name
    (``member_unidentifiable`` otherwise), no member's rows carry two last names, and no last
    name spans two members (``member_ambiguous`` — "Rick Scott" and "Tim Scott", or one member
    filed under two first-name spellings: either could make the figure wrong)."""
    if not rows:
        return 0, None
    if any(r.last is None for r in rows):
        return 0, "member_unidentifiable"
    parent: Dict[Any, Any] = {}

    def find(x: Any) -> Any:
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i, r in enumerate(rows):
        node = ("row", i)
        find(node)
        for k in r.ids:
            parent[find(("key", k))] = find(node)
    groups: Dict[Any, Set[str]] = {}
    for i, r in enumerate(rows):
        groups.setdefault(find(("row", i)), set()).add(r.last)
    if any(len(lasts) != 1 for lasts in groups.values()):
        return 0, "member_ambiguous"
    if len({r.last for r in rows}) != len(groups):
        return 0, "member_ambiguous"
    return len(groups), None


def _in_month_signature(rows: Sequence[_CongressRow], start: date, end: date) -> Counter:
    return Counter(r for r in rows if r.disclosed is not None and start <= r.disclosed <= end)


def _count_month(rows: Sequence[_CongressRow], start: date, end: date) -> Dict[str, Any]:
    """The month's aggregate, identity-free: ``counts`` {symbol: members} (certain counts only),
    ``refused`` {symbol: reason}, ``rejections`` {reason: n} and ``stock_purchases`` — every
    in-month stock purchase as (canonical symbol or None, folded description) for the issuer
    check. Purchases only ("purchase" exactly — never an exchange or a sale); stock only (the
    asset type exactly "stock": an option, a bond, a fund is not counted). A symbol is refused
    (``asset_uncertain``) when one of its in-month purchases has an asset type that is neither
    "stock" nor clearly something else (missing, "Other", "REIT" …), or a "stock" row whose
    description names another instrument (an option, a warrant, a note …)."""
    rejections: Counter = Counter()
    per_symbol: Dict[str, List[_CongressRow]] = {}
    stock: List[Tuple[Optional[str], str]] = []
    bad_symbols: Dict[str, str] = {}
    uncertain: Set[str] = set()
    for r in rows:
        if r.disclosed is None or not (start <= r.disclosed <= end):
            continue
        if "exchange" in r.kind:
            rejections["exchange_type"] += 1
            continue
        if r.kind != "purchase":
            continue                          # a sale: not what the post counts
        sym = R.canonical_symbol(r.symbol)
        if r.asset != "stock":
            rejections["option_asset"] += 1
            if r.asset not in _CONGRESS_NON_STOCK_ASSETS:
                # Not clearly anything but stock: it may be one more purchase of the stock.
                stock.append((sym, r.description))
                if sym is not None:
                    uncertain.add(sym)
            continue
        stock.append((sym, r.description))
        if sym is None:
            bad_symbols[r.symbol] = R.symbol_problem(r.symbol) or "symbol_grammar"
            continue
        if _NON_COMMON_DESC_RE.search(r.description):
            uncertain.add(sym)
        per_symbol.setdefault(sym, []).append(r)
    for problem in bad_symbols.values():
        rejections[problem] += 1
    counts: Dict[str, int] = {}
    refused: Dict[str, str] = {}
    for sym, sym_rows in per_symbol.items():
        n, why = _members(sym_rows)
        if why is None and sym in uncertain:
            why = "asset_uncertain"
        if why is not None:
            refused[sym] = why
        else:
            counts[sym] = n
    return {"counts": counts, "refused": refused, "rejections": dict(rejections),
            "stock_purchases": stock}


async def _congress_month(fmp: Any, month: str) -> Dict[str, Any]:
    """Walk both chambers until each provably reaches past ``month`` (`CONGRESS_WALK_LIMITS`),
    read the covering pages once more and require the month's rows of both reads to be equal (a
    row inserted while the parallel pages were read can shift a page boundary and LOSE a row
    without breaking the order), then count. Returns the identity-free aggregate only; the
    salted hashes die with this frame. Any failed, partial or empty read →
    ``congress_feed_unavailable``; a rising date → ``congress_feed_unordered``; not covered at
    the largest walk → ``congress_window_uncovered``."""
    start = date(int(month[:4]), int(month[5:7]), 1)
    end = R.month_end_of(month)
    salt = secrets.token_hex(16)

    def unavailable(reason: str, detail: str) -> MarketingNewsUnavailable:
        return MarketingNewsUnavailable("congress_count", reason, f"stage=congress_walk; {detail}")

    async def read(chamber: str, method: str, limit: int) -> List[_CongressRow]:
        try:
            raw = await getattr(fmp, method)(limit)
        except Exception as e:  # noqa: BLE001 — a partial page set or an outage: fail closed
            raise unavailable("congress_feed_unavailable", f"{chamber} read failed ({_describe(e)})") from e
        if not isinstance(raw, list) or not raw:
            # `[]` is the client's every-page-403/404 degrade — or an outage it swallowed.
            raise unavailable("congress_feed_unavailable", f"{chamber} returned no rows")
        return _scrub_congress(raw, chamber, salt)

    covered: Dict[str, Tuple[int, List[_CongressRow]]] = {}
    for limit in CONGRESS_WALK_LIMITS:
        todo = [(c, m) for c, m in _CONGRESS_CHAMBERS if c not in covered]
        reads = await asyncio.gather(*(read(c, m, limit) for c, m in todo), return_exceptions=True)
        for got in reads:
            if isinstance(got, BaseException):
                raise got
        for (chamber, _m), rows in zip(todo, reads):
            problem = _feed_problem(rows, start)
            if problem == "congress_feed_unordered":
                raise unavailable("congress_feed_unordered", f"{chamber} at {limit} rows")
            if problem is None:
                covered[chamber] = (limit, rows)
        if len(covered) == len(_CONGRESS_CHAMBERS):
            break
    missing = [c for c, _m in _CONGRESS_CHAMBERS if c not in covered]
    if missing:
        raise unavailable("congress_window_uncovered",
                          f"{','.join(missing)} did not reach {start} within {CONGRESS_WALK_LIMITS[-1]} rows")

    confirms = await asyncio.gather(*(read(c, m, covered[c][0]) for c, m in _CONGRESS_CHAMBERS),
                                    return_exceptions=True)
    for got in confirms:
        if isinstance(got, BaseException):
            raise got
    for (chamber, _m), again in zip(_CONGRESS_CHAMBERS, confirms):
        if _in_month_signature(covered[chamber][1], start, end) != _in_month_signature(again, start, end):
            raise unavailable("congress_feed_unavailable",
                              f"{chamber}: the month's rows differ between two reads (the feed moved)")
    rows = [r for c, _m in _CONGRESS_CHAMBERS for r in covered[c][1]]
    return _count_month(rows, start, end)


def _words(text: str) -> Set[str]:
    return set(_IDENT_WORD_RE.findall(text))


def _compact_named(name_words: Sequence[str], desc: str) -> bool:
    """Review round 9 (#11): does a run of CONSECUTIVE description words spell the display name's
    significant words run together ("jp morgan chase" → "jpmorganchase", "exxonmobil",
    "united health" → "unitedhealth")? Word-aligned on purpose: a bare substring would refuse
    "Visa" inside "advisa…" and "Meta" inside "metals". Linear in the description's words."""
    compact = "".join(name_words)
    if len(compact) < 2:
        return False
    words = _IDENT_WORD_RE.findall(desc)
    for i in range(len(words)):
        joined = ""
        for w in words[i:]:
            joined += w
            if len(joined) >= len(compact):
                if joined == compact:
                    return True
                break
            if not compact.startswith(joined):
                break
    return False


def _issuer_clash(symbol: str, company: "R.CompanyRef",
                  stock_purchases: Sequence[Tuple[Optional[str], str]]) -> Optional[str]:
    """Another in-month stock purchase that may be the SAME company (the count would be low):
    another share class of the symbol's root (BRK-A beside BRK-B → ``share_class_overlap``), or
    a row under another or no usable symbol whose description names the company — every
    significant word of its display name, or "(SYM)" / "SYM - …" (``share_class_overlap`` /
    ``unmapped_purchase``), or — review round 9 — those words run together or split another way
    on a word boundary (`_compact_named`: ``share_class_overlap`` / ``count_uncertain``). Errs
    toward refusing: an unrelated description that happens to hold the name costs the candidate."""
    root = symbol.split("-")[0]
    ordered = _IDENT_WORD_RE.findall(_fold_text(company.name, 64))
    name_words = set(ordered)
    significant = name_words - _GENERIC_NAME_WORDS or name_words
    # The significant words in NAME order, for the run-together spelling ("JPMorgan Chase").
    in_order = [w for w in ordered if w in significant]
    tags = {s.lower() for s in (symbol, symbol.replace("-", "."), symbol.replace("-", ""))}
    for other, desc in stock_purchases:
        if other == symbol:
            continue
        if other is not None and other.split("-")[0] == root:
            return "share_class_overlap"
        named = bool(significant) and significant <= _words(desc)
        tagged = any(f"({t})" in desc or desc.startswith(f"{t} -") for t in tags)
        if named or tagged:
            return "unmapped_purchase" if other is None else "share_class_overlap"
        # Review round 9 (#11): the same company spelled with its words run together or split
        # differently ("JP Morgan Chase & Co." for "JPMorgan Chase & Co.", "ExxonMobil Corp",
        # "United Health Group Inc") — the count could be low.
        if _compact_named(in_order, desc):
            return "count_uncertain" if other is None else "share_class_overlap"
    return None


async def _collect_congress_count(ctx: _Ctx) -> Candidates:
    month = selection.congress_disclosure_month(ctx.run_date)
    end = R.month_end_of(month)
    if ctx.run_date < end + timedelta(days=CONGRESS_DUE_DAYS):
        return ctx.done((), "congress_not_due")
    key = f"{R.LEDGER_PREFIX}congress_count:{month}"
    if key in ctx.exclude:                  # the month is posted: no feed, no profile call
        ctx.reject("already_posted")
        return ctx.done((), "congress_none_qualified")
    agg = await ctx.step(
        "congress_walk",
        lambda: _shared(("congress", month), CONGRESS_TTL_SECONDS, lambda: _congress_month(ctx.fmp, month)),
        reason="congress_feed_unavailable",
    )
    for reason, n in sorted(agg["rejections"].items()):
        ctx.reject(reason, n)
    for _sym, why in sorted(agg["refused"].items()):
        ctx.reject(why)
    ranked = sorted(((n, s) for s, n in agg["counts"].items() if n >= R.CONGRESS_MIN_MEMBERS),
                    key=lambda t: (-t[0], t[1]))
    ctx.considered = len(ranked)
    top = ranked[:CONGRESS_PROFILE_CANDIDATES]
    profiles = await _profiles(ctx, [s for _n, s in top]) if top else {}
    found: List[Any] = []
    for n, sym in top:
        company = R.company_from_profile(profiles.get(sym), sym, purpose="common")
        if isinstance(company, str):
            ctx.reject(company)
            continue
        clash = _issuer_clash(sym, company, agg["stock_purchases"])
        if clash:
            ctx.reject(clash)
            logger.info("company news congress_count: %s refused (%s) — another in-month purchase may "
                        "be the same company", sym, clash)
            continue
        try:
            found.append(R.CongressCount(series="congress_count", company=company, month=month,
                                         members=n, fetched_on=ctx.run_date))
        except ValueError as e:
            logger.info("company news congress_count: %s refused as record_invalid (%s)", sym, _describe(e))
            ctx.reject("record_invalid")
    ctx.check_profiles()
    # Backstop: two candidates that resolved to one display name are one company counted twice.
    by_name = Counter(rec.company.name.casefold() for rec in found)
    counts = []
    for rec in found:
        if by_name[rec.company.name.casefold()] > 1:
            ctx.reject("share_class_overlap")
        else:
            counts.append(rec)
    return _congress_month_record(ctx, month, counts)


def _congress_month_record(ctx: _Ctx, month: str, counts: Sequence[Any]) -> Candidates:
    """news-v23 (the restyle, owner 2026-11-19: "for congress disclosed, i want to have 2-3 companies at once"): the
    ONE `R.CongressMonth` record of the month — the companies whose counts passed every gate, the most members first
    (then the symbol), up to `R.CONGRESS_MONTH_COMPANIES`' most; one whose issuer root is already in the post
    (`R.issuer_root`: `_issuer_clash` should have refused it) is left out as `share_class_overlap`. Fewer than the
    post's minimum: no record — ``congress_too_few_companies`` (exactly one qualified) or ``congress_none_qualified``
    (none). Its ledger key is the month's, so every company it draws is posted once for the month."""
    lo, hi = R.CONGRESS_MONTH_COMPANIES
    picked: List[Any] = []
    for rec in counts:
        if len(picked) >= hi:
            break
        if any(R.issuer_root(rec.company.symbol) == R.issuer_root(p.company.symbol) for p in picked):
            logger.info("company news congress_count: %s left out as share_class_overlap — its issuer is already in "
                        "the post", rec.company.symbol)
            ctx.reject("share_class_overlap")
            continue
        picked.append(rec)
    if len(picked) < lo:
        if picked:
            logger.info("company news congress_count: only %s qualified for %s — a Congress Count shows at least %d "
                        "companies, so none is posted (congress_too_few_companies)", picked[0].company.symbol, month,
                        lo)
        return ctx.done((), "congress_too_few_companies" if picked else "congress_none_qualified")
    try:
        rec = R.CongressMonth(series="congress_count", month=month, fetched_on=ctx.run_date, counts=tuple(picked))
    except ValueError as e:
        logger.error("company news congress_count: the month's %d counts (%s) do not make a record (%s) — none is "
                     "posted", len(picked), ",".join(c.company.symbol for c in picked), _describe(e))
        ctx.reject("record_invalid")
        return ctx.done((), "congress_none_qualified")
    logger.info("company news congress_count: ONE post of %d companies for %s (%s), the most members first",
                len(picked), month, ",".join(f"{c.company.symbol}={c.members}" for c in picked))
    return ctx.done((rec,), "congress_none_qualified")


# ── company_stakes (Drop 2b) ──────────────────────────────────────────────────
#
# A Trillion Club member's disclosed stake in a NAMED company with a disclosed DOLLAR figure and
# its basis ("invested", "committed up to", "carrying value", "fair value" — never a price).
# Catalogue stakes are allowed (`R.STAKES_INCLUDE_CATALOGUE`, main-session decision 1), newest
# ``as_of`` first, each posted once (its ledger key). FMP-free for the data: the club group, its
# rows and one stakes read; one profile batch makes the CompanyRefs.
#
# The stake finder (migration 205, 2026-10-28) adds machine-made rows: a row reaches a post only
# when a person reviewed it — hand-kept (``origin`` seed), published by the owner's tap (proposal),
# or published by the finder and KEPT by the owner (auto + ``owner_kept_at``). The read filters on
# it in PostgREST and `_postable_stake_rows` re-checks every row before the known words are built.


@dataclass(frozen=True)
class _StakeCandidate:
    stake_id: str
    slug: str
    investor_symbol: str
    investee_symbol: Optional[str]
    investee_name: str
    kind: str
    value_usd: float
    value_basis: str
    ownership_pct: Optional[float]
    as_of: date
    verified_on: date
    source_title: str
    background: Optional[str]
    listed_since: Optional[date]
    local_listing: Optional[str]
    is_new: bool


def _stake_refused_key(stake_id: str) -> Tuple[str, str]:
    return ("stake_template_refused", stake_id)


def _stake_fingerprint(stake: Any) -> Tuple[Any, ...]:
    """What a stake's post is composed from that its ROW holds — the same values on a `_StakeCandidate` and on the
    `R.CompanyStake` built from it (RR3-STAKES-TEMPLATE-WALL). A remembered refusal holds only while the row still
    says this: an edit to any of it (a shortened title, a corrected name or basis) lifts it. Never ``verified_on``
    (a routine re-check of the source changes nothing the post says, and must not put a refused stake back at the
    head), the background (never drawn: `news_templates.STAKE_BACKGROUND_IN_CAPTIONS`) or the ownership share (no
    stake post states it)."""
    return (stake.investee_name, stake.kind, stake.value_usd, stake.value_basis, stake.as_of, stake.source_title,
            stake.local_listing, stake.listed_since)


def _stake_template_refused_on(stake: Any) -> Optional[date]:
    """The run day the template last refused ``stake`` (`remember_template_refusal`), or None: never refused, a
    malformed entry (ignored — it can only cost the stake its place), or a row edited since (`_stake_fingerprint`)."""
    hit = _memo_get(_stake_refused_key(stake.stake_id))
    if hit is _MISS or not isinstance(hit, tuple) or len(hit) != 2:
        return None
    fingerprint, refused_on = hit
    if not isinstance(refused_on, date) or fingerprint != _stake_fingerprint(stake):
        return None
    return refused_on


def _stake_left_out(row: Mapping[str, Any]) -> Optional[str]:
    """Why a stake row may not reach a public post because of WHO made it (migration 205's ``origin``), or None
    when it may: a hand-kept row (``seed``) and one the owner published with a tap (``proposal``) may; one the stake
    finder published on its own (``auto``) only once the owner tapped Keep on it — ``owner_kept_at`` holds the tap's
    timestamp, read as `_day` reads one — so a machine-found fact reaches a public post only after a person read it
    (``auto_not_kept`` otherwise). Strict, the read's PostgREST filter (`_STAKE_POSTABLE_FILTER`) checked again
    here: an origin that is not exactly one of those strings — missing, NULL, another case or spacing, a value a
    later migration adds — is ``origin_unknown``, and a stamp with no date in it (an empty string, ``"false"``,
    ``0``, ``True``, ``'infinity'``) keeps nothing. Pure."""
    origin = row.get("origin")
    if not isinstance(origin, str):
        return "origin_unknown"
    if origin in _STAKE_ORIGINS_POSTABLE:
        return None
    if origin == _STAKE_ORIGIN_AUTO:
        return None if _day(row.get("owner_kept_at")) is not None else "auto_not_kept"
    return "origin_unknown"


def _postable_stake_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The stake rows `_stake_left_out` lets through, in their order; the rest are counted in one log line per kind
    (no rejection code: they are not candidates the template could refuse) — never named (an un-reviewed row's
    investee name may not be a company's). An unknown origin is a WARNING naming the values: a new origin needs a
    decision in `_stake_left_out` before its rows can be posted."""
    postable: List[Dict[str, Any]] = []
    not_kept = 0
    unknown: Counter = Counter()
    for r in rows:
        why = _stake_left_out(r)
        if why is None:
            postable.append(r)
        elif why == "auto_not_kept":
            not_kept += 1
        else:
            unknown[_scrub(repr(r.get("origin")), 40)] += 1
    if not_kept:
        logger.info("company news company_stakes: %d of %d stake row(s) left out — found automatically by the stake "
                    "finder and not kept by the owner (origin %r, no Keep stamp): a machine-found stake reaches a "
                    "public post only after the owner's Keep or Publish tap", not_kept, len(rows), _STAKE_ORIGIN_AUTO)
    if unknown:
        logger.warning("company news company_stakes: %d of %d stake row(s) left out — an origin the marketing read "
                       "does not know (%s; it posts %s rows, and %r rows once kept): decide in _stake_left_out "
                       "before such rows may be posted", sum(unknown.values()), len(rows),
                       ", ".join(f"{o} ×{n}" for o, n in sorted(unknown.items())[:5]),
                       "/".join(_STAKE_ORIGINS_POSTABLE), _STAKE_ORIGIN_AUTO)
    return postable


def _stake_candidate(ctx: _Ctx, row: Mapping[str, Any], investors: Mapping[str, Optional[str]],
                     known: FrozenSet[str] = frozenset()) -> Optional[_StakeCandidate]:
    """One stake row through the pure gates (no I/O), or None (counted). ``known``: the casefolded words of the
    club's and the catalogue's company names (`R.stake_known_words`) — the re-review of 2026-10-19 (ST-1-R) holds
    the source title (refused, ``stake_source_unverified``) and the local listing (dropped) to a POSITIVE list
    (`R.stake_text_unknown_words`): a word that is neither a known company's nor a document, date or place word may
    be a person's name, and it would be drawn on the image, a card and every caption.

    The round-3 review of 2026-10-20 (RR3-STAKES-TEMPLATE-WALL): every gate of the template's that a stake ROW
    decides is applied here too, so a stake the template must refuse is never offered — before, five of them at the
    head of the newest-first walk were offered and refused every day while every publishable stake behind them
    waited. ``stake_invalid``, each logged with the field and its measure (never the text): a ``committed_up_to``
    basis on a kind other than ``commitment`` (the club's `stake_problem` checks only the other direction), a
    ``verified_on`` after the run date (the club's own check allows one day for ET vs UTC; the template refuses a
    stake dated after the run), an investee-name word longer than `R.STAKE_PAIR_NAME_WORD_MAX_CHARS` (the name sits
    under its plate in half the column), and a source title longer than `R.STAKE_SOURCE_DRAW_MAX_CHARS` or holding a
    word longer than `R.STAKE_TEXT_WORD_MAX_CHARS`. A local listing the template cannot draw — longer than
    `R.COMPANY_NAME_CHARS` [1] or holding a word longer than `R.STAKE_TEXT_WORD_MAX_CHARS` — is DROPPED, as an
    unknown word drops it: card 3 has a generic body. A refusal only the template can make (its slot rules, its
    person check) is reported back by the script flow and remembered (`remember_template_refusal`): that stake then
    waits behind every other candidate (`_collect_company_stakes`)."""
    slug = row.get("company_slug")
    if slug not in investors:
        return None                           # another company's row (defensive; the read filters)
    try:
        stake_id = str(uuid.UUID(str(row.get("id")))) if isinstance(row.get("id"), str) else None
    except ValueError:
        stake_id = None
    if stake_id is None:
        ctx.reject("stake_invalid")
        return None
    problem = stake_problem(row, ctx.run_date)
    if problem is not None:
        logger.info("company news company_stakes: stake %s refused as stake_invalid (%s)", stake_id, _scrub(problem))
        ctx.reject("stake_invalid")
        return None
    name = row.get("investee_name")
    if not isinstance(name, str):
        ctx.reject("stake_invalid")
        return None
    if "(" in name or "not named" in name.lower():
        ctx.reject("stake_aggregate")
        return None
    value, basis = _num(row.get("disclosed_value_usd")), row.get("value_basis")
    if value is None or value <= 0 or basis not in R.VALUE_BASES:
        ctx.reject("stake_no_figure")
        return None
    if value > R.STAKE_MAX_VALUE_USD:
        ctx.reject("stake_invalid")           # a unit error, not a stake
        return None
    if basis == "committed_up_to" and row.get("kind") != "commitment":
        # RR3-STAKES-TEMPLATE-WALL: "committed up to" is a commitment's basis only (the template refuses the
        # pair either way round; `stake_problem` checked only that a commitment carries it)
        logger.info("company news company_stakes: stake %s refused as stake_invalid — a committed_up_to basis on "
                    "a %s stake (the template says it only of a commitment)", stake_id, _scrub(row.get("kind")))
        ctx.reject("stake_invalid")
        return None
    as_of, verified_on = _day(row.get("as_of")), _day(row.get("verified_on"))
    if as_of is None or verified_on is None or as_of > verified_on or as_of > ctx.run_date:
        ctx.reject("stake_invalid")
        return None
    if verified_on > ctx.run_date:
        # RR3-STAKES-TEMPLATE-WALL: the club allows a day for ET vs UTC; the template refuses a stake dated after
        # the run (refused today, read again tomorrow)
        logger.info("company news company_stakes: stake %s refused as stake_invalid — verified on %s, after the "
                    "run date %s", stake_id, verified_on.isoformat(), ctx.run_date.isoformat())
        ctx.reject("stake_invalid")
        return None
    if (ctx.run_date - verified_on).days > R.STAKE_STALE_DAYS:
        ctx.reject("stake_stale")
        return None
    if (ctx.run_date - as_of).days > R.STAKE_MAX_AGE_DAYS:
        ctx.reject("stake_too_old")
        return None
    created = _day(row.get("created_at"))
    is_new = created is not None and created >= R.COMPANY_WEEKLY_LAUNCH
    if not is_new and not R.STAKES_INCLUDE_CATALOGUE:
        return None                           # catalogue switched off: not a candidate at all
    if R.company_name_problem(name) is not None:
        ctx.reject("stake_invalid")           # a name the post cannot draw or say
        return None
    if R.longest_word(name) > R.STAKE_PAIR_NAME_WORD_MAX_CHARS:
        # RR3-STAKES-TEMPLATE-WALL: the name sits under its plate in half the column (the name itself is never
        # logged: an unlisted investee's may not be a company's)
        logger.info("company news company_stakes: stake %s refused as stake_invalid — its investee name holds a "
                    "word of %d characters (the template draws at most %d)", stake_id, R.longest_word(name),
                    R.STAKE_PAIR_NAME_WORD_MAX_CHARS)
        ctx.reject("stake_invalid")
        return None
    title = row.get("source_title")
    if not R.free_text_ok(title, lo=2, hi=R.STAKE_SOURCE_TITLE_MAX):
        ctx.reject("stake_invalid")
        return None
    if len(title) > R.STAKE_SOURCE_DRAW_MAX_CHARS or R.longest_word(title) > R.STAKE_TEXT_WORD_MAX_CHARS:
        # RR3-STAKES-TEMPLATE-WALL: the source title is drawn whole on the image, card 4 and every caption
        logger.info("company news company_stakes: stake %s refused as stake_invalid — its source title is %d "
                    "characters with a longest word of %d (the template draws at most %d, words of at most %d); "
                    "shorten it in the catalogue", stake_id, len(title), R.longest_word(title),
                    R.STAKE_SOURCE_DRAW_MAX_CHARS, R.STAKE_TEXT_WORD_MAX_CHARS)
        ctx.reject("stake_invalid")
        return None
    unknown = R.stake_text_unknown_words(title, known)
    if unknown:
        # the words themselves are never logged: one may be a person's name
        logger.info("company news company_stakes: stake %s refused as stake_source_unverified — its source title "
                    "carries %d word(s) on no list (neither a club or catalogue company's nor in "
                    "R.STAKE_SOURCE_WORDS); add a document word there if it is one", stake_id, len(unknown))
        ctx.reject("stake_source_unverified")
        return None
    listed_raw = row.get("listed_since")
    listed_since = _day(listed_raw) if listed_raw is not None else None
    if listed_raw is not None and (listed_since is None or listed_since > ctx.run_date):
        ctx.reject("stake_invalid")           # "listed since <a future date>" would be false
        return None
    investor_symbol = R.canonical_symbol(investors[slug]) if investors[slug] else None
    if investor_symbol is None:
        ctx.reject("investor_unlisted")
        return None
    # A percentage is carried only when the source states it with no qualifier of its own
    # ("voting", "economic"): the record has no field for the qualifier, and "owns 25%" without
    # it can be false.
    pct = _num(row.get("ownership_pct"))
    if pct is not None and (not 0 < pct <= 100 or row.get("ownership_basis") not in (None, "")):
        pct = None
    background = row.get("background")
    background = background if R.free_text_ok(background, hi=R.STAKE_BACKGROUND_MAX) else None
    local = row.get("local_listing")
    local = local if R.free_text_ok(local, hi=R.STAKE_LOCAL_LISTING_MAX) else None
    if local is not None and R.stake_text_unknown_words(local, known):
        logger.info("company news company_stakes: stake %s local listing dropped — a word on no list (ST-1-R)",
                    stake_id)
        local = None
    if local is not None and (len(local) > R.COMPANY_NAME_CHARS[1]
                              or R.longest_word(local) > R.STAKE_TEXT_WORD_MAX_CHARS):
        # RR3-STAKES-TEMPLATE-WALL: card 3 draws it whole, or uses its generic body
        logger.info("company news company_stakes: stake %s local listing dropped — %d characters with a longest "
                    "word of %d (the template draws at most %d, words of at most %d)", stake_id, len(local),
                    R.longest_word(local), R.COMPANY_NAME_CHARS[1], R.STAKE_TEXT_WORD_MAX_CHARS)
        local = None
    return _StakeCandidate(
        stake_id=stake_id, slug=slug, investor_symbol=investor_symbol,
        investee_symbol=R.canonical_symbol(row.get("investee_us_symbol")) if row.get("investee_us_symbol") else None,
        investee_name=name, kind=row.get("kind"), value_usd=value, value_basis=basis, ownership_pct=pct,
        as_of=as_of, verified_on=verified_on, source_title=title, background=background,
        listed_since=listed_since, local_listing=local, is_new=is_new,
    )


def _names_agree(listed_name: str, stake_name: str) -> bool:
    """Does the investee's listing (its profile's display name) name the stake's company? Every
    significant word of the listing's name must be in the stake's name ("Rivian Automotive" ⊆
    "Rivian Automotive"; "Intel" ⊆ "Intel"). Otherwise the symbol is not trusted to be this
    investee and the stake is shown by name only (no logo, no link)."""
    words = _words(_fold_text(listed_name, 64))
    significant = words - _GENERIC_NAME_WORDS or words
    return bool(significant) and significant <= _words(_fold_text(stake_name, 128))


async def _collect_company_stakes(ctx: _Ctx) -> Candidates:
    if not settings.TRILLION_CLUB_ENABLED:
        logger.info("company news company_stakes: TRILLION_CLUB_ENABLED is off — no stakes")
        return ctx.done((), "stakes_feature_off")
    group = await ctx.step("club_group", lambda: ctx.club.get_group(), reason="club_unavailable")
    club = await _club_rows(ctx, reason="club_unavailable")
    investors: Dict[str, Optional[str]] = {}
    for item in [*(getattr(group, "companies", None) or ()), *(getattr(group, "also_in_club", None) or ())]:
        slug = getattr(item, "slug", None)
        if isinstance(slug, str) and slug in club and slug not in investors:
            symbol = club[slug].get("detail_symbol")
            investors[slug] = symbol if isinstance(symbol, str) and symbol.strip() else None
    if not investors:
        return ctx.done((), "stakes_none_qualified")
    members = sorted(investors)
    rows = await _rows(
        ctx, "club_stakes", ("club_stakes", tuple(members)),
        lambda sb: sb.table(_STAKES_TABLE).select(_STAKE_COLUMNS)
        .in_("company_slug", members).eq("published", True).eq("source_confidence", "primary")
        .or_(_STAKE_POSTABLE_FILTER)
        .limit(STAKES_READ_LIMIT),
        reason="club_unavailable",
    )
    if len(rows) >= STAKES_READ_LIMIT:
        raise MarketingNewsUnavailable(ctx.series, "stakes_truncated",
                                       f"stage=club_stakes; {len(rows)} rows at the {STAKES_READ_LIMIT}-row cap")
    # The stake finder (migration 205): only rows a person reviewed may be posted — the read's filter, re-checked
    # strictly on every row it returned. After the cap check (a full page may be cut, whatever this drops) and BEFORE
    # the known words below: a left-out row's investee name must never vouch for another row's source title.
    rows = _postable_stake_rows(rows)
    # the companies a stake source may name (ST-1-R): the club's, by its own names, and every catalogue investee
    known = R.stake_known_words(
        [getattr(item, "name", None) for item in [*(getattr(group, "companies", None) or ()),
                                                  *(getattr(group, "also_in_club", None) or ())]]
        + [r.get("investee_name") for r in rows if isinstance(r, Mapping)])
    eligible = [c for c in (_stake_candidate(ctx, r, investors, known) for r in rows) if c is not None]
    # Newest `as_of` first (main-session decision 1), a new row before a catalogue one on the
    # same day, then the id — deterministic. The round-3 review of 2026-10-20 (RR3-STAKES-TEMPLATE-WALL):
    # AFTER every other candidate, each stake the template refused when it was last offered (reported back
    # by the script flow, `remember_template_refusal`), least recently refused first — so a refusal only the
    # template can make never keeps the head of the walk while publishable stakes wait behind it (five such
    # stakes, newest first and five records a call, used to end the series every day).
    refused_on = {c.stake_id: _stake_template_refused_on(c) for c in eligible}

    def order(c: _StakeCandidate) -> Tuple[bool, date, int, bool, str]:
        held = refused_on[c.stake_id]
        return (held is not None, held or date.min, -c.as_of.toordinal(), not c.is_new, c.stake_id)

    eligible.sort(key=order)
    held = sum(1 for c in eligible if refused_on[c.stake_id] is not None)
    if held:
        logger.info("company news company_stakes: %d of %d stake(s) were refused by their template when last "
                    "offered — tried after the others (RR3-STAKES-TEMPLATE-WALL)", held, len(eligible))
    ctx.considered = len(eligible)
    # news-v23: a post's key names every stake it held (`R.stake_ref_ids`): each one is posted once
    posted = _posted_stake_ids(ctx.exclude)
    fresh = []
    for c in eligible:
        if c.stake_id in posted:
            ctx.reject("already_posted")      # before any profile call
        else:
            fresh.append(c)
    # PUBLIC ONLY (news-v23, owner 2026-11-19: "filter for companies that public only. no private"): a stake whose kind
    # is never public (a private or non-U.S.-listed investee) or that carries no listed U.S. symbol is dropped before
    # any profile call — its investee never named, here or anywhere
    shaped: List[_StakeCandidate] = []
    for c in fresh:
        if c.kind not in R.PUBLIC_STAKE_KINDS or not c.investee_symbol:
            ctx.reject("stake_not_public")
            logger.info("company news company_stakes: stake %s dropped as stake_not_public — %s, %s (public "
                        "companies only)", c.stake_id, _scrub(c.kind, 40),
                        "a listed U.S. symbol" if c.investee_symbol else "no listed U.S. symbol")
        else:
            shaped.append(c)
    batch = shaped[:STAKES_PROFILE_CANDIDATES]
    if len(shaped) > len(batch):
        logger.info("company news company_stakes: %d of %d stake(s) with a listed symbol reach the profile stage "
                    "(STAKES_PROFILE_CANDIDATES) — the rest wait for a later call", len(batch), len(shaped))
    symbols: List[str] = []
    for c in batch:
        symbols.append(c.investor_symbol)
        symbols.append(c.investee_symbol)
    profiles = await _profiles(ctx, symbols) if symbols else {}
    # the public stakes of each investor, in the walk's order (an investor's first stake places it)
    groups: Dict[str, Tuple[R.CompanyRef, List[Any]]] = {}
    for c in batch:
        investor = R.company_from_profile(profiles.get(c.investor_symbol), c.investor_symbol, purpose="listing")
        if isinstance(investor, str):
            ctx.reject(investor)
            continue
        if R.longest_word(investor.name) > R.STAKE_PAIR_NAME_WORD_MAX_CHARS:
            # RR3-STAKES-TEMPLATE-WALL: the investor's name is drawn whole too (the title)
            logger.info("company news company_stakes: stake %s refused as company_name_unusable — its investor %s's "
                        "display name holds a word of %d characters (the template draws at most %d)", c.stake_id,
                        investor.symbol, R.longest_word(investor.name), R.STAKE_PAIR_NAME_WORD_MAX_CHARS)
            ctx.reject("company_name_unusable")
            continue
        investee = _public_investee(ctx, c, investor, profiles)
        if investee is None:
            continue
        try:
            row = R.CompanyStake(
                series="company_stakes", stake_id=c.stake_id, investor=investor, investee_name=c.investee_name,
                investee=investee, kind=c.kind, value_usd=c.value_usd, value_basis=c.value_basis,
                ownership_pct=c.ownership_pct, as_of=c.as_of, verified_on=c.verified_on,
                source_title=c.source_title, background=c.background, listed_since=c.listed_since,
                local_listing=c.local_listing, is_new=c.is_new,
            )
        except ValueError as e:
            logger.info("company news company_stakes: stake %s refused as record_invalid (%s)", c.stake_id, _describe(e))
            ctx.reject("record_invalid")
            continue
        groups.setdefault(investor.symbol, (investor, []))[1].append(row)
    ctx.check_profiles()
    records = _stakes_records(ctx, groups, refused_on)
    if records:
        return ctx.done(records, None)
    if eligible and not fresh:
        return ctx.done((), "stakes_none_unposted")
    if fresh:
        return ctx.done((), "stakes_none_public")
    if not R.STAKES_INCLUDE_CATALOGUE and not eligible:
        return ctx.done((), "stakes_none_new")
    return ctx.done((), "stakes_none_qualified")


def _posted_stake_ids(exclude: FrozenSet[str]) -> Set[str]:
    """Every stake id a posted company_stakes ledger key names (`R.stake_ref_ids`: a one-stake key of any post before
    news-v23, or a several-stake post's joined key). A malformed key is skipped whole. Pure; never raises."""
    out: Set[str] = set()
    for key in exclude:
        out.update(R.stake_ref_ids(key) or ())
    return out


def _public_investee(ctx: _Ctx, c: _StakeCandidate, investor: R.CompanyRef,
                     profiles: Mapping[str, Any]) -> Optional[R.CompanyRef]:
    """The investee's CompanyRef when it is a PUBLIC company (news-v23, owner 2026-11-19 "public only"), else None —
    the stake DROPPED, counted and logged (its id and symbol, never its name): `stake_not_trading` — the investee's
    profile does not say ``isActivelyTrading`` exactly True (False, absent or junk: fail closed, `_closed_out_checked`'s
    reading); `stake_listing_unverified` — no profile, a profile `R.company_from_profile` refuses (purpose "common": a
    major U.S. exchange, USD, no ETF / fund / ADR, a common listing, a drawable name), one that names another company
    (`_names_agree`), or the investor itself."""
    sym = c.investee_symbol
    profile = profiles.get(sym)
    trading = profile.get("isActivelyTrading") if isinstance(profile, Mapping) else None
    if isinstance(profile, Mapping) and trading is not True:
        ctx.reject("stake_not_trading")
        logger.warning("company news company_stakes: stake %s investee %s dropped as stake_not_trading — its profile "
                       "does not say it is actively trading (%r): public companies only", c.stake_id, sym,
                       trading if trading is None or isinstance(trading, bool) else "junk")
        return None
    ref = R.company_from_profile(profile, sym, purpose="common")
    if not isinstance(ref, R.CompanyRef) or ref.symbol == investor.symbol or not _names_agree(ref.name, c.investee_name):
        ctx.reject("stake_listing_unverified")
        logger.info("company news company_stakes: stake %s investee %s dropped as stake_listing_unverified (%s)",
                    c.stake_id, sym, ref if isinstance(ref, str) else
                    "the investor itself" if ref.symbol == investor.symbol else "its listing names another company")
        return None
    return ref


def _stakes_records(ctx: _Ctx, groups: Mapping[str, Tuple[R.CompanyRef, List[Any]]],
                    refused_on: Mapping[str, Optional[date]]) -> List[Any]:
    """news-v23 (owner 2026-11-19: "add 1-3 more companies for the NVIDIA (or the other trillion companies)"): ONE
    `R.CompanyStakes` per investor, the investor with the most not-yet-posted public stakes first — counting only the
    stakes no template refused when last offered (RR3-STAKES-TEMPLATE-WALL: a refused stake never wins its investor
    the head of the walk), then every stake, then the walk's own order (an investor's first stake) — at most
    ``ctx.limit`` of them (the next investor is the script flow's fallback when the template refuses the first). Each
    holds at most `R.STAKES_POST_STAKES` stakes — its unrefused ones first, then the refused ones least recently
    refused first — drawn in `R.stake_rank_key` order (the largest figure first).

    ONE row per investee ISSUER (review 2026-11-20): the club table is unique only on (investor, investee name, kind),
    so an investor may hold two postable rows of one listed company — an equity stake and a commitment in it, or two
    spellings of its name — which `R.CompanyStakes` refuses whole ("two rows of one company"), every run. Before the
    investors are ranked (a duplicate never wins its investor the head of the walk), each investor keeps the FIRST row
    of an issuer in that order — unrefused before refused, the largest figure first — by the keys the record checks
    (`R._distinct_companies`: symbol, `R.issuer_root`, the case-folded name); every later one is DROPPED from the post
    (``share_class_overlap``, logged with both stake ids and the symbol) and stays unposted for a later run."""
    ranked = []
    for first, (sym, (investor, rows)) in enumerate(groups.items()):
        fresh = sorted((r for r in rows if refused_on.get(r.stake_id) is None), key=R.stake_rank_key)
        held = sorted((r for r in rows if refused_on.get(r.stake_id) is not None),
                      key=lambda r: (refused_on[r.stake_id], R.stake_rank_key(r)))
        kept_rows: List[Any] = []
        seen: Dict[Tuple[str, Any], Any] = {}
        for r in fresh + held:
            keys = [("symbol", r.investee.symbol), ("issuer", R.issuer_root(r.investee.symbol)),
                    ("name", r.investee.name.casefold())]
            twin = next((seen[k] for k in keys if k in seen), None)
            if twin is not None:
                ctx.reject("share_class_overlap")
                logger.warning("company news company_stakes: %s's stake %s in %s DROPPED as share_class_overlap — the "
                               "post already holds its stake %s in the same company (%s); it waits for a later post",
                               investor.symbol, r.stake_id, r.investee.symbol, twin.stake_id, twin.investee.symbol)
                continue
            for k in keys:
                seen[k] = r
            kept_rows.append(r)
        fresh = [r for r in kept_rows if refused_on.get(r.stake_id) is None]
        ranked.append(((-len(fresh), -len(kept_rows), first), investor, kept_rows))
    ranked.sort(key=lambda t: t[0])
    records: List[Any] = []
    for _key, investor, rows in ranked:
        if len(records) >= ctx.limit:
            break
        chosen = sorted(rows[:R.STAKES_POST_STAKES[1]], key=R.stake_rank_key)
        if len(rows) > len(chosen):
            logger.info("company news company_stakes: %s holds %d public stakes — the post takes %d, the rest wait for "
                        "a later post", investor.symbol, len(rows), len(chosen))
        try:
            rec = R.CompanyStakes(series="company_stakes", investor=investor, stakes=tuple(chosen))
        except ValueError as e:
            logger.error("company news company_stakes: %s's %d public stakes do not make a record (%s) — not posted",
                         investor.symbol, len(chosen), _describe(e))
            ctx.reject("record_invalid")
            continue
        logger.info("company news company_stakes: ONE post of %s's %d public stake(s) (%s), the largest figure first",
                    investor.symbol, len(chosen), ",".join(r.investee.symbol for r in chosen))
        records.append(rec)
    return records


# ── earnings (Drop 2b; Batch 1, 2026-10-15) ───────────────────────────────────
#
# One company per record: its reported EPS against the analyst estimate, with its revenue and the revenue
# estimate, from the previous seven ET days' calendar, ONE day per call (a multi-day answer is
# cut at 4,000 rows, newest kept). The revenue pair is a data-quality and quarter check only: since news-v21
# (main-session decision of 2026-10-20) no post prints it — the feed never says which basis its revenue ESTIMATE
# is on (GE Aerospace's adjusted-revenue consensus beside its GAAP actual), so an issuer like GE publishes its EPS
# alone (`news_templates.EARNINGS_REVENUE_IN_POSTS`). Batch 1 (2026-10-15), each refusing the WHOLE report:
#
# * one plausible revenue pair or nothing (`R.earnings_revenue_pair` → `revenue_implausible`): the
#   pair is the only sign the row is the quarter the estimate is for — Progressive's 10-14 row held
#   ONE MONTH ($7.16B against a $22.6B quarter estimate) and the old code kept its EPS ($1.08 vs $4.35);
# * a clear surprise (`R.clear_surprise` → `surprise_unclear`): a gap under 5% is not news — as the feed
#   wrote the figures AND as the post prints them (review of 2026-10-16: 0.095 vs 0.10 prints "$0.10 vs $0.10");
# * a verified quarter (`R.earnings_period` over ONE quarterly-statement read, which also settles the
#   reporting currency → `period_mismatch` / `period_unverified` / `non_usd_reporter`): the post then
#   says "the quarter that ended <date>" (Delta: the statement's $20.186B against the calendar's $20.2B
#   → September 30) — a row that is a QUARTER (review of 2026-10-16: its span from the row before, never a
#   half-year). The EPS is never checked against GAAP (the analysts' basis differs): the one EPS gate on the
#   statement is the narrow mixed-basis one (`R.earnings_basis_mixed` → `eps_basis_mixed`, review of
#   2026-10-16: an actual off the statement's scale beside an estimate on it — Prologis), and a Real Estate /
#   REIT issuer is refused before its read (`R.earnings_sector_problem` → `eps_basis_reit`). A read that FAILS
#   is `statement_unavailable`; every read failing raises `earnings_statements_unavailable`.
#
# One record per ISSUER (review of 2026-10-16, ER-3 / live preview F3; the other series already dedupe this
# way): a 5-letter twin of a 4-letter root listed in the window is dropped before any profile
# (`EARNINGS_CLASS_LETTERS`; never an OTC foreign F / Y line, `EARNINGS_FOREIGN_LETTERS`; Alphabet's GOOGL stands for
# the issuer when GOOG has no reported row, `EARNINGS_CLASS_LINES`), a NASDAQ preferred line at the profile gate
# (`R.nasdaq_preferred_symbol`), and the listings left of one issuer (profile CIK, else display name) keep the ROOT;
# listings whose rows do not vouch for one report refuse the issuer (`_family_problem`, the re-review of
# 2026-10-19: two actuals, or a listing's consensus that leaves the surprise unclear or turns it — never consensus
# figures that merely differ per ticker); a listing whose issuer has a posted earnings key in this window (or the
# `EARNINGS_POSTED_LOOKBACK_DAYS` before it) is `already_posted` (the frozen ledger key stays per symbol:
# news:earnings:<SYM>:<day>).
#
# Ranked by SIZE, never by the widest gap (that led with one-offs: Nurix's $4.73 vs -$0.60 at a $2.55B
# cap): pre-ordered by the revenue estimate (a size proxy before any profile is read), the first
# `EARNINGS_MAX_PROFILES` profiled at once and gated, then ordered by (market cap desc, gap desc, symbol)
# — the cap is read in the gate only and never leaves — and only then read, in that order, until
# ``limit`` reports are held.


async def _calendar_day(fmp: Any, *, from_date: str, to_date: str) -> List[Dict[str, Any]]:
    """One ET day of the earnings calendar. A day at FMP's silent row cap is probably CUT:
    ``earnings_calendar_truncated`` (raised as the series' own exception — a new exception class
    here would need its own `classify_exception` branch)."""
    rows = await fmp.get_earnings_calendar(from_date=from_date, to_date=to_date)
    if not isinstance(rows, list):
        raise FMPUnavailableException(f"earnings calendar {from_date} returned {type(rows).__name__}, not a list")
    if len(rows) >= EARNINGS_TRUNCATION_ROWS:
        raise MarketingNewsUnavailable("earnings", "earnings_calendar_truncated",
                                       f"stage=earnings_calendar; {from_date}: {len(rows)} rows "
                                       f"(cap {EARNINGS_TRUNCATION_ROWS})")
    return rows


async def _earnings_days(ctx: _Ctx, days: Sequence[date]) -> Dict[date, List[Dict[str, Any]]]:
    """{day: that day's calendar rows}: memoized per day (30 min); the missing days are fetched
    together, all-or-nothing (`fetch_calendar_days`), and only then memoized."""
    out: Dict[date, List[Dict[str, Any]]] = {}
    missing: List[date] = []
    for d in days:
        hit = _memo_get(("earnings_day", d.isoformat()))
        if hit is _MISS:
            missing.append(d)
        else:
            out[d] = hit
    if missing:
        async def fetch() -> Dict[date, List[Dict[str, Any]]]:
            got = await fetch_calendar_days(lambda **kw: _calendar_day(ctx.fmp, **kw), list(missing))
            for d, rows in got.items():
                _memo_put(("earnings_day", d.isoformat()), rows, EARNINGS_DAY_TTL_SECONDS)
            return got

        key = ("earnings_days", tuple(d.isoformat() for d in missing))
        got = await ctx.step("earnings_calendar", lambda: _shared(key, 0, fetch),
                             reason="earnings_calendar_unavailable")
        for d in missing:
            if d not in got:
                raise MarketingNewsUnavailable(ctx.series, "earnings_calendar_unavailable",
                                               f"stage=earnings_calendar; {d} missing from the answer")
            out[d] = got[d]
    return out


def _first_num(row: Mapping[str, Any], *keys: str) -> Optional[float]:
    for k in keys:
        v = _num(row.get(k))
        if v is not None:
            return v
    return None


def _row_figures(row: Mapping[str, Any]) -> Optional[Tuple[float, float, Optional[float], Optional[float]]]:
    """A REPORTED calendar row's figures as the feed wrote them — (EPS actual, EPS estimate, revenue actual,
    revenue estimate), each a finite number or None — or None when the EPS pair is not there (not reported
    yet: no candidate). THE one reading of a row's figures: `_earnings_row` and the issuer checks compare
    exactly what the record would carry."""
    actual = _first_num(row, "epsActual", "eps")
    estimate = _first_num(row, "epsEstimated", "epsEstimate")
    if actual is None or estimate is None:
        return None
    return (actual, estimate, _first_num(row, "revenueActual", "revenue"),
            _first_num(row, "revenueEstimated", "revenueEstimate"))


def _window_symbol(row: Mapping[str, Any]) -> Optional[str]:
    """A calendar row's canonical symbol — reported or not — or None for a foreign line (a "." in it), no
    symbol, or one the grammar refuses: the listings of the window the twin rule looks at."""
    raw = row.get("symbol")
    if not isinstance(raw, str) or "." in raw:
        return None
    return R.canonical_symbol(raw)


def _earnings_row(row: Mapping[str, Any]) -> Tuple[Optional[str], Any]:
    """One calendar row → (symbol, its report or a rejection reason), or (None, None) when it is
    no candidate at all (not reported yet), or (None, reason) for an unusable symbol."""
    figures = _row_figures(row)
    if figures is None:
        return None, None                     # not reported (yet): not a candidate
    actual, estimate, ra, re_ = figures
    raw = row.get("symbol")
    if not isinstance(raw, str) or "." in raw:
        return None, "symbol_grammar"         # a foreign line (ZOO.L, 005930.KS) or no symbol
    sym = R.canonical_symbol(raw)
    if sym is None:
        return None, R.symbol_problem(raw) or "symbol_grammar"
    if abs(estimate) < R.EPS_MIN_ABS_ESTIMATE:
        return sym, "eps_estimate_too_small"
    if abs(actual) > R.EPS_MAX_ABS or abs(estimate) > R.EPS_MAX_ABS:
        return sym, "eps_implausible"         # owner-delegated ceiling (2026-10-19): a feed error, never a result
    if eps_digit_shift_suspect(actual, estimate, None):
        return sym, "eps_digit_shift"         # no filed GAAP EPS to break the tie: refused
    if abs(actual - estimate) > EARNINGS_MAX_GAP_MULTIPLE * abs(estimate):
        return sym, "eps_gap_implausible"
    return sym, {"actual": actual, "estimate": estimate, "ra": ra, "re": re_}


def _posted_in_window(exclude: FrozenSet[str], days: Sequence[date]) -> Set[str]:
    """The symbols with a posted earnings key (``news:earnings:<SYM>:<day>``) for ANY day of this window — or of
    the `EARNINGS_POSTED_LOOKBACK_DAYS` before it. An issuer reports once a quarter, so a posted key of a listing
    for any day of the week is this week's report — FMP re-dating the row a day later must not post it twice
    (review of 2026-10-16) — and (the re-review of 2026-10-19, EME-1) neither must a re-dating ACROSS two windows:
    a report posted under the last day of one window and re-dated onto the first day of the next was offered again,
    with no posted key inside the new window. A key dated after the window is not counted (a later run's window).
    news-v23 (the restyle, owner 2026-11-19): ONE earnings post holds 2-4 companies, and its key names every one of
    them (``news:earnings:<SYM>:<day>+<SYM>:<day>…``, `R.ledger_key`) — EACH company it drew counts here as posted,
    read by `R.earnings_ref_items` (a one-company key, every post before news-v23, reads the same). Never raises on a
    malformed key (it is skipped whole)."""
    if not days:
        return set()
    first, last = min(days), max(days)
    oldest = first - timedelta(days=EARNINGS_POSTED_LOOKBACK_DAYS)
    out: Set[str] = set()
    for key in exclude:
        for sym, day in R.earnings_ref_items(key) or ():
            if oldest <= day <= last:
                out.add(sym)
    return out


def _twin_families(listed: Set[str]) -> Dict[str, Set[str]]:
    """{4-letter ROOT listed in the window: its twin lines} — each a listed 5-letter symbol of letters only that
    starts with the root and ends in neither a share-class letter (`EARNINGS_CLASS_LETTERS`) nor an OTC foreign
    letter (`EARNINGS_FOREIGN_LETTERS`: often another company — CMPRF beside CMPR). A curated class line
    (`EARNINGS_CLASS_LINES`, GOOGL) is a family member, and makes its root a family's root even when the root has
    no row in the window (GOOGM and GOOGN still vouch for GOOGL, `_drop_twin_lines`). Roots without a twin are
    left out."""
    roots = {s for s in listed if len(s) == 4} | {s[:4] for s in listed if s in EARNINGS_CLASS_LINES}
    out: Dict[str, Set[str]] = {}
    for sym in listed:
        if (len(sym) == 5 and sym.isalpha() and sym[-1] not in EARNINGS_CLASS_LETTERS
                and sym[-1] not in EARNINGS_FOREIGN_LETTERS and sym[:4] in roots):
            out.setdefault(sym[:4], set()).add(sym)
    return out


def _surprise_sign(actual: float, estimate: float) -> int:
    return (actual > estimate) - (actual < estimate)


def _family_problem(head: Mapping[str, Any], figure_sets: Iterable[Tuple[Any, ...]]) -> Optional[str]:
    """Do the reported calendar rows of every listing of one issuer (``figure_sets``: `_row_figures` tuples, the
    head's own row among them) vouch for the head's report? None, or the reason the head is refused — the
    re-review of 2026-10-19 (B1R-ER3; ER-3 refused on ANY differing figure, and FMP keys its consensus per TICKER,
    so GOOG's and GOOGL's estimates always differ — 2.63 against 2.57, $111.3184B against $111.3324B — and
    Alphabet, the largest reporter of its week, was refused every quarter):

    * the rows' ACTUALS — (EPS actual, revenue actual) — must be ONE pair: two reported results for one company
      make the result a guess (``record_invalid``: FITB 0.83 beside FITBI 0.84);
    * every row's EPS ESTIMATE must give a clear surprise (`R.clear_surprise`) in the head's own direction: the
      post prints the head's estimate, and a beat by one listing's consensus that is no clear beat (or a miss)
      by another's is no clear surprise (``surprise_unclear``). The revenue estimates may differ: the post
      prints no revenue at all (news-v21; before it, only "revenue of X vs Y" from the head's own row).

    Pure; exact on the figures as written (no tolerance: FMP copies an issuer's actuals onto each listing)."""
    sets = [f for f in figure_sets if isinstance(f, tuple) and len(f) == 4]
    if len({(f[0], f[2]) for f in sets}) > 1:
        return "record_invalid"
    actual, sign = head["actual"], _surprise_sign(head["actual"], head["estimate"])
    for f in sets:
        if not R.clear_surprise(actual, f[1]) or _surprise_sign(actual, f[1]) != sign:
            return "surprise_unclear"
    return None


def _drop_twin_lines(ctx: _Ctx, best: Dict[str, Dict[str, Any]], listed: Set[str],
                     figures: Mapping[str, Set[Tuple[Any, ...]]], posted: Set[str]) -> None:
    """Review of 2026-10-16 (ER-3 / live preview F3), before any profile: a TWIN line (`_twin_families`) is
    never a candidate while its root reported — FMP profiles WAFDP, HBANL or FITBI under the issuer's plain name,
    with a market cap that can outrank the common's, and their calendar rows can carry another EPS (WAFDP 0.76
    against WAFD's 0.85) — so each one still in ``best`` is dropped: `share_class_overlap`, or `already_posted`
    when a listing of its family has a posted key (`_posted_in_window`). The HEAD — the root, or (EME-3, the
    re-review of 2026-10-19) a curated class line (`EARNINGS_CLASS_LINES`: GOOGL) when the root has NO reported row
    in the window — stays one report of its issuer, unless the family's reported rows do not vouch for it
    (`_family_problem`: two actuals → ``record_invalid``, FITB 0.83 beside FITBI 0.84; an estimate that leaves the
    surprise unclear or turns it → ``surprise_unclear``) or the family was posted (``already_posted``). This runs
    whether or not the root is a candidate: with WAFD refused (`surprise_unclear`), WAFDP's 9.8% "miss" would
    otherwise pass alone — and a refused root never hands its report to a class line (that line's consensus would
    decide what the root's did not). Mutates ``best``; every candidate it drops is counted once."""
    for root, twins in sorted(_twin_families(listed).items()):
        family = {root} | twins
        was_posted = bool(family & posted)
        stand_in = None
        if root not in figures:
            stand_in = next((t for t in sorted(twins) if t in EARNINGS_CLASS_LINES and t in best), None)
        dropped = sorted(t for t in twins if t in best and t != stand_in)
        for twin in dropped:
            del best[twin]
            ctx.reject("already_posted" if was_posted else "share_class_overlap")
        shown = sorted({f for s in family for f in figures.get(s, set())}, key=repr)
        head = root if root in best else stand_in
        head_reason = None
        if head is not None:
            head_reason = "already_posted" if was_posted else _family_problem(best[head], shown)
            if head_reason:
                del best[head]
                ctx.reject(head_reason)
        if dropped or head_reason or stand_in:
            logger.info("company news earnings: twin line(s) %s of root %s dropped as %s; the head %s: %s "
                        "(the family's reported rows hold %d figure set(s))", dropped, root,
                        "already_posted" if was_posted else "share_class_overlap", head,
                        head_reason or ("kept" if head is not None else "no candidate"), len(shown))


def _issuer_keys(profile: Any, name: Optional[str]) -> Set[Tuple[str, str]]:
    """A listing's issuer identity: its profile CIK and its casefolded display name (either one ties two
    listings to one issuer, as `_one_row_per_issuer` and `R.theme_company_count` do)."""
    keys: Set[Tuple[str, str]] = set()
    cik = R.cik10(profile.get("cik")) if isinstance(profile, Mapping) else None
    if cik is not None:
        keys.add(("cik", cik))
    if isinstance(name, str) and name:
        keys.add(("name", name.casefold()))
    return keys


def _posted_issuer_keys(posted: Set[str], profiles: Mapping[str, Dict[str, Any]]) -> Set[Tuple[str, str]]:
    """The issuer identities of the listings posted in this window (their own profiles). A posted listing
    without a profile cannot be matched to its other listings: logged at WARNING (a second post of one report
    is the risk — never a wrong figure)."""
    keys: Set[Tuple[str, str]] = set()
    for sym in sorted(posted):
        profile = profiles.get(sym)
        found = _issuer_keys(profile, R.display_company_name(profile.get("companyName"))
                             if isinstance(profile, Mapping) else None)
        if not found:
            logger.warning("company news earnings: posted listing %s has no usable profile — another listing of "
                           "its issuer cannot be matched to it this run", sym)
        keys |= found
    return keys


def _one_listing_per_issuer(ctx: _Ctx, gated: Sequence[Tuple[Tuple[float, float, str], Any, Dict[str, Any],
                                                               Set[Tuple[str, str]]]],
                            figures: Mapping[str, Set[Tuple[Any, ...]]]
                            ) -> List[Tuple[Tuple[float, float, str], Any, Dict[str, Any]]]:
    """Review of 2026-10-16 (ER-3 / live preview F3): one record per ISSUER — the listings left after the gate
    that share a profile CIK or a display name (BRK-A / BRK-B, LBRDA / LBRDK, two "Alphabet" lines) are one
    company, and two records would post one report twice. Each group keeps its ROOT listing — the shortest
    symbol, then the first in order (never the largest FMP market cap: FMP prices a $25 preferred on the
    common's share count, so HBANL's $34.1B outranks HBAN's $31.5B) — with its own rank key and its own row's
    figures; the others are counted `share_class_overlap`. A group whose listings' rows do not vouch for the
    root's report is refused whole (`_family_problem`, the re-review of 2026-10-19: two different ACTUALS →
    ``record_invalid``, BRK-A's per-class EPS beside BRK-B's included — an accepted over-block; an estimate that
    leaves the surprise unclear or turns it → ``surprise_unclear``; listings whose consensus figures merely
    differ are one report), the rest `share_class_overlap`."""
    n = len(gated)
    parent = list(range(n))

    def root(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    owner: Dict[Tuple[str, str], int] = {}
    for i, (_key, _company, _b, keys) in enumerate(gated):
        for k in sorted(keys):
            a, b2 = root(i), root(owner.setdefault(k, i))
            if a != b2:
                parent[a] = b2
    groups: Dict[int, List[int]] = {}
    for i in range(n):
        groups.setdefault(root(i), []).append(i)
    out: List[Tuple[Tuple[float, float, str], Any, Dict[str, Any]]] = []
    for members in groups.values():
        key, company, b, _keys = gated[members[0]]
        if len(members) == 1:
            out.append((key, company, b))
            continue
        syms = sorted((gated[i][0][2] for i in members), key=lambda s: (len(s), s))
        keep = next(i for i in members if gated[i][0][2] == syms[0])
        shown = sorted({f for s in syms for f in figures.get(s, set())}, key=repr)
        ctx.reject("share_class_overlap", len(members) - 1)
        key, company, b, _keys = gated[keep]
        problem = _family_problem(b, shown)
        if problem is not None:
            logger.info("company news earnings: listings %s of one issuer do not vouch for one report (%d figure "
                        "set(s)) — the issuer is refused (%s %s, the rest share_class_overlap)", syms, len(shown),
                        syms[0], problem)
            ctx.reject(problem)
            continue
        logger.info("company news earnings: listings %s are one issuer — %s kept, the rest share_class_overlap",
                    syms, syms[0])
        out.append((key, company, b))
    return out


async def _collect_earnings(ctx: _Ctx) -> Candidates:
    days = [ctx.run_date - timedelta(days=i) for i in range(R.earnings_window_days(ctx.run_date), 0, -1)]
    by_day = await _earnings_days(ctx, days)
    reports: Dict[str, List[Tuple[date, Any]]] = {}
    listed: Set[str] = set()                                # every listing with a row in the window
    figures: Dict[str, Set[Tuple[Any, ...]]] = {}           # every REPORTED row's figures, per listing
    for d in days:
        for row in by_day.get(d) or ():
            if not isinstance(row, dict):
                continue
            listing = _window_symbol(row)
            if listing is not None:
                listed.add(listing)
                shown = _row_figures(row)
                if shown is not None:
                    figures.setdefault(listing, set()).add(shown)
            sym, got = _earnings_row(row)
            if got is None:
                continue
            if sym is None:
                ctx.reject(got)
                continue
            reports.setdefault(sym, []).append((d, got))
    best: Dict[str, Dict[str, Any]] = {}
    for sym, found in reports.items():
        refused = [got for _d, got in found if isinstance(got, str)]
        if refused:
            ctx.reject(refused[-1])           # any refused row refuses the company (its newest reason)
            continue
        # One report per company. Two reported rows in one week must agree on EPS — otherwise which one
        # is the result is a guess (`record_invalid`) — and on ONE plausible revenue pair (Batch 1: a
        # missing, disagreeing, non-positive or out-of-band pair refuses the WHOLE report, never the EPS
        # kept without its revenue).
        if len({(g["actual"], g["estimate"]) for _d, g in found}) > 1:
            ctx.reject("record_invalid")
            continue
        revenue = R.earnings_revenue_pair([(g["ra"], g["re"]) for _d, g in found])
        if revenue is None:
            ctx.reject("revenue_implausible")
            continue
        day, first = max(found, key=lambda t: t[0])
        if not R.clear_surprise(first["actual"], first["estimate"]):
            ctx.reject("surprise_unclear")
            continue
        best[sym] = {"day": day, "ratio": abs(first["actual"] - first["estimate"]) / abs(first["estimate"]),
                     "actual": first["actual"], "estimate": first["estimate"], "ra": revenue[0], "re": revenue[1]}
    ctx.considered = len(best)
    # Before any profile call: a report posted in this window (its key for ANY day of the week — review of
    # 2026-10-16, `_posted_in_window`), then the twin lines of a listed root (`_drop_twin_lines`).
    posted = _posted_in_window(ctx.exclude, days)
    for sym in sorted(best):
        if sym in posted:
            del best[sym]
            ctx.reject("already_posted")
    _drop_twin_lines(ctx, best, listed, figures, posted)
    # The size proxy before any profile is read: the revenue estimate (then the gap, then the symbol).
    pre = sorted(best.items(), key=lambda kv: (-kv[1]["re"], -kv[1]["ratio"], kv[0]))
    if len(pre) > EARNINGS_MAX_PROFILES:
        logger.info("company news earnings: %d of %d report(s) — the smallest revenue estimates — are past "
                    "the profile cap of %d and are never profiled", len(pre) - EARNINGS_MAX_PROFILES, len(pre),
                    EARNINGS_MAX_PROFILES)
    head = pre[:EARNINGS_MAX_PROFILES]
    if not head:
        return ctx.done((), "earnings_none_qualified")
    # The listings posted in this window are profiled beside the head (review of 2026-10-16): their issuer
    # identity is what ties a sibling listing (BRK-B after a posted BRK-A) to a report already posted.
    profiles = await _profiles(ctx, [s for s, _b in head] + sorted(posted))
    ctx.check_profiles()
    posted_keys = _posted_issuer_keys(posted, profiles)
    gated: List[Tuple[Tuple[float, float, str], Any, Dict[str, Any], Set[Tuple[str, str]]]] = []
    for sym, b in head:
        profile = profiles.get(sym)
        company = R.company_from_profile(profile, sym, purpose="earnings")
        if isinstance(company, str):
            ctx.reject(company)
            continue
        keys = _issuer_keys(profile, company.name)
        if keys & posted_keys:
            logger.info("company news earnings: %s refused as already_posted — a listing of its issuer was posted "
                        "in this window (one report, one post)", sym)
            ctx.reject("already_posted")
            continue
        sector_problem = R.earnings_sector_problem(profile)
        if sector_problem is not None:
            logger.info("company news earnings: %s refused as %s — sector %r, industry %r: a REIT's calendar EPS "
                        "can be FFO-based, and an unknown sector cannot be shown not to be one", sym, sector_problem,
                        _scrub(profile.get("sector"), 40), _scrub(profile.get("industry"), 40))
            ctx.reject(sector_problem)
            continue
        cap = _num(profile.get("marketCap")) or 0.0                     # the rank only; never leaves
        gated.append(((-cap, -b["ratio"], sym), company, b, keys))
    ranked = _one_listing_per_issuer(ctx, gated, figures)
    # The series' rank order, across everything profiled: the largest company first.
    ranked.sort(key=lambda t: t[0])
    held = await _earnings_reads(ctx, ranked, hold=R.EARNINGS_DAY_COMPANIES[1])
    return _earnings_day(ctx, [rec for _key, rec in held])


def _earnings_day(ctx: _Ctx, reports: Sequence[Any]) -> Candidates:
    """news-v23 (the restyle, owner 2026-11-19: "combine 2 to 4 companies at once"): the ONE `R.EarningsDay` record of
    the call — the held reports (each through every per-company gate), the largest company first, up to
    `R.EARNINGS_DAY_COMPANIES`' most; a report whose issuer is already in the post (`R.issuer_root`, the display name —
    `_one_listing_per_issuer` should have caught it) is left out as `share_class_overlap`. Fewer than the post's
    minimum after that: no record — ``earnings_too_few_companies`` (exactly one qualified) or
    ``earnings_none_qualified`` (none), the chain falls through."""
    lo, hi = R.EARNINGS_DAY_COMPANIES
    picked: List[Any] = []
    for rec in reports:
        if len(picked) >= hi:
            break
        if any(R.issuer_root(rec.company.symbol) == R.issuer_root(p.company.symbol)
               or rec.company.name.casefold() == p.company.name.casefold() for p in picked):
            logger.info("company news earnings: %s left out as share_class_overlap — its issuer is already in the post",
                        rec.company.symbol)
            ctx.reject("share_class_overlap")
            continue
        picked.append(rec)
    if len(picked) < lo:
        if picked:
            logger.info("company news earnings: only %s qualified after every gate — an earnings post shows at least "
                        "%d companies, so none is posted (earnings_too_few_companies)", picked[0].company.symbol, lo)
        return ctx.done((), "earnings_too_few_companies" if picked else "earnings_none_qualified")
    try:
        day = R.EarningsDay(series="earnings", reports=tuple(picked))
    except ValueError as e:
        logger.error("company news earnings: the day's %d reports (%s) do not make a record (%s) — none is posted",
                     len(picked), ",".join(r.company.symbol for r in picked), _describe(e))
        ctx.reject("record_invalid")
        return ctx.done((), "earnings_none_qualified")
    logger.info("company news earnings: ONE post of %d companies (%s), the largest first", len(picked),
                ",".join(r.company.symbol for r in picked))
    return ctx.done((day,), "earnings_none_qualified")


async def _earnings_reads(ctx: _Ctx, gated: Sequence[Tuple[Tuple[float, float, str], Any, Dict[str, Any]]],
                          *, hold: Optional[int] = None) -> List[Tuple[Tuple[float, float, str], Any]]:
    """The read stage, in the gated rank order (market cap, gap, symbol): one quarterly-statement read per
    report, in waves of the reports still needed — until `hold` reports are held (news-v23: the post's most companies,
    `R.EARNINGS_DAY_COMPANIES`; None: the call's `limit`) — at most `EARNINGS_MAX_STATEMENT_READS` in all. A report
    is HELD only when its statements report in USD and `R.earnings_period` places it (the record then
    carries the quarter's end); every refusal is counted ONCE, a held report never. Out of budget on a later
    wave ends the walk with the reports already held (each fully checked); with none held — or on any other
    unavailability — the series raises. Review of 2026-10-16 (ER-2): a read that FAILED refuses its report
    (`statement_unavailable`), and when EVERY read of the call failed (nothing held, so) the source is down —
    `earnings_statements_unavailable`, as `_Ctx.check_profiles` for the profiles — never an honest "nothing
    qualified" that would hide an FMP outage behind a list of refused reports."""
    held: List[Tuple[Tuple[float, float, str], Any]] = []
    reads = failed = 0
    last_failure = ""
    queue = list(gated)
    target = ctx.limit if hold is None else hold
    while queue and len(held) < target:
        if reads >= EARNINGS_MAX_STATEMENT_READS:
            logger.info("company news earnings: statement-read cap %d reached with %d record(s) kept and %d "
                        "report(s) unread", EARNINGS_MAX_STATEMENT_READS, len(held), len(queue))
            break
        want = min(target - len(held), EARNINGS_MAX_STATEMENT_READS - reads)
        wave: List[Tuple[Tuple[float, float, str], Dict[str, Any], Any]] = []
        while queue and len(wave) < want:
            key, company, b = queue.pop(0)
            try:
                # Validated BEFORE a read is spent on it; the period is set once the statement places it.
                rec = R.EarningsReport(
                    series="earnings", company=company, report_date=b["day"], period_end=None,
                    eps_actual=b["actual"], eps_estimate=b["estimate"],
                    revenue_actual=b["ra"], revenue_estimate=b["re"],
                )
            except ValueError as e:
                logger.info("company news earnings: %s refused as record_invalid (%s)", key[2], _describe(e))
                ctx.reject("record_invalid")
                continue
            wave.append((key, b, rec))
        if not wave:
            break
        reads += len(wave)
        statements = await asyncio.gather(*(_quarter_statements(ctx, key[2]) for key, _b, _rec in wave),
                                          return_exceptions=True)
        stop = next((s for s in statements if isinstance(s, (MarketingNewsUnavailable, asyncio.CancelledError))),
                    None)
        if stop is not None:
            # A record already held is fully checked: out of budget on a LATER wave ends the walk with it
            # (never discarded); with none held — or any other failure — the series raises.
            if not (isinstance(stop, MarketingNewsUnavailable) and stop.reason == "budget_exhausted" and held):
                raise stop
            logger.warning("company news earnings: out of budget after %d record(s) — the walk ends with them (%s)",
                           len(held), _describe(stop))
            break
        for (key, b, rec), got in zip(wave, statements):
            if isinstance(got, BaseException):
                failed += 1
                last_failure = f"{key[2]}: {_describe(got)}"
            kept = _earnings_checked(ctx, key[2], b, rec, got)
            if kept is not None:
                held.append((key, kept))
    if reads and failed == reads and not held:
        raise MarketingNewsUnavailable(ctx.series, "earnings_statements_unavailable",
                                       f"stage=earnings_statement; all {reads} statement read(s) failed; "
                                       f"last {last_failure}")
    return held


def _earnings_checked(ctx: _Ctx, sym: str, b: Mapping[str, Any], rec: Any, statements: Any) -> Any:
    """One report against its quarterly statements: the record (its period set) or None, the refusal
    counted and logged with its identifiers."""
    if isinstance(statements, BaseException):
        # Review of 2026-10-16 (ER-2): its own reason — counted as `non_usd_reporter` before, an FMP outage at
        # this stage read as "no USD reporter qualified"; `_earnings_reads` raises when every read failed.
        logger.warning("company news earnings: %s quarterly statement unreadable — refused as statement_unavailable "
                       "(%s)", sym, _describe(statements))
        ctx.reject("statement_unavailable")
        return None
    cur = R.statement_currency(statements)
    if cur != "USD":
        logger.info("company news earnings: %s reports in %s, not USD — refused (its EPS and revenue are not "
                    "dollars)", sym, _scrub(cur, 12))
        ctx.reject("non_usd_reporter")
        return None
    period_end, why = R.earnings_period(statements, report_date=b["day"], revenue_actual=b["ra"])
    if why is not None:
        logger.info("company news earnings: %s refused as %s — reported %s, calendar revenue %.6g; the "
                    "quarterly statements do not place it", sym, why, b["day"], b["ra"])
        ctx.reject(why)
        return None
    if period_end is None:
        # `R.EARNINGS_ALLOW_SCALE_CHECK` only: kept without a period ("reported on <date>").
        logger.info("company news earnings: %s kept WITHOUT a period (the statements lag; the scale check "
                    "passed)", sym)
        return rec
    # Review of 2026-10-16 (live preview F1): the actual and the estimate on two bases — the estimate on the
    # scale of the placed quarter's statement diluted EPS, the actual off it (Prologis' $1.60 Core-FFO-scale
    # actual, GAAP $0.86, estimate $0.777) — is refused: "reported $1.60 a share on the analysts' basis; the
    # estimate was $0.78" would be false. Narrow on purpose: never a GAAP check of the actual alone.
    if R.earnings_basis_mixed(statements, period_end=period_end, actual=b["actual"], estimate=b["estimate"]):
        logger.info("company news earnings: %s refused as eps_basis_mixed — actual %.6g, estimate %.6g: the "
                    "estimate is on the scale of the %s quarter's statement EPS, the actual is not", sym,
                    b["actual"], b["estimate"], period_end)
        ctx.reject("eps_basis_mixed")
        return None
    try:
        return replace(rec, period_end=period_end)        # re-validated: the period ends before the report
    except ValueError as e:
        logger.info("company news earnings: %s refused as record_invalid with period %s (%s)", sym, period_end,
                    _describe(e))
        ctx.reject("record_invalid")
        return None


async def _fetch_quarter_statements(fmp: Any, sym: str) -> List[Dict[str, Any]]:
    """``sym``'s latest `EARNINGS_STATEMENT_ROWS` QUARTERLY income statements — ONE call — as dict rows.
    No dict row, or no readable reporting currency on the newest period (`R.statement_currency`), raises
    (the caller refuses the report; an exception is never memoized). The one answer settles both checks:
    the reporting currency and the quarter the figures are for (`R.earnings_period`)."""
    rows = await fmp.get_income_statement(sym, "quarter", limit=EARNINGS_STATEMENT_ROWS)
    dated = [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []
    if not dated:
        raise FMPUnavailableException(f"quarterly income statement of {sym}: {type(rows).__name__} with no row")
    if R.statement_currency(dated) is None:
        raise FMPUnavailableException(f"quarterly income statement of {sym}: no readable reportedCurrency "
                                      f"on its newest period")
    return dated


async def _quarter_statements(ctx: _Ctx, sym: str) -> List[Dict[str, Any]]:
    """`_fetch_quarter_statements`, memoized like a calendar day (``("quarter_statements", sym)``, 30 min;
    an exception never is). Budget exhaustion raises `MarketingNewsUnavailable`; any other failure passes
    through raw (the caller refuses THAT report — fail closed — and the week keeps the rest)."""
    return await ctx.step(
        "earnings_statement",
        lambda: _shared(("quarter_statements", sym), EARNINGS_DAY_TTL_SECONDS,
                        lambda: _fetch_quarter_statements(ctx.fmp, sym)),
        reason="earnings_calendar_unavailable", passthrough=(Exception,),
    )


# ── theme_explainer (Drop 2b, a fallback) ─────────────────────────────────────
#
# What the companies of one Emerging Frontiers theme sell: the theme's NAME and its ticker list
# (never its performance, momentum, ETF-based reasons, image, subtitle or any Gemini text), each
# member through the company gate, and the largest revenue segment of the first members (the
# revenue breakdown service, its own two-tier cache first). A member without a segment fact is
# still listed: the record keeps every member that passed the gates (6..24), and the theme's full
# size in companies before any gate (`theme_size`, `R.theme_company_count`: the app card counts
# tickers, but GOOG and GOOGL are one company — review round 15).


def _segment_fact(bd: Any, run_date: date, *, max_age_years: int = THEME_FACT_MAX_AGE_YEARS
                  ) -> Optional[Tuple[str, float, str]]:
    """(largest named segment, its share of revenue in (0, 1], fiscal year) — or None whenever
    the claim "largest segment, X% of revenue" could be wrong: a degraded breakdown, an old or
    unreadable fiscal year (older than `max_age_years` before the run's: the insider spotlight's 2, a theme member's
    `R.THEME_TAGGED_FACT_MAX_AGE_YEARS` — drawn with its own year tag), segments that include intersegment sales
    (eliminations), no reported revenue, one segment only, a tie for the top, an unnamed remainder bigger than the
    top, or segments adding up to more than revenue. The names are `_segments`' own (Batch 1: a code such as
    "QCT" or a generic "Products" is folded into the remainder, so it is never a tile's line — and a
    folded line larger than every named one leaves the remainder above the top: no fact)."""
    if list(getattr(bd, "degraded", None) or ()):
        return None
    fy = getattr(bd, "fiscal_year", None)
    if not isinstance(fy, str) or not re.fullmatch(r"[0-9]{4}", fy):
        return None
    if not (run_date.year - max_age_years <= int(fy) <= run_date.year):
        return None
    elim = _num(getattr(bd, "intersegment_eliminations", None))
    if elim is not None and elim > 0:
        return None
    revenue = _num(getattr(bd, "reported_revenue", None))
    if revenue is None or revenue <= 0:
        return None
    named, rest = _segments(bd)
    if not named or len(named) + (1 if rest > 0 else 0) < 2:
        return None
    ordered = sorted(named, key=lambda s: -s.value_usd)
    top = ordered[0]
    if len(ordered) > 1 and ordered[1].value_usd == top.value_usd:
        return None
    if rest > top.value_usd:
        return None
    if math.fsum(s.value_usd for s in named) + rest > revenue * (1.0 + THEME_SEGMENT_SUM_TOLERANCE):
        return None
    share = top.value_usd / revenue
    if not 0 < share <= 1:
        return None
    return top.name, share, fy


def _stack_is_gross(bd: Any) -> bool:
    """True when ``bd`` (a revenue breakdown, or one of `get_segment_years`' years) is a readable, non-degraded stack of
    a four-digit fiscal year whose segments INCLUDE intersegment sales (`intersegment_eliminations` > 0 — the
    service's reconciliation's "gross" outcome): no segment's share of revenue can be stated from it. Pure."""
    if list(getattr(bd, "degraded", None) or ()):
        return False
    fy = getattr(bd, "fiscal_year", None)
    if not isinstance(fy, str) or not re.fullmatch(r"[0-9]{4}", fy):
        return False
    elim = _num(getattr(bd, "intersegment_eliminations", None))
    return elim is not None and elim > 0


def _walked_back_fact(sym: str, newest: Any, years: Any, run_date: date
                      ) -> Optional[Tuple[str, float, str, str]]:
    """Phase 4 (2026-11-20, the owner's "for AI CHIPS, i don't see ARM, Intel and SNPS? add for me"): a theme member
    whose NEWEST segment stack is gross (`_stack_is_gross`: INTC since its Intel Foundry reorganisation) — the segment
    fact of the newest fiscal year whose stack is NOT, walking back year by year from ``newest``'s, within
    `R.THEME_TAGGED_FACT_MAX_AGE_YEARS` of the run: (segment, share, its fiscal year, the company's newest fiscal year)
    — the last tells the template the fact is never the company's latest year (it always draws its "FY2022" tag). A
    share is never computed from a gross stack. None — fail closed — whenever the walk could pick a wrong year:
    ``newest`` not gross or dated after the run's year; ``years`` (`get_segment_years`, newest first) not a sequence,
    holding a year that is not four
    digits, another company's year, or one fiscal year twice; its newest year not ``newest``'s, or not gross there too
    (the two reads disagree); a year missing on the way (never skipped); every year gross down to the bound; the
    newest non-gross year older than the bound, degraded (unpaired, conflicting, thin, unreconciled) or giving no
    fact (`_segment_fact`: a tie for the top, one segment, an unnamed remainder above the top …) — the walk never
    goes past it to an older year. Pure."""
    if not _stack_is_gross(newest) or not isinstance(years, (list, tuple)):
        return None
    latest = int(newest.fiscal_year)
    if latest > run_date.year:
        return None                            # a newest year dated after the run is a broken feed, never walked
    floor = run_date.year - R.THEME_TAGGED_FACT_MAX_AGE_YEARS
    by_year: Dict[int, Any] = {}
    for y in years:
        fy = getattr(y, "fiscal_year", None)
        if (not isinstance(fy, str) or not re.fullmatch(r"[0-9]{4}", fy) or getattr(y, "symbol", None) != sym
                or int(fy) in by_year):
            return None
        by_year[int(fy)] = y
    if not by_year or max(by_year) != latest or not _stack_is_gross(by_year[latest]):
        return None
    for year in range(latest - 1, floor - 1, -1):
        y = by_year.get(year)
        if y is None:
            return None
        if _stack_is_gross(y):
            continue
        fact = _segment_fact(y, run_date, max_age_years=R.THEME_TAGGED_FACT_MAX_AGE_YEARS)
        return None if fact is None else (fact[0], fact[1], fact[2], newest.fiscal_year)
    return None


async def _theme_member_fact(ctx: _Ctx, sym: str, bd: Any, years_sem: asyncio.Semaphore
                             ) -> Optional[Tuple[str, float, str, Optional[str]]]:
    """One theme member's segment fact (segment, share, fiscal year, None) from its newest breakdown (`_segment_fact`,
    up to `R.THEME_TAGGED_FACT_MAX_AGE_YEARS` old) — or, when that newest stack is GROSS, the walked-back fact
    (`_walked_back_fact`, its last item the company's newest year) from ONE older-years read (`get_segment_years`:
    the card's own two licensed feeds; ``years_sem`` in flight; memoised THEME_BREAKDOWN_TTL_SECONDS when it holds
    years). A failed older-years read is that member's missing fact (WARNING), never a guess."""
    fact = _segment_fact(bd, ctx.run_date, max_age_years=R.THEME_TAGGED_FACT_MAX_AGE_YEARS)
    if fact is not None:
        return fact[0], fact[1], fact[2], None
    if not _stack_is_gross(bd):
        return None
    years = _memo_get(("theme_segment_years", sym))
    if years is _MISS:
        try:
            async with years_sem:
                years = await ctx.revenue.get_segment_years(sym)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 — that member's fact only; logged
            logger.warning("company news theme_explainer: %s's newest segment stack (fiscal %s) includes intersegment "
                           "sales and its older years could not be read — listed without a segment (%s)", sym,
                           bd.fiscal_year, _describe(e))
            return None
        if isinstance(years, (list, tuple)) and years:
            _memo_put(("theme_segment_years", sym), years, THEME_BREAKDOWN_TTL_SECONDS)
    walked = _walked_back_fact(sym, bd, years, ctx.run_date)
    if walked is None:
        logger.info("company news theme_explainer: %s's newest segment stack (fiscal %s) includes intersegment sales "
                    "and no older year within %d years gives a certain fact — listed without a segment", sym,
                    bd.fiscal_year, R.THEME_TAGGED_FACT_MAX_AGE_YEARS)
    else:
        logger.info("company news theme_explainer: %s's newest segment stack (fiscal %s) includes intersegment sales "
                    "— its fact is fiscal %s's, drawn with that year", sym, bd.fiscal_year, walked[2])
    return walked


def _breakdown_memoisable(bd: Any) -> bool:
    """May a revenue breakdown sit in the adapter's memo? Only a COMPLETE build — the revenue service's own rule
    (`revenue_breakdown_service._is_cacheable`): a DEGRADED build (a 429 its gather swallowed, a feed that failed) lives
    60 s there so the next read retries the feed; memoised THEME_BREAKDOWN_TTL_SECONDS here it would freeze that failure
    as "no segment fact" for every theme read for 30 minutes (final review 2026-11-20: a cached failure is not an
    answer). A build with no reported revenue (the service's "no revenue data at all" placeholder among them) gives no
    fact either, and is never memoised. Pure."""
    return not list(getattr(bd, "degraded", None) or ()) and getattr(bd, "reported_revenue", None) is not None


async def _breakdown(ctx: _Ctx, sym: str, key: Tuple[Any, ...], ttl: float) -> Any:
    """One company's revenue breakdown through `_shared` (final review 2026-11-20): the read runs as the adapter's OWN
    leader task, so a caller whose deadline expires — an angle's share, a step's `wait_for` — cancels only its own wait.
    Awaited directly, the cancelled caller WAS the revenue service's in-flight leader, and the service fails its shared
    future with RuntimeError("in-flight fetch was cancelled"): a user opening that ticker's revenue card, or a paid
    report collecting it, that had joined the same build got that error. Memoised `ttl` seconds when
    `_breakdown_memoisable` (0: never — dedupe and detach only). The leader keeps running after a cancelled caller and
    lands in the service's own cache for the next read."""
    return await _shared(key, ttl, lambda: ctx.revenue.get_revenue_breakdown(sym), keep=_breakdown_memoisable)


async def _theme_facts(ctx: _Ctx, refs: Sequence[Any]) -> Dict[str, Tuple[str, float, str, Optional[str]]]:
    """{symbol: (segment, share, fiscal year, the company's newest fiscal year when the fact walked back past a gross
    one — else None)} for ``refs``, READ IN THE ORDER GIVEN (the caller's: the largest market value first):
    THEME_SEGMENT_CONCURRENCY breakdowns at a time (`_breakdown`: each a complete build memoised
    THEME_BREAKDOWN_TTL_SECONDS; the service keeps its own two-tier cache behind it), each member's fact by
    `_theme_member_fact`. Bounded by the angle's time: the waits still running when it is spent are cancelled and THOSE
    members are listed without a segment (WARNING) — the smallest, since the largest were started first; the reads
    already started run on as the adapter's own leaders (never cancelled inside the shared revenue service, whose
    other callers may have joined them) and no new one starts; the answers in hand are kept (each is a whole, checked
    fact). One
    member's failed lookup is that member's missing fact (WARNING). No lookup answered at all: every one failed →
    ``revenue_unavailable`` (the service is down); the time ran out first → ``budget_exhausted`` (the angle's turn
    lost). Less than MIN_STEP_SECONDS left → ``budget_exhausted`` before any read."""
    if not refs:
        return {}
    left = ctx.left()
    if left < MIN_STEP_SECONDS:
        raise MarketingNewsUnavailable(ctx.series, "budget_exhausted", f"stage=theme_segments left={left:.1f}s")
    reads = asyncio.Semaphore(THEME_SEGMENT_CONCURRENCY)
    years_sem = asyncio.Semaphore(THEME_SEGMENT_YEARS_CONCURRENCY)

    async def one(sym: str) -> Optional[Tuple[str, float, str, Optional[str]]]:
        bd = _memo_get(("theme_breakdown", sym))
        if bd is _MISS:
            async with reads:                  # FIFO: the reads start in the order the tasks were made
                bd = await _breakdown(ctx, sym, ("theme_breakdown", sym), THEME_BREAKDOWN_TTL_SECONDS)
        return await _theme_member_fact(ctx, sym, bd, years_sem)

    tasks = [asyncio.ensure_future(one(r.symbol)) for r in refs]
    try:
        _done, pending = await asyncio.wait(tasks, timeout=left)
    except asyncio.CancelledError:
        for t in tasks:
            t.cancel()
        raise
    for t in pending:
        t.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)
    out: Dict[str, Tuple[str, float, str, Optional[str]]] = {}
    failed: List[BaseException] = []
    unread: List[str] = []
    for ref, task in zip(refs, tasks):
        if task in pending or task.cancelled():
            unread.append(ref.symbol)
            continue
        err = task.exception()
        if err is not None:
            failed.append(err)
            logger.warning("company news theme_explainer: %s segment lookup failed — listed without a segment (%s)",
                           ref.symbol, _describe(err))
            continue
        fact = task.result()
        if fact is not None:
            out[ref.symbol] = fact
    if len(failed) + len(unread) == len(refs):
        if unread:
            raise MarketingNewsUnavailable(ctx.series, "budget_exhausted",
                                           f"stage=theme_segments; none of {len(refs)} lookups answered within "
                                           f"{left:.1f}s")
        raise MarketingNewsUnavailable(ctx.series, "revenue_unavailable",
                                       f"stage=theme_segments; all {len(refs)} lookups failed; "
                                       f"first {_describe(failed[0])}")
    if unread:
        logger.warning("company news theme_explainer: %d of %d segment lookup(s) not answered within the angle's "
                       "%.1f s — listed without a segment (read last: the smallest by market value): %s", len(unread),
                       len(refs), left, unread[:12])
    return out


@dataclass(frozen=True)
class _ThemeIn:
    """One theme past its gates and its profile batch: what every angle reads."""

    slug: str
    title: str
    refs: Tuple[Any, ...]                  # the members (CompanyRef), theme order, every gate passed
    profiles: Mapping[str, Mapping[str, Any]]
    size: Optional[int]
    as_of: date
    #: True when the row has no `tickers_as_of` (the app's live list, owner 2026-11-20): `as_of` is then the run day
    #: and is never printed (the record's `tickers_as_of_from_row`).
    as_of_from_row: bool = False


def _theme_as_of(row: Mapping[str, Any], run_date: date) -> Tuple[Optional[date], bool]:
    """(the date a theme's member list is aged from, whether that date is the list's OWN) — None: ``theme_stale``.

    OWNER DECISION 2026-11-20 ("Trust the app's lists (Recommended)": "Your app already shows these exact lists to
    users, so when a theme has no review date, posts treat its list as current … Each company still passes its own
    checks (still trading, has data)."): `trending_themes.tickers_as_of` NULL (every production row, 2026-11-20) →
    the live list counts as current — (run_date, False): never refused `theme_stale`, never dated in public
    (`ThemeExplainer.tickers_as_of_from_row`), and the row's `updated_at` is not read at all (it neither gates nor
    dates the list). A PRESENT `tickers_as_of` (a reviewed list) keeps THEME_STALE_DAYS exactly as before: the caller
    refuses a day after the run or more than 70 days old, and a present value that is not an ISO date is None (fail
    closed — a malformed value is a broken row, never read as a missing one). Pure."""
    raw = row.get("tickers_as_of")
    if raw is None:
        return run_date, False
    return (_day(raw) if isinstance(raw, str) else None), True


def _theme_history(ctx: _Ctx) -> Dict[str, Tuple[date, str]]:
    """{slug: (the day of its LAST theme post, that post's angle)} from every theme key the call sees — the exclude
    refs and the build's own theme ledger read (`ctx.theme_history`, THEME_HISTORY_DAYS back) — read strictly by
    `R.theme_ref_items` (an old key counts as "how they make money" on its date). A key dated after the run (a hand
    edit) counts as the run day: excluding more is the safe side. Two keys of one day: the later angle in rotation
    order. Pure."""
    out: Dict[str, Tuple[date, str]] = {}
    for ref in ctx.exclude | ctx.theme_history:
        items = R.theme_ref_items(ref)
        if items is None:
            continue
        slug, angle, day = items
        day = min(day, ctx.run_date)
        cur = out.get(slug)
        if cur is None or (day, R.THEME_ANGLES.index(angle)) > (cur[0], R.THEME_ANGLES.index(cur[1])):
            out[slug] = (day, angle)
    return out


def _theme_order(rows: Sequence[Mapping[str, Any]], history: Mapping[str, Tuple[date, str]]) -> List[Mapping[str, Any]]:
    """The rotation (owner 2026-11-19): the theme never posted first, then the one posted LONGEST ago; ties by
    `sort_order` (a missing one last), then the slug."""
    def key(r: Mapping[str, Any]) -> Tuple[int, date, int, str]:
        so = r.get("sort_order")
        last = history.get(str(r.get("slug") or ""))
        return (0 if last is None else 1, date.min if last is None else last[0],
                so if isinstance(so, int) and not isinstance(so, bool) else 10 ** 9, str(r.get("slug") or ""))

    return sorted(rows, key=key)


def _theme_read_priority(t: _ThemeIn) -> List[Any]:
    """The order "how they make money" READS its members' breakdowns in (phase 4, 2026-11-20): the largest market value
    first by the theme's own profile batch (`marketCap` — the rank only, never leaves, as the earnings series ranks; the
    market-value batch that orders the TILES runs beside the reads, so its answer is not in hand when they start), ties
    by the symbol; a member without a positive one after, in the theme's own order. A share of the time cut short thus
    leaves only the smallest members unread. Pure."""
    def cap(ref: Any) -> Optional[float]:
        v = _num((t.profiles.get(ref.symbol) or {}).get("marketCap"))
        return v if v is not None and v > 0 else None

    capped = sorted((r for r in t.refs if cap(r) is not None), key=lambda r: (-(cap(r) or 0.0), r.symbol))
    return capped + [r for r in t.refs if cap(r) is None]


async def _theme_tile_values(ctx: _Ctx, t: _ThemeIn) -> Dict[str, float]:
    """{symbol: market value} that orders the "how they make money" TILES: the ONE market-capitalization batch the
    "biggest" angle reads (memoised per member set, so the two angles share it), through the same checks
    (`_usable_market_values`: USD listing, one dated value per member, the post's day within the week). Order only —
    never drawn, never on the record (`ThemeExplainer` refuses market values outside a market-value post). Its read
    failing or outlasting the angle's time is NOT the angle's failure: no values (WARNING), so the tiles keep the
    theme's own order."""
    try:
        rows = await _theme_value_rows(ctx, t)
    except MarketingNewsUnavailable as e:
        logger.warning("company news theme_explainer: %s — no market values for the tile order (%s: %s); the members "
                       "with a fact keep the theme's own order", t.slug, e.reason, e.detail)
        return {}
    values, _as_of, _left_out = _usable_market_values(rows, t, ctx.run_date)
    return values


def _theme_tile_order(t: _ThemeIn, facts: Mapping[str, Any], values: Mapping[str, float]) -> List[Any]:
    """The record's member order for "how they make money" (the template draws its tiles in it, the members with a
    fact first): the members WITH a fact, the largest market value first (ties by the symbol — the "biggest" angle's
    rule), those without a usable value after them in the theme's own order; then every member without a fact, in the
    theme's own order (the captions' "{co} is also in …"). Pure."""
    with_fact = [r for r in t.refs if r.symbol in facts]
    valued = sorted((r for r in with_fact if r.symbol in values), key=lambda r: (-values[r.symbol], r.symbol))
    return (valued + [r for r in with_fact if r.symbol not in values]
            + [r for r in t.refs if r.symbol not in facts])


async def _theme_make_money(ctx: _Ctx, t: _ThemeIn) -> Union[Any, str]:
    """Angle 1, "how they make money" (theme_ai_chips.jpg, theme_ai_chips_more.jpg): every member, each with its largest
    revenue line and that line's share of revenue where certain. Phase 4 (2026-11-20, the owner's "for AI CHIPS, i don't
    see ARM, Intel and SNPS? add for me"): EVERY member's breakdown is read (`_theme_facts`, in `_theme_read_priority`'s
    market-value order), a member whose newest stack is gross walks back to the newest year that is not
    (`_walked_back_fact`), and the record lists the members with a fact LARGEST MARKET VALUE FIRST (the market-value
    batch "biggest" reads, beside the breakdown reads: `_theme_tile_values`), so the picture's 12 tiles are the largest
    companies that have one — and only those THEME_FACT_TILES carry their fact (the rest are named: the captions' "+N
    more"). At least THEME_ANGLE_MIN_COMPANIES members with a fact, else ``theme_facts_thin``."""
    values_task = asyncio.ensure_future(_theme_tile_values(ctx, t))
    try:
        facts = await _theme_facts(ctx, _theme_read_priority(t)[:THEME_SEGMENT_LOOKUPS])
        values = await values_task
    except BaseException:
        values_task.cancel()
        await asyncio.gather(values_task, return_exceptions=True)
        raise
    if len(facts) < max(R.THEME_MIN_FACTS, R.THEME_ANGLE_MIN_COMPANIES):
        return "theme_facts_thin"
    # the picture's tiles carry the facts: the THEME_FACT_TILES largest members with one; the rest are named only
    ranked = [r.symbol for r in _theme_tile_order(t, facts, values) if r.symbol in facts]
    if len(ranked) > THEME_FACT_TILES:
        logger.info("company news theme_explainer: %s — %d member(s) with a fact past the picture's %d tiles are listed "
                    "without it (the captions name them): %s", t.slug, len(ranked) - THEME_FACT_TILES,
                    THEME_FACT_TILES, ranked[THEME_FACT_TILES:])
        facts = {s: facts[s] for s in ranked[:THEME_FACT_TILES]}

    def member(ref: Any) -> Any:
        f = facts.get(ref.symbol)
        if f is None:
            return R.ThemeMember(company=ref, top_segment=None, top_segment_share=None, fiscal_year=None)
        return R.ThemeMember(company=ref, top_segment=f[0], top_segment_share=f[1], fiscal_year=f[2],
                             latest_fiscal_year=f[3])

    members = tuple(member(ref) for ref in _theme_tile_order(t, facts, values))
    return R.ThemeExplainer(series="theme_explainer", slug=t.slug, title=t.title, members=members,
                            tickers_as_of=t.as_of, tickers_as_of_from_row=t.as_of_from_row, theme_size=t.size,
                            angle="make_money", posted_on=ctx.run_date)


async def _fetch_market_values(fmp: Any, symbols: Sequence[str]) -> List[Dict[str, Any]]:
    got = await fmp.get_market_cap_batch(list(symbols))
    if not isinstance(got, list):
        raise FMPUnavailableException(f"market-capitalization batch returned {type(got).__name__}, not a list")
    return [r for r in got if isinstance(r, dict)]


async def _theme_value_rows(ctx: _Ctx, t: _ThemeIn) -> List[Dict[str, Any]]:
    """The theme members' market-capitalization batch rows: ONE licensed call ("3 Company Information"), memoised
    THEME_MARKET_VALUE_TTL_SECONDS per member set — shared by "biggest" (its values) and "how they make money" (its
    tile order). A failed call is ``profiles_unavailable``-shaped; the time running out, ``budget_exhausted``."""
    symbols = sorted(r.symbol for r in t.refs)
    return await ctx.step("theme_market_values",
                          lambda: _shared(("theme_market_values", tuple(symbols)), THEME_MARKET_VALUE_TTL_SECONDS,
                                          lambda: _fetch_market_values(ctx.fmp, symbols)),
                          reason="profiles_unavailable")


def _usable_market_values(rows: Sequence[Mapping[str, Any]], t: _ThemeIn, run_date: date
                          ) -> Tuple[Dict[str, float], Optional[date], List[str]]:
    """({symbol: market value in USD}, their ONE date, the members left out) from the batch ``rows``. Only `symbol`,
    `date` and `marketCap` of a row are read — never a price. A member is LEFT OUT (never ranked on a guess) when its
    listing does not trade in USD (the batch's figure is in the listing's own currency), it has no row, its value is
    missing / not a positive finite number / over THEME_MARKET_VALUE_MAX_USD, it has two rows that disagree, or its row
    is dated another day than the post's. The post's day is the day most rows carry (ties: the newest), and only within
    THEME_MARKET_VALUE_MAX_AGE_DAYS before the run (else every member is left out — WARNING). Pure but for the log."""
    symbols = sorted(r.symbol for r in t.refs)
    seen: Dict[str, Tuple[float, date]] = {}
    bad: Set[str] = set()
    for row in rows:
        sym = R.canonical_symbol(row.get("symbol"))
        if sym is None or sym not in symbols:
            continue
        value, day = _num(row.get("marketCap")), _day(row.get("date"))
        if (value is None or not 0 < value <= R.THEME_MARKET_VALUE_MAX_USD or day is None
                or (sym in seen and seen[sym] != (value, day))):
            bad.add(sym)
            continue
        seen[sym] = (value, day)
    usd = {r.symbol for r in t.refs
           if str((t.profiles.get(r.symbol) or {}).get("currency") or "").strip().upper() == "USD"}
    for sym in symbols:
        if sym in bad or sym not in seen or sym not in usd:
            bad.add(sym)
    good = {s: v for s, v in seen.items() if s not in bad}
    days = Counter(d for _v, d in good.values())
    as_of = max(days, key=lambda d: (days[d], d)) if days else None
    if as_of is not None and not 0 <= (run_date - as_of).days <= R.THEME_MARKET_VALUE_MAX_AGE_DAYS:
        logger.warning("company news theme_explainer: %s market values are dated %s — not within %d days before the "
                       "run %s; none used", t.slug, as_of, R.THEME_MARKET_VALUE_MAX_AGE_DAYS, run_date)
        as_of, good = None, {}
    out = {s: v for s, (v, d) in good.items() if d == as_of}
    return out, as_of, sorted(set(symbols) - set(out))


async def _theme_market_values(ctx: _Ctx, t: _ThemeIn) -> Tuple[Dict[str, float], Optional[date]]:
    """({symbol: market value in USD}, their ONE date) for the "biggest" angle: the memoised batch
    (`_theme_value_rows`) through `_usable_market_values`; every member left out is counted
    (``market_value_unusable``) and the post says "{n} of its {m} companies". A failed call is
    ``profiles_unavailable``-shaped: the angle is unavailable (the caller tries the next angle)."""
    rows = await _theme_value_rows(ctx, t)
    out, as_of, left_out = _usable_market_values(rows, t, ctx.run_date)
    if left_out:
        ctx.reject("market_value_unusable", len(left_out))
        logger.info("company news theme_explainer: %s — %d of %d member(s) left out of the market-value post (no "
                    "usable, same-day USD value: %s)", t.slug, len(left_out), len(t.refs), left_out[:12])
    return out, as_of


async def _theme_biggest(ctx: _Ctx, t: _ThemeIn) -> Union[Any, str]:
    """Angle 2, "who's biggest" by market value (theme_biggest_power.jpg): the members ranked by their dated market
    value, the largest first (`_theme_market_values`; a member without a usable value is left out and the post says
    "{n} of its {m} companies"). At least THEME_ANGLE_MIN_COMPANIES valued members, else ``theme_values_thin``. A
    fact only — never a price, a % move or a verdict."""
    values, as_of = await _theme_market_values(ctx, t)
    valued = [ref for ref in t.refs if ref.symbol in values]
    if len(valued) < R.THEME_ANGLE_MIN_COMPANIES or as_of is None:
        return "theme_values_thin"
    ranked = sorted(valued, key=lambda r: (-values[r.symbol], r.symbol))
    members = tuple(R.ThemeMember(company=r, top_segment=None, top_segment_share=None, fiscal_year=None,
                                  market_value_usd=values[r.symbol]) for r in ranked)
    return R.ThemeExplainer(series="theme_explainer", slug=t.slug, title=t.title, members=members,
                            tickers_as_of=t.as_of, tickers_as_of_from_row=t.as_of_from_row, theme_size=t.size,
                            angle="biggest", posted_on=ctx.run_date,
                            values_as_of=as_of)


# ── the theme post's last three angles (news-v23 phase 3, 2026-11-20) ─────────────────────────────────────────────


def _theme_table_refs(ctx: _Ctx, t: _ThemeIn, angle: str) -> List[Any]:
    """The theme's members (theme order) whose display name the angle's TABLE can draw (`R.theme_table_name_fits`);
    the others are left out before any read (``company_name_unusable``), so the template never refuses the record — a
    theme is never stuck on an angle it cannot draw."""
    out: List[Any] = []
    for ref in t.refs:
        if R.theme_table_name_fits(angle, ref.name):
            out.append(ref)
        else:
            logger.info("company news theme_explainer: %s left out of the %s post — its name does not fit the table's "
                        "name column", ref.symbol, angle)
            ctx.reject("company_name_unusable")
    return out


async def _fetch_annual_statements(fmp: Any, sym: str) -> List[Dict[str, Any]]:
    """``sym``'s latest THEME_STATEMENT_ROWS ANNUAL income statements — ONE licensed call — as dict rows; no dict row
    raises (the caller leaves the member out: an exception is never memoised)."""
    rows = await fmp.get_income_statement(sym, "annual", limit=THEME_STATEMENT_ROWS)
    dated = [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []
    if not dated:
        raise FMPUnavailableException(f"annual income statement of {sym}: {type(rows).__name__} with no row")
    return dated


def _theme_growth_fact(rows: Sequence[Any], run_date: date) -> Union[Tuple[str, float, float, float], str]:
    """(latest fiscal year, its revenue, the year before's revenue, its net income) from a member's annual income
    statements — or the reason the claim "revenue grew X% and it kept $Y per $100" could be wrong (`growth_unusable`,
    with the detail logged by the caller): fewer than two dated years, two rows of one date that disagree, the latest
    year after the run or more than `R.THEME_STATEMENT_MAX_AGE_DAYS` before it, the two years' ends not
    `R.THEME_GROWTH_YEAR_GAP_DAYS` apart, an unreadable or not consecutive fiscal year, two reporting currencies, a
    missing or non-positive revenue, a missing net income, growth above `R.THEME_GROWTH_MAX_ABS`, net income above
    revenue or a loss past `R.THEME_LOSS_MAX_RATIO` × revenue, or a profit per $100 too small to print (under a cent,
    not zero). The figures are in the company's reporting currency: the post states only the two ratios. Pure."""
    by_day: Dict[date, Mapping[str, Any]] = {}
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        day = _day(row.get("date"))
        if day is None:
            continue
        if day in by_day and any(by_day[day].get(k) != row.get(k) for k in ("revenue", "netIncome")):
            return "two rows of one year disagree"
        by_day.setdefault(day, row)
    days = sorted(by_day, reverse=True)
    if len(days) < 2:
        return "fewer than two dated years"
    latest, prior = by_day[days[0]], by_day[days[1]]
    age = (run_date - days[0]).days
    if age < 0 or age > R.THEME_STATEMENT_MAX_AGE_DAYS:
        return f"latest year ended {days[0]} ({age} days before the run)"
    gap = (days[0] - days[1]).days
    lo, hi = R.THEME_GROWTH_YEAR_GAP_DAYS
    if not lo <= gap <= hi:
        return f"the two years end {gap} days apart"
    fy, fy_prior = _row_fy(latest), _row_fy(prior)
    if not re.fullmatch(r"[0-9]{4}", fy) or not re.fullmatch(r"[0-9]{4}", fy_prior) or int(fy) != int(fy_prior) + 1:
        return f"fiscal years {fy[:8]!r} / {fy_prior[:8]!r} are not consecutive"
    if not run_date.year - 3 <= int(fy) <= run_date.year + 1:
        return f"fiscal year {fy} is not near the run"
    cur = [str(r.get("reportedCurrency") or "").strip().upper() for r in (latest, prior)]
    if not cur[0] or cur[0] != cur[1]:
        return "the two years' reporting currencies differ or are missing"
    revenue, before, net = _num(latest.get("revenue")), _num(prior.get("revenue")), _num(latest.get("netIncome"))
    if revenue is None or before is None or revenue <= 0 or before <= 0:
        return "revenue missing or not positive"
    if net is None:
        return "net income missing"
    growth = (revenue - before) / before
    if abs(growth) > R.THEME_GROWTH_MAX_ABS:
        return f"growth {growth:+.2f} past the plausibility bound"
    if net > revenue or net < -R.THEME_LOSS_MAX_RATIO * revenue:
        return "net income above revenue, or an implausible loss"
    per_100 = 100.0 * net / revenue
    if net != 0 and abs(per_100) < 0.005:
        return "profit per $100 under a cent"
    return fy, revenue, before, net


async def _theme_growth_profit(ctx: _Ctx, t: _ThemeIn) -> Union[Any, str]:
    """Angle 3, "growth + profit" (theme_growth_profit_silicon.jpg): the first THEME_STATEMENT_LOOKUPS members (theme
    order) read, the first `R.THEME_GROWTH_MAX_MEMBERS` of them with a certain fact kept (the rest are spares), each
    with its latest fiscal year's revenue growth over the year before and its net income per $100 of revenue
    (`_theme_growth_fact`); a member without a certain fact is LEFT OUT (``growth_unusable`` /
    ``statements_unreadable``, never a guess). At least THEME_ANGLE_MIN_COMPANIES members with the fact, else
    ``theme_growth_thin``; every read failing → ``revenue_unavailable`` (the source is down, not "nothing qualified").
    The rows keep the THEME's order: no ranking word."""
    refs = _theme_table_refs(ctx, t, "growth_profit")[:THEME_STATEMENT_LOOKUPS]
    sem = asyncio.Semaphore(THEME_LOOKUP_CONCURRENCY)

    async def one(sym: str) -> Any:
        async with sem:
            return await _shared(("theme_annual", sym), THEME_STATEMENT_TTL_SECONDS,
                                 lambda: _fetch_annual_statements(ctx.fmp, sym))

    results = await ctx.step("theme_statements",
                             lambda: asyncio.gather(*(one(r.symbol) for r in refs), return_exceptions=True),
                             reason="revenue_unavailable")
    failed = [r for r in results if isinstance(r, BaseException)]
    if refs and len(failed) == len(results):
        raise MarketingNewsUnavailable(ctx.series, "revenue_unavailable",
                                       f"stage=theme_statements; all {len(results)} reads failed; "
                                       f"first {_describe(failed[0])}")
    members: List[Any] = []
    for ref, rows in zip(refs, results):
        if isinstance(rows, BaseException):
            logger.warning("company news theme_explainer: %s annual statements unreadable — left out of the growth + "
                           "profit post (%s)", ref.symbol, _describe(rows))
            ctx.reject("statements_unreadable")
            continue
        fact = _theme_growth_fact(rows, ctx.run_date)
        if isinstance(fact, str):
            logger.info("company news theme_explainer: %s left out of the growth + profit post — %s", ref.symbol, fact)
            ctx.reject("growth_unusable")
            continue
        if len(members) >= R.THEME_GROWTH_MAX_MEMBERS:
            continue                           # the post's companies are taken (theme order); a spare read only
        fy, revenue, before, net = fact
        members.append(R.ThemeMember(company=ref, top_segment=None, top_segment_share=None, fiscal_year=fy,
                                     revenue_usd=revenue, prior_revenue_usd=before, net_income_usd=net))
    if len(members) < R.THEME_ANGLE_MIN_COMPANIES:
        return "theme_growth_thin"
    return R.ThemeExplainer(series="theme_explainer", slug=t.slug, title=t.title, members=tuple(members),
                            tickers_as_of=t.as_of, tickers_as_of_from_row=t.as_of_from_row, theme_size=t.size,
                            angle="growth_profit", posted_on=ctx.run_date)


def _theme_insider_totals(rows: Sequence[Any], *, cik: str, window_start: date, window_end: date
                          ) -> Union[Tuple[float, float], str]:
    """(purchases, sales) — code P and code S lines, "open market or private" (never drawn as "open-market") — in
    dollars AS FILED (Σ shares × each line's own per-share amount) over one ISSUER's Form 4 rows FILED in
    [window_start, window_end], or the reason the totals could be wrong
    (the member is then LEFT OUT, never shown with a guessed zero):

    * ``amended_filing`` — ANY amendment row of the issuer (a "/A" form, any code, any security, any reporter) filed on
      or after `window_start` (the Form 4 posts' company-level rule, `_is_amendment_row` / `_filed_since`);
    * ``insider_feed_unusable`` — a row of another issuer (the read was not filtered), or a code-P / code-S line in the
      window whose filing date, security, direction, shares or per-share amount cannot be read (a blank security on
      such a line is not known to be stock — fail closed).

    Counted: Form 4 lines (a blank form reads "4"), code P (purchases) and code S (sales) by the code before the dash
    of `transactionType` ("S-Sale", "S-Sale+OE"), COMMON stock only (`is_common_stock`: an option, a warrant, a note, a
    preferred or a unit line is a derivative or another security — excluded), acquisition "A" for P and disposition
    "D" for S when stated. Never an award, an option exercise, a gift or tax withholding (codes A, M, G, F …). The same
    line (reporter, trade date, code, ownership, shares, per-share amount) on two filings counts once, on the earliest
    — identical lines on ONE filing are separate fills (`extract_insider_buys`' rule).

    JOINT FILERS (fix round 2026-11-20): a fund, its general partner and its manager — or a trust and its trustee —
    each report the SAME Form 4 line under their own reporter, so summing every reporter's copy counted one $50M sale
    as $150M. The COUNTED lines go through `_insider_common.distinct_trades` (one copy per issuer, trade date, code,
    shares, per-share amount, security and post-trade balance, across different reporters — the holders and report
    paths' rule) AFTER every raw row was read for the issuer and amendment checks above (a 4/A filed under a second
    co-filer alone is still seen: `distinct_trades` has no form type). A copy it cannot match — the same-looking line
    (trade date, code, shares, per-share amount, security) under another reporter with no readable balance on one of
    them — could be a joint copy or a second trade: ``insider_feed_unusable`` (fail closed, never a doubled total).
    Pure."""
    want = R.cik10(cik)
    kept: List[Tuple[Dict[str, Any], Tuple[Any, ...], date, str, float]] = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        issuer = normalize_cik(row.get("companyCik"))
        if issuer is not None and R.cik10(issuer) != want:
            return "insider_feed_unusable"
        if _is_amendment_row(row) and _filed_since(row, window_start):
            return "amended_filing"
        tx = row.get("transactionType")
        code = tx.split("-", 1)[0].strip().upper() if isinstance(tx, str) else ""
        if code not in ("P", "S"):
            continue
        form = row.get("formType")
        form_type = str(form).strip().upper() if form not in (None, "") else "4"
        if not form_type.startswith("4"):
            continue
        filed = _day(row.get("filingDate"))
        if filed is None:
            return "insider_feed_unusable"
        if not window_start <= filed <= window_end:
            continue
        security = row.get("securityName")
        if not isinstance(security, str) or not security.strip():
            return "insider_feed_unusable"
        if not is_common_stock(security):
            continue                                  # a derivative or another security: not the stock
        direction = str(row.get("acquisitionOrDisposition") or "").strip().upper()
        if direction and direction != ("A" if code == "P" else "D"):
            return "insider_feed_unusable"
        shares, each = _num(row.get("securitiesTransacted")), _num(row.get("price"))
        if shares is None or each is None or shares <= 0 or each <= 0:
            return "insider_feed_unusable"
        dollars = shares * each
        if not math.isfinite(dollars) or dollars > _FORM4_MAX_ROW_DOLLARS:
            return "insider_feed_unusable"
        traded = _day(row.get("transactionDate"))
        key = (insider_reporter_key(row), traded, code, str(row.get("directOrIndirect") or "").strip().upper(),
               round(shares, 4), round(each, 4))
        kept.append((dict(row), key, filed, code, dollars))
    # joint filers: one copy of each line (the copies handed in are the very objects `distinct_trades` returns)
    survivors = {id(r) for r in distinct_trades([k[0] for k in kept])}
    kept = [k for k in kept if id(k[0]) in survivors]
    reporters: Dict[Tuple[Any, ...], Set[str]] = {}
    unmatched: Set[Tuple[Any, ...]] = set()
    for row, key, _filed, _code, _d in kept:
        line = key[1:3] + key[4:] + (str(row.get("securityName") or "").strip().casefold(),)
        reporters.setdefault(line, set()).add(key[0])
        if _num(row.get("securitiesOwned")) is None:
            unmatched.add(line)
    if any(len(reporters[line]) > 1 for line in unmatched):
        return "insider_feed_unusable"           # a joint copy or a second trade — cannot tell: never a doubled total
    first: Dict[Tuple[Any, ...], date] = {}
    for _row, key, filed, _code, _d in kept:
        if key not in first or filed < first[key]:
            first[key] = filed
    buys = math.fsum(d for _row, key, filed, code, d in kept if code == "P" and filed == first[key])
    sells = math.fsum(d for _row, key, filed, code, d in kept if code == "S" and filed == first[key])
    if 0 < buys < 1 or 0 < sells < 1:
        return "insider_feed_unusable"           # a total under $1 would print as "$0" — a zero that is not one
    return buys, sells


async def _walk_theme_issuer(fmp: Any, cik: str, window_start: date) -> List[Dict[str, Any]]:
    """One ISSUER's insider rows filed since `window_start` (every code, class and form, by CIK): the fail-closed
    per-issuer feed, at most THEME_INSIDER_MAX_PAGES pages (past them `FMPPartialPageException`: the issuer is left
    out, never totalled on a partial window)."""
    rows = await fmp.get_insider_trades_since(window_start.isoformat(), company_cik=cik,
                                              page_size=THEME_INSIDER_PAGE_SIZE, max_pages=THEME_INSIDER_MAX_PAGES)
    if not isinstance(rows, list):
        raise FMPUnavailableException(f"per-issuer insider feed returned {type(rows).__name__}, not a list")
    return rows


async def _theme_insiders(ctx: _Ctx, t: _ThemeIn) -> Union[Any, str]:
    """Angle 4, the "insider scorecard" (theme_insiders_cyber.jpg; owner 2026-11-19): for the first
    THEME_INSIDER_LOOKUPS members (theme order), each issuer's Form 4 purchases (code P) and sales (code S) FILED in the
    THEME_INSIDER_WINDOW_DAYS days before the run (`_theme_insider_totals`, by the profile's issuer CIK). A member is
    CHECKED when its read is whole and clean; it is LISTED when it has a purchase or a sale. A member with no CIK, a
    failed or partial read, another issuer's rows, an unreadable P / S line (``insider_feed_unusable``) or a Form 4/A
    in the window (``amended_filing``) is neither — never a zero. At least THEME_ANGLE_MIN_COMPANIES listed members,
    else ``theme_insiders_thin``; every read failing → ``insider_feed_unavailable``. Company-level totals only: no
    person, role or name of anyone is read into the record.

    CHECKED needs a Form 4 FILER (fix round 2026-11-20): the post says "No purchase or sale … was disclosed at the
    other {k} companies checked", which is true only of a company whose insiders file Form 4 and whose read came back.
    So, before any read, a member that is not a US common listing — an ADR, a non-USD listing or a non-common line
    (`R.company_from_profile(purpose="common")`: the Congress Count's and the Form 4 posts' gate; a foreign private
    issuer is exempt from Section 16 and files no Form 4) — is left out with that reason (never read, never checked);
    and a read that holds NO row of the issuer at all is not a check (``insider_feed_unusable``): the per-issuer read
    sends no date filter, so any issuer that ever filed returns its newest rows — an empty answer is a foreign issuer
    the flags missed (ASML, CHKP: ordinary shares, `isAdr` False), a wrong CIK, or a refused read (FMP answers a page-0
    403/404 with ``[]``). Such reads are never memoised (``keep=bool``, as `_issuer_feed`). Every read failing or empty
    → ``insider_feed_unavailable`` (an outage, never "thin")."""
    window_end = ctx.run_date - timedelta(days=1)
    window_start = ctx.run_date - timedelta(days=R.THEME_INSIDER_WINDOW_DAYS)
    refs: List[Any] = []
    for r in _theme_table_refs(ctx, t, "insiders")[:THEME_INSIDER_LOOKUPS]:
        listing = R.company_from_profile(t.profiles.get(r.symbol), r.symbol, purpose="common")
        if isinstance(listing, str):
            logger.info("company news theme_explainer: %s left out of the insider scorecard before any read — %s (not "
                        "a US common listing: its insiders may file no Form 4)", r.symbol, listing)
            ctx.reject(listing)
            continue
        refs.append(r)
    ciks: Dict[str, Optional[str]] = {r.symbol: R.cik10((t.profiles.get(r.symbol) or {}).get("cik")) for r in refs}
    readable = [r for r in refs if ciks[r.symbol] is not None]
    for r in refs:
        if ciks[r.symbol] is None:
            logger.warning("company news theme_explainer: %s has no issuer CIK on its profile — left out of the insider "
                           "scorecard (its Form 4s cannot be read by issuer)", r.symbol)
            ctx.reject("insider_feed_unusable")
    sem = asyncio.Semaphore(THEME_INSIDER_CONCURRENCY)

    async def one(cik: str) -> Any:
        async with sem:
            return await _shared(("theme_insider_issuer", cik, window_start.isoformat()), INSIDER_RAW_TTL_SECONDS,
                                 lambda: _walk_theme_issuer(ctx.fmp, cik, window_start), keep=bool)

    results = await ctx.step(
        "theme_insider_feeds",
        lambda: asyncio.gather(*(one(ciks[r.symbol] or "") for r in readable), return_exceptions=True),
        reason="insider_feed_unavailable")
    failed = [x for x in results if isinstance(x, BaseException)]
    if readable and len(failed) == len(results):
        raise MarketingNewsUnavailable(ctx.series, "insider_feed_unavailable",
                                       f"stage=theme_insider_feeds; all {len(results)} reads failed; "
                                       f"first {_describe(failed[0])}")

    def issuer_rows(rows: Any, cik: str) -> int:
        want = R.cik10(cik)
        return sum(1 for row in rows if isinstance(row, Mapping)
                   and (issuer := normalize_cik(row.get("companyCik"))) is not None and R.cik10(issuer) == want)

    if readable and all(isinstance(x, BaseException) or not issuer_rows(x, ciks[r.symbol] or "")
                        for r, x in zip(readable, results)):
        raise MarketingNewsUnavailable(ctx.series, "insider_feed_unavailable",
                                       f"stage=theme_insider_feeds; none of {len(results)} reads held a row of its "
                                       f"issuer ({len(failed)} failed)")
    checked = 0
    members: List[Any] = []
    for ref, rows in zip(readable, results):
        if isinstance(rows, BaseException):
            logger.warning("company news theme_explainer: %s Form 4 read failed or ran past %d pages — left out of the "
                           "insider scorecard (%s)", ref.symbol, THEME_INSIDER_MAX_PAGES, _describe(rows))
            ctx.reject("insider_feed_unusable")
            continue
        if not issuer_rows(rows, ciks[ref.symbol] or ""):
            logger.warning("company news theme_explainer: %s per-issuer Form 4 read held no row of its issuer (%d row(s) "
                           "in all) — a foreign issuer, a wrong CIK or a refused read: left out of the insider "
                           "scorecard, not counted as checked", ref.symbol, len(rows))
            ctx.reject("insider_feed_unusable")
            continue
        got = _theme_insider_totals(rows, cik=ciks[ref.symbol] or "", window_start=window_start,
                                    window_end=window_end)
        if isinstance(got, str):
            logger.info("company news theme_explainer: %s left out of the insider scorecard — %s", ref.symbol, got)
            ctx.reject(got)
            continue
        checked += 1
        buys, sells = got
        if buys > 0 or sells > 0:
            members.append(R.ThemeMember(company=ref, top_segment=None, top_segment_share=None, fiscal_year=None,
                                         purchases_usd=buys, sales_usd=sells))
    if len(members) < R.THEME_ANGLE_MIN_COMPANIES:
        return "theme_insiders_thin"
    members.sort(key=R.theme_insider_order_key)
    return R.ThemeExplainer(series="theme_explainer", slug=t.slug, title=t.title, members=tuple(members),
                            tickers_as_of=t.as_of, tickers_as_of_from_row=t.as_of_from_row, theme_size=t.size,
                            angle="insiders", posted_on=ctx.run_date,
                            window_start=window_start, window_end=window_end, companies_checked=checked)


async def _theme_congress(ctx: _Ctx, t: _ThemeIn) -> Union[Any, str]:
    """Angle 5, "Congress" (theme_congress_defense.jpg): the theme's members whose stock at least
    `R.CONGRESS_MIN_MEMBERS` DISTINCT members of Congress disclosed PURCHASES of in the disclosure month — the
    congress_count series' own month (`selection.congress_disclosure_month`, due `CONGRESS_DUE_DAYS` after it ends),
    its own memoised walk of both chambers (`_congress_month` under the ``("congress", month)`` key: ONE walk of the
    feeds per build, whichever series reads it first) and its own identity-free count (purchases only, stock only,
    members deduplicated on hashed identity keys and dropped at the source — never a name, a party, a state or a
    chamber). A member whose in-month purchases may be another spelling of the same company is left out
    (`_issuer_clash`), and so is one the Congress Count's listing gate refuses (`purpose="common"`: an ADR, a non-USD or
    a non-common listing — fix round 2026-11-20; theme members are built as "listing" refs, which keep ADRs). At least
    THEME_ANGLE_MIN_COMPANIES such members, else ``theme_congress_thin``.

    The same (company, month) may also be on that month's congress_count post: both read the SAME memoised
    aggregate through the SAME listing gate, so the two counts can never disagree and a company refused there is
    refused here too; the theme post is a theme's view of those disclosures (its key is the theme's, never the
    month's) — a repeat of a true count, never a second fact (decided 2026-11-20)."""
    month = selection.congress_disclosure_month(ctx.run_date)
    if ctx.run_date < R.month_end_of(month) + timedelta(days=CONGRESS_DUE_DAYS):
        return "theme_congress_not_due"
    agg = await ctx.step(
        "congress_walk",
        lambda: _shared(("congress", month), CONGRESS_TTL_SECONDS, lambda: _congress_month(ctx.fmp, month)),
        reason="congress_feed_unavailable",
    )
    counts, refused = agg["counts"], agg["refused"]
    members: List[Any] = []
    for ref in _theme_table_refs(ctx, t, "congress"):
        n = counts.get(ref.symbol)
        if ref.symbol in refused:
            ctx.reject(refused[ref.symbol])
            continue
        if not isinstance(n, int) or n < R.CONGRESS_MIN_MEMBERS:
            continue
        # the Congress Count's own listing gate (`purpose="common"`, fix round 2026-11-20): a US common stock only — an
        # ADR, a non-USD or a non-common listing is refused there with this reason, so it is here too (the two posts
        # list the same companies, never only the counts)
        listing = R.company_from_profile(t.profiles.get(ref.symbol), ref.symbol, purpose="common")
        if isinstance(listing, str):
            logger.info("company news theme_explainer: %s left out of the Congress post — %s (the Congress Count's "
                        "listing gate)", ref.symbol, listing)
            ctx.reject(listing)
            continue
        clash = _issuer_clash(ref.symbol, ref, agg["stock_purchases"])
        if clash:
            logger.info("company news theme_explainer: %s left out of the Congress post (%s) — another in-month "
                        "purchase may be the same company", ref.symbol, clash)
            ctx.reject(clash)
            continue
        members.append(R.ThemeMember(company=ref, top_segment=None, top_segment_share=None, fiscal_year=None,
                                     congress_members=n))
    if len(members) < R.THEME_ANGLE_MIN_COMPANIES:
        return "theme_congress_thin"
    members.sort(key=R.theme_congress_order_key)
    return R.ThemeExplainer(series="theme_explainer", slug=t.slug, title=t.title, members=tuple(members),
                            tickers_as_of=t.as_of, tickers_as_of_from_row=t.as_of_from_row, theme_size=t.size,
                            angle="congress", posted_on=ctx.run_date,
                            month=month)


#: The theme ANGLES whose collector exists, by `R.THEME_ANGLES` name (pinned == `R.THEME_BUILT_ANGLES` and the
#: templates' angle builders). An angle is ONE function `(ctx, theme) -> record | rejection reason`; an angle not listed
#: would be skipped by the rotation with ``theme_angle_not_built`` and no read (every angle is built since 2026-11-20).
THEME_ANGLE_COLLECTORS: Dict[str, Callable[[_Ctx, _ThemeIn], Awaitable[Union[Any, str]]]] = {
    "make_money": _theme_make_money,
    "biggest": _theme_biggest,
    "growth_profit": _theme_growth_profit,
    "insiders": _theme_insiders,
    "congress": _theme_congress,
}


async def _collect_theme_explainer(ctx: _Ctx) -> Candidates:
    """The weekly theme post (news-v23 phase 3, owner 2026-11-19): ONE record per theme evaluated, in ROTATION order
    (`_theme_order`: never posted first, then the oldest post; ties by sort order, then slug), each theme at its NEXT
    angle (`R.next_theme_angle` after its last posted one, from `_theme_history`) — or the angle after, while an angle
    is not built (``theme_angle_not_built``), cannot be drawn by the run's worker (``theme_angle_unsupported``,
    `ctx.theme_angles`), or has fewer than THEME_ANGLE_MIN_COMPANIES companies with its data (``theme_facts_thin`` /
    ``theme_values_thin``). A theme posted fewer than THEME_REPEAT_DAYS days ago is ``already_posted`` before any read.
    Bounded: THEME_MAX_EVALUATED themes profiled, THEME_MAX_ANGLE_ATTEMPTS angle evaluations that read.

    An angle's source failing (``revenue_unavailable``, a market-value read) — or its reads outlasting the angle's own
    share of the time (THEME_ANGLE_BUDGET_SECONDS, fix round 2026-11-20) — is that angle's turn lost: the next angle
    is tried; with no record at all the first such failure is raised (an outage, never "nothing qualified"). Only the
    SERIES' deadline ends the walk (``budget_exhausted``, raised with no record). A held record survives a later
    theme's failure (review round 9 #10)."""
    rows = await _rows(
        ctx, "themes_explainer", ("themes_explainer",),
        lambda sb: sb.table(_THEMES_TABLE).select(_THEME_COLUMNS)
        .eq("is_active", True).order("sort_order").limit(200),
        reason="themes_unavailable",
    )
    history = _theme_history(ctx)
    records: List[Any] = []
    evaluated = attempts = 0
    unavailable: Optional[MarketingNewsUnavailable] = None
    for r in _theme_order(rows, history):
        if len(records) >= ctx.limit or evaluated >= THEME_MAX_EVALUATED or attempts >= THEME_MAX_ANGLE_ATTEMPTS:
            break
        if r.get("is_active") is not True:
            continue
        slug = r.get("slug")
        if not isinstance(slug, str) or not _THEME_SLUG_RE.fullmatch(slug):
            ctx.reject("record_invalid")
            continue
        as_of, reviewed = _theme_as_of(r, ctx.run_date)
        if as_of is None or as_of > ctx.run_date or (ctx.run_date - as_of).days > R.THEME_STALE_DAYS:
            ctx.reject("theme_stale")         # only a list with its OWN date (or a broken one) can be stale
            continue
        ctx.considered += 1
        last = history.get(slug)
        if last is not None and (ctx.run_date - last[0]).days < R.THEME_REPEAT_DAYS:
            ctx.reject("already_posted")      # the same theme within four weeks — before any profile call
            continue
        title = r.get("title")
        problem = R.theme_title_problem(title)
        if problem:
            ctx.reject(problem)
            continue
        tickers, blocked_raw = r.get("tickers"), r.get("blocked_tickers")
        if not isinstance(tickers, list) or (blocked_raw is not None and not isinstance(blocked_raw, list)):
            ctx.reject("record_invalid")
            continue
        blocked = {_sym_key(b) for b in (blocked_raw or ()) if isinstance(b, str)}
        members: List[str] = []
        for t in tickers:
            if isinstance(t, str) and _sym_key(t) in blocked:
                continue
            sym = R.canonical_symbol(t)
            if sym is None:
                ctx.reject(R.symbol_problem(t) or "symbol_grammar")
                continue
            if sym not in members and sym not in blocked:
                members.append(sym)
        lo, hi = R.THEME_MEMBERS
        if len(members) > hi:
            ctx.reject("theme_too_large")
            continue
        if len(members) < lo:
            ctx.reject("theme_members_thin")
            continue
        evaluated += 1
        # Review round 9 (#10): a theme already held was built and checked on complete data; a LATER theme's profile
        # batch failing or running out of budget ends the walk with the held record(s) — never throws them away.
        try:
            profiles = await _profiles(ctx, members)
        except MarketingNewsUnavailable as e:
            if not records:
                raise
            logger.warning("company news theme_explainer: %s unavailable at theme %s with %d record(s) held — "
                           "returning them (%s)", e.reason, slug, len(records), e.detail)
            break
        refs: List[Any] = []
        names: Set[str] = set()
        ciks: Set[str] = set()
        for sym in members:
            company = R.company_from_profile(profiles.get(sym), sym, purpose="listing")
            if isinstance(company, str):
                ctx.reject(company)
                continue
            # GOOG beside GOOGL: one company, listed once — the same display name, or the same
            # issuer CIK under another name (review round 15; the size below merges the same way).
            cik = R.cik10(profiles[sym].get("cik"))
            if company.name.casefold() in names or (cik is not None and cik in ciks):
                ctx.reject("share_class_overlap")
                continue
            names.add(company.name.casefold())
            if cik is not None:
                ciks.add(cik)
            refs.append(company)
        # The theme's size in COMPANIES before any gate (the shared contract's `theme_size`):
        # the copy says "{n} of its {m} companies" whenever a gate dropped one, so share classes
        # of one issuer count once (review round 15: GOOG + GOOGL read "7 of the 8 companies").
        size = R.theme_company_count(tickers, profiles)
        if len(refs) < lo:
            ctx.reject("theme_members_thin")
            continue
        theme = _ThemeIn(slug=slug, title=title, refs=tuple(refs), profiles=profiles, size=size, as_of=as_of,
                         as_of_from_row=not reviewed)
        start = R.THEME_ANGLES.index(R.next_theme_angle(None if last is None else last[1]))
        for k in range(len(R.THEME_ANGLES)):
            angle = R.THEME_ANGLES[(start + k) % len(R.THEME_ANGLES)]
            collect = THEME_ANGLE_COLLECTORS.get(angle)
            if collect is None:
                ctx.reject("theme_angle_not_built")
                continue
            klass = R.THEME_ANGLE_CLASSES.get(angle)
            if klass is not None and ctx.theme_classes is not None and klass not in ctx.theme_classes:
                ctx.reject("theme_angle_class_off")      # an angle of filings while class C is off — no read
                continue
            if angle not in ctx.theme_angles:
                ctx.reject("theme_angle_unsupported")
                continue
            if attempts >= THEME_MAX_ANGLE_ATTEMPTS:
                logger.info("company news theme_explainer: the call's %d angle evaluations are spent — %s stops at "
                            "%s", THEME_MAX_ANGLE_ATTEMPTS, slug, angle)
                break
            attempts += 1
            # the angle's OWN share of the time (THEME_ANGLE_BUDGET_SECONDS), never past the series' deadline
            series_deadline = ctx.deadline
            ctx.deadline = min(series_deadline, ctx.deps.monotonic() + THEME_ANGLE_BUDGET_SECONDS[angle])
            try:
                got = await collect(ctx, theme)
            except MarketingNewsUnavailable as e:
                if e.reason == "budget_exhausted" and series_deadline - ctx.deps.monotonic() >= MIN_STEP_SECONDS:
                    # the ANGLE's share ran out, not the series': that angle's turn lost (an outage — it might have
                    # qualified), the next angle tried with the series' time
                    unavailable = unavailable or e
                    logger.warning("company news theme_explainer: %s angle %s ran out of its own %.0f s — trying the "
                                   "next angle (%s)", slug, angle, THEME_ANGLE_BUDGET_SECONDS[angle], e.detail)
                    continue
                if e.reason == "budget_exhausted":
                    if not records:
                        raise
                    logger.warning("company news theme_explainer: budget exhausted at theme %s angle %s with %d "
                                   "record(s) held — returning them", slug, angle, len(records))
                    return _theme_done(ctx, records)
                unavailable = unavailable or e
                logger.warning("company news theme_explainer: %s angle %s unavailable (%s: %s) — trying the next "
                               "angle", slug, angle, e.reason, e.detail)
                continue
            finally:
                ctx.deadline = series_deadline
            if isinstance(got, str):
                logger.info("company news theme_explainer: %s angle %s refused as %s — trying the next angle", slug,
                            angle, got)
                ctx.reject(got)
                continue
            records.append(got)
            logger.info("company news theme_explainer: %s at angle %s (last posted %s) with %d member(s)", slug,
                        angle, "never" if last is None else f"{last[0]} as {last[1]}", len(got.members))
            break
    if not records and unavailable is not None:
        raise unavailable
    return _theme_done(ctx, records)


def _theme_done(ctx: _Ctx, records: Sequence[Any]) -> Candidates:
    ctx.check_profiles()
    return ctx.done(records, "theme_none_qualified")


# ── the public entry points ───────────────────────────────────────────────────

#: One collector per series whose code exists (2a + 2b, and the spotlight lane's two single-company series,
#: 2026-11-15, shipped with their templates in news-v22): exactly `selection.SHIPPED_SERIES` (pinned), and a chain
#: never names an unshipped series.
#: Production still runs only what the per-series switch MARKETING_NEWS_SERIES lists (its default is the 2a four).
COLLECTORS: Dict[str, Callable[[_Ctx], Awaitable[Candidates]]] = {
    "ceo_buys": _collect_ceo_buys,
    "insider_buys": _collect_insider_buys,
    "thirteen_f": _collect_thirteen_f,
    "congress_count": _collect_congress_count,
    "company_stakes": _collect_company_stakes,
    "earnings": _collect_earnings,
    "money_map": _collect_money_map,
    "theme_explainer": _collect_theme_explainer,
    "insider_spotlight": _collect_insider_spotlight,
    "thirteen_f_spotlight": _collect_thirteen_f_spotlight,
}
#: The most ``spotlight_recent`` entries one call takes — more is a caller's bug, refused. Sized from the data (review
#: of 2026-11-26): the script flow sends one (symbol, name) pair for EVERY company of every insider list accepted in
#: [run_date − SPOTLIGHT_ISSUER_COOLDOWN_DAYS, run_date] (its own run left out) — at most one list a day (one run per
#: day and lane), each of at most INSIDER_MAX_ROWS companies: (14 + 1) × 5 = 75 (final review of 2026-11-26: the owner's
#: "at most 5"). The old 64 dated from the one-issuer post and, at 8 a list, refused EVERY insider list once a
#: 5-day-a-week schedule (MARKETING_SPOTLIGHT_WEEKDAYS takes any days) had 9 full lists in the cooldown; 75 fits 15.
SPOTLIGHT_RECENT_MAX = (R.SPOTLIGHT_ISSUER_COOLDOWN_DAYS + 1) * R.INSIDER_MAX_ROWS


def _spotlight_recent_arg(raw: Any) -> Tuple[Tuple[str, str], ...]:
    """``spotlight_recent`` checked: ``(symbol, display name)`` pairs of strings — the symbol canonical or ``""``
    (unknown), the name 1..80 characters or ``""``, never both empty — at most `SPOTLIGHT_RECENT_MAX`. Anything else
    is a caller's bug: ValueError, never a silently shorter cooldown."""
    if isinstance(raw, (str, bytes, Mapping)):
        raise ValueError("spotlight_recent must be an iterable of (symbol, name) pairs")
    try:
        entries = iter(raw)
    except TypeError:
        raise ValueError("spotlight_recent must be an iterable of (symbol, name) pairs") from None
    out: List[Tuple[str, str]] = []
    for entry in entries:
        if not (isinstance(entry, tuple) and len(entry) == 2 and all(isinstance(x, str) for x in entry)):
            raise ValueError(f"spotlight_recent entry {entry!r:.80} is not a (symbol, name) pair of strings")
        sym, name = entry[0].strip(), " ".join(entry[1].split())
        if sym and R.canonical_symbol(sym) != sym:
            raise ValueError(f"spotlight_recent symbol {sym!r:.20} is not canonical")
        if len(name) > 80 or not (sym or name):
            raise ValueError(f"spotlight_recent entry {entry!r:.80} names nothing usable")
        out.append((sym, name))
        if len(out) > SPOTLIGHT_RECENT_MAX:
            raise ValueError(f"spotlight_recent holds more than {SPOTLIGHT_RECENT_MAX} entries")
    return tuple(out)


#: The most theme ledger refs one call takes (THEME_HISTORY_DAYS of two lanes is at most 168; more is a caller's bug).
THEME_HISTORY_MAX = 256


def _theme_classes_arg(classes: Any) -> Optional[FrozenSet[str]]:
    """``theme_classes`` checked: None (every class), or an iterable of the content classes switched on — strings only
    (ValueError otherwise: a caller's bug, never a silently wider set of angles)."""
    if classes is None:
        return None
    if isinstance(classes, (str, bytes, Mapping)):
        raise ValueError("theme_classes must be an iterable of content class names")
    got = frozenset(classes)
    if not all(isinstance(c, str) for c in got):
        raise ValueError("theme_classes must hold strings")
    return got


def _theme_args(history: Any, angles: Any) -> Tuple[FrozenSet[str], FrozenSet[str]]:
    """``theme_history`` / ``theme_angles`` checked (ValueError on a caller's bug, never a silently different rotation):
    the refs as a frozenset of strings (at most THEME_HISTORY_MAX), the angles a subset of `R.THEME_ANGLES` (None:
    every one)."""
    if isinstance(history, (str, bytes, Mapping)):
        raise ValueError("theme_history must be an iterable of ledger refs")
    refs = list(history or ())
    if len(refs) > THEME_HISTORY_MAX or not all(isinstance(r, str) for r in refs):
        raise ValueError(f"theme_history must hold at most {THEME_HISTORY_MAX} strings")
    if angles is None:
        return frozenset(refs), frozenset(R.THEME_ANGLES)
    if isinstance(angles, (str, bytes, Mapping)):
        raise ValueError("theme_angles must be an iterable of angle names")
    got = frozenset(angles)
    if not got <= frozenset(R.THEME_ANGLES):
        raise ValueError(f"theme_angles names unknown angle(s) {sorted(map(str, got - set(R.THEME_ANGLES)))[:5]}")
    return frozenset(refs), got


async def candidates(series: str, *, run_date: date, exclude: FrozenSet[str], limit: int = 5,
                     deadline: float, deps: Optional[NewsDeps] = None,
                     spotlight_checked_through: Optional[date] = None,
                     spotlight_recent: Iterable[Tuple[str, str]] = (),
                     theme_history: Iterable[str] = (), theme_angles: Optional[Iterable[str]] = None,
                     theme_classes: Optional[Iterable[str]] = None) -> Candidates:
    """The qualifying records of one series for ``run_date``, best first (contract D6).

    ``exclude`` holds the recent ledger refs (`marketing_scripts.source_ref`); a candidate whose
    ledger key is in it is dropped before any profile or logo call and counted ``already_posted``.
    ``deadline`` is an absolute `NewsDeps.monotonic` time. Raises ValueError for an unknown series
    or a bad argument, `MarketingNewsUnavailable` for everything that is not an honest answer.

    The insider spotlight's two inputs (the spotlight lane, 2026-11-15; the script flow passes them for that
    series only, every other series ignores them): ``spotlight_checked_through`` — the END of the last Form 4 window an
    earlier insider spotlight checked (C0, 2026-11-16), before ``run_date``, or None (its window starts a week back) —
    and ``spotlight_recent`` —
    ``(symbol, display name)`` of the insider spotlights accepted within the issuer cooldown
    (`_spotlight_recent_arg`).

    The theme explainer's two inputs (news-v23 phase 3, the weekly theme post; every other series ignores them):
    ``theme_history`` — the theme ledger refs of the build's own bounded read (`R.THEME_HISTORY_DAYS` back; the
    rotation reads them beside ``exclude``), and ``theme_angles`` — the angles the run's worker can draw (None: every
    angle; a worker without WORKER_CAPABILITY_THEME_ANGLES draws "how they make money" only). A history entry that is
    not a string, more than THEME_HISTORY_MAX entries, or an angle outside `R.THEME_ANGLES` is a caller's bug:
    ValueError. ``theme_classes`` (2026-11-20): the content classes switched on (MARKETING_CONTENT_CLASSES; None: every
    class) — an angle of filings (`R.THEME_ANGLE_CLASSES`: the insider scorecard, Congress) is skipped with
    ``theme_angle_class_off`` and no read while its class is off."""
    if series not in COLLECTORS:
        raise ValueError(f"unknown or unshipped company-news series {series!r}")
    if not isinstance(run_date, date) or isinstance(run_date, datetime):
        raise ValueError("run_date must be a date")
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
        raise ValueError(f"limit must be a positive int, got {limit!r}")
    if _num(deadline) is None:
        raise ValueError("deadline must be a finite monotonic time")
    if spotlight_checked_through is not None and (
            not isinstance(spotlight_checked_through, date) or isinstance(spotlight_checked_through, datetime)
            or spotlight_checked_through >= run_date):
        raise ValueError(f"spotlight_checked_through must be a date before {run_date}, "
                         f"got {spotlight_checked_through!r}")
    recent = _spotlight_recent_arg(spotlight_recent)
    history, angles = _theme_args(theme_history, theme_angles)
    classes = _theme_classes_arg(theme_classes)
    deps = deps or NewsDeps()
    ctx = _Ctx(series, run_date, frozenset(x for x in (exclude or ()) if isinstance(x, str)),
               limit, float(deadline), deps, spotlight_checked_through=spotlight_checked_through,
               spotlight_recent=recent, theme_history=history, theme_angles=angles, theme_classes=classes)
    started = deps.monotonic()
    try:
        if ctx.left() < MIN_STEP_SECONDS:
            raise MarketingNewsUnavailable(series, "budget_exhausted", f"stage=start left={ctx.left():.1f}s")
        result = await COLLECTORS[series](ctx)
    except MarketingNewsUnavailable as e:
        logger.warning("marketing news UNAVAILABLE series=%s run_date=%s reason=%s detail=%s rejections=%s",
                       series, run_date, e.reason, e.detail, dict(ctx.rejections))
        raise
    except asyncio.CancelledError:
        raise
    except Exception as e:  # noqa: BLE001 — a bug, not an upstream answer
        logger.exception("marketing news INTERNAL ERROR series=%s run_date=%s (%s)",
                         series, run_date, _describe(e))
        raise MarketingNewsUnavailable(series, "internal_error", _describe(e)) from e
    logger.info(
        "marketing news candidates series=%s run_date=%s candidates=%d kept=%d skip=%s "
        "rejections=%s keys=%s in %.1fs",
        series, run_date, ctx.considered, len(result.records), result.skip_reason,
        dict(result.rejections), [R.ledger_key(r) for r in result.records], deps.monotonic() - started,
    )
    return result


async def fetch_logo(symbol: str, *, max_bytes: int, timeout: float,
                     deps: Optional[NewsDeps] = None) -> Optional[Tuple[bytes, str]]:
    """The company's logo bytes and the response's content type, or None. Never raises
    (cancellation aside) and never validates the bytes (`logo_check.inspect_logo` does).

    Only ``https://images.financialmodelingprep.com/symbol/<SYM>.png`` is requested — built from
    the validated symbol and required to equal the profile's ``image`` exactly — and only while
    the profile says it is not the vendor's default image. No redirects; status 200 only;
    ``Accept-Encoding: identity`` and an encoded answer refused; the raw body is streamed and
    abandoned past ``max_bytes`` wire bytes. A WARNING names the symbol and the reason, never
    the URL."""
    sym = R.canonical_symbol(symbol)
    if sym is None:
        logger.warning("marketing logo: refused symbol=%r reason=bad_symbol", str(symbol)[:16])
        return None
    try:
        return await asyncio.wait_for(_fetch_logo(sym, int(max_bytes), float(timeout), deps or NewsDeps()),
                                      timeout=float(timeout))
    except asyncio.CancelledError:
        raise
    except asyncio.TimeoutError:
        logger.warning("marketing logo: no logo symbol=%s reason=timeout", sym)
    except Exception as e:  # noqa: BLE001 — a logo never fails a candidate
        logger.warning("marketing logo: no logo symbol=%s reason=error (%s)", sym, _describe(e))
    return None


async def _fetch_logo(sym: str, max_bytes: int, timeout: float, deps: NewsDeps) -> Optional[Tuple[bytes, str]]:
    if max_bytes <= 0 or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("max_bytes and timeout must be positive")
    hit = _memo_get(("logo", sym))
    if hit is not _MISS:
        return hit

    def no(reason: str) -> None:
        logger.warning("marketing logo: no logo symbol=%s reason=%s", sym, reason)

    profile = _memo_get(("profile", sym))
    if profile is _MISS:
        fmp = deps.fmp if deps.fmp is not None else get_fmp_client()
        profile = (await _shared(("profiles", (sym,)), 0, lambda: _fetch_profiles(fmp, [sym]))).get(sym)
    if not isinstance(profile, Mapping):
        no("no_profile")
        return None
    if profile.get("defaultImage") is not False:
        no("default_image")
        return None
    url = LOGO_URL_TEMPLATE.format(symbol=sym)
    if profile.get("image") != url:
        no("url_mismatch")
        return None
    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout), follow_redirects=False,
                                 transport=deps.http) as client:
        # Ask for the bytes as stored, refuse any encoded answer, and count WIRE bytes: httpx
        # decodes gzip/deflate/brotli per network read with no output limit, so a decoded-size
        # cap would let a few hundred compressed bytes inflate to hundreds of MB in this (the
        # single web) process before refusing them.
        async with client.stream("GET", url, headers={"Accept-Encoding": "identity"}) as resp:
            if resp.status_code != 200:
                no(f"http_{resp.status_code}")
                return None
            if resp.headers.get("content-encoding", "").strip().lower() not in _IDENTITY_ENCODINGS:
                no("encoded")
                return None
            declared = resp.headers.get("content-length")
            if declared is not None and declared.strip().isdigit() and int(declared) > max_bytes:
                no("too_large")
                return None
            chunks: List[bytes] = []
            size = 0
            async for chunk in resp.aiter_raw():
                size += len(chunk)
                if size > max_bytes:
                    no("too_large")
                    return None
                chunks.append(chunk)
            ctype = resp.headers.get("content-type", "").split(";")[0].strip().lower()
    body = b"".join(chunks)
    if not body:
        no("empty")
        return None
    _memo_put(("logo", sym), (body, ctype), LOGO_TTL_SECONDS)
    return body, ctype


#: `company_facts`: a sector or industry name as the profile writes it ("Technology", "Software - Infrastructure",
#: "Banks—Regional"): letters first, then letters, digits, spaces and plain punctuation, 2..60 characters.
_COMPANY_FACT_RE = re.compile(r"[A-Za-z][A-Za-z0-9 &,.'()/\-\u2013\u2014]{1,59}")


async def company_facts(symbol: str, *, timeout: float = COMPANY_FACTS_TIMEOUT_SECONDS,
                        deps: Optional[NewsDeps] = None) -> Optional[Dict[str, str]]:
    """``{"sector": …, "industry": …}`` of a company (B decision 8, 2026-11-15; workstream C's art brief) — the two
    NAME-FREE words of its FMP profile, a key left out when its value is missing or not a plain name, None when
    neither is known. Never the company's or a person's name, a price, a market cap, a URL or a description (the
    brief takes name-free facts only).

    From the profile memo a collector filled this process (no call — the template build that chose the record read
    the profile minutes before), else ONE profile read under ``timeout`` through the same shared fetch. Reachable
    from the script flow through `MarketingScriptService._news_source_fn` (no new import of this module). Never
    raises (cancellation aside): a bad symbol, a failed or slow read is None, logged at WARNING with the symbol."""
    sym = R.canonical_symbol(symbol) if isinstance(symbol, str) else None
    if sym is None or sym != symbol:
        logger.warning("company news company_facts: %r is not a canonical symbol — no facts", str(symbol)[:20])
        return None
    profile = _memo_get(("profile", sym))
    if profile is _MISS:
        deps = deps or NewsDeps()
        try:
            fmp = deps.fmp if deps.fmp is not None else get_fmp_client()
            got = await asyncio.wait_for(
                _shared(("profiles", (sym,)), 0, lambda: _fetch_profiles(fmp, [sym])), timeout=timeout)
            profile = got.get(sym) if isinstance(got, Mapping) else None
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 — never raises: the art brief goes without the facts
            logger.warning("company news company_facts: %s profile read failed — no facts (%s)", sym, _describe(e))
            return None
    if not isinstance(profile, Mapping):
        logger.warning("company news company_facts: %s has no profile — no facts", sym)
        return None
    out: Dict[str, str] = {}
    for key in ("sector", "industry"):
        value = profile.get(key)
        text = " ".join(value.split()) if isinstance(value, str) else ""
        if text and _COMPANY_FACT_RE.fullmatch(text) and not _URL_RE.search(text):
            out[key] = text
    if not out:
        logger.info("company news company_facts: %s profile names no usable sector or industry", sym)
    return out or None

__all__ = [
    "MarketingNewsUnavailable", "Candidates", "NewsDeps", "candidates", "fetch_logo", "company_facts", "COLLECTORS",
    "remember_template_refusal", "clear_memo", "MIN_STEP_SECONDS", "INSIDER_RAW_TTL_SECONDS", "PROFILE_TTL_SECONDS",
    "BUILT_13F_TTL_SECONDS", "LOGO_TTL_SECONDS", "LIST_TTL_SECONDS", "INSIDER_PAGE_SIZE",
    "INSIDER_MAX_PAGES", "INSIDER_PROFILE_CANDIDATES", "INSIDER_MAX_CAP_SHARE", "MAX_LIVE_13F_BUILDS",
    "THIRTEEN_F_MAX_UNAVAILABLE", "THIRTEEN_F_MAX_PROFILED", "THIRTEEN_F_MIN_CHANGE",
    "THIRTEEN_F_MATERIAL_MIN_USD", "THIRTEEN_F_MATERIAL_SHARE", "THIRTEEN_F_MAX_SPLIT_CHECKS",
    "MONEY_MAP_MAX_ATTEMPTS", "MONEY_MAP_MAX_CONTENT_REJECTIONS", "MONEY_MAP_MAX_LOOKUPS",
    "MONEY_MAP_MAX_OTHER_SHARE",
    "INSIDER_AMENDMENT_MAX_SYMBOLS", "INSIDER_AMENDMENT_PAGE_SIZE", "INSIDER_AMENDMENT_MAX_PAGES",
    "CEO_TITLE_WORDS", "CFO_TITLE_WORDS", "DIRECTOR_TITLE_WORDS",
    "MONEY_MAP_STALE_DAYS", "MONEY_MAP_NEXT_YEAR_DUE_DAYS", "MONEY_MAP_FY_END_TTL_SECONDS", "LOGO_URL_TEMPLATE",
    "MONEY_MAP_REFUSAL_RECHECK_DAYS",
    "CONGRESS_WALK_LIMITS", "CONGRESS_COVERAGE_MARGIN_DAYS", "CONGRESS_DUE_DAYS",
    "CONGRESS_PROFILE_CANDIDATES", "CONGRESS_TTL_SECONDS", "CONGRESS_IDENTITY_FIELDS",
    "EARNINGS_WINDOW_DAYS", "EARNINGS_TRUNCATION_ROWS", "EARNINGS_DAY_TTL_SECONDS",
    "EARNINGS_MAX_PROFILES", "EARNINGS_MAX_STATEMENT_READS", "EARNINGS_STATEMENT_ROWS",
    "EARNINGS_MAX_GAP_MULTIPLE", "EARNINGS_CLASS_LETTERS", "STAKES_READ_LIMIT",
    "STAKES_PROFILE_CANDIDATES", "STAKE_TEMPLATE_REFUSAL_TTL_SECONDS", "THEME_MAX_EVALUATED", "THEME_SEGMENT_LOOKUPS",
    "THEME_LOOKUP_CONCURRENCY", "THEME_FACT_MAX_AGE_YEARS", "THEME_SEGMENT_SUM_TOLERANCE",
    # phase 4 (2026-11-20): every member read, in market-value order; the gross stack walk-back
    "THEME_SEGMENT_CONCURRENCY", "THEME_SEGMENT_YEARS_CONCURRENCY", "THEME_FACT_TILES",
    # news-v23 phase 3: the weekly theme post's rotation and angles
    "THEME_MAX_ANGLE_ATTEMPTS", "THEME_BREAKDOWN_TTL_SECONDS", "THEME_MARKET_VALUE_TTL_SECONDS", "THEME_HISTORY_MAX",
    "THEME_ANGLE_COLLECTORS", "THEME_STATEMENT_LOOKUPS", "THEME_STATEMENT_ROWS", "THEME_STATEMENT_TTL_SECONDS",
    "THEME_INSIDER_LOOKUPS", "THEME_INSIDER_PAGE_SIZE", "THEME_INSIDER_MAX_PAGES", "THEME_INSIDER_CONCURRENCY",
    "SPOTLIGHT_ROLES", "SPOTLIGHT_PEOPLE_PER_ISSUER", "SPOTLIGHT_AMENDMENT_MAX_ISSUERS", "SPOTLIGHT_AMENDMENT_MAX_PEOPLE",
    "THIRTEEN_F_SPOTLIGHT_SPARE_FILINGS", "COMPANY_FACTS_TIMEOUT_SECONDS", "SPOTLIGHT_RECENT_MAX",
]
