"""
No single Swift expression may be a giant literal of initializer calls.

WHY THIS EXISTS — it kernel-panicked the development Mac. Two GENERATED tables were each one
dictionary literal: `ReadAlongBlock.byBook` (Models/BookReadAlong.swift, ~4,300 initializer calls)
and `CoreChapterContent.booksByOrder` (Models/BooksContent.swift, ~1,300). The Swift type checker's
memory on such a literal grows roughly QUADRATICALLY (measured 2026-09-25 on truncated copies:
~290 calls ≈ nothing, ~830 calls ≈ 2 GB, ~2,000 calls > 6.4 GB within 3 s), and the emit-module job
type-checks every stored-property initializer in the module. So each build that re-emitted the
module pushed swift-frontend to tens of GB; a `generic/platform=iOS Simulator` build runs arm64 AND
x86_64 at once, and two ~40 GB frontends exhausted the memory compressor — WindowServer watchdog
panic 2026-09-25 07:00, jetsam 2026-09-24 22:06. A stack sample showed
typeCheckPatternBinding → ConstraintSystem::solve → finalize(); `-solver-expression-time-threshold=1`
named exactly those two lines.

The fix (backend/scripts/gen_book_read_along.py, gen_books_swift.py) builds each table in a private
function, ONE STATEMENT PER CORE. The real emit-module job then peaks at ~0.5 GB in ~11 s.

Two complementary measurements, the worse one counts:
  * per EXPRESSION — from an `=`, a `return`, or an implicit-return `{ [` to the end of the
    statement (catches `a = Foo(…) + Bar(…) + …` on one line);
  * per outermost `(`/`[` GROUP, wherever it sits (catches a giant literal passed as an argument,
    `append(contentsOf: [...])`, an implicit return).
Known blind spot: a multi-line `+` chain of separate small literals.

The limit is set far under the danger zone and above everything legitimate (largest today: ~135).
If this fails on a new table, split it the same way — do not raise the limit to get a build through.

Pure source scan: no compiler, no network, no writes.
"""
from __future__ import annotations

import ast
import bisect
import re
import subprocess
import sys
from pathlib import Path

import pytest

_IOS = Path(__file__).resolve().parents[2] / "frontend" / "ios"
_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"

# Initializer calls allowed in ONE expression. Danger measured at ~830 (2 GB) and growing ~n².
MAX_CALLS_PER_EXPRESSION = 300

_CALL = re.compile(r"\b[A-Z][A-Za-z0-9_]*\(")
_START = re.compile(r"(?<![=!<>])=(?![=>])|\breturn\b|\{\s*\n\s*(?=\[)")
_OPEN, _CLOSE = "([{", ")]}"


def _blank(src: str) -> str:
    """Blank comments and string-literal CONTENTS (length and newlines kept), so a `Foo(` or a
    bracket inside prose — these tables are mostly prose — is never counted.

    A small Swift lexer, not a regex: raw strings (`#"raw"#`, also in their triple-quoted
    multi-line form), nested `/* /* */ */` comments, and `\\(interpolation)` whose CODE (which may
    itself contain quoted strings) stays visible. A lexer that loses track of one string would
    hide — or invent — a whole literal."""
    out = list(src)
    n = len(src)

    def wipe(a: int, b: int) -> None:
        for k in range(a, b):
            if out[k] != "\n":
                out[k] = " "

    def string_at(i: int):
        j = i
        while j < n and src[j] == "#":
            j += 1
        return (j - i, j) if j < n and src[j] == '"' else None

    def skip_string(hashes: int, q0: int) -> int:
        triple = src.startswith('"""', q0)
        quote = '"""' if triple else '"'
        closing, escape = quote + "#" * hashes, "\\" + "#" * hashes
        seg = j = q0 + len(quote)
        while j < n:
            if src.startswith(escape, j):
                k = j + len(escape)
                if k < n and src[k] == "(":
                    wipe(seg, j)
                    j = seg = skip_code(k + 1, in_interpolation=True)
                    continue
                j = k + 1
                continue
            if src.startswith(closing, j):
                wipe(seg, j)
                return j + len(closing)
            if not triple and src[j] == "\n":      # unterminated single-line string
                wipe(seg, j)
                return j
            j += 1
        wipe(seg, n)
        return n

    def skip_code(i: int, in_interpolation: bool = False) -> int:
        depth = 0
        while i < n:
            if src.startswith("//", i):
                e = src.find("\n", i)
                e = n if e < 0 else e
                wipe(i, e)
                i = e
                continue
            if src.startswith("/*", i):
                d, e = 1, i + 2
                while e < n and d:
                    if src.startswith("/*", e):
                        d, e = d + 1, e + 2
                    elif src.startswith("*/", e):
                        d, e = d - 1, e + 2
                    else:
                        e += 1
                wipe(i, e)
                i = e
                continue
            if src[i] in '#"':
                s = string_at(i)
                if s:
                    i = skip_string(*s)
                    continue
            if in_interpolation:
                if src[i] == "(":
                    depth += 1
                elif src[i] == ")":
                    if depth == 0:
                        return i + 1
                    depth -= 1
            i += 1
        return n

    skip_code(0)
    return "".join(out)


def _line_of(b: str):
    starts = [0] + [m.end() for m in re.finditer("\n", b)]
    return lambda pos: bisect.bisect_right(starts, pos)


def _expressions(b: str):
    """(pos, calls) per statement-anchored expression, bracket-bounded to the first newline at
    depth 0 (or the bracket closing its enclosing scope)."""
    n = len(b)
    for m in _START.finditer(b):
        i, depth = m.end(), 0
        while i < n:
            c = b[i]
            if c in _OPEN:
                depth += 1
            elif c in _CLOSE:
                depth -= 1
                if depth < 0:
                    break
            elif c == "\n" and depth == 0 and b[m.end():i].strip():
                break
            i += 1
        yield m.start(), len(_CALL.findall(b, m.end(), i))


def _groups(b: str):
    """(pos, calls) per outermost `(`/`[` group — braces are transparent, so a closure passed
    INSIDE a call's parentheses counts toward that call."""
    depth, start = 0, 0
    for i, c in enumerate(b):
        if c in "([":
            if depth == 0:
                start = i
            depth += 1
        elif c in ")]" and depth:
            depth -= 1
            if depth == 0:
                yield start, len(_CALL.findall(b, start, i + 1))


def _worst(src: str) -> tuple[int, int]:
    """(max calls in one expression, its 1-based line)."""
    b = _blank(src)
    line = _line_of(b)
    best = max(list(_expressions(b)) + list(_groups(b)), key=lambda t: t[1], default=(0, 0))
    return best[1], (line(best[0]) if best[1] else 0)


def test_no_swift_expression_is_a_giant_literal():
    swift = sorted(_IOS.rglob("*.swift"))
    assert len(swift) > 400, f"scanned only {len(swift)} Swift files — wrong root?"
    offenders = []
    for path in swift:
        calls, line = _worst(path.read_text(encoding="utf-8"))
        if calls > MAX_CALLS_PER_EXPRESSION:
            offenders.append(f"{path.relative_to(_IOS)}:{line} ({calls} initializer calls)")
    assert not offenders, (
        f"expression(s) over {MAX_CALLS_PER_EXPRESSION} initializer calls — the type checker's "
        "memory is ~quadratic in that and this shape panicked the Mac on 2026-09-25. Build the "
        "table one statement per chunk inside a function (see ReadAlongBlock.makeByBook()): "
        + "; ".join(offenders)
    )


# One-call rows whose STRINGS would fool a naive scanner: a `Fake(` and a `[` inside a string
# with an interpolation; a raw string holding quotes and a paren; an interpolation whose code
# contains a quoted string with a `)` in it (Swift: `"n=\(f("x)"))"`).
_PLAIN_ROW = '        Row(title: "Fake(\\(x)) [", n: {i}),'
_RAW_ROW = '        Row(title: #"a "quoted" (paren"#, n: {i}),'
_INTERP_ROW = '        Row(title: "n=\\(f("x)"))", n: {i}),'


def _rows(n: int, row: str = _PLAIN_ROW) -> str:
    return "\n".join(row.format(i=i) for i in range(n))


def test_the_scanner_flags_the_shape_that_panicked():
    """Not vacuous: every way of writing one giant literal is caught; prose inside strings and
    comments is not counted; the per-statement shape passes."""
    over = MAX_CALLS_PER_EXPRESSION + 1
    rows, raw_rows, interp_rows = _rows(over), _rows(over, _RAW_ROW), _rows(over, _INTERP_ROW)
    shapes = {
        "stored property": f"struct T {{\n    static let table: [Int: Row] = [\n{rows}\n    ]\n}}\n",
        "= on its own line": f"let table: [Row] =\n    [\n{rows}\n    ]\n",
        "return": f"func make() -> [Row] {{\n    return [\n{rows}\n    ]\n}}\n",
        "implicit return": f"var t: [Row] {{\n    [\n{rows}\n    ]\n}}\n",
        "call argument": f"func f() {{\n    rows.append(contentsOf: [\n{rows}\n    ])\n}}\n",
        "raw-string rows": f"let t = [\n{raw_rows}\n]\n",
        "interpolation rows": f"let t = [\n{interp_rows}\n]\n",
    }
    for name, src in shapes.items():
        assert _worst(src)[0] >= over, f"{name}: giant literal not counted ({_worst(src)[0]})"

    assert _worst(f"let t = [\n{_rows(10)}\n]\n")[0] == 10     # `Fake(` / `[` inside strings don't count
    commented = ("let a = Row(n: 1) // " + " ".join(["Row(1)"] * 900) + "\n/* /* nested */ "
                 + " ".join(["Row(2)"] * 900) + " */\n")
    assert _worst(commented)[0] == 1
    multiline_string = 'let s = """\n' + "\n".join(["Row(3) [ ( \" x"] * 900) + '\n"""\nlet b = Row(n: 2)\n'
    assert _worst(multiline_string)[0] == 1
    split = "func make() {\n" + "\n".join(f"    t[{i}] = Row(n: {i})" for i in range(900)) + "\n}\n"
    assert _worst(split)[0] == 1


def _code(path: Path) -> str:
    return _blank(path.read_text(encoding="utf-8"))


def test_the_generated_book_tables_are_built_per_core():
    read_along = _code(_IOS / "ios" / "Models" / "BookReadAlong.swift")
    assert re.search(r"static let byBook: \[Int: \[Int: \[ReadAlongBlock\]\]\] = makeByBook\(\)", read_along)
    assert re.search(r"private static func makeByBook\(\)", read_along)
    assert re.search(r"^        cores\[\d+\] = \[$", read_along, re.M)

    books = _code(_IOS / "ios" / "Models" / "BooksContent.swift")
    assert re.search(r"static let booksByOrder: \[Int: \[Int: CoreChapterContent\]\] = makeBooksByOrder\(\)", books)
    assert re.search(r"private static func makeBooksByOrder\(\)", books)
    assert re.search(r"^        cores\[\d+\] = CoreChapterContent\($", books, re.M)


def test_the_generators_emit_the_per_core_shape():
    """The Swift files are regenerated from source — pin the generators too, or the next
    regeneration silently restores the giant literal."""
    read_along = (_SCRIPTS / "gen_book_read_along.py").read_text(encoding="utf-8")
    assert "= makeByBook()" in read_along
    assert 'f"        cores[{num}] = ["' in read_along
    assert "[ReadAlongBlock]]] = [\n{body}" not in read_along

    books = (_SCRIPTS / "gen_books_swift.py").read_text(encoding="utf-8")
    assert "= makeBooksByOrder()" in books
    assert 'f"        cores[{num}] = CoreChapterContent("' in books
    assert "CoreChapterContent]] = [\n" not in books


_WRITERS = {"write_text", "write_bytes", "write", "mkdir", "unlink", "rename", "replace", "touch", "print"}


def _module_level_actions(source: str) -> list[str]:
    """Statements that DO something when the module is imported. Allowed at module level: the
    docstring, imports (also inside a `with` that only imports), `sys.path.insert(...)`, defs,
    constant assignments and the `if __name__ == "__main__":` guard."""
    tree = ast.parse(source)
    defined = {n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}
    offenders = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Import, ast.ImportFrom)):
            continue
        if isinstance(node, ast.If) and "__main__" in ast.unparse(node.test):
            continue
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
            continue                                                    # docstring
        if isinstance(node, ast.Expr) and ast.unparse(node.value).startswith("sys.path.insert("):
            continue
        if isinstance(node, ast.With) and all(isinstance(b, (ast.Import, ast.ImportFrom)) for b in node.body):
            continue
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            offenders.append(f"line {node.lineno}: module-level {type(node).__name__}: {ast.unparse(node)[:60]}")
            continue
        for call in (c for c in ast.walk(node) if isinstance(c, ast.Call)):
            name = call.func.attr if isinstance(call.func, ast.Attribute) else getattr(call.func, "id", "")
            if name in _WRITERS or name in defined:
                offenders.append(f"line {node.lineno}: module-level call to {name}()")
    return offenders


@pytest.mark.parametrize("script", ["gen_books_swift.py", "gen_book_read_along.py"])
def test_importing_a_book_generator_does_nothing(script):
    """Six scripts — and, via gen_book_read_along, test_book_read_along_blocks.py — import
    gen_books_swift only for BD / BOOKS / core_num / parse_core. Its generation used to run at
    module level, so every pytest run REWROTE frontend/ios/ios/Models/BooksContent.swift (and could
    race a running Xcode build). Module level may define things; only main() may act — and a bare
    `main()` left behind after deleting the guard counts as acting."""
    offenders = _module_level_actions((_SCRIPTS / script).read_text(encoding="utf-8"))
    assert not offenders, f"{script} acts at import: " + "; ".join(offenders)


def test_the_import_purity_check_is_not_vacuous():
    src = (_SCRIPTS / "gen_books_swift.py").read_text(encoding="utf-8")
    guard = 'if __name__ == "__main__":\n    main()\n'
    assert guard in src
    assert _module_level_actions(src.replace(guard, "main()\n")), "a bare main() must be flagged"
    assert _module_level_actions(src + "\nfor b in BOOKS:\n    OUT.write_text('x')\n"), "a module-level loop must be flagged"
    assert _module_level_actions(src + "\nX = OUT.write_text('x')\n"), "a writer call must be flagged"


def test_importing_gen_books_swift_writes_no_file(tmp_path):
    """Behavioural twin of the AST check: import it for real against an EMPTY root and require
    that the root stays empty (its OUT path hangs off AI_INVESTOR_ROOT)."""
    env = {"PATH": "/usr/bin:/bin", "AI_INVESTOR_ROOT": str(tmp_path)}
    r = subprocess.run([sys.executable, "-c", "import gen_books_swift"], cwd=_SCRIPTS, env=env,
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    assert r.stdout == "", f"importing printed output: {r.stdout[:200]!r}"
    assert not any(tmp_path.rglob("*")), f"importing wrote {sorted(tmp_path.rglob('*'))}"
