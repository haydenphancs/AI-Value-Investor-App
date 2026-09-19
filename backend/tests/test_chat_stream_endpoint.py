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
        # eq / neq / in_ / gte / lte / order / limit / range / single / maybe_single
        def _chain(*a, **k):
            return self
        return _chain

    # The E7 card probe: `.not_.is_("rich_content->widget", "null")` on chat_messages.
    @property
    def not_(self):
        return self

    def is_(self, column, value):
        if "widget" in str(column):
            self.op = "select-card-probe"
        return self

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
        if self.table == "chat_messages" and self.op == "select-card-probe":
            # A prior assistant row with a card exists unless a test says otherwise —
            # the common case for a later turn of a grounded session.
            return _Result([{"id": "prior-card"}] if self.db.prior_card else [])
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
        self.prior_card = True   # a later grounded turn already has a card on file

    def table(self, name: str) -> _Query:
        return _Query(self, name)


# ── the quota + chat service fakes ───────────────────────────────────────────

class _Quota:
    """Mirrors `_ChatQuota`'s ONE-SHOT settlement: the real `refund_once` / `settle_no_cost`
    return after the first call (`_settled`), so a later backstop (the `finally` in
    `_metered_stream`) is a no-op. Without the flag the fake recorded a second entry on
    every error-site path and no exact reason list was assertable — which is how four
    stream-door refund sites went untested (F12-5). `every_call` keeps the raw log."""

    def __init__(self):
        self.refunds: List[str] = []      # `refund_once`: the turn never arrived (first only)
        self.settled: List[str] = []      # `settle_no_cost`: delivered, but cost nothing
        self.every_call: List[str] = []   # every settlement attempt, in order
        self.delivered = 0
        self._settled = False

    def refund_once(self, reason: str) -> None:
        self.every_call.append(f"refund:{reason}")
        if self._settled:
            return
        self._settled = True
        self.refunds.append(reason)

    def settle_no_cost(self, reason: str) -> None:
        self.every_call.append(f"settle:{reason}")
        if self._settled:
            return
        self._settled = True
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
    # The continuation round (`stream_text`) a CUT answer triggers. Class-level knobs,
    # reset by the harness: the events it streams, or whether it raises on first read.
    continue_events: List[Any] = []
    continue_raises: bool = False
    continue_calls: List[Dict[str, Any]] = []

    def __init__(self, events):
        self._events = events
        self.stream_calls: List[Dict[str, Any]] = []
        self.model_name = "gemini-2.5-flash"

    def stream_agentic(self, prompt, **kwargs):
        self.stream_calls.append({"prompt": prompt, **kwargs})
        return _events(*self._events)()

    def stream_text(self, prompt, **kwargs):
        cls = type(self)
        cls.continue_calls.append({"prompt": prompt, **kwargs})

        async def _gen():
            if cls.continue_raises:
                raise RuntimeError("continuation exploded")
            for e in cls.continue_events:
                yield e
        return _gen()


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
        self.fallback_kwargs = dict(kw)
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
    _FakeGemini.continue_events = []
    _FakeGemini.continue_raises = False
    _FakeGemini.continue_calls = []
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


# ── F12-12: the pre-flight refusals are JSON at the endpoint, on BOTH doors ──────
#
# `_claim_chat_quota` answers 402 INSUFFICIENT_CREDITS / 409 SYSTEM_BUSY as a JSONResponse
# BEFORE the stream opens (iOS decodes bodies only for 400/402/403/409). Nothing drove
# that at the endpoint: a refactor that deferred the return into the SSE body would have
# turned the friendly 402 into an `error` frame with every unit test green.


@pytest.mark.parametrize("door", ["stream", "send"])
@pytest.mark.parametrize("code, status, kwargs", [
    ("INSUFFICIENT_CREDITS", 402, {}),                 # no explicit status: pins the default
    ("SYSTEM_BUSY", 409, {"status_code": 409}),
])
def test_a_preflight_refusal_is_a_json_status_not_an_sse_frame(harness, monkeypatch, door, code, status, kwargs):
    from app.api.error_response import ErrorCode, make_error_response

    client, db, quota, _ = harness
    refusal = make_error_response(getattr(ErrorCode, code), message="refused", **kwargs)
    monkeypatch.setattr(chat_mod, "_claim_chat_quota", lambda *a, **k: (None, refusal))
    path = f"/api/v1/chat/sessions/{_SESSION}/messages" + ("/stream" if door == "stream" else "")
    r = client.post(path, json={"message": "How is Apple doing?"},
                    headers={"Authorization": "Bearer test"})
    assert r.status_code == status
    assert r.headers["content-type"].startswith("application/json"), r.headers
    assert r.json()["error_code"] == code
    assert db.inserted_messages == [], "nothing persisted for a refused turn"
    assert _FakeChatService.instances == [], "no generation was started"
    assert quota.refunds == [] and quota.delivered == 0, "the harness quota was never touched"


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
    deep dive, cached for every user for 24 h with its last sentence missing.

    With the continuation round yielding NOTHING (the harness default) the cut stands:
    refunded, never cached — and now MARKED on the wire with the single Continue chip in
    place of model-written follow-ups (E1)."""
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
    assert done["truncated"] is True
    assert done["suggestions"] == [chat_mod._CONTINUE_CHIP]
    assert _FakeChatService.instances[-1].suggestion_calls == 0, \
        "no model-written chips off a half sentence"
    assistant = [m for m in db.inserted_messages if m.get("role") == "assistant"][0]
    assert assistant["rich_content"]["truncated"] is True
    assert assistant["rich_content"]["finish_reason"] == "MAX_TOKENS"
    # The continuation was attempted once, on the tool-less instruction, under the caps.
    assert len(_FakeGemini.continue_calls) == 1
    call = _FakeGemini.continue_calls[0]
    assert "<<<PARTIAL_ANSWER>>>" in call["prompt"] and "SPY is up 1.2%" in call["prompt"]
    assert call["max_output_tokens"] and "thinking_budget" in call
    assert call["usage_tag"].endswith(":continue")


# ── E1: a cut answer is continued in the same turn ───────────────────────────

_CUT_EVENTS = [
    ("thought", "Let me check."),
    ("answer", "A cryptocurrency is deflationary if supply is capped. For Polygon (MATIC), the"),
    ("finish", "MAX_TOKENS"),
]


def test_a_cut_answer_is_completed_by_one_continuation_round(harness, monkeypatch):
    """The TestFlight MATIC turn: cut at "For Polygon (MATIC), the". The continuation
    streams the rest into the SAME turn — complete answer, no refund, normal chips, no
    truncation mark, and a deep dive is cacheable again."""
    client, db, quota, _ = harness
    _FakeChatService.prep_overrides = {"is_deep_dive": True, "deep_dive_context": "ctx",
                                       "deep_dive_cached": None,
                                       "system_instruction_no_tools": "SYS-NO-TOOLS"}
    writes = []
    monkeypatch.setattr(_FakeChatService, "_upsert_deep_dive_cache",
                        lambda self, *a, **k: writes.append(a))
    _FakeChatService.events = list(_CUT_EVENTS)
    _FakeGemini.continue_events = [
        ("thought", "Picking up where I stopped."),   # must NOT reach the client
        ("answer", " supply is uncapped, so it is mildly inflationary."),
    ]
    db.session_row["stock_id"] = "MATIC"
    r = _post(client, message="Is MATIC inflationary?")
    assert r.status_code == 200, r.text
    frames = _parse_sse(r.text)
    done = frames[-1][1]["message"]
    assert done["content"].startswith(
        "A cryptocurrency is deflationary if supply is capped. For Polygon (MATIC), the "
        "supply is uncapped, so it is mildly inflationary."
    ), done["content"]
    assert done.get("truncated") is None
    assert quota.settled == [] and quota.delivered == 1, (quota.settled, quota.refunds)
    assert done["suggestions"] == ["What about its margins?", "How does it compare to peers?"]
    assert _FakeChatService.instances[-1].suggestion_calls == 1
    assert len(writes) == 1, "a completed brief is cacheable again"
    # The continuation's text arrived as ordinary token frames, after the cut point…
    tokens = [d["delta"] for e, d in frames if e == "token"]
    assert " supply is uncapped, so it is mildly inflationary." in tokens
    # …and its thought never re-activated the thinking card.
    reasoning = [d["delta"] for e, d in frames if e == "reasoning"]
    assert "Picking up where I stopped." not in reasoning
    assistant = [m for m in db.inserted_messages if m.get("role") == "assistant"][0]
    assert "truncated" not in assistant["rich_content"]
    assert "finish_reason" not in assistant["rich_content"]
    call = _FakeGemini.continue_calls[0]
    assert call["system_instruction"] == "SYS-NO-TOOLS", "the data was gathered in round one"


def test_a_continuation_that_is_cut_again_keeps_the_turn_truncated(harness):
    client, db, quota, _ = harness
    _FakeChatService.events = list(_CUT_EVENTS)
    _FakeGemini.continue_events = [("answer", " supply is"), ("finish", "MAX_TOKENS")]
    r = _post(client, message="Is MATIC inflationary?")
    assert r.status_code == 200, r.text
    done = _parse_sse(r.text)[-1][1]["message"]
    assert done["content"].endswith("For Polygon (MATIC), the supply is"), "both fragments kept"
    assert done["truncated"] is True
    assert done["suggestions"] == [chat_mod._CONTINUE_CHIP]
    assert quota.settled == ["chat_degraded_truncated"]
    assert len(_FakeGemini.continue_calls) == 1, "at most one continuation per turn"


def test_a_continuation_that_raises_keeps_the_partial_answer(harness):
    """A failed continuation must never reach the full-regenerate fallback: the partial
    answer the user is reading stands, marked, refunded."""
    client, db, quota, _ = harness
    _FakeChatService.events = list(_CUT_EVENTS)
    _FakeGemini.continue_raises = True
    r = _post(client, message="Is MATIC inflationary?")
    assert r.status_code == 200, r.text
    frames = _parse_sse(r.text)
    assert "reset" not in [f[0] for f in frames], "no fallback ran"
    assert _FakeChatService.instances[-1].fallback_calls == 0
    done = frames[-1][1]["message"]
    assert done["content"].endswith("For Polygon (MATIC), the")
    assert done["truncated"] is True and done["suggestions"] == [chat_mod._CONTINUE_CHIP]
    assert quota.settled == ["chat_degraded_truncated"]


def test_a_continuation_with_only_thoughts_is_still_a_cut(harness):
    client, db, quota, _ = harness
    _FakeChatService.events = list(_CUT_EVENTS)
    _FakeGemini.continue_events = [("thought", "Hmm."), ("answer", "   ")]
    r = _post(client, message="Is MATIC inflationary?")
    assert r.status_code == 200, r.text
    done = _parse_sse(r.text)[-1][1]["message"]
    assert done["truncated"] is True
    assert quota.settled == ["chat_degraded_truncated"]


def test_auto_continue_can_be_switched_off(harness, monkeypatch):
    from app.config import settings
    client, db, quota, _ = harness
    monkeypatch.setattr(settings, "CHAT_AUTO_CONTINUE_ENABLED", False)
    _FakeChatService.events = list(_CUT_EVENTS)
    _FakeGemini.continue_events = [("answer", " never used")]
    r = _post(client, message="Is MATIC inflationary?")
    assert r.status_code == 200, r.text
    assert _FakeGemini.continue_calls == []
    done = _parse_sse(r.text)[-1][1]["message"]
    assert done["truncated"] is True and done["suggestions"] == [chat_mod._CONTINUE_CHIP]
    assert quota.settled == ["chat_degraded_truncated"]


def test_a_clean_answer_never_runs_a_continuation_and_carries_no_mark(harness):
    client, db, quota, _ = harness
    _FakeGemini.continue_events = [("answer", " never used")]
    r = _post(client)
    assert r.status_code == 200, r.text
    assert _FakeGemini.continue_calls == []
    done = _parse_sse(r.text)[-1][1]["message"]
    assert done.get("truncated") is None
    assistant = [m for m in db.inserted_messages if m.get("role") == "assistant"][0]
    assert "truncated" not in assistant["rich_content"]
    assert "finish_reason" not in assistant["rich_content"]


def test_a_cut_on_an_already_degraded_synthesis_turn_is_still_marked(harness, monkeypatch):
    """`degraded` is first-wins (the ledger keeps `partial_specialists`); the truncation
    mark and the Continue chip key off the finish reason and must not be lost to it."""
    client, db, quota, _ = harness
    _route_synthesize(monkeypatch)
    _FakeChatService.synthesis_signal = "partial_specialists"
    _FakeChatService.events = [("answer", "Merged answer that stops mid"), ("finish", "MAX_TOKENS")]
    r = _post(client)
    assert r.status_code == 200, r.text
    done = _parse_sse(r.text)[-1][1]["message"]
    assert quota.settled == ["chat_degraded_partial_specialists"]
    assert done["truncated"] is True
    assert done["suggestions"] == [chat_mod._CONTINUE_CHIP]
    assert len(_FakeGemini.continue_calls) == 1, "still continued"


def test_a_cut_on_a_degraded_synthesis_turn_completed_by_continuation_keeps_its_ledger_label(harness, monkeypatch):
    client, db, quota, _ = harness
    _route_synthesize(monkeypatch)
    _FakeChatService.synthesis_signal = "partial_specialists"
    _FakeChatService.events = [("answer", "Merged answer that stops mid"), ("finish", "MAX_TOKENS")]
    _FakeGemini.continue_events = [("answer", "-sentence, then finishes.")]
    r = _post(client)
    assert r.status_code == 200, r.text
    done = _parse_sse(r.text)[-1][1]["message"]
    assert done.get("truncated") is None
    assert done["content"].endswith("mid-sentence, then finishes.")
    assert quota.settled == ["chat_degraded_partial_specialists"], "the lens shortfall still refunds"


def test_a_fallback_answer_that_was_cut_is_marked_and_gets_the_continue_chip(harness):
    """The stream died, `generate_response` answered — and ITS answer was cut. The
    non-stream door's verdict (`truncated` + `finish_reason` in the result) is what the
    persisted row reflects; the aborted stream's own cut is discarded with its tokens."""
    client, db, quota, _ = harness
    _FakeChatService.fallback_result = {
        "content": "Plain answer that stops mid", "tokens_used": 30,
        "degraded": "truncated", "truncated": True, "finish_reason": "MAX_TOKENS",
    }
    async def _boom(*a, **k):
        yield ("answer", "partial…")
        raise RuntimeError("stream died")
    orig = _FakeGemini.stream_agentic
    _FakeGemini.stream_agentic = lambda self, prompt, **kw: _boom()
    try:
        r = _post(client)
    finally:
        _FakeGemini.stream_agentic = orig
    assert r.status_code == 200, r.text
    done = _parse_sse(r.text)[-1][1]["message"]
    assert done["content"].startswith("Plain answer")
    assert done["truncated"] is True and done["suggestions"] == [chat_mod._CONTINUE_CHIP]
    assert quota.settled == ["chat_degraded_truncated"]
    assert _FakeGemini.continue_calls == [], "a fallback answer is not continued on this door"
    assistant = [m for m in db.inserted_messages if m.get("role") == "assistant"][0]
    assert assistant["rich_content"]["finish_reason"] == "MAX_TOKENS"


def test_a_healthy_fallback_after_a_cut_stream_carries_no_mark(harness):
    """The aborted stream was cut, but the fallback answered cleanly: the stream's finish
    reason must be dropped with its tokens, or a complete answer would read as cut."""
    client, db, quota, _ = harness
    _FakeChatService.fallback_result = {"content": "Full healthy answer. " * 3, "tokens_used": 80}
    async def _cut_then_die(*a, **k):
        yield ("answer", "partial…")
        yield ("finish", "MAX_TOKENS")
        raise RuntimeError("stream died after the cut")
    orig = _FakeGemini.stream_agentic
    _FakeGemini.stream_agentic = lambda self, prompt, **kw: _cut_then_die()
    try:
        r = _post(client)
    finally:
        _FakeGemini.stream_agentic = orig
    assert r.status_code == 200, r.text
    done = _parse_sse(r.text)[-1][1]["message"]
    assert done.get("truncated") is None
    assert quota.settled == [], quota.settled


def test_the_non_stream_door_marks_a_cut_answer_identically(harness):
    """Same verdict, same row shape, same settlement label as the stream door."""
    client, db, quota, _ = harness
    _FakeChatService.fallback_result = {
        "content": "Plain answer that stops mid", "tokens_used": 30,
        "degraded": "truncated", "truncated": True, "finish_reason": "MAX_TOKENS",
    }
    r = client.post(f"/api/v1/chat/sessions/{_SESSION}/messages", json={"message": "hi"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["truncated"] is True
    assert body["suggestions"] == [chat_mod._CONTINUE_CHIP]
    assert quota.settled == ["chat_degraded_truncated"]
    assistant = [m for m in db.inserted_messages if m.get("role") == "assistant"][0]
    rich = assistant["rich_content"]
    assert rich["truncated"] is True and rich["finish_reason"] == "MAX_TOKENS"
    assert rich["suggestions"] == [chat_mod._CONTINUE_CHIP]


def test_the_non_stream_door_leaves_a_clean_answer_unmarked(harness):
    client, db, quota, _ = harness
    _FakeChatService.fallback_result = {"content": "Plain answer. " * 3, "tokens_used": 30,
                                        "finish_reason": "STOP"}
    r = client.post(f"/api/v1/chat/sessions/{_SESSION}/messages", json={"message": "hi"})
    assert r.status_code == 200, r.text
    assert r.json().get("truncated") is None
    assistant = [m for m in db.inserted_messages if m.get("role") == "assistant"][0]
    assert "truncated" not in assistant["rich_content"]


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


def _keepalives_after_last_token(text: str) -> int:
    """Count `: keepalive` comment lines that sit AFTER the final `event: token` frame and
    BEFORE the `credits` frame — i.e. the ones that cover the post-persist suggestions call."""
    last_token = text.rfind("event: token")
    credits = text.find("event: credits")
    assert last_token >= 0 and credits > last_token, text[:400]
    return text[last_token:credits].count(": keepalive")


def test_the_suggestions_step_after_persist_carries_keepalives(harness, monkeypatch):
    """F01-4. The follow-up-suggestions call runs AFTER the last `token` frame and is one
    awaited Gemini call (2 × 90 s ceilings plus the overload ladder's backoffs). With no
    heartbeat, an overloaded model crossed iOS's 120 s idle timeout in silence: the client
    dropped the socket, removed the bubble the user was reading, and never saw `credits`.
    This exact assertion counted 0 before the fix."""
    import asyncio as _aio
    from app.config import settings
    client, db, quota, _ = harness
    monkeypatch.setattr(settings, "CHAT_STREAM_KEEPALIVE_SECONDS", 0.02)

    async def _slow_suggestions(self, *a, **k):
        self.suggestion_calls += 1
        await _aio.sleep(0.25)
        return ["What about its margins?", "How does it compare to peers?"]
    monkeypatch.setattr(_FakeChatService, "generate_followup_suggestions", _slow_suggestions)

    r = _post(client)
    assert r.status_code == 200, r.text
    assert _keepalives_after_last_token(r.text) >= 2, r.text[-600:]
    frames = _parse_sse(r.text)
    names = [f[0] for f in frames]
    assert names[-2:] == ["credits", "done"]
    # The chips still arrive, live and in `done`, and the turn is still charged once.
    assert "suggestions" in names
    done = frames[-1][1]["message"]
    assert done["rich_content"]["suggestions"] == ["What about its margins?", "How does it compare to peers?"]
    assert quota.settled == [] and quota.delivered == 1
    assert _FakeChatService.instances[-1].suggestion_calls == 1


def test_a_suggestions_call_that_outruns_the_turn_budget_is_cancelled_not_awaited(harness, monkeypatch):
    """Chips are best-effort: a call that outlives the turn deadline is cancelled and the
    turn completes without them — never a hung stream, never a lost `credits`/`done`."""
    import asyncio as _aio
    from app.config import settings
    client, db, quota, _ = harness
    monkeypatch.setattr(settings, "CHAT_STREAM_KEEPALIVE_SECONDS", 0.02)
    # The deadline is max(now+5, min(started+STREAM_BUDGET, now+SEND_BUDGET)) and is
    # computed BEFORE the suggestions task first runs. Rather than waiting 50 s of wall
    # time (or shrinking the budgets, which also starves the answer pump that runs under
    # the same STREAM_BUDGET), the suggestions call jumps the monotonic clock past it.
    import time as _t
    real = _t.monotonic
    skew = {"v": 0.0}
    monkeypatch.setattr(_t, "monotonic", lambda: real() + skew["v"])
    budget = max(5.0, min(settings.CHAT_STREAM_BUDGET_SECONDS, settings.CHAT_SEND_BUDGET_SECONDS))

    cancelled = {"v": False}

    async def _hung(self, *a, **k):
        self.suggestion_calls += 1
        skew["v"] = budget + 10.0         # the whole turn budget is now behind us
        try:
            await _aio.sleep(30)
        except _aio.CancelledError:
            cancelled["v"] = True
            raise
        return ["never"]
    monkeypatch.setattr(_FakeChatService, "generate_followup_suggestions", _hung)

    r = _post(client)
    assert r.status_code == 200, r.text
    frames = _parse_sse(r.text)
    names = [f[0] for f in frames]
    assert names[-2:] == ["credits", "done"], names
    assert "token" in names and _FakeChatService.instances[-1].fallback_calls == 0
    assert "suggestions" not in names
    assert frames[-1][1]["message"]["rich_content"].get("suggestions") is None
    assert cancelled["v"] is True, "the orphaned Gemini call was left running"
    assert quota.settled == [] and quota.delivered == 1


def test_a_budget_overrun_is_logged_once_and_never_reads_a_pending_task(harness, monkeypatch, caplog):
    """W2 regress-A-1: after the deadline's `cancel()` the task is still pending, so
    `.result()` raised InvalidStateError into the generic except — the same "no chips"
    outcome, reached through a misleading second warning on every overrun."""
    import asyncio as _aio
    import logging as _logging
    from app.config import settings
    client, db, quota, _ = harness
    monkeypatch.setattr(settings, "CHAT_STREAM_KEEPALIVE_SECONDS", 0.02)
    import time as _t
    real = _t.monotonic
    skew = {"v": 0.0}
    monkeypatch.setattr(_t, "monotonic", lambda: real() + skew["v"])
    budget = max(5.0, min(settings.CHAT_STREAM_BUDGET_SECONDS, settings.CHAT_SEND_BUDGET_SECONDS))

    async def _hung(self, *a, **k):
        self.suggestion_calls += 1
        skew["v"] = budget + 10.0
        await _aio.sleep(30)
        return ["never"]
    monkeypatch.setattr(_FakeChatService, "generate_followup_suggestions", _hung)

    with caplog.at_level(_logging.WARNING, logger="app.api.v1.endpoints.chat"):
        r = _post(client)
    assert r.status_code == 200
    names = [f[0] for f in _parse_sse(r.text)]
    assert names[-2:] == ["credits", "done"] and "suggestions" not in names
    msgs = [rec.getMessage() for rec in caplog.records if rec.name == "app.api.v1.endpoints.chat"]
    assert any("exceeded the turn budget" in m for m in msgs)
    assert not any("InvalidStateError" in m for m in msgs), msgs
    assert not any("suggestions step failed" in m for m in msgs), msgs


def test_a_suggestions_failure_still_delivers_credits_and_done(harness, monkeypatch):
    client, db, quota, _ = harness

    async def _boom(self, *a, **k):
        raise RuntimeError("flash-lite 503")
    monkeypatch.setattr(_FakeChatService, "generate_followup_suggestions", _boom)
    r = _post(client)
    assert r.status_code == 200, r.text
    names = [f[0] for f in _parse_sse(r.text)]
    assert names[-2:] == ["credits", "done"] and "suggestions" not in names
    assert quota.settled == [] and quota.delivered == 1


def test_a_quiet_stream_with_a_fast_suggestions_call_emits_no_stray_keepalive(harness, monkeypatch):
    """Control: a prompt suggestions call yields no comment frames after the last token."""
    from app.config import settings
    client, db, quota, _ = harness
    monkeypatch.setattr(settings, "CHAT_STREAM_KEEPALIVE_SECONDS", 5.0)
    r = _post(client)
    assert r.status_code == 200, r.text
    assert _keepalives_after_last_token(r.text) == 0


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


# ── F12-5: every stream-door refund site, at the endpoint ────────────────────────────
#
# `refund_once` has four callers on the stream door (fallback failed, empty answer, persist
# failed, the `finally` backstop on a client disconnect). None had an endpoint test: deleting
# any one of them left every chat/credit guard file green while a turn whose stream died
# kept its precharged credit — and the `error` frame it emits is TERMINAL on iOS (no
# re-POST), so nothing on the client recovered it either.


def test_refund_site_fallback_failed(harness):
    """The stream dies before any answer and the non-stream fallback raises too."""
    client, db, quota, _ = harness
    _FakeChatService.events = [("thought", "Let me check.")]     # no answer → fallback
    _FakeChatService.fallback_result = None                        # fallback raises
    r = _post(client)
    assert r.status_code == 200
    names = [f[0] for f in _parse_sse(r.text)]
    assert "error" in names and "done" not in names
    assert quota.refunds == ["chat_stream_fallback_failed"], quota.every_call
    assert quota.delivered == 0 and db.inserted_messages == []


def test_refund_site_empty_answer(harness):
    """Thought-only stream, fallback answers an EMPTY string: nothing to persist or charge."""
    client, db, quota, _ = harness
    _FakeChatService.events = [("thought", "Let me check.")]
    _FakeChatService.fallback_result = {"content": "", "tokens_used": 0}
    r = _post(client)
    names = [f[0] for f in _parse_sse(r.text)]
    assert names[-1] == "error" and "done" not in names
    assert quota.refunds == ["chat_stream_empty"], quota.every_call
    assert quota.delivered == 0 and db.inserted_messages == []


def test_refund_site_persist_failed(harness):
    """The answer streamed, the 2-row insert raised: not recorded → handed back."""
    client, db, quota, _ = harness
    app.dependency_overrides[get_supabase] = lambda: _raising_db(db, "chat_messages", RuntimeError("insert died"))
    r = _post(client)
    frames = _parse_sse(r.text)
    names = [f[0] for f in frames]
    assert "token" in names and names[-1] == "error"
    assert frames[-1][1]["user_message"].startswith("Your answer was generated but couldn't be saved")
    assert quota.refunds == ["chat_stream_persist_failed"], quota.every_call
    assert quota.delivered == 0


def test_refund_site_client_disconnect_backstop(harness, monkeypatch):
    """The phone drops mid-stream: the `finally` in `_metered_stream` is the ONLY thing
    that hands the credit back. Driven over raw ASGI so the disconnect actually lands."""
    import asyncio as _aio
    client, db, quota, _ = harness
    slow_events = [("thought", "Let me check."), ("answer", "Apple is ")]

    async def _slow_gen(*a, **k):
        yield slow_events[0]
        await _aio.sleep(0.5)          # long enough for the disconnect to land
        yield slow_events[1]
        yield ("answer", "doing fine.")
    _FakeChatService.events = slow_events

    monkeypatch.setattr(_FakeGemini, "stream_agentic", lambda self, prompt, **kw: _slow_gen())

    body = json.dumps({"message": "How is Apple doing?"}).encode()
    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "POST",
        "scheme": "http", "path": f"/api/v1/chat/sessions/{_SESSION}/messages/stream",
        "raw_path": f"/api/v1/chat/sessions/{_SESSION}/messages/stream".encode(),
        "query_string": b"", "root_path": "", "server": ("testserver", 80), "client": ("127.0.0.1", 1),
        "headers": [
            (b"host", b"testserver"), (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode()), (b"accept", b"text/event-stream"),
        ],
    }
    sent: List[Dict[str, Any]] = []
    state = {"body": False}

    async def receive():
        if state["body"]:
            await _aio.sleep(0.05)
            return {"type": "http.disconnect"}
        state["body"] = True
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message):
        sent.append(message)

    _aio.run(app(scope, receive, send))
    text = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body").decode()
    names = [f[0] for f in _parse_sse(text)]
    assert "done" not in names
    assert quota.delivered == 0 and db.inserted_messages == []
    assert quota.refunds == ["chat_stream_cancelled"], quota.every_call


# ── F03-10 / F03-8 / F02-6 / F13-6 ─────────────────────────────────────────────


def test_a_per_call_gemini_timeout_is_not_reported_as_a_budget_overrun(harness, monkeypatch):
    """`GeminiTimeoutError` subclasses `asyncio.TimeoutError`, so the budget arm swallowed
    it and blamed CHAT_SEND_BUDGET_SECONDS for a call that timed out on its own ceiling."""
    from app.integrations.gemini import GeminiTimeoutError
    client, db, quota, _ = harness

    async def _boom(self, *a, **k):
        raise GeminiTimeoutError("generate_content timed out after 60s")
    monkeypatch.setattr(_FakeChatService, "generate_response", _boom)
    r = client.post(f"/api/v1/chat/sessions/{_SESSION}/messages", json={"message": "hi"})
    body = r.json()
    assert body["error_code"] == "GEMINI_UNAVAILABLE", r.text
    assert body.get("details", {}).get("step") != "chat_send_budget", body
    assert quota.refunds == ["chat_undelivered"]


def test_a_whitespace_only_streamed_answer_is_refunded_like_an_empty_one(harness):
    client, db, quota, _ = harness
    _FakeChatService.events = [("thought", "Let me check."), ("answer", "\n  \n")]
    # A whitespace-only stream reads as "empty stream result" and falls back; the
    # fallback answering whitespace too is the case the door then has to judge.
    _FakeChatService.fallback_result = {"content": " \n\t", "tokens_used": 1}
    r = _post(client)
    names = [f[0] for f in _parse_sse(r.text)]
    assert names[-1] == "error" and "done" not in names
    assert quota.refunds == ["chat_stream_empty"], quota.every_call
    assert db.inserted_messages == []


def test_a_deep_dive_cache_hit_does_not_promise_a_specialist(harness, monkeypatch):
    """The replayed brief was written by the general path; a `routing` frame naming
    "Valuation" put a false stage on the thinking card for a lens that never ran."""
    client, db, quota, _ = harness
    _FakeChatService.prep_overrides = {
        "is_deep_dive": True,
        "deep_dive_cached": "# Cached brief\n\nApple looks fine." + " More." * 30,
    }
    r = _post(client, message="Give me a full AI Analyst brief on AAPL")
    frames = _parse_sse(r.text)
    routing = [f[1] for f in frames if f[0] == "routing"]
    assert all(fr.get("specialists") == ["general"] for fr in routing), routing
    assert all("Valuation" not in (fr.get("labels") or []) for fr in routing), routing


def test_the_stream_handler_map_is_class_filtered_like_the_declarations(harness):
    """The declarations decide what the model is OFFERED; a handler left in the map for
    an undeclared tool still ran if the model named it from memory. On an INDEX screen
    only the two index tools may be executable."""
    from app.services.agents.chat_tools import build_chat_tool_handlers, tools_for_asset_type
    client, db, quota, _ = harness
    _FakeChatService.prep_overrides = {"asset_type": "INDEX"}
    _FakeChatService.events = [("answer", "The S&P is up.")]
    r = _post(client)
    assert r.status_code == 200, r.text
    # BEHAVIOURAL (W2 vacuity-1: this used to be two comment-blind source greps with a
    # capture that was never wired): the handler map the pump actually hands the model.
    sent = _FakeChatService.instances[-1].gemini.stream_calls[0]
    handlers = set((sent.get("tool_handlers") or {}).keys())
    allowed = set(tools_for_asset_type("INDEX"))
    assert handlers == allowed, handlers ^ allowed
    assert "get_analyst_analysis" not in handlers and "explain_price_move" not in handlers
    # ...and the filter is what removed them: the unfiltered map is strictly larger.
    assert set(build_chat_tool_handlers(_FakeChatService.instances[-1])) > handlers
    # Every DECLARED tool has a handler and vice versa — the two tables cannot drift.
    declared = {fd.name for t in (sent.get("tools") or []) for fd in (t.function_declarations or [])}
    assert declared == handlers, declared ^ handlers


# ── F03-6: a LOST REPLY on the insert is not a lost write ─────────────────────────


def _commit_then_raise_db(base, exc):
    """`chat_messages` INSERT commits the rows and THEN raises (an edge 520 on the reply);
    a later SELECT by id finds them."""
    class _DB(_FakeDB):
        def table(self, name):
            q = super().table(name)
            if name == "chat_messages":
                db = self
                q.filters = []

                def eq(col, val):
                    q.filters.append((col, val)); return q
                q.eq = eq

                def execute():
                    if q.op == "insert":
                        rows = q.payload if isinstance(q.payload, list) else [q.payload]
                        db.inserted_messages.extend(dict(r) for r in rows)
                        db.calls.append(("chat_messages", "insert", q.payload))
                        raise exc
                    if q.op == "select":
                        wanted = dict(q.filters).get("id")
                        return _Result([r for r in db.inserted_messages if r.get("id") == wanted])
                    return _Result([])
                q.execute = execute
            return q
    out = _DB(base.session_row)
    out.calls, out.inserted_messages = base.calls, base.inserted_messages
    return out


@pytest.mark.parametrize("door", ["stream", "send"])
def test_a_transient_failure_on_a_committed_insert_is_delivered_not_refunded(harness, door):
    """The rows committed; only the reply was lost. Refunding here charged the re-send
    twice and left history with two identical exchanges, the first unacknowledged."""
    client, db, quota, _ = harness
    app.dependency_overrides[get_supabase] = lambda: _commit_then_raise_db(db, _gateway_520())
    if door == "stream":
        r = _post(client)
        names = [f[0] for f in _parse_sse(r.text)]
        assert names[-1] == "done", names
    else:
        _FakeChatService.fallback_result = {"content": "Plain answer. " * 3, "tokens_used": 30}
        r = client.post(f"/api/v1/chat/sessions/{_SESSION}/messages", json={"message": "hi"})
        assert r.status_code == 200, r.text
    assert quota.delivered == 1 and quota.refunds == [], quota.every_call
    assistant = [m for m in db.inserted_messages if m.get("role") == "assistant"]
    assert len(assistant) == 1 and assistant[0].get("id"), "ids must be pre-minted"


@pytest.mark.parametrize("door", ["stream", "send"])
def test_a_transient_failure_on_an_uncommitted_insert_still_refunds(harness, door):
    """The re-read finds nothing → the write never landed → the existing refund stands."""
    client, db, quota, _ = harness
    app.dependency_overrides[get_supabase] = lambda: _raising_db(db, "chat_messages", _gateway_520())
    if door == "stream":
        r = _post(client)
        assert [f[0] for f in _parse_sse(r.text)][-1] == "error"
        assert quota.refunds == ["chat_stream_persist_failed"]
    else:
        _FakeChatService.fallback_result = {"content": "Plain answer. " * 3, "tokens_used": 30}
        r = client.post(f"/api/v1/chat/sessions/{_SESSION}/messages", json={"message": "hi"})
        assert r.status_code != 200
        assert quota.refunds == ["chat_undelivered"]
    assert quota.delivered == 0


def test_the_non_stream_door_persists_the_same_rich_content_shape_as_the_stream_door(harness):
    """F03-9 / F01-7: thinking + sources + widget(s), never a bare `{"widget": w}`."""
    client, db, quota, _ = harness
    _FakeChatService.fallback_result = {
        "content": "Plain answer. " * 3, "tokens_used": 30,
        "sources": [{"kind": "screen", "label": "Stock detail", "detail": "AAPL"}],
        "widget": {"widget_type": "stock_chart", "ticker": "AAPL"},
    }
    r = client.post(f"/api/v1/chat/sessions/{_SESSION}/messages", json={"message": "hi"})
    assert r.status_code == 200, r.text
    assistant = [m for m in db.inserted_messages if m.get("role") == "assistant"][0]
    rich = assistant["rich_content"]
    assert set(rich) >= {"thinking", "sources", "widget", "widgets"}, rich
    assert rich["thinking"]["source_count"] == 1 and rich["thinking"]["stages"] == []
    assert isinstance(rich["thinking"]["elapsed_ms"], int)
    assert rich["widgets"] == [rich["widget"]]


# ── E7: the grounded asset's card once per session ──────────────────────────
# The tester: "It doesn't need to show the price chart for the second question. Only
# show at the first question only." The base card was appended on EVERY streamed turn.

_DOGE_CARD = {"widget_type": "stock_chart", "ticker": "DOGEUSD", "company_name": "Dogecoin",
              "current_price": 0.09, "change": 0.0, "change_percent": 3.3, "day_high": 0.0,
              "day_low": 0.0, "volume": 1, "avg_volume": 0, "historical_data": []}


def _doge_session(db):
    db.session_row.update({"session_type": "STOCK", "stock_id": "DOGE",
                           "context_type": "CRYPTO", "reference_id": "DOGE"})


def test_the_base_card_is_attached_on_the_first_turn(harness):
    client, db, quota, _ = harness
    _doge_session(db)
    db.session_row["message_count"] = 0
    _FakeChatService.prep_overrides = {"widget": dict(_DOGE_CARD), "asset_type": "CRYPTO"}
    r = _post(client, message="Who maintains DOGE?")
    assert r.status_code == 200, r.text
    assert [w["ticker"] for w in _done_widgets(r)] == ["DOGEUSD"]


@pytest.mark.parametrize("count", [2, 4, "2", 10])
def test_the_base_card_is_not_attached_on_a_later_turn(harness, count):
    client, db, quota, _ = harness
    _doge_session(db)
    db.session_row["message_count"] = count
    _FakeChatService.prep_overrides = {"widget": dict(_DOGE_CARD), "asset_type": "CRYPTO"}
    r = _post(client, message="what is doge's market cap?")
    assert r.status_code == 200, r.text
    assert _done_widgets(r) == []
    # The quote still reached the model: prep (which folds the LIVE QUOTE line into the
    # instruction) ran exactly as before.
    assert _FakeChatService.instances[-1].prep_calls, "prep must still build the card/quote line"
    assistant = [m for m in db.inserted_messages if m.get("role") == "assistant"][0]
    assert "widget" not in assistant["rich_content"], "history replays the same decision"


@pytest.mark.parametrize("count", [None, "", "garbage", -1])
def test_a_malformed_message_count_reads_as_a_first_turn(harness, count):
    """Degrade to showing the card, never to never showing it."""
    client, db, quota, _ = harness
    _doge_session(db)
    db.session_row["message_count"] = count
    _FakeChatService.prep_overrides = {"widget": dict(_DOGE_CARD), "asset_type": "CRYPTO"}
    r = _post(client, message="Who maintains DOGE?")
    assert r.status_code == 200, r.text
    assert [w["ticker"] for w in _done_widgets(r)] == ["DOGEUSD"]


def test_a_tool_card_for_another_ticker_still_attaches_on_a_later_turn(harness):
    client, db, quota, _ = harness
    _doge_session(db)
    db.session_row["message_count"] = 2
    _FakeChatService.prep_overrides = {"widget": dict(_DOGE_CARD), "asset_type": "CRYPTO"}
    _FakeChatService.events = [
        ("tool", {"name": "get_stock_chart_data", "args": {"ticker": "BTCUSD"},
                  "result": {**_DOGE_CARD, "ticker": "BTCUSD", "company_name": "Bitcoin"}}),
        ("answer", "Bitcoin is larger."),
    ]
    r = _post(client, message="how does it compare to bitcoin?")
    assert r.status_code == 200, r.text
    assert [w["ticker"] for w in _done_widgets(r)] == ["BTCUSD"]


def test_a_tool_card_for_the_screen_asset_is_dropped_on_a_later_turn(harness):
    """The model re-fetched the grounded coin's own chart on turn two: the card the
    user already has must not come back under a one-line answer."""
    client, db, quota, _ = harness
    _doge_session(db)
    db.session_row["message_count"] = 2
    _FakeChatService.prep_overrides = {"widget": dict(_DOGE_CARD), "asset_type": "CRYPTO"}
    _FakeChatService.events = [
        ("tool", {"name": "get_stock_chart_data", "args": {"ticker": "DOGEUSD"},
                  "result": dict(_DOGE_CARD)}),
        ("answer", "Dogecoin's market cap is $13.49 billion."),
    ]
    r = _post(client, message="what is doge's market cap?")
    assert r.status_code == 200, r.text
    assert _done_widgets(r) == []


def test_a_tool_card_for_the_screen_asset_on_the_first_turn_is_deduped_not_doubled(harness):
    client, db, quota, _ = harness
    _doge_session(db)
    db.session_row["message_count"] = 0
    _FakeChatService.prep_overrides = {"widget": dict(_DOGE_CARD), "asset_type": "CRYPTO"}
    _FakeChatService.events = [
        ("tool", {"name": "get_stock_chart_data", "args": {"ticker": "DOGEUSD"},
                  "result": dict(_DOGE_CARD)}),
        ("answer", "Dogecoin trades around nine cents."),
    ]
    r = _post(client, message="what's the price?")
    assert r.status_code == 200, r.text
    assert [w["ticker"] for w in _done_widgets(r)] == ["DOGEUSD"]


def test_an_ungrounded_session_keeps_every_tool_card(harness):
    """No base card → nothing to suppress: a general chat that asks about AAPL twice
    gets its tool card both times."""
    client, db, quota, _ = harness
    db.session_row["message_count"] = 6
    _FakeChatService.events = [
        ("tool", {"name": "get_stock_chart_data", "args": {"ticker": "AAPL"},
                  "result": {**_DOGE_CARD, "ticker": "AAPL", "company_name": "Apple"}}),
        ("answer", "Apple is doing fine."),
    ]
    r = _post(client)
    assert r.status_code == 200, r.text
    assert [w["ticker"] for w in _done_widgets(r)] == ["AAPL"]


def test_the_fallback_honours_the_first_turn_verdict(harness):
    client, db, quota, _ = harness
    _doge_session(db)
    db.session_row["message_count"] = 2
    _FakeChatService.fallback_result = {"content": "Plain answer. " * 3, "tokens_used": 30}
    async def _boom(*a, **k):
        yield ("answer", "partial…")
        raise RuntimeError("stream died")
    orig = _FakeGemini.stream_agentic
    _FakeGemini.stream_agentic = lambda self, prompt, **kw: _boom()
    try:
        r = _post(client, message="what is doge's market cap?")
    finally:
        _FakeGemini.stream_agentic = orig
    assert r.status_code == 200, r.text
    assert _FakeChatService.instances[-1].fallback_kwargs["attach_base_widget"] is False


def test_the_fallback_on_a_first_turn_asks_for_the_card(harness):
    client, db, quota, _ = harness
    _doge_session(db)
    db.session_row["message_count"] = 0
    _FakeChatService.fallback_result = {"content": "Plain answer. " * 3, "tokens_used": 30}
    async def _boom(*a, **k):
        yield ("answer", "partial…")
        raise RuntimeError("stream died")
    orig = _FakeGemini.stream_agentic
    _FakeGemini.stream_agentic = lambda self, prompt, **kw: _boom()
    try:
        r = _post(client, message="Who maintains DOGE?")
    finally:
        _FakeGemini.stream_agentic = orig
    assert r.status_code == 200, r.text
    assert _FakeChatService.instances[-1].fallback_kwargs["attach_base_widget"] is True


@pytest.mark.parametrize("count,expected", [(0, True), (2, False), (None, True), ("x", True)])
def test_the_non_stream_door_passes_the_same_first_turn_verdict(harness, count, expected):
    client, db, quota, _ = harness
    _doge_session(db)
    db.session_row["message_count"] = count
    _FakeChatService.fallback_result = {"content": "Plain answer. " * 3, "tokens_used": 30}
    r = client.post(f"/api/v1/chat/sessions/{_SESSION}/messages", json={"message": "hi"})
    assert r.status_code == 200, r.text
    assert _FakeChatService.instances[-1].fallback_kwargs["attach_base_widget"] is expected


# ── E6: the session LIST — error contract + has_more paging ──────────────────
# The history panel fetched one `limit=50` page and the tester's account had grown past
# it, so the oldest chats fell off the list with nothing saying so; a failed refresh
# surfaced as a bare 500 the client could not read.

def _list_db(base, rows):
    class _ListDB(_FakeDB):
        def table(self, name):
            q = super().table(name)
            if name == "chat_sessions":
                captured = {}

                def _range(lo, hi):
                    captured["range"] = (lo, hi)
                    return q

                def _exec():
                    self.calls.append(("chat_sessions", "select-list", captured.get("range")))
                    lo, hi = captured.get("range", (0, len(rows)))
                    return _Result(rows[lo:hi + 1])
                q.range = _range
                q.execute = _exec
            return q
    return _ListDB(base.session_row)


def _session_rows(n):
    return [{
        "id": f"s-{i:03d}", "title": f"Chat {i}", "session_type": "NORMAL", "stock_id": None,
        "context_type": None, "reference_id": None, "preview_message": None,
        "message_count": 2, "is_saved": False,
        "created_at": "2026-09-01T00:00:00+00:00", "last_message_at": f"2026-09-{1 + i % 28:02d}T00:00:00+00:00",
    } for i in range(n)]


def test_a_failed_session_list_returns_the_error_contract_not_a_500(harness):
    client, db, quota, _ = harness
    app.dependency_overrides[get_supabase] = lambda: _raising_db(db, "chat_sessions", _gateway_520())
    r = client.get("/api/v1/chat/sessions?limit=50&offset=0")
    assert r.status_code == 409, r.text
    body = r.json()
    assert body["error_code"] == "SYSTEM_BUSY"
    assert body["details"]["step"] == "chat_session_list"
    assert body["user_message"]


def test_a_deterministic_session_list_failure_is_the_same_contract(harness, caplog):
    import logging
    client, db, quota, _ = harness
    app.dependency_overrides[get_supabase] = lambda: _raising_db(db, "chat_sessions", _pgrst("42P01"))
    with caplog.at_level(logging.ERROR, logger="app.api.v1.endpoints.chat"):
        r = client.get("/api/v1/chat/sessions")
    assert r.status_code == 409 and r.json()["error_code"] == "SYSTEM_BUSY"
    assert any("chat_sessions list failed" in rec.getMessage() for rec in caplog.records), \
        "a deterministic failure is logged at ERROR with its type"


@pytest.mark.parametrize("n,limit,offset,expect_len,expect_more", [
    (57, 50, 0, 50, True),     # the tester's account: 57 sessions, one 50-row page
    (57, 50, 50, 7, False),    # the second page closes the walk
    (50, 50, 0, 50, False),    # exactly one page — no phantom next page
    (0, 50, 0, 0, False),
    (1, 50, 0, 1, False),
    (3, 2, 0, 2, True),
    (3, 2, 2, 1, False),
    (3, 2, 4, 0, False),       # past the end
])
def test_session_list_pages_with_an_exact_has_more_probe(harness, n, limit, offset, expect_len, expect_more):
    client, db, quota, _ = harness
    app.dependency_overrides[get_supabase] = lambda: _list_db(db, _session_rows(n))
    r = client.get(f"/api/v1/chat/sessions?limit={limit}&offset={offset}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["sessions"]) == expect_len
    assert body["total"] == expect_len, "`total` keeps its page-length meaning for old builds"
    assert body["has_more"] is expect_more
    ids = [s["id"] for s in body["sessions"]]
    assert ids == [f"s-{i:03d}" for i in range(offset, min(n, offset + limit))], "no row skipped or doubled"


def test_the_probe_row_is_never_returned(harness):
    """`limit + 1` rows are fetched; exactly `limit` may leave the server."""
    client, db, quota, _ = harness
    listdb = _list_db(db, _session_rows(12))
    app.dependency_overrides[get_supabase] = lambda: listdb
    r = client.get("/api/v1/chat/sessions?limit=5&offset=0")
    assert len(r.json()["sessions"]) == 5
    rng = [c for c in listdb.calls if c[1] == "select-list"][0][2]
    assert rng == (0, 5), "range must ask for limit+1 rows (0..5 inclusive)"


# ── E1: the join between a cut answer and its continuation ───────────────────
# First live run under a forced cap: "printing" + "more" rendered "printingmore".

@pytest.mark.parametrize("partial,cont,expected", [
    ("a country printing", "more money", " more money"),          # word boundary → space
    ("For Polygon (MATIC), the", " supply is uncapped", " supply is uncapped"),  # already spaced
    ("ends with a period.", "Next sentence", " Next sentence"),
    ("market cap is $13.", "49 billion", "49 billion"),           # a decimal seam is one number
    ("about 13,", "000 coins", "000 coins"),
    ("value of 13", ".49", ".49"),                                 # a digit + '.' is not a word edge
    ("printing\n", "more", "more"),                                # partial ends in whitespace
    ("printing", "\nmore", "\nmore"),                              # continuation opens with whitespace
    ("**Proof of Work", "**: the miners", " **: the miners"),
    ("", "anything", "anything"),
    ("anything", "", ""),
    ("the supply is uncapped, so it is", "the supply is uncapped, so it is mildly inflationary",
     " mildly inflationary"),                                      # repeated tail trimmed (≥12 chars)
    ("THE SUPPLY IS UNCAPPED", "the supply is uncapped and growing", " and growing"),  # case-insensitive
    ("supply is", "supply is uncapped", " supply is uncapped"),   # 9-char overlap: too short to trust
])
def test_join_continuation_repairs_the_seam(partial, cont, expected):
    assert chat_mod._join_continuation(partial, cont) == expected


def test_join_continuation_mid_word_cut_is_the_accepted_cost():
    """A ceiling that lands INSIDE a multi-token word gets a spurious space. Documented
    rather than special-cased: cuts land at token boundaries and tokens carry their
    leading space, so the word-end case is the common one and the prompt tells the
    model to finish the word first."""
    assert chat_mod._join_continuation("deflationa", "ry") == " ry"


def test_the_continuation_is_joined_before_it_reaches_the_bubble(harness):
    client, db, quota, _ = harness
    _FakeChatService.events = [("answer", "imagine a country printing"), ("finish", "MAX_TOKENS")]
    _FakeGemini.continue_events = [("answer", "more"), ("answer", " money every year.")]
    r = _post(client, message="Is DOGE inflationary?")
    assert r.status_code == 200, r.text
    frames = _parse_sse(r.text)
    done = frames[-1][1]["message"]
    assert done["content"] == "imagine a country printing more money every year."
    tokens = [d["delta"] for e, d in frames if e == "token"]
    assert " more money every year." in tokens, "the first chunks are buffered and joined once"
    assert quota.settled == []


def test_a_repeated_tail_in_the_continuation_is_trimmed(harness):
    client, db, quota, _ = harness
    _FakeChatService.events = [("answer", "Dogecoin has no supply cap, so it is"), ("finish", "MAX_TOKENS")]
    _FakeGemini.continue_events = [("answer", "no supply cap, so it is"), ("answer", " mildly inflationary.")]
    r = _post(client, message="Is DOGE inflationary?")
    assert r.status_code == 200, r.text
    done = _parse_sse(r.text)[-1][1]["message"]
    assert done["content"] == "Dogecoin has no supply cap, so it is mildly inflationary."


def test_a_short_continuation_that_never_fills_the_buffer_is_still_delivered(harness):
    client, db, quota, _ = harness
    _FakeChatService.events = [("answer", "the answer is"), ("finish", "MAX_TOKENS")]
    _FakeGemini.continue_events = [("answer", "yes.")]
    r = _post(client, message="Is DOGE inflationary?")
    assert r.status_code == 200, r.text
    done = _parse_sse(r.text)[-1][1]["message"]
    assert done["content"] == "the answer is yes."
    assert done.get("truncated") is None


# ── review findings (2026-09-19): a cut is continued and chipped ONLY when it was the
# output ceiling; `no_tools` outranks `truncated` for the ledger; the chip rides the insert.

@pytest.mark.parametrize("reason", ["SAFETY", "RECITATION", "OTHER", "FINISH_REASON_SAFETY"])
def test_a_non_length_cut_is_marked_and_refunded_but_neither_continued_nor_chipped(harness, reason):
    """Asking the model to resume a passage the safety / recitation filter stopped is a
    wasted capped round with a false premise, and a Continue chip after it is a dead end
    (the E3 rule). The turn is still marked and refunded like any cut."""
    client, db, quota, _ = harness
    _FakeChatService.events = [("answer", "Some text the filter stopped"), ("finish", reason)]
    _FakeGemini.continue_events = [("answer", " never used")]
    r = _post(client, message="tell me about it")
    assert r.status_code == 200, r.text
    assert _FakeGemini.continue_calls == [], "no continuation past a non-length stop"
    done = _parse_sse(r.text)[-1][1]["message"]
    assert done["truncated"] is True
    assert done.get("suggestions") is None, "no Continue chip, and no model chips off a half answer"
    assert _FakeChatService.instances[-1].suggestion_calls == 0
    assert quota.settled == ["chat_degraded_truncated"]
    assistant = [m for m in db.inserted_messages if m.get("role") == "assistant"][0]
    assert assistant["rich_content"]["finish_reason"] == reason
    assert "suggestions" not in assistant["rich_content"]


def test_the_non_stream_door_gives_a_safety_cut_no_continue_chip(harness):
    client, db, quota, _ = harness
    _FakeChatService.fallback_result = {
        "content": "Some text the filter stopped", "tokens_used": 30,
        "degraded": "truncated", "truncated": True, "finish_reason": "SAFETY",
    }
    r = client.post(f"/api/v1/chat/sessions/{_SESSION}/messages", json={"message": "hi"})
    assert r.status_code == 200, r.text
    assert r.json()["truncated"] is True and r.json().get("suggestions") is None
    assert quota.settled == ["chat_degraded_truncated"]


def test_the_continue_chip_rides_the_atomic_insert_on_the_stream_door(harness):
    """Known before the write, so a disconnect after the insert cannot leave a cut row
    with no way out — the same shape the non-stream door persists."""
    client, db, quota, _ = harness
    _FakeChatService.events = list(_CUT_EVENTS)          # continuation yields nothing → still cut
    r = _post(client, message="Is MATIC inflationary?")
    assert r.status_code == 200, r.text
    assistant = [m for m in db.inserted_messages if m.get("role") == "assistant"][0]
    assert assistant["rich_content"]["suggestions"] == [chat_mod._CONTINUE_CHIP]
    updates = [c for c in db.calls if c[0] == "chat_messages" and c[1] == "update"
               and isinstance(c[2], dict) and "suggestions" in (c[2].get("rich_content") or {})]
    assert updates == [], "no follow-up write for a chip that was already inserted"
    done = _parse_sse(r.text)[-1][1]["message"]
    assert done["suggestions"] == [chat_mod._CONTINUE_CHIP]


def test_every_tool_failing_outranks_the_truncation_label_for_the_ledger(harness):
    """The non-stream door labels an all-tools-failed answer `no_tools` first-wins; the
    stream door's `finish` handler used to claim the slot first, so the same shape was
    refunded under a different reason per door. The truncation MARK is unaffected."""
    client, db, quota, _ = harness
    _FakeChatService.events = [
        ("tool", {"name": "get_stock_chart_data", "args": {"ticker": "AAPL"},
                  "result": {"error": "timed_out", "tool": "get_stock_chart_data", "upstream": True}}),
        ("answer", "Apple is doing fine, from memory, but the"),
        ("finish", "MAX_TOKENS"),
    ]
    r = _post(client)
    assert r.status_code == 200, r.text
    assert quota.settled == ["chat_degraded_no_tools"]
    done = _parse_sse(r.text)[-1][1]["message"]
    assert done["truncated"] is True and done["suggestions"] == [chat_mod._CONTINUE_CHIP]


def test_every_tool_failing_still_refunds_when_the_continuation_completes(harness):
    client, db, quota, _ = harness
    _FakeChatService.events = [
        ("tool", {"name": "get_stock_chart_data", "args": {"ticker": "AAPL"},
                  "result": {"error": "timed_out", "tool": "get_stock_chart_data", "upstream": True}}),
        ("answer", "Apple is doing fine, from memory, but the"),
        ("finish", "MAX_TOKENS"),
    ]
    _FakeGemini.continue_events = [("answer", " chart could not be fetched.")]
    r = _post(client)
    assert r.status_code == 200, r.text
    assert quota.settled == ["chat_degraded_no_tools"], "none of its live data, whatever the length"
    done = _parse_sse(r.text)[-1][1]["message"]
    assert done.get("truncated") is None


def test_the_answer_model_is_handed_to_the_thinking_budget_resolver(harness, monkeypatch):
    """The cheap route (`CHAT_CHEAP_MODEL`) does not think unless a budget is attached, so
    the flagship's ceiling must not be forwarded to it."""
    from app.config import settings
    import app.services.agents.chat_router as router
    client, db, quota, _ = harness
    monkeypatch.setattr(router, "select_model", lambda *a, **k: settings.CHAT_CHEAP_MODEL)
    r = _post(client)
    assert r.status_code == 200, r.text
    call = _FakeChatService.instances[-1].gemini.stream_calls[-1]
    assert call["model_name"] == settings.CHAT_CHEAP_MODEL
    assert call["thinking_budget"] is None


# ── review finding (2026-09-19): a first-turn card fetch that failed must not leave the
# session cardless forever — a later turn asks the rows, not the counter.

def test_a_later_turn_gets_the_card_when_no_earlier_answer_rendered_one(harness):
    client, db, quota, _ = harness
    _doge_session(db)
    db.session_row["message_count"] = 2
    db.prior_card = False                      # turn 1's fetch timed out: cardless row
    _FakeChatService.prep_overrides = {"widget": dict(_DOGE_CARD), "asset_type": "CRYPTO"}
    r = _post(client, message="what is doge's market cap?")
    assert r.status_code == 200, r.text
    assert [w["ticker"] for w in _done_widgets(r)] == ["DOGEUSD"]
    assert any(c[1] == "select-card-probe" for c in db.calls), "the rows were asked"


def test_the_card_probe_is_skipped_on_the_first_turn_and_on_ungrounded_sessions(harness):
    client, db, quota, _ = harness
    db.session_row["message_count"] = 4        # ungrounded (no stock_id / reference_id)
    r = _post(client)
    assert r.status_code == 200, r.text
    assert not any(c[1] == "select-card-probe" for c in db.calls)
    db.calls.clear()
    _doge_session(db)
    db.session_row["message_count"] = 0
    _FakeChatService.prep_overrides = {"widget": dict(_DOGE_CARD), "asset_type": "CRYPTO"}
    r = _post(client, message="Who maintains DOGE?")
    assert r.status_code == 200, r.text
    assert not any(c[1] == "select-card-probe" for c in db.calls), "the counter already answers"


def test_a_failing_card_probe_falls_back_to_the_count_rule(harness):
    client, db, quota, _ = harness
    _doge_session(db)
    db.session_row["message_count"] = 2
    _FakeChatService.prep_overrides = {"widget": dict(_DOGE_CARD), "asset_type": "CRYPTO"}

    class _ProbeBoom(_FakeDB):
        def table(self, name):
            q = super().table(name)
            if name == "chat_messages":
                orig_is = q.is_

                def _is(column, value):
                    orig_is(column, value)
                    if q.op == "select-card-probe":
                        def _boom():
                            raise RuntimeError("probe exploded")
                        q.execute = _boom
                    return q
                q.is_ = _is
            return q
    boom = _ProbeBoom(db.session_row)
    app.dependency_overrides[get_supabase] = lambda: boom
    r = _post(client, message="what is doge's market cap?")
    assert r.status_code == 200, r.text
    assert _done_widgets(r) == [], "unknown → no card, never a guess"


def test_the_non_stream_door_asks_the_rows_too(harness):
    client, db, quota, _ = harness
    _doge_session(db)
    db.session_row["message_count"] = 2
    db.prior_card = False
    _FakeChatService.fallback_result = {"content": "Plain answer. " * 3, "tokens_used": 30}
    r = client.post(f"/api/v1/chat/sessions/{_SESSION}/messages", json={"message": "hi"})
    assert r.status_code == 200, r.text
    assert _FakeChatService.instances[-1].fallback_kwargs["attach_base_widget"] is True
