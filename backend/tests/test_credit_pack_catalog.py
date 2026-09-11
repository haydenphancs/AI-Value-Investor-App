"""The credit-pack catalog behind the Buy Credits screen — the PRICING the client renders.

WHY THIS FILE EXISTS
--------------------
`normalize_pack_catalog`, `SubscriptionService.get_credit_pack_catalog`, and the two
`billing.py` handlers that serve the paywall (`get_plans`) and the Buy Credits screen
(`get_credit_packs`) had **zero test coverage**. Found by mutation: swapping the
`credits` and `price_cents` values inside `normalize_pack_catalog` — so "Starter" renders
as 299 credits for $1.30 — left the whole suite green, and so did gutting
`get_credit_packs` to serve `_FALLBACK_PACKS` unconditionally.

The plan-catalog twin (`normalize_catalog` / `get_plan_catalog`) has had
`test_subscription_service.py` since the paywall shipped. The pack side landed in
migration 117 and has been repriced twice since (138, then 141) with nothing pinning it.

Why the numbers matter more here than on the plan side: `CreditPackResponse.credits` is
AUTHORITATIVE — it is the same value `add_purchased_credits` grants — and the iOS
`CreditPackDTO` (SubscriptionModels.swift) decodes every field as a non-optional `Int` /
`String`. A wrong mapping is a user shown the wrong number of credits for their money; a
`None` that reaches the wire is a decode crash on the one screen that takes it.

Pure module plus a fake `sb`; no network, no Supabase.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.api.v1.api import api_router
from app.api.v1.endpoints import billing
from app.config import settings
from app.schemas.subscription import CreditPackCatalogResponse, PlanCatalogResponse
from app.services import subscription_service as svc

_MIGRATIONS = Path(__file__).resolve().parents[1] / "database" / "migrations"

# Deliberately NOT the seeded ladder: every number — credits, price AND sort_order —
# differs from `_FALLBACK_PACKS`, so a service or handler that quietly serves the constant
# instead of the table cannot pass on any of them.
_LIVE_PACKS = [
    {"product_id": "com.phan.caydex.credits.mega", "credits": 4200,
     "price_cents": 3199, "display_name": "Mega", "sort_order": 40, "is_active": True},
    {"product_id": "com.phan.caydex.credits.starter", "credits": 111,
     "price_cents": 199, "display_name": "Starter", "sort_order": 10, "is_active": True},
    {"product_id": "com.phan.caydex.credits.power", "credits": 900,
     "price_cents": 1499, "display_name": "Power", "sort_order": 30, "is_active": True},
    {"product_id": "com.phan.caydex.credits.plus", "credits": 333,
     "price_cents": 799, "display_name": "Plus", "sort_order": 20, "is_active": True},
]
_LIVE_BY_ID = {r["product_id"]: r for r in _LIVE_PACKS}
_LIVE_CREDITS = {pid: row["credits"] for pid, row in _LIVE_BY_ID.items()}
_PRICE_ORDER = [
    "com.phan.caydex.credits.starter",
    "com.phan.caydex.credits.plus",
    "com.phan.caydex.credits.power",
    "com.phan.caydex.credits.mega",
]

_LIVE_PLANS = [
    {"tier": "premium", "monthly_credits": 4321, "price_cents": 4599, "display_name": "Max"},
    {"tier": "free", "monthly_credits": 42, "price_cents": 0, "display_name": "Free"},
    {"tier": "pro", "monthly_credits": 1234, "price_cents": 1799, "display_name": "Pro"},
]

# Exactly the keys `CreditPackDTO` in SubscriptionModels.swift decodes — all non-optional.
_IOS_PACK_KEYS = {
    "product_id", "display_name", "credits", "price_cents", "price_label", "sort_order",
}


class _Result:
    def __init__(self, data):
        self.data = data


class _FakeSupabase:
    """Models the one `credit_packs` / `plan_credits` read the service makes.

    Unlike a rubber-stamp fake, it APPLIES the `.eq` filters and PROJECTS to the selected
    columns at `execute()` time, like PostgREST does — so dropping `.eq("is_active", True)`
    genuinely leaks a retired pack, and dropping `credits` from the select genuinely
    strips it from the rows. `rows=[]` / `data_none=True` model the empty-table cases the
    service's `or []` covers; `raises=True` the transport fault. All must serve the
    fallback, never an empty screen and never an exception.
    """

    def __init__(self, rows=None, *, raises=False, data_none=False):
        self.rows = [dict(r) for r in (_LIVE_PACKS if rows is None else rows)]
        self.raises = raises
        self.data_none = data_none
        self.tables_read: list[str] = []
        self.filters: list[tuple[str, object]] = []
        self._selected: str | None = None

    def table(self, name):
        self.tables_read.append(name)
        return self

    def select(self, columns, *_a, **_k):
        self._selected = columns
        return self

    def eq(self, column, value):
        self.filters.append((column, value))
        return self

    def limit(self, *_a, **_k):
        return self

    def execute(self):
        if self.raises:
            raise RuntimeError("supabase down")
        if self.data_none:
            return _Result(None)
        out = [dict(r) for r in self.rows
               if all(r.get(c) == v for c, v in self.filters)]
        # `select("*")` is every column in PostgREST; projecting it to nothing would
        # fail every handler test for a query that works in production.
        if self._selected and self._selected.strip() != "*":
            cols = {c.strip() for c in self._selected.split(",")}
            out = [{k: v for k, v in r.items() if k in cols} for r in out]
        return _Result(out)


@pytest.fixture(autouse=True)
def _no_real_supabase(monkeypatch):
    """`SubscriptionService.__init__` calls `get_supabase()`, which `subscription_service`
    binds with a MODULE-LEVEL import — so that module's own name is the one live at call
    time, and it is what every test below patches. Default it to a loud refusal: a test
    that forgets to install a fake must fail HERE, not build the real client and reach for
    the network (which conftest reports as a NETWORK block against the whole suite).

    There is no memoized global in `subscription_service` to reset; `app.database`'s
    client singleton is never constructed because this runs before any service does."""
    def _refuse():
        raise AssertionError("test did not install a fake supabase")

    monkeypatch.setattr(svc, "get_supabase", _refuse)


def _install(monkeypatch, sb: _FakeSupabase) -> _FakeSupabase:
    monkeypatch.setattr(svc, "get_supabase", lambda: sb)
    return sb


# ── 1. normalize_pack_catalog — the field mapping the client renders ─────────────

def test_every_seeded_field_passes_through_unchanged():
    """🔴 The money assertion. Swapping `credits` and `price_cents` here passed the whole
    suite. Each row is checked by product_id against its OWN input, so a mapping that
    reads the right field from the wrong row is caught too."""
    out = svc.normalize_pack_catalog(_LIVE_PACKS)
    assert len(out) == len(_LIVE_PACKS)
    for row in out:
        src = _LIVE_BY_ID[row["product_id"]]
        for field in ("credits", "price_cents", "display_name", "sort_order"):
            assert row[field] == src[field], f"{row['product_id']}.{field} was rewritten"


def test_price_label_is_derived_from_each_rows_own_price():
    """Pinned per row, not "some label exists": a label built from `credits`, or from a
    neighbouring row after the sort, is exactly the kind of mutation the file is for."""
    labels = {
        r["product_id"]: r["price_label"] for r in svc.normalize_pack_catalog(_LIVE_PACKS)
    }
    assert labels == {
        "com.phan.caydex.credits.starter": "$1.99",
        "com.phan.caydex.credits.plus": "$7.99",
        "com.phan.caydex.credits.power": "$14.99",
        "com.phan.caydex.credits.mega": "$31.99",
    }


def test_sorted_cheapest_to_dearest_regardless_of_input_order():
    ids = [r["product_id"] for r in svc.normalize_pack_catalog(list(reversed(_LIVE_PACKS)))]
    assert ids == _PRICE_ORDER


def test_a_bad_sort_order_cannot_present_a_dearer_pack_as_the_cheaper_one():
    """The docstring's explicit promise: price first, `sort_order` only as a tie-break.
    Inverting every sort_order (Mega first … Starter last) must change nothing."""
    inverted = [dict(r, sort_order=50 - r["sort_order"]) for r in _LIVE_PACKS]
    ids = [r["product_id"] for r in svc.normalize_pack_catalog(inverted)]
    assert ids == _PRICE_ORDER


def test_sort_order_breaks_a_price_tie():
    rows = [
        {"product_id": "b", "credits": 10, "price_cents": 500, "display_name": "B",
         "sort_order": 2},
        {"product_id": "a", "credits": 10, "price_cents": 500, "display_name": "A",
         "sort_order": 1},
    ]
    assert [r["product_id"] for r in svc.normalize_pack_catalog(rows)] == ["a", "b"]


def test_sorted_by_price_not_by_credits():
    """Found by mutation: in `_LIVE_PACKS` credits, price and sort_order all agree in
    order, and only sort_order's agreement is ever broken above — so a sort keyed on
    `credits` first passed every test in this file. The ladder invariant (dearer = more
    credits) makes that mutation invisible on seeded data, but the documented key is
    PRICE, and a promo row or a Studio hand-edit is exactly when the two disagree."""
    rows = [
        {"product_id": "few-dear", "credits": 100, "price_cents": 999, "display_name": "A",
         "sort_order": 1},
        {"product_id": "many-cheap", "credits": 5000, "price_cents": 199, "display_name": "B",
         "sort_order": 2},
    ]
    assert [r["product_id"] for r in svc.normalize_pack_catalog(rows)] == [
        "many-cheap", "few-dear",
    ]


def test_empty_is_empty():
    assert svc.normalize_pack_catalog([]) == []


@pytest.mark.parametrize("row", [
    pytest.param({"product_id": "x", "credits": 10, "display_name": "X", "sort_order": 9},
                 id="absent"),
    pytest.param({"product_id": "x", "credits": 10, "price_cents": None, "display_name": "X",
                  "sort_order": 9}, id="null"),
])
def test_a_missing_or_null_price_is_tolerated_as_zero(row):
    """Documented tolerance: `price_cents` is edited by hand in Studio. A row without one
    must not take down the catalog — it sorts first and labels as Free (the label is a
    display FALLBACK; Apple's `displayPrice` is what the client actually shows)."""
    dear = {"product_id": "y", "credits": 10, "price_cents": 999, "display_name": "Y",
            "sort_order": 1}
    out = svc.normalize_pack_catalog([dear, row])
    assert [r["product_id"] for r in out] == ["x", "y"]
    assert out[0]["price_label"] == "Free"


@pytest.mark.parametrize("row", [
    pytest.param({"product_id": "x", "credits": 10, "price_cents": 100, "display_name": "X"},
                 id="absent"),
    pytest.param({"product_id": "x", "credits": 10, "price_cents": 100, "display_name": "X",
                  "sort_order": None}, id="null"),
])
def test_a_missing_or_null_sort_order_is_coerced_to_int_zero(row):
    """`CreditPackDTO.sortOrder` is a non-optional `Int`, so a `None` on the wire is a
    decode crash on the Buy Credits screen. The coercion here is what keeps the row
    servable, and the TYPE is pinned as hard as the value."""
    (out,) = svc.normalize_pack_catalog([row])
    assert out["sort_order"] == 0
    assert type(out["sort_order"]) is int


def test_does_not_mutate_its_input():
    rows = [dict(r) for r in _LIVE_PACKS]
    before = [dict(r) for r in rows]
    _ = svc.normalize_pack_catalog(rows)
    assert rows == before
    assert all("price_label" not in r for r in rows)


def test_garbage_in_price_cents_never_renders_as_a_price():
    """Falls CLOSED. Two acceptable outcomes — raise (today's behaviour: `int()` refuses
    it, the endpoint 500s, iOS shows its error state) or drop the row. The one outcome this
    forbids is a label fabricated from garbage: "Free" or "$0.00" on a pack that costs money
    is a price shown to a user that nobody chose."""
    rows = [{"product_id": "x", "credits": 10, "price_cents": "two dollars",
             "display_name": "X", "sort_order": 1}]
    try:
        out = svc.normalize_pack_catalog(rows)
    except (ValueError, TypeError):
        return
    assert all(r["product_id"] != "x" for r in out), (
        f"a non-numeric price was rendered as {out!r} instead of being refused"
    )


# ── 2. SubscriptionService.get_credit_pack_catalog — table read + fallback ───────

def test_serves_the_live_table_not_the_constant(monkeypatch):
    """Anti-vacuity for the whole service: every live number differs from the fallback, so
    `return normalize_pack_catalog(_FALLBACK_PACKS)` — the shape of "the table is down but
    nobody noticed for a month" — fails here. The table name is pinned because a read of
    `plan_credits` would ALSO fall back and look healthy."""
    sb = _install(monkeypatch, _FakeSupabase())
    out = svc.SubscriptionService().get_credit_pack_catalog()

    assert sb.tables_read == ["credit_packs"]
    assert [r["product_id"] for r in out] == _PRICE_ORDER
    assert {r["product_id"]: r["credits"] for r in out} == _LIVE_CREDITS
    assert all(r["price_label"].startswith("$") for r in out)


def test_a_retired_pack_is_not_listed(monkeypatch):
    """Retiring a pack is a DB flag, not a deploy. The fake applies the filter, so removing
    `.eq("is_active", True)` from the query — not just mistyping it — surfaces here as the
    cheapest pack on the screen being one Apple no longer sells.

    The live credits are pinned too, because the fallback ladder has the SAME four ids in
    the SAME order: a filter mutated to match nothing (`"true"` for `True`) would serve the
    fallback and pass the id assertions alone."""
    retired = {"product_id": "com.phan.caydex.credits.retired", "credits": 1,
               "price_cents": 1, "display_name": "Retired", "sort_order": 0,
               "is_active": False}
    sb = _install(monkeypatch, _FakeSupabase(rows=[retired, *_LIVE_PACKS]))
    out = svc.SubscriptionService().get_credit_pack_catalog()

    ids = [r["product_id"] for r in out]
    assert "com.phan.caydex.credits.retired" not in ids
    assert ids == _PRICE_ORDER
    assert {r["product_id"]: r["credits"] for r in out} == _LIVE_CREDITS
    assert ("is_active", True) in sb.filters


@pytest.mark.parametrize("make_sb", [
    pytest.param(lambda: _FakeSupabase(rows=[]), id="empty-table"),
    pytest.param(lambda: _FakeSupabase(data_none=True), id="data-is-None"),
    pytest.param(lambda: _FakeSupabase(raises=True), id="transport-fault"),
])
def test_an_unreadable_table_serves_the_seeded_fallback(monkeypatch, make_sb):
    """Both branches of the degrade path — empty result AND exception — must land on the
    same place: the seeded ladder, normalized, so the Buy Credits screen still renders.
    An empty list here is a blank screen; a raise is a 500 on the money path."""
    _install(monkeypatch, make_sb())
    out = svc.SubscriptionService().get_credit_pack_catalog()

    assert out, "an empty fallback is a blank Buy Credits screen for the whole outage"
    assert [r["product_id"] for r in out] == [r["product_id"] for r in svc._FALLBACK_PACKS]
    for row in out:
        assert row["price_label"].startswith("$"), row
        assert row["credits"] > 0, row
    # Served as copies — stamping `price_label` must not leak into the module constant.
    assert all("price_label" not in r for r in svc._FALLBACK_PACKS)


def _strip_sql_comments(sql: str) -> str:
    """Migration 141's header QUOTES the 138 ladder in prose; a raw-text scan would parse
    rows out of a comment."""
    sql = re.sub(r"/\*.*?\*/", "", sql, flags=re.S)
    return re.sub(r"--[^\n]*", "", sql)


def _seeded_packs() -> tuple[str, dict[str, dict]]:
    """{product_id -> row} from the HIGHEST-numbered migration that INSERTs into
    `credit_packs`, columns indexed BY NAME from the header. Resolved rather than pinned
    to a filename, for the reason `test_iap_product_and_privacy_parity._effective_seed`
    spells out: 117 and 138 still contain perfectly valid, superseded ladders."""
    candidates = []
    for path in sorted(_MIGRATIONS.glob("[0-9][0-9][0-9]_*.sql")):
        code = _strip_sql_comments(path.read_text(encoding="utf-8"))
        if re.search(r"INSERT\s+INTO\s+(?:public\.)?credit_packs\b", code, re.I):
            candidates.append((int(path.name[:3]), path.name, code))
    assert candidates, "no migration INSERTs into credit_packs — the resolver has drifted"
    _, name, code = max(candidates, key=lambda c: c[0])
    m = re.search(
        r"INSERT\s+INTO\s+(?:public\.)?credit_packs\s*\(([^)]*)\)\s*VALUES(.*?)"
        r"(?:ON\s+CONFLICT|;)",
        code, re.I | re.S,
    )
    assert m, f"could not parse the credit_packs seed in {name}"
    columns = [c.strip() for c in m.group(1).split(",")]
    rows: dict[str, dict] = {}
    for tup in re.findall(r"\(([^()]*)\)", m.group(2)):
        raw = [v.strip() for v in tup.split(",")]
        if len(raw) != len(columns):
            continue
        row = {c: (v[1:-1] if v.startswith("'") else int(v)) for c, v in zip(columns, raw)}
        rows[row["product_id"]] = row
    return name, rows


def test_the_fallback_mirrors_the_effective_migration_seed():
    """The fallback is what an outage RENDERS. If a reprice migration lands without this
    constant moving, a Supabase blip shows last quarter's ladder — and `credits` on that
    screen is described to the user as what they will receive."""
    name, seeded = _seeded_packs()
    assert len(seeded) == 4, f"{name} seeded {len(seeded)} packs — resolver went vacuous"

    fallback = {r["product_id"]: r for r in svc._FALLBACK_PACKS}
    assert set(fallback) == set(seeded), f"fallback packs != {name} packs"
    for pid, seed in seeded.items():
        for col in ("credits", "price_cents", "display_name", "sort_order"):
            assert fallback[pid][col] == seed[col], (
                f"_FALLBACK_PACKS[{pid}].{col} = {fallback[pid][col]!r} but {name} seeds "
                f"{seed[col]!r} — the outage screen would show a stale ladder"
            )


# ── 3. The two handlers — what iOS actually decodes ──────────────────────────────
#
# The real `SubscriptionService` runs behind each handler against the fake (the handler
# constructs it; only the module-level `get_supabase` binding is stubbed). So these prove
# the WIRING — a handler serving `_FALLBACK_PACKS` directly, or the plan catalog, or a
# hand-built list, all fail on the distinctive live numbers.

@pytest.mark.asyncio
async def test_get_credit_packs_returns_what_the_service_produces(monkeypatch):
    _install(monkeypatch, _FakeSupabase())
    resp = await billing.get_credit_packs()

    assert isinstance(resp, CreditPackCatalogResponse)
    assert [p.product_id for p in resp.packs] == _PRICE_ORDER
    for pack in resp.packs:
        src = _LIVE_BY_ID[pack.product_id]
        assert (pack.credits, pack.price_cents, pack.display_name, pack.sort_order) == (
            src["credits"], src["price_cents"], src["display_name"], src["sort_order"]
        )
    assert resp.report_cost == settings.REPORT_CREDIT_COST
    assert resp.chat_cost == settings.CHAT_CREDIT_COST


@pytest.mark.asyncio
async def test_get_credit_packs_wire_shape_is_exactly_what_the_ios_dto_decodes(monkeypatch):
    """`CreditPackDTO` synthesizes its decoder from six non-optional fields. A missing key
    or a `null` is a decode failure on the Buy Credits screen; the pin is on the DUMPED
    shape — what goes on the wire — not on the Python object."""
    _install(monkeypatch, _FakeSupabase())
    wire = (await billing.get_credit_packs()).model_dump()

    assert set(wire) == {"packs", "report_cost", "chat_cost"}
    assert wire["packs"], "an empty catalog is a blank Buy Credits screen"
    for pack in wire["packs"]:
        assert set(pack) == _IOS_PACK_KEYS, pack
        assert None not in pack.values(), pack
        for k in ("credits", "price_cents", "sort_order"):
            assert type(pack[k]) is int, (k, pack[k])
        for k in ("product_id", "display_name", "price_label"):
            assert type(pack[k]) is str, (k, pack[k])


@pytest.mark.asyncio
async def test_get_credit_packs_still_renders_when_supabase_is_down(monkeypatch):
    _install(monkeypatch, _FakeSupabase(raises=True))
    resp = await billing.get_credit_packs()
    assert resp.packs, "a Supabase outage blanked the Buy Credits screen"
    assert [p.product_id for p in resp.packs] == [r["product_id"] for r in svc._FALLBACK_PACKS]
    assert all(p.credits > 0 for p in resp.packs)


@pytest.mark.parametrize("credits", [
    pytest.param("<absent>", id="missing"),
    pytest.param(None, id="null"),
    pytest.param("lots", id="garbage"),
])
@pytest.mark.asyncio
async def test_a_pack_row_with_no_usable_credits_is_refused_not_served_as_zero(
    monkeypatch, credits,
):
    """`credits` is AUTHORITATIVE (schema docstring): what the screen promises is what
    `add_purchased_credits` grants. A row that lost the column must not reach the wire
    with a defaulted 0 — "0 credits for $1.99" is a listing, not an error.

    Falls CLOSED, two acceptable outcomes: refuse the response (today's behaviour —
    Pydantic raises, the endpoint 500s, iOS shows its error state) or drop the row. What
    it forbids is the row reaching the wire with a fabricated count — a
    `credits: int = 0` default on `CreditPackResponse` is exactly the mutation this
    fails on."""
    broken = [dict(r) for r in _LIVE_PACKS]
    if credits == "<absent>":
        del broken[1]["credits"]
    else:
        broken[1]["credits"] = credits
    victim = broken[1]["product_id"]
    _install(monkeypatch, _FakeSupabase(rows=broken))

    try:
        resp = await billing.get_credit_packs()
    except ValidationError as e:
        # The refusal must be ABOUT `credits`. A raise on some other field would also
        # land here and pass, with the credits contract never exercised.
        assert any(err["loc"] == ("credits",) for err in e.errors()), e.errors()
        return
    assert all(p.product_id != victim for p in resp.packs), (
        f"{victim} lost its credits column and was still listed: "
        f"{[(p.product_id, p.credits) for p in resp.packs]}"
    )


@pytest.mark.asyncio
async def test_get_plans_returns_what_the_service_produces(monkeypatch):
    sb = _install(monkeypatch, _FakeSupabase(rows=_LIVE_PLANS))
    resp = await billing.get_plans()

    assert isinstance(resp, PlanCatalogResponse)
    assert sb.tables_read == ["plan_credits"]
    assert [p.tier for p in resp.plans] == ["free", "pro", "premium"]
    assert {p.tier: p.monthly_credits for p in resp.plans} == {
        "free": 42, "pro": 1234, "premium": 4321,
    }
    assert {p.tier: p.price_label for p in resp.plans} == {
        "free": "Free", "pro": "$17.99", "premium": "$45.99",
    }
    assert all(p.features for p in resp.plans), "a plan reached the paywall with no features"
    assert resp.report_cost == settings.REPORT_CREDIT_COST
    assert resp.chat_cost == settings.CHAT_CREDIT_COST


@pytest.mark.asyncio
async def test_get_plans_still_renders_when_supabase_is_down(monkeypatch):
    _install(monkeypatch, _FakeSupabase(raises=True))
    resp = await billing.get_plans()
    assert [p.tier for p in resp.plans] == ["free", "pro", "premium"]
    assert all(p.features for p in resp.plans)


@pytest.mark.parametrize("handler", [billing.get_plans, billing.get_credit_packs])
@pytest.mark.asyncio
async def test_the_per_action_costs_are_read_live_from_settings(monkeypatch, handler):
    """Both screens quote "a report costs N credits" next to the ladder. The numbers must
    come from `settings` at call time — not a literal copied in when the handler was
    written — or a repricing via environment leaves the storefront lying."""
    rows = _LIVE_PLANS if handler is billing.get_plans else None
    _install(monkeypatch, _FakeSupabase(rows=rows))
    monkeypatch.setattr(settings, "REPORT_CREDIT_COST", 77)
    monkeypatch.setattr(settings, "CHAT_CREDIT_COST", 3)
    resp = await handler()
    assert (resp.report_cost, resp.chat_cost) == (77, 3)


def test_both_catalog_routes_are_registered_where_ios_looks():
    """`APIEndpoint.swift` hardcodes `/api/v1/billing/plans` and
    `/api/v1/billing/credit-packs`. A renamed path or a re-prefixed router is a 404 on the
    paywall, which the handler tests above cannot see. `api_router` is what `main.py`
    mounts at `/api/v1` (pinned by `test_account_only_licence_gate.py`), so the paths
    here are checked one level below that mount."""
    # `getattr`: a WebSocket route or a Mount has no `.methods`, and one of those landing
    # on `api_router` later must not crash the pricing guard.
    routes = {
        (r.path, tuple(sorted(getattr(r, "methods", None) or ())))
        for r in api_router.routes
    }
    assert ("/billing/plans", ("GET",)) in routes
    assert ("/billing/credit-packs", ("GET",)) in routes
    # And they resolve to THESE handlers, not a same-path route registered elsewhere.
    endpoints = {r.path: getattr(r, "endpoint", None) for r in api_router.routes}
    assert endpoints["/billing/plans"] is billing.get_plans
    assert endpoints["/billing/credit-packs"] is billing.get_credit_packs
