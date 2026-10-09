"""Which registry 13F filers' latest FMP filing has no ADJACENT previous quarter — READ-ONLY.

Both whale 13F writers (`whale_service.WhaleService._process_13f_path`, the live profile path,
and `hydrate_whales.WhaleHydrator._process_13f`, the nightly sweep) read FMP's
`institutional-ownership/dates` list. Until 2026-10-09 they took ``dates[0]`` as the latest
filing and diffed it with the most recent EARLIER entry, so a quarter FMP does not list booked
every share change across the hole as trades of the latest quarter (Norges Bank: 2026-Q2 diffed
with 2025-Q4 — its Q1 and Q3 books are filed under SEC confidential treatment and disclosed a
year later). Since the owner's decision that day (option A) both decide with
`_whale_common.select_13f_comparison` — this script's classifier too — and only the ADJACENT
quarter is ever diffed.

For each 13F filer in backend/data/whale_registry.json this prints the latest quarter, the
quarter it is compared with, and one status:

  adjacent      the quarter before the latest is listed — diffed; trades are written
  gap           only older quarters are listed — NOT compared: holdings only, no trades
                (the quarters missing are named)
  first_filing  nothing older is listed — NOT compared, as a gap
  no_filing     FMP lists nothing usable — the writers keep the stored snapshot

plus notes: ``order`` (``dates[0]`` is not the newest listed quarter, or the list is not
newest-first — the writers take the newest whatever the order), ``malformed`` (rows with no
usable quarter, skipped), ``stale`` (the latest quarter is older than the newest one past its
13F deadline) and the holes in the last ``--history`` quarters (how often FMP's list skips a
quarter for that filer).

``--probe`` additionally asks FMP for the missing ADJACENT quarter's extract of every gap filer
(one more call each): does FMP serve rows for a quarter its own dates list omits?

It writes nothing and has no write flag.

Modes
  --fixture FILE  hermetic: dates lists from a JSON file (see `load_fixture`); no network.
  (default)       live: FMP only, ONE call per filer (45 today; +1 per gap filer with
                  --probe). Supabase is never touched (a tripwire replaces the client); the FMP
                  key is read by the app settings from backend/.env and scrubbed from output.

Examples (from backend/):
    ./venv/bin/python -m scripts.measure_13f_quarter_gaps
    ./venv/bin/python -m scripts.measure_13f_quarter_gaps --probe --json /tmp/gaps.json
    ./venv/bin/python -m scripts.measure_13f_quarter_gaps --cik 0001374170 --probe
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

BACKEND = Path(__file__).resolve().parents[1]
REGISTRY_PATH = BACKEND / "data" / "whale_registry.json"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.log_redaction import redact_secrets  # noqa: E402
from app.utils.period_labels import latest_filed_13f_quarter  # noqa: E402
from app.services._whale_common import (  # noqa: E402
    COMPARISON_GAP,
    COMPARISON_QUARTER,
    select_13f_comparison,
    thirteen_f_dates_row_quarter,
)

logger = logging.getLogger("measure_13f_quarter_gaps")

ADJACENT, GAP, FIRST_FILING, NO_FILING = "adjacent", "gap", "first_filing", "no_filing"
#: the production comparison → this report's status
_STATUS = {COMPARISON_QUARTER: ADJACENT, COMPARISON_GAP: GAP}
STATUSES = (ADJACENT, GAP, FIRST_FILING, NO_FILING)

YQ = Tuple[int, int]


# ── Read-only guarantees ───────────────────────────────────────────────────────────────


class SupabaseRefused(RuntimeError):
    """Raised by the tripwire: this measurement never reads or writes Supabase."""


class _SupabaseTripwire:
    def __getattr__(self, name: str) -> Any:
        raise SupabaseRefused(
            f"measure_13f_quarter_gaps is read-only and Supabase-free: refused client.{name}"
        )


def install_supabase_tripwire() -> None:
    """Every ``get_supabase()`` in this process now returns a client that refuses all use."""
    import app.database as database

    database._supabase_client = _SupabaseTripwire()


def safe(text: Any, secret: Optional[str] = None) -> str:
    """Redact query-string secrets (``apikey=``) and the literal key, if known."""
    out = redact_secrets(str(text))
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
        except Exception as e:
            clean = safe(f"<unformattable log record: {type(e).__name__}: {e}>", self._secret)
        record.msg, record.args = clean, None
        return True


def configure_logging(secret: Optional[str]) -> None:
    handler = logging.StreamHandler()
    handler.addFilter(_KeyScrubFilter(secret))
    handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    logging.basicConfig(level=logging.WARNING, handlers=[handler], force=True)


# ── Classification (pure) ──────────────────────────────────────────────────────────────


def label(yq: Optional[YQ]) -> Optional[str]:
    return f"{yq[0]}-Q{yq[1]}" if yq else None


def previous_quarter(yq: YQ) -> YQ:
    year, quarter = yq
    return (year - 1, 4) if quarter == 1 else (year, quarter - 1)


def quarters_between(older: YQ, newer: YQ) -> List[YQ]:
    """The quarters strictly between ``older`` and ``newer``, oldest first."""
    out: List[YQ] = []
    cur = previous_quarter(newer)
    while cur > older:
        out.append(cur)
        cur = previous_quarter(cur)
    return out[::-1]


def classify(dates: Any, *, now: datetime, history: int = 12) -> Dict[str, Any]:
    """One filer's dates list → the writers' decision (`select_13f_comparison`, the
    production classifier) plus notes. Never raises."""
    rows = dates if isinstance(dates, list) else []
    parsed = [thirteen_f_dates_row_quarter(r) for r in rows]
    seq = [p[0] for p in parsed if p is not None]
    listed = sorted(set(seq), reverse=True)
    selection = select_13f_comparison(rows)

    out: Dict[str, Any] = {
        "listed_count": len(listed),
        "malformed_rows": selection.skipped_rows if selection else len(rows),
        "duplicate_rows": len(seq) - len(listed),
        "latest": selection.period if selection else None,
        "compared_with": label(selection.previous) if selection else None,
        "adjacent": label(selection.adjacent) if selection else None,
        "status": _STATUS.get(selection.comparison, FIRST_FILING) if selection else NO_FILING,
        "missing_quarters": [],
        "notes": [],
    }
    if not isinstance(dates, list):
        out["notes"].append(f"dates is {type(dates).__name__}, not a list")
    if out["malformed_rows"]:
        out["notes"].append(f"malformed: {out['malformed_rows']} row(s) without a usable quarter (skipped)")
    if selection is None:
        return out

    latest = selection.latest
    if out["status"] == GAP and selection.older is not None:
        out["missing_quarters"] = [label(q) for q in quarters_between(selection.older, latest)]

    if seq and seq[0] != latest:
        out["notes"].append(f"order: dates[0] is {label(seq[0])}, the newest listed is {label(latest)}")
    elif any(a < b for a, b in zip(seq, seq[1:])):
        out["notes"].append("order: the list is not newest-first")

    expected = latest_filed_13f_quarter(now=now)
    if latest < expected:
        out["notes"].append(f"stale: newest listed {label(latest)} < {label(expected)} (past its deadline)")

    # Holes in the recent history: quarters inside the span FMP lists that it skips.
    window_floor = latest
    for _ in range(max(0, history - 1)):
        window_floor = previous_quarter(window_floor)
    in_window = [yq for yq in listed if yq >= window_floor]
    if len(in_window) >= 2:
        holes = [q for q in quarters_between(in_window[-1], latest) if q not in listed]
        out["history_holes"] = [label(q) for q in holes]
    else:
        out["history_holes"] = []
    return out


# ── Inputs ─────────────────────────────────────────────────────────────────────────────


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


def load_fixture(path: str) -> List[Dict[str, Any]]:
    """``[{"cik", "name", "dates": [...], "probe"?: int | null}]`` or ``{cik: [dates...]}``."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(data, dict):
        return [{"cik": k, "name": k, "dates": v} for k, v in data.items()]
    if isinstance(data, list):
        return [dict(e) for e in data if isinstance(e, dict)]
    raise ValueError(f"{path}: expected an object or a list")


async def measure_live(
    fmp: Any, filers: Sequence[Mapping[str, str]], *, now: datetime, probe: bool,
    history: int, secret: Optional[str],
) -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []
    for f in filers:
        entry: Dict[str, Any] = {"cik": f["cik"], "name": f["name"]}
        try:
            dates = await fmp.get_institutional_filing_dates(f["cik"], strict=True)
        except Exception as e:
            entry["error"] = safe(f"dates: {type(e).__name__}: {e}", secret)
            logger.warning("CIK %s: %s", f["cik"], entry["error"])
            results.append(entry)
            continue
        entry.update(classify(dates, now=now, history=history))
        if probe and entry["status"] == GAP:
            y, q = (int(x) for x in entry["adjacent"].split("-Q"))
            try:
                rows = await fmp.get_institutional_holdings(f["cik"], y, q, strict=True)
                entry["probe_rows"] = len(rows) if isinstance(rows, list) else None
            except Exception as e:
                entry["probe_error"] = safe(f"{type(e).__name__}: {e}", secret)
        results.append(entry)
    return results


def measure_fixture(entries: Sequence[Mapping[str, Any]], *, now: datetime, history: int) -> List[Dict[str, Any]]:
    out = []
    for e in entries:
        entry: Dict[str, Any] = {"cik": e.get("cik"), "name": e.get("name") or e.get("cik")}
        entry.update(classify(e.get("dates"), now=now, history=history))
        if "probe" in e and entry["status"] == GAP:
            entry["probe_rows"] = e["probe"]
        out.append(entry)
    return out


# ── Report ─────────────────────────────────────────────────────────────────────────────


def render(results: Sequence[Mapping[str, Any]]) -> str:
    lines = [f"{'status':<13} {'latest':<8} {'compared':<11} {'missing':<17} name · CIK"]
    order = {s: i for i, s in enumerate((GAP, FIRST_FILING, NO_FILING, ADJACENT))}
    for r in sorted(results, key=lambda r: (0 if r.get("error") else 1 + order.get(r.get("status"), 9),
                                            r.get("name") or "")):
        if r.get("error"):
            lines.append(f"{'ERROR':<13} {'':<8} {'':<11} {'':<17} {r['name']} · {r['cik']}: {r['error']}")
            continue
        missing = ",".join(r.get("missing_quarters") or []) or "-"
        lines.append(
            f"{r['status']:<13} {r.get('latest') or '-':<8} {r.get('compared_with') or '-':<11} "
            f"{missing:<17} {r['name']} · {r['cik']}"
        )
        extra = list(r.get("notes") or [])
        if r.get("history_holes"):
            extra.append(f"holes in recent history: {', '.join(r['history_holes'])}")
        if "probe_rows" in r:
            extra.append(f"probe: FMP serves {r['probe_rows']} extract row(s) for unlisted {r.get('adjacent')}")
        if r.get("probe_error"):
            extra.append(f"probe failed: {r['probe_error']}")
        lines += [f"{'':<14}↳ {x}" for x in extra]

    ok = [r for r in results if not r.get("error")]
    counts = {s: sum(1 for r in ok if r.get("status") == s) for s in STATUSES}
    lines += [
        "",
        f"{len(results)} filer(s): " + ", ".join(f"{counts[s]} {s}" for s in STATUSES)
        + (f", {len(results) - len(ok)} error(s)" if len(ok) != len(results) else ""),
        f"order notes: {sum(1 for r in ok if any(n.startswith('order') for n in r.get('notes', [])))}"
        f" · stale: {sum(1 for r in ok if any(n.startswith('stale') for n in r.get('notes', [])))}"
        f" · with holes in recent history: {sum(1 for r in ok if r.get('history_holes'))}",
    ]
    return "\n".join(lines)


# ── CLI ────────────────────────────────────────────────────────────────────────────────


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Read-only: 13F filers whose latest FMP quarter has no adjacent previous one.")
    p.add_argument("--fixture", help="hermetic: read dates lists from this JSON file, never FMP")
    p.add_argument("--cik", action="append", default=[], help="live: only this CIK (repeatable)")
    p.add_argument("--limit", type=int, default=0, help="live: at most N filers")
    p.add_argument("--probe", action="store_true",
                   help="live: also fetch each gap filer's missing adjacent extract (1 call each)")
    p.add_argument("--history", type=int, default=12, help="quarters of history scanned for holes")
    p.add_argument("--json", help="also write every result to this file")
    return p.parse_args(argv)


async def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    install_supabase_tripwire()
    now = datetime.now(timezone.utc)
    history = max(2, args.history)
    secret: Optional[str] = None
    if args.fixture:
        configure_logging(None)
        results = measure_fixture(load_fixture(args.fixture), now=now, history=history)
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
                fmp, filers, now=now, probe=args.probe, history=history, secret=secret,
            )
        finally:
            await close_fmp_client()

    print(safe(render(results), secret))
    if args.json:
        text = json.dumps(results, indent=1, sort_keys=True, default=str)
        if secret and secret in text:
            raise SystemExit("refusing to write the JSON: it contains the FMP key")
        Path(args.json).write_text(text + "\n", encoding="utf-8")
        print(f"\nWrote {args.json} ({len(text):,} bytes)")
    return 1 if any(r.get("error") for r in results) else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
