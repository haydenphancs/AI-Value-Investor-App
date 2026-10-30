"""
Why did this stock move TODAY — answered from structured data, not from a model.

PURE MODULE — no network, no Supabase, no Gemini, no wall-clock reads except through
injected values. Same posture as `updates_materiality` and `price_volatility`, and for
the same reason: every branch here is a deterministic function of its arguments, so the
whole answer set is exhaustively testable without a fixture or a network.

WHY THIS EXISTS
---------------
The widget asks one question — "which stock moved most today, and why" — and the
existing machinery answered a different one. `price_catalyst_service` (retired
2026-10-02 with Google Search grounding) was a Gemini web-search over an arbitrary
*window label*, and in practice the report pipeline's multi-day windows dominated it. Measured 2026-08-14, the only cached ACHR row read:

    tag "Boeing Acquisition & Partnership" · window "Last 15 Days" · +42.7%
    "Archer Aviation's stock surged following the announcement…"

Printing that under a red −5.02% is not a wrong-signed answer. It is a *correct answer
to a different question*, and no threshold, sign guard or prompt tweak turns a 15-day
rally narrative into a daily explanation. Window is the filter that matters, not sign.

THE INSIGHT
-----------
"Why did it move today" has a small, enumerable answer set, and almost none of it needs
a language model:

    earnings today · an analyst acted today · classifiable company news ·
    its industry moved · the market moved · it gapped at the open · nothing

Each is a fact with a date attached. Detection is arithmetic and string matching over
data the app already pays for, so the output cannot hallucinate and is daily by
construction. An LLM, if ever added, would only *phrase* what is established here.

ONE MODEL-WRITTEN INPUT — THE NEWS LEAN (owner, 2026-10-14)
----------------------------------------------------------
The session's Updates AI Insights card, when its NET sentiment points the way the stock
moved, is the first answer `attribute` tries (owner, 2026-10-30 — see PRECEDENCE):
`detect_news_lean`. It is never generated here, only reprinted behind a gate —
Bearish/Bullish (never Neutral) agreeing with the move, an Unusual move (z ≥ 2, else ≥ 5%
without σ), not an after-close card on a report day, and a headline that names its subject
and carries no price, window, schedule or relative-day talk (`usable_lean_headline`).
Tagged "Bearish News" / "Bullish News" — what the news was, never a catalyst label. When
the card agrees but is withheld, the NONE sentence says "Today's news leaned bearish."
instead of "No clear catalyst", which would contradict the card the app shows beside it.
CRWV −15.46% on 2026-10-14 (z 2.57, a Bearish 13:09 ET card) is the case it exists for;
ACHR's all-bullish card under −5.02% is the case it must keep refusing
(`test_daily_move_attribution.py`).

PRECEDENCE
----------
The news lean first, whenever its gate passes: the card is the session's own account of the
news, written after whatever was reported, so it says how the tape took an event and not
just that the event happened. INTC −30.6% on 2026-10-30 is why: the earnings detector won
with "INTC beat EPS estimates by 27.8%" while the Bearish card beside it read "Intel's weak
PC outlook and guidance overshadows strong data center results" (owner: "i need the widget
to show it"). Then the dated detectors, most-specific first, because a stock that reported
earnings AND drifted with its sector moved on the earnings. `attribute()` returns the
single best cause plus the always-true context (σ multiple, industry delta, gap split), so
a caller can render a headline and a supporting line without re-deriving anything.

WHAT THIS DELIBERATELY WILL NOT DO
----------------------------------
Manufacture a cause. On most days for most tickers the honest answer is
`CauseKind.NONE` — measured against the live corpus, ACHR fell 5% with seven articles in
48h, all bullish, the most recent published hours before the drop. The insight prompt
was *told* about the drop and correctly declined to invent a reason. Returning "no
identifiable cause, and here is how unusual the move was" is the useful answer there,
not a failure to find one.
"""

from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import Any, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)


# ── Tuning ────────────────────────────────────────────────────────────

# A move counts as "in line with its industry" when it lands within this multiple of the
# industry's own move. Wide on purpose: the claim is "this went with its group", not a
# regression. 0.6-1.7x of a −2% industry day is −1.2% to −3.4%, which is the band a
# reader would call "moved with the sector" without quibbling.
_SECTOR_INLINE_LOW = 0.6
_SECTOR_INLINE_HIGH = 1.7

# Below this the group's own move is noise and cannot explain anything, however well the
# ratio happens to line up. A stock down 0.3% "in line with" an industry down 0.2% is not
# an explanation, it is a coincidence of small numbers.
_MIN_GROUP_MOVE_PCT = 0.75

# A gap is only worth calling out when it carries most of the day's move. Below this the
# stock opened roughly flat and did its work during the session.
_GAP_DOMINANT_SHARE = 0.6
# …and the gap itself has to be big enough to notice.
_MIN_GAP_PCT = 1.0

# Earnings land the evening before or the morning of. Anything older is not today's news —
# where "the evening before" means the previous TRADING day's close, so a Friday
# after-close print still explains Monday (see `detect_earnings`).
_EARNINGS_MAX_AGE_DAYS = 1
# An analyst action is "today's" only on the session date itself. A three-month-old
# rating is not why the stock moved this morning — ACHR's most recent action was
# 2026-05-12 while it fell 5% on 08-14, and treating that as causal would be a lie.
_GRADE_MAX_AGE_DAYS = 1

# Below this a move DISPLAYS as 0.00%, the client's `isFlat` (it rounds to the two decimals
# it prints). A sentence must not call such a stock "went the other way" beside a 0.00% badge.
_FLAT_PCT = 0.005

# ── The news lean (owner, 2026-10-14) ─────────────────────────────────
#
# The badge for an Updates Insights card whose NET sentiment points the way the stock moved.
# Worded as what the news WAS ("Bearish News"), never as a catalyst ("Analyst Downgrade"):
# a sentiment agreeing with a move is evidence, not proof, of what moved it. Mirrors the
# card's own pill on the Updates screen. Chat pins these strings (`test_chat_market_tools`).
NEWS_LEAN_TAGS: Dict[str, str] = {"bearish": "Bearish News", "bullish": "Bullish News"}

# "Unusual" in the shared z→tier vocabulary (`price_volatility._tier_for_z`). Owner: "Unusual,
# ≥2σ". Below it an agreeing card is reported as a lean ("Today's news leaned bearish.") and
# its headline is withheld — a generic PR card agrees with a routine day's sign by coin flip,
# which is how ACHR's headline read as a cause in the first build. CRWV 2026-10-14 was 2.57σ.
_LEAN_MIN_Z = 2.0
# The σ-less fallback (new listings): the fixed "extreme" band of `updates_materiality`.
_LEAN_MIN_PCT_NO_SIGMA = 5.0
# The card prompt asks for "under 90 characters"; past 100 the medium tile cuts it off.
_LEAN_MIN_CHARS = 12
_LEAN_MAX_CHARS = 100
# A card written this long after the bell, on a day the company reported, is about the print.
_POST_CLOSE_SLACK_MINUTES = 15

# ⚠️ These tables are a DENYLIST over model text, and the 2026-10-14 review found natural
# phrasings past every first draft of them ("Falls for Third Straight Session", "Gives Back
# Two Weeks of Gains", "Ahead of Q3 Report", "After the Bell"). A refusal costs only the
# weaker "Today's news leaned …" sentence, so a doubtful row errs towards refusing. Every row
# has a must-refuse sample that it ALONE refuses AND an honest twin, in
# `test_daily_move_attribution.py` (`_LEAN_ROWS`, `_SUBJECT_VERBS`); add both with any new row.
# A sample another layer also refuses pins nothing: on 2026-10-16 each review-round row could
# be deleted with the whole suite still green. Every row is linear-time (bounded repetitions,
# no nested quantifier) over a headline already capped at `_LEAN_MAX_CHARS`.
# The other way round costs as much: refusing a COMMON honest shape empties the feature. The
# 2026-10-16 round-2 review found the first multi-day rows refusing fundamentals ("Intel Warns
# of Continued Decline in PC Demand", "Posts Back-to-Back Losses", "Six-Month Lockup Expiry",
# "FDA Premarket Approval", "Fails to Report Annual Results on Time"), so every exemption and
# word-list entry has its own twin or sample too (`_EXEMPT_TWINS`, `_CONTINUE_WORDS`, …).
#
# A headline is reused VERBATIM as `detail_aged`, so it may name no relative day.
_LEAN_RELATIVE_DAY_RE = re.compile(
    r"\b(?:today|tonight|tomorrow|yesterday|overnight|weekend"
    r"|this\s+(?:morning|afternoon|evening|week|month|quarter)"
    r"|(?:earlier|later)\s+this\s+(?:week|month|quarter|year)"
    r"|right\s+now|next\s+(?:week|month)|last\s+(?:night|week|month))\b",
    re.IGNORECASE,
)
_NUMBER_WORD = (
    r"(?:two|three|four|five|six|seven|eight|nine|ten|eleven|twelve"
    r"|first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|\d+(?:st|nd|rd|th))"
)
# The tape nouns a HYPHENATED window must lead into ("Three-Day Slide", "Two-Week Decline"):
# alone, "Six-Month Lockup Expiry", "Four-Day Work Week" and "First-Day Contract Win" are news
# (2026-10-16, round 2) — and so is a price level ("Stock at Six-Month High"). Rally, streak,
# rout and sell-off are absent on purpose: their own rows refuse them anywhere, so here they
# could never be pinned. Never before a hyphen (the row ends `(?!-)`): "Six-Month High-Yield
# Notes" and "Two-Week Pop-Up Store" are news; "Run-Up" is the one hyphenated tape noun.
_LEAN_TAPE_NOUN = (
    r"(?:slides?|declines?|drops?|falls?|gains?|loss(?:es)?|losing|winning|run-ups?|runs?"
    r"|slumps?|surges?|climbs?|advances?|retreats?|skids?|rebounds?|highs?|lows?"
    r"|plunges?|tumbles?|pullbacks?|jumps?|dives?|dips?|slips?|spikes?|pops?|swoons?"
    r"|downturns?|corrections?|upswings?)"
)
# A multi-day window is the bug that started the rebuild (module header). Years and quarters
# are deliberately absent: "5-Year Deal" and "Third-Quarter Revenue" are news.
_LEAN_WINDOW_RE = re.compile(
    r"\b\d+[\s-]+(?:day|week|month|session)s?\b"
    # "Third Straight Session", "for a Third Day"
    rf"|\b{_NUMBER_WORD}\s+(?:(?:straight|consecutive|successive|losing|winning)\s+)?"
    r"(?:trading\s+)?(?:day|week|month|session)s?\b"
    # hyphenated only before a tape noun: "Three-Day Slide", "Two-Week Decline"
    rf"|\b{_NUMBER_WORD}-(?:day|week|month|session)s?\s+{_LEAN_TAPE_NOUN}\b(?!-)"
    r"|\b(?:straight|consecutive|successive)\s+(?:day|week|month|session|loss|losses|decline"
    r"|declines|drop|drops|gain|gains)\b"
    r"|\b(?:days?|weeks?|months?)\s+running\b"
    r"|\b(?:week|month|year)-?long\b|\bmulti-?(?:day|week|month|session)\b"
    r"|\byear-to-date\b|\bytd\b"
    r"|\b(?:past|last|previous)\s+(?:\w+\s+)?(?:days?|weeks?|months?|sessions?)\b"
    r"|\bover\s+the\s+(?:last|past)\b"
    r"|\bsince\b"
    r"|\bsurged\b"
    r"|\brall(?:y|ies|ied|ying)\b"
    # Multi-day tape (2026-10-16): the move told as the continuation of earlier sessions'.
    # Only tape nouns: "Back-to-Back Quarterly Losses", "Back-to-Back Losses Despite Delivery
    # Gains" (a net loss, round 2), "Q3 Net Loss Widens" and "Adds to Net Losses" are news.
    # "Continues / Keeps <tape verb>" lives in `_LEAN_AFTER_SUBJECT_RE` since round 2: said of
    # anything but the subject itself ("Intel Warns of Continued Decline in PC Demand", "GPU
    # Demand Keeps Rising") it is news.
    r"|\bback[\s-]to[\s-]back\s+(?:(?:daily|weekly)\s+)?(?:declines|drops|falls|advances"
    r"|sessions|days|weeks)\b(?!\s+(?:in|of)\b)"
    r"|\b(?:daily|weekly|monthly)\s+(?:loss|losses|gain|gains|decline|declines|drop|drops"
    r"|slide|advance|advances)\b(?!\s+(?:in|of)\b)"
    r"|\b(?:losing|winning)\s+(?:run|stretch|spell)\b"
    r"|\badd(?:s|ed|ing)?\s+to\s+(?:its\s+|their\s+)?(?:recent|prior|previous|earlier|early"
    r"|steep|sharp|deep)\s+(?:losses|gains|declines?|slide|drop|advance)\b",
    re.IGNORECASE,
)
# The words that may sit between a schedule word and its event: "Set for Its Q3 Report",
# "Head Into Q3 Report", "Look to Fiscal 2026 Earnings". A closed list, never `\w+`: "Probe
# Into Accounting Report" and "Looks to Raise $2 Billion" are news. ("upcoming" needs no
# entry: its own row refuses it anywhere.)
_LEAN_EVENT_QUAL = (
    r"(?:(?:its|the|their|q[1-4]|fiscal|quarterly|annual|full-year|year-end|next"
    r"|(?:first|second|third|fourth)[\s-]quarter|fy\s?\d{2,4}|20\d\d)\s+){0,3}"
)
_LEAN_EVENT = r"(?:earnings|results|report|print|call(?!\s+options?\b))"  # never "Call Options"
# Round 2 (2026-10-16): "to report" is a schedule — except a late filing, which is a
# catalyst ("Super Micro Fails to Report Annual Results on Time", "Misses Deadline to Report
# Q3 Results", "Seeks Delay to Report…"). Fixed-width look-behinds, one per word.
_LEAN_NOT_LATE = (
    r"(?<!fails\s)(?<!failed\s)(?<!failing\s)(?<!deadline\s)(?<!delay\s)(?<!extension\s)"
    r"(?<!unable\s)"
    # a preannouncement ("Expects to Report Q3 Results Below Guidance") and a granted
    # extension ("Gets More Time to Report") are catalysts too (2026-10-16); the passive
    # "Expected to Report" stays a schedule
    r"(?<!expects\s)(?<!time\s)"
)
# "before" / "into" an event only in the TAPE sense — a price verb or the positioning right
# before it (round 2): "CEO Sold Shares Before Earnings, Filing Shows", "Raises Cash Before
# Q3 Results" and "SEC Probe Into Its Earnings Disclosures" are news.
_LEAN_BEFORE_WORD = (
    r"(?:slid(?:e|es|ing)|slip(?:s|ped|ping)?|fall(?:s|ing)?|fell|ris(?:e|es|ing)|rose"
    r"|climb(?:s|ed|ing)?|dip(?:s|ped|ping)?|drift(?:s|ed|ing)?|lower|higher|jitters"
    r"|positioning|caution|nerves)"
)
_LEAN_INTO_VERB = (
    r"(?:head(?:s|ed|ing)?|go(?:es|ing)?|went|slid(?:e|es|ing)|fall(?:s|ing)?|fell"
    r"|ris(?:e|es|ing)|rose|climb(?:s|ed|ing)?|dip(?:s|ped|ping)?|drift(?:s|ed|ing)?"
    r"|trad(?:e|es|ed|ing)|mov(?:e|es|ed|ing))"
)
# "aftermarket" / "post-close" are the TAPE only when used as an adverb (at the end, before
# punctuation or a linking word) or before a trading noun: "Q3 Results Released After-Market",
# "Slides in the Aftermarket on Debt News", "Post-Close Trading". Before any other noun they
# are an adjective, and news: aerospace's "Commercial Aftermarket Strength", auto parts'
# "Aftermarket Sales", a deal's "Post-Close Integration" / "Post-Close Adjustments"
# (2026-10-16: a closed noun list refused RTX / HEICO / GE Aerospace earnings headlines).
_LEAN_ADVERBIAL = (
    r"(?=\s*$|\s*[.,;:!?)]|\s+(?:on|as|after|amid|despite|following|while|trading|trades?"
    r"|session|moves?|movers|action|selling|buying)\b)"
)
# Trading activity, schedules and after-the-bell events that `price_claims` /
# `stale_timing_claims` do not cover (verified 2026-10-14: "Losing Streak Extends", "Sell-Off
# Deepens", "Volatility Grips…", "Braces for Q3 Report" all passed both). Each is a story
# about the tape, about an event still ahead, or about one that came after the session —
# never a reason the session moved.
_LEAN_TRADING_RE = re.compile(
    r"\bstreaks?\b"
    r"|\bsell-?offs?\b"
    r"|\brouts?\b"
    r"|\bvolatil\w*"
    r"|\b(?:slide|decline|selloff|slump|drop|rally|climb|retreat|losses)\s+(?:continues|deepens"
    r"|extends|accelerates|resumes|worsens|eases|widens)\b"
    r"|\b(?:top|biggest)\s+(?:losers|gainers|decliners|movers)\b"
    r"|\b(?:selling|buying)\s+pressure\b|\bprofit[-\s]taking\b"
    r"|\bgaps?\s+(?:down|up|lower|higher)\b|\bat\s+the\s+open\b"
    # never "Announces Deal Before the Opening Bell": news out before the open is today's
    r"|(?<!before\s)(?<!before\sthe\s)\bopening\s+bell\b"
    r"|\bfrom\s+(?:its\s+|their\s+)?(?:record|peak|all-time|52-week)\b|\bbig\s+run\b"
    # "Off Highs", "Off Its Peak" — never "Pays Off High-Cost Debt" or "Sells Off Its
    # Low-Margin Unit": before a hyphen it is a compound adjective, not the price's high.
    # ("Off 52-Week Highs" needs no qualifier here: the window rule refuses "52-Week".)
    r"|\boff\s+(?:(?:its|their)\s+)?(?:(?:record|session|all-time)\s+)?"
    r"(?:highs|lows|peaks?)\b(?!-)"
    r"|\boff\s+(?:its|their)\s+(?:high|low)\b(?!-)"
    r"|\bmarket\s+(?:value|cap(?:italization)?)\s+(?:shrinks|falls|drops|sinks|plunges|tumbles"
    r"|slides|soars|jumps|rises|climbs|surges|swells|tops|hits|crosses|sheds|loses|erases)\b"
    r"|\b(?:erases?|erased|wipes?\s+out|wiped\s+out|gives?\s+back|gave\s+back|pares?|pared"
    r"|gives?\s+up|gave\s+up)"
    r"\s+(?:\w+\s+){0,4}(?:gains|losses)\b"
    # "reverses" only intraday, or bare gains: "Reverses Losses With First Quarterly Profit"
    # is a turnaround, and news (round 2)
    r"|\brevers(?:es?|ed|ing)\s+(?:(?:its|their)\s+)?(?:(?:early|earlier|initial|session"
    r"|intraday|morning|opening)\s+(?:gains|losses)|gains)\b"
    r"|\b(?:stocks|shares|markets|peers|indexes|indices)\s+(?:lower|higher)\b"
    r"|\bbraces?\s+for\b|\bgears?\s+up\s+for\b|\bpreviews?\b|\bwhat\s+to\s+(?:expect|watch)\b"
    r"|\bawait(?:s|ing)?\b|\bon\s+deck\b|\bupcoming\b"
    r"|\bahead\s+of\s+(?:\w+\s+){0,3}(?:earnings|results|report|call|print|update|meeting"
    r"|vote|decision|launch|ipo|data)\b"
    r"|\bdue\s+(?:on\s+)?(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\w*\b"
    r"|\bset\s+to\s+(?:report|announce|release|unveil|present|debut|list)\b"
    # Schedules (2026-10-16, narrowed in round 2 to the schedule of an EVENT): "Q3 Report
    # Scheduled for Wednesday" names the session's own day, so the weekday rule passes it;
    # "Q3 Earnings Due Wednesday" (never "Report Due to an Audit"), "Set for Q3 Report",
    # "to Report / Will Report Q3 Results on Nov. 12", "Investors Eye Q3 Report", "Look
    # Forward to Q3 Results", "as Q3 Report Nears", "Earnings Loom". "Set for S&P 500
    # Inclusion", "Deal Slated to Close", "Holiday Season Nears" and "Margin Call Looms" are news.
    # Any event set for a WEEKDAY is a schedule too ("Investor Day Set for Wednesday" on the
    # Wednesday session — the weekday rule passes the session's own day).
    rf"|\b{_LEAN_EVENT}\s+(?:(?:is|are)\s+)?due\b(?!\s+to\s+(?!be\b))"
    rf"|\b{_LEAN_EVENT}\s+(?:(?:is|are)\s+)?(?:scheduled|slated|set)\s+(?:for|on|to)\b"
    rf"|\bset\s+for\s+{_LEAN_EVENT_QUAL}{_LEAN_EVENT}\b"
    r"|\b(?:set|scheduled|slated)\s+(?:for|on)\s+(?:mon|tues|wednes|thurs|fri|satur|sun)day\b"
    rf"|\b(?:{_LEAN_NOT_LATE}to|will)\s+(?:report|announce|release|post|publish)\s+"
    rf"{_LEAN_EVENT_QUAL}(?:earnings|results|numbers)\b"
    rf"|\b{_LEAN_BEFORE_WORD}\s+before\s+{_LEAN_EVENT_QUAL}{_LEAN_EVENT}\b"
    rf"|\b{_LEAN_INTO_VERB}\s+into\s+{_LEAN_EVENT_QUAL}{_LEAN_EVENT}\b"
    r"|\b(?:eyes?|eyeing|watch(?:es|ing)?|look(?:s|ing)?\s+(?:to|toward|towards|ahead\s+to"
    rf"|forward\s+to)|turn(?:s|ing)?\s+to)\s+(?:(?:on|for)\s+)?{_LEAN_EVENT_QUAL}{_LEAN_EVENT}\b"
    # "Netflix Eyes Earnings Boost From Ad Tier", "Looks to Earnings Growth" (2026-10-16)
    r"(?!\s+(?:boost|lift|growth|gains?|upside|hit|drag|benefits?|tailwinds?|headwinds?)\b)"
    r"|\b(?:earnings(?:\s+season)?|results|report|print|(?<!margin\s)call|meeting|vote"
    r"|decision|ipo)\s+(?:nears|nearing|looms?|looming|approaches|approaching)\b"
    # After the session: TIMING only. "After Losing Key Customer" and "After Guidance Cut" are
    # events, and news; so is anything announced BEFORE the bell / the open (round 2: news
    # out before the open is today's cause — the tape before the bell is still refused, by
    # the subject's own verb, "CoreWeave Slides Before the Open", or the price guard,
    # "CoreWeave Shares Fall Before the Bell"). The auto-parts "aftermarket" and the FDA's
    # "premarket approval" / "postmarket study" are news too.
    rf"|\bafter[\s-]hours\b|\bpost-?close\b{_LEAN_ADVERBIAL}"
    # ... and after a segment word it is the business: "Commercial Aerospace Aftermarket"
    r"|(?<!aerospace\s)(?<!commercial\s)(?<!auto\s)(?<!automotive\s)(?<!aviation\s)"
    rf"(?<!defense\s)(?<!engine\s)\bafter-?market\b{_LEAN_ADVERBIAL}"
    r"|\bafter\s+(?:the\s+)?(?:bell|close|closing\s+bell|markets?\s+(?:close|closes|closed))\b"
    r"|\b(?:extended|late)\s+trading\b|\bextended[\s-]hours\b"
    r"|\b(?:pre|post)-?market\b(?!\s+(?:stud(?:y|ies)|surveillance|approvals?|safety"
    r"|requirements?|commitments?|data|applications?|clearances?|trials?|monitoring"
    r"|authorizations?|reviews?|notifications?)\b)",
    re.IGNORECASE,
)
# "advance" only intransitive (followed by nothing, punctuation or a preposition): "Moderna
# Continues to Advance Cancer Vaccine Program" is news.
_LEAN_INTRANSITIVE = (
    r"(?=\s*(?:$|[,:;.])|\s+(?:on|as|after|amid|despite|while|following|with)\b)"
)
# A subject that keeps / continues to move AGAINST A RIVAL is competition, and news: "Intel
# Continues to Fall Behind TSMC", "AMD Continues Gains Against Intel" — never against its
# peers or the market ("Continues Its Slide Against Peers" is the tape). Ground lost "to"
# or "in" a market, and gains / losses "in" one, are news too ("Apple Keeps Losing Ground
# to Huawei", "Amazon Continues Gains in Grocery Market Share", 2026-10-16) — a bare "to"
# never is: "Continues to Slide to New Low".
_LEAN_NOT_RIVAL = (
    r"(?!\s+(?:behind\b|against\s+(?!(?:(?:its|their)\s+)?(?:peers|rivals)\b"
    r"|the\s+(?:broader\s+)?(?:market|index|sector|s&p|nasdaq|dow)\b)))"
)
# A verb right after the headline's SUBJECT that narrates the tape ("CoreWeave Falls for…",
# "CoreWeave Shares Extend Slide", "CoreWeave Bounces Back") — the move itself offered as its
# own cause. Ambiguous verbs only in their tape collocation: "Extends Partnership", "Gains
# Approval", "Leads Funding Round" and "Hits Milestone" are news.
_LEAN_AFTER_SUBJECT_RE = re.compile(
    r"(?:['’]s)?\s+(?:(?:shares|stock|stocks|share\s+price)\s+)?(?:"
    r"falls|fell|falling|slides|slid|sliding|drops|dropped|dropping|plunges|plunged|plummets"
    r"|plummeted|tumbles|tumbled|sinks|sank|sinking|slumps|slumped|retreats|retreated|dips"
    r"|dipped|tanks|tanked|craters|cratered|slips|slipped|skids|skidded|rises|rose|rising"
    r"|jumps|jumped|climbs|climbed|soars|soared|surges|surged|rallies|rallied|rebounds"
    r"|rebounded|bounce|bounces|bounced|recovers|recovered|spikes|spiked|pops|popped"
    r"|underperforms|outperforms|outpaces|lags|lagged|trails|trailed|down|up|lower|higher"
    r"|extends?\s+(?:its\s+)?(?:continued\s+)?(?:slide|decline|declines|losses|drop|fall|slump"
    r"|gains|rally|climb|losing|winning|sell-?off|selloff|rout|retreat|rebound)"
    r"|resumes?\s+(?:its\s+)?(?:slide|decline|fall|climb|rally)|pulls?\s+back"
    r"|halves|doubles\s+(?:from|in|since)|leads?\s+(?:\w+\s+){0,3}(?:lower|higher)"
    r"|hits\s+(?:a\s+)?(?:record|all-time|52-week|new\s+(?:high|low))"
    # the price guard's own "<verb> lower/higher" list misses these four (2026-10-16)
    r"|(?:swings?|spirals?|races?|starts?)\s+(?:lower|higher)(?!-)"
    r"|(?:is|are)\s+(?:down|up|lower|higher)"
    # Multi-day tape (round 2, from `_LEAN_WINDOW_RE`): only the SUBJECT continuing its move
    # — "CoreWeave Continues Slide", "CoreWeave Shares Keep Falling", "CoreWeave Adds to
    # Losses". "Continues Expansion", "Adds to Board" and "Keeps Growth Guidance" are news.
    r"|continu(?:e|es|ed|ing)\s+(?:(?:its|their|to)\s+)?(?:slid(?:e|ing)|slip(?:ping)?"
    r"|declin(?:e|ing)|fall(?:ing)?|drop(?:ping)?|sink(?:ing)?|slump(?:ing)?|retreat(?:ing)?"
    rf"|tumbl(?:e|ing)|climb(?:ing)?|ris(?:e|ing)|advanc(?:e|ing){_LEAN_INTRANSITIVE}"
    r"|rebound(?:ing)?|(?:losses|gains)(?!\s+in\b)|(?:lower|higher)(?!-))"
    rf"{_LEAN_NOT_RIVAL}"
    r"|keeps?\s+(?:on\s+)?(?:falling|sliding|slipping|dropping|sinking|declining|tumbling"
    rf"|retreating|climbing|rising|advancing{_LEAN_INTRANSITIVE}"
    r"|(?:losing|gaining)\s+ground(?!\s+(?:in|to)\b))"
    rf"{_LEAN_NOT_RIVAL}"
    r"|add(?:s|ed|ing)?\s+to\s+(?:(?:its|their)\s+)?(?:losses|gains)"
    r"|deepen(?:s|ed)?\s+(?:(?:its|their)\s+)?(?:losses|slide))\b",
    re.IGNORECASE,
)
_LEAN_WEEKDAY_RE = re.compile(
    r"\b(Mon|Tues|Wednes|Thurs|Fri|Satur|Sun)day\b", re.IGNORECASE
)
# Abbreviated weekdays, capitalised only ("Wed" lower-case is a verb). "Sat"/"Sun" only with
# a period — "Sun" is also a word and a company.
_LEAN_WEEKDAY_ABBR_RE = re.compile(
    r"\b(Mon|Tue|Tues|Wed|Weds|Thu|Thur|Thurs|Fri)\b\.?|\b(Sat|Sun)\."
)
_WEEKDAY_ABBR = {
    "mon": "monday", "tue": "tuesday", "tues": "tuesday", "wed": "wednesday",
    "weds": "wednesday", "thu": "thursday", "thur": "thursday", "thurs": "thursday",
    "fri": "friday", "sat": "saturday", "sun": "sunday",
}


class CauseKind(str, Enum):
    """What we were able to establish. Ordered most specific to least."""

    EARNINGS = "earnings"          # reported today or last night
    ANALYST = "analyst"            # an upgrade/downgrade dated today
    COMPANY_NEWS = "company_news"  # a classifiable catalyst in today's headlines, or the
                                   # gated news lean (tag "Bearish News" / "Bullish News")
    SECTOR = "sector"              # moved in line with its industry
    MARKET = "market"              # moved in line with the whole tape
    NONE = "none"                  # nothing identifiable — the honest common case


@dataclass(frozen=True)
class MoveContext:
    """Always-true arithmetic about the move. Never absent, never a guess."""

    change_percent: float
    # |move| / σ_daily. None when σ is unknown — NOT 0.0, which would read as
    # "judged, and perfectly normal" for a ticker we cannot judge at all.
    z: Optional[float] = None
    # The overnight half of the move, when both `open` and `previousClose` were usable.
    gap_percent: Optional[float] = None
    intraday_percent: Optional[float] = None
    # True when the gap carried most of the day — i.e. it moved before anyone could trade.
    gap_dominant: bool = False
    industry_name: Optional[str] = None
    industry_change_percent: Optional[float] = None
    market_change_percent: Optional[float] = None


@dataclass(frozen=True)
class Attribution:
    kind: CauseKind
    # 2-4 word label for the badge ("Earnings Beat", "Analyst Downgrade"). None for NONE.
    tag: Optional[str] = None
    # One punchy sentence. Always non-empty — the NONE case says so explicitly.
    detail: str = ""
    context: Optional[MoveContext] = None
    # Everything we checked and ruled out, for debuggability. Never rendered.
    considered: List[str] = field(default_factory=list)
    # The always-true arithmetic beside an EARNINGS / ANALYST / COMPANY_NEWS cause
    # ("Software - Infrastructure fell 4.0%."). None for SECTOR,
    # MARKET and NONE, whose `detail` already says it. No session word, so no aged twin.
    note: Optional[str] = None
    # "bearish" / "bullish" when the session's Insights card leaned the way the stock moved —
    # whether or not its headline was shown. Lets Ask Cay word a withheld lean honestly.
    lean: Optional[str] = None


@dataclass(frozen=True)
class NewsLean:
    """The Updates AI Insights card for the session being attributed: its headline and its NET
    sentiment ("Bullish" / "Bearish" / "Neutral" — the lean of the news, NOT the price).

    The CALLER has already session-gated the card (`widget_movers_service._classified_today_news`
    / `_classified_rolling_news` returned `had_news`); this module only decides whether the
    headline may stand as the reason. It is the one model-written input here: never generated
    on the read path, only reprinted — and only behind `detect_news_lean`'s gate.
    """

    headline: str
    sentiment: str
    # The names the headline may use for its subject: `news_insight_service.price_terms`.
    subject_terms: Tuple[str, ...] = ()
    # ISO-8601 instant the card was written (for the post-close earnings rule).
    generated_at: Optional[str] = None
    # A round-the-clock asset: no session close, so no post-close rule.
    rolling: bool = False
    # The day the caller gated the card on (the ET calendar day for a 24/7 row, else the
    # session). A headline naming any OTHER weekday is a story about another day.
    day: Optional[date] = None


# ── Helpers ───────────────────────────────────────────────────────────


def _finite(value: Any) -> Optional[float]:
    """A usable float, or None. NaN and ±inf are not usable."""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _parse_day(value: Any) -> Optional[date]:
    """Leading `YYYY-MM-DD` of a date or datetime string."""
    if isinstance(value, date):
        return value
    if not isinstance(value, str) or len(value) < 10:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _pct(value: float) -> str:
    """1 decimal, matching the app's prose convention (labels use 2 — see
    `PriceChangeLabel`; prose uses 1 — see `deterministic_reason`)."""
    return f"{abs(value):.1f}%"


def _dir_word(value: float) -> str:
    return "rose" if value > 0 else "fell"


def compute_gap(
    open_price: Any, previous_close: Any, change_percent: Any
) -> tuple[Optional[float], Optional[float], bool]:
    """Split today's move into its overnight and intraday halves.

    `open` and `previousClose` are both already on the batch-quote row the sweeper
    fetches every five minutes, and nothing else in the repo reads `open` — so this
    costs nothing and is pure arithmetic, which means it cannot be wrong the way a
    model can be wrong.

    It matters because a gap-down happened while nobody could trade: the cause is
    overnight news, an earnings print, or an analyst note published before the bell. A
    stock that opened flat and slid from 14:00 has a different kind of cause entirely.

    Returns `(gap_pct, intraday_pct, gap_dominant)`; the first two are None when either
    price is unusable.
    """
    op = _finite(open_price)
    pc = _finite(previous_close)
    total = _finite(change_percent)
    # BOTH prices must be positive, not just the denominator. A batch-quote row can
    # carry `open: 0` for a name that has not traded yet in the session, and an
    # unguarded `op` turned that into a gap of exactly −100.0% — a number the docstring
    # above promises cannot be produced, and one that then reads as gap-dominant.
    if op is None or pc is None or op <= 0 or pc <= 0 or total is None:
        return None, None, False

    gap = (op - pc) / pc * 100.0
    intraday = total - gap
    dominant = (
        abs(gap) >= _MIN_GAP_PCT
        and abs(total) > 0
        and abs(gap) / abs(total) >= _GAP_DOMINANT_SHARE
        # Same direction: a +3% gap that faded to −1% is not "the gap explains it".
        and (gap > 0) == (total > 0)
    )
    return gap, intraday, dominant


def _moved_with_group(change: float, group_change: Optional[float]) -> bool:
    """Did this stock go where its group went, by roughly the same amount?

    Requires the same direction, a group move big enough to mean something, and a ratio
    inside the in-line band. ACHR is the worked example of the rejection: Aerospace &
    Defense fell 1.2% and ACHR fell 5.0%, a 4.1x ratio — it did NOT move with its
    industry, it massively underperformed it, and saying "sector selloff" there would be
    false.
    """
    g = _finite(group_change)
    if g is None or abs(g) < _MIN_GROUP_MOVE_PCT:
        return False
    if (g > 0) != (change > 0):
        return False
    ratio = abs(change) / abs(g)
    return _SECTOR_INLINE_LOW <= ratio <= _SECTOR_INLINE_HIGH


# ── Individual detectors (each returns an Attribution or None) ────────


_DAY_NAMES = {"Mon": "Monday", "Tue": "Tuesday", "Wed": "Wednesday", "Thu": "Thursday",
              "Fri": "Friday", "Sat": "Saturday", "Sun": "Sunday"}


def session_words(session_word: str = "today") -> tuple[str, str, str]:
    """(adverb, possessive, capitalised possessive) for the session a move belongs to.

    "today" -> ("today", "today's", "Today's"); "on Fri" -> ("on Fri", "Friday's",
    "Friday's"). Every sentence this module writes says WHEN, and pre-market Monday the
    numbers are Friday's — calling them "today's" was the cross-session claim the
    industry gate exists to suppress, printed by the headline itself.
    """
    if session_word == "today":
        return "today", "today's", "Today's"
    day = session_word.replace("on ", "").strip()
    full = _DAY_NAMES.get(day, day)
    return session_word, f"{full}'s", f"{full}'s"


def detect_earnings(
    ticker: str, change: float, earnings_row: Optional[Dict[str, Any]], today: date,
    session_word: str = "today",
) -> Optional[Attribution]:
    """Reported today or last night — and ONLY if it has actually reported.

    The row comes from the market-wide `earnings-calendar` — ONE FMP call covering every
    ticker, which the earnings notification sender already makes daily and then throws
    away.

    ⚠️ A CALENDAR ROW IS A SCHEDULE, NOT AN EVENT. The calendar lists what is *due*, so a
    today-dated row is routinely a company that will report TONIGHT. Gating on the date
    alone made the widget say "NVDA reported earnings this morning" at 11:00 about a
    company that had not reported at all — and because earnings sits at the top of the
    precedence chain, it also discarded the move's real cause (a same-day downgrade).
    A fabricated event outranking a true one is the worst failure this module can have.

    The reliable signal is `epsActual`: `earnings_service` and `earnings_sender` both
    already use its presence as the "has reported" test. FMP's `time` field is free-form
    and often blank, so it is a useful *secondary* guard and a wording input, never the
    primary gate.

    TRADING-DAY AWARE. "Last night" is the previous TRADING day's close, not the calendar
    day before: a Friday after-close print is what moved Monday, and the Tuesday after a
    Monday holiday follows Friday. A calendar-day gate rejected both and let the tile fall
    through to "No clear catalyst" on the headline. Across such a gap the print must be
    EXPLICITLY after the close — FMP's `time` is often blank, and a blank Friday row may be
    a before-open print the Friday tape already priced; a fabricated cause is worse than a
    missed one.
    """
    if not isinstance(earnings_row, dict):
        return None
    when = _parse_day(earnings_row.get("date"))
    if when is None or when > today:
        return None
    # Pure: the holiday table, no clock. Imported lazily like the timing helpers below.
    from app.utils.market_hours import previous_trading_day

    gap_days = (today - when).days
    if gap_days > _EARNINGS_MAX_AGE_DAYS and when != previous_trading_day(today):
        return None

    actual = _finite(earnings_row.get("epsActual"))
    estimate = _finite(earnings_row.get("epsEstimated"))

    from app.services._earnings_common import (
        AFTER_CLOSE,
        BEFORE_OPEN,
        parse_fmp_timing,
    )

    timing = parse_fmp_timing(earnings_row.get("time"))

    if actual is None:
        # No reported number. For a TODAY-dated row that means it has not happened yet
        # (or the print has not landed), so it cannot be today's cause.
        if when == today:
            return None
        # A t-1 row with no number is ambiguous enough that claiming it moved today's
        # tape is not supportable either.
        return None

    # `today` is the STAMPED session, not the wall-clock day: pre-market Tuesday it is
    # Monday, and Monday's BMO print is not "this morning" any more. The sibling
    # detectors already take `session_word`; without it the headline asserted a false
    # WHEN ("reported this morning" shown Tuesday 07:00 about Monday's print).
    _adverb, possessive, _cap = session_words(session_word)
    live_session = session_word == "today"
    if when == today:
        # A same-day AFTER-CLOSE print cannot explain the session that preceded it.
        if timing == AFTER_CLOSE:
            return None
        when_word = "this morning" if live_session else f"before {possessive} open"
    else:
        # Yesterday's row: say which side of the session it landed on rather than
        # assuming "after the close". A BMO print is ~30 hours old and belongs to the
        # PREVIOUS session's move, not this one.
        if timing == BEFORE_OPEN:
            return None
        if gap_days > 1:
            # Across a weekend / holiday: only an explicit after-close print (docstring).
            if timing != AFTER_CLOSE:
                return None
            # "yesterday" would be false on a Monday — name the day the print landed.
            day = when.strftime("%a")
            when_word = f"after {_DAY_NAMES.get(day, day)}'s close"
        else:
            when_word = "after yesterday's close" if live_session else "after the prior close"

    beat: Optional[bool] = None
    surprise: Optional[float] = None
    if estimate is not None and abs(estimate) > 1e-9:
        surprise = (actual - estimate) / abs(estimate) * 100.0
        beat = actual > estimate

    # An in-line print is neither a beat nor a miss, and "beat estimates by 0.0%"
    # contradicts itself in a single sentence.
    if beat is None or surprise is None or abs(surprise) < 0.05:
        tag = "Earnings"
        detail = f"{ticker} reported earnings {when_word}."
    elif beat:
        tag = "Earnings Beat"
        detail = f"{ticker} beat EPS estimates by {_pct(surprise)}, reported {when_word}."
    else:
        tag = "Earnings Miss"
        detail = f"{ticker} missed EPS estimates by {_pct(abs(surprise))}, reported {when_word}."
    return Attribution(kind=CauseKind.EARNINGS, tag=tag, detail=detail)


def detect_analyst_action(
    ticker: str, grade_rows: Optional[Sequence[Dict[str, Any]]], today: date,
    session_word: str = "today",
) -> Optional[Attribution]:
    """An upgrade / downgrade dated today.

    Age-gated hard. A stale rating is the single easiest way to produce a confident lie:
    ACHR's most recent action was 2026-05-12, three months before the 5% drop this is
    meant to explain.
    """
    for row in grade_rows or []:
        if not isinstance(row, dict):
            continue
        when = _parse_day(row.get("date") or row.get("publishedDate"))
        if when is None or (today - when).days >= _GRADE_MAX_AGE_DAYS or when > today:
            continue

        firm = str(row.get("gradingCompany") or "").strip()
        new_grade = str(row.get("newGrade") or "").strip()
        prev = str(row.get("previousGrade") or "").strip()

        # Normalise via the SHARED classifier rather than substring-matching the raw
        # string. FMP's `action` is inconsistent — it labels genuine cuts "maintain" —
        # and `normalize_fmp_action` exists precisely to fall back to comparing
        # previousGrade against newGrade on a 0-4 rank when the label cannot be trusted.
        # Matching the raw text here dropped real same-day rating changes, and the
        # detector below then asserted a SECTOR cause instead: a false explanation
        # substituted for a true one.
        from app.services._analyst_common import normalize_fmp_action

        action = normalize_fmp_action(row.get("action"), prev, new_grade)

        if action == "downgrade":
            tag = "Analyst Downgrade"
            verb = "downgraded"
        elif action == "upgrade":
            tag = "Analyst Upgrade"
            verb = "upgraded"
        elif action == "initiate":
            tag = "New Coverage"
            verb = "initiated coverage on"
        else:
            # "maintain" is not why a stock moved 5% — it is the absence of a change.
            continue

        who = firm or "An analyst"
        detail = f"{who} {verb} {ticker}"
        if new_grade:
            detail += f" to {new_grade}"
            if prev and prev.lower() != new_grade.lower():
                detail += f" from {prev}"
        detail += f" {session_words(session_word)[0]}."
        return Attribution(kind=CauseKind.ANALYST, tag=tag, detail=detail)
    return None


def detect_company_news(
    ticker: str,
    classified: Optional[Sequence[tuple[str, str]]],
) -> Optional[Attribution]:
    """A classifiable catalyst among TODAY's headlines.

    `classified` is `[(tag, headline)]` — the caller runs the existing
    `_classify_news_catalyst` over today's `ticker_news_cache` rows, so the eleven-tag
    vocabulary (FDA Approval, M&A, Guidance Cut, …) stays in one place.
    """
    for entry in classified or []:
        try:
            tag, headline = entry
        except (TypeError, ValueError):
            continue
        tag = str(tag or "").strip()
        headline = str(headline or "").strip()
        if not tag or not headline:
            continue
        return Attribution(
            kind=CauseKind.COMPANY_NEWS, tag=tag, detail=headline.rstrip(".") + "."
        )
    return None


def detect_group_move(
    ticker: str,
    change: float,
    industry_name: Optional[str],
    industry_change: Optional[float],
    market_change: Optional[float],
    session_word: str = "today",
) -> Optional[Attribution]:
    """It went where its industry, or the whole tape, went."""
    when = session_words(session_word)[0]
    if _moved_with_group(change, industry_change):
        name = (industry_name or "its industry").strip()
        return Attribution(
            kind=CauseKind.SECTOR,
            tag="Sector Move",
            detail=(
                f"{name} {_dir_word(industry_change or 0)} "
                f"{_pct(industry_change or 0)} {when}; {ticker} moved with it."
            ),
        )
    if _moved_with_group(change, market_change):
        return Attribution(
            kind=CauseKind.MARKET,
            tag="Market Move",
            detail=(
                f"The market {_dir_word(market_change or 0)} "
                f"{_pct(market_change or 0)} {when}; {ticker} moved with it."
            ),
        )
    return None


def _subject_ends(headline: str, terms: Sequence[str]) -> List[int]:
    """Where the headline names ITS OWN subject — the end offset of each mention; empty when
    it never does (a peer's, the sector's or the index's story).

    * A ticker ("CRWV", "BRK-B", "P&G") matches exactly as written, anywhere, but never on a
      hyphen ("T-Mobile" is not AT&T) — and a one-letter symbol or one that is ordinary
      prose ("AI", "IT", `insight_conclusion._PC_TICKER_STOP`) never counts alone: "AI
      Spending Doubts Weigh on Software Makers" is not C3.ai's news.
    * A name matches only capitalised, with straight and curly apostrophes alike and
      "amazon.com" as "Amazon" — the price guard's own `_term_regex`. A MULTI-word name
      ("joby aviation") anywhere; a ONE-word name only as the headline's subject: leading it
      (after an optional "The") or possessive. Title case capitalises every word, so
      "…Rethink Strategy Under New Rules" (MSTR) or "Walmart Misses Target…" (TGT) is
      otherwise indistinguishable from the company.
    """
    from app.services.insight_conclusion import _PC_TICKER_STOP, _term_regex

    ends: List[int] = []
    for raw in terms:
        term = " ".join(str(raw or "").split())
        if not term:
            continue
        if term.upper() == term and any(c.isalpha() for c in term) and " " not in term:
            if len(term) == 1 or term in _PC_TICKER_STOP:
                continue
            pattern = rf"(?<![A-Za-z0-9-]){re.escape(term)}(?![A-Za-z0-9-])"
            ends.extend(m.end() for m in re.finditer(pattern, headline))
            continue
        try:
            body = _term_regex(term)
        except Exception:  # a malformed name is no name; the symbol may still match
            continue
        if " " in term:
            pattern = rf"(?<![A-Za-z0-9]){body}(?![A-Za-z0-9])"
            ends.extend(m.end() for m in re.finditer(pattern, headline, re.IGNORECASE))
            continue
        lead = re.match(rf"(?:The\s+)?{body}(?![A-Za-z0-9])", headline, re.IGNORECASE)
        if lead:
            ends.append(lead.end())
        ends.extend(
            m.end(1) for m in re.finditer(
                rf"(?<![A-Za-z0-9])({body})['’]s\b", headline, re.IGNORECASE
            )
        )
    return sorted(set(ends))


def _names_another_day(headline: str, day: date) -> bool:
    """Does the headline name a weekday other than `day`'s, in full or abbreviated?"""
    named = {m.group(0).lower() for m in _LEAN_WEEKDAY_RE.finditer(headline)}
    for m in _LEAN_WEEKDAY_ABBR_RE.finditer(headline):
        named.add(_WEEKDAY_ABBR[(m.group(1) or m.group(2)).lower()])
    return bool(named) and named != {day.strftime("%A").lower()}


def usable_lean_headline(
    headline: Any,
    ticker: str,
    subject_terms: Sequence[str] = (),
    day: Optional[date] = None,
) -> Optional[str]:
    """The headline, whitespace-collapsed, if it may stand as the reason the stock moved —
    else None. FAIL-CLOSED: a refusal costs the "news leaned" sentence, a pass prints model
    text as a cause, so every doubt refuses.

    Refused: not a string; outside 12-100 characters; clipped ("…", "...") or ending "?" /
    "!" (a question explains nothing; clickbait is banned upstream); a relative day (it is
    reprinted as `detail_aged`); a multi-day window; trading activity or a schedule
    (`_LEAN_TRADING_RE`, `insight_conclusion.stale_timing_claims`); a price move or level
    (`insight_conclusion.price_claims`, with the company's own names — the card's read-time
    net only knows the symbol); a weekday other than `day`'s; and a headline that does not
    name its subject at all — a peer's, the sector's or the index's story ("AI Stocks
    Retreat…", "Wall Street Falls…" under SPY) explains nothing about this ticker.
    """
    if not isinstance(headline, str):
        return None
    h = re.sub(r"\s+", " ", headline).strip()
    if not (_LEAN_MIN_CHARS <= len(h) <= _LEAN_MAX_CHARS):
        return None
    if "…" in h or h.endswith("...") or h.endswith("?") or h.endswith("!"):
        return None
    if (
        _LEAN_RELATIVE_DAY_RE.search(h)
        or _LEAN_WINDOW_RE.search(h)
        or _LEAN_TRADING_RE.search(h)
    ):
        return None
    if day is not None and _names_another_day(h, day):
        return None
    terms = tuple(dict.fromkeys([ticker, *[str(t) for t in subject_terms if t]]))
    ends = _subject_ends(h, terms)
    if not ends:
        return None
    # The subject's own verb narrating the tape: the move offered as its own cause.
    if any(_LEAN_AFTER_SUBJECT_RE.match(h, end) for end in ends):
        return None
    # Pure, stdlib-only, compiled once at import; lazy like the module's other imports.
    from app.services.insight_conclusion import price_claims, stale_timing_claims

    if stale_timing_claims(h) or price_claims(h, terms):
        return None
    return h


def _written_after_close(generated_at: Any, today: date) -> bool:
    """Was the card written after `today`'s close (+ slack)? Unreadable → True: the only
    caller refuses on True, and a card it cannot place in time must not stand as a cause."""
    from app.utils.market_hours import ET, _close_minute, to_utc_instant

    ts = to_utc_instant(generated_at)
    if ts is None:
        return True
    local = ts.astimezone(ET)
    if local.date() != today:
        return local.date() > today
    minute = local.hour * 60 + local.minute
    return minute > _close_minute(today) + _POST_CLOSE_SLACK_MINUTES


def detect_news_lean(
    ticker: str,
    change: float,
    z: Optional[float],
    today: date,
    lean: Optional[NewsLean],
    earnings_row: Optional[Dict[str, Any]] = None,
) -> Tuple[Optional[Attribution], Optional[str]]:
    """The session's Insights card as the reason — `(attribution, leaned)`.

    `leaned` is "bearish" / "bullish" whenever the card's sentiment points the way the stock
    moved, shown or not; `attribution` is set only when the headline may stand as the cause.
    The gate, in order (owner, 2026-10-14):

    1. a card, already session-gated by the caller;
    2. sentiment Bearish or Bullish — Neutral never (balanced news under −8.84% reads as a
       cause it is not);
    3. the stock moved (not flat) and in the card's direction — ACHR's all-bullish card
       under −5.02% fails here, the first build's bug;
    4. an Unusual move: z ≥ 2, or ≥ 5% when σ is unknown;
    5. an equity card written after the close on a day the company reported is about the
       print, which cannot explain the session before it (`detect_earnings`' rule);
    6. `usable_lean_headline`.

    Steps 4-6 withhold the headline but keep `leaned`: the reader then gets "Today's news
    leaned bearish." — true, and the same lean the app shows — never "No clear catalyst".
    """
    if lean is None:
        return None, None
    c = _finite(change)
    if c is None or abs(c) < _FLAT_PCT:
        return None, None
    sentiment = str(getattr(lean, "sentiment", "") or "").strip().lower()
    tag = NEWS_LEAN_TAGS.get(sentiment)
    if tag is None or (sentiment == "bearish") != (c < 0):
        return None, None
    leaned = sentiment

    zf = _finite(z)
    if zf is not None:
        if zf < _LEAN_MIN_Z:
            return None, leaned
    elif abs(c) < _LEAN_MIN_PCT_NO_SIGMA:
        return None, leaned

    if not getattr(lean, "rolling", False) and isinstance(earnings_row, dict):
        if _parse_day(earnings_row.get("date")) == today and _written_after_close(
            getattr(lean, "generated_at", None), today
        ):
            return None, leaned

    h = usable_lean_headline(
        getattr(lean, "headline", None),
        ticker,
        getattr(lean, "subject_terms", ()) or (),
        getattr(lean, "day", None) or today,
    )
    if h is None:
        return None, leaned
    detail = h if h.endswith(".") else h + "."
    return Attribution(kind=CauseKind.COMPANY_NEWS, tag=tag, detail=detail, lean=leaned), leaned


def describe_context(
    ticker: str, ctx: MoveContext, *, include_in_line: bool = False
) -> List[str]:
    """The always-true arithmetic sentences about the move — no session word in any of them.

    How it compares with its industry (flat / the industry's figure when the stock far
    outran it / the other way) and whether an overnight gap carried it. `include_in_line` adds "…; T moved with it." — for the NOTE
    under an established cause only: `describe_no_cause` never needs it, because an in-line
    move is a SECTOR cause and never reaches NONE.
    """
    parts: List[str] = []

    ind = _finite(ctx.industry_change_percent)
    if ind is not None and ctx.industry_name and abs(ind) >= 0.1:
        same_dir = (ind > 0) == (ctx.change_percent > 0)
        if abs(ctx.change_percent) < _FLAT_PCT:
            # A move that displays as 0.00% has no direction. The sign test below read
            # 0.0 (and −0.004) as "the other way" from a rising industry — a sentence
            # contradicting the flat badge printed beside it.
            parts.append(
                f"{ticker} was flat while {ctx.industry_name} {_dir_word(ind)} {_pct(ind)}."
            )
        elif same_dir and abs(ctx.change_percent) > abs(ind) * _SECTOR_INLINE_HIGH:
            # The industry's figure alone (owner, 2026-10-30): the stock's own percent is
            # printed beside this line, so "; T moved far more." only restated it.
            parts.append(f"{ctx.industry_name} {_dir_word(ind)} {_pct(ind)}.")
        elif not same_dir:
            parts.append(
                f"{ctx.industry_name} {_dir_word(ind)} {_pct(ind)}; "
                f"{ticker} went the other way."
            )
        elif include_in_line and _moved_with_group(ctx.change_percent, ind):
            parts.append(
                f"{ctx.industry_name} {_dir_word(ind)} {_pct(ind)}; "
                f"{ticker} moved with it."
            )

    if ctx.gap_dominant and ctx.gap_percent is not None:
        parts.append("Most of it was an overnight gap at the open.")
    return parts


def context_note(ticker: str, ctx: Optional[MoveContext]) -> Optional[str]:
    """`describe_context` as one line for `Attribution.note`, or None when there is none."""
    if ctx is None:
        return None
    return " ".join(describe_context(ticker, ctx, include_in_line=True)) or None


# The causes whose `detail` says nothing about the industry, so the note adds a true fact.
_NOTED_KINDS = frozenset({CauseKind.EARNINGS, CauseKind.ANALYST, CauseKind.COMPANY_NEWS})


def describe_no_cause(
    ticker: str, ctx: MoveContext, had_news: bool, news_checked: bool = True,
    session_word: str = "today", leaned: Optional[str] = None,
) -> str:
    """The honest answer, and the most common one.

    Says the most informative TRUE things available instead of apologising: how unusual
    the move was for this particular stock, and how it compares with its industry. For
    ACHR that reads "Aerospace & Defense fell 1.2%." beside its own −5.0% — which is
    genuinely useful, and is exactly what a generic PR headline failed to convey.

    `news_checked=False` means the news lookup itself FAILED — a Supabase error, or a
    ticker the sweeper has never covered. That is a third state, and it must not be
    collapsed into either of the other two. "No company news today" is an assertion about
    the world; making it because a database call errored is a confident lie, and the
    reader has no way to tell it from a real finding. Silence about what we could not
    check is the only honest option left.

    `leaned` ("bearish" / "bullish") is the session's Insights card agreeing with the move
    while its headline was withheld (`detect_news_lean`). "No clear catalyst" would then
    contradict the Bearish card the app shows beside it, so the sentence states the lean
    instead — true, and claims no cause.
    """
    parts = describe_context(ticker, ctx)

    when, poss, poss_cap = session_words(session_word)
    if not news_checked:
        # Do NOT assert a negative we never established. If the arithmetic above already
        # said something true and useful, let that stand alone rather than appending a
        # hedge nobody can act on.
        if not parts:
            parts.append(f"{poss_cap} news could not be checked.")
    elif not had_news:
        parts.append(f"No company news {when}.")
    elif leaned in NEWS_LEAN_TAGS:
        parts.append(f"{poss_cap} news leaned {leaned}.")
    else:
        parts.append(f"No clear catalyst in {poss} news.")
    return " ".join(parts)


# ── The entry point ───────────────────────────────────────────────────


def attribute(
    *,
    ticker: str,
    change_percent: Any,
    today: date,
    sigma_daily: Any = None,
    z: Optional[float] = None,
    open_price: Any = None,
    previous_close: Any = None,
    industry_name: Optional[str] = None,
    industry_change_percent: Any = None,
    market_change_percent: Any = None,
    earnings_row: Optional[Dict[str, Any]] = None,
    grade_rows: Optional[Sequence[Dict[str, Any]]] = None,
    classified_news: Optional[Sequence[tuple[str, str]]] = None,
    had_news: bool = False,
    # False when the news lookup FAILED, as opposed to succeeding and finding nothing.
    # Defaults True so every existing caller and test keeps its current meaning.
    news_checked: bool = True,
    # "today", or "on Fri" when `today` is a prior session (see `session_words`).
    session_word: str = "today",
    # The session's Insights card (already session-gated by the caller), or None.
    news_lean: Optional[NewsLean] = None,
) -> Optional[Attribution]:
    """Best available same-day explanation, plus always-true context.

    Returns None only when the move itself is unreadable — an unusable quote is not a
    flat day and must not be presented as one. Every other path returns an Attribution,
    including `CauseKind.NONE`.

    Precedence: the news lean > earnings > analyst > classified company news > group move >
    none. The lean leads (owner, 2026-10-30; see the module's PRECEDENCE) but only behind its
    own unchanged gate, so a card that disagrees with the move, a routine move, or an
    after-close card on a report day still falls through to the dated detectors. Nothing is
    lost when it wins: the note keeps the industry arithmetic, "moved with it" included.
    """
    change = _finite(change_percent)
    if change is None:
        return None

    if z is None:
        # Lazily importing keeps this module free of a services-layer dependency at
        # import time; `move_z` is itself pure.
        from app.services.updates_materiality import move_z

        z = move_z(change, sigma_daily)

    gap, intraday, gap_dominant = compute_gap(open_price, previous_close, change)
    ctx = MoveContext(
        change_percent=change,
        z=z,
        gap_percent=gap,
        intraday_percent=intraday,
        gap_dominant=gap_dominant,
        industry_name=(industry_name or None),
        industry_change_percent=_finite(industry_change_percent),
        market_change_percent=_finite(market_change_percent),
    )

    # The news lean FIRST — see the docstring. Its `leaned` also words the NONE sentence below.
    leaned: Optional[str] = None
    try:
        lean_hit, leaned = detect_news_lean(
            ticker, change, z, today, news_lean, earnings_row
        )
    except Exception as e:  # a malformed card must never break the widget
        logger.warning(
            "daily_move_attribution: news lean raised for %s (%s: %s)",
            ticker, type(e).__name__, e,
        )
        lean_hit, leaned = None, None
    if lean_hit is not None:
        return Attribution(
            kind=lean_hit.kind,
            tag=lean_hit.tag,
            detail=lean_hit.detail,
            context=ctx,
            # Nothing was checked and ruled out before it.
            considered=[],
            note=context_note(ticker, ctx),
            lean=leaned,
        )

    considered: List[str] = []
    for name, detector in (
        ("earnings", lambda: detect_earnings(ticker, change, earnings_row, today, session_word)),
        ("analyst", lambda: detect_analyst_action(ticker, grade_rows, today, session_word)),
        ("company_news", lambda: detect_company_news(ticker, classified_news)),
        (
            "group",
            lambda: detect_group_move(
                ticker,
                change,
                ctx.industry_name,
                ctx.industry_change_percent,
                ctx.market_change_percent,
                session_word,
            ),
        ),
    ):
        try:
            hit = detector()
        except Exception as e:  # a malformed upstream row must never break the widget
            logger.warning(
                "daily_move_attribution: %s detector raised for %s (%s: %s)",
                name, ticker, type(e).__name__, e,
            )
            hit = None
        if hit is not None:
            # No `lean` here even when the card agreed: the answer did not use it, and a set
            # `lean` marks a turn as built on the card (`web_assisted`, review 2026-10-29).
            return Attribution(
                kind=hit.kind,
                tag=hit.tag,
                detail=hit.detail,
                context=ctx,
                considered=considered,
                note=context_note(ticker, ctx) if hit.kind in _NOTED_KINDS else None,
            )
        considered.append(name)

    return Attribution(
        kind=CauseKind.NONE,
        tag=None,
        detail=describe_no_cause(
            ticker, ctx, had_news, news_checked, session_word, leaned=leaned
        ),
        context=ctx,
        considered=considered,
        lean=leaned,
    )


__all__ = [
    "CauseKind",
    "MoveContext",
    "Attribution",
    "NewsLean",
    "NEWS_LEAN_TAGS",
    "attribute",
    "compute_gap",
    "detect_earnings",
    "detect_analyst_action",
    "detect_company_news",
    "detect_group_move",
    "detect_news_lean",
    "usable_lean_headline",
    "describe_context",
    "context_note",
    "describe_no_cause",
]
