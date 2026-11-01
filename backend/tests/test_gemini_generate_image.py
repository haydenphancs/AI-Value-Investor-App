"""`GeminiClient.generate_image` — the weekly cover art's one image call (2026-11-01).

The retry semantics are the catalog script's `_call_model`: transient failures (transport, timeout,
429, 5xx, an EMPTY NO_IMAGE answer) are retried; a refusal (an answer without an image for any other
reason) fails at once — retrying it only burns quota; a bad request fails at once. It must never touch
the process-wide quota breaker (an image model's 429 must not fail-fast chat and reports), and its
typed errors classify as the AI engine, never as an FMP outage.

Hermetic: `client._client.aio.models.generate_content` is replaced by a scripted fake.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, List

import pytest
from google.genai import errors as genai_errors

from app.api.error_response import ErrorCode, classify_exception
from app.integrations import gemini
from app.integrations.gemini import (
    GeminiClient,
    GeminiImageError,
    GeminiImageRefusedError,
    GeminiImageUnavailableError,
    GeminiTimeoutError,
)

PNG = b"\x89PNG\r\n\x1a\nfake-image-bytes"


def answer(*parts: Any, finish: str = "STOP", ratings: Any = None, candidates: bool = True) -> Any:
    cand = SimpleNamespace(content=SimpleNamespace(parts=list(parts)), finish_reason=finish,
                           safety_ratings=ratings)
    return SimpleNamespace(candidates=[cand] if candidates else [], prompt_feedback="BLOCKED",
                           usage_metadata=None)


def image_part(data: bytes = PNG) -> Any:
    return SimpleNamespace(inline_data=SimpleNamespace(data=data), text=None)


def text_part(text: str) -> Any:
    return SimpleNamespace(inline_data=None, text=text)


class Script:
    def __init__(self, *items: Any) -> None:
        self.items: List[Any] = list(items)
        self.calls: List[dict] = []

    async def __call__(self, *, model, contents, config):
        self.calls.append({"model": model, "contents": contents, "config": config})
        item = self.items.pop(0) if len(self.items) > 1 else self.items[0]
        if isinstance(item, BaseException):
            raise item
        return item


@pytest.fixture
def client(monkeypatch):
    gemini._quota_circuit.reset()
    c = GeminiClient()
    c.patch = lambda fn: monkeypatch.setattr(c._client.aio.models, "generate_content", fn)
    yield c
    gemini._quota_circuit.reset()


def go(c: GeminiClient, script: Script, **kw) -> bytes:
    c.patch(script)
    return asyncio.run(c.generate_image("PROMPT", model_name="image-model", backoff_seconds=0, **kw))


def test_the_image_bytes_are_returned_even_after_a_text_part(client):
    s = Script(answer(text_part("Here is your picture"), image_part()))
    assert go(client, s) == PNG
    (call,) = s.calls
    assert call["model"] == "image-model" and call["contents"] == "PROMPT"
    cfg = call["config"]
    assert cfg.response_modalities == ["IMAGE"]
    assert cfg.image_config.aspect_ratio == "16:9" and cfg.image_config.image_size == "2K"


@pytest.mark.parametrize("resp", [
    answer(text_part("I can't make that image."), finish="STOP"),
    answer(finish="IMAGE_SAFETY", ratings=[{"category": "x"}]),
    answer(finish="NO_IMAGE", ratings=[{"category": "x"}]),        # ratings → a refusal, not an empty
    answer(text_part("no"), finish="NO_IMAGE"),                      # text → a refusal
    answer(candidates=False),                                        # a blocked prompt
])
def test_a_refusal_fails_at_once(client, resp):
    s = Script(resp, answer(image_part()))
    with pytest.raises(GeminiImageRefusedError):
        go(client, s)
    assert len(s.calls) == 1


def test_an_empty_answer_is_retried(client):
    s = Script(answer(finish="NO_IMAGE"), answer(image_part()))
    assert go(client, s) == PNG and len(s.calls) == 2


@pytest.mark.parametrize("exc", [
    RuntimeError("connection reset"),
    GeminiTimeoutError("generate_image exceeded its 90s per-request ceiling"),
    genai_errors.ServerError(503, {"error": {"code": 503, "message": "overloaded", "status": "UNAVAILABLE"}}),
    genai_errors.ClientError(429, {"error": {"code": 429, "message": "q", "status": "RESOURCE_EXHAUSTED"}}),
])
def test_transient_failures_are_retried_then_succeed(client, exc):
    s = Script(exc, answer(image_part()))
    assert go(client, s) == PNG and len(s.calls) == 2


def test_exhausted_transient_failures_are_unavailable(client):
    s = Script(RuntimeError("down"))
    with pytest.raises(GeminiImageUnavailableError) as e:
        go(client, s, max_attempts=3)
    assert len(s.calls) == 3 and "3 attempt" in str(e.value) and e.value.transient is True
    s = Script(answer(finish="NO_IMAGE"))
    with pytest.raises(GeminiImageUnavailableError):
        go(client, s, max_attempts=2)
    assert len(s.calls) == 2


@pytest.mark.parametrize("exc", [
    genai_errors.ClientError(400, {"error": {"code": 400, "message": "bad", "status": "INVALID_ARGUMENT"}}),
    genai_errors.ClientError(404, {"error": {"code": 404, "message": "no model", "status": "NOT_FOUND"}}),
    ValueError("bad config"),
])
def test_a_bad_request_fails_at_once_as_permanent(client, exc):
    s = Script(exc, answer(image_part()))
    with pytest.raises(GeminiImageError) as e:
        go(client, s)
    assert type(e.value) is GeminiImageError and e.value.transient is False and len(s.calls) == 1


def test_a_429_never_touches_the_shared_quota_breaker(client):
    cb = gemini._quota_circuit
    s = Script(genai_errors.ClientError(429, {"error": {"code": 429, "message": "q",
                                                        "status": "RESOURCE_EXHAUSTED"}}))
    with pytest.raises(GeminiImageUnavailableError):
        go(client, s, max_attempts=3)
    assert cb._consecutive == 0 and not cb.tripped and not cb.is_open()


def test_cancellation_propagates(client):
    async def hang(**_):
        await asyncio.sleep(30)

    client.patch(hang)

    async def main():
        task = asyncio.ensure_future(client.generate_image("P", model_name="m", backoff_seconds=0))
        await asyncio.sleep(0.01)
        task.cancel()
        await task

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(main())


@pytest.mark.parametrize("cls", [GeminiImageError, GeminiImageRefusedError, GeminiImageUnavailableError])
def test_the_image_errors_classify_as_the_ai_engine_never_fmp(cls):
    code, status = classify_exception(cls("generate_image: no image (unavailable 503)"))
    assert code == ErrorCode.GEMINI_UNAVAILABLE
