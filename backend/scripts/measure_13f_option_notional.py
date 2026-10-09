"""What the share-only 13F holdings rule removed, filer by filer — READ-ONLY.

Owner decision 2026-10-09 (option A). Until then both whale holdings builders —
`whale_service.WhaleService._build_holdings` (live profile path) and
`hydrate_whales.WhaleHydrator._build_13f_holdings` (nightly sweep) — summed EVERY FMP extract
row per symbol. A 13F reports an option at the value of its UNDERLYING shares, so a put or call
row added that notional, not what the option is worth (and a put is a bet the other way). The
"13F Equity Portfolio" figure (`whales.portfolio_value`), the Current Holdings with their
allocation and change_percent, the AI behaviour / sentiment text fed the top holdings, and the
diffs' allocation denominators (`whale_trades.previous_allocation` / `new_allocation`, the Home
Whale Accumulation drill-down's weight) all inherited it. Both builders now return
`_whale_common.thirteen_f_holdings`: the share positions the diffs already read (put/call and
non-SH rows dropped, per-manager rows summed, the latest accession wins — the row rules of
`trillion_club.builder.normalize_rows`).

For each filer this puts BEFORE (the every-row rule, frozen below as `all_rows_holdings`)
beside NOW (the production builders) on its latest two 13F extracts, so the owner can see what
the change moves before and after it ships, and that the two writers agree. It writes nothing
and has no write flag.

Modes
  --fixture FILE  hermetic: filings from a JSON file (see `load_fixture`); no network at all.
  (default)       live: every 13F filer in backend/data/whale_registry.json, against FMP only.
                  Supabase is never touched (a tripwire replaces the client); the FMP key is
                  read by the app settings from backend/.env and scrubbed from every line.
                  Cost: ~4 FMP calls per filer (dates, two extracts, the holder-performance
                  summary), about 180 for the 45 registry filers.

Not reproduced: split restatement (no split lookups are made, so a ticker that split between
the two quarters can produce a trade production would suppress — only the trades' allocation
COLUMNS are compared), logo / name enrichment, and the sector chart (it comes from FMP's
holder-industry-breakdown, which the change does not touch; the holder-performance probe says
whether FMP's own aggregates count option notional — the closest a script gets to that chart's
basis).

Examples (from backend/):
    ./venv/bin/python -m scripts.measure_13f_option_notional \\
        --fixture tests/fixtures/thirteen_f_options/synthetic_option_books.json
    ./venv/bin/python -m scripts.measure_13f_option_notional
    ./venv/bin/python -m scripts.measure_13f_option_notional --cik 0001649339 --json /tmp/opts.json
"""
from __future__ import annotations

import argparse
import asyncio
import copy
import json
import logging
import math
import statistics
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

BACKEND = Path(__file__).resolve().parents[1]
REGISTRY_PATH = BACKEND / "data" / "whale_registry.json"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.log_redaction import redact_secrets  # noqa: E402
from app.services._whale_common import (  # noqa: E402
    format_amount_short,
    is_13f_non_share_row,
    select_13f_comparison,
    thirteen_f_share_positions,
)
from app.services.trillion_club.rules import (  # noqa: E402
    accession_from_link,
    parse_period,
    quarter_end,
)
from app.services.whale_service import WhaleService  # noqa: E402
from scripts.hydrate_whales import WhaleHydrator  # noqa: E402

logger = logging.getLogger("measure_13f_option_notional")

#: `whale_holdings` and the profile keep the first 30 holdings (`holdings[:30]`).
TOP_CAP = 30
#: Summary buckets: options at least this share of the BEFORE figure.
MATERIAL_PCT = 10.0
DOMINANT_PCT = 50.0
#: FMP's holder-performance `marketValue` "matches" a total within this relative distance…
BASIS_TOLERANCE = 0.01
#: …and the two totals are told apart only when they are at least this % apart.
BASIS_MIN_GAP_PCT = 2.0

SHARE, PUT, CALL, PRINCIPAL = "share", "put", "call", "principal"
KINDS = (SHARE, PUT, CALL, PRINCIPAL)


# ── Read-only guarantees ───────────────────────────────────────────────────────────────


class SupabaseRefused(RuntimeError):
    """Raised by the tripwire: this measurement never reads or writes Supabase."""


class _SupabaseTripwire:
    """Stands in for the process's Supabase client: ANY use raises."""

    def __getattr__(self, name: str) -> Any:
        raise SupabaseRefused(
            f"measure_13f_option_notional is read-only and Supabase-free: refused client.{name}"
        )


def install_supabase_tripwire() -> None:
    """Every ``get_supabase()`` in this process now returns a client that refuses all use
    (it returns the module singleton once set, whatever name the caller imported it by)."""
    import app.database as database

    database._supabase_client = _SupabaseTripwire()


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
            clean = safe(record.getMessage(), self._secret)
        except Exception as e:  # a record that cannot even format is still shown, scrubbed
            clean = safe(f"<unformattable log record: {type(e).__name__}: {e}>", self._secret)
        record.msg, record.args = clean, None
        return True


def configure_logging(secret: Optional[str]) -> None:
    handler = logging.StreamHandler()
    handler.addFilter(_KeyScrubFilter(secret))
    handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    logging.basicConfig(level=logging.WARNING, handlers=[handler], force=True)


# ── Row reading ────────────────────────────────────────────────────────────────────────


def row_kind(row: Mapping[str, Any]) -> str:
    """``put`` / ``call`` (an explicit ``putCallShare``), ``principal`` (an explicit
    non-``SH`` ``sharesType`` such as PRN), else ``share`` — `is_13f_non_share_row`'s
    reading, split by kind. A missing type is a share row, as the share positions read it."""
    put_call = str(row.get("putCallShare") or "").strip().lower()
    if put_call in (PUT, CALL):
        return put_call
    return PRINCIPAL if is_13f_non_share_row(row) else SHARE


def _finite(value: Any) -> float:
    try:
        f = float(value)
    except (TypeError, ValueError, OverflowError):
        return 0.0
    return f if math.isfinite(f) else 0.0


def _row_symbol(row: Mapping[str, Any]) -> str:
    """The every-row rule's key, ``(symbol or tickercusip or "").upper()`` (no strip), so the
    composition below adds up to exactly the BEFORE figure."""
    return str(row.get("symbol") or row.get("tickercusip") or "").upper()


def _dict_rows(raw: Any) -> List[Dict[str, Any]]:
    return [r for r in raw if isinstance(r, dict)] if isinstance(raw, (list, tuple)) else []


def composition(rows: Iterable[Mapping[str, Any]]) -> Dict[str, Dict[str, float]]:
    """``{ticker: {kind: value}}`` over exactly the rows the every-row rule COUNTED: a
    finite value above 0 and a symbol that is neither empty nor ``--``."""
    out: Dict[str, Dict[str, float]] = {}
    for r in rows:
        val = _finite(r.get("value"))
        if val <= 0:
            continue
        sym = _row_symbol(r)
        if not sym or sym == "--":
            continue
        out.setdefault(sym, dict.fromkeys(KINDS, 0.0))[row_kind(r)] += val
    return out


def accession_count(rows: Iterable[Mapping[str, Any]]) -> int:
    """Distinct filings among the rows (``link`` / ``finalLink`` accession, else the filing
    date) — more than one means FMP folded a 13F-HR/A in beside the original."""
    keys = set()
    for r in rows:
        acc = accession_from_link(r.get("link")) or accession_from_link(r.get("finalLink"))
        keys.add(acc or f"~unknown:{str(r.get('filingDate') or '')[:10]}")
    return len(keys)


# ── The two readings ───────────────────────────────────────────────────────────────────


def all_rows_holdings(rows: Iterable[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    """BEFORE: the rule both holdings builders applied until 2026-10-09, frozen here only to
    measure what the change removed — every row with a finite value above 0 summed per
    symbol (put/call notional and principal lines included, across every accession),
    allocation to 2 dp of that total, largest first."""
    merged: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        val = _finite(r.get("value"))
        if val <= 0:
            continue
        sym = _row_symbol(r)
        if not sym or sym == "--":
            continue
        h = merged.setdefault(sym, {"ticker": sym, "value": 0.0})
        h["value"] += val
    total = sum(h["value"] for h in merged.values())
    if total <= 0:
        return []
    out = list(merged.values())
    for h in out:
        h["allocation"] = round(h["value"] / total * 100, 2)
    out.sort(key=lambda h: (-h["value"], h["ticker"]))
    return out


def production_holdings(rows: List[Dict[str, Any]]) -> Tuple[List[Dict], List[Dict]]:
    """NOW: ``(live, nightly)`` — the two production builders, run on copies."""
    live = WhaleService._build_holdings(WhaleService.__new__(WhaleService), copy.deepcopy(rows))
    nightly = WhaleHydrator._build_13f_holdings(
        WhaleHydrator.__new__(WhaleHydrator), copy.deepcopy(rows)
    )
    return live, nightly


def _builders_disagree(live: List[Dict], nightly: List[Dict]) -> List[str]:
    a = {h["ticker"]: (round(h["value"], 2), h["allocation"]) for h in live}
    b = {h["ticker"]: (round(h["value"], 2), h["allocation"]) for h in nightly}
    return sorted(t for t in set(a) | set(b) if a.get(t) != b.get(t))


# ── Measurements ───────────────────────────────────────────────────────────────────────


def _pct(part: float, whole: float) -> Optional[float]:
    return round(part / whole * 100, 2) if whole > 0 else None


def _mix(comp: Mapping[str, float]) -> Dict[str, float]:
    total = sum(comp.values())
    return {k: (round(comp.get(k, 0.0) / total * 100, 1) if total > 0 else 0.0) for k in KINDS}


def _top(holdings: List[Dict], comp: Mapping[str, Mapping[str, float]], n: int) -> List[Dict]:
    out = []
    for rank, h in enumerate(holdings[:n], 1):
        row = {"rank": rank, "ticker": h["ticker"], "allocation": h["allocation"], "value": h["value"]}
        if comp:
            row["mix"] = _mix(comp.get(h["ticker"]) or {SHARE: 1.0})
        out.append(row)
    return out


def fmp_market_value_basis(
    performance: Any, period_end: Optional[str], all_rows_total: float, share_total: float,
) -> Dict[str, Any]:
    """Which total FMP's holder-performance ``marketValue`` for THIS quarter matches.

    ``includes_options`` / ``shares_only`` when it lands within ``BASIS_TOLERANCE`` of one
    total and the two totals are at least ``BASIS_MIN_GAP_PCT`` apart (closer than that they
    cannot be told apart: ``indistinguishable``); ``neither`` when it matches neither;
    ``no_row`` when there is no usable row dated to this quarter end."""
    entry = None
    for p in performance if isinstance(performance, list) else []:
        if isinstance(p, dict) and period_end and str(p.get("date") or "")[:10] == period_end:
            entry = p
            break
    mv = _finite(entry.get("marketValue")) if entry else 0.0
    if mv <= 0:
        return {"basis": "no_row", "market_value": None}
    gap_pct = _pct(all_rows_total - share_total, all_rows_total) or 0.0

    def _near(x: float) -> bool:
        return x > 0 and abs(mv - x) / x <= BASIS_TOLERANCE

    if gap_pct < BASIS_MIN_GAP_PCT and (_near(all_rows_total) or _near(share_total)):
        basis = "indistinguishable"
    elif _near(all_rows_total):
        basis = "includes_options"
    elif _near(share_total):
        basis = "shares_only"
    else:
        basis = "neither"
    return {"basis": basis, "market_value": mv}


def _change_percents(
    cur: List[Dict], prev: List[Dict], live: List[Dict], nightly: List[Dict],
) -> Dict[str, Any]:
    """`change_percent` (allocation-point move vs the previous quarter): the live writer
    against the nightly one NOW (they must agree), and NOW against the BEFORE rule, over the
    stored top 30."""
    hyd = WhaleHydrator.__new__(WhaleHydrator)
    nightly_cp = {
        h["ticker"]: h["change_percent"]
        for h in hyd._calculate_change_percent(
            copy.deepcopy(nightly), hyd._build_13f_holdings(copy.deepcopy(prev))
        )
    }
    svc = WhaleService.__new__(WhaleService)
    live_cp = {
        h["ticker"]: h["change_percent"]
        for h in svc._apply_change_percent(copy.deepcopy(live), copy.deepcopy(prev))
    }
    before_prev = {h["ticker"]: h["allocation"] for h in all_rows_holdings(prev)}
    before_cp = {
        h["ticker"]: round(h["allocation"] - before_prev.get(h["ticker"], 0.0), 2)
        for h in all_rows_holdings(cur)
    }
    stored = [h["ticker"] for h in nightly[:TOP_CAP]]
    vs_before = max(
        ((abs(nightly_cp.get(t, 0.0) - before_cp[t]), t) for t in stored if t in before_cp),
        default=(0.0, None),
    )
    live_vs_nightly = max(
        ((abs(live_cp.get(t, 0.0) - nightly_cp.get(t, 0.0)), t) for t in stored), default=(0.0, None)
    )
    return {
        "max_shift_vs_before": {"points": round(vs_before[0], 2), "ticker": vs_before[1]},
        "live_vs_nightly_now": {"points": round(live_vs_nightly[0], 2), "ticker": live_vs_nightly[1]},
        "now_nightly": {t: nightly_cp.get(t) for t in stored},
        "before": {t: before_cp.get(t) for t in stored},
    }


def _trade_allocations(
    cur: List[Dict], prev: List[Dict], period_end: str, now_total: float, before_total: float,
    before_prev_total: float,
) -> Dict[str, Any]:
    """The production nightly diff's ``new_allocation`` (share-book denominators, as the
    writers pass them) against the same trades over the BEFORE every-row totals. No split
    lookups."""
    hyd = WhaleHydrator.__new__(WhaleHydrator)
    group = hyd._diff_quarters(
        copy.deepcopy(cur), copy.deepcopy(prev), period_end, now_total, {}, set()
    )
    trades = (group or {}).get("trades") or []
    cur_pos = thirteen_f_share_positions(cur)
    prev_pos = thirteen_f_share_positions(prev)
    prev_share_total = sum(p["value"] for p in prev_pos.values() if p["value"] > 0)

    def _alloc(pos: Mapping[str, Dict], total: float, t: str) -> float:
        v = (pos.get(t) or {}).get("value", 0.0)
        return round(v / total * 100, 2) if total > 0 and v > 0 else 0.0

    deltas = []
    for t in trades:
        before = _alloc(cur_pos, before_total, t["ticker"])
        deltas.append((abs((t.get("new_allocation") or 0.0) - before), t["ticker"],
                       before, t.get("new_allocation")))
    worst = max(deltas, key=lambda d: (d[0], d[1]), default=(0.0, None, None, None))
    return {
        "trades": len(trades),
        "denominator_ratio_current": (round(before_total / now_total, 2) if now_total > 0 else None),
        "denominator_ratio_previous": (
            round(before_prev_total / prev_share_total, 2) if prev_share_total > 0 else None
        ),
        "max_new_allocation_shift": {
            "points": round(worst[0], 2), "ticker": worst[1],
            "before": worst[2], "now": worst[3],
        },
        "median_new_allocation_shift": (
            round(statistics.median(d[0] for d in deltas), 2) if deltas else 0.0
        ),
    }


def measure_filing(
    current_raw: Any,
    previous_raw: Any = None,
    *,
    period_end: Optional[str] = None,
    performance: Any = None,
    top: int = 5,
) -> Dict[str, Any]:
    """Every number the change moves for ONE filer's latest 13F, BEFORE vs NOW. Pure; never
    raises on a malformed row (non-dict rows are counted and left out)."""
    cur = _dict_rows(current_raw)
    prev = _dict_rows(previous_raw)
    before = all_rows_holdings(cur)
    live, nightly = production_holdings(cur)
    now = live
    comp = composition(cur)

    before_total = sum(h["value"] for h in before)
    now_total = sum(h["value"] for h in now)
    by_kind = {k: sum(c[k] for c in comp.values()) for k in KINDS}
    options = by_kind[PUT] + by_kind[CALL]

    kinds = [row_kind(r) for r in cur]
    rows = {k: kinds.count(k) for k in KINDS}
    rows.update(
        total=len(current_raw) if isinstance(current_raw, (list, tuple)) else 0,
        non_dict=(len(current_raw) - len(cur)) if isinstance(current_raw, (list, tuple)) else 0,
        untyped=sum(
            1 for r, k in zip(cur, kinds)
            if k == SHARE and not str(r.get("sharesType") or "").strip()
        ),
        accessions=accession_count(cur),
    )

    before_alloc = {h["ticker"]: h["allocation"] for h in before}
    now_alloc = {h["ticker"]: h["allocation"] for h in now}
    before_top = [h["ticker"] for h in before[:TOP_CAP]]
    now_top = [h["ticker"] for h in now[:TOP_CAP]]
    option_only = [
        {"rank": i, "ticker": t, "allocation": before_alloc[t]}
        for i, t in enumerate(before_top, 1)
        if t not in now_alloc
    ]
    option_heavy = []
    for i, t in enumerate(before_top, 1):
        mix = _mix(comp.get(t) or {})
        if t in now_alloc and mix[PUT] + mix[CALL] > 50.0:
            option_heavy.append({"rank": i, "ticker": t, "allocation": before_alloc[t],
                                 "option_pct": round(mix[PUT] + mix[CALL], 1)})
    shifts = [
        (round(now_alloc.get(t, 0.0) - before_alloc.get(t, 0.0), 2), t)
        for t in set(before_top) | set(now_top)
    ]
    big = max(shifts, key=lambda s: (abs(s[0]), s[1]), default=(0.0, None))

    out: Dict[str, Any] = {
        "rows": rows,
        "value": {
            "before_total": before_total,
            "now_total": now_total,
            "change_pct": _pct(now_total - before_total, before_total),
            **{k: by_kind[k] for k in KINDS},
            "option_notional": options,
            "option_pct": _pct(options, before_total) or 0.0,
            "principal_pct": _pct(by_kind[PRINCIPAL], before_total) or 0.0,
            # Share rows the every-row rule summed across BOTH an original and its 13F-HR/A,
            # which the share positions read once (latest accession wins per CUSIP).
            "amendment_overlap": round(by_kind[SHARE] - now_total, 2),
        },
        "builders_disagree": _builders_disagree(live, nightly),
        "top_before": _top(before, comp, top),
        "top_now": _top(now, {}, top),
        "top_holding": {
            "before": _top(before, comp, 1)[0] if before else None,
            "now": _top(now, {}, 1)[0] if now else None,
        },
        "option_only_in_top30": option_only,
        "option_heavy_in_top30": option_heavy,
        "top30_left": [t for t in before_top if t not in now_top],
        "top30_entered": [t for t in now_top if t not in before_top],
        "max_allocation_shift": {
            "ticker": big[1], "points": big[0],
            "before": before_alloc.get(big[1], 0.0) if big[1] else None,
            "now": now_alloc.get(big[1], 0.0) if big[1] else None,
        },
        "change_percent": None,
        "trade_allocations": None,
        "fmp_market_value": fmp_market_value_basis(performance, period_end, before_total, now_total),
    }
    th = out["top_holding"]
    th["changes"] = bool((th["before"] or {}).get("ticker") != (th["now"] or {}).get("ticker"))
    if prev:
        out["change_percent"] = _change_percents(cur, prev, live, nightly)
        out["trade_allocations"] = _trade_allocations(
            cur, prev, period_end or "", now_total, before_total,
            sum(h["value"] for h in all_rows_holdings(prev)),
        )
    return out


# ── Sources ────────────────────────────────────────────────────────────────────────────


def _period_end_of(period: Optional[str], rows: Sequence[Any]) -> Optional[str]:
    if period:
        try:
            return quarter_end(*parse_period(period)).isoformat()
        except ValueError:
            logger.warning("fixture period %r is not a 13F period label — reading the rows' date", period)
    for r in rows:
        if isinstance(r, dict) and r.get("date"):
            return str(r["date"])[:10]
    return None


def load_fixture(path: str) -> List[Dict[str, Any]]:
    """Filings from JSON, in either of two shapes:

    * ``{"filers": [{"name", "cik", "period", "previous_period", "current": [rows],
      "previous": [rows], "performance": [rows]?}]}`` — the measurement's own shape;
    * ``{"<cik>": {"YYYY-Qn": [rows], ...}, "_meta": {"filers": {name: cik}}}`` — the recorded
      FMP extracts under ``tests/fixtures/trillion_club/`` (newest period = current, the one
      before it = previous)."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(data, dict) and isinstance(data.get("filers"), list):
        out = []
        for f in data["filers"]:
            cur = f.get("current") or []
            out.append({
                "name": f.get("name") or f.get("cik") or "?",
                "cik": f.get("cik") or "",
                "period": f.get("period"),
                "previous_period": f.get("previous_period"),
                "period_end": _period_end_of(f.get("period"), cur),
                "current": cur,
                "previous": f.get("previous") or [],
                "performance": f.get("performance"),
            })
        return out
    if isinstance(data, dict):
        names = {cik: name for name, cik in ((data.get("_meta") or {}).get("filers") or {}).items()}
        out = []
        for cik, periods in data.items():
            if cik.startswith("_") or not isinstance(periods, dict):
                continue
            keys = []
            for k in periods:
                try:
                    keys.append((parse_period(k), k))
                except ValueError:
                    logger.warning("fixture %s: CIK %s key %r is not a 13F period — skipped", path, cik, k)
            keys.sort()
            if not keys:
                continue
            cur_key = keys[-1][1]
            prev_key = keys[-2][1] if len(keys) > 1 else None
            out.append({
                "name": names.get(cik, cik),
                "cik": cik,
                "period": cur_key,
                "previous_period": prev_key,
                "period_end": _period_end_of(cur_key, periods[cur_key]),
                "current": periods[cur_key],
                "previous": periods[prev_key] if prev_key else [],
                "performance": None,
            })
        return out
    raise ValueError(f"{path}: not a recognised fixture shape (expected an object)")


def registry_13f_filers(ciks: Sequence[str] = (), limit: int = 0) -> List[Dict[str, str]]:
    """The registry's 13F filers (`data_source == "13f"`), optionally narrowed to ``ciks``."""
    rows = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    wanted = {c.strip().zfill(10) for c in ciks if c and c.strip()}
    out = []
    for r in rows if isinstance(rows, list) else []:
        cik = str(r.get("cik") or "").strip()
        if r.get("data_source") != "13f" or not cik:
            continue
        if wanted and cik.zfill(10) not in wanted:
            continue
        firm, person = (r.get("firm_name") or "").strip(), (r.get("name") or "").strip()
        out.append({"cik": cik, "name": f"{firm} — {person}" if firm else person})
    return out[:limit] if limit > 0 else out


async def fetch_filing(fmp: Any, cik: str, *, performance: bool = True) -> Optional[Dict[str, Any]]:
    """The latest 13F and the one before it, chosen exactly as the writers choose them
    (`select_13f_comparison`: the newest listed quarter, compared only with the ADJACENT
    one — since 2026-10-09). ``None`` when FMP lists no filing. An extract or dates failure
    RAISES (strict fetches): a measurement on a missing quarter is wrong."""
    dates = await fmp.get_institutional_filing_dates(cik, strict=True)
    if not dates:
        return None
    selection = select_13f_comparison(dates)
    if selection is None:
        logger.warning("CIK %s: FMP's dates name no usable quarter — nothing to measure", cik)
        return None
    year, quarter = selection.latest
    prev = selection.previous
    calls = [fmp.get_institutional_holdings(cik, year, quarter, strict=True)]
    if prev:
        calls.append(fmp.get_institutional_holdings(cik, prev[0], prev[1], strict=True))
    else:
        logger.warning(
            "CIK %s: %s-Q%s has no adjacent previous quarter on file (%s) — no comparisons",
            cik, year, quarter, selection.comparison,
        )
    if performance:
        calls.append(fmp.get_institutional_performance(cik))
    results = await asyncio.gather(*calls, return_exceptions=True)
    current = results[0]
    if isinstance(current, BaseException):
        raise current
    previous: Any = []
    if prev:
        previous = results[1]
        if isinstance(previous, BaseException):
            raise previous
        if not previous:
            # The writers refuse this quarter outright (an empty prior book would book every
            # position as new); the measurement keeps the current-quarter figures only.
            logger.warning(
                "CIK %s: the %s-Q%s extract came back EMPTY although FMP lists it — "
                "quarter-over-quarter comparisons skipped", cik, prev[0], prev[1],
            )
    perf = results[-1] if performance else None
    if isinstance(perf, BaseException):
        logger.warning(
            "holder-performance probe failed for CIK %s: %s: %s", cik, type(perf).__name__, perf
        )
        perf = None
    period = f"{year}-Q{quarter}"
    return {
        "period": period,
        "previous_period": f"{prev[0]}-Q{prev[1]}" if prev else None,
        "period_end": selection.latest_date or _period_end_of(period, current),
        "current": current,
        "previous": previous,
        "performance": perf,
    }


def measure_entry(entry: Mapping[str, Any], *, top: int = 5) -> Dict[str, Any]:
    out = {k: entry.get(k) for k in ("name", "cik", "period", "previous_period", "period_end")}
    try:
        out["measure"] = measure_filing(
            entry.get("current"), entry.get("previous"),
            period_end=entry.get("period_end"), performance=entry.get("performance"), top=top,
        )
    except Exception as e:
        logger.exception("measurement failed for %s (CIK %s)", entry.get("name"), entry.get("cik"))
        out["error"] = f"{type(e).__name__}: {e}"
    return out


async def measure_live(
    fmp: Any, filers: Sequence[Mapping[str, str]], *, secret: Optional[str], top: int = 5,
    performance: bool = True,
) -> List[Dict[str, Any]]:
    results = []
    for f in filers:
        try:
            filing = await fetch_filing(fmp, f["cik"], performance=performance)
        except Exception as e:
            logger.error(
                "13F fetch failed for %s (CIK %s): %s: %s",
                f["name"], f["cik"], type(e).__name__, safe(e, secret),
            )
            results.append({**f, "error": f"{type(e).__name__}: {safe(e, secret)}"})
            continue
        if filing is None:
            results.append({**f, "error": "FMP lists no 13F filing for this CIK"})
            continue
        results.append(measure_entry({**f, **filing}, top=top))
    return results


# ── Report ─────────────────────────────────────────────────────────────────────────────


def _money(v: Optional[float]) -> str:
    return "—" if v is None else format_amount_short(v)


def _mix_tag(mix: Optional[Mapping[str, float]]) -> str:
    if not mix:
        return ""
    parts = [f"{k} {mix[k]:.0f}%" for k in (PUT, CALL, PRINCIPAL) if mix.get(k)]
    return f" [{' · '.join(parts)}]" if parts else ""


def render_filer(r: Mapping[str, Any], top: int = 5) -> List[str]:
    who = str(r.get("name") or "?")
    if r.get("cik") and r.get("cik") != who:
        who += f" · CIK {r['cik']}"
    head = f"━━ {who} · {r.get('period') or '?'}"
    if r.get("previous_period"):
        head += f" vs {r['previous_period']}"
    if r.get("error"):
        return [head, f"   ERROR: {r['error']}"]
    m = r["measure"]
    v, rows = m["value"], m["rows"]
    lines = [
        head,
        f"   rows {rows['total']}: share {rows[SHARE]} · put {rows[PUT]} · call {rows[CALL]} · "
        f"principal {rows[PRINCIPAL]} · no type {rows['untyped']}"
        + (f" · non-dict {rows['non_dict']}" if rows["non_dict"] else "")
        + f" · {rows['accessions']} accession(s)",
        f"   portfolio figure before (every row) {_money(v['before_total'])} → now (share "
        f"positions) {_money(v['now_total'])}"
        + (f" ({v['change_pct']:+.1f}%)" if v["change_pct"] is not None else ""),
        f"     the before figure = shares {_money(v[SHARE])} · puts {_money(v[PUT])} · calls "
        f"{_money(v[CALL])} · principal {_money(v[PRINCIPAL])} — options {v['option_pct']:.1f}%",
    ]
    if v["amendment_overlap"] > 0.5:
        lines.append(
            f"     ⚠ {_money(v['amendment_overlap'])} of share rows were counted twice before "
            f"(the same CUSIP in an original 13F and its 13F-HR/A)"
        )
    if m["builders_disagree"]:
        lines.append(
            "     ⚠ the live and nightly builders DISAGREE now on: "
            + ", ".join(m["builders_disagree"][:10])
        )
    th = m["top_holding"]
    t0, s0 = th["before"], th["now"]
    lines.append(
        "   #1 before: "
        + (f"{t0['ticker']} {t0['allocation']:.2f}%{_mix_tag(t0.get('mix'))}" if t0 else "—")
        + " → now: "
        + (f"{s0['ticker']} {s0['allocation']:.2f}%" if s0 else "— (no share positions)")
        + ("" if th["changes"] else "  (same holding)")
    )
    lines.append(
        f"   top {top} before: "
        + (" · ".join(f"{h['ticker']} {h['allocation']:.2f}%{_mix_tag(h.get('mix'))}"
                      for h in m["top_before"]) or "—")
    )
    lines.append(
        f"   top {top} now: "
        + (" · ".join(f"{h['ticker']} {h['allocation']:.2f}%" for h in m["top_now"]) or "—")
    )
    if m["option_only_in_top30"]:
        oo = m["option_only_in_top30"]
        lines.append(
            f"   held only through options/principal, gone from the top {TOP_CAP}: {len(oo)} "
            f"(Σ {sum(o['allocation'] for o in oo):.1f}% of the old list) — "
            + ", ".join(f"{o['ticker']} #{o['rank']} {o['allocation']:.2f}%" for o in oo[:8])
            + (" …" if len(oo) > 8 else "")
        )
    if m["option_heavy_in_top30"]:
        lines.append(
            "   mostly-option tickers that keep a share position: "
            + ", ".join(
                f"{o['ticker']} #{o['rank']} ({o['option_pct']:.0f}% option)"
                for o in m["option_heavy_in_top30"][:8]
            )
        )
    big = m["max_allocation_shift"]
    lines.append(
        f"   top-{TOP_CAP}: {len(m['top30_left'])} leave, {len(m['top30_entered'])} enter"
        + (
            f" · largest allocation move {big['ticker']} {big['before']:.2f}% → "
            f"{big['now']:.2f}% ({big['points']:+.2f} pts)"
            if big["ticker"] and big["points"] else " · no allocation moves"
        )
    )
    cp = m.get("change_percent")
    if cp:
        vs, ln = cp["max_shift_vs_before"], cp["live_vs_nightly_now"]
        lines.append(
            f"   change_percent: moves up to {vs['points']:.2f} pts vs before"
            + (f" ({vs['ticker']})" if vs["ticker"] and vs["points"] else "")
            + f" · live vs nightly writer now differ by {ln['points']:.2f} pts"
            + (f" ({ln['ticker']})" if ln["ticker"] and ln["points"] else "")
        )
    if r.get("previous_period") and not cp:
        lines.append("   previous quarter: no rows — quarter-over-quarter comparisons skipped")
    ta = m.get("trade_allocations")
    if ta and ta["trades"]:
        w = ta["max_new_allocation_shift"]
        ratio = ta["denominator_ratio_current"]
        lines.append(
            f"   trade weights: {ta['trades']} trade(s) · the before denominator was "
            + (f"{ratio:.2f}×" if ratio is not None else "—")
            + " today's"
            + (
                f" · largest new_allocation move {w['ticker']} {w['before']:.2f}% → "
                f"{w['now']:.2f}%"
                if w["ticker"] and w["points"] else " · no weight moves"
            )
        )
    basis = m["fmp_market_value"]
    label = {
        "includes_options": "matches the every-row figure (its aggregates count option notional)",
        "shares_only": "matches the share-position figure (its aggregates leave options out)",
        "indistinguishable": "options too small here to tell the two totals apart",
        "neither": "matches neither total",
        "no_row": "no row for this quarter",
    }[basis["basis"]]
    lines.append(
        "   holder-performance marketValue"
        + (f" {_money(basis['market_value'])}" if basis["market_value"] else "")
        + f": {label}"
    )
    return lines


def summarize(results: Sequence[Mapping[str, Any]]) -> List[str]:
    ok = [r for r in results if r.get("measure")]
    failed = [r for r in results if r.get("error")]
    with_options = [r for r in ok if r["measure"]["value"]["option_notional"] > 0]
    material = [r for r in ok if r["measure"]["value"]["option_pct"] >= MATERIAL_PCT]
    dominant = [r for r in ok if r["measure"]["value"]["option_pct"] >= DOMINANT_PCT]
    flips = [r for r in ok if r["measure"]["top_holding"]["changes"]]
    top10_option_only = [
        r for r in ok if any(o["rank"] <= 10 for o in r["measure"]["option_only_in_top30"])
    ]
    overlap = [r for r in ok if r["measure"]["value"]["amendment_overlap"] > 0.5]
    disagree = [r for r in ok if r["measure"]["builders_disagree"]]
    writers_differ = [
        r for r in ok
        if (r["measure"].get("change_percent") or {}).get("live_vs_nightly_now", {}).get("points")
    ]
    before = sum(r["measure"]["value"]["before_total"] for r in ok)
    now = sum(r["measure"]["value"]["now_total"] for r in ok)
    options = sum(r["measure"]["value"]["option_notional"] for r in ok)
    bases: Dict[str, int] = {}
    for r in ok:
        b = r["measure"]["fmp_market_value"]["basis"]
        bases[b] = bases.get(b, 0) + 1

    def _flip(r: Mapping[str, Any]) -> str:
        th = r["measure"]["top_holding"]
        return (f"{r['name']} ({(th['before'] or {}).get('ticker', '—')} → "
                f"{(th['now'] or {}).get('ticker', '—')})")

    lines = [
        f"━━ SUMMARY — {len(ok)} filer(s) measured, {len(failed)} failed",
        f"   with put/call rows: {len(with_options)} · options ≥ {MATERIAL_PCT:.0f}% of the "
        f"before figure: {len(material)} · ≥ {DOMINANT_PCT:.0f}%: {len(dominant)}",
        f"   #1 holding changes: {len(flips)}" + (" — " + "; ".join(_flip(r) for r in flips) if flips else ""),
        f"   an options-only ticker was in the old top 10: {len(top10_option_only)} filer(s)",
        f"   Σ portfolio figure before {_money(before)} → now {_money(now)}"
        + (f" ({(now - before) / before * 100:+.1f}%)" if before > 0 else "")
        + f" · Σ option notional removed {_money(options)}",
        f"   share rows counted twice across a 13F-HR/A before: {len(overlap)} filer(s)"
        + (" — " + ", ".join(str(r["name"]) for r in overlap) if overlap else ""),
        "   holder-performance marketValue: "
        + " · ".join(f"{k} {v}" for k, v in sorted(bases.items())),
    ]
    if disagree:
        lines.append(
            "   ⚠ live and nightly holdings builders disagree for: "
            + ", ".join(str(r["name"]) for r in disagree)
        )
    if writers_differ:
        lines.append(
            "   ⚠ live and nightly change_percent differ for: "
            + ", ".join(str(r["name"]) for r in writers_differ)
        )
    if failed:
        lines.append("   failed: " + ", ".join(f"{r.get('name')} ({r.get('error')})" for r in failed))
    return lines


def render(results: Sequence[Mapping[str, Any]], top: int = 5) -> str:
    lines: List[str] = []
    for r in results:
        lines += render_filer(r, top) + [""]
    lines += summarize(results)
    return "\n".join(lines)


# ── CLI ────────────────────────────────────────────────────────────────────────────────


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Read-only: what the share-only 13F holdings rule changed for each whale.",
    )
    p.add_argument("--fixture", help="hermetic: read filings from this JSON file, never FMP")
    p.add_argument("--cik", action="append", default=[], help="live: only this CIK (repeatable)")
    p.add_argument("--limit", type=int, default=0, help="live: at most N filers")
    p.add_argument("--top", type=int, default=5, help="holdings listed per filer (default 5)")
    p.add_argument(
        "--no-performance", action="store_true",
        help="live: skip the holder-performance probe (one FMP call per filer)",
    )
    p.add_argument("--json", help="also write every measurement to this file")
    return p.parse_args(argv)


async def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    install_supabase_tripwire()
    top = max(1, args.top)
    secret: Optional[str] = None
    if args.fixture:
        configure_logging(None)
        results = [measure_entry(e, top=top) for e in load_fixture(args.fixture)]
    else:
        # `app.config.Settings` reads backend/.env itself; nothing is copied into os.environ.
        from app.config import settings
        from app.integrations.fmp import close_fmp_client, get_fmp_client

        secret = getattr(settings, "FMP_API_KEY", None) or None
        configure_logging(secret)
        filers = registry_13f_filers(args.cik, args.limit)
        if not filers:
            logger.error("no 13F filer in %s matches %s", REGISTRY_PATH, args.cik or "the registry")
            return 1
        fmp = get_fmp_client()
        try:
            results = await measure_live(
                fmp, filers, secret=secret, top=top, performance=not args.no_performance,
            )
        finally:
            await close_fmp_client()

    print(render(results, top=top))
    if args.json:
        text = json.dumps(results, indent=1, sort_keys=True, allow_nan=False, default=str)
        if secret and secret in text:
            raise SystemExit("refusing to write the JSON: it contains the FMP key")
        Path(args.json).write_text(text + "\n", encoding="utf-8")
        print(f"\nWrote {args.json} ({len(text):,} bytes)")
    return 1 if any(r.get("error") for r in results) else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
