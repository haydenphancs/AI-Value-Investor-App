"""No module in `app/` or `scripts/` may reference a global that is never bound.

WHY THIS EXISTS — a real production incident, 2026-08-21.

A refactor moved the congressional hashing helpers out of `scripts/hydrate_whales.py`
into `app/services/_whale_common.py`. Two module-level constants sat on the lines
immediately after the moved block:

    GEMINI_SEMAPHORE = asyncio.Semaphore(3)
    FMP_BATCH_SIZE = 30

The deletion range ran two lines too far and took both with it, while leaving six call
sites behind. Nothing caught it:

  * `ast.parse` (the repo's PostToolUse Python hook) only checks SYNTAX — an undefined
    global is syntactically perfect.
  * importing the module does not fail either, because both names are referenced only
    INSIDE function bodies, where resolution is deferred to call time.
  * the 6,500-test suite stayed green, because no test exercised those two code paths.

So it shipped, and `FMP_BATCH_SIZE` surfaced in Sentry as
`NameError: name 'FMP_BATCH_SIZE' is not defined` — "Politician hydration failed for
Gilbert Cisneros" — with `GEMINI_SEMAPHORE` sitting behind it as the same bug on a path
that simply had not run yet.

That is the gap this test closes: the failure mode is a *runtime* NameError in a function
nobody covers, and the only cheap way to see it is static scope analysis.

Deliberately uses stdlib `symtable` rather than pyflakes/flake8 — neither is a declared
dependency of this project, and a guard that silently skips when its linter is absent is
worse than no guard at all. `symtable` performs real scope analysis (module vs function
vs class, global vs local), so it does not confuse a local shadow for a missing global.
Measured across all 263 files in `app/` + `scripts/`: zero false positives.
"""

from __future__ import annotations

import builtins
import pathlib
import symtable

import pytest

_BACKEND = pathlib.Path(__file__).resolve().parents[1]
_ROOTS = ("app", "scripts")

# Names the interpreter injects into every module namespace. `symtable` does not know
# about them, so without this they read as undefined.
_MODULE_DUNDERS = {
    "__file__", "__name__", "__doc__", "__package__",
    "__spec__", "__loader__", "__builtins__", "__debug__",
}


def _undefined_globals(path: pathlib.Path) -> list[tuple[str, str]]:
    """Return `(scope_name, symbol)` for every global referenced but never bound.

    A name counts as BOUND when the module assigns it, imports it, or declares it as a
    function/class (`is_namespace`). Anything referenced as a global and bound nowhere
    can only raise `NameError` if its line is ever reached.
    """
    source = path.read_text(encoding="utf-8", errors="replace")
    try:
        top = symtable.symtable(source, str(path), "exec")
    except SyntaxError:
        # Syntax is already guarded by the PostToolUse hook and by import-time failures;
        # this test is only about scope resolution.
        return []

    bound = set(dir(builtins)) | _MODULE_DUNDERS
    for sym in top.get_symbols():
        if sym.is_assigned() or sym.is_imported() or sym.is_namespace():
            bound.add(sym.get_name())

    missing: list[tuple[str, str]] = []

    def walk(table: symtable.SymbolTable) -> None:
        for sym in table.get_symbols():
            # `is_assigned()` guards the `global X; X = ...` pattern, where the name is
            # legitimately created from inside a function.
            if sym.is_global() and not sym.is_assigned():
                if sym.get_name() not in bound:
                    missing.append((table.get_name(), sym.get_name()))
        for child in table.get_children():
            walk(child)

    walk(top)
    return sorted(set(missing))


def _python_files() -> list[pathlib.Path]:
    files: list[pathlib.Path] = []
    for root in _ROOTS:
        files.extend(sorted((_BACKEND / root).rglob("*.py")))
    return files


def test_the_scan_actually_covers_the_backend():
    """Anti-vacuity control #1 — a glob that silently matches nothing passes everything."""
    files = _python_files()
    assert len(files) > 200, f"expected the full backend, found {len(files)} files"
    assert any(f.name == "hydrate_whales.py" for f in files), \
        "the module that caused the incident is not even being scanned"


@pytest.mark.parametrize("path", _python_files(), ids=lambda p: p.name)
def test_module_references_no_undefined_global(path: pathlib.Path):
    missing = _undefined_globals(path)
    assert not missing, (
        f"{path.relative_to(_BACKEND)} references global(s) that are never bound — "
        "this is a runtime NameError waiting for the path to be reached, and neither "
        "`ast.parse` nor importing the module will catch it:\n"
        + "\n".join(f"    {name!r} referenced in {scope}()" for scope, name in missing)
    )


def test_the_checker_catches_the_incident_it_was_written_for():
    """Anti-vacuity control #2 — mutation-test, in-process.

    Re-creates the exact deletion that shipped: drop the two constants from
    `hydrate_whales.py` and confirm the checker names all four call sites, including
    `_enrich_logos`, which is the frame Sentry reported.
    """
    target = _BACKEND / "scripts" / "hydrate_whales.py"
    source = target.read_text()

    broken = source.replace(
        "GEMINI_SEMAPHORE = asyncio.Semaphore(3)\nFMP_BATCH_SIZE = 30\n", "", 1
    )
    assert broken != source, (
        "the constants this test re-deletes are no longer present verbatim — update the "
        "mutation, do not delete the control"
    )

    tmp = target.with_suffix(".mutation-probe")
    try:
        tmp.write_text(broken)
        found = _undefined_globals(tmp)
    finally:
        tmp.unlink(missing_ok=True)

    names = {name for _scope, name in found}
    scopes = {scope for scope, _name in found}
    assert "FMP_BATCH_SIZE" in names and "GEMINI_SEMAPHORE" in names, (
        f"the checker missed the very bug it exists to catch: {found}"
    )
    assert "_enrich_logos" in scopes, (
        "the checker did not flag `_enrich_logos`, the frame Sentry reported"
    )
