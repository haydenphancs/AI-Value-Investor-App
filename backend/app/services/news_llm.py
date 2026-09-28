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
    OpenAICompatContentRejected,
    OpenAICompatError,
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

DEFAULT_GEMINI_MODEL = "gemini-2.5-flash-lite"

_warned_bad_provider = False
_warned_bad_gemini_model = False


@dataclass(frozen=True)
class NewsLLMConfig:
    provider: str
    model: str
    base_url: Optional[str] = None
    api_key: Optional[str] = field(default=None, repr=False)
    json_mode: str = "json_object"
    extra_body: Dict[str, Any] = field(default_factory=dict)
    max_tokens: Optional[int] = None
    max_tokens_field: str = "max_tokens"
    send_temperature: bool = True


def _is_gemini_model(model: str) -> bool:
    return model.lower().startswith("gemini-")


def news_llm_config() -> NewsLLMConfig:
    """Read the settings each call (cheap), so a test or a hot env change is honoured.

    The model is scoped to the provider, so a half-done switch degrades to the working
    default instead of stopping every article from being summarised (each logged ERROR once):
      * an unknown provider, or an openai_compat setup missing its URL, key or its OWN model
        (unset, or still a ``gemini-`` name), falls back to Gemini flash-lite;
      * provider gemini with a non-Gemini model — the leftover of rolling back from a switch
        by changing only NEWS_LLM_PROVIDER — uses flash-lite, not a name Gemini 404s on.
    """
    global _warned_bad_provider, _warned_bad_gemini_model
    provider = (getattr(settings, "NEWS_LLM_PROVIDER", None) or PROVIDER_GEMINI).strip().lower()
    model = (getattr(settings, "NEWS_LLM_MODEL", None) or "").strip()
    if provider == PROVIDER_OPENAI_COMPAT:
        # Stripped: a key pasted with a trailing newline is an illegal header (h11 refuses
        # it); a URL without a scheme is refused by httpx. Both used to "retry" forever.
        base_url = (getattr(settings, "NEWS_LLM_BASE_URL", None) or "").strip()
        api_key = (getattr(settings, "NEWS_LLM_API_KEY", None) or "").strip()
        url_ok = base_url.lower().startswith(("https://", "http://"))
        if base_url and api_key and url_ok and model and not _is_gemini_model(model):
            field_name = (getattr(settings, "NEWS_LLM_MAX_TOKENS_FIELD", None) or "max_tokens").strip()
            if field_name not in ("max_tokens", "max_completion_tokens"):
                field_name = "max_tokens"
            cap = getattr(settings, "NEWS_LLM_MAX_TOKENS", None)
            return NewsLLMConfig(
                provider=provider, model=model, base_url=base_url, api_key=api_key,
                json_mode=(getattr(settings, "NEWS_LLM_JSON_MODE", None) or "json_object"),
                extra_body=parse_extra_body(getattr(settings, "NEWS_LLM_EXTRA_BODY", None)),
                max_tokens=int(cap) if isinstance(cap, int) and not isinstance(cap, bool) and cap > 0 else None,
                max_tokens_field=field_name,
                send_temperature=getattr(settings, "NEWS_LLM_SEND_TEMPERATURE", True) is not False,
            )
        if not (base_url and api_key):
            reason = "NEWS_LLM_BASE_URL / NEWS_LLM_API_KEY missing"
        elif not url_ok:
            reason = "NEWS_LLM_BASE_URL must start with https://"
        else:
            reason = f"NEWS_LLM_MODEL must name the openai_compat model (got {model or 'nothing'!r})"
    elif provider == PROVIDER_GEMINI:
        if model and _is_gemini_model(model):
            return NewsLLMConfig(provider=PROVIDER_GEMINI, model=model)
        if model and not _warned_bad_gemini_model:
            logger.error("news_llm: NEWS_LLM_MODEL %r is not a Gemini model — using %s "
                         "(unset NEWS_LLM_MODEL after switching back to gemini)",
                         model, DEFAULT_GEMINI_MODEL)
            _warned_bad_gemini_model = True
        return NewsLLMConfig(provider=PROVIDER_GEMINI, model=DEFAULT_GEMINI_MODEL)
    else:
        reason = f"unknown NEWS_LLM_PROVIDER {provider!r}"
    if not _warned_bad_provider:
        logger.error("news_llm: %s — falling back to Gemini %s", reason, DEFAULT_GEMINI_MODEL)
        _warned_bad_provider = True
    return NewsLLMConfig(provider=PROVIDER_GEMINI, model=DEFAULT_GEMINI_MODEL)


def news_model_name() -> str:
    """The model the news features are labelling with right now (recorded on each label)."""
    return news_llm_config().model


def is_transient_news_llm_error(exc: BaseException) -> bool:
    """Capacity / quota / 5xx on EITHER provider — expected degradation, logged as WARNING.

    An openai_compat error is judged by its own TYPE only: Gemini's classifier matches
    substrings ("429", "quota"), which would read an exhausted account ("HTTP 429
    insufficient_quota", deliberately non-transient) as a blip to wait out in silence.
    """
    if isinstance(exc, OpenAICompatError):
        return is_transient_openai_compat_error(exc)
    return is_transient_gemini_error(exc)


def is_content_refusal(exc: BaseException) -> bool:
    """The provider's moderation refused the prompt — an unusable answer for that batch,
    handled like Gemini's blocked prompt (never an outage, never a failed run)."""
    return isinstance(exc, OpenAICompatContentRejected)


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
        max_tokens_field=cfg.max_tokens_field,
        send_temperature=cfg.send_temperature,
    )
    result = await client.generate_json(
        prompt=_schema_prompt(prompt, json_schema, wrapped),
        model=cfg.model,
        system_instruction=system_instruction,
        json_schema=json_schema,
        temperature=temperature,
        usage_tag=usage_tag,
        # Gemini always ran with GEMINI_MAX_TOKENS (8192); a provider default of 4K would cut
        # a 25–50-article enrichment answer mid-JSON and re-bill it on every scroll.
        max_tokens=cfg.max_tokens,
    )
    if wrapped:
        result = {**result, "text": _unwrap_array_text(result.get("text") or "")}
    return result


__all__ = [
    "DEFAULT_GEMINI_MODEL",
    "NewsLLMConfig",
    "PROVIDERS",
    "PROVIDER_GEMINI",
    "PROVIDER_OPENAI_COMPAT",
    "WRAP_KEY",
    "gemini_schema_to_json_schema",
    "generate_news_json",
    "is_content_refusal",
    "is_transient_news_llm_error",
    "news_llm_config",
    "news_model_name",
    "quota_tripped",
]
