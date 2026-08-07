# Git history purge — runbook

**Status: not run. You run this, not Claude.** It rewrites every commit, so it needs a
force-push and a fresh clone everywhere the repo exists.

Written 2026-08-07. Every number below was measured on this repo, not estimated.

---

## Why

`.git` is **1.0 GB** on a public repo. The launch checklist (§4) scopes this as "purge the two
copyrighted book PDFs" — that is the wrong scope by three orders of magnitude. The PDFs are a
few MB. The size is media:

| Path | Bytes in history | Tracked at HEAD |
|---|---|---|
| `backend/data/book_audio` | **702 MB** | 17 `.m4a` + 30 `.json` + 1 `.jsonl` |
| `separate_project/stocks_detector` | 367 MB | already deleted |
| `backend/data/journey_audio` | 83 MB | 207 `.m4a` |
| `backend/data/money_moves_audio` | 45 MB | 13 `.m4a` |
| `*_gemini_bak` (journey + money moves) | 54 MB | 220 files |
| The two book PDFs + 2 unrelated PDFs | a few MB | already deleted |

490 files / 609 MB are still tracked at HEAD.

### What this is NOT about

An earlier framing of mine called the narrations "full-length AI narrations of copyrighted
investing books." **That is wrong and should not be repeated.** Each book in `documents/Books/`
is ~10 `core N.txt` files of ~500 words of original didactic prose written for the app; the
audio narrates those. *The Most Important Thing* is 58 minutes against ~5,000 words of script
— ~150 wpm, which matches a summary, not a book. The narrations are the app's own content.

**The justification is repo size.** The two PDFs are a genuine copyright item and are included
because they are in the same rewrite, not because the audio is.

---

## Safety check — done, and re-do it before you run

Every shipped audio asset is **already in Supabase Storage**, so nothing here is the only copy.
Verified 2026-08-07:

| Bucket | Objects under `audio/` | Tracked `.m4a` (unique basenames) | Match |
|---|---|---|---|
| `book-media` | 10 | 10 | ✅ exact |
| `journey-media` | 207 | 207 | ✅ exact |
| `money-moves-media` | 13 | 13 | ✅ exact |

Set difference in both directions was empty for all three. Re-run before purging:

```bash
cd backend && ./venv/bin/python -c "
from app.config import settings
from supabase import create_client
sb = create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_ROLE_KEY)
for b in ('book-media','journey-media','money-moves-media'):
    print(b, len(sb.storage.from_(b).list('audio', {'limit': 2000})))
"
```

Expect `10 / 207 / 13`. If any count is short, **stop** — seed that bucket first
(`scripts/seed_book_audio.py`, `seed_journey.py`, `seed_money_moves.py`).

The extra 7 tracked `.m4a` under `book_audio/orig/` and `orig_speed/` are pre-normalisation
intermediates (~168 MB). They are already in `.gitignore` — which does not untrack files that
were committed before the rule existed, which is why they are still here. They are not in
Storage and are not needed by anything; `normalize_book_audio.py` regenerates them.

---

## Run it

### 0. Prerequisites

`git-filter-repo` is already installed at `/usr/local/bin/git-filter-repo`. Nothing to do.

### 1. Back up — non-negotiable

```bash
cd /Users/haiphan/BIGDATA/myApp && cp -a AI-Value-Investor-App AI-Value-Investor-App.prepurge-backup
```

Keep it until you have confirmed a fresh clone works and the app still builds. Also confirm
`git log --oneline origin/main..HEAD` is empty (it was, at `9ff3740`) so nothing local is
unpushed.

### 2. Move the media out of the working tree first

The rewrite removes these paths from every commit. Take them out of git's control *and* keep
them on disk, so the seeding scripts still work:

```bash
cd /Users/haiphan/BIGDATA/myApp/AI-Value-Investor-App
git rm -r --cached backend/data/book_audio backend/data/journey_audio \
                   backend/data/money_moves_audio \
                   backend/data/journey_audio_gemini_bak \
                   backend/data/money_moves_audio_gemini_bak
```

`--cached` untracks without deleting your local files.

Then append to `.gitignore`:

```gitignore
# Learn narration audio — served from Supabase Storage (book-media / journey-media /
# money-moves-media), NOT from the repo. 702 MB of history came from committing these.
# Regenerate with scripts/generate_*_audio.py, publish with scripts/seed_*.py.
backend/data/book_audio/
backend/data/journey_audio/
backend/data/money_moves_audio/
backend/data/journey_audio_gemini_bak/
backend/data/money_moves_audio_gemini_bak/
```

Commit that.

### 3. Rewrite history

```bash
cd /Users/haiphan/BIGDATA/myApp/AI-Value-Investor-App
git filter-repo --force \
  --path backend/data/book_audio --path backend/data/journey_audio \
  --path backend/data/money_moves_audio \
  --path backend/data/journey_audio_gemini_bak \
  --path backend/data/money_moves_audio_gemini_bak \
  --path separate_project \
  --path documents/Books \
  --path backend/scripts/caydex_report_poc.pdf \
  --invert-paths
```

⚠️ `--path documents/Books` removes the 109 `core N.txt` files **as well as** the two PDFs,
because the PDFs sit inside per-book folders. Those `.txt` files are your Learn source content
and are small (600 KB total) — **copy them somewhere first** and re-add them after the rewrite
if you want them tracked:

```bash
cp -a documents/Books /tmp/caydex-book-cores   # BEFORE the rewrite
```

If you would rather keep them tracked throughout, replace `--path documents/Books` with the two
exact PDF paths (note the trailing space in one directory name — it is real):

```
--path "documents/Books/The Little Book that Still Beats the Market/little-book-that-still-beats-the-market-the-joel-greenblatt.pdf"
--path "documents/Books/The Psychology of Money /The-Psychology-of-Money-Morgan-Housel.pdf"
```

### 4. Re-add the remote and force-push

`git filter-repo` deletes `origin` on purpose, to stop a reflexive `git push`.

```bash
git remote add origin https://github.com/haydenphancs/AI-Value-Investor-App.git
git push --force --all
git push --force --tags
```

### 5. Clean up

```bash
git reflog expire --expire=now --all && git gc --prune=now --aggressive
du -sh .git      # expect well under 100 MB, from 1.0 GB
```

---

## After

- **Every other clone is now invalid.** Delete and re-clone; do not merge. There are 8+ remote
  `claude/*` branches — `--all` rewrites them too, and any PR open against them will show a
  rewritten base.
- **GitHub keeps unreachable objects for a while.** The old blobs stay fetchable by SHA until
  GitHub GCs. For the PDFs specifically, open a support request to purge cached views if that
  matters to you.
- **Verify nothing broke:**

```bash
cd backend && ./venv/bin/pytest -q
```

```bash
DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer xcodebuild -project frontend/ios/ios.xcodeproj -scheme ios -sdk iphonesimulator -destination 'platform=iOS Simulator,name=iPhone 17 Pro' build
```

```bash
git rev-list --objects --all | git cat-file --batch-check='%(objecttype) %(objectname) %(objectsize) %(rest)' | awk '$1=="blob" && $3>5000000' | sort -k3 -rn | head
```

The last one should print nothing — no blob over 5 MB anywhere in history.

- **The Learn pipeline is unaffected** as long as you keep the audio on disk. 17 scripts read
  those directories (`generate_*_audio.py`, `align_*_audio.py`, `seed_*.py`, …). They are build
  inputs, never runtime assets — iOS reads the public Storage URLs. If you ever lose the local
  copies, download them back from the three buckets.
