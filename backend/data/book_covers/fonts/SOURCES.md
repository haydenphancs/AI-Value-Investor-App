# Fonts used by the book-cover pipeline

| File | Family | Licence | Source |
|---|---|---|---|
| `Inter-var.ttf` | Inter (variable: opsz, wght) | SIL OFL 1.1 — `LICENSES/Inter-OFL.txt` | https://raw.githubusercontent.com/google/fonts/main/ofl/inter/Inter%5Bopsz%2Cwght%5D.ttf |

Committed on purpose. The compositor is deterministic only if the exact font binary is
pinned — a system font would drift with macOS releases, and Apple/Monotype faces
(`Didot.ttc`, `Futura.ttc`) cannot be redistributed at all. `.ttc` collections are also
unusable here: `ImageFont.truetype` needs an `index=` whose ordering is not stable.

To re-fetch:
    curl -L -o Inter-var.ttf 'https://raw.githubusercontent.com/google/fonts/main/ofl/inter/Inter%5Bopsz%2Cwght%5D.ttf'
