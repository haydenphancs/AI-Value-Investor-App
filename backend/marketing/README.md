# marketing/ — the media worker

The worker half of the marketing engine (design doc §12). It is its **own Railway service**:

| File | Role |
|---|---|
| `Dockerfile` | the worker image — same Debian base + ffmpeg as the web app, plus Kokoro + torch-CPU with the weights and the `af_heart` voice BAKED in (`HF_HUB_OFFLINE=1` at run time); never the web app's requirements |
| `railway.toml` | config-as-code for that service: cron schedule, start command, watch patterns |
| `requirements.txt` | the worker's Python deps: httpx, torch 2.6 (CPU), kokoro/misaki 0.9.4, spaCy's English model (hash-pinned), Pillow, fontTools |
| `main.py` | entrypoint: ET gate → claim → preflight → stages → checkpoint → exit. Stages: `selected` and `scripted` (kick-and-poll the web side for the day's script), then `voiced` (Phase 3). A voiced run closes `skipped` / `phase3_voice_only` until Phase 4 renders |
| `voice.py` | the `voiced` stage: Kokoro in a child process (`python -m marketing.voice child …`), seeded, bit-exact AAC, word timings in the audio asset's metadata, the pointer in the checkpoint PATCH |
| `timings.py` | pure: engine tokens → the script's own display words with times |
| `captions.py` | pure: timed words → the ASS caption file libass burns (Phase 4) |
| `preview.py` | local check: `./venv_marketing/bin/python -m marketing.preview` (from `backend/`) renders narration + captions over a solid background into the gitignored `out/` |
| `assets/fonts/` | Inter Bold (static, OFL) for the caption burn and measuring |

Railway setup: service on this repo with **Root Directory `/backend`** and **Config File
`/backend/marketing/railway.toml`**, **4 GB of memory** (the voice child peaks near 2 GB).
Environment: `MARKETING_API_BASE_URL`, `MARKETING_WORKER_TOKEN`, `MARKETING_RUN_HOUR_ET`,
`MARKETING_DRY_RUN` (optionally `SUPABASE_PUBLISHABLE_KEY`) — and **no** Supabase secret. Voice
knobs, all optional: `MARKETING_TTS_VOICE` (default `af_heart`; only a voice baked into the image
works — the preflight reports it), `MARKETING_TTS_SPEED` (1.0), `MARKETING_TTS_THREADS` (else the
container's CPU quota), `MARKETING_MAX_VIDEO_SECONDS` (75, mirroring the web setting).

Nothing here imports `app.*`. The web-side half (ledger, publisher loop, content selection, the
writer, its validators and the compliance judge) is `app/services/marketing/`; the worker reaches
the database only through `app/api/v1/endpoints/marketing_internal.py`, and every call after its
claim presents that claim (`X-Marketing-Claim`). It never sees a caption: copy is server-authored
and recorded by `create_posts` from the accepted script, and the narration's timed words must be
exactly the accepted script's (the server checks them).

Order of operations for the first deploy: deploy the web service FIRST (it requires the claim
header this worker sends) → create this service and set the SAME `MARKETING_WORKER_TOKEN` on both
→ watch the first in-window tick: a `marketing_runs` row with `source_ref`, `metadata.preflight.voice.ready`
true, then `marketing_scripts` accepted, an `audio` asset `ready`, and the run closed `skipped`
with `metadata.skip_reason` = `phase3_voice_only` — or another reason that says why: `rest_day`,
`content_rejected`, `writer_unavailable`, `empty_pool`, `source_ineligible`,
`narration_too_long`. A 409 `MARKETING_RUN_NOT_HELD` means this tick no longer holds the run; the
worker logs a WARNING and exits 0. Never set `MARKETING_RUN_DATE` to a future date: the backend
refuses it (a claim is a UNIQUE row, so a sample run would consume the real day).
