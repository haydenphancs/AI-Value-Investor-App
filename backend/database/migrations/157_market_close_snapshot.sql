-- 157_market_close_snapshot.sql
--
-- Why: FMP enforced its Data Packages on 2026-09-03, and the `quote` / `batch-quote`
-- family ("Real-time Market Data") is not one we bought — both now answer
-- `402 Restricted Endpoint`. Those two endpoints supplied every price in the app across
-- 39 call sites, so Home tiles, the watchlist, Updates pills, price alerts and the widget
-- are all serving nothing today.
--
-- The entitled replacements cover price but NOT the day change:
--
--   * `/stable/profile`             -> live price AND `change` / `changePercentage`, but
--                                     ONE symbol per call (`?symbol=A,B` returns []).
--   * `/stable/company-screener`    -> live price, volume, marketCap, sector, industry for
--                                     the whole US universe in ~1 s, but it has NO change
--                                     field at all (verified: the payload's only
--                                     "change"-ish keys are `exchange` /
--                                     `exchangeShortName`).
--
-- So a BATCH change% needs a previous close from somewhere, and fetching it per symbol
-- would be one call per tile. This table is that somewhere.
--
-- Source: `/stable/batch-eod?date=YYYY-MM-DD` — entitled under package 7 ("EOD Price"),
-- and it returns the WHOLE market's official OHLCV for one session in a single request:
-- 65,690 rows / 11.7 MB / ~10 s, measured. Far too heavy for a request path, and it only
-- changes once per session, so a daily job writes it here and reads are keyed by symbol.
--
-- Why the official close and not a screener sample: the screener reports whatever the
-- price is at the moment you ask. A "snapshot at 16:00" is therefore approximate and
-- drifts with job timing, and any drift shows up as a wrong day-change % on the user's
-- own holdings. `batch-eod` is the settled close for a named `date`, so the denominator
-- is exact and reproducible.
--
-- 🔴 ENTITLEMENT — the ingest MUST filter, and this is not optional.
-- `batch-eod` is inconsistently enforced on FMP's side: it includes `^GSPC`, `GCUSD`,
-- `BTCUSD` and `EURUSD`, all of which return 402 on the per-symbol
-- `historical-price-eod/full`. Index, commodity, crypto and FX market data is NOT in any
-- purchased package. Ingesting those rows from the bulk endpoint would be taking data FMP
-- deliberately blocks elsewhere — exactly what ToS §2.10 (monitor and terminate) exists
-- for. `price_service` drops them via `fmp_entitlements.is_blocked_symbol()` before the
-- upsert, so this table can never become a back door. Do not "fix" that filter to make an
-- index chart work; buy the package instead (one line in `PURCHASED_PACKAGES`).
--
-- Shape: one row per symbol holding only the MOST RECENT close. No history — the daily
-- bars already live in `historical-price-eod/*` and duplicating them here would be a
-- second, staler copy of a dataset we can already fetch per symbol.
--
-- Deploy order does NOT matter. `price_service` wraps its read in try/except: until this
-- migration is applied it logs a warning about the missing relation and returns no
-- previous closes, so batch change% is simply absent and the caller hides the field
-- rather than showing a fabricated 0.00%. Degraded, never wrong. (Same posture as 148/149.)
--
-- Idempotent: every statement is IF (NOT) EXISTS.

CREATE TABLE IF NOT EXISTS public.market_close_snapshot (
    symbol      TEXT PRIMARY KEY,
    trade_date  DATE        NOT NULL,
    close       NUMERIC     NOT NULL,
    volume      BIGINT,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Reads are always "the closes for these N symbols" (a watchlist, the Home tiles), so the
-- PK on `symbol` already serves the hot path. This index serves the OTHER access pattern:
-- the daily job checking what the newest stored session is before deciding to re-ingest,
-- and pruning rows left behind by delistings.
CREATE INDEX IF NOT EXISTS idx_market_close_snapshot_trade_date
    ON public.market_close_snapshot (trade_date);

ALTER TABLE public.market_close_snapshot ENABLE ROW LEVEL SECURITY;

-- 🔴 SERVICE-ROLE ONLY — deliberately NOT the `*_cache` public-read template.
--
-- The cache-table template in .claude/rules/database.md grants SELECT to anon and
-- authenticated. That is right for a per-ticker cache a signed-in user is already
-- entitled to see. It is wrong here: this table is a near-complete dump of one session's
-- closes for tens of thousands of symbols, and the anon key ships inside the iOS binary.
-- Granting public read would publish a bulk FMP-derived dataset to anyone who extracts
-- that key — redistribution under FMP ToS §2.6.1, which bars distributing "data or
-- information contained in or derived from The Services", and a much worse version of the
-- `benchmark_universe.json`-in-a-public-repo problem already on the launch checklist.
--
-- The backend reads this with the service role and serves only the handful of symbols a
-- given screen needs, which is display (licensed) rather than redistribution (not).
DROP POLICY IF EXISTS "market_close_snapshot_service_all" ON public.market_close_snapshot;
CREATE POLICY "market_close_snapshot_service_all" ON public.market_close_snapshot
    FOR ALL TO service_role USING (true) WITH CHECK (true);

REVOKE ALL ON public.market_close_snapshot FROM anon, authenticated;
GRANT ALL ON public.market_close_snapshot TO service_role;

COMMENT ON TABLE public.market_close_snapshot IS
    'Most recent official close per symbol, ingested daily from FMP /stable/batch-eod. '
    'Supplies the denominator for batch day-change %, which the entitled '
    'company-screener cannot provide (it has no change field) and profile can only give '
    'one symbol at a time. Service-role only: a bulk close dump must not be readable with '
    'the shipped anon key (FMP ToS 2.6.1). Index/commodity/crypto/FX symbols are filtered '
    'out on ingest — they are not in any purchased package.';
