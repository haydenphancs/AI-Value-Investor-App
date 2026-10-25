"""The weekly WRITER: one fact sheet → one validated iOS article, or a typed reason why not
(plan "Wiser weekly content engine" §1.7).

Flow of ONE generation (the unit the ledger counts, `generation_id`):

    outline ──▶ shape + header checks ──▶ clean? ──no──▶ ONE outline repair ──▶ clean? ──no──▶ REJECT
       │
       ▼  for each section (5-7), given ONLY its own fact slice:
    section ──▶ validate every block + its length ──▶ clean? ──no──▶ ONE section repair ──▶ clean? ──no──▶ REJECT
       │
       ▼
    render (code-owned fields) ──▶ validators.validate_article ──▶ clean? ──no──▶ REJECT
       │
       ▼
    judge (LEARN_JUDGE_MODE: shadow records · enforce gates · off skips) ──▶ disclosure appended ──▶ ACCEPT

Outcomes (`WriterOutcome.status`), each counted against its OWN cap by the build step:

* `accepted` — the rendered article (disclosure appended LAST, never validated), its section
  kinds, the judge record and the tokens/calls spent;
* `content_rejected` — the content broke a rule after its one repair (every issue is returned
  so the build can store it and the owner can see why);
* `writer_failed` — no verdict on the content: a Gemini error, a blocked / MAX_TOKENS / empty /
  non-JSON / wrong-shape answer, an unusable judge answer in `enforce`, or a bug. Fail CLOSED —
  never parsed, never repaired as if it were content — and the tokens already spent are carried;
* `lease_lost` — `before_call()` returned False (or raised): another generation owns the piece,
  so this one stops spending at once.

Contract with the build step (pinned by `tests/test_learn_weekly_writer.py`):
* AT MOST `MODEL_CALLS_PER_GENERATION` model calls (outline + its repair + 2 per section for at
  most 7 sections + the judge + at most `judge.SUPPORT_MAX_CALLS` support-check calls = 22), each
  preceded by `before_call()`;
* every prompt opens with the nonce `REQUEST {generation_id} round {n} ({stage})` and every call
  passes `cache=False`: a rejected draft is never served back from the response cache;
* the writer model is `settings.LEARN_WRITER_MODEL`, thinking `LEARN_WRITER_THINKING_BUDGET`, and
  its system instruction is `neutral_system_instruction(prompts.SYSTEM_BODY)` (identity + advice
  guards; `tests/test_identity_rule_coverage.py`). The persona module is imported LAZILY: importing
  `agents` loads the research agent and the FMP client, which no learn_weekly module but `signals`
  may load at import time (the FMP boundary);
* RESUME: `resume` (a previous generation's `progress`: the outline and the sections it finished)
  is re-validated in full and reused only where it is still clean — a deploy mid-build does not
  re-buy finished sections. `on_progress` receives the same shape after every accepted step.

Never raises for content or model trouble (an outcome says what happened); raises ValueError only
for a caller bug (unknown kind/category, a sheet that is not a FactSheet). CancelledError
propagates untouched.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Awaitable, Callable, Dict, Iterable, List, Mapping, Optional, Sequence

from pydantic import ValidationError

from app.config import settings
from app.services.learn_weekly import article_schema as A
from app.services.learn_weekly import contracts as C
from app.services.learn_weekly import judge as J
from app.services.learn_weekly import prompts as P
from app.services.learn_weekly import validators as V

logger = logging.getLogger(__name__)

STATUS_ACCEPTED = "accepted"
STATUS_CONTENT_REJECTED = "content_rejected"
STATUS_WRITER_FAILED = "writer_failed"
STATUS_LEASE_LOST = "lease_lost"
STATUSES = (STATUS_ACCEPTED, STATUS_CONTENT_REJECTED, STATUS_WRITER_FAILED, STATUS_LEASE_LOST)

USAGE_TAG = "learn_weekly_writer"

#: ⚠️ The most model calls ONE generation makes: outline + ONE outline repair, then for each of at
#: most `article_schema.SECTION_COUNT[1]` (7) sections one call + ONE repair, then ONE judge call and
#: the support check's calls (at most `judge.SUPPORT_MAX_CALLS`, 2026-10-24).
#: The build's lease is sized from it (`build.LEASE_SECONDS` is refreshed before each call, so the
#: bound that matters is per call), and a writer change that adds a call (a second repair, a judge
#: retry, an article-level repair) must raise it in the same change — a test walks the worst path.
MODEL_CALLS_PER_GENERATION = 2 + 2 * A.SECTION_COUNT[1] + 1 + J.SUPPORT_MAX_CALLS

#: A section's body words must land inside these multiples of its (code-normalised) target.
SECTION_LENGTH_TOLERANCE = (0.8, 1.25)
#: How far a later non-peg section's target may move (multiples of its outline target) to absorb
#: the words the sections before it ran short or long (`running_target`).
RUNNING_TARGET_RANGE = (0.6, 1.5)
#: Display caps of the header strings (the catalog card and the hero; the validators cap far
#: higher only to bound regex work).
HEADER_CHAR_LIMITS: Mapping[str, int] = {"title": 90, "subtitle": 200, "cardSubtitle": 120}
TAG_LABEL_MAX_WORDS = 4
TAG_LABEL_MAX_CHARS = 32

#: Finish reasons that mean "refused or stopped" — never parsed (marketing's list).
_BLOCKED_FINISHES = frozenset({
    "SAFETY", "RECITATION", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII", "OTHER", "IMAGE_SAFETY",
    "LANGUAGE", "MALFORMED_FUNCTION_CALL",
})
_SECTION_CONTENT_FIELD = re.compile(r"^sections\[\d+\]\.content")
_ERROR_CAP = 300
#: An answer longer than this is a repetition loop, not an outline or a section — and parsing it
#: would cost the single uvicorn worker for nothing.
_MAX_ANSWER_CHARS = 200_000


@dataclass
class WriterOutcome:
    status: str
    content: Optional[Dict[str, Any]] = None        # rendered iOS article, disclosure appended
    section_kinds: List[str] = field(default_factory=list)
    issues: List[Dict[str, str]] = field(default_factory=list)
    judge: Optional[Dict[str, Any]] = None           # JudgeRecord.as_dict(), None when off
    tokens_used: int = 0
    calls: int = 0
    error: Optional[str] = None                      # writer_failed / lease_lost: one line
    rounds: List[Dict[str, Any]] = field(default_factory=list)
    model: str = ""
    prompt_version: str = P.PROMPT_VERSION

    @property
    def issue_codes(self) -> List[str]:
        return sorted({i.get("code", "") for i in self.issues})

    def validation_record(self) -> Dict[str, Any]:
        """What the build stores in the piece's `validation` column (JSON-safe): the issues, the
        section kinds, the judge record and how the article was produced."""
        return {
            "outcome": self.status,
            "issues": list(self.issues),
            "section_kinds": list(self.section_kinds),
            "judge": self.judge,
            "writer_model": self.model,
            "prompt_version": self.prompt_version,
            "calls": self.calls,
            "tokens_used": self.tokens_used,
            "rounds": list(self.rounds),
            "error": self.error,
        }


class _Stop(Exception):
    """Internal: end the generation with `status` (never escapes `write_article`)."""

    def __init__(self, status: str, error: str = "", issues: Sequence[V.Issue] = ()) -> None:
        super().__init__(error or status)
        self.status = status
        self.error = error
        self.issues = list(issues)


def _one_line(text: Any, cap: int = _ERROR_CAP) -> str:
    return " ".join(str(text).split())[:cap]


def _tokens(result: Any) -> int:
    """`tokens_used` of a generate_json result, read defensively (never NaN, negative or a bool)."""
    value = result.get("tokens_used") if isinstance(result, Mapping) else None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    if isinstance(value, float) and not math.isfinite(value):
        return 0
    return max(0, int(value))


def _exception_tokens(e: BaseException) -> int:
    """The tokens an exception says its stage already cost (`tokens_used`, set by the judge and
    the support check), read like `_tokens`: never NaN, negative or a bool."""
    return _tokens({"tokens_used": getattr(e, "tokens_used", None)})


def _finish(result: Mapping[str, Any]) -> str:
    finish = result.get("finish_reason")
    name = str(getattr(finish, "name", finish) or "").upper()
    for prefix in ("FINISHREASON.", "FINISH_REASON_"):
        if name.startswith(prefix):
            name = name[len(prefix):]
    return name


def parse_answer(result: Any) -> "tuple[Optional[Dict[str, Any]], Optional[str]]":
    """`generate_json`'s result → (the JSON object, None) or (None, why it is unusable). Blocked,
    MAX_TOKENS, empty, oversized, fenced-but-not-JSON and non-object answers are all unusable —
    they are never parsed into a "partial" draft (marketing `writer_service.parse_response`)."""
    if not isinstance(result, Mapping):
        return None, f"the model client returned {type(result).__name__}, not a result"
    finish = _finish(result)
    if finish in _BLOCKED_FINISHES:
        return None, f"blocked (finish={finish})"
    if finish == "MAX_TOKENS":
        return None, "truncated (finish=MAX_TOKENS)"
    text = result.get("text")
    if not isinstance(text, str) or not text.strip():
        return None, f"empty answer (finish={finish or None})"
    if len(text) > _MAX_ANSWER_CHARS:
        return None, f"answer of {len(text)} characters (> {_MAX_ANSWER_CHARS})"
    try:
        obj = json.loads(J.strip_code_fences(text))
    except (ValueError, RecursionError) as e:          # RecursionError: nested past the C stack
        return None, f"not JSON ({type(e).__name__})"
    if not isinstance(obj, dict):
        return None, f"top level is {type(obj).__name__}, not an object"
    return obj, None


def _issue_dicts(issues: Iterable[V.Issue]) -> List[Dict[str, str]]:
    return [i.as_dict() if isinstance(i, V.Issue) else dict(i) for i in issues]


class _Generation:
    """One generation's state: the call/token ledger and the steps. Not reusable."""

    def __init__(self, *, sheet: C.FactSheet, kind: str, category: str, topic_label: str,
                 served_titles: Sequence[str], in_flight_titles: Sequence[str],
                 served_slugs: Sequence[str], generation_id: str,
                 before_call: Callable[[], Awaitable[Optional[bool]]], client: Any,
                 reject_reason: Optional[str], published_date: date,
                 catalog: Sequence[Mapping[str, Any]],
                 on_progress: Optional[Callable[[Dict[str, Any]], Awaitable[Any]]],
                 judge_mode: str) -> None:
        self.sheet, self.kind, self.category = sheet, kind, category
        self.topic_label = topic_label
        self.served_titles, self.in_flight_titles = served_titles, in_flight_titles
        self.served_slugs = served_slugs
        self.gen = generation_id
        self.before_call, self.client = before_call, client
        self.reject_reason = reject_reason
        self.published_date = published_date
        self.catalog = catalog
        self.on_progress = on_progress
        self.judge_mode = judge_mode
        self.model = str(settings.LEARN_WRITER_MODEL or "").strip()
        self.thinking = _thinking_budget()
        self.calls = 0
        self.tokens = 0
        self.rounds: List[Dict[str, Any]] = []
        self.ctx = V.build_grounding_context(sheet)
        self.subjects = [x for s in sheet.subjects for x in (s.name, s.ticker) if isinstance(x, str)]
        self.fact_ids = [f.id for f in sheet.facts]
        # Imported here, not at module load: `agents/__init__` loads the research agent and the
        # FMP client (module docstring). The call-site scan accepts a variable reference.
        from app.services.agents.persona_config import neutral_system_instruction

        self.system = neutral_system_instruction(P.SYSTEM_BODY)

    # ── the model-call gate ──────────────────────────────────────────────────────────────────

    async def _gate(self, stage: str) -> None:
        """Before EVERY model call: the budget, then the caller's lease check."""
        if self.calls >= MODEL_CALLS_PER_GENERATION:
            logger.error("learn writer: generation %s would exceed its %d-call budget at %s — "
                         "stopped (a contract breach; see MODEL_CALLS_PER_GENERATION)",
                         self.gen, MODEL_CALLS_PER_GENERATION, stage)
            raise _Stop(STATUS_WRITER_FAILED, f"call budget of {MODEL_CALLS_PER_GENERATION} "
                                              f"exhausted at {stage}")
        try:
            ok = await self.before_call()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            # The lease cannot be confirmed: stop spending (fail safe). The build decides what a
            # still-held lease means.
            logger.error("learn writer: before_call raised before %s (generation %s): %s: %s",
                         stage, self.gen, type(e).__name__, e, exc_info=True)
            raise _Stop(STATUS_LEASE_LOST, _one_line(f"before_call raised before {stage}: "
                                                     f"{type(e).__name__}: {e}"))
        if ok is False:
            logger.warning("learn writer: lease lost before %s (generation %s) — stopping", stage,
                           self.gen)
            raise _Stop(STATUS_LEASE_LOST, f"lease lost before {stage}")
        self.calls += 1

    async def _write_call(self, stage: str, round_no: int, prompt: str,
                          schema: Dict[str, Any]) -> Dict[str, Any]:
        await self._gate(stage)
        try:
            result = await self.client.generate_json(
                prompt,
                system_instruction=self.system,
                model_name=self.model,
                response_schema=schema,
                thinking_budget=self.thinking,
                usage_tag=USAGE_TAG,
                cache=False,
            )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            self.rounds.append({"stage": stage, "round": round_no, "error": type(e).__name__})
            raise _Stop(STATUS_WRITER_FAILED, _one_line(f"{stage}: {type(e).__name__}: {e}"))
        used = _tokens(result)
        self.tokens += used
        obj, problem = parse_answer(result)
        self.rounds.append({"stage": stage, "round": round_no, "tokens_used": used,
                            "finish_reason": _finish(result) if isinstance(result, Mapping) else None,
                            "problem": problem})
        if problem is not None:
            raise _Stop(STATUS_WRITER_FAILED, _one_line(f"{stage}: {problem}"))
        return obj

    # ── outline ──────────────────────────────────────────────────────────────────────────────

    def _parse_outline(self, obj: Any, stage: str) -> A.Outline:
        try:
            return A.normalize_outline(A.parse_outline(obj), self.kind)
        except (ValidationError, TypeError, ValueError) as e:
            raise _Stop(STATUS_WRITER_FAILED, _one_line(f"{stage}: wrong shape: {e}"))

    def outline_issues(self, outline: A.Outline) -> List[V.Issue]:
        """Everything wrong with an outline BEFORE any section is bought: counts, fact ids, the
        peg rule, header lengths — then the validators over a stub article (header text, key
        highlights, statistics labels, section titles/icons/kinds, title and slug uniqueness)."""
        issues: List[V.Issue] = []
        n = len(outline.sections)
        lo, hi = A.SECTION_COUNT
        if not lo <= n <= hi:
            issues.append(V.Issue("sections", "section_count", f"{n} sections, need {lo}-{hi}"))
        ids = set(self.fact_ids)
        for i, sec in enumerate(outline.sections):
            unknown = [f for f in sec.fact_ids if f not in ids]
            if unknown:
                issues.append(V.Issue(f"sections[{i}].fact_ids", "unknown_fact",
                                      ", ".join(unknown)[:160]))
        has_peg_facts = any(f.origin == C.ORIGIN_SEC_PEG for f in self.sheet.facts)
        pegs = [i for i, s in enumerate(outline.sections) if s.kind == "peg"]
        if has_peg_facts and len(pegs) != 1:
            issues.append(V.Issue("sections", "peg_section",
                                  f"{len(pegs)} peg sections; the sheet has peg facts, need 1"))
        elif not has_peg_facts and pegs:
            issues.append(V.Issue("sections", "peg_section",
                                  f"{len(pegs)} peg sections; the sheet has no peg facts, need 0"))
        by_id = self.sheet.by_id()
        for i in pegs:
            if not any(by_id.get(f) is not None and by_id[f].origin == C.ORIGIN_SEC_PEG
                       for f in outline.sections[i].fact_ids):
                issues.append(V.Issue(f"sections[{i}].fact_ids", "peg_section",
                                      "the peg section cites no [P...] fact"))
        for key, cap in HEADER_CHAR_LIMITS.items():
            value = getattr(outline, key)
            if len(value) > cap:
                issues.append(V.Issue(key, "header_length", f"{len(value)} characters > {cap}"))
        tag_words = len(outline.tagLabel.split())
        if not (1 <= tag_words <= TAG_LABEL_MAX_WORDS) or len(outline.tagLabel) > TAG_LABEL_MAX_CHARS:
            issues.append(V.Issue("tagLabel", "header_length",
                                  f"{tag_words} words / {len(outline.tagLabel)} characters"))
        # The validators over a stub (the outline with empty sections): drop only what an empty
        # body must fail — the body word count / peg share ("sections") and the block lists.
        try:
            stub = A.render_ios_content(
                outline, [[] for _ in outline.sections], kind=self.kind, category=self.category,
                slug=A.make_slug(outline.title, self.published_date),
                published_date=self.published_date, sheet=self.sheet, related_titles=())
        except ValueError as e:
            issues.append(V.Issue("article", "article_invalid", _one_line(e, 160)))
            return issues
        view = A.draft_view(stub, A.section_kinds(outline))
        for issue in V.validate_article(view, kind=self.kind, sheet=self.sheet,
                                        served_titles=self.served_titles,
                                        in_flight_titles=self.in_flight_titles,
                                        served_slugs=self.served_slugs):
            if issue.field == "sections" or _SECTION_CONTENT_FIELD.match(issue.field):
                continue
            issues.append(issue)
        return issues

    async def outline(self, resume: Optional[Mapping[str, Any]]) -> "tuple[A.Outline, bool]":
        """The accepted outline, and whether it is the RESUMED one (its stored sections may then
        be reused too)."""
        if resume is not None and resume.get("outline") is not None:
            try:
                resumed = A.normalize_outline(A.parse_outline(resume["outline"]), self.kind)
            except (ValidationError, TypeError, ValueError) as e:
                logger.warning("learn writer: generation %s cannot reuse the stored outline (%s) — "
                               "writing a new one", self.gen, _one_line(e, 160))
            else:
                problems = self.outline_issues(resumed)
                if not problems:
                    logger.info("learn writer: generation %s RESUMED the stored outline (%d sections)",
                                self.gen, len(resumed.sections))
                    return resumed, True
                logger.info("learn writer: generation %s discards the stored outline (%s)", self.gen,
                            sorted({p.code for p in problems}))
        obj = await self._write_call("outline", 1, P.outline_prompt(
            sheet=self.sheet, kind=self.kind, category=self.category, topic_label=self.topic_label,
            served_titles=self.served_titles, in_flight_titles=self.in_flight_titles,
            generation_id=self.gen, round_no=1, reject_reason=self.reject_reason),
            A.outline_response_schema(self.fact_ids))
        outline = self._parse_outline(obj, "outline")
        issues = self.outline_issues(outline)
        if not issues:
            return outline, False
        logger.info("learn writer: generation %s outline needs a repair: %s", self.gen,
                    sorted({i.code for i in issues}))
        obj = await self._write_call("outline repair", 2, P.outline_repair_prompt(
            sheet=self.sheet, kind=self.kind, category=self.category, topic_label=self.topic_label,
            served_titles=self.served_titles, in_flight_titles=self.in_flight_titles,
            generation_id=self.gen, previous=obj, issues=issues, round_no=2,
            reject_reason=self.reject_reason), A.outline_response_schema(self.fact_ids))
        outline = self._parse_outline(obj, "outline repair")
        issues = self.outline_issues(outline)
        if issues:
            raise _Stop(STATUS_CONTENT_REJECTED, "outline still broke rules after its repair", issues)
        return outline, False

    # ── sections ─────────────────────────────────────────────────────────────────────────────

    def _blocks(self, obj: Any, stage: str, title: Optional[str] = None) -> List[Dict[str, Any]]:
        try:
            return A.section_blocks(A.parse_section(obj), title)
        except (ValidationError, TypeError, ValueError) as e:
            raise _Stop(STATUS_WRITER_FAILED, _one_line(f"{stage}: wrong shape: {e}"))

    def section_issues(self, index: int, sec: A.OutlineSection,
                       blocks: Sequence[Mapping[str, Any]],
                       target: Optional[int] = None) -> List[V.Issue]:
        """The section's rule and length issues; the length is judged against `target` (the
        running target, `running_target`), else the outline's own."""
        target = sec.target_words if target is None else target
        issues: List[V.Issue] = []
        base = f"sections[{index}]"
        if not blocks:
            issues.append(V.Issue(f"{base}.content", "bad_value", "the section has no blocks"))
        # A list's lead-in paragraph is code-made (`article_schema.section_blocks`) from the
        # list's own text: the model's answer is capped by the schema, so those never count.
        cap = A.MAX_BLOCKS_PER_SECTION + sum(1 for j in range(len(blocks))
                                             if V.is_list_lead_in(blocks, j))
        if len(blocks) > cap:
            issues.append(V.Issue(f"{base}.content", "bad_value", f"{len(blocks)} blocks > {cap}"))
        for j, block in enumerate(list(blocks)[:cap]):
            issues += V.validate_block(f"{base}.content[{j}]", block, section_kind=sec.kind,
                                       grounding_ctx=self.ctx, subjects=self.subjects)
        words = A.body_words(blocks)
        lo = int(target * SECTION_LENGTH_TOLERANCE[0])
        hi = int(math.ceil(target * SECTION_LENGTH_TOLERANCE[1]))
        if blocks and not lo <= words <= hi:
            issues.append(V.Issue(base, "section_length",
                                  f"{words} words; target {target} ({lo}-{hi})"))
        return issues

    async def section(self, outline: A.Outline, index: int,
                      resumed: Optional[Sequence[Mapping[str, Any]]],
                      target: Optional[int] = None
                      ) -> "tuple[List[Dict[str, Any]], bool]":
        """The accepted blocks of one section, and whether they were freshly written (False = the
        stored copy passed every check again and was reused). `target` is the section's running
        target (`running_target`), else the outline's own."""
        sec = outline.sections[index]
        target = sec.target_words if target is None else target
        if resumed is not None:
            blocks = [dict(b) for b in resumed if isinstance(b, Mapping)]
            # Length is soft (the writer kept this section with any length miss it had), so a
            # stored section is re-checked against its hard rules only — else every section kept
            # with a miss, and every one after it, was bought again on resume (review round 2).
            if (len(blocks) == len(resumed) and blocks
                    and not [i for i in self.section_issues(index, sec, blocks, target)
                             if i.code != "section_length"]):
                logger.info("learn writer: generation %s RESUMED section %d", self.gen, index + 1)
                return blocks, False
            logger.info("learn writer: generation %s rewrites section %d (the stored copy no "
                        "longer passes)", self.gen, index + 1)
        stage = f"section {index + 1}"
        obj = await self._write_call(stage, 1, P.section_prompt(
            sheet=self.sheet, outline=outline, index=index, target_words=target,
            generation_id=self.gen, round_no=1, reject_reason=self.reject_reason),
            A.SECTION_RESPONSE_SCHEMA)
        blocks = self._blocks(obj, stage, sec.title)
        issues = self.section_issues(index, sec, blocks, target)
        if not issues:
            return blocks, True
        logger.info("learn writer: generation %s %s needs a repair: %s", self.gen, stage,
                    sorted({i.code for i in issues}))
        first, first_hard = blocks, any(i.code != "section_length" for i in issues)
        # The repair sees the blocks that were CHECKED (empty blocks dropped, list lead-ins split
        # out), so every "content[j]" an issue names is the block it shows.
        try:
            obj = await self._write_call(f"{stage} repair", 2, P.section_repair_prompt(
                sheet=self.sheet, outline=outline, index=index, target_words=target,
                generation_id=self.gen, previous={"blocks": blocks}, issues=issues, round_no=2,
                reject_reason=self.reject_reason), A.SECTION_RESPONSE_SCHEMA)
            blocks = self._blocks(obj, f"{stage} repair", sec.title)
        except _Stop as e:
            if first_hard or e.status != STATUS_WRITER_FAILED:
                raise
            # An unusable LENGTH repair (blocked, cut off, wrong shape, a model error) is no
            # reason to drop a first draft that broke no rule (review round 2).
            logger.warning("learn writer: generation %s %s: the length repair failed (%s) — "
                           "keeping the first draft (%d words, target %d)", self.gen, stage,
                           e.error, A.body_words(first), target)
            return first, True
        issues = self.section_issues(index, sec, blocks, target)
        hard = [i for i in issues if i.code != "section_length"]
        if hard and not first_hard:
            # Asked only for more (or fewer) words, the repair broke a rule: the first draft broke
            # none, so it stands with its length miss rather than sinking the generation.
            logger.warning("learn writer: generation %s %s: the length repair broke %s — keeping "
                           "the first draft (%d words, target %d)", self.gen, stage,
                           sorted({i.code for i in hard}), A.body_words(first), target)
            return first, True
        if hard:
            raise _Stop(STATUS_CONTENT_REJECTED, f"{stage} still broke rules after its repair",
                        issues)
        if issues:
            # Length alone is soft: the later sections' running targets absorb it, and the
            # article-level word count is the real bound. A length-only repair that came back
            # FURTHER from the target is not kept over the first draft (a 3M repair went 209 → 136
            # words for a 378 target and sank the article, 2026-10-23).
            if not first_hard and (abs(A.body_words(first) - target)
                                   < abs(A.body_words(blocks) - target)):
                logger.info("learn writer: generation %s %s keeps its first draft (%d words) over "
                            "the repair (%d words) — closer to the target %d", self.gen, stage,
                            A.body_words(first), A.body_words(blocks), target)
                return first, True
            logger.info("learn writer: generation %s %s kept with a length miss (%s)", self.gen,
                        stage, issues[0].detail)
        return blocks, True

    async def progress(self, outline: A.Outline, sections: Sequence[Sequence[Mapping[str, Any]]]
                       ) -> None:
        if self.on_progress is None:
            return
        payload = {"outline": outline.model_dump(mode="json"),
                   "sections": [[dict(b) for b in blocks] for blocks in sections]}
        try:
            await self.on_progress(payload)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            # Progress is an optimisation (resume after a deploy); losing it costs calls, not
            # correctness.
            logger.warning("learn writer: generation %s could not save progress (%s: %s)",
                           self.gen, type(e).__name__, e)

    # ── judge ────────────────────────────────────────────────────────────────────────────────

    async def judge(self, content: Mapping[str, Any]) -> Optional[J.JudgeRecord]:
        """The rubric's one call, then the support check (`judge.check_support`, one gated call per
        `SUPPORT_CHUNK` sentences). Shadow records both — a failed one is recorded, never a gate;
        enforce fails closed on either failing and rejects on any verdict; off skips both."""
        if self.judge_mode == J.MODE_OFF:
            return None
        record = J.JudgeRecord(mode=self.judge_mode)
        errors: List[str] = []
        await self._gate("judge")
        try:
            verdicts, raw = await J.judge_article(self.client, self.sheet, content,
                                                  generation_id=self.gen)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            self._judge_failed(record, errors, "judge", e)
        else:
            used = _tokens(raw)
            self.tokens += used
            record.tokens_used += used
            record.finish_reason = _finish(raw) or None
            record.verdicts = list(verdicts)
            self.rounds.append({"stage": "judge", "round": 1, "tokens_used": used,
                                "finish_reason": record.finish_reason, "problem": None})
            if record.verdicts and self.judge_mode == J.MODE_ENFORCE:
                self._reject(record)          # already rejected: the support check is not bought
        try:
            found, meta = await J.check_support(self.client, self.sheet, content,
                                                generation_id=self.gen,
                                                before_call=lambda: self._gate("support"))
        except _Stop as e:
            # The gate stopped it between chunks (budget, lost lease): the chunks already
            # answered were still paid for.
            self.tokens += _exception_tokens(e)
            raise
        except Exception as e:
            self._judge_failed(record, errors, "support", e)
        else:
            used = int(meta.get("tokens_used") or 0)
            self.tokens += used
            record.tokens_used += used
            record.verdicts += found
            record.support = {"calls": meta.get("calls"), "sentences": meta.get("sentences"),
                              "flagged": len(found)}
            self.rounds.append({"stage": "support", "round": 1, "tokens_used": used,
                                "calls": meta.get("calls"), "problem": None})
        if errors:
            record.error = _one_line("; ".join(errors))
            logger.warning("learn writer: shadow judge FAILED for generation %s (%s) — recorded, "
                           "not a gate", self.gen, record.error)
        if record.verdicts:
            codes = sorted({v.rule for v in record.verdicts})
            if self.judge_mode == J.MODE_ENFORCE:
                self._reject(record)
            logger.info("learn writer: shadow judge would flag generation %s: %s (%d verdicts)",
                        self.gen, codes, len(record.verdicts))
        return record

    def _reject(self, record: J.JudgeRecord) -> None:
        """Enforce: the judge's verdicts are a content rejection, with every one as an issue."""
        codes = sorted({v.rule for v in record.verdicts})
        raise _Stop(STATUS_CONTENT_REJECTED, f"the judge flagged {codes}",
                    [V.Issue(v.label, v.rule, f"\"{v.quote}\" - {v.reason}"[:160])
                     for v in record.verdicts])

    def _judge_failed(self, record: J.JudgeRecord, errors: List[str], stage: str,
                      e: Exception) -> None:
        """One judge stage failed: carry the tokens it already cost and record why. Under enforce
        that ends the generation at once (no verdict is never a pass, and no further call is
        worth its cost) — as a content rejection when the rubric's call had already flagged it."""
        spent = _exception_tokens(e)
        self.tokens += spent
        record.tokens_used += spent
        errors.append(("" if stage == "judge" else f"{stage}: ") + f"{type(e).__name__}: {e}")
        self.rounds.append({"stage": stage, "round": 1, "tokens_used": spent,
                            "error": type(e).__name__})
        if self.judge_mode == J.MODE_ENFORCE:
            if record.verdicts:
                self._reject(record)
            record.error = _one_line("; ".join(errors))
            raise _Stop(STATUS_WRITER_FAILED, f"judge: {record.error}")

    # ── the whole generation ─────────────────────────────────────────────────────────────────

    async def run(self, resume: Optional[Mapping[str, Any]]) -> WriterOutcome:
        outline, resumed = await self.outline(resume)
        if not resumed:
            await self.progress(outline, [])
        kinds = A.section_kinds(outline)
        # Stored sections are reusable only under the RESUMED outline: a new outline restarts.
        stored = resume.get("sections") if resumed and isinstance(resume, Mapping) else None
        stored = stored if isinstance(stored, list) else []
        sections: List[List[Dict[str, Any]]] = []
        targets = [s.target_words for s in outline.sections]
        written: List[int] = []
        reusing = True
        for i in range(len(outline.sections)):
            # Reuse is a PREFIX: once one stored section is rewritten, the later ones were written
            # beside a different neighbour and are rewritten too (no stitched-together article).
            prior = stored[i] if reusing and i < len(stored) and isinstance(stored[i], list) else None
            blocks, fresh = await self.section(outline, i, prior,
                                               running_target(targets, kinds, i, written))
            written.append(A.body_words(blocks))
            sections.append(blocks)
            if fresh:
                reusing = False
                await self.progress(outline, sections)
        content = A.render_ios_content(
            outline, sections, kind=self.kind, category=self.category,
            slug=A.make_slug(outline.title, self.published_date),
            published_date=self.published_date, sheet=self.sheet, related_titles=self.catalog)
        issues = V.validate_article(A.draft_view(content, kinds), kind=self.kind, sheet=self.sheet,
                                    served_titles=self.served_titles,
                                    in_flight_titles=self.in_flight_titles,
                                    served_slugs=self.served_slugs)
        if issues:
            raise _Stop(STATUS_CONTENT_REJECTED, "the assembled article broke rules", issues)
        record = await self.judge(content)
        final = A.append_disclosure(content)
        return self._outcome(STATUS_ACCEPTED, content=final, kinds=kinds,
                             judge=record.as_dict() if record is not None else None)

    def _outcome(self, status: str, *, content: Optional[Dict[str, Any]] = None,
                 kinds: Sequence[str] = (), issues: Sequence[V.Issue] = (),
                 judge: Optional[Dict[str, Any]] = None, error: Optional[str] = None
                 ) -> WriterOutcome:
        return WriterOutcome(status=status, content=content, section_kinds=list(kinds),
                             issues=_issue_dicts(issues), judge=judge, tokens_used=self.tokens,
                             calls=self.calls, error=error, rounds=list(self.rounds),
                             model=self.model)


def running_target(targets: Sequence[int], kinds: Sequence[str], index: int,
                   written: Sequence[int]) -> int:
    """Section `index`'s target once the sections before it hold `written` body words (one count
    per earlier section).

    The model writes about 85% of each target (live 3M sections, 2026-10-23: five sections summed
    to 1,288-1,404 words against 1,651), so per-section windows alone let every section pass
    while the article misses its range. A non-peg section therefore takes its share of what is
    still to write — the outline's total minus the words written and minus the targets of the peg
    sections still to come — in proportion to its outline target, within `RUNNING_TARGET_RANGE`
    of it. A peg keeps its own target, and only a peg's SHORTFALL is taken up: its overshoot is
    never taken out of the evergreen sections, or a long peg would shrink them and fail the
    article's peg share (review round 2). Pure: a resumed prefix gets the targets it was written
    under."""
    base = targets[index]
    if kinds[index] == "peg":
        return base
    counted = sum(min(w, targets[j]) if kinds[j] == "peg" else w
                  for j, w in enumerate(list(written)[:index]))
    later = sum(targets[j] for j in range(index, len(targets)) if kinds[j] != "peg")
    pegs_left = sum(targets[j] for j in range(index, len(targets)) if kinds[j] == "peg")
    remaining = sum(targets) - counted - pegs_left
    scaled = base * remaining / max(later, 1)
    lo, hi = RUNNING_TARGET_RANGE
    return int(round(min(max(scaled, base * lo), base * hi)))


def _thinking_budget() -> Optional[int]:
    """`LEARN_WRITER_THINKING_BUDGET` (0 = no thinking). A non-integer or negative value means the
    model's own default (None), logged — never a crash in the middle of a build."""
    value = settings.LEARN_WRITER_THINKING_BUDGET
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        logger.warning("learn writer: LEARN_WRITER_THINKING_BUDGET=%r is not a non-negative int — "
                       "using the model default", value)
        return None
    return value


async def write_article(*, sheet: C.FactSheet, kind: str, category: str, topic_label: str,
                        served_titles: Iterable[str], in_flight_titles: Iterable[str],
                        generation_id: str,
                        before_call: Callable[[], Awaitable[Optional[bool]]],
                        client: Any = None, reject_reason: Optional[str] = None,
                        published_date: date,
                        catalog: Sequence[Mapping[str, Any]] = (),
                        served_slugs: Iterable[str] = (),
                        resume: Optional[Mapping[str, Any]] = None,
                        on_progress: Optional[Callable[[Dict[str, Any]], Awaitable[Any]]] = None,
                        ) -> WriterOutcome:
    """One generation for one piece (module docstring). `served_titles` / `served_slugs` are the
    live catalog's, `in_flight_titles` every OTHER non-terminal piece's; `catalog` (served
    article dicts) feeds the related tiles; `published_date` (the ET publish date) dates the slug
    and the sort order. `reject_reason` (the owner's, on a Regenerate) is told to every prompt.
    `before_call()` runs before every model call: False (or an exception) ends the generation
    `lease_lost`."""
    if kind not in C.KINDS:
        raise ValueError(f"unknown piece kind {kind!r}")
    if category not in C.CATEGORIES:
        raise ValueError(f"category {category!r} not in {C.CATEGORIES}")
    if not isinstance(sheet, C.FactSheet):
        raise ValueError(f"sheet must be a contracts.FactSheet, got {type(sheet).__name__}")
    if not isinstance(published_date, date):
        raise ValueError("published_date must be a date")
    mode = J.normalize_mode(settings.LEARN_JUDGE_MODE)
    if client is None:
        from app.integrations.gemini import get_gemini_client

        client = get_gemini_client()
    if str(settings.LEARN_WRITER_MODEL).strip() == J.JUDGE_MODEL and mode != J.MODE_OFF:
        logger.warning("learn writer: LEARN_WRITER_MODEL equals the judge model %s — the second "
                       "gate shares the writer's blind spots", J.JUDGE_MODEL)
    gen = _Generation(
        sheet=sheet, kind=kind, category=category, topic_label=topic_label or sheet.topic_title,
        served_titles=[t for t in (served_titles or ()) if isinstance(t, str)],
        in_flight_titles=[t for t in (in_flight_titles or ()) if isinstance(t, str)],
        served_slugs=[s for s in (served_slugs or ()) if isinstance(s, str)],
        generation_id=str(generation_id), before_call=before_call, client=client,
        reject_reason=reject_reason, published_date=published_date, catalog=list(catalog or ()),
        on_progress=on_progress, judge_mode=mode)
    logger.info("learn writer: generation %s starts (%s/%s, topic %r, %d facts, judge %s, model %s)",
                gen.gen, kind, category, gen.topic_label[:80], len(sheet.facts), mode, gen.model)
    try:
        outcome = await gen.run(resume if isinstance(resume, Mapping) else None)
    except _Stop as stop:
        outcome = gen._outcome(stop.status, issues=stop.issues, error=stop.error or None)
    except asyncio.CancelledError:
        raise
    except Exception as e:  # a bug: fail closed and loudly, never publish around it
        logger.exception("learn writer: generation %s crashed (%s: %s)", gen.gen, type(e).__name__, e)
        outcome = gen._outcome(STATUS_WRITER_FAILED, error=_one_line(f"crash: {type(e).__name__}: {e}"))
    log = logger.info if outcome.status == STATUS_ACCEPTED else logger.warning
    log("learn writer: generation %s → %s (calls=%d tokens=%d codes=%s%s)", gen.gen, outcome.status,
        outcome.calls, outcome.tokens_used, outcome.issue_codes,
        f" error={outcome.error}" if outcome.error else "")
    return outcome


__all__ = [
    "MODEL_CALLS_PER_GENERATION", "RUNNING_TARGET_RANGE", "SECTION_LENGTH_TOLERANCE", "STATUSES",
    "STATUS_ACCEPTED", "STATUS_CONTENT_REJECTED", "STATUS_LEASE_LOST", "STATUS_WRITER_FAILED",
    "WriterOutcome",
    "parse_answer", "running_target", "write_article",
]
