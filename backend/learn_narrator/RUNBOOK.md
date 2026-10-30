# Learn narrator — owner runbook

The narrator is a standalone Railway cron service (`backend/learn_narrator/`) that voices the
weekly Wiser pieces. Before it ships, the **Phase-0 benchmark** decides the engine: keep the
catalog voice (Chatterbox clone) on CPU if it passes, otherwise Kokoro with a male voice
(owner, 2026-10-22: *"i lean to option 1, but can you use the same voice as in the app but on
CPU? if not, then pick option 1 as it is"*).

> 🔴 **Never build this image or run the bench on the 16 GB Mac.** The build's smoke step renders
> with Chatterbox on the CPU, and an amd64 build on Apple silicon runs under emulation. Build on
> an x86 machine or in CI. Never use `scripts/runpod/setup_runpod.sh` for this either: it installs
> the CUDA (cu124) wheels and asserts a GPU.

## 1. Build the image (linux/amd64)

The build context is the folder itself (`backend/learn_narrator/`). The build needs network for
the pip install and the weight bake (~3 GB), then runs a smoke test with the network **off**. The
smoke refuses the network in-process (`child._refuse_network`), not with `RUN --network=none`:
Railway's Dockerfile validation rejects that flag ("flag '--network=none' is not supported"). A dead
proxy on the smoke command alone (never an `ENV` line) also fails any child process that tries to download.

### Option A — GitHub Actions → GHCR (no x86 machine needed)

Add this workflow as `.github/workflows/learn-narrator-image.yml` (run it by hand from the
Actions tab). The default `ubuntu-latest` runner is x86, 4 vCPU / 16 GB — enough for the build.

```yaml
name: learn-narrator image
on: workflow_dispatch
permissions:
  contents: read
  packages: write
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: docker/setup-buildx-action@v3
      - uses: docker/login-action@v3
        with:
          registry: ghcr.io
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}
      - uses: docker/build-push-action@v6
        with:
          context: backend/learn_narrator
          platforms: linux/amd64
          push: true
          tags: ghcr.io/${{ github.repository_owner }}/caydex-learn-narrator:bench
```

### Option B — buildx on an x86 host

```bash
docker buildx build --platform linux/amd64 -t caydex-learn-narrator:bench --load backend/learn_narrator
```

A successful build prints the smoke report (`[smoke] chatterbox OK …`, `align OK`, `kokoro OK`)
and the preflight JSON (`"engines": ["chatterbox", "kokoro"]`).

## 2. Run the benchmark

The bench refuses to start if any `SUPABASE_*` or `LEARN_NARRATOR_API_*` variable is present, never
calls the API, and writes only under `/tmp`. Mount a host folder there to keep the results.

```bash
docker run --rm --cpus=4 --memory=8g --memory-swap=8g -v /tmp/bench:/tmp/bench ghcr.io/OWNER/caydex-learn-narrator:bench python -m learn_narrator.bench --out /tmp/bench/run1
```

For the blind A/B (A5), also mount the two catalog clips (on the Mac they are in
`backend/data/money_moves_audio/`; copy `how-amazon-built-its-moat.m4a` and
`costcos-membership-magic.m4a` to the x86 box):

```bash
docker run --rm --cpus=4 --memory=8g --memory-swap=8g -v /tmp/bench:/tmp/bench -v /path/to/catalog:/catalog:ro ghcr.io/OWNER/caydex-learn-narrator:bench python -m learn_narrator.bench --out /tmp/bench/run1 --catalog-dir /catalog
```

### As a RunPod CPU pod

Create a **CPU** pod (4 vCPU, 8 GB or more) with the GHCR image as its container image and the
start command `sleep infinity`. In the pod's web terminal run
`python -m learn_narrator.bench --out /tmp/bench/run1`, then download `/tmp/bench/run1`.
Make sure the pod's environment carries no `SUPABASE_*` / `LEARN_NARRATOR_API_*` variable.
Note that a pod with more than 4 vCPU must be capped with `LEARN_NARRATOR_THREADS=4` to measure
the 4-vCPU bar (the container's cgroup quota is what the worker reads otherwise).

## 3. Read the report

`/tmp/bench/run1/report.txt` (and `report.json`, `timings.json`, `narration.m4a`):

| ID | Check | Pass bar |
|---|---|---|
| A1 | Wall time (render + align) | ≤ 150 min for the ~1,600-word fixture |
| A2 | Peak RSS of the largest child | ≤ 6 GB |
| A3 | Blocks | no Chatterbox call ≥ 39.5 s raw; every call 1.8–3.6 spoken words/s (calls under 8 spoken words: 0.9–5.0) after the one re-render |
| A4 | Voice bands | F0 130–142 Hz, 144–166 wpm, 11.5–14.0 kB/s (12.8 until 2026-10-30: the image's ffmpeg 5.1 encodes 96k at ~12.6–12.9k), −17.5..−14.5 LUFS |
| A5 | Blind A/B | **you**: see below |
| A6 | Read-along | ≥ 98 % of sentences aligned, the table passes the server's checks, block-start error ≤ 0.15 s |
| exaggeration | Conditioning | `emotion_adv` = 0.65 (the setting really reached the model) |

**A5 — the blind A/B.** `ab_kit/` holds, per article, `<slug>.A.m4a` and `<slug>.B.m4a`: one is the
catalog clip, one the new render (the new one is a cut of the same opening text). Listen to each
pair and write down which one you think is the catalog, and whether you would call them the same
voice. Only then open `ab_key.json`. Pass = "same voice" for both pairs.

The bench exits 0 when every automated check passed (A5 still needs you), 1 otherwise.

Two things the report shows that the plan did not anticipate (both measured, 2026-10-22):

* **Split blocks** (`A3.split_blocks`). Chatterbox stops at 40 s per call. A block dense with figures
  says far more words than it shows — the fixture's 75-word worst case says ~128 ("$574.8 billion" is
  eight) — so the worker renders such a block as balanced runs of whole sentences joined by a 0.2 s
  pause (the old script's sentence-mode pause) instead of truncating it. Every catalog-sized block
  stays one call. Listen to the figure-dense block in the render for that seam.
* **Short calls** get a wide pace band. Chatterbox pads every call with silence; the catalog's own
  3-word title "Two Identical Engines" took 1.96 s (1.53 words/s), under the plan's 1.8 floor, which
  would have failed every job at its first section title.

## 4. If it fails

* A miss under 2× (e.g. A1 at 200 min) → re-run on 8 vCPU: `--cpus=8 --memory=16g` (and
  `LEARN_NARRATOR_THREADS=8` on a pod).
* Otherwise → **Kokoro**. Run the bench with a male voice and compare a few:
  `python -m learn_narrator.bench --engine kokoro --voice am_michael` (also `am_adam`, `am_eric`,
  `bm_george`, `bm_lewis`). Baked voices: `python -m learn_narrator.main --preflight`. Pick one by
  ear (the catalog narrator sits near F0 133 Hz), then set the WEB service's
  `LEARN_NARRATOR_ENGINE=kokoro` and `LEARN_NARRATOR_KOKORO_VOICE=<id>` — the engine is frozen
  into each job, the worker never chooses it.

## 5. Deploy

1. Create a Railway service from this repo with the settings in `railway.toml` (Root Directory
   `/backend/learn_narrator`, cron `*/20 * * * *`, restart Never, watch `/backend/learn_narrator/**`,
   8 GB, 4 vCPU). Confirm it on a throwaway service first (the plan's step), with the web side
   still in DRY RUN.
2. Variables: `LEARN_NARRATOR_API_BASE=https://caydexinvest.com` and `LEARN_NARRATOR_API_TOKEN`
   (= the web service's `LEARN_NARRATOR_TOKEN`). **No Supabase key.**
3. A tick with no job logs `no narration job` and exits 0.
4. **Never redeploy the narrator while a job is rendering** (OWNER_TASKS): the container dies, the
   lease lapses and the server re-claims the job, spending one of its re-claims.

## 6. Operations

Exit codes: `0` done / nothing to do / job no longer ours, `1` the job failed (reported to the
server) or the API failed, `2` configuration or credentials (wrong token → 401/403).

Failure codes the worker reports (`/fail`): `block_gate` (a block truncated at Chatterbox's 40 s cap
or outside the words/s band twice), `timings_invalid` (the read-along table would be refused),
`voice_out_of_band` (the file is not the expected voice — the render is deterministic, so a retry of
the same text fails the same way; on 2026-10-30 a 12,888 B/s file failed only the old 12.8k ceiling,
raised to 14k: this image's ffmpeg 5.1 encodes 96k higher than the local catalog), `weights_missing`, `voice_unsupported`,
`engine_unsupported`, `job_invalid`, `render_timeout` / `render_oom` / `render_failed`,
`render_part_oom` / `render_part_failed` (Chatterbox, below),
`align_timeout` / `align_oom` / `align_failed`, `encode_failed`, `upload_failed` (Storage refused
the PUT), `request_refused` (a 422),
`api_error`, `worker_error` (a bug — the log has the traceback). The server counts worker
failures (≤ 2 per job, then the round fails and alerts) — the worker keeps no count.

### Chatterbox renders in parts (2026-10-24)

The first live render (3M, 75 blocks, 1,697 words) ran in ONE child on 4 vCPU / 8 GB: memory sat
at the limit and the kernel killed the child (`render_oom`, exit -9) after 3,589 s, about 30 blocks
in. A second attempt showed why: the loaded model holds ~4.5 GB, then memory climbs ~120-170 MB
with every block and is never given back. So the worker now renders Chatterbox jobs in parts: one
`render_part` child per `RENDER_PART_BLOCKS` (10) blocks, each saving its blocks under `parts/` as they finish, then the
`render` child, which assembles the saved blocks without loading the model. A part killed by the
limit (`render_part_oom`) runs once more in a fresh process and resumes after its last saved block;
a second kill, or any other failure, ends the job with that part's code. Every block keeps its own
seed, so the audio is the one a single process would make. All parts share the one render budget
(`LEARN_NARRATOR_RENDER_TIMEOUT_MINUTES`, default 4 h): the budget running out, in any child, is
`render_timeout`. Each part reloads the model (~1–2 min) and logs its peak memory ("render part k …
peak N MB"), so the margin under the limit is in the log. The bench (§3) renders the same way and
refuses an `--out` that already holds `parts/` from an earlier run (it would reuse them unrendered).
