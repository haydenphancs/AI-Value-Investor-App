"""Every suggestion chip the app authors must be a question Cay AI will answer.

TestFlight 2026-09-16 (E3): the chips under an answer proposed "where can I buy DOGE?"
and "Who maintains DOGE?" — and the next turn declined both. The tester's rule is
absolute: *"it is a suggestion question, all suggestion question must have answer!"*
The chip generator (`ChatService.generate_followup_suggestions`) had no idea what the
chat could answer; this module is the deterministic half of the fix — the prompt now
describes the answerable scope, and this filter drops anything the chat would refuse
even if the model ignores that description. It runs on generation AND on replay of
stored chips, so rows written before the fix cannot re-offer a dead end.

What is refused, and therefore dropped:
  * personal advice / trade decisions — `chat_intent.is_trade_intent` (the same
    classifier that gates the disclaimer), so the two surfaces agree by construction;
  * price predictions and targets — "what will the price be", "will it go up", "how
    high could it go", "price target"; the app has no forecast and the outlook rule
    forbids inventing one;
  * analyst consensus / ratings / upgrades — the analyst package is unlicensed and the
    prompt tells the model to say so.

Web-search chips (`chat_intent.web_ask_kind`), in two cases:
  * where a web search IS open on an ask (``drop_web_search=True``: a REPORT chat, or any chat
    where every-chat search is open for this caller — `chat_web_search_service.web_chips_dropped`)
    every web-ask chip goes — "Any recent news on AVGO?", "Can you verify the margin?": a paid
    search is offered on the user's own ask, never one tap away on a chip the product wrote (owner
    decision, 2026-10-02);
  * everywhere else, a chip that asks to SEARCH THE WEB ("Search the web for the DOJ case",
    "Google it") goes too: no web search answers it there, so it is a dead end. A news or verify
    chip stays — Caydex's licensed headlines and data answer it.

What is deliberately KEPT, because the same brief made it answerable: outlook framed
as scenarios ("What's next for tech?", "what could drive it higher?"), venue questions
("where can I buy DOGE?", answered as availability), and background knowledge ("Who
maintains DOGE?", "how does it work?").

Pure functions, no I/O, never raise.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Iterable, List

from app.services.chat_intent import WEB_ASK_EXPLICIT, is_trade_intent, web_ask_kind

logger = logging.getLogger(__name__)

_UNANSWERABLE_CHIP_RE = re.compile(
    "|".join((
        # price predictions / forecasts / targets — a PRICE-shaped subject, or a price tail
        r"\bprice\s+(?:target|forecast|prediction|projection)s?\b",
        r"\btarget\s+price\b",
        r"\bwhat\s+will\s+(?:the\s+|its\s+|\w+'s\s+)?(?:price|value|stock|shares?|market\s+cap)\s+be\b",
        # a PRICE-shaped subject ("it" in a grounded chat is the asset) with any move verb;
        # double/triple only when nothing but a time/terminal follows — "Could it double
        # revenue?" is a fundamentals outlook, "Could it double by 2027?" a price call.
        r"\b(?:will|would|could|can|might)\s+"
        r"(?:it|the\s+(?:price|stock|shares?|coin|token|market\s+cap)|\w+'s\s+(?:price|stock|shares?))\s+"
        r"(?:go\s+(?:up|down|higher|lower)|hit|reach|drop|fall|rise|climb|crash|moon|rally|recover|rebound"
        r"|(?:double|triple)(?=\s*(?:\?|$|in\b|by\b|this\b|next\b|from\b|again\b|soon\b|over\b|within\b|before\b|after\b)))\b",
        # a bare subject with a price MOVE — "Could the stock double this year?", "Will it
        # go up?" — but not a fundamentals object: "Could it double revenue?" is outlook.
        r"\b(?:will|would|could|can|might)\s+(?:it|the\s+\w+|\w+)\s+(?:go\s+(?:up|down|higher|lower)|moon|crash)\b",
        r"\b(?:will|would|could|can|might)\s+(?:it|the\s+\w+|\w+)\s+(?:double|triple)"
        r"(?=\s*(?:\?|$|in\b|by\b|this\b|next\b|from\b|again\b|soon\b|over\b|within\b|before\b|after\b|its\s+price\b|the\s+price\b))",
        r"\b(?:will|would|could|can|might)\s+\w+\s+(?:hit|reach)\s+\$?\d",
        r"\bhow\s+(?:high|low|much\s+higher|much\s+lower|far)\s+(?:will|could|can|might|would)\b",
        r"\bprice\s+(?:in|by)\s+(?:20\d\d|\d+\s+(?:years?|months?|weeks?))\b",
        r"\b(?:predict|forecast)\s+(?:the\s+)?(?:price|stock|value|market)\b",
        r"\b(?:projected|predicted|expected|future)\s+price\b",
        r"\b\d+x\b",                                            # "a 10x from here?"
        r"\breach\s+\$\d",                                     # "Will DOGE reach $1?"
        # the model refuses these decision shapes even where the disclaimer classifier
        # sees no first-person frame
        r"\bis\s+\S+\s+(?:a\s+)?(?:good\s+|bad\s+|smart\s+|safe\s+)?(?:buy|sell|investment|bet|hold)\b",
        r"\b(?:better|best|worse|worst)\s+(?:buy|investment|pick|bet|hold)\b",
        r"\brecommend(?:ed|s|ation|ations)?\b",
        r"\b(?:add|put)\s+\S+\s+(?:to|in|into)\s+(?:my|the|a)\s+portfolio\b",
        r"\bgood\s+investment\b",
        # analyst package (unlicensed — the prompt says Caydex has no such data)
        r"\banalysts?'?s?\s+(?:rating|ratings|consensus|target|targets|opinion|opinions|recommendation|recommendations|upgrades?|downgrades?)\b",
        r"\b(?:consensus|wall\s+street(?:'s)?)\s+(?:rating|target|price\s+target|estimate|view|opinion)s?\b",
        r"\bconsensus\s+on\b",
        r"\brated\s+(?:a\s+)?(?:buy|sell|hold|strong\s+buy)\b",
        r"\brating\s+(?:up|down)grades?\b",
        r"\b(?:up|down)grades?\s+(?:to|from)\s+(?:buy|sell|hold|neutral|overweight|underweight|outperform)\b",
        r"\b(?:up|down)grades?\s+(?:or|and)\s+(?:up|down)grades?\b",
        r"\bwhat\s+do\s+(?:the\s+)?analysts\b",
    )),
    re.IGNORECASE,
)

# A web-ask chip that only a web search can answer: it names the search itself (search, google,
# browse, look it up, online, the internet / web, websites). Read only on a chip `web_ask_kind`
# already classified as an explicit ask, so "web" alone ("Amazon Web Services") never reaches it.
_SEARCH_THE_WEB_CHIP_RE = re.compile(
    r"\b(?:search\w*|google|brows(?:e|ing)|look\s+(?:(?:it|this|that)\s+)?up|online|internet|"
    r"web\s*sites?|the\s+web|web\s+search)\b",
    re.IGNORECASE,
)

# The model, stored rows and iOS smart quotes emit the typographic apostrophe; the
# patterns above are written with the ASCII one, so the text is folded before matching.
_APOSTROPHES = str.maketrans({"\u2019": "'", "\u2018": "'", "\u02bc": "'"})


# `is_trade_intent` is the DISCLAIMER gate — "someone might act on this" — which is wider
# than "the chat declines this". A checklist question ("What questions should I ask before
# buying anything?", a bundled evergreen starter) carries the `should I` frame and the
# `buying` verb, gets its disclaimer, and is answered in full. The frame below names the
# educational verbs that make a `should I` question a lesson rather than a decision; a
# chip matching it skips the trade-intent drop (the price/analyst drops still apply).
# Bounded to ONE clause (no crossing a sentence boundary), never `how much / how many`
# (a sizing question), and never when the educational verb leads straight into a trade
# continuation ("what should I consider buying?" is the decision, not the lesson).
_EDUCATIONAL_FRAME_RE = re.compile(
    r"\b(?:what|which|how)\b(?!\s+(?:much|many)\b)[^.?!\n]{0,40}?\bshould\s+(?:i|we|one|someone)\s+"
    r"(?:ask|consider|look\s+(?:for|at)|check|watch|know|understand|research|evaluate|"
    r"compare|read|study|learn|review|think\s+about)\b"
    r"(?!\s+(?:buying|selling|holding|shorting|investing|adding|owning|trimming|"
    r"(?:to\s+)?(?:buy|sell|hold|short|invest|add|own|trim))\b)",
    re.IGNORECASE,
)


def is_answerable_chip(text: Any, *, drop_web_search: bool = False) -> bool:
    """True when a chip is a question the chat will answer rather than decline.

    `drop_web_search` (a report chat, or a chat where every-chat search is open for the caller):
    a chip that would open the paid web search is refused. Without it, a chip that asks to search
    the web is refused too — nothing would answer it."""
    if not isinstance(text, str):
        return False
    t = text.strip().translate(_APOSTROPHES)
    if not t:
        return False
    if _UNANSWERABLE_CHIP_RE.search(t):
        return False
    kind = web_ask_kind(t)
    if kind is not None and drop_web_search:
        return False
    if kind == WEB_ASK_EXPLICIT and _SEARCH_THE_WEB_CHIP_RE.search(t):
        return False
    if _EDUCATIONAL_FRAME_RE.search(t):
        return True
    if is_trade_intent(t):
        return False
    return True


def filter_answerable_chips(raw: Any, limit: int = 2, *, drop_web_search: bool = False) -> List[str]:
    """Normalise a model's / a stored row's chip list to at most `limit` answerable chips.

    Order-preserving, case-insensitive dedup (a duplicate chip collides the iOS
    `ForEach(id: \\.self)`), refusals dropped BEFORE the cap so a dead chip never
    displaces a live one. Anything that is not a list of strings degrades to `[]`.
    """
    if not isinstance(raw, (list, tuple)):
        return []
    try:
        cap = max(0, int(limit))
    except (TypeError, ValueError):
        cap = 2
    if cap == 0:
        return []
    out: List[str] = []
    seen: set = set()
    dropped: List[str] = []
    for item in raw:
        if not isinstance(item, str):
            continue
        t = item.strip()
        if not t:
            continue
        key = t.lower()
        if key in seen:
            continue
        seen.add(key)
        if not is_answerable_chip(t, drop_web_search=drop_web_search):
            dropped.append(t)
            continue
        out.append(t)
        if len(out) >= cap:
            break
    if dropped:
        logger.info("Chat chips dropped as unanswerable: %s", dropped[:4])
    return out
