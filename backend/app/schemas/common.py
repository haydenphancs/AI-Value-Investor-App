"""Common shared schemas and utilities."""

import math
import re
from typing import Any


def camel_to_snake(name: str) -> str:
    """Convert camelCase to snake_case."""
    s1 = re.sub('(.)([A-Z][a-z]+)', r'\1_\2', name)
    return re.sub('([a-z0-9])([A-Z])', r'\1_\2', s1).lower()


def sanitize_non_finite(value: Any) -> Any:
    """Recursively replace NaN / ±Infinity floats with ``None``.

    FMP emits bare ``NaN`` / ``Infinity`` JSON tokens and ``json.loads`` parses them
    happily, so a non-finite float reaches us looking like an ordinary number: it is
    truthy (so it survives ``x or 0``), and EVERY comparison against it is False (so it
    survives ``if x <= 0``). The endpoints below hand raw FMP dicts straight to
    ``JSONResponse``, which renders with ``json.dumps(..., allow_nan=False)`` — that
    raises, and it raises INSIDE the renderer, i.e. after the endpoint's ``try/except``
    has already returned. The result is a bare HTTP 500 for the whole screen that no
    handler can catch and nothing logs usefully.

    Every Pydantic-modelled response on this path was hardened with a per-service
    ``_safe_float``; the untyped pass-through endpoints had no schema policing them and
    so were missed. Sanitising here covers all of them at the one choke point they
    share, and is a no-op for well-formed data.

    ``None`` (rather than 0.0) is deliberate: absent is honest, zero is a number the
    company never had.
    """
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {k: sanitize_non_finite(v) for k, v in value.items()}
    if isinstance(value, list):
        return [sanitize_non_finite(v) for v in value]
    return value


def normalize_fmp_response(data: dict) -> dict:
    """Convert FMP camelCase keys to snake_case for iOS decoder compatibility.

    Also strips non-finite floats — see :func:`sanitize_non_finite`.
    """
    if not isinstance(data, dict):
        return sanitize_non_finite(data)
    return {camel_to_snake(k): sanitize_non_finite(v) for k, v in data.items()}


def normalize_fmp_list(data: list) -> list:
    """Convert list of FMP dicts to snake_case (non-finite floats stripped)."""
    if not isinstance(data, list):
        return data
    return [normalize_fmp_response(item) if isinstance(item, dict)
            else sanitize_non_finite(item) for item in data]
