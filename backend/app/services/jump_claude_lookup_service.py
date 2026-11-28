"""The Updates jump search's FALLBACK web lookup (2026-11-27) — a second provider finds the pages.

WHY. On 2026-11-23 the jump search's Brave budget was spent by 10:07 ET and the pages that
explained CRWV's +22% appeared around noon, with no search left; on 2026-11-25 PLUG's searches were
gone 35 minutes after its reversal. Owner decisions (2026-11-27): "do Brave 80 a day, and use Claude
API (200 credits as fallback)", "Claude Sonnet 5.5", "$40 a month" (+ 30 lookups a day). The jump
search (`jump_web_search_service`, the ONLY importer — `tests/test_claude_web_search_boundary.py`)
asks this module when Brave cannot run or finds nothing; it decides WHEN (its triggers T1-T3), this
module decides WHETHER (`enabled`, the budget) and does the one call.

WHAT IS SENT. A code-authored prompt holding ONLY the company's cleaned name (the same name the
Brave query uses) and "up" / "down" — no symbol, no figure, no date, nothing about a user. The
server-side web search tool (`web_search_20260209`, at most MAX_USES searches) with the hygiene
deny-list as `blocked_domains` and an approximate US / New York location. No temperature, no forced
tool, no structured output, no fallback model, one attempt (`llm_provider.ClaudeOnly.cited_web_search`).

WHAT IS KEPT. Only the result blocks the answer CITED: url and title from the result block,
`page_age` as given, and as the snippet the verbatim `cited_text` of that URL's citations, joined
and capped at SNIPPET_CAP. The model's own sentences are DISCARDED (they never reach a card); an
uncited result, a citation with no result block and every `encrypted_*` field are dropped; an answer
of NONE, or with no web citation, gives no rows. The rows then go through the jump search's own
digest, exactly like Brave's.

BUDGET (fail-CLOSED, no migration: `chat_usage_budget` buckets, uuid5 keys):
  1. claim one unit of the day's lookups (UPDATES_JUMP_CLAUDE_DAILY_LOOKUPS, keyed on the ET day);
     none left → this process latches the day used up, so `enabled()` reads False until the next
     ET day (or a higher daily setting);
  2. read the month's recorded spend (micro-dollars in `token_count` of the month bucket, keyed on
     the LAST day of the ET month, which the 7-day sweep never reaches mid-month); skip (unit
     refunded) when spend + the worst-case reserve would pass the cap;
  3. PRE-CHARGE the reserve with a raising RPC (failure → unit refunded, no call); a total past the
     cap after it (another instance charged first) → no call;
  4. call; afterwards add only max(0, actual − reserve) (actual = the usage at the model's list rates
     + $0.01 per billed search). Nothing is ever refunded after a call that may have billed; a call
     refused before any I/O (breaker open, an invalid request) refunds the day's unit only (the
     reserve cannot be taken back: `add_chat_tokens` floors at 0 — an over-count, never an under).
  Cancellation (a restart, the sweeper's shutdown) propagates and keeps every unit and charge.

FAILURES. Used-up credits or a 401 → off for the rest of the ET day in this process (one ERROR; the
provider opens the shared account breaker). A 403 / 400 / other refusal of the request → off for the
day for THIS feature only (one ERROR, no breaker: web search may simply not be enabled for the
organisation yet). A 429, an overload, a 5xx or a timeout → a PAUSE_SECONDS pause. `lookup` never
raises (except cancellation).

LOGGING. One counts-only line per lookup (`UPDATES_JUMP_LOOKUP`) — never the prompt, a title, a
snippet, a URL or a host — and, like every other surface of this module, no model or vendor name
(the provider's own CLAUDE_USAGE line is the one place the model is named). The Sentry scrubber drops
this module's frame locals (`log_redaction._WEB_RESULT_MODULES`).
"""

from __future__ import annotations

import asyncio
import logging
import math
import re
import time
import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from app.config import settings
from app.integrations import anthropic_client as ac
from app.services import llm_provider as lp
from app.services import web_result_hygiene as _hygiene
from app.services.chat_budget_service import ChatBudgetUnavailable, get_chat_budget_service

logger = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")

# ── The call ─────────────────────────────────────────────────────────────────
WEB_TOOL = ac.WEB_SEARCH_TOOL_TYPE              # "web_search_20260209" (pinned by a test)
#: The models this lookup may use: they take WEB_TOOL and are priced (`llm_provider.claude_rates`).
WEB_SEARCH_MODELS: Tuple[str, ...] = ("claude-sonnet-5-5", "claude-opus-5-5")
MAX_USES = 2
#: The answer's cap, thinking included (Opus 5.5 always thinks; Sonnet 5.5 runs `between_tools`).
MAX_TOKENS = 3000
#: Inside the provider's own CAYDEX_CLAUDE_REQUEST_TIMEOUT_SECONDS; ends in the typed timeout.
DEADLINE_SECONDS = 90.0
EFFORT = "low"
USAGE_TAG = "updates_jump_lookup"
USER_LOCATION: Dict[str, str] = {"type": "approximate", "country": "US", "timezone": "America/New_York"}

# ── Money ────────────────────────────────────────────────────────────────────
#: The reserve's input side: two searches' results in context, rounded well up (an answer above it
#: is topped up after the call, never lost).
WORST_INPUT_TOKENS = 60_000
#: $10 per 1,000 searches.
WEB_SEARCH_MICROS = 10_000
#: A 429 / overload / 5xx / timeout pauses every lookup in this process this long.
PAUSE_SECONDS = 600

# ── Rows ─────────────────────────────────────────────────────────────────────
SNIPPET_CAP = 300
MAX_ROWS = 10
_TITLE_CAP = 500
_PAGE_AGE_CAP = 64
_URL_CAP = 2048

# ── Budget buckets (uuid5 — the column is shared with chat turns' per-account rows) ──
_DAY_BUCKET = str(uuid.uuid5(uuid.NAMESPACE_URL, "caydex:updates:jump-claude-lookups"))
_MONTH_BUCKET = str(uuid.uuid5(uuid.NAMESPACE_URL, "caydex:updates:jump-claude-month"))

# ── The prompt: code-authored; the ONLY variable parts are the cleaned name and up / down ──
SYSTEM_PROMPT = (
    "You find the news behind a company's stock move. Search the web for news about the company "
    "from the last day, then answer in at most three short sentences, citing the pages you used. "
    "Use only pages that name this company. If no page explains the move, answer with the single "
    "word NONE."
)
USER_TEMPLATE = "Company: {name}\nIts stock is {word} sharply today. Which news explains the move? Cite the pages you used."
_DIRECTION_WORDS = {"up": "up", "down": "down"}
#: The name, once cleaned: letters (any script), digits, spaces and . & ' - only, at most 80
#: characters — the jump search's own query-name rule.
_QUERY_NAME_RE = re.compile(r"[^\W_](?:[^\W_]|[ .&'\-]){0,79}")
_SCOPE_RE = re.compile(r"[A-Z0-9.\-^=_]{1,24}")
_TRIGGER_RE = re.compile(r"[A-Za-z0-9_]{1,16}")
_REQUEST_ID_RE = re.compile(r"request_id=([A-Za-z0-9_\-]{1,80})")

# ── Statuses (LookupOutcome.status) ──────────────────────────────────────────
STATUS_OK = "ok"                          # cited pages came back
STATUS_NONE = "none"                      # the call ran; no cited web page
STATUS_DISABLED = "disabled"              # a gate is closed (counts name which)
STATUS_BAD_INPUT = "bad_input"            # an unusable name, direction or time — nothing claimed
STATUS_DAY_CAPPED = "day_capped"
STATUS_MONTH_CAPPED = "month_capped"
STATUS_BUDGET_UNAVAILABLE = "budget_unavailable"
STATUS_BREAKER_OPEN = "breaker_open"
STATUS_REFUSED = "refused"                # the model declined (billed)
STATUS_TRUNCATED = "truncated"            # cut at MAX_TOKENS (billed)
STATUS_BAD_ANSWER = "bad_answer"          # a paused turn or another stop reason (billed)
STATUS_CREDITS = "credits_exhausted"      # off for the day + the shared account breaker
STATUS_AUTH = "auth_failed"               # a 401: off for the day + the shared account breaker
STATUS_TOOL_REFUSED = "tool_refused"      # a 403: off for the day, this feature only
STATUS_REQUEST_REFUSED = "request_refused"  # another 4xx: off for the day, this feature only
STATUS_NOT_CONFIGURED = "not_configured"  # no key after all: off for the day
STATUS_INVALID_REQUEST = "invalid_request"  # refused before sending (a bug here): off for the day
STATUS_RATE_LIMITED = "rate_limited"      # a 429: paused
STATUS_UNAVAILABLE = "unavailable"        # an overload, a 5xx, a dropped connection: paused
STATUS_TIMEOUT = "timeout"                # past DEADLINE_SECONDS: paused
STATUS_ERROR = "error"                    # an unexpected error in this module (logged with the stack)


@dataclass(repr=False)
class LookupOutcome:
    """What one lookup did. `rows` are {"url", "title", "description", "page_age"} shaped like
    Brave's, for the jump search's digest; `counts` are for its counts-only log."""

    rows: List[Dict[str, Any]]
    status: str
    counts: Counter = field(default_factory=Counter)

    def __repr__(self) -> str:
        # Never a title, snippet or link: a repr is what an error report captures from a frame.
        return f"LookupOutcome(status={self.status!r}, rows={len(self.rows)})"


@dataclass
class _State:
    off_day: Optional[date] = None          # the ET day this process stopped looking up
    off_reason: str = ""
    paused_until: float = 0.0               # _clock()
    month_full: Optional[Tuple[str, int]] = None   # (month key, cap micros) seen full
    # (ET day, daily lookups) seen used up: without it `enabled()` stayed True after the day's
    # lookups were gone, so the jump search kept a no-cause episode "still searching" through two
    # spaced lookups that could only answer day_capped. Keyed on the limit too, so raising
    # UPDATES_JUMP_CLAUDE_DAILY_LOOKUPS mid-day reopens it (as a new cap reopens `month_full`).
    day_full: Optional[Tuple[date, int]] = None


_state = _State()
_logged_once: set = set()


def reset_state() -> None:
    """Tests only."""
    global _state
    _state = _State()
    _logged_once.clear()


def _clock() -> float:
    """The pause clock — its own function so a test can move it."""
    return time.monotonic()


def _error_once(key: str, msg: str, *args: Any, exc_info: bool = False) -> None:
    if key not in _logged_once:
        _logged_once.add(key)
        logger.error(msg, *args, exc_info=exc_info)


# ── Pure helpers ─────────────────────────────────────────────────────────────
def _aware(now: Any) -> Optional[datetime]:
    if not isinstance(now, datetime):
        return None
    return now if now.tzinfo is not None else now.replace(tzinfo=timezone.utc)


def et_day(now: datetime) -> date:
    return _aware(now).astimezone(ET).date()


def month_key(now: datetime) -> str:
    """The month bucket's day key: the LAST day of `now`'s ET month (ISO). Never in the past while
    the month runs, so `ChatBudgetService.cleanup_old_budget_rows` (7 days back) cannot delete it."""
    day = et_day(now)
    first_of_next = (day.replace(day=28) + timedelta(days=4)).replace(day=1)
    return (first_of_next - timedelta(days=1)).isoformat()


def configured_model() -> str:
    return str(settings.UPDATES_JUMP_CLAUDE_MODEL or "").strip()


def budget_micros() -> int:
    """UPDATES_JUMP_CLAUDE_MONTHLY_BUDGET_USD in micro-dollars; 0 for anything unusable."""
    usd = settings.UPDATES_JUMP_CLAUDE_MONTHLY_BUDGET_USD
    if isinstance(usd, bool) or not isinstance(usd, (int, float)) or not math.isfinite(usd) or usd <= 0:
        return 0
    return int(round(usd * 1_000_000))


def daily_lookups() -> int:
    n = settings.UPDATES_JUMP_CLAUDE_DAILY_LOOKUPS
    return n if isinstance(n, int) and not isinstance(n, bool) and n > 0 else 0


def model_problem(model: str) -> Optional[str]:
    """None when `model` may be used; else why not: not on WEB_SEARCH_MODELS, or no input/output price."""
    if model not in WEB_SEARCH_MODELS:
        return "model_not_allowed"
    rates = lp.claude_rates(model)
    if rates is None or rates[0] is None or rates[1] is None:
        return "model_unpriced"
    return None


def reserve_micros(model: str) -> Optional[int]:
    """The worst case one lookup is pre-charged: WORST_INPUT_TOKENS in + MAX_TOKENS out at the
    model's list rates, plus MAX_USES searches — rounded up. None for an unpriced model. About
    170,000 µ$ ($0.17) on Sonnet 5.5."""
    rates = lp.claude_rates(model) if isinstance(model, str) else None
    if rates is None or rates[0] is None or rates[1] is None:
        return None
    tokens = math.ceil((WORST_INPUT_TOKENS * rates[0] + MAX_TOKENS * rates[1]) / 1_000_000)
    return tokens + MAX_USES * WEB_SEARCH_MICROS


def actual_micros(model: Any, usage: Any, web_searches: Any) -> Optional[int]:
    """What a call cost: its tokens by kind at the model's rates, rounded up, plus $0.01 per billed
    search (MAX_USES when the count could not be read). None when the usage cannot be read or a kind
    with tokens has no rate — the reserve then stands."""
    rates = lp.claude_rates(model) if isinstance(model, str) and model else None
    if rates is None or not isinstance(usage, dict):
        return None
    numerator = 0
    for kind, rate in zip(ac.USAGE_KINDS, rates):
        n = usage.get(kind, 0)
        if isinstance(n, bool) or not isinstance(n, int) or n < 0:
            return None
        if n and rate is None:
            return None
        if n:
            numerator += n * rate
    searches = web_searches if isinstance(web_searches, int) and not isinstance(web_searches, bool) \
        and web_searches >= 0 else MAX_USES
    return math.ceil(numerator / 1_000_000) + searches * WEB_SEARCH_MICROS


def clean_query_name(query_name: Any) -> Optional[str]:
    """The name as it may be sent: whitespace collapsed, then letters, digits and . & ' - only, at
    most 80 characters. None otherwise — nothing is sent."""
    if not isinstance(query_name, str):
        return None
    name = " ".join(query_name.split())
    return name if _QUERY_NAME_RE.fullmatch(name) else None


def _direction_word(direction: Any) -> Optional[str]:
    return _DIRECTION_WORDS.get(direction) if isinstance(direction, str) else None


def build_prompt(query_name: Any, direction: Any) -> Optional[str]:
    """USER_TEMPLATE with the cleaned name and "up" / "down" — nothing else. None for an unusable
    name or direction."""
    name = clean_query_name(query_name)
    word = _direction_word(direction)
    if name is None or word is None:
        return None
    return USER_TEMPLATE.format(name=name, word=word)


def blocked_domains() -> Tuple[str, ...]:
    """The jump search's deny-list (social sites, search engines, price trackers), sorted, as the
    web tool's `blocked_domains` — the same hosts its digest drops, so no search is spent on them."""
    hosts = getattr(_hygiene, "DENIED_HOSTS", None)
    if hosts is None:
        hosts = _hygiene._DENIED_HOSTS
    return tuple(sorted(hosts))


def _cap(text: str, cap: int) -> str:
    return text if len(text) <= cap else text[: cap - 1].rstrip() + "…"


def _usable_url(url: Any) -> bool:
    return isinstance(url, str) and 0 < len(url) <= _URL_CAP and not any(ch.isspace() for ch in url)


def map_rows(answer: Any) -> Tuple[List[Dict[str, Any]], Counter]:
    """The rows a web-search answer gives: one per result block the answer CITED, in citation order,
    at most MAX_ROWS — {"url", "title" (the result block's), "description" (the URL's verbatim
    cited texts, whitespace collapsed, repeats dropped, joined and capped at SNIPPET_CAP),
    "page_age" (as given, or None)}. Dropped and counted: a result without a usable url or title,
    a repeated result, an uncited result, a citation whose URL has no result block (orphan), an empty
    cited text. An answer of NONE gives no rows. Never Claude's own sentences."""
    counts: Counter = Counter()
    if not isinstance(answer, dict):
        counts["answer_unreadable"] += 1
        return [], counts
    results = answer.get("results") if isinstance(answer.get("results"), list) else []
    citations = answer.get("citations") if isinstance(answer.get("citations"), list) else []
    counts["results"] = len(results)
    counts["citations"] = len(citations)
    errors = answer.get("search_errors")
    if isinstance(errors, dict):
        counts["search_errors"] = sum(n for n in errors.values() if isinstance(n, int) and not isinstance(n, bool))
    by_url: Dict[str, Dict[str, Any]] = {}
    for r in results:
        url = r.get("url") if isinstance(r, dict) else None
        title = r.get("title") if isinstance(r, dict) else None
        if not _usable_url(url) or not isinstance(title, str) or not title.strip():
            counts["result_dropped"] += 1
            continue
        if url in by_url:
            counts["result_repeat"] += 1
            continue
        age = r.get("page_age")
        by_url[url] = {"title": _cap(" ".join(title.split()), _TITLE_CAP),
                       "page_age": age.strip()[:_PAGE_AGE_CAP] if isinstance(age, str) and age.strip() else None}
    text = answer.get("text")
    if isinstance(text, str) and text.strip().strip(".").strip().upper() == "NONE":
        counts["answer_none"] += 1
        counts["uncited"] = len(by_url)
        return [], counts
    cited: Dict[str, List[str]] = {}
    for c in citations:
        url = c.get("url") if isinstance(c, dict) else None
        if not isinstance(url, str) or url not in by_url:
            counts["orphan_citation"] += 1
            continue
        raw = c.get("cited_text")
        quote = " ".join(raw.split()) if isinstance(raw, str) else ""
        if not quote:
            counts["empty_cited_text"] += 1
            continue
        parts = cited.setdefault(url, [])
        if quote not in parts:
            parts.append(quote)
    rows: List[Dict[str, Any]] = []
    for url, parts in cited.items():
        if len(rows) >= MAX_ROWS:
            counts["over_row_cap"] += 1
            continue
        page = by_url[url]
        rows.append({"url": url, "title": page["title"], "description": _cap(" ".join(parts), SNIPPET_CAP),
                     "page_age": page["page_age"]})
    counts["uncited"] = len(by_url) - len(cited)
    counts["rows"] = len(rows)
    return rows, counts


# ── Gates ────────────────────────────────────────────────────────────────────
def _gate_problem(now: datetime) -> Optional[str]:
    """None when a lookup may start now; else why not (a neutral code, for counts)."""
    if settings.UPDATES_JUMP_CLAUDE_FALLBACK_ENABLED is not True:
        return "switch_off"
    if settings.UPDATES_JUMP_WEB_SEARCH_ENABLED is not True:
        return "web_search_off"
    if not ac.is_configured():
        return "no_key"
    problem = model_problem(configured_model())
    if problem is not None:
        _error_once(f"model:{problem}",
                    "UPDATES_JUMP_LOOKUP off: the fallback model setting is %s — no lookups until it is fixed",
                    "not on the allowed web-search list" if problem == "model_not_allowed" else "without a price row")
        return problem
    cap = budget_micros()
    if cap <= 0:
        return "no_budget"
    if daily_lookups() <= 0:
        return "no_daily_lookups"
    if _state.off_day == et_day(now):
        return "off_today"
    if _state.day_full == (et_day(now), daily_lookups()):
        return "day_full"
    if _clock() < _state.paused_until:
        return "paused"
    if _state.month_full == (month_key(now), cap):
        return "month_full"
    if lp.breaker_open():
        return "breaker_open"
    return None


def enabled() -> bool:
    """Every gate, read NOW: the switch (off by default), the jump web search on, a key, an allowed
    and priced model, a monthly budget and daily lookups above 0, and this process not off for the
    ET day, not seen the day's lookups used up, not paused, not seen the month full, the shared
    account breaker closed. Never raises."""
    try:
        return _gate_problem(datetime.now(timezone.utc)) is None
    except Exception as e:  # noqa: BLE001 — a gate that cannot be read is closed
        _error_once(f"gate:{type(e).__name__}", "UPDATES_JUMP_LOOKUP gate unreadable (%s) — no lookups",
                    type(e).__name__, exc_info=True)
        return False


# ── The lookup ───────────────────────────────────────────────────────────────
def _shown(value: Any, pattern: re.Pattern) -> str:
    return value if isinstance(value, str) and pattern.fullmatch(value) else "?"


def _request_id(e: BaseException) -> str:
    m = _REQUEST_ID_RE.search(str(e))
    return m.group(1) if m else "-"


def _latch_off(day: date, reason: str, why: str, e: Optional[BaseException], *, stack: bool = False) -> None:
    """Off for the rest of the ET day in this process; one ERROR per day and reason."""
    _state.off_day = day
    _state.off_reason = reason
    _error_once(f"off:{day.isoformat()}:{reason}",
                "UPDATES_JUMP_LOOKUP off for the rest of %s ET in this process: %s (http=%s request_id=%s)",
                day.isoformat(), why, getattr(e, "status_code", None), _request_id(e) if e else "-",
                exc_info=stack)


def _pause(status: str, e: BaseException) -> None:
    _state.paused_until = _clock() + PAUSE_SECONDS
    logger.warning("UPDATES_JUMP_LOOKUP paused %ds: the fallback provider was %s (http=%s request_id=%s "
                   "billing_unknown=%s)", PAUSE_SECONDS, status.replace("_", " "),
                   getattr(e, "status_code", None), _request_id(e), bool(getattr(e, "billing_unknown", False)))


async def _refund_day(svc: Any, day: str, counts: Counter) -> None:
    await asyncio.to_thread(svc.refund_turn, _DAY_BUCKET, day=day)
    counts["day_unit_refunded"] = 1


async def _top_up(svc: Any, month: str, reserve: int, actual: Optional[int], counts: Counter) -> None:
    """Add what the call cost above its pre-charged reserve (nothing when it cost less or the cost is
    unknown — the reserve then stands)."""
    if actual is None:
        counts["actual_unknown"] = 1
        return
    counts["actual_micros"] = actual
    extra = actual - reserve
    if extra <= 0:
        return
    try:
        await asyncio.to_thread(svc.add_tokens_strict, _MONTH_BUCKET, extra, month)
        counts["topup_micros"] = extra
    except ChatBudgetUnavailable:
        logger.error("UPDATES_JUMP_LOOKUP spend under-recorded by %d micro-dollars for %s: the top-up "
                     "write failed (the call had already run)", extra, month)


async def _on_failure(e: ac.ClaudeException, *, svc: Any, day: str, today: date, month: str, reserve: int,
                      counts: Counter) -> str:
    """The status of a failed call, after its bookkeeping (refund, latch, pause, top-up)."""
    if isinstance(e, ac.ClaudeBreakerOpenException):
        await _refund_day(svc, day, counts)
        return STATUS_BREAKER_OPEN
    if isinstance(e, ac.ClaudeRequestInvalidException):
        await _refund_day(svc, day, counts)
        _latch_off(today, STATUS_INVALID_REQUEST, "a request this service built was refused before sending", e,
                   stack=True)
        return STATUS_INVALID_REQUEST
    if isinstance(e, ac.ClaudeConfigException):
        await _refund_day(svc, day, counts)
        _latch_off(today, STATUS_NOT_CONFIGURED, "the fallback provider has no key", e)
        return STATUS_NOT_CONFIGURED
    if isinstance(e, ac.ClaudeCreditsExhaustedException):
        _latch_off(today, STATUS_CREDITS, "the fallback provider's credits are used up", e)
        return STATUS_CREDITS
    if isinstance(e, ac.ClaudeAuthException):
        if getattr(e, "status_code", None) == 401:
            _latch_off(today, STATUS_AUTH, "the fallback provider refused the key (401)", e)
            return STATUS_AUTH
        _latch_off(today, STATUS_TOOL_REFUSED,
                   "the fallback provider refused the web search (403 — is web search enabled for the organisation?)", e)
        return STATUS_TOOL_REFUSED
    if isinstance(e, ac.ClaudeRateLimitException):
        _pause(STATUS_RATE_LIMITED, e)
        return STATUS_RATE_LIMITED
    if isinstance(e, ac.ClaudeTimeoutException):
        _pause(STATUS_TIMEOUT, e)
        return STATUS_TIMEOUT
    if isinstance(e, ac.ClaudeUnavailableException):
        _pause(STATUS_UNAVAILABLE, e)
        return STATUS_UNAVAILABLE
    billed = {ac.ClaudeRefusalException: STATUS_REFUSED, ac.ClaudeTruncatedException: STATUS_TRUNCATED,
              ac.ClaudeBadAnswerException: STATUS_BAD_ANSWER}
    for cls, status in billed.items():
        if isinstance(e, cls):
            searches = getattr(e, "web_searches", None)
            if isinstance(searches, int) and not isinstance(searches, bool):
                counts["web_searches"] = searches
            await _top_up(svc, month, reserve, actual_micros(getattr(e, "model", None), getattr(e, "usage", None),
                                                            searches), counts)
            logger.warning("UPDATES_JUMP_LOOKUP the fallback provider's answer was not usable (%s, request "
                           "billed)", status)
            return status
    _latch_off(today, STATUS_REQUEST_REFUSED, "the fallback provider refused the request", e)
    return STATUS_REQUEST_REFUSED


async def _lookup(*, scope: Any, query_name: Any, direction: Any, now: Any, counts: Counter) -> LookupOutcome:
    prompt = build_prompt(query_name, direction)
    when = _aware(now)
    if prompt is None or when is None:
        counts["bad_name" if clean_query_name(query_name) is None else
               "bad_direction" if _direction_word(direction) is None else "bad_time"] += 1
        return LookupOutcome([], STATUS_BAD_INPUT, counts)
    problem = _gate_problem(when)
    if problem is not None:
        counts[f"off_{problem}"] = 1
        return LookupOutcome([], STATUS_DISABLED, counts)
    model = configured_model()
    reserve = reserve_micros(model)
    cap = budget_micros()
    today = et_day(when)
    day = today.isoformat()
    month = month_key(when)
    svc = get_chat_budget_service()

    # 1. One of the day's lookups.
    limit = daily_lookups()
    try:
        claimed = await asyncio.to_thread(svc.try_claim_turn, _DAY_BUCKET, limit, day=day)
    except ChatBudgetUnavailable:
        return LookupOutcome([], STATUS_BUDGET_UNAVAILABLE, counts)
    if claimed == -1:
        # Latched for the rest of the ET day in this process, so `enabled()` reads False and the
        # jump search stops holding episodes open for lookups that cannot run. Fail closed: a unit
        # another lookup refunds after this is left unused until midnight ET.
        if _state.day_full != (today, limit):
            logger.info("UPDATES_JUMP_LOOKUP the day's %d lookups are used up for %s ET", limit, day)
        _state.day_full = (today, limit)
        return LookupOutcome([], STATUS_DAY_CAPPED, counts)
    counts["day_unit"] = 1

    # 2. The month's recorded spend, against the cap.
    try:
        spent = await asyncio.to_thread(svc.read_tokens, _MONTH_BUCKET, month)
    except ChatBudgetUnavailable:
        await _refund_day(svc, day, counts)
        return LookupOutcome([], STATUS_BUDGET_UNAVAILABLE, counts)
    if spent + reserve > cap:
        _state.month_full = (month, cap)
        await _refund_day(svc, day, counts)
        logger.info("UPDATES_JUMP_LOOKUP month %s is full: %d of %d micro-dollars spent, a lookup reserves %d",
                    month, spent, cap, reserve)
        return LookupOutcome([], STATUS_MONTH_CAPPED, counts)

    # 3. No pre-charge for a call that cannot run.
    if lp.breaker_open():
        await _refund_day(svc, day, counts)
        return LookupOutcome([], STATUS_BREAKER_OPEN, counts)

    # 4. Pre-charge the worst case.
    try:
        total = await asyncio.to_thread(svc.add_tokens_strict, _MONTH_BUCKET, reserve, month)
    except ChatBudgetUnavailable:
        await _refund_day(svc, day, counts)
        return LookupOutcome([], STATUS_BUDGET_UNAVAILABLE, counts)
    counts["reserve_micros"] = reserve
    if total > cap:
        # Another instance charged between the read and this charge: the cap holds strictly. This
        # reserve stays charged with no call (an over-count, never an under-count).
        _state.month_full = (month, cap)
        await _refund_day(svc, day, counts)
        return LookupOutcome([], STATUS_MONTH_CAPPED, counts)

    # 5. The call.
    only = lp.ClaudeOnly(model, breaker=lp.BREAKER_ACCOUNT)
    try:
        result = await only.cited_web_search(
            prompt, system_instruction=SYSTEM_PROMPT, max_uses=MAX_USES, max_tokens=MAX_TOKENS,
            deadline_seconds=DEADLINE_SECONDS, effort=EFFORT, blocked_domains=list(blocked_domains()),
            user_location=dict(USER_LOCATION), usage_tag=USAGE_TAG)
    except ac.ClaudeException as e:
        status = await _on_failure(e, svc=svc, day=day, today=today, month=month, reserve=reserve, counts=counts)
        return LookupOutcome([], status, counts)

    # 6. What it cost above the reserve, then the rows.
    searches = result.get("web_search_requests")
    if isinstance(searches, int) and not isinstance(searches, bool):
        counts["web_searches"] = searches
    else:
        counts["web_searches_unknown"] = 1
    await _top_up(svc, month, reserve, actual_micros(result.get("model") or model, result.get("usage"), searches),
                  counts)
    rows, mapped = map_rows(result)
    counts.update(mapped)
    return LookupOutcome(rows, STATUS_OK if rows else STATUS_NONE, counts)


_LOGGED_COUNTS = ("rows", "results", "citations", "uncited", "orphan_citation", "empty_cited_text",
                  "result_dropped", "search_errors", "web_searches", "reserve_micros", "actual_micros",
                  "topup_micros")


def _log_outcome(scope: Any, trigger: Any, direction: Any, outcome: LookupOutcome) -> None:
    """ONE counts-only line per lookup: never the prompt, a title, a snippet, a URL or a host."""
    extra = " ".join(f"{k}={outcome.counts[k]}" for k in _LOGGED_COUNTS if outcome.counts.get(k))
    gates = ",".join(sorted(k[4:] for k in outcome.counts if k.startswith("off_")))
    logger.info("UPDATES_JUMP_LOOKUP scope=%s trigger=%s direction=%s status=%s%s%s",
                _shown(scope, _SCOPE_RE), _shown(trigger, _TRIGGER_RE),
                _direction_word(direction) or "?", outcome.status,
                f" {extra}" if extra else "", f" gate={gates}" if gates else "")


async def lookup(*, scope: str, query_name: str, direction: str, now: datetime, trigger: str) -> LookupOutcome:
    """One fallback lookup for `scope`'s move in `direction` (see the module docstring). Never
    raises: every failure is a status, and an unexpected error is logged with its stack and returned
    as STATUS_ERROR. Cancellation propagates, keeping the day's unit and the month's charge."""
    counts: Counter = Counter()
    try:
        outcome = await _lookup(scope=scope, query_name=query_name, direction=direction, now=now, counts=counts)
    except asyncio.CancelledError:
        raise
    except Exception as e:  # noqa: BLE001 — the jump search must never fail on its fallback
        logger.error("UPDATES_JUMP_LOOKUP scope=%s status=error unexpected=%s", _shown(scope, _SCOPE_RE),
                     type(e).__name__, exc_info=True)
        outcome = LookupOutcome([], STATUS_ERROR, counts)
    _log_outcome(scope, trigger, direction, outcome)
    return outcome


__all__ = [
    "DEADLINE_SECONDS", "EFFORT", "LookupOutcome", "MAX_TOKENS", "MAX_USES", "PAUSE_SECONDS", "SNIPPET_CAP",
    "SYSTEM_PROMPT", "USER_LOCATION", "USER_TEMPLATE", "WEB_SEARCH_MICROS", "WEB_SEARCH_MODELS", "WEB_TOOL",
    "WORST_INPUT_TOKENS", "actual_micros", "blocked_domains", "build_prompt", "enabled", "lookup",
    "map_rows", "month_key", "reserve_micros",
]
