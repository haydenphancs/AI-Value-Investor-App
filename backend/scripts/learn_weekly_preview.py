#!/usr/bin/env python
"""Write one Wiser weekly piece OFFLINE and print it — the owner's look at real drafts before the
engine is ever switched on (plan "Wiser weekly content engine", go-live step 3).

    cd backend && ./venv/bin/python scripts/learn_weekly_preview.py \\
        --tickers CRWV,NVDA,MSFT --theme silicon-rush --label "AI infrastructure" \\
        [--as-of 2026-10-22] [--kind deep_dive] [--category blueprints] [--out /tmp/learn-preview]

WHAT IT TOUCHES
---------------
* Reads PUBLIC data only: SEC EDGAR (company tickers, submissions, XBRL facts, 8-K text — needs
  SEC_EDGAR_USER_AGENT) and the bundled `backend/data/money_moves.json` (served titles, related
  tiles). It calls the writer model with the Gemini key from backend/.env (a few cents a run).
* It NEVER touches the database: before anything runs, every `get_supabase` binding under `app.`
  is replaced by a function that raises, so a code path that would read or write production fails
  loudly instead (`PreviewDatabaseBlocked`). No Telegram, no Storage, no ledger row. FMP is not
  used either: the topic is given on the command line instead of scored.
* Writes only into `--out` (default: a fresh temp directory): sheet.json, draft.json, draft.txt
  (the article as a reader sees it), report.json (validator issues, shadow-judge verdicts,
  tokens, model calls).

`--as-of` picks the week whose 8-Ks become the "why now" peg facts (default: today, ET). A week with
no usable 8-K for the given companies yields no peg fact — the real topic step would drop that
candidate; the preview stops and says so.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tempfile
import uuid
from dataclasses import asdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

BUNDLE = _BACKEND / "data" / "money_moves.json"


class PreviewDatabaseBlocked(RuntimeError):
    """A preview code path tried to reach Supabase. The preview never reads or writes the DB."""


def _blocked_supabase(*_a: Any, **_k: Any) -> Any:
    raise PreviewDatabaseBlocked(
        "learn_weekly_preview never touches the database — a code path asked for get_supabase()")


def block_database() -> List[str]:
    """Replace `get_supabase` in `app.database` and in EVERY already-imported `app.*` module that
    bound it at import time (a `from app.database import get_supabase` copies the function, so
    patching the source alone would leave those bindings live — testing.md "patch the binding the
    caller actually uses"). Returns the patched module names."""
    import app.database as database

    patched = []
    database.get_supabase = _blocked_supabase
    patched.append("app.database")
    for name, module in list(sys.modules.items()):
        if name.startswith("app.") and getattr(module, "get_supabase", None) is not None \
                and module is not database:
            setattr(module, "get_supabase", _blocked_supabase)
            patched.append(name)
    return patched


def _bundle_articles() -> List[Dict[str, Any]]:
    data = json.loads(BUNDLE.read_text(encoding="utf-8"))
    return [a for a in data.get("articles", []) if isinstance(a, dict)]


def readable_text(content: Dict[str, Any]) -> str:
    """The article as a reader sees it, block by block (for draft.txt)."""
    lines = [content.get("title", ""), content.get("subtitle", ""), ""]
    for h in content.get("keyHighlights") or []:
        lines.append(f"  • {h.get('title')}: {h.get('description')}")
    for s in content.get("statistics") or []:
        lines.append(f"  # {s.get('value')} — {s.get('label')} ({s.get('trend')})")
    for section in content.get("sections") or []:
        lines += ["", f"## {section.get('title')}"]
        for block in section.get("content") or []:
            kind = block.get("type")
            if kind == "bulletList":
                lines += [f"  - {item}" for item in block.get("items") or []]
            elif kind == "subheading":
                lines.append(f"### {block.get('text')}")
            elif kind == "callout":
                lines.append(f"[{block.get('style')}] {block.get('text')}")
            else:
                lines.append(str(block.get("text")))
    return "\n".join(lines).strip() + "\n"


async def preview(args: argparse.Namespace, *, gemini: Any = None, sec: Any = None,
                  out_dir: Optional[Path] = None) -> Dict[str, Any]:
    """The whole preview. `gemini` / `sec` are injectable for the test."""
    from app.config import settings
    from app.services.learn_weekly import build, contracts as C, signals, sec_facts, topic, writer
    from app.services.learn_weekly import topic_scorer

    block_database()   # AFTER the imports, so every module-level binding exists to be patched

    if gemini is None:
        from app.integrations.gemini import get_gemini_client
        gemini = get_gemini_client()
    if sec is None and not settings.SEC_EDGAR_USER_AGENT:
        raise SystemExit("SEC_EDGAR_USER_AGENT is blank — the SEC client refuses every call")

    as_of = date.fromisoformat(args.as_of) if args.as_of else datetime.now(timezone.utc).date()
    tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()][:3]
    if not tickers:
        raise SystemExit("--tickers needs at least one ticker")

    mapping = await signals.ticker_cik_map(sec=sec)
    subjects = []
    for t in tickers:
        cik = signals.cik_for(t, mapping)
        if cik is None:
            print(f"  ! {t}: no CIK in SEC's company_tickers.json — left out")
            continue
        summary = await sec_facts.company_summary(cik, client=sec)
        if summary is None or summary.foreign_filer:
            print(f"  ! {t}: {'no SEC record' if summary is None else 'foreign filer (20-F/40-F)'} "
                  "— left out (never a main subject)")
            continue
        subjects.append((C.Subject(name=summary.name, cik=summary.cik, ticker=t), summary))
    if not subjects:
        raise SystemExit("no eligible main subject — nothing to write about")

    run = topic._Run(period_key=f"preview:{as_of.isoformat()}", llm=gemini, sec=sec)
    pegs = await topic._peg_facts(run, subjects, as_of, cand_key="preview")
    if not pegs:
        raise SystemExit(f"no verified peg fact from the week ending {as_of} "
                         f"({run.drops.get('preview', 'no 8-K')}) — the real topic step would drop "
                         "this candidate; try another --as-of or other tickers")
    kind_theme = topic_scorer.KIND_THEME if args.theme else "ticker"
    cand = C.TopicCandidate(kind=kind_theme, key=args.theme or tickers[0],
                            title=args.label or args.theme or tickers[0], score=0.0,
                            tickers=tuple(s.ticker for s, _ in subjects))
    category = args.category or "blueprints"
    label = args.label or await topic._label(run, cand, [s for s, _ in subjects], category, [],
                                             args.theme or subjects[0][0].name)
    candidate = {**cand.to_json(), "label": label, "category": category,
                 "subjects": [asdict(s) for s, _ in subjects],
                 "peg_facts": [asdict(f) for f in pegs]}
    sheet = await build.assemble_fact_sheet(args.kind, candidate)

    catalog = _bundle_articles()
    outcome = await writer.write_article(
        sheet=sheet, kind=args.kind, category=category, topic_label=label,
        served_titles=[a.get("title", "") for a in catalog], in_flight_titles=[],
        generation_id=str(uuid.uuid4()), before_call=_always, client=gemini,
        published_date=as_of, catalog=catalog,
        served_slugs=[a.get("slug", "") for a in catalog])

    out = out_dir or Path(args.out or tempfile.mkdtemp(prefix="learn-preview-"))
    out.mkdir(parents=True, exist_ok=True)
    (out / "sheet.json").write_text(json.dumps(sheet.to_json(), indent=2, ensure_ascii=False))
    report = {
        "status": outcome.status, "error": outcome.error, "label": label, "category": category,
        "tokens_used": outcome.tokens_used, "model_calls": outcome.calls,
        "peg_facts": [f.text for f in pegs], "issues": outcome.issues, "judge": outcome.judge,
        "topic_llm_calls": run.llm_calls,
    }
    (out / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str))
    if outcome.content:
        (out / "draft.json").write_text(json.dumps(outcome.content, indent=2, ensure_ascii=False))
        (out / "draft.txt").write_text(readable_text(outcome.content), encoding="utf-8")
    print(f"\n{outcome.status.upper()} — {label} ({category}); {outcome.tokens_used} tokens, "
          f"{outcome.calls} writer calls; files in {out}")
    for issue in (outcome.issues or [])[:30]:
        print(f"  - {issue}")
    return report


async def _always() -> bool:
    return True


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Offline preview of one Wiser weekly piece (writes nothing).")
    p.add_argument("--tickers", required=True, help="up to 3 main subjects, e.g. CRWV,NVDA,MSFT")
    p.add_argument("--theme", default=None, help="Emerging Frontiers theme slug (its primers join the sheet)")
    p.add_argument("--label", default=None, help="topic label (else the model labels it)")
    p.add_argument("--kind", default="deep_dive", choices=["deep_dive", "slot"])
    p.add_argument("--category", default=None, choices=["blueprints", "valueTraps", "battles"])
    p.add_argument("--as-of", default=None, help="YYYY-MM-DD: the week whose 8-Ks become the peg")
    p.add_argument("--out", default=None, help="output directory (default: a new temp dir)")
    return p.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    report = asyncio.run(preview(parse_args(argv)))
    return 0 if report.get("status") == "accepted" else 1


if __name__ == "__main__":
    raise SystemExit(main())
