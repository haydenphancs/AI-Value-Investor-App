#!/usr/bin/env python3
"""
Refresh the Congress block-list (`backend/data/congress_roster.json`) — OWNER-RUN.
==================================================================================

Company Weekly never names a member of Congress (rules/marketing.md §1; owner decision
2026-10-09). `company_news_rules.congress_names()` reads this file (plus the whale registry's
politicians) and refuses any text that names a member; while the file is missing, unreadable
or truncated, every person slot in a company-news post renders role-only.

Source: the public-domain (CC0) unitedstates/congress-legislators dataset —
`legislators-current.json` + `legislators-historical.json`. Kept: every current member, and
every historical member with a term ending on or after 2021-01-03 (the 117th Congress). Names
only: first, middle, last, nickname, official_full, current — no party, state, district, term, id
or any other field. The middle name is kept (review round 10) because a member known by it files
a Form 4 under first + middle, and the block-list indexes it as a given name.

The roster also records ``congress_start`` (review round 11, main-session decision 2026-10-10):
the January 3 that began the Congress its current members serve in, computed FROM THE SOURCE —
never from the day the script ran. The rule (`infer_congress_start`): take each current member's
LATEST term (the one with the latest ``start``), snap its start to the odd-year January 3 on or
before it (a special-election start mid-Congress counts toward that Congress), and keep the
January 3 shared by MORE THAN HALF of the current members (the 441 House seats alone are a
majority; senators keep older starts) — else there is none. The build is refused (nothing
written) when there is no such January 3; when it is not the latest odd-year January 3 on or
before the build date (a refresh made on January 3-5 from a dataset that still lists the
outgoing Congress — retry a few days later); when it is after the build date; or when any current
member's latest term ended on or before it (a dataset half-way through the switch: an outgoing
member still listed as current means new members may be missing).

Dry run by default: it fetches, builds and validates, then logs what would change. Pass
`--write` to replace the file (the ONLY file this script writes; written atomically). Run it
after every odd-year January 3 (a new Congress is sworn in) and after special elections: from
that January 3 until the roster holds the new Congress, `company_news_rules.roster_fresh_for` is
False and both Form 4 series (CEO / insider buys) are refused (logged at ERROR; those days fall
back along the chain). `--from-dir DIR` reads the two JSON files from a local directory instead
of fetching them.

Usage (from backend/):
    ./venv/bin/python -m scripts.refresh_congress_roster            # dry run
    ./venv/bin/python -m scripts.refresh_congress_roster --write    # replace the file
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import tempfile
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import httpx

logger = logging.getLogger("refresh_congress_roster")

BACKEND = Path(__file__).resolve().parents[1]
ROSTER_PATH = BACKEND / "data" / "congress_roster.json"
SOURCE_BASE = "https://unitedstates.github.io/congress-legislators/"
SOURCE_FILES = ("legislators-current.json", "legislators-historical.json")
ALLOWED_HOSTS = frozenset({"unitedstates.github.io"})
#: The 117th Congress convened on this date; a historical member counts when a term ended on or
#: after it.
SINCE = "2021-01-03"
MAX_SOURCE_BYTES = 64 * 1024 * 1024
FETCH_TIMEOUT_SECONDS = 120.0
#: Mirrors `company_news_rules.ROSTER_MIN_MEMBERS` (pinned equal by a test): a roster with fewer
#: usable members than one full Congress is refused, here and at read time.
ROSTER_MIN_MEMBERS = 535
MEMBER_KEYS = ("first", "middle", "last", "nickname", "official_full", "current")
ROSTER_KEYS = ("_about", "fetched_on", "congress_start", "members")


class RosterError(Exception):
    """A source file or the built roster failed validation; nothing is written."""


def _about(fetched_on: str) -> str:
    return (
        "Members of the US Congress serving now or at any time since 2021-01-03, names only. "
        "Source: the public-domain (CC0) unitedstates/congress-legislators dataset "
        "(legislators-current.json + legislators-historical.json), fetched "
        f"{fetched_on}. Used ONLY as a block-list: a company-news template refuses any "
        "text naming a member (rules/marketing.md §1 — members of Congress are never named). "
        "Refresh with backend/scripts/refresh_congress_roster.py (owner-run)."
    )


def _member(person: Mapping[str, Any], current: bool) -> Optional[Tuple[str, Dict[str, Any]]]:
    name = person.get("name") if isinstance(person.get("name"), Mapping) else {}
    first = name.get("first").strip() if isinstance(name.get("first"), str) else ""
    last = name.get("last").strip() if isinstance(name.get("last"), str) else ""
    if not first or not last:
        return None
    middle = name.get("middle").strip() if isinstance(name.get("middle"), str) else ""
    nick = name.get("nickname").strip() if isinstance(name.get("nickname"), str) else ""
    official = name.get("official_full").strip() if isinstance(name.get("official_full"), str) else ""
    ids = person.get("id") if isinstance(person.get("id"), Mapping) else {}
    bioguide = ids.get("bioguide") if isinstance(ids.get("bioguide"), str) else ""
    row: Dict[str, Any] = {"first": first}
    if middle:
        row["middle"] = middle
    row["last"] = last
    if nick:
        row["nickname"] = nick
    if official:
        row["official_full"] = official
    row["current"] = current
    return (bioguide or f"{first} {last}"), row


def _today() -> date:
    """The build date (``fetched_on``); a seam so tests never depend on the wall clock."""
    return date.today()


def congress_start_on_or_before(day: date) -> date:
    """The most recent odd-year January 3 on or before ``day``. Mirrors
    `company_news_rules.congress_start_on_or_before` (pinned equal by a test; this script imports
    nothing from ``app``)."""
    year = day.year if day.year % 2 == 1 else day.year - 1
    start = date(year, 1, 3)
    return start if start <= day else date(year - 2, 1, 3)


#: No term starts before the First Congress; an earlier "date" is a malformed row (and would
#: underflow the January 3 arithmetic).
_FIRST_CONGRESS = date(1789, 3, 4)


def _iso_day(value: Any) -> Optional[date]:
    if not isinstance(value, str):
        return None
    try:
        day = date.fromisoformat(value.strip())
    except ValueError:
        return None
    return day if day >= _FIRST_CONGRESS else None


def latest_term_span(person: Mapping[str, Any]) -> Tuple[Optional[date], Optional[date]]:
    """(start, end) of the person's term with the latest readable ``start``; (None, None) when no
    term has one. ``end`` is None when that term's end is unreadable."""
    terms = person.get("terms")
    best: Optional[Tuple[date, Mapping[str, Any]]] = None
    for t in terms if isinstance(terms, list) else ():
        if not isinstance(t, Mapping):
            continue
        start = _iso_day(t.get("start"))
        if start is not None and (best is None or start > best[0]):
            best = (start, t)
    if best is None:
        return None, None
    return best[0], _iso_day(best[1].get("end"))


def infer_congress_start(latest_starts: Iterable[Optional[date]]) -> Optional[date]:
    """The rule in the module docstring: each current member's latest term start (None when
    unreadable — it still counts in the total), snapped to the odd-year January 3 on or before it;
    the January 3 shared by MORE THAN HALF of them, else None."""
    starts = list(latest_starts)
    snapped = Counter(congress_start_on_or_before(d) for d in starts if d is not None)
    if not snapped:
        return None
    best, count = max(snapped.items(), key=lambda kv: (kv[1], kv[0]))
    return best if count * 2 > len(starts) else None


def _served_since(person: Mapping[str, Any], since: str) -> bool:
    terms = person.get("terms")
    if not isinstance(terms, list):
        return False
    return any(isinstance(t, Mapping) and str(t.get("end", "")) >= since for t in terms)


def build_roster(current: Any, historical: Any, *, fetched_on: str, since: str = SINCE) -> Dict[str, Any]:
    """The roster document from the two source lists (pure). Every current member, plus every
    historical member with a term ending on/after ``since``; keyed by bioguide id (a current
    entry wins over a historical one), sorted by (last, first), names only — plus
    ``congress_start`` (`infer_congress_start` over the current members' latest terms; None when
    no January 3 has a majority, which `validate_roster` refuses). Raises RosterError when a
    current member's latest term ended on or before that ``congress_start`` (the dataset is half-way
    through a new Congress)."""
    if not isinstance(current, list) or not isinstance(historical, list):
        raise RosterError("a source file is not a JSON list")
    rows: Dict[str, Dict[str, Any]] = {}
    spans: Dict[str, Tuple[Optional[date], Optional[date]]] = {}
    for src, is_current in ((current, True), (historical, False)):
        for person in src:
            if not isinstance(person, Mapping):
                continue
            if not is_current and not _served_since(person, since):
                continue
            got = _member(person, is_current)
            if got is None:
                continue
            key, row = got
            if key in rows and rows[key]["current"] and not is_current:
                continue
            rows[key] = row
            if is_current:
                spans[key] = latest_term_span(person)
    members = sorted(rows.values(), key=lambda r: (r["last"].lower(), r["first"].lower()))
    start = infer_congress_start(span[0] for span in spans.values())
    if start is not None:
        leftovers = sum(1 for _s, end in spans.values() if end is not None and end <= start)
        if leftovers:
            raise RosterError(f"{leftovers} current member(s) whose latest term ended on or before the "
                              f"Congress sworn in on {start}: the dataset is mid-way through a new "
                              "Congress (new members may be missing) — retry in a few days")
    return {"_about": _about(fetched_on), "fetched_on": fetched_on,
            "congress_start": start.isoformat() if start is not None else None, "members": members}


def _validate_congress_start(raw: Any, fetched_on: date) -> None:
    """The roster must hold the Congress sitting on its build date: ``congress_start`` is an
    odd-year January 3, not older than the latest one on or before ``fetched_on`` (a dataset that
    still lists the outgoing Congress is refused — the owner retries later) and not after it."""
    if raw is None:
        raise RosterError("congress_start is missing: no Congress start is shared by more than half of "
                          "the current members")
    day = _iso_day(raw)
    if day is None:
        raise RosterError(f"congress_start is not an ISO date: {str(raw)[:40]!r}")
    if (day.month, day.day) != (1, 3) or day.year % 2 == 0:
        raise RosterError(f"congress_start {day} is not an odd-year January 3")
    sitting = congress_start_on_or_before(fetched_on)
    if day < sitting:
        raise RosterError(f"the dataset holds the Congress sworn in on {day}, but the Congress sworn in on "
                          f"{sitting} sits on the build date {fetched_on}: it does not list the new "
                          "Congress yet — retry in a few days")
    if day > fetched_on:
        raise RosterError(f"congress_start {day} is after the build date {fetched_on}")


def validate_roster(doc: Any) -> None:
    """Refuse a roster this module would not trust at read time (RosterError)."""
    if not isinstance(doc, dict) or set(doc) != set(ROSTER_KEYS):
        raise RosterError("roster keys must be exactly " + ", ".join(ROSTER_KEYS))
    try:
        fetched_on = date.fromisoformat(doc["fetched_on"])
    except (TypeError, ValueError) as e:
        raise RosterError(f"fetched_on is not an ISO date: {e}") from e
    _validate_congress_start(doc["congress_start"], fetched_on)
    members = doc["members"]
    if not isinstance(members, list):
        raise RosterError("members is not a list")
    for m in members:
        if not isinstance(m, dict) or not set(m) <= set(MEMBER_KEYS):
            raise RosterError(f"member has unexpected keys: {sorted(m) if isinstance(m, dict) else m!r}")
        if not all(isinstance(m.get(k), str) and m[k].strip() for k in ("first", "last")):
            raise RosterError("member without a first and last name")
        if not all(isinstance(m[k], str) and m[k].strip() for k in ("middle", "nickname", "official_full") if k in m):
            raise RosterError("member with an empty or non-text middle / nickname / official_full")
        if not isinstance(m.get("current"), bool):
            raise RosterError("member without a bool 'current'")
    current = sum(1 for m in members if m["current"])
    if current < ROSTER_MIN_MEMBERS:
        raise RosterError(f"{current} current members < {ROSTER_MIN_MEMBERS} (one full Congress): "
                          "refusing a truncated roster")


def diff_summary(old: Any, new: Mapping[str, Any]) -> Tuple[List[str], List[str]]:
    """(added, removed) display names between an existing roster and a new one."""
    def names(doc: Any) -> set:
        members = doc.get("members") if isinstance(doc, Mapping) else None
        return {f"{m.get('first')} {m.get('last')}" for m in members or [] if isinstance(m, Mapping)}
    before, after = names(old), names(new)
    return sorted(after - before), sorted(before - after)


async def _fetch_one(client: httpx.AsyncClient, filename: str) -> Any:
    url = SOURCE_BASE + filename
    async with client.stream("GET", url) as resp:
        if resp.url.host not in ALLOWED_HOSTS:
            raise RosterError(f"{filename}: redirected off {sorted(ALLOWED_HOSTS)}")
        if resp.status_code != 200:
            raise RosterError(f"{filename}: HTTP {resp.status_code}")
        chunks: List[bytes] = []
        size = 0
        async for chunk in resp.aiter_bytes():
            size += len(chunk)
            if size > MAX_SOURCE_BYTES:
                raise RosterError(f"{filename}: larger than {MAX_SOURCE_BYTES} bytes")
            chunks.append(chunk)
    try:
        return json.loads(b"".join(chunks))
    except ValueError as e:
        raise RosterError(f"{filename}: not JSON ({e})") from e


async def fetch_sources() -> Tuple[Any, Any]:
    """Fetch both source files over HTTPS (the owner's machine; never from a test)."""
    async with httpx.AsyncClient(timeout=FETCH_TIMEOUT_SECONDS, follow_redirects=True,
                                 max_redirects=3) as client:
        current, historical = await asyncio.gather(*(_fetch_one(client, f) for f in SOURCE_FILES))
    return current, historical


def read_sources(directory: Path) -> Tuple[Any, Any]:
    out = []
    for filename in SOURCE_FILES:
        path = directory / filename
        if path.stat().st_size > MAX_SOURCE_BYTES:
            raise RosterError(f"{path}: larger than {MAX_SOURCE_BYTES} bytes")
        try:
            out.append(json.loads(path.read_text(encoding="utf-8")))
        except ValueError as e:
            raise RosterError(f"{path}: not JSON ({e})") from e
    return out[0], out[1]


def render(doc: Mapping[str, Any]) -> str:
    """The file's exact text (the format the committed roster uses)."""
    return json.dumps(doc, indent=1, ensure_ascii=False) + "\n"


def write_roster(doc: Mapping[str, Any], path: Optional[Path] = None) -> None:
    """Validate, then replace ``path`` (default: `ROSTER_PATH`, read at CALL time) atomically —
    a temp file in the same directory, then a rename; the file keeps mode 0644."""
    path = Path(path) if path is not None else ROSTER_PATH
    validate_roster(doc)
    fd, tmp = tempfile.mkstemp(prefix=".congress_roster.", suffix=".json", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(render(doc))
        os.chmod(tmp, 0o644)   # mkstemp creates 0600; the committed file is world-readable
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Refresh backend/data/congress_roster.json (owner-run).")
    parser.add_argument("--write", action="store_true", help="replace the roster file (default: dry run)")
    parser.add_argument("--from-dir", type=Path, default=None,
                        help="read the two source JSON files from this directory instead of fetching")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")

    try:
        if args.from_dir is not None:
            current, historical = read_sources(args.from_dir)
        else:
            current, historical = asyncio.run(fetch_sources())
        doc = build_roster(current, historical, fetched_on=_today().isoformat())
        validate_roster(doc)
    except (RosterError, httpx.HTTPError, OSError) as e:
        logger.error("congress roster refresh FAILED, nothing written: %s: %s", type(e).__name__, e)
        return 1

    try:
        old = json.loads(ROSTER_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        logger.warning("existing roster unreadable (%s: %s); treating it as empty", type(e).__name__, e)
        old = {}
    added, removed = diff_summary(old, doc)
    logger.info("built %d members (%d current, Congress sworn in on %s); %d added, %d removed vs %s",
                len(doc["members"]), sum(1 for m in doc["members"] if m["current"]),
                doc["congress_start"], len(added), len(removed), ROSTER_PATH.name)
    for name in added[:50]:
        logger.info("  + %s", name)
    for name in removed[:50]:
        logger.info("  - %s", name)
    if not args.write:
        logger.info("dry run: nothing written (pass --write to replace %s)", ROSTER_PATH)
        return 0
    write_roster(doc, ROSTER_PATH)
    logger.info("wrote %s", ROSTER_PATH)
    return 0


if __name__ == "__main__":
    sys.exit(main())
