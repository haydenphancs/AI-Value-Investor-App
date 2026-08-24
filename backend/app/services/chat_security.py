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
  - ``finalize_disclaimer`` (+ the ``ensure_disclaimer`` / ``disclaimer_suffix``
                            wrappers) — apply the "educational, not financial advice"
                            policy in code, not by prompt-hope. GATED on trade-action
                            intent, which the CALLER decides (``chat_intent``): the
                            line is guaranteed on a turn where someone might act on
                            the answer, and a volunteered one is stripped otherwise.
                            A note on every answer, including "Hi", is how you train
                            people to stop reading notes.

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

# Markers that indicate the text already carries an advice disclaimer.
#
# Used in BOTH directions now, which is why the narrowness matters twice over:
#   * suppress-append — on a trade turn, don't double up on a note the model wrote itself;
#   * permit-strip    — on a non-trade turn, only a block bearing this signature may be removed.
#
# DELIBERATELY NARROW: only the negated-advice signature of a real disclaimer. Broad incidental
# phrases ("do your own research", "educational purposes", "consult a qualified financial
# advisor") occur constantly in normal finance prose. Matching them would make the append
# SUPPRESS a required line (a false "already has one") AND make the strip eat real sentences.
# The forms below essentially only appear AS a disclaimer.
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


# ── Trailing-disclaimer strip (non-trade turns) ──────────────────────────────
#
# Removes a SHORT TRAILING BLOCK that is unmistakably boilerplate, and nothing else.
# Every constraint below exists because the failure mode is eating real prose:
#
#   1. TRAILING ONLY. A real answer opened with "As Cay AI, I cannot tell you whether
#      you should buy Apple, as I am not a financial advisor." That is the advice
#      boundary in the model's OWN voice, it is the substance of the answer, and it
#      MUST survive. Only the last line / last sentence is ever a candidate.
#   2. SHORT. <= 320 chars and <= 3 sentences. A paragraph of analysis that happens to
#      contain the phrase is not a disclaimer.
#   3. SIGNATURE. Must carry one of the narrow `_DISCLAIMER_MARKERS` (the same set the
#      append side uses, so the two stay symmetric) or be verbatim what we appended.
#      "do your own research" is deliberately NOT a trigger — see
#      test_disclaimer_not_suppressed_by_incidental_finance_prose for a real sentence
#      that ends that way and must not be eaten.
#   4. NEVER a list item, heading or blockquote — those are content.
#   5. NEVER empties the answer.

_STRIP_MARKERS = _DISCLAIMER_MARKERS + (
    "for educational purposes only",
    "not a recommendation to buy",
    "not a recommendation to invest",
)
# Real boilerplate is short: the configured value is ~105 chars in production and 163 as
# the code default. 200 keeps comfortable headroom while making it much harder to eat a
# genuine long closing sentence that happens to contain a marker. The asymmetry says be
# stingy here — failing to strip shows one extra disclaimer, whereas over-stripping
# deletes content silently.
_MAX_DISCLAIMER_CHARS = 200
_MAX_DISCLAIMER_SENTENCES = 3
_LIST_OR_HEADING_RE = re.compile(r"^\s*(?:[-*+]\s|\d+[.)]\s|#{1,6}\s|>\s)")
_EDGE_DECOR_RE = re.compile(r"^[\s*_>#\-–—•]+|[\s*_\-–—]+$")
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")
_RULE_LINE_RE = re.compile(r"^\s*(?:-{3,}|\*{3,}|_{3,})\s*$")
_WS_RE = re.compile(r"\s+")


def _norm_ws(s: str) -> str:
    return _WS_RE.sub(" ", s).strip().lower()


def _is_disclaimer_block(block: str) -> bool:
    if _LIST_OR_HEADING_RE.match(block):
        return False                       # a bullet is content, never a closing note
    s = _EDGE_DECOR_RE.sub("", block).strip()
    if not s:
        return False
    # Exact match FIRST, before any heuristic bound. This is verbatim what we appended,
    # so whatever length or sentence count the deployed LEGAL_DISCLAIMER happens to have,
    # we must always be able to take it back off again.
    if _norm_ws(s) == _norm_ws(settings.LEGAL_DISCLAIMER):
        return True
    if len(s) > _MAX_DISCLAIMER_CHARS:
        return False
    if not any(m in s.lower() for m in _STRIP_MARKERS):
        return False
    return len(_SENTENCE_SPLIT_RE.split(s)) <= _MAX_DISCLAIMER_SENTENCES


def _drop_trailing_rule(body: str) -> str:
    """Drop the `---` separator / blank lines the note was hanging off."""
    lines = body.split("\n")
    while lines and (not lines[-1].strip() or _RULE_LINE_RE.match(lines[-1])):
        lines.pop()
    return "\n".join(lines)


def _strip_disclaimer_once(text: str) -> Tuple[str, bool]:
    body = text.rstrip()
    if not body:
        return text, False
    nl = body.rfind("\n")
    if nl != -1:
        candidate, head = body[nl + 1:], body[:nl].rstrip()
        # `head` non-empty → never strip the ENTIRE answer down to nothing.
        if head and _is_disclaimer_block(candidate):
            return _drop_trailing_rule(head), True
    # The model glued the note onto the end of the prose instead of giving it a line.
    tail_start = nl + 1 if nl != -1 else 0
    parts = _SENTENCE_SPLIT_RE.split(body[tail_start:])
    if len(parts) >= 2 and _is_disclaimer_block(parts[-1]):
        kept = " ".join(parts[:-1]).rstrip()
        if kept.strip():
            return _drop_trailing_rule(body[:tail_start] + kept), True
    return text, False


def strip_trailing_disclaimer(text: Optional[str]) -> str:
    """Remove a trailing boilerplate disclaimer block. Idempotent; never empties the
    answer. Bounded to 2 passes — a stored row can carry the model's own note AND the
    line an earlier build appended underneath it."""
    out = text or ""
    for _ in range(2):
        out, changed = _strip_disclaimer_once(out)
        if not changed:
            break
    return out


def finalize_disclaimer(text: Optional[str], *, trade_intent: bool) -> Tuple[str, str]:
    """Apply the intent-gated disclaimer policy. Returns ``(final_text, live_suffix)``.

    ``trade_intent`` True  → the line is GUARANTEED: appended unless the answer already
                             carries one of the narrow markers anywhere in it.
    ``trade_intent`` False → nothing is appended, AND a trailing boilerplate note the
                             model volunteered anyway is stripped.

    ``live_suffix`` is '' unless something was appended; the STREAM path emits it as one
    more live token so the visible reveal matches the durable content. ONE function for
    both endpoints so they cannot drift — a prompt instruction and a code append that
    each half-knew about the other is exactly what made the old behaviour unreasonable.

    The caller decides ``trade_intent`` (see ``chat_intent.is_trade_intent``): this
    module stays pure input-security and holds no opinion about WHY a turn qualifies.

    Deliberate trade-off: ``_has_disclaimer`` is whole-text, so on a trade turn where
    the model opens with "…as I am not a financial advisor" nothing is appended — the
    disclaimer is present, just at the top. Scoping the check to the trailing region
    would guarantee a CLOSING line but would double up on the most common compliant
    answer shape. The always-on `InlineDisclaimerNotice` in the UI is the belt to this
    braces.
    """
    base = text or ""
    if not trade_intent:
        return strip_trailing_disclaimer(base), ""
    if _has_disclaimer(base):
        return base, ""
    suffix = "\n\n" + settings.LEGAL_DISCLAIMER
    return base + suffix, suffix


def disclaimer_suffix(text: Optional[str], *, trade_intent: bool) -> str:
    """The disclaimer line to append, or ''. Thin wrapper over `finalize_disclaimer`."""
    return finalize_disclaimer(text, trade_intent=trade_intent)[1]


def ensure_disclaimer(text: Optional[str], *, trade_intent: bool) -> str:
    """Apply the disclaimer policy and return the final text. Idempotent.

    `trade_intent` is keyword-only and REQUIRED — with four call sites, a loud break at
    every one of them beats a default that silently keeps the old always-on behaviour.
    """
    return finalize_disclaimer(text, trade_intent=trade_intent)[0]
