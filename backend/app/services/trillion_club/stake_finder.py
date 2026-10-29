"""The Trillion Club stake finder: the stakes a member newly disclosed in SEC filings.

Three entry points (plan ``~/.claude/plans/eventual-prancing-pebble.md`` v3; the ledger and its
rules are ``backend/database/migrations/205_trillion_club_stake_finder.sql``):

* ``run_stakes(now)`` — the daily 18:30 ET run (``scheduler._stakes_tick``), Mondays with the
  re-check of the published machine rows;
* ``apply_owner_decision(verb, finding_id, …)`` — what ONE owner tap in Telegram does
  (``stake_review.handle_stake_callback``: Withdraw, Keep, Publish, Dismiss);
* ``classify_only(…)`` — the same discovery and classification, read-only, for
  ``scripts/preview_trillion_club_stakes.py``.

No LLM, no FMP. SEC reads go through ``app.integrations.sec_edgar`` (one at a time, its shared
5 rps limiter); every Supabase read and write goes through ``stake_store.StakeStore`` (the stake
rows and the findings ledger) and ``store.TrillionClubStore.read_companies`` (the registry).

ONE RUN, IN ORDER
-----------------
1. Gate 1, the switches: ``TRILLION_CLUB_JOBS_ENABLED``, ``TRILLION_CLUB_STAKE_FINDER_ENABLED``,
   the SEC User-Agent and the review bot. Any one missing (or the registry unreadable) → only the
   DATABASE part of reconcile runs — repairs that hide or record, never a send and never a
   publish — and the run returns ``ok=False`` naming why.
2. The registry: published, effective members that are not ``whale_link`` and are listed in
   ``backend/data/trillion_club_filers.json``. A published member missing from that file gets one
   ``not_enabled`` digest line on the first day of each month. An unreadable filers file is an
   ERROR: no SEC stage runs (reconcile, expiry and the digest still do).
3. RECONCILE, before any SEC call (so it runs when SEC is throttled or the run later stops on an
   SEC error). Every write is fenced on origin + finding_id:
   (a) a machine stake withdrawn but published (205 forbids it) → unpublished;
   (b) ``staged`` → publish gates → note → ``noted`` → flip → ``published`` (a gate that fails
       downgrades: a proposal, or a heads-up when the gate was the identity or a validator);
   (c) ``noted``, unpublished, not withdrawn → publish gates → flip → ``published``;
   (d) ``publishing`` older than ``PUBLISHING_STALE_SECONDS`` → finish (the stake is live, or the
       gates pass and the flip lands) or roll back to ``proposed`` with a digest line;
   (e) ``staged`` / ``noted`` / ``published`` / ``kept`` whose stake is gone (a Studio DELETE:
       the FK set ``stake_id`` NULL) → ``withdrawn`` / ``deleted_in_studio`` (a tombstone) + a line;
   (f) a stake now owned by the seed (adopted by ``seed_trillion_club.py --apply --update``) →
       ``superseded`` + a line (owned by another finding of ours → ``superseded``, silently);
   (g) ``new`` auto / proposal / exit / re-check findings resume from the write-ahead row; an
       ``error`` with ``MAX_ATTEMPTS`` attempts → a ``fetch_failed`` heads-up (one digest line with
       its EDGAR link — never a silent skip);
   (h) a heads-up from a publish-time identity refusal whose duplicate stake is not hidden yet
       (a failure after its ledger write) → hidden, with no owner decision.
   A stake withdrawn while its finding still shows it live (a tap whose ledger write was lost)
   is caught up: the owner's decision is recorded once, from the stake's ``withdrawn_at``.
4. S1, each member's OWN Schedule 13D/13G (structured XML) from its filer CIKs' submissions, in
   ``[today - TRILLION_CLUB_STAKE_LOOKBACK_DAYS, today]`` ET: ``stake_schedule`` reads the
   position, the issuer's submissions give the listing, the member's latest COMPLETE 13F decides
   ``on_13f_note``.
5. S2, EDGAR full-text search for each member alias over the root forms, echo-validated, paged
   (``MAX_EFTS_PAGES_PER_QUERY``; a window holding more is split by day), hits grouped by
   accession, each document read (≤ 12 MiB) and classified by ``stake_templates`` in ONE
   ``asyncio.to_thread`` call (the text conversion and the regexes hold the GIL). The best result
   per accession wins (proposal > heads-up > skipped; ties by document order).
6. Mondays ET: the re-check of every published machine row (a definite pass bumps
   ``verified_on``; a Form 25 / 15 after ``as_of`` is a loud note with Withdraw; anything else —
   an error, an open circuit, a ticker that changed, a private row whose issuer now lists a U.S.
   ticker, a member's own newer 13D/13G of the pair in ANY status (an exit, other facts) —
   changes nothing).
7. Expiry: a proposal unanswered for ``PROPOSAL_TTL_DAYS`` → ``expired`` (a tombstone).
8. The SILENT digest: new heads-ups (deduped per (member, investee CIK, reason) for
   ``DIGEST_DEDUPE_DAYS`` against delivered lines; indications of interest merged across members
   and filings), expiries, hand-overs, Studio deletes, waiting notes, cap / breaker alerts, the
   not-enabled lines, today's skipped count and, on Mondays, the week's skips per investee.
   A line is marked delivered only after the digest was sent.

THE GATES (plan v3; each failure downgrades or skips, never drops silently)
---------------------------------------------------------------------------
1 switches · 2 member in scope · 3 identity dedupe FIRST — for every candidate that would make
or change a row (``stake_names.IdentityIndex`` over the member's stake rows, its open and
in-flight proposals and its tombstones): an open proposal absorbs the filing's evidence (its
message is edited, never resent); the member's own machine row takes the exit / update path; any
other row, a tombstone or a name-only match is a heads-up · 4 the source rules (``stake_schedule``
/ ``stake_templates``) · 5 the validators (``stake_rules.stake_problems`` on the 21 columns, the
read path's ``stake_problem``, migration 205's machine-row rules) · 6 people
(``company_news_rules``, fail closed) · 7 dates (event ≤ filed ≤ today, ``as_of`` at most
``AS_OF_MAX_AGE_DAYS`` old) · and for an auto row only: 8 volume (``AUTO_MAX_PER_DAY`` per ET day,
``AUTO_MAX_PER_WEEK`` per rolling 7 days, counted by the day a row goes LIVE — ``published_at`` —
plus every auto find still on its way to a publish, never by ``created_at``), 9 the breaker
(``BREAKER_WITHDRAWALS`` owner Withdraws since ``TRILLION_CLUB_STAKE_AUTO_SINCE``; it never lifts
on its own), 10 the tier (``TRILLION_CLUB_STAKE_AUTO_TIERS``: only ``13dg`` exists) and
``filed_on >= AUTO_SINCE`` (blank → no auto). Every publish write (the first flip, a reconcile
flip) re-runs gates 1, 3, 5 and 8-10 with fresh reads, right before it is made; a Publish tap
re-runs 3 and 5 (and the age rule of 7).

WRITE ORDERS (every step a fenced, single-shot write; a crash anywhere resumes in reconcile)
--------------------------------------------------------------------------------------------
* auto: ledger ``new`` (write-ahead) → stake INSERT unpublished, origin auto → ``staged`` (+
  stake_id) → gates → LOUD note → ``noted`` (message id) → gates → flip → ``published`` →
  ``jobs._invalidate_read_cache()``. No delivered note, no publish: the run is ``ok=False``.
* proposal: ledger ``new`` (proposed_row, fact_sha) → LOUD proposal → ``proposed``.
* heads-up: ledger ``new`` → the digest → ``reported``. skipped: ledger ``skipped``.
* exit (the member's own 13D/13G now ≤ 5%): ledger ``new`` (with the exit's event date,
  ``_event``) → a LIVE own auto row unpublished → the pair's auto finds still waiting to publish
  held back (``closed``) and its open proposals dated on or before the exit void (``superseded``)
  → LOUD note → both findings ``closed`` (a proposal-origin row stays live: its note offers
  Withdraw). With no live row the exit is one digest line. Whatever order the filings are read in,
  an older filing of the pair is refused against the exit on file (``_decide``, the publish gates,
  the Publish tap).
* At most ``MAX_LOUD_MESSAGES_PER_RUN`` loud messages a run; the rest stay ``new`` (a digest line
  says how many wait — not a failure).

STOP RULES
----------
A plain ``SECRateLimitException`` (the shared circuit) or ``SECConfigException`` stops every SEC
stage; ``SECEdgarFullTextThrottled`` stops S2 only. A ``RUN_DEADLINE_SECONDS`` deadline and the
caps (``MAX_EFTS_PAGES_PER_QUERY``, ``MAX_SEC_REQUESTS_PER_RUN``, ``MAX_DOCUMENTS_PER_RUN``) end the
SEC stages with ``ok=False``; the ledger makes the next attempt resume. A transient failure on ONE
filing records an ``error`` finding (read again at most once per ET day until ``MAX_ATTEMPTS``,
then a ``fetch_failed`` heads-up); one full-text query that fails fails that alias only.

WHAT A LEDGER ROW CARRIES
-------------------------
``proposed_row`` of an auto / proposal finding is the stake row it proposes: the 21
``STAKE_COLUMNS`` plus ``investee_cik`` (and an optional ``_detail`` the Telegram text shows,
e.g. a T2 fixed amount the row may not carry). A heads-up's or a skip's ``proposed_row`` holds
ONLY underscore keys — what its digest line says (``_investee``, ``_detail``, ``_t3``, ``_old``) and,
for an EXIT filing alone, its event date (``_event``: the marker an older filing of the pair is
refused against) — and is never a stake row: nothing reads it as one. ``fact_sha`` is the sha256 of the canonical
JSON of ``proposed_row``; the Publish tap fences on it.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
import uuid
from dataclasses import dataclass, replace
from datetime import date, datetime, time as dtime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

from app.config import settings
from app.integrations import sec_edgar
from app.integrations.sec_edgar import (
    MAX_DISCOVERY_DOCUMENT_BYTES,
    SECConfigException,
    SECEdgarDocumentTooLargeException,
    SECEdgarFullTextThrottled,
    SECException,
    SECRateLimitException,
    normalize_accession,
    normalize_cik,
)
from app.services.trillion_club.stake_contracts import (
    CALLBACK_VERBS,
    DECISION_DELETED_IN_STUDIO,
    DECISION_DISMISSED,
    DECISION_KEPT,
    DECISION_PUBLISHED,
    DECISION_WITHDRAWN,
    MACHINE_ORIGINS,
    MAX_ATTEMPTS,
    ORIGIN_AUTO,
    ORIGIN_PROPOSAL,
    ORIGIN_SEED,
    OUTCOME_AUTO,
    OUTCOME_HEADS_UP,
    OUTCOME_PROPOSAL,
    OUTCOME_SKIPPED,
    PROPOSAL_TTL_DAYS,
    REASONS,
    SOURCE_EFTS,
    SOURCE_SCH13,
    STATUS_CLOSED,
    STATUS_DISMISSED,
    STATUS_ERROR,
    STATUS_EXPIRED,
    STATUS_KEPT,
    STATUS_NEW,
    STATUS_NOTED,
    STATUS_PROPOSED,
    STATUS_PUBLISHED,
    STATUS_PUBLISHING,
    STATUS_REPORTED,
    STATUS_SKIPPED,
    STATUS_STAGED,
    STATUS_SUPERSEDED,
    STATUS_WITHDRAWN,
    TIER_13DG,
    TIER_DIRECTED,
    TIER_INSTRUMENT,
    TIER_IOI,
    TIER_OTHER,
    TIER_SEC_AGREEMENT,
    TIER_SEC_CLOSING,
    TIER_UPDATE,
    TOMBSTONE_STATUSES,
    VERB_KEEP,
    VERB_PUBLISH,
    VERB_WITHDRAW,
    MemberConfig,
    TapResult,
    load_member_configs,
)
from app.services.trillion_club.stake_names import (
    INVESTEE_NAME_MAX,
    KIND_COMMITMENT,
    KIND_ON_13F_NOTE,
    KIND_PRIVATE,
    KIND_US_LISTED_OFF_13F,
    IdentityIndex,
    IdentityMatch,
    archive_url,
    auto_tier_gate,
    choose_us_common_ticker,
    clean_investee_name,
    cusip_identity,
    decide_kind,
    document_source_title,
    normalize_symbol,
    parse_auto_since,
    parse_auto_tiers,
    schedule_document_paths,
    schedule_source_title,
    strip_state_tag,
    unique_preserving,
)
from app.services.trillion_club.stake_rules import STAKE_COLUMNS, stake_problems
from app.services.trillion_club.stake_schedule import (
    ScheduleParseError,
    parse_schedule_xml,
    select_member_position,
)
from app.services.trillion_club.stake_store import (
    AUTO_IN_FLIGHT_STATUSES,
    StakeConflict,
    StakeStore,
    StakeStoreError,
    stake_check_violations,
)
from app.services.trillion_club.stake_templates import TemplateResult, classify_document
from app.services.trillion_club.store import TrillionClubStore
from app.utils.filing_text import html_to_text
from app.utils.market_hours import ET

logger = logging.getLogger(__name__)

# ── Knobs ────────────────────────────────────────────────────────────────────────────────

#: The reviewed member file (backend/data/trillion_club_filers.json).
FILERS_PATH = Path(__file__).resolve().parents[3] / "data" / "trillion_club_filers.json"

#: One run's wall-clock budget (the scheduler's backstop cancels at 50 minutes).
RUN_DEADLINE_SECONDS = 40 * 60
MAX_EFTS_PAGES_PER_QUERY = 5
MAX_SEC_REQUESTS_PER_RUN = 600
#: Discovery documents (S2) read per run; the members' 13D/13G XML counts as SEC requests only.
MAX_DOCUMENTS_PER_RUN = 80
#: Notes, proposals and lifecycle notes (the LOUD messages) per run; edits do not count.
MAX_LOUD_MESSAGES_PER_RUN = 10
AUTO_MAX_PER_DAY = 3
AUTO_MAX_PER_WEEK = 10
BREAKER_WITHDRAWALS = 2
AS_OF_MAX_AGE_DAYS = 365
DIGEST_DEDUPE_DAYS = 30
#: Lines per digest; the rest wait for the next one ("+N more", logged).
DIGEST_MAX_LINES = 60
#: The digest's size budget, in UTF-16 units of its lines: Telegram parts hold ≤ 4,096 units and
#: the splitter fills each at least half way, so 12,000 units stay ≤ 6 parts — under
#: stake_review.MAX_DIGEST_PARTS (8), whose backstop drops lines. Overflow waits, unmarked.
DIGEST_MAX_UNITS = 12_000
#: Investees listed in the Monday summary of the week's skips.
WEEKLY_SKIP_LINES = 20
#: A ``publishing`` finding older than this belongs to reconcile (the tap died mid-way).
PUBLISHING_STALE_SECONDS = 600
#: ``sort_order`` of every machine row: after the owner's hand-ordered rows.
SORT_ORDER = 900
#: The ledger's verified_spans caps (205: ≤ 100 entries, ≤ 64 KiB), kept with room to spare.
LEDGER_SPANS_MAX = 100
LEDGER_SPANS_MAX_BYTES = 60_000
DETAIL_MAX_CHARS = 300

SCHEDULE_FORMS: Tuple[str, ...] = ("SCHEDULE 13D", "SCHEDULE 13D/A", "SCHEDULE 13G", "SCHEDULE 13G/A")
#: Pre-2024-12-18 HTML / text schedules: never read automatically (a heads-up).
LEGACY_SCHEDULE_FORMS: Tuple[str, ...] = ("SC 13D", "SC 13D/A", "SC 13G", "SC 13G/A")
#: ROOT forms only: a root includes its amendments, and a "/A" form in the list returns 0 hits.
EFTS_ROOT_FORMS: Tuple[str, ...] = ("8-K", "6-K", "424B3", "424B4", "424B5", "424B7", "S-1", "F-1")
EFTS_PHRASES: Tuple[str, ...] = (
    "securities purchase agreement", "share purchase agreement", "stock purchase agreement",
    "subscription agreement", "investment agreement", "concurrent private placement",
    "indicated an interest", "directed share issuance",
)
EFTS_PAGE_SIZE = sec_edgar.EFTS_PAGE_SIZE
EFTS_MAX_WINDOW = sec_edgar.EFTS_MAX_WINDOW
#: An issuer's delisting / deregistration forms: after a row's as_of, a definite re-check negative.
DELISTING_FORMS = frozenset({"25", "25-NSE", "15-12G", "15-12B", "15-15D", "15F-12G", "15F-12B", "15F-15D"})

#: The clock the deadline reads (a module attribute so tests can drive it).
_monotonic = time.monotonic

# Toasts (≤ stake_contracts.TOAST_MAX_CHARS).
TOAST_CRASH = "Something went wrong — logged"
TOAST_UNKNOWN = "Unknown button"
TOAST_GONE = "This item is no longer on file"
TOAST_DECIDED = "Already decided"
TOAST_STALE = "This button is from an older message"
TOAST_NOT_LIVE = "Not live yet"
TOAST_PUBLISHING = "Still publishing — check again in a minute"
TOAST_CHANGED = "This proposal changed under the tap — nothing published; tap its newest message"
#: A Publish tap that failed BEFORE any row was written (rolled back: the owner may tap again) —
#: and one that failed after (the row exists: the next run's reconcile finishes the publish).
TOAST_TRY_AGAIN = "Not published — try again"
TOAST_UNFINISHED = "Publishing did not finish — the next run completes it"
#: The marks an exit filing puts on the messages of the pair it stops (review B TAP-1 / GI-2).
VERDICT_HELD_EXIT = "Held back — a later filing reports 5% or less"
VERDICT_DOWN_EXIT = "Taken down — a later filing reports 5% or less"
VERDICT_VOID_EXIT = "Void — a newer filing reports 5% or less"

_TAG = "[tc-stakes]"
_PLACEHOLDER_FINDING = "00000000-0000-4000-8000-000000000000"
_DOCUMENT_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,199}")
_DISPLAY_NAME_RE = re.compile(
    r"(?P<name>.*?)\s*(?:\((?P<tickers>[A-Za-z0-9.,\- ]{1,120})\)\s*)?\(CIK\s*(?P<cik>[0-9]{1,10})\)\s*"
)
_LIVE_OWN_STATUSES = frozenset({STATUS_STAGED, STATUS_NOTED, STATUS_PUBLISHED, STATUS_KEPT})
_OPEN_STATUSES = frozenset({STATUS_PROPOSED, STATUS_PUBLISHING})
#: A publish-time identity refusal of these reasons means a SECOND row for the pair: the hidden
#: duplicate is withdrawn for good (never an open update's target — review B WO-4).
_HIDE_REASONS = frozenset({"existing_row", "tombstone", "possible_match"})
#: Proposal findings an exit filing makes void (a Publish tap in flight — publishing — is left
#: to the tap's own exit check).
_VOIDABLE_STATUSES = frozenset({STATUS_NEW, STATUS_STAGED, STATUS_NOTED, STATUS_PROPOSED})
#: The kinds an AUTO row may have (205's auto scope): a proposal of another kind is never a
#: "would auto-publish" trial (review B facts-F5).
_AUTO_KINDS = frozenset({KIND_ON_13F_NOTE, KIND_US_LISTED_OFF_13F})
#: What one run reads of the ledger by status: everything but the bulk that only grows (reported,
#: skipped, closed, superseded), so the read stays far below the store's 5,000-row ceiling.
_ACTIVE_READ: Tuple[str, ...] = (
    STATUS_NEW, STATUS_STAGED, STATUS_NOTED, STATUS_PUBLISHED, STATUS_KEPT, STATUS_PROPOSED,
    STATUS_PUBLISHING, STATUS_ERROR, STATUS_WITHDRAWN, STATUS_DISMISSED, STATUS_EXPIRED,
)
_FACT_COLUMNS: Tuple[str, ...] = (
    "kind", "investee_cusip", "investee_us_symbol", "ownership_pct", "ownership_basis",
    "disclosed_value_usd", "value_basis", "as_of",
)
_S1_PROPOSAL_REASONS = frozenset({"reporting_type", "group_member", "shared_power", "percent_above_50"})
#: ``no_dispositive_power`` is a heads-up, never a pre-filled proposal: the reported block is
#: (partly) voting power under support agreements, so "<member> holds X%" would be one tap from
#: publishing a holding the member may not own.
_S1_HEADS_UP_REASONS = frozenset({"schedule_unparseable", "member_line_missing", "member_lines_disagree",
                                  "class_unmapped", "legacy_schedule", "cusip_invalid", "no_dispositive_power",
                                  "ownership_disclaimed"})
#: S2 tiers that are evidence of a CLOSED purchase (they may update a commitment row).
_CLOSING_TIERS = frozenset({TIER_SEC_CLOSING, TIER_DIRECTED, TIER_INSTRUMENT})


# ── Small helpers ────────────────────────────────────────────────────────────────────────


def _err(e: BaseException) -> str:
    """``Type: message`` on one line, capped (SEC messages are already scrubbed of the
    User-Agent by the client)."""
    return f"{type(e).__name__}: {' '.join(str(e).split())[:300]}"


def _clip(text: Any, cap: int = DETAIL_MAX_CHARS) -> str:
    out = " ".join(str(text).split()) if text is not None else ""
    return out if len(out) <= cap else out[: cap - 1] + "…"


def _as_date(value: Any) -> Optional[date]:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str) and len(value) >= 10:
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


def _as_ts(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)
    return None


def _et_start(day: date) -> datetime:
    """Midnight ET of ``day``, as an aware datetime."""
    return datetime.combine(day, dtime(0, 0), tzinfo=ET)


def _short_date(day: Optional[date]) -> str:
    return f"{day:%b} {day.day}, {day.year}" if isinstance(day, date) else "an unknown date"


def _hhmm(now: datetime) -> str:
    return now.astimezone(ET).strftime("%H:%M ET")


def _usd_short(value: Any) -> Optional[str]:
    try:
        x = float(value)
    except (TypeError, ValueError):
        return None
    if not x > 0:
        return None
    if x >= 1e9:
        return f"${x / 1e9:.1f}B"
    if x >= 1e6:
        return f"${f'{x / 1e6:.1f}'.rstrip('0').rstrip('.')}M"
    return f"${x:,.0f}"


def _pct_text(value: Any) -> Optional[str]:
    try:
        x = float(value)
    except (TypeError, ValueError):
        return None
    return f"{x:g}%" if x > 0 else None


def _power_text(position: Any) -> str:
    """The numbers behind a ``no_dispositive_power`` heads-up: "reports 19.9% (3,000,000 shares);
    power to sell: sole 0, shared not stated". Never raises: a missing value reads "not stated"."""
    def count(v: Any) -> str:
        try:
            return "not stated" if v is None else f"{Decimal(v):,.0f}"
        except (ArithmeticError, TypeError, ValueError):
            return "not stated"
    person = getattr(position, "person", None)
    pct = _pct_text(getattr(position, "percent", None)) or "a block"
    shares = getattr(position, "shares", None)
    held = f"reports {pct}" + (f" ({count(shares)} shares)" if shares is not None else "")
    return (f"{held}; power to sell: sole {count(getattr(person, 'sole_dispositive', None))}, "
            f"shared {count(getattr(person, 'shared_dispositive', None))}")


def _fact_sha(proposed_row: Mapping[str, Any]) -> str:
    """sha256 of the canonical JSON of the proposed row (the Publish tap fences on it)."""
    body = json.dumps(proposed_row, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _is_stake_row(pr: Any) -> bool:
    return isinstance(pr, Mapping) and all(c in pr for c in STAKE_COLUMNS)


def _stake_columns(pr: Mapping[str, Any]) -> Dict[str, Any]:
    """The 21 hand-kept columns of a proposed row (meta keys and investee_cik dropped)."""
    return {c: pr.get(c) for c in STAKE_COLUMNS}


def _meta(pr: Any, key: str) -> Any:
    return pr.get(key) if isinstance(pr, Mapping) else None


def _exit_event(f: Mapping[str, Any]) -> Optional[date]:
    """The event date of an EXIT FILING's finding — the member's own 13D/13G reporting 5% or less,
    whose ``proposed_row`` carries ``_event`` — else None. Only an exit filing carries it: a
    finding refused BECAUSE of an exit (reason ``exit`` too) never does, so it is never read as
    one (it never takes a row down, and it is never an exit marker)."""
    return _as_date(_meta(f.get("proposed_row"), "_event")) if isinstance(f, Mapping) else None


def _newest_exit(findings: Iterable[Mapping[str, Any]]) -> Optional[date]:
    """The newest exit-filing event date among a pair's findings (any status), or None."""
    days = [d for d in (_exit_event(f) for f in findings) if d is not None]
    return max(days) if days else None


def _exit_binds(row: Mapping[str, Any], exit_on: Optional[date]) -> bool:
    """Does the member's exit filing (it reports 5% or less, as of ``exit_on``) supersede this row?
    Only a row dated on or before the exit that STATES AN OWNERSHIP PERCENTAGE — every member-own
    13D/13G row does (a non-exit one says 5% or more, an exact 5.0% included: stake_schedule reads
    an exit only below 5 or with the box checked). An amount-only fact ("$5.0B invested as of
    Dec 26": every S2 row) stays a true, dated fact: an S2 closing proposal is never voided or
    refused by the member's later 13G/A (verifier R2). No as-of date counts as before the exit."""
    if exit_on is None:
        return False
    as_of = _as_date(row.get("as_of"))
    if as_of is not None and as_of > exit_on:
        return False
    try:
        pct = float(row.get("ownership_pct"))
    except (TypeError, ValueError):
        return False
    return pct == pct          # a stated (finite) percentage


def _is_trial(tier: Any, row: Any) -> bool:
    """"Trial — would auto-publish": an 8-K closing whose row could BE an auto row (205's auto
    scope: a 13F note or an off-13F U.S. listing). A private-kind closing never could."""
    return tier == TIER_SEC_CLOSING and isinstance(row, Mapping) and row.get("kind") in _AUTO_KINDS


def _cap_spans(spans: Iterable[Any]) -> List[str]:
    """Distinct, non-empty span strings within the ledger's caps (oldest kept)."""
    out: List[str] = []
    size = 2
    for s in spans:
        if not isinstance(s, str) or not s.strip() or s in out:
            continue
        cost = len(json.dumps(s, ensure_ascii=False).encode("utf-8")) + 2
        if len(out) >= LEDGER_SPANS_MAX or size + cost > LEDGER_SPANS_MAX_BYTES:
            break
        out.append(s)
        size += cost
    return out


def _same_number(a: Any, b: Any) -> bool:
    if a is None or b is None:
        return a is None and b is None
    try:
        return abs(float(a) - float(b)) < 1e-9
    except (TypeError, ValueError):
        return False


def _same_facts(new: Mapping[str, Any], old: Mapping[str, Any]) -> bool:
    for col in _FACT_COLUMNS:
        if col in ("ownership_pct", "disclosed_value_usd"):
            if not _same_number(new.get(col), old.get(col)):
                return False
        elif col == "investee_us_symbol":
            if normalize_symbol(new.get(col)) != normalize_symbol(old.get(col)):
                return False
        elif str(new.get(col) or "") != str(old.get(col) or ""):
            return False
    return True


def _adds_nothing(new: Mapping[str, Any], old: Mapping[str, Any]) -> bool:
    """Every fact the new row would SHOW is already on the hand-kept row: the same as-of date, and
    each percent / basis / figure the new row carries equals the row's. A figure the filing does
    not state (a 13D carries no purchase price) contradicts nothing. Identifiers (kind, CUSIP,
    ticker) are left out: a hand-kept row's are reviewed by hand."""
    as_of = _as_date(new.get("as_of"))
    if as_of is None or as_of != _as_date(old.get("as_of")):
        return False
    for col in ("ownership_pct", "disclosed_value_usd"):
        if new.get(col) is not None and not _same_number(new.get(col), old.get(col)):
            return False
    return all(new.get(col) is None or str(new.get(col)) == str(old.get(col) or "")
               for col in ("ownership_basis", "value_basis"))


def _row_summary(row: Mapping[str, Any]) -> str:
    """"Nebius · 9.3% of Class A · as of Jul 13, 2026" — for digest and detail lines."""
    bits = [str(row.get("investee_name") or "a row")]
    pct = _pct_text(row.get("ownership_pct"))
    if pct:
        bits.append(f"{pct} {row.get('ownership_basis') or ''}".strip())
    usd = _usd_short(row.get("disclosed_value_usd"))
    if usd:
        bits.append(f"{usd} {row.get('value_basis') or ''}".strip())
    as_of = _as_date(row.get("as_of"))
    if as_of:
        bits.append(f"as of {_short_date(as_of)}")
    return " · ".join(bits)


def _conformed_name(display_name: Any) -> Optional[str]:
    """``"INTEL CORP  (INTC)  (CIK 0000050863)"`` → ``"INTEL CORP"`` (EFTS's display name)."""
    if not isinstance(display_name, str):
        return None
    m = _DISPLAY_NAME_RE.fullmatch(display_name.strip())
    name = (m.group("name") if m else display_name).strip()
    return name or None


def _invalidate() -> None:
    """The Home read cache (``jobs._invalidate_read_cache``: never raises). Imported lazily —
    ``jobs`` loads the 13F machinery, which a tap does not need until it writes."""
    from app.services.trillion_club import jobs

    jobs._invalidate_read_cache()


class _StopSEC(Exception):
    """Every SEC stage stops (the shared circuit, the configuration, the deadline, a cap)."""


class _StopEFTS(Exception):
    """Full-text search stops (its own circuit); the other SEC stages go on."""


class _QueryFailed(Exception):
    """One full-text query cannot be trusted (an echo that is not our query, a malformed answer)."""


# ── The notifier that sends nothing ──────────────────────────────────────────────────────


class NullNotifier:
    """A ``stake_contracts.StakeNotifier`` that sends nothing: ``available()`` is False, so a run
    given it does only the database part of reconcile and stops (``ok=False``). Every send
    returns None / False, like a send that failed."""

    def available(self) -> bool:
        return False

    async def send_auto_note(self, finding: Mapping[str, Any], row: Mapping[str, Any]) -> Optional[int]:
        return None

    async def send_proposal(
        self, finding: Mapping[str, Any], row: Mapping[str, Any], *, trial: bool, evidence: Sequence[str],
    ) -> Optional[int]:
        return None

    async def edit_proposal(
        self, finding: Mapping[str, Any], row: Mapping[str, Any], *, message_id: int, trial: bool,
        evidence: Sequence[str],
    ) -> bool:
        return False

    async def send_lifecycle_note(
        self, finding: Mapping[str, Any], row: Optional[Mapping[str, Any]], *, text: str, offer_withdraw: bool,
    ) -> Optional[int]:
        return None

    async def mark_message(self, *, message_id: int, finding_id: str, verdict: str, buttons: Sequence[str]) -> bool:
        return False

    async def send_digest(self, lines: Sequence[str]) -> bool:
        return False


# ── SEC access for one run: caps, deadline and the stop rules ────────────────────────────


class _SecGate:
    """The SEC client for ONE run. Every call is admitted against the deadline and the request
    cap, and the circuit exceptions become the run's stop rules (``SECEdgarFullTextThrottled``
    is checked FIRST: it is a ``SECRateLimitException`` too, but stops S2 only)."""

    def __init__(self, client: Any, *, deadline: float):
        self.client = client
        self.deadline = deadline
        self.requests = 0
        self.documents = 0
        self.stopped: Optional[str] = None
        self.efts_stopped: Optional[str] = None

    def _admit(self, what: str) -> None:
        if self.stopped:
            raise _StopSEC(self.stopped)
        if _monotonic() >= self.deadline:
            self.stopped = f"the {RUN_DEADLINE_SECONDS // 60}-minute run deadline passed (before {what})"
            raise _StopSEC(self.stopped)
        if self.requests >= MAX_SEC_REQUESTS_PER_RUN:
            self.stopped = f"the {MAX_SEC_REQUESTS_PER_RUN}-request SEC cap was reached (before {what})"
            raise _StopSEC(self.stopped)

    async def _call(self, what: str, fn: Callable[[], Any]) -> Any:
        self._admit(what)
        self.requests += 1
        try:
            return await fn()
        except SECEdgarFullTextThrottled as e:
            self.efts_stopped = f"full-text search is throttled ({_err(e)})"
            raise _StopEFTS(self.efts_stopped) from e
        except SECRateLimitException as e:
            self.stopped = f"SEC throttled the run — every SEC stage stopped ({_err(e)})"
            raise _StopSEC(self.stopped) from e
        except SECConfigException as e:
            self.stopped = f"SEC is not configured ({_err(e)})"
            raise _StopSEC(self.stopped) from e

    async def submissions(self, cik: str) -> Any:
        return await self._call(f"submissions CIK{cik}", lambda: self.client.get_submissions(cik))

    async def document(self, cik: str, accession: str, document: str, *, discovery: bool) -> Any:
        if discovery:
            if self.stopped:
                raise _StopSEC(self.stopped)
            if self.documents >= MAX_DOCUMENTS_PER_RUN:
                self.stopped = f"the {MAX_DOCUMENTS_PER_RUN}-document cap was reached (before {accession})"
                raise _StopSEC(self.stopped)
            self.documents += 1
            return await self._call(
                f"{accession} {document}",
                lambda: self.client.get_filing_document(cik, accession, document,
                                                        max_bytes=MAX_DISCOVERY_DOCUMENT_BYTES))
        return await self._call(f"{accession} {document}",
                                lambda: self.client.get_filing_document(cik, accession, document))

    async def search(self, query: str, *, start: date, end: date, offset: int) -> Any:
        if self.efts_stopped:
            raise _StopEFTS(self.efts_stopped)
        return await self._call(
            f"full-text search {start}..{end} from {offset}",
            lambda: self.client.full_text_search(query, start=start, end=end, forms=EFTS_ROOT_FORMS,
                                                 offset=offset))


# ── Candidates ───────────────────────────────────────────────────────────────────────────


@dataclass
class _Raw:
    """One filing for one member after its SOURCE rules (gate 4), before the identity and the
    row gates. ``outcome`` is what the source rules allow (auto / proposal / heads_up / skipped);
    ``exit`` marks a ≤ 5% filing whose meaning the identity decides; ``error`` a transient SEC
    failure (an ``error`` finding)."""

    slug: str
    source: str
    accession: str
    filer_cik: str
    form: str
    filed_on: date
    document: str
    source_url: str
    investee_cik: Optional[str]
    tier: str
    outcome: str
    reason: str
    row: Optional[Dict[str, Any]] = None
    spans: Tuple[str, ...] = ()
    detail: str = ""
    label: Optional[str] = None             # the investee as the digest names it
    event_date: Optional[date] = None
    exit: bool = False
    cusip: Optional[str] = None             # identity keys of a candidate with no row (an exit)
    symbol: Optional[str] = None
    t3: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    existing: Optional[Dict[str, Any]] = None   # the accession's `error` finding being retried
    closed: bool = False                    # S2: the template read a COMPLETED deal (TemplateResult.closed)


@dataclass
class _Decision:
    """A candidate after every gate the caller ran: what to write (``action``)."""

    raw: _Raw
    action: str                 # insert | append | exit | update | error
    outcome: str
    tier: str
    reason: str
    row: Optional[Dict[str, Any]] = None
    detail: str = ""
    target: Optional[Dict[str, Any]] = None     # append: the open finding · exit/update: the stake
    old: Optional[str] = None                   # the matched row, for the digest line

    @property
    def trial(self) -> bool:
        return self.outcome == OUTCOME_PROPOSAL and _is_trial(self.tier, self.row)


def _heads_up(raw: _Raw, reason: str, detail: str = "", *, old: Optional[str] = None) -> _Decision:
    return _Decision(raw=raw, action="insert", outcome=OUTCOME_HEADS_UP, tier=raw.tier, reason=reason,
                     detail=detail or raw.detail, old=old)


def _skip(raw: _Raw, reason: str, detail: str = "") -> _Decision:
    return _Decision(raw=raw, action="insert", outcome=OUTCOME_SKIPPED, tier=raw.tier, reason=reason,
                     detail=detail or raw.detail)


# ── Shared context: the registry, the member's rows, identity ────────────────────────────


class _Ctx:
    """What the gates read: the parsed registry, the raw company rows (for ``stake_problems``),
    the member configs, and this run's view of the stake rows and the active findings — kept
    current by the writes the run itself makes."""

    def __init__(self, *, today: date, companies: Mapping[str, Any], company_rows: Mapping[str, Mapping[str, Any]],
                 configs: Mapping[str, MemberConfig], stakes: Iterable[Mapping[str, Any]] = (),
                 findings: Iterable[Mapping[str, Any]] = ()):
        self.today = today
        self.companies = dict(companies)
        self.company_rows = dict(company_rows)
        self.configs = dict(configs)
        self.by_cik: Dict[str, Any] = {}
        for co in self.companies.values():
            for cik in co.ciks:
                self.by_cik.setdefault(cik, co)
        self.symbol_aliases: Dict[str, Tuple[str, ...]] = {
            co.cap_symbol: tuple(sorted(co.symbols)) for co in self.companies.values() if co.cap_symbol
        }
        self.stakes: Dict[str, Dict[str, Any]] = {}
        self.findings: Dict[str, Dict[str, Any]] = {}
        #: (slug, investee CIK) → the newest exit filing's event date on file: a row dated on or
        #: before it is never built (gate 3; review B GI-2 / TAP-2). The run loads each pair from
        #: the ledger (any status) before deciding a candidate; classify_only from its own exits.
        self.exits: Dict[Tuple[str, str], date] = {}
        self.reset(stakes, findings)

    def reset(self, stakes: Iterable[Mapping[str, Any]], findings: Iterable[Mapping[str, Any]]) -> None:
        self.stakes = {str(s["id"]): dict(s) for s in stakes if isinstance(s, Mapping) and s.get("id")}
        self.findings = {str(f["id"]): dict(f) for f in findings if isinstance(f, Mapping) and f.get("id")}
        self.exits = {}
        for f in self.findings.values():
            self._note_exit_of(f)

    def display(self, slug: Any) -> str:
        co = self.companies.get(slug) if isinstance(slug, str) else None
        return co.display_name if co is not None else str(slug)

    def put_stake(self, row: Mapping[str, Any]) -> None:
        if isinstance(row, Mapping) and row.get("id"):
            self.stakes[str(row["id"])] = dict(row)

    def put_finding(self, row: Mapping[str, Any]) -> None:
        if isinstance(row, Mapping) and row.get("id"):
            self.findings[str(row["id"])] = dict(row)
            self._note_exit_of(row)

    def _note_exit_of(self, f: Mapping[str, Any]) -> None:
        slug, cik = f.get("company_slug"), f.get("investee_cik")
        if isinstance(slug, str) and isinstance(cik, str):
            self.note_exit(slug, cik, _exit_event(f))

    def note_exit(self, slug: str, cik: str, day: Optional[date]) -> None:
        if day is not None and ((slug, cik) not in self.exits or day > self.exits[(slug, cik)]):
            self.exits[(slug, cik)] = day

    def exit_on(self, slug: Any, cik: Any) -> Optional[date]:
        return self.exits.get((slug, cik)) if isinstance(slug, str) and isinstance(cik, str) else None

    def changed(self, finding_id: str, changes: Mapping[str, Any]) -> None:
        f = self.findings.get(finding_id)
        if f is not None:
            f.update(changes)

    def stake_by_finding(self, finding_id: str) -> Optional[Dict[str, Any]]:
        for s in self.stakes.values():
            if s.get("finding_id") == finding_id:
                return s
        return None

    def index(self, slug: str, *, exclude_findings: Iterable[str] = (), exclude_stakes: Iterable[str] = ()) -> IdentityIndex:
        return IdentityIndex(
            _identity_entries(slug, self.stakes.values(), self.findings.values(),
                              exclude_findings=set(exclude_findings), exclude_stakes=set(exclude_stakes)),
            symbol_aliases=self.symbol_aliases,
        )


def _stake_rank(row: Mapping[str, Any]) -> int:
    """Live rows first, then unpublished, then withdrawn — a live row always wins the identity
    over a tombstone or a hidden duplicate of the same investee."""
    if row.get("withdrawn_at") is not None:
        return 2
    return 0 if row.get("published") is True else 1


def _identity_entries(slug: str, stakes: Iterable[Mapping[str, Any]], findings: Iterable[Mapping[str, Any]], *,
                      exclude_findings: Set[str], exclude_stakes: Set[str]) -> List[Dict[str, Any]]:
    """Gate 3's rows for ONE member, in priority order (``IdentityIndex`` returns the first
    identifier match): open and in-flight proposals (a new filing joins them), then every stake
    row (live first), then the tombstones. Each entry carries ``_src`` and ``_ref``."""
    own_stakes = [dict(s) for s in stakes if s.get("company_slug") == slug and str(s.get("id")) not in exclude_stakes]
    owned_by = {s.get("finding_id") for s in own_stakes if s.get("finding_id")}
    entries: List[Dict[str, Any]] = []
    tombs: List[Dict[str, Any]] = []
    for f in sorted((dict(f) for f in findings if f.get("company_slug") == slug), key=lambda f: str(f.get("created_at"))):
        if str(f.get("id")) in exclude_findings or not _is_stake_row(f.get("proposed_row")):
            continue
        pr = f["proposed_row"]
        # The whole proposed row (so a line can say what the open proposal or the tombstone
        # said), with the finding's own investee CIK.
        entry = {**pr, "investee_cik": f.get("investee_cik") or pr.get("investee_cik"), "_ref": f}
        status, outcome = f.get("status"), f.get("outcome")
        if status in TOMBSTONE_STATUSES:
            tombs.append({**entry, "_src": "tombstone"})
        elif outcome == OUTCOME_PROPOSAL and status in (STATUS_NEW, STATUS_PROPOSED, STATUS_PUBLISHING):
            entries.append({**entry, "_src": "open"})
        elif outcome == OUTCOME_AUTO and status == STATUS_NEW and f.get("id") not in owned_by:
            entries.append({**entry, "_src": "inflight"})
    for s in sorted(own_stakes, key=lambda s: (_stake_rank(s), str(s.get("id")))):
        entries.append({**s, "_src": "stake", "_ref": s})
    return entries + tombs


def _match(ctx: _Ctx, slug: str, *, cik: Optional[str], cusip: Optional[str], symbol: Optional[str],
           name: Optional[str], exclude_findings: Iterable[str] = (), exclude_stakes: Iterable[str] = ()) -> Optional[IdentityMatch]:
    return ctx.index(slug, exclude_findings=exclude_findings, exclude_stakes=exclude_stakes).match(
        investee_cik=cik, cusip=cusip, symbol=symbol, name=name)


# ── The row gates (5 validators, 6 people, 7 dates) — pure ───────────────────────────────


def _validator_problem(row: Mapping[str, Any], *, origin: str, company_rows: Mapping[str, Mapping[str, Any]],
                       today: date) -> Optional[str]:
    """Gate 5: the seed's ``stake_problems`` on the 21 columns, the read path's ``stake_problem``
    and migration 205's machine-row rules — on the row as it will be SHOWN (published)."""
    cols = _stake_columns(row)
    shown = {**cols, "published": True}
    problems = stake_problems(shown, company_rows, today=today)
    if problems:
        return problems[0]
    from app.services.trillion_club_service import stake_problem  # lazy: the read path's module

    read_problem = stake_problem(shown, today)
    if read_problem:
        return f"the read path refuses it ({read_problem})"
    violations = stake_check_violations({
        **cols, "published": False, "origin": origin, "finding_id": _PLACEHOLDER_FINDING,
        "investee_cik": row.get("investee_cik"), "owner_kept_at": None, "withdrawn_at": None,
    })
    if violations:
        return f"migration 205 refuses it ({', '.join(violations)})"
    return None


def _people_problem(name: Any, title: Any, today: date) -> Optional[str]:
    """Gate 6: a person's name in the investee name or the source title — FAIL CLOSED (a check
    that cannot run is a refusal). ``company_news_rules`` is imported here, lazily: it loads the
    congress roster and the marketing lexicons."""
    try:
        from app.services.marketing import company_news_rules as rules
    except Exception as e:  # noqa: BLE001 — fail closed, loudly
        logger.error("[tc-stakes-gate] the person-name check could not load (%s) — every row is a heads-up", _err(e),
                     exc_info=True)
        return "the person-name check could not load"
    try:
        if not rules.person_names_allowed():
            return "the person-name block-list is unavailable (roster or registry)"
        if not rules.roster_fresh_for(today):
            return "the congress roster does not hold the sitting Congress"
        if rules.filer_looks_like_person(name):
            return f"the name {_clip(name, 60)!r} reads as a person's"
        hits = rules.stake_person_surname_hits(f"{name} {title}")
        if hits:
            return f"names {hits[0]!r}"
    except Exception as e:  # noqa: BLE001
        logger.error("[tc-stakes-gate] the person-name check failed (%s) — a heads-up", _err(e), exc_info=True)
        return "the person-name check failed"
    return None


def _dates_problem(*, event_date: Optional[date], filed_on: date, as_of: Optional[date],
                   today: date) -> Optional[Tuple[str, str]]:
    """Gate 7 (ET): event ≤ filed ≤ today, and ``as_of`` at most ``AS_OF_MAX_AGE_DAYS`` old."""
    if as_of is None:
        return "dates_invalid", "the filing gives no event date"
    if event_date is not None and event_date > filed_on:
        return "dates_invalid", f"the event ({event_date}) is after the filing ({filed_on})"
    if filed_on > today:
        return "dates_invalid", f"filed {filed_on}, after today ({today})"
    if as_of > today:
        return "dates_invalid", f"as of {as_of}, after today ({today})"
    if (today - as_of).days > AS_OF_MAX_AGE_DAYS:
        return "too_old", f"as of {as_of}, more than {AS_OF_MAX_AGE_DAYS} days ago"
    return None


def _row_gates(row: Mapping[str, Any], *, origin: str, ctx: _Ctx, filed_on: date,
               event_date: Optional[date]) -> Optional[Tuple[str, str]]:
    """Gates 5, 6 and 7 in that order → ``(reason, detail)`` of the first that fails, or None."""
    problem = _validator_problem(row, origin=origin, company_rows=ctx.company_rows, today=ctx.today)
    if problem:
        return "validator", problem
    people = _people_problem(row.get("investee_name"), row.get("source_title"), ctx.today)
    if people:
        return "person_name", people
    return _dates_problem(event_date=event_date, filed_on=filed_on, as_of=_as_date(row.get("as_of")),
                          today=ctx.today)


# ── Gate 3 + the row gates: one candidate → one decision ─────────────────────────────────


def _decide(raw: _Raw, ctx: _Ctx) -> _Decision:
    """Gates 3, 5, 6 and 7 for one candidate (gates 8-10 are the run's: they read the ledger)."""
    if raw.error is not None:
        return _Decision(raw=raw, action="error", outcome=OUTCOME_HEADS_UP, tier=raw.tier, reason="fetch_failed",
                         detail=raw.error)
    if raw.exit:
        return _decide_exit(raw, ctx)
    if raw.outcome not in (OUTCOME_AUTO, OUTCOME_PROPOSAL):
        return _Decision(raw=raw, action="insert", outcome=raw.outcome, tier=raw.tier, reason=raw.reason,
                         detail=raw.detail)
    row = raw.row or {}
    exit_on = ctx.exit_on(raw.slug, raw.investee_cik)
    if _exit_binds(row, exit_on):
        # The member's own exit filing (5% or less) is dated on or after this filing's facts: they
        # are superseded, whatever order the two were read in (an error retry, a parked find, a
        # correction filed late). Never a row, never an update — one digest line.
        return _heads_up(raw, "exit", _clip(f"the member's later filing reports 5% or less (as of "
                                            f"{_short_date(exit_on)}); this filing says {_row_summary(row)} "
                                            "— not shown"))
    m = _match(ctx, raw.slug, cik=raw.investee_cik, cusip=row.get("investee_cusip"),
               symbol=row.get("investee_us_symbol"), name=row.get("investee_name"))
    if m is not None:
        decided = _decide_match(raw, m, ctx)
        if decided is not None:
            return decided
    origin = ORIGIN_AUTO if raw.outcome == OUTCOME_AUTO else ORIGIN_PROPOSAL
    failed = _row_gates(row, origin=origin, ctx=ctx, filed_on=raw.filed_on, event_date=raw.event_date)
    if failed:
        return _heads_up(raw, failed[0], failed[1])
    return _Decision(raw=raw, action="insert", outcome=raw.outcome, tier=raw.tier, reason=raw.reason, row=dict(row),
                     detail=raw.detail)


def _decide_match(raw: _Raw, m: IdentityMatch, ctx: _Ctx) -> Optional[_Decision]:
    """What an identity match makes of a row candidate (None: no effect — never happens today)."""
    entry, src = m.row, m.row.get("_src")
    old = _row_summary(entry)
    if m.by == "name":
        return _heads_up(raw, "possible_match", f"may be the same company as {old}", old=old)
    if src == "open":
        said = _row_summary(raw.row or {}) + (f" · {raw.detail}" if raw.detail else "")
        if not raw.spans:
            # A 13D/13G carries no verified sentences: there is no evidence to add, and the open
            # proposal's facts never change — the owner reads both sets of facts in the digest.
            return _heads_up(raw, "existing_row", f"an open proposal says {old}; this newer filing says {said}",
                             old=old)
        return _Decision(raw=raw, action="append", outcome=OUTCOME_HEADS_UP, tier=raw.tier, reason="open_proposal",
                         detail=_clip(f"this filing says {said}; the open proposal says {old}"),
                         target=entry["_ref"], old=old)
    if src == "inflight":
        return _heads_up(raw, "existing_row", f"another filing for this pair is being processed ({old})", old=old)
    if src == "tombstone":
        return _heads_up(raw, "tombstone", f"the owner withdrew or dismissed {old}", old=old)
    stake = entry["_ref"]
    if stake.get("origin") not in MACHINE_ORIGINS:
        new_as_of, old_as_of = _as_date((raw.row or {}).get("as_of")), _as_date(stake.get("as_of"))
        if new_as_of and old_as_of and new_as_of < old_as_of:
            return _skip(raw, "existing_row", f"older than the hand-kept row on file ({old})")
        if _adds_nothing(raw.row or {}, stake):
            # A re-filing of what the hand-kept row already shows ("SpaceX · 7.2% → SpaceX · 7.2%")
            # is no news for the owner: recorded and counted, never a digest line.
            return _skip(raw, "existing_row", f"nothing the hand-kept row does not already show ({old})")
        return _heads_up(raw, "existing_row", f"{old} → {_row_summary(raw.row or {})}", old=old)
    if stake.get("withdrawn_at") is not None:
        return _heads_up(raw, "tombstone", f"the owner withdrew {old}", old=old)
    if raw.source == SOURCE_EFTS and not (stake.get("kind") == KIND_COMMITMENT and raw.tier in _CLOSING_TIERS
                                          and raw.closed):
        # The plan's update triggers are a member's own newer 13D/13G (an amendment, a 13F kind
        # change) and closing evidence for a COMMITMENT. Another company's filing about a pair
        # that already has the member's own row is "a newer filing for an existing row".
        return _heads_up(raw, "existing_row", f"{old} → {_row_summary(raw.row or {})}", old=old)
    new = raw.row or {}
    old_as_of, new_as_of = _as_date(stake.get("as_of")), _as_date(new.get("as_of"))
    if _same_facts(new, stake):
        return _skip(raw, "existing_row", f"the same facts as the row on file ({old})")
    if old_as_of and new_as_of and new_as_of < old_as_of:
        return _skip(raw, "existing_row", f"older than the row on file ({old})")
    failed = _row_gates(new, origin=ORIGIN_PROPOSAL, ctx=ctx, filed_on=raw.filed_on, event_date=raw.event_date)
    if failed:
        return _heads_up(raw, failed[0], failed[1], old=old)
    return _Decision(raw=raw, action="update", outcome=OUTCOME_PROPOSAL, tier=TIER_UPDATE, reason="update",
                     row=dict(new), detail=_clip(f"Replaces: {old}" + (f" · {raw.detail}" if raw.detail else "")),
                     target=stake, old=old)


def _same_pair(ctx: _Ctx, row: Mapping[str, Any], *, cik: Optional[str], cusip: Optional[str],
               symbol: Optional[str]) -> bool:
    """``row`` is about the same investee by an IDENTIFIER (investee CIK, CUSIP-6 or symbol, with
    the club's symbol aliases) — never by name alone."""
    return IdentityIndex([row], symbol_aliases=ctx.symbol_aliases).match(
        investee_cik=cik, cusip=cusip, symbol=symbol, name=None) is not None


def _own_live_machine_row(ctx: _Ctx, raw: _Raw) -> Optional[Dict[str, Any]]:
    """The member's OWN live machine row for the exit's pair — looked up among the stake rows
    alone, so an open proposal or an in-flight finding of the pair can never shadow it (review B
    TAP-1: identity puts those first)."""
    for s in sorted(ctx.stakes.values(), key=lambda r: str(r.get("id"))):
        if (s.get("company_slug") == raw.slug and s.get("origin") in MACHINE_ORIGINS and s.get("published") is True
                and s.get("withdrawn_at") is None
                and _same_pair(ctx, s, cik=raw.investee_cik, cusip=raw.cusip, symbol=raw.symbol)):
            return s
    return None


def _exit_waiting(ctx: _Ctx, raw: _Raw, exit_on: date) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """What an exit stops, from the run's view: (the member's AUTO findings of the pair still on
    their way to a publish — new / staged / noted — and its open proposals), each dated on or
    before the exit (a newer one is a re-entry the exit is not about)."""
    hold: List[Dict[str, Any]] = []
    void: List[Dict[str, Any]] = []
    for f in sorted(ctx.findings.values(), key=lambda r: (str(r.get("created_at")), str(r.get("id")))):
        pr = f.get("proposed_row")
        if f.get("company_slug") != raw.slug or not _is_stake_row(pr):
            continue
        entry = {**pr, "investee_cik": f.get("investee_cik") or pr.get("investee_cik")}
        if not _same_pair(ctx, entry, cik=raw.investee_cik, cusip=raw.cusip, symbol=raw.symbol):
            continue
        if not _exit_binds(pr, exit_on):
            continue   # newer than the exit (a re-entry), or a fact the exit does not contradict
        if f.get("outcome") == OUTCOME_AUTO and f.get("status") in AUTO_IN_FLIGHT_STATUSES:
            hold.append(f)
        elif f.get("outcome") == OUTCOME_PROPOSAL and f.get("status") in _VOIDABLE_STATUSES:
            void.append(f)
    return hold, void


def _decide_exit(raw: _Raw, ctx: _Ctx) -> _Decision:
    """An exit filing (≤ 5%) binds its pair whatever order the filings are read in (review B
    TAP-1, GI-2, GI-3, lifecycle-F1/F2):

    * the member's own LIVE machine row (found among the stake rows ALONE, never shadowed by an
      open proposal) takes the exit path — an auto row comes down with a LOUD note, a
      proposal-origin row gets a note offering Withdraw — and the pair's waiting finds and open
      proposals are stopped with it (``go_exit``);
    * no live row, but an auto find of the pair still on its way to a publish, or an open
      proposal dated on or before the exit → those are stopped for good (held back / void) and
      the exit is ONE visible digest line — never a silent skip;
    * otherwise as before: a hand-kept row → a heads-up; a name-only match → a heads-up; a row an
      earlier exit already took down, a tombstone or nothing on file → skipped. Every exit
      finding carries its event date (``_event``): an older filing of the pair read later is
      refused against it (``_decide``, the publish gates, the Publish tap)."""
    exit_on = raw.event_date or raw.filed_on
    live = _own_live_machine_row(ctx, raw)
    if live is not None:
        old = _row_summary(live)
        live_as_of = _as_date(live.get("as_of"))
        if live_as_of is not None and live_as_of > exit_on:
            return _skip(raw, "exit_filing", f"older than the row on file ({old})")
        return _Decision(raw=raw, action="exit", outcome=OUTCOME_HEADS_UP, tier=raw.tier, reason="exit",
                         detail=raw.detail, target=live, old=old)
    hold, void = _exit_waiting(ctx, raw, exit_on)
    if hold or void:
        said = [raw.detail or "reports 5% or less"]
        if hold:
            said.append("held back before it went live: "
                        + "; ".join(_row_summary(f["proposed_row"]) for f in hold))
        if void:
            said.append("the open proposal is void: " + "; ".join(_row_summary(f["proposed_row"]) for f in void))
        first = (hold or void)[0]["proposed_row"]
        return _Decision(raw=raw, action="exit", outcome=OUTCOME_HEADS_UP, tier=raw.tier, reason="exit",
                         detail=_clip(" — ".join(said)), target=None, old=_row_summary(first))
    m = _match(ctx, raw.slug, cik=raw.investee_cik, cusip=raw.cusip, symbol=raw.symbol, name=raw.label)
    if m is None:
        return _skip(raw, "exit_filing", "no row on file for this investee")
    entry, src = m.row, m.row.get("_src")
    old = _row_summary(entry)
    if m.by == "name":
        return _heads_up(raw, "exit", f"reports 5% or less; may be {old}", old=old)
    if src in ("open", "inflight"):
        ref = entry.get("_ref") or {}
        if ref.get("status") == STATUS_PUBLISHING:
            return _heads_up(raw, "exit", f"reports 5% or less while a Publish of {old} is in flight", old=old)
        entry_as_of = _as_date(entry.get("as_of"))
        if entry_as_of is not None and entry_as_of > exit_on:
            return _skip(raw, "exit_filing", f"older than the newer filing being decided ({old})")
        # Dated on or before the exit but a fact it does not contradict (an amount, or 5% or less —
        # _exit_binds): the proposal stays open, and the owner deciding it learns the member now
        # reports 5% or less (verifier R2).
        return _heads_up(raw, "exit", f"reports 5% or less — the open proposal {old} states an amount, not "
                                      "a percentage, and stays open", old=old)
    if src == "tombstone":
        return _skip(raw, "exit_filing", f"the pair is a tombstone ({old})")
    stake = entry["_ref"]
    if stake.get("origin") not in MACHINE_ORIGINS:
        return _heads_up(raw, "exit", f"reports 5% or less — the hand-kept row {old} may need an edit", old=old)
    stake_as_of = _as_date(stake.get("as_of"))
    if stake_as_of is not None and stake_as_of > exit_on:
        # A newer row of the pair (a re-entry still on its way): the exit is not about it — and its
        # date on the ledger never refuses it.
        return _skip(raw, "exit_filing", f"older than the row on file ({old})")
    # A machine row that is not live — an earlier exit took it down, it was held back — or withdrawn.
    return _skip(raw, "exit_filing", f"the row on file is not live ({old})")


# ── Discovery: S1 and S2, the source rules (shared by the run and classify_only) ─────────


@dataclass(frozen=True)
class _Entry:
    accession: str
    filer_cik: str
    form: str
    filed_on: date
    primary_document: str
    legacy: bool


@dataclass(frozen=True)
class _Hit:
    accession: str
    document: str
    ciks: Tuple[str, ...]
    display_name: Optional[str]
    root_form: str
    form: str
    file_type: Optional[str]
    filed_on: date
    items: Tuple[str, ...]
    sequence: int


class _Discovery:
    """S1 and S2 for ONE run: SEC reads (through ``_SecGate``), the source rules and the row
    builder. Writes nothing: ``known`` says which (member, accession) the ledger already holds,
    ``holdings`` the member's latest complete 13F (``None`` = unknown → never ``on_13f_note``)."""

    def __init__(self, *, ctx: _Ctx, sec: _SecGate, start: date, end: date,
                 known: Callable[[str, List[str]], Any], holdings: Callable[[str], Any],
                 fail: Callable[[str, str], None]):
        self.ctx = ctx
        self.sec = sec
        self.start = start
        self.end = end
        self._known = known
        self._holdings = holdings
        self._fail = fail
        self._issuers: Dict[str, Any] = {}

    # ── shared reads ──

    async def issuer(self, cik: str) -> Optional[Dict[str, Any]]:
        """The issuer's submissions (cached per run). None when SEC has none; a transient failure
        raises the client's exception (the caller records an error finding)."""
        if cik in self._issuers:
            return self._issuers[cik]
        subs = await self.sec.submissions(cik)
        if subs is not None and not isinstance(subs, Mapping):
            raise SECException(f"submissions for CIK{cik} are a {type(subs).__name__}, not an object")
        self._issuers[cik] = subs
        return subs

    async def on_13f(self, slug: str, *, cusip: Optional[str], symbol: Optional[str]) -> bool:
        holdings = await self._holdings(slug)
        if not holdings:
            return False
        cusip_id = cusip_identity(cusip)
        if cusip_id and cusip_id in set(holdings.get("cusips") or ()):
            return True
        sym = normalize_symbol(symbol)
        return bool(sym and sym in set(holdings.get("symbols") or ()))

    async def _kind(self, slug: str, *, cusip: Optional[str], symbol: Optional[str]) -> Optional[str]:
        co = self.ctx.companies.get(slug)
        use_13f = bool(co is not None and co.use_13f and co.card_kind == "thirteen_f")
        on_13f = use_13f and bool(symbol) and await self.on_13f(slug, cusip=cusip, symbol=symbol)
        return decide_kind(use_13f=use_13f, on_latest_13f=on_13f, symbol=symbol)

    # ── S1: the member's own Schedule 13D/13G ──

    async def s1(self, cfg: MemberConfig) -> AsyncIterator[_Raw]:
        slug = cfg.slug
        entries: Dict[str, _Entry] = {}
        for filer in cfg.s1_filers:
            try:
                subs = await self.sec.submissions(filer.cik)
            except (_StopSEC, _StopEFTS):
                raise
            except SECException as e:
                self._fail("s1", f"{slug}: submissions of filer CIK {filer.cik} failed ({_err(e)})")
                continue
            if subs is None:
                self._fail("s1", f"{slug}: SEC has no submissions for filer CIK {filer.cik}")
                continue
            try:
                found = _schedule_entries(subs, filer.cik, self.start, self.end)
            except ValueError as e:
                self._fail("s1", f"{slug}: the submissions of filer CIK {filer.cik} are malformed ({e})")
                continue
            for e in found:
                entries.setdefault(e.accession, e)
        if not entries:
            return
        known = await self._known(slug, sorted(entries))
        for e in sorted(entries.values(), key=lambda x: (x.filed_on, x.accession)):
            prior = known.get(e.accession)
            if prior is not None and not _retry_due(prior, self.ctx.today):
                continue
            raw = await self._s1_one(cfg, e)
            raw.existing = prior
            yield raw

    async def _s1_one(self, cfg: MemberConfig, e: _Entry) -> _Raw:
        slug = cfg.slug
        base = dict(slug=slug, source=SOURCE_SCH13, accession=e.accession, filer_cik=e.filer_cik, form=e.form,
                    filed_on=e.filed_on, investee_cik=None, tier=TIER_13DG)
        if e.legacy:
            doc = e.primary_document if _DOCUMENT_RE.fullmatch(e.primary_document or "") else "index.htm"
            return _Raw(**base, document=doc, source_url=archive_url(e.filer_cik, e.accession, doc),
                        outcome=OUTCOME_HEADS_UP, reason="legacy_schedule",
                        detail="an old-format schedule; read it on EDGAR")
        paths = schedule_document_paths(e.primary_document)
        if paths is None:
            return _Raw(**base, document="primary_doc.xml",
                        source_url=archive_url(e.filer_cik, e.accession, "primary_doc.xml"),
                        outcome=OUTCOME_HEADS_UP, reason="schedule_unparseable",
                        detail=_clip(f"unexpected primary document {e.primary_document!r}"))
        doc, folder = paths
        url = archive_url(e.filer_cik, e.accession, doc, folder)
        base.update(document=doc, source_url=url)
        try:
            text = await self.sec.document(e.filer_cik, e.accession, doc, discovery=False)
        except (_StopSEC, _StopEFTS):
            raise
        except SECEdgarDocumentTooLargeException as ex:
            return _Raw(**base, outcome=OUTCOME_HEADS_UP, reason="too_large", detail=_clip(_err(ex)))
        except SECException as ex:
            return _Raw(**base, outcome=OUTCOME_HEADS_UP, reason="fetch_failed", error=_err(ex))
        if text is None:
            return _Raw(**base, outcome=OUTCOME_HEADS_UP, reason="fetch_failed",
                        detail="SEC answered 404 for the schedule's XML")
        try:
            filing = parse_schedule_xml(text)
        except ScheduleParseError as ex:
            return _Raw(**base, outcome=OUTCOME_HEADS_UP, reason=ex.code, detail=_clip(ex.message))
        position = select_member_position(filing, cfg)
        base["investee_cik"] = filing.issuer_cik
        issuer_co = self.ctx.by_cik.get(filing.issuer_cik) if filing.issuer_cik else None
        name = issuer_co.display_name if issuer_co is not None else clean_investee_name(filing.issuer_name)
        label = name or _clip(strip_state_tag(filing.issuer_name or "") or "an unnamed issuer", 80)
        base.update(label=label, event_date=filing.event_date)
        reason = position.reason
        detail = position.derivative_quote or ""
        if reason == "about_member":
            return _Raw(**base, outcome=OUTCOME_SKIPPED, reason="about_member")
        if reason == "exit_filing":
            pct = _pct_text(position.percent)
            return _Raw(**base, outcome=OUTCOME_HEADS_UP, reason="exit_filing", exit=True, cusip=position.cusip,
                        detail=f"reports {pct or '5% or less'}" + (f" as of {filing.event_date}"
                                                                   if filing.event_date else ""))
        if reason is None:
            outcome, reason = OUTCOME_AUTO, "plain_13dg"
        elif reason == "derivative_position":
            if not position.derivative_note:
                return _Raw(**base, outcome=OUTCOME_HEADS_UP, reason="derivative_position", detail=_clip(detail))
            outcome = OUTCOME_PROPOSAL
        elif reason in _S1_PROPOSAL_REASONS:
            if position.derivative_quote and not position.derivative_note:
                # The warrants cannot be counted: a basis without them would misstate the stake.
                return _Raw(**base, outcome=OUTCOME_HEADS_UP, reason="derivative_position",
                            detail=_clip(f"{REASONS[reason]}; {detail}"))
            outcome = OUTCOME_PROPOSAL
        elif reason in _S1_HEADS_UP_REASONS:
            if reason == "no_dispositive_power":
                detail = "; ".join(x for x in (_power_text(position), detail) if x)
            elif reason == "ownership_disclaimed":
                # the filer's own sentence, so the digest line says why (stake_schedule.disclaimer_quote)
                quote = getattr(position, "disclaimer_quote", None)
                detail = "; ".join(x for x in ((f"“{quote}”" if quote else None), detail) if x)
            return _Raw(**base, outcome=OUTCOME_HEADS_UP, reason=reason, detail=_clip(detail))
        else:
            logger.warning("[tc-stakes-s1] %s %s: unknown position reason %r — a heads-up", slug, e.accession, reason)
            return _Raw(**base, outcome=OUTCOME_HEADS_UP,
                        reason=reason if reason in REASONS else "schedule_unparseable", detail=_clip(detail))
        if name is None:
            return _Raw(**base, outcome=OUTCOME_HEADS_UP, reason="name_unusable",
                        detail=_clip(f"the issuer name {filing.issuer_name!r} cannot be shown as written"))
        if position.percent is None or position.basis is None:
            return _Raw(**base, outcome=OUTCOME_HEADS_UP, reason="schedule_unparseable",
                        detail="the position has no percent or class basis")
        try:
            subs = await self.issuer(filing.issuer_cik)
        except (_StopSEC, _StopEFTS):
            raise
        except SECException as ex:
            return _Raw(**base, outcome=OUTCOME_HEADS_UP, reason="fetch_failed", error=_err(ex))
        if subs is None:
            return _Raw(**base, outcome=OUTCOME_HEADS_UP, reason="fetch_failed",
                        detail=f"SEC has no submissions for the issuer CIK {filing.issuer_cik}")
        symbol, why = choose_us_common_ticker(subs.get("tickers"), subs.get("exchanges"))
        if why == "ambiguous_listing":
            return _Raw(**base, outcome=OUTCOME_HEADS_UP, reason="ambiguous_listing",
                        detail=_clip(f"tickers {subs.get('tickers')!r}"))
        if symbol is None:
            kind = KIND_PRIVATE
            if outcome == OUTCOME_AUTO:
                outcome, reason = OUTCOME_PROPOSAL, "no_us_listing"
        else:
            try:
                kind = await self._kind(slug, cusip=position.cusip, symbol=symbol)
            except StakeStoreError as ex:
                # Never guess "not on the 13F": an error finding, retried by the next run.
                return _Raw(**base, outcome=OUTCOME_HEADS_UP, reason="fetch_failed",
                            error=f"the member's 13F holdings could not be read ({_err(ex)})")
        basis = position.basis
        if position.derivative_note:
            basis = f"{basis}, {position.derivative_note}"
        title = schedule_source_title(cfg.source_name, e.form, e.filed_on)
        if title is None:
            return _Raw(**base, outcome=OUTCOME_HEADS_UP, reason="schedule_unparseable",
                        detail=f"no source title for form {e.form!r}")
        row = _row(slug=slug, kind=kind, name=name, cusip=position.cusip,
                   symbol=symbol if kind != KIND_PRIVATE else None, pct=float(position.percent), basis=basis,
                   value=None, value_basis=None, as_of=filing.event_date, title=title, url=url,
                   today=self.ctx.today, investee_cik=filing.issuer_cik)
        return _Raw(**base, outcome=outcome, reason=reason, row=row, detail=_clip(detail),
                    cusip=position.cusip, symbol=symbol)

    # ── S2: EDGAR full-text search over other companies' filings ──

    async def s2(self, cfg: MemberConfig, other_aliases: Sequence[str]) -> AsyncIterator[_Raw]:
        slug = cfg.slug
        hits: Dict[Tuple[str, str], _Hit] = {}
        for alias in cfg.s2_aliases:
            try:
                found = await self._search(alias, self.start, self.end)
            except (_StopSEC, _StopEFTS):
                raise
            except _QueryFailed as e:
                self._fail("s2", f"{slug}: the full-text query for {alias!r} failed closed ({e})")
                continue
            except SECException as e:
                # One query's upstream failure (a 5xx / timeout after the client's retry, another
                # 4xx, an answer too large) — never the whole stage: the stop rules arrive here
                # already turned into _StopSEC / _StopEFTS by _SecGate (review B S2-SECEXCEPTION).
                self._fail("s2", f"{slug}: full-text search for {alias!r} failed ({_err(e)}) — the next run retries")
                continue
            for h in found:
                hits.setdefault((h.accession, h.document), h)
        groups: Dict[str, List[_Hit]] = {}
        for h in hits.values():
            groups.setdefault(h.accession, []).append(h)
        if not groups:
            return
        known = await self._known(slug, sorted(groups))
        co = self.ctx.companies.get(slug)
        own_ciks = set(cfg.filer_ciks) | set(co.ciks if co is not None else ())
        for acc in sorted(groups, key=lambda a: (min(h.filed_on for h in groups[a]), a)):
            prior = known.get(acc)
            if prior is not None and not _retry_due(prior, self.ctx.today):
                continue
            group = sorted(groups[acc], key=lambda h: (h.sequence, h.document))
            raw = await self._s2_accession(cfg, group, own_ciks, other_aliases)
            raw.existing = prior
            yield raw

    async def _search(self, alias: str, start: date, end: date) -> List[_Hit]:
        query = efts_query(alias)
        hits: List[_Hit] = []
        offset, pages = 0, 0
        while True:
            resp = await self.sec.search(query, start=start, end=end, offset=offset)
            problem = echo_problem(resp, alias=alias, start=start, end=end, offset=offset)
            if problem:
                raise _QueryFailed(problem)
            total, relation, page, sent = _hits_page(resp)
            if pages == 0 and start < end and (
                    relation != "eq" or total > EFTS_MAX_WINDOW or total > MAX_EFTS_PAGES_PER_QUERY * EFTS_PAGE_SIZE):
                logger.info("[tc-stakes-s2] %r %s..%s: %d hit(s) (%s) — split by day", alias, start, end, total, relation)
                out: List[_Hit] = []
                day = start
                while day <= end:
                    out.extend(await self._search(alias, day, day))
                    day += timedelta(days=1)
                return out
            hits.extend(page)
            pages += 1
            # The last page is decided on what EFTS SENT, never on what _parse_hit kept: a full
            # page with one refused hit is not the end (review B S2-PAGING-FILTERED-COUNT).
            if sent < EFTS_PAGE_SIZE or offset + EFTS_PAGE_SIZE >= total:
                return hits
            if pages >= MAX_EFTS_PAGES_PER_QUERY or offset + EFTS_PAGE_SIZE >= EFTS_MAX_WINDOW:
                self._fail("s2", f"{alias!r} {start}..{end}: {total} hits, over the "
                                 f"{MAX_EFTS_PAGES_PER_QUERY}-page cap — the rest waits for the next run")
                return hits
            offset += EFTS_PAGE_SIZE

    async def _s2_accession(self, cfg: MemberConfig, group: List[_Hit], own_ciks: Set[str],
                            other_aliases: Sequence[str]) -> _Raw:
        slug = cfg.slug
        first = group[0]
        filer = first.ciks[0] if first.ciks else None
        multi = any(len(h.ciks) != 1 for h in group) or len({h.ciks[0] for h in group if h.ciks}) > 1
        base = dict(slug=slug, source=SOURCE_EFTS, accession=first.accession, filer_cik=filer or "",
                    form=first.form, filed_on=first.filed_on, document=first.document,
                    source_url=archive_url(filer, first.accession, first.document) if filer else "",
                    investee_cik=None if multi else filer, tier=TIER_OTHER,
                    label=_conformed_name(first.display_name))
        if multi or filer is None:
            return _Raw(**base, outcome=OUTCOME_HEADS_UP, reason="multi_cik",
                        detail=_clip(f"registrants {sorted({c for h in group for c in h.ciks})}"))
        if filer in own_ciks:
            return _Raw(**base, outcome=OUTCOME_HEADS_UP, reason="member_filed")
        conformed = _conformed_name(first.display_name)
        if conformed is None:
            try:
                subs = await self.issuer(filer)
            except (_StopSEC, _StopEFTS):
                raise
            except SECException as ex:
                return _Raw(**base, outcome=OUTCOME_HEADS_UP, reason="fetch_failed", error=_err(ex))
            conformed = (subs or {}).get("name") if isinstance((subs or {}).get("name"), str) else None
        if not conformed:
            return _Raw(**base, outcome=OUTCOME_HEADS_UP, reason="s2_name_mismatch",
                        detail="SEC gives no name for the filer")
        best: Optional[Tuple[TemplateResult, _Hit]] = None
        for h in group:
            try:
                raw_text = await self.sec.document(filer, h.accession, h.document, discovery=True)
            except (_StopSEC, _StopEFTS):
                if best is not None and best[0].outcome == OUTCOME_PROPOSAL:
                    break
                raise
            except SECEdgarDocumentTooLargeException as ex:
                result = _plain_result("too_large", OUTCOME_HEADS_UP, _clip(_err(ex)))
            except SECException as ex:
                if best is not None and best[0].outcome == OUTCOME_PROPOSAL:
                    break
                return _Raw(**base, outcome=OUTCOME_HEADS_UP, reason="fetch_failed", error=_err(ex))
            else:
                if raw_text is None:
                    result = _plain_result("fetch_failed", OUTCOME_HEADS_UP, f"SEC answered 404 for {h.document}")
                else:
                    try:
                        result = await asyncio.to_thread(
                            _classify_text, raw_text, h, list(cfg.s2_aliases), conformed, list(other_aliases))
                    except Exception as ex:  # noqa: BLE001 — a template bug: an error finding, loudly
                        logger.error("[tc-stakes-s2] %s %s %s: classification raised (%s)", slug, h.accession,
                                     h.document, _err(ex), exc_info=True)
                        return _Raw(**base, outcome=OUTCOME_HEADS_UP, reason="fetch_failed",
                                    error=f"classification raised {_err(ex)}")
                del raw_text   # the raw document is never kept past its conversion
            if best is None or _rank(result) > _rank(best[0]):
                best = (result, h)
            if result.outcome == OUTCOME_PROPOSAL:
                break
        assert best is not None
        result, hit = best
        url = archive_url(filer, hit.accession, hit.document)
        base.update(document=hit.document, source_url=url, tier=result.tier, spans=tuple(result.spans),
                    event_date=result.event_date, label=result.company_name or base["label"],
                    closed=result.closed is True)
        detail = _clip(result.detail)
        if result.outcome == OUTCOME_SKIPPED:
            return _Raw(**base, outcome=OUTCOME_SKIPPED, reason=result.reason, detail=detail)
        if result.outcome == OUTCOME_HEADS_UP:
            t3 = None
            if result.tier == TIER_IOI:
                t3 = {"key": result.group_term or (result.spans[0] if result.spans else hit.accession),
                      "company": result.company_name or base["label"],
                      "members": list(result.group_members),
                      # a defined group or a several-name subject: the amount is THEIR figure
                      "combined": bool(result.group_term) or bool(result.group_members),
                      "amount": float(result.amount_usd) if result.amount_usd is not None else None}
            return _Raw(**base, outcome=OUTCOME_HEADS_UP, reason=result.reason, detail=detail, t3=t3)
        return await self._s2_row(cfg, base, result, hit, filer, detail)

    async def _s2_row(self, cfg: MemberConfig, base: Dict[str, Any], result: TemplateResult, hit: _Hit,
                      filer: str, detail: str) -> _Raw:
        slug = cfg.slug
        club = self.ctx.by_cik.get(filer)
        base_name = club.display_name if club is not None else result.company_name
        if not base_name:
            return _Raw(**base, outcome=OUTCOME_HEADS_UP, reason="name_unusable", detail=detail)
        name = f"{base_name} {result.instrument_label}" if result.instrument_label else base_name
        if len(name) > INVESTEE_NAME_MAX:
            return _Raw(**base, outcome=OUTCOME_HEADS_UP, reason="name_unusable",
                        detail=_clip(f"{name!r} is over {INVESTEE_NAME_MAX} characters"))
        symbol: Optional[str] = None
        if result.tier == TIER_SEC_AGREEMENT:
            kind = KIND_COMMITMENT
        else:
            try:
                subs = await self.issuer(filer)
            except (_StopSEC, _StopEFTS):
                raise
            except SECException as ex:
                return _Raw(**base, outcome=OUTCOME_HEADS_UP, reason="fetch_failed", error=_err(ex))
            symbol, why = (choose_us_common_ticker(subs.get("tickers"), subs.get("exchanges"))
                           if subs is not None else (None, "no_us_listing"))
            if why == "ambiguous_listing":
                return _Raw(**base, outcome=OUTCOME_HEADS_UP, reason="ambiguous_listing",
                            detail=_clip(f"tickers {(subs or {}).get('tickers')!r}"))
            if result.tier == TIER_INSTRUMENT:
                # A preferred / warrant / note is never the common line a 13F note is about.
                kind = KIND_US_LISTED_OFF_13F if symbol else KIND_PRIVATE
            else:
                try:
                    kind = (await self._kind(slug, cusip=None, symbol=symbol)) if symbol else None
                except StakeStoreError as ex:
                    return _Raw(**base, outcome=OUTCOME_HEADS_UP, reason="fetch_failed",
                                error=f"the member's 13F holdings could not be read ({_err(ex)})")
                kind = kind or KIND_PRIVATE
        value = float(result.amount_usd) if result.amount_usd is not None and result.value_basis else None
        title = document_source_title(base_name, hit.form, hit.filed_on)
        row = _row(slug=slug, kind=kind, name=name, cusip=None,
                   symbol=symbol if kind in (KIND_ON_13F_NOTE, KIND_US_LISTED_OFF_13F) else None,
                   pct=None, basis=None, value=value, value_basis=result.value_basis if value else None,
                   as_of=result.event_date or hit.filed_on, title=title, url=base["source_url"],
                   today=self.ctx.today, investee_cik=filer)
        return _Raw(**base, outcome=OUTCOME_PROPOSAL, reason=result.reason, row=row, detail=detail, symbol=symbol)


def _retry_due(prior: Mapping[str, Any], today: date) -> bool:
    """A filing already in the ledger is read again only when it is an ``error`` finding with
    attempts left whose LAST attempt (its ``updated_at``) was on an earlier ET day: the scheduler
    retries a failed run hourly, and one evening's retries must not spend every attempt
    (review B lifecycle-F3). An unreadable stamp reads as due."""
    if prior.get("status") != STATUS_ERROR or int(prior.get("attempts") or 0) >= MAX_ATTEMPTS:
        return False
    last = _as_ts(prior.get("updated_at"))
    return last is None or last.astimezone(ET).date() < today


def _plain_result(reason: str, outcome: str, detail: str) -> TemplateResult:
    return TemplateResult(tier=TIER_OTHER, reason=reason, outcome=outcome, company_name=None, instrument_label=None,
                          amount_usd=None, value_basis=None, shares=None, price_per_share=None, event_date=None,
                          spans=(), group_term=None, group_members=(), detail=_clip(detail))


def _rank(r: TemplateResult) -> int:
    return {OUTCOME_PROPOSAL: 2, OUTCOME_HEADS_UP: 1}.get(r.outcome, 0)


def _classify_text(raw: str, hit: _Hit, aliases: List[str], conformed: str, others: List[str]) -> TemplateResult:
    """The worker-thread step: ONE conversion of the document and ONE classification."""
    text = html_to_text(raw, document=hit.document, max_chars=None, log_tag="tc-stakes")
    return classify_document(text=text, member_aliases=aliases, filer_conformed_name=conformed, form=hit.root_form,
                             items=list(hit.items), filed_on=hit.filed_on, file_type=hit.file_type,
                             other_member_aliases=others)


def _row(*, slug: str, kind: str, name: str, cusip: Optional[str], symbol: Optional[str], pct: Optional[float],
         basis: Optional[str], value: Optional[float], value_basis: Optional[str], as_of: Optional[date], title: str,
         url: str, today: date, investee_cik: Optional[str]) -> Dict[str, Any]:
    """The 21 ``STAKE_COLUMNS`` of a machine row, plus ``investee_cik`` (plan v3 "Row fields")."""
    return {
        "company_slug": slug, "kind": kind, "investee_name": name, "investee_cusip": cusip_identity(cusip),
        "investee_us_symbol": symbol, "local_listing": None, "ownership_pct": pct, "ownership_basis": basis,
        "disclosed_value_usd": value, "value_basis": value_basis,
        "as_of": as_of.isoformat() if isinstance(as_of, date) else None, "source_title": title,
        "source_url": url, "source_confidence": "primary", "material": False, "tied_to_deal": False,
        "listed_since": None, "background": None, "verified_on": today.isoformat(), "published": False,
        "sort_order": SORT_ORDER, "investee_cik": investee_cik,
    }


def _schedule_entries(subs: Mapping[str, Any], filer_cik: str, start: date, end: date) -> List[_Entry]:
    """The structured and legacy 13D/13G filings in ``[start, end]`` from a submissions record's
    ``filings.recent`` parallel arrays (ValueError when they are not parallel lists)."""
    recent = (subs.get("filings") or {}).get("recent") if isinstance(subs.get("filings"), Mapping) else None
    if not isinstance(recent, Mapping):
        raise ValueError("no filings.recent object")
    cols = {k: recent.get(k) for k in ("accessionNumber", "form", "filingDate", "primaryDocument")}
    if not all(isinstance(v, list) for v in cols.values()) or len({len(v) for v in cols.values()}) != 1:
        raise ValueError("filings.recent arrays are missing or not parallel")
    out: List[_Entry] = []
    for acc_raw, form_raw, day_raw, doc in zip(*cols.values()):
        form = " ".join(str(form_raw).upper().split()) if isinstance(form_raw, str) else ""
        if form not in SCHEDULE_FORMS and form not in LEGACY_SCHEDULE_FORMS:
            continue
        acc, filed = normalize_accession(acc_raw), _as_date(day_raw)
        if acc is None or filed is None:
            logger.warning("[tc-stakes-s1] CIK%s: a %s row with accession %r / date %r skipped", filer_cik, form,
                           acc_raw, day_raw)
            continue
        if not start <= filed <= end:
            continue
        out.append(_Entry(accession=acc, filer_cik=filer_cik, form=form, filed_on=filed,
                          primary_document=doc if isinstance(doc, str) else "",
                          legacy=form in LEGACY_SCHEDULE_FORMS))
    return out


def efts_query(alias: str) -> str:
    """``"<alias>" "<p1>" OR … OR "<p8>"`` — no parentheses (EFTS reads them as literal text)."""
    return f'"{alias}" ' + " OR ".join(f'"{p}"' for p in EFTS_PHRASES)


def echo_problem(resp: Any, *, alias: str, start: date, end: date, offset: int) -> Optional[str]:
    """Why EFTS's echo of the search it ran is not OUR search (None when it is): the alias as the
    one ``must`` phrase, the 8 phrases as ``should`` with ``minimum_should_match`` 1, the root
    forms and the window as the filters, page size 100 from ``offset``, no ``must_not``."""
    if not isinstance(resp, Mapping):
        return f"the answer is a {type(resp).__name__}"
    echo = resp.get("query")
    if not isinstance(echo, Mapping):
        return "no query echo"
    if echo.get("size") != EFTS_PAGE_SIZE:
        return f"page size {echo.get('size')!r}"
    if (echo.get("from") or 0) != offset:
        return f"from {echo.get('from')!r}, asked {offset}"
    inner = echo.get("query")
    bool_q = inner.get("bool") if isinstance(inner, Mapping) else None
    if not isinstance(bool_q, Mapping):
        return "no bool query"

    def phrases(items: Any) -> Optional[List[str]]:
        if not isinstance(items, list):
            return None
        out = []
        for it in items:
            mp = it.get("match_phrase") if isinstance(it, Mapping) else None
            text = mp.get("doc_text") if isinstance(mp, Mapping) else None
            if not isinstance(text, str):
                return None
            out.append(text)
        return out

    if phrases(bool_q.get("must")) != [alias]:
        return f"must {bool_q.get('must')!r}"
    should = phrases(bool_q.get("should"))
    if should is None or sorted(should) != sorted(EFTS_PHRASES):
        return "should is not the 8 deal phrases"
    if bool_q.get("minimum_should_match") != 1:
        return f"minimum_should_match {bool_q.get('minimum_should_match')!r}"
    if bool_q.get("must_not") != []:
        return f"must_not {bool_q.get('must_not')!r}"
    filters = bool_q.get("filter")
    if not isinstance(filters, list) or len(filters) != 2:
        return "the filters are not the forms and the window"
    forms_ok = window_ok = False
    for f in filters:
        terms = f.get("terms") if isinstance(f, Mapping) else None
        rng = f.get("range") if isinstance(f, Mapping) else None
        if isinstance(terms, Mapping) and isinstance(terms.get("root_forms"), list):
            forms_ok = sorted(terms["root_forms"]) == sorted(EFTS_ROOT_FORMS)
        elif isinstance(rng, Mapping) and isinstance(rng.get("file_date"), Mapping):
            fd = rng["file_date"]
            window_ok = fd.get("gte") == start.isoformat() and fd.get("lte") == end.isoformat()
    if not forms_ok:
        return "the forms filter is not the root forms"
    if not window_ok:
        return "the date filter is not the asked window"
    return None


def _hits_page(resp: Mapping[str, Any]) -> Tuple[int, str, List[_Hit], int]:
    """(total, relation, the hits _parse_hit accepted, how many items EFTS sent on this page). A
    refused hit is logged and skipped; paging reads the SENT count."""
    hits = resp.get("hits")
    if not isinstance(hits, Mapping):
        raise _QueryFailed("no hits object")
    total = hits.get("total")
    if not isinstance(total, Mapping) or isinstance(total.get("value"), bool) or not isinstance(total.get("value"), int):
        raise _QueryFailed("no hits.total")
    relation = total.get("relation") if isinstance(total.get("relation"), str) else "gte"
    items = hits.get("hits")
    if not isinstance(items, list):
        raise _QueryFailed("hits.hits is not a list")
    page: List[_Hit] = []
    for item in items:
        h = _parse_hit(item)
        if h is None:
            logger.warning("[tc-stakes-s2] a malformed full-text hit skipped: %s", _clip(repr(item)[:200], 200))
            continue
        page.append(h)
    return total["value"], relation, page, len(items)


def _parse_hit(item: Any) -> Optional[_Hit]:
    if not isinstance(item, Mapping) or not isinstance(item.get("_source"), Mapping):
        return None
    src, hid = item["_source"], item.get("_id")
    if not isinstance(hid, str) or ":" not in hid:
        return None
    acc_raw, document = hid.split(":", 1)
    acc = normalize_accession(src.get("adsh") or acc_raw)
    if acc is None or acc != normalize_accession(acc_raw) or not _DOCUMENT_RE.fullmatch(document):
        return None
    ciks_raw = src.get("ciks")
    if not isinstance(ciks_raw, list) or not ciks_raw:
        return None
    ciks = tuple(c for c in (normalize_cik(x) for x in ciks_raw) if c)
    if len(ciks) != len(ciks_raw):
        return None
    roots = src.get("root_forms")
    root = roots[0] if isinstance(roots, list) and roots and isinstance(roots[0], str) else None
    filed = _as_date(src.get("file_date"))
    form = src.get("form") if isinstance(src.get("form"), str) else root
    if root is None or filed is None or not form or len(form) > 40:
        return None
    names = src.get("display_names")
    items = src.get("items") if isinstance(src.get("items"), list) else []
    seq_raw = src.get("sequence")
    try:
        seq = int(seq_raw) if not isinstance(seq_raw, bool) else 1
    except (TypeError, ValueError):
        seq = 1_000_000
    return _Hit(accession=acc, document=document, ciks=ciks,
                display_name=names[0] if isinstance(names, list) and names and isinstance(names[0], str) else None,
                root_form=root, form=form, file_type=src.get("file_type") if isinstance(src.get("file_type"), str) else None,
                filed_on=filed, items=tuple(i for i in items if isinstance(i, str)), sequence=seq)


# ── classify_only (the preview) ──────────────────────────────────────────────────────────


def _parse_registry(rows: Iterable[Any], fail: Optional[Callable[[str, str], None]] = None
                    ) -> Tuple[Dict[str, Any], Dict[str, Dict[str, Any]]]:
    """``({slug: jobs.Company}, {slug: raw row})``; a malformed row is skipped loudly."""
    from app.services.trillion_club.jobs import parse_company

    companies: Dict[str, Any] = {}
    raw_rows: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        try:
            co = parse_company(row)
        except ValueError as e:
            logger.error("%s registry row skipped (%s) — fix it in Studio", _TAG, e)
            if fail is not None:
                fail("registry", f"malformed company row: {e}")
            continue
        if co.slug in companies:
            logger.error("%s duplicate registry slug %r skipped", _TAG, co.slug)
            continue
        companies[co.slug] = co
        raw_rows[co.slug] = dict(row)
    return companies, raw_rows


def _in_scope(companies: Mapping[str, Any], configs: Mapping[str, MemberConfig]) -> Tuple[List[MemberConfig], List[str]]:
    """Gate 2: (the members to search, sorted by slug; the published members missing from the
    filers file)."""
    members: List[MemberConfig] = []
    missing: List[str] = []
    for slug in sorted(companies):
        co = companies[slug]
        if not co.published or not co.effective_member or co.card_kind == "whale_link":
            continue
        if slug in configs:
            members.append(configs[slug])
        else:
            missing.append(slug)
    for slug in sorted(set(configs) - set(companies)):
        logger.warning("%s the filers file lists %r, which is not in the registry", _TAG, slug)
    return members, missing


def _decision_dict(d: _Decision) -> Dict[str, Any]:
    raw = d.raw
    return {
        "slug": raw.slug, "source": raw.source, "accession": raw.accession, "form": raw.form,
        "filed_on": raw.filed_on.isoformat(), "document": raw.document, "source_url": raw.source_url,
        "investee_cik": raw.investee_cik, "tier": d.tier, "outcome": d.outcome, "reason": d.reason,
        "row": dict(d.row) if d.row else None, "spans": list(raw.spans), "detail": d.detail or "",
        "trial": d.trial,
    }


class ClassifyResult(list):
    """``classify_only``'s answer: a list of findings (one dict each), plus ``problems`` — every
    part that did not finish (a member whose stage failed, a full-text query that failed, a stop
    rule). Empty ``problems`` = the classification is complete; otherwise the list holds what WAS
    classified, and the caller must say it is partial (the preview exits 1)."""

    def __init__(self, items: Iterable[Any] = (), problems: Iterable[str] = ()):
        super().__init__(items)
        self.problems: List[str] = list(problems)

    @property
    def complete(self) -> bool:
        return not self.problems


async def classify_only(*, now: datetime, sec: Any, companies: Sequence[Mapping[str, Any]],
                        stakes: Sequence[Mapping[str, Any]], member_configs: Mapping[str, MemberConfig], since: date,
                        holdings: Optional[Mapping[str, Any]] = None) -> List[Dict[str, Any]]:
    """S1 + S2 discovery and classification with gates 2-7 against the GIVEN rows — READ-ONLY:
    no store, no notifier, no write. The preview's entry point.

    ``companies`` / ``stakes`` are registry and stake rows (the seed's, in the preview);
    ``holdings`` optionally maps a member slug to its latest complete 13F (``{"cusips": …,
    "symbols": …}``) — absent, a row is never ``on_13f_note``. Gates 8-10 read the ledger and are
    not run, so ``outcome`` "auto" means AUTO-ELIGIBLE. A candidate that would make a row joins
    the identity view of the ones after it, as in the run (an exit filing binds its pair for the
    ones after it too). One dict per finding: slug, source, accession, form, filed_on, document,
    source_url, investee_cik, tier, outcome, reason, row, spans, detail, trial.

    Returns a ``ClassifyResult`` (a list): a failure of one member's stage or one query never
    loses what was already classified — it is listed in ``.problems`` (review B S2-SECEXCEPTION)."""
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ValueError(f"classify_only: now must be timezone-aware, got {now!r}")
    if isinstance(since, datetime) or not isinstance(since, date):
        raise ValueError(f"classify_only: since must be a date, got {since!r}")
    today = now.astimezone(ET).date()
    parsed, raw_rows = _parse_registry(companies)
    ctx = _Ctx(today=today, companies=parsed, company_rows=raw_rows, configs=member_configs, stakes=stakes)
    gate = _SecGate(sec, deadline=_monotonic() + RUN_DEADLINE_SECONDS)
    problems: List[str] = []

    async def known(_slug: str, _accessions: List[str]) -> Dict[str, Any]:
        return {}

    async def holdings_of(slug: str) -> Optional[Mapping[str, Any]]:
        return (holdings or {}).get(slug) if isinstance(holdings, Mapping) else None

    disc = _Discovery(ctx=ctx, sec=gate, start=since, end=today, known=known, holdings=holdings_of,
                      fail=lambda stage, msg: problems.append(f"{stage}: {msg}"))
    members, _missing = _in_scope(parsed, member_configs)
    out: List[Dict[str, Any]] = []
    synthetic = 0

    def keep(raw: _Raw) -> None:
        nonlocal synthetic
        d = _decide(raw, ctx)
        out.append(_decision_dict(d))
        if raw.exit and raw.investee_cik:
            ctx.note_exit(raw.slug, raw.investee_cik, raw.event_date or raw.filed_on)
        if d.action == "insert" and d.outcome in (OUTCOME_AUTO, OUTCOME_PROPOSAL) and d.row:
            synthetic += 1
            fid = f"00000000-0000-4000-8000-{synthetic:012d}"
            if d.outcome == OUTCOME_AUTO:
                ctx.put_stake({**d.row, "id": fid, "origin": ORIGIN_AUTO, "finding_id": fid,
                               "published": True, "withdrawn_at": None})
            else:
                ctx.put_finding({"id": fid, "company_slug": raw.slug, "status": STATUS_PROPOSED,
                                 "outcome": OUTCOME_PROPOSAL, "investee_cik": raw.investee_cik,
                                 "proposed_row": d.row, "created_at": f"9999-{synthetic:06d}"})

    async def member_stage(stage: str, cfg: MemberConfig, rows: AsyncIterator[_Raw]) -> None:
        """One member's S1 or S2: a failure is recorded and the next member goes on — what was
        classified stays classified."""
        try:
            async for raw in rows:
                keep(raw)
        except (_StopSEC, _StopEFTS, asyncio.CancelledError):
            raise
        except SECException as e:
            problems.append(f"{stage}: {cfg.slug}: an SEC read failed ({_err(e)}) — its other filings were not read")
        except Exception as e:  # noqa: BLE001 — a bug in one member's classification never loses the rest
            logger.exception("[tc-stakes-preview] %s %s: the classification raised", stage, cfg.slug)
            problems.append(f"{stage}: {cfg.slug}: the classification raised ({_err(e)})")

    try:
        for cfg in members:
            if cfg.s1_filers:
                await member_stage("s1", cfg, disc.s1(cfg))
        others_of = {c.slug: [a for s, o in member_configs.items() if s != c.slug for a in o.s2_aliases]
                     for c in members}
        for cfg in members:
            if cfg.s2_aliases:
                try:
                    await member_stage("s2", cfg, disc.s2(cfg, others_of[cfg.slug]))
                except _StopEFTS as e:
                    problems.append(f"s2: {e}")
                    break
    except _StopSEC as e:
        problems.append(f"sec: {e}")
    for p in problems:
        logger.warning("[tc-stakes-preview] %s", p)
    return ClassifyResult(out, problems)


# ── The run ──────────────────────────────────────────────────────────────────────────────


@dataclass
class _GateFail:
    kind: str           # stop | heads_up | proposal
    reason: str
    detail: str = ""


class _Run:
    """One ``run_stakes`` call: its clients, its summary and the state machine's steps."""

    def __init__(self, now: datetime, *, sec: Any, store: Any, notifier: Any,
                 configs: Optional[Mapping[str, MemberConfig]], configs_error: Optional[str], club_store: Any):
        self.now = now
        self.today = now.astimezone(ET).date()
        self.store = store
        self.notifier = notifier
        self.club = club_store
        self.configs: Dict[str, MemberConfig] = dict(configs or {})
        self.configs_error = configs_error
        self.sec = _SecGate(sec, deadline=_monotonic() + RUN_DEADLINE_SECONDS)
        self.summary: Dict[str, Any] = {
            "job": "stakes", "now": now.isoformat(), "ok": True, "items": 0, "failures": [], "stages": {},
        }
        self.ctx: Optional[_Ctx] = None
        self.loud_left = MAX_LOUD_MESSAGES_PER_RUN
        self.waiting = 0
        self.alerts: List[str] = []
        self.missing: List[str] = []
        #: (slug, accession) of filings whose processing failed in THIS run: their line waits for
        #: the run that finishes them (an exit reported before its sweep landed would never be
        #: swept again — final verify #7).
        self.unfinished: Set[Tuple[Any, Any]] = set()
        self._holdings: Dict[str, Any] = {}

    # ── summary plumbing ──

    def stage(self, name: str) -> Dict[str, Any]:
        return self.summary["stages"].setdefault(name, {"ok": True})

    def count(self, stage: str, key: str, n: int = 1) -> None:
        st = self.stage(stage)
        st[key] = st.get(key, 0) + n

    def fail(self, stage: str, message: str) -> None:
        text = _clip(f"{stage}: {message}", 400)
        self.stage(stage)["ok"] = False
        self.summary["ok"] = False
        if text not in self.summary["failures"]:
            self.summary["failures"].append(text)
        logger.warning("[tc-stakes-%s] %s", stage, message)

    def wrote(self, n: int = 1) -> None:
        self.summary["items"] += n

    def shown_changed(self) -> None:
        """A write changed what Home shows: invalidate now (a later crash must not leave it stale)."""
        _invalidate()

    def gate1_problem(self) -> Optional[str]:
        if not settings.TRILLION_CLUB_JOBS_ENABLED:
            return "TRILLION_CLUB_JOBS_ENABLED is off"
        if not settings.TRILLION_CLUB_STAKE_FINDER_ENABLED:
            return "TRILLION_CLUB_STAKE_FINDER_ENABLED is off"
        if not sec_edgar.user_agent_configured():
            return "SEC_EDGAR_USER_AGENT is not set (or not printable ASCII)"
        try:
            available = bool(self.notifier.available())
        except Exception as e:  # noqa: BLE001 — a notifier bug reads as unavailable, loudly
            logger.error("%s the notifier's available() raised (%s)", _TAG, _err(e), exc_info=True)
            available = False
        if not available:
            return "the review bot is not configured (MARKETING_TELEGRAM_*)"
        return None

    def take_loud(self) -> bool:
        if self.loud_left <= 0:
            self.waiting += 1
            return False
        self.loud_left -= 1
        return True

    def loud_free(self) -> bool:
        if self.loud_left <= 0:
            self.waiting += 1
            return False
        return True

    def present(self, f: Mapping[str, Any], **extra: Any) -> Dict[str, Any]:
        """The finding as the notifier reads it: the ledger row + the member's display name and
        the proposed row's ``_detail`` (+ ``extra``, e.g. an update's ``target_live``)."""
        out = dict(f)
        out["member_name"] = self.ctx.display(f.get("company_slug")) if self.ctx else f.get("company_slug")
        detail = _meta(f.get("proposed_row"), "_detail")
        if isinstance(detail, str) and detail:
            out["detail"] = detail
        out.update(extra)
        return out

    # ── the run ──

    async def go(self) -> None:
        problem = self.gate1_problem()
        try:
            rows = await self.club.read_companies()
            if not isinstance(rows, (list, tuple)):
                raise TypeError(f"read_companies answered a {type(rows).__name__}")
        except Exception as e:  # noqa: BLE001
            logger.error("%s the registry read failed (%s)", _TAG, _err(e), exc_info=True)
            problem = problem or f"the registry read failed ({_err(e)})"
            rows = []
        # A malformed row is skipped with an ERROR (a retry cannot fix it): never a failed run.
        companies, raw_rows = _parse_registry(rows)
        self.ctx = _Ctx(today=self.today, companies=companies, company_rows=raw_rows, configs=self.configs)
        if problem:
            await self._run_stage("reconcile", lambda: self.reconcile(db_only=True))
            self.fail("switches", f"{problem} — only the database repairs ran")
            return
        await self._run_stage("reconcile", lambda: self.reconcile(db_only=False))
        if self.configs_error:
            self.fail("filers", f"the filers file is unusable ({self.configs_error}) — no SEC stage ran")
        else:
            members, self.missing = _in_scope(companies, self.configs)
            if self.missing:
                # The digest line goes out on the 1st of each month (no ledger row can carry a
                # 30-day marker for a member with no filing); the log says it every run.
                (logger.warning if self.today.day == 1 else logger.info)(
                    "[tc-stakes-registry] published members not in %s — never searched: %s", FILERS_PATH.name,
                    ", ".join(self.missing))
            await self._refresh()
            disc = _Discovery(ctx=self.ctx, sec=self.sec, start=self.today - timedelta(
                days=int(settings.TRILLION_CLUB_STAKE_LOOKBACK_DAYS)), end=self.today,
                known=self._known, holdings=self._member_holdings, fail=self.fail)
            await self._run_stage("s1", lambda: self.s1(disc, members))
            await self._run_stage("s2", lambda: self.s2(disc, members))
            if self.today.weekday() == 0:
                await self._run_stage("recheck", lambda: self.recheck(disc))
        await self._run_stage("expiry", self.expiry)
        await self._run_stage("digest", self.digest)

    async def _run_stage(self, name: str, fn: Callable[[], Any]) -> None:
        self.stage(name)
        try:
            await fn()
        except _StopSEC as e:
            self.fail(name, str(e))
        except _StopEFTS as e:
            self.fail(name, str(e))
        except StakeStoreError as e:
            self.fail(name, f"the store failed ({_err(e)})")
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 — one stage's bug never stops the others
            logger.exception("[tc-stakes-%s] the stage crashed", name)
            self.fail(name, f"crashed ({_err(e)})")

    async def _refresh(self) -> None:
        stakes = await self.store.read_stakes()
        findings = await self.store.read_findings_by_status(_ACTIVE_READ)
        self.ctx.reset(stakes, findings)

    async def _known(self, slug: str, accessions: List[str]) -> Dict[str, Any]:
        return await self.store.read_findings_for_accessions(slug, accessions)

    async def _pair_exit(self, slug: Any, cik: Any) -> Optional[date]:
        """The newest exit filing on file for (slug, investee CIK), read FRESH from the ledger
        (any status); None when the pair has none or no investee CIK."""
        if not isinstance(slug, str) or not isinstance(cik, str) or not cik:
            return None
        return _newest_exit(await self.store.read_findings_for_pair(slug, cik))

    async def _load_exits(self, slug: str, cik: str) -> None:
        self.ctx.note_exit(slug, cik, await self._pair_exit(slug, cik))

    async def _member_holdings(self, slug: str) -> Optional[Dict[str, Any]]:
        """The union of the member's CIKs' latest complete 13F builds (cached per run). A read that
        fails raises — the caller records an error finding rather than guess "not on the 13F"."""
        if slug in self._holdings:
            return self._holdings[slug]
        co = self.ctx.companies.get(slug)
        if co is None or not co.use_13f:
            self._holdings[slug] = None
            return None
        cusips: Set[str] = set()
        symbols: Set[str] = set()
        found = False
        for cik in co.ciks:
            h = await self.store.read_latest_complete_holdings(cik)
            if h:
                found = True
                cusips |= set(h.get("cusips") or ())
                symbols |= set(h.get("symbols") or ())
        self._holdings[slug] = {"cusips": frozenset(cusips), "symbols": frozenset(symbols)} if found else None
        return self._holdings[slug]

    # ── persisting one decision ──

    async def consume(self, raw: _Raw, stage: str) -> None:
        """Gates 3-7 (``_decide``), then — for an auto row — 8-10, then the write order."""
        try:
            if (raw.error is None and not raw.exit and raw.outcome in (OUTCOME_AUTO, OUTCOME_PROPOSAL)
                    and raw.investee_cik):
                # The pair's exit filings, ANY status (a skipped or reported exit still binds it).
                await self._load_exits(raw.slug, raw.investee_cik)
            d = _decide(raw, self.ctx)
            if d.action == "insert" and d.outcome == OUTCOME_AUTO:
                reason = await self.auto_gate(exclude=None, filed_on=raw.filed_on)
                if reason:
                    self.count(stage, f"downgraded_{reason}")
                    if reason == "volume_cap":
                        self.alerts.append("volume_cap")
                    if reason == "breaker":
                        self.alerts.append("breaker")
                    d = replace(d, outcome=OUTCOME_PROPOSAL, reason=reason)
            self.count(stage, f"{d.action}_{d.outcome}")
            await self.persist(d)
        except (_StopSEC, _StopEFTS):
            raise
        except StakeStoreError as e:
            self.unfinished.add((raw.slug, raw.accession))
            self.fail(stage, f"{raw.slug} {raw.accession}: the store failed ({_err(e)}) — the next run resumes")
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 — one filing's bug never stops the run
            self.unfinished.add((raw.slug, raw.accession))
            logger.exception("[tc-stakes-%s] %s %s: persisting crashed", stage, raw.slug, raw.accession)
            self.fail(stage, f"{raw.slug} {raw.accession}: crashed ({_err(e)})")

    def _finding_payload(self, d: _Decision, status: str, *, stake_id: Optional[str] = None,
                         attempts: Optional[int] = None, last_error: Optional[str] = None) -> Dict[str, Any]:
        raw = d.raw
        if d.row:
            pr: Dict[str, Any] = dict(d.row)
            if d.detail:
                pr["_detail"] = _clip(d.detail)
        else:
            pr = {"_investee": _clip(raw.label or "", 80) or None, "_detail": _clip(d.detail) or None}
            if raw.t3:
                pr["_t3"] = raw.t3
            if d.old:
                pr["_old"] = _clip(d.old)
            if raw.exit:
                # The exit's date stays on the ledger: an older filing of the pair read later — an
                # error retry, a parked find, a late correction — is refused against it.
                pr["_event"] = (raw.event_date or raw.filed_on).isoformat()
            pr = {k: v for k, v in pr.items() if v is not None}
        row: Dict[str, Any] = {
            "company_slug": raw.slug, "source": raw.source, "accession": raw.accession, "filer_cik": raw.filer_cik,
            "investee_cik": raw.investee_cik, "form": raw.form[:40], "filed_on": raw.filed_on,
            "document": raw.document, "source_url": raw.source_url, "tier": d.tier, "outcome": d.outcome,
            "reason": d.reason, "proposed_row": pr or None, "verified_spans": _cap_spans(raw.spans),
            "fact_sha": _fact_sha(pr) if pr else None, "status": status,
        }
        if stake_id is not None:
            row["stake_id"] = stake_id
        if attempts is not None:
            row["attempts"] = attempts
        if last_error is not None:
            row["last_error"] = last_error
        return row

    async def _write(self, d: _Decision, status: str, *, stake_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """The write-ahead ledger row: an INSERT, or — for a retried ``error`` finding — a CAS of
        that row to the new classification. None when another writer holds the accession."""
        existing = d.raw.existing
        if existing is not None:
            payload = self._finding_payload(d, status, stake_id=stake_id,
                                            attempts=int(existing.get("attempts") or 0) + 1)
            changes = {k: v for k, v in payload.items() if k not in ("company_slug", "accession")}
            changes["last_error"] = None
            ok = await self.store.update_finding(existing["id"], expected_status=STATUS_ERROR, changes=changes,
                                                 now=self.now)
            if not ok:
                logger.info("%s %s %s: the error finding moved on — another writer has it", _TAG, d.raw.slug,
                            d.raw.accession)
                return None
            f = await self.store.read_finding(existing["id"])
        else:
            f, created = await self.store.insert_finding(self._finding_payload(d, status, stake_id=stake_id),
                                                         now=self.now)
            if not created:
                logger.info("%s %s %s: the accession is already in the ledger (finding %s)", _TAG, d.raw.slug,
                            d.raw.accession, f.get("id"))
                return None
        if f is None:
            return None
        self.wrote()
        self.ctx.put_finding(f)
        return f

    async def persist(self, d: _Decision) -> None:
        if d.action == "error":
            await self._record_error(d)
            return
        if d.action == "append":
            await self._append(d)
            return
        if d.action == "exit":
            f = await self._write(d, STATUS_NEW, stake_id=str(d.target["id"]) if d.target else None)
            if f is not None:
                await self.go_exit(f)
            return
        if d.action == "update":
            f = await self._write(d, STATUS_NEW, stake_id=str(d.target["id"]))
            if f is not None:
                await self.go_proposal_new(f)
            return
        status = STATUS_SKIPPED if d.outcome == OUTCOME_SKIPPED else STATUS_NEW
        f = await self._write(d, status)
        if f is None:
            return
        if d.outcome == OUTCOME_AUTO:
            await self.go_auto_new(f)
        elif d.outcome == OUTCOME_PROPOSAL:
            await self.go_proposal_new(f)

    async def _record_error(self, d: _Decision) -> None:
        """A transient SEC failure on one filing. It is read again at most once per ET day
        (``_retry_due``: the scheduler retries a failed run hourly, which would spend every attempt
        in one evening), and the last failed attempt turns it into a ``fetch_failed`` heads-up with
        its EDGAR link — never a silent skip (review B lifecycle-F3)."""
        raw = d.raw
        existing = raw.existing
        attempts = int(existing.get("attempts") or 0) + 1 if existing else 1
        if existing is not None and attempts >= MAX_ATTEMPTS:
            logger.warning("[tc-stakes-sec] %s %s (finding %s): %s — the last of %d attempts failed; a heads-up with "
                           "the EDGAR link", raw.slug, raw.accession, existing.get("id"), raw.error, MAX_ATTEMPTS)
            await self._error_exhausted(existing, attempts=attempts, error=raw.error)
            return
        self.fail("sec", f"{raw.slug} {raw.accession}: {raw.error} (attempt {attempts} of {MAX_ATTEMPTS}; read "
                         "again on a later ET day)")
        if existing is not None:
            await self.store.update_finding(existing["id"], expected_status=STATUS_ERROR,
                                            changes={"attempts": attempts, "last_error": raw.error}, now=self.now)
            return
        row = self._finding_payload(d, STATUS_ERROR, attempts=1, last_error=raw.error)
        f, created = await self.store.insert_finding(row, now=self.now)
        if created:
            self.wrote()
            self.ctx.put_finding(f)

    async def _error_exhausted(self, f: Dict[str, Any], *, attempts: int, error: Optional[str]) -> None:
        """An ``error`` finding out of attempts → ONE heads-up line (status new, reason
        fetch_failed) whose link is the filing on EDGAR: the owner reads it there."""
        pr = f.get("proposed_row") if isinstance(f.get("proposed_row"), Mapping) else {}
        last = error or f.get("last_error") or "an SEC error"
        meta = {k: v for k, v in {
            "_investee": _meta(pr, "_investee"),
            "_detail": _clip((f"could not be read on {attempts} ET days" if attempts >= MAX_ATTEMPTS else
                              f"could not be read ({attempts} attempt(s)) before it left the "
                              f"{int(settings.TRILLION_CLUB_STAKE_LOOKBACK_DAYS)}-day window")
                             + f" — read it on EDGAR (last error: {last})"),
        }.items() if v}
        if await self._cas(f, STATUS_ERROR, {"status": STATUS_NEW, "outcome": OUTCOME_HEADS_UP, "reason": "fetch_failed",
                                             "proposed_row": meta, "fact_sha": _fact_sha(meta), "attempts": attempts,
                                             "last_error": last}):
            self.count("reconcile", "errors_to_heads_up")

    # ── auto ──

    async def go_auto_new(self, f: Dict[str, Any]) -> None:
        """``new`` auto → stake INSERT → ``staged`` → the rest of the write order. The run's view
        of the stakes was read at its start; ``insert_stake`` itself re-reads by finding id after
        any error, so a row an earlier attempt wrote is returned, never inserted twice."""
        fid = str(f["id"])
        stake = self.ctx.stake_by_finding(fid)
        if stake is None:
            pr = f.get("proposed_row")
            if not _is_stake_row(pr):
                logger.error("%s %s %s: an auto finding with no stake row to write — a heads-up", _TAG,
                             f.get("company_slug"), f.get("accession"))
                await self._to_heads_up(f, STATUS_NEW, "validator", "the finding carries no row")
                return
            m = _match(self.ctx, f["company_slug"], cik=f.get("investee_cik"), cusip=pr.get("investee_cusip"),
                       symbol=pr.get("investee_us_symbol"), name=pr.get("investee_name"), exclude_findings=[fid])
            if m is not None:
                await self._to_heads_up(f, STATUS_NEW, "existing_row", f"a row is on file: {_row_summary(m.row)}")
                return
            if not self.loud_free():
                return
            row = {**_stake_columns(pr), "origin": ORIGIN_AUTO, "finding_id": fid, "investee_cik": f.get("investee_cik")}
            try:
                stake = await self.store.insert_stake(row, now=self.now)
            except StakeConflict:
                await self._to_heads_up(f, STATUS_NEW, "existing_row",
                                        f"another row already has the name {pr.get('investee_name')!r} and kind")
                return
            self.wrote()
            self.ctx.put_stake(stake)
        if not await self._cas(f, STATUS_NEW, {"status": STATUS_STAGED, "stake_id": str(stake["id"])}):
            return
        await self.go_staged(f, stake)

    async def go_staged(self, f: Dict[str, Any], stake: Dict[str, Any]) -> None:
        if stake.get("withdrawn_at") is not None:
            await self._catch_up_withdrawn(f, stake)
            return
        if stake.get("origin") == ORIGIN_PROPOSAL:
            await self._send_held_proposal(f, stake)
            return
        failed = await self.publish_gates(f, stake)
        if failed is not None:
            await self._publish_gate_failed(f, stake, failed)
            return
        if not self.take_loud():
            return
        mid = await self.notifier.send_auto_note(self.present(f), _stake_columns(stake))
        if mid is None:
            self.fail("notes", f"{f.get('company_slug')} {f.get('accession')}: the auto note was not delivered — "
                               "nothing published; the next run retries")
            return
        if not await self._cas(f, STATUS_STAGED, {"status": STATUS_NOTED, "telegram_message_id": mid,
                                                   "notified_at": self.now}):
            return
        await self.go_noted(f, stake)

    async def go_noted(self, f: Dict[str, Any], stake: Dict[str, Any]) -> None:
        if stake.get("withdrawn_at") is not None:
            await self._catch_up_withdrawn(f, stake)
            return
        if stake.get("published") is True:
            # The flip landed but its ledger write was lost: caught up, counted live from now
            # (gate 8 counts by published_at — late is the safe side).
            await self._cas(f, STATUS_NOTED, {"status": STATUS_PUBLISHED, "published_at": self.now})
            return
        if stake.get("origin") == ORIGIN_PROPOSAL:
            await self._send_held_proposal(f, stake)
            return
        failed = await self.publish_gates(f, stake)
        if failed is not None:
            await self._publish_gate_failed(f, stake, failed)
            return
        if not await self.store.publish_stake(str(stake["id"]), str(f["id"]), now=self.now):
            current = await self.store.read_stake(str(stake["id"]))
            logger.info("%s %s %s: the flip lost (the stake is now %s) — never re-published", _TAG,
                        f.get("company_slug"), f.get("accession"),
                        "withdrawn" if current and current.get("withdrawn_at") else "changed")
            if current is not None:
                self.ctx.put_stake(current)
                if current.get("withdrawn_at") is not None:
                    await self._catch_up_withdrawn(f, current)
            return
        self.wrote()
        self.ctx.put_stake({**stake, "published": True})
        self.shown_changed()
        await self._cas(f, STATUS_NOTED, {"status": STATUS_PUBLISHED, "published_at": self.now})
        logger.info("%s %s %s: published automatically (stake %s)", _TAG, f.get("company_slug"),
                    f.get("accession"), stake.get("id"))

    async def _send_held_proposal(self, f: Dict[str, Any], stake: Dict[str, Any]) -> None:
        """A downgraded auto row (origin proposal): its proposal note is due — unless the member's
        exit filing is on file and dated on or after it (the same refusal as go_proposal_new; an
        exit whose void write failed must never let the pre-exit row be proposed: verifier R5)."""
        exit_on = await self._pair_exit(f.get("company_slug"), stake.get("investee_cik") or f.get("investee_cik"))
        if _exit_binds(stake, exit_on):
            await self._to_heads_up(f, f.get("status"), "exit", f"the member's later filing reports 5% or less (as of "
                                                                f"{_short_date(exit_on)}); {_row_summary(stake)} is not proposed")
            return
        if not self.take_loud():
            return
        status = f.get("status")
        mid = await self.notifier.send_proposal(self.present(f), _stake_columns(stake), trial=False,
                                                evidence=list(f.get("verified_spans") or []))
        if mid is None:
            self.fail("notes", f"{f.get('company_slug')} {f.get('accession')}: the proposal was not delivered")
            return
        await self._cas(f, status, {"status": STATUS_PROPOSED, "outcome": OUTCOME_PROPOSAL,
                                    "telegram_message_id": mid, "notified_at": self.now})

    async def publish_gates(self, f: Mapping[str, Any], stake: Mapping[str, Any]) -> Optional[_GateFail]:
        """Gates 1, 3, 5 and 8-10 with FRESH reads, right before a publish write."""
        problem = self.gate1_problem()
        if problem:
            return _GateFail("stop", "switches", problem)
        fid, sid, slug = str(f["id"]), str(stake["id"]), str(stake.get("company_slug"))
        exit_on = await self._pair_exit(slug, stake.get("investee_cik"))
        if _exit_binds(stake, exit_on):
            # The member's own exit filing is on file, dated on or after this row: it never goes
            # live (the row stays unpublished — an exit is no tombstone; review B GI-2 / F1).
            return _GateFail("heads_up", "exit", f"a later filing of the member reports 5% or less (as of "
                                                 f"{_short_date(exit_on)}) — {_row_summary(stake)} never went live")
        stakes = await self.store.read_stakes()
        findings = await self.store.read_findings_by_status(
            (STATUS_NEW, STATUS_PROPOSED, STATUS_PUBLISHING, *sorted(TOMBSTONE_STATUSES)))
        fresh = _Ctx(today=self.today, companies=self.ctx.companies, company_rows=self.ctx.company_rows,
                     configs=self.configs, stakes=stakes, findings=findings)
        # A finding pointing at THIS stake (an UPDATE proposal of this very row) is the same pair,
        # not a second row (review B WO-4).
        same_row = [str(x.get("id")) for x in findings if str(x.get("stake_id") or "") == sid]
        m = _match(fresh, slug, cik=stake.get("investee_cik"), cusip=stake.get("investee_cusip"),
                   symbol=stake.get("investee_us_symbol"), name=stake.get("investee_name"),
                   exclude_findings=[fid, *same_row], exclude_stakes=[sid])
        if m is not None:
            reason = "possible_match" if m.by == "name" else (
                "tombstone" if m.row.get("_src") == "tombstone" or m.row.get("withdrawn_at") else "existing_row")
            return _GateFail("heads_up", reason, f"on file now: {_row_summary(m.row)}")
        problem5 = _validator_problem({**_stake_columns(stake), "investee_cik": stake.get("investee_cik")},
                                      origin=ORIGIN_AUTO, company_rows=self.ctx.company_rows, today=self.today)
        if problem5:
            return _GateFail("heads_up", "validator", problem5)
        reason = await self.auto_gate(exclude=fid, filed_on=_as_date(f.get("filed_on")))
        if reason:
            return _GateFail("proposal", reason, REASONS[reason])
        return None

    async def auto_gate(self, *, exclude: Optional[str], filed_on: Optional[date] = None) -> Optional[str]:
        """Gates 8-10 for one auto row, from the ledger: volume, breaker, tier, AUTO_SINCE.

        Gate 8 counts auto PUBLISHES on the day they go live (review B WO-1 / GI-1): every other
        auto finding still on its way to a publish (new / staged / noted, whatever its
        created_at — a publish in waiting, today at the earliest), plus every auto finding whose
        ``published_at`` falls in today's ET day (the day cap) or the last 7 days (the week cap).
        ``exclude`` is the finding being gated."""
        tiers = parse_auto_tiers(settings.TRILLION_CLUB_STAKE_AUTO_TIERS)
        since = parse_auto_since(settings.TRILLION_CLUB_STAKE_AUTO_SINCE)
        week_start = self.now - timedelta(days=7)
        rows = await self.store.read_auto_volume(week_start)
        day_start = _et_start(self.today)
        day = week = 0
        for r in rows:
            if r.get("id") == exclude or r.get("outcome") != OUTCOME_AUTO:
                continue
            if r.get("status") in AUTO_IN_FLIGHT_STATUSES:
                day += 1
                week += 1
                continue
            published = _as_ts(r.get("published_at"))
            if published is None or published < week_start:
                continue
            week += 1
            if published >= day_start:
                day += 1
        if day >= AUTO_MAX_PER_DAY or week >= AUTO_MAX_PER_WEEK:
            logger.warning("[tc-stakes-gate] volume cap: %d today, %d this week — a proposal", day, week)
            return "volume_cap"
        if since is not None:
            withdrawals = await self.store.read_decisions_since(DECISION_WITHDRAWN, _et_start(since))
            if len(withdrawals) >= BREAKER_WITHDRAWALS:
                logger.warning("[tc-stakes-gate] breaker: %d owner withdrawals since %s — auto-publish paused", len(withdrawals), since)
                return "breaker"
        return auto_tier_gate(filed_on, tiers=tiers, since=since)

    async def _publish_gate_failed(self, f: Dict[str, Any], stake: Dict[str, Any], failed: _GateFail) -> None:
        status = f.get("status")
        fid = str(f["id"])
        note = f.get("telegram_message_id") if status == STATUS_NOTED else None
        if failed.kind == "stop":
            self.fail("switches", f"{failed.detail} — {f.get('company_slug')} {f.get('accession')} waits")
            return
        if failed.kind == "heads_up":
            # The LEDGER first — the finding becomes the heads-up (its stake_id kept) — and only then
            # the hide: a failure between the two leaves a heads-up whose duplicate the next
            # reconcile hides, never a live-looking finding with a withdrawn stake, which would be
            # caught up as an owner's Withdraw the owner never made (review B WO-2).
            if not await self._to_heads_up(f, status, failed.reason, failed.detail):
                return
            if failed.reason in _HIDE_REASONS:
                # A second row for the pair: hidden for good (the pair's other row stays).
                await self._hide_duplicate(f, stake)
            if note:
                verdict = (VERDICT_HELD_EXIT if failed.reason == "exit"
                           else f"Held back — not published ({REASONS.get(failed.reason)})")
                await self._mark(note, fid, verdict)
            return
        if not await self.store.adopt_as_proposal(str(stake["id"]), fid, now=self.now):
            logger.info("%s %s %s: the downgrade lost — the stake changed", _TAG, f.get("company_slug"),
                        f.get("accession"))
            return
        self.ctx.put_stake({**stake, "origin": ORIGIN_PROPOSAL, "owner_kept_at": None})
        if not await self._cas(f, status, {"outcome": OUTCOME_PROPOSAL, "reason": failed.reason}):
            return
        if failed.reason in ("volume_cap", "breaker"):
            self.alerts.append(failed.reason)
        if note:
            await self._mark(note, fid, f"Held back — sent as a proposal ({REASONS.get(failed.reason)})")
        await self._send_held_proposal(f, {**stake, "origin": ORIGIN_PROPOSAL})

    async def _open_update_of(self, stake_id: str) -> Optional[Dict[str, Any]]:
        """An open UPDATE proposal whose target is this stake (it must never be hidden: the update
        would then refuse to publish, and nothing of the pair could go live — review B WO-4)."""
        for x in await self.store.read_findings_for_stake(stake_id):
            if x.get("tier") == TIER_UPDATE and x.get("status") in (STATUS_NEW, STATUS_PROPOSED, STATUS_PUBLISHING):
                return x
        return None

    async def _hide_duplicate(self, f: Mapping[str, Any], stake: Mapping[str, Any]) -> None:
        """Hide the heads-up finding's OWN unpublished stake for good (``withdrawn_at``) — no owner
        decision. A failure is logged and left to the next reconcile (the digest keeps the line
        until the stake is hidden)."""
        fid, sid = str(f["id"]), str(stake["id"])
        try:
            upd = await self._open_update_of(sid)
            if upd is not None:
                logger.warning("%s %s %s: an open update proposal (finding %s) targets stake %s — not hidden",
                               _TAG, f.get("company_slug"), f.get("accession"), upd.get("id"), sid)
                return
            n = await self.store.withdraw_stakes_for_finding(fid, now=self.now)
        except StakeStoreError as e:
            self.fail("reconcile", f"{f.get('company_slug')} {f.get('accession')}: the duplicate stake {sid} could not "
                                   f"be hidden ({_err(e)}) — the next run hides it")
            return
        if n:
            self.ctx.put_stake({**stake, "published": False, "withdrawn_at": self.now.isoformat()})
            self.count("reconcile", "duplicates_hidden", n)

    def _hide_pending(self, f: Mapping[str, Any]) -> bool:
        """A heads-up whose own duplicate stake is not hidden yet (and no open update protects it):
        its digest line waits for the reconcile that hides it."""
        if not (f.get("status") == STATUS_NEW and f.get("outcome") == OUTCOME_HEADS_UP
                and f.get("reason") in _HIDE_REASONS and f.get("stake_id")):
            return False
        s = self.ctx.stakes.get(str(f["stake_id"])) if self.ctx else None
        if s is None or s.get("finding_id") != f.get("id") or s.get("withdrawn_at") is not None:
            return False
        return not any(x.get("tier") == TIER_UPDATE and str(x.get("stake_id") or "") == str(s.get("id"))
                       and x.get("status") in (STATUS_NEW, STATUS_PROPOSED, STATUS_PUBLISHING)
                       for x in self.ctx.findings.values())

    async def _to_heads_up(self, f: Dict[str, Any], status: Any, reason: str, detail: str) -> bool:
        pr = f.get("proposed_row") if isinstance(f.get("proposed_row"), Mapping) else {}
        meta = {"_investee": _clip(pr.get("investee_name") or _meta(pr, "_investee") or "", 80) or None,
                "_detail": _clip(detail) or None}
        meta = {k: v for k, v in meta.items() if v is not None}
        return await self._cas(f, status, {"status": STATUS_NEW, "outcome": OUTCOME_HEADS_UP, "reason": reason,
                                           "proposed_row": meta, "fact_sha": _fact_sha(meta)})

    async def _restore_if_decided(self, finding_id: str, message_id: int) -> None:
        """A Publish / Dismiss tap can land while the proposal edit above waited on the pacer; that edit
        then put Publish / Dismiss back on a decided proposal. Re-read it, and put back its CURRENT
        state (a live row: Withdraw) — review A TG-1."""
        current = await self.store.read_finding(finding_id)
        if current is None or current.get("status") == STATUS_PROPOSED:
            return
        view = await _state_view(self.store, current)
        if view is None:
            return
        if not await self.notifier.mark_message(message_id=message_id, finding_id=finding_id, verdict=view[0],
                                                buttons=view[1]):
            logger.warning("%s finding %s: its message %s could not be restored after a decision (%r)", _TAG,
                           finding_id, message_id, view[0])

    async def _mark(self, message_id: Any, finding_id: str, verdict: str, buttons: Sequence[str] = ()) -> None:
        if not isinstance(message_id, int) or isinstance(message_id, bool):
            return
        ok = await self.notifier.mark_message(message_id=message_id, finding_id=finding_id, verdict=verdict,
                                              buttons=tuple(buttons))
        if not ok:
            logger.warning("%s message %s of finding %s could not be marked (%r)", _TAG, message_id, finding_id,
                           verdict)

    async def _cas(self, f: Dict[str, Any], expected: Any, changes: Mapping[str, Any]) -> bool:
        """A ledger compare-and-swap on the finding's observed status; the local copy follows."""
        fid = str(f["id"])
        ok = await self.store.update_finding(fid, expected_status=expected, changes=changes, now=self.now)
        if ok:
            local = {k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in changes.items()}
            f.update(local)
            self.ctx.changed(fid, local)
        else:
            logger.info("%s %s %s: finding %s moved on from %s (lost race)", _TAG, f.get("company_slug"),
                        f.get("accession"), fid, expected)
        return ok

    async def _catch_up_withdrawn(self, f: Dict[str, Any], stake: Mapping[str, Any]) -> None:
        """The stake was withdrawn while the finding still shows it live (a tap whose ledger write
        was lost, or the Studio valve): the finding follows. The owner's decision is recorded ONCE
        per stake — never when another finding of it already carries it (the breaker counts them),
        and never for a finding the finder itself downgraded to a heads-up: the finder hides a
        duplicate only AFTER that downgrade is in the ledger (review B WO-2), so such a withdrawal
        is the finder's, not the owner's."""
        linked = await self.store.read_findings_for_stake(str(stake["id"]))
        decided = any(x.get("owner_decision") == DECISION_WITHDRAWN for x in linked)
        changes: Dict[str, Any] = {"status": STATUS_WITHDRAWN}
        if (not decided and stake.get("finding_id") == f.get("id")
                and f.get("outcome") in (OUTCOME_AUTO, OUTCOME_PROPOSAL)):
            changes.update(owner_decision=DECISION_WITHDRAWN, decided_at=stake.get("withdrawn_at"))
        await self._cas(f, f.get("status"), changes)

    # ── proposals ──

    async def go_proposal_new(self, f: Dict[str, Any]) -> None:
        """``new`` proposal → (re-validated for today) → the LOUD proposal → ``proposed``."""
        pr = f.get("proposed_row")
        if not _is_stake_row(pr):
            await self._to_heads_up(f, STATUS_NEW, "validator", "the proposal carries no row")
            return
        failed = _row_gates({**pr}, origin=ORIGIN_PROPOSAL, ctx=self.ctx, filed_on=_as_date(f.get("filed_on")) or self.today,
                            event_date=None)
        if failed:
            await self._to_heads_up(f, STATUS_NEW, failed[0], failed[1])
            return
        exit_on = await self._pair_exit(f.get("company_slug"), f.get("investee_cik") or pr.get("investee_cik"))
        if _exit_binds(pr, exit_on):
            await self._to_heads_up(f, STATUS_NEW, "exit", f"the member's later filing reports 5% or less (as of "
                                                           f"{_short_date(exit_on)}); {_row_summary(pr)} is not proposed")
            return
        extra: Dict[str, Any] = {}
        if f.get("tier") == TIER_UPDATE:
            target = self.ctx.stakes.get(str(f.get("stake_id"))) if f.get("stake_id") else None
            if target is None or target.get("withdrawn_at") is not None:
                await self._to_heads_up(f, STATUS_NEW, "existing_row", "the row this updates is gone or withdrawn")
                return
            # The text must not say "replaces the live row" about a row that is not live (TAP-2).
            extra["target_live"] = target.get("published") is True
        if not self.take_loud():
            return
        mid = await self.notifier.send_proposal(self.present(f, **extra), _stake_columns(pr),
                                                trial=_is_trial(f.get("tier"), pr),
                                                evidence=list(f.get("verified_spans") or []))
        if mid is None:
            self.fail("notes", f"{f.get('company_slug')} {f.get('accession')}: the proposal was not delivered")
            return
        await self._cas(f, STATUS_NEW, {"status": STATUS_PROPOSED, "telegram_message_id": mid, "notified_at": self.now})

    async def _append(self, d: _Decision) -> None:
        """An open proposal for the pair absorbs this filing's EVIDENCE (its verified spans) and
        its message is EDITED, never resent. The proposal's row and fact_sha never change here —
        so no message can publish facts the owner was not shown. A filing that adds no new
        evidence (a 13D/13G has no spans), or whose edit fails, stays a heads-up digest line
        naming its own facts."""
        f = await self._write(d, STATUS_NEW)
        if f is None:
            return
        target = await self.store.read_finding(str(d.target["id"]))
        if target is None or target.get("status") not in (STATUS_NEW, STATUS_PROPOSED):
            return   # decided meanwhile: this filing stays a heads-up line
        merged = _cap_spans(list(target.get("verified_spans") or []) + list(d.raw.spans))
        if merged == list(target.get("verified_spans") or []):
            return   # nothing new to show in the proposal: the digest line says what this filing says
        if not await self._cas(target, target.get("status"), {"verified_spans": merged}):
            return
        if target.get("status") == STATUS_PROPOSED and isinstance(target.get("telegram_message_id"), int):
            pr = target.get("proposed_row") if _is_stake_row(target.get("proposed_row")) else {}
            extra: Dict[str, Any] = {}
            if target.get("tier") == TIER_UPDATE and target.get("stake_id"):
                row_on_file = self.ctx.stakes.get(str(target["stake_id"]))
                extra["target_live"] = bool(row_on_file and row_on_file.get("published") is True)
            edited = await self.notifier.edit_proposal(
                self.present(target, **extra), _stake_columns(pr), message_id=target["telegram_message_id"],
                trial=_is_trial(target.get("tier"), pr), evidence=merged)
            if not edited:
                logger.warning("%s %s %s: the open proposal %s could not be edited — a digest line instead", _TAG,
                               d.raw.slug, d.raw.accession, target.get("id"))
                return
            await self._restore_if_decided(str(target["id"]), target["telegram_message_id"])
        await self._cas(f, STATUS_NEW, {"status": STATUS_REPORTED, "notified_at": self.now})

    # ── exits ──

    async def go_exit(self, f: Dict[str, Any]) -> None:
        """An exit 13D/G (≤ 5%) of the member's own. Whatever order the filings came in, the pair
        stops: a LIVE own auto row is unpublished at once (LOUD note; both findings close), a
        live proposal-origin row stays live and its note offers Withdraw — and, in every case,
        the pair's auto finds still on their way to a publish are held back and its open
        proposals dated on or before the exit are void (``_exit_sweep``). An exit with no live
        row (``stake_id`` NULL) is a digest line once its sweep ran. Every step is a fenced,
        repeatable write: reconcile re-runs a ``new`` exit until it is done."""
        exit_on = _exit_event(f) or _as_date(f.get("filed_on")) or self.today
        stake = None
        if f.get("stake_id"):
            stake = self.ctx.stakes.get(str(f["stake_id"])) or await self.store.read_stake(str(f["stake_id"]))
        if stake is None:
            await self._exit_sweep(f, exit_on, keep=())
            if f.get("stake_id"):
                await self._cas(f, STATUS_NEW, {"status": STATUS_CLOSED})
            return   # no stake_id: an exit line — the digest reports it
        member, investee = self.ctx.display(f.get("company_slug")), stake.get("investee_name")
        pr = f.get("proposed_row") if isinstance(f.get("proposed_row"), Mapping) else {}
        said = _meta(pr, "_detail") or "reports 5% or less"
        filed = _short_date(_as_date(f.get("filed_on")))
        auto = stake.get("origin") == ORIGIN_AUTO
        if auto and stake.get("published") is True:
            if await self.store.unpublish_auto(str(stake["id"]), now=self.now):
                self.wrote()
                self.ctx.put_stake({**stake, "published": False})
                self.shown_changed()
        await self._exit_sweep(f, exit_on, keep=(str(stake.get("finding_id") or ""),))
        if auto:
            text = (f"{member}'s {f.get('form')} filed {filed} {said} of {investee}. The row was taken down "
                    "automatically; it stays on file, dated.")
            offer = False
        else:
            text = (f"{member}'s {f.get('form')} filed {filed} {said} of {investee}. The row was published by your "
                    "tap, so it stays live until you withdraw it.")
            offer = stake.get("withdrawn_at") is None
        if not self.take_loud():
            return
        mid = await self.notifier.send_lifecycle_note(self.present(f), _stake_columns(stake), text=text,
                                                      offer_withdraw=offer)
        if mid is None:
            self.fail("notes", f"{f.get('company_slug')} {f.get('accession')}: the exit note was not delivered")
            return
        if not await self._cas(f, STATUS_NEW, {"status": STATUS_CLOSED if auto else STATUS_REPORTED,
                                               "telegram_message_id": mid, "notified_at": self.now}):
            return
        if auto:
            own = self.ctx.findings.get(str(stake.get("finding_id"))) if stake.get("finding_id") else None
            if own is None and stake.get("finding_id"):
                own = await self.store.read_finding(str(stake["finding_id"]))
            if own is not None and own.get("status") in _LIVE_OWN_STATUSES:
                await self._cas(own, own.get("status"), {"status": STATUS_CLOSED})

    async def _exit_sweep(self, f: Mapping[str, Any], exit_on: date, *, keep: Sequence[str]) -> None:
        """Stop the pair for good on an exit (review B TAP-1, GI-2, lifecycle-F1/F2), from a FRESH
        read of the pair (any status), each step a compare-and-swap that a repeat finds done:

        * every AUTO finding still on its way to a publish (new / staged / noted) and dated on or
          before the exit → ``closed``: its stake, if written, stays unpublished (an exit is no
          tombstone); a delivered auto note is marked "Held back — …" with no buttons;
        * every open PROPOSAL (plain or update; new / staged / noted / proposed) dated on or
          before the exit → ``superseded`` (reason exit): its message is marked "Void — …" with no
          buttons, so it can never be Published after the exit. A Publish already in flight is
          left to the tap, which re-checks the exit itself.

        ``keep`` names findings the caller handles itself (the live row's own finding)."""
        slug, cik = f.get("company_slug"), f.get("investee_cik")
        if not isinstance(slug, str) or not isinstance(cik, str) or not cik:
            return
        skip = {str(f.get("id")), *(k for k in keep if k)}
        for x in await self.store.read_findings_for_pair(slug, cik):
            xid = str(x.get("id"))
            pr = x.get("proposed_row")
            if xid in skip or not _is_stake_row(pr):
                continue
            if not _exit_binds(pr, exit_on):
                continue   # newer than the exit (a re-entry), or a fact the exit does not contradict
            status, outcome, mid = x.get("status"), x.get("outcome"), x.get("telegram_message_id")
            if outcome == OUTCOME_AUTO and status in AUTO_IN_FLIGHT_STATUSES:
                if await self._cas(x, status, {"status": STATUS_CLOSED}):
                    self.count("exit", "held_back")
                    logger.info("[tc-stakes-exit] %s %s: held back before it went live — finding %s's exit %s",
                                slug, x.get("accession"), f.get("id"), f.get("accession"))
                    if status == STATUS_NOTED:
                        await self._mark(mid, xid, VERDICT_HELD_EXIT)
            elif outcome == OUTCOME_PROPOSAL and status in _VOIDABLE_STATUSES:
                if await self._cas(x, status, {"status": STATUS_SUPERSEDED, "reason": "exit", "notified_at": self.now}):
                    self.count("exit", "voided")
                    logger.info("[tc-stakes-exit] %s %s: the open proposal (finding %s) is void — the exit %s",
                                slug, x.get("accession"), xid, f.get("accession"))
                    await self._mark(mid, xid, VERDICT_VOID_EXIT)
            elif outcome == OUTCOME_PROPOSAL and status == STATUS_PUBLISHING:
                logger.warning("[tc-stakes-exit] %s %s: a Publish of finding %s is in flight as its exit %s lands — "
                               "the tap re-checks the exit", slug, x.get("accession"), xid, f.get("accession"))

    # ── reconcile ──

    async def reconcile(self, *, db_only: bool) -> None:
        stakes = await self.store.read_stakes()
        findings = await self.store.read_findings_by_status(_ACTIVE_READ)
        self.ctx.reset(stakes, findings)
        for s in list(self.ctx.stakes.values()):
            if s.get("origin") in MACHINE_ORIGINS and s.get("withdrawn_at") is not None and s.get("published") is True:
                await self._repair_withdrawn_live(s)
        for f in sorted(list(self.ctx.findings.values()), key=lambda r: (str(r.get("created_at")), str(r.get("id")))):
            try:
                await self._reconcile_one(f, db_only=db_only)
            except StakeStoreError as e:
                self.unfinished.add((f.get("company_slug"), f.get("accession")))
                self.fail("reconcile", f"{f.get('company_slug')} {f.get('accession')}: {_err(e)}")
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                self.unfinished.add((f.get("company_slug"), f.get("accession")))
                logger.exception("[tc-stakes-reconcile] finding %s crashed", f.get("id"))
                self.fail("reconcile", f"{f.get('company_slug')} {f.get('accession')}: crashed ({_err(e)})")

    async def _repair_withdrawn_live(self, s: Mapping[str, Any]) -> None:
        """(a) — 205 forbids it; if it is ever seen, the row is taken down."""
        logger.error("[tc-stakes-reconcile] stake %s (%s) is withdrawn AND published — taking it down", s.get("id"),
                     s.get("company_slug"))
        if s.get("origin") == ORIGIN_AUTO and await self.store.unpublish_auto(str(s["id"]), now=self.now):
            self.wrote()
            self.ctx.put_stake({**s, "published": False})
            self.shown_changed()
            return
        self.fail("reconcile", f"stake {s.get('id')} is withdrawn and published and could not be taken down — "
                               "unpublish it in Studio")

    def _stake_of(self, f: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
        if f.get("stake_id"):
            return self.ctx.stakes.get(str(f["stake_id"]))
        return self.ctx.stake_by_finding(str(f["id"]))

    async def _reconcile_one(self, f: Dict[str, Any], *, db_only: bool) -> None:
        status, fid = f.get("status"), str(f.get("id"))
        if status == STATUS_ERROR:
            filed = _as_date(f.get("filed_on"))
            window_start = self.today - timedelta(days=int(settings.TRILLION_CLUB_STAKE_LOOKBACK_DAYS))
            if int(f.get("attempts") or 0) >= MAX_ATTEMPTS or (filed is not None and filed < window_start):
                # Out of attempts — or out of the lookback window, so S1/S2 can never read it again
                # (a filing near the window's start): one heads-up line with the EDGAR link, never a
                # silent `error` row forever.
                await self._error_exhausted(f, attempts=int(f.get("attempts") or 0), error=None)
            return
        if status == STATUS_NEW:
            if f.get("outcome") == OUTCOME_HEADS_UP and f.get("stake_id") and f.get("reason") in _HIDE_REASONS:
                # A publish-time identity refusal whose duplicate stake was not hidden yet (a failure
                # after its ledger write): a database repair, so it runs under db_only too (WO-2).
                await self._hide_held_duplicate(f)
                return
            if db_only:
                if f.get("outcome") == OUTCOME_AUTO:
                    stake = self.ctx.stake_by_finding(fid)
                    if stake is not None:
                        await self._cas(f, STATUS_NEW, {"status": STATUS_STAGED, "stake_id": str(stake["id"])})
                return
            if f.get("outcome") == OUTCOME_AUTO:
                await self.go_auto_new(f)
            elif f.get("outcome") == OUTCOME_PROPOSAL:
                await self.go_proposal_new(f)
            elif f.get("reason") == "exit" and _exit_event(f) is not None:
                # An exit FILING (never a finding refused because of one): its sweep is repeated
                # until done, and a live row's note goes out.
                await self.go_exit(f)
            elif f.get("reason") == "recheck_negative" and f.get("stake_id"):
                await self._recheck_note(f)
            return
        if status in (STATUS_DISMISSED, STATUS_EXPIRED):
            # The Dismiss / expiry withdraws the stake the finding OWNS (an adopted auto row); a
            # crash between its two writes left it un-withdrawn. Never an update's target.
            owned = self.ctx.stake_by_finding(fid)
            if owned is not None and owned.get("withdrawn_at") is None and owned.get("origin") in MACHINE_ORIGINS:
                n = await self.store.withdraw_stakes_for_finding(fid, now=self.now)
                self.count("reconcile", "tombstone_stakes_withdrawn", n)
            return
        if status not in _LIVE_OWN_STATUSES | _OPEN_STATUSES:
            return
        update = f.get("tier") == TIER_UPDATE and status in _OPEN_STATUSES
        stake = self._stake_of(f)
        if status in _LIVE_OWN_STATUSES and stake is None:
            await self._studio_deleted(f)
            return
        if stake is not None and not update and stake.get("finding_id") != f.get("id"):
            await self._handed_over(f, stake)
            return
        if status == STATUS_STAGED:
            if not db_only:
                await self.go_staged(f, stake)
            elif stake.get("withdrawn_at") is not None:
                await self._catch_up_withdrawn(f, stake)
        elif status == STATUS_NOTED:
            if stake.get("withdrawn_at") is not None:
                await self._catch_up_withdrawn(f, stake)
            elif stake.get("published") is True:
                await self._cas(f, STATUS_NOTED, {"status": STATUS_PUBLISHED, "published_at": self.now})
            elif not db_only:
                await self.go_noted(f, stake)
        elif status in (STATUS_PUBLISHED, STATUS_KEPT):
            if stake.get("withdrawn_at") is not None:
                await self._catch_up_withdrawn(f, stake)
        elif status == STATUS_PUBLISHING:
            updated = _as_ts(f.get("updated_at"))
            if updated is not None and (self.now - updated).total_seconds() < PUBLISHING_STALE_SECONDS:
                return   # a tap is in flight
            await self._finish_publishing(f, stake, db_only=db_only)
        elif status == STATUS_PROPOSED and stake is not None and stake.get("withdrawn_at") is not None and not update:
            await self._cas(f, STATUS_PROPOSED, {"status": STATUS_WITHDRAWN})

    async def _hide_held_duplicate(self, f: Dict[str, Any]) -> None:
        """(WO-2) A heads-up from a publish-time identity refusal still owning an un-withdrawn
        machine stake: hide it now — with NO owner decision (the finder downgraded it)."""
        stake = self.ctx.stakes.get(str(f["stake_id"]))
        if (stake is None or stake.get("finding_id") != f.get("id") or stake.get("origin") not in MACHINE_ORIGINS
                or stake.get("withdrawn_at") is not None):
            return
        logger.info("[tc-stakes-reconcile] %s %s: hiding the held-back duplicate stake %s (finding %s)",
                    f.get("company_slug"), f.get("accession"), stake.get("id"), f.get("id"))
        await self._hide_duplicate(f, stake)

    async def _studio_deleted(self, f: Dict[str, Any]) -> None:
        """(e) The owner deleted the row in Studio: a tombstone + one digest line (notified_at NULL
        until the digest delivered it)."""
        if await self._cas(f, f.get("status"), {"status": STATUS_WITHDRAWN, "owner_decision": DECISION_DELETED_IN_STUDIO,
                                                "decided_at": self.now, "notified_at": None}):
            self.count("reconcile", "studio_deleted")

    async def _handed_over(self, f: Dict[str, Any], stake: Mapping[str, Any]) -> None:
        """(f) The seed adopted the row (or a later finding of ours replaced it): ``superseded``,
        never retried — a digest line for a seed adoption."""
        seed = stake.get("origin") == ORIGIN_SEED
        if await self._cas(f, f.get("status"), {"status": STATUS_SUPERSEDED,
                                                "notified_at": None if seed else self.now}):
            self.count("reconcile", "handed_over" if seed else "replaced")

    async def _finish_publishing(self, f: Dict[str, Any], stake: Optional[Dict[str, Any]], *, db_only: bool) -> None:
        """(d) A Publish tap that died mid-way. The stake carries this finding (the INSERT, the
        adoption or an update's replace landed): live → ``published``; not live → gates 3 and 5
        (and the pair's exits), then the ``withdrawn_at``-guarded flip — and the proposal's message
        is marked with the verdict and a Withdraw button, so the live row is never left without
        one (review B TAP-3). Otherwise — or when a gate refuses — back to ``proposed`` (the owner
        may tap again) with a digest line and its 14 days restarted: the owner DID answer, and
        this run's expiry must never tombstone that answer (review B WO-3). A db_only run makes
        only the record of a publish that landed — never a send, a publish or a roll-back (its
        digest line would be lost: verifier R4)."""
        fid = str(f["id"])
        owns = stake is not None and stake.get("finding_id") == f.get("id")
        if owns and stake.get("published") is True:
            if await self._cas(f, STATUS_PUBLISHING, {"status": STATUS_PUBLISHED, "owner_decision": DECISION_PUBLISHED,
                                                      "decided_at": self.now, "stake_id": str(stake["id"]),
                                                      "published_at": self.now}) and not db_only:
                await self._mark_finished(f)
            return
        if db_only:
            # The roll-back of a Publish that wrote no row waits for a FULL run: a db_only run sends no
            # digest, so its "back to a proposal" line would be lost and the owner — told the publish
            # would finish — would never learn it did not (verifier R4).
            logger.info("[tc-stakes-reconcile] %s %s: an unfinished Publish (finding %s) waits for a full run",
                        f.get("company_slug"), f.get("accession"), fid)
            return
        pr = f.get("proposed_row")
        if owns and stake.get("withdrawn_at") is None and stake.get("origin") in MACHINE_ORIGINS and _is_stake_row(pr):
            sid = str(stake["id"])
            problem = await self._tap_gates(f, pr, exclude_stakes=[sid])
            adopted = stake.get("origin") == ORIGIN_PROPOSAL or (
                problem is None and await self.store.adopt_as_proposal(sid, fid, now=self.now))
            if problem is None and adopted and await self.store.publish_stake(sid, fid, now=self.now):
                self.wrote()
                self.shown_changed()
                if await self._cas(f, STATUS_PUBLISHING, {"status": STATUS_PUBLISHED, "owner_decision": DECISION_PUBLISHED,
                                                          "decided_at": self.now, "stake_id": sid,
                                                          "published_at": self.now}):
                    await self._mark_finished(f)
                return
            if problem:
                logger.warning("[tc-stakes-reconcile] %s %s: the unfinished Publish is refused now (%s) — back to a proposal",
                               f.get("company_slug"), f.get("accession"), problem)
        if await self._cas(f, STATUS_PUBLISHING, {"status": STATUS_PROPOSED, "notified_at": self.now}):
            self.alerts.append(f"rolled_back:{f.get('company_slug')}:{f.get('accession')}")

    async def _mark_finished(self, f: Mapping[str, Any]) -> None:
        """The proposal's message after the run finished its Publish: the verdict, and Withdraw."""
        await self._mark(f.get("telegram_message_id"), str(f["id"]),
                         f"Published {_hhmm(self.now)} (finished by the run)", buttons=(VERB_WITHDRAW,))

    async def _tap_gates(self, f: Mapping[str, Any], pr: Mapping[str, Any], *, exclude_stakes: Sequence[str]) -> Optional[str]:
        stakes = await self.store.read_stakes()
        findings = await self.store.read_findings_by_status(
            (STATUS_NEW, STATUS_PROPOSED, STATUS_PUBLISHING, *sorted(TOMBSTONE_STATUSES)))
        exit_on = await self._pair_exit(f.get("company_slug"), f.get("investee_cik") or pr.get("investee_cik"))
        return _publish_tap_problem(f, pr, stakes=stakes, findings=findings, ctx=self.ctx, exclude_stakes=exclude_stakes,
                                    today=self.today, exit_on=exit_on)

    # ── S1 / S2 ──

    async def s1(self, disc: _Discovery, members: Sequence[MemberConfig]) -> None:
        for cfg in members:
            if not cfg.s1_filers:
                continue
            self.count("s1", "members")
            async for raw in disc.s1(cfg):
                await self.consume(raw, "s1")

    async def s2(self, disc: _Discovery, members: Sequence[MemberConfig]) -> None:
        for cfg in members:
            if not cfg.s2_aliases:
                continue
            self.count("s2", "members")
            others = [a for s, o in self.configs.items() if s != cfg.slug for a in o.s2_aliases]
            async for raw in disc.s2(cfg, others):
                await self.consume(raw, "s2")

    # ── the Monday re-check ──

    async def recheck(self, disc: _Discovery) -> None:
        for s in sorted(self.ctx.stakes.values(), key=lambda r: str(r.get("id"))):
            if (s.get("origin") not in MACHINE_ORIGINS or s.get("published") is not True
                    or s.get("withdrawn_at") is not None or not s.get("finding_id")):
                continue
            self.count("recheck", "rows")
            try:
                await self._recheck_one(disc, s)
            except (_StopSEC, _StopEFTS):
                raise
            except StakeStoreError as e:
                self.fail("recheck", f"stake {s.get('id')}: {_err(e)}")

    async def _recheck_one(self, disc: _Discovery, s: Mapping[str, Any]) -> None:
        slug, cik, sid = s.get("company_slug"), s.get("investee_cik"), str(s["id"])
        if not cik:
            logger.warning("[tc-stakes-recheck] stake %s (%s) has no investee CIK — no change", sid, slug)
            return
        as_of = _as_date(s.get("as_of"))
        try:
            subs = await disc.issuer(cik)
        except (_StopSEC, _StopEFTS):
            raise
        except SECException as e:
            self.fail("recheck", f"{slug} stake {sid}: the issuer's submissions failed ({_err(e)}) — no change")
            return
        if subs is None:
            logger.warning("[tc-stakes-recheck] %s stake %s: SEC has no submissions for CIK %s — no change", slug, sid, cik)
            return
        delisting = _delisting_after(subs, as_of)
        if delisting is not None:
            await self._recheck_negative(s, delisting, cik)
            return
        symbol = s.get("investee_us_symbol")
        if symbol:
            listed, _why = choose_us_common_ticker(subs.get("tickers"), subs.get("exchanges"))
            if normalize_symbol(listed) != normalize_symbol(symbol):
                logger.warning("[tc-stakes-recheck] %s stake %s: the U.S. ticker is now %r, not %r — no change (read it)", slug, sid, listed, symbol)
                self.count("recheck", "unclear")
                return
        elif s.get("kind") == KIND_PRIVATE:
            # A "Private" row whose issuer now lists a U.S. common ticker (an IPO) — or more than one
            # — is no definite pass: its chip would be re-asserted as freshly verified (facts-F4).
            # Commitments are never held to this: most commitment investees are listed.
            listed, why = choose_us_common_ticker(subs.get("tickers"), subs.get("exchanges"))
            if listed or why == "ambiguous_listing":
                logger.warning("[tc-stakes-recheck] %s stake %s: a private row whose issuer now lists %s — no change "
                               "(read it)", slug, sid, repr(listed) if listed else f"tickers {subs.get('tickers')!r}")
                self.count("recheck", "unclear")
                return
        superseding = _superseding_filing(await self.store.read_findings_for_pair(str(slug), str(cik)), s)
        if superseding is not None:
            # The member's own newer 13D/13G for the pair — an exit, different facts, or one not
            # read yet — in ANY status (reported, skipped, expired, dismissed included): the plan's
            # definite pass needs "no superseding 13D/G" (lifecycle-F5).
            logger.warning("[tc-stakes-recheck] %s stake %s: %s — no change", slug, sid, superseding)
            self.count("recheck", "unclear")
            return
        if s.get("kind") == KIND_ON_13F_NOTE:
            try:
                listed_13f = await disc.on_13f(str(slug), cusip=s.get("investee_cusip"), symbol=symbol)
            except StakeStoreError as e:
                self.fail("recheck", f"{slug} stake {sid}: the 13F holdings read failed ({_err(e)}) — no change")
                return
            if not listed_13f:
                logger.warning("[tc-stakes-recheck] %s stake %s: the latest 13F no longer lists it — no change (read it)",
                               slug, sid)
                self.count("recheck", "unclear")
                return
        if await self.store.bump_verified_on(sid, str(s["finding_id"]), self.today, now=self.now):
            self.wrote()
            self.count("recheck", "verified")
            self.shown_changed()

    async def _recheck_negative(self, s: Mapping[str, Any], delisting: Tuple[str, str, date, str], cik: str) -> None:
        """A Form 25 / 15 after the row's as_of: ONE finding per such filing (its accession is the
        claim), and a loud note offering Withdraw. The row stays live until the owner decides."""
        acc, form, filed, doc = delisting
        slug = str(s.get("company_slug"))
        known = await self.store.read_findings_for_accessions(slug, [acc])
        if acc in known:
            return
        raw = _Raw(slug=slug, source=SOURCE_SCH13, accession=acc, filer_cik=cik, form=form, filed_on=filed,
                   document=doc, source_url=archive_url(cik, acc, doc), investee_cik=cik, tier=TIER_OTHER,
                   outcome=OUTCOME_HEADS_UP, reason="recheck_negative", label=s.get("investee_name"),
                   detail=f"Form {form} filed {filed} (after the row's as-of date {s.get('as_of')})")
        f = await self._write(_heads_up(raw, "recheck_negative"), STATUS_NEW, stake_id=str(s["id"]))
        if f is not None:
            self.count("recheck", "negative")
            await self._recheck_note(f)

    async def _recheck_note(self, f: Dict[str, Any]) -> None:
        stake = self.ctx.stakes.get(str(f.get("stake_id"))) or await self.store.read_stake(str(f.get("stake_id")))
        if stake is None:
            await self._cas(f, STATUS_NEW, {"status": STATUS_REPORTED})
            return
        if not self.take_loud():
            return
        detail = _meta(f.get("proposed_row"), "_detail") or "a filing after the row's as-of date"
        text = (f"Monday re-check: {stake.get('investee_name')} — {detail}. The row stays live until you decide; "
                "Withdraw takes it down.")
        mid = await self.notifier.send_lifecycle_note(self.present(f), _stake_columns(stake), text=text,
                                                      offer_withdraw=stake.get("withdrawn_at") is None)
        if mid is None:
            self.fail("notes", f"{f.get('company_slug')} {f.get('accession')}: the re-check note was not delivered")
            return
        await self._cas(f, STATUS_NEW, {"status": STATUS_REPORTED, "telegram_message_id": mid, "notified_at": self.now})

    # ── expiry ──

    async def expiry(self) -> None:
        cutoff = self.now - timedelta(days=PROPOSAL_TTL_DAYS)
        proposed = await self.store.read_findings_by_status([STATUS_PROPOSED])
        for f in proposed:
            sent = _as_ts(f.get("notified_at"))
            if sent is None or sent > cutoff:
                continue
            if not await self._cas(f, STATUS_PROPOSED, {"status": STATUS_EXPIRED, "notified_at": None}):
                continue
            self.count("expiry", "expired")
            await self.store.withdraw_stakes_for_finding(str(f["id"]), now=self.now)
            mid = f.get("telegram_message_id")
            if isinstance(mid, int) and not isinstance(mid, bool):
                ok = await self.notifier.mark_message(message_id=mid, finding_id=str(f["id"]),
                                                      verdict="Expired — not published", buttons=())
                if not ok:
                    logger.warning("[tc-stakes-expiry] message %s of finding %s could not be marked", mid, f.get("id"))

    # ── the digest ──

    async def digest(self) -> None:
        pending = await self.store.read_findings_by_status(
            [STATUS_NEW, STATUS_EXPIRED, STATUS_SUPERSEDED, STATUS_WITHDRAWN])
        # A held-back duplicate whose stake is not hidden yet waits for the reconcile that hides it:
        # once its line is reported, nothing would ever hide it (review B WO-2).
        pending = [f for f in pending if not self._hide_pending(f)
                   and (f.get("company_slug"), f.get("accession")) not in self.unfinished]
        recent = await self.store.read_findings_since(self.now - timedelta(days=DIGEST_DEDUPE_DAYS))
        lines, marks, deduped = _digest_lines(pending, recent, ctx=self.ctx, now=self.now, waiting=self.waiting,
                                              alerts=self.alerts, missing=self.missing)
        self.stage("digest")["lines"] = len(lines)
        if not lines:
            for f in deduped:
                await self._cas(f, STATUS_NEW, {"status": STATUS_REPORTED})
            return
        try:
            sent = await self.notifier.send_digest(lines)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 — the notifier promises not to raise; never trust it
            logger.error("[tc-stakes-digest] send_digest raised (%s)", _err(e), exc_info=True)
            sent = False
        if not sent:
            self.fail("digest", f"the digest ({len(lines)} line(s)) was not delivered — the next run resends")
            return
        self.stage("digest")["sent"] = True
        for f in marks:
            if f.get("status") == STATUS_NEW:
                await self._cas(f, STATUS_NEW, {"status": STATUS_REPORTED, "notified_at": self.now})
            else:
                await self._cas(f, f.get("status"), {"notified_at": self.now})
        for f in deduped:
            await self._cas(f, STATUS_NEW, {"status": STATUS_REPORTED})


_DECIDING_STATUSES = frozenset({STATUS_NEW, STATUS_STAGED, STATUS_NOTED, STATUS_PROPOSED, STATUS_PUBLISHING,
                                STATUS_ERROR})


def _superseding_filing(pair: Iterable[Mapping[str, Any]], s: Mapping[str, Any]) -> Optional[str]:
    """Why the Monday re-check may NOT call a live row verified, from ALL of its pair's findings
    (any status) — or None. In order: an exit filing of the member dated on or after the row's
    as_of; another finding of the pair still being decided; the member's own 13D/13G filed after
    the row's as_of whose facts differ (a dismissed or expired update included) or that holds no
    comparable row (a heads-up) — a skip (the same facts, an older event) changes nothing."""
    as_of = _as_date(s.get("as_of"))
    rows = [dict(x) for x in pair if isinstance(x, Mapping) and str(x.get("id")) != str(s.get("finding_id"))]
    exit_on = _newest_exit(rows)
    if _exit_binds(s, exit_on):
        return f"the member's exit filing (as of {exit_on}) is on file"
    for x in rows:
        if x.get("status") in _DECIDING_STATUSES:
            return f"a newer filing for the pair is still being decided ({x.get('accession')}, {x.get('status')})"
    for x in rows:
        if _exit_event(x) is not None and not _exit_binds(s, _exit_event(x)):
            continue   # an exit that does not contradict this row (an amount-only S2 row): no news for it
        filed = _as_date(x.get("filed_on"))
        if (x.get("source") != SOURCE_SCH13 or x.get("reason") == "recheck_negative" or filed is None
                or as_of is None or filed <= as_of or x.get("status") == STATUS_SUPERSEDED):
            continue   # (a finding this row replaced — an update's old finding, a hand-over — is no news)
        pr = x.get("proposed_row")
        if _is_stake_row(pr):
            event = _as_date(pr.get("as_of"))
            if event is not None and event < as_of:
                continue   # its facts are OLDER than the row's (filed late): nothing newer. The same
                #            as-of date with other facts is a correction, and still counts.
            if not _same_facts(pr, s):
                return f"the member's {x.get('form')} {x.get('accession')} ({x.get('status')}) says {_row_summary(pr)}"
        elif x.get("outcome") != OUTCOME_SKIPPED:
            return f"the member's {x.get('form')} {x.get('accession')} ({x.get('reason')}) is not reflected in the row"
    return None


def _delisting_after(subs: Mapping[str, Any], as_of: Optional[date]) -> Optional[Tuple[str, str, date, str]]:
    """The first Form 25 / 15 the issuer filed after ``as_of`` → (accession, form, filed, document)."""
    recent = (subs.get("filings") or {}).get("recent") if isinstance(subs.get("filings"), Mapping) else None
    if not isinstance(recent, Mapping) or as_of is None:
        return None
    rows = zip(recent.get("accessionNumber") or [], recent.get("form") or [], recent.get("filingDate") or [],
               recent.get("primaryDocument") or [])
    found = []
    for acc_raw, form, day, doc in rows:
        form = " ".join(str(form).upper().split()) if isinstance(form, str) else ""
        filed, acc = _as_date(day), normalize_accession(acc_raw)
        if form in DELISTING_FORMS and filed is not None and acc and filed > as_of:
            name = doc.rsplit("/", 1)[-1] if isinstance(doc, str) and doc else "index.htm"
            found.append((acc, form, filed, name if _DOCUMENT_RE.fullmatch(name) else "index.htm"))
    return min(found, key=lambda x: (x[2], x[0])) if found else None


def _publish_tap_problem(f: Mapping[str, Any], pr: Mapping[str, Any], *, stakes: Iterable[Mapping[str, Any]],
                         findings: Iterable[Mapping[str, Any]], ctx: _Ctx, exclude_stakes: Sequence[str],
                         today: date, exit_on: Optional[date] = None) -> Optional[str]:
    """Gates 3 and 5 (and 7's age rule) before a Publish tap's write — why not, or None.

    ``exit_on`` is the newest exit filing of the pair on file (any status): a proposal — plain or
    UPDATE — dated on or before it is never published (review B TAP-1 / TAP-2: an update's Publish
    would put a pre-exit row back up). An UPDATE proposal is otherwise checked against the other
    STAKE rows only: its target is the pair's row, so a dismissed or expired earlier update of that
    row is no tombstone for it."""
    if _exit_binds(pr, exit_on):
        return f"a later filing of the member reports 5% or less (as of {_short_date(exit_on)})"
    if f.get("tier") == TIER_UPDATE:
        findings = ()
    fresh = _Ctx(today=today, companies=ctx.companies, company_rows=ctx.company_rows, configs=ctx.configs,
                 stakes=stakes, findings=findings)
    slug = str(f.get("company_slug"))
    excluded = list(exclude_stakes) + [str(s.get("id")) for s in fresh.stakes.values() if s.get("finding_id") == f.get("id")]
    m = _match(fresh, slug, cik=f.get("investee_cik") or pr.get("investee_cik"), cusip=pr.get("investee_cusip"),
               symbol=pr.get("investee_us_symbol"), name=pr.get("investee_name"), exclude_findings=[str(f.get("id"))],
               exclude_stakes=excluded)
    if m is not None:
        if m.row.get("_src") == "tombstone" or m.row.get("withdrawn_at"):
            return f"this pair was withdrawn or dismissed before ({_clip(_row_summary(m.row), 80)})"
        return f"a row for this company is on file ({_clip(_row_summary(m.row), 80)})"
    problem = _validator_problem({**pr}, origin=ORIGIN_PROPOSAL, company_rows=ctx.company_rows, today=today)
    if problem:
        return f"the row fails a check ({_clip(problem, 100)})"
    as_of = _as_date(pr.get("as_of"))
    if as_of is None or (today - as_of).days > AS_OF_MAX_AGE_DAYS:
        return f"the row is more than {AS_OF_MAX_AGE_DAYS} days old"
    return None


# ── Digest lines (pure) ──────────────────────────────────────────────────────────────────


def _dedupe_key(f: Mapping[str, Any]) -> Tuple[Any, Any, Any]:
    return (f.get("company_slug"), f.get("investee_cik") or f"acc:{f.get('accession')}", f.get("reason"))


def _heads_up_line(f: Mapping[str, Any], ctx: _Ctx) -> str:
    pr = f.get("proposed_row") if isinstance(f.get("proposed_row"), Mapping) else {}
    member = ctx.display(f.get("company_slug"))
    investee = _meta(pr, "_investee") or pr.get("investee_name") or (
        f"issuer CIK {f.get('investee_cik')}" if f.get("investee_cik") else "a filing")
    detail = _meta(pr, "_detail")
    reason = REASONS.get(str(f.get("reason")), str(f.get("reason")))
    filed = _short_date(_as_date(f.get("filed_on")))
    body = f"{member} → {investee}: {reason}" + (f" — {_clip(detail, 200)}" if detail else "")
    return f"• {body} ({f.get('form')}, filed {filed}) {f.get('source_url')}"


def _t3_line(group: List[Mapping[str, Any]], ctx: _Ctx) -> str:
    first = group[0]
    t3 = _meta(first.get("proposed_row"), "_t3") or {}
    alias_to_slug = {a: s for s, c in ctx.configs.items() for a in c.s2_aliases}
    names: List[str] = []
    for alias in t3.get("members") or []:
        slug = alias_to_slug.get(alias)
        names.append(ctx.display(slug) if slug else str(alias))
    for f in group:
        name = ctx.display(f.get("company_slug"))
        if name not in names:
            names.append(name)
    t3s = [_meta(f.get("proposed_row"), "_t3") or {} for f in group]
    amounts = [x for x in t3s if x.get("amount")]
    amount = _usd_short(amounts[0]["amount"]) if amounts else None
    grouped = any(x.get("combined") is True for x in t3s)
    # "NVIDIA and Beta Capital LLC" / "the Cornerstone Investors": the amount is the group's, so a
    # lone club name never reads as that member's own figure.
    who = f"{names[0]} and others" if grouped and len(names) == 1 else ", ".join(names)
    combined = " combined" if grouped or len(names) > 1 else ""
    forms = ", ".join(unique_preserving(str(f.get("form")) for f in group))
    tail = f", up to {amount}{combined}" if amount else ""
    return (f"• {t3.get('company') or 'A company'} {forms} names {who}: non-binding interest{tail} "
            f"{first.get('source_url')}")


def _digest_lines(pending: Sequence[Mapping[str, Any]], recent: Sequence[Mapping[str, Any]], *, ctx: _Ctx,
                  now: datetime, waiting: int, alerts: Sequence[str], missing: Sequence[str]
                  ) -> Tuple[List[str], List[Dict[str, Any]], List[Dict[str, Any]]]:
    """(lines, findings to mark delivered when they are sent, heads-ups deduped away)."""
    today = now.astimezone(ET).date()
    window = now - timedelta(days=DIGEST_DEDUPE_DAYS)
    delivered = {_dedupe_key(f) for f in recent if f.get("status") == STATUS_REPORTED
                 and (_as_ts(f.get("notified_at")) or datetime.min.replace(tzinfo=timezone.utc)) >= window}
    entries: List[Tuple[str, List[Dict[str, Any]]]] = []
    deduped: List[Dict[str, Any]] = []
    seen: Set[Tuple[Any, Any, Any]] = set()
    t3_groups: Dict[Tuple[Any, Any], List[Dict[str, Any]]] = {}
    for f in sorted((dict(x) for x in pending), key=lambda r: (str(r.get("created_at")), str(r.get("id")))):
        status = f.get("status")
        if status == STATUS_NEW and f.get("outcome") == OUTCOME_HEADS_UP:
            if f.get("stake_id") and (f.get("reason") == "recheck_negative"
                                      or (f.get("reason") == "exit" and _exit_event(f) is not None)):
                continue   # a lifecycle note, not a digest line
            key = _dedupe_key(f)
            # An exit (a held-back row, a void proposal, a refused older filing) is never deduped:
            # each one stops something the owner should see.
            # A fetch_failed line is the only pointer to ITS filing: never deduped by pair either.
            if (f.get("reason") not in ("exit", "fetch_failed")
                    and (key in delivered or key in seen and f.get("reason") != "t3_indication")):
                deduped.append(f)
                continue
            seen.add(key)
            t3 = _meta(f.get("proposed_row"), "_t3")
            if f.get("reason") == "t3_indication" and isinstance(t3, Mapping):
                t3_groups.setdefault((f.get("investee_cik"), t3.get("key")), []).append(f)
                continue
            entries.append((_heads_up_line(f, ctx), [f]))
        elif status in (STATUS_EXPIRED, STATUS_SUPERSEDED, STATUS_WITHDRAWN) and f.get("notified_at") is None:
            pr = f.get("proposed_row") if isinstance(f.get("proposed_row"), Mapping) else {}
            pair = f"{ctx.display(f.get('company_slug'))} → {pr.get('investee_name') or _meta(pr, '_investee') or 'a row'}"
            if status == STATUS_EXPIRED:
                entries.append((f"• Expired unpublished ({PROPOSAL_TTL_DAYS} days, no answer): {pair}", [f]))
            elif status == STATUS_SUPERSEDED:
                entries.append((f"• Handed over to the seed file (the finder no longer manages it): {pair}", [f]))
            elif f.get("owner_decision") == DECISION_DELETED_IN_STUDIO:
                entries.append((f"• Deleted in Studio — now a tombstone (never re-added): {pair}", [f]))
    for _key, group in t3_groups.items():
        entries.append((_t3_line(group, ctx), group))
    extras: List[str] = []
    if waiting:
        extras.append(f"• {waiting} message(s) wait for the next run (at most {MAX_LOUD_MESSAGES_PER_RUN} a run)")
    if "volume_cap" in alerts:
        extras.append(f"• Auto-publish cap reached ({AUTO_MAX_PER_DAY} a day / {AUTO_MAX_PER_WEEK} a week): finds "
                      "went out as proposals")
    if "breaker" in alerts:
        extras.append(f"• Auto-publish is paused: {BREAKER_WITHDRAWALS}+ owner Withdraws since "
                      "TRILLION_CLUB_STAKE_AUTO_SINCE. Move that date past them to resume.")
    rolled = [a for a in alerts if a.startswith("rolled_back:")]
    for a in rolled:
        _, slug, acc = a.split(":", 2)
        extras.append(f"• A Publish tap did not finish — back to a proposal: {ctx.display(slug)} {acc}")
    if today.day == 1:
        for slug in missing:
            extras.append(f"• {ctx.display(slug)}: stake finder not enabled (not in trillion_club_filers.json)")
    # The skip count is of THIS run's new skips (their created_at is the run's `now`): a retried
    # run, which finds every filing already on file, repeats no line and sends no digest.
    skipped_now = [f for f in recent if f.get("status") == STATUS_SKIPPED
                   and (_as_ts(f.get("created_at")) or now - timedelta(days=1)) >= now]
    if skipped_now:
        by_reason: Dict[str, int] = {}
        for f in skipped_now:
            by_reason[str(f.get("reason"))] = by_reason.get(str(f.get("reason")), 0) + 1
        parts = ", ".join(f"{r} {n}" for r, n in sorted(by_reason.items(), key=lambda kv: (-kv[1], kv[0])))
        extras.append(f"• Skipped (recorded, not shown): {len(skipped_now)} new ({parts})")
    if today.weekday() == 0 and (entries or extras):
        # The Monday summary rides with a digest that has news; it never sends one alone.
        week_start = now - timedelta(days=7)
        per: Dict[str, int] = {}
        for f in recent:
            if f.get("status") != STATUS_SKIPPED or (_as_ts(f.get("created_at")) or now) < week_start:
                continue
            label = _meta(f.get("proposed_row"), "_investee") or (f"CIK {f.get('investee_cik')}"
                                                                  if f.get("investee_cik") else "no investee")
            per[str(label)] = per.get(str(label), 0) + 1
        ranked = sorted(per.items(), key=lambda kv: (-kv[1], kv[0]))
        for label, n in ranked[:WEEKLY_SKIP_LINES]:
            extras.append(f"• This week's skips — {label}: {n}")
        if len(ranked) > WEEKLY_SKIP_LINES:
            extras.append(f"• This week's skips — +{len(ranked) - WEEKLY_SKIP_LINES} more investees")
    lines: List[str] = []
    marks: List[Dict[str, Any]] = []
    room = DIGEST_MAX_LINES - len(extras)
    units = sum(_digest_units(x) for x in extras) + _digest_units("• +9999 more — they come in the next digest")
    taken = 0
    for text, group in entries[:max(room, 0)]:
        cost = _digest_units(text)
        if units + cost > DIGEST_MAX_UNITS:
            break
        units += cost
        lines.append(text)
        marks.extend(group)
        taken += 1
    if len(entries) > taken:
        more = len(entries) - taken
        lines.append(f"• +{more} more — they come in the next digest")
        logger.warning("[tc-stakes-digest] %d line(s) over the %d-line / %d-unit cap wait for the next digest",
                       more, DIGEST_MAX_LINES, DIGEST_MAX_UNITS)
    return lines + extras, marks, deduped


def _digest_units(line: str) -> int:
    """What one line costs in the digest (UTF-16 units, as Telegram counts; capped as the notifier
    caps a line, plus its newline)."""
    return len(str(line)[:1000].encode("utf-16-le")) // 2 + 1


# ── run_stakes ───────────────────────────────────────────────────────────────────────────


def _default_notifier() -> Any:
    from app.services.trillion_club.stake_review import TelegramStakeNotifier

    return TelegramStakeNotifier()


async def run_stakes(now: datetime, *, sec: Any = None, store: Any = None, notifier: Any = None,
                     member_configs: Optional[Mapping[str, MemberConfig]] = None, club_store: Any = None) -> Dict[str, Any]:
    """One run of the stake finder (the module docstring has the order). Returns
    ``{"job": "stakes", "now", "ok", "items", "failures", "stages"}`` — ``ok`` is True only when
    every stage completed; ``jobs.failure_text`` reads it. Raises only for a naive ``now``."""
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ValueError(f"stake finder: now must be a timezone-aware datetime, got {now!r}")
    configs_error: Optional[str] = None
    if member_configs is None:
        try:
            member_configs = load_member_configs(str(FILERS_PATH))
        except (OSError, ValueError) as e:
            logger.error("%s the filers file %s is unusable (%s) — no SEC stage runs", _TAG, FILERS_PATH.name, _err(e))
            configs_error, member_configs = _err(e), {}
    if notifier is None:
        try:
            notifier = _default_notifier()
        except Exception as e:  # noqa: BLE001
            logger.error("%s the Telegram notifier could not load (%s)", _TAG, _err(e), exc_info=True)
            notifier = NullNotifier()
    run = _Run(now, sec=sec if sec is not None else sec_edgar.get_sec_edgar_client(),
               store=store if store is not None else StakeStore(), notifier=notifier, configs=member_configs,
               configs_error=configs_error, club_store=club_store if club_store is not None else TrillionClubStore())
    try:
        await run.go()
    except asyncio.CancelledError:
        raise
    except Exception as e:  # noqa: BLE001 — a run never raises for a data or I/O problem
        logger.exception("%s the run crashed", _TAG)
        run.fail("run", f"crashed ({_err(e)})")
    summary = run.summary
    summary["stages"]["sec"] = {**summary["stages"].get("sec", {"ok": True}), "requests": run.sec.requests,
                                "documents": run.sec.documents}
    log = logger.info if summary["ok"] else logger.warning
    log("[tc-stakes-run] %s — %d write(s); %s%s", "ok" if summary["ok"] else "INCOMPLETE", summary["items"],
        ", ".join(f"{k}={'ok' if v.get('ok', True) else 'FAILED'}" for k, v in summary["stages"].items()),
        f"; failures: {' | '.join(summary['failures'])}" if summary["failures"] else "")
    return summary


# ── apply_owner_decision: the taps ───────────────────────────────────────────────────────


async def apply_owner_decision(verb: str, finding_id: str, *, message_id: Optional[int], now: datetime,
                               store: Any = None, club_store: Any = None) -> TapResult:
    """What ONE owner tap does — the whole tap state machine (module docstring, "Verbs" in the
    plan). Never gated by the finder flag. Never raises except ``CancelledError``: an unexpected
    error is logged with its stack and answered ``TapResult(ok=False, toast=TOAST_CRASH)``.
    ``club_store`` (the registry, for the Publish tap's validators) defaults like the run's."""
    try:
        return await _apply(verb, finding_id, message_id=message_id, now=now,
                            store=store if store is not None else StakeStore(),
                            club_store=club_store if club_store is not None else TrillionClubStore())
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001
        logger.exception("[tc-stakes-tap] %r on finding %r (message %r) crashed", verb, finding_id, message_id)
        return TapResult(ok=False, toast=TOAST_CRASH)


async def _apply(verb: str, finding_id: str, *, message_id: Optional[int], now: datetime, store: Any,
                 club_store: Any) -> TapResult:
    if verb not in CALLBACK_VERBS:
        return TapResult(ok=False, toast=TOAST_UNKNOWN)
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ValueError(f"a tap needs a timezone-aware now, got {now!r}")
    try:
        fid = str(uuid.UUID(str(finding_id)))
    except (TypeError, ValueError):
        return TapResult(ok=False, toast=TOAST_UNKNOWN)
    f = await store.read_finding(fid)
    if f is None:
        return TapResult(ok=False, toast=TOAST_GONE)
    logger.info("[tc-stakes-tap] %s on %s %s (finding %s, status %s, message %s)", verb, f.get("company_slug"),
                f.get("accession"), fid, f.get("status"), message_id)
    if verb == VERB_WITHDRAW:
        return await _tap_withdraw(store, f, now)
    if verb == VERB_KEEP:
        return await _tap_keep(store, f, message_id, now)
    if verb == VERB_PUBLISH:
        return await _tap_publish(store, club_store, f, message_id, now)
    return await _tap_dismiss(store, f, message_id, now)


async def _stake_for(store: Any, f: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    if f.get("stake_id"):
        return await store.read_stake(str(f["stake_id"]))
    return await store.read_stake_by_finding(str(f["id"]))


_DECIDED_VERDICTS: Dict[str, str] = {
    STATUS_WITHDRAWN: "Withdrawn earlier", STATUS_DISMISSED: "Dismissed earlier",
    STATUS_EXPIRED: "Expired — not published", STATUS_SUPERSEDED: "Handed over to the seed file",
}


async def _state_view(store: Any, f: Mapping[str, Any]) -> Optional[Tuple[str, Tuple[str, ...]]]:
    """(verdict, buttons) for a finding's CURRENT decided state, or None when it is not decided (or
    the stake cannot be read). A live machine row always comes back with its Withdraw button."""
    status = f.get("status")
    if status in (STATUS_PUBLISHED, STATUS_KEPT):
        try:
            stake = await _stake_for(store, f)
        except StakeStoreError as e:
            logger.warning("[tc-stakes-tap] finding %s: its stake could not be read to show its state (%s)",
                           f.get("id"), _err(e))
            return None
        if stake is None or stake.get("origin") not in MACHINE_ORIGINS:
            return None
        if stake.get("withdrawn_at") is not None:
            return "Withdrawn earlier", ()
        if stake.get("published") is True:
            return "Live — published earlier", (VERB_WITHDRAW,)
        return await _exit_stopped_view(store, f, stake)
    if status == STATUS_SUPERSEDED and f.get("reason") == "exit":
        return VERDICT_VOID_EXIT, ()     # voided by the member's exit filing (``_exit_sweep``)
    if status == STATUS_CLOSED and f.get("outcome") == OUTCOME_AUTO:
        # Held back (never live) or taken down by the member's exit filing (verifier R6): no button.
        try:
            stake = await _stake_for(store, f)
        except StakeStoreError as e:
            logger.warning("[tc-stakes-tap] finding %s: its stake could not be read to show its state (%s)",
                           f.get("id"), _err(e))
            return None
        if stake is not None and stake.get("published") is True and stake.get("withdrawn_at") is None:
            return None                  # live after all: the Withdraw tap acts on it
        return (VERDICT_DOWN_EXIT if f.get("published_at") else VERDICT_HELD_EXIT), ()
    verdict = _DECIDED_VERDICTS.get(str(status))
    if verdict:
        return verdict, ()
    if f.get("stake_id") and f.get("outcome") == OUTCOME_HEADS_UP:
        # e.g. an auto row the publish gate refused because of the member's exit: its old note
        try:
            return await _exit_stopped_view(store, f, await _stake_for(store, f))
        except StakeStoreError as e:
            logger.warning("[tc-stakes-tap] finding %s: its stake could not be read (%s)", f.get("id"), _err(e))
    return None


async def _exit_stopped_view(store: Any, f: Mapping[str, Any],
                             stake: Optional[Mapping[str, Any]]) -> Optional[Tuple[str, Tuple[str, ...]]]:
    """The held-back / taken-down view of a machine stake that is NOT live and that the member's
    own exit filing binds — decided from the stake and the pair's exits, whatever the finding's
    status (a heads-up from the publish gate, a finding whose close was not written yet: final
    verify #3). None otherwise, or when the pair cannot be read (the caller acts as before)."""
    if (stake is None or stake.get("origin") not in MACHINE_ORIGINS or stake.get("published") is True
            or stake.get("withdrawn_at") is not None):
        return None
    slug, cik = stake.get("company_slug"), stake.get("investee_cik") or f.get("investee_cik")
    if not isinstance(slug, str) or not isinstance(cik, str) or not cik:
        return None
    try:
        exit_on = _newest_exit(await store.read_findings_for_pair(slug, cik))
    except StakeStoreError as e:
        logger.warning("[tc-stakes-tap] finding %s: the pair's exits could not be read (%s)", f.get("id"), _err(e))
        return None
    if not _exit_binds(stake, exit_on):
        return None
    return (VERDICT_DOWN_EXIT if f.get("published_at") else VERDICT_HELD_EXIT), ()


async def _decided_result(store: Any, f: Mapping[str, Any], toast: str) -> TapResult:
    """A tap on an already-decided finding re-shows its CURRENT state on the tapped message: a
    message whose verdict edit failed (flood control, an outage) — or that a late proposal edit
    reset to Publish / Dismiss — is repaired by the owner's next tap (review A TG-1)."""
    view = await _state_view(store, f)
    if view is None:
        return TapResult(ok=False, toast=toast)
    return TapResult(ok=False, toast=toast, verdict=view[0], buttons=view[1])


async def _cas_loop(store: Any, fid: str, changes: Mapping[str, Any], now: datetime, *,
                    allowed: Callable[[Mapping[str, Any]], bool], attempts: int = 3) -> bool:
    """CAS the finding from whatever status it has now, while ``allowed(row)`` — a tap racing
    the run (noted → published) re-reads and tries again."""
    for _ in range(attempts):
        f = await store.read_finding(fid)
        if f is None or not allowed(f):
            return False
        if await store.update_finding(fid, expected_status=f.get("status"), changes=changes, now=now):
            return True
    return False


async def _follow_up(store: Any, fid: str, changes: Mapping[str, Any], now: datetime, *, what: str,
                     allowed: Callable[[Mapping[str, Any]], bool]) -> None:
    """A ledger write that FOLLOWS a stake write which already landed (the row is down, or live):
    best effort. Its failure is logged and left to the next run's reconcile — the tap's answer
    says what happened to the row, never "something went wrong" about a write that worked."""
    try:
        if not await _cas_loop(store, fid, changes, now, allowed=allowed):
            logger.info("[tc-stakes-tap] %s (finding %s) had moved on — nothing to follow up", what, fid)
    except StakeStoreError as e:
        logger.warning("[tc-stakes-tap] %s (finding %s) could not be updated (%s) — the next run's reconcile "
                       "catches the ledger up", what, fid, _err(e))


async def _tap_withdraw(store: Any, f: Mapping[str, Any], now: datetime) -> TapResult:
    """Withdraw: ONE PATCH on the stake (no message or fact fence: unpublishing is always safe),
    then the ledger, then the read cache. Works after Keep, from any finding linked to the row."""
    if f.get("status") in TOMBSTONE_STATUSES:
        return await _decided_result(store, f, TOAST_DECIDED)
    if f.get("status") == STATUS_CLOSED and f.get("outcome") == OUTCOME_AUTO:
        # The member's exit already held this row back or took it down: a stale Withdraw on its old
        # note never records an owner decision (the breaker counts those) nor tombstones the pair
        # against a genuine re-entry (verifier R6). A row somehow live again is withdrawn as usual.
        view = await _state_view(store, f)
        if view is not None:
            return TapResult(ok=False, toast="Already down — the member's later filing reports 5% or less",
                             verdict=view[0], buttons=view[1])
    stake = await _stake_for(store, f)
    if stake is None:
        return TapResult(ok=False, toast="Nothing to withdraw — the row is not on file")
    if stake.get("origin") not in MACHINE_ORIGINS:
        return TapResult(ok=False, toast="A hand-kept row — edit it in the seed file")
    stopped = await _exit_stopped_view(store, f, stake)
    if stopped is not None:
        # Down already because of the member's own exit (whatever the finding's status): a stale
        # Withdraw records no owner decision and never tombstones the pair (final verify #3).
        return TapResult(ok=False, toast="Already down — the member's later filing reports 5% or less",
                         verdict=stopped[0], buttons=stopped[1])
    already = stake.get("withdrawn_at") is not None
    if not await store.withdraw_stake(str(stake["id"]), now=now):
        return TapResult(ok=False, toast="Nothing to withdraw — the row is not on file")
    fid, sid = str(f["id"]), str(stake["id"])
    linked = await store.read_findings_for_stake(sid)
    changes: Dict[str, Any] = {"status": STATUS_WITHDRAWN}
    # ONE owner decision per withdrawn stake (the breaker counts them): recorded unless a finding
    # of this stake already carries it — a row the Studio valve took down still gets its one.
    if not any(x.get("owner_decision") == DECISION_WITHDRAWN for x in linked):
        changes.update(owner_decision=DECISION_WITHDRAWN, decided_at=now)
    # The row is down: the ledger follows, best effort — a failed write here is caught up by the
    # next run's reconcile (one decision per stake), never answered as a failed Withdraw.
    await _follow_up(store, fid, changes, now, what="the withdrawn finding",
                     allowed=lambda r: r.get("status") not in TOMBSTONE_STATUSES)
    for other in linked:
        if other.get("id") != fid and other.get("status") in _LIVE_OWN_STATUSES:
            await _follow_up(store, str(other["id"]), {"status": STATUS_WITHDRAWN}, now,
                             what="another finding of the withdrawn row",
                             allowed=lambda r: r.get("status") in _LIVE_OWN_STATUSES)
    _invalidate()
    if already:
        return TapResult(ok=False, toast="Already withdrawn", verdict="Withdrawn earlier", buttons=())
    return TapResult(ok=True, toast="Withdrawn", verdict=f"Withdrawn {_hhmm(now)}", buttons=())


async def _tap_keep(store: Any, f: Mapping[str, Any], message_id: Optional[int], now: datetime) -> TapResult:
    """Keep: only a LIVE auto row, only from its own note (the message fence)."""
    status = f.get("status")
    if status in (STATUS_NEW, STATUS_STAGED, STATUS_NOTED):
        return TapResult(ok=False, toast=TOAST_NOT_LIVE)
    if status != STATUS_PUBLISHED:
        return await _decided_result(store, f, TOAST_DECIDED)
    if message_id is None or f.get("telegram_message_id") != message_id:
        return TapResult(ok=False, toast=TOAST_STALE)
    stake = await _stake_for(store, f)
    if stake is None or stake.get("origin") != ORIGIN_AUTO:
        return TapResult(ok=False, toast=TOAST_DECIDED)
    if not await store.keep_stake(str(stake["id"]), now=now):
        return TapResult(ok=False, toast=TOAST_DECIDED)
    if not await store.update_finding(str(f["id"]), expected_status=STATUS_PUBLISHED, message_id=message_id,
                                      changes={"status": STATUS_KEPT, "owner_decision": DECISION_KEPT,
                                               "decided_at": now}, now=now):
        return TapResult(ok=False, toast=TOAST_DECIDED)
    _invalidate()
    return TapResult(ok=True, toast="Kept", verdict=f"Kept {_hhmm(now)}", buttons=(VERB_WITHDRAW,))


async def _tap_dismiss(store: Any, f: Mapping[str, Any], message_id: Optional[int], now: datetime) -> TapResult:
    """Dismiss: never while publishing; a tombstone for the pair; any stake of the finding withdrawn."""
    status = f.get("status")
    if status == STATUS_PUBLISHING:
        taken = await _take_over_stale_publishing(store, f, now)
        if taken is None:
            return TapResult(ok=False, toast="Publishing in progress — try again in a minute")
        f, status = taken, taken.get("status")
    if status != STATUS_PROPOSED:
        return await _decided_result(store, f, TOAST_DECIDED)
    if message_id is None or f.get("telegram_message_id") != message_id:
        return TapResult(ok=False, toast=TOAST_STALE)
    if not await store.update_finding(str(f["id"]), expected_status=STATUS_PROPOSED, message_id=message_id,
                                      changes={"status": STATUS_DISMISSED, "owner_decision": DECISION_DISMISSED,
                                               "decided_at": now}, now=now):
        current = await store.read_finding(str(f["id"]))
        if current is not None and current.get("status") == STATUS_PUBLISHING:
            return TapResult(ok=False, toast="Publishing in progress — try again in a minute")
        return TapResult(ok=False, toast=TOAST_DECIDED)
    try:
        await store.withdraw_stakes_for_finding(str(f["id"]), now=now)
    except StakeStoreError as e:
        # The Dismiss landed (the tombstone and the owner's decision are recorded): the stake it
        # owns is unpublished anyway, and the next run's reconcile withdraws it (review B TAP-4).
        logger.warning("[tc-stakes-tap] %s %s: dismissed, but its stake could not be withdrawn yet (%s) — the "
                       "next run's reconcile does it (finding %s)", f.get("company_slug"), f.get("accession"), _err(e),
                       f.get("id"))
    return TapResult(ok=True, toast="Dismissed", verdict="Dismissed", buttons=())


async def _take_over_stale_publishing(store: Any, f: Mapping[str, Any], now: datetime) -> Optional[Dict[str, Any]]:
    """A Publish that died mid-way, wrote no row and is older than PUBLISHING_STALE_SECONDS: the
    owner's next tap rolls it back and goes on from the proposal (a db_only run leaves it to a full
    run — verifier R4 — so without this every tap would answer "still publishing" meanwhile).
    The re-read finding, or None (not stale, owns a row, or the roll-back lost a race)."""
    updated = _as_ts(f.get("updated_at"))
    if updated is None or (now - updated).total_seconds() < PUBLISHING_STALE_SECONDS:
        return None
    if await store.read_stake_by_finding(str(f["id"])) is not None:
        return None
    if not await _rollback(store, str(f["id"]), now):
        return None
    logger.info("[tc-stakes-tap] finding %s: a stale Publish that wrote no row is back to a proposal (the tap "
                "takes over)", f.get("id"))
    return await store.read_finding(str(f["id"]))


async def _rollback(store: Any, fid: str, now: datetime) -> bool:
    """A Publish tap that will not publish: back to ``proposed`` — its 14 days restarted, since
    the owner did answer (this run's or the next run's expiry must never tombstone an owner's
    Publish; review B WO-3). True when the roll-back landed."""
    if not await store.update_finding(fid, expected_status=STATUS_PUBLISHING,
                                      changes={"status": STATUS_PROPOSED, "notified_at": now}, now=now):
        logger.warning("[tc-stakes-tap] finding %s: the roll-back to proposed lost (reconcile decides)", fid)
        return False
    return True


async def _tap_publish(store: Any, club_store: Any, f: Mapping[str, Any], message_id: Optional[int],
                       now: datetime) -> TapResult:
    """Publish: CAS proposed → publishing (message + fact_sha fences) → gates 3 and 5 for today →
    the row (adopt the finding's own unpublished auto row, else INSERT origin proposal; an UPDATE
    proposal replaces its target in place) → link → flip → published → invalidate."""
    status, fid = f.get("status"), str(f["id"])
    if status == STATUS_PUBLISHING:
        taken = await _take_over_stale_publishing(store, f, now)
        if taken is None:
            return TapResult(ok=False, toast=TOAST_PUBLISHING)
        f, status = taken, taken.get("status")
    if status in (STATUS_PUBLISHED, STATUS_KEPT):
        return await _decided_result(store, f, "Already published")
    if status != STATUS_PROPOSED:
        return await _decided_result(store, f, TOAST_DECIDED)
    if message_id is None or f.get("telegram_message_id") != message_id:
        return TapResult(ok=False, toast=TOAST_STALE)
    pr = f.get("proposed_row")
    sha = f.get("fact_sha")
    if not _is_stake_row(pr) or not isinstance(sha, str):
        return TapResult(ok=False, toast="Cannot publish — the proposal has no row on file")
    if not await store.update_finding(fid, expected_status=STATUS_PROPOSED, message_id=message_id, fact_sha=sha,
                                      changes={"status": STATUS_PUBLISHING}, now=now):
        current = await store.read_finding(fid)
        if current is not None and current.get("status") == STATUS_PUBLISHING:
            return TapResult(ok=False, toast=TOAST_PUBLISHING)
        if current is not None and current.get("status") == STATUS_PROPOSED:
            # Still open, but its message or its facts moved under this tap: never publish facts
            # the owner was not shown.
            return TapResult(ok=False, toast=TOAST_CHANGED)
        return TapResult(ok=False, toast=TOAST_DECIDED)
    try:
        return await _publish_claimed(store, club_store, f, pr, now)
    except asyncio.CancelledError:
        raise
    except Exception as e:  # noqa: BLE001 — answered by what the ledger now holds, never the crash toast
        return await _publish_failed(store, f, now, e)


async def _publish_failed(store: Any, f: Mapping[str, Any], now: datetime, error: BaseException) -> TapResult:
    """An error after the Publish tap's proposed → publishing claim (review B WO-3 / TAP-3). A
    finding that owns NO stake row (re-read) wrote nothing: back to ``proposed`` with its 14 days
    restarted — the owner may tap again. One that owns a row, or whose row cannot be read, is
    left ``publishing``: the next run's reconcile finishes the publish (or rolls it back) — never
    a blanket roll-back, which could leave a live row under a ``proposed`` finding."""
    fid = str(f["id"])
    if isinstance(error, StakeStoreError):
        logger.warning("[tc-stakes-tap] %s %s: the Publish failed after its claim (%s) — finding %s",
                       f.get("company_slug"), f.get("accession"), _err(error), fid)
    else:
        logger.error("[tc-stakes-tap] %s %s: the Publish raised after its claim (%s) — finding %s",
                     f.get("company_slug"), f.get("accession"), _err(error), fid, exc_info=error)
    try:
        owned = await store.read_stake_by_finding(fid)
        if owned is None and await _rollback(store, fid, now):
            return TapResult(ok=False, toast=TOAST_TRY_AGAIN)
    except StakeStoreError as e:
        logger.warning("[tc-stakes-tap] finding %s: after the failed Publish its row could not be read (%s) — "
                       "left to the next run's reconcile", fid, _err(e))
    return TapResult(ok=False, toast=TOAST_UNFINISHED)


async def _publish_claimed(store: Any, club_store: Any, f: Mapping[str, Any], pr: Mapping[str, Any],
                           now: datetime) -> TapResult:
    """The Publish tap after its claim (the finding is ``publishing``): gates 3 and 5 and the
    pair's exits for today → the row → link → flip → published."""
    fid = str(f["id"])
    today = now.astimezone(ET).date()
    rows = await club_store.read_companies()
    companies, raw_rows = _parse_registry(rows)
    ctx = _Ctx(today=today, companies=companies, company_rows=raw_rows, configs={})
    update = f.get("tier") == TIER_UPDATE
    target = await store.read_stake(str(f["stake_id"])) if update and f.get("stake_id") else None
    stakes = await store.read_stakes()
    findings = await store.read_findings_by_status(
        (STATUS_NEW, STATUS_PROPOSED, STATUS_PUBLISHING, *sorted(TOMBSTONE_STATUSES)))
    cik = f.get("investee_cik") or pr.get("investee_cik")
    exit_on = (_newest_exit(await store.read_findings_for_pair(str(f.get("company_slug")), str(cik)))
               if isinstance(cik, str) and cik else None)
    problem = _publish_tap_problem(f, pr, stakes=stakes, findings=findings, ctx=ctx,
                                   exclude_stakes=[str(target["id"])] if target else [], today=today,
                                   exit_on=exit_on)
    if problem:
        await _rollback(store, fid, now)
        return TapResult(ok=False, toast=_clip(f"Not published — {problem}", 180))
    if update:
        return await _publish_update(store, f, pr, target, now)
    stake = await store.read_stake_by_finding(fid)
    if stake is not None:
        if stake.get("withdrawn_at") is not None:
            await _rollback(store, fid, now)
            return TapResult(ok=False, toast="Not published — its row was withdrawn")
        if stake.get("origin") == ORIGIN_AUTO and not await store.adopt_as_proposal(str(stake["id"]), fid, now=now):
            await _rollback(store, fid, now)
            return TapResult(ok=False, toast="Not published — its row changed; tap again")
    else:
        try:
            stake = await store.insert_stake({**_stake_columns(pr), "published": False, "origin": ORIGIN_PROPOSAL,
                                              "finding_id": fid, "investee_cik": f.get("investee_cik")}, now=now)
        except StakeConflict:
            await _rollback(store, fid, now)
            return TapResult(ok=False, toast="Not published — another row already has this name and kind")
    sid = str(stake["id"])
    if not await store.update_finding(fid, expected_status=STATUS_PUBLISHING, changes={"stake_id": sid}, now=now):
        return TapResult(ok=False, toast=TOAST_DECIDED)
    if not await store.publish_stake(sid, fid, now=now):
        current = await store.read_stake(sid)
        if current is not None and current.get("withdrawn_at") is not None:
            await store.update_finding(fid, expected_status=STATUS_PUBLISHING, changes={"status": STATUS_WITHDRAWN},
                                       now=now)
            return TapResult(ok=False, toast="Not published — the row was withdrawn meanwhile")
        return TapResult(ok=False, toast=TOAST_PUBLISHING)
    _invalidate()
    await _follow_up(store, fid, {"status": STATUS_PUBLISHED, "owner_decision": DECISION_PUBLISHED, "decided_at": now,
                                  "published_at": now},
                     now, what="the published proposal", allowed=lambda r: r.get("status") == STATUS_PUBLISHING)
    return TapResult(ok=True, toast="Published", verdict=f"Published {_hhmm(now)}", buttons=(VERB_WITHDRAW,))


async def _publish_update(store: Any, f: Mapping[str, Any], pr: Mapping[str, Any], target: Optional[Mapping[str, Any]],
                          now: datetime) -> TapResult:
    """An UPDATE proposal: the target row replaced in place (fenced on its updated_at as read and
    withdrawn_at NULL), kept live when it was, flipped when it was not."""
    fid = str(f["id"])
    if target is None:
        await _rollback(store, fid, now)
        return TapResult(ok=False, toast="Not published — the row it updates is gone")
    if target.get("withdrawn_at") is not None or target.get("origin") not in MACHINE_ORIGINS:
        await _rollback(store, fid, now)
        return TapResult(ok=False, toast="Not published — the row it updates was withdrawn or handed over")
    row = {**_stake_columns(pr), "published": target.get("published") is True, "investee_cik": f.get("investee_cik")}
    try:
        replaced = await store.replace_stake(str(target["id"]), expected_updated_at=target.get("updated_at"), row=row,
                                             finding_id=fid, now=now)
    except StakeConflict:
        await _rollback(store, fid, now)
        return TapResult(ok=False, toast="Not published — another row already has this name and kind")
    if not replaced:
        await _rollback(store, fid, now)
        return TapResult(ok=False, toast="Not published — the live row changed; tap Publish again")
    old_finding = target.get("finding_id")
    if target.get("published") is not True and not await store.publish_stake(str(target["id"]), fid, now=now):
        return TapResult(ok=False, toast=TOAST_PUBLISHING)
    _invalidate()
    await _follow_up(store, fid, {"status": STATUS_PUBLISHED, "owner_decision": DECISION_PUBLISHED, "decided_at": now,
                                  "published_at": now},
                     now, what="the published update", allowed=lambda r: r.get("status") == STATUS_PUBLISHING)
    if old_finding and old_finding != fid:
        await _follow_up(store, str(old_finding), {"status": STATUS_SUPERSEDED, "notified_at": now}, now,
                         what="the replaced row's finding", allowed=lambda r: r.get("status") in _LIVE_OWN_STATUSES)
    return TapResult(ok=True, toast="Published — the row is updated",
                     verdict=f"Published (row updated) {_hhmm(now)}", buttons=(VERB_WITHDRAW,))


__all__ = [
    "FILERS_PATH", "RUN_DEADLINE_SECONDS", "MAX_EFTS_PAGES_PER_QUERY", "MAX_SEC_REQUESTS_PER_RUN",
    "MAX_DOCUMENTS_PER_RUN", "MAX_LOUD_MESSAGES_PER_RUN", "AUTO_MAX_PER_DAY", "AUTO_MAX_PER_WEEK",
    "BREAKER_WITHDRAWALS", "AS_OF_MAX_AGE_DAYS", "DIGEST_DEDUPE_DAYS", "DIGEST_MAX_LINES", "DIGEST_MAX_UNITS",
    "PUBLISHING_STALE_SECONDS", "SORT_ORDER", "SCHEDULE_FORMS", "LEGACY_SCHEDULE_FORMS", "EFTS_ROOT_FORMS",
    "EFTS_PHRASES", "DELISTING_FORMS", "NullNotifier", "ClassifyResult", "run_stakes", "apply_owner_decision",
    "classify_only", "efts_query", "echo_problem",
]
