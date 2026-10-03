"""No Google Search grounding anywhere in the backend — retired 2026-10-02 for its terms.

The Gemini API Additional Terms for "Grounding with Google Search" require a Grounded Result
to be shown WITH its Search Suggestions, only to the end user who sent the prompt, unmodified,
and not cached, stored or analysed beyond narrow exceptions. Five features used it as shared
background research instead — app-written prompts, parsed into report fields, cached for
24 h to 100 days and served to every user, with an audit copy kept forever and the Search
Suggestions never captured. None of that is fixable by a flag: the owner retired the tool
(licensed data only), and the stored output is purged by migration 188.

A kill switch would not have been enough — every one of the five still served its stored
grounded rows with the switch off. So the guard is on the TOOL: no code under `backend/`
(the API, the marketing worker, scripts) may build a Google Search grounding tool or call
the removed `generate_grounded_research`. Bringing it back is a licensing decision, not a
code change; it needs written permission from Google or a display-compliant design first.

AST-based, so a comment or docstring that explains the history never trips it; string
constants are checked by EXACT value (the raw-REST spelling `{"google_search": {}}`).
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]

# Identifiers that build or read a Google Search grounding call, in every SDK spelling:
# google-genai (`types.Tool(google_search=types.GoogleSearch())`), the legacy retrieval tool,
# Vertex's enterprise web grounding, the raw REST JSON keys, and the response fields only a
# grounded call carries.
FORBIDDEN = frozenset({
    "google_search", "GoogleSearch", "googleSearch",
    "google_search_retrieval", "GoogleSearchRetrieval", "googleSearchRetrieval",
    "enterprise_web_search", "EnterpriseWebSearch", "enterpriseWebSearch",
    "search_entry_point", "grounding_metadata", "grounding_chunks", "web_search_queries",
    "generate_grounded_research",
})

_SKIP_DIRS = {"tests", "__pycache__", ".pytest_cache", "node_modules"}


def _scanned_files() -> list[Path]:
    out = []
    for path in BACKEND.rglob("*.py"):
        rel = path.relative_to(BACKEND).parts
        if any(part in _SKIP_DIRS or part.startswith("venv") or part.startswith(".")
               for part in rel[:-1]):
            continue
        out.append(path)
    return sorted(out)


def _docstring_nodes(tree: ast.AST) -> set[int]:
    ids = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", None) or []
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                    and isinstance(body[0].value.value, str):
                ids.add(id(body[0].value))
    return ids


def forbidden_uses(source: str) -> list[str]:
    tree = ast.parse(source)
    docstrings = _docstring_nodes(tree)
    hits = []
    for node in ast.walk(tree):
        token = None
        if isinstance(node, ast.Name):
            token = node.id
        elif isinstance(node, ast.Attribute):
            token = node.attr
        elif isinstance(node, ast.keyword):
            token = node.arg
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            token = node.name
        elif isinstance(node, ast.alias):
            token = (node.asname or node.name).split(".")[-1]
            if node.name.split(".")[-1] in FORBIDDEN:
                token = node.name.split(".")[-1]
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) \
                and id(node) not in docstrings:
            token = node.value.strip()
        if token in FORBIDDEN:
            hits.append(f"line {getattr(node, 'lineno', '?')}: {token}")
    return hits


def test_the_scan_covers_the_api_the_worker_and_the_scripts():
    files = {str(p.relative_to(BACKEND)) for p in _scanned_files()}
    assert "app/integrations/gemini.py" in files
    assert any(f.startswith("marketing/") for f in files)
    assert any(f.startswith("scripts/") for f in files)
    assert not any(f.startswith("tests/") for f in files)


def test_no_backend_code_uses_google_search_grounding():
    offenders = {}
    for path in _scanned_files():
        hits = forbidden_uses(path.read_text(encoding="utf-8"))
        if hits:
            offenders[str(path.relative_to(BACKEND))] = hits
    assert not offenders, (
        "Google Search grounding is retired (Gemini API Additional Terms; owner decision "
        f"2026-10-02, see this file's docstring): {offenders}"
    )


@pytest.mark.parametrize("snippet", [
    "tools=[types.Tool(google_search=types.GoogleSearch())]",
    "tool = types.Tool(google_search_retrieval=types.GoogleSearchRetrieval())",
    "from google.genai.types import GoogleSearch",
    "body = {'tools': [{'google_search': {}}]}",
    "q = response.candidates[0].grounding_metadata.web_search_queries",
    "chip = meta.search_entry_point.rendered_content",
    "out = await gem.generate_grounded_research(prompt)",
    "async def generate_grounded_research(self, prompt): ...",
    "fn = getattr(gem, 'generate_grounded_research')",
])
def test_the_guard_catches_every_spelling(snippet):
    assert forbidden_uses(snippet), snippet


@pytest.mark.parametrize("snippet", [
    '"""Retired: we no longer call generate_grounded_research or google_search."""\nx = 1',
    "# types.Tool(google_search=types.GoogleSearch())\nx = 1",
    "msg = 'Grounding with Google Search was retired'",
    "web_search = 1  # the Brave report-chat tool is a different, licensed search",
])
def test_the_guard_ignores_prose_and_unrelated_names(snippet):
    assert forbidden_uses(snippet) == [], snippet


def test_no_chat_tool_but_web_search_promises_web_research():
    """The prompt half of the retirement: `explain_price_move` lost its grounded catalyst tier on
    2026-10-02, but its description and capability line still promised "a web-researched
    catalyst with sources" — an invitation to claim web research no tool performed. Only the
    Brave `web_search` tool may mention the web."""
    import re

    from app.services.agents.chat_tools import TOOL_CAPABILITIES, TOOL_DESCRIPTIONS, WEB_SEARCH_TOOL

    web = re.compile(r"\bweb\b|web-|\binternet\b|\bgoogle\b|\bonline\b", re.IGNORECASE)
    offenders = [
        f"{table}[{name}]"
        for table, texts in (("TOOL_DESCRIPTIONS", TOOL_DESCRIPTIONS), ("TOOL_CAPABILITIES", TOOL_CAPABILITIES))
        for name, text in texts.items()
        if name != WEB_SEARCH_TOOL and web.search(text)
    ]
    assert offenders == [], offenders
    assert web.search(TOOL_DESCRIPTIONS[WEB_SEARCH_TOOL]), "the regex must still match the real web tool"
