"""Competitor intel — revenue-mix-aware peer selection (Phase 2).

The TickerReport Moat & Competitors section's peer source is FMP's
`/stock-peers` augmented from `data/industry_universe.json` (Phase 1).
That deterministic path is structurally too narrow because FMP's
`industry` field reflects a company's PRIMARY classification, not its
revenue-mix overlap with another company. For Oracle, the real
competitors — Microsoft, Amazon (AWS), Salesforce, SAP, IBM, Adobe,
Google (GCP), Broadcom (post-VMware) — span 4-5 different FMP
industries.

Phase 2: ask Gemini, with Google Search grounding, to identify
competitors based on overlapping revenue mix, listed MOST DIRECT FIRST
(by the share of the focal company's revenue each one contests), and
to leave out companies that are mainly customers, suppliers or design
partners. Each row carries a `relationship` label (rows labelled as a
customer / partner / supplier are dropped here, before FMP validation,
so they cost no profile call) and a short "competes in" `segment`
label (cleaned by `_clean_segment`, ≤ `SEGMENT_MAX_CHARS`), persisted
per ticker in `competitor_intel_cache.competitor_details` (migration
186) and served by `get_competitor_details`. Validate every returned
ticker against FMP `/profile` (no unknown / fabricated symbols). Trim
to 7 only when Gemini returns more than 7, keeping the research order
— below that, take the full 4-6 list as-is (a $10B niche rival with
verifiable revenue overlap is a real competitor, regardless of mkt-cap
delta to the focal). The list is NEVER re-sorted by market cap: its
order is the directness rank the collector scores and displays.

Freshness is a VERSION MARKER, not a date floor. A cache row is
current only when its `model_version` ends in `|cip-v2` (or in
`|cip-v2-nodetails` while migration 186 is not applied — see
`_write_cache`). Any other row is STALE: re-extracted on the next
collection, and still served if that re-extraction fails
(stale-on-error). A date floor was wrong whenever the deploy landed
earlier or later than the date picked. Bump `CACHE_MARKER` when the
prompt or the validation rules change what a cached row means.

Cost: re-extraction runs on the next COLLECTION of a ticker —
including the detail-view and hourly pre-warms — and costs one
grounded call plus one FMP profile per suggestion. A failed extraction
— any failure, including an unexpected exception — leaves a 30-minute
negative entry in the in-memory tier (`_NEGATIVE_TTL_SECONDS`; only
`_READ_FAILED_NEGATIVE_TTL_SECONDS` when the cache read failed too, since
a servable row may exist), so a pre-warm loop cannot re-bill it on
every pass. The quarterly batch (force_refresh) re-extracts the top
500 with whatever code is live, and counts a ticker applied only when
it was extracted fresh (never a stale fallback it joined in flight).

Architecture mirrors industry_override_service (Phase B of the dossier
pipeline):
  * Same quarterly schedule (first Sunday Jan/Apr/Jul/Oct 02:00 UTC),
    chained after industry_dossier_service.recompute_all() in main.py.
  * Same audit-log discipline — every Gemini extraction leaves a row
    in competitor_intel_audit, with raw response, suggested tickers,
    validated survivors, rejections, tokens, model version.
  * Same anti-fabrication guardrail — no ticker survives without an
    FMP profile resolution + positive mktCap.

In addition (because Phase 2 has an on-demand per-request path that
Phase B doesn't): two-tier cache (in-memory dict + Supabase
competitor_intel_cache) + `_inflight` dedup so a thundering herd of
concurrent ticker-report requests doesn't trigger duplicate Gemini
calls for the same ticker.

Cache TTL ≈ 100 days — competitors are stable quarter-over-quarter,
and the quarterly batch on the first Sunday of each quarter overwrites
the row before it expires.

Kill switch: `settings.COMPETITOR_INTEL_AI_ENABLED = False` skips the
Gemini call. A current cached row is still served; a stale one is served
as-is (it cannot be re-extracted); with nothing cached, callers see None
→ fall back to the Phase 1 deterministic peer-augmentation path.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import unicodedata
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, NamedTuple, Optional, Tuple

from app.utils.postgrest_paging import fetch_all_rows
from app.utils.inflight import fail_shared_future
from app.utils.supabase_errors import is_unknown_column_error
from app.config import settings
from app.database import get_supabase
from app.integrations.fmp import get_fmp_client
from app.integrations.gemini import get_gemini_client

logger = logging.getLogger(__name__)


# ── Configuration constants ────────────────────────────────────────────

_COMPETITOR_MAX_N = 7              # iOS renders up to 7 competitor rows
_CACHE_TTL_DAYS = 100              # one quarter + ~7 day safety margin
_IN_MEM_TTL_SECONDS = 300          # 5-minute in-memory dedup tier
_NEGATIVE_TTL_SECONDS = 1800       # a failed extraction is not retried for 30 min (per process)
# … but only for 60 s when the cache READ failed too: a servable row may exist, and a
# 30-min entry would hide it long after the database recovers.
_READ_FAILED_NEGATIVE_TTL_SECONDS = 60
_BATCH_TOP_N = 500                 # top-N watchlisted tickers per quarterly run
_BATCH_CONCURRENCY = 5             # concurrent Gemini calls during batch
_GEMINI_MAX_OUTPUT_TOKENS = 8192

# Longest "competes in" label kept (iOS renders it on its own line under the
# competitor name; the report PDF prints it too). Shared contract value.
SEGMENT_MAX_CHARS = 48
_SEGMENT_RAW_CAP = 400             # model text is length-capped before any regex runs


# Cache-row VERSION MARKER — the freshness key (module docstring). `_write_cache`
# stamps `model_version = f"{model}|{CACHE_MARKER}"`, or `…|{CACHE_MARKER_NODETAILS}`
# when the write fell back to tickers-only because `competitor_details` (migration
# 186) does not exist yet. Bump CACHE_MARKER (e.g. to "cip-v3") whenever the prompt
# or the validation rules change what a cached row means.
CACHE_MARKER = "cip-v2"
CACHE_MARKER_NODETAILS = "cip-v2-nodetails"


# Legacy schema floor — SUPERSEDED by CACHE_MARKER as the freshness key (2026-10-01).
# Still enforced (a row computed before it is a plain cache miss, never even a stale
# fallback), but do not bump it for a prompt change any more: a date is wrong whenever
# the deploy lands earlier or later than the date chosen. Bump CACHE_MARKER instead.
COMPETITOR_INTEL_SCHEMA_FLOOR = datetime(2026, 5, 26, 0, 0, 0, tzinfo=timezone.utc)


# ── Prompt template ────────────────────────────────────────────────────
#
# Hybrid prose+JSON format is intentional: the Gemini grounded-search
# API only populates `groundingChunks` (real source URLs) when the
# response contains text that cites those sources inline. A pure-JSON
# response would have searches run but return zero chunks — we want the
# actual URLs for the audit log + source_labels.

_RESEARCH_PROMPT = """You are a financial research analyst. Today's date is {today}. For the company below, identify its TOP 5-7 BUSINESS COMPETITORS based on overlapping revenue mix — companies that earn money from substantially the same products, services, and customer segments.

A competitor is NOT just a company in the same industry classification. Example: Oracle's real competitors include Microsoft, Amazon (because of AWS), Salesforce, SAP, IBM, Adobe, Google (because of GCP), and Broadcom (post-VMware) — even though these span 4-5 different SIC codes — because they all sell substantially-overlapping enterprise software, cloud infrastructure, or database products to overlapping customer bases.

Use CURRENT sources only: {ticker}'s latest fiscal-year 10-K (its Competition section), its last two earnings calls, and reputable coverage from the past 12 months (Reuters, Bloomberg, Morningstar, Gartner, Forrester). Do not rely on an older 10-K or older articles when newer ones exist.

COMPANY: {company_name} ({ticker})
SECTOR: {sector}
INDUSTRY: {industry}
DESCRIPTION: {description}

ORDER — list the competitors MOST DIRECT FIRST: rank them by how much of {ticker}'s revenue each one contests (the share of {ticker}'s revenue that sits in segments where that company sells a competing product). The first entry must be {ticker}'s closest rival, not simply the largest or most powerful company.

EXCLUDE companies that are mainly {ticker}'s customers, suppliers or design partners, unless they also sell a competing product to third parties. A company that buys from {ticker}, supplies it, or co-develops products with it is not a competitor on that basis alone.

In ONE paragraph, summarize which companies compete with {ticker} and on what revenue segments.

Then output JSON in a markdown code fence (mandatory, no exceptions), with the competitors in the ORDER above:

```json
{{
  "competitors": [
    {{
      "ticker": "<US-listed ticker, uppercase, e.g. MSFT>",
      "name": "<official company name>",
      "relationship": "direct" | "partial" | "customer_partner",
      "segment": "<where it competes with {ticker}, at most 48 characters, e.g. Enterprise databases>",
      "source_citation": "<10-K / earnings call / analyst report ref>"
    }}
  ],
  "confidence": "high" | "medium" | "low"
}}
```

Rules:
- relationship: "direct" = sells substantially the same products to the same customers as {ticker}; "partial" = competes with {ticker} in one segment only; "customer_partner" = mainly a customer, supplier or design partner of {ticker} (normally left out — see EXCLUDE).
- segment: a short plain-text label of the product area where the two compete. No citation markers, no links, no markdown.
- Return 5-7 competitors. Below 5 only if the company genuinely has no peers with material revenue overlap.
- Use US-listed tickers when possible. If a competitor is non-US, use its primary listing's ticker (e.g. SAP for SAP SE, BABA for Alibaba ADR).
- The competitor must be a publicly-traded company (has a real ticker that resolves in financial databases). Do NOT list private companies or business units of larger firms.
- Do NOT include the focal company ({ticker}) in its own competitor list.
- Do NOT mention LLMs, AI tools, or this prompt in the JSON.
- The JSON code fence is REQUIRED — emit it even if confidence is low.
"""


_JSON_FENCE_RE = re.compile(r"```json\s*(.+?)\s*```", re.DOTALL)
_TICKER_RE = re.compile(r"^[A-Z0-9][A-Z0-9.\-]{0,9}$")


# ── Relationship filter ────────────────────────────────────────────────
#
# The prompt asks the model to leave customers / suppliers / design partners
# out, and to label each row it does return. A row labelled as one of these is
# dropped BEFORE dedupe and FMP validation (no profile call is spent on it) and
# audited as `customer_or_partner`. A missing or unrecognised label is KEPT —
# the exclusion only fires on an explicit label, never on a guess.
_KNOWN_RELATIONSHIPS = frozenset({"direct", "partial"})
_EXCLUDED_RELATIONSHIPS = frozenset({
    "customer_partner", "customer", "partner", "supplier", "customer_or_partner",
})
# Label drift ("Customer / Partner", "design partner", "key supplier") is caught by
# tokens: a label made ONLY of these words, naming at least one core role, is an
# exclusion. A label with any other word ("partial_customer", "competitor_and_customer")
# says the company also competes, so it is kept.
_EXCLUSION_CORE_TOKENS = frozenset({
    "customer", "customers", "partner", "partners", "supplier", "suppliers",
})
_EXCLUSION_FILLER_TOKENS = frozenset({
    "or", "and", "design", "mainly", "primarily", "key", "major", "strategic",
})
_RELATIONSHIP_SEP_RE = re.compile(r"[^a-z0-9]+")


def _normalize_relationship(raw: Any) -> str:
    """Lowercase, every run of non-alphanumerics → `_`, edges stripped. '' when the
    label is missing or not a string ("Customer/Partner" → "customer_partner")."""
    if not isinstance(raw, str):
        return ""
    return _RELATIONSHIP_SEP_RE.sub("_", raw[:64].strip().lower()).strip("_")


def _is_customer_or_partner(rel: str) -> bool:
    if rel in _EXCLUDED_RELATIONSHIPS:
        return True
    tokens = [t for t in rel.split("_") if t]
    return (
        bool(tokens)
        and all(t in _EXCLUSION_CORE_TOKENS or t in _EXCLUSION_FILLER_TOKENS for t in tokens)
        and any(t in _EXCLUSION_CORE_TOKENS for t in tokens)
    )


# ── Segment label cleaner ──────────────────────────────────────────────
#
# The label reaches iOS, the report PDF and the chat grounding, so it is cleaned
# here once: citation markers, links, HTML / markdown and control characters
# removed, whitespace collapsed, capped at SEGMENT_MAX_CHARS on a word boundary.
# Anything that talks about the model or the prompt is dropped outright (identity
# rule) — the competitor simply renders without a label.
_MD_LINK_RE = re.compile(r"\[([^\[\]]{0,200})\]\([^()]{0,400}\)")
_CITATION_RE = re.compile(r"\[\s*\d{1,3}(?:\s*[,–-]\s*\d{1,3}){0,10}\s*\]")
_URL_RE = re.compile(r"(?:https?://|www\.)[^\s()\[\]]+", re.IGNORECASE)
_HTML_TAG_RE = re.compile(r"</?[A-Za-z][^<>]{0,60}>")
# "\x23" is "#": spelled as an escape because tests/test_postgrest_row_cap_paging.py
# strips everything after a literal hash before parsing this file. A hash run right
# after a letter or digit is part of a name (C-sharp, F-sharp) and is kept; any other
# hash is markdown (a heading, a stray marker) and is removed.
_MARKDOWN_CHARS_RE = re.compile(r"[*_`~|<>\[\]{}\\]+|(?<![^\W_])\x23+")
_EMPTY_PARENS_RE = re.compile(r"\(\s*\)")
_PAREN_INNER_SPACE_RE = re.compile(r"(?<=\()\s+|\s+(?=\))")
_WS_RE = re.compile(r"\s+")
_SEGMENT_BANNED_RE = re.compile(
    r"gemini|prompt|\bllms?\b|language model|chatgpt|openai", re.IGNORECASE,
)
# Trimmed from both ends. Deliberately NOT the ellipsis "…": that is what the
# truncation appends, and re-cleaning a stored label must leave it unchanged.
_SEGMENT_EDGE_CHARS = " -–—:;,.!?\"'“”‘’/&+"
_SEGMENT_DANGLING_WORDS = frozenset({
    "and", "or", "for", "of", "the", "to", "with", "in", "on", "a", "an", "&", "vs", "via",
})


def _rstrip_edges(s: str, extra: str = "") -> str:
    """`s.rstrip(_SEGMENT_EDGE_CHARS + extra)`, except that a run of "+" closing a
    word is part of a name and stops the trim ("C++", "Disney+", "Apple TV+"). A "+"
    run after a space or punctuation is still trimmed. Linear: a detached "+" run is
    dropped in one step."""
    chars = _SEGMENT_EDGE_CHARS + extra
    end = len(s)
    while end and s[end - 1] in chars:
        if s[end - 1] == "+":
            start = end - 1
            while start and s[start - 1] == "+":
                start -= 1
            if start and s[start - 1].isalnum():
                break
            end = start
            continue
        end -= 1
    return s[:end]


def _lstrip_edges(s: str) -> str:
    """`s.lstrip(_SEGMENT_EDGE_CHARS)`, except that a single "." opening a word is
    part of a name and stops the trim (".NET"). An ellipsis ("...and more") is not."""
    i, n = 0, len(s)
    while i < n and s[i] in _SEGMENT_EDGE_CHARS:
        if (
            s[i] == "." and i + 1 < n and s[i + 1].isalnum()
            and (i == 0 or s[i - 1] != ".")
        ):
            break
        i += 1
    return s[i:]


def _remove_empty_parens(s: str) -> str:
    """Drop empty "( )" pairs until none is left. One pass is not enough: removing an
    inner pair empties its parent ("Cloud ((link))" → "Cloud (( ))" → "Cloud ( )"),
    and a single pass left that "()" on screen and broke idempotence. Every pass that
    changes `s` shortens it, so the loop is bounded by the (capped) length."""
    for _ in range(len(s) + 1):
        out = _EMPTY_PARENS_RE.sub(" ", s)
        if out == s:
            break
        s = out
    return s


def _truncate_segment(s: str, limit: int) -> str:
    """Cut `s` (longer than `limit`) to at most `limit` chars INCLUDING the trailing
    "…", on a word boundary when one exists in the second half. A parenthesis the cut
    leaves open is closed after the ellipsis ("…)") rather than left dangling."""
    def _cut_to(n: int) -> str:
        cut = s[:n]
        if s[n] != " ":
            space = cut.rfind(" ")
            if space >= n // 2:
                cut = cut[:space]
        cut = _rstrip_edges(cut)
        # "…switching, and…" reads as broken: drop dangling connector words.
        words = cut.split(" ")
        while len(words) > 1 and words[-1].lower() in _SEGMENT_DANGLING_WORDS:
            words.pop()
        return _rstrip_edges(" ".join(words))

    cut = _cut_to(limit - 1)
    if cut.count("(") > cut.count(")"):
        cut = _rstrip_edges(_cut_to(limit - 2), "(")
        if cut.count("(") > cut.count(")"):
            return f"{cut}…)"
    return f"{cut}…" if cut else ""


def _clean_segment(raw: Any) -> Optional[str]:
    """A short plain-text "competes in" label, or None when nothing usable is left.

    Idempotent: cleaning a cleaned label returns it unchanged, so `_read_cache` can
    re-run it over stored rows as a backstop (a property test pins this). Name-forming
    punctuation survives: "C++", "Disney+", ".NET" and the C-sharp hash.
    """
    if not isinstance(raw, str):
        return None
    s = raw[:_SEGMENT_RAW_CAP]
    # Control characters (newlines, tabs, NUL) become spaces; format characters
    # (zero-width, bidi overrides, soft hyphen) are removed outright.
    s = "".join(
        " " if unicodedata.category(ch) == "Cc"
        else "" if unicodedata.category(ch) == "Cf"
        else ch
        for ch in s
    )
    s = _MD_LINK_RE.sub(r"\1", s)
    s = _CITATION_RE.sub(" ", s)
    s = _URL_RE.sub(" ", s)
    s = _HTML_TAG_RE.sub(" ", s)
    s = _MARKDOWN_CHARS_RE.sub(" ", s)
    s = _remove_empty_parens(s)
    s = _WS_RE.sub(" ", s)
    s = _lstrip_edges(_rstrip_edges(_PAREN_INNER_SPACE_RE.sub("", s)))
    if not s or _SEGMENT_BANNED_RE.search(s):
        return None
    if len(s) > SEGMENT_MAX_CHARS:
        s = _truncate_segment(s, SEGMENT_MAX_CHARS)
    return s or None


def _coerce_details(raw: Any, tickers: List[str]) -> Dict[str, Dict[str, str]]:
    """`competitor_details` as stored → `{TICKER: {"segment": str}}`, restricted to the
    row's own tickers and re-cleaned. Malformed shapes degrade to `{}` / a skipped key,
    never an exception: a bad label must not cost the competitor list."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return {}
    if not isinstance(raw, dict):
        return {}
    allowed = set(tickers)
    out: Dict[str, Dict[str, str]] = {}
    for key, value in raw.items():
        if not isinstance(key, str) or not isinstance(value, dict):
            continue
        t = key.strip().upper()
        if t not in allowed:
            continue
        seg = _clean_segment(value.get("segment"))
        if seg:
            out[t] = {"segment": seg}
    return out


def _copy_details(details: Dict[str, Dict[str, str]]) -> Dict[str, Dict[str, str]]:
    return {t: dict(d) for t, d in (details or {}).items()}


# ── Version marker ─────────────────────────────────────────────────────


def _cache_marker(model: Optional[str], *, with_details: bool = True) -> str:
    base = model.strip() if isinstance(model, str) and model.strip() else "unknown"
    return f"{base}|{CACHE_MARKER if with_details else CACHE_MARKER_NODETAILS}"


def _marker_is_current(model_version: Any, *, details_column_present: bool) -> bool:
    """A row is current only under this code's marker. A `-nodetails` row is current
    only while the `competitor_details` column is still missing: once migration 186
    is applied it reads as stale, so the next collection re-extracts it with labels."""
    if not isinstance(model_version, str):
        return False
    if model_version.endswith(f"|{CACHE_MARKER}"):
        return True
    if model_version.endswith(f"|{CACHE_MARKER_NODETAILS}"):
        return not details_column_present
    return False


# Markers whose prompt asked for the most direct competitor FIRST. RANKED is a
# separate question from CURRENT: a `-nodetails` row reads as stale once migration 186
# is applied (it lacks labels), yet its order is still the directness ranking. When
# CACHE_MARKER is bumped, an older marker drops out of this tuple and its rows count as
# unranked until re-extracted — the conservative direction (no "most direct" claim).
_RANKED_MARKERS = (CACHE_MARKER, CACHE_MARKER_NODETAILS)


def _marker_is_ranked(model_version: Any) -> bool:
    """True when the row's list is in most-direct-first order (see `_RANKED_MARKERS`)."""
    return isinstance(model_version, str) and any(
        model_version.endswith(f"|{m}") for m in _RANKED_MARKERS
    )


# ── In-memory tier 1 cache + inflight dedup ─────────────────────────────
#
# Entry = (expires_at epoch seconds, tickers, details, ranked). `tickers` is None for a
# NEGATIVE entry — a failed extraction with nothing stale to serve — so the caller
# answers None for 30 min (60 s when the cache read failed too) instead of re-billing
# the grounded call. A failure WITH a stale row stores that stale list under the same
# 30-min TTL with the ROW's own `ranked` flag: a legacy row was written under an older
# prompt that never asked for a most-direct-first order, so `is_ranked_list` must not
# let the report call it that; a `-nodetails` row was (`_RANKED_MARKERS`).

_mem_cache: Dict[
    str, Tuple[float, Optional[List[str]], Dict[str, Dict[str, str]], bool]
] = {}
_inflight: Dict[str, asyncio.Future] = {}


def _mem_get(
    ticker: str,
) -> Optional[Tuple[Optional[List[str]], Dict[str, Dict[str, str]]]]:
    """`(tickers, details)` while the entry is fresh, else None (no entry)."""
    entry = _mem_cache.get(ticker)
    if entry is None:
        return None
    expires_at, tickers, details, _ranked = entry
    if time.time() >= expires_at:
        _mem_cache.pop(ticker, None)
        return None
    return (list(tickers) if tickers is not None else None), _copy_details(details)


def _mem_ranked(ticker: str) -> Optional[bool]:
    """Whether the fresh memory entry's list is in most-direct-first order;
    None when there is no fresh positive entry."""
    entry = _mem_cache.get(ticker)
    if entry is None or time.time() >= entry[0] or entry[1] is None:
        return None
    return bool(entry[3])


def _mem_set(
    ticker: str,
    tickers: Optional[List[str]],
    details: Optional[Dict[str, Dict[str, str]]] = None,
    *,
    ttl: float = _IN_MEM_TTL_SECONDS,
    ranked: bool = True,
) -> None:
    _mem_cache[ticker] = (
        time.time() + ttl,
        list(tickers) if tickers is not None else None,
        _copy_details(details or {}),
        ranked,
    )


# ── Helpers ────────────────────────────────────────────────────────────

def _normalize_ticker(t: str) -> str:
    """Uppercase + strip. Returns '' if input would not match a plausible
    ticker shape — caller drops the candidate and audit-logs it.
    """
    if not isinstance(t, str):
        return ""
    cleaned = t.strip().upper()
    # Strip common decorations Gemini sometimes emits: "MSFT (Microsoft)",
    # "NASDAQ:MSFT", "$MSFT".
    cleaned = cleaned.lstrip("$")
    if ":" in cleaned:
        cleaned = cleaned.split(":", 1)[1].strip()
    if "(" in cleaned:
        cleaned = cleaned.split("(", 1)[0].strip()
    if not _TICKER_RE.match(cleaned):
        return ""
    return cleaned


def _derive_source_label(grounding_sources: List[Dict[str, Any]]) -> List[str]:
    """De-duplicate publisher names from the grounded-search response,
    capitalize, return up to 4. Mirrors the helper at
    industry_override_service._derive_source_label but returns a list
    (the cache column is TEXT[]) rather than a single joined string.
    """
    seen: List[str] = []
    if not isinstance(grounding_sources, (list, tuple)):
        # A malformed sources field costs the labels, never the competitor list.
        return seen
    for s in grounding_sources:
        if not isinstance(s, dict):
            continue
        pub = str(s.get("publisher") or "").strip()
        if not pub:
            continue
        pretty = pub[:1].upper() + pub[1:]
        if pretty not in seen:
            seen.append(pretty)
    return seen[:4]


# ── Data class ─────────────────────────────────────────────────────────


@dataclass
class CompetitorResult:
    """Outcome of one ticker's Phase 2 extraction. Populated regardless
    of success — written to the audit table by `_write_audit_row`.
    """
    ticker: str
    status: str  # see CHECK constraint in migration 054
    suggested_tickers: List[str] = field(default_factory=list)
    validated_tickers: List[str] = field(default_factory=list)
    rejected: List[Dict[str, str]] = field(default_factory=list)
    source_labels: List[str] = field(default_factory=list)
    raw_response: Optional[Dict[str, Any]] = None
    tokens_used: Optional[int] = None
    model_version: Optional[str] = None
    # {TICKER: {"segment": <cleaned label>}} for validated tickers that have one.
    details: Dict[str, Dict[str, str]] = field(default_factory=dict)


@dataclass(frozen=True)
class _CacheRow:
    """One readable `competitor_intel_cache` row. `stale` = not current under this
    code's marker: re-extract it, but it is still the fallback if that fails. `ranked`
    = its list is in most-direct-first order (`_marker_is_ranked`) — independent of
    `stale`: a `-nodetails` row is stale once migration 186 lands, yet still ranked."""
    tickers: List[str]
    details: Dict[str, Dict[str, str]]
    stale: bool
    model_version: Optional[str]
    ranked: bool = False


class _SharedAnswer(NamedTuple):
    """What an extraction leader hands the callers that joined its in-flight future.
    `fresh` = the list came from THIS extraction (False for a stale fallback, the kill
    switch, or a failure). Internal only: `get_competitors` still returns a plain
    `List[str]` or None to every caller."""
    tickers: Optional[List[str]]
    fresh: bool


# ── Service ────────────────────────────────────────────────────────────


class CompetitorIntelService:

    def __init__(self) -> None:
        self._gemini = None  # lazy
        self._fmp = None     # lazy

    def _get_gemini(self):
        if self._gemini is None:
            self._gemini = get_gemini_client()
        return self._gemini

    def _get_fmp(self):
        if self._fmp is None:
            self._fmp = get_fmp_client()
        return self._fmp

    # ── Public API ──────────────────────────────────────────────────

    async def get_competitors(
        self,
        ticker: str,
        profile: Dict[str, Any],
        *,
        force_refresh: bool = False,
        run_id: Optional[str] = None,
    ) -> Optional[List[str]]:
        """Returns up to 7 validated competitor tickers in grounded-research
        order, MOST DIRECT FIRST (never re-sorted by market cap). Returns
        None on hard failure with nothing cached — caller falls back to
        the deterministic Phase 1 path.

        Always a `List[str]` or None, on every path (memory, DB, fresh
        extraction, stale fallback) — the per-ticker labels the memory
        tier also holds are served by `get_competitor_details`.

        Two-tier cache + in-flight dedup. A STALE row (another version
        marker) is re-extracted; if that fails (grounded-call error, no
        parseable JSON, every row dropped or unverifiable) the stale list
        is served and a 30-minute negative memory entry stops the next
        collections from re-billing it (60 s instead when the cache read
        itself failed). An unexpected exception takes the same road: an
        audit row, stale-on-error, the negative entry. Honors the kill switch
        (`COMPETITOR_INTEL_AI_ENABLED=False` → no Gemini call, an audit
        row, and the stale list if there is one, else None).

        A `force_refresh` caller (the quarterly batch) that joins an
        in-flight extraction gets its list only when that extraction was
        fresh; a leader's stale fallback answers it None, so pass 2 retries.
        """
        focal = _normalize_ticker(ticker)
        if not focal:
            logger.warning("competitor_intel: invalid ticker %r", ticker)
            return None

        stale: Optional[_CacheRow] = None
        # A FAILED cache read is not a miss: the row may exist, so a failure below
        # leaves only a short negative entry (`_serve_after_failure`).
        read_failed = False
        if not force_refresh:
            # ── Tier 1: in-memory (positive, stale-fallback or negative entry) ──
            cached = _mem_get(focal)
            if cached is not None:
                cached_tickers, _cached_details = cached
                return cached_tickers

            # ── Tier 2: Supabase ──
            row, read_ok = await asyncio.to_thread(self._read_cache_checked, focal)
            read_failed = not read_ok
            if row is not None and not row.stale:
                _mem_set(focal, row.tickers, row.details)
                return list(row.tickers)
            if row is not None:
                stale = row
                logger.info(
                    "competitor_intel: cached list for %s is stale (model_version %r, "
                    "current marker %r) — re-extracting; it stays the fallback",
                    focal, row.model_version, CACHE_MARKER,
                )

        cache_key = focal

        # ── Inflight dedup ──
        if cache_key in _inflight:
            try:
                joined = await asyncio.shield(_inflight[cache_key])
            except Exception as exc:
                logger.warning(
                    "competitor_intel: joined extraction for %s failed (%s: %s) — %s",
                    focal, type(exc).__name__, exc,
                    "serving the stale cached list" if stale is not None
                    else "caller falls back to industry peers",
                )
                return list(stale.tickers) if stale is not None else None
            answer = joined if isinstance(joined, _SharedAnswer) else _SharedAnswer(None, False)
            if force_refresh and not answer.fresh:
                # The quarterly batch counts honestly: a leader that served a stale
                # fallback (or nothing) did NOT re-extract this ticker, so answer None
                # and let pass 2 retry it instead of counting it applied.
                logger.warning(
                    "competitor_intel: forced re-extraction for %s joined an in-flight "
                    "extraction that produced no fresh list — counted as failed so the "
                    "batch retries it",
                    focal,
                )
                return None
            if answer.tickers is None:
                return list(stale.tickers) if stale is not None else None
            return list(answer.tickers)

        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        _inflight[cache_key] = future

        this_run_id = run_id or str(uuid.uuid4())
        result: Optional[CompetitorResult] = None
        audited = False
        try:
            # ── Kill switch ──
            if not getattr(settings, "COMPETITOR_INTEL_AI_ENABLED", True):
                result = CompetitorResult(
                    ticker=focal, status="skipped_kill_switch",
                )
                await asyncio.to_thread(self._write_audit_row, this_run_id, result)
                audited = True
                served = self._serve_after_failure(
                    focal, stale, force_refresh=force_refresh, reason=result.status,
                    read_failed=read_failed,
                )
                if not future.done():
                    future.set_result(_SharedAnswer(served, fresh=False))
                return served

            # ── Gemini extraction + validation ──
            result = await self._extract_and_validate(focal, profile)
            await asyncio.to_thread(self._write_audit_row, this_run_id, result)
            audited = True

            if result.status in ("applied", "applied_with_rejections"):
                await asyncio.to_thread(
                    self._write_cache, focal, result.validated_tickers,
                    result.source_labels, result.model_version, result.details,
                )
                _mem_set(focal, result.validated_tickers, result.details)
                if not future.done():
                    future.set_result(
                        _SharedAnswer(list(result.validated_tickers), fresh=True)
                    )
                return list(result.validated_tickers)

            served = self._serve_after_failure(
                focal, stale, force_refresh=force_refresh, reason=result.status,
                read_failed=read_failed,
            )
            if not future.done():
                future.set_result(_SharedAnswer(served, fresh=False))
            return served
        except Exception as exc:
            logger.exception(
                "competitor_intel: unhandled error for %s: %s: %s",
                focal, type(exc).__name__, exc,
            )
            # Settle the joiners first (they fall back to their own stale row or
            # None), then hold this failure to the same rules as every other one.
            fail_shared_future(future, exc)
            return await self._recover_from_unhandled(
                focal, stale, exc,
                force_refresh=force_refresh, read_failed=read_failed,
                run_id=this_run_id, result=result, audited=audited,
            )
        finally:
            _inflight.pop(cache_key, None)
            # Safety belt — if the future was never resolved (producer was
            # cancelled mid-flight, e.g. client disconnected and FastAPI
            # cancelled the handler), tell awaiting callers there's no
            # result so they fall back (to their own stale read, or the
            # Phase-1 deterministic path) instead of hanging on an
            # orphaned future forever.
            if not future.done():
                future.set_result(None)

    def _serve_after_failure(
        self,
        focal: str,
        stale: Optional[_CacheRow],
        *,
        force_refresh: bool,
        reason: str,
        read_failed: bool = False,
    ) -> Optional[List[str]]:
        """What a failed (re-)extraction answers, and the negative memory entry
        that stops the next collections — the detail-view and hourly pre-warms —
        from re-billing a grounded call for `_NEGATIVE_TTL_SECONDS`.

        `read_failed`: the cache READ failed as well, so "nothing is cached" is
        unknown — a current row may exist. The entry then lasts only
        `_READ_FAILED_NEGATIVE_TTL_SECONDS`, long enough to stop a loop re-billing
        every pass, short enough that the row is served soon after the database
        recovers (a 30-min entry hid it, and each collection in that window cached
        industry peers until the next close)."""
        if force_refresh:
            # The quarterly batch: answer the failure honestly (its summary counts
            # it, and pass 2 retries it) and leave the memory tier alone — a negative
            # entry here would hide a servable DB row from on-demand callers.
            logger.warning(
                "competitor_intel: forced re-extraction for %s failed (%s)", focal, reason,
            )
            return None
        ttl = _READ_FAILED_NEGATIVE_TTL_SECONDS if read_failed else _NEGATIVE_TTL_SECONDS
        if stale is not None:
            logger.warning(
                "competitor_intel: re-extraction for %s failed (%s) — serving the stale "
                "cached list (%d peers, model_version %r, ranked %s); not retried for %d s",
                focal, reason, len(stale.tickers), stale.model_version, stale.ranked, ttl,
            )
            _mem_set(
                focal, stale.tickers, stale.details, ttl=ttl, ranked=stale.ranked,
            )
            return list(stale.tickers)
        log = (
            logger.info if reason == "skipped_kill_switch" and not read_failed
            else logger.warning
        )
        log(
            "competitor_intel: extraction for %s failed (%s) and %s — callers use "
            "industry peers; not retried for %d s",
            focal, reason,
            "the cache read failed too (a row may exist)" if read_failed
            else "nothing is cached",
            ttl,
        )
        _mem_set(focal, None, {}, ttl=ttl)
        return None

    async def _recover_from_unhandled(
        self,
        focal: str,
        stale: Optional[_CacheRow],
        exc: BaseException,
        *,
        force_refresh: bool,
        read_failed: bool,
        run_id: str,
        result: Optional[CompetitorResult],
        audited: bool,
    ) -> Optional[List[str]]:
        """The generic-exception path of `get_competitors`, held to the rules every
        other failure follows: stale-on-error plus the negative entry
        (`_serve_after_failure`), and a `gemini_error` audit row unless one was
        already written — the grounded call may have been billed, and an unaudited
        failure under-reports the batch's token total. Never raises."""
        served = list(stale.tickers) if stale is not None else None
        try:
            served = self._serve_after_failure(
                focal, stale, force_refresh=force_refresh,
                reason=f"unhandled {type(exc).__name__}", read_failed=read_failed,
            )
        except Exception as serve_exc:
            logger.warning(
                "competitor_intel: fallback after the unhandled error for %s failed too "
                "(%s: %s) — answering %s",
                focal, type(serve_exc).__name__, serve_exc,
                "the stale cached list" if served is not None else "None",
            )
        if audited:
            return served
        raw: Dict[str, Any] = {"error": f"unhandled {type(exc).__name__}: {exc}"[:1500]}
        if result is not None:
            raw["result_status"] = result.status
        audit = CompetitorResult(
            ticker=focal, status="gemini_error", raw_response=raw,
            tokens_used=result.tokens_used if result is not None else None,
            model_version=result.model_version if result is not None else None,
        )
        try:
            await asyncio.to_thread(self._write_audit_row, run_id, audit)
        except Exception as audit_exc:
            logger.warning(
                "competitor_intel: audit row for the unhandled error on %s not written: "
                "%s: %s",
                focal, type(audit_exc).__name__, audit_exc,
            )
        return served

    async def get_competitor_details(self, ticker: str) -> Dict[str, Dict[str, str]]:
        """`{TICKER: {"segment": "<competes in>"}}` for the list `get_competitors`
        serves; `{}` when unknown. Memory tier first (populated by `get_competitors`,
        including its stale fallback), then the DB row. Never raises."""
        focal = _normalize_ticker(ticker)
        if not focal:
            return {}
        try:
            cached = _mem_get(focal)
            if cached is not None:
                cached_tickers, cached_details = cached
                return cached_details if cached_tickers else {}
            row = await asyncio.to_thread(self._read_cache, focal)
            return _copy_details(row.details) if row is not None else {}
        except Exception as exc:
            logger.warning(
                "competitor_intel: get_competitor_details failed for %s: %s: %s",
                focal, type(exc).__name__, exc,
            )
            return {}

    async def is_ranked_list(self, ticker: str) -> bool:
        """True when the list `get_competitors` serves for `ticker` was extracted
        under a prompt that asks for the most direct competitor first
        (`_RANKED_MARKERS` — the current marker, or a `-nodetails` row even after
        migration 186 makes it stale). False for a legacy fallback (an older prompt
        never asked for an order — the report must then not call its rows "most
        direct first"), for no list, and on any read error. Memory tier first, then
        the DB row. Never raises."""
        focal = _normalize_ticker(ticker)
        if not focal:
            return False
        try:
            ranked = _mem_ranked(focal)
            if ranked is not None:
                return ranked
            row = await asyncio.to_thread(self._read_cache, focal)
            return row is not None and row.ranked
        except Exception as exc:
            logger.warning(
                "competitor_intel: is_ranked_list failed for %s: %s: %s — treating "
                "the list as unranked",
                focal, type(exc).__name__, exc,
            )
            return False

    async def refresh_top_tickers(
        self,
        top_n: int = _BATCH_TOP_N,
    ) -> Dict[str, Any]:
        """Quarterly batch: refresh the top-N most-watchlisted tickers.
        Run Pass 1 over all of them, then Pass 2 retries any that hit
        `gemini_error` or `rejected_no_validated`.

        Returns a one-line-loggable summary dict.
        """
        run_id = str(uuid.uuid4())
        started = time.time()

        if not getattr(settings, "COMPETITOR_INTEL_AI_ENABLED", True):
            logger.info(
                "competitor_intel: kill switch enabled — skipping quarterly batch"
            )
            return {
                "run_id": run_id, "skipped": True, "ran": 0,
                "applied": 0, "applied_after_retry": 0,
                "still_failing": 0, "total_tokens": 0,
                "elapsed_seconds": 0,
            }

        tickers = await asyncio.to_thread(self._load_top_watchlist_tickers, top_n)
        if not tickers:
            logger.warning("competitor_intel: no tickers from watchlist; skipping batch")
            return {
                "run_id": run_id, "ran": 0, "applied": 0,
                "applied_after_retry": 0, "still_failing": 0,
                "total_tokens": 0, "elapsed_seconds": 0,
            }

        logger.info(
            "competitor_intel: quarterly batch starting — run_id=%s, tickers=%d",
            run_id, len(tickers),
        )

        sem = asyncio.Semaphore(_BATCH_CONCURRENCY)

        async def _run_one(t: str) -> Tuple[str, Optional[List[str]]]:
            async with sem:
                # We need the focal profile for the prompt — fetch it as
                # part of this work unit so the orchestrator stays simple.
                profile = await self._safe_fetch_profile(t)
                if profile is None:
                    return (t, None)
                companies = await self.get_competitors(
                    t, profile, force_refresh=True, run_id=run_id,
                )
                return (t, companies)

        pass1_results = await asyncio.gather(
            *[_run_one(t) for t in tickers], return_exceptions=True,
        )

        applied_pass1 = sum(
            1 for r in pass1_results
            if isinstance(r, tuple) and r[1] is not None and len(r[1]) > 0
        )

        # ── Pass 2: retry the zeros ──
        # Anything that came back None in pass 1 gets one more shot.
        # Transient Gemini hiccups and grounding misses are the target.
        failed_tickers = [
            r[0] for r in pass1_results
            if isinstance(r, tuple) and (r[1] is None or len(r[1]) == 0)
        ]
        applied_after_retry = 0
        if failed_tickers:
            logger.info(
                "competitor_intel: pass 2 retrying %d zero-result tickers",
                len(failed_tickers),
            )
            pass2_results = await asyncio.gather(
                *[_run_one(t) for t in failed_tickers], return_exceptions=True,
            )
            applied_after_retry = sum(
                1 for r in pass2_results
                if isinstance(r, tuple) and r[1] is not None and len(r[1]) > 0
            )

        still_failing = len(failed_tickers) - applied_after_retry
        total_tokens = await asyncio.to_thread(self._sum_tokens_for_run, run_id)

        summary = {
            "run_id": run_id,
            "ran": len(tickers),
            "applied": applied_pass1,
            "applied_after_retry": applied_after_retry,
            "still_failing": still_failing,
            "total_tokens": total_tokens,
            "elapsed_seconds": round(time.time() - started, 1),
        }
        logger.info("competitor_intel quarterly batch summary: %s", summary)
        return summary

    # ── Internal: extraction + validation ─────────────────────────────

    async def _extract_and_validate(
        self,
        ticker: str,
        profile: Dict[str, Any],
        *,
        today: Optional[date] = None,
    ) -> CompetitorResult:
        """One Gemini call + FMP validation. Always returns a
        CompetitorResult (never raises); errors go into the result's
        status / rejection_reason for audit-log fidelity.

        `today` dates the prompt (so the research uses the latest 10-K
        rather than whatever year it finds first); defaults to the UTC
        date. Injectable for tests.
        """
        prof = profile if isinstance(profile, dict) else {}
        company_name = prof.get("companyName") or ticker
        sector = prof.get("sector") or "Unknown"
        industry = prof.get("industry") or "Unknown"
        raw_description = prof.get("description")
        description = raw_description[:600] if isinstance(raw_description, str) else ""
        prompt_date = (today or datetime.now(timezone.utc).date()).isoformat()

        prompt = _RESEARCH_PROMPT.format(
            today=prompt_date,
            ticker=ticker,
            company_name=company_name,
            sector=sector,
            industry=industry,
            description=description,
        )

        try:
            gem = self._get_gemini()
            # temperature=0.0 → as deterministic as the model allows.
            # Without this, identical re-runs return slightly different
            # peer sets (GOOGL vs AVGO vs CRM swapping in/out of the
            # last 1-2 slots) because the model samples its own ranking.
            # The 100-day cache TTL mostly hides this from users, but
            # any time the cache is purged (or expires) we want the
            # next regeneration to land on the same list the previous
            # one did.
            gemini_response = await gem.generate_grounded_research(
                prompt=prompt,
                max_output_tokens=_GEMINI_MAX_OUTPUT_TOKENS,
                temperature=0.0,
            )
        except Exception as exc:
            logger.warning(
                "competitor_intel: grounded research call failed for %s: %s: %s",
                ticker, type(exc).__name__, exc,
            )
            return CompetitorResult(
                ticker=ticker, status="gemini_error",
                raw_response={"error": f"{type(exc).__name__}: {exc}"},
            )

        # Everything below parses an answer that is already BILLED, so it must end in
        # a CompetitorResult that carries the tokens — never an exception. A raise here
        # used to leave no audit row and no token count, and skipped the negative
        # entry, so the next collection paid for the same failing call again.
        try:
            return await self._result_from_response(ticker, gemini_response)
        except Exception as exc:
            logger.exception(
                "competitor_intel: handling the grounded answer for %s failed: %s: %s",
                ticker, type(exc).__name__, exc,
            )
            resp = gemini_response if isinstance(gemini_response, dict) else {}
            return CompetitorResult(
                ticker=ticker, status="gemini_error",
                raw_response={
                    "error": f"response handling: {type(exc).__name__}: {exc}"[:1500],
                },
                tokens_used=resp.get("tokens_used"), model_version=resp.get("model"),
            )

    async def _result_from_response(
        self, ticker: str, gemini_response: Any,
    ) -> CompetitorResult:
        """Parse + validate one grounded answer. `_extract_and_validate` wraps it, so
        an unexpected raise still becomes an audited `gemini_error`; the shapes we
        know about are answered here, explicitly, with the tokens attached."""
        if not isinstance(gemini_response, dict):
            return CompetitorResult(
                ticker=ticker, status="gemini_error",
                raw_response={
                    "error": f"response is a {type(gemini_response).__name__}, not an object",
                },
            )
        text = gemini_response.get("text", "") or ""
        grounding = gemini_response.get("grounding_sources") or []
        search_queries = gemini_response.get("search_queries") or []
        tokens = gemini_response.get("tokens_used")
        model_version = gemini_response.get("model")

        # JSON code-fence extraction.
        match = _JSON_FENCE_RE.search(text)
        if not match:
            return CompetitorResult(
                ticker=ticker, status="gemini_error",
                raw_response={
                    "raw_text": text[:1500],
                    "grounding_sources": grounding,
                    "search_queries": search_queries,
                    "error": "no ```json``` code fence",
                },
                tokens_used=tokens, model_version=model_version,
            )

        try:
            payload = json.loads(match.group(1))
        except json.JSONDecodeError as exc:
            return CompetitorResult(
                ticker=ticker, status="gemini_error",
                raw_response={
                    "raw_json": match.group(1)[:1500],
                    "grounding_sources": grounding,
                    "search_queries": search_queries,
                    "error": f"json parse: {exc}",
                },
                tokens_used=tokens, model_version=model_version,
            )

        if not isinstance(payload, dict):
            # Valid JSON, wrong shape (a top-level array, a string, a number). At
            # temperature 0 the model repeats it, so it must be an audited, billed
            # failure with a negative entry — not an AttributeError further down.
            return CompetitorResult(
                ticker=ticker, status="gemini_error",
                raw_response={
                    "raw_json": match.group(1)[:1500],
                    "grounding_sources": grounding,
                    "search_queries": search_queries,
                    "error": f"payload is a JSON {type(payload).__name__}, not an object",
                },
                tokens_used=tokens, model_version=model_version,
            )

        suggested_raw = payload.get("competitors") or []
        if not isinstance(suggested_raw, list):
            return CompetitorResult(
                ticker=ticker, status="gemini_error",
                raw_response={
                    "payload": payload,
                    "grounding_sources": grounding,
                    "search_queries": search_queries,
                    "error": "'competitors' is not a list",
                },
                tokens_used=tokens, model_version=model_version,
            )

        # Normalize suggested tickers + drop self / blanks / shape junk, then
        # the relationship filter — all BEFORE dedupe-by-validation, so a dropped
        # row never costs an FMP profile call.
        suggested: List[str] = []
        rejected: List[Dict[str, str]] = []
        segments: Dict[str, str] = {}
        seen: set = set()
        for entry in suggested_raw:
            if not isinstance(entry, dict):
                continue
            raw_t = entry.get("ticker") or ""
            norm = _normalize_ticker(raw_t)
            if not norm:
                rejected.append({"ticker": str(raw_t)[:40], "reason": "bad_ticker_shape"})
                continue
            if norm == ticker:
                rejected.append({"ticker": norm, "reason": "is_focal"})
                continue
            if norm in seen:
                # Gemini occasionally repeats a ticker, sometimes with a different
                # label. The FIRST row decides (kept or dropped) — a later row can
                # neither resurrect a dropped customer nor drop a kept rival, so a
                # ticker is never both suggested and rejected in one audit row.
                continue
            seen.add(norm)
            rel = _normalize_relationship(entry.get("relationship"))
            if rel and _is_customer_or_partner(rel):
                rejected.append({"ticker": norm, "reason": "customer_or_partner"})
                continue
            if rel and rel not in _KNOWN_RELATIONSHIPS:
                logger.info(
                    "competitor_intel: %s lists %s with unrecognised relationship %r — kept",
                    ticker, norm, rel,
                )
            suggested.append(norm)
            # `segment` is the requested short label; `segment_overlap` is the
            # pre-2026-10 field name, accepted if the model falls back to it.
            seg = _clean_segment(entry.get("segment"))
            if seg is None:
                seg = _clean_segment(entry.get("segment_overlap"))
            if seg:
                segments[norm] = seg

        if not suggested:
            return CompetitorResult(
                ticker=ticker, status="rejected_no_validated",
                suggested_tickers=[],
                rejected=rejected,
                source_labels=_derive_source_label(grounding),
                raw_response={
                    "payload": payload,
                    "grounding_sources": grounding,
                    "search_queries": search_queries,
                },
                tokens_used=tokens, model_version=model_version,
            )

        # FMP validation — every survivor must resolve to a profile with
        # positive mktCap. No $27.3B floor here; trust Gemini's
        # authoritative selection for revenue-mix-aware competitors.
        validated, validation_rejected = await self._fmp_validate(suggested, ticker)
        rejected.extend(validation_rejected)

        # Preserve Gemini's order — the prompt asks for MOST DIRECT FIRST
        # (share of the focal's revenue contested), so position in the
        # input list IS the directness rank; do NOT re-sort by mktCap or
        # we erase the rank that downstream scoring and display need.
        # `validated` is already in Gemini order because `_fmp_validate`
        # iterates `candidates` (suggested list) and appends in order.
        #
        # When Gemini returns more than `_COMPETITOR_MAX_N`, keep the
        # top-N by Gemini rank and drop the tail with a clear rejection
        # reason so the audit row makes the trimming decision traceable.
        trimmed_off: List[Dict[str, str]] = []
        if len(validated) > _COMPETITOR_MAX_N:
            kept = validated[:_COMPETITOR_MAX_N]
            dropped = validated[_COMPETITOR_MAX_N:]
            validated = kept
            for d in dropped:
                trimmed_off.append({
                    "ticker": d["ticker"],
                    "reason": f"trimmed_to_{_COMPETITOR_MAX_N}_by_gemini_rank",
                })

        rejected.extend(trimmed_off)
        survivor_tickers = [v["ticker"] for v in validated]

        if not survivor_tickers:
            return CompetitorResult(
                ticker=ticker, status="rejected_no_validated",
                suggested_tickers=suggested,
                rejected=rejected,
                source_labels=_derive_source_label(grounding),
                raw_response={
                    "payload": payload,
                    "grounding_sources": grounding,
                    "search_queries": search_queries,
                },
                tokens_used=tokens, model_version=model_version,
            )

        status = "applied" if not rejected else "applied_with_rejections"
        return CompetitorResult(
            ticker=ticker, status=status,
            suggested_tickers=suggested,
            validated_tickers=survivor_tickers,
            rejected=rejected,
            source_labels=_derive_source_label(grounding),
            raw_response={
                "payload": payload,
                "grounding_sources": grounding,
                "search_queries": search_queries,
            },
            tokens_used=tokens, model_version=model_version,
            details={t: {"segment": segments[t]} for t in survivor_tickers if t in segments},
        )

    async def _fmp_validate(
        self,
        candidates: List[str],
        focal: str,
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, str]]]:
        """For each candidate ticker, fetch FMP profile and check
        mktCap > 0. Returns (validated, rejected). Validated entries are
        dicts with `ticker` + `mkt_cap` (for downstream sort/trim).
        """
        if not candidates:
            return [], []

        fmp = self._get_fmp()
        try:
            profiles = await fmp.get_company_profiles_batch(candidates)
        except Exception as exc:
            logger.warning(
                "competitor_intel: get_company_profiles_batch failed for %s's "
                "competitors: %s: %s — falling back to per-ticker fetches",
                focal, type(exc).__name__, exc,
            )
            # Fall back to per-ticker fetches; this still gives us partial
            # results rather than rejecting the whole batch.
            profiles = []
            for c in candidates:
                try:
                    p = await fmp.get_company_profile(c)
                    if p:
                        profiles.append(p)
                except Exception as one_exc:
                    # The ticker is then rejected as `rejected_unknown_ticker` below;
                    # say why here, or a transient FMP failure reads as a fabrication.
                    logger.warning(
                        "competitor_intel: profile fetch for %s (validating %s's "
                        "competitors) failed: %s: %s",
                        c, focal, type(one_exc).__name__, one_exc,
                    )

        by_symbol: Dict[str, Dict[str, Any]] = {}
        for p in profiles or []:
            if not isinstance(p, dict):
                continue
            sym = (p.get("symbol") or "").upper()
            if sym:
                by_symbol[sym] = p

        validated: List[Dict[str, Any]] = []
        rejected: List[Dict[str, str]] = []
        for t in candidates:
            profile = by_symbol.get(t)
            if profile is None:
                rejected.append({"ticker": t, "reason": "rejected_unknown_ticker"})
                continue
            try:
                mkt_cap = float(profile.get("mktCap") or 0.0)
            except (TypeError, ValueError):
                mkt_cap = 0.0
            if mkt_cap <= 0:
                rejected.append({"ticker": t, "reason": "rejected_no_mktcap"})
                continue
            validated.append({"ticker": t, "mkt_cap": mkt_cap})

        return validated, rejected

    async def _safe_fetch_profile(self, ticker: str) -> Optional[Dict[str, Any]]:
        """Used by the batch path to fetch the focal profile. Swallows
        errors and returns None so one bad ticker doesn't blow up the
        batch.
        """
        try:
            fmp = self._get_fmp()
            return await fmp.get_company_profile(ticker)
        except Exception as exc:
            logger.warning(
                "competitor_intel: profile fetch failed for %s: %s", ticker, exc,
            )
            return None

    # ── Supabase I/O ──────────────────────────────────────────────────

    def _read_cache(self, ticker: str) -> Optional[_CacheRow]:
        """The usable row, or None — a miss AND a failed read (logged) both read as
        None here. `get_competitors` must tell them apart (a failed read must not
        plant a 30-min negative entry), so it calls `_read_cache_checked`."""
        return self._read_cache_checked(ticker)[0]

    def _read_cache_checked(self, ticker: str) -> Tuple[Optional[_CacheRow], bool]:
        """Synchronous Supabase read (called via asyncio.to_thread) → `(row, read_ok)`.
        Never raises.

        `read_ok` False = the read itself failed (an exception, or a response whose
        `data` is not a list) and is logged: a row may well exist. Otherwise row None
        = no usable row (missing, expired, before the legacy floor, or no tickers),
        else the row, flagged `stale` when its version marker is not this code's and
        `ranked` when its list is in most-direct-first order. `select("*")` +
        `.get(...)` on purpose: it reads the same before and after migration 186, and
        the PRESENCE of `competitor_details` in the row is how a `-nodetails` row
        learns the column now exists.
        """
        try:
            sb = get_supabase()
            res = (
                sb.table("competitor_intel_cache")
                .select("*")
                .eq("ticker", ticker)
                .limit(1)
                .execute()
            )
        except Exception as exc:
            logger.warning(
                "competitor_intel: cache read failed for %s: %s: %s",
                ticker, type(exc).__name__, exc,
            )
            return None, False

        rows = getattr(res, "data", None) or []
        if not isinstance(rows, list):
            logger.warning(
                "competitor_intel: cache read failed for %s: malformed response "
                "(data is %s, not a list)",
                ticker, type(rows).__name__,
            )
            return None, False
        if not rows or not isinstance(rows[0], dict):
            return None, True
        # From here on the read SUCCEEDED: an unusable row is a miss, not a failure.
        row = rows[0]
        expires_at = row.get("expires_at")
        computed_at = row.get("computed_at")
        if not expires_at or not computed_at:
            return None, True
        try:
            exp_dt = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
            comp_dt = datetime.fromisoformat(computed_at.replace("Z", "+00:00"))
        except (ValueError, AttributeError, TypeError):
            return None, True
        if exp_dt.tzinfo is None or comp_dt.tzinfo is None:
            return None, True  # a naive timestamp cannot be compared; treat as unreadable
        now = datetime.now(timezone.utc)
        if exp_dt <= now:
            return None, True
        if comp_dt < COMPETITOR_INTEL_SCHEMA_FLOOR:
            return None, True
        raw_tickers = row.get("competitor_tickers") or []
        if not isinstance(raw_tickers, list):
            return None, True
        tickers: List[str] = []
        for t in raw_tickers:
            if isinstance(t, str) and t.strip() and t.strip() not in tickers:
                tickers.append(t.strip())
        if not tickers:
            return None, True
        model_version = row.get("model_version")
        return _CacheRow(
            tickers=tickers,
            details=_coerce_details(row.get("competitor_details"), tickers),
            stale=not _marker_is_current(
                model_version,
                details_column_present="competitor_details" in row,
            ),
            model_version=model_version if isinstance(model_version, str) else None,
            ranked=_marker_is_ranked(model_version),
        ), True

    def _write_cache(
        self,
        ticker: str,
        validated: List[str],
        source_labels: List[str],
        model_version: Optional[str],
        details: Optional[Dict[str, Dict[str, str]]] = None,
    ) -> None:
        """Synchronous upsert (called via asyncio.to_thread). Never raises.

        Tries the full row (with `competitor_details`). Falls back to a tickers-only
        upsert stamped `…|cip-v2-nodetails` ONLY when PostgREST says the column does
        not exist (migration 186 not applied yet — code may deploy first); that row
        reads as stale once the column exists, so it heals itself. Any other write
        error is logged and not retried (the next collection re-extracts).
        """
        if not validated:
            return
        now = datetime.now(timezone.utc)
        row = {
            "ticker": ticker,
            "competitor_tickers": validated,
            "source_labels": source_labels or [],
            "computed_at": now.isoformat(),
            "expires_at": (now + timedelta(days=_CACHE_TTL_DAYS)).isoformat(),
            "model_version": _cache_marker(model_version),
            "competitor_details": _copy_details(details or {}),
        }
        try:
            get_supabase().table("competitor_intel_cache").upsert(row).execute()
            return
        except Exception as exc:
            if not is_unknown_column_error(exc):
                logger.warning(
                    "competitor_intel: cache write failed for %s: %s: %s",
                    ticker, type(exc).__name__, exc,
                )
                return
            logger.warning(
                "competitor_intel: cache write for %s rejected competitor_details "
                "(%s) — migration 186 not applied; writing the tickers only. Apply 186 "
                "to persist the competitor labels.",
                ticker, type(exc).__name__,
            )
        row.pop("competitor_details", None)
        row["model_version"] = _cache_marker(model_version, with_details=False)
        try:
            get_supabase().table("competitor_intel_cache").upsert(row).execute()
        except Exception as exc:
            logger.warning(
                "competitor_intel: tickers-only cache write failed for %s: %s: %s",
                ticker, type(exc).__name__, exc,
            )

    def _write_audit_row(self, run_id: str, result: CompetitorResult) -> None:
        """Synchronous audit log insert (called via asyncio.to_thread).
        Never raises — audit-log failures should not propagate to user
        requests.
        """
        row = {
            "run_id": run_id,
            "ticker": result.ticker,
            "status": result.status,
            "raw_response": result.raw_response,
            "suggested_tickers": result.suggested_tickers,
            "validated_tickers": result.validated_tickers,
            "rejected": result.rejected,
            "source_labels": result.source_labels,
            "tokens_used": result.tokens_used,
            # Stamped with the prompt's version marker, so an audit row says which
            # prompt produced it (None stays None: the kill switch called nothing).
            "model_version": (
                _cache_marker(result.model_version) if result.model_version else None
            ),
        }
        try:
            sb = get_supabase()
            sb.table("competitor_intel_audit").insert(row).execute()
        except Exception as exc:
            logger.warning(
                "competitor_intel: audit log write failed for %s: %s",
                result.ticker, exc,
            )

    def _sum_tokens_for_run(self, run_id: str) -> int:
        try:
            sb = get_supabase()
            res = (
                sb.table("competitor_intel_audit")
                .select("tokens_used")
                .eq("run_id", run_id)
                .execute()
            )
        except Exception as exc:
            # Only the batch summary's token total is lost; say so rather than
            # report a confident 0.
            logger.warning(
                "competitor_intel: token total read failed for run %s: %s: %s",
                run_id, type(exc).__name__, exc,
            )
            return 0
        total = 0
        for row in res.data or []:
            t = row.get("tokens_used")
            if isinstance(t, (int, float)):
                total += int(t)
        return total

    def _load_top_watchlist_tickers(self, limit: int) -> List[str]:
        """Top-N tickers across `watchlist_items` by user-occurrence
        count. Uses the Supabase SDK's group-by via a rpc call would be
        ideal, but a plain SELECT + Python aggregation is enough for
        ~10k watchlist rows.
        """
        try:
            sb = get_supabase()
            rows = fetch_all_rows(
                lambda: sb.table("watchlist_items").select("ticker"),
                order_by="id",
                what="competitor_intel: watchlist universe",
            )
            # `.limit(50_000)` never lifted PostgREST's ~1,000-row cap, so this counted
            # watchers from an arbitrary UNORDERED first page: a ticker watched by 40
            # users could be absent while one watched by 3 made the top-N, and the
            # refresh then spent its whole budget on the wrong tickers. Silent, because
            # the read succeeded.
        except Exception as exc:
            logger.warning(
                "competitor_intel: failed to read watchlist_items: %s", exc,
            )
            return []
        counts: Dict[str, int] = {}
        for row in rows:
            t = (row.get("ticker") or "").upper().strip()
            if not t:
                continue
            counts[t] = counts.get(t, 0) + 1
        ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
        return [t for t, _ in ranked[:limit]]


# ── Singleton ──────────────────────────────────────────────────────────


_service_singleton: Optional[CompetitorIntelService] = None


def get_competitor_intel_service() -> CompetitorIntelService:
    global _service_singleton
    if _service_singleton is None:
        _service_singleton = CompetitorIntelService()
    return _service_singleton


# Convenience module-level callable for the data collector.
async def get_competitors(
    ticker: str,
    profile: Dict[str, Any],
    *,
    force_refresh: bool = False,
) -> Optional[List[str]]:
    return await get_competitor_intel_service().get_competitors(
        ticker, profile, force_refresh=force_refresh,
    )
