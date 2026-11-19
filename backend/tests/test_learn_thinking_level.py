"""LEARN_WRITER_THINKING_LEVEL and LEARN_TOPIC_THINKING_LEVEL reach the Learn weekly Gemini calls
(2026-12-01 reroute prep, 2026-11-16) — and, unset, change NOTHING.

Google routes every gemini-2.5-flash request to gemini-3.5-flash-lite from 2026-12-01; on it the
writer without thinking failed 3 of 3 pieces (too short). The two settings let the owner pick a
level by a Railway variable. What is pinned:

  * DEFAULT = TODAY: with both settings unset, every writer, judge, support, peg and label call
    carries exactly the keywords it carried before — no `thinking_level` key at all (so a fake or a
    client with an explicit signature still works, and the response-cache key is unchanged);
  * SET: the writer's outline and section calls (and their repairs) carry the level, the judge's and
    the support check's do NOT (they are not the writer); the topic step's ONE model call serves the
    peg AND the label, so both carry LEARN_TOPIC_THINKING_LEVEL; the thinking BUDGET is still sent
    (the encoder picks one of the two per model);
  * through llm_provider: a Gemini role forwards the level; a Claude-first role never forwards it to
    Claude (bound to the real ClaudeClient signature) and its Gemini fallback still gets it;
    ClaudeOnly (measurement) ignores it too;
  * outliers: blank → no key; a non-text value → no key, logged; " LOW " → "low"; the topic level is
    read at CALL time, the writer level once per generation.

Hermetic: the writer and topic suites' own fakes (imported), no network.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
from typing import Any, Dict, List

import pytest

import test_learn_weekly_topic as TT
import test_learn_weekly_writer as WT
from app.config import Settings, settings
from app.integrations import anthropic_client as ac
from app.integrations import gemini as gem
from app.services import llm_provider as lp
from app.services.learn_weekly import judge as J
from app.services.learn_weekly import sec_facts, topic
from app.services.learn_weekly import writer as W

#: The keywords each call sent BEFORE the thinking-level settings existed (2026-11-16).
WRITER_KEYS = {"system_instruction", "model_name", "response_schema", "thinking_budget", "usage_tag",
               "cache"}
TOPIC_KEYS = {"system_instruction", "model_name", "thinking_budget", "usage_tag", "temperature", "cache"}


@pytest.fixture(autouse=True)
def _settings(monkeypatch):
    # The writer suite's settings (its own autouse fixture does not apply in this module).
    monkeypatch.setattr(settings, "LEARN_JUDGE_MODE", "shadow")
    monkeypatch.setattr(settings, "LEARN_WRITER_MODEL", "gemini-2.5-flash")
    monkeypatch.setattr(settings, "LEARN_WRITER_THINKING_BUDGET", 0)
    monkeypatch.setattr(settings, "LEARN_WRITER_PROVIDER", "gemini")
    monkeypatch.setattr(settings, "LEARN_JUDGE_PROVIDER", "gemini")
    monkeypatch.setattr(settings, "LEARN_WRITER_THINKING_LEVEL", None)
    monkeypatch.setattr(settings, "LEARN_TOPIC_THINKING_LEVEL", None)
    # The topic suite's (SEC pacing off, caches clean).
    monkeypatch.setattr(settings, "SEC_EDGAR_USER_AGENT", TT.UA)
    monkeypatch.setattr(settings, "SEC_EDGAR_MAX_RPS", 1000.0)
    monkeypatch.setattr(TT.sec_edgar, "_circuit_open_until", 0.0)
    monkeypatch.setattr(TT.sec_edgar, "_next_request_at", 0.0)
    topic.clear_caches()
    sec_facts.clear_caches()
    lp.reset_breaker()
    yield
    topic.clear_caches()
    sec_facts.clear_caches()
    lp.reset_breaker()


def test_both_settings_default_to_unset():
    """The defaults themselves (the fixture pins None; this pins that None IS the shipped default)."""
    assert Settings.model_fields["LEARN_WRITER_THINKING_LEVEL"].default is None
    assert Settings.model_fields["LEARN_TOPIC_THINKING_LEVEL"].default is None


# ═════════════════════════════════════════════════════════════════════════════════════════════
# The writer
# ═════════════════════════════════════════════════════════════════════════════════════════════


def _writer_calls(client: WT.FakeClient) -> List[Dict[str, Any]]:
    return [c for c in client.calls if not WT.is_grader(c["stage"])]


def _grader_calls(client: WT.FakeClient) -> List[Dict[str, Any]]:
    return [c for c in client.calls if WT.is_grader(c["stage"])]


def test_unset_the_writer_call_is_todays_byte_for_byte():
    client = WT.FakeClient()
    out = WT.run(client)
    assert out.status == W.STATUS_ACCEPTED, (out.error, out.issues)
    writer = _writer_calls(client)
    assert writer and _grader_calls(client)
    for call in writer:
        assert set(call["kw"]) == WRITER_KEYS, call["stage"]
        assert call["kw"]["thinking_budget"] == 0 and call["kw"]["model_name"] == "gemini-2.5-flash"
    assert all("thinking_level" not in c["kw"] for c in client.calls)


def test_unset_a_client_with_an_explicit_pre_level_signature_still_works():
    """A client whose generate_json has no `thinking_level` parameter (every fake and wrapper written
    before 2026-11-16) must still be callable — a forwarded key would be a TypeError → writer_failed."""

    class Strict(WT.FakeClient):
        async def generate_json(self, prompt, system_instruction=None, model_name=None,
                                response_schema=None, thinking_budget=None, usage_tag=None,
                                temperature=None, cache=True, service_tier=None):
            kw = dict(system_instruction=system_instruction, model_name=model_name,
                      response_schema=response_schema, thinking_budget=thinking_budget,
                      usage_tag=usage_tag, cache=cache)
            return await WT.FakeClient.generate_json(self, prompt, **kw)

    out = WT.run(Strict())
    assert out.status == W.STATUS_ACCEPTED, (out.error, out.issues)


@pytest.mark.parametrize("level", ["minimal", "low", "medium", "high"])
def test_set_every_writer_call_carries_the_level_and_the_graders_do_not(monkeypatch, level):
    monkeypatch.setattr(settings, "LEARN_WRITER_THINKING_LEVEL", level)
    client = WT.FakeClient()
    out = WT.run(client)
    assert out.status == W.STATUS_ACCEPTED, (out.error, out.issues)
    writer = _writer_calls(client)
    assert [c["stage"] for c in writer] == ["outline"] + [f"section {i}" for i in range(1, 6)]
    for call in writer:
        assert call["kw"]["thinking_level"] == level, call["stage"]
        assert set(call["kw"]) == WRITER_KEYS | {"thinking_level"}
        # The budget still rides along: the encoder sends ONE of the two per model.
        assert call["kw"]["thinking_budget"] == 0
        assert call["kw"]["usage_tag"] == W.USAGE_TAG
    graders = _grader_calls(client)
    assert graders and all("thinking_level" not in c["kw"] for c in graders)


def test_set_the_repair_call_carries_the_level_too(monkeypatch):
    monkeypatch.setattr(settings, "LEARN_WRITER_THINKING_LEVEL", "low")
    client = WT.FakeClient({"section 2": WT.ok({"blocks": [{"type": "paragraph", "text": WT.LONG_PARA}]})})
    client.overrides["section 2 repair"] = lambda p, kw: client.section_answer(p, 1)
    out = WT.run(client)
    assert out.status == W.STATUS_ACCEPTED, out.issues
    repair = [c for c in client.calls if c["stage"] == "section 2 repair"]
    assert repair and repair[0]["kw"]["thinking_level"] == "low"


@pytest.mark.parametrize("raw,sent", [
    ("  ", None),          # blank = unset (the Settings validator maps it to None too)
    ("", None),
    (" LOW ", "low"),      # normalised (the Settings validator does the same at start-up)
    ("High", "high"),
])
def test_writer_level_text_outliers(monkeypatch, raw, sent):
    monkeypatch.setattr(settings, "LEARN_WRITER_THINKING_LEVEL", raw)
    client = WT.FakeClient()
    out = WT.run(client)
    assert out.status == W.STATUS_ACCEPTED, (out.error, out.issues)
    for call in _writer_calls(client):
        assert call["kw"].get("thinking_level") == sent
        assert ("thinking_level" in call["kw"]) is (sent is not None)


@pytest.mark.parametrize("raw", [1, True, ["low"]])
def test_a_non_text_writer_level_sends_no_key_and_is_logged(monkeypatch, caplog, raw):
    monkeypatch.setattr(settings, "LEARN_WRITER_THINKING_LEVEL", raw)
    client = WT.FakeClient()
    with caplog.at_level(logging.WARNING, logger="app.services.learn_weekly.writer"):
        out = WT.run(client)
    assert out.status == W.STATUS_ACCEPTED, (out.error, out.issues)
    assert all("thinking_level" not in c["kw"] for c in client.calls)
    assert any("LEARN_WRITER_THINKING_LEVEL" in r.getMessage() for r in caplog.records)


def test_the_writer_level_is_read_once_per_generation(monkeypatch):
    """One generation = one configuration (like its model and budget): a level flipped while it runs
    reaches the NEXT generation, never half of this one's sections."""
    monkeypatch.setattr(settings, "LEARN_WRITER_THINKING_LEVEL", "low")
    client = WT.FakeClient()
    client.overrides["section 1"] = lambda p, kw: (
        monkeypatch.setattr(settings, "LEARN_WRITER_THINKING_LEVEL", "high"), client.section_answer(p, 0))[1]
    out = WT.run(client)
    assert out.status == W.STATUS_ACCEPTED, (out.error, out.issues)
    assert {c["kw"]["thinking_level"] for c in _writer_calls(client)} == {"low"}
    nxt = WT.FakeClient()
    assert WT.run(nxt).status == W.STATUS_ACCEPTED
    assert {c["kw"]["thinking_level"] for c in _writer_calls(nxt)} == {"high"}


# ── through llm_provider (the production client) ─────────────────────────────────────────────


class _StrictClaude:
    """Bound to the REAL ClaudeClient signature: a forwarded `thinking_level` would be a TypeError
    here, exactly as in production. `fail` makes every call raise a typed Claude failure."""

    def __init__(self, fail: bool = False):
        self.inner, self.calls, self.fail = WT.FakeClient(), [], fail

    async def generate_json(self, prompt, **kw):
        inspect.signature(ac.ClaudeClient.generate_json).bind(None, prompt, **kw)
        self.calls.append(dict(kw))
        if self.fail:
            raise ac.ClaudeUnavailableException("overloaded (test)")
        result = await self.inner.generate_json(prompt, **kw)
        return {**result, "model": kw["model_name"]}


def _route(monkeypatch, gemini, claude, *, writer_provider: str):
    monkeypatch.setattr(gem, "get_gemini_client", lambda: gemini)
    monkeypatch.setattr(ac, "get_claude_client", lambda: claude)
    monkeypatch.setattr(settings, "CAYDEX_ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setattr(settings, "LEARN_WRITER_PROVIDER", writer_provider)
    monkeypatch.setattr(settings, "LEARN_JUDGE_PROVIDER", "gemini")


def test_router_on_gemini_forwards_the_level(monkeypatch):
    monkeypatch.setattr(settings, "LEARN_WRITER_THINKING_LEVEL", "medium")
    gemini, claude = WT.FakeClient(), _StrictClaude()
    _route(monkeypatch, gemini, claude, writer_provider="gemini")
    out = WT.run(None)                                       # no client → the real ProviderRouter
    assert out.status == W.STATUS_ACCEPTED, (out.error, out.issues)
    assert claude.calls == []
    assert {c["kw"].get("thinking_level") for c in _writer_calls(gemini)} == {"medium"}


def test_router_claude_first_never_forwards_the_level_to_claude(monkeypatch):
    monkeypatch.setattr(settings, "LEARN_WRITER_THINKING_LEVEL", "low")
    gemini, claude = WT.FakeClient(), _StrictClaude()
    _route(monkeypatch, gemini, claude, writer_provider="claude")
    out = WT.run(None)
    assert out.status == W.STATUS_ACCEPTED, (out.error, out.issues)
    # Claude answered every writer call (a TypeError would have fallen back to Gemini instead).
    assert claude.calls and all("thinking_level" not in kw for kw in claude.calls)
    assert _writer_calls(gemini) == []


def test_router_claude_failure_falls_back_to_gemini_with_the_level(monkeypatch):
    monkeypatch.setattr(settings, "LEARN_WRITER_THINKING_LEVEL", "low")
    gemini, claude = WT.FakeClient(), _StrictClaude(fail=True)
    _route(monkeypatch, gemini, claude, writer_provider="claude")
    out = WT.run(None)
    assert out.status == W.STATUS_ACCEPTED, (out.error, out.issues)
    assert claude.calls and all("thinking_level" not in kw for kw in claude.calls)
    writer = _writer_calls(gemini)
    assert writer and {c["kw"].get("thinking_level") for c in writer} == {"low"}


def test_claude_only_measurement_ignores_the_level():
    claude = _StrictClaude()
    result = asyncio.run(lp.ClaudeOnly("claude-sonnet-5-5", claude).generate_json(
        "REQUEST g round 1 (judge)", system_instruction="s", thinking_budget=0,
        usage_tag=J.USAGE_TAG, thinking_level="low", cache=False, temperature=0.0))
    assert result["provider"] == lp.PROVIDER_CLAUDE
    assert claude.calls and "thinking_level" not in claude.calls[0]


# ═════════════════════════════════════════════════════════════════════════════════════════════
# The topic step (peg + label share ONE call)
# ═════════════════════════════════════════════════════════════════════════════════════════════


class _RecordingLLM(TT.FakeLLM):
    """The topic suite's fake, recording EVERY keyword (its own signature has no thinking_level)."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.kw_calls: List[Dict[str, Any]] = []

    async def generate_json(self, prompt, **kw):
        self.kw_calls.append(dict(kw))
        kw.pop("thinking_level", None)
        return await super().generate_json(prompt, **kw)


@pytest.mark.asyncio
async def test_unset_the_topic_calls_are_todays_byte_for_byte(monkeypatch):
    TT.World(monkeypatch)
    llm = _RecordingLLM()
    row = await TT.run(llm=llm)
    assert row is not None and row["status"] == TT.C.TOPIC_PENDING
    assert {kw["usage_tag"] for kw in llm.kw_calls} == {"learn_topic_peg", "learn_topic_label"}
    for kw in llm.kw_calls:
        assert set(kw) == TOPIC_KEYS, kw["usage_tag"]
        assert kw["thinking_budget"] == 0 and kw["temperature"] == 0.0 and kw["cache"] is False


@pytest.mark.asyncio
async def test_unset_the_topic_suites_strict_fake_still_answers(monkeypatch):
    """TT.FakeLLM has an explicit signature with no thinking_level: a forwarded key would raise inside
    `_llm_json` (counted as a failure), the label would fall back, and the peg would be dropped."""
    TT.World(monkeypatch)
    llm = TT.FakeLLM()
    row = await TT.run(llm=llm)
    assert row is not None and row["status"] == TT.C.TOPIC_PENDING
    assert row["topic"]["candidates"][0]["label"] == "AI Cloud Capacity"     # the MODEL's label


@pytest.mark.asyncio
@pytest.mark.parametrize("level", ["minimal", "low", "medium", "high"])
async def test_set_both_the_peg_and_the_label_carry_the_level(monkeypatch, level):
    monkeypatch.setattr(settings, "LEARN_TOPIC_THINKING_LEVEL", level)
    TT.World(monkeypatch)
    llm = _RecordingLLM()
    row = await TT.run(llm=llm)
    assert row is not None and row["status"] == TT.C.TOPIC_PENDING
    assert {kw["usage_tag"] for kw in llm.kw_calls} == {"learn_topic_peg", "learn_topic_label"}
    for kw in llm.kw_calls:
        assert kw["thinking_level"] == level, kw["usage_tag"]
        assert set(kw) == TOPIC_KEYS | {"thinking_level"}
        assert kw["thinking_budget"] == 0 and kw["model_name"] == settings.LEARN_WRITER_MODEL


@pytest.mark.asyncio
async def test_the_topic_level_is_independent_of_the_writer_level(monkeypatch):
    monkeypatch.setattr(settings, "LEARN_WRITER_THINKING_LEVEL", "high")
    TT.World(monkeypatch)
    llm = _RecordingLLM()
    await TT.run(llm=llm)
    assert llm.kw_calls and all("thinking_level" not in kw for kw in llm.kw_calls)


def _one_call(level: Any) -> Dict[str, Any]:
    llm = _RecordingLLM()
    run = topic._Run(period_key="test", llm=llm, sec=None)
    asyncio.run(topic._llm_json(run, prompt="REQUEST x", body="b", usage_tag="learn_topic_peg", ids="t"))
    assert run.llm_failures == 0
    return llm.kw_calls[0]


@pytest.mark.parametrize("raw,sent", [("  ", None), ("", None), (" Medium ", "medium"), ("LOW", "low")])
def test_topic_level_text_outliers(monkeypatch, raw, sent):
    monkeypatch.setattr(settings, "LEARN_TOPIC_THINKING_LEVEL", raw)
    kw = _one_call(raw)
    assert kw.get("thinking_level") == sent
    assert ("thinking_level" in kw) is (sent is not None)


@pytest.mark.parametrize("raw", [0, 2.5, False, {"level": "low"}])
def test_a_non_text_topic_level_sends_no_key_and_is_logged(monkeypatch, caplog, raw):
    monkeypatch.setattr(settings, "LEARN_TOPIC_THINKING_LEVEL", raw)
    with caplog.at_level(logging.WARNING, logger=TT.LOGGER):
        kw = _one_call(raw)
    assert "thinking_level" not in kw
    if raw is not None:
        assert any("LEARN_TOPIC_THINKING_LEVEL" in r.getMessage() for r in caplog.records)


def test_the_topic_level_is_read_at_call_time(monkeypatch):
    llm = _RecordingLLM()
    run = topic._Run(period_key="test", llm=llm, sec=None)
    for value in (None, "low", "high", None):
        monkeypatch.setattr(settings, "LEARN_TOPIC_THINKING_LEVEL", value)
        asyncio.run(topic._llm_json(run, prompt="REQUEST x", body="b", usage_tag="learn_topic_label",
                                    ids="t"))
    assert [kw.get("thinking_level") for kw in llm.kw_calls] == [None, "low", "high", None]
