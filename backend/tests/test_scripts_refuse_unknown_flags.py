"""A script that reads flags by hand must refuse a flag it does not know.

`seed_money_moves.py` tested `"--dry-run" in sys.argv`, so on 2026-11-05 `--help` was ignored and the
script ran a REAL seed against production while being read for its usage. A typo'd `--dryrun` would
have done the same. `scripts/_cli_flags.py` is the fix; this file proves it, and fails the build on a
new script that reads `sys.argv` flags without it.

Nothing here runs one of those scripts. A subprocess check would run the real seed the day the guard
regressed, so the wiring is proved from the AST instead.
"""

import ast
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from _cli_flags import check_flags  # noqa: E402

#: Every production writer in this set was exposed by the same pattern, so the scan below must see them.
_MUST_BE_FOUND = {"seed_money_moves", "seed_journey", "seed_book_audio", "seed_book_covers"}


def _is_sys_argv(node) -> bool:
    return (isinstance(node, ast.Attribute) and node.attr == "argv"
            and isinstance(node.value, ast.Name) and node.value.id == "sys")


def _flag_constants(tree) -> set:
    """Every "--x" this module compares an argument with, or looks up in sys.argv."""
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Compare):
            parts = [node.left, *node.comparators]
        elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "index"
              and _is_sys_argv(node.func.value)):
            parts = node.args
        else:
            continue
        found |= {p.value for p in parts
                  if isinstance(p, ast.Constant) and isinstance(p.value, str) and p.value.startswith("--")}
    return found


def _is_main_guard(node) -> bool:
    return (isinstance(node, ast.If) and isinstance(node.test, ast.Compare)
            and isinstance(node.test.left, ast.Name) and node.test.left.id == "__name__")


def _first_call_under_main(tree):
    """(call, statements before it) for the check_flags call in the `__main__` block, else (None, [])."""
    for node in tree.body:
        if not _is_main_guard(node):
            continue
        for i, stmt in enumerate(node.body):
            if (isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call)
                    and getattr(stmt.value.func, "id", "") == "check_flags"):
                return stmt.value, node.body[:i]
    return None, []


def _flag_readers():
    out = {}
    for path in sorted(SCRIPTS.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imports_argparse = any(
            (isinstance(n, ast.Import) and any(a.name == "argparse" for a in n.names))
            or (isinstance(n, ast.ImportFrom) and n.module == "argparse") for n in ast.walk(tree))
        flags = _flag_constants(tree)
        if flags and not imports_argparse:
            out[path.stem] = (tree, flags)
    return out


def test_the_scan_sees_the_scripts_that_write_production():
    """Without this a broken scan would pass by finding nothing."""
    assert _MUST_BE_FOUND <= set(_flag_readers())


@pytest.mark.parametrize("name", sorted(_flag_readers()))
def test_a_hand_rolled_flag_reader_refuses_unknown_flags_before_it_does_anything(name):
    tree, flags = _flag_readers()[name]
    call, before = _first_call_under_main(tree)
    assert call is not None, f"{name}: call check_flags(sys.argv, known=..., doc=__doc__) under __main__"
    assert all(isinstance(s, (ast.Import, ast.ImportFrom)) for s in before), \
        f"{name}: check_flags must run before anything else in the __main__ block"
    assert _is_sys_argv(call.args[0]), f"{name}: check_flags must read sys.argv"
    known = next(k.value for k in call.keywords if k.arg == "known")
    assert isinstance(known, ast.Set), f"{name}: known= must be a literal set"
    listed = {e.value for e in known.elts}
    assert flags <= listed, f"{name}: reads {sorted(flags - listed)} but does not list them, so they are refused"


def test_help_prints_the_doc_and_stops(capsys):
    with pytest.raises(SystemExit) as exc:
        check_flags(["x.py", "--help"], known={"--dry-run"}, doc="Usage: x")
    assert exc.value.code == 0
    assert "Usage: x" in capsys.readouterr().out


def test_help_wins_over_an_unknown_flag():
    with pytest.raises(SystemExit) as exc:
        check_flags(["x.py", "--nope", "-h"], known=set())
    assert exc.value.code == 0


@pytest.mark.parametrize("flag", ["--dryrun", "--dry_run", "-n", "--DRY-RUN", "--force=1"])
def test_a_near_miss_never_runs(flag, capsys):
    with pytest.raises(SystemExit) as exc:
        check_flags(["x.py", flag], known={"--dry-run", "--force"})
    assert exc.value.code == 2
    assert "Nothing was run" in capsys.readouterr().err


@pytest.mark.parametrize("argv", [["x.py"], ["x.py", "--dry-run"], ["x.py", "--force", "12"],
                                  ["x.py", "some-slug", "--dry-run"]])
def test_a_clean_command_passes(argv):
    assert check_flags(argv, known={"--dry-run", "--force"}) is None
