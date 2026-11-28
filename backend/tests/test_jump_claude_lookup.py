"""`app/services/jump_claude_lookup_service.py` — the Updates jump search's fallback web lookup.

Owner 2026-11-27: "do Brave 80 a day, and use Claude API (200 credits as fallback)", "Claude Sonnet
5.5", "$40 a month" (+ 30 lookups a day). What these tests pin:
  * the request: the web search tool (web_search_20260209, max_uses 2, the hygiene deny-list as
    blocked_domains, a US / New York location), no temperature / top_p / top_k / tool_choice /
    structured output / fallback, effort low, Sonnet's between_tools; the prompt holds ONLY the
    cleaned name and up / down;
  * the rows: only CITED result blocks, the verbatim cited text as the snippet (≤ 300), never the
    model's own sentences, never an encrypted field;
  * the budget order — claim the day's unit, read the month, pre-charge the worst case with a
    raising RPC, call, top up only above the reserve — its refunds, and the month key;
  * the failure policy: credits / 401 → off for the day (+ the shared breaker, by the provider);
    403 / 400 → off for the day for this feature only, never the breaker; 429 / 5xx / timeout →
    a 600 s pause; cancellation keeps every unit;
  * the gates (off by default) and the identity rule: nothing returned or logged here names a model
    or vendor.

Hermetic: the budget service and the Claude client are fakes; the one wire test runs the real SDK over
a mock transport. Hand-written page text only — never a real card or page.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import re
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock

import anthropic
import httpx2
import pytest

from app.config import Settings, settings
from app.integrations import anthropic_client as ac
from app.services import jump_claude_lookup_service as jcl
from app.services import llm_provider as lp
from app.services import web_result_hygiene as hygiene
from app.services.chat_budget_service import ChatBudgetService, ChatBudgetUnavailable

NOW = datetime(2026, 11, 27, 16, 0, tzinfo=timezone.utc)   # Fri 11:00 ET
TODAY = "2026-11-27"
MONTH = "2026-11-30"
NAME = "Acme Widgets"
_URL_A = "https://news.example.org/acme-order"
_URL_B = "https://wire.example.net/acme-plant"
_URL_C = "https://blog.example.com/acme-other"
_VENDOR = re.compile(r"(?i)claude|anthropic|sonnet|opus|fable|haiku")


# ── fakes ─────────────────────────────────────────────────────────────────────
class FakeBudget:
    """The chat budget service's four calls, in memory, recorded in order."""

    def __init__(self, *, day_used: int = 0, month_spent: int = 0, fail: str = "", race_extra: int = 0) -> None:
        self.calls: List[tuple] = []
        self.turns: Dict[tuple, int] = {}
        self.tokens: Dict[tuple, int] = {}
        self.fail = fail
        self.race_extra = race_extra
        self.turns[(jcl._DAY_BUCKET, TODAY)] = day_used
        self.tokens[(jcl._MONTH_BUCKET, MONTH)] = month_spent

    def try_claim_turn(self, bucket: str, limit: Optional[int] = None, *, day: Optional[str] = None) -> int:
        self.calls.append(("claim", bucket, limit, day))
        if self.fail == "claim":
            raise ChatBudgetUnavailable("down")
        n = self.turns.get((bucket, day), 0)
        if n >= limit:
            return -1
        self.turns[(bucket, day)] = n + 1
        return n + 1

    def refund_turn(self, bucket: str, *, day: Optional[str] = None) -> None:
        self.calls.append(("refund", bucket, day))
        self.turns[(bucket, day)] = max(self.turns.get((bucket, day), 0) - 1, 0)

    def read_tokens(self, bucket: str, day: str) -> int:
        self.calls.append(("read", bucket, day))
        if self.fail == "read":
            raise ChatBudgetUnavailable("down")
        return self.tokens.get((bucket, day), 0)

    def add_tokens_strict(self, bucket: str, n: int, day: str) -> int:
        self.calls.append(("add", bucket, n, day))
        if self.fail == "add" or (self.fail == "topup" and len([c for c in self.calls if c[0] == "add"]) > 1):
            raise ChatBudgetUnavailable("down")
        self.tokens[(bucket, day)] = self.tokens.get((bucket, day), 0) + n + self.race_extra
        self.race_extra = 0
        return self.tokens[(bucket, day)]

    def kinds(self) -> List[str]:
        return [c[0] for c in self.calls]

    def month_total(self) -> int:
        return self.tokens.get((jcl._MONTH_BUCKET, MONTH), 0)


def _answer(**over: Any) -> Dict[str, Any]:
    out = {
        "text": "Acme rose after news of an order.",
        "model": "claude-sonnet-5-5",
        "tokens_used": 9_300,
        "usage": {"input": 9_000, "output": 300, "cache_read": 0, "cache_write": 0},
        "web_search_requests": 2,
        "results": [
            {"url": _URL_A, "title": "Acme Widgets lands a utility order", "page_age": "November 27, 2026"},
            {"url": _URL_B, "title": "Acme opens a second plant", "page_age": None},
            {"url": _URL_C, "title": "An Acme post nobody cited", "page_age": "2 hours ago"},
        ],
        "citations": [
            {"url": _URL_A, "title": "Acme Widgets lands a utility order",
             "cited_text": "Acme Widgets said a utility placed a multi-year order."},
            {"url": _URL_B, "title": None, "cited_text": "The company opened its second plant this week."},
            {"url": _URL_A, "title": None, "cited_text": "Shipments start in the spring."},
        ],
        "search_errors": {},
        "stop_reason": "end_turn",
    }
    out.update(over)
    return out


class FakeSearchClaude:
    def __init__(self, answer: Optional[Dict[str, Any]] = None, error: Optional[BaseException] = None,
                 block: Optional[asyncio.Event] = None) -> None:
        self.calls: List[tuple] = []
        self.answer = answer if answer is not None else _answer()
        self.error = error
        self.block = block

    async def cited_web_search(self, prompt: str, **kwargs: Any) -> Dict[str, Any]:
        inspect.signature(ac.ClaudeClient.cited_web_search).bind(None, prompt, **kwargs)
        self.calls.append((prompt, dict(kwargs)))
        if self.block is not None:
            await self.block.wait()
        if self.error is not None:
            raise self.error
        return self.answer


def _http_error(cls, status: Optional[int], message: str = "x request_id=req_test123"):
    e = cls(message)
    e.status_code = status
    return e


@pytest.fixture(autouse=True)
def _on(monkeypatch):
    jcl.reset_state()
    lp.reset_breaker()
    monkeypatch.setattr(settings, "UPDATES_JUMP_CLAUDE_FALLBACK_ENABLED", True)
    monkeypatch.setattr(settings, "UPDATES_JUMP_WEB_SEARCH_ENABLED", True)
    monkeypatch.setattr(settings, "CAYDEX_ANTHROPIC_API_KEY", "sk-ant-test-not-a-real-key")
    monkeypatch.setattr(settings, "UPDATES_JUMP_CLAUDE_MODEL", "claude-sonnet-5-5")
    monkeypatch.setattr(settings, "UPDATES_JUMP_CLAUDE_MONTHLY_BUDGET_USD", 40.0)
    monkeypatch.setattr(settings, "UPDATES_JUMP_CLAUDE_DAILY_LOOKUPS", 30)
    monkeypatch.setattr(settings, "CAYDEX_CLAUDE_REQUEST_TIMEOUT_SECONDS", 120.0)
    yield
    jcl.reset_state()
    lp.reset_breaker()


def _wire(monkeypatch, *, budget: Optional[FakeBudget] = None, claude: Optional[FakeSearchClaude] = None):
    budget = budget or FakeBudget()
    claude = claude or FakeSearchClaude()
    monkeypatch.setattr(jcl, "get_chat_budget_service", lambda: budget)
    monkeypatch.setattr(ac, "get_claude_client", lambda: claude)
    return budget, claude


async def _lookup(**over: Any) -> jcl.LookupOutcome:
    args = dict(scope="ACME", query_name=NAME, direction="up", now=NOW, trigger="T2")
    args.update(over)
    return await jcl.lookup(**args)


# ── constants and the prompt ──────────────────────────────────────────────────
def test_the_call_constants_are_the_approved_ones():
    assert jcl.WEB_TOOL == "web_search_20260209" == ac.WEB_SEARCH_TOOL_TYPE
    assert jcl.WEB_SEARCH_MODELS == ("claude-sonnet-5-5", "claude-opus-5-5")
    assert (jcl.MAX_USES, jcl.MAX_TOKENS, jcl.EFFORT) == (2, 3000, "low")
    assert 60 <= jcl.DEADLINE_SECONDS <= 120
    assert (jcl.WORST_INPUT_TOKENS, jcl.WEB_SEARCH_MICROS, jcl.PAUSE_SECONDS) == (60_000, 10_000, 600)
    assert jcl.SNIPPET_CAP == 300
    # $0.12 in + $0.03 out + 2 × $0.01 on Sonnet 5.5; Opus 5.5 at its own rates
    assert jcl.reserve_micros("claude-sonnet-5-5") == 170_000
    assert jcl.reserve_micros("claude-opus-5-5") == 320_000
    assert jcl.reserve_micros("claude-unknown-9") is None


def test_the_default_switch_is_off():
    assert Settings.model_fields["UPDATES_JUMP_CLAUDE_FALLBACK_ENABLED"].default is False


@pytest.mark.parametrize("direction", ["up", "down"])
def test_the_prompt_holds_only_the_name_and_the_direction(direction):
    prompt = jcl.build_prompt("  Acme   Widgets ", direction)
    assert prompt == jcl.USER_TEMPLATE.format(name=NAME, word=direction)
    assert NAME in prompt and f" {direction} " in prompt
    assert not re.search(r"\d", prompt), "no figure or date"
    assert "ACME" not in prompt, "no symbol"
    assert not re.search(r"\d", jcl.SYSTEM_PROMPT) and not re.search(r"\d", jcl.USER_TEMPLATE)
    for word in ("Caydex", "user", "watch"):
        assert word.lower() not in prompt.lower()
    assert "NONE" in jcl.SYSTEM_PROMPT, "an honest no-cause answer is allowed"


@pytest.mark.parametrize("name", [
    "https://evil.example.com", "a@b.com", "Acme; DROP TABLE", "", "   ", "x" * 81, None, 5, ["Acme"],
    "_Acme", "Acme <b>", "Acme\x00Widgets",
])
def test_an_unusable_name_is_never_sent(name):
    assert jcl.build_prompt(name, "up") is None


def test_a_name_on_two_lines_is_sent_on_one():
    assert jcl.build_prompt("Acme\n  Widgets", "up") == jcl.USER_TEMPLATE.format(name=NAME, word="up")


@pytest.mark.parametrize("direction", ["UP", "flat", "", None, 1, ["up"]])
def test_an_unknown_direction_is_never_sent(direction):
    assert jcl.build_prompt(NAME, direction) is None


def test_the_blocked_domains_are_the_hygiene_deny_list_and_fit_the_api():
    hosts = getattr(hygiene, "DENIED_HOSTS", None) or hygiene._DENIED_HOSTS
    assert jcl.blocked_domains() == tuple(sorted(hosts))
    assert 0 < len(jcl.blocked_domains()) <= ac.MAX_BLOCKED_DOMAINS
    ac.web_search_tool(jcl.MAX_USES, list(jcl.blocked_domains()), jcl.USER_LOCATION)   # accepted as is


# ── the request on the wire (the real SDK over a mock transport) ──────────────
def _sdk_message() -> Dict[str, Any]:
    return {"id": "msg_s", "type": "message", "role": "assistant", "model": "claude-sonnet-5-5",
            "content": [
                {"type": "web_search_tool_result", "tool_use_id": "s1", "content": [
                    {"type": "web_search_result", "url": _URL_A, "title": "Acme Widgets lands a utility order",
                     "page_age": "November 27, 2026", "encrypted_content": "ENC-SECRET"}]},
                {"type": "text", "text": "It rose on an order.", "citations": [
                    {"type": "web_search_result_location", "url": _URL_A, "title": "x",
                     "cited_text": "Acme Widgets said a utility placed a multi-year order.",
                     "encrypted_index": "IDX-SECRET"}]}],
            "stop_reason": "end_turn", "stop_sequence": None,
            "usage": {"input_tokens": 9000, "output_tokens": 300, "cache_read_input_tokens": 0,
                      "cache_creation_input_tokens": 0,
                      "server_tool_use": {"web_search_requests": 1, "web_fetch_requests": 0}}}


@pytest.mark.asyncio
async def test_the_request_on_the_wire(monkeypatch):
    seen: List[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(200, json=_sdk_message())

    monkeypatch.setattr(ac, "_client", anthropic.AsyncAnthropic(
        api_key="sk-ant-test-not-a-real-key", max_retries=0,
        http_client=anthropic.DefaultAsyncHttpxClient(transport=httpx2.MockTransport(handler))))
    budget = FakeBudget()
    monkeypatch.setattr(jcl, "get_chat_budget_service", lambda: budget)
    out = await _lookup(direction="down")
    assert len(seen) == 1
    body = json.loads(seen[0].content)
    assert body["model"] == "claude-sonnet-5-5"
    assert body["tools"] == [{"type": "web_search_20260209", "name": "web_search", "max_uses": 2,
                              "blocked_domains": list(jcl.blocked_domains()),
                              "user_location": {"type": "approximate", "country": "US",
                                                "timezone": "America/New_York"}}]
    assert body["messages"] == [{"role": "user", "content": jcl.build_prompt(NAME, "down")}]
    assert body["system"] == jcl.SYSTEM_PROMPT
    assert body["max_tokens"] == 3000
    assert body["output_config"] == {"effort": "low"}
    assert body["thinking"] == {"type": "between_tools"}
    for banned in ("temperature", "top_p", "top_k", "tool_choice", "fallbacks", "metadata"):
        assert banned not in body, banned
    assert seen[0].headers.get("anthropic-version") == "2026-10-21"
    assert out.status == jcl.STATUS_OK
    assert out.rows == [{"url": _URL_A, "title": "Acme Widgets lands a utility order",
                         "description": "Acme Widgets said a utility placed a multi-year order.",
                         "page_age": "November 27, 2026"}]
    assert "SECRET" not in json.dumps(out.rows)


# ── the rows ──────────────────────────────────────────────────────────────────
def test_rows_are_the_cited_results_with_their_verbatim_cited_text():
    rows, counts = jcl.map_rows(_answer())
    assert rows == [
        {"url": _URL_A, "title": "Acme Widgets lands a utility order",
         "description": "Acme Widgets said a utility placed a multi-year order. Shipments start in the spring.",
         "page_age": "November 27, 2026"},
        {"url": _URL_B, "title": "Acme opens a second plant",
         "description": "The company opened its second plant this week.", "page_age": None},
    ]
    assert counts["uncited"] == 1 and counts["rows"] == 2
    joined = json.dumps(rows)
    assert "Acme rose after news of an order" not in joined, "the model's own sentences are discarded"
    assert _URL_C not in joined, "an uncited result is dropped"


def test_an_orphan_citation_an_empty_quote_and_a_repeat_are_dropped():
    answer = _answer(citations=[
        {"url": "https://elsewhere.example.org/x", "title": "x", "cited_text": "Text with no result block."},
        {"url": _URL_A, "title": None, "cited_text": "   "},
        {"url": _URL_A, "title": None, "cited_text": "Acme said  an order\nwas placed."},
        {"url": _URL_A, "title": None, "cited_text": "Acme said an order was placed."},
        {"url": None, "cited_text": "x"},
        "not a dict",
    ], results=[*_answer()["results"], {"url": _URL_A, "title": "a repeat", "page_age": None},
                {"url": "has space.example.com/x y", "title": "t"}, {"url": _URL_B}, "junk"])
    rows, counts = jcl.map_rows(answer)
    assert rows == [{"url": _URL_A, "title": "Acme Widgets lands a utility order",
                     "description": "Acme said an order was placed.", "page_age": "November 27, 2026"}]
    assert counts["orphan_citation"] == 3 and counts["empty_cited_text"] == 1
    assert counts["result_repeat"] == 1 and counts["result_dropped"] == 3


def test_a_long_quote_is_capped_at_300_and_titles_are_one_line():
    long = "Acme " + "word " * 200
    rows, _ = jcl.map_rows(_answer(
        results=[{"url": _URL_A, "title": "Acme\n  lands   an order", "page_age": "  2 hours ago  "}],
        citations=[{"url": _URL_A, "title": None, "cited_text": long}]))
    assert len(rows[0]["description"]) == jcl.SNIPPET_CAP and rows[0]["description"].endswith("…")
    assert rows[0]["title"] == "Acme lands an order" and rows[0]["page_age"] == "2 hours ago"


@pytest.mark.parametrize("text", ["NONE", "none.", "  None  "])
def test_an_answer_of_none_gives_no_rows(text):
    rows, counts = jcl.map_rows(_answer(text=text))
    assert rows == [] and counts["answer_none"] == 1


@pytest.mark.parametrize("answer", [
    _answer(citations=[]), _answer(results=[], citations=[]), _answer(results=None, citations=None),
    _answer(text="", citations=[]), None, "text", [],
])
def test_no_web_citation_means_no_rows(answer):
    rows, _counts = jcl.map_rows(answer)
    assert rows == []


def test_rows_are_capped():
    results = [{"url": f"https://n{i}.example.org/a", "title": f"Acme {i}", "page_age": None} for i in range(15)]
    cites = [{"url": r["url"], "title": None, "cited_text": f"Acme quote {i}."} for i, r in enumerate(results)]
    rows, counts = jcl.map_rows(_answer(results=results, citations=cites))
    assert len(rows) == jcl.MAX_ROWS and counts["over_row_cap"] == 5


# ── money ─────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("now,key", [
    (datetime(2026, 11, 30, 20, 0, tzinfo=timezone.utc), "2026-11-30"),
    (datetime(2026, 12, 1, 3, 0, tzinfo=timezone.utc), "2026-11-30"),    # Nov 30, 22:00 ET
    (datetime(2026, 12, 1, 5, 0, tzinfo=timezone.utc), "2026-12-31"),
    (datetime(2026, 12, 31, 12, 0, tzinfo=timezone.utc), "2026-12-31"),
    (datetime(2027, 1, 1, 4, 59, tzinfo=timezone.utc), "2026-12-31"),    # Dec 31, 23:59 ET
    (datetime(2027, 2, 10, 15, 0, tzinfo=timezone.utc), "2027-02-28"),
    (datetime(2028, 2, 10, 15, 0, tzinfo=timezone.utc), "2028-02-29"),   # leap year
    (datetime(2028, 2, 29, 15, 0, tzinfo=timezone.utc), "2028-02-29"),
    (datetime(2026, 11, 27, 11, 0), "2026-11-30"),                       # naive = UTC
])
def test_the_month_key_is_the_last_day_of_the_et_month(now, key):
    assert jcl.month_key(now) == key


def test_the_retention_sweep_never_deletes_the_month_row_while_the_month_runs():
    """`cleanup_old_budget_rows` deletes `budget_day < today − 7`; the month key is never before today."""
    day = date(2026, 1, 1)
    while day < date(2029, 1, 1):
        noon = datetime(day.year, day.month, day.day, 17, 0, tzinfo=timezone.utc)
        key = date.fromisoformat(jcl.month_key(noon))
        assert key >= day and key - day < timedelta(days=31), day
        cutoff = day - timedelta(days=ChatBudgetService.RETENTION_DAYS)
        assert not key < cutoff
        day += timedelta(days=1)


def test_actual_cost_is_the_usage_at_list_rates_plus_a_cent_a_search():
    usage = {"input": 9_000, "output": 300, "cache_read": 0, "cache_write": 0}
    assert jcl.actual_micros("claude-sonnet-5-5", usage, 2) == 18_000 + 3_000 + 20_000
    assert jcl.actual_micros("claude-sonnet-5-5-20261001", usage, 0) == 21_000
    assert jcl.actual_micros("claude-sonnet-5-5", usage, None) == 21_000 + 2 * 10_000, "unknown count = worst"
    assert jcl.actual_micros("claude-sonnet-5-5", {"cache_read": 1}, 0) == 1, "0.2 µ$ rounded up"
    for bad in (None, "x", {"input": -1}, {"input": True}, {"input": 1.5}):
        assert jcl.actual_micros("claude-sonnet-5-5", bad, 1) is None
    assert jcl.actual_micros("claude-haiku-5-5", {"cache_read": 5}, 0) is None, "no rate for a kind with tokens"
    assert jcl.actual_micros(None, usage, 1) is None and jcl.actual_micros("gpt-x", usage, 1) is None


@pytest.mark.asyncio
async def test_the_budget_order_on_a_clean_lookup(monkeypatch):
    budget, claude = _wire(monkeypatch, budget=FakeBudget(month_spent=1_000_000))
    out = await _lookup()
    assert out.status == jcl.STATUS_OK and len(out.rows) == 2
    assert budget.calls == [
        ("claim", jcl._DAY_BUCKET, 30, TODAY),
        ("read", jcl._MONTH_BUCKET, MONTH),
        ("add", jcl._MONTH_BUCKET, 170_000, MONTH),
    ], "the actual cost (41,000) is under the reserve: nothing more is added"
    assert budget.month_total() == 1_170_000
    assert len(claude.calls) == 1
    assert out.counts["reserve_micros"] == 170_000 and out.counts["actual_micros"] == 41_000
    assert jcl._DAY_BUCKET != jcl._MONTH_BUCKET


@pytest.mark.asyncio
async def test_a_call_above_its_reserve_adds_only_the_difference(monkeypatch):
    heavy = _answer(usage={"input": 100_000, "output": 2_500, "cache_read": 0, "cache_write": 0})
    budget, _c = _wire(monkeypatch, claude=FakeSearchClaude(heavy))
    out = await _lookup()
    # 100,000 × $2 + 2,500 × $10 per 1M + 2 searches = 245,000 µ$ → 75,000 above the reserve
    assert budget.calls[-1] == ("add", jcl._MONTH_BUCKET, 75_000, MONTH)
    assert budget.month_total() == 245_000 and out.counts["topup_micros"] == 75_000


@pytest.mark.asyncio
async def test_a_failed_top_up_is_logged_and_the_rows_still_come_back(monkeypatch, caplog):
    heavy = _answer(usage={"input": 100_000, "output": 2_500, "cache_read": 0, "cache_write": 0})
    _wire(monkeypatch, budget=FakeBudget(fail="topup"), claude=FakeSearchClaude(heavy))
    with caplog.at_level("ERROR", logger="app.services.jump_claude_lookup_service"):
        out = await _lookup()
    assert out.status == jcl.STATUS_OK and out.rows
    assert any("under-recorded by 75000" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_the_days_lookups_used_up_means_no_read_and_no_call(monkeypatch):
    budget, claude = _wire(monkeypatch, budget=FakeBudget(day_used=30))
    out = await _lookup()
    assert out.status == jcl.STATUS_DAY_CAPPED and budget.kinds() == ["claim"] and claude.calls == []


@pytest.mark.asyncio
async def test_a_used_up_day_closes_the_gate_until_the_next_et_day(monkeypatch, caplog):
    """Review 2026-11-27: with the day's lookups gone, `enabled()` used to stay True, so the jump
    search kept a no-cause episode "still searching" through two spaced lookups that could only
    answer day_capped. The day now latches like the month does."""
    from app.services import insight_move
    from app.services import jump_web_search_service as jws

    budget, claude = _wire(monkeypatch, budget=FakeBudget(day_used=30))
    ep = jws._Episode(direction="up", anchor=NOW, verdict=insight_move.VERDICT_NO_CAUSE,
                      verdict_urls=frozenset())
    search = jws.JumpWebSearchService()
    with monkeypatch.context() as m:
        m.setattr(jcl, "datetime", _FrozenDatetime)
        assert jcl.enabled() is True and search._fallback_may_run(ep) is True
    with caplog.at_level("INFO", logger="app.services.jump_claude_lookup_service"):
        first = await _lookup()
        second = await _lookup(scope="OTHER")
    assert first.status == jcl.STATUS_DAY_CAPPED
    assert second.status == jcl.STATUS_DISABLED and second.counts["off_day_full"] == 1
    assert budget.kinds() == ["claim"], "no second claim once the day is seen used up"
    assert claude.calls == []
    assert sum("used up" in r.getMessage() for r in caplog.records) == 1
    with monkeypatch.context() as m:
        m.setattr(jcl, "datetime", _FrozenDatetime)
        assert jcl.enabled() is False
        assert search._fallback_may_run(ep) is False, "the jump search no longer waits on a lookup"
        # a higher daily setting reopens the gate (the latch is keyed on the limit, like the month's cap)
        m.setattr(settings, "UPDATES_JUMP_CLAUDE_DAILY_LOOKUPS", 31)
        assert jcl.enabled() is True
    # the next ET day starts afresh
    out = await _lookup(now=NOW + timedelta(days=1))
    assert out.status == jcl.STATUS_OK and len(claude.calls) == 1


@pytest.mark.asyncio
async def test_a_full_month_refunds_the_days_unit_and_latches_the_month(monkeypatch):
    budget, claude = _wire(monkeypatch, budget=FakeBudget(month_spent=40_000_000 - 169_999))
    out = await _lookup()
    assert out.status == jcl.STATUS_MONTH_CAPPED and claude.calls == []
    assert budget.kinds() == ["claim", "read", "refund"]
    assert budget.turns[(jcl._DAY_BUCKET, TODAY)] == 0
    with monkeypatch.context() as m:
        m.setattr(jcl, "datetime", _FrozenDatetime)
        assert jcl.enabled() is False, "the month is latched full in this process"
    assert jcl._gate_problem(NOW) == "month_full"
    # exactly at the cap is allowed
    jcl.reset_state()
    budget, claude = _wire(monkeypatch, budget=FakeBudget(month_spent=40_000_000 - 170_000))
    assert (await _lookup()).status == jcl.STATUS_OK


class _FrozenDatetime(datetime):
    """`jcl.datetime` frozen at NOW for `enabled()`. `now()` returns an instance of THIS class:
    while it is patched, the module's `isinstance(now, datetime)` reads this class, and a plain
    datetime there made every gate unreadable, so `enabled()` read False for the wrong reason."""

    @classmethod
    def now(cls, tz=None):
        frozen = NOW if tz is None else NOW.astimezone(tz)
        return cls.combine(frozen.date(), frozen.timetz())


@pytest.mark.asyncio
async def test_another_instance_charging_first_holds_the_cap_strictly(monkeypatch):
    budget, claude = _wire(monkeypatch, budget=FakeBudget(month_spent=39_700_000, race_extra=200_000))
    out = await _lookup()
    assert out.status == jcl.STATUS_MONTH_CAPPED and claude.calls == []
    assert budget.kinds() == ["claim", "read", "add", "refund"], "the reserve stays charged: never under-counted"


@pytest.mark.parametrize("fail,kinds", [
    ("claim", ["claim"]),
    ("read", ["claim", "read", "refund"]),
    ("add", ["claim", "read", "add", "refund"]),
])
@pytest.mark.asyncio
async def test_an_unavailable_budget_fails_closed(monkeypatch, fail, kinds):
    budget, claude = _wire(monkeypatch, budget=FakeBudget(fail=fail))
    out = await _lookup()
    assert out.status == jcl.STATUS_BUDGET_UNAVAILABLE and claude.calls == []
    assert budget.kinds() == kinds


@pytest.mark.asyncio
async def test_an_open_breaker_after_the_read_refunds_and_never_pre_charges(monkeypatch):
    budget, claude = _wire(monkeypatch)
    real_read = budget.read_tokens

    def read_then_open(bucket, day):
        lp._open_breaker("ClaudeAuthException (tag=marketing_judge)")
        return real_read(bucket, day)

    monkeypatch.setattr(budget, "read_tokens", read_then_open)
    out = await _lookup()
    assert out.status == jcl.STATUS_BREAKER_OPEN and claude.calls == []
    assert budget.kinds() == ["claim", "read", "refund"] and budget.month_total() == 0, "never pre-charged"


@pytest.mark.asyncio
async def test_a_breaker_opening_between_the_charge_and_the_call_refunds_the_unit_only(monkeypatch):
    budget, claude = _wire(monkeypatch)
    real_add = budget.add_tokens_strict

    def add_then_open(bucket, n, day):
        total = real_add(bucket, n, day)
        lp._open_breaker("ClaudeCreditsExhaustedException (tag=marketing_judge)")
        return total

    monkeypatch.setattr(budget, "add_tokens_strict", add_then_open)
    out = await _lookup()
    assert out.status == jcl.STATUS_BREAKER_OPEN and claude.calls == []
    assert budget.kinds() == ["claim", "read", "add", "refund"] and budget.month_total() == 170_000


# ── failures ──────────────────────────────────────────────────────────────────
_FAILURES = [
    # (error, status, latched off today, paused, breaker opened, the day's unit refunded)
    (ac.ClaudeCreditsExhaustedException("credit balance is too low request_id=req_1"),
     jcl.STATUS_CREDITS, True, False, True, False),
    (_http_error(ac.ClaudeAuthException, 401), jcl.STATUS_AUTH, True, False, True, False),
    (_http_error(ac.ClaudeAuthException, 403), jcl.STATUS_TOOL_REFUSED, True, False, False, False),
    (_http_error(ac.ClaudeException, 400), jcl.STATUS_REQUEST_REFUSED, True, False, False, False),
    (_http_error(ac.ClaudeException, 404), jcl.STATUS_REQUEST_REFUSED, True, False, False, False),
    (_http_error(ac.ClaudeRateLimitException, 429), jcl.STATUS_RATE_LIMITED, False, True, False, False),
    (_http_error(ac.ClaudeUnavailableException, 529), jcl.STATUS_UNAVAILABLE, False, True, False, False),
    (_http_error(ac.ClaudeUnavailableException, 500), jcl.STATUS_UNAVAILABLE, False, True, False, False),
    (ac.ClaudeTimeoutException("slow"), jcl.STATUS_TIMEOUT, False, True, False, False),
    (ac.ClaudeConfigException("no key"), jcl.STATUS_NOT_CONFIGURED, True, False, False, True),
    (ac.ClaudeRequestInvalidException("bad"), jcl.STATUS_INVALID_REQUEST, True, False, False, True),
]


@pytest.mark.parametrize("error,status,latched,paused,breaker,refunded", _FAILURES)
@pytest.mark.asyncio
async def test_the_failure_policy(monkeypatch, caplog, error, status, latched, paused, breaker, refunded):
    budget, claude = _wire(monkeypatch, claude=FakeSearchClaude(error=error))
    with caplog.at_level("INFO"):
        out = await _lookup()
    assert out.status == status and out.rows == []
    assert len(claude.calls) == 1
    assert (jcl._state.off_day == date(2026, 11, 27)) is latched
    assert (jcl._state.paused_until > jcl._clock()) is paused
    assert lp.breaker_open() is breaker, "only credits or a 401 open the shared breaker"
    assert ("refund" in budget.kinds()) is refunded, "never refunded after a call that may have billed"
    assert budget.month_total() == 170_000, "the reserve stays charged"
    mine = [r for r in caplog.records if r.name == "app.services.jump_claude_lookup_service"]
    if latched:
        errors = [r for r in mine if r.levelname == "ERROR"]
        assert len(errors) == 1 and "off for the rest of 2026-11-27 ET" in errors[0].getMessage()
    for r in mine:
        assert not _VENDOR.search(r.getMessage()), r.getMessage()


@pytest.mark.asyncio
async def test_a_latched_day_makes_no_more_calls_and_logs_no_more_errors(monkeypatch, caplog):
    budget, claude = _wire(monkeypatch, claude=FakeSearchClaude(error=_http_error(ac.ClaudeAuthException, 403)))
    with caplog.at_level("ERROR", logger="app.services.jump_claude_lookup_service"):
        await _lookup()
        second = await _lookup()
    assert second.status == jcl.STATUS_DISABLED and second.counts["off_off_today"] == 1
    assert len(claude.calls) == 1 and budget.kinds().count("claim") == 1
    assert sum(r.levelname == "ERROR" for r in caplog.records) == 1
    # the next ET day starts afresh
    out = await _lookup(now=NOW + timedelta(days=1))
    assert out.status == jcl.STATUS_TOOL_REFUSED and len(claude.calls) == 2


@pytest.mark.asyncio
async def test_a_pause_lasts_600_seconds(monkeypatch):
    clock = [1_000.0]
    monkeypatch.setattr(jcl, "_clock", lambda: clock[0])
    _budget, claude = _wire(monkeypatch, claude=FakeSearchClaude(error=_http_error(ac.ClaudeRateLimitException, 429)))
    await _lookup()
    clock[0] += 599
    assert (await _lookup()).status == jcl.STATUS_DISABLED
    clock[0] += 1
    assert (await _lookup()).status == jcl.STATUS_RATE_LIMITED
    assert len(claude.calls) == 2


@pytest.mark.parametrize("error,status", [
    (ac.ClaudeRefusalException("declined", tokens_used=9_300, model="claude-sonnet-5-5",
                               usage={"input": 9_000, "output": 300, "cache_read": 0, "cache_write": 0}),
     jcl.STATUS_REFUSED),
    (ac.ClaudeTruncatedException("cut", tokens_used=9_300, model="claude-sonnet-5-5",
                                 usage={"input": 9_000, "output": 300, "cache_read": 0, "cache_write": 0}),
     jcl.STATUS_TRUNCATED),
    (ac.ClaudeBadAnswerException("pause_turn", tokens_used=9_300, model="claude-sonnet-5-5",
                                 usage={"input": 9_000, "output": 300, "cache_read": 0, "cache_write": 0}),
     jcl.STATUS_BAD_ANSWER),
])
@pytest.mark.asyncio
async def test_a_billed_unusable_answer_keeps_its_charge_and_pauses_nothing(monkeypatch, error, status):
    error.web_searches = 2
    budget, _c = _wire(monkeypatch, claude=FakeSearchClaude(error=error))
    out = await _lookup()
    assert out.status == status and out.rows == []
    assert "refund" not in budget.kinds() and budget.month_total() == 170_000
    assert out.counts["actual_micros"] == 41_000 and out.counts["web_searches"] == 2
    assert jcl._state.off_day is None and jcl._state.paused_until == 0.0 and lp.breaker_open() is False
    # a heavy billed failure is topped up like a success
    jcl.reset_state()
    heavy = type(error)("x", tokens_used=1, model="claude-sonnet-5-5",
                        usage={"input": 100_000, "output": 2_500, "cache_read": 0, "cache_write": 0})
    heavy.web_searches = None
    budget, _c = _wire(monkeypatch, claude=FakeSearchClaude(error=heavy))
    await _lookup()
    assert budget.month_total() == 245_000


@pytest.mark.asyncio
async def test_cancellation_keeps_the_days_unit_and_the_charge(monkeypatch):
    block = asyncio.Event()
    budget, claude = _wire(monkeypatch, claude=FakeSearchClaude(block=block))
    task = asyncio.create_task(_lookup())
    for _ in range(200):
        await asyncio.sleep(0.005)
        if claude.calls:
            break
    assert claude.calls, "the call started"
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert budget.kinds() == ["claim", "read", "add"], "no refund, no top-up after a cancel"
    assert budget.turns[(jcl._DAY_BUCKET, TODAY)] == 1 and budget.month_total() == 170_000


@pytest.mark.asyncio
async def test_an_unexpected_error_is_a_status_with_its_stack_never_a_raise(monkeypatch, caplog):
    class Broken(FakeSearchClaude):
        async def cited_web_search(self, prompt, **kwargs):
            raise KeyError("boom")

    _wire(monkeypatch, claude=Broken())
    with caplog.at_level("ERROR", logger="app.services.jump_claude_lookup_service"):
        out = await _lookup()
    assert out.status == jcl.STATUS_ERROR and out.rows == []
    assert any(r.exc_info for r in caplog.records)


@pytest.mark.parametrize("over", [dict(query_name="https://x.example.com"), dict(direction="flat"),
                                  dict(now="2026-11-27"), dict(query_name=None)])
@pytest.mark.asyncio
async def test_bad_input_claims_nothing(monkeypatch, over):
    budget, claude = _wire(monkeypatch)
    out = await _lookup(**over)
    assert out.status == jcl.STATUS_BAD_INPUT and budget.calls == [] and claude.calls == []


# ── gates ─────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("name,value,gate", [
    ("UPDATES_JUMP_CLAUDE_FALLBACK_ENABLED", False, "switch_off"),
    ("UPDATES_JUMP_CLAUDE_FALLBACK_ENABLED", "true", "switch_off"),
    ("UPDATES_JUMP_WEB_SEARCH_ENABLED", False, "web_search_off"),
    ("CAYDEX_ANTHROPIC_API_KEY", None, "no_key"),
    ("CAYDEX_ANTHROPIC_API_KEY", "   ", "no_key"),
    ("UPDATES_JUMP_CLAUDE_MODEL", "claude-haiku-5-5", "model_not_allowed"),
    ("UPDATES_JUMP_CLAUDE_MODEL", "", "model_not_allowed"),
    ("UPDATES_JUMP_CLAUDE_MONTHLY_BUDGET_USD", 0.0, "no_budget"),
    ("UPDATES_JUMP_CLAUDE_MONTHLY_BUDGET_USD", float("nan"), "no_budget"),
    ("UPDATES_JUMP_CLAUDE_DAILY_LOOKUPS", 0, "no_daily_lookups"),
])
@pytest.mark.asyncio
async def test_every_gate_is_read_at_call_time_and_closes_before_any_claim(monkeypatch, name, value, gate):
    budget, claude = _wire(monkeypatch)
    monkeypatch.setattr(settings, name, value)
    assert jcl.enabled() is False
    out = await _lookup()
    assert out.status == jcl.STATUS_DISABLED and out.counts[f"off_{gate}"] == 1
    assert budget.calls == [] and claude.calls == []


@pytest.mark.asyncio
async def test_an_unpriced_allowed_model_is_off_with_one_error(monkeypatch, caplog):
    _wire(monkeypatch)
    monkeypatch.setattr(jcl, "WEB_SEARCH_MODELS", (*jcl.WEB_SEARCH_MODELS, "claude-newer-6"))
    monkeypatch.setattr(settings, "UPDATES_JUMP_CLAUDE_MODEL", "claude-newer-6")
    with caplog.at_level("ERROR", logger="app.services.jump_claude_lookup_service"):
        for _ in range(3):
            assert jcl.enabled() is False
    assert sum(r.levelname == "ERROR" for r in caplog.records) == 1
    assert not any(_VENDOR.search(r.getMessage()) for r in caplog.records)


def test_enabled_with_every_gate_open_and_an_open_breaker_closes_it():
    assert jcl.enabled() is True
    lp._open_breaker("ClaudeCreditsExhaustedException (tag=x)")
    assert jcl.enabled() is False


def test_an_unreadable_gate_is_closed(monkeypatch):
    def boom(_now):
        raise RuntimeError("x")

    monkeypatch.setattr(jcl, "_gate_problem", boom)
    assert jcl.enabled() is False


# ── identity and logs ─────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_nothing_returned_or_logged_here_names_a_model_or_vendor_or_a_page(monkeypatch, caplog):
    _wire(monkeypatch)
    with caplog.at_level("INFO"):
        out = await _lookup()
    assert not _VENDOR.search(json.dumps(out.rows)) and not _VENDOR.search(out.status)
    assert not any(_VENDOR.search(str(k)) for k in out.counts)
    assert repr(out) == "LookupOutcome(status='ok', rows=2)", "the repr carries no page"
    mine = [r.getMessage() for r in caplog.records if r.name == "app.services.jump_claude_lookup_service"]
    assert len(mine) == 1 and mine[0].startswith("UPDATES_JUMP_LOOKUP scope=ACME trigger=T2 direction=up status=ok")
    line = mine[0]
    for leak in (NAME, "Acme", _URL_A, "example", "utility", "plant", "November"):
        assert leak not in line, leak
    assert not _VENDOR.search(line)
    assert "rows=2" in line and "web_searches=2" in line and "reserve_micros=170000" in line


@pytest.mark.asyncio
async def test_odd_scope_and_trigger_values_are_never_echoed(monkeypatch, caplog):
    _wire(monkeypatch)
    with caplog.at_level("INFO", logger="app.services.jump_claude_lookup_service"):
        await _lookup(scope="acme widgets <script>", trigger="T1; drop")
    line = [r.getMessage() for r in caplog.records][-1]
    assert "scope=? trigger=? " in line and "script" not in line and "drop" not in line


def test_the_sentry_scrubber_drops_this_modules_frames():
    from app.log_redaction import _WEB_RESULT_MODULES

    assert "app.services.jump_claude_lookup_service" in _WEB_RESULT_MODULES


# ── the chat budget service's new keyword and strict calls ────────────────────
@pytest.fixture
def svc():
    s = ChatBudgetService.__new__(ChatBudgetService)
    s.supabase = MagicMock()
    return s


def _rpc_returns(s, value=None, error=None):
    call = MagicMock()
    if error is not None:
        call.execute.side_effect = error
    else:
        call.execute.return_value = MagicMock(data=value)
    s.supabase.rpc.return_value = call


def test_the_day_keyword_reaches_every_rpc_and_defaults_to_today(svc, monkeypatch):
    from app.services import chat_budget_service as cbs

    monkeypatch.setattr(cbs, "_budget_day", lambda: "2026-11-27")
    _rpc_returns(svc, 1)
    svc.try_claim_turn("b", 5, day="2026-11-30")
    assert svc.supabase.rpc.call_args[0][1] == {"p_user_id": "b", "p_day": "2026-11-30", "p_turn_limit": 5}
    svc.try_claim_turn("b", 5)
    assert svc.supabase.rpc.call_args[0][1]["p_day"] == "2026-11-27", "unchanged without the keyword"
    svc.refund_turn("b", day="2026-11-30")
    assert svc.supabase.rpc.call_args[0] == ("release_chat_turn", {"p_user_id": "b", "p_day": "2026-11-30"})
    svc.refund_turn("b")
    assert svc.supabase.rpc.call_args[0][1]["p_day"] == "2026-11-27"
    svc.record_tokens("b", 7, day="2026-11-30")
    assert svc.supabase.rpc.call_args[0] == ("add_chat_tokens", {"p_user_id": "b", "p_day": "2026-11-30",
                                                                 "p_tokens": 7})


@pytest.mark.parametrize("day", ["2026-11-31", "11/30/2026", "2026-1-5", 20261130, date(2026, 11, 30), ""])
def test_a_bad_day_is_a_caller_bug_before_any_rpc(svc, day):
    with pytest.raises(ValueError):
        svc.try_claim_turn("b", 5, day=day)
    with pytest.raises(ValueError):
        svc.read_tokens("b", day)
    assert not svc.supabase.rpc.called and not svc.supabase.table.called


def _table_returns(s, data=None, error=None):
    chain = s.supabase.table.return_value.select.return_value.eq.return_value.eq.return_value.limit.return_value
    if error is not None:
        chain.execute.side_effect = error
    else:
        chain.execute.return_value = MagicMock(data=data)


@pytest.mark.parametrize("data,expected", [([], 0), ([{"token_count": 170000}], 170000),
                                           ([{"token_count": "42"}], 42)])
def test_read_tokens_reads_the_row(svc, data, expected):
    _table_returns(svc, data)
    assert svc.read_tokens("b", "2026-11-30") == expected
    svc.supabase.table.assert_called_with("chat_usage_budget")


@pytest.mark.parametrize("data", [None, {"token_count": 1}, [{"token_count": -1}], [{"token_count": 1.5}],
                                  [{"token_count": True}], [{"token_count": "x"}], [{}], ["row"]])
def test_read_tokens_raises_on_anything_it_cannot_vouch_for(svc, data):
    _table_returns(svc, data)
    with pytest.raises(ChatBudgetUnavailable):
        svc.read_tokens("b", "2026-11-30")


def test_read_tokens_raises_when_the_read_fails(svc):
    _table_returns(svc, error=RuntimeError("supabase down"))
    with pytest.raises(ChatBudgetUnavailable):
        svc.read_tokens("b", "2026-11-30")


def test_add_tokens_strict_returns_the_total_and_raises_on_failure(svc):
    _rpc_returns(svc, 340000)
    assert svc.add_tokens_strict("b", 170000, "2026-11-30") == 340000
    assert svc.supabase.rpc.call_args[0] == ("add_chat_tokens", {"p_user_id": "b", "p_day": "2026-11-30",
                                                                 "p_tokens": 170000})
    for value in (None, -1, "x", True):
        _rpc_returns(svc, value)
        with pytest.raises(ChatBudgetUnavailable):
            svc.add_tokens_strict("b", 1, "2026-11-30")
    _rpc_returns(svc, error=RuntimeError("down"))
    with pytest.raises(ChatBudgetUnavailable):
        svc.add_tokens_strict("b", 1, "2026-11-30")


@pytest.mark.parametrize("n", [0, -5, True, 1.5, 2 ** 31])
def test_add_tokens_strict_refuses_a_bad_amount_before_any_rpc(svc, n):
    with pytest.raises(ValueError):
        svc.add_tokens_strict("b", n, "2026-11-30")
    assert not svc.supabase.rpc.called
