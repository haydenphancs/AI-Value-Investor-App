"""The notification registry — one place that answers "what is this alert, and who
decides whether it goes out?"

Every notification the app can send is declared here as a `NotificationKind`. Nothing
else in the backend is allowed to invent a preference key, a cap bucket, an APNs
interruption level, or a route shape: a sender names a kind, and this table supplies
the rest.

WHY A REGISTRY AND NOT ARGUMENTS AT THE CALL SITE
-------------------------------------------------
Three separate failures made this necessary, and each of them is a real one this repo
has already had or narrowly avoided:

  1. **A preference key with no sender, or a sender with no toggle.** Twelve of the
     thirteen `notify_*` keys were synced by the app and read by `preference_enabled`
     while nothing ever sent them, so their toggles had to be HIDDEN. The inverse
     shipped too — users received alerts with no in-app way to turn them off, only iOS
     Settings, which kills every type at once and never re-prompts. Both directions are
     now pinned by tests against this table.
  2. **A single global daily cap.** `PUSH_MAX_ALERTS_PER_USER_PER_DAY = 3` counted every
     row in `push_send_log`, so an earnings reminder and a price move competed for the
     same three slots. `category` + `CATEGORY_DAILY_CAPS` splits the budget.
  3. **Copy sprawl.** Notification copy is the surface a regulator reads. FINRA and the
     SEC name push notifications explicitly as a digital-engagement practice under
     supervision, and the UK FCA measured an 11% trading-volume increase from push
     alone. Keeping every template in one file makes "is our copy informational rather
     than directive?" a question that can be answered by reading one screen, and keeps
     it that way as senders multiply.

INVARIANT: `default_on` MIRRORS the iOS `preferenceDefaults` map in
`NotificationsSettingsView.swift`. It is not a second opinion — the client omits any
key the user has never touched (`SettingsSyncManager.currentBlob()` skips keys with no
UserDefaults entry), so the backend default IS what an untouched toggle means. A
mismatch opts users into something the UI shows as off.
`tests/test_notification_kinds.py` pins the two sides together.

This module is PURE DATA + PURE FUNCTIONS. No I/O, no Supabase, no imports from the
service layer — so it is trivially unit-testable and safe to import from anywhere,
including the source-scan guards.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional

# Key NAMES only — `quiet_hours` is a pure leaf and imports nothing from here, so this
# direction is safe and keeps each key spelled in exactly one place.
from app.services.quiet_hours import PREF_QUIET_ENABLED


# ── Categories ───────────────────────────────────────────────────────────────
# The cap bucket a kind counts against. Stored denormalised on every
# `notification_events` row so the per-recipient cap probe never joins, and so
# editing this file cannot retroactively re-bucket history a cap already counted.

CATEGORY_WATCHLIST = "watchlist"
CATEGORY_EARNINGS = "earnings"
CATEGORY_SMART_MONEY = "smart_money"
CATEGORY_PRICE_ALERT = "price_alert"
CATEGORY_APP = "app"
# Deliberately "match", NOT "for_you": "for you" is suitability-adjacent in exactly
# the way chat_guardrails._SUITABILITY_PATTERNS exists to catch, and a notification
# category name leaks into copy far too easily.
CATEGORY_MATCH = "match"

# Per-user, per-CATEGORY, per-day ceiling. `None` = uncapped.
#
# These numbers are the product decision, not a technical limit. iOS never re-prompts
# once a user disables notifications, so the cost of over-sending is permanent and
# unrecoverable — every value here is deliberately on the low side and should only move
# after the opt-out rate has been measured.
#
# `app` is uncapped because its only member is `research_complete`: the user pressed a
# button and paid credits seconds ago. Capping the answer to a request the user just
# made would be indistinguishable from the feature being broken.
#
# `watchlist` keeps 3 so the one shipping notification's behaviour is unchanged by the
# refactor; `settings.PUSH_MAX_ALERTS_PER_USER_PER_DAY` still overrides it (see
# `category_cap`) so the existing env knob keeps working.
CATEGORY_DAILY_CAPS: Dict[str, Optional[int]] = {
    CATEGORY_WATCHLIST: 3,
    CATEGORY_EARNINGS: 4,
    CATEGORY_SMART_MONEY: 3,
    CATEGORY_PRICE_ALERT: 10,   # user-created; they asked for each one by name
    CATEGORY_APP: None,         # user-initiated, never capped
    # ONE per day. This is a DERIVED alert — no external event forces its timing, so
    # it has the weakest claim on attention of any kind in the registry. Everything
    # else here is triggered by something that actually happened at a moment.
    CATEGORY_MATCH: 1,
}


# ── Interruption levels ──────────────────────────────────────────────────────
# APNs `aps.interruption-level`. Drives whether iOS may batch the delivery, and
# whether it pierces a Focus mode.

LEVEL_PASSIVE = "passive"                # no wake; iOS may batch. Background signals.
LEVEL_ACTIVE = "active"                  # the default banner.
LEVEL_TIME_SENSITIVE = "time-sensitive"  # pierces Focus. REQUIRES the entitlement
                                         # com.apple.developer.usernotifications.time-sensitive
                                         # or iOS silently downgrades it to `active`.

_VALID_LEVELS = frozenset({LEVEL_PASSIVE, LEVEL_ACTIVE, LEVEL_TIME_SENSITIVE})


# ── Destinations inside the ticker screen ────────────────────────────────────
# These MIRROR the iOS enums and are spelled lowercase here; iOS lowercases the
# `rawValue` before matching, so "holders" pairs with `TickerDetailTab.holders`
# ("Holders"). Keep them in step with:
#   frontend/ios/ios/Models/TickerDetailModels.swift  — TickerDetailTab
#   frontend/ios/ios/Models/HoldersModels.swift       — RecentActivitiesTab / SmartMoneyTab
# `tests/test_notification_schema_parity.py` scans the Swift source and fails if a value
# here has no counterpart there, so a typo cannot ship as a silent land-on-Overview.

TAB_OVERVIEW = "overview"
TAB_NEWS = "news"
TAB_ANALYSIS = "analysis"
TAB_FINANCIALS = "financials"
TAB_HOLDERS = "holders"

_VALID_TABS = frozenset({TAB_OVERVIEW, TAB_NEWS, TAB_ANALYSIS, TAB_FINANCIALS, TAB_HOLDERS})

# Sub-tabs of Holders. Note the code name / UI label split the app already carries:
# `SmartMoneyTab.hedgeFunds` renders as "Institutions" (see project_hedge_fund_naming).
SECTION_INSIDERS = "insiders"
SECTION_INSTITUTIONS = "institutions"
SECTION_CONGRESS = "congress"

_VALID_SECTIONS = frozenset({SECTION_INSIDERS, SECTION_INSTITUTIONS, SECTION_CONGRESS})

# Which tab owns which sub-tabs. A section under the wrong tab is a routing bug that
# would land the user on the right screen with the wrong list selected.
_SECTIONS_BY_TAB: Dict[str, frozenset] = {TAB_HOLDERS: _VALID_SECTIONS}


@dataclass(frozen=True)
class NotificationKind:
    """One notification type, fully specified.

    `key` is the identity: it names the registry entry, ships as `data["kind"]` in the
    APNs payload, and is stored on the ledger row. The iOS `NotificationRouter`
    switches on `route_kind`, which is deliberately separate — several kinds can land
    on the same screen (every smart-money kind opens a ticker) without collapsing their
    preferences, caps or copy.
    """

    key: str
    # The user's own toggle for this exact type.
    preference_key: str
    # The GROUP toggle, ANDed with `preference_key`. `None` = no group.
    # Turning off "Smart Money" must silence all three of its children even though each
    # keeps its own key, so both are consulted and either one being off suppresses.
    master_preference_key: Optional[str]
    # What an ABSENT preference means. MIRRORS iOS `preferenceDefaults`. See module docstring.
    default_on: bool
    category: str
    interruption_level: str
    # APNs `aps.thread-id` — iOS groups the notification stack by this. Grouping by
    # category rather than by ticker keeps a busy day as one collapsible stack per
    # topic instead of N stacks of one.
    thread_id: str
    # Whether this kind may be held during the user's quiet hours.
    respects_quiet_hours: bool
    # What the iOS router switches on to pick a destination screen.
    route_kind: str
    # WHERE INSIDE that screen to land. Both are optional and both are pure hints.
    #
    # `route_kind` names a destination FAMILY (which screen); these name a location
    # within it. They are deliberately NOT folded into `route_kind` — doing that would
    # mean `ticker`, `ticker_holders`, `ticker_financials`, … one enum value per
    # combination, and `test_every_registered_kind_is_nameable_by_the_ios_router`
    # requires a matching literal in `NotificationRouter.swift` for every one of them.
    #
    # Only `TickerDetailTab` (the STOCK screen) has Financials and Holders; the crypto,
    # ETF, index and commodity screens top out at Overview/News/Analysis. A tab an asset
    # type does not have is ignored by iOS and lands on Overview, which is why these are
    # hints rather than promises.
    #
    # Human-readable label — used by the admin preview endpoint and in logs.
    label: str
    route_tab: Optional[str] = None
    # Sub-tab inside `route_tab` (Holders has Insiders / Institutions / Congress).
    route_section: Optional[str] = None

    def __post_init__(self) -> None:
        # Fail at IMPORT, not at send time. A typo'd level would otherwise surface as a
        # silently-downgraded notification months later, on a user's phone, with no log.
        if self.interruption_level not in _VALID_LEVELS:
            raise ValueError(
                f"NotificationKind {self.key!r}: unknown interruption_level "
                f"{self.interruption_level!r} (expected one of {sorted(_VALID_LEVELS)})"
            )
        if self.category not in CATEGORY_DAILY_CAPS:
            raise ValueError(
                f"NotificationKind {self.key!r}: category {self.category!r} has no "
                f"entry in CATEGORY_DAILY_CAPS — an uncapped category by accident is "
                f"how a user gets forty notifications in an afternoon"
            )
        if self.route_tab is not None and self.route_tab not in _VALID_TABS:
            raise ValueError(
                f"NotificationKind {self.key!r}: route_tab {self.route_tab!r} is not a "
                f"tab iOS knows (expected one of {sorted(_VALID_TABS)}). An unknown tab "
                f"is not an error on the client — it silently lands on Overview — so it "
                f"has to fail here instead."
            )
        if self.route_section is not None:
            if self.route_tab is None:
                raise ValueError(
                    f"NotificationKind {self.key!r}: route_section "
                    f"{self.route_section!r} with no route_tab. A sub-tab is meaningless "
                    f"without the tab that contains it."
                )
            allowed = _SECTIONS_BY_TAB.get(self.route_tab, frozenset())
            if self.route_section not in allowed:
                raise ValueError(
                    f"NotificationKind {self.key!r}: route_section "
                    f"{self.route_section!r} does not belong to tab {self.route_tab!r} "
                    f"(expected one of {sorted(allowed) or 'none — that tab has no sub-tabs'})"
                )
        if not self.preference_key.startswith("notify_"):
            # `user_settings_service._KNOWN_KEY_PREFIXES` only accepts `notify_*`, so a
            # key without the prefix is silently dropped by `sanitize_preferences` and
            # the toggle never persists.
            raise ValueError(
                f"NotificationKind {self.key!r}: preference_key {self.preference_key!r} "
                f"must start with 'notify_' or the settings sync will drop it"
            )


# ── Kind keys ────────────────────────────────────────────────────────────────

KIND_TICKER_MOVE = "ticker_move"
KIND_RESEARCH_COMPLETE = "research_complete"
KIND_RESEARCH_FAILED = "research_failed"
KIND_EARNINGS_UPCOMING = "earnings_upcoming"
KIND_EARNINGS_RESULT = "earnings_result"
KIND_INSIDER_TRADE = "insider_trade"
KIND_WHALE_13F = "whale_13f"
KIND_CONGRESS_TRADE = "congress_trade"
KIND_PRICE_ALERT = "price_alert"
KIND_PROFILE_MATCH = "profile_match"


NOTIFICATION_KINDS: Dict[str, NotificationKind] = {
    k.key: k
    for k in (
        # The one kind that already shipped. Its preference key, category cap (3/day)
        # and dedup shape are unchanged by the move onto this registry — that is
        # deliberate, so the refactor is not also a behaviour change.
        NotificationKind(
            key=KIND_TICKER_MOVE,
            preference_key="notify_watchlist_changes",
            master_preference_key=None,
            default_on=True,
            category=CATEGORY_WATCHLIST,
            interruption_level=LEVEL_ACTIVE,
            thread_id="watchlist",
            respects_quiet_hours=True,
            route_kind="ticker",
            label="Watchlist price moves",
        ),
        # Quiet hours are BYPASSED: the user pressed Generate and paid credits minutes
        # ago. Holding "your report is ready" until 07:00 is worse than not sending it,
        # and by then they have almost certainly opened the app and found it themselves.
        NotificationKind(
            key=KIND_RESEARCH_COMPLETE,
            preference_key="notify_research_complete",
            master_preference_key=None,
            default_on=True,
            category=CATEGORY_APP,
            interruption_level=LEVEL_ACTIVE,
            thread_id="app",
            respects_quiet_hours=False,
            route_kind="report",
            label="Report ready",
        ),
        # The mirror of the above, and the only event in the whole registry where the
        # user SPENT MONEY AND GOT SILENCE.
        #
        # A report that fails is stamped `status='failed'` and its 20 credits are
        # refunded — correctly, atomically, and completely invisibly. The client polls
        # for at most 3 minutes and then gives up, so a user who backgrounded the app
        # learns nothing: no report, no explanation, and a credit balance that quietly
        # went back up. Every other kind here announces something that happened TO the
        # market; this one announces the failure of something they asked for and paid
        # for, which is the strongest claim on attention in the file.
        #
        # `route_kind="ticker"`, NOT "report": there is no report to open. The ticker
        # screen is where they would start again.
        #
        # Uncapped (CATEGORY_APP) and quiet-hours-exempt for the same reason
        # `research_complete` is — this answers a question the user is actively holding
        # open, and holding it to 07:00 means they find the empty result themselves
        # first, which is the failure this exists to prevent.
        NotificationKind(
            key=KIND_RESEARCH_FAILED,
            preference_key="notify_research_failed",
            master_preference_key=None,
            default_on=True,
            category=CATEGORY_APP,
            interruption_level=LEVEL_ACTIVE,
            thread_id="app",
            respects_quiet_hours=False,
            route_kind="ticker",
            label="Report didn't finish",
        ),
        NotificationKind(
            key=KIND_EARNINGS_UPCOMING,
            preference_key="notify_earnings_upcoming",
            master_preference_key="notify_earnings_alerts",
            default_on=True,
            category=CATEGORY_EARNINGS,
            interruption_level=LEVEL_ACTIVE,
            thread_id="earnings",
            respects_quiet_hours=True,
            route_kind="ticker",
            label="Earnings coming up",
            route_tab=TAB_FINANCIALS,
        ),
        NotificationKind(
            key=KIND_EARNINGS_RESULT,
            preference_key="notify_earnings_surprises",
            master_preference_key="notify_earnings_alerts",
            default_on=True,
            category=CATEGORY_EARNINGS,
            interruption_level=LEVEL_ACTIVE,
            thread_id="earnings",
            respects_quiet_hours=True,
            route_kind="ticker",
            label="Earnings results",
            route_tab=TAB_FINANCIALS,
        ),
        # Smart-money kinds are PASSIVE and get apns-priority 5: a Form 4 filed this
        # evening is information, not an interruption. iOS is allowed to batch them for
        # battery, which is exactly right for something the user will read tomorrow.
        NotificationKind(
            key=KIND_INSIDER_TRADE,
            preference_key="notify_smart_money_insider",
            master_preference_key="notify_smart_money",
            default_on=True,
            category=CATEGORY_SMART_MONEY,
            interruption_level=LEVEL_PASSIVE,
            thread_id="smart_money",
            respects_quiet_hours=True,
            route_kind="ticker",
            label="Insider trades",
            route_tab=TAB_HOLDERS,
            route_section=SECTION_INSIDERS,
        ),
        # Ships OFF. 13F data is up to 45 days stale by the time it is public, so it is
        # the least time-critical signal in the app and the easiest to over-send (a
        # single filing touches dozens of positions). Mirrored in the iOS defaults map.
        NotificationKind(
            key=KIND_WHALE_13F,
            preference_key="notify_smart_money_institutional",
            master_preference_key="notify_smart_money",
            default_on=False,
            category=CATEGORY_SMART_MONEY,
            interruption_level=LEVEL_PASSIVE,
            thread_id="smart_money",
            respects_quiet_hours=True,
            route_kind="ticker",
            label="Institutional (13F) filings",
            route_tab=TAB_HOLDERS,
            route_section=SECTION_INSTITUTIONS,
        ),
        NotificationKind(
            key=KIND_CONGRESS_TRADE,
            preference_key="notify_smart_money_whale",
            master_preference_key="notify_smart_money",
            default_on=True,
            category=CATEGORY_SMART_MONEY,
            interruption_level=LEVEL_PASSIVE,
            thread_id="smart_money",
            respects_quiet_hours=True,
            route_kind="ticker",
            label="Congressional trades",
            route_tab=TAB_HOLDERS,
            route_section=SECTION_CONGRESS,
        ),
        # TIME-SENSITIVE and quiet-hours-exempt: the user typed this threshold in
        # themselves. A price alert that arrives after the move is over is worthless, so
        # it is the one kind that earns a Focus-piercing delivery.
        # ⚠️ Requires the com.apple.developer.usernotifications.time-sensitive
        # entitlement on the App ID AND in both .entitlements files. Without it iOS
        # downgrades to `active` silently — there is no error, no log, nothing.
        NotificationKind(
            key=KIND_PRICE_ALERT,
            preference_key="notify_price_alerts",
            master_preference_key=None,
            default_on=True,
            category=CATEGORY_PRICE_ALERT,
            interruption_level=LEVEL_TIME_SENSITIVE,
            thread_id="price_alert",
            respects_quiet_hours=False,
            route_kind="ticker",
            label="Price alerts",
        ),
        # Signals in a topic or feed the reader told us they follow.
        #
        # `default_on=False` is REQUIRED, not stylistic. Every other kind here is
        # triggered by an event about something the user explicitly tracks (a watchlist
        # ticker, a threshold they typed, a report they paid for). This one is derived
        # from stated preferences, so it must be opted INTO — a notification the user
        # never asked for, about a topic they merely said was interesting, is the fastest
        # way to lose the notification permission permanently.
        #
        # PASSIVE and quiet-hours-respecting for the same reason: nothing here is
        # time-critical. The congress feed lags filings by 30-45 days and 13F data is
        # quarterly, so waking someone for it would misrepresent its freshness.
        NotificationKind(
            key=KIND_PROFILE_MATCH,
            preference_key="notify_profile_topics",
            master_preference_key=None,
            default_on=False,
            category=CATEGORY_MATCH,
            interruption_level=LEVEL_PASSIVE,
            thread_id="match",
            respects_quiet_hours=True,
            route_kind="ticker",
            label="Topics you follow",
        ),
    )
}


# Preference keys the iOS app still SYNCS but that no kind owns yet.
#
# These are the leftovers of the original thirteen toggles: the "Market" group never
# got a sender, and is out of scope for this overhaul. They are declared here rather
# than quietly dropped for one specific reason — `SettingsSyncManager` still lists them
# in `boolKeys`, so a user's stored value still round-trips, and
# `preference_enabled` still needs to know what an ABSENT value means for each. Drop
# `notify_market_volatility` from this map and a user with no stored value silently
# becomes opted IN to something the UI declares as off, the moment anything sends it.
#
# Their toggles stay HIDDEN in the app: the invariant is that a visible toggle must
# have a sender behind it. Wiring a sender means moving the key OUT of this map and
# into a real NotificationKind above, in the same change as its UI row.
LEGACY_PREFERENCE_DEFAULTS: Dict[str, bool] = {
    "notify_market_alerts": True,       # group master for the unbuilt Market group
    "notify_market_macro": True,
    "notify_market_sector": True,
    "notify_market_volatility": False,  # ships OFF — mirrored in the iOS defaults map
}

# Boolean preferences that shape DELIVERY rather than selecting a category.
#
# They carry the `notify_` prefix because that prefix is what makes a key syncable at
# all (`user_settings_service._KNOWN_KEY_PREFIXES`), not because they name a
# notification type — no `NotificationKind` references them, and `preference_enabled`
# never consults them. `quiet_hours.resolve_window` reads this one directly.
#
# Declared here anyway so `preference_defaults()` stays the single answer to "what does
# an absent notify_* key mean?", which is the contract the iOS parity test pins. Leaving
# it out let the two sides disagree about a key that ships OFF, and an off-by-default
# key that the backend thinks is on is the exact failure this whole map exists to stop.
DELIVERY_PREFERENCE_DEFAULTS: Dict[str, bool] = {
    PREF_QUIET_ENABLED: False,
}


# Reverse index: preference_key → kind key.
#
# Built at import so an ambiguous mapping is a startup crash rather than a silent
# mis-route. `notify_watchers(preference_key=...)` — the signature the sweeper has used
# since push shipped — resolves its kind through this, so two kinds sharing a
# preference key would make that lookup arbitrary, and "arbitrary" here means a
# notification delivered under the wrong cap, thread and interruption level.
_KIND_BY_PREFERENCE_KEY: Dict[str, str] = {}
for _k in NOTIFICATION_KINDS.values():
    if _k.preference_key in _KIND_BY_PREFERENCE_KEY:
        raise ValueError(
            f"two notification kinds share preference_key {_k.preference_key!r}: "
            f"{_KIND_BY_PREFERENCE_KEY[_k.preference_key]!r} and {_k.key!r}. "
            f"kind_for_preference_key() would be arbitrary."
        )
    _KIND_BY_PREFERENCE_KEY[_k.preference_key] = _k.key
del _k


# ── Lookups ──────────────────────────────────────────────────────────────────


def kind_for_preference_key(preference_key: Optional[str]) -> Optional[str]:
    """The kind that owns this preference key, or ``None``.

    Returns ``None`` rather than raising: the caller (`notify_watchers`) has a
    documented default to fall back to, and a group MASTER key legitimately maps to no
    single kind.
    """
    if not preference_key:
        return None
    return _KIND_BY_PREFERENCE_KEY.get(preference_key)


def get_kind(key: str) -> NotificationKind:
    """Resolve a registry entry, or raise.

    Deliberately raises rather than returning a default. A sender naming a kind that
    does not exist is a programming error, and the safe-looking fallback ("just use
    ACTIVE and the watchlist cap") would send a real notification to real people under
    the wrong preference key — i.e. to users who had opted out.
    """
    try:
        return NOTIFICATION_KINDS[key]
    except KeyError:
        raise KeyError(
            f"unknown notification kind {key!r}; "
            f"registered: {sorted(NOTIFICATION_KINDS)}"
        ) from None


def ticker_route(
    kind_key: str,
    ticker: str,
    *,
    asset_type: str = "stock",
    whale_id: Optional[str] = None,
) -> Dict[str, str]:
    """Build the `route` payload for a notification that opens an asset screen.

    ONE builder, because the shape has drifted per-sender before and the failures are
    invisible on the server:

      * `ticker_move` emitted only `{kind, ticker}` — no `asset_type` — and the iOS
        router's documented `.stock` fallback then sent every BTC and ETH price alert to
        `TickerDetailView`, the STOCK screen, to render equity fundamentals for a coin.
        That fallback exists for unknown values, not as the primary path for the app's
        most common notification.
      * `profile_match` set `kind` where it meant `route`, which `push_dispatch_service`
        then overwrote. It worked only because the client defaults the family to
        "ticker".

    Both are the kind of bug that looks fine in every log and only shows up on a phone,
    so the shape is built here from the registry instead of hand-written per sender.
    `tests/test_notification_schema_parity.py` pins every sender to this function.
    """
    kind = get_kind(kind_key)
    route: Dict[str, str] = {
        "route": kind.route_kind,
        "ticker": ticker.upper(),
        "asset_type": (asset_type or "stock").strip().lower() or "stock",
    }
    # Omitted entirely when absent — an empty string would decode on iOS as a present-
    # but-blank tab and defeat the `nil` check that falls back to Overview.
    if kind.route_tab:
        route["tab"] = kind.route_tab
    if kind.route_section:
        route["section"] = kind.route_section
    # The investor this alert is ABOUT, when there is one.
    #
    # The whale senders have always held this id — it is in their dedup key and it selects
    # half their audience (`followers_of_whale`) — and then dropped it, so a 13F alert could
    # only ever offer "open the ticker". Someone who follows an investor and is told that
    # investor filed has one obvious next question, and the client could not answer it.
    #
    # An ADDITIVE key on an open JSONB dict: the iOS decoder is a tolerant
    # `[String: RouteScalar]` map that ignores names it does not know (`alert_id` has ridden
    # along unread for months), so no migration and no older-client breakage. Kept in THIS
    # builder rather than merged into a dict at the call site because a hand-written route is
    # what shipped `ticker_move` with no `asset_type` and sent every crypto alert to the
    # equity screen.
    if whale_id:
        route["whale_id"] = str(whale_id)
    return route


def preference_defaults() -> Dict[str, bool]:
    """Every preference key the registry knows, mapped to its absent-value default.

    Includes the group masters, which have no kind of their own but are ANDed into
    every child's decision — so `preference_enabled` needs a default for them too.
    Group masters default ON: turning a whole group off is an explicit act.

    Also includes `LEGACY_PREFERENCE_DEFAULTS`, because the client still syncs those
    keys and the backend must still know what an absent value means for each.
    """
    defaults: Dict[str, bool] = {
        **LEGACY_PREFERENCE_DEFAULTS,
        **DELIVERY_PREFERENCE_DEFAULTS,
    }
    for kind in NOTIFICATION_KINDS.values():
        defaults[kind.preference_key] = kind.default_on
        if kind.master_preference_key:
            defaults.setdefault(kind.master_preference_key, True)
    return defaults


def category_cap(category: str, watchlist_override: Optional[int] = None) -> Optional[int]:
    """Daily ceiling for a category. `None` = uncapped.

    `watchlist_override` threads `settings.PUSH_MAX_ALERTS_PER_USER_PER_DAY` through so
    the pre-existing env knob keeps working after the split into per-category caps —
    otherwise an operator who had tuned it would find it silently inert. A value <= 0
    means "uncapped", matching the `cap > 0` guard the dispatcher already used.
    """
    if category == CATEGORY_WATCHLIST and watchlist_override is not None:
        return watchlist_override if watchlist_override > 0 else None
    return CATEGORY_DAILY_CAPS.get(category)
