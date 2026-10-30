"""Shared types and constants of the Wiser weekly content engine.

Pure (stdlib only): every other `learn_weekly` module imports from here, and nothing here imports
FMP, Supabase or Gemini. The plan this implements is the owner-approved 2026-10-22 plan
"Wiser weekly content engine" (Friday Feature Deep Dive + category pieces).

Licence boundary, restated because every type below is shaped by it: a figure that is FROZEN into
an article's text or narration comes from SEC EDGAR (public domain) or the human-verified case
library — never from FMP. FMP data only RANKS topics (`TickerSignal` carries counts and booleans,
never FMP strings), and only `signals.py` may import the FMP client
(`tests/test_learn_weekly_fmp_boundary.py`).
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

# ── Piece kinds and Money Moves categories ────────────────────────────────────────────────────

KIND_DEEP_DIVE = "deep_dive"
KIND_SLOT = "slot"
KINDS: Tuple[str, ...] = (KIND_DEEP_DIVE, KIND_SLOT)

# The Postgres enum `money_move_category` and the iOS `MoneyMoveArticleDTO.category(from:)` map.
# Anything else is filed under Blueprints on iOS and fails the enum column — never emit it.
CATEGORIES: Tuple[str, ...] = ("blueprints", "valueTraps", "battles")

# ── Ledger (`learn_weekly_pieces.status`) ─────────────────────────────────────────────────────
#
# planned → topic_pending → building → drafted → media_pending → ready_for_review → in_review
#   → approved → publishing → published (→ unpublished)
# A dry-run piece ends `rehearsed` instead of publishing.

PLANNED = "planned"
TOPIC_PENDING = "topic_pending"
BUILDING = "building"
DRAFTED = "drafted"
MEDIA_PENDING = "media_pending"
READY_FOR_REVIEW = "ready_for_review"
IN_REVIEW = "in_review"
APPROVED = "approved"
PUBLISHING = "publishing"
PUBLISHED = "published"
REHEARSED = "rehearsed"
SKIPPED = "skipped"
FAILED = "failed"
OWNER_REJECTED = "owner_rejected"
EXPIRED = "expired"
UNPUBLISHED = "unpublished"

PIECE_STATUSES: Tuple[str, ...] = (
    PLANNED, TOPIC_PENDING, BUILDING, DRAFTED, MEDIA_PENDING, READY_FOR_REVIEW, IN_REVIEW,
    APPROVED, PUBLISHING, PUBLISHED, REHEARSED, SKIPPED, FAILED, OWNER_REJECTED, EXPIRED,
    UNPUBLISHED,
)
# No further automatic transition. (`published` is live, not terminal: it can be unpublished.)
TERMINAL_STATUSES = frozenset({REHEARSED, SKIPPED, FAILED, OWNER_REJECTED, EXPIRED, UNPUBLISHED})
# What the hold's end expires. NEVER `publishing`: its row may already be live (recovered by
# row existence instead) — plan §1.10.
EXPIRABLE_STATUSES = frozenset({
    PLANNED, TOPIC_PENDING, BUILDING, DRAFTED, MEDIA_PENDING, READY_FOR_REVIEW, IN_REVIEW, APPROVED,
})
# Terminal outcomes that alert the owner loudly (plan §1.9).
ALERT_STATUSES = frozenset({FAILED, SKIPPED, EXPIRED, OWNER_REJECTED})
# A rebuild (owner verb or `scripts/learn_weekly_rebuild.py`) may reopen only these.
REBUILDABLE_STATUSES = frozenset({FAILED, OWNER_REJECTED, EXPIRED, SKIPPED})

# ── Narration jobs (`learn_narration_jobs.status`) ────────────────────────────────────────────

JOB_QUEUED = "queued"
JOB_IN_PROGRESS = "in_progress"
JOB_READY = "ready"
JOB_FAILED = "failed"
JOB_CANCELLED = "cancelled"
JOB_STATUSES: Tuple[str, ...] = (JOB_QUEUED, JOB_IN_PROGRESS, JOB_READY, JOB_FAILED, JOB_CANCELLED)

ENGINE_CHATTERBOX = "chatterbox"
ENGINE_KOKORO = "kokoro"
ENGINES: Tuple[str, ...] = (ENGINE_CHATTERBOX, ENGINE_KOKORO)

# Worker-reported failures and lease re-claims are capped SEPARATELY (plan §1.8): an infra kill of a
# 2-hour render must not burn the same budget as a real synthesis failure.
MAX_JOB_WORKER_FAILURES = 2
MAX_JOB_LEASE_RECLAIMS = 4
JOB_LEASE_SECONDS = 600

#: Worker `/fail` codes judged on the FINISHED file of a deterministic render (Chatterbox seeds every
#: block with SEED + its index and encodes bitexact; the aligner runs fixed weights over those same
#: bytes): the same job renders the same file, so a retry fails the same way. On 2026-10-30 one Deep
#: Dive failed `voice_out_of_band` at 12,888 B/s, then at 12,887 on the next round — ~1.5 h of 4 vCPU
#: each. Such a code fails the job at its FIRST report (`narration.fail`), and `narration.after_job_failed`
#: queues no automatic round with the same render inputs after it (another engine may get one).
#: Every other code — an OOM kill, a timeout, an upload, the API, a bug, an asset mismatch — keeps the
#: retry path. The worker holds no copy: `test_learn_narrator_worker.py` pins every code here to a
#: `JobFailure("<code>", …)` the worker raises after the render, and to no child-process code.
DETERMINISTIC_FAIL_CODES = frozenset({"voice_out_of_band", "timings_invalid"})


def fail_code_of(error: Any) -> Optional[str]:
    """The code a job's stored `error` opens with (`narration.fail` stores "<code>: <detail>"), or
    None when it does not open with one."""
    if not isinstance(error, str):
        return None
    m = re.match(r"([a-z0-9][a-z0-9_.\-]{0,63}):", error.strip())
    return m.group(1) if m else None

# Worst-case render seconds per AUDIO second, by engine — sizes the catch-up cut-off
# (`schedule.catch_up_cutoff`). Chatterbox = the Phase-0 acceptance bar (A1: 150 min for a 10.5-min
# fixture ≈ 14.3×); Kokoro ≈ 1.5× realtime measured on Railway 4 vCPU, doubled for margin.
RENDER_FACTOR: Mapping[str, float] = {ENGINE_CHATTERBOX: 14.3, ENGINE_KOKORO: 3.0}
NARRATION_WPM = 155.0

# ── Period keys ───────────────────────────────────────────────────────────────────────────────

# ASCII digits and `fullmatch` on purpose: `\d` accepts non-ASCII digits and `$` a trailing newline,
# both of which pass Python and then fail the SQL CHECK in production (`ledger.validate_period_key`
# adds the calendar check on top).
DEEP_DIVE_KEY_RE = re.compile(r"[0-9]{4}-W[0-9]{2}:deep_dive")
SLOT_KEY_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}:slot")


def deep_dive_period_key(iso_year: int, iso_week: int) -> str:
    """`2026-W43:deep_dive` — the ISO week the Deep Dive PUBLISHES in (Friday's ISO week)."""
    return f"{int(iso_year):04d}-W{int(iso_week):02d}:{KIND_DEEP_DIVE}"


def slot_period_key(publish_date: date) -> str:
    """`2026-10-26:slot` — keyed by the ET publish date."""
    return f"{publish_date.isoformat()}:{KIND_SLOT}"


def is_period_key(value: Any) -> bool:
    return isinstance(value, str) and bool(DEEP_DIVE_KEY_RE.fullmatch(value) or SLOT_KEY_RE.fullmatch(value))


# ── Topic signals (FMP-derived numbers live ONLY here, as numbers and booleans) ───────────────


@dataclass(frozen=True)
class TickerSignal:
    """One candidate ticker's weekly ranking inputs, built by `signals.py`.

    `None` means MISSING (the fetch raised or returned `fetch_failed`) — never read it as zero. A
    saturated news count is a LOWER bound. Every field is a number, a bool or an identifier: no FMP
    text (headline, company name, description) ever enters this type.
    """

    ticker: str
    cik: Optional[str] = None                 # 10-digit zero-padded, from SEC (not FMP)
    news_week: Optional[int] = None
    news_baseline: Tuple[int, ...] = ()       # trailing weekly counts, oldest → newest
    news_saturated: bool = False
    filings_8k_week: Optional[int] = None
    filings_8k_baseline: Tuple[int, ...] = ()
    items_8k: Tuple[str, ...] = ()            # 8-K item codes filed this week ("2.02", "1.01", …)
    earnings_this_week: bool = False
    size_ok: Optional[bool] = None            # ≥ $2B market cap AND a major US exchange
    foreign_filer: Optional[bool] = None      # files 20-F/40-F (IFRS) — excluded as main subject
    excluded_reason: Optional[str] = None     # hard exclusion decided upstream (blocklist, ETF, …)


@dataclass(frozen=True)
class ThemeInput:
    slug: str
    title: str
    members: Tuple[str, ...]
    eligible: bool                            # ≥ 2 verified case-library primers for this theme
    last_covered: Optional[str] = None        # ISO date of the last Deep Dive on this theme


@dataclass(frozen=True)
class TopicCandidate:
    kind: str                                 # "theme" | "ticker"
    key: str                                  # theme slug, or the ticker
    title: str                                # theme title, or the ticker (labelled later)
    score: float
    tickers: Tuple[str, ...]                  # ranked members that carried the score
    components: Mapping[str, float] = field(default_factory=dict)
    reason: str = "ranked"                    # "ranked" | "quiet_week_fallback"

    def to_json(self) -> Dict[str, Any]:
        d = asdict(self)
        d["tickers"] = list(self.tickers)
        d["components"] = {k: round(float(v), 4) for k, v in dict(self.components).items()}
        return d


# ── Fact sheet ────────────────────────────────────────────────────────────────────────────────

ORIGIN_SEC_XBRL = "sec_xbrl"        # code-rendered sentence from an XBRL fact (accession kept)
ORIGIN_SEC_PEG = "sec_peg"          # verbatim span of an 8-K / EX-99.1 filing text
ORIGIN_LIBRARY = "library"          # a verified case-library fact
FACT_ORIGINS = (ORIGIN_SEC_XBRL, ORIGIN_SEC_PEG, ORIGIN_LIBRARY)

DEEP_DIVE_SHEET_CAP = 9000
SLOT_SHEET_CAP = 6000

#: The sentence every peg fact opens with on the sheet (`fact_sheet.date_peg_facts`): SEC's own
#: filing date, never the model's. The peg section must state the full date of what it restates
#: and grounding refuses any number the sheet does not hold; the stored spans carried no date, so
#: "On October 21, 2026" was refused as an ungrounded "21" (and was wrong: SEC says October 20)
#: and the week's piece was skipped (2026-10-23). It is its OWN sentence, and grounding reads it
#: as its own fact (`validators.build_grounding_context`), so the day and year anchor only to
#: "Form 8-K filed with the SEC", never to the span's words ("for 20 consecutive years").
PEG_DATE_PREFIX = "Form 8-K filed with the SEC on {date}. "
PEG_MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August",
              "September", "October", "November", "December")
PEG_DATE_RE = re.compile(r"\AForm 8-K filed with the SEC on (?:" + "|".join(PEG_MONTHS)
                         + r") (?:[1-9]|[12]\d|3[01]), \d{4}\. ")


def peg_date_text(day: date) -> str:
    return PEG_DATE_PREFIX.format(date=f"{PEG_MONTHS[day.month - 1]} {day.day}, {day.year}")


def split_peg_date(text: str) -> Tuple[str, str]:
    """(the code-owned date sentence, the filed span) of a dated peg text; ("", text) when the
    text does not open with one."""
    m = PEG_DATE_RE.match(text) if isinstance(text, str) else None
    return (m.group(0), text[m.end():]) if m else ("", text)


@dataclass(frozen=True)
class Fact:
    id: str                                   # "S1" (xbrl), "P1" (peg), "L:<entry>:<fid>" (library)
    text: str                                 # one sentence, exactly what the writer may restate
    origin: str
    url: Optional[str] = None
    accession: Optional[str] = None
    entry_id: Optional[str] = None


@dataclass(frozen=True)
class Subject:
    name: str                                 # display name from SEC submissions / the library
    cik: Optional[str] = None
    ticker: Optional[str] = None


@dataclass(frozen=True)
class FactSheet:
    kind: str
    topic_title: str
    theme_slug: Optional[str]
    subjects: Tuple[Subject, ...]
    facts: Tuple[Fact, ...]

    def sentences(self) -> List[str]:
        return [f.text for f in self.facts]

    def by_id(self) -> Dict[str, Fact]:
        return {f.id: f for f in self.facts}

    def render(self, ids: Optional[Sequence[str]] = None) -> str:
        """Numbered lines for a prompt: `[S1] text`. `ids` limits it to one section's slice."""
        wanted = None if ids is None else set(ids)
        return "\n".join(f"[{f.id}] {f.text}" for f in self.facts if wanted is None or f.id in wanted)

    def char_count(self) -> int:
        return sum(len(f.text) for f in self.facts)

    def to_json(self) -> Dict[str, Any]:
        return {
            "kind": self.kind,
            "topic_title": self.topic_title,
            "theme_slug": self.theme_slug,
            "subjects": [asdict(s) for s in self.subjects],
            "facts": [asdict(f) for f in self.facts],
        }

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> "FactSheet":
        return cls(
            kind=str(data["kind"]),
            topic_title=str(data.get("topic_title") or ""),
            theme_slug=data.get("theme_slug"),
            subjects=tuple(Subject(**dict(s)) for s in data.get("subjects") or ()),
            facts=tuple(Fact(**dict(f)) for f in data.get("facts") or ()),
        )

    def canonical_json(self) -> str:
        return json.dumps(self.to_json(), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


# ── Narration script (server side; the worker sees only i/text/role) ─────────────────────────

ROLE_CONTEXT = "context"    # title, subtitle, section titles
ROLE_BODY = "body"          # paragraph / subheading / callout / quote text
ROLE_ITEM = "item"          # one bulletList item
ROLES = (ROLE_CONTEXT, ROLE_BODY, ROLE_ITEM)


@dataclass(frozen=True)
class NarrationBlock:
    i: int
    text: str
    role: str
    # Where this block's sentence timings merge back into the article:
    #   ("title",) | ("subtitle",) | ("section_title", s) | ("block", s, b) | ("item", s, b, k)
    dest: Tuple[Any, ...]

    def wire(self) -> Dict[str, Any]:
        """The worker's view (internal API contract): never `dest`."""
        return {"i": self.i, "text": self.text, "role": self.role}


# ── Article shape limits (writer prompts + validators share these) ────────────────────────────

MAX_PARAGRAPH_WORDS = 75          # Chatterbox hard cap: 1,000 tokens = 40.0 s raw per block
MAX_SUBHEADING_WORDS = 8
MAX_BULLET_ITEMS = 5
MAX_BULLET_WORDS = 30
MAX_CALLOUT_WORDS = 50
DEEP_DIVE_WORDS = (1400, 1900)
SLOT_WORDS = (900, 1250)
MAX_PEG_SHARE = 0.20              # peg sections ≤ 20% of the words (≥ 80% evergreen)
SECTION_KINDS = ("peg", "history", "mechanics", "lessons")
BLOCK_TYPES = ("paragraph", "subheading", "callout", "bulletList")   # no `quote` in weekly pieces
CALLOUT_STYLES = ("highlight", "info", "warning")

AUTHOR = {"name": "Caydex Research", "title": "AI-assisted · Editor-reviewed", "isVerified": False}
# Must contain "AI-assisted": the public disclosures (support.html, terms.html) promise that label.
DISCLOSURE_TEXT = (
    "AI-assisted: this article was researched and written with AI from public filings and "
    "reviewed by Caydex before publishing. It is educational only and not investment advice."
)
