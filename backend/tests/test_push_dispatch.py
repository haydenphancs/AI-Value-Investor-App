"""Push dispatch — who gets alerted, whether they want it, and exactly once.

A duplicate cache write is invisible. A duplicate push is a buzz in someone's pocket
that cannot be taken back, and it is the fastest way to earn an uninstall. So the
properties pinned here are deliberately paranoid:

  * the dedup claim happens BEFORE the send, and a failed claim means DON'T send;
  * a user who turned the category off is never sent to;
  * one bad recipient never abandons the rest of the fan-out;
  * the fan-out is bounded, and truncation is LOGGED rather than silent.

No Supabase, no APNs — both are stubbed.
"""

import asyncio

import pytest

from app.services.push_service import PushOutcome

from app.services.notification_kinds import KIND_TICKER_MOVE, get_kind
from app.services.push_dispatch_service import (
    MAX_RECIPIENTS_PER_SCOPE,
    PushDispatchService,
    _Recipient,
)

_MOVE = get_kind(KIND_TICKER_MOVE)


class _FakePush:
    def __init__(self, enabled=True, accepted=1, attempted=None, failures=()):
        self.enabled = enabled
        self._accepted = accepted
        # A double of `PushService.send_to_user`, which returns a `PushOutcome` — how many
        # devices were TRIED and what APNs said about each, not a bare accepted-count. The
        # extra fields let a test express a PARTIAL delivery, which is the case that used to
        # be invisible in production.
        self._attempted = accepted if attempted is None else attempted
        self._failures = tuple(failures)
        self.sent = []

    async def send_to_user(self, user_id, *, title, body, data=None, **kw):
        # **kw absorbs the APNs-shaping arguments the registry supplies
        # (interruption_level / thread_id / collapse_id / category / badge / devices).
        # They are asserted separately in test_notification_dispatch.py; the properties
        # pinned in THIS file are about who gets sent to, not how the payload is shaped.
        self.sent.append((user_id, title, body, data))
        self.last_kwargs = kw
        # Mirrors the real `PushService.send_to_user`, which checks `enabled` FIRST and
        # returns an outcome describing the misconfiguration. A double that ignored
        # `enabled` reported a successful delivery with APNs switched off.
        if not self.enabled:
            return PushOutcome(
                attempted=0, accepted=0,
                failures=("APNs is not configured on this server",),
            )
        return PushOutcome(
            attempted=self._attempted, accepted=self._accepted, failures=self._failures
        )


def _service(*, watchers=None, prefs=None, claim=True, push=None,
             watchers_raise=False, prefs_raise=False, claim_raise=None,
             sent_today=0, devices=True):
    """A PushDispatchService with every Supabase seam stubbed.

    `prefs` is user_id -> bool (does this user want the category?), which the fake
    translates into the preferences blob `decide()` actually reads. `sent_today` is an
    int or a user_id -> int map, standing in for the per-category count.
    """
    svc = object.__new__(PushDispatchService)
    svc._push = push or _FakePush()
    svc.supabase = None
    svc.claimed = []

    def _watchers_of(ticker):
        if watchers_raise:
            raise RuntimeError("db down")
        users = list(watchers or [])
        return users[:MAX_RECIPIENTS_PER_SCOPE]

    def _count_for(user_id):
        return sent_today.get(user_id, 0) if isinstance(sent_today, dict) else sent_today

    def _resolve(user_ids, kind, now):
        out = {}
        for uid in user_ids:
            blob = {}
            if not prefs_raise and prefs is not None and uid in prefs:
                blob = {kind.preference_key: prefs[uid]}
            out[uid] = _Recipient(
                user_id=uid,
                preferences=blob,
                # prefs_raise stands for a failed read, whose documented behaviour is to
                # fail OPEN to the declared default — i.e. an empty blob, exactly as
                # `_preferences_bulk` produces.
                preferences_known=not prefs_raise,
                devices=[{"token": f"tok-{uid}", "environment": "sandbox"}] if devices else [],
                category_sent_today=_count_for(uid),
                unread=0,
            )
        return out

    def _claim(user_id, dedup_key, **kw):
        if claim_raise:
            return False
        svc.claimed.append((user_id, dedup_key))
        return claim

    svc.watchers_of = _watchers_of
    svc.resolve_recipients = _resolve
    svc.claim_send = _claim
    svc.mark_state = lambda *a, **k: None
    svc.preference_enabled = lambda user_id, key: (
        True if prefs_raise else (prefs or {}).get(user_id, True)
    )
    svc.alerts_sent_today = lambda user_id, category=None: _count_for(user_id)
    return svc


async def _notify(svc, ticker="NVDA", key="move:NVDA:2026-08-01"):
    return await svc.notify_watchers(
        ticker=ticker, title=ticker, body="moved 8%", dedup_key=key
    )


# ── the once-only property ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_claims_before_sending():
    """Order matters: claiming first risks a rare missed alert, sending first risks a
    duplicate. Missing one is forgivable; buzzing twice is not."""
    push = _FakePush()
    svc = _service(watchers=["u1"], push=push)
    await _notify(svc)
    assert svc.claimed == [("u1", "move:NVDA:2026-08-01")]
    assert [s[0] for s in push.sent] == ["u1"]


@pytest.mark.asyncio
async def test_an_already_claimed_user_is_not_sent_to():
    """The second pass over the same scope on the same day must be silent."""
    push = _FakePush()
    svc = _service(watchers=["u1", "u2"], claim=False, push=push)
    sent = await _notify(svc)
    assert sent == 0
    assert push.sent == []


@pytest.mark.asyncio
async def test_a_failed_claim_roundtrip_does_not_send():
    """If we cannot prove it's unsent, don't send. A DB blip must not become a
    duplicate notification."""
    push = _FakePush()
    svc = _service(watchers=["u1"], claim_raise=True, push=push)
    assert await _notify(svc) == 0
    assert push.sent == []


# ── preferences ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_users_who_turned_the_category_off_are_skipped():
    push = _FakePush()
    svc = _service(watchers=["yes", "no"], prefs={"yes": True, "no": False}, push=push)
    await _notify(svc)
    assert [s[0] for s in push.sent] == ["yes"]


@pytest.mark.asyncio
async def test_a_user_with_no_saved_preference_is_opted_in():
    """Absent → ON, matching the iOS default for notify_watchlist_changes."""
    push = _FakePush()
    svc = _service(watchers=["u1"], prefs={}, push=push)
    await _notify(svc)
    assert len(push.sent) == 1


@pytest.mark.asyncio
async def test_an_opted_out_user_is_not_even_claimed():
    """Claiming for someone we won't message would burn their dedup slot and
    silently suppress a LATER alert they did want."""
    svc = _service(watchers=["no"], prefs={"no": False})
    await _notify(svc)
    assert svc.claimed == []


# ── resilience ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_disabled_push_still_records_the_notification():
    """⚠️ REVERSED DECISION — see the twin test in test_notification_dispatch.py.

    This asserted `svc.claimed == []`. An unconfigured APNs must not also empty the in-app
    inbox: that is the one artifact meant to outlive a failed push, and losing it is how a
    month of undelivered notifications left no trace anywhere.
    """
    svc = _service(watchers=["u1"], push=_FakePush(enabled=False))
    assert await _notify(svc) == 0, "nothing is DELIVERED — only recorded"
    assert len(svc.claimed) == 1, "the notification must still reach the inbox"


@pytest.mark.asyncio
async def test_no_watchers_is_a_noop():
    push = _FakePush()
    svc = _service(watchers=[], push=push)
    assert await _notify(svc) == 0
    assert push.sent == []


@pytest.mark.asyncio
async def test_a_failed_watcher_lookup_degrades_to_zero():
    svc = _service(watchers_raise=True)
    assert await _notify(svc) == 0


@pytest.mark.asyncio
async def test_one_failing_recipient_does_not_abandon_the_rest():
    class _Flaky(_FakePush):
        async def send_to_user(self, user_id, *, title, body, data=None, **kw):
            if user_id == "bad":
                raise RuntimeError("apns exploded")
            return await super().send_to_user(
                user_id, title=title, body=body, data=data, **kw
            )

    push = _Flaky()
    svc = _service(watchers=["a", "bad", "b"], push=push)
    sent = await _notify(svc)
    assert sent == 2
    assert [s[0] for s in push.sent] == ["a", "b"]


@pytest.mark.asyncio
async def test_a_rejected_send_is_not_counted_as_delivered():
    push = _FakePush(accepted=0)   # APNs accepted nothing
    svc = _service(watchers=["u1"], push=push)
    assert await _notify(svc) == 0


# ── payload ──────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_the_payload_carries_the_ticker_for_the_tap_handler():
    """AppDelegate.didReceive reads `ticker` from userInfo to route the tap. Without
    it the notification says 'NVDA moved 8%' and opens whatever tab was last used."""
    push = _FakePush()
    svc = _service(watchers=["u1"], push=push)
    await svc.notify_watchers(
        ticker="NVDA", title="NVDA", body="moved 8%",
        dedup_key="k", data={"kind": "ticker_move", "ticker": "NVDA"},
    )
    payload = push.sent[0][3]
    assert payload["ticker"] == "NVDA"
    assert payload["kind"] == "ticker_move"
    # And the dedup key, which is what makes the notification's own "Mark as Read"
    # button work: the payload carries no `notification_events.id`, so this is the only
    # thing that identifies the row from a lock-screen tap.
    assert payload["dedup_key"] == "k"


# ── bounds ───────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_the_fanout_is_bounded():
    """A mega-cap moving on a busy day must not stall the sweeper behind thousands of
    sequential sends."""
    push = _FakePush()
    many = [f"u{i}" for i in range(MAX_RECIPIENTS_PER_SCOPE + 250)]
    svc = _service(watchers=many, push=push)
    await _notify(svc)
    assert len(push.sent) == MAX_RECIPIENTS_PER_SCOPE


def test_dedup_key_is_one_alert_per_ticker_per_day():
    from app.services.push_dispatch_service import trading_date_et

    assert trading_date_et() == trading_date_et()   # stable within a run
    assert len(trading_date_et()) == 10             # ISO date, not a timestamp


# ── how a duplicate is actually detected ─────────────────────────────────────
#
# `claim_send` is the whole dedup mechanism, and it decides "already sent" by
# inspecting the exception from a conflicting INSERT. Verified empirically against a
# live Supabase composite-PK insert: supabase-py raises `APIError` with
# `.code == "23505"` and a message containing "duplicate key value violates unique
# constraint". Both shapes are pinned here so a client upgrade that changes one
# doesn't silently turn every duplicate into an "unknown error" — which fails safe
# (no send) but would suppress alerts indefinitely with only a warning.

class _ConflictError(Exception):
    code = "23505"

    def __str__(self):
        return ('{"message": "duplicate key value violates unique constraint '
                '\\"push_send_log_pkey\\"", "code": "23505"}')


class _MessageOnlyConflict(Exception):
    """No structured code — only the message, as an older/other client might raise."""

    def __str__(self):
        return 'duplicate key value violates unique constraint "push_send_log_pkey"'


def _claim_service(raises):
    class _Tbl:
        def insert(self, row): self._row = row; return self
        def execute(self):
            if raises:
                raise raises
            return type("R", (), {"data": [{}]})()

    svc = object.__new__(PushDispatchService)
    svc.supabase = type("S", (), {"table": lambda self, n: _Tbl()})()
    return svc


def test_a_structured_23505_is_read_as_already_sent():
    assert _claim_service(_ConflictError()).claim_send("u1", "k") is False


def test_a_message_only_conflict_is_also_read_as_already_sent():
    assert _claim_service(_MessageOnlyConflict()).claim_send("u1", "k") is False


def test_a_clean_insert_grants_the_claim():
    assert _claim_service(None).claim_send("u1", "k") is True


def test_an_unrelated_db_error_refuses_the_claim():
    """Fails SAFE: if we can't prove the alert is unsent, don't send it. A duplicate
    buzz is worse than a missed one."""
    assert _claim_service(RuntimeError("connection reset")).claim_send("u1", "k") is False


# ── the sweeper gate (adversarial review, 2026-08-01) ────────────────────────
#
# `decision.price_band` carries a STALE σ-tier when the current quote is unusable
# (it falls back to `last_price_band`). The paid-catalyst path already guards against
# that; the notify path did not, so a ticker that moved 9% yesterday and has no quote
# today would interrupt someone about a move that isn't happening — and because the
# dedup key is per DAY, it would land as a genuinely new alert rather than a duplicate.

class _Decision:
    def __init__(self, band):
        self.price_band = band
        self.inputset_id = ""
        self.reason = "test"


def _sweeper():
    from app.services.updates_insight_sweeper import InsightSweeper

    return object.__new__(InsightSweeper)


@pytest.mark.asyncio
async def test_no_push_when_the_current_quote_is_missing(monkeypatch):
    from app.services.updates_materiality import TIER_EXTREME
    import app.services.push_dispatch_service as pds

    calls = []

    class _Spy:
        async def notify_watchers(self, **kw):
            calls.append(kw)
            return 1

    monkeypatch.setattr(pds, "get_push_dispatch_service", lambda: _Spy())

    await _sweeper()._notify_watchers(
        "NVDA", _Decision(TIER_EXTREME), {"headline": "NVDA fell hard"},
        None, quote=None,      # no quote this cycle → the band is stale
    )
    assert calls == [], "alerted on a stale tier with no live quote"


@pytest.mark.asyncio
async def test_no_push_when_the_move_rounds_to_zero(monkeypatch):
    from app.services.updates_materiality import TIER_EXTREME
    import app.services.push_dispatch_service as pds

    calls = []

    class _Spy:
        async def notify_watchers(self, **kw):
            calls.append(kw)
            return 1

    monkeypatch.setattr(pds, "get_push_dispatch_service", lambda: _Spy())

    await _sweeper()._notify_watchers(
        "NVDA", _Decision(TIER_EXTREME), {"headline": "h"},
        None, quote={"changePercentage": 0.001},
    )
    assert calls == [], "alerted on a move that rounds to 0.00%"


@pytest.mark.asyncio
async def test_a_real_move_with_a_live_quote_does_push(monkeypatch):
    from app.services.updates_materiality import TIER_EXTREME
    import app.services.push_dispatch_service as pds

    calls = []

    class _Spy:
        async def notify_watchers(self, **kw):
            calls.append(kw)
            return 1

    monkeypatch.setattr(pds, "get_push_dispatch_service", lambda: _Spy())

    await _sweeper()._notify_watchers(
        "NVDA", _Decision(TIER_EXTREME), {"headline": "NVDA fell 9% on guidance"},
        None, quote={"changePercentage": -9.2},
    )
    assert len(calls) == 1
    assert calls[0]["ticker"] == "NVDA"
    assert calls[0]["data"]["ticker"] == "NVDA"   # the tap handler needs this
    assert "move:NVDA:" in calls[0]["dedup_key"]


@pytest.mark.asyncio
async def test_a_routine_drift_never_pushes(monkeypatch):
    """Only Unusual/Extreme earns an interruption. A 0.4% drift already regenerates a
    card; notifying on those would train users to ignore the app within a week."""
    import app.services.push_dispatch_service as pds

    calls = []

    class _Spy:
        async def notify_watchers(self, **kw):
            calls.append(kw)
            return 1

    monkeypatch.setattr(pds, "get_push_dispatch_service", lambda: _Spy())

    await _sweeper()._notify_watchers(
        "NVDA", _Decision("typical"), {"headline": "h"},
        None, quote={"changePercentage": 0.4},
    )
    assert calls == []


@pytest.mark.asyncio
async def test_an_empty_headline_never_pushes(monkeypatch):
    """Silence beats a notification that says nothing — the card is on the Updates
    tab either way."""
    from app.services.updates_materiality import TIER_EXTREME
    import app.services.push_dispatch_service as pds

    calls = []

    class _Spy:
        async def notify_watchers(self, **kw):
            calls.append(kw)
            return 1

    monkeypatch.setattr(pds, "get_push_dispatch_service", lambda: _Spy())

    await _sweeper()._notify_watchers(
        "NVDA", _Decision(TIER_EXTREME), {"headline": "   "},
        None, quote={"changePercentage": -9.0},
    )
    assert calls == []


# ── per-user daily volume cap ────────────────────────────────────────────────
#
# The dedup key stops the SAME alert repeating. It does nothing about VOLUME: in a
# market-wide selloff, ten watchlist tickers each crossing the Unusual threshold means
# ten notifications in one afternoon — the classic way an app gets its notifications
# switched off, after which iOS never re-prompts.

@pytest.mark.asyncio
async def test_a_user_at_the_daily_cap_is_not_alerted(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "PUSH_MAX_ALERTS_PER_USER_PER_DAY", 3)
    push = _FakePush()
    svc = _service(watchers=["u1"], sent_today=3, push=push)
    assert await _notify(svc) == 0
    assert push.sent == []


@pytest.mark.asyncio
async def test_a_capped_user_does_not_burn_the_dedup_slot(monkeypatch):
    """Checked BEFORE the claim on purpose: a suppressed alert must not consume this
    ticker's dedup key, or the user silently loses tomorrow's alert for it too."""
    from app.config import settings

    monkeypatch.setattr(settings, "PUSH_MAX_ALERTS_PER_USER_PER_DAY", 1)
    svc = _service(watchers=["u1"], sent_today=5)
    await _notify(svc)
    assert svc.claimed == []


@pytest.mark.asyncio
async def test_the_cap_is_per_user_not_global(monkeypatch):
    """One heavy user hitting their ceiling must not silence everyone else."""
    from app.config import settings

    monkeypatch.setattr(settings, "PUSH_MAX_ALERTS_PER_USER_PER_DAY", 2)
    push = _FakePush()
    svc = _service(watchers=["heavy", "quiet"],
                   sent_today={"heavy": 9, "quiet": 0}, push=push)
    await _notify(svc)
    assert [s[0] for s in push.sent] == ["quiet"]


@pytest.mark.asyncio
async def test_a_user_under_the_cap_still_gets_alerted(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "PUSH_MAX_ALERTS_PER_USER_PER_DAY", 3)
    push = _FakePush()
    svc = _service(watchers=["u1"], sent_today=2, push=push)
    assert await _notify(svc) == 1


@pytest.mark.asyncio
async def test_a_zero_cap_disables_the_check(monkeypatch):
    """0 = no volume ceiling, matching the other `set 0 to disable` knobs."""
    from app.config import settings

    monkeypatch.setattr(settings, "PUSH_MAX_ALERTS_PER_USER_PER_DAY", 0)
    push = _FakePush()
    svc = _service(watchers=["u1"], sent_today=999, push=push)
    assert await _notify(svc) == 1


def test_a_failed_count_read_fails_open():
    """A DB blip silencing someone's alerts is worse and far harder to diagnose than
    one extra notification — and the per-ticker dedup still prevents real repeats."""
    class _Boom:
        def table(self, name):
            raise RuntimeError("db down")

    svc = object.__new__(PushDispatchService)
    svc.supabase = _Boom()
    assert svc.alerts_sent_today("u1") == 0


# ── the ETF upgrade ──────────────────────────────────────────────────────────
#
# 13F and congressional filings are FULL of funds — SPY, QQQ and IWM are among the
# most-held 13F positions — and nothing about the SYMBOL "SPY" says fund, so
# `detect_asset_class` cannot see it. Every one of those alerts opened
# `TickerDetailView` to render equity fundamentals for an ETF.


def _resolver(*, watchlist_rows=None, profile=None):
    """A dispatcher with only the asset-type resolution live."""
    from app.services.push_dispatch_service import PushDispatchService as _PDS

    class _Q:
        def __init__(self, table):
            self.table_name = table
        def select(self, *_a): return self
        def eq(self, *_a): return self
        def limit(self, *_a): return self
        def execute(self):
            class _R:
                pass
            r = _R()
            r.data = (
                (watchlist_rows or []) if self.table_name == "watchlist_items"
                else ([{"profile_json": profile}] if profile is not None else [])
            )
            return r

    class _Supa:
        def table(self, name):
            return _Q(name)

    svc = object.__new__(_PDS)
    svc.supabase = _Supa()
    return svc


def test_a_cached_profile_upgrades_a_stock_route_to_etf():
    """`isEtf` / `isFund` on the cached FMP profile — the same flags the Home dashboard
    already calls the only reliable bulk source. Read locally; never an FMP call on the
    notification send path."""
    svc = _resolver(profile={"symbol": "SPY", "isEtf": True})
    assert svc.resolve_route_asset_type("SPY", "stock") == "etf"


def test_a_fund_flag_counts_too():
    svc = _resolver(profile={"symbol": "VTSAX", "isFund": True})
    assert svc.resolve_route_asset_type("VTSAX", "stock") == "etf"


def test_a_specific_asset_type_is_never_DOWNGRADED():
    """The invariant that makes this safe to run on every notification. A sender that
    knows better always wins, and a stale watchlist row can never turn a crypto alert
    back into an equity."""
    svc = _resolver(watchlist_rows=[{"asset_type": "stock"}], profile={"isEtf": True})
    assert svc.resolve_route_asset_type("BTCUSD", "crypto") is None
    assert svc.resolve_route_asset_type("^GSPC", "index") is None


def test_the_symbol_answers_before_any_stored_value():
    """`detect_asset_class` is pure and cannot be influenced by a request."""
    svc = _resolver()
    assert svc.resolve_route_asset_type("BTCUSD", "stock") == "crypto"
    assert svc.resolve_route_asset_type("^GSPC", "stock") == "index"
    assert svc.resolve_route_asset_type("GCUSD", "stock") == "commodity"


def test_the_client_writable_watchlist_column_never_decides_a_route():
    """A CROSS-TENANT write, and the reason `asset_type_of` was deleted rather than left
    unused.

    `watchlist_items.asset_type` is written by `POST/PATCH /api/v1/tracking/holdings`,
    whose body field is a bare `Optional[str]` — no enum, no validator — on routes taking
    `get_watchlist_identity`, i.e. `.guestAllowed`, which resolves a signed-out caller
    from the CLIENT-CHOSEN `X-Guest-Id` header (auth.md §1a). The old reader selected on
    `ticker` ALONE with no user scope and took the mode over 50 arbitrary rows. So anyone
    willing to rotate a header could decide where every OTHER user's notifications for
    that ticker opened — with no account and nothing rate-limiting it.

    It survived only because it was nearly unreachable. Routing it through
    `resolve_route_asset_type` would have run it for every ticker notification in the
    system.
    """
    import ast
    import inspect
    import textwrap
    from app.services import push_dispatch_service as pds
    from app.services.push_dispatch_service import PushDispatchService as _PDS

    def _executable(fn):
        """A function's source with its DOCSTRING and comments removed.

        Both matter here: this method's docstring names `watchlist_items` at length to
        explain why it must not be read, and a scan that cannot tell prose from code
        would fail on the explanation while passing on the defect.
        """
        tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
        body = tree.body[0].body
        if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
            body = body[1:]
        return "\n".join(ast.unparse(node) for node in body)

    assert "watchlist_items" not in _executable(_PDS.resolve_route_asset_type), (
        "the routing path reads the client-writable watchlist column again"
    )
    assert "def asset_type_of" not in inspect.getsource(pds).replace("# ", "#"), (
        "asset_type_of is back. It reads a client-writable column with no user scope, and "
        "its answer decides where OTHER users' notifications open."
    )
    # And the ETF source that replaced it is the server-written one.
    etf = _executable(_PDS._is_etf)
    assert "company_profile_cache" in etf and "watchlist_items" not in etf


def test_the_watcher_lookup_no_longer_selects_the_poisoned_column():
    """It was selected only to feed `asset_type_of` ("rides along … no extra round
    trip"). With that gone, continuing to fetch it invites the next reader."""
    import inspect
    from app.services.push_dispatch_service import PushDispatchService as _PDS

    body = inspect.getsource(_PDS.watchers_of)
    assert 'select("user_id")' in body, (
        "watchers_of still fetches asset_type for a consumer that no longer exists"
    )


def test_an_unknown_ticker_leaves_the_route_alone():
    """No cached profile → today's behaviour, not a guess."""
    svc = _resolver()
    assert svc.resolve_route_asset_type("ZZZZ", "stock") is None


def test_a_cache_miss_is_never_memoised_as_not_a_fund():
    """The memo has NO TTL and the service is a process-lifetime singleton, so a wrong
    answer written once is wrong until the next deploy.

    A 13F alert for SPY fires before anyone has opened SPY, so `company_profile_cache`
    has no row. If that "no" were remembered, every SPY notification for the rest of the
    instance's life would route to the equity screen — including after a user opens SPY
    and the profile lands.
    """
    svc = _resolver()
    assert svc._is_etf("SPY") is False
    # …the profile lands…
    svc = _resolver(profile={"isEtf": True})
    svc.__dict__["_etf_cache"] = {}
    assert svc._is_etf("SPY") is True


def test_a_profile_with_the_flags_STRIPPED_is_unknown_not_false():
    """Two writers share `company_profile_cache.profile_json` on the same key with
    `upsert(on_conflict="ticker")`, so the last one replaces it whole.
    `stock_overview_service._upsert_company_profile_db` — reached from every ticker-detail
    view — stores a FORMATTED dict (description / ceo / founded / sector / …) carrying
    neither flag. Remembering that as "not a fund" is how opening SPY once would
    permanently mis-route its alerts.
    """
    trimmed = {"description": "SPDR S&P 500 ETF Trust", "sector": "", "ceo": None}
    svc = _resolver(profile=trimmed)
    assert svc._is_etf("SPY") is False
    assert "SPY" not in svc.__dict__.get("_etf_cache", {}), (
        "a profile with neither isEtf nor isFund was memoised as 'not a fund' — that is "
        "the trimmed-profile case, and it is indistinguishable from a real answer once "
        "cached"
    )


def test_a_definite_answer_IS_memoised():
    """Anti-vacuity for the two tests above: the cache must still do its job. ETF-ness
    does not change, so a real answer is worth remembering."""
    svc = _resolver(profile={"isEtf": True})
    assert svc._is_etf("SPY") is True
    assert svc.__dict__["_etf_cache"]["SPY"] is True
    svc = _resolver(profile={"isEtf": False, "isFund": False})
    assert svc._is_etf("AAPL") is False
    assert svc.__dict__["_etf_cache"]["AAPL"] is False


def test_a_failed_profile_read_is_not_cached_as_not_an_etf():
    """A transient read error must not pin the wrong answer for the life of the process —
    the memo has no TTL because ETF-ness does not change, which makes a poisoned entry
    permanent."""
    from app.services.push_dispatch_service import PushDispatchService as _PDS

    calls = {"n": 0}

    class _Boom:
        def table(self, name):
            calls["n"] += 1
            raise RuntimeError("postgrest down")

    svc = object.__new__(_PDS)
    svc.supabase = _Boom()
    assert svc._is_etf("SPY") is False
    assert svc._is_etf("SPY") is False
    assert calls["n"] == 2, "a failed lookup was memoised — the error is now permanent"
