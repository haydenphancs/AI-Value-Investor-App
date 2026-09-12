"""ONE real request through `POST /chat/sessions/{id}/messages/stream` (2026-09-11).

WHY THIS FILE EXISTS
--------------------
34 `test_chat_*.py` files and not one of them invoked the 600-line SSE generator: every test
called a private helper or AST-scanned the source. Frame ORDER, the settlement ORDER (persist →
refund decision → credits frame → done) and the replay branches were pinned by structure only.
This drives the handler through FastAPI's TestClient with every collaborator stubbed at its
seam — no Gemini, no Supabase, no FMP — and asserts on the frames the client actually sees.

Stubs (each at the binding the handler resolves, per .claude/rules/testing.md):
  * `get_chat_identity` / `get_supabase` / the `ChatRateLimit` checker → dependency overrides;
  * `chat._claim_chat_quota` → a recording quota (refunds, delivered);
  * `chat.route_question` → a fixed single/general route;
  * `app.services.chat_service.ChatService` (imported INSIDE event_gen) → a fake whose
    `gemini.stream_agentic` yields scripted thought/answer/tool events;
  * `app.services.chat_starter_warm_service.lookup` → miss (or a tripwire);
  * the reader-lens / memory / token-accounting helpers → no-ops.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List

import pytest
from fastapi.testclient import TestClient

import app.api.v1.endpoints.chat as chat_mod
from app.database import get_supabase
from app.dependencies import ChatRateLimit, get_chat_identity
from app.main import app

_USER = {"id": "11111111-1111-1111-1111-111111111111", "is_guest": False, "tier": "pro",
         "email": "t@example.com"}
_SESSION = "22222222-2222-2222-2222-222222222222"


# ── a permissive fluent Supabase fake ────────────────────────────────────────

class _Result:
    def __init__(self, data):
        self.data = data


class _Query:
    def __init__(self, db: "_FakeDB", table: str):
        self.db, self.table, self.op, self.payload = db, table, "select", None

    def __getattr__(self, name):
        # eq / neq / in_ / gte / lte / order / limit / range / single / maybe_single / is_
        def _chain(*a, **k):
            return self
        return _chain

    def insert(self, rows):
        self.op, self.payload = "insert", rows
        return self

    def update(self, patch):
        self.op, self.payload = "update", patch
        return self

    def upsert(self, rows, **k):
        self.op, self.payload = "upsert", rows
        return self

    def execute(self):
        self.db.calls.append((self.table, self.op, self.payload))
        if self.table == "chat_sessions" and self.op == "select":
            return _Result(dict(self.db.session_row))
        if self.table == "chat_messages" and self.op == "insert":
            out = []
            for i, row in enumerate(self.payload if isinstance(self.payload, list) else [self.payload]):
                out.append({**row, "id": f"msg-{len(self.db.calls)}-{i}"})
            self.db.inserted_messages.extend(out)
            return _Result(out)
        return _Result([])


class _FakeDB:
    def __init__(self, session_row: Dict[str, Any]):
        self.session_row = session_row
        self.calls: List[tuple] = []
        self.inserted_messages: List[Dict[str, Any]] = []

    def table(self, name: str) -> _Query:
        return _Query(self, name)


# ── the quota + chat service fakes ───────────────────────────────────────────

class _Quota:
    def __init__(self):
        self.refunds: List[str] = []      # `refund_once`: the turn never arrived
        self.settled: List[str] = []      # `settle_no_cost`: delivered, but cost nothing
        self.delivered = 0

    def refund_once(self, reason: str) -> None:
        self.refunds.append(reason)

    def settle_no_cost(self, reason: str) -> None:
        self.settled.append(reason)

    def on_delivered(self) -> None:
        self.delivered += 1

    def cost_frame(self) -> Dict[str, Any]:
        gone = self.refunds or self.settled
        return {"outcome": "refunded" if gone else "charged",
                "credits": 0 if gone else 1, "balance": 41}

    def cost_payload(self):
        return None

    @property
    def charged(self) -> bool:
        return not (self.refunds or self.settled)


def _events(*evs):
    async def _gen(*a, **k):
        for e in evs:
            yield e
    return _gen


class _FakeGemini:
    def __init__(self, events):
        self._events = events
        self.stream_calls: List[Dict[str, Any]] = []
        self.model_name = "gemini-2.5-flash"

    def stream_agentic(self, prompt, **kwargs):
        self.stream_calls.append({"prompt": prompt, **kwargs})
        return _events(*self._events)()


class _FakeChatService:
    prep_overrides: Dict[str, Any] = {}
    events: List[Any] = []
    instances: List["_FakeChatService"] = []
    # The stream→non-stream fallback (`generate_response`). None → the fallback raises, as
    # the original fake did; a dict → returned. `synthesis_signal` makes a fake
    # `stream_synthesis` set `signals["degraded"]` before streaming `events`.
    fallback_result: Any = None
    synthesis_signal: Any = None
    synthesis_raises: bool = False

    def __init__(self):
        self.gemini = _FakeGemini(list(type(self).events))
        self.prep_calls: List[Dict[str, Any]] = []
        self.suggestion_calls = 0
        self.fallback_calls = 0
        type(self).instances.append(self)

    async def generate_response(self, **kw):
        self.fallback_calls += 1
        if type(self).fallback_result is None:
            raise RuntimeError("no fallback configured")
        return dict(type(self).fallback_result)

    def stream_synthesis(self, prep, user_message, route, tools, handlers, signals=None):
        cls = type(self)
        if signals is not None and cls.synthesis_signal:
            signals["degraded"] = cls.synthesis_signal

        async def _gen():
            if cls.synthesis_raises:
                raise RuntimeError("merge exploded")
            for e in cls.events:
                yield e
        return _gen()

    async def prepare_stream_generation(self, **kw):
        self.prep_calls.append(kw)
        prep = {
            "prompt": "PROMPT", "system_instruction": "SYS", "citations": None,
            "widget": None, "sources": None, "asset_type": "NORMAL",
            "is_deep_dive": False, "deep_dive_cached": None, "grounded": False,
            "server_grounded": False,
        }
        prep.update(type(self).prep_overrides)
        return prep

    async def generate_followup_suggestions(self, *a, **k):
        self.suggestion_calls += 1
        return ["What about its margins?", "How does it compare to peers?"]

    def _upsert_deep_dive_cache(self, *a, **k):
        pass

    @staticmethod
    def _chat_symbol(raw):
        return str(raw or "").upper()


def _parse_sse(text: str) -> List[tuple[str, Dict[str, Any]]]:
    frames = []
    for chunk in text.strip().split("\n\n"):
        ev = data = None
        for line in chunk.splitlines():
            if line.startswith("event: "):
                ev = line[7:]
            elif line.startswith("data: "):
                data = json.loads(line[6:])
        if ev:
            frames.append((ev, data))
    return frames


@pytest.fixture
def harness(monkeypatch):
    """Wire every seam; yield (client, db, quota, warm_calls)."""
    db = _FakeDB({
        "id": _SESSION, "user_id": _USER["id"], "session_type": "NORMAL", "stock_id": None,
        "context_type": None, "reference_id": None, "context_snapshot": None,
        "memory_summary": None, "memory_summary_upto": None, "message_count": 0,
    })
    quota = _Quota()
    warm_calls: List[str] = []

    app.dependency_overrides[get_chat_identity] = lambda: _USER
    app.dependency_overrides[get_supabase] = lambda: db
    app.dependency_overrides[ChatRateLimit.dependency] = lambda: None

    monkeypatch.setattr(chat_mod, "_claim_chat_quota", lambda *a, **k: (quota, None))
    # `route_question` is imported INSIDE event_gen from chat_router, so patch that module.
    import app.services.agents.chat_router as router
    # A REAL specialist, so the `routing` frame is emitted (a general route sends none) and
    # `apply_specialist` appends a lens to the system instruction.
    monkeypatch.setattr(router, "route_question",
                        _async_value({"specialists": ["valuation"], "mode": "single",
                                      "labels": ["Valuation"], "degraded": False}))
    monkeypatch.setattr(chat_mod, "_reader_lens_for_async", _async_value(None))
    monkeypatch.setattr(chat_mod, "_record_memory_facts_async", _async_value(None))
    monkeypatch.setattr(chat_mod, "_record_chat_tokens", lambda *a, **k: None)
    monkeypatch.setattr(chat_mod, "_persist_context_snapshot", lambda *a, **k: None)

    import app.services.chat_service as cs
    import app.services.chat_starter_warm_service as warm
    _FakeChatService.instances = []
    _FakeChatService.prep_overrides = {}
    _FakeChatService.fallback_result = None
    _FakeChatService.synthesis_signal = None
    _FakeChatService.synthesis_raises = False
    _FakeChatService.events = [("thought", "Let me check."), ("answer", "Apple is "),
                               ("answer", "doing fine.")]
    monkeypatch.setattr(cs, "ChatService", _FakeChatService)

    async def _lookup(q):
        warm_calls.append(q)
        return None
    monkeypatch.setattr(warm, "lookup", _lookup)

    client = TestClient(app)  # no context manager: the lifespan must not run
    try:
        yield client, db, quota, warm_calls
    finally:
        app.dependency_overrides.pop(get_chat_identity, None)
        app.dependency_overrides.pop(get_supabase, None)
        app.dependency_overrides.pop(ChatRateLimit.dependency, None)


def _async_value(value):
    async def _f(*a, **k):
        return value
    return _f


def _post(client: TestClient, message: str = "How is Apple doing?", **body):
    return client.post(
        f"/api/v1/chat/sessions/{_SESSION}/messages/stream",
        json={"message": message, **body},
        headers={"Authorization": "Bearer test"},
    )


# ── the frames a real turn produces, in order ────────────────────────────────

def test_a_streamed_turn_emits_the_documented_frame_order_and_persists_once(harness):
    client, db, quota, _ = harness
    r = _post(client)
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("text/event-stream")

    frames = _parse_sse(r.text)
    names = [f[0] for f in frames]
    assert names[0] == "meta"
    assert names[-1] == "done"
    assert names[-2] == "credits", "the credits frame lands right before done"
    assert "routing" in names and "reasoning" in names and "token" in names
    assert names.index("routing") < names.index("token")
    assert names.index("suggestions") < names.index("credits")

    meta = frames[0][1]
    assert meta["session_id"] == _SESSION and meta["user_message"] == "How is Apple doing?"

    routing = [d for e, d in frames if e == "routing"][0]
    assert routing.get("labels") == ["Valuation"] or "Valuation" in json.dumps(routing)

    done = frames[-1][1]["message"]
    assert done["role"] == "assistant"
    assert done["content"].startswith("Apple is doing fine.")
    # The specialist lens reached the model: apply_specialist appends it to the system prompt.
    sent = _FakeChatService.instances[-1].gemini.stream_calls[0]
    assert "VALUATION lens" in sent["system_instruction"]
    assert done["suggestions"] == ["What about its margins?", "How does it compare to peers?"]

    # Exactly ONE two-row insert, user first.
    inserts = [c for c in db.calls if c[0] == "chat_messages" and c[1] == "insert"]
    assert len(inserts) == 1 and [r["role"] for r in inserts[0][2]] == ["user", "assistant"]
    assert quota.refunds == [] and quota.delivered == 1


def test_a_normal_turn_is_charged_not_refunded(harness):
    client, db, quota, _ = harness
    r = _post(client)
    credits = [d for e, d in _parse_sse(r.text) if e == "credits"][0]
    assert credits["outcome"] == "charged" and credits["credits"] == 1
    assert quota.refunds == []


# ── the settlement branches this file exists to pin ───────────────────────────

def test_a_streamed_deep_dive_cache_hit_is_refunded(harness):
    """The replay branch never set tokens_used, so the `== 0` refund gate was dead on the
    default streaming path while the non-streaming endpoint refunded the same hit."""
    client, db, quota, _ = harness
    db.session_row.update({"stock_id": "SPY", "context_type": "ETF", "reference_id": "SPY"})
    _FakeChatService.prep_overrides = {
        "asset_type": "ETF", "is_deep_dive": True,
        "deep_dive_cached": "CACHED BRIEF " * 30,
    }
    r = _post(client, "Give me a deep dive on SPY", context="SPY $500 +1%")
    assert r.status_code == 200, r.text
    frames = _parse_sse(r.text)
    # A DELIVERED no-cost turn settles through `settle_no_cost` (never re-grants a free
    # follow-up), not through the never-arrived `refund_once`.
    assert quota.settled == ["chat_cache_hit"] and quota.refunds == [], (quota.settled, quota.refunds)
    credits = [d for e, d in frames if e == "credits"][0]
    assert credits["outcome"] == "refunded" and credits["credits"] == 0
    # Zero Gemini: the fake's stream_agentic was never called.
    assert _FakeChatService.instances[-1].gemini.stream_calls == []
    assert frames[-1][1]["message"]["content"].startswith("CACHED BRIEF")


def test_a_grounded_session_never_consults_the_warm_starter_cache(harness):
    """Warmed rows are written by the GLOBAL chat with no screen behind them; the same words
    inside a stock session must not replay them and discard the grounding."""
    client, db, quota, warm_calls = harness
    db.session_row.update({"stock_id": "AAPL", "context_type": "STOCK", "reference_id": "AAPL"})
    _FakeChatService.prep_overrides = {"asset_type": "STOCK", "grounded": True}
    r = _post(client, "What's hot today?", context="AAPL $230 +2%")
    assert r.status_code == 200
    assert warm_calls == [], "warm lookup must be skipped for a grounded turn"


def test_an_ungrounded_global_turn_does_consult_the_warm_cache(harness):
    client, db, quota, warm_calls = harness
    r = _post(client, "What's hot today?")
    assert r.status_code == 200
    assert warm_calls == ["What's hot today?"]


def test_a_bad_session_is_a_json_404_not_an_sse_frame(harness):
    client, db, quota, _ = harness

    class _NoRow(_FakeDB):
        def table(self, name):
            q = super().table(name)
            if name == "chat_sessions":
                q.execute = lambda: _Result(None)
            return q

    app.dependency_overrides[get_supabase] = lambda: _NoRow(db.session_row)
    r = _post(client)
    assert r.status_code == 404
    assert quota.refunds == [] and quota.delivered == 0


# ── settlement parity between the two doors ───────────────────────────────────

def _route_synthesize(monkeypatch):
    import app.services.agents.chat_router as router
    monkeypatch.setattr(router, "route_question",
                        _async_value({"specialists": ["valuation", "macro"], "mode": "synthesize",
                                      "labels": ["Valuation", "Macro"], "degraded": False}))


def test_a_degraded_fallback_answer_is_settled_no_cost(harness):
    """Stream dies → `generate_response` answers with `degraded="no_tools"` (function calling
    failed, plain text with none of its live data). The non-streaming door refunds exactly
    this result; the stream door used to charge it AND grant a free follow-up."""
    client, db, quota, _ = harness
    _FakeChatService.events = [("answer", "partial…")]
    _FakeChatService.fallback_result = {"content": "Plain answer from memory. " * 3,
                                        "tokens_used": 30, "degraded": "no_tools"}
    # Make the stream raise after one token so the fallback runs.
    async def _boom(*a, **k):
        yield ("answer", "partial…")
        raise RuntimeError("stream died")
    monkeypatch_gemini = _FakeChatService.instances  # (instances are created per request)
    orig = _FakeGemini.stream_agentic
    _FakeGemini.stream_agentic = lambda self, prompt, **kw: _boom()
    try:
        r = _post(client)
    finally:
        _FakeGemini.stream_agentic = orig
    assert r.status_code == 200, r.text
    frames = _parse_sse(r.text)
    assert "reset" in [f[0] for f in frames], "the fallback replaces streamed tokens"
    assert quota.settled == ["chat_degraded_no_tools"], (quota.settled, quota.refunds)
    assert quota.refunds == []
    assert frames[-1][1]["message"]["content"].startswith("Plain answer")


def test_a_healthy_fallback_answer_stays_charged(harness):
    """The fallback answered the same prompt at higher cost — not a refund shape."""
    client, db, quota, _ = harness
    _FakeChatService.fallback_result = {"content": "Full healthy answer. " * 3, "tokens_used": 80}
    orig = _FakeGemini.stream_agentic
    async def _boom(*a, **k):
        raise RuntimeError("stream died before any token")
        yield  # pragma: no cover
    _FakeGemini.stream_agentic = lambda self, prompt, **kw: _boom()
    try:
        r = _post(client)
    finally:
        _FakeGemini.stream_agentic = orig
    assert r.status_code == 200, r.text
    assert quota.settled == [] and quota.refunds == [] and quota.delivered == 1


def test_a_stale_no_specialists_signal_does_not_refund_a_healthy_fallback(harness, monkeypatch):
    """`stream_synthesis` sets `no_specialists` BEFORE its rescue run; if that rescue then
    raises, the stale marker must not survive into settlement and refund a full fallback."""
    client, db, quota, _ = harness
    _route_synthesize(monkeypatch)
    _FakeChatService.synthesis_signal = "no_specialists"
    _FakeChatService.synthesis_raises = True
    _FakeChatService.fallback_result = {"content": "Full healthy answer. " * 3, "tokens_used": 80}
    r = _post(client)
    assert r.status_code == 200, r.text
    assert quota.settled == [] and quota.delivered == 1


def test_an_unmerged_synthesis_is_settled_no_cost(harness, monkeypatch):
    client, db, quota, _ = harness
    _route_synthesize(monkeypatch)
    _FakeChatService.synthesis_signal = "unmerged"
    r = _post(client)
    assert r.status_code == 200, r.text
    assert quota.settled == ["chat_degraded_unmerged"]


def test_every_tool_failing_on_a_streamed_turn_is_settled_no_cost(harness):
    """Single-mode agentic turn whose only tool call came back `{error}`: the answer has
    none of its live data — the same `no_tools` shape the non-streaming door refunds."""
    client, db, quota, _ = harness
    _FakeChatService.events = [
        ("thought", "Checking the chart."),
        ("tool", {"name": "get_stock_chart_data", "args": {"ticker": "AAPL"},
                  "result": {"error": "timed_out", "tool": "get_stock_chart_data"}}),
        ("answer", "Apple is doing fine, from memory."),
    ]
    r = _post(client)
    assert r.status_code == 200, r.text
    frames = _parse_sse(r.text)
    step = [d for e, d in frames if e == "tool_step"][0]
    assert step["name"] == "get_stock_chart_data" and step["error"] == "timed_out"
    assert quota.settled == ["chat_degraded_no_tools"]


def test_a_partial_tool_failure_on_a_streamed_turn_stays_charged(harness):
    client, db, quota, _ = harness
    _FakeChatService.events = [
        ("tool", {"name": "get_ticker_news", "args": {"ticker": "AAPL"},
                  "result": {"error": "timed_out"}}),
        ("tool", {"name": "get_stock_chart_data", "args": {"ticker": "AAPL"},
                  "result": {"widget_type": "stock_chart", "ticker": "AAPL"}}),
        ("answer", "Apple is doing fine."),
    ]
    r = _post(client)
    assert r.status_code == 200, r.text
    assert quota.settled == [] and quota.delivered == 1
    done = _parse_sse(r.text)[-1][1]["message"]
    assert (done.get("widgets") or [done.get("widget")])[0]["widget_type"] == "stock_chart"


def test_a_quiet_stream_carries_keepalive_comments(harness, monkeypatch):
    """A synthesis round buffers its specialists and a grounded search may run ~75 s; iOS
    drops the stream after 120 s of silence. Comment frames reset that clock."""
    import asyncio as _aio
    from app.config import settings
    client, db, quota, _ = harness
    monkeypatch.setattr(settings, "CHAT_STREAM_KEEPALIVE_SECONDS", 0.02)

    async def _slow(*a, **k):
        yield ("thought", "thinking…")
        await _aio.sleep(0.12)
        yield ("answer", "Apple is doing fine.")
    orig = _FakeGemini.stream_agentic
    _FakeGemini.stream_agentic = lambda self, prompt, **kw: _slow()
    try:
        r = _post(client)
    finally:
        _FakeGemini.stream_agentic = orig
    assert r.status_code == 200, r.text
    assert r.text.count(": keepalive") >= 2, r.text[:400]
    # Comment lines are invisible to the frame parser: the documented order is intact.
    names = [f[0] for f in _parse_sse(r.text)]
    assert names[0] == "meta" and names[-1] == "done" and "token" in names
    assert quota.settled == [] and quota.delivered == 1


def test_the_non_streaming_door_settles_a_degraded_answer_no_cost(harness):
    """POST /messages — the client's stream-failure retry — must settle the same
    `no_tools` result the same way, or the two doors price one answer differently."""
    client, db, quota, _ = harness
    _FakeChatService.fallback_result = {"content": "Plain answer from memory. " * 3,
                                        "tokens_used": 30, "degraded": "no_tools"}
    r = client.post(
        f"/api/v1/chat/sessions/{_SESSION}/messages",
        json={"message": "How is Apple doing?"},
        headers={"Authorization": "Bearer test"},
    )
    assert r.status_code == 200, r.text
    assert quota.settled == ["chat_degraded_no_tools"] and quota.refunds == []
    assert r.json()["content"].startswith("Plain answer")


def test_a_warm_hit_whose_turn_fell_back_does_not_reuse_the_stored_chips(harness, monkeypatch):
    """The stored chips belong to the stored answer; a fallback answered differently."""
    import app.services.chat_starter_warm_service as warm
    client, db, quota, _ = harness

    async def _hit(q):
        return {"answer": "Warm answer " * 20, "suggestions": ["Warm chip one?", "Warm chip two?"],
                "widget": None}
    monkeypatch.setattr(warm, "lookup", _hit)
    _FakeChatService.fallback_result = {"content": "Live fallback answer. " * 3, "tokens_used": 80}
    # Make the replay itself blow up before it streams, so the fallback runs.
    monkeypatch.setattr(chat_mod, "_replay_cached_answer",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("replay died")))
    r = _post(client, message="What's hot today?")
    assert r.status_code == 200, r.text
    done = _parse_sse(r.text)[-1][1]["message"]
    assert done["content"].startswith("Live fallback")
    assert done["suggestions"] != ["Warm chip one?", "Warm chip two?"]
