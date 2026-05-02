-- Sector aggregates cache table.
--
-- Pre-computed per-sector market structure metrics that the
-- ticker-report's Industry & Competitive Moat module reads from on
-- every request. Populated by `sector_aggregates_service.compute_and_
-- persist_all_sectors()` from a scheduled job (not from request paths).
--
-- HHI and top-N share percentages are derived from S&P 500 constituent
-- market caps within each sector. The 5y CAGR is the geometric growth
-- rate of summed sector revenue.
--
-- Reads are gated 24h stale in the service layer (rows older than that
-- → return None → ticker report renders honest defaults instead of stale
-- numbers). Writes always upsert by sector primary key.

create table if not exists public.sector_aggregates (
  sector text primary key,
  total_revenue_usd numeric,
  cagr_5yr_pct numeric,
  hhi numeric,
  top1_share_pct numeric,
  top2_share_pct numeric,
  num_constituents integer,
  computed_at timestamptz not null default now()
);

-- Row Level Security: enabled so the table can't be read via the anon
-- key by default. The FastAPI backend uses the service role which
-- bypasses RLS, so its reads/writes still work without a policy.
alter table public.sector_aggregates enable row level security;

-- Authenticated app users may read the latest aggregates (e.g. for an
-- in-app debug panel showing which sector benchmarks are loaded). They
-- can never write — there is no insert/update/delete policy.
create policy "sector_aggregates_read_authenticated"
  on public.sector_aggregates
  for select
  to authenticated
  using (true);

-- Service role does the heavy reads/writes from the FastAPI backend.
-- Grants are still set explicitly because Supabase's default privileges
-- for new tables don't include the service role; RLS bypass alone is
-- not sufficient without the underlying GRANT.
grant select, insert, update, delete on public.sector_aggregates to service_role;

-- The authenticated grant pairs with the SELECT policy above.
grant select on public.sector_aggregates to authenticated;
