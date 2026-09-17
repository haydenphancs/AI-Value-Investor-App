"""
Marketing engine — the MEDIA WORKER (SYSTEM_DESIGN_GUIDELINES §12).

A separate Railway cron service built from `marketing/Dockerfile`, configured by
`marketing/railway.toml`, entered through `python -m marketing.main`. It renders the day's
artefacts and hands them to the backend through the token-gated internal API; it holds no
Supabase key and no social secret.

⚠️ Nothing in this package may import `app.*` — `app.config.Settings` needs the Supabase
variables this container deliberately lacks, and `app.main` would start every lifespan loop a
second time. `tests/test_marketing_worker.py` scans every file here for that.
The web-side services live in `app/services/marketing/`.
"""
