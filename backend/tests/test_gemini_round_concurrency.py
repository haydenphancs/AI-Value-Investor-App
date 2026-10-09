"""One round's tool calls run CONCURRENTLY on both doors (2026-10-08, plan step A2).

WHY
---
`generate_with_tools` (the send door) and `stream_agentic` (the stream door) awaited each tool
call of a round one after another, so a round cost the SUM of its tools: two 20 s tools in one
round passed the send door's 50 s budget (`CHAT_SEND_BUDGET_SECONDS`) and the turn was refunded
as GEMINI_UNAVAILABLE. The round now gathers its unique calls, so it costs its SLOWEST tool.

What must NOT change, pinned here:
  * one function_response (and one `tool` event) per CALL, in the model's call order — the API
    pairs them by position and 400s on a mismatch;
  * every call keeps its own `_TOOL_TIMEOUTS` ceiling and its own error result — one timeout or
    raise never cancels or blanks another call;
  * identical calls (same name, same canonical args) in one round run ONCE and share the result;
  * a cancelled turn still cancels the WAIT (CancelledError propagates) while the shielded
    handlers finish and warm their caches;
  * the stream door's per-turn memo still replays only SUCCESSFUL earlier-round results.

No network: fakes for the SDK and the handlers.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time

import pytest

from app.integrations import gemini as gem
from app.integrations.gemini import GeminiClient, _TTLCache


# ── fakes ─────────────────────────────────────────────────────────────────────


class _Part:
    def __init__(self, fc=None, text=None):
        self.function_call = fc
        self.text = text
        self.thought = False


class _FC:
    def __init__(self, name, args):
        self.name = name
        self.args = args


class _Content:
    def __init__(self, parts):
        self.parts = parts


class _Cand:
    def __init__(self, parts):
        self.content = _Content(parts)
        self.finish_reason = None


class _Resp:
    def __init__(self, parts):
        self.candidates = [_Cand(parts)]
        self.usage_metadata = None


def _calls(*specs):
    """A model turn carrying one function_call part per (name, args)."""
    return _Resp([_Part(fc=_FC(n, a)) for n, a in specs])


def _send_client(responses):
    """A `generate_with_tools` client whose model returns `responses` in order."""
    calls: list = []

    class _Models:
        async def generate_content(self, **kwargs):
            calls.append(kwargs)
            await asyncio.sleep(0)
            return responses.pop(0)

    client = GeminiClient.__new__(GeminiClient)
    client.model_name = "gemini-2.5-flash"
    client._temperature = 0.7
    client._max_tokens = 8192
    client._response_cache = _TTLCache(max_size=8, ttl_seconds=60)
    client._embedding_cache = _TTLCache(max_size=8, ttl_seconds=60)

    class _Aio:
        models = _Models()

    class _C:
        aio = _Aio()

    client._client = _C()
    gem._quota_circuit.reset()
    return client, calls


class _Chunk:
    def __init__(self, *parts):
        self.candidates = [_Cand(list(parts))]
        self.usage_metadata = None


def _stream_client(rounds):
    """A `stream_agentic` client: one canned round (a list of chunks) per send."""
    sent: list = []

    class _Chat:
        async def send_message_stream(self, message, **kw):
            sent.append(message)
            chunks = rounds.pop(0)

            async def _gen():
                for c in chunks:
                    yield c
            return _gen()

    class _Chats:
        def create(self, **kw):
            return _Chat()

    class _Aio:
        chats = _Chats()

    class _C:
        aio = _Aio()

    client = GeminiClient.__new__(GeminiClient)
    client.model_name = "gemini-2.5-flash"
    client._temperature = 0.7
    client._max_tokens = 8192
    client._client = _C()
    gem._quota_circuit.reset()
    return client, sent


def _sent_names(parts) -> list:
    return [p.function_response.name for p in parts]


def _sent_results(parts, *, stream: bool) -> list:
    out = [p.function_response.response["result"] for p in parts]
    return [json.loads(r) for r in out] if stream else out


def _sleeper(name, delay, log, value=None):
    async def h(args):
        log.append(("start", name))
        await asyncio.sleep(delay)
        log.append(("end", name))
        return value if value is not None else {"tool": name, "ticker": args.get("ticker")}
    return h


def _assert_one_cancelled_round_line(caplog, *, door, calls, ran, started, never=()):
    """A round that did not settle still leaves ONE `GEMINI_TOOL_ROUND … outcome=cancelled`
    WARNING (names and counts only), and no settled-round INFO line."""
    lines = [r for r in caplog.records if "GEMINI_TOOL_ROUND" in r.getMessage()]
    assert len(lines) == 1, [r.getMessage() for r in lines]
    rec = lines[0]
    msg = rec.getMessage()
    assert rec.levelno == logging.WARNING, msg
    assert f"GEMINI_TOOL_ROUND door={door} calls={calls} ran={ran} " in msg, msg
    assert f" started={started} " in msg and "outcome=cancelled" in msg, msg
    for token in never:
        assert token not in msg, f"the round line carried an argument: {msg}"


# ── send door: generate_with_tools ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_send_door_responses_keep_call_order_when_the_first_tool_finishes_last():
    log: list = []
    client, calls = _send_client([
        _calls(("slow_a", {"ticker": "AAPL"}), ("fast_b", {"ticker": "MSFT"})),
        _Resp([_Part(text="Answer.")]),
    ])
    out = await client.generate_with_tools(
        prompt="p", tools=[],
        tool_handlers={"slow_a": _sleeper("slow_a", 0.15, log), "fast_b": _sleeper("fast_b", 0.0, log)},
    )
    assert out["text"] == "Answer."
    # fast_b FINISHED first (concurrency is real) …
    assert log.index(("end", "fast_b")) < log.index(("end", "slow_a"))
    # … yet everything the model and the caller see is in CALL order.
    parts = calls[1]["contents"][-1].parts
    assert _sent_names(parts) == ["slow_a", "fast_b"]
    assert [r["tool"] for r in _sent_results(parts, stream=False)] == ["slow_a", "fast_b"]
    assert [r["tool"] for r in out["tool_results"]] == ["slow_a", "fast_b"]


@pytest.mark.asyncio
async def test_send_door_two_slow_tools_cost_the_slower_not_the_sum():
    log: list = []
    client, _ = _send_client([
        _calls(("get_x", {"ticker": "AAPL"}), ("get_y", {"ticker": "AAPL"})),
        _Resp([_Part(text="Both.")]),
    ])
    t0 = time.monotonic()
    out = await client.generate_with_tools(
        prompt="p", tools=[],
        tool_handlers={"get_x": _sleeper("get_x", 0.5, log), "get_y": _sleeper("get_y", 0.5, log)},
    )
    elapsed = time.monotonic() - t0
    assert out["text"] == "Both." and len(out["tool_results"]) == 2
    assert elapsed < 0.85, f"serial would be ~1.0 s, concurrent ~0.5 s — took {elapsed:.2f}s"
    # Both handlers were IN FLIGHT together.
    assert log[:2] == [("start", "get_x"), ("start", "get_y")]


@pytest.mark.asyncio
async def test_send_door_identical_calls_in_one_round_run_once_and_each_get_a_response(caplog):
    ran: list = []

    async def chart(args):
        ran.append(dict(args))
        await asyncio.sleep(0.01)
        return {"widget_type": "stock_chart", "ticker": args["ticker"]}

    client, calls = _send_client([
        # Same name, same args (key order differs) → one run; a different ticker → its own run.
        _calls(("get_stock_chart_data", {"ticker": "AAPL", "period": "1Y"}),
               ("get_stock_chart_data", {"period": "1Y", "ticker": "AAPL"}),
               ("get_stock_chart_data", {"ticker": "MSFT", "period": "1Y"})),
        _Resp([_Part(text="ok")]),
    ])
    with caplog.at_level(logging.INFO, logger=gem.__name__):
        out = await client.generate_with_tools(prompt="p", tools=[],
                                               tool_handlers={"get_stock_chart_data": chart})
    assert sorted(r["ticker"] for r in ran) == ["AAPL", "MSFT"], "the duplicate did not run again"
    parts = calls[1]["contents"][-1].parts
    assert len(parts) == 3, "still ONE function_response per call"
    assert [r["ticker"] for r in _sent_results(parts, stream=False)] == ["AAPL", "AAPL", "MSFT"]
    assert [r["ticker"] for r in out["tool_results"]] == ["AAPL", "AAPL", "MSFT"], "one entry per call"
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert text.count("Gemini invoked tool 'get_stock_chart_data'") == 2
    assert "repeated tool 'get_stock_chart_data'" in text
    assert "GEMINI_TOOL_ROUND door=send calls=3 ran=2" in text


@pytest.mark.asyncio
async def test_send_door_one_timeout_leaves_the_other_result_intact(monkeypatch):
    monkeypatch.setitem(gem._TOOL_TIMEOUTS, "slow_tool", 0.05)
    log: list = []
    client, calls = _send_client([
        _calls(("slow_tool", {"ticker": "AAPL"}), ("get_y", {"ticker": "AAPL"})),
        _Resp([_Part(text="Partial answer.")]),
    ])
    t0 = time.monotonic()
    out = await client.generate_with_tools(
        prompt="p", tools=[],
        tool_handlers={"slow_tool": _sleeper("slow_tool", 1.0, log),
                       "get_y": _sleeper("get_y", 0.1, log)},
    )
    elapsed = time.monotonic() - t0
    assert elapsed < 0.6, "the round waits for its slowest CEILING, not the timed-out work"
    sent = _sent_results(calls[1]["contents"][-1].parts, stream=False)
    assert sent[0] == {"error": "timed_out", "tool": "slow_tool", "timeout_seconds": 0.05,
                       "upstream": True}
    assert sent[1] == {"tool": "get_y", "ticker": "AAPL"}, "the timeout did not cancel get_y"
    assert out["tool_results"] == [{"tool": "get_y", "ticker": "AAPL"}]
    assert out["tool_errors"] == [{"name": "slow_tool", "error": "timed_out", "upstream": True}]


@pytest.mark.asyncio
async def test_send_door_a_raising_handler_becomes_its_own_error_response():
    async def boom(args):
        await asyncio.sleep(0.01)
        raise RuntimeError("FMP 502 upstream")

    async def ok(args):
        await asyncio.sleep(0.02)
        return {"ok": True}

    client, calls = _send_client([
        _calls(("get_x", {"ticker": "AAPL"}), ("get_y", {"ticker": "AAPL"}), ("ghost", {})),
        _Resp([_Part(text="Answer.")]),
    ])
    out = await client.generate_with_tools(prompt="p", tools=[],
                                           tool_handlers={"get_x": boom, "get_y": ok})
    sent = _sent_results(calls[1]["contents"][-1].parts, stream=False)
    assert "FMP 502 upstream" in sent[0]["error"] and sent[0]["upstream"] is True
    assert sent[1] == {"ok": True}
    assert sent[2] == {"error": "unknown tool: ghost"}
    assert out["tool_results"] == [{"ok": True}]
    assert [e["name"] for e in out["tool_errors"]] == ["get_x", "ghost"]
    assert [e["upstream"] for e in out["tool_errors"]] == [True, False]


@pytest.mark.asyncio
async def test_send_door_cancellation_propagates_and_the_shielded_handlers_finish(caplog):
    caplog.set_level(logging.INFO, logger=gem.__name__)
    state: dict = {"started": 0}

    async def slow(args):
        state["started"] += 1
        try:
            await asyncio.sleep(0.2)
            state[args["ticker"]] = "done"
            return {"ok": args["ticker"]}
        except asyncio.CancelledError:
            state[args["ticker"]] = "cancelled"
            raise

    client, calls = _send_client([
        _calls(("get_x", {"ticker": "AAPL"}), ("get_y", {"ticker": "MSFT"})),
        _Resp([_Part(text="never")]),
    ])
    task = asyncio.ensure_future(client.generate_with_tools(
        prompt="p", tools=[], tool_handlers={"get_x": slow, "get_y": slow},
    ))
    for _ in range(100):
        await asyncio.sleep(0.005)
        if state["started"] == 2:
            break
    assert state["started"] == 2, "both handlers were in flight together"
    assert "AAPL" not in state and "MSFT" not in state, "neither had finished: they overlapped"
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert len(calls) == 1, "no follow-up after a cancelled round"
    await asyncio.sleep(0.3)
    assert state["AAPL"] == "done" and state["MSFT"] == "done", (
        "the turn's cancellation reached a shielded handler", state)
    _assert_one_cancelled_round_line(caplog, door="send", calls=2, ran=2, started=2,
                                     never=("AAPL", "MSFT"))


@pytest.mark.asyncio
async def test_send_door_a_handler_that_raises_cancelled_error_is_never_swallowed(caplog):
    """A CancelledError is never turned into a tool result — re-raised once the round settles
    (a serial round raised it too), and the sibling still finished its own work."""
    caplog.set_level(logging.INFO, logger=gem.__name__)
    state: dict = {}

    async def self_cancel(args):
        await asyncio.sleep(0.01)
        raise asyncio.CancelledError()

    async def ok(args):
        await asyncio.sleep(0.03)
        state["ok"] = True
        return {"ok": True}

    client, calls = _send_client([
        _calls(("get_x", {"ticker": "AAPL"}), ("get_y", {"ticker": "AAPL"})),
        _Resp([_Part(text="never")]),
    ])
    with pytest.raises(asyncio.CancelledError):
        await client.generate_with_tools(prompt="p", tools=[],
                                         tool_handlers={"get_x": self_cancel, "get_y": ok})
    assert state.get("ok") is True
    assert len(calls) == 1
    _assert_one_cancelled_round_line(caplog, door="send", calls=2, ran=2, started=2)


@pytest.mark.asyncio
async def test_send_door_forced_extra_round_also_runs_concurrently():
    """The forced turn's one extra executed round goes through the same runner."""
    log: list = []
    client, calls = _send_client([
        _calls(("web_search", {"query": "q"})),
        _calls(("get_x", {"ticker": "MSFT"}), ("get_y", {"ticker": "MSFT"})),
        _Resp([_Part(text="Answer with all three.")]),
    ])
    t0 = time.monotonic()
    out = await client.generate_with_tools(
        prompt="p", tools=[],
        tool_handlers={"web_search": _sleeper("web_search", 0.0, log),
                       "get_x": _sleeper("get_x", 0.4, log), "get_y": _sleeper("get_y", 0.4, log)},
        force_first_tool="web_search",
    )
    assert out["text"] == "Answer with all three."
    assert time.monotonic() - t0 < 0.7, "serial would be ~0.8 s"
    assert _sent_names(calls[2]["contents"][-1].parts) == ["get_x", "get_y"]
    assert [r["tool"] for r in out["tool_results"]] == ["web_search", "get_x", "get_y"]


@pytest.mark.asyncio
async def test_send_door_round_log_never_carries_arguments(caplog):
    async def ok(args):
        return {"ok": True}

    client, _ = _send_client([
        _calls(("web_search", {"query": "SECRETQUERY about my holdings"}), ("get_y", {"ticker": "ZZZQ"})),
        _Resp([_Part(text="Answer.")]),
    ])
    with caplog.at_level(logging.INFO, logger=gem.__name__):
        await client.generate_with_tools(prompt="p", tools=[],
                                         tool_handlers={"web_search": ok, "get_y": ok})
    round_lines = [r.getMessage() for r in caplog.records if "GEMINI_TOOL_ROUND" in r.getMessage()]
    assert len(round_lines) == 1
    line = round_lines[0]
    assert "web_search:" in line and "get_y:" in line
    assert "SECRETQUERY" not in line and "ZZZQ" not in line
    everything = "\n".join(r.getMessage() for r in caplog.records)
    assert "SECRETQUERY" not in everything, "the invocation log still redacts web_search"


# ── odd upstream argument shapes degrade, never raise ─────────────────────────


@pytest.mark.asyncio
async def test_uncanonicalisable_args_run_without_dedup_and_never_raise(caplog):
    ran: list = []

    async def h(args):
        ran.append(args)
        return {"ok": True}

    weird = {1: "a", "b": 2}          # json.dumps(sort_keys=True) raises TypeError on these keys
    client, calls = _send_client([
        _calls(("get_x", weird), ("get_x", dict(weird))),
        _Resp([_Part(text="ok")]),
    ])
    with caplog.at_level(logging.WARNING, logger=gem.__name__):
        out = await client.generate_with_tools(prompt="p", tools=[], tool_handlers={"get_x": h})
    assert out["text"] == "ok"
    assert len(ran) == 2, "an unkeyable call is never folded into another"
    assert len(calls[1]["contents"][-1].parts) == 2
    assert "cannot be canonicalised" in "\n".join(r.getMessage() for r in caplog.records)


@pytest.mark.parametrize("raw", ["not-a-mapping", 42, [1, 2, 3]])
@pytest.mark.asyncio
async def test_malformed_args_degrade_to_an_empty_dict(raw, caplog):
    seen: list = []

    async def h(args):
        seen.append(args)
        return {"error": "ticker required"}

    client, calls = _send_client([_calls(("get_x", raw)), _Resp([_Part(text="ok")])])
    with caplog.at_level(logging.WARNING, logger=gem.__name__):
        out = await client.generate_with_tools(prompt="p", tools=[], tool_handlers={"get_x": h})
    assert seen == [{}]
    assert out["tool_errors"] == [{"name": "get_x", "error": "ticker required", "upstream": False}]
    assert "not a mapping" in "\n".join(r.getMessage() for r in caplog.records)


def test_the_call_key_is_canonical_and_name_scoped():
    k = gem._tool_call_key
    assert k("t", {"a": 1, "b": [1, 2]}) == k("t", {"b": [1, 2], "a": 1})
    assert k("t", {"a": 1}) != k("u", {"a": 1})
    assert k("t", {"a": 1}) != k("t", {"a": "1"})
    assert k("t", {"x": float("nan")}) == k("t", {"x": float("nan")})
    assert k("t", {"big": 1e300, "neg": -0.0}) is not None
    deep: dict = {}
    cur = deep
    for _ in range(5000):
        cur["n"] = {}
        cur = cur["n"]
    assert k("t", deep) is None, "absurd nesting degrades to no-dedup, never a RecursionError"


@pytest.mark.asyncio
async def test_an_empty_round_runs_nothing():
    assert await gem._gather_tool_calls([], door="send", calls=0) == []


# ── stream door: stream_agentic ───────────────────────────────────────────────


async def _collect(client, **kw):
    return [e async for e in client.stream_agentic("p", tools=[], **kw)]


@pytest.mark.asyncio
async def test_stream_door_announces_every_tool_first_then_reports_in_call_order():
    log: list = []
    client, sent = _stream_client([
        [_Chunk(_Part(fc=_FC("slow_a", {"ticker": "AAPL"})), _Part(fc=_FC("fast_b", {"ticker": "MSFT"})))],
        [_Chunk(_Part(text="Done."))],
    ])
    events: list = []
    async for kind, payload in client.stream_agentic(
        "p", tools=[], tool_handlers={"slow_a": _sleeper("slow_a", 0.15, log),
                                      "fast_b": _sleeper("fast_b", 0.0, log)},
    ):
        events.append((kind, payload))
        if kind == "tool_start":
            log.append(("tool_start", payload["name"]))
    # Both announcements reached the consumer before EITHER handler started.
    assert log[:2] == [("tool_start", "slow_a"), ("tool_start", "fast_b")]
    assert log.index(("end", "fast_b")) < log.index(("end", "slow_a")), "concurrency is real"
    kinds = [k for k, _ in events]
    assert kinds == ["tool_start", "tool_start", "tool", "tool", "answer"]
    assert [p["name"] for k, p in events if k == "tool"] == ["slow_a", "fast_b"]
    assert _sent_names(sent[1]) == ["slow_a", "fast_b"]
    assert [r["tool"] for r in _sent_results(sent[1], stream=True)] == ["slow_a", "fast_b"]


@pytest.mark.asyncio
async def test_stream_door_two_slow_tools_cost_the_slower_not_the_sum():
    log: list = []
    client, _ = _stream_client([
        [_Chunk(_Part(fc=_FC("get_x", {"ticker": "AAPL"})), _Part(fc=_FC("get_y", {"ticker": "AAPL"})))],
        [_Chunk(_Part(text="Done."))],
    ])
    t0 = time.monotonic()
    events = await _collect(client, tool_handlers={"get_x": _sleeper("get_x", 0.5, log),
                                                   "get_y": _sleeper("get_y", 0.5, log)})
    elapsed = time.monotonic() - t0
    assert ("answer", "Done.") in events
    assert elapsed < 0.85, f"serial would be ~1.0 s, concurrent ~0.5 s — took {elapsed:.2f}s"


@pytest.mark.asyncio
async def test_stream_door_identical_calls_in_one_round_run_once():
    ran: list = []

    async def h(args):
        ran.append(args)
        await asyncio.sleep(0.01)
        return {"widget_type": "stock_chart", "ticker": args["ticker"]}

    fc = _FC("get_x", {"ticker": "AAPL"})
    client, sent = _stream_client([
        [_Chunk(_Part(fc=fc), _Part(fc=_FC("get_x", {"ticker": "AAPL"})))],
        [_Chunk(_Part(text="Done."))],
    ])
    events = await _collect(client, tool_handlers={"get_x": h})
    assert ran == [{"ticker": "AAPL"}]
    kinds = [k for k, _ in events]
    assert kinds.count("tool_start") == 1 and kinds.count("tool") == 2
    tools = [p for k, p in events if k == "tool"]
    assert tools[0].get("memoized") is None and tools[1]["memoized"] is True
    assert tools[0]["result"] == tools[1]["result"]
    assert len(sent[1]) == 2, "one function_response per call"


@pytest.mark.asyncio
async def test_stream_door_a_shared_failure_is_not_frozen_for_the_next_round():
    """An identical pair in one round shares ONE failed run; the failure is not memoised, so the
    model's retry in the next round runs the handler again."""
    n = {"calls": 0}

    async def h(args):
        n["calls"] += 1
        if n["calls"] == 1:
            return {"error": "timed_out", "upstream": True}
        return {"ok": True}

    client, _ = _stream_client([
        [_Chunk(_Part(fc=_FC("get_x", {"ticker": "AAPL"})), _Part(fc=_FC("get_x", {"ticker": "AAPL"})))],
        [_Chunk(_Part(fc=_FC("get_x", {"ticker": "AAPL"})))],
        [_Chunk(_Part(text="Done."))],
    ])
    events = await _collect(client, tool_handlers={"get_x": h}, max_rounds=4)
    tools = [p for k, p in events if k == "tool"]
    assert n["calls"] == 2
    assert tools[0]["result"]["error"] == "timed_out" and tools[1]["result"]["error"] == "timed_out"
    assert tools[2]["result"] == {"ok": True} and "memoized" not in tools[2]


@pytest.mark.asyncio
async def test_stream_door_memo_replays_across_rounds_while_new_calls_run(caplog):
    ran: list = []

    async def h(args):
        ran.append(args["ticker"])
        return {"ok": args["ticker"]}

    client, sent = _stream_client([
        [_Chunk(_Part(fc=_FC("get_x", {"ticker": "AAPL"})), _Part(fc=_FC("get_x", {"ticker": "MSFT"})))],
        [_Chunk(_Part(fc=_FC("get_x", {"ticker": "AAPL"})), _Part(fc=_FC("get_x", {"ticker": "NVDA"})))],
        [_Chunk(_Part(text="Done."))],
    ])
    with caplog.at_level(logging.INFO, logger=gem.__name__):
        events = await _collect(client, tool_handlers={"get_x": h}, max_rounds=4)
    assert sorted(ran) == ["AAPL", "MSFT", "NVDA"], "AAPL ran once for the whole turn"
    kinds = [k for k, _ in events]
    assert kinds == ["tool_start", "tool_start", "tool", "tool",
                     "tool_start", "tool", "tool", "answer"]
    round2 = [p for k, p in events if k == "tool"][2:]
    assert [p["args"]["ticker"] for p in round2] == ["AAPL", "NVDA"], "call order kept"
    assert round2[0]["memoized"] is True and "memoized" not in round2[1]
    assert [r["ok"] for r in _sent_results(sent[2], stream=True)] == ["AAPL", "NVDA"]
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "re-issued tool 'get_x'" in text
    assert "GEMINI_TOOL_ROUND door=stream calls=2 ran=2" in text
    assert "GEMINI_TOOL_ROUND door=stream calls=2 ran=1" in text


@pytest.mark.asyncio
async def test_stream_door_one_timeout_and_one_raise_leave_the_third_intact(monkeypatch):
    monkeypatch.setitem(gem._TOOL_TIMEOUTS, "slow_tool", 0.05)
    log: list = []

    async def boom(args):
        raise ValueError("bad payload from upstream")

    client, sent = _stream_client([
        [_Chunk(_Part(fc=_FC("slow_tool", {"ticker": "AAPL"})), _Part(fc=_FC("boom", {"ticker": "AAPL"})),
                _Part(fc=_FC("get_y", {"ticker": "AAPL"})))],
        [_Chunk(_Part(text="Done."))],
    ])
    t0 = time.monotonic()
    events = await _collect(client, tool_handlers={
        "slow_tool": _sleeper("slow_tool", 1.0, log), "boom": boom, "get_y": _sleeper("get_y", 0.1, log)})
    assert time.monotonic() - t0 < 0.6
    tools = [p for k, p in events if k == "tool"]
    assert [p["name"] for p in tools] == ["slow_tool", "boom", "get_y"]
    assert tools[0]["result"]["error"] == "timed_out" and tools[0]["result"]["upstream"] is True
    assert "bad payload from upstream" in tools[1]["result"]["error"]
    assert tools[2]["result"] == {"tool": "get_y", "ticker": "AAPL"}
    assert len(sent[1]) == 3


@pytest.mark.asyncio
async def test_stream_door_cancellation_propagates_and_the_shielded_handlers_finish(caplog):
    caplog.set_level(logging.INFO, logger=gem.__name__)
    state: dict = {"started": 0}

    async def slow(args):
        state["started"] += 1
        try:
            await asyncio.sleep(0.2)
            state[args["ticker"]] = "done"
            return {"ok": True}
        except asyncio.CancelledError:
            state[args["ticker"]] = "cancelled"
            raise

    client, sent = _stream_client([
        [_Chunk(_Part(fc=_FC("get_x", {"ticker": "AAPL"})), _Part(fc=_FC("get_y", {"ticker": "MSFT"})))],
        [_Chunk(_Part(text="never"))],
    ])
    events: list = []

    async def consume():
        async for e in client.stream_agentic("p", tools=[], tool_handlers={"get_x": slow, "get_y": slow}):
            events.append(e)

    task = asyncio.ensure_future(consume())
    for _ in range(100):
        await asyncio.sleep(0.005)
        if state["started"] == 2:
            break
    assert state["started"] == 2
    assert "AAPL" not in state and "MSFT" not in state, "neither had finished: they overlapped"
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert [k for k, _ in events] == ["tool_start", "tool_start"], "no tool event after the cancel"
    assert len(sent) == 1, "no next round after a cancelled one"
    await asyncio.sleep(0.3)
    assert state["AAPL"] == "done" and state["MSFT"] == "done", state
    _assert_one_cancelled_round_line(caplog, door="stream", calls=2, ran=2, started=2)


@pytest.mark.asyncio
async def test_stream_door_invocation_log_is_redacted_for_web_search(caplog):
    async def ok(args):
        return {"web_search": True, "status": "ok"}

    client, _ = _stream_client([
        [_Chunk(_Part(fc=_FC("web_search", {"query": "SECRETQUERY lawsuit"})),
                _Part(fc=_FC("get_ticker_news", {"ticker": "AAPL"})))],
        [_Chunk(_Part(text="Done."))],
    ])
    with caplog.at_level(logging.INFO, logger=gem.__name__):
        await _collect(client, tool_handlers={"web_search": ok, "get_ticker_news": ok})
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "Gemini invoked tool 'web_search'" in text and "SECRETQUERY" not in text
    assert "Gemini invoked tool 'get_ticker_news'" in text and "AAPL" in text


@pytest.mark.asyncio
async def test_odd_result_shapes_keep_their_own_slot_on_both_doors():
    """None, a list and a huge dict side by side: each call's own result, in call order, the big
    one structurally truncated for the model — never another call's result, never a raise."""
    async def none_h(args):
        await asyncio.sleep(0.02)
        return None

    async def list_h(args):
        return [1, 2, 3]

    async def big_h(args):
        await asyncio.sleep(0.01)
        return {"rows": [{"v": 1e15, "label": "x" * 400} for _ in range(400)]}

    handlers = {"none_t": none_h, "list_t": list_h, "big_t": big_h}
    specs = (("none_t", {}), ("list_t", {}), ("big_t", {}))

    client, calls = _send_client([_calls(*specs), _Resp([_Part(text="ok")])])
    out = await client.generate_with_tools(prompt="p", tools=[], tool_handlers=handlers)
    sent = _sent_results(calls[1]["contents"][-1].parts, stream=False)
    assert sent[0] is None and sent[1] == [1, 2, 3]
    assert sent[2]["_truncated"] is True and len(sent[2]["rows"]) < 400
    assert out["tool_results"][0] is None and out["tool_results"][1] == [1, 2, 3]
    assert out["tool_errors"] == []

    sclient, ssent = _stream_client([[_Chunk(*[_Part(fc=_FC(n, a)) for n, a in specs])],
                                     [_Chunk(_Part(text="ok"))]])
    events = await _collect(sclient, tool_handlers=handlers)
    tools = [p for k, p in events if k == "tool"]
    assert [p["name"] for p in tools] == ["none_t", "list_t", "big_t"]
    assert tools[0]["result"] is None and tools[1]["result"] == [1, 2, 3]
    streamed = _sent_results(ssent[1], stream=True)
    assert streamed[0] is None and streamed[1] == [1, 2, 3] and streamed[2]["_truncated"] is True


# ── a round is BOUNDED (fix round 2026-10-08) ─────────────────────────────────
#
# Unbounded, a cancelled turn let every job of the round run on behind the shield: one 1-credit
# turn could start a dozen cold builds at once on the shared FMP pool, where the serial loop had
# started one. Now at most `_TOOL_ROUND_MAX_CONCURRENCY` jobs wait on a handler at once (the
# permit is taken BEFORE the handler exists, so a queued job never starts after a cut), and at
# most `_TOOL_ROUND_MAX_JOBS` unique jobs with a handler run per round.


def _counting(state: dict, delay: float):
    """A handler that records how many ran, how many were in flight at once, and how many ended."""
    state.setdefault("started", 0)
    state.setdefault("inflight", 0)
    state.setdefault("peak", 0)
    state.setdefault("done", 0)

    async def h(args):
        state["started"] += 1
        state["inflight"] += 1
        state["peak"] = max(state["peak"], state["inflight"])
        try:
            await asyncio.sleep(delay)
            state["done"] += 1
            return {"ticker": args.get("ticker")}
        finally:
            state["inflight"] -= 1
    return h


def _tickers(n: int) -> list:
    return [f"T{i:02d}" for i in range(n)]


def test_the_bounds_are_sane():
    """Read from the REAL ceilings (the old literal 20.0 stayed green while two waves of the 30 s
    ceilings — 60 s — passed the 50 s budget). Two waves may not fit, so the send door caps every
    wait at its deadline; what must fit is ONE wave of the largest ceiling plus the answer reserve."""
    from app.config import Settings
    assert 1 <= gem._TOOL_ROUND_MAX_CONCURRENCY <= gem._TOOL_ROUND_MAX_JOBS
    budget = Settings.model_fields["CHAT_SEND_BUDGET_SECONDS"].default
    largest = max(gem._TOOL_TIMEOUTS.values())
    assert largest + gem._SEND_ANSWER_RESERVE_SECONDS < budget
    assert gem._tool_ceiling("explain_price_move") == largest == 30.0


# ── the send door's deadline (final review 2026-10-09) ───────────────────────


@pytest.mark.asyncio
async def test_a_deadline_caps_every_wait_and_never_starts_a_job_queued_past_it(caplog):
    """Five hanging 30 s-ceiling jobs, four at a time, under a 0.3 s deadline: the first wave's
    waits are capped at the deadline (`timed_out`, upstream) and the fifth — whose permit arrives at
    the deadline — never starts (`not_run`). The round settles at the deadline, not at 60 s."""
    caplog.set_level(logging.WARNING, logger=gem.__name__)
    state: dict = {}
    handler = _counting(state, 10.0)
    fcalls = [_FC("explain_price_move", {"ticker": t}) for t in _tickers(5)]
    named, slots, jobs = gem._plan_tool_round(fcalls, {"explain_price_move": handler})
    t0 = time.monotonic()
    results = await gem._gather_tool_calls(jobs, door="send", calls=5,
                                           deadline=time.monotonic() + 0.3)
    elapsed = time.monotonic() - t0
    assert elapsed < 1.0, elapsed
    assert [r["error"] for r in results] == ["timed_out"] * 4 + ["not_run"]
    assert all(r.get("upstream") is True for r in results)
    assert results[4]["tool"] == "explain_price_move" and "did not run" in results[4]["detail"]
    assert state["started"] == 4, "the queued job's handler never started"
    assert any("not run" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_without_a_deadline_the_round_is_unchanged(monkeypatch):
    monkeypatch.setitem(gem._TOOL_TIMEOUTS, "explain_price_move", 0.05)
    state: dict = {}
    fcalls = [_FC("explain_price_move", {"ticker": t}) for t in _tickers(5)]
    _n, _s, jobs = gem._plan_tool_round(fcalls, {"explain_price_move": _counting(state, 10.0)})
    results = await gem._gather_tool_calls(jobs, door="send", calls=5)
    assert [r["error"] for r in results] == ["timed_out"] * 5 and state["started"] == 5


@pytest.mark.asyncio
async def test_send_door_round_settles_inside_its_deadline_and_still_answers(monkeypatch):
    """The reviewer's repro, scaled: 5 hanging `explain_price_move` calls (30 s ceilings) inside a
    0.6 s budget — before the fix the round alone took two waves and the budget cancelled the turn.
    Now the round settles before the answer reserve and the follow-up answers."""
    monkeypatch.setattr(gem, "_SEND_ANSWER_RESERVE_SECONDS", 0.2)
    state: dict = {}
    client, calls = _send_client([
        _calls(*[("explain_price_move", {"ticker": t}) for t in _tickers(5)]),
        _Resp([_Part(text="Answer from what loaded.")]),
    ])
    out = await asyncio.wait_for(client.generate_with_tools(
        prompt="p", tools=[], tool_handlers={"explain_price_move": _counting(state, 10.0)},
        deadline=time.monotonic() + 0.6,
    ), timeout=0.6)
    assert out["text"] == "Answer from what loaded."
    assert sorted(e["error"] for e in out["tool_errors"]) == ["not_run"] + ["timed_out"] * 4
    assert state["started"] == 4


@pytest.mark.asyncio
async def test_send_door_extra_round_runs_only_while_its_slowest_call_still_fits():
    """With a deadline the extra round's gate is the time LEFT (the follow-up's slowest ceiling
    before the answer reserve), not the 20 s elapsed rule."""
    def _client():
        return _send_client([
            _calls(("get_x", {"ticker": "AAPL"}), ("web_search", {"query": "q"})),
            _calls(("web_search", {"query": "q"})),
            _Resp([_Part(text="Final.")]),
            _Resp([_Part(text="Final.")]),
        ])
    log: list = []
    handlers = {"get_x": _sleeper("get_x", 0.0, log), "web_search": _sleeper("web_search", 0.0, log)}
    # Plenty of time: the extra round runs (web_search runs twice in total).
    client, _ = _client()
    await client.generate_with_tools(prompt="p", tools=[], tool_handlers=handlers,
                                     extra_round_tools=frozenset({"web_search"}),
                                     deadline=time.monotonic() + 100.0)
    assert log.count(("start", "web_search")) == 2
    # The web search's 15 s ceiling no longer fits before the reserve: skipped, tool-less final.
    log.clear()
    client, calls = _client()
    out = await client.generate_with_tools(prompt="p", tools=[], tool_handlers=handlers,
                                           extra_round_tools=frozenset({"web_search"}),
                                           deadline=time.monotonic() + gem._SEND_ANSWER_RESERVE_SECONDS + 5.0)
    assert log.count(("start", "web_search")) == 1 and out["text"] == "Final."
    assert calls[-1]["config"].tools is None, "the final answer is tool-less"


@pytest.mark.asyncio
async def test_a_budget_cut_starts_at_most_the_concurrency_limit_and_never_the_queue(monkeypatch, caplog):
    """The reviewer's repro: 12 distinct cold ownership builds in one round, the turn cut at
    0.05 s. Before the fix all 12 handlers had started (the serial loop would have started 1)."""
    monkeypatch.setattr(gem, "_TOOL_ROUND_MAX_JOBS", 12)        # all 12 are jobs: test the gate
    caplog.set_level(logging.INFO, logger=gem.__name__)
    state: dict = {}
    handler = _counting(state, 0.2)
    fcalls = [_FC("check_ownership_filings", {"ticker": t}) for t in _tickers(12)]
    named, slots, jobs = gem._plan_tool_round(fcalls, {"check_ownership_filings": handler})
    assert len(jobs) == 12
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(gem._gather_tool_calls(jobs, door="send", calls=12), timeout=0.05)
    assert state["started"] == gem._TOOL_ROUND_MAX_CONCURRENCY, state
    await asyncio.sleep(0.4)
    assert state["started"] == gem._TOOL_ROUND_MAX_CONCURRENCY, "a queued job started after the cut"
    assert state["done"] == gem._TOOL_ROUND_MAX_CONCURRENCY, "the shielded, started ones finished"
    _assert_one_cancelled_round_line(caplog, door="send", calls=12, ran=12,
                                     started=gem._TOOL_ROUND_MAX_CONCURRENCY, never=("T00", "T11"))


@pytest.mark.asyncio
async def test_a_round_never_has_more_than_the_limit_in_flight_and_keeps_call_order():
    state: dict = {}
    handler = _counting(state, 0.1)
    n = gem._TOOL_ROUND_MAX_JOBS
    fcalls = [_FC("get_x", {"ticker": t}) for t in _tickers(n)]
    _named, _slots, jobs = gem._plan_tool_round(fcalls, {"get_x": handler})
    t0 = time.monotonic()
    results = await gem._gather_tool_calls(jobs, door="send", calls=n)
    elapsed = time.monotonic() - t0
    assert state["peak"] == gem._TOOL_ROUND_MAX_CONCURRENCY, state
    assert state["started"] == n and state["done"] == n
    assert [r["ticker"] for r in results] == _tickers(n), "results stay in job order"
    waves = -(-n // gem._TOOL_ROUND_MAX_CONCURRENCY)
    assert waves * 0.1 - 0.02 <= elapsed < waves * 0.1 + 0.3, elapsed


@pytest.mark.asyncio
async def test_a_queued_jobs_ceiling_does_not_burn_while_it_waits(monkeypatch):
    """The permit is taken BEFORE `_run_tool_handler` starts the ceiling: with one slot, the
    second job waits 0.1 s and still gets its whole 0.15 s ceiling — it does not time out."""
    monkeypatch.setattr(gem, "_TOOL_ROUND_MAX_CONCURRENCY", 1)
    monkeypatch.setitem(gem._TOOL_TIMEOUTS, "get_x", 0.15)
    state: dict = {}
    handler = _counting(state, 0.1)
    fcalls = [_FC("get_x", {"ticker": t}) for t in _tickers(2)]
    _named, _slots, jobs = gem._plan_tool_round(fcalls, {"get_x": handler})
    results = await gem._gather_tool_calls(jobs, door="send", calls=2)
    assert results == [{"ticker": "T00"}, {"ticker": "T01"}]
    assert state["peak"] == 1


@pytest.mark.parametrize("bad", [0, -3])
@pytest.mark.asyncio
async def test_a_nonsense_concurrency_limit_still_runs_the_round(monkeypatch, bad):
    """A zero or negative limit (a bad edit) degrades to one-at-a-time — never a deadlock."""
    monkeypatch.setattr(gem, "_TOOL_ROUND_MAX_CONCURRENCY", bad)
    state: dict = {}
    fcalls = [_FC("get_x", {"ticker": t}) for t in _tickers(3)]
    _named, _slots, jobs = gem._plan_tool_round(fcalls, {"get_x": _counting(state, 0.0)})
    results = await asyncio.wait_for(gem._gather_tool_calls(jobs, door="send", calls=3), timeout=2)
    assert [r["ticker"] for r in results] == _tickers(3) and state["peak"] == 1


@pytest.mark.asyncio
async def test_send_door_a_cut_mid_round_never_starts_the_queued_jobs(caplog):
    caplog.set_level(logging.INFO, logger=gem.__name__)
    state: dict = {}
    n = gem._TOOL_ROUND_MAX_JOBS
    client, calls = _send_client([
        _calls(*[("get_x", {"ticker": t}) for t in _tickers(n)]),
        _Resp([_Part(text="never")]),
    ])
    task = asyncio.ensure_future(client.generate_with_tools(
        prompt="p", tools=[], tool_handlers={"get_x": _counting(state, 0.2)},
    ))
    for _ in range(100):
        await asyncio.sleep(0.005)
        if state.get("started", 0) >= gem._TOOL_ROUND_MAX_CONCURRENCY:
            break
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.sleep(0.4)
    assert state["started"] == gem._TOOL_ROUND_MAX_CONCURRENCY, state
    assert state["done"] == gem._TOOL_ROUND_MAX_CONCURRENCY
    assert len(calls) == 1
    _assert_one_cancelled_round_line(caplog, door="send", calls=n, ran=n,
                                     started=gem._TOOL_ROUND_MAX_CONCURRENCY, never=("T00",))


@pytest.mark.asyncio
async def test_stream_door_a_cut_mid_round_never_starts_the_queued_jobs(caplog):
    caplog.set_level(logging.INFO, logger=gem.__name__)
    state: dict = {}
    n = gem._TOOL_ROUND_MAX_JOBS
    client, sent = _stream_client([
        [_Chunk(*[_Part(fc=_FC("get_x", {"ticker": t})) for t in _tickers(n)])],
        [_Chunk(_Part(text="never"))],
    ])
    events: list = []

    async def consume():
        async for e in client.stream_agentic("p", tools=[], tool_handlers={"get_x": _counting(state, 0.2)}):
            events.append(e)

    task = asyncio.ensure_future(consume())
    for _ in range(100):
        await asyncio.sleep(0.005)
        if state.get("started", 0) >= gem._TOOL_ROUND_MAX_CONCURRENCY:
            break
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.sleep(0.4)
    assert state["started"] == gem._TOOL_ROUND_MAX_CONCURRENCY, state
    assert all(k == "tool_start" for k, _ in events), "no tool event after the cut"
    assert len(sent) == 1
    _assert_one_cancelled_round_line(caplog, door="stream", calls=n, ran=n,
                                     started=gem._TOOL_ROUND_MAX_CONCURRENCY)


@pytest.mark.asyncio
async def test_a_cancelled_round_line_never_carries_arguments(caplog):
    caplog.set_level(logging.INFO, logger=gem.__name__)

    async def slow(args):
        await asyncio.sleep(0.3)
        return {"ok": True}

    fcalls = [_FC("web_search", {"query": "SECRETQUERY my holdings"}), _FC("get_y", {"ticker": "ZZZQ"})]
    _named, _slots, jobs = gem._plan_tool_round(fcalls, {"web_search": slow, "get_y": slow})
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(gem._gather_tool_calls(jobs, door="stream", calls=2), timeout=0.02)
    _assert_one_cancelled_round_line(caplog, door="stream", calls=2, ran=2, started=2,
                                     never=("SECRETQUERY", "ZZZQ"))
    assert "tools=web_search,get_y" in caplog.records[-1].getMessage()
    await asyncio.sleep(0.35)       # let the shielded handlers settle before the loop closes


# ── the per-round job cap ─────────────────────────────────────────────────────


def test_the_job_cap_counts_only_new_calls_that_run_a_handler():
    """Duplicates of a planned job, memo replays and unknown tools never use a slot; the first
    NEW call past the cap is refused, and so is its own duplicate (each with its own dict)."""
    cap = gem._TOOL_ROUND_MAX_JOBS

    async def h(args):
        return {"ok": True}

    memo = {gem._tool_call_key("get_x", {"ticker": "MEMO"}): {"ok": "memo"}}
    fcalls = [_FC("get_x", {"ticker": t}) for t in _tickers(cap)]
    fcalls += [_FC("get_x", {"ticker": "T00"}),            # in-round duplicate → shares job 0
               _FC("ghost_a", {}), _FC("ghost_b", {"x": 1}),  # unknown tools → instant errors
               _FC("get_x", {"ticker": "MEMO"}),           # memo replay
               _FC("get_x", {"ticker": "OVER"}),           # the first NEW call past the cap
               _FC("get_x", {"ticker": "OVER"})]           # its duplicate
    _named, slots, jobs = gem._plan_tool_round(fcalls, {"get_x": h}, memo)
    kinds = [s[0] for s in slots]
    assert kinds == ["job"] * cap + ["job", "job", "job", "memo", "refused", "refused"]
    assert slots[cap] == ("job", 0, False)
    assert sum(1 for j in jobs if j[1] is not None) == cap
    assert [j[0] for j in jobs if j[1] is None] == ["ghost_a", "ghost_b"]
    over_a, over_b = slots[-2][1], slots[-1][1]
    assert over_a == over_b and over_a is not over_b, "each refused call owns its result"
    assert over_a["error"] == "too_many_tool_calls" and over_a["limit"] == cap
    assert "upstream" not in over_a, "a refusal is the model's ask, never a refund trigger"
    for vendor in ("gemini", "google", "openai", "fmp", "financial modeling"):
        assert vendor not in json.dumps(over_a).lower()

    # Junk calls FIRST must not use up the slots a real call needs.
    junk_first = [_FC(f"ghost_{i}", {}) for i in range(cap)]
    junk_first += [_FC("get_x", {"ticker": t}) for t in _tickers(cap)]
    _named, slots, jobs = gem._plan_tool_round(junk_first, {"get_x": h})
    assert [s[0] for s in slots] == ["job"] * (2 * cap), "no real call refused behind junk"
    assert sum(1 for j in jobs if j[1] is not None) == cap


@pytest.mark.asyncio
async def test_send_door_calls_past_the_cap_are_refused_in_place(caplog):
    caplog.set_level(logging.INFO, logger=gem.__name__)
    cap = gem._TOOL_ROUND_MAX_JOBS
    state: dict = {}
    names = _tickers(cap + 2)
    client, calls = _send_client([
        _calls(*[("get_x", {"ticker": t}) for t in names]),
        _Resp([_Part(text="Partial.")]),
    ])
    out = await client.generate_with_tools(prompt="p", tools=[],
                                           tool_handlers={"get_x": _counting(state, 0.0)})
    assert out["text"] == "Partial."
    assert state["started"] == cap, "only the first `cap` unique calls ran"
    sent = _sent_results(calls[1]["contents"][-1].parts, stream=False)
    assert len(sent) == cap + 2, "still ONE function_response per call"
    assert [r["ticker"] for r in sent[:cap]] == names[:cap], "call order kept"
    assert [r["error"] for r in sent[cap:]] == ["too_many_tool_calls"] * 2
    assert len(out["tool_results"]) == cap
    assert out["tool_errors"] == [{"name": "get_x", "error": "too_many_tool_calls", "upstream": False}] * 2
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert f"GEMINI_TOOL_ROUND door=send calls={cap + 2} ran={cap} refused=2 " in text
    refusal = [r.getMessage() for r in caplog.records if "refusing" in r.getMessage()]
    assert len(refusal) == 1 and names[-1] not in refusal[0], "the refusal names tools, not args"


@pytest.mark.asyncio
async def test_stream_door_a_refused_call_runs_when_the_model_asks_again():
    """Past the cap: no `tool_start`, its own error `tool` event, NOT memoised — the next
    round's re-request runs the handler, while a replay of a round-1 success stays memoised."""
    cap = gem._TOOL_ROUND_MAX_JOBS
    ran: list = []

    async def h(args):
        ran.append(args["ticker"])
        return {"ok": args["ticker"]}

    names = _tickers(cap + 2)
    client, sent = _stream_client([
        [_Chunk(*[_Part(fc=_FC("get_x", {"ticker": t})) for t in names])],
        [_Chunk(_Part(fc=_FC("get_x", {"ticker": names[-2]})), _Part(fc=_FC("get_x", {"ticker": names[-1]})),
                _Part(fc=_FC("get_x", {"ticker": names[0]})))],
        [_Chunk(_Part(text="Done."))],
    ])
    events = await _collect(client, tool_handlers={"get_x": h}, max_rounds=4)
    kinds = [k for k, _ in events]
    assert kinds[:cap] == ["tool_start"] * cap and kinds[cap:2 * cap + 2] == ["tool"] * (cap + 2)
    tools = [p for k, p in events if k == "tool"]
    r1 = tools[:cap + 2]
    assert [p["args"]["ticker"] for p in r1] == names
    assert [p["result"].get("error") for p in r1[cap:]] == ["too_many_tool_calls"] * 2
    assert all("memoized" not in p for p in r1[cap:])
    r2 = tools[cap + 2:]
    assert [p["result"] for p in r2] == [{"ok": names[-2]}, {"ok": names[-1]}, {"ok": names[0]}]
    assert "memoized" not in r2[0] and "memoized" not in r2[1] and r2[2]["memoized"] is True
    assert sorted(ran) == sorted(names), "each ticker ran exactly once over the turn"
    assert len(sent[1]) == cap + 2 and len(sent[2]) == 3, "one function_response per call"


# ── the round observer and the `deferred` memo rule (review 2026-10-09) ───────
#
# The chat doors pass `on_tool_round=web_turn.note_tool_round` so an AUTOMATIC web search called
# in the same round as one of Caydex's own tools waits for their results. The observer must see
# the round's names BEFORE any handler starts, on both doors (the send door's extra round too),
# must never break a round when it raises, and a result marked `deferred` must never be memoised
# (the model's identical call in the next round has to RUN).


@pytest.mark.asyncio
async def test_send_door_observer_sees_each_rounds_names_before_its_handlers():
    log: list = []
    client, _ = _send_client([
        _calls(("get_x", {"ticker": "AAPL"}), ("web_search", {"query": "Apple lawsuit"})),
        _calls(("web_search", {"query": "Apple lawsuit"})),
        _Resp([_Part(text="Answer.")]),
    ])
    out = await client.generate_with_tools(
        prompt="p", tools=[], extra_round_tools=frozenset({"web_search"}),
        tool_handlers={"get_x": _sleeper("get_x", 0.0, log),
                       "web_search": _sleeper("web_search", 0.0, log)},
        on_tool_round=lambda names: log.append(("round", names)),
    )
    assert out["text"] == "Answer."
    assert log[0] == ("round", ("get_x", "web_search"))
    # The extra round is the LAST round that runs tools: observed as an empty tuple (a search
    # deferred there could never run), still before its handlers.
    second = log.index(("round", ()))
    assert all(e[0] != "start" for e in log[:1]) and log.index(("end", "get_x")) < second
    assert log[second + 1] == ("start", "web_search"), "the extra round is observed before it runs"


@pytest.mark.asyncio
async def test_stream_door_observer_sees_each_rounds_names_before_its_handlers():
    log: list = []
    client, _ = _stream_client([
        [_Chunk(_Part(fc=_FC("get_x", {"ticker": "AAPL"})), _Part(fc=_FC("web_search", {"query": "q"})))],
        [_Chunk(_Part(fc=_FC("web_search", {"query": "q2"})))],
        [_Chunk(_Part(text="Done."))],
    ])
    await _collect(client, tool_handlers={"get_x": _sleeper("get_x", 0.0, log),
                                          "web_search": _sleeper("web_search", 0.0, log)},
                   on_tool_round=lambda names: log.append(("round", names)), max_rounds=4)
    rounds = [e for e in log if e[0] == "round"]
    assert rounds == [("round", ("get_x", "web_search")), ("round", ("web_search",))]
    assert log[0] == ("round", ("get_x", "web_search"))
    assert log.index(("round", ("web_search",))) > log.index(("end", "get_x"))


@pytest.mark.asyncio
@pytest.mark.parametrize("door", ["send", "stream"])
async def test_a_raising_observer_never_breaks_the_round(door, caplog):
    def bad(names):
        raise RuntimeError("observer bug")
    log: list = []
    with caplog.at_level(logging.WARNING, logger=gem.__name__):
        if door == "send":
            client, _ = _send_client([_calls(("get_x", {"ticker": "AAPL"})), _Resp([_Part(text="A.")])])
            out = await client.generate_with_tools(
                prompt="p", tools=[], tool_handlers={"get_x": _sleeper("get_x", 0.0, log)},
                on_tool_round=bad)
            assert out["text"] == "A." and out["tool_results"]
        else:
            client, _ = _stream_client([[_Chunk(_Part(fc=_FC("get_x", {"ticker": "AAPL"})))],
                                        [_Chunk(_Part(text="A."))]])
            events = await _collect(client, tool_handlers={"get_x": _sleeper("get_x", 0.0, log)},
                                    on_tool_round=bad)
            assert ("answer", "A.") in events
    assert ("end", "get_x") in log
    assert any("tool-round observer failed" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_stream_door_never_memoises_a_deferred_result():
    """A `deferred` result (the automatic web search waiting for Caydex's tools) must not be
    replayed to the model's identical call in the next round: that call runs the handler."""
    n = {"calls": 0}

    async def web(args):
        n["calls"] += 1
        if n["calls"] == 1:
            return {"web_search": True, "status": "deferred", "deferred": True}
        return {"web_search": True, "status": "ok"}

    client, _ = _stream_client([
        [_Chunk(_Part(fc=_FC("get_x", {"ticker": "AAPL"})), _Part(fc=_FC("web_search", {"query": "q"})))],
        [_Chunk(_Part(fc=_FC("web_search", {"query": "q"})))],
        [_Chunk(_Part(text="Done."))],
    ])
    events = await _collect(client, tool_handlers={"get_x": _sleeper("get_x", 0.0, []),
                                                   "web_search": web}, max_rounds=4)
    webs = [p for k, p in events if k == "tool" and p["name"] == "web_search"]
    assert n["calls"] == 2 and webs[1]["result"]["status"] == "ok" and "memoized" not in webs[1]


def test_memoisable():
    assert gem._memoisable({"ok": 1}) and gem._memoisable("x") and gem._memoisable(None)
    assert not gem._memoisable({"error": "x"}) and not gem._memoisable({"deferred": True})
    assert gem._memoisable({"deferred": "yes"}), "only the literal True marker"


# ── the observer names only what RUNS (final review 2026-10-09) ──────────────
#
# A memo replay, an in-round duplicate, a call refused past the cap and an unknown tool run no
# handler, so none may defer the automatic web search; and the last round that runs tools is
# observed as () — a search deferred there could never run.


def _auto_turn():
    from app.services.chat_web_search_service import WebSearchTurn
    return WebSearchTurn(user_id="u-observer", tier="auto")


def _deferral_probe(turn, seen):
    """A web_search handler that records whether the REAL predicate would defer it right now."""
    async def h(args):
        seen.append(turn.would_defer())
        return {"web_search": True, "status": "ok"}
    return h


@pytest.mark.asyncio
async def test_stream_door_a_memo_replay_beside_the_search_never_defers_it():
    turn, seen, log = _auto_turn(), [], []
    client, _ = _stream_client([
        [_Chunk(_Part(fc=_FC("get_x", {"ticker": "AAPL"})))],
        [_Chunk(_Part(fc=_FC("web_search", {"query": "q"})), _Part(fc=_FC("get_x", {"ticker": "AAPL"})))],
        [_Chunk(_Part(text="Done."))],
    ])
    events = await _collect(client, tool_handlers={"get_x": _sleeper("get_x", 0.0, log),
                                                   "web_search": _deferral_probe(turn, seen)},
                            on_tool_round=turn.note_tool_round, max_rounds=4)
    assert seen == [False], "the memo-replayed financials call ran nothing"
    assert any(k == "tool" and p.get("memoized") for k, p in events)


@pytest.mark.asyncio
async def test_stream_door_an_unknown_tool_beside_the_search_never_defers_it():
    turn, seen = _auto_turn(), []
    client, _ = _stream_client([
        [_Chunk(_Part(fc=_FC("no_such_tool", {})), _Part(fc=_FC("web_search", {"query": "q"})))],
        [_Chunk(_Part(text="Done."))],
    ])
    await _collect(client, tool_handlers={"web_search": _deferral_probe(turn, seen)},
                   on_tool_round=turn.note_tool_round, max_rounds=4)
    assert seen == [False]


@pytest.mark.asyncio
async def test_a_refused_call_past_the_cap_never_defers_the_search(monkeypatch):
    monkeypatch.setattr(gem, "_TOOL_ROUND_MAX_JOBS", 1)
    turn, seen, log = _auto_turn(), [], []
    client, _ = _stream_client([
        [_Chunk(_Part(fc=_FC("web_search", {"query": "q"})), _Part(fc=_FC("get_x", {"ticker": "AAPL"})))],
        [_Chunk(_Part(text="Done."))],
    ])
    await _collect(client, tool_handlers={"get_x": _sleeper("get_x", 0.0, log),
                                          "web_search": _deferral_probe(turn, seen)},
                   on_tool_round=turn.note_tool_round, max_rounds=4)
    assert seen == [False] and ("start", "get_x") not in log


@pytest.mark.asyncio
async def test_a_real_caydex_tool_beside_the_search_still_defers_it():
    """The control: a Caydex tool that RUNS in the same round, not the last round → deferred."""
    turn, seen, log = _auto_turn(), [], []
    client, _ = _stream_client([
        [_Chunk(_Part(fc=_FC("get_x", {"ticker": "AAPL"})), _Part(fc=_FC("web_search", {"query": "q"})))],
        [_Chunk(_Part(text="Done."))],
    ])
    await _collect(client, tool_handlers={"get_x": _sleeper("get_x", 0.0, log),
                                          "web_search": _deferral_probe(turn, seen)},
                   on_tool_round=turn.note_tool_round, max_rounds=4)
    assert seen == [True]


@pytest.mark.asyncio
async def test_stream_door_the_last_tool_round_never_defers_the_search():
    """Round `max_rounds - 1` is the last that runs tools (the final round ignores calls): a
    search deferred there could never run, so it is observed as () and runs."""
    turn, seen, log = _auto_turn(), [], []
    client, _ = _stream_client([
        [_Chunk(_Part(fc=_FC("get_y", {"ticker": "MSFT"})))],
        [_Chunk(_Part(fc=_FC("get_x", {"ticker": "AAPL"})), _Part(fc=_FC("web_search", {"query": "q"})))],
        [_Chunk(_Part(text="Done."))],
    ])
    await _collect(client, tool_handlers={"get_x": _sleeper("get_x", 0.0, log),
                                          "get_y": _sleeper("get_y", 0.0, log),
                                          "web_search": _deferral_probe(turn, seen)},
                   on_tool_round=turn.note_tool_round, max_rounds=2)
    assert seen == [False]


@pytest.mark.asyncio
async def test_send_door_a_mixed_extra_round_never_defers_the_search():
    """The send door's extra round is its last executed round: a follow-up that calls the search
    beside another tool must run it (before, it was deferred and the final call had no tools)."""
    turn, seen, log = _auto_turn(), [], []
    client, _ = _send_client([
        _calls(("get_x", {"ticker": "AAPL"})),
        _calls(("web_search", {"query": "q"}), ("get_news", {"ticker": "AAPL"})),
        _Resp([_Part(text="Answer.")]),
    ])
    out = await client.generate_with_tools(
        prompt="p", tools=[], extra_round_tools=frozenset({"web_search"}),
        tool_handlers={"get_x": _sleeper("get_x", 0.0, log), "get_news": _sleeper("get_news", 0.0, log),
                       "web_search": _deferral_probe(turn, seen)},
        on_tool_round=turn.note_tool_round,
    )
    assert out["text"] == "Answer." and seen == [False]
