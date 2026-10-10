"""
The FMP import boundary of the marketing engine (`.claude/rules/marketing.md` §1).

"No FMP import on the marketing path." Since 2026-09-28 public posts may carry FMP data except
price display (§1), but only through one future adapter that allow-lists permitted fields. Until
it exists, the cheapest way to keep a price out of a public post is to keep the FMP client out of
the code that writes one. Two independent guards:

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

`writer_service` is one exemption, and both halves of that sentence are pinned as they are
true today: it is the only marketing module that imports `app.services.agents` (for
`persona_config.neutral_system_instruction`), and importing it DOES load `app.integrations.fmp`
through `agents/__init__`. If that stops being true, update the exemption and the docstring of
`writer_service.py` together.

`company_news_adapter` (Company Weekly, contract D15) is the other — §1's ONE allow-listing FMP
adapter. Three pins keep it the only door: (a) the AST scan accepts, in that file alone, exactly
the FMP names in `ADAPTER_FMP_NAMES`; (b) every `app.*` module it imports is on
`ADAPTER_ALLOWED_MODULES`, and the FMP-relayed / web-search / LLM modules are named as banned;
(c) nothing under `app/` imports it except `MarketingScriptService._news_source_fn`, inside the
function (a lazy import, like the writer's). A fresh interpreter importing it loads the FMP client
and none of agents, gemini, whale, signals, brave, price_service or home_dashboard.

Mutation-tested by hand (2026-10-09, scratch copies only): adding `from ..whale_service import x`
to a copy of the adapter turned `_adapter_import_problems` red; a module-level
`from . import company_news_adapter` in a copy of `selection.py`, and the same import moved into
a method of another class, each turned `adapter_importers` red; removing an entry from
`ADAPTER_FMP_NAMES` turned the real-tree scan red.

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
    # Report chat's web search (2026-10-02): third-party web results, held transiently for one
    # user's answer under Brave's terms, must never feed a public post (§1 "web-search output is
    # third-party content"). `tests/test_brave_search_boundary.py` pins the wider import graph.
    "app.integrations.brave_search",
    "app.services.chat_web_search_service",
)
FORBIDDEN_NAME = "generate_grounded_research"

#: The pure modules the plan requires to be FMP-free even transitively. `publish_clock` is on the
#: public /go request path (smart_link imports it); `tests/test_marketing_smart_link.py` also pins it
#: stdlib-only.
PURE_MODULES = (
    "numbers", "compliance", "grounding", "content_pool", "selection", "post_copy",
    "writer_prompts", "smart_link", "judge", "generation_budget", "publish_clock",
    # Company Weekly (Drop 2, contract D15): the record types / gates, the logo header checks, the
    # on-screen allow-list and the templates.
    "company_news_rules", "logo_check", "template_onscreen", "news_templates",
)
WRITER_MODULE = "app.services.marketing.writer_service"
ADAPTER_MODULE = "app.services.marketing.company_news_adapter"
#: The two marketing modules that may load the FMP client (see the module docstring).
EXEMPT_MODULES = frozenset({WRITER_MODULE, ADAPTER_MODULE})
ADAPTER_REL = _MARKETING_REL / "company_news_adapter.py"

#: The ONLY FMP names the adapter may import (per-file allow-list of the AST scan).
ADAPTER_FMP_NAMES = frozenset({
    "app.integrations.fmp",
    "app.integrations.fmp.get_fmp_client",
    "app.integrations.fmp.FMPException",
    "app.integrations.fmp.FMPUnavailableException",
    "app.integrations.fmp.FMPPartialPageException",
    "app.integrations.fmp.FMPNotEntitledException",
    "app.integrations.fmp.FMPRateLimitException",
})
#: Every `app.*` module the adapter may import (contract D15). Anything else fails.
ADAPTER_ALLOWED_MODULES = frozenset({
    "app.integrations.fmp",
    "app.services._insider_buys_common", "app.services._insider_common",
    "app.services._whale_common", "app.services._earnings_common",
    "app.services.trillion_club_service", "app.services.trillion_club.builder",
    "app.services.trillion_club.rules", "app.services.trillion_club.copy_rules",
    "app.schemas.trillion_club",
    "app.services.revenue_breakdown_service", "app.services.profit_power_service",
    "app.services.company_facts_service", "app.services.theme_rotation.read_model",
    "app.services.marketing.company_news_rules", "app.services.marketing.selection",
    "app.database", "app.config", "app.utils.supabase_async", "app.utils.inflight",
    "app.utils.market_hours",
    # Drop 2b earnings (2026-10-10): `fetch_calendar_days`, the one-day-per-call, all-or-nothing
    # calendar pager the Home Earnings Shockers card uses. Pure at import (stdlib, market_hours and
    # two pure leaves — `test_the_adapter_exemption_is_exactly_what_it_claims` still holds); the
    # adapter passes it the FMP getter.
    "app.services.earnings_window_service",
})
#: Named explicitly: an FMP-relayed service, third-party web search, or the LLM stack.
ADAPTER_BANNED_MODULES = (
    "app.services.whale_service", "app.services.signals_service", "app.services.universe_data",
    "app.integrations.brave_search", "app.services.chat_web_search_service", "app.services.agents",
    "app.integrations.gemini", "app.services.price_service", "app.services.home_dashboard_service",
    "app.services.theme_insights_service",
)
#: The adapter's only importer: (file, enclosing qualname), function-scoped.
ALLOWED_ADAPTER_IMPORTERS = frozenset({
    ("app/services/marketing/script_service.py", "MarketingScriptService._news_source_fn"),
})


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
        # The adapter alone may import exactly the FMP names on its allow-list; every other
        # forbidden import (whale_service, ...) still fails there too.
        allowed = ADAPTER_FMP_NAMES if Path(rel) == ADAPTER_REL else frozenset()
        bad += [f"{rel}:{line}: imports {name}" for line, name in imported
                if _forbidden(name) and name not in allowed]
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
    "from app.integrations import brave_search",
    "from ...integrations.brave_search import web_search",
    "from app.services.chat_web_search_service import run_web_search",
    "def f():\n    from .. import chat_web_search_service\n",
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
    """Everything in the package except the two exemptions, plus the internal endpoint. One
    interpreter suffices: if the union of imports loads no FMP module, none of them does."""
    mods = sorted(
        _module_of(p)[0] for p in _files()
        if _module_of(p)[0] not in EXEMPT_MODULES
    )
    assert "app.api.v1.endpoints.marketing_internal" in mods and len(mods) >= 8
    assert not set(mods) & EXEMPT_MODULES
    loaded = _loaded_after_import(mods)
    assert _fmp_modules(loaded) == [], _fmp_modules(loaded)


def test_the_writer_service_exemption_is_exactly_what_it_claims():
    """Pinned as TRUE today: importing writer_service loads the FMP client, and the path is
    persona_config (agents/__init__). If this goes red because it stopped being true, narrow the
    exemption in this file and in writer_service's docstring."""
    loaded = _loaded_after_import([WRITER_MODULE])
    assert "app.integrations.fmp" in loaded
    assert "app.services.agents.persona_config" in loaded
    via = _loaded_after_import(["app.services.agents.persona_config"])
    assert "app.integrations.fmp" in via


# ── the company-news adapter: the one FMP door (contract D15) ─────────────────


def _module_exists(dotted: str, backend: Path = _BACKEND) -> bool:
    path = backend.joinpath(*dotted.split("."))
    return path.with_suffix(".py").exists() or (path / "__init__.py").exists()


def adapter_modules_of(src: str, module: str, is_pkg: bool = False, backend: Path = _BACKEND) -> List[Tuple[int, str]]:
    """The MODULES a file imports: ``import a.b`` → a.b; ``from X import Y`` → X.Y when that
    is a module, else X. Every node (a nested import counts); importlib literals too."""
    out: List[Tuple[int, str]] = []
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Import):
            out += [(node.lineno, a.name) for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            base = (_resolve_relative(node.level, node.module or "", module, is_pkg)
                    if node.level else (node.module or ""))
            subs = [f"{base}.{a.name}" for a in node.names if a.name != "*"
                    and _module_exists(f"{base}.{a.name}", backend)]
            if subs:
                out += [(node.lineno, s) for s in subs]
            if not subs or len(subs) < len(node.names):
                out.append((node.lineno, base))
        elif isinstance(node, ast.Call) and _call_name(node) in ("import_module", "__import__"):
            target = _str_arg(node, 0, "name")
            if target is not None and not target.startswith("."):
                out.append((node.lineno, target))
    return out


def _adapter_import_problems(src: str, module: str = ADAPTER_MODULE, backend: Path = _BACKEND) -> List[str]:
    bad = []
    for line, name in adapter_modules_of(src, module, False, backend):
        if not (name == "app" or name.startswith("app.")):
            continue                                   # stdlib / third-party
        banned = [b for b in ADAPTER_BANNED_MODULES if name == b or name.startswith(b + ".")]
        if banned:
            bad.append(f"{line}: imports BANNED {name}")
        elif name not in ADAPTER_ALLOWED_MODULES:
            bad.append(f"{line}: imports {name}, which is not on the adapter's allow-list")
    return bad


def test_the_adapter_imports_only_its_allow_list():
    src = (_BACKEND / ADAPTER_REL).read_text(encoding="utf-8")
    assert _adapter_import_problems(src) == []
    seen = {name for _, name in adapter_modules_of(src, ADAPTER_MODULE)}
    # Anti-vacuity: the scan resolves "from pkg import module" to the module itself.
    assert {"app.integrations.fmp", "app.services.marketing.company_news_rules",
            "app.services.trillion_club.builder", "app.services.marketing.selection"} <= seen
    assert not [m for m in ADAPTER_ALLOWED_MODULES if not _module_exists(m)]


@pytest.mark.parametrize("line", [
    "from app.services import whale_service",
    "from app.services.signals_service import _extract_ceo_buys",
    "import app.services.universe_data",
    "from app.integrations.brave_search import web_search",
    "from app.services.chat_web_search_service import run_web_search",
    "from app.services.agents.persona_config import IDENTITY_RULE",
    "from app.integrations.gemini import get_gemini_client",
    "from app.services.price_service import price_source",
    "from app.services.home_dashboard_service import x",
    "from app.services.theme_insights_service import x",
    "def f():\n    from ..whale_service import x\n",
    "import importlib\nimportlib.import_module('app.services.price_service')",
    "from app.services.marketing import run_service",          # on no list: not allowed either
])
def test_the_adapter_allow_list_flags(line):
    assert _adapter_import_problems(line), line


@pytest.mark.parametrize("line", [
    "from app.integrations.fmp import get_fmp_client",
    "from app.services.trillion_club import builder as b",
    "from app.services.marketing import company_news_rules as R",
    "from app.utils.inflight import fail_shared_future",
    "import httpx\nimport json",
])
def test_the_adapter_allow_list_passes(line):
    assert _adapter_import_problems(line) == [], line


def test_the_adapters_fmp_names_are_the_only_exemption_in_the_scan():
    scan = _scan()
    adapter_fmp = {name for _, name in scan[str(ADAPTER_REL)][0] if _forbidden(name)}
    assert adapter_fmp and adapter_fmp <= ADAPTER_FMP_NAMES          # it does import FMP
    # The exemption is per FILE: the same import anywhere else is still a violation.
    fake = dict(scan)
    fake[str(_MARKETING_REL / "selection.py")] = ([(1, "app.integrations.fmp.get_fmp_client")], [])
    assert _violations(fake) == ["app/services/marketing/selection.py:1: imports app.integrations.fmp.get_fmp_client"]


def _is_adapter(name: str) -> bool:
    return name == ADAPTER_MODULE or name.startswith(ADAPTER_MODULE + ".")


def adapter_importers(src: str, module: str, is_pkg: bool = False) -> List[Tuple[int, str, bool]]:
    """``(line, enclosing qualname or "<module>", inside a function?)`` for every import of
    the adapter in one file — absolute, relative, ``from pkg import adapter`` and importlib
    literals."""
    hits: List[Tuple[int, str, bool]] = []
    stack: List[Tuple[str, str]] = []

    class V(ast.NodeVisitor):
        def _scope(self, node, kind):
            stack.append((kind, node.name))
            self.generic_visit(node)
            stack.pop()

        def visit_FunctionDef(self, node):
            self._scope(node, "function")

        visit_AsyncFunctionDef = visit_FunctionDef

        def visit_ClassDef(self, node):
            self._scope(node, "class")

        def _hit(self, node):
            qual = ".".join(n for _, n in stack) or "<module>"
            hits.append((node.lineno, qual, bool(stack) and stack[-1][0] == "function"))

        def visit_Import(self, node):
            if any(_is_adapter(a.name) for a in node.names):
                self._hit(node)

        def visit_ImportFrom(self, node):
            base = (_resolve_relative(node.level, node.module or "", module, is_pkg)
                    if node.level else (node.module or ""))
            if _is_adapter(base) or any(_is_adapter(f"{base}.{a.name}") for a in node.names):
                self._hit(node)

        def visit_Call(self, node):
            if _call_name(node) in ("import_module", "__import__"):
                target = _str_arg(node, 0, "name")
                if target is not None and target.startswith("."):
                    package = _str_arg(node, 1, "package") or ""
                    level = len(target) - len(target.lstrip("."))
                    target = _resolve_relative(level, target.lstrip("."), package, True)
                if target is not None and _is_adapter(target):
                    self._hit(node)
            self.generic_visit(node)

    V().visit(ast.parse(src))
    return hits


def _adapter_importer_violations(rel: str, hits: Iterable[Tuple[int, str, bool]]) -> List[str]:
    return [f"{rel}:{line}: imports the company-news adapter in {qual}" for line, qual, func in hits
            if not func or (rel, qual) not in ALLOWED_ADAPTER_IMPORTERS]


def test_nothing_else_imports_the_adapter():
    bad, found = [], []
    files = sorted(p for p in (_BACKEND / "app").rglob("*.py") if "__pycache__" not in p.parts)
    assert len(files) > 100
    for path in files:
        rel = str(path.relative_to(_BACKEND))
        if Path(rel) == ADAPTER_REL:
            continue
        module, is_pkg = _module_of(path)
        hits = adapter_importers(path.read_text(encoding="utf-8"), module, is_pkg)
        found += [(rel, q) for _l, q, _f in hits]
        bad += _adapter_importer_violations(rel, hits)
    assert bad == []
    # Anti-vacuity: once script_service grows its seam, the walk must see the import inside it.
    script = (_BACKEND / _MARKETING_REL / "script_service.py").read_text(encoding="utf-8")
    if "def _news_source_fn" in script:
        assert ("app/services/marketing/script_service.py",
                "MarketingScriptService._news_source_fn") in found


_SELF = "app.services.marketing.selection"


@pytest.mark.parametrize("src", [
    "from app.services.marketing import company_news_adapter",
    "import app.services.marketing.company_news_adapter",
    "from app.services.marketing.company_news_adapter import candidates",
    "from . import company_news_adapter",
    "from .company_news_adapter import candidates as c",
    "import importlib\nimportlib.import_module('app.services.marketing.company_news_adapter')",
    "import_module('.company_news_adapter', package='app.services.marketing')",
    "class MarketingScriptService:\n    from . import company_news_adapter\n",
    "class Other:\n    def _news_source_fn(self):\n        from . import company_news_adapter\n",
    "def _news_source_fn():\n    from . import company_news_adapter\n",
])
def test_the_importer_walk_flags(src):
    hits = adapter_importers(src, _SELF)
    assert hits, src
    assert _adapter_importer_violations("app/services/marketing/script_service.py", hits), src


def test_the_importer_walk_passes_the_one_seam():
    src = ("class MarketingScriptService:\n"
           "    def _news_source_fn(self):\n"
           "        from app.services.marketing import company_news_adapter\n"
           "        return company_news_adapter.candidates\n")
    hits = adapter_importers(src, "app.services.marketing.script_service")
    assert hits == [(3, "MarketingScriptService._news_source_fn", True)]
    assert _adapter_importer_violations("app/services/marketing/script_service.py", hits) == []
    # The same seam in any other FILE is not the seam.
    assert _adapter_importer_violations("app/services/marketing/run_service.py", hits)
    assert adapter_importers("from . import company_news_rules\nimport json", _SELF) == []


def test_the_adapter_exemption_is_exactly_what_it_claims():
    """Importing the adapter loads the FMP client (it IS the exemption) and none of the
    FMP-relayed services, web search or the LLM stack."""
    loaded = _loaded_after_import([ADAPTER_MODULE])
    assert ADAPTER_MODULE in loaded and "app.integrations.fmp" in loaded
    banned = ("app.services.agents", "app.integrations.gemini", "app.services.whale_service",
              "app.services.signals_service", "app.integrations.brave_search",
              "app.services.chat_web_search_service", "app.services.price_service",
              "app.services.home_dashboard_service", "app.services.universe_data",
              "app.services.theme_insights_service")
    assert not [m for m in loaded if any(m == b or m.startswith(b + ".") for b in banned)]
