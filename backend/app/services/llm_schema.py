"""
Gemini `response_schema` dialect → standard JSON Schema. Pure (stdlib only), so a pure caller —
the marketing judge, whose import boundary forbids anything that loads FMP — can use it.

Moved out of `app/services/news_llm.py` (2026-10-30), which re-exports it unchanged; the Claude
provider router (`app/services/llm_provider.py`) uses it too.
"""

from __future__ import annotations

from typing import Any, Dict


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
