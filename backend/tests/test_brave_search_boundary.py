"""Who may reach the Brave Search client — and the eval scripts may never reach it.

Brave's Search API terms allow TRANSIENT storage only and forbid using results to evaluate or train
an AI (§3(b)(xiii)). The code honours that by keeping the client behind ONE service
(`chat_web_search_service`: per-user transient cache, no Supabase tier) and that service behind the
chat doors. This file pins the import graph by AST (comments and docstrings can neither satisfy
nor trip it):

* `app.integrations.brave_search` is imported only by `chat_web_search_service.py` and `main.py`
  (the lifespan closer);
* `app.services.chat_web_search_service` only by `chat_tools.py`, `chat_service.py` and
  `endpoints/chat.py`;
* nothing under `backend/scripts/` or `app/services/marketing/` imports either;
* both eval scripts force `CHAT_REPORT_WEB_SEARCH_ENABLED = False` at module level, BEFORE any
  function runs.

Anti-vacuity: each allowed importer must really import, and the scanner self-tests every import
form it claims to see.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Dict, List, Set, Tuple

import pytest

_BACKEND = Path(__file__).resolve().parents[1]
_APP = _BACKEND / "app"
_SCRIPTS = _BACKEND / "scripts"

BRAVE = "app.integrations.brave_search"
SERVICE = "app.services.chat_web_search_service"

_ALLOWED: Dict[str, Set[str]] = {
    BRAVE: {"app/services/chat_web_search_service.py", "app/main.py"},
    SERVICE: {"app/services/agents/chat_tools.py", "app/services/chat_service.py",
              "app/api/v1/endpoints/chat.py"},
}
_EVAL_SCRIPTS = ("scripts/eval_chat.py", "scripts/eval_model_routing.py")


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


def _scan(roots: List[Path]) -> Dict[str, Set[str]]:
    out: Dict[str, Set[str]] = {}
    for root in roots:
        for path in sorted(root.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            rel = str(path.relative_to(_BACKEND))
            module, is_pkg = _module_of(rel)
            out[rel] = imported_names(path.read_text(encoding="utf-8"), module, is_pkg)
    return out


@pytest.fixture(scope="module")
def tree() -> Dict[str, Set[str]]:
    return _scan([_APP, _SCRIPTS])


@pytest.mark.parametrize("target", [BRAVE, SERVICE])
def test_only_the_allowed_files_import(tree, target):
    importers = {rel for rel, names in tree.items() if _reaches(names, target)}
    importers.discard(str(Path(target.replace(".", "/") + ".py")))   # the module itself
    assert importers == _ALLOWED[target], (
        f"{target} is imported by {sorted(importers - _ALLOWED[target])} (unexpected) / missing "
        f"from {sorted(_ALLOWED[target] - importers)}. Brave results may be held only transiently "
        "and never used to evaluate an AI — a new caller is a terms question first."
    )


def test_no_script_and_no_marketing_module_reaches_web_search(tree):
    bad = sorted(
        rel for rel, names in tree.items()
        if (rel.startswith("scripts/") or rel.startswith("app/services/marketing/"))
        and (_reaches(names, BRAVE) or _reaches(names, SERVICE))
    )
    assert bad == []


def test_the_scan_is_not_vacuous(tree):
    assert len(tree) > 200
    assert any(rel.startswith("scripts/") for rel in tree)
    assert any(rel.startswith("app/services/marketing/") for rel in tree)
    for target, allowed in _ALLOWED.items():
        for rel in allowed:
            assert rel in tree, rel
            assert _reaches(tree[rel], target), f"{rel} no longer imports {target} — update the list"


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
def test_the_eval_scripts_force_web_search_off_before_anything_runs(rel):
    src = (_BACKEND / rel).read_text(encoding="utf-8")
    line = _module_level_assignment_line(src, "CHAT_REPORT_WEB_SEARCH_ENABLED")
    assert line, f"{rel} must set settings.CHAT_REPORT_WEB_SEARCH_ENABLED = False at module level"
    body = ast.parse(src).body
    first_def = min((n.lineno for n in body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))),
                    default=10 ** 9)
    settings_import = min((n.lineno for n in body if isinstance(n, ast.ImportFrom)
                           and n.module == "app.config"
                           and any(a.name == "settings" for a in n.names)), default=0)
    assert settings_import and settings_import < line < first_def


def test_the_assignment_detector_is_not_vacuous():
    assert _module_level_assignment_line("settings.CHAT_REPORT_WEB_SEARCH_ENABLED = False\n",
                                         "CHAT_REPORT_WEB_SEARCH_ENABLED") == 1
    for src in (
        "# settings.CHAT_REPORT_WEB_SEARCH_ENABLED = False\n",
        "'''settings.CHAT_REPORT_WEB_SEARCH_ENABLED = False'''\n",
        "settings.CHAT_REPORT_WEB_SEARCH_ENABLED = True\n",
        "def f():\n    settings.CHAT_REPORT_WEB_SEARCH_ENABLED = False\n",
        "other.CHAT_REPORT_WEB_SEARCH_ENABLED = False\n",
    ):
        assert _module_level_assignment_line(src, "CHAT_REPORT_WEB_SEARCH_ENABLED") == 0, src


@pytest.mark.parametrize("src", [
    "import app.integrations.brave_search",
    "from app.integrations import brave_search",
    "from app.integrations.brave_search import web_search",
    "def f():\n    from app.integrations import brave_search\n",
    "import importlib\nimportlib.import_module('app.integrations.brave_search')",
    "__import__('app.integrations.brave_search')",
])
def test_the_scanner_sees_every_import_form(src):
    assert _reaches(imported_names(src), BRAVE), src


def test_the_scanner_resolves_relative_imports():
    names = imported_names("from ..integrations import brave_search", "app.services.x")
    assert _reaches(names, BRAVE)
    names = imported_names("from . import chat_web_search_service", "app.services.x")
    assert _reaches(names, SERVICE)


@pytest.mark.parametrize("src", [
    "# from app.integrations import brave_search\n",
    "'''import app.integrations.brave_search'''\n",
    "s = 'app.integrations.brave_search is off limits'\n",
    "from app.integrations import brave_search_other\n",
])
def test_the_scanner_ignores_prose_and_lookalikes(src):
    assert not _reaches(imported_names(src), BRAVE), src
