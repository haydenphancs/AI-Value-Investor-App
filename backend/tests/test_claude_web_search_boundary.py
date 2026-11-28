"""Who may reach the jump search's fallback web lookup — and nothing that evaluates AI may.

2026-11-27 (owner: "use Claude API (200 credits as fallback)"): Claude's server-side web search
finds the pages behind a big stock move when Brave cannot. It is the ONE app feature that calls
Claude, and its results are web results like Brave's: kept only in the Updates Insights card they
explain, never used to evaluate or tune an AI (SYSTEM_DESIGN_GUIDELINES §9b.10a). So, by AST
(comments and docstrings can neither satisfy nor trip it):

* `cited_web_search` is DEFINED only in `anthropic_client.py` (the client) and `llm_provider.py`
  (`ClaudeOnly`), and CALLED only from `llm_provider.py` and `jump_claude_lookup_service.py`; the
  bare name appears as a string only in `log_redaction.py` (the Sentry frame scrub);
* `app.services.jump_claude_lookup_service` is imported only by `jump_web_search_service.py` (never
  `main.py`: the sweeper's shutdown closes the jump search, `tests/test_brave_search_boundary.py`);
* nothing under `backend/scripts/` or `app/services/marketing/` imports it;
* the switch ships OFF, and every script that evaluates model output forces it OFF at module level.

Anti-vacuity: the allowed importer and callers must really import / call, and each scanner
self-tests the forms it claims to see.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Dict, List, Set, Tuple

import pytest

_BACKEND = Path(__file__).resolve().parents[1]
_APP = _BACKEND / "app"
_SCRIPTS = _BACKEND / "scripts"

LOOKUP = "app.services.jump_claude_lookup_service"
_IMPORTERS = {"app/services/jump_web_search_service.py"}
_DEFINERS = {"app/integrations/anthropic_client.py", "app/services/llm_provider.py"}
_CALLERS = {"app/services/llm_provider.py", "app/services/jump_claude_lookup_service.py"}
_NAME_AS_STRING = {"app/log_redaction.py"}
_METHOD = "cited_web_search"
#: Every script that evaluates model output — the chat evals and the Insights card preview.
_EVAL_SCRIPTS = ("scripts/eval_chat.py", "scripts/eval_model_routing.py", "scripts/preview_insight_card.py")
_SWITCH = "UPDATES_JUMP_CLAUDE_FALLBACK_ENABLED"


def _module_of(rel: str) -> Tuple[str, bool]:
    parts = list(Path(rel).with_suffix("").parts)
    is_pkg = parts[-1] == "__init__"
    if is_pkg:
        parts.pop()
    return ".".join(parts), is_pkg


def _resolve_relative(level: int, target: str, module: str, is_pkg: bool) -> str:
    base = module.split(".") if is_pkg else module.split(".")[:-1]
    if level > 1:
        base = base[: max(0, len(base) - (level - 1))]
    return ".".join(base + ([target] if target else []))


def imported_names(src: str, module: str = "app.x", is_pkg: bool = False) -> Set[str]:
    """Every dotted name a source file imports — top-level or nested, absolute or relative,
    `from pkg import mod`, and `importlib.import_module` / `__import__` with a string literal."""
    out: Set[str] = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Import):
            out.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = (_resolve_relative(node.level, node.module or "", module, is_pkg)
                    if node.level else (node.module or ""))
            out.add(base)
            out.update(f"{base}.{a.name}" for a in node.names if a.name != "*")
        elif isinstance(node, ast.Call):
            f = node.func
            name = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")
            if name in ("import_module", "__import__") and node.args and \
                    isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                out.add(node.args[0].value)
    return out


def _reaches(names: Set[str], target: str) -> bool:
    return any(n == target or n.startswith(target + ".") for n in names)


def method_uses(src: str) -> Dict[str, int]:
    """How a source file touches `cited_web_search`: as an attribute (`x.cited_web_search`), as a
    function or method it DEFINES, and as a string constant (a `getattr(x, "cited_web_search")`)."""
    out = {"attribute": 0, "definition": 0, "string": 0}
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Attribute) and node.attr == _METHOD:
            out["attribute"] += 1
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == _METHOD:
            out["definition"] += 1
        elif isinstance(node, ast.Constant) and node.value == _METHOD:
            out["string"] += 1
    return out


def _sources(roots: List[Path]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for root in roots:
        for path in sorted(root.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            out[str(path.relative_to(_BACKEND))] = path.read_text(encoding="utf-8")
    return out


@pytest.fixture(scope="module")
def sources() -> Dict[str, str]:
    return _sources([_APP, _SCRIPTS])


@pytest.fixture(scope="module")
def imports(sources) -> Dict[str, Set[str]]:
    return {rel: imported_names(src, *_module_of(rel)) for rel, src in sources.items()}


@pytest.fixture(scope="module")
def uses(sources) -> Dict[str, Dict[str, int]]:
    return {rel: method_uses(src) for rel, src in sources.items()}


def test_only_the_jump_search_imports_the_lookup(imports):
    importers = {rel for rel, names in imports.items() if _reaches(names, LOOKUP)}
    importers.discard("app/services/jump_claude_lookup_service.py")
    assert importers == _IMPORTERS, (
        f"{LOOKUP} is imported by {sorted(importers - _IMPORTERS)} (unexpected) / missing from "
        f"{sorted(_IMPORTERS - importers)}. Its pages are web results: a new caller is a terms question first."
    )


def test_no_script_and_no_marketing_module_reaches_the_lookup(imports):
    bad = sorted(rel for rel, names in imports.items()
                 if (rel.startswith("scripts/") or rel.startswith("app/services/marketing/"))
                 and _reaches(names, LOOKUP))
    assert bad == []
    assert not _reaches(imports["app/main.py"], LOOKUP), "closed through the sweeper's shutdown, never main.py"


def test_the_web_search_call_is_defined_and_called_only_where_allowed(uses):
    definers = {rel for rel, u in uses.items() if u["definition"]}
    callers = {rel for rel, u in uses.items() if u["attribute"]}
    strings = {rel for rel, u in uses.items() if u["string"]}
    assert definers == _DEFINERS, sorted(definers ^ _DEFINERS)
    assert callers == _CALLERS, (
        f"cited_web_search is reached from {sorted(callers - _CALLERS)} (unexpected) / not from "
        f"{sorted(_CALLERS - callers)} — the jump lookup is the one app feature that may search with Claude")
    assert strings == _NAME_AS_STRING, sorted(strings ^ _NAME_AS_STRING)


def test_the_switch_ships_off():
    from app.config import Settings

    assert Settings.model_fields[_SWITCH].default is False


def _module_level_assignment_line(src: str, attr: str) -> int:
    """Line of a MODULE-LEVEL `settings.<attr> = False`, or 0."""
    for node in ast.parse(src).body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant) \
                and node.value.value is False:
            for t in node.targets:
                if isinstance(t, ast.Attribute) and t.attr == attr \
                        and isinstance(t.value, ast.Name) and t.value.id == "settings":
                    return node.lineno
    return 0


@pytest.mark.parametrize("rel", _EVAL_SCRIPTS)
def test_the_evaluation_scripts_force_the_fallback_off_before_anything_runs(rel):
    src = (_BACKEND / rel).read_text(encoding="utf-8")
    line = _module_level_assignment_line(src, _SWITCH)
    assert line, f"{rel} must set settings.{_SWITCH} = False at module level"
    body = ast.parse(src).body
    first_def = min((n.lineno for n in body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))),
                    default=10 ** 9)
    settings_import = min((n.lineno for n in body if isinstance(n, ast.ImportFrom)
                           and n.module == "app.config"
                           and any(a.name == "settings" for a in n.names)), default=0)
    assert settings_import and settings_import < line < first_def


# ── anti-vacuity ──────────────────────────────────────────────────────────────
def test_the_scan_is_not_vacuous(sources, imports, uses):
    assert len(sources) > 200
    assert any(rel.startswith("scripts/") for rel in sources)
    assert any(rel.startswith("app/services/marketing/") for rel in sources)
    for rel in _IMPORTERS:
        assert _reaches(imports[rel], LOOKUP), f"{rel} no longer imports {LOOKUP} — update the list"
    for rel in _CALLERS:
        assert uses[rel]["attribute"], f"{rel} no longer calls {_METHOD} — update the list"
    for rel in _DEFINERS:
        assert uses[rel]["definition"], f"{rel} no longer defines {_METHOD} — update the list"


@pytest.mark.parametrize("src", [
    "import app.services.jump_claude_lookup_service",
    "from app.services import jump_claude_lookup_service as claude",
    "from app.services.jump_claude_lookup_service import lookup",
    "def f():\n    from app.services import jump_claude_lookup_service as claude\n",
    "import importlib\nimportlib.import_module('app.services.jump_claude_lookup_service')",
])
def test_the_import_scanner_sees_every_form(src):
    assert _reaches(imported_names(src), LOOKUP), src


def test_the_import_scanner_resolves_relative_imports():
    assert _reaches(imported_names("from . import jump_claude_lookup_service", "app.services.x"), LOOKUP)


@pytest.mark.parametrize("src,kind", [
    ("await only.cited_web_search('p')", "attribute"),
    ("f = client.cited_web_search", "attribute"),
    ("async def cited_web_search(self):\n    pass\n", "definition"),
    ("def cited_web_search():\n    pass\n", "definition"),
    ("getattr(c, 'cited_web_search')", "string"),
])
def test_the_method_scanner_sees_every_form(src, kind):
    assert method_uses(src)[kind] == 1, src


@pytest.mark.parametrize("src", [
    "# only.cited_web_search('p')\n",
    "'''only.cited_web_search'''\n",
    "x = 'call cited_web_search later'\n",
    "only.cited_web_search_v2('p')\n",
])
def test_the_method_scanner_ignores_prose_and_lookalikes(src):
    u = method_uses(src)
    assert u["attribute"] == 0 and u["definition"] == 0, src
