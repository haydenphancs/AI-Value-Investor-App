# marketing/ — the media worker

The worker half of the marketing engine (design doc §12). It is its **own Railway service**:

| File | Role |
|---|---|
| `Dockerfile` | the worker image — same Debian base + ffmpeg as the web app, plus (Phase 3) Kokoro + torch-cpu with weights baked in; never the web app's requirements |
| `railway.toml` | config-as-code for that service: cron schedule, start command, watch patterns |
| `requirements.txt` | the worker's Python deps (httpx today) |
| `main.py` | entrypoint: ET gate → claim → stages → checkpoint → exit |
| `stages/` | (Phase 3–4) TTS, captions, cards, video |
| `assets/fonts/` | vendored OFL fonts for the caption burn |

Railway setup: service on this repo with **Root Directory `/backend`** and **Config File
`/backend/marketing/railway.toml`**. Environment: `MARKETING_API_BASE_URL`,
`MARKETING_WORKER_TOKEN`, `MARKETING_RUN_HOUR_ET`, `MARKETING_DRY_RUN`
(optionally `SUPABASE_PUBLISHABLE_KEY`) — and **no** Supabase secret.

Nothing here imports `app.*`. The web-side half (ledger, publisher loop, and from Phase 2 the
content/writer services) is `app/services/marketing/`; the worker reaches the database only
through `app/api/v1/endpoints/marketing_internal.py`.
