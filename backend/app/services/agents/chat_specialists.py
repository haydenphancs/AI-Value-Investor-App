"""Chat topic specialists for the multi-agent router (Phase 3).

A "specialist" is a focused analyst LENS: a short system-prompt extension that shapes the voice +
what to emphasize. Specialists deliberately keep the tool set the ASSET CLASS is granted
(`chat_tools.tools_for_asset_type`) — the specialization is the lens, not a tool restriction, so
a specialist can never be starved of data it needs. A lens must not ORDER a tool the class lacks
(the router is not asset-aware): phrase tool references as "the X tool you are offered". The base Cay AI system instruction (identity rule + brevity +
grounding) already applies; the specialist focus is appended.

Mirrors the registry pattern of ``persona_config`` (keyed configs + a loud-fallback getter).
"""

from dataclasses import dataclass
from typing import Dict, Tuple


@dataclass(frozen=True)
class ChatSpecialist:
    key: str
    label: str   # short human label for the "Routing to X" thinking step
    focus: str   # system-prompt extension (the analyst lens); "" = the plain general agent


_GENERAL = ChatSpecialist(
    key="general",
    label="General",
    focus="",  # no extension — the default Cay AI behavior
)

_SPECIALISTS: Dict[str, ChatSpecialist] = {
    # 2026-10-08: no "forward P/E" and no "price vs. analyst targets" anchor. No chat data
    # carries a forward multiple (the index card's is a 0 placeholder, flagged
    # `forward_pe_known=False`), analyst price targets are outside the data licence, and a
    # fair-value figure is a MODEL estimate — naming any of them as an anchor invited the model
    # to supply it from memory. Final review 2026-10-09: the no-analyst-tool clause names only
    # ratings and price targets (the unlicensed part) — "no analyst data" contradicted the base
    # prompt's licensed revenue / EPS ESTIMATES and, appended last, won on every valuation turn.
    "valuation": ChatSpecialist(
        "valuation", "Valuation",
        "LENS: Answer through a VALUATION lens — is it cheap or expensive, and why? Anchor on "
        "P/E and the other multiples in your data (earnings yield and any others it gives, each "
        "with its period and basis), and what the multiple implies about expectations. Use a "
        "forward multiple only if your data states one, and never read a 0 or a missing multiple "
        "as a value. A fair-value figure is a model estimate, never a price target — never give "
        "a price target, and do not anchor on analyst price targets. Pull the price tool you are "
        "offered (on an index screen that is the market-overview tool, which carries the index's "
        "P/E and earnings yield when they are known) and the analyst tool when it is offered, "
        "for real numbers; if no analyst tool is offered, say Caydex has no analyst ratings or "
        "price targets rather than recalling them; analysts' revenue and EPS estimates, when "
        "your data or the estimates section of the financials tool you are offered gives them, "
        "may be used, labelled as estimates and never as a rating, recommendation or target. "
        "Don't drift into unrelated technicals or macro.",
    ),
    "technicals": ChatSpecialist(
        "technicals", "Technicals",
        "LENS: Answer through a TECHNICAL lens — recent price action, trend, momentum, and notable "
        "moves. Use the price/chart tool when you are offered one for the real move; on an index "
        "screen the level and today's change are in the screen data when present, and the "
        "market-overview tool carries valuation and breadth, not the level. Keep it about how "
        "it's TRADING, not the underlying business.",
    ),
    "fundamentals": ChatSpecialist(
        "fundamentals", "Fundamentals",
        "LENS: Answer through a FUNDAMENTALS lens — revenue/earnings growth, margins, balance-sheet "
        "health, moat, and business quality. Ground claims in the company data you were given, "
        "and in the financials tool when you are offered one; name the period of every figure "
        "(fiscal year or trailing twelve months). This is about the BUSINESS, not the chart.",
    ),
    "macro": ChatSpecialist(
        "macro", "Macro",
        "LENS: Answer through a MACRO / market lens — overall conditions, valuations, sector "
        # `get_market_snapshot` named FIRST and unconditionally, because this lens is the one
        # routinely selected for "why is <sector> lagging" — and until that tool existed the
        # instruction below pointed at `get_market_overview`, which `_TOOLS_BY_ASSET_TYPE`
        # granted to INDEX chats ONLY. On every other screen this lens was being told to call
        # a tool that was not in its declaration list, which is how it ended up explaining
        # that it could not do sectors at all.
        "rotation, rates, and macro drivers. Use get_market_snapshot for sector performance, "
        "market breadth and what is moving today — it names every sector, so answer sector "
        "questions from it rather than declining them — and for its dated macro readings on "
        "rates, inflation, unemployment and exchange rates, each with its date; the VIX and the "
        "DXY are not in Caydex data, so never estimate them. On an index screen, also use the "
        "market-overview tool for "
        # Was "Do NOT name specific indices — say 'the market'", a second copy of the gag that
        # `_ASSET_PERSONAS["INDEX"]` carried. This lens is selected on index detail screens too,
        # so leaving it here would have re-imposed the evasion the persona fix removes.
        "questions. Name the index you are actually discussing; say 'the market' only when you "
        "mean conditions broadly rather than one specific index.",
    ),
    "sentiment": ChatSpecialist(
        "sentiment", "Sentiment",
        "LENS: Answer through a SENTIMENT lens — market mood, social + news sentiment, positioning, "
        "and why it feels bullish or bearish. Use the sentiment tool when you are offered one "
        "(an index or a commodity has no per-symbol mood; use the market snapshot's breadth and "
        "news summary instead) and explain what the mood means in plain language.",
    ),
    "education": ChatSpecialist(
        "education", "Education",
        "LENS: Answer as an EDUCATOR — explain the concept clearly in plain language with one simple "
        "example. Live data is optional and only to illustrate; don't force a tool call.",
    ),
    "general": _GENERAL,
}

# Selectable keys (specialists first, general last as the fallback).
SPECIALIST_KEYS: Tuple[str, ...] = (
    "valuation", "technicals", "fundamentals", "macro", "sentiment", "education", "general",
)


def get_specialist(key: str) -> ChatSpecialist:
    """Return the specialist for ``key`` (case-insensitive); unknown → the general agent."""
    return _SPECIALISTS.get((key or "").strip().lower(), _GENERAL)


def apply_specialist(system_instruction: str, key: str) -> str:
    """Append the specialist's focus lens to the base system instruction. General → unchanged.
    Includes a guard so the model answers WITH the emphasis without narrating it (no "lens"/"as a
    valuation specialist" meta-phrasing leaking into the user-facing answer)."""
    focus = get_specialist(key).focus
    if not focus:
        return system_instruction
    return (
        f"{system_instruction}\n\n{focus}\n"
        "Answer WITH this emphasis, but never mention it — do not use the words 'lens', "
        "'perspective', or 'specialist', and don't say 'as a … analyst'. Just answer."
    )
