"""Trillion-Dollar Club Bets — what the companies worth $1 trillion or more own in others.

Package layout (see ``backend/app/services/trillion_club_service.py`` for the read path):

* ``rules``   — pure: club membership (join after 10 straight dated closes at or above
  $1.00T, leave after 20 straight below; owner overrides), SEC 13F due dates on the
  FEDERAL business-day calendar, quarter helpers, CUSIP -> US ISIN, accession parsing and
  the card-notice predicates.
* ``builder`` — builds one ``trillion_club_filings`` row from FMP's 13F extract:
  per-accession normalisation (a 13F-HR/A folds into the original quarter), symbol
  resolution, profiles, split restatement and the quarter-over-quarter share diff.
* ``store`` / ``jobs`` / ``scheduler`` — Supabase I/O and the daily/weekly loops (plus the
  daily 18:30 ET stake-finder loop).
* The stake finder (migration 205) — finds a member's newly disclosed stakes in SEC filings:
  ``stake_contracts`` (vocabulary, the filers file, the Telegram callback grammar),
  ``stake_names`` (name / class / ticker / CUSIP helpers), ``stake_rules`` (the stake
  validators, shared with ``scripts/seed_trillion_club.py``), ``stake_schedule`` (a member's
  own Schedule 13D/13G XML), ``stake_templates`` (fixed templates over other companies' 8-K /
  6-K / prospectus text), ``stake_store`` (its only Supabase I/O), ``stake_finder`` (the run:
  reconcile, discovery, gates, write orders, taps) and ``stake_review`` (the owner's Telegram
  notes and Withdraw / Keep / Publish / Dismiss taps). Every row it creates carries
  ``origin`` 'auto' or 'proposal'; it never writes a seed row.

Nothing here writes a ``whales`` row, a follow, a push or a whale alert: a company's 13F
is not an investor's "trade", and most Q2 2026 "new" rows were IPO conversions.
"""
