"""Refuse a flag a hand-rolled script does not know, before it does anything.

Why this exists (2026-11-05): `seed_money_moves.py` reads its flags with `"--dry-run" in sys.argv`, so
`--help` was simply ignored and the script ran for real. It was run that way to read its usage, and it
upserted every catalog row in production. A script without argparse must treat an unknown flag as a
mistake, because a typo'd `--dryrun` would do exactly the same thing.

Call it first thing under `if __name__ == "__main__":`, never at import: tests import these modules,
and pytest's own arguments are in `sys.argv` there.

    if __name__ == "__main__":
        from _cli_flags import check_flags

        check_flags(sys.argv, known={"--dry-run", "--force"}, doc=__doc__)
        main()

`tests/test_scripts_refuse_unknown_flags.py` fails the build on a script that reads flags from
`sys.argv` without this call, or that reads a flag it did not list.
"""

from __future__ import annotations

import sys
from typing import Iterable, Optional, Sequence

HELP_FLAGS = frozenset({"-h", "--help"})


def check_flags(argv: Sequence[str], *, known: Iterable[str], doc: Optional[str] = None) -> None:
    """Print `doc` and exit 0 on -h / --help; exit 2 on any other dash argument not in `known`.

    Plain arguments (slugs, numbers, a value after a flag) pass through, unless they start with a dash.
    """
    args = list(argv[1:])
    if HELP_FLAGS & set(args):
        print((doc or "").strip() or f"flags: {' '.join(sorted(known)) or '(none)'}")
        raise SystemExit(0)
    allowed = set(known)
    unknown = sorted(a for a in args if a.startswith("-") and a not in allowed)
    if unknown:
        print(f"unknown flag(s): {' '.join(unknown)}. Known: {' '.join(sorted(allowed)) or '(none)'}. "
              "Nothing was run.", file=sys.stderr)
        raise SystemExit(2)
