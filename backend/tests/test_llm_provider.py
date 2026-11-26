"""
`app/services/llm_provider.py` — Claude first, Gemini as the automatic fallback, for four roles;
and `ClaudeOnly`, Claude with NO fallback, for the measurement scripts and the marketing per-post
art brief and check (owner decision 2026-11-15, "Google draws, Claude checks").

Owner (2026-10-30): "it will not replace anything ... a second option like a fall back option".
So the load-bearing pins are:
  * every switch at its default → the EXACT Gemini call and the Gemini result object itself;
  * a role on Claude → Claude asked first with the same prompt/system/schema; ANY Claude failure
    → the same Gemini call with the original kwargs, byte for byte;
  * used-up credits / 429 / auth → a breaker that skips Claude for every role;
  * a tag outside the four roles never reaches Claude THROUGH THE ROUTER, whatever the switches say
    (the art is not a role: it calls ClaudeOnly itself, and ROLES / the lease never see it);
  * the judge never runs on the writer's model;
  * ClaudeOnly without its new keywords is today's call; the art's policy opens the shared breaker
    only on used-up credits or auth, never on a 429 / overload / timeout.
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


# ── the Claude tokens by model and kind (the marketing cost line prices them apart, 2026-10-31) ──────

_U = {"input": 900, "output": 300, "cache_read": 0, "cache_write": 0}


class BillingClaude(FakeClaude):
    """FakeClaude answering like the real client: `usage` by kind, its total in `tokens_used`."""

    async def generate_json(self, prompt: str, **kwargs: Any) -> Dict[str, Any]:
        result = await super().generate_json(prompt, **kwargs)
        return {**result, "tokens_used": 1_200, "usage": dict(_U)}


def _truncated(model: str = "claude-sonnet-5-5") -> ac.ClaudeTruncatedException:
    return ac.ClaudeTruncatedException("cut", tokens_used=12, model=model,
                                       usage={"input": 10, "output": 2, "cache_read": 0, "cache_write": 0})


@pytest.mark.asyncio
async def test_a_claude_answer_carries_its_tokens_by_model_and_kind(monkeypatch):
    for name in ("MARKETING_WRITER_PROVIDER", "MARKETING_JUDGE_PROVIDER"):
        monkeypatch.setattr(settings, name, "claude")
    router, _gemini, claude = _router(BillingClaude())
    writer = await router.generate_json("P", **GEMINI_KW)
    judge = await router.generate_json("P", **{**GEMINI_KW, "usage_tag": "marketing_judge"})
    assert writer[lp.CLAUDE_USAGE_KEY] == {"claude-sonnet-5-5": _U}
    assert judge[lp.CLAUDE_USAGE_KEY] == {"claude-opus-5-5": _U}, "keyed by the model that billed"
    assert writer["tokens_used"] == 1_200, "a breakdown, never added to the total"
    assert lp.claude_usage_tokens(writer[lp.CLAUDE_USAGE_KEY]) == writer["tokens_used"]


@pytest.mark.asyncio
async def test_a_failed_claude_attempts_usage_rides_on_the_gemini_answer(monkeypatch):
    monkeypatch.setattr(settings, "MARKETING_WRITER_PROVIDER", "claude")
    router, gemini, _claude = _router(FakeClaude(_truncated()))
    result = await router.generate_json("P", **GEMINI_KW)
    assert result["tokens_used"] == 50 + 12
    assert result[lp.CLAUDE_USAGE_KEY] == {"claude-sonnet-5-5": {"input": 10, "output": 2, "cache_read": 0,
                                                                  "cache_write": 0}}
    assert lp.CLAUDE_USAGE_KEY not in gemini.result, "Gemini's own result object is never mutated"
    # a Claude call that never answered billed nothing: no breakdown at all
    for error in (ac.ClaudeTimeoutException("slow"), ac.ClaudeUnavailableException("529"), RuntimeError("bug")):
        lp.reset_breaker()
        router, _g, _c = _router(FakeClaude(error))
        assert lp.CLAUDE_USAGE_KEY not in await router.generate_json("P", **GEMINI_KW)
    # nor does the breaker's straight-to-Gemini path
    router, _g, _c = _router(FakeClaude(ac.ClaudeRateLimitException("429")))
    await router.generate_json("P", **GEMINI_KW)
    assert lp.CLAUDE_USAGE_KEY not in await router.generate_json("P", **GEMINI_KW)


@pytest.mark.asyncio
async def test_a_billed_claude_attempts_usage_rides_on_the_gemini_error_when_both_fail(monkeypatch):
    monkeypatch.setattr(settings, "MARKETING_WRITER_PROVIDER", "claude")
    router, gemini, _claude = _router(FakeClaude(_truncated()))

    class Boom(RuntimeError):
        pass

    async def fail(prompt, **kwargs):
        e = Boom("gemini down")
        setattr(e, lp.CLAUDE_USAGE_KEY, {"claude-opus-5-5": {"output": 4}})   # an earlier hop's, kept
        raise e

    gemini.generate_json = fail
    with pytest.raises(Boom) as info:
        await router.generate_json("P", **GEMINI_KW)
    assert lp.claude_usage_of(info.value) == {
        "claude-opus-5-5": {"input": 0, "output": 4, "cache_read": 0, "cache_write": 0},
        "claude-sonnet-5-5": {"input": 10, "output": 2, "cache_read": 0, "cache_write": 0}}


@pytest.mark.asyncio
async def test_claude_only_sums_the_usage_of_its_retries():
    async def no_sleep(_seconds):
        return None

    class Flaky(BillingClaude):
        def __init__(self):
            super().__init__()
            self.errors = [ac.ClaudeRateLimitException("429", tokens_used=3, model="claude-opus-5-5",
                                                       usage={"input": 3})]

        async def generate_json(self, prompt, **kwargs):
            if self.errors:
                raise self.errors.pop(0)
            return await super().generate_json(prompt, **kwargs)

    ok = await lp.ClaudeOnly("claude-opus-5-5", claude=Flaky(), retries=2, sleep=no_sleep).generate_json(
        "P", **GEMINI_KW)
    assert ok["tokens_used"] == 1_203
    assert ok[lp.CLAUDE_USAGE_KEY] == {"claude-opus-5-5": {**_U, "input": 903}}


@pytest.mark.parametrize("raw", ["x", [], 7, {"m": "x"}, {"": {"input": 1}}, {7: {"input": 1}},
                                 {"m": {"input": -1}}, {"m": {"input": True}}, {"m": {"input": 1.0}},
                                 {"m": {"input": "1"}}, {"m": {"thinking": 1}}])
def test_an_unreadable_claude_usage_is_refused_whole(raw, caplog):
    assert lp.parse_claude_usage(raw) is None
    assert lp.merge_claude_usage({"m": {"input": 1}}, raw) == {"m": {"input": 1, "output": 0, "cache_read": 0,
                                                                     "cache_write": 0}}
    assert lp.claude_usage_tokens(raw) == 0
    e = RuntimeError()
    setattr(e, lp.CLAUDE_USAGE_KEY, raw)
    assert lp.claude_usage_of(e) == {} and lp.claude_usage_of({lp.CLAUDE_USAGE_KEY: raw}) == {}
    assert any("unreadable" in r.getMessage() for r in caplog.records)


def test_claude_usage_is_summed_model_by_model_and_kind_by_kind():
    assert lp.parse_claude_usage(None) == {} and lp.parse_claude_usage({}) == {}
    assert lp.parse_claude_usage({"m": {"input": 0}}) == {}, "a model that billed nothing is dropped"
    merged = lp.merge_claude_usage({"a": {"input": 1, "output": 2}}, None, {"a": {"output": 3}, "b": {"cache_read": 4}})
    assert merged == {"a": {"input": 1, "output": 5, "cache_read": 0, "cache_write": 0},
                      "b": {"input": 0, "output": 0, "cache_read": 4, "cache_write": 0}}
    assert lp.claude_usage_tokens(merged) == 10
    assert lp.claude_usage_of(RuntimeError()) == {} and lp.claude_usage_of({"text": "{}"}) == {}


# ── ClaudeOnly for the marketing per-post art (owner decision 2026-11-15, "Google draws, Claude checks") ──
# The art brief and check: Claude with NO fallback, images passed through, max_tokens and a client
# deadline set by the caller, and the opt-in breaker policy that opens the shared breaker only on a
# failure of the account itself. Not a role.

_JPEG = b"\xff\xd8\xff\xe0" + b"\x00\x10JFIF" + b"\x01" * 64
_TODAYS_KEYS = {"json_schema", "model_name", "system_instruction", "thinking", "effort", "usage_tag"}
_ART_KW = dict(system_instruction="SYS", response_schema={"type": "OBJECT", "properties": {
    "subject": {"type": "STRING"}}}, usage_tag="marketing_art_check")


@pytest.mark.parametrize("images", [(), [], None])
@pytest.mark.asyncio
async def test_claude_only_without_the_new_keywords_sends_exactly_todays_call(images):
    plain, empty = FakeClaude(), FakeClaude()
    await lp.ClaudeOnly("claude-opus-5-5", claude=plain).generate_json("P", **GEMINI_KW)
    await lp.ClaudeOnly("claude-opus-5-5", claude=empty).generate_json("P", images=images, **GEMINI_KW)
    assert set(plain.calls[0][1]) == _TODAYS_KEYS, "no images / max_tokens / deadline key is sent"
    assert empty.calls == plain.calls


@pytest.mark.asyncio
async def test_claude_only_passes_images_max_tokens_deadline_and_effort_through(monkeypatch):
    monkeypatch.setattr(settings, "CAYDEX_CLAUDE_EFFORT", "max")
    claude, gemini = FakeClaude(), FakeGemini()
    only = lp.ClaudeOnly("claude-opus-5-5", claude=claude, breaker=lp.BREAKER_ACCOUNT)
    result = await only.generate_json("BRIEF", images=[(_JPEG, "image/jpeg")],
                                      max_tokens=settings.CAYDEX_CLAUDE_MAX_TOKENS, deadline_seconds=40.0,
                                      effort="low", **_ART_KW)
    prompt, kw = claude.calls[0]
    assert prompt == "BRIEF"
    assert kw["images"] == [(_JPEG, "image/jpeg")]
    assert kw["max_tokens"] == settings.CAYDEX_CLAUDE_MAX_TOKENS
    assert kw["deadline_seconds"] == 40.0
    assert kw["effort"] == "low", "the caller's effort, not CAYDEX_CLAUDE_EFFORT"
    assert kw["thinking"] is None, "Opus 5.5 thinks adaptively: no thinking field"
    assert kw["usage_tag"] == "marketing_art_check" and kw["system_instruction"] == "SYS"
    assert result["provider"] == "claude" and gemini.calls == [], "no Gemini anywhere on this path"


@pytest.mark.asyncio
async def test_an_unknown_effort_argument_is_sent_as_low_and_logged_once(caplog):
    claude = FakeClaude()
    with caplog.at_level("ERROR", logger="app.services.llm_provider"):
        for _ in range(2):
            await lp.ClaudeOnly("claude-opus-5-5", claude=claude).generate_json("P", effort="turbo", **GEMINI_KW)
    assert [kw["effort"] for _p, kw in claude.calls] == ["low", "low"]
    assert sum("effort=" in r.getMessage() for r in caplog.records) == 1


def test_an_unknown_breaker_policy_is_refused_at_construction():
    with pytest.raises(ValueError):
        lp.ClaudeOnly("claude-opus-5-5", breaker="always")
    assert lp.BREAKER_POLICIES == (None, lp.BREAKER_ACCOUNT)


_BREAKER_TABLE = [
    # (error, the router opens the breaker (the roles' set), the art policy opens it)
    (ac.ClaudeRateLimitException("429"), True, False),
    (ac.ClaudeCreditsExhaustedException("credit balance is too low"), True, True),
    (ac.ClaudeAuthException("401"), True, True),
    (ac.ClaudeConfigException("no key"), True, False),
    (ac.ClaudeUnavailableException("529"), False, False),
    (ac.ClaudeTimeoutException("slow"), False, False),
    (ac.ClaudeRefusalException("declined", tokens_used=12), False, False),
    (ac.ClaudeTruncatedException("cut", tokens_used=12), False, False),
    (ac.ClaudeBadAnswerException("empty", tokens_used=12), False, False),
    (ac.ClaudeRequestInvalidException("image/gif"), False, False),
    (ac.ClaudeException("bad schema"), False, False),
]


@pytest.mark.parametrize("error,router_opens,art_opens", _BREAKER_TABLE)
@pytest.mark.asyncio
async def test_the_art_policy_opens_the_shared_breaker_only_on_credits_or_auth(monkeypatch, error,
                                                                               router_opens, art_opens):
    # the art path (ClaudeOnly, BREAKER_ACCOUNT): no fallback, and only an ACCOUNT failure opens it
    claude = FakeClaude(error)
    with pytest.raises(type(error)):
        await lp.ClaudeOnly("claude-opus-5-5", claude=claude, breaker=lp.BREAKER_ACCOUNT).generate_json(
            "P", **_ART_KW)
    assert len(claude.calls) == 1
    assert lp.breaker_open() is art_opens
    # a measurement script (the default policy) never opens it, whatever the failure
    lp.reset_breaker()
    with pytest.raises(type(error)):
        await lp.ClaudeOnly("claude-opus-5-5", claude=FakeClaude(error)).generate_json("P", **GEMINI_KW)
    assert lp.breaker_open() is False
    # the four roles keep the wider set (ac.BREAKER_EXCEPTIONS), falling back to Gemini
    lp.reset_breaker()
    monkeypatch.setattr(settings, "MARKETING_WRITER_PROVIDER", "claude")
    router, gemini, _c = _router(FakeClaude(error))
    result = await router.generate_json("P", **GEMINI_KW)
    assert result["provider"] == "gemini" and len(gemini.calls) == 1
    assert lp.breaker_open() is router_opens


@pytest.mark.asyncio
async def test_an_open_breaker_means_no_art_call_but_a_measurement_still_calls():
    lp._open_breaker("ClaudeCreditsExhaustedException (tag=marketing_judge)")
    art = FakeClaude()
    with pytest.raises(ac.ClaudeBreakerOpenException) as info:
        await lp.ClaudeOnly("claude-opus-5-5", claude=art, breaker=lp.BREAKER_ACCOUNT).generate_json(
            "P", images=[(_JPEG, "image/jpeg")], **_ART_KW)
    assert art.calls == [], "no Claude call while the breaker is open"
    assert isinstance(info.value, ac.ClaudeUnavailableException)
    assert info.value.tokens_used == 0 and info.value.billing_unknown is False
    assert "ClaudeCreditsExhaustedException" in str(info.value), "the log names why it is open"
    measured = FakeClaude()
    await lp.ClaudeOnly("claude-opus-5-5", claude=measured).generate_json("P", **GEMINI_KW)
    assert len(measured.calls) == 1, "the calibration scripts never read the breaker (unchanged)"


@pytest.mark.asyncio
async def test_the_art_policy_keeps_the_billed_usage_on_success_and_on_failure():
    ok = await lp.ClaudeOnly("claude-opus-5-5", claude=BillingClaude(), breaker=lp.BREAKER_ACCOUNT).generate_json(
        "P", images=[(_JPEG, "image/jpeg")], **_ART_KW)
    assert ok[lp.CLAUDE_USAGE_KEY] == {"claude-opus-5-5": _U} and ok["tokens_used"] == 1_200
    with pytest.raises(ac.ClaudeTruncatedException) as info:
        await lp.ClaudeOnly("claude-opus-5-5", claude=FakeClaude(_truncated("claude-opus-5-5")),
                            breaker=lp.BREAKER_ACCOUNT).generate_json("P", **_ART_KW)
    assert info.value.tokens_used == 12
    assert lp.claude_usage_of(info.value) == {"claude-opus-5-5": {"input": 10, "output": 2, "cache_read": 0,
                                                                   "cache_write": 0}}
    assert lp.breaker_open() is False, "a cut answer is this call's problem, not the account's"


@pytest.mark.asyncio
async def test_billing_unknown_survives_a_retry_onto_the_final_exception():
    async def no_sleep(_seconds):
        return None

    class Flaky(FakeClaude):
        def __init__(self, errors):
            super().__init__()
            self.errors = list(errors)

        async def generate_json(self, prompt, **kwargs):
            self.calls.append((prompt, dict(kwargs)))
            raise self.errors.pop(0)

    flaky = Flaky([ac.ClaudeTimeoutException("slow"), ac.ClaudeRefusalException("no", tokens_used=5)])
    with pytest.raises(ac.ClaudeRefusalException) as info:
        await lp.ClaudeOnly("claude-opus-5-5", claude=flaky, retries=1, sleep=no_sleep).generate_json(
            "P", **GEMINI_KW)
    assert info.value.billing_unknown is True, "the timed-out first attempt may have billed"
    clean = Flaky([ac.ClaudeRefusalException("no", tokens_used=5)])
    with pytest.raises(ac.ClaudeRefusalException) as info:
        await lp.ClaudeOnly("claude-opus-5-5", claude=clean).generate_json("P", **GEMINI_KW)
    assert info.value.billing_unknown is False


@pytest.mark.asyncio
async def test_a_cancelled_art_call_propagates_and_opens_nothing():
    import asyncio

    with pytest.raises(asyncio.CancelledError):
        await lp.ClaudeOnly("claude-opus-5-5", claude=FakeClaude(asyncio.CancelledError()),
                            breaker=lp.BREAKER_ACCOUNT).generate_json("P", **_ART_KW)
    assert lp.breaker_open() is False


@pytest.mark.asyncio
async def test_the_art_call_on_the_wire_images_first_and_a_caller_deadline(monkeypatch):
    """ClaudeOnly → the REAL ClaudeClient over a mock transport: the picture is a base64 block before
    the prompt, max_tokens and effort are the art's, and a short deadline ends in the typed timeout."""
    import asyncio
    import base64
    import json

    import anthropic
    import httpx2

    seen: List[Any] = []

    def handler(request):
        seen.append(request)
        return httpx2.Response(200, json={
            "id": "msg_t", "type": "message", "role": "assistant", "model": "claude-opus-5-5",
            "content": [{"type": "text", "text": '{"subject": "a vault door"}'}], "stop_reason": "end_turn",
            "stop_sequence": None, "usage": {"input_tokens": 1800, "output_tokens": 900,
                                             "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}})

    monkeypatch.setattr(ac, "_client", anthropic.AsyncAnthropic(
        api_key="sk-ant-test", max_retries=0,
        http_client=anthropic.DefaultAsyncHttpxClient(transport=httpx2.MockTransport(handler))))
    monkeypatch.setattr(settings, "CAYDEX_CLAUDE_EFFORT", "high")
    only = lp.ClaudeOnly("claude-opus-5-5", claude=ac.ClaudeClient(), breaker=lp.BREAKER_ACCOUNT)
    result = await only.generate_json("CHECK THIS", images=[(_JPEG, "image/jpeg")],
                                      max_tokens=settings.CAYDEX_CLAUDE_MAX_TOKENS, deadline_seconds=30.0,
                                      effort="low", **_ART_KW)
    body = json.loads(seen[0].content)
    content = body["messages"][0]["content"]
    assert [b["type"] for b in content] == ["image", "text"] and content[1]["text"] == "CHECK THIS"
    assert base64.standard_b64decode(content[0]["source"]["data"]) == _JPEG
    assert body["max_tokens"] == settings.CAYDEX_CLAUDE_MAX_TOKENS
    assert body["output_config"]["effort"] == "low" and "thinking" not in body
    assert result[lp.CLAUDE_USAGE_KEY] == {"claude-opus-5-5": {"input": 1800, "output": 900, "cache_read": 0,
                                                                "cache_write": 0}}

    async def slow(**_kw):
        await asyncio.sleep(5)

    monkeypatch.setattr(ac._client.messages, "create", slow)
    with pytest.raises(ac.ClaudeTimeoutException) as info:
        await only.generate_json("CHECK THIS", images=[(_JPEG, "image/jpeg")], deadline_seconds=0.05, **_ART_KW)
    assert info.value.billing_unknown is True
    assert lp.breaker_open() is False, "a timeout means no art this run, never an open breaker"
    with pytest.raises(ac.ClaudeRequestInvalidException):
        await only.generate_json("CHECK THIS", images=[(_JPEG, "image/webp")], **_ART_KW)
    assert len(seen) == 1, "a refused picture is never sent"


def test_the_art_is_not_a_role_and_leaves_the_lease_alone(monkeypatch):
    """With the art switched on and its model set, ROLES, MARKETING_SETTINGS, the router's routing and
    the marketing lease are exactly today's: the art calls ClaudeOnly itself."""
    from app.services.marketing import script_service as ss

    monkeypatch.setattr(settings, "MARKETING_ART_ENABLED", True)
    monkeypatch.setattr(settings, "MARKETING_ART_CLAUDE_MODEL", "claude-opus-5-5")
    assert set(lp.ROLES) == {"marketing_writer", "marketing_judge", "learn_weekly_writer",
                             "learn_weekly_judge", "learn_weekly_support"}
    assert lp.MARKETING_SETTINGS == ("MARKETING_WRITER_PROVIDER", "MARKETING_JUDGE_PROVIDER")
    assert lp.any_marketing_role_on_claude() is False
    assert ss.claude_first_seconds() == 0.0
    for tag in ("marketing_art_brief", "marketing_art_check"):
        assert lp.route_for(tag) is None, "an art tag never routes through the router"
    monkeypatch.setattr(settings, "MARKETING_JUDGE_PROVIDER", "claude")
    assert lp.any_marketing_role_on_claude() is True, "the roles' own switch still decides"
