"""Analytics ingest bounds + failure behaviour.

`POST /api/v1/events` is the ONLY route that accepts arbitrary client-supplied
structure, so its caps are a security boundary, not hygiene. Two properties are
pinned here:

  * **Bounded** — batch size, prop count, key/value length, scalar-only values, and
    an event-name allowlist. Without these an events table is a storage bomb and a
    place for user-typed text (chat messages, search queries) to land in a store the
    privacy policy never described.
  * **Never breaks the app** — a failed insert still returns 200. A non-200 would make
    a retrying client amplify a Supabase outage into a write storm.

No Supabase: the service is stubbed and the schema layer is exercised directly.
"""

import pytest
from pydantic import ValidationError

from app.schemas.analytics import (
    ALLOWED_EVENTS,
    MAX_EVENTS_PER_BATCH,
    MAX_PROPS_PER_EVENT,
    MAX_PROP_VALUE_CHARS,
    AnalyticsBatchRequest,
    AnalyticsEvent,
)


# ── allowlist ────────────────────────────────────────────────────────────────

def test_known_event_passes_the_allowlist():
    assert AnalyticsEvent(event="app_open").is_known


def test_unknown_event_is_flagged_not_raised():
    """The critical one. Raising would fail the WHOLE batch via Pydantic, so an app
    update shipping a new event name before the backend learns it would discard every
    good event flushed alongside it."""
    e = AnalyticsEvent(event="totally_made_up")
    assert e.is_known is False


def test_one_unknown_event_does_not_reject_the_batch():
    batch = AnalyticsBatchRequest(events=[
        {"event": "app_open"},
        {"event": "not_a_real_event"},
        {"event": "chat_sent"},
    ])
    assert len(batch.events) == 3
    assert [e.is_known for e in batch.events] == [True, False, True]


def test_event_name_is_trimmed():
    assert AnalyticsEvent(event="  app_open  ").is_known


# ── prop bounds — the leak-prevention surface ────────────────────────────────

def test_nested_props_are_dropped():
    """A dict/list value is how an entire request body — or a user's typed message —
    ends up inside a 'dimension'."""
    e = AnalyticsEvent(event="screen_view", props={
        "tab": "home",
        "ticker": {"secret": "should not persist"},
        "persona": [1, 2, 3],
    })
    assert e.props == {"tab": "home"}


def test_none_props_are_dropped():
    e = AnalyticsEvent(event="screen_view", props={"tab": "home", "ticker": None})
    assert e.props == {"tab": "home"}


def test_long_string_values_are_truncated_not_stored_whole():
    e = AnalyticsEvent(event="screen_view", props={"tab": "z" * 5000})
    assert len(e.props["tab"]) == MAX_PROP_VALUE_CHARS


def test_prop_count_is_capped():
    from app.schemas.analytics import ALLOWED_PROP_KEYS

    e = AnalyticsEvent(
        event="screen_view",
        props={k: 1 for k in ALLOWED_PROP_KEYS} | {f"junk{i}": i for i in range(50)},
    )
    assert len(e.props) <= MAX_PROPS_PER_EVENT


def test_overlong_prop_keys_are_dropped():
    e = AnalyticsEvent(event="screen_view", props={"k" * 500: "v", "tab": "home"})
    assert e.props == {"tab": "home"}


def test_scalar_types_survive():
    e = AnalyticsEvent(event="report_completed", props={
        "ticker": "AAPL", "seconds": 12.5, "count": 3, "cached": True,
    })
    assert e.props == {"ticker": "AAPL", "seconds": 12.5, "count": 3, "cached": True}


def test_empty_props_is_fine():
    assert AnalyticsEvent(event="app_open").props == {}


# ── batch bounds ─────────────────────────────────────────────────────────────

def test_batch_size_is_capped():
    batch = AnalyticsBatchRequest(
        events=[{"event": "app_open"} for _ in range(MAX_EVENTS_PER_BATCH + 250)]
    )
    assert len(batch.events) == MAX_EVENTS_PER_BATCH


def test_empty_batch_is_valid():
    assert AnalyticsBatchRequest(events=[]).events == []


def test_overlong_session_id_is_truncated_not_rejected():
    """A `max_length` here RAISES → 422 → the entire batch is discarded. Truncating
    keeps the events."""
    from app.schemas.analytics import MAX_SESSION_ID_CHARS

    batch = AnalyticsBatchRequest(events=[{"event": "app_open"}], session_id="s" * 5000)
    assert len(batch.session_id) == MAX_SESSION_ID_CHARS
    assert len(batch.events) == 1


def test_overlong_event_name_is_truncated_not_rejected():
    """Same reasoning: it must degrade like an unknown name (drop the one event),
    never like a validation error (drop all 50)."""
    e = AnalyticsEvent(event="x" * 5000)
    assert e.is_known is False
    assert len(e.event) <= 60


def test_one_overlong_event_name_does_not_reject_the_batch():
    batch = AnalyticsBatchRequest(events=[
        {"event": "app_open"}, {"event": "y" * 9000}, {"event": "chat_sent"},
    ])
    assert len(batch.events) == 3
    assert sum(e.is_known for e in batch.events) == 2


# ── service behaviour ────────────────────────────────────────────────────────

class _FakeTable:
    def __init__(self, raises=False):
        self.raises = raises
        self.inserted = None

    def insert(self, rows):
        self.inserted = rows
        return self

    def execute(self):
        if self.raises:
            raise RuntimeError("supabase down")
        return type("R", (), {"data": self.inserted})()


class _FakeSupabase:
    def __init__(self, raises=False):
        self.table_obj = _FakeTable(raises)

    def table(self, name):
        return self.table_obj


def _service(raises=False):
    from app.services.analytics_service import AnalyticsService

    svc = object.__new__(AnalyticsService)
    svc.supabase = _FakeSupabase(raises)
    return svc


def test_record_batch_filters_unknown_events_and_reports_counts():
    svc = _service()
    accepted, dropped = svc.record_batch("id-1", [
        AnalyticsEvent(event="app_open"),
        AnalyticsEvent(event="nope"),
        AnalyticsEvent(event="chat_sent"),
    ])
    assert (accepted, dropped) == (2, 1)
    assert [r["event"] for r in svc.supabase.table_obj.inserted] == ["app_open", "chat_sent"]


def test_record_batch_never_raises_on_insert_failure():
    """The whole point: instrumentation must not become an outage."""
    svc = _service(raises=True)
    accepted, dropped = svc.record_batch("id-1", [AnalyticsEvent(event="app_open")])
    assert accepted == 0
    assert dropped == 1


def test_record_batch_with_only_unknown_events_does_not_touch_the_db():
    svc = _service()
    accepted, dropped = svc.record_batch("id-1", [AnalyticsEvent(event="bogus")])
    assert (accepted, dropped) == (0, 1)
    assert svc.supabase.table_obj.inserted is None


def test_identity_key_is_stamped_on_every_row():
    svc = _service()
    svc.record_batch("install-uuid", [
        AnalyticsEvent(event="app_open"), AnalyticsEvent(event="chat_sent"),
    ])
    assert {r["identity_key"] for r in svc.supabase.table_obj.inserted} == {"install-uuid"}


def test_sweep_never_raises():
    svc = _service(raises=True)

    class _Del:
        def delete(self): return self
        def lt(self, *a): return self
        def execute(self): raise RuntimeError("boom")

    svc.supabase.table = lambda name: _Del()
    assert svc.sweep_expired() == 0


# ── endpoint contract ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_endpoint_returns_200_even_when_the_insert_fails(monkeypatch):
    import app.api.v1.endpoints.analytics as ep

    monkeypatch.setattr(ep, "get_analytics_service", lambda: _service(raises=True))
    resp = await ep.ingest_events(
        AnalyticsBatchRequest(events=[{"event": "app_open"}]),
        user={"id": "u1"}, x_guest_id=None, x_app_version="1.0", x_platform="iOS",
    )
    assert resp.accepted == 0   # nothing stored...
    assert resp.dropped == 1    # ...and the client is told, but still gets a 200 body


@pytest.mark.asyncio
async def test_endpoint_short_circuits_an_empty_batch(monkeypatch):
    import app.api.v1.endpoints.analytics as ep

    called = {"n": 0}

    def _boom():
        called["n"] += 1
        raise AssertionError("should not touch the service for an empty batch")

    monkeypatch.setattr(ep, "get_analytics_service", _boom)
    resp = await ep.ingest_events(
        AnalyticsBatchRequest(events=[]),
        user={"id": "u1"}, x_guest_id=None, x_app_version=None, x_platform=None,
    )
    assert resp.accepted == 0 and called["n"] == 0


@pytest.mark.asyncio
async def test_endpoint_buckets_guests_per_install(monkeypatch):
    """Two guest installs must not collapse into one identity, or every funnel and
    retention number computed from this table is wrong."""
    import app.api.v1.endpoints.analytics as ep
    from app.dependencies import GUEST_USER_ID

    seen = []
    svc = _service()
    monkeypatch.setattr(ep, "get_analytics_service", lambda: svc)

    for install in ("install-A", "install-B"):
        await ep.ingest_events(
            AnalyticsBatchRequest(events=[{"event": "app_open"}]),
            user={"id": GUEST_USER_ID}, x_guest_id=install,
            x_app_version=None, x_platform=None,
        )
        seen.append(svc.supabase.table_obj.inserted[0]["identity_key"])

    assert seen[0] != seen[1]
    assert "install-A" not in seen[0]   # hashed, never the raw client string


def test_endpoint_is_rate_limited():
    """This route accepts arbitrary client structure — it must not be unbounded."""
    import inspect

    from app.api.v1.endpoints import analytics as ep
    from app.dependencies import IdentityRateLimitChecker, RateLimitChecker

    has_limit = any(
        isinstance(getattr(p.default, "dependency", None), (RateLimitChecker, IdentityRateLimitChecker))
        for p in inspect.signature(ep.ingest_events).parameters.values()
    )
    assert has_limit


def test_allowlist_covers_the_launch_questions():
    """Guard against someone trimming the vocabulary: without these we cannot answer
    activation, conversion, or retention on launch day."""
    required = {
        "app_open", "report_completed", "paywall_shown", "purchase_completed",
    }
    assert required <= ALLOWED_EVENTS


# ── backend ↔ iOS parity ─────────────────────────────────────────────────────

def test_ios_event_names_match_the_backend_allowlist():
    """A drift here fails SILENTLY in production: the backend drops unknown names
    (deliberately — see test_unknown_event_is_flagged_not_raised), so an iOS event
    the allowlist doesn't know just produces a permanently empty metric that looks
    like "nobody does that" rather than an error.

    Parsed from the Swift source rather than duplicated, so the test can't drift
    from the thing it guards.
    """
    import re
    from pathlib import Path

    swift = Path(__file__).resolve().parents[2] / (
        "frontend/ios/ios/Core/Services/Analytics.swift"
    )
    assert swift.exists(), f"Analytics.swift moved — update this test ({swift})"

    src = swift.read_text()
    block = re.search(r"enum AnalyticsEventName: String \{(.*?)\n\}", src, re.S)
    assert block, "could not find `enum AnalyticsEventName` in Analytics.swift"

    ios_events = set(re.findall(r'case\s+\w+\s*=\s*"([^"]+)"', block.group(1)))
    assert ios_events, "parsed zero event names — the enum's shape changed"

    missing_on_backend = ios_events - ALLOWED_EVENTS
    assert not missing_on_backend, (
        f"iOS emits {sorted(missing_on_backend)} but the backend allowlist would "
        f"DROP them — the metric would be silently empty. Add them to ALLOWED_EVENTS."
    )

    declared_but_absent = ALLOWED_EVENTS - ios_events
    assert not declared_but_absent, (
        f"backend allows {sorted(declared_but_absent)} but iOS declares no such case — "
        f"either add it to AnalyticsEventName or remove it from ALLOWED_EVENTS."
    )


def test_every_allowed_event_has_a_real_ios_call_site():
    """Stronger than the enum comparison above: a case can exist in the enum and
    still never be emitted, which produces the same silently-empty metric. This
    greps the actual `Analytics.shared.track(.x)` call sites."""
    import re
    from pathlib import Path

    ios_root = Path(__file__).resolve().parents[2] / "frontend/ios/ios"
    emitted_cases: set[str] = set()
    for swift_file in ios_root.rglob("*.swift"):
        for m in re.finditer(r"Analytics\.shared\.track\(\s*\.(\w+)", swift_file.read_text()):
            emitted_cases.add(m.group(1))

    # Map the enum's Swift case names back to their wire values.
    src = (ios_root / "Core/Services/Analytics.swift").read_text()
    block = re.search(r"enum AnalyticsEventName: String \{(.*?)\n\}", src, re.S)
    case_to_wire = dict(re.findall(r'case\s+(\w+)\s*=\s*"([^"]+)"', block.group(1)))

    emitted_wire = {case_to_wire[c] for c in emitted_cases if c in case_to_wire}
    never_emitted = ALLOWED_EVENTS - emitted_wire
    assert not never_emitted, (
        f"{sorted(never_emitted)} are allowed by the backend and declared on iOS, but "
        f"NO call site emits them. That metric will read as 'nobody does this' rather "
        f"than 'not instrumented'."
    )


# ── prop-key allowlist + timestamp hardening (post-review) ───────────────────

def test_unlisted_prop_keys_are_dropped():
    """`/events` is public and unauthenticated. Without a KEY allowlist the real
    guarantee would be '12 arbitrary keys x 120 chars of caller text per event',
    which is not what the privacy policy or the App Privacy filing describes."""
    e = AnalyticsEvent(event="chat_sent", props={
        "context": "ticker",
        "message": "the user's actual private chat text",
        "email": "someone@example.com",
        "query": "what the user searched for",
    })
    assert e.props == {"context": "ticker"}


def test_malformed_client_ts_is_nulled_not_passed_through():
    """client_ts lands in a TIMESTAMPTZ column. One unparseable value fails the
    ENTIRE multi-row INSERT (22007), so a single client with a broken clock format
    could repeatedly destroy 49 other good events per batch."""
    assert AnalyticsEvent(event="app_open", client_ts="not-a-date").client_ts is None
    assert AnalyticsEvent(event="app_open", client_ts="").client_ts is None
    assert AnalyticsEvent(event="app_open", client_ts="12/25/2026").client_ts is None


def test_valid_client_ts_survives():
    for ts in ("2026-07-30T21:00:00Z", "2026-07-30T21:00:00+00:00"):
        assert AnalyticsEvent(event="app_open", client_ts=ts).client_ts == ts


def test_one_bad_timestamp_does_not_poison_the_batch():
    batch = AnalyticsBatchRequest(events=[
        {"event": "app_open", "client_ts": "2026-07-30T21:00:00Z"},
        {"event": "chat_sent", "client_ts": "garbage"},
    ])
    assert batch.events[0].client_ts is not None
    assert batch.events[1].client_ts is None   # nulled, not rejected, not passed on


def test_analytics_has_its_own_rate_limit_bucket():
    """Sharing StandardRateLimit's window would let telemetry flushes 429 the user's
    REAL requests — instrumentation degrading the product is exactly what this
    module forbids."""
    from app.dependencies import AnalyticsRateLimit, StandardRateLimit

    analytics_dep = AnalyticsRateLimit.dependency
    standard_dep = StandardRateLimit.dependency
    assert getattr(analytics_dep, "bucket", None) == "analytics"
    assert analytics_dep is not standard_dep


@pytest.mark.asyncio
async def test_endpoint_survives_a_service_construction_failure(monkeypatch):
    """get_analytics_service() calls get_supabase(). If that throws, an eagerly
    constructed service would 500 this endpoint — the instrumentation-caused error
    the module forbids."""
    import app.api.v1.endpoints.analytics as ep

    def _explode():
        raise RuntimeError("supabase client init failed")

    monkeypatch.setattr(ep, "get_analytics_service", _explode)
    resp = await ep.ingest_events(
        AnalyticsBatchRequest(events=[{"event": "app_open"}]),
        user={"id": "u1"}, x_guest_id=None, x_app_version=None, x_platform=None,
    )
    assert resp.accepted == 0 and resp.dropped == 1   # 200, not a 500


def test_sweep_is_chunked_and_makes_forward_progress():
    """An unbounded DELETE over the highest-volume table risks a timeout on the first
    post-retention sweep — and then retries the same too-large delete every 2h
    forever, never deleting anything."""
    from app.services.analytics_service import AnalyticsService

    svc = object.__new__(AnalyticsService)
    remaining = {"n": 12000}
    deleted = {"n": 0}

    class _T:
        def select(self, *a): return self
        def lt(self, *a): return self
        def limit(self, n): self._n = n; return self
        # `returning` is asserted, not merely tolerated: postgrest-py defaults every write
        # to representation, so without minimal this DELETE ships each swept row back in
        # full (`props` and all). `sweep_expired` catches its own exceptions, so a wrong
        # kwarg here silently returns 0 forever instead of failing loudly — this assert is
        # the only thing between that and production.
        def delete(self, *, returning=None, count=None):
            assert returning == "minimal", f"expected returning='minimal', got {returning!r}"
            self._del = True
            return self
        def in_(self, col, ids): self._ids = ids; return self

        def execute(self):
            if getattr(self, "_del", False):
                deleted["n"] += len(self._ids)
                remaining["n"] -= len(self._ids)
                return type("R", (), {"data": []})()
            take = min(self._n, remaining["n"])
            return type("R", (), {"data": [{"id": i} for i in range(take)]})()

    svc.supabase = type("S", (), {"table": lambda self, n: _T()})()
    total = svc.sweep_expired()

    assert total == 12000
    assert remaining["n"] == 0


def test_ios_prop_keys_are_all_allowlisted():
    """Same silent-failure shape as the event-name parity test, one level down: a
    prop key the backend doesn't allow is DROPPED, so the dimension just reads as
    always-null rather than as an error."""
    import re
    from pathlib import Path

    from app.schemas.analytics import ALLOWED_PROP_KEYS

    ios_root = Path(__file__).resolve().parents[2] / "frontend/ios/ios"
    used_keys: set[str] = set()
    for swift_file in ios_root.rglob("*.swift"):
        text = swift_file.read_text()
        for call in re.finditer(
            r"Analytics\.shared\.track\(\s*\.\w+\s*,\s*\[(.*?)\]\s*\)", text, re.S
        ):
            used_keys.update(re.findall(r'"([^"]+)"\s*:', call.group(1)))

    assert used_keys, "parsed zero prop keys — the call shape changed"
    unlisted = used_keys - ALLOWED_PROP_KEYS
    assert not unlisted, (
        f"iOS sends prop key(s) {sorted(unlisted)} that the backend DROPS — that "
        f"dimension would silently be null forever. Add them to ALLOWED_PROP_KEYS."
    )
