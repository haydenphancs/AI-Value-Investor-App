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

No vendor is named anywhere in the result (IDENTITY_RULE): the sources are SEC filings.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

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
    "percentages are of shares outstanding."
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
    verb = ("acquired" if t.acquired is True
            else "disposed of" if t.acquired is False else "reported")
    text = f"{t.transaction_type or 'unspecified code'}: {verb} {_shares_text(t.shares)} shares"
    if t.average_price:
        text += f" at an average ${t.average_price:,.2f}"
    return text


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
    # 100 − free float. A failed float read leaves a 0.0 placeholder, which is not a figure.
    if "Shares float" not in degraded and breakdown.insiders_percent > 0:
        block["insiders_percent"] = _percent(breakdown.insiders_percent)
        block["insiders_percent_basis"] = "shares outside the public float (insiders and other strategic holders)"
        if known:
            block["public_and_other_percent"] = _percent(breakdown.public_other_percent)
    top = list(getattr(getattr(breakdown, "top_10_owners", None), "institutions", None) or [])
    if top:
        rows = []
        for inst in top[:8]:
            text = f"{inst.name}: {_percent(inst.percent_ownership)}% of shares"
            value = float(inst.value_in_billions or 0.0)
            if value > 0:
                text += (f", worth ${value:,.2f} billion" if value >= 1
                         else f", worth ${value * 1000:,.1f} million") + " at the quarter's end"
            rows.append(text)
        block["largest_institutions"] = rows
        known = True
    elif "Institutional holders" in degraded:
        block["largest_institutions"] = "could not be loaded right now"
    block["available"] = known
    return block


def _fit(result: Dict[str, Any], budget: int) -> Dict[str, Any]:
    """Shrink to `budget` characters of JSON: older people lose their trade line first, then
    the least recent people are dropped, each one NAMED under `not_shown`."""
    def size() -> int:
        return len(json.dumps(result, default=str))

    if size() <= budget:
        return result
    insiders = result.get("insiders") or {}
    people: List[Dict[str, Any]] = insiders.get("people") or []
    # Least recent first: the person a question names is usually the one in the news.
    for person in reversed(people[_KEEP_TRADES_FOR:]):
        if size() <= budget:
            return result
        person.pop("latest_transaction", None)
    dropped: List[str] = []
    capped = list(insiders.get("not_shown") or [])   # named by the derivation's cap already
    while size() > budget and len(people) > 1:
        dropped.insert(0, str(people.pop().get("name")))
        insiders["not_shown"] = dropped + capped
    if size() > budget and people:
        holdings = people[0].get("holdings") or []
        while size() > budget and len(holdings) > 1:
            holdings.pop()
            people[0]["holdings_truncated"] = True
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


def _remember_read(sym: str, detail: Any) -> None:
    _fresh_reads.pop(sym, None)
    while len(_fresh_reads) >= _FRESH_MEMORY:
        _fresh_reads.pop(next(iter(_fresh_reads)))      # the oldest stored
    _fresh_reads[sym] = detail


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
            _fresh_reads.pop(sym, None)
            kept = None
    if not _has_insiders(detail):
        return kept
    if kept is None:
        return detail
    if (_built_at(detail) or _EPOCH) >= (_built_at(kept) or _EPOCH):
        _fresh_reads.pop(sym, None)                     # the Holders build caught up
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
        _remember_read(sym, fresh_detail)
        if fresh_degraded:
            logger.info("chat tool check_ownership_filings: %s rebuilt with its insider filings "
                        "but degraded elsewhere (%s) — kept for chat", sym, ", ".join(fresh_degraded))
        return fresh_detail, None
    return best, _NEWER_NOT_LOADED


async def fetch_ownership(ticker: str) -> Dict[str, Any]:
    """Who holds `ticker`: each insider's shares after their latest reported transaction (as
    of that filing) and institutional ownership. Never raises."""
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

    degraded = list(degraded or [])
    # Insiders come from the newest read of the filings (a forced rebuild's, when a newer
    # Form 4 landed); institutions from the Holders build as the tab shows it — a rebuild for
    # insiders never trades good 13F figures for a degraded copy.
    detail, freshness = await _freshen(holders, sym, getattr(resp, "ownership_detail", None))
    built = _built_at(detail)
    result: Dict[str, Any] = {
        "ticker": sym,
        "filings_checked_at": (built.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
                               if built else "unknown"),
        "insiders": _insiders_block(detail),
        "institutions": _institutions_block(resp, degraded),
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
