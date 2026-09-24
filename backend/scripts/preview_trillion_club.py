"""Preview the Trillion-Dollar Club — membership and every 13F card — READ-ONLY.

Runs the SAME code the daily job runs (``rules.evaluate_membership``, through the job's
stamp rule ``jobs.evaluate_fmp_sized`` for an FMP-sized company, over dated
``historical-market-capitalization`` closes; ``builder.build_filing`` over FMP's 13F extract
with the quarter before fetched live) against LIVE FMP, and prints:

* membership per company — the newest dated close, the current streak (closes at/above and
  below $1T), member since;
* per ``use_13f`` company — its newest 13F card (holdings, total, top 3 by weight, dates) and
  the quarter-over-quarter SHARE changes.

It WRITES NOTHING and has no write flag. It never talks to Supabase at all: the company list
comes from ``backend/data/trillion_club_seed.json`` (a built-in list of the 16 members of
2026-09-24 when that file is absent), any Supabase access raises (a tripwire replaces the
client for this process), and the split derivation runs with its Supabase cache disabled.
The FMP key is read from ``backend/.env`` by the app's settings and never printed: every
error line is scrubbed.

``--json FILE`` also writes the preview AND the API payloads the iOS app would receive — the
Home group and each company's detail, full (Pro) and redacted (Free) — assembled by the real
``trillion_club_service`` from an in-memory, read-only copy of the rows (seed + this preview's
membership + these builds). It is for a UI harness; it is not production data.

``--check-m1`` asserts the plan's M1 acceptance figures against the 2026-Q2 builds (NVIDIA 8
positions / $63,439,974,569 with SPCX newly reported and newly listed; Alphabet SPCX newly
listed and Ethos -> LIFE; Amazon XE + ALGT newly reported, NAUT no longer reported; AMD SPCX +
NTNX + CBRS newly reported, MRVL no longer reported) and exits 1 on any miss.

Cost: ~20 history calls + per filer ~1 dates + 2-3 extracts + 1 profile batch (+ a few ISIN
lookups and split-derivation price series) — about 60-80 FMP calls.

Examples (from backend/):
    ./venv/bin/python -m scripts.preview_trillion_club
    ./venv/bin/python -m scripts.preview_trillion_club --check-m1 --period 2026-Q2
    ./venv/bin/python -m scripts.preview_trillion_club --json /tmp/trillion_club_preview.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import math
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

BACKEND = Path(__file__).resolve().parents[1]
SEED_PATH = BACKEND / "data" / "trillion_club_seed.json"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.log_redaction import redact_secrets  # noqa: E402
from app.services.corporate_actions_service import CorporateActionsService  # noqa: E402
from app.services.trillion_club import rules  # noqa: E402
from app.services.trillion_club.builder import (  # noqa: E402
    BuiltFiling,
    FilingRefused,
    FilingUnavailable,
    build_filing,
)
# The job's history request (so the preview asks FMP exactly what it asks) and its pure
# stamp rule for an FMP-sized company — never a run, never the store.
from app.services.trillion_club.jobs import (  # noqa: E402
    HISTORY_LIMIT,
    HISTORY_LOOKBACK_DAYS,
    evaluate_fmp_sized,
)
from app.utils.market_hours import ET, last_completed_close  # noqa: E402

logger = logging.getLogger("preview_trillion_club")

#: The club on 2026-09-24 (research, verified on SEC EDGAR + live FMP). Used ONLY when the
#: seed file does not exist yet; a test pins it against the seed so it cannot drift silently.
#: (slug, display_name, cik or None, card_kind, use_13f, cap_source, cap_symbol, aliases,
#:  home_country, membership_mode)
FALLBACK_COMPANIES: Tuple[Tuple[Any, ...], ...] = (
    ("nvidia", "NVIDIA", "0001045810", "thirteen_f", True, "fmp_us", "NVDA", (), "US", "auto"),
    ("apple", "Apple", "0000320193", "no_thirteen_f", False, "fmp_us", "AAPL", (), "US", "auto"),
    ("alphabet", "Alphabet", "0001652044", "thirteen_f", True, "fmp_us", "GOOGL", ("GOOG",), "US", "auto"),
    ("microsoft", "Microsoft", "0000789019", "no_thirteen_f", False, "fmp_us", "MSFT", (), "US", "auto"),
    ("amazon", "Amazon", "0001018724", "thirteen_f", True, "fmp_us", "AMZN", (), "US", "auto"),
    ("tsmc", "TSMC", "0001046179", "non_us", False, "fmp_adr", "TSM", (), "TW", "auto"),
    ("spacex", "SpaceX", "0001181412", "no_thirteen_f", False, "fmp_us", "SPCX", (), "US", "auto"),
    ("meta", "Meta", "0001326801", "no_thirteen_f", False, "fmp_us", "META", (), "US", "auto"),
    ("broadcom", "Broadcom", "0001730168", "no_thirteen_f", False, "fmp_us", "AVGO", (), "US", "auto"),
    ("tesla", "Tesla", "0001318605", "no_thirteen_f", False, "fmp_us", "TSLA", (), "US", "auto"),
    ("micron", "Micron", "0000723125", "no_thirteen_f", False, "fmp_us", "MU", (), "US", "auto"),
    ("berkshire", "Berkshire Hathaway", "0001067983", "whale_link", False, "fmp_us", "BRK-B", ("BRK-A",), "US", "auto"),
    ("eli-lilly", "Eli Lilly", "0000059478", "no_thirteen_f", False, "fmp_us", "LLY", (), "US", "auto"),
    ("amd", "AMD", "0000002488", "thirteen_f", True, "fmp_us", "AMD", (), "US", "auto"),
    ("saudi-aramco", "Saudi Aramco", None, "non_us", False, "manual", None, (), "SA", "force_in"),
    ("samsung", "Samsung Electronics", "0000879316", "non_us", False, "manual", None, (), "KR", "force_in"),
)

#: M1 acceptance for the 2026-Q2 builds (plan §M1): (slug, description, predicate).
M1_PERIOD = "2026-Q2"


def _change(built: BuiltFiling, symbol: str) -> Optional[Dict[str, Any]]:
    return next((r for r in built.changes.get("rows") or [] if r.get("symbol") == symbol), None)


def _is(built: BuiltFiling, symbol: str, change: str, newly_listed: Optional[bool] = None) -> bool:
    row = _change(built, symbol)
    if row is None or row.get("change") != change:
        return False
    return newly_listed is None or row.get("newly_listed") is newly_listed


M1_CHECKS: Tuple[Tuple[str, str, Callable[[BuiltFiling], bool]], ...] = (
    ("nvidia", "8 positions", lambda b: b.position_count == 8),
    ("nvidia", "$63,439,974,569 reported", lambda b: abs(b.total_value - 63_439_974_569) < 1),
    ("nvidia", "SPCX newly reported + newly listed", lambda b: _is(b, "SPCX", "newly_reported", True)),
    ("alphabet", "SPCX newly reported + newly listed", lambda b: _is(b, "SPCX", "newly_reported", True)),
    ("alphabet", "Ethos (29765A101) resolves to LIFE",
     lambda b: any(h.get("cusip") == "29765A101" and h.get("symbol") == "LIFE" for h in b.holdings)),
    ("amazon", "XE newly reported", lambda b: _is(b, "XE", "newly_reported")),
    ("amazon", "ALGT newly reported", lambda b: _is(b, "ALGT", "newly_reported")),
    ("amazon", "NAUT no longer reported", lambda b: _is(b, "NAUT", "no_longer_reported")),
    ("amd", "SPCX newly reported", lambda b: _is(b, "SPCX", "newly_reported")),
    ("amd", "NTNX newly reported", lambda b: _is(b, "NTNX", "newly_reported")),
    ("amd", "CBRS newly reported", lambda b: _is(b, "CBRS", "newly_reported")),
    ("amd", "MRVL no longer reported", lambda b: _is(b, "MRVL", "no_longer_reported")),
)


# ── Read-only guarantees ───────────────────────────────────────────────────────────────


class SupabaseRefused(RuntimeError):
    """Raised by the tripwire: this preview never reads or writes Supabase."""


class _SupabaseTripwire:
    """Stands in for the process's Supabase client: ANY use raises."""

    def __getattr__(self, name: str) -> Any:
        raise SupabaseRefused(
            f"preview_trillion_club is read-only and Supabase-free: refused client.{name}"
        )


def install_supabase_tripwire() -> None:
    """Make every ``get_supabase()`` in this process return a client that refuses all use.

    ``get_supabase`` returns the module-level singleton once it is set, whatever name the
    caller imported it under, so replacing the singleton covers every call site."""
    import app.database as database

    database._supabase_client = _SupabaseTripwire()


class ReadOnlyCorporateActions(CorporateActionsService):
    """The production split derivation (entitled FMP price series) minus its Supabase cache
    tier: ``corporate_action_cache`` is neither read nor written by a preview."""

    async def _db_get(self, sym, kind, from_date, to_date):  # noqa: D401 - override
        return None

    async def _db_put(self, sym, kind, from_date, to_date, events):
        return None


class _Result:
    def __init__(self, data: List[Dict[str, Any]]):
        self.data = data


class _MemoryQuery:
    """A read-only PostgREST-like query over in-memory rows (select/eq/in_/order/limit)."""

    def __init__(self, rows: List[Dict[str, Any]]):
        self._rows = rows
        self._filters: List[Callable[[Dict[str, Any]], bool]] = []
        self._order: List[Tuple[str, bool]] = []
        self._limit: Optional[int] = None
        self._columns: Optional[List[str]] = None

    def select(self, columns: str = "*") -> "_MemoryQuery":
        cols = [c.strip() for c in str(columns).split(",") if c.strip()]
        self._columns = None if cols in ([], ["*"]) else cols
        return self

    def eq(self, col: str, value: Any) -> "_MemoryQuery":
        self._filters.append(lambda r, c=col, v=value: r.get(c) == v)
        return self

    def in_(self, col: str, values: Sequence[Any]) -> "_MemoryQuery":
        wanted = list(values)
        self._filters.append(lambda r, c=col, w=wanted: r.get(c) in w)
        return self

    def order(self, col: str, desc: bool = False) -> "_MemoryQuery":
        self._order.append((col, bool(desc)))
        return self

    def limit(self, n: int) -> "_MemoryQuery":
        self._limit = int(n)
        return self

    def execute(self) -> _Result:
        rows = [r for r in self._rows if all(f(r) for f in self._filters)]
        for col, desc in reversed(self._order):
            present = [r for r in rows if r.get(col) is not None]
            absent = [r for r in rows if r.get(col) is None]
            rows = sorted(present, key=lambda r: r[col], reverse=desc) + absent
        if self._limit is not None:
            rows = rows[: self._limit]
        if self._columns is not None:
            rows = [{c: r.get(c) for c in self._columns} for r in rows]
        return _Result(json.loads(json.dumps(rows)))      # a copy, like a real response

    def __getattr__(self, name: str) -> Any:
        raise SupabaseRefused(f"read-only preview DB: .{name}() is not supported")


class MemoryDB:
    """In-memory, READ-ONLY tables for assembling the API payloads without Supabase."""

    def __init__(self, tables: Mapping[str, List[Dict[str, Any]]]):
        self._tables = {k: [dict(r) for r in v] for k, v in tables.items()}

    def table(self, name: str) -> "_MemoryTable":
        return _MemoryTable(self._tables.get(name, []))

    def rpc(self, *_a: Any, **_k: Any) -> Any:
        raise SupabaseRefused("read-only preview DB: rpc() refused")


class _MemoryTable:
    def __init__(self, rows: List[Dict[str, Any]]):
        self._rows = rows

    def select(self, columns: str = "*") -> _MemoryQuery:
        return _MemoryQuery(self._rows).select(columns)

    def __getattr__(self, name: str) -> Any:        # insert / update / upsert / delete
        raise SupabaseRefused(f"read-only preview DB: .{name}() refused")


def safe(text: Any, secret: Optional[str] = None) -> str:
    """Redact query-string secrets (``apikey=``) and the literal key, if known."""
    out = redact_secrets(text)
    if secret and len(secret) >= 8:
        out = out.replace(secret, "***")
    return out


class _KeyScrubFilter(logging.Filter):
    def __init__(self, secret: Optional[str]):
        super().__init__()
        self._secret = secret

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
            clean = safe(msg, self._secret)
            if clean != msg:
                record.msg, record.args = clean, ()
            if record.exc_info:
                record.exc_text = safe(logging.Formatter().formatException(record.exc_info), self._secret)
        except Exception:        # a log line must never take the preview down
            record.msg, record.args = "(log line dropped: could not be scrubbed)", ()
        return True


# ── Companies ──────────────────────────────────────────────────────────────────────────


def fallback_company_rows() -> List[Dict[str, Any]]:
    rows = []
    for slug, name, cik, kind, use_13f, cap_source, cap_symbol, aliases, country, mode in FALLBACK_COMPANIES:
        rows.append({
            "slug": slug, "display_name": name, "ciks": [cik] if cik else [], "card_kind": kind,
            "use_13f": use_13f, "cap_symbol": cap_symbol, "symbol_aliases": list(aliases),
            "detail_symbol": cap_symbol if country == "US" else None, "logo_symbol": cap_symbol,
            "home_country": country, "cap_source": cap_source, "manual_cap_usd": None,
            "manual_cap_as_of": None, "manual_cap_source_url": None, "manual_fx_rate": None,
            "manual_fx_source": None, "membership_mode": mode,
            "link_whale": kind == "whale_link", "published": True, "reviewed_on": None,
        })
    return rows


def load_seed(path: Path = SEED_PATH) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], str]:
    """``(company rows, stake rows, source)`` — the seed JSON, else the built-in list."""
    if not path.exists():
        return fallback_company_rows(), [], f"built-in list ({path.name} not found)"
    data = json.loads(path.read_text())
    companies = data.get("companies") if isinstance(data, dict) else None
    stakes = data.get("stakes") if isinstance(data, dict) else None
    if not isinstance(companies, list) or not companies:
        raise SystemExit(f"{path}: no 'companies' list")
    try:
        source = str(path.resolve().relative_to(BACKEND))
    except ValueError:                      # a seed outside backend/ (a copy under review)
        source = str(path)
    return ([dict(c) for c in companies if isinstance(c, dict)],
            [dict(s) for s in stakes or [] if isinstance(s, dict)] if isinstance(stakes, list) else [],
            source)


# ── Membership ─────────────────────────────────────────────────────────────────────────


async def preview_membership(companies: Sequence[Mapping[str, Any]], fmp: Any, now: datetime,
                             secret: Optional[str]) -> List[Dict[str, Any]]:
    now_et = now.astimezone(ET)
    out: List[Dict[str, Any]] = []
    for c in companies:
        slug, mode = c.get("slug"), c.get("membership_mode") or "auto"
        entry: Dict[str, Any] = {"slug": slug, "display_name": c.get("display_name"),
                                 "cap_source": c.get("cap_source"), "cap_symbol": c.get("cap_symbol"),
                                 "mode": mode, "published": c.get("published") is True,
                                 "rows": 0, "error": None, "state": None}
        closes: List[Tuple[Any, Any]] = []
        if c.get("cap_source") != "manual":
            try:
                # The job's exact request (jobs.HISTORY_LOOKBACK_DAYS): without from/to FMP
                # answers with ~3 months whatever the limit.
                history = await fmp.get_historical_market_cap(
                    str(c.get("cap_symbol")),
                    from_date=(now_et.date() - timedelta(days=HISTORY_LOOKBACK_DAYS)).isoformat(),
                    to_date=now_et.date().isoformat(),
                    limit=HISTORY_LIMIT,
                )
            except Exception as e:
                entry["error"] = f"{type(e).__name__}: {safe(e, secret)}"
                out.append(entry)
                continue
            closes = [(r.get("date"), r.get("marketCap")) for r in history or [] if isinstance(r, dict)]
            entry["rows"] = len(closes)
        # An FMP-sized company goes through the job's stamp rule: a force_in / force_out one
        # with no usable close is KEPT by the job (not stamped), so it is shown as such here.
        evaluate = evaluate_fmp_sized if c.get("cap_source") != "manual" else rules.evaluate_membership
        state = evaluate(closes, mode=mode, prior=None, today_et=now_et.date(),
                         now_et=now_et, log_ctx=f"preview slug={slug}")
        if state is None and mode == rules.MODE_AUTO:
            entry["error"] = "no usable answer (fail closed: < 20 usable closes, stale or conflicting)"
        elif state is None:
            entry["error"] = (f"no usable close — the job keeps the stored row unstamped (the "
                              f"owner's {mode} still decides what Home shows)")
        else:
            entry["state"] = {
                "is_member": state.is_member,
                "member_since": state.member_since.isoformat() if state.member_since else None,
                "closes_at_or_above": state.closes_at_or_above,
                "closes_below": state.closes_below,
                "last_market_cap": state.last_cap,
                "last_cap_date": state.last_cap_date.isoformat() if state.last_cap_date else None,
            }
        if c.get("cap_source") == "manual":
            entry["manual_cap_usd"] = c.get("manual_cap_usd")
            entry["manual_cap_as_of"] = c.get("manual_cap_as_of")
        out.append(entry)
    return out


def _usd(v: Any) -> str:
    if not isinstance(v, (int, float)) or isinstance(v, bool) or not math.isfinite(v):
        return "—"
    return f"${v / 1e12:,.3f}T" if v >= 1e12 else f"${v / 1e9:,.1f}B"


def print_membership(rows: Sequence[Mapping[str, Any]], now: datetime) -> None:
    last = last_completed_close(now).astimezone(ET).date()
    print(f"\n━━ Membership — dated closes from historical-market-capitalization "
          f"(last completed session {last.isoformat()}); join 10 straight ≥ $1T, leave 20 below")
    print(f"  {'slug':14} {'symbol':7} {'mode':9} {'member':6} {'since':10}  "
          f"{'last close':>12} {'date':10} {'≥$1T':>5} {'<$1T':>5} {'rows':>4}")
    for r in rows:
        s = r.get("state") or {}
        if r.get("cap_source") == "manual":
            member = "yes" if s.get("is_member") else "no"
            print(f"  {str(r['slug']):14} {'—':7} {r['mode']:9} {member:6} {'':10}  "
                  f"{_usd(r.get('manual_cap_usd')):>12} {str(r.get('manual_cap_as_of') or '—'):10}"
                  f"  (hand-entered cap; the owner's {r['mode']} decides)")
            continue
        if r.get("error"):
            print(f"  {str(r['slug']):14} {str(r.get('cap_symbol')):7} {r['mode']:9} "
                  f"— {r['error']} (rows={r.get('rows')})")
            continue
        print(f"  {str(r['slug']):14} {str(r.get('cap_symbol')):7} {r['mode']:9} "
              f"{'yes' if s.get('is_member') else 'no':6} {str(s.get('member_since') or '—'):10}  "
              f"{_usd(s.get('last_market_cap')):>12} {str(s.get('last_cap_date') or '—'):10} "
              f"{s.get('closes_at_or_above', 0):>5} {s.get('closes_below', 0):>5} {r.get('rows', 0):>4}")


# ── 13F cards ──────────────────────────────────────────────────────────────────────────


async def preview_filings(
    companies: Sequence[Mapping[str, Any]], fmp: Any, actions: Any, today: date, *,
    period: Optional[str], quarters: int, secret: Optional[str],
) -> List[Dict[str, Any]]:
    """Build the newest (or ``period``) quarter and up to ``quarters - 1`` before it, per CIK
    of every ``use_13f`` company. Nothing is written anywhere."""
    wanted = rules.parse_period(period) if period else None
    out: List[Dict[str, Any]] = []
    for c in companies:
        if c.get("use_13f") is not True:
            continue
        for cik in c.get("ciks") or []:
            entry: Dict[str, Any] = {"slug": c.get("slug"), "display_name": c.get("display_name"),
                                     "cik": cik, "builds": [], "error": None}
            out.append(entry)
            try:
                dates = await fmp.get_institutional_filing_dates(cik, strict=True)
            except Exception as e:
                entry["error"] = f"13F dates: {type(e).__name__}: {safe(e, secret)}"
                continue
            listed = set()
            for d in dates if isinstance(dates, list) else []:
                try:
                    listed.add((int(d.get("year")), int(d.get("quarter"))))
                except (AttributeError, TypeError, ValueError):
                    continue
            listed = {p for p in listed if p[1] in (1, 2, 3, 4)}
            if not listed:
                entry["error"] = "FMP lists no 13F quarter for this CIK"
                continue
            newest = wanted if wanted else max(listed)
            if newest not in listed:
                entry["error"] = f"{rules.period_label(*newest)} is not listed by FMP for this CIK"
                continue
            chain = [newest]
            while len(chain) < max(1, quarters):
                prev = rules.previous_quarter(*chain[-1])
                if prev not in listed:
                    break
                chain.append(prev)
            for yq in chain:
                label = rules.period_label(*yq)
                try:
                    built = await build_filing(
                        fmp, cik, yq[0], yq[1],
                        prev_quarter_available=rules.previous_quarter(*yq) in listed,
                        actions=actions, stored_unresolved={}, today=today,
                        older_filing_exists=any(p < yq for p in listed),
                    )
                    row = built.as_row()
                except (FilingRefused, FilingUnavailable, ValueError) as e:
                    entry["builds"].append({"period": label, "error": f"{type(e).__name__}: {safe(e, secret)}"})
                    continue
                except Exception as e:
                    entry["builds"].append({"period": label, "error": f"{type(e).__name__}: {safe(e, secret)}"})
                    logger.error("preview build %s %s failed", cik, label, exc_info=True)
                    continue
                entry["builds"].append({"period": label, "built": built, "row": row,
                                        "degraded_reasons": list(built.degraded_reasons)})
    return out


def _fmt_date(value: Any) -> str:
    if isinstance(value, date):
        return value.strftime("%b %-d, %Y")
    return "—"


def print_filings(entries: Sequence[Mapping[str, Any]]) -> None:
    for e in entries:
        head = f"\n━━ {e['display_name']} (cik {e['cik']})"
        if e.get("error"):
            print(f"{head} — {e['error']}")
            continue
        for b in e["builds"]:
            if b.get("error"):
                print(f"{head} {b['period']} — NOT BUILT: {b['error']}")
                continue
            built: BuiltFiling = b["built"]
            amended = f" · amended {_fmt_date(built.amended_on)}" if built.amended_on else ""
            print(f"{head} — {built.period} [{built.build_status}]  holdings on "
                  f"{_fmt_date(built.period_end)} · filed {_fmt_date(built.filed_on)}{amended}")
            print(f"   {built.position_count} positions · ${built.total_value:,.0f} reported "
                  f"({_usd(built.total_value)}) · {len(built.accessions)} accession(s) "
                  f"{', '.join(built.accessions)} · {built.excluded_rows} row(s) excluded")
            top = " · ".join(f"{h['name']} ({h['symbol'] or h['cusip']}) {h['weight'] * 100:.1f}%"
                             for h in built.holdings[:3])
            print(f"   Top 3: {top}")
            ch = built.changes
            counts = ", ".join(f"{k} {v}" for k, v in (ch.get("counts") or {}).items())
            print(f"   vs {ch.get('prev_period') or '—'} ({ch.get('comparison')}): {counts}")
            for r in ch.get("rows") or []:
                shares = f"{r['shares']:,.0f}" if isinstance(r.get("shares"), (int, float)) else "—"
                prev = f"{r['prev_shares']:,.0f}" if isinstance(r.get("prev_shares"), (int, float)) else "—"
                flag = "  (newly listed)" if r.get("newly_listed") else ""
                print(f"     {r['change']:19} {str(r.get('symbol') or '—'):6} {str(r.get('name'))[:36]:36} "
                      f"shares {prev} -> {shares}{flag}")
            unrouted = [h for h in built.holdings if not h.get("routable")]
            if unrouted:
                print("   Not routable: " + ", ".join(f"{h['name']} ({h['symbol'] or h['cusip']})" for h in unrouted))
            if b.get("degraded_reasons"):
                print(f"   DEGRADED: {'; '.join(b['degraded_reasons'])}")


def run_m1_checks(entries: Sequence[Mapping[str, Any]]) -> List[Tuple[str, str, bool]]:
    by_slug: Dict[str, BuiltFiling] = {}
    for e in entries:
        for b in e.get("builds") or []:
            if b.get("period") == M1_PERIOD and b.get("built") is not None:
                by_slug[str(e["slug"])] = b["built"]
    results = []
    for slug, what, predicate in M1_CHECKS:
        built = by_slug.get(slug)
        try:
            ok = built is not None and bool(predicate(built))
        except Exception:
            ok = False
        results.append((slug, what, ok))
    return results


# ── API payloads for the UI harness ────────────────────────────────────────────────────


def _company_rows_with_membership(companies: Sequence[Mapping[str, Any]],
                                  membership: Sequence[Mapping[str, Any]], now: datetime) -> List[Dict[str, Any]]:
    by_slug = {m["slug"]: m for m in membership}
    out = []
    for c in companies:
        row = dict(c)
        s = (by_slug.get(c.get("slug")) or {}).get("state")
        row.update({
            "is_member": bool(s and s["is_member"]),
            "member_since": s["member_since"] if s else None,
            "closes_at_or_above": s["closes_at_or_above"] if s else 0,
            "closes_below": s["closes_below"] if s else 0,
            "last_market_cap": s["last_market_cap"] if s else None,
            "last_cap_date": s["last_cap_date"] if s else None,
            "membership_checked_at": now.isoformat() if s else None,
        })
        out.append(row)
    return out


async def assemble_api(companies: Sequence[Mapping[str, Any]], stakes: Sequence[Mapping[str, Any]],
                       membership: Sequence[Mapping[str, Any]], filings: Sequence[Mapping[str, Any]],
                       now: datetime) -> Dict[str, Any]:
    """The Home group and every detail, as the real service would serve them from these rows.

    The service reads through ``get_supabase``; for THIS process only it is pointed at a
    read-only in-memory copy, and ``TRILLION_CLUB_ENABLED`` is switched on in memory."""
    from app.config import settings
    from app.services import trillion_club_service as svc_mod
    from app.services.entitlements import required_tier_for_trillion_club_detail

    rows = [b["row"] | {"built_at": now.isoformat()}
            for e in filings for b in e.get("builds") or [] if b.get("row")]
    db = MemoryDB({
        "trillion_club_companies": _company_rows_with_membership(companies, membership, now),
        "trillion_club_stakes": list(stakes),
        "trillion_club_filings": rows,
        "whales": [],
    })
    cls = svc_mod.TrillionClubService
    saved = (svc_mod.get_supabase, settings.TRILLION_CLUB_ENABLED, cls._group_cache,
             cls._detail_cache, cls._invalidated_at)
    svc_mod.get_supabase = lambda: db
    settings.TRILLION_CLUB_ENABLED = True
    cls._group_cache, cls._detail_cache, cls._invalidated_at = {}, {}, 0.0
    try:
        service = cls()
        group = await service.get_group()
        slugs = [c.slug for c in group.companies] + [b.slug for b in group.also_in_club]
        details: Dict[str, Any] = {}
        free_tier = required_tier_for_trillion_club_detail("free")
        for slug in slugs:
            detail = await service.get_detail(slug)
            if detail is None:
                details[slug] = None
                continue
            details[slug] = {
                "pro": detail.model_dump(mode="json"),
                "free": svc_mod.redact_trillion_club_detail(detail, free_tier).model_dump(mode="json"),
            }
    finally:
        # Every global touched above goes back exactly as it was (the preview is one
        # process, but this function is also driven from a test).
        (svc_mod.get_supabase, settings.TRILLION_CLUB_ENABLED, cls._group_cache,
         cls._detail_cache, cls._invalidated_at) = saved
    return {"group": group.model_dump(mode="json"), "details": details,
            "note": "whales rows are not available offline, so the Berkshire card has no whale_id"}


def _json_ready(entries: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    out = []
    for e in entries:
        builds = []
        for b in e.get("builds") or []:
            builds.append({k: v for k, v in b.items() if k != "built"})
        out.append({**{k: v for k, v in e.items() if k != "builds"}, "builds": builds})
    return out


# ── Main ───────────────────────────────────────────────────────────────────────────────


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    parser.add_argument("--json", help="also write the preview + the API payloads to this file")
    parser.add_argument("--period", help="build this quarter (YYYY-Qn) instead of each filer's newest")
    parser.add_argument("--quarters", type=int, default=2,
                        help="quarters to build per filer, newest first (1-4; default 2)")
    parser.add_argument("--check-m1", action="store_true",
                        help=f"assert the plan's M1 acceptance figures on the {M1_PERIOD} builds")
    parser.add_argument("--slug", action="append", help="limit to this company (repeatable)")
    args = parser.parse_args(argv)
    if args.period:
        rules.parse_period(args.period)          # ValueError on a malformed label
    if args.check_m1 and args.period and args.period != M1_PERIOD:
        parser.error(f"--check-m1 checks the {M1_PERIOD} figures; it cannot run with --period {args.period}")
    args.quarters = min(4, max(1, args.quarters))
    return args


async def run_preview(args: argparse.Namespace, *, fmp: Any, actions: Any, secret: Optional[str],
                      now: Optional[datetime] = None, seed_path: Path = SEED_PATH) -> int:
    """Everything after the environment is set up. Returns the exit code (1 = an M1 miss)."""
    companies, stakes, source = load_seed(seed_path)
    if args.slug:
        companies = [c for c in companies if c.get("slug") in set(args.slug)]
    now = now or datetime.now(timezone.utc)
    today = now.astimezone(ET).date()
    print(f"Trillion-Dollar Club preview — {now.astimezone(ET):%Y-%m-%d %H:%M} ET · READ-ONLY "
          f"(live FMP, no Supabase) · companies from {source} ({len(companies)})")

    code = 0
    membership = await preview_membership(companies, fmp, now, secret)
    print_membership(membership, now)
    filings = await preview_filings(companies, fmp, actions, today,
                                    period=args.period or (M1_PERIOD if args.check_m1 else None),
                                    quarters=args.quarters, secret=secret)
    print_filings(filings)
    if args.check_m1:
        print(f"\n━━ M1 acceptance ({M1_PERIOD})")
        results = run_m1_checks(filings)
        for slug, what, ok in results:
            print(f"  {'PASS' if ok else 'FAIL'}  {slug:9} {what}")
        if not all(ok for _, _, ok in results):
            code = 1
    if args.json:
        payload: Dict[str, Any] = {
            "generated_at": now.isoformat(),
            "note": "READ-ONLY preview from live FMP and the seed JSON — not production data",
            "source": source,
            "membership": membership,
            "filings": _json_ready(filings),
        }
        try:
            payload["api"] = await assemble_api(companies, stakes, membership, filings, now)
        except Exception as e:
            payload["api"] = None
            payload["api_error"] = f"{type(e).__name__}: {safe(e, secret)}"
            logger.error("preview: API assembly failed", exc_info=True)
        text = json.dumps(payload, indent=1, sort_keys=True, allow_nan=False)
        if secret and secret in text:
            raise SystemExit("refusing to write the JSON: it contains the FMP key")
        Path(args.json).write_text(text + "\n")
        print(f"\nWrote {args.json} ({len(text):,} bytes)")
    return code


async def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    # `app.config.Settings` reads backend/.env itself (env_file); nothing is copied into
    # os.environ here, so no other secret is exposed to this process's children.
    install_supabase_tripwire()
    from app.config import settings
    from app.integrations.fmp import close_fmp_client, get_fmp_client

    secret = getattr(settings, "FMP_API_KEY", None) or None
    handler = logging.StreamHandler()
    handler.addFilter(_KeyScrubFilter(secret))
    handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    logging.basicConfig(level=logging.WARNING, handlers=[handler], force=True)

    fmp = get_fmp_client()
    try:
        return await run_preview(args, fmp=fmp, actions=ReadOnlyCorporateActions(), secret=secret)
    finally:
        await close_fmp_client()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
