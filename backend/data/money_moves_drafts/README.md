# Money Moves drafts — 3 hand-written articles (2026-11-01), staged, NOT integrated

Verified and FROZEN (the narration must be rendered from exactly this text):
`mcdonalds-the-real-estate-empire.json`, `coca-cola-vs-pepsico.json`,
`kodak-inventing-the-future-it-couldnt-sell.json`. Their sources and every correction made after the
two-verifier audit are in `documents/money_moves_sources.md` (also `CORRECTIONS.md` here).
`ledgers/` holds the writers' claim ledgers (written BEFORE the corrections), and `verify/` holds the six
verifier reports.

Status:
- Art: DONE, in `backend/data/money_moves_art/` (the coca-cola master was retouched locally; the original
  is in `_prev/`).
- Narration: McDonald's DONE (`backend/data/money_moves_audio/mcdonalds-the-real-estate-empire.m4a`).
  Coca-Cola and Kodak are pending and must be rendered OFF the laptop (owner: "i don't want my laptop
  on fire"). Use the catalog voice: ref `caydex_voice_achird_v2.wav`, exag 0.65, cfg 0.40,
  CLONE_MODE=block, 165 WPM, 0.6 s block pause, `-b:a 96k`. Clips land at
  `backend/data/money_moves_audio/<slug>.m4a`.

Order once every clip and its read-along exist (from `backend/`):
1. `python3 data/money_moves_drafts/integrate.py`: appends the 3 articles to BOTH money_moves.json
   files (sortOrder 23-25, publishedDaysAgo 0, real ffprobe durations) and adds back-links in costco,
   home-depot-vs-lowes, the-fall-of-sears and microsofts-cloud-metamorphosis.
2. (No art-table step: the three ARTICLES entries already live, byte-identical, in
   `app/services/money_moves_art_style.py`, which the art script imports. `art_subjects.json` stays
   as the record.)
3. `python3 data/money_moves_drafts/apply_test_edits.py`: 23 → 26 in the three tests that pin the size.
4. (Narration + read-along come from Railway BEFORE step 1:
   `scripts/learn_catalog_narrate.py --slug <slug> --yes`, then `scripts/learn_catalog_fetch.py --slug
   <slug> --yes`, which writes the clip and merges readAlong/itemsReadAlong + audioDurationSeconds into
   the draft here. integrate.py keeps that duration and warns about a draft with no read-along.)
5. Tests: `tests/test_money_moves_*.py tests/test_learn_*.py tests/test_marketing_content_pool.py`,
   then add `_TITLE_COMPANIES` entries if the content-pool test asks.
6. `./venv/bin/python scripts/seed_money_moves.py --dry-run`, then the real seed (production write:
   ask the owner first), then `scripts/check_money_moves_published.py`.

`check_article.py <draft.json>` re-runs the bundle rules (validators, icons, banned phrases) on a draft.
