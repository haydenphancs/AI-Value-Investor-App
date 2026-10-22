"""
Company Weekly (Drop 2) — the PURE rules of the company-news series (contract D4).

Everything here is deterministic and FMP-free (`PURE_MODULES` in
`tests/test_marketing_import_boundary.py`): the ONE adapter (`company_news_adapter.py`, the §1
FMP exemption) does the I/O and hands its findings to these types and gates; the templates
(`news_templates.py`) import the same types without ever loading the FMP client.

What lives here, and why each piece is in the pure layer:

* **Records** — one frozen, slotted dataclass per series (tuples, never lists). Each validates
  itself in `__post_init__` (types, finite non-bool numbers, date order, string caps and
  `compliance.clean(s) == s`), so a record that exists is a record a template may render. Float
  fields are normalised to `float`, so a record and its JSON round trip compare equal.
  NO price, market cap, profile CEO, image, URL or member identity has a field: the leak test
  (`tests/test_marketing_company_news_leaks.py`) pins the exact field list per type.
* **Fact sheet** — `fact_sheet()` / `record_to_dict()` / `record_from_dict()` /
  `record_from_fact_sheet()`. Strict both ways (an unknown or missing key, or `schema != 1`,
  raises ValueError) and independent of key order, because JSONB reorders keys on read-back and
  re-serialises numbers (100000.0 → 100000).
* **Ledger keys and source labels** — pinned verbatim; a source label never names the data
  vendor.
* **Shared gates** — `canonical_symbol` / `symbol_problem`, `display_company_name`,
  `company_from_profile` (a str result is the rejection reason), the fail-closed person-name
  renderer (`render_person_name`, the intersection of the two designs' rule sets),
  `corroborates`, the Congress block-list (`congress_names`, `is_congress_name`,
  `congress_name_hits`), `filer_name_problem` and the exact Money Map segment-label overrides
  (`segment_display_name`, `SEGMENT_DISPLAY_OVERRIDES`, and — the review of 2026-10-16 — the
  company-scoped `SEGMENT_SYMBOL_OVERRIDES`: McDonald's retired "High-Growth Markets" label is its
  International Operated Markets, for McDonald's only).
* **The cover's word cap** (review of 2026-10-16) — `COMPANY_NAME_WORD_MAX_CHARS` /
  `company_name_word_too_long`: no word of a company's display name is longer than the video cover can
  draw whole; refused at the profile gate and by every `CompanyRef`, never by the matching helpers.
* **Stake-source surnames** (review of 2026-10-16) — `STAKE_SOURCE_PERSON_SURNAMES` /
  `stake_person_surname_hits`: the people a stake's hand-kept source is likeliest to cite, matched on top of
  the template's person check.
* **What a stake post can draw** (the round-3 review of 2026-10-20, RR3-STAKES-TEMPLATE-WALL) —
  `STAKE_SOURCE_DRAW_MAX_CHARS`, `STAKE_TEXT_WORD_MAX_CHARS`, `STAKE_PAIR_NAME_WORD_MAX_CHARS` and `longest_word`:
  copies of the template's bounds (pinned equal to them by a test), so the adapter refuses at the read a stake
  text the template cannot draw instead of offering it every day.
* **Earnings gates** (Batch 1, 2026-10-15) — `earnings_revenue_pair` (one plausible revenue pair or the
  whole report is refused; since news-v21 a data-quality and quarter check only — no post prints revenue, the
  feed never saying which basis its revenue estimate is on), `clear_surprise` (an EPS gap of at least
  `EARNINGS_CLEAR_SURPRISE`, exact at the boundary, on the feed's figures AND on the two-place figures the post
  prints), `statement_currency` (the reporting currency of the newest statement period), `earnings_period` (the
  quarter the figures are for, from the quarterly statements — a row that is not quarter-shaped,
  `EARNINGS_QUARTER_SPAN_DAYS`, places nothing), `earnings_sector_problem` (a Real Estate / REIT issuer, whose calendar EPS can be FFO-based) and
  `earnings_basis_mixed` (an actual and an estimate on two different bases, judged against the statement's
  diluted EPS — the review of 2026-10-16, live preview F1: Prologis' $1.60 Core-FFO-scale actual beside a
  $0.78 GAAP-scale estimate).
* **The members-elect window** (owner decision 4, 2026-10-15) — `general_election_day`,
  `members_elect_window` and `members_elect_for_run`: from an even year's Election Day through January 3,
  Form 4 posts are role-only — judged for every day a run's posts can go out (the run date and the
  `MEMBERS_ELECT_PUBLISH_DAYS` after it), never the run date alone.
* **NASDAQ preferred lines** — `nasdaq_preferred_symbol` (a 5-letter symbol ending P/O/N/M, copied from
  `stock_search_service`): never a common stock, so the "insider", "earnings" and "common" purposes refuse it.
* **Money Map labels and regions** (Batch 1, 2026-10-15) — `segment_fold_reason` (a company code of 4
  capitals or fewer outside `SEGMENT_ACRONYMS_OK` and not a region name, or a generic label from
  `GENERIC_SEGMENT_LABELS`, is folded into the unnamed rest by the adapter: it names no part of the company a
  reader knows), and the strict region list (`REGION_NAMES`, `region_key`, `is_region_name`; `REGION_ARTICLE`
  / `takes_region_article` for "the United States", "the U.S.", "the rest of the world" in running text) that
  alone may make a breakdown read "by region".
* **Descriptive stake names** (Batch 1, 2026-10-15) — `DESCRIPTIVE_NAME_HEADS` / `takes_name_article`: a
  stake named by what it is rather than by a company ("Louisiana data center venture") takes "the" in
  running text, never on a drawn label.
* **13F filer tables** (Batch 1, 2026-10-15) — `THIRTEEN_F_SUCCESSORS` (a registry CIK whose holdings are
  now filed under another EDGAR CIK — Pershing Square → 0002026053, named in `THIRTEEN_F_FILERS`) and
  `THIRTEEN_F_LEAD_FILERS` (read before the club and the rest of the registry: Berkshire); since the review of
  2026-10-16 `THIRTEEN_F_SUCCESSOR_SINCE` / `successor_transition`: a successor's hand-over quarter (diffed
  against its own smaller pre-hand-over book) is never compared.
* **Reason codes** — `UNAVAILABLE_REASONS` (raise), `SKIP_REASONS` (an honest "nothing
  qualified"), `REJECTION_REASONS` (per-candidate counters, `already_posted` included).
* **The Money Map seed** (owner decision 2, 2026-10-09) — `MONEY_MAP_SEED`, a reviewable list of
  well-known US companies (no banks, insurers, REITs or real estate) that goes FIRST; the adapter
  appends the Trillion Club and Emerging Frontiers members through `merge_money_map_pool`.

Members of Congress are never named (rules/marketing.md §1, owner decision 2026-10-09): the
block-list is the whale registry's politicians plus `data/congress_roster.json` (CC0
unitedstates/congress-legislators, names only). That file is now part of the repo, so a missing
or unreadable roster logs ERROR and switches every person slot to role-only
(`person_names_allowed()` is False) — fail closed, never "no one is a member". Review round 9: a
member also matches by SURNAME plus any form of a given name (a legal first name, a nickname
twin, a prefix, an initial — EDGAR files "GOTTHEIMER JOSHUA S" for "Josh Gottheimer"). Review
round 11 (main-session decision 2026-10-10): freshness is about CONTENT — the roster records the
Congress its current members serve in (``congress_start``, the odd-year January 3 that began it),
and a roster that does not hold the SITTING Congress on the run date, is undated, or is older than
`ROSTER_MAX_AGE_DAYS` (`roster_fresh_for` False) PAUSES both Form 4 series: role-only would still
point at a member the list cannot know.
"""

from __future__ import annotations

import json
import logging
import math
import re
import typing
import unicodedata
import uuid
from dataclasses import MISSING, dataclass, field, fields, is_dataclass
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal, localcontext
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, FrozenSet, Iterable, List, Literal, Mapping, Optional, Sequence, Tuple, Union

from app.schemas.trillion_club import STAKE_KINDS, VALUE_BASES
from app.services.marketing import compliance as _compliance
# `x_link_tokens` is THE definition of "X turns this into a link" (the publisher's URL guard uses
# it): a company or filer name that reads as a domain ("C3.ai") is refused where names are made.
from app.services.marketing.post_copy import x_link_tokens as _x_link_tokens
from app.services.trillion_club import copy_rules as _copy_rules

logger = logging.getLogger(__name__)

# ── series, classes, versions ─────────────────────────────────────────────────

#: The fact-sheet / record-dict schema. A stored sheet with any other value is refused.
FACT_SHEET_SCHEMA = 1

#: Every company-news series id, in `selection.SERIES` order. 2b ids are listed so 2b only adds
#: collectors and templates: the records below already exist for them.
NEWS_SERIES: Tuple[str, ...] = (
    "ceo_buys", "insider_buys", "thirteen_f", "congress_count",
    "company_stakes", "earnings", "money_map", "theme_explainer",
)
#: Content class per series (C = reportorial filings, F = company fundamentals). Pinned equal to
#: `selection.SERIES` by `tests/test_marketing_company_news_rules.py`.
SERIES_CLASS: Dict[str, str] = {
    "ceo_buys": "C", "insider_buys": "C", "thirteen_f": "C", "congress_count": "C",
    "company_stakes": "F", "earnings": "F", "money_map": "F", "theme_explainer": "F",
}

#: Every ledger key (`marketing_scripts.source_ref`) of a news post starts with this. Learn pool
#: refs are `<kind>:<slug>`, so the prefix never collides with a lesson.
LEDGER_PREFIX = "news:"

# ── thresholds shared by the adapter, the records and the templates ───────────

#: Major US exchanges. Pinned equal to `trillion_club.builder.US_EXCHANGES` (a test; the builder
#: loads the FMP client, so it is not imported here).
MAJOR_US_EXCHANGES: FrozenSet[str] = frozenset({"NASDAQ", "NYSE", "AMEX"})
INSIDER_MIN_MARKET_CAP = 250_000_000.0
#: Earnings' floor (an owner question; the default chosen by the contract).
EARNINGS_MIN_MARKET_CAP = 2_000_000_000.0
#: "listing" = any major-exchange listing (13F moves, Money Map, a stake's investor, theme
#: members — an ADR included). "common" (Drop 2b: a Congress Count company, a stake's investee)
#: = a US common stock: "listing" plus no ADR, USD, and no non-common name — but no cap floor.
#: "insider" (a Form 4 issuer) refuses a non-common name too since Batch 1 (2026-10-15): FMP names
#: AgomAb's line "… American Depositary Shares" while its `isAdr` flag is False, and a post saying
#: "disclosed buying … of the company's stock" must be about the company's common stock.
PROFILE_PURPOSES: Tuple[str, ...] = ("insider", "earnings", "listing", "common")

#: Form 4 filing window: `[run_date - 7, run_date - 1]`.
INSIDER_WINDOW_DAYS = 7
#: A row's floor, after implausible lines are dropped (signals parity: $100K).
INSIDER_MIN_AMOUNT_USD = 100_000.0
#: The garbage bound of signals_service (`_CEO_MAX_ROW_DOLLARS`); a real ~$1B buy exists.
INSIDER_MAX_AMOUNT_USD = 5_000_000_000.0
INSIDER_MAX_ROWS = 5
#: A trade may predate its filing by at most this (signals' `_CEO_MAX_FILING_LAG_DAYS`).
INSIDER_MAX_FILING_LAG_DAYS = 30

#: 13F moves in their canonical order (`counts` keys appear in this order).
THIRTEEN_F_MOVES: Tuple[str, ...] = ("newly_reported", "increased", "decreased", "no_longer_reported")
THIRTEEN_F_MAX_MOVES = 8

CONGRESS_MIN_MEMBERS = 2

EPS_MIN_ABS_ESTIMATE = 0.10
#: Owner-delegated decision (2026-10-19, the Batch 1 re-review): a calendar row whose |EPS actual| or |EPS
#: estimate| is ABOVE this is no result a post can show — a feed error (a unit slip, a junk row), refused WHOLE
#: (``eps_implausible``: the adapter's row gate, the `EarningsReport` record and the template alike). Berkshire
#: Hathaway's Class A, the largest per-share figure listed in the US, reports about $7,000-$20,000 a share, far
#: inside it. Before it, an absurd but self-consistent pair (both figures ~1e20, within the gap gate) composed with
#: a huge printed figure, and from 1e26 up failed only at compose.
EPS_MAX_ABS = 1_000_000.0
#: revenue actual / estimate must sit inside this band, else the WHOLE report is refused
#: (`revenue_implausible`, Batch 1 2026-10-15). Before, the revenue row was omitted and the EPS kept —
#: and a calendar row holding one MONTH (Progressive's 10-14 row: $7.16B against a $22.6B quarter
#: estimate) published its monthly EPS as the quarter's ("$1.08 vs an estimate of $4.35"). The revenue
#: pair is the only check that the row is the quarter the estimate is for, so no pair, no post.
REVENUE_RATIO_BAND: Tuple[float, float] = (0.5, 1.5)

# ── earnings: the newsworthiness and period gates (Batch 1, 2026-10-15) ───────

#: A report whose |actual − estimate| / |estimate| is BELOW this is no clear surprise (`surprise_unclear`):
#: "EPS of $1.01 vs an estimate of $1.00" is not news. Computed on the figures as written (`clear_surprise`),
#: so a 5.0% gap is kept and a 4.99% gap refused, whatever the float behind them — AND on the figures as the
#: post PRINTS them (`EPS_PRINT_PLACES` places, half-up, as `news_templates.eps_image` writes them): review
#: of 2026-10-16 (ER-1) — 0.095 vs 0.10 is a 5.0% gap in the feed and printed "$0.10 vs $0.10", a "clear
#: surprise" a reader cannot see. A surprise must be clear on both.
EARNINGS_CLEAR_SURPRISE = 0.05
#: The places a post prints a per-share figure with ("$4.86", "-$0.05"): `news_templates.eps_image`'s
#: rounding (half-up on the magnitude), pinned equal by tests/test_marketing_company_news_rules.py.
EPS_PRINT_PLACES = 2
#: The Decimal precision the EPS gates compute in (`clear_surprise`, `printed_eps`): every finite float is exact in
#: it (at most 309 integer digits, plus the printed places and the threshold's), so a gate never rounds and never
#: raises. The default context's 28 digits cannot quantize a figure of 1e26 or more (`decimal.InvalidOperation`),
#: and the adapter turns an exception into ``internal_error`` for the WHOLE series: one absurd calendar row would
#: have cost the earnings day (review of 2026-10-16, a regression of the ER-1 printed-figure gate, caught in its
#: hardening pass).
EPS_DECIMAL_PRECISION = 400
#: The period check (`earnings_period`): a quarterly statement row whose period END falls in
#: ``[report − EARNINGS_PERIOD_WINDOW_DAYS, report)`` can be the reported quarter.
EARNINGS_PERIOD_WINDOW_DAYS = 120
#: ... but only the NEWEST such row is, and only while it ended at most this many days before the report.
#: A row older than that may be the quarter BEFORE the reported one — the statement has not caught up yet
#: (a bank reports mid-month and files its 10-Q weeks later) — and a company whose revenue is flat
#: quarter to quarter would match it within the tolerance, so the post would date the figures to the
#: wrong quarter. A 10-K is due 60-90 days after a year end and a 10-Q 40-45 days after a quarter end
#: (results are released by then), while the next fiscal quarter (12 weeks at the shortest) ends 84+ days
#: after a quarter end and is reported a week or more later — so 90 days keeps the timely reports and
#: refuses the stale row (`period_unverified`, fail closed; a late filer past 90 days loses its post).
EARNINGS_PERIOD_MAX_LAG_DAYS = 90
#: Review of 2026-10-16 (ER-4): the row the period check keeps must be a QUARTER. Its period end must sit this
#: many days (inclusive) after the company's next-older statement period end — 11 to 17 weeks: a 12-week retail
#: quarter is 84 days, a 13-week one 91, a calendar quarter 90-92, a 16-week first or fourth quarter (Kroger,
#: Costco) 112 and a 17-week one in a 53-week year 119 — and the report must come LESS than that same span after
#: it (else the NEXT quarter of that length had already ended by the report day: the row may be the quarter
#: before the reported one — a 12-week quarter ending Jun 13, reported Sep 10, 89 days on, is inside
#: `EARNINGS_PERIOD_MAX_LAG_DAYS` yet the Sep 5 quarter was the one reported). A half-year row (FMP files a
#: semiannual reporter's halves as its "quarters": ~181 days apart), a single row or an odd span →
#: ``period_unverified``: the post would say "the quarter that ended …" about six months of figures.
EARNINGS_QUARTER_SPAN_DAYS: Tuple[int, int] = (77, 119)
#: The reported quarter's statement revenue must be within this share of the calendar's revenueActual
#: (Delta's 2026-09-30 statement: $20.186B against the calendar's $20.2B — 0.07%).
#:
#: Accepted coverage loss (live preview of 2026-10-16, F4 — never loosen this for everyone): US banks and
#: brokers are mostly refused here. Their newest statement row is GROSS revenue (interest income included:
#: Goldman Sachs $37.70B against the calendar's net $18.68B, 2.02x; JPM 1.52x, BAC 1.59x, MS 1.64x, WFC 1.52x,
#: SCHW 1.14x, IBKR 1.67x, USB 1.41x, PNC 1.43x, RF 1.24x), and the calendar's revenue is NET, so the row does
#: not match and the report is refused `period_mismatch` — fail closed, no false post, and "the largest company
#: with a clear surprise" is almost never a bank (Citi passes only while FMP's newest Citi row happens to be on
#: the net basis). An insurer can miss the 3% too (Progressive: $23.18B vs $22.30B, 3.9%). A net-revenue basis
#: for banks or a wider tolerance for insurers is an owner decision, not a default.
EARNINGS_PERIOD_REVENUE_TOLERANCE = 0.03
#: Contingency, OFF: when True, a report whose newest statement row is the quarter BEFORE (the statement
#: has not caught up) is kept WITHOUT a period (``period_end`` None, the post says "reported on <date>")
#: while the calendar's revenue is within `EARNINGS_SCALE_BAND` of that quarter's — a scale check that
#: still refuses a month or a half-year. Never a period from that row.
EARNINGS_ALLOW_SCALE_CHECK = False
EARNINGS_SCALE_BAND: Tuple[float, float] = (0.75, 1.33)
#: Review of 2026-10-16 (live preview F1): FMP's earnings calendar mixes BASES. For Prologis (2026-10-15) the
#: actual was $1.60 — a Core-FFO-scale figure — against a GAAP-scale estimate of $0.777, while the quarter's
#: GAAP diluted EPS was $0.86; the post would have said "Prologis reported EPS of $1.60 vs an analyst estimate
#: of $0.78" (a 106% gap that exists on neither basis). Two gates, each refusing the WHOLE report:
#: * a Real Estate issuer — or one whose industry starts "REIT" — is refused before its statement is read
#:   (`earnings_sector_problem` → ``eps_basis_reit``): a REIT's calendar EPS can be FFO on either side. Its
#:   sector must be readable: an unknown sector cannot be shown not to be a REIT (fail closed, as the Money Map
#:   sector gate);
#: * any other issuer is refused when its calendar actual sits outside `EARNINGS_BASIS_ACTUAL_BAND` × the
#:   quarter's statement diluted EPS while the estimate sits within ± `EARNINGS_BASIS_ESTIMATE_TOLERANCE` of it
#:   (`earnings_basis_mixed` → ``eps_basis_mixed``): the estimate is on the statement's scale and the actual is
#:   not, so the two figures the post sets side by side are on two bases. Narrow on purpose — it is NOT a GAAP
#:   check of the actual (the plan's "no GAAP EPS check" stands: an adjusted actual far from GAAP beside an
#:   adjusted estimate is a true pair — BlackRock's $15.93 vs $14.32 with GAAP $10.57 is kept), it never
#:   refuses when the statement EPS is missing or not positive, and it does not catch the reverse mix (a
#:   GAAP-scale actual beside an adjusted estimate).
EARNINGS_EXCLUDED_SECTORS: FrozenSet[str] = frozenset({"Real Estate"})
EARNINGS_EXCLUDED_INDUSTRY_PREFIX = "REIT"
EARNINGS_BASIS_ACTUAL_BAND: Tuple[float, float] = (0.67, 1.5)
EARNINGS_BASIS_ESTIMATE_TOLERANCE = 0.25

MONEY_MAP_SEGMENTS: Tuple[int, int] = (2, 5)
#: |Σ segments + other + eliminations − revenue| ≤ this share of revenue.
MONEY_MAP_SUM_TOLERANCE = 0.005
MONEY_MAP_EXCLUDED_SECTORS: FrozenSet[str] = frozenset({"Financial Services", "Real Estate"})

THEME_MEMBERS: Tuple[int, int] = (6, 24)
#: `ThemeExplainer.theme_size` above this is junk, not a theme (the record refuses it).
THEME_SIZE_MAX = 200
#: A theme is explained only when at least this many of its members carry a segment fact.
THEME_MIN_FACTS = 4
#: A theme's ticker list must have been refreshed within this many days of the run.
THEME_STALE_DAYS = 70

#: A stake whose row was created on/after this date is "new" (2b company_stakes).
COMPANY_WEEKLY_LAUNCH = date(2026, 11, 16)
#: Main-session decision (1), 2026-10-09: company_stakes MAY post CATALOGUE stakes (rows created
#: before `COMPANY_WEEKLY_LAUNCH`) — published, source-valid, a named investee and a disclosed
#: dollar figure — newest ``as_of`` first, each stake once (its ledger key). False → new rows only.
STAKES_INCLUDE_CATALOGUE = True
#: A stake whose ``verified_on`` is older than this is not re-published (the club's own rule).
STAKE_STALE_DAYS = 120
#: A stake whose ``as_of`` is older than this is history, not news.
STAKE_MAX_AGE_DAYS = 3 * 365
#: A disclosed stake value above this is a unit error (thousands typed as dollars …), not a stake.
STAKE_MAX_VALUE_USD = 1e12

COMPANY_NAME_CHARS: Tuple[int, int] = (2, 32)
#: The longest WORD a company's display name may hold (the review of 2026-10-16): every series draws the name
#: on its video cover's headline, whose column holds exactly this many "W"s (the widest glyph) at the step
#: before the floor — the room every cover must keep (measured with the worker's own layout code: 20 there,
#: 21 at the floor; the image header and title hold 23-24). A longer word was a CardOverflow at render — a
#: skipped DAY, not one lost post: the Form 4 and Money Map templates checked no word length (13F and the 2b
#: series refuse past `news_templates.NAME_WORD_MAX_CHARS` / `F13_MAX_WORD_CHARS`, the same 20 — pinned equal
#: by a test). Refused at the profile gate (`company_from_profile` → ``company_name_unusable``) and by every
#: record (`CompanyRef`), so a stored fact sheet is held to it too. Deliberately NOT part of
#: `company_name_problem` / `display_company_name`: those also key the 13F share-class tie and the earnings
#: issuer dedupe, which must keep matching a name however it is drawn. Real names stay below it (the longest word
#: seen live is about 18 characters: "Biopharmaceuticals") — but for ONE: the re-review of 2026-10-19 (NAME-CAP-1)
#: screened the 3,686 NYSE / NASDAQ / AMEX companies of $250M or more and found only "Archer-Daniels-Midland"
#: (22, one hyphenated word), shown as its own brand "ADM" (`DISPLAY_OVERRIDES`). Characters, not drawn width: a
#: "W"-count is the bound the cover proves.
COMPANY_NAME_WORD_MAX_CHARS = 20
PERSON_NAME_MAX = 40
FILER_NAME_CHARS: Tuple[int, int] = (2, 60)

# ── reason codes (adapter design §6, with the contract's renames) ─────────────

#: `MarketingNewsUnavailable(series, reason)`: an upstream failure — never a "nothing qualified".
UNAVAILABLE_REASONS: FrozenSet[str] = frozenset({
    "insider_feed_unavailable", "insider_feed_empty", "earnings_calendar_unavailable",
    "earnings_calendar_truncated", "profiles_unavailable", "club_unavailable", "stakes_truncated",
    "thirteen_f_unavailable", "congress_feed_unavailable", "congress_window_uncovered",
    "congress_feed_unordered", "revenue_unavailable", "themes_unavailable", "budget_exhausted",
    "internal_error",
    # review of 2026-10-16 (ER-2): EVERY quarterly-statement read of an earnings call failed (FMP down, rate
    # limited, an answer no row could be read from) and no report was held — the source is down, which an
    # honest "nothing qualified" would hide (as `profiles_unavailable` does for the profiles)
    "earnings_statements_unavailable",
})
#: `Candidates.skip_reason`: an honest "nothing qualified". `already_posted` is NOT one: excluded
#: refs are dropped silently and counted in `rejections`.
SKIP_REASONS: FrozenSet[str] = frozenset({
    "ceo_none_qualified", "insider_none_qualified", "earnings_none_qualified",
    # `stakes_none_qualified` (Drop 2b): no stake passed the gates (`stakes_none_unposted`: every
    # one that did is already posted; `stakes_none_new`: catalogue stakes are switched off)
    "stakes_feature_off", "stakes_none_new", "stakes_none_unposted", "stakes_none_qualified",
    "thirteen_f_off_season", "thirteen_f_none_qualified",
    "congress_not_due", "congress_none_qualified",
    "money_map_none_qualified", "theme_none_qualified",
})
#: Per-candidate counters (`Candidates.rejections`, stored in the fact sheet).
REJECTION_REASONS: FrozenSet[str] = frozenset({
    # ledger and record
    "already_posted", "record_invalid",
    # profile / company (`non_common_listing`, Drop 2b: a warrant / unit / right / preferred /
    # notes line by its profile name — purposes "earnings" and "common"; "insider" too since Batch 1:
    # a depositary-share line FMP does not flag `isAdr`)
    "profile_missing", "not_major_exchange", "etf_or_fund", "adr", "not_usd", "inactive",
    "below_cap_floor", "company_name_unusable", "company_name_banned_word", "non_common_listing",
    # symbol
    "symbol_grammar", "warrant_unit_right",
    # insider
    "price_reference_missing", "price_implausible", "over_cap_share", "below_dollar_floor",
    "ambiguous_ceo", "congress_name",
    # insider (review round 1): a fund / LLC reporting as a director is not a person; a line
    # filed for another issuer (FMP keys the symbol on the EDGAR folder) or a profile without a
    # CIK to check it against
    "reporter_is_entity", "issuer_mismatch", "issuer_unverified",
    # insider (review rounds 7-8, replacing rounds 1-6's amendment codes): THE amendment rule —
    # ANY amendment row (any form ending "/A", any code, security or line, any reporter) filed
    # since the window opened on the ISSUER (its symbol, its issuer CIK, every share class)
    # refuses every row of that issuer for the week; the per-ISSUER all-code read of that rule
    # (by the profile's issuer CIK, every share class at once — review round 9) failing, not
    # possible (no profile CIK) or not holding the person's newest-day lines and every walk row
    # of the issuer filed since (fail closed for the row, never the week); a CEO / CFO any of
    # whose role titles is not a sitting officer's (an allow-list of title words: "Former CEO",
    # "CEO until 12/31", "CEO Nominee", a filing as a non-officer …) — the free `other:` text
    # too when it names the role, and a director's `other:` text when it names the seat
    "amended_filing", "amendment_check_failed", "role_uncertain",
    # insider + 13F: a second share class of one issuer (GOOG/GOOGL, BF-A/BF-B) — the issuer,
    # not the symbol, is the unit
    "share_class_overlap",
    # earnings. `revenue_dropped` has no producer since Batch 1 (2026-10-15) — a report without a
    # plausible revenue pair is refused whole (`revenue_implausible`), never kept without its revenue —
    # and stays here (append-only) so the set still names what older fact sheets carry
    "eps_estimate_too_small", "eps_digit_shift", "eps_gap_implausible", "revenue_dropped",
    # stakes (`investor_unlisted`, Drop 2b: the club company has no US listing a CompanyRef can
    # be made from — Samsung, Saudi Aramco)
    "stake_invalid", "stake_stale", "stake_too_old", "stake_no_figure", "stake_aggregate",
    "investor_unlisted",
    # 13F filers. `filer_named_after_person` is RESERVED: owner decision 5 (2026-10-09) allows a
    # filer entity named after a person, so `filer_name_problem` never returns it.
    "filer_entity_unknown", "filer_name_unusable", "filer_named_after_person", "filer_not_filed",
    "filer_unavailable", "book_too_large", "degraded_build", "non_comparable",
    # 13F moves (`move_immaterial`, review round 7: a newly reported / no-longer-reported move
    # below max($5M, 0.05% of the filer's total) — an exit valued on the previous book)
    "move_value_exceeds_total", "move_too_small", "move_unknown_listing", "move_not_routable",
    "move_immaterial",
    # congress (Drop 2b: `member_ambiguous` — the identity fields cannot tell whether two rows
    # are one member or two, so the count could be wrong either way; `unmapped_purchase` — an
    # in-month stock purchase with no usable symbol names the company, so the count could be low)
    "exchange_type", "option_asset", "member_unidentifiable", "member_ambiguous",
    "unmapped_purchase",
    # `asset_uncertain` (Drop 2b): a purchase of the symbol whose asset type is neither "stock"
    # nor clearly something else, or a "stock" row describing another instrument — the count of
    # STOCK purchasers could be wrong
    "asset_uncertain",
    # Money Map (`money_map_mostly_other`, review round 7: "Other" above the largest named
    # segment or 30% of revenue). `non_usd_reporter` is also the earnings refusal (review round
    # 9): the latest quarterly statement is not reported in USD (a statement that could not be read
    # at all is `statement_unavailable` since the review of 2026-10-16)
    "money_map_degraded", "money_map_excluded", "segments_thin", "sector_excluded",
    "non_usd_reporter", "revenue_mismatch", "net_income_mismatch", "money_map_stale",
    "money_map_inconsistent", "money_map_mostly_other",
    # themes
    "theme_stale", "theme_title_unusable", "theme_too_large", "theme_members_thin",
    "theme_facts_thin",
    # review round 9: `partial_person` — a Form 4 person some of whose in-window purchases of the
    # issuer the row would not hold (a line dropped one by one, skipped by the extractor, under
    # another class or no symbol): refused, never re-summed; `move_has_options` — a 13F move whose
    # issuer has option / note rows on the current or previous book (a share-only "no longer
    # reported" / "newly reported" would be false); `filer_is_person` — a 13F filed by a natural
    # person (the subject is the FILING entity and a person never heads a post); `count_uncertain`
    # — a Congress count beside an in-month, ticker-less purchase that names the company in a
    # spelling the whole-word check misses ("JP Morgan Chase", "ExxonMobil")
    "partial_person", "move_has_options", "filer_is_person", "count_uncertain",
    # review round 10: `same_person` — a Form 4 row whose person (reporting CIK or name) already
    # has a larger row at ANOTHER issuer this week ("At least 2 CEOs" about one person running two
    # affiliated funds): the week counts people, so one person is one row
    "same_person",
    # earnings (Batch 1, 2026-10-15), each refusing the WHOLE report: `revenue_implausible` — the
    # revenue pair is missing, disagrees between the company's rows, is <= 0 or sits outside
    # `REVENUE_RATIO_BAND`; `surprise_unclear` — |actual − estimate| under `EARNINGS_CLEAR_SURPRISE` of the
    # estimate; `period_mismatch` — the reported quarter's statement revenue is not the calendar's;
    # `period_unverified` — no statement row can be the reported quarter (none in the window, a stale or
    # unreadable one, a period ending on or after the report) — see `earnings_period`
    "revenue_implausible", "surprise_unclear", "period_mismatch", "period_unverified",
    # Money Map (Batch 1, 2026-10-15): `money_map_next_year_due` — the fiscal year ended at least
    # `company_news_adapter.MONEY_MAP_NEXT_YEAR_DUE_DAYS` (364, 52 weeks) before the run, so the NEXT year
    # has ended and its annual report is due; a content refusal, never an upstream attempt
    "money_map_next_year_due",
    # 13F (Batch 1, 2026-10-15): `split_unchecked` — an increased / decreased move of a filing whose
    # split lookups the shared cap trimmed (`BuiltFiling.split_unchecked`): a held-through split reads
    # as "more shares", so the filing's more / fewer moves and counts are not shown; newly reported and
    # exited moves stay (the split check only ever restates a holding on BOTH books, so the cap changes
    # nothing about them)
    "split_unchecked",
    # earnings (review of 2026-10-16), each refusing the WHOLE report: `statement_unavailable` — its
    # quarterly-statement read FAILED (an outage, a rate limit, an answer with no readable row or currency):
    # counted apart from `non_usd_reporter` (an answered read in another currency), so a run whose every read
    # failed is told apart and raises `earnings_statements_unavailable`; `eps_basis_reit` — a Real Estate / REIT
    # issuer (or one whose sector is unknown), whose calendar EPS can be FFO-based (`earnings_sector_problem`);
    # `eps_basis_mixed` — the calendar's actual and estimate on two different bases, judged against the
    # quarter's statement diluted EPS (`earnings_basis_mixed`: Prologis' $1.60 Core-FFO-scale actual beside a
    # $0.78 GAAP-scale estimate)
    "statement_unavailable", "eps_basis_reit", "eps_basis_mixed",
    # 13F (review of 2026-10-16), each an increased / decreased move dropped with both counts left out (newly
    # reported and exited moves stay): `split_in_window` — the adapter's own re-check of EVERY more / fewer row
    # found a stock split the build did not restate, an adjustment the classifier could not name, or a lookup
    # that failed, in the quarter (the shared suspect filter never looks up a split whose per-share price moved
    # under 30%: a 5:4 split at a flat price, a 3:2 split after a 15% rally — published as "more shares").
    # `split_unchecked` (above) now also covers a row that could not be re-checked at all (no symbol, more rows
    # than the check's cap, a club filer's stored change rows unreadable or not the detail's), and
    # `degraded_build` a club filer whose STORED build is degraded (its row keeps no reason: a failed split
    # lookup is possible). `successor_transition` — a successor filer's hand-over quarter
    # (`THIRTEEN_F_SUCCESSOR_SINCE`): its 13F would be diffed against its own smaller pre-hand-over book, so
    # continuing positions read as "newly reported"; the whole filing is refused before any FMP call
    "split_in_window", "successor_transition",
    # earnings (owner-delegated decision, 2026-10-19): `eps_implausible` — the row's |EPS actual| or |EPS
    # estimate| is above `EPS_MAX_ABS` ($1,000,000 a share): a feed error, never a result; the WHOLE report is
    # refused
    "eps_implausible",
    # stakes (the re-review of 2026-10-19, ST-1-R): `stake_source_unverified` — the stake's source title carries a
    # capitalised word that is neither a club or catalogue company's nor on `STAKE_SOURCE_WORDS`
    # (`stake_text_unknown_words`): it may be a person's name, which is never drawn; the stake is refused
    "stake_source_unverified",
    # Money Map (the round-2 review of 2026-10-20, RR2-MM-TEMPLATE-WALL), each REMEMBERED for the symbol's fiscal
    # year by the adapter's refusal memo (B3PD-1), so the walk tries that company after every candidate that can
    # publish: `money_map_loss_above_revenue` — a net loss, or a kept gross / operating loss, larger than revenue,
    # refused at the read exactly as the template refuses such a record (`news_templates._money_map_parts`:
    # ``implausible_figures``; no bar is drawn longer than revenue's); `template_refused` — the record's template
    # refused it when it was last offered (the script flow reports it back:
    # `company_news_adapter.remember_template_refusal`), a template-only gate the read cannot apply (a segment
    # named like a web address, any future gate)
    "money_map_loss_above_revenue", "template_refused",
})
_REASON_RE = re.compile(r"[a-z][a-z0-9_]{0,63}")

# ── source labels (pinned verbatim; never the data vendor) ────────────────────

SOURCE_LABELS: Dict[str, str] = {
    "ceo_buys": "SEC Form 4 filings",
    "insider_buys": "SEC Form 4 filings",
    "thirteen_f": "SEC Form 13F",
    "congress_count": "congressional periodic transaction reports",
    "earnings": "company results and analyst consensus",
    "money_map": "company financial statements",
    # review round 14: a theme's facts come from each company's revenue breakdown (product or business
    # lines), which is not always its reportable segments (Apple's iPhone is not one) — never "segment"
    "theme_explainer": "company revenue breakdowns; grouping by Caydex",
}
THIRTEEN_F_AMENDED_LABEL = "SEC Form 13F-HR/A"

# ── the Money Map pool (owner decision 2, 2026-10-09) ─────────────────────────

#: Curated FIRST: well-known US companies that report several revenue segments in USD. No banks,
#: insurers (health insurers included), REITs or real estate. Order is the rotation order; every
#: candidate still passes every gate (USD reporter, major exchange, consistency, ledger).
#: Owner-reviewable: add, remove or reorder freely (a test pins only the hygiene).
MONEY_MAP_SEED: Tuple[str, ...] = (
    "AAPL", "MSFT", "AMZN", "GOOGL", "META", "NVDA", "COST", "WMT", "KO", "PEP",
    "MCD", "NKE", "SBUX", "DIS", "NFLX", "PG", "JNJ", "TSLA", "HD", "TGT",
    "CAT", "DE", "BA", "HON", "UPS", "FDX", "XOM", "CVX", "T", "VZ",
    "CMCSA", "ADBE", "CRM", "ORCL", "INTC", "AMD", "QCOM", "CSCO", "IBM", "UBER",
    "ABNB", "BKNG", "YUM", "HSY", "MDLZ", "CL", "KMB", "LLY", "PFE", "ABBV",
    "MRK", "AMGN", "ABT", "TMO", "EA", "INTU",
)
#: Never a Money Map, whatever pool proposes them: banks, card networks and payments (Financial
#: Services), insurers (health insurers sit in Healthcare, so the sector gate misses them),
#: REITs and real estate. The sector gate (`MONEY_MAP_EXCLUDED_SECTORS`) still applies to all.
MONEY_MAP_EXCLUDED_SYMBOLS: FrozenSet[str] = frozenset({
    "JPM", "BAC", "WFC", "C", "GS", "MS", "SCHW", "USB", "PNC", "TFC", "COF", "BK", "AXP",
    "V", "MA", "PYPL", "BRK-A", "BRK-B",
    "UNH", "CVS", "CI", "ELV", "HUM", "CNC", "MOH", "MET", "PRU", "AIG", "ALL", "PGR", "TRV",
    "CB", "AFL",
    "AMT", "PLD", "SPG", "O", "EQIX", "PSA", "CCI", "WELL", "DLR", "CBRE", "VICI",
})


def merge_money_map_pool(*sources: Iterable[Any]) -> Tuple[str, ...]:
    """The Money Map candidate order: `MONEY_MAP_SEED` first, then each source in the order given
    (the adapter passes the US-listed Trillion Club members, then the Emerging Frontiers theme
    tickers). Canonicalised, de-duplicated in order, excluded symbols and unusable tickers
    (warrants, units, bad grammar) dropped. Pure; never raises on junk entries."""
    out: List[str] = []
    seen = set()
    for source in (MONEY_MAP_SEED, *sources):
        for raw in source or ():
            sym = canonical_symbol(raw)
            if sym is None or sym in seen or sym in MONEY_MAP_EXCLUDED_SYMBOLS:
                continue
            seen.add(sym)
            out.append(sym)
    return tuple(out)


# ── symbols ───────────────────────────────────────────────────────────────────

_SYMBOL_RE = re.compile(r"[A-Z]{1,5}(-[A-C])?")
_WARRANT_UNIT_RIGHT = frozenset("WURQZ")


def symbol_problem(raw: Any) -> Optional[str]:
    """Why ``raw`` is not a usable common-stock ticker, or None. Upper-cased and "." folded to
    "-" first (BRK.B → BRK-B). A 5-letter ticker ending in W, U, R, Q or Z is a warrant, unit,
    right or a special listing (RZLVW was rank 7 on the Pro card): `warrant_unit_right`."""
    if not isinstance(raw, str) or not raw.isascii():
        return "symbol_grammar"
    sym = raw.strip().upper().replace(".", "-")
    if not _SYMBOL_RE.fullmatch(sym):
        return "symbol_grammar"
    if len(sym) == 5 and sym[-1] in _WARRANT_UNIT_RIGHT:
        return "warrant_unit_right"
    return None


def canonical_symbol(raw: Any) -> Optional[str]:
    """The canonical dash form (``^[A-Z]{1,5}(-[A-C])?$``) of ``raw``, or None when unusable
    (`symbol_problem` says why)."""
    if symbol_problem(raw) is not None:
        return None
    return raw.strip().upper().replace(".", "-")


#: NASDAQ reserves a fifth letter P / O / N / M for an issuer's first to fourth PREFERRED series (WAFDP, HBANP,
#: AGNCM, ACGLO, SLMBP …). EXACTLY five letters: SNAP, RAMP and every other four-letter P/O/N/M ticker is a
#: common stock. COPIED from `stock_search_service._NASDAQ_PREFERRED_RE` (that module loads the FMP client; this
#: one is pure) and pinned equal to it by tests/test_marketing_company_news_rules.py — that service found 186
#: active symbols of the shape and no common stock among them. Review of 2026-10-16 (live preview F3): FMP's
#: profile of such a line carries the ISSUER's plain name ("WaFd, Inc.", NASDAQ, no ADR flag) and a market cap
#: priced on the common's share count, so the name gate (`non_common_name`) let WAFDP through as "WaFd" — and
#: its calendar row can carry a different EPS from the common's (WAFDP 0.76 vs WAFD 0.85 against one 0.843
#: estimate: a 9.8% "miss" that was never the company's).
NASDAQ_PREFERRED_SYMBOL_RE = re.compile(r"^[A-Z]{4}[PONM]$")


def nasdaq_preferred_symbol(symbol: Any) -> bool:
    """Is ``symbol`` (canonical) a NASDAQ preferred line by its shape (`NASDAQ_PREFERRED_SYMBOL_RE`)? A
    non-string is False (the symbol gate refuses it with its own reason)."""
    return isinstance(symbol, str) and NASDAQ_PREFERRED_SYMBOL_RE.match(symbol) is not None


def cik10(raw: Any) -> Optional[str]:
    """A CIK as the 10-digit, zero-padded string the ledger key uses, or None. Accepts an int or
    a digit string (leading zeros allowed); never a bool, a float or an all-zero CIK."""
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        raw = str(raw)
    if not isinstance(raw, str):
        return None
    s = raw.strip()
    if not s.isascii() or not s.isdigit() or len(s) > 10 or int(s) == 0:
        return None
    return s.zfill(10)


# ── quarters, months, windows ─────────────────────────────────────────────────

#: ASCII digits only and `fullmatch` everywhere: `\d` admits other scripts' digits and `$` a
#: trailing newline, either of which would reach a ledger key.
_PERIOD_RE = re.compile(r"([0-9]{4})-Q([1-4])")
_MONTH_RE = re.compile(r"([0-9]{4})-(0[1-9]|1[0-2])")


def period_label(year: int, quarter: int) -> str:
    """"YYYY-Qn"."""
    if not (1 <= int(quarter) <= 4):
        raise ValueError(f"quarter {quarter!r} out of range")
    return f"{int(year):04d}-Q{int(quarter)}"


def period_end_of(period: str) -> date:
    """The calendar quarter end of a "YYYY-Qn" label; ValueError when malformed."""
    m = _PERIOD_RE.fullmatch(period) if isinstance(period, str) else None
    if not m:
        raise ValueError(f"period {period!r} is not YYYY-Qn")
    year, q = int(m.group(1)), int(m.group(2))
    return _month_end(year, q * 3)


def previous_period_end_of(period: str) -> date:
    """The end of the quarter BEFORE a "YYYY-Qn" label (the adjacent quarter a 13F is diffed
    against); ValueError when malformed."""
    end = period_end_of(period)
    start = date(end.year, end.month - 2, 1)
    return start - timedelta(days=1)


def _month_end(year: int, month: int) -> date:
    nxt = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    return nxt - timedelta(days=1)


def month_end_of(month: str) -> date:
    """The last day of a "YYYY-MM" month; ValueError when malformed."""
    m = _MONTH_RE.fullmatch(month) if isinstance(month, str) else None
    if not m:
        raise ValueError(f"month {month!r} is not YYYY-MM")
    return _month_end(int(m.group(1)), int(m.group(2)))


def insider_window(run_date: date) -> Tuple[date, date]:
    """The Form 4 filing window of a run: ``[run_date - 7, run_date - 1]`` (both inclusive)."""
    if not isinstance(run_date, date) or isinstance(run_date, datetime):
        raise ValueError("run_date must be a date")
    return run_date - timedelta(days=INSIDER_WINDOW_DAYS), run_date - timedelta(days=1)


# ── the members-elect window (owner decision 4, 2026-10-15) ───────────────────
#
# From a general election until the new Congress is sworn in (January 3), the people just elected are
# MEMBERS-ELECT: not on the roster (it holds the SITTING Congress, `roster_fresh_for`), so the Congress
# block-list cannot know them, and a Form 4 filed by one would be named. Inside the window every Form 4
# row is ROLE-ONLY — the adapter builds no person (`_insider_row`) and the template names no one
# (`_insider_parts`, re-checked by `validate_package`) — and the posts keep running. Read from the RUN
# date, never the wall clock, so a stored output and its re-check at create_posts (`revalidate`, the same
# run date) always agree — through `members_elect_for_run`, which also covers the days after the run date
# that its posts can still go out on (review of 2026-10-16).


def general_election_day(year: int) -> date:
    """The US general election day of ``year``: the Tuesday after the first Monday of November
    (2 U.S.C. §7) — always between November 2 and November 8."""
    if not isinstance(year, int) or isinstance(year, bool):
        raise ValueError(f"year must be an int, got {year!r}")
    nov1 = date(year, 11, 1)
    first_monday = nov1 + timedelta(days=(7 - nov1.weekday()) % 7)
    return first_monday + timedelta(days=1)


def members_elect_window(day: date) -> bool:
    """True from Election Day of an EVEN year (the federal general election) through January 3 of the
    next year, both inclusive; never in an odd year's November or December (no federal general
    election). January 1-3 belong to the window of the year before, when that year was even."""
    if not isinstance(day, date) or isinstance(day, datetime):
        raise ValueError("day must be a date")
    if day.month == 1 and day.day <= 3:
        return (day.year - 1) % 2 == 0
    return day.year % 2 == 0 and day >= general_election_day(day.year)


#: Review of 2026-10-16 (ME-1): a run's posts do not all go out on the run date. The publisher sends an approved
#: post on its run day or the NEXT ET day (later it expires), and an Upload-Post job it never acknowledged may be
#: resent up to 20 hours after its first send — so a post composed on Monday Nov 2, 2026 (outside the window: a
#: NAMED Form 4 post) could be approved on Tuesday night, after races are called, or resent on Wednesday Nov 4,
#: and name a filer who has just become a member-elect. The window is therefore judged over every day a run's
#: posts can go out: the run date and the this-many days after it.
MEMBERS_ELECT_PUBLISH_DAYS = 2


def members_elect_for_run(run_date: date) -> bool:
    """THE members-elect check of a run (every caller: the adapter's `_insider_row`, the templates'
    `_insider_parts`, `validate_package` and — through them — `revalidate`): is ANY day its posts can go out
    on — ``run_date`` through ``run_date + MEMBERS_ELECT_PUBLISH_DAYS`` — inside `members_elect_window`? The
    window is one interval (Election Day → January 3), so its value on the two ends decides every day between.
    A pure function of the run date (and `members_elect_window`, read at call time), so compose, the adapter
    and every re-check on the same run date agree. 2026: Nov 1 and Nov 2 are role-only runs (their posts can go
    out on Election Day), Oct 31 is not; Jan 3, 2027 is the last role-only run."""
    if not isinstance(run_date, date) or isinstance(run_date, datetime):
        raise ValueError("run_date must be a date")
    return members_elect_window(run_date) or members_elect_window(
        run_date + timedelta(days=MEMBERS_ELECT_PUBLISH_DAYS))


# ── company names ─────────────────────────────────────────────────────────────

#: The builder's security-class tail ("CoreWeave, Inc. Class A Common Stock" → "CoreWeave, Inc."),
#: copied from `trillion_club.builder._CLASS_SUFFIX_RE` (the builder loads the FMP client).
_CLASS_TAIL_RE = re.compile(
    r"[\s,]+(?:class\s+[a-z]\s+)?(?:common\s+stock|ordinary\s+shares|"
    r"subordinate\s+voting\s+shares|american\s+depositary\s+shares|depositary\s+shares)$",
    re.IGNORECASE,
)
#: One trailing legal suffix. "Company" is NOT one ("The Coca-Cola Company" keeps it); "Co."
#: needs its period, and is never stripped from "& Co." (see `_strip_legal`).
_LEGAL_SUFFIX_RE = re.compile(
    r"[,\s]+(?:inc\.?|incorporated|corp\.?|corporation|ltd\.?|limited|plc|n\.?\s?v\.?|"
    r"s\.?\s?a\.?|co\.|l\.?l\.?c\.?|l\.?p\.?)$",
    re.IGNORECASE,
)
_ASCII_PUNCT = str.maketrans({
    "‘": "'", "’": "'", "‛": "'", "′": "'",
    "‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-",
})
_COMPANY_NAME_RE = re.compile(r"[A-Za-z0-9 &.,'+-]+")
#: Words a news name may not carry beyond `copy_rules` (the plan's "signal / shocker / BREAKING"
#: bans): "Signal Hill" would put the banned product noun into a headline.
_NAME_EXTRA_BANNED_RE = re.compile(r"\b(?:signals?|shockers?|breaking)\b", re.IGNORECASE)

#: Owner-curated display names, keyed by the folded name AFTER the class tail and legal suffixes
#: are stripped. Each value must itself pass `company_name_problem` (a test). Add freely.
DISPLAY_OVERRIDES: Dict[str, str] = {
    "costco wholesale": "Costco",
    "meta platforms": "Meta",
    "amazon.com": "Amazon",
    "the coca-cola company": "Coca-Cola",
    "the walt disney company": "Disney",
    "the procter & gamble company": "Procter & Gamble",
    "the home depot": "Home Depot",
    "the boeing company": "Boeing",
    "the hershey company": "Hershey",
    "international business machines": "IBM",
    "advanced micro devices": "AMD",
    "qualcomm": "Qualcomm",
    "nike": "Nike",
    "eli lilly and company": "Eli Lilly",
    "merck & co.": "Merck",
    "honeywell international": "Honeywell",
    "united parcel service": "UPS",
    "verizon communications": "Verizon",
    "cisco systems": "Cisco",
    "uber technologies": "Uber",
    "colgate-palmolive company": "Colgate-Palmolive",
    "yum! brands": "Yum Brands",
    # A leading "The" whose legal suffix the strip removed leaves a fragment ("The Kroger Co." →
    # "The Kroger", live in the Berkshire 2026-Q2 preview; review round 8). Each key is the exact
    # stripped FMP legal name — never a generic "drop The" rule ("The Trade Desk" and "The Cigna
    # Group" are those companies' own names and stay as they are).
    "the kroger": "Kroger",                                    # The Kroger Co.
    "the charles schwab": "Charles Schwab",                    # The Charles Schwab Corporation
    "the allstate": "Allstate",                                # The Allstate Corporation
    "the progressive": "Progressive",                          # The Progressive Corporation
    "the aes": "AES",                                          # The AES Corporation
    "the goldman sachs group": "Goldman Sachs",                # The Goldman Sachs Group, Inc.
    "the pnc financial services group": "PNC",                 # The PNC Financial Services Group, Inc.
    "the sherwin-williams company": "Sherwin-Williams",        # The Sherwin-Williams Company
    "the kraft heinz company": "Kraft Heinz",                  # The Kraft Heinz Company
    "the clorox company": "Clorox",                            # The Clorox Company
    "the tjx companies": "TJX",                                # The TJX Companies, Inc.
    "the travelers companies": "Travelers",                    # The Travelers Companies, Inc.
    # A club member whose legal name is over the 32-character cap (Drop 2b company_stakes: the
    # investor of its stakes) — its own short name, never a truncation.
    "taiwan semiconductor manufacturing company": "TSMC",     # ... Company Limited
    # Domain-shaped legal names: X, Threads, Facebook and LinkedIn would autolink them (a bare
    # third-party link in our body, and the X post refused at publish). Any other domain-shaped
    # name is refused by `company_name_problem` — add it here to show it.
    "c3.ai": "C3 AI",
    "bigbear.ai holdings": "BigBear AI",
    "1-800-flowers.com": "1-800-Flowers",
    # A vendor misspelling, shown as the company spells itself (Batch 1, 2026-10-15): FMP's profile for NAUT
    # reads "Nautilus Biotechnolgy, Inc." (read live 2026-10-14 and again 2026-10-15), while the SEC's own
    # issuer name is "NAUTILUS BIOTECHNOLOGY INC". Nothing catches a typo in a name, so each one is a row.
    "nautilus biotechnolgy": "Nautilus Biotechnology",
    # The re-review of 2026-10-19 (NAME-CAP-1): "Archer-Daniels-Midland" is ONE 22-character word, past
    # `COMPANY_NAME_WORD_MAX_CHARS` — the only one among 3,686 NYSE / NASDAQ / AMEX companies of $250M or more (a
    # read-only screener scan, 2026-10-19) — so ADM was refused for every series. The company's own brand since
    # 2019 is "ADM".
    "archer-daniels-midland company": "ADM",
}

#: Money Map segment labels as FMP's revenue segmentation spells them → the company's own name
#: for the segment (review round 7, live L3: Microsoft's "Linked In Corporation" and "XBOX",
#: Apple's "Service"). EXACT match on the label (surrounding whitespace aside) — never a generic
#: casing rule, which would turn "iPhone" or "AWS" into something the company does not write.
#: Each value must itself be a drawable `Segment` name (a test). Add a row only for a label seen
#: in FMP's data (or its plain spacing twin).
SEGMENT_DISPLAY_OVERRIDES: Dict[str, str] = {
    "Linked In Corporation": "LinkedIn",
    "LinkedIn Corporation": "LinkedIn",
    "Linked In": "LinkedIn",              # the same spacing defect without the suffix
    "XBOX": "Xbox",
    "Service": "Services",                # Apple reports "Services"
    # Batch 1 (2026-10-15), FMP's product segmentation read live that day: McDonald's fiscal 2025 labels
    # its home market in capitals; PepsiCo's "(Segment)" is XBRL's disambiguation suffix, not the
    # segment's name (its parentheses also made the label undrawable, so its revenue fell into the rest).
    "UNITED STATES": "United States",
    "Latin America (Segment)": "Latin America",
    # The review of 2026-10-16 (live preview F5): Walmart's labels with their punctuation stripped by the feed —
    # "Walmart U S" for the company's "Walmart U.S." and "Sams Club" for its "Sam's Club" (a misspelled brand).
    "Walmart U S": "Walmart U.S.",
    "Sams Club": "Sam's Club",
}

#: Money Map segment labels that are wrong for ONE company → that company's own segment name, keyed by
#: (canonical symbol, the label exactly as the feed spells it). The review of 2026-10-16 (MM-1, confirmed on
#: EDGAR): FMP's fiscal-2025 breakdown for McDonald's labels $13.63B "High-Growth Markets" — a segment of its
#: 2015-2018 structure — beside the current "International Developmental Licensed Markets and Corporate"; since
#: 2019 McDonald's reports U.S., International Operated Markets and IDL & Corporate (the FY2025 10-K: 0 hits for
#: "High-Growth Markets", the three values add up to revenue), so the $13.63B is International Operated Markets
#: under a retired name. NEVER in the label-only `SEGMENT_DISPLAY_OVERRIDES`: another company may really report a
#: segment of that name. Consulted first by `segment_display_name(…, symbol=…)` (the adapter's `_segments` passes
#: the breakdown's symbol, so a theme's segment fact reads it too). Each value must itself be a drawable `Segment`
#: name that does not fold (a test). Add a row only after checking the company's latest 10-K segment note.
SEGMENT_SYMBOL_OVERRIDES: Dict[Tuple[str, str], str] = {
    ("MCD", "High-Growth Markets"): "International Operated Markets",
    # The re-review of 2026-10-19 (B1R-MM-PEP-STALE-LABEL, checked on EDGAR): PepsiCo realigned its segments for
    # fiscal 2025 into PFNA, PBNA, International Beverages Franchise, Europe, Middle East and Africa (EMEA), LatAm
    # Foods and Asia Pacific Foods — India moved from the former AMESA into Asia Pacific Foods. FMP's fiscal-2025
    # breakdown still labels the $18.02B "Africa, Middle East and South Asia" (the pre-2025 AMESA, $6.22B in
    # fiscal 2024 beside a $13.87B Europe line that is gone in 2025): the FY2025 10-K (0000077476-26-000007) has 0
    # hits for that name or "AMESA" and 1 for "Europe, Middle East and Africa", and the Q4 2025 release gives EMEA
    # net revenue of $18,025M — FMP's figure. Fiscal 2024 and earlier, when the label was right, can never reach a
    # Money Map (`money_map_next_year_due`). The 10-K also writes "International Beverages Franchise" (FMP:
    # "International Beverage Franchise").
    ("PEP", "Africa, Middle East and South Asia"): "Europe, Middle East and Africa",
    ("PEP", "International Beverage Franchise"): "International Beverages Franchise",
}


def segment_display_name(raw: Any, *, symbol: Any = None) -> Any:
    """The display name of a Money Map segment label: the company's own `SEGMENT_SYMBOL_OVERRIDES` entry when
    ``symbol`` is given (exact match on the canonical symbol and the stripped label), else its
    `SEGMENT_DISPLAY_OVERRIDES` entry on an exact match (whitespace stripped), else the label unchanged (a
    non-string as is — the caller's own gates refuse it)."""
    if not isinstance(raw, str):
        return raw
    label = raw.strip()
    sym = canonical_symbol(symbol) if symbol is not None else None
    if sym is not None:
        fixed = SEGMENT_SYMBOL_OVERRIDES.get((sym, label))
        if fixed is not None:
            return fixed
    return SEGMENT_DISPLAY_OVERRIDES.get(label, raw)


# ── Money Map labels that name nothing, and the regions (Batch 1, 2026-10-15) ─

#: Company codes a reader knows as the line's own public name — kept although they match the code shape
#: below. AWS is how Amazon itself names Amazon Web Services (FMP spelled it out in the 2026-10-09 preview;
#: a feed or year that writes "AWS" must not fold Amazon's best-known line into the unnamed rest); the others
#: are region names (`REGION_NAMES`): EMEA and APAC, and (the review of 2026-10-16, MM-2) US, USA, UK, EU and
#: UAE — a breakdown of "Europe, Asia Pacific, US" folded the US revenue into the rest while it still read "by
#: region". Every acronym-shaped `REGION_NAMES` key is listed here (a test), and `segment_fold_reason` never
#: folds a region name anyway. Add a code only when the company uses it as the line's public name, never to
#: rescue a map.
SEGMENT_ACRONYMS_OK: FrozenSet[str] = frozenset({"AWS", "EMEA", "APAC", "US", "USA", "UK", "EU", "UAE"})
#: Labels that name no part of the company (exact after case and spacing, `segment_fold_reason`):
#: "Products made up 66% of revenue" or "Net sales, $40 billion" tells the reader nothing.
GENERIC_SEGMENT_LABELS: FrozenSet[str] = frozenset({
    "product", "products", "segment", "segments", "revenue", "revenues", "sales", "net sales", "total",
})
#: A company's internal code: the WHOLE label is 1-4 capital letters ("QCT", "QTL", "NXE", "CMBU";
#: PepsiCo's "PBNA", "PFNA", "IBF"). Checked on the DISPLAY name, so "XBOX" is "Xbox" first and stays.
_SEGMENT_CODE_RE = re.compile(r"[A-Z]{1,4}")


def segment_fold_reason(name: Any) -> Optional[str]:
    """Why a Money Map (or theme) segment's DISPLAY name — after `segment_display_name` — is folded into
    the unnamed rest (the "Everything else" bar), or None when it may be drawn and narrated:

    * ``"code"`` — the whole label is 1-4 capital letters, not on `SEGMENT_ACRONYMS_OK` and not a region
      name (`is_region_name`: "US", "UK"): an internal code ("QCT: 85% of revenue" tells no one anything);
    * ``"generic"`` — the label, case- and space-folded, is on `GENERIC_SEGMENT_LABELS`.

    Folding never refuses on its own: the rest is still revenue, and the adapter's own gates
    (`segments_thin`, `money_map_mostly_other`, a theme fact's "rest above the top") decide whether what
    is left explains the company. A non-string is None here — the caller's own gates refuse it. Pure."""
    if not isinstance(name, str):
        return None
    label = name.strip()
    if _SEGMENT_CODE_RE.fullmatch(label) and label not in SEGMENT_ACRONYMS_OK and not is_region_name(label):
        return "code"
    if " ".join(label.casefold().split()) in GENERIC_SEGMENT_LABELS:
        return "generic"
    return None


#: The ONLY names a Money Map calls regions (`region_key` forms). A breakdown reads "How fiscal 2025 revenue
#: broke down BY REGION" only when EVERY named segment is one of these — exact, never a word inside a label:
#: McDonald's "International Operated Markets" groups the markets it runs itself, not one place, and
#: PepsiCo's "Frito-Lay North America" is a business. Anything else reads the neutral "How fiscal 2025
#: revenue broke down", which is true of any breakdown (FMP's product segmentation is product lines for some
#: companies, regions for others: before Batch 1 every map said "by product or business line", false for
#: McDonald's and Costco). The acronyms and the "U.S." forms (the review of 2026-10-16, MM-2 / MM-3) read as
#: `region_key` writes them: "U.S." is "u s", "U.S.A." is "u s a".
REGION_NAMES: FrozenSet[str] = frozenset({
    # countries
    "united states", "canada", "mexico", "brazil", "united kingdom", "germany", "france", "japan", "china",
    "greater china", "india", "australia", "south korea", "taiwan",
    "us", "u s", "usa", "u s a", "uk", "u k", "uae",
    "united states and canada", "u s and canada", "us and canada",
    # continents and groups of them
    "north america", "south america", "latin america", "americas", "europe", "emea", "eu",
    "europe middle east and africa", "middle east and africa", "africa", "asia", "asia pacific", "apac",
    "rest of asia pacific",
    # the remainder outside the home market
    "international", "other international", "rest of world", "rest of the world",
})
#: Region names that take "the" in running text — "the United States made up 73% of revenue", "the Americas,
#: $40 billion", "The U.S. made up …" (`news_templates`, sentences only: a bar label, a card and the alt text
#: keep the name as drawn). Every key is also a `REGION_NAMES` key (a test). The review of 2026-10-16 (MM-3):
#: since Batch 1 the part's name OPENS narration line 2 ("{s} made up {sh} of revenue", no "Its {s} business"
#: before it), so a name that needs "the" and lacks it here reads "U.S. made up 50% of revenue" — narrated and
#: burned into the captions. Every country or group named with "the" in English is listed (the U.S. and U.K.
#: forms, the acronyms, "the United States and Canada", "the rest of the world" / "the rest of Asia Pacific");
#: the names left out read whole without it ("Canada", "Europe", "Asia Pacific", "International", "Other
#: International" — a label that names a part, like "Services"), and so does every product or business line.
REGION_ARTICLE: FrozenSet[str] = frozenset({
    "united states", "united kingdom", "americas", "middle east and africa",
    "us", "u s", "usa", "u s a", "uk", "u k", "uae", "eu",
    "united states and canada", "u s and canada", "us and canada",
    "rest of world", "rest of the world", "rest of asia pacific",
})
#: Punctuation `region_key` reads as a space ("Asia-Pacific", "Europe, Middle East & Africa").
_REGION_PUNCT_RE = re.compile(r"[.,;:()/'’\-–—]")


def region_key(name: Any) -> str:
    """A segment name as `REGION_NAMES` spells it: NFKC, case-folded, "&" read as "and", punctuation read
    as a space, spaces collapsed ("Asia-Pacific" → "asia pacific"). "" for a non-string. Pure."""
    if not isinstance(name, str):
        return ""
    s = unicodedata.normalize("NFKC", name).casefold().replace("&", " and ")
    return " ".join(_REGION_PUNCT_RE.sub(" ", s).split())


def is_region_name(name: Any) -> bool:
    """True only for a name on `REGION_NAMES` (exact, after `region_key`)."""
    return region_key(name) in REGION_NAMES


def takes_region_article(name: Any) -> bool:
    """True for a region name that takes "the" in running text (`REGION_ARTICLE`)."""
    return region_key(name) in REGION_ARTICLE


# ── descriptive investee names (Batch 1, 2026-10-15) ──────────────────────────

#: The head nouns of a DESCRIPTIVE stake name — one that describes what is held instead of naming a company
#: (the catalogue's "Louisiana data center venture", Meta's 20% membership interest). In running text such a
#: name takes "the" ("As of that date, the Louisiana data center venture was not listed …", news_templates'
#: `st.ee.the`); before Batch 1 the post read "Louisiana data center venture was not listed …", narrated and so
#: burned into the video's captions. Only the LAST word decides, only in lower case, and only one of these
#: nouns: a company's own name ends in a capital ("Iroquois Gas Transmission System", "Mitsui & Co.") or is
#: styled in lower case on purpose ("SK hynix", "bluebird bio") — neither takes an article, and "the SK hynix"
#: would be a new mistake. A descriptive name with a head noun missing here keeps reading without its article
#: (the pre-Batch-1 wording, never a wrong word): add the noun when the catalogue gains such a name.
DESCRIPTIVE_NAME_HEADS: FrozenSet[str] = frozenset({
    "venture", "ventures", "partnership", "partnerships", "consortium", "alliance", "project", "projects",
    "campus", "facility", "facilities", "plant", "plants", "factory", "factories", "site", "sites", "center",
    "centers", "centre", "centres", "complex", "pipeline", "pipelines", "terminal", "terminals", "refinery",
    "refineries", "mine", "mines", "farm", "farms", "fund", "funds", "portfolio", "investment", "investments",
    "business", "businesses", "unit", "units", "subsidiary", "subsidiaries", "affiliate", "affiliates",
    "operation", "operations", "company", "companies", "entity", "entities", "vehicle", "vehicles",
})
#: One word of a stake name, for the article rule: lower-case ASCII letters only (a hyphenated, numbered or
#: capitalised word is never a head noun here).
_HEAD_NOUN_RE = re.compile(r"[a-z]{2,20}")
#: `takes_name_article` reads no longer name (a stake's investee name is at most 40 characters where it is
#: drawn; anything longer is refused before it is said, and the bound keeps the split small).
STAKE_NAME_ARTICLE_MAX_CHARS = 200


def takes_name_article(name: Any) -> bool:
    """True when a stake's investee name is DESCRIPTIVE (`DESCRIPTIVE_NAME_HEADS`): two words or more, its last
    word one of those nouns in lower case, and not already opened by "the". Running text then says "the"
    before it (news_templates `st.ee.the` / `st.ee.the.cap`); a drawn label (a plate, a card) never does. A
    non-string or blank name is False. Pure, linear."""
    if not isinstance(name, str) or len(name) > STAKE_NAME_ARTICLE_MAX_CHARS:
        return False
    words = name.split()
    if len(words) < 2 or words[0].casefold() == "the":
        return False
    last = words[-1]
    return bool(_HEAD_NOUN_RE.fullmatch(last)) and last in DESCRIPTIVE_NAME_HEADS


def _strip_legal(name: str) -> str:
    out = name
    for _ in range(2):
        m = _LEGAL_SUFFIX_RE.search(out)
        if not m:
            break
        cand = out[: m.start()].rstrip(" ,")
        # "Merck & Co." must not become "Merck &": a suffix that leaves a dangling "&"/"and"
        # was part of the name.
        if not cand or cand.endswith("&") or cand.lower().endswith(" and"):
            break
        out = cand
    return out


def _single_line_clean(s: str) -> bool:
    return (
        isinstance(s, str)
        and _compliance.clean(s) == s
        and not any(unicodedata.category(ch) == "Cc" for ch in s)
    )


def company_name_problem(name: Any) -> Optional[str]:
    """Why ``name`` cannot be a drawn/narrated company name, or None:
    `company_name_unusable` (not 2..32 ASCII chars of ``[A-Za-z0-9 &.,'+-]`` with a letter, not
    clean, or a span X would autolink — "C3.ai", "1-800-FLOWERS.COM": a bare third-party link in
    the post) / `company_name_banned_word` (`copy_rules` banned or forecast wording, or a
    signal/shocker/breaking word — "Hot Topic", "Signal Hill")."""
    lo, hi = COMPANY_NAME_CHARS
    if (
        not isinstance(name, str)
        or not (lo <= len(name) <= hi)
        or not name.isascii()
        or not _COMPANY_NAME_RE.fullmatch(name)
        or not any(ch.isalpha() for ch in name)
        or name != name.strip()
        or "  " in name
        or not _single_line_clean(name)
        or _x_link_tokens(name)
    ):
        return "company_name_unusable"
    if (
        _copy_rules.contains_banned_copy(name)
        or _copy_rules.contains_forecast(name)
        or _NAME_EXTRA_BANNED_RE.search(name)
    ):
        return "company_name_banned_word"
    return None


#: A raw vendor name longer than this is junk; refused BEFORE any regex runs (the suffix patterns
#: backtrack over a long run of commas).
_RAW_NAME_MAX = 200


def _display_and_problem(raw: Any) -> Tuple[Optional[str], Optional[str]]:
    if not isinstance(raw, str) or len(raw) > _RAW_NAME_MAX:
        return None, "company_name_unusable"
    s = " ".join(_compliance.clean(raw).translate(_ASCII_PUNCT).split())
    s = _CLASS_TAIL_RE.sub("", s).strip()
    s = _strip_legal(s)
    if _LEGAL_SUFFIX_RE.fullmatch(" " + s):
        return None, "company_name_unusable"     # nothing but a legal suffix ("Inc.")
    s = DISPLAY_OVERRIDES.get(_compliance.fold(s), s)
    problem = company_name_problem(s)
    return (None, problem) if problem else (s, None)


def display_company_name(raw: Any) -> Optional[str]:
    """The company's display name: class tail and at most two legal suffixes stripped ("Company"
    kept), `DISPLAY_OVERRIDES` applied, then `company_name_problem` must pass. None otherwise."""
    return _display_and_problem(raw)[0]


def company_name_word_too_long(name: Any) -> bool:
    """Does a display name hold a word longer than `COMPANY_NAME_WORD_MAX_CHARS` (one the video cover cannot
    draw whole)? A word is a run between spaces, so "Sherwin-Williams" is one word of 16. A non-string is
    False here — the name gates refuse it with their own reason. Linear."""
    return isinstance(name, str) and any(len(w) > COMPANY_NAME_WORD_MAX_CHARS for w in name.split())


def _flag_set(v: Any) -> bool:
    """A profile flag counts as SET unless it is False or absent (fail closed on junk)."""
    return v is not None and v is not False


#: A profile name that marks a line other than common stock (Drop 2b: earnings and the "common"
#: purpose — "no warrants / units / rights / preferred"; Batch 1: the "insider" purpose too). The
#: symbol grammar catches most such lines first (-WT, -U, -PB, a 5-letter W/U/R suffix; a NASDAQ
#: preferred's 5-letter P/O/N/M shape at the profile gate, `nasdaq_preferred_symbol` — its profile name
#: is the issuer's own, so this list never sees it); this is the
#: name-side backstop — and the only gate for a depositary line whose `isAdr` flag is False (AgomAb,
#: 2026-10-12). Accepted over-blocks: an MLP's "Common Units" (not a common stock either), and a
#: company whose own name holds one of these words as a word ("Preferred Bank") — one company's posts
#: lost, never a post about the wrong security.
_NON_COMMON_NAME_RE = re.compile(
    r"\b(?:warrants?|units?|rights?|preferred|pfd|depositary|notes?|debentures?)\b", re.IGNORECASE)


def non_common_name(raw: Any) -> bool:
    """Does a profile's company name mark a non-common line (warrant, unit, right, preferred,
    depositary share, note, debenture)? Linear. A non-string or over-long name is False here:
    the name gate right after refuses it with its own reason (`company_name_unusable`)."""
    if not isinstance(raw, str) or len(raw) > _RAW_NAME_MAX:
        return False
    return bool(_NON_COMMON_NAME_RE.search(raw))


def company_from_profile(profile: Any, symbol: Any, *, purpose: str) -> Union["CompanyRef", str]:
    """The CompanyRef of ``symbol`` from its profile, or the rejection reason (a str).

    Every purpose: `symbol_grammar` / `warrant_unit_right` (the requested symbol),
    `profile_missing` (no dict, or its canonical symbol differs), `not_major_exchange`,
    `etf_or_fund`, `inactive`, then the name (`company_name_unusable` /
    `company_name_banned_word`). Purposes "insider", "earnings" and "common" also refuse `adr`,
    `not_usd` and `non_common_listing` (`non_common_name`; "insider" since Batch 1, 2026-10-15: a
    depositary-share line FMP does not flag `isAdr`; and, since the review of 2026-10-16, a NASDAQ
    preferred symbol by its shape, `nasdaq_preferred_symbol` — FMP profiles WAFDP as plain "WaFd, Inc.");
    "insider" and "earnings" also
    `below_cap_floor` ($250M insider, `EARNINGS_MIN_MARKET_CAP` earnings; a missing or non-finite
    cap is below it). Only "listing" keeps a non-common name (a 13F holding, a Money Map, a stake's
    investor or a theme member is named by its listing, an ADR included). The profile's cap is read
    here and never leaves."""
    if purpose not in PROFILE_PURPOSES:
        raise ValueError(f"unknown profile purpose {purpose!r}")
    want = canonical_symbol(symbol)
    if want is None:
        return symbol_problem(symbol) or "symbol_grammar"
    if not isinstance(profile, Mapping) or canonical_symbol(profile.get("symbol")) != want:
        return "profile_missing"
    exchange = profile.get("exchange")
    if not isinstance(exchange, str) or exchange.strip().upper() not in MAJOR_US_EXCHANGES:
        return "not_major_exchange"
    if _flag_set(profile.get("isEtf")) or _flag_set(profile.get("isFund")):
        return "etf_or_fund"
    trading = profile.get("isActivelyTrading")
    if trading is not None and trading is not True:
        return "inactive"
    if purpose in ("insider", "earnings", "common"):
        if _flag_set(profile.get("isAdr")):
            return "adr"
        currency = profile.get("currency")
        if not isinstance(currency, str) or currency.strip().upper() != "USD":
            return "not_usd"
    if purpose in ("insider", "earnings"):
        floor = INSIDER_MIN_MARKET_CAP if purpose == "insider" else EARNINGS_MIN_MARKET_CAP
        cap = profile.get("marketCap")
        if not _is_num(cap) or cap < floor:
            return "below_cap_floor"
    if purpose in ("insider", "earnings", "common") and (
            non_common_name(profile.get("companyName")) or nasdaq_preferred_symbol(want)):
        return "non_common_listing"
    name, problem = _display_and_problem(profile.get("companyName"))
    if problem:
        return problem
    if company_name_word_too_long(name):
        # a word the cover cannot draw whole (`COMPANY_NAME_WORD_MAX_CHARS`): one candidate refused here,
        # never a CardOverflow that skips the whole day at render
        logger.info("company news: %s display name has a word longer than %d characters — refused "
                    "(company_name_unusable)", want, COMPANY_NAME_WORD_MAX_CHARS)
        return "company_name_unusable"
    return CompanyRef(symbol=want, name=name)


def filer_name_problem(name: Any) -> Optional[str]:
    """Why a 13F filer name cannot be the post's subject, or None.

    Owner decision 5 (2026-10-09): a filer named after a person ("Soros Fund Management") is
    ALLOWED — the subject is always the entity, never "<person> bought". So only:
    `filer_entity_unknown` (missing, blank or a placeholder) and `filer_name_unusable` (not
    2..60 ASCII chars of the company-name alphabet, not clean, a span X would autolink, or
    banned/forecast wording)."""
    if not isinstance(name, str) or not name.strip():
        return "filer_entity_unknown"
    if name.strip().lower() in ("unknown", "n/a", "na", "none", "null", "-", "--"):
        return "filer_entity_unknown"
    lo, hi = FILER_NAME_CHARS
    if (
        not (lo <= len(name) <= hi)
        or not name.isascii()
        or not _COMPANY_NAME_RE.fullmatch(name)
        or not any(ch.isalpha() for ch in name)
        or name != name.strip()
        or "  " in name
        or not _single_line_clean(name)
        or _x_link_tokens(name)
        or _copy_rules.contains_banned_copy(name)
        or _copy_rules.contains_forecast(name)
        or _NAME_EXTRA_BANNED_RE.search(name)
    ):
        return "filer_name_unusable"
    return None


# ── 13F filers: the FILING entity (review round 9) ───────────────────────────

#: The subject of a registry 13F post is the entity that FILED it on EDGAR — never the curated
#: `whales.firm_name`, which can name another entity: CIK 0000921669 is labelled "Icahn
#: Enterprises" (a listed company, IEP) but the 13F is filed by Carl C. Icahn in his own name;
#: 0000898382 "Omega Family Office" is filed by Leon G. Cooperman; 0001549575 "Pabrai Investment
#: Funds" by Dalal Street, LLC. FMP's 13F extract carries no filer name, so this table is the
#: source: registry CIK → the EDGAR filer's name as shown (its legal-form suffix dropped), or
#: None for a NATURAL PERSON filer (refused, `filer_is_person` — the subject is the filing entity
#: and a person never heads a post). A registry CIK missing here is refused
#: (`filer_entity_unknown`) until its EDGAR filer is checked and added. Owner-reviewable: written
#: 2026-10-10 from the filers' EDGAR company names WITHOUT a live EDGAR read (the review verified
#: 0000921669 = "ICAHN CARL C" on data.sec.gov) — re-check each against EDGAR before the 13F season;
#: Greenlight Capital (0001489933) and GMO (0001352662) are left out until their filer is checked.
THIRTEEN_F_FILERS: Mapping[str, Optional[str]] = {
    "0001067983": "Berkshire Hathaway",
    "0001336528": "Pershing Square Capital Management",
    "0001649339": "Scion Asset Management",
    "0001350694": "Bridgewater Associates",
    "0001697748": "ARK Investment Management",
    "0001656456": "Appaloosa",
    "0001029160": "Soros Fund Management",
    "0000921669": None,                                   # ICAHN CARL C (a person)
    "0001536411": "Duquesne Family Office",
    "0001061768": "Baupost Group",
    "0001040273": "Third Point",
    "0000949509": "Oaktree Capital Management",
    "0001345471": "Trian Fund Management",
    "0001791786": "Elliott Investment Management",
    "0001647251": "TCI Fund Management",
    "0001569205": "Fundsmith",
    "0001709323": "Himalaya Capital Management",
    "0001549575": "Dalal Street",
    "0001166559": "Gates Foundation Trust",
    "0001112520": "Akre Capital Management",
    "0001096343": "Markel Group",
    "0001135730": "Coatue Management",
    "0001061165": "Lone Pine Capital",
    "0001103804": "Viking Global Investors",
    "0001541617": "Altimeter Capital Management",
    "0001747057": "D1 Capital Partners",
    "0001759760": "H&H International Investment",
    "0001035674": "Paulson & Co",
    "0000898382": None,                                   # COOPERMAN LEON G (a person)
    "0000850529": "Fisher Asset Management",
    "0001138995": "Glenview Capital Management",
    # Batch 1 (2026-10-15): a SUCCESSOR filer (`THIRTEEN_F_SUCCESSORS`), not a registry CIK —
    # EDGAR "PERSHING SQUARE INC." files Pershing Square's holdings since the old CIK filed only a
    # 13F-NT for 2026-Q2 (a notice that another manager reports them).
    "0002026053": "Pershing Square",
}

#: Batch 1 (2026-10-15): a registry 13F CIK whose holdings are now filed under ANOTHER EDGAR CIK →
#: that successor. The marketing adapter reads the successor's filing dates, builds and diffs its
#: 13F, keys the ledger on it (a post under the old key also counts as posted) and names it from
#: `THIRTEEN_F_FILERS[successor]`; the curated ORDER of `data/whale_registry.json` still comes from
#: the old CIK. One hop, never a chain (tested). The whales table, the registry file and the in-app
#: whale profile keep the old CIK — the profile goes stale until the whale feature follows (an
#: owner task, outside the marketing engine).
THIRTEEN_F_SUCCESSORS: Mapping[str, str] = {
    "0001336528": "0002026053",                           # Pershing Square Capital → PERSHING SQUARE INC.
}

#: The review of 2026-10-16 (live preview F2): the FIRST quarter whose full book each successor filed, keyed
#: like `THIRTEEN_F_SUCCESSORS` (the registry CIK). A successor's 13F is diffed against the successor's OWN
#: previous filing, and in the hand-over quarter that filing is a different, smaller book: PERSHING SQUARE
#: INC. filed only Howard Hughes (9,000,000 shares) for 2026-Q1 while the old CIK filed the eleven positions,
#: so the 2026-Q2 diff read Uber, Brookfield, Microsoft … as "13 newly reported holdings" and the consolidated
#: 27,852,064 Howard Hughes shares (18,852,064 + 9,000,000) as "more shares". A comparison whose previous
#: quarter is earlier than this one is refused (`successor_transition`, the whole filing) — never diffed
#: against the predecessor's book instead: two filers' books in one diff would break the share, option and
#: class checks, which read the previous book from the filer itself. The hand-over quarter is never
#: compared; the next one compares two full books. A successor missing here is refused for every quarter
#: (fail closed). Pershing's first full book: 2026-Q2 (14 positions; FMP extract read 2026-10-16).
THIRTEEN_F_SUCCESSOR_SINCE: Mapping[str, str] = {
    "0001336528": "2026-Q2",
}


def successor_transition(registry_cik: Any, period: Any) -> bool:
    """Would ``period``'s 13F, read from the successor of ``registry_cik`` (`THIRTEEN_F_SUCCESSORS`), be
    diffed against a quarter before the successor's first full book (`THIRTEEN_F_SUCCESSOR_SINCE`)? False
    for a registry CIK with no successor (it is its own filer). True — fail closed — for a successor with
    no since entry or an unreadable one, and for a malformed ``period``."""
    if registry_cik not in THIRTEEN_F_SUCCESSORS:
        return False
    since = THIRTEEN_F_SUCCESSOR_SINCE.get(registry_cik)
    try:
        return previous_period_end_of(period) < period_end_of(since)
    except (TypeError, ValueError):
        return True

#: Batch 1 (2026-10-15, owner default "Berkshire first in 13F season"): the registry filers read
#: BEFORE the Trillion Club filers and the rest of the registry, in this order (matched on the
#: registry CIK or its successor). They share the call's one live-build budget
#: (`company_news_adapter.MAX_LIVE_13F_BUILDS`) with the rest — never a budget of their own.
THIRTEEN_F_LEAD_FILERS: Tuple[str, ...] = ("0001067983",)  # Berkshire Hathaway


def filer_looks_like_person(name: Any) -> bool:
    """Does a 13F filer name read as a natural person ("ICAHN CARL C", "Leon G. Cooperman")? Not
    an entity word anywhere (`is_entity_reporter`) AND the fail-closed renderer makes a person of
    it. A backstop to `THIRTEEN_F_FILERS` (and the club path's only person check)."""
    if not isinstance(name, str) or is_entity_reporter(name):
        return False
    return render_person_name(name) is not None or render_person_name(_flip_first_last(name)) is not None


def _flip_first_last(name: str) -> str:
    """"Leon G. Cooperman" → "Cooperman Leon G." (the renderer reads LAST FIRST [M])."""
    toks = name.replace(",", " ").split()
    return " ".join(toks[-1:] + toks[:-1]) if len(toks) >= 2 else name


#: The data vendor in any spelling — never in a public text (no vendor credit, §1). A substring
#: match on purpose: the leak test refuses "fmp" anywhere in a fact sheet.
_VENDOR_RE = re.compile(r"fmp|financial\s*modeling\s*prep", re.IGNORECASE)
_TEXT_URL_RE = re.compile(r"(?:https?:|www\.|://|@)", re.IGNORECASE)
#: A hand-kept stake text (its source title, background, local listing) longer than this is junk.
STAKE_SOURCE_TITLE_MAX = 120
STAKE_BACKGROUND_MAX = 90
STAKE_LOCAL_LISTING_MAX = 40
#: The round-3 review of 2026-10-20 (RR3-STAKES-TEMPLATE-WALL): what the company_stakes template can DRAW, copied
#: from `news_templates` (which the adapter may not import: tests/test_marketing_import_boundary.py) and pinned EQUAL
#: to its twin there by a test, so the adapter refuses at the read every stake text the template must refuse. The
#: source title is drawn whole on the `pair` image, card 4 and every caption: at most `STAKE_SOURCE_DRAW_MAX_CHARS`
#: characters (`news_templates.STAKE_SOURCE_MAX_CHARS`) with no word longer than `STAKE_TEXT_WORD_MAX_CHARS`
#: (`news_templates.NAME_WORD_MAX_CHARS`) — the junk bound above (`STAKE_SOURCE_TITLE_MAX`) is longer, and the
#: template refused what lay between. The same word cap holds a local listing (card 3) to at most
#: `COMPANY_NAME_CHARS[1]` characters, the bound the template reads from this module. An investee's (and an
#: investor's) name sits under its plate in HALF the column: no word longer than `STAKE_PAIR_NAME_WORD_MAX_CHARS`
#: (`news_templates.PAIR_NAME_WORD_MAX_CHARS`; a word of exactly that length is drawn). A word is a run between
#: spaces, as the template counts it ("Sherwin-Williams" is one word of 16). Before, the read passed such stakes,
#: the template refused each one every day, and five of them at the head of the newest-first walk walled the
#: series (the review's probes: a shared 93-character title; investee names with a 16-18 character word).
STAKE_SOURCE_DRAW_MAX_CHARS = 80
STAKE_TEXT_WORD_MAX_CHARS = 20
STAKE_PAIR_NAME_WORD_MAX_CHARS = 15


def free_text_ok(text: Any, *, lo: int = 1, hi: int) -> bool:
    """A hand-kept free text a news record may carry (a stake's source title, background or
    local listing): a str of ``lo..hi`` chars, unpadded, one clean line, no URL, e-mail or
    autolinking domain, no vendor name, no banned or forecast wording. Pure, linear."""
    return (
        isinstance(text, str)
        and lo <= len(text) <= hi
        and text == text.strip()
        and "  " not in text
        and _single_line_clean(text)
        and not _TEXT_URL_RE.search(text)
        and not _x_link_tokens(text)
        and not _VENDOR_RE.search(text)
        and not _copy_rules.contains_banned_copy(text)
        and not _copy_rules.contains_forecast(text)
        and not _NAME_EXTRA_BANNED_RE.search(text)
    )


def longest_word(text: Any) -> int:
    """The length of the longest word of ``text`` — a run between whitespace, the template's own measure
    (`news_templates._longest_word`: "Sherwin-Williams" and "10-Q/A-Exhibit-99.1" are one word each); 0 for a
    non-string or blank text. Pure, linear."""
    return max((len(w) for w in text.split()), default=0) if isinstance(text, str) else 0


#: Surnames of the people a stake's hand-kept source is most likely to cite — the leaders, founders and
#: investors of the Trillion Club's investors and investees, and the best-known fund managers. The review of
#: 2026-10-16 (ST-1): the stake person check (`news_templates._stake_person_problem`) leaned on lexicons built
#: for lessons and missed exactly these — "Dario Amodei essay", "Altman blog", "Mira Murati interview",
#: "Ilya Sutskever interview", "Ellison interview", "Abel letter" all passed, and the source line is drawn on the
#: image, card 4 and every caption (a name is never drawn: marketing.md §1 decision 2). ON TOP OF that check,
#: never instead of it, and only for the three stake texts it reads (`stake_person_surname_hits`). Matched as a
#: whole word in its Title or UPPER form, AFTER the record's own companies are masked — so a listed investee
#: named for a person ("Charles Schwab") still passes. Left out on purpose: surnames that are everyday words or
#: other companies' names a source would cite ("Wood", "Page", "Dell"). Accepted over-block: an unlisted
#: investee such as "S.C. Johnson & Son" ("Son") is refused — one stake post lost, never a name drawn. Add a
#: surname when the catalogue gains a source that names a new person.
STAKE_SOURCE_PERSON_SURNAMES: FrozenSet[str] = frozenset({
    # AI labs and their backers
    "Amodei", "Altman", "Brockman", "Murati", "Sutskever", "Friar", "Krieger", "Hassabis", "Suleyman",
    "Musk", "Shotwell",
    # the largest listed companies
    "Huang", "Kress", "Nadella", "Hood", "Gates", "Ballmer", "Pichai", "Porat", "Ashkenazi", "Brin",
    "Zuckerberg", "Bezos", "Jassy", "Olsavsky", "Cook", "Parekh", "Ellison", "Catz", "Son", "Tan", "Wei",
    "Benioff", "Saylor",
    # Berkshire Hathaway
    "Buffett", "Munger", "Abel", "Jain", "Combs", "Weschler",
    # banks and fund managers
    "Dimon", "Fink", "Dalio", "Ackman", "Icahn", "Soros", "Druckenmiller", "Tepper", "Burry", "Thiel",
})
#: One alternation over the list's Title and UPPER forms, never inside a longer word ("Sons", "Abelson",
#: "Cookware"); a possessive or a hyphen after it still matches ("Buffett's", "Altman-led").
_STAKE_SURNAME_RE = re.compile(
    r"(?<![A-Za-z])(?:"
    + "|".join(sorted({re.escape(s) for n in STAKE_SOURCE_PERSON_SURNAMES for s in (n, n.upper())},
                      key=lambda s: (-len(s), s)))
    + r")(?![A-Za-z])")


#: The re-review of 2026-10-19 (ST-1-R): the surname list above is a DENYLIST, and a source naming anyone off it —
#: "Alexandr Wang memo", "Jensen keynote", "Elon post on X", "Mensch interview", "Intrator letter" — composed with
#: the name on the image, card 4 and every caption. So the adapter now also holds a stake's hand-kept SOURCE TITLE
#: and LOCAL LISTING to a POSITIVE list (`stake_text_unknown_words`): every capitalised word must be a word of a
#: company of the club or the stake catalogue (the adapter passes them, `stake_known_words`), a document, date or
#: place word below, a code with a digit (10-Q, 13G/A, FY2025, Q2) or a single letter (Series D). Lower-case words
#: are never names. Built from the live catalogue's 81 source titles and 10 local listings (2026-10-19), which all
#: pass. A new source shape adds its word here (one stake post is refused until then, logged — never a name drawn).
STAKE_SOURCE_WORDS: FrozenSet[str] = frozenset(w.casefold() for w in (
    # documents and their parts
    "Form", "Forms", "Schedule", "Annual", "Report", "Reports", "Release", "Releases", "Note", "Notes", "Series",
    "Exhibit", "Ex", "Proxy", "Statement", "Statements", "Letter", "Prospectus", "Interim", "Consolidated",
    "Financial", "Half-year", "Audit", "Media", "Joint", "Shareholder", "Shareholders", "Shareholding", "Pattern",
    "Large", "Draft", "Red", "Herring", "Abridged", "Updated", "Amended", "Press", "Announcement", "Filing",
    "Registration", "Supplement", "Quarterly", "Earnings", "Investor", "Presentation", "Stock", "Data", "Page",
    "Summary", "Results", "Fiscal", "Year", "Quarter", "Integrated", "Substantial", "Allotment", "Share", "Notice",
    "Circular", "Disclosure", "Holdings", "Company", "Co", "Inc", "Corp", "Ltd", "Group",
    # filing systems, regulators and document codes without a digit
    "SEC", "EDGAR", "DART", "FSA", "IR", "DEF", "AGM", "ADR", "ADS", "IPO", "SAFE", "JV", "CEO", "CFO",
    # Berkshire Hathaway's insurer (National Indemnity Company) and the LSE's own acronym, as the sources write them
    "NICO", "LSEG",
    # months
    "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Sept", "Oct", "Nov", "Dec", "January", "February",
    "March", "April", "June", "July", "August", "September", "October", "November", "December",
    # places and exchanges (a local listing; "Japan's FSA")
    "Japan", "Korea", "China", "India", "Saudi", "Arabia", "Taiwan", "London", "Switzerland", "Austria", "Germany",
    "France", "Netherlands", "Canada", "Israel", "Singapore", "Hong", "Kong", "Australia", "Brazil", "Mexico",
    "Europe", "Asia", "America", "United", "States", "Kingdom", "U.S", "U.K", "US", "UK", "EU", "Texas",
    "Louisiana", "Shenzhen", "Shanghai", "Tokyo", "Taipei", "Seoul", "Mumbai", "Exchange", "KOSDAQ", "KOSPI",
    "Nasdaq", "NYSE", "TSX", "Euronext",
    # companies a live source names beside the stake's own (not in the club or the catalogue)
    "Sony",
))
_STAKE_WORD_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9'’&.\-/]*")
_STAKE_TEXT_MAX = 400


def _stake_word_key(token: str) -> str:
    """A stake-text token as the positive list compares it: trailing punctuation and a possessive dropped."""
    key = token.rstrip(".,;:")
    for tail in ("'s", "’s", "'", "’"):
        if key.endswith(tail):
            key = key[: -len(tail)]
            break
    return key


def stake_known_words(names: Iterable[Any]) -> FrozenSet[str]:
    """The casefolded words of every name given (the club's companies and the stake catalogue's investees: the
    companies a stake source may name). Non-strings are skipped. Pure."""
    out = set()
    for name in names:
        if isinstance(name, str):
            for tok in _STAKE_WORD_RE.findall(name[:_STAKE_TEXT_MAX]):
                key = _stake_word_key(tok)
                if key:
                    out.add(key.casefold())
    return frozenset(out)


def stake_text_unknown_words(text: Any, known: Iterable[str]) -> List[str]:
    """The capitalised words of a stake's source title or local listing that are on no list (ST-1-R, above) — a
    word of ``known`` (`stake_known_words`), `STAKE_SOURCE_WORDS`, a code with a digit or a single letter is
    allowed; [] for a non-string. The caller refuses the source title (and drops the local listing) when any is
    left: a person's name is never drawn. Pure, linear (texts are capped)."""
    if not isinstance(text, str):
        return []
    known_cf = known if isinstance(known, (set, frozenset)) else frozenset(known)
    out: List[str] = []
    for tok in _STAKE_WORD_RE.findall(text[:_STAKE_TEXT_MAX]):
        key = _stake_word_key(tok)
        if not key or not key[0].isupper() or len(key) == 1 or any(ch.isdigit() for ch in key):
            continue
        cf = key.casefold()
        if cf in STAKE_SOURCE_WORDS or cf in known_cf:
            continue
        out.append(key)
    return out


def stake_person_surname_hits(text: Any) -> List[str]:
    """The `STAKE_SOURCE_PERSON_SURNAMES` a stake text names (whole words, Title or UPPER form), in order of
    appearance; [] for a non-string. The caller masks the record's own companies first. Pure, linear (one
    alternation of literals, lengths capped by the stake text bounds)."""
    if not isinstance(text, str):
        return []
    return [m.group(0) for m in _STAKE_SURNAME_RE.finditer(text)]


def theme_title_problem(title: Any) -> Optional[str]:
    """Why an Emerging Frontiers theme title cannot name a Theme Explainer, or None: the company
    name alphabet and hygiene (`company_name_problem`, up to 40 chars) — ``theme_title_unusable``
    for every failure."""
    if not isinstance(title, str) or not (2 <= len(title) <= 40) or not title.isascii():
        return "theme_title_unusable"
    if (
        not _COMPANY_NAME_RE.fullmatch(title)
        or not any(ch.isalpha() for ch in title)
        or title != title.strip()
        or "  " in title
        or not _single_line_clean(title)
        or _x_link_tokens(title)
        or _VENDOR_RE.search(title)
        or _copy_rules.contains_banned_copy(title)
        or _copy_rules.contains_forecast(title)
        or _NAME_EXTRA_BANNED_RE.search(title)
    ):
        return "theme_title_unusable"
    return None


# ── people: the fail-closed name renderer ─────────────────────────────────────

_PARTICLES = frozenset(
    "van von de der den del della di da dos du la le st saint bin al el".split()
)
_SUFFIXES = frozenset("jr sr ii iii iv v md phd esq cpa".split())
_ENTITY_WORDS = frozenset((
    "llc inc lp llp trust trustee fund funds capital partners holdings corp co ltd group "
    "foundation estate family management investments advisors associates bank company "
    "revocable irrevocable living"
).split())
#: `is_entity_reporter` also refuses these (legal forms and fund words a person's name never
#: carries; "L.P." / "L.L.C." / "S.A." fold to "lp" / "llc" / "sa" once the dots are gone).
_ENTITY_REPORTER_EXTRA = frozenset((
    "enterprises ventures limited plc master offshore lllp gp sa nv ag advisers adviser "
    "corporation incorporated partnership holding investment investors trustees"
).split())
_REPORTER_TOKEN_RE = re.compile(r"[a-z]+")
#: A reporting name longer than this is junk; checked BEFORE any tokenising.
_REPORTER_RAW_MAX = 200


def is_entity_reporter(raw: Any) -> bool:
    """Is a Form 4 reporting name an ENTITY (a fund, LLC, trust, partnership, estate) rather
    than a natural person? Activist and PE funds with a board designee file with the Director
    box checked ("STARBOARD VALUE LP", "TRIAN FUND MANAGEMENT, L.P."): FMP then says "director",
    and role-only copy would publish "A Disney director disclosed buying $110 million" about a
    fund. NFKC, case-folded, dots deleted ("L.P." → "lp"), split on non-letters; True when any
    token — or a run of single letters joined ("L P" → "lp") — is an entity word. Fail closed:
    a non-string, an over-long or a letterless name is an entity (never shown as a person)."""
    if not isinstance(raw, str) or len(raw) > _REPORTER_RAW_MAX:
        return True
    s = unicodedata.normalize("NFKC", raw).casefold().replace(".", "")
    tokens = _REPORTER_TOKEN_RE.findall(s)
    if not tokens:
        return True
    words = set(tokens)
    run: List[str] = []
    for tok in tokens + [""]:
        if len(tok) == 1:
            run.append(tok)
            continue
        if len(run) >= 2:
            words.add("".join(run))
        run = []
    return bool(words & (_ENTITY_WORDS | _ENTITY_REPORTER_EXTRA))


_HONORIFIC_RE = re.compile(r"^(?:mr|mrs|ms|dr|prof|sir)\.?\s+")
_PERSON_CHARS_RE = re.compile(r"[A-Za-z .,\-]+")
_LAST_RE = re.compile(r"[A-Za-z]+(?:-[A-Za-z]+)?")
_FIRST_RE = re.compile(r"[A-Za-z]+")
_INITIAL_RE = re.compile(r"[A-Za-z]\.?")


def render_person_name(raw: Any, *, company: Optional[str] = None) -> Optional[str]:
    """A Form 4 reporting name as "First [M.] Last", or None (the template then says the role
    only). Fail closed — the intersection of both designs' rule sets (contract D4.3):

    1. a str; after NFKC + whitespace collapse, 3..60 ASCII chars of letters, spaces, ".", ","
       and "-" only (an apostrophe, accent or digit → None);
    2. exactly ``LAST FIRST [M]`` or ``Last, First [M.]``; the third token is a single letter;
    3. LAST: letters with at most one internal hyphen, ≥ 2 letters, not a particle, suffix or
       entity word, no part starting MC/MAC, and NOT a given name (order evidence);
    4. FIRST: ≥ 3 letters and a given name (`compliance.given_names()`; a missing list → None);
    5. all-caps input is title-cased per part; mixed-case input is kept only when every token
       (and hyphen part) starts with a capital and no multi-letter token is all-caps;
    6. ≤ 40 chars, and its fold differs from ``company`` (when given).

    Every person slot is role-only while the Congress block-list is unusable
    (`person_names_allowed()`). This renderer does NOT consult the roster itself: a row whose
    reporting name is a member of Congress must be DROPPED (`is_congress_name`), never shown
    role-only — the role would still point at the member."""
    if not isinstance(raw, str) or not person_names_allowed():
        return None
    s = " ".join(unicodedata.normalize("NFKC", raw).split())
    if not (3 <= len(s) <= 60) or not s.isascii() or not _PERSON_CHARS_RE.fullmatch(s):
        return None
    if s.count(",") > 1:
        return None
    if "," in s:
        head, tail = s.split(",")
        head_tokens, tail_tokens = head.split(), tail.split()
        if len(head_tokens) != 1 or not tail_tokens:
            return None
        tokens = head_tokens + tail_tokens
    else:
        tokens = s.split()
    if len(tokens) not in (2, 3):
        return None
    last, first = tokens[0], tokens[1]
    middle = tokens[2] if len(tokens) == 3 else None
    if not _LAST_RE.fullmatch(last) or not _FIRST_RE.fullmatch(first):
        return None
    if middle is not None and not _INITIAL_RE.fullmatch(middle):
        return None
    low_last, low_first = last.lower(), first.lower()
    parts = low_last.split("-")
    if sum(len(p) for p in parts) < 2:
        return None
    words = [*parts, low_last, low_first] + ([middle.rstrip(".").lower()] if middle else [])
    if any(w in _PARTICLES or w in _SUFFIXES or w in _ENTITY_WORDS for w in words):
        return None
    if any(p.startswith(("mc", "mac")) for p in parts):
        return None
    given = _compliance.given_names()
    if low_last in given:
        return None
    if len(first) < 3 or low_first not in given:
        return None
    letters = [ch for ch in s if ch.isalpha()]
    if all(ch.isupper() for ch in letters):
        last_out = "-".join(p.capitalize() for p in parts)
        first_out = first.capitalize()
    else:
        pieces = [*last.split("-"), first] + ([middle] if middle else [])
        if any(not p[0].isupper() for p in pieces):
            return None
        if any(len(p) >= 2 and p.isupper() for p in [*last.split("-"), first]):
            return None
        last_out, first_out = last, first
    mid_out = f" {middle[0].upper()}." if middle else ""
    out = f"{first_out}{mid_out} {last_out}"
    if len(out) > PERSON_NAME_MAX:
        return None
    if company is not None and _compliance.fold(out) == _compliance.fold(str(company)):
        return None
    return out


_TOKEN_RE = re.compile(r"[a-z0-9]+(?:['-][a-z0-9]+)*")


def _name_tokens(text: Any) -> List[str]:
    """Folded (accent-free, lower-case) word tokens; a possessive "'s" is dropped."""
    if not isinstance(text, str):
        return []
    out = []
    for tok in _TOKEN_RE.findall(_compliance.fold(text)):
        if tok.endswith("'s"):
            tok = tok[:-2]
        if tok:
            out.append(tok)
    return out


def corroborates(rendered: Any, profile_ceo: Any) -> bool:
    """CEO only: does the profile's ``ceo`` name (a leading honorific stripped) contain BOTH the
    rendered first and last names as whole tokens? Otherwise the row's person_name is None."""
    if not isinstance(rendered, str) or not isinstance(profile_ceo, str):
        return False
    mine = _name_tokens(rendered)
    if len(mine) < 2:
        return False
    theirs = set(_name_tokens(_HONORIFIC_RE.sub("", _compliance.fold(profile_ceo))))
    return mine[0] in theirs and mine[-1] in theirs


# ── members of Congress: the block-list ───────────────────────────────────────

CONGRESS_ROSTER_PATH = _compliance.DATA_DIR / "congress_roster.json"
WHALE_REGISTRY_PATH = _compliance.DATA_DIR / "whale_registry.json"
#: A roster with fewer usable members than one full Congress is truncated, not trusted.
ROSTER_MIN_MEMBERS = 535
#: The registry must still list its politicians (11 on 2026-10-09).
REGISTRY_MIN_POLITICIANS = 1
_MAX_NAME_TOKENS = 6
#: The roster's age, from its ``fetched_on`` to the run date (review round 9). Past
#: `ROSTER_WARN_AGE_DAYS` every Form 4 run logs ERROR (refresh it); past `ROSTER_MAX_AGE_DAYS` —
#: or with no readable ``fetched_on`` — the roster is not fresh and both Form 4 series are refused
#: (`roster_fresh_for`). Special elections add members between Congresses, which only a refresh
#: brings in: refresh with `scripts/refresh_congress_roster.py` after each.
ROSTER_WARN_AGE_DAYS = 180
ROSTER_MAX_AGE_DAYS = 400
#: Review round 11 (main-session decision 2026-10-10; it replaces round 10's 14-day grace, which
#: trusted ``fetched_on`` — the day the script ran, not what the data held): the roster's
#: ``congress_start`` is the odd-year January 3 that began the Congress its current members serve
#: in, computed by the refresh script FROM THE SOURCE. On a run date whose sitting Congress began
#: later (a new Congress is sworn in every odd-year January 3), the roster cannot know that
#: Congress's members and the Form 4 series are refused — no grace — until the roster is refreshed
#: from a dataset that lists them.

#: Given-name equivalences (review round 9). A Form 4 reporting name is the LEGAL name ("MANCHIN
#: JOSEPH", "COTTON THOMAS B", "GOTTHEIMER JOSHUA S"); the roster stores the name a member goes by
#: ("Joe", "Tom", "Josh"). Each line is one group of interchangeable given names: a reporter's
#: given name in a member's group, under the member's surname, is that member (fail closed —
#: over-block costs one Form 4 row, a miss names a member of Congress). Pinned by the rules tests;
#: grow it, never shrink it.
NICKNAME_GROUPS: Tuple[FrozenSet[str], ...] = tuple(frozenset(line.split()) for line in (
    "charles chuck charlie chip chaz",
    "william bill billy will willie wm",
    "robert bob bobby rob robbie bert",
    "james jim jimmy jamie",
    "michael mike mikey mick",
    "thomas tom tommy",
    "richard dick rick ricky rich richie",
    "edward ted teddy ed eddie ned",
    "theodore ted teddy theo",
    "joseph joe joey",
    "daniel dan danny",
    "david dave davey",
    "steven stephen steve stevie",
    "timothy tim timmy",
    "patrick pat paddy",
    "patricia pat patty patsy trish",
    "elizabeth liz beth betsy betty eliza libby lisa lizzie",
    "katherine catherine kathryn kate kathy katie kat cathy",
    "andrew andy drew",
    "anthony tony",
    "antonio tony",                 # review round 10: "CARDENAS ANTONIO" (Tony Cárdenas)
    "rudolph rudy",                 # review round 10: "YAKYM RUDOLPH" (Rudy Yakym III)
    "gregory greg",
    "ronald ron ronnie",
    "donald don donnie",
    "kenneth ken kenny",
    "jeffrey geoffrey jeff",
    "christopher chris kit",
    "christine christina chris tina",
    "matthew matt",
    "nicholas nick nicky",
    "samuel sam sammy",
    "samantha sam",
    "benjamin ben benny",
    "joshua josh",
    "jacob jake",
    "john jack johnny jon",
    "jonathan jon jonny",
    "henry hank harry",
    "harold harry hal",
    "lawrence laurence larry",
    "gerald jerry gerry",
    "jerome jerry",
    "margaret peggy maggie meg marge",
    "mary molly polly mamie",
    "sarah sara sally",
    "abraham abe",
    "gabriel gabe",
    "rebecca becca becky",
    "bernard bernardo bernie",
    "rohit ro",
    "dustin dusty",
    "raymond ray",
    "frederick fred freddie",
    "alexander alex sandy al",
    "alexandra alexandria alex sandy",
    "albert al bert",
    "alan allan allen al",
    "douglas doug",
    "terrence terence terry",
    "vincent vince",
    "phillip philip phil",
    "louis lou luis",
    "zachary zach zack",
    "valerie val",
    "jennifer jen jenny",
    "susan sue susie",
    "deborah debra debbie deb",
    "cynthia cindy",
    "kimberly kim",
    "pamela pam",
    "victoria vicky tori",
    "wesley wes",
    "francis frank franklin",
    "leonard len lenny leo",
    "mitchell mitch",
    "randal randall randy rand",
    "eugene gene",
    "walter walt",
    "clifford cliff",
    "bradley brad",
    "frances fran",
))
#: Members known by a name unrelated to their LEGAL first name (no nickname group can link
#: "Mitt" to "Willard"): folded "first last" as the roster spells it → the legal given names.
MEMBER_LEGAL_ALIASES: Dict[str, Tuple[str, ...]] = {
    "mitt romney": ("willard",),
    "ted cruz": ("rafael",),
    "pete ricketts": ("john",),
    "mike rounds": ("marion",),
    "mitch mcconnell": ("addison",),
    "jon tester": ("raymond",),
    "tammy duckworth": ("ladda",),
    "burgess owens": ("clarence",),
    # review round 10: Form 4 legal names unrelated to the name the member goes by
    "mike kelly": ("george",),             # "KELLY GEORGE J JR"
    "trey hollingsworth": ("joseph",),     # "HOLLINGSWORTH JOSEPH A III"
    "mac thornberry": ("william",),        # "THORNBERRY WILLIAM" (only "… M" was blocked)
}
_NICK_INDEX: Dict[str, FrozenSet[str]] = {}
for _group in NICKNAME_GROUPS:
    for _n in _group:
        _NICK_INDEX[_n] = _NICK_INDEX.get(_n, frozenset()) | _group
#: Two given names sharing this many leading letters are one name for the block-list ("Dusty" /
#: "Dustin"); a short name that is a prefix of the other ("Ro" / "Rohit", "Josh" / "Joshua") is too.
_GIVEN_COMMON_PREFIX = 4


def _name_keys(text: Any) -> List[str]:
    """Matching keys for one name: its folded tokens joined, with initials and generational
    suffixes dropped as a second form. Keys of one token are never produced."""
    toks = _name_tokens(text)
    core = [t for t in toks if len(t) > 1 and t not in _SUFFIXES]
    keys = []
    for form in (toks, core):
        if len(form) >= 2:
            keys.append(" ".join(form))
    return keys


def _member_keys(member: Mapping[str, Any]) -> List[str]:
    first, last = member.get("first"), member.get("last")
    if not isinstance(first, str) or not isinstance(last, str):
        return []
    nick = member.get("nickname") if isinstance(member.get("nickname"), str) else ""
    official = member.get("official_full") if isinstance(member.get("official_full"), str) else ""
    firsts = []
    base = re.sub(r"\(.*?\)", " ", first)
    for f in [base, *re.findall(r"\((.*?)\)", first), nick]:
        toks = [t for t in _name_tokens(f) if len(t) > 1]
        if toks:
            firsts.append(" ".join(toks))
            firsts.append(toks[0])
    lasts = [" ".join(t for t in _name_tokens(last) if t not in _SUFFIXES)]
    keys = [f"{f} {l}" for f in firsts for l in lasts if f and l]
    if official:
        keys += _name_keys(official.replace('"', " "))
        core = [t for t in _name_tokens(official.replace('"', " ")) if len(t) > 1 and t not in _SUFFIXES]
        if len(core) >= 2:
            keys.append(f"{core[0]} {core[-1]}")
    return keys


def _with_hyphen_forms(keys: Iterable[str]) -> List[str]:
    out = []
    for k in keys:
        if len(k.split()) < 2:
            continue
        out.append(k)
        if "-" in k:
            out.append(k.replace("-", " "))
    return out


# ── the surname rule (review round 9) ─────────────────────────────────────────
#
# The exact keys above miss a member who files a Form 4 under a LEGAL name the roster does not
# hold ("GOTTHEIMER JOSHUA S" for "Josh Gottheimer", "ROMNEY WILLARD M" for "Mitt Romney"). So a
# name ALSO matches a member when one of its words (or runs of words) is the member's SURNAME and
# another of its words is one of the member's given names — equal, in the same `NICKNAME_GROUPS`
# group, a prefix of it ("Ro" / "Rohit"), sharing `_GIVEN_COMMON_PREFIX` letters with it, a legal
# alias (`MEMBER_LEGAL_ALIASES`) — or a single INITIAL of one of those (the "M" of "ROMNEY
# WILLARD M"), or any word starting with the initial a member's legal name begins with ("J.
# French Hill" → "HILL JAMES"). A surname alone never matches.


@dataclass(frozen=True)
class _Member:
    given: FrozenSet[str]          # every given name (2+ letters), with its nickname groups and aliases
    named: FrozenSet[str]          # the given names the roster / registry spells out (no expansions)
    initials: FrozenSet[str]       # initials of the names the member GOES BY (and their groups)
    lead: Optional[str]            # the initial a legal form begins with ("J. French Hill" → "j")


def _expand(names: Iterable[str]) -> FrozenSet[str]:
    out = set()
    for n in names:
        out.add(n)
        out |= _NICK_INDEX.get(n, frozenset())
    return frozenset(out)


def _surname_forms(tokens: Sequence[str]) -> List[Tuple[str, ...]]:
    """A surname's lookup forms: its tokens, hyphens split, apostrophes dropped, the words run
    together ("De La Cruz" → "delacruz") and its last word ("cruz") when it has particles."""
    toks = [t for t in tokens if t not in _SUFFIXES]
    if not toks:
        return []
    forms = {tuple(toks)}
    split = tuple(p for t in toks for p in t.split("-") if p)
    forms.add(split)
    forms.add(tuple(t.replace("'", "") for t in toks))
    if len(split) > 1:
        forms.add(("".join(t.replace("'", "") for t in split),))
        # Each word of a compound surname alone ("SCHULTZ DEBORAH WASSERMAN"), never a particle.
        forms.update((t,) for t in split if t not in _PARTICLES and len(t) > 1)
    return [f for f in forms if f and all(f)]


def _member_record(primary: Sequence[str], others: Sequence[str], legal_lead: Optional[str],
                   aliases: Sequence[str] = ()) -> Optional[_Member]:
    primary_full = [t for t in primary if len(t) > 1]
    named = frozenset(primary_full + [t for t in others if len(t) > 1] + list(aliases))
    if not named and legal_lead is None:
        return None
    given = _expand(named)
    initials = frozenset(t[0] for t in (*primary, *_expand(primary_full), *aliases) if t)
    return _Member(given=given, named=named, initials=initials, lead=legal_lead)


def _roster_member(m: Mapping[str, Any]) -> List[Tuple[Tuple[str, ...], _Member]]:
    first, last = m.get("first"), m.get("last")
    if not isinstance(first, str) or not isinstance(last, str):
        return []
    nick = m.get("nickname") if isinstance(m.get("nickname"), str) else ""
    official = m.get("official_full") if isinstance(m.get("official_full"), str) else ""
    # Review round 10: the dataset's MIDDLE name is a given name too (a member known by it files
    # under first + middle) — indexed as a spelled-out given name, never as an initial.
    middle = m.get("middle") if isinstance(m.get("middle"), str) else ""
    last_toks = [t for t in _name_tokens(last) if t not in _SUFFIXES]
    if not last_toks:
        return []
    primary: List[str] = []
    for f in [re.sub(r"\(.*?\)", " ", first), *re.findall(r"\((.*?)\)", first), nick]:
        primary += _name_tokens(f.replace(".", ". "))
    off = _name_tokens(official.replace('"', " ").replace(".", ". "))
    quoted = [t for q in re.findall(r'"(.*?)"', official) for t in _name_tokens(q)]
    if off:
        primary.append(off[0])
    primary += quoted
    surname = set(last_toks) | {p for t in last_toks for p in t.split("-")}
    others = [t for t in (*off, *_name_tokens(middle.replace(".", ". ")))
              if t not in surname and t not in _SUFFIXES]
    lead = None
    for form in (first, official):
        toks = _name_tokens(form.replace(".", ". "))
        # A legal form that begins with an initial ("J. French Hill"), or with two letters and no
        # vowel — initials or an abbreviation ("TJ" Cox, "Wm." Lacy Clay).
        if toks and (len(toks[0]) == 1 or (len(toks[0]) == 2 and not set(toks[0]) & set("aeiouy"))):
            lead = toks[0][0]
            break
    first_key = " ".join(t for t in _name_tokens(re.sub(r"\(.*?\)", " ", first)) if len(t) > 1)
    aliases = MEMBER_LEGAL_ALIASES.get(f"{first_key} {' '.join(last_toks)}", ())
    rec = _member_record(primary, others, lead, aliases)
    return [(f, rec) for f in _surname_forms(last_toks)] if rec else []


def _registry_member(name: str) -> List[Tuple[Tuple[str, ...], _Member]]:
    toks = [t for t in _name_tokens(name) if t not in _SUFFIXES]
    if len(toks) < 2:
        return []
    out = []
    for n_last in (1, 2):
        if len(toks) - n_last < 1:
            continue
        given, last = toks[:-n_last], toks[-n_last:]
        rec = _member_record(given[:1], given[1:], None)
        if rec:
            out += [(f, rec) for f in _surname_forms(last)]
    return out


def _given_related(tok: str, member: _Member) -> bool:
    """Is ``tok`` (one word of a name, not the surname) one of ``member``'s given names?"""
    if not tok:
        return False
    if len(tok) == 1:
        return tok in member.initials
    if tok in member.given:
        return True
    if tok in _PARTICLES:
        return False              # "de", "la", "al": a given name only when it IS one ("Al Green")
    if member.lead is not None and tok[0] == member.lead:
        return True
    for g in member.given:
        short, long_ = (tok, g) if len(tok) <= len(g) else (g, tok)
        if len(short) >= 2 and long_.startswith(short):
            return True
        if len(short) >= _GIVEN_COMMON_PREFIX and tok[:_GIVEN_COMMON_PREFIX] == g[:_GIVEN_COMMON_PREFIX]:
            return True
    return False


#: The longest surname, in words, the surname rule looks up ("de la cruz").
_MAX_SURNAME_TOKENS = 3


def _surname_hit(tokens: Sequence[str], surnames: Mapping[Tuple[str, ...], Tuple[_Member, ...]]) -> bool:
    """A name's words (folded, at most `_MAX_NAME_TOKENS`): does a run of them name a member's
    surname while ANOTHER of its words is one of that member's given names (`_given_related`)?"""
    variants = {tuple(tokens), tuple(p for t in tokens for p in t.split("-") if p),
                tuple(t.replace("'", "") for t in tokens)}
    for toks in variants:
        for i in range(len(toks)):
            for n in range(1, _MAX_SURNAME_TOKENS + 1):
                if i + n > len(toks):
                    break
                members = surnames.get(tuple(toks[i:i + n]))
                if not members:
                    continue
                rest = [t for t in (*toks[:i], *toks[i + n:]) if t not in _SUFFIXES]
                if any(_given_related(t, m) for m in members for t in rest):
                    return True
    return False


@dataclass(frozen=True)
class _CongressState:
    names: FrozenSet[str]
    ok: bool
    surnames: Mapping[Tuple[str, ...], Tuple[_Member, ...]]
    fetched_on: Optional[date]
    # Review round 11: the odd-year January 3 that began the Congress the roster's current members
    # serve in (`_roster_congress_start`), or None when missing or invalid.
    congress_start: Optional[date] = None

    # `_congress_state()[0]` / `[1]` kept their meaning (names, usable) for older readers.
    def __getitem__(self, i: int) -> Any:
        return (self.names, self.ok)[i]


def _roster_congress_start(raw: Any, fetched_on: Optional[date]) -> Optional[date]:
    """The roster's ``congress_start`` as a day, or None (logged at ERROR) unless it is an ISO date
    that IS an odd-year January 3 and is not after ``fetched_on`` (a dataset cannot hold the
    members of a Congress not yet sworn in when it was fetched; an undated roster has no
    ``congress_start`` either). Fail closed: None makes `roster_fresh_for` False."""
    if raw is None:
        return None              # `roster_fresh_for` logs the missing field on every Form 4 run
    day: Optional[date] = None
    if isinstance(raw, str):
        try:
            day = date.fromisoformat(raw.strip())
        except ValueError:
            day = None
    problem = None
    if day is None:
        problem = "is not an ISO date"
    elif (day.month, day.day) != (1, 3) or day.year % 2 == 0:
        problem = "is not an odd-year January 3 (the day a Congress is sworn in)"
    elif fetched_on is None or day > fetched_on:
        problem = f"is not on or before the roster's fetched_on ({fetched_on})"
    if problem is not None:
        logger.error("company news: congress roster congress_start %r %s — treated as missing; the Form 4 "
                     "series are refused until it is refreshed", str(raw)[:40], problem)
        return None
    return day


def _read_roster() -> Tuple[List[str], bool, List[Tuple[Tuple[str, ...], _Member]], Optional[date],
                            Optional[date]]:
    """(name keys, usable, surname entries, fetched_on, congress_start)."""
    try:
        data = json.loads(Path(CONGRESS_ROSTER_PATH).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        logger.error("company news: congress roster %s unreadable (%s: %s) — every person slot "
                     "renders role-only", Path(CONGRESS_ROSTER_PATH).name, type(e).__name__, e)
        return [], False, [], None, None
    members = data.get("members") if isinstance(data, dict) else None
    if not isinstance(members, list):
        logger.error("company news: congress roster has no members list — every person slot "
                     "renders role-only")
        return [], False, [], None, None
    fetched_on: Optional[date] = None
    raw_fetched = data.get("fetched_on")
    if isinstance(raw_fetched, str):
        try:
            fetched_on = date.fromisoformat(raw_fetched.strip())
        except ValueError:
            fetched_on = None
    congress_start = _roster_congress_start(data.get("congress_start"), fetched_on)
    keys: List[str] = []
    entries: List[Tuple[Tuple[str, ...], _Member]] = []
    usable = 0
    for m in members:
        if not isinstance(m, dict):
            continue
        mk = _member_keys(m)
        if mk:
            usable += 1
            keys += mk
            entries += _roster_member(m)
    if usable < ROSTER_MIN_MEMBERS:
        logger.error("company news: congress roster lists %d usable members (< %d) — treated as "
                     "truncated; every person slot renders role-only", usable, ROSTER_MIN_MEMBERS)
        return [], False, entries, fetched_on, congress_start
    return keys, True, entries, fetched_on, congress_start


def _read_registry_politicians() -> Tuple[List[str], bool, List[Tuple[Tuple[str, ...], _Member]]]:
    # The `compliance._registry_names` loader pattern, politicians only.
    try:
        rows = json.loads(Path(WHALE_REGISTRY_PATH).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        logger.error("company news: whale registry unreadable (%s: %s) — every person slot "
                     "renders role-only", type(e).__name__, e)
        return [], False, []
    keys: List[str] = []
    entries: List[Tuple[Tuple[str, ...], _Member]] = []
    count = 0
    for row in rows if isinstance(rows, list) else []:
        if isinstance(row, dict) and row.get("category") == "politicians":
            name = str(row.get("name") or "")
            k = _name_keys(name)
            if k:
                count += 1
                keys += k
                entries += _registry_member(name)
    if count < REGISTRY_MIN_POLITICIANS:
        logger.error("company news: whale registry lists no politicians — every person slot "
                     "renders role-only")
        return [], False, entries
    return keys, True, entries


@lru_cache(maxsize=1)
def _congress_state() -> _CongressState:
    registry, registry_ok, reg_entries = _read_registry_politicians()
    roster, roster_ok, roster_entries, fetched_on, congress_start = _read_roster()
    names = frozenset(_with_hyphen_forms(registry + roster))
    surnames: Dict[Tuple[str, ...], List[_Member]] = {}
    # Every member read is indexed, even from a list too short to trust: blocking more is safe.
    for form, member in reg_entries + roster_entries:
        surnames.setdefault(form, []).append(member)
    ok = registry_ok and roster_ok
    if ok:
        logger.info("company news: congress block-list loaded (%d name keys, %d surnames)",
                    len(names), len(surnames))
    return _CongressState(names=names, ok=ok,
                          surnames={k: tuple(v) for k, v in surnames.items()}, fetched_on=fetched_on,
                          congress_start=congress_start)


def congress_names() -> FrozenSet[str]:
    """Folded "first last" (and full-name) keys of every member of Congress serving now or since
    2021-01-03, plus the whale registry's politicians. Never a surname alone."""
    return _congress_state().names


def person_names_allowed() -> bool:
    """False while the block-list could not be built (roster or registry missing, unreadable or
    truncated): every person slot then renders role-only. Logged at ERROR once per process.
    Whether the roster holds the SITTING Congress (and its age) is a separate, run-date check
    (`roster_fresh_for`), on which the Form 4 collectors refuse their series."""
    return _congress_state().ok


def roster_fetched_on() -> Optional[date]:
    """The roster's ``fetched_on`` day, or None when it is missing or unreadable."""
    return _congress_state().fetched_on


def congress_start_on_or_before(day: date) -> date:
    """The most recent odd-year January 3 — the day a new Congress is sworn in — on or before
    ``day``."""
    year = day.year if day.year % 2 == 1 else day.year - 1
    start = date(year, 1, 3)
    return start if start <= day else date(year - 2, 1, 3)


def roster_congress_start() -> Optional[date]:
    """The roster's ``congress_start`` (the odd-year January 3 that began the Congress its current
    members serve in), or None when it is missing or invalid (`_roster_congress_start`)."""
    return _congress_state().congress_start


def roster_fresh_for(run_date: date) -> bool:
    """May the Form 4 series run on ``run_date`` as far as the roster's CONTENT goes (review round 11,
    main-session decision 2026-10-10)? True iff the roster holds the SITTING Congress — its
    ``congress_start`` is on or after the latest odd-year January 3 on or before ``run_date`` (no
    grace) — AND its ``fetched_on`` is at most `ROSTER_MAX_AGE_DAYS` before ``run_date``. False,
    with an ERROR, when either fails or ``congress_start`` / ``fetched_on`` is missing or invalid:
    a list without the sitting Congress cannot know its new members, and role-only would still
    point at one, so the caller refuses the series (never names, never role-only). Past
    `ROSTER_WARN_AGE_DAYS` it is still True but logs an ERROR (refresh the roster). Reads
    ``run_date``, never the wall clock."""
    fetched = roster_fetched_on()
    if fetched is None:
        logger.error("company news: congress roster has no readable fetched_on — its age is unknown; the "
                     "Form 4 series are refused until it is refreshed with scripts/refresh_congress_roster.py")
        return False
    held = roster_congress_start()
    if held is None:
        logger.error("company news: congress roster has no valid congress_start — it cannot show which "
                     "Congress it holds; the Form 4 series are refused until it is refreshed with "
                     "scripts/refresh_congress_roster.py")
        return False
    age = (run_date - fetched).days
    if age > ROSTER_MAX_AGE_DAYS:
        logger.error("company news: congress roster is %d days old on %s (> %d) — the Form 4 series are "
                     "refused; refresh it with scripts/refresh_congress_roster.py",
                     age, run_date, ROSTER_MAX_AGE_DAYS)
        return False
    sitting = congress_start_on_or_before(run_date)
    if held < sitting:
        logger.error("company news: congress roster holds the Congress sworn in on %s, but the Congress "
                     "sworn in on %s sits on %s — it cannot know the new members, so the Form 4 series "
                     "are refused; refresh it with scripts/refresh_congress_roster.py (it refuses a "
                     "dataset that does not list the new Congress yet: retry a few days later)",
                     held, sitting, run_date)
        return False
    if age > ROSTER_WARN_AGE_DAYS:
        logger.error("company news: congress roster is %d days old on %s (> %d) — refresh it with "
                     "scripts/refresh_congress_roster.py before it reaches %d days and the Form 4 "
                     "series are refused", age, run_date, ROSTER_WARN_AGE_DAYS, ROSTER_MAX_AGE_DAYS)
    return True


def is_congress_name(name: Any) -> bool:
    """Is ``name`` — a rendered "First [M.] Last" OR a raw Form 4 reporting name in any order
    ("PELOSI NANCY", "Garcia, Jesus G", "GOTTHEIMER JOSHUA S") — a member of Congress? Two
    rules, both erring toward dropping the row (fail closed):

    * the exact keys: initials and suffixes are ignored and every rotation and ordered pair of
      its words is tried against `congress_names()`;
    * the surname rule (review round 9): a run of its words is a member's surname and another
      word is one of that member's given names — equal, a nickname-group twin, a prefix, a
      legal alias, or a single initial of one (`_given_related`).

    A surname alone never matches."""
    toks = _name_tokens(name)[:_MAX_NAME_TOKENS]
    if len(toks) < 2:
        return False
    state = _congress_state()
    names = state.names
    core = [t for t in toks if len(t) > 1 and t not in _SUFFIXES]
    candidates = set()
    for form in (toks, core):
        for k in range(len(form)):
            rot = form[k:] + form[:k]
            if len(rot) >= 2:
                candidates.add(" ".join(rot))
    for i, a in enumerate(core):
        for j, b in enumerate(core):
            if i != j:
                candidates.add(f"{a} {b}")
    if any(c in names for c in candidates):
        return True
    return _surname_hit([t for t in toks if t not in _SUFFIXES], state.surnames)


#: A word of free text, case kept (ASCII after the accent fold), for the text form of the surname rule.
_CASED_TOKEN_RE = re.compile(r"[A-Za-z0-9]+(?:['\-][A-Za-z0-9]+)*")


def _cased_tokens(text: str) -> List[Tuple[str, bool]]:
    """(folded word, starts with a capital) for each word of ``text``; a possessive "'s" is dropped."""
    s = unicodedata.normalize("NFKD", text)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = s.replace("’", "'").replace("‘", "'")
    out = []
    for m in _CASED_TOKEN_RE.finditer(s):
        w = m.group(0)
        low = w.lower()
        if low.endswith("'s"):
            low = low[:-2]
        if low:
            out.append((low, w[0].isupper()))
    return out


def congress_name_hits(text: Any) -> List[str]:
    """Every block-list name that appears in ``text`` as whole words (folded, accent-free; a
    possessive "'s" ignored), sorted. Linear: word n-grams of 2..6 tokens against a set, plus
    the surname rule for a rendered "First [M.] Last" (review round 9: "Joshua S. Gottheimer",
    "Thomas Cotton"): two capitalised words — the first one of the member's given names (one the
    roster spells out, or a name on the given-names list related to one), an optional single
    initial between — whose second word is a member's surname. A hit by that rule is reported as
    "first last"."""
    toks = _name_tokens(text)
    state = _congress_state()
    names = state.names
    hits = set()
    for n in range(2, _MAX_NAME_TOKENS + 1):
        for i in range(0, len(toks) - n + 1):
            key = " ".join(toks[i:i + n])
            if key in names:
                hits.add(key)
    if not isinstance(text, str) or not state.surnames:
        return sorted(hits)
    cased = _cased_tokens(text)
    given_list = _compliance.given_names()
    for i, (first, cap) in enumerate(cased):
        if not cap or len(first) < 2:
            continue
        j = i + 1
        if j < len(cased) and len(cased[j][0]) == 1:
            j += 1                                   # "Joshua S. Gottheimer": skip the initial
        for n in range(1, _MAX_SURNAME_TOKENS + 1):
            run = cased[j:j + n]
            if len(run) < n or not all(c for _w, c in run):
                break
            members = state.surnames.get(tuple(w for w, _c in run))
            if not members:
                continue
            for m in members:
                if first in m.named or (first in given_list and _given_related(first, m)):
                    hits.add(f"{first} {' '.join(w for w, _c in run)}")
                    break
    return sorted(hits)


# ── record validation helpers ─────────────────────────────────────────────────

def _is_num(v: Any) -> bool:
    if not isinstance(v, (int, float)) or isinstance(v, bool):
        return False
    try:
        return math.isfinite(v)
    except OverflowError:       # an int too large for a float (a JSON integer of 400 digits) is no figure
        return False


def _is_day(v: Any) -> bool:
    return isinstance(v, date) and not isinstance(v, datetime)


class _Check:
    """Field checks for one record's `__post_init__`; every failure is a ValueError naming the
    type and the field."""

    __slots__ = ("rec", "cls")

    def __init__(self, rec: Any) -> None:
        self.rec = rec
        self.cls = type(rec).__name__

    def fail(self, name: str, why: str) -> None:
        raise ValueError(f"{self.cls}.{name}: {why}")

    def get(self, name: str) -> Any:
        return getattr(self.rec, name)

    def text(self, name: str, lo: int, hi: int, *, optional: bool = False) -> None:
        v = self.get(name)
        if v is None and optional:
            return
        if not isinstance(v, str):
            self.fail(name, f"expected str, got {type(v).__name__}")
        if not (lo <= len(v) <= hi):
            self.fail(name, f"length {len(v)} outside {lo}..{hi}")
        if v != v.strip() or not _single_line_clean(v):
            self.fail(name, "not clean single-line text")

    def number(self, name: str, *, gt: Optional[float] = None, ge: Optional[float] = None,
               le: Optional[float] = None, optional: bool = False) -> None:
        v = self.get(name)
        if v is None and optional:
            return
        if not _is_num(v):
            self.fail(name, f"expected a finite non-bool number, got {v!r}")
        if gt is not None and not v > gt:
            self.fail(name, f"{v!r} not > {gt}")
        if ge is not None and not v >= ge:
            self.fail(name, f"{v!r} not >= {ge}")
        if le is not None and not v <= le:
            self.fail(name, f"{v!r} not <= {le}")
        object.__setattr__(self.rec, name, float(v))

    def integer(self, name: str, *, ge: Optional[int] = None, le: Optional[int] = None) -> None:
        v = self.get(name)
        if not isinstance(v, int) or isinstance(v, bool):
            self.fail(name, f"expected int, got {v!r}")
        if ge is not None and v < ge:
            self.fail(name, f"{v!r} < {ge}")
        if le is not None and v > le:
            self.fail(name, f"{v!r} > {le}")

    def day(self, name: str, *, optional: bool = False) -> None:
        v = self.get(name)
        if v is None and optional:
            return
        if not _is_day(v):
            self.fail(name, f"expected a date, got {v!r}")

    def flag(self, name: str) -> None:
        if not isinstance(self.get(name), bool):
            self.fail(name, "expected bool")

    def choice(self, name: str, options: Iterable[str]) -> None:
        if self.get(name) not in tuple(options):
            self.fail(name, f"{self.get(name)!r} not one of {tuple(options)}")

    def items(self, name: str, typ: type, lo: int, hi: int) -> tuple:
        v = self.get(name)
        if not isinstance(v, tuple):
            self.fail(name, f"expected a tuple, got {type(v).__name__}")
        if not (lo <= len(v) <= hi):
            self.fail(name, f"{len(v)} items outside {lo}..{hi}")
        if any(not isinstance(x, typ) for x in v):
            self.fail(name, f"every item must be {typ.__name__}")
        return v


# ── records ───────────────────────────────────────────────────────────────────

Role = Literal["ceo", "cfo", "director"]
Holding = Literal["direct", "mixed", "indirect"]
Move = Literal["newly_reported", "increased", "decreased", "no_longer_reported"]
InsiderSeries = Literal["ceo_buys", "insider_buys"]


@dataclass(frozen=True, slots=True)
class CompanyRef:
    symbol: str   # ^[A-Z]{1,5}(-[A-C])?$, canonical dash form
    name: str     # display_company_name(), 2..32 chars

    def __post_init__(self) -> None:
        c = _Check(self)
        if not isinstance(self.symbol, str) or canonical_symbol(self.symbol) != self.symbol:
            c.fail("symbol", f"{self.symbol!r} is not a canonical common-stock symbol")
        problem = company_name_problem(self.name)
        if problem:
            c.fail("name", problem)
        elif company_name_word_too_long(self.name):
            c.fail("name", f"a word longer than {COMPANY_NAME_WORD_MAX_CHARS} characters (the cover cannot draw it)")


@dataclass(frozen=True, slots=True)
class InsiderPurchase:
    """One person's open-market buys of one company over the window."""

    company: CompanyRef
    role: Role
    # Placement (contract D7 A7): a person may appear in written body text only — never in the video
    # (owner decision 2026-10-09 "Role-only video": no narration line, burned caption or card).
    person_name: Optional[str] = field(metadata={"placement": "body_only"})
    amount_usd: float                      # >= 100_000, after implausible lines are dropped
    shares: float
    purchases: int                         # contributing Form 4 lines
    earliest_trade_date: date
    latest_trade_date: date
    filing_dates: Tuple[date, ...]         # sorted, unique, inside the week's window
    holding: Holding
    amended: bool                          # a Form 4/A contributed (never, since review round 7:
                                           # the adapter refuses any amended person)

    def __post_init__(self) -> None:
        c = _Check(self)
        if not isinstance(self.company, CompanyRef):
            c.fail("company", "expected CompanyRef")
        c.choice("role", ("ceo", "cfo", "director"))
        if self.person_name is not None:
            c.text("person_name", 3, PERSON_NAME_MAX)
            if not re.fullmatch(r"[A-Z][A-Za-z]+(?: [A-Z]\.)? [A-Z][A-Za-z]*(?:-[A-Z][A-Za-z]*)?",
                                self.person_name):
                c.fail("person_name", "not a rendered 'First [M.] Last' name")
            if _compliance.fold(self.person_name) == _compliance.fold(self.company.name):
                c.fail("person_name", "equals the company name")
        c.number("amount_usd", ge=INSIDER_MIN_AMOUNT_USD, le=INSIDER_MAX_AMOUNT_USD)
        c.number("shares", gt=0)
        c.integer("purchases", ge=1, le=10_000)
        c.day("earliest_trade_date")
        c.day("latest_trade_date")
        if self.earliest_trade_date > self.latest_trade_date:
            c.fail("earliest_trade_date", "after latest_trade_date")
        filings = c.items("filing_dates", date, 1, 64)
        if any(isinstance(d, datetime) for d in filings):
            c.fail("filing_dates", "datetimes are not dates")
        if any(a >= b for a, b in zip(filings, filings[1:])):
            c.fail("filing_dates", "must be strictly ascending (sorted, unique)")
        if self.latest_trade_date > filings[-1] + timedelta(days=1):
            c.fail("latest_trade_date", "more than a day after the last filing")
        if self.earliest_trade_date < filings[0] - timedelta(days=INSIDER_MAX_FILING_LAG_DAYS):
            c.fail("earliest_trade_date", "older than the filing-lag bound")
        c.choice("holding", ("direct", "mixed", "indirect"))
        c.flag("amended")


def _insider_order(row: InsiderPurchase) -> Tuple[float, str, str]:
    return (-row.amount_usd, row.company.name.casefold(), row.company.symbol)


@dataclass(frozen=True, slots=True)
class InsiderBuysWeek:
    series: InsiderSeries
    window_start: date
    window_end: date
    rows: Tuple[InsiderPurchase, ...]   # 1..5, unique symbol, amount desc, then company name

    def __post_init__(self) -> None:
        c = _Check(self)
        c.choice("series", ("ceo_buys", "insider_buys"))
        c.day("window_start")
        c.day("window_end")
        if not (0 <= (self.window_end - self.window_start).days <= 13):
            c.fail("window_end", "window must run forward and span at most 14 days")
        rows = c.items("rows", InsiderPurchase, 1, INSIDER_MAX_ROWS)
        if len({r.company.symbol for r in rows}) != len(rows):
            c.fail("rows", "duplicate symbol")
        if list(rows) != sorted(rows, key=_insider_order):
            c.fail("rows", "not ordered by amount desc, then company name")
        roles = {"ceo"} if self.series == "ceo_buys" else {"cfo", "director"}
        if any(r.role not in roles for r in rows):
            c.fail("rows", f"{self.series} carries only roles {sorted(roles)}")
        for r in rows:
            if r.filing_dates[0] < self.window_start or r.filing_dates[-1] > self.window_end:
                c.fail("rows", f"{r.company.symbol} has a filing outside the window")


@dataclass(frozen=True, slots=True)
class ThirteenFMove:
    company: CompanyRef
    move: Move
    shares: Optional[float]
    prev_shares: Optional[float]
    value_usd: Optional[float]
    listed_on: Optional[date]   # set only when newly listed (a newly_reported move)
    # Review round 8 (shared contract with the templates): the position's value on the PREVIOUS
    # quarter's book, set ONLY on a no_longer_reported move (>= 0, finite), None otherwise. Never
    # drawn or said — a 13F value is the position's — it ranks an exit by size beside the other
    # kinds. Additive and defaulted, so a record built without it (and a fact sheet stored before
    # it existed) still reads.
    prev_value_usd: Optional[float] = None

    def __post_init__(self) -> None:
        c = _Check(self)
        if not isinstance(self.company, CompanyRef):
            c.fail("company", "expected CompanyRef")
        c.choice("move", THIRTEEN_F_MOVES)
        c.number("shares", ge=0, optional=True)
        c.number("prev_shares", ge=0, optional=True)
        c.number("value_usd", ge=0, optional=True)
        c.day("listed_on", optional=True)
        c.number("prev_value_usd", ge=0, optional=True)
        if self.listed_on is not None and self.move != "newly_reported":
            c.fail("listed_on", "only a newly_reported move carries a listing date")
        if self.prev_value_usd is not None and self.move != "no_longer_reported":
            c.fail("prev_value_usd", "only a no_longer_reported move carries a previous-quarter value")
        s, p = self.shares, self.prev_shares
        if self.move == "newly_reported" and p not in (None, 0.0):
            c.fail("prev_shares", "a newly reported position had no previous shares")
        if self.move == "no_longer_reported" and s not in (None, 0.0):
            c.fail("shares", "a position no longer reported has no shares")
        if self.move in ("increased", "decreased"):
            if s is None or p is None or s <= 0 or p <= 0:
                c.fail("shares", "an increase/decrease needs both share counts > 0")
            if (self.move == "increased") != (s > p) or s == p:
                c.fail("shares", f"share counts contradict '{self.move}'")


@dataclass(frozen=True, slots=True)
class ThirteenFFiling:
    series: Literal["thirteen_f"]
    filer_name: str                  # the ENTITY, never a person
    filer_cik: str                   # 10 digits, zero-padded
    filer_symbol: Optional[str]
    period: str                      # "YYYY-Qn"
    period_end: date
    filed_on: date
    amended_on: Optional[date]
    total_value_usd: float
    position_count: int
    moves: Tuple[ThirteenFMove, ...]  # 1..8, unique symbols
    counts: Tuple[Tuple[str, int], ...]

    def __post_init__(self) -> None:
        c = _Check(self)
        c.choice("series", ("thirteen_f",))
        if filer_name_problem(self.filer_name) is not None:
            c.fail("filer_name", filer_name_problem(self.filer_name) or "")
        if not isinstance(self.filer_cik, str) or cik10(self.filer_cik) != self.filer_cik:
            c.fail("filer_cik", "expected a 10-digit zero-padded CIK")
        if self.filer_symbol is not None and (
            not isinstance(self.filer_symbol, str) or canonical_symbol(self.filer_symbol) != self.filer_symbol
        ):
            c.fail("filer_symbol", "not a canonical symbol")
        try:
            quarter_end = period_end_of(self.period)
        except ValueError as e:
            c.fail("period", str(e))
        c.day("period_end")
        c.day("filed_on")
        c.day("amended_on", optional=True)
        if self.period_end != quarter_end:
            c.fail("period_end", f"{self.period_end} is not the end of {self.period}")
        if self.filed_on <= self.period_end:
            c.fail("filed_on", "a 13F is filed after its quarter ends")
        if self.amended_on is not None and self.amended_on < self.filed_on:
            c.fail("amended_on", "before the original filing")
        c.number("total_value_usd", gt=0)
        c.integer("position_count", ge=1)
        moves = c.items("moves", ThirteenFMove, 1, THIRTEEN_F_MAX_MOVES)
        if len({m.company.symbol for m in moves}) != len(moves):
            c.fail("moves", "duplicate symbol")
        if any(m.value_usd is not None and m.value_usd > self.total_value_usd for m in moves):
            c.fail("moves", "a move's value exceeds the filer total")
        # "X first appears in this filing; it was listed in <month>" is true only for a listing
        # AFTER the previous quarter's end (the diff is adjacent-quarter only) and no later than
        # this quarter's end (a holding at quarter end was listed by then).
        prev_end = previous_period_end_of(self.period)
        for m in moves:
            if m.listed_on is not None and not (prev_end < m.listed_on <= self.period_end):
                c.fail("moves", f"{m.company.symbol} listed_on {m.listed_on} is outside "
                                f"{prev_end} (exclusive) .. {self.period_end}")
        counts = self.counts
        if not isinstance(counts, tuple) or any(
            not isinstance(p, tuple) or len(p) != 2 or not isinstance(p[0], str)
            or not isinstance(p[1], int) or isinstance(p[1], bool) or p[1] < 0
            for p in counts
        ):
            c.fail("counts", "expected ((move, non-negative int), ...)")
        keys = [k for k, _ in counts]
        if any(k not in THIRTEEN_F_MOVES for k in keys) or keys != [m for m in THIRTEEN_F_MOVES if m in keys]:
            c.fail("counts", f"keys must be unique moves in the order {THIRTEEN_F_MOVES}")
        totals = dict(counts)
        for kind in THIRTEEN_F_MOVES:
            shown = sum(1 for m in moves if m.move == kind)
            if shown and totals.get(kind, 0) < shown:
                c.fail("counts", f"{kind} count below the {shown} moves shown")


@dataclass(frozen=True, slots=True)
class CongressCount:
    series: Literal["congress_count"]
    company: CompanyRef
    month: str          # "YYYY-MM", the DISCLOSURE month
    members: int        # >= 2 distinct members, purchases only
    fetched_on: date    # NO member-identity field exists

    def __post_init__(self) -> None:
        c = _Check(self)
        c.choice("series", ("congress_count",))
        if not isinstance(self.company, CompanyRef):
            c.fail("company", "expected CompanyRef")
        try:
            end = month_end_of(self.month)
        except ValueError as e:
            c.fail("month", str(e))
        c.integer("members", ge=CONGRESS_MIN_MEMBERS, le=535)
        c.day("fetched_on")
        if self.fetched_on <= end:
            c.fail("fetched_on", "the month must be over before it is counted")


@dataclass(frozen=True, slots=True)
class CompanyStake:
    series: Literal["company_stakes"]
    stake_id: str
    investor: CompanyRef
    investee_name: str
    investee: Optional[CompanyRef]
    kind: str                       # STAKE_KINDS
    value_usd: Optional[float]
    value_basis: Optional[str]      # VALUE_BASES
    ownership_pct: Optional[float]
    as_of: date
    verified_on: date
    source_title: str
    background: Optional[str]
    listed_since: Optional[date]
    local_listing: Optional[str]
    is_new: bool

    def __post_init__(self) -> None:
        c = _Check(self)
        c.choice("series", ("company_stakes",))
        try:
            canonical = str(uuid.UUID(self.stake_id)) if isinstance(self.stake_id, str) else None
        except ValueError:
            canonical = None
        if canonical != self.stake_id:
            c.fail("stake_id", "expected a canonical lower-case UUID")
        if not isinstance(self.investor, CompanyRef):
            c.fail("investor", "expected CompanyRef")
        if self.investee is not None and not isinstance(self.investee, CompanyRef):
            c.fail("investee", "expected CompanyRef or None")
        c.text("investee_name", 2, 80)
        if "(" in self.investee_name or "not named" in self.investee_name.lower():
            c.fail("investee_name", "an aggregate or unnamed stake")
        c.choice("kind", STAKE_KINDS)
        c.number("value_usd", gt=0, optional=True)
        if self.value_basis is not None:
            c.choice("value_basis", VALUE_BASES)
        if (self.value_usd is None) != (self.value_basis is None):
            c.fail("value_basis", "a value and its basis come together")
        c.number("ownership_pct", gt=0, le=100, optional=True)
        if self.value_usd is None and self.ownership_pct is None:
            c.fail("value_usd", "a stake with no figure is refused")
        c.day("as_of")
        c.day("verified_on")
        if self.as_of > self.verified_on:
            c.fail("as_of", "after verified_on")
        c.text("source_title", 2, 200)
        c.text("background", 1, 400, optional=True)
        c.day("listed_since", optional=True)
        c.text("local_listing", 1, 40, optional=True)
        c.flag("is_new")


@dataclass(frozen=True, slots=True)
class EarningsReport:
    series: Literal["earnings"]
    company: CompanyRef
    report_date: date
    period_end: Optional[date]
    eps_actual: float
    eps_estimate: float
    revenue_actual: Optional[float]
    revenue_estimate: Optional[float]

    def __post_init__(self) -> None:
        c = _Check(self)
        c.choice("series", ("earnings",))
        if not isinstance(self.company, CompanyRef):
            c.fail("company", "expected CompanyRef")
        c.day("report_date")
        c.day("period_end", optional=True)
        if self.period_end is not None and self.period_end > self.report_date:
            c.fail("period_end", "after the report date")
        c.number("eps_actual")
        c.number("eps_estimate")
        if abs(self.eps_estimate) < EPS_MIN_ABS_ESTIMATE:
            c.fail("eps_estimate", f"|estimate| below {EPS_MIN_ABS_ESTIMATE}")
        # the owner-delegated EPS ceiling (2026-10-19): a stored fact sheet is held to it too
        for name in ("eps_actual", "eps_estimate"):
            if abs(getattr(self, name)) > EPS_MAX_ABS:
                c.fail(name, f"|{name}| above {EPS_MAX_ABS:,.0f} a share")
        c.number("revenue_actual", gt=0, optional=True)
        c.number("revenue_estimate", gt=0, optional=True)
        if (self.revenue_actual is None) != (self.revenue_estimate is None):
            c.fail("revenue_actual", "revenue actual and estimate come together")
        if self.revenue_actual is not None:
            lo, hi = REVENUE_RATIO_BAND
            if not (lo <= self.revenue_actual / self.revenue_estimate <= hi):
                c.fail("revenue_actual", f"revenue ratio outside {REVENUE_RATIO_BAND}")


# ── earnings gates (pure; Batch 1, 2026-10-15) ────────────────────────────────
#
# The adapter runs these over what FMP answered; each refuses the WHOLE report (one post lost, never a
# wrong figure about a named company). The record class above is unchanged — it still admits a report
# without revenue or a period, so a fact sheet stored before these gates reads back the same.


def _figure(v: Any) -> Decimal:
    """A finite number as the Decimal of its shortest repr — the figure as the feed wrote it ("4.35", not
    4.34999999999999964…), so a gate's boundary is exact."""
    return Decimal(repr(float(v)))


def earnings_revenue_pair(pairs: Sequence[Any]) -> Optional[Tuple[float, float]]:
    """The ONE ``(revenue actual, revenue estimate)`` pair every calendar row of a report carries, or None —
    the report is then refused (`revenue_implausible`): no row; rows that disagree (a row without revenue
    beside one with it included: which is the report is a guess); a missing, non-finite, bool or
    non-positive figure; a ratio outside `REVENUE_RATIO_BAND` (bounds inclusive). Pure; never raises."""
    pairs = list(pairs) if isinstance(pairs, (list, tuple)) else []
    if not pairs or any(not isinstance(p, tuple) or len(p) != 2 for p in pairs):
        return None
    first = pairs[0]
    if any(p != first for p in pairs[1:]):          # NaN != NaN: a NaN pair never agrees, fail closed
        return None
    ra, re_ = first
    if not (_is_num(ra) and _is_num(re_)) or ra <= 0 or re_ <= 0:
        return None
    lo, hi = REVENUE_RATIO_BAND
    if not lo <= ra / re_ <= hi:
        return None
    return float(ra), float(re_)


def printed_eps(value: Any) -> Decimal:
    """A per-share figure as the post PRINTS it: `_figure` rounded to `EPS_PRINT_PLACES` places, half-up away
    from zero (`news_templates.eps_image` rounds the magnitude half-up and puts the sign back: the same
    figure). Exact for every finite float, never raising (`EPS_DECIMAL_PRECISION`). The caller passes a number
    (`_is_num`)."""
    with localcontext() as ctx:
        ctx.prec = EPS_DECIMAL_PRECISION
        return _figure(value).quantize(Decimal(1).scaleb(-EPS_PRINT_PLACES), rounding=ROUND_HALF_UP)


def clear_surprise(actual: Any, estimate: Any) -> bool:
    """Is |actual − estimate| at least `EARNINGS_CLEAR_SURPRISE` of |estimate| — on the figures as written
    (`_figure`) AND on the figures as printed (`printed_eps`)? Exact at the boundary both ways: 0.21 vs 0.20
    (5.0%) is clear, 1.0499 vs 1.00 is not (4.99% as written, though it prints "$1.05 vs $1.00"); 0.095 vs
    0.10 is not (5.0% as written, printed "$0.10 vs $0.10": no visible gap — review of 2026-10-16, ER-1), nor
    0.3049 vs 0.2895 (5.3% as written, printed "$0.30 vs $0.29": 3.4%). False for a missing, non-finite or
    bool figure, a zero estimate, and an estimate that prints as $0.00 (never a division). Computed in
    `EPS_DECIMAL_PRECISION` digits: exact, and never raising, for any finite figure (1e300 included — a figure
    the post cannot print fails at compose, which the script flow logs and counts as a refused candidate; it is
    never an exception here that sinks the whole series)."""
    if not (_is_num(actual) and _is_num(estimate)) or estimate == 0:
        return False
    with localcontext() as ctx:
        ctx.prec = EPS_DECIMAL_PRECISION
        threshold = _figure(EARNINGS_CLEAR_SURPRISE)
        a, e = _figure(actual), _figure(estimate)
        if abs(a - e) < threshold * abs(e):
            return False
        pa, pe = printed_eps(actual), printed_eps(estimate)
        return pe != 0 and abs(pa - pe) >= threshold * abs(pe)


def _statement_day(v: Any) -> Optional[date]:
    """A statement row's period end (FMP's ``date``): a date (never a datetime) or the "YYYY-MM-DD" prefix
    of a str, else None (an unreadable row is never a candidate)."""
    if isinstance(v, datetime):
        return None
    if isinstance(v, date):
        return v
    if isinstance(v, str) and len(v) >= 10 and v[:10].isascii():
        try:
            return date.fromisoformat(v[:10])
        except ValueError:
            return None
    return None


def statement_currency(rows: Any) -> Optional[str]:
    """The REPORTING currency of a quarterly statement answer: the ``reportedCurrency`` of its NEWEST
    readable period end (rows sharing that date must all carry the same one), upper-cased — or None when
    it cannot be read (no dated row, a missing / blank / non-str code, two codes on one date). The newest
    period is the one `earnings_period` can keep (it refuses an answer with a later one), so the currency
    checked is the currency of the figures published. Pure; never raises."""
    dated: List[Tuple[date, Mapping[str, Any]]] = []
    for row in rows if isinstance(rows, (list, tuple)) else ():
        if isinstance(row, Mapping):
            d = _statement_day(row.get("date"))
            if d is not None:
                dated.append((d, row))
    if not dated:
        return None
    newest = max(d for d, _row in dated)
    codes = set()
    for d, row in dated:
        if d == newest:
            code = row.get("reportedCurrency")
            if not isinstance(code, str) or not code.strip():
                return None
            codes.add(code.strip().upper())
    return codes.pop() if len(codes) == 1 else None


def earnings_period(rows: Any, *, report_date: date, revenue_actual: Any
                    ) -> Tuple[Optional[date], Optional[str]]:
    """Which fiscal quarter the calendar's figures are for, from the company's latest QUARTERLY income
    statements (FMP's ``date`` = the period end): ``(period_end, None)`` keeps the report with its period,
    ``(None, None)`` keeps it without one (only under `EARNINGS_ALLOW_SCALE_CHECK`), ``(None, reason)``
    refuses it. The EPS is never checked against the statement HERE (the analysts' basis differs from GAAP);
    `earnings_basis_mixed` is the one narrow EPS gate, run on the placed quarter's row.

    The reported quarter is the NEWEST statement row whose period ends in ``[report −
    EARNINGS_PERIOD_WINDOW_DAYS, report)``:

    * no such row, or no readable positive revenue on it → ``period_unverified``;
    * any row ending ON or AFTER the report day → ``period_unverified`` (a quarter that cannot have
      been reported yet: the answer is not trusted for the one that was);
    * the row must be QUARTER-SHAPED (review of 2026-10-16, ER-4): the next-older dated row (any age) ended
      `EARNINGS_QUARTER_SPAN_DAYS` before it — no older row (a single row), a half-year span (~181 days: FMP
      files a semiannual reporter's halves as "quarters") or an odd one → ``period_unverified``;
    * that row ended more than `EARNINGS_PERIOD_MAX_LAG_DAYS` before the report → it may be the quarter
      BEFORE the reported one (the statement has not caught up), and a flat-revenue company would match
      it → ``period_unverified`` — or, under the contingency, kept WITHOUT a period while the row is
      quarter-shaped and the calendar's revenue is within `EARNINGS_SCALE_BAND` of it (a quarter's scale,
      never a month or a half-year);
    * the report came at least the row's own span after it (its NEXT quarter, as long as this one, had ended
      by then: a 12-week quarter ending Jun 13 and reported Sep 10) → ``period_unverified``;
    * its revenue within `EARNINGS_PERIOD_REVENUE_TOLERANCE` of the calendar's revenueActual → its period
      end; else ``period_mismatch`` (a month, a half-year, another basis — never "the quarter that ended").

    Rows sharing the newest date must all agree. ``report_date`` must be a date (ValueError: a caller bug);
    everything else is data, and junk in it refuses the report — never raises on it."""
    if not isinstance(report_date, date) or isinstance(report_date, datetime):
        raise ValueError("report_date must be a date")
    if not _is_num(revenue_actual) or revenue_actual <= 0:
        return None, "period_unverified"
    dated: List[Tuple[date, Mapping[str, Any]]] = []
    for row in rows if isinstance(rows, (list, tuple)) else ():
        if isinstance(row, Mapping):
            d = _statement_day(row.get("date"))
            if d is not None:
                dated.append((d, row))
    if any(d >= report_date for d, _row in dated):
        return None, "period_unverified"
    start = report_date - timedelta(days=EARNINGS_PERIOD_WINDOW_DAYS)
    window = [(d, row) for d, row in dated if d >= start]
    if not window:
        return None, "period_unverified"
    newest = max(d for d, _row in window)
    revenues = [row.get("revenue") for d, row in window if d == newest]
    if not all(_is_num(v) and v > 0 for v in revenues):
        return None, "period_unverified"
    ra = _figure(revenue_actual)
    # Review of 2026-10-16 (ER-4): a QUARTER, by its distance from the company's previous period end (that row
    # may sit outside the window: a 120-day window can hold one quarter only). No older row → unverified.
    older = [d for d, _row in dated if d < newest]
    span = (newest - max(older)).days if older else None
    quarter_shaped = span is not None and EARNINGS_QUARTER_SPAN_DAYS[0] <= span <= EARNINGS_QUARTER_SPAN_DAYS[1]
    lag = (report_date - newest).days
    if lag > EARNINGS_PERIOD_MAX_LAG_DAYS:
        if EARNINGS_ALLOW_SCALE_CHECK and quarter_shaped:
            lo, hi = (_figure(x) for x in EARNINGS_SCALE_BAND)
            if all(lo * _figure(v) <= ra <= hi * _figure(v) for v in revenues):
                return None, None
        return None, "period_unverified"
    if not quarter_shaped or lag >= span:
        return None, "period_unverified"
    tolerance = _figure(EARNINGS_PERIOD_REVENUE_TOLERANCE) * ra
    if all(abs(_figure(v) - ra) <= tolerance for v in revenues):
        return newest, None
    return None, "period_mismatch"


def earnings_sector_problem(profile: Any) -> Optional[str]:
    """``eps_basis_reit`` when an earnings issuer's profile says Real Estate (`EARNINGS_EXCLUDED_SECTORS`) or
    an industry starting "REIT" (`EARNINGS_EXCLUDED_INDUSTRY_PREFIX`, any case: "REIT - Industrial",
    "REIT - Mortgage") — a REIT's calendar EPS can be FFO on either side (review of 2026-10-16, live preview F1:
    Prologis) — or when its sector cannot be read (missing, blank, not a string, over-long): an unknown sector
    cannot be shown not to be a REIT (fail closed, as the Money Map sector gate). None otherwise. A missing
    industry alone decides nothing (the sector does). Pure; never raises."""
    if not isinstance(profile, Mapping):
        return "eps_basis_reit"
    sector = profile.get("sector")
    if not isinstance(sector, str) or not sector.strip() or len(sector) > _RAW_NAME_MAX:
        return "eps_basis_reit"
    if sector.strip() in EARNINGS_EXCLUDED_SECTORS:
        return "eps_basis_reit"
    industry = profile.get("industry")
    if isinstance(industry, str) and industry.strip()[:len(EARNINGS_EXCLUDED_INDUSTRY_PREFIX)].upper() == \
            EARNINGS_EXCLUDED_INDUSTRY_PREFIX:
        return "eps_basis_reit"
    return None


def _statement_eps(row: Mapping[str, Any]) -> Optional[Decimal]:
    """A statement row's diluted EPS (``epsDiluted``, else ``eps`` — PRESENCE, not truthiness: a reported 0.0
    is a value) as a Decimal of its figure, or None when neither is a number."""
    for key in ("epsDiluted", "eps"):
        v = row.get(key)
        if _is_num(v):
            return _figure(v)
    return None


def earnings_basis_mixed(rows: Any, *, period_end: date, actual: Any, estimate: Any) -> bool:
    """Are the calendar's actual and estimate on two different BASES (``eps_basis_mixed``, review of
    2026-10-16, live preview F1)? Judged against the diluted EPS (`_statement_eps`) of the statement row(s)
    dated ``period_end`` — the quarter `earnings_period` placed: True when, for a row whose EPS G is POSITIVE,
    the actual sits outside `EARNINGS_BASIS_ACTUAL_BAND` × G while the estimate sits within ±
    `EARNINGS_BASIS_ESTIMATE_TOLERANCE` × G of it (the estimate is on the statement's scale, the actual is not).
    Prologis: 1.60 against GAAP 0.86 (1.86×) with an estimate of 0.777 (0.90×) → True; BlackRock: 15.93 against
    GAAP 10.57 (1.507×) with an estimate of 14.32 (1.35×: not on the GAAP scale either) → False. Never True when
    no dated row carries a positive EPS (the gate does not apply: a loss quarter, a missing figure), and never
    for a missing or non-numeric actual / estimate (the record gates refuse those). Every band edge inclusive,
    exact on the figures as written. Pure; never raises."""
    if not _is_day(period_end) or not (_is_num(actual) and _is_num(estimate)):
        return False
    lo, hi = (_figure(x) for x in EARNINGS_BASIS_ACTUAL_BAND)
    tol = _figure(EARNINGS_BASIS_ESTIMATE_TOLERANCE)
    a, e = _figure(actual), _figure(estimate)
    for row in rows if isinstance(rows, (list, tuple)) else ():
        if not isinstance(row, Mapping) or _statement_day(row.get("date")) != period_end:
            continue
        g = _statement_eps(row)
        if g is None or g <= 0:
            continue
        if not (lo * g <= a <= hi * g) and abs(e - g) <= tol * g:
            return True
    return False


@dataclass(frozen=True, slots=True)
class Segment:
    name: str
    value_usd: float

    def __post_init__(self) -> None:
        c = _Check(self)
        c.text("name", 1, 40)
        if _copy_rules.contains_banned_copy(self.name) or _copy_rules.contains_forecast(self.name):
            c.fail("name", "banned or forecast wording")
        c.number("value_usd", gt=0)


@dataclass(frozen=True, slots=True)
class MoneyMap:
    series: Literal["money_map"]
    company: CompanyRef
    fiscal_year: str                     # "YYYY"
    period_end: date
    segments: Tuple[Segment, ...]        # 2..5 named
    other_usd: Optional[float]
    eliminations_usd: Optional[float]
    revenue_usd: float
    gross_profit_usd: Optional[float]
    operating_profit_usd: Optional[float]
    net_income_usd: float

    def __post_init__(self) -> None:
        c = _Check(self)
        c.choice("series", ("money_map",))
        if not isinstance(self.company, CompanyRef):
            c.fail("company", "expected CompanyRef")
        if not isinstance(self.fiscal_year, str) or not re.fullmatch(r"[0-9]{4}", self.fiscal_year):
            c.fail("fiscal_year", "expected YYYY")
        c.day("period_end")
        lo, hi = MONEY_MAP_SEGMENTS
        segs = c.items("segments", Segment, lo, hi)
        if len({s.name.casefold() for s in segs}) != len(segs):
            c.fail("segments", "duplicate segment name")
        c.number("other_usd", ge=0, optional=True)
        c.number("eliminations_usd", le=0, optional=True)
        c.number("revenue_usd", gt=0)
        c.number("gross_profit_usd", optional=True)
        c.number("operating_profit_usd", optional=True)
        c.number("net_income_usd")
        total = sum(s.value_usd for s in segs) + (self.other_usd or 0.0) + (self.eliminations_usd or 0.0)
        if abs(total - self.revenue_usd) > MONEY_MAP_SUM_TOLERANCE * self.revenue_usd:
            c.fail("segments", "segments + other + eliminations do not add up to revenue")
        g, o = self.gross_profit_usd, self.operating_profit_usd
        if g is not None and g > self.revenue_usd:
            c.fail("gross_profit_usd", "above revenue")
        if o is not None and (o > self.revenue_usd or (g is not None and o > g)):
            c.fail("operating_profit_usd", "above gross profit or revenue")
        if self.net_income_usd > self.revenue_usd:
            c.fail("net_income_usd", "above revenue")


@dataclass(frozen=True, slots=True)
class ThemeMember:
    company: CompanyRef
    top_segment: Optional[str]
    top_segment_share: Optional[float]   # 0..1
    fiscal_year: Optional[str]

    def __post_init__(self) -> None:
        c = _Check(self)
        if not isinstance(self.company, CompanyRef):
            c.fail("company", "expected CompanyRef")
        c.text("top_segment", 1, 40, optional=True)
        if self.top_segment is not None and (
            _copy_rules.contains_banned_copy(self.top_segment)
            or _copy_rules.contains_forecast(self.top_segment)
        ):
            c.fail("top_segment", "banned or forecast wording")
        c.number("top_segment_share", gt=0, le=1, optional=True)
        if self.top_segment_share is not None and self.top_segment is None:
            c.fail("top_segment_share", "a share needs its segment")
        if self.fiscal_year is not None and (
            not isinstance(self.fiscal_year, str) or not re.fullmatch(r"[0-9]{4}", self.fiscal_year)
        ):
            c.fail("fiscal_year", "expected YYYY")


def theme_company_count(tickers: Any, profiles: Any = None) -> Optional[int]:
    """The theme's size in COMPANIES before any gate — `ThemeExplainer.theme_size` (review round 15).

    The app card counts TICKERS (Home's ``ticker_count``: distinct non-empty
    ``str(t).strip().upper()``), but the copy says "{n} of its {m} companies": a basket holding
    GOOG and GOOGL lists Alphabet once, so "7 of the 8 companies" claimed an eighth company that
    does not exist. Every distinct spelling starts as its own company, and two merge when they are
    ONE issuer: the same ticker in dot or dash form (BRK.B / BRK-B), the same CIK where a profile
    carries one, or the same display name (`display_company_name`, casefolded — the members' own
    dedupe key). ``profiles`` maps a canonical symbol to its profile; a ticker without a usable one
    (blocked and never profiled, an unusable symbol, a missing profile) can only count as its own
    company. None when ``tickers`` is not a list."""
    if not isinstance(tickers, list):
        return None
    if not isinstance(profiles, Mapping):
        profiles = {}
    spellings = list(dict.fromkeys(s for s in (str(t).strip().upper() for t in tickers) if s))
    parent = list(range(len(spellings)))

    def root(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    owner: Dict[Tuple[str, str], int] = {}
    for i, spelling in enumerate(spellings):
        sym = canonical_symbol(spelling)
        keys: List[Tuple[str, str]] = [("symbol", sym or spelling)]
        profile = profiles.get(sym) if sym else None
        if isinstance(profile, Mapping) and canonical_symbol(profile.get("symbol")) == sym:
            cik, name = cik10(profile.get("cik")), display_company_name(profile.get("companyName"))
            if cik is not None:
                keys.append(("cik", cik))
            if name is not None:
                keys.append(("name", name.casefold()))
        for key in keys:
            a, b = root(i), root(owner.setdefault(key, i))
            if a != b:
                parent[a] = b
    return len({root(i) for i in range(len(spellings))})


@dataclass(frozen=True, slots=True)
class ThemeExplainer:
    series: Literal["theme_explainer"]
    slug: str
    title: str
    members: Tuple[ThemeMember, ...]     # 6..24, ALL members that passed the gates, unique symbols
    tickers_as_of: date
    # The shared adapter/templates contract (2026-10-10), an ADDITIVE field (an older fact sheet
    # without it reads back as None): the theme's size in COMPANIES before any gate
    # (`theme_company_count`: share classes of one issuer count once — review round 15; the app
    # card's own number counts tickers), so >= len(members). When it is larger than len(members)
    # the gates dropped companies, and the copy must say "{n} of its {m} companies" — never claim
    # the list is complete.
    theme_size: Optional[int] = None

    def __post_init__(self) -> None:
        c = _Check(self)
        c.choice("series", ("theme_explainer",))
        if not isinstance(self.slug, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,62}", self.slug):
            c.fail("slug", "expected a lower-case slug")
        c.text("title", 2, 60)
        if (
            _copy_rules.contains_banned_copy(self.title)
            or _copy_rules.contains_forecast(self.title)
            or _NAME_EXTRA_BANNED_RE.search(self.title)
        ):
            c.fail("title", "banned or forecast wording")
        lo, hi = THEME_MEMBERS
        members = c.items("members", ThemeMember, lo, hi)
        if len({m.company.symbol for m in members}) != len(members):
            c.fail("members", "duplicate symbol")
        c.day("tickers_as_of")
        if self.theme_size is not None:
            c.integer("theme_size", ge=len(members), le=THEME_SIZE_MAX)


NewsRecord = Union[
    InsiderBuysWeek, ThirteenFFiling, CongressCount, CompanyStake, EarningsReport, MoneyMap,
    ThemeExplainer,
]
#: The top-level record types (one per series family). The leak test walks these and
#: `NESTED_RECORD_TYPES`.
RECORD_TYPES: Tuple[type, ...] = (
    InsiderBuysWeek, ThirteenFFiling, CongressCount, CompanyStake, EarningsReport, MoneyMap,
    ThemeExplainer,
)
NESTED_RECORD_TYPES: Tuple[type, ...] = (CompanyRef, InsiderPurchase, ThirteenFMove, Segment, ThemeMember)
ALL_RECORD_TYPES: Tuple[type, ...] = RECORD_TYPES + NESTED_RECORD_TYPES
RECORD_TYPE_BY_SERIES: Dict[str, type] = {
    "ceo_buys": InsiderBuysWeek, "insider_buys": InsiderBuysWeek, "thirteen_f": ThirteenFFiling,
    "congress_count": CongressCount, "company_stakes": CompanyStake, "earnings": EarningsReport,
    "money_map": MoneyMap, "theme_explainer": ThemeExplainer,
}


# ── ledger keys and source labels ─────────────────────────────────────────────

def ledger_key(rec: NewsRecord) -> str:
    """The `marketing_scripts.source_ref` of a record (pinned format, contract D4.2)."""
    if isinstance(rec, InsiderBuysWeek):
        return f"news:{rec.series}:{rec.window_start.isoformat()}"
    if isinstance(rec, ThirteenFFiling):
        return f"news:thirteen_f:{rec.filer_cik}:{rec.period}"
    if isinstance(rec, CongressCount):
        return f"news:congress_count:{rec.month}"
    if isinstance(rec, CompanyStake):
        return f"news:company_stakes:{rec.stake_id}"
    if isinstance(rec, EarningsReport):
        return f"news:earnings:{rec.company.symbol}:{rec.report_date.isoformat()}"
    if isinstance(rec, MoneyMap):
        return f"news:money_map:{rec.company.symbol}:{rec.fiscal_year}"
    if isinstance(rec, ThemeExplainer):
        return f"news:theme_explainer:{rec.slug}:{rec.tickers_as_of.isoformat()}"
    raise ValueError(f"not a news record: {type(rec).__name__}")


def source_label(rec: NewsRecord) -> str:
    """The public "Source:" label of a record (pinned; never the data vendor)."""
    if isinstance(rec, CompanyStake):
        return rec.source_title
    if isinstance(rec, ThirteenFFiling):
        return THIRTEEN_F_AMENDED_LABEL if rec.amended_on is not None else SOURCE_LABELS["thirteen_f"]
    if not isinstance(rec, RECORD_TYPES):
        raise ValueError(f"not a news record: {type(rec).__name__}")
    return SOURCE_LABELS[rec.series]


# ── (de)serialisation ─────────────────────────────────────────────────────────

def _encode(v: Any, path: str) -> Any:
    if is_dataclass(v) and not isinstance(v, type):
        return {f.name: _encode(getattr(v, f.name), f"{path}.{f.name}") for f in fields(v)}
    if isinstance(v, datetime):
        raise ValueError(f"{path}: a datetime is not a date")
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, tuple):
        return [_encode(x, f"{path}[{i}]") for i, x in enumerate(v)]
    if isinstance(v, float):
        if not math.isfinite(v):
            raise ValueError(f"{path}: non-finite number")
        return v
    if v is None or isinstance(v, (str, bool, int)):
        return v
    raise ValueError(f"{path}: cannot encode {type(v).__name__}")


def record_to_dict(rec: NewsRecord) -> Dict[str, Any]:
    """The JSON-safe dict of a record: ISO dates, lists for tuples, finite floats, nested records
    as dicts, plus ``"schema": 1``. Checked with ``json.dumps(allow_nan=False)``."""
    if not isinstance(rec, RECORD_TYPES):
        raise ValueError(f"not a news record: {type(rec).__name__}")
    out = {"schema": FACT_SHEET_SCHEMA, **_encode(rec, "record")}
    json.dumps(out, allow_nan=False)
    return out


@lru_cache(maxsize=None)
def _hints(cls: type) -> Dict[str, Any]:
    return typing.get_type_hints(cls)


def _decode(tp: Any, v: Any, path: str) -> Any:
    origin = typing.get_origin(tp)
    args = typing.get_args(tp)
    if origin is Union:
        if v is None and type(None) in args:
            return None
        inner = [a for a in args if a is not type(None)]
        if len(inner) != 1:
            raise ValueError(f"{path}: unsupported union")
        return _decode(inner[0], v, path)
    if origin is Literal:
        if isinstance(v, str) and v in args:
            return v
        raise ValueError(f"{path}: {v!r} not one of {args}")
    if origin is tuple:
        if not isinstance(v, (list, tuple)):
            raise ValueError(f"{path}: expected a list")
        if len(args) == 2 and args[1] is Ellipsis:
            return tuple(_decode(args[0], x, f"{path}[{i}]") for i, x in enumerate(v))
        if len(v) != len(args):
            raise ValueError(f"{path}: expected {len(args)} items")
        return tuple(_decode(a, x, f"{path}[{i}]") for i, (a, x) in enumerate(zip(args, v)))
    if isinstance(tp, type) and is_dataclass(tp):
        return _decode_dataclass(tp, v, path)
    if tp is date:
        if isinstance(v, str) and len(v) == 10:
            try:
                return date.fromisoformat(v)
            except ValueError:
                pass
        raise ValueError(f"{path}: expected an ISO date, got {v!r}")
    if tp is bool:
        if isinstance(v, bool):
            return v
        raise ValueError(f"{path}: expected a bool")
    if tp is int:
        if isinstance(v, int) and not isinstance(v, bool):
            return v
        if isinstance(v, float) and math.isfinite(v) and v.is_integer():
            return int(v)
        raise ValueError(f"{path}: expected an int, got {v!r}")
    if tp is float:
        if _is_num(v):
            return float(v)
        raise ValueError(f"{path}: expected a finite number, got {v!r}")
    if tp is str:
        if isinstance(v, str):
            return v
        raise ValueError(f"{path}: expected a str")
    raise ValueError(f"{path}: unsupported type {tp!r}")


def _decode_dataclass(cls: type, d: Any, path: str) -> Any:
    if not isinstance(d, Mapping):
        raise ValueError(f"{path}: expected an object")
    fs = fields(cls)
    names = [f.name for f in fs]
    # A field added later WITH a default (the additive-field rule; review round 8:
    # `ThirteenFMove.prev_value_usd`) may be absent from a fact sheet stored before it existed —
    # it reads back as its default. Every other key must be present; an unknown key never is.
    defaulted = {f.name for f in fs if f.default is not MISSING or f.default_factory is not MISSING}
    missing = sorted(set(names) - set(d) - defaulted)
    unknown = sorted(set(d) - set(names))
    if missing or unknown:
        raise ValueError(f"{path}: missing {missing} / unknown {unknown} keys for {cls.__name__}")
    hints = _hints(cls)
    kwargs = {n: _decode(hints[n], d[n], f"{path}.{n}") for n in names if n in d}
    try:
        return cls(**kwargs)
    except TypeError as e:
        raise ValueError(f"{path}: {e}") from e


def _schema_ok(v: Any) -> bool:
    if isinstance(v, bool):
        return False
    return (isinstance(v, int) and v == FACT_SHEET_SCHEMA) or (
        isinstance(v, float) and v == float(FACT_SHEET_SCHEMA)
    )


def record_from_dict(d: Any) -> NewsRecord:
    """The record of a `record_to_dict` dict. Strict: ``schema`` must be 1, the series known, and
    every key present and known at every level (ValueError otherwise) — except a field added
    later with a default, which may be absent from an older sheet and reads back as its default.
    Key order is irrelevant and numbers may come back re-serialised (100000.0 ↔ 100000)."""
    if not isinstance(d, Mapping):
        raise ValueError("record: expected an object")
    if not _schema_ok(d.get("schema")):
        raise ValueError(f"record: schema {d.get('schema')!r} != {FACT_SHEET_SCHEMA}")
    cls = RECORD_TYPE_BY_SERIES.get(d.get("series")) if isinstance(d.get("series"), str) else None
    if cls is None:
        raise ValueError(f"record: unknown series {d.get('series')!r}")
    body = {k: v for k, v in d.items() if k != "schema"}
    return _decode_dataclass(cls, body, "record")


def _rejections_dict(rejections: Any) -> Dict[str, int]:
    if not isinstance(rejections, Mapping):
        raise ValueError("rejections: expected a mapping")
    out: Dict[str, int] = {}
    for k in sorted(rejections, key=str):
        n = rejections[k]
        if not isinstance(k, str) or not _REASON_RE.fullmatch(k):
            raise ValueError(f"rejections: bad reason key {k!r}")
        if not isinstance(n, int) or isinstance(n, bool) or n < 0:
            raise ValueError(f"rejections: bad count {n!r} for {k}")
        out[k] = n
    return out


_FACT_SHEET_KEYS = frozenset({
    "schema", "series", "content_class", "source_ref", "record", "rejections", "selection",
})


def fact_sheet(rec: NewsRecord, *, rejections: Mapping[str, int],
               selection: Mapping[str, Any]) -> Dict[str, Any]:
    """The `marketing_scripts.fact_sheet` of a news post:
    ``{"schema": 1, "series", "content_class", "source_ref", "record", "rejections", "selection"}``.
    JSON-safe (``allow_nan=False``); ``selection`` is deep-copied through JSON."""
    if not isinstance(rec, RECORD_TYPES):
        raise ValueError(f"not a news record: {type(rec).__name__}")
    if not isinstance(selection, Mapping):
        raise ValueError("selection: expected a mapping")
    try:
        selection_copy = json.loads(json.dumps(dict(selection), allow_nan=False))
    except TypeError as e:
        raise ValueError(f"selection: not JSON-safe ({e})") from e
    sheet = {
        "schema": FACT_SHEET_SCHEMA,
        "series": rec.series,
        "content_class": SERIES_CLASS[rec.series],
        "source_ref": ledger_key(rec),
        "record": record_to_dict(rec),
        "rejections": _rejections_dict(rejections),
        "selection": selection_copy,
    }
    json.dumps(sheet, allow_nan=False)
    return sheet


def record_from_fact_sheet(sheet: Any) -> NewsRecord:
    """The record of a stored fact sheet, after checking the sheet's own keys, schema, series,
    class and ledger key agree with it (ValueError otherwise)."""
    if not isinstance(sheet, Mapping):
        raise ValueError("fact sheet: expected an object")
    if set(sheet) != _FACT_SHEET_KEYS:
        raise ValueError(f"fact sheet: keys {sorted(sheet)} != {sorted(_FACT_SHEET_KEYS)}")
    if not _schema_ok(sheet.get("schema")):
        raise ValueError(f"fact sheet: schema {sheet.get('schema')!r} != {FACT_SHEET_SCHEMA}")
    rec = record_from_dict(sheet["record"])
    if sheet["series"] != rec.series:
        raise ValueError("fact sheet: series does not match its record")
    if sheet["content_class"] != SERIES_CLASS[rec.series]:
        raise ValueError("fact sheet: content_class does not match its series")
    if sheet["source_ref"] != ledger_key(rec):
        raise ValueError("fact sheet: source_ref does not match its record")
    _rejections_dict(sheet["rejections"])
    if not isinstance(sheet["selection"], Mapping):
        raise ValueError("fact sheet: selection must be an object")
    return rec


__all__ = [
    "FACT_SHEET_SCHEMA", "NEWS_SERIES", "SERIES_CLASS", "LEDGER_PREFIX", "MAJOR_US_EXCHANGES",
    "INSIDER_MIN_MARKET_CAP", "EARNINGS_MIN_MARKET_CAP", "PROFILE_PURPOSES", "INSIDER_WINDOW_DAYS",
    "INSIDER_MIN_AMOUNT_USD", "INSIDER_MAX_AMOUNT_USD", "INSIDER_MAX_ROWS",
    "INSIDER_MAX_FILING_LAG_DAYS", "THIRTEEN_F_MOVES", "THIRTEEN_F_MAX_MOVES",
    "CONGRESS_MIN_MEMBERS", "EPS_MIN_ABS_ESTIMATE", "REVENUE_RATIO_BAND", "MONEY_MAP_SEGMENTS",
    "MONEY_MAP_SUM_TOLERANCE", "MONEY_MAP_EXCLUDED_SECTORS", "THEME_MEMBERS", "THEME_MIN_FACTS",
    "THEME_STALE_DAYS", "THEME_SIZE_MAX", "theme_company_count", "STAKES_INCLUDE_CATALOGUE", "STAKE_STALE_DAYS", "STAKE_MAX_AGE_DAYS",
    "STAKE_MAX_VALUE_USD",
    "STAKE_SOURCE_TITLE_MAX", "STAKE_BACKGROUND_MAX", "STAKE_LOCAL_LISTING_MAX", "non_common_name",
    "free_text_ok", "theme_title_problem",
    "COMPANY_WEEKLY_LAUNCH", "STAKE_KINDS", "VALUE_BASES", "ROSTER_MIN_MEMBERS", "UNAVAILABLE_REASONS", "SKIP_REASONS", "REJECTION_REASONS",
    "SOURCE_LABELS", "THIRTEEN_F_AMENDED_LABEL", "MONEY_MAP_SEED", "MONEY_MAP_EXCLUDED_SYMBOLS",
    "merge_money_map_pool", "symbol_problem", "canonical_symbol", "cik10", "period_label",
    "period_end_of", "previous_period_end_of", "month_end_of", "insider_window", "DISPLAY_OVERRIDES",
    "SEGMENT_DISPLAY_OVERRIDES", "segment_display_name",
    "company_name_problem", "display_company_name", "company_from_profile", "filer_name_problem",
    "THIRTEEN_F_FILERS", "filer_looks_like_person", "ROSTER_WARN_AGE_DAYS", "ROSTER_MAX_AGE_DAYS",
    "congress_start_on_or_before", "roster_congress_start",
    "NICKNAME_GROUPS", "MEMBER_LEGAL_ALIASES", "roster_fetched_on", "roster_fresh_for",
    "render_person_name", "corroborates", "CONGRESS_ROSTER_PATH", "WHALE_REGISTRY_PATH",
    "congress_names", "person_names_allowed", "is_congress_name", "congress_name_hits",
    "CompanyRef", "InsiderPurchase", "InsiderBuysWeek", "ThirteenFMove", "ThirteenFFiling",
    "CongressCount", "CompanyStake", "EarningsReport", "Segment", "MoneyMap", "ThemeMember",
    "ThemeExplainer", "NewsRecord", "RECORD_TYPES", "NESTED_RECORD_TYPES", "ALL_RECORD_TYPES",
    "RECORD_TYPE_BY_SERIES", "ledger_key", "source_label", "record_to_dict", "record_from_dict",
    "fact_sheet", "record_from_fact_sheet",
    # Batch 1 (2026-10-15): the earnings gates and the members-elect window
    "EARNINGS_CLEAR_SURPRISE", "EARNINGS_PERIOD_WINDOW_DAYS", "EARNINGS_PERIOD_MAX_LAG_DAYS",
    "EARNINGS_PERIOD_REVENUE_TOLERANCE", "EARNINGS_ALLOW_SCALE_CHECK", "EARNINGS_SCALE_BAND",
    "earnings_revenue_pair", "clear_surprise", "statement_currency", "earnings_period",
    "general_election_day", "members_elect_window",
    # Batch 1 (2026-10-15): Money Map labels that name nothing, and the regions
    "SEGMENT_ACRONYMS_OK", "GENERIC_SEGMENT_LABELS", "segment_fold_reason", "REGION_NAMES", "REGION_ARTICLE",
    "region_key", "is_region_name", "takes_region_article",
    # Batch 1 (2026-10-15): 13F successor filers and the lead filers read first
    "THIRTEEN_F_SUCCESSORS", "THIRTEEN_F_LEAD_FILERS", "THIRTEEN_F_SUCCESSOR_SINCE", "successor_transition",
    # Batch 1 (2026-10-15): a descriptive stake name takes "the" in running text
    "DESCRIPTIVE_NAME_HEADS", "STAKE_NAME_ARTICLE_MAX_CHARS", "takes_name_article",
    # review of 2026-10-16: the printed surprise, the quarter's shape, the EPS basis, NASDAQ preferred lines and
    # the members-elect window over a run's publishing days
    "EPS_PRINT_PLACES", "EPS_DECIMAL_PRECISION", "printed_eps", "EARNINGS_QUARTER_SPAN_DAYS",
    "EARNINGS_EXCLUDED_SECTORS",
    "EARNINGS_EXCLUDED_INDUSTRY_PREFIX", "EARNINGS_BASIS_ACTUAL_BAND", "EARNINGS_BASIS_ESTIMATE_TOLERANCE",
    "earnings_sector_problem", "earnings_basis_mixed", "NASDAQ_PREFERRED_SYMBOL_RE", "nasdaq_preferred_symbol",
    "MEMBERS_ELECT_PUBLISH_DAYS", "members_elect_for_run",
    # review of 2026-10-16: company-scoped segment labels, the cover's word cap, the stake-source surnames
    "SEGMENT_SYMBOL_OVERRIDES", "COMPANY_NAME_WORD_MAX_CHARS", "company_name_word_too_long",
    "STAKE_SOURCE_PERSON_SURNAMES", "stake_person_surname_hits",
    # the round-3 review of 2026-10-20 (RR3-STAKES-TEMPLATE-WALL): what the stake template can draw, at the read
    "STAKE_SOURCE_DRAW_MAX_CHARS", "STAKE_TEXT_WORD_MAX_CHARS", "STAKE_PAIR_NAME_WORD_MAX_CHARS", "longest_word",
]
