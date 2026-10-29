"""Telegram review of the Trillion Club stake finder (plan 2026-10-28 v3, "Telegram" and "Write orders, taps and
reconcile"): the notes, proposals and digest the finder sends, and the owner's `z:` button taps.

It rides the marketing REVIEW BOT — the same bot, the same private chat, the same webhook
(`marketing.review_service.handle_update` hands every `z:` callback here AFTER its owner allow-list check and
before its own parser; `y` is Learn's letter, `z` this module's, and neither is a marketing verb) and the same
ONE pacer and flood back-off (`marketing.telegram_pacing`).

SENDING — `TelegramStakeNotifier` implements `stake_contracts.StakeNotifier`. `stake_finder` calls it from its
daily run, never from a request:
  * the AUTO note ("going live automatically": ↩️ Withdraw + 👍 Keep) and the PROPOSAL (✅ Publish + 🗑 Dismiss,
    "Trial — would auto-publish" on an 8-K closing) ring; so does a LIFECYCLE note (an exit unpublished, a
    negative re-check; ↩️ Withdraw when offered). The digest is SILENT (Quiet Telegram, rules/marketing.md §2)
    and split under Telegram's 4,096 UTF-16 units by `review_service.split_message`.
  * every send follows Learn's `alerts.notify`: the chat's flood back-off in force → nothing is sent; else
    `PACER.wait()` → the Bot API → a 429 is recorded with `note_rate_limit`, so every other sender stops too.
  * nothing raises (except CancelledError): a send that failed returns None / False and is logged with the
    finding's slug, accession, CIKs, id and reason — never the row's text.
  * plain text, no `parse_mode`: every SEC-sourced value is folded onto one line, stripped of control and
    format characters and capped, so a company name can never fake a line of the note. A message is at most
    4,096 units: evidence lines are dropped first, never the facts.
  * a message the finder marks later (`mark_message` — an expired proposal) gets its buttons replaced and the
    verdict as a silent reply threaded under it: the Bot API cannot read a message back and the finder keeps
    no copy of its text, so the original is never rewritten blind.

THE TAPS — `handle_stake_callback`, data `z:<verb>:<finding uuid>` (40 bytes; `stake_contracts` owns the
grammar): w Withdraw · k Keep · p Publish · d Dismiss. The decision itself — every fence, replay and race — is
`stake_finder.apply_owner_decision`, imported LAZILY (the marketing bot loads no finder code until a stake tap
arrives). This module runs it and shows its answer:
  * the decision runs in its own task, SHIELDED and waited for at most TAP_TIMEOUT_SECONDS — well inside the
    webhook's 45 s cap — so neither that cap nor a dropped connection can cut a write sequence in half. Past
    the timeout the owner is told "Still working — check again in a minute"; the task carries on, its end is
    logged, and a verdict it returns late still edits the tapped message.
  * every tap is answered (answerCallbackQuery) EXACTLY once, with the result's toast; when the result carries
    a verdict the tapped message gets it appended and its buttons replaced (`TapResult.buttons`, same finding).
    A crash is answered once too ("Something went wrong — logged").
  * the tap path never waits on the PACER (the owner is looking at a spinner), and nothing here reads
    TRILLION_CLUB_STAKE_FINDER_ENABLED: a Withdraw must still work after the finder is switched off.
"""

from __future__ import annotations

import asyncio
import functools
import logging
import math
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Awaitable, Callable, Dict, List, Mapping, Optional, Sequence, Set, Tuple

from app.integrations import telegram
from app.integrations.telegram import (
    MAX_MESSAGE_CHARS,
    TelegramException,
    TelegramRateLimitException,
    TelegramRequestError,
)
from app.services.marketing import review_service, telegram_pacing
from app.services.trillion_club.stake_contracts import (
    CALLBACK_VERBS,
    PROPOSAL_TTL_DAYS,
    REASONS,
    TIER_UPDATE,
    VERB_DISMISS,
    VERB_KEEP,
    VERB_PUBLISH,
    VERB_WITHDRAW,
    TapResult,
    callback_data,
    parse_callback_data,
)
from app.utils.market_hours import ET

logger = logging.getLogger(__name__)

# ── limits and wording ────────────────────────────────────────────────────────────────────────

#: How long a tap waits for its decision before answering "Still working". The webhook's own cap is 45 s
#: (`app/main.py` `_TELEGRAM_HANDLER_TIMEOUT_SECONDS`), and Telegram refuses an answer to a callback query
#: that is too old (about 15 s), so the "Still working" toast must land well before that. The decision is
#: shielded, so a short wait cuts nothing: its late end is shown on the message (a verdict) or as a silent
#: reply threaded under it (a toast-only result or a failure).
TAP_TIMEOUT_SECONDS = 10.0
#: One evidence line, whole (plan v3: each ≤ 400 characters).
EVIDENCE_LINE_MAX = 400
#: Evidence lines considered at all — only a handful ever fit beside the facts.
MAX_EVIDENCE_LINES = 50
#: One digest line (a heads-up with its filing link is far shorter).
DIGEST_LINE_MAX = 1000
#: A BACKSTOP on one digest. The finder caps its lines ("+N more", logged) far below this; this exists so a bug
#: there cannot flood the owner's chat. Past it the last part says how many lines were not sent (ERROR logged).
MAX_DIGEST_PARTS = 8
#: The finder's plain-text body of a lifecycle note; a verdict line on an edited message.
LIFECYCLE_TEXT_MAX = 1500
VERDICT_MAX = 500

TOAST_UNKNOWN = "Unknown button"
TOAST_CRASH = "Something went wrong — logged"
TOAST_STILL_WORKING = "Still working — check again in a minute"
TRIAL_LINE = "🧪 Trial — would auto-publish once the 8-K closing tier is enabled"
DIGEST_HEADER = "🗂 Trillion Club stake finder — digest"

BUTTON_LABELS: Dict[str, str] = {
    VERB_WITHDRAW: "↩️ Withdraw", VERB_KEEP: "👍 Keep", VERB_PUBLISH: "✅ Publish", VERB_DISMISS: "🗑 Dismiss",
}
#: `trillion_club_stakes.kind` → what the owner reads (`schemas.trillion_club.STAKE_KINDS`).
KIND_LABELS: Dict[str, str] = {
    "on_13f_note": "a note on its 13F card",
    "us_listed_off_13f": "U.S.-listed, not on its 13F",
    "private": "private (no U.S. listing in SEC data)",
    "commitment": "a commitment (signed, not closed)",
    "non_us_listed": "listed outside the U.S.",
}
#: `value_basis` → words (`schemas.trillion_club.VALUE_BASES`).
VALUE_BASIS_LABELS: Dict[str, str] = {
    "invested": "invested", "committed_up_to": "committed, up to", "carrying_value": "carrying value",
    "fair_value": "fair value",
}

_TAG = "[tc-stakes-review]"
_KEYBOARD_REMOVED: Dict[str, Any] = {"inline_keyboard": []}
#: Characters that never reach a note: controls (newlines included), format characters (a zero-width space,
#: a right-to-left override that would make a name read backwards), lone surrogates, line / paragraph seps.
_UNSAFE_CATEGORIES = frozenset({"Cc", "Cf", "Cs", "Zl", "Zp"})
_NOT_MODIFIED = "message is not modified"

ApplyFn = Callable[..., Awaitable[Any]]


# ── small pure helpers ────────────────────────────────────────────────────────────────────────


def _capped(text: str, cap: int, *, cut: bool) -> str:
    """`text` within `cap` characters, with an ellipsis whenever anything was left out (`cut`: the input was
    already sliced before cleaning)."""
    if cap <= 1:
        return text[: max(cap, 0)]
    if len(text) > cap or (cut and len(text) == cap):
        return text[: cap - 1] + "…"
    return text + "…" if cut else text


def _one_line(value: Any, cap: int) -> str:
    """`value` as ONE plain line: unsafe characters become spaces, whitespace runs collapse, capped at `cap`
    characters with an ellipsis. Never raises."""
    try:
        text = value if isinstance(value, str) else str(value)
    except Exception:  # noqa: BLE001 — a hostile __str__ must not break a note
        text = f"<unprintable {type(value).__name__}>"
    limit = max(cap, 1) * 4                 # bound the per-character pass below
    cut = len(text) > limit
    text = "".join(" " if unicodedata.category(ch) in _UNSAFE_CATEGORIES else ch for ch in text[:limit])
    return _capped(" ".join(text.split()), cap, cut=cut)


def _block(value: Any, cap: int, *, max_lines: int = 20) -> str:
    """Like `_one_line` but keeps the newlines of a code-authored body (each line cleaned), capped overall."""
    try:
        raw = value if isinstance(value, str) else ("" if value is None else str(value))
    except Exception:  # noqa: BLE001
        raw = f"<unprintable {type(value).__name__}>"
    limit = max(cap, 1) * 4
    parts = raw[:limit].split("\n")
    cut = len(raw) > limit or len(parts) > max_lines
    text = "\n".join(_one_line(part, cap) for part in parts[:max_lines]).strip("\n")
    return _capped(text, cap, cut=cut)


def _text(row: Mapping[str, Any], key: str, cap: int) -> Optional[str]:
    """A non-empty string column, cleaned; None for anything else."""
    value = row.get(key)
    if not isinstance(value, str) or not value.strip():
        return None
    cleaned = _one_line(value, cap)
    return cleaned or None


def _finite(value: Any) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        out = float(value)
    except (OverflowError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _trim_zeros(text: str) -> str:
    return text.rstrip("0").rstrip(".") if "." in text else text


def pct_text(value: Any) -> Optional[str]:
    """`9.9` → "9.9%", `10.0` → "10%", `0.004` → "0.004%"; None for a missing or unusable figure."""
    pct = _finite(value)
    if pct is None or pct <= 0:
        return None
    return f"{_trim_zeros(f'{pct:.2f}')}%" if pct >= 0.01 else f"{pct:.3g}%"


def usd_text(value: Any) -> Optional[str]:
    """`2e9` → "$2B ($2,000,000,000)": the short form for reading, the exact one for checking the filing."""
    usd = _finite(value)
    if usd is None or usd <= 0:
        return None
    exact = f"${usd:,.0f}"
    for scale, unit in ((1e12, "T"), (1e9, "B"), (1e6, "M")):
        if usd >= scale:
            return f"${_trim_zeros(f'{usd / scale:,.2f}')}{unit} ({exact})"
    return exact


def date_text(value: Any) -> Optional[str]:
    """An ISO date (or a `date`) as "Jun 30, 2026"; anything else cleaned as written, or None."""
    day: Optional[date] = None
    if isinstance(value, datetime):
        day = value.date()
    elif isinstance(value, date):
        day = value
    elif isinstance(value, str) and value.strip():
        try:
            day = date.fromisoformat(value.strip()[:10])
        except ValueError:
            return _one_line(value, 20)
    if day is None:
        return None
    return f"{day:%b} {day.day}, {day.year}"


def utf16_len(text: str) -> int:
    return review_service.utf16_len(text)


def _fit(text: str, limit: int = MAX_MESSAGE_CHARS) -> str:
    """`text` cut to `limit` UTF-16 units (Telegram's measure) with an ellipsis, never mid-character."""
    if utf16_len(text) <= limit:
        return text
    out: List[str] = []
    units = 0
    for ch in text:
        width = 2 if ord(ch) > 0xFFFF else 1
        if units + width > limit - 1:
            break
        out.append(ch)
        units += width
    return "".join(out) + "…"


def canonical_id(value: Any) -> Optional[str]:
    """The canonical lowercase uuid string a button is built from, or None."""
    if isinstance(value, bool) or value is None:
        return None
    try:
        return str(uuid.UUID(str(value)))
    except (TypeError, ValueError, AttributeError):
        return None


def _is_message_id(value: Any) -> bool:
    return type(value) is int and value > 0


def message_id_of(envelope: Any) -> Optional[int]:
    """The `message_id` of a Bot API send/edit answer, or None (Learn's `review.message_id_of`)."""
    result = envelope.get("result") if isinstance(envelope, dict) else None
    mid = result.get("message_id") if isinstance(result, dict) else None
    return mid if _is_message_id(mid) else None


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _ctx(finding: Any) -> str:
    """The identifiers every WARNING/ERROR line carries — never the row's text."""
    f = _mapping(finding)
    parts = []
    for key, label in (("company_slug", "slug"), ("accession", "accession"), ("filer_cik", "filer_cik"),
                       ("investee_cik", "investee_cik"), ("id", "finding"), ("reason", "reason")):
        value = f.get(key)
        parts.append(f"{label}={_one_line(value, 64) if value is not None else '-'}")
    return " ".join(parts)


def keyboard(finding_id: Any, verbs: Sequence[str]) -> Dict[str, Any]:
    """One row of buttons for `finding_id`, in `verbs` order (`()` = an empty keyboard, which REMOVES the
    buttons on an edit). Refuses (ValueError) an unknown or repeated verb and a finding id that is not a uuid —
    a button that could never be decided is a bug, never something to send."""
    verbs = tuple(verbs)
    if any(v not in CALLBACK_VERBS for v in verbs) or len(set(verbs)) != len(verbs):
        raise ValueError(f"stake buttons must be distinct callback verbs, got {verbs!r}")
    if not verbs:
        return {"inline_keyboard": []}
    fid = canonical_id(finding_id)
    if fid is None:
        raise ValueError(f"stake buttons need a finding uuid, got {finding_id!r}")
    return {"inline_keyboard": [[{"text": BUTTON_LABELS[v], "callback_data": callback_data(v, fid)}
                                 for v in verbs]]}


# ── message texts (pure; the preview prints them too) ─────────────────────────────────────────


def reason_text(code: Any) -> str:
    """The digest text of a ledger reason (`stake_contracts.REASONS`), or the cleaned code when unknown."""
    if isinstance(code, str) and code in REASONS:
        return REASONS[code]
    if isinstance(code, str) and code.strip():
        logger.warning("%s reason %r is not in stake_contracts.REASONS — shown as its code", _TAG,
                       _one_line(code, 40))
        return f"reason {_one_line(code, 40)}"
    return "reason not recorded"


def _source_url(finding: Mapping[str, Any], row: Mapping[str, Any], *, prefer_finding: bool = False) -> Optional[str]:
    """The row's source URL, else the finding's (the finding's first when `prefer_finding`: a lifecycle note is
    about the filing that TRIGGERED it, not the row's old source) — only an https URL with no space or control
    character."""
    order = (finding.get("source_url"), row.get("source_url")) if prefer_finding else (
        row.get("source_url"), finding.get("source_url"))
    for value in order:
        if (isinstance(value, str) and value.startswith("https://") and len(value) <= 500
                and not any(ch.isspace() or unicodedata.category(ch) in _UNSAFE_CATEGORIES for ch in value)):
            return value
    return None


def fact_lines(finding: Mapping[str, Any], row: Optional[Mapping[str, Any]], *, brief: bool = False,
               link_filing: bool = False) -> List[str]:
    """The facts of one finding — never trimmed to fit: member → investee, the row's figures and basis, as_of,
    kind / ticker / CUSIP, the source title, the filing, the source URL ON ITS OWN LINE, and why. `brief`
    (a lifecycle note with no row) keeps the pair, the filing, the link and the reason only. `link_filing` (a
    lifecycle note) links the filing named on the "Filing:" line — the one that triggered the note — while
    "Source:" still names the row's own source (review B facts-F3)."""
    f = _mapping(finding)
    r = _mapping(row)
    if not r and isinstance(f.get("proposed_row"), Mapping):
        r = f["proposed_row"]
    member = _text(f, "member_name", 60) or _text(f, "company_slug", 60) or "a member"
    investee = _text(r, "investee_name", 80) or "an unnamed company"
    lines = [f"{member} → {investee}"]
    if not brief:
        pct = pct_text(r.get("ownership_pct"))
        basis = _text(r, "ownership_basis", 80)
        if pct:
            lines.append(f"Stake: {pct} {basis}" if basis else f"Stake: {pct}")
        value = usd_text(r.get("disclosed_value_usd"))
        if value:
            vb = r.get("value_basis")
            words = VALUE_BASIS_LABELS.get(vb) if isinstance(vb, str) else None
            words = words or _text(r, "value_basis", 40)
            lines.append(f"Value: {value} — {words}" if words else f"Value: {value}")
        if not pct and not value:
            lines.append("Stake: no figure on the row")
        as_of = date_text(r.get("as_of"))
        if as_of:
            lines.append(f"As of: {as_of}")
        kind = r.get("kind")
        bits = [(KIND_LABELS.get(kind) if isinstance(kind, str) else None) or _text(r, "kind", 40) or "kind not set"]
        symbol = _text(r, "investee_us_symbol", 20)
        if symbol:
            bits.append(f"ticker {symbol}")
        cusip = _text(r, "investee_cusip", 12)
        if cusip:
            bits.append(f"CUSIP {cusip}")
        lines.append("Kind: " + " · ".join(bits))
        local = _text(r, "local_listing", 60)
        if local:
            lines.append(f"Listed: {local}")
        detail = _text(f, "detail", EVIDENCE_LINE_MAX)
        if detail:
            # An optional key the finder may add (not a ledger column): a T2 fixed amount the row cannot carry.
            lines.append(f"Note: {detail}")
        title = _text(r, "source_title", 160)
        if title:
            lines.append(f"Source: {title}")
    filing = [b for b in (_text(f, "form", 30), _text(f, "accession", 30)) if b]
    filed = date_text(f.get("filed_on"))
    if filed:
        filing.append(f"filed {filed}")
    issuer = _text(f, "investee_cik", 12)
    if issuer:
        filing.append(f"issuer CIK {issuer}")
    if filing:
        lines.append("Filing: " + " · ".join(filing))
    url = _source_url(f, r, prefer_finding=link_filing)
    if url is None:
        logger.warning("%s no usable source URL to show %s", _TAG, _ctx(f))
    lines.append(url or "(no usable source link — see the logs)")
    lines.append(f"Why: {reason_text(f.get('reason'))}")
    return lines


def evidence_lines(evidence: Any) -> List[str]:
    """The filing's verified sentences as "• “…”" lines, each ≤ EVIDENCE_LINE_MAX characters. A non-string
    entry is skipped; a bare string is ONE line (never its characters)."""
    if isinstance(evidence, str):
        items: Sequence[Any] = [evidence]
    elif isinstance(evidence, Sequence):
        items = evidence
    else:
        items = []
    out: List[str] = []
    for item in list(items)[:MAX_EVIDENCE_LINES]:
        if not isinstance(item, str) or not item.strip():
            continue
        out.append(f"• “{_one_line(item, EVIDENCE_LINE_MAX - 4)}”")
    return out


def _compose(head: List[str], evidence: List[str], tail: List[str], *, what: str) -> str:
    """head + evidence + tail within MAX_MESSAGE_CHARS units: evidence is dropped from the END first (with a
    "+N more" line), never the head or the tail. Only a head that alone overflows — impossible for capped
    fields — is hard-cut, with a WARNING."""
    total = len(evidence)
    text = ""
    for shown in range(total, -1, -1):
        parts = list(head)
        if total:
            parts.append("Evidence (from the filing):")
            parts += evidence[:shown]
            if shown < total:
                parts.append(f"(+{total - shown} more not shown — see the filing)")
        parts += tail
        text = "\n".join(parts)
        if utf16_len(text) <= MAX_MESSAGE_CHARS:
            return text
    logger.warning("%s %s: the facts alone exceed %d units — hard-cut", _TAG, what, MAX_MESSAGE_CHARS)
    return _fit(text)


def auto_note_text(finding: Mapping[str, Any], row: Mapping[str, Any]) -> str:
    head = ["🟢 Trillion Club stake — going live automatically"] + fact_lines(finding, row)
    tail = ["It goes live now, from the member's own SEC filing. ↩️ Withdraw takes it down at any time · "
            "👍 Keep confirms it (only a kept row may appear in marketing posts)."]
    return _compose(head, [], tail, what="auto note")


def proposal_text(finding: Mapping[str, Any], row: Mapping[str, Any], *, trial: bool, evidence: Any) -> str:
    f = _mapping(finding)
    update = f.get("tier") == TIER_UPDATE
    head = ["🟡 Trillion Club stake — " + ("update proposal" if update else "proposal") + " (Publish or Dismiss)"]
    if trial:
        head.append(TRIAL_LINE)
    head += fact_lines(finding, row)
    if not update:
        action = "✅ Publish puts it live (re-checked first) · 🗑 Dismiss drops it; this pair is not proposed again."
    elif f.get("target_live") is False:
        # The finder says the row it updates is NOT live (held back, or taken down): never "the live row".
        action = ("✅ Publish puts this in place of the row on file — which is not live now — and makes it live "
                  "(re-checked first) · 🗑 Dismiss leaves that row as it is, not live.")
    else:
        action = ("✅ Publish replaces the live row with this one (re-checked first) · 🗑 Dismiss leaves the live row "
                  "as it is.")
    tail = [action, f"No answer in {PROPOSAL_TTL_DAYS} days → it expires unpublished."]
    return _compose(head, evidence_lines(evidence), tail, what="proposal")


def lifecycle_text(finding: Mapping[str, Any], row: Optional[Mapping[str, Any]], *, text: Any,
                   offer_withdraw: bool) -> str:
    body = _block(text, LIFECYCLE_TEXT_MAX) or "(no details given)"
    head = ["🔔 Trillion Club stake — a change to a machine-found row", body]
    head += fact_lines(finding, row, brief=not isinstance(row, Mapping), link_filing=True)
    tail = ["↩️ Withdraw takes the row down."] if offer_withdraw else []
    return _compose(head, [], tail, what="lifecycle note")


def _digest_line(value: str) -> str:
    """One digest line: unsafe characters → spaces, trailing space dropped, capped. Leading spaces (an
    indented sub-line) are kept."""
    limit = DIGEST_LINE_MAX * 4
    text = "".join(" " if unicodedata.category(ch) in _UNSAFE_CATEGORIES else ch for ch in value[:limit]).rstrip()
    return _capped(text, DIGEST_LINE_MAX, cut=len(value) > limit)


def digest_parts(lines: Any, *, today: date) -> Tuple[List[str], int]:
    """The digest as message parts (each ≤ 4,096 units, `review_service.split_message`) and how many lines
    the MAX_DIGEST_PARTS backstop left out. `([], 0)` when there is nothing to say. Raises TypeError for a
    `lines` that is not a sequence (a finder bug: nothing is sent, so nothing is marked reported)."""
    if isinstance(lines, str):
        logger.warning("%s digest given one string, not a list of lines — sent as one line", _TAG)
        items: Sequence[Any] = [lines]
    elif isinstance(lines, Sequence):
        items = lines
    else:
        raise TypeError(f"digest lines must be a sequence of strings, got {type(lines).__name__}")
    body: List[str] = []
    skipped = 0
    for item in items:
        if isinstance(item, str):
            body.append(_digest_line(item))
        else:
            skipped += 1
    if skipped:
        logger.warning("%s digest: %d non-string line(s) left out", _TAG, skipped)
    if not any(line.strip() for line in body):
        return [], 0
    text = "\n".join([f"{DIGEST_HEADER} · {today:%a %b} {today.day}"] + body)
    parts = review_service.split_message(text, MAX_MESSAGE_CHARS)
    if len(parts) <= MAX_DIGEST_PARTS:
        return parts, 0
    kept = parts[: MAX_DIGEST_PARTS - 1]
    dropped = sum(1 for line in "\n".join(parts[MAX_DIGEST_PARTS - 1:]).split("\n") if line.strip())
    kept.append(f"… {dropped} more digest line(s) not sent — the digest is longer than {MAX_DIGEST_PARTS} "
                f"messages (see the logs).")
    return kept, dropped


# ── the Bot API calls of the sending side (each paced; none raises except CancelledError) ────────


def _configured_chat(what: str, ctx: str) -> Optional[int]:
    chat_id = review_service.review_chat_id()
    if not review_service.is_configured() or chat_id is None:
        logger.error("%s %s NOT SENT — the review bot is not configured (MARKETING_TELEGRAM_*) %s", _TAG, what, ctx)
        return None
    if telegram_pacing.is_rate_limited():
        logger.warning("%s %s NOT SENT — Telegram flood control for %.0f s more; the finder retries on a later "
                       "run %s", _TAG, what, telegram_pacing.seconds_left(), ctx)
        return None
    return chat_id


async def _send(text: str, *, what: str, ctx: str, silent: bool, board: Optional[Dict[str, Any]] = None,
                reply_to: Optional[int] = None) -> Tuple[bool, Optional[int]]:
    """(delivered, message id) — Learn's `_send` with the flood gate in front: never into a closed chat."""
    chat_id = _configured_chat(what, ctx)
    if chat_id is None:
        return False, None
    try:
        await telegram_pacing.PACER.wait()
        env = await telegram.send_message(chat_id, text, reply_markup=board, disable_notification=silent,
                                          reply_to_message_id=reply_to)
    except asyncio.CancelledError:
        raise
    except TelegramRateLimitException as e:
        # Telegram's 429 — or the shared pacer refusing because ANOTHER sender's 429 closed the chat while this
        # one waited. Either way the back-off is recorded for every sender (never shortened).
        telegram_pacing.note_rate_limit(e.retry_after)
        logger.warning("%s %s NOT SENT — Telegram flood control (retry_after=%s s, %s) %s", _TAG, what,
                       e.retry_after, type(e).__name__, ctx)
        return False, None
    except TelegramException as e:
        logger.error("%s %s NOT SENT (%s: %s) %s", _TAG, what, type(e).__name__, e, ctx)
        return False, None
    except Exception as e:  # noqa: BLE001 — a send never raises into the finder
        logger.error("%s %s NOT SENT (%s: %s) %s", _TAG, what, type(e).__name__, e, ctx, exc_info=True)
        return False, None
    mid = message_id_of(env)
    if mid is None:
        logger.error("%s %s was sent but Telegram returned no message id %s", _TAG, what, ctx)
    return True, mid


async def _edit(method: str, message_id: int, *, what: str, ctx: str, text: Optional[str] = None,
                board: Dict[str, Any]) -> bool:
    """editMessageText (`text` given) or editMessageReplyMarkup, paced like a send. "message is not modified"
    is success: the message already shows exactly this."""
    chat_id = _configured_chat(what, ctx)
    if chat_id is None:
        return False
    try:
        await telegram_pacing.PACER.wait()
        if method == "text" and text is not None:
            await telegram.edit_message_text(chat_id, message_id, text, reply_markup=board)
        else:
            await telegram.edit_message_reply_markup(chat_id, message_id, board)
        return True
    except asyncio.CancelledError:
        raise
    except TelegramRateLimitException as e:
        telegram_pacing.note_rate_limit(e.retry_after)
        logger.warning("%s %s NOT APPLIED — Telegram flood control (retry_after=%s s, %s) message=%s %s", _TAG,
                       what, e.retry_after, type(e).__name__, message_id, ctx)
        return False
    except TelegramException as e:
        if isinstance(e, TelegramRequestError) and _NOT_MODIFIED in str(e.description or "").lower():
            return True
        logger.warning("%s %s NOT APPLIED (%s: %s) message=%s %s", _TAG, what, type(e).__name__, e, message_id, ctx)
        return False
    except Exception as e:  # noqa: BLE001
        logger.error("%s %s NOT APPLIED (%s: %s) message=%s %s", _TAG, what, type(e).__name__, e, message_id, ctx,
                     exc_info=True)
        return False


class TelegramStakeNotifier:
    """`stake_contracts.StakeNotifier` over the marketing review bot (module docstring). Stateless: every
    method reads the bot settings at call time, so one instance serves a whole run."""

    def available(self) -> bool:
        """The review bot is configured (`review_service.is_configured()`)."""
        try:
            return bool(review_service.is_configured())
        except Exception as e:  # noqa: BLE001 — a settings bug reads as "not available", loudly
            logger.error("%s could not read the review bot settings (%s: %s)", _TAG, type(e).__name__, e,
                         exc_info=True)
            return False

    async def _note(self, what: str, finding: Any, render: Callable[[], str],
                    verbs: Tuple[str, ...]) -> Optional[int]:
        ctx = _ctx(finding)
        try:
            board = keyboard(_mapping(finding).get("id"), verbs) if verbs else None
            text = render()
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 — a note that cannot be built is not sent (never half a note)
            logger.error("%s %s NOT SENT — it could not be built (%s: %s) %s", _TAG, what, type(e).__name__, e, ctx,
                         exc_info=True)
            return None
        sent, mid = await _send(text, what=what, ctx=ctx, silent=False, board=board)
        if sent and mid is not None:
            logger.info("%s %s sent as message %d %s", _TAG, what, mid, ctx)
        return mid if sent else None

    async def send_auto_note(self, finding: Mapping[str, Any], row: Mapping[str, Any]) -> Optional[int]:
        """The LOUD 'going live automatically' note with ↩️ Withdraw + 👍 Keep. The message id, or None."""
        return await self._note("auto note", finding, lambda: auto_note_text(finding, row),
                                (VERB_WITHDRAW, VERB_KEEP))

    async def send_proposal(self, finding: Mapping[str, Any], row: Mapping[str, Any], *, trial: bool,
                            evidence: Sequence[str]) -> Optional[int]:
        """The LOUD one-tap proposal with ✅ Publish + 🗑 Dismiss. The message id, or None."""
        return await self._note("proposal", finding,
                                lambda: proposal_text(finding, row, trial=trial, evidence=evidence),
                                (VERB_PUBLISH, VERB_DISMISS))

    async def edit_proposal(self, finding: Mapping[str, Any], row: Mapping[str, Any], *, message_id: int,
                            trial: bool, evidence: Sequence[str]) -> bool:
        """Re-render an open proposal IN PLACE (the new evidence appended) — never a new message."""
        ctx = _ctx(finding)
        if not _is_message_id(message_id):
            logger.error("%s proposal edit refused — message id %r is not a Telegram message id %s", _TAG,
                         message_id, ctx)
            return False
        try:
            board = keyboard(_mapping(finding).get("id"), (VERB_PUBLISH, VERB_DISMISS))
            text = proposal_text(finding, row, trial=trial, evidence=evidence)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            logger.error("%s proposal edit NOT APPLIED — it could not be built (%s: %s) %s", _TAG,
                         type(e).__name__, e, ctx, exc_info=True)
            return False
        return await _edit("text", message_id, what="proposal edit", ctx=ctx, text=text, board=board)

    async def send_lifecycle_note(self, finding: Mapping[str, Any], row: Optional[Mapping[str, Any]], *,
                                  text: str, offer_withdraw: bool) -> Optional[int]:
        """A LOUD lifecycle note: the finder's `text`, then the facts; ↩️ Withdraw when `offer_withdraw`."""
        return await self._note("lifecycle note", finding,
                                lambda: lifecycle_text(finding, row, text=text, offer_withdraw=offer_withdraw),
                                (VERB_WITHDRAW,) if offer_withdraw else ())

    async def mark_message(self, *, message_id: int, finding_id: str, verdict: str, buttons: Sequence[str]) -> bool:
        """Replace an earlier message's buttons (`()` = none) and thread `verdict` under it as a SILENT reply
        (module docstring: the original text cannot be read back). True when both landed."""
        ctx = f"finding={_one_line(finding_id, 64)} message={message_id!r}"
        if not _is_message_id(message_id):
            logger.error("%s mark refused — message id %r is not a Telegram message id %s", _TAG, message_id, ctx)
            return False
        if not isinstance(verdict, str) or not verdict.strip():
            logger.error("%s mark refused — empty verdict %s", _TAG, ctx)
            return False
        try:
            if isinstance(buttons, (str, bytes)):     # "wk" would otherwise read as two verbs
                raise TypeError(f"buttons must be a sequence of verbs, got the string {buttons!r}")
            board = keyboard(finding_id, tuple(buttons or ()))
        except (TypeError, ValueError) as e:
            logger.error("%s mark refused — %s %s", _TAG, e, ctx)
            return False
        buttons_ok = await _edit("markup", message_id, what="mark (buttons)", ctx=ctx, board=board)
        sent, _ = await _send(_block(verdict, VERDICT_MAX), what="mark (verdict)", ctx=ctx, silent=True,
                              reply_to=message_id)
        return buttons_ok and sent

    async def send_digest(self, lines: Sequence[str]) -> bool:
        """The SILENT digest, split under 4,096 units. True when every part was delivered (and when there is
        nothing to send); False as soon as one part was not — the finder then keeps its lines for a later run."""
        try:
            parts, dropped = digest_parts(lines, today=datetime.now(timezone.utc).astimezone(ET).date())
        except Exception as e:  # noqa: BLE001
            logger.error("%s digest NOT SENT — it could not be built (%s: %s)", _TAG, type(e).__name__, e,
                         exc_info=True)
            return False
        if not parts:
            logger.info("%s digest: nothing to send", _TAG)
            return True
        if dropped:
            logger.error("%s digest over the %d-message backstop: %d line(s) NOT sent — the finder's size cap "
                         "(DIGEST_MAX_UNITS) should have stopped this; answering 'not delivered' so no dropped "
                         "line is marked reported", _TAG, MAX_DIGEST_PARTS, dropped)
        for i, part in enumerate(parts, 1):
            sent, _ = await _send(part, what=f"digest part {i}/{len(parts)}", ctx=f"parts={len(parts)}",
                                  silent=True)
            if not sent:
                return False
        logger.info("%s digest sent in %d part(s)", _TAG, len(parts))
        return not dropped


# ── the taps ──────────────────────────────────────────────────────────────────────────────────


class _StillWorking(Exception):
    """The decision outlived TAP_TIMEOUT_SECONDS; it keeps running, shielded."""


@dataclass
class _Tap:
    cq_id: str
    chat_id: int
    from_id: int
    message: Dict[str, Any]
    verb: str
    finding_id: str
    now: datetime
    answered: bool = False
    #: The handler stopped waiting (timeout, or the webhook was cancelled): the task's end is reported by
    #: whichever of `_detach` / its done callback runs second — exactly once.
    detached: bool = False
    finished: bool = False

    @property
    def message_id(self) -> Optional[int]:
        mid = self.message.get("message_id")
        return mid if _is_message_id(mid) else None


#: Strong references to every running decision and late edit: the event loop keeps only weak ones, and a
#: decision collected mid-write would be a write sequence cut in half.
_RUNNING: Set["asyncio.Future[Any]"] = set()


def _default_apply() -> ApplyFn:
    """`stake_finder.apply_owner_decision`, imported at tap time — LAZY, so the marketing bot loads no finder
    code until a stake tap arrives (an import failure is answered like any crash)."""
    from app.services.trillion_club.stake_finder import apply_owner_decision

    return apply_owner_decision


async def _answer(tap: _Tap, text: str) -> None:
    """answerCallbackQuery — EXACTLY once per tap (a second call is refused and logged as the bug it is)."""
    if tap.answered:
        logger.error("%s tap %s on finding %s: a second answer (%r) was refused", _TAG, tap.verb, tap.finding_id,
                     text)
        return
    tap.answered = True
    try:
        await telegram.answer_callback_query(tap.cq_id, text)
    except asyncio.CancelledError:
        raise
    except TelegramException as e:
        logger.warning("%s answerCallbackQuery failed — tap %s on finding %s (%s: %s)", _TAG, tap.verb,
                       tap.finding_id, type(e).__name__, e)
    except Exception as e:  # noqa: BLE001
        logger.error("%s answerCallbackQuery raised — tap %s on finding %s (%s: %s)", _TAG, tap.verb,
                     tap.finding_id, type(e).__name__, e, exc_info=True)


async def _show(tap: _Tap, verdict: str, buttons: Sequence[str]) -> None:
    """Append `verdict` to the tapped message and set its buttons (TapResult.buttons, same finding; `()`
    removes them). If the message cannot be edited and buttons must stay reachable, they go out on a NEW
    message threaded under it. Never paced, never raises (except CancelledError)."""
    try:
        board = keyboard(tap.finding_id, tuple(buttons))
    except ValueError as e:              # cannot happen: TapResult and the parser checked both
        logger.error("%s tap %s on finding %s: buttons %r refused (%s) — removing them", _TAG, tap.verb,
                     tap.finding_id, buttons, e)
        board, buttons = _KEYBOARD_REMOVED, ()
    line = _block(verdict, VERDICT_MAX)
    original = tap.message.get("text")
    try:
        if tap.message_id is None or not isinstance(original, str) or not original.strip():
            raise TelegramRequestError("no message text to append to", method="editMessageText")
        await telegram.edit_message_text(tap.chat_id, tap.message_id, review_service.append_verdict(original, line),
                                         reply_markup=board)
        return
    except asyncio.CancelledError:
        raise
    except TelegramRateLimitException as e:
        telegram_pacing.note_rate_limit(e.retry_after)
        logger.warning("%s edit hit flood control — tap %s on finding %s (%s)", _TAG, tap.verb, tap.finding_id, e)
        return
    except TelegramException as e:
        if _NOT_MODIFIED in str(getattr(e, "description", "") or "").lower():
            return
        logger.warning("%s edit failed — tap %s on finding %s message %s (%s: %s)", _TAG, tap.verb,
                       tap.finding_id, tap.message_id, type(e).__name__, e)
    except Exception as e:  # noqa: BLE001
        logger.error("%s edit raised — tap %s on finding %s (%s: %s)", _TAG, tap.verb, tap.finding_id,
                     type(e).__name__, e, exc_info=True)
    if not buttons:
        return
    if telegram_pacing.is_rate_limited():
        logger.warning("%s could not re-offer %s for finding %s — Telegram flood control", _TAG, buttons,
                       tap.finding_id)
        return
    try:
        await telegram.send_message(tap.chat_id, line, reply_markup=board, reply_to_message_id=tap.message_id)
    except asyncio.CancelledError:
        raise
    except TelegramRateLimitException as e:
        telegram_pacing.note_rate_limit(e.retry_after)
        logger.warning("%s could not re-offer the buttons for finding %s (%s)", _TAG, tap.finding_id, e)
    except Exception as e:  # noqa: BLE001
        logger.warning("%s could not re-offer the buttons for finding %s (%s: %s)", _TAG, tap.finding_id,
                       type(e).__name__, e)


def _report_late(tap: _Tap, fut: "asyncio.Future[Any]") -> None:
    """The end of a decision the handler stopped waiting for: logged, and its verdict shown if it has one."""
    if fut.cancelled():
        logger.error("%s tap %s on finding %s: its decision was cancelled after the answer (shutdown?) — the "
                     "finder's reconcile finishes or rolls back what it left", _TAG, tap.verb, tap.finding_id)
        return
    exc = fut.exception()
    if exc is not None:
        logger.error("%s tap %s on finding %s FAILED after the owner was answered (%s: %s)", _TAG, tap.verb,
                     tap.finding_id, type(exc).__name__, exc, exc_info=(type(exc), exc, exc.__traceback__))
        _late_task(tap, fut, f"{TOAST_CRASH} — that tap did not finish; tap again.")
        return
    result = fut.result()
    if not isinstance(result, TapResult):
        logger.error("%s tap %s on finding %s: the decision returned a %s, not a TapResult", _TAG, tap.verb,
                     tap.finding_id, type(result).__name__)
        return
    logger.info("%s tap %s on finding %s finished after its answer: ok=%s toast=%r", _TAG, tap.verb,
                tap.finding_id, result.ok, result.toast)
    if result.verdict is None:
        # The owner saw only "Still working": a refusal or a failed Withdraw must still reach them.
        _late_task(tap, fut, result.toast)
        return
    try:
        late = fut.get_loop().create_task(_show(tap, result.verdict, result.buttons))
    except RuntimeError as e:            # the loop is closing
        logger.error("%s tap %s on finding %s: its late verdict could not be shown (%s)", _TAG, tap.verb,
                     tap.finding_id, e)
        return
    _RUNNING.add(late)
    late.add_done_callback(_RUNNING.discard)


async def _late_reply(tap: _Tap, text: str) -> None:
    """A late, toast-only answer as a SILENT reply threaded under the tapped message (paced: this is off the
    tap path by now). Never raises (except CancelledError) — `_send` logs any failure."""
    await _send(_block(text, VERDICT_MAX), what=f"late answer to tap {tap.verb}", ctx=f"finding={tap.finding_id}",
                silent=True, reply_to=tap.message_id)


def _late_task(tap: _Tap, fut: "asyncio.Future[Any]", text: Optional[str]) -> None:
    if not isinstance(text, str) or not text.strip():
        return
    coro = _late_reply(tap, text)
    try:
        late = fut.get_loop().create_task(coro)
    except RuntimeError as e:            # the loop is closing
        coro.close()
        logger.error("%s tap %s on finding %s: its late answer %r could not be sent (%s)", _TAG, tap.verb,
                     tap.finding_id, text, e)
        return
    _RUNNING.add(late)
    late.add_done_callback(_RUNNING.discard)


def _finished(tap: _Tap, fut: "asyncio.Future[Any]") -> None:
    """The decision task's done callback."""
    _RUNNING.discard(fut)
    tap.finished = True
    if tap.detached:
        _report_late(tap, fut)


def _detach(tap: _Tap, fut: "asyncio.Future[Any]") -> None:
    tap.detached = True
    if tap.finished:                     # its callback already ran while the handler still waited
        _report_late(tap, fut)


async def _decide(tap: _Tap, apply: Optional[ApplyFn]) -> Any:
    """Run the decision in its own SHIELDED task for at most TAP_TIMEOUT_SECONDS. Raises `_StillWorking` past
    the timeout (the task keeps running), and whatever the decision raised."""
    fn = apply if apply is not None else _default_apply()
    fut = asyncio.ensure_future(fn(tap.verb, tap.finding_id, message_id=tap.message_id, now=tap.now))
    _RUNNING.add(fut)
    fut.add_done_callback(functools.partial(_finished, tap))
    try:
        return await asyncio.wait_for(asyncio.shield(fut), timeout=TAP_TIMEOUT_SECONDS)
    except asyncio.CancelledError:
        # The webhook gave up (its 45 s cap, a shutdown): the shield keeps the writes going; their end is logged.
        _detach(tap, fut)
        raise
    except asyncio.TimeoutError:
        if fut.done():
            # Finished right at the deadline — or the decision raised TimeoutError itself (re-raised here,
            # so it is answered as the crash it is).
            return fut.result()
        _detach(tap, fut)
        raise _StillWorking() from None


async def handle_stake_callback(*, cq_id: str, chat_id: int, from_id: int, message: Any, data: Any,
                                now: Optional[datetime] = None, apply: Optional[ApplyFn] = None) -> bool:
    """Apply one `z:` button tap. Called ONLY by `marketing.review_service.handle_update`, AFTER its owner
    allow-list (the tapping user and the chat are the configured owner). `apply` defaults to
    `stake_finder.apply_owner_decision` (lazy). Returns whether the tap DECIDED something (`TapResult.ok`
    within the timeout). Never raises (except CancelledError); every tap is answered exactly once."""
    msg = message if isinstance(message, dict) else {}
    when = now if isinstance(now, datetime) and now.tzinfo is not None and now.utcoffset() is not None \
        else datetime.now(timezone.utc)
    parsed = parse_callback_data(data)
    verb, fid = parsed if parsed is not None else ("", "")
    tap = _Tap(cq_id=cq_id, chat_id=chat_id, from_id=from_id, message=msg, verb=verb, finding_id=fid,
               now=when.astimezone(timezone.utc))
    if parsed is None:
        logger.warning("%s unknown callback data %r from %s — nothing decided", _TAG, _one_line(data, 80), from_id)
        await _answer(tap, TOAST_UNKNOWN)
        return False
    try:
        result = await _decide(tap, apply)
    except asyncio.CancelledError:
        raise
    except _StillWorking:
        logger.warning("%s tap %s on finding %s (message %s) is still running after %.0f s — answered 'still "
                       "working'; it carries on", _TAG, verb, fid, tap.message_id, TAP_TIMEOUT_SECONDS)
        await _answer(tap, TOAST_STILL_WORKING)
        return False
    except Exception as e:  # noqa: BLE001 — the webhook answers 200 whatever happens
        logger.error("%s tap %s on finding %s (message %s) FAILED (%s: %s) — the buttons stay; tap again", _TAG,
                     verb, fid, tap.message_id, type(e).__name__, e, exc_info=True)
        await _answer(tap, TOAST_CRASH)
        return False
    if not isinstance(result, TapResult):
        logger.error("%s tap %s on finding %s: the decision returned a %s, not a TapResult", _TAG, verb, fid,
                     type(result).__name__)
        await _answer(tap, TOAST_CRASH)
        return False
    logger.info("%s tap %s on finding %s (message %s) by telegram:%s → ok=%s toast=%r", _TAG, verb, fid,
                tap.message_id, from_id, result.ok, result.toast)
    await _answer(tap, result.toast)
    if result.verdict is not None:
        await _show(tap, result.verdict, result.buttons)
    return result.ok is True
