"""
The switchable language model behind the NEWS features — and nothing else.

Scope, on purpose:
  * per-article sentiment + summary bullets (`news_cache_service._batch_enrich_articles`);
  * the 90-day sentiment backfill (`news_sentiment_backfill_service`).
Cay AI chat, research reports, the Updates Insights card and the "why it moved" catalyst
(which needs Google Search grounding) stay on `app/integrations/gemini.py` and never come
through here.

Settings pick the provider (`NEWS_LLM_PROVIDER`):
  * ``gemini`` (default) — the existing `GeminiClient.generate_json`, called with EXACTLY the
    arguments the enrichment used before this module existed (a golden test pins that), so
    the default path is unchanged byte for byte.
  * ``openai_compat`` — any OpenAI-compatible `/chat/completions` endpoint (DeepSeek, OpenAI,
    Qwen, Groq, Together, OpenRouter) via `app/integrations/openai_compat.py`, configured by
    `NEWS_LLM_BASE_URL`, `NEWS_LLM_API_KEY`, `NEWS_LLM_MODEL`, `NEWS_LLM_JSON_MODE` and
    `NEWS_LLM_EXTRA_BODY` (e.g. DeepSeek ``{"thinking": {"type": "disabled"}}``).

Callers write their response schema ONCE, in the Gemini dialect they already use; this module
translates it to JSON Schema for the other providers, puts it in the prompt (most of them only
enforce "valid JSON", not a shape), wraps a top-level array in an object (JSON mode requires an
object) and unwraps it again, so every caller parses the same text as before. Validation stays
with the callers — which already refuse a wrong-count or malformed answer and leave the articles
unenriched (retryable), never labelled "neutral".

Switching providers is an OWNER decision with a checklist (documents/OWNER_TASKS.md): env vars,
`scripts/calibrate_sentiment_backfill.py` agreement ≥ 85%, and the privacy-policy provider list
when the new provider stores data outside the US.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from app.config import settings
from app.integrations.gemini import get_gemini_client, is_transient_gemini_error
from app.integrations.openai_compat import (
    OpenAICompatClient,
    is_transient_openai_compat_error,
    parse_extra_body,
    quota_breaker as openai_compat_breaker,
)

logger = logging.getLogger(__name__)

PROVIDER_GEMINI = "gemini"
PROVIDER_OPENAI_COMPAT = "openai_compat"
PROVIDERS = (PROVIDER_GEMINI, PROVIDER_OPENAI_COMPAT)

#: The key a top-level array is wrapped under for providers whose JSON mode needs an object.
WRAP_KEY = "items"

_warned_bad_provider = False


@dataclass(frozen=True)
class NewsLLMConfig:
    provider: str
    model: str
    base_url: Optional[str] = None
    api_key: Optional[str] = field(default=None, repr=False)
    json_mode: str = "json_object"
    extra_body: Dict[str, Any] = field(default_factory=dict)


def news_llm_config() -> NewsLLMConfig:
    """Read the settings each call (cheap), so a test or a hot env change is honoured.

    An unknown provider, or an incomplete OpenAI-compatible setup, falls back to Gemini
    with an ERROR logged once — a typo in Railway must degrade to the working default, not
    stop every article from being summarised.
    """
    global _warned_bad_provider
    provider = (getattr(settings, "NEWS_LLM_PROVIDER", None) or PROVIDER_GEMINI).strip().lower()
    model = (getattr(settings, "NEWS_LLM_MODEL", None) or "gemini-2.5-flash-lite").strip()
    if provider == PROVIDER_OPENAI_COMPAT:
        base_url = getattr(settings, "NEWS_LLM_BASE_URL", None)
        api_key = getattr(settings, "NEWS_LLM_API_KEY", None)
        if base_url and api_key and model:
            return NewsLLMConfig(
                provider=provider, model=model, base_url=base_url, api_key=api_key,
                json_mode=(getattr(settings, "NEWS_LLM_JSON_MODE", None) or "json_object"),
                extra_body=parse_extra_body(getattr(settings, "NEWS_LLM_EXTRA_BODY", None)),
            )
        reason = "NEWS_LLM_BASE_URL / NEWS_LLM_API_KEY missing"
    elif provider == PROVIDER_GEMINI:
        return NewsLLMConfig(provider=PROVIDER_GEMINI, model=model)
    else:
        reason = f"unknown NEWS_LLM_PROVIDER {provider!r}"
    if not _warned_bad_provider:
        logger.error("news_llm: %s — falling back to Gemini gemini-2.5-flash-lite", reason)
        _warned_bad_provider = True
    return NewsLLMConfig(provider=PROVIDER_GEMINI, model="gemini-2.5-flash-lite")


def news_model_name() -> str:
    """The model the news features are labelling with right now (recorded on each label)."""
    return news_llm_config().model


def is_transient_news_llm_error(exc: BaseException) -> bool:
    """Capacity / quota / 5xx on EITHER provider — expected degradation, logged as WARNING."""
    return is_transient_gemini_error(exc) or is_transient_openai_compat_error(exc)


def quota_tripped() -> bool:
    """Whether the ACTIVE provider's quota breaker is open (the backfill checks per batch)."""
    cfg = news_llm_config()
    if cfg.provider == PROVIDER_OPENAI_COMPAT:
        return openai_compat_breaker.tripped
    try:
        from app.integrations.gemini import _quota_circuit

        return bool(_quota_circuit.tripped)
    except Exception:  # noqa: BLE001 — an unreadable breaker must not stop the caller
        return False


# ── Schema translation (Gemini dialect → JSON Schema) ─────────────────────────


_TYPE = {
    "OBJECT": "object", "ARRAY": "array", "STRING": "string", "INTEGER": "integer",
    "NUMBER": "number", "BOOLEAN": "boolean",
}


def gemini_schema_to_json_schema(schema: Any) -> Any:
    """Translate a Gemini `response_schema` dict to standard JSON Schema. Pure.

    Uppercase types → lowercase; `properties` / `items` recurse; `enum`, `required`,
    `minItems` / `maxItems`, `description` carry over; `propertyOrdering` becomes the
    order of `properties` (dicts keep insertion order) and is dropped; `nullable` becomes
    a `[type, "null"]` union; objects get `additionalProperties: false`.
    """
    if not isinstance(schema, dict):
        return schema
    out: Dict[str, Any] = {}
    raw_type = schema.get("type")
    json_type = _TYPE.get(str(raw_type).upper(), raw_type) if raw_type is not None else None
    if json_type is not None:
        out["type"] = [json_type, "null"] if schema.get("nullable") else json_type
    if "description" in schema:
        out["description"] = schema["description"]
    if "enum" in schema:
        out["enum"] = list(schema["enum"])
    if "properties" in schema and isinstance(schema["properties"], dict):
        props = schema["properties"]
        order = [k for k in schema.get("propertyOrdering") or [] if k in props]
        order += [k for k in props if k not in order]
        out["properties"] = {k: gemini_schema_to_json_schema(props[k]) for k in order}
        out["additionalProperties"] = False
    if "items" in schema:
        out["items"] = gemini_schema_to_json_schema(schema["items"])
    for key in ("required", "minItems", "maxItems", "minimum", "maximum"):
        if key in schema:
            out[key] = list(schema[key]) if key == "required" else schema[key]
    return out


def _is_array_schema(schema: Any) -> bool:
    return isinstance(schema, dict) and str(schema.get("type", "")).upper() == "ARRAY"


def _wrap(json_schema: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "type": "object",
        "properties": {WRAP_KEY: json_schema},
        "required": [WRAP_KEY],
        "additionalProperties": False,
    }


def _unwrap_array_text(text: str) -> str:
    """`{"items": [...]}` → `[...]` as JSON text; anything else is returned untouched, so a
    malformed answer still reaches the caller's own validation (and fails it there)."""
    try:
        parsed = json.loads(text)
    except (TypeError, ValueError):
        return text
    if isinstance(parsed, dict) and isinstance(parsed.get(WRAP_KEY), list):
        return json.dumps(parsed[WRAP_KEY])
    return text


def _schema_prompt(prompt: str, json_schema: Dict[str, Any], wrapped: bool) -> str:
    shape = (
        f'Return a JSON object with one key, "{WRAP_KEY}", holding the array described above.'
        if wrapped else "Return a single JSON object."
    )
    return (
        f"{prompt}\n\nOUTPUT FORMAT: {shape} Respond with JSON only — no prose, no code fences. "
        f"It must validate against this JSON Schema:\n{json.dumps(json_schema, separators=(',', ':'))}"
    )


# ── The one entry point ───────────────────────────────────────────────────────


async def generate_news_json(
    *,
    prompt: str,
    system_instruction: Optional[str],
    response_schema: Dict[str, Any],
    usage_tag: Optional[str] = None,
    temperature: Optional[float] = None,
    cache: bool = True,
    service_tier: Optional[str] = None,
    gemini_client: Any = None,
) -> Dict[str, Any]:
    """One JSON answer from the configured news model, as `{"text", "model", ...}`.

    On Gemini, only arguments that differ from the defaults are passed, so the enrichment
    call is the same call it always was. `service_tier` is Gemini-only (Flex); other
    providers ignore it. `gemini_client` lets a service hand in the client it was built
    with (tests inject a fake there); None means the process-wide one.
    """
    cfg = news_llm_config()
    if cfg.provider == PROVIDER_GEMINI:
        kwargs: Dict[str, Any] = {
            "prompt": prompt,
            "system_instruction": system_instruction,
            "model_name": cfg.model,
            "response_schema": response_schema,
        }
        if usage_tag is not None:
            kwargs["usage_tag"] = usage_tag
        if temperature is not None:
            kwargs["temperature"] = temperature
        if not cache:
            kwargs["cache"] = False
        if service_tier:
            kwargs["service_tier"] = service_tier
        client = gemini_client if gemini_client is not None else get_gemini_client()
        return await client.generate_json(**kwargs)

    json_schema = gemini_schema_to_json_schema(response_schema)
    wrapped = _is_array_schema(response_schema)
    if wrapped:
        json_schema = _wrap(json_schema)
    client = OpenAICompatClient(
        base_url=cfg.base_url or "", api_key=cfg.api_key or "",
        extra_body=cfg.extra_body, json_mode=cfg.json_mode,
    )
    result = await client.generate_json(
        prompt=_schema_prompt(prompt, json_schema, wrapped),
        model=cfg.model,
        system_instruction=system_instruction,
        json_schema=json_schema,
        temperature=temperature,
        usage_tag=usage_tag,
    )
    if wrapped:
        result = {**result, "text": _unwrap_array_text(result.get("text") or "")}
    return result


__all__ = [
    "NewsLLMConfig",
    "PROVIDERS",
    "PROVIDER_GEMINI",
    "PROVIDER_OPENAI_COMPAT",
    "WRAP_KEY",
    "gemini_schema_to_json_schema",
    "generate_news_json",
    "is_transient_news_llm_error",
    "news_llm_config",
    "news_model_name",
    "quota_tripped",
]
