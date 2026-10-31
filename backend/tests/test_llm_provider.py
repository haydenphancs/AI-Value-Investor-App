"""
`app/services/llm_provider.py` — Claude first, Gemini as the automatic fallback, for four roles.

Owner (2026-10-30): "it will not replace anything ... a second option like a fall back option".
So the load-bearing pins are:
  * every switch at its default → the EXACT Gemini call and the Gemini result object itself;
  * a role on Claude → Claude asked first with the same prompt/system/schema; ANY Claude failure
    → the same Gemini call with the original kwargs, byte for byte;
  * used-up credits / 429 / auth → a breaker that skips Claude for every role;
  * a tag outside the four roles never reaches Claude, whatever the switches say;
  * the judge never runs on the writer's model.
"""

from __future__ import annotations

import inspect
from typing import Any, Dict, List

import pytest

from app.config import settings
from app.integrations import anthropic_client as ac
from app.services import llm_provider as lp

GEMINI_KW = dict(
    system_instruction="SYS", model_name="gemini-2.5-flash",
    response_schema={"type": "OBJECT", "properties": {"hook": {"type": "STRING"}}},
    thinking_budget=0, usage_tag="marketing_writer",
)


class FakeGemini:
    def __init__(self) -> None:
        self.calls: List[tuple] = []
        self.result = {"text": "{}", "model": "gemini-2.5-flash", "tokens_used": 50,
                       "finish_reason": "STOP"}

    async def generate_json(self, prompt: str, **kwargs: Any) -> Dict[str, Any]:
        self.calls.append((prompt, dict(kwargs)))
        return self.result


class FakeClaude:
    def __init__(self, error: Exception = None) -> None:
        self.calls: List[tuple] = []
        self.error = error

    async def generate_json(self, prompt: str, **kwargs: Any) -> Dict[str, Any]:
        # Bound to the REAL ClaudeClient signature: a router that forwarded a Gemini-only keyword
        # (temperature, cache, service_tier, response_schema) would raise TypeError here as it
        # would in production.
        inspect.signature(ac.ClaudeClient.generate_json).bind(None, prompt, **kwargs)
        self.calls.append((prompt, dict(kwargs)))
        if self.error is not None:
            raise self.error
        return {"text": '{"hook": "x"}', "model": kwargs["model_name"], "tokens_used": 70,
                "finish_reason": "STOP"}


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    lp.reset_breaker()
    monkeypatch.setattr(lp, "_logged_once", set())
    monkeypatch.setattr(settings, "CAYDEX_ANTHROPIC_API_KEY", "sk-ant-test")
    for name in ("MARKETING_WRITER_PROVIDER", "MARKETING_JUDGE_PROVIDER",
                 "LEARN_WRITER_PROVIDER", "LEARN_JUDGE_PROVIDER"):
        monkeypatch.setattr(settings, name, "gemini")
    monkeypatch.setattr(settings, "CAYDEX_CLAUDE_WRITER_MODEL", "claude-sonnet-5-5")
    monkeypatch.setattr(settings, "CAYDEX_CLAUDE_JUDGE_MODEL", "claude-opus-5-5")
    monkeypatch.setattr(settings, "CAYDEX_CLAUDE_EFFORT", "low")
    yield
    lp.reset_breaker()


def _router(claude: FakeClaude = None):
    gemini, claude = FakeGemini(), claude or FakeClaude()
    return lp.ProviderRouter(gemini=gemini, claude=claude), gemini, claude


# ── the default path is the old Gemini call ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_every_switch_at_its_default_is_the_exact_gemini_call_and_result():
    router, gemini, claude = _router()
    for tag in ("marketing_writer", "marketing_judge", "learn_weekly_writer",
                "learn_weekly_judge", "learn_weekly_support"):
        result = await router.generate_json("P", **{**GEMINI_KW, "usage_tag": tag})
        assert result is gemini.result, "the Gemini result object itself — no added keys"
    assert claude.calls == []
    assert all(kw == {**GEMINI_KW, "usage_tag": t} for (_p, kw), t in zip(
        gemini.calls, ("marketing_writer", "marketing_judge", "learn_weekly_writer",
                       "learn_weekly_judge", "learn_weekly_support")))


@pytest.mark.asyncio
async def test_a_tag_outside_the_four_roles_never_reaches_claude(monkeypatch):
    for name in ("MARKETING_WRITER_PROVIDER", "MARKETING_JUDGE_PROVIDER",
                 "LEARN_WRITER_PROVIDER", "LEARN_JUDGE_PROVIDER"):
        monkeypatch.setattr(settings, name, "claude")
    router, gemini, claude = _router()
    for tag in ("learn_topic_peg", "learn_topic_label", "chat", None, ""):
        result = await router.generate_json("P", **{**GEMINI_KW, "usage_tag": tag})
        assert result is gemini.result
    assert claude.calls == []


@pytest.mark.asyncio
async def test_claude_switched_on_without_a_key_stays_on_gemini(monkeypatch):
    monkeypatch.setattr(settings, "MARKETING_WRITER_PROVIDER", "claude")
    monkeypatch.setattr(settings, "CAYDEX_ANTHROPIC_API_KEY", "  ")
    router, gemini, claude = _router()
    assert await router.generate_json("P", **GEMINI_KW) is gemini.result
    assert claude.calls == []


@pytest.mark.parametrize("value", ["CLAUDE", " claude "])
def test_the_switch_value_is_normalised(monkeypatch, value):
    monkeypatch.setattr(settings, "MARKETING_WRITER_PROVIDER", value)
    assert lp.provider_setting("MARKETING_WRITER_PROVIDER") == "claude"


@pytest.mark.parametrize("value", ["openai", "anthropic", "1", None])
def test_an_unknown_switch_value_is_gemini(monkeypatch, value):
    monkeypatch.setattr(settings, "MARKETING_WRITER_PROVIDER", value)
    assert lp.provider_setting("MARKETING_WRITER_PROVIDER") == "gemini"


# ── Claude first ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_claude_role_asks_claude_with_the_same_prompt_system_and_translated_schema(monkeypatch):
    monkeypatch.setattr(settings, "MARKETING_WRITER_PROVIDER", "claude")
    router, gemini, claude = _router()
    result = await router.generate_json("PROMPT", **GEMINI_KW)
    assert gemini.calls == []
    assert result["provider"] == "claude" and result["model"] == "claude-sonnet-5-5"
    prompt, kw = claude.calls[0]
    assert prompt == "PROMPT"
    assert kw["system_instruction"] == "SYS"
    assert kw["model_name"] == "claude-sonnet-5-5"
    assert kw["json_schema"] == {"type": "object", "properties": {"hook": {"type": "string"}},
                                 "additionalProperties": False}
    assert kw["effort"] == "low"
    assert kw["thinking"] == {"type": "between_tools"}, "thinking_budget 0 → Sonnet's thinking-off"
    assert "temperature" not in kw
    assert lp.served_by(result) == "claude:claude-sonnet-5-5"


@pytest.mark.asyncio
async def test_the_judge_roles_use_the_judge_model_and_opus_gets_no_thinking_field(monkeypatch):
    monkeypatch.setattr(settings, "MARKETING_JUDGE_PROVIDER", "claude")
    monkeypatch.setattr(settings, "LEARN_JUDGE_PROVIDER", "claude")
    router, _gemini, claude = _router()
    for tag in ("marketing_judge", "learn_weekly_judge", "learn_weekly_support"):
        await router.generate_json("P", **{**GEMINI_KW, "usage_tag": tag, "temperature": 0.0,
                                           "cache": False, "service_tier": "flex"})
    assert [kw["model_name"] for _p, kw in claude.calls] == ["claude-opus-5-5"] * 3
    assert all(kw["thinking"] is None for _p, kw in claude.calls)
    for _p, kw in claude.calls:   # the judges' Gemini-only knobs never reach Claude
        assert not {"temperature", "cache", "service_tier", "response_schema"} & set(kw)


@pytest.mark.asyncio
async def test_between_tools_is_never_sent_above_high_effort(monkeypatch):
    monkeypatch.setattr(settings, "MARKETING_WRITER_PROVIDER", "claude")
    monkeypatch.setattr(settings, "CAYDEX_CLAUDE_EFFORT", "max")
    router, _gemini, claude = _router()
    await router.generate_json("P", **GEMINI_KW)
    assert claude.calls[0][1]["thinking"] is None
    assert claude.calls[0][1]["effort"] == "max"


@pytest.mark.asyncio
async def test_an_unknown_effort_is_sent_as_low(monkeypatch):
    monkeypatch.setattr(settings, "MARKETING_WRITER_PROVIDER", "claude")
    monkeypatch.setattr(settings, "CAYDEX_CLAUDE_EFFORT", "turbo")
    router, _gemini, claude = _router()
    await router.generate_json("P", **GEMINI_KW)
    assert claude.calls[0][1]["effort"] == "low"


@pytest.mark.asyncio
async def test_a_judge_on_the_writers_model_stays_on_gemini(monkeypatch):
    monkeypatch.setattr(settings, "MARKETING_JUDGE_PROVIDER", "claude")
    monkeypatch.setattr(settings, "CAYDEX_CLAUDE_JUDGE_MODEL", "claude-sonnet-5-5")
    router, gemini, claude = _router()
    result = await router.generate_json("P", **{**GEMINI_KW, "usage_tag": "marketing_judge"})
    assert result is gemini.result and claude.calls == []


# ── the fallback ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("error,opens_breaker", [
    (ac.ClaudeRefusalException("declined", tokens_used=12), False),
    (ac.ClaudeTruncatedException("cut", tokens_used=12), False),
    (ac.ClaudeBadAnswerException("empty", tokens_used=12), False),
    (ac.ClaudeTimeoutException("slow"), False),
    (ac.ClaudeUnavailableException("529"), False),
    (ac.ClaudeException("bad schema"), False),
    (ac.ClaudeRateLimitException("429"), True),
    (ac.ClaudeCreditsExhaustedException("credit balance is too low"), True),
    (ac.ClaudeAuthException("401"), True),
    (ac.ClaudeConfigException("no key"), True),
])
@pytest.mark.asyncio
async def test_any_claude_failure_makes_the_original_gemini_call(monkeypatch, error, opens_breaker):
    monkeypatch.setattr(settings, "MARKETING_WRITER_PROVIDER", "claude")
    router, gemini, claude = _router(FakeClaude(error))
    kw = {**GEMINI_KW, "temperature": 0.3, "cache": False}
    result = await router.generate_json("PROMPT", **kw)
    assert gemini.calls == [("PROMPT", kw)], "the Gemini call is the caller's, byte for byte"
    assert result["provider"] == "gemini"
    assert result["fallback_reason"] == type(error).__name__
    assert result["text"] == gemini.result["text"]
    assert result["tokens_used"] == 50 + error.tokens_used, "the failed Claude call's tokens count"
    assert lp.breaker_open() is opens_breaker
    assert lp.served_by(result).startswith("gemini (Claude fallback: ")


@pytest.mark.asyncio
async def test_an_open_breaker_skips_claude_for_every_role(monkeypatch):
    for name in ("MARKETING_WRITER_PROVIDER", "LEARN_JUDGE_PROVIDER"):
        monkeypatch.setattr(settings, name, "claude")
    router, gemini, claude = _router(FakeClaude(ac.ClaudeCreditsExhaustedException("out")))
    await router.generate_json("P", **GEMINI_KW)
    assert len(claude.calls) == 1
    second = await router.generate_json("P", **{**GEMINI_KW, "usage_tag": "learn_weekly_judge"})
    assert len(claude.calls) == 1, "no second Claude call while the breaker is open"
    assert second["fallback_reason"] == "breaker_open"
    assert len(gemini.calls) == 2


@pytest.mark.asyncio
async def test_the_breaker_closes_after_its_window(monkeypatch):
    monkeypatch.setattr(settings, "MARKETING_WRITER_PROVIDER", "claude")
    monkeypatch.setattr(settings, "CAYDEX_CLAUDE_BREAKER_SECONDS", 0.0)
    router, _gemini, claude = _router(FakeClaude(ac.ClaudeRateLimitException("429")))
    await router.generate_json("P", **GEMINI_KW)
    await router.generate_json("P", **GEMINI_KW)
    assert len(claude.calls) == 2


@pytest.mark.asyncio
async def test_a_gemini_failure_after_the_fallback_propagates_unchanged(monkeypatch):
    monkeypatch.setattr(settings, "MARKETING_WRITER_PROVIDER", "claude")
    router, gemini, _claude = _router(FakeClaude(ac.ClaudeUnavailableException("529")))

    class Boom(RuntimeError):
        pass

    async def fail(prompt, **kwargs):
        raise Boom("gemini down")

    gemini.generate_json = fail
    with pytest.raises(Boom):
        await router.generate_json("P", **GEMINI_KW)


@pytest.mark.asyncio
async def test_an_unexpected_error_on_the_claude_path_still_falls_back_loudly(monkeypatch, caplog):
    monkeypatch.setattr(settings, "MARKETING_WRITER_PROVIDER", "claude")
    router, gemini, _claude = _router(FakeClaude(KeyError("bug")))
    with caplog.at_level("ERROR", logger="app.services.llm_provider"):
        result = await router.generate_json("P", **GEMINI_KW)
    assert result["fallback_reason"] == "KeyError" and len(gemini.calls) == 1
    assert any(r.levelname == "ERROR" and r.exc_info for r in caplog.records)
    assert lp.breaker_open() is False


@pytest.mark.asyncio
async def test_a_cancellation_on_the_claude_path_is_never_turned_into_a_gemini_call(monkeypatch):
    import asyncio

    monkeypatch.setattr(settings, "MARKETING_WRITER_PROVIDER", "claude")
    router, gemini, _claude = _router(FakeClaude(asyncio.CancelledError()))
    with pytest.raises(asyncio.CancelledError):
        await router.generate_json("P", **GEMINI_KW)
    assert gemini.calls == []


# ── the measurement adapter and the lease input ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_claude_only_never_falls_back():
    claude = FakeClaude(ac.ClaudeRefusalException("declined"))
    with pytest.raises(ac.ClaudeRefusalException):
        await lp.ClaudeOnly("claude-opus-5-5", claude=claude).generate_json("P", **GEMINI_KW)
    ok = await lp.ClaudeOnly("claude-opus-5-5", claude=FakeClaude()).generate_json("P", **GEMINI_KW)
    assert ok["provider"] == "claude" and ok["model"] == "claude-opus-5-5"


def test_the_marketing_lease_adds_claudes_deadline_only_while_a_marketing_role_is_on_claude(monkeypatch):
    from app.services.marketing import script_service as ss

    assert ss.claude_first_seconds() == 0.0
    monkeypatch.setattr(settings, "LEARN_WRITER_PROVIDER", "claude")
    assert ss.claude_first_seconds() == 0.0, "a Learn role does not touch the marketing lease"
    monkeypatch.setattr(settings, "MARKETING_JUDGE_PROVIDER", "claude")
    assert ss.claude_first_seconds() == settings.CAYDEX_CLAUDE_REQUEST_TIMEOUT_SECONDS
    base = ss.worst_case_model_call_seconds()
    monkeypatch.setattr(settings, "MARKETING_JUDGE_PROVIDER", "gemini")
    assert ss.worst_case_model_call_seconds() == base - settings.CAYDEX_CLAUDE_REQUEST_TIMEOUT_SECONDS


def test_the_default_lease_numbers_are_unchanged():
    from app.services.marketing import script_service as ss

    assert ss.claude_first_seconds() == 0.0
    assert ss.LEASE_SECONDS == int(__import__("math").ceil(ss.worst_case_model_call_seconds()
                                                           + ss.LEASE_MARGIN_SECONDS))


# ── review round (2026-10-30) ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_billed_claude_attempt_rides_on_the_gemini_error_when_both_fail(monkeypatch):
    monkeypatch.setattr(settings, "MARKETING_WRITER_PROVIDER", "claude")
    router, gemini, _claude = _router(FakeClaude(ac.ClaudeTruncatedException("cut", tokens_used=12)))

    class Boom(RuntimeError):
        pass

    async def fail(prompt, **kwargs):
        raise Boom("gemini down")

    gemini.generate_json = fail
    with pytest.raises(Boom) as info:
        await router.generate_json("P", **GEMINI_KW)
    assert lp.claude_tokens_of(info.value) == 12
    assert getattr(info.value, "tokens_used", None) is None, "the caller's own attribute is untouched"


@pytest.mark.parametrize("value,expected", [(5, 5), (0, 0), (-3, 0), (True, 0), ("7", 0), (None, 0)])
def test_claude_tokens_of_reads_defensively(value, expected):
    e = RuntimeError()
    setattr(e, lp.CLAUDE_TOKENS_ATTR, value)
    assert lp.claude_tokens_of(e) == expected
    assert lp.claude_tokens_of(RuntimeError()) == 0


@pytest.mark.parametrize("key", ["sk-ant-api03-" + "Z" * 40, "AIzaSy" + "Z" * 33])
def test_a_key_pasted_into_a_switch_is_never_logged_raw(monkeypatch, caplog, key):
    monkeypatch.setattr(settings, "MARKETING_WRITER_PROVIDER", key)
    monkeypatch.setattr(settings, "CAYDEX_CLAUDE_EFFORT", key)
    with caplog.at_level("ERROR", logger="app.services.llm_provider"):
        assert lp.provider_setting("MARKETING_WRITER_PROVIDER") == "gemini"
        assert lp._effort() == "low"
    text = " ".join(r.getMessage() for r in caplog.records)
    assert caplog.records and key not in text and "ZZZZZZZZZZ" not in text


@pytest.mark.asyncio
async def test_claude_only_retries_transport_errors_but_never_a_refusal():
    sleeps: List[float] = []

    async def no_sleep(seconds):
        sleeps.append(seconds)

    class Flaky(FakeClaude):
        def __init__(self, errors):
            super().__init__()
            self.errors = list(errors)

        async def generate_json(self, prompt, **kwargs):
            if self.errors:
                self.calls.append((prompt, dict(kwargs)))
                raise self.errors.pop(0)
            return await super().generate_json(prompt, **kwargs)

    flaky = Flaky([ac.ClaudeRateLimitException("429"), ac.ClaudeTimeoutException("slow")])
    ok = await lp.ClaudeOnly("claude-opus-5-5", claude=flaky, retries=3, sleep=no_sleep).generate_json(
        "P", **GEMINI_KW)
    assert ok["provider"] == "claude" and len(flaky.calls) == 3 and sleeps == [2.0, 4.0]

    refusing = Flaky([ac.ClaudeRefusalException("no", tokens_used=5)])
    with pytest.raises(ac.ClaudeRefusalException) as info:
        await lp.ClaudeOnly("claude-opus-5-5", claude=refusing, retries=3, sleep=no_sleep).generate_json(
            "P", **GEMINI_KW)
    assert len(refusing.calls) == 1 and info.value.tokens_used == 5

    down = Flaky([ac.ClaudeUnavailableException("529")] * 4)
    with pytest.raises(ac.ClaudeUnavailableException):
        await lp.ClaudeOnly("claude-opus-5-5", claude=down, retries=3, sleep=no_sleep).generate_json(
            "P", **GEMINI_KW)
    assert len(down.calls) == 4


@pytest.mark.parametrize("name,value", [
    ("CAYDEX_CLAUDE_REQUEST_TIMEOUT_SECONDS", "241"), ("CAYDEX_CLAUDE_REQUEST_TIMEOUT_SECONDS", "0"),
    ("CAYDEX_CLAUDE_REQUEST_TIMEOUT_SECONDS", "nan"), ("CAYDEX_CLAUDE_REQUEST_TIMEOUT_SECONDS", "inf"),
    ("CAYDEX_CLAUDE_MAX_TOKENS", "100"), ("CAYDEX_CLAUDE_MAX_TOKENS", "64001"),
    ("CAYDEX_CLAUDE_BREAKER_SECONDS", "-1"), ("CAYDEX_CLAUDE_BREAKER_SECONDS", "nan"),
])
def test_an_out_of_bounds_claude_setting_fails_the_deploy(monkeypatch, name, value):
    from pydantic import ValidationError

    from app.config import Settings

    monkeypatch.setenv(name, value)
    with pytest.raises(ValidationError) as info:
        Settings()
    assert name in str(info.value)


def test_the_marketing_lease_stays_under_the_workers_poll_session_at_the_timeout_ceiling(monkeypatch):
    """The lease is refreshed before each model call and must outlive the slowest one; a crashed
    owner then blocks the run for at most the lease, which one worker poll session must outlast
    (marketing/main.py SCRIPT_POLL_BUDGET_SECONDS). Checked with a marketing role on Claude and the
    timeout at its declared ceiling."""
    import ast
    import math
    from pathlib import Path

    from app.config import Settings
    from app.services.marketing import script_service as ss

    src = (Path(__file__).resolve().parents[1] / "marketing/main.py").read_text(encoding="utf-8")
    budget = next(eval(compile(ast.Expression(n.value), "b", "eval"))
                  for n in ast.walk(ast.parse(src)) if isinstance(n, ast.Assign)
                  and any(getattr(t, "id", None) == "SCRIPT_POLL_BUDGET_SECONDS" for t in n.targets))
    ceiling = next(m.le for m in Settings.model_fields["CAYDEX_CLAUDE_REQUEST_TIMEOUT_SECONDS"].metadata
                   if getattr(m, "le", None) is not None)
    monkeypatch.setattr(settings, "MARKETING_WRITER_PROVIDER", "claude")
    monkeypatch.setattr(settings, "CAYDEX_CLAUDE_REQUEST_TIMEOUT_SECONDS", float(ceiling))
    lease = math.ceil(ss.worst_case_model_call_seconds() + ss.LEASE_MARGIN_SECONDS)
    assert ss.claude_first_seconds() == ceiling
    assert lease < budget, (lease, budget)


def test_a_short_wrong_word_is_still_shown_so_the_typo_is_findable():
    assert lp._shown("claud") == "'claud'"
    assert lp._shown("x" * 25) == "<25 chars, not shown>"
    assert lp._shown(None) == "''"


def test_effective_effort_is_what_is_sent_not_the_raw_setting(monkeypatch):
    monkeypatch.setattr(settings, "CAYDEX_CLAUDE_EFFORT", "hgih")
    assert lp.effective_effort() == "low"
    monkeypatch.setattr(settings, "CAYDEX_CLAUDE_EFFORT", " MEDIUM ")
    assert lp.effective_effort() == "medium"
