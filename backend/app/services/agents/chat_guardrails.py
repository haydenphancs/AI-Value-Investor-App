"""Output-side guardrails for the chat answer.

TWO layers, deliberately different in aggression:

1. ``scan_answer`` — MONITORING only. Detects likely advice-boundary drift (a personal
   buy/sell/hold DIRECTIVE), identity drift (the model naming its provider), suitability
   claims, and persona drift (speaking AS a real investor, or claiming holdings or trades
   of its own — the report chat's mode voice must never do either). The
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


# Suitability claims (monitor-only). Personalization lets the model address the reader
# directly, and the failure mode that matters is no longer "buy this" but "this suits
# YOU" — the one assertion that turns impersonal analysis into personalized investment
# advice. `ADVICE_BOUNDARY` forbids it; this measures whether the model obeys.
#
# ⚠️ DELIBERATELY NOT ENFORCED (see the redaction section below, which is reserved for
# high-confidence, near-zero-false-positive classes). "whether it's right for you depends
# on circumstances I can't see" is the model COMPLYING correctly, and it trips these
# patterns. Redacting it would corrupt the compliant answers while leaving the
# non-compliant ones — which say "this IS right for you" — largely untouched. Watch the
# rate; escalate only with real traffic to justify a narrower enforcement set.
_SUITABILITY_PATTERNS = (
    "right for you", "right for your", "suitable for you", "suitable for your",
    "a good fit for you", "a great fit for you", "fits your profile", "fits your risk",
    "fits your goals", "matches your profile", "matches your risk", "matches your goals",
    "suits your", "appropriate for you", "appropriate for your",
    "well-suited to you", "well suited to you", "ideal for you", "perfect for you",
    "given your risk tolerance", "based on your risk", "aligns with your goals",
    "given your goals", "given your profile", "for someone like you",
)


# ── Persona drift (monitor-only, 2026-10-02) ──────────────────────────────────
# A report chat now answers in the report's MODE ("Cay AI · Growth Hunter Agent"), a method
# voice that is name-free and third-person by construction (agents/report_voice_prompt.py).
# The two ways it can drift are the two claims that are not true of the product: speaking AS a
# real investor (right of publicity / false endorsement — migration 103's rule) and claiming
# holdings or trades of its own (FTC §5 / SEC AI-washing: an AI with no portfolio saying "I own
# shares"). MONITOR ONLY, never redacted: rewriting a first-person sentence mid-answer would
# corrupt it, and the voice's own trailer is the primary control.
#
# Shapes, each pinned by a false-positive AND a false-negative table in
# tests/test_chat_guardrails.py:
#   * a name is only ever matched in a FIRST-PERSON frame ("I'm …", "my name is …", "speaking
#     as …", "As …, I"), never on a bare mention — "GARP was popularized by Peter Lynch's books"
#     and "Berkshire, led by Warren Buffett, owns Apple" are ordinary third-person prose — and
#     never as a possessive or compound ("I am Peter Lynch's biggest fan", "I'm Lynch-like");
#   * "<full name> here" only when a dash, end punctuation other than a comma, or the end
#     follows ("Michael Burry here —"), so the Book chats' "Peter Lynch here argues…" and
#     "Peter Lynch here, in One Up On Wall Street, argues…" stay clean, and never after
#     "by"/"of"/"from" ("…the method described by Peter Lynch here.");
#   * no bare "wood" (an ordinary noun) and no bare "speaking";
#   * holdings allow one adverb ("I also own", "I personally own") and cover hold/held (not the
#     idioms "hold off", "hold the view"), and skip a hypothetical, a question or a worked
#     example ("if I sold", "should I hold", "Suppose I bought…") and a numbered name ("Phase I
#     sold out", "Vision Fund I sold") — but only when that word sits in the SAME clause.

# Full names, then the distinctive single names. The investors behind the five report methods
# plus the two retired ones (graham/munger rows were deactivated by migration 187) and the
# pre-rename Activist (dalio). No bare "wood"; no bare "graham" (a common first name).
_INVESTOR_FULL_NAMES = (
    r"warren\s+buffett", r"charlie\s+munger", r"benjamin\s+graham", r"peter\s+lynch",
    r"cathie\s+wood", r"bill\s+ackman", r"michael\s+burry", r"ray\s+dalio",
)
_INVESTOR_SINGLE_NAMES = ("buffett", "munger", "lynch", "cathie", "ackman", "burry", "dalio")
_INVESTOR_FULL = "(?:" + "|".join(_INVESTOR_FULL_NAMES) + ")"
_INVESTOR_ANY = "(?:" + "|".join(_INVESTOR_FULL_NAMES + _INVESTOR_SINGLE_NAMES) + ")"
_IMPERSONATION_RE = re.compile(
    # "I am Peter Lynch's biggest fan" / "I'm Lynch-like" are third-person: never a name
    # followed by a possessive or a hyphenated compound.
    r"\bi(?:'m|\s+am)\s+(?:mr\.?\s+|ms\.?\s+|mrs\.?\s+)?" + _INVESTOR_ANY + r"\b(?!'s|-)"
    r"|\bmy\s+name\s+is\s+" + _INVESTOR_ANY + r"\b"
    r"|\b(?:speaking|writing|answering|talking)\s+as\s+" + _INVESTOR_ANY + r"\b"
    # `(?:\s*,\s*|\s+)`, not `\s*,?\s+`: a whitespace run has ONE way to match, so a long
    # run after the name cannot backtrack quadratically (scan_answer runs on the event loop).
    r"|(?<!\bsuch\s)\bas\s+" + _INVESTOR_ANY + r"(?:\s*,\s*|\s+)(?:i|i'd|i'm|i've|i'll|my)\b"
    # No comma in the follow set: "Peter Lynch here, in One Up On Wall Street, argues" is a
    # Book chat's ordinary prose.
    r"|(?<!\bby\s)(?<!\bof\s)(?<!\bfrom\s)\b" + _INVESTOR_FULL
    + r"\s+here(?=\s*(?:[-\u2013\u2014.:;!?\u2026]|$))"
)
# "I hold off on a verdict" / "I still hold the view that\u2026" are idioms, not a holding.
_HOLD_IDIOM_GUARD = (
    r"(?!\s+(?:off|back)\b)"
    r"(?!\s+(?:the|that|this|a|an|my|our)\s+(?:view|views|opinion|belief|line)\b)"
)
_HOLDINGS_RE = re.compile(
    r"\bmy\s+(?:own\s+)?(?:portfolio|holdings|fund|track\s+record)\b"
    r"|\bmy\s+(?:position|stake|shares)\s+in\b"
    r"|\bi(?:'ve|\s+have)?(?:\s+(?:also|personally|recently|just|already|still))?"
    r"\s+(?:own|owned|bought|sold|(?:hold|held)\b" + _HOLD_IDIOM_GUARD + r")"
    # "I hold no positions" / "I own none" is the model COMPLYING, not a claim.
    r"\b(?!\s+(?:no|none|nothing|zero)\b)"
)
# The word right before a first-person verb that makes it hypothetical, a question, a worked
# example or a numbered name rather than a claim ("if I sold", "should I hold", "Suppose I
# bought 100 shares", "Phase I sold out", "Vision Fund I sold its stake").
_HOLDINGS_SKIP_BEFORE = frozenset({
    "if", "whether", "unless", "should", "do", "does", "did", "can", "could", "would",
    "will", "shall", "may", "might", "must",
    "suppose", "imagine", "say", "assume", "pretend", "hypothetically",
    "phase", "class", "tier", "series", "part", "stage", "grade", "level", "chapter",
    "section", "title", "schedule", "war",
    "fund", "model", "act", "volume", "book", "gen", "unit", "article", "type",
})
# The previous word counts only inside the same clause: a sentence end or a semicolon between
# it and "I" ("\u2026what to do. I bought more.") means the verb is a fresh claim.
_PREV_WORD_RE = re.compile(r"([a-z]+)[^\w.!?;]*$")


def _persona_text(text: str) -> str:
    """Lower-cased text with curly apostrophes straightened — for the persona patterns ONLY,
    so every pre-existing tag stays byte-identical in what it matches."""
    return text.replace("\u2019", "'").replace("\u2018", "'")


def _has_first_person_holdings(text: str) -> bool:
    for m in _HOLDINGS_RE.finditer(text):
        if m.group(0).startswith("my"):
            return True
        prev = _PREV_WORD_RE.search(text[max(0, m.start() - 24):m.start()])
        if prev is None or prev.group(1) not in _HOLDINGS_SKIP_BEFORE:
            return True
    return False


def _boundary_regex(patterns) -> "re.Pattern":
    """Match any phrase as a whole token, not a substring. `(?<!\\w)…(?!\\w)` stops the short/fragile
    tokens from firing on innocent supersets — the reported class was `as an ai` matching inside
    `as an aid` / `as an aircraft`, flagging a benign answer as an identity leak."""
    alternation = "|".join(re.escape(p) for p in patterns)
    return re.compile(r"(?<!\w)(?:" + alternation + r")(?!\w)")


_ADVICE_RE = _boundary_regex(_ADVICE_PATTERNS)
_IDENTITY_RE = _boundary_regex(_IDENTITY_PATTERNS)
_SUITABILITY_RE = _boundary_regex(_SUITABILITY_PATTERNS)


def scan_answer(answer: str) -> List[str]:
    """Return the guardrail issue tags detected in `answer` (empty = clean). Never raises.
    MONITOR-only — the caller logs these; it does not alter the answer here."""
    text = (answer or "").lower()
    issues: List[str] = []
    if _ADVICE_RE.search(text):
        issues.append("advice_directive")
    if _IDENTITY_RE.search(text):
        issues.append("identity_leak")
    if _SUITABILITY_RE.search(text):
        issues.append("suitability_claim")
    persona_text = _persona_text(text)
    if _IMPERSONATION_RE.search(persona_text):
        issues.append("persona_impersonation")
    if _has_first_person_holdings(persona_text):
        issues.append("first_person_holdings")
    return issues


# ── Enforcement (redaction) — high-confidence, near-zero-false-positive classes ──

# Secret / API-key shapes. Case-sensitive: real key prefixes are case-specific. Each is
# anchored with `(?<![A-Za-z0-9])` so the prefix can't match MID-WORD (e.g. "sk-" inside
# "risk-averse-..."), and the sk-/sbp- bodies exclude hyphens so a long hyphenated finance
# compound ("basket-of-stocks-strategy") can't masquerade as a key.
_SECRET_PATTERNS = (
    r"(?<![A-Za-z0-9])AIza[0-9A-Za-z_\-]{20,}",                       # Google API key
    r"(?<![A-Za-z0-9])sk-(?:proj-)?[A-Za-z0-9]{20,}",                 # OpenAI-style secret key
    r"(?<![A-Za-z0-9])sbp_[A-Za-z0-9]{20,}",                          # Supabase access token
    r"(?<![A-Za-z0-9])eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{6,}",  # JWT
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

# SELF-REFERENTIAL identity leaks only — every pattern is FIRST-PERSON-anchored so the
# model describing ITS OWN nature/provider is redacted, while legitimate finance prose about
# the AI SECTOR is preserved. Critical: bare "as an AI <noun>" ("NVIDIA, as an AI chip maker")
# and "as a language model <verb>" ("as a language model grows in parameters") are NOT here —
# they are everyday phrasing in an AI-investing product; only the self-referential ", I ..."
# forms are. Likewise "created by Google" (a legit product statement) is NOT redacted; only
# "I was created by Google" is. Validated against a leak/legit corpus.
_IDENTITY_ENFORCE_PATTERNS = (
    r"\bi(?:'m| am) an? ai\b",                                  # "I am an AI"
    # LOOKAHEAD (?=,? i) so ", I" is NOT consumed — else a following "…was trained by Google"
    # would be orphaned and leak. Requires the self-ref ", I" so "as an AI chip maker" is safe.
    r"\bas an ai(?=,? i\b)",                                    # "As an AI, I …"
    r"\bi(?:'m| am) a (?:large )?language model\b",             # "I'm a large language model"
    r"\bas a language model(?=,? i\b)",                         # "As a language model, I …"
    # "trained/fine-tuned by <provider>" needs NO first-person: a COMPANY is not "trained", a
    # model is — so this is a self-reveal even after an earlier match consumed the ", I".
    r"\b(?:trained|fine-?tuned)(?:\s+\w+){0,3}\s+by (?:google|openai|gemini)\b",
    # "created/built/made/…by <provider>" IS ambiguous (a product can be "made by Google"), so
    # require first-person here to avoid redacting a legit "created by Google DeepMind".
    r"\bi(?:'m| am| was)?\s+(?:developed|built|created|made|powered)"
    r"(?:\s+\w+){0,3}\s+by (?:google|openai|gemini)\b",
    r"\bi(?:'m| am) (?:gemini|chatgpt|gpt-?[345])\b",           # "I am Gemini / ChatGPT / GPT-4"
    r"\bi(?:'m| am) a model (?:trained|made|built|created|developed|powered)\b",
    r"\bthe (?:language |ai )?model behind me\b",
    r"\bmy underlying (?:language |ai )?model\b",
)
_IDENTITY_ENFORCE_RE = re.compile("|".join(_IDENTITY_ENFORCE_PATTERNS), re.IGNORECASE)


# Markdown links `[label](target)` and image embeds `![alt](target)`. The label is kept, the
# target is dropped: a link in a Cay AI bubble is model output — steered by a headline, a
# tool result, a cached brief or the user's own earlier turn — and iOS rendered it as a
# tappable blue link that left the app for ANY scheme (`https://phishing`, `tel:+1900…`,
# `sms:…&body=…`). Cay AI cites through the `sources` pills, never inline. The iOS renderer
# strips link attributes too; this is the server belt for persisted rows and older builds.
_MD_LINK_RE = re.compile(r"!?\[([^\]\n]{0,200})\]\(\s*<?[^)\s]*>?(?:\s+\"[^\"]*\")?\s*\)")
# Autolinks `<https://…>` and bare non-web schemes that a client could linkify.
_AUTOLINK_RE = re.compile(r"<(?:https?|mailto|tel|sms|facetime|itms[a-z-]*):[^>\s]+>", re.IGNORECASE)
_BARE_SCHEME_RE = re.compile(r"\b(?:tel|sms|facetime|mailto|itms[a-z-]*):[^\s)\]]+", re.IGNORECASE)


def strip_links(text: str) -> Tuple[str, int]:
    """`[label](target)` → `label`; `![alt](img)` → `alt`; autolinks / tel: / sms: removed."""
    out, n1 = _MD_LINK_RE.subn(lambda m: m.group(1), text)
    out, n2 = _AUTOLINK_RE.subn("", out)
    out, n3 = _BARE_SCHEME_RE.subn("", out)
    return out, n1 + n2 + n3


def enforce_answer(answer: str) -> Tuple[str, List[str]]:
    """Redact high-confidence leaks from `answer` and return `(redacted, tags)`.

    Redacts (never blocks the whole answer):
      - secrets / API keys / JWTs → ``***``
      - internal DB schema identifiers + secret names → ``***``
      - self-referential model-identity phrases → ``Cay AI``
      - markdown links / image embeds → their label; autolinks and tel:/sms: targets dropped

    Advice-boundary phrasing is intentionally NOT redacted (see ``scan_answer``).
    Never raises.
    """
    text = answer or ""
    tags: List[str] = []

    text, n_links = strip_links(text)
    if n_links:
        tags.append("link_stripped")

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
