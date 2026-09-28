"""The response cache keeps only COMPLETE answers.

`generate_text` / `generate_json` stored the result unconditionally for `GEMINI_CACHE_TTL`
(1 h) — including an empty text (safety block, MAX_TOKENS spent while thinking) or a
non-STOP finish. The cache key is fully deterministic for a chat prompt and a failed
non-stream turn is not persisted, so the retry rebuilt the identical prompt and was served
the identical failure for an hour.
"""
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


def test_both_text_paths_gate_their_cache_write():
    import inspect
    import re

    src = inspect.getsource(gem.GeminiClient)
    # generate_json's gate also honours `cache=False` (the sentiment backfill opts out), so
    # its condition reads `if cache and _cacheable_answer(result):` — still gated.
    gated = re.compile(
        r"if (?:cache and )?_cacheable_answer\(result\):\n                self\._response_cache\.set\(key, result\)"
    )
    assert len(gated.findall(src)) == 2
    assert "            self._response_cache.set(key, result)\n            return result" not in gated.sub("", src)


def test_generate_json_can_skip_the_shared_cache():
    import inspect

    src = inspect.getsource(gem.GeminiClient.generate_json)
    assert "cached = self._response_cache.get(key) if cache else None" in src
    assert "if cache and _cacheable_answer(result):" in src
