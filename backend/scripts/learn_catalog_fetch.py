#!/usr/bin/env python
"""Bring a catalog article's narrator render home: the audio file and its read-along timings.

⚠️ READS PRODUCTION (`learn_narration_jobs`, the private `money-moves-media` bucket) with the service-role
key in backend/.env. It writes only LOCAL files — and, without `--yes`, nothing at all.

    cd backend && ./venv/bin/python scripts/learn_catalog_fetch.py --slug coca-cola-vs-pepsico
    cd backend && ./venv/bin/python scripts/learn_catalog_fetch.py --slug coca-cola-vs-pepsico \\
        [--article data/money_moves_drafts/coca-cola-vs-pepsico.json] [--force] --yes

WHEN TO USE IT
--------------
After `scripts/learn_catalog_narrate.py --yes` queued the article and the owner's Telegram said "Catalog
narration READY" (owner 2026-11-01: render the hand-written catalog articles on the Railway narrator,
not on the Mac). Then the authoring session's `data/money_moves_drafts/integrate.py` appends the article
to both money_moves.json, and `seed_money_moves.py` uploads the clip and sets the catalog `audioUrl`.

WHAT IT DOES
------------
1. Picks the newest READY job of the slug (migration 207: `catalog_slug` set, no piece).
2. Checks, before anything is written (pure):
   * the article's narration still hashes to the job's `script_sha256` — the text was not edited after
     the render (a changed transcript drifts the highlight; re-narrate instead);
   * `narration.merge_timings` merges the job's timings — the server's own timing gate runs again —
     giving `readAlong` / `itemsReadAlong`, `audioDurationSeconds` and `hasAudioVersion`, and NO
     `audioUrl` (the weekly public URL form is never written into a catalog article; the seed sets the
     catalog's own);
   * every pause between two sentences of one block is under 3.0 s (the catalog suite's
     `test_the_catalogs_pauses_fit_under_the_silence_cap` bound) and every sentence's seconds per word
     sits in the server's band (`test_the_catalogs_own_narration_passes_the_band`);
   * the file's bytes per second and the pace sit in the catalog voice bands
     (`test_money_moves_catalog_parity.py`: 11,500–14,000 B/s, 144–166 WPM).
3. With --yes: downloads the object BYTE FOR BYTE (never re-encoded) and verifies its size, its sha256
   against the content-addressed path and its ffprobe duration; writes
   `backend/data/money_moves_audio/<slug>.m4a` (refusing to overwrite a different file unless --force);
   then writes the merged article back to the article file (the same `indent=1` form the drafts use).
   An article read from the bundle is written to `data/money_moves_drafts/<slug>.json` instead — the
   bundle is never edited here.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

DRAFTS_DIR = _BACKEND / "data" / "money_moves_drafts"
BUNDLE = _BACKEND.parent / "frontend" / "ios" / "ios" / "Resources" / "MoneyMoves" / "money_moves.json"
AUDIO_DIR = _BACKEND / "data" / "money_moves_audio"
#: Mirrors of tests/test_money_moves_catalog_parity.py's voice bands (pinned equal by
#: tests/test_learn_catalog_scripts.py): checked HERE so a bad file never reaches integrate.
CLONE_BYTES_PER_SEC = (11_500, 14_000)
CLONE_WPM = (144.0, 166.0)
#: The catalog suite's bound on a pause between two sentences of one block
#: (`narration.MAX_SILENCE_SECONDS / 2`).
MAX_IN_BLOCK_PAUSE_SECONDS = 3.0
#: ffprobe's duration may differ from the worker's report by encoder priming / rounding.
DURATION_TOLERANCE_SECONDS = 0.5
_PROBE_TIMEOUT = 60


def load_article(slug: str, article: Optional[Path] = None) -> Tuple[Dict[str, Any], Path, bool]:
    """(article, where it was read, whether it came from a bundle) — `learn_catalog_narrate.load_article`'s
    rules. SystemExit when it is missing, unreadable or another article."""
    candidates = [article] if article is not None else [DRAFTS_DIR / f"{slug}.json", BUNDLE]
    for path in candidates:
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            raise SystemExit(f"{path}: unreadable JSON ({type(e).__name__}: {e})")
        if isinstance(data, dict) and isinstance(data.get("articles"), list):
            found = [a for a in data["articles"] if isinstance(a, dict) and a.get("slug") == slug]
            if len(found) == 1:
                return found[0], path, True
            if article is not None:
                raise SystemExit(f"{path}: holds {len(found)} articles with slug {slug!r}")
            continue
        if not isinstance(data, dict):
            raise SystemExit(f"{path}: not an article object")
        if data.get("slug") != slug:
            raise SystemExit(f"{path} is the article {data.get('slug')!r}, not {slug!r}")
        return data, path, False
    raise SystemExit(f"no article {slug!r} in {', '.join(str(p) for p in candidates)}")


def pick_job(jobs: Iterable[Mapping[str, Any]], slug: str) -> Tuple[Dict[str, Any], List[str]]:
    """(the newest READY job of `slug`, warnings). SystemExit when none is ready, naming every round."""
    from app.services.learn_weekly import contracts as C

    rows = [dict(j) for j in jobs if j.get("catalog_slug") == slug and j.get("piece_id") is None]
    ready = [j for j in rows if j.get("status") == C.JOB_READY]

    def rnd(j: Mapping[str, Any]) -> int:
        r = j.get("round")
        return r if isinstance(r, int) and not isinstance(r, bool) else -1

    if not ready:
        seen = ", ".join(f"round {j.get('round')}: {j.get('status')}"
                         + (f" ({str(j.get('error'))[:120]})" if j.get("error") else "") for j in rows)
        raise SystemExit(f"{slug}: no READY narration job ({seen or 'no job at all'}) — "
                         f"scripts/learn_catalog_narrate.py --slug {slug} queues one")
    job = max(ready, key=rnd)
    later = [j for j in rows if rnd(j) > rnd(job)]
    warnings = [f"round {j.get('round')} is {j.get('status')} — fetching the newest READY round {job.get('round')}"
                for j in later]
    return job, warnings


def _spans(article: Mapping[str, Any]) -> Iterable[List[Mapping[str, Any]]]:
    for section in article.get("sections") or []:
        for block in section.get("content") or []:
            if block.get("readAlong"):
                yield block["readAlong"]
            for spans in block.get("itemsReadAlong") or []:
                if spans:
                    yield spans


def read_along_problems(article: Mapping[str, Any]) -> List[str]:
    """Pauses and paces the catalog suite would refuse, on the ROUNDED spans the bundle stores."""
    from app.services.learn_weekly import narration

    lo, hi = narration.SPW_BAND
    out: List[str] = []
    for spans in _spans(article):
        for x in spans:
            spw = (x["end"] - x["start"]) / narration.spoken_words(x["text"])
            if not lo <= spw <= hi:
                out.append(f"'{x['text'][:60]}' is {spw:.3f} s/word (band {lo}–{hi})")
        for x, y in zip(spans, spans[1:]):
            gap = y["start"] - x["end"]
            if gap >= MAX_IN_BLOCK_PAUSE_SECONDS:
                out.append(f"a {gap:.2f} s pause before '{y['text'][:60]}' (cap {MAX_IN_BLOCK_PAUSE_SECONDS} s)")
    return out


def voice_band_problems(article: Mapping[str, Any], audio_bytes: int) -> List[str]:
    """The catalog parity suite's two fingerprints, from the file size and the authored duration."""
    from app.services.learn_weekly import narration

    duration = article.get("audioDurationSeconds")
    if not isinstance(duration, int) or isinstance(duration, bool) or duration <= 0:
        return [f"audioDurationSeconds is {duration!r}"]
    out = []
    bps = audio_bytes / duration
    if not CLONE_BYTES_PER_SEC[0] <= bps <= CLONE_BYTES_PER_SEC[1]:
        out.append(f"{bps:,.0f} B/s outside the catalog voice band {CLONE_BYTES_PER_SEC}")
    words = sum(len(b.text.split()) for b in narration.narration_blocks(article))
    wpm = words / duration * 60
    if not CLONE_WPM[0] <= wpm <= CLONE_WPM[1]:
        out.append(f"{wpm:.1f} WPM outside the catalog voice band {CLONE_WPM}")
    return out


def plan_fetch(article: Mapping[str, Any], job: Mapping[str, Any], slug: str) -> Dict[str, Any]:
    """{"article": the merged article, "path", "sha12", "bytes", "duration"} — or SystemExit naming why the
    render must not be used. Pure: nothing is downloaded or written."""
    from app.services.learn_weekly import contracts as C
    from app.services.learn_weekly import narration
    from app.services.learn_weekly.errors import LearnNarratorRequestInvalid

    if job.get("status") != C.JOB_READY or job.get("piece_id") is not None or job.get("catalog_slug") != slug:
        raise SystemExit(f"job {job.get('id')} is not a READY catalog job of {slug}")
    path = job.get("audio_path")
    m = narration._CATALOG_AUDIO_RE.fullmatch(path) if isinstance(path, str) else None
    if m is None or m.group(1) != slug:
        raise SystemExit(f"job {job.get('id')} has audio_path {path!r}, not a catalog object of {slug}")
    size, duration = job.get("audio_bytes"), job.get("duration_seconds")
    if isinstance(size, bool) or not isinstance(size, int) or size <= 0:
        raise SystemExit(f"job {job.get('id')} has an unreadable audio_bytes {size!r}")
    if isinstance(duration, bool) or not isinstance(duration, (int, float)) or not 0 < float(duration):
        raise SystemExit(f"job {job.get('id')} has an unreadable duration_seconds {duration!r}")
    try:
        blocks = narration.blocks_from_script(job.get("script"), expected_sha256=job.get("script_sha256"))
    except ValueError as e:
        raise SystemExit(f"job {job.get('id')}: {e}")
    try:
        current = narration.script_sha256(narration.narration_blocks(article))
    except ValueError as e:
        raise SystemExit(f"the article cannot be narrated: {e}")
    if current != job.get("script_sha256"):
        raise SystemExit(f"the article's text changed after round {job.get('round')} was rendered (script "
                         f"{current[:12]} ≠ the job's {str(job.get('script_sha256'))[:12]}) — the audio would not "
                         f"match the transcript; re-narrate: scripts/learn_catalog_narrate.py --slug {slug}")
    try:
        merged = narration.merge_timings(article, blocks, job.get("timings"), duration_seconds=float(duration),
                                         audio_url=None)
    except (LearnNarratorRequestInvalid, ValueError) as e:
        raise SystemExit(f"the job's timings do not merge: {type(e).__name__}: {e}")
    url = merged.get("audioUrl")
    if isinstance(url, str) and ("/audio/weekly/" in url or "/audio/catalog/" in url):
        merged["audioUrl"] = None      # a narrator object is never the catalog's audio URL
    added = set(merged) - set(article)
    if not added <= {"audioDurationSeconds", "hasAudioVersion"}:
        raise SystemExit(f"the merge added unexpected top-level keys {sorted(added)}")
    problems = read_along_problems(merged) + voice_band_problems(merged, size)
    if problems:
        raise SystemExit("the render does not fit the catalog:\n  " + "\n  ".join(problems))
    return {"article": merged, "path": path, "sha12": m.group(2), "bytes": size, "duration": float(duration)}


def check_download(data: bytes, plan: Mapping[str, Any]) -> None:
    """The downloaded bytes are the job's object: its registered size and the sha256 its path names."""
    if len(data) != plan["bytes"]:
        raise SystemExit(f"downloaded {len(data)} bytes; the job registered {plan['bytes']}")
    sha = hashlib.sha256(data).hexdigest()
    if sha[:12] != plan["sha12"]:
        raise SystemExit(f"the download hashes to {sha[:12]}…, not the {plan['sha12']} its path names")


def check_duration(probed: float, plan: Mapping[str, Any]) -> None:
    if abs(probed - plan["duration"]) > DURATION_TOLERANCE_SECONDS:
        raise SystemExit(f"ffprobe reads {probed:.2f} s; the job reported {plan['duration']:.2f} s")


def article_out_path(source: Path, from_bundle: bool, slug: str) -> Path:
    """Where the merged article goes: back to its own file, or — read from a bundle — a new drafts file
    (the bundle is never edited here). SystemExit rather than overwrite an unrelated drafts file."""
    if not from_bundle:
        return source
    out = DRAFTS_DIR / f"{slug}.json"
    if out.exists():
        raise SystemExit(f"{slug} was read from the bundle but {out} exists — pass --article {out}")
    return out


def dump_article(article: Mapping[str, Any]) -> str:
    """The drafts' own serialisation (indent=1, UTF-8, no trailing newline), so a diff shows only the
    narration fields."""
    return json.dumps(article, indent=1, ensure_ascii=False)


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


# ── I/O seams (tests replace these) ──────────────────────────────────────────────────────────


def _jobs(slug: str) -> List[Dict[str, Any]]:
    from app.services.learn_weekly import ledger

    return ledger.list_catalog_jobs_sync(slug)


def _download(path: str) -> bytes:
    from app.database import get_supabase
    from app.services.learn_weekly import narration

    data = get_supabase().storage.from_(narration.AUDIO_BUCKET).download(path)
    if not isinstance(data, (bytes, bytearray)) or not data:
        raise SystemExit(f"downloading {narration.AUDIO_BUCKET}/{path} returned nothing")
    return bytes(data)


def _probe_duration(path: Path) -> float:
    try:
        done = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of",
                               "default=noprint_wrappers=1:nokey=1", str(path)],
                              capture_output=True, text=True, timeout=_PROBE_TIMEOUT, check=False)
    except FileNotFoundError:
        raise SystemExit("ffprobe is not installed (brew install ffmpeg) — the duration check needs it")
    if done.returncode != 0:
        raise SystemExit(f"ffprobe failed ({done.returncode}): {done.stderr.strip()[:300]}")
    try:
        return float(done.stdout.strip())
    except ValueError:
        raise SystemExit(f"ffprobe printed an unreadable duration {done.stdout.strip()[:60]!r}")


def main(argv: Optional[Sequence[str]] = None) -> int:
    p = argparse.ArgumentParser(description="Download a catalog narration and merge its timings (local writes).")
    p.add_argument("--slug", required=True, help="e.g. coca-cola-vs-pepsico")
    p.add_argument("--article", type=Path, default=None,
                   help="the article JSON (default: data/money_moves_drafts/<slug>.json, else the bundle)")
    p.add_argument("--force", action="store_true", help="overwrite a DIFFERENT local clip")
    p.add_argument("--yes", action="store_true", help="actually download and write (default: print only)")
    args = p.parse_args(argv)

    from app.services.learn_weekly import ledger

    try:
        ledger.validate_catalog_slug(args.slug)
    except ValueError as e:
        raise SystemExit(str(e))
    article, source, from_bundle = load_article(args.slug, args.article)
    try:
        jobs = _jobs(args.slug)
    except ledger.LearnLedgerError as e:
        raise SystemExit(f"could not read the narration ledger (is migration 207 applied?): {e}")
    job, warnings = pick_job(jobs, args.slug)
    for w in warnings:
        print(f"⚠️  {w}")
    plan = plan_fetch(article, job, args.slug)
    out_article = article_out_path(source, from_bundle, args.slug)
    clip = AUDIO_DIR / f"{args.slug}.m4a"
    merged = plan["article"]
    print(f"{args.slug}: job {job.get('id')} round {job.get('round')} — {plan['path']} ({plan['bytes']} bytes, "
          f"{plan['duration']:.1f} s → audioDurationSeconds {merged['audioDurationSeconds']})")
    print(f"  audio → {clip}{' (exists)' if clip.exists() else ''}")
    print(f"  article → {out_article}")
    if not args.yes:
        print("dry run — nothing downloaded or written (add --yes)")
        return 0

    data = _download(plan["path"])
    check_download(data, plan)
    # Probe a scratch copy first: a file that fails a check never lands in money_moves_audio/.
    with tempfile.TemporaryDirectory(prefix="learn-catalog-fetch-") as tmp:
        scratch = Path(tmp) / f"{args.slug}.m4a"
        scratch.write_bytes(data)
        check_duration(_probe_duration(scratch), plan)
    existing = clip.read_bytes() if clip.exists() else None
    if existing == data:
        print(f"  {clip} already holds these exact bytes — not rewritten")
    elif existing is not None and not args.force:
        raise SystemExit(f"{clip} exists with DIFFERENT bytes ({len(existing)} vs {len(data)}) — refusing to "
                         "overwrite (pass --force after checking which one is right)")
    else:
        _atomic_write(clip, data)
    if clip.read_bytes() != data:
        raise SystemExit(f"{clip} does not read back as the downloaded bytes")
    _atomic_write(out_article, dump_article(merged).encode("utf-8"))
    print(f"wrote {clip} ({len(data)} bytes, sha256 {hashlib.sha256(data).hexdigest()[:12]}…) and the merged "
          f"article {out_article}. Next: data/money_moves_drafts/integrate.py, the tests, then the seed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
