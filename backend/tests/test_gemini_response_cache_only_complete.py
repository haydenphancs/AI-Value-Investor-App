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
    src = inspect.getsource(gem.GeminiClient)
    assert src.count("if _cacheable_answer(result):\n                self._response_cache.set(key, result)") == 2
    assert "            self._response_cache.set(key, result)\n            return result" not in src.replace(
        "if _cacheable_answer(result):\n                self._response_cache.set(key, result)", "")
