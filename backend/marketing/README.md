# marketing/ — the media worker

The worker half of the marketing engine (design doc §12). It is its **own Railway service**:

| File | Role |
|---|---|
| `Dockerfile` | the worker image — same Debian base + ffmpeg as the web app, plus Kokoro + torch-CPU with the weights and the `af_heart` voice BAKED in (`HF_HUB_OFFLINE=1` at run time); never the web app's requirements |
| `railway.toml` | config-as-code for that service: cron schedule, start command, watch patterns |
| `requirements.txt` | the worker's direct Python deps: httpx, torch 2.6 (CPU), kokoro/misaki 0.9.4, spaCy's English model (hash-pinned), Pillow, fontTools |
| `constraints.txt` | EVERY package of the image pinned to the versions the 2026-09-26 spike measured (applied with `-c`); keep it in step with `requirements.txt` |
| `main.py` | entrypoint: ET gate → claim → preflight → stages → checkpoint → exit. Stages: `selected` and `scripted` (kick-and-poll the web side for the day's script), `voiced` (Phase 3), `rendered` and `assets_ready` (Phase 4). A finished run closes `media_ready` |
| `voice.py` | the `voiced` stage: Kokoro in a child process (`python -m marketing.voice child …`), seeded, bit-exact AAC, word timings in the audio asset's metadata, the pointer in the checkpoint PATCH. Skipped on a day with no video outlet |
| `render.py` | the `rendered` stage (read back the verified narration, download + sha check, cards, captions, one ffmpeg call, register the video with what it drew) and the `assets_ready` stage (record the day's posts) |
| `cards.py` | pure: the 1080×1920 PNG cards (brand, text, stat, disclaimer) in the brand colours; never truncates |
| `video.py` | the timeline, the ffmpeg argv, the runner and the ffprobe gate — never `-shortest` |
| `timings.py` | pure: engine tokens → the script's own display words with times |
| `captions.py` | pure: timed words → the ASS caption file libass burns |
| `preview.py` | local check: `./venv_marketing/bin/python -m marketing.preview` (from `backend/`) renders the full video — voice, cards, captions — into the gitignored `out/` |
| `assets/fonts/` | Inter Bold (static, OFL) for the cards, the caption burn and measuring |
| `assets/brand/` | the Caydex logo for the brand and disclaimer cards (the image copies only `marketing/`) |

Railway setup: service on this repo with **Root Directory `/backend`** and **Config File
`/backend/marketing/railway.toml`** (without it Railway reads the web's `backend/railway.toml`),
**4 GB of memory** (the voice child peaks near 2 GB; the render runs after it has exited).
Environment: `MARKETING_API_BASE_URL` (the web's public URL, `https://caydexinvest.com`),
`MARKETING_WORKER_TOKEN`, `MARKETING_RUN_HOUR_ET`, `MARKETING_DRY_RUN` (optionally
`SUPABASE_PUBLISHABLE_KEY`) — and **no** Supabase secret, **no** social or bot token. Knobs, all
optional: `MARKETING_TTS_VOICE` (default `af_heart`; only a voice baked into the image works — the
preflight reports it), `MARKETING_TTS_SPEED` (1.0), `MARKETING_TTS_THREADS` and
`MARKETING_RENDER_THREADS` (else the container's CPU quota), `MARKETING_MAX_VIDEO_SECONDS` (75,
mirroring the web setting).

Nothing here imports `app.*`. The web-side half (ledger, publisher loop, review bot, content
selection, the writer, its validators and the compliance judge) is `app/services/marketing/`; the
worker reaches the database only through `app/api/v1/endpoints/marketing_internal.py`, and every
call after its claim presents that claim (`X-Marketing-Claim`). It never sees a caption: copy is
server-authored and recorded by `create_posts` from the accepted script. The server checks what the
video SAYS (the narration's timed words must be the accepted script's) and what it DRAWS (the video
declares every on-screen string; only the script's cards, its disclaimer card and the end card are
allowed, and the disclaimer card is required).

Order of operations for the first deploy: deploy the web service FIRST → create this service and
set the SAME `MARKETING_WORKER_TOKEN` on both → watch the first in-window tick: a `marketing_runs`
row with `metadata.preflight.voice.ready` and `metadata.preflight.render.ready` true, then
`marketing_scripts` accepted, `audio` and `video` assets `ready` (on a day with a video outlet),
`marketing_posts` rows `pending_review`, and the run closed `media_ready`. Each stage's checkpoint
`timings` carry its wall time and peak memory (`<stage>_children_maxrss_mb`, and
`<stage>_cgroup_peak_mb` where the kernel reports it). A day can also close `skipped` with a
reason that says why: `rest_day`, `content_rejected`, `writer_unavailable`, `empty_pool`,
`source_ineligible`, `narration_too_long`, `unrenderable_text` (a glyph the font lacks, or a card
that cannot fit), `judge_not_enforced` (the web runs MARKETING_JUDGE_MODE other than `enforce`). A
409 `MARKETING_RUN_NOT_HELD` means this tick no longer holds the run; the worker logs a WARNING and
exits 0. Never set `MARKETING_RUN_DATE` to a future date: the backend refuses it (a claim is a
UNIQUE row, so a sample run would consume the real day).
