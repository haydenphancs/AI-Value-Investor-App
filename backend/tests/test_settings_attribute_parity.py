"""Every `settings.X` read ANYWHERE under `app/` must be defined on `Settings`.

WHY THIS FILE EXISTS — it has now happened twice, the same way both times.

`Settings` sets `extra="ignore"`, so an undeclared name is not a config error: the env var
is silently dropped and `settings.X` raises `AttributeError` at call time. When that read
sits in a background loop BEFORE its `while True` (or outside any `try`), the task dies
seconds after boot, nothing restarts it, and the API stays healthy — so the feature is
simply absent in production with no failing request to point at.

1. `_run_scheduled_notification_senders` read `EARNINGS_NOTIFY_HOUR_ET` /
   `SMART_MONEY_NOTIFY_HOUR_ET`, neither defined. Both daily senders never ran.
   `test_notification_scheduler_settings.py` was written to stop exactly this.
2. It did not. That guard is scoped to ONE function in ONE module, so it never saw
   `run_price_alert_loop` reading four undefined `PRICE_ALERT_*` names — killing the loop
   at `price_alert_service.py`'s interval read and 503-ing all four /alerts/price routes
   for every user, indefinitely.

A guard narrower than the bug class does not close it. This one sweeps the whole package.

An import check cannot catch any of this: the names are read at call time inside async
tasks, so the module imports cleanly and the failure only exists at runtime.
"""

import ast
from pathlib import Path

import pytest

import app as app_pkg
from app.config import settings

APP_ROOT = Path(app_pkg.__file__).resolve().parent

# `settings` is a module-level singleton everywhere in this codebase. If a function ever
# shadows the name with a local, add it here with a comment rather than weakening the scan.
_SHADOWED: set[str] = set()


def _iter_settings_reads():
    """Yield (attribute_name, relative_path, lineno) for every `settings.X` under app/."""
    for path in sorted(APP_ROOT.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError as e:  # a broken file is its own failure; don't mask it here
            pytest.fail(f"{path.relative_to(APP_ROOT.parent)} does not parse: {e}")
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.value.id == "settings"
                and node.attr not in _SHADOWED
            ):
                yield node.attr, str(path.relative_to(APP_ROOT.parent)), node.lineno


def test_every_settings_attribute_read_under_app_is_defined():
    missing: dict[str, list[str]] = {}
    for attr, rel_path, lineno in _iter_settings_reads():
        if not hasattr(settings, attr):
            missing.setdefault(attr, []).append(f"{rel_path}:{lineno}")

    assert not missing, (
        "These `settings.X` names are read but NOT defined on Settings:\n"
        + "\n".join(
            f"  settings.{attr} — {', '.join(sites)}" for attr, sites in sorted(missing.items())
        )
        + '\n\n`extra="ignore"` means a Railway variable CANNOT supply them: the read raises '
        "AttributeError. If the read sits outside a try (or before a loop's `while True`), "
        "the whole task dies silently at boot and the feature is absent in production."
    )


# ── Guards against the guard ────────────────────────────────────────────────────
# A source scan that silently stops matching passes forever. These pin that it is
# actually looking at the codebase, and that it would still fail on a real regression.


def test_the_sweep_is_not_vacuous():
    reads = list(_iter_settings_reads())
    files = {rel for _, rel, _ in reads}
    attrs = {attr for attr, _, _ in reads}

    assert len(files) >= 20, f"only {len(files)} files contain settings reads — scan drifted"
    assert len(attrs) >= 50, f"only {len(attrs)} distinct settings read — scan drifted"


def test_the_sweep_would_catch_a_missing_name():
    """Mutation test: the assertion must actually fire on an undefined name."""
    assert not hasattr(settings, "DEFINITELY_NOT_A_REAL_SETTING")
    bogus = [
        attr for attr in ("DEFINITELY_NOT_A_REAL_SETTING",) if not hasattr(settings, attr)
    ]
    assert bogus, "hasattr-based detection is broken — the real test would pass vacuously"


def test_the_regressions_this_file_exists_for_stay_fixed():
    """The two specific outages, pinned by name so a revert is a red test."""
    for name in (
        # Outage 1 — daily notification senders
        "EARNINGS_NOTIFY_HOUR_ET",
        "SMART_MONEY_NOTIFY_HOUR_ET",
        "PROFILE_MATCH_NOTIFY_HOUR_ET",
        # Outage 2 — price alerts
        "PRICE_ALERT_INTERVAL_SECONDS",
        "PRICE_ALERT_MAX_PER_USER",
        "PRICE_ALERT_MAX_PER_TICKER_PER_USER",
        "PRICE_ALERT_REARM_PCT",
    ):
        assert hasattr(settings, name), f"Settings no longer defines {name}"


def test_price_alert_settings_are_sane():
    """Values a bad default would make dangerous rather than merely wrong."""
    # The loop floors this at 15s; a 0 or negative default would spin the sweeper.
    assert settings.PRICE_ALERT_INTERVAL_SECONDS >= 15
    assert settings.PRICE_ALERT_MAX_PER_USER > 0
    assert settings.PRICE_ALERT_MAX_PER_TICKER_PER_USER > 0
    # Per-ticker cap must not exceed the per-user cap, or it can never bind.
    assert settings.PRICE_ALERT_MAX_PER_TICKER_PER_USER <= settings.PRICE_ALERT_MAX_PER_USER
    # A 0 re-arm band notifies every cycle while price oscillates across the threshold;
    # a band >= 1.0 (100%) can never re-arm.
    assert 0 < settings.PRICE_ALERT_REARM_PCT < 1.0


def test_price_alert_caps_match_the_schema_defaults_ios_falls_back_to():
    """iOS shows the schema defaults until its first successful fetch. If these drift, a
    user sees one cap in the UI and hits a different one on POST."""
    from app.schemas.price_alerts import PriceAlertListResponse

    defaults = PriceAlertListResponse()
    assert defaults.max_per_user == settings.PRICE_ALERT_MAX_PER_USER
    assert defaults.max_per_ticker == settings.PRICE_ALERT_MAX_PER_TICKER_PER_USER


def test_the_chat_budgets_sit_under_the_ios_timeouts_that_bound_them():
    """`CHAT_SEND_BUDGET_SECONDS` exists so the non-stream door answers BEFORE iOS gives up
    (its client-side timeout is what turns a slow answer into a refund-or-double-charge
    question); nothing pinned the two numbers together (F03-12). Read the iOS table rather
    than hardcoding a third copy of the number."""
    import re
    from pathlib import Path

    swift = (Path(__file__).resolve().parents[2]
             / "frontend/ios/ios/Core/Services/APIEndpoint.swift").read_text(encoding="utf-8")
    code = "\n".join(re.sub(r"//.*$", "", l) for l in swift.splitlines())
    i = code.index("nonisolated var timeout: TimeInterval")
    body = code[i:i + 3000]
    send = re.search(r"case \.sendChatMessage:\s*return (\d+)", body)
    stream = re.search(r"case \.streamChatMessage:\s*return (\d+)", body)
    assert send and stream, "the iOS timeout table changed shape"
    ios_send, ios_stream = int(send.group(1)), int(stream.group(1))
    assert 0 < settings.CHAT_SEND_BUDGET_SECONDS < ios_send, (
        f"CHAT_SEND_BUDGET_SECONDS={settings.CHAT_SEND_BUDGET_SECONDS} must be under the iOS "
        f"sendChatMessage timeout ({ios_send}s) or the client reports failure first"
    )
    # The stream is kept alive by keepalives, so its wall-clock budget may exceed the
    # client's IDLE timeout — but the keepalive interval itself must sit well under it.
    assert settings.CHAT_STREAM_KEEPALIVE_SECONDS < ios_stream / 2


def _upper_names_read_outside_config() -> set:
    """Every attribute name and whole-string constant in app/ outside config.py (the AST, so a
    name that survives only in a comment never counts)."""
    names: set = set()
    for path in sorted(APP_ROOT.rglob("*.py")):
        if path.name == "config.py" and path.parent == APP_ROOT:
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"), filename=str(path))):
            if isinstance(node, ast.Attribute):
                names.add(node.attr)
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                names.add(node.value)
    return names


def test_every_iap_setting_is_read_under_app():
    """The reverse direction, for the IAP_ family (IAP audit F24): a declared setting nothing
    reads is a promise with no code behind it. IAP_WEBHOOK_SHARED_SECRET's comment said the
    webhook "additionally requires it" — nothing ever read it, and App Store Server
    Notifications V2 carry no secret at all."""
    from app.config import Settings

    iap = sorted(n for n in Settings.model_fields if n.startswith("IAP_"))
    read = _upper_names_read_outside_config()
    assert len(iap) >= 9, f"only {len(iap)} IAP_ settings declared — scan drifted"
    assert {"IAP_ENVIRONMENT", "IAP_BILLING_GRACE_DAYS", "IAP_ROOT_CERT_DIR"} <= read, \
        "the scan no longer sees settings it knows are read"
    unread = [n for n in iap if n not in read]
    assert unread == [], f"IAP_ settings that nothing under app/ reads: {unread}"


# ── Per-post marketing ART (owner decisions 2026-11-15: "Google draws, Claude checks") ─────────────
# Six web settings, every one fail-closed. The bounds are pinned from `FieldInfo.metadata` (pydantic v2
# keeps ge/le there as annotated_types.Ge/Le — a FieldInfo has no `.le` attribute), so a loosened bound
# is a red test, and an out-of-range value fails the deploy with the field named.


def _bound(name: str, kind: str):
    from app.config import Settings

    return next(getattr(m, kind) for m in Settings.model_fields[name].metadata
                if getattr(m, kind, None) is not None)


def test_the_art_settings_default_to_off_and_to_the_owners_numbers():
    from app.config import Settings

    fields = Settings.model_fields
    assert fields["MARKETING_ART_ENABLED"].default is False, "fail-closed: no art until the owner turns it on"
    assert fields["MARKETING_ART_MONTHLY_BUDGET_USD"].default == 30.0
    assert fields["MARKETING_ART_BUDGET_SECONDS"].default == 180
    assert fields["MARKETING_ART_CLAUDE_MODEL"].default == "claude-opus-5-5"
    assert fields["MARKETING_ART_IMAGE_MODEL"].default == "gemini-3-pro-image"
    assert fields["MARKETING_ART_IMAGE_MICROS"].default == 134_000


def test_the_art_settings_bounds_are_pinned():
    assert (_bound("MARKETING_ART_MONTHLY_BUDGET_USD", "ge"), _bound("MARKETING_ART_MONTHLY_BUDGET_USD", "le")) \
        == (0, 500)
    # le 240: the template build + the art + two kick waits stay under the worker's script poll, and the
    # voice stage inside its start margin (the timing pins in the marketing suites read this bound)
    assert (_bound("MARKETING_ART_BUDGET_SECONDS", "ge"), _bound("MARKETING_ART_BUDGET_SECONDS", "le")) == (60, 240)
    assert (_bound("MARKETING_ART_IMAGE_MICROS", "ge"), _bound("MARKETING_ART_IMAGE_MICROS", "le")) \
        == (1_000, 1_000_000)
    assert _bound("MARKETING_ART_MONTHLY_BUDGET_USD", "allow_inf_nan") is False


@pytest.mark.parametrize("name,value", [
    ("MARKETING_ART_MONTHLY_BUDGET_USD", "-1"), ("MARKETING_ART_MONTHLY_BUDGET_USD", "500.01"),
    ("MARKETING_ART_MONTHLY_BUDGET_USD", "nan"), ("MARKETING_ART_MONTHLY_BUDGET_USD", "inf"),
    ("MARKETING_ART_BUDGET_SECONDS", "59"), ("MARKETING_ART_BUDGET_SECONDS", "241"),
    ("MARKETING_ART_BUDGET_SECONDS", "90.5"),
    ("MARKETING_ART_IMAGE_MICROS", "999"), ("MARKETING_ART_IMAGE_MICROS", "1000001"),
    ("MARKETING_ART_ENABLED", "maybe"),
])
def test_an_out_of_range_art_setting_fails_the_deploy(monkeypatch, name, value):
    from pydantic import ValidationError

    from app.config import Settings

    monkeypatch.setenv(name, value)
    with pytest.raises(ValidationError) as info:
        Settings()
    assert name in str(info.value)


@pytest.mark.parametrize("name,value,expected", [
    ("MARKETING_ART_MONTHLY_BUDGET_USD", "0", 0.0), ("MARKETING_ART_MONTHLY_BUDGET_USD", "500", 500.0),
    ("MARKETING_ART_BUDGET_SECONDS", "60", 60), ("MARKETING_ART_BUDGET_SECONDS", "240", 240),
    ("MARKETING_ART_IMAGE_MICROS", "1000", 1_000), ("MARKETING_ART_ENABLED", "true", True),
])
def test_the_art_settings_accept_their_edges(monkeypatch, name, value, expected):
    from app.config import Settings

    monkeypatch.setenv(name, value)
    assert getattr(Settings(), name) == expected


def test_the_art_models_defaults_are_a_priced_claude_model_and_a_stable_image_model():
    """The cap must be able to price the default brief/check model (digest_service's Claude table), and
    the default drawing model is never a moving -preview / -latest id (marketing.md §7a)."""
    from app.config import Settings
    from app.services.marketing import digest_service

    claude = Settings.model_fields["MARKETING_ART_CLAUDE_MODEL"].default
    image = Settings.model_fields["MARKETING_ART_IMAGE_MODEL"].default
    assert digest_service.claude_rates(claude) is not None, claude
    assert claude.startswith("claude-") and not image.startswith("claude-")
    for model in (claude, image):
        assert not model.endswith(("-preview", "-latest")) and model == model.strip()


# ── The Home Screen widget switches (migration 216, 2026-11-27) ──────────────────────────────
#
# Every one defaults to today's behaviour (OFF / the owner's numbers) and every tuning field is
# BOUNDED, so a bad Railway value fails the deploy instead of silently widening a cap.

_WIDGET_SETTINGS = {
    # name: (default, ge, le) — None bounds for the two switches
    "WIDGET_HOLDINGS_GRANTS_ENABLED": (False, None, None),
    "WIDGET_PUSH_ENABLED": (False, None, None),
    "WIDGET_PUSH_MIN_INTERVAL_SECONDS": (3600, 900, 86400),
    "WIDGET_PUSH_DAILY_CAP": (12, 1, 48),
    "WIDGET_PUSH_PASS_BUDGET_SECONDS": (20, 5, 60),
    "WIDGET_PUSH_MAX_SENDS_PER_PASS": (200, 10, 2000),
    "WIDGET_PUSH_SENDS_PER_SECOND": (20, 1, 100),
    "WIDGET_CRYPTO_MAX_AGE_SECONDS": (600, 60, 3600),
    "WIDGET_CRYPTO_BOARD_MAX_COINS": (250, 1, 1000),
    "WIDGET_CRYPTO_BOARD_PER_USER": (20, 1, 100),
}


@pytest.mark.parametrize("name", sorted(_WIDGET_SETTINGS))
def test_the_widget_settings_are_declared_with_their_defaults_and_bounds(name):
    from app.config import Settings

    default, ge, le = _WIDGET_SETTINGS[name]
    field = Settings.model_fields.get(name)
    assert field is not None, f"{name} is not declared on Settings"
    assert field.default == default, (name, field.default)
    bounds = {type(m).__name__: m for m in field.metadata}
    if ge is None:
        assert field.annotation is bool
        return
    assert bounds["Ge"].ge == ge and bounds["Le"].le == le, (name, field.metadata)


@pytest.mark.parametrize("name,value", [
    ("WIDGET_PUSH_DAILY_CAP", 0), ("WIDGET_PUSH_DAILY_CAP", 49),
    ("WIDGET_PUSH_MIN_INTERVAL_SECONDS", 60), ("WIDGET_CRYPTO_BOARD_MAX_COINS", 0),
    ("WIDGET_PUSH_MAX_SENDS_PER_PASS", 5000),
])
def test_an_out_of_bounds_widget_value_fails_validation(name, value):
    """Pinned to the FIELD: a baseline that stopped validating (a new validator, a masked
    secret) would otherwise make every row pass whatever the bounds are."""
    import pydantic

    from app.config import Settings

    with pytest.raises(pydantic.ValidationError) as e:
        Settings.model_validate({**settings.model_dump(), name: value})
    locs = [err["loc"] for err in e.value.errors()]
    assert locs == [(name,)], locs          # locations only: an input may be a secret


def test_the_unmodified_settings_dump_validates():
    """The control for the rows above: the baseline they perturb is itself valid. On failure,
    only the failing field NAMES are reported — pydantic's message would echo the inputs, and
    the dump holds every secret."""
    import pydantic

    from app.config import Settings

    try:
        Settings.model_validate(settings.model_dump())
    except pydantic.ValidationError as e:
        pytest.fail(f"the settings dump does not validate: {[err['loc'] for err in e.errors()]}",
                    pytrace=False)


@pytest.mark.parametrize("name", sorted(n for n, (_d, ge, _le) in _WIDGET_SETTINGS.items() if ge))
def test_each_widget_bound_is_inclusive(name):
    """Both ends validate; one past either end fails on that field alone."""
    import pydantic

    from app.config import Settings

    _default, ge, le = _WIDGET_SETTINGS[name]
    base = settings.model_dump()
    for ok in (ge, le):
        assert getattr(Settings.model_validate({**base, name: ok}), name) == ok
    for bad in (ge - 1, le + 1):
        with pytest.raises(pydantic.ValidationError) as e:
            Settings.model_validate({**base, name: bad})
        locs = [err["loc"] for err in e.value.errors()]
        assert locs == [(name,)], locs
