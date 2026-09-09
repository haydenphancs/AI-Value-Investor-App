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
-- (`watchlist_items.asset_type` defaults to 'Stock' and the add endpoint never wrote it),
-- so these nine bare symbols are resolved toward the COIN. That matches every other
-- convention in the app: search lists the coin FIRST for an exact match, the crypto
-- screen's star writes the bare form, and `asset_class._BARE_CRYPTO_SYMBOLS` classifies
-- them as crypto. A user who genuinely tracked one of the seven securities above can
-- re-add it; after this migration the bare form means exactly that.
--
-- price_alerts DOES carry a trustworthy `asset_type` (the sheet sends "crypto"), so its
-- rows are converted only where that column actually says crypto — no guessing needed.
--
-- Idempotent: re-running is a no-op because the WHERE clauses exclude anything already
-- ending in USD, and the ON CONFLICT arms drop rows that would collide with a pair-form
-- row the user already has.

BEGIN;

-- ── 1. price_alerts — asset_type is reliable here, so no guess ──────────────
-- DESTRUCTIVE (bounded): drops a bare-form rule only where renaming it would collide with
-- an identical pair-form rule the user already has. The unique key is
-- `price_alerts_no_dupes UNIQUE (user_id, ticker, kind, threshold)`, so all FOUR columns
-- must match — matching on user_id+ticker alone would delete a rule that differs in kind
-- or threshold and could have been renamed safely. No data is lost that the user does not
-- already have under the canonical ticker.
DELETE FROM price_alerts a
USING price_alerts b
WHERE  a.user_id = b.user_id
  AND  lower(a.asset_type) = 'crypto'
  AND  a.ticker IN ('BTC','ETH','SOL','ADA','DOT','AVAX','MATIC','LINK','XRP',
                    'DOGE','SHIB','UNI','AAVE','LTC','BCH','ATOM')
  AND  b.ticker = a.ticker || 'USD'
  AND  b.kind = a.kind
  AND  b.threshold IS NOT DISTINCT FROM a.threshold
  AND  a.id <> b.id;

UPDATE price_alerts
SET    ticker = ticker || 'USD'
WHERE  lower(asset_type) = 'crypto'
  AND  ticker IN ('BTC','ETH','SOL','ADA','DOT','AVAX','MATIC','LINK','XRP',
                  'DOGE','SHIB','UNI','AAVE','LTC','BCH','ATOM');

-- ── 2. watchlist_items — no usable asset_type; resolve toward the coin ──────
-- DESTRUCTIVE (bounded): the unique key is `watchlist_items_user_id_ticker_key
-- UNIQUE (user_id, ticker)`, so a bare row is dropped only when the user already holds
-- the pair form. Nothing they do not already have is lost.
DELETE FROM watchlist_items a
USING watchlist_items b
WHERE  a.user_id = b.user_id
  AND  a.ticker IN ('BTC','ETH','SOL','ADA','DOT','AVAX','MATIC','LINK','XRP',
                    'DOGE','SHIB','UNI','AAVE','LTC','BCH','ATOM')
  AND  b.ticker = a.ticker || 'USD'
  AND  a.id <> b.id;

UPDATE watchlist_items
SET    ticker = ticker || 'USD'
WHERE  ticker IN ('BTC','ETH','SOL','ADA','DOT','AVAX','MATIC','LINK','XRP',
                  'DOGE','SHIB','UNI','AAVE','LTC','BCH','ATOM');

COMMIT;
