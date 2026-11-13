"""`earnings_filing_cache` in memory, for the tests that run the live ticks.

Not a test module (no `test_` prefix, like `_price_fakes.py`). Both ticks reach this table through
`app.database.get_supabase`, so every fixture that runs a tick installs one of these, which also
keeps those tests off the network.

A restored record is only as true as what the table gives back, so this answers the way the real
table does, and a fake that accepted what Postgres refuses would make a broken write look green:
  * the columns and the key are migration 209's, read from the file: an unknown column is refused
    on a write (PGRST204) and on a read (42703), a NULL in a NOT NULL column too (23502), and so
    is an `on_conflict` that is not the table's key (42P10);
  * a read gives back the columns it named and no others, so one it forgets is lost here as well;
  * the builders have the real SDK's shape: a table offers `.select` and `.upsert` only, and `.in_`
    exists only on what `.select` returns, so a read with no select raises here too;
  * the body is encoded as httpx encodes it (`allow_nan=False`), so a NaN or a `datetime` left in a
    row fails before any write;
  * values come back as PostgREST writes them: a whole float8 as a JSON integer, a timestamp with
    `+00:00` rather than `Z`;
  * `.in_` really filters, so a read that asks for the wrong days misses its rows.

A statement executed ON the event loop, or a read of every row, raises `FakeMisuse`, a
BaseException no `except Exception` in the code under test can swallow: the Supabase SDK is
synchronous, and the table is never read whole.
"""
from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

_MIGRATION = (Path(__file__).resolve().parents[1] / "database" / "migrations"
              / "209_earnings_filing_cache.sql")


class FakeMisuse(BaseException):
    """The code under test used the table in a way the real SDK would punish."""


#: The migration as written, its header included.
MIGRATION_TEXT = _MIGRATION.read_text(encoding="utf-8")
#: The migration with its comments gone: its header quotes what the code below it does.
MIGRATION_SQL = re.sub(r"--.*$", "", MIGRATION_TEXT, flags=re.M)


def _migration_table() -> Tuple[Dict[str, str], Set[str], Tuple[str, ...]]:
    """({column: its SQL type}, the NOT NULL columns, the primary key) of the table as the
    migration creates it."""
    body = re.search(r"CREATE TABLE IF NOT EXISTS public\.earnings_filing_cache \((.*?)\n\);",
                     MIGRATION_SQL, flags=re.S)
    assert body is not None, "209 no longer creates public.earnings_filing_cache"
    lines = [" ".join(line.split()).rstrip(",") for line in body.group(1).splitlines()]
    columns = [line.split(" ", 1) for line in lines if line and not line.startswith("PRIMARY KEY")]
    primary = re.search(r"PRIMARY KEY \(([^)]*)\)", body.group(1))
    assert primary is not None, "209 no longer states a primary key"
    key = tuple(name.strip() for name in primary.group(1).split(","))
    return ({name: re.sub(r" (?:NOT NULL|DEFAULT\b).*$", "", spec) for name, spec in columns},
            {name for name, spec in columns if "NOT NULL" in spec} | set(key), key)


COLUMN_TYPES, NOT_NULL, PRIMARY_KEY = _migration_table()
COLUMNS = set(COLUMN_TYPES)


def _as_postgrest(value: Any) -> Any:
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str) and re.fullmatch(r"\d{4}-\d\d-\d\dT[\d:.]+Z", value):
        return value[:-1] + "+00:00"
    return value


def _off_the_loop() -> None:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return
    raise FakeMisuse("a statement ran on the event loop")


class _Read:
    """What `.select` returns, as the SDK's select builder: the one place `.in_` exists."""

    def __init__(self, cache: "FilingCache", columns: List[str]):
        self.cache = cache
        self.columns = columns
        self.days: Optional[List[str]] = None

    def in_(self, column: str, values: Iterable[str]) -> "_Read":
        assert column == "report_date", column
        self.days = list(values)
        return self

    def execute(self) -> SimpleNamespace:
        _off_the_loop()
        if self.days is None:
            raise FakeMisuse("a read of every row: the window was not applied")
        return self.cache._read(self.days, self.columns)


class _Write:
    def __init__(self, cache: "FilingCache", row: Dict[str, Any], on_conflict: str):
        self.cache = cache
        self.row = row
        self.on_conflict = on_conflict

    def execute(self) -> SimpleNamespace:
        _off_the_loop()
        return self.cache._write(self.row, self.on_conflict)


class _Table:
    """`get_supabase().table(name)`, as the SDK's request builder: no filter until a select."""

    def __init__(self, cache: "FilingCache"):
        self.cache = cache

    def select(self, *columns: str) -> _Read:
        named = [name.strip() for group in columns for name in group.split(",")]
        return _Read(self.cache, sorted(COLUMNS) if named in ([], ["*"]) else named)

    def upsert(self, row: Dict[str, Any], *, on_conflict: str) -> _Write:
        return _Write(self.cache, json.loads(json.dumps(row, allow_nan=False)), on_conflict)


class FilingCache:
    """Stands in for `get_supabase()`. Its rows outlive a deploy; the records do not."""

    def __init__(self, rows: Iterable[Dict[str, Any]] = ()):
        self.rows: Dict[Tuple[Any, ...], Dict[str, Any]] = {}
        self.reads: List[List[str]] = []        # the days each read asked for
        self.writes: List[Dict[str, Any]] = []
        self.read_error: Optional[Exception] = None     # every read raises this while it is set
        self.write_error: Optional[Exception] = None    # likewise every write
        for row in rows:
            self.hold(row)

    def hold(self, row: Dict[str, Any]) -> None:
        """A row an earlier process left."""
        self.rows[tuple(row[name] for name in PRIMARY_KEY)] = dict(row)

    def table(self, name: str) -> _Table:
        assert name == "earnings_filing_cache", name
        return _Table(self)

    def _write(self, row: Dict[str, Any], on_conflict: str) -> SimpleNamespace:
        if self.write_error is not None:
            raise self.write_error
        if set(on_conflict.split(",")) != set(PRIMARY_KEY):
            raise RuntimeError(f"42P10: no unique constraint matches ON CONFLICT ({on_conflict})")
        unknown = set(row) - COLUMNS
        if unknown:
            raise RuntimeError(f"PGRST204: no column {sorted(unknown)} in the schema cache")
        missing = {name for name in NOT_NULL if row.get(name) is None}
        if missing:
            raise RuntimeError(f"23502: null value in column {sorted(missing)}")
        self.writes.append(row)
        self.rows[tuple(row[name] for name in PRIMARY_KEY)] = row
        return SimpleNamespace(data=[row])

    def _read(self, days: List[str], columns: Sequence[str]) -> SimpleNamespace:
        self.reads.append(days)
        if self.read_error is not None:
            raise self.read_error
        unknown = set(columns) - COLUMNS
        if unknown:
            raise RuntimeError(f"42703: column {sorted(unknown)} does not exist")
        rows = [{name: _as_postgrest(row.get(name)) for name in columns}
                for row in self.rows.values() if row["report_date"] in days]
        return SimpleNamespace(data=rows)
