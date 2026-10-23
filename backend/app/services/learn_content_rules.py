"""Shared Learn content rules: the misquote lists and the SF Symbol allow-list.

Pure (stdlib only), so a test, a seeder check and the Wiser weekly validators
(`learn_weekly/validators.py`) all read ONE copy. Until 2026-10-22 the two misquote lists lived in
`tests/test_learn_content_misattributions.py`, which is where their history is written down; the
weekly engine (plan "Wiser weekly content engine", §1.1) needs them at RUN time to refuse a model
draft, and production code must never import a test module.

* `BANNED_PHRASES` — wording whose only popular form is a misattribution or unsourced lore
  ("eighth wonder", "impatient to the patient"). Moved verbatim. `marketing/compliance.py`'s
  `MISATTRIBUTIONS` must stay a superset (`test_marketing_compliance.py` pins it against THIS
  module).
* `ATTRIBUTION_REQUIRED` — genuine wording, but only as a particular person's. A hand-made bundle
  block that uses one must name every listed person; a WEEKLY piece carries no quotes at all
  (plan §1.6), so there it is a hard refusal whoever is credited.
* `ICON_ALLOWLIST` — every SF Symbol name the bundled Money Moves JSON draws (keyHighlights,
  section icons, callout icons). `tests/test_ios_symbol_availability.py` proves each of those is
  available at the app's deployment target; a weekly piece may draw only these, so that guarantee
  carries over without a weekly piece ever reaching the availability database (a too-new symbol
  draws NOTHING on an older iPhone — no crash, no log). Pinned EQUAL to the bundle's set by
  `tests/test_learn_weekly_validators.py`: a new bundle icon must be added here in the same change.

⚠️ Editing a narrated `text` to satisfy a rule here means re-voicing that clip
(`.claude/rules/learn-content.md`).
"""

from __future__ import annotations

import re
import unicodedata
from types import MappingProxyType
from typing import FrozenSet, Mapping, Tuple

# Wording whose only popular form is a misattribution or unsourced lore (normalised substrings).
BANNED_PHRASES: Tuple[str, ...] = (
    "eighth wonder",                                    # not Einstein (shipped: Journey description)
    "einstein",                                         # in finance copy, only ever that myth
    "compound interest is the most powerful",           # not Einstein
    "most powerful force in the universe",              # not Einstein
    "impatient to the patient",                         # no primary source (shipped: Journey card)
    "irrational longer than you can remain solvent",    # not Keynes
    "when the facts change",                            # not Keynes
    "blood in the streets",                             # apocryphal Rothschild
    "investment in knowledge pays",                     # not Franklin
    "roughly right than precisely wrong",               # Carveth Read, pinned on Keynes/Buffett
    "sells to optimists",                               # Jason Zweig's 2003 commentary, not Graham
    "four most dangerous words",                        # Templeton via secondary sources only
    "rolls royce",                                      # unsourced Buffett lore
    "sitting in the shade",                             # unsourced Buffett lore
    "make money while you sleep",                       # unsourced Buffett lore
    "spend what is left after saving",                  # unsourced Buffett lore
    "protection against ignorance",                     # unsourced Buffett lore
    "best time to plant a tree",                        # misattributed proverb
)

# Genuine wording, but only as these people's: the block using it must name all of them.
# Read-only: every importer shares this one object, and a caller that mutated it would loosen the
# rule for the whole process.
ATTRIBUTION_REQUIRED: Mapping[str, Tuple[str, ...]] = MappingProxyType({
    # Buffett's rendering of Graham (1987 and 1993 Berkshire letters: "As Ben said: ...").
    # Shipped credited to Graham alone.
    "short run the market is a voting machine": ("buffett", "graham"),
    # Buffett crediting Graham (2008 letter: "Long ago, Ben Graham taught me that ...").
    "price is what you pay": ("buffett", "graham"),
})


def norm(text: str) -> str:
    """Casefold, straighten apostrophes, drop punctuation (same as the cover's test), so
    "short-run," and "short run" match alike. The rule phrases above are written pre-normalised
    (`norm(phrase) == phrase`, pinned by `test_learn_content_misattributions.py`).

    Linear: two character-class substitutions, no backtracking."""
    text = unicodedata.normalize("NFKC", text).casefold().replace("’", "'").replace("'", "")
    text = re.sub(r"[^\w\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


# Every SF Symbol the bundled Money Moves JSON draws (keyHighlights + section icons + callout
# icons), 79 names on 2026-10-22. A literal, not a read of the bundle at import time: the backend
# must not depend on the iOS tree at run time, and a literal makes an added icon a reviewed diff.
ICON_ALLOWLIST: FrozenSet[str] = frozenset({
    "airplane", "apps.iphone", "arrow.2.squarepath", "arrow.clockwise",
    "arrow.down.right.circle.fill", "arrow.left.arrow.right", "arrow.triangle.2.circlepath",
    "arrow.triangle.branch", "arrow.triangle.merge", "arrow.up.right", "arrow.uturn.down",
    "banknote.fill", "bitcoinsign.circle.fill", "bolt.car.fill", "bolt.fill", "book.closed.fill",
    "book.fill", "briefcase.fill", "building.2.fill", "building.columns.fill", "calendar",
    "cart.fill", "chart.bar.fill", "chart.line.downtrend.xyaxis", "chart.line.uptrend.xyaxis",
    "chart.pie.fill", "chart.xyaxis.line", "checkmark.shield.fill",
    "chevron.left.forwardslash.chevron.right", "circle.grid.cross.fill", "clock.fill",
    "cloud.fill", "cpu.fill", "creditcard.fill", "crown.fill", "doc.text.magnifyingglass",
    "dollarsign.circle.fill", "drop.fill", "exclamationmark.triangle.fill", "eye.slash.fill",
    "eyeglasses", "figure.walk", "flag.fill", "flame.fill", "gearshape.2.fill",
    "globe.americas.fill", "graduationcap.fill", "hammer.fill", "hourglass", "infinity",
    "info.circle.fill", "iphone", "iphone.gen3", "lightbulb.fill", "lock.fill", "lock.open.fill",
    "lock.shield.fill", "magnifyingglass", "map.fill", "newspaper.fill", "paintbrush.fill",
    "percent", "person.2.fill", "person.crop.circle.badge.exclamationmark",
    "person.crop.circle.badge.xmark", "person.fill.questionmark", "play.tv.fill",
    "point.3.connected.trianglepath.dotted", "questionmark.circle.fill", "scalemass.fill",
    "server.rack", "shield.checkered", "shield.lefthalf.filled", "shippingbox.fill", "sparkles",
    "square.grid.2x2.fill", "square.on.square", "square.split.2x2.fill", "wand.and.stars",
})
