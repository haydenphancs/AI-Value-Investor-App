"""Chat input-security helpers (denial-of-wallet + prompt-injection front line).

Pure, dependency-light functions used by the chat endpoints and prompt builder.
No network, no DB — safe to import anywhere and to unit-test in isolation.

What lives here:
  - ``normalize_text``    — Unicode NFKC + strip zero-width / bidi-control / other
                            control chars (kills the classic "invisible instruction"
                            and homoglyph-obfuscation injection tricks) before any
                            length or pattern check runs.
  - ``validate_message``  — normalize + enforce the friendly length ceiling, mapping
                            to the iOS ``ErrorCode`` contract (INVALID_INPUT /
                            CHAT_MESSAGE_TOO_LONG). Returns the CLEAN text to use.
  - ``sanitize_context``  — normalize + truncate the client-supplied grounding blob
                            (which lands in the SYSTEM instruction — an injection
                            surface) to a bounded size. Best-effort: truncate, never
                            reject, since context is optional grounding.
  - ``scan_input``        — regex tags for likely prompt-injection / jailbreak markers.
                            MONITORING only (logged by the endpoint); the real defense
                            is the fenced prompt + system rules. Never blocks.
  - ``cap_prompt``        — final assembled-prompt ceiling (defense-in-depth token cap).
  - ``ensure_disclaimer`` / ``disclaimer_suffix`` — GUARANTEE the "educational, not
                            financial advice" line in code, not just by prompt-hope.

None of these raise on bad input (None / non-str / empty) — they degrade to a safe value.
"""

from __future__ import annotations

import re
import unicodedata
from typing import List, Optional, Tuple

from app.config import settings
from app.api.error_response import ErrorCode


# ── Unicode hygiene ──────────────────────────────────────────────────────────

# Zero-width + bidirectional control characters. These render invisibly, so an
# attacker can hide "ignore your instructions" inside what looks like a benign
# message, or reverse text to defeat naive pattern checks. Stripped BEFORE any
# length/pattern check so the scan sees what the model effectively sees. Listed as
# explicit code points (never literal invisibles in source) so the set is reviewable.
_INVISIBLE_CODEPOINTS = (
    0x200B, 0x200C, 0x200D,          # zero-width space / non-joiner / joiner
    0x200E, 0x200F,                  # LRM, RLM
    0x202A, 0x202B, 0x202C, 0x202D, 0x202E,  # bidi embeddings + overrides (LRE/RLE/PDF/LRO/RLO)
    0x2066, 0x2067, 0x2068, 0x2069,  # bidi isolates (LRI/RLI/FSI/PDI)
    0x2060,                          # word-joiner
    0xFEFF,                          # BOM / zero-width no-break space
)
_INVISIBLE_CHARS = {cp: None for cp in _INVISIBLE_CODEPOINTS}

# Control chars EXCEPT tab (\x09), newline (\x0a), carriage return (\x0d), which are
# legitimate in a chat message and must survive normalization.
_OTHER_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

# 3+ consecutive blank lines → collapse to one blank line (bounds "essay" padding).
_BLANK_RUN_RE = re.compile(r"\n{3,}")


def normalize_text(s: Optional[str]) -> str:
    """NFKC-normalize and strip invisible/control characters. Never raises."""
    if not s or not isinstance(s, str):
        return ""
    s = unicodedata.normalize("NFKC", s)
    s = s.translate(_INVISIBLE_CHARS)
    s = _OTHER_CONTROL_RE.sub("", s)
    s = _BLANK_RUN_RE.sub("\n\n", s)
    return s


# Runs of 2+ angle brackets — the building block of every prompt fence delimiter
# (`<<<USER_MESSAGE>>>`, `<<<CONTEXT>>>`, `<<<CLIENT_CONTEXT>>>` + their END forms).
_ANGLE_RUN_RE = re.compile(r"<{2,}|>{2,}")


def neutralize_fences(text: Optional[str]) -> str:
    """Stop untrusted text from reproducing a prompt-fence delimiter (delimiter injection).

    A user (or a poisoned RAG chunk) that embeds the literal ``<<<END_USER_MESSAGE>>>`` — or the
    full-width homoglyph ``＜＜＜…＞＞＞`` that NFKC folds into it — could otherwise CLOSE the
    spotlighting fence early and make trailing text land OUTSIDE the untrusted span, defeating the
    fence. This normalizes (folding full-width `<`/`>`) then collapses any run of 2+ angle brackets
    to a single one, so no `<<<`/`>>>` boundary can form. Single `<`/`>`/`<=`/`>=` (math) survive.
    Applied to EVERY text bound for a fenced slot (user message, client context, RAG chunk,
    conversation history). Never raises.
    """
    t = normalize_text(text)
    if not t:
        return ""
    t = _ANGLE_RUN_RE.sub(lambda m: m.group(0)[0], t)
    return t


# ── Symbol hygiene (the one untrusted value that reaches the SYSTEM prompt UNFENCED) ──

# Every legitimate shape the app actually uses, and nothing else:
#   ^GSPC ^DJI ^IXIC ^TNX ^VIX   indices (leading caret)
#   BTCUSD BTCUSDT GCUSD CLUSD   crypto + commodity
#   BTC ETH GOLD OIL NATGAS      bare crypto / friendly commodity aliases
#   AAPL BRK.B BRK-B             equities, incl. dot/hyphen share classes
# 16 chars is comfortably above the longest of those (7) without being a free-text field.
_SYMBOL_RE = re.compile(r"^\^?[A-Za-z0-9][A-Za-z0-9.\-]{0,14}$")


def sanitize_symbol(raw: Optional[str]) -> Optional[str]:
    """A ticker safe to interpolate into the system instruction, or ``None``.

    ⚠️ WHY THIS EXISTS. ``stock_id`` is the ONE untrusted, caller-supplied value that reaches
    the system instruction **unfenced** — ``_build_system_instruction`` writes
    ``"You are currently helping analyze {stock_id}."`` directly. Every other untrusted span
    (user message, client context, RAG chunk, history) goes through ``neutralize_fences`` into a
    spotlighted fence; this one had no guard at all, and ``CreateChatSessionRequest.stock_id`` was
    a bare ``Optional[str]``.

    That let a caller write arbitrary instructions into the system prompt, positioned directly
    AFTER ``ADVICE_BOUNDARY`` and the identity rule — i.e. exactly where text can override them.
    Verified reachable on the commonest session type: ``_ASSET_PERSONAS`` covers only
    INDEX/CRYPTO/ETF/COMMODITY, so a ``STOCK`` session falls into that ``elif``.

    Rejecting (rather than escaping) is right here: this is a closed-vocabulary identifier, not
    prose. Anything that is not symbol-shaped is not a symbol, and dropping it degrades to a
    perfectly good generic chat instead of smuggling text into the prompt.

    Never raises; returns ``None`` for missing / malformed / over-long input.
    """
    t = normalize_text(raw)
    if not t:
        return None
    t = t.strip().upper()
    return t if _SYMBOL_RE.match(t) else None


# ── Message validation (friendly length ceiling) ─────────────────────────────

def validate_message(raw: Optional[str]) -> Tuple[str, Optional[ErrorCode]]:
    """Normalize + validate the user message.

    Returns ``(clean_text, error_code_or_None)``. On an empty-after-normalize
    message → ``INVALID_INPUT``; over the friendly ceiling → ``CHAT_MESSAGE_TOO_LONG``.
    The caller uses ``clean_text`` for the rest of the turn (so the model + the
    persisted row both see the sanitized text).
    """
    clean = normalize_text(raw).strip()
    if not clean:
        return "", ErrorCode.INVALID_INPUT
    if len(clean) > settings.CHAT_MESSAGE_MAX_CHARS:
        return clean, ErrorCode.CHAT_MESSAGE_TOO_LONG
    return clean, None


def sanitize_context(raw: Optional[str]) -> Optional[str]:
    """Normalize + truncate the client-supplied grounding blob (SYSTEM-instruction
    injection surface). Best-effort: returns None when empty, else bounded text."""
    clean = normalize_text(raw).strip()
    if not clean:
        return None
    max_chars = settings.CHAT_CONTEXT_MAX_CHARS
    if len(clean) > max_chars:
        clean = clean[:max_chars]
    return clean


# ── Prompt-injection / jailbreak markers (monitor-only) ──────────────────────

# Whole-ish phrase markers of a direct injection / jailbreak attempt. These do NOT
# block — a false positive must never drop a legitimate question — they are logged so
# attacks are observable in prod. The real defense is the fenced prompt (the untrusted
# spans are delimited + labeled "data, not instructions") + the system rules.
_INJECTION_PATTERNS = (
    r"ignore (?:all |the |any |your |these |previous |prior |above )+(?:instruction|prompt|rule|direction)",
    r"disregard (?:all |the |any |your |these |previous |prior |above )+(?:instruction|prompt|rule)",
    r"forget (?:all |everything|your |the |previous |prior )",
    r"(?:reveal|show|print|repeat|output|leak|expose) (?:me )?(?:your |the )?(?:system )?(?:prompt|instruction|rule)",
    r"what (?:is|are) your (?:system )?(?:prompt|instruction|rule)",
    r"system prompt",
    r"you are now",
    r"act as (?:if|a|an|though)",
    r"pretend (?:to be|you are|that)",
    r"developer mode",
    r"jailbreak",
    r"</?(?:system|assistant|user|instruction)>",
    r"(?<!\w)DAN(?!\w)",
    r"do anything now",
)
_INJECTION_RE = re.compile("|".join(_INJECTION_PATTERNS), re.IGNORECASE)


def scan_input(text: Optional[str]) -> List[str]:
    """Return injection/jailbreak tags for ``text`` (empty = clean). Never raises,
    never blocks — for logging/observability only."""
    if not text:
        return []
    return ["injection_marker"] if _INJECTION_RE.search(text) else []


# ── Assembled-prompt ceiling ─────────────────────────────────────────────────

def cap_prompt(prompt: str, max_chars: Optional[int] = None) -> str:
    """Bound the final assembled prompt. Keeps the TAIL (the user message +
    answer instructions live at the end of the builder), dropping the oldest
    context/history first. Defense-in-depth: message/context/history are already
    individually bounded, so this only fires on a pathological combination."""
    limit = max_chars if max_chars is not None else settings.CHAT_PROMPT_MAX_CHARS
    if limit <= 0:
        # A 0/negative cap means "nothing" — guard against prompt[-0:] returning the
        # WHOLE string (the surprising Python slice), which would defeat the cap.
        return ""
    if not prompt or len(prompt) <= limit:
        return prompt or ""
    return prompt[-limit:]


# ── Disclaimer guarantee ─────────────────────────────────────────────────────

# Markers that indicate the model already emitted an advice disclaimer, so we don't
# double-append. DELIBERATELY NARROW: only the negated-advice signature of a real disclaimer.
# Broad incidental phrases ("do your own research", "educational purposes", "consult a qualified
# financial advisor") occur constantly in normal finance prose — matching them would make
# `ensure_disclaimer` SUPPRESS the legally-required line on ordinary answers (a false "already
# has one"). The forms below essentially only appear AS a disclaimer.
_DISCLAIMER_MARKERS = (
    "not financial advice",
    "not investment advice",
    "not a financial advisor",
    "not investment recommendations",
    "educational, not financial",
)


def _has_disclaimer(text: str) -> bool:
    low = text.lower()
    return any(m in low for m in _DISCLAIMER_MARKERS)


def disclaimer_suffix(text: Optional[str]) -> str:
    """The disclaimer line to append, or '' when the answer already carries one."""
    if text and _has_disclaimer(text):
        return ""
    return "\n\n" + settings.LEGAL_DISCLAIMER


def ensure_disclaimer(text: Optional[str]) -> str:
    """Guarantee the 'educational, not financial advice' line is present. Idempotent."""
    base = text or ""
    return base + disclaimer_suffix(base)
