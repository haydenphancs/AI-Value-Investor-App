"""Offline tests for the Phase-2 agentic streaming loop.

  * GeminiClient.stream_agentic — the multi-round manual-FC streaming loop (mocked SDK: a fake
    AsyncChat whose send_message_stream returns canned chunks with thought / answer / function_call
    parts). Pins: thought vs answer routing, tool events, handler invocation, feeding the response
    back next round, and the final-answer round when max_rounds is exhausted.
  * chat_tools — declarations count + the renderable-widget extraction / dedup helpers.

No network.
"""

from types import SimpleNamespace

import pytest

from google.genai import types

from app.integrations import gemini as gem
from app.services.agents import chat_tools


# ── Fakes mirroring the streamed-chunk shape ────────────────────────────────

class _FakePart:
    def __init__(self, text=None, thought=False, function_call=None):
        self._t = text
        self.thought = thought
        self.function_call = function_call

    @property
    def text(self):
        if self._t is None:
            raise ValueError("no text in this part")
        return self._t


class _FakeFC:
    def __init__(self, name, args):
        self.name = name
        self.args = args


def _chunk(*parts):
    return SimpleNamespace(candidates=[SimpleNamespace(content=SimpleNamespace(parts=list(parts)))])


class _FakeChat:
    """Returns one canned stream (list of chunks) per send_message_stream call."""
    def __init__(self, rounds):
        self._rounds = rounds
        self._i = 0
        self.sent = []

    async def send_message_stream(self, message):
        self.sent.append(message)
        chunks = self._rounds[min(self._i, len(self._rounds) - 1)]
        self._i += 1

        async def _gen():
            for c in chunks:
                yield c

        return _gen()


class _FakeChats:
    def __init__(self, chat):
        self._chat = chat

    def create(self, *, model, config):
        return self._chat


class _FakeClient:
    def __init__(self, chat):
        self.aio = SimpleNamespace(chats=_FakeChats(chat))


def _client(chat) -> gem.GeminiClient:
    c = gem.GeminiClient.__new__(gem.GeminiClient)
    c.model_name = "gemini-2.5-flash"
    c._temperature = 0.7
    c._max_tokens = 128
    c._client = _FakeClient(chat)
    gem._quota_circuit.record_success()
    return c


_TOOL = types.Tool(function_declarations=[types.FunctionDeclaration(
    name="get_x", description="x",
    parameters=types.Schema(type=types.Type.OBJECT,
                            properties={"ticker": types.Schema(type=types.Type.STRING)}, required=["ticker"]))])


# ── stream_agentic ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_stream_agentic_runs_tool_then_answers():
    fc = _FakeFC("get_x", {"ticker": "AAPL"})
    chat = _FakeChat(rounds=[
        [_chunk(_FakePart(text="thinking", thought=True), _FakePart(function_call=fc))],  # round 0
        [_chunk(_FakePart(text="Answer here."))],                                          # round 1
    ])
    c = _client(chat)
    ran = {}

    async def handler(args):
        ran["args"] = args
        return {"widget_type": "stock_chart", "ticker": args["ticker"]}

    events = [ev async for ev in c.stream_agentic("prompt", tools=[_TOOL], tool_handlers={"get_x": handler})]

    assert ("thought", "thinking") in events
    assert ("answer", "Answer here.") in events
    tool_payloads = [p for k, p in events if k == "tool"]
    assert tool_payloads and tool_payloads[0]["name"] == "get_x"
    assert tool_payloads[0]["result"]["widget_type"] == "stock_chart"
    assert ran["args"] == {"ticker": "AAPL"}
    # Round 1 was sent the function-response parts (a list), not the raw prompt.
    assert isinstance(chat.sent[1], list)


@pytest.mark.asyncio
async def test_stream_agentic_no_tools_just_answers():
    chat = _FakeChat(rounds=[[_chunk(_FakePart(text="Direct answer."))]])
    c = _client(chat)
    events = [ev async for ev in c.stream_agentic("prompt", tools=[_TOOL], tool_handlers={})]
    assert events == [("answer", "Direct answer.")]
    assert len(chat.sent) == 1  # only one round; no tool feedback


@pytest.mark.asyncio
async def test_stream_agentic_unknown_tool_degrades():
    fc = _FakeFC("mystery", {"ticker": "AAPL"})
    chat = _FakeChat(rounds=[
        [_chunk(_FakePart(function_call=fc))],
        [_chunk(_FakePart(text="ok"))],
    ])
    c = _client(chat)
    events = [ev async for ev in c.stream_agentic("prompt", tools=[_TOOL], tool_handlers={})]
    tool_payloads = [p for k, p in events if k == "tool"]
    assert tool_payloads[0]["result"] == {"error": "unknown tool: mystery"}
    assert ("answer", "ok") in events


@pytest.mark.asyncio
async def test_stream_agentic_handler_exception_becomes_error_result():
    fc = _FakeFC("get_x", {"ticker": "AAPL"})
    chat = _FakeChat(rounds=[[_chunk(_FakePart(function_call=fc))], [_chunk(_FakePart(text="done"))]])
    c = _client(chat)

    async def boom(args):
        raise RuntimeError("fmp down")

    events = [ev async for ev in c.stream_agentic("prompt", tools=[_TOOL], tool_handlers={"get_x": boom})]
    tool_payloads = [p for k, p in events if k == "tool"]
    assert "fmp down" in tool_payloads[0]["result"]["error"]
    assert ("answer", "done") in events  # a failed tool doesn't abort the turn


@pytest.mark.asyncio
async def test_stream_agentic_final_round_when_rounds_exhausted():
    # The model calls a tool EVERY round; with max_rounds=2 the loop runs a final answer round.
    fc = _FakeFC("get_x", {"ticker": "AAPL"})
    chat = _FakeChat(rounds=[
        [_chunk(_FakePart(function_call=fc))],  # round 0
        [_chunk(_FakePart(function_call=fc))],  # round 1
        [_chunk(_FakePart(text="final answer"))],  # final answer round
    ])
    c = _client(chat)

    async def handler(args):
        return {"ok": True}

    events = [ev async for ev in c.stream_agentic(
        "prompt", tools=[_TOOL], tool_handlers={"get_x": handler}, max_rounds=2)]
    assert ("answer", "final answer") in events
    assert len(chat.sent) == 3  # 2 tool rounds + 1 final answer round


@pytest.mark.asyncio
async def test_stream_agentic_fails_fast_when_circuit_open(monkeypatch):
    chat = _FakeChat(rounds=[[_chunk(_FakePart(text="x"))]])
    c = _client(chat)
    monkeypatch.setattr(gem._quota_circuit, "is_open", lambda: True)
    with pytest.raises(gem.GeminiQuotaError):
        async for _ in c.stream_agentic("prompt", tools=[_TOOL], tool_handlers={}):
            pass


# ── chat_tools helpers ──────────────────────────────────────────────────────

def _tool_names(asset_type=None):
    return {
        fd.name
        for t in chat_tools.build_chat_tool_declarations(asset_type)
        for fd in (t.function_declarations or [])
    }


def _finish_chunk(reason, *parts):
    """A terminal chunk: the candidate carries `finish_reason` (only the last chunk does)."""
    return SimpleNamespace(candidates=[SimpleNamespace(
        content=SimpleNamespace(parts=list(parts)), finish_reason=SimpleNamespace(name=reason))])


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["MAX_TOKENS", "SAFETY", "RECITATION"])
async def test_a_stream_cut_after_answer_text_yields_a_finish_marker(reason):
    """The model CUT the answer after real text. It used to end cleanly — charged in full
    and, for a deep dive, cached for every user for 24 h with its last sentence missing."""
    chat = _FakeChat(rounds=[[_chunk(_FakePart(text="The Fed will likely **")),
                              _finish_chunk(reason)]])
    events = [ev async for ev in _client(chat).stream_agentic("prompt", tools=[_TOOL], tool_handlers={})]
    assert events == [("answer", "The Fed will likely **"), ("finish", reason)]


@pytest.mark.asyncio
async def test_a_clean_stop_yields_no_finish_marker():
    chat = _FakeChat(rounds=[[_chunk(_FakePart(text="Done.")), _finish_chunk("STOP")]])
    events = [ev async for ev in _client(chat).stream_agentic("prompt", tools=[_TOOL], tool_handlers={})]
    assert events == [("answer", "Done.")]


@pytest.mark.asyncio
async def test_a_cut_inside_thinking_only_is_still_an_empty_answer_not_a_marker():
    """No answer text streamed: the endpoint's "empty stream result" → fallback path must
    keep owning this case (a marker with nothing to keep would be a refund for nothing)."""
    chat = _FakeChat(rounds=[[_chunk(_FakePart(text="thinking…", thought=True)),
                              _finish_chunk("MAX_TOKENS")]])
    events = [ev async for ev in _client(chat).stream_agentic("prompt", tools=[_TOOL], tool_handlers={})]
    assert events == [("thought", "thinking…")]


@pytest.mark.asyncio
async def test_a_cut_on_the_final_round_after_tools_is_marked_too():
    fc = _FakeFC("get_x", {"ticker": "AAPL"})
    chat = _FakeChat(rounds=[
        [_chunk(_FakePart(function_call=fc))],
        [_chunk(_FakePart(text="Apple is up because"), ), _finish_chunk("MAX_TOKENS")],
    ])

    async def handler(args):
        return {"ok": True}
    events = [ev async for ev in _client(chat).stream_agentic(
        "prompt", tools=[_TOOL], tool_handlers={"get_x": handler}, max_rounds=1)]
    kinds = [k for k, _ in events]
    assert kinds == ["tool", "answer", "finish"], events


def test_declarations_default_to_the_full_equity_set(monkeypatch):
    """No asset type = no screen context, so any stock may come up. Also the safe default:
    an unrecognised value must never silently strip a tool.

    The licence filter is pinned ON here because this test is about the ASSET-CLASS default, not
    about entitlements — with the real manifest `get_analyst_analysis` is withheld from every
    chat (see `test_chat_asset_awareness.py` §1b). Patch the binding `chat_tools` actually
    resolves, which is its own module-level one."""
    from app.services.agents import chat_tools as _ct

    monkeypatch.setattr(_ct, "analyst_section_available", lambda: True)
    # The market-awareness tools ride along on the equity default. They were added because the
    # three-tool set below left the model with no news, no sector data and no way to answer a
    # "why did it move" question — which it then said out loud to a user.
    equity = {
        "get_stock_chart_data", "get_analyst_analysis", "get_sentiment_analysis",
        "get_ticker_news", "explain_price_move", "get_market_snapshot",
    }
    assert _tool_names() == equity
    assert _tool_names("STOCK") == equity
    assert _tool_names("NORMAL") == equity
    assert _tool_names("who-knows") == equity


def test_declarations_gate_market_overview():
    """The index tool is offered to INDEX chats and to nothing else."""
    assert "get_market_overview" in _tool_names("INDEX")
    for other in ("STOCK", "NORMAL", "ETF", "CRYPTO", "COMMODITY"):
        assert "get_market_overview" not in _tool_names(other), other


def test_widget_from_tool_result_only_renderable():
    assert chat_tools.widget_from_tool_result({"widget_type": "stock_chart", "ticker": "AAPL"}) is not None
    assert chat_tools.widget_from_tool_result({"widget_type": "market_overview"}) is not None
    assert chat_tools.widget_from_tool_result({"consensus": "BUY"}) is None       # analyst data, not a widget
    assert chat_tools.widget_from_tool_result({"error": "no data"}) is None
    assert chat_tools.widget_from_tool_result("nope") is None


def test_widget_key_dedups_by_type_and_ticker():
    a = chat_tools.widget_key({"widget_type": "stock_chart", "ticker": "aapl"})
    b = chat_tools.widget_key({"widget_type": "stock_chart", "ticker": "AAPL"})
    c = chat_tools.widget_key({"widget_type": "stock_chart", "ticker": "MSFT"})
    assert a == b and a != c


# ── stream_synthesis degradation (adversarial-review fix) ────────────────────

@pytest.mark.asyncio
async def test_stream_synthesis_degrades_to_specialist_answer_when_merge_fails():
    """If the final MERGE call fails (e.g. the quota circuit opens after the specialists finished),
    stream_synthesis must NOT throw away the already-computed specialist answers — it degrades to the
    top one instead of propagating an error (which would leave the user with nothing)."""
    from app.services.chat_service import ChatService

    class _G:
        async def stream_agentic(self, prompt, tools=None, tool_handlers=None,
                                 system_instruction=None, max_rounds=4, model_name=None,
                                 usage_tag=None, max_output_tokens=None):
            yield ("answer", "valuation view: looks cheap")

        async def stream_text(self, prompt, system_instruction=None, model_name=None,
                              usage_tag=None, max_output_tokens=None):
            raise RuntimeError("quota circuit open (resource_exhausted)")
            yield  # pragma: no cover — makes this an async generator

    svc = object.__new__(ChatService)
    svc.gemini = _G()
    prep = {"system_instruction": "sys", "prompt": "p"}
    route = {"specialists": ["valuation", "fundamentals"],
             "labels": ["Valuation", "Fundamentals"], "mode": "synthesize"}

    events = [ev async for ev in svc.stream_synthesis(prep, "is it a buy?", route, tools=[], tool_handlers={})]
    answers = [p for k, p in events if k == "answer"]
    assert any("looks cheap" in a for a in answers)   # a real answer survived the merge failure


# ── generate_with_tools: parallel / multiple function calls (non-streaming path) ──

class _FakeResp:
    """A generate_content response shaped like the bits GeminiClient reads."""
    def __init__(self, parts, text=None, finish="STOP", tokens=7):
        self.candidates = [SimpleNamespace(
            content=SimpleNamespace(parts=list(parts)),
            finish_reason=SimpleNamespace(name=finish),
        )]
        self.usage_metadata = SimpleNamespace(total_token_count=tokens)
        self._text = text

    @property
    def text(self):
        if self._text is None:
            raise ValueError("no text in this response")
        return self._text


class _FakeModels:
    """Returns the canned responses in order; records each call's `contents`."""
    def __init__(self, responses):
        self._responses = list(responses)
        self._i = 0
        self.calls = []

    async def generate_content(self, *, model, contents, config):
        self.calls.append(contents)
        r = self._responses[min(self._i, len(self._responses) - 1)]
        self._i += 1
        return r


def _tools_client(models) -> gem.GeminiClient:
    c = gem.GeminiClient.__new__(gem.GeminiClient)
    c.model_name = "gemini-2.5-flash"
    c._temperature = 0.7
    c._max_tokens = 128
    c._client = SimpleNamespace(aio=SimpleNamespace(models=models))
    gem._quota_circuit.record_success()
    return c


@pytest.mark.asyncio
async def test_generate_with_tools_handles_parallel_function_calls():
    """The confirmed bug: gemini-2.5 can emit MULTIPLE function_call parts in one turn. The loop
    handled only the FIRST and echoed candidate.content (holding ALL the calls) with a SINGLE
    function_response → an N-call/1-response mismatch the API 400s on, silently dropping every
    tool/widget + burning a round-trip. The fix runs every call and sends one response per call."""
    fc1 = _FakeFC("get_stock_chart_data", {"ticker": "AAPL"})
    fc2 = _FakeFC("get_analyst_analysis", {"ticker": "AAPL"})
    first = _FakeResp(parts=[_FakePart(function_call=fc1), _FakePart(function_call=fc2)])
    final = _FakeResp(parts=[_FakePart(text="Chart + ratings.")], text="Chart + ratings.")
    models = _FakeModels([first, final])
    c = _tools_client(models)

    ran = []

    async def chart(args):
        ran.append(("chart", dict(args)))
        return {"widget_type": "stock_chart", "ticker": args["ticker"]}

    async def analyst(args):
        ran.append(("analyst", dict(args)))
        return {"consensus": "BUY"}

    result = await c.generate_with_tools(
        "compare AAPL chart and ratings", tools=[_TOOL],
        tool_handlers={"get_stock_chart_data": chart, "get_analyst_analysis": analyst},
    )

    # BOTH handlers ran (not just the first parallel call).
    assert ("chart", {"ticker": "AAPL"}) in ran
    assert ("analyst", {"ticker": "AAPL"}) in ran
    # A follow-up round was issued and returned the final text; both results are carried.
    assert result["text"] == "Chart + ratings."
    assert len(result["tool_results"]) == 2
    # Crux: the follow-up user turn carried ONE function_response PER call (2), so the response
    # count matches the 2-call model turn — no INVALID_ARGUMENT.
    follow_up_contents = models.calls[1]
    assert len(follow_up_contents[-1].parts) == 2


@pytest.mark.asyncio
async def test_generate_with_tools_unknown_among_parallel_calls_keeps_counts_matched():
    """An unknown handler among several parallel calls still gets an error function_response, so the
    response count keeps matching the call count (rather than being silently dropped → mismatch)."""
    fc1 = _FakeFC("get_stock_chart_data", {"ticker": "MSFT"})
    fc2 = _FakeFC("mystery_tool", {"ticker": "MSFT"})
    first = _FakeResp(parts=[_FakePart(function_call=fc1), _FakePart(function_call=fc2)])
    final = _FakeResp(parts=[_FakePart(text="ok")], text="ok")
    models = _FakeModels([first, final])
    c = _tools_client(models)

    async def chart(args):
        return {"widget_type": "stock_chart", "ticker": args["ticker"]}

    result = await c.generate_with_tools(
        "x", tools=[_TOOL], tool_handlers={"get_stock_chart_data": chart},
    )
    # Only the known handler's result is surfaced, but BOTH calls were answered back to the model.
    assert len(result["tool_results"]) == 1
    assert len(models.calls[1][-1].parts) == 2


# ── stream_agentic: parallel function calls in a SINGLE streamed round ────────

@pytest.mark.asyncio
async def test_stream_agentic_parallel_function_calls_in_one_round():
    """gemini-2.5 can emit MULTIPLE function_call parts in one streamed round. Both handlers must run,
    two ('tool', …) events fire, and the NEXT round is fed one function_response PER call (2) so the
    follow-up doesn't 400 on a call/response count mismatch. The non-streaming generate_with_tools has
    a dedicated parallel-FC test; the streaming path (the one chat actually uses) had none."""
    fc1 = _FakeFC("get_x", {"ticker": "AAPL"})
    fc2 = _FakeFC("get_y", {"ticker": "MSFT"})
    chat = _FakeChat(rounds=[
        [_chunk(_FakePart(function_call=fc1), _FakePart(function_call=fc2))],  # round 0: two calls
        [_chunk(_FakePart(text="both done"))],                                  # round 1: answer
    ])
    c = _client(chat)
    ran = []

    async def hx(args):
        ran.append(("x", dict(args)))
        return {"widget_type": "stock_chart", "ticker": args["ticker"]}

    async def hy(args):
        ran.append(("y", dict(args)))
        return {"widget_type": "stock_chart", "ticker": args["ticker"]}

    events = [ev async for ev in c.stream_agentic(
        "prompt", tools=[_TOOL], tool_handlers={"get_x": hx, "get_y": hy})]

    tool_names = [p["name"] for k, p in events if k == "tool"]
    assert tool_names == ["get_x", "get_y"]           # BOTH parallel calls emitted a tool event
    assert ("x", {"ticker": "AAPL"}) in ran and ("y", {"ticker": "MSFT"}) in ran
    assert ("answer", "both done") in events
    # Round 1 was fed a LIST of two function_response parts (one per call) — count matches the turn.
    assert isinstance(chat.sent[1], list) and len(chat.sent[1]) == 2


# ── stream_synthesis: clean-but-empty merge + all-specialists-fail fallback ───

@pytest.mark.asyncio
async def test_stream_synthesis_clean_but_empty_merge_uses_specialist_answer():
    """The bug: the specialist-salvage lived ONLY in the merge's `except`. If stream_text completes
    WITHOUT raising but emits zero `answer` parts (all-thoughts / MAX_TOKENS-during-thinking /
    safety-filtered empty), stream_synthesis yielded no answer → the endpoint sees empty content and
    burns a THIRD full generation. The salvage now also runs after a clean-but-empty merge."""
    from app.services.chat_service import ChatService

    class _G:
        async def stream_agentic(self, prompt, tools=None, tool_handlers=None,
                                 system_instruction=None, max_rounds=4, model_name=None,
                                 usage_tag=None, max_output_tokens=None):
            yield ("answer", "fundamentals view: solid balance sheet")

        async def stream_text(self, prompt, system_instruction=None, model_name=None,
                              usage_tag=None, max_output_tokens=None):
            # Clean completion, but ONLY a thought — never an answer token.
            yield ("thought", "weighing the two lenses…")

    svc = object.__new__(ChatService)
    svc.gemini = _G()
    prep = {"system_instruction": "sys", "prompt": "p"}
    route = {"specialists": ["valuation", "fundamentals"],
             "labels": ["Valuation", "Fundamentals"], "mode": "synthesize"}
    events = [ev async for ev in svc.stream_synthesis(prep, "is it a buy?", route, tools=[], tool_handlers={})]
    answers = [p for k, p in events if k == "answer"]
    assert any("solid balance sheet" in a for a in answers)   # salvaged despite the clean-empty merge


@pytest.mark.asyncio
async def test_stream_synthesis_all_specialists_fail_falls_back_to_general():
    """When EVERY specialist produces no answer (results == []), stream_synthesis must degrade to a
    single general agentic stream so the user still gets a reply — the 'user always gets an answer'
    guarantee. The general fallback shares the same stream_agentic, so a call counter distinguishes
    the (answerless) specialist runs from the fallback run that comes strictly after the gather."""
    from app.services.chat_service import ChatService

    class _G:
        def __init__(self):
            self.calls = 0

        async def stream_agentic(self, prompt, tools=None, tool_handlers=None,
                                 system_instruction=None, max_rounds=4, model_name=None,
                                 usage_tag=None, max_output_tokens=None):
            self.calls += 1
            n = self.calls
            if n <= 2:                       # the two specialist runs → only a thought → empty answer
                yield ("thought", "specialist thinking")
            else:                            # the general fallback run → a real answer
                yield ("answer", "general fallback answer")

    svc = object.__new__(ChatService)
    svc.gemini = _G()
    prep = {"system_instruction": "sys", "prompt": "p"}
    route = {"specialists": ["valuation", "fundamentals"],
             "labels": ["Valuation", "Fundamentals"], "mode": "synthesize"}
    events = [ev async for ev in svc.stream_synthesis(prep, "is it a buy?", route, tools=[], tool_handlers={})]
    answers = [p for k, p in events if k == "answer"]
    assert answers == ["general fallback answer"]   # the guarantee held; no empty stream


# ── stream_synthesis: tool events, partial specialists, mid-merge death (2026-09-16) ───

def _synth_svc(per_specialist, merge):
    from app.services.chat_service import ChatService
    """`per_specialist(sys_instruction) -> events`, `merge() -> events | raises`."""
    class _G:
        async def stream_agentic(self, prompt, tools=None, tool_handlers=None,
                                 system_instruction=None, max_rounds=4, model_name=None,
                                 usage_tag=None, max_output_tokens=None):
            for ev in per_specialist(system_instruction):
                if isinstance(ev, Exception):
                    raise ev
                yield ev

        async def stream_text(self, prompt, system_instruction=None, model_name=None,
                              usage_tag=None, max_output_tokens=None):
            for ev in merge():
                if isinstance(ev, Exception):
                    raise ev
                yield ev
    svc = object.__new__(ChatService)
    svc.gemini = _G()
    return svc


_ROUTE2 = {"specialists": ["valuation", "fundamentals"], "labels": ["Valuation", "Fundamentals"],
           "mode": "synthesize"}
_PREP = {"system_instruction": "sys", "prompt": "p"}


@pytest.mark.asyncio
async def test_synthesis_re_yields_every_specialists_tool_events():
    """The specialists' `tool` events used to be consumed inside `_run` (only the widget was
    read), so the endpoint's tool counters stayed at 0 on the multi-agent path and a turn
    whose EVERY tool failed was charged in full — single mode and the non-stream door settle
    the identical turn as `no_tools`. Every event now reaches the endpoint."""
    failed = {"name": "get_stock_chart_data", "args": {"ticker": "AAPL"}, "result": {"error": "timed_out"}}
    svc = _synth_svc(lambda sys: [("tool", failed), ("answer", "from memory")],
                     lambda: [("answer", "merged")])
    signals = {}
    events = [ev async for ev in svc.stream_synthesis(_PREP, "q", _ROUTE2, tools=[], tool_handlers={}, signals=signals)]
    tools = [p for k, p in events if k == "tool"]
    assert len(tools) == 2 and all(t["result"].get("error") for t in tools)
    # No signal is set here: the endpoint derives `no_tools` from the counters it now sees.
    assert signals == {}
    assert "".join(p for k, p in events if k == "answer") == "merged"


@pytest.mark.asyncio
async def test_a_specialist_that_never_answered_settles_the_turn_as_partial():
    """The `routing` frame promised two lenses; one raised (a refused half-open breaker trial)
    and the merge ran over the survivor as if it were the whole promise — charged in full."""
    def per(sys):
        if "fundamentals" in (sys or "").lower():
            return [RuntimeError("quota circuit open")]
        return [("answer", "valuation view")]
    svc = _synth_svc(per, lambda: [("answer", "merged")])
    signals = {}
    events = [ev async for ev in svc.stream_synthesis(_PREP, "q", _ROUTE2, tools=[], tool_handlers={}, signals=signals)]
    assert signals == {"degraded": "partial_specialists"}
    assert "".join(p for k, p in events if k == "answer") == "merged"


@pytest.mark.asyncio
async def test_both_specialists_answering_is_not_partial():
    svc = _synth_svc(lambda sys: [("answer", "a view")], lambda: [("answer", "merged")])
    signals = {}
    _ = [ev async for ev in svc.stream_synthesis(_PREP, "q", _ROUTE2, tools=[], tool_handlers={}, signals=signals)]
    assert signals == {}


@pytest.mark.asyncio
async def test_a_merge_that_dies_after_streaming_text_is_handed_to_the_fallback():
    """Swallowing a mid-body failure handed the endpoint a truncated fragment as a complete,
    fully charged answer. Re-raising lets the endpoint's fallback regenerate (with `reset`)."""
    svc = _synth_svc(lambda sys: [("answer", "a view")],
                     lambda: [("answer", "The market is"), RuntimeError("ReadTimeout")])
    signals = {}
    with pytest.raises(RuntimeError):
        _ = [ev async for ev in svc.stream_synthesis(_PREP, "q", _ROUTE2, tools=[], tool_handlers={}, signals=signals)]


@pytest.mark.asyncio
async def test_a_merge_that_dies_before_any_text_still_serves_the_top_specialist():
    svc = _synth_svc(lambda sys: [("answer", "a view")], lambda: [RuntimeError("503")])
    signals = {}
    events = [ev async for ev in svc.stream_synthesis(_PREP, "q", _ROUTE2, tools=[], tool_handlers={}, signals=signals)]
    assert signals == {"degraded": "unmerged"}
    assert [p for k, p in events if k == "answer"] == ["a view"]
