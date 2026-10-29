"""Preview the Trillion Club stake finder — classify-only, READ-ONLY.

Runs the finder's discovery and classification (`stake_finder.classify_only`: S1, the members' own Schedule
13D/13G, and S2, EDGAR full-text search over other companies' filings, with gates 2-7) against LIVE SEC EDGAR
for the filings of `--since` … today (ET), and prints one block per finding grouped by outcome — auto,
proposal, heads-up, skipped — then the counts. It shows what the daily run WOULD do; it does none of it.

It WRITES NOTHING and has no write flag at all:
  * Supabase is never reached. The first thing `main` does is install `preview_trillion_club`'s tripwire, so
    every `get_supabase()` in this process returns a client that refuses all use. The members and their
    stakes come from `backend/data/trillion_club_seed.json` (each stake as origin 'seed'), the member settings
    from `backend/data/trillion_club_filers.json`.
  * Telegram is never reached. Every send / edit / answer function of `app.integrations.telegram`, and the
    one request function they all go through, is replaced by a stub that raises AND is counted: a call — even
    one the caller swallowed — fails the preview (exit 1).
  * No FMP, no LLM. Membership is not computed: the seed's published companies are treated as members (the
    seed is the club as reviewed). The members' 13F holdings live in Supabase, so they are not loaded
    (`holdings=None`): an `on_13f_note` decision may differ from the daily run's.
SEC reads use the app's client and its shared 5 rps limiter. `SEC_EDGAR_USER_AGENT` (backend/.env) carries a
contact address: it is never printed, and every error line is scrubbed of it.

Exit codes: 0 = the preview ran; 1 = the classification failed or is INCOMPLETE (a member's SEC read or
a full-text query failed: the findings printed are partial, and the parts that did not finish are listed),
or something tried to reach Telegram; 2 = it could not start (a bad window, an unreadable filers or seed
file, no User-Agent, no finder).

Examples (from backend/):
    ./venv/bin/python scripts/preview_trillion_club_stakes.py --since 2026-09-24
    ./venv/bin/python -m scripts.preview_trillion_club_stakes --slug nvidia --show-messages
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import re
import sys
import unicodedata
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, FrozenSet, List, Mapping, Optional, Sequence, Tuple

BACKEND = Path(__file__).resolve().parents[1]
SEED_PATH = BACKEND / "data" / "trillion_club_seed.json"
FILERS_PATH = BACKEND / "data" / "trillion_club_filers.json"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.services.trillion_club.stake_contracts import (  # noqa: E402
    OUTCOME_AUTO,
    OUTCOME_HEADS_UP,
    OUTCOME_PROPOSAL,
    OUTCOME_SKIPPED,
    REASONS,
    load_member_configs,
)
from app.services.trillion_club.stake_names import (  # noqa: E402
    auto_tier_gate,
    parse_auto_since,
    parse_auto_tiers,
)
from app.utils.market_hours import ET  # noqa: E402
# The Supabase tripwire, the seed loader, the secret scrub and the log filter of the 13F preview — one copy.
from scripts.preview_trillion_club import (  # noqa: E402
    _KeyScrubFilter,
    install_supabase_tripwire,
    load_seed,
    safe,
)

logger = logging.getLogger("preview_trillion_club_stakes")

#: The default window: filings of the last 30 days (ET).
DEFAULT_WINDOW_DAYS = 30
#: The oldest `--since` accepted (the finder's own lookback is at most 120 days).
MAX_WINDOW_DAYS = 366
OUTCOME_ORDER: Tuple[str, ...] = (OUTCOME_AUTO, OUTCOME_PROPOSAL, OUTCOME_HEADS_UP, OUTCOME_SKIPPED)
OUTCOME_TITLES: Dict[str, str] = {
    OUTCOME_AUTO: "AUTO — would go live on its own with a Withdraw / Keep note (if the daily and weekly caps "
                  "and the withdrawal breaker allow: they need the ledger and are not checked here)",
    OUTCOME_PROPOSAL: "PROPOSAL — a one-tap Publish / Dismiss message",
    OUTCOME_HEADS_UP: "HEADS-UP — one digest line, never a row",
    OUTCOME_SKIPPED: "SKIPPED — recorded and counted only",
}
#: Every function of `app.integrations.telegram` that talks to the Bot API, plus `_call`, the one request
#: function they all use (so even a function bound by `from … import send_message` before the stubs went in is
#: refused).
TELEGRAM_FUNCTIONS: Tuple[str, ...] = (
    "send_message", "send_video", "send_photo", "send_audio", "edit_message_text", "edit_message_reply_markup",
    "answer_callback_query", "set_webhook", "_call",
)
#: Every refused Telegram call of this process, by function name (a non-empty list fails the preview).
TELEGRAM_STUB_CALLS: List[str] = []

_NOT_RUN = object()                 # the classification raised: there are no findings to print
_ISO_DATE_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
_UNSAFE_CATEGORIES = frozenset({"Cc", "Cf", "Cs", "Zl", "Zp"})
_SEED_ID_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "https://caydexinvest.com/trillion-club/seed-stake")


# ── Read-only guarantees ───────────────────────────────────────────────────────────────


class TelegramRefused(RuntimeError):
    """Raised by the Telegram stubs: this preview never sends, edits or answers anything."""


def _telegram_stub(name: str) -> Callable[..., Any]:
    async def refused(*_args: Any, **_kwargs: Any) -> Any:
        TELEGRAM_STUB_CALLS.append(name)
        raise TelegramRefused(f"preview_trillion_club_stakes is read-only: refused telegram.{name}")

    refused.__name__ = f"refused_{name}"
    refused.stake_preview_stub = True  # type: ignore[attr-defined]
    return refused


def install_telegram_stubs() -> Dict[str, Any]:
    """Replace every Bot API function (`TELEGRAM_FUNCTIONS`) with a raising, counted stub. Returns the
    originals for `restore_telegram`."""
    from app.integrations import telegram

    saved: Dict[str, Any] = {}
    for name in TELEGRAM_FUNCTIONS:
        if hasattr(telegram, name):
            saved[name] = getattr(telegram, name)
            setattr(telegram, name, _telegram_stub(name))
    return saved


def restore_telegram(saved: Mapping[str, Any]) -> None:
    from app.integrations import telegram

    for name, fn in saved.items():
        setattr(telegram, name, fn)


def _user_agent() -> Optional[str]:
    from app.config import settings

    value = getattr(settings, "SEC_EDGAR_USER_AGENT", None)
    return value.strip() if isinstance(value, str) and value.strip() else None


def clean(value: Any, cap: int = 200) -> str:
    """One printable line: secrets scrubbed (the SEC User-Agent included), control and format characters
    replaced, capped."""
    text = safe(value if isinstance(value, str) else str(value), _user_agent())
    text = "".join(" " if unicodedata.category(ch) in _UNSAFE_CATEGORIES else ch for ch in text[: cap * 4])
    text = " ".join(text.split())
    return text if len(text) <= cap else text[: cap - 1] + "…"


# ── Inputs ─────────────────────────────────────────────────────────────────────────────


def preview_companies(rows: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    """The seed's company rows as the finder reads them. The seed carries no membership columns (the daily
    job computes them from FMP closes, which a preview never calls), so a PUBLISHED company in `auto` mode is
    marked a member: the seed is the club as reviewed. `force_in` / `force_out` decide by themselves."""
    out: List[Dict[str, Any]] = []
    for row in rows:
        company = dict(row)
        if company.get("published") is True and (company.get("membership_mode") or "auto") == "auto":
            company.setdefault("is_member", True)
        out.append(company)
    return out


def preview_stakes(rows: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    """The seed's stake rows as the table holds them after migration 205: origin 'seed', no machine columns,
    and a stable synthetic id (the seed has none) so identity matches can name the row they hit."""
    out: List[Dict[str, Any]] = []
    for row in rows:
        stake = dict(row)
        key = f"{stake.get('company_slug')}|{stake.get('investee_name')}|{stake.get('kind')}"
        stake.setdefault("id", str(uuid.uuid5(_SEED_ID_NAMESPACE, key)))
        stake.setdefault("origin", "seed")
        for column in ("owner_kept_at", "withdrawn_at", "investee_cik", "finding_id"):
            stake.setdefault(column, None)
        out.append(stake)
    return out


def _iso_date(value: str) -> date:
    if not isinstance(value, str) or not _ISO_DATE_RE.fullmatch(value):
        raise argparse.ArgumentTypeError(f"{value!r} is not a YYYY-MM-DD date")
    try:
        return date.fromisoformat(value)
    except ValueError as e:
        raise argparse.ArgumentTypeError(f"{value!r} is not a real date ({e})") from None


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    # allow_abbrev=False: an unknown flag is an error, never silently read as the option it prefixes.
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0], allow_abbrev=False)
    parser.add_argument("--since", type=_iso_date, default=None,
                        help=f"the first filing date to read, YYYY-MM-DD (ET); default {DEFAULT_WINDOW_DAYS} "
                             f"days ago")
    parser.add_argument("--slug", action="append", help="limit to this member (repeatable)")
    parser.add_argument("--show-messages", action="store_true",
                        help="also print the Telegram text an auto note or a proposal would carry")
    return parser.parse_args(argv)


# ── Output ─────────────────────────────────────────────────────────────────────────────


def _reason(code: Any) -> str:
    if isinstance(code, str) and code in REASONS:
        return f"{code} — {REASONS[code]}"
    return f"{clean(code, 40)} — (not a known reason code)"


def _row_summary(row: Mapping[str, Any]) -> str:
    bits = [clean(row.get("investee_name") or "(no name)", 80), clean(row.get("kind") or "(no kind)", 30)]
    for key, prefix in (("investee_us_symbol", ""), ("investee_cusip", "CUSIP "), ("local_listing", "listed ")):
        if row.get(key):
            bits.append(prefix + clean(row[key], 40))
    pct = row.get("ownership_pct")
    if isinstance(pct, (int, float)) and not isinstance(pct, bool):
        bits.append(f"{pct:g}% {clean(row.get('ownership_basis') or '', 60)}".rstrip())
    value = row.get("disclosed_value_usd")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        bits.append(f"${value:,.0f} {clean(row.get('value_basis') or '', 30)}".rstrip())
    if row.get("as_of"):
        bits.append(f"as of {clean(row['as_of'], 20)}")
    return " · ".join(bits)


def _member_names(companies: Sequence[Mapping[str, Any]]) -> Dict[str, str]:
    return {str(c.get("slug")): str(c.get("display_name") or c.get("slug")) for c in companies}


def _message_text(finding: Mapping[str, Any], members: Mapping[str, str]) -> Optional[str]:
    """The Telegram text the daily run would send for this finding (auto note / proposal), else None."""
    from app.services.trillion_club.stake_review import auto_note_text, proposal_text

    row = finding.get("row")
    if not isinstance(row, Mapping):
        return None
    shown = {**finding, "company_slug": finding.get("slug"),
             "member_name": members.get(str(finding.get("slug"))) or finding.get("slug")}
    spans = finding.get("spans") if isinstance(finding.get("spans"), list) else []
    if finding.get("outcome") == OUTCOME_AUTO:
        return auto_note_text(shown, row)
    if finding.get("outcome") == OUTCOME_PROPOSAL:
        return proposal_text(shown, row, trial=finding.get("trial") is True, evidence=spans)
    return None


def _auto_switches() -> Tuple[FrozenSet[str], Optional[date]]:
    """The two auto-publish switches as the daily run reads them (gate 10)."""
    from app.config import settings

    return (parse_auto_tiers(settings.TRILLION_CLUB_STAKE_AUTO_TIERS),
            parse_auto_since(settings.TRILLION_CLUB_STAKE_AUTO_SINCE))


def apply_auto_switches(findings: Sequence[Any], *, tiers: FrozenSet[str], since: Optional[date]) -> List[Any]:
    """Gate 10 over classify_only's auto-ELIGIBLE findings (classify_only stops at gate 7): a finding today's
    switches would not let go live is shown as the proposal the daily run would send, with its reason."""
    out: List[Any] = []
    for f in findings:
        if isinstance(f, Mapping) and f.get("outcome") == OUTCOME_AUTO:
            filed = f.get("filed_on")
            try:
                filed_on = date.fromisoformat(filed) if isinstance(filed, str) else None
            except ValueError:
                filed_on = None
            why = auto_tier_gate(filed_on, tiers=tiers, since=since)
            if why is not None:
                note = f"auto-eligible, but {why} with today's switches — the daily run sends a proposal"
                f = {**f, "outcome": OUTCOME_PROPOSAL, "reason": why,
                     "detail": "; ".join(x for x in (note, f.get("detail")) if x)}
        out.append(f)
    return out


def print_findings(findings: Any, *, members: Mapping[str, str], show_messages: bool) -> Dict[str, int]:
    """One block per finding, grouped by outcome (auto, proposal, heads-up, skipped, then anything else),
    then the counts line. Returns the counts."""
    groups: Dict[str, List[Any]] = {o: [] for o in OUTCOME_ORDER}
    other: List[Any] = []
    for item in findings if isinstance(findings, (list, tuple)) else []:
        if isinstance(item, Mapping) and item.get("outcome") in groups:
            groups[item["outcome"]].append(item)
        else:
            other.append(item)
    for outcome in OUTCOME_ORDER:
        items = groups[outcome]
        print(f"\n━━ {OUTCOME_TITLES[outcome]} ({len(items)})")
        for f in items:
            head = " · ".join(clean(f.get(k) or "—", 40) for k in ("slug", "source", "form", "accession"))
            if outcome == OUTCOME_SKIPPED:
                print(f"  • {head} · {_reason(f.get('reason'))}")
                continue
            trial = " · TRIAL (would auto-publish)" if f.get("trial") is True else ""
            print(f"  • {head} · filed {clean(f.get('filed_on') or '—', 20)}")
            print(f"    tier {clean(f.get('tier') or '—', 30)} · {_reason(f.get('reason'))}{trial}")
            if isinstance(f.get("row"), Mapping):
                print(f"    row: {_row_summary(f['row'])}")
                if f["row"].get("source_title"):
                    print(f"    title: {clean(f['row']['source_title'], 160)}")
            if f.get("investee_cik"):
                print(f"    issuer CIK {clean(f['investee_cik'], 12)}")
            print(f"    {clean(f.get('source_url') or '(no source URL)', 300)}")
            if f.get("detail"):
                print(f"    detail: {clean(f['detail'], 400)}")
            spans = [s for s in f.get("spans") or [] if isinstance(s, str)] if isinstance(f.get("spans"), list) else []
            if spans:
                more = f" (+{len(spans) - 1} more)" if len(spans) > 1 else ""
                print(f"    evidence: “{clean(spans[0], 300)}”{more}")
            if show_messages:
                text = _message_text(f, members)
                if text:
                    print("    ┌ Telegram message:")
                    for line in text.split("\n"):
                        print(f"    │ {line}")
    if other:
        print(f"\n━━ OTHER — not a known outcome ({len(other)})")
        for item in other:
            if isinstance(item, Mapping):
                print(f"  • {clean(item.get('slug') or '—', 40)} · {clean(item.get('accession') or '—', 30)} · "
                      f"outcome {clean(item.get('outcome'), 30)}")
            else:
                print(f"  • (malformed finding: {type(item).__name__})")
    counts = {o: len(groups[o]) for o in OUTCOME_ORDER}
    counts["other"] = len(other)
    total = sum(counts.values())
    print(f"\nCounts: auto {counts[OUTCOME_AUTO]} · proposal {counts[OUTCOME_PROPOSAL]} · heads-up "
          f"{counts[OUTCOME_HEADS_UP]} · skipped {counts[OUTCOME_SKIPPED]} · other {counts['other']} — "
          f"{total} finding(s)")
    return counts


# ── Main ───────────────────────────────────────────────────────────────────────────────


async def run_preview(args: argparse.Namespace, *, classify: Optional[Callable[..., Any]] = None,
                      sec: Any = None, now: Optional[datetime] = None, seed_path: Optional[Path] = None,
                      filers_path: Optional[Path] = None) -> int:
    """Everything after the read-only guarantees are in place. Returns the exit code."""
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None or now.utcoffset() is None:
        print("preview: `now` must be timezone-aware")
        return 2
    today = now.astimezone(ET).date()
    since = args.since or today - timedelta(days=DEFAULT_WINDOW_DAYS)
    if since > today:
        print(f"preview: --since {since.isoformat()} is after today ({today.isoformat()} ET)")
        return 2
    if since < today - timedelta(days=MAX_WINDOW_DAYS):
        print(f"preview: --since {since.isoformat()} is more than {MAX_WINDOW_DAYS} days ago")
        return 2

    try:
        company_rows, stake_rows, source = load_seed(seed_path or SEED_PATH)
    except SystemExit as e:           # load_seed's own refusal of a seed with no companies
        print(f"preview: the seed could not be used — {clean(e)}")
        return 2
    except (OSError, ValueError) as e:
        print(f"preview: the seed could not be read — {type(e).__name__}: {clean(e)}")
        return 2
    companies, stakes = preview_companies(company_rows), preview_stakes(stake_rows)
    path = filers_path or FILERS_PATH
    try:
        configs = load_member_configs(str(path))
    except (OSError, ValueError) as e:
        print(f"preview: {path.name} could not be used — {type(e).__name__}: {clean(e)}")
        return 2
    if args.slug:
        unknown = sorted(set(args.slug) - set(configs))
        if unknown:
            print(f"preview: not in {path.name}: {', '.join(clean(s, 40) for s in unknown)}")
            return 2
        configs = {slug: cfg for slug, cfg in configs.items() if slug in set(args.slug)}

    if classify is None:
        try:
            from app.services.trillion_club.stake_finder import classify_only as classify
        except ImportError as e:
            print(f"preview: the stake finder could not be loaded — {type(e).__name__}: {clean(e)}")
            return 2
    own_sec = sec is None
    if own_sec:
        from app.integrations import sec_edgar

        if not sec_edgar.user_agent_configured():
            print("preview: SEC_EDGAR_USER_AGENT is not set (or not printable ASCII) in backend/.env — SEC "
                  "refuses requests without one")
            return 2
        sec = sec_edgar.get_sec_edgar_client()

    print(f"Trillion Club stake finder preview — {now.astimezone(ET):%Y-%m-%d %H:%M} ET · READ-ONLY (live SEC "
          f"reads; no Supabase, no Telegram, no FMP)")
    print(f"  filings since {since.isoformat()} · members {len(configs)} from {path.name} · companies and "
          f"{len(stakes)} stake row(s) from {source}")
    print("  membership: the seed's published companies count as members · 13F holdings: not loaded")
    tiers, auto_since = _auto_switches()
    print(f"  auto-publish switches: TRILLION_CLUB_STAKE_AUTO_TIERS={','.join(sorted(tiers)) or '(none)'} · "
          f"TRILLION_CLUB_STAKE_AUTO_SINCE={auto_since.isoformat() if auto_since else '(none)'}"
          + ("" if tiers and auto_since else " — nothing goes live on its own; auto-eligible finds are proposals"))
    code = 0
    findings: Any = _NOT_RUN
    try:
        findings = await classify(now=now, sec=sec, companies=companies, stakes=stakes, member_configs=configs,
                                  since=since, holdings=None)
    except Exception as e:  # noqa: BLE001 — reported, and the exit code says so
        print(f"\npreview: the classification FAILED — {type(e).__name__}: {clean(e, 400)}")
        logger.error("preview: the classification failed (%s)", type(e).__name__, exc_info=True)
        code = 1
    finally:
        if own_sec:
            from app.integrations import sec_edgar

            await sec_edgar.close_sec_edgar_client()
    if findings is not _NOT_RUN:
        if not isinstance(findings, (list, tuple)):
            print(f"\npreview: the finder returned a {type(findings).__name__}, not a list of findings")
            code = 1
        else:
            # classify_only's ClassifyResult lists what did not finish (an SEC error on one member, a
            # failed query): the findings shown are then PARTIAL, and the exit code says so.
            raw_problems = getattr(findings, "problems", None)
            problems = [p for p in raw_problems if isinstance(p, str)] if isinstance(raw_problems, list) else []
            findings = apply_auto_switches(findings, tiers=tiers, since=auto_since)
            print_findings(findings, members=_member_names(companies), show_messages=args.show_messages)
            if problems:
                print(f"\npreview: INCOMPLETE — {len(problems)} part(s) did not finish; the findings above are "
                      "partial:")
                for p in problems:
                    print(f"  ! {clean(p, 400)}")
                code = 1
    if TELEGRAM_STUB_CALLS:
        print(f"\npreview: Telegram was called {len(TELEGRAM_STUB_CALLS)} time(s) "
              f"({', '.join(sorted(set(TELEGRAM_STUB_CALLS)))}) and refused — a read-only preview must never "
              f"send; this is a bug")
        code = 1
    return code


def main(argv: Optional[Sequence[str]] = None, *, classify: Optional[Callable[..., Any]] = None,
         sec: Any = None, now: Optional[datetime] = None) -> int:
    """The preview's entry point. `classify` defaults to `stake_finder.classify_only` (lazy), `sec` to the app's
    SEC client, `now` to the clock."""
    install_supabase_tripwire()          # FIRST: nothing below may reach Supabase
    saved = install_telegram_stubs()
    try:
        args = parse_args(argv)
        return asyncio.run(run_preview(args, classify=classify, sec=sec, now=now))
    finally:
        restore_telegram(saved)


def _configure_logging() -> None:
    handler = logging.StreamHandler()
    handler.addFilter(_KeyScrubFilter(_user_agent()))
    handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    logging.basicConfig(level=logging.WARNING, handlers=[handler], force=True)


if __name__ == "__main__":
    _configure_logging()
    raise SystemExit(main())
