"""Shared vocabulary for the Trillion Club stake finder — pure, stdlib only.

The stake finder (``stake_finder``) reads SEC filings and turns a member's newly disclosed
stake into one of four outcomes: an AUTO row (a member's own plain-share Schedule 13D/13G),
a one-tap PROPOSAL the owner publishes or dismisses in Telegram, a HEADS_UP line in the daily
digest, or a SKIPPED record. Every finding is one row of ``trillion_club_stake_findings``
(migration 205); every stake row it creates carries ``origin`` 'auto' or 'proposal'.

Every tuple below is pinned to a CHECK constraint of
``backend/database/migrations/205_trillion_club_stake_finder.sql`` by a parity test, so a value
added on one side only fails the build. Nothing here does I/O.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Protocol, Sequence, Tuple

# ── trillion_club_stakes.origin ──────────────────────────────────────────────────────────

ORIGIN_SEED = "seed"          # hand-kept: written by scripts/seed_trillion_club.py
ORIGIN_AUTO = "auto"          # published by the finder with no owner tap (tier 13dg only)
ORIGIN_PROPOSAL = "proposal"  # built by the finder, published by the owner's Publish tap
ORIGINS: Tuple[str, ...] = (ORIGIN_SEED, ORIGIN_AUTO, ORIGIN_PROPOSAL)
MACHINE_ORIGINS: Tuple[str, ...] = (ORIGIN_AUTO, ORIGIN_PROPOSAL)

# ── trillion_club_stake_findings.source ──────────────────────────────────────────────────

SOURCE_SCH13 = "sch13"  # a member's own Schedule 13D/13G (structured XML), via submissions
SOURCE_EFTS = "efts"    # another company's filing found through EDGAR full-text search
FINDING_SOURCES: Tuple[str, ...] = (SOURCE_SCH13, SOURCE_EFTS)

# ── .outcome ─────────────────────────────────────────────────────────────────────────────

OUTCOME_AUTO = "auto"
OUTCOME_PROPOSAL = "proposal"
OUTCOME_HEADS_UP = "heads_up"
OUTCOME_SKIPPED = "skipped"
FINDING_OUTCOMES: Tuple[str, ...] = (
    OUTCOME_AUTO, OUTCOME_PROPOSAL, OUTCOME_HEADS_UP, OUTCOME_SKIPPED,
)

# ── .tier (which evidence class produced the finding) ────────────────────────────────────

TIER_13DG = "13dg"                    # S1: a member's own plain-share 13D/13G
TIER_SEC_CLOSING = "sec_closing"      # T1: an investee 8-K reporting a CLOSED sale to the member
TIER_SEC_AGREEMENT = "sec_agreement"  # T2: a signed, not-yet-closed purchase agreement
TIER_IOI = "ioi"                      # T3: a non-binding indication of interest (never a row)
TIER_DIRECTED = "directed_issuance"   # T4: a foreign issuer's 6-K directed issuance
TIER_INSTRUMENT = "instrument"        # preferred / warrant / convertible / note / SAFE / units
TIER_UPDATE = "update"                # a newer filing for a pair that already has a row
TIER_OTHER = "other"
FINDING_TIERS: Tuple[str, ...] = (
    TIER_13DG, TIER_SEC_CLOSING, TIER_SEC_AGREEMENT, TIER_IOI, TIER_DIRECTED,
    TIER_INSTRUMENT, TIER_UPDATE, TIER_OTHER,
)

#: Tokens ``TRILLION_CLUB_STAKE_AUTO_TIERS`` may enable in this build. ``sec_closing`` is the
#: owner-approved follow-up (after a reviewed trial); until it ships it is refused with an
#: ERROR, exactly like an unknown token, so the switch cannot enable an untested path.
AUTO_TIER_TOKENS: Tuple[str, ...] = (TIER_13DG,)
DEFERRED_TIER_TOKENS: Tuple[str, ...] = (TIER_SEC_CLOSING,)

# ── .status (the finding's lifecycle) ────────────────────────────────────────────────────

STATUS_NEW = "new"                # write-ahead: recorded, nothing else done yet
STATUS_STAGED = "staged"          # auto: the stake row exists, unpublished; note not yet sent
STATUS_NOTED = "noted"            # auto: the Withdraw/Keep note was delivered; not yet live
STATUS_PUBLISHED = "published"    # live (auto, or a proposal the owner published)
STATUS_KEPT = "kept"              # auto row the owner tapped Keep on
STATUS_WITHDRAWN = "withdrawn"    # the owner withdrew it (or deleted it in Studio): tombstone
STATUS_PROPOSED = "proposed"      # proposal note delivered, waiting for Publish / Dismiss
STATUS_PUBLISHING = "publishing"  # Publish tap in flight (reconcile finishes or rolls back)
STATUS_DISMISSED = "dismissed"    # the owner dismissed the proposal: tombstone
STATUS_EXPIRED = "expired"        # proposal not answered in PROPOSAL_TTL_DAYS: tombstone
STATUS_SUPERSEDED = "superseded"  # replaced by a newer finding for the same pair
STATUS_CLOSED = "closed"          # auto row unpublished by the lifecycle (an exit filing)
STATUS_REPORTED = "reported"      # heads-up line already sent in a digest
STATUS_SKIPPED = "skipped"        # recorded and counted only
STATUS_ERROR = "error"            # transient failure; retried while attempts < MAX_ATTEMPTS
FINDING_STATUSES: Tuple[str, ...] = (
    STATUS_NEW, STATUS_STAGED, STATUS_NOTED, STATUS_PUBLISHED, STATUS_KEPT, STATUS_WITHDRAWN,
    STATUS_PROPOSED, STATUS_PUBLISHING, STATUS_DISMISSED, STATUS_EXPIRED, STATUS_SUPERSEDED,
    STATUS_CLOSED, STATUS_REPORTED, STATUS_SKIPPED, STATUS_ERROR,
)
#: A (slug, accession) with a finding in one of these is never processed again.
TERMINAL_STATUSES = frozenset({
    STATUS_PUBLISHED, STATUS_KEPT, STATUS_WITHDRAWN, STATUS_DISMISSED, STATUS_EXPIRED,
    STATUS_SUPERSEDED, STATUS_CLOSED, STATUS_REPORTED, STATUS_SKIPPED,
})
#: Statuses that block a NEW row for the same (member, investee) pair: the owner said no.
TOMBSTONE_STATUSES = frozenset({STATUS_WITHDRAWN, STATUS_DISMISSED, STATUS_EXPIRED})
#: An ERROR finding is retried until it has this many attempts, then recorded as SKIPPED.
MAX_ATTEMPTS = 3
PROPOSAL_TTL_DAYS = 14

# ── .owner_decision ──────────────────────────────────────────────────────────────────────

DECISION_KEPT = "kept"
DECISION_WITHDRAWN = "withdrawn"
DECISION_PUBLISHED = "published"
DECISION_DISMISSED = "dismissed"
DECISION_DELETED_IN_STUDIO = "deleted_in_studio"
OWNER_DECISIONS: Tuple[str, ...] = (
    DECISION_KEPT, DECISION_WITHDRAWN, DECISION_PUBLISHED, DECISION_DISMISSED,
    DECISION_DELETED_IN_STUDIO,
)

# ── .reason (closed set; the digest text for each lives here too) ────────────────────────

REASONS: Dict[str, str] = {
    # S1 (a member's own 13D/13G)
    "plain_13dg": "the member's own 13D/13G on plain shares",
    "about_member": "a filing ABOUT the member by someone else",
    "legacy_schedule": "an old-format (HTML) 13D/13G — not read automatically",
    "schedule_unparseable": "the 13D/13G XML could not be read with confidence",
    "member_line_missing": "no reporting-person line matches the member",
    "member_lines_disagree": "the member's reporting-person lines report different holdings",
    "reporting_type": "the reporting person's type is not an operating company",
    "group_member": "filed as part of a group with outside holders",
    "shared_power": "the member shares voting or investment power with a holder outside its own chain",
    "no_dispositive_power": "the member reports no power to sell all of the shares (voting agreements, or not stated)",
    "ownership_disclaimed": "the member disclaims beneficial ownership of the shares it reports",
    "derivative_position": "the position includes warrants, options or convertibles",
    "class_unmapped": "the share class is not plain common/ordinary/Class A stock",
    "percent_above_50": "above 50% — likely a controlled company",
    "exit_filing": "the filing reports 5% or less (an exit)",
    "cusip_invalid": "the CUSIP is missing, all zeros or fails its check digit",
    "no_us_listing": "no U.S. exchange listing in SEC data — check before Publish",
    "ambiguous_listing": "more than one possible U.S. ticker",
    # S2 (EDGAR full-text search) and its templates
    "t1_closing": "a filing reports a completed sale to the member",
    "t2_agreement": "a signed purchase agreement that had not closed",
    "t3_indication": "a non-binding indication of interest",
    "t4_directed": "a directed share issuance (foreign issuer)",
    "instrument": "preferred stock, a warrant, a convertible, a note, a SAFE or units",
    "member_filed": "the member's own filing names a stake",
    "multi_cik": "a filing with several registrants",
    "exhibit_skipped": "an indenture or warrant exhibit that does not name the member as a party",
    "commercial_only": "the member appears only as a commercial counterparty",
    "co_investor_only": "the member appears only as another investor in the filer",
    "no_template": "names the member near deal wording, but no rule matched",
    "too_large": "the document is too large to read",
    "s2_name_mismatch": "the company name in the document does not match SEC's",
    # gates
    "name_unusable": "the company name could not be used as written",
    "copy_rule": "the name or title trips the section's copy rules",
    "person_name": "a person's name may appear",
    "dates_invalid": "the dates are inconsistent or in the future",
    "too_old": "the event is more than a year old",
    "existing_row": "a newer filing for a pair that already has a row",
    "possible_match": "may be the same company as an existing row",
    "tombstone": "the owner already withdrew or dismissed this pair",
    "open_proposal": "added to the open proposal for this pair",
    "validator": "the row fails the stake validators",
    "volume_cap": "over the daily or weekly auto-publish cap",
    "breaker": "auto-publish is paused after owner withdrawals",
    "tier_off": "this evidence tier is not enabled for auto-publish",
    "before_auto_since": "filed before the auto-publish start date",
    "trial": "trial — would auto-publish once the 8-K closing tier is enabled",
    "not_enabled": "the stake finder is not enabled for this member",
    "fetch_failed": "the filing could not be fetched",
    # lifecycle
    "update": "a newer filing changes a row on file",
    "exit": "the member reported 5% or less",
    "deleted_in_studio": "the row was deleted in Studio",
    "recheck_negative": "the Monday re-check found a problem",
}
REASON_CODES: Tuple[str, ...] = tuple(REASONS)

# ── Telegram callback grammar (marketing review bot; letter y = Learn, z = this) ──────────

CALLBACK_PREFIX = "z:"
VERB_WITHDRAW = "w"
VERB_KEEP = "k"
VERB_PUBLISH = "p"
VERB_DISMISS = "d"
CALLBACK_VERBS: Tuple[str, ...] = (VERB_WITHDRAW, VERB_KEEP, VERB_PUBLISH, VERB_DISMISS)

# ── The reviewed member file (backend/data/trillion_club_filers.json) ────────────────────

# Always used with ``fullmatch``: ``$`` would also accept a trailing newline ("0001045810\n"),
# a CIK that never equals SEC's and silently disables the about-member check.
_CIK_RE = re.compile(r"[0-9]{10}")
_SLUG_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,39}")
#: Longest S2 alias: EFTS refuses a query over 1,000 characters, and eight phrases are added.
ALIAS_MAX_CHARS = 80
_FILE_KEYS = frozenset({"version", "reviewed_on", "note", "members"})
_MEMBER_KEYS = frozenset({"slug", "source_name", "names", "s1_filers", "s2_aliases", "intermediates"})
_FILER_KEYS = frozenset({"cik", "role", "names", "types"})
FILER_ROLES: Tuple[str, ...] = ("member", "affiliate")
#: Reporting-person types an auto row may come from. OO (an LLC) only for a listed affiliate.
AUTO_REPORTING_TYPES: Tuple[str, ...] = ("CO",)
AFFILIATE_EXTRA_TYPES: Tuple[str, ...] = ("OO",)


@dataclass(frozen=True)
class FilerEntry:
    """One CIK whose OWN-ACCOUNT Schedule 13D/13G filings belong to a member."""

    cik: str                      # 10 digits
    role: str                     # "member" | "affiliate"
    names: Tuple[str, ...]        # the reporting-person names it files under
    types: Tuple[str, ...]        # reporting-person types accepted for an auto row


@dataclass(frozen=True)
class MemberConfig:
    """The reviewed stake-finder settings of one club member."""

    slug: str
    source_name: str              # seed-style source title prefix: "NVIDIA", "Lilly"
    names: Tuple[str, ...]        # every name the member's own line may carry
    s1_filers: Tuple[FilerEntry, ...]
    s2_aliases: Tuple[str, ...]   # full-text-search aliases (legal names, never bare words)
    #: Reviewed holding companies INSIDE the member's own chain that co-report its block on a
    #: joint Schedule 13D/13G (Alphabet: "XXVI Holdings Inc." between Google LLC and Alphabet
    #: Inc.). Never searched, never the member's own line; their lines must report the same
    #: block, and their shared power is not "shared with an outside holder".
    intermediates: Tuple[str, ...] = ()

    @property
    def filer_ciks(self) -> Tuple[str, ...]:
        return tuple(f.cik for f in self.s1_filers)


def _str_tuple(value: Any, what: str, *, allow_empty: bool) -> Tuple[str, ...]:
    if not isinstance(value, list) or (not value and not allow_empty):
        raise ValueError(f"{what} must be a {'possibly empty ' if allow_empty else ''}list")
    out: List[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip() or item != item.strip():
            raise ValueError(f"{what}: {item!r} is not a trimmed, non-empty string")
        if not item.isprintable():
            raise ValueError(f"{what}: {item!r} has an unprintable character")
        if item in out:
            raise ValueError(f"{what}: {item!r} is listed twice")
        out.append(item)
    return tuple(out)


def _refuse_unknown_keys(obj: Mapping[str, Any], allowed: frozenset, what: str) -> None:
    """A misspelt or invented key would read as configured while doing nothing."""
    unknown = sorted(str(k) for k in obj if k not in allowed)
    if unknown:
        raise ValueError(f"{what}: unknown key(s) {unknown}")


def parse_member_configs(data: Mapping[str, Any]) -> Dict[str, MemberConfig]:
    """Validate the parsed filers file and return ``{slug: MemberConfig}``.

    Raises ``ValueError`` naming the first problem: the file is reviewed data, so a bad
    entry stops the finder (one ERROR) rather than silently searching the wrong names.
    """
    version = data.get("version") if isinstance(data, Mapping) else None
    if type(version) is not int or version != 1:  # True == 1 and 1.0 == 1: refuse both
        raise ValueError("filers file: expected an object with version 1")
    _refuse_unknown_keys(data, _FILE_KEYS, "filers file")
    members = data.get("members")
    if not isinstance(members, list) or not members:
        raise ValueError("filers file: 'members' must be a non-empty list")
    out: Dict[str, MemberConfig] = {}
    seen_ciks: Dict[str, str] = {}
    for raw in members:
        if not isinstance(raw, Mapping):
            raise ValueError("filers file: every member must be an object")
        slug = raw.get("slug")
        if not isinstance(slug, str) or not _SLUG_RE.fullmatch(slug):
            raise ValueError(f"filers file: bad slug {slug!r}")
        _refuse_unknown_keys(raw, _MEMBER_KEYS, f"filers file: {slug}")
        if slug in out:
            raise ValueError(f"filers file: slug {slug!r} listed twice")
        source_name = raw.get("source_name")
        if not isinstance(source_name, str) or not source_name.strip() or len(source_name) > 40:
            raise ValueError(f"filers file: {slug}: bad source_name {source_name!r}")
        names = _str_tuple(raw.get("names"), f"{slug}.names", allow_empty=False)
        aliases = _str_tuple(raw.get("s2_aliases"), f"{slug}.s2_aliases", allow_empty=True)
        for alias in aliases:
            if len(alias) < 6 or " " not in alias.strip():
                # A bare word ("Apple", "Meta") matches thousands of unrelated filings.
                raise ValueError(f"filers file: {slug}: alias {alias!r} must be a multi-word legal name")
            if any(ch in alias for ch in '"()'):
                raise ValueError(f"filers file: {slug}: alias {alias!r} has a quote or parenthesis")
            if not alias.isascii() or len(alias) > ALIAS_MAX_CHARS:
                # The full-text search refuses a non-ASCII query, so every search would fail.
                raise ValueError(
                    f"filers file: {slug}: alias {alias!r} must be ASCII and at most {ALIAS_MAX_CHARS} characters"
                )
        filers_raw = raw.get("s1_filers")
        if not isinstance(filers_raw, list):
            raise ValueError(f"filers file: {slug}: s1_filers must be a list")
        filers: List[FilerEntry] = []
        for f in filers_raw:
            if not isinstance(f, Mapping):
                raise ValueError(f"filers file: {slug}: every filer must be an object")
            _refuse_unknown_keys(f, _FILER_KEYS, f"filers file: {slug}: filer")
            cik = f.get("cik")
            if not isinstance(cik, str) or not _CIK_RE.fullmatch(cik) or int(cik) == 0:
                raise ValueError(f"filers file: {slug}: bad filer CIK {cik!r}")
            if cik in seen_ciks:
                raise ValueError(f"filers file: CIK {cik} listed for {seen_ciks[cik]} and {slug}")
            seen_ciks[cik] = slug
            role = f.get("role")
            if role not in FILER_ROLES:
                raise ValueError(f"filers file: {slug}: filer {cik} has bad role {role!r}")
            fnames = _str_tuple(f.get("names"), f"{slug}.{cik}.names", allow_empty=False)
            types = _str_tuple(f.get("types"), f"{slug}.{cik}.types", allow_empty=False)
            allowed = set(AUTO_REPORTING_TYPES) | (set(AFFILIATE_EXTRA_TYPES) if role == "affiliate" else set())
            for t in types:
                if t not in allowed:
                    raise ValueError(f"filers file: {slug}: filer {cik} type {t!r} is not allowed for a {role}")
            if role == "member" and not set(fnames) <= set(names):
                # The cover-line matcher reads MemberConfig.names: a member-filer name missing
                # there would never match the member's own line.
                raise ValueError(f"filers file: {slug}: filer {cik} names must all appear in {slug}.names")
            filers.append(FilerEntry(cik=cik, role=role, names=fnames, types=types))
        intermediates = _str_tuple(raw.get("intermediates", []), f"{slug}.intermediates", allow_empty=True)
        filer_names = {n for f in filers for n in f.names}
        for inter in intermediates:
            if not inter.isascii() or len(inter) > 120:
                raise ValueError(f"filers file: {slug}: intermediate {inter!r} must be ASCII and at most 120 characters")
            if inter in names or inter in filer_names:
                raise ValueError(f"filers file: {slug}: intermediate {inter!r} is already a member or filer name")
        if intermediates and not filers:
            raise ValueError(f"filers file: {slug}: intermediates need s1_filers (they only matter on its 13D/13G)")
        if not filers and not aliases:
            raise ValueError(f"filers file: {slug}: neither s1_filers nor s2_aliases — leave it out instead")
        if filers and not any(f.role == "member" for f in filers):
            raise ValueError(f"filers file: {slug}: s1_filers needs the member's own CIK")
        out[slug] = MemberConfig(
            slug=slug, source_name=source_name.strip(), names=names,
            s1_filers=tuple(filers), s2_aliases=aliases, intermediates=intermediates,
        )
    return out


def load_member_configs(path: str) -> Dict[str, MemberConfig]:
    """Read and validate the filers file at ``path`` (``ValueError`` / ``OSError`` on failure)."""
    with open(path, encoding="utf-8") as fh:
        return parse_member_configs(json.load(fh))


def callback_data(verb: str, finding_id: str) -> str:
    """``z:<verb>:<finding uuid>`` — 40 bytes, inside Telegram's 64-byte limit."""
    if verb not in CALLBACK_VERBS:
        raise ValueError(f"unknown stake callback verb {verb!r}")
    data = f"{CALLBACK_PREFIX}{verb}:{finding_id}"
    if parse_callback_data(data) != (verb, finding_id):
        raise ValueError(f"stake callback for {finding_id!r} does not round-trip")
    return data


_CALLBACK_RE = re.compile(
    r"z:([" + "".join(CALLBACK_VERBS) + r"]):"
    r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})"
)


def parse_callback_data(data: Any) -> Optional[Tuple[str, str]]:
    """``(verb, finding_id)`` for a well-formed stake callback, else None."""
    if not isinstance(data, str) or len(data.encode("utf-8")) > 64:
        return None
    m = _CALLBACK_RE.fullmatch(data)
    return (m.group(1), m.group(2)) if m else None


# ── The finder ⇄ Telegram seam (stake_finder calls it; stake_review implements it) ───────

#: Longest toast Telegram's answerCallbackQuery shows without truncating.
TOAST_MAX_CHARS = 180


@dataclass(frozen=True)
class TapResult:
    """What one owner tap did — ``stake_finder.apply_owner_decision``'s answer, rendered by
    ``stake_review`` (the toast always; the message edit only when ``verdict`` is set).

    ``buttons`` are the verbs the edited message offers afterwards (``("w",)`` after a Publish
    so the owner can still Withdraw; ``()`` removes every button). Plain text only.
    """

    ok: bool
    toast: str
    verdict: Optional[str] = None
    buttons: Tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.toast, str) or not self.toast.strip() or len(self.toast) > TOAST_MAX_CHARS:
            raise ValueError(f"TapResult.toast must be 1..{TOAST_MAX_CHARS} characters, got {self.toast!r}")
        if self.verdict is not None and (not isinstance(self.verdict, str) or not self.verdict.strip()):
            raise ValueError("TapResult.verdict must be None or a non-empty string")
        if any(b not in CALLBACK_VERBS for b in self.buttons) or len(set(self.buttons)) != len(self.buttons):
            raise ValueError(f"TapResult.buttons must be distinct callback verbs, got {self.buttons!r}")
        if self.buttons and self.verdict is None:
            raise ValueError("TapResult.buttons needs a verdict (buttons change only with an edit)")


class StakeNotifier(Protocol):
    """The Telegram side of the finder. Every method returns its result and never raises
    (except ``asyncio.CancelledError``): a send that failed returns None / False, logged.

    ``finding`` is the ledger row as stored, plus ``member_name`` (the member's display
    name). ``row`` is the stake row to show (the proposed row, or the live stake row).
    Notes and proposals are LOUD; the digest is silent. Message ids are Telegram's.
    """

    def available(self) -> bool:
        """The review bot is configured (``review_service.is_configured()``)."""

    async def send_auto_note(self, finding: Mapping[str, Any], row: Mapping[str, Any]) -> Optional[int]:
        """The 'going live automatically' note with Withdraw + Keep. The id, or None."""

    async def send_proposal(
        self, finding: Mapping[str, Any], row: Mapping[str, Any], *, trial: bool, evidence: Sequence[str],
    ) -> Optional[int]:
        """A one-tap proposal with Publish + Dismiss (``trial`` marks an 8-K closing that would
        auto-publish once that tier ships). For an UPDATE proposal the finding may carry
        ``target_live`` (bool): False means the row it replaces is not live now, and the text must
        not say Publish "replaces the live row". The id, or None."""

    async def edit_proposal(
        self, finding: Mapping[str, Any], row: Mapping[str, Any], *, message_id: int, trial: bool,
        evidence: Sequence[str],
    ) -> bool:
        """Re-render an open proposal in place (new evidence appended; ``target_live`` as for
        send_proposal). Never sends a new message."""

    async def send_lifecycle_note(
        self, finding: Mapping[str, Any], row: Optional[Mapping[str, Any]], *, text: str, offer_withdraw: bool,
    ) -> Optional[int]:
        """A loud lifecycle note (exit unpublished, re-check negative, …); ``text`` is the
        finder's plain-text body; ``offer_withdraw`` adds a Withdraw button for ``finding``."""

    async def mark_message(self, *, message_id: int, finding_id: str, verdict: str, buttons: Sequence[str]) -> bool:
        """Append ``verdict`` to an earlier message and replace its buttons (``()`` = none)."""

    async def send_digest(self, lines: Sequence[str]) -> bool:
        """The silent daily digest, split under Telegram's 4,096-character cap. True when every
        part was delivered."""
