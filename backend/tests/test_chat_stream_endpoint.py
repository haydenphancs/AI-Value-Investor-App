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

    async def refresh_widget(self, widget):
        # The real one re-fetches a stored card by symbol; tests replace it per case.
        return widget

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
                  "result": {"error": "timed_out", "tool": "get_stock_chart_data", "upstream": True}}),
        ("answer", "Apple is doing fine, from memory."),
    ]
    r = _post(client)
    assert r.status_code == 200, r.text
    frames = _parse_sse(r.text)
    step = [d for e, d in frames if e == "tool_step"][0]
    assert step["name"] == "get_stock_chart_data" and step["error"] == "timed_out"
    assert quota.settled == ["chat_degraded_no_tools"]


@pytest.mark.parametrize("result", [
    {"error": "invalid or missing ticker"},                       # _INVALID_TICKER
    {"error": "No quote data found for QQQQQ"},                   # a symbol FMP does not cover
    {"error": "unknown tool: get_secret_data"},                   # a hallucinated tool
], ids=["invalid-ticker", "unknown-symbol", "unknown-tool"])
def test_a_tool_error_the_model_shaped_stays_charged(harness, result):
    """The `no_tools` refund was farmable: 'Explain P/E, and pull the chart for QQQQQ' made
    the model call a tool that answered `{error}`, every tool 'failed', the turn was
    refunded — and the P/E answer was delivered. At 15/min that is unlimited free Gemini
    with the balance never moving. Only an UPSTREAM failure (tagged at the source) refunds."""
    client, db, quota, _ = harness
    _FakeChatService.events = [
        ("tool", {"name": "get_stock_chart_data", "args": {"ticker": "QQQQQ"}, "result": result}),
        ("answer", "A P/E ratio is price over earnings. I could not chart QQQQQ."),
    ]
    r = _post(client, message="Explain what a P/E ratio is, and pull the chart for QQQQQ")
    assert r.status_code == 200, r.text
    assert quota.settled == [] and quota.delivered == 1, quota.settled
    step = [d for e, d in _parse_sse(r.text) if e == "tool_step"][0]
    assert step["error"] == result["error"], "the frame still shows the model its miss"


def test_a_cut_answer_is_settled_as_degraded_and_never_cached(harness, monkeypatch):
    """MAX_TOKENS / SAFETY after real text used to end cleanly: charged in full and, for a
    deep dive, cached for every user for 24 h with its last sentence missing."""
    client, db, quota, _ = harness
    _FakeChatService.prep_overrides = {"is_deep_dive": True, "deep_dive_context": "ctx",
                                       "deep_dive_cached": None}
    writes = []
    monkeypatch.setattr(_FakeChatService, "_upsert_deep_dive_cache",
                        lambda self, *a, **k: writes.append(a))
    _FakeChatService.events = [
        ("answer", "SPY is up 1.2% today because the Fed will likely **"),
        ("finish", "MAX_TOKENS"),
    ]
    db.session_row["stock_id"] = "SPY"
    r = _post(client, message="Give me a deep dive on SPY")
    assert r.status_code == 200, r.text
    assert quota.settled == ["chat_degraded_truncated"]
    assert writes == [], "a cut brief must never enter the shared cache"
    done = _parse_sse(r.text)[-1][1]["message"]
    assert done["content"].startswith("SPY is up 1.2%")


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


def test_the_stream_is_never_gzip_buffered_for_a_gzip_accepting_client(harness):
    """`GZipMiddleware` is registered app-wide (main.py) and Starlette's streaming gzip path
    never flushes zlib between chunks, so for any client that advertises gzip — iOS's
    URLSession does by default — every frame and every keepalive sat in the compressor until
    the generator closed. Measured on prod 2026-09-12: `meta` arrived at the END of a 14 s
    turn; declining gzip made the same turn stream from 0.56 s. The endpoint declares
    `Content-Encoding: identity`, which the middleware passes through untouched.

    TestClient runs the whole app before returning, so this drives the REAL ASGI app (with
    the harness's overrides) and records every `http.response.body` message: the first
    non-empty body must be plain `event: meta` text — not the gzip magic bytes — and the
    frames must arrive as separate messages (one per yield), which is what "streaming"
    means to the client."""
    import asyncio as _aio
    client, db, quota, _ = harness
    body = json.dumps({"message": "How is Apple doing?"}).encode()
    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "POST",
        "scheme": "http", "path": f"/api/v1/chat/sessions/{_SESSION}/messages/stream",
        "raw_path": f"/api/v1/chat/sessions/{_SESSION}/messages/stream".encode(),
        "query_string": b"", "root_path": "", "server": ("testserver", 80), "client": ("127.0.0.1", 1),
        "headers": [
            (b"host", b"testserver"), (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode()), (b"accept", b"text/event-stream"),
            (b"accept-encoding", b"gzip, deflate, br"),
        ],
    }
    sent: List[Dict[str, Any]] = []
    delivered = {"body": False}

    async def receive():
        if delivered["body"]:
            await _aio.sleep(3600)  # no disconnect: the app must finish on its own
        delivered["body"] = True
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message):
        sent.append(message)

    _aio.run(app(scope, receive, send))
    start = next(m for m in sent if m["type"] == "http.response.start")
    assert start["status"] == 200, start
    headers = {k.decode().lower(): v.decode() for k, v in start["headers"]}
    assert headers.get("content-encoding") == "identity", headers
    bodies = [m.get("body", b"") for m in sent if m["type"] == "http.response.body"]
    non_empty = [b for b in bodies if b]
    assert non_empty[0][:2] != b"\x1f\x8b", "first body chunk is a gzip header"
    assert non_empty[0].startswith(b"event: meta\n"), non_empty[0][:80]
    # One ASGI message per yielded frame — the pump did not collapse into a single flush.
    assert len(non_empty) >= 5, len(non_empty)
    text = b"".join(bodies).decode()
    names = [f[0] for f in _parse_sse(text)]
    assert names[0] == "meta" and names[-1] == "done" and "token" in names
    assert quota.delivered == 1


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


def _warm_hit(monkeypatch, *, widget, stale):
    import app.services.chat_starter_warm_service as warm

    async def _hit(q):
        return {"answer": "Warm answer " * 20, "suggestions": [], "widget": widget,
                "widget_stale": stale}
    monkeypatch.setattr(warm, "lookup", _hit)


def _done_widgets(r):
    done = _parse_sse(r.text)[-1][1]["message"]
    return done.get("widgets") or ([done["widget"]] if done.get("widget") else [])


_WARM_CARD = {"widget_type": "stock_chart", "ticker": "NVDA", "current_price": 100.0,
              "is_market_open": True}


def test_a_stale_warm_card_is_re_fetched_by_symbol_before_it_is_replayed(harness, monkeypatch):
    """A starter answer warmed at 10:15 carried a 10:15 price under a green Live dot; tapped
    at 15:50 the card must be this minute's, fetched the way a live turn fetches it."""
    client, db, quota, _ = harness
    _warm_hit(monkeypatch, widget=dict(_WARM_CARD), stale=True)
    seen = []

    async def _refresh(self, w):
        seen.append(w)
        return {**w, "current_price": 123.45}
    monkeypatch.setattr(_FakeChatService, "refresh_widget", _refresh)
    r = _post(client, message="What's hot today?")
    assert r.status_code == 200, r.text
    assert seen == [_WARM_CARD]
    assert _done_widgets(r) == [{**_WARM_CARD, "current_price": 123.45}]


def test_a_stale_warm_card_whose_refresh_fails_is_dropped_not_replayed(harness, monkeypatch):
    client, db, quota, _ = harness
    _warm_hit(monkeypatch, widget=dict(_WARM_CARD), stale=True)

    async def _refresh(self, w):
        return None
    monkeypatch.setattr(_FakeChatService, "refresh_widget", _refresh)
    r = _post(client, message="What's hot today?")
    assert r.status_code == 200, r.text
    assert _done_widgets(r) == [], "a stale price must never be replayed as live"
    assert quota.delivered == 1, "the answer itself is still served and charged"


def test_a_fresh_warm_card_is_replayed_without_a_re_fetch(harness, monkeypatch):
    client, db, quota, _ = harness
    _warm_hit(monkeypatch, widget=dict(_WARM_CARD), stale=False)

    async def _refresh(self, w):
        raise AssertionError("a fresh card must not be re-fetched")
    monkeypatch.setattr(_FakeChatService, "refresh_widget", _refresh)
    r = _post(client, message="What's hot today?")
    assert r.status_code == 200, r.text
    assert _done_widgets(r) == [_WARM_CARD]


# ── 2026-09-16: transient-aware session lookup, bounded history, precharge compensation ──

def _raising_db(base, table_name, exc):
    class _Raising(_FakeDB):
        def table(self, name):
            q = super().table(name)
            if name == table_name:
                def _boom():
                    raise exc
                q.execute = _boom
            return q
    return _Raising(base.session_row)


def _gateway_520():
    from tests.test_supabase_transient_classifier import gateway_error
    return gateway_error(520)


def _pgrst(code):
    from tests.test_supabase_transient_classifier import postgrest_error
    return postgrest_error(code)


@pytest.mark.parametrize("door", ["stream", "send"])
def test_a_supabase_edge_error_on_the_session_lookup_is_a_409_not_a_404(harness, door):
    """A Cloudflare 520 on the lookup used to read as "session not found" on both doors and
    on the history GET — iOS's reconcile took that as "not persisted" and re-POSTed a turn
    the server had already saved and charged."""
    client, db, quota, _ = harness
    app.dependency_overrides[get_supabase] = lambda: _raising_db(db, "chat_sessions", _gateway_520())
    if door == "stream":
        r = _post(client)
    else:
        r = client.post(f"/api/v1/chat/sessions/{_SESSION}/messages", json={"message": "hi"})
    assert r.status_code == 409, r.text
    assert r.json()["error_code"] == "SYSTEM_BUSY"
    assert quota.delivered == 0 and quota.refunds == []


@pytest.mark.parametrize("code", ["PGRST116", "23505"])
def test_a_deterministic_lookup_failure_stays_a_404(harness, code):
    client, db, quota, _ = harness
    app.dependency_overrides[get_supabase] = lambda: _raising_db(db, "chat_sessions", _pgrst(code))
    assert _post(client).status_code == 404


def test_the_history_oracle_answers_409_on_a_transient_failure_and_reads_the_newest_rows(harness):
    client, db, quota, _ = harness
    app.dependency_overrides[get_supabase] = lambda: _raising_db(db, "chat_sessions", _gateway_520())
    r = client.get(f"/api/v1/chat/sessions/{_SESSION}")
    assert r.status_code == 409 and r.json()["error_code"] == "SYSTEM_BUSY"

    # The messages read is bounded and DESC so the tail is always present.
    class _Recording(_FakeDB):
        def table(self, name):
            q = super().table(name)
            if name == "chat_messages":
                rec = self.calls
                def order(col, desc=False):
                    rec.append(("chat_messages", "order", (col, desc))); return q
                def limit(n):
                    rec.append(("chat_messages", "limit", n)); return q
                q.order = order
                q.limit = limit
                q.execute = lambda: _Result([
                    {"id": "m2", "session_id": _SESSION, "role": "assistant", "content": "newest",
                     "created_at": "2026-09-16T00:00:02+00:00", "rich_content": None,
                     "citations": None, "tokens_used": None},
                    {"id": "m1", "session_id": _SESSION, "role": "user", "content": "q",
                     "created_at": "2026-09-16T00:00:01+00:00", "rich_content": None,
                     "citations": None, "tokens_used": None},
                ])
            return q
    rdb = _Recording({**db.session_row, "id": _SESSION, "created_at": "2026-09-16T00:00:00+00:00",
                      "updated_at": "2026-09-16T00:00:00+00:00", "title": None, "is_saved": False,
                      "message_count": 2})
    app.dependency_overrides[get_supabase] = lambda: rdb
    r = client.get(f"/api/v1/chat/sessions/{_SESSION}")
    assert r.status_code == 200, r.text
    assert ("chat_messages", "order", ("created_at", True)) in rdb.calls
    assert ("chat_messages", "limit", chat_mod.CHAT_HISTORY_PAGE_ROWS) in rdb.calls
    msgs = r.json()["messages"]
    assert [m["content"] for m in msgs] == ["q", "newest"], "rows must come back oldest→newest"


def test_a_degraded_deep_dive_is_never_written_to_the_cache(harness, monkeypatch):
    """A tool-less / unmerged brief is refunded — caching it would replay it for 24 h as a
    zero-cost hit ("cached failure ≡ real answer")."""
    client, db, quota, _ = harness
    _route_synthesize(monkeypatch)
    _FakeChatService.prep_overrides = {"is_deep_dive": True, "deep_dive_context": "ctx",
                                       "deep_dive_cached": None}
    _FakeChatService.synthesis_signal = "unmerged"
    writes = []
    monkeypatch.setattr(_FakeChatService, "_upsert_deep_dive_cache",
                        lambda self, *a, **k: writes.append(a))
    db.session_row["stock_id"] = "SPY"
    r = _post(client, message="Give me a market deep dive on SPY")
    assert r.status_code == 200
    assert quota.settled == ["chat_degraded_unmerged"], quota.settled
    assert writes == [], "a degraded brief was cached"


def test_an_unconfirmed_precharge_is_compensated_with_the_turn_ref(monkeypatch):
    """`spend_credits` transport failure is not proof the debit did not commit. The per-turn
    ref_id makes an exact compensating refund possible: `refunded` if it landed,
    `no_matching_debit` (quiet) if it never did."""
    from app.services.credit_service import CreditServiceUnavailable
    calls = []

    class _CS:
        def precharge(self, *a, **k):
            raise CreditServiceUnavailable("edge 520")

        def refund_ledgered(self, user_id, amount, *, reason, ref_id=None, quiet_no_match=False):
            calls.append((user_id, amount, reason, ref_id, quiet_no_match))
            return {"outcome": "no_matching_debit"}
    monkeypatch.setattr(chat_mod, "CreditService", _CS)
    import app.services.chat_budget_service as cbs
    monkeypatch.setattr(cbs.ChatBudgetService, "claim_free_followup", lambda self, sid: False)
    quota, err = chat_mod._claim_chat_quota(_USER, None, session_id=_SESSION)
    assert quota is None and err.status_code == 409
    assert len(calls) == 1
    uid, amount, reason, ref_id, quiet = calls[0]
    assert uid == _USER["id"] and reason == "chat_precharge_unconfirmed" and quiet is True
    assert ref_id.startswith(f"{_SESSION}:")


def test_the_non_stream_door_is_bounded_and_refunds_on_the_budget(harness, monkeypatch):
    """The non-stream door sends nothing until it is done and iOS gives it 60 s; every server
    ceiling on that path is larger. Past `CHAT_SEND_BUDGET_SECONDS` it answers before any
    write so the charge is handed back."""
    import asyncio as _aio
    from app.config import settings
    client, db, quota, _ = harness
    monkeypatch.setattr(settings, "CHAT_SEND_BUDGET_SECONDS", 0.05)

    async def _slow(self, *a, **k):
        await _aio.sleep(1.0)
        return {"content": "late", "citations": None, "tokens_used": 5}
    monkeypatch.setattr(_FakeChatService, "generate_response", _slow)
    r = client.post(f"/api/v1/chat/sessions/{_SESSION}/messages", json={"message": "hi"})
    assert r.status_code in (502, 503), r.text
    assert r.json()["error_code"] == "GEMINI_UNAVAILABLE"
    assert db.inserted_messages == []
    assert quota.delivered == 0 and quota.refunds == ["chat_undelivered"]


def test_the_non_stream_door_classifies_a_quota_outage(harness, monkeypatch):
    from app.integrations.gemini import GeminiQuotaError
    client, db, quota, _ = harness

    async def _quota(self, *a, **k):
        raise GeminiQuotaError("quota circuit open (resource_exhausted)")
    monkeypatch.setattr(_FakeChatService, "generate_response", _quota)
    r = client.post(f"/api/v1/chat/sessions/{_SESSION}/messages", json={"message": "hi"})
    assert r.status_code != 500
    assert r.json()["error_code"] == "GEMINI_QUOTA_EXCEEDED", r.text
    assert quota.refunds == ["chat_undelivered"]


def test_the_non_stream_door_does_not_persist_a_turn_whose_client_is_gone(harness, monkeypatch):
    """A plain JSON route is never cancelled on a client disconnect (Starlette watches
    `http.disconnect` on streaming responses only), so a turn the client abandoned — phone
    locked, its own 60 s ceiling — was persisted and charged for nobody. The probe runs
    BEFORE the write so `delivered` stays False and the `finally` refunds."""
    from starlette.requests import Request
    client, db, quota, _ = harness
    _FakeChatService.fallback_result = {"content": "A fine answer. " * 5, "tokens_used": 30}

    async def _gone(self):
        return True
    monkeypatch.setattr(Request, "is_disconnected", _gone)
    r = client.post(f"/api/v1/chat/sessions/{_SESSION}/messages", json={"message": "hi"})
    assert r.status_code in (502, 503), r.text
    body = r.json()
    assert body["error_code"] == "GEMINI_UNAVAILABLE" and body["details"]["step"] == "chat_send_disconnected"
    assert db.inserted_messages == [], "nothing may be persisted for a client that left"
    assert quota.delivered == 0 and quota.refunds == ["chat_undelivered"]


def test_a_failing_disconnect_probe_lets_the_turn_proceed(harness, monkeypatch):
    from starlette.requests import Request
    client, db, quota, _ = harness
    _FakeChatService.fallback_result = {"content": "A fine answer. " * 5, "tokens_used": 30}

    async def _boom(self):
        raise RuntimeError("probe broke")
    monkeypatch.setattr(Request, "is_disconnected", _boom)
    r = client.post(f"/api/v1/chat/sessions/{_SESSION}/messages", json={"message": "hi"})
    assert r.status_code == 200, r.text
    assert quota.delivered == 1


# ── Session OWNERSHIP on every session-scoped route ─────────────────────────────
#
# The permissive `_Query` above answers the `chat_sessions` lookup with the fixture row for
# ANY filter, so deleting `.eq("user_id", user["id"])` from a door left the whole file green.
# In production a signed-in account that learns another user's session UUID could then POST
# a turn INTO the victim's transcript (the attacker pays, so the ledger shows nothing), read
# their history, or delete it. This fake honours the recorded filters, so the guard is real.


class _OwnedQuery(_Query):
    """A `chat_sessions` lookup that applies EVERY `.eq()` it was given."""

    def __init__(self, db, table):
        super().__init__(db, table)
        self.filters: List[tuple] = []

    def eq(self, col, val):
        self.filters.append((col, val))
        return self

    def delete(self):
        self.op = "delete"
        return self

    def execute(self):
        if self.table == "chat_sessions" and self.op == "select":
            self.db.calls.append((self.table, "select", tuple(self.filters)))
            row = self.db.session_row
            if all(row.get(c) == v for c, v in self.filters):
                return _Result(dict(row))
            return _Result(None)
        return super().execute()


class _OwnedDB(_FakeDB):
    def table(self, name: str):
        return _OwnedQuery(self, name)


_ATTACKER = {**_USER, "id": "33333333-3333-3333-3333-333333333333"}


def _owned_db(db: _FakeDB) -> _OwnedDB:
    # `created_at` so the GET door's `_row_to_session` can render the owner's row.
    out = _OwnedDB({**db.session_row, "created_at": "2026-09-17T00:00:00+00:00"})
    out.calls, out.inserted_messages = db.calls, db.inserted_messages
    return out


@pytest.mark.parametrize("door", ["stream", "send", "get", "delete"])
def test_another_users_session_is_a_404_on_every_door(harness, door):
    client, db, quota, _ = harness
    owned = _owned_db(db)
    app.dependency_overrides[get_supabase] = lambda: owned
    app.dependency_overrides[get_chat_identity] = lambda: _ATTACKER

    if door == "stream":
        r = _post(client)
    elif door == "send":
        r = client.post(f"/api/v1/chat/sessions/{_SESSION}/messages", json={"message": "hi"})
    elif door == "get":
        r = client.get(f"/api/v1/chat/sessions/{_SESSION}")
    else:
        r = client.delete(f"/api/v1/chat/sessions/{_SESSION}")

    assert r.status_code == 404, r.text
    # Nothing of the victim's was touched, and the attacker was not charged for the attempt.
    assert db.inserted_messages == []
    assert quota.delivered == 0 and quota.refunds == []
    assert _FakeChatService.instances == []
    assert not [c for c in db.calls if c[0] == "chat_sessions" and c[1] in ("update", "delete")]
    assert not [c for c in db.calls if c[0] == "chat_messages" and c[1] == "delete"]
    # Tripwire against a vacuous pass: the lookup CARRIED the caller's id as a filter — a
    # refactor that answered None for an unrelated reason would otherwise keep this green.
    lookups = [c for c in db.calls if c[0] == "chat_sessions" and c[1] == "select"]
    assert lookups and ("user_id", _ATTACKER["id"]) in lookups[0][2], lookups


@pytest.mark.parametrize("door", ["stream", "send", "get", "delete"])
def test_the_owner_still_passes_the_filtered_lookup(harness, door):
    """Control: the honouring fake is not simply refusing everyone."""
    client, db, quota, _ = harness
    owned = _owned_db(db)
    app.dependency_overrides[get_supabase] = lambda: owned
    if door == "stream":
        r = _post(client)
    elif door == "send":
        _FakeChatService.fallback_result = {"content": "Plain answer. " * 3, "tokens_used": 30}
        r = client.post(f"/api/v1/chat/sessions/{_SESSION}/messages", json={"message": "hi"})
    elif door == "get":
        r = client.get(f"/api/v1/chat/sessions/{_SESSION}")
    else:
        r = client.delete(f"/api/v1/chat/sessions/{_SESSION}")
    assert r.status_code == 200, (door, r.status_code, r.text[:300])
