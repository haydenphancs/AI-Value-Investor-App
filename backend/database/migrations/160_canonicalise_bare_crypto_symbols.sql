-- 160_canonicalise_bare_crypto_symbols.sql
--
-- Why: a BARE coin ticker is ambiguous, and the app knows it. `GET /stocks/search`
-- deliberately returns BOTH rows for "BTC" — "Bitcoin" (from the local crypto map) and
-- "Grayscale Bitcoin Mini Trust ETF" (from FMP) — because seven of the sixteen bare coin
-- tickers are ALSO real, actively-traded US listings:
--
--     BTC  -> Grayscale Bitcoin Mini Trust ETF   (AMEX)
--     ETH  -> Grayscale Ethereum Mini Trust ETF  (AMEX)
--     XRP  -> Bitwise XRP ETF                    (AMEX)
--     LTC  -> LTC Properties, Inc.  (a REIT)     (NYSE)
--     BCH  -> Banco de Chile                     (NYSE)
--     ATOM -> Atomera Incorporated               (NASDAQ)
--     SOL  -> Emeren Group, Ltd.                 (NYSE, inactive)
--
-- Both choices were persisted as the identical string, after which nothing downstream
-- could tell them apart — so the price shown was whichever source the routing happened to
-- pick. That produced a REIT rendered as "Litecoin", and Bitcoin priced at $34.
--
-- Resolution: coins are stored in the PAIR form ("BTCUSD"), which is what
-- `asset_class.uses_coingecko_price` routes to CoinGecko, leaving the bare form free to
-- mean the listed security. `POST /watchlist` and the price-alert create endpoint now
-- canonicalise on write (`asset_class.canonical_stored_symbol`); this migration fixes the
-- rows written before that.
--
-- ⚠️ DIRECTION OF THE GUESS. Existing watchlist rows carry no usable asset type
-- (`watchlist_items.asset_type` defaults to 'Stock' and NOTHING has ever written it —
-- verified in production: all 35 rows are the default), so these bare symbols are resolved
-- toward the COIN. That matches every other convention in the app: search lists the coin
-- FIRST for an exact match, the crypto screen's star writes the bare form, and
-- `asset_class._BARE_CRYPTO_SYMBOLS` classifies them as crypto. A user who genuinely
-- tracked one of the seven securities above can re-add it; after this migration the bare
-- form means exactly that.
--
-- price_alerts DOES carry a trustworthy `asset_type` (the sheet sends "crypto"), so its
-- rows are converted only where that column actually says crypto — no guessing needed.
--
-- ─────────────────────────────────────────────────────────────────────────────
-- THREE THINGS AN EARLIER DRAFT OF THIS FILE GOT WRONG. Each was caught by an
-- adversarial pre-flight review against the live database, and each would have shipped a
-- worse state than the one it was fixing.
--
-- 1. ⚠️ portfolio_items MUST move in the SAME transaction. `portfolios.py` states the
--    invariant in its module docstring — "Tickers in portfolio_items.ticker must already
--    exist as a watchlist row for the same user" — and `PUT /portfolios/{id}/tickers`
--    ENFORCES it destructively: it filters the requested tickers against watchlist_items,
--    then DELETEs every row for the portfolio and reinserts only the survivors.
--    `PortfolioStore.pruneTickers` does the same from the client. Converting the watchlist
--    alone would orphan every bare crypto position, and the user's next portfolio edit
--    would SILENTLY DELETE it together with its hand-entered `shares` / `market_value`.
--    Measured before applying: 0 orphaned portfolio items today, and converting
--    watchlist_items alone would have created exactly 5 — including a 2.2686 ETH position.
--
-- 2. ⚠️ The row's ENRICHMENT describes the SECURITY, not the coin, and renaming only
--    `ticker` produces the INVERTED form of the very bug this migration exists to fix.
--    Measured in production, these are the actual stored values:
--        BTC  -> company_name 'Grayscale Bitcoin Mini Trust ETF'  + that ETF's logo
--        ETH  -> company_name 'Grayscale Ethereum Mini Trust ETF' + market_cap 2.55e9,
--                                                                   beta 2.4860
--        SOL  -> company_name 'Emeren Group, Ltd.'                (a NYSE solar company)
--    A ticker rename alone would leave "Emeren Group, Ltd." priced from Solana. So the
--    security-specific columns are repaired in the same statement.
--
-- 3. ⚠️ price_alerts.last_price is a BASELINE measured against the other asset. Carrying a
--    $34 baseline onto a symbol that now prices at $78,000 manufactures a crossing on the
--    very next sweep — mass-firing pushes and, for `repeat_mode='once'` rules, permanently
--    deactivating them. It is cleared with the rename, so the next sweep re-baselines.
--    (Zero crypto alerts exist in production today; this keeps the file correct anyway.)
--
-- Idempotent: re-running is a no-op because every WHERE clause joins on the BARE form and
-- nothing bare remains afterwards, and the DELETE arms drop only rows that would collide
-- with a pair-form row the same user/portfolio already has.

BEGIN;

-- ── 0. The symbol map, declared ONCE ────────────────────────────────────────
-- An earlier draft repeated the 16-symbol IN-list six times. That is exactly how the
-- migration and `asset_class.canonical_stored_symbol` drift apart, so it is a temp table
-- joined by every arm below instead. `coin_name` is the curated name from
-- `crypto_service._CRYPTO_PROFILES` where one exists.
CREATE TEMP TABLE _coin_canon (
    bare      text PRIMARY KEY,
    pair      text NOT NULL,
    coin_name text NOT NULL
) ON COMMIT DROP;

INSERT INTO _coin_canon (bare, pair, coin_name) VALUES
    ('BTC',   'BTCUSD',   'Bitcoin'),
    ('ETH',   'ETHUSD',   'Ethereum'),
    ('SOL',   'SOLUSD',   'Solana'),
    ('ADA',   'ADAUSD',   'Cardano'),
    ('DOT',   'DOTUSD',   'Polkadot'),
    ('AVAX',  'AVAXUSD',  'Avalanche'),
    ('MATIC', 'MATICUSD', 'Polygon'),
    ('LINK',  'LINKUSD',  'Chainlink'),
    ('XRP',   'XRPUSD',   'XRP'),
    ('DOGE',  'DOGEUSD',  'Dogecoin'),
    ('SHIB',  'SHIBUSD',  'Shiba Inu'),
    ('UNI',   'UNIUSD',   'Uniswap'),
    ('AAVE',  'AAVEUSD',  'Aave'),
    ('LTC',   'LTCUSD',   'Litecoin'),
    ('BCH',   'BCHUSD',   'Bitcoin Cash'),
    ('ATOM',  'ATOMUSD',  'Cosmos Hub');

-- ── 1. price_alerts — asset_type is reliable here, so no guess ──────────────
-- DESTRUCTIVE (bounded): drops a bare-form rule only where renaming it would collide with
-- an identical pair-form rule the user already has. The unique key is
-- `price_alerts_no_dupes UNIQUE (user_id, ticker, kind, threshold)` (verified against the
-- live catalog), so all FOUR columns must match — matching on user_id+ticker alone would
-- delete a rule that differs in kind or threshold and could have been renamed safely.
--
-- ⚠️ The surviving row is not necessarily the one the user would keep: `repeat_mode`,
-- `is_active`, `armed`, `note` and `trigger_count` sit OUTSIDE the unique key, so a dead
-- one-shot rule can outlive an active repeating one. Prefer the ACTIVE row explicitly
-- rather than leaving it to physical order.
-- Two statements, because a collision must ALWAYS be resolved. Deleting only the bare row
-- when the pair row wins leaves the opposite case unresolved — and then the UPDATE below
-- renames the bare row onto the existing pair row and aborts the whole transaction on
-- `price_alerts_no_dupes`. So: first drop the PAIR row where the BARE one is strictly more
-- useful (it is about to be renamed into that slot), then drop any bare row still colliding.
DELETE FROM price_alerts b
USING price_alerts a, _coin_canon c
WHERE  a.ticker = c.bare
  AND  b.ticker = c.pair
  AND  a.user_id = b.user_id
  AND  lower(a.asset_type) = 'crypto'
  AND  b.kind = a.kind
  AND  b.threshold IS NOT DISTINCT FROM a.threshold
  AND  a.id <> b.id
  -- An active rule beats an inactive one; on a tie the newer wins.
  AND (COALESCE(a.is_active, true), a.created_at)
        > (COALESCE(b.is_active, true), b.created_at);

DELETE FROM price_alerts a
USING price_alerts b, _coin_canon c
WHERE  a.ticker = c.bare
  AND  b.ticker = c.pair
  AND  a.user_id = b.user_id
  AND  lower(a.asset_type) = 'crypto'
  AND  b.kind = a.kind
  AND  b.threshold IS NOT DISTINCT FROM a.threshold
  AND  a.id <> b.id;

UPDATE price_alerts a
SET    ticker            = c.pair,
       -- Re-baseline: see note 3 in the header. A baseline taken against the listed
       -- security would fire spuriously on the next sweep.
       last_price        = NULL,
       last_evaluated_at = NULL,
       updated_at        = now()
FROM   _coin_canon c
WHERE  a.ticker = c.bare
  AND  lower(a.asset_type) = 'crypto';

-- ── 2. watchlist_items — no usable asset_type; resolve toward the coin ──────
-- DESTRUCTIVE (bounded): the unique key is `watchlist_items_user_id_ticker_key
-- UNIQUE (user_id, ticker)` (verified live), so a bare row is dropped only when the user
-- already holds the pair form.
--
-- ⚠️ `shares` and `market_value` are hand-entered and sit outside that key, so prefer the
-- row that actually carries holdings — otherwise the survivor can be the empty one and the
-- user's typed position is destroyed. The header's original "nothing is lost" claim was
-- true only because no such collision exists in production today (measured: 0).
-- Same two-statement shape as price_alerts above, and for the same reason: a one-sided
-- DELETE turns the winning-bare-row case into a unique violation on the UPDATE.
DELETE FROM watchlist_items b
USING watchlist_items a, _coin_canon c
WHERE  a.ticker = c.bare
  AND  b.ticker = c.pair
  AND  a.user_id = b.user_id
  AND  a.id <> b.id
  AND (a.shares IS NOT NULL OR a.market_value IS NOT NULL)
        > (b.shares IS NOT NULL OR b.market_value IS NOT NULL);

DELETE FROM watchlist_items a
USING watchlist_items b, _coin_canon c
WHERE  a.ticker = c.bare
  AND  b.ticker = c.pair
  AND  a.user_id = b.user_id
  AND  a.id <> b.id;

-- The rename AND the enrichment repair, together — see note 2 in the header. Leaving the
-- FMP profile columns behind is what would turn "priced as the ETF, labelled Bitcoin" into
-- "priced as Bitcoin, labelled Grayscale Bitcoin Mini Trust ETF".
--   • company_name -> the coin's real name.
--   • logo_url / sector / industry / market_cap / beta -> NULL. These are the listed
--     security's. NULL is a well-tested state: every watchlist-only row had a NULL sector
--     until recently, and nothing re-enriches on read, so a stale value would be permanent.
--   • asset_type -> 'crypto'. Nothing has ever written this column, and
--     `tracking_service` lower()s it to choose the 24/7 sparkline window — so 'Stock'
--     currently gives a coin a market-hours session.
--   • country is deliberately left alone: it is not security-identifying, and no
--     NULL-handling for it has been verified.
UPDATE watchlist_items w
SET    ticker       = c.pair,
       company_name = c.coin_name,
       asset_type   = 'crypto',
       logo_url     = NULL,
       sector       = NULL,
       industry     = NULL,
       market_cap   = NULL,
       beta         = NULL
FROM   _coin_canon c
WHERE  w.ticker = c.bare;

-- ── 3. portfolio_items — must track watchlist_items exactly (see note 1) ────
-- A portfolio is a named SUBSET of the watchlist, seeded from it by copying the ticker
-- verbatim (`_seed_default_portfolio`), and both the server (`PUT /portfolios/{id}/tickers`)
-- and the client (`PortfolioStore.pruneTickers`) DELETE members that are not on the
-- watchlist. Leaving these bare would not merely mis-price the position (Grayscale's ETH
-- ETF at ~$24 instead of Ethereum at ~$2,490 — a 105x error on the user's own money); it
-- would DESTROY it on the next edit, along with `shares` and `market_value`.
--
-- DESTRUCTIVE (bounded): the unique key is `portfolio_items_portfolio_id_ticker_key
-- UNIQUE (portfolio_id, ticker)` (verified live). Scoped by portfolio_id, not user_id —
-- holdings are deliberately independent across a user's portfolios.
DELETE FROM portfolio_items b
USING portfolio_items a, _coin_canon c
WHERE  a.ticker = c.bare
  AND  b.ticker = c.pair
  AND  a.portfolio_id = b.portfolio_id
  AND  a.id <> b.id
  AND (a.shares IS NOT NULL OR a.market_value IS NOT NULL)
        > (b.shares IS NOT NULL OR b.market_value IS NOT NULL);

DELETE FROM portfolio_items a
USING portfolio_items b, _coin_canon c
WHERE  a.ticker = c.bare
  AND  b.ticker = c.pair
  AND  a.portfolio_id = b.portfolio_id
  AND  a.id <> b.id;

UPDATE portfolio_items p
SET    ticker = c.pair
FROM   _coin_canon c
WHERE  p.ticker = c.bare;

COMMIT;
