"""
ChatContextResolver — turns {context_type, reference_id} into a compact,
token-budgeted grounding block for the Cay AI chat prompt.

The iOS client sends only the screen type + a reference id (a ticker,
"TICKER|persona", an article slug, a book curriculum order, ...). This resolver
fetches the ALREADY-CACHED data for that screen from the existing service layer
and returns a text block that ``chat_service`` injects into the Gemini system
instruction, so iOS stops shipping big raw context strings.

Grounding strategy — "prune-then-dump" (one generic serializer, not per-field
curation): a short curated LEAD guarantees the highest-value facts survive the
cap, then ``_flatten_for_grounding`` dumps the WHOLE payload the resolver already
holds (a cached report dict / a Pydantic ``model_dump`` / a bundled article dict)
as compact ``key: value`` text — MINUS the heavy non-semantic keys a text model
can't use (price/chart float series, audio read-along timing arrays, gradients,
urls, embeddings). This grounds the chat on ~all of the screen's data, auto-picks
up new fields, and stays cheap because ``_DUMP_CAP`` bounds every block.

Contract:
  * Never recomputes — only reads existing caches / bundled content.
  * Never raises — any miss / failure degrades to the client-provided context
    (or ``None``) with a ``logger.warning``, so a chat can always proceed.

STOCK is intentionally a no-op here: ``chat_service`` already enriches stock
chats from ``stock_id`` (profit / snapshot / company-profile summaries) + the
iOS current-tab context, so the resolver defers to that path.
"""

import asyncio
import logging
import math
import re
from typing import Any, Dict, Iterator, List, Optional, Tuple

logger = logging.getLogger(__name__)


def _log_ref(value: Any, cap: int) -> str:
    """A client-chosen field as it may appear in a log line: bounded and rendered with
    `%r` by the caller, so a newline or a 1 MB string cannot forge a second record or
    flood the log (S03-7). The schema caps these at 64 / 256 chars too; this is the
    belt for any path that reaches the resolver without going through it."""
    text = "" if value is None else str(value)
    return text if len(text) <= cap else text[:cap] + "…"

# Hard bound on how long a single context resolve may take. Cache-only reads
# (report / money-moves) finish well under this; the ETF/CRYPTO/INDEX services
# fall through to a cold recompute — this ceiling stops that from stalling the
# FIRST streamed token; on timeout the chat proceeds ungrounded (never blocked).
_RESOLVE_TIMEOUT_SECONDS = 4.0

# ── Grounding caps ──────────────────────────────────────────────────
_MAX_REPORT_SUMMARY = 800   # executive-summary portion of the report lead
_MAX_REPORT_MODULE = 280    # the price-move narrative / commodity blurb in a lead
_MAX_REPORT_THESIS = 600    # each of the bull / bear case lines in the report lead
_DUMP_CAP = 2800            # the flattened-payload portion (per screen). Lead adds ~0.2–1.2k on top.
# The TICKER_REPORT dump, flattened in FAIR mode (every section gets a share). Ten
# sections at 2800 left each ~280 chars; 3600 gives each ~360 — most of a narrative line.
# The report lead (summary, thesis, competitor list) adds ~2k on top, ~5k at worst.
_REPORT_DUMP_CAP = 3600
_STR_CAP = 400              # any single string field is trimmed to this in the dump
# Fair mode never cuts a string to fewer than this many characters: a 12-char stub of a
# narrative is noise, so the line is dropped instead.
_MIN_CUT_CHARS = 40

# ── Competitor lead (TICKER_REPORT) ──
# Duplicated on purpose — importing the collector here would pull the whole report
# pipeline (and its FMP client) into every chat turn. Pinned equal to the collector's
# `_THREAT_HIGH_THRESHOLD` / `_THREAT_LOW_THRESHOLD` by tests/test_chat_context_resolver.py.
# The segment cap is the one the collector wrote "competes in" segments under (48) until
# the grounded research list was retired (2026-10-02); only stored reports carry one now.
_COMPETITOR_SEGMENT_CAP = 48
_COMPETITOR_NAME_CAP = 80
_COMPETITOR_LEAD_MAX_ROWS = 7
_THREAT_HIGH_AT = 7.0
_THREAT_LOW_AT = 3.0
_REPORT_DATE_CAP = 40

# Keys dropped ANYWHERE in the payload during the dump — heavy, non-semantic data
# a text model can't use (measured: 48–84% of a raw payload). Compared case-
# insensitively, so both snake_case (`chart_data`) and camelCase (`readAlong`,
# `heroGradientColors`, `audioUrl`) forms match.
_DROP_KEYS = frozenset({
    # numeric / chart / price series (raw coordinates)
    "chart_data", "prices", "recent_prices", "recent_price_dates", "timeline_prices",
    "hedge_fund_price_data", "hedge_fund_flow_data", "data_points", "history",
    "dividend_history", "dividends", "growth_chart", "profit_power", "earnings_track_record",
    "news_articles", "news",
    # report per-metric frozen history + forecast/insider series (chart data that would otherwise
    # eat the dump budget EARLY — fundamental_metrics sits before the narrative modules — and starve
    # the moat/revenue/Wall-Street/macro insights out of the block). The metric name+value survive.
    "annual_history", "quarterly_history", "sector_annual_history", "sector_quarterly_history",
    "annual_timeline", "projections", "insider_flow",
    # audio read-along timing arrays + UI cosmetics
    "readalong", "readalongwords", "itemsreadalong", "herogradientcolors",
    "audiourl", "imageurl", "videourl", "logo_url", "logourl", "icon", "heroimage",
    "website", "whitepaper", "url", "uri", "source_url", "sourceurl",
    # embeddings / bulky source lists
    "embedding", "query_embedding", "sources",
})

# Keys dropped ONLY as direct children of one top-level section — never globally, because the
# same flattener serves every screen and these names are generic (`rating`, `target_price` may be
# real, licensed data elsewhere). The report's "Wall Street Consensus" section became
# "Valuation & Institutions" (2026-09-26): its analyst half — rating, price targets, rating
# distribution, momentum — is unlicensed FMP data the user no longer sees, and
# `valuation_status` / `discount_percent` / `dcf_measured` are the verdict-shaped FMP-DCF
# leftovers. The dump is presented to the model as "data the user can see", so none of it may
# reach it. Direct children only: the published estimate below it carries `analyst_years` (an
# assumption, kept). Compared case-insensitively: exact names, then name prefixes.
_SECTION_DROP_KEYS: Dict[str, Tuple[frozenset, Tuple[str, ...]]] = {
    "wall_street_consensus": (
        frozenset({"rating", "target_price", "low_target", "high_target",
                   "valuation_status", "discount_percent", "dcf_measured",
                   # the vendor stamp (the model must not name a data vendor), and the
                   # analyst-card insight's pre-rename key
                   "dcf_source", "hedge_fund_note"}),
        ("analyst_", "momentum_"),
    ),
    # The competitor rows reach the model through the report lead (`_competitor_lead`),
    # which states what their ORDER means. Dumped again here they carried
    # `market_share_percent: 0` (a back-compat placeholder iOS does not render) and no
    # order semantics at all.
    "moat_competition": (frozenset({"competitors"}), ()),
}
_NO_SECTION_DROP: Tuple[frozenset, Tuple[str, ...]] = (frozenset(), ())

# Keys dropped at ANY depth inside one top-level section (the direct-children list above
# cannot reach them). The TAM card's source label / quote name a data vendor, and the
# model must not name one. A moat pillar's `drivers` (per-metric focal values, sector
# medians, sub-scores) and `confidence` are on no screen — iOS `MoatDimensionDTO` decodes
# name / score / peer_score / source only, and the PDF renders neither — yet `drivers` was
# most of the moat's lines (~6 per metric, ~3 metrics a pillar): with the pillars ahead of
# `competitive_insight`, it would wall off every later pillar, the insight and the market.
_SECTION_DEEP_DROP_KEYS: Dict[str, frozenset] = {
    "moat_competition": frozenset({"tam_source_label", "tam_source_quote",
                                   "drivers", "confidence"}),
}
_NO_DEEP_DROP: frozenset = frozenset()

# Line kinds the fair flattener budgets differently: a TEXT value may be shortened on a
# word boundary (`_cap`); a NUM value is never cut — a cut "180.25" reads "18" — so it is
# dropped when it does not fit; a LIST value is cut only between elements.
_LINE_TEXT, _LINE_NUM, _LINE_LIST = "text", "num", "list"

# A reference_id built from EITHER form ("AAPL|buffett" or "AAPL|warren_buffett") resolves to
# the same cache row through the SHARED `persona_config.AGENT_TAG_TO_KEY` (imported where it is
# used: `app.services.agents` imports the research agent). This module kept a private copy of
# that map until 2026-10-02. The shared map holds CURRENT tags only: a legacy `dalio` reference
# must miss the cache rather than ground an old chat on today's Activist report.
_DEFAULT_PERSONA = "warren_buffett"

# TICKER_REPORT dump order (fair mode — every present section gets a share). The moat sits
# early: it is what "who are the competitors / how wide is the moat" questions need.
_REPORT_PRIORITY: Tuple[str, ...] = (
    "overall_assessment", "moat_competition", "revenue_engine",
    "revenue_forecast", "wall_street_consensus", "critical_factors", "key_management",
    "insider_data", "hidden_market_signals", "macro_data",
)
# Inside a dict, these children lead (then scalars before containers): the narrative or the
# headline figure a question is usually about, before the bulky per-metric arrays. Keyed by
# DOTTED PATH at any depth, a list item written `[*]` ("a.b[*]" = every item of a.b). A report
# read from JSONB stores keys shortest-first, and pass 2 hands a section only 2-3 lines — so
# without an entry, `caydex_fair_value.beta` and `.as_of` led and the estimate itself
# (`fair_value`) never reached the model, and ten `market_dynamics` scalars kept every moat
# pillar score out. Unlisted children keep payload order after the listed ones.
# The moat's pillar scores lead its one-sentence `competitive_insight`: on a full report the
# section gets the durability note plus ~2 lines, the insight's subject (the rivals) is in
# the lead's competitor list, and the durability note already says which rival threatens most.
_REPORT_CHILD_PRIORITY: Dict[str, Tuple[str, ...]] = {
    "moat_competition": ("durability_note", "dimensions", "competitive_insight", "market_dynamics"),
    "moat_competition.dimensions[*]": ("name", "score", "peer_score", "source"),
    "wall_street_consensus": ("wall_street_insight", "caydex_fair_value", "current_price",
                              "hedge_fund_smart_money"),
    # A refusal has no fair_value / range (None is skipped), so its reason leads instead.
    "wall_street_consensus.caydex_fair_value": (
        "fair_value", "range_low", "range_high", "refusal_reason", "method", "as_of", "currency",
        "alternative_value", "discount_rate_pct", "terminal_growth_pct",
    ),
    "macro_data": ("headline", "overall_threat_level", "intelligence_brief", "risk_factors",
                   "last_updated"),
}
# `[0]`, `[12]` … in a walked path → `[*]`, the form `_REPORT_CHILD_PRIORITY` is keyed by.
_LIST_INDEX = re.compile(r"\[\d+\]")


def _path_key(path: str) -> str:
    """A walked key path in the form a child-priority map is keyed by: list indices → `[*]`,
    lower-cased (keys are matched case-insensitively, like every other key list here)."""
    return _LIST_INDEX.sub("[*]", path).lower()


# Treated as "no grounding needed" — fall through to any client context.
_NO_CONTEXT = {"", "NONE", "GENERAL", "NORMAL"}
#: Context types whose client string is a CONTROL token for the resolver (Updates sends
#: `window=90`, the chart's window), never text to ground on: when the resolver builds
#: nothing, times out or fails, the chat is ungrounded — the token is not passed through.
_CONTROL_CONTEXT = {"UPDATES_SCOPE"}
_UPDATES_WINDOW_RE = re.compile(r"window=(\d{1,3})")


def updates_trend_window(client_context: Optional[str]) -> int:
    """The chart window an Updates chat was opened from (`window=7|30|90`), else 30. Strict:
    anything else — absent, malformed, a window the chart does not offer — is 30."""
    from app.services.news_sentiment_trend_service import TREND_DAYS

    m = _UPDATES_WINDOW_RE.fullmatch((client_context or "").strip())
    days = int(m.group(1)) if m else 30
    return days if days in TREND_DAYS else 30


def _cap(text: str, limit: int) -> str:
    """Trim to `limit` chars on a word boundary, adding an ellipsis if cut."""
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0]
    return cut + "…"


def _num(v: Any) -> Optional[str]:
    """Format a number plainly (comma-grouped, no scientific notation, no trailing zeros).
    Returns None for a non-finite float (NaN / ±inf) so it never leaks a bogus token into the
    grounding. Sub-cent values (|v| < 1e-4, e.g. meme-coin prices) keep significant figures so they
    don't collapse to "0" under 4-decimal formatting."""
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, int):
        return f"{v:,}"
    if isinstance(v, float):
        if not math.isfinite(v):   # NaN or ±inf
            return None
        if v.is_integer():
            return f"{int(v):,}"
        if 0 < abs(v) < 1e-4:      # sub-cent — 4 decimals would round it to "0"
            return f"{v:.8f}".rstrip("0").rstrip(".")
        return f"{v:,.4f}".rstrip("0").rstrip(".")
    return None


def _price(v: Any) -> Optional[str]:
    """Format a USD price for a LEAD line: 2 decimals normally, but keep significant figures for a
    sub-cent-but-nonzero value (meme coins) so it never reads "$0.00"/"$0.0000". Returns None for a
    non-finite / non-numeric value so the caller can omit the price rather than assert a false one."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    if isinstance(v, float) and not math.isfinite(v):
        return None
    # A zero price is not a price. `0 < abs(v) < 0.01` is False for 0.0, so it fell
    # through and returned the TRUTHY string "0.00" — and the caller's
    # `if px and ...` then happily stated "Price $0.00" in the grounding lead, telling
    # Cay AI the asset is worthless. The degraded gates in etf/index/commodity all treat
    # `price <= 0` as absent; this now matches them.
    if v <= 0:
        return None
    if 0 < abs(v) < 0.01:
        return f"{v:.8f}".rstrip("0").rstrip(".")
    return f"{v:,.2f}"


def _as_of_et() -> str:
    """"as of 9:36 AM ET, Sep 14" — so a brief written from this block dates itself.

    `_DEEP_DIVE_STYLE` tells the model to give its as-of date; without a stamp in the data
    it had nothing to quote, and a cached brief replayed hours later read as current.
    """
    from datetime import datetime
    from app.utils.market_hours import ET
    now = datetime.now(ET)
    return f"as of {now.strftime('%-I:%M %p')} ET, {now.strftime('%b %-d')}"



# ── UPDATES_SCOPE helpers ───────────────────────────────────────────
# Same shape the Updates endpoints accept (`endpoints/updates._valid_scope`): the reserved
# market key, or an FMP-style symbol. Checked here too because the reference id is
# client-chosen and reaches a Supabase filter.
_UPDATES_SCOPE_MAX_LEN = 32
_UPDATES_MAX_HEADLINES = 8
_UPDATES_HEADLINE_CAP = 200
_UPDATES_BULLET_CAP = 240


def updates_scope_class_hint(reference_id: Optional[str]) -> Optional[str]:
    """The asset class an Updates reference declares after `|`, or None.

    `"SPY|ETF"`: the watchlist row says the scope is a fund, which no symbol-shape rule can
    tell (`detect_asset_class` has no ETF branch). Only ETF is honoured — every other class
    is recognisable from the symbol itself. Client-chosen, but it only picks the chat's
    voice and tools for the user's own conversation, the same trust the ETF screen's
    context type already has.
    """
    parts = (reference_id or "").split("|")
    if len(parts) < 2:
        return None
    hint = parts[1].strip().upper()
    return hint if hint == "ETF" else None


def _updates_scope(reference_id: Optional[str]) -> Optional[str]:
    from app.services.news_cache_service import MARKET_SCOPE

    raw = (reference_id or "").split("|")[0].strip()
    if raw == MARKET_SCOPE:
        return MARKET_SCOPE
    scope = raw.upper()
    if not scope or len(scope) > _UPDATES_SCOPE_MAX_LEN:
        return None
    if not all(c.isalnum() or c in ".-^=" for c in scope):
        return None
    return scope


def _et_stamp(value: Any, now: Any) -> Optional[str]:
    """"Thu Sep 24 17:02 ET, 2 days ago" for an ISO timestamp, or None."""
    from app.utils.market_hours import ET, to_utc_instant

    instant = to_utc_instant(value) if isinstance(value, str) else None
    if instant is None:
        return None
    local = instant.astimezone(ET)
    stamp = f"{local:%a %b} {local.day} {local:%H:%M} ET"
    minutes = int((now - instant).total_seconds() // 60)
    if minutes < 0:
        return stamp
    if minutes < 60:
        ago = f"{minutes} min ago"
    elif minutes < 48 * 60:
        ago = f"{minutes // 60} h ago"
    else:
        ago = f"{minutes // (24 * 60)} days ago"
    return f"{stamp}, {ago}"


_GUIDANCE_READS = ("raised", "maintained", "lowered")


def _without_unmeasured_guidance(report: Any) -> Any:
    """The report dump is labelled "data the user can see". `management_guidance`
    is "unknown" on every report while earnings-call transcripts are unlicensed,
    and the card HIDES the block on that value — so the four guidance keys must not
    reach Cay AI as visible data ("the report lists guidance as unknown"). Returns
    the report untouched when a stance was actually read."""
    if not isinstance(report, dict):
        return report
    rf = report.get("revenue_forecast")
    if not isinstance(rf, dict) or rf.get("management_guidance") in _GUIDANCE_READS:
        return report
    trimmed = dict(rf)
    for key in ("management_guidance", "guidance_quote", "guidance_speaker", "guidance_period"):
        trimmed.pop(key, None)
    out = dict(report)
    out["revenue_forecast"] = trimmed
    return out


# ── TICKER_REPORT lead helpers ──────────────────────────────────────
_WS_RUN = re.compile(r"\s+")
# C0/C1 controls plus the Unicode line/paragraph separators: a report field must not be able
# to start a new grounding line of its own.
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f-\x9f\u2028\u2029]")
_COMPETITOR_TICKER = re.compile(r"[A-Z0-9][A-Z0-9.\-^=]{0,14}")
_AS_OF_PREFIX = re.compile(r"^as of(\s+|$)", re.IGNORECASE)

# What the score means, per `score_basis` (the collector's two scoring paths). The relative
# path blends a DIRECTNESS rank, which is Cay's research order on the research list and the
# industry peer list's own order otherwise — the sentence says which, so it is true of both.
_SCORE_BASIS_TEXT = {
    ("relative", "research"): (
        "blends how directly the rival competes (Cay's research order) with its return on "
        "invested capital vs the company's, scaled by moat; 5 is a neutral midpoint"
    ),
    ("relative", None): (
        "blends the rival's place in the industry peer list with its return on invested "
        "capital vs the company's, scaled by moat; 5 is a neutral midpoint"
    ),
    ("absolute", None): (
        "operating margin, ROE and revenue growth vs the rival's own sector median (5 = median)"
    ),
}
_COMPETITOR_SOURCE_TEXT = {
    "research": "Cay's web research into filings and public coverage",
    "industry_peers": "same-industry peers",
}
# A report stored before the rows carried `score_basis` / the list carried
# `competitor_source` (everything before 2026-10-01, e.g. the AVGO report of TestFlight
# #57) still needs an answer to "how did we get these competitors?". These either/or
# sentences are true of BOTH scoring paths and BOTH sources, so they can be said without
# knowing which one built the row. No badge legend: the 7.0 / 3.0 thresholds date from
# 2026-05-28, and an older report may have used others.
# The score sentence never claims directness: on these reports the relative path's rank
# input is the row's place in its source list (an industry peer list, or a research list
# whose prompt never asked for an order), not a measured directness — and it says only
# what is true whatever the row order (the head line says whether the scores descend).
# Said only when a row carries a real 0-10 `competitive_score`: rows stored before
# 2026-05-27 carry `moat_score` instead, scored by neither path (`_competitor_lead`).
_SCORE_UNKNOWN_BASIS_TEXT = (
    "Threat score (0-10): a threat measure comparing each rival with the company — depending "
    "on the data available, either the rival's place in the list it came from plus its return "
    "on invested capital vs the company's, scaled by moat strength, or its operating margin, "
    "ROE and revenue growth vs its own sector median. A higher score means a bigger threat, "
    "not necessarily a closer rival."
)
_COMPETITOR_SOURCE_UNKNOWN_TEXT = (
    "How the list was built: listed companies with overlapping products and customers, from "
    "Cay's research into filings and public coverage, or same-industry peers when that "
    "research is unavailable."
)


def _clean_label(value: Any, cap: int) -> Optional[str]:
    """A report string as one bounded grounding token: str only, controls → spaces, whitespace
    collapsed, word-boundary capped. None when nothing is left."""
    if not isinstance(value, str):
        return None
    text = _WS_RUN.sub(" ", _CONTROL_CHARS.sub(" ", value)).strip()
    return _cap(text, cap) if text else None


def _threat_label(value: Any) -> str:
    """The badge iOS draws (`mapCompetitorThreat`): high / moderate, anything else Low."""
    s = value.lower() if isinstance(value, str) else ""
    return {"high": "High", "moderate": "Moderate"}.get(s, "Low")


def _finite_float(value: Any) -> Optional[float]:
    """A real number as a float, or None (bool, non-numeric, NaN, ±inf, an int too big for a float)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        f = float(value)
    except (OverflowError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _threat_score(value: Any) -> Optional[float]:
    """The 0-10 competitive score, or None when it is not one (bool, NaN, ±inf, out of range)."""
    f = _finite_float(value)
    return f if f is not None and 0.0 <= f <= 10.0 else None


def _report_date(report: Dict[str, Any]) -> Optional[str]:
    """The close the report's figures are as of: `price_close_date`, else `live_date`
    ("As of Sep 22, 2026 close" → "Sep 22, 2026 close")."""
    for key in ("price_close_date", "live_date"):
        text = _clean_label(report.get(key), _REPORT_DATE_CAP)
        if text:
            text = _AS_OF_PREFIX.sub("", text).strip()
            if text:
                return text
    return None


def _thesis_lead(report: Dict[str, Any]) -> List[str]:
    """The report's bull and bear case, each one bounded line ("; "-joined points)."""
    thesis = report.get("core_thesis")
    if not isinstance(thesis, dict):
        return []
    out: List[str] = []
    for key, label in (("bull_case", "Bull case"), ("bear_case", "Bear case")):
        items = thesis.get(key)
        if isinstance(items, str):
            items = [items]
        if not isinstance(items, list):
            continue
        points = [p for p in (_clean_label(x, _STR_CAP) for x in items[:8]) if p]
        if points:
            out.append(f"{label}: " + _cap("; ".join(points), _MAX_REPORT_THESIS))
    return out


def _competitor_lead(report: Dict[str, Any]) -> List[str]:
    """The report's competitor list as the user sees it, with what its ORDER means.

    The rows were reachable only through the dump, where JSONB key order and the budget kept
    them out of every real report — so "Is NVIDIA the main competitor?" was answered from
    memory, and the answer contradicted the screen. The opening depends on the order marker
    the collector stores: only `competitor_order == "direct"` (the research list, rank order)
    may be called "most direct first". Every report stored before the marker existed is
    score-ordered, and says so — "highest threat score first" only when the scores on the
    rows really descend. Model-authored text (segment labels) stays inside the caller's fence.
    """
    mc = report.get("moat_competition")
    if not isinstance(mc, dict):
        return []
    rows = mc.get("competitors")
    if not isinstance(rows, list):
        return []

    entries: List[Tuple[str, str, Optional[float], Optional[str]]] = []
    for row in rows[:_COMPETITOR_LEAD_MAX_ROWS * 3]:
        if len(entries) >= _COMPETITOR_LEAD_MAX_ROWS:
            break
        if not isinstance(row, dict):
            continue
        ticker = row.get("ticker")
        ticker = ticker.strip().upper() if isinstance(ticker, str) else ""
        if not _COMPETITOR_TICKER.fullmatch(ticker):
            continue   # iOS draws a ticker on every row; a row without one is not on screen
        name = _clean_label(row.get("name"), _COMPETITOR_NAME_CAP)
        segment = _clean_label(row.get("segment"), _COMPETITOR_SEGMENT_CAP)
        score = _threat_score(row.get("competitive_score"))
        basis = row.get("score_basis") if row.get("score_basis") in ("relative", "absolute") else None
        line = f"{name} ({ticker})" if name and name.upper() != ticker else ticker
        if segment:
            line += f" — competes in: {segment}"
        line += f" — threat {_threat_label(row.get('threat_level'))}"
        if score is not None:
            line += f", {score:.1f}"
        entries.append((ticker, line, score, basis))
    if not entries:
        return []

    if mc.get("competitor_order") == "direct":
        head = "Competitors on the report, most direct first:"
    else:
        scores = [e[2] for e in entries]
        descending = all(s is not None for s in scores) and all(
            a >= b for a, b in zip(scores, scores[1:])
        )
        head = ("Competitors on the report, in the order shown, highest threat score first:"
                if descending else "Competitors on the report, in the order shown:")
    out = [head] + [f"{i}. {e[1]}" for i, e in enumerate(entries, 1)]

    source = mc.get("competitor_source")
    source = source if isinstance(source, str) else None
    explained = False
    for basis in ("relative", "absolute"):
        tickers = [e[0] for e in entries if e[3] == basis]
        if not tickers:
            continue
        text = (_SCORE_BASIS_TEXT.get((basis, source))
                or _SCORE_BASIS_TEXT.get((basis, None)))
        scope = "" if len(tickers) == len(entries) else f" for {', '.join(tickers)}"
        out.append(f"Threat score{scope} (0-10): {text}.")
        explained = True
    if explained:
        out.append(
            f"Threat badge: High at {_THREAT_HIGH_AT:.1f} or above, Low at "
            f"{_THREAT_LOW_AT:.1f} or below, Moderate in between."
        )
    elif mc.get("competitor_order") != "direct" and any(e[2] is not None for e in entries):
        # A pre-marker report: no row says which path scored it. Rows with no real 0-10
        # score (pre-2026-05-27 `moat_score` rows, or garbage) show no score to explain.
        out.append(_SCORE_UNKNOWN_BASIS_TEXT)
    found = _COMPETITOR_SOURCE_TEXT.get(source or "")
    if found:
        out.append(f"How the list was built: {found}.")
    elif source is None:
        out.append(_COMPETITOR_SOURCE_UNKNOWN_TEXT)
    return out


def _rank_of(rank: Dict[str, int], k: Any) -> int:
    """Sort key for an ORDERED priority list: listed keys in list order, then every other
    string key (stable, payload order), then non-string keys (skipped by the walker)."""
    if not isinstance(k, str):
        return len(rank) + 1
    return rank.get(k.lower(), len(rank))


def _render_line(key: str, value: str) -> str:
    return f"{key}: {value}" if key else value


def _flatten_for_grounding(
    payload: Any, max_chars: int, str_cap: int = _STR_CAP, skip_top: Tuple[str, ...] = (),
    priority_top: Tuple[str, ...] = (), *, fair: bool = False,
    child_priority: Optional[Dict[str, Tuple[str, ...]]] = None,
) -> str:
    """Render a JSON-ish payload (a cached dict / a ``model_dump`` / an article dict) to compact
    ``key: value`` grounding lines. Drops ``_DROP_KEYS`` anywhere in the tree + ``skip_top`` at the top
    level + ``_SECTION_DROP_KEYS`` among one top-level section's direct children +
    ``_SECTION_DEEP_DROP_KEYS`` anywhere inside one section (all case-insensitive), truncates long
    strings to ``str_cap``, caps total output at ``max_chars``, and NEVER raises (a bad node is
    skipped). Lists are inlined (scalars) or walked per-item (dicts), both capped at 12 elements so one
    long array can't blow the budget.

    ``priority_top`` is an ORDERED list: its top-level keys are emitted first, in that order, then
    every other key in payload order. It used to be a yes/no split that kept the PAYLOAD's order, and
    a report read back from JSONB stores keys shortest-first — so `macro_data` led every report dump
    and spent the whole budget before the moat, revenue and Wall Street sections were reached.

    ``fair=True`` (opt-in; TICKER_REPORT) budgets per SECTION instead of first-come:
      * inside a section, children are ordered narrative-first, at every depth: the keys
        ``child_priority`` lists for that dict's DOTTED PATH (``"section"``, ``"section.child"``,
        ``"section.items[*]"`` — see ``_path_key``) in that order, then scalars before dicts/lists;
      * a first pass gives each present priority section up to ``max_chars // sections``; the
        text or list line the share runs out on is shortened into what is left of it, so every
        section's narrative shows (a number waits for the next pass);
      * the rest is handed out ONE line per section per round, in priority order, with every
        non-priority key last;
      * a numeric value is never cut (a cut "180.25" reads "18") — it is dropped instead; text is cut
        only on a word boundary (``_cap``), a list only between elements; output ≤ ``max_chars``.
    Off by default, so every other screen's output is unchanged."""
    if max_chars <= 0:
        return ""
    skip_top_l = {s.lower() for s in skip_top}
    rank = {s.lower(): i for i, s in enumerate(priority_top)}
    child_rank_by_path = {
        _path_key(str(path)): {c.lower(): i for i, c in enumerate(children)}
        for path, children in (child_priority or {}).items()
    }

    def _scalar(v: Any) -> Optional[Tuple[str, str]]:
        if isinstance(v, str):
            s = v.strip()
            if not s:
                return None
            if fair:
                return _cap(s, str_cap), _LINE_TEXT
            return ((s[:str_cap] + "…") if len(s) > str_cap else s), _LINE_TEXT
        n = _num(v)
        return (n, _LINE_NUM) if n is not None else None

    def _lines(o: Any, prefix: str, top: bool = False,
               section_drop: Tuple[frozenset, Tuple[str, ...]] = _NO_SECTION_DROP,
               deep_drop: frozenset = _NO_DEEP_DROP) -> Iterator[Tuple[str, str, str, Tuple[str, ...]]]:
        """Yield ``(key, value, kind, list_parts)`` per grounding line, depth-first."""
        if isinstance(o, dict):
            drop_names, drop_prefixes = section_drop
            items = list(o.items())
            if top:
                if rank:   # emit high-value narratives before a bulky early section
                    items.sort(key=lambda kv: _rank_of(rank, kv[0]))
            elif fair:     # narrative-first: listed children, then scalars before containers
                cr = child_rank_by_path.get(_path_key(prefix)) if child_rank_by_path else None
                items.sort(key=lambda kv: (
                    _rank_of(cr, kv[0]) if cr else 0,
                    1 if isinstance(kv[1], (dict, list)) else 0,
                ))
            for k, v in items:
                if not isinstance(k, str):
                    continue
                kl = k.lower()
                if kl in _DROP_KEYS or (top and kl in skip_top_l):
                    continue
                if kl in drop_names or (drop_prefixes and kl.startswith(drop_prefixes)):
                    continue
                if kl in deep_drop:
                    continue
                if v is None or v == "" or v == [] or v == {}:
                    continue
                key = f"{prefix}.{k}" if prefix else k
                if isinstance(v, (dict, list)):
                    # A section's drop list applies to ITS direct children only (never deeper);
                    # its deep-drop set, and nothing else, follows the walk all the way down.
                    if top:
                        child_drop = _SECTION_DROP_KEYS.get(kl, _NO_SECTION_DROP)
                        child_deep = _SECTION_DEEP_DROP_KEYS.get(kl, _NO_DEEP_DROP)
                        yield from _lines(v, key, section_drop=child_drop, deep_drop=child_deep)
                    else:
                        yield from _lines(v, key, deep_drop=deep_drop)
                else:
                    sv = _scalar(v)
                    if sv is not None:
                        yield key, sv[0], sv[1], ()
        elif isinstance(o, list):
            if all(not isinstance(x, (dict, list)) for x in o):   # pure-scalar list → inline
                vals = [s[0] for s in (_scalar(x) for x in o[:12]) if s is not None]
                if vals:
                    joined = ", ".join(vals)
                    if len(joined) > 600:   # one list line must not dominate / overshoot the cap
                        joined = joined[:600].rsplit(", ", 1)[0] + ", …"
                    yield prefix, joined, _LINE_LIST, tuple(vals)
            else:                                                 # mixed / dict list → per item
                for i, item in enumerate(o[:12]):
                    yield from _lines(item, f"{prefix}[{i}]", deep_drop=deep_drop)
        else:                                                     # a scalar reached via a list item
            sv = _scalar(o)
            if sv is not None:
                yield prefix, sv[0], sv[1], ()

    if not fair:
        lines: List[str] = []
        remaining = max_chars
        try:
            for key, value, _kind, _parts in _lines(payload, "", top=True):
                line = _render_line(key, value)
                lines.append(line)
                remaining -= len(line) + 1
                if remaining <= 0:
                    break
        except Exception as e:   # a malformed node must never drop the whole grounding block
            logger.debug("chat_context: flatten stopped early (%s: %s)", type(e).__name__, e)
        return "\n".join(lines)
    return _fair_flatten(payload, max_chars, rank, _lines)


def _shrink_line(key: str, value: str, kind: str, parts: Tuple[str, ...], room: int) -> Optional[str]:
    """The longest honest version of a line that costs ≤ ``room`` (its newline included), or None.
    A number is never shortened; text is cut on a word boundary; a list only between elements."""
    head = len(key) + 2 if key else 0
    avail = room - 1 - head                      # chars left for the value itself
    if kind == _LINE_NUM or avail < _MIN_CUT_CHARS:
        return None
    if kind == _LINE_LIST:
        kept: List[str] = []
        for part in parts:
            if len(", ".join(kept + [part])) + 3 > avail:   # + ", …"
                break
            kept.append(part)
        return _render_line(key, ", ".join(kept) + ", …") if kept else None
    cut = _cap(value, avail - 1)                 # `_cap` may append one "…"
    if not cut or cut == "…" or len(cut) > avail:
        return None
    return _render_line(key, cut)


def _fair_flatten(payload: Any, max_chars: int, rank: Dict[str, int], lines_of) -> str:
    """The ``fair=True`` budget of `_flatten_for_grounding` — see its docstring."""
    capacity = max_chars + 1                     # every line costs len + 1 (its newline)
    collect_limit = 2 * capacity                 # no section can use more than the whole budget

    def _collect(gen, label: str, limit: int) -> Tuple[List[Tuple[str, str, str, Tuple[str, ...]]], int]:
        out: List[Tuple[str, str, str, Tuple[str, ...]]] = []
        size = 0
        try:
            for line in gen:
                out.append(line)
                size += len(_render_line(line[0], line[1])) + 1
                if size > limit:
                    break
        except Exception as e:   # a malformed node costs its section's tail, never the block
            logger.warning(
                "chat_context: fair flatten stopped section %r early (%s: %s)",
                _log_ref(label, 64), type(e).__name__, e,
            )
        return out, size

    sections: List[List[Tuple[str, str, str, Tuple[str, ...]]]] = []
    rest_gens = []
    if isinstance(payload, dict):
        items = sorted(payload.items(), key=lambda kv: _rank_of(rank, kv[0]))
        for k, v in items:
            if not isinstance(k, str):
                continue
            # Each top-level key is walked as a one-key payload, so the walker applies its
            # top-level rules (skip_top, drop keys, the section drops) exactly as it does
            # for the whole dict.
            gen = lines_of({k: v}, "", top=True)
            if k.lower() in rank:
                section, _size = _collect(gen, k, collect_limit)
                if section:
                    sections.append(section)
            else:
                rest_gens.append((k, gen))
    else:
        rest_gens.append(("", lines_of(payload, "", top=True)))
    n_priority = len(sections)

    # Every non-priority key shares ONE trailing section, in payload order.
    rest: List[Tuple[str, str, str, Tuple[str, ...]]] = []
    rest_size = 0
    for label, gen in rest_gens:
        if rest_size > collect_limit:
            break
        chunk, size = _collect(gen, label, collect_limit - rest_size)
        rest.extend(chunk)
        rest_size += size
    if rest:
        sections.append(rest)

    taken: List[List[str]] = [[] for _ in sections]
    pos = [0] * len(sections)
    used = 0

    # Pass 1 — each priority section's fair share.
    if n_priority:
        share = capacity // n_priority
        for si in range(n_priority):
            s_used = 0
            lines = sections[si]
            while pos[si] < len(lines):
                key, value, kind, parts = lines[pos[si]]
                line = _render_line(key, value)
                cost = len(line) + 1
                if s_used + cost <= share and used + cost <= capacity:
                    taken[si].append(line)
                    pos[si] += 1
                    s_used += cost
                    used += cost
                    continue
                # The share runs out mid-line: a text / list line is shortened into what is
                # left of it (so every section's narrative shows, even if cut); a number is
                # never cut and waits for pass 2.
                short = _shrink_line(key, value, kind, parts, min(share - s_used, capacity - used))
                if short is not None:
                    taken[si].append(short)
                    used += len(short) + 1
                    pos[si] += 1
                break

    # Pass 2 — round-robin, one line per section per round, priority order, the rest last.
    progressed = True
    while progressed and used < capacity:
        progressed = False
        for si, lines in enumerate(sections):
            if pos[si] >= len(lines):
                continue
            progressed = True
            key, value, kind, parts = lines[pos[si]]
            pos[si] += 1
            line = _render_line(key, value)
            cost = len(line) + 1
            if used + cost <= capacity:
                taken[si].append(line)
                used += cost
                continue
            short = _shrink_line(key, value, kind, parts, capacity - used)
            if short is not None:
                taken[si].append(short)
                used += len(short) + 1
            # else: dropped — a number is never cut, and a stub is noise

    return "\n".join(line for section in taken for line in section)


class ChatContextResolver:
    """Dispatches {context_type, reference_id} → compact grounding text."""

    async def resolve(
        self,
        context_type: Optional[str],
        reference_id: Optional[str],
        client_context: Optional[str] = None,
        user_id: Optional[str] = None,
        meta: Optional[Dict[str, Any]] = None,
    ) -> Optional[str]:
        """`meta` (optional out-param): facts about WHAT was grounded, beside the block itself.
        Two keys, both TICKER_REPORT only: `report_persona_key` — the persona of the row the block
        was built from (its stored `agent` tag), which decides the report chat's mode voice — and
        `report_as_of` — the report's own as-of date (`_report_date`), which the web-results
        caveat names. Written
        only when the handler finished inside the ceiling: a timed-out or failed resolve leaves
        it untouched, so a late-finishing shielded task can never change a turn already built."""
        if not context_type:
            return client_context
        ctype = context_type.strip().upper()
        if ctype in _NO_CONTEXT:
            return client_context
        # BOOK has no backend text (book content is bundled in the iOS app), so
        # the client sends a context string (title/author + the passage) we pass through.
        if ctype == "BOOK":
            return client_context

        handler = self._dispatch().get(ctype)
        if handler is None:
            logger.warning(
                "chat_context: unknown context_type=%r (ref=%r) — using client context",
                _log_ref(context_type, 64), _log_ref(reference_id, 128),
            )
            return client_context

        # TICKER_REPORT is the only screen whose grounding is OWNER-SCOPED (it can read
        # the caller's own frozen report row), so it is the only handler that takes an
        # identity. Special-cased here rather than widening all eight signatures with a
        # parameter seven of them would ignore.
        handler_meta: Dict[str, Any] = {}
        if ctype == "TICKER_REPORT":
            coro = self._resolve_ticker_report(
                reference_id, client_context, user_id=user_id, meta=handler_meta,
            )
        else:
            coro = handler(self, reference_id, client_context)

        try:
            # SHIELDED, like `_run_tool_handler` and `_deterministic_widget`: the ceiling
            # abandons THIS caller's wait, it must not cancel the work. The handler is
            # usually the LEADER of a shared detail build (`get_index_detail` / `get_etf_detail`
            # / `get_crypto_detail` `_inflight`), and a cancelled leader handed every
            # joiner — the screen itself, the widget batch — "fetch was cancelled".
            block = await asyncio.wait_for(
                asyncio.shield(coro),
                timeout=_RESOLVE_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            logger.warning(
                "chat_context: resolve TIMED OUT (>%.1fs) for %r/%r — likely a cold "
                "detail-cache recompute; proceeding ungrounded",
                _RESOLVE_TIMEOUT_SECONDS, _log_ref(context_type, 64), _log_ref(reference_id, 128),
            )
            return None if ctype in _CONTROL_CONTEXT else client_context
        except Exception as e:
            logger.warning(
                "chat_context: resolve failed for %r/%r: %s: %s — degrading to client context",
                _log_ref(context_type, 64), _log_ref(reference_id, 128), type(e).__name__, e,
            )
            return None if ctype in _CONTROL_CONTEXT else client_context
        # Copied only HERE, after the handler returned inside the ceiling (see the docstring).
        if meta is not None and handler_meta:
            meta.update(handler_meta)
        if ctype in _CONTROL_CONTEXT:
            return block
        return block or client_context

    # ── Dispatch table ──────────────────────────────────────────────
    @classmethod
    def _dispatch(cls):
        return {
            "TICKER_REPORT": cls._resolve_ticker_report,
            "STOCK": cls._resolve_stock,
            "ETF": cls._resolve_etf,
            "CRYPTO": cls._resolve_crypto,
            "INDEX": cls._resolve_index,
            "COMMODITY": cls._resolve_commodity,
            "MONEY_MOVES_ARTICLE": cls._resolve_money_move,
            "JOURNEY_LESSON": cls._resolve_journey_lesson,
            "UPDATES_SCOPE": cls._resolve_updates_scope,
        }

    @staticmethod
    def _as_dict(obj: Any) -> Dict[str, Any]:
        """Best-effort dict view for the dump (a Pydantic model → model_dump; a dict → itself)."""
        if isinstance(obj, dict):
            return obj
        dump = getattr(obj, "model_dump", None)
        if callable(dump):
            try:
                return dump()
            except Exception:
                return {}
        return {}

    # ── TICKER_REPORT ────────────────────────────────────────────────
    async def _resolve_ticker_report(
        self,
        reference_id: Optional[str],
        client_context: Optional[str],
        user_id: Optional[str] = None,
        meta: Optional[Dict[str, Any]] = None,
    ) -> Optional[str]:
        """Ground the chat in the report the user is ACTUALLY LOOKING AT.

        `meta` (see `resolve`) receives `report_persona_key`: the persona of the row the block
        is built from, read from its stored `agent` tag — the report chat's mode voice follows
        the report actually grounded, not the reference segment (an installed build opening a
        report from a notification sends `warren_buffett` whatever the report is). Set only
        when a report resolved and its tag is a known persona (legacy `dalio` included).

        `reference_id` is `"TICKER|persona"` or `"TICKER|persona|<report_id>"`.

        The report id half matters because reports are FROZEN point-in-time
        snapshots while `ticker_report_cache` is CLOSE-ALIGNED: `get_cached_report`
        returns None for any row written before the most recent weekday 18:00 ET.
        Grounding on that cache alone therefore had two failure modes, both silent:

          * After the next close, a saved report from the Reports tab resolved to
            None, and `chat_service` fell through to LIVE stock enrichment — so
            "Chat with the report…" answered about today's quote while the user read
            a three-week-old analysis.
          * Before it, the cache could hold a NEWER regeneration of the same
            (ticker, persona), so the answer cited numbers that are not on screen.

        The user's own `research_reports` row is immutable history and is exactly
        what Path A rendered, so it is preferred. The lookup is filtered by
        `user_id` as well as `id`: a report id is a bare UUID with no other access
        control, and an unscoped read here would turn the chat into an oracle for
        anyone else's report.
        """
        if not reference_id:
            return None
        from app.services.agents.persona_config import AGENT_TAG_TO_KEY, persona_key_from_tag

        parts = [p.strip() for p in reference_id.split("|")]
        ticker = (parts[0] if parts else "").upper()
        segment = parts[1] if len(parts) > 1 else ""
        persona = segment.lower()
        # CURRENT tags only (no legacy `dalio`): this key looks up TODAY's shared cache row.
        # An unknown string passes through and simply misses, never grounding on another persona.
        persona = AGENT_TAG_TO_KEY.get(persona, persona) or _DEFAULT_PERSONA
        report_id = parts[2] if len(parts) > 2 and parts[2] else None
        if not ticker:
            return None

        from app.services import ticker_report_cache

        report = None
        if report_id and user_id:
            report = await self._stored_report_for_user(report_id, user_id)
        if not report:
            report = await ticker_report_cache.get_cached_report(ticker, persona)
        # Kill switch: never ground the chat on a published estimate the switch has withdrawn.
        from app.services.dcf_report_gate import (
            strip_caydex_if_disabled, wall_street_insight_is_for_this_card,
        )
        report = strip_caydex_if_disabled(report)
        # The section's insight reaches the model only when the user can see it: beside the
        # estimate it was written with (the rule the app card and the PDF use). Checked on the
        # RAW section, before the flattener drops the analyst fields the rule reads.
        ws = report.get("wall_street_consensus") if isinstance(report, dict) else None
        if isinstance(ws, dict) and ws.get("wall_street_insight") \
                and not wall_street_insight_is_for_this_card(ws):
            report = {**report, "wall_street_consensus": {**ws, "wall_street_insight": None}}
        if not report or not isinstance(report, dict):
            logger.info(
                "chat_context: no report for %s/%s (report_id=%s) — chat proceeds ungrounded",
                ticker, persona, report_id,
            )
            return None

        # The grounded report's OWN persona (its stored tag; legacy tags are a method, fine
        # for a voice). Logged when it disagrees with the reference — the visible trace of an
        # old build's notification route, where the voice now follows the report.
        stored_agent = report.get("agent")
        grounded_persona = persona_key_from_tag(stored_agent, include_legacy=True)
        ref_persona = persona_key_from_tag(segment, include_legacy=True)
        if grounded_persona is None:
            logger.warning(
                "chat_context: report for %r (report_id=%r) carries no known persona tag "
                "agent=%r — report chat voice falls back to the reference segment (ref=%r)",
                _log_ref(ticker, 32), _log_ref(report_id, 64), _log_ref(stored_agent, 32),
                _log_ref(reference_id, 128),
            )
        else:
            if meta is not None:
                meta["report_persona_key"] = grounded_persona
            # A reference that names no known persona (empty → the default lookup) claims
            # nothing, so it cannot disagree.
            if ref_persona is not None and ref_persona != grounded_persona:
                logger.warning(
                    "chat_context: report persona mismatch for %r (report_id=%r) — the "
                    "grounded report is %s, the reference says %s (ref=%r); the chat voice "
                    "follows the grounded report",
                    _log_ref(ticker, 32), _log_ref(report_id, 64), grounded_persona, ref_persona,
                    _log_ref(reference_id, 128),
                )

        # LEAD — guarantee the highest-value, most-asked facts survive the cap.
        lead: List[str] = [
            f"The user is viewing the in-depth Cay research report for "
            f"{_clean_label(report.get('company_name'), _COMPETITOR_NAME_CAP) or ticker} ({ticker})."
        ]
        # The report is a frozen snapshot: its figures are as of this close, not today.
        dated = _report_date(report)
        if dated:
            lead.append(f"Report dated {dated}.")
            # The same as-of date for the code-authored web-results caveat ("Your report reflects
            # data as of …", `chat_security.finalize_answer_notes`). Copied to the caller's meta
            # only when this handler finished inside the ceiling (see `resolve`).
            if meta is not None:
                meta["report_as_of"] = dated
        score = _finite_float(report.get("quality_score"))
        # 0-100 scale (iOS renders /100). A /10 label told Gemini "72/10", poisoning grounding.
        # A bool / NaN / ±inf is not a score ("nan/100" would reach the model).
        if score is not None:
            lead.append(f"Overall quality score: {score:.0f}/100.")
        pa = report.get("price_action")
        if isinstance(pa, dict):
            narrative = pa.get("narrative")
            narrative = narrative.strip() if isinstance(narrative, str) else ""
            if narrative:
                bits: List[str] = []
                change = _finite_float(pa.get("change_pct"))
                if change is not None:   # NaN / ±inf never become "+inf%"
                    window = _clean_label(pa.get("window_label"), 60)
                    bits.append(f"{change:+.1f}%" + (f" over {window}" if window else ""))
                tag = _clean_label(pa.get("tag"), 60)
                if tag:
                    bits.append(tag)
                head = f"Recent price movement ({'; '.join(bits)}): " if bits else "Recent price movement: "
                lead.append(head + _cap(narrative, _MAX_REPORT_MODULE))
        summary = report.get("executive_summary_text")
        summary = summary.strip() if isinstance(summary, str) else ""
        if summary:
            lead.append("Executive summary: " + _cap(summary, _MAX_REPORT_SUMMARY))
        # The thesis rides in the lead, not the fair dump: its two lists are ~750 chars on a
        # real report, and an equal section share (~330) kept only the bear case — "what is
        # the bull case?" then read as missing from the report.
        lead.extend(_thesis_lead(report))
        lead.extend(_competitor_lead(report))

        # DUMP — every other section the user can see (assessment, fundamentals, revenue, moat,
        # ownership, Wall Street, macro, critical factors) minus the lead keys + the heavy
        # chart/price arrays.
        dump = _flatten_for_grounding(
            _without_unmeasured_guidance(report), _REPORT_DUMP_CAP,
            skip_top=("symbol", "company_name", "exchange", "agent", "quality_score",
                      "live_date", "price_close_date", "price_action", "executive_summary_text",
                      "disclaimer_text", "core_thesis",   # core_thesis: in the lead
                      # Internal scoring inputs, never sent to iOS: they carry the analyst
                      # rating/target (or the estimate re-labelled "price_target") and a
                      # "deep_undervalued" valuation status.
                      "_scoring_inputs", "key_vitals"),
            # ORDERED, and budgeted FAIRLY: a report read back from JSONB stores its keys
            # shortest-first, so `macro_data` came first and spent the whole budget before
            # the moat, revenue and Wall Street sections were reached. Each section now gets
            # a share, narrative children first; macro is last because its brief is the
            # most generic. fundamental_metrics is not listed → it takes what is left.
            priority_top=_REPORT_PRIORITY,
            fair=True,
            child_priority=_REPORT_CHILD_PRIORITY,
        )
        parts = list(lead)
        if dump:
            parts.append("Report data the user can see (an excerpt; long sections are shortened):\n" + dump)
        parts.append(
            "Answer grounded in THIS report and never invent figures. If something is not in "
            "this excerpt, say it was not included here, never that the report lacks it."
        )
        return "\n".join(parts)

    @staticmethod
    async def _stored_report_for_user(
        report_id: str, user_id: str
    ) -> Optional[Dict[str, Any]]:
        """The caller's OWN frozen `ticker_report_data`, or None.

        Owner-scoped by `user_id` — see `_resolve_ticker_report`. Never raises: a
        Supabase blip must degrade to the shared cache, not break the chat turn.
        Runs the sync client in a thread so it cannot block the event loop.
        """
        def _query() -> Optional[Dict[str, Any]]:
            try:
                from app.database import get_supabase

                row = (
                    get_supabase()
                    .table("research_reports")
                    .select("ticker_report_data")
                    .eq("id", report_id)
                    .eq("user_id", user_id)
                    .eq("status", "completed")
                    .limit(1)
                    .execute()
                )
                data = (row.data or [{}])[0].get("ticker_report_data")
                return data if isinstance(data, dict) else None
            except Exception as e:
                logger.warning(
                    "chat_context: stored report lookup failed for %s: %s: %s",
                    report_id, type(e).__name__, e,
                )
                return None

        return await asyncio.to_thread(_query)

    # ── UPDATES_SCOPE (the Updates tab: Insights card, headlines, news-tone trend) ──
    async def _resolve_updates_scope(
        self, reference_id: Optional[str], client_context: Optional[str]
    ) -> Optional[str]:
        """Ground an "Ask Cay AI" opened from the Updates tab on what that tab shows.

        `reference_id` is the scope — a ticker, a coin pair or `__MARKET__` — optionally
        followed by `|ETF` (see `updates_scope_class_hint`). Three cache reads, run together
        and each allowed to fail on its own: the stored Insights card, the feed's in-window
        headlines (with Cay AI's label where one exists) and the news-tone trend for the
        window the chart showed (`window=7|30|90` in the client context; else 30).
        Nothing is generated: the card is only ever written by the sweeper, and the trend is
        a GROUP BY over stored labels.

        It mirrors `GET /updates/feed` so the model is told what the user actually sees: the
        headline window is the feed's UNFILTERED one, the stored card is "on screen" only
        when the feed has in-window news (the endpoint hides it otherwise), and a scope with
        no stored card is described as showing the plain "Latest headlines" list. Returns
        None when nothing at all was read, so the "Updates feed" source pill is not claimed
        for an answer grounded on nothing.
        """
        scope = _updates_scope(reference_id)
        if scope is None:
            logger.warning("chat_context: invalid UPDATES_SCOPE ref=%r", _log_ref(reference_id, 64))
            return None

        from datetime import date as _date, datetime, timezone

        from app.services.news_cache_service import MARKET_SCOPE, get_news_cache_service
        from app.services.news_insight_service import (
            get_news_insight_service,
            select_recent_corpus,
        )
        from app.services.news_sentiment_trend_service import (
            get_news_sentiment_trend_service,
            summarize_trend,
        )
        from app.utils.market_hours import ET

        is_market = scope == MARKET_SCOPE
        now = datetime.now(timezone.utc)

        async def _card():
            cards = await get_news_insight_service().get_cards([scope])
            return cards.get(scope)

        async def _feed_window():
            rows = (await asyncio.to_thread(
                get_news_cache_service().get_cached_bulk, [scope], 25,
            )).get(scope) or []
            # No subject filter: this is the window the feed endpoint shows and builds its
            # fallback card from. The subject-filtered corpus only feeds the AI card, and a
            # ticker whose coverage is all peer wraps would otherwise read as "no news" under
            # a timeline full of it.
            recent, _hours = select_recent_corpus(rows, now)
            return recent

        # The window the chart was SHOWING when "Ask about this" was tapped (iOS sends
        # `window=N`): the answer must quote the numbers on screen, not a fixed 30 days.
        window = updates_trend_window(client_context)

        async def _trend():
            data = await get_news_sentiment_trend_service().get_trend(scope, window, now=now)
            since = data.get("tracking_since")
            return summarize_trend(
                data.get("series") or [], days=window, today=now.astimezone(ET).date(),
                tracking_since=_date.fromisoformat(since) if since else None,
                history_status=data.get("history_status"),
            )

        card, feed_recent, trend_text = await asyncio.gather(
            _card(), _feed_window(), _trend(), return_exceptions=True,
        )
        for label, result in (("card", card), ("headlines", feed_recent), ("trend", trend_text)):
            if isinstance(result, Exception):
                logger.warning(
                    "chat_context: UPDATES_SCOPE %s read failed for %s: %s: %s",
                    label, scope, type(result).__name__, result,
                )
        card = card if isinstance(card, dict) else None
        feed_recent = feed_recent if isinstance(feed_recent, list) else []
        trend_text = trend_text if isinstance(trend_text, str) else None

        headline_lines: List[str] = []
        for row in feed_recent[:_UPDATES_MAX_HEADLINES]:
            title = _cap(str(row.get("headline") or ""), _UPDATES_HEADLINE_CAP)
            if not title:
                continue
            stamp = _et_stamp(row.get("published_at"), now)
            label = row.get("sentiment") if row.get("ai_processed") else None
            headline_lines.append(
                "- " + (f"[{stamp}] " if stamp else "") + title
                + (f" ({str(label).lower()})" if label else "")
            )

        if card is None and not headline_lines and not trend_text:
            logger.info("chat_context: UPDATES_SCOPE %s — nothing to ground on", scope)
            return None

        subject = "the overall market" if is_market else scope
        lines: List[str] = [
            f"The user is on the Updates tab, looking at the news feed for {subject}"
            + (" (general market news)." if is_market else ".")
        ]
        if card:
            written = _et_stamp(card.get("generated_at"), now)
            on_screen = bool(feed_recent)
            lines.append(
                ("Cay AI Insights card on screen" if on_screen
                 else "The most recent Cay AI Insights card (not on screen: the feed has no "
                      "news in its window right now)")
                + (f", written {written}" if written else "")
                + f" — sentiment {card.get('sentiment') or 'Neutral'}: "
                + _cap(str(card.get("headline") or ""), _UPDATES_HEADLINE_CAP)
            )
            for bullet in card.get("bullets") or []:
                lines.append("• " + _cap(str(bullet), _UPDATES_BULLET_CAP))
        elif headline_lines:
            lines.append(
                "There is no Cay AI summary for this feed yet: the card on screen is the plain "
                "\"Latest headlines\" list, i.e. the newest headlines below, verbatim."
            )

        if headline_lines:
            lines.append("Headlines in the feed (newest first; label = Cay AI's read, where scored):")
            lines.extend(headline_lines)

        if trend_text:
            lines.append(trend_text)

        lines.append(
            "Explain from these items and your tools. They are point-in-time: say how old the "
            "card or a headline is when it matters, and never invent figures, dates or events "
            "that are not here or in a tool result."
        )
        return "\n".join(lines)

    # ── STOCK (no-op — chat_service enriches via stock_id + iOS tab context) ──
    async def _resolve_stock(
        self, reference_id: Optional[str], client_context: Optional[str]
    ) -> Optional[str]:
        return None

    # ── ETF ──────────────────────────────────────────────────────────
    async def _resolve_etf(
        self, reference_id: Optional[str], client_context: Optional[str]
    ) -> Optional[str]:
        symbol = (reference_id or "").strip().upper()
        if not symbol:
            return None
        from app.services.etf_service import get_etf_service

        detail = await get_etf_service().get_etf_detail(symbol)
        if not detail:
            return None
        px = _price(detail.current_price)
        if px is None:
            # A DEGRADED build (the quote failed; the service refuses to cache it and
            # the screen shows $0.00). Grounding on it hands the model a zeroed payload
            # and — with the deep-dive cache keyed without the block — would pin that
            # brief for every user for 24 h. No grounding beats wrong grounding.
            logger.warning("chat grounding: ETF %r build has no usable price — not grounding", _log_ref(symbol, 64))
            return None
        chg = detail.price_change_percent
        price_str = (f" Price ${px} ({chg:+.2f}%) {_as_of_et()}."
                     if isinstance(chg, (int, float)) and math.isfinite(chg) else "")
        lead = f"The user is viewing the ETF detail screen for {detail.name} ({detail.symbol})." + price_str
        dump = _flatten_for_grounding(
            self._as_dict(detail), _DUMP_CAP,
            skip_top=("symbol", "name", "current_price", "price_change_percent"),
            # What the fund IS and its strategy hook, before the statistics / holdings
            # arrays that can fill the cap on their own.
            priority_top=("etf_profile", "strategy"),
        )
        return lead + ("\nScreen data the user can see:\n" + dump if dump else "")

    # ── CRYPTO ────────────────────────────────────────────────────────
    async def _resolve_crypto(
        self, reference_id: Optional[str], client_context: Optional[str]
    ) -> Optional[str]:
        symbol = (reference_id or "").strip().upper()
        if not symbol:
            return None
        from app.services.crypto_service import get_crypto_service

        detail = await get_crypto_service().get_crypto_detail(symbol)
        if not detail:
            return None
        px = _price(detail.current_price)
        if px is None:
            logger.warning("chat grounding: crypto %r build has no usable price — not grounding", _log_ref(symbol, 64))
            return None
        chg = detail.price_change_percent
        price_str = (f" Price ${px} ({chg:+.2f}%) {_as_of_et()}."
                     if isinstance(chg, (int, float)) and math.isfinite(chg) else "")
        lead = f"The user is viewing the crypto detail screen for {detail.name} ({detail.symbol})." + price_str
        # `crypto_profile` (the coin's description — origin, consensus, supply policy) sits
        # AFTER `key_statistics_groups` / `performance_periods` / `snapshots` in the schema
        # order, and those three can fill the 2800-char cap on their own, so the one block
        # that answers "who maintains / how does it work" was the one most often starved
        # (TestFlight 2026-09-16, E5). Emit it first; the numbers still follow.
        dump = _flatten_for_grounding(
            self._as_dict(detail), _DUMP_CAP,
            skip_top=("symbol", "name", "current_price", "price_change_percent"),
            priority_top=("crypto_profile",),
        )
        return lead + ("\nScreen data the user can see:\n" + dump if dump else "")

    # ── INDEX ────────────────────────────────────────────────────────
    async def _resolve_index(
        self, reference_id: Optional[str], client_context: Optional[str]
    ) -> Optional[str]:
        symbol = (reference_id or "").strip().upper()
        if not symbol:
            return None
        from app.services.index_service import get_index_service

        detail = await get_index_service().get_index_detail(symbol)
        if not detail:
            return None
        name = (getattr(detail, "index_name", "") or "").strip()
        lead = f"The user is viewing the market/index detail screen for {name or symbol}."
        px = _price(getattr(detail, "current_price", None))
        if px is None:
            logger.warning("chat grounding: index %r build has no usable level — not grounding", _log_ref(symbol, 64))
            return None
        chg = getattr(detail, "price_change_percent", None)
        if isinstance(chg, (int, float)) and math.isfinite(chg):
            lead += f" Level {px} ({chg:+.2f}%) {_as_of_et()}."
        dump = _flatten_for_grounding(
            self._as_dict(detail), _DUMP_CAP,
            skip_top=("symbol", "index_name", "current_price", "price_change_percent"),
            priority_top=("index_profile",),   # what the index is, before the snapshot arrays
        )
        return lead + ("\nScreen data the user can see:\n" + dump if dump else "")

    # ── COMMODITY ────────────────────────────────────────────────────
    async def _resolve_commodity(
        self, reference_id: Optional[str], client_context: Optional[str]
    ) -> Optional[str]:
        # iOS already passes a rich commodity context (price / stats / performance / news). Enrich it
        # with the curated commodity PROFILE (what it is + who produces / consumes it) that the client
        # context lacks — read from the BUNDLED static registry (`_get_meta`, no FMP fetch, honoring
        # the never-recompute contract). Never replaces the client context; degrades to it on any miss.
        symbol = (reference_id or "").strip().upper()
        if not symbol:
            return client_context
        try:
            from app.services.commodity_service import _get_meta
            meta = _get_meta(symbol)
        except Exception as e:
            logger.warning("chat_context: commodity profile lookup failed for %r: %s", _log_ref(symbol, 64), e)
            return client_context
        if not isinstance(meta, dict) or not meta:
            return client_context
        dump = _flatten_for_grounding(meta, _DUMP_CAP, skip_top=("fmp_symbol", "related", "tick_size", "unit"))
        if not dump:
            return client_context
        profile_block = "Commodity profile (what the user is viewing):\n" + dump
        return f"{client_context}\n\n{profile_block}" if client_context else profile_block

    # ── MONEY_MOVES_ARTICLE ──────────────────────────────────────────
    async def _resolve_money_move(
        self, reference_id: Optional[str], client_context: Optional[str]
    ) -> Optional[str]:
        slug = (reference_id or "").strip()
        if not slug:
            return None
        from app.services.money_moves_content_service import get_money_moves_content_service

        resp = await get_money_moves_content_service().get_money_moves()
        article = next(
            (a for a in (resp.articles or []) if isinstance(a, dict) and a.get("slug") == slug),
            None,
        )
        if not article:
            logger.info("chat_context: money move slug=%r not found", _log_ref(slug, 64))
            return None
        author = article.get("author") or {}
        author_name = author.get("name") if isinstance(author, dict) else str(author or "")
        lead = [
            f'The user is reading the Money Moves article "{article.get("title", "")}"'
            + (f" by {author_name}" if author_name else "") + "."
        ]
        subtitle = (article.get("subtitle") or "").strip()
        if subtitle:
            lead.append(subtitle)
        # Dump the article MINUS the engagement/cosmetic metadata (so the budget goes to the body +
        # highlights + statistics). The drop set strips the read-along timing arrays + gradients.
        dump = _flatten_for_grounding(
            article, _DUMP_CAP,
            skip_top=("slug", "title", "subtitle", "author", "cardsubtitle", "category",
                      "readtimeminutes", "viewcount", "learnercount", "sortorder", "commentcount",
                      "publisheddaysago", "taglabel", "isfeatured", "hasaudioversion",
                      "audiodurationseconds"),
            # The article's key highlights and statistics are short and are what the card
            # shows first; in payload order they came AFTER the body, which fills the cap on
            # its own — so every article's highlights and statistics were cut.
            priority_top=("keyHighlights", "statistics", "sections"),
        )
        parts = list(lead)
        if dump:
            parts.append("Article content the user can see:\n" + dump)
        parts.append("Answer in the context of this article's ideas.")
        return "\n".join(parts)

    # ── JOURNEY_LESSON ───────────────────────────────────────────────
    async def _resolve_journey_lesson(
        self, reference_id: Optional[str], client_context: Optional[str]
    ) -> Optional[str]:
        ref = (reference_id or "").strip()
        if not ref:
            return None
        from app.services.journey_content_service import get_journey_content_service

        resp = await get_journey_content_service().get_journey()
        lessons = getattr(resp, "lessons", None) or []

        def _match(lesson: Any) -> bool:
            get = lesson.get if isinstance(lesson, dict) else lambda k, d=None: getattr(lesson, k, d)
            return str(get("id", "")) == ref or str(get("title", "")) == ref

        lesson = next((l for l in lessons if _match(l)), None)
        if not lesson:
            return None
        get = lesson.get if isinstance(lesson, dict) else lambda k, d=None: getattr(lesson, k, d)
        title = get("title", "") or ""
        lead = [f'The user is on the Investor Journey lesson "{title}".']
        desc = (get("description", "") or "").strip()
        if desc:
            lead.append(desc)
        # Dump the lesson body (story_content.cards[].text) + metadata; the drop set strips the
        # per-word read-along timing arrays.
        dump = _flatten_for_grounding(
            self._as_dict(lesson), _DUMP_CAP,
            skip_top=("id", "title", "description", "sort_order", "level", "category", "duration_minutes"),
        )
        parts = list(lead)
        if dump:
            parts.append("Lesson content the user can see:\n" + dump)
        parts.append("Answer in the context of this lesson.")
        return "\n".join(parts)


# ── Module-level singleton (matches every other service) ────────────
_resolver: Optional[ChatContextResolver] = None


def get_chat_context_resolver() -> ChatContextResolver:
    global _resolver
    if _resolver is None:
        _resolver = ChatContextResolver()
    return _resolver
