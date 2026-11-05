"""The response cache keeps only COMPLETE answers.

`generate_text` / `generate_json` stored the result unconditionally for `GEMINI_CACHE_TTL`
(1 h) — including an empty text (safety block, MAX_TOKENS spent while thinking) or a
non-STOP finish. The cache key is fully deterministic for a chat prompt and a failed
non-stream turn is not persisted, so the retry rebuilt the identical prompt and was served
the identical failure for an hour.
"""
import logging
from types import SimpleNamespace

import pytest

from app.integrations import gemini as gem


@pytest.mark.parametrize("result, expected", [
    ({"text": "A real answer.", "finish_reason": "STOP"}, True),
    ({"text": "A real answer.", "finish_reason": None}, True),
    ({"text": "A real answer.", "finish_reason": "FINISH_REASON_STOP"}, True),
    ({"text": "", "finish_reason": "STOP"}, False),
    ({"text": "   \n", "finish_reason": "STOP"}, False),
    ({"text": None, "finish_reason": "STOP"}, False),
    ({"text": "partial", "finish_reason": "MAX_TOKENS"}, False),
    ({"text": "partial", "finish_reason": "SAFETY"}, False),
    ({"text": "partial", "finish_reason": "RECITATION"}, False),
    ({}, False),
])
def test_cacheable_answer_predicate(result, expected):
    assert gem._cacheable_answer(result) is expected


_INCIDENT_TEXT = '{"macro": [{"description": "the "Other Bets" unit", "impact": 0.2}]}'


@pytest.mark.parametrize("text, finish, expected", [
    ('{"quality_score": 71}', "STOP", True),
    ('{"quality_score": 71}', None, True),
    ('[{"a": 1}]', "STOP", True),
    (_INCIDENT_TEXT, "STOP", False),                 # the incident: a clean STOP, broken JSON
    ('{"quality_score": 71', "STOP", False),         # ended right after a complete value
    ('{"a": "cut in the mid', "STOP", False),
    ("null", "STOP", False),                         # valid JSON, never a usable answer
    ("5", "STOP", False),
    ('"text"', "STOP", False),
    ("true", "STOP", False),
    ('```json\n{"a": 1}\n```', "STOP", False),       # safe direction: one extra call
    ("[" * 200_000, "STOP", False),                  # RecursionError, never a raise
    ('{"a": 1}', "MAX_TOKENS", False),               # the base gate still rules
    ('{"a": 1}', "RECITATION", False),
    ("", "STOP", False),
    (None, "STOP", False),
])
def test_only_real_json_is_cacheable_for_generate_json(text, finish, expected):
    assert gem._cacheable_json_answer({"text": text, "finish_reason": finish}) is expected


def test_a_refused_answer_says_why_in_one_line(caplog):
    with caplog.at_level(logging.WARNING, logger="app.integrations.gemini"):
        gem._cacheable_json_answer(
            {"text": _INCIDENT_TEXT, "finish_reason": "STOP"}, tag="report_stage_a:AAPL:x",
        )
    records = [r for r in caplog.records if "[gemini-json-not-cached]" in r.getMessage()]
    assert len(records) == 1
    assert "tag=report_stage_a:AAPL:x" in records[0].getMessage()
    assert "Other Bets" not in records[0].getMessage(), "the raw text stays out of this line"


class _Resp:
    def __init__(self, text, finish="STOP"):
        self.text = text
        self.usage_metadata = None
        self.candidates = [SimpleNamespace(finish_reason=finish)]


class _Models:
    """Each call hands back the next scripted answer."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = 0

    async def generate_content(self, *, model, contents, config):
        self.calls += 1
        return self.answers.pop(0)


def _client(models):
    c = gem.GeminiClient.__new__(gem.GeminiClient)
    c.model_name = "gemini-2.5-flash"
    c._temperature, c._max_tokens = 0.7, 128
    c._client = SimpleNamespace(aio=SimpleNamespace(models=models))
    c._response_cache = gem._TTLCache(max_size=8, ttl_seconds=3600)
    gem._quota_circuit.record_success()
    return c


@pytest.mark.asyncio
async def test_a_broken_json_answer_is_never_replayed_to_the_next_caller():
    """The hour-long outage this gate closes: one bad answer, then every later caller of the
    same prompt read it back with no upstream call."""
    models = _Models(_Resp(_INCIDENT_TEXT), _Resp('{"quality_score": 71}'))
    c = _client(models)
    first = await c.generate_json("same prompt")
    second = await c.generate_json("same prompt")
    assert first["text"] == _INCIDENT_TEXT
    assert second["text"] == '{"quality_score": 71}'
    assert models.calls == 2, "the second caller must reach the model"


@pytest.mark.asyncio
async def test_a_good_json_answer_is_still_served_from_the_cache(caplog):
    models = _Models(_Resp('{"quality_score": 71}'))
    c = _client(models)
    await c.generate_json("same prompt", usage_tag="tagged")
    with caplog.at_level(logging.INFO, logger="app.integrations.gemini"):
        second = await c.generate_json("same prompt", usage_tag="tagged")
    assert second["text"] == '{"quality_score": 71}' and models.calls == 1
    assert "generate_json cache HIT tag=tagged" in caplog.text, "a replay must be visible"


def test_both_text_paths_gate_their_cache_write():
    import inspect
    import re

    src = inspect.getsource(gem.GeminiClient)
    # generate_json's gate also honours `cache=False` (the sentiment backfill opts out) and
    # checks the text is really JSON, so its condition reads
    # `if cache and _cacheable_json_answer(result, tag=usage_tag):` — still gated.
    gated = re.compile(
        r"if (?:_cacheable_answer\(result\)|cache and _cacheable_json_answer\(result, tag=usage_tag\)):"
        r"\n                self\._response_cache\.set\(key, result\)"
    )
    assert len(gated.findall(src)) == 2
    assert "            self._response_cache.set(key, result)\n            return result" not in gated.sub("", src)


def test_generate_json_can_skip_the_shared_cache():
    import inspect

    src = inspect.getsource(gem.GeminiClient.generate_json)
    assert "cached = self._response_cache.get(key) if cache else None" in src
    assert "if cache and _cacheable_json_answer(result, tag=usage_tag):" in src
