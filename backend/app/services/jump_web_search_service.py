"""Web search for WHY a stock is jumping — the Updates Insights jump mode (2026-10-28).

WHY. TestFlight, ORCL, Wed 2026-10-28: the stock rose ~12% from ~14:15 ET on its Investor
Day while its Insights card still summarised the previous day's news. When a ticker JUMPS (an
Unusual/Extreme move in the current session, `updates_materiality.jump_band`), the sweeper asks
this service to search the web for the reason; the pages it keeps are a generation input of
that ticker's card (a fenced block in the prompt) and the first entries of its Sources list,
which the app opens in its in-app browser.

OWNER DECISION 2026-10-28 ("i pick option 2, i don't think we break any terms"): ON at deploy,
and the card — shared by every watcher, served until a newer card for the ticker replaces it —
may hold text written from these pages and their titles and links. That replaces, FOR INSIGHTS
CARDS ONLY, the 2026-10-09 rule "no cross-user cache of web results". What still binds: web
results are never used to evaluate or tune the AI (Brave terms §3(b)(xiii)) — so the preview and
eval scripts force the switch off, and chat's grounding audit and refund judge skip any turn that
read a web-sourced card — with ONE narrow exception, an ACCEPTED RISK (owner 2026-11-13: "Accept the
risk"; the clause may cover it): the runtime grounding check of a web-only Insights card against the
pages it was written from (word checks + `_judge_web_only`), before it is shown (SYSTEM_DESIGN_
GUIDELINES §9b.10a). They are never kept anywhere else: `WebPage`'s repr carries no
title, snippet or link, and the Sentry scrubber drops the frame locals that hold them
(`log_redaction`).

WHAT IS SENT. Only a code-authored query built from the company's name as the FMP screener
spells it ("why is tesla stock up today" from "Tesla, Inc.") — never the client-writable
watchlist name, never a figure, never anything about a user. A name that is not, once cleaned,
letters, digits and . & ' - means no search. The fallback provider (below) is given the same
cleaned name and "up"/"down" only.

BUDGET (2026-11-27, owner: "do Brave 80 a day"). One durable, fail-CLOSED bucket in
`chat_usage_budget` (`UPDATES_JUMP_WEB_SEARCH_DAILY_CAP`, outside chat's 180, which it must never
eat), claimed before every call and refunded only when Brave proves the search did not run. The
claim's limit is PACED (`paced_limit`): 15% of the cap before 09:30 ET, 30% at the open rising
linearly to 100% at the close (13:00 on a half day), 100% after the close and on non-trading days —
on 2026-11-23 eighteen jumpers spent the whole bucket by 10:07 ET and the articles explaining CRWV
came at ~12:00 with nothing left. A paced refusal waits for the limit to grow and never latches the
day; only a refusal at the full cap does.

THE LADDER. Per (scope, direction) an EPISODE starts at the scope's first candidacy of the ET day
in that direction; its searches fall due at `LADDER_OFFSETS_SECONDS` (0, 20, 45, 90 min, 3 h, 5 h)
after it — at most one per sweep, a missed step skipped, never run in a burst — and at most
`MAX_SEARCHES_PER_SCOPE_PER_DAY` counted searches run per scope and ET day over all its episodes.
A sign flip starts the other direction's episode at once (or resumes it by its own clock) and
each direction keeps its own pages. Steps are skipped while the scope holds a COMPANY-cause move
card (`JumpCandidate.held_kind`); a market cause keeps searching. Candidates are served
fewest-searches-in-episode first, then biggest move, at most `MAX_SEARCHES_PER_REFRESH` a sweep.
A search that provably did not run (Brave's `not_run`) uses no step and is retried after
`NOT_RUN_RETRY_SECONDS`; an auth or plan refusal switches the search off for the rest of the ET
day in this process with ONE ERROR; a 429 holds every scope for its retry-after.

WHAT IS KEPT. https pages outside the deny-list (social, search engines, quote pages), published
in the last 24 h by their own date (or Brave's minute/hour age), naming the company or its symbol,
NOT describing the opposite move (an up/down lexicon matched only beside the company's own name or
symbol; a page saying both is kept) and NOT price talk alone (`news_insight_service.page_has_news`);
newest first (a page with only today's date after every page with a time); MERGED with the pages
the episode already kept — one per URL and per host, re-aged against now, at most `MAX_KEPT` (the
card lists every page it reads) — text cleaned and capped; pages of a set the card found no cause
in rank after every other page (`merge_pages(judged=)`). The kept set lives in THIS process for
the rest of the ET day (a restart starts over); a re-search that keeps nothing never erases it, and
it is returned only for the direction it was searched for. `refreshed_at` moves only when the set
GAINS a page no card has read (never when a page only ages out), and never past the `now` of the
sweep that read it (`context`) — the gate's one-shot trigger (`decide(web_refreshed_at=)`).

THE FALLBACK (owner 2026-11-27; ships OFF — `jump_claude_lookup_service.enabled()`, read at call
time). A second provider is asked when the primary search is spent at a later step (capped, or
paced for `FALLBACK_PACED_SECONDS`), failed or kept nothing at a later step, or the card written
from the kept pages found no cause (`note_move_outcome`) an hour into the episode — never in an
episode's first 20 minutes, at most `FALLBACK_LOOKUPS_PER_EPISODE` an hour apart, never while a
company cause is held, never in the two minutes before the close. It runs as a detached task (one
in flight per process, at most one started per sweep; `aclose` cancels it) and its rows go through
the SAME digest and merge, as `provider="fallback"` — a neutral code, never a vendor name.

LOGGING. One counts-only line per search (`UPDATES_JUMP_WEB_SEARCH`): never the query, a title, a
snippet or a host — `unknown_host` counts the kept pages from a publisher we do not name.
"""

from __future__ import annotations

import asyncio
import functools
import logging
import math
import re
import time
import unicodedata
import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import (
    AbstractSet, Any, Callable, Dict, FrozenSet, List, Optional, Sequence, Set, Tuple,
)

from app.config import settings
from app.integrations import brave_search
from app.services import insight_move
from app.services import news_insight_service as _nis
from app.services.chat_budget_service import (
    ChatBudgetUnavailable,
    budget_day,
    get_chat_budget_service,
)
from app.services.news_cache_service import is_crypto_scope
from app.services.news_insight_service import (
    _GENERIC_NAME_HEADS,
    _NAME_PREFIX_STOPS,
    MAX_SOURCE_URL_CHARS,
    MAX_WEB_SOURCES,
    company_name_variants,
    price_terms,
)
from app.services.web_result_hygiene import (
    _INJECTION_RE,
    _clean_text,
    _host_denied,
    _known_publisher,
    _publisher_for_host,
    _safe_https_url,
    parse_fallback_page_age,
    parse_page_age,
    parse_relative_age,
)
from app.utils.market_hours import ET, _close_minute, is_trading_day

logger = logging.getLogger(__name__)

# ── Sizes and cadence ────────────────────────────────────────────────────────
#: The card lists every page it reads (Privacy §3: "stored with the title and link of each web
#: page it used"), so no more are kept than its Sources show.
MAX_KEPT = MAX_WEB_SOURCES
#: Over-fetch: Brave bills per REQUEST (20 rows cost what 10 do), and the deny-list, the 24 h
#: window and the direction and price-only drops take a share.
_FETCH_COUNT = 20
#: When a search falls due, measured from the episode's first candidacy: 0, 20, 45, 90 min,
#: 3 h, 5 h. The explaining articles of a big move often appear hours after it starts.
LADDER_OFFSETS_SECONDS: Tuple[int, ...] = (0, 1200, 2700, 5400, 10800, 18000)
#: Counted searches per scope and ET day, over every episode (six steps plus a reversal's room).
MAX_SEARCHES_PER_SCOPE_PER_DAY = 8
#: Searches one sweep may start. A broad sell-off makes most watched names jumpers at once;
#: two at a time, a long list would hold that sweep's cards for minutes. The rest are searched
#: on later sweeps, fewest searches first, inside the same daily bucket.
MAX_SEARCHES_PER_REFRESH = 8
#: A search that provably did not run (refunded) is retried no sooner than this.
NOT_RUN_RETRY_SECONDS = 300
#: A 429's retry-after beyond this is not believed (an hour holds every scope already).
_MAX_RETRY_AFTER_SECONDS = 3600
#: The paced share of the daily cap before the open, and at the open (rising linearly to 1.0
#: at the close).
PACE_PREMARKET_SHARE = 0.15
PACE_OPEN_SHARE = 0.30
_REGULAR_OPEN_MINUTE = 9 * 60 + 30

# ── The fallback provider (owner 2026-11-27) ─────────────────────────────────
#: Never in an episode's first 20 minutes: explainers rarely exist yet.
FALLBACK_MIN_EPISODE_SECONDS = 1200
#: Trigger 3 — the card from the kept pages found no cause — only this far into the episode.
FALLBACK_NO_CAUSE_AFTER_SECONDS = 3600
#: Two lookups in one episode at least this far apart.
FALLBACK_SPACING_SECONDS = 3600
FALLBACK_LOOKUPS_PER_EPISODE = 2
#: Trigger 1's paced arm: a later step refused by pacing for this long.
FALLBACK_PACED_SECONDS = 1200
#: A lookup started this close to the regular close lands with no sweep left to use it (half day).
FALLBACK_CLOSE_GUARD_SECONDS = 120
MAX_FALLBACK_INFLIGHT = 1
#: An episode whose scope has not been a candidate for this long is no longer being searched.
EPISODE_IDLE_SECONDS = 1200
#: Which search found a page — neutral codes for counts only, never a vendor name.
PROVIDER_PRIMARY = "primary"
PROVIDER_FALLBACK = "fallback"

_MAX_PAGE_AGE = timedelta(hours=24)
#: A page dated a little ahead of our clock is clock skew; further ahead is a wrong date.
_FUTURE_SKEW = timedelta(minutes=10)
_TITLE_CAP = 160
_SNIPPET_CAP = 300
#: Brave's own timeout plus this bounds the whole call (the claim-steal window is 120 s).
_HARD_BOUND_SLACK = 2.0
_CONCURRENCY = 2
#: A symbol is matched as a whole uppercase token only at this length or more ("F", "BE"
#: and "ON" are words); shorter tickers rely on the company name.
_MIN_SYMBOL_MATCH_CHARS = 3

# The name a query carries, once cleaned: letters (any script), digits, spaces and . & ' - only —
# no URL, no email, no operator soup — at most 80 characters.
_QUERY_NAME_RE = re.compile(r"[^\W_](?:[^\W_]|[ .&'\-]){0,79}")
_SPACES_RE = re.compile(r"\s+")
# A share-class tail is not part of a company's name: "CoreWeave, Inc. Class A Common Stock".
_SHARE_CLASS_TAIL_RE = re.compile(
    r"\s+(?:class\s+[a-z](?![a-z])|(?:common|ordinary|preferred)\s+(?:stock|shares?)(?![a-z])|"
    r"american\s+depositary\s+(?:shares?|receipts?)(?![a-z])).*$",
    re.IGNORECASE,
)
# Slashed legal forms must reach the suffix list as its own tokens: "Novo Nordisk A/S" → "as",
# "Anheuser-Busch InBev SA/NV" → "sa nv", "Sendas Distribuidora S/A" → "sa".
_LEGAL_SLASH_RE = re.compile(r"(?<![^\W_])(?:SA/NV|S/A|N/V|A/S)(?![^\W_])", re.IGNORECASE)
_LEGAL_SLASH = {"sa/nv": "SA NV", "s/a": "SA", "n/v": "NV", "a/s": "AS"}
# Marks a brand carries that no headline or query needs: "Yum! Brands".
_BRAND_MARKS_RE = re.compile(r"[!®™©]")
# "JPMorgan Chase & Co." loses "co." to the suffix list and leaves a dangling "&".
_DANGLING_AND_RE = re.compile(r"(?:\s+(?:&|and))+$")

# A ONE-word name (an alias, the head of a longer name, or the first two words standing in for a
# longer one) makes a page about the company only beside a COMPANY CUE. Capitalisation cannot
# tell: a Title Case headline capitalises every word, so "Walmart Price Target Raised" would be
# about Target and "High-Beta Stocks Lead" about BETA Technologies (review 2026-10-29, round 2).
# The cue: a possessive, "stock"/"shares", a legal form, a listing in brackets — or "shares of".
_CUE_AFTER = (
    r"(?:['’]s(?![A-Za-z0-9])"
    r"|\s+(?i:stock|shares|inc\.?|corp\.?|corporation|company|co\.|ltd\.?|limited|plc|llc|"
    r"holdings?|group|technologies)(?![A-Za-z0-9])"
    r"|\s*\(\s*(?:(?i:nyse|nasdaq|amex|otc|tsx|lse)(?:\s*arca)?\s*:\s*)?[A-Z][A-Z.]{0,6}\s*\))"
)
_CUE_BEFORE = r"(?i:shares|stock)\s+(?i:of|in)\s+"

# ── Which way a page says the stock moved (2026-11-27) ───────────────────────
# PLUG, Wed 2026-11-25: the "up" search after its reversal kept pages about the MORNING's slide,
# and the card explained a +10% move with them. A page is dropped when it describes only the
# OPPOSITE move of THIS company: a move word counts only within `_DIRECTION_WINDOW` tokens of the
# company's own name or symbol, so a page naming another company's fall is kept, and a page
# saying both ("…rebounds after early slide" beside the name) is kept.
_DIRECTION_WINDOW = 3
# Verbs that are a share-price move wherever they sit beside the name.
_UP_MOVE_WORDS = frozenset({
    "rise", "rises", "rising", "rose", "risen", "jump", "jumps", "jumped", "jumping",
    "soar", "soars", "soared", "soaring", "surge", "surges", "surged", "surging",
    "climb", "climbs", "climbed", "climbing", "rally", "rallies", "rallied", "rallying",
    "rebound", "rebounds", "rebounded", "rebounding", "spike", "spikes", "spiked", "spiking",
    "pop", "pops", "popped", "popping", "leap", "leaps", "leaped", "leapt", "leaping",
    "rocketed", "rocketing", "skyrocket", "skyrockets", "skyrocketed", "skyrocketing",
    "zoom", "zooms", "zoomed", "zooming", "bounce", "bounces", "bounced", "bouncing",
})
_DOWN_MOVE_WORDS = frozenset({
    "fall", "falls", "falling", "fell", "fallen", "slide", "slides", "sliding", "slid",
    "plunge", "plunges", "plunged", "plunging", "tumble", "tumbles", "tumbled", "tumbling",
    "sink", "sinks", "sinking", "sank", "sunk", "slump", "slumps", "slumped", "slumping",
    "slip", "slips", "slipped", "slipping", "dive", "dives", "dived", "diving",
    "nosedive", "nosedives", "nosedived", "plummet", "plummets", "plummeted", "plummeting",
    "crater", "craters", "cratered", "cratering", "tanked", "tanking",
    "retreat", "retreats", "retreated", "retreating", "skid", "skids", "skidded",
    "sag", "sags", "sagged", "sagging", "sell-off", "sell-offs", "selloff", "selloffs", "rout",
})
# Words that are a move only right after a stock word ("shares gain", "stock is down") or right
# after the name with a figure or nothing behind them ("Plug Power drops 9%") — "gains approval",
# "drops lawsuit" and "sets up a plant" are news, not a move.
_UP_WEAK_WORDS = frozenset({
    "gain", "gains", "gained", "gaining", "advance", "advances", "advanced", "advancing",
    "higher", "up",
})
_DOWN_WEAK_WORDS = frozenset({
    "drop", "drops", "dropped", "dropping", "decline", "declines", "declined", "declining",
    "lower", "down", "lose", "loses", "losing", "lost", "shed", "sheds", "shedding",
})
_WEAK_LEADS = frozenset({
    "stock", "stocks", "share", "shares", "price", "is", "are", "was", "were", "trades",
    "trading", "traded", "moves", "moved", "moving", "edges", "edged", "closes", "closed",
    "opens", "opened", "ends", "ended", "heads", "headed", "ticks", "ticked", "inches",
    "inched", "drifts", "drifted", "sharply", "much",
})
_WEAK_TAILS = frozenset({"sharply", "further", "again", "more", "steeply", "today"})
# A move word right after one of these is about that noun, not the stock ("revenue falls",
# "price target rises", "oil slides") — or it is a noun itself ("a rise in orders").
_NOT_A_MOVE_LEADS = frozenset({
    "a", "an", "the", "its", "this", "that", "deep",
    "revenue", "revenues", "sales", "earnings", "eps", "income", "profit", "profits", "loss",
    "losses", "margin", "margins", "cost", "costs", "expenses", "spending", "debt", "cash",
    "burn", "output", "production", "orders", "bookings", "backlog", "guidance", "outlook",
    "forecast", "forecasts", "deliveries", "shipments", "demand", "inventory", "inventories",
    "subscribers", "users", "traffic", "headcount", "jobs", "volume", "volumes", "target",
    "targets", "estimate", "estimates", "rating", "ratings", "prices", "rates", "yields",
    "oil", "gold", "bitcoin", "futures", "index",
})
# "…rally fades" is the move ending, not a rise.
_FADE_WORDS = frozenset({
    "fade", "fades", "faded", "fading", "stall", "stalls", "stalled", "stalling", "cools",
    "cooled", "cooling", "falters", "faltered", "faltering", "fizzles", "fizzled", "wanes",
    "waned", "waning", "unwinds", "unwound", "reverses", "reversed",
})
# A company whose name ENDS in one of `_NOT_A_MOVE_LEADS` ("Target", "Barrick Gold"): a move word
# right after the name is the stock's move ("Target plunges after…") unless the word before the
# name makes it a noun phrase ("price target rises", "spot gold falls", "revenue target falls").
_NAME_AS_NOUN_LEADS = _NOT_A_MOVE_LEADS | frozenset({"price", "spot"})
# "falls short of estimates" is an earnings miss, not a move ("fell well short" too).
_FALL_FAMILY = frozenset({"fall", "falls", "falling", "fell", "fallen"})
_SHORT_DEGREE = frozenset({"well", "just", "far", "slightly", "way"})
# "sinks $2 billion into a partnership" is an investment, not a move: one of these verbs with a
# money amount before "into" (the tokenizer drops "$", so the amount is told by its scale word or
# suffix — "20% into the close" stays a move).
_INVEST_VERBS = frozenset({
    "sink", "sinks", "sinking", "sank", "sunk", "plunge", "plunges", "plunged", "plunging",
})
_MONEY_SCALES = frozenset({"million", "billion", "trillion", "mln", "bln", "mn", "bn"})
_MONEY_TOKEN_RE = re.compile(r"[0-9]+(?:k|m|mn|b|bn)")
_INVEST_WINDOW = 5
# A move word BEFORE the name with one of these between belongs to another subject's clause
# ("Datadog falls as Snowflake unveils …") — unless that subject is the stock itself ("Shares
# tumble as Snowflake cuts its outlook").
_LINK_WORDS = frozenset({
    "as", "while", "after", "when", "whereas", "but", "amid", "and", "though", "although",
    "since", "before",
})
_STOCK_SUBJECTS = frozenset({"stock", "stocks", "share", "shares"})
# Tokens: letters and digits joined by - ' ’ & ("sell-off", "AT&T", "Power's"). Linear: each
# repetition needs a joiner, so a run of letters is consumed once.
_TOKEN_RE = re.compile(r"[A-Za-z0-9]+(?:[-'’&][A-Za-z0-9]+)*")
# A clause ends at these marks; a move word never reaches across one ("…rises; Peer falls").
_CLAUSE_SPLIT_RE = re.compile(r"[;!?|\n]")
_DIRECTION_TEXT_CAP = 600
_DIRECTION_TOKEN_CAP = 120

# ── Budget bucket (uuid5 — the column is shared with chat turns' per-account rows) ──
_BUDGET_BUCKET = str(uuid.uuid5(uuid.NAMESPACE_URL, "caydex:updates:jump-web-search-budget"))


def _wall() -> datetime:
    """Wall-clock now (aware UTC) — its own function so a test can move it. Used where no sweep
    `now` exists: a fallback lookup's pages land minutes after the sweep that started it, and
    their `refreshed_at` must be later than the card that sweep wrote."""
    return datetime.now(timezone.utc)


def _aware(now: datetime) -> datetime:
    return now if now.tzinfo is not None else now.replace(tzinfo=timezone.utc)


@dataclass(frozen=True, repr=False)
class WebPage:
    """One kept page: everything the card needs, nothing it does not."""

    title: str
    snippet: Optional[str]
    url: str
    publisher: str
    published_at: datetime   # aware UTC
    #: Which search found the page: "primary" or "fallback" — a neutral code for logs and counts
    #: only, never a vendor name, never stored in a card or its sources.
    provider: str = PROVIDER_PRIMARY
    #: False when the page stated only its DAY (today, ET): `published_at` is then 00:00 ET and
    #: the card must never print a time for it.
    published_precise: bool = True

    def __repr__(self) -> str:
        # Never the title, snippet or link: a repr is what an error report captures from a
        # frame, and a web result may be kept only in the card it explains.
        return f"WebPage(publisher={self.publisher!r})"


@dataclass(frozen=True)
class JumpCandidate:
    """A jumper the sweeper asks about; served fewest-searches-first, then by ``score`` (the
    gate's `move_score`)."""

    scope: str
    name: Optional[str]          # the FMP screener's company name, never a watchlist one
    change_percent: float
    score: float = 0.0
    #: False for a candidate the gate added that is not a live jumper (a scope that jumped
    #: earlier today and now moves the other way).
    live: bool = True
    #: The `insight_move` kind of the move card the scope holds for THIS move today ("company",
    #: "market", "none"), None when none. A held COMPANY cause skips ladder steps and never buys a
    #: fallback lookup; a market cause keeps searching.
    held_kind: Optional[str] = None


@dataclass
class _Episode:
    """One (scope, direction) search episode of the ET day."""

    direction: str
    anchor: datetime                              # the first candidacy in this direction today
    next_step: int = 0                            # the first LADDER step not yet used
    searches: int = 0                             # counted primary searches in this episode
    retry_at: Optional[datetime] = None           # a not_run failure's retry floor
    paced_since: Optional[datetime] = None        # a due step refused by pacing since
    last_seen: Optional[datetime] = None          # the last sweep that listed the scope
    pages: Tuple[WebPage, ...] = ()
    #: When the kept set last GAINED a page no card has read yet (`_keep`); a set that only shrank
    #: (a page aged out) or regrouped pages already judged keeps it.
    refreshed_at: Optional[datetime] = None
    verdict: Optional[str] = None                 # the writer's verdict on `verdict_urls`
    verdict_urls: Optional[FrozenSet[str]] = None
    #: Every URL of a kept set the writer found no cause in (or would not accept one from) in this
    #: episode: such pages rank after every other page in a merge (`merge_pages(judged=)`).
    rejected_urls: FrozenSet[str] = frozenset()
    fallback_lookups: int = 0
    fallback_last: Optional[datetime] = None
    fallback_inflight: bool = False


@dataclass
class _ScopeDay:
    day: date
    searches: int = 0                             # counted primary searches, every episode
    current_direction: Optional[str] = None
    episodes: Dict[str, _Episode] = field(default_factory=dict)
    #: The last move card the writer reported WRITTEN for this scope today (any direction).
    last_written: Optional[insight_move.MoveTag] = None


def _clean_company_name(name: Any) -> Optional[str]:
    """The FMP company name as the press writes it: one line, no comma, no share-class tail,
    "A/S" as its suffix "AS". None when nothing is left."""
    if not isinstance(name, str):
        return None
    text = _BRAND_MARKS_RE.sub("", unicodedata.normalize("NFC", name))
    text = _SPACES_RE.sub(" ", text.replace(",", " ")).strip()
    text = _SHARE_CLASS_TAIL_RE.sub("", text)
    text = _LEGAL_SLASH_RE.sub(lambda m: _LEGAL_SLASH[m.group(0).lower()], text).strip()
    return text or None


def _fold(text: str) -> str:
    """Accents off ("Estée" → "Estee"), so a page and a name match however either spells it."""
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


def query_name_for(name: Any) -> Optional[str]:
    """The company name a query carries ("tesla" from "Tesla, Inc."), else None.

    The name is the FMP screener's, so it is not user text; cleaning it (through the legal-form
    rule the news subject filter uses) and then requiring letters, digits and . & ' - in at most
    80 characters is the second wall: no URL, no email, no operator soup. The whitelist reads the
    name the QUERY carries, not FMP's raw string — a comma in "Tesla, Inc." once kept about half
    of all large caps from ever being searched. The fallback provider gets exactly this name.
    """
    variants = company_name_variants(_clean_company_name(name))
    if not variants:
        return None
    query_name = _DANGLING_AND_RE.sub("", variants[0]).strip()
    if not _QUERY_NAME_RE.fullmatch(query_name):
        return None
    return query_name


def build_query(name: Any, direction: str) -> Optional[str]:
    """``why is <name> stock up|down today`` from the company name, else None — byte for byte the
    query the Privacy Policy describes (`query_name_for`)."""
    query_name = query_name_for(name)
    if query_name is None:
        return None
    word = "up" if direction == "up" else "down"
    return f"why is {query_name} stock {word} today"


def _et_day(now: datetime) -> date:
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return now.astimezone(ET).date()


def _name_patterns(scope: str, name: Optional[str]) -> List[re.Pattern]:
    """Whole-token patterns that make a page ABOUT this company, matched on accent-folded text.

    * ALONE: the full name when it has two or more words ("meta platforms", "american
      airlines"), in any case — a phrase is specific; a multi-word press name ("John Deere");
      the symbol, uppercase, when it is at least `_MIN_SYMBOL_MATCH_CHARS` long.
    * Only BESIDE A COMPANY CUE (`_CUE_AFTER` / `_CUE_BEFORE`): a one-word full name
      ("Target", "Oracle"), the head of a longer name ("Archer"), a one-word press name
      ("Disney", "Google": `widget_movers_service._LEAN_BRAND_ALIASES`), and the first two
      words of a longer name whose head is an ordinary word ("Rocket Lab" for Rocket Lab USA) —
      each as a name is written (capitalised, in capitals, or as FMP or the press spells it).
      A head that is an ordinary or sector word ("american", "general": `_GENERIC_NAME_HEADS`)
      never counts by itself.
    """
    from app.services.widget_movers_service import _LEAN_BRAND_ALIASES

    out: List[re.Pattern] = []
    cleaned = _fold(_clean_company_name(name) or "")
    spelled = {t.lower(): t for t in cleaned.split()}
    weak: set = set()

    def _written(words: List[str]) -> set:
        return {" ".join(spelled.get(w, w.capitalize()) for w in words),
                " ".join(w.capitalize() for w in words), " ".join(words).upper()}

    for i, variant in enumerate(company_name_variants(cleaned)):
        words = _DANGLING_AND_RE.sub("", variant).strip().split()
        if len(words) >= 2:
            out.append(_phrase(" ".join(words)))
            head = words[0]
            if (len(words) >= 3 and words[1] not in _NAME_PREFIX_STOPS
                    and head in _GENERIC_NAME_HEADS):
                weak |= _written(words[:2])
            continue
        if words and not (i > 0 and words[0] in _GENERIC_NAME_HEADS):
            weak |= _written(words)
    symbol = (scope or "").strip().upper()
    for alias in _LEAN_BRAND_ALIASES.get(symbol, ()):
        alias = _fold(alias)
        if " " in alias:
            out.append(_phrase(alias))
        else:
            weak |= {alias} if alias.isupper() else {alias, alias.capitalize(), alias.upper()}
    if weak:
        alternation = "|".join(re.escape(f) for f in sorted(weak, key=len, reverse=True))
        out.append(re.compile(
            rf"(?<![A-Za-z0-9])(?:{alternation}){_CUE_AFTER}"
            rf"|{_CUE_BEFORE}(?:{alternation})(?![A-Za-z0-9])"
        ))
    if len(symbol) >= _MIN_SYMBOL_MATCH_CHARS and symbol.isalnum():
        out.append(re.compile(rf"(?<![A-Za-z0-9]){re.escape(symbol)}(?![A-Za-z0-9])"))
    return out


def _phrase(text: str) -> re.Pattern:
    return re.compile(rf"(?<![A-Za-z0-9]){re.escape(text)}(?![A-Za-z0-9])", re.IGNORECASE)


def _strip_possessive(token: str) -> str:
    return token[:-2] if token[-2:] in ("'s", "’s") else token


def _direction_subjects(terms: Sequence[str]) -> List[Tuple[Tuple[str, ...], bool]]:
    """``(tokens, exact)`` for each name or symbol in ``terms``, longest first. A one-token,
    all-capitals term (the symbol) must match in exact case, so "ON" never matches "on"; every
    other term matches case-blind (headlines are Title Case)."""
    out: List[Tuple[Tuple[str, ...], bool]] = []
    for term in terms:
        if not isinstance(term, str) or not term.strip():
            continue
        words = [_strip_possessive(w) for w in _TOKEN_RE.findall(_fold(term))]
        if not words:
            continue
        if len(words) == 1 and words[0].isupper() and len(words[0]) >= 2:
            out.append(((words[0],), True))
        elif len(words) > 1 or len(words[0]) >= 2:
            out.append((tuple(w.lower() for w in words), False))
    out = list(dict.fromkeys(out))
    out.sort(key=lambda s: -len(s[0]))
    return out


def _noun_lead(lowered: Sequence[str], spans: Sequence[Tuple[int, int]], j: int) -> bool:
    """The word before ``j`` is a noun the move word describes ("revenue falls"). When that word
    ENDS the company's own name ("Target plunges", "Barrick Gold tumbles") it is the stock, unless
    the word before the name makes the name a noun ("price target rises", "spot gold falls")."""
    owner = next((a for a, b in spans if b == j), None)
    if owner is None:
        return True
    return owner > 0 and lowered[owner - 1] in _NAME_AS_NOUN_LEADS


def _other_subject_before(lowered: Sequence[str], j: int, a: int) -> bool:
    """A move word at ``j`` before the name starting at ``a`` belongs to another subject: a
    linking word sits between them and the word before the move is not the stock itself."""
    if not any(lowered[t] in _LINK_WORDS for t in range(j + 1, a)):
        return False
    return j > 0 and lowered[j - 1] not in _STOCK_SUBJECTS


def _falls_short(lowered: Sequence[str], j: int) -> bool:
    """"falls short" / "fell well short" — a miss against a figure, not a move."""
    k = j + 1
    if k < len(lowered) and lowered[k] in _SHORT_DEGREE:
        k += 1
    return k < len(lowered) and lowered[k] == "short"


def _invests_into(lowered: Sequence[str], j: int) -> bool:
    """"sinks $2 billion into …": an amount of money, then "into", within `_INVEST_WINDOW`."""
    if j + 1 >= len(lowered) or not lowered[j + 1][:1].isdigit():
        return False
    money = False
    for k in range(j + 1, min(len(lowered), j + 1 + _INVEST_WINDOW)):
        token = lowered[k]
        if token == "into":
            return money
        if token in _MONEY_SCALES or _MONEY_TOKEN_RE.fullmatch(token):
            money = True
    return False


def _clause_directions(
    clause: str, subjects: List[Tuple[Tuple[str, ...], bool]],
) -> Set[str]:
    tokens = [_strip_possessive(t) for t in _TOKEN_RE.findall(clause)][:_DIRECTION_TOKEN_CAP]
    lowered = [t.lower() for t in tokens]
    n = len(tokens)
    spans: List[Tuple[int, int]] = []
    i = 0
    while i < n:
        width = 0
        for words, exact in subjects:
            k = len(words)
            if i + k <= n and tuple(tokens[i:i + k] if exact else lowered[i:i + k]) == words:
                width = k
                break
        if width:
            spans.append((i, i + width))
            i += width
        else:
            i += 1
    if not spans:
        return set()
    covered = {j for a, b in spans for j in range(a, b)}
    found: Set[str] = set()
    for j, word in enumerate(lowered):
        if j in covered:
            continue
        near = [(a, b) for a, b in spans
                if 1 <= ((a - j) if j < a else (j - b + 1)) <= _DIRECTION_WINDOW]
        if not near:
            continue
        prev = lowered[j - 1] if j > 0 else ""
        nxt = lowered[j + 1] if j + 1 < n else ""
        if prev in _NOT_A_MOVE_LEADS and _noun_lead(lowered, spans, j):
            continue
        if all(j < a and _other_subject_before(lowered, j, a) for a, _b in near):
            continue
        if word in _UP_MOVE_WORDS:
            # "…rally fades": the move is ending, which is not a rise.
            found.add("down" if nxt in _FADE_WORDS else "up")
        elif word in _DOWN_MOVE_WORDS:
            if word in _FALL_FAMILY and _falls_short(lowered, j):
                continue
            if word in _INVEST_VERBS and _invests_into(lowered, j):
                continue
            found.add("down")
        elif word in _UP_WEAK_WORDS or word in _DOWN_WEAK_WORDS:
            after_name = (j - 1) in covered and (not nxt or nxt[0].isdigit() or nxt in _WEAK_TAILS)
            if prev in _WEAK_LEADS or after_name:
                found.add("up" if word in _UP_WEAK_WORDS else "down")
    return found


def move_directions(text: Any, terms: Sequence[str]) -> FrozenSet[str]:
    """Which ways (``{"up", "down"}``) ``text`` says the company named by ``terms`` moved: a move
    word counts only within `_DIRECTION_WINDOW` tokens of the company's own name or symbol, inside
    one clause, and never right after a noun it would describe instead ("revenue falls", "a rise
    in orders"). Pure; never raises; bounded input."""
    if not isinstance(text, str) or not text.strip():
        return frozenset()
    subjects = _direction_subjects(terms)
    if not subjects:
        return frozenset()
    found: Set[str] = set()
    for clause in _CLAUSE_SPLIT_RE.split(_fold(text[:_DIRECTION_TEXT_CAP])):
        found |= _clause_directions(clause, subjects)
    return frozenset(found)


def describes_opposite_move(text: Any, direction: str, terms: Sequence[str]) -> bool:
    """True when ``text`` describes only the OPPOSITE of ``direction`` ("up"/"down") for the
    company: an opposite move beside its name and no same-way move. A text saying both is not."""
    if direction not in insight_move.MOVE_DIRECTIONS:
        return False
    found = move_directions(text, terms)
    opposite = insight_move.DIRECTION_DOWN if direction == insight_move.DIRECTION_UP else (
        insight_move.DIRECTION_UP)
    return opposite in found and direction not in found


def _page_terms(scope: str, name: Optional[str]) -> List[str]:
    """The company's own names and symbol for the direction and price-only checks: the price
    rule's terms (`news_insight_service.price_terms`) plus the press names the subject filter
    knows (`widget_movers_service._LEAN_BRAND_ALIASES`)."""
    from app.services.widget_movers_service import _LEAN_BRAND_ALIASES

    terms = list(price_terms(scope, _clean_company_name(name)))
    terms.extend(_LEAN_BRAND_ALIASES.get((scope or "").strip().upper(), ()))
    return list(dict.fromkeys(t for t in terms if isinstance(t, str) and t.strip()))


def _has_news(title: str, snippet: Optional[str], terms: Sequence[str]) -> bool:
    """`news_insight_service.page_has_news` (its public alias when present): whether the page says
    anything beyond the price. A raise keeps the page (the writer's own guards still run) and is
    logged — never silently."""
    check = getattr(_nis, "page_has_news", None) or _nis._page_has_news
    try:
        return bool(check({"title": title, "snippet": snippet or ""}, terms))
    except Exception as e:  # noqa: BLE001 — a page check must not drop a whole answer
        logger.warning("UPDATES_JUMP_WEB_SEARCH price-only check raised (%s) — page kept",
                       type(e).__name__)
        return True


_AgeParser = Callable[[Dict[str, Any], datetime], Tuple[Optional[datetime], bool]]


def _primary_age(row: Dict[str, Any], now: datetime) -> Tuple[Optional[datetime], bool]:
    """Brave: the page's own ISO instant, else its minute/hour age — always a time of day."""
    published = parse_page_age(row.get("page_age")) or parse_relative_age(row.get("age"), now)
    return published, True


def _fallback_age(row: Dict[str, Any], now: datetime) -> Tuple[Optional[datetime], bool]:
    """The fallback provider: an instant, a relative age, or today's date alone (not precise)."""
    return parse_fallback_page_age(row.get("page_age"), now)


def _rank(pages: Any) -> List[WebPage]:
    """Newest first; a page with only today's date after every page with a time (its real time is
    unknown, so it cannot claim to be newer). Stable: the provider's own rank breaks ties."""
    return sorted(pages, key=lambda p: (not p.published_precise, -p.published_at.timestamp()))


def _bare_host(url: str) -> str:
    safe = _safe_https_url(url)
    host = safe[1] if safe is not None else url
    return host[4:] if host.startswith("www.") else host


def _one_per_host(pages: Sequence[WebPage], drops: Optional[Counter] = None) -> List[WebPage]:
    out: List[WebPage] = []
    hosts: Set[str] = set()
    for page in pages:
        host = _bare_host(page.url)
        if host in hosts:
            if drops is not None:
                drops["same_host"] += 1
            continue
        hosts.add(host)
        out.append(page)
    return out


def _digest_all(
    raw: Any, scope: str, name: Optional[str], now: datetime, *,
    direction: Optional[str], provider: str, parse_age: _AgeParser,
) -> Tuple[List[WebPage], Counter]:
    """Every page worth keeping from one answer, ranked, one per host — and why the others were
    dropped. `digest` caps it; the search merges all of it with the episode's kept set."""
    drops: Counter = Counter()
    rows = raw.get("results") if isinstance(raw, dict) else None
    if not isinstance(rows, list):
        return [], drops
    now = _aware(now)
    about = _name_patterns(scope, name)
    terms: Optional[List[str]] = None
    survivors: List[WebPage] = []
    for row in rows:
        if not isinstance(row, dict):
            drops["malformed"] += 1
            continue
        safe = _safe_https_url(row.get("url"))
        if safe is None:
            drops["url"] += 1
            continue
        url, host, path = safe
        if len(url) > MAX_SOURCE_URL_CHARS:
            # The card stores links clipped at this length; a page it cannot link is not read.
            drops["url"] += 1
            continue
        if _host_denied(host, path):
            drops["denied"] += 1
            continue
        title = _clean_text(row.get("title"), _TITLE_CAP)
        if not title:
            drops["no_title"] += 1
            continue
        snippet = _clean_text(row.get("description"), _SNIPPET_CAP)
        if _INJECTION_RE.search(title) or (snippet and _INJECTION_RE.search(snippet)):
            drops["injection"] += 1
            continue
        published, precise = parse_age(row, now)
        if published is None:
            drops["no_age"] += 1
            continue
        if published > now + _FUTURE_SKEW:
            drops["future"] += 1
            continue
        if now - published > _MAX_PAGE_AGE:
            drops["stale"] += 1
            continue
        text = _fold(f"{title} {snippet or ''}")
        if not about or not any(p.search(text) for p in about):
            drops["off_subject"] += 1
            continue
        if terms is None:
            terms = _page_terms(scope, name)
        if direction in insight_move.MOVE_DIRECTIONS and _page_opposite(
                title, snippet, direction, terms):
            drops["opposite_direction"] += 1
            continue
        if not _has_news(title, snippet, terms):
            drops["price_only"] += 1
            continue
        survivors.append(WebPage(
            title=title, snippet=snippet, url=url,
            publisher=_publisher_for_host(host), published_at=published,
            provider=provider, published_precise=bool(precise),
        ))
    return _one_per_host(_rank(survivors), drops), drops


def _page_opposite(title: str, snippet: Optional[str], direction: str, terms: Sequence[str]) -> bool:
    found = move_directions(title, terms) | move_directions(snippet or "", terms)
    opposite = insight_move.DIRECTION_DOWN if direction == insight_move.DIRECTION_UP else (
        insight_move.DIRECTION_UP)
    return opposite in found and direction not in found


def digest(
    raw: Any, scope: str, name: Optional[str], now: datetime, *,
    direction: Optional[str] = None, provider: str = PROVIDER_PRIMARY,
    parse_age: Optional[_AgeParser] = None,
) -> Tuple[List[WebPage], Counter]:
    """The pages worth keeping from one answer (at most `MAX_KEPT`, newest first, one per host),
    and why the others were dropped.

    Every row must be an https page outside the deny-list, titled, free of injection text, dated
    within 24 h, about the company, NOT describing only the opposite of ``direction`` (when one is
    given) and NOT price talk alone. ``provider`` labels the pages ("primary" = the primary
    search, "fallback" = the second provider) and picks the date reader unless ``parse_age``
    is given. Pure apart from the regexes it compiles; ``now`` must be aware.
    """
    if parse_age is None:
        parse_age = _fallback_age if provider == PROVIDER_FALLBACK else _primary_age
    pages, drops = _digest_all(raw, scope, name, now, direction=direction, provider=provider,
                               parse_age=parse_age)
    if len(pages) > MAX_KEPT:
        drops["surplus"] += len(pages) - MAX_KEPT
    return pages[:MAX_KEPT], drops


def _better_copy(a: WebPage, b: WebPage) -> bool:
    """Two copies of one URL: a page with a time beats one with only a date, then the longer
    snippet."""
    return (a.published_precise, len(a.snippet or "")) > (b.published_precise, len(b.snippet or ""))


def merge_pages(
    old: Sequence[WebPage], new: Sequence[WebPage], now: datetime, *,
    judged: AbstractSet[str] = frozenset(),
) -> Tuple[WebPage, ...]:
    """The episode's kept set after a new answer: the new pages and the kept ones together, one
    copy per URL (`_better_copy`), re-aged against ``now`` (older than 24 h or dated ahead of it
    → gone), one per host, at most `MAX_KEPT`, newest first. Pure.

    A page in ``judged`` (a URL of a set the writer found no cause in) is chosen only after every
    other page — so an answer that is older, or carries only a date, still replaces pages known to
    explain nothing, and wins their host — and among judged pages the ones already kept come
    first, so finding judged pages again never swaps one judged set for another."""
    now = _aware(now)
    best: Dict[str, WebPage] = {}
    for page in (*new, *old):
        if not isinstance(page, WebPage):
            continue
        if page.published_at > now + _FUTURE_SKEW or now - page.published_at > _MAX_PAGE_AGE:
            continue
        current = best.get(page.url)
        if current is None or _better_copy(page, current):
            best[page.url] = page
    if not judged:
        return tuple(_one_per_host(_rank(best.values()))[:MAX_KEPT])
    kept_urls = {p.url for p in old if isinstance(p, WebPage)}
    unjudged = _rank(p for p in best.values() if p.url not in judged)
    # Stable: within each group the rank order holds.
    seen = sorted(_rank(p for p in best.values() if p.url in judged),
                  key=lambda p: p.url not in kept_urls)
    chosen = _one_per_host([*unjudged, *seen])[:MAX_KEPT]
    return tuple(_rank(chosen))


def paced_limit(cap: Any, now: datetime) -> int:
    """How much of the daily cap may be spent by ``now`` (owner 2026-11-27: spend must not be
    front-loaded): 15% before 09:30 ET, 30% at the open rising linearly to 100% at the close
    (13:00 ET on a half day, `market_hours`), 100% after the close and on a non-trading day.
    Never below 1 for a positive cap; 0 for a cap of 0 or less, or one that is not a number."""
    if isinstance(cap, bool):
        return 0
    try:
        whole = int(cap)
    except (TypeError, ValueError, OverflowError):
        return 0
    if whole <= 0:
        return 0
    et = _aware(now).astimezone(ET)
    day = et.date()
    if not is_trading_day(day):
        return whole
    minute = et.hour * 60 + et.minute + et.second / 60.0
    close = _close_minute(day)
    if minute < _REGULAR_OPEN_MINUTE:
        share = PACE_PREMARKET_SHARE
    elif minute >= close:
        share = 1.0
    else:
        share = PACE_OPEN_SHARE + (1.0 - PACE_OPEN_SHARE) * (
            (minute - _REGULAR_OPEN_MINUTE) / (close - _REGULAR_OPEN_MINUTE))
    return max(1, min(whole, int(whole * share + 1e-9)))


def _near_close(now: datetime) -> bool:
    """Within `FALLBACK_CLOSE_GUARD_SECONDS` BEFORE today's regular close (13:00 on a half day)."""
    et = _aware(now).astimezone(ET)
    day = et.date()
    if not is_trading_day(day):
        return False
    close_at = datetime(day.year, day.month, day.day, tzinfo=ET) + timedelta(
        minutes=_close_minute(day))
    return close_at - timedelta(seconds=FALLBACK_CLOSE_GUARD_SECONDS) <= et < close_at


def _due_step(ep: _Episode, now: datetime) -> Optional[int]:
    """The LATEST ladder step due by ``now`` that is not used yet, or None. Running it marks every
    earlier unused step used too: missed steps are skipped, never run in a burst."""
    elapsed = (now - ep.anchor).total_seconds()
    due: Optional[int] = None
    for k in range(ep.next_step, len(LADDER_OFFSETS_SECONDS)):
        if LADDER_OFFSETS_SECONDS[k] <= elapsed:
            due = k
        else:
            break
    return due


_NO_CAUSE_VERDICTS = frozenset({insight_move.VERDICT_NO_CAUSE, insight_move.VERDICT_UNSUPPORTED})


def _no_cause_now(ep: _Episode) -> bool:
    """The writer found no cause in (or would not accept one from) the CURRENT kept set: its
    verdict on exactly this set — or, for a set that only shrank or regrouped pages it already saw
    (a page aged out, a judged page came back), every page of it was in a set judged that way.
    An EMPTY set counts only through a verdict on it (a card written from the news alone found no
    cause). False for a verdict of None (a model or judge outage) or any unjudged page."""
    current = frozenset(p.url for p in ep.pages)
    if ep.verdict_urls is not None and ep.verdict_urls == current:
        return ep.verdict in _NO_CAUSE_VERDICTS
    return bool(current) and current <= ep.rejected_urls


def _full_of_unjudged_precise(ep: _Episode) -> bool:
    """The kept set is full of pages with a time that no card found causeless: a fallback page,
    which mostly carries only its date (`parse_fallback_page_age`), could displace none of them."""
    return len(ep.pages) >= MAX_KEPT and all(
        p.published_precise and p.url not in ep.rejected_urls for p in ep.pages)


def _fallback_module() -> Any:
    """The fallback lookup module, imported lazily: only this service may import it, and nothing
    loads it until a fallback is considered. None (logged once) when it cannot be imported."""
    global _fallback_import_logged
    try:
        from app.services import jump_claude_lookup_service as claude
    except ImportError as e:
        if not _fallback_import_logged:
            _fallback_import_logged = True
            logger.warning(
                "UPDATES_JUMP_WEB_SEARCH fallback module unavailable (%s) — no fallback lookup",
                type(e).__name__,
            )
        return None
    return claude


_fallback_import_logged = False


def _fallback_enabled(module: Any) -> bool:
    try:
        return bool(module.enabled())
    except Exception as e:  # noqa: BLE001 — a gate that raises is closed
        logger.warning("UPDATES_JUMP_WEB_SEARCH fallback gate raised (%s) — no fallback lookup",
                       type(e).__name__)
        return False


_SAFE_CODE_RE = re.compile(r"[a-z0-9_]{1,40}")


def _safe_code(value: Any) -> str:
    """A status code fit for a counts-only log line, else "unknown"."""
    return value if isinstance(value, str) and _SAFE_CODE_RE.fullmatch(value) else "unknown"


def _safe_counts(value: Any) -> Dict[str, int]:
    if not isinstance(value, dict):
        return {}
    return {k: int(v) for k, v in value.items()
            if isinstance(k, str) and _SAFE_CODE_RE.fullmatch(k)
            and isinstance(v, int) and not isinstance(v, bool)}


class JumpWebSearchService:
    """Process-local state for the day's jump searches; see the module docstring."""

    def __init__(self) -> None:
        self._days: Dict[str, _ScopeDay] = {}
        self._capped_day: Optional[str] = None
        #: (budget day, paced limit) the store last refused: no RPC until the limit grows.
        self._paced_refused: Optional[Tuple[str, int]] = None
        #: The ET day the primary search refused this service (auth / plan): off until it ends.
        self._off_day: Optional[date] = None
        #: A 429's retry-after, held for every scope.
        self._rate_limited_until: Optional[datetime] = None
        self._fallback_tasks: Set["asyncio.Task[None]"] = set()

    # ── reads ────────────────────────────────────────────────────────────

    @staticmethod
    def enabled() -> bool:
        """The switch AND a configured key — read at call time, never cached."""
        return bool(getattr(settings, "UPDATES_JUMP_WEB_SEARCH_ENABLED", False)) and (
            brave_search.is_configured()
        )

    def context(
        self, scope: str, now: datetime, *, direction: Optional[str],
    ) -> Tuple[Tuple[WebPage, ...], Optional[datetime]]:
        """This process's kept pages for ``scope`` and ``direction`` ("up"/"down", the sign of
        today's move now) on the current ET day, and when that set last changed. Empty on another
        day, after a restart, when nothing was kept — and for the other direction: pages explaining
        a rally must never be presented under a fall.

        Only the sweeper calls this, once per sweep after `refresh`, and the card it writes from
        the pages is stamped with the same ``now``. A fallback lookup that landed during that
        sweep is stamped with the WALL clock (`_wall`), later than ``now``: the stored stamp is
        clamped to ``now`` here, or every later sweep would read the set as newer than the card
        written from it and pay for a second card from the same pages. A set landing after this
        read keeps its later stamp and triggers the next sweep."""
        st = self._days.get(scope)
        if st is None or st.day != _et_day(now) or direction is None:
            return (), None
        ep = st.episodes.get(direction)
        if ep is None or not ep.pages:
            return (), None
        now = _aware(now)
        if ep.refreshed_at is not None and ep.refreshed_at > now:
            ep.refreshed_at = now
        return ep.pages, ep.refreshed_at

    def note_move_outcome(self, scope: str, now: datetime, outcome: Any) -> None:
        """Record the writer's verdict on one move-mode attempt (`insight_move.MoveOutcome`).

        The verdict is kept for the episode of ``outcome.direction`` only when ``outcome.page_urls``
        is exactly that episode's kept set — a verdict about an older set says nothing about the
        pages kept now. A no-cause or unsupported verdict also marks each of those pages judged
        (`_Episode.rejected_urls`) for the rest of the episode. A WRITTEN card is remembered with
        its direction, so a company cause held for the other direction (a reversal) never stops
        this direction's search. Never raises."""
        if not isinstance(outcome, insight_move.MoveOutcome):
            return
        now = _aware(now)
        st = self._days.get(scope)
        if st is None or st.day != _et_day(now):
            return
        if (outcome.written and outcome.kind in insight_move.MOVE_KINDS
                and outcome.direction in insight_move.MOVE_DIRECTIONS):
            st.last_written = insight_move.MoveTag(kind=outcome.kind, direction=outcome.direction)
        if outcome.direction not in insight_move.MOVE_DIRECTIONS:
            return
        ep = st.episodes.get(outcome.direction)
        if ep is None:
            return
        urls = frozenset(u for u in (outcome.page_urls or ()) if isinstance(u, str))
        if urls != frozenset(p.url for p in ep.pages):
            return
        ep.verdict = outcome.verdict
        ep.verdict_urls = urls
        if outcome.verdict in _NO_CAUSE_VERDICTS:
            ep.rejected_urls = ep.rejected_urls | urls

    def episode_exhausted(self, scope: str, now: datetime, *, direction: Optional[str]) -> bool:
        """True when nothing more will be searched for this move today: the ladder is done, the
        scope's limit (or the day's bucket, or the provider) is spent, or the scope stopped being
        a candidate — and no fallback lookup is in flight or still due. True as well when the
        search is off, the scope is a coin, there is no usable direction, or no episode exists
        today (the scope is not being searched). Never raises."""
        if not self.enabled() or is_crypto_scope(scope):
            return True
        if direction not in insight_move.MOVE_DIRECTIONS:
            return True
        now = _aware(now)
        st = self._days.get(scope)
        if st is None or st.day != _et_day(now):
            return True
        ep = st.episodes.get(direction)
        if ep is None:
            return True
        if ep.fallback_inflight:
            return False
        if ep.last_seen is None or (now - ep.last_seen).total_seconds() > EPISODE_IDLE_SECONDS:
            return True
        if self._fallback_may_run(ep):
            return False
        return (
            ep.next_step >= len(LADDER_OFFSETS_SECONDS)
            or st.searches >= MAX_SEARCHES_PER_SCOPE_PER_DAY
            or self._off_day == st.day
            or self._capped_day == budget_day()
        )

    # ── the search ───────────────────────────────────────────────────────

    async def refresh(
        self, candidates: Sequence[JumpCandidate], now: datetime,
    ) -> Dict[str, int]:
        """Run each candidate's due ladder step — fewest searches in the episode first, then the
        biggest move, two at a time — and start at most one fallback lookup. Returns status counts
        for the sweep log. Never raises (cancellation propagates)."""
        counts: Counter = Counter()
        if not candidates or not self.enabled():
            return dict(counts)
        now = _aware(now)
        ordered = self._order(candidates, now)
        sem = asyncio.Semaphore(_CONCURRENCY)
        slots = [MAX_SEARCHES_PER_REFRESH]
        fallback_slot = [False]

        async def _one(c: JumpCandidate) -> None:
            async with sem:
                started: Optional[str] = None
                try:
                    status, started = await self._search_one(c, now, slots, fallback_slot)
                except asyncio.CancelledError:
                    raise
                except Exception as e:  # noqa: BLE001 — one scope must not stop the sweep
                    logger.warning(
                        "UPDATES_JUMP_WEB_SEARCH scope=%s status=error unexpected=%s",
                        c.scope, type(e).__name__, exc_info=True,
                    )
                    status = "error"
                counts[status] += 1
                if started:
                    counts[started] += 1

        await asyncio.gather(*(_one(c) for c in ordered))
        return dict(counts)

    def _order(self, candidates: Sequence[JumpCandidate], now: datetime) -> List[JumpCandidate]:
        """Fewest searches in the candidate's episode first (every jumper gets its first search
        before any gets a third), then the biggest move; one entry per scope."""
        day = _et_day(now)

        def _key(c: JumpCandidate) -> Tuple[int, float]:
            st = self._days.get(c.scope)
            direction = insight_move.direction_of(c.change_percent)
            ep = st.episodes.get(direction) if (st is not None and st.day == day
                                                and direction is not None) else None
            score = c.score if (isinstance(c.score, (int, float)) and not isinstance(c.score, bool)
                                and math.isfinite(c.score)) else 0.0
            return (ep.searches if ep is not None else 0, -float(score))

        out: List[JumpCandidate] = []
        seen: Set[str] = set()
        for c in sorted(candidates, key=_key):
            if c.scope in seen:
                continue
            seen.add(c.scope)
            out.append(c)
        return out

    def _state(self, scope: str, now: datetime) -> _ScopeDay:
        day = _et_day(now)
        st = self._days.get(scope)
        if st is None or st.day != day:
            st = _ScopeDay(day=day)
            self._days[scope] = st
        return st

    @staticmethod
    def _held_company(c: JumpCandidate, st: _ScopeDay, direction: str) -> bool:
        """The scope holds a COMPANY cause for THIS move. A company card this process saw written
        for the other direction (a reversal since) does not count: the new move needs its cause."""
        if c.held_kind != insight_move.KIND_COMPANY:
            return False
        last = st.last_written
        return last is None or last.direction == direction

    async def _search_one(
        self, c: JumpCandidate, now: datetime, slots: List[int], fallback_slot: List[bool],
    ) -> Tuple[str, Optional[str]]:
        direction = insight_move.direction_of(c.change_percent)
        if direction is None:
            return "no_direction", None
        if is_crypto_scope(c.scope):
            # A coin's change is a rolling 24 h window and Privacy §3 says "a stock": never searched.
            return "not_equity", None
        query = build_query(c.name, direction)
        if query is None:
            # The scope, never the name: a name that cannot be searched should be visible.
            logger.info("UPDATES_JUMP_WEB_SEARCH scope=%s status=no_name", c.scope)
            return "no_name", None
        st = self._state(c.scope, now)
        ep = st.episodes.get(direction)
        if ep is None:
            # A first candidacy in this direction today — or a sign flip — starts an episode now.
            ep = _Episode(direction=direction, anchor=now)
            st.episodes[direction] = ep
        st.current_direction = direction
        ep.last_seen = now
        held_company = self._held_company(c, st, direction)
        step = _due_step(ep, now)
        status, signal = await self._primary_step(c, st, ep, now, step, query, held_company, slots)
        trigger = self._fallback_trigger(ep, now, step, signal, held_company)
        started = (self._start_fallback(c, st, ep, now, trigger, fallback_slot)
                   if trigger is not None else None)
        return status, started

    async def _primary_step(
        self, c: JumpCandidate, st: _ScopeDay, ep: _Episode, now: datetime,
        step: Optional[int], query: str, held_company: bool, slots: List[int],
    ) -> Tuple[str, Optional[str]]:
        """Run the episode's due step, if any. Returns ``(status, signal)``; the signal feeds the
        fallback triggers: "capped", "paced", "failed" (the search failed or could not run) or
        "empty" (nothing kept after the merge), None otherwise."""
        if ep.next_step >= len(LADDER_OFFSETS_SECONDS):
            return "ladder_done", None
        if step is None:
            return "not_due", None
        if ep.retry_at is not None and now < ep.retry_at:
            return "retry_wait", None
        if held_company:
            # A held company cause: the step is used with no spend.
            ep.next_step = step + 1
            ep.paced_since = None
            return "skipped_company", None
        if st.searches >= MAX_SEARCHES_PER_SCOPE_PER_DAY:
            return "day_limit", None
        if self._off_day == st.day:
            ep.next_step = step + 1
            ep.paced_since = None
            return "provider_off", "failed"
        if self._rate_limited_until is not None and now < self._rate_limited_until:
            return "rate_limited_wait", None
        latched = self._claim_latched(now)
        if latched == "capped":
            ep.next_step = step + 1
            ep.paced_since = None
            # A cap of 0 is the owner turning paid searching off, not a spent day: no fallback.
            return "capped", ("capped" if _daily_cap() > 0 else None)
        if latched == "paced":
            if ep.paced_since is None:
                ep.paced_since = now
            return "paced", "paced"
        # Checked and taken with no await between: the event loop cannot interleave them.
        if slots[0] <= 0:
            return "sweep_limit", None
        slots[0] -= 1

        claim = await self._claim(now)
        if claim == "capped":
            ep.next_step = step + 1
            ep.paced_since = None
            return "capped", "capped"
        if claim == "paced":
            if ep.paced_since is None:
                ep.paced_since = now
            return "paced", "paced"
        if claim != "ok":
            return claim, None          # budget_unavailable: the step stays due

        # The step is used and the search counted the moment the unit is claimed: a failing
        # upstream must not be retried every tick. Only a search that provably did not run gives
        # both back (`_primary_failure`).
        prev_step = ep.next_step
        ep.next_step = step + 1
        ep.paced_since = None
        ep.retry_at = None
        st.searches += 1
        ep.searches += 1
        started = time.monotonic()
        try:
            raw = await asyncio.wait_for(
                brave_search.web_search(query, count=_FETCH_COUNT, freshness="pd"),
                timeout=float(settings.BRAVE_SEARCH_TIMEOUT_SECONDS) + _HARD_BOUND_SLACK,
            )
        except asyncio.CancelledError:
            # Fail toward UNDER-spend: the unit stays claimed (the search may have been billed).
            raise
        except brave_search.BraveSearchException as e:
            status = await self._primary_failure(e, st, ep, prev_step, now)
            self._log(c.scope, status, step, st, ep, now, 0, Counter(), started)
            return status, "failed"
        except asyncio.TimeoutError:
            self._log(c.scope, "timeout", step, st, ep, now, 0, Counter(), started)
            return "timeout", "failed"

        pages, drops = _digest_all(raw, c.scope, c.name, now, direction=ep.direction,
                                   provider=PROVIDER_PRIMARY, parse_age=_primary_age)
        changed, refreshed = self._keep(
            ep, merge_pages(ep.pages, pages, now, judged=ep.rejected_urls), now)
        status = "ok" if pages else "no_results"
        self._log(c.scope, status, step, st, ep, now, len(pages), drops, started, changed=changed,
                  refreshed=refreshed)
        return status, (None if ep.pages else "empty")

    async def _primary_failure(
        self, e: brave_search.BraveSearchException, st: _ScopeDay, ep: _Episode,
        prev_step: int, now: datetime,
    ) -> str:
        """Book one failed primary search; returns its status."""
        if getattr(e, "not_run", False):
            # Provably never ran (never billed): the unit, the step and the count come back, and
            # the scope waits before it tries again.
            await self._refund()
            ep.next_step = prev_step
            st.searches = max(0, st.searches - 1)
            ep.searches = max(0, ep.searches - 1)
            ep.retry_at = now + timedelta(seconds=NOT_RUN_RETRY_SECONDS)
        if isinstance(e, (brave_search.BraveSearchAuthException,
                          brave_search.BraveSearchRequestException,
                          brave_search.BraveSearchNotConfiguredException)):
            # The key, the plan or the request shape was refused: it never heals by itself.
            if self._off_day != st.day:
                self._off_day = st.day
                logger.error(
                    "UPDATES_JUMP_WEB_SEARCH the search provider refused this service (%s, "
                    "status=%s) — jump search OFF for the rest of the ET day in this process",
                    type(e).__name__, getattr(e, "status", None),
                )
            return "refused"
        if isinstance(e, brave_search.BraveSearchRateLimitException):
            wait = getattr(e, "retry_after", None)
            if (not isinstance(wait, (int, float)) or isinstance(wait, bool)
                    or not math.isfinite(wait) or wait <= 0):
                wait = NOT_RUN_RETRY_SECONDS
            until = now + timedelta(seconds=min(float(wait), _MAX_RETRY_AFTER_SECONDS))
            if self._rate_limited_until is None or until > self._rate_limited_until:
                self._rate_limited_until = until
            return "rate_limited"
        return "error"

    @staticmethod
    def _keep(ep: _Episode, merged: Tuple[WebPage, ...], now: datetime) -> Tuple[bool, bool]:
        """Store the merged set; returns ``(set_changed, refreshed)`` for the log line.

        ``refreshed_at`` — the gate's one-shot "new pages" trigger, which also breaks a held move
        card's hold — moves only when the set GAINED a page that is neither kept already nor
        judged causeless this episode. A set that only shrank (a page aged past 24 h) or regrouped
        judged pages holds nothing a card has not read: it is stored, the stamp stays."""
        new_urls = frozenset(p.url for p in merged)
        old_urls = frozenset(p.url for p in ep.pages)
        ep.pages = merged
        if new_urls - old_urls - ep.rejected_urls:
            ep.refreshed_at = now
            return new_urls != old_urls, True
        return new_urls != old_urls, False

    def _claim_latched(self, now: datetime) -> Optional[str]:
        """"capped" / "paced" when this process already knows the store would refuse — no RPC and
        no sweep slot; None when a claim must be asked."""
        cap = _daily_cap()
        today = budget_day()
        if cap <= 0 or self._capped_day == today:
            return "capped"
        limit = paced_limit(cap, now)
        if limit < cap and self._paced_refused == (today, limit):
            return "paced"
        return None

    async def _claim(self, now: datetime) -> str:
        """"ok" | "paced" | "capped" | "budget_unavailable" — one unit of the day's bucket, under
        the PACED limit. Fails CLOSED. A refusal under a paced limit below the cap never latches
        the day (it waits for the limit to grow); a refusal at the full cap does."""
        cap = _daily_cap()
        today = budget_day()
        if cap <= 0 or self._capped_day == today:
            return "capped"
        limit = paced_limit(cap, now)
        try:
            count = await asyncio.to_thread(
                get_chat_budget_service().try_claim_turn, _BUDGET_BUCKET, limit,
            )
        except ChatBudgetUnavailable as e:
            logger.warning(
                "UPDATES_JUMP_WEB_SEARCH budget unavailable — failing CLOSED, no search: %s", e,
            )
            return "budget_unavailable"
        except Exception as e:  # noqa: BLE001 — a budget read never runs a search on a guess
            logger.warning(
                "UPDATES_JUMP_WEB_SEARCH budget raised (%s: %s) — failing CLOSED, no search",
                type(e).__name__, e,
            )
            return "budget_unavailable"
        if count == -1:
            if limit < cap:
                if self._paced_refused != (today, limit):
                    logger.info("UPDATES_JUMP_WEB_SEARCH paced (limit=%d of cap=%d)", limit, cap)
                self._paced_refused = (today, limit)
                return "paced"
            self._capped_day = today
            logger.info("UPDATES_JUMP_WEB_SEARCH daily cap reached (limit=%d)", cap)
            return "capped"
        return "ok"

    async def _refund(self) -> None:
        try:
            await asyncio.to_thread(get_chat_budget_service().refund_turn, _BUDGET_BUCKET)
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "UPDATES_JUMP_WEB_SEARCH unit refund failed (%s: %s)", type(e).__name__, e,
            )
        else:
            # A refunded unit reopens a bucket this process may have latched shut.
            self._capped_day = None
            self._paced_refused = None

    # ── the fallback provider ────────────────────────────────────────────

    @staticmethod
    def _fallback_trigger(
        ep: _Episode, now: datetime, step: Optional[int], signal: Optional[str],
        held_company: bool,
    ) -> Optional[str]:
        """Which trigger asks the fallback provider now, or None (the guards come after):
        T1 a later step (k ≥ 1) refused at the full cap, or paced for `FALLBACK_PACED_SECONDS`;
        T2 a later step's search failed or kept nothing; T3 the writer found no cause in (or would
        not accept a cause from) the CURRENT kept set (`_no_cause_now`),
        `FALLBACK_NO_CAUSE_AFTER_SECONDS` into the episode — a verdict of None (a model or judge
        outage) never triggers. T1 and T2 never fire on a set full of unjudged pages with a time
        (`_full_of_unjudged_precise`): the lookup's dated rows could displace none of them, so it
        would be paid for and thrown away — T3 asks once the writer finds no cause in them."""
        if held_company:
            return None
        later = step is not None and step >= 1 and not _full_of_unjudged_precise(ep)
        if later and signal == "capped":
            return "t1_capped"
        if (later and signal == "paced" and ep.paced_since is not None
                and (now - ep.paced_since).total_seconds() >= FALLBACK_PACED_SECONDS):
            return "t1_paced"
        if later and signal in ("failed", "empty"):
            return f"t2_{signal}"
        if (_no_cause_now(ep)
                and (now - ep.anchor).total_seconds() >= FALLBACK_NO_CAUSE_AFTER_SECONDS):
            return "t3_verdict"
        return None

    def _fallback_busy(self) -> bool:
        return sum(1 for t in self._fallback_tasks if not t.done()) >= MAX_FALLBACK_INFLIGHT

    def _fallback_may_run(self, ep: _Episode) -> bool:
        """A fallback lookup is still due for this episode (its verdict asks for one and a lookup
        is left) — so the search is not over yet."""
        if ep.fallback_lookups >= FALLBACK_LOOKUPS_PER_EPISODE:
            return False
        if not _no_cause_now(ep):
            return False
        module = _fallback_module()
        return module is not None and _fallback_enabled(module)

    def _start_fallback(
        self, c: JumpCandidate, st: _ScopeDay, ep: _Episode, now: datetime, trigger: str,
        fallback_slot: List[bool],
    ) -> Optional[str]:
        """Start one detached fallback lookup when every guard allows it; "fallback_started" or
        None. The sweep never waits for it."""
        if fallback_slot[0] or ep.fallback_inflight or self._fallback_busy():
            return None
        if ep.fallback_lookups >= FALLBACK_LOOKUPS_PER_EPISODE:
            return None
        if (now - ep.anchor).total_seconds() < FALLBACK_MIN_EPISODE_SECONDS:
            return None
        if ep.fallback_last is not None and (
                now - ep.fallback_last).total_seconds() < FALLBACK_SPACING_SECONDS:
            return None
        if _near_close(now):
            return None
        query_name = query_name_for(c.name)
        if query_name is None:
            return None
        module = _fallback_module()
        if module is None or not _fallback_enabled(module):
            return None
        fallback_slot[0] = True
        ep.fallback_lookups += 1
        ep.fallback_last = now
        ep.fallback_inflight = True
        try:
            task = asyncio.get_running_loop().create_task(
                self._fallback_run(module, c.scope, c.name, query_name, st, ep, now, trigger))
        except Exception as e:  # noqa: BLE001 — no loop / a closing loop: nothing started
            ep.fallback_inflight = False
            logger.warning("UPDATES_JUMP_WEB_SEARCH scope=%s provider=fallback status=not_started "
                           "error=%s", c.scope, type(e).__name__)
            return None
        self._fallback_tasks.add(task)
        task.add_done_callback(functools.partial(self._fallback_done, c.scope))
        logger.info(
            "UPDATES_JUMP_WEB_SEARCH scope=%s provider=fallback status=started trigger=%s "
            "lookups=%d episode_min=%d", c.scope, trigger, ep.fallback_lookups,
            int((now - ep.anchor).total_seconds() // 60),
        )
        return "fallback_started"

    async def _fallback_run(
        self, module: Any, scope: str, name: Optional[str], query_name: str, st: _ScopeDay,
        ep: _Episode, started_at: datetime, trigger: str,
    ) -> None:
        """The detached lookup: ask, then merge its rows through the SAME digest into the episode
        it started for (if that episode is still today's). Every failure is logged, counts only."""
        started = time.monotonic()
        try:
            outcome = await module.lookup(
                scope=scope, query_name=query_name, direction=ep.direction, now=started_at,
                trigger=trigger,
            )
            rows = getattr(outcome, "rows", None)
            status = _safe_code(getattr(outcome, "status", None))
            lookup_counts = _safe_counts(getattr(outcome, "counts", None))
            if not isinstance(rows, list):
                rows = []
            wall = _wall()
            if (self._days.get(scope) is not st or st.episodes.get(ep.direction) is not ep
                    or st.day != _et_day(wall)):
                logger.info("UPDATES_JUMP_WEB_SEARCH scope=%s provider=fallback status=stale "
                            "rows=%d", scope, len(rows))
                return
            pages, drops = _digest_all(
                {"results": rows}, scope, name, wall, direction=ep.direction,
                provider=PROVIDER_FALLBACK, parse_age=_fallback_age,
            )
            # The lookup was bought because the kept pages explain nothing (or the search could
            # not run): its pages outrank every page already judged causeless.
            changed, refreshed = self._keep(
                ep, merge_pages(ep.pages, pages, wall, judged=ep.rejected_urls), wall)
            self._log(scope, f"fallback_{status}", None, st, ep, wall, len(pages), drops, started,
                      provider=PROVIDER_FALLBACK, changed=changed, refreshed=refreshed,
                      extra=lookup_counts, trigger=trigger)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 — a lookup failure must never reach the sweep
            logger.warning(
                "UPDATES_JUMP_WEB_SEARCH scope=%s provider=fallback status=error unexpected=%s",
                scope, type(e).__name__, exc_info=True,
            )
        finally:
            ep.fallback_inflight = False

    def _fallback_done(self, scope: str, task: "asyncio.Task[None]") -> None:
        """Done-callback: forget the task and log how it ended — a failure is never left for the
        garbage collector's "exception was never retrieved"."""
        self._fallback_tasks.discard(task)
        if task.cancelled():
            logger.info("UPDATES_JUMP_WEB_SEARCH scope=%s provider=fallback status=cancelled",
                        scope)
            return
        exc = task.exception()
        if exc is not None:
            logger.error(
                "UPDATES_JUMP_WEB_SEARCH scope=%s provider=fallback status=task_failed error=%s",
                scope, type(exc).__name__, exc_info=(type(exc), exc, exc.__traceback__),
            )

    async def aclose(self) -> None:
        """Cancel every fallback lookup in flight and wait for them (the sweeper's shutdown).
        A cancelled lookup keeps whatever budget it claimed (fail toward under-spend)."""
        tasks = [t for t in self._fallback_tasks if not t.done()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._fallback_tasks.clear()

    # ── logging ──────────────────────────────────────────────────────────

    @staticmethod
    def _log(
        scope: str, status: str, step: Optional[int], st: _ScopeDay, ep: _Episode,
        now: datetime, new: int, drops: Counter, started: float, *,
        provider: str = PROVIDER_PRIMARY, changed: bool = False, refreshed: bool = False,
        extra: Optional[Dict[str, int]] = None, trigger: Optional[str] = None,
    ) -> None:
        """One counts-only line: never the query, a title, a snippet, a link or a host.
        ``changed`` = the kept set changed; ``refreshed`` = it gained an unread page
        (`refreshed_at` moved — the gate's new-pages trigger)."""
        newest = max((p.published_at for p in ep.pages), default=None)
        age_min = int((now - newest).total_seconds() // 60) if newest is not None else None
        kept_by = dict(Counter(p.provider for p in ep.pages))
        logger.info(
            "UPDATES_JUMP_WEB_SEARCH scope=%s status=%s kept=%d unknown_host=%d provider=%s "
            "step=%s episode_min=%d new=%d changed=%s refreshed=%s dropped=%s kept_by=%s ms=%d "
            "searches_today=%d episode_searches=%d newest_age_min=%s%s%s",
            scope, status, len(ep.pages), _unknown_hosts(ep.pages), provider,
            step if step is not None else "-", int((now - ep.anchor).total_seconds() // 60),
            new, changed, refreshed, dict(drops), kept_by,
            int((time.monotonic() - started) * 1000),
            st.searches, ep.searches, age_min,
            f" trigger={trigger}" if trigger else "",
            f" lookup={extra}" if extra else "",
        )


def _daily_cap() -> int:
    try:
        return int(getattr(settings, "UPDATES_JUMP_WEB_SEARCH_DAILY_CAP", 0) or 0)
    except (TypeError, ValueError):
        return 0


def _unknown_hosts(pages: Sequence[WebPage]) -> int:
    """How many kept pages come from a publisher we do not name — a count for the log (the
    card's Sources are shared with every watcher), never the hosts themselves."""
    unknown = 0
    for page in pages:
        safe = _safe_https_url(page.url)
        if safe is None or not _known_publisher(safe[1]):
            unknown += 1
    return unknown


_service: Optional[JumpWebSearchService] = None


def get_jump_web_search_service() -> JumpWebSearchService:
    global _service
    if _service is None:
        _service = JumpWebSearchService()
    return _service
