"""Migration 205 (the Trillion Club stake finder) — the SQL pinned to the Python that writes it.

WHY THIS FILE EXISTS
--------------------
205 adds the machine-written half of Trillion-Dollar Club Bets: `origin` / `finding_id` /
`investee_cik` / `owner_kept_at` / `withdrawn_at` on `trillion_club_stakes`, and the finder's
ledger `trillion_club_stake_findings`. Every way the two sides can drift is a production failure
that no unit test of the finder would see, because the finder's tests run against an in-memory
fake:

* a closed-set value the Python writes but the CHECK lacks is a 23514 on the write — for an auto
  row, after the Telegram note already went out; a value the CHECK allows but no code writes is a
  state nobody handles. So every CHECK list must EQUAL its `stake_contracts` tuple, in order.
* a rule weakened by one token (an OR for an AND, an unescaped dot in the SEC URL regex) still
  "has the constraint". So each expression is pinned EXACTLY, built from the Python constants.
* a non-idempotent statement fails the owner's second apply half-way through a transaction he
  runs by hand in Studio. So every statement's idempotency shape is checked.
* a CHECK that requires `stake_id` would make the owner's Studio DELETE of a stake fail (the FK
  is ON DELETE SET NULL). So no ledger CHECK may mention it — statically AND in Postgres, for
  every status.

WHAT IT CANNOT SEE
------------------
The live database. The Postgres half (section E) replays 175, the real seed and 205 twice in a
throwaway cluster on a PRIVATE Unix socket (no TCP; deleted afterwards) — the database decides,
not a regex — and skips cleanly, with the reason, when PostgreSQL is not installed. The owner runs
the VERIFY block in 205's footer against Supabase after applying it.

Hermetic: no Supabase, no SEC, no network.
"""

from __future__ import annotations

import ast
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import pytest
from dotenv import dotenv_values

from _migration_sql import blank_literals, migration_sources, norm_sql, split_top_level, statements
from _migration_sql import strip_sql_comments
from app.config import Settings
from app.services.trillion_club import stake_contracts as c
from app.services.trillion_club import stake_names as sn

BACKEND = Path(__file__).resolve().parents[1]
MIGRATIONS = BACKEND / "database" / "migrations"
MIGRATION = MIGRATIONS / "205_trillion_club_stake_finder.sql"
M175 = MIGRATIONS / "175_trillion_club.sql"
SEED_JSON = BACKEND / "data" / "trillion_club_seed.json"
SNAPSHOT = BACKEND / "database" / "schema_snapshot.sql"
ENV_EXAMPLE = BACKEND / ".env.example"

COMPANIES = "trillion_club_companies"
STAKES = "trillion_club_stakes"
FINDINGS = "trillion_club_stake_findings"
JOB = "trillion_club_stake_finder"
#: The phrase the plan asked every surface to use for where a stake comes from.
SOURCE_PHRASE = "hand-kept, or found automatically from SEC filings"


# ── reading the SQL ─────────────────────────────────────────────────────────────────────────


def _raw() -> str:
    return MIGRATION.read_text(encoding="utf-8")


def _sql() -> str:
    return strip_sql_comments(_raw())


def _close_paren(blank: str, open_at: int) -> int:
    """The offset of the ')' matching the '(' at `open_at` in literal-blanked SQL."""
    assert blank[open_at] == "(", blank[open_at:open_at + 20]
    depth = 0
    for i in range(open_at, len(blank)):
        if blank[i] == "(":
            depth += 1
        elif blank[i] == ")":
            depth -= 1
            if depth == 0:
                return i
    raise AssertionError(f"unbalanced parenthesis at offset {open_at}")


_CHECK_HEAD = re.compile(r"\bCONSTRAINT\s+([a-z][a-z0-9_]*)\s+CHECK\s*\(", re.I)
_DROP_HEAD = re.compile(r"\bDROP\s+CONSTRAINT\s+IF\s+EXISTS\s+([a-z][a-z0-9_]*)", re.I)


def named_checks(sql: str) -> Dict[str, str]:
    """{constraint name: CHECK expression} of one comment-stripped file. Searched on the
    literal-blanked text, so a COMMENT that quotes a constraint is never read as one."""
    blank = blank_literals(sql)
    out: Dict[str, str] = {}
    for m in _CHECK_HEAD.finditer(blank):
        name = m.group(1).lower()
        assert name not in out, f"CHECK {name} is defined twice in one migration"
        out[name] = sql[m.end():_close_paren(blank, m.end() - 1)]
    return out


def live_checks(sources: Sequence[Tuple[str, str]]) -> Dict[str, Tuple[str, str]]:
    """{name: (file, expression)} for every named CHECK of the finder's two tables as the
    migrations leave it: the LAST definition in apply order, unless a later `DROP CONSTRAINT`
    removed it. A later migration that widens a list is therefore the one this file reads."""
    live: Dict[str, Tuple[str, str]] = {}
    for fname, raw in sources:
        sql = strip_sql_comments(raw)
        blank = blank_literals(sql)
        events = [(m.start(), "add", m.group(1).lower(),
                   sql[m.end():_close_paren(blank, m.end() - 1)])
                  for m in _CHECK_HEAD.finditer(blank)]
        events += [(m.start(), "drop", m.group(1).lower(), "") for m in _DROP_HEAD.finditer(blank)]
        for _, kind, name, expr in sorted(events, key=lambda e: e[0]):
            if not name.startswith("trillion_club_stake"):
                continue
            if kind == "drop":
                live.pop(name, None)
            else:
                live[name] = (fname, expr)
    return live


def _live() -> Dict[str, str]:
    return {name: expr for name, (_f, expr) in live_checks(migration_sources()).items()}


def _quoted(text: str) -> Tuple[str, ...]:
    return tuple(re.findall(r"'([^']*)'", text))


def in_list(expr: str, column: str, *, negated: bool = False) -> Tuple[str, ...]:
    op = r"NOT\s+IN" if negated else r"IN"
    found = re.findall(rf"(?<![\w.]){column}\s+{op}\s*\(([^)]*)\)", expr, re.I)
    assert len(found) == 1, f"expected one `{column} {'NOT ' if negated else ''}IN (...)` in {expr!r}"
    return _quoted(found[0])


def _sql_list(values: Sequence[str]) -> str:
    return ", ".join(f"'{v}'" for v in values)


def _create_table_body(sql: str, table: str) -> str:
    blank = blank_literals(sql)
    m = re.search(rf"CREATE TABLE IF NOT EXISTS public\.{table}\s*\(", blank)
    assert m, f"205 no longer creates public.{table}"
    return sql[m.end():_close_paren(blank, m.end() - 1)]


def ledger_columns(sql: str) -> Dict[str, str]:
    """{column: its normalised definition} of the ledger's CREATE TABLE."""
    out: Dict[str, str] = {}
    for part in split_top_level(_create_table_body(sql, FINDINGS)):
        text = norm_sql(part)
        if text.upper().startswith("CONSTRAINT "):
            continue
        name, _, rest = text.partition(" ")
        assert name not in out, f"column {name} declared twice"
        out[name] = rest
    return out


def stake_columns_added(sql: str) -> Dict[str, str]:
    """{column: its normalised definition} for every `ADD COLUMN` 205 makes on the stakes table."""
    out: Dict[str, str] = {}
    for _, stmt in statements(sql, rf"^ALTER TABLE public\.{STAKES}\b"):
        for part in split_top_level(stmt[len(f"ALTER TABLE public.{STAKES}"):]):
            m = re.match(r"ADD COLUMN IF NOT EXISTS (\w+) (.*)$", part, re.S)
            if m:
                out[m.group(1)] = norm_sql(m.group(2))
    return out


def column_spec(definition: str) -> Tuple[str, bool, Optional[str]]:
    """(type, NOT NULL, DEFAULT expression) of a normalised column definition."""
    blank = blank_literals(definition)
    col_type = definition.split()[0]
    not_null = bool(re.search(r"\bNOT NULL\b|\bPRIMARY KEY\b", blank))
    m = re.search(r"\bDEFAULT (\S+)", blank)
    default = definition[m.start(1):m.end(1)] if m else None
    return col_type, not_null, default


def _comment_text(sql: str, target: str) -> Optional[str]:
    """The text of the last `COMMENT ON <target> IS '...' '...';` (adjacent literals joined)."""
    found = None
    for _, stmt in statements(sql, rf"^COMMENT ON {re.escape(target)} IS\b"):
        literals = re.findall(r"'((?:[^']|'')*)'", stmt[len(f"COMMENT ON {target} IS"):])
        found = "".join(literals).replace("''", "'")
    return found


# ── A. the closed vocabularies equal stake_contracts ────────────────────────────────────────

_CLOSED_LISTS = (
    # (constraint, column, the stake_contracts tuple it mirrors)
    ("trillion_club_stakes_origin_check", "origin", "ORIGINS"),
    ("trillion_club_stake_findings_source_check", "source", "FINDING_SOURCES"),
    ("trillion_club_stake_findings_outcome_check", "outcome", "FINDING_OUTCOMES"),
    ("trillion_club_stake_findings_tier_check", "tier", "FINDING_TIERS"),
    ("trillion_club_stake_findings_status_check", "status", "FINDING_STATUSES"),
    ("trillion_club_stake_findings_decision_check", "owner_decision", "OWNER_DECISIONS"),
    ("trillion_club_stake_findings_reason_check", "reason", "REASON_CODES"),
)


@pytest.mark.parametrize("constraint, column, contract", _CLOSED_LISTS, ids=[x[0] for x in _CLOSED_LISTS])
def test_every_closed_list_equals_its_contract_tuple(constraint, column, contract):
    live = _live()
    assert constraint in live, f"no live CHECK named {constraint}"
    got = in_list(live[constraint], column)
    want = tuple(getattr(c, contract))
    assert got == want, (
        f"{constraint} != stake_contracts.{contract}\n"
        f"  only in SQL:    {sorted(set(got) - set(want))}\n"
        f"  only in Python: {sorted(set(want) - set(got))}\n"
        f"  (same members in a different order: {set(got) == set(want)})"
    )


def test_the_closed_lists_are_plain_tokens_without_duplicates():
    for _constraint, _column, contract in _CLOSED_LISTS:
        values = tuple(getattr(c, contract))
        assert len(values) == len(set(values)), f"{contract} repeats a value"
        bad = [v for v in values if not re.fullmatch(r"[a-z0-9_]{1,40}", v)]
        assert not bad, f"{contract}: {bad} would need quoting in the CHECK"
    assert set(c.MACHINE_ORIGINS) < set(c.ORIGINS) and c.ORIGIN_SEED not in c.MACHINE_ORIGINS
    assert c.STATUS_NEW in c.FINDING_STATUSES
    assert c.TERMINAL_STATUSES <= set(c.FINDING_STATUSES) and c.TOMBSTONE_STATUSES <= c.TERMINAL_STATUSES


# ── B. every rule, exactly ──────────────────────────────────────────────────────────────────

#: The prefix every machine row's source_url must carry, derived from the URL builder itself.
_SEC_PREFIX = sn.archive_url("1045810", "0001045810-26-000062", "primary_doc.xml").split("/data/")[0] + "/data/"
_SEC_URL_RE = "^" + re.escape(_SEC_PREFIX)


def _expected_rules() -> Dict[str, str]:
    machine = _sql_list(c.MACHINE_ORIGINS)
    auto = c.ORIGIN_AUTO
    kinds = _sql_list((sn.KIND_ON_13F_NOTE, sn.KIND_US_LISTED_OFF_13F))
    cik = "~ '^[0-9]{10}$'"
    return {
        # trillion_club_stakes
        "trillion_club_stakes_origin_check": f"origin IN ({_sql_list(c.ORIGINS)})",
        "trillion_club_stakes_investee_cik_check": f"investee_cik {cik} AND investee_cik <> '0000000000'",
        "trillion_club_stake_finding_iff_machine": f"(finding_id IS NOT NULL) = (origin IN ({machine}))",
        "trillion_club_stake_withdrawn_never_published": "NOT (published AND withdrawn_at IS NOT NULL)",
        "trillion_club_stake_machine_shape": (
            f"origin NOT IN ({machine}) OR (background IS NULL AND NOT tied_to_deal "
            f"AND source_confidence = 'primary' AND source_url ~ '{_SEC_URL_RE}')"),
        "trillion_club_stake_auto_scope": (
            f"origin <> '{auto}' OR owner_kept_at IS NOT NULL OR (NOT material AND kind IN ({kinds}) "
            "AND disclosed_value_usd IS NULL AND value_basis IS NULL)"),
        "trillion_club_stake_kept_only_auto": f"owner_kept_at IS NULL OR origin = '{auto}'",
        # trillion_club_stake_findings
        "trillion_club_stake_findings_source_check": f"source IN ({_sql_list(c.FINDING_SOURCES)})",
        "trillion_club_stake_findings_tier_check": f"tier IN ({_sql_list(c.FINDING_TIERS)})",
        "trillion_club_stake_findings_outcome_check": f"outcome IN ({_sql_list(c.FINDING_OUTCOMES)})",
        "trillion_club_stake_findings_reason_check": f"reason IN ({_sql_list(c.REASON_CODES)})",
        "trillion_club_stake_findings_status_check": f"status IN ({_sql_list(c.FINDING_STATUSES)})",
        "trillion_club_stake_findings_decision_check": (
            f"owner_decision IS NULL OR owner_decision IN ({_sql_list(c.OWNER_DECISIONS)})"),
        "trillion_club_stake_findings_decision_dated": "(owner_decision IS NULL) = (decided_at IS NULL)",
        "trillion_club_stake_findings_accession_check": "accession ~ '^[0-9]{10}-[0-9]{2}-[0-9]{6}$'",
        "trillion_club_stake_findings_cik_check": (
            f"filer_cik {cik} AND filer_cik <> '0000000000' AND (investee_cik IS NULL "
            f"OR (investee_cik {cik} AND investee_cik <> '0000000000'))"),
        "trillion_club_stake_findings_json_shapes": (
            "(proposed_row IS NULL OR (jsonb_typeof(proposed_row) = 'object' "
            "AND octet_length(proposed_row::text) <= 16384)) "
            "AND CASE WHEN jsonb_typeof(verified_spans) = 'array' "
            "THEN jsonb_array_length(verified_spans) <= 100 AND octet_length(verified_spans::text) <= 65536 "
            "ELSE FALSE END"),
        "trillion_club_stake_findings_sha_check": "fact_sha IS NULL OR fact_sha ~ '^[0-9a-f]{64}$'",
        "trillion_club_stake_findings_text_caps": (
            "char_length(form) BETWEEN 1 AND 40 AND char_length(document) BETWEEN 1 AND 255 "
            "AND source_url ~ '^https://' AND char_length(source_url) <= 500 "
            "AND (last_error IS NULL OR char_length(last_error) <= 2000)"),
        "trillion_club_stake_findings_ranges": (
            "attempts >= 0 AND (telegram_message_id IS NULL OR telegram_message_id > 0)"),
    }


#: Named CHECKs 175 put on the stakes table under the same prefix (pinned by the 175 tests).
_175_RULES = frozenset({"trillion_club_stake_value_has_basis"})


def test_205_declares_exactly_these_rules():
    assert set(named_checks(_sql())) == set(_expected_rules())


def test_the_live_rules_are_exactly_these_word_for_word():
    """Exact, not a substring: `NOT IN` vs `IN`, an OR for an AND or a dropped conjunct each
    pass a substring guard (review of the fix pass, 2026-10-15). Read from the LIVE definition
    (the last one in apply order), so the follow-up migration that relaxes the auto scope for
    the 8-K closing tier updates `_expected_rules` here — and its probes in section E."""
    live = _live()
    got = {k: norm_sql(v) for k, v in live.items() if k not in _175_RULES}
    want = {k: norm_sql(v) for k, v in _expected_rules().items()}
    assert set(got) == set(want), (
        f"a later migration added {sorted(set(got) - set(want))} or dropped "
        f"{sorted(set(want) - set(got))}: pin the live rule set here")
    wrong = {k: (got[k], want[k]) for k in want if got[k] != want[k]}
    assert not wrong, "\n".join(f"{k}:\n  got  {g}\n  want {w}" for k, (g, w) in wrong.items())


def test_no_ledger_rule_mentions_stake_id():
    """stake_id is ON DELETE SET NULL: any CHECK that reads it can refuse the owner's Studio
    DELETE of a stake row (the SET NULL is an UPDATE of the finding, and CHECKs re-run)."""
    rules = {k: v for k, v in named_checks(_sql()).items() if k.startswith("trillion_club_stake_findings_")}
    assert len(rules) >= 12, sorted(rules)
    assert [k for k, v in rules.items() if re.search(r"\bstake_id\b", v)] == []


def test_the_sec_url_rule_accepts_what_the_finder_builds_and_nothing_like_it():
    from app.integrations import sec_edgar  # lazily: a concurrent edit there must not hide the rest

    rule = re.compile(_SEC_URL_RE)
    built = [
        sn.archive_url("0001045810", "0001045810-26-000062", "primary_doc.xml", "xslSCHEDULE_13G_X01"),
        sn.archive_url("59478", "0001193125-26-013469", "primary_doc.xml", "xslSCHEDULE_13D_X02"),
        sn.archive_url("2088896", "0001104659-26-092071", "d123456d8k.htm"),
        sec_edgar.filing_index_url("0001045810", "0001045810-26-000062"),
    ]
    for url in built:
        assert url and rule.match(url), url
    for seed_row in json.loads(SEED_JSON.read_text(encoding="utf-8"))["stakes"]:
        if "Schedule 13" in seed_row["source_title"]:
            assert rule.match(seed_row["source_url"]), seed_row["source_url"]
    for lookalike in (
        "http://www.sec.gov/Archives/edgar/data/1045810/x.htm",
        "https://sec.gov/Archives/edgar/data/1045810/x.htm",
        "https://www.sec.gov.example.com/Archives/edgar/data/1/x.htm",
        "https://wwwXsecXgov/Archives/edgar/data/1/x.htm",          # the dots are escaped
        "https://www.sec.gov/Archives/edgar/datax/1/x.htm",
        "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany",
        "https://nvidianews.nvidia.com/news/nvidia-meta",
        " https://www.sec.gov/Archives/edgar/data/1/x.htm",
    ):
        assert not rule.match(lookalike), lookalike


def test_the_cik_and_accession_rules_agree_with_the_sec_normalisers():
    """The ledger stores what sec_edgar.normalize_cik / normalize_accession return. Postgres'
    `^...$` anchors the whole string (no end-of-line exception), emulated with fullmatch."""
    from app.integrations import sec_edgar

    def cik_ok(v: str) -> bool:
        return bool(re.fullmatch(r"[0-9]{10}", v)) and v != "0000000000"

    def acc_ok(v: str) -> bool:
        return bool(re.fullmatch(r"[0-9]{10}-[0-9]{2}-[0-9]{6}", v))

    # Everything the normalisers return passes the SQL rule ...
    for raw in (320193, "320193", "0000320193", " 59478 ", 1824723, "0000320193\n"):
        assert cik_ok(sec_edgar.normalize_cik(raw)), raw
    for raw in ("0001045810-26-000062", "000104581026000062", " 0001045810-26-000062 "):
        assert acc_ok(sec_edgar.normalize_accession(raw)), raw
    # ... what they refuse, the rule refuses too ...
    for refused in ("0", "0000000000", "12345678901", "abc", "１２３"):
        assert sec_edgar.normalize_cik(refused) is None and not cik_ok(refused), refused
    for refused in ("0001045810-26-00006", "0001045810_26_000062", "abc"):
        assert sec_edgar.normalize_accession(refused) is None and not acc_ok(refused), refused
    # ... and a RAW value the normaliser would have fixed is refused: the finder must write the
    # normalised form (unpadded CIK, undashed accession, a stray newline).
    for raw in ("320193", "0000320193\n"):
        assert not cik_ok(raw), raw
    for raw in ("000104581026000062", "0001045810-26-000062\n"):
        assert not acc_ok(raw), raw


# ── C. idempotency and statement shapes ─────────────────────────────────────────────────────

_ALLOWED_STATEMENTS = (
    r"BEGIN$", r"COMMIT$",
    r"ALTER TABLE public\.\w+ ",
    r"CREATE TABLE IF NOT EXISTS public\.\w+ \(",
    r"CREATE INDEX IF NOT EXISTS \w+ ON public\.\w+ \(",
    r"COMMENT ON (?:TABLE|COLUMN) public\.[\w.]+ IS '",
    r'DROP POLICY IF EXISTS "\w+" ON public\.\w+$',
    r'CREATE POLICY "\w+" ON public\.\w+ ',
    r"REVOKE ALL ON public\.\w+ FROM anon, authenticated$",
    r"GRANT ALL ON public\.\w+ TO service_role$",
    r"INSERT INTO public\.notification_job_state \(",
)
_ALTER_SUBCOMMANDS = (
    r"ADD COLUMN IF NOT EXISTS \w+ ", r"DROP CONSTRAINT IF EXISTS \w+$", r"ADD CONSTRAINT \w+ CHECK \(",
    r"ENABLE ROW LEVEL SECURITY$",
)


def idempotency_problems(sql: str) -> List[str]:
    """Every reason a second apply of `sql` (comment-stripped) would fail or change something."""
    problems: List[str] = []
    stmts = [s for _, s in statements(sql)]
    if not stmts or stmts[0] != "BEGIN":
        problems.append("the first statement is not BEGIN")
    if not stmts or stmts[-1] != "COMMIT":
        problems.append("the last statement is not COMMIT")
    dropped: set = set()
    policies: set = set()
    for st in stmts:
        flat = blank_literals(st)
        short = st[:90]
        if not any(re.match(p, st) for p in _ALLOWED_STATEMENTS):
            problems.append(f"a statement this guard does not know: {short}")
        if re.search(r"\bNOT VALID\b", flat, re.I):
            problems.append(f"NOT VALID skips the existing rows: {short}")
        if re.match(r"(?:UPDATE|DELETE|TRUNCATE)\b", flat, re.I) or re.search(
                r"\bDROP\s+(?:TABLE|COLUMN|INDEX|FUNCTION|TRIGGER|VIEW)\b", flat, re.I):
            problems.append(f"a destructive statement: {short}")
        if re.match(r"CREATE\s+(?:OR\s+REPLACE\s+)?(?:FUNCTION|TRIGGER|PROCEDURE)\b", flat, re.I):
            problems.append(f"205 defines no function or trigger: {short}")
        if re.match(r"CREATE TABLE\b", flat, re.I) and not st.startswith("CREATE TABLE IF NOT EXISTS"):
            problems.append(f"CREATE TABLE without IF NOT EXISTS: {short}")
        if re.match(r"CREATE (?:UNIQUE )?INDEX\b", flat, re.I) and "IF NOT EXISTS" not in flat:
            problems.append(f"CREATE INDEX without IF NOT EXISTS: {short}")
        m = re.match(r"ALTER TABLE (public\.\w+) (.*)$", st, re.S)
        if m:
            table = m.group(1)
            for part in split_top_level(m.group(2)):
                if not any(re.match(p, part) for p in _ALTER_SUBCOMMANDS):
                    problems.append(f"{table}: a sub-command this guard does not know (or not "
                                    f"idempotent): {part[:70]}")
                drop = re.match(r"DROP CONSTRAINT IF EXISTS (\w+)$", part)
                if drop:
                    dropped.add((table, drop.group(1)))
                add = re.match(r"ADD CONSTRAINT (\w+) ", part)
                if add and (table, add.group(1)) not in dropped:
                    problems.append(f"{table}: ADD CONSTRAINT {add.group(1)} with no earlier "
                                    f"DROP CONSTRAINT IF EXISTS {add.group(1)} — a replay fails 42710")
        pol = re.match(r'DROP POLICY IF EXISTS "(\w+)" ON (public\.\w+)$', st)
        if pol:
            policies.add(pol.groups())
        pol = re.match(r'CREATE POLICY "(\w+)" ON (public\.\w+) ', st)
        if pol and pol.groups() not in policies:
            problems.append(f"CREATE POLICY {pol.group(1)} with no earlier DROP POLICY IF EXISTS")
        if st.startswith("INSERT INTO") and not st.endswith("ON CONFLICT (job) DO NOTHING"):
            problems.append(f"an INSERT a replay would repeat or rewrite: {short}")
    return problems


def test_every_statement_in_205_is_safe_to_replay():
    assert idempotency_problems(_sql()) == []
    assert len(statements(_sql())) >= 30, "the statement reader has rotted"


@pytest.mark.parametrize("bad, expect", [
    ("ALTER TABLE public.t ADD COLUMN c TEXT;", "does not know (or not idempotent)"),
    ("ALTER TABLE public.t ADD CONSTRAINT k CHECK (true);", "no earlier DROP CONSTRAINT"),
    ("ALTER TABLE public.t ADD CHECK (true);", "does not know (or not idempotent)"),
    ("ALTER TABLE public.t DROP CONSTRAINT IF EXISTS k;\nALTER TABLE public.u ADD CONSTRAINT k CHECK (true);",
     "public.u: ADD CONSTRAINT k"),
    ("ALTER TABLE public.t DROP CONSTRAINT IF EXISTS k;\nALTER TABLE public.t ADD CONSTRAINT k CHECK (true) NOT VALID;",
     "NOT VALID"),
    ("ALTER TABLE public.t DROP COLUMN c;", "destructive"),
    ("ALTER TABLE public.t ALTER COLUMN c SET NOT NULL;", "does not know"),
    ("CREATE TABLE public.t (id int);", "CREATE TABLE without IF NOT EXISTS"),
    ("CREATE INDEX i ON public.t (id);", "does not know"),
    ('CREATE POLICY "p" ON public.t FOR ALL TO service_role USING (true);', "no earlier DROP POLICY"),
    ("INSERT INTO public.notification_job_state (job, enabled, updated_at) VALUES ('x', TRUE, NOW()) "
     "ON CONFLICT (job) DO UPDATE SET enabled = TRUE;", "a replay would repeat or rewrite"),
    ("INSERT INTO public.notification_job_state (job, enabled, updated_at) VALUES ('x', TRUE, NOW());",
     "a replay would repeat or rewrite"),
    ("DELETE FROM public.t;", "destructive"),
    ("UPDATE public.trillion_club_stakes SET origin = 'seed';", "destructive"),
    ("GRANT ALL ON public.t TO anon;", "does not know"),
])
def test_the_replay_guard_names_each_unsafe_shape(bad, expect):
    problems = idempotency_problems(strip_sql_comments(f"BEGIN;\n{bad}\nCOMMIT;"))
    assert any(expect in p for p in problems), problems


def test_the_replay_guard_reads_code_not_comments_and_needs_the_transaction():
    assert idempotency_problems("BEGIN;\nCOMMIT;") == []
    commented = strip_sql_comments("BEGIN;\n-- ALTER TABLE public.t ADD COLUMN c TEXT;\nCOMMIT;")
    assert idempotency_problems(commented) == []
    assert "the first statement is not BEGIN" in idempotency_problems("COMMIT;")
    assert "the last statement is not COMMIT" in idempotency_problems("BEGIN;")


def test_the_file_layout_follows_204():
    raw = _raw()
    assert raw.startswith("-- 205_trillion_club_stake_finder.sql\n")
    head = raw[: raw.index("\nBEGIN;\n")]
    assert all(line.startswith("--") or not line.strip() for line in head.splitlines()), \
        "everything before BEGIN must be the header comment"
    for phrase in ("-- Why:", "⚠️ Apply BEFORE deploying", "Idempotent:", "Valves that need no deploy",
                   "stake_contracts.py", "tests/test_trillion_club_stake_migration.py"):
        assert phrase in head, phrase
    tail = raw[raw.rindex("\nCOMMIT;\n") + len("\nCOMMIT;\n"):]
    assert all(line.startswith("--") or not line.strip() for line in tail.splitlines()), \
        "nothing may run after COMMIT: the VERIFY block is comments only"
    assert "-- VERIFY" in tail
    assert len([f for f in MIGRATIONS.glob("205_*.sql")]) == 1, "two migrations numbered 205"


def test_the_verify_block_checks_every_stakes_rule_and_the_job_row():
    raw = _raw()
    tail = raw[raw.rindex("\nCOMMIT;\n"):]
    verify = "\n".join(line[2:] for line in tail.splitlines() if line.startswith("--"))
    m = re.search(r"conname IN \(([^)]*)\)", verify, re.S)
    assert m, "the VERIFY block no longer counts the stakes constraints"
    stakes_rules = {k for k in named_checks(_sql()) if not k.startswith("trillion_club_stake_findings_")}
    assert set(_quoted(m.group(1))) == stakes_rules
    assert re.search(rf"\)\) = {len(stakes_rules)},", verify), "the VERIFY count is not the rule count"
    assert f"job = '{JOB}'" in verify and FINDINGS in verify


# ── D. columns, keys, grants, the job row, bookkeeping ──────────────────────────────────────

_LEDGER = {
    # column: (type, NOT NULL, DEFAULT)
    "id": ("UUID", True, "gen_random_uuid()"),
    "company_slug": ("TEXT", True, None),
    "source": ("TEXT", True, None),
    "accession": ("TEXT", True, None),
    "filer_cik": ("TEXT", True, None),
    "investee_cik": ("TEXT", False, None),
    "form": ("TEXT", True, None),
    "filed_on": ("DATE", True, None),
    "document": ("TEXT", True, None),
    "source_url": ("TEXT", True, None),
    "tier": ("TEXT", True, None),
    "outcome": ("TEXT", True, None),
    "reason": ("TEXT", True, None),
    "proposed_row": ("JSONB", False, None),
    "verified_spans": ("JSONB", True, "'[]'::jsonb"),
    "fact_sha": ("TEXT", False, None),
    "stake_id": ("UUID", False, None),
    "status": ("TEXT", True, f"'{c.STATUS_NEW}'"),
    "telegram_message_id": ("BIGINT", False, None),
    "notified_at": ("TIMESTAMPTZ", False, None),
    # When the row went live (every move to 'published'): gate 8 counts auto publishes by it,
    # never by created_at (review B WO-1). Nullable; no rule reads it.
    "published_at": ("TIMESTAMPTZ", False, None),
    "owner_decision": ("TEXT", False, None),
    "decided_at": ("TIMESTAMPTZ", False, None),
    "attempts": ("INTEGER", True, "0"),
    # Not in the plan's column list: an `error` finding's text, so a retry is diagnosable from
    # the row (204's last_error convention). Nullable; nothing requires it.
    "last_error": ("TEXT", False, None),
    "created_at": ("TIMESTAMPTZ", True, "NOW()"),
    "updated_at": ("TIMESTAMPTZ", True, "NOW()"),
}
_STAKE_ADDS = {
    "origin": ("TEXT", True, f"'{c.ORIGIN_SEED}'"),
    "owner_kept_at": ("TIMESTAMPTZ", False, None),
    "withdrawn_at": ("TIMESTAMPTZ", False, None),
    "investee_cik": ("TEXT", False, None),
    "finding_id": ("UUID", False, None),
}


def test_the_ledger_columns_types_and_defaults():
    cols = ledger_columns(_sql())
    assert {k: column_spec(v) for k, v in cols.items()} == _LEDGER


def test_the_stakes_columns_205_adds():
    added = stake_columns_added(_sql())
    assert {k: column_spec(v) for k, v in added.items()} == _STAKE_ADDS
    # finding_id is a soft pointer: a circular FK would make re-applying 205 non-idempotent.
    assert "REFERENCES" not in added["finding_id"]


def test_the_keys_foreign_keys_and_indexes():
    sql = _sql()
    cols = ledger_columns(sql)
    assert cols["id"] == "UUID PRIMARY KEY DEFAULT gen_random_uuid()"
    assert cols["company_slug"] == (f"TEXT NOT NULL REFERENCES public.{COMPANIES} (slug) "
                                    "ON DELETE CASCADE ON UPDATE CASCADE")
    assert cols["stake_id"] == f"UUID REFERENCES public.{STAKES} (id) ON DELETE SET NULL"
    assert [k for k, v in cols.items() if "REFERENCES" in v] == ["company_slug", "stake_id"]
    body = norm_sql(_create_table_body(sql, FINDINGS))
    assert ("CONSTRAINT trillion_club_stake_findings_company_slug_accession_key "
            "UNIQUE (company_slug, accession)") in body
    assert len(re.findall(r"\bUNIQUE\b", blank_literals(body))) == 1
    for name, cols_ in (("idx_trillion_club_stake_findings_status_notified", "status, notified_at"),
                        ("idx_trillion_club_stake_findings_company_investee", "company_slug, investee_cik")):
        assert f"CREATE INDEX IF NOT EXISTS {name} ON public.{FINDINGS} ({cols_})" in \
            [s for _, s in statements(sql)], name


def test_the_ledger_is_service_role_only():
    stmts = [s for _, s in statements(_sql())]
    assert f"ALTER TABLE public.{FINDINGS} ENABLE ROW LEVEL SECURITY" in stmts
    assert f'DROP POLICY IF EXISTS "{FINDINGS}_service_all" ON public.{FINDINGS}' in stmts
    assert f"REVOKE ALL ON public.{FINDINGS} FROM anon, authenticated" in stmts
    # Exactly one policy and one grant in the whole file, both to service_role: a second policy
    # with no TO clause would apply to PUBLIC, and 205 has no reason to grant anything else.
    assert [s for s in stmts if s.startswith("CREATE POLICY")] == [
        f'CREATE POLICY "{FINDINGS}_service_all" ON public.{FINDINGS} FOR ALL TO service_role '
        "USING (true) WITH CHECK (true)"]
    assert [s for s in stmts if s.startswith("GRANT")] == [f"GRANT ALL ON public.{FINDINGS} TO service_role"]
    assert [s for s in stmts if s.startswith("REVOKE")] == [f"REVOKE ALL ON public.{FINDINGS} FROM anon, authenticated"]


def test_the_job_row_is_seeded_enabled_and_a_replay_never_re_enables_it():
    stmts = [s for _, s in statements(_sql()) if s.startswith("INSERT INTO")]
    assert stmts == [
        "INSERT INTO public.notification_job_state (job, enabled, updated_at) "
        f"VALUES ('{JOB}', TRUE, NOW()) ON CONFLICT (job) DO NOTHING"
    ]


def test_the_job_row_is_the_schedulers_job_name():
    from app.services.trillion_club import scheduler

    names = sorted({v for k, v in vars(scheduler).items()
                    if k.startswith("JOB_") and isinstance(v, str) and "stake" in v})
    if not names:
        pytest.skip("the scheduler's stake-finder job constant lands with the finder (phase 2); "
                    f"it must equal {JOB!r}, the row 205 seeds")
    assert names == [JOB]


def test_the_comments_say_where_a_stake_comes_from():
    sql = _sql()
    stakes = _comment_text(sql, f"TABLE public.{STAKES}")
    assert stakes and SOURCE_PHRASE in stakes, stakes
    assert _comment_text(sql, f"TABLE public.{FINDINGS}"), "the ledger has no COMMENT ON TABLE"
    for col in _STAKE_ADDS:
        assert _comment_text(sql, f"COLUMN public.{STAKES}.{col}"), f"{STAKES}.{col} has no comment"
    # 205's comment is the one the migrations leave (the atlas prefers it over curation).
    last = None
    for _fname, raw in migration_sources():
        last = _comment_text(strip_sql_comments(raw), f"TABLE public.{STAKES}") or last
    assert last == stakes


def test_published_at_is_a_nullable_stamp_with_a_comment_that_says_what_counts_by_it():
    """Review B WO-1: gate 8 counts auto publishes by published_at (stamped on every move to published),
    never by created_at. No rule reads it, so a NULL (not live yet) is always allowed."""
    cols = ledger_columns(_sql())
    assert column_spec(cols["published_at"]) == ("TIMESTAMPTZ", False, None)
    order = list(cols)
    assert order.index("published_at") == order.index("notified_at") + 1
    text = _comment_text(_sql(), f"COLUMN public.{FINDINGS}.published_at")
    assert text and "created_at" in text and "published" in text, text
    assert not [k for k, v in named_checks(_sql()).items() if "published_at" in v], "no rule binds it"


def test_the_grants_test_declares_the_ledger_service_role_only():
    import test_table_grants_service_role_only as grants

    assert grants._SERVICE_ROLE_ONLY["205"] == (FINDINGS,)
    everywhere = [t for group in grants._SERVICE_ROLE_ONLY.values() for t in group]
    assert everywhere.count(FINDINGS) == 1 and FINDINGS not in grants._CLIENT_ACCESS_BY_DESIGN
    state, created, _, _ = grants._replay()
    assert created[FINDINGS] == 205
    for role in ("anon", "authenticated"):
        assert state[(FINDINGS, role)] == ("revoked", 205), role


def _literal_assign(path: Path, name: str) -> Any:
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        target = node.target if isinstance(node, ast.AnnAssign) else (
            node.targets[0] if isinstance(node, ast.Assign) and len(node.targets) == 1 else None)
        if isinstance(target, ast.Name) and target.id == name:
            return ast.literal_eval(node.value)
    raise AssertionError(f"{name} not found in {path.name}")


def test_the_atlas_lists_the_ledger_as_pending_until_it_is_dumped():
    pending = _literal_assign(BACKEND / "tests" / "test_schema_doc_generator.py", "_PENDING_MIGRATION_TABLES")
    dumped = bool(re.search(rf"^CREATE TABLE public\.{FINDINGS} \(", SNAPSHOT.read_text(encoding="utf-8"), re.M))
    if dumped:
        assert f"public.{FINDINGS}" not in pending, "205 was applied and re-dumped: drop the pending entry"
    else:
        assert f"public.{FINDINGS}" in pending, "a written-not-applied table must be listed as pending"


def test_the_ledger_is_curated_and_the_stakes_entry_keeps_its_175_key():
    sys.path.insert(0, str(BACKEND / "scripts"))
    try:
        import schema_curation as cur
    finally:
        sys.path.remove(str(BACKEND / "scripts"))
    doc = cur.CURATION[f"public.{FINDINGS}"]
    assert doc.domain == cur.CURATION[f"public.{STAKES}"].domain and doc.domain in cur.DOMAIN_ORDER
    assert doc.purpose and "205" in doc.note
    cols = set(ledger_columns(_sql()))
    assert doc.key and set(doc.key) <= cols, set(doc.key) - cols
    stakes = cur.CURATION[f"public.{STAKES}"]
    assert "175" in stakes.note and "205" in stakes.note
    # The 175 key is unchanged: until 205 is dumped, a 205 column there would fail the atlas'
    # stale-column guard (the stakes table is NOT pending).
    assert not set(stakes.key) & set(_STAKE_ADDS), stakes.key
    to, to_col, why = cur.IMPLICIT_REFS[(f"public.{STAKES}", "finding_id")]
    assert (to, to_col) == (f"public.{FINDINGS}", "id") and why


def test_the_env_example_documents_the_four_switches_commented_out_at_their_defaults():
    """205's header says nothing writes while TRILLION_CLUB_STAKE_FINDER_ENABLED is False (the
    default). The example lists the switches commented out, at the real defaults, so copying it
    to .env changes nothing."""
    text = ENV_EXAMPLE.read_text(encoding="utf-8")
    parsed = dotenv_values(ENV_EXAMPLE)
    fields = Settings.model_fields
    for name, shown in (("TRILLION_CLUB_STAKE_FINDER_ENABLED", "false"),
                        ("TRILLION_CLUB_STAKE_AUTO_TIERS", ""),
                        ("TRILLION_CLUB_STAKE_AUTO_SINCE", ""),
                        ("TRILLION_CLUB_STAKE_LOOKBACK_DAYS", "45")):
        assert name not in parsed, f"{name} is live in .env.example — it must stay commented out"
        m = re.search(rf"^# {name}=(.*)$", text, re.M)
        assert m, f"{name} is not documented in .env.example"
        assert m.group(1).strip() == shown, (name, m.group(1))
        assert name in fields, name
        assert str(fields[name].default).lower() == shown.lower(), (name, fields[name].default)
        comment = text[: m.start()].rstrip("\n").splitlines()[-1]
        assert comment.startswith("# ") and "=" not in comment.split()[1], \
            f"{name} needs its one-line explanation right above it"


# ── E. a real Postgres decides ──────────────────────────────────────────────────────────────


def _first_error(output: str) -> str:
    """The line that says what went wrong (a FATAL / could-not line), not a mid-word tail."""
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    key = next((line for line in lines if "FATAL" in line or "could not" in line), None)
    return (key or " ".join(lines[-3:]))[:300]


def _pg_tool(name: str) -> Optional[str]:
    for cand in (shutil.which(name), f"/usr/local/bin/{name}", f"/opt/homebrew/bin/{name}",
                 f"/Applications/Postgres.app/Contents/Versions/latest/bin/{name}"):
        if cand and os.path.isfile(cand) and os.access(cand, os.X_OK):
            return cand
    return None


class _Cluster:
    """A throwaway cluster on a private Unix socket (`listen_addresses=''`: no TCP at all)."""

    PORT = "5441"   # only names the socket file inside the private directory

    def __init__(self) -> None:
        self.tools = {n: _pg_tool(n) for n in ("initdb", "pg_ctl", "psql")}
        self.root = tempfile.mkdtemp(prefix="tc205")   # short: a socket path is capped at 103 bytes
        self.data = os.path.join(self.root, "data")
        self.env = {k: v for k, v in os.environ.items() if not k.startswith("PG")}
        self.started = False

    def start(self) -> Optional[str]:
        if not all(self.tools.values()):
            return "PostgreSQL tools (initdb, pg_ctl, psql) are not installed"
        if len(os.path.join(self.root, f".s.PGSQL.{self.PORT}")) > 100:
            return "temp dir too long for a Unix socket"
        for loc in ("en_US.UTF-8", "C.UTF-8", "C"):
            self.env["LC_ALL"] = self.env["LANG"] = loc   # the macOS postmaster needs LC_ALL
            r = subprocess.run([self.tools["initdb"], "-D", self.data, "-A", "trust", "-U", "postgres",
                                "--no-sync", "-E", "UTF8", f"--locale={loc}"],
                               env=self.env, capture_output=True, text=True, timeout=180)
            if r.returncode == 0:
                break
            shutil.rmtree(self.data, ignore_errors=True)
        else:
            return f"initdb failed: {_first_error(r.stderr + r.stdout)}"
        opts = (f"-c listen_addresses='' -k '{self.root}' -p {self.PORT} -c fsync=off "
                "-c full_page_writes=off -c synchronous_commit=off")
        r = subprocess.run([self.tools["pg_ctl"], "-D", self.data, "-l", os.path.join(self.root, "log"),
                            "-w", "-t", "60", "-o", opts, "start"],
                           env=self.env, capture_output=True, text=True, timeout=120)
        if r.returncode != 0:
            log = Path(self.root, "log")
            return f"pg_ctl start failed: {_first_error(r.stderr + (log.read_text(errors='replace') if log.exists() else ''))}"
        self.started = True
        return None

    def psql(self, sql: str, db: str = "postgres") -> subprocess.CompletedProcess:
        return subprocess.run([self.tools["psql"], "-h", self.root, "-p", self.PORT, "-U", "postgres",
                               "-d", db, "-X", "-q", "-At", "-v", "ON_ERROR_STOP=1"],
                              input=sql, env=self.env, capture_output=True, text=True, timeout=300)

    def ok(self, sql: str, db: str = "postgres") -> str:
        r = self.psql(sql, db)
        assert r.returncode == 0, r.stderr[-2000:]
        return r.stdout

    def stop(self) -> None:
        if self.started:
            subprocess.run([self.tools["pg_ctl"], "-D", self.data, "-m", "immediate", "stop"],
                           env=self.env, capture_output=True, text=True, timeout=60)
        shutil.rmtree(self.root, ignore_errors=True)


_SETUP_SQL = """
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN CREATE ROLE anon NOLOGIN; END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN CREATE ROLE authenticated NOLOGIN; END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'service_role') THEN CREATE ROLE service_role NOLOGIN; END IF;
END $$;
-- The WORST case for the grants: Supabase's stock default privileges, under which every table a
-- migration creates is born granted to the client roles (the project's snapshot holds none, but
-- a Supabase project is created with them). Only 205's own REVOKE can close the ledger then.
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO anon, authenticated;
CREATE TABLE IF NOT EXISTS public.notification_job_state (
    job text PRIMARY KEY, enabled boolean NOT NULL DEFAULT true, updated_at timestamptz);

-- Run one statement, report how it ended, and ALWAYS roll it back (a sub-transaction).
CREATE OR REPLACE FUNCTION public.m205_probe(tag text, stmt text) RETURNS void
LANGUAGE plpgsql AS $f$
DECLARE s text; con text; col text; m text; n bigint;
BEGIN
  BEGIN
    EXECUTE stmt;
    GET DIAGNOSTICS n = ROW_COUNT;
    RAISE EXCEPTION USING ERRCODE = 'P0001', MESSAGE = '__accepted__', DETAIL = n::text;
  EXCEPTION WHEN others THEN
    GET STACKED DIAGNOSTICS s = RETURNED_SQLSTATE, con = CONSTRAINT_NAME, col = COLUMN_NAME,
                            m = MESSAGE_TEXT;
    IF m = '__accepted__' THEN
      GET STACKED DIAGNOSTICS m = PG_EXCEPTION_DETAIL;
      RAISE NOTICE 'PROBE|%|ACCEPT|%', tag, m;
    ELSE
      RAISE NOTICE 'PROBE|%|REJECT|%|%|%|%', tag, s, con, col,
        replace(replace(m, E'\\n', ' '), '|', '/');
    END IF;
  END;
END $f$;
"""

F1, F2, F3, F4 = (f"00000000-0000-4000-8000-0000000000f{i}" for i in range(1, 5))
S1, S2, S3, S4 = (f"00000000-0000-4000-8000-0000000000a{i}" for i in range(1, 5))
F_PROBE = "00000000-0000-4000-8000-0000000000ff"
NOW = "2026-10-28T22:30:00+00:00"
_SEC_URL = "https://www.sec.gov/Archives/edgar/data/1045810/000104581026000099/xslSCHEDULE_13G_X01/primary_doc.xml"


def _json_lit(doc: Any) -> str:
    text = json.dumps(doc, ensure_ascii=True, allow_nan=False)
    assert "$J$" not in text and "$Q$" not in text
    return f"$J${text}$J$::json"


def _insert(table: str, doc: Mapping[str, Any]) -> str:
    """The INSERT PostgREST effectively makes: a missing key takes the DEFAULT, null is NULL."""
    cols = list(doc)
    assert all(re.fullmatch(r"[a-z][a-z0-9_]*", k) for k in cols), cols
    names = ", ".join(cols)
    return (f"INSERT INTO public.{table} ({names}) SELECT {names} "
            f"FROM json_populate_record(NULL::public.{table}, {_json_lit(dict(doc))})")


def _probe(tag: str, stmt: str) -> str:
    assert "$Q$" not in stmt and re.fullmatch(r"[a-z0-9:_-]+", tag), tag
    return f"SELECT public.m205_probe('{tag}', $Q${stmt}$Q$);"


def _stake(**over: Any) -> Dict[str, Any]:
    """A valid phase-1 auto row (unpublished), the shape the finder writes."""
    row: Dict[str, Any] = {
        "company_slug": "nvidia", "kind": sn.KIND_US_LISTED_OFF_13F, "investee_name": "Probe Holdings",
        "investee_cusip": None, "investee_us_symbol": "PRBH", "local_listing": None,
        "ownership_pct": 7.5, "ownership_basis": "of common stock", "disclosed_value_usd": None,
        "value_basis": None, "as_of": "2026-10-01", "source_title": "NVIDIA Schedule 13G (Oct 5, 2026)",
        "source_url": _SEC_URL, "source_confidence": "primary", "material": False, "tied_to_deal": False,
        "listed_since": None, "background": None, "verified_on": "2026-10-28", "published": False,
        "sort_order": 900, "origin": c.ORIGIN_AUTO, "finding_id": F_PROBE, "investee_cik": "0009999901",
        "owner_kept_at": None, "withdrawn_at": None,
    }
    row.update(over)
    return row


def _seed_style(**over: Any) -> Dict[str, Any]:
    """What scripts/seed_trillion_club.py inserts: the 21 hand-kept columns, no 205 column."""
    row = _stake(origin=None, background="Disclosed in its 10-Q.", tied_to_deal=True,
                 source_url="https://nvidianews.nvidia.com/news/probe")
    for k in ("origin", "finding_id", "investee_cik", "owner_kept_at", "withdrawn_at"):
        row.pop(k)
    row.update(over)
    return row


def _finding(**over: Any) -> Dict[str, Any]:
    row: Dict[str, Any] = {
        "company_slug": "nvidia", "source": c.SOURCE_SCH13, "accession": "0001045810-26-000099",
        "filer_cik": "0001045810", "investee_cik": "0009999901", "form": "SCHEDULE 13G",
        "filed_on": "2026-10-05", "document": "primary_doc.xml", "source_url": _SEC_URL,
        "tier": c.TIER_13DG, "outcome": c.OUTCOME_AUTO, "reason": "plain_13dg",
        "proposed_row": {"investee_name": "Probe Holdings"}, "verified_spans": [],
        "fact_sha": "a" * 64, "status": c.STATUS_NEW,
    }
    row.update(over)
    return row


def _fixture_rows() -> str:
    """Committed machine rows the UPDATE probes and the scenarios act on: S1 a published auto
    row, S2 a kept (and since then material) auto row, S3 a published proposal with a value,
    S4 a withdrawn auto row — each with its finding."""
    acc = "0001045810-26-0001{:02d}"
    findings = [
        _finding(id=F1, accession=acc.format(1), status=c.STATUS_PUBLISHED),
        _finding(id=F2, accession=acc.format(2), status=c.STATUS_KEPT,
                 owner_decision=c.DECISION_KEPT, decided_at=NOW),
        _finding(id=F3, accession=acc.format(3), source=c.SOURCE_EFTS, form="8-K", document="d8k.htm",
                 tier=c.TIER_SEC_AGREEMENT, outcome=c.OUTCOME_PROPOSAL, reason="t2_agreement",
                 status=c.STATUS_PUBLISHED, owner_decision=c.DECISION_PUBLISHED, decided_at=NOW),
        _finding(id=F4, accession=acc.format(4), status=c.STATUS_WITHDRAWN,
                 owner_decision=c.DECISION_WITHDRAWN, decided_at=NOW),
    ]
    stakes = [
        _stake(id=S1, finding_id=F1, investee_name="Probe Alpha", published=True),
        _stake(id=S2, finding_id=F2, investee_name="Probe Beta", published=True, owner_kept_at=NOW,
               material=True),
        _stake(id=S3, finding_id=F3, investee_name="Probe Gamma", origin=c.ORIGIN_PROPOSAL,
               kind=sn.KIND_COMMITMENT, investee_us_symbol=None, ownership_pct=None, ownership_basis=None,
               disclosed_value_usd=2e9, value_basis="committed_up_to", material=True, published=True),
        _stake(id=S4, finding_id=F4, investee_name="Probe Delta", withdrawn_at=NOW),
    ]
    links = [f"UPDATE public.{FINDINGS} SET stake_id = '{s}' WHERE id = '{f}'"
             for f, s in ((F1, S1), (F2, S2), (F3, S3), (F4, S4))]
    return "BEGIN;\n" + ";\n".join([_insert(FINDINGS, f) for f in findings]
                                    + [_insert(STAKES, s) for s in stakes] + links) + ";\nCOMMIT;"


def _upd(table: str, assignments: str, where: str) -> str:
    return f"UPDATE public.{table} SET {assignments} WHERE {where}"


_FIRST_SEED = (f"id = (SELECT id FROM public.{STAKES} WHERE origin = 'seed' AND published "
               "ORDER BY company_slug, investee_name, kind LIMIT 1)")


def _rej(constraint: str, sqlstate: str = "23514") -> Tuple[str, str, str]:
    return ("REJECT", sqlstate, constraint)


def _nn(column: str) -> Tuple[str, str, str]:
    return ("NOTNULL", "23502", column)


def _acc(rows: int = 1) -> Tuple[str, int]:
    return ("ACCEPT", rows)


# (tag, statement, expected verdict)
_STAKE_PROBES: List[Tuple[str, str, Tuple]] = [
    # accepted
    ("auto-plain", _insert(STAKES, _stake()), _acc()),
    ("auto-published", _insert(STAKES, _stake(published=True)), _acc()),
    ("auto-13f-note", _insert(STAKES, _stake(kind=sn.KIND_ON_13F_NOTE, investee_cusip="N97284108")), _acc()),
    ("auto-kept-lifts-the-scope", _insert(STAKES, _stake(
        owner_kept_at=NOW, material=True, kind=sn.KIND_PRIVATE, investee_us_symbol=None,
        disclosed_value_usd=1e9, value_basis="invested")), _acc()),
    ("auto-withdrawn-unpublished", _insert(STAKES, _stake(withdrawn_at=NOW)), _acc()),
    ("proposal-commitment-with-value", _insert(STAKES, _stake(
        origin=c.ORIGIN_PROPOSAL, kind=sn.KIND_COMMITMENT, investee_us_symbol=None, ownership_pct=None,
        ownership_basis=None, disclosed_value_usd=2e9, value_basis="committed_up_to", material=True,
        published=True)), _acc()),
    ("proposal-private", _insert(STAKES, _stake(origin=c.ORIGIN_PROPOSAL, kind=sn.KIND_PRIVATE,
                                                investee_us_symbol=None)), _acc()),
    ("seed-insert-without-205-columns", _insert(STAKES, _seed_style()), _acc()),
    ("seed-explicit-origin", _insert(STAKES, _seed_style(origin=c.ORIGIN_SEED, finding_id=None)), _acc()),
    ("auto-without-investee-cik", _insert(STAKES, _stake(investee_cik=None)), _acc()),
    # trillion_club_stake_machine_shape
    ("auto-background", _insert(STAKES, _stake(background="Disclosed in July.")),
     _rej("trillion_club_stake_machine_shape")),
    ("auto-non-sec-url", _insert(STAKES, _stake(source_url="https://nvidianews.nvidia.com/news/probe")),
     _rej("trillion_club_stake_machine_shape")),
    ("auto-sec-lookalike-host", _insert(STAKES, _stake(
        source_url="https://www.sec.gov.example.com/Archives/edgar/data/1/2/x.htm")),
     _rej("trillion_club_stake_machine_shape")),
    ("auto-unescaped-dot-lookalike", _insert(STAKES, _stake(
        source_url="https://wwwXsecXgov/Archives/edgar/data/1/2/x.htm")),
     _rej("trillion_club_stake_machine_shape")),
    ("auto-sec-not-archives", _insert(STAKES, _stake(
        source_url="https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany")),
     _rej("trillion_club_stake_machine_shape")),
    ("auto-tied-to-deal", _insert(STAKES, _stake(tied_to_deal=True)), _rej("trillion_club_stake_machine_shape")),
    ("auto-secondary", _insert(STAKES, _stake(source_confidence="secondary")),
     _rej("trillion_club_stake_machine_shape")),
    ("proposal-background", _insert(STAKES, _stake(origin=c.ORIGIN_PROPOSAL, background="x")),
     _rej("trillion_club_stake_machine_shape")),
    # trillion_club_stake_auto_scope
    ("auto-value", _insert(STAKES, _stake(disclosed_value_usd=1e9, value_basis="invested")),
     _rej("trillion_club_stake_auto_scope")),
    ("auto-basis-only", _insert(STAKES, _stake(value_basis="fair_value")), _rej("trillion_club_stake_auto_scope")),
    ("auto-material", _insert(STAKES, _stake(material=True)), _rej("trillion_club_stake_auto_scope")),
    ("auto-private", _insert(STAKES, _stake(kind=sn.KIND_PRIVATE, investee_us_symbol=None)),
     _rej("trillion_club_stake_auto_scope")),
    ("auto-commitment", _insert(STAKES, _stake(kind=sn.KIND_COMMITMENT)), _rej("trillion_club_stake_auto_scope")),
    ("auto-non-us", _insert(STAKES, _stake(kind="non_us_listed", local_listing="Taiwan (6770.TW)")),
     _rej("trillion_club_stake_auto_scope")),
    # trillion_club_stake_finding_iff_machine
    ("auto-without-finding", _insert(STAKES, _stake(finding_id=None)), _rej("trillion_club_stake_finding_iff_machine")),
    ("proposal-without-finding", _insert(STAKES, _stake(origin=c.ORIGIN_PROPOSAL, finding_id=None)),
     _rej("trillion_club_stake_finding_iff_machine")),
    ("seed-with-finding", _insert(STAKES, _seed_style(origin=c.ORIGIN_SEED, finding_id=F_PROBE)),
     _rej("trillion_club_stake_finding_iff_machine")),
    # trillion_club_stakes_origin_check / NOT NULL
    ("unknown-origin", _insert(STAKES, _stake(origin="manual", finding_id=None)),
     _rej("trillion_club_stakes_origin_check")),
    ("null-origin", _insert(STAKES, _stake(origin=None, finding_id=None)), _nn("origin")),
    # trillion_club_stake_withdrawn_never_published
    ("auto-published-and-withdrawn", _insert(STAKES, _stake(published=True, withdrawn_at=NOW)),
     _rej("trillion_club_stake_withdrawn_never_published")),
    ("seed-published-and-withdrawn", _insert(STAKES, _seed_style(published=True, withdrawn_at=NOW)),
     _rej("trillion_club_stake_withdrawn_never_published")),
    # trillion_club_stake_kept_only_auto
    ("proposal-kept", _insert(STAKES, _stake(origin=c.ORIGIN_PROPOSAL, owner_kept_at=NOW)),
     _rej("trillion_club_stake_kept_only_auto")),
    ("seed-kept", _insert(STAKES, _seed_style(owner_kept_at=NOW)), _rej("trillion_club_stake_kept_only_auto")),
    # trillion_club_stakes_investee_cik_check
    *[(f"stake-cik-{i}", _insert(STAKES, _stake(investee_cik=v)), _rej("trillion_club_stakes_investee_cik_check"))
      for i, v in enumerate(("1045810", "0000000000", "00010458101", "ABCDEFGHIJ", "0001045810\n",
                             "０００１０４５８１０"))],
]

_FINDING_PROBES: List[Tuple[str, str, Tuple]] = [
    ("finding-plain", _insert(FINDINGS, _finding()), _acc()),
    ("finding-efts-proposal", _insert(FINDINGS, _finding(
        source=c.SOURCE_EFTS, form="8-K", document="d123456d8k.htm", tier=c.TIER_SEC_CLOSING,
        outcome=c.OUTCOME_PROPOSAL, reason="trial", verified_spans=["x" * 120, "y" * 300])), _acc()),
    ("finding-heads-up-without-row", _insert(FINDINGS, _finding(
        tier=c.TIER_IOI, outcome=c.OUTCOME_HEADS_UP, reason="t3_indication", proposed_row=None,
        fact_sha=None, investee_cik=None)), _acc()),
    ("finding-bigint-message-id", _insert(FINDINGS, _finding(telegram_message_id=3_000_000_000)), _acc()),
    ("finding-100-spans", _insert(FINDINGS, _finding(verified_spans=["s" * 40] * 100)), _acc()),
    ("finding-span-objects", _insert(FINDINGS, _finding(
        verified_spans=[{"text": "x" * 80, "accession": "0001045810-26-000099"}])), _acc()),
    ("finding-decided", _insert(FINDINGS, _finding(owner_decision=c.DECISION_WITHDRAWN, decided_at=NOW)), _acc()),
    ("finding-linked-to-a-stake", _insert(FINDINGS, _finding(stake_id=S1)), _acc()),
    ("finding-same-filing-another-member", _insert(FINDINGS, _finding(
        company_slug="amd", accession="0001045810-26-000101")), _acc()),
    # every value of every closed list, and stake_id NULL with EVERY status
    *[(f"status-{v}", _insert(FINDINGS, _finding(status=v, stake_id=None)), _acc()) for v in c.FINDING_STATUSES],
    *[(f"tier-{v}", _insert(FINDINGS, _finding(tier=v)), _acc()) for v in c.FINDING_TIERS],
    *[(f"outcome-{v}", _insert(FINDINGS, _finding(outcome=v)), _acc()) for v in c.FINDING_OUTCOMES],
    *[(f"reason-{v}", _insert(FINDINGS, _finding(reason=v)), _acc()) for v in c.REASON_CODES],
    *[(f"source-{v}", _insert(FINDINGS, _finding(source=v)), _acc()) for v in c.FINDING_SOURCES],
    *[(f"decision-{v}", _insert(FINDINGS, _finding(owner_decision=v, decided_at=NOW)), _acc())
      for v in c.OWNER_DECISIONS],
    # refused
    ("finding-duplicate-filing", _insert(FINDINGS, _finding(accession="0001045810-26-000101")),
     _rej("trillion_club_stake_findings_company_slug_accession_key", "23505")),
    ("finding-unknown-company", _insert(FINDINGS, _finding(company_slug="no-such-co")),
     _rej("trillion_club_stake_findings_company_slug_fkey", "23503")),
    ("finding-unknown-stake", _insert(FINDINGS, _finding(stake_id=F_PROBE)),
     _rej("trillion_club_stake_findings_stake_id_fkey", "23503")),
    *[(f"finding-accession-{i}", _insert(FINDINGS, _finding(accession=v)),
       _rej("trillion_club_stake_findings_accession_check"))
      for i, v in enumerate(("000104581026000099", "0001045810-26-000099\n", "0001045810-26-00009",
                             "1045810-26-000099", "0001045810-2026-000099"))],
    *[(f"finding-cik-{i}", _insert(FINDINGS, _finding(**{col: v})), _rej("trillion_club_stake_findings_cik_check"))
      for i, (col, v) in enumerate((("filer_cik", "0000000000"), ("filer_cik", "1045810"),
                                    ("filer_cik", "00010458100"), ("investee_cik", "12345678901"),
                                    ("investee_cik", "0000000000"), ("investee_cik", "000999990X")))],
    ("finding-bad-status", _insert(FINDINGS, _finding(status="done")), _rej("trillion_club_stake_findings_status_check")),
    ("finding-bad-tier", _insert(FINDINGS, _finding(tier="13d")), _rej("trillion_club_stake_findings_tier_check")),
    ("finding-bad-outcome", _insert(FINDINGS, _finding(outcome="row")), _rej("trillion_club_stake_findings_outcome_check")),
    ("finding-bad-reason", _insert(FINDINGS, _finding(reason="plain")), _rej("trillion_club_stake_findings_reason_check")),
    ("finding-bad-source", _insert(FINDINGS, _finding(source="edgar")), _rej("trillion_club_stake_findings_source_check")),
    ("finding-bad-decision", _insert(FINDINGS, _finding(owner_decision="approved", decided_at=NOW)),
     _rej("trillion_club_stake_findings_decision_check")),
    ("finding-decision-undated", _insert(FINDINGS, _finding(owner_decision=c.DECISION_KEPT)),
     _rej("trillion_club_stake_findings_decision_dated")),
    ("finding-dated-without-decision", _insert(FINDINGS, _finding(decided_at=NOW)),
     _rej("trillion_club_stake_findings_decision_dated")),
    ("finding-row-array", _insert(FINDINGS, _finding(proposed_row=[])), _rej("trillion_club_stake_findings_json_shapes")),
    ("finding-row-string", _insert(FINDINGS, _finding(proposed_row="x")), _rej("trillion_club_stake_findings_json_shapes")),
    ("finding-row-too-big", _insert(FINDINGS, _finding(proposed_row={"x": "y" * 16400})),
     _rej("trillion_club_stake_findings_json_shapes")),
    # An object must be a clean 23514 — never jsonb_array_length's 22023 (the CASE guard).
    ("finding-spans-object", _insert(FINDINGS, _finding(verified_spans={})), _rej("trillion_club_stake_findings_json_shapes")),
    ("finding-spans-string", _insert(FINDINGS, _finding(verified_spans="x")), _rej("trillion_club_stake_findings_json_shapes")),
    ("finding-spans-101", _insert(FINDINGS, _finding(verified_spans=["s" * 40] * 101)),
     _rej("trillion_club_stake_findings_json_shapes")),
    ("finding-spans-too-big", _insert(FINDINGS, _finding(verified_spans=["x" * 70000])),
     _rej("trillion_club_stake_findings_json_shapes")),
    ("finding-spans-null", _insert(FINDINGS, _finding(verified_spans=None)), _nn("verified_spans")),
    ("finding-sha-upper", _insert(FINDINGS, _finding(fact_sha="A" * 64)), _rej("trillion_club_stake_findings_sha_check")),
    ("finding-sha-short", _insert(FINDINGS, _finding(fact_sha="a" * 63)), _rej("trillion_club_stake_findings_sha_check")),
    ("finding-form-empty", _insert(FINDINGS, _finding(form="")), _rej("trillion_club_stake_findings_text_caps")),
    ("finding-form-long", _insert(FINDINGS, _finding(form="X" * 41)), _rej("trillion_club_stake_findings_text_caps")),
    ("finding-document-long", _insert(FINDINGS, _finding(document="d" * 256)), _rej("trillion_club_stake_findings_text_caps")),
    ("finding-url-http", _insert(FINDINGS, _finding(source_url=_SEC_URL.replace("https", "http", 1))),
     _rej("trillion_club_stake_findings_text_caps")),
    ("finding-url-long", _insert(FINDINGS, _finding(source_url="https://www.sec.gov/" + "x" * 481)),
     _rej("trillion_club_stake_findings_text_caps")),
    ("finding-error-long", _insert(FINDINGS, _finding(last_error="e" * 2001)), _rej("trillion_club_stake_findings_text_caps")),
    ("finding-attempts-negative", _insert(FINDINGS, _finding(attempts=-1)), _rej("trillion_club_stake_findings_ranges")),
    ("finding-message-id-zero", _insert(FINDINGS, _finding(telegram_message_id=0)), _rej("trillion_club_stake_findings_ranges")),
    ("finding-null-status", _insert(FINDINGS, _finding(status=None)), _nn("status")),
    ("finding-null-reason", _insert(FINDINGS, _finding(reason=None)), _nn("reason")),
]

_UPDATE_PROBES: List[Tuple[str, str, Tuple]] = [
    # The seed script's adoption (plan.stake_adoptions): one full-row UPDATE back to the seed.
    ("adopt-kept-auto-row", _upd(STAKES, "origin = 'seed', finding_id = NULL, owner_kept_at = NULL, "
                                 "withdrawn_at = NULL", f"id = '{S2}'"), _acc()),
    ("adopt-withdrawn-row", _upd(STAKES, "origin = 'seed', finding_id = NULL, owner_kept_at = NULL, "
                                 "withdrawn_at = NULL", f"id = '{S4}'"), _acc()),
    ("adopt-keeping-the-finding", _upd(STAKES, "origin = 'seed'", f"id = '{S1}'"),
     _rej("trillion_club_stake_finding_iff_machine")),
    # The no-deploy valve from 205's header, over every machine row.
    ("valve", _upd(STAKES, "published = false, withdrawn_at = now()", "origin IN ('auto', 'proposal')"), _acc(4)),
    # The Withdraw tap: ONE PATCH, any machine origin, after Keep too.
    ("withdraw-after-keep", _upd(STAKES, "published = false, withdrawn_at = now()",
                                 f"id = '{S2}' AND origin IN ('auto', 'proposal')"), _acc()),
    ("withdraw-a-proposal", _upd(STAKES, "published = false, withdrawn_at = now()",
                                 f"id = '{S3}' AND origin IN ('auto', 'proposal')"), _acc()),
    ("republish-a-withdrawn-row", _upd(STAKES, "published = true", f"id = '{S4}'"),
     _rej("trillion_club_stake_withdrawn_never_published")),
    ("keep-a-proposal", _upd(STAKES, "owner_kept_at = now()", f"id = '{S3}'"), _rej("trillion_club_stake_kept_only_auto")),
    # Reconcile downgrading a kept auto row into a proposal must clear Keep in the same write.
    ("auto-to-proposal-keeping-keep", _upd(STAKES, "origin = 'proposal'", f"id = '{S2}'"),
     _rej("trillion_club_stake_kept_only_auto")),
    ("auto-to-proposal-clearing-keep", _upd(STAKES, "origin = 'proposal', owner_kept_at = NULL",
                                            f"id = '{S2}'"), _acc()),
    ("unkept-auto-made-material", _upd(STAKES, "material = true", f"id = '{S1}'"), _rej("trillion_club_stake_auto_scope")),
    ("unkept-auto-given-a-value", _upd(STAKES, "disclosed_value_usd = 1e9, value_basis = 'invested'", f"id = '{S1}'"),
     _rej("trillion_club_stake_auto_scope")),
    ("unkeep-a-material-row", _upd(STAKES, "owner_kept_at = NULL", f"id = '{S2}'"), _rej("trillion_club_stake_auto_scope")),
    ("background-on-a-proposal", _upd(STAKES, "background = 'x'", f"id = '{S3}'"), _rej("trillion_club_stake_machine_shape")),
    # Seed rows stay the owner's to edit in Studio — except that withdrawn is never live.
    ("seed-row-edited-in-studio", _upd(STAKES, "background = 'Edited in Studio.'", _FIRST_SEED), _acc()),
    ("seed-row-withdrawn-while-published", _upd(STAKES, "withdrawn_at = now()", _FIRST_SEED),
     _rej("trillion_club_stake_withdrawn_never_published")),
    ("decision-cleared-alone", _upd(FINDINGS, "owner_decision = NULL", f"id = '{F2}'"),
     _rej("trillion_club_stake_findings_decision_dated")),
    ("finding-cas-to-publishing", _upd(FINDINGS, "status = 'publishing'", f"id = '{F3}' AND status = 'published'"),
     _acc()),
]

_ALL_PROBES = _STAKE_PROBES + _FINDING_PROBES + _UPDATE_PROBES
_PROBE_LINE = re.compile(r"PROBE\|([^|]*)\|(ACCEPT|REJECT)\|([^|]*)(?:\|([^|]*)\|([^|]*)\|(.*))?")


def _parse_probes(stderr: str) -> Dict[str, Dict[str, str]]:
    out: Dict[str, Dict[str, str]] = {}
    for line in stderr.splitlines():
        m = _PROBE_LINE.search(line)
        if m:
            tag, verdict = m.group(1), m.group(2)
            if verdict == "ACCEPT":
                out[tag] = {"verdict": verdict, "rows": m.group(3)}
            else:
                out[tag] = {"verdict": verdict, "sqlstate": m.group(3), "constraint": m.group(4) or "",
                            "column": m.group(5) or "", "message": m.group(6) or ""}
    return out


def probe_mismatches(probes: Mapping[str, Mapping[str, str]], catalogue) -> List[str]:
    out: List[str] = []
    for tag, _stmt, want in catalogue:
        got = probes.get(tag)
        if got is None:
            out.append(f"{tag}: no verdict")
        elif want[0] == "ACCEPT":
            if got["verdict"] != "ACCEPT" or got["rows"] != str(want[1]):
                out.append(f"{tag}: expected ACCEPT ({want[1]} row), got {dict(got)}")
        elif want[0] == "NOTNULL":
            if (got["verdict"], got.get("sqlstate"), got.get("column")) != ("REJECT", want[1], want[2]):
                out.append(f"{tag}: expected NOT NULL on {want[2]}, got {dict(got)}")
        elif (got["verdict"], got.get("sqlstate"), got.get("constraint")) != ("REJECT", want[1], want[2]):
            out.append(f"{tag}: expected {want[2]} ({want[1]}), got {dict(got)}")
    return out


def _seed() -> Dict[str, List[Dict[str, Any]]]:
    return json.loads(SEED_JSON.read_text(encoding="utf-8"))


# ── E'. the rules as Python — the same verdicts with no database ────────────────────────────
#
# `model_violations` states every 205 rule in Python. It is checked against every INSERT probe
# above (whose verdicts Postgres decides in section E) and against the whole seed, so the claim
# "every rule holds on the seed rows" stays verified on a machine without PostgreSQL. It is also
# the reference the finder's in-memory store fake should apply (learn_weekly's FAKE_CHECKS
# pattern). Sizes are jsonb's text form, approximated by `", "` / `": "` separators: exact for
# the probes here, not at the byte boundary.

_PY_CIK = re.compile(r"[0-9]{10}")
_PY_ACCESSION = re.compile(r"[0-9]{10}-[0-9]{2}-[0-9]{6}")
_PY_SHA = re.compile(r"[0-9a-f]{64}")


def _cik_ok(v: str) -> bool:
    return bool(_PY_CIK.fullmatch(v)) and v != "0000000000"


def _json_bytes(v: Any) -> int:
    return len(json.dumps(v, ensure_ascii=False, separators=(", ", ": ")).encode("utf-8"))


_STAKE_RULES_PY = {
    "trillion_club_stakes_origin_check": lambda r: r["origin"] in c.ORIGINS,
    "trillion_club_stakes_investee_cik_check": lambda r: r["investee_cik"] is None or _cik_ok(r["investee_cik"]),
    "trillion_club_stake_finding_iff_machine":
        lambda r: (r["finding_id"] is not None) == (r["origin"] in c.MACHINE_ORIGINS),
    "trillion_club_stake_withdrawn_never_published": lambda r: not (r["published"] and r["withdrawn_at"] is not None),
    "trillion_club_stake_machine_shape": lambda r: r["origin"] not in c.MACHINE_ORIGINS or (
        r["background"] is None and not r["tied_to_deal"] and r["source_confidence"] == "primary"
        and re.match(_SEC_URL_RE, r["source_url"]) is not None),
    "trillion_club_stake_auto_scope": lambda r: r["origin"] != c.ORIGIN_AUTO or r["owner_kept_at"] is not None or (
        not r["material"] and r["kind"] in (sn.KIND_ON_13F_NOTE, sn.KIND_US_LISTED_OFF_13F)
        and r["disclosed_value_usd"] is None and r["value_basis"] is None),
    "trillion_club_stake_kept_only_auto": lambda r: r["owner_kept_at"] is None or r["origin"] == c.ORIGIN_AUTO,
}
_FINDING_RULES_PY = {
    f"{FINDINGS}_source_check": lambda r: r["source"] in c.FINDING_SOURCES,
    f"{FINDINGS}_tier_check": lambda r: r["tier"] in c.FINDING_TIERS,
    f"{FINDINGS}_outcome_check": lambda r: r["outcome"] in c.FINDING_OUTCOMES,
    f"{FINDINGS}_reason_check": lambda r: r["reason"] in c.REASON_CODES,
    f"{FINDINGS}_status_check": lambda r: r["status"] in c.FINDING_STATUSES,
    f"{FINDINGS}_decision_check": lambda r: r["owner_decision"] is None or r["owner_decision"] in c.OWNER_DECISIONS,
    f"{FINDINGS}_decision_dated": lambda r: (r["owner_decision"] is None) == (r["decided_at"] is None),
    f"{FINDINGS}_accession_check": lambda r: _PY_ACCESSION.fullmatch(r["accession"]) is not None,
    f"{FINDINGS}_cik_check":
        lambda r: _cik_ok(r["filer_cik"]) and (r["investee_cik"] is None or _cik_ok(r["investee_cik"])),
    f"{FINDINGS}_json_shapes": lambda r: (
        (r["proposed_row"] is None
         or (isinstance(r["proposed_row"], dict) and _json_bytes(r["proposed_row"]) <= 16384))
        and isinstance(r["verified_spans"], list) and len(r["verified_spans"]) <= 100
        and _json_bytes(r["verified_spans"]) <= 65536),
    f"{FINDINGS}_sha_check": lambda r: r["fact_sha"] is None or _PY_SHA.fullmatch(r["fact_sha"]) is not None,
    f"{FINDINGS}_text_caps": lambda r: (
        1 <= len(r["form"]) <= 40 and 1 <= len(r["document"]) <= 255
        and r["source_url"].startswith("https://") and len(r["source_url"]) <= 500
        and (r["last_error"] is None or len(r["last_error"]) <= 2000)),
    f"{FINDINGS}_ranges":
        lambda r: r["attempts"] >= 0 and (r["telegram_message_id"] is None or r["telegram_message_id"] > 0),
}
#: Column DEFAULTs a missing key takes (175's for the columns the 205 rules read, and 205's).
_DEFAULTS = {
    STAKES: {"origin": c.ORIGIN_SEED, "source_confidence": "primary", "material": False,
             "tied_to_deal": False, "published": False},
    FINDINGS: {"verified_spans": [], "status": c.STATUS_NEW, "attempts": 0,
               # gen_random_uuid() / NOW(): any value — no 205 rule reads them
               "id": "<gen_random_uuid()>", "created_at": "<now()>", "updated_at": "<now()>"},
}
_NOT_NULL = {
    STAKES: ("origin", "kind", "source_url", "source_confidence", "material", "tied_to_deal", "published"),
    FINDINGS: tuple(k for k, (_t, not_null, _d) in _LEDGER.items() if not_null),
}


def model_violations(table: str, doc: Mapping[str, Any]) -> set:
    """The rules a row inserted as `doc` breaks: NOT NULL first (Postgres checks it before any
    CHECK), then every 205 CHECK. Empty = Postgres accepts it as far as 205 is concerned."""
    row: Dict[str, Any] = {**_DEFAULTS[table], **doc}
    missing = {f"NOTNULL:{col}" for col in _NOT_NULL[table] if row.get(col) is None}
    if missing:
        return missing
    full = {k: row.get(k) for k in set(row) | set(_LEDGER) | {
        "origin", "finding_id", "investee_cik", "owner_kept_at", "withdrawn_at", "background",
        "disclosed_value_usd", "value_basis"}}
    rules = _STAKE_RULES_PY if table == STAKES else _FINDING_RULES_PY
    return {name for name, ok in rules.items() if not ok(full)}


def _insert_doc(stmt: str) -> Optional[Tuple[str, Dict[str, Any]]]:
    """(table, doc) back out of an `_insert` statement (the JSON round-trips exactly)."""
    head = re.match(r"INSERT INTO public\.(\w+) \(", stmt)
    if head is None:
        return None
    body = re.search(r"\$J\$(.*)\$J\$::json\)$", stmt, re.S)
    assert body, stmt[:120]
    return head.group(1), json.loads(body.group(1))


def test_the_python_model_states_every_205_rule():
    assert set(_STAKE_RULES_PY) | set(_FINDING_RULES_PY) == set(named_checks(_sql()))


def test_the_python_model_agrees_with_every_insert_probe():
    """Postgres decides these probes in section E; the same expectations are checked here with
    no database. UPDATE probes and the UNIQUE / FK refusals need table state and stay pg-only."""
    out, checked = [], 0
    for tag, stmt, want in _ALL_PROBES:
        parsed = _insert_doc(stmt)
        if parsed is None or (want[0] == "REJECT" and want[1] != "23514"):
            continue
        table, doc = parsed
        expect = (set() if want[0] == "ACCEPT"
                  else {f"NOTNULL:{want[2]}"} if want[0] == "NOTNULL" else {want[2]})
        got = model_violations(table, doc)
        checked += 1
        if got != expect:
            out.append(f"{tag}: the model says {sorted(got)}, the probe expects {sorted(expect)}")
    assert out == []
    assert checked >= 150, f"only {checked} insert probes were modelled"


def test_the_python_model_finds_every_seed_row_clean():
    """Every 205 rule holds on every seed row, inserted the seed script's way (no 205 column, so
    every row takes origin's DEFAULT 'seed') — the same claim section E proves in Postgres."""
    stakes = _seed()["stakes"]
    bad = [(s["company_slug"], s["investee_name"], sorted(model_violations(STAKES, s)))
           for s in stakes if model_violations(STAKES, s)]
    assert bad == [] and len(stakes) >= 100
    # ... while the same rows marked as machine rows break the machine rules (non-vacuity).
    broken = [s for s in stakes if model_violations(STAKES, {**s, "origin": c.ORIGIN_AUTO})]
    assert len(broken) == len(stakes), "every seed row lacks a finding_id, so none may be 'auto'"


def _load_seed_sql(seed: Mapping[str, Any]) -> str:
    rows = [_insert(COMPANIES, co) for co in seed["companies"]] + [_insert(STAKES, s) for s in seed["stakes"]]
    return "BEGIN;\n" + ";\n".join(rows) + ";\nCOMMIT;"


_CATALOGUE_SQL = f"""
SELECT 'C|' || conrelid::regclass::text || '|' || conname || '|' || contype::text || '|'
       || convalidated::text || '|' || confdeltype::text || '|' || confupdtype::text
  FROM pg_constraint
 WHERE conrelid IN ('public.{STAKES}'::regclass, 'public.{FINDINGS}'::regclass)
 ORDER BY 1;
"""


def _constraints(server: _Cluster, db: str = "postgres") -> Dict[str, Tuple[str, ...]]:
    out = {}
    for line in server.ok(_CATALOGUE_SQL, db).splitlines():
        _c, table, name, kind, valid, on_delete, on_update = line.split("|")
        out[name] = (table, kind, valid, on_delete, on_update)
    return out


def _constraints_175(server: _Cluster, db: str) -> Dict[str, Tuple[str, ...]]:
    """The stakes table's constraints in a database that holds 175 only (no ledger yet)."""
    out = {}
    for line in server.ok(
            "SELECT conname || '|' || contype::text || '|' || convalidated::text FROM pg_constraint "
            f"WHERE conrelid = 'public.{STAKES}'::regclass ORDER BY 1;", db).splitlines():
        name, kind, valid = line.split("|")
        out[name] = (STAKES, kind, valid)
    return out


def _mutant_db(server: _Cluster, db: str, migration_205: str) -> None:
    """A fresh database holding 175, the NVIDIA company row and the given 205 text."""
    server.ok(f"DROP DATABASE IF EXISTS {db};")
    server.ok(f"CREATE DATABASE {db};")
    server.ok(_SETUP_SQL, db)
    server.ok(M175.read_text(encoding="utf-8"), db)
    nvidia = next(co for co in _seed()["companies"] if co["slug"] == "nvidia")
    server.ok(_insert(COMPANIES, nvidia) + ";", db)
    server.ok(migration_205, db)


@pytest.fixture(scope="module")
def pg():
    """175 → the real seed → 205 → 175 → 205 → 205 in a throwaway cluster, then every probe."""
    server = _Cluster()
    why = server.start()
    if why:
        server.stop()
        pytest.skip(why)
    try:
        info: Dict[str, Any] = {"server": server}

        def step(name: str, sql: str) -> None:
            info[name] = server.psql(sql)
            assert info[name].returncode == 0, f"{name} failed:\n{info[name].stderr[-2000:]}"

        server.ok(_SETUP_SQL)
        seed = _seed()
        info["seed"] = seed
        step("apply_175", M175.read_text(encoding="utf-8"))
        step("seed_load", _load_seed_sql(seed))
        step("apply_205", _raw())
        info["constraints_1"] = _constraints(server)
        step("reapply_175", M175.read_text(encoding="utf-8"))
        step("reapply_205", _raw())
        info["constraints_2"] = _constraints(server)
        # The owner's stop must survive a replay: ON CONFLICT DO NOTHING, never DO UPDATE.
        server.ok(f"UPDATE public.notification_job_state SET enabled = false WHERE job = '{JOB}';")
        step("reapply_stopped", _raw())
        info["enabled_after_stop"] = server.ok(
            f"SELECT enabled::text FROM public.notification_job_state WHERE job = '{JOB}';").strip()
        server.ok(f"UPDATE public.notification_job_state SET enabled = true WHERE job = '{JOB}';")
        server.ok(_fixture_rows())
        run = server.psql("\n".join(_probe(t, s) for t, s, _ in _ALL_PROBES))
        assert run.returncode == 0, run.stderr[-2000:]
        info["probes"] = _parse_probes(run.stderr)
        # The same cluster with 175 only: what 205 adds is the difference.
        server.ok("DROP DATABASE IF EXISTS m205base;")
        server.ok("CREATE DATABASE m205base;")
        server.ok(_SETUP_SQL, "m205base")
        server.ok(M175.read_text(encoding="utf-8"), "m205base")
        info["constraints_175"] = {k: v for k, v in _constraints_175(server, "m205base").items()}
        yield info
    finally:
        server.stop()


def test_pg_175_seed_205_replayed_twice(pg):
    for step in ("apply_175", "seed_load", "apply_205", "reapply_175", "reapply_205", "reapply_stopped"):
        assert pg[step].returncode == 0, (step, pg[step].stderr[-1500:])
    server, seed = pg["server"], pg["seed"]
    counts = server.ok(f"SELECT count(*) FROM public.{COMPANIES}; "
                       f"SELECT count(*) FROM public.{STAKES} WHERE investee_name NOT LIKE 'Probe %';")
    assert counts.split() == [str(len(seed["companies"])), str(len(seed["stakes"]))]
    # Every hand-kept row came through 205 as the owner's: 'seed', no finding, no tombstone.
    odd = server.ok(f"SELECT count(*) FROM public.{STAKES} WHERE investee_name NOT LIKE 'Probe %' AND "
                    "(origin <> 'seed' OR finding_id IS NOT NULL OR owner_kept_at IS NOT NULL "
                    "OR withdrawn_at IS NOT NULL OR investee_cik IS NOT NULL);")
    assert odd.strip() == "0"


def test_pg_every_rule_holds_on_the_seed_and_a_replay_changes_nothing(pg):
    first, second = pg["constraints_1"], pg["constraints_2"]
    assert first == second, "re-applying 175 + 205 changed the constraint set"
    named = named_checks(_sql())
    for name in named:
        assert name in first, f"{name} is not in Postgres"
        assert first[name][1:3] == ("c", "true"), (name, first[name])   # a VALIDATED check
    assert first["trillion_club_stake_findings_company_slug_accession_key"][:2] == (FINDINGS, "u")
    assert first["trillion_club_stake_findings_company_slug_fkey"][1:] == ("f", "true", "c", "c")
    assert first["trillion_club_stake_findings_stake_id_fkey"][1:] == ("f", "true", "n", "a")
    # Nothing 205 did not declare (Postgres would suffix a colliding auto-name with a digit).
    ledger = {k for k, v in first.items() if v[0] == FINDINGS}
    assert ledger == {k for k in named if k.startswith(f"{FINDINGS}_")} | {
        f"{FINDINGS}_pkey", f"{FINDINGS}_company_slug_accession_key", f"{FINDINGS}_company_slug_fkey",
        f"{FINDINGS}_stake_id_fkey"}


def test_pg_205_adds_exactly_its_seven_stakes_rules_and_keeps_175s(pg):
    base = pg["constraints_175"]
    after = {k: v[:3] for k, v in pg["constraints_2"].items() if v[0] == STAKES}
    added = set(after) - set(base)
    assert added == {k for k in named_checks(_sql()) if not k.startswith(f"{FINDINGS}_")}, sorted(added)
    assert len(added) == 7
    assert {k: after[k] for k in base} == base, "205 changed or dropped one of 175's constraints"
    assert len(base) >= 15, sorted(base)   # 175's own set really was read


def test_pg_the_job_row_is_seeded_and_the_owners_stop_survives_a_replay(pg):
    assert pg["enabled_after_stop"] == "false"
    rows = pg["server"].ok(f"SELECT job || '|' || enabled::text FROM public.notification_job_state "
                           "WHERE job LIKE 'trillion_club_%' ORDER BY 1;").split()
    assert rows == ["trillion_club_daily|true", f"{JOB}|true", "trillion_club_weekly|true"]


def test_pg_the_ledger_is_service_role_only_even_under_default_privileges(pg):
    server = pg["server"]
    privs = server.ok(
        "SELECT r || '|' || p || '|' || has_table_privilege(r, 'public." + FINDINGS + "', p)::text "
        "FROM unnest(ARRAY['anon', 'authenticated', 'service_role']) r, "
        "unnest(ARRAY['SELECT', 'INSERT', 'UPDATE', 'DELETE']) p ORDER BY 1;").split()
    got = {tuple(x.split("|")[:2]): x.split("|")[2] for x in privs}
    assert len(got) == 12, privs   # 3 roles x 4 privileges: an empty answer must not pass
    for (role, priv), value in got.items():
        assert value == ("true" if role == "service_role" else "false"), (role, priv, value)
    assert server.ok(f"SELECT relrowsecurity::text FROM pg_class WHERE oid = 'public.{FINDINGS}'::regclass;").strip() == "true"
    policies = server.ok(
        "SELECT policyname || '|' || array_to_string(roles, ',') || '|' || cmd || '|' || qual || '|' "
        f"|| with_check FROM pg_policies WHERE schemaname = 'public' AND tablename = '{FINDINGS}';").split("\n")
    assert [p for p in policies if p] == [f"{FINDINGS}_service_all|service_role|ALL|true|true"]


def test_pg_columns_defaults_and_comments(pg):
    server = pg["server"]
    cols = dict(line.split("|", 1) for line in server.ok(
        "SELECT table_name || '.' || column_name || '|' || coalesce(column_default, '') || '|' || is_nullable "
        "|| '|' || data_type FROM information_schema.columns "
        f"WHERE table_schema = 'public' AND table_name IN ('{STAKES}', '{FINDINGS}');").splitlines())
    assert cols[f"{STAKES}.origin"] == "'seed'::text|NO|text"
    assert cols[f"{STAKES}.finding_id"] == "|YES|uuid"
    assert cols[f"{FINDINGS}.status"] == "'new'::text|NO|text"
    assert cols[f"{FINDINGS}.telegram_message_id"] == "|YES|bigint"
    assert cols[f"{FINDINGS}.id"] == "gen_random_uuid()|NO|uuid"
    assert set(k.split(".", 1)[1] for k in cols if k.startswith(f"{FINDINGS}.")) == set(_LEDGER)
    comment = server.ok(f"SELECT obj_description('public.{STAKES}'::regclass, 'pg_class');").strip()
    assert SOURCE_PHRASE in comment, comment


def test_pg_every_probe_ends_the_way_the_rules_say(pg):
    assert probe_mismatches(pg["probes"], _ALL_PROBES) == []


def test_pg_every_rule_in_205_has_a_probe_it_refuses():
    """A new CHECK in 205 without a probe fails here (the catalogue is the evidence)."""
    targeted = {want[2] for _t, _s, want in _ALL_PROBES if want[0] == "REJECT"}
    missing = sorted(set(named_checks(_sql())) - targeted)
    assert missing == [], f"rules no probe tries to break: {missing}"


def test_pg_a_studio_delete_always_succeeds_and_leaves_the_finding(pg):
    """reconcile's deleted_in_studio path depends on it: the row goes, the finding stays with
    stake_id NULL whatever its status (published, kept, withdrawn …)."""
    out = pg["server"].ok(
        "BEGIN;\n"
        f"DELETE FROM public.{STAKES} WHERE origin IN ('auto', 'proposal');\n"
        f"SELECT string_agg(status || ':' || (stake_id IS NULL)::text, ',' ORDER BY id) FROM public.{FINDINGS} "
        f"WHERE id IN ('{F1}', '{F2}', '{F3}', '{F4}');\n"
        "ROLLBACK;").strip()
    assert out == "published:true,kept:true,published:true,withdrawn:true", out


def test_pg_a_company_delete_or_slug_rename_cascades_to_the_ledger(pg):
    out = pg["server"].ok(
        "BEGIN;\n"
        f"UPDATE public.{COMPANIES} SET slug = 'nvidia-corp' WHERE slug = 'nvidia';\n"
        f"SELECT count(*) FROM public.{FINDINGS} WHERE company_slug = 'nvidia-corp';\n"
        f"DELETE FROM public.{COMPANIES} WHERE slug = 'nvidia-corp';\n"
        f"SELECT count(*) FROM public.{FINDINGS} WHERE company_slug IN ('nvidia', 'nvidia-corp');\n"
        f"SELECT count(*) FROM public.{STAKES} WHERE company_slug IN ('nvidia', 'nvidia-corp');\n"
        "ROLLBACK;").split()
    assert out == ["4", "0", "0"], out


def _verify_query(raw: str) -> str:
    """The footer's VERIFY SELECT, uncommented — the query the owner runs in Studio."""
    tail = raw[raw.rindex("\nCOMMIT;\n"):]
    lines = [line[3:] if line.startswith("-- ") else line[2:] for line in tail.splitlines() if line.startswith("--")]
    start = next(i for i, line in enumerate(lines) if line.strip() == "SELECT")
    return "\n".join(lines[start:])


def test_pg_the_owners_verify_query_runs_and_answers_all_true_after_a_fresh_apply(pg):
    server = pg["server"]
    try:
        server.ok("DROP DATABASE IF EXISTS m205verify;")
        server.ok("CREATE DATABASE m205verify;")
        server.ok(_SETUP_SQL, "m205verify")
        server.ok(M175.read_text(encoding="utf-8"), "m205verify")
        server.ok(_load_seed_sql(pg["seed"]), "m205verify")
        server.ok(_raw(), "m205verify")
        got = server.ok(_verify_query(_raw()), "m205verify").strip()
        assert got == "|".join(["t"] * 8), got
        # ... and it can say no: on the main database the probes' fixture rows are machine rows.
        assert server.ok(_verify_query(_raw())).strip().split("|")[2] == "f"
    finally:
        server.psql("DROP DATABASE IF EXISTS m205verify;")


def _without_statement(raw: str, head: str) -> str:
    """`raw` with the one statement that starts at `head` removed (through its `;`)."""
    start = raw.index(head)
    end = raw.index(";", start) + 1
    assert raw.count(head) == 1, head
    return raw[:start] + raw[end:]


def test_pg_the_probes_fail_on_a_205_missing_a_rule_or_its_revoke(pg):
    """Non-vacuity in the database itself: without the machine-shape rule a background line
    is ACCEPTED; without the REVOKE the default privileges hand the ledger to anon."""
    server = pg["server"]
    try:
        mutant = _without_statement(_raw(), "ALTER TABLE public.trillion_club_stakes\n    ADD CONSTRAINT "
                                             "trillion_club_stake_machine_shape")
        mutant = _without_statement(mutant, f"REVOKE ALL ON public.{FINDINGS} FROM anon, authenticated;")
        _mutant_db(server, "m205mut", mutant)
        tag, stmt, _ = next(p for p in _STAKE_PROBES if p[0] == "auto-background")
        run = server.psql(_probe(tag, stmt), "m205mut")
        assert run.returncode == 0, run.stderr
        assert _parse_probes(run.stderr)[tag]["verdict"] == "ACCEPT"
        anon = server.ok(f"SELECT has_table_privilege('anon', 'public.{FINDINGS}', 'SELECT')::text;", "m205mut")
        assert anon.strip() == "true", "the default-privilege emulation is inert — the REVOKE test proves nothing"
    finally:
        server.psql("DROP DATABASE IF EXISTS m205mut;")


def test_pg_the_stop_check_fails_on_a_job_row_that_updates_on_conflict(pg):
    server = pg["server"]
    try:
        mutant = _raw().replace("ON CONFLICT (job) DO NOTHING;", "ON CONFLICT (job) DO UPDATE SET enabled = TRUE;")
        assert mutant != _raw()
        _mutant_db(server, "m205mut2", mutant)
        server.ok(f"UPDATE public.notification_job_state SET enabled = false WHERE job = '{JOB}';", "m205mut2")
        server.ok(mutant, "m205mut2")
        got = server.ok(f"SELECT enabled::text FROM public.notification_job_state WHERE job = '{JOB}';", "m205mut2")
        assert got.strip() == "true", "the mutant should re-enable the job — the stop check proves nothing"
    finally:
        server.psql("DROP DATABASE IF EXISTS m205mut2;")
