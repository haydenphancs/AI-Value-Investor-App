"""The FMP-derived universe files must be sourceable from OUTSIDE the repo.

`benchmark_universe.json` (5,704 tickers with per-ticker market-cap floats) and
`industry_universe.json` (9,188) are built from FMP's `company-screener`. Market caps are
licensed CONTENT, so a public repo carrying them is redistribution under ToS §2.6.1 and the
remedy under §2.10 is key termination.

They can only leave the repo if the app can still get them, and if a failure to get them is
LOUD. All four readers degrade to `[]`, so a silent miss renders as "no data for this
industry" — indistinguishable from a real answer.

Hermetic: Supabase is stubbed throughout.
"""
from __future__ import annotations

import ast
import importlib
import inspect
import json
import logging
from pathlib import Path

import pytest

from app.services import universe_data as ud


@pytest.fixture(autouse=True)
def _clean(monkeypatch, tmp_path):
    ud.reset_cache_for_tests()
    monkeypatch.setenv(ud._ENV_DIR, str(tmp_path))
    yield
    ud.reset_cache_for_tests()


def _payload(n=2):
    return {
        "ticker_count": n,
        "industries": [
            {"industry": "Software", "sector": "Technology",
             "tickers": ["AAPL"], "market_caps": {"AAPL": 1.0}}
        ],
    }


# ── one resolver ─────────────────────────────────────────────────────────────

def test_every_reader_uses_the_shared_resolver():
    """There were FOUR path constructions for one directory — `parents[2]` in three
    services and `parents[3]` in the report collector (it sits a level deeper). Nothing
    shared a constant, so relocating the files meant finding each by hand.
    """
    backend = Path(__file__).resolve().parents[1]
    # `scripts/` too: two of them (`hydrate_hedge_fund_flow.py`, `verify_industry_dossier.py`)
    # kept a hand-built `parents[1] / "data" / …` path after the files left the repo, so the
    # hydrator silently lost its screener fallback and the verifier crashed. The two
    # BUILDERS that write the files are the only legitimate hardcoded paths.
    writers = {"build_benchmark_universe.py", "discover_industries.py"}
    offenders = []
    candidates = list((backend / "app").rglob("*.py")) + list((backend / "scripts").glob("*.py"))
    for path in candidates:
        roots = backend / "app" if "app" in path.parts[len(backend.parts):][:1] else backend / "scripts"
        if path.name == "universe_data.py" or path.name in writers:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:                       # pragma: no cover
            continue
        # Strip DOCSTRINGS as well as comments (ast drops comments by construction).
        # Three modules legitimately DISCUSS these filenames in prose — `admin.py`,
        # `sector_benchmark_service.py` and `competitor_intel_service.py` — and an
        # unstripped scan flags all three. Prose is not a hardcoded path.
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.ClassDef,
                                 ast.FunctionDef, ast.AsyncFunctionDef)):
                if (node.body and isinstance(node.body[0], ast.Expr)
                        and isinstance(node.body[0].value, ast.Constant)
                        and isinstance(node.body[0].value.value, str)):
                    node.body = node.body[1:] or [ast.Pass()]
        body = ast.unparse(tree)
        for name in ("benchmark_universe.json", "industry_universe.json"):
            if name in body and "universe_data" not in body:
                offenders.append(f"{path.relative_to(roots)} hardcodes {name}")
    assert offenders == [], offenders


@pytest.mark.parametrize("module,attr", [
    ("app.services.industry_benchmark_service", "load_universe"),
    ("app.services.industry_dossier_service", "load_universe"),
    ("app.services.industry_moat_benchmark_service", "load_universe"),
    ("app.services.agents.ticker_report_data_collector", "load_universe"),
])
def test_each_reader_imports_the_loader(module, attr):
    m = importlib.import_module(module)
    assert hasattr(m, attr), f"{module} no longer routes through the shared loader"


# ── the Storage fallback ─────────────────────────────────────────────────────

def test_a_local_miss_falls_back_to_storage(monkeypatch, tmp_path):
    blob = json.dumps(_payload()).encode()
    monkeypatch.setattr(ud, "_download_from_storage", lambda _f: blob)
    rows = ud.load_universe(ud.INDUSTRY_UNIVERSE)
    assert len(rows) == 1 and rows[0]["industry"] == "Software"


def test_a_download_is_still_usable_when_the_disk_write_fails(monkeypatch, tmp_path, caplog):
    """🔴 Railway containers can have a read-only filesystem. Making the return value depend
    on caching-to-disk would turn a perfectly good download into an EMPTY industry
    benchmark surface."""
    blob = json.dumps(_payload()).encode()

    class _SB:
        def storage(self): ...
    def _fake_supabase():
        class _S:
            def from_(self, _b):
                class _F:
                    def download(self, _n): return blob
                return _F()
        class _C:
            storage = _S()
        return _C()

    monkeypatch.setattr("app.database.get_supabase", _fake_supabase, raising=True)
    # Make every write fail.
    monkeypatch.setattr(ud.Path, "mkdir", lambda *a, **k: (_ for _ in ()).throw(PermissionError("ro")))

    with caplog.at_level(logging.WARNING, logger="app.services.universe_data"):
        rows = ud.load_universe(ud.INDUSTRY_UNIVERSE)
    assert len(rows) == 1, "a read-only filesystem emptied the universe"
    assert any("could not cache" in r.message for r in caplog.records)


def test_a_total_failure_is_empty_AND_logged_at_error(monkeypatch, caplog):
    """Stub at the SUPABASE boundary, not at `_download_from_storage` — the ERROR log lives
    inside the downloader, so stubbing it away would remove the very thing under test.
    (First version of this test did exactly that and asserted on an empty caplog.)"""
    def _boom():
        raise RuntimeError("storage unreachable")

    monkeypatch.setattr("app.database.get_supabase", _boom, raising=True)
    with caplog.at_level(logging.ERROR, logger="app.services.universe_data"):
        assert ud.load_universe(ud.INDUSTRY_UNIVERSE) == []
    assert any("could not fetch" in r.message for r in caplog.records), (
        "a missing universe degraded SILENTLY — the whole point of this module"
    )


def test_a_malformed_payload_is_empty_AND_logged(monkeypatch, tmp_path, caplog):
    (tmp_path / ud.INDUSTRY_UNIVERSE).write_text('{"industries": "not a list"}')
    with caplog.at_level(logging.ERROR, logger="app.services.universe_data"):
        assert ud.load_universe(ud.INDUSTRY_UNIVERSE) == []
    assert any("unreadable" in r.message for r in caplog.records)


def test_the_result_is_memoised(monkeypatch):
    calls = {"n": 0}

    def _dl(_f):
        calls["n"] += 1
        return json.dumps(_payload()).encode()

    monkeypatch.setattr(ud, "_download_from_storage", _dl)
    for _ in range(4):
        ud.load_universe(ud.INDUSTRY_UNIVERSE)
    assert calls["n"] == 1, "four services + a quarterly job read this; it must not refetch"


# ── the startup check ────────────────────────────────────────────────────────

def test_the_startup_check_reports_per_file(monkeypatch):
    monkeypatch.setattr(ud, "_download_from_storage", lambda f: (
        json.dumps(_payload()).encode() if f == ud.BENCHMARK_UNIVERSE else None
    ))
    out = ud.verify_universe_files_present()
    assert out == {ud.BENCHMARK_UNIVERSE: True, ud.INDUSTRY_UNIVERSE: False}


def test_the_startup_check_is_wired_into_the_lifespan():
    """A check nothing CALLS is not a check.

    ⚠️ Asserting the NAME appears is not enough — the import line alone satisfies that, so
    replacing the call with `pass` left this green. Look for an actual Call node.
    """
    from app import main as app_main

    tree = ast.parse(inspect.cleandoc(inspect.getsource(app_main.lifespan)))
    called = {
        node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", "")
        for node in ast.walk(tree) if isinstance(node, ast.Call)
    }
    # It runs via `asyncio.to_thread(verify_universe_files_present)`, so the function is an
    # ARGUMENT rather than the callee — look for it in either position.
    referenced_as_arg = any(
        isinstance(a, ast.Name) and a.id == "verify_universe_files_present"
        for node in ast.walk(tree) if isinstance(node, ast.Call) for a in node.args
    )
    assert "verify_universe_files_present" in called or referenced_as_arg, (
        "the startup universe check is imported but never invoked"
    )


def test_the_bucket_is_named_and_private_by_intent():
    assert ud.UNIVERSE_BUCKET == "universe-data"
    doc = ud.__doc__ or ""
    assert "private" in doc.lower() or "private" in (inspect.getsource(ud)[:3000].lower())


# ── the entitlement manifest must not point at a repo path any more ──────────

def test_the_sp500_substitution_no_longer_names_a_repo_path():
    """`fmp_entitlements` surfaces this string in the `FMPNotEntitledException` message. A
    hardcoded `backend/data/...` becomes a lie at the exact moment someone hits the 402."""
    from app.integrations.fmp_entitlements import SUBSTITUTION

    sub = SUBSTITUTION["sp500-constituent"]
    assert "backend/data/" not in sub, sub
    assert "universe_data" in sub or "benchmark_universe" in sub
