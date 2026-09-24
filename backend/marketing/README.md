# marketing/ — the media worker

The worker half of the marketing engine (design doc §12). It is its **own Railway service**:

| File | Role |
|---|---|
| `Dockerfile` | the worker image — same Debian base + ffmpeg as the web app, plus (Phase 3) Kokoro + torch-cpu with weights baked in; never the web app's requirements |
| `railway.toml` | config-as-code for that service: cron schedule, start command, watch patterns |
| `requirements.txt` | the worker's Python deps (httpx today) |
| `main.py` | entrypoint: ET gate → claim → preflight → stages (Phase 2: `selected`, `scripted` — kick-and-poll the web side for the day's script) → checkpoint → exit. A scripted run closes `skipped` / `phase2_script_only` until Phases 3–4 add voice and render |
| `stages/` | (Phase 3–4) TTS, captions, cards, video |
| `assets/fonts/` | vendored OFL fonts for the caption burn |

Railway setup: service on this repo with **Root Directory `/backend`** and **Config File
`/backend/marketing/railway.toml`**. Environment: `MARKETING_API_BASE_URL`,
`MARKETING_WORKER_TOKEN`, `MARKETING_RUN_HOUR_ET`, `MARKETING_DRY_RUN`
(optionally `SUPABASE_PUBLISHABLE_KEY`) — and **no** Supabase secret.

Nothing here imports `app.*`. The web-side half (ledger, publisher loop, content selection, the
writer and its validators) is `app/services/marketing/`; the worker reaches the database only
through `app/api/v1/endpoints/marketing_internal.py`. It never sees a caption: copy is
server-authored and recorded by `create_posts` from the accepted script.

Order of operations for the first deploy: apply migration 176 (173 is already applied; 176 adds the
columns the hardened script service writes) → deploy the web service → create
this service and set the SAME `MARKETING_WORKER_TOKEN` on both → watch the first in-window tick
(a `marketing_runs` row with `source_ref`, then `marketing_scripts` accepted — or a run closed
`skipped` whose `metadata.skip_reason` says why: `rest_day`, `content_rejected`,
`writer_unavailable`, `empty_pool`, `source_ineligible`, or `phase2_script_only` for a day whose
script was accepted, since nothing is voiced or rendered yet). A 409 `MARKETING_RUN_NOT_HELD`
means this tick no longer holds the run; the worker logs a WARNING and exits 0.
Never set `MARKETING_RUN_DATE` to a future date: the backend refuses it (a claim is a UNIQUE row,
so a sample run would consume the real day).
