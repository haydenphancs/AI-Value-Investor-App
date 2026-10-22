"""The ONE reader of the SQL migrations for source-scan guards: a function's LATEST definition.

Not a test module (no `test_` prefix, like `_price_fakes.py`): the SQL-text guards in
test_credit_paid_periods, test_monthly_expired_ledger_row, test_credit_review_fixes and
test_iap_entitlement import it, so they all read the same live body the same way.

Why it exists (IAP audit F19/F20, 2026-10-21). Each guard used to carry its own reader, and they
disagreed:
  * test_credit_paid_periods._latest_body matched only `CREATE OR REPLACE` and stripped only `--`.
    A `DROP FUNCTION ...; CREATE FUNCTION` — the shape a return-type change forces; 142 did it to
    refund_credits — was invisible to all fifteen 198 guards built on it. It was already wrong:
    it returned 139's superseded refund_credits, not the live 142.
  * K82's reader (test_monthly_expired_ledger_row) saw both spellings but removed `/* */` BEFORE
    `--` and literals, so a `/*` inside a `--` line (110, 157, 163, 173: `/research/*`,
    `/admin/*`, ...) or a '...' literal (113's `'... /api/v1/admin/*. ...'`) would pair with any
    later `*/` and delete every definition in between.
  * Others opened 112 / 100 / 101 by NUMBER, so they pinned bodies that no longer run.

How it reads:
  * Comments go in ONE left-to-right pass, so whichever construct opens first wins, as in the
    Postgres lexer: a `/*` inside a `--` line or a '...' literal opens nothing, a `--` inside a
    literal is text, and `/* */` nests.
  * A dollar-quoted region ($$ ... $$, $fn$ ... $fn$) ends at the first copy of its own tag, as
    in Postgres, and its CONTENT is stripped the same way: a function body is code, so its own
    comments go too. Recursing per region also bounds a stray apostrophe in dollar-quoted prose
    (a `COMMENT ... IS $c$it's$c$`) to that region instead of desynchronising the rest of the file.
  * The latest definition is the last `CREATE [OR REPLACE] FUNCTION [public.]<name>(` in file
    order, over the stripped text.

Assumptions — each needs THIS module changed, never a guard weakened:
  * File order is apply order. The owner applies migrations in number order, and a file that must
    never run after a later one refuses to (194 and 196 after 198).
  * Names are matched alone: no function here is overloaded, so the last CREATE of the name is the
    live one whatever its argument list.
  * Identifiers are unquoted and unqualified or `public.`-qualified, as every migration writes them.
  * A `DROP FUNCTION` of the name after its last CREATE means nothing is live: that fails loudly
    rather than handing back the dropped body.
"""

from __future__ import annotations

import functools
import re
from pathlib import Path
from typing import NamedTuple

MIGRATIONS = Path(__file__).resolve().parents[1] / "database" / "migrations"
_GLOB = "[0-9][0-9][0-9]_*.sql"
_NAME = re.compile(r"^\d{3}_.*\.sql$")

# '...' (with '' escapes) | -- to end of line | /* | a dollar-quote opener. The opener's tag
# follows identifier rules (no leading digit, so `$1` is a parameter, not a quote), and a `$`
# inside an identifier (`a$b$c`) opens nothing.
_SCAN = re.compile(r"'(?:[^']|'')*'|--[^\n]*|/\*|(?<![\w$])\$(?:[A-Za-z_][A-Za-z_0-9]*)?\$")
_BLOCK = re.compile(r"/\*|\*/")


def strip_sql_comments(sql: str) -> str:
    """`sql` with every `--` and `/* */` comment removed; '...' literals kept verbatim.

    A block comment becomes the newlines it spanned (one space if none), so line-anchored guards
    still see the lines around it. An unterminated `/*` or dollar quote is an AssertionError:
    Postgres refuses the file too, and a reader that guessed would hide what follows.
    """
    out, pos = [], 0
    while (m := _SCAN.search(sql, pos)):
        out.append(sql[pos:m.start()])
        tok = m.group()
        if tok[0] == "'":
            out.append(tok)
            pos = m.end()
        elif tok[0] == "-":
            pos = m.end()                                   # the newline stays
        elif tok[0] == "/":                                 # nested, as Postgres nests them
            depth, j = 1, m.end()
            while depth:
                b = _BLOCK.search(sql, j)
                assert b, f"unterminated /* comment at offset {m.start()}"
                depth += 1 if b.group() == "/*" else -1
                j = b.end()
            out.append("\n" * sql.count("\n", m.start(), j) or " ")
            pos = j
        else:                                               # $tag$ ... $tag$
            close = sql.find(tok, m.end())
            assert close >= 0, f"unterminated {tok} quote at offset {m.start()}"
            out.append(tok + strip_sql_comments(sql[m.end():close]) + tok)
            pos = close + len(tok)
    out.append(sql[pos:])
    return "".join(out)


@functools.lru_cache(maxsize=1024)
def _stripped(raw: str) -> str:
    """Keyed on the file's CONTENT, so an in-memory mutation is never served a stale strip."""
    return strip_sql_comments(raw)


class Definition(NamedTuple):
    file: str      # the migration file name, e.g. "198_subscriptions_per_lineage_....sql"
    header: str    # from just after "<name>(" to just before "AS $tag$"
    body: str      # between the dollar tags, comments stripped
    text: str      # the whole defining file, comments stripped


def _definition_re(name: str) -> re.Pattern:
    return re.compile(
        rf"\bCREATE\s+(?:OR\s+REPLACE\s+)?FUNCTION\s+(?:public\.)?{re.escape(name)}\s*\("
        r"(?P<header>.*?)\bAS\s+(?P<tag>\$\w*\$)(?P<body>.*?)(?P=tag)",
        re.IGNORECASE | re.DOTALL)


def _drop_re(name: str) -> re.Pattern:
    # The name anywhere in the DROP statement (a DROP may list several functions), public or
    # unqualified, and never as part of a longer identifier.
    return re.compile(
        rf"\bDROP\s+FUNCTION\b[^;]*?(?<![\w.$])(?:public\.)?{re.escape(name)}(?![\w$])",
        re.IGNORECASE)


def migration_sources() -> list[tuple[str, str]]:
    """(file name, raw SQL) for every migration, in apply order.

    `latest_definition` calls this through the module global, so a test can feed a synthetic
    later migration in memory with `monkeypatch.setattr(_migration_sql, "migration_sources", ...)`
    — never by writing a file into migrations/.
    """
    stray = sorted(p.name for p in MIGRATIONS.glob("*.sql") if not _NAME.match(p.name))
    assert not stray, f"migrations the {_GLOB} glob would silently skip: {stray}"
    files = sorted(MIGRATIONS.glob(_GLOB))
    assert len(files) > 100, f"the migration glob found only {len(files)} files: it has rotted"
    return [(p.name, p.read_text(encoding="utf-8")) for p in files]


def latest_definition(name: str, sources: list[tuple[str, str]] | None = None) -> Definition:
    """The live definition of `public.<name>`: the last CREATE [OR REPLACE] FUNCTION in `sources`
    ((file name, raw SQL) in apply order; default: every migration on disk)."""
    create, drop = _definition_re(name), _drop_re(name)
    last: tuple[str, Definition | str] | None = None
    found = 0
    for file, raw in (migration_sources() if sources is None else sources):
        text = _stripped(raw)
        # A CREATE counts at its END, so a DROP written inside its own body (dynamic SQL) is
        # ordered before it, and the DROP + CREATE pair 142 uses reads as a CREATE.
        events = [(m.end(), Definition(file, m["header"], m["body"], text))
                  for m in create.finditer(text)]
        events += [(m.start(), file) for m in drop.finditer(text)]
        for _pos, event in sorted(events, key=lambda e: e[0]):
            last = ("create", event) if isinstance(event, Definition) else ("drop", event)
        found += sum(isinstance(e, Definition) for _p, e in events)
    assert found, f"no CREATE [OR REPLACE] FUNCTION {name} in any migration"
    kind, event = last
    assert kind == "create", (
        f"{name}() is DROPPED in {event} after its last definition: nothing is live to check")
    return event


def norm_sql(sql: str) -> str:
    """Whitespace-normalised SQL (one space; none just inside parentheses), so a guard can pin an
    expression EXACTLY. A substring guard ("v_cur_holds" in cond) passes `NOT v_cur_holds`, an
    `OR` for an `AND` and an extra conjunct — review of the fix pass, 2026-10-15."""
    sql = re.sub(r"\s+", " ", sql).strip()
    return re.sub(r"\s+\)", ")", re.sub(r"\(\s+", "(", sql))


# ── Structure helpers for exact pins over a (comment-stripped) body ──────────────────────
#
# Each one skips '...' literals, so a `;`, `,`, `(` or `IF` inside a RAISE message is text.

_LITERAL = re.compile(r"'(?:[^']|'')*'")


def blank_literals(sql: str) -> str:
    """`sql` with every '...' literal's content replaced by `_`, SAME LENGTH: offsets still map."""
    return _LITERAL.sub(lambda m: "'" + "_" * (len(m.group()) - 2) + "'", sql)


def split_top_level(text: str, sep: str = ",") -> list[str]:
    """`text` split on `sep` outside parentheses and literals, each part stripped."""
    blank, parts, depth, start = blank_literals(text), [], 0, 0
    for i, ch in enumerate(blank):
        depth += (ch == "(") - (ch == ")")
        if ch == sep and depth == 0:
            parts.append(text[start:i].strip())
            start = i + 1
    parts.append(text[start:].strip())
    return [p for p in parts if p]


def statements(sql: str, head: str = "") -> list[tuple[int, str]]:
    """(offset of its `;`, normalised text without the `;`) for each `;`-terminated statement in
    which the regex `head` matches, cut to start there. The cut matters: PL/pgSQL puts `IF x THEN`
    and `END IF;` in the same `;`-chunk as the statement after them."""
    out, start = [], 0
    for m in re.finditer(";", blank_literals(sql)):
        chunk = norm_sql(sql[start:m.start()])
        hit = re.search(head, blank_literals(chunk))
        if chunk and hit:
            out.append((m.start(), chunk[hit.start():]))
        start = m.end()
    return out


def set_items(assignments: str) -> dict[str, str]:
    """`a = x, b = GREATEST(y, z)` -> {"a": "x", "b": "GREATEST(y, z)"}; a column set twice is an
    AssertionError (Postgres refuses it too, and a dict would hide the first)."""
    out: dict[str, str] = {}
    for item in split_top_level(assignments):
        column, _eq, value = item.partition("=")
        column = column.strip()
        assert column not in out, f"{column} is assigned twice in SET {assignments!r}"
        out[column] = norm_sql(value)
    return out


def insert_rows(sql: str, table: str) -> list[dict[str, str]]:
    """{column: value expression} for each `INSERT INTO <table> (cols) VALUES (vals)` in `sql`."""
    rows = []
    for _at, stmt in statements(sql, rf"\bINSERT INTO {re.escape(table)}(?![\w.])"):
        m = re.fullmatch(rf"INSERT INTO {re.escape(table)} ?\((.*?)\) VALUES ?\((.*)\)", stmt, re.S)
        assert m, f"an INSERT INTO {table} this reader cannot parse: {stmt}"
        cols, vals = split_top_level(m.group(1)), split_top_level(m.group(2))
        assert len(cols) == len(vals), (cols, vals)
        rows.append(dict(zip(cols, vals)))
    return rows


_IF_TOKEN = re.compile(r"\bEND\s+IF\b|\bIF\b", re.IGNORECASE)


def if_block(sql: str, at: int) -> str:
    """The text of the PL/pgSQL `IF ... END IF;` block whose `IF` starts at offset `at`, nested
    IFs matched — the SQL counterpart of brace-bounding (testing.md rule 2)."""
    blank = blank_literals(sql)
    assert re.match(r"IF\b", blank[at:], re.IGNORECASE), f"no IF at offset {at}: {sql[at:at + 40]!r}"
    depth = 0
    for m in _IF_TOKEN.finditer(blank, at):
        depth += -1 if m.group().upper().startswith("END") else 1
        if depth == 0:
            end = blank.index(";", m.end())
            return sql[at:end + 1]
    raise AssertionError(f"unbalanced IF at offset {at}")
