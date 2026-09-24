"""
Marketing engine — the WEB-side half (SYSTEM_DESIGN_GUIDELINES §12).

This package runs inside the FastAPI process and may import anything under `app.*` EXCEPT the
FMP client and FMP-relayed services (`tests/test_marketing_import_boundary.py`). It owns the
ledger (`run_service`), the publisher loop (`publisher_service`), and since Phase 2 the class-A
content engine: the Learn content pool (`content_pool`), cadence + rotation (`selection`), the
writer (`writer_service`, `writer_prompts`), its pure validators (`compliance`, `grounding`,
`numbers`), code-owned post copy (`post_copy`), the kick-and-poll script orchestration
(`script_service`) and the public smart link (`smart_link`).

The WORKER-side half — the media pipeline that renders audio/video — lives in
`backend/marketing/` (its own Railway cron service, its own Dockerfile) and must NEVER import
this package or anything else under `app.*`: it holds no Supabase key and would fail on
`app.config`. The two halves share nothing but the tables of migration 170, reached from the
worker only through `app/api/v1/endpoints/marketing_internal.py`.
"""
