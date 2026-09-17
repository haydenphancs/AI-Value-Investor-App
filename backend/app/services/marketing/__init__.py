"""
Marketing engine — the WEB-side half (SYSTEM_DESIGN_GUIDELINES §12).

This package runs inside the FastAPI process and may import anything under `app.*`. It owns
the ledger (`run_service`), the publisher loop (`publisher_service`) and, from Phase 2, the
content selection and writer services.

The WORKER-side half — the media pipeline that renders audio/video — lives in
`backend/marketing/` (its own Railway cron service, its own Dockerfile) and must NEVER import
this package or anything else under `app.*`: it holds no Supabase key and would fail on
`app.config`. The two halves share nothing but the tables of migration 170, reached from the
worker only through `app/api/v1/endpoints/marketing_internal.py`.
"""
