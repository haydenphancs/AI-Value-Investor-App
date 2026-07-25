"""Output-side guardrails for the chat answer.

TWO layers, deliberately different in aggression:

1. ``scan_answer`` — MONITORING only. Detects likely advice-boundary drift (a personal
   buy/sell/hold DIRECTIVE) and identity drift (the model naming its provider). The
   endpoint LOGS these WITHOUT altering the answer, because a false positive silently
   dropping a good answer is worse than a logged flag a human can review. This is the
   safety net that makes drift observable.

2. ``enforce_answer`` — REDACTION (targeted enforcement). Redacts only HIGH-confidence,
   near-zero-false-positive leaks: secrets/API keys, internal DB schema identifiers, and
   SELF-REFERENTIAL model-identity phrases ("as an AI", "trained by Google", "I am
   Gemini"). It never redacts a bare product/company name (a user may legitimately ask
   about "OpenAI" or "Gemini" the exchange), and it never touches advice phrasing.
   Returns the possibly-redacted answer + the tags it fired, so the endpoint can log.

The primary defense stays the fenced prompt + the system identity rule; these guardrails
are the observable + enforced backstop. Both functions never raise.
"""

import re
from typing import List, Tuple

# ── Advice directives (monitor-only) ─────────────────────────────────────────
# Personal directives (imperative "you should buy/sell/hold" style). Kept targeted —
# explaining tradeoffs ("some investors consider…") is fine and must NOT trip these.
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
# MONITOR list — broad on purpose so bare product mentions are visible in logs. Enforcement
# (redaction) uses the narrower self-referential set below, so a legit answer about "OpenAI"
# the company is logged but NOT corrupted.
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
    """Return the guardrail issue tags detected in `answer` (empty = clean). Never raises.
    MONITOR-only — the caller logs these; it does not alter the answer here."""
    text = (answer or "").lower()
    issues: List[str] = []
    if _ADVICE_RE.search(text):
        issues.append("advice_directive")
    if _IDENTITY_RE.search(text):
        issues.append("identity_leak")
    return issues


# ── Enforcement (redaction) — high-confidence, near-zero-false-positive classes ──

# Secret / API-key shapes. Case-sensitive: real key prefixes are case-specific, and this
# avoids redacting ordinary prose. These essentially never appear in a finance answer.
_SECRET_PATTERNS = (
    r"AIza[0-9A-Za-z_\-]{20,}",                                   # Google API key
    r"sk-[A-Za-z0-9_\-]{20,}",                                    # OpenAI-style secret key
    r"sbp_[A-Za-z0-9]{20,}",                                      # Supabase access token
    r"eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{6,}",  # JWT (header.payload.sig)
)
_SECRET_RE = re.compile("|".join(_SECRET_PATTERNS))

# Internal DB schema identifiers + secret NAMES. Redacting these can't corrupt a genuine
# investing answer (no user asks about "chat_usage_budget"). Case-insensitive.
_SCHEMA_PATTERNS = (
    r"chat_messages", r"chat_sessions", r"chat_usage_budget",
    r"search_filing_chunks", r"search_all_chunks",
    r"ai_insight_budget", r"updates_insight_state",
    r"service_role", r"row_security", r"auth\.uid",
    r"SUPABASE_[A-Z_]*KEY", r"SUPABASE_JWT_SECRET", r"SERVICE_ROLE_KEY",
)
_SCHEMA_RE = re.compile("|".join(_SCHEMA_PATTERNS), re.IGNORECASE)

# SELF-REFERENTIAL identity leaks only (the model describing ITS OWN nature/provider).
# A bare "OpenAI"/"Gemini" is NOT here — a user may legitimately ask about those. These
# phrasings essentially only occur when the assistant reveals itself, so redacting them
# to "Cay AI" is safe.
_IDENTITY_ENFORCE_PATTERNS = (
    r"as an? ai\b",
    r"i(?:'m| am) an? ai\b",
    r"as a language model",
    r"i(?:'m| am) a (?:large )?language model",
    r"(?:trained|developed|built|created|powered|made) by (?:google|openai|gemini)",
    r"(?:google|openai)'s (?:model|language model)",
    r"i(?:'m| am) (?:gemini|chatgpt|gpt-?[345])\b",
    r"the (?:language )?model behind me",
    r"my underlying (?:language )?model",
)
_IDENTITY_ENFORCE_RE = re.compile("|".join(_IDENTITY_ENFORCE_PATTERNS), re.IGNORECASE)


def enforce_answer(answer: str) -> Tuple[str, List[str]]:
    """Redact high-confidence leaks from `answer` and return `(redacted, tags)`.

    Redacts (never blocks the whole answer):
      - secrets / API keys / JWTs → ``***``
      - internal DB schema identifiers + secret names → ``***``
      - self-referential model-identity phrases → ``Cay AI``

    Advice-boundary phrasing is intentionally NOT redacted (see ``scan_answer``).
    Never raises.
    """
    text = answer or ""
    tags: List[str] = []

    text, n_secret = _SECRET_RE.subn("***", text)
    if n_secret:
        tags.append("secret_redacted")

    text, n_schema = _SCHEMA_RE.subn("***", text)
    if n_schema:
        tags.append("schema_redacted")

    text, n_ident = _IDENTITY_ENFORCE_RE.subn("Cay AI", text)
    if n_ident:
        tags.append("identity_redacted")

    return text, tags
