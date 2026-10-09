"""Ask Cay AI's ownership tool, `check_ownership_filings`: who holds a stock, from filings.

WHY THIS EXISTS (TestFlight 1.0 (11), 2026-10-05). In an Updates chat on CRWV, after a
director's Form 4 sale, "how many shares does he own now?" got "Caydex does not have
information on Director Brian Venturo's current total ownership". The data was there — the
very Form 4 line behind that sale reports the balance he held after it — but no chat tool
or grounding carried it: a STOCK chat saw only the Overview's aggregate "Insiders &
Ownership" snapshot (insider %, institutional %, 12-month net flow), so the model was
describing its environment accurately.

WHAT IT READS — nothing new is fetched here. Only the Holders build
(`holders_service.get_holders_with_status`): the Holders tab's own rows under the shared
insider rules (issuer CIK, equity lines, Form 4/A supersession, fail-closed fetch), its
5-minute and 24-hour tiers and its in-flight dedup. From it:
  * `ownership_detail.insider_holdings` — each insider's balance after their latest Form 4
    transactions (`_insider_holdings`, which explains why that is not "the last row");
  * `shareholder_breakdown` — the % held by institutions (13F) and insiders, and the
    largest institutional holders, with `ownership_detail.institutions_quarter` naming the
    13F quarter they describe.

Degradation is SAID, never zeroed: a failed or unattributable insider read is "could not be
loaded", not "insiders own nothing"; an unknown institutional figure is "unknown", not 0%.

The result is compact TEXT per holding ("Class A Common Stock held directly: 302,526 shares
as of 2026-09-30"): a tool result is cut at `GEMINI_TOOL_RESULT_MAX_CHARS`, and one dict per
balance overflowed it at a dozen insiders. When it still does not fit, the least recent
filers are dropped BY NAME (`not_shown`), so the model can say a person's figures were not
loaded rather than that they hold nothing.

EXTENDED (2026-10-08), still from that one Holders build — no new upstream call is needed
for any of it:
  * `insider_activity` — open-market buying and selling over 3, 6 and 12 months (the tab's
    chart bars by month label, its activity list and its 12-month summary);
  * institutional change per largest holder, the quarter's biggest changes and the flow;
  * `float` — float, shares outstanding and free float from the build's ONE shares-float
    read (`ownership_detail.float_*`); the insiders' percentage is derived from that same
    figure, never from a second read;
  * `short_interest` — the Key Stats rule (`stock_overview_service.short_percent_of_float`)
    over the exchange-reported short interest, read through its own cache and waited on for
    at most `_SHORT_INTEREST_WAIT` seconds; otherwise omitted with a note;
  * `congress` — ONLY through `congress_holders_unlocked(user_tier)` / `redact_congress`:
    None, free and unknown tiers get a locked note and no member's name or trade anywhere;
    Pro wording is "disclosed purchase/sale", with ranges and dates;
  * a foreign-issuer note (such issuers may be exempt from Form 4), from the company-profile
    cache, when no Form 4 filer was found.
Trades are worded from the shared code table (`_insider_common.plain_transaction_phrase`): an
F code is tax withholding, not a sale; an S-Sale's dollar value is proceeds.

No vendor is named anywhere in the result (IDENTITY_RULE): the sources are SEC filings.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from app.services._insider_common import insider_window_cutoff, plain_transaction_phrase
from app.services.asset_class import detect_asset_class

logger = logging.getLogger(__name__)

# Below the tool-result cap (`GEMINI_TOOL_RESULT_MAX_CHARS`, 8000 by default) so the
# structural pruner never cuts this result blind.
_BUDGET_MARGIN = 600
# People whose newest-day trades stay in the result when it must be shortened.
_KEEP_TRADES_FOR = 3

_HOW_TO_READ = (
    "Ownership comes from SEC filings, not a live register. Insiders (officers, directors and "
    "10% owners) report each trade on Form 4 within two business days; each holding below is "
    "the number of shares the person reported holding right AFTER the transaction on its "
    "'as of' date, so say 'as of <date>' and never present it as a live or current count. "
    "filings_checked_at is when Caydex last read the filings: a trade filed after it is not "
    "included, so if the user mentions a newer trade, say these figures predate it; when "
    "freshness is present, say what it says. "
    "Shares held directly and shares held indirectly (through a trust, family member or "
    "entity, one entry per holding) are separate, and so is a directly held lot listed on its "
    "own; an entry marked 'earlier figure' was last reported before that person's newest "
    "transaction on that line and may be a separate holding or an older figure for one "
    "listed, so never add holdings up into a total. Stock options and restricted stock units "
    "are not included. Only holdings with a reported transaction since covers_filings_since "
    "are visible: if a person is not listed anywhere, say no filing of theirs was found in "
    "that period; if they are named under not_shown or older_filers_not_shown, say their "
    "figures were not loaded in this answer — never that they own nothing. Institutional "
    "figures come from quarterly 13F reports, filed up to 45 days after the quarter ends; "
    "percentages are of shares outstanding. "
    "A sale's dollar figure is its PROCEEDS, never a tax; withholding for taxes (code F) is "
    "not a sale, and filings report no tax amount. People are listed even with no trade in "
    "the insider_activity windows. An unavailable block means not loaded, never zero."
)

# Short interest is read through its own cache (memory → its table → one exchange-data
# query); the tool waits this long for it and no longer — a cold read finishes in the
# background and warms the cache the Overview's Key Stats read.
_SHORT_INTEREST_WAIT = 2.5
# The company-profile cache read behind the foreign-issuer note (a DB read, off the loop).
_PROFILE_WAIT = 2.0
# Strong references for side reads left running past their wait (a bare task is only weakly
# held by the loop and can be collected mid-flight).
_side_tasks: set = set()
_US_COUNTRIES = frozenset({"US", "USA", "UNITED STATES", "UNITED STATES OF AMERICA"})
_NAME_MAX = 80

_ACTIVITY_BASIS = (
    "open-market purchases and sales only (Form 4 codes P and pure S; grants, exercises, "
    "gifts, tax withholding and conversions excluded); the 3- and 6-month windows are calendar "
    "months, the newest partial; dollars are shares times each trade's price (a sale's are "
    "proceeds)."
)
_ACTIVITY_UNAVAILABLE = {
    "available": False,
    "note": ("Insider buying and selling totals could not be loaded right now. Say so; never "
             "treat them as zero or as no activity."),
}
_ACTIVITY_OLDER_READ = (
    "These totals come from an earlier read of the filings (read_at) than the insiders list: "
    "they may not include the newest filing shown there. If a trade listed under insiders is "
    "missing from these windows, say the totals predate it — never that it did not happen."
)
_CONGRESS_LOCKED_NOTE = (
    "Congressional trading disclosures are part of Caydex Pro. If asked, say they are "
    "available on Caydex Pro; never name a member of Congress or describe any of their trades."
)
# The Holders build's congressional sources (`holders_service._build_holders`, each critical
# with an EMPTY default) → the chamber a failure leaves out.
_CONGRESS_SOURCES = {
    "Senate latest": "Senate", "Senate disclosure": "Senate",
    "House latest": "House", "House disclosure": "House",
}
_CONGRESS_UNAVAILABLE_NOTE = (
    "Congressional disclosures could not be loaded right now. Say so; never say there are none."
)
_CONGRESS_BASIS = (
    "STOCK Act periodic transaction reports: amounts only as ranges, filed up to 45 days after "
    "the trade, possibly a spouse's or dependent's trade — say 'disclosed a purchase/sale' "
    "with the range, never 'bought'/'sold' or an exact amount."
)
_BENEFICIAL_OWNERS_NOTE = (
    "Not in this answer: 5%+ holders reporting on Schedule 13D/13G instead of Form 4 (often "
    "founders) — if asked, say this answer does not include them, never that there are none."
)
_FOREIGN_ISSUER_NOTE = (
    "This issuer is based outside the US or trades as an ADR: foreign private issuers' "
    "insiders may be exempt from Form 4, so the absence of filings is not the absence of "
    "insider holdings — say so."
)

# Freshness (finding 3, 2026-10-07): the Holders row is cached up to 24 h and nothing
# invalidates it when a Form 4 lands. A build older than this is checked against the feed's
# newest page (one call) before it is trusted, at most once per ticker per probe interval.
_PROBE_AFTER_SECONDS = 30 * 60
_PROBE_EVERY_SECONDS = 5 * 60
_PROBE_MEMORY = 2048
# sym -> (monotonic time of the last probe, its answer: True newer filing / False / None
# unknown, the `built_at` of the build it was asked about — an answer is replayed only for
# that same build).
_last_probe: Dict[str, Tuple[float, Optional[bool], Optional[str]]] = {}
# The insider read of a forced rebuild, per ticker (review F4, 2026-10-07). Holders keeps a
# forced build that degraded in ANY source — the Senate feed, say — out of both its tiers, so
# the next question went back to the older build and called the newer filing "could not be
# loaded" a minute after showing it, and every question after the probe window rebuilt the
# whole payload again. Only the chat's slice is kept (`OwnershipDetailSchema`), while it is
# younger than the 24h row, and only while nothing newer has been built.
_FRESH_MEMORY = 64
_FRESH_SECONDS = 24 * 3600
_fresh_reads: Dict[str, Any] = {}
# The insider SIDE of the same forced rebuild (its chart and activity list — `_InsiderView`),
# per ticker, so `insider_activity` describes the very read `insiders` came from: a question
# asked right after a new Form 4 used to list the sale under the person while the 3-month
# line said no sale was reported (the stale build's bars). Kept and dropped with
# `_fresh_reads[sym]`; matched to it by identity.
_fresh_views: Dict[str, "_InsiderView"] = {}
# One probe (and its rebuild) per ticker at a time: concurrent questions wait for it (F4).
_probe_inflight: Dict[str, asyncio.Future] = {}
_EPOCH = datetime.min.replace(tzinfo=timezone.utc)

_NEWER_NOT_LOADED = (
    "a newer insider filing exists but could not be loaded: the figures below predate it — "
    "say so, and do not present them as the latest."
)
_UNCHECKED = (
    "could not check for newer filings: the figures below are as of filings_checked_at, and "
    "a trade filed since then would not be included."
)

_INSIDERS_UNAVAILABLE = {
    "available": False,
    "note": (
        "Insider filings could not be loaded right now. Say so plainly; never say the "
        "insiders own nothing or that there are no filings."
    ),
}

# How the institutional percentage was arrived at (`holders_service._resolve_institutional_pct`).
_INSTITUTION_BASIS = {
    "recomputed": "13F shares divided by shares outstanding",
    "last_quarter": "from the previous quarter's 13F filings (the latest total was not usable)",
    "top_holders_sum": "a LOWER BOUND: the sum of the largest holders only",
}


def _shares_text(value: Optional[float]) -> str:
    if value is None:
        return "an unreported number of"
    number = float(value)
    if number.is_integer():
        return f"{int(number):,}"
    return f"{number:,.4f}".rstrip("0").rstrip(".")


def _holding_text(h: Any) -> str:
    # The filing date rides on the person's `latest_transaction` line; one per holding was
    # ~20 characters × every balance against the tool-result cap.
    held = "directly" if h.held == "direct" else "indirectly"
    if h.shares is not None:
        amount = f"{_shares_text(h.shares)} shares"
    else:
        options = " or ".join(_shares_text(v) for v in (h.possible_shares or []))
        amount = (f"one of {options} shares (the filings do not show which line came last "
                  f"that day)") if options else "an unreported number of shares"
    text = f"{h.security} held {held}: {amount} as of {h.as_of}"
    lines = getattr(h, "same_balance_lines", None) or 0
    if lines > 1:
        # One entry the rows cannot prove is ONE holding (review F2): say so, never count it.
        text += (f" ({lines} filing lines that day ended on this same balance: it may be one "
                 f"holding or up to {lines} holdings of this size)")
    if h.reported_earlier:
        text += " [earlier figure]"
    if h.changed_after:
        # The same day counts: a day's lines have no known order, so the unusable one may
        # have been the last trade (finding 2).
        text += (f" — a transaction on {h.changed_after} reported no usable balance, so this "
                 f"may not be the latest figure")
    return text


def _trade_text(t: Any) -> str:
    """The filing's code, then what it means in plain words (`plain_transaction_phrase`, the
    Holders tab's own code table): "S-Sale: sold 65,616 shares in the open market at an
    average $87.69 (about $5.75 million in sale proceeds)"; "F-InKind: had … withheld … (not
    a sale …)"."""
    code = t.transaction_type.strip()[:20] if isinstance(t.transaction_type, str) else ""
    phrase = plain_transaction_phrase(code, t.shares, t.average_price, acquired=t.acquired)
    return f"{code}: {phrase}" if code else phrase


def _person_view(p: Any) -> Dict[str, Any]:
    view: Dict[str, Any] = {"name": p.name, "role": p.role}
    when = p.latest_transaction_date
    if p.latest_filing_date and p.latest_filing_date != when:
        when += f" (filed {p.latest_filing_date})"
    trades = [_trade_text(t) for t in p.latest_trades]
    view["latest_transaction"] = when + (": " + "; ".join(trades) if trades else "")
    view["holdings"] = [_holding_text(h) for h in p.holdings] or [
        "no holding balance could be read from this person's filings in the period"
    ]
    if p.holdings_not_shown:
        view["more_holdings_not_shown"] = p.holdings_not_shown
    return view


def _insiders_block(detail: Any) -> Dict[str, Any]:
    """`detail` is the `OwnershipDetailSchema` answered from (see `_freshen`)."""
    holdings = getattr(detail, "insider_holdings", None) if detail is not None else None
    if holdings is None:
        return dict(_INSIDERS_UNAVAILABLE)
    block: Dict[str, Any] = {"available": True}
    if not holdings.complete:
        block["complete"] = False
        block["note"] = (
            "Some older filings could not be loaded: the figures shown are each listed "
            "person's newest, but people may be missing from the list."
        )
    if holdings.covers_filings_since:
        block["covers_filings_since"] = holdings.covers_filings_since
    block["people"] = [_person_view(p) for p in holdings.insiders]
    # Everyone left out is NAMED (finding 7): a question about one of them must read "not
    # loaded", never "no filing of theirs was found". `_fit` adds the people it drops in front.
    cut = list(holdings.insiders_not_shown_names or [])
    unnamed = max(0, holdings.insiders_not_shown - len(cut))
    if cut:
        block["not_shown"] = cut
    if unnamed:
        block["not_shown_unnamed_count"] = unnamed
    if holdings.inactive_not_shown:
        names = list(holdings.inactive_not_shown_names or [])
        block["older_filers_not_shown"] = (
            f"{holdings.inactive_not_shown} past filer(s) whose last filing is over two years "
            f"older than the newest one — most likely no longer insiders"
            + (f": {', '.join(names)}" if names else "")
        )
    if not block["people"]:
        block["note"] = (
            "No insider reported a transaction in the period covered. That is not the same "
            "as insiders owning nothing — say no recent filing was found."
        )
    return block


# ── 2026-10-08 blocks: all read from the Holders build already in hand ───────────────────

def _finite(value: Any) -> Optional[float]:
    """A finite number, else None — None, NaN/±inf, bools and non-numbers are not figures."""
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _count_text(shares: float) -> str:
    """A share count: whole shares with separators (float noise from the bars' millions
    rounded away), a real fraction kept — a 0.5-share DRIP lot never reads as "0 shares"."""
    rounded = round(shares)
    if abs(shares - rounded) < 1e-4:
        text = f"{int(rounded):,}"
    else:
        text = f"{shares:,.4f}".rstrip("0").rstrip(".")
    return f"{text} share" if text == "1" else f"{text} shares"


def _usd_text(value: float) -> str:
    sign = "-" if value < 0 else ""
    v = abs(value)
    if v >= 1e9:
        return f"{sign}${v / 1e9:,.2f} billion"
    if v >= 1e6:
        return f"{sign}${v / 1e6:,.2f} million"
    return f"{sign}${v:,.0f}"


def _big_count_text(value: float) -> str:
    if value >= 1e9:
        return f"{value / 1e9:,.2f} billion shares"
    if value >= 1e6:
        return f"{value / 1e6:,.2f} million shares"
    return _count_text(value)


def _month_key(label: Any) -> Optional[Tuple[int, int]]:
    """``"MM/YYYY"`` (the chart's bucket label) → (year, month); None for anything else."""
    if not isinstance(label, str):
        return None
    parts = label.strip().split("/")
    if len(parts) != 2 or not (parts[0].isdigit() and parts[1].isdigit()):
        return None
    month, year = int(parts[0]), int(parts[1])
    if not (1 <= month <= 12 and 1900 <= year <= 9999):
        return None
    return year, month


def _months_ending(anchor: Tuple[int, int], count: int) -> List[Tuple[int, int]]:
    year, month = anchor
    out = []
    for _ in range(count):
        out.append((year, month))
        month -= 1
        if month == 0:
            month, year = 12, year - 1
    return out


def _date_key(day: Any) -> Optional[Tuple[int, int]]:
    if not isinstance(day, str) or len(day) < 7:
        return None
    return _month_key(f"{day[5:7]}/{day[:4]}")


def _window_trades(activities: List[Any], months: set) -> Dict[str, Any]:
    """Open-market trades of `activities` (the tab's list) whose date falls in `months`."""
    bought_usd = sold_usd = 0.0
    buyers: set = set()
    sellers: set = set()
    bought_sh = sold_sh = 0.0
    unpriced = 0
    for act in activities:
        kind = getattr(act, "transaction_type", "")
        if kind not in ("Informative Buy", "Informative Sell"):
            continue
        if _date_key(getattr(act, "date", None)) not in months:
            continue
        shares = _finite(getattr(act, "change_in_millions", None))
        if shares is None:
            continue
        shares = abs(shares) * 1e6
        price = _finite(getattr(act, "price_at_transaction", None))
        value = shares * price if price is not None and price > 0 else None
        if value is None:
            unpriced += 1
        name = str(getattr(act, "name", "") or "").strip().lower()
        if kind == "Informative Buy":
            bought_sh += shares
            bought_usd += value or 0.0
            buyers.add(name)
        else:
            sold_sh += shares
            sold_usd += value or 0.0
            sellers.add(name)
    return {"bought_sh": bought_sh, "sold_sh": sold_sh, "bought_usd": bought_usd,
            "sold_usd": sold_usd, "buyers": len(buyers), "sellers": len(sellers),
            "unpriced": unpriced}


def _window_text(span: str, bought_sh: float, sold_sh: float, bought_usd: Optional[float],
                 sold_usd: Optional[float], buyers: Optional[int], sellers: Optional[int],
                 unpriced: int = 0) -> str:
    if bought_sh <= 0 and sold_sh <= 0:
        return f"{span}: no open-market purchase or sale was reported"
    parts = []
    for verb, shares, usd in (("bought", bought_sh, bought_usd), ("sold", sold_sh, sold_usd)):
        text = f"{verb} {_count_text(shares)}"
        if shares > 0 and usd:
            text += f" (about {_usd_text(usd)}{' in proceeds' if verb == 'sold' else ''})"
        parts.append(text)
    text = f"{span}: insiders " + ", ".join(parts)
    if buyers is not None and sellers is not None:
        text += f"; {buyers} buyer(s), {sellers} seller(s)"
    if unpriced:
        # An unpriced trade adds $0 to its side, so a net DOLLAR verdict could have either sign —
        # "net buying" beside an unpriced 500,000-share sale (final review 2026-10-09). The per-side
        # dollar figures stay (the note says what they leave out); the direction is never stated.
        text += (f"; net direction in dollars is not stated: {unpriced} trade(s) reported no "
                 "price and are not in the dollar figures")
    elif bought_usd is not None and sold_usd is not None and (bought_usd or sold_usd):
        net = bought_usd - sold_usd
        text += f"; net about {_usd_text(net)} ({'net buying' if net > 0 else 'net selling' if net < 0 else 'even'})"
    return text


def _built_day(resp: Any) -> Optional[datetime]:
    return _built_at(getattr(resp, "ownership_detail", None))


def _unpriced_since(activities: List[Any], cutoff: str) -> int:
    """Open-market trades of the activity list dated on or after `cutoff` (``YYYY-MM-DD``)
    that reported no price — the ones a dollar total built from priced trades leaves out."""
    count = 0
    for act in activities:
        if getattr(act, "transaction_type", "") not in ("Informative Buy", "Informative Sell"):
            continue
        day = getattr(act, "date", None)
        if not isinstance(day, str) or day[:10] < cutoff:
            continue
        if _finite(getattr(act, "change_in_millions", None)) is None:
            continue
        price = _finite(getattr(act, "price_at_transaction", None))
        if price is None or price <= 0:
            count += 1
    return count


def _insider_activity_block(resp: Any, *, note: Optional[str] = None) -> Dict[str, Any]:
    """Open-market buying and selling over 3, 6 and 12 months, from ONE Holders build (`resp`,
    or the insider side of a forced rebuild — `_InsiderView`): share totals from the chart's
    monthly bars (by label — any order, missing months are simply absent), dollars and
    buyer/seller counts from the tab's activity list for the same months, and the 12 months
    from the tab's own summary card (trailing 365 days), with the open-market trades that
    reported no price — which its dollars leave out — counted from the list. `note` (with
    the read's time) is set when this build is OLDER than the read the insiders list came
    from (`_activity_source`)."""
    smart = getattr(resp, "insider_data", None)
    recent = getattr(getattr(resp, "recent_activities", None), "insider_activities", None)
    if smart is None or recent is None or getattr(smart, "unavailable", None) or getattr(recent, "unavailable", None):
        return dict(_ACTIVITY_UNAVAILABLE)
    activities = list(getattr(recent, "activities", None) or [])
    bars: Dict[Tuple[int, int], Tuple[float, float]] = {}
    for point in getattr(smart, "flow_data", None) or []:
        key = _month_key(getattr(point, "month", None))
        if key is None:
            continue
        buy = _finite(getattr(point, "buy_volume", None)) or 0.0
        sell = _finite(getattr(point, "sell_volume", None)) or 0.0
        prev = bars.get(key, (0.0, 0.0))
        bars[key] = (prev[0] + max(buy, 0.0), prev[1] + max(sell, 0.0))
    built = _built_day(resp)
    if built is not None:
        anchor = (built.year, built.month)
    elif bars:
        anchor = max(bars)
    else:
        now = datetime.now(timezone.utc)
        anchor = (now.year, now.month)
    block: Dict[str, Any] = {"available": True, "basis": _ACTIVITY_BASIS}
    if note:
        block["read_at"] = (built.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
                            if built is not None else "unknown")
        block["note"] = note
    for count in (3, 6):
        months = _months_ending(anchor, count)
        wanted = set(months)
        trades = _window_trades(activities, wanted)
        if bars:
            bought_sh = sum(bars[m][0] for m in months if m in bars) * 1e6
            sold_sh = sum(bars[m][1] for m in months if m in bars) * 1e6
        else:
            bought_sh, sold_sh = trades["bought_sh"], trades["sold_sh"]
        oldest, newest = months[-1], months[0]
        span = f"last {count} months ({oldest[1]:02d}/{oldest[0]}-{newest[1]:02d}/{newest[0]})"
        block[f"last_{count}_months"] = _window_text(
            span, bought_sh, sold_sh, trades["bought_usd"], trades["sold_usd"],
            trades["buyers"], trades["sellers"], trades["unpriced"],
        )
    summary = getattr(recent, "summary", None)
    card = getattr(smart, "summary", None)
    bought_sh = (_finite(getattr(summary, "informative_buys_in_millions", None)) or 0.0) * 1e6
    sold_sh = (_finite(getattr(summary, "informative_sells_in_millions", None)) or 0.0) * 1e6
    bought_usd = _finite(getattr(card, "total_buy_usd_millions", None))
    sold_usd = _finite(getattr(card, "total_sell_usd_millions", None))
    span = "last 12 months (trailing 365 days" + (
        f" to {built.date().isoformat()})" if built is not None else ")")
    # The card sums priced trades only (`holders_service`: a trade with no price adds 0);
    # the trades it left out are counted over the card's own window, as the 3/6 lines do.
    cutoff = insider_window_cutoff(built)
    block["last_12_months"] = _window_text(
        span, max(bought_sh, 0.0), max(sold_sh, 0.0),
        bought_usd * 1e6 if bought_usd is not None else None,
        sold_usd * 1e6 if sold_usd is not None else None,
        _count_or_none(getattr(summary, "num_buyers", None)),
        _count_or_none(getattr(summary, "num_sellers", None)),
        # Only beside dollar figures (an older build's card carries none).
        _unpriced_since(activities, cutoff)
        if bought_usd is not None or sold_usd is not None else 0,
    )
    return block


def _count_or_none(value: Any) -> Optional[int]:
    number = _finite(value)
    return int(number) if number is not None and number >= 0 else None


def _float_block(resp: Any) -> Dict[str, Any]:
    """Float, shares outstanding and free float from the build's ONE shares-float read."""
    detail = getattr(resp, "ownership_detail", None)
    float_shares = _finite(getattr(detail, "float_shares", None))
    outstanding = _finite(getattr(detail, "outstanding_shares", None))
    free_pct = _finite(getattr(detail, "free_float_percent", None))
    float_shares = float_shares if float_shares is not None and float_shares > 0 else None
    outstanding = outstanding if outstanding is not None and outstanding > 0 else None
    free_pct = free_pct if free_pct is not None and 0 < free_pct <= 100 else None
    if float_shares is None and outstanding is None and free_pct is None:
        return {"available": False,
                "note": "The float and shares outstanding could not be loaded right now; do not state them."}
    as_of = getattr(detail, "float_as_of", None)
    block: Dict[str, Any] = {"as_of": as_of if isinstance(as_of, str) and as_of else "date not reported"}
    if outstanding is not None:
        block["shares_outstanding"] = _big_count_text(outstanding)
    if float_shares is not None:
        block["public_float"] = _big_count_text(float_shares)
    if free_pct is not None:
        block["free_float_percent"] = _percent(free_pct)
    block["basis"] = "one share-count reading; insiders_percent is 100 minus free_float_percent"
    return block


def _short_block(short_interest: Any, status: str, resp: Any) -> Dict[str, Any]:
    """Exchange-reported short interest; Short % of Float by the Key Stats rule over the
    build's own float figure (`short_percent_of_float`)."""
    if not isinstance(short_interest, dict) or not short_interest:
        note = ("Short interest was not loaded in this answer" if status == "not_loaded"
                else "Short interest is not available for this symbol right now")
        return {"available": False,
                "note": note + "; do not state a figure, and never say there is none."}
    detail = getattr(resp, "ownership_detail", None)
    float_shares = _finite(getattr(detail, "float_shares", None))
    clean: Dict[str, Any] = {}
    for key in ("shares_short", "short_ratio", "short_change_3m", "short_percent_of_float"):
        value = _finite(short_interest.get(key))
        if value is not None:
            clean[key] = value
    block: Dict[str, Any] = {}
    settlement = short_interest.get("settlement_date")
    if isinstance(settlement, str) and settlement.strip():
        block["settlement_date"] = settlement.strip()[:10]
    if clean.get("shares_short") is not None and clean["shares_short"] >= 0:
        block["shares_short"] = _big_count_text(clean["shares_short"])
    try:
        from app.services.stock_overview_service import short_percent_of_float

        pct = _finite(short_percent_of_float(
            clean, float_shares if float_shares is not None and float_shares > 0 else None))
    except Exception as e:  # noqa: BLE001 — one figure, never the answer
        logger.warning("chat tool check_ownership_filings: short %% of float failed: %s: %s",
                       type(e).__name__, e)
        pct = None
    if pct is not None and pct >= 0:
        block["percent_of_float"] = _percent(pct)
    if clean.get("short_ratio") is not None and clean["short_ratio"] >= 0:
        block["days_to_cover"] = round(clean["short_ratio"], 2)
    if clean.get("short_change_3m") is not None:
        block["change_3_months_percent"] = round(clean["short_change_3m"], 2)
    if not block or set(block) == {"settlement_date"}:
        return {"available": False,
                "note": "Short interest is not available for this symbol right now; do not state a figure."}
    block["basis"] = "exchange-reported, as of settlement_date; percent of the float block's float"
    return block


def _congress_missing(degraded: Any) -> List[str]:
    """The chambers whose disclosure feeds failed in this Holders build ("House", "Senate"):
    holders substitutes an EMPTY list for a failed feed and names it in `degraded`."""
    names = degraded if isinstance(degraded, (list, tuple, set, frozenset)) else []
    return sorted({_CONGRESS_SOURCES[n] for n in names if isinstance(n, str) and n in _CONGRESS_SOURCES})


def _congress_block(resp: Any, unlocked: bool, degraded: Any = ()) -> Dict[str, Any]:
    """Congressional disclosures — Pro and above only. `resp` has already been passed through
    `redact_congress` for any other tier; a locked block names nobody either way.

    `degraded` is the Holders build's failed critical sources: a failed chamber feed arrives
    as an empty list, so with any of `_CONGRESS_SOURCES` in it the block is marked
    ``complete: False`` — the count reads "at least", and "no disclosure was found" is never
    said (degradation is said, never zeroed)."""
    if not unlocked:
        return {"locked": True, "note": _CONGRESS_LOCKED_NOTE}
    data = getattr(getattr(resp, "recent_activities", None), "congress_activities", None)
    if data is None:
        return {"available": False, "note": _CONGRESS_UNAVAILABLE_NOTE}
    missing = _congress_missing(degraded)
    acts = list(getattr(data, "activities", None) or [])
    summary = getattr(data, "summary", None)
    block: Dict[str, Any] = {"basis": _CONGRESS_BASIS}
    if missing:
        block["complete"] = False
    buyers = _count_or_none(getattr(summary, "num_buyers", None))
    sellers = _count_or_none(getattr(summary, "num_sellers", None))
    if buyers is not None and sellers is not None:
        if not missing:
            block["last_12_months"] = (f"{buyers} member(s) disclosed purchases and "
                                       f"{sellers} disclosed sales")
        elif buyers or sellers:
            block["last_12_months"] = (f"at least {buyers} member(s) disclosed purchases and "
                                       f"at least {sellers} disclosed sales (incomplete)")
    rows = []
    for act in acts[:5]:
        kind = getattr(act, "transaction_type", "")
        verb = "a purchase" if kind == "Purchase" else "a sale" if kind == "Sale" else None
        if verb is None:
            continue
        name = str(getattr(act, "name", "") or "")[:_NAME_MAX]
        role = str(getattr(act, "role", "") or "")[:40]
        owner = str(getattr(act, "owner", "") or "")[:20]
        amount = str(getattr(act, "amount_range", "") or "").strip()[:40] or "an undisclosed range"
        traded = str(getattr(act, "date", "") or "")[:10]
        disclosed = getattr(act, "disclosure_date", None)
        when = f"traded {traded}" + (f", disclosed {disclosed[:10]}" if isinstance(disclosed, str)
                                     and disclosed else ", disclosure date not recorded")
        who = f"{name} ({role}" + (f", owner {owner}" if owner and owner != "Self" else "") + ")"
        rows.append(f"{who}: disclosed {verb} of {amount}, {when}")
    if rows:
        block["disclosures"] = rows
        if len(acts) > len(rows):
            block["more_disclosures_not_shown"] = len(acts) - len(rows)
    if missing:
        block["note"] = (
            f"The {' and '.join(missing)} disclosure feed(s) could not be loaded in this answer: "
            "the disclosures listed may be incomplete. Say so; never say there are none, and "
            "never present the count as complete."
        )
    elif not rows:
        block["note"] = "No congressional disclosure of this stock was found in the period."
    return block


def _is_foreign(flags: Any) -> bool:
    if not isinstance(flags, dict):
        return False
    if flags.get("is_adr") is True:
        return True
    country = flags.get("country")
    return isinstance(country, str) and bool(country.strip()) and (
        country.strip().upper() not in _US_COUNTRIES)


def _percent(value: float) -> float:
    return round(float(value), 4 if abs(value) < 0.01 else 2)


def _institutions_block(resp: Any, degraded: List[str]) -> Dict[str, Any]:
    breakdown = getattr(resp, "shareholder_breakdown", None)
    detail = getattr(resp, "ownership_detail", None)
    quarter = getattr(detail, "institutions_quarter", None) if detail is not None else None
    if breakdown is None:
        return {"available": False, "note": "Institutional ownership could not be loaded right now."}
    block: Dict[str, Any] = {
        "as_of": f"13F filings for {quarter}" if quarter else "the latest quarterly 13F filings",
    }
    known = False
    if breakdown.institutions_unknown:
        block["institutions_percent"] = "unknown (the filings' total could not be determined)"
    else:
        block["institutions_percent"] = _percent(breakdown.institutions_percent)
        known = True
        basis = _INSTITUTION_BASIS.get(breakdown.institutions_source or "")
        if basis:
            block["institutions_basis"] = basis
    # 100 − free float, from the SAME shares-float reading the float block states (2026-10-08)
    # — never a second read taken at another time. A build without that reading (a pre-v6
    # row) falls back to the breakdown's own figure; a failed float read leaves a 0.0
    # placeholder there, which is not a figure.
    free_pct = _finite(getattr(detail, "free_float_percent", None)) if detail is not None else None
    if "Shares float" not in degraded and free_pct is not None and 0 < free_pct <= 100:
        block["insiders_percent"] = _percent(max(0.0, min(100.0, 100.0 - free_pct)))
        block["insiders_percent_basis"] = (
            "shares outside the public float (insiders and other strategic holders): 100 minus "
            "free_float_percent in the float block")
        if known:
            block["public_and_other_percent"] = _percent(breakdown.public_other_percent)
    elif "Shares float" not in degraded and breakdown.insiders_percent > 0:
        block["insiders_percent"] = _percent(breakdown.insiders_percent)
        block["insiders_percent_basis"] = "shares outside the public float (insiders and other strategic holders)"
        if known:
            block["public_and_other_percent"] = _percent(breakdown.public_other_percent)
    activities = list(getattr(getattr(resp, "recent_activities", None),
                              "institutional_activities", None) or [])
    by_name = {}
    for act in activities:
        key = str(getattr(act, "institution_name", "") or "").strip().lower()
        if key and key not in by_name:
            by_name[key] = act
    top = list(getattr(getattr(breakdown, "top_10_owners", None), "institutions", None) or [])
    listed: set = set()
    if top:
        rows = []
        for inst in top[:8]:
            text = f"{inst.name}: {_percent(inst.percent_ownership)}% of shares"
            value = float(inst.value_in_billions or 0.0)
            if value > 0:
                text += (f", worth ${value:,.2f} billion" if value >= 1
                         else f", worth ${value * 1000:,.1f} million") + " at the quarter's end"
            key = str(inst.name or "").strip().lower()
            listed.add(key)
            change = _change_text(by_name.get(key))
            if change:
                text += f"; {change}"
            rows.append(text)
        block["largest_institutions"] = rows
        known = True
    elif "Institutional holders" in degraded:
        block["largest_institutions"] = "could not be loaded right now"
    others = [a for a in activities
              if str(getattr(a, "institution_name", "") or "").strip().lower() not in listed]
    changes = []
    for act in others[:4]:
        text = _change_text(act, with_worth=True)
        if text:
            name = str(getattr(act, "institution_name", "") or "")[:_NAME_MAX]
            changes.append(f"{name}: {text}")
    if changes:
        block["other_large_changes"] = changes
    flow = _flow_text(getattr(getattr(resp, "recent_activities", None),
                              "institutional_flow_summary", None))
    if flow:
        block["quarter_flow"] = flow
    block["available"] = known
    return block


def _change_text(act: Any, *, with_worth: bool = False) -> Optional[str]:
    """One holder's change over the 13F quarter (split-restated by the Holders build)."""
    if act is None:
        return None
    amount = _finite(getattr(act, "change_in_millions", None))
    if getattr(act, "is_new_position", False):
        worth = _finite(getattr(act, "total_held_in_billions", None))
        text = "a NEW position in the quarter"
        if with_worth and worth is not None and worth > 0:
            text += f", worth about {_usd_text(worth * 1e9)}"
        return text
    pct = _finite(getattr(act, "change_percent", None))
    parts = []
    if pct is not None:
        parts.append(f"{'+' if pct > 0 else ''}{round(pct, 2)}% shares in the quarter")
    if amount is not None and amount != 0:
        parts.append(f"{'bought' if amount > 0 else 'sold'} about {_usd_text(abs(amount) * 1e6)}")
    if not parts:
        return None
    return parts[0] + (f" ({parts[1]})" if len(parts) > 1 else "")


def _flow_text(flow: Any) -> Optional[str]:
    inflow = _finite(getattr(flow, "in_flow_in_billions", None))
    outflow = _finite(getattr(flow, "out_flow_in_billions", None))
    if not inflow and not outflow:
        return None
    quarter = str(getattr(flow, "quarter_description", "") or "").strip()[:10]
    period = str(getattr(flow, "period_description", "") or "").strip()[:30]
    label = " ".join(x for x in (quarter, f"({period})" if period else "") if x) or "the latest quarter"
    return (f"{label}: about {_usd_text((inflow or 0.0) * 1e9)} bought and "
            f"{_usd_text((outflow or 0.0) * 1e9)} sold across 13F filers (estimated)")


def _fit(result: Dict[str, Any], budget: int) -> Dict[str, Any]:
    """Shrink to `budget` characters of JSON. People are the core of this tool, so they go
    last, and each one left out is NAMED under `not_shown`:
      1. older people lose their trade line;
      2. the side lists shrink (other institutions' changes, congressional disclosures, the
         largest-institution list);
      3. older people keep only their first holding (`more_holdings_not_shown` counts the
         rest) — still listed with their latest reported holding and its date;
      4. the other institutions' changes and the quarter's flow are dropped;
      5. the side lists go to a minimum and the six-month window goes;
      6. the least recent people are dropped, by name;
      7. the first person's later holdings, as before;
      8. last resort: the names of people left out are counted instead.
    Anything trimmed in steps 2-8 sets `shortened` so the model says "not loaded"."""
    def size() -> int:
        return len(json.dumps(result, default=str))

    def mark() -> None:
        result["shortened"] = ("some detail was left out to fit this answer: anything not "
                               "listed was not loaded — never zero or none")

    if size() <= budget:
        return result
    insiders = result.get("insiders") or {}
    people: List[Dict[str, Any]] = insiders.get("people") or []
    institutions = result.get("institutions") if isinstance(result.get("institutions"), dict) else {}
    congress = result.get("congress") if isinstance(result.get("congress"), dict) else {}
    # 1. Least recent first: the person a question names is usually the one in the news.
    for person in reversed(people[_KEEP_TRADES_FOR:]):
        if size() <= budget:
            return result
        person.pop("latest_transaction", None)
    # 2. The side lists, to a compact length each.
    for holder, key, keep in ((institutions, "other_large_changes", 2),
                              (congress, "disclosures", 3),
                              (institutions, "largest_institutions", 5)):
        if size() <= budget:
            return result
        rows = holder.get(key)
        if isinstance(rows, list) and len(rows) > keep:
            if key == "disclosures":
                congress["more_disclosures_not_shown"] = (
                    int(congress.get("more_disclosures_not_shown") or 0) + len(rows) - keep)
            del rows[keep:]
            mark()
    # 3. Older people keep their first holding.
    for person in reversed(people[_KEEP_TRADES_FOR:]):
        if size() <= budget:
            return result
        holdings = person.get("holdings")
        if isinstance(holdings, list) and len(holdings) > 1:
            person["more_holdings_not_shown"] = (
                int(person.get("more_holdings_not_shown") or 0) + len(holdings) - 1)
            del holdings[1:]
            mark()
    # 4. The other institutions' changes and the quarter's flow.
    for key in ("other_large_changes", "quarter_flow"):
        if size() <= budget:
            return result
        if institutions.pop(key, None) is not None:
            mark()
    # 5. The side lists to a minimum, and the six-month window — before any person goes.
    activity = result.get("insider_activity") if isinstance(result.get("insider_activity"), dict) else {}
    for holder, key, keep in ((institutions, "largest_institutions", 3), (congress, "disclosures", 1)):
        if size() <= budget:
            return result
        rows = holder.get(key)
        if isinstance(rows, list) and len(rows) > keep:
            if key == "disclosures":
                congress["more_disclosures_not_shown"] = (
                    int(congress.get("more_disclosures_not_shown") or 0) + len(rows) - keep)
            del rows[keep:]
            mark()
    if size() > budget and activity.pop("last_6_months", None) is not None:
        mark()
    # 6. The least recent people, each NAMED.
    dropped: List[str] = []
    capped = list(insiders.get("not_shown") or [])   # named by the derivation's cap already
    while size() > budget and len(people) > 1:
        dropped.insert(0, str(people.pop().get("name")))
        insiders["not_shown"] = dropped + capped
        mark()
    # 7. The first person's later holdings.
    if size() > budget and people:
        holdings = people[0].get("holdings") or []
        while size() > budget and len(holdings) > 1:
            holdings.pop()
            people[0]["holdings_truncated"] = True
            mark()
    # 8. Last resort, so the result ALWAYS fits: the names of people left out are counted
    #    instead (still "not loaded", never "no filing").
    names = insiders.get("not_shown")
    while size() > budget and isinstance(names, list) and names:
        names.pop()
        insiders["not_shown_unnamed_count"] = int(insiders.get("not_shown_unnamed_count") or 0) + 1
        mark()
    if isinstance(names, list) and not names:
        insiders.pop("not_shown", None)
    return result


def _built_at(detail: Any) -> Optional[datetime]:
    """When `detail` (an `OwnershipDetailSchema`) read the filings; None when unstamped."""
    raw = getattr(detail, "built_at", None) if detail is not None else None
    if not isinstance(raw, str):
        return None
    try:
        when = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return when if when.tzinfo else when.replace(tzinfo=timezone.utc)


def _age_seconds(when: datetime) -> float:
    return (datetime.now(timezone.utc) - when).total_seconds()


def _has_insiders(detail: Any) -> bool:
    return detail is not None and getattr(detail, "insider_holdings", None) is not None


def _remember_probe(sym: str, newer: Optional[bool], asked: Optional[str]) -> None:
    if sym not in _last_probe and len(_last_probe) >= _PROBE_MEMORY:
        _last_probe.pop(min(_last_probe, key=lambda k: _last_probe[k][0]), None)
    _last_probe[sym] = (time.monotonic(), newer, asked)


class _InsiderView:
    """The insider side of one Holders build — its read of the filings (`ownership_detail`),
    its insider chart (`insider_data`) and its activity list (`recent_activities` carrying
    ONLY `insider_activities`: never the untiered congressional rows) — readable by
    `_insider_activity_block` exactly as a `HoldersResponse` is."""

    __slots__ = ("ownership_detail", "insider_data", "recent_activities")

    def __init__(self, resp: Any) -> None:
        self.ownership_detail = getattr(resp, "ownership_detail", None)
        smart = getattr(resp, "insider_data", None)
        if hasattr(smart, "model_copy"):
            # Only the bars and the summary card are read: the price series (a year of daily
            # closes) are not kept in the per-ticker memory.
            smart = smart.model_copy(update={"price_data": [], "daily_prices": []})
        self.insider_data = smart
        self.recent_activities = _InsiderActivitiesOnly(
            getattr(getattr(resp, "recent_activities", None), "insider_activities", None))


class _InsiderActivitiesOnly:
    __slots__ = ("insider_activities",)

    def __init__(self, insider_activities: Any) -> None:
        self.insider_activities = insider_activities


def _forget_read(sym: str) -> None:
    _fresh_reads.pop(sym, None)
    _fresh_views.pop(sym, None)


def _remember_read(sym: str, detail: Any, view: Optional[_InsiderView] = None) -> None:
    _forget_read(sym)
    while len(_fresh_reads) >= _FRESH_MEMORY:
        _forget_read(next(iter(_fresh_reads)))          # the oldest stored
    while len(_fresh_views) >= _FRESH_MEMORY:
        _fresh_views.pop(next(iter(_fresh_views)))
    _fresh_reads[sym] = detail
    if view is not None:
        _fresh_views[sym] = view


def _activity_available(source: Any) -> bool:
    smart = getattr(source, "insider_data", None)
    recent = getattr(getattr(source, "recent_activities", None), "insider_activities", None)
    return (smart is not None and recent is not None and not getattr(smart, "unavailable", None)
            and not getattr(recent, "unavailable", None))


def _activity_source(sym: str, resp: Any, detail: Any) -> Tuple[Any, Optional[str]]:
    """What `insider_activity` is built from: the build the insiders list (`detail`) came
    from — `resp` itself, or the kept forced rebuild's `_InsiderView` — and None; or, when
    that rebuild's activity is not held (or unusable), `resp` with a note that its totals
    predate the newest filing the insiders list shows. `detail` differs from `resp`'s own
    read only when it is NEWER (`_newest_read`, `_check_and_rebuild`)."""
    if detail is None or detail is getattr(resp, "ownership_detail", None):
        return resp, None
    view = _fresh_views.get(sym)
    if view is not None and view.ownership_detail is detail and _activity_available(view):
        return view, None
    return resp, _ACTIVITY_OLDER_READ


def _kept_for(detail: Any) -> int:
    """How long a forced read is kept: a day — or, when it lost a page of the feed (people
    may be missing), only until the next probe, which then retries the rebuild."""
    complete = getattr(getattr(detail, "insider_holdings", None), "complete", True)
    return _FRESH_SECONDS if complete is not False else _PROBE_AFTER_SECONDS


def _newest_read(sym: str, detail: Any) -> Any:
    """The newest insider read held for `sym` — the Holders build's, or a forced rebuild's
    kept for chat — or None when neither carries insider holdings."""
    kept = _fresh_reads.get(sym)
    if kept is not None:
        kept_at = _built_at(kept)
        if kept_at is None or _age_seconds(kept_at) > _kept_for(kept):
            _forget_read(sym)
            kept = None
    if not _has_insiders(detail):
        return kept
    if kept is None:
        return detail
    if (_built_at(detail) or _EPOCH) >= (_built_at(kept) or _EPOCH):
        _forget_read(sym)                               # the Holders build caught up
        return detail
    return kept


def _replayed(newer: Optional[bool]) -> Optional[str]:
    return _NEWER_NOT_LOADED if newer else _UNCHECKED if newer is None else None


async def _freshen(holders: Any, sym: str, detail: Any) -> Tuple[Any, Optional[str]]:
    """The insider read to answer from, and a freshness note (or None).

    The newest read held (`_newest_read`) is trusted as is while younger than
    `_PROBE_AFTER_SECONDS`. An older one (or one with no stamp) is checked with ONE call
    (`newer_insider_filing`), at most once per ticker per `_PROBE_EVERY_SECONDS` — a repeat
    question about that SAME read reuses the answer — and one check runs per ticker at a time:
    concurrent questions wait for it rather than probing again or replaying a half-finished
    answer (review F4). A newer filing forces a rebuild; a rebuild that read the insiders is
    kept for chat (`_remember_read`) even when an unrelated source degraded it, so a follow-up
    never goes backwards. A rebuild that cannot read them — or hands back a read no newer than
    the one it replaces (review F3) — serves the older figures with a note saying they predate
    a filing. Returns ``(detail, note or None)``."""
    best = _newest_read(sym, detail)
    if best is None:
        return detail, None                 # nothing to freshen: insiders are unavailable
    built = _built_at(best)
    if built is not None and _age_seconds(built) < _PROBE_AFTER_SECONDS:
        return best, None
    asked = getattr(best, "built_at", None)
    last = _last_probe.get(sym)
    if last is not None and last[2] == asked and time.monotonic() - last[0] < _PROBE_EVERY_SECONDS:
        return best, _replayed(last[1])
    flight = _probe_inflight.get(sym)
    if flight is not None:
        try:
            return await asyncio.shield(flight)
        except Exception as e:  # noqa: BLE001 — the check that ran for us did not finish
            logger.warning("chat tool check_ownership_filings: the freshness check this question "
                           "waited on failed for %s: %s: %s", sym, type(e).__name__, e)
            return best, _UNCHECKED
    future: asyncio.Future = asyncio.get_running_loop().create_future()
    _probe_inflight[sym] = future
    try:
        try:
            outcome = await _check_and_rebuild(holders, sym, best)
        except Exception:  # noqa: BLE001 — a defect here must not cost the answer
            logger.exception("chat tool check_ownership_filings: freshness check failed for %s", sym)
            outcome = (best, _UNCHECKED)
        if not future.done():
            future.set_result(outcome)
        return outcome
    finally:
        if _probe_inflight.get(sym) is future:
            _probe_inflight.pop(sym, None)
        if not future.done():
            # Cancelled mid-check (the tool's timeout): questions waiting on it must not hang.
            future.set_exception(RuntimeError(f"ownership freshness check for {sym} was cancelled"))
            future.exception()


async def _check_and_rebuild(holders: Any, sym: str, best: Any) -> Tuple[Any, Optional[str]]:
    """One probe, and a forced rebuild when it finds a newer filing. Never raises."""
    asked = getattr(best, "built_at", None)
    try:
        newer = await holders.newer_insider_filing(sym, best)
    except Exception as e:  # noqa: BLE001 — "could not check" is an answer
        logger.warning("chat tool check_ownership_filings: freshness probe failed for %s: %s: %s",
                       sym, type(e).__name__, e)
        newer = None
    if not newer:
        _remember_probe(sym, newer, asked)
        return best, _replayed(newer)
    logger.info("chat tool check_ownership_filings: %s has an insider filing newer than its "
                "Holders build (%s) — rebuilding", sym, asked)
    try:
        fresh, fresh_degraded = await holders.get_holders_with_status(sym, force_refresh=True)
    except Exception as e:  # noqa: BLE001
        logger.warning("chat tool check_ownership_filings: forced refresh failed for %s: %s: %s",
                       sym, type(e).__name__, e, exc_info=True)
        fresh, fresh_degraded = None, []
    fresh_detail = getattr(fresh, "ownership_detail", None)
    fresh_at, best_at = _built_at(fresh_detail), _built_at(best)
    _remember_probe(sym, True, asked)
    if fresh is not None and not _has_insiders(fresh_detail):
        logger.warning("chat tool check_ownership_filings: refreshed build for %s has no insider "
                       "holdings (%s) — serving the older build, flagged", sym,
                       ", ".join(fresh_degraded or []) or "no detail")
    elif fresh is not None and (fresh_at is None or (best_at is not None and fresh_at <= best_at)):
        logger.warning("chat tool check_ownership_filings: the refresh for %s handed back a read "
                       "no newer than the stale one (%s, was %s) — serving the older figures, "
                       "flagged", sym, getattr(fresh_detail, "built_at", None), asked)
    elif fresh is not None:
        # The rebuild's insider chart and activity list are kept WITH its read, so the
        # activity windows describe the same filings as the insiders list (review, 10-08).
        _remember_read(sym, fresh_detail, _InsiderView(fresh))
        if fresh_degraded:
            logger.info("chat tool check_ownership_filings: %s rebuilt with its insider filings "
                        "but degraded elsewhere (%s) — kept for chat", sym, ", ".join(fresh_degraded))
        return fresh_detail, None
    return best, _NEWER_NOT_LOADED


def _start_side_read(coro: Any, sym: str, what: str) -> "asyncio.Task":
    """A side read as a task held in `_side_tasks` until it finishes; its failure is logged
    (never a silent loss) even when no one waits for it any more."""
    task = asyncio.ensure_future(coro)
    _side_tasks.add(task)

    def _done(t: "asyncio.Task") -> None:
        _side_tasks.discard(t)
        if t.cancelled():
            return
        exc = t.exception()
        if exc is not None:
            logger.warning("chat tool check_ownership_filings: %s read failed for %s: %s: %s",
                           what, sym, type(exc).__name__, exc)

    task.add_done_callback(_done)
    return task


async def _await_side(task: "asyncio.Task", timeout: float) -> Tuple[Any, str]:
    """``(value, status)``: "ok", "not_loaded" (still running after `timeout` — it keeps going
    and warms its cache) or "failed". Never raises."""
    if not task.done():
        await asyncio.wait({task}, timeout=max(0.0, timeout))
    if not task.done():
        return None, "not_loaded"
    if task.cancelled() or task.exception() is not None:
        return None, "failed"
    return task.result(), "ok"


async def _load_short_interest(sym: str) -> Any:
    """The exchange-reported short interest through its own two-tier cache (the same read
    the Overview's Key Stats makes)."""
    from app.integrations.finra_short_interest import get_short_interest

    return await get_short_interest(sym)


async def _issuer_profile_flags(sym: str) -> Optional[Dict[str, Any]]:
    """The company-profile cache row (`country`, `is_adr`) — a DB read off the loop, bounded;
    None when absent, slow or failed (the foreign-issuer note is then simply not added)."""
    try:
        from app.services.stock_overview_service import get_stock_overview_service

        service = get_stock_overview_service()
        row = await asyncio.wait_for(
            asyncio.to_thread(service.get_cached_company_profile, sym), timeout=_PROFILE_WAIT)
    except Exception as e:  # noqa: BLE001 — the note is optional; the answer is not
        logger.warning("chat tool check_ownership_filings: company profile read failed for %s: "
                       "%s: %s", sym, type(e).__name__, e)
        return None
    return row if isinstance(row, dict) else None


def _resolved_as(sym: str) -> str:
    text = f"{sym}: the US-listed security with this ticker"
    if detect_asset_class(sym, include_bare_coins=True) == "crypto":
        text += " — not the cryptocurrency of the same symbol"
    return text


async def fetch_ownership(ticker: str, user_tier: Optional[str] = None) -> Dict[str, Any]:
    """Who holds `ticker`: each insider's shares after their latest reported transaction (as
    of that filing), insider buying and selling over 3/6/12 months, institutional ownership
    and change, the float, short interest and — for a Pro or higher `user_tier` only —
    congressional disclosures. `user_tier` None, "free" or anything unrecognised is LOCKED
    (`congress_holders_unlocked` fails closed): no member's name or trade appears anywhere in
    the result. Never raises."""
    sym = (ticker or "").strip().upper().replace(".", "-")
    if not sym:
        return {"error": "no ticker supplied"}
    if detect_asset_class(sym) != "stock":
        # Answered, not an outage: a coin, an index or a futures contract has no Form 4 filers.
        return {"ticker": sym, "error": "ownership filings exist only for a company's listed stock",
                "note": "Say ownership filings cover listed companies' shares only."}

    from app.services.holders_service import _validate_ticker, get_holders_service

    try:
        sym = _validate_ticker(sym)
    except ValueError:
        return {"ticker": sym, "error": "not a US-listed stock symbol the ownership filings cover",
                "note": "Say the ownership filings could not be looked up for this symbol."}
    holders = get_holders_service()
    started = time.monotonic()
    short_task = _start_side_read(_load_short_interest(sym), sym, "short interest")
    try:
        resp, degraded = await holders.get_holders_with_status(sym)
    except Exception as e:  # noqa: BLE001 — a tool failure is data for the model
        from app.log_redaction import redact_secrets

        logger.warning(
            "chat tool check_ownership_filings failed for %s: %s: %s",
            sym, type(e).__name__, e, exc_info=True,
        )
        return {
            "ticker": sym, "available": False, "upstream": True,
            "error": redact_secrets(f"{type(e).__name__}: {e}")[:200],
            "note": "Ownership filings could not be loaded right now; do not say there are none.",
        }

    # Congress is Pro and above. The Holders build is UNTIERED (the report and the snapshot
    # read it too), so the gate is applied here, before any block is built — the same
    # `redact_congress` the Holders route applies.
    from app.services.entitlements import (
        TIER_PRO,
        congress_holders_unlocked,
        required_tier_for_congress_holders,
    )
    from app.services.holders_service import redact_congress

    unlocked = congress_holders_unlocked(user_tier)
    if not unlocked:
        try:
            resp = redact_congress(resp, required_tier_for_congress_holders(user_tier) or TIER_PRO)
        except Exception as e:  # noqa: BLE001 — the locked block never reads congress anyway
            logger.warning("chat tool check_ownership_filings: congress redaction failed for %s: "
                           "%s: %s", sym, type(e).__name__, e)

    degraded = list(degraded or [])
    # Insiders come from the newest read of the filings (a forced rebuild's, when a newer
    # Form 4 landed); institutions from the Holders build as the tab shows it — a rebuild for
    # insiders never trades good 13F figures for a degraded copy.
    detail, freshness = await _freshen(holders, sym, getattr(resp, "ownership_detail", None))
    # The activity windows come from the SAME read as the insiders list (a forced rebuild's
    # chart and list when it supplied the insiders), or say they predate it.
    activity_source, activity_note = _activity_source(sym, resp, detail)
    built = _built_at(detail)
    insiders = _insiders_block(detail)
    if insiders.get("available") and not insiders.get("people"):
        # No Form 4 filer at all: a foreign private issuer's insiders may be exempt.
        if _is_foreign(await _issuer_profile_flags(sym)):
            insiders["foreign_issuer"] = _FOREIGN_ISSUER_NOTE
    short_interest, short_status = await _await_side(
        short_task, _SHORT_INTEREST_WAIT - (time.monotonic() - started))
    result: Dict[str, Any] = {
        "ticker": sym,
        "resolved_as": _resolved_as(sym),
        "filings_checked_at": (built.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
                               if built else "unknown"),
        "insiders": insiders,
        "insider_activity": _insider_activity_block(activity_source, note=activity_note),
        "institutions": _institutions_block(resp, degraded),
        "float": _float_block(resp),
        "short_interest": _short_block(short_interest, short_status, resp),
        "congress": _congress_block(resp, unlocked, degraded),
        "beneficial_owners_13d_13g": _BENEFICIAL_OWNERS_NOTE,
        "how_to_read": _HOW_TO_READ,
    }
    if freshness:
        result["freshness"] = freshness
    if not result["insiders"].get("available") and not result["institutions"].get("available"):
        # Nothing usable reached the model: the doors' refund gate counts this as a failed tool.
        result["error"] = "ownership data unavailable (upstream fetch failed)"
        result["upstream"] = True
    if degraded:
        logger.info("chat tool check_ownership_filings: %s served degraded (%s)",
                    sym, ", ".join(degraded))

    from app.config import settings

    cap = int(getattr(settings, "GEMINI_TOOL_RESULT_MAX_CHARS", 8000) or 8000)
    return _fit(result, max(2000, cap - _BUDGET_MARGIN))
