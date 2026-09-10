"""Deterministic daily selection from a rotating pool.

The FIRST date-rotation math in this backend, so the reasoning is written down here
rather than assumed.

Pure: same ``(pool, k, iso_date, salt)`` -> same list, in any process, on any host,
forever. That last word is the requirement that picks the algorithm. The composed
starter payload is cached for a whole ET day and iOS caches it again on top, so a
schedule that shifts underneath us does not read as "a different shuffle" — it reads
to the user as the chips changing twice in one afternoon, which is exactly the
complaint this feature exists to answer.

Three choices follow from that, and each is load-bearing:

* **SHA-256 counter stream, not ``random.Random(seed)``.** ``random``'s Mersenne
  seeding is an implementation detail CPython has changed before and is free to
  change again; a hash is a specification. `test_daily_rotation.py` pins three golden
  outputs so a swap here is a red test, not a silent reshuffle in production.
* **Rejection sampling, not ``% bound``.** Taking a raw draw modulo the bound biases
  toward low indices. Over a year that systematically over-shows whichever questions
  sort early, which is a content bug nobody would ever trace back to here.
* **``n`` is inside the seed.** Adding or removing a question reshuffles the whole
  schedule. That is what keeps every index in range with no clamping and no
  ``IndexError`` when someone edits the table mid-cycle; the cost is that an edit
  restarts the coverage walk, which is the right trade for a table edited a few times
  a year.

Deliberately imports nothing from ``app.*`` — the test suite is hermetic and this
module has no I/O, so its tests run in milliseconds with no stubbing at all.
"""

from __future__ import annotations

import hashlib
from datetime import date
from typing import Iterable, Iterator, List, Sequence

#: Upper bound on how many pool entries we will consider. Not a product limit — a
#: guard so a runaway table (or a bad seed run) cannot turn a request into a
#: multi-second Fisher-Yates. The real pool is ~100.
_MAX_POOL = 2000

#: How many bytes of the hash stream one uniform draw consumes.
_DRAW_BYTES = 4


def normalize_pool(raw: Iterable[str]) -> List[str]:
    """Dedupe, drop empties, and SORT the pool into a canonical order.

    Sorted rather than left in source order because PostgREST does not guarantee row
    order without an explicit ``ORDER BY``. A schedule keyed on list POSITION would
    therefore differ between two reads of the same table — meaning two Railway
    instances could serve different chips on the same day, which looks exactly like a
    caching bug and is not one. Sorting makes the schedule a function of the SET
    alone, so any read order produces the same answer.

    Deduping is an iOS correctness requirement rather than tidiness: the chip row is a
    ``ForEach`` and two identical strings collapse into one element, silently
    shortening the row.
    """
    seen: set[str] = set()
    out: List[str] = []
    for item in raw:
        # Collapse every run of whitespace, so "Should  I buy?" and "Should I buy?"
        # are one question and the width of a chip cannot depend on stray spacing.
        text = " ".join(str(item or "").split())
        if not text:
            continue
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(text)
    out.sort()
    return out[:_MAX_POOL]


def day_index(iso_date: str) -> int:
    """An ET calendar date -> a monotone integer (proleptic Gregorian ordinal).

    Returns 0 on anything unparseable and never raises. A malformed date must cost
    one boring row of chips, not the whole endpoint — this function sits under a route
    whose entire contract is that it degrades instead of failing.
    """
    try:
        return date.fromisoformat(str(iso_date)).toordinal()
    except (TypeError, ValueError):
        return 0


def _byte_stream(seed: str) -> Iterator[int]:
    """An endless, deterministic byte stream derived from ``seed``.

    Counter mode over SHA-256: unbounded output, no state to carry between calls, and
    reproducible from the seed string alone.
    """
    counter = 0
    while True:
        for byte in hashlib.sha256(f"{seed}|{counter}".encode("utf-8")).digest():
            yield byte
        counter += 1


def _below(stream: Iterator[int], bound: int) -> int:
    """A uniform integer in ``[0, bound)``, by rejection sampling.

    See the module docstring on why this is not ``next(stream) % bound``. The rejection
    window is at most ``bound`` wide out of 2**32, so the expected number of retries is
    negligible for any realistic pool.
    """
    if bound <= 1:
        return 0
    span = 1 << (8 * _DRAW_BYTES)
    limit = span - (span % bound)          # largest multiple of `bound` that fits
    while True:
        value = int.from_bytes(bytes(next(stream) for _ in range(_DRAW_BYTES)), "big")
        if value < limit:
            return value % bound


def _permutation(n: int, cycle: int, salt: str) -> List[int]:
    """A deterministic permutation of ``range(n)`` for one cycle of the walk.

    Fisher-Yates driven by the hash stream. ``n`` is in the seed on purpose — see the
    module docstring. This is the RAW permutation: :func:`_cycle_permutation` layers
    the seam repair on top, and the two are kept separate because the repair must be
    able to read the previous cycle's raw form without recursing.
    """
    perm = list(range(n))
    stream = _byte_stream(f"{salt}|{n}|{cycle}")
    for i in range(n - 1, 0, -1):
        j = _below(stream, i + 1)
        perm[i], perm[j] = perm[j], perm[i]
    return perm


#: Windows-per-cycle below which the seam repair has nowhere to draw donors from.
#: Derived, not tuned: the repair needs up to ``k`` non-carried donors taken from
#: windows ``1 .. per_cycle-2``, which hold ``(per_cycle-2)*k`` slots of which at most
#: ``k`` are carried — so it needs ``(per_cycle-2)*k - k >= k``, i.e. ``per_cycle >= 4``.
_MIN_WINDOWS_FOR_SEAM_REPAIR = 4


def _cycle_permutation(n: int, k: int, cycle: int, salt: str) -> List[int]:
    """The permutation actually walked during ``cycle``, with the seam repaired.

    THE SEAM is the only place the walk can serve the same question two days running.
    Windows inside one cycle are disjoint slices, so they are safe by construction; but
    the first window of cycle ``c`` comes from a brand-new shuffle and knows nothing
    about the last window of cycle ``c-1``.

    The repair evicts any carried-over item from window 0, swapping it with a
    non-carried item drawn from the MIDDLE windows only. Two properties make this work,
    and both are load-bearing:

    * **Donors never come from the last window.** That leaves cycle ``c``'s final
      window byte-identical to its raw form, so cycle ``c+1`` can read it from
      :func:`_permutation` directly. Without that restriction each cycle's repair
      would depend on the previous cycle's repair, and resolving day 737,473 would
      mean replaying 46,000 cycles.
    * **The repair is a swap, not a rotation.** An earlier draft rotated the
      permutation by whole windows *only when ``slot == 0``*, so the cycle's remaining
      days walked the UNROTATED permutation and the rotated-in window reappeared a few
      days later — the disjointness guarantee silently held for one day and then broke.
      A swap applied for the whole cycle cannot do that.

    Below ``_MIN_WINDOWS_FOR_SEAM_REPAIR`` there are not enough donors and the raw
    permutation is returned; :func:`pick_for_day`'s safety check still guarantees
    consecutive days differ, just not that they are disjoint.
    """
    perm = _permutation(n, cycle, salt)
    per_cycle = n // k
    if cycle <= 0 or per_cycle < _MIN_WINDOWS_FOR_SEAM_REPAIR:
        return perm

    previous = _permutation(n, cycle - 1, salt)
    carried = set(previous[(per_cycle - 1) * k : per_cycle * k])

    # Donor slots: the middle windows only (1 .. per_cycle-2), never the last.
    donors = [
        pos for pos in range(k, (per_cycle - 1) * k)
        if perm[pos] not in carried
    ]
    next_donor = 0
    for pos in range(k):
        if perm[pos] in carried and next_donor < len(donors):
            swap_with = donors[next_donor]
            next_donor += 1
            perm[pos], perm[swap_with] = perm[swap_with], perm[pos]
    return perm


def _modular_window(items: Sequence[str], k: int, day: int, salt: str) -> List[str]:
    """The small-pool walk: a cyclic slice that advances ``k`` positions a day.

    Used when the pool is too small for the seam repair to have donors. It needs NO
    lookback at all, which is the point: an earlier design bridged this regime with a
    "did today equal yesterday? then nudge" check, and that check has to compare
    against what was actually SERVED yesterday — which may itself have been nudged,
    and so on back to day zero. At n=2, k=1 that chain is unbounded and the guarantee
    genuinely broke. A pure function of the day cannot have that problem.

    Guarantees here fall straight out of the arithmetic: consecutive days start
    ``k`` apart and ``0 < k < n``, so the ordered lists always differ; and once
    ``2k <= n`` two slices ``k`` apart cannot overlap, so they are disjoint too.
    """
    n = len(items)
    perm = _permutation(n, 0, salt)          # one fixed shuffle; the START does the work
    start = (day * k) % n
    return [items[perm[(start + offset) % n]] for offset in range(k)]


def _cycle_window(items: Sequence[str], k: int, day: int, salt: str) -> List[str]:
    """The large-pool walk: disjoint windows over a per-cycle permutation."""
    n = len(items)
    per_cycle = n // k
    cycle, slot = divmod(day, per_cycle)
    perm = _cycle_permutation(n, k, cycle, salt)
    return [items[i] for i in perm[slot * k : slot * k + k]]


def pick_for_day(
    pool: Sequence[str],
    k: int,
    iso_date: str,
    *,
    salt: str = "",
) -> List[str]:
    """Choose ``k`` questions for the ET day named by ``iso_date``.

    Three regimes, and every one of them is a PURE function of the day — there is no
    "compare against yesterday and nudge" anywhere, because such a check must compare
    against what was actually served yesterday, which may itself have been nudged, and
    that chain has no bottom.

    ============================  ==========================================
    Pool size                     Walk
    ============================  ==========================================
    ``n <= k``                    rotate the whole pool by ``day % n``
    ``k < n < 4k``                :func:`_modular_window` — cyclic slice, +k/day
    ``n >= 4k``                   :func:`_cycle_window` — disjoint windows,
                                  reshuffled each cycle, seam-repaired
    ============================  ==========================================

    Guarantees, in the order they matter:

    * No repeat WITHIN a day.
    * ``pick(d) != pick(d-1)`` as an ORDERED list whenever ``n > 1``, in all three
      regimes. At ``n == 1`` there is one possible answer and the claim is vacuous.
    * Disjoint from the previous day whenever ``n >= 2k`` — the modular walk covers
      ``2k <= n < 4k`` and the seam-repaired cycle walk covers ``n >= 4k``.
      ⚠️ **Not** below ``2k``, and that is arithmetic rather than a defect: two windows
      of size ``k`` drawn from fewer than ``2k`` items must share at least ``2k - n``
      of them. A test written to the stronger claim is red on day one and cannot be
      made green.
    * Every item is served before any item repeats.
    * ``n == 0``, ``n < k``, ``n == k`` and ``k <= 0`` all return something sane.

    ``salt`` separates independent rotations that share a date — the five detail
    scopes pass their scope name, so they do not all sit at the same phase of the walk.
    """
    items = normalize_pool(pool)
    n = len(items)
    if n == 0 or k <= 0:
        return []

    day = day_index(iso_date)

    # Pool too small to make a SELECTION at all: there is nothing to choose between, so
    # show everything, rotated, and let the order carry the "it changed today" signal.
    if n <= k:
        offset = day % n
        return items[offset:] + items[:offset]

    if n < k * _MIN_WINDOWS_FOR_SEAM_REPAIR:
        return _modular_window(items, k, day, salt)

    return _cycle_window(items, k, day, salt)
