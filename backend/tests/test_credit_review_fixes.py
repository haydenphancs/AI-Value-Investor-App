"""Outlier tests for the credit-enforcement bugs found in the adversarial review.

Backend, cleanly testable slice:
  * #6 research /generate: an insert that RAISES (not just empty .data) still refunds
    the precharge and returns a structured error (guest → no charge, no refund).
  * (#7 ticker /report/chat was deleted 2026-09-11 — no client called it and it bypassed
    every chat guardrail; its tests went with it.)
  * #1 GET /me/credits: rolls the monthly allocation (ensure_period) for an authed user
    and SKIPS the guest sentinel.
  * Why a refund that lands after a period roll cannot mint, and 112's two grant invariants —
    pinned on the LIVE SQL bodies through tests/_migration_sql.py (IAP audit F19).

All collaborators mocked — no Supabase / FMP / Gemini. Mirrors the existing
test_research_concurrency_cap / test_ticker_report_credits mock style.
"""

import json
import re
from unittest.mock import AsyncMock, MagicMock

import pytest

from _migration_sql import (if_block, insert_rows, latest_definition, migration_sources, norm_sql,
                            set_items, statements)

import app.api.v1.endpoints.research as research
import app.api.v1.endpoints.users as users
from app.api.error_response import ErrorCode
from app.dependencies import GUEST_USER_ID
from app.schemas.research import GenerateResearchRequest


def _credit_mock(monkeypatch, module, *, precharge_return=100):
    inst = MagicMock()
    inst.precharge.return_value = precharge_return
    inst.refund_ledgered.return_value = 80
    cls = MagicMock(return_value=inst)
    cls.DEEP_RESEARCH_COST = 20
    monkeypatch.setattr(module, "CreditService", cls)
    return inst


def _generate_supabase_insert_raises():
    """Supabase mock: the two in-flight COUNT queries succeed, the research_reports
    INSERT (3rd execute) raises — the exact 'insert raised, not empty' failure."""
    q = MagicMock()
    for m in ("select", "eq", "in_", "gte", "insert"):
        getattr(q, m).return_value = q
    q.execute.side_effect = [
        MagicMock(count=0, data=[]),   # per-user concurrency cap
        MagicMock(count=0, data=[]),   # global admission cap
        RuntimeError("insert boom"),   # research_reports insert RAISES
    ]
    sb = MagicMock()
    sb.table.return_value = q
    return sb


def _stub_fmp(monkeypatch):
    fmp = MagicMock()
    fmp.get_company_profile = AsyncMock(return_value={})
    monkeypatch.setattr("app.integrations.fmp.get_fmp_client", lambda: fmp)


def _req():
    return GenerateResearchRequest(stock_id="AAPL", investor_persona="warren_buffett")


# ── #6 research /generate: insert RAISE still refunds ────────────────

@pytest.mark.asyncio
async def test_research_insert_raise_refunds_precharge(monkeypatch):
    credit = _credit_mock(monkeypatch, research, precharge_return=99)
    _stub_fmp(monkeypatch)
    resp = await research.generate_research_report(
        request=_req(), user={"id": "authed-1"},
        supabase=_generate_supabase_insert_raises(), _rate_limit=None,
    )
    assert json.loads(resp.body)["error_code"] == ErrorCode.REPORT_GENERATION_FAILED.value
    credit.precharge.assert_called_once()
    credit.refund_ledgered.assert_called_once()   # charge handed back despite the raise


@pytest.mark.asyncio
async def test_research_insert_raise_refunds_the_charge(monkeypatch):
    """Insert failure AFTER a successful precharge must hand the credits back.

    Was `test_research_guest_insert_raise_no_refund`, asserting the guest branch charged
    nothing so had nothing to refund. Generation is account-only now, so every caller is
    charged and this path always owes a refund — the stronger property, and the one where a
    bug actually costs a user money.
    """
    credit = _credit_mock(monkeypatch, research)
    _stub_fmp(monkeypatch)
    resp = await research.generate_research_report(
        request=_req(), user={"id": "user-1"},
        supabase=_generate_supabase_insert_raises(), _rate_limit=None,
    )
    assert json.loads(resp.body)["error_code"] == ErrorCode.REPORT_GENERATION_FAILED.value
    credit.precharge.assert_called_once()
    credit.refund_ledgered.assert_called_once()


# ── #1 GET /me/credits: monthly reset on read ────────────────────────

def _credits_supabase(row):
    q = MagicMock()
    q.select.return_value = q
    q.eq.return_value = q
    q.limit.return_value = q          # endpoint now reads via .limit(1), not .single()
    q.single.return_value = q         # harmless back-compat
    q.execute.return_value = MagicMock(data=[row])  # list result (limit(1))
    sb = MagicMock()
    sb.table.return_value = q
    return sb


@pytest.mark.asyncio
async def test_get_credits_rolls_month_for_authed(monkeypatch):
    inst = MagicMock()
    monkeypatch.setattr(users, "CreditService", MagicMock(return_value=inst))
    sb = _credits_supabase({"total": 50, "used": 10, "remaining": 40, "resets_at": None})
    await users.get_user_credits(user={"id": "authed-1"}, supabase=sb)
    inst.ensure_period.assert_called_once_with("authed-1")   # fresh balance on read


@pytest.mark.asyncio
async def test_get_credits_skips_reset_for_guest(monkeypatch):
    inst = MagicMock()
    monkeypatch.setattr(users, "CreditService", MagicMock(return_value=inst))
    sb = _credits_supabase({"total": 100000, "used": 0, "remaining": 100000, "resets_at": None})
    await users.get_user_credits(user={"id": GUEST_USER_ID}, supabase=sb)
    inst.ensure_period.assert_not_called()   # guest is never reset


# ── A refund that lands AFTER a period roll cannot mint credits ─────────────
#
# Flagged by the 2026-08-07 audit and left unresolved (its adversarial verifiers died on a
# session limit). Resolved by reading the SQL: it is a FALSE ALARM, and this pins why so it is
# not re-chased.
#
# The worry: a report fails at 23:59 on the last day of the month (or of a paid period),
# `ensure_credit_period` rolls the period, and the refund then lands in the NEW period —
# crediting back 20 credits that were spent out of the OLD one.
#
# It cannot mint, for two reasons that live in two functions:
#
#   1. Every roll that STARTS a fresh pool sets `used = 0`: the calendar reset on the ET 1st, a new
#      paid period, and a lapse back to the calendar. The other two rolls are deliberately NOT
#      claimed: the refund clawback keeps `used` under its calendar floor (IAP audit F15's gap),
#      and a REFUND_REVERSED reinstatement restores the refunded period instead of starting one.
#   2. `refund_credits` (142, DROP + CREATE) caps each pool by LEAST(what the paired debit took
#      from it, p_amount, `used` / `purchased_used` read under the balance lock). After a fresh roll
#      that cap is 0: the refund moves nothing, writes NO ledger row (it returns first), and answers
#      `capped_to_zero`, which pages — the user IS owed (K42) — instead of minting.
#
# A refund that does move records the ACTUAL amounts moved, never p_amount, so sum(delta) cannot
# desync from the balance, and its one balance write never touches `total` or `resets_at`.
#
# Until 2026-10-21 (IAP audit F19) these tests opened migrations 100 and 101 BY NUMBER, comments
# included: bodies that stopped running long ago, so they passed whatever the live functions did.
# They now read the LIVE definitions through tests/_migration_sql.py, and
# `test_the_live_sql_detectors_catch_each_later_redefinition` feeds each detector in-memory later
# migrations (never a file in migrations/) that must turn it red — or, for a CORRECT later
# redefinition, keep it green.
#
# What a correct later redefinition may do (migration 200 is planned to redefine both functions
# for F10/F11/F15), so these stay green on it and red on a regression:
#   * refund_credits may PERFORM ensure_credit_period (F10: roll first, as every spend does), as
#     long as it re-reads the pools after that and before the caps;
#   * a refund the clawback already covered may write a 0/0/0 pairing row (F15), inside the
#     zero-move block — it moves nothing and records zeros;
#   * a reinstatement may keep the refund window's spend in `used` (F15).
# Assumed unchanged (a change is a deliberate edit of `_REFUND_CAPS` in the same commit): 142's
# four LEAST caps — F15 absorbs into v_spent_granted BEFORE them.

_LEDGER = "public.credit_transactions"


def _live(name: str, sources=None) -> tuple[str, str]:
    """(file, whitespace-normalised comment-stripped body) of the live public.<name>."""
    d = latest_definition(name, sources)
    return d.file, norm_sql(d.body)


def _user_credit_updates(sql: str) -> list[tuple]:
    """(offset of its `;`, SET items, WHERE, RETURNING) of each UPDATE public.user_credits."""
    out = []
    for at, stmt in statements(sql, r"\bUPDATE public\.user_credits\b"):
        m = re.fullmatch(r"UPDATE public\.user_credits SET (.*?) WHERE (.*?)(?: RETURNING (.*))?", stmt)
        assert m, f"an UPDATE public.user_credits this guard cannot parse: {stmt}"
        out.append((at, set_items(m.group(1)), m.group(2), m.group(3)))
    return out


# Ledger reasons of the rolls (an UPDATE that sets resets_at) of ensure_credit_period.
_FRESH_POOL = ("'monthly_reset'", "'plan_period'")       # starts a new pool: must zero `used`
_NOT_FRESH = ("'tier_revoked'", "'plan_reinstated'")      # clawback / reinstatement: not claimed


def _fresh_pool_problems(sources=None) -> list[str]:
    source, body = _live("ensure_credit_period", sources)
    out, fresh = [], []
    for at, sets, _where, _ret in _user_credit_updates(body):
        if "resets_at" not in sets:
            continue                                      # not a roll (the mid-month top-up)
        after = body[at:]
        stop = re.search(r"\bRETURN\b", after)            # the roll's own branch ends there
        reasons = {row.get("reason") for row in insert_rows(after[:stop.start() if stop else None], _LEDGER)}
        if len(reasons) != 1 or not reasons <= set(_FRESH_POOL + _NOT_FRESH):
            out.append(f"{source}: a roll (SET {sets}) ledgers {sorted(map(str, reasons))} — classify it "
                       "in _FRESH_POOL or _NOT_FRESH")
            continue
        reason = reasons.pop()
        if reason in _FRESH_POOL:
            fresh.append(reason)
            if sets.get("used") != "0":
                out.append(f"{source}: the {reason} roll sets used = {sets.get('used')!r}, not 0 — a late "
                           "refund of the old period's charge would be paid out of the new pool")
    if set(fresh) != set(_FRESH_POOL) or len(fresh) < 3:
        out.append(f"{source}: expected the calendar reset and the lapse ('monthly_reset') and a new "
                   f"paid period ('plan_period'); found {fresh}")
    return out


_POOL_READ = ("SELECT used, purchased_used INTO v_old_used, v_old_pused FROM public.user_credits "
              "WHERE user_id = p_user_id")
_REFUND_CAPS = [
    ("v_back_purch", "LEAST(GREATEST(v_spent_purch, 0), p_amount, v_old_pused)"),
    ("v_back_granted", "LEAST(GREATEST(v_spent_granted, 0), p_amount - v_back_purch, v_old_used)"),
    ("v_back_granted", "LEAST(p_amount, v_old_used)"),
    ("v_back_purch", "LEAST(p_amount - v_back_granted, v_old_pused)"),
]


def _refund_cap_problems(sources=None) -> list[str]:
    source, body = _live("refund_credits", sources)
    out = []
    if int(source[:3]) < 142:
        out.append(f"the live refund_credits is {source}, older than 142's DROP + CREATE")
    reads = [(m.start(), bool(m.group(1)))
             for m in re.finditer(re.escape(_POOL_READ) + r"( FOR UPDATE)?;", body)]
    targets = [m.group(1) for m in re.finditer(r"\bINTO\s+(?:STRICT\s+)?([\w\s,]+?)(?=\s+FROM\b|\s*;)", body)
               if re.search(r"\bv_old_p?used\b", m.group(1))]
    if len(targets) != len(reads) or re.search(r"\bv_old_p?used\s*:=", body):
        out.append(f"{source}: v_old_used / v_old_pused must come ONLY from `{_POOL_READ}`")
    if not reads or not reads[0][1]:
        out.append(f"{source}: the first read of the pools must take the balance lock (FOR UPDATE)")
    caps = [(m.start(), m.group(1), m.group(2))
            for m in re.finditer(r"\b(v_back_(?:granted|purch)) := (.*?);", body)]
    if [(var, expr) for _at, var, expr in caps] != _REFUND_CAPS:
        out.append(f"{source}: the refund caps changed:\n  {[(v, e) for _a, v, e in caps]}\n  "
                   f"expected {_REFUND_CAPS}")
    if caps and reads:
        first_cap = caps[0][0]
        before = [at for at, _locked in reads if at < first_cap]
        # Anything that can move the pools between the last read and the caps (F10's PERFORM of
        # ensure_credit_period, a balance write) makes the caps compare against a stale pool.
        movers = list(re.finditer(r"\bPERFORM\b|\bUPDATE public\.user_credits\b", body[:first_cap]))
        if not before:
            out.append(f"{source}: the caps run before the pools are read")
        elif movers and movers[-1].start() > before[-1]:
            out.append(f"{source}: `{movers[-1].group()}` runs after the last pool read and before the "
                       "caps — re-read used/purchased_used after it")
    return out


_MOVED_ROW = {"user_id": "p_user_id", "delta": "v_back_granted + v_back_purch",
              "granted_delta": "v_back_granted", "purchased_delta": "v_back_purch",
              "reason": "p_reason", "ref_id": "p_ref_id", "balance_after": "v_spendable",
              "reverses_id": "v_debit_id"}
_ZERO_MOVE = "IF v_back_granted + v_back_purch = 0 THEN"


def _is_zero_row(row: dict) -> bool:
    return row.get("delta") == row.get("granted_delta") == row.get("purchased_delta") == "0"


def _is_moved_row(row: dict) -> bool:
    """The refund's ledger row. Since migration 200 it also records `covered` (what a clawback had
    already paid for), and only as the absorbed amount: a literal there would misstate a replay."""
    rest = {k: v for k, v in row.items() if k != "covered"}
    return rest == _MOVED_ROW and row.get("covered", "v_absorb") == "v_absorb"


def _refund_ledger_problems(sources=None) -> list[str]:
    source, body = _live("refund_credits", sources)
    rows = insert_rows(body, _LEDGER)
    out = []
    if sum(map(_is_moved_row, rows)) != 1:
        out.append(f"{source}: expected ONE ledger row recording the amounts moved, {_MOVED_ROW}; got {rows}")
    stray = [row for row in rows if not _is_moved_row(row) and not _is_zero_row(row)]
    if stray:
        out.append(f"{source}: a ledger row records something other than the amounts moved: {stray}")
    if body.count(_ZERO_MOVE) != 1:
        return out + [f"{source}: the zero-move return `{_ZERO_MOVE}` is gone or doubled"]
    block = if_block(body, body.index(_ZERO_MOVE))
    inside = insert_rows(block, _LEDGER)
    if not re.search(r"\bRETURN\b", block):
        out.append(f"{source}: a refund that moves nothing must RETURN before the ledger row")
    if any(not _is_zero_row(row) for row in inside):
        out.append(f"{source}: a refund that moves nothing writes a non-zero ledger row: {inside}")
    if sum(map(_is_zero_row, rows)) != len(inside):
        out.append(f"{source}: a 0/0/0 pairing row outside the zero-move block")
    return out


_REFUND_SETS = {"used": "used - v_back_granted", "purchased_used": "purchased_used - v_back_purch",
                "updated_at": "NOW()"}


def _refund_update_problems(sources=None) -> list[str]:
    source, body = _live("refund_credits", sources)
    ups = _user_credit_updates(body)
    if len(ups) != 1:
        return [f"{source}: refund_credits must write the balance exactly once, found {len(ups)}"]
    at, sets, where, returning = ups[0]
    out = []
    if sets != _REFUND_SETS:
        out.append(f"{source}: the refund's balance write is SET {sets}: it must move used / "
                   "purchased_used back and never touch total (the allocation) or resets_at (the period)")
    if where != "user_id = p_user_id" or returning != "spendable INTO v_spendable":
        out.append(f"{source}: the refund's balance write is WHERE {where} RETURNING {returning}")
    if body.count(_ZERO_MOVE) != 1:
        return out + [f"{source}: the zero-move return `{_ZERO_MOVE}` is gone or doubled"]
    start = body.index(_ZERO_MOVE)
    block = if_block(body, start)
    if start + len(block) > at or not re.search(r"\bRETURN\b", block):
        out.append(f"{source}: a refund that moves nothing must return BEFORE the balance write")
    return out


def test_every_roll_that_starts_a_fresh_pool_zeroes_used():
    """Half of the reason a late refund cannot mint."""
    assert _fresh_pool_problems() == []


def test_refund_floors_used_at_zero():
    """The other half. Without the `used` caps, a refund crossing the boundary WOULD mint."""
    assert _refund_cap_problems() == []


def test_the_refund_ledger_records_the_actual_change_not_the_request():
    """So a no-op refund writes no row rather than +20, and sum(delta) stays consistent with the
    balance."""
    assert _refund_ledger_problems() == []


def test_refund_does_not_touch_total_or_resets_at():
    """A refund must not roll the period itself — that would be a second way to mint."""
    assert _refund_update_problems() == []


# ── 112's two grant invariants, which now live in ensure_credit_period ─────────────
#
# 112's grant_tier_upgrade topped a user up to users.tier's allocation, and was safe only because
# of two properties: a replay guard (no grant once the allocation is met — else every
# Transaction.updates replay mints) and a GREATEST, never a bare assignment, that leaves `used`
# alone (else a downgrade deletes paid credits and an upgrade's value depends on the day). Since
# 196 grant_tier_upgrade is a pure delegate to ensure_credit_period, and both properties live in
# its mid-month top-up: `IF v_alloc > v_row.tier_alloc THEN` (a replay finds tier_alloc = v_alloc)
# and ONE UPDATE `total = GREATEST(v_alloc, v_row.total), tier_alloc = v_alloc`. 112's own pin
# ("the UPDATE touches total, never used") described no live code — since 196 the grant has no
# UPDATE at all — so it is restated here. test_iap_entitlement's
# `test_the_grant_delegates_and_the_mid_month_top_up_keeps_112s_two_invariants` delegates to these.

_GRANT_DELEGATE = ("BEGIN IF p_user_id = '00000000-0000-4000-8000-00000000dead'::UUID THEN RETURN 0; "
                   "END IF; RETURN public.ensure_credit_period(p_user_id); END;")
_TOP_UP_GUARD = "IF v_alloc > v_row.tier_alloc THEN"
_TOP_UP_SETS = {"total": "GREATEST(v_alloc, v_row.total)", "tier_alloc": "v_alloc", "updated_at": "NOW()"}


def _grant_problems(sources=None) -> list[str]:
    source, body = _live("grant_tier_upgrade", sources)
    out = []
    if int(source[:3]) < 196:
        out.append(f"the live grant_tier_upgrade is {source}, older than 196's delegate")
    if body != _GRANT_DELEGATE:
        out.append(f"{source}: grant_tier_upgrade is no longer a pure delegate to ensure_credit_period "
                   f"(a grant of its own is the double grant 196 removed): {body}")
    return out


def _top_up_problems(sources=None) -> list[str]:
    source, body = _live("ensure_credit_period", sources)
    if body.count(_TOP_UP_GUARD) != 1:
        return [f"{source}: the mid-month top-up guard `{_TOP_UP_GUARD}` (112's replay guard) occurs "
                f"{body.count(_TOP_UP_GUARD)} times, not once"]
    ups = _user_credit_updates(if_block(body, body.index(_TOP_UP_GUARD)))
    if len(ups) != 1:
        return [f"{source}: the top-up must write the balance exactly once, found {len(ups)}"]
    _at, sets, where, _returning = ups[0]
    out = []
    if sets != _TOP_UP_SETS:
        out.append(f"{source}: the top-up is SET {sets}; expected {_TOP_UP_SETS} — never `used`, and "
                   "total only ever raised (a bare assignment deletes credits on a downgrade)")
    if where != "user_id = p_user_id":
        out.append(f"{source}: the top-up writes WHERE {where}")
    return out


def _swap(old: str, new: str):
    """A mutation of the normalised live body; its anchor must occur exactly once."""
    def mutate(body: str) -> str:
        assert body.count(old) == 1, f"mutation anchor drifted ({body.count(old)} hits): {old!r}"
        return body.replace(old, new)
    return mutate


def _drop_zero_move_block(body: str) -> str:
    return body.replace(if_block(body, body.index(_ZERO_MOVE)), "", 1)


def _reinstate_keeps_window_spend(body: str) -> str:
    # Pattern, not a fixed anchor: it must match 198's `total = v_restore, used = 0` and F15's
    # planned `total = v_total, used = v_used` alike.
    out, hits = re.subn(r"(IF v_reinstate THEN .*?UPDATE public\.user_credits SET total = )[^,]+, used = [^,]+,",
                        r"\1v_total, used = v_used,", body, count=1, flags=re.S)
    assert hits == 1, "the reinstatement's balance write moved — re-derive this mutation"
    return out


_ZERO_ROW_INSERT = ("INSERT INTO public.credit_transactions (user_id, delta, granted_delta, "
                    "purchased_delta, reason, ref_id, balance_after, reverses_id) VALUES ")
# The live refund row (since 200 it also records `covered`).
_MOVED_INSERT = ("INSERT INTO public.credit_transactions (user_id, delta, granted_delta, "
                 "purchased_delta, reason, ref_id, balance_after, reverses_id, covered) VALUES "
                 "(p_user_id, v_back_granted + v_back_purch,")

# case: (function, mutation of its normalised live body, detector, must the detector go red?)
# The ok-cases are CORRECT redefinitions in the shape migration 200 plans: they must stay green.
_LIVE_SQL_MUTANTS = {
    "grant tops up on its own again": (
        "grant_tier_upgrade", _swap("RETURN public.ensure_credit_period(p_user_id);",
                                    "UPDATE public.user_credits SET total = total + 4000 WHERE user_id = "
                                    "p_user_id; RETURN public.ensure_credit_period(p_user_id);"),
        _grant_problems, True),
    "grant's delegate kept only in a comment": (
        "grant_tier_upgrade", _swap("RETURN public.ensure_credit_period(p_user_id);",
                                    "/* RETURN public.ensure_credit_period(p_user_id); */ RETURN 0;"),
        _grant_problems, True),
    "top-up replay guard dropped": (
        "ensure_credit_period", _swap(_TOP_UP_GUARD, "IF TRUE THEN"), _top_up_problems, True),
    "top-up zeroes used": (
        "ensure_credit_period", _swap("SET total = GREATEST(v_alloc, v_row.total), tier_alloc = v_alloc,",
                                      "SET total = GREATEST(v_alloc, v_row.total), used = 0, "
                                      "tier_alloc = v_alloc,"),
        _top_up_problems, True),
    "top-up assigns total bare": (
        "ensure_credit_period", _swap("SET total = GREATEST(v_alloc, v_row.total),", "SET total = v_alloc,"),
        _top_up_problems, True),
    "top-up's GREATEST kept only in a comment": (
        "ensure_credit_period", _swap("SET total = GREATEST(v_alloc, v_row.total),",
                                      "SET total = v_alloc, -- GREATEST(v_alloc, v_row.total),\n"),
        _top_up_problems, True),
    "calendar reset keeps used": (
        "ensure_credit_period",
        _swap("SET total = v_alloc, used = 0, tier_alloc = v_alloc, resets_at = v_next_reset, updated_at",
              "SET total = v_alloc, tier_alloc = v_alloc, resets_at = v_next_reset, updated_at"),
        _fresh_pool_problems, True),
    "new paid period keeps used": (
        "ensure_credit_period", _swap("used = 0, tier_alloc = v_plan_alloc,", "tier_alloc = v_plan_alloc,"),
        _fresh_pool_problems, True),
    "lapse's used = 0 kept only in a comment": (
        "ensure_credit_period",
        _swap("SET total = v_alloc, used = 0, tier_alloc = v_alloc, resets_at = v_next_reset, paid_period_id",
              "SET total = v_alloc, -- used = 0,\n tier_alloc = v_alloc, resets_at = v_next_reset, "
              "paid_period_id"),
        _fresh_pool_problems, True),
    "a roll nobody classified": (
        "ensure_credit_period", _swap("0, 'plan_period',", "0, 'plan_started',"), _fresh_pool_problems, True),
    "ok: a reinstatement keeps the refund window's spend (F15)": (
        "ensure_credit_period", _reinstate_keeps_window_spend, _fresh_pool_problems, False),
    "refund floor removed": (
        "refund_credits", _swap("v_back_granted := LEAST(p_amount, v_old_used);", "v_back_granted := p_amount;"),
        _refund_cap_problems, True),
    "refund floor kept only in a comment": (
        "refund_credits", _swap("v_back_granted := LEAST(p_amount, v_old_used);",
                                "v_back_granted := p_amount; -- LEAST(p_amount, v_old_used);\n"),
        _refund_cap_problems, True),
    "split cap forgets the paired debit": (
        "refund_credits", _swap("LEAST(GREATEST(v_spent_purch, 0), p_amount, v_old_pused)",
                                "LEAST(p_amount, v_old_pused)"),
        _refund_cap_problems, True),
    "pools read without the balance lock": (
        "refund_credits", _swap(_POOL_READ + " FOR UPDATE;", _POOL_READ + ";"), _refund_cap_problems, True),
    "pool overwritten after the read": (
        "refund_credits", _swap(_POOL_READ + " FOR UPDATE;", _POOL_READ + " FOR UPDATE; v_old_used := 1000;"),
        _refund_cap_problems, True),
    "rolls the period first and never re-reads (F10 half done)": (
        "refund_credits", _swap("IF v_have_split THEN",
                                "PERFORM public.ensure_credit_period(p_user_id); IF v_have_split THEN"),
        _refund_cap_problems, True),
    "ok: rolls the period first, then re-reads (F10)": (
        "refund_credits", _swap("IF v_have_split THEN",
                                f"PERFORM public.ensure_credit_period(p_user_id); {_POOL_READ}; "
                                "IF v_have_split THEN"),
        _refund_cap_problems, False),
    "refund ledgers the request": (
        "refund_credits", _swap("(p_user_id, v_back_granted + v_back_purch, v_back_granted, v_back_purch,",
                                "(p_user_id, p_amount, v_back_granted, v_back_purch,"),
        _refund_ledger_problems, True),
    "a refund that moves nothing ledgers the request": (
        "refund_credits", _swap(_ZERO_MOVE, f"{_ZERO_MOVE} {_ZERO_ROW_INSERT}(p_user_id, p_amount, p_amount, 0, "
                                            "p_reason, p_ref_id, 0, v_debit_id);"),
        _refund_ledger_problems, True),
    "ok: a covered refund writes a 0/0/0 pairing row (F15), ledger": (
        "refund_credits", _swap(_ZERO_MOVE, f"{_ZERO_MOVE} IF v_absorb > 0 THEN {_ZERO_ROW_INSERT}(p_user_id, 0, "
                                            "0, 0, p_reason, p_ref_id, v_spendable, v_debit_id); RETURN "
                                            "jsonb_build_object('outcome', 'covered_by_clawback'); END IF;"),
        _refund_ledger_problems, False),
    "ok: a covered refund writes a 0/0/0 pairing row (F15), balance write": (
        "refund_credits", _swap(_ZERO_MOVE, f"{_ZERO_MOVE} IF v_absorb > 0 THEN {_ZERO_ROW_INSERT}(p_user_id, 0, "
                                            "0, 0, p_reason, p_ref_id, v_spendable, v_debit_id); RETURN "
                                            "jsonb_build_object('outcome', 'covered_by_clawback'); END IF;"),
        _refund_update_problems, False),
    "refund writes total": (
        "refund_credits", _swap("SET used = used - v_back_granted,",
                                "SET used = used - v_back_granted, total = total + v_back_granted,"),
        _refund_update_problems, True),
    "refund rolls the period": (
        "refund_credits", _swap("updated_at = NOW() WHERE user_id = p_user_id RETURNING",
                                "updated_at = NOW(), resets_at = now() WHERE user_id = p_user_id RETURNING"),
        _refund_update_problems, True),
    "a refund that moves nothing falls through to the write": (
        "refund_credits", _drop_zero_move_block, _refund_update_problems, True),
    "a second balance write": (
        "refund_credits", _swap(_MOVED_INSERT, "UPDATE public.user_credits SET used = 0 WHERE user_id = "
                                               f"p_user_id; {_MOVED_INSERT}"),
        _refund_update_problems, True),
}


def _later_sources(name: str, mutate, create: str) -> list[tuple[str, str]]:
    """The real migrations plus ONE in-memory later migration redefining public.<name> with the
    mutated body — DROP + plain CREATE, or CREATE OR REPLACE. Nothing is written to migrations/."""
    d = latest_definition(name)
    body = mutate(norm_sql(d.body))
    drop = "" if "replace" in create.lower() else f"DROP FUNCTION IF EXISTS public.{name};\n"
    later = f"BEGIN;\n{drop}{create} public.{name}({d.header}AS $fn$\n{body}\n$fn$;\nCOMMIT;\n"
    return migration_sources() + [("999_synthetic_redefinition.sql", later)]


@pytest.mark.parametrize("create", ["CREATE FUNCTION", "CREATE OR REPLACE FUNCTION"])
@pytest.mark.parametrize("case", sorted(_LIVE_SQL_MUTANTS))
def test_the_live_sql_detectors_catch_each_later_redefinition(case, create):
    """testing.md rule 3 as a standing test, for the five detectors above (and the two that
    test_iap_entitlement delegates to). The old by-number tests stayed green under every red case
    here, because they never read the live definition."""
    name, mutate, detector, must_fail = _LIVE_SQL_MUTANTS[case]
    assert detector() == [], "the real migrations must pass before a mutation can mean anything"
    sources = _later_sources(name, mutate, create)
    assert latest_definition(name, sources).file == "999_synthetic_redefinition.sql"
    if not must_fail:
        assert detector(sources) == [], f"{case}: a CORRECT later {name}() fails {detector.__name__}"
        return
    try:
        problems = detector(sources)
    except AssertionError as e:                 # an unparseable or vanished anchor is red too
        problems = [f"raised: {e}"]
    assert problems, f"{case}: a broken later {name}() passes {detector.__name__} — it is vacuous"


# ── 2026-10-12: the deep door's charge is named by the report it pays for ─────────────

def _generate_supabase(*, insert_raises=False, claim_raises=False, insert_rows=None,
                       threads=None):
    """A recording Supabase mock for /research/generate. Counts and inserts are separate
    chains so the insert payload and the claim can be inspected."""
    import threading

    record = {"insert": None, "claim": None, "stamps": [],
              "threads": threads if threads is not None else []}

    def _exec(result=None, raises=None):
        def _run():
            record["threads"].append(threading.get_ident())
            if raises is not None:
                raise raises
            return result
        return _run

    count_q = MagicMock()
    for m in ("select", "eq", "in_", "gte"):
        getattr(count_q, m).return_value = count_q
    count_q.execute.side_effect = _exec(MagicMock(count=0, data=[]))

    class _Insert:
        def __init__(self, payload):
            record["insert"] = payload
            self.payload = payload

        def execute(self):
            rows = insert_rows if insert_rows is not None else [dict(self.payload)]
            return _exec(MagicMock(data=rows), RuntimeError("insert boom") if insert_raises else None)()

    class _Claim:
        def __init__(self, payload):
            record["claim"] = {"payload": payload, "filters": []}

        def eq(self, *a):
            record["claim"]["filters"].append(("eq",) + a)
            return self

        def in_(self, *a):
            record["claim"]["filters"].append(("in",) + a)
            return self

        def execute(self):
            return _exec(MagicMock(data=[]), RuntimeError("claim boom") if claim_raises else None)()

    class _Stamp:
        """Migration 195's refund_outcome stamp — recorded APART from the orphan claim, so
        `record["claim"]` stays the claim the assertions above are about."""

        def __init__(self, payload):
            self.entry = {"payload": payload, "filters": [], "thread": None}
            record["stamps"].append(self.entry)

        def _f(self, *a):
            self.entry["filters"].append(a)
            return self

        eq = is_ = or_ = in_ = _f

        def execute(self):
            self.entry["thread"] = threading.get_ident()
            return _exec(MagicMock(data=[{"id": "x"}]))()

    class _Table:
        def select(self, *a, **k):
            return count_q.select(*a, **k)

        def insert(self, payload):
            return _Insert(payload)

        def update(self, payload):
            if "refund_outcome" in payload:
                return _Stamp(payload)
            return _Claim(payload)

    sb = MagicMock()
    sb.table.side_effect = lambda name: _Table()
    return sb, record


@pytest.fixture
def _no_worker(monkeypatch):
    """The deep door launches `_run_research_task`; replace it so no pipeline runs."""
    started = []

    async def _fake_task(report_id, ticker, persona, user_id):
        started.append(report_id)

    monkeypatch.setattr(research, "_run_research_task", _fake_task)
    return started


@pytest.mark.asyncio
async def test_the_deep_door_charges_with_the_minted_report_id(monkeypatch, _no_worker):
    from app.services.credit_service import ChargeRef

    credit = _credit_mock(monkeypatch, research)
    _stub_fmp(monkeypatch)
    sb, record = _generate_supabase()
    resp = await research.generate_research_report(
        request=_req(), user={"id": "u1"}, supabase=sb, _rate_limit=None,
    )
    ref = credit.precharge.call_args.kwargs["ref_id"]
    assert isinstance(ref, ChargeRef)
    assert ref == f"AAPL:{record['insert']['id']}", "the charge is not named by the row it pays for"
    assert resp.report_id == record["insert"]["id"]
    credit.refund_ledgered.assert_not_called()


@pytest.mark.asyncio
async def test_an_insert_failure_refunds_on_the_charge_ref(monkeypatch):
    credit = _credit_mock(monkeypatch, research)
    _stub_fmp(monkeypatch)
    sb, record = _generate_supabase(insert_raises=True)
    resp = await research.generate_research_report(
        request=_req(), user={"id": "u1"}, supabase=sb, _rate_limit=None,
    )
    assert json.loads(resp.body)["error_code"] == ErrorCode.REPORT_GENERATION_FAILED.value
    charge = credit.precharge.call_args.kwargs["ref_id"]
    credit.refund_ledgered.assert_called_once()
    kw = credit.refund_ledgered.call_args.kwargs
    assert kw["ref_id"] == charge and kw["reason"] == "report_refund"
    assert "quiet_no_match" not in kw, "charged in this request: a no-match must page"
    # The id-scoped claim armed before the refund, on THIS row.
    assert ("eq", "id", record["insert"]["id"]) in record["claim"]["filters"]


@pytest.mark.asyncio
async def test_a_transient_deep_precharge_is_compensated_with_the_same_ref(monkeypatch):
    from app.services.credit_service import CreditServiceUnavailable

    credit = _credit_mock(monkeypatch, research)
    credit.precharge.side_effect = CreditServiceUnavailable("edge 520")
    _stub_fmp(monkeypatch)
    sb, record = _generate_supabase()
    resp = await research.generate_research_report(
        request=_req(), user={"id": "u1"}, supabase=sb, _rate_limit=None,
    )
    body = json.loads(resp.body)
    assert resp.status_code == 409 and body["error_code"] == ErrorCode.SYSTEM_BUSY.value
    credit.refund_ledgered.assert_called_once()
    kw = credit.refund_ledgered.call_args.kwargs
    charge = credit.precharge.call_args.kwargs["ref_id"]
    assert kw["reason"] == "report_precharge_unconfirmed" and kw["quiet_no_match"] is True
    assert kw["ref_id"] == charge
    import re
    assert re.match(r"^AAPL:[0-9a-f-]{36}$", charge), charge
    assert body["details"]["charge_ref"] == charge
    assert record["insert"] is None, "a report row was inserted for an unconfirmed charge"


@pytest.mark.asyncio
async def test_an_insufficient_balance_is_never_compensated(monkeypatch):
    credit = _credit_mock(monkeypatch, research, precharge_return=None)
    _stub_fmp(monkeypatch)
    sb, record = _generate_supabase()
    resp = await research.generate_research_report(
        request=_req(), user={"id": "u1"}, supabase=sb, _rate_limit=None,
    )
    assert resp.status_code == 402
    credit.refund_ledgered.assert_not_called()
    assert record["insert"] is None


@pytest.mark.asyncio
async def test_a_raised_orphan_claim_still_refunds_now(monkeypatch):
    """The claim raising proves nothing: no row (the likeliest), a row it DID claim before its
    reply was lost, or a committed row it did not. Deferring to the sweep leaks in the first two;
    refunding now is safe because a later sweep refund of the same charge converges."""
    credit = _credit_mock(monkeypatch, research)
    _stub_fmp(monkeypatch)
    sb, _record = _generate_supabase(insert_raises=True, claim_raises=True)
    resp = await research.generate_research_report(
        request=_req(), user={"id": "u1"}, supabase=sb, _rate_limit=None,
    )
    assert json.loads(resp.body)["error_code"] == ErrorCode.REPORT_GENERATION_FAILED.value
    credit.refund_ledgered.assert_called_once()
    assert credit.refund_ledgered.call_args.kwargs["ref_id"] == credit.precharge.call_args.kwargs["ref_id"]


@pytest.mark.asyncio
async def test_a_cancel_after_the_charge_hands_it_back_detached(monkeypatch):
    import asyncio
    from app.services.credit_service import drain_detached_settlements

    credit = _credit_mock(monkeypatch, research)
    fmp = MagicMock()
    fmp.get_company_profile = AsyncMock(side_effect=asyncio.CancelledError())
    monkeypatch.setattr("app.integrations.fmp.get_fmp_client", lambda: fmp)
    sb, record = _generate_supabase()
    with pytest.raises(asyncio.CancelledError):
        await research.generate_research_report(
            request=_req(), user={"id": "u1"}, supabase=sb, _rate_limit=None,
        )
    assert await drain_detached_settlements(2.0) == 0
    credit.refund_ledgered.assert_called_once()
    kw = credit.refund_ledgered.call_args.kwargs
    assert kw["reason"] == "report_refund"
    assert kw["ref_id"] == credit.precharge.call_args.kwargs["ref_id"]
    assert record["insert"] is None


@pytest.mark.asyncio
async def test_an_unexpected_error_after_the_charge_refunds_it(monkeypatch):
    """Anything escaping between the charge and the hand-off to the worker must hand the
    charge back (inline, the frame is not being cancelled)."""
    credit = _credit_mock(monkeypatch, research)
    _stub_fmp(monkeypatch)
    sb, _record = _generate_supabase(insert_rows=[{"no_id_here": True}])
    with pytest.raises(KeyError):
        await research.generate_research_report(
            request=_req(), user={"id": "u1"}, supabase=sb, _rate_limit=None,
        )
    credit.refund_ledgered.assert_called_once()
    assert credit.refund_ledgered.call_args.kwargs["reason"] == "report_refund"


@pytest.mark.asyncio
async def test_a_handed_off_report_is_never_refunded_by_the_door(monkeypatch, _no_worker):
    credit = _credit_mock(monkeypatch, research)
    _stub_fmp(monkeypatch)
    sb, _record = _generate_supabase()
    await research.generate_research_report(
        request=_req(), user={"id": "u1"}, supabase=sb, _rate_limit=None,
    )
    credit.refund_ledgered.assert_not_called()


@pytest.mark.asyncio
async def test_deep_door_money_calls_leave_the_loop_thread(monkeypatch, _no_worker):
    import threading

    loop_thread = threading.get_ident()
    credit = _credit_mock(monkeypatch, research)
    seen = []
    credit.precharge.side_effect = lambda *a, **k: seen.append(threading.get_ident()) or 80
    _stub_fmp(monkeypatch)
    sb, record = _generate_supabase(threads=seen)
    await research.generate_research_report(
        request=_req(), user={"id": "u1"}, supabase=sb, _rate_limit=None,
    )
    # two counts + the precharge + the insert
    assert len(seen) == 4 and loop_thread not in seen, "a deep-door round trip ran on the loop"
