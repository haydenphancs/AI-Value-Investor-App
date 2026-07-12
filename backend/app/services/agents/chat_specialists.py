"""Chat topic specialists for the multi-agent router (Phase 3).

A "specialist" is a focused analyst LENS: a short system-prompt extension that shapes the voice +
what to emphasize. Specialists deliberately keep the FULL chat tool set (chart / analyst / sentiment
/ market-overview) — the specialization is the lens, not a tool restriction, so a specialist can
never be starved of data it needs. The base Cay AI system instruction (identity rule + brevity +
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
    "valuation": ChatSpecialist(
        "valuation", "Valuation",
        "LENS: Answer through a VALUATION lens — is it cheap or expensive, and why? Anchor on P/E, "
        "forward P/E, earnings yield, price vs. analyst targets, and what the multiple implies about "
        "expectations. Pull the analyst + price tools for real numbers. Don't drift into unrelated "
        "technicals or macro.",
    ),
    "technicals": ChatSpecialist(
        "technicals", "Technicals",
        "LENS: Answer through a TECHNICAL lens — recent price action, trend, momentum, and notable "
        "moves. Use the price/chart tool for the real move. Keep it about how it's TRADING, not the "
        "underlying business.",
    ),
    "fundamentals": ChatSpecialist(
        "fundamentals", "Fundamentals",
        "LENS: Answer through a FUNDAMENTALS lens — revenue/earnings growth, margins, balance-sheet "
        "health, moat, and business quality. Ground claims in the provided financials + tool data. "
        "This is about the BUSINESS, not the chart.",
    ),
    "macro": ChatSpecialist(
        "macro", "Macro",
        "LENS: Answer through a MACRO / market lens — overall conditions, valuations, sector "
        "rotation, rates, and macro drivers. Use the market-overview tool for market/index "
        "questions. Do NOT name specific indices — say 'the market'.",
    ),
    "sentiment": ChatSpecialist(
        "sentiment", "Sentiment",
        "LENS: Answer through a SENTIMENT lens — market mood, social + news sentiment, positioning, "
        "and why it feels bullish or bearish. Use the sentiment tool and explain what the mood "
        "means in plain language.",
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
    """Append the specialist's focus lens to the base system instruction. General → unchanged."""
    focus = get_specialist(key).focus
    return f"{system_instruction}\n\n{focus}" if focus else system_instruction
