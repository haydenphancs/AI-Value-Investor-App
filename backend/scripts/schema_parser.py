#!/usr/bin/env python3
"""
schema_parser.py — parse a `pg_dump --schema-only` file into structured Python.

Used by `generate_schema_doc.py` to build the Caydex Database Atlas
(documents/System Design/caydex-database-schema.html) from
backend/database/schema_snapshot.sql.

Stdlib only, deliberately: this is a documentation tool and must never pull a
dependency into backend/.

Scope note — the dump is machine-regular (pg_dump 18.4, `--schema-only
--no-owner --no-privileges`), so regex parsing is sound here. This is NOT a
general-purpose SQL parser and does not try to be; every pattern below is
pinned to the exact shape pg_dump emits. If a future pg_dump changes that
shape, `--check` in the generator is what tells you, loudly.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterator

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class Column:
    name: str
    type: str
    nullable: bool = True
    default: str | None = None
    comment: str | None = None

    @property
    def is_array(self) -> bool:
        return self.type.endswith("[]")

    @property
    def base_type(self) -> str:
        """Type with the array suffix and any `public.` qualifier stripped."""
        t = self.type[:-2] if self.type.endswith("[]") else self.type
        return t.split(".")[-1] if "." in t and "(" not in t.split(".")[-1] else t


@dataclass
class ForeignKey:
    schema: str
    table: str
    columns: list[str]
    ref_schema: str
    ref_table: str
    ref_columns: list[str]
    on_delete: str | None = None
    on_update: str | None = None
    name: str = ""

    @property
    def child(self) -> str:
        return f"{self.schema}.{self.table}"

    @property
    def parent(self) -> str:
        return f"{self.ref_schema}.{self.ref_table}"


@dataclass
class Index:
    name: str
    schema: str
    table: str
    method: str
    expression: str
    unique: bool = False
    predicate: str | None = None
    comment: str | None = None


@dataclass
class Policy:
    name: str
    schema: str
    table: str
    command: str = "ALL"
    roles: list[str] = field(default_factory=list)
    using: str | None = None
    with_check: str | None = None


@dataclass
class Trigger:
    name: str
    schema: str
    table: str
    timing: str
    function: str


@dataclass
class Table:
    schema: str
    name: str
    columns: list[Column] = field(default_factory=list)
    checks: list[tuple[str, str]] = field(default_factory=list)  # (name, expr)
    primary_key: list[str] = field(default_factory=list)
    uniques: list[list[str]] = field(default_factory=list)
    indexes: list[Index] = field(default_factory=list)
    policies: list[Policy] = field(default_factory=list)
    triggers: list[Trigger] = field(default_factory=list)
    rls: bool = False
    comment: str | None = None
    raw_ddl: str = ""
    partition_by: str | None = None

    @property
    def qname(self) -> str:
        return f"{self.schema}.{self.name}"

    def column(self, name: str) -> Column | None:
        for c in self.columns:
            if c.name == name:
                return c
        return None


@dataclass
class EnumType:
    schema: str
    name: str
    values: list[str]

    @property
    def qname(self) -> str:
        return f"{self.schema}.{self.name}"


@dataclass
class Function:
    schema: str
    name: str
    args: str
    returns: str
    language: str = ""
    security_definer: bool = False
    volatility: str = ""
    body: str = ""
    comment: str | None = None
    tables: list[str] = field(default_factory=list)  # table names named in the body

    @property
    def qname(self) -> str:
        return f"{self.schema}.{self.name}"

    @property
    def signature(self) -> str:
        return f"{self.name}({self.args})"


@dataclass
class View:
    schema: str
    name: str
    definition: str
    comment: str | None = None

    @property
    def qname(self) -> str:
        return f"{self.schema}.{self.name}"


@dataclass
class Schema:
    tables: dict[str, Table] = field(default_factory=dict)
    enums: dict[str, EnumType] = field(default_factory=dict)
    functions: list[Function] = field(default_factory=list)
    views: list[View] = field(default_factory=list)
    foreign_keys: list[ForeignKey] = field(default_factory=list)
    extensions: list[str] = field(default_factory=list)
    pg_version: str = ""
    dump_version: str = ""

    def tables_in(self, schema: str) -> list[Table]:
        return sorted(
            (t for t in self.tables.values() if t.schema == schema),
            key=lambda t: t.name,
        )


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

_IDENT = r'(?:"[^"]+"|[A-Za-z_][A-Za-z0-9_$]*)'


def _unquote(ident: str) -> str:
    ident = ident.strip()
    if len(ident) >= 2 and ident[0] == '"' and ident[-1] == '"':
        return ident[1:-1]
    return ident


def _unquote_literal(lit: str) -> str:
    """Turn a single-quoted SQL literal into its text value."""
    lit = lit.strip()
    if len(lit) >= 2 and lit[0] == "'" and lit[-1] == "'":
        return lit[1:-1].replace("''", "'")
    return lit


def _split_top_level(text: str, sep: str = ",") -> list[str]:
    """Split on `sep`, ignoring separators nested in parens or string literals."""
    out: list[str] = []
    depth = 0
    in_str = False
    buf: list[str] = []
    i = 0
    while i < len(text):
        ch = text[i]
        if in_str:
            buf.append(ch)
            if ch == "'":
                # '' is an escaped quote, not a terminator
                if i + 1 < len(text) and text[i + 1] == "'":
                    buf.append("'")
                    i += 2
                    continue
                in_str = False
            i += 1
            continue
        if ch == "'":
            in_str = True
            buf.append(ch)
        elif ch == "(":
            depth += 1
            buf.append(ch)
        elif ch == ")":
            depth -= 1
            buf.append(ch)
        elif ch == sep and depth == 0:
            out.append("".join(buf).strip())
            buf = []
        else:
            buf.append(ch)
        i += 1
    tail = "".join(buf).strip()
    if tail:
        out.append(tail)
    return out


def _match_paren(text: str, open_idx: int) -> int:
    """Index of the `)` matching the `(` at open_idx. -1 if unbalanced."""
    assert text[open_idx] == "("
    depth = 0
    in_str = False
    i = open_idx
    while i < len(text):
        ch = text[i]
        if in_str:
            if ch == "'":
                if i + 1 < len(text) and text[i + 1] == "'":
                    i += 2
                    continue
                in_str = False
        elif ch == "'":
            in_str = True
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return -1


def _col_list(raw: str) -> list[str]:
    return [_unquote(c) for c in _split_top_level(raw) if c.strip()]


# ---------------------------------------------------------------------------
# Individual passes
# ---------------------------------------------------------------------------

# The trailing `([^;]*)` absorbs a partitioned table's `PARTITION BY RANGE (...)`
# clause, which sits between the closing paren and the semicolon
# (`realtime.messages`). Without it that table is skipped silently and the
# non-greedy body match runs on into the next statement.
_RE_TABLE = re.compile(
    rf"^CREATE TABLE ({_IDENT})\.({_IDENT}) \(\n(.*?)\n\)([^;]*);", re.M | re.S
)
_RE_CHECK = re.compile(rf"^CONSTRAINT ({_IDENT}) CHECK ", re.I)


def _parse_column(defn: str) -> Column | None:
    """Parse one entry from a CREATE TABLE body."""
    defn = defn.strip()
    if not defn or _RE_CHECK.match(defn):
        return None
    m = re.match(rf"^({_IDENT})\s+(.*)$", defn, re.S)
    if not m:
        return None
    name = _unquote(m.group(1))
    rest = " ".join(m.group(2).split())

    nullable = True
    if rest.upper().endswith(" NOT NULL"):
        nullable = False
        rest = rest[: -len(" NOT NULL")].rstrip()
    elif rest.upper() == "NOT NULL":
        return None  # malformed; no type

    default = None
    # Only a top-level DEFAULT counts — one inside a type's parens is not a default.
    for part_start in _find_keyword_positions(rest, " DEFAULT "):
        default = rest[part_start + len(" DEFAULT ") :].strip()
        rest = rest[:part_start].rstrip()
        break

    # `GENERATED ... AS IDENTITY` / `GENERATED ALWAYS AS (...) STORED`
    gen = re.search(r"\s+GENERATED\s+", rest, re.I)
    if gen:
        default = default or rest[gen.start() :].strip()
        rest = rest[: gen.start()].rstrip()

    return Column(name=name, type=rest.strip(), nullable=nullable, default=default)


def _find_keyword_positions(text: str, keyword: str) -> Iterator[int]:
    """Yield indexes of `keyword` occurring at paren depth 0, outside strings."""
    depth = 0
    in_str = False
    i = 0
    kl = len(keyword)
    upper = text.upper()
    key = keyword.upper()
    while i < len(text):
        ch = text[i]
        if in_str:
            if ch == "'":
                if i + 1 < len(text) and text[i + 1] == "'":
                    i += 2
                    continue
                in_str = False
        elif ch == "'":
            in_str = True
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif depth == 0 and upper.startswith(key, i):
            yield i
            i += kl
            continue
        i += 1


def parse_tables(sql: str) -> dict[str, Table]:
    tables: dict[str, Table] = {}
    for m in _RE_TABLE.finditer(sql):
        schema, name, body = _unquote(m.group(1)), _unquote(m.group(2)), m.group(3)
        t = Table(schema=schema, name=name, raw_ddl=m.group(0))
        t.partition_by = " ".join(m.group(4).split()) or None
        for entry in _split_top_level(body):
            entry = entry.strip()
            cm = _RE_CHECK.match(entry)
            if cm:
                expr = entry[cm.end() :].strip()
                t.checks.append((_unquote(cm.group(1)), expr))
                continue
            col = _parse_column(entry)
            if col:
                t.columns.append(col)
        tables[t.qname] = t
    return tables


_RE_CONSTRAINT = re.compile(
    rf"^ALTER TABLE (?:ONLY )?({_IDENT})\.({_IDENT})\s*\n"
    rf"\s+ADD CONSTRAINT ({_IDENT}) (PRIMARY KEY|UNIQUE|FOREIGN KEY)\s*\((.*?)\)(.*?);",
    re.M | re.S,
)


def parse_constraints(sql: str, tables: dict[str, Table]) -> list[ForeignKey]:
    fks: list[ForeignKey] = []
    for m in _RE_CONSTRAINT.finditer(sql):
        schema, table = _unquote(m.group(1)), _unquote(m.group(2))
        cname, kind, cols, tail = _unquote(m.group(3)), m.group(4), m.group(5), m.group(6)
        columns = _col_list(cols)
        t = tables.get(f"{schema}.{table}")
        if kind == "PRIMARY KEY":
            if t is not None:
                t.primary_key = columns
        elif kind == "UNIQUE":
            if t is not None:
                t.uniques.append(columns)
        else:  # FOREIGN KEY
            rm = re.search(
                rf"REFERENCES\s+(?:({_IDENT})\.)?({_IDENT})\s*\((.*?)\)", tail, re.S
            )
            if not rm:
                continue
            od = re.search(r"ON DELETE ((?:NO ACTION|SET NULL|SET DEFAULT|RESTRICT|CASCADE))", tail, re.I)
            ou = re.search(r"ON UPDATE ((?:NO ACTION|SET NULL|SET DEFAULT|RESTRICT|CASCADE))", tail, re.I)
            fks.append(
                ForeignKey(
                    schema=schema,
                    table=table,
                    columns=columns,
                    ref_schema=_unquote(rm.group(1)) if rm.group(1) else schema,
                    ref_table=_unquote(rm.group(2)),
                    ref_columns=_col_list(rm.group(3)),
                    on_delete=od.group(1).upper() if od else None,
                    on_update=ou.group(1).upper() if ou else None,
                    name=cname,
                )
            )
    return fks


_RE_INDEX = re.compile(
    rf"^CREATE (UNIQUE )?INDEX ({_IDENT}) ON ({_IDENT})\.({_IDENT}) USING ({_IDENT}) ",
    re.M,
)


def parse_indexes(sql: str, tables: dict[str, Table]) -> list[Index]:
    out: list[Index] = []
    for m in _RE_INDEX.finditer(sql):
        open_idx = sql.find("(", m.end() - 1)
        if open_idx == -1:
            continue
        close_idx = _match_paren(sql, open_idx)
        if close_idx == -1:
            continue
        expr = sql[open_idx + 1 : close_idx].strip()
        tail_end = sql.find(";", close_idx)
        tail = sql[close_idx + 1 : tail_end] if tail_end != -1 else ""
        pm = re.search(r"\bWHERE\b(.*)$", tail, re.S)
        idx = Index(
            name=_unquote(m.group(2)),
            schema=_unquote(m.group(3)),
            table=_unquote(m.group(4)),
            method=m.group(5),
            expression=" ".join(expr.split()),
            unique=bool(m.group(1)),
            predicate=" ".join(pm.group(1).split()) if pm else None,
        )
        out.append(idx)
        t = tables.get(f"{idx.schema}.{idx.table}")
        if t is not None:
            t.indexes.append(idx)
    return out


_RE_POLICY = re.compile(
    rf"^CREATE POLICY (\"[^\"]+\"|{_IDENT}) ON ({_IDENT})\.({_IDENT})(.*?);\s*$",
    re.M | re.S,
)


def parse_policies(sql: str, tables: dict[str, Table]) -> list[Policy]:
    out: list[Policy] = []
    for m in _RE_POLICY.finditer(sql):
        tail = m.group(4)
        cmd = re.search(r"^\s+FOR (ALL|SELECT|INSERT|UPDATE|DELETE)\b", tail)
        roles_m = re.search(r"\bTO ([\w\s,\"]+?)(?=\s+(?:USING|WITH CHECK)\b|\s*$)", tail)
        using = re.search(r"\bUSING\s*\(", tail)
        wcheck = re.search(r"\bWITH CHECK\s*\(", tail)
        p = Policy(
            name=_unquote(m.group(1)),
            schema=_unquote(m.group(2)),
            table=_unquote(m.group(3)),
            command=cmd.group(1) if cmd else "ALL",
            roles=[r.strip().strip('"') for r in roles_m.group(1).split(",")] if roles_m else [],
            using=_extract_paren(tail, using.end() - 1) if using else None,
            with_check=_extract_paren(tail, wcheck.end() - 1) if wcheck else None,
        )
        out.append(p)
        t = tables.get(f"{p.schema}.{p.table}")
        if t is not None:
            t.policies.append(p)
    return out


def _extract_paren(text: str, open_idx: int) -> str | None:
    close = _match_paren(text, open_idx)
    if close == -1:
        return None
    return " ".join(text[open_idx + 1 : close].split())


_RE_RLS = re.compile(
    rf"^ALTER TABLE ({_IDENT})\.({_IDENT}) ENABLE ROW LEVEL SECURITY;", re.M
)


def parse_rls(sql: str, tables: dict[str, Table]) -> None:
    for m in _RE_RLS.finditer(sql):
        t = tables.get(f"{_unquote(m.group(1))}.{_unquote(m.group(2))}")
        if t is not None:
            t.rls = True


_RE_ENUM = re.compile(
    rf"^CREATE TYPE ({_IDENT})\.({_IDENT}) AS ENUM \(\n(.*?)\n\);", re.M | re.S
)


def parse_enums(sql: str) -> dict[str, EnumType]:
    out: dict[str, EnumType] = {}
    for m in _RE_ENUM.finditer(sql):
        vals = [_unquote_literal(v) for v in _split_top_level(m.group(3)) if v.strip()]
        e = EnumType(schema=_unquote(m.group(1)), name=_unquote(m.group(2)), values=vals)
        out[e.qname] = e
    return out


_RE_FUNC_HEAD = re.compile(rf"^CREATE (?:OR REPLACE )?FUNCTION ({_IDENT})\.({_IDENT})\(", re.M)


def parse_functions(sql: str) -> list[Function]:
    out: list[Function] = []
    for m in _RE_FUNC_HEAD.finditer(sql):
        open_idx = m.end() - 1
        close_idx = _match_paren(sql, open_idx)
        if close_idx == -1:
            continue
        args = " ".join(sql[open_idx + 1 : close_idx].split())
        # header runs from after the arg list to the `AS $tag$`
        as_m = re.compile(r"\n\s+AS (\$[A-Za-z_]*\$)").search(sql, close_idx)
        if not as_m:
            continue
        header = sql[close_idx + 1 : as_m.start()]
        tag = as_m.group(1)
        body_start = as_m.end()
        body_end = sql.find(tag, body_start)
        body = sql[body_start:body_end] if body_end != -1 else ""

        ret = re.search(r"RETURNS\s+(.*?)(?=\n)", header, re.S)
        lang = re.search(r"\bLANGUAGE\s+(\w+)", header)
        vol = re.search(r"\b(IMMUTABLE|STABLE|VOLATILE)\b", header)
        out.append(
            Function(
                schema=_unquote(m.group(1)),
                name=_unquote(m.group(2)),
                args=args,
                returns=" ".join(ret.group(1).split()) if ret else "",
                language=lang.group(1) if lang else "",
                security_definer="SECURITY DEFINER" in header,
                volatility=vol.group(1) if vol else "",
                body=body,
            )
        )
    return out


_RE_TRIGGER = re.compile(
    rf"^CREATE (?:OR REPLACE )?(?:CONSTRAINT )?TRIGGER ({_IDENT}) "
    rf"((?:BEFORE|AFTER|INSTEAD OF)[^\n]*?) ON ({_IDENT})\.({_IDENT}) "
    rf".*?EXECUTE (?:FUNCTION|PROCEDURE) ({_IDENT}\.{_IDENT})\(",
    re.M | re.S,
)


def parse_triggers(sql: str, tables: dict[str, Table]) -> list[Trigger]:
    out: list[Trigger] = []
    for m in _RE_TRIGGER.finditer(sql):
        tr = Trigger(
            name=_unquote(m.group(1)),
            schema=_unquote(m.group(3)),
            table=_unquote(m.group(4)),
            timing=" ".join(m.group(2).split()),
            function=m.group(5),
        )
        out.append(tr)
        t = tables.get(f"{tr.schema}.{tr.table}")
        if t is not None:
            t.triggers.append(tr)
    return out


_RE_VIEW = re.compile(
    rf"^CREATE (?:OR REPLACE )?VIEW ({_IDENT})\.({_IDENT}) AS\n(.*?);\n", re.M | re.S
)


def parse_views(sql: str) -> list[View]:
    return [
        View(
            schema=_unquote(m.group(1)),
            name=_unquote(m.group(2)),
            definition=m.group(3).strip(),
        )
        for m in _RE_VIEW.finditer(sql)
    ]


_RE_COMMENT = re.compile(
    r"^COMMENT ON (TABLE|COLUMN|FUNCTION|INDEX|VIEW) (.*?) IS '(.*?)';\s*$",
    re.M | re.S,
)


def parse_comments(sql: str, schema: Schema) -> None:
    """Attach COMMENT ON text. These are authored in migrations and are the
    best available purpose text — better than anything inferred."""
    fn_by_name: dict[str, list[Function]] = {}
    for f in schema.functions:
        fn_by_name.setdefault(f.qname, []).append(f)
    idx_by_name = {
        f"{i.schema}.{i.name}": i for t in schema.tables.values() for i in t.indexes
    }
    view_by_name = {v.qname: v for v in schema.views}

    for m in _RE_COMMENT.finditer(sql):
        kind, target, text = m.group(1), m.group(2).strip(), _unquote_literal("'" + m.group(3) + "'")
        if kind == "TABLE":
            t = schema.tables.get(_qualify(target))
            if t is not None:
                t.comment = text
        elif kind == "VIEW":
            v = view_by_name.get(_qualify(target))
            if v is not None:
                v.comment = text
        elif kind == "COLUMN":
            parts = target.split(".")
            if len(parts) != 3:
                continue
            t = schema.tables.get(f"{_unquote(parts[0])}.{_unquote(parts[1])}")
            if t is None:
                continue
            c = t.column(_unquote(parts[2]))
            if c is not None:
                c.comment = text
        elif kind == "INDEX":
            i = idx_by_name.get(_qualify(target))
            if i is not None:
                i.comment = text
        elif kind == "FUNCTION":
            qn = _qualify(target.split("(")[0])
            for f in fn_by_name.get(qn, []):
                f.comment = text


def _qualify(target: str) -> str:
    parts = target.split(".")
    return ".".join(_unquote(p) for p in parts)


_RE_EXT = re.compile(rf"^CREATE EXTENSION IF NOT EXISTS ({_IDENT})", re.M)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def parse_dump(sql: str) -> Schema:
    s = Schema()

    pg = re.search(r"-- Dumped from database version ([\d.]+)", sql)
    dv = re.search(r"-- Dumped by pg_dump version ([\d.]+)", sql)
    s.pg_version = pg.group(1) if pg else "unknown"
    s.dump_version = dv.group(1) if dv else "unknown"
    s.extensions = sorted({_unquote(m.group(1)) for m in _RE_EXT.finditer(sql)})

    s.tables = parse_tables(sql)
    s.enums = parse_enums(sql)
    s.foreign_keys = parse_constraints(sql, s.tables)
    parse_indexes(sql, s.tables)
    parse_policies(sql, s.tables)
    parse_rls(sql, s.tables)
    parse_triggers(sql, s.tables)
    s.functions = parse_functions(sql)
    s.views = parse_views(sql)
    parse_comments(sql, s)

    _attach_function_tables(s)
    return s


def _attach_function_tables(s: Schema) -> None:
    """Record which tables each function body names, so a table card can show
    'RPCs that touch this'. Word-boundary match on the bare table name."""
    names = sorted({t.name for t in s.tables.values() if t.schema == "public"})
    if not names:
        return
    pattern = re.compile(r"\b(" + "|".join(re.escape(n) for n in names) + r")\b")
    for f in s.functions:
        found = set(pattern.findall(f.body))
        # A function whose own name contains a table name is not a reference.
        found.discard(f.name)
        f.tables = sorted(found)
