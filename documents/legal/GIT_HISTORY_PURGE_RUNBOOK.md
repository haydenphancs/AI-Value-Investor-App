# Git history purge — runbook

**Status: not run. You run this, not Claude.** It rewrites every commit, so it needs a
force-push and a fresh clone everywhere the repo exists.

Written 2026-08-07. Every number below was measured on this repo, not estimated.

> **Reviewed and corrected 2026-08-14** — six defects, two of which lose data:
> **1b** (`--force` `git reset --hard`s your uncommitted work), **2** (skipping it deletes
> ~620 MB of audio off disk), **3** (two paths missing, so the *After* check failed even on a
> perfect run), **5** (residual target unreachable), **After** (47 remote branches, not "8+";
> `--all` does not rewrite them), and the Storage recovery note (buckets went private in
> migration 128). Re-measured the same day: history holds 1516.8 MB of blob content.
>
> Also re-confirmed the reason to run it: the repo **is public** (`"private": false`) and both
> copyrighted PDFs are still anonymously downloadable today by direct commit SHA. They are
> deleted at tip, so a `/main/` URL 404s — which is exactly why deletion was not enough.

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
`git log --oneline origin/main..HEAD` is empty (still empty as of 2026-08-14) so nothing local
is unpushed.

### 1b. 🔴 COMMIT OR STASH YOUR WORKING TREE FIRST — step 3 destroys it

**This is the single most dangerous thing in this runbook and the original version did not
mention it.**

`git filter-repo --force` does **not** merely skip a confirmation. `--force` bypasses
`sanity_check` entirely (`git-filter-repo:3327`), which is where the
`abort("you have unstaged changes")` guard lives (`:3488`) — and the run then ends with an
unconditional `git reset --hard` in a non-bare repo. **Every uncommitted change is gone**,
recoverable only from the step-1 backup, which nothing else in this document tells you to
go looking for.

```bash
git status --porcelain    # MUST be empty before step 3
```

As of 2026-08-14 this repo has uncommitted work in it — including this file and
`LAUNCH_CHECKLIST.md`. Commit or stash, then re-check.

### 2. Move the media out of the working tree first

⚠️ **This step is load-bearing, not tidiness.** Because step 3 ends in `git reset --hard`,
skipping the `git rm --cached` + `.gitignore` commit means the reset **deletes ~490 tracked
audio files (~620 MB) off your disk**, not just out of history. The seeding scripts then have
nothing to read. Do step 2, and commit it, before step 3.

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

⚠️ **Do steps 3a and 3b in the SAME pass.** Both rewrite every commit and both force a
re-clone, so running them separately doubles the disruption for no benefit.

#### 3a. Real-investor names in narration source — `--replace-text`

Added 2026-08-14 (plan Phase 2.4). `--invert-paths` below removes *paths*; it does not touch
`backend/scripts/`, so without this the strings survive the purge:

- `backend/scripts/generate_book_audio.py` carried `"Narrate as Warren Buffett, …"` and six
  siblings until commit `4bce04f`. The current file is clean — **the history is not.**
  Confirm with `git log --all -S"Narrate as" --oneline` (3 commits today).
- `generate_book_audio_clone.py` and `clone_prototype.py` carried a `REFS` dict of
  `<author>_<voice>.wav` filenames. Also cleaned in the tree, also still in history.

Why it matters: these are stock synthetic TTS voices given delivery-style directions, and no
real person was ever recorded. A public history saying "Narrate as Warren Buffett" beside a
product called *The Essays of Warren Buffett* misdescribes what was built and reads as intent
to imitate a real person's voice. The `.wav` clips themselves were never committed
(`.gitignore:245`), so this is the whole of the exposure.

Write the expressions file (NOT inside the repo — `filter-repo` refuses a dirty tree):

```bash
cat > /tmp/caydex-replace.txt <<'EOF'
Narrate as Warren Buffett, a==>Read this as a
Narrate as Peter Lynch, a==>Read this as a
Narrate as Philip Fisher, a==>Read this as a
Narrate as John Bogle, a==>Read this as a
Narrate as Burton Malkiel, a==>Read this as a
Narrate as Joel Greenblatt, a==>Read this as a
Narrate as Howard Marks, a==>Read this as a
graham_iapetus.wav==>iapetus_erudite_professor.wav
buffett_zubenelgenubi.wav==>zubenelgenubi_warm_elder.wav
fisher_schedar.wav==>schedar_scholarly_analyst.wav
bogle_alnilam.wav==>alnilam_elder_statesman.wav
malkiel_orus.wav==>orus_witty_emeritus.wav
greenblatt_achird.wav==>achird_patient_teacher.wav
marks_sadaltager.wav==>sadaltager_contemplative.wav
clone:graham_iapetus==>clone:iapetus_erudite_professor
clone:buffett_zubenelgenubi==>clone:zubenelgenubi_warm_elder
clone:fisher_schedar==>clone:schedar_scholarly_analyst
clone:bogle_alnilam==>clone:alnilam_elder_statesman
clone:malkiel_orus==>clone:orus_witty_emeritus
clone:greenblatt_achird==>clone:achird_patient_teacher
clone:marks_sadaltager==>clone:sadaltager_contemplative
EOF
```

Each line is `old==>new` and is a literal match (no regex). The `, a` on the prompt lines is
deliberate: it makes the replacement produce grammatical text ("Read this as a seasoned,
sharp stock-picker…") identical in shape to what the file says today.

#### 3b. The path removal, with 3a folded in

```bash
cd /Users/haiphan/BIGDATA/myApp/AI-Value-Investor-App
git filter-repo --force \
  --replace-text /tmp/caydex-replace.txt \
  --path backend/data/book_audio --path backend/data/journey_audio \
  --path backend/data/money_moves_audio \
  --path backend/data/money_moves_audio_clone \
  --path backend/data/journey_audio_gemini_bak \
  --path backend/data/money_moves_audio_gemini_bak \
  --path separate_project \
  --path documents/Books \
  --path backend/scripts/caydex_report_poc.pdf \
  --path app_logic.txt \
  --invert-paths
```

Verify afterwards — both must be empty:

```bash
git log --all -S"Narrate as" --oneline
git log --all -S"buffett_zubenelgenubi" --oneline
```

**Two paths were missing from this list until 2026-08-14**, and both defeat the success
criterion in the *After* section:

- `backend/data/money_moves_audio_clone/how-amazon-built-its-moat.m4a` — **5.6 MB**, the single
  largest survivor. Note the directory is `money_moves_audio_clone`, a *sibling* of
  `money_moves_audio`; a `--path` on the latter does **not** match it.
- `app_logic.txt` — 2.1 MB at the repo root.

**Deliberately NOT purged:** `backend/data/book_covers/` (43 objects, **14.4 MB**). It is live,
actively-edited content that is tracked at HEAD, and `--invert-paths` removes a path from HEAD
too — purging it would delete the cover art. It is the bulk of what remains, and that is the
right trade. (Only three narrow `book_covers` patterns are gitignored today — `.tmp_*`,
`*.art.v*.jpg`, `*.candidates.jpg` — so the composed `.art.jpg` and `.manifest.json` files are
tracked on purpose.)

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
du -sh .git      # expect roughly 60-120 MB, from 1.0 GB
```

⚠️ **Do not expect "well under 100 MB"** — that was the original target and it is not
achievable while `book_covers` stays tracked. Measured 2026-08-14: history holds **1516.8 MB**
of blob content; the purge as written leaves **192.2 MB**, and with the two paths added above
**184.5 MB** — of which 14.4 MB is the book cover art you are deliberately keeping. That is
uncompressed blob content, so packed `.git` lands well below it, but a run that finishes at
~100 MB is a SUCCESS, not a reason to re-run the rewrite.

---

## After

- **Every other clone is now invalid.** Delete and re-clone; do not merge.
- 🔴 **`--all` does NOT do what this said about the remote branches.** There are **47** remote
  `origin/claude/*` branches (counted 2026-08-14, not "8+"). `git filter-repo` rewrites *local*
  refs; those 47 exist only on the remote, so `git push --force --all` pushes your local set and
  leaves every unrewritten remote branch in place — each one still carrying the full old
  history, which keeps the PDF blobs fetchable and the repo large. Worse, filter-repo maps any
  branch it *does* see, so a stale local tracking branch can be *recreated* on the remote.
  **Delete the remote `claude/*` branches on GitHub first**, then push `main` explicitly:

  ```bash
  git branch -r | grep 'origin/claude/' | wc -l      # expect 0 before you push
  git push --force origin main
  ```
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

⚠️ **As originally written this check FAILED even on a perfect run**, because
`money_moves_audio_clone/how-amazon-built-its-moat.m4a` (5.6 MB) was not in the `--path` list.
It is now. With the corrected list the check passes; the largest survivor is a 1.63 MB cover
image. If it prints anything, add that path and re-run rather than assuming the rewrite failed.

- **The Learn pipeline is unaffected** as long as you keep the audio on disk. 17 scripts read
  those directories (`generate_*_audio.py`, `align_*_audio.py`, `seed_*.py`, …). They are build
  inputs, never runtime assets.
- ⚠️ **The "download them back from the buckets" fallback is no longer a one-liner.** Migration
  128 made `journey-media`, `money-moves-media` and `book-media` **private** (`public = false`)
  — verified by probing the public object URL, which now answers
  `{"statusCode":"404","error":"Bucket not found"}`. Recovery needs the service-role key and a
  signed-URL download, not a plain `curl`. **The step-1 backup is your real safety net**, and
  the safety check above must be re-run with the service role, not against public URLs.
