"""scripts/grant_feedback_reward.py — the owner's one tool for rewarding a confirmed report.

What matters most is what it does NOT do: a dry run (the default) makes no RPC call at all, an
unknown account never reaches the RPC, and `--apply` makes exactly one call with exactly the
payload migration 199's function takes. The fake database records every call so those are
asserted, not assumed; it returns canned rows rather than modelling the SQL (the SQL's own
arithmetic is modelled in tests/test_credit_pool_isolation.py).
"""
from __future__ import annotations

import pytest

from scripts import grant_feedback_reward as gfr

_UID = "6f1c2a3b-4d5e-4f60-8a7b-9c0d1e2f3a4b"
_REF = "R-7K2Q9M"


# ── a fake Supabase client that records every call ───────────────────────────


class _Result:
    def __init__(self, data):
        self.data = data


class _Query:
    def __init__(self, db, table):
        self._db, self._table, self.filters = db, table, []

    def select(self, *_a, **_k):
        return self

    def eq(self, column, value):
        self.filters.append((column, value))
        return self

    def order(self, *_a, **_k):
        return self

    def limit(self, *_a, **_k):
        return self

    def execute(self):
        self._db.table_calls.append((self._table, tuple(self.filters)))
        if self._table in self._db.read_errors:
            raise self._db.read_errors[self._table]
        rows = self._db.tables.get(self._table, [])
        # Honour the equality filters, so a lookup by the wrong id/email finds nothing.
        return _Result([r for r in rows if all(r.get(c) == v for c, v in self.filters)])


class _RPC:
    def __init__(self, db, name, params):
        self._db, self._name, self._params = db, name, params

    def execute(self):
        self._db.rpc_calls.append((self._name, self._params))
        if self._db.rpc_error is not None:
            raise self._db.rpc_error
        return _Result(self._db.rpc_data)


class _FakeDB:
    def __init__(self, *, users=None, credits=None, ledger=None, rpc_data=None, rpc_error=None):
        self.tables = {
            "users": users if users is not None else [
                {"id": _UID, "email": "someone@x.com", "tier": "free", "comp_tier": None}],
            "user_credits": credits if credits is not None else [
                {"user_id": _UID, "total": 50, "used": 10, "purchased_total": 0, "purchased_used": 0}],
            "credit_transactions": ledger or [],
        }
        self.rpc_data = rpc_data if rpc_data is not None else {
            "outcome": "granted", "credits": 20, "spendable": 60}
        self.rpc_error = rpc_error
        self.read_errors: dict = {}
        self.rpc_calls: list = []
        self.table_calls: list = []

    def table(self, name):
        return _Query(self, name)

    def rpc(self, name, params):
        return _RPC(self, name, params)


class _APIError(Exception):
    """postgrest's APIError carries the PostgREST / Postgres code on `.code`."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def _ledger_row(row_id, ref, delta=20, user_id=_UID):
    """An earlier reward row. `user_id` and `reason` are there for the fake's `.eq()` filters."""
    return {"id": row_id, "user_id": user_id, "reason": "feedback_reward", "delta": delta,
            "ref_id": ref, "created_at": "2026-10-01T12:00:00Z"}


# ── dry run (the default) ────────────────────────────────────────────────────


def test_a_dry_run_makes_no_rpc_call(capsys):
    db = _FakeDB()
    assert gfr.main(["--user-id", _UID, "--ref", _REF], db=db) == 0
    assert db.rpc_calls == [], "a dry run must never call the RPC"
    out = capsys.readouterr().out
    assert "Dry run only" in out
    assert "someone@x.com" in out and _UID in out
    assert "tier=free" in out
    assert "+20 credits" in out


def test_a_dry_run_reads_only_this_accounts_reward_rows():
    db = _FakeDB()
    gfr.main(["--user-id", _UID, "--ref", _REF], db=db)
    ledger_reads = [f for t, f in db.table_calls if t == "credit_transactions"]
    assert ledger_reads == [(("user_id", _UID), ("reason", "feedback_reward"))]


def test_an_earlier_reward_for_the_same_report_is_flagged(capsys):
    db = _FakeDB(ledger=[_ledger_row(41, "R-ABC234"), _ledger_row(40, _REF)])
    assert gfr.main(["--user-id", _UID, "--ref", _REF], db=db) == 0
    out = capsys.readouterr().out
    assert "THIS REPORT" in out
    assert f"⚠ {_REF} was already rewarded" in out
    assert "R-ABC234" in out, "every earlier reward is listed, not only the matching one"
    assert db.rpc_calls == []


def test_no_warning_when_the_report_is_new(capsys):
    db = _FakeDB(ledger=[_ledger_row(41, "R-ABC234")])
    gfr.main(["--user-id", _UID, "--ref", _REF], db=db)
    out = capsys.readouterr().out
    assert "already rewarded" not in out and "THIS REPORT" not in out


def test_a_lowercase_code_is_matched_against_earlier_rewards(capsys):
    db = _FakeDB(ledger=[_ledger_row(40, _REF)])
    gfr.main(["--user-id", _UID, "--ref", _REF.lower()], db=db)
    assert "already rewarded" in capsys.readouterr().out


# ── --apply ──────────────────────────────────────────────────────────────────


def test_apply_makes_exactly_one_call_with_the_exact_payload(capsys):
    db = _FakeDB()
    assert gfr.main(["--user-id", _UID, "--ref", _REF, "--apply"], db=db) == 0
    assert db.rpc_calls == [("grant_feedback_reward",
                             {"p_user_id": _UID, "p_credits": 20, "p_ref_id": _REF})]
    out = capsys.readouterr().out
    assert "✓ granted 20 credits" in out
    assert "reply to the user" in out, "after a grant the owner is reminded to answer the report"


def test_apply_sends_the_requested_amount_and_the_normalised_code():
    db = _FakeDB()
    gfr.main(["--user-id", _UID.upper(), "--ref", " r-7k2q9m ", "--credits", "40", "--apply"], db=db)
    assert db.rpc_calls == [("grant_feedback_reward",
                             {"p_user_id": _UID, "p_credits": 40, "p_ref_id": _REF})]


def test_a_replay_exits_zero_and_says_nothing_changed(capsys):
    db = _FakeDB(rpc_data={"outcome": "replay", "credits": 20, "spendable": 60,
                           "granted_at": "2026-10-01T12:00:00+00:00"})
    assert gfr.main(["--user-id", _UID, "--ref", _REF, "--apply"], db=db) == 0
    out = capsys.readouterr().out
    assert "nothing changed" in out
    assert "reply to the user" not in out
    assert len(db.rpc_calls) == 1


def test_an_invalid_outcome_exits_one_with_its_reason(capsys):
    db = _FakeDB(rpc_data={"outcome": "invalid", "reason": "unknown_user"})
    assert gfr.main(["--user-id", _UID, "--ref", _REF, "--apply"], db=db) == 1
    assert "unknown_user" in capsys.readouterr().out


@pytest.mark.parametrize("code", ["PGRST202", "42883"])
def test_a_missing_function_exits_one_with_the_migration_hint(code, capsys):
    db = _FakeDB(rpc_error=_APIError(code, "Could not find the function public.grant_feedback_reward"))
    assert gfr.main(["--user-id", _UID, "--ref", _REF, "--apply"], db=db) == 1
    assert "apply migration 199 first" in capsys.readouterr().out
    assert len(db.rpc_calls) == 1


def test_any_other_rpc_error_exits_one_without_the_migration_hint(capsys):
    db = _FakeDB(rpc_error=_APIError("57014", "canceling statement due to statement timeout"))
    assert gfr.main(["--user-id", _UID, "--ref", _REF, "--apply"], db=db) == 1
    out = capsys.readouterr().out
    assert "migration 199" not in out and "statement timeout" in out


# ── finding the account ──────────────────────────────────────────────────────


def test_an_unknown_user_exits_one_without_calling_the_rpc(capsys):
    db = _FakeDB(users=[])
    assert gfr.main(["--user-id", _UID, "--ref", _REF, "--apply"], db=db) == 1
    assert db.rpc_calls == []
    assert "no public.users row" in capsys.readouterr().out


def test_email_is_trimmed_and_lowercased_before_the_lookup():
    db = _FakeDB()
    assert gfr.main(["--email", "  SomeOne@X.com ", "--ref", _REF, "--apply"], db=db) == 0
    assert db.table_calls[0] == ("users", (("email", "someone@x.com"),))
    assert db.rpc_calls[0][1]["p_user_id"] == _UID, "the grant goes to the id the lookup found"


def test_the_guest_account_is_refused_even_when_found_by_email(capsys):
    db = _FakeDB(users=[{"id": gfr.GUEST_USER_ID, "email": "guest@local", "tier": "free"}])
    assert gfr.main(["--email", "guest@local", "--ref", _REF, "--apply"], db=db) == 1
    assert db.rpc_calls == []


def test_a_read_failure_exits_one_without_calling_the_rpc(capsys):
    db = _FakeDB()
    db.read_errors["credit_transactions"] = RuntimeError("connection reset")
    assert gfr.main(["--user-id", _UID, "--ref", _REF, "--apply"], db=db) == 1
    assert db.rpc_calls == []


def test_a_new_account_with_no_balance_row_can_still_be_rewarded(capsys):
    """ensure_credit_period (inside the RPC) creates the row on a first touch."""
    db = _FakeDB(credits=[])
    assert gfr.main(["--user-id", _UID, "--ref", _REF, "--apply"], db=db) == 0
    assert len(db.rpc_calls) == 1
    assert "no balance row yet" in capsys.readouterr().out


@pytest.mark.parametrize("argv", [
    ["--user-id", "not-a-uuid", "--ref", _REF, "--apply"],
    ["--user-id", gfr.GUEST_USER_ID, "--ref", _REF, "--apply"],
    ["--user-id", _UID, "--ref", "R-7K2Q9", "--apply"],             # one character short
    ["--user-id", _UID, "--ref", "R-0K2Q9M", "--apply"],            # 0 is not in the alphabet
    ["--user-id", _UID, "--ref", _REF, "--credits", "0", "--apply"],
    ["--user-id", _UID, "--ref", _REF, "--credits", "201", "--apply"],
    ["--email", "   ", "--ref", _REF, "--apply"],
])
def test_bad_input_exits_one_before_touching_the_database(argv):
    db = _FakeDB()
    assert gfr.main(argv, db=db) == 1
    assert db.rpc_calls == [] and db.table_calls == []


@pytest.mark.parametrize("argv", [
    ["--ref", _REF],                                         # no account at all
    ["--user-id", _UID, "--email", "a@b.c", "--ref", _REF],  # both
    ["--user-id", _UID],                                     # no report code
])
def test_the_argument_contract_is_enforced_by_argparse(argv):
    with pytest.raises(SystemExit) as exc:
        gfr.main(argv, db=_FakeDB())
    assert exc.value.code == 2


# ── pure helpers ─────────────────────────────────────────────────────────────


def test_the_contract_constants():
    assert (gfr.DEFAULT_CREDITS, gfr.MAX_CREDITS) == (20, 200)
    assert (gfr.REASON, gfr.RPC_NAME) == ("feedback_reward", "grant_feedback_reward")
    assert gfr.GUEST_USER_ID == "00000000-0000-4000-8000-00000000dead"
    assert (gfr.REPORT_CODE_PREFIX, gfr.REPORT_CODE_LENGTH) == ("R-", 6)
    assert gfr.REPORT_CODE_ALPHABET == "ABCDEFGHJKMNPQRSTUVWXYZ23456789"


@pytest.mark.parametrize("value,expected", [
    (_UID, _UID),
    (_UID.upper(), _UID),
    (f"  {_UID}\n", _UID),
    ("6f1c2a3b4d5e4f608a7b9c0d1e2f3a4b", _UID),   # the hyphen-less form is the same UUID
])
def test_parse_user_id_canonicalises(value, expected):
    assert gfr.parse_user_id(value) == expected


@pytest.mark.parametrize("value", [
    "", "   ", None, 42, "not-a-uuid", "6f1c2a3b-4d5e", "00000000-0000-4000-8000-00000000DEAD",
])
def test_parse_user_id_refuses_junk_and_the_guest(value):
    with pytest.raises(ValueError):
        gfr.parse_user_id(value)


@pytest.mark.parametrize("value,expected", [
    ("R-7K2Q9M", "R-7K2Q9M"),
    ("r-7k2q9m", "R-7K2Q9M"),
    ("  R-7K2Q9M  ", "R-7K2Q9M"),
    ("R-ZZ2345", "R-ZZ2345"),
    ("testflight-2026-10-04", "testflight-2026-10-04"),   # a report without a code: kept as typed
    ("X" * 64, "X" * 64),
])
def test_normalize_ref(value, expected):
    assert gfr.normalize_ref(value) == expected


@pytest.mark.parametrize("value", [
    "", "   ", None, 7,
    "R-7K2Q9",       # too short
    "R-7K2Q9MM",     # too long
    "R-7K2Q9O",      # O: left out of the alphabet
    "R-1K2Q9M",      # 1
    "R-IK2Q9M",      # I
    "R-LK2Q9M",      # L
    "R-0K2Q9M",      # 0
    "R-7K 2Q9",      # inner space
    "X" * 65,        # over the SQL's limit
])
def test_normalize_ref_refuses(value):
    with pytest.raises(ValueError):
        gfr.normalize_ref(value)


@pytest.mark.parametrize("value", [1, 20, 200])
def test_validate_credits_accepts_the_range(value):
    assert gfr.validate_credits(value) == value


@pytest.mark.parametrize("value", [0, -1, 201, 2000, None, "20", 20.0, True])
def test_validate_credits_refuses(value):
    with pytest.raises(ValueError):
        gfr.validate_credits(value)


def test_prior_reward_lines_flags_only_the_matching_ref():
    lines, already = gfr.prior_reward_lines([_ledger_row(41, "R-ABC234"), _ledger_row(40, _REF)], _REF)
    assert already is True
    assert len(lines) == 2
    assert "THIS REPORT" in lines[1] and "THIS REPORT" not in lines[0]
    assert "#40" in lines[1] and "+20" in lines[1]


@pytest.mark.parametrize("rows", [None, [], [_ledger_row(41, "R-ABC234")], [_ledger_row(42, None)]])
def test_prior_reward_lines_without_a_match(rows):
    lines, already = gfr.prior_reward_lines(rows, _REF)
    assert already is False
    assert len(lines) == len(rows or [])


@pytest.mark.parametrize("data,code,needle", [
    ({"outcome": "granted", "credits": 20, "spendable": 60}, 0, "✓ granted 20"),
    ([{"outcome": "granted", "credits": 20, "spendable": 60}], 0, "✓ granted 20"),
    ({"outcome": "replay", "credits": 20, "spendable": 60}, 0, "nothing changed"),
    ({"outcome": "invalid", "reason": "bad_ref"}, 1, "bad_ref"),
    ({"outcome": "invalid"}, 1, "no reason given"),
    ({"outcome": "surprise"}, 1, "unexpected outcome"),
    (None, 1, "unexpected response"),
    ([], 1, "unexpected response"),
    ("granted", 1, "unexpected response"),
])
def test_interpret_outcome(data, code, needle):
    got_code, message = gfr.interpret_outcome(data)
    assert got_code == code
    assert needle in message
