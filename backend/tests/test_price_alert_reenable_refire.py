"""A one-shot price alert fires ONCE PER ARMING, and a suppressed fire never consumes it.

Two defects with one root cause — the rule's fired state was written BEFORE anyone knew
what became of the notification (C12, 2026-09-25):

  1. **Re-enabled rules were silent.** The one-shot dedup key was `pa:{id}` alone. A user
     who turned a fired rule back on (`update(is_active=True)` re-arms it) had its next
     crossing claim the SAME key, hit the `UNIQUE (user_id, dedup_key)` row the first fire
     left, and get dropped as a duplicate — no push, no inbox row — while the rule was
     switched off again with a fresh trigger.
  2. **Suppressed fires consumed the rule.** A crossing suppressed by the daily
     `price_alert` cap (10/day vs 20 rules a user may hold) or by the user's own toggle
     writes no ledger row, but the rule was still deactivated and counted as triggered.

No network: Supabase is an in-memory fake that enforces the ledger's unique index, the
price source is the documented `svc.price` seam, and the dispatcher is a fake that follows
the real decision ladder's ledger contract — `preference_off` / `cap_reached` write NO row
(SYSTEM_DESIGN_GUIDELINES §11.6), everything else claims one first.
"""

from __future__ import annotations

import math
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import app.services.price_alert_service as mod
from app.services.price_alert_service import PriceAlertService

ALERT_ID = "11111111-1111-1111-1111-111111111111"
USER = "u1"


# ── fakes ────────────────────────────────────────────────────────────────────


class _DB:
    """`price_alerts` + `notification_events`, with the ledger's UNIQUE (user_id, dedup_key)."""

    def __init__(self, rule):
        self.rules = {rule["id"]: dict(rule)}
        self.ledger = []
        self.fail_ledger_read = False
        self.fail_state_write = False
        self.state_writes = 0

    def table(self, name):
        return _Q(self, name)

    def claim(self, user_id, key):
        if any(r["user_id"] == user_id and r["dedup_key"] == key for r in self.ledger):
            return False                                   # 23505 → "duplicate"
        self.ledger.append({"user_id": user_id, "dedup_key": key})
        return True

    @property
    def rule(self):
        return self.rules[ALERT_ID]


class _Q:
    def __init__(self, db, name):
        self.db, self.name = db, name
        self.op, self.patch, self.filters = "select", None, {}

    def select(self, *a, **k):
        self.op = "select"
        return self

    def update(self, patch):
        self.op, self.patch = "update", dict(patch)
        return self

    def eq(self, col, val):
        self.filters[col] = val
        return self

    def limit(self, n):
        return self

    def _match(self, row):
        return all(str(row.get(k)) == str(v) for k, v in self.filters.items())

    def execute(self):
        if self.name == "notification_events" and self.op == "select":
            if self.db.fail_ledger_read:
                raise RuntimeError("ledger unreachable")
            return SimpleNamespace(data=[r for r in self.db.ledger if self._match(r)])
        if self.name == "price_alerts" and self.op == "update":
            if self.db.fail_state_write:
                raise RuntimeError("state write lost")
            self.db.state_writes += 1
            out = []
            for row in self.db.rules.values():
                if self._match(row):
                    row.update(self.patch)
                    out.append(dict(row))
            return SimpleNamespace(data=out)
        raise AssertionError(f"unexpected {self.op} on {self.name}")


class _Dispatcher:
    """`notify_users` with the real ledger contract. `verdict` is what `decide` answers."""

    def __init__(self, db, verdict="ok"):
        self.db, self.verdict = db, verdict
        self.keys = []

    async def notify_users(self, users, *, dedup_key, **kw):
        sent = 0
        for uid in users:
            key = dedup_key(uid) if callable(dedup_key) else dedup_key
            self.keys.append(key)
            if self.verdict in ("cap_reached", "preference_off"):
                continue                                   # no claim, no row
            if not self.db.claim(uid, key):
                continue                                   # duplicate
            if self.verdict in ("no_device", "dry_run"):
                continue                                   # row written, nothing delivered
            sent += 1
        return sent


class _Quotes:
    def __init__(self, rows):
        self.rows = rows

    async def get_quotes_list(self, tickers):
        return list(self.rows)


def _rule(**over):
    base = {
        "id": ALERT_ID, "user_id": USER, "ticker": "AAPL", "asset_type": "stock",
        "kind": "price_above", "threshold": 250.0, "repeat_mode": "once",
        "armed": True, "last_price": 240.0, "trigger_count": 0, "is_active": True,
    }
    base.update(over)
    return base


def _svc(db):
    svc = object.__new__(PriceAlertService)
    svc.supabase = db
    return svc


async def _cycle(svc, db, dispatcher, price, change_pct=None):
    quote = {"symbol": "AAPL", "price": price}
    if change_pct is not None:
        quote["changePercentage"] = change_pct
    svc.price = _Quotes([quote])
    active = lambda self, tickers: [dict(r) for r in db.rules.values() if r.get("is_active")]
    with patch.object(PriceAlertService, "_active_universe", lambda self, *a, **k: ["AAPL"]), \
         patch.object(PriceAlertService, "_active_rules", active), \
         patch.object(mod, "get_push_dispatch_service", lambda: dispatcher):
        return await svc.evaluate_once()


# ── 1. re-enable → re-fire reaches the user ──────────────────────────────────


@pytest.mark.asyncio
async def test_a_re_enabled_one_shot_rule_fires_again_under_a_new_key():
    db = _DB(_rule())
    svc, dispatcher = _svc(db), _Dispatcher(db)

    first = await _cycle(svc, db, dispatcher, 255.0)
    assert first["sent"] == 1
    k1 = dispatcher.keys[-1]
    assert db.rule["is_active"] is False and db.rule["trigger_count"] == 1

    # The user turns it back on (Tracking → Alerts / the Price Alerts sheet).
    assert svc.update(USER, ALERT_ID, {"is_active": True}) is not None
    assert db.rule["armed"] is True and db.rule["last_price"] is None

    seed = await _cycle(svc, db, dispatcher, 245.0)          # cold start seeds, silent
    assert seed["fired"] == 0 and db.rule["last_price"] == 245.0

    second = await _cycle(svc, db, dispatcher, 256.0)        # a fresh crossing
    k2 = dispatcher.keys[-1]
    assert k2 != k1, "the re-armed rule reused the first arming's key"
    assert second["sent"] == 1, "the re-fire was dropped as a duplicate of the first fire"
    assert len(db.ledger) == 2
    assert db.rule["is_active"] is False and db.rule["trigger_count"] == 2


@pytest.mark.asyncio
async def test_two_instances_evaluating_one_row_share_one_key_and_buzz_once():
    """Cross-instance dedup is untouched: both read the same pre-fire count."""
    db = _DB(_rule())
    a, b = _svc(db), _svc(db)
    snapshot = [dict(db.rule)]                                # both read before either writes
    dispatcher = _Dispatcher(db)
    sent = 0
    for svc in (a, b):
        svc.price = _Quotes([{"symbol": "AAPL", "price": 255.0}])
        with patch.object(PriceAlertService, "_active_universe", lambda self, *x, **k: ["AAPL"]), \
             patch.object(PriceAlertService, "_active_rules", lambda self, t: [dict(r) for r in snapshot]), \
             patch.object(mod, "get_push_dispatch_service", lambda: dispatcher):
            sent += (await svc.evaluate_once())["sent"]
    assert dispatcher.keys[0] == dispatcher.keys[1]
    assert sent == 1 and len(db.ledger) == 1
    # The loser saw the winner's row, so it records the same consumed state — including
    # the dropped latch, which a "suppressed" reading would have raised again.
    assert db.rule["is_active"] is False and db.rule["trigger_count"] == 1
    assert db.rule["armed"] is False


@pytest.mark.asyncio
async def test_a_lost_state_write_dedups_against_its_own_claim():
    """The count only moves with a successful write, so a lost write re-fires under the
    SAME key — the claim absorbs it and the next cycle consumes the rule."""
    db = _DB(_rule())
    svc, dispatcher = _svc(db), _Dispatcher(db)
    db.fail_state_write = True
    assert (await _cycle(svc, db, dispatcher, 255.0))["sent"] == 1
    assert db.rule["is_active"] is True and db.rule["trigger_count"] == 0
    db.fail_state_write = False
    assert (await _cycle(svc, db, dispatcher, 256.0))["sent"] == 0
    assert dispatcher.keys[0] == dispatcher.keys[1] and len(db.ledger) == 1
    assert db.rule["is_active"] is False and db.rule["trigger_count"] == 1


# ── 2. a suppressed fire does not consume the rule ────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("verdict", ["cap_reached", "preference_off"])
async def test_a_suppressed_one_shot_rule_stays_active_and_armed(verdict):
    db = _DB(_rule())
    svc, dispatcher = _svc(db), _Dispatcher(db, verdict)
    stats = await _cycle(svc, db, dispatcher, 255.0)
    assert stats["fired"] == 1 and stats["sent"] == 0 and db.ledger == []
    rule = db.rule
    assert rule["is_active"] is True, "a suppressed once-rule was switched off"
    assert rule["armed"] is True
    assert rule["trigger_count"] == 0, "a suppressed fire was counted as a trigger"
    assert "last_triggered_at" not in rule
    assert rule["last_price"] == 255.0, "the baseline must advance, or it re-fires every minute"

    # The cap rolls / the toggle comes back: the NEXT crossing reaches the user, under the
    # same first-arming key (nothing was claimed for it).
    dispatcher.verdict = "ok"
    assert (await _cycle(svc, db, dispatcher, 255.0))["fired"] == 0   # no new crossing
    await _cycle(svc, db, dispatcher, 245.0)
    stats = await _cycle(svc, db, dispatcher, 257.0)
    assert stats["sent"] == 1
    assert db.ledger == [{"user_id": USER, "dedup_key": f"pa:{ALERT_ID}"}]
    assert db.rule["is_active"] is False and db.rule["trigger_count"] == 1


@pytest.mark.asyncio
async def test_a_capped_daily_rule_is_not_latched_or_counted():
    db = _DB(_rule(repeat_mode="daily"))
    svc = _svc(db)
    await _cycle(svc, db, _Dispatcher(db, "cap_reached"), 255.0)
    assert db.rule["armed"] is True and db.rule["trigger_count"] == 0
    assert db.rule["is_active"] is True


@pytest.mark.asyncio
async def test_a_capped_percent_once_rule_stays_active():
    db = _DB(_rule(kind="percent_move", threshold=5.0, last_price=None))
    svc = _svc(db)
    stats = await _cycle(svc, db, _Dispatcher(db, "cap_reached"), 255.0, change_pct=-6.2)
    assert stats["fired"] == 1 and stats["sent"] == 0
    assert db.rule["is_active"] is True and db.rule["trigger_count"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("verdict", ["no_device", "dry_run"])
async def test_a_claimed_but_undelivered_fire_still_consumes_the_rule(verdict):
    """Negative control for the discriminator: `notify_users` returns 0 here too, but the
    alert IS in the inbox — the ledger row says so — so the one-shot rule is spent."""
    db = _DB(_rule())
    stats = await _cycle(_svc(db), db, _Dispatcher(db, verdict), 255.0)
    assert stats["sent"] == 0 and len(db.ledger) == 1
    assert db.rule["is_active"] is False and db.rule["trigger_count"] == 1
    assert db.rule["armed"] is False


@pytest.mark.asyncio
async def test_an_unreadable_ledger_holds_the_whole_state_for_a_retry():
    """0 sent + no answer from the ledger = unknown. Write nothing; the next cycle sees the
    same crossing and re-dispatches under the same key, which the claim makes safe."""
    db = _DB(_rule())
    svc = _svc(db)
    db.fail_ledger_read = True
    before = dict(db.rule)
    stats = await _cycle(svc, db, _Dispatcher(db, "cap_reached"), 255.0)
    assert stats["fired"] == 1
    assert db.rule == before and db.state_writes == 0

    db.fail_ledger_read = False
    dispatcher = _Dispatcher(db, "ok")
    assert (await _cycle(svc, db, dispatcher, 255.0))["sent"] == 1
    assert dispatcher.keys == [f"pa:{ALERT_ID}"]
    assert db.rule["is_active"] is False


@pytest.mark.asyncio
async def test_a_non_firing_cycle_still_persists_its_state():
    """The reorder must not stop the ordinary baseline write."""
    db = _DB(_rule())
    stats = await _cycle(_svc(db), db, _Dispatcher(db), 246.0)
    assert stats["fired"] == 0 and db.state_writes == 1 and db.rule["last_price"] == 246.0


# ── 3. the key itself ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("count,expected", [
    (0, f"pa:{ALERT_ID}"),
    (None, f"pa:{ALERT_ID}"),
    (1, f"pa:{ALERT_ID}:n1"),
    (7, f"pa:{ALERT_ID}:n7"),
    ("3", f"pa:{ALERT_ID}:n3"),
    (-2, f"pa:{ALERT_ID}"),
    ("garbage", f"pa:{ALERT_ID}"),
    (math.nan, f"pa:{ALERT_ID}"),
    (math.inf, f"pa:{ALERT_ID}"),
])
def test_the_one_shot_key_is_per_arming_and_tolerates_garbage(count, expected):
    assert _svc(None).dedup_key(_rule(trigger_count=count), "once") == expected


def test_the_daily_key_is_unchanged_by_the_count():
    svc = _svc(None)
    with patch.object(mod, "trading_date_et", lambda: "2026-09-25"):
        assert svc.dedup_key(_rule(trigger_count=0), "daily") == f"pa:{ALERT_ID}:2026-09-25"
        assert svc.dedup_key(_rule(trigger_count=4), "daily") == f"pa:{ALERT_ID}:2026-09-25"


def test_the_state_write_and_the_key_read_the_same_count():
    """`_persist` bumps from the same reader the key uses, so a garbage count cannot make
    the key and the stored count disagree about which arming a fire was."""
    from app.services.price_alert_engine import AlertDecision

    db = _DB(_rule(trigger_count="garbage"))
    _svc(db)._persist(db.rule, AlertDecision(True, 255.0, False, True, "crossed"))
    assert db.rule["trigger_count"] == 1
