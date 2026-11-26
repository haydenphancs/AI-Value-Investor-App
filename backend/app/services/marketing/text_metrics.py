"""
The worker's font, measured on the server — news-v23 phase 3 (2026-11-20), the weekly theme post.

The worker draws every template string in ONE face, Inter Bold (`marketing/assets/fonts/Inter-Bold.ttf`, OFL). The
server's older fit bounds are counted in "W"s (the widest glyph): sound, but so loose for a short line that a tile
holding "Compute and Networking" would read as two lines. The theme tiles and ranked rows (angle 1 and 2) need the
line count the worker will actually draw at its FLOOR size, so the template can choose a tile's wording (one line, a
wrapped share line, the segment alone) and know it fits — never a picture that only a smaller-than-floor size could
draw (the worker would skip the day, `unrenderable_text`).

`INTER_BOLD_ADVANCES` is the face's horizontal advance of every character a template line may hold, in font units
(`UNITS_PER_EM` a size), read from the font's `hmtx` table; `text_width` is an UPPER BOUND on the width Pillow draws a
string at `px`, under either layout engine the worker may use:

* BASIC layout (no shaping) draws each glyph at its HINTED advance, a whole pixel that is not the exact advance rounded
  (Inter Bold's hinting moves it by up to ~0.51 px) — and applies no kerning (the face has no `kern` table);
* RAQM (HarfBuzz, the production worker image) adds the GPOS `kern` feature, which WIDENS 284 pairs of the table's
  characters ("WW", "ty", "rt", "_j" …: Inter's kerning does not only narrow — the 2026-11-20 check) and positions in
  1/64 px.

At the sizes the server measures at (the tile, row and name floors: 22 px), `HINTED_ADVANCES[px]` is Pillow's own
hinted advance of every table character, and a glyph counts max(hinted, exact + 1/64) — exact for BASIC, and above
RAQM's (hinted or exact) advance — plus, for each adjacent pair, its widening (`POSITIVE_KERNS`, the most any `kern`
lookup of the face can add to that pair, + 1/64). Tight: "Semiconductor Process Control" (KLA's segment, Pillow 344 px
at 22 px) bounds at 346.6 px — one line of a 347 px tile — where the older flat allowance (half a pixel a glyph + 1)
said 358 and dropped the tile's "47% of revenue" (2026-11-20). At any other size a glyph counts its exact advance +
half a pixel (+ 1 a string) and the same widening. A character outside the table counts as the face's widest glyph of
ALL (`WIDEST_ADVANCE`, wider than "W": a segment label from a filing may hold any character), and a pair touching one
the face's widest possible widening (`MAX_PAIR_KERN`) — never narrower than it may draw. `wrap_lines` is the worker's
greedy word wrap on those widths. The tables are pinned to the font file and to Pillow by
tests/test_marketing_news_layouts.py (fontTools' read of `hmtx` and GPOS, and Pillow's widths), so a font or Pillow
change fails there before any bound drifts.

Pure: stdlib only. No FMP, no I/O.
"""

from __future__ import annotations

from typing import Dict, List, Sequence

#: Inter Bold's units per em.
UNITS_PER_EM = 2048
#: Horizontal advances (font units) of printable ASCII and the punctuation the templates write ("·", dashes, quotes,
#: the minus sign), from the font's hmtx table.
INTER_BOLD_ADVANCES: Dict[str, int] = {
    ' ': 485, '!': 692, '"': 1130, '#': 1329, '$': 1341, '%': 2080, '&': 1376, "'": 694, '(': 772, ')': 772,
    '*': 1145, '+': 1390, ',': 684, '-': 958, '.': 684, '/': 795, '0': 1381, '1': 883, '2': 1290, '3': 1322,
    '4': 1385, '5': 1274, '6': 1330, '7': 1191, '8': 1333, '9': 1330, ':': 684, ';': 702, '<': 1390, '=': 1390,
    '>': 1390, '?': 1146, '@': 2081, 'A': 1529, 'B': 1355, 'C': 1515, 'D': 1479, 'E': 1244, 'F': 1202, 'G': 1537,
    'H': 1530, 'I': 575, 'J': 1197, 'K': 1473, 'L': 1158, 'M': 1908, 'N': 1561, 'O': 1578, 'P': 1327, 'Q': 1591,
    'R': 1345, 'S': 1341, 'T': 1367, 'U': 1499, 'V': 1529, 'W': 2125, 'X': 1512, 'Y': 1497, 'Z': 1360, '[': 772,
    '\\': 795, ']': 772, '^': 997, '_': 975, '`': 748, 'a': 1189, 'b': 1291, 'c': 1205, 'd': 1291, 'e': 1220,
    'f': 815, 'g': 1294, 'h': 1275, 'i': 555, 'j': 555, 'k': 1188, 'l': 555, 'm': 1869, 'n': 1275, 'o': 1256,
    'p': 1291, 'q': 1291, 'r': 834, 's': 1147, 't': 750, 'u': 1275, 'v': 1228, 'w': 1741, 'x': 1188, 'y': 1233,
    'z': 1173, '{': 960, '|': 761, '}': 960, '~': 1390, '·': 684, 'é': 1220, '–': 1024, '—': 2048, '‘': 636,
    '’': 636, '“': 1106, '”': 1089, '−': 1390,
}
#: What a character outside the table counts as: the widest advance of ANY glyph in the face (its long arrows), so an
#: unexpected character only ever over-measures (a tile then drops its line, never draws past its column).
WIDEST_ADVANCE = 5492


#: Pillow's HINTED advance (whole pixels, BASIC layout) of every `INTER_BOLD_ADVANCES` character at the sizes the
#: server measures at — the theme tile / ranked row / table name floors (22 px). Pinned to Pillow by
#: tests/test_marketing_news_layouts.py.
HINTED_ADVANCES: Dict[int, Dict[str, int]] = {22: {
    ' ': 5, '!': 7, '"': 12, '#': 14, '$': 14, '%': 22, '&': 15, "'": 7, '(': 8, ')': 8, '*': 12, '+': 15, ',': 7,
    '-': 10, '.': 7, '/': 9, '0': 15, '1': 9, '2': 14, '3': 14, '4': 15, '5': 14, '6': 14, '7': 13, '8': 14, '9': 14,
    ':': 7, ';': 8, '<': 15, '=': 15, '>': 15, '?': 12, '@': 22, 'A': 16, 'B': 15, 'C': 16, 'D': 16, 'E': 13,
    'F': 13, 'G': 17, 'H': 16, 'I': 6, 'J': 13, 'K': 16, 'L': 12, 'M': 21, 'N': 17, 'O': 17, 'P': 14, 'Q': 17,
    'R': 14, 'S': 14, 'T': 15, 'U': 16, 'V': 16, 'W': 23, 'X': 16, 'Y': 16, 'Z': 15, '[': 8, '\\': 9, ']': 8,
    '^': 11, '_': 10, '`': 8, 'a': 13, 'b': 14, 'c': 13, 'd': 14, 'e': 13, 'f': 9, 'g': 14, 'h': 14, 'i': 6, 'j': 6,
    'k': 13, 'l': 6, 'm': 20, 'n': 14, 'o': 14, 'p': 14, 'q': 14, 'r': 9, 's': 12, 't': 8, 'u': 14, 'v': 13, 'w': 19,
    'x': 13, 'y': 13, 'z': 13, '{': 10, '|': 8, '}': 10, '~': 15, '·': 7, 'é': 13, '–': 11, '—': 22, '‘': 7, '’': 7,
    '“': 12, '”': 12, '−': 15,
}}
#: The most each adjacent pair of `INTER_BOLD_ADVANCES` characters can be WIDENED by the face's GPOS `kern` feature
#: (font units; summed over its lookups, each lookup's largest positive x-advance for the pair in any subtable — an
#: over-approximation of HarfBuzz's first-match). Pairs it only narrows (or leaves alone) are not listed. Pinned to the
#: font's GPOS by tests/test_marketing_news_layouts.py.
POSITIVE_KERNS: Dict[str, int] = {
    '(B': 37, '(D': 37, '(E': 37, '(F': 37, '(H': 37, '(I': 37, '(K': 37, '(L': 37, '(M': 37, '(N': 37, '(P': 37,
    '(R': 37, '(b': 37, '(h': 37, '(j': 27, '(k': 37, '(l': 37, '(m': 37, '(n': 37, '(p': 37, '(r': 37, '(|': 37,
    '/V': 16, '/W': 16, '/\\': 16, '0t': 35, '1)': 37, '1]': 37, '1_': 37, '1t': 30, '1v': 21, '1y': 21, '1}': 37,
    '77': 40, '9t': 35, 'A/': 24, 'AA': 30, 'Ai': 5, 'Cv': 12, 'Cy': 12, 'H)': 37, 'H]': 37, 'H_': 37, 'Ht': 30,
    'Hv': 21, 'Hy': 21, 'H}': 37, 'I)': 37, 'I]': 37, 'I_': 37, 'It': 30, 'Iv': 21, 'Iy': 21, 'I}': 37, 'Ki': 15,
    'M)': 37, 'M]': 37, 'M_': 37, 'Mt': 30, 'Mv': 21, 'My': 21, 'M}': 37, 'N)': 37, 'N]': 37, 'N_': 37, 'Nt': 30,
    'Nv': 21, 'Ny': 21, 'N}': 37, 'TY': 18, 'VV': 30, 'V\\': 30, 'WV': 16, 'WW': 45, 'W\\': 16, 'YT': 18, '[B': 37,
    '[D': 37, '[E': 37, '[F': 37, '[H': 37, '[I': 37, '[K': 37, '[L': 37, '[M': 37, '[N': 37, '[P': 37, '[R': 37,
    '[b': 37, '[h': 37, '[j': 27, '[k': 37, '[l': 37, '[m': 37, '[n': 37, '[p': 37, '[r': 37, '[|': 37, '\\/': 24,
    '\\A': 30, '\\i': 5, '_B': 69, '_D': 69, '_E': 69, '_F': 69, '_H': 69, '_I': 69, '_K': 69, '_L': 69, '_M': 69,
    '_N': 69, '_P': 69, '_R': 69, '_b': 69, '_h': 69, '_i': 32, '_j': 197, '_k': 69, '_l': 69, '_m': 69, '_n': 69,
    '_p': 69, '_r': 69, '_|': 69, 'ba': 20, 'ca': 20, 'd)': 37, 'd]': 37, 'd_': 37, 'dt': 30, 'dv': 21, 'dy': 21,
    'd}': 37, 'ea': 20, 'fY': 9, 'fi': 17, 'fj': 11, 'fw': 41, 'gj': 18, 'kv': 16, 'ky': 16, 'l)': 37, 'l]': 37,
    'l_': 37, 'lt': 30, 'lv': 21, 'ly': 21, 'l}': 37, 'oa': 20, 'pa': 20, 'qj': 18, 'rB': 32, 'rD': 32, 'rE': 32,
    'rF': 32, 'rH': 32, 'rI': 32, 'rK': 32, 'rL': 32, 'rM': 32, 'rN': 32, 'rP': 32, 'rR': 32, 'rb': 32, 'rf': 40,
    'rh': 32, 'ri': 9, 'rj': 31, 'rk': 32, 'rl': 32, 'rm': 15, 'rn': 15, 'rp': 15, 'rr': 15, 'rt': 40, 'rv': 32,
    'rw': 17, 'rx': 32, 'ry': 32, 'r|': 32, 'r’': 33, 'r”': 33, 'tB': 5, 'tD': 5, 'tE': 5, 'tF': 5, 'tH': 5, 'tI': 5,
    'tK': 5, 'tL': 5, 'tM': 5, 'tN': 5, 'tP': 5, 'tR': 5, 'ta': 10, 'tb': 5, 'th': 5, 'tj': 15, 'tk': 5, 'tl': 5,
    'tv': 45, 'tw': 41, 'ty': 45, 't|': 5, 't’': 23, 't”': 23, 'u’': 29, 'u”': 29, 'vB': 5, 'vD': 5, 'vE': 5,
    'vF': 5, 'vH': 5, 'vI': 5, 'vK': 5, 'vL': 5, 'vM': 5, 'vN': 5, 'vP': 5, 'vR': 5, 'vb': 5, 'vf': 30, 'vh': 5,
    'vk': 5, 'vl': 5, 'vt': 35, 'v|': 5, 'xf': 29, 'xt': 29, 'yB': 5, 'yD': 5, 'yE': 5, 'yF': 5, 'yH': 5, 'yI': 5,
    'yK': 5, 'yL': 5, 'yM': 5, 'yN': 5, 'yP': 5, 'yR': 5, 'yb': 5, 'yf': 30, 'yh': 5, 'yk': 5, 'yl': 5, 'yt': 35,
    'y|': 5, '{B': 37, '{D': 37, '{E': 37, '{F': 37, '{H': 37, '{I': 37, '{K': 37, '{L': 37, '{M': 37, '{N': 37,
    '{P': 37, '{R': 37, '{b': 37, '{h': 37, '{j': 27, '{k': 37, '{l': 37, '{m': 37, '{n': 37, '{p': 37, '{r': 37,
    '{|': 37, '|)': 37, '|]': 37, '|_': 37, '|t': 30, '|v': 21, '|y': 21, '|}': 37, 'éa': 20, '’t': 13, '’v': 13,
    '’y': 13, '”t': 13, '”v': 13, '”y': 13,
}
#: The most ANY pair of the face's glyphs can be widened (the sum over its `kern` lookups of each one's largest positive
#: x-advance): a pair touching a character outside the table counts this.
MAX_PAIR_KERN = 521
#: HarfBuzz positions in 1/64 px: an advance or a kerning value is rounded to that grid (at most half a step up).
_SUBPIXEL = 1 / 64


def text_width(text: str, px: int) -> float:
    """An UPPER BOUND on the width (pixels) the worker draws `text` in at `px` (see the module docstring)."""
    scale = px / UNITS_PER_EM
    hinted = HINTED_ADVANCES.get(px)
    total = 0.0
    for ch in text:
        units = INTER_BOLD_ADVANCES.get(ch)
        if units is None:
            total += WIDEST_ADVANCE * scale + 0.5
        elif hinted is not None:
            total += max(hinted[ch], units * scale + _SUBPIXEL)
        else:
            total += units * scale + 0.5
    for pair in zip(text, text[1:]):
        if pair[0] not in INTER_BOLD_ADVANCES or pair[1] not in INTER_BOLD_ADVANCES:
            total += MAX_PAIR_KERN * scale + _SUBPIXEL
        else:
            widen = POSITIVE_KERNS.get(pair[0] + pair[1])
            if widen:
                total += widen * scale + _SUBPIXEL
    return total if hinted is not None else total + 1.0


def wrap_lines(text: str, px: int, width: float) -> List[str]:
    """`text` word-wrapped greedily into lines no wider than `width` at `px` (by `text_width`) — the worker's own wrap
    (`look.wrap`). A single word wider than `width` is its own line (the caller checks `fits`)."""
    lines: List[str] = []
    cur = ""
    for word in text.split():
        trial = f"{cur} {word}" if cur else word
        if cur and text_width(trial, px) > width:
            lines.append(cur)
            cur = word
        else:
            cur = trial
    if cur:
        lines.append(cur)
    return lines


def fits(lines: Sequence[str], px: int, width: float) -> bool:
    """Every line no wider than `width` at `px`."""
    return all(text_width(line, px) <= width for line in lines)


def line_count(text: str, px: int, width: float) -> int:
    """How many lines the worker draws `text` in at `px` in `width` — 10**6 when a word alone is wider (it cannot be
    drawn whole)."""
    lines = wrap_lines(text, px, width)
    return len(lines) if fits(lines, px, width) else 10 ** 6


__all__ = ["UNITS_PER_EM", "INTER_BOLD_ADVANCES", "WIDEST_ADVANCE", "text_width", "wrap_lines", "fits",
           "line_count"]
