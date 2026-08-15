"""Paywall copy — what each plan gives you, derived from the gates that enforce it.

WHY THIS IS A SEPARATE MODULE FROM `entitlements.py`
----------------------------------------------------
`entitlements.py` owns the NUMBERS and states its own contract: "PURE DATA + PURE
FUNCTIONS. No I/O, no Supabase, no service imports — so it is trivially unit-testable and
safe to import from an endpoint, a service, **or a source-scan guard**." Two things break
if the marketing copy moves in there:

  1. A copy guard that greps for forbidden claims ("priority", "personaliz") would start
     matching the copy it now contains, and the exclusion needed to fix that weakens the
     guard into uselessness.
  2. Copy needs `REPORT_CREDIT_COST` / `CHAT_CREDIT_COST`, which live in `settings`. That
     would put the first `from app.config import settings` into a module whose entire
     value is having none.

The dependency runs ONE WAY — this module imports `entitlements`, never the reverse — so a
number can only ever flow gate → copy. That is the whole point: `entitlements.py`'s
docstring says a limit with no upgrade path "cannot tell the client WHICH plan lifts it,
so the UI has to hardcode a second copy of the ladder", and that the second failure mode is
"silent drift between the gate and the paywall". Before this module, the iOS paywall
rendered nothing but a credit count, and the two sentences that *did* describe value
("priority analysis", "priority AI") described a feature that does not exist — the report
semaphore is global and no code path reads `tier`. This module is what makes the paywall
render from the source that enforced.

⚠️ CALL THE ENTITLEMENT FUNCTIONS, NEVER INDEX THE DICTS.
`UPDATES_TICKER_LIMITS[tier]` raises KeyError on an unrecognised tier; `updates_ticker_limit`
normalises first and cannot raise. A paywall that 500s is strictly worse than one that is a
little vague, and `plan_credits` is a config table a human edits.

PURE. No I/O, no settings import — the per-action credit costs arrive as arguments.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.services import entitlements

# ── Row groups ───────────────────────────────────────────────────────────────────────
#
# "plan"   — a reason to pay. Its value CHANGES between tiers (a number, or included/not).
# "always" — free on every plan, rendered identically on all three cards under a muted
#            "Included on every plan" heading.
#
# The third group is not decoration; it is the only honest shape for the Investor Journey.
# Journey narration is free on every tier (`JOURNEY_AUDIO_UNLOCKED_TIERS`, which fails OPEN
# by construction). Listing it as a Pro unlock is a lie; listing it only on Free reads as
# "Pro loses it"; omitting it is a lie by silence. A group that renders the same everywhere
# is the only version that is true on all three cards at once.
GROUP_PLAN = "plan"
GROUP_ALWAYS = "always"

# ── Feature keys ─────────────────────────────────────────────────────────────────────
#
# Stable machine ids. iOS joins on these twice — to highlight the row the user was locked
# out of (`PaywallContext.featureKey`), and to match a bundled fallback row when the
# backend predates this module. Renaming one silently breaks both, so they are constants.
KEY_CREDITS = "credits"
KEY_UPDATES_TICKERS = "updates_tickers"
KEY_SIGNALS = "signals"
KEY_WHALE_TRACKING = "whale_tracking"
KEY_WHALE_DETAIL = "whale_detail"
KEY_LEARN_AUDIO = "learn_audio"
KEY_JOURNEY_AUDIO = "journey_audio"
KEY_LEARN_TEXT = "learn_text"
KEY_MARKET_DATA = "market_data"
KEY_TRACKING_TOOLS = "tracking_tools"
KEY_PDF_EXPORT = "pdf_export"

# ── Accents ──────────────────────────────────────────────────────────────────────────
#
# SEMANTIC KEYS, never a hex. iOS maps each to an audited TEXT-role `AppColors` token, so
# every glyph clears 4.5:1 in both appearances without this module knowing anything about
# either palette. Sending a colour would put an unaudited value on a surface the launch
# contrast audit cannot see, and `Color(themedHex:role:)` is for cases where the backend
# genuinely owns a brand hue (persona accents) — this vocabulary is ours and fixed.
ACCENT_CREDITS = "credits"
ACCENT_UPDATES = "updates"
ACCENT_SIGNALS = "signals"
ACCENT_WHALES = "whales"
ACCENT_LEARN = "learn"
ACCENT_NEUTRAL = "neutral"

# Pinned by tests on both sides: the backend may not emit an accent iOS cannot map.
ACCENTS = frozenset({
    ACCENT_CREDITS, ACCENT_UPDATES, ACCENT_SIGNALS,
    ACCENT_WHALES, ACCENT_LEARN, ACCENT_NEUTRAL,
})


def _row(
    key: str,
    title: str,
    detail: str,
    icon: str,
    accent: str,
    *,
    included: bool = True,
    group: str = GROUP_PLAN,
) -> Dict[str, Any]:
    return {
        "key": key,
        "title": title,
        "detail": detail,
        "icon": icon,
        "accent": accent,
        "included": included,
        "group": group,
    }


def _credits_row(credits: int, report_cost: int, chat_cost: int) -> Dict[str, Any]:
    """The monthly allowance, expressed in the two things it actually buys.

    ⚠️ ONE POOL. Reports and chat draw from the same balance, so the two figures are
    alternatives, not a sum — "50 credits" is ~2 reports **or** 50 replies, never both.
    The word "or" is the only thing standing between this row and a 2x overstatement of
    what the plan includes, which is why a test pins it rather than trusting the wording
    to survive the next copy edit.

    Both costs are guarded against 0: they are ints from `settings`, and a zero would be a
    ZeroDivisionError on the paywall — the one screen that must render for everyone.
    """
    clauses: List[str] = []
    if report_cost > 0:
        clauses.append(f"{credits // report_cost:,} AI research reports")
    if chat_cost > 0:
        clauses.append(f"{credits // chat_cost:,} Cay AI replies")

    if len(clauses) == 2:
        detail = (
            f"About {clauses[0]} or {clauses[1]} — one shared pool "
            "that resets at the start of each month."
        )
    elif clauses:
        detail = f"About {clauses[0]}. Resets at the start of each month."
    else:
        detail = "Resets at the start of each month."

    return _row(
        KEY_CREDITS,
        f"{credits:,} credits a month",
        detail,
        "creditcard.fill",
        ACCENT_CREDITS,
    )


def _updates_row(tier: str) -> Dict[str, Any]:
    """How many watchlist tickers get their own tab in Updates.

    A QUANTITY, not a lock — `included` stays True on Free because Free really does get a
    ticker tab plus the always-free Market feed. Rendering it as a strikethrough would
    misdescribe the gate.

    The Free copy states the alphabetical rule on purpose: `select_visible_tickers` is
    deterministic by design and its docstring says the trade-off "is why the rule is stated
    in the UI copy rather than left implicit" — adding a ticker that sorts earlier swaps
    which chip a Free user sees, and that is otherwise inexplicable.
    """
    limit = entitlements.updates_ticker_limit(tier)
    noun = "ticker" if limit == 1 else "tickers"
    if limit <= 1:
        detail = (
            "The Market feed is free on every plan. Your first ticker alphabetically "
            "gets its own news tab with an AI insight card."
        )
    else:
        detail = (
            "Each gets its own news tab with an AI insight card, alongside the "
            "always-free Market feed."
        )
    return _row(
        KEY_UPDATES_TICKERS,
        f"{limit} watchlist {noun} in Updates",
        detail,
        "chart.bar.doc.horizontal",
        ACCENT_UPDATES,
    )


def _signals_row(tier: str) -> Dict[str, Any]:
    """App-Exclusive Signals: the ticker is gated, the card is not.

    The locked copy names what Free KEEPS, not only what it lacks. That is not softening —
    it is the App Store Guideline 5.1.1(v) posture `entitlements.py` documents ("the screen
    still demonstrates what it does"), finally stated on the paywall instead of only in a
    backend comment.
    """
    unlocked = entitlements.signals_unlocked(tier)
    detail = (
        "See the exact ticker behind every Congress, institutional and earnings signal, "
        "plus the investors driving it."
        if unlocked else
        "Free shows every App-Exclusive Signal and how many investors are moving — "
        "the ticker symbols stay hidden."
    )
    return _row(
        KEY_SIGNALS,
        "Signal tickers",
        detail,
        "antenna.radiowaves.left.and.right",
        ACCENT_SIGNALS,
        included=unlocked,
    )


def _whale_tracking_row(tier: str) -> Dict[str, Any]:
    """How many investors an account may track. ``None`` means unlimited.

    Branching on None is mandatory, not stylistic: `whale_follow_limit` returns it for Max
    and formatting it would print "Track None investors" on the most expensive plan.

    ⚠️ NO ROSTER COUNT, and NO NAME. The roster size lives in `whale_registry.json`, so
    quoting it here would create a brand-new drift source while removing another. And
    `FREE_TIER_WHALE_NAME` is a real person: `documents/legal/app-store-listing.md` rule #1
    bans real investor names in screenshots, and the paywall is the screen most likely to
    end up in one.
    """
    limit = entitlements.whale_follow_limit(tier)
    if limit is None:
        title = "Track unlimited investors"
        detail = "Follow the whole roster and keep every one of them on your Tracking tab."
    else:
        noun = "investor" if limit == 1 else "investors"
        title = f"Track {limit} {noun}"
        detail = (
            "One preset investor to start. Browsing every investor is free on every plan."
            if limit <= 1 else
            "Pick any from the full roster. Browsing is free on every plan."
        )
    return _row(KEY_WHALE_TRACKING, title, detail, "person.2.fill", ACCENT_WHALES)


def _whale_detail_row(tier: str) -> Dict[str, Any]:
    unlocked = entitlements.whale_detail_unlocked(tier)
    detail = (
        "Current Picks, Recent Trades and the AI sentiment summary on every investor "
        "you open."
        if unlocked else
        "Free shows each investor's profile, risk badge and sector exposure. Current "
        "Picks, Recent Trades and the AI sentiment summary are paid."
    )
    return _row(
        KEY_WHALE_DETAIL,
        "Holdings, trades & AI sentiment",
        detail,
        "chart.pie.fill",
        ACCENT_WHALES,
        included=unlocked,
    )


def _learn_audio_row(tier: str) -> Dict[str, Any]:
    """Narration for MONEY MOVES and BOOKS only.

    ⚠️ Never widen this title to "narration" or "audio lessons". Investor Journey narration
    is free on every tier and is emitted separately in the "always" group; a blanket claim
    here would sell something the reader already has.
    """
    unlocked = entitlements.learn_audio_unlocked(tier)
    detail = (
        "Listen to Money Moves and the book library with synced read-along highlighting."
        if unlocked else
        "Every article and book stays free to read. Narration with read-along "
        "highlighting is paid."
    )
    return _row(
        KEY_LEARN_AUDIO,
        "Money Moves & book narration",
        detail,
        "headphones",
        ACCENT_LEARN,
        included=unlocked,
    )


def _always_rows(tier: str) -> List[Dict[str, Any]]:
    """What every plan includes, Free among them.

    This block is load-bearing for two separate reasons:
      • Honesty about the Journey (see GROUP_ALWAYS).
      • It is the paywall's own statement of the 5.1.1(v) posture the whole app is built
        on — "significant" parts of Caydex are not behind a login or a plan, and a reader
        deciding whether to pay deserves to know exactly where the line is rather than
        inferring that everything unlisted is paid.
    """
    rows = [
        _row(
            KEY_MARKET_DATA,
            "Market data & company research",
            "Stocks, ETFs, crypto, indices and commodities — detail screens, charts, "
            "news and search, on every plan.",
            "chart.xyaxis.line",
            ACCENT_NEUTRAL,
            group=GROUP_ALWAYS,
        ),
        _row(
            KEY_TRACKING_TOOLS,
            "Watchlists, portfolios & price alerts",
            "Unlimited watchlists and portfolios with a diversification score, plus "
            "price alerts. No plan required.",
            "list.bullet.rectangle.portrait",
            ACCENT_NEUTRAL,
            group=GROUP_ALWAYS,
        ),
        _row(
            KEY_LEARN_TEXT,
            "The full Wiser library",
            "Every lesson, article and book guide is free to read, with progress and "
            "bookmarks, on every plan.",
            "book.fill",
            ACCENT_NEUTRAL,
            group=GROUP_ALWAYS,
        ),
    ]

    # Gated on the real function rather than appended unconditionally, so re-gating the
    # Journey later stays the one-line edit `entitlements.py` promises it is.
    if entitlements.journey_audio_unlocked(tier):
        rows.append(_row(
            KEY_JOURNEY_AUDIO,
            "Investor Journey narration",
            "Every Journey lesson reads itself aloud with read-along highlighting — "
            "on every plan, including Free.",
            "waveform",
            ACCENT_NEUTRAL,
            group=GROUP_ALWAYS,
        ))

    rows.append(_row(
        KEY_PDF_EXPORT,
        "PDF export",
        "Export any AI research report as a multi-page PDF. No plan required.",
        "square.and.arrow.up",
        ACCENT_NEUTRAL,
        group=GROUP_ALWAYS,
    ))
    return rows


def features_for_tier(
    tier: Optional[str],
    *,
    monthly_credits: int,
    report_cost: int,
    chat_cost: int,
) -> List[Dict[str, Any]]:
    """Pure: the paywall rows for one plan, with every number read from `entitlements`.

    Returns ``[]`` for a tier this build does not recognise. Deliberately NOT Free's rows:
    `normalize_tier` would happily fold "platinum" to "free", and rendering Free's copy
    under a "Platinum" header is a worse failure than rendering none — it would state
    limits the server is not enforcing for that plan. An empty list makes the client fall
    back to its bundled table, which is keyed by a tier it *does* know.
    """
    key = entitlements.normalize_tier(tier)
    if key != tier:
        return []

    credits = max(0, int(monthly_credits or 0))
    report_cost = max(0, int(report_cost or 0))
    chat_cost = max(0, int(chat_cost or 0))

    return [
        _credits_row(credits, report_cost, chat_cost),
        _updates_row(key),
        _signals_row(key),
        _whale_tracking_row(key),
        _whale_detail_row(key),
        _learn_audio_row(key),
        *_always_rows(key),
    ]
