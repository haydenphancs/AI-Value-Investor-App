"""
The FMP import boundary of the marketing engine (`.claude/rules/marketing.md` §1).

"No FMP data, ever, on a public surface" — the signed Order Form is authenticated-display only,
and Public External Display was declined in writing. The cheapest way to keep FMP data out of a
public post is to keep the FMP client out of the code that writes one. Two independent guards:

1. **AST scan** of every `.py` under `app/services/marketing/` plus
   `app/api/v1/endpoints/marketing_internal.py`: no import — absolute, relative, nested in a
   function, or via `importlib.import_module` / `__import__` with a string literal — of
   `app.integrations.fmp`, the FMP-relayed services (`whale_service`, `signals_service`,
   `universe_data`), or ANY module whose dotted name contains "fmp"; no reference to
   `generate_grounded_research` (web-search output is third-party content); and no dynamic import
   whose target is not a literal (it could not be checked, so it fails closed). Being an AST
   scan, comments and docstrings can never satisfy or trip it.
2. **Subprocess `sys.modules` check**: importing the pure modules in a fresh interpreter must not
   load `app.integrations.fmp` even TRANSITIVELY — which the AST scan cannot see.

`writer_service` is the ONE exemption, and both halves of that sentence are pinned as they are
true today: it is the only marketing module that imports `app.services.agents` (for
`persona_config.neutral_system_instruction`), and importing it DOES load `app.integrations.fmp`
through `agents/__init__`. If that stops being true, update the exemption and the docstring of
`writer_service.py` together.

Mutation-tested by hand (2026-09-23): the whole marketing package + `marketing_internal.py` were
copied to a scratch `backend/` tree; appending `from ...integrations import fmp` to the copy of
`numbers.py`, and separately `importlib.import_module("app.services.whale_service")` inside a
function of the copy of `selection.py`, each turned `_violations(_scan(<scratch>))` non-empty (red);
the real tree stays green. The subprocess guard went red when `app.services.agents.persona_config`
was added to its module list. The synthetic self-tests below keep every import form covered.
"""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Iterable, List, Sequence, Tuple

import pytest

_BACKEND = Path(__file__).resolve().parents[1]
_MARKETING_REL = Path("app/services/marketing")
_INTERNAL_REL = Path("app/api/v1/endpoints/marketing_internal.py")

FORBIDDEN_MODULES = (
    "app.integrations.fmp",
    "app.services.whale_service",
    "app.services.signals_service",
    "app.services.universe_data",
)
FORBIDDEN_NAME = "generate_grounded_research"

#: The pure modules the plan requires to be FMP-free even transitively.
PURE_MODULES = (
    "numbers", "compliance", "grounding", "content_pool", "selection", "post_copy",
    "writer_prompts", "smart_link",
)
EXEMPT_MODULE = "app.services.marketing.writer_service"


# ── the scanner ───────────────────────────────────────────────────────────────


def _files(backend: Path = _BACKEND) -> List[Path]:
    files = sorted(p for p in (backend / _MARKETING_REL).rglob("*.py") if "__pycache__" not in p.parts)
    return files + [backend / _INTERNAL_REL]


def _module_of(path: Path, backend: Path = _BACKEND) -> Tuple[str, bool]:
    parts = list(path.relative_to(backend).with_suffix("").parts)
    is_pkg = parts[-1] == "__init__"
    if is_pkg:
        parts.pop()
    return ".".join(parts), is_pkg


def _resolve_relative(level: int, target: str, module: str, is_pkg: bool) -> str:
    base = module.split(".") if is_pkg else module.split(".")[:-1]
    if level > 1:
        base = base[: max(0, len(base) - (level - 1))]
    return ".".join(base + ([target] if target else []))


def _call_name(node: ast.Call) -> str:
    f = node.func
    if isinstance(f, ast.Attribute):
        return f.attr
    if isinstance(f, ast.Name):
        return f.id
    return ""


def _str_arg(node: ast.Call, pos: int, kw: str):
    if len(node.args) > pos and isinstance(node.args[pos], ast.Constant):
        return node.args[pos].value if isinstance(node.args[pos].value, str) else None
    for k in node.keywords:
        if k.arg == kw and isinstance(k.value, ast.Constant) and isinstance(k.value.value, str):
            return k.value.value
    return None


def imports_of(src: str, module: str, is_pkg: bool = False) -> Tuple[List[Tuple[int, str]], List[Tuple[int, str]]]:
    """(imported dotted names, problems) for one source file. Every node, so an import nested
    in a function (the lazy-import idiom) is seen exactly like a top-level one."""
    tree = ast.parse(src)
    imported: List[Tuple[int, str]] = []
    problems: List[Tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                imported.append((node.lineno, a.name))
                if FORBIDDEN_NAME in (a.name.split(".")[-1], a.asname):
                    problems.append((node.lineno, f"name {FORBIDDEN_NAME}"))
        elif isinstance(node, ast.ImportFrom):
            base = (_resolve_relative(node.level, node.module or "", module, is_pkg)
                    if node.level else (node.module or ""))
            imported.append((node.lineno, base))
            for a in node.names:
                if a.name != "*":
                    imported.append((node.lineno, f"{base}.{a.name}"))
                if FORBIDDEN_NAME in (a.name, a.asname):
                    problems.append((node.lineno, f"name {FORBIDDEN_NAME}"))
        elif isinstance(node, ast.Call):
            name = _call_name(node)
            if name in ("import_module", "__import__"):
                target = _str_arg(node, 0, "name")
                if target is None:
                    problems.append((node.lineno, f"dynamic {name}() with a non-literal target"))
                    continue
                if target.startswith("."):
                    package = _str_arg(node, 1, "package") or ""
                    level = len(target) - len(target.lstrip("."))
                    target = _resolve_relative(level, target.lstrip("."), package, True)
                imported.append((node.lineno, target))
            elif name == "getattr" and _str_arg(node, 1, "name") == FORBIDDEN_NAME:
                problems.append((node.lineno, f"getattr {FORBIDDEN_NAME}"))
        elif isinstance(node, ast.Attribute) and node.attr == FORBIDDEN_NAME:
            problems.append((node.lineno, f"attribute {FORBIDDEN_NAME}"))
        elif isinstance(node, ast.Name) and node.id == FORBIDDEN_NAME:
            problems.append((node.lineno, f"name {FORBIDDEN_NAME}"))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == FORBIDDEN_NAME:
            problems.append((node.lineno, f"defines {FORBIDDEN_NAME}"))
    return imported, problems


def _forbidden(dotted: str) -> bool:
    d = dotted.lower()
    return "fmp" in d or any(d == m or d.startswith(m + ".") for m in FORBIDDEN_MODULES)


def _scan(backend: Path = _BACKEND) -> dict:
    """{relative path: (imported names, problems)} over the scanned tree."""
    out = {}
    for path in _files(backend):
        module, is_pkg = _module_of(path, backend)
        out[str(path.relative_to(backend))] = imports_of(path.read_text(encoding="utf-8"), module, is_pkg)
    return out


def _violations(scan: dict) -> List[str]:
    bad = []
    for rel, (imported, problems) in scan.items():
        bad += [f"{rel}:{line}: imports {name}" for line, name in imported if _forbidden(name)]
        bad += [f"{rel}:{line}: {what}" for line, what in problems]
    return bad


# ── the real tree ─────────────────────────────────────────────────────────────


def test_no_marketing_module_imports_fmp_or_an_fmp_relayed_service():
    assert _violations(_scan()) == []


def test_the_scan_is_not_vacuous():
    scan = _scan()
    assert len(scan) >= 8, sorted(scan)
    assert str(_INTERNAL_REL) in scan
    for must in ("content_pool.py", "compliance.py", "writer_service.py", "selection.py"):
        assert str(_MARKETING_REL / must) in scan, must
    internal = {name for _, name in scan[str(_INTERNAL_REL)][0]}
    assert "app.services.marketing.run_service" in internal, sorted(internal)
    writer = {name for _, name in scan[str(_MARKETING_REL / "writer_service.py")][0]}
    assert "app.services.agents.persona_config" in writer
    # The nested lazy import in writer_service is seen too.
    assert "app.integrations.gemini.get_gemini_client" in writer


def test_writer_service_is_the_only_marketing_module_that_reaches_into_agents():
    reach = {
        (rel, name)
        for rel, (imported, _) in _scan().items()
        for _, name in imported
        if name == "app.services.agents" or name.startswith("app.services.agents.")
    }
    wrel = str(_MARKETING_REL / "writer_service.py")
    assert reach == {
        (wrel, "app.services.agents.persona_config"),
        (wrel, "app.services.agents.persona_config.neutral_system_instruction"),
    }, sorted(reach)


# ── the scanner catches every form (permanent self-test) ──────────────────────

_MOD = "app.services.marketing.example"


@pytest.mark.parametrize("src", [
    "import app.integrations.fmp",
    "import app.integrations.fmp as f",
    "from app.integrations import fmp",
    "from app.integrations.fmp import FMPClient",
    "from ...integrations import fmp",
    "from ...integrations.fmp import get_fmp_client",
    "from .. import whale_service",
    "from ..signals_service import x",
    "from app.services import universe_data",
    "from app.services.agents.fmp_tools import TOOLS",
    "from app.services.fmp_entitlement_cache import x",
    "def f():\n    from app.integrations import fmp\n",
    "async def f():\n    import app.services.whale_service\n",
    "import importlib\nimportlib.import_module('app.integrations.fmp')",
    "from importlib import import_module\nimport_module('app.services.signals_service')",
    "import_module(name='app.services.universe_data')",
    "import_module('.fmp_tools', package='app.services.agents')",
    "import_module('..whale_service', 'app.services.marketing')",
    "__import__('app.integrations.fmp')",
    "import importlib\nimportlib.import_module(some_variable)",
    "__import__(prefix + '.fmp')",
    "x = client.generate_grounded_research(q)",
    "from app.integrations.gemini import generate_grounded_research",
    "from app.integrations.gemini import generate_grounded_research as g",
    "fn = getattr(client, 'generate_grounded_research')",
    "generate_grounded_research = None",
])
def test_the_scanner_flags(src):
    imported, problems = imports_of(src, _MOD)
    flagged = [n for _, n in imported if _forbidden(n)] + [p for _, p in problems]
    assert flagged, src


@pytest.mark.parametrize("src", [
    "import json\nfrom app.services.marketing import numbers",
    "from . import compliance\nfrom .grounding import check_grounding",
    "from app.services.chat_security import normalize_text",
    '"""Never import app.integrations.fmp or call generate_grounded_research."""\n',
    "# from app.integrations import fmp\n# client.generate_grounded_research()\nx = 1",
    "import importlib\nimportlib.import_module('app.services.marketing.numbers')",
    "s = 'app.integrations.fmp is forbidden'",
])
def test_the_scanner_passes(src):
    imported, problems = imports_of(src, _MOD)
    assert not [n for _, n in imported if _forbidden(n)] and not problems, (src, imported, problems)


def test_relative_imports_resolve_against_the_right_package():
    imported, _ = imports_of("from . import a\nfrom .. import b\nfrom ...integrations import c",
                             "app.services.marketing.mod")
    names = {n for _, n in imported}
    assert {"app.services.marketing.a", "app.services.b", "app.integrations.c"} <= names
    imported, _ = imports_of("from . import a", "app.services.marketing", is_pkg=True)
    assert "app.services.marketing.a" in {n for _, n in imported}


# ── transitive: a fresh interpreter's sys.modules ─────────────────────────────


def _loaded_after_import(modules: Sequence[str]) -> List[str]:
    code = (
        "import importlib, json, sys\n"
        f"for m in {list(modules)!r}:\n"
        "    importlib.import_module(m)\n"
        "print(json.dumps(sorted(sys.modules)))\n"
    )
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "SENTRY_DSN": ""}
    proc = subprocess.run([sys.executable, "-c", code], cwd=_BACKEND, capture_output=True,
                          text=True, timeout=180, env=env)
    assert proc.returncode == 0, proc.stderr[-2000:]
    return json.loads(proc.stdout.strip().splitlines()[-1])


def _fmp_modules(loaded: Iterable[str]) -> List[str]:
    return sorted(m for m in loaded if "fmp" in m.lower())


def _pure_modules() -> List[str]:
    present = []
    for name in PURE_MODULES:
        if (_BACKEND / _MARKETING_REL / f"{name}.py").exists():
            present.append(f"app.services.marketing.{name}")
        else:
            assert name == "smart_link", f"pure module {name}.py is missing"
    return present


def test_the_pure_modules_never_load_fmp_even_transitively():
    mods = _pure_modules()
    assert len(mods) >= 7
    loaded = _loaded_after_import(mods)
    assert set(mods) <= set(loaded)  # anti-vacuity: they really were imported
    assert "app.integrations.fmp" not in loaded
    assert _fmp_modules(loaded) == []
    assert not [m for m in loaded if m == "app.services.agents" or m.startswith("app.services.agents.")]


def test_every_other_marketing_module_is_fmp_free_at_import_too():
    """Everything in the package except the exemption, plus the internal endpoint. One
    interpreter suffices: if the union of imports loads no FMP module, none of them does."""
    mods = sorted(
        _module_of(p)[0] for p in _files()
        if _module_of(p)[0] != EXEMPT_MODULE
    )
    assert "app.api.v1.endpoints.marketing_internal" in mods and len(mods) >= 8
    loaded = _loaded_after_import(mods)
    assert _fmp_modules(loaded) == [], _fmp_modules(loaded)


def test_the_writer_service_exemption_is_exactly_what_it_claims():
    """Pinned as TRUE today: importing writer_service loads the FMP client, and the path is
    persona_config (agents/__init__). If this goes red because it stopped being true, narrow the
    exemption in this file and in writer_service's docstring."""
    loaded = _loaded_after_import([EXEMPT_MODULE])
    assert "app.integrations.fmp" in loaded
    assert "app.services.agents.persona_config" in loaded
    via = _loaded_after_import(["app.services.agents.persona_config"])
    assert "app.integrations.fmp" in via
