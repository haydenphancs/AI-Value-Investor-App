"""Common shared schemas and utilities."""

import re


def camel_to_snake(name: str) -> str:
    """Convert camelCase to snake_case."""
    s1 = re.sub('(.)([A-Z][a-z]+)', r'\1_\2', name)
    return re.sub('([a-z0-9])([A-Z])', r'\1_\2', s1).lower()


def normalize_fmp_response(data: dict) -> dict:
    """Convert FMP camelCase keys to snake_case for iOS decoder compatibility."""
    if not isinstance(data, dict):
        return data
    return {camel_to_snake(k): v for k, v in data.items()}


def normalize_fmp_list(data: list) -> list:
    """Convert list of FMP dicts to snake_case."""
    return [normalize_fmp_response(item) if isinstance(item, dict) else item for item in data]
