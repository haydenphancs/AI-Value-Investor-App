"""A company's own press releases — the licensed answer to "what guidance did X give".

WHY THIS EXISTS (2026-10-08). The news tool reads the wire-news corpus; a company's own
releases (results, guidance, buybacks, leadership changes) sit on FMP's entitled
``news/press-releases`` ("9 Market News") and no code ever called it. Without them, "what
did the company say" fell to model memory, or — once the automatic web tier exists — to a
paid web search for something our licence already covers.

Cache: process memory only, `_TTL_SECONDS` (1 h) per ticker, concurrent fetches deduped by
`_inflight`. One small call per ticker per hour needs no Supabase tier; a failure is NOT
cached beyond `_FAILURE_TTL_SECONDS` (a herd guard), so an outage is never remembered as
"this company issued nothing".

Rows are cleaned at the boundary:
  * only rows for the symbol asked (FMP's news endpoints serve a default symbol's feed when
    the filter is lost — never attribute another company's release);
  * a valid date, newest first, de-duplicated, at most `_MAX_RELEASES`;
  * title and text are third-party text: control characters and HTML tags stripped,
    fences neutralised, the text cut to `_TEXT_MAX` characters;
  * ``publisher`` is the COMPANY (a press release is the issuer's own statement; the wire
    service that distributed it is not its author).

`get_press_releases` never raises. A failed fetch returns an EMPTY list that says so
(`EmptyAfterFailure`, ``fetch_failed = True``) with a WARNING; a genuinely empty answer is a
plain ``[]``.
"""

from __future__ import annotations

import asyncio
import copy
import logging
import re
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_TTL_SECONDS = 3600
_FAILURE_TTL_SECONDS = 60
_MAX_RELEASES = 5
_FETCH_LIMIT = 15           # a few extra, so dropped rows (wrong symbol, no date) still leave 5
_TITLE_MAX = 200
_TEXT_MAX = 300
_MEM_MAX = 512

_TAG_RE = re.compile(r"<[^<>]{0,500}>")
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_SPACE_RE = re.compile(r"\s+")
_STAMP_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})(?:[ T](\d{2}:\d{2}))?")

_mem: Dict[str, Tuple[float, float, List[Dict[str, Any]]]] = {}
_inflight: Dict[str, "asyncio.Task"] = {}


def _fmp():
    from app.integrations.fmp import get_fmp_client

    return get_fmp_client()


def _normalize(ticker: Any) -> Optional[str]:
    if not isinstance(ticker, str):
        return None
    sym = ticker.strip().upper()
    if not sym or len(sym) > 16 or any(c.isspace() for c in sym):
        return None
    return sym


def _clean(value: Any, cap: int) -> Optional[str]:
    """Bounded, tag-free, fence-neutralised text, or None."""
    if not isinstance(value, str):
        return None
    from app.services.chat_security import neutralize_fences

    # Bounded before any regex: a release body can be tens of kilobytes.
    text = value[: cap * 20]
    text = _TAG_RE.sub(" ", text)
    text = _CONTROL_RE.sub(" ", text)
    text = _SPACE_RE.sub(" ", neutralize_fences(text)).strip()
    if not text:
        return None
    return text if len(text) <= cap else text[: cap - 1].rstrip() + "…"


def _stamp(value: Any) -> Optional[str]:
    """"2026-10-01 08:30:00" → "2026-10-01 08:30"; None when it is not a real date."""
    if not isinstance(value, str):
        return None
    m = _STAMP_RE.match(value.strip())
    if not m:
        return None
    try:
        datetime.strptime(m.group(1), "%Y-%m-%d")
    except ValueError:
        return None
    return f"{m.group(1)} {m.group(2)}" if m.group(2) else m.group(1)


def clean_releases(rows: Any, sym: str, *, limit: int = _MAX_RELEASES) -> List[Dict[str, Any]]:
    """Pure: FMP press-release rows → the caller's list (see the module docstring)."""
    if not isinstance(rows, list):
        return []
    out: List[Dict[str, Any]] = []
    seen = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        row_sym = row.get("symbol")
        if (not isinstance(row_sym, str)
                or row_sym.strip().upper().replace(".", "-") != sym.replace(".", "-")):
            continue
        when = _stamp(row.get("publishedDate") or row.get("date"))
        title = _clean(row.get("title"), _TITLE_MAX)
        if not when or not title:
            continue
        key = (when[:10], title.lower())
        if key in seen:
            continue
        seen.add(key)
        item: Dict[str, Any] = {"date": when, "title": title,
                                "publisher": f"{sym} (the company's own press release)"}
        text = _clean(row.get("text"), _TEXT_MAX)
        if text:
            item["text"] = text
        out.append(item)
    out.sort(key=lambda r: r["date"], reverse=True)
    return out[: max(0, int(limit))]


def _mem_get(sym: str) -> Optional[List[Dict[str, Any]]]:
    entry = _mem.get(sym)
    if entry is None:
        return None
    stored_at, ttl, value = entry
    if time.monotonic() - stored_at > ttl:
        _mem.pop(sym, None)
        return None
    return value


def _mem_set(sym: str, value: List[Dict[str, Any]], ttl: float) -> None:
    _mem.pop(sym, None)
    _mem[sym] = (time.monotonic(), ttl, value)
    if len(_mem) > _MEM_MAX:
        for old in list(_mem.keys())[: len(_mem) - _MEM_MAX]:
            _mem.pop(old, None)


def clear_memory() -> None:
    """Test/ops hook."""
    _mem.clear()


def _failed(reason: str) -> List[Dict[str, Any]]:
    from app.integrations.fmp import EmptyAfterFailure

    return EmptyAfterFailure(reason)


async def _load(sym: str) -> List[Dict[str, Any]]:
    """One upstream read. Never raises; memoises what it returns."""
    try:
        rows = await _fmp().get_press_releases(sym, limit=_FETCH_LIMIT)
    except Exception as e:  # noqa: BLE001 — typed FMP failures and anything unexpected
        logger.warning("press releases: fetch failed for %s: %s: %s — answered as not loaded",
                       sym, type(e).__name__, e)
        # A fixed reason: the exception's class names the vendor, and a caller may surface
        # `.reason` — the class stays in the log line above.
        failed = _failed("fetch failed")
        _mem_set(sym, failed, _FAILURE_TTL_SECONDS)
        return failed
    cleaned = clean_releases(rows, sym)
    dropped = (len(rows) if isinstance(rows, list) else 0) - len(cleaned)
    if dropped > 0 and isinstance(rows, list) and rows and not cleaned:
        logger.warning("press releases: all %d rows for %s were unusable (wrong symbol, no "
                       "date or no title)", len(rows), sym)
    _mem_set(sym, cleaned, _TTL_SECONDS)
    logger.info("press releases: %d for %s", len(cleaned), sym)
    return cleaned


def _on_done(sym: str, task: "asyncio.Task") -> None:
    if _inflight.get(sym) is task:
        _inflight.pop(sym, None)
    if not task.cancelled() and task.exception() is not None:
        logger.warning("press releases: load for %s raised %s", sym, task.exception())


def _copy(value: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if getattr(value, "fetch_failed", False):
        return _failed(getattr(value, "reason", "") or "fetch failed")
    return copy.deepcopy(list(value))


async def get_press_releases(ticker: str, *, wait: Optional[float] = None
                             ) -> List[Dict[str, Any]]:
    """The latest ≤5 press releases for `ticker`, newest first:
    ``[{"date": "YYYY-MM-DD[ HH:MM]", "title", "text"? (≤300 chars), "publisher"}]``.

    Never raises. A failure — or, with ``wait`` (seconds), a fetch still running after it —
    returns an empty list with ``fetch_failed = True``; the fetch keeps going and warms the
    cache. A plain ``[]`` means the company has no releases on file.
    """
    sym = _normalize(ticker)
    if sym is None:
        return []
    hit = _mem_get(sym)
    if hit is not None:
        return _copy(hit)
    task = _inflight.get(sym)
    if task is None:
        task = asyncio.ensure_future(_load(sym))
        _inflight[sym] = task
        task.add_done_callback(lambda t, s=sym: _on_done(s, t))
    try:
        if wait is not None:
            done, _ = await asyncio.wait({task}, timeout=max(0.0, float(wait)))
            if not done:
                logger.warning("press releases: %s still loading after %.1fs — not in this "
                               "answer", sym, float(wait))
                return _failed("still loading")
        result = await asyncio.shield(task)
    except asyncio.CancelledError:
        raise
    except Exception as e:  # noqa: BLE001 — `_load` never raises; belt and braces
        logger.warning("press releases: failed for %s: %s: %s", sym, type(e).__name__, e)
        return _failed("fetch failed")
    return _copy(result)


__all__ = ["get_press_releases", "clean_releases", "clear_memory"]
