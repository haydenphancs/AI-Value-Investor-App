"""`earnings_filing_cache` in memory, for the tests that run the live ticks.

Not a test module (no `test_` prefix, like `_price_fakes.py`). Both ticks reach this table through
`app.database.get_supabase`, so every fixture that runs a tick installs one of these, which also
keeps those tests off the network.

A restored record is only as true as what the table gives back, so this answers the way the real
table does, and a fake that accepted what Postgres refuses would make a broken write look green:
  * the columns are migration 209's, read from the file: an unknown column is refused on a write
    (PGRST204) and on a read (42703), and so is a NULL in a NOT NULL column (23502);
  * the body is encoded as httpx encodes it (`allow_nan=False`), so a NaN or a `datetime` left in a
    row fails before any write;
  * values come back as PostgREST writes them: a whole float8 as a JSON integer, a timestamp with
    `+00:00` rather than `Z`;
  * `.in_` really filters, so a read that asks for the wrong days misses its rows.

A statement executed ON the event loop raises `FakeMisuse`, a BaseException no `except Exception`
in the code under test can swallow: the Supabase SDK is synchronous.
"""
from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

_MIGRATION = (Path(__file__).resolve().parents[1] / "database" / "migrations"
              / "209_earnings_filing_cache.sql")


class FakeMisuse(BaseException):
    """The code under test used the table in a way the real SDK would punish."""


#: The migration with its comments gone: its header quotes what the code below it does.
MIGRATION_SQL = re.sub(r"--.*$", "", _MIGRATION.read_text(encoding="utf-8"), flags=re.M)


def _migration_columns() -> Tuple[Set[str], Set[str]]:
    """(every column, the NOT NULL ones) of the table as the migration creates it."""
    body = re.search(r"CREATE TABLE IF NOT EXISTS public\.earnings_filing_cache \((.*?)\n\);",
                     MIGRATION_SQL, flags=re.S)
    assert body is not None, "209 no longer creates public.earnings_filing_cache"
    lines = [line.strip() for line in body.group(1).splitlines() if line.strip()]
    columns = [line for line in lines if not line.startswith("PRIMARY KEY")]
    primary = re.search(r"PRIMARY KEY \(([^)]*)\)", body.group(1))
    assert primary is not None, "209 no longer states a primary key"
    keyed = {name.strip() for name in primary.group(1).split(",")}
    return ({line.split()[0] for line in columns},
            {line.split()[0] for line in columns if "NOT NULL" in line} | keyed)


COLUMNS, NOT_NULL = _migration_columns()


def _as_postgrest(value: Any) -> Any:
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str) and re.fullmatch(r"\d{4}-\d\d-\d\dT[\d:.]+Z", value):
        return value[:-1] + "+00:00"
    return value


class _Query:
    def __init__(self, cache: "FilingCache"):
        self.cache = cache
        self.row: Optional[Dict[str, Any]] = None
        self.days: Optional[List[str]] = None

    def select(self, columns: str) -> "_Query":
        unknown = set(columns.split(",")) - COLUMNS
        if unknown:
            raise RuntimeError(f"42703: column {sorted(unknown)} does not exist")
        return self

    def in_(self, column: str, values: Iterable[str]) -> "_Query":
        assert column == "report_date", column
        self.days = list(values)
        return self

    def upsert(self, row: Dict[str, Any], *, on_conflict: str) -> "_Query":
        assert on_conflict == "symbol,report_date", on_conflict
        self.row = json.loads(json.dumps(row, allow_nan=False))
        return self

    def execute(self) -> SimpleNamespace:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass
        else:
            raise FakeMisuse("a statement ran on the event loop")
        if self.row is not None:
            return self.cache._write(self.row)
        assert self.days is not None, "a read of every row: the window was not applied"
        return self.cache._read(self.days)


class FilingCache:
    """Stands in for `get_supabase()`. Its rows outlive a deploy; the records do not."""

    def __init__(self, rows: Iterable[Dict[str, Any]] = ()):
        self.rows: Dict[Tuple[str, str], Dict[str, Any]] = {}
        self.reads: List[List[str]] = []        # the days each read asked for
        self.writes: List[Dict[str, Any]] = []
        self.read_error: Optional[Exception] = None     # every read raises this while it is set
        self.write_error: Optional[Exception] = None    # likewise every write
        for row in rows:
            self.hold(row)

    def hold(self, row: Dict[str, Any]) -> None:
        """A row an earlier process left."""
        self.rows[(row["symbol"], row["report_date"])] = dict(row)

    def table(self, name: str) -> _Query:
        assert name == "earnings_filing_cache", name
        return _Query(self)

    def _write(self, row: Dict[str, Any]) -> SimpleNamespace:
        if self.write_error is not None:
            raise self.write_error
        unknown = set(row) - COLUMNS
        if unknown:
            raise RuntimeError(f"PGRST204: no column {sorted(unknown)} in the schema cache")
        missing = {name for name in NOT_NULL if row.get(name) is None}
        if missing:
            raise RuntimeError(f"23502: null value in column {sorted(missing)}")
        self.writes.append(row)
        self.rows[(row["symbol"], row["report_date"])] = row
        return SimpleNamespace(data=[row])

    def _read(self, days: List[str]) -> SimpleNamespace:
        self.reads.append(days)
        if self.read_error is not None:
            raise self.read_error
        rows = [{k: _as_postgrest(v) for k, v in row.items()}
                for row in self.rows.values() if row["report_date"] in days]
        return SimpleNamespace(data=rows)
