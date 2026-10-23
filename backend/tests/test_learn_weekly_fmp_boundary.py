"""The FMP boundary of the Wiser weekly content engine (plan §1.1, §1.5; contracts.py docstring).

A Learn article is stored, narrated and cached offline, and the FMP Order Form forbids display "in
any downloadable fashion" — so nothing FMP may be frozen into one. FMP only RANKS the week's
topics, through ONE module, `learn_weekly/signals.py`, which hands back `TickerSignal` /
`ThemeInput` (counts, booleans, identifiers). Three independent guards:

1. **AST scan** of every `.py` under `app/services/learn_weekly/`: no import — absolute, relative,
   nested in a function, or `importlib.import_module` / `__import__` with a literal — of
   `app.integrations.fmp`, of any module whose dotted name contains "fmp", or of the FMP-relayed
   services marketing also bans; a dynamic import with a non-literal target fails closed. Only
   `signals.py` is exempt, and it is pinned to really import the client (anti-vacuity). Being an
   AST scan, comments and docstrings can never satisfy or trip it.
2. **Fresh interpreter**: importing the pipeline's other modules must not load
   `app.integrations.fmp` (or `learn_weekly.signals`) even TRANSITIVELY — which the AST scan
   cannot see. Modules a wave has not written yet are skipped, never faked. ⚠️ A module that
   imports `app.services.agents.persona_config` at module level fails this: `agents/__init__`
   loads the FMP client. Import `neutral_system_instruction` inside the function that calls the
   model (`topic._llm_json` does).
3. **Sentinel**: FMP fixtures carry unique strings (a company name, a CEO, a headline, a URL) and
   a 7-digit number (inside the market cap, the price, the volume, a calendar revenue). They run
   through the REAL `signals.universe` / `collect_signals` and the real topic step (SEC and the
   model faked), and none may reach a `TickerSignal`, the stored topic JSON, or any prompt or
   system instruction sent to the model.

Mutation-tested by hand (2026-10-22): a module-level `from app.integrations import fmp` added to
`topic.py` turned guards 1 and 2 red; `_profile_size` returning the profile's `companyName` as the
`excluded` reason turned guard 3 red; both files were restored byte-for-byte (cmp).
"""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import List, Sequence, Tuple

import pytest

from app.config import settings
from app.integrations import sec_edgar
from app.services.learn_weekly import case_library, ledger, schedule, sec_facts, signals, topic

_BACKEND = Path(__file__).resolve().parents[1]
_PKG_REL = Path("app/services/learn_weekly")
_DOOR = _PKG_REL / "signals.py"

FORBIDDEN_MODULES = (
    "app.integrations.fmp",
    "app.services.whale_service",
    "app.services.signals_service",
    "app.services.universe_data",
)
#: The pipeline modules that must stay FMP-free at import (plan §1.1), plus wave A's. Missing
#: files are skipped (later waves); the anti-vacuity check below needs the wave-A ones present.
FRESH_MODULES = (
    "fact_sheet", "prompts", "writer", "validators", "judge", "narration", "case_library", "review",
    "publisher", "alerts", "build",
    "contracts", "schedule", "topic_scorer", "errors", "ledger", "sec_facts", "topic",
)
_WAVE_A = ("fact_sheet", "validators", "case_library", "contracts", "schedule", "topic_scorer",
           "ledger", "sec_facts", "topic")


# ── 1. the AST scan ───────────────────────────────────────────────────────────────────────────


def _module_of(path: Path) -> Tuple[str, bool]:
    parts = list(path.relative_to(_BACKEND).with_suffix("").parts)
    is_pkg = parts[-1] == "__init__"
    if is_pkg:
        parts.pop()
    return ".".join(parts), is_pkg


def _resolve_relative(level: int, target: str, module: str, is_pkg: bool) -> str:
    base = module.split(".") if is_pkg else module.split(".")[:-1]
    if level > 1:
        base = base[: max(0, len(base) - (level - 1))]
    return ".".join(base + ([target] if target else []))


def _literal(node: ast.Call, pos: int, kw: str):
    if len(node.args) > pos and isinstance(node.args[pos], ast.Constant) and isinstance(node.args[pos].value, str):
        return node.args[pos].value
    for k in node.keywords:
        if k.arg == kw and isinstance(k.value, ast.Constant) and isinstance(k.value.value, str):
            return k.value.value
    return None


def imports_of(src: str, module: str, is_pkg: bool = False) -> Tuple[List[Tuple[int, str]], List[Tuple[int, str]]]:
    """(imported dotted names, problems). Every node is walked, so a lazy import inside a function
    is seen exactly like a top-level one."""
    imported: List[Tuple[int, str]] = []
    problems: List[Tuple[int, str]] = []
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Import):
            imported += [(node.lineno, a.name) for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            base = (_resolve_relative(node.level, node.module or "", module, is_pkg)
                    if node.level else (node.module or ""))
            imported.append((node.lineno, base))
            imported += [(node.lineno, f"{base}.{a.name}") for a in node.names if a.name != "*"]
        elif isinstance(node, ast.Call):
            f = node.func
            name = f.attr if isinstance(f, ast.Attribute) else f.id if isinstance(f, ast.Name) else ""
            if name in ("import_module", "__import__"):
                target = _literal(node, 0, "name")
                if target is None:
                    problems.append((node.lineno, f"dynamic {name}() with a non-literal target"))
                    continue
                if target.startswith("."):
                    level = len(target) - len(target.lstrip("."))
                    target = _resolve_relative(level, target.lstrip("."), _literal(node, 1, "package") or "", True)
                imported.append((node.lineno, target))
    return imported, problems


def _forbidden(dotted: str) -> bool:
    d = dotted.lower()
    return "fmp" in d or any(d == m or d.startswith(m + ".") for m in FORBIDDEN_MODULES)


def _files(backend: Path = _BACKEND) -> List[Path]:
    return sorted(p for p in (backend / _PKG_REL).rglob("*.py") if "__pycache__" not in p.parts)


def _scan(backend: Path = _BACKEND) -> dict:
    out = {}
    for path in _files(backend):
        module, is_pkg = _module_of(path) if backend == _BACKEND else (
            ".".join(path.relative_to(backend).with_suffix("").parts), path.name == "__init__.py")
        out[str(path.relative_to(backend))] = imports_of(path.read_text(encoding="utf-8"), module, is_pkg)
    return out


def violations(scan: dict) -> List[str]:
    bad = []
    for rel, (imported, problems) in scan.items():
        exempt = Path(rel) == _DOOR
        bad += [f"{rel}:{line}: imports {name}" for line, name in imported
                if _forbidden(name) and not (exempt and name.startswith("app.integrations.fmp"))]
        bad += [f"{rel}:{line}: {what}" for line, what in problems]
    return bad


def test_only_signals_imports_fmp_in_the_learn_weekly_package():
    assert violations(_scan()) == []


def test_the_scan_is_not_vacuous():
    scan = _scan()
    assert len(scan) >= 10, sorted(scan)
    door = {name for _, name in scan[str(_DOOR)][0]}
    assert "app.integrations.fmp" in door, "signals.py is the FMP door — it must really import it"
    assert "app.integrations.fmp.get_fmp_client" in door
    topic_imports = {name for _, name in scan[str(_PKG_REL / "topic.py")][0]}
    # The lazy imports inside functions are seen too.
    assert "app.services.learn_weekly.signals" in topic_imports
    assert "app.services.agents.persona_config.neutral_system_instruction" in topic_imports


@pytest.mark.parametrize("src", [
    "import app.integrations.fmp",
    "from app.integrations import fmp",
    "from app.integrations.fmp import FMPClient",
    "from ...integrations import fmp",
    "from ...integrations.fmp import get_fmp_client",
    "from app.integrations.fmp_entitlements import x",
    "from app.services.agents.fmp_tools import build_fmp_tool_declarations",
    "def f():\n    from app.integrations import fmp\n",
    "async def f():\n    import app.services.whale_service\n",
    "from .. import signals_service",
    "import importlib\nimportlib.import_module('app.integrations.fmp')",
    "__import__('app.integrations.fmp')",
    "import_module('..integrations.fmp', 'app.services.learn_weekly')",
    "import importlib\nimportlib.import_module(name)",
])
def test_the_scanner_flags(src):
    imported, problems = imports_of(src, "app.services.learn_weekly.example")
    assert [n for _, n in imported if _forbidden(n)] + problems, src


@pytest.mark.parametrize("src", [
    "from app.services.learn_weekly import signals",          # importing the door is fine
    "from . import contracts\nfrom .sec_facts import company_summary",
    '"""Never import app.integrations.fmp here."""',
    "# from app.integrations import fmp\nx = 1",
    "s = 'app.integrations.fmp'",
])
def test_the_scanner_passes(src):
    imported, problems = imports_of(src, "app.services.learn_weekly.example")
    assert not [n for _, n in imported if _forbidden(n)] and not problems, (src, imported, problems)


def test_a_planted_import_in_a_scratch_tree_is_caught(tmp_path):
    """Mutation, permanently: a copy of the package with one bad line is red; the copy as-is green."""
    pkg = tmp_path / "app" / "services" / "learn_weekly"
    pkg.mkdir(parents=True)
    for path in _files():
        (pkg / path.name).write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    assert violations(_scan(tmp_path)) == []
    target = pkg / "topic.py"
    target.write_text(target.read_text(encoding="utf-8") + "\nfrom ...integrations import fmp\n",
                      encoding="utf-8")
    bad = violations(_scan(tmp_path))
    assert bad and "topic.py" in bad[0]


# ── 2. a fresh interpreter's sys.modules ──────────────────────────────────────────────────────


def _loaded_after_import(modules: Sequence[str]) -> List[str]:
    code = (
        "import importlib, json, sys\n"
        f"for m in {list(modules)!r}:\n"
        "    importlib.import_module(m)\n"
        "print(json.dumps(sorted(sys.modules)))\n"
    )
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "SENTRY_DSN": ""}
    proc = subprocess.run([sys.executable, "-c", code], cwd=_BACKEND, capture_output=True, text=True,
                          timeout=180, env=env)
    assert proc.returncode == 0, proc.stderr[-2000:]
    return json.loads(proc.stdout.strip().splitlines()[-1])


def _present() -> List[str]:
    out = []
    for name in FRESH_MODULES:
        if (_BACKEND / _PKG_REL / f"{name}.py").exists():
            out.append(f"app.services.learn_weekly.{name}")
        else:
            assert name not in _WAVE_A, f"wave-A module {name}.py is missing"
    return out


def _bad_loads(loaded: Sequence[str]) -> List[str]:
    return sorted(m for m in loaded
                  if "fmp" in m.lower() or m == "app.services.learn_weekly.signals")


def test_no_pipeline_module_loads_fmp_even_transitively():
    mods = _present()
    loaded = _loaded_after_import(mods)
    assert set(mods) <= set(loaded)     # anti-vacuity: they really were imported
    bad = _bad_loads(loaded)
    if bad:
        # Name the culprit(s) — one interpreter per module, only on failure.
        culprits = [m for m in mods if _bad_loads(_loaded_after_import([m]))]
        pytest.fail(f"importing {culprits} loads {bad}. Import the FMP door (signals) or "
                    "persona_config lazily, inside the function that needs it.")


def test_the_door_really_loads_fmp():
    """The other direction: if `signals` stopped loading the client, guard 2 could be passing
    because nothing anywhere imports FMP any more — this pins that it is the door that does."""
    assert "app.integrations.fmp" in _loaded_after_import(["app.services.learn_weekly.signals"])


# ── 3. the sentinel ───────────────────────────────────────────────────────────────────────────

SENT_NAME = "ZQXSENTINELCORP Holdings"
SENT_CEO = "Zqxanna Sentinelova"
SENT_HEADLINE = "ZQX sentinel headline about record demand"
SENT_URL = "https://zqx-sentinel.example/article"
SENT_NUM = "7654321"
SENTINELS = (SENT_NAME, "ZQXSENTINEL", SENT_CEO, "Zqxanna", SENT_HEADLINE, "zqx-sentinel", SENT_NUM)

NOW = schedule.deep_dive_times_for(date(2026, 10, 22)).topic_at + timedelta(minutes=5)
DD = schedule.deep_dive_times_for(date(2026, 10, 22))
TODAY = date(2026, 10, 22)
CRWV, ORCL, MSFT = "0001769628", "0001341439", "0000789019"
ACC_C, ACC_O = "0001769628-26-000101", "0001341439-26-000050"
CRWV_SPAN = ("On October 20, 2026, CoreWeave, Inc. entered into a Master Services Agreement with a "
             "customer for the provision of cloud computing capacity.")
ORCL_SPAN = ("Total revenues for the first quarter of fiscal 2027 were $16.1 billion, compared with "
             "$13.3 billion for the first quarter of fiscal 2026.")


def _news(ticker: str, per_week: Sequence[int]):
    rows = []
    for k, n in enumerate(per_week):
        for i in range(n):
            day = TODAY - timedelta(days=7 * k + (i % 7))
            rows.append({"symbol": ticker, "publishedDate": f"{day.isoformat()} 09:30:00",
                         "title": f"{SENT_HEADLINE} {k}-{i}", "text": f"{SENT_NAME} said {SENT_NUM} units",
                         "url": f"{SENT_URL}/{ticker}/{k}-{i}", "site": "zqx-sentinel.example",
                         "publisher": SENT_NAME, "image": f"{SENT_URL}/img.png"})
    rows.sort(key=lambda r: r["publishedDate"], reverse=True)
    return rows


def _profile(ticker, exchange="NASDAQ"):
    return {"symbol": ticker, "companyName": SENT_NAME, "ceo": SENT_CEO, "description": SENT_HEADLINE,
            "marketCap": float(f"2{SENT_NUM}000"), "price": float(f"{SENT_NUM}.5"), "volAvg": int(SENT_NUM),
            "exchange": exchange, "exchangeFullName": f"{SENT_NAME} Exchange", "website": SENT_URL,
            "isEtf": False, "isFund": False, "isActivelyTrading": True, "image": SENT_URL}


class _FMP:
    def __init__(self):
        spike = [30] + [5] * 8
        self.news = {"CRWV": _news("CRWV", spike), "ORCL": _news("ORCL", spike), "MSFT": _news("MSFT", [5] * 9),
                     "OTCX": _news("OTCX", spike)}
        self.calls = {"news": 0, "profile": 0, "calendar": 0}

    async def get_stock_news(self, ticker=None, limit=10, from_date=None, to_date=None, page=0):
        self.calls["news"] += 1
        return self.news.get(ticker, [])[page * limit:(page + 1) * limit]

    async def get_company_profile(self, ticker):
        self.calls["profile"] += 1
        return _profile(ticker, exchange="OTC" if ticker == "OTCX" else "NASDAQ")

    async def get_earnings_calendar(self, from_date=None, to_date=None):
        self.calls["calendar"] += 1
        return [{"symbol": "ORCL", "date": from_date, "revenueEstimated": float(f"1{SENT_NUM}000"),
                 "epsEstimated": float(f"{SENT_NUM}.0"), "lastUpdated": SENT_HEADLINE}]


def _subs(name, filings, sic):
    return {"name": name, "tickers": [], "exchanges": [], "sic": sic, "sicDescription": "x",
            "filings": {"recent": {"form": [f[0] for f in filings], "filingDate": [f[1] for f in filings],
                                   "items": [f[2] for f in filings], "accessionNumber": [f[3] for f in filings],
                                   "primaryDocument": [f[4] for f in filings]}}}


def _index(acc, primary, ex99=None):
    rows = f'<tr><td>1</td><td>8-K</td><td><a href="/x/{primary}">{primary}</a></td><td>8-K</td><td>1</td></tr>'
    if ex99:
        rows += f'<tr><td>2</td><td>PR</td><td><a href="/x/{ex99}">{ex99}</a></td><td>EX-99.1</td><td>1</td></tr>'
    return f"<table>{rows}</table>"


class _SEC:
    async def get_company_tickers(self):
        return {"0": {"cik_str": int(CRWV), "ticker": "CRWV"}, "1": {"cik_str": int(ORCL), "ticker": "ORCL"},
                "2": {"cik_str": int(MSFT), "ticker": "MSFT"}, "3": {"cik_str": 5551212, "ticker": "OTCX"}}

    async def get_submissions(self, cik):
        return {
            CRWV: _subs("CoreWeave, Inc.", [("8-K", "2026-10-21", "1.01", ACC_C, "c.htm")], "7374"),
            ORCL: _subs("ORACLE CORP", [("8-K", "2026-10-20", "2.02,9.01", ACC_O, "o.htm")], "7372"),
            MSFT: _subs("MICROSOFT CORP", [], "7372"),
        }.get(cik, _subs("OTC THING", [], "9999"))

    async def get_filing_document(self, cik, accession, document):
        return {
            (ACC_C, "c.htm"): f"<p>{CRWV_SPAN}</p>", (ACC_C, f"{ACC_C}-index.htm"): _index(ACC_C, "c.htm"),
            (ACC_O, "o.htm"): "<p>See Exhibit 99.1.</p>", (ACC_O, "pr.htm"): f"<p>{ORCL_SPAN}</p>",
            (ACC_O, f"{ACC_O}-index.htm"): _index(ACC_O, "o.htm", "pr.htm"),
        }.get((accession, document))


class _LLM:
    def __init__(self):
        self.sent: List[str] = []

    async def generate_json(self, prompt, system_instruction=None, **kw):
        self.sent += [prompt, system_instruction or ""]
        if kw.get("usage_tag") == "learn_topic_label":
            return {"text": json.dumps({"label": "AI Cloud Capacity"}), "finish_reason": "STOP"}
        span = CRWV_SPAN if "CoreWeave" in prompt else ORCL_SPAN if "ORACLE" in prompt else None
        return {"text": json.dumps({"spans": [span] if span else []}), "finish_reason": "STOP"}


class _Supabase:
    def table(self, name):
        return self

    def select(self, *a):
        return self

    def eq(self, *a):
        return self

    def order(self, *a, **k):
        return self

    def execute(self):
        return SimpleNamespace(data=[{"slug": "ai-cloud", "title": "AI Cloud Builders", "is_active": True,
                                      "tickers": ["CRWV", "ORCL", "MSFT", "OTCX"]}])


def _leaks(text: str) -> List[str]:
    low = text.lower()
    return [s for s in SENTINELS if s.lower() in low]


@pytest.mark.asyncio
async def test_no_fmp_string_or_figure_reaches_a_signal_the_topic_or_a_prompt(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "SEC_EDGAR_USER_AGENT", "Caydex Test sentinel@example.com")
    monkeypatch.setattr(settings, "SEC_EDGAR_MAX_RPS", 1000.0)
    monkeypatch.setattr(sec_edgar, "_circuit_open_until", 0.0)
    monkeypatch.setattr(sec_edgar, "_next_request_at", 0.0)
    signals.clear_caches()
    sec_facts.clear_caches()
    topic.clear_caches()
    cands = tmp_path / "learn_candidates.json"
    cands.write_text(json.dumps({"version": 1, "tickers": ["CRWV"]}))
    monkeypatch.setattr(signals, "CANDIDATES_PATH", cands)
    monkeypatch.setattr(signals, "get_supabase", lambda: _Supabase())
    piece = {"id": "3f2b6a3e-5d4c-4a8b-9b1e-2c7d8e9f0a1b", "period_key": DD.period_key, "kind": "deep_dive",
             "status": "planned", "topic": None}
    stored = {}

    async def create_piece(*a, **k):
        return dict(piece)

    async def transition(piece_id, *, from_statuses, to_status, fields=None, fence=None, **k):
        ledger._validate_piece_fields(fields or {})
        stored.update(fields or {}, status=to_status)
        return {**piece, **stored}

    async def recent_subjects(*a, **k):
        return ledger.RecentSubjects(frozenset(), frozenset(), frozenset(), ())

    async def list_pieces(**k):
        return []

    async def lists():
        return SimpleNamespace(stock_sections=[SimpleNamespace(kind="most_added", items=[SimpleNamespace(symbol="ORCL")])])

    async def no_alert(*a, **k):
        raise AssertionError("the sentinel run must not skip")

    monkeypatch.setattr(ledger, "create_piece", create_piece)
    monkeypatch.setattr(ledger, "transition", transition)
    monkeypatch.setattr(ledger, "recent_subjects", recent_subjects)
    monkeypatch.setattr(ledger, "list_pieces", list_pieces)
    monkeypatch.setattr(topic, "_search_lists", lists)
    monkeypatch.setattr(topic, "_notify_terminal", no_alert)
    library = case_library.parse_library({"version": 1, "entries": [
        {"id": f"p{i}", "kind": "mechanics", "title": "t", "themes": ["ai-cloud"], "category_fit": ["blueprints"],
         "companies": [], "llm_eligible": True, "status": None,
         "facts": [{"id": "f1", "text": "A data centre needs power.", "source": {"title": "10-K", "url": "https://www.sec.gov/x"}}],
         "verified_by": "Owner", "verified_at": "2026-10-20"} for i in range(2)]})

    fmp, sec, llm = _FMP(), _SEC(), _LLM()
    # The signals themselves, straight from the door.
    tickers = await signals.universe(NOW, fmp=fmp)
    sigs = await signals.collect_signals(NOW, tickers, fmp=fmp, sec=sec)
    assert {s.ticker for s in sigs} >= {"CRWV", "ORCL", "MSFT", "OTCX"}
    for s in sigs:
        assert _leaks(repr(s)) == [], s
    # The whole step.
    signals.clear_caches()
    row = await topic.run_topic_step(DD, now=NOW, fmp=fmp, sec=sec, llm=llm, library=library)
    assert row is not None and row["status"] == "topic_pending"
    tp = row["topic"]
    assert tp["candidates"] and tp["candidates"][0]["peg_facts"]              # anti-vacuity
    assert fmp.calls["news"] and fmp.calls["profile"] and fmp.calls["calendar"]
    assert _leaks(json.dumps(tp)) == []
    assert llm.sent and all(_leaks(t) == [] for t in llm.sent), [ _leaks(t) for t in llm.sent ]
    otcx = next(s for s in sigs if s.ticker == "OTCX")
    assert otcx.size_ok is False                                             # read, decided, discarded
