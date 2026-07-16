"""Output-side guardrails for the chat answer (Phase 5).

MONITORING, not enforcement: `scan_answer` detects likely violations of the two hard rules for a
fintech assistant — a personal buy/sell/hold DIRECTIVE, or a leak of the underlying model/provider —
and the endpoint LOGS them (so regressions are visible in prod) WITHOUT altering the answer. Hard
blocking is deliberately avoided: a false positive silently dropping a good answer is worse than a
logged flag a human can review. The primary defense stays the system prompt (identity rule + the
advice-boundary directive); this is the safety net that makes drift observable.
"""

import re
from typing import List

# Personal directives (imperative "you should buy/sell/hold" style). Kept targeted — explaining
# tradeoffs ("some investors consider…") is fine and must NOT trip these.
_ADVICE_PATTERNS = (
    "you should buy", "you should sell", "you should hold",
    "you ought to buy", "you ought to sell",
    "i recommend buying", "i recommend selling", "i'd recommend buying", "i'd recommend selling",
    "my recommendation is to buy", "my recommendation is to sell",
    "you must buy", "you must sell", "you need to buy", "you need to sell",
    "buy this stock now", "sell this stock now", "definitely buy", "definitely sell",
    "i'd buy it", "i would buy it", "i'd sell it", "i would sell it",
)

# Underlying-model / provider leaks (NOT bare "google", which is a legitimate company/ticker).
_IDENTITY_PATTERNS = (
    "gemini", "openai", "chatgpt", "gpt-3", "gpt-4", "gpt-5",
    "i am an ai", "i'm an ai", "as an ai", "as a language model", "large language model",
    "google's model", "trained by google", "developed by google",
)


def _boundary_regex(patterns) -> "re.Pattern":
    """Match any phrase as a whole token, not a substring. `(?<!\\w)…(?!\\w)` stops the short/fragile
    tokens from firing on innocent supersets — the reported class was `as an ai` matching inside
    `as an aid` / `as an aircraft`, flagging a benign answer as an identity leak."""
    alternation = "|".join(re.escape(p) for p in patterns)
    return re.compile(r"(?<!\w)(?:" + alternation + r")(?!\w)")


_ADVICE_RE = _boundary_regex(_ADVICE_PATTERNS)
_IDENTITY_RE = _boundary_regex(_IDENTITY_PATTERNS)


def scan_answer(answer: str) -> List[str]:
    """Return the guardrail issue tags detected in `answer` (empty = clean). Never raises."""
    text = (answer or "").lower()
    issues: List[str] = []
    if _ADVICE_RE.search(text):
        issues.append("advice_directive")
    if _IDENTITY_RE.search(text):
        issues.append("identity_leak")
    return issues
