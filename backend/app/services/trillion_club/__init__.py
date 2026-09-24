"""Trillion-Dollar Club Bets — what the companies worth $1 trillion or more own in others.

Package layout (see ``backend/app/services/trillion_club_service.py`` for the read path):

* ``rules``   — pure: club membership (join after 10 straight dated closes at or above
  $1.00T, leave after 20 straight below; owner overrides), SEC 13F due dates on the
  FEDERAL business-day calendar, quarter helpers, CUSIP -> US ISIN, accession parsing and
  the card-notice predicates.
* ``builder`` — builds one ``trillion_club_filings`` row from FMP's 13F extract:
  per-accession normalisation (a 13F-HR/A folds into the original quarter), symbol
  resolution, profiles, split restatement and the quarter-over-quarter share diff.
* ``store`` / ``jobs`` / ``scheduler`` — Supabase I/O and the daily/weekly loops.

Nothing here writes a ``whales`` row, a follow, a push or a whale alert: a company's 13F
is not an investor's "trade", and most Q2 2026 "new" rows were IPO conversions.
"""
