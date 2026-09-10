"""The FMP-derived universe files — ONE resolver, and one place they can be sourced from.

`benchmark_universe.json` (5,704 tickers with per-ticker market caps) and
`industry_universe.json` (9,188) are built from FMP's `available-industries` +
`company-screener`. FMP ToS §2.6.1 bars distributing data "derived from The Services", so
they must not live in a public git repo — see `documents/legal/GIT_HISTORY_PURGE_RUNBOOK.md`.

This module is what lets them leave the repo without breaking the app:

  * **One path resolver.** There were FOUR different idioms for the same directory —
    `parents[2]` in three services, `parents[3]` in the report collector (it sits one level
    deeper), and `REPO / "backend" / "data"` in the scripts. Nothing shared a constant, so
    moving the files meant finding every one of them by hand.
  * **One fetch.** When the file is absent locally it is pulled once from a private Supabase
    Storage bucket and cached to disk, mirroring how Learn narration already lives in
    Storage rather than in git.

⚠️ **Degradation is deliberately LOUD but not fatal.** All four readers already swallow a
missing file and `return []`, which means a failed fetch silently empties the industry
benchmark, moat, dossier and competitor-peer surfaces — the exact "never swallow silently"
violation CLAUDE.md forbids. Nothing here raises into a request path (that would turn a
degraded surface into a 500), but:

  * every failure logs at ERROR with the filename and the reason;
  * `verify_universe_files_present()` runs once at startup so a broken deploy says so at
    boot, in one obvious line, instead of being discovered as an empty card weeks later.

The read is memoised per filename: the payload is ~300-440 KB of JSON parsed into a list,
and four services plus a quarterly job read it.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

#: `backend/data/`. Resolved from THIS file (`backend/app/services/universe_data.py`), so
#: every caller gets the same answer regardless of how deep its own module sits.
DATA_DIR: Path = Path(__file__).resolve().parents[2] / "data"

BENCHMARK_UNIVERSE = "benchmark_universe.json"
INDUSTRY_UNIVERSE = "industry_universe.json"

#: Private bucket holding the two files. Not public: they are the licensed derivation.
UNIVERSE_BUCKET = "universe-data"

#: Set `UNIVERSE_DATA_DIR` to override the location (Railway volume, tmpdir in tests).
_ENV_DIR = "UNIVERSE_DATA_DIR"

_cache: Dict[str, List[Dict[str, Any]]] = {}
_lock = threading.Lock()


def universe_dir() -> Path:
    """Where the universe files live on this machine."""
    override = os.environ.get(_ENV_DIR)
    return Path(override) if override else DATA_DIR


def universe_path(filename: str) -> Path:
    """Absolute path for one universe file. The single source of truth for all callers."""
    return universe_dir() / filename


def _download_from_storage(filename: str) -> Optional[bytes]:
    """Pull one universe file from Supabase Storage. Returns the BYTES, or None.

    ⚠️ Returns the payload rather than a success flag, and caching it to disk is
    best-effort ON TOP of that. A container with a read-only filesystem (or a full disk)
    must still get its universe: making the return value depend on the write would turn a
    perfectly good download into an empty industry-benchmark surface, which is precisely
    the silent degradation this module exists to prevent.

    The disk copy is written via a `.part` file and renamed, so a concurrent worker can
    never read a half-downloaded payload as a truncated universe.
    """
    try:
        from app.database import get_supabase

        blob = get_supabase().storage.from_(UNIVERSE_BUCKET).download(filename)
    except Exception as exc:
        logger.error(
            "universe_data: could not fetch %s from Supabase Storage bucket=%s (%s: %s) — "
            "industry benchmarks / moat / dossier / competitor peers will be EMPTY until "
            "this is fixed",
            filename, UNIVERSE_BUCKET, type(exc).__name__, exc,
        )
        return None

    if not blob:
        logger.error(
            "universe_data: Supabase Storage returned an empty object for %s (bucket=%s) — "
            "industry benchmarks / moat / dossier / peers will be EMPTY",
            filename, UNIVERSE_BUCKET,
        )
        return None

    logger.info(
        "universe_data: fetched %s from Supabase Storage (%d bytes)", filename, len(blob)
    )
    try:
        dest = universe_path(filename)
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(dest.suffix + ".part")
        tmp.write_bytes(blob)
        tmp.replace(dest)
    except Exception as exc:
        # Not an error: the payload is in hand and the process memoises it. Only the
        # next cold start pays for the miss again.
        logger.warning(
            "universe_data: fetched %s but could not cache it to %s (%s: %s) — serving "
            "from memory; a read-only filesystem will re-download on every boot",
            filename, universe_dir(), type(exc).__name__, exc,
        )
    return blob


def load_universe(filename: str) -> List[Dict[str, Any]]:
    """The `industries` list from one universe file, or `[]`.

    `[]` rather than an exception because all four call sites are on request or job paths
    where a missing universe must degrade, not 500. Every `[]` here is logged at ERROR.
    """
    with _lock:
        cached = _cache.get(filename)
        if cached is not None:
            return cached

        path = universe_path(filename)
        raw: Optional[bytes] = None
        if not path.exists():
            raw = _download_from_storage(filename)
            if raw is None:
                _cache[filename] = []
                return []

        try:
            # Prefer the bytes we just downloaded — the disk write above is best-effort and
            # may have failed on a read-only filesystem.
            payload = json.loads(
                raw.decode("utf-8") if raw is not None else path.read_text(encoding="utf-8")
            )
            industries = payload.get("industries")
            if not isinstance(industries, list):
                raise ValueError(
                    f"'industries' is {type(industries).__name__}, expected list"
                )
        except Exception as exc:
            logger.error(
                "universe_data: %s is present but unreadable (%s: %s) — the surfaces that "
                "depend on it will be EMPTY", path, type(exc).__name__, exc,
            )
            _cache[filename] = []
            return []

        logger.info(
            "universe_data: loaded %s (%d industries, %s tickers)",
            filename, len(industries), payload.get("ticker_count", "?"),
        )
        _cache[filename] = industries
        return industries


def verify_universe_files_present() -> Dict[str, bool]:
    """Startup check. Logs ONE loud line per missing file and returns the per-file result.

    Deliberately not fatal: the app serves plenty of screens that need neither file, and
    killing the container would turn a degraded feature into a total outage. But an empty
    industry benchmark surface is otherwise indistinguishable from "no data for this
    industry", which is exactly the silent degradation this exists to prevent.
    """
    out: Dict[str, bool] = {}
    for filename in (BENCHMARK_UNIVERSE, INDUSTRY_UNIVERSE):
        ok = bool(load_universe(filename))
        out[filename] = ok
        if not ok:
            logger.error(
                "STARTUP: universe file %s is MISSING or empty. Dependent surfaces will "
                "render empty: industry benchmarks (incl. the sector aggregate rows), moat "
                "benchmarks, the industry dossier, and competitor peers. Upload it to the "
                "'%s' Supabase Storage bucket, or set %s.",
                filename, UNIVERSE_BUCKET, _ENV_DIR,
            )
    return out


def reset_cache_for_tests() -> None:
    with _lock:
        _cache.clear()
