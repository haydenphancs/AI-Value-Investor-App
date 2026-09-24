"""
The class-A marketing writer: one Learn fact sheet → a validated, publishable package
(SYSTEM_DESIGN_GUIDELINES §12.5, rules marketing.md §7).

Flow of one GENERATION (the unit the run ledger counts and caps):

    draft prompt ──generate_json──▶ parse ──▶ validate ──▶ clean? accept
                                                   │
                                                   └─ violations ─▶ ONE repair prompt (lists every
                                                      violation) ──▶ parse ──▶ validate ──▶ accept
                                                      the best acceptable round, else REJECT

"Acceptable" is scoped, because an all-or-nothing verdict over ~15 fields would reject most
honest drafts: the SHARED parts (hook, script, cards, slides) must be clean, and each platform's
caption stands alone — a caption that fails drops only that outlet (recorded in
`dropped_outlets`), provided at least `MIN_OUTLETS` survive.

What this module never does: invent a disclaimer, a CTA or a hashtag (code-owned, `post_copy`),
publish anything, or swallow a Gemini error — `generate_json` failures propagate so the caller
can tell a transient quota blip (retry later) from a broken request (log loudly).

It is the ONE marketing module allowed to import `agents.persona_config` (for
`neutral_system_instruction`), which loads the FMP client transitively through
`agents/__init__`; nothing here touches FMP data. `tests/test_marketing_import_boundary.py`
pins both halves of that sentence.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from app.services.agents.persona_config import neutral_system_instruction
from app.services.marketing import writer_prompts as wp
from app.services.marketing.compliance import Violation, clean, scan_text
from app.services.marketing.content_pool import ContentItem, strict_instruments
from app.services.marketing.grounding import check_grounding
from app.services.marketing.post_copy import (
    CAPTION_FIELDS,
    PLATFORMS,
    ComposedPost,
    body_budget,
    check_composed,
    compose,
    disclaimer_card,
    measured_length,
)
from app.services.marketing.selection import Template

logger = logging.getLogger(__name__)

WRITER_MODEL = "gemini-2.5-flash"
#: 0 = no thinking. Thought tokens bill at the output rate and this is template prose; the
#: validators, not the model's reasoning, are what keep it honest.
WRITER_THINKING_BUDGET = 0
# The per-RUN caps live in `script_service`, which owns the lease and the counters: two of them,
# `MAX_GENERATIONS` (generations the validators rejected) and `MAX_WRITER_FAILURES` (generations
# that ended with no verdict — a model error, a crash, a lost lease). This module's unit is ONE
# generation — a draft plus at most one repair — so it deliberately defines no cap of its own:
# a second copy here once said 3 while the enforced value was 4, and editing it changed nothing.
#: Fewest platform outlets an accepted package may carry.
MIN_OUTLETS = 3
USAGE_TAG = "marketing_writer"

#: Finish reasons that mean "the model refused or was stopped" — never parse those.
_BLOCKED_FINISHES = frozenset({
    "SAFETY", "RECITATION", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII", "OTHER", "IMAGE_SAFETY",
    "LANGUAGE", "MALFORMED_FUNCTION_CALL",
})
_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.IGNORECASE)

SHARED_FIELDS = ("hook", "video_script", "cards", "carousel_slides")


@dataclass
class ValidationResult:
    package: Optional[Dict[str, Any]]
    shared: List[Violation] = field(default_factory=list)
    outlets: Dict[str, List[Violation]] = field(default_factory=dict)
    posts: Dict[str, ComposedPost] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.package is not None and not self.shared and len(self.posts) >= MIN_OUTLETS

    @property
    def violations(self) -> List[Violation]:
        out = list(self.shared)
        for vs in self.outlets.values():
            out.extend(vs)
        return out


@dataclass
class RoundRecord:
    round: int
    kind: str
    finish_reason: Optional[str]
    tokens_used: int
    violations: List[Dict[str, str]]
    valid_outlets: List[str]

    def as_dict(self) -> Dict[str, Any]:
        return {
            "round": self.round, "kind": self.kind, "finish_reason": self.finish_reason,
            "tokens_used": self.tokens_used, "violations": self.violations,
            "valid_outlets": self.valid_outlets,
        }


@dataclass
class WriterResult:
    status: str                      # "accepted" | "rejected"
    package: Optional[Dict[str, Any]]
    #: accepted → the surviving package's own violations (its dropped outlets), untagged.
    #: rejected → EVERY round's violations, each tagged with its `round`, in round order — the
    #: last round alone is often a parse failure ("blocked", "not_json") that hides the content
    #: rule the drafts actually kept breaking.
    violations: List[Dict[str, Any]]
    rounds: List[RoundRecord]
    tokens_used: int
    model: str = WRITER_MODEL
    prompt_version: str = wp.PROMPT_VERSION
    raw_outputs: List[Any] = field(default_factory=list)   # for the preview CLI only


# ── parsing ───────────────────────────────────────────────────────────────────


def parse_response(result: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], List[Violation]]:
    """`generate_json`'s result → the JSON object, or None with the reason. SAFETY / RECITATION /
    MAX_TOKENS do not raise in `generate_json` (they come back as empty or partial text with a
    finish reason), so they are handled here, never parsed."""
    finish = str(result.get("finish_reason") or "").upper()
    for prefix in ("FINISHREASON.", "FINISH_REASON_"):
        if finish.startswith(prefix):
            finish = finish[len(prefix):]
    text = result.get("text") or ""
    if finish in _BLOCKED_FINISHES:
        return None, [Violation("response", "blocked", finish)]
    if finish == "MAX_TOKENS":
        return None, [Violation("response", "truncated", "output hit the token ceiling")]
    if not isinstance(text, str) or not text.strip():
        return None, [Violation("response", "empty_response", finish or "no text")]
    body = _FENCE_RE.sub("", text.strip())
    try:
        obj = json.loads(body)
    except ValueError as e:
        return None, [Violation("response", "not_json", f"{type(e).__name__}: {e}")]
    if not isinstance(obj, dict):
        return None, [Violation("response", "not_json", f"top level is {type(obj).__name__}")]
    return obj, []


# ── validation ────────────────────────────────────────────────────────────────


def _words(text: str) -> int:
    return len(text.split())


#: "Has a body": two letters in a row somewhere. Blank, whitespace-only, zero-width-only (clean()
#: has already removed those characters) and punctuation-only text ("." "-" "?") all fail it.
#: Deliberately NOT a length or word-count floor — a one-word hook or a short X body is
#: legitimate. Fixed-width pattern, scanned over a capped prefix: linear on any input.
_HAS_WORD_RE = re.compile(r"[^\W\d_]{2}")
_HAS_WORD_SCAN_CAP = 6000


def _has_words(text: str) -> bool:
    return bool(_HAS_WORD_RE.search(text[:_HAS_WORD_SCAN_CAP]))


#: A card / slide TITLE needs one letter or digit — "1929" and "2008" are natural case-study
#: titles — while "...", "?" and "-" are not (round 2, w2ww-2).
_HAS_ALNUM_RE = re.compile(r"[^\W_]")


def _str(obj: Dict[str, Any], key: str, out: List[Violation]) -> str:
    v = obj.get(key)
    if not isinstance(v, str):
        out.append(Violation(key, "schema", f"expected a string, got {type(v).__name__}"))
        return ""
    return clean(v)


def _pairs(obj: Dict[str, Any], key: str, lo: int, hi: int, title_max: int, body_max: int,
           out: List[Violation]) -> List[Dict[str, str]]:
    raw = obj.get(key)
    if not isinstance(raw, list):
        out.append(Violation(key, "schema", "expected a list"))
        return []
    if not lo <= len(raw) <= hi:
        out.append(Violation(key, "count", f"{len(raw)} not in {lo}-{hi}"))
    pairs: List[Dict[str, str]] = []
    for i, p in enumerate(raw[:hi]):
        name = f"{key}[{i}]"
        if not isinstance(p, dict) or not isinstance(p.get("title"), str) or not isinstance(p.get("body"), str):
            out.append(Violation(name, "schema", "expected {title, body} strings"))
            continue
        title, body = clean(p["title"]), clean(p["body"])
        # Round 2 (w2ww-2): punctuation-only text is burned into a card or narrated as if it
        # were content. An EMPTY string is reported by the scan itself ("field is empty").
        if title and not _HAS_ALNUM_RE.search(title[:_HAS_WORD_SCAN_CAP]):
            out.append(Violation(name + ".title", "empty", "title has no words"))
        if body and not _has_words(body):
            out.append(Violation(name + ".body", "empty", "body has no words"))
        if _words(title) > title_max:
            out.append(Violation(name + ".title", "too_long", f"{_words(title)} words > {title_max}"))
        if _words(body) > body_max:
            out.append(Violation(name + ".body", "too_long", f"{_words(body)} words > {body_max}"))
        pairs.append({"title": title, "body": body})
    return pairs


def _scan(name: str, text: str, item: ContentItem, *, allow_emoji: bool,
          myth_framed: bool = False, next_text: str = "") -> List[Violation]:
    return (scan_text(name, text, allow_emoji=allow_emoji,
                      strict_instruments=strict_instruments(item.kind),
                      company_terms=item.company_terms, myth_framed=myth_framed,
                      sheet_words=item.grounding.tokens, next_text=next_text)
            + check_grounding(name, text, item.grounding))


#: A card / slide TITLE that labels its body as the myth ("The myth", "Myth #2", "Common
#: misconception", "Myth One", "Myth No. 1", "Myth Number One"): the body's first sentence
#: states the misconception the template asks for, so the promise rows (and, for a claim with
#: "always"/"never", the forecast rows) read it as labelled (round 2 W2-OB-3, round 3 W3OB-4).
#: "Myth vs. Fact" labels nothing.
_MYTH_TITLE_RE = re.compile(
    r"^\s*(?:the\s+|a\s+)?(?:(?:common|popular|big|biggest|classic|old|first|second|third)\s+)?"
    r"(?:myth|misconception)(?:\s*(?:#|no\.?|number)?\s*(?:[0-9]+|one|two|three|four|five|six|"
    r"seven|eight|nine|ten))?\s*[:.!?]?\s*$", re.IGNORECASE)


def validate_package(obj: Dict[str, Any], item: ContentItem, run_date: date, *,
                     allow_x_url: bool = False) -> ValidationResult:
    """Clean, cap, scan and ground every field; compose the per-platform posts. Pure."""
    shared: List[Violation] = []

    hook = _str(obj, "hook", shared)
    # The schema's `required` is satisfied by "", and the hook is scanned only when non-empty
    # below, so without this a blank hook was accepted and handed to the worker as ''. A
    # non-string hook already carries `schema` — do not report it twice.
    if isinstance(obj.get("hook"), str) and not _has_words(hook):
        shared.append(Violation("hook", "empty", "hook has no words" if hook else "hook is empty"))
    if hook and _words(hook) > wp.HOOK_MAX_WORDS:
        shared.append(Violation("hook", "too_long", f"{_words(hook)} words"))

    script_raw = obj.get("video_script")
    script: List[str] = []
    if not isinstance(script_raw, list) or not all(isinstance(s, str) for s in script_raw):
        shared.append(Violation("video_script", "schema", "expected a list of strings"))
    else:
        script = [clean(s) for s in script_raw if clean(s)]
        words = sum(_words(s) for s in script)
        if not wp.SCRIPT_MIN_LINES <= len(script) <= wp.SCRIPT_MAX_LINES:
            shared.append(Violation("video_script", "count", f"{len(script)} lines"))
        if not wp.SCRIPT_MIN_WORDS <= words <= wp.SCRIPT_MAX_WORDS:
            shared.append(Violation("video_script", "length", f"{words} words"))
        for i, line in enumerate(script):
            if not _has_words(line):
                # A pause line ("…") would be narrated as silence and counted as a line.
                shared.append(Violation(f"video_script[{i}]", "empty", "line has no words"))
            if _words(line) > wp.SCRIPT_LINE_MAX_WORDS:
                shared.append(Violation(f"video_script[{i}]", "too_long", f"{_words(line)} words"))

    cards = _pairs(obj, "cards", wp.CARDS_MIN, wp.CARDS_MAX, wp.CARD_TITLE_MAX_WORDS,
                   wp.CARD_BODY_MAX_WORDS, shared)
    slides = _pairs(obj, "carousel_slides", wp.SLIDES_MIN, wp.SLIDES_MAX,
                    wp.SLIDE_TITLE_MAX_WORDS, wp.SLIDE_BODY_MAX_WORDS, shared)

    # Three reading CHAINS: the hook then the script, and each deck (title, body, next title…).
    # Every field is scanned with the field a reader meets next, so a question closing one field
    # is ANSWERED by the next ("Does the market always recover?" then "Yes, every time.") and a
    # claim is debunked by it exactly as by a next sentence (round 3, W3CB-2).
    chains: List[List[Tuple[str, str, bool]]] = [
        ([("hook", hook, False)] if hook else [])
        + [(f"video_script[{i}]", s, False) for i, s in enumerate(script)]]
    for key, pairs in (("cards", cards), ("carousel_slides", slides)):
        deck: List[Tuple[str, str, bool]] = []
        for i, p in enumerate(pairs):
            deck += [(f"{key}[{i}].title", p["title"], False),
                     (f"{key}[{i}].body", p["body"], bool(_MYTH_TITLE_RE.match(p["title"])))]
        chains.append(deck)
    for chain in chains:
        for j, (name, text, myth_framed) in enumerate(chain):
            nxt = chain[j + 1][1] if j + 1 < len(chain) else ""
            shared.extend(_scan(name, text, item, allow_emoji=False, myth_framed=myth_framed,
                                next_text=nxt))

    caps_raw = obj.get("captions")
    bodies: Dict[str, str] = {}
    field_violations: Dict[str, List[Violation]] = {}
    if not isinstance(caps_raw, dict):
        shared.append(Violation("captions", "schema", "expected an object"))
    else:
        for f in CAPTION_FIELDS:
            vs: List[Violation] = []
            text = _str(caps_raw, f, vs)
            if _has_words(text):
                budget = body_budget(f, item.category, run_date, allow_x_url=allow_x_url)
                n = measured_length(f, text)
                if n > budget:
                    vs.append(Violation(f, "too_long", f"{n} > {budget}"))
                vs.extend(_scan(f, text, item, allow_emoji=True))
            elif not vs:
                # compose() always appends hashtags + CTA + disclaimer, so a body-less caption
                # would still compose into a "post" — and count toward MIN_OUTLETS.
                vs.append(Violation(f, "empty", "caption has no words" if text else "caption is empty"))
            bodies[f] = text
            field_violations[f] = vs

    outlets: Dict[str, List[Violation]] = {}
    posts: Dict[str, ComposedPost] = {}
    for platform in PLATFORMS:
        fields = ("youtube_title", "youtube_description") if platform == "youtube" else (platform,)
        vs = [v for f in fields for v in field_violations.get(f, [])]
        if not vs and all(f in bodies for f in fields):
            post = compose(platform, bodies, category=item.category, run_date=run_date,
                           allow_x_url=allow_x_url)
            vs = check_composed(post, run_date)
            if not vs:
                posts[platform] = post
        if vs:
            outlets[platform] = vs

    package = {
        "hook": hook,
        "video_script": script,
        "cards": cards,
        "carousel_slides": slides,
        "captions": bodies,
        "posts": {p: post.as_dict() for p, post in posts.items()},
        "dropped_outlets": {p: [v.as_dict() for v in vs] for p, vs in outlets.items()},
        "disclaimer_card": disclaimer_card(run_date),
        "source_ref": item.key,
        "run_date": run_date.isoformat(),
    }
    return ValidationResult(package=package, shared=shared, outlets=outlets, posts=posts)


# ── generation ────────────────────────────────────────────────────────────────


def _pick_best(candidates: List[ValidationResult]) -> Optional[ValidationResult]:
    ok = [c for c in candidates if c.ok]
    if not ok:
        return None
    return max(ok, key=lambda c: (len(c.posts), -len(c.violations)))


#: Attribute a propagating exception carries: tokens this generation already spent before it
#: failed (pre-agreed contract with script_service, which adds it to `tokens_used`). The
#: exception itself is never wrapped or replaced — `is_transient_gemini_error` classifies by
#: type, and a wrapper would turn every transient timeout into an ERROR page.
TOKENS_ATTR = "marketing_tokens_used"


def _round_codes(rounds: List[RoundRecord]) -> Dict[str, List[str]]:
    """Codes only, per round — never `detail`, which carries matched names or model text and
    these logs feed Sentry and the Discord digest."""
    return {f"r{r.round}": sorted({v["code"] for v in r.violations}) for r in rounds}


def _note_failed_generation(e: Exception, tokens: int, rounds: List[RoundRecord], item: ContentItem,
                            generation_id: str) -> None:
    try:
        setattr(e, TOKENS_ATTR, int(tokens))
    except (AttributeError, TypeError) as attach_err:  # a __slots__ / C-level exception type
        logger.warning(
            "marketing writer: could not attach %s to %s (%s: %s) source_ref=%s generation=%s "
            "tokens=%d — this spend will be missing from the ledger", TOKENS_ATTR,
            type(e).__name__, type(attach_err).__name__, attach_err, item.key, generation_id, tokens,
        )
    if rounds:
        # Without this the rounds that DID come back (and why they failed) vanish: the caller
        # records only the exception text.
        logger.warning(
            "marketing writer: generation aborted by %s after %d round(s) source_ref=%s "
            "generation=%s tokens=%d codes=%s", type(e).__name__, len(rounds), item.key,
            generation_id, tokens, _round_codes(rounds),
        )


async def generate_package(
    item: ContentItem,
    template: Template,
    run_date: date,
    *,
    generation_id: str,
    client: Any = None,
    allow_x_url: bool = False,
    before_call: Optional[Callable[[], Awaitable[Optional[bool]]]] = None,
) -> WriterResult:
    """One generation: a draft and, if it has any violation, ONE repair. Gemini errors
    propagate (the caller classifies transient vs not) — unchanged in type, but carrying the
    tokens this generation already spent as `marketing_tokens_used` (`TOKENS_ATTR`); content
    failures come back as a `rejected` result with every round's violations. `before_call`
    runs before each model call and whatever it raises propagates (the script service
    refreshes its lease there — a generation that lost the run must not spend another call;
    telling a lost lease from a database blip is the caller's job, not this module's). If it
    returns False (the lease can no longer cover a call), a publishable candidate in hand is
    kept and the call is skipped; with nothing to keep the call still runs — it is the only way
    to a package, and the caller's terminal write is fenced. None or True mean proceed."""
    if client is None:
        from app.integrations.gemini import get_gemini_client

        client = get_gemini_client()

    rounds: List[RoundRecord] = []
    raw_outputs: List[Any] = []
    candidates: List[ValidationResult] = []
    tokens = 0
    last_violations: List[Violation] = []
    previous: Any = None

    try:
        for round_no, kind in ((1, "draft"), (2, "repair")):
            if kind == "draft":
                prompt = wp.draft_prompt(item, template, run_date, generation_id=generation_id,
                                         round_no=round_no, allow_x_url=allow_x_url)
            else:
                prompt = wp.repair_prompt(item, template, run_date, generation_id=generation_id,
                                          round_no=round_no, previous=previous,
                                          violations=last_violations, allow_x_url=allow_x_url)
            if before_call is not None and await before_call() is False:
                best = _pick_best(candidates)
                if best is not None:
                    logger.warning(
                        "marketing writer: the lease cannot cover the %s call source_ref=%s "
                        "generation=%s — accepting the round-%d package", kind, item.key,
                        generation_id, round_no - 1,
                    )
                    break
                logger.warning(
                    "marketing writer: the lease cannot cover the %s call source_ref=%s "
                    "generation=%s and nothing publishable is in hand — calling anyway (the "
                    "terminal write is fenced)", kind, item.key, generation_id,
                )
            try:
                result = await client.generate_json(
                    prompt,
                    system_instruction=neutral_system_instruction(wp.SYSTEM_BODY),
                    model_name=WRITER_MODEL,
                    response_schema=wp.RESPONSE_SCHEMA,
                    thinking_budget=WRITER_THINKING_BUDGET,
                    usage_tag=USAGE_TAG,
                )
            except Exception as e:
                best = _pick_best(candidates)
                if best is None:
                    raise
                # The repair call failed but the draft was already publishable: keep it rather
                # than spend another generation. Loud, because it is a degraded path.
                logger.warning(
                    "marketing writer repair call failed (%s: %s) source_ref=%s generation=%s — "
                    "accepting the round-1 package", type(e).__name__, e, item.key, generation_id,
                )
                break
            used = int(result.get("tokens_used") or 0)
            tokens += used
            obj, parse_violations = parse_response(result)
            raw_outputs.append(obj if obj is not None else (result.get("text") or "")[:2000])
            if obj is None:
                vr = None
                last_violations = parse_violations
            else:
                vr = validate_package(obj, item, run_date, allow_x_url=allow_x_url)
                candidates.append(vr)
                last_violations = vr.violations
                previous = obj
            rounds.append(RoundRecord(
                round=round_no, kind=kind, finish_reason=result.get("finish_reason"),
                tokens_used=used, violations=[v.as_dict() for v in last_violations],
                valid_outlets=sorted(vr.posts) if vr else [],
            ))
            if vr is not None and vr.ok and not vr.violations:
                break  # clean — nothing for a repair to improve
    except Exception as e:
        # Only Exception: a CancelledError (BaseException) must propagate untouched.
        _note_failed_generation(e, tokens, rounds, item, generation_id)
        raise

    best = _pick_best(candidates)
    if best is None:
        # Every round, tagged — the repair prompt above still saw only the last round's list.
        history = [{**v, "round": r.round} for r in rounds for v in r.violations]
        logger.warning(
            "marketing writer REJECTED generation source_ref=%s template=%s generation=%s "
            "codes=%s", item.key, template.id, generation_id, _round_codes(rounds),
        )
        return WriterResult("rejected", None, history, rounds, tokens,
                            raw_outputs=raw_outputs)
    package = dict(best.package or {})
    package.update({"template_id": template.id, "prompt_version": wp.PROMPT_VERSION,
                    "model": WRITER_MODEL})
    return WriterResult("accepted", package, [v.as_dict() for v in best.violations], rounds,
                        tokens, raw_outputs=raw_outputs)
