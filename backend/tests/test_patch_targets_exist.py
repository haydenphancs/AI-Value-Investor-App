"""A test may not patch a name that does not exist, nor leak a module global.

WHY THIS FILE EXISTS — a guard that patches nothing looks exactly like a guard that
works, and this suite shipped one.

`tests/test_negative_earnings_display.py` did

    monkeypatch.setattr(mod, "get_current_benchmarks", lambda *a, **k: {}, raising=False)

but `get_current_benchmarks` exists only as a METHOD on `SectorBenchmarkLookup` — never
as a module-level name. Production calls
`get_sector_benchmark_lookup().get_current_benchmark_values(...)`. With `raising=False`
that is a silent no-op, so every test in the file ran against the live Supabase-backed
lookup instead of the stub, and passed. Verified at the time by spying on the real
symbol and watching it get invoked.

Plain `monkeypatch.setattr` and plain `mock.patch` already raise `AttributeError` on a
missing target, so they need no guard. The three shapes below are the ones with NO
built-in protection:

  1. `monkeypatch.setattr(<mod>, "<name>", ..., raising=False)`
  2. `patch(...)` / `patch.object(...)` with `create=True`
  3. a bare assignment `<module_alias>.<attr> = ...` — which additionally never restores,
     so it leaks into every later test in the session. Two files did this:
     `test_supabase_client_isolation.py` poisoned `app.database` for the 58 test files
     that sort after it, and `test_home_dashboard_watchlist.py` permanently rebound three
     names on `home_dashboard_service`. Both are fixed; the allow-list below is EMPTY so
     it stays that way.

Scans `tests/` — the 16 existing AST scans in this suite all target `app/` or the Swift
tree, so nothing looked at the tests themselves. Structure follows
`test_settings_attribute_parity.py`: walk, accumulate into `missing`, fail ONCE with
every violation listed. Anything not statically resolvable is SKIPPED rather than
guessed at — a false positive here would train people to weaken the scan.
"""

import ast
import importlib
from pathlib import Path

import pytest

TESTS_ROOT = Path(__file__).resolve().parent

# Deliberate exceptions: a patch that legitimately CREATES an attribute, or an assignment
# that legitimately leaks. Add an entry with a comment explaining why, rather than
# weakening the scan. Empty is the correct baseline and was achievable — keep it that way.
_ALLOWED: set[tuple[str, str]] = set()


def _module_alias_map(tree: ast.Module) -> dict[str, str]:
    """`import app.x.y as z` / `import app.x` → {alias: dotted module}, module scope and
    function scope alike (test helpers import inside the function constantly)."""
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if not a.name.startswith("app"):
                    continue
                aliases[a.asname or a.name.split(".")[0]] = a.name
    return aliases


def _const_str(node) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _resolve(dotted: str):
    try:
        return importlib.import_module(dotted)
    except Exception:
        return None


def _restored_attrs(tree: ast.Module) -> set[str]:
    """Attribute names the file puts back.

    Two shapes count: a `finally:` (or any later) assignment of the same attribute — the
    `original = ...` / `try` / `finally: mod.x = original` idiom already used in this
    suite — and an autouse fixture that snapshots with `getattr` and restores with
    `setattr`, which is how a helper called from 21 places is made safe without
    threading `monkeypatch` through all of them.
    """
    counts: dict[str, int] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Attribute):
                    counts[tgt.attr] = counts.get(tgt.attr, 0) + 1
    restored = {name for name, n in counts.items() if n > 1}

    # `setattr(mod, name, saved)` inside a fixture restores whatever it iterates.
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "setattr"):
            for other in ast.walk(tree):
                if (isinstance(other, ast.Call) and isinstance(other.func, ast.Name)
                        and other.func.id == "getattr"):
                    return restored | _names_in(tree)
    return restored


def _names_in(tree: ast.Module) -> set[str]:
    """String literals in a `names = (...)` tuple — the snapshot/restore list."""
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.value, (ast.Tuple, ast.List)):
            for elt in node.value.elts:
                if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                    out.add(elt.value)
    return out


def _scan_file(path: Path, missing: dict[str, list[str]], leaks: list[str]) -> None:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError as e:
        pytest.fail(f"{path.name} does not parse: {e}")

    aliases = _module_alias_map(tree)
    rel = path.name

    for node in ast.walk(tree):
        # ── 1 + 2: patches that suppress the missing-attribute error ────────
        if isinstance(node, ast.Call):
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")

            suppressed = any(
                kw.arg in ("raising", "create")
                and isinstance(kw.value, ast.Constant)
                # raising=False suppresses; create=True suppresses.
                and kw.value.value is (False if kw.arg == "raising" else True)
                for kw in node.keywords
            )
            # monkeypatch.setattr's 4th POSITIONAL arg is `raising`.
            if name == "setattr" and len(node.args) >= 4:
                if isinstance(node.args[3], ast.Constant) and node.args[3].value is False:
                    suppressed = True

            if not suppressed or name not in ("setattr", "patch", "object", "delattr"):
                continue
            if len(node.args) < 2:
                continue

            target, attr = node.args[0], _const_str(node.args[1])
            if attr is None or not isinstance(target, ast.Name):
                continue                       # not statically resolvable → skip
            dotted = aliases.get(target.id)
            if dotted is None:
                continue
            mod = _resolve(dotted)
            if mod is None:
                continue
            if not hasattr(mod, attr) and (rel, attr) not in _ALLOWED:
                missing.setdefault(f"{dotted}.{attr}", []).append(
                    f"{rel}:{node.lineno}"
                )

    # ── 3: bare `alias.attr = ...` that rebinds a FUNCTION and never restores it ──
    #
    # Two deliberate narrowings, because a scan with false positives trains people to
    # weaken it:
    #
    #  * CALLABLES only. Rebinding module-level mutable state (`_AGENT_SEMAPHORE`,
    #    `_INFLIGHT_REPORTS`, `_WARM_SEMAPHORE`, a timeout int) is the idiomatic
    #    reset-between-tests here and every consumer re-initialises it lazily. Rebinding
    #    a FUNCTION — `get_supabase`, `get_active_group`, a service factory — is what
    #    silently changes behaviour for every later test.
    #  * RESTORED assignments are fine. If the same attribute is assigned again anywhere
    #    in the file (a `try/finally` restore) or the file installs an autouse
    #    snapshot/restore fixture, the state does not escape.
    restored = _restored_attrs(tree)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        for tgt in node.targets:
            if not (isinstance(tgt, ast.Attribute) and isinstance(tgt.value, ast.Name)):
                continue
            dotted = aliases.get(tgt.value.id)
            if dotted is None or (rel, tgt.attr) in _ALLOWED:
                continue
            if tgt.attr in restored:
                continue
            mod = _resolve(dotted)
            if mod is None or not callable(getattr(mod, tgt.attr, None)):
                continue
            leaks.append(f"{rel}:{node.lineno}  {tgt.value.id}.{tgt.attr}")


def _scan_all():
    missing: dict[str, list[str]] = {}
    leaks: list[str] = []
    for path in sorted(TESTS_ROOT.rglob("test_*.py")):
        _scan_file(path, missing, leaks)
    return missing, leaks


def test_no_patch_targets_a_nonexistent_attribute():
    missing, _ = _scan_all()
    assert not missing, (
        "these patches suppress the missing-attribute error AND the attribute does not "
        "exist, so they are SILENT NO-OPS and the test runs against the real thing:\n"
        + "\n".join(f"  {name}  ({', '.join(sites)})" for name, sites in sorted(missing.items()))
    )


def test_no_test_leaks_a_module_global():
    _, leaks = _scan_all()
    assert not leaks, (
        "these assign to an `app.*` module attribute without monkeypatch, so the stub "
        "leaks into every test that runs after them (and single-file runs then differ "
        "from full-suite runs). Use `monkeypatch.setattr`, or an autouse "
        "snapshot/restore fixture:\n" + "\n".join(f"  {leak}" for leak in sorted(leaks))
    )


def test_the_scan_finds_the_constructs_it_claims_to_guard():
    """Anti-vacuity. Both tests above pass trivially if the walk matches nothing — which
    is the exact failure mode this file exists to prevent. Parse a known-bad snippet and
    assert each detector fires."""
    import tempfile

    # NOTE the shape: the leak line must rebind a name that IS an existing CALLABLE on
    # the module (`get_supabase`), because the detector deliberately ignores both
    # non-callable module state and names that do not exist. An earlier version of this
    # snippet assigned a made-up attribute and silently stopped exercising the detector
    # the moment it was narrowed — which is precisely the failure this test guards.
    bad = '''
import app.database as db

def test_x(monkeypatch):
    monkeypatch.setattr(db, "definitely_not_a_real_name", 1, raising=False)

def test_y():
    db.get_supabase = lambda: None
'''
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "test_bad.py"
        p.write_text(bad)
        missing: dict[str, list[str]] = {}
        leaks: list[str] = []
        _scan_file(p, missing, leaks)

    assert "app.database.definitely_not_a_real_name" in missing, (
        "the raising=False detector did not fire on a known-bad file"
    )
    assert any("get_supabase" in leak for leak in leaks), (
        "the module-global leak detector did not fire on a known-bad file"
    )
