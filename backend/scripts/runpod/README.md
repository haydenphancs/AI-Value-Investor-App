# RunPod GPU runbook — Chatterbox book narration

Run the **local Chatterbox voice-clone** generator on a rented RunPod GPU instead of the
Mac (`mps` ≈ 4–5 h/book → CUDA ≈ 25–40 min/book, ~$0.15–0.25/book at $0.34/hr). Everything
downstream (seed / swift-gen / read-along / Xcode) stays on the Mac, unchanged.

> **Railway can't do this.** Railway is CPU-only; Chatterbox is a *local model*, so it would
> be as slow as the Mac. Railway stays the home for the *Gemini-API* audio (Money Moves /
> Journey / Gemini book voices). Only a GPU fixes "too long".

## What runs where

| Step | Where |
|---|---|
| `generate_book_audio_clone.py N` (Chatterbox) | **RunPod GPU** |
| `align_book_audio.py N` (torchaudio MMS_FA) | Mac, under `venv_clone` (pinned torchaudio) |
| `seed_book_audio.py N --force` (Supabase upload — needs secrets) | Mac |
| `gen_books_swift.py` / `gen_book_audio_swift.py` / `gen_book_read_along.py` (write `*.swift`) | Mac |
| Xcode rebuild + sim reinstall | Mac |

The pod needs **no secrets** — generation reads only committed `documents/books/*.txt` + the
ref `.wav`. `.env` never leaves the Mac.

## One-time setup

**RunPod console (only you can do these):**
1. Create a **Network Volume** (~20–30 GB) in a region that has RTX 4090.
2. Launch an **RTX 4090 Community** pod, official **PyTorch** template, volume mounted at `/workspace`, SSH enabled.
3. Add your Mac's SSH **public** key to the pod (so rsync needs no password).
4. Note the pod's SSH **host + port** and the **$/hr** shown.

**Mac shell env (every session):**
```bash
export POD_HOST=<pod-ip>  POD_PORT=<ssh-port>  POD_USER=root
export RUNPOD_RATE_USD_PER_HR=0.34          # match the pod's real rate
```

**Pod, first boot (bootstrap rsync, then provision):**
```bash
apt-get update && apt-get install -y rsync     # rsync must exist on both ends
# (now run sync_up.sh from the Mac — see below — then:)
bash /workspace/AI-Value-Investor-App/backend/scripts/runpod/setup_runpod.sh
```
`setup_runpod.sh` installs ffmpeg/libsndfile, builds `venv_clone`, installs CUDA torch, and
points `HF_HOME`/`TORCH_HOME`/`PIP_CACHE_DIR` at the volume so weights download **once**.

## Per book

```bash
# 1. [Mac] map the book's ref in generate_book_audio_clone.py REFS (book 2 already done),
#    ensure the ref wav is in backend/data/voice_clone/refs/, then push:
bash backend/scripts/runpod/sync_up.sh

# 2. [Pod] generate, metered (live $/hr + per-core timing + cost_report.json):
cd /workspace/AI-Value-Investor-App/backend
./venv_clone/bin/python scripts/runpod/time_and_cost.py --label "clone book 2" -- \
    ./venv_clone/bin/python scripts/generate_book_audio_clone.py 2

# 3. [Mac] pull artifacts, VERIFY, then TERMINATE the pod:
bash backend/scripts/runpod/sync_down.sh 2
ls -lh backend/data/book_audio/2_*          # confirm m4a + manifest + cost_report
#   -> terminate the pod in the console (keep the volume)

# 4. [Mac] finish (unchanged pipeline):
./venv/bin/python scripts/seed_book_audio.py 2 --force
./venv/bin/python scripts/gen_books_swift.py
./venv/bin/python scripts/gen_book_audio_swift.py
./venv_clone/bin/python scripts/align_book_audio.py 2
./venv/bin/python scripts/gen_book_read_along.py
#   -> Xcode rebuild + reinstall in the sim (same bucket URL → clear cache)
```

**GATE:** review **book 2** in-app (audio quality + read-along sync) before rolling out
books 1, 3, 4, 8 then 5, 6, 7, 9, 10. Each later book skips the model download (warm volume).

## Learn narration (Money Moves / Investor Journey)

Same pod, same voice clone — different content and a different output path. This is the narration
step in `.claude/skills/add-learn-content/SKILL.md`, moved to a GPU because the Mac runs it at
**~0.38 spoken words/sec** (a ~600-word article ≈ 27 min); a 4090 is roughly 8-10x that.

```bash
# 1. [Mac] push code + the ref wav + BOTH learn content JSONs (sync_up.sh covers all of it):
export POD_HOST=<pod-ip> POD_PORT=<ssh-port> POD_USER=root
bash backend/scripts/runpod/sync_up.sh

# 2. [Pod] provision once per pod, then run DETACHED (SSH drops under GPU load):
cd /workspace/AI-Value-Investor-App/backend
bash scripts/runpod/setup_runpod.sh                      # first boot only
setsid nohup bash scripts/runpod/run_learn_clone.sh > /workspace/learn_clone.out 2>&1 &
tail -f /workspace/learn_clone.out                       # Ctrl-C is safe; the job survives

# 3. [Mac] pull, verify, THEN terminate the pod:
bash backend/scripts/runpod/sync_down_learn.sh moneymoves
```

`run_learn_clone.sh` preflights (venv_clone, ffmpeg, ref wav, content JSON, **CUDA**) before
generating, so a misconfigured pod fails in seconds instead of after an hour of billing. It exports
`CLONE_MODE=block` itself, keeps going past a failed slug, and prints the first slug's wall clock so
the rate can be checked before walking away. Re-running skips finished clips.

**Clips land in `data/money_moves_audio_clone/`, never the live dir** — `sync_down_learn.sh` keeps
that separation on purpose. Re-voicing a published article means backing up its old `.m4a` first,
and `align_*.py` downloads the PUBLISHED clip when the local one is absent, which would align new
timings against old audio. Do the move on the Mac, then align + seed there.

## Cost / time tracking

- **Live** (`time_and_cost.py`): `elapsed Xm, $Y at $0.34/hr` every 15 s + a final `DONE` line.
- **Per-core** (generator): `core N done in Xs · cum Ym · proj book $Z`.
- **Per-book**: `<N>_<slug>.cost_report.json` (wall seconds, $ at rate, audio seconds).
- **Ledger**: every job appends to `backend/data/book_audio/runpod_cost_log.jsonl`.
  Totals any time: `./venv/bin/python scripts/runpod/time_and_cost.py --summary`.
- **Truth**: the meter times the *command*; RunPod bills *total pod uptime*. Reconcile against
  the console's billed minutes. **Spin up → run → sync down → terminate promptly.**

## Gotchas

- **Smoke-test CUDA before a full book** on a fresh pod: `setup_runpod.sh` asserts
  `torch.cuda.is_available()`; also try one sentence before committing hours.
- **Sync before terminate** — artifacts live on the volume, but the Mac is the canonical home.
- **Resume** (`.clone_cache_N/`) survives pod restarts only because the checkout is on the volume.
- **ffmpeg must exist** or the m4a encode fails *after* generation — `setup_runpod.sh` checks it.
