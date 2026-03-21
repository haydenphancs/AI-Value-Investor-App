-- Drop foreign key constraint on watchlist_items.user_id
-- This allows the backend to insert for guest users (UUID 00000000...)
-- and for users managed by custom JWT auth (not in auth.users)

ALTER TABLE watchlist_items DROP CONSTRAINT IF EXISTS watchlist_items_user_id_fkey;
ALTER TABLE portfolio_holdings DROP CONSTRAINT IF EXISTS portfolio_holdings_user_id_fkey;
