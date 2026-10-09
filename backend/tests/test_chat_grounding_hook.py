"""Where the log-only numeric grounding audit (`CHAT_GROUNDING`) hooks into both chat doors.

Source scans, comments STRIPPED (an explanatory comment names every token checked here) and
bound to the one function each check is about (`event_gen` inside `stream_chat_message`,
`send_chat_message`, `ChatService.generate_response` / `prepare_stream_generation`):

* stream door — the audit starts on the ENFORCED answer: after `enforce_answer(content)` and
  BEFORE `finalize_answer_notes(` (code-written notes are never audited); non-web tool results
  feed the evidence in the `kind == "tool"` branch, as the model saw them
  (`truncate_tool_result`); the fallback's own audit is captured; the task is settled only
  AFTER the `done` frame (it can never delay the persist or the user-visible `done`);
* send door — the counts `generate_response` computed are logged after `scan_answer` and
  before `finalize_answer_notes(`;
* `generate_response` computes the audit off the loop, BOUNDED (`audit_answer_bounded`), from
  the instruction it actually used and the truncated tool results, and a web turn is a skip.

Mutation-tested by hand (2026-10-08): moving `start_grounding_audit(` after
`finalize_answer_notes(`, deleting the `add_tool_result` line, moving the settle above
`_persist_turn(`, and dropping `web_used=web_used` each turn this file red. Fix round
(2026-10-08): dropping `truncate_tool_result(` from either door, moving the settle back above
`yield _sse("done"`, and swapping `audit_answer_bounded` for an unbounded `to_thread` each turn it
red too.

The behavioural half (one line per turn, answer byte-identical when the audit raises, the
fallback / replay / web skips) is in `tests/test_chat_stream_endpoint.py`.
"""

from __future__ import annotations

import ast
import io
import tokenize
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import app.services.chat_service as cs
from app.services.chat_service import ChatService

_BACKEND = Path(__file__).resolve().parents[1]
_CHAT_EP = _BACKEND / "app/api/v1/endpoints/chat.py"
_CHAT_SERVICE = _BACKEND / "app/services/chat_service.py"


def _strip_comments(src: str) -> str:
    """Source without comments (docstrings stay — they never hold a call)."""
    toks = [t for t in tokenize.generate_tokens(io.StringIO(src).readline)
            if t.type != tokenize.COMMENT]
    return tokenize.untokenize(toks)


def _func_source(path: Path, *names: str) -> str:
    """The source of the function reached by the `names` path (outer → nested), comments
    stripped. Raises if any level is missing, so a rename cannot make a check vacuous."""
    src = path.read_text()
    node: ast.AST = ast.parse(src)
    for name in names:
        found = None
        for child in ast.walk(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) \
                    and child.name == name \
                    and child is not node:
                found = child
                break
        assert found is not None, f"{name} not found in {path.name}"
        node = found
    return _strip_comments(ast.get_source_segment(src, node))


def _stream() -> str:
    return _func_source(_CHAT_EP, "stream_chat_message", "event_gen")


def _send() -> str:
    return _func_source(_CHAT_EP, "send_chat_message")


# ── stream door ───────────────────────────────────────────────────────────────

def test_the_stream_audit_starts_after_enforcement_and_before_the_notes():
    body = _stream()
    enforce = body.index("content, enforced = enforce_answer(content)")
    start = body.index("start_grounding_audit(")
    notes = body.index("finalize_answer_notes(")
    assert enforce < start < notes, (enforce, start, notes)
    assert body.count("start_grounding_audit(") == 1
    call = body[start:body.index(")", body.index("fallback=used_fallback", start))]
    assert call.split("(", 1)[1].lstrip().startswith("content,"), call
    assert "web_turn=web_search_used" in call
    assert "precomputed=" in call and "fallback_grounding_audit" in call
    assert "replay=None if used_fallback else grounding_replay" in call


def test_the_stream_hook_is_wrapped_so_it_cannot_raise_into_the_turn():
    body = _stream()
    start = body.index("start_grounding_audit(")
    before = body[:start]
    assert before.rstrip().endswith("grounding_task ="), before[-200:]
    try_at = before.rindex("try:")
    except_at = body.index("except Exception", start)
    assert try_at < start < except_at
    assert "grounding_task = None" in body[except_at:except_at + 400]


def test_non_web_tool_results_feed_the_evidence_in_the_tool_branch():
    body = _stream()
    branch = body[body.index('elif kind == "tool":'):body.index('elif kind == "widget":')]
    add = branch.index("grounding_evidence.add_tool_result(")
    guard = branch.rindex("if ", 0, add)
    assert "not _is_web" in branch[guard:add], branch[guard:add]
    assert branch.index('_is_web = payload.get("name") == WEB_SEARCH_TOOL') < guard
    # The evidence is the model's view of the result, never the full handler result.
    call = branch[add:branch.index("\n", branch.index(")", branch.index("truncate_tool_result(", add)) + 1)]
    assert "truncate_tool_result(_res)" in call, call
    assert branch.count("grounding_evidence.add_tool_result(") == 1


def test_the_evidence_is_seeded_from_prep_and_replays_are_marked():
    body = _stream()
    assert 'grounding_evidence = GroundingEvidence.from_seed(prep.get("grounding_seed"))' in body
    assert 'prep.get("deep_dive_cached")' in body[body.index("grounding_replay = ("):]
    # Bound before the try, so the hook never reads an unbound name when prep raised.
    import re
    first_try = body.index("try:")
    for name in ("grounding_evidence", "grounding_asset", "grounding_replay",
                 "fallback_grounding_audit", "grounding_task"):
        first_bind = re.search(rf"\b{name}\s*(?::[^=\n]*)?=(?!=)", body)
        assert first_bind is not None and first_bind.start() < first_try, name


def test_the_fallback_captures_its_own_audit():
    body = _stream()
    result = body.index("ai_result = _fallback_task.result()")
    capture = body.index('fallback_grounding_audit = ai_result.get("grounding_audit")')
    assert result < capture < body.index("content, enforced = enforce_answer(content)")


def test_the_task_is_settled_only_after_the_durable_write_and_the_done_frame():
    body = _stream()
    persist = body.index("_persist_turn(")
    delivered = body.index("delivered = True", persist)
    done = body.index('yield _sse("done"', delivered)
    settle = body.index("settle_grounding_audit(grounding_task)")
    assert persist < delivered < body.index("_record_memory_facts_async(", delivered) < done \
        < settle, (persist, delivered, done, settle)
    assert body.count("settle_grounding_audit(") == 1
    # Nothing is yielded after it: the settle is the generator's last statement.
    assert "yield" not in body[settle:]
    # Never a bare `await grounding_task` (cancelling the request would cancel the audit).
    assert "await grounding_task" not in body


# ── send door ─────────────────────────────────────────────────────────────────

def test_the_send_door_logs_after_the_scan_and_before_the_notes():
    body = _send()
    scan = body.index("advice_flags = scan_answer(clean_answer)")
    log = body.index("log_grounding_audit(")
    notes = body.index("finalize_answer_notes(")
    assert scan < log < notes
    call = body[log:body.index(")", body.index("context_type=", log))]
    assert 'ai_result.get("grounding_audit")' in call and 'door="send"' in call
    assert body.rindex("try:", 0, log) > scan, "the log call is inside its own try"


# ── the service half ──────────────────────────────────────────────────────────

def test_generate_response_audits_the_final_text_from_the_instruction_it_used():
    body = _func_source(_CHAT_SERVICE, "ChatService", "generate_response")
    text = body.index('ai_text = response["text"]')
    audit = body.index("self._audit_answer_numbers(")
    result = body.index("result: Dict[str, Any] = {")
    assert text < audit < result
    call = body[audit:body.index("web_used=web_used", audit)]
    assert "ai_text" in call and "used_instruction" in call
    assert '"grounding_audit": grounding_audit' in body[result:]
    # The tool-less fallback rebinds `used_instruction` to the instruction it really used.
    fallback = body[body.index("except Exception as e:", body.index("generate_with_tools")):]
    assert "used_instruction = self._build_system_instruction(" in fallback
    assert "system_instruction=used_instruction" in fallback
    # A replayed deep dive is a skip, not an audit.
    hit = body[body.index("if cached_report:"):body.index("return out")]
    assert 'GroundingAudit(skipped="cached")' in hit


def test_the_stream_prep_seeds_the_evidence_after_the_quote_line():
    body = _func_source(_CHAT_SERVICE, "ChatService", "prepare_stream_generation")
    quote = body.index("system_instruction += quote_line")
    seed = body.index('"grounding_seed": self._grounding_seed(')
    assert quote < seed


def test_the_send_audit_skips_a_web_turn_and_excludes_web_results():
    body = _func_source(_CHAT_SERVICE, "ChatService", "_audit_answer_numbers")
    assert body.index("if web_used:") < body.index("GroundingEvidence.from_seed(")
    assert "web_results_delivered(raw)" in body
    # Bounded (never an unbounded worker-thread wait inside the send budget) …
    assert "await audit_answer_bounded(answer, evidence)" in body
    assert "to_thread" not in body
    # … and the tool evidence is the model's view of each result.
    assert "evidence.add_tool_result(None, truncate_tool_result(raw))" in body


# ── behaviour of the service half ─────────────────────────────────────────────

def _stub(svc, monkeypatch, gem, tools_history=None):
    import app.services.chat_context_resolver as res
    monkeypatch.setattr(res, "get_chat_context_resolver",
                        lambda: SimpleNamespace(resolve=AsyncMock(return_value=None)))
    svc.supabase = object()
    svc.fmp = object()
    svc.gemini = gem
    svc._get_recent_messages = lambda *a, **k: list(tools_history or [])
    svc._retrieve_context = AsyncMock(return_value=([], []))
    svc._condense_history = AsyncMock(return_value="")
    svc._detect_asset_type = lambda *a, **k: "STOCK"
    svc._get_profit_summary = AsyncMock(return_value="Latest annual margins for AAPL (FY2025): Net 24.3%.")
    svc._get_snapshot_summary = AsyncMock(return_value=None)
    svc._get_company_profile_summary = AsyncMock(return_value=None)
    svc._is_deep_dive_request = lambda *a, **k: False
    svc._deterministic_widget = AsyncMock(return_value=None)


class _ToolGem:
    def __init__(self, text, tool_results):
        self.text, self.tool_results = text, tool_results

    async def generate_with_tools(self, **kw):
        return {"text": self.text, "tokens_used": 10, "tool_results": self.tool_results,
                "finish_reason": "STOP"}


@pytest.mark.asyncio
async def test_the_send_door_result_carries_the_counts(monkeypatch):
    svc = ChatService.__new__(ChatService)
    _stub(svc, monkeypatch, _ToolGem("AAPL trades at $231.50; FY2025 net margin was 24.3%; "
                                     "revenue $9.9B.", [{"current_price": 231.5}]),
          tools_history=[{"role": "assistant", "content": "earlier: revenue $9.9B"}])
    out = await svc.generate_response("sess", "how is AAPL doing?", stock_id="AAPL")
    a = out["grounding_audit"]
    assert a["skipped"] is None and a["asset"] == "STOCK"
    # $231.50 (tool), 24.3% (the profit line in the instruction), $9.9B (a prior answer only).
    assert (a["numbers"], a["grounded"], a["prior_answer_only"], a["ungrounded"]) == (3, 2, 1, 0), a


@pytest.mark.asyncio
async def test_the_send_door_answer_is_byte_identical_when_the_audit_raises(monkeypatch):
    text = "AAPL trades at $231.50; margin 41.9%."
    svc = ChatService.__new__(ChatService)
    _stub(svc, monkeypatch, _ToolGem(text, [{"current_price": 231.5}]))
    base = await svc.generate_response("sess", "q", stock_id="AAPL")

    def _boom(*a, **k):
        raise RuntimeError("audit exploded")
    # `audit_answer_bounded` resolves `audit_answer` from its own module on every call.
    import app.services.chat_numeric_grounding as g
    monkeypatch.setattr(g, "audit_answer", _boom)
    svc2 = ChatService.__new__(ChatService)
    _stub(svc2, monkeypatch, _ToolGem(text, [{"current_price": 231.5}]))
    out = await svc2.generate_response("sess", "q", stock_id="AAPL")
    assert out["content"] == base["content"] == text
    assert out["grounding_audit"]["skipped"] == "error"
    assert {k: v for k, v in out.items() if k != "grounding_audit"} == \
        {k: v for k, v in base.items() if k != "grounding_audit"}


@pytest.mark.asyncio
async def test_the_send_audit_never_reads_web_results(monkeypatch):
    web = {"web_search": True, "status": "ok", "result_count": 1,
           "results": [{"title": "x", "snippet": "$9.9B"}]}
    a = await ChatService._audit_answer_numbers("Revenue $9.9B.", {"caydex": []}, [web],
                                                web_used=True)
    assert a["skipped"] == "web_turn"
    # Even if a caller passed web_used=False, a delivered web result is never evidence.
    b = await ChatService._audit_answer_numbers("Revenue $9.9B.", {"caydex": []}, [web],
                                                web_used=False)
    assert b["ungrounded"] == 1 and b["grounded"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_results", [None, [], "junk", [None, 5, "x"], [{"v": float("nan")}]])
async def test_the_send_audit_tolerates_any_tool_results_shape(tool_results):
    a = await ChatService._audit_answer_numbers("It is 41.5.", {"caydex": ["41.5"]},
                                                tool_results, web_used=False)
    assert a["grounded"] == 1 and a["skipped"] is None


@pytest.mark.parametrize("args", [
    (None, None, None, None, None),
    ("SYS 1", "q", "not a list", 5, [None, {"chunk_text": None}]),
    ("SYS", "q", [None, {"role": "user"}, {"role": "assistant", "content": 7}], "", []),
])
def test_the_seed_builder_never_raises(args):
    seed = ChatService._grounding_seed(*args)
    assert set(seed) == {"caydex", "user", "prior_answer"}
    assert all(isinstance(v, list) for v in seed.values())


def test_the_seed_routes_each_turn_to_its_source():
    seed = ChatService._grounding_seed(
        "SYS", "now?", [{"role": "user", "content": "u1"}, {"role": "assistant", "content": "a1"}],
        "SUMMARY", [{"chunk_text": "filing"}],
    )
    assert seed == {"caydex": ["SYS", "filing"], "user": ["u1", "now?"],
                    "prior_answer": ["a1", "SUMMARY"]}


# ── fix round (2026-10-08): the model's view, and a bounded inline wait ───────────────────────

def _long_chart(n=600):
    """A result the model only ever sees PRUNED (`truncate_tool_result` keeps the list head)."""
    return {"symbol": "AAPL", "points": [{"date": "2026-01-02", "close": round(100 + i * 0.37, 2)}
                                         for i in range(n)]}


def test_the_long_chart_really_is_pruned_for_the_model():
    from app.integrations.gemini import truncate_tool_result
    result = _long_chart()
    seen = truncate_tool_result(result)
    closes = {p["close"] for p in seen["points"]}
    assert result["points"][0]["close"] in closes
    assert result["points"][-1]["close"] not in closes, "precondition: the tail is pruned"
    assert seen.get("_truncated") is True


@pytest.mark.asyncio
async def test_the_send_audit_reads_only_what_the_model_saw():
    result = _long_chart()
    head, tail = result["points"][0]["close"], result["points"][-1]["close"]
    a = await ChatService._audit_answer_numbers(
        f"It closed at ${head:.2f}, and later at ${tail:.2f}.", {"caydex": []}, [result],
        web_used=False,
    )
    assert (a["grounded"], a["ungrounded"]) == (1, 1), a


@pytest.mark.asyncio
async def test_a_busy_worker_pool_cannot_hold_the_send_answer(monkeypatch):
    """The send door audits inside `generate_response`, under its `CHAT_SEND_BUDGET_SECONDS`:
    a stuck worker-thread queue costs at most `INLINE_AUDIT_SECONDS`, logged as a skip, and the
    answer is the same bytes."""
    import threading
    import time
    import app.services.chat_numeric_grounding as g
    text = "AAPL trades at $231.50; margin 41.9%."
    svc = ChatService.__new__(ChatService)
    _stub(svc, monkeypatch, _ToolGem(text, [{"current_price": 231.5}]))
    base = await svc.generate_response("sess", "q", stock_id="AAPL")

    gate = threading.Event()

    def _stuck(*a, **k):
        gate.wait(5)
        return g.GroundingAudit(numbers=1, grounded=1)

    monkeypatch.setattr(g, "audit_answer", _stuck)
    monkeypatch.setattr(g, "INLINE_AUDIT_SECONDS", 0.05)
    svc2 = ChatService.__new__(ChatService)
    _stub(svc2, monkeypatch, _ToolGem(text, [{"current_price": 231.5}]))
    t0 = time.monotonic()
    try:
        out = await svc2.generate_response("sess", "q", stock_id="AAPL")
    finally:
        gate.set()
    assert time.monotonic() - t0 < 2.0
    assert out["grounding_audit"]["skipped"] == "timeout"
    assert out["content"] == base["content"] == text
    assert {k: v for k, v in out.items() if k != "grounding_audit"} == \
        {k: v for k, v in base.items() if k != "grounding_audit"}
